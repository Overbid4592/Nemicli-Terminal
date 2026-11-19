"""
updater.py - /update: NemiCLI per Git aktualisieren, ohne selbst zu tippen.

Ablauf: Stand merken → `git pull --ff-only` → neuen Stand merken. Die Pakete im
venv prüft danach venvpflege.py.
Nur Fast-Forward: bei lokalen Änderungen, die kollidieren würden, bricht Git ab
und wir zeigen die Meldung – es wird nie etwas überschrieben oder gemergt.

Funktioniert nur im Skript-Modus mit Git-Repo und eingerichtetem Remote (nur https://
oder SSH). In der exe (kein Git) und ohne Remote wird dieser Teil übersprungen.
Signaturprüfung der Commits (--verify-signatures) mit SIGNATUR_SCHALTER in der Konfiguration.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    from paths import ROOT, INSTALL, FROZEN
except Exception:
    ROOT = INSTALL = Path(__file__).resolve().parent.parent
    FROZEN = bool(getattr(sys, "frozen", False))

SIGNATUR_SCHALTER = "update_signaturen_pruefen"    # nemicli.config.json: git pull --verify-signatures
_SCP = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:(?!//)[^\s]+$")   # git@host:pfad (SSH-Kurzform)


def remote_url() -> tuple[str, str]:
    """(Name, Abruf-Adresse) des Remotes, von dem `git pull` holt – ("", "") wenn keins da ist."""
    _, zweig = _git("symbolic-ref", "--quiet", "--short", "HEAD")
    name = ""
    zweig = zweig.strip()
    if zweig and not any(z.isspace() for z in zweig):
        code, out = _git("config", "--get", f"branch.{zweig}.remote")
        name = out.strip() if code == 0 else ""
    name = name or "origin"
    code, out = _git("remote", "get-url", name)
    return (name, out.strip()) if code == 0 and out.strip() else ("", "")


def remote_sicher(url: str) -> bool:
    """Nur https:// oder SSH (ssh://, git@host:pfad) – kein http://, git://, file://, kein lokaler Pfad, kein ext::."""
    u = (url or "").strip()
    if not u or any(z.isspace() for z in u):
        return False
    klein = u.lower()
    if klein.startswith(("https://", "ssh://")):
        return not klein.split("://", 1)[1].startswith("-")      # kein Host, der wie eine Option aussieht
    return bool(_SCP.match(u))


def _signaturen_pruefen() -> bool:
    try:
        import config
        return config.load().get(SIGNATUR_SCHALTER) is True
    except Exception:
        return False


def _git(*args: str, timeout: int = 120) -> tuple[int, str]:
    """(Exit-Code, Ausgabe) eines Git-Befehls im Projektordner."""
    import prozesslauf
    try:
        code, aus, fehler = prozesslauf.laufen(["git", *args], timeout, cwd=INSTALL)
        return code, (aus.decode("utf-8", "replace") + fehler.decode("utf-8", "replace")).strip()
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
    name, url = remote_url()
    if not remote_sicher(url):
        res["output"] = (f"Remote {name or '?'} zeigt auf {url or 'keine Adresse'} – geholt wird nur über https:// "
                         "oder SSH. Nichts geändert.")
        return res
    signiert = _signaturen_pruefen()
    on_status("hole Updates (git pull --ff-only" + (" --verify-signatures" if signiert else "") + ") …")
    code, out = _git("pull", "--ff-only", *(["--verify-signatures"] if signiert else []))
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
