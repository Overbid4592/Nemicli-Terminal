"""Neustart von NemiCLI und Wache (/restart, Frage nach Updates und Installationen).

Ausgelöst wird er nur über die Eingabe des Nutzers bzw. dessen Antwort auf die Frage – nie über ein Werkzeug,
ein Menü der KI oder die Inbox. Ablauf: Wache stoppen → NemiCLI beendet sich mit CODE → das eigene Fenster
startet NemiCLI neu (außerhalb davon startet ein neuer Prozess, der auf das Ende des alten wartet). Die Wache
startet der neue NemiCLI-Start selbst.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

CODE = 75             # gleich ui/terminal_fenster.NEUSTART_CODE
WACHHUND_S = 20       # hängt das normale Beenden länger, endet der Prozess hart
WARTEN_S = 60         # so lange wartet der neue Prozess höchstens auf das Ende des alten
BEFEHLE = ("/restart", "/neustart")

ANGEFORDERT = False
BEENDEN = None        # von der Oberfläche gesetzt: beendet sie (Textual app.exit)
_lock = threading.Lock()
_fertig = False


def ist_befehl(text: str) -> bool:
    return (text or "").strip().lower() in BEFEHLE


def im_eigenen_fenster() -> bool:
    return os.environ.get("NEMICLI_FENSTER") == "1"


def offene_arbeit() -> list[str]:
    """Hintergrund-Aufgaben, die der Neustart abbricht."""
    try:
        import hintergrund
        return [e.get("titel") or e.get("tool") or "Aufgabe" for e in hintergrund.laufend()]
    except Exception:
        return []


def wache_stoppen() -> str:
    """Wache beenden (wartet bis zu 15 s). Der nächste NemiCLI-Start startet sie wieder."""
    try:
        from wache import zugang
        if not zugang.laeuft():
            return ""
        return zugang.stoppen()
    except Exception as e:
        return f"Wache ließ sich nicht stoppen: {e}"


async def ausfuehren() -> None:
    """Der ganze Ablauf mit Meldungen: offene Arbeit nennen, Wache stoppen, NemiCLI neu starten."""
    import asyncio
    import ui
    offen = offene_arbeit()
    if offen:
        ui.warn("⏹ Wird abgebrochen: " + ", ".join(offen))
    ui.info("🔄 Neustart – die Wache wird beendet …")
    meldung = await asyncio.to_thread(wache_stoppen)
    if meldung:
        ui.info(f"🛡 {meldung}")
    ui.info("🔄 NemiCLI startet neu …")
    anfordern()


def anfordern() -> None:
    """Neustart vormerken und die Oberfläche beenden; der Wachhund beendet hart, falls etwas hängt."""
    global ANGEFORDERT
    ANGEFORDERT = True
    threading.Thread(target=_wachhund, daemon=True, name="neustart-wachhund").start()
    if BEENDEN is None:               # ohne Vollbild-Oberfläche (klassisch, beim Start): gleich beenden
        beenden()
    try:
        BEENDEN()
    except Exception:
        beenden()


def _wachhund() -> None:
    time.sleep(WACHHUND_S)
    beenden()


def _neuer_prozess() -> list[str]:
    import paths
    warte = ["--warte-pid", str(os.getpid())]
    if getattr(sys, "frozen", False):
        w = paths.exe_ohne_fenster()
        if w.name.lower() == "nemicli.exe":
            return [str(w), *warte]
        return [str(paths.exe_mit_fenster()), *warte]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), *warte]


def beenden() -> None:
    """Prozess mit CODE beenden. Außerhalb des eigenen Fensters vorher den Nachfolger starten."""
    global _fertig
    with _lock:
        if _fertig:
            return
        _fertig = True
        if not im_eigenen_fenster():
            try:
                subprocess.Popen(_neuer_prozess(), cwd=str(Path.home()), close_fds=True,
                                 creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            except Exception:
                pass
        try:                             # os._exit überspringt atexit: Bericht sonst als „unsauber beendet“
            import debugbericht
            debugbericht.ereignis("Neustart (/restart)")
            debugbericht.beenden()
        except Exception:
            pass
        for strom in (sys.stdout, sys.stderr):
            try:
                strom.flush()
            except Exception:
                pass
        os._exit(CODE)


def warten(argv: list[str]) -> None:
    """--warte-pid <pid>: erst weiter, wenn der alte Prozess beendet ist (Grafikkarte, Dateien frei)."""
    if "--warte-pid" not in argv:
        return
    i = argv.index("--warte-pid")
    try:
        pid = int(argv[i + 1])
    except (IndexError, ValueError):
        pid = 0
    del argv[i:i + 2]
    if pid <= 0 or pid == os.getpid():
        return
    try:
        import psutil
        psutil.Process(pid).wait(timeout=WARTEN_S)
    except Exception:
        pass
