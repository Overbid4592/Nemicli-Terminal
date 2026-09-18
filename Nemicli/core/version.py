"""
version.py - Welche NemiCLI läuft hier eigentlich?  (/version, --version)

VERSION ist die Hauptnummer, die der Entwickler vergibt (3.5 Alpha, 3.6 …); STAND ist
das Datum des Codes (Jahr.Monat.Tag). Dazu kommt, wenn das
Projekt als Git-Repo läuft, der kurze Commit-Hash und dessen Datum – so lässt
sich bei Support-Fragen genau sagen, welcher Stand gemeint ist. In der
gepackten exe gibt es kein Git; dann steht nur die VERSION.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

VERSION = "3.5 Alpha"
STAND = "2026.09.18"
AUTHOR = "D. Hoffmann (Vibecoder)"
BUILT_WITH = "Claude Opus 5 · Anthropic"

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT, INSTALL as _INSTALL, FROZEN as _FROZEN
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = _INSTALL = Path(__file__).resolve().parent.parent
    _FROZEN = bool(getattr(sys, "frozen", False))

_GIT_CACHE: dict | None = None


def _git(*args: str) -> str:
    """Ein Git-Befehl im Projektordner, leerer String bei jedem Problem."""
    try:
        r = subprocess.run(["git", *args], cwd=_INSTALL, capture_output=True, text=True,
                           timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def git_info() -> dict:
    """{"hash", "date", "branch", "dirty"} – alles leer, wenn kein Git-Repo da ist."""
    global _GIT_CACHE
    if _GIT_CACHE is None:
        info = {"hash": "", "date": "", "branch": "", "dirty": False}
        if not _FROZEN and (_INSTALL / ".git").exists():
            info["hash"] = _git("rev-parse", "--short", "HEAD")
            info["date"] = _git("log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M")
            info["branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")
            info["dirty"] = bool(_git("status", "--porcelain"))
        _GIT_CACHE = info
    return _GIT_CACHE


def build_label() -> str:
    """Kurzform für Banner/Statuszeile: '3.5 Alpha · 2026.09.18 (a4f25d1)' bzw. '… (exe)'."""
    g = git_info()
    if g["hash"]:
        return f"{VERSION} · {STAND} ({g['hash']}{'*' if g['dirty'] else ''})"
    return f"{VERSION} · {STAND} ({'exe' if _FROZEN else 'Skript'})"


def report(model: str | None = None, persona: str | None = None) -> list[tuple[str, str]]:
    """Zeilen für /version: (Bezeichnung, Wert)."""
    g = git_info()
    rows = [("Version", VERSION), ("Stand", STAND),
            ("Entwickelt von", AUTHOR),
            ("Gebaut mit", BUILT_WITH)]
    if g["hash"]:
        rows.append(("Build", f"{g['hash']} · {g['branch']} · {g['date']}"
                              + ("  (lokale Änderungen)" if g["dirty"] else "")))
    else:
        rows.append(("Build", "exe (PyInstaller)" if _FROZEN else "Skript, kein Git-Repo"))
    rows.append(("Python", f"{sys.version.split()[0]} · {sys.executable}"))
    rows.append(("System", f"{platform.system()} {platform.release()} ({platform.machine()})"))
    rows.append(("Datenordner", str(_ROOT)))
    if model:
        rows.append(("Modell", model))
    if persona:
        rows.append(("Persönlichkeit", persona))
    return rows
