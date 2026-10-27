"""rangliste.py - Learning-to-Rank für die Gedächtnissuche, lokal und klein.

Gelernt wird aus dem, was die Persönlichkeit mit den Treffern tut: Liest sie nach
gedaechtnis_suchen eine Quelle mit gedaechtnis_lesen, war deren Treffer nützlich (1),
die übrigen gezeigten dieser Suche nicht (0). Eine Suche ohne Lesen sagt nichts aus
und wird nicht gelernt.

Merkmale je Treffer: Rang der Fusion, Ähnlichkeit, gefunden über Wort / Sinn /
Vermutung, Alter der Quelle, Art der Quelle, wie oft die Quelle schon gelesen wurde.
Ein logistisches Modell (numpy, L2) schätzt daraus die Nützlichkeit p; die Suche
gewichtet ihre Punkte mit (0.5 + p). Erst ab genug Beispielen – vorher bleibt alles,
wie die Fusion es sortiert.

Daten in memory.db: rang_beispiele, rang_gelesen, Gewichte im Merkzettel `stand`.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime

ARTEN = ("chat", "chatmd", "note", "bericht", "wissen", "skill", "code")
MIN_BEISPIELE = 40                 # ab so vielen Beispielen wird gelernt …
MIN_NUETZLICH = 8                  # … und davon so vielen nützlichen
NEU_LERNEN_AB = 10                 # so viele neue Beispiele, dann wird neu gelernt
BEISPIELE_MAX = 5000               # ältere fallen heraus
SCHRITTE = 400
LERNRATE = 0.3
L2 = 0.01

_sperre = threading.Lock()
_letzte: dict = {}                 # die zuletzt gezeigte Suche: {"zeit", "treffer": [(ref, merkmale)], "gelesen": set}
_gewichte: dict = {"stand": None, "w": None}


def _alter_tage(datum: str) -> float:
    try:
        return max(0.0, (datetime.now() - datetime.strptime((datum or "")[:16], "%Y-%m-%d %H:%M")).days)
    except ValueError:
        try:
            return max(0.0, (datetime.now() - datetime.strptime((datum or "")[:10], "%Y-%m-%d")).days)
        except ValueError:
            return 365.0


def merkmale(h: dict, rang: int, gelesen: int) -> list[float]:
    """Merkmalsvektor eines Treffers (feste Reihenfolge; die 1.0 vorn ist der Achsenabschnitt)."""
    wie = set((h.get("wie") or "").split("+"))
    aehnlich = h.get("aehnlich")
    return ([1.0, 1.0 / (1.0 + rang), float(aehnlich) if aehnlich is not None else 0.0,
             float("wort" in wie), float("sinn" in wie), float("vermutung" in wie),
             float(len(wie - {""}) >= 2), math.log1p(_alter_tage(h.get("datum", ""))) / 6.0,
             math.log1p(gelesen) / 3.0]
            + [float(h.get("kind") == a) for a in ARTEN])


def _verbinden():
    import indexdb
    con = indexdb._connect()
    con.execute("CREATE TABLE IF NOT EXISTS rang_beispiele (zeit TEXT, merkmale TEXT, nuetzlich INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS rang_gelesen (ref TEXT PRIMARY KEY, n INTEGER)")
    return con


def _gelesen_zahlen(refs) -> dict[str, int]:
    refs = list(dict.fromkeys(refs))
    if not refs:
        return {}
    con = _verbinden()
    try:
        return {r: n for r, n in con.execute(
            f"SELECT ref, n FROM rang_gelesen WHERE ref IN ({','.join('?' * len(refs))})", refs)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Beobachten
# ---------------------------------------------------------------------------

def gezeigt(treffer: list[dict]) -> None:
    """Das Werkzeug hat diese Treffer gezeigt. Die vorige Suche wird dabei abgeschlossen (gelernt)."""
    try:
        zahlen = _gelesen_zahlen(h.get("ref", "") for h in treffer)
    except Exception:
        zahlen = {}
    neu = {"zeit": time.time(), "gelesen": set(),
           "treffer": [(h.get("ref", ""), merkmale(h, i, zahlen.get(h.get("ref", ""), 0)))
                       for i, h in enumerate(treffer)]}
    with _sperre:
        alt = dict(_letzte)
        _letzte.clear()
        _letzte.update(neu)
    _abschliessen(alt)


def gelesen(ref: str) -> None:
    """Die Persönlichkeit liest eine Quelle (gedaechtnis_lesen)."""
    ref = (ref or "").strip()
    if not ref:
        return
    try:
        con = _verbinden()
        try:
            con.execute("INSERT INTO rang_gelesen(ref, n) VALUES (?, 1) "
                        "ON CONFLICT(ref) DO UPDATE SET n = n + 1", (ref,))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass
    with _sperre:
        if _letzte and any(r == ref for r, _m in _letzte.get("treffer", ())):
            _letzte["gelesen"].add(ref)


def _abschliessen(suche: dict) -> None:
    """Eine Suche mit gelesenen Quellen → Beispiele (gelesen = nützlich, sonst nicht)."""
    if not suche or not suche.get("gelesen"):
        return
    jetzt = datetime.now().strftime("%Y-%m-%d %H:%M")
    zeilen = [(jetzt, json.dumps([round(x, 4) for x in m]), int(ref in suche["gelesen"]))
              for ref, m in suche["treffer"]]
    try:
        con = _verbinden()
        try:
            con.executemany("INSERT INTO rang_beispiele(zeit, merkmale, nuetzlich) VALUES (?,?,?)", zeilen)
            con.execute("DELETE FROM rang_beispiele WHERE rowid NOT IN "
                        "(SELECT rowid FROM rang_beispiele ORDER BY rowid DESC LIMIT ?)", (BEISPIELE_MAX,))
            con.commit()
        finally:
            con.close()
        import indexdb
        zaehler = int(indexdb._stand("rang_beispiele_zaehler") or 0) + len(zeilen)
        indexdb._stand("rang_beispiele_zaehler", str(zaehler))
        lernen()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lernen und Anwenden
# ---------------------------------------------------------------------------

def _beispiele() -> tuple[list[list[float]], list[int]]:
    con = _verbinden()
    try:
        zeilen = con.execute("SELECT merkmale, nuetzlich FROM rang_beispiele").fetchall()
    finally:
        con.close()
    x, y = [], []
    for m, n in zeilen:
        try:
            v = json.loads(m)
        except ValueError:
            continue
        x.append(v)
        y.append(int(n))
    return x, y


def _trainieren(x: list[list[float]], y: list[int]) -> list[float]:
    """Logistische Regression mit L2, Gradientenabstieg; nützliche Beispiele werden nach Häufigkeit gewichtet."""
    import numpy as np
    X, Y = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    pos = max(1.0, Y.sum())
    gewicht = np.where(Y > 0, (len(Y) - pos) / pos, 1.0)        # seltene Klasse nicht untergehen lassen
    w = np.zeros(X.shape[1])
    for _ in range(SCHRITTE):
        p = 1.0 / (1.0 + np.exp(-(X @ w)))
        grad = X.T @ ((p - Y) * gewicht) / len(Y) + L2 * np.r_[0.0, w[1:]]
        w -= LERNRATE * grad
    return [float(v) for v in w]


def lernen(erzwingen: bool = False) -> bool:
    """Neu lernen, wenn genug neue Beispiele da sind. True, wenn danach ein Modell gilt."""
    import indexdb
    x, y = _beispiele()
    # Fortlaufend gezählt: len(y) bleibt ab BEISPIELE_MAX stehen, und es würde nie wieder gelernt.
    zaehler = int(indexdb._stand("rang_beispiele_zaehler") or len(y))
    zuletzt = indexdb._stand("rang_beispiele_gelernt")
    if not erzwingen and zuletzt and zaehler - int(zuletzt or 0) < NEU_LERNEN_AB:
        return bool(indexdb._stand("rang_gewichte"))
    if len(y) < MIN_BEISPIELE or sum(y) < MIN_NUETZLICH or sum(y) == len(y):
        return False
    w = _trainieren(x, y)
    indexdb._stand("rang_gewichte", json.dumps(w))
    indexdb._stand("rang_beispiele_gelernt", str(zaehler))
    _gewichte["stand"] = None
    return True


def gewichte() -> list[float] | None:
    """Die gelernten Gewichte (zwischengespeichert, solange sich die Datenbank nicht ändert)."""
    import indexdb
    stand = indexdb._db_stamp()
    if _gewichte["stand"] != stand:
        try:
            roh = indexdb._stand("rang_gewichte")
            _gewichte["w"] = json.loads(roh) if roh else None
        except Exception:
            _gewichte["w"] = None
        _gewichte["stand"] = stand
    w = _gewichte["w"]
    return w if w and len(w) == len(merkmale({}, 0, 0)) else None


def nuetzlich(h: dict, rang: int, gelesen: int, w: list[float]) -> float:
    z = sum(a * b for a, b in zip(w, merkmale(h, rang, gelesen)))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def umsortieren(treffer: list[dict]) -> list[dict]:
    """Punkte mit dem gelernten Modell gewichten (0.5 + p); ohne Modell unverändert."""
    w = gewichte()
    if not w or not treffer:
        return treffer
    zahlen = _gelesen_zahlen(h.get("ref", "") for h in treffer)
    for i, h in enumerate(treffer):
        h["score"] = round(h["score"] * (0.5 + nuetzlich(h, i, zahlen.get(h.get("ref", ""), 0), w)), 4)
    return sorted(treffer, key=lambda h: -h["score"])
