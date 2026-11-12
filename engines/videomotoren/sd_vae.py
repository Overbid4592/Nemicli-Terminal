"""
sd_vae.py - Die VAE von Stable Diffusion aus dem Checkpoint (8× kleiner, 4 Latent-Kanäle): Bilder aus
Latents auspacken und ein Startbild in ein Latent einpacken.

Der Decoder ist derselbe Aufbau wie bei den Flux-VAEs (bildmotoren/vae_flux.py); hier kommt der Encoder dazu.
Gerechnet wird in float32 – die SDXL-VAE läuft mit halber Genauigkeit über.
"""

from __future__ import annotations

from pathlib import Path

from bildmotoren import vae_flux
from bildmotoren.gewichte import Quelle

VORSATZ = "first_stage_model."
SKALA = {"sd15": 0.18215, "sdxl": 0.13025}
STAPEL = 2                                  # so viele Bilder packt der Decoder auf einmal aus (512×768: rund 3 GB je Bild)


def laden(pfad: Path) -> dict:
    """Encoder und Decoder (float32, Original-Namen) im Arbeitsspeicher."""
    import torch
    with Quelle(pfad, VORSATZ) as q:
        sd = {k: q.tensor(k, torch.float32) for k in q.alle()
              if k.startswith(("encoder.", "decoder.", "quant_conv.", "post_quant_conv."))}
    if "decoder.conv_in.weight" not in sd or "encoder.conv_in.weight" not in sd:
        raise RuntimeError(f"{Path(pfad).name}: keine VAE im Checkpoint")
    for k, v in list(sd.items()):                # ältere Dateien speichern die 1×1-Faltungen der Mitte als Linear
        if ".mid.attn_1." in k and k.endswith(".weight") and v.dim() == 2:
            sd[k] = v[:, :, None, None]
    return sd


def _teil(sd: dict, vorsatz: str) -> dict:
    return {k[len(vorsatz):]: v for k, v in sd.items() if k.startswith(vorsatz)}


def auspacken(sd_ram: dict, latents, art: str, device, on_status=None):
    """Latents (Bilder, 4, h, w) -> uint8-Bilder (Bilder, 8h, 8w, 3)."""
    import numpy as np
    import torch
    dec = {k: v.to(device) for k, v in _teil(sd_ram, "decoder.").items()}
    dec.update({"post_quant_conv." + k: v.to(device) for k, v in _teil(sd_ram, "post_quant_conv.").items()})
    aus = []
    for a in range(0, latents.shape[0], STAPEL):
        if on_status:
            on_status(f"Bilder auspacken {min(a + STAPEL, latents.shape[0])}/{latents.shape[0]} …")
        z = latents[a:a + STAPEL].to(device, torch.float32) / SKALA[art]
        x = vae_flux.dekodieren(dec, z)
        aus.append(((x.permute(0, 2, 3, 1).clamp(-1, 1) + 1.0) * 127.5).round().to(torch.uint8).cpu().numpy())
    return np.concatenate(aus, axis=0)


def einpacken(sd_ram: dict, bild, art: str, device):
    """uint8-Bild (H, W, 3), Seiten durch 8 teilbar -> Latent (1, 4, H/8, W/8): der Mittelwert, ohne Rauschen."""
    import torch
    import torch.nn.functional as F
    enc = {k: v.to(device) for k, v in _teil(sd_ram, "encoder.").items()}
    with torch.no_grad():
        x = torch.tensor(bild).to(device, torch.float32).permute(2, 0, 1)[None] / 127.5 - 1.0
        x = vae_flux._conv(x, enc, "conv_in")
        stufen = 1 + max(int(k.split(".")[1]) for k in enc if k.startswith("down."))
        for i in range(stufen):
            j = 0
            while f"down.{i}.block.{j}.conv1.weight" in enc:
                x = vae_flux._res(x, enc, f"down.{i}.block.{j}")
                j += 1
            if f"down.{i}.downsample.conv.weight" in enc:      # ungleich gefüllt: nur rechts und unten
                x = F.conv2d(F.pad(x, (0, 1, 0, 1)), enc[f"down.{i}.downsample.conv.weight"],
                             enc[f"down.{i}.downsample.conv.bias"], stride=2)
        x = vae_flux._res(x, enc, "mid.block_1")
        x = vae_flux._attn(x, enc, "mid.attn_1")
        x = vae_flux._res(x, enc, "mid.block_2")
        x = vae_flux._conv(F.silu(vae_flux._gn(x, enc, "norm_out")), enc, "conv_out")
        x = F.conv2d(x, sd_ram["quant_conv.weight"].to(device), sd_ram["quant_conv.bias"].to(device))
        return x[:, :x.shape[1] // 2] * SKALA[art]
