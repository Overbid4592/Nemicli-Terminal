"""Gemeinsame Laufzeit für alle TensorRT-Netze von NemiCLI.

SDK-Suche: <Programm- oder Daten-Ordner>/Models/TensorRT-RTX/ (der entpackte NVIDIA-Ordner darf
eine Ebene tiefer liegen). Die DLL-Ordner kommen vor dem Import in den Suchpfad, das Python-Paket
(tensorrt_rtx) liegt im venv – fehlt es, installiert `laden()` es einmal offline aus dem SDK-Ordner.

Gebaute Netze liegen unter Models/TensorRT-RTX/engines/<schlüssel>.engine; der Schlüssel enthält
SDK-Version, Grafikkarte und alles, was das Netz bestimmt – ein Treiber- oder SDK-Wechsel baut neu.
Daneben der Laufzeit-Cache (.cache) mit den beim ersten Lauf übersetzten Kerneln.
"""

from __future__ import annotations

import hashlib
import json
import os
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

_lock = threading.Lock()
_zustand: dict = {}                                  # trt, sdk, grund, dll_handles


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
    """Der SDK-Ordner (mit bin/ und python/), neueste Version zuerst – None ohne SDK."""
    gefunden = []
    for w in _wurzeln():
        if not w.is_dir():
            continue
        for kandidat in [w, *sorted(w.iterdir(), reverse=True)]:
            if kandidat.is_dir() and any((kandidat / "bin").glob(_DLL)):
                gefunden.append(kandidat)
    return gefunden[0] if gefunden else None


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
    """Das Python-Paket aus dem SDK-Ordner ins venv (pip, ohne Netz, ohne Abhängigkeiten)."""
    whl = rad(sdk)
    if whl is None:
        return "kein passendes .whl im SDK-Ordner"
    py = _venv_python()
    if py is None:
        return "kein venv (/einrichten)"
    try:
        erg = subprocess.run([py, "-m", "pip", "install", "--no-deps", "--no-index", str(whl)],
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


class Netz:
    """Ein gebautes Netz mit Ausführungs-Kontext und eigenem CUDA-Stream."""

    def __init__(self, engine, cache_datei: Path | None = None, budget: int | None = None):
        """budget: VRAM für streambare Gewichte in Bytes (Netze mit Weight-Streaming), None = alle.
        Netze ohne Streaming behalten ihre Gewichte im VRAM, bis sie freigegeben werden.
        Kerne für neue Formen zieht TensorRT im Hintergrund nach (LAZY) und sichert sie im Laufzeit-Cache;
        gleich übersetzte (EAGER) waren in der Messung langsamer."""
        import torch
        self.engine = engine
        self.streaming = engine.streamable_weights_size > 0
        self.cache_datei = cache_datei
        self.rc = engine.create_runtime_config()
        self.cache = self.rc.create_runtime_cache()
        if cache_datei is not None and cache_datei.is_file():
            try:
                self.cache.deserialize(cache_datei.read_bytes())
            except Exception:
                pass
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
            self.ctx = engine.create_execution_context(self.rc)
        self.namen = [engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]

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
        self.ctx = self.engine.create_execution_context(self.rc)

    def _formen(self, formen: dict) -> int:
        for n, f in formen.items():
            self.ctx.set_input_shape(n, tuple(f))
        return int(self.ctx.update_device_memory_size_for_shapes())

    def _platz(self, groesse: int, formen: dict) -> int:
        """Erst den Arbeitsspeicher für diese Formen einplanen, dann das Gewichte-Budget: fehlt Platz,
        schrumpft das Budget (mehr Streaming); mit `neu_bemessen` darf es auch wachsen."""
        import torch
        self._puffer = None
        torch.cuda.empty_cache()                     # Zwischenlager von torch zählt sonst als belegt
        frei, _ = torch.cuda.mem_get_info()
        luft = int(frei) - self.reserve - groesse
        neu = self.budget + luft if (luft < 0 or self.neu_bemessen) else self.budget
        self.neu_bemessen = False
        if self.streaming and max(0, min(neu, self.engine.streamable_weights_size)) != self.budget:
            self.budget_setzen(neu)                  # neuer Kontext: Formen noch einmal setzen
            groesse = self._formen(formen)
        return groesse

    def puffer_frei(self) -> None:
        """Arbeitsspeicher nach dem Lauf zurückgeben (der nächste Lauf holt ihn neu)."""
        import torch
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
            self.ctx = self.engine.create_execution_context(self.rc)
            groesse = self._formen(formen)
            self._puffer = torch.empty(max(groesse, 256), dtype=torch.uint8, device="cuda")
            self.ctx.set_device_memory(self._puffer.data_ptr(), self._puffer.numel())
        for n, t in adressen.items():
            self.ctx.set_tensor_address(n, t.data_ptr())
        self.stream.wait_stream(torch.cuda.current_stream())
        if not self.ctx.execute_async_v3(self.stream.cuda_stream):
            raise RuntimeError("TensorRT: Ausführung fehlgeschlagen")
        torch.cuda.current_stream().wait_stream(self.stream)

    def cache_sichern(self) -> None:
        if self.cache_datei is None:
            return
        try:
            daten = self.cache.serialize()
            if daten is not None and daten.nbytes:
                teil = self.cache_datei.with_suffix(".tmp")
                teil.write_bytes(bytes(daten))
                teil.replace(self.cache_datei)
        except Exception as exc:
            _DBG.ereignis(f"TensorRT-Laufzeit-Cache nicht gesichert: {exc}")

    def gewichte_weg(self) -> None:
        """Streambare Gewichte aus dem VRAM (Budget 0): das Netz bleibt lauffähig und streamt alles.
        Auch der Arbeitsspeicher geht zurück (der nächste Lauf holt ihn neu)."""
        if self.streaming:
            self.budget_setzen(0)
        self.stream.synchronize()
        self._puffer = None
        self.neu_bemessen = True

    def gewichte_her(self, budget: int | None = None) -> None:
        if self.streaming:
            self.budget_setzen(self.engine.streamable_weights_size if budget is None else budget)

    def an_puffer(self, puffer, formen: dict) -> None:
        """Gemeinsamen Arbeitsspeicher (NetzKette) übernehmen – mit frischem Kontext, s. __call__.
        Erst die Formen: ohne sie verlangt der Kontext Speicher für die größten Formen des Profils."""
        self.ctx = self.engine.create_execution_context(self.rc)
        self._formen(formen)
        self._puffer = puffer
        self.ctx.set_device_memory(puffer.data_ptr(), puffer.numel())

    def starten(self, adressen: dict, formen: dict) -> None:
        """Ausführen mit schon gebundenem Arbeitsspeicher (an_puffer)."""
        import torch
        self._formen(formen)
        for n, t in adressen.items():
            self.ctx.set_tensor_address(n, t.data_ptr())
        self.stream.wait_stream(torch.cuda.current_stream())
        if not self.ctx.execute_async_v3(self.stream.cuda_stream):
            raise RuntimeError("TensorRT: Ausführung fehlgeschlagen")
        torch.cuda.current_stream().wait_stream(self.stream)


ABBAU_PAUSE_S = 0.02                     # zwischen zwei Abbau-Schritten kommt die Oberfläche dran


def abbauen(netz, pause_s: float = ABBAU_PAUSE_S) -> None:
    """Ein Netz in Schritten freigeben (Kontext vor den Gewichten), mit Pause dazwischen – jeder Schritt hält
    Python nur kurz fest. Danach ist das Netz nicht mehr lauffähig."""
    for teil in ("ctx", "_puffer", "cache", "rc", "engine"):
        if getattr(netz, teil, None) is not None:
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

    Beim Setzen des Gewichte-Budgets lädt TensorRT alle gehaltenen Gewichte des Netzes neu und hält dabei
    Python fest (gemessen etwa 0,24 s je GB) – ein großes Netz ließ die Oberfläche bis zu 2 s stehen.
    Hier wird das Budget Netz für Netz gesetzt, mit kurzer Pause dazwischen."""

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
                n.budget_setzen(anteil)
                time.sleep(self.PAUSE_S)

    def _platz(self, groesse: int) -> None:
        import torch
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


def holen(art: str, teile: dict, bauen, fortschritt=None, budget: int | None = None) -> Netz:
    """Gebautes Netz aus dem Ordner – oder `bauen(trt, builder) -> (netz, config)` und speichern.
    budget: VRAM für die Gewichte bei Netzen mit Weight-Streaming (None = alle)."""
    trt = laden()
    if trt is None:
        raise RuntimeError(grund())
    name = schluessel(art, teile)
    ordner = engine_ordner()
    datei, cache = ordner / f"{name}.engine", ordner / f"{name}.cache"
    log = trt.Logger(trt.Logger.ERROR)
    runtime = trt.Runtime(log)
    engine = blob = None
    if datei.is_file():
        blob = datei.read_bytes()
        engine = runtime.deserialize_cuda_engine(blob)
        if engine is None:
            _DBG.ereignis(f"TensorRT: {datei.name} unlesbar, baue neu")
    if engine is None:
        if fortschritt:
            fortschritt("baue TensorRT-Netz …")
        builder = trt.Builder(log)
        netz, cfg = bauen(trt, builder)
        ser = builder.build_serialized_network(netz, cfg)
        if ser is None:
            raise RuntimeError("TensorRT: Bau fehlgeschlagen")
        ordner.mkdir(parents=True, exist_ok=True)
        teil = datei.with_suffix(".tmp")
        teil.write_bytes(bytes(ser))
        teil.replace(datei)
        blob = bytes(ser)
        engine = runtime.deserialize_cuda_engine(blob)
        _DBG.ereignis(f"TensorRT: {datei.name} gebaut ({datei.stat().st_size / 2**20:.0f} MiB)")
    netz = Netz(engine, cache, budget)
    # Beim Weight-Streaming liest TensorRT die Gewichte direkt aus dem Plan im RAM: Runtime und Plan
    # müssen so lange leben wie das Netz (sonst Müll-Gewichte oder „invalid argument“).
    netz.runtime, netz.plan = runtime, blob
    return netz
