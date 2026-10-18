"""Lernweg: ein differenzierbarer Vorwärtsweg über die Gewichte eines geladenen GGUF-Modells.

Der GGUF-Motor rechnet für Antworten: mit Zuständen, die er an Ort und Stelle fortschreibt
(KV-Cache, Delta-Zustand), und unter `inference_mode`. Daraus lässt sich kein Gradient
gewinnen. Der Lernweg rechnet dieselbe Mathematik für eine ganze Folge noch einmal – ohne
Zustand, ohne Änderungen an Ort und Stelle – und benutzt die Gewichte des Modells nur lesend.

Eingefrorene Schichten laufen vorwärts über ihren eigenen (quantisierten) Rechenweg; rückwärts
wird ihr Gewicht kurz entpackt (grad_x = grad_y @ W). Trainiert werden nur die LoRA-Matrizen.
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint

from ggufengine.models.common import rms_norm
from ggufengine.quant import dequantize

from .adapter import BAUARTEN, schichten

LOGIT_STUECK = 256                       # Positionen je Logit-Rechnung (Wortschatz ist groß)


def gewicht(schicht) -> torch.Tensor:
    """Gewicht (n_out, n_in) einer eingefrorenen Schicht im Rechenformat."""
    w = getattr(schicht, "w", None)
    if w is not None:
        return w
    if hasattr(schicht, "bf16_weight"):
        return schicht.bf16_weight()
    if hasattr(schicht, "raw") and hasattr(schicht, "ggml_type"):
        return dequantize(schicht.raw.reshape(-1), schicht.ggml_type, schicht.n_out * schicht.n_in,
                          schicht.dtype).view(schicht.n_out, schicht.n_in)
    raise TypeError(f"unbekannte Schicht {type(schicht).__name__}")


class _Gefroren(torch.autograd.Function):
    """y = schicht(x) ohne Gradient für das Gewicht; nichts Großes wird gespeichert."""

    @staticmethod
    def forward(ctx, x, schicht):
        ctx.schicht = schicht
        ctx.dtype = x.dtype
        with torch.no_grad():
            return schicht(x).to(x.dtype)

    @staticmethod
    def backward(ctx, g):
        w = gewicht(ctx.schicht)
        return (g.to(w.dtype) @ w).to(ctx.dtype), None


def _delta_folge(q, k, v, g, beta, C: int = 64):
    """Gated Delta Rule über eine ganze Folge ab Zustand 0 – wie `delta_blockweise` des Motors,
    aber ohne Änderungen an Ort und Stelle (differenzierbar). Formen wie dort."""
    L, H, Dk = k.shape
    Dv = v.shape[-1]
    pad = (-L) % C
    q, k, v = (x.transpose(0, 1) for x in (q, k, v))
    g, beta = g.t(), beta.t()
    if pad:
        q, k, v = (F.pad(x, (0, 0, 0, pad)) for x in (q, k, v))
        g, beta = F.pad(g, (0, pad)), F.pad(beta, (0, pad))
    n = (L + pad) // C
    q, k = q.reshape(H, n, C, Dk), k.reshape(H, n, C, Dk)
    v = v.reshape(H, n, C, Dv)
    g = g.reshape(H, n, C).cumsum(-1)
    beta = beta.reshape(H, n, C, 1)
    k_beta, v_beta = k * beta, v * beta
    dev = q.device
    ab_diag = torch.triu(torch.ones(C, C, dtype=torch.bool, device=dev))
    oberhalb = torch.triu(torch.ones(C, C, dtype=torch.bool, device=dev), diagonal=1)
    zerfall = (g.unsqueeze(-1) - g.unsqueeze(-2)).tril().exp().tril()
    eins = torch.eye(C, dtype=q.dtype, device=dev)
    N = ((k_beta @ k.transpose(-1, -2)) * zerfall).masked_fill(ab_diag, 0) + eins
    T = torch.linalg.solve_triangular(N, eins.expand_as(N), upper=False, unitriangular=True)
    u = T @ v_beta
    w = T @ (k_beta * g.exp().unsqueeze(-1))
    S = torch.zeros(H, Dk, Dv, dtype=q.dtype, device=dev)
    teile = []
    for c in range(n):
        qc, kc, gc = q[:, c], k[:, c], g[:, c]
        innen = (qc @ kc.transpose(-1, -2) * zerfall[:, c]).masked_fill(oberhalb, 0)
        v_neu = u[:, c] - w[:, c] @ S
        teile.append((qc * gc.exp().unsqueeze(-1)) @ S + innen @ v_neu)
        S = S * gc[:, -1].exp().view(H, 1, 1) + (kc * (gc[:, -1:] - gc).exp().unsqueeze(-1)).transpose(-1, -2) @ v_neu
    return torch.stack(teile, 1).reshape(H, n * C, Dv)[:, :L].transpose(0, 1)


class Lernweg:
    """LoRA-Parameter + differenzierbarer Vorwärtsweg für ein geladenes qwen35-Modell."""

    def __init__(self, model, arch: str, rang: int = 16, alpha: float | None = None):
        if arch not in BAUARTEN:
            raise ValueError(f"Für die Bauart {arch} gibt es noch keinen Lernweg (vorhanden: {', '.join(BAUARTEN)}).")
        if any(getattr(b, "moe", False) for b in model.blocks):
            raise ValueError("Modelle mit Experten (MoE) werden noch nicht trainiert.")
        self.model = model
        self.rang = int(rang)
        self.alpha = float(alpha if alpha is not None else 2 * rang)
        self.faktor = self.alpha / self.rang
        self.eps = model.hp["eps"]
        self.ziele = schichten(model)
        self.namen = {(id(b), a): n for n, (b, a) in self.ziele.items()}
        geraet = model.device
        self.a: dict[str, torch.nn.Parameter] = {}
        self.b: dict[str, torch.nn.Parameter] = {}
        for name, (besitzer, attr) in self.ziele.items():
            s = getattr(besitzer, attr)
            a = torch.empty(self.rang, s.n_in, device=geraet, dtype=torch.float32)
            torch.nn.init.kaiming_uniform_(a, a=math.sqrt(5))
            self.a[name] = torch.nn.Parameter(a)
            self.b[name] = torch.nn.Parameter(torch.zeros(s.n_out, self.rang, device=geraet, dtype=torch.float32))

    def parameter(self) -> list[torch.nn.Parameter]:
        return [p for n in self.ziele for p in (self.a[n], self.b[n])]

    def gewichte(self) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
        return {n: (self.a[n], self.b[n]) for n in self.ziele}

    def laden(self, gewichte: dict[str, tuple[torch.Tensor, torch.Tensor]]) -> None:
        with torch.no_grad():
            for n, (a, b) in gewichte.items():
                if n in self.a and self.a[n].shape == a.shape and self.b[n].shape == b.shape:
                    self.a[n].copy_(a)
                    self.b[n].copy_(b)

    # -- Bausteine -------------------------------------------------------------
    def _lin(self, besitzer, attr: str, x: torch.Tensor) -> torch.Tensor:
        schicht = getattr(besitzer, attr)
        y = _Gefroren.apply(x.float(), schicht)
        name = self.namen.get((id(besitzer), attr))
        if name is None:
            return y
        return y + (x.float() @ self.a[name].t() @ self.b[name].t()) * self.faktor

    def _attention(self, m, x: torch.Tensor, pos3: torch.Tensor) -> torch.Tensor:
        L = x.shape[0]
        qg = self._lin(m, "q", x).view(L, m.H, 2, m.D)
        q, gate = qg[:, :, 0], qg[:, :, 1]
        k = self._lin(m, "k", x).view(L, m.Hkv, m.D)
        v = self._lin(m, "v", x).view(L, m.Hkv, m.D)
        q = m.rope(rms_norm(q, m.q_norm, m.eps), pos3)
        k = m.rope(rms_norm(k, m.k_norm, m.eps), pos3)
        o = F.scaled_dot_product_attention(q.transpose(0, 1)[None], k.transpose(0, 1)[None],
                                           v.transpose(0, 1)[None], is_causal=True, scale=m.scale,
                                           enable_gqa=m.Hkv != m.H)[0].transpose(0, 1)
        o = o * torch.sigmoid(gate)
        return self._lin(m, "o", o.reshape(L, -1))

    def _delta(self, m, x: torch.Tensor) -> torch.Tensor:
        L = x.shape[0]
        qkv = self._lin(m, "qkv", x)
        z = self._lin(m, "z", x)
        if m.ba is not None:
            rep = m.Hv // m.Hk
            ba = _Gefroren.apply(x.float(), m.ba).view(L, m.Hk, 2 * rep)
            b, a = ba[..., :rep].reshape(L, m.Hv), ba[..., rep:].reshape(L, m.Hv)
        else:
            b, a = _Gefroren.apply(x.float(), m.beta), _Gefroren.apply(x.float(), m.alpha)
        beta = torch.sigmoid(b)
        g = m.a * F.softplus(a + m.dt_bias)
        voll = torch.cat([torch.zeros(m.K - 1, qkv.shape[1], device=x.device, dtype=qkv.dtype), qkv], dim=0)
        conv = F.silu(F.conv1d(voll.t().unsqueeze(0), m.conv_w, groups=m.conv_channels)[0].t())
        nk = m.Hk * m.Dk
        q = conv[:, :nk].view(L, m.Hk, m.Dk)
        k = conv[:, nk:2 * nk].view(L, m.Hk, m.Dk)
        v = conv[:, 2 * nk:].view(L, m.Hv, m.Dv)
        q = q * torch.rsqrt(q.pow(2).sum(-1, keepdim=True) + 1e-6) * (m.Dk ** -0.5)
        k = k * torch.rsqrt(k.pow(2).sum(-1, keepdim=True) + 1e-6)
        if m.Hv != m.Hk:
            rep = m.Hv // m.Hk
            if m.gruppiert:
                q, k = q.repeat_interleave(rep, dim=1), k.repeat_interleave(rep, dim=1)
            else:
                q, k = q.repeat(1, rep, 1), k.repeat(1, rep, 1)
        out = _delta_folge(q, k, v, g, beta)
        o = rms_norm(out, m.norm_w, m.eps) * F.silu(z.view(L, m.Hv, m.Dv))
        return self._lin(m, "out", o.reshape(L, -1))

    def _block(self, blk, h: torch.Tensor, pos3: torch.Tensor) -> torch.Tensor:
        x = rms_norm(h, blk.attn_norm, self.eps)
        h = h + (self._attention(blk.mixer, x, pos3) if blk.is_attn else self._delta(blk.mixer, x))
        x = rms_norm(h, blk.ffn_norm, self.eps)
        mitte = F.silu(self._lin(blk, "gate", x)) * self._lin(blk, "up", x)
        return h + self._lin(blk, "down", mitte)

    # -- ganze Folge ------------------------------------------------------------
    def verborgen(self, ids: torch.Tensor, sparen: bool = True) -> torch.Tensor:
        """Letzte verborgene Zustände (L, E) nach der Schlussnorm; `sparen`: je Block neu rechnen
        statt alles zu behalten (Gradient-Checkpointing)."""
        h = self.model.embed(ids).float()
        pos3 = torch.arange(ids.numel(), device=ids.device).unsqueeze(1).expand(-1, 3)
        for blk in self.model.blocks:
            if sparen and torch.is_grad_enabled():
                h = checkpoint(self._block, blk, h, pos3, use_reentrant=False)
            else:
                h = self._block(blk, h, pos3)
        return rms_norm(h, self.model.out_norm, self.eps)

    def logits(self, ids: torch.Tensor) -> torch.Tensor:
        """Logits aller Positionen (nur für Tests und kurze Folgen)."""
        h = self.verborgen(ids, sparen=False)
        return _Gefroren.apply(h, self.model.lm_head).float()

    def verlust(self, ids: torch.Tensor, maske: torch.Tensor) -> torch.Tensor:
        """Mittlere Kreuzentropie der nächsten Token an den Stellen, wo `maske` (L,) wahr ist."""
        h = self.verborgen(ids)[:-1]             # Zeile i sagt Token i+1 voraus
        ziel = ids[1:]
        zaehlen = maske[1:]
        n = int(zaehlen.sum())
        if n == 0:
            raise ValueError("Beispiel ohne Antwort-Token")
        summe = h.new_zeros(())
        for s in range(0, ziel.numel(), LOGIT_STUECK):
            teil = zaehlen[s:s + LOGIT_STUECK]
            if not bool(teil.any()):
                continue
            summe = summe + checkpoint(self._stueck_verlust, h[s:s + LOGIT_STUECK][teil], ziel[s:s + LOGIT_STUECK][teil],
                                       use_reentrant=False)
        return summe / n

    def _stueck_verlust(self, h: torch.Tensor, ziel: torch.Tensor) -> torch.Tensor:
        logits = _Gefroren.apply(h, self.model.lm_head).float()
        return F.cross_entropy(logits, ziel, reduction="sum")
