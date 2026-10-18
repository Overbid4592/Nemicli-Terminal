"""netze.py – die Netze von ACE-Step 1.5 in PyTorch: Qwen3-Schichten (Textleser, Liedtext- und
Klangfarben-Encoder), Bedingungs-Encoder, Diffusions-Transformer (DiT) und Oobleck-VAE (Decoder und
Encoder).

Aufbau wie acestep.cpp (src/qwen3-enc.h, cond-enc.h, dit-graph.h, vae.h, vae-enc.h), das dieselben GGUFs liest.
"""
from __future__ import annotations

import math

import torch
import torch.nn.functional as F

EPS = 1e-6
THETA = 1_000_000.0
FENSTER = 128                  # gleitendes Fenster (beidseitig) der „sliding“-Schichten


def rms(x: torch.Tensor, w: torch.Tensor, eps: float = EPS) -> torch.Tensor:
    x32 = x.float()
    return (x32 * torch.rsqrt(x32.pow(2).mean(-1, keepdim=True) + eps)).to(x.dtype) * w


def rope_tabelle(n: int, dim: int, geraet, dtype) -> tuple[torch.Tensor, torch.Tensor]:
    inv = 1.0 / (THETA ** (torch.arange(0, dim, 2, device=geraet, dtype=torch.float32) / dim))
    f = torch.arange(n, device=geraet, dtype=torch.float32)[:, None] * inv[None]
    emb = torch.cat([f, f], -1)
    return emb.cos().to(dtype), emb.sin().to(dtype)


def _rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """x: [B, S, H, D] – NeoX-Paarung (d mit d + D/2)."""
    h = x.shape[-1] // 2
    gedreht = torch.cat([-x[..., h:], x[..., :h]], -1)
    return x * cos[None, :, None] + gedreht * sin[None, :, None]


def fenster_maske(n: int, geraet) -> torch.Tensor:
    """True = darf hinsehen: |i − j| ≤ FENSTER."""
    i = torch.arange(n, device=geraet)
    return (i[:, None] - i[None]).abs() <= FENSTER


class Qwen3Schicht:
    """Pre-Norm-Block: Aufmerksamkeit mit q/k-Norm und RoPE (GQA), dann SwiGLU."""

    def __init__(self, laden, praefix: str, koepfe: int = 16, kv: int = 8, dim: int = 128):
        g = lambda n: laden(f"{praefix}.{n}")                     # noqa: E731
        self.ln1, self.ln2 = g("input_layernorm.weight"), g("post_attention_layernorm.weight")
        self.wq, self.wk, self.wv, self.wo = (g(f"self_attn.{n}_proj.weight") for n in "qkvo")
        self.qn, self.kn = g("self_attn.q_norm.weight"), g("self_attn.k_norm.weight")
        self.gate, self.up, self.down = (g(f"mlp.{n}_proj.weight") for n in ("gate", "up", "down"))
        self.koepfe, self.kv, self.dim = koepfe, kv, dim

    def __call__(self, x, cos, sin, maske=None, kausal=False):
        B, S, _ = x.shape
        h = rms(x, self.ln1)
        q = rms((h @ self.wq.T).view(B, S, self.koepfe, self.dim), self.qn)
        k = rms((h @ self.wk.T).view(B, S, self.kv, self.dim), self.kn)
        v = (h @ self.wv.T).view(B, S, self.kv, self.dim)
        q, k = _rope(q, cos, sin), _rope(k, cos, sin)
        a = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2),
                                           attn_mask=maske, is_causal=kausal and maske is None, enable_gqa=True)
        x = x + a.transpose(1, 2).reshape(B, S, -1) @ self.wo.T
        h = rms(x, self.ln2)
        return x + (F.silu(h @ self.gate.T) * (h @ self.up.T)) @ self.down.T


class Textleser:
    """Qwen3-Embedding-0.6B: kausal, Ausgabe nach der letzten Norm [B, S, 1024]."""

    def __init__(self, datei, geraet, dtype=torch.bfloat16):
        laden = lambda n: datei.tensor(n, geraet, dtype)          # noqa: E731
        self.embed = laden("embed_tokens.weight")
        n = int(datei.meta("acestep-text-enc.block_count", 28))
        self.schichten = [Qwen3Schicht(laden, f"layers.{i}") for i in range(n)]
        self.norm = laden("norm.weight")
        self.geraet, self.dtype = geraet, dtype

    @torch.no_grad()
    def einbetten(self, ids: list[int]) -> torch.Tensor:
        return self.embed[torch.tensor(ids, device=self.geraet)][None]

    @torch.no_grad()
    def __call__(self, ids: list[int]) -> torch.Tensor:
        x = self.einbetten(ids)
        cos, sin = rope_tabelle(len(ids), 128, self.geraet, self.dtype)
        for s in self.schichten:
            x = s(x, cos, sin, kausal=True)
        return rms(x, self.norm)


class Bedingung:
    """Bedingungs-Encoder: Liedtext (8 Schichten), Klangfarbe (4), Textprojektion -> [B, E, 2048].
    Reihenfolge: Liedtext, ein Klangfarben-Token, Textprojektion (acestep.cpp cond-enc.h)."""

    def __init__(self, datei, geraet, dtype=torch.bfloat16):
        laden = lambda n: datei.tensor(n, geraet, dtype)          # noqa: E731
        p = "encoder.lyric_encoder"
        self.lyr_w, self.lyr_b = laden(f"{p}.embed_tokens.weight"), laden(f"{p}.embed_tokens.bias")
        self.lyr = [Qwen3Schicht(laden, f"{p}.layers.{i}") for i in range(8)]
        self.lyr_norm = laden(f"{p}.norm.weight")
        p = "encoder.timbre_encoder"
        self.tim_w, self.tim_b = laden(f"{p}.embed_tokens.weight"), laden(f"{p}.embed_tokens.bias")
        self.tim = [Qwen3Schicht(laden, f"{p}.layers.{i}") for i in range(4)]
        self.tim_norm = laden(f"{p}.norm.weight")
        self.text_w = laden("encoder.text_projector.weight")
        self.geraet, self.dtype = geraet, dtype

    def _stapel(self, x, schichten, norm):
        n = x.shape[1]
        cos, sin = rope_tabelle(n, 128, self.geraet, self.dtype)
        fenster = fenster_maske(n, self.geraet)
        for i, s in enumerate(schichten):
            x = s(x, cos, sin, maske=fenster if i % 2 == 0 else None)
        return rms(x, norm)

    @torch.no_grad()
    def __call__(self, text_hidden, liedtext_emb, klangfarbe) -> torch.Tensor:
        lyr = self._stapel(liedtext_emb @ self.lyr_w.T + self.lyr_b, self.lyr, self.lyr_norm)
        tim = self._stapel(klangfarbe @ self.tim_w.T + self.tim_b, self.tim, self.tim_norm)[:, :1]
        return torch.cat([lyr, tim, text_hidden @ self.text_w.T], 1)


def _zeit_sinus(t: torch.Tensor, dim: int = 256, max_periode: float = 10000.0) -> torch.Tensor:
    """[cos | sin] wie ggml_timestep_embedding."""
    halb = dim // 2
    f = torch.exp(-math.log(max_periode) * torch.arange(halb, device=t.device, dtype=torch.float32) / halb)
    a = t.float()[:, None] * f[None]
    return torch.cat([a.cos(), a.sin()], -1)


class _Zeit:
    def __init__(self, laden, p):
        self.l1w, self.l1b = laden(f"{p}.linear_1.weight"), laden(f"{p}.linear_1.bias")
        self.l2w, self.l2b = laden(f"{p}.linear_2.weight"), laden(f"{p}.linear_2.bias")
        self.pw, self.pb = laden(f"{p}.time_proj.weight"), laden(f"{p}.time_proj.bias")

    def __call__(self, t, dtype):
        h = F.silu(_zeit_sinus(t * 1000.0).to(dtype) @ self.l1w.T + self.l1b)
        temb = h @ self.l2w.T + self.l2b
        return temb, F.silu(temb) @ self.pw.T + self.pb


class _DiTSchicht:
    def __init__(self, laden, p):
        g = lambda n: laden(f"{p}.{n}")                           # noqa: E731
        self.ss = g("scale_shift_table")
        self.n_sa, self.n_ca, self.n_mlp = g("self_attn_norm.weight"), g("cross_attn_norm.weight"), g("mlp_norm.weight")
        self.sq, self.sk, self.sv, self.so = (g(f"self_attn.{n}_proj.weight") for n in "qkvo")
        self.sqn, self.skn = g("self_attn.q_norm.weight"), g("self_attn.k_norm.weight")
        self.cq, self.ck, self.cv, self.co = (g(f"cross_attn.{n}_proj.weight") for n in "qkvo")
        self.cqn, self.ckn = g("cross_attn.q_norm.weight"), g("cross_attn.k_norm.weight")
        self.gate, self.up, self.down = (g(f"mlp.{n}_proj.weight") for n in ("gate", "up", "down"))


class DiT:
    """Diffusions-Transformer (2B Turbo): Eingang [Kontext 128 | x_t 64] je Latent-Bild (25 Hz),
    Patches zu 2, 24 Schichten (gerade: gleitendes Fenster), Ausgabe Geschwindigkeit [B, T, 64]."""

    def __init__(self, datei, geraet, dtype=torch.bfloat16):
        laden = lambda n: datei.tensor(n, geraet, dtype)          # noqa: E731
        d = "decoder"
        self.zeit_t, self.zeit_r = _Zeit(laden, f"{d}.time_embed"), _Zeit(laden, f"{d}.time_embed_r")
        self.in_w, self.in_b = laden(f"{d}.proj_in.1.weight"), laden(f"{d}.proj_in.1.bias")
        self.ce_w, self.ce_b = laden(f"{d}.condition_embedder.weight"), laden(f"{d}.condition_embedder.bias")
        n = int(datei.meta("acestep-dit.block_count", 24))
        self.schichten = [_DiTSchicht(laden, f"{d}.layers.{i}") for i in range(n)]
        self.norm_out, self.oss = laden(f"{d}.norm_out.weight"), laden(f"{d}.scale_shift_table")
        self.out_w, self.out_b = laden(f"{d}.proj_out.1.weight"), laden(f"{d}.proj_out.1.bias")
        self.stille = datei.tensor("silence_latent", geraet, torch.float32)       # [15000, 64]
        self.geraet, self.dtype = geraet, dtype

    @staticmethod
    def _attn(q, k, v, maske=None):
        return F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2),
                                              attn_mask=maske, enable_gqa=True).transpose(1, 2)

    @torch.no_grad()
    def __call__(self, x_ein: torch.Tensor, t: float, enc: torch.Tensor) -> torch.Tensor:
        B, T, _ = x_ein.shape
        dt = self.dtype
        tt = torch.full((B,), float(t), device=self.geraet)
        temb_t, proj_t = self.zeit_t(tt, dt)
        temb_r, proj_r = self.zeit_r(tt - tt, dt)               # Turbo: t_r = t -> Eingang 0
        temb = temb_t + temb_r
        proj = (proj_t + proj_r).view(B, 6, -1)
        h = F.conv1d(x_ein.transpose(1, 2).to(dt), self.in_w, self.in_b, stride=2).transpose(1, 2)
        S = h.shape[1]
        enc = enc.to(dt) @ self.ce_w.T + self.ce_b
        cos, sin = rope_tabelle(S, 128, self.geraet, dt)
        fenster = fenster_maske(S, self.geraet)
        for i, s in enumerate(self.schichten):
            mod = s.ss[None] + proj                              # [B, 6, H]
            sh_sa, sc_sa, g_sa, sh_ff, sc_ff, g_ff = (mod[:, j:j + 1] for j in range(6))
            n = rms(h, s.n_sa) * (1 + sc_sa) + sh_sa
            q = _rope(rms((n @ s.sq.T).view(B, S, 16, 128), s.sqn), cos, sin)
            k = _rope(rms((n @ s.sk.T).view(B, S, 8, 128), s.skn), cos, sin)
            v = (n @ s.sv.T).view(B, S, 8, 128)
            a = self._attn(q, k, v, fenster if i % 2 == 0 else None).reshape(B, S, -1)
            h = h + (a @ s.so.T) * g_sa
            n = rms(h, s.n_ca)
            E = enc.shape[1]
            q = rms((n @ s.cq.T).view(B, S, 16, 128), s.cqn)
            k = rms((enc @ s.ck.T).view(B, E, 8, 128), s.ckn)
            v = (enc @ s.cv.T).view(B, E, 8, 128)
            h = h + self._attn(q, k, v).reshape(B, S, -1) @ s.co.T
            n = rms(h, s.n_mlp) * (1 + sc_ff) + sh_ff
            h = h + ((F.silu(n @ s.gate.T) * (n @ s.up.T)) @ s.down.T) * g_ff
        sh, sc = self.oss[0] + temb[:, None], self.oss[1] + temb[:, None]
        n = rms(h, self.norm_out) * (1 + sc) + sh
        aus = F.conv_transpose1d(n.transpose(1, 2), self.out_w, self.out_b, stride=2)
        return aus.transpose(1, 2).float()[:, :T]


# ---------------------------------------------------------------------------
# Oobleck-VAE-Decoder (64 Kanäle, 25 Hz -> Stereo 48 kHz, Faktor 1920)
# ---------------------------------------------------------------------------

def _wn(datei, p, geraet) -> torch.Tensor:
    """Weight-Norm zusammenrechnen: w = g · v / ‖v‖ (Norm über alle Achsen außer der ersten)."""
    g = datei.tensor(f"{p}.weight_g", geraet, torch.float32)
    v = datei.tensor(f"{p}.weight_v", geraet, torch.float32)
    return g * v / v.flatten(1).norm(dim=1).clamp_min(1e-12).view(-1, 1, 1)


def _snake(x, alpha, beta_inv):
    return x + beta_inv * torch.sin(alpha * x).pow(2)


def _snake_laden(datei, p, geraet) -> tuple[torch.Tensor, torch.Tensor]:
    a = datei.tensor(f"{p}.alpha", geraet, torch.float32).exp().view(1, -1, 1)
    b = 1.0 / (datei.tensor(f"{p}.beta", geraet, torch.float32).exp() + 1e-9).view(1, -1, 1)
    return a, b


DILATIONEN = (1, 3, 9)


def _res_laden(datei, p, geraet) -> list:
    """Drei Residual-Einheiten (Dilation 1, 3, 9) unter `p`."""
    ru = []
    for r, dil in enumerate(DILATIONEN):
        q = f"{p}.res_unit{r + 1}"
        ru.append((_snake_laden(datei, f"{q}.snake1", geraet), _wn(datei, f"{q}.conv1", geraet),
                   datei.tensor(f"{q}.conv1.bias", geraet, torch.float32), _snake_laden(datei, f"{q}.snake2", geraet),
                   _wn(datei, f"{q}.conv2", geraet), datei.tensor(f"{q}.conv2.bias", geraet, torch.float32), dil))
    return ru


def _res(x, ru):
    for (a1, b1), w1, c1, (a2, b2), w2, c2, dil in ru:
        y = F.conv1d(_snake(x, a1, b1), w1, c1, padding=3 * dil, dilation=dil)
        y = F.conv1d(_snake(y, a2, b2), w2, c2)
        rand = (x.shape[-1] - y.shape[-1]) // 2
        x = (x[..., rand:x.shape[-1] - rand] if rand > 0 else x) + y
    return x


class VAEDecoder:
    SCHRITTE = (10, 6, 4, 4, 2)

    def __init__(self, datei, geraet):
        self.geraet = geraet
        self.c1w, self.c1b = _wn(datei, "decoder.conv1", geraet), datei.tensor("decoder.conv1.bias", geraet,
                                                                               torch.float32)
        self.bloecke = []
        for i, schritt in enumerate(self.SCHRITTE):
            p = f"decoder.block.{i}"
            self.bloecke.append((_snake_laden(datei, f"{p}.snake1", geraet), _wn(datei, f"{p}.conv_t1", geraet),
                                 datei.tensor(f"{p}.conv_t1.bias", geraet, torch.float32), schritt,
                                 _res_laden(datei, p, geraet)))
        self.snake_aus = _snake_laden(datei, "decoder.snake1", geraet)
        self.c2w = _wn(datei, "decoder.conv2", geraet)

    @torch.no_grad()
    def __call__(self, z: torch.Tensor) -> torch.Tensor:
        """z: [B, 64, T] -> Ton [B, 2, T·1920]."""
        x = F.conv1d(z.float(), self.c1w, self.c1b, padding=3)
        for (sa, sb), wt, bt, schritt, ru in self.bloecke:
            x = F.conv_transpose1d(_snake(x, sa, sb), wt, bt, stride=schritt, padding=math.ceil(schritt / 2))
            x = _res(x, ru)
        return F.conv1d(_snake(x, *self.snake_aus), self.c2w, padding=3)

    @torch.no_grad()
    def dekodieren(self, z: torch.Tensor, stueck: int = 256, ueberlapp: int = 32, melden=None) -> torch.Tensor:
        """In Stücken mit Überlappung: z [64, T] -> Ton [2, T·1920] (CPU, float32)."""
        T = z.shape[-1]
        teile = []
        for a in range(0, T, stueck):
            b = min(T, a + stueck)
            a0, b0 = max(0, a - ueberlapp), min(T, b + ueberlapp)
            ton = self(z[None, :, a0:b0].to(self.geraet))[0]
            teile.append(ton[:, (a - a0) * 1920:(a - a0) * 1920 + (b - a) * 1920].cpu())
            if melden:
                melden(f"Musik: Ton {b}/{T}")
        return torch.cat(teile, -1)


class VAEEncoder:
    """Spiegel des Decoders: Stereo 48 kHz -> 64 Kanäle bei 25 Hz (Mittelwert, ohne Zufall)."""
    SCHRITTE = (2, 4, 4, 6, 10)
    FAKTOR = 1920

    def __init__(self, datei, geraet):
        self.geraet = geraet
        self.c1w, self.c1b = _wn(datei, "encoder.conv1", geraet), datei.tensor("encoder.conv1.bias", geraet,
                                                                               torch.float32)
        self.bloecke = []
        for i, schritt in enumerate(self.SCHRITTE):
            p = f"encoder.block.{i}"
            self.bloecke.append((_res_laden(datei, p, geraet), _snake_laden(datei, f"{p}.snake1", geraet),
                                 _wn(datei, f"{p}.conv1", geraet),
                                 datei.tensor(f"{p}.conv1.bias", geraet, torch.float32), schritt))
        self.snake_aus = _snake_laden(datei, "encoder.snake1", geraet)
        self.c2w, self.c2b = _wn(datei, "encoder.conv2", geraet), datei.tensor("encoder.conv2.bias", geraet,
                                                                               torch.float32)

    @torch.no_grad()
    def __call__(self, ton: torch.Tensor) -> torch.Tensor:
        """ton: [B, 2, N] (N Vielfaches von 1920) -> [B, 64, N/1920]."""
        x = F.conv1d(ton.float(), self.c1w, self.c1b, padding=3)
        for ru, (sa, sb), w, b, schritt in self.bloecke:
            x = F.conv1d(_snake(_res(x, ru), sa, sb), w, b, stride=schritt, padding=math.ceil(schritt / 2))
        return F.conv1d(_snake(x, *self.snake_aus), self.c2w, self.c2b, padding=1)[:, :64]

    @torch.no_grad()
    def kodieren(self, ton: torch.Tensor, stueck: int = 256, ueberlapp: int = 64, melden=None) -> torch.Tensor:
        """In Stücken mit Überlappung: Ton [2, N] -> z [T, 64] (float32, auf dem Gerät), T = ⌈N/1920⌉."""
        f = self.FAKTOR
        T = -(-ton.shape[-1] // f)
        ton = F.pad(ton.float(), (0, T * f - ton.shape[-1]))
        kern = stueck - 2 * ueberlapp
        teile = []
        for a in range(0, T, kern):
            b = min(T, a + kern)
            a0, b0 = max(0, a - ueberlapp), min(T, b + ueberlapp)
            z = self(ton[None, :, a0 * f:b0 * f].to(self.geraet))[0]
            teile.append(z[:, a - a0:b - a0])
            if melden:
                melden(f"Musik: höre die Vorlage {b}/{T}")
        return torch.cat(teile, -1).T.contiguous()
