"""
zimage.py - Z-Image (Tongyi-MAI), z. B. Z-Image-Turbo: Single-Stream-DiT mit 6 B Parametern.

    Prompt -> Qwen3-4B (Zustand nach der vorletzten Schicht) -> DiT -> Flux-VAE -> PNG

Turbo: 9 Schritte, ohne CFG, feste Zeitverschiebung 3.0. Liest ComfyUI-Namen (fusioniertes qkv,
auch als GGUF) und diffusers-Namen (to_q/to_k/to_v).
"""

from __future__ import annotations

import math
from pathlib import Path

from .gemeinsam import Bloecke, aufraeumen, aus_ram, euler, sigmas_linear
from .gewichte import Quelle, namen, ohne_praefix, zu

TITEL = "Z-Image"
SYMBOL = "🟩"
STANDARD = {"schritte": 9, "groesse": (1024, 1024), "shift": 3.0}
SCHRITTE = (4, 30)
TEXT_BIS = -2                       # hidden_states[-2]: Zustand nach der vorletzten Schicht
TEXT_MAX = 512
NORM_EPS = 1e-5
ROPE_THETA = 256.0
ROPE_ACHSEN = (32, 48, 48)
FOLGE_VIELFACHES = 32
VORLAGE = ("<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n")

# Bekannte Dateien: Name -> (Bytes, SHA-256) laut Hugging Face
BEKANNT = {
    # Comfy-Org/z_image_turbo (split_files/…)
    "z_image_turbo_bf16.safetensors": (12309866400, "2407613050b809ffdff18a4ac99af83ea6b95443ecebdf80e064a79c825574a6"),
    "z_image_turbo_nvfp4.safetensors": (4509509600, "a553c889dbcb910de4c98293237573219a37007c1074a3f04576646a088bd5c8"),
    "qwen_3_4b.safetensors": (8044982048, "6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a"),
    "qwen_3_4b_fp8_mixed.safetensors": (5631994051, "72450b19758172c5a7273cf7de729d1c17e7f434a104a00167624cba94f68f15"),
    "qwen_3_4b_fp4_mixed.safetensors": (3479416193, "7ca32dcf07dfe7692945d80fff86e3a74cb83c6206b9b223ac6836b939bb85d6"),
    "ae.safetensors": (335304388, "afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38"),
    # unsloth/Z-Image-Turbo-GGUF
    "z-image-turbo-BF16.gguf": (12311939136, "9ccc4fb8b84519b8aa201abcffd900d19ee146b409ae9897625d235b104ef9c5"),
    "z-image-turbo-F16.gguf": (12311939136, "996ed5b13fc124053edec41d3ad8fef3fe1dca0ef3f7aa24b4123d84367d3666"),
    "z-image-turbo-Q2_K.gguf": (2311895616, "84e9fc7512a783b07b86fa7f096ed0a27d33b1649e3da352bf5303775e6571b3"),
    "z-image-turbo-Q3_K_M.gguf": (2696294976, "398853e879a1323759ccdf277790d7892680975f929f43f6d2fc852d3d8ab2ab"),
    "z-image-turbo-Q3_K_S.gguf": (3953380416, "4f875f50f7e7cb7b82ddf4c92906705292702ec9e6d7d8c1fd9d0c9d8ca287b6"),
    "z-image-turbo-Q4_0.gguf": (4585244736, "3da2a7650cbe4d18857c41ec3c6742c6dfd690c55d47a8b7ab0f01293740eb66"),
    "z-image-turbo-Q4_1.gguf": (4850665536, "54c1466254bc3e3b346f1bf18b2e5e04b92c7bb46d17a6c9a020697990e530d3"),
    "z-image-turbo-Q4_K_M.gguf": (3850130496, "4ab6bceb1f0b82c9e725f123848018eac662a6fc1d467c019e13e701afb7b55c"),
    "z-image-turbo-Q4_K_S.gguf": (4712702016, "e4a5193337b86ebd8e26bacf3d90dbdd4edeafe869a6561f6b78efd493d214aa"),
    "z-image-turbo-Q5_0.gguf": (5263542336, "902402f967801d43b2b6770541e1ebc808f35f8aa655ac16266ac7947338084b"),
    "z-image-turbo-Q5_1.gguf": (5528963136, "27ac9e1a858fb21810cb58afaabdf9affe55cbc351eee70fc815bcca49b7920f"),
    "z-image-turbo-Q5_K_M.gguf": (4619474496, "53b6f401c922192403e362e342d84e80b39703a4debd93a45191b4dcee9a1bff"),
    "z-image-turbo-Q5_K_S.gguf": (5239311936, "6e6c70d6c441d75e9d9934fb3152c834f5ace37025dd10e1a92702612eaef9e4"),
    "z-image-turbo-Q6_K.gguf": (5387973696, "6c52dea7308232c378d0d78c8970c0f72d258a4ccee3b81bbab4eb4508d01048"),
    "z-image-turbo-Q8_0.gguf": (7224707136, "f163d60b0eb427469510b8226243d196574a18139a2e40c017409cfbda95ecfe"),
    # unsloth/Qwen3-4B-GGUF (Text-Encoder)
    "Qwen3-4B-BF16.gguf": (8051285536, "f59dbdf0a0131f4bafe257e5eaf25137a88c89c7a009e9418b0e447accda58a8"),
    "Qwen3-4B-Q8_0.gguf": (4280405792, "eed555233267a33c7e8ee31682762cc7751b3f6d224039086e0e846f05fffa5d"),
    "Qwen3-4B-Q6_K.gguf": (3306261792, "002b8b61c298976afc2c9c3ca65f9dbb28deba22c4b6381853dfaa92b5690022"),
    "Qwen3-4B-Q5_K_M.gguf": (2889514272, "37edbd37f5b7a4d4e7a766400c57def1f790e809de03513b86bf11bb06979695"),
    "Qwen3-4B-Q4_K_M.gguf": (2497281312, "f6f851777709861056efcdad3af01da38b31223a3ba26e61a4f8bf3a2195813a"),
}


# ===========================================================================
#  Erkennen und Zubehör
# ===========================================================================

def erkennen(alle: dict) -> bool:
    """Namen (ohne Checkpoint-Vorsatz) eines Z-Image-DiT?"""
    return ("cap_embedder.1.weight" in alle and any(k.startswith("context_refiner.0.") for k in alle)
            and any(k.startswith("noise_refiner.0.") for k in alle))


def text_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .textenc import qwen3_form
    f = qwen3_form(pfad)
    return bool(f) and f["art"] == "qwen3" and f["hidden"] == 2560


def vae_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .vae_flux import ist_flux_vae
    return ist_flux_vae(pfad)


def vorgaben(dit: Path) -> dict:
    return dict(STANDARD)


ZUBEHOER = {
    "text": "Qwen3-4B als Text-Encoder (z. B. qwen_3_4b.safetensors oder Qwen3-4B-Q8_0.gguf)",
    "vae": "Flux-VAE (ae.safetensors)",
}


# ===========================================================================
#  Gewichte
# ===========================================================================

def _block(q: Quelle, p: str, dtype, mod: bool) -> dict:
    B = {}
    if q.hat(p + "attention.qkv.weight"):
        B["qkv"] = q.lin(p + "attention.qkv", dtype)
        B["out"] = q.lin(p + "attention.out", dtype)
        B["qn"] = q.tensor(p + "attention.q_norm.weight").float()
        B["kn"] = q.tensor(p + "attention.k_norm.weight").float()
    else:                                              # diffusers
        for n in ("q", "k", "v"):
            B[n] = q.lin(p + f"attention.to_{n}", dtype)
        B["out"] = q.lin(p + "attention.to_out.0", dtype)
        B["qn"] = q.tensor(p + "attention.norm_q.weight").float()
        B["kn"] = q.tensor(p + "attention.norm_k.weight").float()
    for n in ("w1", "w2", "w3"):
        B[n] = q.lin(p + "feed_forward." + n, dtype)
    for n in ("attention_norm1", "attention_norm2", "ffn_norm1", "ffn_norm2"):
        B[n] = q.tensor(p + n + ".weight").float()
    if mod:
        B["mod"] = q.lin(p + "adaLN_modulation.0", dtype)
    return B


def laden(pfad: Path, dtype) -> dict:
    """DiT im Arbeitsspeicher: kleine Teile (`klein`) und die Hauptschichten (`schichten`)."""
    _, vors = ohne_praefix(namen(pfad))
    with Quelle(pfad, vors) as q:
        diff = q.hat("all_x_embedder.2-1.weight")
        xe = "all_x_embedder.2-1" if diff else "x_embedder"
        fl = "all_final_layer.2-1" if diff else "final_layer"
        n_ref = 1 + max(int(k.split(".")[1]) for k in q.alle() if k.startswith("noise_refiner."))
        n_sch = 1 + max(int(k.split(".")[1]) for k in q.alle() if k.startswith("layers."))
        klein = {
            "t1": q.lin("t_embedder.mlp.0", dtype), "t2": q.lin("t_embedder.mlp.2", dtype),
            "x": q.lin(xe, dtype),
            "cap_norm": q.tensor("cap_embedder.0.weight").float(), "cap": q.lin("cap_embedder.1", dtype),
            "x_pad": q.tensor("x_pad_token").float().reshape(1, -1),
            "cap_pad": q.tensor("cap_pad_token").float().reshape(1, -1),
            "fin_mod": q.lin(fl + ".adaLN_modulation.1", dtype), "fin": q.lin(fl + ".linear", dtype),
            "rausch": [_block(q, f"noise_refiner.{i}.", dtype, True) for i in range(n_ref)],
            "kontext": [_block(q, f"context_refiner.{i}.", dtype, False) for i in range(n_ref)],
        }
        schichten = [_block(q, f"layers.{i}.", dtype, True) for i in range(n_sch)]
    return {"klein": klein, "schichten": schichten}


# ===========================================================================
#  Rechnen
# ===========================================================================

def _rms(x, w, dtype):
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w, eps=NORM_EPS).to(dtype)


def rope(pos, device):
    """pos (n, 3) -> cos, sin je (n, kopf/2) für die drei Achsen (Index, Höhe, Breite)."""
    import torch
    teile = []
    for i, d in enumerate(ROPE_ACHSEN):
        f = 1.0 / (ROPE_THETA ** (torch.arange(0, d, 2, dtype=torch.float64) / d))
        teile.append(torch.outer(pos[:, i].double(), f))
    w = torch.cat(teile, -1).float().to(device)
    return w.cos(), w.sin()


def _rope_an(x, cs):
    """x (1, H, n, D): Paare (2i, 2i+1) drehen."""
    import torch
    cos, sin = cs
    xf = x.float().unflatten(-1, (-1, 2))
    a, b = xf[..., 0], xf[..., 1]
    return torch.stack((a * cos - b * sin, a * sin + b * cos), -1).flatten(-2).to(x.dtype)


def _attention(B, h, cs, dtype):
    import torch.nn.functional as F
    n = h.shape[1]
    D = B["qn"].shape[0]
    if "qkv" in B:
        q, k, v = B["qkv"](h).chunk(3, -1)
    else:
        q, k, v = B["q"](h), B["k"](h), B["v"](h)
    q, k, v = (t.view(1, n, -1, D) for t in (q, k, v))
    q = _rope_an(_rms(q, B["qn"], dtype).transpose(1, 2), cs)
    k = _rope_an(_rms(k, B["kn"], dtype).transpose(1, 2), cs)
    a = F.scaled_dot_product_attention(q, k, v.transpose(1, 2))
    return B["out"](a.transpose(1, 2).reshape(1, n, -1))


def block(B, x, cs, adaln, dtype):
    """Ein Transformer-Block; x (1, n, dim) in float32."""
    import torch.nn.functional as F
    if "mod" in B:
        m = B["mod"](adaln).float()[:, None]
        s_attn, g_attn, s_ffn, g_ffn = m.chunk(4, -1)
        g_attn, g_ffn = g_attn.tanh(), g_ffn.tanh()
        h = (_rms(x, B["attention_norm1"], dtype).float() * (1 + s_attn)).to(dtype)
        x = x + g_attn * _rms(_attention(B, h, cs, dtype), B["attention_norm2"], dtype).float()
        h = (_rms(x, B["ffn_norm1"], dtype).float() * (1 + s_ffn)).to(dtype)
        f = B["w2"](F.silu(B["w1"](h)) * B["w3"](h))
        return x + g_ffn * _rms(f, B["ffn_norm2"], dtype).float()
    h = _rms(x, B["attention_norm1"], dtype)
    x = x + _rms(_attention(B, h, cs, dtype), B["attention_norm2"], dtype).float()
    h = _rms(x, B["ffn_norm1"], dtype)
    return x + _rms(B["w2"](F.silu(B["w1"](h)) * B["w3"](h)), B["ffn_norm2"], dtype).float()


def _zeit(t, dim=256):
    import torch
    half = dim // 2
    f = torch.exp(-math.log(10000.0) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
    a = t[:, None].float() * f[None]
    return torch.cat([torch.cos(a), torch.sin(a)], -1)


class Netz:
    """Das DiT für ein Bild: kleine Teile auf der Karte, Hauptschichten über `Bloecke`."""

    def __init__(self, ram: dict, cap_feats, device, dtype, platz: int | None = None):
        import torch
        self.device, self.dtype = device, dtype
        self.k = zu(ram["klein"], device)
        self.schichten = Bloecke(ram["schichten"], device, platz)
        with torch.no_grad():
            n = cap_feats.shape[0]
            self.n_cap = n + (-n) % FOLGE_VIELFACHES
            c = _rms(cap_feats.to(device), self.k["cap_norm"], dtype)
            c = self.k["cap"](c).float()
            pad = self.k["cap_pad"].to(device).expand(self.n_cap - n, -1)
            self.cap = torch.cat([c, pad], 0)[None]
            pos = torch.zeros(self.n_cap, 3, dtype=torch.long)
            pos[:, 0] = torch.arange(1, self.n_cap + 1)
            self.cap_cs = rope(pos, device)
            self._kontext_fertig = None

    def _kontext(self):
        if self._kontext_fertig is None:
            c = self.cap
            for B in self.k["kontext"]:
                c = block(B, c, self.cap_cs, None, self.dtype)
            self._kontext_fertig = c
        return self._kontext_fertig

    def __call__(self, lat, sigma: float):
        """lat (1, 16, H, W) float32 -> Ausgabe des Netzes (gleiche Form). Geschwindigkeit = -Ausgabe."""
        import torch
        dt = self.dtype
        _, C, H, W = lat.shape
        ht, wt = H // 2, W // 2
        n_bild = ht * wt
        n_x = n_bild + (-n_bild) % FOLGE_VIELFACHES
        t = torch.full((1,), (1.0 - sigma) * 1000.0, device=self.device)
        adaln = self.k["t2"](torch.nn.functional.silu(self.k["t1"](_zeit(t).to(dt)))).to(dt)

        p = lat.view(C, ht, 2, wt, 2).permute(1, 3, 2, 4, 0).reshape(n_bild, 4 * C)
        x = self.k["x"](p.to(dt)).float()
        if n_x > n_bild:
            x = torch.cat([x, self.k["x_pad"].expand(n_x - n_bild, -1)], 0)
        pos = torch.zeros(n_x, 3, dtype=torch.long)
        pos[:n_bild, 0] = self.n_cap + 1
        pos[:n_bild, 1] = torch.arange(ht).repeat_interleave(wt)
        pos[:n_bild, 2] = torch.arange(wt).repeat(ht)
        x_cs = rope(pos, self.device)
        x = x[None]
        for B in self.k["rausch"]:
            x = block(B, x, x_cs, adaln, dt)

        u = torch.cat([x, self._kontext()], 1)
        cs = (torch.cat([x_cs[0], self.cap_cs[0]]), torch.cat([x_cs[1], self.cap_cs[1]]))
        for B in self.schichten:
            u = block(B, u, cs, adaln, dt)

        skala = 1.0 + self.k["fin_mod"](torch.nn.functional.silu(adaln)).float()
        u = torch.nn.functional.layer_norm(u, (u.shape[-1],), eps=1e-6) * skala[:, None]
        o = self.k["fin"](u[0, :n_bild].to(dt)).float()
        return o.view(ht, wt, 2, 2, C).permute(4, 0, 2, 1, 3).reshape(1, C, H, W)

    def geschwindigkeit(self, lat, sigma: float):
        """Flow-Geschwindigkeit für den Sampler: Z-Image sagt ihr Gegenteil voraus."""
        return -self(lat, sigma)


# ===========================================================================
#  Ein Bild
# ===========================================================================

def text_zustand(prompt: str, teile: dict, device, dtype, on_status=None):
    """Prompt -> (n, 2560) im Rechentyp."""
    if on_status:
        on_status(f"{TITEL}: Text-Encoder …")
    from .textenc import qwen3_form, qwen3_laden, qwen3_zustand, tokenizer_fuer
    pfad = teile["text"]
    bis = qwen3_form(pfad)["schichten"] + 1 + TEXT_BIS
    gew = aus_ram("text", (pfad, bis), lambda: qwen3_laden(pfad, bis, dtype))
    ids = tokenizer_fuer(pfad, teile["tokenizer"])(VORLAGE.format(prompt))[:TEXT_MAX]
    h = qwen3_zustand(gew, ids, device, dtype).detach().clone()
    aufraeumen()
    return h


def malen(prompt: str, teile: dict, *, schritte: int, breite: int, hoehe: int, seed: int,
          device, dtype, on_status=None, shift: float | None = None):
    """Rechnet ein Bild und gibt es als uint8-Array (H, W, 3) zurück."""
    import torch
    from . import vae_flux
    cap = text_zustand(prompt, teile, device, dtype, on_status)

    if on_status:
        on_status(f"{TITEL}: Modell in den Grafikspeicher …")
    ram = aus_ram("dit", teile["dit"], lambda: laden(teile["dit"], dtype))
    netz = Netz(ram, cap, device, dtype)
    lat = torch.randn((1, 16, hoehe // 8, breite // 8), generator=torch.Generator().manual_seed(seed),
                      dtype=torch.float32).to(device)
    sig = sigmas_linear(schritte, STANDARD["shift"] if shift is None else shift)
    lat = euler(netz.geschwindigkeit, lat, sig, on_status, TITEL)
    netz = cap = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: VAE …")
    vae = aus_ram("vae", teile["vae"], lambda: vae_flux.laden(teile["vae"]))
    bild = vae_flux.bild(vae, lat, device, dtype)
    aufraeumen()
    return bild
