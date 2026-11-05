"""
EmbeddingGemma 2 (arch "gemma-embedding2"), following transformers' models/embedding_gemma2:

* an encoder: every layer attends bidirectionally, sliding-window layers to |i - j| <= window / 2
  (the GGUF stores the full width, the reference config the radius)
* Gemma 4 blocks: q/k RMSNorm with weight, v RMSNorm without, attention scale 1.0, post-attention and
  post-FFN norms, GELU (tanh) FFN, per-layer output scalar; sliding-window layers head_dim 256 (rope base
  1e4), full layers head_dim 512 (rope base 1e6), head counts per layer
* projection-only per-layer embeddings (PLE): RMSNorm(proj(embeddings) / sqrt(n_embd)) per layer, no
  token table and no blending scale
* final norm, then `output.weight` maps every row to `embedding_length_out`; mean pooling and L2 norm
  follow outside (sentence-transformers).  Projection and mean commute (no bias).
* text gets <bos> … <eos>; images replace the <|image|> rows between <|image> and <image|> with the
  vision encoder's projected tokens (gemma4v mmproj) before the per-layer projection
"""
from __future__ import annotations

import math
from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import RoPE, WeightLoader, rms_norm

STAPEL_PLATZ = 2048              # so viele Token laufen gepackt in einem Durchgang


def _gelu(x: torch.Tensor) -> torch.Tensor:
    return F.gelu(x, approximate="tanh")


def _je_schicht(wert, n: int, name: str) -> List[int]:
    werte = wert if isinstance(wert, list) else [wert] * n
    if len(werte) != n or not all(isinstance(x, int) and not isinstance(x, bool) and 0 < x <= 1024 for x in werte):
        raise GGUFError(f"invalid {name}")
    return werte


class _Schicht:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, is_swa: bool, rope: RoPE):
        p = f"blk.{i}."
        self.is_swa = is_swa
        self.H, self.Hkv = hp["n_heads"][i], hp["n_kv_heads"][i]
        if self.H % self.Hkv:
            raise GGUFError(f"layer {i}: head_count must be a multiple of head_count_kv")
        self.D = hp["head_dim_swa"] if is_swa else hp["head_dim"]
        self.eps = hp["eps"]
        self.rope = rope
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.q, self.k, self.v = (ld.linear(p + f"attn_{n}.weight") for n in "qkv")
        if self.q.n_out != self.H * self.D or self.k.n_out != self.Hkv * self.D or self.v.n_out != self.Hkv * self.D:
            raise GGUFError(f"layer {i}: attention projections have unexpected sizes")
        self.q_norm = ld.norm(p + "attn_q_norm.weight")
        self.k_norm = ld.norm(p + "attn_k_norm.weight")
        self.o = ld.linear(p + "attn_output.weight")
        self.post_attn_norm = ld.norm(p + "post_attention_norm.weight")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.gate = ld.linear(p + "ffn_gate.weight")
        self.up = ld.linear(p + "ffn_up.weight")
        self.down = ld.linear(p + "ffn_down.weight")
        self.post_ffn_norm = ld.norm(p + "post_ffw_norm.weight")
        self.inp_gate = ld.linear(p + "inp_gate.weight")
        self.proj = ld.linear(p + "proj.weight")
        self.post_norm = ld.norm(p + "post_norm.weight")
        self.out_scale = ld.norm(p + "layer_output_scale.weight") if ld.has(p + "layer_output_scale.weight") else None

    def __call__(self, h: torch.Tensor, pos: torch.Tensor, ple: torch.Tensor, maske: Optional[torch.Tensor],
                 dtype: torch.dtype) -> torch.Tensor:
        L = h.shape[0]
        x = rms_norm(h, self.attn_norm, self.eps)
        q = self.rope(rms_norm(self.q(x).float().view(L, self.H, self.D), self.q_norm, self.eps), pos)
        k = self.rope(rms_norm(self.k(x).float().view(L, self.Hkv, self.D), self.k_norm, self.eps), pos)
        v = rms_norm(self.v(x).float().view(L, self.Hkv, self.D), 1.0, self.eps)
        if self.Hkv != self.H:
            k = k.repeat_interleave(self.H // self.Hkv, dim=1)
            v = v.repeat_interleave(self.H // self.Hkv, dim=1)
        a = F.scaled_dot_product_attention(q.transpose(0, 1).unsqueeze(0).to(dtype),
                                           k.transpose(0, 1).unsqueeze(0).to(dtype),
                                           v.transpose(0, 1).unsqueeze(0).to(dtype),
                                           attn_mask=maske, scale=1.0)[0].transpose(0, 1).reshape(L, -1)
        h = h + rms_norm(self.o(a).float(), self.post_attn_norm, self.eps)
        f = rms_norm(h, self.ffn_norm, self.eps)
        f = self.down(_gelu(self.gate(f).float()) * self.up(f).float()).float()
        h = h + rms_norm(f, self.post_ffn_norm, self.eps)
        g = _gelu(self.inp_gate(h).float()) * ple
        h = h + rms_norm(self.proj(g).float(), self.post_norm, self.eps)
        if self.out_scale is not None:
            h = h * self.out_scale
        return h


class EmbeddingGemma2Model:
    arch_names = ("gemma-embedding2",)
    mit_bos = True                # Folgen beginnen mit <bos> (tokenizer.ggml.add_bos_token)
    supports_images = True
    graph = None

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        k = lambda s: f"{a}.{s}"
        if gg.get(k("attention.causal"), False):
            raise GGUFError("gemma-embedding2 must be non-causal")
        n_layer = int(gg.require(k("block_count")))
        n_embd = int(gg.require(k("embedding_length")))
        n_heads = _je_schicht(gg.require(k("attention.head_count")), n_layer, "head_count")
        hp = dict(
            n_layer=n_layer, n_embd=n_embd, n_heads=n_heads,
            n_kv_heads=_je_schicht(gg.get(k("attention.head_count_kv"), n_heads), n_layer, "head_count_kv"),
            head_dim=int(gg.require(k("attention.key_length"))),
            head_dim_swa=int(gg.require(k("attention.key_length_swa"))),
            eps=float(gg.get(k("attention.layer_norm_rms_epsilon"), 1e-6)),
            window=int(gg.require(k("attention.sliding_window"))),
            n_pl=int(gg.require(k("embedding_length_per_layer_input"))),
            n_out=int(gg.get(k("embedding_length_out"), n_embd)),
        )
        if gg.require(k("attention.value_length")) != hp["head_dim"] or \
                gg.require(k("attention.value_length_swa")) != hp["head_dim_swa"]:
            raise GGUFError("gemma-embedding2 requires key_length == value_length")
        swa = gg.require(k("attention.sliding_window_pattern"))
        if not isinstance(swa, list) or len(swa) != n_layer or not all(isinstance(b, bool) for b in swa):
            raise GGUFError("invalid sliding_window_pattern")
        if not (0 < n_layer <= 512 and 0 < n_embd <= 65536 and 0 < hp["head_dim"] <= 1024 and 0 < hp["window"]
                and 0 < hp["n_pl"] <= 65536 and 0 < hp["n_out"] <= 65536):
            raise GGUFError("implausible hyper-parameters")
        self.hp = hp
        self.n_ctx = int(n_ctx)
        self.device = device
        self.dtype = dtype
        ld = WeightLoader(gg, device, dtype, progress, quant=quant)
        self.embed, _ = ld.embedding("token_embd.weight", tied_head=False)
        self.embd_scale = math.sqrt(n_embd)
        self.pl_proj = ld.linear("per_layer_model_proj.weight")
        if self.pl_proj.n_out != n_layer * hp["n_pl"]:
            raise GGUFError("per_layer_model_proj has the wrong width")
        self.pl_norm = ld.norm("per_layer_proj_norm.weight")
        self.out_norm = ld.norm("output_norm.weight")
        self.out_proj = ld.linear("output.weight")
        if self.out_proj.n_out != hp["n_out"]:
            raise GGUFError("output.weight does not match embedding_length_out")
        rope_full = RoPE(int(gg.get(k("rope.dimension_count"), 0)) or hp["head_dim"],
                         float(gg.get(k("rope.freq_base"), 1_000_000.0)), device, self.n_ctx, neox=True)
        rope_swa = RoPE(int(gg.get(k("rope.dimension_count_swa"), 0)) or hp["head_dim_swa"],
                        float(gg.get(k("rope.freq_base_swa"), 10_000.0)), device, self.n_ctx, neox=True)
        self.layers = [_Schicht(ld, i, hp, swa[i], rope_swa if swa[i] else rope_full) for i in range(n_layer)]
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        self.dim_out = hp["n_out"]

    def forward(self, *_a, **_k):
        raise GGUFError("EmbeddingGemma is an embedding model and cannot write text – it belongs in "
                        "Models/embeddings (choose it with /embeddings)")

    # -- compute ---------------------------------------------------------------
    def _verborgen(self, h: torch.Tensor, pos: torch.Tensor, segmente: Optional[torch.Tensor]) -> torch.Tensor:
        """h: (L, E) input rows -> (L, n_out) projected rows.  `pos`: position per row (from 0 in each
        sequence), `segmente`: sequence index per row when several sequences are packed."""
        L, hp = h.shape[0], self.hp
        ple = rms_norm((self.pl_proj(h).float() / self.embd_scale).view(L, hp["n_layer"], hp["n_pl"]),
                       self.pl_norm, hp["eps"])
        voll = None if segmente is None else segmente[:, None] == segmente[None, :]
        fenster = (pos[:, None] - pos[None, :]).abs() <= hp["window"] // 2      # Radius: halbe Fensterbreite
        if voll is not None:
            fenster = fenster & voll
        if bool(fenster.all()):
            fenster = None
        for i, schicht in enumerate(self.layers):
            h = schicht(h, pos, ple[:, i], fenster if schicht.is_swa else voll, self.dtype)
        return self.out_proj(rms_norm(h, self.out_norm, hp["eps"])).float()

    def eingabe(self, tokens: List[int], inject=None) -> torch.Tensor:
        """Scaled token embeddings; `inject` = (row indices, embeddings) replaces image rows unscaled."""
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        h = self.embed(tok).float() * self.embd_scale
        if inject is not None:
            rows, emb = inject
            h = h.index_copy(0, rows.to(self.device), emb.to(self.device, h.dtype))
        return h

    @torch.inference_mode()
    def einbetten(self, tokens: List[int], pooling: str = "mean", inject=None) -> torch.Tensor:
        """One vector for the whole sequence (float32, not normalised)."""
        if not 0 < len(tokens) <= self.n_ctx:
            raise RuntimeError(f"text must have 1..{self.n_ctx} tokens")
        if pooling not in ("mean", "cls", "last"):
            raise ValueError(f"unknown pooling {pooling!r}")
        pos = torch.arange(len(tokens), device=self.device, dtype=torch.long)
        x = self._verborgen(self.eingabe(tokens, inject), pos, None)
        return x.mean(0) if pooling == "mean" else x[0] if pooling == "cls" else x[-1]

    def stapel_platz(self) -> int:
        return min(self.n_ctx, STAPEL_PLATZ)

    @torch.inference_mode()
    def einbetten_stapel(self, folgen: List[List[int]], pooling: str = "mean") -> List[torch.Tensor]:
        """Several sequences in one pass, each attending only to itself – the same vectors as `einbetten`."""
        laengen = [len(f) for f in folgen]
        if not folgen or min(laengen) < 1:
            raise ValueError("empty sequence")
        if len(folgen) == 1:
            return [self.einbetten(folgen[0], pooling)]
        if sum(laengen) > self.stapel_platz():
            raise RuntimeError(f"packed sequences must have at most {self.stapel_platz()} tokens")
        dev = self.device
        pos = torch.cat([torch.arange(n, device=dev, dtype=torch.long) for n in laengen])
        segmente = torch.repeat_interleave(torch.arange(len(folgen), device=dev), torch.tensor(laengen, device=dev))
        x = self._verborgen(self.eingabe([t for f in folgen for t in f]), pos, segmente)
        raus, start = [], 0
        for n in laengen:
            teil = x[start:start + n]
            raus.append(teil.mean(0) if pooling == "mean" else teil[0] if pooling == "cls" else teil[-1])
            start += n
        return raus
