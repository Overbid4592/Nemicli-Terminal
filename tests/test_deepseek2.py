"""DeepSeek V2/V3 (Bauart deepseek2) gegen transformers: winzige Zufallsmodelle (nichts wird
geladen), als GGUF geschrieben, von beiden gerechnet – die Logits müssen übereinstimmen.

python -m unittest tests.test_deepseek2
"""

import gc
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

V, N_CTX = 96, 48


def _cfg(cls, **kw):
    basis = dict(vocab_size=V, hidden_size=64, intermediate_size=96, moe_intermediate_size=32, num_hidden_layers=3,
                 first_k_dense_replace=1, n_routed_experts=6, n_shared_experts=1, num_experts_per_tok=2,
                 num_attention_heads=4, num_key_value_heads=4, kv_lora_rank=32, qk_nope_head_dim=16,
                 qk_rope_head_dim=8, v_head_dim=16, max_position_embeddings=64, rms_norm_eps=1e-6,
                 tie_word_embeddings=False, attn_implementation="eager", pad_token_id=0, bos_token_id=1,
                 eos_token_id=2, routed_scaling_factor=1.5)
    basis.update(kw)
    return cls(**basis)


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class DeepSeekGegenTransformers(unittest.TestCase):

    def _vergleich(self, cfg, modell_cls, n=14, vorab=9, teilen=False):
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
        m, t = B.umwandeln(hf, "deepseek2")
        if teilen:                                   # neuere Dateien: k_b/v_b schon getrennt
            for name in [k for k in t if k.endswith("attn_kv_b.weight")]:
                kv_b = t.pop(name).view(cfg.num_attention_heads, cfg.qk_nope_head_dim + cfg.v_head_dim, -1)
                t[name.replace("kv_b", "k_b")] = kv_b[:, :cfg.qk_nope_head_dim].transpose(1, 2).contiguous()
                t[name.replace("kv_b", "v_b")] = kv_b[:, cfg.qk_nope_head_dim:].contiguous()
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, m, t)
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device("cpu"), torch.float32, N_CTX, quant=False)
                self.assertEqual(type(modell).__name__, "DeepSeek2Model")
                ids = torch.randint(0, V, (n,))
                with torch.no_grad():
                    ref = hf(ids[None]).logits[0].float()
                aus = [modell.forward(ids[:vorab].tolist(), 0)]
                aus += [modell.forward([int(ids[j])], j) for j in range(vorab, n)]
                fehler = [(j, float((a - ref[j]).abs().max())) for j, a in zip(range(vorab - 1, n), aus)]
                del modell, aus
            finally:
                gc.collect()
                gg.close()
            for j, diff in fehler:
                self.assertLess(diff, 2e-3 * max(1.0, float(ref[j].abs().max())), f"Position {j}: {diff:.2e}")
        finally:
            os.unlink(pfad)

    def test_v2_lite_q_direkt_softmax(self):
        self._vergleich(_cfg(T.DeepseekV2Config, q_lora_rank=None, topk_method="greedy", n_group=1, topk_group=1),
                        T.DeepseekV2ForCausalLM)

    def test_v2_q_lora_gruppen(self):
        # eine erlaubte Gruppe: die beiden besten Experten liegen oft in verschiedenen Gruppen
        self._vergleich(_cfg(T.DeepseekV2Config, q_lora_rank=24, topk_method="group_limited_greedy",
                             n_group=3, topk_group=1), T.DeepseekV2ForCausalLM)

    def test_v3_sigmoid_bias_gruppen_yarn(self):
        cfg = _cfg(T.DeepseekV3Config, q_lora_rank=24, n_group=3, topk_group=2, norm_topk_prob=True,
                   routed_scaling_factor=2.5, rope_interleave=True,
                   rope_scaling={"rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 16,
                                 "mscale": 1.0, "mscale_all_dim": 1.0, "beta_fast": 32.0, "beta_slow": 1.0})
        self._vergleich(cfg, T.DeepseekV3ForCausalLM)

    def test_getrennte_k_b_v_b(self):
        self._vergleich(_cfg(T.DeepseekV2Config, q_lora_rank=None, topk_method="greedy", n_group=1, topk_group=1),
                        T.DeepseekV2ForCausalLM, teilen=True)


if __name__ == "__main__":
    unittest.main()
