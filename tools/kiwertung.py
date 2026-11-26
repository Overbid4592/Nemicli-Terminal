"""
kiwertung.py - Wächter: ➕/➖ des Nutzers zu den Antworten der KI-Modelle, Werte und Rangliste (Toplist-KI).

learned/ki_wertung.db hält je Wertung Zeit, Modell, Persönlichkeit, ➕/➖, Grund (nur für den Nutzer), die Themen
der Runde, die Temperatur der Antwort (falls NemiCLI sie gesetzt hat) und die Antwortlänge. Daraus:

    Punkte      je Modell ➕, ➖ und eine vorsichtige Quote (untere Wilson-Grenze – wenige Wertungen zählen wenig)
    Themen      Stärken und Schwächen je Thema (bilder, coding, internet …)
    Verlauf     die Punkte über die Zeit
    Temperatur  je Stufe ➕/➖; mit dem Schalter wählt NemiCLI sie je Modell selbst (Thompson-Sampling: jede Stufe
                bekommt eine Beta-Verteilung aus ihren Wertungen, die Stichprobe mit dem höchsten Wert gewinnt)

Die KI erfährt nur, dass sie ein ➕ oder ➖ bekam (notiz_abholen) – nie den Grund, den Befehl oder die Rangliste.
"""
from __future__ import annotations

import math
import random
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

try:
    from paths import DATEN as _DATEN
except Exception:                                  # Selbsttest ohne Bootstrap
    _DATEN = Path(__file__).resolve().parent.parent

DB = Path(_DATEN) / "learned" / "ki_wertung.db"
STUFEN = (0.3, 0.5, 0.7, 0.9)                      # Temperatur-Stufen des Lerners
CONFIG_SCHALTER = "waechter_temperatur"            # an: NemiCLI wählt die Temperatur je Modell selbst
MIN_STUFE = 3                                      # ab so vielen Wertungen gilt eine Stufe als belegt
VERLAUF_MAX = 60
MAX_GRUND = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS wertung (
    id INTEGER PRIMARY KEY, zeit REAL NOT NULL, modell TEXT NOT NULL, persoenlichkeit TEXT DEFAULT '',
    wert INTEGER NOT NULL, grund TEXT DEFAULT '', themen TEXT DEFAULT '', temperatur REAL, laenge INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS wertung_modell ON wertung(modell);
"""

_lock = threading.Lock()
_offen: list[int] = []                             # Wertungen, die die KI noch nicht erfahren hat
_letzte_temperatur: dict[str, float] = {}          # Modell → Temperatur der letzten Antwort


def _verbinden(db: Path | None = None) -> sqlite3.Connection:
    p = Path(db or DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10)
    c.executescript(SCHEMA)
    return c


def wert_lesen(text: str) -> int | None:
    """„+“, „➕“, „👍“, „gut“ → +1; „-“, „–“, „➖“, „👎“, „schlecht“ → -1; sonst None."""
    t = (text or "").strip().lower()
    if t in ("+", "➕", "👍", "gut", "plus", "top", "ja", "+1"):
        return 1
    if t in ("-", "–", "—", "➖", "👎", "schlecht", "minus", "nein", "-1"):
        return -1
    return None


def werten(wert: int, modell: str, persoenlichkeit: str = "", grund: str = "", themen: list[str] | None = None,
           laenge: int = 0, db: Path | None = None) -> int:
    """Eine Wertung speichern und für die KI vormerken. Gibt die Punkte des Modells danach zurück."""
    if wert not in (1, -1) or not modell:
        raise ValueError("Wertung ist ➕ oder ➖, und ein Modell muss aktiv sein.")
    with _lock, closing(_verbinden(db)) as c, c:
        c.execute("INSERT INTO wertung (zeit, modell, persoenlichkeit, wert, grund, themen, temperatur, laenge) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (time.time(), modell, persoenlichkeit, wert, " ".join(str(grund or "").split())[:MAX_GRUND],
                   ",".join(t for t in (themen or []) if t), _letzte_temperatur.get(modell), int(laenge or 0)))
        punkte = c.execute("SELECT COALESCE(SUM(wert), 0) FROM wertung WHERE modell = ?", (modell,)).fetchone()[0]
        _offen.append(wert)
    return int(punkte)


def notiz_abholen() -> str:
    """Für die nächste Nachricht an die KI: nur, dass sie ➕ oder ➖ bekam – einmal, dann ist es weg."""
    with _lock:
        werte, _offen[:] = list(_offen), []
    if not werte:
        return ""
    zeichen = " ".join("➕" if w > 0 else "➖" for w in werte[-5:])
    return f"\n\n[Wächter] 🛡 Deine letzte Antwort hat vom Nutzer bekommen: {zeichen}"


# ---------------------------------------------------------------------------
# Werte
# ---------------------------------------------------------------------------

def wilson(plus: int, n: int, z: float = 1.64) -> float:
    """Untere Grenze der ➕-Quote (90 %): 3 von 3 zählt weniger als 30 von 32."""
    if n <= 0:
        return 0.0
    p = plus / n
    nenner = 1 + z * z / n
    mitte = p + z * z / (2 * n)
    rand = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (mitte - rand) / nenner)


def _zaehlen(zeilen, schluessel) -> dict[str, list[int]]:
    raus: dict[str, list[int]] = {}
    for z in zeilen:
        for k in schluessel(z):
            paar = raus.setdefault(k, [0, 0])
            paar[0 if z["wert"] > 0 else 1] += 1
    return raus


def werte(db: Path | None = None) -> list[dict]:
    """Rangliste: je Modell plus, minus, punkte, quote, sicher (Wilson), verlauf (Punkte über die Zeit), themen,
    persoenlichkeiten, temperatur ({Stufe: [plus, minus]}), beste_temperatur – die besten zuerst."""
    if not Path(db or DB).is_file():
        return []
    with closing(_verbinden(db)) as c:
        c.row_factory = sqlite3.Row
        zeilen = [dict(r) for r in c.execute("SELECT * FROM wertung ORDER BY zeit")]
    je_modell: dict[str, list[dict]] = {}
    for z in zeilen:
        je_modell.setdefault(z["modell"], []).append(z)
    liste = []
    for modell, eigene in je_modell.items():
        plus = sum(1 for z in eigene if z["wert"] > 0)
        n = len(eigene)
        stand, verlauf = 0, []
        for z in eigene:
            stand += z["wert"]
            verlauf.append(stand)
        temp = _zaehlen([z for z in eigene if z["temperatur"] is not None],
                        lambda z: [f"{float(z['temperatur']):.1f}"])
        belegt = {k: (p + 1) / (p + m + 2) for k, (p, m) in temp.items() if p + m >= MIN_STUFE}
        liste.append({
            "modell": modell, "plus": plus, "minus": n - plus, "punkte": stand, "quote": plus / n,
            "sicher": wilson(plus, n), "verlauf": verlauf[-VERLAUF_MAX:],
            "themen": _zaehlen(eigene, lambda z: [t for t in z["themen"].split(",") if t] or ["allgemein"]),
            "persoenlichkeiten": _zaehlen(eigene, lambda z: [z["persoenlichkeit"] or "?"]),
            "temperatur": temp, "beste_temperatur": float(max(belegt, key=belegt.get)) if belegt else None,
            "zuletzt": eigene[-1]["zeit"],
        })
    liste.sort(key=lambda m: (-m["sicher"], -m["punkte"], -m["zuletzt"]))
    return liste


# ---------------------------------------------------------------------------
# Temperatur
# ---------------------------------------------------------------------------

def steuern_an() -> bool:
    try:
        import config
        return bool(config.load().get(CONFIG_SCHALTER, False))
    except Exception:
        return False


def steuern_setzen(an: bool) -> bool:
    import config
    config.update(**{CONFIG_SCHALTER: bool(an)})
    return bool(an)


def temperatur_fuer(modell: str, db: Path | None = None, zufall: random.Random | None = None) -> float | None:
    """Temperatur für die nächste Antwort dieses Modells – nur mit dem Schalter, sonst None (Vorgabe des
    Anbieters). Thompson-Sampling über STUFEN; die gewählte gilt für die nächste Wertung."""
    if not modell or not steuern_an():
        _letzte_temperatur.pop(modell, None)
        return None
    zaehler = {f"{s:.1f}": [0, 0] for s in STUFEN}
    if Path(db or DB).is_file():
        with closing(_verbinden(db)) as c:
            for t, wert in c.execute("SELECT temperatur, wert FROM wertung WHERE modell = ? AND temperatur "
                                     "IS NOT NULL", (modell,)):
                if (k := f"{float(t):.1f}") in zaehler:
                    zaehler[k][0 if wert > 0 else 1] += 1
    z = zufall or random
    wahl = max(STUFEN, key=lambda s: z.betavariate(zaehler[f"{s:.1f}"][0] + 1, zaehler[f"{s:.1f}"][1] + 1))
    _letzte_temperatur[modell] = wahl
    return wahl
