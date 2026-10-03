"""
Dequantisation of GGML block formats into torch tensors.

All functions take the raw block bytes as a uint8 tensor (any device) and
return a float32 tensor with `n_blocks * block_size` elements.  The bit
layouts follow ggml-quants.c exactly.  Everything is vectorised, so it runs
on CUDA as well as on the CPU.
"""
from __future__ import annotations

import warnings
from functools import partial
from typing import Callable, Dict

import torch

from .gguf import GGML_TYPES, GGUFError

_LOW = 0x0F


def _f16(b: torch.Tensor) -> torch.Tensor:
    """Interpret the last dimension (2 bytes) as one float16 value -> float32."""
    return b.contiguous().view(torch.float16).float()


def _blocks(raw: torch.Tensor, type_size: int) -> torch.Tensor:
    if raw.dtype != torch.uint8 or raw.dim() != 1 or raw.numel() % type_size:
        raise GGUFError("raw tensor bytes do not match the block size")
    return raw.view(-1, type_size)


# --- simple formats ---------------------------------------------------------

def deq_f32(raw):
    return raw.contiguous().view(torch.float32)


def deq_f16(raw):
    return raw.contiguous().view(torch.float16).float()


def deq_bf16(raw):
    return raw.contiguous().view(torch.bfloat16).float()


def deq_q8_0(raw):
    b = _blocks(raw, 34)
    d = _f16(b[:, :2])                       # (nb,1)
    q = b[:, 2:].contiguous().view(torch.int8).float()
    return (d * q).reshape(-1)


def _nibbles(qs: torch.Tensor):
    """qs (nb,16) uint8 -> (nb,32) int16 with low nibbles first then high."""
    lo = (qs & _LOW).to(torch.int16)
    hi = (qs >> 4).to(torch.int16)
    return torch.cat([lo, hi], dim=1)


def deq_q4_0(raw):
    b = _blocks(raw, 18)
    d = _f16(b[:, :2])
    q = _nibbles(b[:, 2:18]).float() - 8.0
    return (d * q).reshape(-1)


def deq_q4_1(raw):
    b = _blocks(raw, 20)
    d = _f16(b[:, :2]); m = _f16(b[:, 2:4])
    q = _nibbles(b[:, 4:20]).float()
    return (d * q + m).reshape(-1)


def _q5_high_bits(qh_bytes: torch.Tensor) -> torch.Tensor:
    """qh (nb,4) uint8 -> (nb,32) int16 with the 5th bit (value 16) for each element."""
    qh = qh_bytes.contiguous().view(torch.int32)          # (nb,1)
    j = torch.arange(16, device=qh.device, dtype=torch.int32)
    lo = ((qh >> j) & 1) << 4                             # elements 0..15
    hi = ((qh >> (j + 16)) & 1) << 4                      # elements 16..31
    return torch.cat([lo, hi], dim=1).to(torch.int16)


def deq_q5_0(raw):
    b = _blocks(raw, 22)
    d = _f16(b[:, :2])
    q = (_nibbles(b[:, 6:22]) | _q5_high_bits(b[:, 2:6])).float() - 16.0
    return (d * q).reshape(-1)


def deq_q5_1(raw):
    b = _blocks(raw, 24)
    d = _f16(b[:, :2]); m = _f16(b[:, 2:4])
    q = (_nibbles(b[:, 8:24]) | _q5_high_bits(b[:, 4:8])).float()
    return (d * q + m).reshape(-1)


# --- K-quants ---------------------------------------------------------------

def _scale_min_k4(scales: torch.Tensor):
    """scales (nb,12) uint8 -> (sc, m) each (nb,8) float, per ggml get_scale_min_k4."""
    s = scales.to(torch.int16)
    sc = torch.empty(s.shape[0], 8, dtype=torch.int16, device=s.device)
    mn = torch.empty_like(sc)
    sc[:, :4] = s[:, 0:4] & 63
    mn[:, :4] = s[:, 4:8] & 63
    sc[:, 4:] = (s[:, 8:12] & _LOW) | ((s[:, 0:4] >> 6) << 4)
    mn[:, 4:] = (s[:, 8:12] >> 4) | ((s[:, 4:8] >> 6) << 4)
    return sc.float(), mn.float()


def deq_q4_k(raw):
    b = _blocks(raw, 144)
    nb = b.shape[0]
    d = _f16(b[:, :2]); dmin = _f16(b[:, 2:4])
    sc, mn = _scale_min_k4(b[:, 4:16])
    qs = b[:, 16:144].reshape(nb, 4, 32)                 # 4 groups of 32 bytes
    lo = (qs & _LOW).float(); hi = (qs >> 4).float()
    q = torch.stack([lo, hi], dim=2).reshape(nb, 8, 32)   # sub-block 2i = low, 2i+1 = high
    y = (d * sc).unsqueeze(-1) * q - (dmin * mn).unsqueeze(-1)
    return y.reshape(-1)


def deq_q5_k(raw):
    b = _blocks(raw, 176)
    nb = b.shape[0]
    d = _f16(b[:, :2]); dmin = _f16(b[:, 2:4])
    sc, mn = _scale_min_k4(b[:, 4:16])
    qh = b[:, 16:48].to(torch.int16)                      # (nb,32)
    qs = b[:, 48:176].reshape(nb, 4, 32).to(torch.int16)
    lo = qs & _LOW; hi = qs >> 4
    shifts = torch.arange(4, device=b.device, dtype=torch.int16) * 2   # u1 = 1<<(2i), u2 = 2<<(2i)
    qh_e = qh.unsqueeze(1)                                # (nb,1,32)
    lo = lo | (((qh_e >> shifts.view(1, 4, 1)) & 1) << 4)
    hi = hi | (((qh_e >> (shifts.view(1, 4, 1) + 1)) & 1) << 4)
    q = torch.stack([lo, hi], dim=2).reshape(nb, 8, 32).float()
    y = (d * sc).unsqueeze(-1) * q - (dmin * mn).unsqueeze(-1)
    return y.reshape(-1)


def deq_q6_k(raw):
    b = _blocks(raw, 210)
    nb = b.shape[0]
    ql = b[:, 0:128].to(torch.int16)
    qh = b[:, 128:192].to(torch.int16)
    sc = b[:, 192:208].contiguous().view(torch.int8).float()   # (nb,16)
    d = _f16(b[:, 208:210])                                    # (nb,1)
    out = []
    for n in range(2):                                         # two halves of 128 elements
        ql_n = ql[:, n * 64:(n + 1) * 64]
        qh_n = qh[:, n * 32:(n + 1) * 32]
        sc_n = sc[:, n * 8:(n + 1) * 8]                        # (nb,8)
        q1 = (ql_n[:, :32] & _LOW) | ((qh_n & 3) << 4)
        q2 = (ql_n[:, 32:] & _LOW) | (((qh_n >> 2) & 3) << 4)
        q3 = (ql_n[:, :32] >> 4) | (((qh_n >> 4) & 3) << 4)
        q4 = (ql_n[:, 32:] >> 4) | (((qh_n >> 6) & 3) << 4)
        q = torch.stack([q1, q2, q3, q4], dim=1).float() - 32.0          # (nb,4,32)
        s = sc_n.reshape(nb, 4, 2).repeat_interleave(16, dim=2)          # scale[is+2c] for l<16 / l>=16
        out.append((d.unsqueeze(-1) * s * q).reshape(nb, 128))
    return torch.cat(out, dim=1).reshape(-1)


# --- IQ formats -------------------------------------------------------------
# Grid entry and sign pattern of a group of 8 values are merged into one table row
# (value = grid byte * sign), so a group costs one lookup.  The tables are built once
# per device and dtype; the formats can return bf16 directly (`dtype`).

_TABLES: dict = {}


def _gitter(werte, n: int) -> torch.Tensor:
    """Packed grid entries (n bytes each, lowest first) -> (n_grid, n) float32."""
    g = torch.tensor([v - (1 << 64) if v >= 1 << 63 else v for v in werte], dtype=torch.int64)
    return g.view(torch.uint8).view(-1, 8)[:, :n].float() if n == 8 else         torch.stack([(g >> (8 * j)) & 0xFF for j in range(n)], dim=1).float()


def _vorzeichen(n_bits: int) -> torch.Tensor:
    """(2^n_bits, 8) of +-1: bit j set -> element j negative.  7 stored bits get the parity
    as 8th bit (ksigns_iq2xs)."""
    s = torch.arange(1 << n_bits)
    if n_bits == 7:
        s = s | ((torch.stack([(s >> j) & 1 for j in range(7)]).sum(0) & 1) << 7)
    return 1.0 - 2.0 * ((s.unsqueeze(1) >> torch.arange(8)) & 1).float()


def _nibble_paare(werte) -> torch.Tensor:
    """(256, 2): the values of the low and the high nibble of a byte."""
    kv = torch.tensor(werte, dtype=torch.float32)
    b = torch.arange(256)
    return torch.stack([kv[b & 0x0F], kv[b >> 4]], dim=1)


def _table(name: str, device, dtype) -> torch.Tensor:
    """IQ2_*: (n_grid * n_sign, 8), row = g * n_sign + s.  *_GRID: (n_grid, 4 or 8).
    SIGNS7/SIGNS8: (n_sign, 8) of +-1.  IQ4_NL/MXFP4: nibble pairs (256, 2)."""
    key = (name, str(device), dtype)
    if key not in _TABLES:
        from . import iqtabellen as it
        if name == "IQ4_NL":
            t = _nibble_paare(it.KVALUES_IQ4NL)
        elif name == "MXFP4":
            t = _nibble_paare(KVALUES_MXFP4)
        elif name in ("IQ2_XXS", "IQ2_XS", "IQ2_S"):
            grid = _gitter({"IQ2_XXS": it.IQ2XXS_GRID, "IQ2_XS": it.IQ2XS_GRID, "IQ2_S": it.IQ2S_GRID}[name], 8)
            sign = _vorzeichen(8 if name == "IQ2_S" else 7)
            t = (grid.unsqueeze(1) * sign.unsqueeze(0)).reshape(-1, 8)
        elif name == "IQ3_XXS_GRID":
            t = _gitter(it.IQ3XXS_GRID, 4)
        elif name == "IQ3_S_GRID":
            t = _gitter(it.IQ3S_GRID, 4)
        elif name == "IQ1_S_GRID":
            t = _gitter(it.IQ1S_GRID, 8).to(torch.uint8).view(torch.int8).float()
        elif name in ("SIGNS7", "SIGNS8"):
            t = _vorzeichen(int(name[-1]))
        else:
            raise KeyError(name)
        _TABLES[key] = t.to(device=device, dtype=dtype).contiguous()
    return _TABLES[key]


_WORT = {4: torch.int32, 8: torch.int64, 16: torch.complex128}


def _lookup(t: torch.Tensor, idx: torch.Tensor) -> torch.Tensor:
    """t[idx] -> (idx.numel(), row length).  Rows are fetched as whole machine words of up
    to 16 bytes: gathering rows element by element is an order of magnitude slower."""
    rb = t.shape[1] * t.element_size()
    w = min(rb, 16)
    flat = t.view(_WORT[w]).reshape(-1)
    idx = idx.reshape(-1)
    if rb > w:
        idx = (idx.unsqueeze(1) * (rb // w) + torch.arange(rb // w, device=idx.device)).reshape(-1)
    return flat[idx].view(t.dtype).view(-1, t.shape[1])


def deq_iq2_xxs(raw, dtype=torch.float32):
    b = _blocks(raw, 66)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)                                   # (nb,1)
    q = b[:, 2:].reshape(nb, 8, 8)                                 # per 32 values: 4 grid bytes + uint32
    w = q[:, :, 4:].contiguous().view(torch.int32)                 # (nb,8,1)
    shifts = torch.arange(0, 28, 7, device=b.device, dtype=torch.int32)
    row = q[:, :, :4].long() * 128 + ((w >> shifts) & 127).long()   # (nb,8,4)
    vals = _lookup(_table("IQ2_XXS", b.device, dtype), row).view(nb, 8, 32)
    db = d.unsqueeze(-1) * (0.125 + 0.25 * ((w >> 28) & 15).to(dtype))
    return (vals * db).reshape(-1)


def deq_iq2_s(raw, dtype=torch.float32):
    b = _blocks(raw, 82)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    qs = b[:, 2:34].reshape(nb, 8, 4).long()
    signs = b[:, 34:66].reshape(nb, 8, 4).long()
    qh = b[:, 66:74].reshape(nb, 8, 1).long()
    sc = b[:, 74:82].to(dtype)                                     # (nb,8)
    shifts = torch.arange(0, 8, 2, device=b.device)
    row = (qs | (((qh >> shifts) & 3) << 8)) * 256 + signs          # (nb,8,4)
    vals = _lookup(_table("IQ2_S", b.device, dtype), row).view(nb, 8, 2, 16)
    scale = torch.stack([sc.remainder(16), torch.floor(sc / 16)], dim=2)   # low nibble: groups 0,1
    db = d.unsqueeze(-1) * (0.125 + 0.25 * scale)                  # (nb,8,2)
    return (vals * db.unsqueeze(-1)).reshape(-1)


def deq_iq4_nl(raw, dtype=torch.float32):
    b = _blocks(raw, 18)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    pairs = _lookup(_table("IQ4_NL", b.device, dtype), b[:, 2:18].long()).view(nb, 16, 2)
    return (pairs.transpose(1, 2) * d.unsqueeze(-1)).reshape(-1)   # low nibbles first, then high


def _u8(t: torch.Tensor, *shifts) -> torch.Tensor:
    """Shift amounts as a uint8 tensor on the device of t."""
    return torch.tensor(shifts, dtype=torch.uint8, device=t.device)


def deq_q2_k(raw, dtype=torch.float32):
    b = _blocks(raw, 84)
    nb = b.shape[0]
    sc, qs = b[:, :16], b[:, 16:80]
    d, dmin = _f16(b[:, 80:82]).to(dtype), _f16(b[:, 82:84]).to(dtype)
    dl = (d * (sc & 0x0F).to(dtype)).unsqueeze(-1)                 # (nb,16,1), 16 values each
    ml = (dmin * (sc >> 4).to(dtype)).unsqueeze(-1)
    q = ((qs.view(nb, 2, 1, 32) >> _u8(b, 0, 2, 4, 6).view(1, 1, 4, 1)) & 3).reshape(nb, 16, 16)
    return (dl * q.to(dtype) - ml).reshape(-1)


def deq_q3_k(raw, dtype=torch.float32):
    b = _blocks(raw, 110)
    nb = b.shape[0]
    hmask, qs, sc = b[:, :32], b[:, 32:96], b[:, 96:108]
    d = _f16(b[:, 108:110]).to(dtype)
    lo = (sc[:, :8].view(nb, 1, 8) >> _u8(b, 0, 4).view(1, 2, 1)).reshape(nb, 16) & 0x0F
    hi = (sc[:, 8:12].view(nb, 1, 4) >> _u8(b, 0, 2, 4, 6).view(1, 4, 1)).reshape(nb, 16) & 3
    dl = (d * ((lo | (hi << 4)).to(torch.int16) - 32).to(dtype)).unsqueeze(-1)       # (nb,16,1)
    ql = ((qs.view(nb, 2, 1, 32) >> _u8(b, 0, 2, 4, 6).view(1, 1, 4, 1)) & 3).reshape(nb, 16, 16)
    qh = ((hmask.view(nb, 1, 1, 32) >> torch.arange(8, dtype=torch.uint8, device=b.device).view(1, 1, 8, 1))
          & 1).reshape(nb, 16, 16)
    q = ql.to(torch.int16) - ((qh ^ 1).to(torch.int16) << 2)    # high bit clear: value - 4
    return (dl * q.to(dtype)).reshape(-1)


def deq_iq2_xs(raw, dtype=torch.float32):
    b = _blocks(raw, 74)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    q = b[:, 2:66].contiguous().view(torch.int16).to(torch.int32) & 0xFFFF       # (nb,32)
    row = (q & 511).long() * 128 + (q >> 9).long()
    vals = _lookup(_table("IQ2_XS", b.device, dtype), row).view(nb, 16, 16)
    sc = (b[:, 66:74].view(nb, 8, 1) >> _u8(b, 0, 4).view(1, 1, 2)).reshape(nb, 16) & 0x0F
    db = d * (0.125 + 0.25 * sc.to(dtype))                         # (nb,16), 16 values each
    return (vals * db.unsqueeze(-1)).reshape(-1)


def deq_iq3_xxs(raw, dtype=torch.float32):
    b = _blocks(raw, 98)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    grid = _lookup(_table("IQ3_XXS_GRID", b.device, dtype), b[:, 2:66].long()).view(nb, 8, 4, 8)
    aux = b[:, 66:98].contiguous().view(torch.int32).view(nb, 8, 1)                 # per 32 values
    s7 = (aux >> torch.arange(0, 28, 7, device=b.device, dtype=torch.int32)) & 127   # (nb,8,4)
    signs = _lookup(_table("SIGNS7", b.device, dtype), s7.long()).view(nb, 8, 4, 8)
    db = d.unsqueeze(-1) * (0.25 + 0.5 * ((aux >> 28) & 15).to(dtype))             # (nb,8,1)
    return (grid * signs * db.unsqueeze(-1)).reshape(-1)


def deq_iq3_s(raw, dtype=torch.float32):
    b = _blocks(raw, 110)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    qh = (b[:, 66:74].view(nb, 8, 1) >> torch.arange(8, dtype=torch.uint8, device=b.device)) & 1
    idx = b[:, 2:66].long() | (qh.reshape(nb, 64).long() << 8)
    grid = _lookup(_table("IQ3_S_GRID", b.device, dtype), idx).view(nb, 8, 4, 8)
    signs = _lookup(_table("SIGNS8", b.device, dtype), b[:, 74:106].long()).view(nb, 8, 4, 8)
    sc = (b[:, 106:110].view(nb, 4, 1) >> _u8(b, 0, 4).view(1, 1, 2)).reshape(nb, 8) & 0x0F
    db = d * (1 + 2 * sc.to(dtype))                                # (nb,8), 32 values each
    return (grid * signs * db.view(nb, 8, 1, 1)).reshape(-1)


IQ1_DELTA = 0.125


def deq_iq1_s(raw, dtype=torch.float32):
    b = _blocks(raw, 50)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    qh = b[:, 34:50].contiguous().view(torch.int16).to(torch.int32) & 0xFFFF      # (nb,8)
    idx = b[:, 2:34].long().view(nb, 8, 4) | \
        (((qh.unsqueeze(-1) >> torch.arange(0, 12, 3, device=b.device, dtype=torch.int32)) & 7).long() << 8)
    grid = _lookup(_table("IQ1_S_GRID", b.device, dtype), idx).view(nb, 8, 32)
    dl = d * (2 * ((qh >> 12) & 7) + 1).to(dtype)                  # (nb,8)
    delta = torch.where((qh & 0x8000) != 0, -IQ1_DELTA, IQ1_DELTA).to(dtype)
    return (dl.unsqueeze(-1) * (grid + delta.unsqueeze(-1))).reshape(-1)


def deq_iq1_m(raw, dtype=torch.float32):
    b = _blocks(raw, 56)
    nb = b.shape[0]
    sc = b[:, 48:56].contiguous().view(torch.int16).to(torch.int32) & 0xFFFF      # (nb,4)
    d16 = ((sc[:, 0] >> 12) | ((sc[:, 1] >> 8) & 0xF0) | ((sc[:, 2] >> 4) & 0xF00) | (sc[:, 3] & 0xF000))
    d = d16.to(torch.int16).view(torch.float16).float().to(dtype).view(nb, 1)
    s3 = (sc.unsqueeze(-1) >> torch.arange(0, 12, 3, device=b.device, dtype=torch.int32)) & 7  # (nb,4,4)
    dl = d * (2 * s3.reshape(nb, 16) + 1).to(dtype)               # (nb,16), 16 values each
    nib = (b[:, 32:48].view(nb, 16, 1) >> _u8(b, 0, 4).view(1, 1, 2)).reshape(nb, 32) & 0x0F
    idx = b[:, :32].long() | ((nib & 7).long() << 8)
    grid = _lookup(_table("IQ1_S_GRID", b.device, dtype), idx).view(nb, 32, 8)
    delta = torch.where((nib & 8) != 0, -IQ1_DELTA, IQ1_DELTA).to(dtype).unsqueeze(-1)
    return ((grid + delta).view(nb, 16, 16) * dl.unsqueeze(-1)).reshape(-1)


def deq_iq4_xs(raw, dtype=torch.float32):
    b = _blocks(raw, 136)
    nb = b.shape[0]
    d = _f16(b[:, :2]).to(dtype)
    sh = b[:, 2:4].contiguous().view(torch.int16).to(torch.int32) & 0xFFFF       # (nb,1)
    sl = (b[:, 4:8].view(nb, 4, 1) >> _u8(b, 0, 4).view(1, 1, 2)).reshape(nb, 8) & 0x0F
    hi = (sh >> torch.arange(0, 16, 2, device=b.device, dtype=torch.int32)) & 3                # (nb,8)
    dl = d * ((sl.to(torch.int32) | (hi << 4)) - 32).to(dtype)    # (nb,8), 32 values each
    pairs = _lookup(_table("IQ4_NL", b.device, dtype), b[:, 8:136].long()).view(nb, 8, 16, 2)
    return (pairs.transpose(2, 3) * dl.view(nb, 8, 1, 1)).reshape(-1)   # low nibbles first


# MXFP4: e2m1 values (doubled) with a shared power-of-two scale per 32 values (E8M0)
KVALUES_MXFP4 = (0, 1, 2, 3, 4, 6, 8, 12, 0, -1, -2, -3, -4, -6, -8, -12)


def deq_mxfp4(raw, dtype=torch.float32):
    b = _blocks(raw, 17)
    nb = b.shape[0]
    e = b[:, :1].to(torch.int32)
    # 2^(e-128) as float32 bits, as ggml's E8M0_TO_FP32_HALF (subnormal for e < 2)
    bits = torch.where(e < 2, torch.full_like(e, 0x00200000) << e, (e - 1) << 23)
    d = bits.view(torch.float32).to(dtype)                         # (nb,1)
    pairs = _lookup(_table("MXFP4", b.device, dtype), b[:, 1:17].long()).view(nb, 16, 2)
    return (pairs.transpose(1, 2) * d.unsqueeze(-1)).reshape(-1)


DEQUANT: Dict[str, Callable[[torch.Tensor], torch.Tensor]] = {
    "F32": deq_f32, "F16": deq_f16, "BF16": deq_bf16,
    "Q8_0": deq_q8_0, "Q4_0": deq_q4_0, "Q4_1": deq_q4_1,
    "Q5_0": deq_q5_0, "Q5_1": deq_q5_1,
    "Q4_K": deq_q4_k, "Q5_K": deq_q5_k, "Q6_K": deq_q6_k,
    "Q2_K": deq_q2_k, "Q3_K": deq_q3_k,
    "IQ1_S": deq_iq1_s, "IQ1_M": deq_iq1_m, "IQ2_XXS": deq_iq2_xxs, "IQ2_XS": deq_iq2_xs,
    "IQ2_S": deq_iq2_s, "IQ3_XXS": deq_iq3_xxs, "IQ3_S": deq_iq3_s, "IQ4_NL": deq_iq4_nl,
    "IQ4_XS": deq_iq4_xs, "MXFP4": deq_mxfp4,
}
# formats that can produce the target dtype themselves (no float32 intermediate)
_EIGENER_DTYPE = {"Q2_K", "Q3_K", "IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ3_XXS", "IQ3_S",
                  "IQ4_NL", "IQ4_XS", "MXFP4"}

# how many elements to dequantise per chunk (bounds temporary memory on the GPU)
CHUNK_ELEMENTS = 32 * 1024 * 1024


def dequantize(raw: torch.Tensor, ggml_type: int, n_elements: int,
               out_dtype: torch.dtype = torch.float32) -> torch.Tensor:
    """Dequantise raw block bytes (uint8 tensor) into a flat tensor of n_elements."""
    name, block_size, type_size = GGML_TYPES[ggml_type]
    fn = DEQUANT.get(name)
    if fn is None:
        raise GGUFError(f"tensor type {name} is not supported by this engine")
    if raw.numel() != n_elements // block_size * type_size:
        raise GGUFError(f"byte count mismatch for {name}")
    if name in _EIGENER_DTYPE and out_dtype in (torch.float32, torch.bfloat16, torch.float16):
        fn = partial(fn, dtype=out_dtype)
    if n_elements <= CHUNK_ELEMENTS:
        return fn(raw).to(out_dtype)
    out = torch.empty(n_elements, dtype=out_dtype, device=raw.device)
    blocks_per_chunk = CHUNK_ELEMENTS // block_size
    n_blocks = n_elements // block_size
    for b0 in range(0, n_blocks, blocks_per_chunk):
        b1 = min(b0 + blocks_per_chunk, n_blocks)
        out[b0 * block_size:b1 * block_size] = fn(raw[b0 * type_size:b1 * type_size]).to(out_dtype)
    return out


def raw_to_tensor(buf: memoryview, device) -> torch.Tensor:
    """Wrap a read-only memoryview (mmap slice) as uint8 tensor and move it to device."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # torch warns about non-writable buffers; we never write
        t = torch.frombuffer(buf, dtype=torch.uint8)
    return t.to(device, non_blocking=False)
