"""Qwen3-VL (Bauart qwen3vl + mmproj mit DeepStack) gegen transformers: winzige Zufallsmodelle
(nichts wird geladen), als GGUF geschrieben, von beiden gerechnet – Ergebnisse müssen übereinstimmen.

python -m unittest tests.test_qwen3vl
"""

import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
    T.Qwen3VLTextModel, T.Qwen3VLVisionModel
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None

V, E, N_CTX, SECTIONS = 96, 64, 48, [3, 3, 2]


def _gguf_datei(meta, tensoren):
    import gguf_testbau as B
    fd, pfad = tempfile.mkstemp(suffix=".gguf")
    os.close(fd)
    B.schreibe_gguf(pfad, meta, tensoren)
    return pfad


def _zufall(modell):
    torch.manual_seed(0)
    with torch.no_grad():
        for p in modell.parameters():
            p.normal_(0, 0.2)
    return modell


def _text_gguf(hf, n_deepstack):
    """Qwen3VLTextModel -> GGUF-Schema qwen3vl (wie der llama.cpp-Konverter, Kopf gebunden)."""
    c, sd = hf.config, hf.state_dict()
    a = lambda s: f"qwen3vl.{s}"
    meta = {"general.architecture": "qwen3vl", a("block_count"): c.num_hidden_layers,
            a("context_length"): c.max_position_embeddings, a("embedding_length"): c.hidden_size,
            a("feed_forward_length"): c.intermediate_size, a("attention.head_count"): c.num_attention_heads,
            a("attention.head_count_kv"): c.num_key_value_heads, a("attention.key_length"): c.head_dim,
            a("attention.value_length"): c.head_dim, a("attention.layer_norm_rms_epsilon"): float(c.rms_norm_eps),
            a("rope.freq_base"): float(c.rope_parameters["rope_theta"]), a("rope.dimension_sections"): SECTIONS + [0],
            a("n_deepstack_layers"): n_deepstack}
    t = {"token_embd.weight": sd["embed_tokens.weight"], "output_norm.weight": sd["norm.weight"]}
    namen = {"input_layernorm": "attn_norm", "post_attention_layernorm": "ffn_norm",
             "self_attn.q_proj": "attn_q", "self_attn.k_proj": "attn_k", "self_attn.v_proj": "attn_v",
             "self_attn.o_proj": "attn_output", "self_attn.q_norm": "attn_q_norm", "self_attn.k_norm": "attn_k_norm",
             "mlp.gate_proj": "ffn_gate", "mlp.up_proj": "ffn_up", "mlp.down_proj": "ffn_down"}
    for i in range(c.num_hidden_layers):
        for hf_name, name in namen.items():
            t[f"blk.{i}.{name}.weight"] = sd[f"layers.{i}.{hf_name}.weight"]
    return meta, t


def _vision_gguf(hf):
    """Qwen3VLVisionModel -> mmproj im Schema des llama.cpp-Konverters (qwen3vl_merger + DeepStack)."""
    c, sd = hf.config, hf.state_dict()
    tiefe = list(c.deepstack_visual_indexes)
    meta = {"clip.vision.projector_type": "qwen3vl_merger", "clip.has_vision_encoder": True,
            "clip.vision.embedding_length": c.hidden_size, "clip.vision.attention.head_count": c.num_heads,
            "clip.vision.block_count": c.depth, "clip.vision.patch_size": c.patch_size,
            "clip.vision.projection_dim": c.out_hidden_size, "clip.vision.spatial_merge_size": c.spatial_merge_size,
            "clip.vision.attention.layer_norm_epsilon": 1e-6,
            "clip.vision.is_deepstack_layers": [i in tiefe for i in range(c.depth)]}
    w = sd["patch_embed.proj.weight"]                                  # (E, C, T, P, P)
    t = {"v.patch_embd.weight": w[:, :, 0].contiguous(), "v.patch_embd.weight.1": w[:, :, 1].contiguous(),
         "v.patch_embd.bias": sd["patch_embed.proj.bias"], "v.position_embd.weight": sd["pos_embed.weight"]}
    namen = {"norm1": "ln1", "norm2": "ln2", "attn.qkv": "attn_qkv", "attn.proj": "attn_out",
             "mlp.linear_fc1": "ffn_up", "mlp.linear_fc2": "ffn_down"}
    for i in range(c.depth):
        for hf_name, name in namen.items():
            for x in ("weight", "bias"):
                t[f"v.blk.{i}.{name}.{x}"] = sd[f"blocks.{i}.{hf_name}.{x}"]
    for x in ("weight", "bias"):
        t[f"v.post_ln.{x}"] = sd[f"merger.norm.{x}"]
        t[f"mm.0.{x}"] = sd[f"merger.linear_fc1.{x}"]
        t[f"mm.2.{x}"] = sd[f"merger.linear_fc2.{x}"]
        for j, blk in enumerate(tiefe):
            for hf_name, name in (("norm", "norm"), ("linear_fc1", "fc1"), ("linear_fc2", "fc2")):
                t[f"v.deepstack.{blk}.{name}.{x}"] = sd[f"deepstack_merger_list.{j}.{hf_name}.{x}"]
    return meta, t


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class TextGegenTransformers(unittest.TestCase):
    N_TIEF = 2

    def setUp(self):
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        cfg = T.Qwen3VLTextConfig(vocab_size=V, hidden_size=E, intermediate_size=96, num_hidden_layers=3,
                                  num_attention_heads=4, num_key_value_heads=2, head_dim=16,
                                  max_position_embeddings=64, attn_implementation="eager",
                                  rope_parameters={"rope_type": "default", "rope_theta": 5_000_000.0,
                                                   "mrope_section": SECTIONS, "mrope_interleaved": True})
        self.hf = _zufall(T.Qwen3VLTextModel(cfg).eval().float())
        self.pfad = _gguf_datei(*_text_gguf(self.hf, self.N_TIEF))
        self.gg = GGUFFile(self.pfad)
        self.modell = build_model(self.gg, torch.device("cpu"), torch.float32, N_CTX, quant=False)

    def tearDown(self):
        del self.modell
        gc.collect()
        self.gg.close()
        os.unlink(self.pfad)

    def _ref(self, emb, pos3, maske=None, tief=None):
        with torch.no_grad():
            h = self.hf(inputs_embeds=emb[None], position_ids=pos3.t()[:, None, :],
                        visual_pos_masks=None if maske is None else maske[None],
                        deepstack_visual_embeds=tief).last_hidden_state[0]
            return h @ self.hf.embed_tokens.weight.t()

    def test_bauart_bekannt(self):
        self.assertEqual(type(self.modell).__name__, "Qwen3VLModel")

    def test_nur_text_auch_schrittweise(self):
        ids = torch.randint(0, V, (12,))
        pos3 = torch.arange(12)[:, None].expand(-1, 3)
        ref = self._ref(self.hf.embed_tokens(ids).detach(), pos3)
        aus = [self.modell.forward(ids[:8].tolist(), 0)] + [self.modell.forward([int(ids[j])], j) for j in range(8, 12)]
        for j, a in zip(range(7, 12), aus):
            self.assertLess(float((a - ref[j]).abs().max()), 2e-3 * max(1.0, float(ref[j].abs().max())), j)

    def test_bild_mit_deepstack_und_mrope(self):
        """2x3 Bild-Token ab Position 2: Einbettung ersetzt, DeepStack nach den ersten Schichten addiert."""
        torch.manual_seed(1)
        ids = torch.randint(0, V, (11,))
        zeilen = torch.arange(2, 8)
        haupt = torch.randn(6, E)
        tief = [torch.randn(6, E) for _ in range(self.N_TIEF)]
        pos3 = torch.tensor([(0, 0, 0), (1, 1, 1)] + [(2, 2 + r // 3, 2 + r % 3) for r in range(6)]
                            + [(5, 5, 5), (6, 6, 6), (7, 7, 7)])
        emb = self.hf.embed_tokens(ids).detach().clone()
        emb[zeilen] = haupt
        maske = torch.zeros(11, dtype=torch.bool)
        maske[zeilen] = True
        ref = self._ref(emb, pos3, maske, tief)
        ohne = self._ref(emb, pos3)
        inject = (zeilen, torch.cat([haupt] + tief, dim=1))
        a = self.modell.forward(ids.tolist(), 0, inject, pos3)
        self.assertLess(float((a - ref[-1]).abs().max()), 2e-3 * max(1.0, float(ref[-1].abs().max())))
        self.assertGreater(float((ohne[-1] - ref[-1]).abs().max()), 1e-2)      # DeepStack wirkt wirklich


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class VisionGegenTransformers(unittest.TestCase):

    def test_encoder_mit_deepstack(self):
        from ggufengine.vision import load_projector
        cfg = T.Qwen3VLVisionConfig(depth=4, hidden_size=32, intermediate_size=48, num_heads=2, patch_size=4,
                                    spatial_merge_size=2, temporal_patch_size=2, out_hidden_size=24,
                                    num_position_embeddings=16, deepstack_visual_indexes=[1, 2])
        cfg._attn_implementation = "eager"
        hf = _zufall(T.Qwen3VLVisionModel(cfg).eval().float())
        pfad = _gguf_datei(*_vision_gguf(hf))
        try:
            v = load_projector(pfad, "cpu", torch.float32)
            self.assertEqual((v.n_out, v.n_deepstack, v.deepstack), (24, 2, [1, 2]))
            torch.manual_seed(2)
            pix = torch.randn(3, 16, 24)                                 # Raster 4x6 -> 2x3 Token
            P, m, gy, gx = 4, 2, 4, 6
            x = pix[None].expand(2, -1, -1, -1).reshape(1, 2, 3, gy // m, m, P, gx // m, m, P)
            werte = x.permute(0, 3, 6, 4, 7, 2, 1, 5, 8).reshape(gy * gx, 3 * 2 * P * P)
            with torch.no_grad():
                out = hf(werte, grid_thw=torch.tensor([[1, gy, gx]]))
            ref = torch.cat([out.pooler_output] + list(out.deepstack_features), dim=1)
            v.load()
            v.pixels = lambda image, tokens: pix
            emb, raster = v.encode(None)
            v.unload()
            self.assertEqual(raster, (2, 3))
            self.assertEqual(tuple(emb.shape), (6, 24 * 3))
            self.assertLess(float((emb - ref).abs().max()), 2e-3 * max(1.0, float(ref.abs().max())))
            gc.collect()
            v.gg.close()
        finally:
            os.unlink(pfad)

    def test_unstimmige_deepstack_angaben_abgelehnt(self):
        from ggufengine.gguf import GGUFError, GGUFFile
        from ggufengine.vision import _deepstack_blocks
        cfg = T.Qwen3VLVisionConfig(depth=3, hidden_size=16, intermediate_size=16, num_heads=2, patch_size=4,
                                    out_hidden_size=8, num_position_embeddings=16, deepstack_visual_indexes=[1])
        meta, t = _vision_gguf(T.Qwen3VLVisionModel(cfg))
        meta["clip.vision.is_deepstack_layers"] = [True, False, False]
        pfad = _gguf_datei(meta, t)
        try:
            gg = GGUFFile(pfad)
            with self.assertRaises(GGUFError):
                _deepstack_blocks(gg, 3)
            gg.close()
        finally:
            os.unlink(pfad)


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class MotorAnbindung(unittest.TestCase):

    def test_deepstack_mmproj_braucht_deepstack_modell(self):
        from types import SimpleNamespace as NS
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.model = NS(supports_images=True)
        e.gg = NS(architecture="qwen35", get=lambda k, s=None: 24)
        e.tokenizer = NS(special={"<|vision_start|>": 1, "<|image_pad|>": 2, "<|vision_end|>": 3})
        e._mrope, e.load_phases, e.device = True, {}, "cpu"
        bild = NS(n_out=24, n_deepstack=3, uses_grid=True,
                  marks=("<|vision_start|>", "<|image_pad|>", "<|vision_end|>"))
        import ggufengine.vision as GV
        alt = GV.load_projector
        GV.load_projector = lambda pfad, geraet: bild
        try:
            with self.assertRaises(ValueError):
                e.enable_vision("x.gguf")
        finally:
            GV.load_projector = alt

    def test_bauart_in_der_ladeliste(self):
        from ggufengine.models import SUPPORTED_ARCHS
        import hfladen
        self.assertIn("qwen3vl", SUPPORTED_ARCHS)
        self.assertIn("qwen3vl", hfladen.unterstuetzt())


if __name__ == "__main__":
    unittest.main()
