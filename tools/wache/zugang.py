"""Zugang zur Wache aus anderen Prozessen und für die Werkzeuge der Persönlichkeit.

Die Wache läuft als eigener Hintergrund-Prozess (main.py --wache). Das
interaktive NemiCLI und die Werkzeuge der Persönlichkeit greifen über denselben SQLite-Speicher
zu (WAL: Lesen stört das Schreiben nicht). `motor` ist nur im Hintergrund-
Prozess gesetzt – dort wirken Justierungen sofort, sonst beim nächsten
Einstellungs-Check (alle 30 s) des Hintergrund-Prozesses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from . import DB_PFAD, LOCK, STOPP, ordner_anlegen
from .speicher import Speicher

motor = None                       # im Hintergrund-Prozess: die laufende Motor-Instanz
_speicher: Speicher | None = None
_START = time.time()               # Start dieses Prozesses – das Lebenszeichen ändert ihn nicht
STOPP_WARTEN_S = 15                # so lange wartet stoppen() auf das Ende (Dienst prüft jede Sekunde)


def speicher() -> Speicher:
    global _speicher
    if motor is not None:
        return motor.speicher
    if _speicher is None:
        ordner_anlegen()
        _speicher = Speicher(DB_PFAD)
    return _speicher


# ---------------------------------------------------------------------------
# Hintergrund-Prozess: läuft er? starten, stoppen
# ---------------------------------------------------------------------------

def _build() -> str:
    try:
        import version
        return version.build_nummer()
    except Exception:
        return ""


def lock_schreiben() -> None:
    """Lebenszeichen (alle 10 s). `start` bleibt die Startzeit des Prozesses, `build`
    sein Programmstand – beides braucht veraltet()."""
    ordner_anlegen()
    LOCK.write_text(json.dumps({"pid": os.getpid(), "start": _START, "build": _build(),
                                "zuletzt": time.time(), "python": sys.executable}), encoding="utf-8")


def lock_loeschen() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def laeuft() -> dict | None:
    """Info über den laufenden Hintergrund-Prozess oder None."""
    if motor is not None:
        return {"pid": os.getpid(), "start": getattr(motor, "start_zeit", 0.0), "hier": True}
    try:
        info = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = int(info.get("pid", 0))
    try:
        p = psutil.Process(pid)
        name = p.name().lower()
        if p.is_running() and ("python" in name or "nemicli" in name):   # als exe: NemiCLI.exe
            info["hier"] = False
            return info
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        pass
    lock_loeschen()                          # Leiche
    return None


def code_neuer_als(zeit: float) -> bool:
    """Wurde am Programm (tools/wache, main.py, actions, modes, persona) seit `zeit`
    etwas geändert? Dann läuft der Hintergrund-Prozess mit altem Stand."""
    install = Path(__file__).resolve().parent.parent.parent
    dateien = list((install / "tools" / "wache").glob("*.py")) + [
        install / "main.py", install / "tools" / "actions.py", install / "core" / "modes.py",
        install / "core" / "persona.py", install / "engines" / "comfyui.py"]
    try:
        return any(p.exists() and p.stat().st_mtime > zeit for p in dateien)
    except OSError:
        return False


def veraltet(info: dict | None) -> bool:
    """Läuft die Wache mit einem anderen Programmstand als dieses NemiCLI? Anderer Build
    (auch in der exe) oder – im Quelltext – seit ihrem Start geänderter Code."""
    if not info or info.get("hier"):
        return False
    if info.get("build") != _build():
        return True
    return code_neuer_als(float(info.get("start") or 0))


def wache_befehl(install_ordner: Path) -> list[str]:
    """Befehl für den Hintergrund-Prozess. Als exe ist das die exe selbst –
    dort gibt es kein main.py und kein pythonw."""
    if getattr(sys, "frozen", False):
        from paths import exe_ohne_fenster
        return [str(exe_ohne_fenster()), "--wache"]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    return [str(exe), str(install_ordner / "main.py"), "--wache"]


def starten(install_ordner: Path) -> str:
    """Hintergrund-Prozess ohne Fenster starten."""
    if laeuft():
        return "Die Wache läuft schon."
    try:
        STOPP.unlink(missing_ok=True)
    except OSError:
        pass
    befehl = wache_befehl(install_ordner)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if not Path(befehl[0]).name.lower().startswith("pythonw"):
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)      # Konsolenprogramm ohne Fenster
    try:
        subprocess.Popen(befehl,
                         cwd=str(install_ordner), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return f"Start fehlgeschlagen: {exc}"
    for _ in range(30):
        time.sleep(0.2)
        if laeuft():
            return "🛡 Wache gestartet – läuft jetzt im Hintergrund (Symbol in der Taskleiste)."
    return "Wache gestartet, meldet sich aber noch nicht – bitte in ein paar Sekunden /wache status."


def stoppen() -> str:
    info = laeuft()
    if not info:
        return "Die Wache läuft nicht."
    ordner_anlegen()
    STOPP.write_text(str(time.time()), encoding="utf-8")
    for _ in range(int(STOPP_WARTEN_S / 0.2)):
        time.sleep(0.2)
        if not laeuft():
            return "Wache gestoppt."
    return "Stopp-Signal gesetzt – die Wache beendet sich gleich."


def stopp_gewuenscht() -> bool:
    return STOPP.exists()


# ---------------------------------------------------------------------------
# Windows-Autostart (HKCU\…\Run) – nur der Nutzer, über /wache autostart
# ---------------------------------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "NemiCLI-Wache"


def autostart_setzen(an: bool, install_ordner: Path) -> str:
    try:
        import winreg
    except ImportError:
        return "Nur unter Windows."
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if an:
                befehl = " ".join(f'"{t}"' if not t.startswith("--") else t
                                  for t in wache_befehl(install_ordner))
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, befehl)
                return ("Autostart eingetragen (HKCU\\…\\Run\\NemiCLI-Wache). Hinweis: Die Wache "
                        "selbst meldet diesen neuen Autostart-Eintrag beim nächsten Inventar – "
                        "das ist dann richtig so.")
            try:
                winreg.DeleteValue(k, _RUN_NAME)
            except FileNotFoundError:
                pass
            return "Autostart entfernt."
    except OSError as exc:
        return f"Registry nicht änderbar: {exc}"


def autostart_auffrischen(install_ordner: Path) -> bool:
    """Steht im Autostart ein anderer Befehl als der aktuelle (z. B. noch die
    Terminal-exe), wird er ersetzt. True, wenn geändert. Nur aus der exe –
    ein Lauf aus dem Quelltext soll den Autostart nicht umbiegen."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            alt = str(winreg.QueryValueEx(k, _RUN_NAME)[0])
    except (ImportError, OSError):
        return False
    neu = " ".join(f'"{t}"' if not t.startswith("--") else t for t in wache_befehl(install_ordner))
    if alt == neu:
        return False
    autostart_setzen(True, install_ordner)
    return True


def autostart_aktiv() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _RUN_NAME)
            return True
    except (ImportError, OSError):
        return False
