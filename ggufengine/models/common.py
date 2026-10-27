"""Shared building blocks: weight loading, norms, RoPE, KV cache, attention, CUDA-graph decode."""
from __future__ import annotations

import math
import re
import threading
from typing import Dict, List, Optional

import torch
import torch.nn.functional as F

from .. import cudakern, mmvq
from .. import debuglog as dbg
from .. import hostmem, schichten
from ..gguf import GGML_TYPES, GGUFError, GGUFFile
from ..qlinear import build_quant_linear, kernel_available, quantizable
from ..quant import dequantize, raw_to_tensor


def release_pinned_memory() -> None:
    """Hands pinned RAM nothing uses any more back to the system: dead `hostmem` blocks and
    torch's cache of freed pinned blocks (read buffers of finished loaders)."""
    hostmem.flush()
    if hasattr(torch._C, "_host_emptyCache"):
        torch._C._host_emptyCache()


LAGER = True             # MoE experts beyond the VRAM budget: learning slots (False: whole tensors per layer)
_EXPERTEN = re.compile(r"blk\.(\d+)\.ffn_\w+_exps\.weight$")


def lager_plaetze(gg: GGUFFile, budget: Optional[int]) -> int:
    """VRAM slots per MoE layer for an expert budget: the budget split evenly over the layers
    (one slot holds one expert in every part: gate, up, down).  0: no storeroom (no budget, no
    expert tensors, a format without kernel, or not even one slot)."""
    if budget is None:
        return 0
    je_schicht: Dict[int, int] = {}
    n_exp = 0
    for name, ti in gg.tensors.items():
        m = _EXPERTEN.match(name)
        if m is None or len(ti.shape) != 3:
            continue
        if GGML_TYPES[ti.ggml_type][0] not in cudakern.FORMATE:
            return 0
        n_exp = max(n_exp, ti.shape[0])
        je_schicht[int(m.group(1))] = je_schicht.get(int(m.group(1)), 0) + ti.n_bytes // ti.shape[0]
    if not je_schicht:
        return 0
    return max(0, min(n_exp, budget // sum(je_schicht.values())))


class WeightLoader:
    """Dequantises tensors from the GGUF file on demand, straight onto the device."""

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        """experten_vram: VRAM budget in bytes for MoE experts (None: no limit).  Expert tensors
        beyond it stay in pinned RAM in their block format; the GPU kernels read the chosen
        experts directly over PCIe (like llama.cpp --n-cpu-moe, filled layer by layer)."""
        self.gg = gg
        self.device = device
        self.dtype = dtype
        self.kv_bits = kv_bits      # full-context KV caches: 16 (compute dtype) or 8 (int8)
        self.progress = progress
        self.loaded_bytes = 0
        self.quant = quant and kernel_available(device)
        self.experten_rest = experten_vram if device.type == "cuda" else None
        # with a budget: every expert in pinned RAM, the budget becomes VRAM slots per layer for
        # the most used experts (moe.ExpertenLager) – if the experts have kernels and LAGER is on
        self.lager_plaetze = lager_plaetze(gg, experten_vram) if LAGER and self.experten_rest is not None else 0
        self.n_quant = 0            # matrices kept in quantised form
        self.ram_namen = SCHICHTEN_RAM if device.type == "cuda" else frozenset()
        self.ram_bytes = 0          # weights actually placed in pinned RAM
        self.band = Fliessband(device) if self.ram_namen else None
        self._ring = None           # pinned read buffers [tensor, event of the last copy]
        self._ring_pos = 0
        self._strom = None          # copy stream: file reads overlap the conversions on the GPU

    LESE_STUECK = 32 * 2**20
    LESE_PUFFER = 4

    def _roh(self, ti, device) -> torch.Tensor:
        """Raw tensor bytes on `device`.  To the GPU they go as large file reads through a
        ring of pinned buffers on a copy stream, so reading the next tensor overlaps the
        conversion of this one (page faults on the mapping are about half as fast); on the
        CPU they stay a zero-copy view of the mapping."""
        device = torch.device(device)
        if device.type != "cuda":
            return raw_to_tensor(self.gg.tensor_bytes(ti), device)
        if self._ring is None:
            try:
                self._ring = [[torch.empty(self.LESE_STUECK, dtype=torch.uint8, pin_memory=True), None]
                              for _ in range(self.LESE_PUFFER)]
                self._strom = torch.cuda.Stream(device)
            except RuntimeError as exc:
                dbg.event(f"Lesepuffer nicht festsetzbar ({exc}), Gewichte über die Dateiabbildung")
                self._ring = []
        if not self._ring:
            return raw_to_tensor(self.gg.tensor_bytes(ti), device)
        ziel = torch.empty(ti.n_bytes, dtype=torch.uint8, device=device)
        # `ziel` may reuse memory that queued kernels on the compute stream still read
        self._strom.wait_stream(torch.cuda.current_stream(device))
        for start in range(0, ti.n_bytes, self.LESE_STUECK):
            platz = self._ring[self._ring_pos]
            self._ring_pos = (self._ring_pos + 1) % len(self._ring)
            if platz[1] is not None:
                platz[1].synchronize()                # buffer still being copied
            n = self.gg.read_into(ti, platz[0].numpy(), start)
            with torch.cuda.stream(self._strom):
                ziel[start:start + n].copy_(platz[0][:n], non_blocking=True)
                platz[1] = torch.cuda.Event()
                platz[1].record(self._strom)
        ziel.record_stream(self._strom)
        torch.cuda.current_stream(device).wait_stream(self._strom)
        return ziel

    def _im_ram(self, ti) -> Optional[torch.Tensor]:
        """Raw bytes of a planned RAM tensor as a CUDA view of pinned RAM (None: not planned,
        or the driver cannot pin that much – then it goes to VRAM as usual)."""
        if ti.name not in self.ram_namen:
            return None
        try:
            host = hostmem.empty(ti.n_bytes, self.device)
            self.gg.read_into(ti, host.numpy())
            sicht = hostmem.abbilden(host, self.device)
        except (RuntimeError, ValueError) as exc:
            dbg.event(f"{ti.name}: RAM nicht festsetzbar ({exc}), bleibt im VRAM")
            return None
        self.ram_bytes += ti.n_bytes
        return sicht

    def has(self, name: str) -> bool:
        return name in self.gg.tensors

    def get(self, name: str, dtype: Optional[torch.dtype] = None) -> torch.Tensor:
        ti = self.gg.tensors.get(name)
        if ti is None:
            raise GGUFError(f"missing tensor {name!r}")
        raw = self._roh(ti, self.device)
        out = dequantize(raw, ti.ggml_type, ti.n_elements, dtype or self.dtype)
        del raw
        self.loaded_bytes += ti.n_bytes
        if self.progress:
            self.progress(name, self.loaded_bytes)
        return out.view(ti.shape)

    def norm(self, name: str) -> torch.Tensor:
        return self.get(name, dtype=torch.float32)

    def _info2d(self, name: str):
        ti = self.gg.tensors.get(name)
        if ti is None:
            raise GGUFError(f"missing tensor {name!r}")
        if len(ti.shape) != 2:
            raise GGUFError(f"tensor {name!r} is not a matrix")
        return ti

    def _count(self, ti):
        self.n_quant += 1
        self.loaded_bytes += ti.n_bytes
        if self.progress:
            self.progress(ti.name, self.loaded_bytes)

    def linear(self, name: str, bias_name: Optional[str] = None):
        """Linear layer; stays quantised in VRAM when the kernel supports the block type."""
        ti = self._info2d(name)
        bias = self.norm(bias_name) if bias_name and self.has(bias_name) else None
        n_out, n_in = ti.shape
        if (roh := self._im_ram(ti)) is not None:                # block format, copied over PCIe per use
            pl = PackedLinear(roh, ti.ggml_type, n_out, n_in, bias, self.dtype)
            pl.band, pl.nr = self.band, self.band.anmelden(roh)
            self._count(ti)
            return pl
        if self.quant and mmvq_weg(ti.ggml_type, n_in, self.device):   # bleibt im Blockformat (mmvq)
            pl = PackedLinear(self._roh(ti, self.device), ti.ggml_type, n_out, n_in, bias, self.dtype)
            self._count(ti)
            return pl
        if self.quant and quantizable(ti.ggml_type, n_out, n_in):
            raw = self._roh(ti, self.device)
            ql = build_quant_linear(raw, ti.ggml_type, n_out, n_in, bias)
            del raw
            self._count(ti)
            return ql
        if packed_type(ti.ggml_type):
            pl = PackedLinear(self._roh(ti, self.device), ti.ggml_type,
                              n_out, n_in, bias, self.dtype)
            self._count(ti)
            return pl
        return Linear(self.get(name), bias)

    def experts(self, name: str) -> list:
        """3-D expert tensor (n_expert, out, in) -> one linear layer per expert (each
        expert's rows are contiguous in the file, so they are cut out without copying)."""
        ti = self.gg.tensors.get(name)
        if ti is None:
            raise GGUFError(f"missing tensor {name!r}")
        if len(ti.shape) != 3:
            raise GGUFError(f"tensor {name!r} is not a 3-D expert tensor")
        n_exp, n_out, n_in = ti.shape
        if not 0 < n_exp <= 1024:
            raise GGUFError(f"tensor {name!r}: implausible expert count {n_exp}")
        per = ti.n_bytes // n_exp
        if self.experten_rest is not None and GGML_TYPES[ti.ggml_type][0] in cudakern.FORMATE \
                and (self.lager_plaetze or not self._passt_in_vram(ti, n_out, n_in)):
            try:                                                             # pinned: readable by the GPU kernels
                roh = hostmem.empty(ti.n_bytes, self.device)               # exact size, no rounding
                self.gg.read_into(ti, roh.numpy())
            except RuntimeError as exc:                                      # too much to pin: unpack per step
                roh = raw_to_tensor(self.gg.tensor_bytes(ti), "cpu")       # view of the file mapping
                dbg.event(f"{name}: RAM nicht festsetzbar ({exc}), Experten werden je Schritt kopiert")
            ex = PackedExperts(roh, ti.ggml_type, n_exp, n_out, n_in, self.dtype, geraet=self.device)
            self._count(ti)
            return ex
        if packed_type(ti.ggml_type):
            ex = PackedExperts(self._roh(ti, self.device), ti.ggml_type,
                               n_exp, n_out, n_in, self.dtype)
            self._count(ti)
            return ex
        raw = self._roh(ti, self.device)
        out = []
        for e in range(n_exp):
            part = raw[e * per:(e + 1) * per]
            if self.quant and quantizable(ti.ggml_type, n_out, n_in, min_elements=0):
                out.append(build_quant_linear(part, ti.ggml_type, n_out, n_in))
            else:
                w = dequantize(part, ti.ggml_type, n_out * n_in, self.dtype).view(n_out, n_in)
                out.append(Linear(w))
        del raw
        self._count(ti)
        return out

    def _passt_in_vram(self, ti, n_out: int, n_in: int) -> bool:
        """Expert tensor within the remaining VRAM budget (then it is booked)."""
        art = GGML_TYPES[ti.ggml_type][0]
        if packed_type(ti.ggml_type):
            groesse = ti.n_bytes
        elif self.quant and quantizable(ti.ggml_type, n_out, n_in, min_elements=0):
            groesse = ti.n_elements * (5 if art in ("Q4_0", "Q4_1", "Q4_K") else 10) // 8
        else:
            groesse = ti.n_elements * 2
        if groesse > self.experten_rest:
            return False
        self.experten_rest -= groesse
        return True

    def embedding(self, name: str, tied_head: bool, device=None):
        """Token embedding (+ optional tied LM head sharing the same bytes).
        `device="cpu"` keeps a block-format table in the file mapping (RAM) instead of VRAM."""
        ti = self._info2d(name)
        n_vocab, n_embd = ti.shape
        blockformat = ti.ggml_type in GGML_TYPES and GGML_TYPES[ti.ggml_type][1] > 1
        if device is not None and torch.device(device).type == "cpu" and blockformat and not tied_head:
            raw = raw_to_tensor(self.gg.tensor_bytes(ti), "cpu")      # zero-copy view of the mmap
            self._count(ti)
            return Embedding(raw, ti.ggml_type, n_vocab, n_embd), None
        if not tied_head and blockformat and (roh := self._im_ram(ti)) is not None:
            self._count(ti)                                      # one row per token over PCIe
            return Embedding(roh, ti.ggml_type, n_vocab, n_embd), None
        if self.quant and mmvq_weg(ti.ggml_type, n_embd, self.device):   # der Kopf liest dieselben Bytes
            raw = self._roh(ti, self.device)
            head = PackedLinear(raw, ti.ggml_type, n_vocab, n_embd, None, self.dtype) if tied_head else None
            self._count(ti)
            return Embedding(raw, ti.ggml_type, n_vocab, n_embd), head
        if self.quant and quantizable(ti.ggml_type, n_vocab, n_embd):
            raw = self._roh(ti, self.device)
            emb = Embedding(raw, ti.ggml_type, n_vocab, n_embd)
            head = build_quant_linear(raw, ti.ggml_type, n_vocab, n_embd) if tied_head else None
            self._count(ti)
            return emb, head
        if packed_type(ti.ggml_type):
            raw = self._roh(ti, self.device)
            head = PackedLinear(raw, ti.ggml_type, n_vocab, n_embd, None, self.dtype) if tied_head else None
            self._count(ti)
            return Embedding(raw, ti.ggml_type, n_vocab, n_embd), head
        table = self.get(name)
        return Embedding(table, None, n_vocab, n_embd), (Linear(table) if tied_head else None)


def packed_type(ggml_type: int) -> bool:
    """Block formats without an int4 path: matrices stay in this format in memory."""
    return GGML_TYPES[ggml_type][0] in cudakern.IMMER_GEPACKT


# Formate, die im Blockformat bleiben und über die int8-Kerne (mmvq) rechnen statt über den int4-Weg:
# kleiner (Q4_K 4,5 statt 5, Q6_K 6,56 statt 10 Bit je Gewicht), beim Schreiben schneller und genauer.
MMVQ_STATT_INT4 = frozenset(("Q4_K", "Q6_K"))


def mmvq_weg(ggml_type: int, n_in: int, device) -> bool:
    name = GGML_TYPES[ggml_type][0]
    return name in MMVQ_STATT_INT4 and torch.device(device).type == "cuda" and mmvq.verfuegbar(device, name, n_in)


class Fliessband:
    """Matrices in pinned RAM reach the GPU as whole copies on a copy stream, a few ahead of their
    use, into a ring of VRAM slots – one large copy runs at full PCIe speed, kernels reading the
    RAM directly only reach about half of it.  The copies wait for the slot's last use and the
    computation waits for its copy (events), so it also works inside a CUDA graph capture.

    Matrices are numbered in loading order, which is the order of use in a forward pass; a number
    not above the previous one starts a new pass."""

    PLAETZE = schichten.BAND_PLAETZE

    def __init__(self, device: torch.device):
        self.device = device
        self.quellen: List[torch.Tensor] = []
        self._ring: Optional[torch.Tensor] = None
        self._strom = None
        self._inhalt: List[int] = []             # matrix number per slot (-1: none)
        self._fertig: list = []                  # event per slot: last computation that read it
        self._gelesen: List[bool] = []           # that event belongs to this pass
        self._bereit: list = []                  # event per slot: copy finished
        self._zuletzt = 1 << 62

    def anmelden(self, quelle: torch.Tensor) -> int:
        self.quellen.append(quelle.reshape(-1))
        return len(self.quellen) - 1

    def _anlegen(self) -> None:
        platz = max(q.numel() for q in self.quellen)
        self._ring = torch.empty(self.PLAETZE, platz, dtype=torch.uint8, device=self.device)
        self._strom = torch.cuda.Stream(self.device)
        self._inhalt = [-1] * self.PLAETZE
        self._gelesen = [False] * self.PLAETZE
        self._fertig = [torch.cuda.Event() for _ in range(self.PLAETZE)]
        self._bereit = [torch.cuda.Event() for _ in range(self.PLAETZE)]

    def _laden(self, nr: int) -> None:
        p = nr % self.PLAETZE
        if self._inhalt[p] == nr:
            return
        q = self.quellen[nr]
        if self._gelesen[p]:                     # earlier uses: covered by the wait at the start of the pass
            self._strom.wait_event(self._fertig[p])
        with torch.cuda.stream(self._strom):
            self._ring[p, :q.numel()].copy_(q, non_blocking=True)
            self._bereit[p].record(self._strom)
        self._inhalt[p] = nr

    def holen(self, nr: int) -> torch.Tensor:
        """Bytes of matrix nr in VRAM; the following ones are already on their way."""
        if self._ring is None:
            self._anlegen()
        rechnen = torch.cuda.current_stream(self.device)
        if nr <= self._zuletzt:                  # new pass: slots from before may still be read
            self._strom.wait_stream(rechnen)
            self._inhalt = [-1] * self.PLAETZE
            self._gelesen = [False] * self.PLAETZE
        self._zuletzt = nr
        for k in range(nr, min(nr + self.PLAETZE - 1, len(self.quellen) - 1) + 1):
            self._laden(k)
        p = nr % self.PLAETZE
        rechnen.wait_event(self._bereit[p])
        return self._ring[p, :self.quellen[nr].numel()]

    def fertig(self, nr: int) -> None:
        p = nr % self.PLAETZE
        self._fertig[p].record(torch.cuda.current_stream(self.device))
        self._gelesen[p] = True


class PackedLinear:
    """y = x @ W^T (+ b), W (n_out, n_in) kept in a block format without int4 path: up to
    KERNEL_BIS rows (tokens) per call through the own kernels (one row: mv, more: mm, the
    weights read once), more rows unpack W piecewise into the compute dtype."""

    KERNEL_BIS = cudakern.MM_ZEILEN
    ZEILEN_JE_STUECK = 32 * 1024 * 1024          # values unpacked at once

    def __init__(self, raw: torch.Tensor, ggml_type: int, n_out: int, n_in: int,
                 bias: Optional[torch.Tensor], dtype: torch.dtype):
        self.raw = raw.view(1, -1)
        self.ggml_type = ggml_type
        self.n_out, self.n_in = n_out, n_in
        self.b = None if bias is None else bias.to(dtype)
        self.dtype = dtype
        self.w = None                            # duck-typing with Linear
        self.name = GGML_TYPES[ggml_type][0]
        _, bs, ts = GGML_TYPES[ggml_type]
        self.zeilen_bytes = n_in // bs * ts
        self.kernel = raw.is_cuda and cudakern.verfuegbar(raw.device, self.name, n_in)
        self.mmvq = raw.is_cuda and mmvq.verfuegbar(raw.device, self.name, n_in)
        self.mm_bis = min(self.KERNEL_BIS, cudakern.mm_bis(raw.device)) if self.kernel else 1
        self.im_ram = raw.is_cuda and hostmem.abgebildet(raw)   # pinned RAM, read over PCIe
        self.band: Optional[Fliessband] = None   # with it: whole copies a few matrices ahead
        self.nr = -1
        self._nullen: dict = {}

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        if self.band is None:
            return self._rechnen(x, self.raw)
        roh = self.band.holen(self.nr).view(1, -1)
        try:
            return self._rechnen(x, roh)
        finally:
            self.band.fertig(self.nr)

    def _rechnen(self, x: torch.Tensor, raw: torch.Tensor) -> torch.Tensor:
        x2 = x.reshape(-1, self.n_in)
        M = x2.shape[0]
        im_ram = self.im_ram and raw is self.raw
        if self.mmvq and raw.is_cuda and M <= mmvq.MMVQ_BIS[self.name] and not im_ram:   # wenige Token: int8-Kerne
            y = mmvq.mv(self.name, raw, x2, self.n_out, self.n_in).to(self.dtype)
        elif self.mmvq and raw.is_cuda and M <= mmvq.MMQ_BIS[self.name] and not im_ram:  # mittel: Tensor-Kerne
            y = mmvq.mm(self.name, raw, x2, self.n_out, self.n_in).to(self.dtype)
        elif self.kernel and raw.is_cuda and M == 1:
            if M not in self._nullen:            # the row uses "expert" 0, i.e. W itself
                self._nullen[M] = torch.zeros(M, dtype=torch.long, device=raw.device)
            y = cudakern.mv(self.name, raw, self._nullen[M], x2, self.n_out, self.n_in).to(self.dtype)
        elif self.kernel and raw.is_cuda and M <= self.mm_bis:
            y = cudakern.mm(self.name, raw, x2, self.n_out, self.n_in).to(self.dtype)
        else:
            xd = x2.to(self.dtype)
            y = torch.empty(M, self.n_out, dtype=self.dtype, device=x2.device)
            stueck = max(1, self.ZEILEN_JE_STUECK // self.n_in)
            for r0 in range(0, self.n_out, stueck):
                r1 = min(r0 + stueck, self.n_out)
                roh = raw[0, r0 * self.zeilen_bytes:r1 * self.zeilen_bytes]
                if im_ram:                       # one copy over PCIe, unpacking reads the bytes several times
                    roh = roh.clone()
                if self.mmvq:
                    w = mmvq.entpacken(self.name, roh, r1 - r0, self.n_in).to(self.dtype)
                else:
                    w = dequantize(roh, self.ggml_type, (r1 - r0) * self.n_in, self.dtype).view(r1 - r0, self.n_in)
                y[:, r0:r1] = xd @ w.t()
                del w
        if self.b is not None:
            y = y + self.b
        return y.reshape(*x.shape[:-1], self.n_out)


class PackedExperts:
    """Expert tensor (n_expert, out, in) kept in its GGUF block format.  For one token the
    selected experts are multiplied directly on the block data (cudakern); longer inputs
    unpack the selected experts into the compute dtype (temporary copy)."""

    def __init__(self, raw: torch.Tensor, ggml_type: int, n_exp: int, n_out: int, n_in: int,
                 dtype: torch.dtype, geraet: Optional[torch.device] = None):
        """geraet: where the computation runs (raw may live in pinned RAM)."""
        self.raw = raw.view(n_exp, -1)
        self.geraet = torch.device(geraet) if geraet is not None else raw.device
        self.ggml_type = ggml_type
        self.n_exp, self.n_out, self.n_in = n_exp, n_out, n_in
        self.dtype = dtype
        self.name = GGML_TYPES[ggml_type][0]
        lesbar = raw.is_cuda or (self.geraet.type == "cuda" and raw.is_pinned())
        self.kernel = lesbar and cudakern.verfuegbar(self.geraet, self.name, n_in)
        self.im_ram = not raw.is_cuda and self.geraet.type == "cuda" and raw.is_pinned()
        self.adressen: Optional[torch.Tensor] = None   # set by moe.ExpertenLager: VRAM slot or RAM per expert

    def __len__(self) -> int:
        return self.n_exp

    def weights(self, ids: torch.Tensor) -> torch.Tensor:
        """(k,) expert ids (any device) -> (k, n_out, n_in) in the compute dtype."""
        part = self.raw.index_select(0, ids.to(self.raw.device)).reshape(-1).to(self.geraet, non_blocking=True)
        return dequantize(part, self.ggml_type, ids.numel() * self.n_out * self.n_in,
                          self.dtype).view(-1, self.n_out, self.n_in)

    def matvec(self, ids: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        """The experts `ids` (k,) applied to one shared input x (n_in,) -> (k, n_out) float32."""
        if self.kernel:
            return cudakern.mv(self.name, self.raw, ids, x.reshape(-1), self.n_out, self.n_in, self.adressen)
        w = self.weights(ids)
        return torch.matmul(w, x.to(w.dtype).reshape(-1, 1))[..., 0].float()

    def matvec_je(self, ids: torch.Tensor, a: torch.Tensor) -> torch.Tensor:
        """Expert ids[j] applied to its own input a[j]: (k, n_in) -> (k, n_out) float32."""
        if self.kernel:
            return cudakern.mv(self.name, self.raw, ids, a.reshape(ids.numel(), -1), self.n_out, self.n_in,
                               self.adressen)
        w = self.weights(ids)
        return torch.bmm(a.to(w.dtype).unsqueeze(1), w.transpose(1, 2))[:, 0].float()

    def __getitem__(self, e: int) -> "_PackedExpert":
        if not 0 <= e < self.n_exp:
            raise IndexError(e)
        return _PackedExpert(self, e)


class _PackedExpert:
    """One expert of a PackedExperts tensor as a linear layer."""

    def __init__(self, owner: PackedExperts, e: int):
        self.owner, self.e = owner, e
        self.n_out, self.n_in = owner.n_out, owner.n_in

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        w = self.owner.weights(torch.tensor([self.e], device=self.owner.geraet))[0]
        return x.to(w.dtype) @ w.t()


class Embedding:
    """Row lookup; rows are dequantised on the fly when the table is kept in block format."""

    def __init__(self, data: torch.Tensor, ggml_type: Optional[int], n_vocab: int, n_embd: int):
        self.n_vocab, self.n_embd = n_vocab, n_embd
        self.ggml_type = ggml_type
        if ggml_type is None:
            self.table = data                                  # (V, E) compute dtype
            self.rows = None
        else:
            _, block_size, type_size = GGML_TYPES[ggml_type]
            self.rows = data.view(n_vocab, n_embd // block_size * type_size)   # raw bytes per row
            self.table = None

    def __call__(self, tok: torch.Tensor) -> torch.Tensor:
        if self.table is not None:
            return self.table.index_select(0, tok).float()
        raw = self.rows.index_select(0, tok.to(self.rows.device)).reshape(-1)
        return dequantize(raw, self.ggml_type, tok.numel() * self.n_embd, torch.float32).view(-1, self.n_embd)


class Linear:
    """y = x @ W^T (+ b).  W stored as (out, in) in the compute dtype."""

    __slots__ = ("w", "b", "n_out", "n_in")

    def __init__(self, w: torch.Tensor, b: Optional[torch.Tensor] = None):
        self.w = w
        self.b = None if b is None else b.to(w.dtype)
        self.n_out, self.n_in = w.shape

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        return F.linear(x.to(self.w.dtype), self.w, self.b)


def letzte_zeilen(model, h: torch.Tensor) -> torch.Tensor:
    """Rows that get logits: the last one, or all while `model.alle_logits` is set
    (several proposed tokens are checked in one pass)."""
    return h if model.alle_logits else h[-1:]


def logit_zeilen(model, logits: torch.Tensor) -> torch.Tensor:
    """(V,) for the last row, (L, V) with `model.alle_logits`."""
    return logits if model.alle_logits else logits[0]


def rms_norm(x: torch.Tensor, w: torch.Tensor, eps: float) -> torch.Tensor:
    xf = x.float()
    xf = xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + eps)
    return xf * w


def layer_norm(x: torch.Tensor, w: torch.Tensor, b: Optional[torch.Tensor], eps: float) -> torch.Tensor:
    return F.layer_norm(x.float(), (x.shape[-1],), w, b, eps)


def group_rms_norm(x: torch.Tensor, w: torch.Tensor, eps: float, groups: int) -> torch.Tensor:
    """RMSNorm over `groups` equal slices of the last dimension (K2-Horizon)."""
    if groups == 1:
        return rms_norm(x, w, eps)
    xf = x.float().unflatten(-1, (groups, -1))
    xf = xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + eps)
    return xf.flatten(-2) * w


class RoPE:
    """Rotary embedding on the first `dim` features of each head.

    neox=True : rotate halves (pairs (i, i+dim/2)) -- Qwen, Llama-3 HF layout, GPT-NeoX, Gemma
    neox=False: rotate adjacent pairs (2i, 2i+1)   -- llama.cpp "normal" mode (llama, mistral)
    `freq_factors` (dim/2,) divides the inverse frequencies (llama.cpp rope_freqs / "proportional rope").
    `scale` > 1: linear position interpolation.  `yarn` = (factor, original context, beta_fast,
    beta_slow[, truncate]): YaRN frequency blend.  `mscale` multiplies cos and sin (YaRN / LongRoPE attention
    factor).  Tables are precomputed for the whole context so lookups are pure tensor ops
    (CUDA-graph safe).
    """

    def __init__(self, dim: int, base: float, device, max_pos: int, neox: bool = True,
                 freq_factors: Optional[torch.Tensor] = None, scale: float = 1.0,
                 yarn: Optional[tuple] = None, mscale: float = 1.0):
        self.dim = dim
        self.neox = neox
        inv = 1.0 / (base ** (torch.arange(0, dim, 2, device=device, dtype=torch.float32) / dim))
        if yarn is not None:
            inv = _yarn_inv_freq(inv, dim, base, *yarn)
        if freq_factors is not None:
            if freq_factors.numel() != dim // 2:
                raise GGUFError("rope_freqs has the wrong length")
            inv = inv / freq_factors.float().to(inv.device)
        if scale != 1.0:
            inv = inv / scale
        t = torch.arange(max_pos, device=device, dtype=torch.float32)
        freqs = torch.outer(t, inv)                     # (n, dim/2)
        self.cos, self.sin = freqs.cos() * mscale, freqs.sin() * mscale

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor) -> torch.Tensor:
        """x: (L, H, D) float32, pos_idx: (L,) long.  Rotates x[..., :dim]."""
        cos = self.cos.index_select(0, pos_idx).unsqueeze(1)   # (L,1,dim/2)
        sin = self.sin.index_select(0, pos_idx).unsqueeze(1)
        xr, xp = x[..., :self.dim], x[..., self.dim:]
        h = self.dim // 2
        if self.neox:
            x1, x2 = xr[..., :h], xr[..., h:]
            xr = torch.cat([x1 * cos - x2 * sin, x1 * sin + x2 * cos], dim=-1)
        else:
            x1, x2 = xr[..., 0::2], xr[..., 1::2]
            xr = torch.stack([x1 * cos - x2 * sin, x1 * sin + x2 * cos], dim=-1).flatten(-2)
        return torch.cat([xr, xp], dim=-1) if xp.shape[-1] else xr


def _yarn_inv_freq(inv: torch.Tensor, dim: int, base: float, factor: float, orig_ctx: int,
                   beta_fast: float = 32.0, beta_slow: float = 1.0, truncate: bool = True) -> torch.Tensor:
    """YaRN: high frequencies kept (extrapolation), low ones divided by `factor`
    (interpolation), a linear ramp in between (transformers `_compute_yarn_parameters`).
    `truncate`: ramp bounds rounded outwards (default; gpt-oss uses them unrounded)."""
    def corr(rot):
        return (dim * math.log(orig_ctx / (rot * 2 * math.pi))) / (2 * math.log(base))
    lo, hi = corr(beta_fast), corr(beta_slow)
    if truncate:
        lo, hi = math.floor(lo), math.ceil(hi)
    lo, hi = max(lo, 0), min(hi, dim - 1)
    if lo == hi:
        hi += 0.001
    ramp = ((torch.arange(dim // 2, device=inv.device, dtype=torch.float32) - lo) / (hi - lo)).clamp(0, 1)
    extra = 1 - ramp
    return (inv / factor) * (1 - extra) + inv * extra


PREFILL_CHUNK = 512      # max. tokens per forward call (Engine.feed splits longer prompts)


KV_GROWTH = 4096         # full KV caches start this big and grow in these steps (Engine.feed)


KV_GROUP = 32            # 8-bit KV cache: one scale per 32 values of a head vector (like q8_0)


# Positions per full KV cache that live in VRAM (None: all).  Set by the Engine while it
# builds the model; positions beyond it go to RAM blocks and travel to the GPU per layer.
KV_GPU_SLOTS: Optional[int] = None

# Weight matrices that stay in pinned RAM (schichten.planen).  Set by the Engine while it
# builds the model; the GPU reads them over PCIe at every step.
SCHICHTEN_RAM: frozenset = frozenset()


class KVCache:
    """Full-context KV cache: slot i holds position i.  Allocated for KV_GROWTH positions
    and grown on demand up to `max_len` (`ensure`), so a short chat does not reserve the
    VRAM of the whole context.  With bits=8 keys and values are stored as int8 with one
    scale per KV_GROUP values (53 % of the bf16 size); `kv(n)` hands them to the attention
    in the compute dtype, one layer at a time.

    With KV_GPU_SLOTS set (smaller than max_len) only the first `gpu_max` positions stay in
    VRAM; later ones go to RAM blocks of KV_GROWTH positions (pinned when possible) and
    `kv(n)` copies them to the GPU for each attention call – a context larger than the VRAM
    allows, slower once it is longer than `gpu_max`."""

    def __init__(self, n_kv_heads: int, head_dim: int, max_len: int, device, dtype, bits: int = 16):
        self.max_len = max_len
        self.dtype = dtype
        self.int8 = bits == 8 and head_dim % KV_GROUP == 0
        teilung = KV_GPU_SLOTS
        self.gpu_max = max_len if not teilung or teilung >= max_len else max(1, int(teilung))
        self.ram: List[tuple] = []        # RAM blocks for positions >= gpu_max
        self.k, self.v, self.ks, self.vs = self._empty(n_kv_heads, min(self.gpu_max, KV_GROWTH), head_dim, device)
        self.pos = None           # slot i holds position i

    @property
    def geteilt(self) -> bool:
        return self.gpu_max < self.max_len

    def _empty(self, heads: int, n: int, dim: int, device, pin: bool = False):
        def neu(shape, dt):
            if pin:
                try:
                    return torch.zeros(shape, dtype=dt, pin_memory=True)
                except RuntimeError:              # pinned RAM exhausted: pageable (slower copies)
                    pass
            return torch.zeros(shape, device=device, dtype=dt)
        if not self.int8:
            return neu((heads, n, dim), self.dtype), neu((heads, n, dim), self.dtype), None, None
        s = (heads, n, dim // KV_GROUP)
        return (neu((heads, n, dim), torch.int8), neu((heads, n, dim), torch.int8),
                neu(s, self.dtype), neu(s, self.dtype))

    @property
    def heads(self) -> int:
        return self.k.shape[0]

    @property
    def head_dim(self) -> int:
        return self.k.shape[2]

    def ensure(self, n: int) -> bool:
        """Room for positions [0, n); returns True if the tensors were replaced (captured
        CUDA graphs that point at the old ones must be dropped)."""
        if n > self.max_len:
            raise RuntimeError(f"context window of {self.max_len} tokens exceeded")
        while self.geteilt and self.gpu_max + len(self.ram) * KV_GROWTH < n:   # RAM blocks
            self.ram.append(self._empty(self.heads, KV_GROWTH, self.head_dim, "cpu", pin=True))
        cap = self.k.shape[1]
        n = min(n, self.gpu_max)
        if n <= cap:
            return False
        neu = min(self.gpu_max, -(-n // KV_GROWTH) * KV_GROWTH)
        with torch.inference_mode():
            parts = self._empty(self.heads, neu, self.head_dim, self.k.device)
            for old, new in zip((self.k, self.v, self.ks, self.vs), parts):
                if old is not None:
                    new[:, :cap].copy_(old)
        self.k, self.v, self.ks, self.vs = parts
        return True

    def _ram_schreiben(self, werte: tuple, pos: torch.Tensor):
        """werte: tensors (H, L, ·) in storage format for positions `pos` (>= gpu_max, CPU)."""
        rel = pos - self.gpu_max
        block = torch.div(rel, KV_GROWTH, rounding_mode="floor")
        for b in torch.unique(block).tolist():
            wahl = block == b
            idx = rel[wahl] - b * KV_GROWTH
            for ziel, w in zip(self.ram[b], werte):
                if ziel is not None:
                    ziel.index_copy_(1, idx, w[:, wahl])

    def _ram_lesen(self, n: int, device) -> List[Optional[torch.Tensor]]:
        """Positions [gpu_max, n) of all four stores on `device`."""
        rest = n - self.gpu_max
        raus = []
        for i in range(4):
            if self.ram[0][i] is None:
                raus.append(None)
                continue
            teile = [blk[i][:, :min(KV_GROWTH, rest - j * KV_GROWTH)]
                     for j, blk in enumerate(self.ram) if j * KV_GROWTH < rest]
            raus.append(torch.cat([t.to(device, non_blocking=True) for t in teile], dim=1))
        return raus

    def _pack(self, x: torch.Tensor):
        """(H, L, D) -> int8 values and one scale per KV_GROUP (scale rounded first, so the
        stored pair reproduces the values as closely as the grid allows)."""
        g = x.float().unflatten(-1, (-1, KV_GROUP))
        s = (g.abs().amax(-1) / 127).to(self.dtype)
        q = torch.round(g / s.float().clamp_min(1e-30).unsqueeze(-1)).clamp_(-127, 127)
        return q.to(torch.int8).flatten(-2), s

    def _unpack(self, q: torch.Tensor, s: torch.Tensor) -> torch.Tensor:
        return (q.unflatten(-1, (-1, KV_GROUP)).to(self.dtype) * s.unsqueeze(-1)).flatten(-2)

    def write(self, k: torch.Tensor, v: torch.Tensor, pos_idx: torch.Tensor):
        """k, v: (L, n_kv_heads, head_dim); pos_idx: (L,) positions to write."""
        k, v = k.transpose(0, 1), v.transpose(0, 1)
        if self.int8:
            (kq, ks), (vq, vs) = self._pack(k), self._pack(v)
            werte = (kq, vq, ks, vs)
        else:
            werte = (k.to(self.k.dtype), v.to(self.v.dtype), None, None)
        if self.geteilt:
            drin = pos_idx < self.gpu_max
            if not bool(drin.all()):                   # some positions go to RAM
                draussen = ~drin
                self._ram_schreiben(tuple(w[:, draussen].cpu() if w is not None else None for w in werte),
                                    pos_idx[draussen].cpu())
                if not bool(drin.any()):
                    return
                werte = tuple(w[:, drin] if w is not None else None for w in werte)
                pos_idx = pos_idx[drin]
        for ziel, w in zip((self.k, self.v, self.ks, self.vs), werte):
            if ziel is not None:
                ziel.index_copy_(1, pos_idx, w)

    def kv(self, n: int):
        """Keys and values of positions [0, n) in the compute dtype, (H, n, D) each."""
        teile = [self.k[:, :n], self.v[:, :n], None if self.ks is None else self.ks[:, :n],
                 None if self.vs is None else self.vs[:, :n]]
        if n > self.gpu_max:                           # the rest comes from RAM
            ram = self._ram_lesen(n, self.k.device)
            teile = [None if t is None else torch.cat([t, r], dim=1) for t, r in zip(teile, ram)]
        if not self.int8:
            return teile[0], teile[1]
        return self._unpack(teile[0], teile[2]), self._unpack(teile[1], teile[3])

    def fill(self, k: torch.Tensor, v: torch.Tensor):
        """Positions [0, n) from keys/values as `kv(n)` returns them (prefix cache)."""
        n = k.shape[1]
        k, v = k.to(self.k.device), v.to(self.k.device)
        if self.int8:
            (kq, ks), (vq, vs) = self._pack(k), self._pack(v)
            werte = (kq, vq, ks, vs)
        else:
            werte = (k.to(self.k.dtype), v.to(self.v.dtype), None, None)
        g = min(n, self.gpu_max)
        for ziel, w in zip((self.k, self.v, self.ks, self.vs), werte):
            if ziel is not None:
                ziel[:, :g].copy_(w[:, :g])
        if n > g:
            self._ram_schreiben(tuple(None if w is None else w[:, g:].cpu() for w in werte),
                                torch.arange(g, n))


class RingKVCache:
    """KV cache for sliding-window layers: a fixed ring of `window + PREFILL_CHUNK` slots
    instead of the full context.  Position p lives in slot p % size; `pos` records which
    position each slot holds (-1 = empty), so the attention mask works on real positions.
    A forward call of up to PREFILL_CHUNK tokens never overwrites a slot that one of its
    queries still needs.  Pure tensor ops – CUDA-graph safe."""

    def __init__(self, n_kv_heads: int, head_dim: int, window: int, device, dtype):
        self.size = window + PREFILL_CHUNK
        self.k = torch.zeros(n_kv_heads, self.size, head_dim, device=device, dtype=dtype)
        self.v = torch.zeros_like(self.k)
        self.pos = torch.full((self.size,), -1, device=device, dtype=torch.long)
        self.max_len = self.size

    def write(self, k: torch.Tensor, v: torch.Tensor, pos_idx: torch.Tensor):
        slot = torch.remainder(pos_idx, self.size)
        self.k.index_copy_(1, slot, k.transpose(0, 1).to(self.k.dtype))
        self.v.index_copy_(1, slot, v.transpose(0, 1).to(self.v.dtype))
        self.pos.index_copy_(0, slot, pos_idx)

    def kv(self, n: int):
        """The whole ring (`pos` tells the attention which position each slot holds)."""
        return self.k, self.v


def attention(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, q_pos: torch.Tensor,
              kv_len: int, scale: float, window: int = 0,
              key_pos: Optional[torch.Tensor] = None, spans=None,
              sinks: Optional[torch.Tensor] = None, chunk: int = 0) -> torch.Tensor:
    """
    q: (L, H, D) ; k, v: caches (Hkv, slots, D) ; q_pos: (L,) positions of the queries.
    Without `key_pos` slot i holds position i and slots [0, kv_len) are used.  With
    `key_pos` (ring cache) every slot is used and `key_pos` gives its position (-1 = empty).
    `window` > 0 limits to the last `window` positions (sliding window, llama.cpp
    "standard" semantics).  `spans`: position ranges [lo, hi) read bidirectionally
    (an image's tokens; all of them must be written in this call).  `sinks` (H,): one extra
    logit per head in the softmax denominator, without a value (gpt-oss).  `chunk` > 0: keys only
    from the query's own block of `chunk` positions (Llama 4 chunked attention).  Returns (L, H, D).
    """
    L, H, D = q.shape
    if (L > 1 and key_pos is None and window <= 0 and chunk <= 0 and not spans and sinks is None
            and not mehrere_aufnahme()):
        # Einlesen: die Token stehen auf den letzten L Plätzen des Caches – unten rechts kausal, ohne
        # ausgeschriebene Maske (der Kern überspringt verdeckte Kacheln). Nicht in einer Graph-Aufnahme
        # mehrerer Token: dort ist kv_len aufgerundet.
        return _kausal_rechts(q, k[:, :kv_len].unsqueeze(0), v[:, :kv_len].unsqueeze(0), scale)
    if key_pos is None:
        kh = k[:, :kv_len].unsqueeze(0)
        vh = v[:, :kv_len].unsqueeze(0)
        key_pos = torch.arange(kv_len, device=q.device)
        ring = False
    else:
        kh, vh = k.unsqueeze(0), v.unsqueeze(0)
        ring = True
    dist = q_pos.unsqueeze(1) - key_pos.unsqueeze(0)   # (L, slots)
    mask = dist >= 0
    if ring:
        mask = mask & (key_pos >= 0).unsqueeze(0)      # empty slots
    if window > 0:
        mask = mask & (dist < window)
    if chunk > 0:
        mask = mask & (torch.div(q_pos, chunk, rounding_mode="floor").unsqueeze(1)
                       == torch.div(key_pos, chunk, rounding_mode="floor").unsqueeze(0))
    for lo, hi in spans or ():                         # image tokens see each other both ways
        drin_q = (q_pos >= lo) & (q_pos < hi)
        drin_k = (key_pos >= lo) & (key_pos < hi)
        mask = mask | (drin_q.unsqueeze(1) & drin_k.unsqueeze(0))
    if L == 1:
        return _attention_step(q, kh[0], vh[0], mask, scale, sinks)
    if sinks is not None:
        return _attention_sinks(q, kh[0], vh[0], mask, scale, sinks)
    qh = q.transpose(0, 1).unsqueeze(0).to(k.dtype)   # (1,H,L,D)
    out = F.scaled_dot_product_attention(qh, kh, vh, attn_mask=mask, scale=scale, enable_gqa=(kh.shape[1] != H))
    return out[0].transpose(0, 1)


_CUDNN_ATTENTION = {"geht": None}      # None: noch nicht versucht, False: nicht verfügbar


def cudnn_zuerst():
    """SDPA mit cuDNN zuerst, die übrigen Kerne als Reserve. `sdpa_kernel` stellt die Kerne für den ganzen
    Prozess um, nicht je Faden: „nur cuDNN“ ließ gleichzeitig rechnende Fäden (Musik in fp32, Köpfe über 128)
    ohne Kern („No available kernel“), und zwei sich überkreuzende Umschaltungen hinterließen „nur cuDNN“
    dauerhaft. Mit Reihenfolge statt Ausschluss bleibt jeder Kern erlaubt."""
    from torch.nn.attention import SDPBackend, sdpa_kernel
    return sdpa_kernel([SDPBackend.CUDNN_ATTENTION, SDPBackend.FLASH_ATTENTION,
                        SDPBackend.EFFICIENT_ATTENTION, SDPBackend.MATH], set_priority=True)
CUDNN_KOPF_BIS = 128                   # cuDNN-SDPA kann Köpfe bis zu dieser Größe (gemessen: 256 nicht)
CUDNN_KOPIE_BIS = 256 * 2**20          # vervielfachte K/V höchstens so groß (langer Kontext, knapper VRAM)


def _kausal_rechts(q: torch.Tensor, kh: torch.Tensor, vh: torch.Tensor, scale: float) -> torch.Tensor:
    """q (L, H, D), kh/vh (1, Hkv, S, D) mit den Abfragen auf den letzten L Plätzen. cuDNN-SDPA (auf Blackwell
    gemessen 2–4× schneller als mit Maske; ohne GQA – KV-Köpfe werden vervielfacht), sonst der effiziente Kern
    mit derselben Kausalität."""
    from torch.nn.attention.bias import causal_lower_right
    L, H, D = q.shape
    Hkv, S = kh.shape[1], kh.shape[2]
    qh = q.transpose(0, 1).unsqueeze(0).to(kh.dtype)                       # (1, H, L, D)
    kausal = causal_lower_right(L, S)
    kopie = 0 if Hkv == H else 2 * H * S * kh.shape[-1] * kh.element_size()
    if (q.is_cuda and max(D, vh.shape[-1]) <= CUDNN_KOPF_BIS and kh.shape[-1] == vh.shape[-1]
            and kopie <= CUDNN_KOPIE_BIS and _CUDNN_ATTENTION["geht"] is not False):
        kk = kh if Hkv == H else kh.repeat_interleave(H // Hkv, dim=1)
        vv = vh if Hkv == H else vh.repeat_interleave(H // Hkv, dim=1)
        try:
            with cudnn_zuerst():
                out = F.scaled_dot_product_attention(qh, kk, vv, attn_mask=kausal, scale=scale)
            _CUDNN_ATTENTION["geht"] = True
            return out[0].transpose(0, 1)
        except RuntimeError:
            if _CUDNN_ATTENTION["geht"] is None:
                dbg.event("cuDNN-Attention nicht verfügbar – Einlesen über den effizienten Kern")
            _CUDNN_ATTENTION["geht"] = False
    out = F.scaled_dot_product_attention(qh, kh, vh, attn_mask=kausal, scale=scale, enable_gqa=(Hkv != H))
    return out[0].transpose(0, 1)


def _attention_step(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, mask: torch.Tensor,
                    scale: float, sinks: Optional[torch.Tensor] = None) -> torch.Tensor:
    """One query token: the query heads of a KV group as rows of one batched matmul.  SDPA
    does not split long contexts for a single query and reaches a fraction of the memory
    bandwidth there (measured 3-5x slower from 9k positions on)."""
    H, D = q.shape[1], q.shape[2]
    Hkv = k.shape[0]
    qg = q[0].to(k.dtype).view(Hkv, H // Hkv, D)
    s = torch.bmm(qg, k.transpose(1, 2)).float() * scale                  # (Hkv, G, S)
    s = s.masked_fill(~mask, float("-inf"))
    if sinks is not None:                                                  # extra column, dropped after
        p = torch.softmax(torch.cat([s, sinks.float().view(Hkv, H // Hkv, 1)], dim=-1), dim=-1)[..., :-1]
    else:
        p = torch.softmax(s, dim=-1)
    return torch.bmm(p.to(v.dtype), v).view(1, H, v.shape[-1])


def _attention_sinks(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, mask: torch.Tensor,
                     scale: float, sinks: torch.Tensor, rows: int = 256) -> torch.Tensor:
    """Several queries with sinks (SDPA has no extra softmax column): explicit, in blocks of
    `rows` queries.  k, v (Hkv, S, D); mask (L, S)."""
    L, H, _ = q.shape
    Hkv = k.shape[0]
    kh = k.repeat_interleave(H // Hkv, dim=0)                              # (H, S, D)
    vh = v.repeat_interleave(H // Hkv, dim=0)
    senke = sinks.float().view(H, 1, 1)
    out = torch.empty(L, H, v.shape[-1], device=q.device, dtype=torch.float32)
    for r in range(0, L, rows):
        s = (q[r:r + rows].transpose(0, 1).to(k.dtype) @ kh.transpose(1, 2)).float() * scale   # (H, l, S)
        s = s.masked_fill(~mask[r:r + rows].unsqueeze(0), float("-inf"))
        p = torch.softmax(torch.cat([s, senke.expand(H, s.shape[1], 1)], dim=-1), dim=-1)[..., :-1]
        out[r:r + rows] = (p.to(v.dtype) @ vh).float().transpose(0, 1)
    return out


def attention_softcap(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, q_pos: torch.Tensor,
                      kv_len: int, scale: float, cap: float, window: int = 0,
                      rows: int = 256) -> torch.Tensor:
    """Like `attention` (full cache), with scores = cap * tanh(scores / cap) before the
    softmax (Gemma 2).  SDPA cannot do that, so it is computed explicitly – in blocks of
    `rows` queries so long prompts do not need an L x kv_len x H score tensor at once."""
    L, H, D = q.shape
    Hkv = k.shape[0]
    kh = k[:, :kv_len].float().repeat_interleave(H // Hkv, dim=0)       # (H, S, D)
    vh = v[:, :kv_len].float().repeat_interleave(H // Hkv, dim=0)
    key_pos = torch.arange(kv_len, device=q.device)
    out = torch.empty(L, H, vh.shape[-1], device=q.device, dtype=torch.float32)
    for r in range(0, L, rows):
        qr = q[r:r + rows].float().transpose(0, 1)                       # (H, l, D)
        s = (qr @ kh.transpose(1, 2)) * scale
        s = torch.tanh(s / cap) * cap
        dist = q_pos[r:r + rows].unsqueeze(1) - key_pos.unsqueeze(0)
        mask = dist >= 0
        if window > 0:
            mask = mask & (dist < window)
        s = s.masked_fill(~mask.unsqueeze(0), float("-inf"))
        out[r:r + rows] = (torch.softmax(s, dim=-1) @ vh).transpose(0, 1)
    return out


def swiglu_mlp(x: torch.Tensor, gate: Linear, up: Linear, down: Linear) -> torch.Tensor:
    return down(F.silu(gate(x)) * up(x))


# Held while a CUDA graph is captured.  Other GPU users in the same process
# (embeddings) take it around their GPU work: device-wide calls from another
# thread (synchronize, empty_cache, moving a model) would invalidate the capture.
GPU_SPERRE = threading.RLock()


MEHRERE_BUCKET = 2048                 # several tokens: fewer captures (each costs several passes)
_MEHRERE = threading.local()          # set while several tokens are warmed up and captured


def _host_lesen_finden():
    """Dispatch mode that fails on reads from the GPU to the host (item/int, nonzero, copies to the CPU).
    Unlike torch.cuda.set_sync_debug_mode it only applies to the calling thread – the global mode made
    synchronizing calls in other threads fail too (seen: the TensorRT build next to the image describer).
    It runs in the warm-up, not in the capture, so an op may finish before it is reported."""
    from torch.utils._python_dispatch import TorchDispatchMode
    from torch.utils._pytree import tree_leaves
    aten = torch.ops.aten
    immer = {aten.item.default, aten._local_scalar_dense.default, aten.nonzero.default,
             aten.masked_select.default, aten.is_nonzero.default, aten.equal.default}

    class HostLesen(TorchDispatchMode):
        def __torch_dispatch__(self, func, types, args=(), kwargs=None):
            if func in immer:
                raise RuntimeError(f"called a synchronizing CUDA operation ({func})")
            erg = func(*args, **(kwargs or {}))
            rein = [t for t in tree_leaves((args, kwargs)) if isinstance(t, torch.Tensor)]
            raus = [t for t in tree_leaves(erg) if isinstance(t, torch.Tensor)]
            if any(t.is_cuda for t in rein) and any(t.device.type == "cpu" for t in raus):
                raise RuntimeError(f"called a synchronizing CUDA operation ({func} to the CPU)")
            return erg
    return HostLesen()
_ZEILEN = threading.local()           # set while proposed tokens are checked (see zeilen_merken)


def zeilen_merken() -> bool:
    """Proposed tokens are being checked: recurrent layers keep their per-token inputs, so the
    state after any row can be rebuilt without reading the tokens again (`nachholen`)."""
    return getattr(_ZEILEN, "an", False)


class ZeilenMerken:
    """Context: `zeilen_merken()` is true inside."""

    def __enter__(self):
        self._vorher = zeilen_merken()
        _ZEILEN.an = True

    def __exit__(self, *a):
        _ZEILEN.an = self._vorher


def fenster_nachholen(fenster: torch.Tensor, zeilen: torch.Tensor, n: int) -> None:
    """Carried convolution window after the first n of `zeilen` (from the window before them)."""
    voll = torch.cat([fenster, zeilen[:n]], dim=0)
    fenster.copy_(voll[-fenster.shape[0]:])


def delta_nachholen(S: torch.Tensor, zerfall: torch.Tensor, k: torch.Tensor, v: torch.Tensor,
                    beta: torch.Tensor, n: int) -> None:
    """Delta rule over the first n rows - the same steps as the token loops of DeltaNet / KDA."""
    for t in range(n):
        S.mul_(zerfall[t])
        kt = k[t].unsqueeze(1)
        delta = (v[t].unsqueeze(1) - torch.bmm(kt, S)) * beta[t]
        S.baddbmm_(kt.transpose(1, 2), delta)


def mischer_nachholen(mischer: list, L: int, n: int) -> bool:
    """Rebuilds the state of every recurrent layer after row n of the last check with L rows
    (state before the check already restored); False if a layer has no rows of that check."""
    if not mischer or any(L not in m._merk for m in mischer):
        return False
    for m in mischer:
        m.nachholen(L, n)
    return True


def mehrere_aufnahme() -> bool:
    """Several tokens are being prepared for a CUDA graph: layers take their host-read-free way."""
    return getattr(_MEHRERE, "an", False)
_AUFNAHME_STROM: Dict = {}


def aufnahme_strom(device) -> "torch.cuda.Stream":
    """One side stream per device for all graph captures: cuBLAS keeps a workspace for every
    stream it saw (about 32 MiB each, never freed) - a new stream per capture let that grow
    with every KV bucket."""
    idx = torch.device(device).index
    idx = torch.cuda.current_device() if idx is None else idx
    if idx not in _AUFNAHME_STROM:
        _AUFNAHME_STROM[idx] = torch.cuda.Stream(device=idx)
    return _AUFNAHME_STROM[idx]


class GraphDecoder:
    """
    Runs single-token decode steps through captured CUDA graphs, one graph per
    KV-length bucket, eliminating per-kernel launch overhead.  The model must
    expose `_run(tok, pos_idx, kv_len)` using only static buffers / in-place
    state updates, plus `snapshot_state()` / `restore_state()`.
    `mehrere` does the same for a few tokens with the logits of every row (checking
    proposed tokens); one graph per (token count, bucket).  With `haupt` (another decoder)
    the graphs and the memory pool are kept there under `name`, so dropping that decoder's
    graphs drops these too.
    """

    def __init__(self, model, n_ctx: int, bucket: int = 256, haupt: Optional["GraphDecoder"] = None,
                 name: str = ""):
        self.model = model
        self.n_ctx = n_ctx
        self.bucket = bucket
        dev = model.device
        self.tok = torch.zeros(1, dtype=torch.long, device=dev)
        self.pos = torch.zeros(1, dtype=torch.long, device=dev)
        self._haupt, self._name = haupt, name
        self._graphs: Dict = {}           # kv_len -> graph; (L, kv_len) for several tokens
        self._pool = None
        self.pool_mehr = None             # own pool of the several-token graphs: dropped with them
        self._puffer: Dict[int, tuple] = {}
        self.mehrere_aus = False          # capturing several tokens failed for this model
        self.aufnahmen = 0                # captures so far (a timed step that captured is not typical)

    @property
    def graphs(self) -> Dict:
        return self._haupt.graphs if self._haupt is not None else self._graphs

    @property
    def pool(self):
        return self._haupt.pool if self._haupt is not None else self._pool

    @pool.setter
    def pool(self, wert):
        if self._haupt is not None:
            self._haupt.pool = wert
        else:
            self._pool = wert

    def _schluessel(self, kv_len: int, L: int = 1):
        if self._name:
            return (self._name, L, kv_len)
        return kv_len if L == 1 else (L, kv_len)

    def _mehrfach(self, key) -> bool:
        """Key of one of this decoder's several-token graphs."""
        if not isinstance(key, tuple):
            return False
        if self._name:
            return len(key) == 3 and key[0] == self._name and key[1] > 1
        return len(key) == 2

    def _capture(self, kv_len: int, L: int = 1):
        with GPU_SPERRE:
            self._capture_exklusiv(kv_len, L)

    def _capture_exklusiv(self, kv_len: int, L: int = 1):
        tok, pos = (self.tok, self.pos) if L == 1 else self._puffer[L]
        snap = self.model.snapshot_state()
        self.model.alle_logits = L > 1
        _MEHRERE.an = _ZEILEN.an = L > 1
        try:
            s = aufnahme_strom(self.model.device)
            s.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(s):
                for i in range(2 if L == 1 else 1):      # warm-up (cuBLAS workspaces etc.)
                    if L > 1:
                        # a host read would break the capture and leave torch half inside it
                        # (stream and pool not switched back): find it here, without capturing
                        with _host_lesen_finden():
                            self.model._run(tok, pos, kv_len)
                    else:
                        self.model._run(tok, pos, kv_len)
            torch.cuda.current_stream().wait_stream(s)
            g = torch.cuda.CUDAGraph()
            # thread_local: other threads (e.g. embeddings) may use the GPU during capture
            with torch.cuda.graph(g, pool=self.pool if L == 1 else self.pool_mehr, stream=s,
                                  capture_error_mode="thread_local"):
                out = self.model._run(tok, pos, kv_len)
        finally:
            self.model.alle_logits = False
            _MEHRERE.an = _ZEILEN.an = False
            self.model.restore_state(snap)
        if L == 1 and self.pool is None:
            self.pool = g.pool()
        elif L > 1 and self.pool_mehr is None:
            self.pool_mehr = g.pool()
        self.graphs[self._schluessel(kv_len, L)] = (g, out)
        self.aufnahmen += 1

    def leeren(self) -> None:
        """Drops all captured graphs and their memory (caches moved, model offloaded)."""
        self.graphs.clear()
        self.pool = None
        self.pool_mehr = None

    def _kv_len(self, ende: int, bucket: int = 0) -> int:
        bucket = bucket or self.bucket
        return min(-(-ende // bucket) * bucket, self.n_ctx)

    def __call__(self, token: int, pos: int) -> torch.Tensor:
        kv_len = self._kv_len(pos + 1)
        self.tok.fill_(token)
        self.pos.fill_(pos)
        key = self._schluessel(kv_len)
        if key not in self.graphs:
            self._capture(kv_len)
        g, out = self.graphs[key]
        g.replay()
        return out

    def mehrere(self, tokens: List[int], pos: int) -> Optional[torch.Tensor]:
        """Logits (L, V) of tokens at slots pos..; None if the model cannot be captured with
        several tokens (the caller runs it without a graph then)."""
        if self.mehrere_aus:
            return None
        L = len(tokens)
        kv_len = self._kv_len(pos + L, MEHRERE_BUCKET)     # several sizes: fewer captures, longer buckets
        dev = self.tok.device
        if L not in self._puffer:
            self._puffer[L] = (torch.zeros(L, dtype=torch.long, device=dev),
                               torch.zeros(L, dtype=torch.long, device=dev))
        tok, pidx = self._puffer[L]
        tok.copy_(torch.tensor(tokens, dtype=torch.long))
        pidx.copy_(torch.arange(pos, pos + L, dtype=torch.long))
        vorher = getattr(self.model, "vor_graph", None)      # host-side inputs the model's forward() fills
        if vorher is not None:
            vorher(tokens)
        key = self._schluessel(kv_len, L)
        if key not in self.graphs:
            # only the current bucket keeps several-token graphs, in a pool of their own that
            # goes with them: each holds its (L, V) logits and activations (measured: 48 graphs
            # over 12k positions in the shared pool = 1 GiB that never came back).  Single-token
            # graphs captured while that pool lived can point into it (library workspaces of a
            # capture land in its pool): they go too, else their replay reads freed memory
            # (measured on Gemma 4 12B: illegal memory access after a bucket change)
            if any(self._mehrfach(k) and k[-1] != kv_len for k in self.graphs):
                torch.cuda.synchronize()
                self.leeren()
            try:
                self._capture(kv_len, L)
            except Exception:
                self.mehrere_aus = True
                return None
        g, out = self.graphs[key]
        g.replay()
        return out
