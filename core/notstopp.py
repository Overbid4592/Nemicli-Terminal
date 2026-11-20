"""
notstopp.py - Not-Stopp: ESC oder „STOP“ beendet die laufende Aufgabe der KI sofort.

ESC hat Vorrang vor allem, in jedem Bereich (Antwort, Werkzeug, Rückfrage, Helfer, Bild, Video, Musik):
  - Die Antwort bricht ab, kein weiteres Werkzeug der Runde startet; ein laufender `befehl` und
    Python-Code in der Sandbox werden beendet, Bilder, Videos und Musik halten beim nächsten
    Rechenschritt an, Helfer brechen ab. Was dann noch läuft, wartet die Runde nicht mehr ab:
    spätestens nach NOTBREMSE_S ist sie hart beendet (main._mit_notbremse).
  - Gelöscht oder zurückgedreht wird nichts: die Todo-Liste bleibt stehen, schon Geändertes bleibt,
    fertige Bilder bleiben. Zurückholen geht einzeln mit /undo.
  - Hintergrund-Aufgaben (/aufgaben) laufen weiter – der Abbruch gilt nur im Vordergrund.
  - Die nächste Nachricht an die KI trägt den Hinweis: nichts von selbst fortsetzen – war es ein
    Versehen und soll sie weitermachen, macht sie genau dort weiter.

„STOP“ als eigene Nachricht (auch während die KI arbeitet) wirkt wie ESC; in einer freien
Runde antwortet die KI nur kurz, ohne Werkzeuge.
"""

from __future__ import annotations

import contextvars
import re
import time

# Eine Nachricht, die nur aus einem Stopp-Wort besteht
_STOP_RE = re.compile(r"^\s*(stop+|stopp+|halt|abbruch|abbrechen|aufh(ö|oe)ren|h(ö|oe)r auf|not-?stopp?)\s*[!.]*\s*$",
                      re.IGNORECASE)

NOTBREMSE_S = 0.5                      # so lange darf die Runde nach ESC noch sauber enden, dann wird sie abgebrochen

HINWEIS_NACH_STOPP = (
    "\n\n[Not-Stopp: Der Nutzer hat die vorige Aufgabe mit ESC/STOP unterbrochen. Gelöscht wurde nichts, eine "
    "offene Todo-Liste steht noch. Setz nichts von selbst fort und beginne nichts Neues – nur, was er jetzt "
    "verlangt. Sagt er, es war ein Versehen oder du sollst weitermachen, machst du genau dort weiter, wo du "
    "unterbrochen wurdest, nach seiner vorigen Anweisung.]")

STOP_RUNDE = (
    "[Not-Stopp: Der Nutzer hat STOP gesagt. Du hörst sofort auf: keine Werkzeuge, keine neuen Schritte, "
    "keine Fortsetzung. Bestätige in einem kurzen Satz in deinen Worten, dass du aufgehört hast, und warte auf "
    "seine Anweisung.]")

GESPERRT_TEXT = "Nicht ausgeführt – Not-Stopp: In dieser Runde laufen keine Werkzeuge."

_zustand = {"ausgeloest": False, "nachwirken": False, "stop_runde": False, "geaendert": [], "start": 0.0,
           "laufend": 0}

# Läuft dieser Code für die Runde im Vordergrund? Werkzeug-Fäden erben den Wert (asyncio.to_thread kopiert den
# Kontext); Hintergrund-Aufgaben starten mit False und rechnen nach ESC weiter.
_VORDERGRUND: contextvars.ContextVar[bool] = contextvars.ContextVar("notstopp_vordergrund", default=False)


class Abgebrochen(BaseException):
    """Mit ESC abgebrochen. BaseException: kein `except Exception` in einem Motor fängt es ab und rechnet weiter;
    actions.run macht daraus ein Ergebnis."""


def vordergrund(an: bool = True) -> contextvars.Token:
    return _VORDERGRUND.set(an)


def vordergrund_ende(token: contextvars.Token) -> None:
    try:
        _VORDERGRUND.reset(token)
    except ValueError:                            # anderer Kontext: der Wert gehört dort ohnehin niemandem
        pass


def abbrechen() -> bool:
    """Soll die Arbeit an dieser Stelle aufhören? Nur im Vordergrund, nur nach ESC/STOP."""
    return _zustand["ausgeloest"] and _VORDERGRUND.get()


def pruefen() -> None:
    """Für Rechenschleifen (Bild, Video, Musik): nach ESC hier aufhören."""
    if abbrechen():
        raise Abgebrochen()


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
    """ESC/STOP während der Runde: ab jetzt startet nichts mehr, die nächste Runde bekommt den Hinweis.
    Die Todo-Liste bleibt stehen – nach einem Versehen geht es dort weiter."""
    _zustand["ausgeloest"] = True
    _zustand["nachwirken"] = True
    try:
        import actions
        actions.befehl_stoppen()                  # ein laufender Befehl oder Sandbox-Code endet sofort
    except Exception as e:                        # sonst liefe er still weiter
        try:
            import debugbericht
            debugbericht.fehler("Not-Stopp: Befehl beenden", e)
            import ui
            ui.warn(f"⏹ Der laufende Befehl ließ sich nicht beenden: {e}")
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
    zeilen = ["⏹ Not-Stopp – die KI hat aufgehört. Es startet nichts mehr, gelöscht wurde nichts."]
    g = geaendert()
    if g:
        zeilen.append("Vorher schon ausgeführt (bleibt so, einzeln zurück mit /undo):")
        zeilen += [f"  • {x}" for x in g]
    else:
        zeilen.append("Am System wurde in dieser Runde nichts geändert.")
    try:
        import hintergrund
        for e in hintergrund.laufend():
            zeilen.append(f"⏳ Im Hintergrund läuft weiter: #{e['nr']} {e['titel']}")
    except Exception:
        pass
    zeilen.append("War es ein Versehen? Sag einfach „weiter“ – dann macht die KI dort weiter.")
    return "\n".join(zeilen)


def _test_reset() -> None:                                              # nur für Tests
    _zustand.update(ausgeloest=False, nachwirken=False, stop_runde=False, geaendert=[], start=0.0)
