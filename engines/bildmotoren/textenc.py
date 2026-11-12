"""
textenc.py - Text-Encoder der Bild-Motoren.

Qwen3 (Z-Image, FLUX.2 klein) und Qwen2/Qwen2.5-VL (Qwen-Image, nur der Sprachteil): Sprachmodell ohne Kopf, abgegriffen werden Zustände nach bestimmten
Schichten (`hidden_states[k]` wie bei transformers). Ein eigener Nachbau für beide Dateiarten:

    safetensors   Namen wie transformers/ComfyUI; Tokenizer aus einem Ordner
    GGUF          Namen wie llama.cpp (blk.N.attn_q …); der Tokenizer steckt in der Datei

Schichten werden bei wenig Grafikspeicher nachgeschoben (gemeinsam.Bloecke).
"""

from __future__ import annotations

import json
from pathlib import Path

from .gemeinsam import Bloecke
from .gewichte import Quelle, namen

QWEN_EPS = 1e-6
QWEN3_THETA = 1_000_000.0
KOPF_QWEN2 = 128

# GGUF (llama.cpp) -> Namen im Nachbau
_GGUF_NAMEN = {"self_attn.q_proj": "attn_q", "self_attn.k_proj": "attn_k", "self_attn.v_proj": "attn_v",
               "self_attn.o_proj": "attn_output", "mlp.gate_proj": "ffn_gate", "mlp.up_proj": "ffn_up",
               "mlp.down_proj": "ffn_down", "input_layernorm": "attn_norm", "post_attention_layernorm": "ffn_norm",
               "self_attn.q_norm": "attn_q_norm", "self_attn.k_norm": "attn_k_norm"}


def qwen3_form(pfad: Path) -> dict | None:
    """Größen eines Qwen3-Text-Encoders aus dem Dateikopf (None: kein Qwen3-Sprachmodell)."""
    pfad = Path(pfad)
    if pfad.suffix.lower() == ".gguf":
        try:
            from ggufengine.gguf import GGUFFile
            with GGUFFile(str(pfad)) as gg:
                a = gg.architecture
                if a not in ("qwen3", "qwen2", "qwen2vl"):
                    return None
                return {"hidden": int(gg.get(f"{a}.embedding_length")), "schichten": int(gg.get(f"{a}.block_count")),
                        "gguf": True, "art": "qwen3" if a == "qwen3" else "qwen2",
                        "head_dim": int(gg.get(f"{a}.attention.key_length") or gg.get(f"{a}.embedding_length")
                                        // gg.get(f"{a}.attention.head_count")),
                        "theta": float(gg.get(f"{a}.rope.freq_base", QWEN3_THETA)),
                        "eps": float(gg.get(f"{a}.attention.layer_norm_rms_epsilon", QWEN_EPS))}
        except Exception:
            return None
    alle = namen(pfad)
    vors = "model." if any(k.startswith("model.layers.") for k in alle) else ""
    if f"{vors}embed_tokens.weight" not in alle or f"{vors}layers.0.self_attn.q_proj.weight" not in alle:
        return None
    art = "qwen3" if f"{vors}layers.0.self_attn.q_norm.weight" in alle else "qwen2"
    if art == "qwen2" and f"{vors}layers.0.self_attn.q_proj.bias" not in alle:
        return None                                   # Llama & Co. ohne Bias: kein Qwen
    if art == "qwen3" and any(k.startswith(("model.visual.", "visual.")) for k in alle):
        return None                                   # Qwen3-VL (Krea 2) ist ein anderer Encoder
    schichten = 1 + max(int(k[len(vors) + 7:].split(".")[0]) for k in alle if k.startswith(f"{vors}layers."))
    return {"hidden": int(alle[f"{vors}embed_tokens.weight"][1]), "schichten": schichten, "gguf": False,
            "art": art, "vorsatz": vors, "theta": QWEN3_THETA, "eps": QWEN_EPS}


# ===========================================================================
#  Tokenizer
# ===========================================================================

_QWEN_SPLIT = (r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}"
               r"| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+")


def tokenizer_da(ordner: Path) -> bool:
    return (ordner / "tokenizer.json").exists() or all(
        (ordner / d).exists() for d in ("vocab.json", "merges.txt", "tokenizer_config.json"))


def qwen_tokenizer(ordner: Path):
    """Qwen-Tokenizer aus einem Ordner: Text (mit <|im_start|> & Co.) -> ids."""
    from tokenizers import AddedToken, Regex, Tokenizer, decoders, models, normalizers, pre_tokenizers
    if (ordner / "tokenizer.json").exists():
        tok = Tokenizer.from_file(str(ordner / "tokenizer.json"))
    else:
        tok = Tokenizer(models.BPE.from_file(str(ordner / "vocab.json"), str(ordner / "merges.txt"),
                                             continuing_subword_prefix="", end_of_word_suffix="",
                                             fuse_unk=False, byte_fallback=False))
        tok.normalizer = normalizers.NFC()
        tok.pre_tokenizer = pre_tokenizers.Sequence([
            pre_tokenizers.Split(Regex(_QWEN_SPLIT), behavior="isolated", invert=False),
            pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
        tok.decoder = decoders.ByteLevel()
        cfg = ordner / "tokenizer_config.json"
        if not cfg.exists():                     # ohne sie würden <|im_start|> & Co. als Text zerlegt
            raise FileNotFoundError(f"Tokenizer unvollständig: {cfg.name} fehlt in {ordner}")
        dec = json.loads(cfg.read_text(encoding="utf-8")).get("added_tokens_decoder", {})
        tok.add_special_tokens([AddedToken(v["content"], special=True, normalized=False)
                                for _, v in sorted(dec.items(), key=lambda kv: int(kv[0]))])
    return lambda text: tok.encode(text, add_special_tokens=False).ids


_SPEZIAL = ("<|im_start|>", "<|im_end|>", "<|endoftext|>")


def gguf_tokenizer(pfad: Path):
    """Tokenizer aus einer GGUF: Text mit Spezialmarken (<|im_start|> …) -> ids."""
    import re
    from ggufengine.gguf import GGUFFile
    from ggufengine.tokenizer import Tokenizer
    with GGUFFile(str(pfad)) as gg:
        tok = Tokenizer(gg)

    def kodieren(text: str) -> list[int]:
        ids: list[int] = []
        for teil in re.split("(" + "|".join(re.escape(s) for s in _SPEZIAL) + ")", text):
            if teil in _SPEZIAL:
                ids.append(tok.special_id(teil))
            elif teil:
                ids += tok.encode(teil)
        return ids
    return kodieren


def tokenizer_fuer(text_pfad: Path, tokenizer_ordner: Path):
    """Passender Tokenizer: aus der GGUF selbst, sonst aus dem Ordner."""
    if Path(text_pfad).suffix.lower() == ".gguf":
        return gguf_tokenizer(text_pfad)
    return qwen_tokenizer(tokenizer_ordner)


# ===========================================================================
#  Qwen3
# ===========================================================================

def _rms(x, w, eps):
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w, eps=eps).to(x.dtype)


def _rope(n, head_dim, theta, device):
    import torch
    inv = 1.0 / (theta ** (torch.arange(0, head_dim, 2, device=device, dtype=torch.float32) / head_dim))
    f = torch.arange(n, device=device, dtype=torch.float32)[:, None] * inv[None]
    emb = torch.cat((f, f), -1)
    return emb.cos()[None, None], emb.sin()[None, None]


def _rot(x, cos, sin):
    import torch
    h = x.shape[-1] // 2
    xf = x.float()
    return (xf * cos + torch.cat((-xf[..., h:], xf[..., :h]), -1) * sin).to(x.dtype)


def qwen3_laden(pfad: Path, bis: int, dtype, schluss_norm: bool = False) -> dict:
    """Gewichte für die ersten `bis` Schichten (im Arbeitsspeicher), aus safetensors oder GGUF;
    mit `schluss_norm` auch die Norm hinter der letzten Schicht (Abgriff "norm")."""
    form = qwen3_form(pfad)
    if form is None:
        raise RuntimeError(f"{Path(pfad).name}: kein Qwen-Text-Encoder")
    gguf, qk_norm = form["gguf"], form["art"] == "qwen3"
    with Quelle(pfad, "" if gguf else form["vorsatz"]) as q:
        schichten = []
        for i in range(bis):
            n = (lambda x: f"blk.{i}.{_GGUF_NAMEN[x]}") if gguf else (lambda x: f"layers.{i}.{x}")  # noqa: E731
            L = {x: q.lin(n(x), dtype) for x in ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
                                                  "self_attn.o_proj", "mlp.gate_proj", "mlp.up_proj",
                                                  "mlp.down_proj")}
            for x in ("input_layernorm", "post_attention_layernorm") + (
                    ("self_attn.q_norm", "self_attn.k_norm") if qk_norm else ()):
                L[x] = q.tensor(n(x) + ".weight").float()
            schichten.append(L)
        embed = q.tensor("token_embd.weight" if gguf else "embed_tokens.weight", dtype)
        norm = q.tensor("output_norm.weight" if gguf else "norm.weight").float() if schluss_norm else None
    # Kopfbreite: GGUF-Angabe, bei Qwen3 die Breite der QK-Norm, sonst 128 (Qwen2.5-VL 3B/7B/32B/72B)
    head_dim = form.get("head_dim") or (int(schichten[0]["self_attn.q_norm"].shape[0]) if qk_norm else KOPF_QWEN2)
    return {"embed": embed, "schichten": schichten, "head_dim": head_dim, "norm": norm,
            "theta": form["theta"], "eps": form["eps"]}


def qwen3_zustaende(gew: dict, ids: list[int], device, dtype, abgriffe=None, echt: int | None = None):
    """Zustände nach den Schichten in `abgriffe` (hidden_states[k]); ohne Angabe nach der letzten geladenen.
    "norm" steht für den Zustand nach der letzten Schicht und der Schluss-Norm (hidden_states[-1]).

    `echt`: nur die ersten `echt` Token sind Text, der Rest Auffüllung – kein Token sieht die Auffüllung
    (wie transformers mit attention_mask). Gibt eine Liste (n, hidden) in Reihenfolge der Abgriffe zurück."""
    import torch
    import torch.nn.functional as F
    abgriffe = list(abgriffe or [len(gew["schichten"])])
    tiefe = max(len(gew["schichten"]) if a == "norm" else a for a in abgriffe)
    eps, D = gew["eps"], gew["head_dim"]
    with torch.no_grad():
        x = gew["embed"][torch.tensor(ids, dtype=torch.long)].to(device, dtype)[None]
        n = x.shape[1]
        cos, sin = _rope(n, D, gew["theta"], device)
        maske = None
        if echt is not None and echt < n:
            j = torch.arange(n, device=device)
            maske = (j[None, :] <= j[:, None]) & (j[None, :] < echt)
        aus = {0: x[0]} if 0 in abgriffe else {}
        for i, L in enumerate(Bloecke(gew["schichten"][:tiefe], device), 1):
            h = _rms(x, L["input_layernorm"], eps)
            q = L["self_attn.q_proj"](h).view(1, n, -1, D).transpose(1, 2)
            k = L["self_attn.k_proj"](h).view(1, n, -1, D).transpose(1, 2)
            v = L["self_attn.v_proj"](h).view(1, n, -1, D).transpose(1, 2)
            if "self_attn.q_norm" in L:
                q, k = _rms(q, L["self_attn.q_norm"], eps), _rms(k, L["self_attn.k_norm"], eps)
            q, k = _rot(q, cos, sin), _rot(k, cos, sin)
            if maske is None:
                a = F.scaled_dot_product_attention(q, k, v, is_causal=True, enable_gqa=True)
            else:
                a = F.scaled_dot_product_attention(q, k, v, attn_mask=maske, enable_gqa=True)
            x = x + L["self_attn.o_proj"](a.transpose(1, 2).reshape(1, n, -1))
            h = _rms(x, L["post_attention_layernorm"], eps)
            x = x + L["mlp.down_proj"](F.silu(L["mlp.gate_proj"](h)) * L["mlp.up_proj"](h))
            if i in abgriffe:
                aus[i] = x[0]
        if "norm" in abgriffe:
            aus["norm"] = _rms(x, gew["norm"].to(device), eps)[0]
        return [aus[k] for k in abgriffe]


def qwen3_zustand(gew: dict, ids: list[int], device, dtype):
    """(n, hidden): Zustand nach den geladenen Schichten (= hidden_states[len(schichten)])."""
    return qwen3_zustaende(gew, ids, device, dtype)[0]


# ===========================================================================
#  T5-Encoder (FLUX.1, Chroma): safetensors (transformers-Namen) oder GGUF (llama.cpp t5encoder)
# ===========================================================================

_T5_GGUF = {"q": "attn_q", "k": "attn_k", "v": "attn_v", "o": "attn_o", "wi_0": "ffn_gate", "wi_1": "ffn_up",
            "wo": "ffn_down", "norm1": "attn_norm", "norm2": "ffn_norm"}
_T5_ST = {"q": "layer.0.SelfAttention.q", "k": "layer.0.SelfAttention.k", "v": "layer.0.SelfAttention.v",
          "o": "layer.0.SelfAttention.o", "wi_0": "layer.1.DenseReluDense.wi_0", "wi_1": "layer.1.DenseReluDense.wi_1",
          "wo": "layer.1.DenseReluDense.wo", "norm1": "layer.0.layer_norm", "norm2": "layer.1.layer_norm"}
T5_EPS = 1e-6


def t5_form(pfad: Path) -> dict | None:
    """Größen eines T5-Encoders (v1.1 mit Gated-GELU) aus dem Kopf; None: kein T5."""
    pfad = Path(pfad)
    if pfad.suffix.lower() == ".gguf":
        try:
            from ggufengine.gguf import GGUFFile
            with GGUFFile(str(pfad)) as gg:
                if gg.architecture != "t5encoder":
                    return None
                a = "t5encoder"
                return {"gguf": True, "hidden": int(gg.get(f"{a}.embedding_length")),
                        "schichten": int(gg.get(f"{a}.block_count")), "koepfe": int(gg.get(f"{a}.attention.head_count")),
                        "buckets": int(gg.get(f"{a}.attention.relative_buckets_count", 32))}
        except Exception:
            return None
    alle = namen(pfad)
    rel = alle.get("encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight")
    if not rel or "encoder.block.0.layer.1.DenseReluDense.wi_0.weight" not in alle:
        return None
    return {"gguf": False, "hidden": int(alle["encoder.block.0.layer.0.SelfAttention.q.weight"][1]),
            "schichten": 1 + max(int(k.split(".")[2]) for k in alle if k.startswith("encoder.block.")),
            "koepfe": int(rel[1]), "buckets": int(rel[0])}


def t5_laden(pfad: Path, dtype) -> dict:
    f = t5_form(pfad)
    if f is None:
        raise RuntimeError(f"{Path(pfad).name}: kein T5-Encoder")
    g = f["gguf"]
    with Quelle(pfad) as q:
        n = (lambda i, x: f"enc.blk.{i}.{_T5_GGUF[x]}") if g else (lambda i, x: f"encoder.block.{i}.{_T5_ST[x]}")  # noqa: E731
        schichten = []
        for i in range(f["schichten"]):
            L = {x: q.lin(n(i, x), dtype) for x in ("q", "k", "v", "o", "wi_0", "wi_1", "wo")}
            L["norm1"], L["norm2"] = (q.tensor(n(i, x) + ".weight").float() for x in ("norm1", "norm2"))
            schichten.append(L)
        rel = q.tensor("enc.blk.0.attn_rel_b.weight" if g else
                       "encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight").float()
        embed = q.tensor("token_embd.weight" if g else ("shared.weight" if q.hat("shared.weight")
                                                        else "encoder.embed_tokens.weight"), dtype)
        norm = q.tensor("enc.output_norm.weight" if g else "encoder.final_layer_norm.weight").float()
    return {"schichten": schichten, "rel": rel, "embed": embed, "norm": norm, "koepfe": f["koepfe"]}


def _t5_bucket(rel, buckets: int, max_distance: int = 128):
    """Relative Position -> Bucket (bidirektional): nah genau, fern logarithmisch."""
    import math
    import torch
    n = buckets // 2
    out = (rel > 0).long() * n
    rel = rel.abs()
    exakt = n // 2
    gross = exakt + (torch.log(rel.float().clamp(min=1) / exakt) / math.log(max_distance / exakt)
                     * (n - exakt)).long()
    return out + torch.where(rel < exakt, rel, torch.minimum(gross, torch.full_like(gross, n - 1)))


def t5_zustand(gew: dict, ids: list[int], device, dtype, echt: int | None = None):
    """(n, hidden) nach der Schluss-Norm. `echt`: Auffüllung dahinter wird als Schlüssel ausgeblendet."""
    import torch
    import torch.nn.functional as F
    with torch.no_grad():
        x = gew["embed"][torch.tensor(ids, dtype=torch.long)].to(device).float()
        n, H = x.shape[0], gew["koepfe"]
        pos = torch.arange(n, device=device)
        bias = gew["rel"].to(device)[_t5_bucket(pos[None, :] - pos[:, None], gew["rel"].shape[0])].permute(2, 0, 1)
        if echt is not None and echt < n:
            bias = bias.masked_fill(pos[None, None, :] >= echt, float("-inf"))
        for L in Bloecke(gew["schichten"], device):
            h = _rms(x, L["norm1"], T5_EPS).to(dtype)
            q, k, v = (L[c](h).view(n, H, -1).transpose(0, 1) for c in "qkv")
            p = torch.matmul(q.float(), k.float().transpose(-1, -2)) + bias        # T5: ohne 1/sqrt(d)
            a = torch.matmul(F.softmax(p, -1).to(v.dtype), v).transpose(0, 1).reshape(n, -1)
            x = x + L["o"](a).float()
            h = _rms(x, L["norm2"], T5_EPS).to(dtype)
            x = x + L["wo"](F.gelu(L["wi_0"](h), approximate="tanh") * L["wi_1"](h)).float()
        return _rms(x, gew["norm"].to(device), T5_EPS).to(dtype)


def t5_tokenizer(ordner: Path, laenge: int):
    """T5-Tokenizer aus tokenizer.json: Text -> ids mit </s>, auf `laenge` mit 0 aufgefüllt. Gibt (ids, echt) zurück."""
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(ordner / "tokenizer.json"))

    def kodieren(text: str):
        ids = tok.encode(text).ids[:laenge]
        if not ids or ids[-1] != 1:
            ids = ids[:laenge - 1] + [1]
        return ids + [0] * (laenge - len(ids)), len(ids)
    return kodieren


# ===========================================================================
#  CLIP-L (FLUX.1): gepoolter Zustand am ersten Ende-Token
# ===========================================================================

CLIP_LAENGE = 77
CLIP_KOPF = 64                                  # Breite je Kopf bei CLIP-L und CLIP-G
CLIP_BOS, CLIP_EOS = 49406, 49407


def _clip_vorsatz(alle) -> str | None:
    for v in ("text_model.", ""):
        if v + "embeddings.token_embedding.weight" in alle and v + "final_layer_norm.weight" in alle:
            return v
    return None


def ist_clip_l(pfad: Path) -> bool:
    alle = namen(pfad)
    v = _clip_vorsatz(alle)
    return v is not None and int(alle[v + "embeddings.token_embedding.weight"][1]) == 768


def clip_laden(pfad: Path, dtype) -> dict:
    with Quelle(pfad) as q:
        v = _clip_vorsatz(set(q.alle()))
        if v is None:
            raise RuntimeError(f"{Path(pfad).name}: kein CLIP-Text-Encoder")
        q.vorsatz = v
        n = 1 + max(int(k.split(".")[2]) for k in q.alle() if k.startswith("encoder.layers."))
        schichten = []
        for i in range(n):
            p = f"encoder.layers.{i}."
            L = {x: q.lin(p + x, dtype) for x in ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
                                                  "self_attn.out_proj", "mlp.fc1", "mlp.fc2")}
            for x in ("layer_norm1", "layer_norm2"):
                L[x] = (q.tensor(p + x + ".weight").float(), q.tensor(p + x + ".bias").float())
            schichten.append(L)
        tok = q.tensor("embeddings.token_embedding.weight").float()
        return {"schichten": schichten, "tok": tok, "koepfe": tok.shape[1] // CLIP_KOPF,
                "pos": q.tensor("embeddings.position_embedding.weight").float(),
                "norm": (q.tensor("final_layer_norm.weight").float(), q.tensor("final_layer_norm.bias").float())}


def clip_gepoolt(gew: dict, ids: list[int], device, dtype, koepfe: int | None = None):
    """(768,): Zustand nach der Schluss-Norm am ersten Ende-Token (wie CLIPTextModel.pooler_output)."""
    import torch
    import torch.nn.functional as F
    ln = lambda x, wb: F.layer_norm(x, (x.shape[-1],), wb[0].to(device), wb[1].to(device), 1e-5)   # noqa: E731
    koepfe = koepfe or gew["koepfe"]
    with torch.no_grad():
        t = torch.tensor(ids, dtype=torch.long)
        n = len(ids)
        x = (gew["tok"][t] + gew["pos"][:n]).to(device)[None]
        for L in Bloecke(gew["schichten"], device):
            h = ln(x, L["layer_norm1"]).to(dtype)
            q, k, v = (L[f"self_attn.{c}_proj"](h).view(1, n, koepfe, -1).transpose(1, 2) for c in "qkv")
            a = F.scaled_dot_product_attention(q, k, v, is_causal=True).transpose(1, 2).reshape(1, n, -1)
            x = x + L["self_attn.out_proj"](a).float()
            h = L["mlp.fc1"](ln(x, L["layer_norm2"]).to(dtype)).float()
            x = x + L["mlp.fc2"]((h * torch.sigmoid(1.702 * h)).to(dtype)).float()
        x = ln(x, gew["norm"])
        return x[0, int(t.argmax())].to(dtype)


_CLIP_SPLIT = (r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+")


def clip_tokenizer(ordner: Path):
    """CLIP-BPE aus vocab.json + merges.txt (oder tokenizer.json): Text -> 77 ids (Anfang, Text, Ende, aufgefüllt)."""
    from tokenizers import Regex, Tokenizer, decoders, models, normalizers, pre_tokenizers
    if (ordner / "tokenizer.json").exists():
        tok = Tokenizer.from_file(str(ordner / "tokenizer.json"))
        tok.no_padding()
        tok.no_truncation()
        roh = lambda s: [i for i in tok.encode(s).ids if i not in (CLIP_BOS, CLIP_EOS)]    # noqa: E731
    else:
        tok = Tokenizer(models.BPE.from_file(str(ordner / "vocab.json"), str(ordner / "merges.txt"),
                                             continuing_subword_prefix="", end_of_word_suffix="</w>",
                                             fuse_unk=False, unk_token="<|endoftext|>"))
        tok.normalizer = normalizers.Sequence([normalizers.NFC(), normalizers.Replace(Regex(r"\s+"), " "),
                                               normalizers.Lowercase()])
        tok.pre_tokenizer = pre_tokenizers.Sequence([
            pre_tokenizers.Split(Regex(_CLIP_SPLIT), behavior="removed", invert=True),
            pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
        tok.decoder = decoders.ByteLevel()
        roh = lambda s: tok.encode(s.strip()).ids          # noqa: E731

    def kodieren(text: str) -> list[int]:
        ids = [CLIP_BOS] + roh(text)[:CLIP_LAENGE - 2] + [CLIP_EOS]
        return ids + [CLIP_EOS] * (CLIP_LAENGE - len(ids))
    return kodieren
