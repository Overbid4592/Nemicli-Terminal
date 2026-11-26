"""
Quantisierte Linear-Schichten: Die GGUF-Blockformate bleiben im VRAM quantisiert, und die
matmul läuft direkt auf ihnen, so wie die mul_mat_vec_q-Kernel von llama.cpp.

Verwendet wird der int4-Kernel mit Gruppen, der im PyTorch-Wheel mitkommt
(`torch._weight_int4pack_mm`, "tinygemm"): Gruppen zu 32 entlang K mit einer bf16-Scale
und einem Zero je Gruppe, berechnet wird  w = (u - 8) * s + z  für Nibbles u.
GGUF-Blöcke werden exakt darauf abgebildet:

  Q4_0 / Q4_1 / Q4_K   eine int4-matmul      (u = Nibble, s = d*sc, z = 8s - m)
  Q8_0 / Q5_0 / Q5_1 / Q5_K
                        8-Bit-Wert u8 = 16*hi + lo -> zwei int4-matmuls mit
                        s_lo = s8, z_lo = 8 s8 ; s_hi = 16 s8, z_hi = 128 s8 + c8
                        sodass  y = s8 * u8 + c8   (exakt)
  Q6_K                  die beiden 16-Element-Sub-Blöcke einer 32er-Gruppe kommen auf
                        ein gemeinsames 8-Bit-Raster (nahezu verlustfrei), dann wie oben.

Keine eigenen Kernel, kein Compiler: alles ist Daten + torchs eigene Operatoren.

Das Einlesen eines langen Prompts (M >= UNPACK_FROM Zeilen) geht anders schneller: tinygemm ist
für kleine M gebaut, daher wird die Matrix für diesen einen Aufruf nach bf16 entpackt (die Tile-
Reihenfolge des gepackten Formats wird einmal vom Packer selbst gelernt) und mit cuBLAS multipliziert.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import torch

from .gguf import GGML_TYPES, GGUFError
from .quant import _blocks, _f16, _nibbles, _q5_high_bits, _scale_min_k4

G = 32                 # Gruppengröße des Kernels == Sub-Block-Größe von GGUF
INNER_K_TILES = 8
PREFILL_SLICE = 32     # Zeilen je Kernel-Aufruf für M > 1 (der Kernel ist auf kleine M abgestimmt)
UNPACK_FROM = 256      # ab so vielen Zeilen: nach bf16 entpacken + cuBLAS statt des Kernels
ROW_CHUNK_ELEMENTS = 32 * 1024 * 1024
TILE_N, TILE_K = 8, 16 * INNER_K_TILES   # ein gepacktes Tile: 8 Zeilen x 128 Spalten = 1024 Nibbles
_TILE_ORDER: dict = {}                   # Gerät -> Tile-Elementindex je gepacktem Slot (Umkehrung)


def kernel_available(device: torch.device) -> bool:
    return device.type == "cuda" and hasattr(torch, "_weight_int4pack_mm")


def quantizable(ggml_type: int, n_out: int, n_in: int, min_elements: int = 1 << 20) -> bool:
    """Kernel-fähig; kleinere Matrizen als `min_elements` bleiben bf16 (für Experten 0: hundert
    kleine Matrizen je Schicht wären in bf16 ein Vielfaches der Datei)."""
    name = GGML_TYPES[ggml_type][0]
    if name not in _CONVERTERS:
        return False
    return n_out % 8 == 0 and n_in % (INNER_K_TILES * 16) == 0 and n_in % G == 0 and n_out * n_in >= min_elements


# --- Umrechnung Block -> Gruppe --------------------------------------------------
# 4-Bit-Konverter liefern (u4 (n, 32) uint8, s (n,), z (n,)) je Gruppe von 32.
# 8-Bit-Konverter liefern (u8 (n, 32) uint8, s8 (n,), c8 (n,))  mit y = s8*u8 + c8.

def _q4_0(raw):
    b = _blocks(raw, 18)
    d = _f16(b[:, :2])[:, 0]
    return _nibbles(b[:, 2:18]).to(torch.uint8), d, torch.zeros_like(d)


def _q4_1(raw):
    b = _blocks(raw, 20)
    d = _f16(b[:, :2])[:, 0]; m = _f16(b[:, 2:4])[:, 0]
    return _nibbles(b[:, 4:20]).to(torch.uint8), d, m + 8 * d


def _q4_k(raw):
    b = _blocks(raw, 144)
    nb = b.shape[0]
    d = _f16(b[:, :2]); dmin = _f16(b[:, 2:4])
    sc, mn = _scale_min_k4(b[:, 4:16])                    # (nb,8)
    qs = b[:, 16:144].reshape(nb, 4, 32)
    u = torch.stack([qs & 0x0F, qs >> 4], dim=2).reshape(nb * 8, 32)
    s = (d * sc).reshape(-1); m = (dmin * mn).reshape(-1)
    return u, s, 8 * s - m


def _q8_0(raw):
    b = _blocks(raw, 34)
    d = _f16(b[:, :2])[:, 0]
    u8 = (b[:, 2:].contiguous().view(torch.int8).to(torch.int16) + 128).to(torch.uint8)
    return u8, d, -128 * d


def _q5_0(raw):
    b = _blocks(raw, 22)
    d = _f16(b[:, :2])[:, 0]
    u8 = (_nibbles(b[:, 6:22]) | _q5_high_bits(b[:, 2:6])).to(torch.uint8)
    return u8, d, -16 * d


def _q5_1(raw):
    b = _blocks(raw, 24)
    d = _f16(b[:, :2])[:, 0]; m = _f16(b[:, 2:4])[:, 0]
    u8 = (_nibbles(b[:, 8:24]) | _q5_high_bits(b[:, 4:8])).to(torch.uint8)
    return u8, d, m


def _q5_k(raw):
    b = _blocks(raw, 176)
    nb = b.shape[0]
    d = _f16(b[:, :2]); dmin = _f16(b[:, 2:4])
    sc, mn = _scale_min_k4(b[:, 4:16])
    qh = b[:, 16:48].to(torch.int16)
    qs = b[:, 48:176].reshape(nb, 4, 32).to(torch.int16)
    shifts = (torch.arange(4, device=b.device, dtype=torch.int16) * 2).view(1, 4, 1)
    qh_e = qh.unsqueeze(1)
    lo = (qs & 0x0F) | (((qh_e >> shifts) & 1) << 4)
    hi = (qs >> 4) | (((qh_e >> (shifts + 1)) & 1) << 4)
    u8 = torch.stack([lo, hi], dim=2).reshape(nb * 8, 32).to(torch.uint8)
    s = (d * sc).reshape(-1); m = (dmin * mn).reshape(-1)
    return u8, s, -m


def _q6_k(raw):
    b = _blocks(raw, 210)
    nb = b.shape[0]
    ql = b[:, 0:128].to(torch.int16)
    qh = b[:, 128:192].to(torch.int16)
    sc = b[:, 192:208].contiguous().view(torch.int8).float()      # (nb,16) eine je 16 Elemente
    d = _f16(b[:, 208:210])                                       # (nb,1)
    halves = []
    for n in range(2):
        ql_n = ql[:, n * 64:(n + 1) * 64]; qh_n = qh[:, n * 32:(n + 1) * 32]
        q1 = (ql_n[:, :32] & 0x0F) | ((qh_n & 3) << 4)
        q2 = (ql_n[:, 32:] & 0x0F) | (((qh_n >> 2) & 3) << 4)
        q3 = (ql_n[:, :32] >> 4) | (((qh_n >> 4) & 3) << 4)
        q4 = (ql_n[:, 32:] >> 4) | (((qh_n >> 6) & 3) << 4)
        halves.append(torch.stack([q1, q2, q3, q4], dim=1).reshape(nb, 128))
    q = (torch.cat(halves, dim=1).float() - 32.0).reshape(nb, 8, 2, 16)   # (nb, group, sub, 16)
    scg = sc.reshape(nb, 8, 2)                                            # Scales je Sub-Block
    m = scg.abs().amax(dim=2, keepdim=True).clamp_min(1.0)                # gemeinsamer Betrag je Gruppe
    # beide Sub-Blöcke auf das Raster d*m/4 legen (feiner als das eigene Raster des gröberen Sub-Blocks)
    u = torch.round(q * (scg * 4.0 / m).unsqueeze(-1)) + 128.0
    u8 = u.clamp_(0, 255).reshape(nb * 8, 32).to(torch.uint8)
    s8 = (d * m[:, :, 0] / 4.0).reshape(-1)
    return u8, s8, -128 * s8


# Konverter, Wert-Bits.  5-Bit-Werte werden vor der Nibble-Aufteilung um 3 nach links geschoben, damit
# der Zero-Point der hi-matmul (2^(bits-1)*s8 + c8) klein bleibt: große Zero-Points würden
# durch die bf16-Rundung Genauigkeit verlieren.
_CONVERTERS = {
    "Q4_0": (_q4_0, 4), "Q4_1": (_q4_1, 4), "Q4_K": (_q4_k, 4),
    "Q8_0": (_q8_0, 8), "Q5_0": (_q5_0, 5), "Q5_1": (_q5_1, 5), "Q5_K": (_q5_k, 5), "Q6_K": (_q6_k, 8),
}


# --- packing -------------------------------------------------------------------------
def _pack(u4: torch.Tensor) -> torch.Tensor:
    """u4: (N, K) uint8-Nibbles -> gepacktes tinygemm-Layout (Zeilen-Chunks müssen Vielfache von 8 sein)."""
    b = ((u4[:, 0::2] << 4) | u4[:, 1::2]).contiguous()
    return torch._convert_weight_to_int4pack(b, INNER_K_TILES)


def _sz(s: torch.Tensor, z: torch.Tensor) -> torch.Tensor:
    """s, z: (N, K/G) float -> (K/G, N, 2) bf16 scales_and_zeros."""
    return torch.stack([s.t(), z.t()], dim=-1).to(torch.bfloat16).contiguous()


def _packed_nibbles(packed: torch.Tensor) -> torch.Tensor:
    """Gepackt (N/8, K/128, 32, 4) int32 -> (N/8, K/128, 1024) uint8 in Speicherreihenfolge."""
    b = packed.contiguous().view(torch.uint8).reshape(packed.shape[0], packed.shape[1], -1)
    return torch.stack([b & 0x0F, b >> 4], dim=-1).reshape(packed.shape[0], packed.shape[1], -1)


def _tile_order(device: torch.device) -> torch.Tensor:
    """Für jedes Tile-Element (Zeile*128 + Spalte): sein Slot im gepackten Tile. Gelernt, indem
    der Elementindex selbst gepackt wird, Stelle für Stelle (3 x 4 Bits decken 1024 Slots ab)."""
    key = str(device)
    if (order := _TILE_ORDER.get(key)) is None:         # lokal gehalten: caches_leeren() kann dazwischenkommen
        idx = torch.arange(TILE_N * TILE_K, device=device).view(TILE_N, TILE_K)
        slot_to_elem = torch.zeros(TILE_N * TILE_K, dtype=torch.long, device=device)
        for digit in range(3):
            part = ((idx >> (4 * digit)) & 0x0F).to(torch.uint8)
            slot_to_elem += _packed_nibbles(_pack(part))[0, 0].long() << (4 * digit)
        if not torch.equal(slot_to_elem.sort().values, torch.arange(TILE_N * TILE_K, device=device)):
            raise RuntimeError("unexpected int4 pack layout")
        order = torch.empty_like(slot_to_elem)
        order[slot_to_elem] = torch.arange(TILE_N * TILE_K, device=device)
        _TILE_ORDER[key] = order
    return order


def caches_leeren() -> None:
    """Gibt die prozessweiten Tabellen des Entpackens frei (Tile-Ordnung, IQ-Tabellen, Pläne); sie
    entstehen beim nächsten Gebrauch neu. Nur aufrufen, wenn kein aufgenommener CUDA-Graph sie liest."""
    from . import quant
    _TILE_ORDER.clear()
    quant.caches_leeren()


def unpack_u4(packed: torch.Tensor, n: int, k: int) -> torch.Tensor:
    """Umkehrung von _pack: (n, k) uint8-Nibbles."""
    tiles = _packed_nibbles(packed).index_select(-1, _tile_order(packed.device))
    return tiles.view(n // TILE_N, k // TILE_K, TILE_N, TILE_K).transpose(1, 2).reshape(n, k)


class QuantLinear:
    """y = x @ W^T (+ b), wobei W im int4-Gruppenformat bleibt (eine oder zwei matmuls)."""

    def __init__(self, mats: List[Tuple[torch.Tensor, torch.Tensor]], n_out: int, n_in: int,
                 bias: Optional[torch.Tensor] = None, type_name: str = ""):
        self.mats = mats
        self.n_out, self.n_in = n_out, n_in
        self.b = None if bias is None else bias.to(torch.bfloat16)
        self.type_name = type_name
        self.w = None   # für Duck-Typing mit Linear (dtype-Abfragen)

    @property
    def dtype(self):
        return torch.bfloat16

    def _mm(self, x2: torch.Tensor) -> torch.Tensor:
        y = None
        for packed, sz in self.mats:
            part = torch._weight_int4pack_mm(x2, packed, G, sz)
            y = part if y is None else y + part
        return y

    def bf16_weight(self) -> torch.Tensor:
        """Die Matrix als (n_out, n_in) bf16 – dieselben Werte, die der Kernel nutzt (w = (u-8)*s + z)."""
        w = None
        for packed, sz in self.mats:
            u = unpack_u4(packed, self.n_out, self.n_in).view(self.n_out, -1, G).to(torch.bfloat16)
            s = sz[..., 0].t().contiguous().unsqueeze(-1)
            z = (sz[..., 1].float() - 8 * sz[..., 0].float()).t().contiguous().unsqueeze(-1).to(torch.bfloat16)
            part = torch.addcmul(z, u, s).reshape(self.n_out, self.n_in)
            w = part if w is None else w + part
        return w

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        x2 = x.reshape(-1, self.n_in).to(torch.bfloat16)
        M = x2.shape[0]
        if M >= UNPACK_FROM:
            y = x2 @ self.bf16_weight().t()
        elif M <= PREFILL_SLICE:
            y = self._mm(x2)
        else:
            y = torch.cat([self._mm(x2[i:i + PREFILL_SLICE]) for i in range(0, M, PREFILL_SLICE)], dim=0)
        if self.b is not None:
            y = y + self.b
        return y.reshape(*x.shape[:-1], self.n_out)


def build_quant_linear(raw: torch.Tensor, ggml_type: int, n_out: int, n_in: int,
                       bias: Optional[torch.Tensor] = None) -> QuantLinear:
    """raw: uint8-Block-Bytes einer (n_out, n_in) GGUF-Matrix, die schon auf dem CUDA-Gerät liegt."""
    name, block_size, type_size = GGML_TYPES[ggml_type]
    conv, bits = _CONVERTERS[name]
    if raw.numel() != n_out * n_in // block_size * type_size:
        raise GGUFError("byte count mismatch in build_quant_linear")
    rows_per_chunk = max(8, (ROW_CHUNK_ELEMENTS // n_in) // 8 * 8)
    bytes_per_row = n_in // block_size * type_size
    groups_per_row = n_in // G
    packed_parts: List[List[torch.Tensor]] = [[], []]
    s_parts: List[List[torch.Tensor]] = [[], []]
    z_parts: List[List[torch.Tensor]] = [[], []]
    for r0 in range(0, n_out, rows_per_chunk):
        r1 = min(r0 + rows_per_chunk, n_out)
        u, s, c = conv(raw[r0 * bytes_per_row:r1 * bytes_per_row])
        rows = r1 - r0
        s = s.reshape(rows, groups_per_row); c = c.reshape(rows, groups_per_row)
        u = u.reshape(rows, n_in)
        if bits == 4:
            packed_parts[0].append(_pack(u)); s_parts[0].append(s); z_parts[0].append(c)
        else:
            shift = 8 - bits                       # den vollen 8-Bit-Bereich nutzen: u' = u << shift, s' = s / 2^shift
            u = u << shift
            s = s / (1 << shift)
            lo, hi = u & 0x0F, u >> 4
            packed_parts[0].append(_pack(lo)); s_parts[0].append(s);      z_parts[0].append(8 * s)
            packed_parts[1].append(_pack(hi)); s_parts[1].append(16 * s); z_parts[1].append(128 * s + c)
        del u, s, c
    mats = []
    for i in range(1 if bits == 4 else 2):
        packed = torch.cat(packed_parts[i], dim=0)
        sz = _sz(torch.cat(s_parts[i], dim=0), torch.cat(z_parts[i], dim=0))
        mats.append((packed, sz))
    return QuantLinear(mats, n_out, n_in, bias, name)
