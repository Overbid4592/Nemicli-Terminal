"""
gemma_text.py - Gemma 4 als Text-Encoder (LTX-2.5): alle Zwischenzustände (Einbettung + jede Schicht).

Gemma 4: RMS-Normen mit Gewicht (ohne +1), Sandwich-Normen um Attention und MLP, Skalar je Schicht,
Einbettung × √Breite, Attention ohne 1/√d (Q/K genormt, V ohne Gewicht genormt). Fenster-Schichten mit
eigener RoPE; globale Schichten (jede sechste) mit größerer Kopfbreite, Schlüssel = Werte und „proportionaler“
RoPE auf einem Teil der Breite.

Die LTX-Datei „gemma4-…-with-proj“ bringt Tokenizer (tokenizer_json), Konfiguration (gemma_config) und die
Projektionen auf Video und Ton (text_embedding_projection.*) mit.
"""

from __future__ import annotations

import json
from pathlib import Path

from bildmotoren.gemeinsam import Bloecke
from bildmotoren.gewichte import Quelle, namen, st_kopf


def ist_gemma4(pfad: Path) -> bool:
    alle = namen(pfad)
    return "model.layers.0.layer_scalar" in alle and "model.layers.0.pre_feedforward_layernorm.weight" in alle


def konfig(pfad: Path) -> dict:
    meta = st_kopf(pfad).get("__metadata__") or {}
    try:
        g = json.loads(meta.get("gemma_config") or "{}")
    except ValueError:
        g = {}
    return g.get("text_config", g)


def laden(pfad: Path, dtype) -> dict:
    """Schichten, Einbettung und (falls vorhanden) die Video-/Ton-Projektionen im Arbeitsspeicher."""
    c = konfig(pfad)
    with Quelle(pfad) as q:
        n = 1 + max(int(k.split(".")[2]) for k in q.alle() if k.startswith("model.layers."))
        typen = c.get("layer_types") or ["full_attention" if (i + 1) % 6 == 0 else "sliding_attention" for i in range(n)]
        schichten = []
        for i in range(n):
            p = f"model.layers.{i}."
            L = {x: q.lin(p + x, dtype) for x in ("self_attn.q_proj", "self_attn.k_proj", "self_attn.o_proj",
                                                  "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj")}
            if q.hat(p + "self_attn.v_proj.weight"):
                L["self_attn.v_proj"] = q.lin(p + "self_attn.v_proj", dtype)
            for x in ("input_layernorm", "post_attention_layernorm", "pre_feedforward_layernorm",
                      "post_feedforward_layernorm", "self_attn.q_norm", "self_attn.k_norm"):
                L[x] = q.tensor(p + x + ".weight").float()
            L["skalar"] = q.tensor(p + "layer_scalar").float() if q.hat(p + "layer_scalar") else None
            L["global"] = typen[i] == "full_attention"
            schichten.append(L)
        aus = {"embed": q.tensor("model.embed_tokens.weight", dtype), "norm": q.tensor("model.norm.weight").float(),
               "schichten": schichten, "cfg": c}
        for s in ("video", "audio"):
            n_ = f"text_embedding_projection.{s}_aggregate_embed"
            if q.hat(n_ + ".weight"):
                aus[s + "_proj"] = q.lin(n_, dtype)
        if q.hat("tokenizer_json"):
            aus["tokenizer_json"] = bytes(q.tensor("tokenizer_json").tolist())
    return aus


def tokenizer(gew: dict, ordner: Path | None = None):
    """Gemma-Tokenizer: aus der Datei (tokenizer_json), sonst tokenizer.json im Ordner. Mit <bos> vorn."""
    from tokenizers import Tokenizer
    if "tokenizer_json" in gew:
        tok = Tokenizer.from_str(gew["tokenizer_json"].decode("utf-8"))
    else:
        tok = Tokenizer.from_file(str(Path(ordner) / "tokenizer.json"))
    tok.no_padding()
    tok.no_truncation()
    return lambda text: tok.encode(text, add_special_tokens=True).ids


def _rms(x, w, eps):
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w, eps=eps)


def _rope(n: int, kopf: int, theta: float, anteil: float, device):
    """cos, sin (n, kopf) für rotate_half; bei Anteil < 1 nur die ersten Frequenzen (proportional)."""
    import torch
    winkel = int(anteil * kopf // 2)
    inv = 1.0 / (theta ** (torch.arange(0, 2 * winkel, 2, dtype=torch.float32, device=device) / kopf))
    if kopf // 2 - winkel > 0:
        inv = torch.cat([inv, torch.zeros(kopf // 2 - winkel, device=device)])
    f = torch.arange(n, device=device, dtype=torch.float32)[:, None] * inv[None]
    e = torch.cat([f, f], -1)
    return e.cos(), e.sin()


def _drehen(x, cs):
    import torch
    cos, sin = cs[0][:, None], cs[1][:, None]            # (n, 1, D) gegen (n, Köpfe, D)
    h = x.shape[-1] // 2
    return x * cos + torch.cat([-x[..., h:], x[..., :h]], -1) * sin


def zustaende(gew: dict, ids: list[int], device, dtype):
    """(n, Breite, Schichten+1): Einbettung und jede Schicht; die letzte nach der Schluss-Norm (wie transformers)."""
    import torch
    import torch.nn.functional as F
    c = gew["cfg"]
    eps = float(c.get("rms_norm_eps", 1e-6))
    fenster = int(c.get("sliding_window", 1024))
    rp = c.get("rope_parameters", {})
    th_l = float(rp.get("sliding_attention", {}).get("rope_theta", 10000.0))
    th_g = float(rp.get("full_attention", {}).get("rope_theta", 1_000_000.0))
    anteil = float(rp.get("full_attention", {}).get("partial_rotary_factor", 0.25))
    with torch.no_grad():
        x = gew["embed"][torch.tensor(ids, dtype=torch.long)].to(device).float()
        n, breite = x.shape
        x = x * torch.tensor(breite ** 0.5, dtype=gew["embed"].dtype).float()
        aus = [x]
        j = torch.arange(n, device=device)
        kausal = j[None, :] <= j[:, None]
        maske_f = kausal & (j[:, None] - j[None, :] < fenster)
        cs = {}
        for L in Bloecke(gew["schichten"], device):
            D = L["self_attn.q_norm"].shape[0]
            if (L["global"], D) not in cs:
                cs[(L["global"], D)] = _rope(n, D, th_g, anteil, device) if L["global"] else _rope(n, D, th_l, 1.0, device)
            dreh = cs[(L["global"], D)]
            h = _rms(x, L["input_layernorm"], eps).to(dtype)
            q = L["self_attn.q_proj"](h).float().view(n, -1, D)
            k = L["self_attn.k_proj"](h).float().view(n, -1, D)
            v = L["self_attn.v_proj"](h).float().view(n, -1, D) if "self_attn.v_proj" in L else k
            q = _drehen(_rms(q, L["self_attn.q_norm"], eps), dreh)
            k = _drehen(_rms(k, L["self_attn.k_norm"], eps), dreh)
            v = _rms(v, None, eps)
            q, k, v = (t.to(dtype).transpose(0, 1) for t in (q, k, v))
            a = F.scaled_dot_product_attention(q, k, v, attn_mask=kausal if L["global"] else maske_f, scale=1.0,
                                               enable_gqa=True)
            a = L["self_attn.o_proj"](a.transpose(0, 1).reshape(n, -1)).float()
            x = x + _rms(a, L["post_attention_layernorm"], eps)
            h = _rms(x, L["pre_feedforward_layernorm"], eps).to(dtype)
            m = L["mlp.down_proj"](F.gelu(L["mlp.gate_proj"](h), approximate="tanh") * L["mlp.up_proj"](h)).float()
            x = x + _rms(m, L["post_feedforward_layernorm"], eps)
            if L["skalar"] is not None:
                x = x * L["skalar"].to(device)
            aus.append(x)
        aus[-1] = _rms(x, gew["norm"].to(device), eps)
    return torch.stack(aus, -1)
