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
        if v and all(isinstance(x, str) for x in v):
            return struct.pack("<IIQ", GG.T_ARRAY, GG.T_STRING, len(v)) + b"".join(_s(x) for x in v)
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


def _rope_global(c) -> dict:
    """transformers 5: `rope_parameters`, bei Modellen mit Fenster-Schichten je Schichtart
    verschachtelt – dann gelten die Werte der globalen Schichten (full_attention)."""
    rp = getattr(c, "rope_parameters", None) or {}
    return rp.get("full_attention", rp) if isinstance(rp.get("full_attention"), dict) else rp


def rope_theta(c, standard: float = 10000.0) -> float:
    """RoPE-Basis: transformers 4 `config.rope_theta`, 5 `rope_parameters["rope_theta"]`."""
    v = getattr(c, "rope_theta", None)
    if v is None:
        v = _rope_global(c).get("rope_theta")
    return float(standard if v is None else v)


def rope_scaling(c) -> dict:
    """RoPE-Skalierung: transformers 4 `rope_scaling`, 5 `rope_parameters` (ohne Skalierung: {})."""
    rs = getattr(c, "rope_scaling", None)
    if isinstance(rs, dict) and isinstance(rs.get("full_attention"), dict):
        rs = None                                   # v5 spiegelt rope_parameters hierher
    if rs:
        return rs
    rp = _rope_global(c)
    return rp if rp.get("rope_type", rp.get("type", "default")) != "default" else {}


def experten(sd: dict, praefix: str, n: int, namen=("gate_proj", "up_proj", "down_proj")) -> dict:
    """{"gate", "up", "down": (E, ·, ·)} – transformers 4 einzeln je Experte, 5 zusammengelegt
    (experts.gate_up_proj = (E, 2F, H) mit erst Gate, dann Up; experts.down_proj = (E, H, F))."""
    if praefix + "experts.gate_up_proj" in sd:
        gu = sd[praefix + "experts.gate_up_proj"]
        f = gu.shape[1] // 2
        return {"gate": gu[:, :f].contiguous(), "up": gu[:, f:].contiguous(),
                "down": sd[praefix + "experts.down_proj"].contiguous()}
    return {ziel: torch.stack([sd[praefix + f"experts.{e}.{quelle}.weight"] for e in range(n)])
            for ziel, quelle in zip(("gate", "up", "down"), namen)}


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
            a("rope.dimension_count"): c.qk_rope_head_dim, a("rope.freq_base"): rope_theta(c),
            a("leading_dense_block_count"): c.first_k_dense_replace,
            a("expert_count"): c.n_routed_experts, a("expert_used_count"): c.num_experts_per_tok,
            a("expert_shared_count"): c.n_shared_experts, a("expert_feed_forward_length"): c.moe_intermediate_size,
            a("expert_weights_scale"): float(c.routed_scaling_factor),
            a("expert_weights_norm"): bool(v3 and c.norm_topk_prob), a("expert_gating_func"): 2 if v3 else 1,
            a("expert_group_count"): c.n_group or 1, a("expert_group_used_count"): c.topk_group or 1}
    if c.q_lora_rank:
        meta[a("attention.q_lora_rank")] = c.q_lora_rank
    rs = rope_scaling(c)
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
        for x, w in experten(sd, m, c.n_routed_experts).items():
            t[b + f"ffn_{x}_exps.weight"] = w
        for x in ("gate", "up", "down"):
            if m + f"shared_experts.{x}_proj.weight" in sd:
                t[b + f"ffn_{x}_shexp.weight"] = sd[m + f"shared_experts.{x}_proj.weight"]
    return meta, t


def _v_kacheln(t: torch.Tensor, dim: int, nk: int, je: int, kopf: int) -> torch.Tensor:
    """V-Köpfe von „gruppiert nach k-Kopf“ auf „gekachelt“ (llama.cpp _reorder_v_heads)."""
    form = list(t.shape)
    dim = dim % len(form)
    neu = form[:dim] + [nk, je, kopf] + form[dim + 1:]
    perm = list(range(len(neu)))
    perm[dim], perm[dim + 1] = perm[dim + 1], perm[dim]
    return t.reshape(*neu).permute(*perm).contiguous().reshape(*form)


def _qwen_delta(model, arch: str, sections=(1, 1, 0, 0)) -> tuple[dict, dict]:
    """Qwen3.5 / Qwen3.5-MoE / Qwen3-Next (transformers) -> GGUF wie conversion/qwen.py von llama.cpp:
    -exp(A_log), Normen +1 (außer der Gated-Norm), qwen35*: V-Köpfe gekachelt, qwen3next: qkvz/ba
    je k-Kopf aufgeteilt (V gruppiert). `sections`: rope.dimension_sections (klein für Mini-Modelle)."""
    c = model.config
    sd = model.state_dict()
    a = lambda s: f"{arch}.{s}"
    H, Hkv, D = c.num_attention_heads, c.num_key_value_heads, c.head_dim
    nk, nv = c.linear_num_key_heads, c.linear_num_value_heads
    dk, dv, je = c.linear_key_head_dim, c.linear_value_head_dim, c.linear_num_value_heads // c.linear_num_key_heads
    teil = float(getattr(c, "partial_rotary_factor", None) or (getattr(c, "rope_parameters", None) or {}).get(
        "partial_rotary_factor", 0.25))
    meta = {"general.architecture": arch, a("block_count"): c.num_hidden_layers,
            a("context_length"): c.max_position_embeddings, a("embedding_length"): c.hidden_size,
            a("feed_forward_length"): getattr(c, "intermediate_size", 0) or 0,
            a("attention.head_count"): H, a("attention.head_count_kv"): Hkv,
            a("attention.key_length"): D, a("attention.value_length"): D,
            a("attention.layer_norm_rms_epsilon"): float(c.rms_norm_eps), a("rope.freq_base"): rope_theta(c),
            a("rope.dimension_count"): int(D * teil), a("ssm.conv_kernel"): c.linear_conv_kernel_dim,
            a("ssm.state_size"): dk, a("ssm.group_count"): nk, a("ssm.time_step_rank"): nv,
            a("ssm.inner_size"): dv * nv, a("full_attention_interval"): 4}
    if arch != "qwen3next":
        meta[a("rope.dimension_sections")] = list(sections)
    if getattr(c, "num_experts", 0):
        meta[a("expert_count")] = c.num_experts
        meta[a("expert_used_count")] = c.num_experts_per_tok
        meta[a("expert_feed_forward_length")] = c.moe_intermediate_size
        meta[a("expert_shared_feed_forward_length")] = c.shared_expert_intermediate_size
    kachel = arch != "qwen3next" and nk != nv
    vk = (lambda t, dim, kopf: _v_kacheln(t, dim, nk, je, kopf)) if kachel else (lambda t, dim, kopf: t)
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "output_norm.weight": sd["model.norm.weight"] + 1}
    if not getattr(c, "tie_word_embeddings", False):
        t["output.weight"] = sd["lm_head.weight"]
    for i in range(c.num_hidden_layers):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"] + 1
        t[b + "post_attention_norm.weight"] = sd[p + "post_attention_layernorm.weight"] + 1
        la = p + "linear_attn."
        if la + "A_log" in sd:
            t[b + "ssm_a"] = vk(-torch.exp(sd[la + "A_log"].float()).unsqueeze(-1), 0, 1).reshape(-1)
            t[b + "ssm_dt.bias"] = vk(sd[la + "dt_bias"].unsqueeze(-1), 0, 1).reshape(-1)
            conv = sd[la + "conv1d.weight"].squeeze(1)
            t[b + "ssm_conv1d.weight"] = torch.cat([conv[:2 * nk * dk], vk(conv[2 * nk * dk:], 0, dv)])
            t[b + "ssm_norm.weight"] = sd[la + "norm.weight"]
            t[b + "ssm_out.weight"] = vk(sd[la + "out_proj.weight"], 1, dv)
            if la + "in_proj_qkvz.weight" in sd:                   # qwen3next
                w = sd[la + "in_proj_qkvz.weight"].t().reshape(c.hidden_size, nk, -1)
                q, k, v, z = torch.split(w, [dk, dk, je * dv, je * dv], dim=-1)
                flach = lambda x: x.reshape(c.hidden_size, -1)
                t[b + "attn_qkv.weight"] = torch.cat([flach(q), flach(k), flach(v)], dim=-1).t().contiguous()
                t[b + "attn_gate.weight"] = flach(z).t().contiguous()
                t[b + "ssm_ba.weight"] = sd[la + "in_proj_ba.weight"]
            else:
                qkv = sd[la + "in_proj_qkv.weight"]
                t[b + "attn_qkv.weight"] = torch.cat([qkv[:2 * nk * dk], vk(qkv[2 * nk * dk:], 0, dv)])
                t[b + "attn_gate.weight"] = vk(sd[la + "in_proj_z.weight"], 0, dv)
                t[b + "ssm_beta.weight"] = vk(sd[la + "in_proj_b.weight"], 0, 1)
                t[b + "ssm_alpha.weight"] = vk(sd[la + "in_proj_a.weight"], 0, 1)
        else:
            at = p + "self_attn."
            for x in "qkv":
                t[b + f"attn_{x}.weight"] = sd[at + f"{x}_proj.weight"]
            t[b + "attn_output.weight"] = sd[at + "o_proj.weight"]
            t[b + "attn_q_norm.weight"] = sd[at + "q_norm.weight"] + 1
            t[b + "attn_k_norm.weight"] = sd[at + "k_norm.weight"] + 1
        m = p + "mlp."
        if m + "gate.weight" in sd:
            t[b + "ffn_gate_inp.weight"] = sd[m + "gate.weight"]
            for x, w in experten(sd, m, c.num_experts).items():
                t[b + f"ffn_{x}_exps.weight"] = w
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}_shexp.weight"] = sd[m + f"shared_expert.{x}_proj.weight"]
            t[b + "ffn_gate_inp_shexp.weight"] = sd[m + "shared_expert_gate.weight"]
        else:
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}.weight"] = sd[m + f"{x}_proj.weight"]
    return meta, t


def _mamba2_tensoren(t: dict, b: str, sd: dict, m: str, ng: int, d_inner: int) -> None:
    """Mamba-2-Mixer wie conversion/mamba.py: conv gequetscht, A = -exp(A_log) und D als (H, 1),
    Norm als (G, d_inner/G)."""
    t[b + "ssm_in.weight"] = sd[m + "in_proj.weight"]
    t[b + "ssm_conv1d.weight"] = sd[m + "conv1d.weight"].squeeze(1)
    if m + "conv1d.bias" in sd:
        t[b + "ssm_conv1d.bias"] = sd[m + "conv1d.bias"]
    t[b + "ssm_dt.bias"] = sd[m + "dt_bias"]
    t[b + "ssm_a"] = (-torch.exp(sd[m + "A_log"].float())).reshape(-1, 1)
    t[b + "ssm_d"] = sd[m + "D"].reshape(-1, 1)
    if m + "norm.weight" in sd:
        t[b + "ssm_norm.weight"] = sd[m + "norm.weight"].reshape(ng, d_inner // ng)
    t[b + "ssm_out.weight"] = sd[m + "out_proj.weight"]


def _mamba_hybrid(model, arch: str) -> tuple[dict, dict]:
    """Granite 4 H / Nemotron-H / Falcon-H1 (transformers) -> GGUF wie conversion/granite.py,
    nemotron.py und falcon_h1.py (Falcon-Multiplikatoren in die Gewichte eingerechnet)."""
    c = model.config
    sd = model.state_dict()
    a = lambda s: f"{arch}.{s}"
    n = c.num_hidden_layers
    granite, falcon = arch == "granitehybrid", arch == "falcon-h1"
    nemo = arch.startswith("nemotron_h")
    if granite:
        heads, hd, ds, ng = c.mamba_n_heads, c.mamba_d_head, c.mamba_d_state, c.mamba_n_groups
        d_inner, typen = int(c.mamba_expand * c.hidden_size), c.layers_block_type
        conv = c.mamba_d_conv
    elif nemo:
        heads, hd, ds, ng = c.mamba_num_heads, c.mamba_head_dim, c.ssm_state_size, c.n_groups
        d_inner, typen, conv = heads * hd, c.layers_block_type, c.conv_kernel
    else:
        heads, hd, ds, ng = c.mamba_n_heads, c.mamba_d_head, c.mamba_d_state, c.mamba_n_groups
        d_inner, typen, conv = c.mamba_d_ssm, ["hybrid"] * n, c.mamba_d_conv
    H = c.num_attention_heads
    D = getattr(c, "head_dim", None) or c.hidden_size // H
    attn = [i for i, x in enumerate(typen) if x in ("full_attention", "attention", "hybrid")]
    meta = {"general.architecture": arch, a("block_count"): n, a("context_length"): c.max_position_embeddings,
            a("embedding_length"): c.hidden_size, a("feed_forward_length"): c.intermediate_size,
            a("attention.head_count"): H,
            a("attention.head_count_kv"): [c.num_key_value_heads if i in attn else 0 for i in range(n)],
            a("attention.key_length"): D, a("attention.value_length"): D,
            a("attention.layer_norm_rms_epsilon"): float(getattr(c, "rms_norm_eps", None) or c.layer_norm_epsilon),
            a("ssm.conv_kernel"): conv, a("ssm.state_size"): ds, a("ssm.group_count"): ng,
            a("ssm.inner_size"): d_inner, a("ssm.time_step_rank"): heads, a("rope.freq_base"): rope_theta(c),
            a("rope.dimension_count"): D}
    t = {}
    if granite:
        for schluessel, wert in (("attention.scale", c.attention_multiplier), ("embedding_scale", c.embedding_multiplier),
                                 ("residual_scale", c.residual_multiplier), ("logit_scale", c.logits_scaling)):
            meta[a(schluessel)] = float(wert)
        meta[a("rope.scaling.finetuned")] = c.position_embedding_type == "rope"
        if c.num_local_experts:
            meta[a("expert_count")] = c.num_local_experts
            meta[a("expert_used_count")] = c.num_experts_per_tok
    if nemo and arch.endswith("moe"):
        meta.update({a("expert_count"): c.n_routed_experts, a("expert_used_count"): c.num_experts_per_tok,
                     a("expert_weights_norm"): bool(c.norm_topk_prob),
                     a("expert_weights_scale"): float(c.routed_scaling_factor), a("expert_group_count"): c.n_group})
    mal = {}
    if falcon:                                            # Multiplikatoren wie conversion/falcon_h1.py
        mal = {"embed": c.embedding_multiplier, "lm_head": c.lm_head_multiplier}
    emb = sd["model.embeddings.weight" if nemo else "model.embed_tokens.weight"]
    t["token_embd.weight"] = emb * mal.get("embed", 1.0)
    t["output_norm.weight"] = sd["model.norm_f.weight" if nemo else ("model.final_layernorm.weight" if falcon
                                                                    else "model.norm.weight")]
    if not getattr(c, "tie_word_embeddings", False):
        t["output.weight"] = sd["lm_head.weight"] * mal.get("lm_head", 1.0)
    for i in range(n):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        if nemo:
            m = p + "mixer."
            t[b + "attn_norm.weight"] = sd[p + "norm.weight"]
            if m + "in_proj.weight" in sd:
                _mamba2_tensoren(t, b, sd, m, ng, d_inner)
            elif m + "q_proj.weight" in sd:
                for x in "qkv":
                    t[b + f"attn_{x}.weight"] = sd[m + f"{x}_proj.weight"]
                t[b + "attn_output.weight"] = sd[m + "o_proj.weight"]
            elif m + "gate.weight" in sd:
                t[b + "ffn_gate_inp.weight"] = sd[m + "gate.weight"]
                t[b + "exp_probs_b.bias"] = sd[m + "gate.e_score_correction_bias"]
                t[b + "ffn_up_exps.weight"] = sd[m + "experts.up_proj"]
                t[b + "ffn_down_exps.weight"] = sd[m + "experts.down_proj"]
                t[b + "ffn_up_shexp.weight"] = sd[m + "shared_experts.up_proj.weight"]
                t[b + "ffn_down_shexp.weight"] = sd[m + "shared_experts.down_proj.weight"]
            else:
                t[b + "ffn_up.weight"] = sd[m + "up_proj.weight"]
                t[b + "ffn_down.weight"] = sd[m + "down_proj.weight"]
            continue
        t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
        if p + "mamba.in_proj.weight" in sd:
            _mamba2_tensoren(t, b, sd, p + "mamba.", ng, d_inner)
        at = p + "self_attn."
        if at + "q_proj.weight" in sd:
            q, k, v = (sd[at + f"{x}_proj.weight"] for x in "qkv")
            if granite:                                   # undo_permute: llama-Umsortierung für RoPE „normal“
                q, k = _permute(q, H), _permute(k, c.num_key_value_heads)
            t[b + "attn_q.weight"], t[b + "attn_k.weight"], t[b + "attn_v.weight"] = q, k, v
            t[b + "attn_output.weight"] = sd[at + "o_proj.weight"]
        if falcon:
            am, km, om = c.attention_in_multiplier, c.key_multiplier, c.attention_out_multiplier
            t[b + "attn_q.weight"] = t[b + "attn_q.weight"] * am
            t[b + "attn_k.weight"] = t[b + "attn_k.weight"] * km * am
            t[b + "attn_v.weight"] = t[b + "attn_v.weight"] * am
            t[b + "attn_output.weight"] = t[b + "attn_output.weight"] * om
            t[b + "ssm_out.weight"] = t[b + "ssm_out.weight"] * c.ssm_out_multiplier
            w = t[b + "ssm_in.weight"] * c.ssm_in_multiplier
            zm, gs = c.ssm_multipliers, ng * ds
            for teil, (von, bis) in enumerate(((0, d_inner), (d_inner, 2 * d_inner), (2 * d_inner, 2 * d_inner + gs),
                                               (2 * d_inner + gs, 2 * d_inner + 2 * gs), (2 * d_inner + 2 * gs, None))):
                w[von:bis] = w[von:bis] * zm[teil]
            t[b + "ssm_in.weight"] = w
            t[b + "ffn_norm.weight"] = sd[p + "pre_ff_layernorm.weight"]
            ff = p + "feed_forward."
            t[b + "ffn_gate.weight"] = sd[ff + "gate_proj.weight"] * c.mlp_multipliers[0]
            t[b + "ffn_up.weight"] = sd[ff + "up_proj.weight"]
            t[b + "ffn_down.weight"] = sd[ff + "down_proj.weight"] * c.mlp_multipliers[1]
            continue
        t[b + "ffn_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
        moe = p + "block_sparse_moe."
        gate, up = sd[p + "shared_mlp.input_linear.weight"].chunk(2, dim=0)
        if moe + "router.weight" in sd:
            t[b + "ffn_gate_inp.weight"] = sd[moe + "router.weight"]
            for x, w in experten(sd, moe, c.num_local_experts).items():
                t[b + f"ffn_{x}_exps.weight"] = w
            t[b + "ffn_gate_shexp.weight"], t[b + "ffn_up_shexp.weight"] = gate, up
            t[b + "ffn_down_shexp.weight"] = sd[p + "shared_mlp.output_linear.weight"]
        else:
            t[b + "ffn_gate.weight"], t[b + "ffn_up.weight"] = gate, up
            t[b + "ffn_down.weight"] = sd[p + "shared_mlp.output_linear.weight"]
    return meta, t


def _lfm2(model, arch: str) -> tuple[dict, dict]:
    """LFM2 / LFM2-MoE (transformers) -> GGUF wie conversion/lfm2.py: Faltung gequetscht,
    End-Norm als token_embd_norm, kv-Köpfe nur für Attention-Schichten, Sigmoid-Router."""
    c = model.config
    sd = model.state_dict()
    a = lambda s: f"{arch}.{s}"
    n, H = c.num_hidden_layers, c.num_attention_heads
    D = c.hidden_size // H
    meta = {"general.architecture": arch, a("block_count"): n, a("context_length"): c.max_position_embeddings,
            a("embedding_length"): c.hidden_size, a("feed_forward_length"): c.intermediate_size,
            a("attention.head_count"): H, a("attention.key_length"): D, a("attention.value_length"): D,
            a("attention.head_count_kv"): [c.num_key_value_heads if x == "full_attention" else 0 for x in c.layer_types],
            a("attention.layer_norm_rms_epsilon"): float(c.norm_eps), a("rope.freq_base"): rope_theta(c),
            a("rope.dimension_count"): D, a("shortconv.l_cache"): c.conv_L_cache}
    if arch == "lfm2moe":
        meta.update({a("expert_count"): c.num_experts, a("expert_used_count"): c.num_experts_per_tok,
                     a("expert_gating_func"): 2, a("leading_dense_block_count"): c.num_dense_layers})
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "token_embd_norm.weight": sd["model.embedding_norm.weight"]}
    if not c.tie_word_embeddings:
        t["output.weight"] = sd["lm_head.weight"]
    for i in range(n):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        t[b + "attn_norm.weight"] = sd[p + "operator_norm.weight"]
        t[b + "ffn_norm.weight"] = sd[p + "ffn_norm.weight"]
        if p + "conv.in_proj.weight" in sd:
            t[b + "shortconv.conv.weight"] = sd[p + "conv.conv.weight"].squeeze(1)
            t[b + "shortconv.in_proj.weight"] = sd[p + "conv.in_proj.weight"]
            t[b + "shortconv.out_proj.weight"] = sd[p + "conv.out_proj.weight"]
        else:
            at = p + "self_attn."
            for x in "qkv":
                t[b + f"attn_{x}.weight"] = sd[at + f"{x}_proj.weight"]
            t[b + "attn_output.weight"] = sd[at + "out_proj.weight"]
            t[b + "attn_q_norm.weight"] = sd[at + "q_layernorm.weight"]
            t[b + "attn_k_norm.weight"] = sd[at + "k_layernorm.weight"]
        ff = p + "feed_forward."
        if ff + "gate.weight" in sd:
            t[b + "ffn_gate_inp.weight"] = sd[ff + "gate.weight"]
            t[b + "exp_probs_b.bias"] = sd[ff + "expert_bias"]
            for x, w in experten(sd, ff, c.num_experts).items():
                t[b + f"ffn_{x}_exps.weight"] = w
        else:
            for ziel, quelle in (("gate", "w1"), ("up", "w3"), ("down", "w2")):
                t[b + f"ffn_{ziel}.weight"] = sd[ff + f"{quelle}.weight"]
    return meta, t


def _llama4(model, arch: str = "llama4") -> tuple[dict, dict]:
    """Llama 4 Text (transformers) -> GGUF wie conversion/llama.py (Llama4Model): ohne Umsortieren,
    gate_up der Experten getrennt und transponiert, llama3-Faktoren als rope_freqs."""
    import math
    c = model.config
    sd = model.state_dict()
    a = lambda s: f"{arch}.{s}"
    H, D = c.num_attention_heads, c.head_dim
    meta = {"general.architecture": arch, a("block_count"): c.num_hidden_layers,
            a("context_length"): c.max_position_embeddings, a("embedding_length"): c.hidden_size,
            a("feed_forward_length"): c.intermediate_size_mlp, a("expert_feed_forward_length"): c.intermediate_size,
            a("attention.head_count"): H, a("attention.head_count_kv"): c.num_key_value_heads,
            a("attention.key_length"): D, a("attention.value_length"): D,
            a("attention.layer_norm_rms_epsilon"): float(c.rms_norm_eps), a("rope.freq_base"): rope_theta(c),
            a("rope.dimension_count"): D, a("expert_count"): c.num_local_experts,
            a("expert_used_count"): c.num_experts_per_tok, a("interleave_moe_layer_step"): c.interleave_moe_layer_step}
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "output_norm.weight": sd["model.norm.weight"],
         "output.weight": sd["lm_head.weight"]}
    rp = _rope_global(c)
    if rp.get("rope_type") == "llama3":
        base = rope_theta(c)
        freqs = 1.0 / (base ** (torch.arange(0, D, 2, dtype=torch.float32) / D))
        f, lo, hi = rp.get("factor", 8.0), rp.get("low_freq_factor", 1.0), rp.get("high_freq_factor", 4.0)
        alt = rp.get("original_max_position_embeddings", 8192)
        faktoren = []
        for fr in freqs.tolist():
            wl = 2 * math.pi / fr
            if wl < alt / hi:
                faktoren.append(1.0)
            elif wl > alt / lo:
                faktoren.append(f)
            else:
                glatt = (alt / wl - lo) / (hi - lo)
                faktoren.append(1 / ((1 - glatt) / f + glatt))
        t["rope_freqs.weight"] = torch.tensor(faktoren, dtype=torch.float32)
    for i in range(c.num_hidden_layers):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
        t[b + "ffn_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
        for x in "qkv":
            t[b + f"attn_{x}.weight"] = sd[p + f"self_attn.{x}_proj.weight"]
        t[b + "attn_output.weight"] = sd[p + "self_attn.o_proj.weight"]
        ff = p + "feed_forward."
        if ff + "router.weight" in sd:
            t[b + "ffn_gate_inp.weight"] = sd[ff + "router.weight"]
            g, u = sd[ff + "experts.gate_up_proj"].transpose(-1, -2).split(c.intermediate_size, dim=-2)
            t[b + "ffn_gate_exps.weight"], t[b + "ffn_up_exps.weight"] = g.contiguous(), u.contiguous()
            t[b + "ffn_down_exps.weight"] = sd[ff + "experts.down_proj"].transpose(-1, -2).contiguous()
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}_shexp.weight"] = sd[ff + f"shared_expert.{x}_proj.weight"]
        else:
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}.weight"] = sd[ff + f"{x}_proj.weight"]
    return meta, t


def _kimi_linear(model, arch: str = "kimi-linear") -> tuple[dict, dict]:
    """Kimi Linear (transformers) -> GGUF wie conversion/kimi_linear.py: Faltung je q/k/v als
    (1, D, 1, K), ssm_a = -exp(A_log), dt als ssm_dt.bias, MLA mit k_b (transponiert) und v_b."""
    c = model.config
    sd = model.state_dict()
    a = lambda s: f"{arch}.{s}"
    H, n = c.num_attention_heads, c.num_hidden_layers
    nope, rot, vd, rank = c.qk_nope_head_dim, c.qk_rope_head_dim, c.v_head_dim, c.kv_lora_rank
    meta = {"general.architecture": arch, a("block_count"): n, a("context_length"): c.max_position_embeddings,
            a("embedding_length"): c.hidden_size, a("feed_forward_length"): c.intermediate_size,
            a("attention.head_count"): H,
            a("attention.head_count_kv"): [1 if x == "full_attention" else 0 for x in c.layer_types],
            a("attention.layer_norm_rms_epsilon"): float(c.rms_norm_eps), a("rope.dimension_count"): rot,
            a("attention.kv_lora_rank"): rank, a("attention.key_length"): rank + rot,
            a("attention.key_length_mla"): nope + rot, a("attention.value_length_mla"): vd,
            a("ssm.conv_kernel"): c.linear_conv_kernel_dim, a("kda.head_dim"): c.linear_head_dim,
            a("expert_count"): c.num_local_experts, a("expert_used_count"): c.num_experts_per_tok,
            a("expert_feed_forward_length"): c.moe_intermediate_size, a("expert_shared_count"): c.n_shared_experts,
            a("leading_dense_block_count"): sum(1 for x in c.mlp_layer_types if x == "dense"),
            a("expert_weights_scale"): float(c.routed_scaling_factor), a("expert_gating_func"): 2}
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "output_norm.weight": sd["model.norm.weight"],
         "output.weight": sd["lm_head.weight"]}
    for i in range(n):
        p, b = f"model.layers.{i}.", f"blk.{i}."
        at = p + "self_attn."
        t[b + "attn_norm.weight"] = sd[p + "input_layernorm.weight"]
        t[b + "ffn_norm.weight"] = sd[p + "post_attention_layernorm.weight"]
        if at + "forget_gate.A_log" in sd:
            qkv = c.linear_num_heads * c.linear_head_dim
            conv = sd[at + "conv1d.weight"].squeeze(1)
            for j, x in enumerate("qkv"):
                teil = conv[j * qkv:(j + 1) * qkv]
                t[b + f"ssm_conv1d_{x}.weight"] = teil.reshape(1, teil.shape[0], 1, teil.shape[1])
                t[b + f"attn_{x}.weight"] = sd[at + f"{x}_proj.weight"]
            t[b + "ssm_a"] = -torch.exp(sd[at + "forget_gate.A_log"].float())
            t[b + "ssm_dt.bias"] = sd[at + "forget_gate.dt_bias"]
            t[b + "ssm_f_a.weight"] = sd[at + "forget_gate.f_a_proj.weight"]
            t[b + "ssm_f_b.weight"] = sd[at + "forget_gate.f_b_proj.weight"]
            t[b + "ssm_beta.weight"] = sd[at + "b_proj.weight"]
            t[b + "ssm_g_a.weight"] = sd[at + "g_a_proj.weight"]
            t[b + "ssm_g_b.weight"] = sd[at + "g_b_proj.weight"]
            t[b + "ssm_norm.weight"] = sd[at + "o_norm.weight"]
        else:
            if at + "q_proj.weight" in sd:
                t[b + "attn_q.weight"] = sd[at + "q_proj.weight"]
            else:
                t[b + "attn_q_a.weight"] = sd[at + "q_a_proj.weight"]
                t[b + "attn_q_a_norm.weight"] = sd[at + "q_a_layernorm.weight"]
                t[b + "attn_q_b.weight"] = sd[at + "q_b_proj.weight"]
            t[b + "attn_kv_a_mqa.weight"] = sd[at + "kv_a_proj_with_mqa.weight"]
            t[b + "attn_kv_a_norm.weight"] = sd[at + "kv_a_layernorm.weight"]
            kv_b = sd[at + "kv_b_proj.weight"].view(H, nope + vd, rank)
            k_b, v_b = kv_b.split([nope, vd], dim=1)
            t[b + "attn_k_b.weight"] = k_b.transpose(1, 2).contiguous()
            t[b + "attn_v_b.weight"] = v_b.contiguous()
        t[b + "attn_output.weight"] = sd[at + "o_proj.weight"]
        m = p + "mlp."
        if m + "gate.weight" in sd:
            t[b + "ffn_gate_inp.weight"] = sd[m + "gate.weight"]
            t[b + "exp_probs_b.bias"] = sd[m + "gate.e_score_correction_bias"]
            for x, w in experten(sd, m, c.num_local_experts).items():
                t[b + f"ffn_{x}_exps.weight"] = w
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}_shexp.weight"] = sd[m + f"shared_experts.{x}_proj.weight"]
        else:
            for x in ("gate", "up", "down"):
                t[b + f"ffn_{x}.weight"] = sd[m + f"{x}_proj.weight"]
    return meta, t


def umwandeln(model, arch: str) -> tuple[dict, dict]:
    """transformers-Modell -> (Metadaten, Tensoren) im GGUF-Schema von `arch`."""
    if arch == "kimi-linear":
        return _kimi_linear(model)
    if arch == "llama4":
        return _llama4(model)
    if arch in ("lfm2", "lfm2moe"):
        return _lfm2(model, arch)
    if arch in ("granitehybrid", "nemotron_h", "nemotron_h_moe", "falcon-h1"):
        return _mamba_hybrid(model, arch)
    if arch == "deepseek2":
        return _deepseek2(model)
    if arch in ("qwen35", "qwen35moe", "qwen3next"):
        return _qwen_delta(model, arch)
    c = model.config
    sd = {k: v for k, v in model.state_dict().items()}
    H, Hkv = c.num_attention_heads, getattr(c, "num_key_value_heads", c.num_attention_heads)
    D = getattr(c, "head_dim", None) or c.hidden_size // H
    gemma = arch.startswith("gemma")
    plus1 = (lambda t: t + 1) if gemma else (lambda t: t)
    paare = arch in ("llama", "granite", "mistral3", "smollm3")
    a = lambda s: f"{arch}.{s}"
    meta = {"general.architecture": arch,
            a("block_count"): c.num_hidden_layers, a("context_length"): c.max_position_embeddings,
            a("embedding_length"): c.hidden_size, a("feed_forward_length"): c.intermediate_size,
            a("attention.head_count"): H, a("attention.head_count_kv"): Hkv,
            a("attention.key_length"): D, a("attention.value_length"): D,
            a("rope.freq_base"): rope_theta(c)}
    if arch in ("cohere2", "command-r"):                # LayerNorm (ohne Bias)
        meta[a("attention.layer_norm_epsilon")] = float(c.layer_norm_eps)
        meta[a("logit_scale")] = float(c.logit_scale)
    else:
        meta[a("attention.layer_norm_rms_epsilon")] = float(c.rms_norm_eps)
    if arch in ("olmo3", "exaone4", "cohere2") and getattr(c, "sliding_window", None):
        meta[a("attention.sliding_window")] = int(c.sliding_window)
    rs = rope_scaling(c)
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
            rs = rope_scaling(c)
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
            for ziel, w in experten(sd, moe, n_exp, namen).items():
                t[b + f"ffn_{ziel}_exps.weight"] = w
            if moe + "gate.e_score_correction_bias" in sd:                 # GLM-4.5: Sigmoid mit Korrektur
                t[b + "exp_probs_b.bias"] = sd[moe + "gate.e_score_correction_bias"]
                meta[a("expert_gating_func")] = 2
                meta[a("expert_group_count")] = c.n_group
                meta[a("expert_group_used_count")] = c.topk_group
                meta[a("expert_weights_scale")] = float(c.routed_scaling_factor)
                meta[a("expert_weights_norm")] = bool(c.norm_topk_prob)
            for ort in ("moe_statics.", "gate.moe_statics."):              # ERNIE 4.5: Auswahl-Korrektur (4 · 5)
                if moe + ort + "e_score_correction_bias" in sd:
                    t[b + "exp_probs_b.bias"] = sd[moe + ort + "e_score_correction_bias"].reshape(-1)
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
