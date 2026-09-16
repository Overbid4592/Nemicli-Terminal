"""
extlibs.py - Die Brücke von der exe zu extern installiertem torch.

Problem: Eine mit PyInstaller gepackte NemiCLI.exe bringt ihr EIGENES Python
mit. Wenn du danach `pip install torch` machst, landet torch im normalen
Python auf dem PC - die exe sieht davon nichts.

torch + diffusers mit in die exe zu packen ginge zwar, macht sie aber ~6 GB
groß. Deshalb dieser Weg: die exe sucht sich ein passendes Python auf dem PC
und hängt dessen `site-packages` HINTEN an den Suchpfad. Hinten heißt: alles,
was die exe selbst mitbringt, gewinnt weiterhin - es kommt nur dazu, was fehlt
(torch, diffusers, numpy ...).

Wichtig: Es muss die GLEICHE Python-Version sein wie die in der exe
(z.B. 3.12), sonst passen die kompilierten Teile von torch nicht.

Im normalen Betrieb (python main.py) macht dieses Modul gar nichts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
PYVER = f"{sys.version_info.major}.{sys.version_info.minor}"     # z.B. "3.12"

_state: dict = {}          # Ergebnis merken, die Suche kostet Zeit


def _run(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout or ""
    except Exception:
        return ""


def _candidates() -> list[Path]:
    """Mögliche site-packages-Ordner eines passenden Python auf diesem PC."""
    out: list[Path] = []

    # 1. Von NemiCLI selbst gesetzt (Notausgang, falls die Suche versagt).
    manual = os.environ.get("NEMICLI_SITE_PACKAGES", "").strip()
    if manual:
        out.append(Path(manual))

    # 2. Ein venv direkt neben der exe (so kann man torch mitliefern, ohne
    #    Python systemweit zu installieren) bzw. ein gerade aktives venv.
    try:
        here = Path(sys.executable).resolve().parent
        out.append(here / "venv" / "Lib" / "site-packages")
        out.append(here / ".venv" / "Lib" / "site-packages")
    except Exception:
        pass
    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv:
        out.append(Path(venv) / "Lib" / "site-packages")

    # 3. Der Windows-Starter `py` weiß, wo welche Version liegt.
    txt = _run(["py", f"-{PYVER}", "-c",
                "import site,json;print(json.dumps(site.getsitepackages()+[site.getusersitepackages()]))"])
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("["):
            try:
                out += [Path(p) for p in json.loads(line)]
            except Exception:
                pass

    # 4. Ein `python` im PATH (nur wenn die Version passt).
    txt = _run(["python", "-c",
                "import sys,site,json;print(json.dumps([sys.version[:4]]+site.getsitepackages()))"])
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("["):
            try:
                data = json.loads(line)
                if data and str(data[0]).startswith(PYVER):
                    out += [Path(p) for p in data[1:]]
            except Exception:
                pass

    # 5. Übliche Installationsorte als letzte Rettung.
    tag = PYVER.replace(".", "")
    for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / f"Python{tag}",
                 Path(f"C:/Python{tag}"),
                 Path(os.environ.get("PROGRAMFILES", "")) / f"Python{tag}"):
        out.append(base / "Lib" / "site-packages")

    # Doppelte raus, Reihenfolge behalten
    seen, uniq = set(), []
    for p in out:
        s = str(p).lower()
        if s and s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def find_torch_dir() -> Path | None:
    """Sucht ein site-packages, in dem torch WIRKLICH liegt."""
    for p in _candidates():
        try:
            if (p / "torch" / "__init__.py").exists():
                return p
        except Exception:
            continue
    return None


def enable() -> dict:
    """Macht extern installiertes torch für die exe nutzbar.

    Rückgabe: {aktiv, grund, pfad}
      aktiv=True  -> torch ist jetzt importierbar (oder war es schon)
    """
    if _state:
        return _state

    # Schon da? (normaler Python-Betrieb, oder torch mitgepackt)
    try:
        import importlib.util
        if importlib.util.find_spec("torch") is not None:
            _state.update(aktiv=True, grund="torch ist direkt vorhanden", pfad="")
            return _state
    except Exception:
        pass

    if not FROZEN:
        _state.update(aktiv=False, grund="torch ist nicht installiert", pfad="")
        return _state

    site = find_torch_dir()
    if not site:
        _state.update(aktiv=False, pfad="", grund=(
            f"Kein Python {PYVER} mit torch gefunden. Installiere Python {PYVER} "
            f"von python.org und dann torch (Befehl steht in /systemcheck)."))
        return _state

    sys.path.append(str(site))                      # HINTEN anhängen
    # torch/lib muss auch als DLL-Ordner bekannt sein, sonst fehlen die CUDA-DLLs.
    for dll in (site / "torch" / "lib",):
        try:
            if dll.exists():
                os.add_dll_directory(str(dll))
        except Exception:
            pass
    _state.update(aktiv=True, pfad=str(site),
                  grund=f"torch aus {site} eingebunden")
    return _state


def status_text() -> str:
    """Eine Zeile für /systemcheck."""
    st = enable()
    if st["aktiv"] and st["pfad"]:
        return f"externes torch eingebunden: {st['pfad']}"
    if st["aktiv"]:
        return "torch direkt vorhanden"
    return st["grund"]


if __name__ == "__main__":          # Selbsttest: python engines/extlibs.py
    print("frozen:", FROZEN, "· Python", PYVER)
    print("Kandidaten:")
    for c in _candidates():
        print("  ", c, "torch" if (c / "torch").exists() else "")
    print("->", status_text())
