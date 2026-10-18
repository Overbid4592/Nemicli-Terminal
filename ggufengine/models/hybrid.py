"""
Hybrids of Mamba-2 state-space layers and attention ("granitehybrid", "nemotron_h",
"nemotron_h_moe", "falcon-h1") and of short convolutions and attention ("lfm2", "lfm2moe").

Mamba-2 layer (llama.cpp layout, conversion/mamba.py):
* ssm_in = [z (d_inner) | xBC (d_inner + 2*G*N) | dt (H)]; ssm_conv1d (C, K) depthwise causal,
  optional bias, SiLU; xBC = [x | B | C]; head h reads group h // (H/G).
* ssm_a = -exp(A_log) (H, 1), ssm_d (H, 1), ssm_dt.bias (H,); dt = softplus(dt + bias).
* S_t = exp(dt*A) S_{t-1} + dt * x_t B_t^T (per head, P x N), y_t = S_t C_t + D x_t.
* optional gated norm ssm_norm (G, d_inner/G): rms over each group of y * silu(z), times w.

Layer forms per architecture:
* granitehybrid: norm -> (mamba | attention) -> h += out * residual_scale; norm -> FFN
  (MoE + shared expert, or dense) -> h += out * residual_scale; Granite scales.
* nemotron_h: one mixer per layer (mamba, attention without RoPE, ReLU² MLP or MoE).
* falcon-h1: attention and mamba in parallel on the same norm, then FFN (multipliers are
  folded into the weights by the converter).
* lfm2: norm -> (short conv | attention with q/k norm) -> norm -> FFN (SwiGLU or MoE with
  sigmoid router and selection bias); the final norm is stored as token_embd_norm.

Short convolution (LFM2): shortconv.in_proj = [b | c | x], y = c * conv(b * x) with a causal
depthwise kernel of shortconv.l_cache taps, then shortconv.out_proj.

State (conv window, S, KV cache) is updated in place: one-token steps run as CUDA graphs.
"""
from __future__ import annotations

from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import (GraphDecoder, KVCache, RoPE, WeightLoader, attention, letzte_zeilen, logit_zeilen,
                     rms_norm, swiglu_mlp, fenster_nachholen, mischer_nachholen, zeilen_merken)
from .moe import Experten

BLOCK_AB = 32           # from this many tokens on the scan runs in blocks
BLOCK = 64              # tokens per block


def ssd_blockweise(x, dt, A, B, C, S, Cb: int = BLOCK):
    """Mamba-2 scan over a sequence in blocks (the chunked SSD form): same result as the
    token loop.  x (L, H, P), dt (L, H) after softplus, A (H,) <= 0, B/C (L, H, N) per head,
    S (H, P, N) is carried forward in place.  Returns y (L, H, P) without the D term."""
    L, H, P = x.shape
    N = B.shape[-1]
    pad = (-L) % Cb
    a = dt * A                                                   # (L, H) log decay
    xdt = x * dt.unsqueeze(-1)
    if pad:
        a = F.pad(a, (0, 0, 0, pad))
        xdt, B, C = (F.pad(t, (0, 0, 0, 0, 0, pad)) for t in (xdt, B, C))
    n = (L + pad) // Cb
    a = a.view(n, Cb, H).permute(0, 2, 1)                        # (n, H, C)
    xdt = xdt.view(n, Cb, H, P).permute(0, 2, 1, 3)              # (n, H, C, P)
    B = B.view(n, Cb, H, N).permute(0, 2, 1, 3)
    C = C.view(n, Cb, H, N).permute(0, 2, 1, 3)
    cs = a.cumsum(-1)                                            # (n, H, C)
    seg = cs.unsqueeze(-1) - cs.unsqueeze(-2)                    # (n, H, i, j) = sum a[j+1..i]
    unten = torch.tril(torch.ones(Cb, Cb, dtype=torch.bool, device=x.device))
    zerfall = torch.exp(seg.masked_fill(~unten, float("-inf")))
    innen = (C @ B.transpose(-1, -2)) * zerfall                  # (n, H, C, C)
    y = innen @ xdt                                              # within the block
    bis_ende = torch.exp(cs[..., -1:] - cs)                      # decay from j to the block end
    beitrag = (B * bis_ende.unsqueeze(-1)).transpose(-1, -2) @ xdt   # (n, H, N, P)
    ab_start = torch.exp(cs).unsqueeze(-1)                       # decay from the block start to i
    for c in range(n):
        y[c] += (C[c] * ab_start[c]) @ S.transpose(-1, -2)       # (H, C, N) @ (H, N, P)
        S.mul_(torch.exp(cs[c, :, -1]).view(H, 1, 1))
        S.add_(beitrag[c].transpose(-1, -2))
    return y.permute(0, 2, 1, 3).reshape(n * Cb, H, P)[:L]


class Mamba2:
    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.d_inner, self.N, self.G, self.H = hp["ssm_inner"], hp["ssm_state"], hp["ssm_groups"], hp["ssm_heads"]
        self.K, self.eps = hp["ssm_conv"], hp["eps"]
        if self.d_inner % self.H or self.H % self.G:
            raise GGUFError(f"layer {i}: inconsistent mamba sizes")
        self.P = self.d_inner // self.H
        self.conv_dim = self.d_inner + 2 * self.G * self.N
        self.inp = ld.linear(p + "ssm_in.weight", p + "ssm_in.bias" if ld.has(p + "ssm_in.bias") else None)
        if self.inp.n_out != 2 * self.d_inner + 2 * self.G * self.N + self.H:
            raise GGUFError(f"layer {i}: unexpected ssm_in size {self.inp.n_out}")
        conv = ld.norm(p + "ssm_conv1d.weight")
        if tuple(conv.shape) != (self.conv_dim, self.K):
            raise GGUFError(f"layer {i}: unexpected conv1d shape {tuple(conv.shape)}")
        self.conv_w = conv.float().unsqueeze(1).contiguous()
        self.conv_b = ld.norm(p + "ssm_conv1d.bias").float() if ld.has(p + "ssm_conv1d.bias") else None
        self.dt_bias = ld.norm(p + "ssm_dt.bias").float().reshape(-1)
        self.A = ld.norm(p + "ssm_a").float().reshape(-1)
        self.D = ld.norm(p + "ssm_d").float().reshape(-1)
        self.norm, self.norm_gruppen = None, self.G
        if ld.has(p + "ssm_norm.weight"):                         # (G, d_inner/G): Gruppen-RMS
            w = ld.norm(p + "ssm_norm.weight").float()
            self.norm, self.norm_gruppen = w.reshape(-1), (w.shape[0] if w.dim() == 2 else self.G)
        self.out = ld.linear(p + "ssm_out.weight", p + "ssm_out.bias" if ld.has(p + "ssm_out.bias") else None)
        dev = self.A.device
        self.kopf_gruppe = torch.arange(self.H, device=dev) // (self.H // self.G)
        self.conv_state = torch.zeros(self.K - 1, self.conv_dim, device=dev, dtype=torch.float32)
        self.S = torch.zeros(self.H, self.P, self.N, device=dev, dtype=torch.float32)
        self._merk: dict = {}                                      # rows of the last check, by its length

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
        proj = self.inp(x).float()
        z, xbc, dt = proj.split([self.d_inner, self.conv_dim, self.H], dim=-1)
        xbc_ein = xbc
        full = torch.cat([self.conv_state, xbc], dim=0)              # causal conv with carried window
        self.conv_state.copy_(full[-(self.K - 1):])
        xbc = F.conv1d(full.t().unsqueeze(0), self.conv_w, self.conv_b, groups=self.conv_dim)[0].t()
        xbc = F.silu(xbc)
        xs, B, C = xbc.split([self.d_inner, self.G * self.N, self.G * self.N], dim=-1)
        xs = xs.view(L, self.H, self.P)
        B = B.view(L, self.G, self.N).index_select(1, self.kopf_gruppe)   # (L, H, N)
        C = C.view(L, self.G, self.N).index_select(1, self.kopf_gruppe)
        dt = F.softplus(dt + self.dt_bias)                           # (L, H)
        S = self.S
        if L >= BLOCK_AB:
            y = ssd_blockweise(xs, dt, self.A, B, C, S)
        else:
            y = torch.empty(L, self.H, self.P, device=x.device, dtype=torch.float32)
            zerfall = torch.exp(dt * self.A)                         # (L, H)
            if zeilen_merken():
                self._merk[L] = (xbc_ein, zerfall, xs, dt, B)
            for t in range(L):
                S.mul_(zerfall[t].view(self.H, 1, 1))
                S.add_((xs[t] * dt[t].unsqueeze(-1)).unsqueeze(-1) * B[t].unsqueeze(1))
                y[t] = (S @ C[t].unsqueeze(-1))[..., 0]
        y = y + xs * self.D.view(1, self.H, 1)
        y = y.reshape(L, self.d_inner) * F.silu(z)
        if self.norm is not None:
            g = y.unflatten(-1, (self.norm_gruppen, -1))
            y = (g * torch.rsqrt(g.pow(2).mean(-1, keepdim=True) + self.eps)).flatten(-2) * self.norm
        return self.out(y)

    def nachholen(self, L: int, n: int) -> None:
        xbc, zerfall, xs, dt, B = self._merk[L]
        fenster_nachholen(self.conv_state, xbc, n)
        for t in range(n):                                           # the steps of the token loop
            self.S.mul_(zerfall[t].view(self.H, 1, 1))
            self.S.add_((xs[t] * dt[t].unsqueeze(-1)).unsqueeze(-1) * B[t].unsqueeze(1))


class ShortConv:
    """LFM2: gated short causal convolution with a carried window of l_cache - 1 rows."""

    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}.shortconv."
        self.inp = ld.linear(p + "in_proj.weight")
        self.out = ld.linear(p + "out_proj.weight")
        conv = ld.norm(p + "conv.weight").float()
        self.E = self.out.n_out
        if self.inp.n_out != 3 * self.E or conv.shape[0] != self.E:
            raise GGUFError(f"layer {i}: unexpected short conv shapes")
        self.K = conv.shape[1]
        self.conv_w = conv.unsqueeze(1).contiguous()
        self.conv_b = ld.norm(p + "conv.bias").float() if ld.has(p + "conv.bias") else None
        self.state = torch.zeros(self.K - 1, self.E, device=conv.device, dtype=torch.float32)
        self._merk: dict = {}

    def reset(self):
        with torch.inference_mode():
            self.state.zero_()

    def snapshot(self):
        return self.state.clone()

    def restore(self, snap):
        self.state.copy_(snap)

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        b, c, xs = self.inp(x).float().chunk(3, dim=-1)
        bx = b * xs
        if zeilen_merken():
            self._merk[bx.shape[0]] = bx
        full = torch.cat([self.state, bx], dim=0)
        self.state.copy_(full[-(self.K - 1):])
        conv = F.conv1d(full.t().unsqueeze(0), self.conv_w, self.conv_b, groups=self.E)[0].t()
        return self.out(c * conv)

    def nachholen(self, L: int, n: int) -> None:
        fenster_nachholen(self.state, self._merk[L], n)


class _Attention:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: Optional[RoPE], n_ctx: int):
        p = f"blk.{i}."
        self.H, self.Hkv, self.D = hp["n_heads"], hp["kv_heads"][i], hp["head_dim"]
        if ld.has(p + "attn_qkv.weight"):
            self.qkv = ld.linear(p + "attn_qkv.weight", p + "attn_qkv.bias" if ld.has(p + "attn_qkv.bias") else None)
            self.q = self.k = self.v = None
        else:
            self.qkv = None
            b = lambda n: p + f"attn_{n}.bias" if ld.has(p + f"attn_{n}.bias") else None
            self.q, self.k, self.v = (ld.linear(p + f"attn_{n}.weight", b(n)) for n in "qkv")
        self.o = ld.linear(p + "attn_output.weight", p + "attn_output.bias" if ld.has(p + "attn_output.bias") else None)
        if not self.Hkv or self.H % self.Hkv:
            raise GGUFError(f"layer {i}: inconsistent attention head counts")
        self.rope = rope
        self.q_norm = ld.norm(p + "attn_q_norm.weight") if ld.has(p + "attn_q_norm.weight") else None
        self.k_norm = ld.norm(p + "attn_k_norm.weight") if ld.has(p + "attn_k_norm.weight") else None
        self.eps = hp["eps"]
        self.scale = hp["attn_scale"] or self.D ** -0.5
        self.cache = KVCache(self.Hkv, self.D, n_ctx, ld.device, ld.dtype, ld.kv_bits)

    def reset(self):
        pass

    def snapshot(self):
        return None

    def restore(self, snap):
        pass

    def __call__(self, x: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        L, H, Hkv, D = x.shape[0], self.H, self.Hkv, self.D
        if self.qkv is not None:
            q, k, v = self.qkv(x).float().split([H * D, Hkv * D, Hkv * D], dim=-1)
        else:
            q, k, v = self.q(x).float(), self.k(x).float(), self.v(x)
        q, k, v = q.view(L, H, D), k.view(L, Hkv, D), v.view(L, Hkv, D)
        if self.q_norm is not None:                             # LFM2: RMS je Kopf vor RoPE
            q, k = rms_norm(q, self.q_norm, self.eps), rms_norm(k, self.k_norm, self.eps)
        if self.rope is not None:
            q, k = self.rope(q, pos_idx), self.rope(k, pos_idx)
        self.cache.write(k, v, pos_idx)
        o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, self.scale)
        return self.o(o.reshape(L, -1))


def _relu2(x: torch.Tensor) -> torch.Tensor:
    return torch.relu(x).square()


class _FFN:
    """Dense (SwiGLU or up-only with the arch's activation) or MoE with optional shared expert."""

    def __init__(self, ld: WeightLoader, i: int, hp: dict):
        p = f"blk.{i}."
        self.hp = hp
        self.akt = _relu2 if hp["relu2"] else F.silu
        self.moe = ld.has(p + "ffn_gate_inp.weight")
        if self.moe:
            self.router = ld.linear(p + "ffn_gate_inp.weight")
            self.experten = Experten(ld, p, self.akt)
            self.wahl_bias = ld.norm(p + "exp_probs_b.bias").float().reshape(-1) if ld.has(p + "exp_probs_b.bias") \
                else None
            if not (self.experten.pruefen() and len(self.experten) == self.router.n_out
                    and 0 < hp["n_expert_used"] <= len(self.experten)):
                raise GGUFError(f"layer {i}: inconsistent expert tensors")
            self.s_up = ld.linear(p + "ffn_up_shexp.weight") if ld.has(p + "ffn_up_shexp.weight") else None
            self.s_gate = ld.linear(p + "ffn_gate_shexp.weight") if ld.has(p + "ffn_gate_shexp.weight") else None
            self.s_down = ld.linear(p + "ffn_down_shexp.weight") if self.s_up is not None else None
        self.up = ld.linear(p + "ffn_up.weight") if ld.has(p + "ffn_up.weight") else None
        self.gate = ld.linear(p + "ffn_gate.weight") if ld.has(p + "ffn_gate.weight") else None
        self.down = ld.linear(p + "ffn_down.weight") if self.up is not None else None
        if not self.moe and self.up is None:
            raise GGUFError(f"layer {i}: no feed-forward tensors")

    def _mlp(self, x, gate, up, down):
        if gate is not None:
            return swiglu_mlp(x, gate, up, down).float()
        return down(self.akt(up(x).float())).float()

    def _auswahl(self, logits):
        hp = self.hp
        if hp["sigmoid"]:                                        # Nemotron-H MoE (DeepSeek-V3 style)
            p = torch.sigmoid(logits)
            wahl = p + self.wahl_bias if self.wahl_bias is not None else p
            if hp["n_group"] > 1:
                Lr, je = p.shape[0], p.shape[1] // hp["n_group"]
                g = wahl.view(Lr, hp["n_group"], je).topk(min(2, je), dim=-1)[0].sum(-1)
                maske = torch.zeros(Lr, hp["n_group"], dtype=torch.bool, device=p.device)
                maske.scatter_(1, g.topk(hp["group_used"], dim=-1)[1], True)
                wahl = wahl.masked_fill(~maske.repeat_interleave(je, dim=1), float("-inf"))
            idx = wahl.topk(hp["n_expert_used"], dim=-1)[1]
            w = p.gather(-1, idx)
        else:
            w, idx = torch.softmax(logits, dim=-1).topk(hp["n_expert_used"], dim=-1)
        if hp["topk_norm"]:
            w = w / (w.sum(-1, keepdim=True) + 1e-20)
        return idx, w * hp["weights_scale"]

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        if not self.moe:
            return self._mlp(x, self.gate, self.up, self.down)
        idx, w = self._auswahl(self.router(x).float())
        out = self.experten(x, idx, w)
        if self.s_up is not None:
            out = out + self._mlp(x, self.s_gate, self.s_up, self.s_down)
        if self.up is not None:                                  # Granite: dense shared MLP neben den Experten
            out = out + self._mlp(x, self.gate, self.up, self.down)
        return out


class _Block:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: Optional[RoPE], n_ctx: int):
        p = f"blk.{i}."
        self.hp, self.eps = hp, hp["eps"]
        self.norm = ld.norm(p + "attn_norm.weight")
        self.ssm = Mamba2(ld, i, hp) if ld.has(p + "ssm_in.weight") else \
            (ShortConv(ld, i, hp) if ld.has(p + "shortconv.in_proj.weight") else None)
        hat_attn = ld.has(p + "attn_q.weight") or ld.has(p + "attn_qkv.weight")
        self.attn = _Attention(ld, i, hp, rope, n_ctx) if hat_attn else None
        ffn_norm = next((n for n in ("ffn_norm", "ffn_pre_norm") if ld.has(p + f"{n}.weight")), None)
        self.ffn_norm = ld.norm(p + f"{ffn_norm}.weight") if ffn_norm else None
        hat_ffn = ld.has(p + "ffn_up.weight") or ld.has(p + "ffn_gate_inp.weight")
        self.ffn = _FFN(ld, i, hp) if hat_ffn else None
        if self.ssm is None and self.attn is None and self.ffn is None:
            raise GGUFError(f"layer {i}: no mixer tensors found")

    def reset(self):
        if self.ssm is not None:
            self.ssm.reset()

    def snapshot(self):
        return self.ssm.snapshot() if self.ssm is not None else None

    def restore(self, snap):
        if self.ssm is not None:
            self.ssm.restore(snap)

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        r = self.hp["residual_scale"]
        x = rms_norm(h, self.norm, self.eps)
        if self.ssm is not None or self.attn is not None:
            out = 0
            if self.attn is not None:
                out = out + self.attn(x, pos_idx, kv_len).float()
            if self.ssm is not None:
                out = out + self.ssm(x).float()
            h = h + out * r
            if self.ffn is None:
                return h
            x = rms_norm(h, self.ffn_norm, self.eps) if self.ffn_norm is not None else x
        return h + self.ffn(x) * r                               # Nemotron-H: MLP-/MoE-Schicht allein


class HybridModel:
    alle_logits = False      # Logits jeder Zeile statt nur der letzten (spekulatives Prüfen)
    arch_names = ("granitehybrid", "nemotron_h", "nemotron_h_moe", "falcon-h1", "lfm2", "lfm2moe")
    prefill_chunk = 2048

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a not in self.arch_names:
            raise GGUFError(f"{a} is not a mamba hybrid model")
        k = lambda s: f"{a}.{s}"
        n_layer = int(gg.require(k("block_count")))
        n_layer -= int(gg.get(k("nextn_predict_layers"), 0) or 0)      # MTP-Schichten am Ende
        n_embd = int(gg.require(k("embedding_length")))
        n_heads = int(gg.get(k("attention.head_count"), 0) or 0)
        kv = gg.get(k("attention.head_count_kv"), n_heads)
        kv_heads = [int(x) for x in kv][:n_layer] if isinstance(kv, list) else [int(kv or 0)] * n_layer
        if len(kv_heads) != n_layer:
            raise GGUFError("attention.head_count_kv does not match block_count")
        n_heads_l = gg.get(k("attention.head_count"), 0)
        if isinstance(n_heads_l, list):
            n_heads = max(int(x) for x in n_heads_l)
        attn_scale = gg.get(k("attention.scale"))
        hp = dict(
            arch=a, n_layer=n_layer, n_embd=n_embd, n_heads=n_heads, kv_heads=kv_heads,
            head_dim=int(gg.get(k("attention.key_length"), (n_embd // n_heads) if n_heads else 0) or 0),
            eps=float(gg.get(k("attention.layer_norm_rms_epsilon"), 1e-5)),
            ssm_inner=int(gg.get(k("ssm.inner_size"), 0) or 0), ssm_state=int(gg.get(k("ssm.state_size"), 0) or 0),
            ssm_groups=int(gg.get(k("ssm.group_count"), 1) or 1), ssm_heads=int(gg.get(k("ssm.time_step_rank"), 0) or 0),
            ssm_conv=int(gg.get(k("ssm.conv_kernel"), 0) or 0),
            attn_scale=float(attn_scale) if attn_scale else None,
            residual_scale=float(gg.get(k("residual_scale"), 1.0) or 1.0),
            embedding_scale=float(gg.get(k("embedding_scale"), 1.0) or 1.0),
            logit_scale=float(gg.get(k("logit_scale"), 1.0) or 1.0),
            relu2=a.startswith("nemotron_h"),
            n_expert_used=int(gg.get(k("expert_used_count"), 0) or 0),
            # Nemotron-H-MoE schreibt keinen Router-Typ: llama.cpp nimmt dort fest Sigmoid
            sigmoid=int(gg.get(k("expert_gating_func"), 2 if a == "nemotron_h_moe" else 1) or 1) == 2,
            n_group=int(gg.get(k("expert_group_count"), 1) or 1),
            group_used=int(gg.get(k("expert_group_used_count"), 1) or 1),
            topk_norm=bool(gg.get(k("expert_weights_norm"), True)),
            weights_scale=float(gg.get(k("expert_weights_scale"), 1.0) or 1.0),
        )
        mamba = not a.startswith("lfm2")
        if not (0 < n_layer <= 512 and 0 < n_embd <= 65536 and (not mamba or (
                1 <= hp["ssm_conv"] <= 16 and 0 < hp["ssm_heads"] <= 4096 and 0 < hp["ssm_state"] <= 4096))):
            raise GGUFError("implausible hyper-parameters")
        self.hp, self.n_ctx, self.device = hp, n_ctx, device
        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.out_norm = ld.norm("output_norm.weight" if ld.has("output_norm.weight") else "token_embd_norm.weight")
        self.lm_head = head if tied else ld.linear("output.weight")
        rope = None
        # Granite: rope.scaling.finetuned schaltet RoPE (fehlt er: an), q/k umsortiert -> normaler Stil;
        # Falcon-H1: NeoX-Stil ohne Umsortieren; Nemotron-H: ohne Positionen
        if a in ("falcon-h1", "lfm2", "lfm2moe") or (a == "granitehybrid" and gg.get(k("rope.scaling.finetuned"), True)):
            dim = int(gg.get(k("rope.dimension_count"), hp["head_dim"]))
            rope = RoPE(dim, float(gg.get(k("rope.freq_base"), 10000.0)), device, n_ctx, neox=a != "granitehybrid")
        self.blocks: List[_Block] = [_Block(ld, i, hp, rope, n_ctx) for i in range(n_layer)]
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" and self._graph_ok() else None

    def _graph_ok(self) -> bool:
        return all(b.ffn is None or not b.ffn.moe or b.ffn.experten.graphfaehig for b in self.blocks)

    # -- state ---------------------------------------------------------------
    def reset(self):
        for b in self.blocks:
            b.reset()

    def snapshot_state(self):
        return [b.snapshot() for b in self.blocks]

    def restore_state(self, snap):
        for b, s in zip(self.blocks, snap):
            b.restore(s)

    def zeilen_nachholen(self, L: int, n: int) -> bool:
        """State after row n of the last check with L rows, without reading the tokens again."""
        return mischer_nachholen([b.ssm for b in self.blocks if b.ssm is not None], L, n)

    def resume_from(self, common: int, old_len: int) -> Optional[int]:
        """The recurrent state only exists for the end of the cached tokens."""
        return common if common == old_len else None

    def checkpoint_state(self):
        return self.snapshot_state()

    def restore_checkpoint(self, snap):
        self.restore_state(snap)

    # -- compute -------------------------------------------------------------
    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        h = self.embed(tok).float()
        if self.hp["embedding_scale"] != 1.0:
            h = h * self.hp["embedding_scale"]
        for blk in self.blocks:
            h = blk(h, pos_idx, kv_len)
        h = rms_norm(letzte_zeilen(self, h), self.out_norm, self.hp["eps"])
        logits = logit_zeilen(self, self.lm_head(h)).float()
        return logits / self.hp["logit_scale"] if self.hp["logit_scale"] != 1.0 else logits

    def _check(self, tokens: List[int], pos: int):
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int) -> torch.Tensor:
        self._check(tokens, pos)
        if len(tokens) == 1 and self.graph is not None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens))
