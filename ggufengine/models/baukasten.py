"""
Baukasten: ein Decoder-Transformer, der sich aus der GGUF-Datei selbst zusammensetzt.

Wie in llama.cpp teilen sich die Bauarten dieselben Bausteine (Norm, Attention, RoPE, FFN,
MoE); was eine Bauart ausmacht, steht in der Datei – vorhandene Tensoren und Metadaten –
plus eine kleine Tabelle je Architektur (RoPE-Art, Aktivierung, Muster der Fenster-Schichten).

Erkannt je Schicht:
* Norm vor und/oder nach Attention und FFN (`attn_norm`, `post_attention_norm`, `ffn_norm`,
  `post_ffw_norm`), RMSNorm oder LayerNorm (mit Bias), K2-Horizon gruppiert
* Q/K/V getrennt oder zusammengefasst (`attn_qkv`), Biases, Q/K-Norm je Kopf oder über alle Köpfe
* RoPE: NeoX oder Paare, teilweise, `rope_freqs` (Llama 3.x), linear, YaRN, LongRoPE (Phi-3/4)
* Schiebefenster je Schicht (Gemma 2/3: Muster; sonst alle Schichten), eigene RoPE-Basis dort
* Attention- und Logit-Softcap (Gemma 2), Skalen (Gemma: Einbettung; Granite: Einbettung,
  Attention, Residual, Logits)
* FFN: gated (SiLU/GELU), Gate+Up zusammengefasst (Phi-3), ohne Gate; Mixture-of-Experts
  (`ffn_gate_inp` + `ffn_*_exps`, optional geteilter Experte, Router- und Experten-Bias,
  Auswahl-Korrektur `exp_probs_b`)
* Router Softmax oder Sigmoid (`expert_gating_func`), Experten-Gruppen, Gewichts-Skala
* gpt-oss: Attention-Sinks (`attn_sinks`), Fenster jede zweite Schicht, Top-k vor der Softmax,
  begrenzte SwiGLU-Variante, YaRN ohne gerundete Grenzen
* Command-R/Cohere 2: Attention und FFN parallel, globale Schichten ohne RoPE (auch EXAONE 4)
* GLM-4.5: Zusatzschichten für Mehr-Token-Vorhersage werden übersprungen

Jede Zahl aus der Datei wird vor Gebrauch auf Plausibilität geprüft.
"""
from __future__ import annotations

import math
from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import (GraphDecoder, KVCache, RoPE, WeightLoader, attention, attention_softcap,
                     group_rms_norm, layer_norm, rms_norm)
from .moe import Experten

ARCHS = ("llama", "mistral", "qwen2", "qwen3", "qwen2moe", "qwen3moe", "k2-horizon",
         "gemma", "gemma2", "gemma3", "phi3", "granite", "granitemoe", "olmo2", "olmo3", "gpt-oss",
         "ernie4_5", "ernie4_5-moe", "seed_oss", "exaone4", "cohere2", "command-r", "glm4", "glm4moe")

# llama.cpp-RoPE-Art „normal“ (benachbarte Paare; der Konverter hat Q/K dafür umsortiert)
_PAARE = {"llama", "mistral", "granite", "granitemoe", "ernie4_5", "ernie4_5-moe", "cohere2", "command-r", "glm4"}
_GELU = {"gemma", "gemma2", "gemma3"}                    # GELU (tanh) statt SiLU
_EINBETTUNG_WURZEL = {"gemma", "gemma2", "gemma3"}      # Einbettung * sqrt(n_embd)
_FENSTER_MUSTER = {"gemma2": 2, "gemma3": 6, "gpt-oss": 2,   # jede n-te Schicht global, Rest Fenster
                   "olmo3": 4, "exaone4": 4, "cohere2": 4}
_GLOBAL_OHNE_ROPE = {"exaone4", "cohere2"}             # mit Fenster: globale Schichten ohne RoPE
_FENSTER_ROPE_SCHLICHT = {"olmo3"}                     # Fenster-Schichten: RoPE ohne Skalierung (YaRN nur global)
_PARALLEL = {"cohere2", "command-r"}                   # Attention und FFN auf derselben Norm, beide aufs Residuum
_LOGITS_MAL_SKALA = {"cohere2", "command-r"}           # logit_scale multipliziert (Granite: teilt)
GATING_SOFTMAX, GATING_SIGMOID = 1, 2
_FENSTER_BASIS = {"gemma3": 10000.0}                    # RoPE-Basis der Fenster-Schichten
_TOPK_NORMIEREN = {"qwen2moe": False}                   # sonst True
_SOFTMAX_NACH_AUSWAHL = {"gpt-oss"}                     # Top-k der Router-Logits, dann Softmax über diese
_FFN_NORM_HEISST_POST_ATTN = {"gpt-oss"}                # llama.cpp: post_attention_norm = Norm vor dem FFN
_YARN_UNGERUNDET = {"gpt-oss"}                          # YaRN-Grenzen ohne Runden (transformers truncate=False)
_OAI_GRENZE, _OAI_ALPHA = 7.0, 1.702


def _glu_oai(g: torch.Tensor, u: torch.Tensor) -> torch.Tensor:
    """gpt-oss: gate und up begrenzt, gate * sigmoid(1.702 gate) * (up + 1)."""
    g = g.clamp(max=_OAI_GRENZE)
    return g * torch.sigmoid(_OAI_ALPHA * g) * (u.clamp(-_OAI_GRENZE, _OAI_GRENZE) + 1)


def _zahl(gg: GGUFFile, key: str, default=None, *, ganz: bool = False, lo=None, hi=None):
    v = gg.get(key, default)
    if v is None:
        return None
    if isinstance(v, (list, str)) or (isinstance(v, bool) and not isinstance(default, bool)):
        raise GGUFError(f"{key}: unexpected value {v!r}")
    v = int(v) if ganz else float(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi) or (not ganz and not math.isfinite(v)):
        raise GGUFError(f"{key}: implausible value {v!r}")
    return v


class _Schicht:
    def __init__(self, ld: WeightLoader, i: int, hp: dict, rope: RoPE, n_ctx: int, fenster: int):
        p = f"blk.{i}."
        self.hp = hp
        self.H, self.Hkv, self.D = hp["n_heads"], hp["n_kv_heads"], hp["head_dim"]
        self.rope = rope
        self.fenster = fenster

        def norm(name):
            if not ld.has(p + name + ".weight"):
                return None
            b = ld.norm(p + name + ".bias") if ld.has(p + name + ".bias") else None
            return ld.norm(p + name + ".weight"), b
        self.attn_norm, self.attn_post = norm("attn_norm"), norm("post_attention_norm")
        self.ffn_norm, self.ffn_post = norm("ffn_norm"), norm("post_ffw_norm")
        if hp["arch"] in _FFN_NORM_HEISST_POST_ATTN:
            self.ffn_norm, self.attn_post = self.attn_post, None
        self.sinks = ld.norm(p + "attn_sinks.weight") if ld.has(p + "attn_sinks.weight") else None
        if self.sinks is not None and self.sinks.numel() != hp["n_heads"]:
            raise GGUFError(f"layer {i}: attn_sinks has an unexpected size")

        H, Hkv, D = self.H, self.Hkv, self.D
        if ld.has(p + "attn_qkv.weight"):
            self.qkv = ld.linear(p + "attn_qkv.weight", p + "attn_qkv.bias")
            if self.qkv.n_out != (H + 2 * Hkv) * D:
                raise GGUFError(f"layer {i}: attn_qkv has an unexpected size")
            self.q = self.k = self.v = None
        else:
            self.qkv = None
            self.q = ld.linear(p + "attn_q.weight", p + "attn_q.bias")
            self.k = ld.linear(p + "attn_k.weight", p + "attn_k.bias")
            self.v = ld.linear(p + "attn_v.weight", p + "attn_v.bias")
            if (self.q.n_out, self.k.n_out, self.v.n_out) != (H * D, Hkv * D, Hkv * D):
                raise GGUFError(f"layer {i}: attention projections have unexpected sizes")
        self.o = ld.linear(p + "attn_output.weight", p + "attn_output.bias")
        self.q_norm = ld.norm(p + "attn_q_norm.weight") if ld.has(p + "attn_q_norm.weight") else None
        self.k_norm = ld.norm(p + "attn_k_norm.weight") if ld.has(p + "attn_k_norm.weight") else None
        for n, w, per_kopf, ganz in (("attn_q_norm", self.q_norm, D, H * D), ("attn_k_norm", self.k_norm, D, Hkv * D)):
            if w is not None and w.numel() not in (per_kopf, ganz):
                raise GGUFError(f"layer {i}: {n} has an unexpected size")

        self.moe = ld.has(p + "ffn_gate_inp.weight")
        if self.moe:
            self.router = ld.linear(p + "ffn_gate_inp.weight", p + "ffn_gate_inp.bias")
            self.experten = Experten(ld, p, self._akt, _glu_oai if hp["arch"] == "gpt-oss" else None)
            n_exp = len(self.experten)
            # Korrektur nur für die Auswahl, nicht für die Gewichte (ERNIE 4.5)
            self.wahl_bias = ld.norm(p + "exp_probs_b.bias").reshape(-1) if ld.has(p + "exp_probs_b.bias") else None
            if self.wahl_bias is not None and self.wahl_bias.numel() != n_exp:
                raise GGUFError(f"layer {i}: exp_probs_b has an unexpected size")
            if not (self.experten.pruefen() and n_exp == self.router.n_out and 0 < hp["n_expert_used"] <= n_exp):
                raise GGUFError(f"layer {i}: inconsistent expert tensors")
            self.s_gate = self.s_up = self.s_down = self.s_inp = None
            if ld.has(p + "ffn_up_shexp.weight"):                 # geteilter Experte (Qwen2-MoE)
                self.s_gate = ld.linear(p + "ffn_gate_shexp.weight")
                self.s_up = ld.linear(p + "ffn_up_shexp.weight")
                self.s_down = ld.linear(p + "ffn_down_shexp.weight")
                if ld.has(p + "ffn_gate_inp_shexp.weight"):
                    self.s_inp = ld.norm(p + "ffn_gate_inp_shexp.weight").reshape(-1)
        else:
            self.gate = ld.linear(p + "ffn_gate.weight") if ld.has(p + "ffn_gate.weight") else None
            self.up = ld.linear(p + "ffn_up.weight", p + "ffn_up.bias")
            self.down = ld.linear(p + "ffn_down.weight", p + "ffn_down.bias")
            self.gate_up = self.gate is None and self.up.n_out == 2 * self.down.n_in
        self.cache = KVCache(Hkv, D, n_ctx, ld.device, ld.dtype, ld.kv_bits)

    # -- Bausteine ---------------------------------------------------------------
    def _norm(self, x, nw, gruppen=True):
        w, b = nw
        hp = self.hp
        if hp["layernorm"]:
            return layer_norm(x, w, b, hp["eps"])
        y = group_rms_norm(x, w, hp["eps"], hp["norm_groups"] if gruppen else 1)
        return y + b if b is not None else y

    def _akt(self, x):
        return F.gelu(x, approximate="tanh") if self.hp["gelu"] else F.silu(x)

    def _mlp(self, x, gate, up, down):
        return down(self._akt(gate(x).float()) * up(x).float()).float()

    def _ffn(self, x):
        if self.moe:
            return self._experten(x)
        if self.gate is not None:
            return self._mlp(x, self.gate, self.up, self.down)
        u = self.up(x).float()
        if self.gate_up:                                          # Phi-3: [gate | up]
            g, u = u.chunk(2, dim=-1)
            return self.down(self._akt(g) * u).float()
        return self.down(self._akt(u)).float()

    def _experten(self, x):
        logits = self.router(x).float()
        if self.hp["arch"] in _SOFTMAX_NACH_AUSWAHL:
            w, idx = logits.topk(self.hp["n_expert_used"], dim=-1)
            w = torch.softmax(w, dim=-1)
        else:
            idx, w = self._auswahl(logits)
        out = self.experten(x, idx, w)
        if self.s_up is not None:
            y = self._mlp(x, self.s_gate, self.s_up, self.s_down)
            if self.s_inp is not None:
                y = y * torch.sigmoid(x.float() @ self.s_inp.unsqueeze(-1))
            out = out + y
        return out

    def _auswahl(self, logits: torch.Tensor):
        """Softmax- oder Sigmoid-Router; Auswahl-Korrektur und Gruppen nur für die Wahl (Gruppen
        nach bestem Experten bzw. Summe der zwei besten, wie DeepSeek V2/V3), Gewichte aus den
        unkorrigierten Werten, optional normiert, mal expert_weights_scale."""
        hp = self.hp
        sigmoid = hp["gating"] == GATING_SIGMOID
        p = torch.sigmoid(logits) if sigmoid else torch.softmax(logits, dim=-1)
        wahl = p + self.wahl_bias if self.wahl_bias is not None else p
        if hp["n_group"] > 1:
            L, je = p.shape[0], p.shape[1] // hp["n_group"]
            g = wahl.view(L, hp["n_group"], je)
            gw = g.topk(min(2, je), dim=-1)[0].sum(-1) if sigmoid else g.amax(-1)
            maske = torch.zeros(L, hp["n_group"], dtype=torch.bool, device=p.device)
            maske.scatter_(1, gw.topk(hp["group_used"], dim=-1)[1], True)
            wahl = wahl.masked_fill(~maske.repeat_interleave(je, dim=1), float("-inf"))
        idx = wahl.topk(hp["n_expert_used"], dim=-1)[1]
        w = p.gather(-1, idx)
        if hp["topk_normieren"]:
            w = w / (w.sum(-1, keepdim=True) + 1e-20)
        return idx, w * hp["weights_scale"]

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        hp, L = self.hp, h.shape[0]
        H, Hkv, D = self.H, self.Hkv, self.D
        x = self._norm(h, self.attn_norm) if self.attn_norm is not None else h
        if self.qkv is not None:
            q, k, v = self.qkv(x).float().split([H * D, Hkv * D, Hkv * D], dim=-1)
        else:
            q, k, v = self.q(x).float(), self.k(x).float(), self.v(x)
        if self.q_norm is not None and self.q_norm.numel() != D:       # über alle Köpfe (OLMo 2)
            q = rms_norm(q, self.q_norm, hp["eps"])
        if self.k_norm is not None and self.k_norm.numel() != D:
            k = rms_norm(k, self.k_norm, hp["eps"])
        q, k, v = q.reshape(L, H, D), k.reshape(L, Hkv, D), v.reshape(L, Hkv, D)
        if self.q_norm is not None and self.q_norm.numel() == D:       # je Kopf (Qwen3, Gemma 3)
            q = rms_norm(q, self.q_norm, hp["eps"])
        if self.k_norm is not None and self.k_norm.numel() == D:
            k = rms_norm(k, self.k_norm, hp["eps"])
        if self.rope is not None:
            q, k = self.rope(q, pos_idx), self.rope(k, pos_idx)
        self.cache.write(k, v, pos_idx)
        if hp["attn_softcap"]:
            o = attention_softcap(q, *self.cache.kv(kv_len), pos_idx, kv_len, hp["attn_scale"],
                                  hp["attn_softcap"], self.fenster)
        else:
            o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, hp["attn_scale"], self.fenster,
                          sinks=self.sinks)
        o = self.o(o.reshape(L, -1)).float()
        if hp["parallel"]:
            return h + o + self._ffn(x)
        if self.attn_post is not None:
            o = self._norm(o, self.attn_post, gruppen=False)
        h = h + o * hp["residual_scale"]
        x = self._norm(h, self.ffn_norm) if self.ffn_norm is not None else h
        y = self._ffn(x)
        if self.ffn_post is not None:
            y = self._norm(y, self.ffn_post, gruppen=False)
        return h + y * hp["residual_scale"]


class BaukastenModel:
    arch_names = ARCHS
    prefill_chunk = 2048            # full KV caches only: long chunks read faster (qlinear)

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 8192, progress=None,
                 quant: bool = True, kv_bits: int = 16, experten_vram: Optional[int] = None):
        a = gg.architecture
        if a not in self.arch_names:
            raise GGUFError(f"architecture {a!r} not handled by the Baukasten")
        k = lambda s: f"{a}.{s}"
        n_layer = _zahl(gg, k("block_count"), ganz=True, lo=1, hi=512)
        # Zusatzschichten für Mehr-Token-Vorhersage (GLM-4.5) am Ende: beim Erzeugen nicht gebraucht
        n_layer -= _zahl(gg, k("nextn_predict_layers"), 0, ganz=True, lo=0, hi=n_layer - 1)
        n_embd = _zahl(gg, k("embedding_length"), ganz=True, lo=1, hi=65536)
        n_heads = _zahl(gg, k("attention.head_count"), ganz=True, lo=1, hi=1024)
        n_kv = _zahl(gg, k("attention.head_count_kv"), n_heads, ganz=True, lo=1, hi=1024)
        head_dim = _zahl(gg, k("attention.key_length"), n_embd // n_heads, ganz=True, lo=1, hi=1024)
        if _zahl(gg, k("attention.value_length"), head_dim, ganz=True) != head_dim:
            raise GGUFError("different key and value head sizes are not supported")
        if n_heads % n_kv:
            raise GGUFError("head_count must be a multiple of head_count_kv")
        rms_eps = gg.get(k("attention.layer_norm_rms_epsilon"))
        eps = _zahl(gg, k("attention.layer_norm_rms_epsilon") if rms_eps is not None
                    else k("attention.layer_norm_epsilon"), 1e-5, lo=0, hi=1)
        rope_dim = _zahl(gg, k("rope.dimension_count"), head_dim, ganz=True, lo=2, hi=head_dim)
        if rope_dim % 2:
            raise GGUFError("rope.dimension_count must be even")
        groesse = "27b" if (a == "gemma2" and n_layer == 46) or (a == "gemma3" and n_layer == 62) else ""
        attn_scale = _zahl(gg, k("attention.scale"), None, lo=0, hi=100)
        if attn_scale is None:                     # Gemma 2/3 27B: 1/sqrt(n_embd/n_head) (query_pre_attn_scalar)
            attn_scale = (n_embd / n_heads) ** -0.5 if groesse else head_dim ** -0.5
        hp = dict(
            arch=a, n_layer=n_layer, n_embd=n_embd, n_heads=n_heads, n_kv_heads=n_kv, head_dim=head_dim, eps=eps,
            layernorm=rms_eps is None and gg.get(k("attention.layer_norm_epsilon")) is not None,
            norm_groups=_zahl(gg, k("attention.group_norm_groups"), 1, ganz=True, lo=1, hi=n_embd),
            gelu=a in _GELU,
            attn_scale=attn_scale,
            attn_softcap=_zahl(gg, k("attn_logit_softcapping"), 0.0, lo=0, hi=1e4) or 0.0,
            final_softcap=_zahl(gg, k("final_logit_softcapping"), 0.0, lo=0, hi=1e4) or 0.0,
            residual_scale=_zahl(gg, k("residual_scale"), 1.0, lo=0, hi=100),
            logit_scale=_zahl(gg, k("logit_scale"), 1.0, lo=1e-6, hi=1e4),
            embedding_scale=(n_embd ** 0.5 if a in _EINBETTUNG_WURZEL
                             else _zahl(gg, k("embedding_scale"), 1.0, lo=0, hi=1e4)),
            n_expert_used=_zahl(gg, k("expert_used_count"), 0, ganz=True, lo=0, hi=1024),
            topk_normieren=bool(gg.get(k("expert_weights_norm"), _TOPK_NORMIEREN.get(a, True))),
            gating=_zahl(gg, k("expert_gating_func"), GATING_SOFTMAX, ganz=True, lo=1, hi=2),
            n_group=_zahl(gg, k("expert_group_count"), 1, ganz=True, lo=1, hi=1024),
            group_used=_zahl(gg, k("expert_group_used_count"), 1, ganz=True, lo=1, hi=1024),
            weights_scale=_zahl(gg, k("expert_weights_scale"), 1.0, lo=0, hi=1e4),
            parallel=a in _PARALLEL,
            logits_mal=a in _LOGITS_MAL_SKALA,
        )
        if n_embd % hp["norm_groups"]:
            raise GGUFError("group_norm_groups must divide embedding_length")
        self.hp = hp
        self.n_ctx = n_ctx
        self.device = device
        ld = WeightLoader(gg, device, dtype, progress, quant=quant, kv_bits=kv_bits, experten_vram=experten_vram)
        tied = not ld.has("output.weight")
        self.embed, head = ld.embedding("token_embd.weight", tied_head=tied)
        self.out_norm = ld.norm("output_norm.weight")
        self.out_norm_b = ld.norm("output_norm.bias") if ld.has("output_norm.bias") else None
        self.lm_head = head if tied else ld.linear("output.weight", "output.bias")

        neox = a not in _PAARE
        global_rope = self._rope(gg, ld, a, rope_dim, neox, n_ctx, device)
        fenster = _zahl(gg, k("attention.sliding_window"), 0, ganz=True, lo=0, hi=1 << 24) or 0
        muster = gg.get(k("attention.sliding_window_pattern"), _FENSTER_MUSTER.get(a))
        if isinstance(muster, bool) or (muster is not None and not isinstance(muster, (int, list))):
            raise GGUFError("invalid attention.sliding_window_pattern")
        if isinstance(muster, list) and (len(muster) != n_layer or any(not isinstance(m, bool) for m in muster)):
            raise GGUFError("attention.sliding_window_pattern has the wrong length")
        if isinstance(muster, int) and not 1 <= muster <= n_layer:
            raise GGUFError("implausible attention.sliding_window_pattern")
        fenster_rope = global_rope
        if fenster and a in _FENSTER_ROPE_SCHLICHT:
            fenster_rope = RoPE(rope_dim, _zahl(gg, k("rope.freq_base"), 10000.0, lo=1, hi=1e12), device,
                                max_pos=n_ctx, neox=neox)
        if fenster and a in _FENSTER_BASIS:
            basis = _zahl(gg, k("rope.freq_base_swa"), _FENSTER_BASIS[a], lo=1, hi=1e12)
            fenster_rope = RoPE(rope_dim, basis, device, max_pos=n_ctx, neox=neox)

        def ist_fenster(i):
            if not fenster:
                return False
            if muster is None:
                return True                         # Fenster in allen Schichten (Mistral)
            if isinstance(muster, list):
                return muster[i]
            return (i % muster) != muster - 1
        def rope_von(i):
            if ist_fenster(i):
                return fenster_rope
            return None if fenster and a in _GLOBAL_OHNE_ROPE else global_rope
        self.layers: List[_Schicht] = [
            _Schicht(ld, i, hp, rope_von(i), n_ctx, fenster if ist_fenster(i) else 0) for i in range(n_layer)]
        self.moe = any(l.moe for l in self.layers)
        if self.moe and not 0 < hp["n_expert_used"]:
            raise GGUFError("expert_used_count missing for a mixture-of-experts model")
        self.n_vocab = self.embed.n_vocab
        self.n_quant = ld.n_quant
        # single steps as CUDA graph: possible when every MoE layer picks its experts on the GPU
        ohne_sync = all(l.experten.graphfaehig for l in self.layers if l.moe)
        self.graph = GraphDecoder(self, n_ctx) if device.type == "cuda" and ohne_sync else None

    @staticmethod
    def _rope(gg, ld, a, dim, neox, n_ctx, device) -> RoPE:
        k = lambda s: f"{a}.{s}"
        basis = _zahl(gg, k("rope.freq_base"), 10000.0, lo=1, hi=1e12)
        art = gg.get(k("rope.scaling.type"), "none")
        faktor = _zahl(gg, k("rope.scaling.factor"), 1.0, lo=1e-3, hi=1e4)
        orig = _zahl(gg, k("rope.scaling.original_context_length"), 0, ganz=True, lo=0, hi=1 << 26)
        mscale = _zahl(gg, k("rope.scaling.attn_factor"), None, lo=0, hi=100)
        freq = ld.norm("rope_freqs.weight") if ld.has("rope_freqs.weight") else None
        if ld.has("rope_factors_long.weight"):                    # LongRoPE (Phi-3/4)
            lang = n_ctx > orig if orig else False
            freq = ld.norm("rope_factors_long.weight" if lang else "rope_factors_short.weight")
            ctx = _zahl(gg, k("context_length"), 0, ganz=True, lo=0, hi=1 << 26)
            if mscale is None:
                s = ctx / orig if orig else 1.0
                mscale = math.sqrt(1 + math.log(s) / math.log(orig)) if s > 1 else 1.0
        yarn, scale = None, 1.0
        if art == "linear" and faktor != 1.0:
            scale = faktor
        elif art == "yarn" and faktor > 1.0 and orig:
            yarn = (faktor, orig,
                    _zahl(gg, k("rope.scaling.yarn_beta_fast"), 32.0, lo=0, hi=1e4),
                    _zahl(gg, k("rope.scaling.yarn_beta_slow"), 1.0, lo=0, hi=1e4), a not in _YARN_UNGERUNDET)
            if mscale is None:
                mscale = 0.1 * math.log(faktor) + 1.0
        elif art not in ("none", "linear", "yarn", "longrope", None):
            raise GGUFError(f"unsupported rope.scaling.type {art!r}")
        return RoPE(dim, basis, device, max_pos=n_ctx, neox=neox, freq_factors=freq, scale=scale,
                    yarn=yarn, mscale=mscale or 1.0)

    # -- Zustand (nur KV-Caches: Plätze werden je Position überschrieben) ----------------
    def reset(self):
        pass

    def snapshot_state(self):
        return None

    def restore_state(self, snap):
        pass

    def resume_from(self, common: int, old_len: int) -> Optional[int]:
        return common

    def checkpoint_state(self):
        return None

    def restore_checkpoint(self, snap):
        pass

    # -- Rechnen ---------------------------------------------------------------------
    def _verborgen(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int, alle: bool = False) -> torch.Tensor:
        """Zustand nach der Schluss-Norm: letzte Zeile oder (alle=True) jede Zeile."""
        hp = self.hp
        h = self.embed(tok)
        if hp["embedding_scale"] != 1.0:
            h = h * hp["embedding_scale"]
        for layer in self.layers:
            h = layer(h, pos_idx, kv_len)
        x = h if alle else h[-1:]
        if hp["layernorm"]:
            return layer_norm(x, self.out_norm, self.out_norm_b, hp["eps"])
        return group_rms_norm(x, self.out_norm, hp["eps"], hp["norm_groups"])

    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        hp = self.hp
        x = self._verborgen(tok, pos_idx, kv_len)
        logits = self.lm_head(x)[0].float()
        if hp["logit_scale"] != 1.0:                   # Granite teilt die Logits, Cohere multipliziert
            logits = logits * hp["logit_scale"] if hp["logits_mal"] else logits / hp["logit_scale"]
        if hp["final_softcap"]:
            c = hp["final_softcap"]
            logits = torch.tanh(logits / c) * c
        return logits

    @torch.inference_mode()
    def einbetten(self, tokens: List[int], pooling: str = "last") -> torch.Tensor:
        """Ein Vektor für die ganze Folge (Embedding-Modelle): letztes Token, Mittel
        oder erstes Token des Zustands nach der Schluss-Norm, in float32."""
        if not 0 < len(tokens) <= self.n_ctx:
            raise RuntimeError(f"text must have 1..{self.n_ctx} tokens")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")
        if pooling not in ("last", "mean", "cls"):
            raise ValueError(f"unknown pooling {pooling!r}")
        summe, erstes, x = None, None, None
        for start in range(0, len(tokens), self.prefill_chunk):
            teil = tokens[start:start + self.prefill_chunk]
            tok = torch.tensor(teil, device=self.device, dtype=torch.long)
            pos_idx = torch.arange(start, start + len(teil), device=self.device, dtype=torch.long)
            x = self._verborgen(tok, pos_idx, start + len(teil), alle=pooling != "last").float()
            if erstes is None:
                erstes = x[0]
            if pooling == "mean":
                summe = x.sum(0) if summe is None else summe + x.sum(0)
        if pooling == "mean":
            return summe / len(tokens)
        return erstes if pooling == "cls" else x[-1]

    @torch.inference_mode()
    def forward(self, tokens: List[int], pos: int) -> torch.Tensor:
        if pos < 0 or pos + len(tokens) > self.n_ctx:
            raise RuntimeError(f"context window of {self.n_ctx} tokens exceeded")
        if any(not 0 <= t < self.n_vocab for t in tokens):
            raise ValueError("token id out of range")
        if len(tokens) == 1 and self.graph is not None:
            return self.graph(tokens[0], pos)
        tok = torch.tensor(tokens, device=self.device, dtype=torch.long)
        pos_idx = torch.arange(pos, pos + len(tokens), device=self.device, dtype=torch.long)
        return self._run(tok, pos_idx, pos + len(tokens))
