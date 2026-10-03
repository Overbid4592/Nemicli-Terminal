"""
Gemma 4 (arch "gemma4": E2B/E4B with PLE, dense 12B/31B, MoE 26B-A4B), following
llama.cpp's src/models/gemma4.cpp:

* embeddings scaled by sqrt(n_embd); per-layer embeddings (PLE): a second table gives every
  layer a 256-d signal per token, mixed with a projection of the main embedding
* alternating sliding-window (head_dim 256, rope base 1e4) and full-attention layers
  (head_dim 512, rope base 1e6 with proportional rope frequency factors)
* the last `shared_kv_layers` layers have no K/V projections and reuse the KV cache of the
  last earlier layer of the same attention type
* attention scale 1.0, q/k RMSNorm with weight, v RMSNorm without weight,
  post-attention and post-FFN norms, GELU (tanh) FFN, per-layer output scalar,
  final logit soft-capping.  Norm weights are stored as-is (no +1 shift).
* head counts may differ per layer (arrays), Q/K/V may be fused (attn_qkv)
* MoE layers: dense MLP and experts run in parallel on the same input, each with its own
  norms; the router sees the unweighted RMS-normed input times 1/sqrt(n_embd) times
  `ffn_gate_inp.scale`, softmax over all experts, top-k renormalised, times the per-expert
  `ffn_down_exps.scale`
"""
from __future__ import annotations

import math
from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from ..quant import dequantize
from .common import PREFILL_CHUNK, GraphDecoder, KVCache, RingKVCache, RoPE, WeightLoader, attention, rms_norm
from .moe import Experten


def _gelu(x: torch.Tensor) -> torch.Tensor:
    return F.gelu(x, approximate="tanh")


def _laeufe(zeilen: List[int], start: int) -> list:
    """Zusammenhängende Zeilen (ein Bild) als Positionsbereiche [lo, hi)."""
    raus = []
    for z in sorted(zeilen):
        if raus and raus[-1][1] == start + z:
            raus[-1][1] += 1
        else:
            raus.append([start + z, start + z + 1])
    return [tuple(r) for r in raus]


def _je_schicht(wert, n: int, name: str, hi: int) -> List[int]:
    """Number or per-layer array -> list of n integers in 1..hi."""
    werte = wert if isinstance(wert, list) else [wert] * n
    if len(werte) != n or not all(isinstance(x, int) and not isinstance(x, bool) and 0 < x <= hi for x in werte):
        raise GGUFError(f"invalid {name}")
    return werte


class _Layer:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, is_swa: bool, has_kv: bool,
                 rope: RoPE, n_ctx: int, kv_source: Optional["_Layer"]):
        p = f"blk.{i}."
        self.i = i
        self.is_swa, self.has_kv = is_swa, has_kv
        self.H, self.Hkv = hp["n_heads"][i], hp["n_kv_heads"][i]
        self.n_used = hp["n_expert_used"]
        if self.H % self.Hkv:
            raise GGUFError(f"layer {i}: head_count must be a multiple of head_count_kv")
        self.D = hp["head_dim_swa"] if is_swa else hp["head_dim"]
        self.eps = hp["eps"]
        self.window = hp["window"] if is_swa else 0
        self.rope = rope
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.qkv = ld.linear(p + "attn_qkv.weight") if ld.has(p + "attn_qkv.weight") else None
        if self.qkv is not None:
            if self.qkv.n_out != (self.H + 2 * self.Hkv) * self.D:
                raise GGUFError(f"layer {i}: attn_qkv has an unexpected size")
        else:
            self.q = ld.linear(p + "attn_q.weight")
            if self.q.n_out != self.H * self.D:
                raise GGUFError(f"layer {i}: attn_q has {self.q.n_out} outputs, expected {self.H * self.D}")
        self.q_norm = ld.norm(p + "attn_q_norm.weight")
        if has_kv:
            if self.qkv is None:
                self.k = ld.linear(p + "attn_k.weight")
                self.v = ld.linear(p + "attn_v.weight") if ld.has(p + "attn_v.weight") else None
                for w in (self.k, self.v):
                    if w is not None and w.n_out != self.Hkv * self.D:
                        raise GGUFError(f"layer {i}: attn_k/attn_v has an unexpected size")
            self.k_norm = ld.norm(p + "attn_k_norm.weight")
            # sliding-window layers only need the last `window` positions: fixed ring
            self.cache = (RingKVCache(self.Hkv, self.D, self.window, ld.device, ld.dtype) if is_swa
                          else KVCache(self.Hkv, self.D, n_ctx, ld.device, ld.dtype, ld.kv_bits))
        else:
            if kv_source is None or kv_source.D != self.D or kv_source.Hkv != self.Hkv:
                raise GGUFError(f"layer {i}: no KV source layer of matching type")
            self.cache = kv_source.cache
        self.o = ld.linear(p + "attn_output.weight")
        self.post_attn_norm = ld.norm(p + "post_attention_norm.weight")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.gate = ld.linear(p + "ffn_gate.weight")
        self.up = ld.linear(p + "ffn_up.weight")
        self.down = ld.linear(p + "ffn_down.weight")
        self.post_ffn_norm = ld.norm(p + "post_ffw_norm.weight")
        self.moe = ld.has(p + "ffn_gate_inp.weight")
        if self.moe:
            self.router = ld.linear(p + "ffn_gate_inp.weight")
            self.router_scale = ld.norm(p + "ffn_gate_inp.scale").reshape(-1)
            self.pre_norm_2 = ld.norm(p + "pre_ffw_norm_2.weight")
            self.post_norm_1 = ld.norm(p + "post_ffw_norm_1.weight")
            self.post_norm_2 = ld.norm(p + "post_ffw_norm_2.weight")
            self.experten = Experten(ld, p, _gelu)
            n_exp = self.router.n_out
            self.down_scale = (ld.norm(p + "ffn_down_exps.scale").reshape(-1)
                               if ld.has(p + "ffn_down_exps.scale") else None)
            if (len(self.experten) != n_exp or not self.experten.pruefen()
                    or self.router_scale.numel() != hp["n_embd"]
                    or (self.down_scale is not None and self.down_scale.numel() != n_exp)
                    or not 0 < self.n_used <= n_exp):
                raise GGUFError(f"layer {i}: inconsistent expert tensors")
        self.ple = hp["n_embd_per_layer"] > 0
        if self.ple:
            self.inp_gate = ld.linear(p + "inp_gate.weight")        # (E -> Epl)
            self.proj = ld.linear(p + "proj.weight")                # (Epl -> E)
            self.post_norm = ld.norm(p + "post_norm.weight")
        self.out_scale = ld.norm(p + "layer_output_scale.weight") if ld.has(p + "layer_output_scale.weight") else None

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, ple: Optional[torch.Tensor],
                 spans=None) -> torch.Tensor:
        L = h.shape[0]
        x = rms_norm(h, self.attn_norm, self.eps)
        if self.qkv is not None:
            q, k, v = self.qkv(x).float().split([self.H * self.D, self.Hkv * self.D, self.Hkv * self.D], dim=-1)
        else:
            q = self.q(x).float()
        q = q.reshape(L, self.H, self.D)
        q = self.rope(rms_norm(q, self.q_norm, self.eps), pos_idx)
        if self.has_kv:
            if self.qkv is None:
                k = self.k(x).float()
                v = self.v(x).float() if self.v is not None else k
            k, v = k.reshape(L, self.Hkv, self.D), v.reshape(L, self.Hkv, self.D)
            k = self.rope(rms_norm(k, self.k_norm, self.eps), pos_idx)
            v = rms_norm(v, 1.0, self.eps)
            self.cache.write(k, v, pos_idx)
        o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, 1.0, self.window, key_pos=self.cache.pos,
                      spans=spans)
        o = rms_norm(self.o(o.reshape(L, -1)), self.post_attn_norm, self.eps)
        h = h + o
        f = rms_norm(h, self.ffn_norm, self.eps)
        f = self.down(_gelu(self.gate(f)) * self.up(f))
        if self.moe:
            f = rms_norm(f, self.post_norm_1, self.eps) + rms_norm(self._experten(h), self.post_norm_2, self.eps)
        h = h + rms_norm(f, self.post_ffn_norm, self.eps)
        if self.ple:
            g = _gelu(self.inp_gate(h).float()) * ple                 # (L, Epl)
            h = h + rms_norm(self.proj(g), self.post_norm, self.eps)
        if self.out_scale is not None:
            h = h * self.out_scale
        return h

    def _experten(self, h: torch.Tensor) -> torch.Tensor:
        L, E = h.shape
        r = rms_norm(h, 1.0, self.eps) * (self.router_scale / math.sqrt(E))
        w = torch.softmax(self.router(r.to(h.dtype)).float(), dim=-1)
        w, idx = w.topk(self.n_used, dim=-1)
        w = w / w.sum(-1, keepdim=True)
        if self.down_scale is not None:
            w = w * self.down_scale[idx]
        return self.experten(rms_norm(h, self.pre_norm_2, self.eps), idx, w)


class Gemma4Model:
    arch_names = ("gemma4",)

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, ple_ram: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        """ple_ram: keep the per-layer embedding table (lookup only, ~1.8 GiB for E4B) in RAM;
        the rows of the current tokens are copied into a fixed GPU buffer before each step."""
        a = gg.architecture
        k = lambda s: f"{a}.{s}"
        n_layer = int(gg.require(k("block_count")))
        n_embd = int(gg.require(k("embedding_length")))
        n_heads = _je_schicht(gg.require(k("attention.head_count")), n_layer, "head_count", 1024)
        hp = dict(
            n_layer=n_layer, n_embd=n_embd, n_heads=n_heads,
            n_kv_heads=_je_schicht(gg.get(k("attention.head_count_kv"), n_heads), n_layer, "head_count_kv", 1024),
            n_expert_used=int(gg.get(k("expert_used_count"), 0)),
            head_dim=int(gg.require(k("attention.key_length"))),
            head_dim_swa=int(gg.require(k("attention.key_length_swa"))),
            eps=float(gg.get(k("attention.layer_norm_rms_epsilon"), 1e-6)),
            rope_base=float(gg.get(k("rope.freq_base"), 1_000_000.0)),
            rope_base_swa=float(gg.get(k("rope.freq_base_swa"), 10_000.0)),
            rope_dim=int(gg.get(k("rope.dimension_count"), 0)) or int(gg.require(k("attention.key_length"))),
            rope_dim_swa=int(gg.get(k("rope.dimension_count_swa"), 0)) or int(gg.require(k("attention.key_length_swa"))),
            window=int(gg.require(k("attention.sliding_window"))),
            n_embd_per_layer=int(gg.get(k("embedding_length_per_layer_input"), 0)),
            softcap=float(gg.get(k("final_logit_softcapping"), 0.0)),
        )
        if gg.require(k("attention.value_length")) != hp["head_dim"] or gg.require(k("attention.value_length_swa")) != hp["head_dim_swa"]:
            raise GGUFError("gemma4 requires key_length == value_length")
        swa = gg.require(k("attention.sliding_window_pattern"))
        if not isinstance(swa, list) or len(swa) != n_layer or not all(isinstance(b, bool) for b in swa):
            raise GGUFError("invalid sliding_window_pattern")
        n_shared = int(gg.get(k("attention.shared_kv_layers"), 0))
        if not (0 <= n_shared < n_layer) or not (0 < n_layer <= 512 and 0 < n_embd <= 65536
                                                 and 0 < hp["head_dim"] <= 1024 and 0 < hp["window"] <= 1 << 20):
            raise GGUFError("implausible hyper-parameters")
        if not 0 <= hp["n_expert_used"] <= 1024:
            raise GGUFError("implausible expert_used_count")
        self.hp = hp
        self.n_ctx = n_ctx
        self.device = device
        n_kv_layers = n_layer - n_shared

        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.lm_head = head if tied else ld.linear("output.weight")
        self.out_norm = ld.norm("output_norm.weight")
        self.embd_scale = math.sqrt(n_embd)
        # image tokens read bidirectionally – all Gemma 4 sizes except E2B/E4B (llama.cpp
        # mtmd_decode_use_non_causal: text widths 1536 and 2560 stay causal)
        self.images_bidirectional = n_embd not in (1536, 2560)
        self.n_pl = hp["n_embd_per_layer"]
        self.ple_ram = False
        if self.n_pl > 0:
            self.ple_ram = ple_ram and device.type == "cuda"
            self.pl_embed, _ = ld.embedding("per_layer_token_embd.weight", tied_head=False,
                                            device="cpu" if self.ple_ram else None)
            if self.pl_embed.n_embd != self.n_pl * n_layer:
                raise GGUFError("per_layer_token_embd has the wrong width")
            rows = self.pl_embed.rows
            self.ple_ram = self.ple_ram and rows is not None and rows.device.type == "cpu"
            if self.ple_ram:
                # fixed buffer with the packed rows of the current step; CUDA graphs read and
                # dequantise it on the GPU (only ~7 KB per token cross the bus)
                self._pl_raw = torch.zeros(PREFILL_CHUNK, self.pl_embed.rows.shape[1], device=device,
                                           dtype=torch.uint8)
            self.pl_proj = ld.linear("per_layer_model_proj.weight")
            self.pl_norm = ld.norm("per_layer_proj_norm.weight")
        freqs = ld.norm("rope_freqs.weight") if ld.has("rope_freqs.weight") else None
        rope_full = RoPE(hp["rope_dim"], hp["rope_base"], device, n_ctx, neox=True, freq_factors=freqs)
        rope_swa = RoPE(hp["rope_dim_swa"], hp["rope_base_swa"], device, n_ctx, neox=True)
        self.layers: List[_Layer] = []
        last_kv = {True: None, False: None}                      # last KV-owning layer per type
        for i in range(n_layer):
            is_swa = swa[i]
            has_kv = i < n_kv_layers
            layer = _Layer(ld, i, hp, is_swa, has_kv, rope_swa if is_swa else rope_full, n_ctx, last_kv[is_swa])
            if has_kv:
                last_kv[is_swa] = layer
            self.layers.append(layer)
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        self.moe = any(l.moe for l in self.layers)
        # expert choice depends on the data -> not recordable as a CUDA graph
        # single steps as CUDA graph: possible when every MoE layer picks its experts on the GPU
        ohne_sync = all(l.experten.graphfaehig for l in self.layers if l.moe)
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" and ohne_sync else None

    # -- state (KV slots are overwritten by position; nothing else is stateful) --
    def reset(self):
        for layer in self.layers:
            if layer.has_kv and layer.cache.pos is not None:
                layer.cache.pos.fill_(-1)

    def snapshot_state(self):
        return None

    # -- prefix cache ----------------------------------------------------------
    def resume_from(self, common: int, old_len: int) -> Optional[int]:
        """Full-attention caches hold every position; the sliding-window rings hold the
        last `size` positions.  Continuing at `common` needs [common - window, common) in
        the rings, i.e. at most PREFILL_CHUNK tokens may be taken back."""
        return common if old_len - common < PREFILL_CHUNK else None

    def checkpoint_state(self):
        """Copies of the rings (~40 MB for E4B); full-attention caches need no copy –
        their slots below the checkpoint are not overwritten while the prefix matches."""
        return [(l.cache.k.clone(), l.cache.v.clone(), l.cache.pos.clone())
                for l in self.layers if l.has_kv and l.cache.pos is not None]

    def restore_checkpoint(self, snap):
        rings = [l.cache for l in self.layers if l.has_kv and l.cache.pos is not None]
        for cache, (k, v, pos) in zip(rings, snap):
            cache.k.copy_(k)
            cache.v.copy_(v)
            cache.pos.copy_(pos)

    def restore_state(self, snap):
        pass

    # -- compute -------------------------------------------------------------
    supports_images = True

    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, inject=None) -> torch.Tensor:
        """inject: (row indices, embeddings) – image embeddings replace the scaled token
        embeddings at these rows before the per-layer projection (llama.cpp: the per-layer
        table uses the pad token, the projection the raw embeddings)."""
        L = tok.numel()
        h = self.embed(tok) * self.embd_scale                            # (L, E)
        spans = None
        if inject is not None:                    # image rows: raw embeddings, not scaled
            rows, emb = inject
            h = h.index_copy(0, rows, emb.to(h.device, h.dtype))
            if self.images_bidirectional:
                spans = _laeufe(rows.tolist(), int(pos_idx[0]))
        ple = None
        if self.n_pl > 0:
            n_layer = self.hp["n_layer"]
            if self.ple_ram:
                rows = dequantize(self._pl_raw[:L].reshape(-1), self.pl_embed.ggml_type,
                                  L * self.pl_embed.n_embd, torch.float32)
            else:
                rows = self.pl_embed(tok)
            pl = rows.view(L, n_layer, self.n_pl) * math.sqrt(self.n_pl)
            proj = (self.pl_proj(h).float() / self.embd_scale).view(L, n_layer, self.n_pl)
            proj = rms_norm(proj, self.pl_norm, self.hp["eps"])
            ple = (proj + pl) * (1.0 / math.sqrt(2.0))                   # (L, n_layer, Epl)
        for i, layer in enumerate(self.layers):
            h = layer(h, pos_idx, kv_len, ple[:, i] if ple is not None else None, spans)
        h = rms_norm(h[-1:], self.out_norm, self.hp["eps"])
        logits = self.lm_head(h)[0].float()
        cap = self.hp["softcap"]
        if cap > 0:
            logits = cap * torch.tanh(logits / cap)
        return logits

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int, inject=None) -> torch.Tensor:
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if len(tokens) > PREFILL_CHUNK:
            raise ValueError(f"at most {PREFILL_CHUNK} tokens per call (sliding-window ring cache)")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")
        if self.ple_ram:
            packed = self.pl_embed.rows.index_select(0, torch.tensor(tokens, dtype=torch.long))
            self._pl_raw[:len(tokens)].copy_(packed)
        if len(tokens) == 1 and self.graph is not None and inject is None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens), inject)
