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

import atexit
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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
    e["UV_NO_PROGRESS"] = "1"               # Fortschrittsbalken ohne Terminal: nur Rauschen
    e["UV_HTTP_TIMEOUT"] = "120"            # große Wheels über langsame Leitungen
    return e


# Laufende uv-Prozesse: werden beim Beenden von NemiCLI mitbeendet, damit kein
# verwaister uv die venv-Sperre hält und die nächste Installation blockiert.
_LAUFEND: set = set()


@atexit.register
def _aufraeumen() -> None:
    for proc in list(_LAUFEND):
        try:
            proc.kill()
        except Exception:
            pass


class _IoZaehler:
    """Ein-/Ausgabe-Bytes eines Prozesses (GetProcessIoCounters). Netzverkehr
    zählt Windows unter „Other“ – daran ist der Download ablesbar, auch wenn
    uv selbst minutenlang nichts ausgibt."""

    def __init__(self, pid: int):
        self._h = None
        try:
            import ctypes

            class _IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in
                            ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
            self._ct, self._IO = ctypes, _IO
            self._k = ctypes.windll.kernel32
            self._h = self._k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        except Exception:
            self._h = None

    def lesen(self) -> tuple[float, float]:
        """(Netz-MB, alle MB) – (0, 0), wenn nicht messbar."""
        if not self._h:
            return 0.0, 0.0
        io = self._IO()
        if not self._k.GetProcessIoCounters(self._h, self._ct.byref(io)):
            return 0.0, 0.0
        mb = 1024 * 1024
        return io.OtherBytes / mb, (io.ReadBytes + io.WriteBytes + io.OtherBytes) / mb

    def schliessen(self) -> None:
        if self._h:
            self._k.CloseHandle(self._h)
            self._h = None


_LADE = re.compile(r"^\s*Downloading (\S+) \(([\d.]+)\s*([KMG])iB\)")
_GELADEN = re.compile(r"^\s*Downloaded (\S+)")
_FAKTOR = {"K": 1 / 1024, "M": 1.0, "G": 1024.0}


def _zeit(sek: float) -> str:
    return f"{int(sek) // 60}:{int(sek) % 60:02d} min"


def fortschritt(zeilen: list[str], geladen_mb: float, rate: float, dauer: float,
                namen: dict | None = None) -> str:
    """Statuszeile aus der uv-Ausgabe und dem gemessenen Netzverkehr.

    Solange Pakete laden: „<Name> · 450/1946 MB · 4.1 MB/s · 1:28 min“ – das
    „i/n“ zeichnet in der Oberfläche den Balken. Danach: Installieren."""
    gesamt: dict[str, float] = {}
    fertig: set[str] = set()
    for z in zeilen:
        m = _LADE.match(z)
        if m:
            gesamt[m.group(1)] = float(m.group(2)) * _FAKTOR[m.group(3)]
            continue
        m = _GELADEN.match(z)
        if m:
            fertig.add(m.group(1))
    namen = namen or {}
    offen = [n for n in gesamt if n not in fertig]
    if offen:
        summe = sum(gesamt.values())
        erledigt = sum(gesamt[n] for n in fertig if n in gesamt)
        stand = min(max(geladen_mb, erledigt), summe * 0.99)
        groesstes = max(offen, key=lambda n: gesamt[n])
        text = (f"{namen.get(groesstes, groesstes)} · {stand:.0f}/{summe:.0f} MB · "
                f"{max(rate, 0):.1f} MB/s")
        if rate > 0.05:
            text += f" · noch ~{_zeit((summe - stand) / rate)}"
        return text
    if gesamt:
        return f"geladen, installiere ins venv … · {_zeit(dauer)}"
    letzte = zeilen[-1].strip()[:70] if zeilen else "arbeite"
    return f"{letzte} · {_zeit(dauer)}"


def _run(cmd: list[str], on_status=None, timeout: int = 4 * 3600,
         stillstand: int = 600, namen: dict | None = None) -> tuple[int, str]:
    """Befehl ausführen, Fortschritt an on_status, mit isolierter Umgebung.

    uv schreibt beim Laden großer Wheels (torch ~2 GB) minutenlang nichts;
    den Fortschritt liefert dann der Netzverkehr des Prozesses. `namen` gibt
    Paketen einen Anzeigenamen (torch → „PyTorch CUDA 13.2“). Abgebrochen
    wird erst, wenn der Prozess weder ausgibt noch Ein-/Ausgabe hat
    (stillstand), oder nach timeout insgesamt."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace",
                                env=_env(), creationflags=_NOWIN)
    except FileNotFoundError as e:
        return 127, str(e)
    _LAUFEND.add(proc)
    zeilen: list[str] = []

    def lesen() -> None:
        for line in proc.stdout:                       # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                zeilen.append(line)

    leser = threading.Thread(target=lesen, daemon=True)
    leser.start()
    io = _IoZaehler(proc.pid)
    start = letzte_aktivitaet = time.monotonic()
    netz_start, alles_alt = io.lesen()
    netz_alt, anzahl_alt, zeit_alt = netz_start, 0, start
    try:
        while True:
            try:
                proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                pass
            jetzt = time.monotonic()
            netz, alles = io.lesen()
            if alles != alles_alt or len(zeilen) != anzahl_alt:
                letzte_aktivitaet = jetzt
            alles_alt, anzahl_alt = alles, len(zeilen)
            if jetzt - start > timeout or jetzt - letzte_aktivitaet > stillstand:
                proc.kill()
                proc.wait()
                grund = ("Zeitüberschreitung" if jetzt - start > timeout
                         else f"keine Aktivität seit {stillstand // 60} min")
                return 124, "\n".join(zeilen) + f"\n({grund})"
            if on_status and jetzt - zeit_alt >= 2:
                rate = (netz - netz_alt) / (jetzt - zeit_alt)
                netz_alt, zeit_alt = netz, jetzt
                on_status(fortschritt(list(zeilen), netz - netz_start, rate, jetzt - start, namen))
    finally:
        _LAUFEND.discard(proc)
        io.schliessen()
        leser.join(timeout=5)
        try:
            proc.stdout.close()                        # type: ignore[union-attr]
        except Exception:
            pass
    return proc.returncode, "\n".join(zeilen)


class _InstallSperre:
    """Nur eine Installation ins venv zur selben Zeit – auch über mehrere
    NemiCLI-Fenster hinweg. Zwei uv-Läufe auf dasselbe venv warten sonst
    gegenseitig auf ihre Sperren."""

    def __init__(self) -> None:
        self._f = None

    def __enter__(self):
        import msvcrt
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self._f = open(RUNTIME / "install.lock", "a+")
        self._f.seek(0)
        try:
            msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._f.close()
            self._f = None
            raise RuntimeError("Eine Installation läuft bereits (anderes NemiCLI-Fenster). "
                               "Erst diese abwarten oder das Fenster schließen.")
        return self

    def __exit__(self, *a) -> None:
        if self._f:
            import msvcrt
            try:
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._f.close()
            self._f = None


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


def pip_install(pakete: list[str], on_status=None, extra: list[str] | None = None,
                namen: dict | None = None) -> str:
    """Installiert Pakete mit uv in das venv (schnell, isoliert). `namen`:
    Anzeigenamen für die Fortschrittszeile."""
    if not venv_python():
        ensure_venv(on_status)
    cmd = [str(UV_EXE), "pip", "install", "--python", str(venv_python())]
    cmd += list(extra or [])
    cmd += list(pakete)
    code, aus = _run(cmd, on_status, namen=namen)
    if code != 0:
        raise RuntimeError(f"Installation fehlgeschlagen: {aus[-300:]}")
    return "ok"


def install_sperre() -> _InstallSperre:
    return _InstallSperre()


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
