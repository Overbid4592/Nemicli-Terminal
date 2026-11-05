"""
vae_ltx2.py - Video-Decoder von LTX-2.x (Faltungs-VAE, „CausalVideoAutoencoder“): 1 Latent-Bild = 8 Bilder,
1 Latent-Zelle = 32×32 Pixel, 128 Kanäle.

Aufbau aus dem Dateikopf (`decoder_blocks`: res_x · compress_space · compress_time · compress_all), sonst wie
LTX-2. Für wenig Grafikspeicher wird in Kacheln dekodiert – räumlich und zeitlich mit Überlappung, die Ränder
werden weich überblendet.

Liest Original-Namen (decoder.up_blocks.N.res_blocks.M …) und diffusers-Namen (mid_block, upsamplers, resnets).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bildmotoren.gewichte import Quelle, st_kopf

PIXEL_EPS = 1e-8
STANDARD_BLOECKE = [["res_x", {"num_layers": 4}], ["compress_space", {"multiplier": 2, "residual": True}],
                    ["res_x", {"num_layers": 6}], ["compress_time", {"multiplier": 2, "residual": True}],
                    ["res_x", {"num_layers": 4}], ["compress_all", {"multiplier": 1, "residual": True}],
                    ["res_x", {"num_layers": 2}], ["compress_all", {"multiplier": 2, "residual": True}],
                    ["res_x", {"num_layers": 2}]]
SCHRITT = {"compress_space": (1, 2, 2), "compress_time": (2, 1, 1), "compress_all": (2, 2, 2)}


def _original(k: str) -> str:
    """diffusers-Name -> Original-Name (mid_block = up_blocks.0, danach je Stufe Upsampler und Resnets)."""
    k = k.replace("latents_mean", "per_channel_statistics.mean-of-means")
    k = k.replace("latents_std", "per_channel_statistics.std-of-means")
    k = k.replace("decoder.mid_block.resnets.", "decoder.up_blocks.0.res_blocks.")
    k = re.sub(r"decoder\.up_blocks\.(\d+)\.upsamplers\.0\.", lambda m: f"decoder.up_blocks.{2 * int(m.group(1)) + 1}.", k)
    k = re.sub(r"decoder\.up_blocks\.(\d+)\.resnets\.", lambda m: f"decoder.up_blocks.{2 * int(m.group(1)) + 2}.res_blocks.", k)
    return k


def ist_ltx2_vae(pfad: Path) -> bool:
    kopf = st_kopf(pfad)
    namen = {_original(k[4:] if k.startswith("vae.") else k) for k in kopf}
    return "per_channel_statistics.mean-of-means" in namen and any(
        k.startswith("decoder.up_blocks.") and ".res_blocks." in k for k in namen)


def _konfig(pfad: Path) -> dict:
    """Decoder-Aufbau aus dem Dateikopf; ohne Angabe wie LTX-2 (Restpfad an, Ränder gespiegelt)."""
    meta = st_kopf(pfad).get("__metadata__") or {}
    try:
        c = json.loads(meta.get("config") or "{}").get("vae", {})
    except ValueError:
        c = {}
    if "decoder_blocks" in c:
        return {"bloecke": c["decoder_blocks"], "rand": c.get("spatial_padding_mode", "zeros"),
                "kausal": bool(c.get("causal_decoder", False)), "patch": int(c.get("patch_size", 4)),
                "skala": float(c.get("scaling_factor", 1.0))}
    return {"bloecke": STANDARD_BLOECKE, "rand": "reflect", "kausal": False, "patch": 4, "skala": 1.0}


def laden(pfad: Path) -> dict:
    """Decoder-Gewichte (float32, Original-Namen ohne `decoder.`) und Aufbau im Arbeitsspeicher."""
    import torch
    with Quelle(pfad) as q:
        sd = {}
        for k in q.alle():
            o = _original(k[4:] if k.startswith("vae.") else k)
            if o.startswith(("decoder.", "per_channel_statistics.")):
                sd[o[len("decoder."):] if o.startswith("decoder.") else o] = q.tensor(k, torch.float32)
    return {"sd": sd, "cfg": _konfig(pfad)}


# ===========================================================================
#  Rechnen
# ===========================================================================

def _pixelnorm(x):
    import torch
    return x * torch.rsqrt(x.float().pow(2).mean(1, keepdim=True) + PIXEL_EPS).to(x.dtype)


def _conv(sd, p, x, cfg):
    """3D-Faltung: Zeit an den Enden mit dem Rand-Bild aufgefüllt (kausal: nur vorn), Raum nach `rand`."""
    import torch
    import torch.nn.functional as F
    w, b = sd[p + ".conv.weight"], sd.get(p + ".conv.bias")
    kt, kh, kw = w.shape[2:]
    if cfg["kausal"]:
        x = torch.cat([x[:, :, :1].expand(-1, -1, kt - 1, -1, -1), x], 2)
    elif kt > 1:
        n = (kt - 1) // 2
        x = torch.cat([x[:, :, :1].expand(-1, -1, n, -1, -1), x, x[:, :, -1:].expand(-1, -1, n, -1, -1)], 2)
    if cfg["rand"] != "zeros" and (kh > 1 or kw > 1):
        x = F.pad(x, (kw // 2, kw // 2, kh // 2, kh // 2, 0, 0), mode=cfg["rand"])
        return F.conv3d(x, w, b)
    return F.conv3d(x, w, b, padding=(0, kh // 2, kw // 2))


def _res(sd, p, x, cfg):
    import torch.nn.functional as F
    h = _conv(sd, p + ".conv1", F.silu(_pixelnorm(x)), cfg)
    h = _conv(sd, p + ".conv2", F.silu(_pixelnorm(h)), cfg)
    if p + ".conv_shortcut.weight" in sd:
        x = F.layer_norm(x.movedim(1, -1), (x.shape[1],), sd[p + ".norm3.weight"], sd[p + ".norm3.bias"], 1e-6).movedim(-1, 1)
        x = F.conv3d(x, sd[p + ".conv_shortcut.weight"], sd.get(p + ".conv_shortcut.bias"))
    return x + h


def _hoch(sd, p, x, schritt, faktor: int, rest: bool, cfg):
    """Tiefe -> Raum/Zeit (DepthToSpace); die ersten st-1 Bilder entfallen (kausale Zeitachse)."""
    b, c, f, h, w = x.shape
    st, sh, sw = schritt

    def umordnen(y):
        y = y.reshape(b, -1, st, sh, sw, f, h, w).permute(0, 1, 5, 2, 6, 3, 7, 4)
        return y.flatten(6, 7).flatten(4, 5).flatten(2, 3)[:, :, st - 1:]

    y = umordnen(_conv(sd, p, x, cfg))
    if rest:
        r = umordnen(x).repeat(1, (st * sh * sw) // faktor, 1, 1, 1)
        y = y + r
    return y


def dekodieren(vae: dict, z):
    """Latent (1, C, F, H, W) im VAE-Maßstab -> Video (1, 3, 1+8(F-1), 32H, 32W) in [-1, 1]."""
    import torch
    import torch.nn.functional as F
    sd, cfg = vae["sd"], vae["cfg"]
    with torch.no_grad():
        x = _conv(sd, "conv_in", z, cfg)
        for i, (art, par) in enumerate(cfg["bloecke"]):
            p = f"up_blocks.{i}"
            if art == "res_x":
                for j in range(int(par.get("num_layers", 1))):
                    x = _res(sd, f"{p}.res_blocks.{j}", x, cfg)
            elif art in SCHRITT:
                x = _hoch(sd, f"{p}.conv", x, SCHRITT[art], int(par.get("multiplier", 1)),
                          bool(par.get("residual", False)), cfg)
            else:
                raise RuntimeError(f"Unbekannter VAE-Block: {art}")
        x = _conv(sd, "conv_out", F.silu(_pixelnorm(x)), cfg)
        b, c, f, h, w = x.shape
        p = cfg["patch"]
        x = x.reshape(b, -1, 1, p, p, f, h, w).permute(0, 1, 5, 2, 6, 4, 7, 3).flatten(6, 7).flatten(4, 5).flatten(2, 3)
    return x


def entnormen(vae: dict, lat):
    """Latent des DiT -> VAE-Maßstab (per_channel_statistics)."""
    sd = vae["sd"]
    m = sd["per_channel_statistics.mean-of-means"].view(1, -1, 1, 1, 1).to(lat.device)
    s = sd["per_channel_statistics.std-of-means"].view(1, -1, 1, 1, 1).to(lat.device)
    return lat * s / vae["cfg"]["skala"] + m


# ===========================================================================
#  Kacheln
# ===========================================================================

def _rampe(n: int, rand: int, links: bool, rechts: bool, device):
    import torch
    g = torch.ones(n, device=device)
    if rand > 0:
        r = torch.linspace(0, 1, rand + 2, device=device)[1:-1]
        if links:
            g[:rand] = r
        if rechts:
            g[-rand:] = r.flip(0)
    return g


def _starts(n: int, kachel: int, ueber: int) -> list[int]:
    if n <= kachel:
        return [0]
    schritt = kachel - ueber
    s = list(range(0, n - kachel, schritt)) + [n - kachel]
    return sorted(set(s))


def dekodieren_kacheln(vae: dict, z, device, dtype, kachel=(16, 16, 16), ueber=(2, 4, 4), on_status=None):
    """Wie dekodieren, aber Stück für Stück (Latent-Kacheln f×h×w mit Überlappung) – Ergebnis auf der CPU (float32)."""
    import torch
    from bildmotoren.gewichte import zu
    _, C, F, H, W = z.shape
    ff, hh, ww = 1 + 8 * (F - 1), 32 * H, 32 * W
    aus = torch.zeros(1, 3, ff, hh, ww)
    summe = torch.zeros(1, 1, ff, hh, ww)
    rechnen = torch.float32 if dtype == torch.float16 else dtype
    karte = {"sd": {k: v.to(rechnen) for k, v in zu(vae["sd"], device).items()}, "cfg": vae["cfg"]}
    ts, hs, ws = _starts(F, kachel[0], ueber[0]), _starts(H, kachel[1], ueber[1]), _starts(W, kachel[2], ueber[2])
    n, i = len(ts) * len(hs) * len(ws), 0
    for t0 in ts:
        for h0 in hs:
            for w0 in ws:
                t1, h1, w1 = min(t0 + kachel[0], F), min(h0 + kachel[1], H), min(w0 + kachel[2], W)
                stueck = dekodieren(karte, z[:, :, t0:t1, h0:h1, w0:w1].to(device, rechnen)).float().cpu()
                a = 0 if t0 == 0 else 8 * t0                  # Pixelbild des ersten Latent-Bildes der Kachel
                gt = _rampe(stueck.shape[2], 8 * ueber[0] // 2, t0 > 0, t1 < F, "cpu")
                gh = _rampe(stueck.shape[3], 32 * ueber[1] // 2, h0 > 0, h1 < H, "cpu")
                gw = _rampe(stueck.shape[4], 32 * ueber[2] // 2, w0 > 0, w1 < W, "cpu")
                g = gt.view(1, 1, -1, 1, 1) * gh.view(1, 1, 1, -1, 1) * gw.view(1, 1, 1, 1, -1)
                sl = (slice(None), slice(None), slice(a, a + stueck.shape[2]),
                      slice(32 * h0, 32 * h0 + stueck.shape[3]), slice(32 * w0, 32 * w0 + stueck.shape[4]))
                aus[sl] += stueck * g
                summe[sl] += g
                i += 1
                if on_status:
                    on_status(f"VAE: Kachel {i}/{n}")
    del karte
    return aus / summe.clamp(min=1e-6)
