"""
uvsetup.py - Alles rund um eine ISOLIERTE Python-Umgebung via `uv`.

Idee (wie beim Hermes-Agent): Statt Python ins System zu installieren und den
PATH zu verbiegen, holt sich NemiCLI ein winziges Werkzeug namens `uv` und lässt
es ein **eigenständiges Python 3.12** in einen App-eigenen Ordner legen. Dann
baut es damit ein `venv` neben NemiCLI und installiert die schweren Bild-Pakete
(torch/diffusers) dort hinein. Nichts davon berührt das System des Nutzers:

  · kein Admin
  · keine PATH-Änderung
  · kein Systemweit-Python, kein Registry-Eintrag

Alles liegt unter  <NemiCLI>/.runtime/  (uv, das isolierte Python, der Cache)
und  <NemiCLI>/venv/  (die Umgebung, die extlibs.py zur Laufzeit einhängt).

SICHERHEIT: `uv` wird NUR von github.com geladen (feste Version, feste Adresse,
HTTPS, Host-Prüfung). `uv` selbst holt Python von der offiziellen
python-build-standalone-Quelle (ebenfalls GitHub) und Pakete von PyPI /
download.pytorch.org – dieselben Quellen wie ein normales `pip`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

try:
    from paths import ROOT, INSTALL
except Exception:
    ROOT = INSTALL = Path(__file__).resolve().parent.parent

# Feste, getestete uv-Version. Zum Aktualisieren nur diese Zeile ändern.
UV_VERSION = "0.12.13"
UV_ASSET = "uv-x86_64-pc-windows-msvc.zip"
UV_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{UV_ASSET}"
# GitHub leitet Release-Downloads auf einen Asset-Host um. Beide Schreibweisen
# sind je nach GitHub-Stand möglich – wir erlauben genau diese.
_ALLOWED_HOSTS = ("github.com", "release-assets.githubusercontent.com",
                  "objects.githubusercontent.com", "github-releases.githubusercontent.com")

# Python-Minor MUSS zur laufenden NemiCLI/exe passen (cp312-Wheels).
PYVER = f"{sys.version_info.major}.{sys.version_info.minor}"      # "3.12"

RUNTIME = INSTALL / ".runtime"              # alles Isolierte hier hinein (Programm)
UV_DIR = RUNTIME / "uv"
UV_EXE = UV_DIR / "uv.exe"
PY_INSTALL_DIR = RUNTIME / "python"         # das eigenständige Python
CACHE_DIR = RUNTIME / "cache"
BIN_DIR = RUNTIME / "bin"                   # Launcher-Shims (statt ~/.local/bin)
VENV_DIR = INSTALL / "venv"                 # gehoert zum PROGRAMM (extlibs.py sucht genau hier)

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Umgebung: alle uv-Pfade in den App-Ordner zwingen (nichts ins System)
# ---------------------------------------------------------------------------

def _env() -> dict:
    e = dict(os.environ)
    e["UV_PYTHON_INSTALL_DIR"] = str(PY_INSTALL_DIR)
    e["UV_CACHE_DIR"] = str(CACHE_DIR)
    e["UV_PYTHON_BIN_DIR"] = str(BIN_DIR)
    e["UV_NO_MODIFY_PATH"] = "1"            # niemals den PATH anfassen
    e["UV_PYTHON_PREFERENCE"] = "only-managed"   # nur unser eigenes Python nutzen
    return e


def _run(cmd: list[str], on_status=None, timeout: int = 3600) -> tuple[int, str]:
    """Befehl ausführen, Ausgabe zeilenweise an on_status, mit isolierter Umgebung."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                env=_env(), creationflags=_NOWIN)
    except FileNotFoundError as e:
        return 127, str(e)
    zeilen: list[str] = []
    try:
        for line in proc.stdout:                       # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                zeilen.append(line)
                if on_status:
                    on_status(line[:120])
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 124, "\n".join(zeilen) + "\n(Zeitüberschreitung)"
    return proc.returncode, "\n".join(zeilen)


# ---------------------------------------------------------------------------
# uv besorgen
# ---------------------------------------------------------------------------

def uv_available() -> bool:
    return UV_EXE.exists()


def ensure_uv(on_status=None) -> Path:
    """Lädt uv einmalig von GitHub in den App-Ordner. Gibt den Pfad zur uv.exe."""
    if UV_EXE.exists():
        return UV_EXE
    if urlparse(UV_URL).hostname not in _ALLOWED_HOSTS:
        raise RuntimeError("Unerwartete uv-Download-Adresse – abgebrochen.")
    import httpx
    UV_DIR.mkdir(parents=True, exist_ok=True)
    zip_pfad = UV_DIR / UV_ASSET
    if on_status:
        on_status(f"lade uv {UV_VERSION} von github.com …")
    with httpx.stream("GET", UV_URL, follow_redirects=True, timeout=120.0) as r:
        r.raise_for_status()
        if urlparse(str(r.url)).hostname not in _ALLOWED_HOSTS:
            raise RuntimeError("uv-Umleitung auf fremde Adresse – abgebrochen.")
        gesamt = int(r.headers.get("content-length", 0))
        geladen = 0
        with open(zip_pfad, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                geladen += len(chunk)
                if on_status and gesamt:
                    on_status(f"lade uv … {geladen * 100 // gesamt} %")
    if on_status:
        on_status("entpacke uv …")
    with zipfile.ZipFile(zip_pfad) as z:
        for name in z.namelist():                 # kein Zip-Slip
            ziel = (UV_DIR / name).resolve()
            if not str(ziel).startswith(str(UV_DIR.resolve())):
                raise RuntimeError("Unsicherer Pfad im uv-Archiv – abgebrochen.")
            z.extract(name, UV_DIR)
    try:
        zip_pfad.unlink()
    except Exception:
        pass
    if not UV_EXE.exists():
        raise RuntimeError("uv.exe nach dem Entpacken nicht gefunden.")
    return UV_EXE


# ---------------------------------------------------------------------------
# Isoliertes Python + venv + Pakete
# ---------------------------------------------------------------------------

def python_ready() -> bool:
    """Ist bereits ein passendes, von uv verwaltetes Python da?"""
    try:
        return any(PY_INSTALL_DIR.glob(f"cpython-{PYVER}*/python.exe"))
    except Exception:
        return False


def ensure_python(on_status=None) -> str:
    """Holt ein eigenständiges Python (nur, wenn noch keins da ist)."""
    ensure_uv(on_status)
    if python_ready():
        return "Isoliertes Python war schon da."
    if on_status:
        on_status(f"hole eigenständiges Python {PYVER} (kein Admin, kein PATH) …")
    code, aus = _run([str(UV_EXE), "python", "install", PYVER], on_status)
    if code != 0 or not python_ready():
        raise RuntimeError(f"Python konnte nicht geholt werden: {aus[-300:]}")
    return f"Isoliertes Python {PYVER} bereit."


def venv_python() -> Path | None:
    exe = VENV_DIR / "Scripts" / "python.exe"
    return exe if exe.exists() else None


def ensure_venv(on_status=None) -> str:
    """Legt das venv neben NemiCLI an (mit dem isolierten Python)."""
    if venv_python():
        return "Arbeits-Umgebung war schon da."
    ensure_python(on_status)
    if on_status:
        on_status("lege isolierte Arbeits-Umgebung an …")
    code, aus = _run([str(UV_EXE), "venv", "--python", PYVER, str(VENV_DIR)], on_status)
    if code != 0 or not venv_python():
        raise RuntimeError(f"venv konnte nicht angelegt werden: {aus[-300:]}")
    return f"Arbeits-Umgebung angelegt: {VENV_DIR}"


def pip_install(pakete: list[str], on_status=None, extra: list[str] | None = None) -> str:
    """Installiert Pakete mit uv in das venv (schnell, isoliert)."""
    if not venv_python():
        ensure_venv(on_status)
    cmd = [str(UV_EXE), "pip", "install", "--python", str(venv_python())]
    cmd += list(extra or [])
    cmd += list(pakete)
    code, aus = _run(cmd, on_status)
    if code != 0:
        raise RuntimeError(f"Installation fehlgeschlagen: {aus[-300:]}")
    return "ok"


def has_package(name: str) -> bool:
    """Läuft im venv `import <name>` durch?"""
    py = venv_python()
    if not py:
        return False
    try:
        r = subprocess.run([str(py), "-c", f"import {name}"], capture_output=True,
                           timeout=120, env=_env(), creationflags=_NOWIN)
        return r.returncode == 0
    except Exception:
        return False


def status() -> dict:
    """Kurzer Zustand für Anzeige/Debug."""
    return {
        "uv": uv_available(),
        "python": python_ready(),
        "venv": bool(venv_python()),
        "runtime_dir": str(RUNTIME),
        "venv_dir": str(VENV_DIR),
    }
