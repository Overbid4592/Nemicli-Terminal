"""Gemeinsame Laufzeit für alle TensorRT-Netze von NemiCLI.

SDK-Suche: <Programm- oder Daten-Ordner>/Models/TensorRT-RTX/ (der entpackte NVIDIA-Ordner darf
eine Ebene tiefer liegen; der Programm-Ordner geht vor). Der DLL-Ordner kommt vor dem Import in den
Suchpfad, das Python-Paket (tensorrt_rtx) liegt im venv – fehlt es, installiert `laden()` es einmal offline
aus dem SDK-Ordner.
Signaturprüfung (Authenticode, lokal): Jede DLL in bin/ muss gültig von NVIDIA signiert sein, sonst wird
nichts geladen oder installiert; vor der Installation ebenso jede .pyd/.dll im Wheel. Die Python-Dateien
des Wheels tragen keine Signatur und bleiben ungeprüft. Config `trt_signatur` = false schaltet die Prüfung ab.

Gebaute Netze liegen unter Models/TensorRT-RTX/engines/<schlüssel>.engine; der Schlüssel enthält
SDK-Version, Grafikkarte und alles, was das Netz bestimmt – ein SDK- oder Kartenwechsel baut neu.
Daneben der Laufzeit-Cache (<schlüssel>.<treiber>.cache) mit den beim ersten Lauf übersetzten Kerneln;
er gilt je Treiberstand. <schlüssel>.json merkt die Modelldatei (Aufräumen alter Stände), <schlüssel>.lock
sperrt den Bau über Prozesse hinweg.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import debugbericht as _DBG
except Exception:                                    # ohne core/ im Pfad (Tests)
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

ORDNER_NAME = "TensorRT-RTX"
_DLL = "tensorrt_rtx_1_*.dll"
SIGNIERER = "nvidia corporation"
TMP_ALTER_S = 3600                                   # ältere Zwischendateien im engines-Ordner sind verwaist
SPERRE_TAKT_S = 30.0                                 # Lebenszeichen einer Bausperre
SPERRE_ALT_S = 300.0                                 # ohne Lebenszeichen so lange: verwaiste Sperre

_lock = threading.Lock()
_zustand: dict = {}                                  # trt, sdk, grund, dll_handles
_bau_locks: dict = {}                                # Schlüssel -> threading.Lock (Bau im Prozess)
_bau_locks_lock = threading.Lock()
_aufgeraeumt: set = set()                            # engines-Ordner, deren Zwischendateien geprüft sind


class TrtFehler(RuntimeError):
    """Rechenfehler mit einem TensorRT-Netz (Speicher, Kontext, Ausführung): ohne TensorRT weiterrechnen."""


class NichtGespeichert(RuntimeError):
    """Kein lesbares gespeichertes Netz, und ohne Einmessung wird nicht gebaut."""


_sperre_gpu = None


def gpu_sperre():
    """GPU_SPERRE des GGUF-Motors (gerätweite Aufrufe aus anderen Fäden machten eine laufende
    CUDA-Graph-Aufnahme ungültig) – ohne Motor ein leerer Kontext. Import erst beim ersten Aufruf."""
    global _sperre_gpu
    if _sperre_gpu is None:
        try:
            from ggufengine.models.common import GPU_SPERRE
            _sperre_gpu = GPU_SPERRE
        except Exception:
            _sperre_gpu = contextlib.nullcontext()
    return _sperre_gpu


def _wurzeln() -> list[Path]:
    """Models/TensorRT-RTX im Programm- und im Daten-Ordner (beides, wenn verschieden)."""
    out = []
    try:
        import paths
        for basis in (paths.INSTALL, paths.DATEN):
            p = Path(basis) / "Models" / ORDNER_NAME
            if p not in out:
                out.append(p)
    except Exception:
        out.append(Path(__file__).resolve().parent.parent / "Models" / ORDNER_NAME)
    return out


def sdk_ordner() -> Path | None:
    """Der SDK-Ordner (mit bin/ und python/) – None ohne SDK. Der Programm-Ordner geht vor dem Daten-Ordner,
    darin die neueste Version (nach Namen)."""
    for w in _wurzeln():
        if not w.is_dir():
            continue
        for kandidat in [w, *sorted(w.iterdir(), reverse=True)]:
            if kandidat.is_dir() and any((kandidat / "bin").glob(_DLL)):
                return kandidat
    return None


def engine_ordner() -> Path:
    sdk = sdk_ordner()
    basis = sdk.parent if sdk is not None and sdk.parent.name == ORDNER_NAME else (sdk or _wurzeln()[0])
    return basis / "engines"


def rad(sdk: Path | None = None) -> Path | None:
    """Das Python-Paket (.whl) des SDK für diese Python-Version."""
    sdk = sdk or sdk_ordner()
    if sdk is None:
        return None
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    treffer = sorted((sdk / "python").glob(f"tensorrt_rtx-*-{tag}-*.whl"))
    return treffer[-1] if treffer else None


def _signatur_pruefen() -> bool:
    try:
        import config
        return bool(config.load().get("trt_signatur", True))
    except Exception:
        return True


def _nicht_signiert(herkunft, datei: Path, anzeige: str) -> str | None:
    """None, wenn `datei` gültig von NVIDIA signiert ist; sonst der Grund."""
    s = herkunft.signatur(str(datei))
    if s.get("gueltig") and SIGNIERER in str(s.get("signierer") or "").lower():
        return None
    return (f"{anzeige} ist nicht gültig von NVIDIA signiert ({s.get('status')}"
            f"{', ' + s['signierer'] if s.get('signierer') else ''}) – nichts geladen oder installiert")


def _herkunft():
    """(wache.herkunft, None) – oder (None, Grund), wenn keine Prüfung möglich ist."""
    try:
        from wache import herkunft
        return herkunft, None
    except Exception as exc:
        return None, f"Signaturprüfung nicht verfügbar ({exc})"


def signatur_fehler(sdk: Path) -> str | None:
    """None, wenn jede DLL in bin/ des SDK gültig von NVIDIA signiert ist; sonst der Grund."""
    if os.name != "nt" or not _signatur_pruefen():
        return None
    herkunft, fehler = _herkunft()
    if herkunft is None:
        return fehler
    if not any((sdk / "bin").glob(_DLL)):
        return "keine TensorRT-RTX-DLL im SDK-Ordner"
    for dll in sorted((sdk / "bin").glob("*.dll")):
        if (fehler := _nicht_signiert(herkunft, dll, dll.name)) is not None:
            return fehler
    return None


def rad_fehler(whl: Path) -> str | None:
    """None, wenn jede .pyd/.dll im Wheel gültig von NVIDIA signiert ist; sonst der Grund. Geprüft wird
    eine ausgepackte Kopie (Temp-Ordner), vor jeder Installation."""
    if os.name != "nt" or not _signatur_pruefen():
        return None
    herkunft, fehler = _herkunft()
    if herkunft is None:
        return fehler
    import tempfile
    import zipfile
    try:
        with zipfile.ZipFile(whl) as z, tempfile.TemporaryDirectory(prefix="trt-rad-") as tmp:
            teile = [n for n in z.namelist() if n.lower().endswith((".pyd", ".dll"))]
            if not teile:
                return f"{whl.name} enthält keine .pyd/.dll zum Prüfen – keine signierte Programmdatei, nicht installiert"
            for i, n in enumerate(teile):
                kopie = Path(tmp) / f"{i}_{Path(n).name}"
                with z.open(n) as quelle, open(kopie, "wb") as ziel:
                    shutil.copyfileobj(quelle, ziel)
                if (fehler := _nicht_signiert(herkunft, kopie, f"{whl.name}: {n}")) is not None:
                    return fehler
    except (OSError, zipfile.BadZipFile) as exc:
        return f"{whl.name} nicht prüfbar ({exc}) – nicht installiert"
    return None


def laden():
    """Das Modul tensorrt_rtx – oder None (dann sagt `grund()` warum). Einmal je Prozess."""
    with _lock:
        if "trt" in _zustand:
            return _zustand["trt"]
        _zustand["trt"], _zustand["grund"] = None, ""
        sdk = sdk_ordner()
        _zustand["sdk"] = sdk
        if sdk is None:
            _zustand["grund"] = f"kein TensorRT-RTX unter Models/{ORDNER_NAME}/"
            return None
        if (fehler := signatur_fehler(sdk)) is not None:   # vor Suchpfad, Import und Installation
            _zustand["grund"] = fehler
            _DBG.ereignis(f"TensorRT-RTX abgelehnt: {fehler}")
            return None
        bin_ = str(sdk / "bin")
        # Hinten anhängen, nicht vorne: sonst fänden alle Kindprozesse (git, python, PowerShell …) zuerst
        # gleichnamige Programme aus dem SDK-Ordner. Die DLLs selbst kommen über add_dll_directory.
        if bin_ not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + bin_
        try:
            _zustand["dll"] = os.add_dll_directory(bin_)
        except (AttributeError, OSError):
            pass
        try:
            import tensorrt_rtx as trt
        except ImportError as exc:
            meldung = _selbst_installieren(sdk)              # liegt dem SDK bei: einmal offline ins venv
            try:
                import tensorrt_rtx as trt
            except ImportError:
                _zustand["grund"] = f"Python-Paket tensorrt_rtx fehlt im venv ({exc}) – {meldung}"
                return None
        except OSError as exc:
            _zustand["grund"] = f"TensorRT-RTX lässt sich nicht laden: {exc}"
            return None
        _zustand["trt"] = trt
        _DBG.ereignis(f"TensorRT-RTX {trt.__version__} aus {sdk}")
        return trt


def grund() -> str:
    """Warum TensorRT nicht bereit ist ("" = bereit)."""
    if laden() is not None:
        return ""
    return _zustand.get("grund") or "TensorRT-RTX nicht verfügbar"


def bereit() -> bool:
    return laden() is not None


def _venv_python() -> str | None:
    """Python des eigenen venv (in der exe ist sys.executable die exe selbst)."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    try:
        import uvsetup
        p = uvsetup.venv_python()
        return str(p) if p else None
    except Exception:
        return None


def _selbst_installieren(sdk: Path) -> str:
    """Das Python-Paket aus dem SDK-Ordner ins venv (pip, ohne Netz, ohne Abhängigkeiten). Installiert wird
    eine Kopie im Temp-Ordner, und zwar genau die, deren Signaturen geprüft sind."""
    import tempfile
    whl = rad(sdk)
    if whl is None:
        return "kein passendes .whl im SDK-Ordner"
    py = _venv_python()
    if py is None:
        return "kein venv (/einrichten)"
    try:
        with tempfile.TemporaryDirectory(prefix="trt-rad-") as tmp:
            kopie = Path(tmp) / whl.name
            shutil.copyfile(whl, kopie)
            if (fehler := rad_fehler(kopie)) is not None:
                _DBG.ereignis(f"TensorRT-RTX: Paket abgelehnt: {fehler}")
                return fehler
            erg = subprocess.run([py, "-m", "pip", "install", "--no-deps", "--no-index", str(kopie)],
                                 capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Installation fehlgeschlagen: {exc}"
    if erg.returncode:
        return f"Installation fehlgeschlagen: {(erg.stderr or erg.stdout).strip()[-300:]}"
    import importlib
    importlib.invalidate_caches()
    _DBG.ereignis(f"TensorRT-RTX: {whl.name} ins venv installiert")
    return f"{whl.name} installiert"


def gpu_kennung() -> dict:
    import torch
    p = torch.cuda.get_device_properties(0)
    return {"gpu": p.name, "cc": f"{p.major}.{p.minor}", "sm": p.multi_processor_count}


def schluessel(art: str, teile: dict) -> str:
    """Name eines gebauten Netzes: Art + Hash über SDK, Grafikkarte und Bau-Angaben."""
    trt = laden()
    roh = json.dumps({"art": art, "trt": getattr(trt, "__version__", "?"), **gpu_kennung(), **teile},
                     sort_keys=True, default=str)
    return f"{art}-{hashlib.sha256(roh.encode()).hexdigest()[:20]}"


def treiber_kennung() -> str:
    """Kurzer Hash des Grafiktreiber-Stands (Windows: Größe und Zeit von nvcuda.dll) – für den Laufzeit-Cache,
    dessen übersetzte Kerne zu einem Treiber gehören."""
    if "treiber" not in _zustand:
        try:
            if os.name == "nt":
                st = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvcuda.dll").stat()
                roh = f"{st.st_size}|{st.st_mtime_ns}"
            else:
                import torch
                roh = str(torch._C._cuda_getDriverVersion())
        except Exception:
            roh = "?"
        _zustand["treiber"] = hashlib.sha256(roh.encode()).hexdigest()[:8]
    return _zustand["treiber"]


def _zwischen(datei: Path) -> Path:
    """Eindeutiger Zwischenname je Datei, Prozess und Faden (gleichzeitige Schreiber kommen sich nicht in die Quere)."""
    return datei.with_name(f"{datei.name}.{os.getpid()}.{threading.get_ident()}.tmp")


class Netz:
    """Ein gebautes Netz mit Ausführungs-Kontext und eigenem CUDA-Stream."""

    def __init__(self, engine, cache_datei: Path | None = None, budget: int | None = None):
        """budget: VRAM für streambare Gewichte in Bytes (Netze mit Weight-Streaming), None = alle.
        Netze ohne Streaming behalten ihre Gewichte im VRAM, bis sie freigegeben werden.
        Kerne für neue Formen übersetzt TensorRT bei Bedarf (LAZY, schneller als EAGER) und sichert sie im
        Laufzeit-Cache."""
        import torch
        self.engine = engine
        self.streaming = engine.streamable_weights_size > 0
        self.cache_datei = cache_datei
        self.rc = engine.create_runtime_config()
        self.cache = self.rc.create_runtime_cache()
        if cache_datei is not None and cache_datei.is_file():
            try:
                self.cache.deserialize(cache_datei.read_bytes())
            except Exception as exc:
                _DBG.ereignis(f"TensorRT: Laufzeit-Cache {cache_datei.name} verworfen ({exc}) – Kerne werden neu übersetzt")
        self.rc.set_runtime_cache(self.cache)
        # Arbeitsspeicher je Lauf passend zu den Formen (statt immer für die größten im Profil)
        trt = laden()
        self.rc.set_execution_context_allocation_strategy(trt.ExecutionContextAllocationStrategy.USER_MANAGED)
        self._puffer = None
        self.reserve: int | None = None              # gesetzt: Budget selbst bemessen, so viel VRAM bleibt frei
        self.neu_bemessen = True                     # beim nächsten Lauf auch wachsen dürfen
        self.ctx = None
        self.stream = torch.cuda.Stream()
        if self.streaming:
            self.budget_setzen(engine.streamable_weights_size if budget is None else budget)
        else:
            self.ctx = self._kontext()
        self.namen = [engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]

    def _kontext(self):
        ctx = self.engine.create_execution_context(self.rc)
        if ctx is None:
            raise TrtFehler("TensorRT: kein Ausführungs-Kontext (Grafikspeicher knapp?)")
        return ctx

    @property
    def budget(self) -> int:
        return int(self.engine.weight_streaming_budget_v2) if self.streaming else 0

    def budget_setzen(self, groesse: int) -> None:
        """So viele Bytes der streambaren Gewichte liegen im VRAM, der Rest kommt je Lauf über PCIe."""
        groesse = max(0, min(int(groesse), int(self.engine.streamable_weights_size)))
        if self.ctx is not None and groesse == self.budget:
            return
        self.stream.synchronize()
        self.ctx = None                                  # das Budget gilt für neue Kontexte
        self._puffer = None
        self.engine.weight_streaming_budget_v2 = groesse
        self.ctx = self._kontext()

    def _formen(self, formen: dict) -> int:
        for n, f in formen.items():
            self.ctx.set_input_shape(n, tuple(f))
        return int(self.ctx.update_device_memory_size_for_shapes())

    def _platz(self, groesse: int, formen: dict) -> int:
        """Erst den Arbeitsspeicher für diese Formen einplanen, dann das Gewichte-Budget: fehlt Platz,
        schrumpft das Budget (mehr Streaming); mit `neu_bemessen` darf es auch wachsen."""
        import torch
        with gpu_sperre():
            self._puffer = None
            torch.cuda.empty_cache()                 # Zwischenlager von torch zählt sonst als belegt
            frei, _ = torch.cuda.mem_get_info()
            luft = int(frei) - self.reserve - groesse
            neu = self.budget + luft if (luft < 0 or self.neu_bemessen) else self.budget
            self.neu_bemessen = False
            if self.streaming and max(0, min(neu, self.engine.streamable_weights_size)) != self.budget:
                self.budget_setzen(neu)              # neuer Kontext: Formen noch einmal setzen
                groesse = self._formen(formen)
        return groesse

    def puffer_frei(self) -> None:
        """Arbeitsspeicher nach dem Lauf zurückgeben (der nächste Lauf holt ihn neu)."""
        import torch
        with gpu_sperre():
            self.stream.synchronize()
            self._puffer = None
            torch.cuda.empty_cache()

    def __call__(self, adressen: dict, formen: dict) -> None:
        """adressen: Name -> torch.Tensor (Ein- und Ausgaben); formen: Name -> Form der Eingaben."""
        import torch
        groesse = self._formen(formen)
        if self._puffer is None or self._puffer.numel() < groesse:
            if self.reserve is not None:
                groesse = self._platz(groesse, formen)
            self._puffer = None
            # Mit Weight-Streaming merkt sich ein Kontext die Adresse seines Arbeitsspeichers: neuer
            # Speicher bekommt einen frischen Kontext (sonst „invalid argument“ beim Nachschieben).
            self.ctx = self._kontext()
            groesse = self._formen(formen)
            self._puffer = torch.empty(max(groesse, 256), dtype=torch.uint8, device="cuda")
            self.ctx.set_device_memory(self._puffer.data_ptr(), self._puffer.numel())
        self._ausfuehren(adressen)

    def _ausfuehren(self, adressen: dict) -> None:
        import torch
        for n, t in adressen.items():
            self.ctx.set_tensor_address(n, t.data_ptr())
        self.stream.wait_stream(torch.cuda.current_stream())
        if not self.ctx.execute_async_v3(self.stream.cuda_stream):
            raise TrtFehler("TensorRT: Ausführung fehlgeschlagen")
        torch.cuda.current_stream().wait_stream(self.stream)

    def cache_sichern(self) -> None:
        if self.cache_datei is None:
            return
        teil = _zwischen(self.cache_datei)
        try:
            daten = self.cache.serialize()
            if daten is not None and daten.nbytes:
                teil.write_bytes(bytes(daten))
                teil.replace(self.cache_datei)
        except Exception as exc:
            _DBG.ereignis(f"TensorRT-Laufzeit-Cache nicht gesichert: {exc}")
            try:
                teil.unlink(missing_ok=True)
            except OSError:
                pass

    def gewichte_weg(self) -> None:
        """Streambare Gewichte aus dem VRAM (Budget 0): das Netz bleibt lauffähig und streamt alles.
        Auch der Arbeitsspeicher geht zurück (der nächste Lauf holt ihn neu)."""
        with gpu_sperre():
            if self.streaming:
                self.budget_setzen(0)
            self.stream.synchronize()
            self._puffer = None
        self.neu_bemessen = True

    def an_puffer(self, puffer, formen: dict) -> None:
        """Gemeinsamen Arbeitsspeicher (NetzKette) übernehmen – mit frischem Kontext, s. __call__.
        Erst die Formen: ohne sie verlangt der Kontext Speicher für die größten Formen des Profils."""
        self.ctx = self._kontext()
        self._formen(formen)
        self._puffer = puffer
        self.ctx.set_device_memory(puffer.data_ptr(), puffer.numel())

    def starten(self, adressen: dict, formen: dict) -> None:
        """Ausführen mit schon gebundenem Arbeitsspeicher (an_puffer)."""
        self._formen(formen)
        self._ausfuehren(adressen)


ABBAU_PAUSE_S = 0.02                     # zwischen zwei Abbau-Schritten kommt die Oberfläche dran


def abbauen(netz, pause_s: float = ABBAU_PAUSE_S) -> None:
    """Ein Netz in Schritten freigeben (Kontext vor den Gewichten), mit Pause dazwischen – jeder Schritt hält
    Python nur kurz fest. Danach ist das Netz nicht mehr lauffähig. Jeder Schritt gibt Grafikspeicher frei
    und läuft deshalb unter gpu_sperre()."""
    for teil in ("ctx", "_puffer", "cache", "rc", "engine"):
        if getattr(netz, teil, None) is not None:
            with gpu_sperre():
                setattr(netz, teil, None)
            if pause_s:
                time.sleep(pause_s)


def stand_abbauen(eintraege: list, pause_s: float = ABBAU_PAUSE_S) -> None:
    """Gemerkte Netze (Einträge mit "netz": Netz oder NetzKette) Netz für Netz abbauen."""
    for st in eintraege:
        netz = st.pop("netz", None)
        st.clear()
        teile = list(getattr(netz, "netze", None) or ([netz] if netz is not None else []))
        if hasattr(netz, "netze"):
            netz.netze = []
        netz = None
        while teile:
            abbauen(teile.pop(), pause_s)


class NetzKette:
    """Mehrere Netze nacheinander (z. B. Block-Gruppen eines großen Modells), ein gemeinsamer Arbeitsspeicher.

    Das Budget-Setzen lädt die Gewichte eines Netzes neu und hält dabei Python fest; deshalb wird es
    Netz für Netz gesetzt, mit kurzer Pause dazwischen."""

    PAUSE_S = 0.003

    def __init__(self, netze: list):
        self.netze = netze
        self.reserve: int | None = None
        self.neu_bemessen = True
        self._puffer = None

    @property
    def streambar(self) -> int:
        return sum(int(n.engine.streamable_weights_size) for n in self.netze)

    @property
    def budget(self) -> int:
        return sum(n.budget for n in self.netze)

    def budget_setzen(self, gesamt: int) -> None:
        """Vorne liegen die Gewichte ganz im VRAM, hinten wird gestreamt."""
        rest = max(0, int(gesamt))
        for n in self.netze:
            anteil = min(rest, int(n.engine.streamable_weights_size))
            rest -= anteil
            if n.streaming and anteil != n.budget:
                with gpu_sperre():
                    n.budget_setzen(anteil)
                time.sleep(self.PAUSE_S)

    def _platz(self, groesse: int) -> None:
        import torch
        with gpu_sperre():                           # Budget danach Netz für Netz, je unter der Sperre
            self._puffer = None
            torch.cuda.empty_cache()
            frei, _ = torch.cuda.mem_get_info()
        luft = int(frei) - self.reserve - groesse
        neu = self.budget + luft if (luft < 0 or self.neu_bemessen) else self.budget
        self.neu_bemessen = False
        neu = max(0, min(neu, self.streambar))
        if neu != self.budget:
            self.budget_setzen(neu)

    def __call__(self, adressen_je_netz: list, formen: dict) -> None:
        import torch
        groesse = max(n._formen(formen) for n in self.netze)
        if self._puffer is None or self._puffer.numel() < groesse:
            if self.reserve is not None:
                self._platz(groesse)
                # Neues Budget = neue Kontexte: Bedarf für die Formen neu bestimmen
                groesse = max(n._formen(formen) for n in self.netze)
            self._puffer = None
            self._puffer = torch.empty(max(groesse, 256), dtype=torch.uint8, device="cuda")
        for n, adressen in zip(self.netze, adressen_je_netz):
            if n._puffer is not self._puffer:
                n.an_puffer(self._puffer, formen)
            n.starten(adressen, formen)

    def cache_sichern(self) -> None:
        for n in self.netze:
            n.cache_sichern()

    def gewichte_weg(self) -> None:
        for n in self.netze:
            n.gewichte_weg()
            time.sleep(self.PAUSE_S)
        self._puffer = None
        self.neu_bemessen = True


def _weg(datei: Path) -> bool:
    try:
        datei.unlink()
        return True
    except OSError:
        return False


def _zwischen_aufraeumen(ordner: Path) -> None:
    """Einmal je Prozess: verwaiste Zwischendateien (*.tmp älter als TMP_ALTER_S) im engines-Ordner löschen."""
    if ordner in _aufgeraeumt or not ordner.is_dir():
        return
    _aufgeraeumt.add(ordner)
    grenze = time.time() - TMP_ALTER_S
    for teil in ordner.glob("*.tmp"):
        try:
            if teil.is_file() and teil.stat().st_mtime < grenze and _weg(teil):
                _DBG.ereignis(f"TensorRT: verwaiste Zwischendatei {teil.name} entfernt")
        except OSError:
            pass


@contextlib.contextmanager
def _bausperre(ordner: Path, name: str):
    """Nur ein Bau je Schlüssel: im Prozess über ein Lock je Schlüssel, zwischen Prozessen über
    <name>.lock (O_EXCL). Der Halter erneuert die Datei alle SPERRE_TAKT_S; eine ältere als SPERRE_ALT_S
    gilt als verwaist (abgestürzter Prozess)."""
    with _bau_locks_lock:
        lk = _bau_locks.setdefault(name, threading.Lock())
    with lk:
        ordner.mkdir(parents=True, exist_ok=True)
        datei = ordner / f"{name}.lock"
        while True:
            try:
                fd = os.open(str(datei), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    alter = time.time() - datei.stat().st_mtime
                except FileNotFoundError:
                    continue
                if alter > SPERRE_ALT_S:
                    if not _weg(datei) and datei.exists():
                        raise RuntimeError(f"TensorRT: verwaiste Bausperre {datei.name} nicht löschbar")
                    _DBG.ereignis(f"TensorRT: verwaiste Bausperre {datei.name} entfernt")
                    continue
                time.sleep(1.0)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        halt = threading.Event()

        def puls():
            while not halt.wait(SPERRE_TAKT_S):
                try:
                    os.utime(datei)
                except OSError:
                    pass
        threading.Thread(target=puls, daemon=True, name="trt-bausperre").start()
        try:
            yield
        finally:
            halt.set()
            _weg(datei)


def _lesen(runtime, datei: Path):
    """(engine, plan) aus der Datei – oder (None, None). Eine unlesbare Datei wird gelöscht: ihr Ersatz
    entsteht nur mit Einmessung."""
    if not datei.is_file():
        return None, None
    blob = datei.read_bytes()
    with gpu_sperre():
        engine = runtime.deserialize_cuda_engine(blob)
    if engine is None:
        _DBG.ereignis(f"TensorRT: {datei.name} unlesbar – gelöscht" + ("" if _weg(datei) else " (geht nicht)"))
        return None, None
    return engine, blob


def _bauen(trt, log, runtime, bauen, datei: Path, fortschritt=None):
    """Bauen, speichern, laden -> (engine, plan). Der Bau selbst läuft ohne gpu_sperre(): er dauert Minuten
    und hielte so lange jede CUDA-Graph-Aufnahme (Stimme, Chat) an."""
    if fortschritt:
        fortschritt("baue TensorRT-Netz …")
    builder = trt.Builder(log)
    netz, cfg = bauen(trt, builder)
    ser = builder.build_serialized_network(netz, cfg)
    if ser is None:
        raise RuntimeError("TensorRT: Bau fehlgeschlagen")
    blob = bytes(ser)
    del ser
    datei.parent.mkdir(parents=True, exist_ok=True)
    teil = _zwischen(datei)
    try:
        teil.write_bytes(blob)
        teil.replace(datei)
    finally:
        _weg(teil)                                       # nur nach einem Fehler noch da
    with gpu_sperre():
        engine = runtime.deserialize_cuda_engine(blob)
    if engine is None:
        raise RuntimeError("TensorRT: frisch gebautes Netz nicht ladbar")
    _DBG.ereignis(f"TensorRT: {datei.name} gebaut ({len(blob) / 2**20:.0f} MiB)")
    return engine, blob


def _quelle_merken(ordner: Path, name: str, art: str, quelle: str) -> None:
    info = ordner / f"{name}.json"
    if info.is_file():
        return
    teil = _zwischen(info)
    try:
        teil.write_text(json.dumps({"art": art, "quelle": quelle}, ensure_ascii=False), encoding="utf-8")
        teil.replace(info)
    except OSError:
        _weg(teil)


def aufraeumen(art: str, quelle: str | None, behalten) -> int:
    """Netze derselben Art und Modelldatei (laut <name>.json) löschen, die nicht in `behalten` stehen – ältere
    Stände der Datei, andere Formate, frühere Fassungen. Gibt die Zahl der gelöschten Netze zurück."""
    if not quelle:
        return 0
    ordner = engine_ordner()
    behalten = set(behalten)
    n = 0
    for info in ordner.glob(f"{art}-*.json"):
        name = info.stem
        if name in behalten:
            continue
        try:
            d = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("art") != art or d.get("quelle") != quelle:
            continue
        if (ordner / f"{name}.lock").exists():               # wird gerade (von einem anderen Prozess) gebaut
            continue
        for datei in (ordner / f"{name}.engine", *ordner.glob(f"{name}.*cache")):
            _weg(datei)
        _weg(info)
        n += 1
        _DBG.ereignis(f"TensorRT: altes Netz {name} entfernt (gleiche Modelldatei)")
    return n


def _caches_aufraeumen(ordner: Path, name: str, cache: Path) -> None:
    """Laufzeit-Caches dieses Netzes für andere Treiberstände (und ohne Treiberkennung) löschen."""
    for alt in ordner.glob(f"{name}.*cache"):
        if alt != cache:
            _weg(alt)


def holen(art: str, teile: dict, bauen, fortschritt=None, budget: int | None = None,
          quelle: str | None = None) -> Netz:
    """Gebautes Netz aus dem Ordner – oder `bauen(trt, builder) -> (netz, config)` und speichern.
    bauen=None: nur laden (ohne Einmessung wird nicht gebaut) – fehlt das Netz: NichtGespeichert.
    budget: VRAM für die Gewichte bei Netzen mit Weight-Streaming (None = alle).
    quelle: Modelldatei, für `aufraeumen` in <name>.json gemerkt."""
    trt = laden()
    if trt is None:
        raise RuntimeError(grund())
    name = schluessel(art, teile)
    ordner = engine_ordner()
    _zwischen_aufraeumen(ordner)
    datei = ordner / f"{name}.engine"
    log = trt.Logger(trt.Logger.ERROR)
    runtime = trt.Runtime(log)
    engine, blob = _lesen(runtime, datei)
    if engine is None:
        if bauen is None:
            raise NichtGespeichert(f"TensorRT: kein gespeichertes Netz {name} – baut erst nach dem Einmessen")
        with _bausperre(ordner, name):
            engine, blob = _lesen(runtime, datei)        # inzwischen von einem anderen Faden oder Prozess gebaut
            if engine is None:
                engine, blob = _bauen(trt, log, runtime, bauen, datei, fortschritt)
    if quelle:
        _quelle_merken(ordner, name, art, quelle)
    cache = ordner / f"{name}.{treiber_kennung()}.cache"
    _caches_aufraeumen(ordner, name, cache)
    with gpu_sperre():
        netz = Netz(engine, cache, budget)
    # Beim Weight-Streaming liest TensorRT die Gewichte direkt aus dem Plan im RAM: Runtime und Plan
    # müssen so lange leben wie das Netz (sonst Müll-Gewichte oder „invalid argument“).
    netz.runtime, netz.plan, netz.name = runtime, blob, name
    return netz
