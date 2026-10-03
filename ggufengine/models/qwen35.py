"""
Qwen3.5 (arch "qwen35"): hybrid of Gated DeltaNet linear-attention layers and
gated full-attention layers (every `full_attention_interval`-th layer), SwiGLU
MLP, tied embeddings.

Tensor layout notes (from llama.cpp's converter, conversion/qwen.py):
* attn_qkv = [q (Hk*Dk) | k (Hk*Dk) | v (Hv*Dv)] contiguous.
* V heads are stored in *tiled* order: v-head j belongs to k-head j % Hk,
  so q/k are expanded with `repeat` (tile), not repeat_interleave.
* ssm_a is already -exp(A_log); ssm_dt.bias is dt_bias.
* Layer norm weights already include the +1 of the zero-centred RMSNorm,
  ssm_norm (the gated group norm) does not.
* attn_q of the attention layers holds [query | gate] per head (2*head_dim).

All state (recurrent S, conv window, KV cache) is updated in place so that a
single-token step can be captured as a CUDA graph.
"""
from __future__ import annotations

from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import GraphDecoder, KVCache, Linear, WeightLoader, attention, rms_norm, swiglu_mlp


def _l2norm(x: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    return x * torch.rsqrt(x.pow(2).sum(-1, keepdim=True) + eps)


BLOCK_AB = 32           # ab so vielen Token blockweise statt Token für Token
BLOCK = 64              # Token je Block


def delta_blockweise(q, k, v, g, beta, S, C: int = BLOCK):
    """Gated Delta Rule über eine ganze Folge in Blöcken zu C Token (der „chunked“
    Rechenweg der Original-Kernels): gleiches Ergebnis wie die Token-Schleife, aber
    wenige große Matrix-Rechnungen statt L kleiner Schritte.

    q, k: (L, H, Dk), v: (L, H, Dv), g: (L, H) log. Zerfall (<= 0), beta: (L, H);
    S: Zustand (H, Dk, Dv), wird fortgeschrieben. Ergebnis (L, H, Dv)."""
    L, H, Dk = k.shape
    Dv = v.shape[-1]
    pad = (-L) % C
    q, k, v = (x.transpose(0, 1) for x in (q, k, v))          # (H, L, D)
    g, beta = g.t(), beta.t()                                 # (H, L)
    if pad:                                                   # Auffüllen: beta 0 = keine Änderung
        q, k, v = (F.pad(x, (0, 0, 0, pad)) for x in (q, k, v))
        g, beta = F.pad(g, (0, pad)), F.pad(beta, (0, pad))
    n = (L + pad) // C
    q, k = q.reshape(H, n, C, Dk), k.reshape(H, n, C, Dk)
    v = v.reshape(H, n, C, Dv)
    g = g.reshape(H, n, C).cumsum(-1)                         # Zerfall ab Blockanfang
    beta = beta.reshape(H, n, C, 1)
    k_beta, v_beta = k * beta, v * beta
    dev = q.device
    ab_diag = torch.triu(torch.ones(C, C, dtype=torch.bool, device=dev))
    oberhalb = torch.triu(torch.ones(C, C, dtype=torch.bool, device=dev), diagonal=1)
    zerfall = (g.unsqueeze(-1) - g.unsqueeze(-2)).tril().exp().tril()        # (H, n, C, C)
    # T = (I + tril(k_beta k^T * zerfall, -1))^-1: untere Einheits-Dreiecksmatrix, ein Löser-Aufruf
    eins = torch.eye(C, dtype=q.dtype, device=dev)
    N = ((k_beta @ k.transpose(-1, -2)) * zerfall).masked_fill(ab_diag, 0) + eins
    T = torch.linalg.solve_triangular(N, eins.expand_as(N), upper=False, unitriangular=True)
    u = T @ v_beta                                            # (H, n, C, Dv)
    w = T @ (k_beta * g.exp().unsqueeze(-1))                  # (H, n, C, Dk)
    out = torch.empty(H, n, C, Dv, dtype=v.dtype, device=dev)
    for c in range(n):
        qc, kc, gc = q[:, c], k[:, c], g[:, c]
        innen = (qc @ kc.transpose(-1, -2) * zerfall[:, c]).masked_fill(oberhalb, 0)
        v_neu = u[:, c] - w[:, c] @ S
        out[:, c] = (qc * gc.exp().unsqueeze(-1)) @ S + innen @ v_neu
        S.mul_(gc[:, -1].exp().view(H, 1, 1))
        S.add_((kc * (gc[:, -1:] - gc).exp().unsqueeze(-1)).transpose(-1, -2) @ v_neu)
    return out.reshape(H, n * C, Dv)[:, :L].transpose(0, 1)


class _GatedDeltaNetLayer:
    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.Hk, self.Hv = hp["n_k_heads"], hp["n_v_heads"]
        self.Dk, self.Dv = hp["d_k"], hp["d_v"]
        self.K = hp["conv_kernel"]
        self.eps = hp["eps"]
        self.qkv = ld.linear(p + "attn_qkv.weight")
        self.z = ld.linear(p + "attn_gate.weight")
        self.beta = ld.linear(p + "ssm_beta.weight")
        self.alpha = ld.linear(p + "ssm_alpha.weight")
        self.a = ld.norm(p + "ssm_a")                              # (Hv,)  = -exp(A_log)
        self.dt_bias = ld.norm(p + "ssm_dt.bias")                  # (Hv,)
        conv = ld.norm(p + "ssm_conv1d.weight")                    # (C, K)
        self.conv_channels = conv.shape[0]
        if conv.shape != (2 * self.Hk * self.Dk + self.Hv * self.Dv, self.K):
            raise GGUFError(f"unexpected conv1d shape {tuple(conv.shape)} in layer {i}")
        self.conv_w = conv.unsqueeze(1).contiguous()               # (C,1,K) for depthwise conv1d
        self.norm_w = ld.norm(p + "ssm_norm.weight")               # (Dv,)
        self.out = ld.linear(p + "ssm_out.weight")
        dev = self.a.device
        self.conv_state = torch.zeros(self.K - 1, self.conv_channels, device=dev, dtype=torch.float32)
        self.S = torch.zeros(self.Hv, self.Dk, self.Dv, device=dev, dtype=torch.float32)

    def reset(self):
        with torch.inference_mode():
            self.conv_state.zero_()
            self.S.zero_()

    def snapshot(self):
        return (self.conv_state.clone(), self.S.clone())

    def restore(self, snap):
        self.conv_state.copy_(snap[0])
        self.S.copy_(snap[1])

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        L = x.shape[0]
        qkv = self.qkv(x).float()                                   # (L, C)
        z = self.z(x).float()                                       # (L, Hv*Dv)
        beta = torch.sigmoid(self.beta(x).float())                  # (L, Hv)
        g = self.a * F.softplus(self.alpha(x).float() + self.dt_bias)   # (L, Hv), <= 0

        # causal depthwise conv over time with carried window
        full = torch.cat([self.conv_state, qkv], dim=0)             # (K-1+L, C)
        self.conv_state.copy_(full[-(self.K - 1):])
        conv = F.conv1d(full.t().unsqueeze(0), self.conv_w, groups=self.conv_channels)[0].t()  # (L, C)
        conv = F.silu(conv)

        nk = self.Hk * self.Dk
        q = conv[:, :nk].view(L, self.Hk, self.Dk)
        k = conv[:, nk:2 * nk].view(L, self.Hk, self.Dk)
        v = conv[:, 2 * nk:].view(L, self.Hv, self.Dv)
        q = _l2norm(q) * (self.Dk ** -0.5)
        k = _l2norm(k)
        if self.Hv != self.Hk:
            rep = self.Hv // self.Hk
            q = q.repeat(1, rep, 1)                                 # tiled: head j -> k-head j % Hk
            k = k.repeat(1, rep, 1)

        S = self.S                                                  # (Hv, Dk, Dv), updated in place
        if L >= BLOCK_AB:
            out = delta_blockweise(q, k, v, g, beta, S)
            o = rms_norm(out, self.norm_w, self.eps) * F.silu(z.view(L, self.Hv, self.Dv))
            return self.out(o.reshape(L, -1))
        out = torch.empty(L, self.Hv, self.Dv, device=x.device, dtype=torch.float32)
        gexp = torch.exp(g).view(L, self.Hv, 1, 1)
        beta = beta.view(L, self.Hv, 1, 1)
        for t in range(L):
            S.mul_(gexp[t])
            kt = k[t].unsqueeze(1)                                  # (Hv,1,Dk)
            delta = (v[t].unsqueeze(1) - torch.bmm(kt, S)) * beta[t]
            S.baddbmm_(kt.transpose(1, 2), delta)                   # S += k^T delta
            out[t] = torch.bmm(q[t].unsqueeze(1), S)[:, 0]

        # gated RMS norm per head, then output projection
        o = rms_norm(out, self.norm_w, self.eps) * F.silu(z.view(L, self.Hv, self.Dv))
        return self.out(o.reshape(L, -1))


class MRoPE:
    """Interleaved multimodal RoPE (Qwen3-VL / Qwen3.5): every frequency pair of the
    rotary dims belongs to one position axis – time, height or width – in the pattern
    t,h,w,t,h,w,… (`sections` pairs each, the rest time).  Text tokens have the same
    position on all three axes, which is exactly ordinary 1-D RoPE.
    Positions are computed per call from (L, 3) tensors – CUDA-graph safe."""

    def __init__(self, dim: int, base: float, sections, device):
        self.dim = dim
        half = dim // 2
        self.inv_freq = 1.0 / (base ** (torch.arange(0, dim, 2, device=device, dtype=torch.float32) / dim))
        axis = torch.zeros(half, dtype=torch.long)
        s = list(sections) + [0, 0, 0]
        for i in range(half):
            if i % 3 == 1 and i < 3 * s[1]:
                axis[i] = 1
            elif i % 3 == 2 and i < 3 * s[2]:
                axis[i] = 2
        self.axis = axis.to(device)

    def __call__(self, x: torch.Tensor, pos3: torch.Tensor) -> torch.Tensor:
        """x: (L, H, D) float32, pos3: (L, 3) long.  NeoX rotation of x[..., :dim]."""
        ang = pos3.index_select(1, self.axis).float() * self.inv_freq      # (L, dim/2)
        cos, sin = ang.cos().unsqueeze(1), ang.sin().unsqueeze(1)
        h = self.dim // 2
        xr, xp = x[..., :self.dim], x[..., self.dim:]
        x1, x2 = xr[..., :h], xr[..., h:]
        xr = torch.cat([x1 * cos - x2 * sin, x1 * sin + x2 * cos], dim=-1)
        return torch.cat([xr, xp], dim=-1) if xp.shape[-1] else xr


class _AttentionLayer:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: RoPE, n_ctx: int):
        p = f"blk.{i}."
        self.H, self.Hkv, self.D = hp["n_heads"], hp["n_kv_heads"], hp["head_dim"]
        self.eps = hp["eps"]
        self.rope = rope
        self.q = ld.linear(p + "attn_q.weight")                     # (H*2*D, E)
        self.k = ld.linear(p + "attn_k.weight")
        self.v = ld.linear(p + "attn_v.weight")
        self.o = ld.linear(p + "attn_output.weight")
        self.q_norm = ld.norm(p + "attn_q_norm.weight")
        self.k_norm = ld.norm(p + "attn_k_norm.weight")
        if self.q.n_out != self.H * 2 * self.D:
            raise GGUFError(f"attn_q shape {tuple(self.q.w.shape)} does not match gated attention layout")
        self.cache = KVCache(self.Hkv, self.D, n_ctx, ld.device, ld.dtype, ld.kv_bits)
        self.scale = self.D ** -0.5

    def reset(self):
        pass                                                        # slots are overwritten by position

    def snapshot(self):
        return None

    def restore(self, snap):
        pass

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, pos3: torch.Tensor) -> torch.Tensor:
        """pos_idx: cache slots (causal order); pos3: rotary positions (L, 3)."""
        L = x.shape[0]
        qg = self.q(x).float().view(L, self.H, 2, self.D)
        q, gate = qg[:, :, 0], qg[:, :, 1]
        k = self.k(x).float().view(L, self.Hkv, self.D)
        v = self.v(x).view(L, self.Hkv, self.D)
        q = self.rope(rms_norm(q, self.q_norm, self.eps), pos3)
        k = self.rope(rms_norm(k, self.k_norm, self.eps), pos3)
        self.cache.write(k, v, pos_idx)
        o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, self.scale).float() * torch.sigmoid(gate)
        return self.o(o.reshape(L, -1))


class _Block:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, mixer):
        p = f"blk.{i}."
        self.mixer = mixer
        self.eps = hp["eps"]
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.ffn_norm = ld.norm(p + "post_attention_norm.weight")
        self.gate = ld.linear(p + "ffn_gate.weight")
        self.up = ld.linear(p + "ffn_up.weight")
        self.down = ld.linear(p + "ffn_down.weight")
        self.is_attn = isinstance(mixer, _AttentionLayer)

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, pos3: torch.Tensor) -> torch.Tensor:
        x = rms_norm(h, self.attn_norm, self.eps)
        h = h + (self.mixer(x, pos_idx, kv_len, pos3) if self.is_attn else self.mixer(x)).float()
        x = rms_norm(h, self.ffn_norm, self.eps)
        return h + swiglu_mlp(x, self.gate, self.up, self.down).float()


class Qwen35Model:
    arch_names = ("qwen35",)
    prefill_chunk = 2048            # no sliding-window ring: long chunks read faster (qlinear)
    supports_images = True
    uses_mrope = True

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a not in self.arch_names:
            raise GGUFError(f"{a} is not a qwen35 model")
        k = lambda s: f"{a}.{s}"
        n_layer = int(gg.require(k("block_count")))
        n_embd = int(gg.require(k("embedding_length")))
        n_heads = int(gg.require(k("attention.head_count")))
        hp = dict(
            n_layer=n_layer, n_embd=n_embd, n_heads=n_heads,
            n_kv_heads=int(gg.get(k("attention.head_count_kv"), n_heads)),
            head_dim=int(gg.get(k("attention.key_length"), n_embd // n_heads)),
            eps=float(gg.get(k("attention.layer_norm_rms_epsilon"), 1e-6)),
            rope_dim=int(gg.get(k("rope.dimension_count"), 64)),
            rope_base=float(gg.get(k("rope.freq_base"), 10_000_000.0)),
            conv_kernel=int(gg.require(k("ssm.conv_kernel"))),
            d_k=int(gg.require(k("ssm.state_size"))),
            n_k_heads=int(gg.require(k("ssm.group_count"))),
            n_v_heads=int(gg.require(k("ssm.time_step_rank"))),
        )
        inner = int(gg.require(k("ssm.inner_size")))
        if inner % hp["n_v_heads"]:
            raise GGUFError("ssm.inner_size not divisible by number of v heads")
        hp["d_v"] = inner // hp["n_v_heads"]
        if hp["n_v_heads"] % hp["n_k_heads"] or hp["n_heads"] % hp["n_kv_heads"]:
            raise GGUFError("head counts are not multiples of the kv/k head counts")
        if not (0 < n_layer <= 512 and 0 < n_embd <= 65536 and 0 < hp["head_dim"] <= 1024
                and 1 <= hp["conv_kernel"] <= 16):
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
        # rotary position of text = cache slot + offset (the offset moves when images
        # take fewer positions than tokens); one element, read by the CUDA graphs
        self._roff = torch.zeros(1, dtype=torch.long, device=device)
        self.blocks: List[_Block] = []
        for i in range(n_layer):
            if ld.has(f"blk.{i}.attn_q.weight"):
                mixer = _AttentionLayer(ld, i, hp, rope, n_ctx)
            elif ld.has(f"blk.{i}.ssm_a"):
                mixer = _GatedDeltaNetLayer(ld, i, hp)
            else:
                raise GGUFError(f"layer {i}: neither attention nor delta-net tensors found")
            self.blocks.append(_Block(ld, i, hp, mixer))
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" else None

    # -- state ---------------------------------------------------------------
    def reset(self):
        for b in self.blocks:
            b.mixer.reset()
        self._roff.zero_()

    def set_rope_offset(self, offset: int) -> None:
        self._roff.fill_(int(offset))

    def snapshot_state(self):
        return [b.mixer.snapshot() for b in self.blocks]

    def restore_state(self, snap):
        for b, s in zip(self.blocks, snap):
            b.mixer.restore(s)

    # -- prefix cache ----------------------------------------------------------
    def resume_from(self, common: int, old_len: int) -> Optional[int]:
        """The recurrent state only exists for the end of the cached tokens: continue
        only when the new prompt extends them."""
        return common if common == old_len else None

    def checkpoint_state(self):
        return self.snapshot_state()

    def restore_checkpoint(self, snap):
        self.restore_state(snap)

    # -- compute -------------------------------------------------------------
    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, inject=None,
             pos3: Optional[torch.Tensor] = None) -> torch.Tensor:
        """inject: (rows, embeddings) replacing token embeddings (images); pos3: explicit
        rotary positions (L, 3), otherwise slot + offset on all three axes."""
        h = self.embed(tok)
        if inject is not None:
            rows, emb = inject
            h = h.index_copy(0, rows, emb.to(h.device, h.dtype))
        if pos3 is None:
            pos3 = (pos_idx + self._roff).unsqueeze(1).expand(-1, 3)
        for blk in self.blocks:
            h = blk(h, pos_idx, kv_len, pos3)
        h = rms_norm(h[-1:], self.out_norm, self.hp["eps"])
        return self.lm_head(h)[0].float()

    def _check(self, tokens: List[int], pos: int):
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int, inject=None, pos3=None) -> torch.Tensor:
        """Process `tokens` at cache slots pos.., return logits of the last token (float32, (V,)).
        pos3: rotary positions (list of (t, h, w) or tensor) when they differ from the slots."""
        self._check(tokens, pos)
        if len(tokens) == 1 and self.graph is not None and inject is None and pos3 is None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        if pos3 is not None and not isinstance(pos3, torch.Tensor):
            pos3 = torch.tensor(pos3, device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens), inject, pos3)
