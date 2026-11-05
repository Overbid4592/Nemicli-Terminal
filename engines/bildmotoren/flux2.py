"""
flux2.py - FLUX.2 (Black Forest Labs), z. B. FLUX.2 klein 4B/9B: Doppel- und Einzel-Blöcke mit gemeinsamer
Modulation, 4-Achsen-RoPE, FLUX.2-VAE mit 32 Latent-Kanälen.

    Prompt -> Qwen3 (Zustände nach Schicht 9, 18, 27, auf 512 Token aufgefüllt) -> DiT -> VAE -> PNG

klein (destilliert): 4 Schritte ohne CFG. „base“-Fassungen: 50 Schritte mit CFG 4 und leerem Gegen-Prompt.
Zeitverschiebung nach Bildgröße und Schrittzahl (empirisch wie diffusers). Liest Original-/ComfyUI-Namen
(auch GGUF) und diffusers-Namen.
"""

from __future__ import annotations

import math
from pathlib import Path

from .gemeinsam import Bloecke, aufraeumen, aus_ram, euler, sigmas_flux
from .gewichte import Quelle, namen, ohne_praefix, zu

TITEL = "FLUX.2"
SYMBOL = "🟦"
STANDARD = {"schritte": 4, "groesse": (1024, 1024)}
STANDARD_BASE = {"schritte": 50, "cfg": 4.0}
SCHRITTE = (2, 60)
TEXT_ABGRIFFE = (9, 18, 27)
TEXT_LAENGE = 512
PAD_TOKEN = "<|endoftext|>"
VORLAGE = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
ROPE_THETA = 2000.0
ROPE_ACHSEN = (32, 32, 32, 32)
EPS = 1e-6
BN_EPS = 1e-4

BEKANNT = {
    # Comfy-Org/Flux-2-Klein-4B · black-forest-labs
    "flux-2-klein-4b.safetensors": (7751105712, "ec3d4e733a771f61c052fb4856c48b336c55eaf2c65487c2a1faeb9bbda7a343"),
    "flux-2-klein-base-4b.safetensors": (7751105712, "9c5fed22b76baea749d88fc2abe3ad53245e7b21a0d353a762665eea00043b92"),
    "flux-2-klein-4b-fp8.safetensors": (4070624520, "97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6"),
    "flux2-vae.safetensors": (336211292, "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3"),
    "qwen_3_4b_fp4_flux2.safetensors": (3848213998, "3eab03a77adb0ee5304a4e677d5c10ac22f9049c1d7c894adca4f8bb39206ca8"),
    # Comfy-Org/Flux-2-Klein-9B (Text-Encoder)
    "qwen_3_8b.safetensors": (16381517176, "f0ff9239d56269ca1d05e5f86da6a79fac111af464955681f11c7ab0ec5ef6c1"),
    "qwen_3_8b_fp8mixed.safetensors": (8664848742, "abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6"),
    "qwen_3_8b_fp4mixed.safetensors": (6802593327, "bbf16f981d98e16d080c566134814c4e9f6aadd0d0e1383c60bc44ba939d760d"),
    # unsloth/FLUX.2-klein-4B-GGUF
    "flux-2-klein-4b-BF16.gguf": (7751115328, "45e92cdad39270a6b4c6055daad75f5817c5ec81a93a7622082342b45e26a53c"),
    "flux-2-klein-4b-F16.gguf": (7751115328, "c814dc08a07e3a6203547183eae8299835d32dc002fe0c24e1d02b4f2af649da"),
    "flux-2-klein-4b-Q2_K.gguf": (1827807808, "681423838cec5788fb2d6227a2d79a254f72cebba75806811fb408181970eaef"),
    "flux-2-klein-4b-Q3_K_M.gguf": (2124489280, "0f27104f7b5842b7bfd5909645c60208cbb6b8d43f5d65f58b085f45ed18c9e1"),
    "flux-2-klein-4b-Q4_K_M.gguf": (2604311104, "0b25d143c8469b342bc5af3bce92b783bf6b0636d285f7b2f75e38af63af9a15"),
    "flux-2-klein-4b-Q4_K_S.gguf": (2583077440, "bce1a0e68882b290a7b50d962936b7aff38fe3f61e598f0dca6ba36a90f3d578"),
    "flux-2-klein-4b-Q5_K_M.gguf": (3073368640, "58c01c75fee2272eadb127f53e26dd05a5a8e7f812e37c36fa44603301f91e54"),
    "flux-2-klein-4b-Q5_K_S.gguf": (3050512960, "8fe0746f3e92d80688ef8560f04196994b82464db0981992f40706f94c9b52c0"),
    "flux-2-klein-4b-Q6_K.gguf": (3409125952, "ef08c2867754b9c628d4440871739895a7dc7d57a66d4ed5b708246aca657c7f"),
    "flux-2-klein-4b-Q8_0.gguf": (4300644928, "24a812e8f9b640e21c784164ea48571d8017ca22873696f4badeeabb006d509c"),
    # unsloth/FLUX.2-klein-9B-GGUF
    "flux-2-klein-9b-BF16.gguf": (18157197600, "12a86c9755cb1e14e7ebaff48fe56da9a912477cd5a200ae552512f5e957786e"),
    "flux-2-klein-9b-F16.gguf": (18157197600, "5a8886af48c74d39a693f8ae7a6d9bedafd24316c35d3c99e61124325aedbb98"),
    "flux-2-klein-9b-Q2_K.gguf": (3403602208, "1f0efdbac12ecb5f8d8651a10cf36932af36bcd4c809d7f93de011a1e013ab8d"),
    "flux-2-klein-9b-Q3_K_M.gguf": (4769503520, "26d7d982f88aaed665252c5ac6c8c06e0b6b68c66eab077ceb593b8575c86901"),
    "flux-2-klein-9b-Q4_K_M.gguf": (5673900320, "23cadc53168a1b9c607a79d2b8cf942deeb0b48dcdb6024bf757360c7a746c19"),
    "flux-2-klein-9b-Q4_K_S.gguf": (5833283872, "8faf39b910c9406f95b575061f79d88dd735aeffd24eb91ccda63d4bf9654d95"),
    "flux-2-klein-9b-Q5_K_M.gguf": (7018436896, "fb3a36c87637b75ff3ef5dd00ca68d0f7c3c52b3a70a6b29fff50a8dee7fefed"),
    "flux-2-klein-9b-Q5_K_S.gguf": (6938220832, "a3daca460b6f537a3e74d2db277396a5edbf1c6656fd007e1884fccc00a3a79f"),
    "flux-2-klein-9b-Q6_K.gguf": (7865162016, "6405d601eb37c4625d023ea11bdc93fba06842a1e78f66119e7b5983a0eb9545"),
    "flux-2-klein-9b-Q8_0.gguf": (10245691680, "68957129335ce59ddadcb771f9c375f0ceb587b9c03a104e427fe303d3aab4c6"),
    # unsloth/Qwen3-8B-GGUF (Text-Encoder für 9B)
    "Qwen3-8B-BF16.gguf": (16388044384, "5e416a2020fe63e76ea13c8979be35fc6070aaf3578f7876400c55c2f5c3eb30"),
    "Qwen3-8B-Q8_0.gguf": (8709519168, "0cfbf745760f07a76ddeb358dd025a27f2e11d1ca9c9a4169a373d52990fe86e"),
    "Qwen3-8B-Q6_K.gguf": (6725900096, "0eaec718fdeab0f429dfa0ec481c090388811f5e63785ddb582292f4ef3c3827"),
    "Qwen3-8B-Q5_K_M.gguf": (5851113280, "159c694b93271e4edc1dc2a305b10cf981032c8f3035a7da00973312f0331504"),
    "Qwen3-8B-Q4_K_M.gguf": (5027784512, "120307ba529eb2439d6c430d94104dabd578497bc7bfe7e322b5d9933b449bd4"),
}


# ===========================================================================
#  Erkennen, Zubehör
# ===========================================================================

def erkennen(alle: dict) -> bool:
    return ("double_stream_modulation_img.lin.weight" in alle
            or "double_stream_modulation_img.linear.weight" in alle)


def _kontext_breite(dit: Path) -> int | None:
    alle, _ = ohne_praefix(namen(dit))
    form = alle.get("txt_in.weight") or alle.get("context_embedder.weight")
    return int(form[1]) if form else None


def text_passt(pfad: Path, dit: Path | None = None) -> bool:
    """Qwen3, dessen drei Abgriffe zusammen die Kontextbreite des DiT ergeben (4B: 2560, 9B: 4096)."""
    from .textenc import qwen3_form
    f = qwen3_form(pfad)
    if not f or f["art"] != "qwen3" or f["schichten"] < max(TEXT_ABGRIFFE):
        return False
    breite = _kontext_breite(dit) if dit is not None else None
    return f["hidden"] * len(TEXT_ABGRIFFE) == breite if breite else f["hidden"] in (2560, 4096)


def vae_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .vae_flux import kanaele
    return kanaele(pfad) == 32


ZUBEHOER = {
    "text": "Qwen3 als Text-Encoder – klein 4B: qwen_3_4b.safetensors oder Qwen3-4B-Q8_0.gguf, "
            "klein 9B: qwen_3_8b.safetensors oder Qwen3-8B-Q8_0.gguf",
    "vae": "FLUX.2-VAE (flux2-vae.safetensors)",
}


def ist_base(dit: Path) -> bool:
    """„base“-Fassungen sind nicht destilliert: mehr Schritte und CFG."""
    return "base" in Path(dit).name.lower()


def vorgaben(dit: Path) -> dict:
    v = dict(STANDARD)
    if ist_base(dit):
        v.update(STANDARD_BASE)
    return v


# ===========================================================================
#  Gewichte
# ===========================================================================

def _lin(q: Quelle, dtype, *namen_):
    for n in namen_:
        if q.hat(n + ".weight"):
            return q.lin(n, dtype)
    raise KeyError(namen_[0])


def _norm(q: Quelle, *namen_):
    for n in namen_:
        if q.hat(n):
            return q.tensor(n).float()
    raise KeyError(namen_[0])


def _qkv(q: Quelle, dtype, fusioniert: str, getrennt: tuple):
    if q.hat(fusioniert + ".weight"):
        return {"qkv": q.lin(fusioniert, dtype)}
    return {"q": q.lin(getrennt[0], dtype), "k": q.lin(getrennt[1], dtype), "v": q.lin(getrennt[2], dtype)}


def _doppel(q: Quelle, i: int, dtype) -> dict:
    o, d = f"double_blocks.{i}.", f"transformer_blocks.{i}."
    B = {}
    for seite, attn, add in (("img", "attn.to_", "attn.norm_"), ("txt", "attn.add_", "attn.norm_added_")):
        getrennt = tuple(d + (f"attn.to_{x}" if seite == "img" else f"attn.add_{x}_proj") for x in "qkv")
        B[seite] = _qkv(q, dtype, o + f"{seite}_attn.qkv", getrennt)
        B[seite]["qn"] = _norm(q, o + f"{seite}_attn.norm.query_norm.scale", d + add + "q.weight")
        B[seite]["kn"] = _norm(q, o + f"{seite}_attn.norm.key_norm.scale", d + add + "k.weight")
        ff = "ff" if seite == "img" else "ff_context"
        B[seite]["proj"] = _lin(q, dtype, o + f"{seite}_attn.proj", d + ("attn.to_out.0" if seite == "img" else "attn.to_add_out"))
        B[seite]["mlp_in"] = _lin(q, dtype, o + f"{seite}_mlp.0", d + f"{ff}.linear_in")
        B[seite]["mlp_out"] = _lin(q, dtype, o + f"{seite}_mlp.2", d + f"{ff}.linear_out")
    return B


def _einzel(q: Quelle, i: int, dtype) -> dict:
    o, d = f"single_blocks.{i}.", f"single_transformer_blocks.{i}."
    return {"lin1": _lin(q, dtype, o + "linear1", d + "attn.to_qkv_mlp_proj"),
            "lin2": _lin(q, dtype, o + "linear2", d + "attn.to_out"),
            "qn": _norm(q, o + "norm.query_norm.scale", d + "attn.norm_q.weight"),
            "kn": _norm(q, o + "norm.key_norm.scale", d + "attn.norm_k.weight")}


def laden(pfad: Path, dtype) -> dict:
    alle, vors = ohne_praefix(namen(pfad))
    zahl = lambda p: 1 + max(int(k.split(".")[1]) for k in alle if k.startswith(p))    # noqa: E731
    diff = "x_embedder.weight" in alle
    n_d = zahl("transformer_blocks." if diff else "double_blocks.")
    n_e = zahl("single_transformer_blocks." if diff else "single_blocks.")
    with Quelle(pfad, vors) as q:
        klein = {
            "img_in": _lin(q, dtype, "img_in", "x_embedder"),
            "txt_in": _lin(q, dtype, "txt_in", "context_embedder"),
            "t1": _lin(q, dtype, "time_in.in_layer", "time_guidance_embed.timestep_embedder.linear_1"),
            "t2": _lin(q, dtype, "time_in.out_layer", "time_guidance_embed.timestep_embedder.linear_2"),
            "mod_img": _lin(q, dtype, "double_stream_modulation_img.lin", "double_stream_modulation_img.linear"),
            "mod_txt": _lin(q, dtype, "double_stream_modulation_txt.lin", "double_stream_modulation_txt.linear"),
            "mod_einzel": _lin(q, dtype, "single_stream_modulation.lin", "single_stream_modulation.linear"),
            "fin_mod": _lin(q, dtype, "final_layer.adaLN_modulation.1", "norm_out.linear"),
            "fin": _lin(q, dtype, "final_layer.linear", "proj_out"),
            "fin_tausch": not q.hat("final_layer.adaLN_modulation.1.weight"),   # diffusers: (scale, shift)
        }
        if q.hat("guidance_in.in_layer.weight") or q.hat("time_guidance_embed.guidance_embedder.linear_1.weight"):
            klein["g1"] = _lin(q, dtype, "guidance_in.in_layer", "time_guidance_embed.guidance_embedder.linear_1")
            klein["g2"] = _lin(q, dtype, "guidance_in.out_layer", "time_guidance_embed.guidance_embedder.linear_2")
        doppel = [_doppel(q, i, dtype) for i in range(n_d)]
        einzel = [_einzel(q, i, dtype) for i in range(n_e)]
    return {"klein": klein, "bloecke": [("d", b) for b in doppel] + [("e", b) for b in einzel]}


# ===========================================================================
#  Rechnen
# ===========================================================================

def _zeit(t, dim=256):
    import torch
    half = dim // 2
    f = torch.exp(-math.log(10000.0) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
    a = t[:, None].float() * f[None]
    return torch.cat([torch.cos(a), torch.sin(a)], -1)


def rope(pos, device, achsen=ROPE_ACHSEN, theta=ROPE_THETA):
    """pos (n, Achsen) -> cos, sin je (n, kopf/2); Paare (2i, 2i+1) werden gedreht."""
    import torch
    teile = []
    for i, d in enumerate(achsen):
        f = 1.0 / (theta ** (torch.arange(0, d, 2, dtype=torch.float64) / d))
        teile.append(torch.outer(pos[:, i].double(), f))
    w = torch.cat(teile, -1).float().to(device)
    return w.cos(), w.sin()


def _rope_an(x, cs):
    import torch
    cos, sin = cs
    xf = x.float().unflatten(-1, (-1, 2))
    a, b = xf[..., 0], xf[..., 1]
    return torch.stack((a * cos - b * sin, a * sin + b * cos), -1).flatten(-2).to(x.dtype)


def _ln(x):
    import torch.nn.functional as F
    return F.layer_norm(x.float(), (x.shape[-1],), eps=EPS)


def _rms(x, w, dtype):
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w, eps=EPS).to(dtype)


def _swiglu(x):
    import torch.nn.functional as F
    a, b = x.chunk(2, -1)
    return F.silu(a) * b


def _qkv_rechnen(S, h, D, dtype):
    n = h.shape[1]
    if "qkv" in S:
        q, k, v = S["qkv"](h).chunk(3, -1)
    else:
        q, k, v = S["q"](h), S["k"](h), S["v"](h)
    q, k, v = (t.view(1, n, -1, D) for t in (q, k, v))
    return _rms(q, S["qn"], dtype), _rms(k, S["kn"], dtype), v


def _attention(q, k, v, cs, maske=None):
    """q, k, v (1, n, H, D) -> (1, n, H*D). `maske` (n,) bool: welche Schlüssel gelten (None: alle)."""
    import torch.nn.functional as F
    q, k = _rope_an(q.transpose(1, 2), cs), _rope_an(k.transpose(1, 2), cs)
    a = F.scaled_dot_product_attention(q, k, v.transpose(1, 2),
                                       attn_mask=None if maske is None else maske.view(1, 1, 1, -1))
    return a.transpose(1, 2).flatten(2)


def doppel_block(B, img, txt, m_img, m_txt, cs, dtype, D):
    """img, txt (1, n, dim) float32; m_* je sechs Modulationsvektoren (shift, scale, gate)×2."""
    import torch
    n_txt = txt.shape[1]
    hi = ((1 + m_img[1]) * _ln(img) + m_img[0]).to(dtype)
    ht = ((1 + m_txt[1]) * _ln(txt) + m_txt[0]).to(dtype)
    qi, ki, vi = _qkv_rechnen(B["img"], hi, D, dtype)
    qt, kt, vt = _qkv_rechnen(B["txt"], ht, D, dtype)
    a = _attention(torch.cat([qt, qi], 1), torch.cat([kt, ki], 1), torch.cat([vt, vi], 1), cs)
    at, ai = a[:, :n_txt], a[:, n_txt:]
    img = img + m_img[2] * B["img"]["proj"](ai).float()
    h = ((1 + m_img[4]) * _ln(img) + m_img[3]).to(dtype)
    img = img + m_img[5] * B["img"]["mlp_out"](_swiglu(B["img"]["mlp_in"](h))).float()
    txt = txt + m_txt[2] * B["txt"]["proj"](at).float()
    h = ((1 + m_txt[4]) * _ln(txt) + m_txt[3]).to(dtype)
    txt = txt + m_txt[5] * B["txt"]["mlp_out"](_swiglu(B["txt"]["mlp_in"](h))).float()
    return img, txt


def einzel_block(B, x, m, cs, dtype, D):
    """x (1, n, dim) float32 (Text vorn, Bild hinten); m = (shift, scale, gate)."""
    import torch
    dim = x.shape[-1]
    h = ((1 + m[1]) * _ln(x) + m[0]).to(dtype)
    y = B["lin1"](h)
    qkv, mlp = y[..., :3 * dim], y[..., 3 * dim:]
    q, k, v = (t.unflatten(-1, (-1, D)) for t in qkv.chunk(3, -1))
    a = _attention(_rms(q, B["qn"], dtype), _rms(k, B["kn"], dtype), v, cs)
    return x + m[2] * B["lin2"](torch.cat([a, _swiglu(mlp)], -1)).float()


class Netz:
    """Das DiT für ein Bild. Text-Zustand (n_txt, breite) bleibt über alle Schritte gleich."""

    def __init__(self, ram: dict, ctx, device, dtype, platz: int | None = None):
        import torch
        self.device, self.dtype = device, dtype
        self.k = zu(ram["klein"], device)
        self.bloecke = Bloecke(ram["bloecke"], device, platz)
        self.D = int(ram["bloecke"][0][1]["img"]["qn"].shape[0])
        self.txt = self.kontext(ctx)
        n = ctx.shape[0]
        pos = torch.zeros(n, 4, dtype=torch.long)
        pos[:, 3] = torch.arange(n)
        self.txt_pos = pos

    def kontext(self, ctx):
        """Text-Zustand -> Eingang der Blöcke (1, n_txt, dim)."""
        import torch
        with torch.no_grad():
            return self.k["txt_in"](ctx.to(self.device, self.dtype)).float()[None]

    def __call__(self, x, sigma: float, guidance: float | None = None):
        """x (1, n_bild, 128) float32, Bild-Positionen in self.bild_pos -> Geschwindigkeit (1, n_bild, 128)."""
        import torch
        dt, k = self.dtype, self.k
        t = torch.full((1,), sigma * 1000.0, device=self.device)
        temb = k["t2"](torch.nn.functional.silu(k["t1"](_zeit(t).to(dt))))
        if "g1" in k and guidance is not None:
            g = torch.full((1,), guidance * 1000.0, device=self.device)
            temb = temb + k["g2"](torch.nn.functional.silu(k["g1"](_zeit(g).to(dt))))
        s = torch.nn.functional.silu(temb)
        m_img = k["mod_img"](s).float()[:, None].chunk(6, -1)
        m_txt = k["mod_txt"](s).float()[:, None].chunk(6, -1)
        m_e = k["mod_einzel"](s).float()[:, None].chunk(3, -1)

        img = k["img_in"](x.to(dt)).float()
        txt = self.txt
        cs = rope(torch.cat([self.txt_pos, self.bild_pos]), self.device)
        n_txt = txt.shape[1]
        u = None
        for art, B in self.bloecke:
            if art == "d":
                img, txt = doppel_block(B, img, txt, m_img, m_txt, cs, dt, self.D)
            else:
                if u is None:
                    u = torch.cat([txt, img], 1)
                u = einzel_block(B, u, m_e, cs, dt, self.D)
        img = (u if u is not None else torch.cat([txt, img], 1))[:, n_txt:]
        a, b = k["fin_mod"](s).float()[:, None].chunk(2, -1)
        shift, scale = (b, a) if k["fin_tausch"] else (a, b)
        return k["fin"](((1 + scale) * _ln(img) + shift).to(dt)).float()


# ===========================================================================
#  Ein Bild
# ===========================================================================

def text_zustand(prompt: str, teile: dict, device, dtype, on_status=None):
    """Prompt -> (512, 3 × hidden): drei Zwischenzustände nebeneinander, auf 512 Token aufgefüllt."""
    import torch
    from .textenc import qwen3_laden, qwen3_zustaende, tokenizer_fuer
    if on_status:
        on_status(f"{TITEL}: Text-Encoder …")
    pfad = teile["text"]
    bis = max(TEXT_ABGRIFFE)
    gew = aus_ram("text", (pfad, bis), lambda: qwen3_laden(pfad, bis, dtype))
    tok = tokenizer_fuer(pfad, teile["tokenizer"])
    ids = tok(VORLAGE.format(prompt))[:TEXT_LAENGE]
    echt = len(ids)
    ids += tok(PAD_TOKEN) * (TEXT_LAENGE - echt)
    z = qwen3_zustaende(gew, ids, device, dtype, TEXT_ABGRIFFE, echt=echt)
    h = torch.cat(z, -1).detach().clone()
    aufraeumen()
    return h


def empirisches_mu(n_bild: int, schritte: int) -> float:
    """Zeitverschiebung nach Bild-Token und Schrittzahl (Werte wie diffusers Flux2KleinPipeline)."""
    a1, b1 = 8.73809524e-05, 1.89833333
    a2, b2 = 0.00016927, 0.45666666
    if n_bild > 4300:
        return a2 * n_bild + b2
    m200, m10 = a2 * n_bild + b2, a1 * n_bild + b1
    a = (m200 - m10) / 190.0
    return a * schritte + (m200 - 200.0 * a)


def malen(prompt: str, teile: dict, *, schritte: int, breite: int, hoehe: int, seed: int,
          device, dtype, on_status=None, cfg: float | None = None):
    import torch
    from . import vae_flux
    ctx = text_zustand(prompt, teile, device, dtype, on_status)
    v = vorgaben(teile["dit"])
    cfg = v.get("cfg", 1.0) if cfg is None else cfg
    leer = text_zustand("", teile, device, dtype) if cfg > 1.0 else None

    if on_status:
        on_status(f"{TITEL}: Modell in den Grafikspeicher …")
    ram = aus_ram("dit", teile["dit"], lambda: laden(teile["dit"], dtype))
    netz = Netz(ram, ctx, device, dtype)
    gegen = netz.kontext(leer) if leer is not None else None
    h, w = hoehe // 16, breite // 16
    pos = torch.zeros(h * w, 4, dtype=torch.long)
    pos[:, 1] = torch.arange(h).repeat_interleave(w)
    pos[:, 2] = torch.arange(w).repeat(h)
    netz.bild_pos = pos
    lat = torch.randn((1, 128, h, w), generator=torch.Generator().manual_seed(seed), dtype=torch.float32)
    x = lat.reshape(1, 128, h * w).permute(0, 2, 1).to(device)
    sig = sigmas_flux(schritte, empirisches_mu(h * w, schritte))

    def geschwindigkeit(x, s):
        v_pos = netz(x, s)
        if gegen is None:
            return v_pos
        txt, netz.txt = netz.txt, gegen              # gleiche Blöcke, leerer Prompt
        try:
            v_neg = netz(x, s)
        finally:
            netz.txt = txt
        return v_neg + cfg * (v_pos - v_neg)

    x = euler(geschwindigkeit, x, sig, on_status, TITEL)
    netz = gegen = ctx = leer = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: VAE …")
    vae = aus_ram("vae", teile["vae"], lambda: vae_flux.laden(teile["vae"]))
    z = x.permute(0, 2, 1).reshape(1, 128, h, w)
    mittel = vae["bn.running_mean"].view(1, -1, 1, 1).to(z.device)
    std = torch.sqrt(vae["bn.running_var"].view(1, -1, 1, 1) + BN_EPS).to(z.device)
    z = z * std + mittel
    z = z.reshape(1, 32, 2, 2, h, w).permute(0, 1, 4, 2, 5, 3).reshape(1, 32, 2 * h, 2 * w)
    bild = vae_flux.bild(vae, z, device, dtype, skala=1.0, versatz=0.0)
    aufraeumen()
    return bild
