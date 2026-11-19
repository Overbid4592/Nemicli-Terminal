"""Pinned Host-Speicher in genau der angeforderten Größe.

Der Caching-Host-Allocator von torch rundet jeden pinned Block auf die nächste Zweierpotenz auf
(300 MB -> 512 MB) und behält freigegebene Blöcke bis `_host_emptyCache`.  Gewichte, die im
RAM bleiben (MoE-Experten, ausgelagerte Modelle), werden stattdessen hier über den Treiber angelegt.

Ein Block wird nur in `flush()` freigegeben, nie vom Garbage Collector: die Freigabe wartet auf das
Gerät und darf weder eine CUDA-Graph-Aufnahme stören noch einer noch eingereihten Kopie den
Speicher unter den Füßen wegziehen.
"""
from __future__ import annotations

import ctypes
from typing import Dict, List

import numpy as np
import torch

from . import cudakern

_PORTABLE = 0x01        # CU_MEMHOSTALLOC_PORTABLE: pinned für jeden Kontext
_DEVICEMAP = 0x02       # CU_MEMHOSTALLOC_DEVICEMAP: Kernel lesen ihn über PCIe

RAM_REST = 8 << 30              # so viel RAM bleibt nach dem Festsetzen frei (sonst normaler RAM)

_ausstehend: List[int] = []     # Zeiger toter Blöcke, in flush() freigegeben
_bereiche: Dict[int, int] = {}  # Anfang -> Ende der lebenden Blöcke (gleiche Adressen auf der GPU)


class _Block:
    """Besitzt eine Treiber-Allokation; numpy (und darüber jede torch-Ansicht) hält sie am Leben."""

    def __init__(self, ptr: int, n: int):
        self.ptr = ptr
        self.__array_interface__ = {"shape": (n,), "typestr": "|u1", "data": (ptr, False), "version": 3}

    def __del__(self):
        try:
            _ausstehend.append(self.ptr)        # atomar unter dem GIL; kein Lock in einem Finalizer
        except Exception:                       # Interpreter-Ende: der Prozess gibt ihn frei
            pass


def _kontext(device: torch.device) -> None:
    """Treiberaufrufe brauchen in diesem Thread einen aktuellen Kontext; torch macht seinen primären aktuell."""
    cu = cudakern._treiber()
    ctx = ctypes.c_void_p()
    if cu.cuCtxGetCurrent(ctypes.byref(ctx)) or not ctx.value:
        torch.zeros(1, device=device)


def ram_reicht(n_bytes: int) -> bool:
    """Bleiben nach dem Festsetzen von `n_bytes` noch RAM_REST frei? Ohne psutil: ja."""
    try:
        import psutil
        return psutil.virtual_memory().available - int(n_bytes) >= RAM_REST
    except Exception:
        return True


def empty(n_bytes: int, device) -> torch.Tensor:
    """uint8-Tensor mit `n_bytes` in pinned RAM, für die GPU abgebildet (bei Unified Addressing an
    derselben Adresse).  RuntimeError, wenn der Treiber so viel nicht pinnen kann oder danach
    weniger als RAM_REST frei bliebe."""
    flush()
    if not ram_reicht(n_bytes):
        raise RuntimeError(f"Festsetzen von {n_bytes} Bytes ließe weniger als {RAM_REST >> 30} GB RAM frei")
    device = torch.device(device)
    _kontext(device)
    ptr = ctypes.c_void_p()
    fehler = cudakern._treiber().cuMemHostAlloc(ctypes.byref(ptr), ctypes.c_size_t(max(1, n_bytes)),
                                                 ctypes.c_uint(_PORTABLE | _DEVICEMAP))
    if fehler or not ptr.value:
        raise RuntimeError(f"cuMemHostAlloc({n_bytes}): CUDA-Fehler {fehler}")
    _bereiche[ptr.value] = ptr.value + max(1, n_bytes)
    return torch.from_numpy(np.asarray(_Block(ptr.value, n_bytes)))


class _GpuSicht:
    """__cuda_array_interface__ eines Host-Blocks; torch hält dieses Objekt (und darüber den
    Block) am Leben, solange der CUDA-Tensor lebt."""

    def __init__(self, host: torch.Tensor):
        self.host = host
        self.__cuda_array_interface__ = {"shape": (host.numel(),), "typestr": "|u1", "strides": None,
                                         "data": (host.data_ptr(), False), "version": 3}


def abbilden(host: torch.Tensor, device) -> torch.Tensor:
    """Ein Block aus `empty()` als CUDA-Tensor an derselben Adresse: Kernel, Kopien und CUDA-Graphen
    lesen ihn direkt über PCIe, im VRAM wird nichts gehalten."""
    if not abgebildet(host):
        raise ValueError("only blocks from hostmem.empty() can be mapped")
    sicht = torch.as_tensor(_GpuSicht(host.view(-1)), device=torch.device(device))
    if sicht.data_ptr() != host.data_ptr():
        raise RuntimeError("no unified addressing: the GPU sees the block at another address")
    return sicht


def abgebildet(t: torch.Tensor) -> bool:
    """t liegt in einem Block aus `empty()` (auch als CUDA-Tensor aus `abbilden`)."""
    p = t.data_ptr()
    return any(a <= p < e for a, e in list(_bereiche.items()))     # Schnappschuss: andere Fäden ändern das Dict


def flush() -> None:
    """Gibt die Blöcke frei, die kein Tensor mehr nutzt, nachdem das Gerät alle Kopien beendet hat."""
    if not _ausstehend or torch.cuda.is_current_stream_capturing():
        return
    from .models.common import GPU_SPERRE
    with GPU_SPERRE:
        torch.cuda.synchronize()
        cu = cudakern._treiber()
        while _ausstehend:
            try:
                ptr = _ausstehend.pop()
            except IndexError:                  # von einem anderen Thread geleert
                break
            _bereiche.pop(ptr, None)
            cu.cuMemFreeHost(ctypes.c_void_p(ptr))


def pending() -> int:
    """Anzahl toter Blöcke, die auf `flush()` warten."""
    return len(_ausstehend)
