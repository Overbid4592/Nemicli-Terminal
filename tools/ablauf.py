"""ablauf.py – Befehlsfolgen aufnehmen, wiedererkennen, abspielen und dabei dazulernen.

Verlauf: Jede Slash-Eingabe des Nutzers (nach der Menüwahl, so wie sie ausgeführt wird) kommt mit
Uhrzeit in learned/ablauf.json (die letzten VERLAUF_MAX). Eingaben der KI, der Einstellungsseite und
von Abläufen selbst zählen nicht – nur, was über `markieren()` als Nutzereingabe gekennzeichnet ist.

Wiedererkennen: Eingaben mit höchstens PAUSE_S Abstand bilden eine Folge. Ist eine Folge zu Ende (die
nächste Eingabe kommt nach längerer Pause), sucht NemiCLI darin die längste Reihe von 2–6 Befehlen, die in
MIN_WIEDERHOLT Folgen genauso vorkam, und schlägt einmal vor, sie als Ablauf zu speichern
(`/ablauf speichern <name>`). Erst am Ende geprüft, damit nicht schon ein Teil der Reihe vorgeschlagen wird.

Gezielt aufnehmen: `/ablauf aufnehmen <name>` nimmt alles auf, auch Chat-Nachrichten, bis `/ablauf stopp`.

Abspielen (main_befehle): Die Schritte laufen nacheinander wie getippt; Esc bricht ab.
Dazulernen beim Abspielen:
  - je Schritt wird gezählt, wie oft er mit einer Fehlermeldung endete (`/ablauf liste` zeigt es);
  - tippt der Nutzer direkt nach dem Ablauf mehrmals denselben Befehl, schlägt NemiCLI vor, ihn
    anzuhängen (`/ablauf anhaengen <name>`).
"""
from __future__ import annotations

import contextvars
import re
import threading
import time
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

DATEI = _ROOT / "learned" / "ablauf.json"
VERLAUF_MAX = 2000
PAUSE_S = 600                    # mehr Abstand zwischen zwei Eingaben beginnt eine neue Folge
MIN_WIEDERHOLT = 3               # in so vielen Folgen gleich, dann ein Vorschlag
LAENGE_MIN, LAENGE_MAX = 2, 6    # so lange Folgen werden erkannt
SCHRITTE_MAX = 20
DANACH_AB = 3                    # so oft derselbe Befehl direkt nach dem Ablauf, dann Vorschlag „anhängen“
NAME_MAX = 30
# Diese Befehle beenden, starten neu, steuern Abläufe selbst oder öffnen der KI Programm-Ordner auf Zeit –
# nie Teil einer Folge.
NICHT_AUFNEHMEN = frozenset({"/ablauf", "/exit", "/quit", "/restart", "/neustart", "/schluessel", "/schlüssel"})
# Sieht eine Eingabe nach einem Schlüssel oder Token aus, wird sie nicht gespeichert (die Datei liegt im Klartext).
_GEHEIM = re.compile(r"(?:sk-|sk_|hf_|ghp_|gho_|github_pat_|xai-|AIza|glpat-)[\w-]{8,}|[A-Za-z0-9_\-+=]{32,}")
GEHEIM_HINWEIS = "🔒 Nicht aufgenommen – die Eingabe sieht nach einem Schlüssel aus."
UNTERBEFEHLE = frozenset({"liste", "speichern", "aufnehmen", "stopp", "stop", "loeschen", "löschen", "anhaengen",
                          "anhängen", "vergessen", "vorschlaege", "vorschläge", "zeigen", "abbrechen"})
SCHALTER = "ablauf_vorschlaege"

_NUTZER: contextvars.ContextVar[bool] = contextvars.ContextVar("ablauf_nutzer", default=False)
_sperre = threading.RLock()
_zuletzt: dict = {}              # zuletzt abgespielt: {"name", "ende"} – für „danach“
_vorschlag: dict = {}            # zuletzt vorgeschlagene Folge: {"schritte", "zeit"}


# ---------------------------------------------------------------------------
# Kennzeichnung der Nutzereingabe
# ---------------------------------------------------------------------------

def markieren() -> None:
    """Die nächste handle_line-Zeile in diesem Ablauf-Kontext ist eine Eingabe des Nutzers."""
    _NUTZER.set(True)


def nimm() -> bool:
    """Ist die laufende Zeile eine Nutzereingabe? Setzt die Marke zurück – verschachtelte Aufrufe
    (Menüs, Werkzeuge der KI, Ablauf-Schritte) zählen damit nicht als Eingabe."""
    wert = _NUTZER.get()
    if wert:
        _NUTZER.set(False)
    return wert


# ---------------------------------------------------------------------------
# Speicher
# ---------------------------------------------------------------------------

def _leer() -> dict:
    return {"verlauf": [], "ablaeufe": {}, "vorgeschlagen": [], "aufnahme": None}


def _laden() -> dict:
    try:
        import atomar
        d = atomar.json_lesen(DATEI, _leer(), dict)
    except Exception:
        d = _leer()
    for k, v in _leer().items():
        if not isinstance(d.get(k), type(v)) and not (k == "aufnahme" and isinstance(d.get(k), dict)):
            d[k] = v
    d["verlauf"] = [e for e in d["verlauf"] if isinstance(e, list) and len(e) == 2
                    and isinstance(e[0], (int, float)) and isinstance(e[1], str)]
    d["ablaeufe"] = {n: a for n, a in d["ablaeufe"].items()
                     if isinstance(a, dict) and isinstance(a.get("schritte"), list)}
    return d


def _speichern(d: dict) -> None:
    import json

    import atomar
    DATEI.parent.mkdir(parents=True, exist_ok=True)
    atomar.text_schreiben(DATEI, json.dumps(d, ensure_ascii=False, indent=1))


def vorschlaege_an() -> bool:
    try:
        import config
        return bool(config.load().get(SCHALTER, True))
    except Exception:
        return True


def vorschlaege_setzen(an: bool) -> None:
    import config
    config.update(**{SCHALTER: bool(an)})


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def zeile_normal(zeile: str) -> str:
    return " ".join(str(zeile or "").split())


def befehl_von(zeile: str) -> str:
    z = zeile_normal(zeile)
    return z.split(" ", 1)[0].lower() if z.startswith("/") else ""


def name_pruefen(name: str) -> str:
    """Name eines Ablaufs: Kleinbuchstaben, Ziffern, - und _ (Umlaute erlaubt). ValueError sonst."""
    n = zeile_normal(name).lower()
    if not n or len(n) > NAME_MAX or not re.fullmatch(r"[\wäöüß-]+", n) or n.startswith("_"):
        raise ValueError(f"Name: 1–{NAME_MAX} Zeichen, nur Buchstaben, Ziffern, - und _ (z. B. morgen).")
    if n in UNTERBEFEHLE:
        raise ValueError(f"„{n}“ ist ein Unterbefehl von /ablauf – bitte einen anderen Namen.")
    return n


def _folgen(verlauf: list) -> list[list[str]]:
    """Verlauf → Folgen (Eingaben mit höchstens PAUSE_S Abstand)."""
    raus: list[list[str]] = []
    vorher = None
    for zeit, zeile in verlauf:
        if vorher is None or zeit - vorher > PAUSE_S:
            raus.append([])
        raus[-1].append(zeile)
        vorher = zeit
    return raus


def _enthaelt(folge: list[str], teil: tuple) -> bool:
    n = len(teil)
    return any(tuple(folge[i:i + n]) == teil for i in range(len(folge) - n + 1))


def _schon_bekannt(d: dict, teil: tuple) -> bool:
    if "\n".join(teil) in d["vorgeschlagen"]:
        return True
    return any(_enthaelt(a["schritte"], teil) for a in d["ablaeufe"].values())


def muster_der_folge(d: dict) -> tuple | None:
    """Die längste Reihe der letzten Folge, die in MIN_WIEDERHOLT Folgen gleich vorkam und noch nicht
    vorgeschlagen oder gespeichert ist (bei gleicher Länge die spätere) – sonst None."""
    folgen = _folgen(d["verlauf"])
    if not folgen:
        return None
    letzte = folgen[-1]
    for n in range(min(LAENGE_MAX, len(letzte)), LAENGE_MIN - 1, -1):
        for start in range(len(letzte) - n, -1, -1):
            teil = tuple(letzte[start:start + n])
            if len(set(teil)) < 2:
                continue                 # derselbe Befehl mehrmals hintereinander ist kein Ablauf
            if sum(_enthaelt(f, teil) for f in folgen) >= MIN_WIEDERHOLT and not _schon_bekannt(d, teil):
                return teil
    return None


def _pfeile(schritte) -> str:
    return " → ".join(schritte)


# ---------------------------------------------------------------------------
# Aufzeichnen
# ---------------------------------------------------------------------------

def aufnahme_laeuft() -> str:
    """Name der laufenden gezielten Aufnahme, sonst ""."""
    with _sperre:
        a = _laden().get("aufnahme")
    return str(a.get("name") or "") if isinstance(a, dict) else ""


def aufzeichnen(zeile: str, slash: bool = True, jetzt: float | None = None) -> list[str]:
    """Eine Eingabe des Nutzers merken. Gibt Hinweise zurück, die der Aufrufer zeigt."""
    zeile = zeile_normal(zeile)
    befehl = befehl_von(zeile)
    if not zeile or befehl in NICHT_AUFNEHMEN:
        return []
    if _GEHEIM.search(zeile):
        return [GEHEIM_HINWEIS] if aufnahme_laeuft() else []
    jetzt = time.time() if jetzt is None else jetzt
    hinweise: list[str] = []
    with _sperre:
        d = _laden()
        aufnahme = d.get("aufnahme")
        if isinstance(aufnahme, dict):
            if len(aufnahme.setdefault("schritte", [])) < SCHRITTE_MAX:
                aufnahme["schritte"].append(zeile)
                hinweise.append(f"⏺ Aufgenommen ({len(aufnahme['schritte'])}): {zeile}")
            else:
                hinweise.append(f"⏺ Höchstens {SCHRITTE_MAX} Schritte – mit /ablauf stopp speichern.")
        if not slash:
            if isinstance(aufnahme, dict):
                _speichern(d)
            return hinweise
        folge_zu_ende = bool(d["verlauf"]) and jetzt - d["verlauf"][-1][0] > PAUSE_S
        if folge_zu_ende and vorschlaege_an() and not isinstance(aufnahme, dict):
            teil = muster_der_folge(d)
            if teil is not None:
                d["vorgeschlagen"].append("\n".join(teil))
                del d["vorgeschlagen"][:-200]
                _vorschlag.clear()
                _vorschlag.update({"schritte": list(teil), "zeit": jetzt})
                hinweise.append(f"💡 Diese Folge machst du öfter: {_pfeile(teil)} – als Ablauf speichern: "
                                "/ablauf speichern <name>")
        d["verlauf"].append([round(jetzt, 1), zeile])
        del d["verlauf"][:-VERLAUF_MAX]
        hinweise += _danach_lernen(d, zeile, jetzt)
        _speichern(d)
    return hinweise


def _danach_lernen(d: dict, zeile: str, jetzt: float) -> list[str]:
    """Der erste Befehl kurz nach einem abgespielten Ablauf zählt als „danach“."""
    name, ende = _zuletzt.get("name"), _zuletzt.get("ende", 0)
    _zuletzt.clear()
    a = d["ablaeufe"].get(name) if name else None
    if a is None or jetzt - ende > PAUSE_S or zeile in a["schritte"]:
        return []
    danach = a.setdefault("danach", {})
    danach[zeile] = int(danach.get(zeile, 0)) + 1
    if danach[zeile] == DANACH_AB and len(a["schritte"]) < SCHRITTE_MAX:
        return [f"💡 Nach „{name}“ tippst du oft noch {zeile} – anhängen: /ablauf anhaengen {name}"]
    return []


# ---------------------------------------------------------------------------
# Verwalten
# ---------------------------------------------------------------------------

def namen() -> list[str]:
    with _sperre:
        return sorted(_laden()["ablaeufe"])


def schritte(name: str) -> list[str]:
    with _sperre:
        a = _laden()["ablaeufe"].get(zeile_normal(name).lower())
    return list(a["schritte"]) if a else []


def speichern(name: str, folge: list[str] | None = None) -> list[str]:
    """Ablauf speichern – ohne `folge` die zuletzt vorgeschlagene. Gibt die Schritte zurück."""
    name = name_pruefen(name)
    if folge is None:
        folge = list(_vorschlag.get("schritte") or [])
    folge = [zeile_normal(z) for z in folge if zeile_normal(z) and befehl_von(z) not in NICHT_AUFNEHMEN]
    if not folge:
        raise ValueError("Keine Folge da – erst einen Vorschlag abwarten oder mit /ablauf aufnehmen <name> "
                         "selbst aufnehmen.")
    folge = folge[:SCHRITTE_MAX]
    with _sperre:
        d = _laden()
        if name in d["ablaeufe"]:
            raise ValueError(f"„{name}“ gibt es schon – erst /ablauf loeschen {name} oder anderer Name.")
        d["ablaeufe"][name] = {"schritte": folge, "erstellt": time.strftime("%Y-%m-%d %H:%M"), "laeufe": 0,
                               "fehler": {}, "danach": {}}
        _speichern(d)
    _vorschlag.clear()
    return folge


def aufnehmen(name: str) -> str:
    name = name_pruefen(name)
    with _sperre:
        d = _laden()
        if name in d["ablaeufe"]:
            raise ValueError(f"„{name}“ gibt es schon – erst /ablauf loeschen {name} oder anderer Name.")
        if isinstance(d.get("aufnahme"), dict):
            raise ValueError(f"Es läuft schon die Aufnahme „{d['aufnahme'].get('name')}“ – /ablauf stopp.")
        d["aufnahme"] = {"name": name, "schritte": [], "start": time.strftime("%Y-%m-%d %H:%M")}
        _speichern(d)
    return name


def stopp() -> tuple[str, list[str]]:
    """Gezielte Aufnahme beenden und speichern. (Name, Schritte); ohne Schritte wird nichts gespeichert."""
    with _sperre:
        d = _laden()
        a = d.get("aufnahme")
        if not isinstance(a, dict):
            raise ValueError("Es läuft keine Aufnahme.")
        d["aufnahme"] = None
        name, folge = str(a.get("name") or ""), [z for z in a.get("schritte") or [] if isinstance(z, str)]
        if folge and name and name not in d["ablaeufe"]:
            d["ablaeufe"][name] = {"schritte": folge[:SCHRITTE_MAX], "erstellt": time.strftime("%Y-%m-%d %H:%M"),
                                   "laeufe": 0, "fehler": {}, "danach": {}}
        _speichern(d)
    return name, folge


def abbrechen() -> str:
    """Gezielte Aufnahme verwerfen. Gibt den Namen zurück."""
    with _sperre:
        d = _laden()
        a = d.get("aufnahme")
        if not isinstance(a, dict):
            raise ValueError("Es läuft keine Aufnahme.")
        d["aufnahme"] = None
        _speichern(d)
    return str(a.get("name") or "")


def loeschen(name: str) -> bool:
    name = zeile_normal(name).lower()
    with _sperre:
        d = _laden()
        if name not in d["ablaeufe"]:
            return False
        folge = "\n".join(d["ablaeufe"].pop(name)["schritte"])
        if folge not in d["vorgeschlagen"]:
            d["vorgeschlagen"].append(folge)          # nicht gleich wieder vorschlagen
        _speichern(d)
    return True


def anhaengen(name: str) -> str:
    """Den häufigsten Befehl „danach“ als letzten Schritt anhängen. Gibt ihn zurück."""
    name = zeile_normal(name).lower()
    with _sperre:
        d = _laden()
        a = d["ablaeufe"].get(name)
        if a is None:
            raise ValueError(f"Keinen Ablauf „{name}“.")
        danach = {z: n for z, n in (a.get("danach") or {}).items() if int(n) >= DANACH_AB}
        if not danach:
            raise ValueError(f"Nach „{name}“ kam noch kein Befehl oft genug ({DANACH_AB}×).")
        if len(a["schritte"]) >= SCHRITTE_MAX:
            raise ValueError(f"„{name}“ hat schon {SCHRITTE_MAX} Schritte.")
        zeile = max(danach, key=lambda z: int(danach[z]))
        a["schritte"].append(zeile)
        a["danach"].pop(zeile, None)
        _speichern(d)
    return zeile


def vergessen() -> int:
    """Den Befehlsverlauf leeren (gespeicherte Abläufe bleiben). Gibt die Zahl der Einträge zurück."""
    with _sperre:
        d = _laden()
        n = len(d["verlauf"])
        d["verlauf"], d["vorgeschlagen"] = [], []
        _speichern(d)
    _vorschlag.clear()
    return n


# ---------------------------------------------------------------------------
# Abspielen: Strichliste
# ---------------------------------------------------------------------------

def abspielbar(zeile: str) -> bool:
    return bool(zeile_normal(zeile)) and befehl_von(zeile) not in NICHT_AUFNEHMEN and not _GEHEIM.search(zeile)


def lauf_ende(name: str, fehler_schritte: list[int], jetzt: float | None = None) -> None:
    """Nach einem Lauf: Zähler, Fehler je Schritt (0-basiert), Merker für „danach“."""
    name = zeile_normal(name).lower()
    with _sperre:
        d = _laden()
        a = d["ablaeufe"].get(name)
        if a is None:
            return
        a["laeufe"] = int(a.get("laeufe", 0)) + 1
        a["zuletzt"] = time.strftime("%Y-%m-%d %H:%M")
        fehler = a.setdefault("fehler", {})
        for i in fehler_schritte:
            fehler[str(i)] = int(fehler.get(str(i), 0)) + 1
        _speichern(d)
    _zuletzt.clear()
    _zuletzt.update({"name": name, "ende": time.time() if jetzt is None else jetzt})


def liste_zeilen() -> list[str]:
    with _sperre:
        d = _laden()
    zeilen = []
    for name in sorted(d["ablaeufe"]):
        a = d["ablaeufe"][name]
        zeilen.append(f"▶ {name} · {len(a['schritte'])} Schritte · {int(a.get('laeufe', 0))}× abgespielt"
                      + (f" · zuletzt {a['zuletzt']}" if a.get("zuletzt") else ""))
        fehler = a.get("fehler") or {}
        for i, z in enumerate(a["schritte"]):
            n = int(fehler.get(str(i), 0))
            zeilen.append(f"   {i + 1}. {z}" + (f"   ⚠ {n}× mit Fehler" if n else ""))
        oft = [(z, int(n)) for z, n in (a.get("danach") or {}).items() if int(n) >= DANACH_AB]
        for z, n in sorted(oft, key=lambda x: -x[1])[:1]:
            zeilen.append(f"   💡 danach oft: {z} ({n}×) – /ablauf anhaengen {name}")
    if isinstance(d.get("aufnahme"), dict):
        a = d["aufnahme"]
        zeilen.append(f"⏺ Aufnahme läuft: {a.get('name')} ({len(a.get('schritte') or [])} Schritte) – /ablauf stopp")
    return zeilen
