"""
notstopp.py - Not-Stopp: ESC oder „STOP“ beendet die laufende Aufgabe der KI sofort.

Was der Not-Stopp tut:
  - Die Antwort bricht ab, kein weiteres Werkzeug der Runde startet, vorbereitete Schritte
    (auch die gelbe Todo-Liste) werden verworfen; ein laufender `befehl` wird beendet.
  - Bilder, Bild-Serien und Videos rechnen zu Ende: abgebrochen wäre der Strom vergeudet.
    Hintergrund-Aufgaben laufen weiter.
  - Danach zeigt NemiCLI, was in dieser Runde schon geändert wurde, und bietet an, die
    gesicherten Änderungen aus dem Papierkorb zurückzuholen – nur auf ausdrücklichen Wunsch.
  - Die nächste Nachricht an die KI trägt den Hinweis, nicht von selbst weiterzumachen.

„STOP“ als eigene Nachricht (auch während die KI arbeitet) wirkt wie ESC; in einer freien
Runde antwortet die KI nur kurz, ohne Werkzeuge.
"""

from __future__ import annotations

import re
import time

# Eine Nachricht, die nur aus einem Stopp-Wort besteht
_STOP_RE = re.compile(r"^\s*(stop+|stopp+|halt|abbruch|abbrechen|aufh(ö|oe)ren|h(ö|oe)r auf|not-?stopp?)\s*[!.]*\s*$",
                      re.IGNORECASE)

# Laufen zu Ende, auch nach einem Not-Stopp – ihr Strom ist schon ausgegeben
ZU_ENDE = {"bild_malen", "bild_serie", "comic_malen", "tagesrueckblick", "musik_machen", "musik_cover", "video_erstellen"}

HINWEIS_NACH_STOPP = (
    "\n\n[Not-Stopp: Der Nutzer hat die vorige Aufgabe mit STOP/ESC abgebrochen. Mach sie nicht von selbst "
    "weiter, beginne nichts Neues und führe keine vorbereiteten Schritte aus – nur, was er jetzt verlangt.]")

STOP_RUNDE = (
    "[Not-Stopp: Der Nutzer hat STOP gesagt. Du hörst sofort auf: keine Werkzeuge, keine neuen Schritte, "
    "keine Fortsetzung. Bestätige in einem kurzen Satz in deinen Worten, dass du aufgehört hast, und warte auf "
    "seine Anweisung.]")

GESPERRT_TEXT = "Nicht ausgeführt – Not-Stopp: In dieser Runde laufen keine Werkzeuge."

_zustand = {"ausgeloest": False, "nachwirken": False, "stop_runde": False, "geaendert": [], "start": 0.0,
           "laufend": 0}


def ist_stop(text: str) -> bool:
    """Besteht die Nachricht nur aus einem Stopp-Wort („STOP“, „stopp!“, „abbrechen“ …)?"""
    return bool(_STOP_RE.match(text or ""))


def runde_beginnt(stop_runde: bool = False) -> None:
    """Neue Nutzer-Runde: Merker leeren. `stop_runde`: die Nachricht war selbst „STOP“."""
    _zustand.update(ausgeloest=False, stop_runde=stop_runde, geaendert=[], start=time.time())


def runde_aktiv(an: bool) -> None:
    """Runde läuft (an=True) oder ist vorbei (an=False); gepaart in try/finally aufrufen."""
    _zustand["laufend"] = max(0, _zustand["laufend"] + (1 if an else -1))


def runde_laeuft() -> bool:
    return _zustand["laufend"] > 0


def ausloesen() -> None:
    """ESC/STOP während der Runde: ab jetzt startet nichts mehr, die nächste Runde bekommt den Hinweis."""
    _zustand["ausgeloest"] = True
    _zustand["nachwirken"] = True
    try:
        import actions
        actions.befehl_stoppen()                  # ein laufender Befehl endet sofort
    except Exception as e:                        # sonst liefe er still weiter
        try:
            import debugbericht
            debugbericht.fehler("Not-Stopp: Befehl beenden", e)
            import ui
            ui.warn(f"⏹ Der laufende Befehl ließ sich nicht beenden: {e}")
        except Exception:
            pass
    try:
        import plan
        plan.verwerfen()                          # vorbereitete Schritte gelten nicht mehr
    except Exception:
        pass


def ausgeloest() -> bool:
    return _zustand["ausgeloest"]


def stop_runde() -> bool:
    return _zustand["stop_runde"]


def runde_start() -> float:
    return _zustand["start"]


def hinweis_abholen() -> str:
    """Einmal nach einem Not-Stopp: Hinweis an die nächste Nachricht der KI."""
    if not _zustand["nachwirken"]:
        return ""
    _zustand["nachwirken"] = False
    return HINWEIS_NACH_STOPP


def geaendert_merken(beschreibung: str) -> None:
    _zustand["geaendert"].append(beschreibung.splitlines()[0][:160])


def geaendert() -> list[str]:
    return list(_zustand["geaendert"])


def sicherungen_seit(start: float) -> list[dict]:
    """Papierkorb-Sicherungen dieser Runde (neueste zuerst) – die lassen sich sicher zurückholen."""
    try:
        import snapshot
    except Exception:
        return []
    grenze = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start - 1))
    return [e for e in snapshot.letzte(200) if str(e.get("zeit", "")) >= grenze and e.get("werkzeug") != "undo"]


def bericht() -> str:
    """Was nach dem Not-Stopp angezeigt wird: was schon geändert ist und was weiterläuft."""
    zeilen = ["⏹ Not-Stopp – die KI hat aufgehört. Es startet nichts mehr."]
    g = geaendert()
    if g:
        zeilen.append("Vorher schon ausgeführt:")
        zeilen += [f"  • {x}" for x in g]
    else:
        zeilen.append("Am System wurde in dieser Runde nichts geändert.")
    try:
        import hintergrund
        for e in hintergrund.laufend():
            zeilen.append(f"⏳ Im Hintergrund läuft weiter: #{e['nr']} {e['titel']}")
    except Exception:
        pass
    return "\n".join(zeilen)


def _test_reset() -> None:                                              # nur für Tests
    _zustand.update(ausgeloest=False, nachwirken=False, stop_runde=False, geaendert=[], start=0.0)
