"""
llamareserve.py - llama.cpp als festgenagelte Reserve neben dem eigenen GGUF-Motor.

Genau ein Build ist erlaubt (BUILD). Die offiziellen Windows-Pakete kommen in die Ablage: von Hand
oder über `holen()` – nur von GitHub (HOSTS), Größe und SHA-256 aus PAKETE/GROESSE, fortsetzbar; welches
Paket, entscheidet die Grafikkarte (`passende_pakete`). `einrichten()` prüft jedes Paket erneut, entpackt
es (kein Pfad aus dem Archiv heraus) und hält die Hashes aller entpackten Dateien in der Prüfliste fest.
Vor jedem Start werden die Dateien gegen diese Liste geprüft.

Der Server startet nur mit festen Schaltern (SCHALTER): 127.0.0.1, freier Port, zufälliger
Schlüssel, ohne Web-Oberfläche, ohne Jinja-Vorlage, ohne Werkzeuge, ohne Slot-Endpunkt, ohne
mmproj, offline. RPC, Slot-Speicher und Schlafmodus bleiben aus (Standard; mehrere CVEs dort).
Die Prompts kommen als fertige Token-Folgen aus ggufengine.chat (llamamotor.py). Hauptprogramm und Wache
teilen sich einen Server je Modell (Zettel im Cache, Schlüssel mit DPAPI verschlüsselt).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

BUILD = "b12199"
RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/tag/{BUILD}"
# Offizielle Windows-x64-Pakete dieses Builds mit der SHA-256 aus dem Release.
PAKETE = {
    f"llama-{BUILD}-bin-win-cpu-x64.zip": "1cde1df073c2526ef3ad34a5b2884998802b837ed4b965a36d0af2920f10a1ae",
    f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip": "3c80120dc7763d67d0e882dda1545fbf626b2db471270030731cca4913cfc228",
    f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip": "1cb9b561b0d62b3ecc48ecbff2003fa656bdbbf71bead919ae92110df7f961a5",
    f"llama-{BUILD}-bin-win-vulkan-x64.zip": "7bb426133c32a424b2b74a14d6dae6aaf7ff56a742f1a93af0bc34dd6f224ce9",
    f"llama-{BUILD}-bin-win-rocm-10.0-x64.zip": "89a1498d553981c9075d8668086cf22ae748c980e2391d2181bca5a1788b2b77",
    f"llama-{BUILD}-bin-win-sycl-x64.zip": "c3fac08ec4cb7fda1a9b6e4a60791df3cede47feb64de26db11165f89f9976a4",
    "cudart-llama-bin-win-cuda-12.4-x64.zip": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    "cudart-llama-bin-win-cuda-13.5-x64.zip": "c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba",
}
GROESSE = {
    f"llama-{BUILD}-bin-win-cpu-x64.zip": 24758741,
    f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip": 288734574,
    f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip": 170781058,
    f"llama-{BUILD}-bin-win-vulkan-x64.zip": 37715448,
    f"llama-{BUILD}-bin-win-rocm-10.0-x64.zip": 267336186,
    f"llama-{BUILD}-bin-win-sycl-x64.zip": 156077103,
    "cudart-llama-bin-win-cuda-12.4-x64.zip": 391443627,
    "cudart-llama-bin-win-cuda-13.5-x64.zip": 426314199,
}
DOWNLOAD_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILD}/{{name}}"
# GitHub leitet Release-Dateien auf seinen Speicher um; andere Ziele werden nicht angefragt.
HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_UMLEITUNGEN = 5
# Paket-Arten für holen(); "auto" wählt nach der Grafikkarte
ARTEN = {"cuda-13.5": f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip",
         "cuda-12.4": f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip",
         "vulkan": f"llama-{BUILD}-bin-win-vulkan-x64.zip",
         "cpu": f"llama-{BUILD}-bin-win-cpu-x64.zip"}
# CUDA-Paket -> passende Laufzeit (ohne sie startet der CUDA-Teil nicht)
LAUFZEIT = {f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip": "cudart-llama-bin-win-cuda-12.4-x64.zip",
            f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip": "cudart-llama-bin-win-cuda-13.5-x64.zip"}
SERVER = "llama-server.exe"
MAX_ENTPACKT = 8 << 30                 # alle Dateien zusammen
MAX_DATEIEN = 2000
START_WARTEN_S = 600                   # Laden großer Modelle von der Platte
STOPP_WARTEN_S = 10


def _daten() -> Path:
    try:
        from paths import DATEN
        return Path(DATEN)
    except Exception:
        return Path(os.environ.get("TEMP") or ".") / "nemicli_daten"


def ablage() -> Path:
    """Hier legt man die heruntergeladenen Pakete ab."""
    return _daten() / "Motoren" / "llama.cpp"


def ziel() -> Path:
    return ablage() / BUILD


def _pruefliste() -> Path:
    return ziel() / "pruefliste.json"


class ReserveFrage(RuntimeError):
    """Der eigene Motor kann das Modell nicht laden, llama.cpp ist eingerichtet: erst fragen."""

    def __init__(self, name: str, grund: str):
        self.name, self.grund = name, grund
        super().__init__(f"Der eigene Motor kann {name} nicht laden: {grund}")

    @property
    def frage(self) -> str:
        return (f"Der eigene Motor kann {self.name} nicht laden ({self.grund}).\n"
                f"Soll die Reserve llama.cpp {BUILD} das Modell übernehmen?")


# ---------------------------------------------------------------------------
# Prüfen und Einrichten
# ---------------------------------------------------------------------------
def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(4 << 20), b""):
            h.update(stueck)
    return h.hexdigest()


def pakete_in_ablage() -> list[Path]:
    ordner = ablage()
    if not ordner.is_dir():
        return []
    return sorted(p for p in ordner.iterdir() if p.is_file() and p.suffix.lower() == ".zip")


def passende_pakete(profil=None, art: str = "auto") -> tuple[list[str], str]:
    """Pakete für diesen Rechner und warum. NVIDIA mit Treiber für CUDA 13.5: CUDA 13.5; ältere Treiber:
    CUDA 12.4, bei Blackwell (Rechen-Stufe 12) Vulkan, weil der 12.4-Build sie nicht kennt; andere
    Grafikkarten: Vulkan; ohne Grafikkarte: CPU."""
    if art != "auto":
        if art not in ARTEN:
            raise ValueError(f"Unbekannte Paket-Art {art!r} (möglich: auto, {', '.join(ARTEN)})")
        haupt = ARTEN[art]
        return [n for n in (LAUFZEIT.get(haupt), haupt) if n], f"ausdrücklich gewählt: {art}"
    if profil is None:
        try:
            import systemprofil
            profil = systemprofil.erkennen()
        except Exception:
            profil = None
    nv = getattr(profil, "nvidia", None) if profil is not None else None
    if nv is not None:
        cuda = float(getattr(profil, "cuda_max", 0.0) or 0.0)
        try:
            stufe = float(nv.cuda_cap or 0)
        except ValueError:
            stufe = 0.0
        if cuda >= 13.5:
            art, grund = "cuda-13.5", f"{nv.name}, Treiber kann CUDA {cuda:g}"
        elif stufe >= 12 or cuda < 12.4:
            art, grund = "vulkan", f"{nv.name}, Treiber kann nur CUDA {cuda:g} – Vulkan läuft trotzdem auf der Karte"
        else:
            art, grund = "cuda-12.4", f"{nv.name}, Treiber kann CUDA {cuda:g}"
    elif profil is not None and getattr(profil, "haupt_gpu", None) is not None:
        art, grund = "vulkan", f"{profil.haupt_gpu.name} (keine NVIDIA)"
    else:
        art, grund = "cpu", "keine Grafikkarte erkannt"
    haupt = ARTEN[art]
    return [n for n in (LAUFZEIT.get(haupt), haupt) if n], grund


class Abbruch(Exception):
    pass


def _host_erlaubt(url) -> bool:
    return url.scheme == "https" and url.host in HOSTS


def _laden(name: str, fortschritt=None, stopp: threading.Event | None = None) -> str:
    """Ein Paket in die Ablage laden (.part wird fortgesetzt), Größe und SHA-256 prüfen, erst dann ablegen."""
    import httpx
    soll, groesse = PAKETE[name], GROESSE[name]
    ziel_datei = ablage() / name
    if ziel_datei.is_file() and ziel_datei.stat().st_size == groesse and sha256(ziel_datei) == soll:
        return f"✅ {name}: schon da, SHA-256 stimmt"
    teil = ablage() / (name + ".part")
    start = teil.stat().st_size if teil.is_file() else 0
    if start >= groesse:
        teil.unlink()
        start = 0
    url = httpx.URL(DOWNLOAD_URL.format(name=name))
    kopf = {"User-Agent": "NemiCLI-llamacpp-Reserve"}
    with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False) as c:
        for _ in range(MAX_UMLEITUNGEN + 1):
            if not _host_erlaubt(url):
                raise OSError(f"{name}: Umleitung zu {url.host} – nur GitHub ist erlaubt, abgebrochen")
            bereich = {"Range": f"bytes={start}-"} if start else {}
            with c.stream("GET", url, headers=kopf | bereich) as r:
                if r.is_redirect:
                    url = url.join(r.headers.get("location", ""))
                    continue
                if r.status_code == 200:
                    start = 0                        # setzt nicht fort: von vorn
                elif r.status_code != 206:
                    raise OSError(f"{name}: GitHub antwortet mit {r.status_code}")
                with open(teil, "ab" if start else "wb") as f:
                    erledigt = start
                    for stueck in r.iter_bytes(1 << 20):
                        if stopp is not None and stopp.is_set():
                            raise Abbruch()
                        erledigt += len(stueck)
                        if erledigt > groesse:
                            raise OSError(f"{name}: größer als angegeben – verworfen")
                        f.write(stueck)
                        if fortschritt is not None:
                            fortschritt(name, erledigt, groesse)
            break
        else:
            raise OSError(f"{name}: zu viele Umleitungen")
    if teil.stat().st_size != groesse or sha256(teil) != soll:
        teil.unlink(missing_ok=True)
        raise OSError(f"{name}: Größe oder SHA-256 stimmen nicht – die Datei wurde verworfen")
    teil.replace(ziel_datei)
    return f"✅ {name}: geladen, SHA-256 stimmt"


def holen(art: str = "auto", fortschritt=None, stopp: threading.Event | None = None,
          profil=None) -> tuple[bool, list[str]]:
    """Passende Pakete von GitHub laden und einrichten. (eingerichtet, Zeilen für die Anzeige)."""
    try:
        namen, grund = passende_pakete(profil, art)
    except ValueError as exc:
        return False, [f"❌ {exc}"]
    zeilen = [f"🦙 llama.cpp {BUILD}: {', '.join(namen)} ({grund})"]
    ablage().mkdir(parents=True, exist_ok=True)
    noetig = sum(GROESSE[n] for n in namen) * 3      # Paket, Prüfkopie beim Entpacken, Entpacktes
    frei = shutil.disk_usage(ablage()).free
    if frei < noetig:
        zeilen.append(f"❌ Zu wenig Platz: frei {frei / 2**30:.1f} GB, nötig etwa {noetig / 2**30:.1f} GB")
        return False, zeilen
    try:
        for n in namen:
            zeilen.append(_laden(n, fortschritt, stopp))
    except Abbruch:
        zeilen.append("⏹ Abgebrochen – der angefangene Teil bleibt liegen und wird beim nächsten Mal fortgesetzt.")
        return False, zeilen
    except Exception as exc:
        zeilen.append(f"❌ {exc}")
        return False, zeilen
    ok, mehr = einrichten()
    return ok, zeilen + mehr


def _sicherer_name(name: str) -> Path | None:
    """Relativer Pfad eines Archiv-Eintrags, oder None, wenn er aus dem Ziel herausführt."""
    teile = name.replace("\\", "/").split("/")
    if not name or name.startswith("/") or ":" in name or any(t in ("..",) for t in teile):
        return None
    teile = [t for t in teile if t not in ("", ".")]
    return Path(*teile) if teile else None


def _entpacken(paket: Path, nach: Path, bisher: int) -> int:
    """Entpackt ein geprüftes Paket nach `nach`; gibt die Bytes zurück. Wirft bei unsicheren Einträgen."""
    summe = bisher
    with zipfile.ZipFile(paket) as z:
        eintraege = [e for e in z.infolist() if not e.is_dir()]
        if len(eintraege) > MAX_DATEIEN:
            raise ValueError(f"{paket.name}: zu viele Dateien ({len(eintraege)})")
        for e in eintraege:
            rel = _sicherer_name(e.filename)
            if rel is None:
                raise ValueError(f"{paket.name}: unsicherer Pfad im Archiv: {e.filename!r}")
            summe += e.file_size
            if summe > MAX_ENTPACKT:
                raise ValueError(f"{paket.name}: entpackt größer als {MAX_ENTPACKT >> 30} GB")
            aus = nach / rel
            aus.parent.mkdir(parents=True, exist_ok=True)
            with z.open(e) as quelle, open(aus, "wb") as senke:
                kopiert = 0
                for stueck in iter(lambda: quelle.read(1 << 20), b""):
                    kopiert += len(stueck)
                    if kopiert > e.file_size:
                        raise ValueError(f"{paket.name}: {e.filename} größer als angegeben")
                    senke.write(stueck)
    return summe


def _server_in(ordner: Path) -> Path | None:
    treffer = sorted(ordner.rglob(SERVER), key=lambda p: len(p.parts))
    return treffer[0] if treffer else None


def einrichten() -> tuple[bool, list[str]]:
    """Prüft die Pakete in der Ablage und entpackt die passenden. (eingerichtet, Zeilen für die Anzeige)."""
    zeilen: list[str] = []
    gut: list[Path] = []
    for p in pakete_in_ablage():
        soll = PAKETE.get(p.name)
        if soll is None:
            zeilen.append(f"⏭ {p.name}: gehört nicht zu {BUILD} – übersprungen")
            continue
        ist = sha256(p)
        if ist != soll:
            zeilen.append(f"❌ {p.name}: SHA-256 stimmt nicht ({ist[:12]}… statt {soll[:12]}…) – nicht entpackt")
            continue
        zeilen.append(f"✅ {p.name}: SHA-256 stimmt")
        gut.append(p)
    haupt = [p for p in gut if p.name.startswith("llama-")]
    if not haupt:
        zeilen.append(f"Kein passendes llama.cpp-Paket in {ablage()}.")
        return False, zeilen
    namen = {p.name for p in gut}
    for p in haupt:
        if (rt := LAUFZEIT.get(p.name)) and rt not in namen:
            zeilen.append(f"⚠ Zu {p.name} fehlt {rt} – ohne sie läuft nur die CPU.")
    neu = ablage() / f"{BUILD}.neu"
    shutil.rmtree(neu, ignore_errors=True)
    try:
        neu.mkdir(parents=True)
        summe = 0
        for p in sorted(gut, key=lambda x: x.name.startswith("llama-")):   # Laufzeit zuerst, Paket überschreibt
            summe = _entpacken(p, neu, summe)
        server = _server_in(neu)
        if server is None:
            raise ValueError(f"{SERVER} fehlt in den Paketen")
        dateien = {str(f.relative_to(neu)).replace("\\", "/"): sha256(f) for f in sorted(neu.rglob("*"))
                   if f.is_file()}
        liste = {"build": BUILD, "pakete": sorted(namen), "server": str(server.relative_to(neu)).replace("\\", "/"),
                 "dateien": dateien}
        (neu / "pruefliste.json").write_text(json.dumps(liste, indent=1), encoding="utf-8")
        _beenden_alle()
        alt = ziel()
        weg = ablage() / f"{BUILD}.alt"
        shutil.rmtree(weg, ignore_errors=True)
        if alt.exists():
            alt.replace(weg)                         # gesperrte Datei: scheitert ganz, nichts halb gelöscht
        try:
            neu.replace(alt)
        except OSError:
            if weg.exists() and not alt.exists():
                weg.replace(alt)                     # alten Stand zurück
            raise
        shutil.rmtree(weg, ignore_errors=True)
    except Exception as exc:
        shutil.rmtree(neu, ignore_errors=True)
        zeilen.append(f"❌ Einrichten abgebrochen: {exc}")
        return False, zeilen
    _GEPRUEFT.clear()
    zeilen.append(f"✅ llama.cpp {BUILD} eingerichtet ({len(dateien)} Dateien, {summe / 2**20:.0f} MB).")
    return True, zeilen


_GEPRUEFT: dict[str, tuple[int, int]] = {}      # Datei -> (Größe, Zeitstempel) beim letzten geprüften Hash
_PRUEF_LOCK = threading.Lock()


def _liste() -> dict | None:
    try:
        daten = json.loads(_pruefliste().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(daten, dict) or daten.get("build") != BUILD or not isinstance(daten.get("dateien"), dict):
        return None
    return daten


def pruefen() -> str | None:
    """None, wenn llama.cpp startbereit ist und jede Datei zur Prüfliste passt; sonst der Grund.
    Unveränderte Dateien (gleiche Größe und Zeit) werden nicht erneut gehasht."""
    daten = _liste()
    if daten is None:
        return f"llama.cpp {BUILD} ist nicht eingerichtet"
    basis = ziel()
    server = daten.get("server")
    if server not in daten["dateien"]:
        return f"{SERVER} fehlt in der Prüfliste"
    with _PRUEF_LOCK:
        vorhanden = {str(f.relative_to(basis)).replace("\\", "/") for f in basis.rglob("*")
                     if f.is_file() and f.name != "pruefliste.json"}
        fremd = vorhanden - set(daten["dateien"])
        if fremd:
            return f"fremde Datei im llama.cpp-Ordner: {sorted(fremd)[0]}"
        for rel, soll in daten["dateien"].items():
            pfad = basis / rel
            try:
                st = pfad.stat()
            except OSError:
                return f"Datei fehlt: {rel}"
            stempel = (st.st_size, st.st_mtime_ns)
            if _GEPRUEFT.get(rel) == stempel:
                continue
            if sha256(pfad) != soll:
                _GEPRUEFT.pop(rel, None)
                return f"Datei verändert: {rel}"
            _GEPRUEFT[rel] = stempel
    return None


def eingerichtet() -> bool:
    return _liste() is not None


def bereit() -> bool:
    return pruefen() is None


def server_pfad() -> Path:
    daten = _liste() or {}
    return ziel() / str(daten.get("server") or SERVER)


def status_zeilen() -> list[str]:
    grund = pruefen()
    zeilen = [f"🦙 llama.cpp-Reserve · festgenagelt auf {BUILD}"]
    if grund is None:
        daten = _liste() or {}
        zeilen.append(f"✅ bereit · Pakete: {', '.join(daten.get('pakete', []))}")
    else:
        zeilen.append(f"⏸ {grund}")
        zeilen += ["So richtest du es ein:",
                   "  /llamacpp holen – NemiCLI wählt das Paket für deine Grafikkarte, lädt es von GitHub, "
                   "prüft jede SHA-256 und richtet es ein",
                   f"  oder von Hand: Pakete von {RELEASE_URL} nach {ablage()} legen, dann /llamacpp einrichten"]
    zeilen.append("Schalter: nur 127.0.0.1, zufälliger Schlüssel, ohne Web-Oberfläche, Jinja, Werkzeuge, RPC, "
                  "Slot-Speicher und Schlafmodus.")
    return zeilen


# ---------------------------------------------------------------------------
# Server-Prozess
# ---------------------------------------------------------------------------
# Feste Schalter; nur Modell, Kontext und Port kommen dazu. Der Schlüssel geht über die Umgebung,
# nicht über die Kommandozeile.
# Zwei Plätze mit gemeinsamem Gesprächsspeicher: Hauptprogramm und Wache verdrängen ihre Prompts nicht
# gegenseitig, der Kontext gilt für jeden Platz ganz (kein zusätzlicher Grafikspeicher).
SCHALTER = ("--host", "127.0.0.1", "--no-webui", "--no-jinja", "--no-slots", "--offline",
            "--parallel", "2", "--kv-unified", "--fit", "on")
OHNE_BILDTEIL = ("--no-mmproj",)


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Job:
    """Windows-Job: endet NemiCLI, endet der Server mit (auch nach einem Absturz)."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes as w
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
            k32.CreateJobObjectW.restype = w.HANDLE
            k32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
            k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            k32.CloseHandle.argtypes = [w.HANDLE]

            class _Basis(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", w.DWORD),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", w.DWORD),
                            ("f", ctypes.c_size_t), ("g", w.DWORD), ("h", w.DWORD)]

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in "abcdef"]

            class _Erweitert(ctypes.Structure):
                _fields_ = [("Basis", _Basis), ("Io", _Io), ("p", ctypes.c_size_t), ("j", ctypes.c_size_t),
                            ("pp", ctypes.c_size_t), ("jp", ctypes.c_size_t)]

            self._k32 = k32
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = _Erweitert()
            info.Basis.LimitFlags = 0x2000                  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(h)
                return
            self.handle = h
        except Exception:
            self.handle = None

    def aufnehmen(self, proc: subprocess.Popen) -> None:
        if self.handle is not None:
            try:
                self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))
            except Exception:
                pass


_JOB: _Job | None = None
_LAUFEND: set = set()                  # gestartete Server (Einrichten beendet sie vorher)


def _job() -> _Job:
    global _JOB
    if _JOB is None:
        _JOB = _Job()
    return _JOB


def _beenden_alle() -> None:
    for s in list(_LAUFEND):
        s.stoppen()


LOG_BEHALTEN = 6                       # so viele Server-Protokolle bleiben liegen
GAST_FRIST_S = 900                     # wer den Server so lange nicht benutzt hat, hält ihn nicht mehr
PAUSE_MAX_S = 1800                     # länger hält eine Mal-Pause andere Prozesse nicht vom Neustart ab
MELDEN_ALLE_S = 60
ZETTEL_MAX = 64 << 10


def _ordner() -> Path:
    return _daten() / "Cache" / "llamacpp"


def log_datei(pid: int | None = None) -> Path:
    """Ein Protokoll je NemiCLI-Prozess (Hauptprogramm und Wache schreiben sonst durcheinander)."""
    return _ordner() / f"server_{pid or os.getpid()}.log"


def _alte_logs_weg() -> None:
    try:
        logs = sorted(_ordner().glob("server_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in logs[LOG_BEHALTEN:]:
            p.unlink(missing_ok=True)
        (_ordner() / "server.log").unlink(missing_ok=True)       # gemeinsames Protokoll früherer Fassungen
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Ein Server je Modell für alle NemiCLI-Prozesse (Hauptprogramm, Wache)
# ---------------------------------------------------------------------------
# Wer zuerst startet, legt einen Zettel ab: Server-PID, Port, Schlüssel (mit DPAPI für diesen Windows-Nutzer
# verschlüsselt). Andere Prozesse hängen sich an, statt ein zweites Mal Grafikspeicher zu belegen. Jeder trägt
# sich bei Benutzung als Gast ein; wer aufhört, beendet den Server nur, wenn sonst niemand ihn in den letzten
# GAST_FRIST_S genutzt hat. Fürs Bildermalen wird er immer beendet; der Zettel sagt dann „Pause“, und die
# anderen warten, statt neu zu starten.
def _lebt(pid) -> bool:
    try:
        import psutil
        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def _ist_unser_server(pid) -> bool:
    """Läuft unter dieser PID genau die geprüfte llama-server.exe?"""
    try:
        import psutil
        exe = Path(psutil.Process(int(pid)).exe())
        return os.path.normcase(str(exe.resolve())) == os.path.normcase(str(server_pfad().resolve()))
    except Exception:
        return False


def _prozess_beenden(pid) -> None:
    if not _ist_unser_server(pid):
        return
    try:
        import psutil
        p = psutil.Process(int(pid))
        p.terminate()
        try:
            p.wait(timeout=STOPP_WARTEN_S)
        except psutil.TimeoutExpired:
            p.kill()
            p.wait(timeout=STOPP_WARTEN_S)
    except Exception:
        pass


def _verschluesseln(text: str) -> str | None:
    try:
        import keyvault
        return keyvault.encrypt(text) if keyvault.available() else None
    except Exception:
        return None


def _entschluesseln(text) -> str | None:
    try:
        import keyvault
        if not isinstance(text, str) or not keyvault.is_encrypted(text):
            return None
        return keyvault.decrypt(text)
    except Exception:
        return None


def _kennung(modell: Path) -> list:
    try:
        st = modell.stat()
        return [os.path.normcase(str(modell.resolve())), st.st_size, st.st_mtime_ns]
    except OSError:
        return []


def _zettel_pfad(modell: Path) -> Path:
    h = hashlib.sha256(os.path.normcase(str(Path(modell).resolve())).encode("utf-8")).hexdigest()[:16]
    return _ordner() / f"geteilt_{h}.json"


class _Sperre:
    """Sperre über Prozesse hinweg (msvcrt.locking): endet ein Prozess, ist sie von selbst frei."""

    def __init__(self, pfad: Path, warten_s: float):
        self.pfad, self.warten_s, self.f = pfad, warten_s, None

    def __enter__(self):
        try:
            import msvcrt
        except ImportError:
            return self
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.pfad, "a+b")
        ende = time.monotonic() + self.warten_s
        while True:
            try:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.monotonic() > ende:
                    self.f.close()
                    self.f = None
                    raise RuntimeError("llama.cpp: ein anderer NemiCLI-Prozess startet den Server gerade zu lange")
                time.sleep(0.2)

    def __exit__(self, *_):
        if self.f is not None:
            try:
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.f.close()


def _zettel_lesen(modell: Path) -> dict | None:
    pfad = _zettel_pfad(modell)
    try:
        if pfad.stat().st_size > ZETTEL_MAX:
            return None
        z = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(z, dict) or z.get("build") != BUILD or z.get("kennung") != _kennung(modell):
        return None
    if not isinstance(z.get("gaeste"), dict):
        z["gaeste"] = {}
    return z


def _zettel_schreiben(modell: Path, z: dict) -> None:
    pfad = _zettel_pfad(modell)
    neu = pfad.with_suffix(f".{os.getpid()}.tmp")
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        neu.write_text(json.dumps(z), encoding="utf-8")
        neu.replace(pfad)
    except OSError:
        neu.unlink(missing_ok=True)


class Server:
    """llama-server für genau eine Modelldatei – eigener Prozess oder der eines anderen NemiCLI-Prozesses."""

    def __init__(self, modell: Path, n_ctx: int = 0, bildteil: Path | None = None):
        self.modell = Path(modell)
        self.bildteil = Path(bildteil) if bildteil else None
        self.n_ctx_wunsch = max(0, int(n_ctx or 0))
        self.proc: subprocess.Popen | None = None
        self.server_pid = 0
        self.ich = os.getpid()
        self.gast = False
        self.port = 0
        self.schluessel = ""
        self.n_ctx = 0
        self.ladezeit = 0.0
        self._gemeldet = 0.0

    @property
    def basis(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def kopf(self) -> dict:
        return {"Authorization": f"Bearer {self.schluessel}"}

    def laeuft(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return bool(self.server_pid) and _lebt(self.server_pid) and _ist_unser_server(self.server_pid)

    def argumente(self) -> list[str]:
        bild = ["--mmproj", str(self.bildteil)] if self.bildteil else list(OHNE_BILDTEIL)
        return [str(server_pfad()), "-m", str(self.modell), "--port", str(self.port),
                "-c", str(self.n_ctx_wunsch), *SCHALTER, *bild]

    def _sperre(self, art: str, warten_s: float) -> _Sperre:
        return _Sperre(_zettel_pfad(self.modell).with_suffix(f".{art}.lock"), warten_s)

    def _props(self) -> int | None:
        """n_ctx des Servers, wenn er mit diesem Schlüssel antwortet."""
        import httpx
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as c:
                r = c.get(self.basis + "/props", headers=self.kopf)
            if r.status_code != 200:
                return None
            return int(((r.json() or {}).get("default_generation_settings") or {}).get("n_ctx") or 0)
        except Exception:
            return None

    def _anhaengen(self) -> bool:
        """Den Server eines anderen NemiCLI-Prozesses für dieselbe Modelldatei mitbenutzen."""
        z = _zettel_lesen(self.modell)
        if not z or z.get("pause") or z.get("bildteil") != (str(self.bildteil) if self.bildteil else None):
            return False
        pid = z.get("server_pid")
        if not isinstance(pid, int) or not (_lebt(pid) and _ist_unser_server(pid)):
            return False
        schluessel = _entschluesseln(z.get("schluessel"))
        port = z.get("port")
        if not schluessel or not isinstance(port, int):
            return False
        alt = (self.port, self.schluessel)
        self.port, self.schluessel = port, schluessel
        n_ctx = self._props()
        if n_ctx is None:
            self.port, self.schluessel = alt
            return False
        self.proc, self.server_pid, self.gast, self.n_ctx = None, pid, True, n_ctx
        self.benutzt(sofort=True)
        return True

    def benutzt(self, sofort: bool = False) -> None:
        """Im Zettel festhalten, dass dieser Prozess den geteilten Server gerade nutzt."""
        if not self.server_pid or (not sofort and time.monotonic() - self._gemeldet < MELDEN_ALLE_S):
            return
        self._gemeldet = time.monotonic()
        with self._sperre("zettel", 10.0):
            z = _zettel_lesen(self.modell)
            if z and z.get("server_pid") == self.server_pid:
                z["gaeste"][str(self.ich)] = time.time()
                _zettel_schreiben(self.modell, z)

    def _pause_abwarten(self, abbruch=None) -> None:
        """Malt ein anderer NemiCLI-Prozess gerade (Server dafür beendet), wird nicht neu gestartet."""
        t0 = time.monotonic()
        while True:
            z = _zettel_lesen(self.modell)
            if not z or not z.get("pause"):
                return
            von = z.get("pause_von")
            if von == self.ich or not _lebt(von) or time.time() - float(z.get("pause_seit") or 0) > PAUSE_MAX_S:
                return
            if abbruch is not None and abbruch():
                raise RuntimeError("Start abgebrochen")
            if time.monotonic() - t0 > PAUSE_MAX_S:
                return
            time.sleep(1.0)

    def starten(self, abbruch=None) -> float:
        """Startet den Server (oder hängt sich an einen laufenden an) und wartet, bis das Modell geladen ist.
        Gibt die Ladezeit zurück (0 beim Anhängen)."""
        if self.laeuft():
            return 0.0
        if (grund := pruefen()) is not None:
            raise RuntimeError(f"llama.cpp-Reserve nicht startbereit: {grund}")
        self._pause_abwarten(abbruch)
        with self._sperre("start", START_WARTEN_S):
            if self._anhaengen():
                self.ladezeit = 0.0
                return 0.0
            return self._selbst_starten(abbruch)

    def _selbst_starten(self, abbruch=None) -> float:
        import httpx
        self.port = _freier_port()
        self.schluessel = secrets.token_urlsafe(32)
        self.gast = False
        umgebung = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
        umgebung["LLAMA_API_KEY"] = self.schluessel
        log = log_datei(self.ich)
        log.parent.mkdir(parents=True, exist_ok=True)
        _alte_logs_weg()
        t0 = time.monotonic()
        with open(log, "ab") as ausgabe:
            self.proc = subprocess.Popen(
                self.argumente(), cwd=str(server_pfad().parent), env=umgebung, stdin=subprocess.DEVNULL,
                stdout=ausgabe, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.server_pid = self.proc.pid
        _job().aufnehmen(self.proc)
        _LAUFEND.add(self)
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as c:
                while True:
                    if not self.laeuft():
                        raise RuntimeError(f"llama-server hat sich beendet: {self.log_ende()}")
                    if abbruch is not None and abbruch():
                        raise RuntimeError("Start abgebrochen")
                    if time.monotonic() - t0 > START_WARTEN_S:
                        raise RuntimeError(f"llama-server antwortet nach {START_WARTEN_S} s nicht")
                    try:
                        if c.get(self.basis + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
            self.n_ctx = self._props() or 0
        except BaseException:
            self.stoppen()
            raise
        if (verschluesselt := _verschluesseln(self.schluessel)) is not None:
            with self._sperre("zettel", 10.0):
                _zettel_schreiben(self.modell, {
                    "build": BUILD, "kennung": _kennung(self.modell), "server_pid": self.server_pid,
                    "besitzer": self.ich, "port": self.port, "schluessel": verschluesselt, "n_ctx": self.n_ctx,
                    "bildteil": str(self.bildteil) if self.bildteil else None,
                    "gaeste": {str(self.ich): time.time()}, "pause": False})
        self._gemeldet = time.monotonic()
        self.ladezeit = time.monotonic() - t0
        return self.ladezeit

    def log_ende(self, zeichen: int = 600) -> str:
        try:
            with open(log_datei(self.ich), "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - zeichen))
                return f.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return "(kein Protokoll)"

    def stoppen(self, fuer_bild: bool = False) -> None:
        """Nicht mehr gebraucht: Nutzt ein anderer NemiCLI-Prozess den Server noch, läuft er für ihn weiter.
        fuer_bild: immer beenden (das Bildmodell braucht die Grafikkarte); die anderen warten auf das Ende."""
        proc, self.proc = self.proc, None
        pid, self.server_pid = self.server_pid, 0
        _LAUFEND.discard(self)
        with self._sperre("zettel", 10.0):
            z = _zettel_lesen(self.modell)
            geteilt = bool(pid) and z is not None and z.get("server_pid") == pid
            if geteilt and not fuer_bild:
                z["gaeste"].pop(str(self.ich), None)
                jetzt = time.time()
                andere = [int(p) for p, ts in z["gaeste"].items()
                          if str(p).isdigit() and jetzt - float(ts or 0) < GAST_FRIST_S and _lebt(int(p))]
                if andere and (proc is None or proc.poll() is None):
                    if z.get("besitzer") == self.ich:
                        z["besitzer"] = andere[0]
                    _zettel_schreiben(self.modell, z)
                    return                                   # läuft für die anderen weiter
            if proc is not None:
                _proc_beenden(proc)
            elif pid:
                _prozess_beenden(pid)
            if fuer_bild:                                    # auch ohne laufenden Server: niemand startet ihn jetzt
                z = z if geteilt else {"build": BUILD, "kennung": _kennung(self.modell), "besitzer": self.ich,
                                       "n_ctx": 0}
                z.update(server_pid=0, port=0, schluessel="", gaeste={}, pause=True, pause_von=self.ich,
                         pause_seit=time.time())
                _zettel_schreiben(self.modell, z)
            elif geteilt:
                _zettel_pfad(self.modell).unlink(missing_ok=True)

    def pause_ende(self) -> None:
        """Das Bild ist fertig: die eigene Pause aufheben (der nächste Start legt einen neuen Zettel an)."""
        with self._sperre("zettel", 10.0):
            z = _zettel_lesen(self.modell)
            if z and z.get("pause") and z.get("pause_von") == self.ich:
                _zettel_pfad(self.modell).unlink(missing_ok=True)


def _proc_beenden(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=STOPP_WARTEN_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=STOPP_WARTEN_S)
        except subprocess.TimeoutExpired:
            pass
