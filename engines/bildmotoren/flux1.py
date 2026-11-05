"""
flux1.py - FLUX.1 (Black Forest Labs): dev, schnell und Abwandlungen (z. B. FLUX.1 Krea dev).

    Prompt -> T5-XXL (Kontext, auf 512/256 Token aufgefüllt) + CLIP-L (ein gepoolter Vektor)
           -> 19 Doppel- und 38 Einzel-Blöcke mit eigener Modulation je Block -> Flux-VAE -> PNG

dev (mit Guidance-Einbettung): 28 Schritte, Guidance 3.5, Zeitverschiebung nach Bildgröße.
schnell (ohne): 4 Schritte, ohne Verschiebung. Original-/ComfyUI-Namen (auch GGUF) und diffusers-Namen.
"""

from __future__ import annotations

from pathlib import Path

from . import flux2 as _f2
from .gemeinsam import Bloecke, aufraeumen, aus_ram, euler, mu_linear, sigmas_flux, sigmas_linear
from .gewichte import Quelle, namen, ohne_praefix, zu

TITEL = "FLUX.1"
SYMBOL = "🟪"
STANDARD_DEV = {"schritte": 28, "groesse": (1024, 1024), "guidance": 3.5, "t5_laenge": 512}
STANDARD_SCHNELL = {"schritte": 4, "groesse": (1024, 1024), "t5_laenge": 256}
SCHRITTE = (1, 60)
ROPE_ACHSEN = (16, 56, 56)
ROPE_THETA = 10000.0

BEKANNT = {
    # comfyanonymous/flux_text_encoders
    "clip_l.safetensors": (246144152, "660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd"),
    "t5xxl_fp16.safetensors": (9787841024, "6e480b09fae049a72d2a8c5fbccb8d3e92febeb233bbe9dfe7256958a9167635"),
    "t5xxl_fp8_e4m3fn.safetensors": (4893934904, "7d330da4816157540d6bb7838bf63a0f02f573fc48ca4d8de34bb0cbfd514f09"),
    "t5xxl_fp8_e4m3fn_scaled.safetensors": (5157348688, "a498f0485dc9536735258018417c3fd7758dc3bccc0a645feaa472b34955557a"),
    # black-forest-labs · Comfy-Org (nur das Diffusionsmodell)
    "flux1-schnell.safetensors": (23782506688, "9403429e0052277ac2a87ad800adece5481eecefd9ed334e1f348723621d2a0a"),
    "flux1-dev.safetensors": (23802932552, "4610115bb0c89560703c892c59ac2742fa821e60ef5871b33493ba544683abd7"),
    # city96/FLUX.1-schnell-gguf · city96/FLUX.1-dev-gguf
    "flux1-schnell-F16.gguf": (23782411168, "e13384dbdeff6dd236cdaad13345e138364f22d090fb93280865bd034c7df12b"),
    "flux1-schnell-Q6_K.gguf": (9834955808, "a42fd143cec4d7194da281dc8d23a8fe54b16875a13423c042cb545d1da6fa50"),
    "flux1-schnell-Q8_0.gguf": (12687821728, "f6694941193b10148dbf1f0f498d4ccd3e9875c127fc53946213b68580c66f10"),
    "flux1-dev-F16.gguf": (23802870944, "c0a28d5ed1d9b57d06778ebb4b16f0f12c5607ee02498411ba2cccd270cbb70a"),
    "flux1-dev-Q6_K.gguf": (9857000736, "9566d56031d7f8de184bb5a0393073956ec4e28b32db3f860bd2b87edca04d13"),
    "flux1-dev-Q8_0.gguf": (12708281504, "129032f32224bf7138f16e18673d8008ba5f84c1ec74063bf4511a8bb4cf553d"),
    # city96/t5-v1_1-xxl-encoder-gguf
    "t5-v1_1-xxl-encoder-Q4_K_M.gguf": (2896123072, "6be2b0b7e2de7cf2919340c88cb802a103a997ce46c53131cec91958c1db1af4"),
    "t5-v1_1-xxl-encoder-Q5_K_M.gguf": (3386856640, "b51cbb10b1a7aac6dd1c3b62f0ed908bfd06e0b42d2f3577d43e061361f51dae"),
    "t5-v1_1-xxl-encoder-Q6_K.gguf": (3908261056, "d80116f6fc39801e4eef425a584e7a7a41cbe5119797bef2dad67299909fe2ae"),
    "t5-v1_1-xxl-encoder-Q8_0.gguf": (5061584064, "9ec60f6028534b7fe5af439fcb535d75a68592a9ca3fcdeb175ef89e3ee99825"),
    "t5-v1_1-xxl-encoder-f16.gguf": (9526060224, "1dc600961d3c5ed081f6700485cdc7ed9cfb4631f2dc385b7ac6bd3c80846d0d"),
}


# ===========================================================================
#  Erkennen, Zubehör
# ===========================================================================

def erkennen(alle: dict) -> bool:
    if "distilled_guidance_layer.in_proj.weight" in alle:
        return False                                    # Chroma
    return ("double_blocks.0.img_mod.lin.weight" in alle
            or ("transformer_blocks.0.norm1.linear.weight" in alle
                and "single_transformer_blocks.0.proj_mlp.weight" in alle))


def mit_guidance(dit: Path) -> bool:
    alle, _ = ohne_praefix(namen(dit))
    return "guidance_in.in_layer.weight" in alle or "time_text_embed.guidance_embedder.linear_1.weight" in alle


def vorgaben(dit: Path) -> dict:
    return dict(STANDARD_DEV if mit_guidance(dit) else STANDARD_SCHNELL)


def text_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .textenc import t5_form
    f = t5_form(pfad)
    return bool(f) and f["hidden"] == 4096


def clip_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .textenc import ist_clip_l
    return ist_clip_l(pfad)


def vae_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .vae_flux import ist_flux_vae
    return ist_flux_vae(pfad)


TOKENIZER = {"tokenizer_t5": "tokenizer.json", "tokenizer_clip": "vocab.json + merges.txt"}
ZUBEHOER = {
    "text": "T5-XXL als Text-Encoder (t5xxl_fp8_e4m3fn_scaled.safetensors oder t5-v1_1-xxl-encoder-Q8_0.gguf)",
    "clip": "CLIP-L (clip_l.safetensors)",
    "vae": "Flux-VAE (ae.safetensors)",
}


# ===========================================================================
#  Gewichte
# ===========================================================================

def _lin(q: Quelle, dtype, *namen_):
    return _f2._lin(q, dtype, *namen_)


def _norm(q: Quelle, *namen_):
    return _f2._norm(q, *namen_)


def _doppel(q: Quelle, i: int, dtype) -> dict:
    o, d = f"double_blocks.{i}.", f"transformer_blocks.{i}."
    B = {}
    if q.hat(o + "img_mod.lin.weight") or q.hat(d + "norm1.linear.weight"):    # Chroma: Modulation von außen
        B["img_mod"] = _lin(q, dtype, o + "img_mod.lin", d + "norm1.linear")
        B["txt_mod"] = _lin(q, dtype, o + "txt_mod.lin", d + "norm1_context.linear")
    for seite, add, ff in (("img", "attn.norm_", "ff"), ("txt", "attn.norm_added_", "ff_context")):
        getrennt = tuple(d + (f"attn.to_{x}" if seite == "img" else f"attn.add_{x}_proj") for x in "qkv")
        S = _f2._qkv(q, dtype, o + f"{seite}_attn.qkv", getrennt)
        S["qn"] = _norm(q, o + f"{seite}_attn.norm.query_norm.scale", d + add + "q.weight")
        S["kn"] = _norm(q, o + f"{seite}_attn.norm.key_norm.scale", d + add + "k.weight")
        S["proj"] = _lin(q, dtype, o + f"{seite}_attn.proj", d + ("attn.to_out.0" if seite == "img" else "attn.to_add_out"))
        S["mlp_in"] = _lin(q, dtype, o + f"{seite}_mlp.0", d + f"{ff}.net.0.proj")
        S["mlp_out"] = _lin(q, dtype, o + f"{seite}_mlp.2", d + f"{ff}.net.2")
        B[seite] = S
    return B


def _einzel(q: Quelle, i: int, dtype) -> dict:
    o, d = f"single_blocks.{i}.", f"single_transformer_blocks.{i}."
    B = {"lin2": _lin(q, dtype, o + "linear2", d + "proj_out"),
         "qn": _norm(q, o + "norm.query_norm.scale", d + "attn.norm_q.weight"),
         "kn": _norm(q, o + "norm.key_norm.scale", d + "attn.norm_k.weight")}
    if q.hat(o + "modulation.lin.weight") or q.hat(d + "norm.linear.weight"):
        B["mod"] = _lin(q, dtype, o + "modulation.lin", d + "norm.linear")
    if q.hat(o + "linear1.weight"):
        B["lin1"] = q.lin(o + "linear1", dtype)
    else:                                                # diffusers: q, k, v und MLP getrennt
        B.update({"q": q.lin(d + "attn.to_q", dtype), "k": q.lin(d + "attn.to_k", dtype),
                  "v": q.lin(d + "attn.to_v", dtype), "mlp": q.lin(d + "proj_mlp", dtype)})
    return B


def laden(pfad: Path, dtype) -> dict:
    alle, vors = ohne_praefix(namen(pfad))
    zahl = lambda p: 1 + max(int(k.split(".")[1]) for k in alle if k.startswith(p))    # noqa: E731
    diff = "x_embedder.weight" in alle
    n_d = zahl("transformer_blocks." if diff else "double_blocks.")
    n_e = zahl("single_transformer_blocks." if diff else "single_blocks.")
    with Quelle(pfad, vors) as q:
        tt = "time_text_embed."
        klein = {
            "img_in": _lin(q, dtype, "img_in", "x_embedder"), "txt_in": _lin(q, dtype, "txt_in", "context_embedder"),
            "t1": _lin(q, dtype, "time_in.in_layer", tt + "timestep_embedder.linear_1"),
            "t2": _lin(q, dtype, "time_in.out_layer", tt + "timestep_embedder.linear_2"),
            "v1": _lin(q, dtype, "vector_in.in_layer", tt + "text_embedder.linear_1"),
            "v2": _lin(q, dtype, "vector_in.out_layer", tt + "text_embedder.linear_2"),
            "fin_mod": _lin(q, dtype, "final_layer.adaLN_modulation.1", "norm_out.linear"),
            "fin": _lin(q, dtype, "final_layer.linear", "proj_out"),
            "fin_tausch": not q.hat("final_layer.adaLN_modulation.1.weight"),
        }
        if q.hat("guidance_in.in_layer.weight") or q.hat(tt + "guidance_embedder.linear_1.weight"):
            klein["g1"] = _lin(q, dtype, "guidance_in.in_layer", tt + "guidance_embedder.linear_1")
            klein["g2"] = _lin(q, dtype, "guidance_in.out_layer", tt + "guidance_embedder.linear_2")
        bloecke = [("d", _doppel(q, i, dtype)) for i in range(n_d)] + [("e", _einzel(q, i, dtype)) for i in range(n_e)]
    return {"klein": klein, "bloecke": bloecke}


# ===========================================================================
#  Rechnen
# ===========================================================================

def _gelu(x):
    import torch.nn.functional as F
    return F.gelu(x, approximate="tanh")


def doppel_block(B, img, txt, s, cs, dtype, D, mods=None, maske=None):
    """`mods`: (mi, mt) je sechs Vektoren von außen (Chroma), sonst aus dem Block; `maske`: gültige Schlüssel."""
    import torch
    if mods is None:
        mi = B["img_mod"](s).float()[:, None].chunk(6, -1)
        mt = B["txt_mod"](s).float()[:, None].chunk(6, -1)
    else:
        mi, mt = mods
    n_txt = txt.shape[1]
    hi = ((1 + mi[1]) * _f2._ln(img) + mi[0]).to(dtype)
    ht = ((1 + mt[1]) * _f2._ln(txt) + mt[0]).to(dtype)
    qi, ki, vi = _f2._qkv_rechnen(B["img"], hi, D, dtype)
    qt, kt, vt = _f2._qkv_rechnen(B["txt"], ht, D, dtype)
    a = _f2._attention(torch.cat([qt, qi], 1), torch.cat([kt, ki], 1), torch.cat([vt, vi], 1), cs, maske)
    img = img + mi[2] * B["img"]["proj"](a[:, n_txt:]).float()
    txt = txt + mt[2] * B["txt"]["proj"](a[:, :n_txt]).float()
    h = ((1 + mi[4]) * _f2._ln(img) + mi[3]).to(dtype)
    img = img + mi[5] * B["img"]["mlp_out"](_gelu(B["img"]["mlp_in"](h))).float()
    h = ((1 + mt[4]) * _f2._ln(txt) + mt[3]).to(dtype)
    txt = txt + mt[5] * B["txt"]["mlp_out"](_gelu(B["txt"]["mlp_in"](h))).float()
    return img, txt


def einzel_block(B, x, s, cs, dtype, D, mods=None, maske=None):
    import torch
    m = B["mod"](s).float()[:, None].chunk(3, -1) if mods is None else mods
    dim = x.shape[-1]
    h = ((1 + m[1]) * _f2._ln(x) + m[0]).to(dtype)
    if "lin1" in B:
        y = B["lin1"](h)
        qkv, mlp = y[..., :3 * dim], y[..., 3 * dim:]
        q, k, v = (t.unflatten(-1, (-1, D)) for t in qkv.chunk(3, -1))
    else:
        q, k, v = (B[c](h).unflatten(-1, (-1, D)) for c in "qkv")
        mlp = B["mlp"](h)
    a = _f2._attention(_f2._rms(q, B["qn"], dtype), _f2._rms(k, B["kn"], dtype), v, cs, maske)
    return x + m[2] * B["lin2"](torch.cat([a, _gelu(mlp)], -1)).float()


class Netz:
    def __init__(self, ram: dict, device, dtype, platz: int | None = None):
        self.device, self.dtype = device, dtype
        self.k = zu(ram["klein"], device)
        self.bloecke = Bloecke(ram["bloecke"], device, platz)
        self.D = int(ram["bloecke"][0][1]["img"]["qn"].shape[0])

    def kontext(self, t5):
        import torch
        with torch.no_grad():
            return self.k["txt_in"](t5.to(self.device, self.dtype)).float()[None]

    def __call__(self, x, sigma: float, txt, gepoolt, h: int, w: int, guidance: float | None = None):
        """x (1, h·w, 64) float32 -> Geschwindigkeit (1, h·w, 64)."""
        import torch
        import torch.nn.functional as F
        dt, k = self.dtype, self.k
        t = torch.full((1,), sigma * 1000.0, device=self.device)
        vec = k["t2"](F.silu(k["t1"](_f2._zeit(t).to(dt))))
        if "g1" in k:
            g = torch.full((1,), (3.5 if guidance is None else guidance) * 1000.0, device=self.device)
            vec = vec + k["g2"](F.silu(k["g1"](_f2._zeit(g).to(dt))))
        vec = vec + k["v2"](F.silu(k["v1"](gepoolt.to(self.device, dt)[None])))
        s = F.silu(vec)
        n_txt = txt.shape[1]
        pos = torch.zeros(n_txt + h * w, 3, dtype=torch.long)
        pos[n_txt:, 1] = torch.arange(h).repeat_interleave(w)
        pos[n_txt:, 2] = torch.arange(w).repeat(h)
        cs = _f2.rope(pos, self.device, ROPE_ACHSEN, ROPE_THETA)
        img = k["img_in"](x.to(dt)).float()
        u = None
        for art, B in self.bloecke:
            if art == "d":
                img, txt = doppel_block(B, img, txt, s, cs, dt, self.D)
            else:
                if u is None:
                    u = torch.cat([txt, img], 1)
                u = einzel_block(B, u, s, cs, dt, self.D)
        img = (u if u is not None else torch.cat([txt, img], 1))[:, n_txt:]
        a, b = k["fin_mod"](s).float()[:, None].chunk(2, -1)
        shift, scale = (b, a) if k["fin_tausch"] else (a, b)
        return k["fin"](((1 + scale) * _f2._ln(img) + shift).to(dt)).float()


# ===========================================================================
#  Ein Bild
# ===========================================================================

def text_zustand(prompt: str, teile: dict, laenge: int, device, dtype, on_status=None):
    """Prompt -> (T5-Kontext (laenge, 4096), CLIP-Vektor (768,))."""
    from .textenc import clip_gepoolt, clip_laden, clip_tokenizer, t5_laden, t5_tokenizer, t5_zustand
    if on_status:
        on_status(f"{TITEL}: Text-Encoder …")
    gew = aus_ram("text", teile["text"], lambda: t5_laden(teile["text"], dtype))
    ids, _ = t5_tokenizer(teile["tokenizer_t5"], laenge)(prompt)
    t5 = t5_zustand(gew, ids, device, dtype).detach().clone()
    gew = None
    clip = aus_ram("clip", teile["clip"], lambda: clip_laden(teile["clip"], dtype))
    gepoolt = clip_gepoolt(clip, clip_tokenizer(teile["tokenizer_clip"])(prompt), device, dtype).detach().clone()
    aufraeumen()
    return t5, gepoolt


def malen(prompt: str, teile: dict, *, schritte: int, breite: int, hoehe: int, seed: int,
          device, dtype, on_status=None, guidance: float | None = None):
    import torch
    from . import vae_flux
    v = vorgaben(teile["dit"])
    t5, gepoolt = text_zustand(prompt, teile, v["t5_laenge"], device, dtype, on_status)
    if on_status:
        on_status(f"{TITEL}: Modell in den Grafikspeicher …")
    ram = aus_ram("dit", teile["dit"], lambda: laden(teile["dit"], dtype))
    netz = Netz(ram, device, dtype)
    txt = netz.kontext(t5)
    h, w = hoehe // 16, breite // 16
    lat = torch.randn((1, 16, 2 * h, 2 * w), generator=torch.Generator().manual_seed(seed), dtype=torch.float32)
    x = lat.view(1, 16, h, 2, w, 2).permute(0, 2, 4, 1, 3, 5).reshape(1, h * w, 64).to(device)
    if "guidance" in v:
        sig = sigmas_flux(schritte, mu_linear(h * w))
    else:
        sig = sigmas_linear(schritte, 1.0)
    g = v.get("guidance") if guidance is None else guidance
    x = euler(lambda x, s: netz(x, s, txt, gepoolt, h, w, g), x, sig, on_status, TITEL)
    netz = txt = t5 = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: VAE …")
    vae = aus_ram("vae", teile["vae"], lambda: vae_flux.laden(teile["vae"]))
    z = x.view(1, h, w, 16, 2, 2).permute(0, 3, 1, 4, 2, 5).reshape(1, 16, 2 * h, 2 * w)
    bild = vae_flux.bild(vae, z, device, dtype)
    aufraeumen()
    return bild
