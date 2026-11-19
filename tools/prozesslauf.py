"""
prozesslauf.py - Externes Programm mit fester Zeitgrenze.

subprocess.run(capture_output=True, timeout=…) wartet unter Windows nach kill() ohne Grenze weiter,
wenn ein Kindprozess des Programms die Pipes noch offen hält. Deshalb gehen die Ausgaben in
temporäre Dateien, und bei Zeitüberschreitung wird der ganze Prozessbaum beendet.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _taskkill() -> str:
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")


def baum_beenden(p: subprocess.Popen) -> None:
    """Prozess samt Kindprozessen beenden; wartet höchstens kurz."""
    if os.name == "nt":
        try:
            subprocess.run([_taskkill(), "/PID", str(p.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=15, creationflags=_NOWIN)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def laufen(befehl: list[str], timeout: float, cwd=None, env=None) -> tuple[int, bytes, bytes]:
    """(Exitcode, stdout, stderr). FileNotFoundError, wenn das Programm fehlt;
    subprocess.TimeoutExpired nach `timeout` (der Prozessbaum ist dann beendet)."""
    with tempfile.TemporaryFile() as aus, tempfile.TemporaryFile() as fehler:
        p = subprocess.Popen(befehl, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=aus, stderr=fehler,
                             creationflags=_NOWIN)
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            baum_beenden(p)
            raise
        aus.seek(0)
        fehler.seek(0)
        return code, aus.read(), fehler.read()
