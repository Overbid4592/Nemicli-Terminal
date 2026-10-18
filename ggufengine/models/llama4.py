"""
Llama 4 (arch "llama4"), text part – as llama.cpp src/models/llama4.cpp:

* RoPE ("normal" pairs, llama3 frequency factors from rope_freqs) on all layers except every
  4th; those NoPE layers scale the query by log(floor((pos+1)/8192)+1)*0.1 + 1 (temperature
  tuning) and attend to the full context.  RoPE layers attend within blocks of 8192 positions
  (chunked attention) unless attention.sliding_window is 0, and normalise q/k after RoPE
  (L2 norm without weight; not for the 128-expert model).
* Every `interleave_moe_layer_step`-th layer is MoE: sigmoid of the top-1 router logit scales
  the expert's *input*, plus a shared expert; the other layers have a dense SwiGLU MLP.
"""
from __future__ import annotations

from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import (GraphDecoder, KVCache, RoPE, WeightLoader, attention, letzte_zeilen, logit_zeilen,
                     rms_norm, swiglu_mlp)
from .moe import Experten

BLOCK = 8192                    # chunked attention and temperature tuning (fixed in llama.cpp)
TEMP_SKALA = 0.1


class _Block:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: RoPE, n_ctx: int):
        p = f"blk.{i}."
        self.hp, self.eps = hp, hp["eps"]
        self.H, self.Hkv, self.D = hp["n_heads"], hp["n_kv_heads"], hp["head_dim"]
        self.mit_rope = (i + 1) % hp["no_rope_step"] != 0
        self.rope = rope if self.mit_rope else None
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.q, self.k, self.v = (ld.linear(p + f"attn_{n}.weight") for n in "qkv")
        self.o = ld.linear(p + "attn_output.weight")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.cache = KVCache(self.Hkv, self.D, n_ctx, ld.device, ld.dtype, ld.kv_bits)
        self.moe = ld.has(p + "ffn_gate_inp.weight")
        if self.moe:
            self.router = ld.linear(p + "ffn_gate_inp.weight")
            self.experten = Experten(ld, p, F.silu)
            if not (self.experten.pruefen() and len(self.experten) == self.router.n_out and hp["n_expert_used"] == 1):
                raise GGUFError(f"layer {i}: inconsistent expert tensors (Llama 4 routes to one expert)")
            self.s_gate, self.s_up, self.s_down = (ld.linear(p + f"ffn_{n}_shexp.weight") for n in ("gate", "up", "down"))
        else:
            self.gate, self.up, self.down = (ld.linear(p + f"ffn_{n}.weight") for n in ("gate", "up", "down"))

    def _attn(self, x, pos_idx, kv_len):
        L, H, Hkv, D, hp = x.shape[0], self.H, self.Hkv, self.D, self.hp
        q = self.q(x).float().view(L, H, D)
        k = self.k(x).float().view(L, Hkv, D)
        v = self.v(x).view(L, Hkv, D)
        if self.mit_rope:
            q, k = self.rope(q, pos_idx), self.rope(k, pos_idx)
            if hp["kq_norm"]:
                q = q * torch.rsqrt(q.pow(2).mean(-1, keepdim=True) + self.eps)
                k = k * torch.rsqrt(k.pow(2).mean(-1, keepdim=True) + self.eps)
        else:                                                    # Temperatur je Position
            stufe = torch.floor((pos_idx.float() + 1) / BLOCK)
            q = q * (torch.log(stufe + 1) * TEMP_SKALA + 1).view(L, 1, 1)
        self.cache.write(k, v, pos_idx)
        block = hp["chunk"] if self.mit_rope else 0
        o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, D ** -0.5, chunk=block)
        return self.o(o.reshape(L, -1)).float()

    def _ffn(self, x):
        if not self.moe:
            return swiglu_mlp(x, self.gate, self.up, self.down).float()
        logits = self.router(x).float()
        w, idx = logits.topk(1, dim=-1)
        rein = x.float() * torch.sigmoid(w)                      # Gewicht VOR dem Experten
        out = self.experten(rein.to(x.dtype), idx, torch.ones_like(w))
        return out + swiglu_mlp(x, self.s_gate, self.s_up, self.s_down).float()

    def __call__(self, h, pos_idx, kv_len):
        h = h + self._attn(rms_norm(h, self.attn_norm, self.eps), pos_idx, kv_len)
        return h + self._ffn(rms_norm(h, self.ffn_norm, self.eps))


class Llama4Model:
    alle_logits = False      # Logits jeder Zeile statt nur der letzten (spekulatives Prüfen)
    arch_names = ("llama4",)
    prefill_chunk = 2048

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a != "llama4":
            raise GGUFError(f"{a} is not a llama4 model")
        k = lambda s: f"{a}.{s}"
        n_layer = int(gg.require(k("block_count")))
        n_embd = int(gg.require(k("embedding_length")))
        n_heads = int(gg.require(k("attention.head_count")))
        n_expert = int(gg.get(k("expert_count"), 0) or 0)
        fenster = gg.get(k("attention.sliding_window"))
        hp = dict(
            n_layer=n_layer, n_heads=n_heads, n_kv_heads=int(gg.get(k("attention.head_count_kv"), n_heads)),
            head_dim=int(gg.get(k("attention.key_length"), n_embd // n_heads)),
            eps=float(gg.get(k("attention.layer_norm_rms_epsilon"), 1e-5)),
            n_expert_used=int(gg.get(k("expert_used_count"), 1) or 1),
            kq_norm=n_expert != 128,
            no_rope_step=n_layer + 1 if fenster == 0 else 4,      # Fenster 0: überall RoPE, volle Attention
            chunk=0 if fenster == 0 else BLOCK,
        )
        if not (0 < n_layer <= 512 and 0 < n_embd <= 65536 and 0 < hp["head_dim"] <= 1024 and n_expert > 0):
            raise GGUFError("implausible hyper-parameters")
        self.hp, self.n_ctx, self.device = hp, n_ctx, device
        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.out_norm = ld.norm("output_norm.weight")
        self.lm_head = head if tied else ld.linear("output.weight")
        faktoren = ld.norm("rope_freqs.weight").float() if ld.has("rope_freqs.weight") else None
        dim = int(gg.get(k("rope.dimension_count"), hp["head_dim"]))
        rope = RoPE(dim, float(gg.get(k("rope.freq_base"), 500000.0)), device, n_ctx, neox=False, freq_factors=faktoren)
        self.blocks: List[_Block] = [_Block(ld, i, hp, rope, n_ctx) for i in range(n_layer)]
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        graph_ok = all(not b.moe or b.experten.graphfaehig for b in self.blocks)
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" and graph_ok else None

    def reset(self):
        pass

    def snapshot_state(self):
        return None

    def restore_state(self, snap):
        pass

    def resume_from(self, common: int, old_len: int) -> Optional[int]:
        return common

    def checkpoint_state(self):
        return None

    def restore_checkpoint(self, snap):
        pass

    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        h = self.embed(tok).float()
        for blk in self.blocks:
            h = blk(h, pos_idx, kv_len)
        h = rms_norm(letzte_zeilen(self, h), self.out_norm, self.hp["eps"])
        return logit_zeilen(self, self.lm_head(h)).float()

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int) -> torch.Tensor:
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")
        if len(tokens) == 1 and self.graph is not None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens))

