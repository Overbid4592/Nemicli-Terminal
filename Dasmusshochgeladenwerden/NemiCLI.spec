# -*- mode: python ; coding: utf-8 -*-
"""
NemiCLI.spec - Bauplan für die NemiCLI.exe (PyInstaller).

Bauen:   python build_exe.py        (empfohlen, prüft alles vorher)
oder:    pyinstaller NemiCLI.spec --noconfirm

Was NICHT mit reinkommt: torch, diffusers, transformers, numpy, opencv, Pillow.
Die gehören zum Bilder-Malen und wären zusammen ~6 GB. NemiCLI holt sie sich
zur Laufzeit aus einem normal installierten Python (siehe engines/extlibs.py);
welcher pip-Befehl dafür der richtige ist, sagt /systemcheck.
"""

from pathlib import Path

ROOT = Path(SPECPATH)

# Die Module liegen in Kategorie-Ordnern, werden aber flach importiert
# (import ui, import models ...) - deshalb kommen die Ordner in den Suchpfad.
SUBDIRS = ["core", "engines", "tools", "ui"]
PATHEX = [str(ROOT)] + [str(ROOT / d) for d in SUBDIRS]

# Alles, was flach importiert wird - explizit nennen, damit auch die
# Imports mitkommen, die erst mitten im Code passieren (import foldersense ...).
HIDDEN = []
for d in SUBDIRS:
    for f in sorted((ROOT / d).glob("*.py")):
        if not f.name.startswith("_"):
            HIDDEN.append(f.stem)
HIDDEN += [
    "anthropic", "openai", "httpx", "httpcore", "h11", "certifi",
    "dotenv", "rich", "prompt_toolkit",
    "textual",                     # Vollbild-Oberfläche (ui/screen_tx.py)
    "tkinter",                     # Fenster für /bearbeiten
    "encodings.oem", "encodings.cp850", "encodings.cp1252",   # Konsolen-Ausgabe
]

# Die KOMPLETTE Standard-Bibliothek mitnehmen (kostet nur ein paar MB).
# Grund: torch & diffusers werden später von außen dazugeladen (extlibs.py)
# und benutzen Standard-Module, die NemiCLI selbst nie anfasst - fehlt eines,
# stirbt der Import mit "No module named 'timeit'" o.ä.
import sys as _sys
_NUR_UNIX = {"nis", "ossaudiodev", "spwd", "crypt", "termios", "tty", "pty",
             "fcntl", "grp", "pwd", "posix", "syslog", "readline", "resource",
             "curses", "_curses", "grp", "asyncio.unix_events"}
_UNNOETIG = {"antigravity", "this", "idlelib", "turtle", "turtledemo",
             "test", "lib2to3", "pydoc_data", "ensurepip", "venv"}
_STDLIB = [m for m in _sys.stdlib_module_names
           if not m.startswith("_") and m not in _NUR_UNIX and m not in _UNNOETIG]
HIDDEN += _STDLIB

# Bei Paketen (unittest, email, ctypes ...) reicht der Name nicht - die
# Unter-Module (unittest.mock!) müssen einzeln benannt werden.
from PyInstaller.utils.hooks import collect_submodules
HIDDEN += collect_submodules("textual")          # Widgets werden dynamisch geladen
for _pkg in _STDLIB:
    try:
        HIDDEN += collect_submodules(_pkg)
    except Exception:
        pass
HIDDEN = sorted(set(HIDDEN))

# Mitgelieferte Dateien (Daten, kein Code).
DATAS = [(str(ROOT / "allowlist.json"), ".")]
for extra in ("README.md", ".env.example"):
    if (ROOT / extra).exists():
        DATAS.append((str(ROOT / extra), "."))

# Bewusst draußen: die schweren Bild-Pakete (siehe Kopf) und Bau-Werkzeug.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "diffusers", "transformers",
    "numpy", "scipy", "cv2", "PIL", "safetensors", "accelerate",
    "matplotlib", "pandas", "IPython", "pytest", "setuptools", "pip",
]

a = Analysis(
    ["main.py"],
    pathex=PATHEX,
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NemiCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,                  # NemiCLI IST ein Terminal-Programm
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "nemicli.ico") if (ROOT / "nemicli.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NemiCLI",
)
