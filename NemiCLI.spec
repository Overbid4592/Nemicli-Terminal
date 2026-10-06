# -*- mode: python ; coding: utf-8 -*-
"""
NemiCLI.spec - Bauplan für die NemiCLI.exe (PyInstaller).

Bauen:   python build_exe.py        (empfohlen, prüft alles vorher)
oder:    pyinstaller NemiCLI.spec --noconfirm

Was NICHT mit reinkommt: torch, numpy, safetensors, opencv, Pillow, ruff.
Die brauchen der GGUF-Motor, Krea 2 und das Gedächtnis, zusammen mehrere GB.
NemiCLI holt sie sich zur Laufzeit aus dem venv (siehe engines/extlibs.py);
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
# Eigener GGUF-Motor (Paket im Programm-Ordner); torch kommt zur Laufzeit aus dem venv.
HIDDEN += ["ggufengine"] + ["ggufengine." + ".".join(f.relative_to(ROOT / "ggufengine").with_suffix("").parts)
                             for f in sorted((ROOT / "ggufengine").rglob("*.py")) if f.stem != "__init__"]
HIDDEN += ["ggufengine.models"]
HIDDEN += [
    "anthropic", "openai", "httpx", "httpcore", "h11", "certifi",
    "dotenv", "rich", "prompt_toolkit",
    "textual",                     # Vollbild-Oberfläche (ui/screen_tx.py)
    "pyte",                        # eigenes Terminal-Fenster (ui/terminal_fenster.py)
    "tkinter",                     # Ordner-Auswahl (core/reich.py, /start)
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",   # Wache: Kugel, Chat, Tray (oberflaeche.py)
    "encodings.oem", "encodings.cp850", "encodings.cp1252",   # Konsolen-Ausgabe
]

# Die KOMPLETTE Standard-Bibliothek mitnehmen (kostet nur ein paar MB).
# Grund: torch & Co. werden später von außen dazugeladen (extlibs.py)
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
# Pakete, die die exe mitbringt UND torch & Co. aus dem venv benutzen: Die exe-Fassung
# wird zuerst gefunden – fehlt ihr ein Untermodul (tqdm.contrib), bricht der Import im venv ab.
# Deshalb vollständig mitnehmen.
for _geteilt in ("tqdm", "packaging", "requests", "jinja2", "yaml"):
    HIDDEN += collect_submodules(_geteilt)
for _pkg in _STDLIB:
    try:
        HIDDEN += collect_submodules(_pkg)
    except Exception:
        pass
HIDDEN = sorted(set(HIDDEN))

# Mitgelieferte Dateien (Daten, kein Code).
DATAS = [(str(ROOT / "allowlist.json"), ".")]
for extra in ("README.md", ".env.example", "requirements.txt", "nemicli.ico"):
    if (ROOT / extra).exists():
        DATAS.append((str(ROOT / extra), "."))

# Build-Nummer in die exe: dort gibt es kein Git, das die Commits zählt.
_sys.path.insert(0, str(ROOT / "core"))
import version as _version
_BUILD_TXT = ROOT / "build" / "build.txt"
_BUILD_TXT.parent.mkdir(parents=True, exist_ok=True)
_BUILD_TXT.write_text(_version.build_nummer() + "\n", encoding="utf-8")
DATAS.append((str(_BUILD_TXT), "."))

# Bewusst draußen: die schweren Bild-Pakete (siehe Kopf) und Bau-Werkzeug.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "diffusers", "transformers",
    "numpy", "scipy", "cv2", "PIL", "safetensors", "accelerate",
    "matplotlib", "pandas", "IPython", "pytest", "setuptools", "pip",
    "ruff",                        # Programm im venv; extlibs bindet es zur Laufzeit ein
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

# exe + Ordner NemiCLIT2 statt einer einzigen Datei: Eine exe, die sich bei
# jedem Start in den Temp-Ordner entpackt und von dort Code ausführt, ist ein
# Schädlingsmuster – Bitdefender (Advanced Threat Defense) hat sie deshalb
# samt der zuletzt geschriebenen Quelldateien in Quarantäne genommen.
# Python, Bibliotheken und Code liegen in NemiCLIT2 (sys._MEIPASS), Daten nie.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    contents_directory="NemiCLIT2",
    name="NemiCLIc",
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

# NemiCLI.exe ist die Fenster-App (ohne Konsole): Doppelklick öffnet das eigene
# Terminal-Fenster; Wache, Vollscan und Aufträge laufen darüber ohne Terminal.
# NemiCLIc.exe (oben, mit Konsole) läuft darin bzw. für --selftest/--version.
exe_w = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    contents_directory="NemiCLIT2",
    name="NemiCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "nemicli.ico") if (ROOT / "nemicli.ico").exists() else None,
)

coll = COLLECT(
    exe,
    exe_w,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NemiCLI",
)
