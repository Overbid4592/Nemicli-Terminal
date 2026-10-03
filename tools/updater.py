"""
updater.py - /update: NemiCLI per Git aktualisieren, ohne selbst zu tippen.

Ablauf: Stand merken → `git pull --ff-only` → neuen Stand merken → hat sich
requirements.txt geändert, Abhängigkeiten (nach Rückfrage in main.py) nachziehen.
Nur Fast-Forward: bei lokalen Änderungen, die kollidieren würden, bricht Git ab
und wir zeigen die Meldung – es wird nie etwas überschrieben oder gemergt.

Funktioniert nur im Skript-Modus mit Git-Repo und eingerichtetem Remote. In der
exe (kein Git) und ohne Remote gibt es eine klare Ansage statt eines Fehlers.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    from paths import ROOT, INSTALL, FROZEN
except Exception:
    ROOT = INSTALL = Path(__file__).resolve().parent.parent
    FROZEN = bool(getattr(sys, "frozen", False))

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(*args: str, timeout: int = 120) -> tuple[int, str]:
    """(Exit-Code, Ausgabe) eines Git-Befehls im Projektordner."""
    try:
        r = subprocess.run(["git", *args], cwd=INSTALL, capture_output=True, text=True,
                           timeout=timeout, creationflags=_NOWIN)
        out = (r.stdout + r.stderr).strip()
        return r.returncode, out
    except FileNotFoundError:
        return 127, "git ist nicht installiert (oder nicht im PATH)."
    except subprocess.TimeoutExpired:
        return 124, "Git hat zu lange gebraucht (Netz?)."
    except Exception as e:
        return 1, str(e)


def why_not() -> str | None:
    """Grund, warum /update hier nicht geht – oder None, wenn alles bereit ist."""
    if FROZEN:
        return ("Als exe gibt es kein Git – lade die neue Version von Hand herunter "
                "und ersetze den Ordner (deine Daten liegen daneben und bleiben).")
    if not (INSTALL / ".git").exists():
        return "Kein Git-Repo: NemiCLI wurde nicht per `git clone` geholt."
    code, out = _git("remote")
    if code != 0:
        return out
    if not out.strip():
        return ("Kein Remote eingerichtet – Git weiß nicht, woher es Updates holen soll. "
                "Einmalig: git remote add origin <URL>")
    return None


def run(on_status) -> dict:
    """Holt Updates. Ergebnis:
        ok            True, wenn git pull sauber durchlief
        before/after  Commit-Hashes (kurz)
        changed       True, wenn sich etwas geändert hat
        files         geänderte Dateien (Liste)
        requirements  True, wenn requirements.txt dabei war
        output        Git-Ausgabe (auch im Fehlerfall)"""
    res = {"ok": False, "before": "", "after": "", "changed": False,
           "files": [], "requirements": False, "output": ""}
    on_status("prüfe aktuellen Stand …")
    _, res["before"] = _git("rev-parse", "--short", "HEAD")
    code, dirty = _git("status", "--porcelain", "--untracked-files=no")
    if code == 0 and dirty.strip():
        res["output"] = ("Es gibt lokale, nicht committete Änderungen:\n" + dirty +
                         "\nErst committen oder verwerfen (git stash), dann nochmal /update.")
        return res
    on_status("hole Updates (git pull --ff-only) …")
    code, out = _git("pull", "--ff-only")
    res["output"] = out
    if code != 0:
        return res
    res["ok"] = True
    _, res["after"] = _git("rev-parse", "--short", "HEAD")
    if res["after"] and res["after"] != res["before"]:
        res["changed"] = True
        _, files = _git("diff", "--name-only", f"{res['before']}..{res['after']}")
        res["files"] = [f for f in files.splitlines() if f.strip()]
        res["requirements"] = "requirements.txt" in res["files"]
    return res


def install_requirements(on_status) -> tuple[bool, str]:
    """pip install -r requirements.txt mit dem laufenden Python (also dem venv)."""
    req = INSTALL / "requirements.txt"
    if not req.exists():
        return False, "requirements.txt fehlt."
    on_status("installiere Abhängigkeiten (pip) …")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)],
                           cwd=INSTALL, capture_output=True, text=True, timeout=900,
                           creationflags=_NOWIN)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-8:])
        return r.returncode == 0, tail
    except Exception as e:
        return False, str(e)
