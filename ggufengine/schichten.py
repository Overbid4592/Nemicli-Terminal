"""Schichten im RAM: welche Gewichte eines Modells im festgesetzten RAM bleiben, wenn der VRAM
nicht für Modell und Kontext reicht.

Die Matrizen kommen bei jedem Schritt über PCIe auf die GPU (ein Fließband aus wenigen
VRAM-Plätzen, das schon die nächsten holt) – jedes Byte im RAM kostet pro Token gleich viel
Zeit, egal welche Schicht. Deshalb zuerst die
Einbettung (pro Token nur eine Zeile), dann die großen FFN-Matrizen, dann der Rest der
Schichten. Im VRAM bleiben: Ausgabe-Kopf, Normen, kleine Matrizen, MTP-Schichten und
MoE-Experten (die haben ihr eigenes Budget).
"""
from __future__ import annotations

import re
from typing import NamedTuple

from . import cudakern
from .gguf import GGML_TYPES, GGUFFile

_VIER_BIT = {"Q4_0", "Q4_1", "Q4_K"}
_INT8_WEG = {"Q4_K", "Q6_K"}            # bleiben im Blockformat (mmvq), wenn NVRTC da ist
_ACHT_BIT = {"Q8_0", "Q5_0", "Q5_1", "Q5_K", "Q6_K"}
MIN_WERTE = 1 << 20                     # kleinere Matrizen bleiben im VRAM (wie qlinear.quantizable)
BAND_PLAETZE = 4                        # VRAM-Plätze des Fließbands (models.common.Fliessband)
_SCHICHT = re.compile(r"blk\.(\d+)\.(.+)$")


def int8_weg_da() -> bool:
    """Q4_K/Q6_K-Matrizen bleiben im Blockformat, wenn die int8-Kerne (mmvq, NVRTC) übersetzbar sind."""
    return cudakern._nvrtc_pfad() is not None


def vram_bytes(name: str, ti, gebunden: bool) -> int:
    """VRAM eines Tensors, wie die Engine ihn ablegt: Q4_K/Q6_K-Matrizen im Blockformat (int8-Kerne),
    andere 4-Bit-Blöcke als int4 mit bf16-Skala und -Nullpunkt je 32 Werte (5 Bit/Wert), 5- bis
    8-Bit-Blöcke als zwei solche Matrizen (10 Bit/Wert), Einbettungstabellen und Formate ohne
    int4-Weg (cudakern.IMMER_GEPACKT) unverändert im Blockformat, der Rest bf16 (Vektoren float32)."""
    art, block, _ = GGML_TYPES.get(ti.ggml_type, ("", 1, 0))
    n_aus, n_ein = (ti.shape[-2], ti.shape[-1]) if len(ti.shape) >= 2 else (1, ti.n_elements)
    experten = len(ti.shape) == 3 and "_exps." in name                # gepackt ohne Mindestgröße
    packbar = (art in _VIER_BIT | _ACHT_BIT and n_aus % 8 == 0 and n_ein % 128 == 0
               and (experten or n_aus * n_ein >= MIN_WERTE))
    gepackt = ti.n_elements * (5 if art in _VIER_BIT else 10) // 8
    if name == "per_layer_token_embd.weight" and block > 1:           # Gemma 4: Tabelle im RAM
        return 0
    if name.endswith(("attn_k_b.weight", "attn_v_b.weight", "attn_kv_b.weight")):   # MLA: float32
        return ti.n_elements * 4
    if art in cudakern.IMMER_GEPACKT:
        return ti.n_bytes
    if art in _INT8_WEG and len(ti.shape) == 2 and n_ein % 256 == 0 and int8_weg_da():
        return ti.n_bytes                                             # auch als gebundener Kopf: dieselben Bytes
    if name == "token_embd.weight" and block > 1 and packbar:
        return ti.n_bytes + (gepackt if gebunden else 0)
    if packbar:
        return gepackt
    return ti.n_elements * (4 if len(ti.shape) < 2 else 2)


def gewichte_bytes(gg: GGUFFile, nur_experten: bool = False) -> int:
    gebunden = "output.weight" not in gg.tensors
    return sum(vram_bytes(n, ti, gebunden) for n, ti in gg.tensors.items()
               if not nur_experten or (len(ti.shape) == 3 and "_exps." in n))


def haupt_schichten(gg: GGUFFile) -> int:
    """Schichten des Modells ohne die MTP-Schichten am Ende."""
    a = gg.architecture
    return int(gg.get(f"{a}.block_count", 0) or 0) - int(gg.get(f"{a}.nextn_predict_layers", 0) or 0)


def _rang(name: str, ti, n_haupt: int, min_werte: int):
    """Reihenfolge fürs Auslagern (kleiner = früher), None: bleibt im VRAM."""
    art, block, _ = GGML_TYPES.get(ti.ggml_type, ("", 1, 0))
    if block <= 1 or art not in cudakern.FORMATE:                     # nur Blockformate mit Kernel
        return None
    if name == "token_embd.weight":
        return (0, 0, "")
    m = _SCHICHT.match(name)
    if m is None or len(ti.shape) != 2 or ti.n_elements < min_werte or "_exps." in name:
        return None
    i = int(m.group(1))
    if i >= n_haupt:                                                  # MTP: schlägt jeden Schritt vor
        return None
    return (1 if m.group(2).startswith("ffn_") else 2, -i, m.group(2))


class Plan(NamedTuple):
    ram: frozenset[str]          # Tensoren im festgesetzten RAM
    ram_bytes: int               # deren Größe im RAM (Dateiformat)
    vram_bytes: int              # Gewichte, die im VRAM bleiben, samt Fließband


def planen(gg: GGUFFile, budget: int | None, min_werte: int = MIN_WERTE) -> Plan:
    """So viele Matrizen in den RAM, bis die Gewichte im VRAM höchstens `budget` Bytes belegen
    (None: alles im VRAM, 0: alles Auslagerbare im RAM)."""
    gebunden = "output.weight" not in gg.tensors
    vram = gewichte_bytes(gg)
    if budget is None or vram <= budget:
        return Plan(frozenset(), 0, vram)
    n_haupt = haupt_schichten(gg)
    kandidaten = []
    for name, ti in gg.tensors.items():
        if name == "token_embd.weight" and gebunden:                  # Tabelle ist zugleich der Kopf
            continue
        rang = _rang(name, ti, n_haupt, min_werte)
        if rang is not None:
            kandidaten.append((rang, name, ti))
    kandidaten.sort(key=lambda k: k[0])
    ram, ram_bytes, groesste = set(), 0, 0
    for _, name, ti in kandidaten:
        if vram + BAND_PLAETZE * groesste <= budget:
            break
        ram.add(name)
        ram_bytes += ti.n_bytes
        vram -= vram_bytes(name, ti, gebunden)
        if name != "token_embd.weight":                              # Zeilen direkt, ohne Fließband
            groesste = max(groesste, ti.n_bytes)
    return Plan(frozenset(ram), ram_bytes, vram + BAND_PLAETZE * groesste)
