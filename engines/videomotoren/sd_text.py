"""
sd_text.py - Text-Encoder von Stable Diffusion aus dem Checkpoint: CLIP-L (SD 1.5), CLIP-L + CLIP-G (SDXL).

SD 1.5 nimmt den letzten Zustand von CLIP-L nach der Schluss-Norm. SDXL nimmt von beiden Encodern den
vorletzten Zustand ohne Schluss-Norm, nebeneinander (768 + 1280), dazu den gepoolten Zustand von CLIP-G
(Schluss-Norm am Ende-Token, dann die Text-Projektion).

CLIP-L liegt mit den Namen von transformers im Checkpoint (`…text_model.encoder.layers.0.self_attn.q_proj`),
CLIP-G mit denen von OpenCLIP (`…transformer.resblocks.0.attn.in_proj_weight`).
"""

from __future__ import annotations

from pathlib import Path

from bildmotoren.gewichte import Quelle

LAENGE = 77
KOPF_BREITE = 64
ANFANG, ENDE = 49406, 49407
VORSATZ_L = ("cond_stage_model.transformer.text_model.", "conditioner.embedders.0.transformer.text_model.")
VORSATZ_G = "conditioner.embedders.1.model."


def vorsatz_l(alle) -> str | None:
    return next((v for v in VORSATZ_L if v + "embeddings.token_embedding.weight" in alle), None)


def hat_g(alle) -> bool:
    return VORSATZ_G + "token_embedding.weight" in alle


def laden_l(pfad: Path) -> dict:
    """CLIP-L aus dem Checkpoint, float32 im Arbeitsspeicher."""
    with Quelle(pfad) as q:
        v = vorsatz_l(set(q.alle()))
        if v is None:
            raise RuntimeError(f"{Path(pfad).name}: kein CLIP-L im Checkpoint")
        q.vorsatz = v
        n = 1 + max(int(k.split(".")[2]) for k in q.alle() if k.startswith("encoder.layers."))
        schichten = []
        for i in range(n):
            p = f"encoder.layers.{i}."
            schichten.append({
                "q": _wb(q, p + "self_attn.q_proj"), "k": _wb(q, p + "self_attn.k_proj"),
                "v": _wb(q, p + "self_attn.v_proj"), "aus": _wb(q, p + "self_attn.out_proj"),
                "norm1": _wb(q, p + "layer_norm1"), "norm2": _wb(q, p + "layer_norm2"),
                "fc1": _wb(q, p + "mlp.fc1"), "fc2": _wb(q, p + "mlp.fc2")})
        return {"schichten": schichten, "tok": q.tensor("embeddings.token_embedding.weight").float(),
                "pos": q.tensor("embeddings.position_embedding.weight").float(),
                "norm": _wb(q, "final_layer_norm"), "schnell": True, "projektion": None}


def laden_g(pfad: Path) -> dict:
    """CLIP-G (OpenCLIP bigG) aus einem SDXL-Checkpoint, float32 im Arbeitsspeicher."""
    with Quelle(pfad, VORSATZ_G) as q:
        n = 1 + max(int(k.split(".")[2]) for k in q.alle() if k.startswith("transformer.resblocks."))
        schichten = []
        for i in range(n):
            p = f"transformer.resblocks.{i}."
            w = q.tensor(p + "attn.in_proj_weight").float()
            b = q.tensor(p + "attn.in_proj_bias").float()
            d = w.shape[1]
            schichten.append({
                "q": (w[:d], b[:d]), "k": (w[d:2 * d], b[d:2 * d]), "v": (w[2 * d:], b[2 * d:]),
                "aus": _wb(q, p + "attn.out_proj"), "norm1": _wb(q, p + "ln_1"), "norm2": _wb(q, p + "ln_2"),
                "fc1": _wb(q, p + "mlp.c_fc"), "fc2": _wb(q, p + "mlp.c_proj")})
        return {"schichten": schichten, "tok": q.tensor("token_embedding.weight").float(),
                "pos": q.tensor("positional_embedding").float(), "norm": _wb(q, "ln_final"), "schnell": False,
                "projektion": q.tensor("text_projection").float()}


def _wb(q: Quelle, name: str):
    return q.tensor(name + ".weight").float(), q.tensor(name + ".bias").float()


def zustaende(gew: dict, ids: list[int], device):
    """(letzter Zustand nach der Schluss-Norm, vorletzter Zustand ohne sie, gepoolt oder None) – je (77, Breite)."""
    import torch
    import torch.nn.functional as F
    auf = lambda wb: (wb[0].to(device), wb[1].to(device))                             # noqa: E731
    with torch.no_grad():
        t = torch.tensor(ids, dtype=torch.long)
        n = len(ids)
        x = (gew["tok"][t] + gew["pos"][:n]).to(device)[None]
        koepfe = x.shape[-1] // KOPF_BREITE
        vorletzter = None
        for i, roh in enumerate(gew["schichten"]):
            if i == len(gew["schichten"]) - 1:
                vorletzter = x
            L = {k: auf(v) for k, v in roh.items()}
            h = F.layer_norm(x, (x.shape[-1],), *L["norm1"], 1e-5)
            q, k, v = (F.linear(h, *L[c]).view(1, n, koepfe, -1).transpose(1, 2) for c in "qkv")
            a = F.scaled_dot_product_attention(q, k, v, is_causal=True).transpose(1, 2).reshape(1, n, -1)
            x = x + F.linear(a, *L["aus"])
            h = F.linear(F.layer_norm(x, (x.shape[-1],), *L["norm2"], 1e-5), *L["fc1"])
            h = h * torch.sigmoid(1.702 * h) if gew["schnell"] else F.gelu(h)
            x = x + F.linear(h, *L["fc2"])
        letzter = F.layer_norm(x, (x.shape[-1],), *auf(gew["norm"]), 1e-5)
        gepoolt = None
        if gew["projektion"] is not None:
            gepoolt = letzter[0, int(t.argmax())] @ gew["projektion"].to(device)
        return letzter[0], vorletzter[0], gepoolt


def ids_g(ids: list[int]) -> list[int]:
    """CLIP-G füllt hinter dem Ende-Token mit 0 auf, nicht mit dem Ende-Token."""
    ende = ids.index(ENDE) + 1
    return ids[:ende] + [0] * (len(ids) - ende)


def text_sd15(clip_l: dict, ids: list[int], device, dtype):
    """Text für SD 1.5: (1, 77, 768)."""
    return zustaende(clip_l, ids, device)[0][None].to(dtype)


def text_sdxl(clip_l: dict, clip_g: dict, ids: list[int], device, dtype):
    """Text für SDXL: ((1, 77, 2048), gepoolt (1280,))."""
    import torch
    _, l, _ = zustaende(clip_l, ids, device)
    _, g, gepoolt = zustaende(clip_g, ids_g(ids), device)
    return torch.cat([l, g], dim=-1)[None].to(dtype), gepoolt
