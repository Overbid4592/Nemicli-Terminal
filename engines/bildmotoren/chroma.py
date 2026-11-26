"""
chroma.py - Chroma (lodestones, z. B. Chroma1-HD): FLUX.1-Blöcke ohne eigene Modulation und ohne CLIP.

    Prompt -> T5-XXL (mit Maske) -> Approximator (alle Modulationen aus Zeit und Index) -> DiT -> Flux-VAE

Die Text-Token hinter dem ersten Auffüll-Token sind im DiT als Schlüssel ausgeblendet. 35 Schritte mit CFG 5
gegen einen leeren Prompt, feste Zeitverschiebung 3.0. Original- (auch GGUF) und diffusers-Namen.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import flux1 as _f1
from . import flux2 as _f2
from .gemeinsam import Bloecke, aufraeumen, aus_ram, euler, sigmas_linear
from .gewichte import Quelle, namen, ohne_praefix, zu

TITEL = "Chroma"
SYMBOL = "🟥"
STANDARD = {"schritte": 35, "groesse": (1024, 1024), "cfg": 5.0, "shift": 3.0}
SCHRITTE = (4, 60)
T5_LAENGE = 512
ZEIT_KANAELE = 16                       # Zeit und Guidance je 16, der Index 32: zusammen 64 Eingänge

BEKANNT = {
    # lodestones/Chroma1-HD · silveroxides/Chroma1-HD-GGUF
    "Chroma1-HD.safetensors": (17800038288, "d446d9695d08276f61e53653e025289dd96f7a489c27982a1fa54ecabc06642a"),
    "Chroma1-HD-Q4_K_M.gguf": (5566533792, "086f561e371d442b82b2c3689149da75046754c62a35293932cda397fc2bf9d2"),
    "Chroma1-HD-Q5_K_M.gguf": (6650777760, "1fed865d923ae85e01f91bede250dd7040408168e5ea4f63db6f0e0195079351"),
    "Chroma1-HD-Q6_K.gguf": (7650971808, "53d3619d77acfa8ee78c700cca1eb45a6c856e6d9563301140a3a04957657379"),
    "Chroma1-HD-Q8_0.gguf": (9735409824, "8c8d66177bfc584573dd21584399ebc1f00bde60acbbc48ac3cc8f58f10c6094"),
}

text_passt = _f1.text_passt
vae_passt = _f1.vae_passt
TOKENIZER = {"tokenizer_t5": "tokenizer.json"}
ZUBEHOER = {"text": _f1.ZUBEHOER["text"], "vae": _f1.ZUBEHOER["vae"]}


def erkennen(alle: dict) -> bool:
    return "distilled_guidance_layer.in_proj.weight" in alle


def vorgaben(dit: Path) -> dict:
    return dict(STANDARD)


# ===========================================================================
#  Gewichte
# ===========================================================================

def laden(pfad: Path, dtype) -> dict:
    alle, vors = ohne_praefix(namen(pfad))
    zahl = lambda p: 1 + max(int(k.split(".")[1]) for k in alle if k.startswith(p))    # noqa: E731
    diff = "x_embedder.weight" in alle
    n_d = zahl("transformer_blocks." if diff else "double_blocks.")
    n_e = zahl("single_transformer_blocks." if diff else "single_blocks.")
    g = "distilled_guidance_layer."
    n_a = 1 + max(int(k.split(".")[2]) for k in alle if k.startswith(g + "layers."))
    with Quelle(pfad, vors) as q:
        appr = {"in": q.lin(g + "in_proj", dtype), "out": q.lin(g + "out_proj", dtype), "schichten": [
            {"norm": _f2._norm(q, f"{g}norms.{i}.scale", f"{g}norms.{i}.weight"),
             "l1": _f2._lin(q, dtype, f"{g}layers.{i}.in_layer", f"{g}layers.{i}.linear_1"),
             "l2": _f2._lin(q, dtype, f"{g}layers.{i}.out_layer", f"{g}layers.{i}.linear_2")} for i in range(n_a)]}
        klein = {"img_in": _f2._lin(q, dtype, "img_in", "x_embedder"),
                 "txt_in": _f2._lin(q, dtype, "txt_in", "context_embedder"),
                 "fin": _f2._lin(q, dtype, "final_layer.linear", "proj_out"), "appr": appr}
        bloecke = ([("d", _f1._doppel(q, i, dtype)) for i in range(n_d)]
                   + [("e", _f1._einzel(q, i, dtype)) for i in range(n_e)])
    return {"klein": klein, "bloecke": bloecke}


# ===========================================================================
#  Rechnen
# ===========================================================================

def _sinus(t, dim):
    """Sinus-Einbettung wie diffusers Timesteps (cos zuerst)."""
    import torch
    half = dim // 2
    f = torch.exp(-math.log(10000.0) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
    a = t[:, None].float() * f[None]
    return torch.cat([torch.cos(a), torch.sin(a)], -1)


def modulationen(appr: dict, sigma: float, n: int, device, dtype):
    """(n, dim): ein Modulationsvektor je Index – aus Zeit, Guidance 0 und dem Index selbst."""
    import torch
    import torch.nn.functional as F
    t = _sinus(torch.full((1,), sigma * 1000.0, device=device), ZEIT_KANAELE)
    g = _sinus(torch.zeros(1, device=device), ZEIT_KANAELE)
    idx = _sinus(torch.arange(n, device=device) * 1000.0, 2 * ZEIT_KANAELE)
    x = torch.cat([torch.cat([t, g], -1).expand(n, -1), idx], -1)
    x = appr["in"](x.to(dtype)).float()
    for L in appr["schichten"]:
        h = F.rms_norm(x, (x.shape[-1],), weight=L["norm"].to(device), eps=None).to(dtype)
        x = x + L["l2"](F.silu(L["l1"](h))).float()
    return appr["out"](x.to(dtype)).float()


class Netz:
    def __init__(self, ram: dict, device, dtype, platz: int | None = None):
        self.device, self.dtype = device, dtype
        self.k = zu(ram["klein"], device)
        self.bloecke = Bloecke(ram["bloecke"], device, platz)
        self.n_d = sum(1 for a, _ in ram["bloecke"] if a == "d")
        self.n_e = len(ram["bloecke"]) - self.n_d
        self.D = int(ram["bloecke"][0][1]["img"]["qn"].shape[0])

    def kontext(self, t5):
        import torch
        with torch.no_grad():
            return self.k["txt_in"](t5.to(self.device, self.dtype)).float()[None]

    def __call__(self, x, sigma: float, txt, gueltig: int, h: int, w: int):
        """x (1, h·w, 64) float32; `gueltig`: so viele Text-Token gelten als Schlüssel -> Geschwindigkeit."""
        import torch
        dt, k = self.dtype, self.k
        n_mod = 3 * self.n_e + 12 * self.n_d + 2
        mods = modulationen(k["appr"], sigma, n_mod, self.device, dt)[:, None, None, :]       # (n, 1, 1, dim)
        n_txt = txt.shape[1]
        pos = torch.zeros(n_txt + h * w, 3, dtype=torch.long)
        pos[n_txt:, 1] = torch.arange(h).repeat_interleave(w)
        pos[n_txt:, 2] = torch.arange(w).repeat(h)
        cs = _f2.rope(pos, self.device, _f1.ROPE_ACHSEN, _f1.ROPE_THETA)
        maske = torch.ones(n_txt + h * w, dtype=torch.bool, device=self.device)
        maske[gueltig:n_txt] = False
        img = k["img_in"](x.to(dt)).float()
        u, i_d, i_e = None, 0, 0
        bild_ab, txt_ab = 3 * self.n_e, 3 * self.n_e + 6 * self.n_d
        for art, B in self.bloecke:
            if art == "d":
                mi = tuple(mods[bild_ab + 6 * i_d + j][0] for j in range(6))
                mt = tuple(mods[txt_ab + 6 * i_d + j][0] for j in range(6))
                img, txt = _f1.doppel_block(B, img, txt, None, cs, dt, self.D, (mi, mt), maske)
                i_d += 1
            else:
                if u is None:
                    u = torch.cat([txt, img], 1)
                m = tuple(mods[3 * i_e + j][0] for j in range(3))
                u = _f1.einzel_block(B, u, None, cs, dt, self.D, m, maske)
                i_e += 1
        img = (u if u is not None else torch.cat([txt, img], 1))[:, n_txt:]
        shift, scale = mods[-2][0], mods[-1][0]
        return k["fin"](((1 + scale) * _f2._ln(img) + shift).to(dt)).float()


# ===========================================================================
#  Ein Bild
# ===========================================================================

def text_zustand(prompt: str, teile: dict, device, dtype, on_status=None):
    """Prompt -> (T5-Zustand (512, 4096), Zahl der gültigen Text-Token = Text + erstes Auffüll-Token)."""
    from .textenc import t5_laden, t5_tokenizer, t5_zustand
    if on_status:
        on_status(f"{TITEL}: Text-Encoder …")
    gew = aus_ram("text", teile["text"], lambda: t5_laden(teile["text"], dtype))
    ids, echt = t5_tokenizer(teile["tokenizer_t5"], T5_LAENGE)(prompt)
    z = t5_zustand(gew, ids, device, dtype, echt=echt).detach().clone()
    aufraeumen()
    return z, min(echt + 1, T5_LAENGE)


def malen(prompt: str, teile: dict, *, schritte: int, breite: int, hoehe: int, seed: int,
          device, dtype, on_status=None, cfg: float | None = None):
    import torch
    from . import vae_flux
    cfg = STANDARD["cfg"] if cfg is None else cfg
    t5, gueltig = text_zustand(prompt, teile, device, dtype, on_status)
    neg = text_zustand("", teile, device, dtype) if cfg > 1.0 else None
    if on_status:
        on_status(f"{TITEL}: Modell in den Grafikspeicher …")
    ram = aus_ram("dit", teile["dit"], lambda: laden(teile["dit"], dtype))
    netz = Netz(ram, device, dtype)
    txt = netz.kontext(t5)
    txt_neg = (netz.kontext(neg[0]), neg[1]) if neg is not None else None
    h, w = hoehe // 16, breite // 16
    lat = torch.randn((1, 16, 2 * h, 2 * w), generator=torch.Generator().manual_seed(seed), dtype=torch.float32)
    x = lat.view(1, 16, h, 2, w, 2).permute(0, 2, 4, 1, 3, 5).reshape(1, h * w, 64).to(device)

    def geschwindigkeit(x, s):
        v = netz(x, s, txt, gueltig, h, w)
        if txt_neg is None:
            return v
        v_neg = netz(x, s, txt_neg[0], txt_neg[1], h, w)
        return v_neg + cfg * (v - v_neg)

    x = euler(geschwindigkeit, x, sigmas_linear(schritte, STANDARD["shift"]), on_status, TITEL)
    netz = txt = txt_neg = t5 = neg = None
    aufraeumen()
    if on_status:
        on_status(f"{TITEL}: VAE …")
    vae = aus_ram("vae", teile["vae"], lambda: vae_flux.laden(teile["vae"]))
    z = x.view(1, h, w, 16, 2, 2).permute(0, 3, 1, 4, 2, 5).reshape(1, 16, 2 * h, 2 * w)
    bild = vae_flux.bild(vae, z, device, dtype)
    aufraeumen()
    return bild
