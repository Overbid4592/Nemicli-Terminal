"""kompass.py – Kompass: beim Tippen von „/“ ein Begleitfenster mit allen Befehlen (Sitzungsseite).

Schalter `kompass` (/kompass an|aus, Vorgabe aus). Ist er an und beginnt die Eingabe mit „/“, startet die Sitzung das
Fenster (ui/kompass_fenster.py, eigener Prozess über gui_bruecke) und schickt ihm bei jeder Änderung den
getippten Text; der passende Befehl leuchtet dort auf. Ein Klick auf einen Unterpunkt setzt den Befehl in die
Eingabe – ausgeführt wird erst mit Enter. Daten kommen aus core/commands.py, nichts ist doppelt gepflegt.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

SCHALTER = "kompass"
NEUSTART_S = 10.0              # so lange nach einem Start kein zweiter (Fenster stürzt ab, startet langsam)

_bruecke = None
_gestartet = 0.0
_gesendet = ""                 # zuletzt geschickter Text („“ = Fenster ist versteckt)
_wunsch = ""                   # zuletzt getippter Befehl – bekommt ein Fenster, das sich erst verbindet
_setzen = None                 # Funktion(text), die den Befehl in die Eingabe schreibt (vom Bildschirm)


def an() -> bool:
    try:
        import config
        return bool(config.load().get(SCHALTER, False))
    except Exception:
        return False


def setzen(an_aus: bool) -> None:
    import config
    config.update(**{SCHALTER: bool(an_aus)})
    if not an_aus:
        verstecken()


def daten() -> dict:
    """Befehle, Unterpunkte und Themen für das Fenster – aus core/commands.py."""
    import commands as C
    return {
        "befehle": [{"name": c.name, "kurz": c.short, "arg": c.arg, "lang": c.long or c.short}
                    for c in C.COMMAND_LIST],
        "untermenues": {name: [{"zusatz": z, "anzeige": a, "frage": f} for z, a, f in eintraege]
                        for name, eintraege in C.UNTERMENUES.items()},
        "themen": [{"symbol": s, "name": n, "befehle": list(b)} for s, n, b in C.THEMEN],
    }


def getippt(text: str, setzen_fn=None) -> None:
    """Vom Bildschirm bei jeder Änderung der Eingabe (im Faden der Oberfläche)."""
    global _gesendet, _setzen, _wunsch
    if setzen_fn is not None:
        _setzen = setzen_fn
    text = text or ""
    befehl = text.startswith("/") and "\n" not in text
    if not befehl or not an():
        _wunsch = ""
        if _gesendet:
            verstecken()
        return
    _wunsch = text
    if text == _gesendet:
        return
    if not _bereit():
        return
    _gesendet = text
    _bruecke.senden({"t": "eingabe", "text": text})


def verstecken() -> None:
    global _gesendet
    _gesendet = ""
    if _bruecke is not None and _bruecke.verbunden():
        _bruecke.senden({"t": "verstecken"})


def _bereit() -> bool:
    """Brücke und Fenster da? Sonst einmal starten (das Fenster meldet sich dann mit „laden“)."""
    global _bruecke, _gestartet
    if _bruecke is not None and _bruecke.verbunden():
        return True
    if time.monotonic() - _gestartet < NEUSTART_S:
        return False
    _gestartet = time.monotonic()
    try:
        if _bruecke is None:
            _bruecke = _neue_bruecke()
        _fenster_starten(_bruecke)
    except Exception:
        pass
    return False


def _laden(_a: dict) -> None:
    """Das Fenster hat sich verbunden: Befehle schicken und den Stand der Eingabe."""
    global _gesendet
    _bruecke.senden({"t": "befehle", **daten()})
    if _wunsch:
        _gesendet = _wunsch
        _bruecke.senden({"t": "eingabe", "text": _wunsch})


def _einsetzen(a: dict) -> None:
    """Klick im Fenster: nur ein Befehl (beginnt mit /, eine Zeile) kommt in die Eingabe, abgeschickt wird er nicht."""
    text = str(a.get("text") or "").lstrip()[:400]          # Leerzeichen am Ende: dort tippt der Nutzer weiter
    if text.startswith("/") and "\n" not in text and "\r" not in text and _setzen is not None:
        _setzen(text)


def _neue_bruecke():
    import gui_bruecke
    loop = asyncio.get_event_loop()
    return gui_bruecke.GuiBruecke(loop, {
        "laden": lambda a: loop.call_soon_threadsafe(_laden, a),
        "einsetzen": lambda a: loop.call_soon_threadsafe(_einsetzen, a),
    })


def fenster_befehl() -> list[str]:
    import paths
    if getattr(sys, "frozen", False):
        return [str(paths.exe_ohne_fenster()), "--kompass"]
    py = Path(sys.executable)
    fensterlos = py.with_name("pythonw.exe")
    return [str(fensterlos if fensterlos.is_file() else py), str(Path(paths.INSTALL) / "main.py"), "--kompass"]


def _fenster_starten(bruecke) -> None:
    import subprocess
    env = dict(os.environ, NEMICLI_GUI_PIPE=bruecke.adresse, NEMICLI_GUI_KEY=bruecke.schluessel.hex())
    subprocess.Popen(fenster_befehl(), env=env, cwd=os.getcwd())
