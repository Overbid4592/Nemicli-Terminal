"""
Ling 3.0 / BailingMoeV3 (arch "bailingmoe3"): hybrid of Kimi Delta Attention (KDA) layers
and Multi-head Latent Attention (MLA) layers (in the GGUF: head_count_kv = 1 for MLA,
0 for KDA), mixture of experts with sigmoid router, expert bias, group-limited top-k and
one shared expert; the first `leading_dense_block_count` layers use a dense SwiGLU MLP.

Tensor layout notes (llama.cpp conversion/bailingmoe3.py):
* ssm_a = exp(A_log) (H, 1); ssm_dt.bias = dt_bias (H*D).
* ssm_f_a / ssm_g_a: full-rank KDA decay-gate / output-gate projections.
* KDA q, k and v each have their own causal short conv (ssm_conv1d_{q,k,v}: (C, 1, K)).
* attn_k_b (H, kv_lora, nope) = key part of kv_b_proj, transposed per head;
  attn_v_b (H, v, kv_lora); attn_kv_a_mqa = [latent (kv_lora) | k_rope].
* The MLA rope dims rotate adjacent pairs (HF "rope_interleave").

KDA gate ("safe gate"): g = lower_bound * sigmoid(exp(A_log) * (f(x) + dt_bias)), a
per-channel log decay in (lower_bound, 0).  MLA runs in the absorbed form: the cache
holds [normed latent | roped k] as one KV head, queries are mapped into the latent space
with attn_k_b and the attention output back with attn_v_b.
"""
from __future__ import annotations

from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import GraphDecoder, KVCache, RoPE, WeightLoader, attention, rms_norm, swiglu_mlp
from .moe import Experten

BLOCK_AB = 32           # from this many tokens: blockwise instead of token by token
EXP_GRENZE = 80.0       # |cumulative log decay| per block; exp(80) still fits float32


def _l2norm(x: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    return x * torch.rsqrt(x.pow(2).sum(-1, keepdim=True) + eps)


def kda_blockweise(q, k, v, g, beta, S, sub: int, teile: int = 4):
    """Delta rule with per-channel decay (KDA) over a whole sequence in blocks of
    C = sub * teile tokens: the same result as the token loop with a few batched matmuls
    per block.

    The pairwise decays exp(G_i - G_j) inside a block are factorised per sub-block of
    `sub` keys around the cumulative decay at its last token, so no factor exceeds
    exp(EXP_GRENZE) as long as sub * |min g| <= EXP_GRENZE; entries above the diagonal
    may overflow and are masked.

    q, k: (L, H, D), v: (L, H, Dv), g: (L, H, D) log decay (<= 0), beta: (L, H);
    S: state (H, D, Dv), updated in place.  Returns (L, H, Dv)."""
    L, H, D = k.shape
    Dv = v.shape[-1]
    C = sub * teile
    pad = (-L) % C
    q, k, v, g = (x.transpose(0, 1) for x in (q, k, v, g))       # (H, L, .)
    beta = beta.t()                                               # (H, L)
    if pad:                                                       # padding: beta 0 = no update, g 0 = no decay
        q, k, v, g = (F.pad(x, (0, 0, 0, pad)) for x in (q, k, v, g))
        beta = F.pad(beta, (0, pad))
    n = (L + pad) // C
    q, k, g = q.reshape(H, n, C, D), k.reshape(H, n, C, D), g.reshape(H, n, C, D)
    v = v.reshape(H, n, C, Dv)
    beta = beta.reshape(H, n, C, 1)
    G = g.cumsum(2)                                               # decay since block start
    kd, qd = k * G.exp(), q * G.exp()                             # tiny values underflow harmlessly
    KK = torch.empty(H, n, C, C, dtype=q.dtype, device=q.device)  # sum_d k_i k_j exp(G_i - G_j)
    QK = torch.empty_like(KK)                                     # sum_d q_i k_j exp(G_i - G_j)
    for b in range(teile):
        s = slice(b * sub, (b + 1) * sub)
        ref = G[:, :, b * sub + sub - 1:b * sub + sub]            # (H, n, 1, D)
        auf = (G - ref).clamp(max=EXP_GRENZE).exp()               # rows i: exp(G_i - ref)
        ab = k[:, :, s] * (ref - G[:, :, s]).exp()                # keys j of the sub-block: exp(ref - G_j) <= 1
        KK[..., s] = (k * auf) @ ab.transpose(-1, -2)
        QK[..., s] = (q * auf) @ ab.transpose(-1, -2)
    dev = q.device
    ab_diag = torch.triu(torch.ones(C, C, dtype=torch.bool, device=dev))
    oberhalb = torch.triu(torch.ones(C, C, dtype=torch.bool, device=dev), diagonal=1)
    eins = torch.eye(C, dtype=q.dtype, device=dev)
    A = (KK * beta).masked_fill(ab_diag, 0)
    T = torch.linalg.solve_triangular(A + eins, eins.expand_as(A), upper=False, unitriangular=True)
    U = T @ (v * beta)                                            # (H, n, C, Dv)
    W = T @ (kd * beta)                                           # (H, n, C, D)
    Qm = QK.masked_fill(oberhalb, 0)                              # (H, n, C, C)
    last = G[:, :, -1:]                                           # (H, n, 1, D)
    kl = k * (last - G).exp()                                     # decay up to block end
    zerfall = last.exp().transpose(-1, -2)                        # (H, n, D, 1)
    out = torch.empty(H, n, C, Dv, dtype=v.dtype, device=dev)
    for c in range(n):
        u = U[:, c] - W[:, c] @ S
        out[:, c] = qd[:, c] @ S + Qm[:, c] @ u
        S.mul_(zerfall[:, c])
        S.add_(kl[:, c].transpose(-1, -2) @ u)
    return out.reshape(H, n * C, Dv)[:, :L].transpose(0, 1)


class _KDALayer:
    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.H, self.D = hp["n_heads"], hp["kda_dim"]
        self.K = hp["conv_kernel"]
        self.eps = hp["eps"]
        self.lower = hp["kda_lower"]
        n = self.H * self.D
        self.n = n
        self.q = ld.linear(p + "attn_q.weight")
        self.k = ld.linear(p + "attn_k.weight")
        self.v = ld.linear(p + "attn_v.weight")
        self.f = ld.linear(p + "ssm_f_a.weight")
        self.g = ld.linear(p + "ssm_g_a.weight")
        self.out = ld.linear(p + "attn_output.weight")
        if any(w.n_out != n for w in (self.q, self.k, self.v, self.f, self.g)) or self.out.n_in != n:
            raise GGUFError(f"layer {i}: KDA projections do not match {self.H} heads x {self.D}")
        self.beta_w = ld.norm(p + "ssm_beta.weight")               # (H, E) float32
        self.a = ld.norm(p + "ssm_a").reshape(-1)                  # (H,) = exp(A_log)
        self.dt_bias = ld.norm(p + "ssm_dt.bias")
        if self.beta_w.shape[0] != self.H or self.a.numel() != self.H or self.dt_bias.numel() != n:
            raise GGUFError(f"layer {i}: KDA gate tensors have unexpected sizes")
        self.dt_bias = self.dt_bias.view(self.H, self.D)
        convs = []
        for x in "qkv":
            w = ld.norm(p + f"ssm_conv1d_{x}.weight")
            if w.numel() != n * self.K:
                raise GGUFError(f"layer {i}: unexpected conv1d_{x} size {tuple(w.shape)}")
            convs.append(w.reshape(n, 1, self.K))
        self.conv_w = torch.cat(convs, 0).contiguous()             # (3n, 1, K), channels [q | k | v]
        self.norm_w = ld.norm(p + "ssm_norm.weight")               # (D,)
        dev = self.a.device
        self.conv_state = torch.zeros(self.K - 1, 3 * n, device=dev, dtype=torch.float32)
        self.S = torch.zeros(self.H, self.D, self.D, device=dev, dtype=torch.float32)
        # sub-block size for kda_blockweise: sub * |lower_bound| <= EXP_GRENZE keeps its factors finite
        self.block = max(1, min(16, int(EXP_GRENZE // abs(self.lower))))

    def reset(self):
        with torch.inference_mode():
            self.conv_state.zero_()
            self.S.zero_()

    def snapshot(self):
        return (self.conv_state.clone(), self.S.clone())

    def restore(self, snap):
        self.conv_state.copy_(snap[0])
        self.S.copy_(snap[1])

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        L, H, D, n = x.shape[0], self.H, self.D, self.n
        proj = torch.cat([self.q(x), self.k(x), self.v(x)], dim=-1).float()   # (L, 3n)
        full = torch.cat([self.conv_state, proj], dim=0)
        self.conv_state.copy_(full[-(self.K - 1):])
        conv = F.silu(F.conv1d(full.t().unsqueeze(0), self.conv_w, groups=3 * n)[0].t())
        q, k, v = (t.reshape(L, H, D) for t in conv.split(n, dim=-1))
        q = _l2norm(q) * (D ** -0.5)
        k = _l2norm(k)
        g = self.lower * torch.sigmoid(self.a.view(H, 1) * (self.f(x).float().view(L, H, D) + self.dt_bias))
        beta = torch.sigmoid(x.float() @ self.beta_w.t())                    # (L, H)

        S = self.S
        if L >= BLOCK_AB:
            out = kda_blockweise(q, k, v, g, beta, S, self.block)
        else:
            out = torch.empty(L, H, D, device=x.device, dtype=torch.float32)
            zerfall = g.exp().unsqueeze(-1)                                   # (L, H, D, 1)
            beta = beta.view(L, H, 1, 1)
            for t in range(L):
                S.mul_(zerfall[t])
                kt = k[t].unsqueeze(1)                                        # (H, 1, D)
                delta = (v[t].unsqueeze(1) - torch.bmm(kt, S)) * beta[t]
                S.baddbmm_(kt.transpose(1, 2), delta)                         # S += k^T delta
                out[t] = torch.bmm(q[t].unsqueeze(1), S)[:, 0]
        o = rms_norm(out, self.norm_w, self.eps) * torch.sigmoid(self.g(x).float().view(L, H, D))
        return self.out(o.reshape(L, n))


class _MLALayer:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: RoPE, n_ctx: int):
        p = f"blk.{i}."
        self.H = hp["n_heads"]
        self.eps = hp["eps"]
        self.nope, self.rope_d = hp["qk_nope"], hp["rope_dim"]
        self.lora, self.vd = hp["kv_lora"], hp["v_dim"]
        self.rope = rope
        H = self.H
        self.q_a = ld.linear(p + "attn_q_a.weight")
        self.q_a_norm = ld.norm(p + "attn_q_a_norm.weight")
        self.q_b = ld.linear(p + "attn_q_b.weight")
        self.kv_a = ld.linear(p + "attn_kv_a_mqa.weight")
        self.kv_a_norm = ld.norm(p + "attn_kv_a_norm.weight")
        self.k_b = ld.get(p + "attn_k_b.weight", torch.float32)       # (H, kv_lora, nope)
        self.v_b = ld.get(p + "attn_v_b.weight", torch.float32)       # (H, v, kv_lora)
        self.gate = ld.linear(p + "attn_gate.weight")                 # (H, E): one gate per head
        self.o = ld.linear(p + "attn_output.weight")
        if (self.q_b.n_out != H * (self.nope + self.rope_d) or self.kv_a.n_out != self.lora + self.rope_d
                or tuple(self.k_b.shape) != (H, self.lora, self.nope)
                or tuple(self.v_b.shape) != (H, self.vd, self.lora)
                or self.gate.n_out != H or self.o.n_in != H * self.vd):
            raise GGUFError(f"layer {i}: MLA tensors do not match the hyper-parameters")
        self.cache = KVCache(1, self.lora + self.rope_d, n_ctx, ld.device, ld.dtype, ld.kv_bits)
        self.scale = (self.nope + self.rope_d) ** -0.5

    def reset(self):
        pass                                                        # slots are overwritten by position

    def snapshot(self):
        return None

    def restore(self, snap):
        pass

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        L, H = x.shape[0], self.H
        q = self.q_b(rms_norm(self.q_a(x), self.q_a_norm, self.eps)).float().view(L, H, self.nope + self.rope_d)
        q_nope, q_rot = q.split([self.nope, self.rope_d], dim=-1)
        kv = self.kv_a(x).float()
        latent = rms_norm(kv[:, :self.lora], self.kv_a_norm, self.eps)
        k_rot = self.rope(kv[:, self.lora:].reshape(L, 1, self.rope_d), pos_idx)
        q_rot = self.rope(q_rot.contiguous(), pos_idx)
        q_lat = torch.einsum("lhn,hcn->lhc", q_nope, self.k_b)                # into the latent space
        key = torch.cat([latent.unsqueeze(1), k_rot], dim=-1)                  # (L, 1, lora + rope)
        # the value is the latent: the first kv_lora features of the stored key
        self.cache.write(key, key, pos_idx)
        o = attention(torch.cat([q_lat, q_rot], dim=-1), *self.cache.kv(kv_len), pos_idx, kv_len, self.scale)
        o = torch.einsum("lhc,hvc->lhv", o[..., :self.lora].float(), self.v_b)   # (L, H, v)
        o = o * torch.sigmoid(self.gate(x).float()).unsqueeze(-1)
        return self.o(o.reshape(L, H * self.vd))


class _MoE:
    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.hp = hp
        self.router = ld.norm(p + "ffn_gate_inp.weight")           # (n_expert, E) float32
        self.bias = ld.norm(p + "exp_probs_b.bias")                # (n_expert,) selection only
        self.experten = Experten(ld, p, F.silu)
        n_exp = hp["n_expert"]
        if not (self.router.shape[0] == n_exp == self.bias.numel() == len(self.experten)
                and self.experten.pruefen()):
            raise GGUFError(f"layer {i}: inconsistent expert tensors")
        self.s_gate = ld.linear(p + "ffn_gate_shexp.weight") if ld.has(p + "ffn_gate_shexp.weight") else None
        if self.s_gate is not None:
            self.s_up = ld.linear(p + "ffn_up_shexp.weight")
            self.s_down = ld.linear(p + "ffn_down_shexp.weight")

    def _auswahl(self, x: torch.Tensor):
        """Router: expert indices (L, n_used) and their weights."""
        hp = self.hp
        L = x.shape[0]
        scores = torch.sigmoid(x.float() @ self.router.t())         # (L, n_expert)
        wahl = scores + self.bias
        gruppen = hp["n_group"]
        if gruppen > 1:
            je = scores.shape[1] // gruppen
            gw = wahl.view(L, gruppen, je).topk(min(2, je), dim=-1)[0].sum(-1)     # (L, groups)
            maske = torch.zeros(L, gruppen, dtype=torch.bool, device=x.device)
            maske.scatter_(1, gw.topk(hp["group_used"], dim=-1)[1], True)
            wahl = wahl.masked_fill(~maske.repeat_interleave(je, dim=1), float("-inf"))
        idx = wahl.topk(hp["n_used"], dim=-1)[1]
        w = scores.gather(1, idx)
        if hp["norm_topk"]:
            w = w / (w.sum(-1, keepdim=True) + 1e-20)
        return idx, w * hp["weights_scale"]

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        idx, w = self._auswahl(x)
        out = self.experten(x, idx, w)
        if self.s_gate is not None:
            out = out + swiglu_mlp(x, self.s_gate, self.s_up, self.s_down).float()
        return out


class _Dense:
    def __init__(self, ld: WeightLoader, i: int):
        p = f"blk.{i}."
        self.gate = ld.linear(p + "ffn_gate.weight")
        self.up = ld.linear(p + "ffn_up.weight")
        self.down = ld.linear(p + "ffn_down.weight")

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        return swiglu_mlp(x, self.gate, self.up, self.down).float()


class _Block:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, mixer, ffn):
        p = f"blk.{i}."
        self.mixer, self.ffn = mixer, ffn
        self.eps = hp["eps"]
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        h = h + self.mixer(rms_norm(h, self.attn_norm, self.eps), pos_idx, kv_len).float()
        return h + self.ffn(rms_norm(h, self.ffn_norm, self.eps))


def _zahl(gg: GGUFFile, key: str, default=None, lo=None, hi=None, ganz: bool = False):
    v = gg.get(key, default) if default is not None else gg.require(key)
    if isinstance(v, (list, tuple, bool)) or not isinstance(v, (int, float)):
        raise GGUFError(f"{key} is not a number")
    if (lo is not None and v < lo) or (hi is not None and v > hi) or v != v:
        raise GGUFError(f"{key} = {v} is implausible")
    return int(v) if ganz else float(v)


class BailingMoe3Model:
    arch_names = ("bailingmoe3",)
    prefill_chunk = 2048            # no sliding-window ring: long chunks read faster (qlinear)

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a not in self.arch_names:
            raise GGUFError(f"{a} is not a bailingmoe3 model")
        k = lambda s: f"{a}.{s}"
        n_layer = _zahl(gg, k("block_count"), lo=1, hi=512, ganz=True)
        kv = gg.get(k("attention.head_count_kv"), 0)
        kv = [int(x) for x in kv] if isinstance(kv, list) else [int(kv)] * n_layer
        if len(kv) != n_layer or any(x not in (0, 1) for x in kv):
            raise GGUFError("attention.head_count_kv must mark each layer with 0 (KDA) or 1 (MLA)")
        if int(gg.get(k("expert_gating_func"), 2)) != 2:
            raise GGUFError("only the sigmoid expert router is supported")
        key_mla = _zahl(gg, k("attention.key_length_mla"), lo=1, hi=1024, ganz=True)
        rope_dim = _zahl(gg, k("rope.dimension_count"), lo=2, hi=1024, ganz=True)
        hp = dict(
            n_layer=n_layer,
            n_embd=_zahl(gg, k("embedding_length"), lo=1, hi=65536, ganz=True),
            n_heads=_zahl(gg, k("attention.head_count"), lo=1, hi=1024, ganz=True),
            eps=_zahl(gg, k("attention.layer_norm_rms_epsilon"), 1e-6, lo=0, hi=1),
            kv_lora=_zahl(gg, k("attention.kv_lora_rank"), lo=1, hi=8192, ganz=True),
            v_dim=_zahl(gg, k("attention.value_length_mla"), lo=1, hi=1024, ganz=True),
            rope_dim=rope_dim, qk_nope=key_mla - rope_dim,
            rope_base=_zahl(gg, k("rope.freq_base"), 10000.0, lo=1, hi=1e12),
            kda_dim=_zahl(gg, k("kda.head_dim"), lo=1, hi=1024, ganz=True),
            kda_lower=_zahl(gg, k("kda.gate_lower_bound"), lo=-1000, hi=-1e-6),
            conv_kernel=_zahl(gg, k("ssm.conv_kernel"), lo=2, hi=16, ganz=True),
            n_expert=_zahl(gg, k("expert_count"), lo=1, hi=1024, ganz=True),
            n_used=_zahl(gg, k("expert_used_count"), lo=1, hi=1024, ganz=True),
            n_group=_zahl(gg, k("expert_group_count"), 1, lo=1, hi=1024, ganz=True),
            group_used=_zahl(gg, k("expert_group_used_count"), 1, lo=1, hi=1024, ganz=True),
            weights_scale=_zahl(gg, k("expert_weights_scale"), 1.0, lo=0, hi=1e4),
            norm_topk=bool(gg.get(k("expert_weights_norm"), True)),
            n_dense=_zahl(gg, k("leading_dense_block_count"), 0, lo=0, hi=n_layer, ganz=True),
        )
        if hp["qk_nope"] < 0 or rope_dim % 2:
            raise GGUFError("key_length_mla / rope dimension do not fit together")
        if (hp["n_used"] > hp["n_expert"] or hp["n_expert"] % hp["n_group"]
                or hp["group_used"] > hp["n_group"]
                or hp["n_used"] > hp["group_used"] * (hp["n_expert"] // hp["n_group"])):
            raise GGUFError("inconsistent expert routing parameters")
        self.hp = hp
        self.n_ctx = n_ctx
        self.device = device

        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.out_norm = ld.norm("output_norm.weight")
        self.lm_head = head if tied else ld.linear("output.weight")
        rope = RoPE(rope_dim, hp["rope_base"], device, max_pos=n_ctx, neox=False)
        self.blocks: List[_Block] = []
        for i in range(n_layer):
            mixer = _MLALayer(ld, i, hp, rope, n_ctx) if kv[i] else _KDALayer(ld, i, hp)
            ffn = _MoE(ld, i, hp) if i >= hp["n_dense"] and ld.has(f"blk.{i}.ffn_gate_inp.weight") else _Dense(ld, i)
            self.blocks.append(_Block(ld, i, hp, mixer, ffn))
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        # single steps as CUDA graph: possible when every MoE layer picks its experts on the GPU
        ohne_sync = all(b.ffn.experten.graphfaehig for b in self.blocks if isinstance(b.ffn, _MoE))
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" and ohne_sync else None

    # -- state ---------------------------------------------------------------
    def reset(self):
        for b in self.blocks:
            b.mixer.reset()

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
    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        h = self.embed(tok)
        for blk in self.blocks:
            h = blk(h, pos_idx, kv_len)
        h = rms_norm(h[-1:], self.out_norm, self.hp["eps"])
        return self.lm_head(h)[0].float()

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int, inject=None) -> torch.Tensor:
        """Process `tokens` at positions pos.., return logits of the last token (float32, (V,))."""
        if inject is not None:
            raise ValueError("bailingmoe3 takes no image inputs")
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")
        if len(tokens) == 1 and self.graph is not None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens))
