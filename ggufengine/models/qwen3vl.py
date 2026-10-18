"""
Qwen3-VL (arch "qwen3vl"): the dense Qwen3 language model (GQA, q/k RMSNorm, SwiGLU)
with interleaved M-RoPE and DeepStack.

DeepStack: the mmproj delivers per image token the input embedding plus one extra
block per early layer (`n_deepstack_layers`); after layer i the i-th block is added
to the hidden state of the image positions (transformers Qwen3VLTextModel
`_deepstack_process`).  Text-only steps run through the CUDA graphs as usual.
"""
from __future__ import annotations

from typing import List, Optional

import torch

from ..gguf import GGUFError, GGUFFile
from .common import (
    GraphDecoder,
    KVCache,
    WeightLoader,
    attention,
    letzte_zeilen,
    logit_zeilen,
    rms_norm,
    swiglu_mlp,
)
from .qwen35 import MRoPE


class _Block:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: MRoPE, n_ctx: int):
        p = f"blk.{i}."
        self.H, self.Hkv, self.D = hp["n_heads"], hp["n_kv_heads"], hp["head_dim"]
        self.eps = hp["eps"]
        self.rope = rope
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.q = ld.linear(p + "attn_q.weight")
        self.k = ld.linear(p + "attn_k.weight")
        self.v = ld.linear(p + "attn_v.weight")
        self.o = ld.linear(p + "attn_output.weight")
        self.q_norm = ld.norm(p + "attn_q_norm.weight")
        self.k_norm = ld.norm(p + "attn_k_norm.weight")
        if self.q.n_out != self.H * self.D or self.k.n_out != self.Hkv * self.D:
            raise GGUFError(f"layer {i}: attention shapes do not match the head counts")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.gate = ld.linear(p + "ffn_gate.weight")
        self.up = ld.linear(p + "ffn_up.weight")
        self.down = ld.linear(p + "ffn_down.weight")
        self.cache = KVCache(self.Hkv, self.D, n_ctx, ld.device, ld.dtype, ld.kv_bits)
        self.scale = self.D ** -0.5

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, pos3: torch.Tensor) -> torch.Tensor:
        L = h.shape[0]
        x = rms_norm(h, self.attn_norm, self.eps)
        q = self.q(x).float().view(L, self.H, self.D)
        k = self.k(x).float().view(L, self.Hkv, self.D)
        v = self.v(x).view(L, self.Hkv, self.D)
        q = self.rope(rms_norm(q, self.q_norm, self.eps), pos3)
        k = self.rope(rms_norm(k, self.k_norm, self.eps), pos3)
        self.cache.write(k, v, pos_idx)
        o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, self.scale)
        h = h + self.o(o.reshape(L, -1)).float()
        x = rms_norm(h, self.ffn_norm, self.eps)
        return h + swiglu_mlp(x, self.gate, self.up, self.down).float()


class Qwen3VLModel:
    alle_logits = False      # Logits jeder Zeile statt nur der letzten (spekulatives Prüfen)
    arch_names = ("qwen3vl",)
    prefill_chunk = 2048
    supports_images = True
    uses_mrope = True
    uses_deepstack = True

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a not in self.arch_names:
            raise GGUFError(f"{a} is not a qwen3vl model")
        k = lambda s: f"{a}.{s}"
        n_layer = int(gg.require(k("block_count")))
        n_embd = int(gg.require(k("embedding_length")))
        n_heads = int(gg.require(k("attention.head_count")))
        head_dim = int(gg.get(k("attention.key_length"), n_embd // n_heads))
        hp = dict(
            n_layer=n_layer, n_embd=n_embd, n_heads=n_heads, head_dim=head_dim,
            n_kv_heads=int(gg.get(k("attention.head_count_kv"), n_heads)),
            eps=float(gg.get(k("attention.layer_norm_rms_epsilon"), 1e-6)),
            rope_dim=int(gg.get(k("rope.dimension_count"), head_dim)),
            rope_base=float(gg.get(k("rope.freq_base"), 5_000_000.0)),
            n_deepstack=int(gg.get(k("n_deepstack_layers"), 0)),
        )
        if n_heads % hp["n_kv_heads"]:
            raise GGUFError("head count is not a multiple of the kv head count")
        if not (0 < n_layer <= 512 and 0 < n_embd <= 65536 and 0 < head_dim <= 1024
                and 0 <= hp["n_deepstack"] <= n_layer and 0 < hp["rope_dim"] <= head_dim):
            raise GGUFError("implausible hyper-parameters")
        self.hp = hp
        self.n_ctx = n_ctx
        self.device = device

        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.out_norm = ld.norm("output_norm.weight")
        self.lm_head = head if tied else ld.linear("output.weight")
        sections = gg.get(k("rope.dimension_sections")) or []
        if not isinstance(sections, list) or len(sections) > 4 or sum(sections[:3]) * 2 > hp["rope_dim"]:
            raise GGUFError("invalid rope.dimension_sections")
        rope = MRoPE(hp["rope_dim"], hp["rope_base"], sections[:3], device)
        self._roff = torch.zeros(1, dtype=torch.long, device=device)    # see Qwen35Model
        self.blocks: List[_Block] = [_Block(ld, i, hp, rope, n_ctx) for i in range(n_layer)]
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" else None

    # -- state: only the KV cache, slots are overwritten by position ------------
    def reset(self):
        self._roff.zero_()

    def set_rope_offset(self, offset: int) -> None:
        self._roff.fill_(int(offset))

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

    # -- compute -------------------------------------------------------------
    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, inject=None,
             pos3: Optional[torch.Tensor] = None) -> torch.Tensor:
        """inject: (rows, embeddings) for image positions – the first n_embd columns
        replace the token embeddings, further blocks of n_embd are the DeepStack
        features added after the first layers."""
        h = self.embed(tok)
        tief = None
        if inject is not None:
            rows, emb = inject
            E = self.hp["n_embd"]
            if emb.shape[1] % E:
                raise ValueError(f"image embeddings of width {emb.shape[1]} do not fit the model ({E})")
            emb = emb.to(h.device, h.dtype)
            h = h.index_copy(0, rows, emb[:, :E])
            n = min(emb.shape[1] // E - 1, self.hp["n_deepstack"])
            if n > 0:
                tief = emb[:, E:E * (n + 1)].reshape(-1, n, E)
        if pos3 is None:
            pos3 = (pos_idx + self._roff).unsqueeze(1).expand(-1, 3)
        for i, blk in enumerate(self.blocks):
            h = blk(h, pos_idx, kv_len, pos3)
            if tief is not None and i < tief.shape[1]:
                h = h.index_add(0, rows, tief[:, i].to(h.dtype))
        h = rms_norm(letzte_zeilen(self, h), self.out_norm, self.hp["eps"])
        return logit_zeilen(self, self.lm_head(h)).float()

    def _check(self, tokens: List[int], pos: int):
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int, inject=None, pos3=None) -> torch.Tensor:
        """Process `tokens` at cache slots pos.., return logits of the last token (float32, (V,))."""
        self._check(tokens, pos)
        if len(tokens) == 1 and self.graph is not None and inject is None and pos3 is None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        if pos3 is not None and not isinstance(pos3, torch.Tensor):
            pos3 = torch.tensor(pos3, device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens), inject, pos3)
