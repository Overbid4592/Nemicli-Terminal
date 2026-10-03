"""
extlibs.py - Die Brücke von der exe zu NemiCLIs eigenem venv.

Die gepackte NemiCLI.exe bringt torch, numpy, Pillow, transformers & Co. nicht
mit (das wären mehrere GB). Sie kommen ausschließlich aus dem venv, das
/einrichten neben der exe anlegt (`<NemiCLI>/venv`, siehe uvsetup.VENV_DIR).
Dessen site-packages wird HINTEN an den Suchpfad gehängt – was die exe selbst
mitbringt, gewinnt weiterhin.

Ein Python-Hauptordner des Systems (py-Starter, python im PATH, Programme\
Python3x) wird NIE benutzt, auch wenn dort torch installiert ist. Fehlt das
eigene venv, fehlt torch – dann ist /einrichten der Weg.

Im normalen Betrieb (python main.py) macht dieses Modul gar nichts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
PYVER = f"{sys.version_info.major}.{sys.version_info.minor}"     # z.B. "3.12"

_state: dict = {}          # Ergebnis merken


def eigenes_venv() -> Path:
    """site-packages des venv, das /einrichten neben NemiCLI anlegt."""
    try:
        from paths import INSTALL
    except Exception:
        INSTALL = Path(sys.executable).resolve().parent
    return INSTALL / "venv" / "Lib" / "site-packages"


def _candidates() -> list[Path]:
    """Einziger Kandidat: das eigene venv."""
    return [eigenes_venv()]


def find_torch_dir() -> Path | None:
    """Das eigene venv, wenn dort torch WIRKLICH liegt."""
    p = eigenes_venv()
    try:
        return p if (p / "torch" / "__init__.py").exists() else None
    except Exception:
        return None


def enable() -> dict:
    """Bindet das eigene venv in die exe ein.

    Rückgabe: {aktiv, grund, pfad}
      aktiv=True  -> torch ist jetzt importierbar (oder war es schon)
    """
    if _state:
        return _state

    if not FROZEN:
        try:
            import importlib.util
            da = importlib.util.find_spec("torch") is not None
        except Exception:
            da = False
        _state.update(aktiv=da, pfad="",
                      grund="torch ist direkt vorhanden" if da else "torch ist nicht installiert")
        return _state

    site = eigenes_venv()
    if not site.is_dir():
        _state.update(aktiv=False, pfad="", grund=(
            f"Kein eigenes venv neben NemiCLI ({site.parent.parent}). "
            "/einrichten legt es an und installiert torch & Co. dort hinein."))
        return _state

    if str(site) not in sys.path:
        sys.path.append(str(site))                  # HINTEN anhängen
    # torch/lib muss auch als DLL-Ordner bekannt sein, sonst fehlen die CUDA-DLLs.
    try:
        if (site / "torch" / "lib").exists():
            os.add_dll_directory(str(site / "torch" / "lib"))
    except Exception:
        pass
    if find_torch_dir() is None:
        _state.update(aktiv=False, pfad=str(site), grund=(
            "Im eigenen venv fehlt torch – /einrichten installiert es."))
        return _state
    _state.update(aktiv=True, pfad=str(site), grund=f"torch aus dem eigenen venv ({site})")
    return _state


def status_text() -> str:
    """Eine Zeile für /systemcheck."""
    st = enable()
    if st["aktiv"] and st["pfad"]:
        return f"eigenes venv eingebunden: {st['pfad']}"
    if st["aktiv"]:
        return "torch direkt vorhanden"
    return st["grund"]


if __name__ == "__main__":          # Selbsttest: python engines/extlibs.py
    print("frozen:", FROZEN, "· Python", PYVER)
    print("eigenes venv:", eigenes_venv(), "torch" if find_torch_dir() else "(ohne torch)")
    print("->", status_text())
