"""Baukasten gegen transformers: je Familie ein winziges Zufallsmodell (nichts wird geladen),
als GGUF geschrieben, von beiden gerechnet – die Logits müssen übereinstimmen.

python -m unittest tests.test_baukasten
"""

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None

V, E, H, HKV, D, FF, N_CTX = 96, 64, 4, 2, 16, 96, 48


def _klein(cls, **kw):
    basis = dict(vocab_size=V, hidden_size=E, intermediate_size=FF, num_hidden_layers=2,
                 num_attention_heads=H, num_key_value_heads=HKV, head_dim=D, max_position_embeddings=64,
                 rms_norm_eps=1e-6, tie_word_embeddings=False, attn_implementation="eager",
                 pad_token_id=0, bos_token_id=1, eos_token_id=2)
    basis.update(kw)
    return cls(**basis)


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class BaukastenGegenTransformers(unittest.TestCase):

    def _vergleich(self, cfg, modell_cls, arch, meta=None, tensoren=None, n=14, vorab=9):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        torch.manual_seed(0)
        hf = modell_cls(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
            for name, buf in hf.named_buffers():
                if name.endswith("e_score_correction_bias"):
                    buf.uniform_(0, 0.1)
        m, t = B.umwandeln(hf, arch)
        m.update(meta or {})
        t.update(tensoren(hf) if callable(tensoren) else (tensoren or {}))
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, m, t)
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device("cpu"), torch.float32, N_CTX, quant=False)
                self.assertEqual(type(modell).__name__, "BaukastenModel")
                ids = torch.randint(0, V, (n,))
                with torch.no_grad():
                    ref = hf(ids[None]).logits[0].float()
                aus = [modell.forward(ids[:vorab].tolist(), 0)]
                aus += [modell.forward([int(ids[j])], j) for j in range(vorab, n)]
                fehler = [(j, float((a - ref[j]).abs().max())) for j, a in zip(range(vorab - 1, n), aus)]
                del modell, aus
            finally:
                import gc
                gc.collect()
                gg.close()
            for j, diff in fehler:
                self.assertLess(diff, 2e-3 * max(1.0, float(ref[j].abs().max())),
                                f"{arch}: Position {j} weicht um {diff:.2e} ab")
        finally:
            os.unlink(pfad)

    # -- Llama-Familie --------------------------------------------------------------
    def test_llama(self):
        self._vergleich(_klein(T.LlamaConfig), T.LlamaForCausalLM, "llama")

    def test_llama3_rope_freqs(self):
        cfg = _klein(T.LlamaConfig, rope_scaling={"rope_type": "llama3", "factor": 8.0, "low_freq_factor": 1.0,
                                                   "high_freq_factor": 4.0, "original_max_position_embeddings": 16})

        def freqs(hf):
            inv = hf.model.rotary_emb.inv_freq.float()
            orig = 1.0 / (hf.config.rope_theta ** (torch.arange(0, D, 2).float() / D))
            return {"rope_freqs.weight": orig / inv}
        self._vergleich(cfg, T.LlamaForCausalLM, "llama", tensoren=freqs)

    def test_mistral_schiebefenster(self):
        self._vergleich(_klein(T.MistralConfig, sliding_window=4), T.MistralForCausalLM, "llama",
                        meta={"llama.attention.sliding_window": 4})

    def test_granite_skalen(self):
        cfg = _klein(T.GraniteConfig, embedding_multiplier=3.0, attention_multiplier=0.2,
                     residual_multiplier=0.5, logits_scaling=4.0)
        self._vergleich(cfg, T.GraniteForCausalLM, "granite",
                        meta={"granite.embedding_scale": 3.0, "granite.attention.scale": 0.2,
                              "granite.residual_scale": 0.5, "granite.logit_scale": 4.0})

    def test_mixtral_moe(self):
        cfg = _klein(T.MixtralConfig, num_local_experts=4, num_experts_per_tok=2)
        self._vergleich(cfg, T.MixtralForCausalLM, "llama")

    # -- Qwen ---------------------------------------------------------------------------
    def test_qwen2_bias(self):
        self._vergleich(_klein(T.Qwen2Config), T.Qwen2ForCausalLM, "qwen2")

    def test_qwen3_qk_norm(self):
        self._vergleich(_klein(T.Qwen3Config), T.Qwen3ForCausalLM, "qwen3")

    def test_qwen3_yarn(self):
        cfg = _klein(T.Qwen3Config, rope_scaling={"rope_type": "yarn", "factor": 4.0,
                                                   "original_max_position_embeddings": 16})
        self._vergleich(cfg, T.Qwen3ForCausalLM, "qwen3",
                        meta={"qwen3.rope.scaling.type": "yarn", "qwen3.rope.scaling.factor": 4.0,
                              "qwen3.rope.scaling.original_context_length": 16})

    def test_qwen3_moe(self):
        cfg = _klein(T.Qwen3MoeConfig, num_experts=4, num_experts_per_tok=2, moe_intermediate_size=48,
                     norm_topk_prob=True, decoder_sparse_step=1, mlp_only_layers=[])
        self._vergleich(cfg, T.Qwen3MoeForCausalLM, "qwen3moe")

    def test_ernie4_5_moe_auswahl_bias_paare(self):
        cfg = _klein(T.Ernie4_5_MoeConfig, num_hidden_layers=3, moe_num_experts=6, moe_k=2, moe_intermediate_size=48,
                     moe_num_shared_experts=1, moe_layer_start_index=1, moe_layer_interval=1, use_bias=False)
        self._vergleich(cfg, T.Ernie4_5_MoeForCausalLM, "ernie4_5-moe")

    def test_seed_oss_bias(self):
        self._vergleich(_klein(T.SeedOssConfig, attention_bias=True, attention_out_bias=True), T.SeedOssForCausalLM,
                        "seed_oss")

    def test_olmo3_fenster_yarn_nur_global(self):
        cfg = _klein(T.Olmo3Config, num_hidden_layers=4, sliding_window=4, max_position_embeddings=256,
                     rope_scaling={"rope_type": "yarn", "factor": 8.0, "original_max_position_embeddings": 32})
        self._vergleich(cfg, T.Olmo3ForCausalLM, "olmo3")

    def test_exaone4_fenster_global_ohne_rope(self):
        cfg = _klein(T.Exaone4Config, num_hidden_layers=4, sliding_window=4, sliding_window_pattern=4)
        self._vergleich(cfg, T.Exaone4ForCausalLM, "exaone4")

    def test_cohere2_parallel_layernorm_logit_skala(self):
        cfg = _klein(T.Cohere2Config, num_hidden_layers=4, sliding_window=4, logit_scale=0.5,
                     tie_word_embeddings=True)
        self._vergleich(cfg, T.Cohere2ForCausalLM, "cohere2")

    def test_command_r_parallel(self):
        cfg = _klein(T.CohereConfig, logit_scale=0.5, use_qk_norm=False, tie_word_embeddings=True)
        self._vergleich(cfg, T.CohereForCausalLM, "command-r")

    def test_glm4_sandwich_halbe_rope_paare(self):
        cfg = _klein(T.Glm4Config, partial_rotary_factor=0.5, attention_bias=True)
        self._vergleich(cfg, T.Glm4ForCausalLM, "glm4")

    def test_glm4moe_sigmoid_korrektur_gruppen(self):
        cfg = _klein(T.Glm4MoeConfig, num_hidden_layers=3, first_k_dense_replace=1, n_routed_experts=6,
                     n_shared_experts=1, num_experts_per_tok=2, moe_intermediate_size=48, n_group=3, topk_group=1,
                     routed_scaling_factor=2.0, norm_topk_prob=True, partial_rotary_factor=0.5, use_qk_norm=True,
                     attention_bias=True)
        self._vergleich(cfg, T.Glm4MoeForCausalLM, "glm4moe")

    def test_qwen2_moe_geteilter_experte(self):
        cfg = _klein(T.Qwen2MoeConfig, num_experts=4, num_experts_per_tok=2, moe_intermediate_size=48,
                     shared_expert_intermediate_size=64, norm_topk_prob=False, decoder_sparse_step=1,
                     mlp_only_layers=[])
        self._vergleich(cfg, T.Qwen2MoeForCausalLM, "qwen2moe")

    def test_gpt_oss_sinks_fenster_yarn_experten_mit_bias(self):
        # YaRN wie im echten Modell (Kopf 64, Original-Kontext 4096): die Rampe liegt zwischen
        # zwei ganzen Zahlen, gerundete Grenzen (falsch für gpt-oss) fielen hier auf
        cfg = _klein(T.GptOssConfig, num_hidden_layers=4, num_local_experts=4, num_experts_per_tok=2,
                     head_dim=64, max_position_embeddings=131072, sliding_window=4, attention_bias=True,
                     rope_theta=150000.0,
                     rope_scaling={"rope_type": "yarn", "factor": 32.0, "beta_fast": 32.0, "beta_slow": 1.0,
                                   "truncate": False, "original_max_position_embeddings": 4096})
        self._vergleich(cfg, T.GptOssForCausalLM, "gpt-oss")

    def test_yarn_tabellen_gerundet_und_ungerundet(self):
        """RoPE-Tabellen gegen transformers bis weit über den Original-Kontext: gpt-oss
        (truncate=False) und das übliche YaRN mit gerundeten Grenzen."""
        from ggufengine.models.common import RoPE
        for truncate in (False, True):
            rs = {"rope_type": "yarn", "factor": 32.0, "beta_fast": 32.0, "beta_slow": 1.0,
                  "truncate": truncate, "original_max_position_embeddings": 4096}
            cfg = _klein(T.GptOssConfig, head_dim=64, rope_theta=150000.0, rope_scaling=rs,
                         max_position_embeddings=131072)
            hf = T.models.gpt_oss.modeling_gpt_oss.GptOssRotaryEmbedding(cfg)
            pos = torch.arange(0, 8000, 7)
            cos, sin = hf(torch.zeros(1, 1, 64), pos[None])
            rope = RoPE(64, 150000.0, "cpu", 8000, yarn=(32.0, 4096, 32.0, 1.0, truncate),
                        mscale=0.1 * math.log(32.0) + 1.0)
            for ours, ref in ((rope.cos[pos], cos[0, :, :32]), (rope.sin[pos], sin[0, :, :32])):
                self.assertLess(float((ours - ref).abs().max()), 2e-4, f"truncate={truncate}")

    # -- Gemma --------------------------------------------------------------------------
    def test_gemma(self):
        self._vergleich(_klein(T.GemmaConfig, hidden_activation="gelu_pytorch_tanh"), T.GemmaForCausalLM, "gemma")

    def test_gemma2_softcap_sandwich_fenster(self):
        cfg = _klein(T.Gemma2Config, num_hidden_layers=4, sliding_window=4, query_pre_attn_scalar=D,
                     attn_logit_softcapping=2.0, final_logit_softcapping=3.0)
        self._vergleich(cfg, T.Gemma2ForCausalLM, "gemma2",
                        meta={"gemma2.attention.sliding_window": 4, "gemma2.attn_logit_softcapping": 2.0,
                              "gemma2.final_logit_softcapping": 3.0})

    def test_gemma3_fenster_muster_und_zwei_rope(self):
        cfg = _klein(T.Gemma3TextConfig, num_hidden_layers=6, sliding_window=4, query_pre_attn_scalar=D,
                     rope_scaling={"rope_type": "linear", "factor": 8.0}, rope_theta=1e6)
        self._vergleich(cfg, T.Gemma3ForCausalLM, "gemma3",
                        meta={"gemma3.attention.sliding_window": 4, "gemma3.rope.scaling.type": "linear",
                              "gemma3.rope.scaling.factor": 8.0})

    # -- Phi, OLMo ---------------------------------------------------------------------
    def _phi3(self, orig):
        """LongRoPE: kurze Faktoren, solange der Kontext in die Trainingslänge passt, sonst die
        langen – fest für den ganzen Kontext (wie llama.cpp; transformers wechselt je Länge)."""
        rot = int(D * 0.75)
        kurz, lang = [1.0 + 0.1 * i for i in range(rot // 2)], [2.0 + 0.3 * i for i in range(rot // 2)]
        cfg = _klein(T.Phi3Config, partial_rotary_factor=0.75, max_position_embeddings=4 * orig,
                     original_max_position_embeddings=orig,
                     rope_scaling={"type": "longrope", "short_factor": kurz, "long_factor": lang})
        self._vergleich(cfg, T.Phi3ForCausalLM, "phi3",
                        meta={"phi3.rope.scaling.original_context_length": orig},
                        tensoren={"rope_factors_short.weight": torch.tensor(kurz),
                                  "rope_factors_long.weight": torch.tensor(lang)})

    def test_phi3_longrope_kurz_teilrotation(self):
        self._phi3(orig=64)             # Kontext 48 <= 64: kurze Faktoren auf beiden Seiten

    def test_phi3_longrope_lang(self):
        self._phi3(orig=8)              # Kontext 48 > 8, Text > 8: lange Faktoren auf beiden Seiten

    def test_olmo2_nach_normen(self):
        self._vergleich(_klein(T.Olmo2Config), T.Olmo2ForCausalLM, "olmo2")


if __name__ == "__main__":
    unittest.main()
