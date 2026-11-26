"""Neue Bauarten und Formate im GGUF-Motor – geprüft gegen transformers mit Mini-Zufallsmodellen."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))


class SeedOssFormat(unittest.TestCase):
    def _tok(self):
        spezial = {"<seed:bos>": 1, "<seed:eos>": 2, "<seed:think>": 3, "</seed:think>": 4}
        return NS(special=spezial, special_id=spezial.__getitem__, bos_id=None, encode=lambda s: [9] * len(s))

    def test_rollen_und_denkmarken(self):
        from ggufengine.chat import ChatFormatter, Message, detect_format
        self.assertEqual(detect_format("{{ '<seed:bos>' + role }}", "llama"), "seed_oss")
        self.assertEqual(detect_format(None, "seed_oss"), "seed_oss")
        self.assertEqual(detect_format("[|system|] [|assistant|] [|endofturn|] [|tool|]", "exaone4"), "exaone3")
        f = ChatFormatter(self._tok(), "seed_oss")
        ids = f.encode([Message("user", "ab"), Message("assistant", "<seed:think>x</seed:think>c")])
        # [B] "user" "\n" a b [E] [B] "assistant" "\n" c [E] [B] "assistant" "\n"
        self.assertEqual(ids, [1] + [9] * 5 + [9, 9, 2] + [1] + [9] * 10 + [9, 2] + [1] + [9] * 10)
        self.assertEqual(f.denk_marken(), ([3], [4]))

    def test_kimi_format(self):
        from ggufengine.chat import ChatFormatter, Message, detect_format
        self.assertEqual(detect_format("<|im_assistant|>assistant<|im_middle|>", "deepseek2"), "kimi_k2")
        self.assertEqual(detect_format(None, "kimi-linear"), "kimi_k2")
        spezial = {"<|im_user|>": 1, "<|im_assistant|>": 2, "<|im_middle|>": 3, "<|im_end|>": 4, "<|im_system|>": 5}
        tok = NS(special=spezial, special_id=spezial.__getitem__, bos_id=None, encode=lambda s: [9] * len(s))
        ids = ChatFormatter(tok, "kimi_k2").encode([Message("user", "hi")])
        # [U] "user" [M] h i [E] [A] "assistant" [M]
        self.assertEqual(ids, [1] + [9] * 4 + [3, 9, 9, 4, 2] + [9] * 9 + [3])

    def test_denktrenner(self):
        sys.path.insert(0, str(ROOT / "engines"))
        import gguflokal as G
        t = G._Denktrenner("seed_oss")
        raus = t("<seed:think>überlegen</seed:think>Antwort") + t.ende()
        self.assertIn(("thinking", "überlegen"), raus)
        self.assertEqual("".join(x for a, x in raus if a == "text"), "Antwort")


try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None


def vergleich(test, hf, arch, *, n=46, vorab=40, umwandeln=None, toleranz=2e-3, n_ctx=64, extra_meta=None):
    """Zufallsgewichte -> GGUF -> Motor; Logits beim Einlesen (vorab Token) und in Einzelschritten
    gleich transformers. Gibt das Motor-Modell nicht zurück (wird freigegeben)."""
    import gc
    import os
    import tempfile
    import gguf_testbau as B
    from ggufengine.gguf import GGUFFile
    from ggufengine.models import build_model
    torch.manual_seed(0)
    hf = hf.eval().float()
    with torch.no_grad():
        for p in hf.parameters():
            p.normal_(0, 0.2)
    m, t = (umwandeln or B.umwandeln)(hf, arch)
    m.update(extra_meta or {})
    fd, pfad = tempfile.mkstemp(suffix=".gguf")
    os.close(fd)
    try:
        B.schreibe_gguf(pfad, m, t)
        gg = GGUFFile(pfad)
        try:
            modell = build_model(gg, torch.device("cpu"), torch.float32, n_ctx, quant=False)
            ids = torch.randint(0, hf.config.vocab_size, (n,))
            with torch.no_grad():
                ref = hf(ids[None]).logits[0].float()
                aus = [modell.forward(ids[:vorab].tolist(), 0)]
                aus += [modell.forward([int(ids[j])], j) for j in range(vorab, n)]
            fehler = [(j, float((a - ref[j]).abs().max())) for j, a in zip(range(vorab - 1, n), aus)]
            del modell, aus
        finally:
            gc.collect()
            gg.close()
    finally:
        os.unlink(pfad)
    for j, diff in fehler:
        test.assertLess(diff, toleranz * max(1.0, float(ref[j].abs().max())), f"{arch}: Position {j} weicht um {diff:.2e} ab")


def _q35_cfg(cls, **kw):
    basis = dict(vocab_size=96, hidden_size=64, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=2,
                 head_dim=16, linear_num_key_heads=2, linear_num_value_heads=4, linear_key_head_dim=16,
                 linear_value_head_dim=16, linear_conv_kernel_dim=4, intermediate_size=96, max_position_embeddings=128,
                 rms_norm_eps=1e-6, tie_word_embeddings=False)
    basis.update(kw)
    return cls(**basis)


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class QwenDeltaNet(unittest.TestCase):
    def test_qwen35_dicht(self):
        vergleich(self, T.Qwen3_5ForCausalLM(_q35_cfg(T.Qwen3_5TextConfig)), "qwen35")

    def test_qwen35_moe(self):
        cfg = _q35_cfg(T.Qwen3_5MoeTextConfig, num_experts=8, num_experts_per_tok=2, moe_intermediate_size=32,
                       shared_expert_intermediate_size=48)
        vergleich(self, T.Qwen3_5MoeForCausalLM(cfg), "qwen35moe")

    def test_qwen3_next(self):
        cfg = _q35_cfg(T.Qwen3NextConfig, num_experts=8, num_experts_per_tok=2, moe_intermediate_size=32,
                       shared_expert_intermediate_size=48)
        vergleich(self, T.Qwen3NextForCausalLM(cfg), "qwen3next")

    def test_mtp_schichten_werden_uebersprungen(self):
        """Echte Qwen-3.6-Dateien mit MTP-Kopf: eine Schicht mehr, nextn_predict_layers = 1."""
        import gguf_testbau as B
        cfg = _q35_cfg(T.Qwen3_5MoeTextConfig, num_experts=8, num_experts_per_tok=2, moe_intermediate_size=32,
                       shared_expert_intermediate_size=48)

        def mit_mtp(hf, arch):
            m, t = B.umwandeln(hf, arch)
            n = hf.config.num_hidden_layers
            m[f"{arch}.block_count"] = n + 1
            m[f"{arch}.nextn_predict_layers"] = 1
            t[f"blk.{n}.nextn.eh_proj.weight"] = torch.zeros(64, 128)
            return m, t
        vergleich(self, T.Qwen3_5MoeForCausalLM(cfg), "qwen35moe", umwandeln=mit_mtp)


class Speicherschaetzung(unittest.TestCase):
    def test_nur_attention_schichten_zaehlen(self):
        sys.path.insert(0, str(ROOT / "engines"))
        import gguflokal as G

        def gg(arch, kv, n=8):
            werte = {f"{arch}.block_count": n, f"{arch}.attention.head_count_kv": kv, f"{arch}.attention.key_length": 16,
                     f"{arch}.full_attention_interval": 4, f"{arch}.attention.head_count": 4}
            return NS(architecture=arch, get=lambda k, s=None: werte.get(k, s), require=lambda k: werte[k])
        self.assertEqual(G._kv_bytes(gg("qwen35moe", 2)), 2 * 2 * 16 * 2 * 2)         # Schichten 3 und 7
        self.assertEqual(G._kv_bytes(gg("qwen3next", 2)), 2 * 2 * 16 * 2 * 2)
        self.assertEqual(G._kv_bytes(gg("nemotron_h", [0, 2, 0, 0, 0, 2, 0, 0])), 2 * 2 * 16 * 2 * 2)


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class MambaHybride(unittest.TestCase):
    BASIS = dict(vocab_size=96, hidden_size=64, num_attention_heads=4, num_key_value_heads=2, intermediate_size=96,
                 max_position_embeddings=128, tie_word_embeddings=False)

    def _granite(self, **kw):
        # eine SSM-Gruppe wie bei echten Granite-4-H-Modellen: transformers normiert dort über das
        # ganze Innere, llama.cpp (und der Motor) je Gruppe – bei einer Gruppe dasselbe
        cfg = dict(self.BASIS, layer_types=["mamba", "attention", "mamba", "mamba"], num_hidden_layers=4,
                   mamba_n_heads=8, mamba_d_head=16, mamba_d_state=16, mamba_n_groups=1, mamba_expand=2,
                   mamba_d_conv=4, shared_intermediate_size=48, embedding_multiplier=1.5, residual_multiplier=0.7,
                   attention_multiplier=0.2, logits_scaling=2.0)
        cfg.update(kw)
        return T.GraniteMoeHybridForCausalLM(T.GraniteMoeHybridConfig(**cfg))

    def test_granite4h_moe_ohne_rope(self):
        vergleich(self, self._granite(num_local_experts=4, num_experts_per_tok=2, position_embedding_type=None),
                  "granitehybrid")

    def test_granite4h_dicht_mit_rope(self):
        vergleich(self, self._granite(num_local_experts=0, position_embedding_type="rope"), "granitehybrid")

    def _nemotron(self, muster, **kw):
        cfg = dict(self.BASIS, hybrid_override_pattern=muster, num_hidden_layers=len(muster), head_dim=16,
                   mamba_num_heads=8, mamba_head_dim=16, ssm_state_size=16, n_groups=2, conv_kernel=4,
                   layer_norm_epsilon=1e-5)
        cfg.update(kw)
        return T.NemotronHForCausalLM(T.NemotronHConfig(**cfg))

    def test_nemotron_h(self):
        vergleich(self, self._nemotron("M*-M"), "nemotron_h")

    def test_nemotron_h_moe(self):
        hf = self._nemotron("M*EM", n_routed_experts=8, num_experts_per_tok=2, moe_intermediate_size=32,
                            n_shared_experts=1, moe_shared_expert_intermediate_size=32, routed_scaling_factor=1.5)
        vergleich(self, hf, "nemotron_h_moe")

    def test_falcon_h1(self):
        cfg = dict(self.BASIS, num_hidden_layers=3, head_dim=16, mamba_n_heads=8, mamba_d_head=16, mamba_d_ssm=128,
                   mamba_d_state=16, mamba_n_groups=2, mamba_d_conv=4, embedding_multiplier=1.3, lm_head_multiplier=0.8,
                   key_multiplier=0.9, attention_in_multiplier=1.1, attention_out_multiplier=0.7,
                   ssm_in_multiplier=1.2, ssm_out_multiplier=0.6, ssm_multipliers=[1.0, 0.9, 1.1, 0.8, 1.2],
                   mlp_multipliers=[1.1, 0.9])
        vergleich(self, T.FalconH1ForCausalLM(T.FalconH1Config(**cfg)), "falcon-h1")


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class Lfm2(unittest.TestCase):
    BASIS = dict(vocab_size=96, hidden_size=64, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=2,
                 intermediate_size=96, max_position_embeddings=128,
                 layer_types=["conv", "full_attention", "conv", "full_attention"])

    def test_lfm2(self):
        vergleich(self, T.Lfm2ForCausalLM(T.Lfm2Config(**self.BASIS)), "lfm2")

    def test_lfm2_moe(self):
        cfg = T.Lfm2MoeConfig(**self.BASIS, num_dense_layers=1, num_experts=8, num_experts_per_tok=2,
                              moe_intermediate_size=32, routed_scaling_factor=1.0)
        vergleich(self, T.Lfm2MoeForCausalLM(cfg), "lfm2moe")


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class Llama4(unittest.TestCase):
    def _hf(self, **kw):
        cfg = dict(vocab_size=96, hidden_size=64, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=2,
                   head_dim=16, intermediate_size=32, intermediate_size_mlp=96, num_local_experts=8, num_experts_per_tok=1,
                   interleave_moe_layer_step=2, max_position_embeddings=128, attention_chunk_size=16, floor_scale=16)
        cfg.update(kw)
        return T.Llama4ForCausalLM(T.Llama4TextConfig(**cfg))

    def test_llama4_bloecke_nope_temperatur(self):
        """Blockgröße 16 statt 8192, damit Block-Attention und Temperatur im Mini-Modell greifen."""
        from unittest import mock
        from ggufengine.models import llama4 as L4
        with mock.patch.object(L4, "BLOCK", 16):
            vergleich(self, self._hf(), "llama4")

    def test_llama4_llama3_faktoren(self):
        from unittest import mock
        from ggufengine.models import llama4 as L4
        rp = {"rope_type": "llama3", "rope_theta": 500000.0, "factor": 8.0, "low_freq_factor": 1.0,
              "high_freq_factor": 4.0, "original_max_position_embeddings": 32}
        with mock.patch.object(L4, "BLOCK", 16):
            vergleich(self, self._hf(rope_parameters=rp), "llama4")


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class KimiLinear(unittest.TestCase):
    def test_kimi_linear(self):
        cfg = T.KimiLinearConfig(
            vocab_size=96, hidden_size=64, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=4,
            intermediate_size=96, max_position_embeddings=128, pad_token_id=0, bos_token_id=1, eos_token_id=2,
            layer_types=["linear_attention", "full_attention", "linear_attention", "linear_attention"],
            mlp_layer_types=["dense", "sparse", "sparse", "sparse"], linear_num_heads=4, linear_head_dim=16,
            kv_lora_rank=32, qk_nope_head_dim=16, qk_rope_head_dim=8, v_head_dim=16, num_local_experts=8,
            num_experts_per_tok=2, moe_intermediate_size=32, routed_scaling_factor=1.7, rms_norm_eps=1e-6)
        vergleich(self, T.KimiLinearForCausalLM(cfg), "kimi-linear")


if __name__ == "__main__":
    unittest.main()
