"""Testhilfe: winzige transformers-Modelle als GGUF schreiben (ohne Download, ohne llama.cpp).

Die Umwandlung folgt den Regeln der üblichen GGUF-Konverter: Tensor-Namen, Q/K-Umsortierung
für RoPE-Paare (llama, granite), Gemma-Norm-Gewichte + 1, Experten gestapelt.
"""

from __future__ import annotations

import struct

import torch

from ggufengine import gguf as GG

ALIGN = 32


def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def _wert(v) -> bytes:
    if isinstance(v, bool):
        return struct.pack("<I?", GG.T_BOOL, v)
    if isinstance(v, int):
        return struct.pack("<Ii", GG.T_INT32, v)
    if isinstance(v, float):
        return struct.pack("<If", GG.T_FLOAT32, v)
    if isinstance(v, str):
        return struct.pack("<I", GG.T_STRING) + _s(v)
    if isinstance(v, list):
        if all(isinstance(x, bool) for x in v):
            return struct.pack("<IIQ", GG.T_ARRAY, GG.T_BOOL, len(v)) + b"".join(struct.pack("<?", x) for x in v)
        if all(isinstance(x, int) for x in v):
            return struct.pack("<IIQ", GG.T_ARRAY, GG.T_INT32, len(v)) + b"".join(struct.pack("<i", x) for x in v)
        return struct.pack("<IIQ", GG.T_ARRAY, GG.T_FLOAT32, len(v)) + b"".join(struct.pack("<f", x) for x in v)
    raise TypeError(type(v))


def schreibe_gguf(pfad, meta: dict, tensoren: dict) -> None:
    """meta: Schlüssel -> Wert; tensoren: Name -> torch.Tensor (als F32 gespeichert) oder
    (GGML-Typ, Form, Rohbytes) für schon gepackte Blöcke."""
    kopf = b"GGUF" + struct.pack("<IQQ", 3, len(tensoren), len(meta))
    for k, v in meta.items():
        kopf += _s(k) + _wert(v)
    daten, off = [], 0
    for name, t in tensoren.items():
        if isinstance(t, tuple):
            typ, form, roh = t
        else:
            typ, form, roh = 0, t.shape, t.detach().float().contiguous().numpy().tobytes()
        dims = list(reversed(form))
        kopf += _s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        kopf += struct.pack("<IQ", typ, off)
        daten.append(roh + b"\0" * ((-len(roh)) % ALIGN))
        off += len(daten[-1])
    kopf += b"\0" * ((-len(kopf)) % ALIGN)
    with open(pfad, "wb") as f:
        f.write(kopf)
        for d in daten:
            f.write(d)


def _permute(w: torch.Tensor, n_head: int) -> torch.Tensor:
    """Q/K-Zeilen für RoPE-Paare umsortieren (wie die Konverter für llama)."""
    return w.reshape(n_head, 2, w.shape[0] // n_head // 2, *w.shape[1:]).swapaxes(1, 2).reshape(w.shape)


def _deepseek2(model) -> tuple[dict, dict]:
    """DeepSeek V2/V3 (transformers) -> GGUF-Schema deepseek2 wie im llama.cpp-Konverter,
    MLA mit attn_kv_b (die Engine teilt es selbst)."""
    c = model.config
    sd = model.state_dict()
    a = lambda s: f"deepseek2.{s}"
    v3 = type(model).__name__.startswith("DeepseekV3")
    meta = {"general.architecture": "deepseek2", a("block_count"): c.num_hidden_layers,
            a("context_length"): c.max_position_embeddings, a("embedding_length"): c.hidden_size,
            a("feed_forward_length"): c.intermediate_size, a("attention.head_count"): c.num_attention_heads,
            a("attention.head_count_kv"): c.num_attention_heads,
            a("attention.key_length"): c.qk_nope_head_dim + c.qk_rope_head_dim,
            a("attention.value_length"): c.v_head_dim, a("attention.kv_lora_rank"): c.kv_lora_rank,
            a("attention.layer_norm_rms_epsilon"): float(c.rms_norm_eps),
            a("rope.dimension_count"): c.qk_rope_head_dim, a("rope.freq_base"): float(c.rope_theta),
            a("leading_dense_block_count"): c.first_k_dense_replace,
            a("expert_count"): c.n_routed_experts, a("expert_used_count"): c.num_experts_per_tok,
            a("expert_shared_count"): c.n_shared_experts, a("expert_feed_forward_length"): c.moe_intermediate_size,
            a("expert_weights_scale"): float(c.routed_scaling_factor),
            a("expert_weights_norm"): bool(v3 and c.norm_topk_prob), a("expert_gating_func"): 2 if v3 else 1,
            a("expert_group_count"): c.n_group or 1, a("expert_group_used_count"): c.topk_group or 1}
    if c.q_lora_rank:
        meta[a("attention.q_lora_rank")] = c.q_lora_rank
    rs = c.rope_scaling or {}
    if rs.get("rope_type", rs.get("type")) == "yarn":
        meta[a("rope.scaling.type")] = "yarn"
        meta[a("rope.scaling.factor")] = float(rs["factor"])
        meta[a("rope.scaling.original_context_length")] = int(rs["original_max_position_embeddings"])
        meta[a("rope.scaling.yarn_log_multiplier")] = 0.1 * float(rs.get("mscale_all_dim", 0))
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "output_norm.weight": sd["model.norm.weight"],
         "output.weight": sd["lm_head.weight"]}
    for i in range(c.num_hidden_layers):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        at = p + "self_attn."
        t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
        t[b + "ffn_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
        if at + "q_proj.weight" in sd:
            t[b + "attn_q.weight"] = sd[at + "q_proj.weight"]
        else:
            t[b + "attn_q_a.weight"] = sd[at + "q_a_proj.weight"]
            t[b + "attn_q_a_norm.weight"] = sd[at + "q_a_layernorm.weight"]
            t[b + "attn_q_b.weight"] = sd[at + "q_b_proj.weight"]
        t[b + "attn_kv_a_mqa.weight"] = sd[at + "kv_a_proj_with_mqa.weight"]
        t[b + "attn_kv_a_norm.weight"] = sd[at + "kv_a_layernorm.weight"]
        t[b + "attn_kv_b.weight"] = sd[at + "kv_b_proj.weight"]
        t[b + "attn_output.weight"] = sd[at + "o_proj.weight"]
        m = p + "mlp."
        if m + "gate.weight" not in sd:
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}.weight"] = sd[m + f"{x}_proj.weight"]
            continue
        t[b + "ffn_gate_inp.weight"] = sd[m + "gate.weight"]
        if m + "gate.e_score_correction_bias" in sd:
            t[b + "exp_probs_b.bias"] = sd[m + "gate.e_score_correction_bias"]
        for x in ("gate", "up", "down"):
            t[b + f"ffn_{x}_exps.weight"] = torch.stack(
                [sd[m + f"experts.{e}.{x}_proj.weight"] for e in range(c.n_routed_experts)])
            if m + f"shared_experts.{x}_proj.weight" in sd:
                t[b + f"ffn_{x}_shexp.weight"] = sd[m + f"shared_experts.{x}_proj.weight"]
    return meta, t


def umwandeln(model, arch: str) -> tuple[dict, dict]:
    """transformers-Modell -> (Metadaten, Tensoren) im GGUF-Schema von `arch`."""
    if arch == "deepseek2":
        return _deepseek2(model)
    c = model.config
    sd = {k: v for k, v in model.state_dict().items()}
    H, Hkv = c.num_attention_heads, getattr(c, "num_key_value_heads", c.num_attention_heads)
    D = getattr(c, "head_dim", None) or c.hidden_size // H
    gemma = arch.startswith("gemma")
    plus1 = (lambda t: t + 1) if gemma else (lambda t: t)
    paare = arch in ("llama", "granite")
    a = lambda s: f"{arch}.{s}"
    meta = {"general.architecture": arch,
            a("block_count"): c.num_hidden_layers, a("context_length"): c.max_position_embeddings,
            a("embedding_length"): c.hidden_size, a("feed_forward_length"): c.intermediate_size,
            a("attention.head_count"): H, a("attention.head_count_kv"): Hkv,
            a("attention.key_length"): D, a("attention.value_length"): D,
            a("rope.freq_base"): float(getattr(c, "rope_theta", 10000.0))}
    if arch in ("cohere2", "command-r"):                # LayerNorm (ohne Bias)
        meta[a("attention.layer_norm_epsilon")] = float(c.layer_norm_eps)
        meta[a("logit_scale")] = float(c.logit_scale)
    else:
        meta[a("attention.layer_norm_rms_epsilon")] = float(c.rms_norm_eps)
    if arch in ("olmo3", "exaone4", "cohere2") and getattr(c, "sliding_window", None):
        meta[a("attention.sliding_window")] = int(c.sliding_window)
    rs = getattr(c, "rope_scaling", None) or {}
    if arch != "gpt-oss" and rs.get("rope_type", rs.get("type")) == "yarn":
        meta[a("rope.scaling.type")] = "yarn"
        meta[a("rope.scaling.factor")] = float(rs["factor"])
        meta[a("rope.scaling.original_context_length")] = int(rs["original_max_position_embeddings"])
    rot = int(D * getattr(c, "partial_rotary_factor", 1.0))
    meta[a("rope.dimension_count")] = rot
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "output_norm.weight": plus1(sd["model.norm.weight"])}
    if "lm_head.weight" in sd and not getattr(c, "tie_word_embeddings", False):
        t["output.weight"] = sd["lm_head.weight"]
    for i in range(c.num_hidden_layers):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        at = p + "self_attn."
        if at + "qkv_proj.weight" in sd:
            t[b + "attn_qkv.weight"] = sd[at + "qkv_proj.weight"]
        else:
            q, k = sd[at + "q_proj.weight"], sd[at + "k_proj.weight"]
            t[b + "attn_q.weight"] = _permute(q, H) if paare else q
            t[b + "attn_k.weight"] = _permute(k, Hkv) if paare else k
            t[b + "attn_v.weight"] = sd[at + "v_proj.weight"]
            for x in "qkv":
                if at + f"{x}_proj.bias" in sd:
                    t[b + f"attn_{x}.bias"] = sd[at + f"{x}_proj.bias"]
        t[b + "attn_output.weight"] = sd[at + "o_proj.weight"]
        if at + "o_proj.bias" in sd:
            t[b + "attn_output.bias"] = sd[at + "o_proj.bias"]
        if at + "sinks" in sd:                                      # gpt-oss
            t[b + "attn_sinks.weight"] = sd[at + "sinks"]
        for x in "qk":
            if at + f"{x}_norm.weight" in sd:
                t[b + f"attn_{x}_norm.weight"] = plus1(sd[at + f"{x}_norm.weight"])
        if arch in ("cohere2", "command-r"):           # Attention und FFN parallel auf einer Norm
            t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
        elif arch == "glm4":                           # Sandwich: Nach-Normen um Attention und MLP
            t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
            t[b + "post_attention_norm.weight"] = sd[p + "post_self_attn_layernorm.weight"]
            t[b + "ffn_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
            t[b + "post_ffw_norm.weight"] = sd[p + "post_mlp_layernorm.weight"]
        elif arch == "gpt-oss":                        # llama.cpp: Norm vor dem FFN heißt post_attention_norm
            t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
            t[b + "post_attention_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
        elif arch in ("olmo2", "olmo3", "exaone4"):    # nur Nach-Normen
            t[b + "post_attention_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
            t[b + "post_ffw_norm.weight"] = sd[p + "post_feedforward_layernorm.weight"]
        elif p + "pre_feedforward_layernorm.weight" in sd:          # Gemma 2/3: Sandwich
            t[b + "attn_norm.weight"] = plus1(sd[p + "input_layernorm.weight"])
            t[b + "post_attention_norm.weight"] = plus1(sd[p + "post_attention_layernorm.weight"])
            t[b + "ffn_norm.weight"] = plus1(sd[p + "pre_feedforward_layernorm.weight"])
            t[b + "post_ffw_norm.weight"] = plus1(sd[p + "post_feedforward_layernorm.weight"])
        else:
            t[b + "attn_norm.weight"] = plus1(sd[p + "input_layernorm.weight"])
            t[b + "ffn_norm.weight"] = plus1(sd[p + "post_attention_layernorm.weight"])
        m = p + "mlp."
        if m + "router.weight" in sd:                               # gpt-oss: gate/up verschränkt, mit Bias
            gu, gub = sd[m + "experts.gate_up_proj"], sd[m + "experts.gate_up_proj_bias"]   # (E,H,2F), (E,2F)
            t[b + "ffn_gate_inp.weight"], t[b + "ffn_gate_inp.bias"] = sd[m + "router.weight"], sd[m + "router.bias"]
            t[b + "ffn_gate_exps.weight"] = gu[..., 0::2].transpose(1, 2).contiguous()
            t[b + "ffn_up_exps.weight"] = gu[..., 1::2].transpose(1, 2).contiguous()
            t[b + "ffn_gate_exps.bias"], t[b + "ffn_up_exps.bias"] = gub[:, 0::2], gub[:, 1::2]
            t[b + "ffn_down_exps.weight"] = sd[m + "experts.down_proj"].transpose(1, 2).contiguous()
            t[b + "ffn_down_exps.bias"] = sd[m + "experts.down_proj_bias"]
            meta[a("expert_count")] = c.num_local_experts
            meta[a("expert_used_count")] = c.num_experts_per_tok
            meta[a("attention.sliding_window")] = c.sliding_window
            rs = c.rope_scaling or {}
            if rs.get("rope_type", rs.get("type")) == "yarn":
                meta[a("rope.scaling.type")] = "yarn"
                meta[a("rope.scaling.factor")] = float(rs["factor"])
                meta[a("rope.scaling.original_context_length")] = int(rs["original_max_position_embeddings"])
        elif m + "gate_up_proj.weight" in sd:                       # Phi-3
            t[b + "ffn_up.weight"] = sd[m + "gate_up_proj.weight"]
            t[b + "ffn_down.weight"] = sd[m + "down_proj.weight"]
        elif m + "gate_proj.weight" in sd:
            t[b + "ffn_gate.weight"] = sd[m + "gate_proj.weight"]
            t[b + "ffn_up.weight"] = sd[m + "up_proj.weight"]
            t[b + "ffn_down.weight"] = sd[m + "down_proj.weight"]
        else:                                                       # MoE
            moe = p + ("block_sparse_moe." if p + "block_sparse_moe.gate.weight" in sd else "mlp.")
            n_exp = next(getattr(c, x) for x in ("num_local_experts", "num_experts", "moe_num_experts", "n_routed_experts")
                         if hasattr(c, x))
            t[b + "ffn_gate_inp.weight"] = sd[moe + "gate.weight"]
            namen = ("w1", "w3", "w2") if "block_sparse_moe" in moe else ("gate_proj", "up_proj", "down_proj")
            for ziel, quelle in zip(("gate", "up", "down"), namen):
                t[b + f"ffn_{ziel}_exps.weight"] = torch.stack(
                    [sd[moe + f"experts.{e}.{quelle}.weight"] for e in range(n_exp)])
            if moe + "gate.e_score_correction_bias" in sd:                 # GLM-4.5: Sigmoid mit Korrektur
                t[b + "exp_probs_b.bias"] = sd[moe + "gate.e_score_correction_bias"]
                meta[a("expert_gating_func")] = 2
                meta[a("expert_group_count")] = c.n_group
                meta[a("expert_group_used_count")] = c.topk_group
                meta[a("expert_weights_scale")] = float(c.routed_scaling_factor)
                meta[a("expert_weights_norm")] = bool(c.norm_topk_prob)
            if moe + "moe_statics.e_score_correction_bias" in sd:          # ERNIE 4.5: Auswahl-Korrektur
                t[b + "exp_probs_b.bias"] = sd[moe + "moe_statics.e_score_correction_bias"].reshape(-1)
            if moe + "shared_experts.up_proj.weight" in sd:                # ERNIE 4.5: ohne Gate
                for x in ("gate", "up", "down"):
                    t[b + f"ffn_{x}_shexp.weight"] = sd[moe + f"shared_experts.{x}_proj.weight"]
            if moe + "shared_expert.up_proj.weight" in sd:
                for x in ("gate", "up", "down"):
                    t[b + f"ffn_{x}_shexp.weight"] = sd[moe + f"shared_expert.{x}_proj.weight"]
                t[b + "ffn_gate_inp_shexp.weight"] = sd[moe + "shared_expert_gate.weight"]
            meta[a("expert_count")] = n_exp
            meta[a("expert_used_count")] = getattr(c, "num_experts_per_tok", None) or c.moe_k
    return meta, t
