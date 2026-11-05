"""
vae_flux.py - Decoder der Flux-VAEs (8× kleiner): FLUX.1/Z-Image mit 16 Latent-Kanälen, FLUX.2 mit
32 Kanälen, post_quant_conv und BatchNorm-Werten für die Latents. Der Aufbau (Stufen, Kanäle,
Blöcke je Stufe) kommt aus der Datei.

Liest Original-Namen (`decoder.up.3.block.0.conv1`, z. B. ae.safetensors) und diffusers-Namen
(`decoder.up_blocks.0.resnets.0.conv1`).
"""

from __future__ import annotations

import re
from pathlib import Path

from .gewichte import Quelle, namen

SKALA, VERSATZ = 0.3611, 0.1159          # latent -> VAE-Eingang: z / SKALA + VERSATZ
GN_EPS = 1e-6


def kanaele(pfad: Path) -> int | None:
    """Latent-Kanäle eines 2D-VAE-Decoders (conv_in), sonst None."""
    alle = namen(pfad)
    form = alle.get("decoder.conv_in.weight") or alle.get("vae.decoder.conv_in.weight")
    return int(form[1]) if form and len(form) == 4 else None


def ist_flux_vae(pfad: Path) -> bool:
    """Flux-VAE mit 16 Latent-Kanälen (FLUX.1, Z-Image)."""
    return kanaele(pfad) == 16


def _original_name(k: str, stufen: int) -> str:
    """diffusers-Name -> Original-Name."""
    k = k.replace("decoder.conv_norm_out.", "decoder.norm_out.")
    k = re.sub(r"decoder\.mid_block\.resnets\.(\d)\.", lambda m: f"decoder.mid.block_{int(m.group(1)) + 1}.", k)
    k = k.replace("decoder.mid_block.attentions.0.group_norm.", "decoder.mid.attn_1.norm.")
    for alt, neu in (("to_q", "q"), ("to_k", "k"), ("to_v", "v"), ("to_out.0", "proj_out")):
        k = k.replace(f"decoder.mid_block.attentions.0.{alt}.", f"decoder.mid.attn_1.{neu}.")
    k = re.sub(r"decoder\.up_blocks\.(\d+)\.resnets\.(\d+)\.",
               lambda m: f"decoder.up.{stufen - 1 - int(m.group(1))}.block.{m.group(2)}.", k)
    k = re.sub(r"decoder\.up_blocks\.(\d+)\.upsamplers\.0\.",
               lambda m: f"decoder.up.{stufen - 1 - int(m.group(1))}.upsample.", k)
    return k.replace(".conv_shortcut.", ".nin_shortcut.")


def laden(pfad: Path) -> dict:
    """Decoder-Gewichte (float32, Original-Namen ohne `decoder.`) im Arbeitsspeicher."""
    import torch
    with Quelle(pfad) as q:
        vors = "vae." if q.hat("vae.decoder.conv_in.weight") else ""
        roh = {k[len(vors):]: q.tensor(k, torch.float32) for k in q.alle()
               if k.startswith(tuple(vors + x for x in ("decoder.", "post_quant_conv.", "bn.running_")))}
    stufen = 1 + max((int(m.group(1)) for k in roh if (m := re.match(r"decoder\.up_blocks\.(\d+)\.", k))),
                     default=-1)
    sd = {}
    for k, v in roh.items():
        neu = _original_name(k, stufen) if stufen > 0 else k
        if re.search(r"attn_1\.(q|k|v|proj_out)\.weight$", neu) and v.dim() == 2:
            v = v[:, :, None, None]                    # diffusers speichert die 1×1-Faltungen als Linear
        sd[neu[len("decoder."):] if neu.startswith("decoder.") else neu] = v
    return sd


def _gn(x, sd, p):
    import torch.nn.functional as F
    return F.group_norm(x, 32, sd[p + ".weight"], sd[p + ".bias"], GN_EPS)


def _conv(x, sd, p, pad=1):
    import torch.nn.functional as F
    return F.conv2d(x, sd[p + ".weight"], sd[p + ".bias"], padding=pad)


def _res(x, sd, p):
    import torch.nn.functional as F
    h = _conv(F.silu(_gn(x, sd, p + ".norm1")), sd, p + ".conv1")
    h = _conv(F.silu(_gn(h, sd, p + ".norm2")), sd, p + ".conv2")
    if p + ".nin_shortcut.weight" in sd:
        x = _conv(x, sd, p + ".nin_shortcut", pad=0)
    return x + h


def _attn(x, sd, p):
    import torch.nn.functional as F
    b, c, hh, ww = x.shape
    h = _gn(x, sd, p + ".norm")
    q, k, v = (_conv(h, sd, f"{p}.{n}", pad=0).reshape(b, 1, c, hh * ww).transpose(-1, -2) for n in "qkv")
    a = F.scaled_dot_product_attention(q, k, v)
    return x + _conv(a.transpose(-1, -2).reshape(b, c, hh, ww), sd, p + ".proj_out", pad=0)


def dekodieren(sd: dict, z):
    """Latent (1, 16, h, w), schon mit SKALA/VERSATZ zurückgerechnet -> Bild (1, 3, 8h, 8w) in [-1, 1]."""
    import torch
    import torch.nn.functional as F
    with torch.no_grad():
        if "post_quant_conv.weight" in sd:
            z = _conv(z, sd, "post_quant_conv", pad=0)
        x = _conv(z, sd, "conv_in")
        x = _res(x, sd, "mid.block_1")
        x = _attn(x, sd, "mid.attn_1")
        x = _res(x, sd, "mid.block_2")
        stufen = 1 + max(int(k.split(".")[1]) for k in sd if k.startswith("up."))
        for i in reversed(range(stufen)):
            j = 0
            while f"up.{i}.block.{j}.conv1.weight" in sd:
                x = _res(x, sd, f"up.{i}.block.{j}")
                j += 1
            if f"up.{i}.upsample.conv.weight" in sd:
                x = _conv(F.interpolate(x, scale_factor=2.0, mode="nearest"), sd, f"up.{i}.upsample.conv")
        x = _conv(F.silu(_gn(x, sd, "norm_out")), sd, "conv_out")
    return x


def bild(sd_ram: dict, latent, device, dtype, skala: float = SKALA, versatz: float = VERSATZ):
    """Latent des DiT -> uint8-Bild (H, W, 3). Rechnet in `dtype`, außer fp16 (läuft über) -> float32."""
    import torch
    from .gewichte import zu
    rechnen = torch.float32 if dtype == torch.float16 else dtype
    sd = {k: v.to(rechnen) for k, v in zu(sd_ram, device).items() if not k.startswith("bn.")}
    z = (latent.to(device, torch.float32) / skala + versatz).to(rechnen)
    x = dekodieren(sd, z)
    del sd
    return ((x[0].permute(1, 2, 0).float().clamp(-1, 1).cpu().numpy() + 1.0) * 127.5).round().astype("uint8")
