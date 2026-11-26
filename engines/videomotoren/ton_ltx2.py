"""
ton_ltx2.py - Der Ton von LTX-2.x: Latents -> Ton-VAE (Mel-Spektrogramm, Stereo) -> Vocoder (BigVGAN mit
SnakeBeta, 16 kHz) -> Bandbreiten-Erweiterung auf 48 kHz.

Liest die Original-Namen (audio_vae.decoder …, vocoder.vocoder.conv_pre/ups/resblocks/act_post/conv_post,
vocoder.bwe_generator, vocoder.mel_stft) und diffusers-Namen (conv_in, upsamplers, resnets, act_out, conv_out).
Aufbau aus dem Dateikopf, sonst wie LTX-2.5.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from bildmotoren.gewichte import Quelle, st_kopf

LATENT_FAKTOR = 4                       # Zeit: 1 Ton-Latent = 4 Mel-Bilder
STANDARD = {
    "vae": {"ch": 128, "ch_mult": [1, 2, 4], "num_res_blocks": 2, "z_channels": 8, "mel_bins": 64, "out_ch": 2,
            "causality_axis": "height", "norm_type": "pixel"},
    "vocoder": {"upsample_rates": [5, 2, 2, 2, 2, 2], "resblock_kernel_sizes": [3, 7, 11],
                "resblock_dilation_sizes": [[1, 3, 5]] * 3, "activation": "snakebeta"},
    "bwe": {"upsample_rates": [6, 5, 2, 2, 2], "resblock_kernel_sizes": [3, 7, 11],
            "resblock_dilation_sizes": [[1, 3, 5]] * 3, "activation": "snakebeta", "hop_length": 80,
            "input_sampling_rate": 16000, "output_sampling_rate": 48000},
}
_DIFFUSERS = [("conv_in.", "conv_pre."), ("upsamplers.", "ups."), ("resnets.", "resblocks."), ("act_out.", "act_post."),
              ("conv_out.", "conv_post."), (".downsample.filter", ".downsample.lowpass.filter")]


def ist_ltx2_ton(pfad: Path) -> bool:
    kopf = st_kopf(pfad)
    return any(k.startswith("audio_vae.decoder.") or k.startswith("decoder.mid.block_1.") for k in kopf) and \
        any("per_channel_statistics" in k or "latents_mean" in k for k in kopf)


def _konfig(pfad: Path) -> dict:
    meta = st_kopf(pfad).get("__metadata__") or {}
    try:
        c = json.loads(meta.get("config") or "{}")
    except ValueError:
        c = {}
    vae = c.get("audio_vae", {}).get("model", {}).get("params", {}).get("ddconfig")
    voc = c.get("vocoder", {})
    return {"vae": vae or STANDARD["vae"], "vocoder": voc.get("vocoder") or STANDARD["vocoder"],
            "bwe": voc.get("bwe") or STANDARD["bwe"]}


def laden(pfad: Path) -> dict:
    """Ton-VAE-Decoder, Vocoder und Erweiterer (float32) im Arbeitsspeicher."""
    import torch
    with Quelle(pfad) as q:
        sd = {}
        for k in q.alle():
            if "encoder." in k:
                continue
            n = k
            for alt, neu in _DIFFUSERS:
                if n.startswith(("vocoder.", "bwe_generator.")) or ".vocoder." in n:
                    n = n.replace(alt, neu)
            n = n.replace("latents_mean", "per_channel_statistics.mean-of-means")
            n = n.replace("latents_std", "per_channel_statistics.std-of-means")
            sd[n] = q.tensor(k, torch.float32)
    if not any(k.startswith("audio_vae.") for k in sd):              # diffusers: Ton-VAE ohne Vorsatz
        sd = {("audio_vae." + k if k.startswith(("decoder.", "per_channel")) else k): v for k, v in sd.items()}
    return {"sd": sd, "cfg": _konfig(pfad)}


# ===========================================================================
#  Ton-VAE
# ===========================================================================

def _pixelnorm(x, eps=1e-6):
    import torch
    return x * torch.rsqrt(x.pow(2).mean(1, keepdim=True) + eps)


def _conv2(sd, p, x, achse):
    import torch.nn.functional as F
    w, b = sd[p + ".conv.weight"], sd.get(p + ".conv.bias")
    kh, kw = w.shape[2:]
    ph, pw = kh - 1, kw - 1
    if achse == "height":
        pad = (pw // 2, pw - pw // 2, ph, 0)
    elif achse in ("width", "width-compatibility"):
        pad = (pw, 0, ph // 2, ph - ph // 2)
    else:
        pad = (pw // 2, pw - pw // 2, ph // 2, ph - ph // 2)
    return F.conv2d(F.pad(x, pad), w, b)


def _res2(sd, p, x, achse):
    import torch.nn.functional as F
    h = _conv2(sd, p + ".conv1", F.silu(_pixelnorm(x)), achse)
    h = _conv2(sd, p + ".conv2", F.silu(_pixelnorm(h)), achse)
    if p + ".nin_shortcut.conv.weight" in sd:
        x = _conv2(sd, p + ".nin_shortcut", x, achse)
    elif p + ".conv_shortcut.conv.weight" in sd:
        x = _conv2(sd, p + ".conv_shortcut", x, achse)
    return x + h


def mel(ton: dict, z):
    """Ton-Latents (1, 8, T, 16) -> Mel-Spektrogramm (1, Kanäle, 4T-3, 64)."""
    import torch
    import torch.nn.functional as F
    sd, c = ton["sd"], ton["cfg"]["vae"]
    achse = c.get("causality_axis", "height")
    p = "audio_vae.decoder."
    with torch.no_grad():
        T = z.shape[2]
        x = _conv2(sd, p + "conv_in", z, achse)
        x = _res2(sd, p + "mid.block_1", x, achse)
        x = _res2(sd, p + "mid.block_2", x, achse)
        n_ebenen = len(c.get("ch_mult", [1, 2, 4]))
        for ebene in reversed(range(n_ebenen)):
            j = 0
            while f"{p}up.{ebene}.block.{j}.conv1.conv.weight" in sd:
                x = _res2(sd, f"{p}up.{ebene}.block.{j}", x, achse)
                j += 1
            if ebene != 0 and f"{p}up.{ebene}.upsample.conv.conv.weight" in sd:
                x = _conv2(sd, f"{p}up.{ebene}.upsample.conv", F.interpolate(x, scale_factor=2.0, mode="nearest"), achse)
                if achse == "height":
                    x = x[:, :, 1:]
                elif achse == "width":
                    x = x[:, :, :, 1:]
        x = _conv2(sd, p + "conv_out", F.silu(_pixelnorm(x)), achse)
        ziel_t = max(T * LATENT_FAKTOR - (LATENT_FAKTOR - 1), 1) if achse else T * LATENT_FAKTOR
        ziel_f = int(c.get("mel_bins", 64))
        x = x[:, : int(c.get("out_ch", 2)), :ziel_t, :ziel_f]
        if x.shape[2] < ziel_t or x.shape[3] < ziel_f:
            x = F.pad(x, (0, ziel_f - x.shape[3], 0, ziel_t - x.shape[2]))
    return x


def entnormen(ton: dict, lat):
    """Gepackte Ton-Latents (T, 128) des DiT -> VAE-Maßstab."""
    sd = ton["sd"]
    return lat * sd["audio_vae.per_channel_statistics.std-of-means"].to(lat.device) + \
        sd["audio_vae.per_channel_statistics.mean-of-means"].to(lat.device)


# ===========================================================================
#  Vocoder (BigVGAN mit SnakeBeta und Anti-Aliasing)
# ===========================================================================

def _kaiser(cutoff: float, half_width: float, k: int):
    import torch
    delta_f = 4 * half_width
    half = k // 2
    a = 2.285 * (half - 1) * math.pi * delta_f + 7.95
    beta = 0.1102 * (a - 8.7) if a > 50 else (0.5842 * (a - 21) ** 0.4 + 0.07886 * (a - 21) if a >= 21 else 0.0)
    fenster = torch.kaiser_window(k, beta=beta, periodic=False)
    t = (torch.arange(-half, half) + 0.5) if k % 2 == 0 else (torch.arange(k) - half)
    t = 2 * cutoff * t
    sinc = torch.where(t == 0, torch.ones_like(t), torch.sin(math.pi * t) / math.pi / t)
    f = 2 * cutoff * fenster * sinc
    return (f / f.sum()).view(1, 1, k)


def _hoch(x, filt, ratio: int = 2):
    import torch.nn.functional as F
    k = filt.shape[-1]
    pad = k // ratio - 1
    links = pad * ratio + (k - ratio) // 2
    rechts = pad * ratio + (k - ratio + 1) // 2
    c = x.shape[1]
    x = F.pad(x, (pad, pad), mode="replicate")
    x = ratio * F.conv_transpose1d(x, filt.to(x.dtype).expand(c, -1, -1), stride=ratio, groups=c)
    return x[..., links:-rechts]


def _runter(x, filt, ratio: int = 2):
    import torch.nn.functional as F
    k = filt.shape[-1]
    c = x.shape[1]
    x = F.pad(x, (k // 2 + k % 2 - 1, k // 2), mode="replicate")
    return F.conv1d(x, filt.to(x.dtype).expand(c, -1, -1), stride=ratio, groups=c)


def _snake(sd, p, x):
    """Anti-Aliasing-Aktivierung: hochtasten, SnakeBeta, runtertasten."""
    import torch
    a = torch.exp(sd[p + ".act.alpha"]).view(1, -1, 1)
    b = torch.exp(sd[p + ".act.beta"]).view(1, -1, 1)
    hoch = sd.get(p + ".upsample.filter")
    runter = sd.get(p + ".downsample.lowpass.filter")
    hoch = _kaiser(0.25, 0.3, 12) if hoch is None else hoch
    runter = _kaiser(0.25, 0.3, 12) if runter is None else runter
    x = _hoch(x, hoch.to(x.device))
    x = x + (1.0 / (b + 1e-9)) * torch.sin(x * a).pow(2)
    return _runter(x, runter.to(x.device))


def _generator(sd, p, c, mel_):
    """mel_ (1, Kanäle, T, Mel) -> Wellenform (1, Kanäle_aus, T·∏raten)."""
    import torch
    import torch.nn.functional as F
    x = mel_.transpose(2, 3).flatten(1, 2)
    x = F.conv1d(x, sd[p + "conv_pre.weight"], sd.get(p + "conv_pre.bias"), padding=3)
    kerne = c.get("resblock_kernel_sizes", [3, 7, 11])
    dil = c.get("resblock_dilation_sizes", [[1, 3, 5]] * 3)
    n_res = len(kerne)
    for i, rate in enumerate(c.get("upsample_rates")):
        w = sd[f"{p}ups.{i}.weight"]
        k = w.shape[-1]
        x = F.conv_transpose1d(x, w, sd.get(f"{p}ups.{i}.bias"), stride=rate, padding=(k - rate) // 2)
        aus = []
        for j in range(n_res):
            r = f"{p}resblocks.{i * n_res + j}"
            h = x
            for m, d in enumerate(dil[j]):
                t = _snake(sd, f"{r}.acts1.{m}", h)
                kk = sd[f"{r}.convs1.{m}.weight"].shape[-1]
                t = F.conv1d(t, sd[f"{r}.convs1.{m}.weight"], sd.get(f"{r}.convs1.{m}.bias"), dilation=d,
                             padding=d * (kk - 1) // 2)
                t = _snake(sd, f"{r}.acts2.{m}", t)
                t = F.conv1d(t, sd[f"{r}.convs2.{m}.weight"], sd.get(f"{r}.convs2.{m}.bias"), padding=(kk - 1) // 2)
                h = h + t
            aus.append(h)
        x = torch.stack(aus, 0).mean(0)
    x = _snake(sd, p + "act_post", x)
    x = F.conv1d(x, sd[p + "conv_post.weight"], sd.get(p + "conv_post.bias"), padding=3)
    if c.get("use_tanh_at_final"):
        x = torch.tanh(x)
    return x


def _hann_hoch(x, ratio: int):
    """Resampler (Hann-gefensterter Sinc) wie diffusers UpSample1d(window_type="hann")."""
    import torch
    import torch.nn.functional as F
    rolloff, breite_lp = 0.99, 6
    breite = math.ceil(breite_lp / rolloff)
    k = 2 * breite * ratio + 1
    links, rechts = 2 * breite * ratio, k - ratio
    t = (torch.arange(k) / ratio - breite) * rolloff
    fenster = torch.cos(t.clamp(-breite_lp, breite_lp) * math.pi / breite_lp / 2) ** 2
    filt = (torch.sinc(t) * fenster * rolloff / ratio).view(1, 1, -1).to(x.device, x.dtype)
    c = x.shape[1]
    x = F.pad(x, (breite, breite), mode="replicate")
    x = ratio * F.conv_transpose1d(x, filt.expand(c, -1, -1), stride=ratio, groups=c)
    return x[..., links:-rechts]


def wellenform(ton: dict, mel_):
    """Mel (1, 2, T, 64) -> Stereo-Wellenform (2, Proben) und Abtastrate."""
    import torch
    import torch.nn.functional as F
    sd, cfg = ton["sd"], ton["cfg"]
    with torch.no_grad():
        x = _generator(sd, "vocoder.vocoder.", cfg["vocoder"], mel_)
        if "vocoder.bwe_generator.conv_pre.weight" not in sd:
            return x[0].clamp(-1, 1), int(cfg["bwe"].get("input_sampling_rate", 16000))
        b = cfg["bwe"]
        hop = int(b.get("hop_length", 80))
        rate_ein, rate_aus = int(b.get("input_sampling_rate", 16000)), int(b.get("output_sampling_rate", 48000))
        n = x.shape[-1]
        if n % hop:
            x = F.pad(x, (0, hop - n % hop))
        # kausale STFT mit den Basen aus der Datei, dann log-Mel
        welle = x.flatten(0, 1)[:, None]
        fb = sd["vocoder.mel_stft.stft_fn.forward_basis"].to(x.device)
        fenster = fb.shape[-1]
        spec = F.conv1d(F.pad(welle, (max(0, fenster - hop), 0)), fb, stride=hop)
        nf = spec.shape[1] // 2
        betrag = torch.sqrt(spec[:, :nf] ** 2 + spec[:, nf:] ** 2)
        m = torch.log(torch.clamp(torch.matmul(sd["vocoder.mel_stft.mel_basis"].to(x.device), betrag), min=1e-5))
        m = m.unflatten(0, (-1, x.shape[1])).transpose(2, 3)
        rest = _generator(sd, "vocoder.bwe_generator.", b, m)
        welle = torch.clamp(rest + _hann_hoch(x, rate_aus // rate_ein), -1, 1)
        return welle[0, :, : n * rate_aus // rate_ein], rate_aus
