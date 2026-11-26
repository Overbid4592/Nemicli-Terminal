"""
Ton-Teil eines Gemma-4-mmproj (clip.audio.projector_type "gemma4a") laden -> ton.Hoeren.

Die Datei folgt den llama.cpp-Namen; `gguf_name` bildet die Namen von transformers' Gemma4AudioModel darauf ab.
`per_dim_scale` steht roh in der Datei (gerechnet wird q · head_dim^-0.5 / ln 2 · softplus(w) wie in transformers).
Geprüft an Sprache-zu-Text-Suche (16 gesprochene Sätze gegen ihre Texte): 16/16 auf Platz 1, Abstand zu den
anderen Sätzen 0,319; per_dim_scale als fertige Skala 0,304; conv_norm/norm_conv vertauscht 14/16.
Werte, die nicht in der Datei stehen, kommen aus Gemma4AudioConfig (gleich bei Gemma 4 und EmbeddingGemma 2).
"""
from __future__ import annotations

import torch

from .gguf import GGUFError, GGUFFile
from .models.common import WeightLoader
from .ton import Hoeren

# transformers-Name je Schicht -> llama.cpp-Name (ohne „a.blk.{i}.“)
_SCHICHT = {
    "feed_forward1.ffw_layer_1": "ffn_up", "feed_forward1.ffw_layer_2": "ffn_down",
    "feed_forward1.pre_layer_norm": "ffn_norm", "feed_forward1.post_layer_norm": "ffn_post_norm",
    "feed_forward2.ffw_layer_1": "ffn_up_1", "feed_forward2.ffw_layer_2": "ffn_down_1",
    "feed_forward2.pre_layer_norm": "ffn_norm_1", "feed_forward2.post_layer_norm": "ffn_post_norm_1",
    "self_attn.q_proj": "attn_q", "self_attn.k_proj": "attn_k", "self_attn.v_proj": "attn_v",
    "self_attn.post": "attn_out", "self_attn.relative_k_proj": "attn_k_rel", "self_attn.per_dim_scale": "per_dim_scale",
    "lconv1d.linear_start": "conv_pw1", "lconv1d.linear_end": "conv_pw2", "lconv1d.depthwise_conv1d": "conv_dw",
    # llama.cpp: conv_norm vor der Faltung, norm_conv nach ihr
    "lconv1d.pre_layer_norm": "conv_norm", "lconv1d.conv_norm": "norm_conv",
    "norm_pre_attn": "attn_pre_norm", "norm_post_attn": "attn_post_norm", "norm_out": "ln2",
}
_SONST = {
    "subsample_conv_projection.layer0.conv.weight": "a.conv1d.0.weight",
    "subsample_conv_projection.layer0.norm.weight": "a.conv1d.0.norm.weight",
    "subsample_conv_projection.layer1.conv.weight": "a.conv1d.1.weight",
    "subsample_conv_projection.layer1.norm.weight": "a.conv1d.1.norm.weight",
    "subsample_conv_projection.input_proj_linear.weight": "a.input_projection.weight",
    "output_proj.weight": "a.pre_encode.out.weight",
    "output_proj.bias": "a.pre_encode.out.bias",
    "embed_audio.embedding_projection.weight": "mm.a.input_projection.weight",
}
# Gemma4AudioConfig (transformers 5.23, config.json von google/embeddinggemma-2)
_VORGABEN = {"attention_chunk_size": 12, "attention_context_left": 13, "attention_context_right": 0,
             "attention_logit_cap": 50.0, "attention_invalid_logits_value": -1e9, "residual_weight": 0.5,
             "conv_kernel_size": 5}


def gguf_name(name: str) -> str:
    """transformers-Name (ohne „audio_tower.“) -> Name in der mmproj-Datei."""
    if name in _SONST:
        return _SONST[name]
    teile = name.split(".")
    if teile[0] != "layers":
        raise KeyError(name)
    i, rest = teile[1], ".".join(teile[2:])
    for hf, ll in sorted(_SCHICHT.items(), key=lambda kv: -len(kv[0])):
        if rest == hf or rest.startswith(hf + "."):
            ende = rest[len(hf):].lstrip(".")
            ende = {"linear.weight": "weight", "": "weight"}.get(ende, ende)
            return f"a.blk.{i}.{ll}.{ende}"
    raise KeyError(name)


_HAT_TON: dict = {}                      # (Pfad, Größe, Zeit) -> bool


def hat_ton(pfad: str) -> bool:
    """Hat der mmproj einen Gemma-4-Audio-Encoder? Liest nur den Kopf, je Dateistand einmal."""
    try:
        import os
        st = os.stat(pfad)
        schluessel = (str(pfad), st.st_size, st.st_mtime_ns)
    except OSError:
        return False
    if schluessel not in _HAT_TON:
        try:
            g = GGUFFile(pfad)
        except Exception:
            return False
        try:
            _HAT_TON[schluessel] = bool(g.get("clip.has_audio_encoder")) and \
                g.get("clip.audio.projector_type") == "gemma4a"
        finally:
            g.close()
    return _HAT_TON[schluessel]


def laden(pfad: str, device) -> Hoeren:
    g = GGUFFile(pfad)
    try:
        if not bool(g.get("clip.has_audio_encoder")) or g.get("clip.audio.projector_type") != "gemma4a":
            raise GGUFError("no Gemma 4 audio encoder in this mmproj")
        cfg = dict(_VORGABEN,
                   num_hidden_layers=int(g.require("clip.audio.block_count")),
                   hidden_size=int(g.require("clip.audio.embedding_length")),
                   num_attention_heads=int(g.require("clip.audio.attention.head_count")),
                   rms_norm_eps=float(g.get("clip.audio.attention.layer_norm_epsilon", 1e-6)),
                   use_clipped_linears="a.blk.0.attn_q.input_min" in g.tensors)
        if int(g.require("clip.audio.num_mel_bins")) != 128:
            raise GGUFError("only 128 mel bins are supported")
        ld = WeightLoader(g, torch.device("cpu"), torch.float32, quant=False)

        def holen(name: str) -> torch.Tensor:
            x = ld.get(gguf_name(name), torch.float32).float().clone()     # nicht in die Datei zeigen (close)
            if name.endswith("depthwise_conv1d.weight") and x.dim() == 2:
                x = x[:, None, :]                  # (H, K) -> (H, 1, K)
            return x

        return Hoeren(holen, cfg, device)
    finally:
        g.close()
