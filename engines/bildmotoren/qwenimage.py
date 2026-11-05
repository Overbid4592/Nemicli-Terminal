"""
qwenimage.py - Qwen-Image (Alibaba Qwen), auch Qwen-Image-2512: MMDiT mit 60 Doppel-Blöcken (20 B).

    Prompt -> Qwen2.5-VL-7B (Sprachteil, Zustand nach der Schluss-Norm, ohne die 34 Token der Systemvorlage)
           -> DiT -> Qwen-Image-VAE (dieselbe wie Krea 2) -> PNG

50 Schritte mit „echtem“ CFG 4 gegen einen leeren Prompt, Norm der Vorhersage zurück auf die des Prompts.
Zeitverschiebung nach Bildgröße, letzter Schritt auf 0.02 gestreckt. Namen wie diffusers (auch ComfyUI, GGUF).
"""

from __future__ import annotations

import math
from pathlib import Path

from .gemeinsam import Bloecke, aufraeumen, aus_ram, euler, mu_linear, sigmas_flux
from .gewichte import Quelle, namen, ohne_praefix, zu

TITEL = "Qwen-Image"
SYMBOL = "🟧"
STANDARD = {"schritte": 30, "groesse": (1328, 1328), "cfg": 4.0}
SCHRITTE = (4, 60)
VORLAGE = ("<|im_start|>system\nDescribe the image by detailing the color, shape, size, texture, quantity, text, "
           "spatial relationships of the objects and background:<|im_end|>\n<|im_start|>user\n{}<|im_end|>\n"
           "<|im_start|>assistant\n")
VORLAGE_WEG = 34                        # Token der Systemvorlage, die nicht ans DiT gehen
TEXT_MAX = 1024
GEGEN_PROMPT = " "
ROPE_ACHSEN = (16, 56, 56)
ROPE_THETA = 10000.0
EPS = 1e-6
ZEIT = {"basis_n": 256, "max_n": 8192, "basis": 0.5, "hoch": 0.9, "ende": 0.02}

BEKANNT = {
    # Comfy-Org/Qwen-Image_ComfyUI (split_files/…)
    "qwen_image_bf16.safetensors": (40861031488, "d08fb5d68026c0d87325f9a7b3ad6454061113a2bc73cc883114dae172937ae7"),
    "qwen_image_fp8_e4m3fn.safetensors": (20430635136, "98763a127701eb6fb59096f7742cb3aa7d64ed510b9f4e882d8351f8176e3ce3"),
    "qwen_image_fp8mixed.safetensors": (20533672381, "0ee8375df948e428b5cee97b62f7311ab11a6085eb5b1918f66bfab047fedc18"),
    "qwen_image_nvfp4.safetensors": (19769139136, "54df91fa379a6487aa7869b678d5c1fa9e4b30db1019d0ab8dbce629daa8a5a8"),
    "qwen_image_2512_bf16.safetensors": (40861031488, "cbf55390fff27dbc785046d7007b04e0c5dd7421e7ef128f2831eacb53a8e075"),
    "qwen_image_2512_fp8_e4m3fn.safetensors": (20430679144, "5dc80554d5d83390046a2f4a94ece06afb7700bf7b0aaf8bde9769793875876b"),
    "qwen_2.5_vl_7b.safetensors": (16584415576, "cfafd739459bc86257397259f612a9aee88e5b98e85b5c0d0d1717e898b3463a"),
    "qwen_2.5_vl_7b_fp8_scaled.safetensors": (9384670680, "cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4"),
    "qwen_2.5_vl_7b_nvfp4.safetensors": (6114433446, "c7bb24d331f5df991bab2ce76a15195b12652e86a69a79823183c6a047407463"),
    "qwen_image_vae.safetensors": (253806246, "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f"),
    # QuantStack/Qwen-Image-GGUF · unsloth/Qwen-Image-2512-GGUF
    "Qwen_Image-Q4_K_M.gguf": (13065746976, "645473886d7dbb0103f84c563c798f7b0867293d919752d4d6be6a432b0bc988"),
    "Qwen_Image-Q5_K_M.gguf": (14934899232, "e4e95109fa78049c411b4fdbe058dcd8f285b5da683aab96d62618c84b482ced"),
    "Qwen_Image-Q6_K.gguf": (16824990240, "0c60643ee869161ee7acd530f7faf1cb2361f131e1ebae27b95fdc481b6df2b2"),
    "Qwen_Image-Q8_0.gguf": (21761817120, "43142b32778dc0568c27395abc6f8291ac53f1de0b98eed919712cf161f248de"),
    "qwen-image-2512-Q4_K_M.gguf": (13299575328, "134aa66a4cb27b46e7528486a37d87736a03e48cea6ba88e5de2068543ec6bcc"),
    "qwen-image-2512-Q5_K_M.gguf": (15395441184, "2accd8aee680ae760bad9db5bf45a54ab336910a4716c77de515c24525b2213f"),
    "qwen-image-2512-Q6_K.gguf": (17948936736, "05f541bf5c61590fe59cc0e9d9dfa7f75fa8042ab1ff47f22323f782e056d3d9"),
    "qwen-image-2512-Q8_0.gguf": (22354590240, "be8144b3c6983dec6c6c3fe8c383d41ce92ed0bf40f288ebb8fd403d02cdab76"),
    # unsloth/Qwen2.5-VL-7B-Instruct-GGUF (Text-Encoder)
    "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf": (4683072384, "d16776dcd9a28d42758c2958ed3a752aabf20a305252cd64ff2be72b4a78c503"),
    "Qwen2.5-VL-7B-Instruct-Q5_K_M.gguf": (5444830080, "793abbb5069da0ca9e11fbcb26352fda62391dace0ad13924db3d0dc57ee819c"),
    "Qwen2.5-VL-7B-Instruct-Q6_K.gguf": (6254197632, "15f3ccbef1e7020939d8c32501d66a777df46b1b3ace9ddec37b5d2df102a89f"),
    "Qwen2.5-VL-7B-Instruct-Q8_0.gguf": (8098524032, "ee770c700d7429cc6f0c74d6c7ab3c063bf521312fc36e80776d1d79bc9fa4ad"),
}


# ===========================================================================
#  Erkennen, Zubehör
# ===========================================================================

def erkennen(alle: dict) -> bool:
    return "txt_norm.weight" in alle and "transformer_blocks.0.img_mod.1.weight" in alle


def text_passt(pfad: Path, dit: Path | None = None) -> bool:
    """Qwen2.5-VL (Sprachteil): Qwen2-Bauart mit der Breite, die das DiT erwartet (7B: 3584)."""
    from .textenc import qwen3_form
    f = qwen3_form(pfad)
    if not f or f["art"] != "qwen2":
        return False
    if dit is not None:
        alle, _ = ohne_praefix(namen(dit))
        form = alle.get("txt_norm.weight")
        return bool(form) and int(form[0]) == f["hidden"]
    return f["hidden"] == 3584


def vae_passt(pfad: Path, dit: Path | None = None) -> bool:
    """Qwen-Image-VAE im ComfyUI-/Wan-Format (kausale 3D-Faltungen, 16 Latent-Kanäle)."""
    alle = namen(pfad)
    form = alle.get("conv2.weight")
    return "decoder.middle.0.residual.0.gamma" in alle and bool(form) and int(form[1]) == 16


def vae_orte() -> list[Path]:
    """Krea 2 bringt dieselbe VAE mit – ihr Ordner wird mit durchsucht."""
    try:
        import krea
        return [d / "vae" for d in krea.KREA_DIRS]
    except Exception:
        return []


def vorgaben(dit: Path) -> dict:
    return dict(STANDARD)


ZUBEHOER = {
    "text": "Qwen2.5-VL-7B als Text-Encoder (qwen_2.5_vl_7b_fp8_scaled.safetensors oder "
            "Qwen2.5-VL-7B-Instruct-Q8_0.gguf)",
    "vae": "Qwen-Image-VAE (qwen_image_vae.safetensors – dieselbe wie bei Krea 2)",
}


# ===========================================================================
#  Gewichte
# ===========================================================================

def _block(q: Quelle, i: int, dtype) -> dict:
    p = f"transformer_blocks.{i}."
    lin = lambda n: q.lin(p + n, dtype)                  # noqa: E731
    norm = lambda n: q.tensor(p + n + ".weight").float()   # noqa: E731
    return {
        "img_mod": lin("img_mod.1"), "txt_mod": lin("txt_mod.1"),
        "img": {"q": lin("attn.to_q"), "k": lin("attn.to_k"), "v": lin("attn.to_v"), "out": lin("attn.to_out.0"),
                "qn": norm("attn.norm_q"), "kn": norm("attn.norm_k"),
                "mlp_in": lin("img_mlp.net.0.proj"), "mlp_out": lin("img_mlp.net.2")},
        "txt": {"q": lin("attn.add_q_proj"), "k": lin("attn.add_k_proj"), "v": lin("attn.add_v_proj"),
                "out": lin("attn.to_add_out"), "qn": norm("attn.norm_added_q"), "kn": norm("attn.norm_added_k"),
                "mlp_in": lin("txt_mlp.net.0.proj"), "mlp_out": lin("txt_mlp.net.2")},
    }


def laden(pfad: Path, dtype) -> dict:
    alle, vors = ohne_praefix(namen(pfad))
    n = 1 + max(int(k.split(".")[1]) for k in alle if k.startswith("transformer_blocks."))
    with Quelle(pfad, vors) as q:
        klein = {"img_in": q.lin("img_in", dtype), "txt_in": q.lin("txt_in", dtype),
                 "txt_norm": q.tensor("txt_norm.weight").float(),
                 "t1": q.lin("time_text_embed.timestep_embedder.linear_1", dtype),
                 "t2": q.lin("time_text_embed.timestep_embedder.linear_2", dtype),
                 "fin_mod": q.lin("norm_out.linear", dtype), "fin": q.lin("proj_out", dtype)}
        bloecke = [_block(q, i, dtype) for i in range(n)]
    return {"klein": klein, "bloecke": bloecke}


# ===========================================================================
#  Rechnen
# ===========================================================================

def _ln(x):
    import torch.nn.functional as F
    return F.layer_norm(x.float(), (x.shape[-1],), eps=EPS)


def _rms(x, w, dtype):
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w, eps=EPS).to(dtype)


def _zeit(t, dim=256):
    import torch
    half = dim // 2
    f = torch.exp(-math.log(10000.0) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
    a = t[:, None].float() * f[None]
    return torch.cat([torch.cos(a), torch.sin(a)], -1)


def rope(pos, device):
    """pos (n, 3) (auch negativ) -> cos, sin je (n, kopf/2) für Bild (Index, Höhe, Breite) bzw. Text."""
    import torch
    teile = []
    for i, d in enumerate(ROPE_ACHSEN):
        f = 1.0 / torch.pow(ROPE_THETA, torch.arange(0, d, 2, dtype=torch.float32) / d)
        teile.append(torch.outer(pos[:, i].float(), f))
    w = torch.cat(teile, -1).to(device)
    return w.cos(), w.sin()


def positionen(h: int, w: int, n_txt: int):
    """Bild-Positionen zentriert (−h/2 … h/2−1), Text danach ab max(h/2, w/2) auf allen Achsen."""
    import torch
    hh = torch.cat([torch.arange(-(h - h // 2), 0), torch.arange(h // 2)])
    ww = torch.cat([torch.arange(-(w - w // 2), 0), torch.arange(w // 2)])
    bild = torch.zeros(h * w, 3, dtype=torch.long)
    bild[:, 1] = hh.repeat_interleave(w)
    bild[:, 2] = ww.repeat(h)
    start = max(h // 2, w // 2)
    txt = torch.arange(start, start + n_txt)[:, None].expand(n_txt, 3).clone()
    return bild, txt


def _rope_an(x, cs):
    import torch
    cos, sin = cs
    xf = x.float().unflatten(-1, (-1, 2))
    a, b = xf[..., 0], xf[..., 1]
    return torch.stack((a * cos - b * sin, a * sin + b * cos), -1).flatten(-2).to(x.dtype)


def _seite(S, h, D, dtype, cs):
    n = h.shape[1]
    q, k, v = (S[x](h).view(1, n, -1, D) for x in "qkv")
    q = _rope_an(_rms(q, S["qn"], dtype).transpose(1, 2), cs)
    k = _rope_an(_rms(k, S["kn"], dtype).transpose(1, 2), cs)
    return q, k, v.transpose(1, 2)


def block(B, img, txt, temb_s, cs_img, cs_txt, dtype, D):
    import torch
    import torch.nn.functional as F
    mi = B["img_mod"](temb_s).float()[:, None].chunk(6, -1)
    mt = B["txt_mod"](temb_s).float()[:, None].chunk(6, -1)
    hi = (_ln(img) * (1 + mi[1]) + mi[0]).to(dtype)
    ht = (_ln(txt) * (1 + mt[1]) + mt[0]).to(dtype)
    qi, ki, vi = _seite(B["img"], hi, D, dtype, cs_img)
    qt, kt, vt = _seite(B["txt"], ht, D, dtype, cs_txt)
    a = F.scaled_dot_product_attention(torch.cat([qt, qi], 2), torch.cat([kt, ki], 2), torch.cat([vt, vi], 2))
    a = a.transpose(1, 2).flatten(2)
    n_txt = txt.shape[1]
    img = img + mi[2] * B["img"]["out"](a[:, n_txt:]).float()
    txt = txt + mt[2] * B["txt"]["out"](a[:, :n_txt]).float()
    gelu = lambda x: F.gelu(x, approximate="tanh")     # noqa: E731
    h = (_ln(img) * (1 + mi[4]) + mi[3]).to(dtype)
    img = img + mi[5] * B["img"]["mlp_out"](gelu(B["img"]["mlp_in"](h))).float()
    h = (_ln(txt) * (1 + mt[4]) + mt[3]).to(dtype)
    txt = txt + mt[5] * B["txt"]["mlp_out"](gelu(B["txt"]["mlp_in"](h))).float()
    return img, txt


class Netz:
    def __init__(self, ram: dict, device, dtype, platz: int | None = None):
        self.device, self.dtype = device, dtype
        self.k = zu(ram["klein"], device)
        self.bloecke = Bloecke(ram["bloecke"], device, platz)
        self.D = int(ram["bloecke"][0]["img"]["qn"].shape[0])

    def kontext(self, ctx):
        """Text-Zustand (n, 3584) -> Eingang der Blöcke (1, n, dim)."""
        import torch
        with torch.no_grad():
            return self.k["txt_in"](_rms(ctx.to(self.device), self.k["txt_norm"], self.dtype)).float()[None]

    def __call__(self, x, sigma: float, txt, h: int, w: int):
        """x (1, h·w, 64) float32 -> Geschwindigkeit (1, h·w, 64)."""
        import torch
        import torch.nn.functional as F
        dt, k = self.dtype, self.k
        t = torch.full((1,), sigma * 1000.0, device=self.device)
        temb = k["t2"](F.silu(k["t1"](_zeit(t).to(dt))))
        s = F.silu(temb)
        bild_pos, txt_pos = positionen(h, w, txt.shape[1])
        cs_img, cs_txt = rope(bild_pos, self.device), rope(txt_pos, self.device)
        img = k["img_in"](x.to(dt)).float()
        for B in self.bloecke:
            img, txt = block(B, img, txt, s, cs_img, cs_txt, dt, self.D)
        scale, shift = k["fin_mod"](s).float()[:, None].chunk(2, -1)
        return k["fin"]((_ln(img) * (1 + scale) + shift).to(dt)).float()


# ===========================================================================
#  Ein Bild
# ===========================================================================

def text_zustand(prompt: str, teile: dict, device, dtype, on_status=None):
    """Prompt -> (n, 3584): Zustand nach der Schluss-Norm, ohne die Systemvorlage."""
    from .textenc import qwen3_form, qwen3_laden, qwen3_zustaende, tokenizer_fuer
    if on_status:
        on_status(f"{TITEL}: Text-Encoder …")
    pfad = teile["text"]
    n = qwen3_form(pfad)["schichten"]
    gew = aus_ram("text", (pfad, n), lambda: qwen3_laden(pfad, n, dtype, schluss_norm=True))
    ids = tokenizer_fuer(pfad, teile["tokenizer"])(VORLAGE.format(prompt))[:TEXT_MAX + VORLAGE_WEG]
    h = qwen3_zustaende(gew, ids, device, dtype, ["norm"])[0][VORLAGE_WEG:].detach().clone()
    aufraeumen()
    return h


def cfg_mischen(v_pos, v_neg, cfg: float):
    """„Echtes“ CFG wie diffusers: danach die Norm je Token zurück auf die der Prompt-Vorhersage."""
    import torch
    v = v_neg + cfg * (v_pos - v_neg)
    return v * (torch.norm(v_pos, dim=-1, keepdim=True) / torch.norm(v, dim=-1, keepdim=True))


def malen(prompt: str, teile: dict, *, schritte: int, breite: int, hoehe: int, seed: int,
          device, dtype, on_status=None, cfg: float | None = None):
    import torch
    cfg = STANDARD["cfg"] if cfg is None else cfg
    ctx = text_zustand(prompt, teile, device, dtype, on_status)
    neg = text_zustand(GEGEN_PROMPT, teile, device, dtype) if cfg > 1.0 else None

    if on_status:
        on_status(f"{TITEL}: Modell in den Grafikspeicher …")
    ram = aus_ram("dit", teile["dit"], lambda: laden(teile["dit"], dtype))
    netz = Netz(ram, device, dtype)
    txt, txt_neg = netz.kontext(ctx), (netz.kontext(neg) if neg is not None else None)
    h, w = hoehe // 16, breite // 16
    lat = torch.randn((1, 16, 2 * h, 2 * w), generator=torch.Generator().manual_seed(seed), dtype=torch.float32)
    x = lat.view(1, 16, h, 2, w, 2).permute(0, 2, 4, 1, 3, 5).reshape(1, h * w, 64).to(device)
    z = ZEIT
    sig = sigmas_flux(schritte, mu_linear(h * w, z["basis_n"], z["max_n"], z["basis"], z["hoch"]), ende=z["ende"])

    def geschwindigkeit(x, s):
        v = netz(x, s, txt, h, w)
        return v if txt_neg is None else cfg_mischen(v, netz(x, s, txt_neg, h, w), cfg)

    x = euler(geschwindigkeit, x, sig, on_status, TITEL)
    netz = txt = txt_neg = ctx = neg = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: VAE …")
    import krea
    lat = x.view(1, h, w, 16, 2, 2).permute(0, 3, 1, 4, 2, 5).reshape(1, 16, 2 * h, 2 * w)
    mittel = torch.tensor(krea._LAT_MEAN, device=device).view(1, 16, 1, 1)
    std = torch.tensor(krea._LAT_STD, device=device).view(1, 16, 1, 1)
    rechnen = torch.float32 if dtype == torch.float16 else dtype
    vae = krea._VAEDecoder(teile["vae"], device, rechnen)
    with torch.no_grad():
        img = vae(lat * std + mittel)
    vae = None
    aufraeumen()
    return ((img[0].permute(1, 2, 0).float().cpu().numpy() + 1.0) * 127.5).round().clip(0, 255).astype("uint8")
