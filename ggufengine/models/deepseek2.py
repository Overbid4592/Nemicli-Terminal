"""
DeepSeek V2/V3 (arch "deepseek2": DeepSeek V2/V2-Lite/V3/R1, Kimi K2, Moonlight …), following
llama.cpp's src/models/deepseek2.cpp and the reference implementations:

* Multi-head latent attention (MLA): the KV cache holds only the normalised latent plus the
  rotated key part (kv_lora + rope per token); keys and values are never expanded – the query
  is moved into the latent space (k_b) and the attention output back out of it (v_b).
  Q directly (V2-Lite) or through a low-rank pair with norm (q_a, q_a_norm, q_b).  Older files
  store k_b/v_b fused as attn_kv_b; they are split on loading.
* RoPE (adjacent pairs) on the last `rope_dim` features of q and k; YaRN with the attention
  scale mscale^2 (mscale = 1 + yarn_log_multiplier * ln(factor)), cos/sin unscaled.
* FFN: the first `leading_dense_block_count` layers dense, then MoE with shared experts.
  Router V2: softmax, groups ranked by their best expert; V3: sigmoid plus a correction bias
  for the choice only, groups ranked by the sum of their two best; weights optionally
  normalised, times expert_weights_scale.
"""
from __future__ import annotations

import math
from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import (GraphDecoder, KVCache, RoPE, WeightLoader, attention, letzte_zeilen, logit_zeilen,
                     rms_norm, swiglu_mlp, fenster_nachholen, mischer_nachholen, zeilen_merken)
from .moe import Experten

GATING_SOFTMAX, GATING_SIGMOID = 1, 2


def _zahl(gg: GGUFFile, key: str, default=None, lo=None, hi=None, ganz: bool = False):
    v = gg.get(key, default) if default is not None else gg.require(key)
    if isinstance(v, (list, tuple, bool, str)) or not isinstance(v, (int, float)):
        raise GGUFError(f"{key} is not a number")
    if (lo is not None and v < lo) or (hi is not None and v > hi) or v != v:
        raise GGUFError(f"{key} = {v} is implausible")
    return int(v) if ganz else float(v)


KDA_BLOCK_AB = 32       # Kimi Delta Attention: ab so vielen Token blockweise
KDA_BLOCK = 64


class _OhneRope:
    """Kimi Linear: MLA ohne Positionen (der RoPE-Teil des Schlüssels bleibt ungedreht)."""

    def __call__(self, x, pos_idx):
        return x


def kda_blockweise(q, k, v, g, beta, S, C: int = KDA_BLOCK):
    """Kimi Delta Attention über eine Folge in Blöcken zu C Token – gleiches Ergebnis wie die
    Token-Schleife.  q, k (L, H, Dk) normiert, q skaliert; v (L, H, Dv); g (L, H, Dk) log. Zerfall
    je Kanal; beta (L, H); S (H, Dk, Dv) wird fortgeschrieben.  Ergebnis (L, H, Dv)."""
    L, H, Dk = k.shape
    Dv = v.shape[-1]
    out = torch.empty(L, H, Dv, device=q.device, dtype=torch.float32)
    eins = torch.eye(C, device=q.device, dtype=torch.float32)
    for a in range(0, L, C):
        e = min(L, a + C)
        n = e - a
        qc, kc, vc = (x[a:e].transpose(0, 1) for x in (q, k, v))             # (H, n, D)
        gc = g[a:e].transpose(0, 1).cumsum(1)                                  # (H, n, Dk)
        bc = beta[a:e].t().unsqueeze(-1)                                       # (H, n, 1)
        zerfall = torch.exp(gc.unsqueeze(2) - gc.unsqueeze(1))                 # (H, i, j, Dk)
        unter = torch.tril(torch.ones(n, n, dtype=torch.bool, device=q.device), -1)
        bis = torch.tril(torch.ones(n, n, dtype=torch.bool, device=q.device))
        kk = (kc.unsqueeze(2) * kc.unsqueeze(1) * zerfall).sum(-1)            # (H, i, j)
        N = (kk * bc).masked_fill(~unter, 0) + eins[:n, :n]
        T = torch.linalg.solve_triangular(N, eins[:n, :n].expand_as(N), upper=False, unitriangular=True)
        u = T @ (vc * bc)
        w = T @ (kc * bc * gc.exp())
        innen = (qc.unsqueeze(2) * kc.unsqueeze(1) * zerfall).sum(-1).masked_fill(~bis, 0)
        v_neu = u - w @ S
        out[a:e] = ((qc * gc.exp()) @ S + innen @ v_neu).transpose(0, 1)
        letzt = gc[:, -1:]                                                     # (H, 1, Dk)
        S.mul_(letzt.exp().transpose(1, 2))
        S.add_((kc * (letzt - gc).exp()).transpose(1, 2) @ v_neu)
    return out


class _KDA:
    """Kimi Delta Attention (llama.cpp kimi-linear): q/k/v mit eigener kausaler Faltung + SiLU,
    q/k L2-normiert, Zerfall je Kanal g = ssm_a * softplus(f_b(f_a(x)) + dt), beta = sigmoid,
    Delta-Regel; Ausgang RMS-normiert je Kopf mal sigmoid(g_b(g_a(x)))."""

    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.H, self.Dk, self.eps = hp["n_heads"], hp["kda_dim"], hp["eps"]
        self.q, self.k, self.v = (ld.linear(p + f"attn_{n}.weight") for n in "qkv")
        self.o = ld.linear(p + "attn_output.weight")
        self.Dv = self.v.n_out // self.H
        self.K = hp["kda_conv"]
        self.conv = []
        for n, breite in (("q", self.H * self.Dk), ("k", self.H * self.Dk), ("v", self.H * self.Dv)):
            w = ld.norm(p + f"ssm_conv1d_{n}.weight").float()
            if w.numel() != breite * self.K:
                raise GGUFError(f"layer {i}: unexpected ssm_conv1d_{n} size")
            self.conv.append(w.reshape(breite, 1, self.K).contiguous())
        self.f_a, self.f_b = ld.linear(p + "ssm_f_a.weight"), ld.linear(p + "ssm_f_b.weight")
        self.g_a, self.g_b = ld.linear(p + "ssm_g_a.weight"), ld.linear(p + "ssm_g_b.weight")
        self.beta = ld.linear(p + "ssm_beta.weight")
        self.a = ld.norm(p + "ssm_a").float().reshape(-1)                     # (H,) = -exp(A_log)
        self.dt = ld.norm(p + "ssm_dt.bias").float().reshape(-1)              # (H*Dk,)
        self.norm = ld.norm(p + "ssm_norm.weight").float()
        if self.a.numel() != self.H or self.dt.numel() != self.H * self.Dk or self.f_b.n_out != self.H * self.Dk:
            raise GGUFError(f"layer {i}: KDA tensors do not match the head sizes")
        dev = self.a.device
        self.zust = [torch.zeros(self.K - 1, w.shape[0], device=dev, dtype=torch.float32) for w in self.conv]
        self._merk: dict = {}                                     # rows of the last check, by its length
        self.S = torch.zeros(self.H, self.Dk, self.Dv, device=dev, dtype=torch.float32)

    def reset(self):
        with torch.inference_mode():
            for z in self.zust:
                z.zero_()
            self.S.zero_()

    def snapshot(self):
        return [z.clone() for z in self.zust] + [self.S.clone()]

    def restore(self, snap):
        for z, s in zip(self.zust + [self.S], snap):
            z.copy_(s)

    def _falten(self, j: int, x: torch.Tensor) -> torch.Tensor:
        full = torch.cat([self.zust[j], x], dim=0)
        self.zust[j].copy_(full[-(self.K - 1):])
        return F.silu(F.conv1d(full.t().unsqueeze(0), self.conv[j], groups=self.conv[j].shape[0])[0].t())

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        L, H, Dk, Dv = x.shape[0], self.H, self.Dk, self.Dv
        ein = (self.q(x).float(), self.k(x).float(), self.v(x).float())
        q = self._falten(0, ein[0]).view(L, H, Dk)
        k = self._falten(1, ein[1]).view(L, H, Dk)
        v = self._falten(2, ein[2]).view(L, H, Dv)
        q = q / torch.sqrt(q.pow(2).sum(-1, keepdim=True) + self.eps) * Dk ** -0.5
        k = k / torch.sqrt(k.pow(2).sum(-1, keepdim=True) + self.eps)
        g = self.a.view(1, H, 1) * F.softplus((self.f_b(self.f_a(x)).float() + self.dt).view(L, H, Dk))
        beta = torch.sigmoid(self.beta(x).float())                            # (L, H)
        S = self.S
        if L >= KDA_BLOCK_AB:
            o = kda_blockweise(q, k, v, g, beta, S)
        else:
            o = torch.empty(L, H, Dv, device=x.device, dtype=torch.float32)
            if zeilen_merken():
                self._merk[L] = (ein, g, k, v, beta)
            for t in range(L):
                S.mul_(g[t].exp().unsqueeze(-1))
                delta = (v[t] - torch.einsum("hk,hkv->hv", k[t], S)) * beta[t].unsqueeze(-1)
                S.add_(k[t].unsqueeze(-1) * delta.unsqueeze(1))
                o[t] = torch.einsum("hk,hkv->hv", q[t], S)
        tor = torch.sigmoid(self.g_b(self.g_a(x)).float().view(L, H, Dv))
        o = rms_norm(o, self.norm, self.eps) * tor
        return self.o(o.reshape(L, H * Dv))

    def nachholen(self, L: int, n: int) -> None:
        ein, g, k, v, beta = self._merk[L]
        for j in range(3):
            fenster_nachholen(self.zust[j], ein[j], n)
        S = self.S
        for t in range(n):                                                    # the steps of the token loop
            S.mul_(g[t].exp().unsqueeze(-1))
            delta = (v[t] - torch.einsum("hk,hkv->hv", k[t], S)) * beta[t].unsqueeze(-1)
            S.add_(k[t].unsqueeze(-1) * delta.unsqueeze(1))


class _MLA:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: RoPE, n_ctx: int):
        p = f"blk.{i}."
        H = self.H = hp["n_heads"]
        self.eps = hp["eps"]
        self.nope, self.rope_d, self.lora, self.vd = hp["qk_nope"], hp["rope_dim"], hp["kv_lora"], hp["v_dim"]
        self.rope = rope
        if ld.has(p + "attn_q_a.weight"):
            self.q_a = ld.linear(p + "attn_q_a.weight")
            self.q_a_norm = ld.norm(p + "attn_q_a_norm.weight")
            self.q = ld.linear(p + "attn_q_b.weight")
        else:
            self.q_a = self.q_a_norm = None
            self.q = ld.linear(p + "attn_q.weight")
        self.kv_a = ld.linear(p + "attn_kv_a_mqa.weight")
        self.kv_a_norm = ld.norm(p + "attn_kv_a_norm.weight")
        if ld.has(p + "attn_k_b.weight"):
            self.k_b = ld.get(p + "attn_k_b.weight", torch.float32)           # (H, kv_lora, nope)
            self.v_b = ld.get(p + "attn_v_b.weight", torch.float32)           # (H, v, kv_lora)
        else:
            kv_b = ld.get(p + "attn_kv_b.weight", torch.float32)              # (H*(nope+v), kv_lora)
            if kv_b.shape[0] != H * (self.nope + self.vd):
                raise GGUFError(f"layer {i}: attn_kv_b does not match the head sizes")
            kv_b = kv_b.view(H, self.nope + self.vd, self.lora)
            self.k_b = kv_b[:, :self.nope].transpose(1, 2).contiguous()
            self.v_b = kv_b[:, self.nope:].contiguous()
        self.o = ld.linear(p + "attn_output.weight")
        if (self.q.n_out != H * (self.nope + self.rope_d) or self.kv_a.n_out != self.lora + self.rope_d
                or tuple(self.k_b.shape) != (H, self.lora, self.nope) or tuple(self.v_b.shape) != (H, self.vd, self.lora)
                or self.o.n_in != H * self.vd):
            raise GGUFError(f"layer {i}: MLA tensors do not match the hyper-parameters")
        self.cache = KVCache(1, self.lora + self.rope_d, n_ctx, ld.device, ld.dtype, ld.kv_bits)
        self.scale = hp["attn_scale"]

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        L, H = x.shape[0], self.H
        qx = rms_norm(self.q_a(x), self.q_a_norm, self.eps) if self.q_a is not None else x
        q = self.q(qx).float().view(L, H, self.nope + self.rope_d)
        q_nope, q_rot = q.split([self.nope, self.rope_d], dim=-1)
        kv = self.kv_a(x).float()
        latent = rms_norm(kv[:, :self.lora], self.kv_a_norm, self.eps)
        k_rot = self.rope(kv[:, self.lora:].reshape(L, 1, self.rope_d), pos_idx)
        q_rot = self.rope(q_rot.contiguous(), pos_idx)
        q_lat = torch.einsum("lhn,hcn->lhc", q_nope, self.k_b)                # into the latent space
        key = torch.cat([latent.unsqueeze(1), k_rot], dim=-1)                  # (L, 1, kv_lora + rope)
        self.cache.write(key, key, pos_idx)                                    # value = latent part of the key
        o = attention(torch.cat([q_lat, q_rot], dim=-1), *self.cache.kv(kv_len), pos_idx, kv_len, self.scale)
        o = torch.einsum("lhc,hvc->lhv", o[..., :self.lora].float(), self.v_b)   # (L, H, v)
        return self.o(o.reshape(L, H * self.vd))


class _MoE:
    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.hp = hp
        self.router = ld.norm(p + "ffn_gate_inp.weight")                      # (n_expert, E) float32
        self.bias = ld.norm(p + "exp_probs_b.bias") if ld.has(p + "exp_probs_b.bias") else None
        self.experten = Experten(ld, p, F.silu)
        n_exp = hp["n_expert"]
        if not (self.router.shape[0] == n_exp == len(self.experten) and self.experten.pruefen()
                and (self.bias is None or self.bias.numel() == n_exp)):
            raise GGUFError(f"layer {i}: inconsistent expert tensors")
        self.s_gate = ld.linear(p + "ffn_gate_shexp.weight") if ld.has(p + "ffn_gate_shexp.weight") else None
        if self.s_gate is not None:
            self.s_up = ld.linear(p + "ffn_up_shexp.weight")
            self.s_down = ld.linear(p + "ffn_down_shexp.weight")

    def _auswahl(self, x: torch.Tensor):
        hp = self.hp
        L = x.shape[0]
        logits = x.float() @ self.router.t()
        sigmoid = hp["gating"] == GATING_SIGMOID
        scores = torch.sigmoid(logits) if sigmoid else torch.softmax(logits, dim=-1)
        wahl = scores + self.bias if self.bias is not None else scores
        gruppen = hp["n_group"]
        if gruppen > 1:
            je = scores.shape[1] // gruppen
            g = wahl.view(L, gruppen, je)
            gw = g.topk(min(2, je), dim=-1)[0].sum(-1) if sigmoid else g.amax(-1)     # (L, groups)
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
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: RoPE, n_ctx: int):
        p = f"blk.{i}."
        self.eps = hp["eps"]
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.attn = _KDA(ld, i, hp) if ld.has(p + "ssm_f_a.weight") else _MLA(ld, i, hp, rope, n_ctx)
        moe = i >= hp["n_dense"] and ld.has(p + "ffn_gate_inp.weight")
        self.ffn = _MoE(ld, i, hp) if moe else _Dense(ld, i)

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        h = h + self.attn(rms_norm(h, self.attn_norm, self.eps), pos_idx, kv_len).float()
        return h + self.ffn(rms_norm(h, self.ffn_norm, self.eps))


class DeepSeek2Model:
    alle_logits = False      # Logits jeder Zeile statt nur der letzten (spekulatives Prüfen)
    arch_names = ("deepseek2", "kimi-linear")
    prefill_chunk = 2048            # full KV caches only: long chunks read faster (qlinear)

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a not in self.arch_names:
            raise GGUFError(f"{a} is not a deepseek2 model")
        k = lambda s: f"{a}.{s}"
        n_layer = _zahl(gg, k("block_count"), lo=1, hi=512, ganz=True)
        rope_dim = _zahl(gg, k("rope.dimension_count"), lo=2, hi=1024, ganz=True)
        # files with split k_b/v_b store the per-head sizes as *_mla, the others directly
        def mla_oder(name):                      # *_mla, sonst der allgemeine Schlüssel
            if gg.get(k(f"attention.{name}_mla")) is not None:
                return _zahl(gg, k(f"attention.{name}_mla"), lo=1, hi=4096, ganz=True)
            return _zahl(gg, k(f"attention.{name}"), lo=1, hi=4096, ganz=True)
        qk_head, v_dim = mla_oder("key_length"), mla_oder("value_length")
        n_expert = _zahl(gg, k("expert_count"), 0, lo=0, hi=1024, ganz=True)
        kimi = a == "kimi-linear"
        hp = dict(
            n_layer=n_layer,
            n_embd=_zahl(gg, k("embedding_length"), lo=1, hi=65536, ganz=True),
            n_heads=_zahl(gg, k("attention.head_count"), lo=1, hi=1024, ganz=True),
            eps=_zahl(gg, k("attention.layer_norm_rms_epsilon"), 1e-6, lo=0, hi=1),
            kv_lora=_zahl(gg, k("attention.kv_lora_rank"), lo=1, hi=16384, ganz=True),
            rope_dim=rope_dim, qk_nope=qk_head - rope_dim, v_dim=v_dim,
            n_dense=_zahl(gg, k("leading_dense_block_count"), 0, lo=0, hi=n_layer, ganz=True),
            n_expert=n_expert,
            n_used=_zahl(gg, k("expert_used_count"), 0, lo=0, hi=1024, ganz=True),
            n_group=_zahl(gg, k("expert_group_count"), 1, lo=1, hi=1024, ganz=True),
            group_used=_zahl(gg, k("expert_group_used_count"), 1, lo=1, hi=1024, ganz=True),
            weights_scale=_zahl(gg, k("expert_weights_scale"), 1.0, lo=0, hi=1e4),
            # Kimi Linear: llama.cpp normiert die Experten-Gewichte immer, Router Sigmoid
            norm_topk=bool(gg.get(k("expert_weights_norm"), kimi)),
            gating=_zahl(gg, k("expert_gating_func"), GATING_SIGMOID if kimi else GATING_SOFTMAX, lo=1, hi=2, ganz=True),
            kda_dim=_zahl(gg, k("kda.head_dim"), 128, lo=1, hi=4096, ganz=True) if kimi else 0,
            kda_conv=_zahl(gg, k("ssm.conv_kernel"), 4, lo=1, hi=16, ganz=True) if kimi else 0,
        )
        if hp["qk_nope"] < 0 or rope_dim % 2:
            raise GGUFError("key length and rope dimension do not fit together")
        if n_expert and (not 0 < hp["n_used"] <= n_expert or n_expert % hp["n_group"]
                         or hp["group_used"] > hp["n_group"]
                         or hp["n_used"] > hp["group_used"] * (n_expert // hp["n_group"])):
            raise GGUFError("inconsistent expert routing parameters")
        rope, mscale = (_OhneRope(), 1.0) if kimi else self._rope(gg, k, rope_dim, n_ctx, device)
        hp["attn_scale"] = mscale * mscale / math.sqrt(qk_head)
        self.hp = hp
        self.n_ctx = n_ctx
        self.device = device

        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.out_norm = ld.norm("output_norm.weight")
        self.lm_head = head if tied else ld.linear("output.weight")
        self.blocks: List[_Block] = [_Block(ld, i, hp, rope, n_ctx) for i in range(n_layer)]
        self.rekurrent = [b.attn for b in self.blocks if isinstance(b.attn, _KDA)]
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        # single steps as CUDA graph: possible when every MoE layer picks its experts on the GPU
        ohne_sync = all(b.ffn.experten.graphfaehig for b in self.blocks if isinstance(b.ffn, _MoE))
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" and ohne_sync else None

    @staticmethod
    def _rope(gg, k, dim, n_ctx, device):
        """RoPE on adjacent pairs; with YaRN the attention scale grows by mscale^2 while
        cos/sin stay unscaled (llama.cpp deepseek2: attn_factor compensated)."""
        basis = _zahl(gg, k("rope.freq_base"), 10000.0, lo=1, hi=1e12)
        art = gg.get(k("rope.scaling.type"), "none")
        faktor = _zahl(gg, k("rope.scaling.factor"), 1.0, lo=1e-3, hi=1e4)
        orig = _zahl(gg, k("rope.scaling.original_context_length"), 0, lo=0, hi=1 << 26, ganz=True)
        yarn, mscale = None, 1.0
        if art == "yarn" and faktor > 1.0 and orig:
            yarn = (faktor, orig, _zahl(gg, k("rope.scaling.yarn_beta_fast"), 32.0, lo=0, hi=1e4),
                    _zahl(gg, k("rope.scaling.yarn_beta_slow"), 1.0, lo=0, hi=1e4))
            mscale = 1.0 + _zahl(gg, k("rope.scaling.yarn_log_multiplier"), 0.1, lo=0, hi=100) * math.log(faktor)
        elif art not in ("none", "yarn", None):
            raise GGUFError(f"unsupported rope.scaling.type {art!r}")
        return RoPE(dim, basis, device, max_pos=n_ctx, neox=False, yarn=yarn), mscale

    # -- state: KV caches are overwritten by position; KDA layers (Kimi) carry a recurrent state -----
    def reset(self):
        for m in self.rekurrent:
            m.reset()

    def snapshot_state(self):
        return [m.snapshot() for m in self.rekurrent] if self.rekurrent else None

    def restore_state(self, snap):
        for m, s in zip(self.rekurrent, snap or ()):
            m.restore(s)

    def zeilen_nachholen(self, L: int, n: int) -> bool:
        """State after row n of the last check with L rows, without reading the tokens again."""
        return mischer_nachholen(self.rekurrent, L, n)

    def resume_from(self, common: int, old_len: int) -> Optional[int]:
        if self.rekurrent:                       # the recurrent state only exists for the end
            return common if common == old_len else None
        return common

    def checkpoint_state(self):
        return self.snapshot_state()

    def restore_checkpoint(self, snap):
        self.restore_state(snap)

    # -- compute ---------------------------------------------------------------------
    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        h = self.embed(tok)
        for b in self.blocks:
            h = b(h, pos_idx, kv_len)
        return logit_zeilen(self, self.lm_head(rms_norm(letzte_zeilen(self, h), self.out_norm, self.hp["eps"]))).float()

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
