"""Pinned host memory of exactly the requested size.

torch's caching host allocator rounds every pinned block up to the next power of two
(300 MB -> 512 MB) and keeps freed blocks until `_host_emptyCache`.  Weights that stay in
RAM (MoE experts, offloaded models) are allocated here through the driver instead.

A block is freed only in `flush()`, never from the garbage collector: the free waits for the
device, and it must neither break a CUDA graph capture nor pull memory from under a copy
that is still queued.
"""
from __future__ import annotations

import ctypes
from typing import Dict, List

import numpy as np
import torch

from . import cudakern

_PORTABLE = 0x01        # CU_MEMHOSTALLOC_PORTABLE: pinned for every context
_DEVICEMAP = 0x02       # CU_MEMHOSTALLOC_DEVICEMAP: kernels read it over PCIe

_ausstehend: List[int] = []     # pointers of dead blocks, freed in flush()
_bereiche: Dict[int, int] = {}  # start -> end of the live blocks (same addresses on the GPU)


class _Block:
    """Owns one driver allocation; numpy (and through it every torch view) keeps it alive."""

    def __init__(self, ptr: int, n: int):
        self.ptr = ptr
        self.__array_interface__ = {"shape": (n,), "typestr": "|u1", "data": (ptr, False), "version": 3}

    def __del__(self):
        try:
            _ausstehend.append(self.ptr)        # atomic under the GIL; no lock in a finaliser
        except Exception:                       # interpreter shutdown: the process frees it
            pass


def _kontext(device: torch.device) -> None:
    """Driver calls need a current context in this thread; torch makes its primary one current."""
    cu = cudakern._treiber()
    ctx = ctypes.c_void_p()
    if cu.cuCtxGetCurrent(ctypes.byref(ctx)) or not ctx.value:
        torch.zeros(1, device=device)


def empty(n_bytes: int, device) -> torch.Tensor:
    """uint8 tensor of `n_bytes` in pinned RAM, mapped for the GPU (with unified addressing at
    the same address).  RuntimeError when the driver cannot pin that much."""
    flush()
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
    """__cuda_array_interface__ of a host block; torch keeps this object (and through it the
    block) alive as long as the CUDA tensor lives."""

    def __init__(self, host: torch.Tensor):
        self.host = host
        self.__cuda_array_interface__ = {"shape": (host.numel(),), "typestr": "|u1", "strides": None,
                                         "data": (host.data_ptr(), False), "version": 3}


def abbilden(host: torch.Tensor, device) -> torch.Tensor:
    """A block from `empty()` as a CUDA tensor at the same address: kernels, copies and CUDA
    graphs read it straight over PCIe, nothing is held in VRAM."""
    if not abgebildet(host):
        raise ValueError("only blocks from hostmem.empty() can be mapped")
    sicht = torch.as_tensor(_GpuSicht(host.view(-1)), device=torch.device(device))
    if sicht.data_ptr() != host.data_ptr():
        raise RuntimeError("no unified addressing: the GPU sees the block at another address")
    return sicht


def abgebildet(t: torch.Tensor) -> bool:
    """t lives in a block from `empty()` (also as a CUDA tensor from `abbilden`)."""
    p = t.data_ptr()
    return any(a <= p < e for a, e in _bereiche.items())


def flush() -> None:
    """Frees the blocks no tensor uses any more, after the device has finished all copies."""
    if not _ausstehend or torch.cuda.is_current_stream_capturing():
        return
    from .models.common import GPU_SPERRE
    with GPU_SPERRE:
        torch.cuda.synchronize()
        cu = cudakern._treiber()
        while _ausstehend:
            try:
                ptr = _ausstehend.pop()
            except IndexError:                  # emptied by another thread
                break
            _bereiche.pop(ptr, None)
            cu.cuMemFreeHost(ctypes.c_void_p(ptr))


def pending() -> int:
    """Number of dead blocks waiting for `flush()`."""
    return len(_ausstehend)
