"""
Ton für Gemma-4-Audio-Encoder (EmbeddingGemma 2, Gemma 4): Welle -> Log-Mel-Merkmale.

Nachbau von transformers' Gemma4AudioFeatureExtractor mit den Werten aus processor_config.json:
16 kHz mono, Fenster 320 (20 ms, periodisches Hann), Schritt 160 (10 ms), FFT 512, 128 Mel-Bänder (HTK,
0–8000 Hz, ohne Normierung), log(Mel + 0,001). Vorne frame_length // 2 Nullen („semicausal“), Rahmen aus
frame_length + 1 Proben, deren letzte Probe wegfällt. Die Länge wird auf ein Vielfaches von 128 Proben
aufgefüllt; Rahmen, deren Fenster in die Auffüllung reicht, gelten als ungültig und werden 0.

Ein Stück hat höchstens MAX_PROBEN (30 s); längere Aufnahmen teilt `stuecke` auf.
"""
from __future__ import annotations

import math

import numpy as np

RATE = 16_000
MEL = 128
FENSTER = 320
SCHRITT = 160
FFT = 512
MEL_BODEN = 1e-3
MAX_PROBEN = 480_000             # 30 s je Stück (max_length des Feature-Extractors)
AUFFUELLEN = 128                 # pad_to_multiple_of


def _hz_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_hz(m):
    return 700.0 * (np.power(10.0, m / 2595.0) - 1.0)


def _filterbank() -> np.ndarray:
    """(FFT // 2 + 1, MEL) Dreiecksfilter wie transformers.audio_utils.mel_filter_bank (htk, norm=None)."""
    mitten = _mel_hz(np.linspace(_hz_mel(0.0), _hz_mel(8000.0), MEL + 2))
    fft_hz = np.linspace(0, RATE // 2, FFT // 2 + 1)
    abstand = np.diff(mitten)
    steigung = mitten[None, :] - fft_hz[:, None]
    ab = -steigung[:, :-2] / abstand[:-1]
    auf = steigung[:, 2:] / abstand[1:]
    return np.maximum(0.0, np.minimum(ab, auf))


_FILTER = _filterbank()
_HANN = np.hanning(FENSTER + 1)[:-1].astype(np.float32)


def stuecke(welle: np.ndarray, max_stuecke: int) -> list[np.ndarray]:
    """Lange Welle in Stücke zu höchstens 30 s, gleichmäßig über die ganze Aufnahme verteilt."""
    welle = np.asarray(welle, np.float32).reshape(-1)
    n = max(1, math.ceil(len(welle) / MAX_PROBEN))
    if n <= max_stuecke:
        return [welle[i * MAX_PROBEN:(i + 1) * MAX_PROBEN] for i in range(n)]
    starts = np.linspace(0, len(welle) - MAX_PROBEN, max_stuecke).astype(int)
    return [welle[s:s + MAX_PROBEN] for s in starts]


def merkmale(welle: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """16-kHz-Mono-Welle (höchstens 30 s) -> (Merkmale (T, 128) float32, gültig (T,) bool)."""
    x = np.asarray(welle, np.float32).reshape(-1)[:MAX_PROBEN]
    if x.size == 0:
        raise ValueError("empty audio")
    gueltig = np.ones(len(x), bool)
    rest = (-len(x)) % AUFFUELLEN
    if rest:
        x = np.pad(x, (0, rest))
        gueltig = np.pad(gueltig, (0, rest))
    links = FENSTER // 2
    x = np.pad(x, (links, 0))
    gueltig = np.pad(gueltig, (links, 0))
    rahmen = FENSTER + 1
    n = (len(x) - rahmen) // SCHRITT + 1
    if n <= 0:
        raise ValueError("audio too short")
    idx = np.arange(n)[:, None] * SCHRITT + np.arange(rahmen)[None, :]
    stuecke_ = x[idx][:, :-1] * _HANN
    spektrum = np.abs(np.fft.rfft(stuecke_, n=FFT, axis=-1))
    mel = np.log(spektrum @ _FILTER + MEL_BODEN).astype(np.float32)
    maske = gueltig[np.arange(n) * SCHRITT + rahmen - 1]
    return mel * maske[:, None], maske


def token_zahl(n_rahmen: int) -> int:
    """Ausgabe-Token des Encoders: zwei Faltungen mit Schritt 2 (Kern 3, Rand 1)."""
    for _ in range(2):
        n_rahmen = (n_rahmen + 2 - 3) // 2 + 1
    return n_rahmen


# ===========================================================================
#  Gemma-4-Audio-Encoder (Conformer nach USM), Nachbau von transformers' Gemma4AudioModel
# ===========================================================================
#
#  Merkmale (T, 128) -> zwei Conv2d 3x3 / Schritt 2 (je LayerNorm ohne Bias, ReLU) -> Linear auf hidden
#  -> n Conformer-Schichten: FFN/2 · Block-Aufmerksamkeit (Blöcke zu 12, 12 zurück, 0 voraus, relative Lage,
#  Softcap 50) · Leicht-Faltung (GLU, kausale Tiefen-Faltung Kern 5, SiLU) · FFN/2 · RMSNorm
#  -> Linear (mit Bias) auf output_proj_dims -> Einbetter des Sprachmodells (RMSNorm ohne Gewicht + Linear).

import torch                                                              # noqa: E402
import torch.nn.functional as F                                           # noqa: E402


class Hoeren:
    """Audio-Encoder eines Gemma-4-mmproj. `holen(name)` liefert Gewichte nach den Namen von transformers
    (ohne „audio_tower.“), `cfg` die Werte von Gemma4AudioConfig."""

    def __init__(self, holen, cfg: dict, device, dtype=torch.float32):
        self.cfg, self.device, self.dtype = cfg, torch.device(device), dtype
        g = lambda n: holen(n).to(self.device, torch.float32)
        self.k0, self.n0 = g("subsample_conv_projection.layer0.conv.weight"), g("subsample_conv_projection.layer0.norm.weight")
        self.k1, self.n1 = g("subsample_conv_projection.layer1.conv.weight"), g("subsample_conv_projection.layer1.norm.weight")
        self.inp = g("subsample_conv_projection.input_proj_linear.weight")
        self.schichten = []
        for i in range(int(cfg["num_hidden_layers"])):
            p = f"layers.{i}."
            s = {n: g(p + n) for n in _SCHICHT_GEWICHTE}
            s["clip"] = {n: tuple(float(holen(p + n + "." + k)) for k in ("input_min", "input_max", "output_min",
                                                                           "output_max"))
                         for n in _CLIP_LINEAR} if cfg.get("use_clipped_linears") else {}
            self.schichten.append(s)
        self.out_w, self.out_b = g("output_proj.weight"), g("output_proj.bias")
        self.embed = g("embed_audio.embedding_projection.weight")
        H = int(cfg["hidden_size"])
        self.H, self.kopf = H, int(cfg["num_attention_heads"])
        self.D = H // self.kopf
        self.block = int(cfg["attention_chunk_size"])
        self.zurueck = int(cfg["attention_context_left"]) - 1
        self.voraus = int(cfg["attention_context_right"])
        self.kontext = self.block + self.zurueck + self.voraus
        n = H // 2
        schritt = math.log(10000.0) / max(n - 1, 1)
        zeiten = torch.exp(torch.arange(n, dtype=torch.float32) * -schritt)
        lage = torch.arange(self.kontext // 2, -1, -1, dtype=torch.float32)[:, None] * zeiten[None, :]
        self.lage = torch.cat([torch.sin(lage), torch.cos(lage)], -1).to(self.device)       # (kontext//2+1, H)
        self.eps = float(cfg.get("rms_norm_eps", 1e-6))

    # -- Bausteine ---------------------------------------------------------------
    def _rms(self, x, w=None):
        x = x * torch.pow(x.pow(2).mean(-1, keepdim=True) + self.eps, -0.5)
        return x * w if w is not None else x

    def _lin(self, s, name, x):
        grenzen = s["clip"].get(name)
        if grenzen:
            x = x.clamp(grenzen[0], grenzen[1])
        x = x @ s[name + ".linear.weight"].T
        if grenzen:
            x = x.clamp(grenzen[2], grenzen[3])
        return x

    def _ffn(self, s, p, x):
        r = x
        x = self._rms(x, s[p + ".pre_layer_norm.weight"])
        x = self._lin(s, p + ".ffw_layer_2", F.silu(self._lin(s, p + ".ffw_layer_1", x)))
        x = self._rms(x, s[p + ".post_layer_norm.weight"])
        return r + x * float(self.cfg.get("residual_weight", 0.5))

    def _aufmerksamkeit(self, s, x, gueltig):
        T, Hk, D, B = x.shape[0], self.kopf, self.D, self.block
        q = self._lin(s, "self_attn.q_proj", x).view(T, Hk, D)
        k = self._lin(s, "self_attn.k_proj", x).view(T, Hk, D)
        v = self._lin(s, "self_attn.v_proj", x).view(T, Hk, D)
        q = q * ((D ** -0.5) / math.log(2)) * F.softplus(s["self_attn.per_dim_scale"])
        k = k * (math.log(1 + math.e) / math.log(2))
        nb = (T + B - 1) // B
        qb = F.pad(q, (0, 0, 0, 0, 0, nb * B - T)).view(nb, B, Hk, D)
        rand = (self.zurueck, self.voraus + B - 1)
        kb = F.pad(k, (0, 0, 0, 0, *rand)).unfold(0, self.kontext, B).permute(0, 3, 1, 2)   # (nb, ctx, Hk, D)
        vb = F.pad(v, (0, 0, 0, 0, *rand)).unfold(0, self.kontext, B).permute(0, 3, 1, 2)
        rel = (self.lage @ s["self_attn.relative_k_proj.weight"].T).view(-1, Hk, D)        # (P, Hk, D)
        ac = torch.einsum("nbhd,nchd->hnbc", qb, kb)
        bd = torch.einsum("nbhd,phd->hnbp", qb, rel)
        P = bd.shape[-1]
        bd = F.pad(bd, (0, self.kontext + 1 - P)).reshape(Hk, nb, B * (self.kontext + 1))
        bd = bd[..., :B * self.kontext].reshape(Hk, nb, B, self.kontext)
        w = ac + bd
        cap = float(self.cfg.get("attention_logit_cap", 50.0))
        w = torch.tanh(w / cap) * cap
        # erlaubt wie sliding_window_mask_function((context_left - 1, context_right)): 0 <= q - kv < zurück
        # oder 0 < kv - q < voraus; dazu müssen Anfrage und Schlüssel echte Token sein
        qi = (torch.arange(nb, device=x.device)[:, None] * B + torch.arange(B, device=x.device)[None, :])  # (nb, B)
        ki = torch.arange(nb, device=x.device)[:, None] * B - self.zurueck + torch.arange(self.kontext,
                                                                                        device=x.device)[None, :]
        abstand = qi[:, :, None] - ki[:, None, :]
        erlaubt = ((abstand >= 0) & (abstand < self.zurueck)) | ((abstand < 0) & (-abstand < self.voraus))
        gq = F.pad(gueltig, (0, nb * B - T))[qi]
        gk = F.pad(gueltig, (self.zurueck, self.voraus + B - 1))[ki + self.zurueck]
        erlaubt = erlaubt & gq[:, :, None] & gk[:, None, :]
        w = w.masked_fill(~erlaubt[None], float(self.cfg.get("attention_invalid_logits_value", -1e9)))
        a = torch.softmax(w, -1)
        o = torch.einsum("hnbc,nchd->nbhd", a, vb).reshape(nb * B, Hk * D)[:T]
        return self._lin(s, "self_attn.post", o)

    def _leichtfaltung(self, s, x):
        r = x
        x = self._rms(x, s["lconv1d.pre_layer_norm.weight"])
        x = F.glu(self._lin(s, "lconv1d.linear_start", x), dim=-1)
        kern = s["lconv1d.depthwise_conv1d.weight"]                            # (H, 1, K)
        x = F.conv1d(F.pad(x.T[None], (kern.shape[-1] - 1, 0)), kern, groups=self.H)[0].T
        x = self._rms(x, s["lconv1d.conv_norm.weight"])
        return r + self._lin(s, "lconv1d.linear_end", F.silu(x))

    def _faltung(self, x, kern, norm, maske):
        x = x * maske[None, None, :, None].to(x.dtype)
        x = F.conv2d(x, kern, stride=2, padding=1)
        x = F.layer_norm(x.permute(0, 2, 3, 1), (x.shape[1],), norm, None, self.eps).permute(0, 3, 1, 2)
        return F.relu(x), maske[::2]

    @torch.inference_mode()
    def encode(self, merkmale_: np.ndarray, gueltig: np.ndarray) -> torch.Tensor:
        """(T, 128) Merkmale + Gültigkeit -> (n, Breite des Sprachmodells) Zeilen der gültigen Token."""
        x = torch.as_tensor(merkmale_, dtype=torch.float32, device=self.device)[None, None]   # (1, 1, T, 128)
        m = torch.as_tensor(gueltig, dtype=torch.bool, device=self.device)
        x, m = self._faltung(x, self.k0, self.n0, m)
        x, m = self._faltung(x, self.k1, self.n1, m)
        x = x[0].permute(1, 2, 0).reshape(x.shape[2], -1) @ self.inp.T                       # (T', H)
        for s in self.schichten:
            x = self._ffn(s, "feed_forward1", x)
            r = x
            x = self._rms(x, s["norm_pre_attn.weight"])
            x = self._rms(self._aufmerksamkeit(s, x, m), s["norm_post_attn.weight"]) + r
            x = self._leichtfaltung(s, x)
            x = self._ffn(s, "feed_forward2", x)
            x = self._rms(x, s["norm_out.weight"])
        x = x @ self.out_w.T + self.out_b
        x = self._rms(x) @ self.embed.T                                                       # embed_audio
        return x[m]


_CLIP_LINEAR = ("feed_forward1.ffw_layer_1", "feed_forward1.ffw_layer_2", "feed_forward2.ffw_layer_1",
                "feed_forward2.ffw_layer_2", "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
                "self_attn.post", "lconv1d.linear_start", "lconv1d.linear_end")
_SCHICHT_GEWICHTE = tuple(n + ".linear.weight" for n in _CLIP_LINEAR) + (
    "feed_forward1.pre_layer_norm.weight", "feed_forward1.post_layer_norm.weight",
    "feed_forward2.pre_layer_norm.weight", "feed_forward2.post_layer_norm.weight",
    "self_attn.relative_k_proj.weight", "self_attn.per_dim_scale",
    "lconv1d.depthwise_conv1d.weight", "lconv1d.pre_layer_norm.weight", "lconv1d.conv_norm.weight",
    "norm_pre_attn.weight", "norm_post_attn.weight", "norm_out.weight")
