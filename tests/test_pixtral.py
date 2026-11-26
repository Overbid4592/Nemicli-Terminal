"""Pixtral-Bildteil (Mistral Small 3.x, Ministral 3) gegen transformers: ein winziges
Mistral3ForConditionalGeneration (Zufallsgewichte, nichts wird geladen), Sprachteil und
mmproj als GGUF geschrieben wie llama.cpp (Q/K des Bildteils umsortiert), dann
Bild-Einbettungen und Logits mit Bild verglichen.

python -m unittest tests.test_pixtral
"""

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

IMG, BREAK, END = 10, 12, 13                        # Token-Nummern wie im echten Mistral-Tokenizer
PATCH, MERGE = 14, 2
H_PIX, W_PIX = 56, 84                               # 4 x 6 Patches -> 2 x 3 Bild-Token


def _modell():
    vis = T.PixtralVisionConfig(hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=2,
                                head_dim=16, image_size=112, patch_size=PATCH, rope_theta=10000.0,
                                hidden_act="silu")                  # wie Pixtral 12B / Ministral 3 (clip.use_silu)
    txt = T.MistralConfig(vocab_size=96, hidden_size=64, intermediate_size=96, num_hidden_layers=2,
                          num_attention_heads=4, num_key_value_heads=2, head_dim=16, max_position_embeddings=64,
                          rms_norm_eps=1e-5, tie_word_embeddings=False, pad_token_id=11)
    cfg = T.Mistral3Config(vision_config=vis, text_config=txt, image_token_index=IMG, spatial_merge_size=MERGE,
                           multimodal_projector_bias=False, projector_hidden_act="gelu", vision_feature_layer=-1)
    cfg._attn_implementation = "eager"
    cfg.vision_config._attn_implementation = "eager"
    cfg.text_config._attn_implementation = "eager"
    torch.manual_seed(0)
    hf = T.Mistral3ForConditionalGeneration(cfg).eval().float()
    with torch.no_grad():
        for p in hf.parameters():
            p.normal_(0, 0.1)
        for name, p in hf.named_parameters():        # spitze Aufmerksamkeit im Bildteil: die Position zählt
            if "vision_tower" in name and name.endswith(("q_proj.weight", "k_proj.weight")):
                p.normal_(0, 1.0)
    return hf


def _mmproj(hf, umsortieren=True):
    """mmproj im Schema von llama.cpp (projector_type pixtral)."""
    import gguf_testbau as B
    vc = hf.config.vision_config
    sd = hf.state_dict()
    vt, mp = "model.vision_tower.", "model.multi_modal_projector."
    perm = (lambda w: B._permute(w, vc.num_attention_heads)) if umsortieren else (lambda w: w)
    meta = {"general.architecture": "clip", "clip.projector_type": "pixtral", "clip.has_vision_encoder": True,
            "clip.vision.embedding_length": vc.hidden_size, "clip.vision.feed_forward_length": vc.intermediate_size,
            "clip.vision.block_count": vc.num_hidden_layers, "clip.vision.attention.head_count": vc.num_attention_heads,
            "clip.vision.patch_size": vc.patch_size, "clip.vision.image_size": vc.image_size,
            "clip.vision.spatial_merge_size": MERGE, "clip.vision.attention.layer_norm_epsilon": 1e-5,
            "clip.rope.freq_base": B.rope_theta(vc), "clip.use_silu": True,
            "clip.vision.projection_dim": hf.config.text_config.hidden_size,
            "clip.vision.image_mean": [0.5, 0.5, 0.5], "clip.vision.image_std": [0.5, 0.5, 0.5]}
    t = {"v.patch_embd.weight": sd[vt + "patch_conv.weight"], "v.pre_ln.weight": sd[vt + "ln_pre.weight"],
         "mm.input_norm.weight": sd[mp + "norm.weight"], "mm.patch_merger.weight": sd[mp + "patch_merger.merging_layer.weight"],
         "mm.1.weight": sd[mp + "linear_1.weight"], "mm.2.weight": sd[mp + "linear_2.weight"]}
    for i in range(vc.num_hidden_layers):
        p, b = f"{vt}transformer.layers.{i}.", f"v.blk.{i}."
        t[b + "attn_q.weight"] = perm(sd[p + "attention.q_proj.weight"])
        t[b + "attn_k.weight"] = perm(sd[p + "attention.k_proj.weight"])
        t[b + "attn_v.weight"] = sd[p + "attention.v_proj.weight"]
        t[b + "attn_out.weight"] = sd[p + "attention.o_proj.weight"]
        t[b + "ln1.weight"] = sd[p + "attention_norm.weight"]
        t[b + "ln2.weight"] = sd[p + "ffn_norm.weight"]
        for x in ("gate", "up", "down"):
            t[b + f"ffn_{x}.weight"] = sd[p + f"feed_forward.{x}_proj.weight"]
    return meta, t


def _sprachteil(hf):
    """Der Sprachteil als MistralForCausalLM -> GGUF-Schema mistral3."""
    import gguf_testbau as B
    lm = T.MistralForCausalLM(hf.config.text_config).eval().float()
    sd = {k.replace("model.language_model.", "model."): v for k, v in hf.state_dict().items()
          if k.startswith("model.language_model.")}
    sd["lm_head.weight"] = hf.lm_head.weight
    lm.load_state_dict(sd)
    return B.umwandeln(lm, "mistral3")


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class PixtralGegenTransformers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hf = _modell()
        torch.manual_seed(1)
        self.pix = torch.randn(3, H_PIX, W_PIX)
        self.sizes = torch.tensor([[H_PIX, W_PIX]])

    def _vision(self, umsortieren=True):
        import gc
        import gguf_testbau as B
        from ggufengine.vision import load_projector
        pfad = os.path.join(self.tmp.name, f"mmproj-{int(umsortieren)}.gguf")
        B.schreibe_gguf(pfad, *_mmproj(self.hf, umsortieren))
        v = load_projector(pfad, "cpu", torch.float32)
        v.load()
        try:
            emb, grid = v.encode_pixels(self.pix)
        finally:
            v.unload()
            v.gg.close()
            del v
            gc.collect()
        return emb, grid

    def _referenz_bild(self):
        with torch.no_grad():
            aus = self.hf.model.get_image_features(pixel_values=self.pix[None], image_sizes=self.sizes)
        # transformers 5: projizierte Merkmale je Bild in pooler_output; 4: Liste je Bild
        return (aus.pooler_output if getattr(aus, "pooler_output", None) is not None else aus)[0].float()

    def test_bild_einbettungen(self):
        emb, grid = self._vision()
        ref = self._referenz_bild()
        self.assertEqual(grid, (H_PIX // PATCH // MERGE, W_PIX // PATCH // MERGE))
        self.assertEqual(tuple(emb.shape), tuple(ref.shape))
        self.assertLess(float((emb - ref).abs().max()), 1e-6)

    def test_gegenprobe_ohne_umsortierung(self):
        emb, _ = self._vision(umsortieren=False)          # Q/K wie in transformers: falsche RoPE-Paare
        ref = self._referenz_bild()
        self.assertGreater(float((emb - ref).abs().max()), 1e-4)

    def test_logits_mit_bild(self):
        """Ende zu Ende: Bild-Token zeilenweise wie der ChatFormatter, Einbettungen über inject."""
        import gc
        import gguf_testbau as B
        from types import SimpleNamespace as NS
        from ggufengine.chat import ChatFormatter, Message
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        emb, grid = self._vision()
        emb.grid = grid
        special = {"[INST]": 3, "[/INST]": 4, "[IMG]": IMG, "[IMG_BREAK]": BREAK, "[IMG_END]": END, "</s>": 2}
        tok = NS(special=special, special_id=special.__getitem__, bos_id=1, add_bos=True,
                 encode=lambda s: [20 + (ord(c) % 60) for c in s])
        ids = ChatFormatter(tok, "mistral-v7-tekken").encode([Message("user", "Was?", images=[emb])])
        rows, cols = grid
        self.assertEqual(ids[:3 + cols], [1, 3] + [IMG] * cols + [BREAK])
        self.assertEqual(ids.count(IMG), rows * cols)
        self.assertEqual(ids[ids.index(END) - 1], IMG)
        with torch.no_grad():
            ref = self.hf(input_ids=torch.tensor([ids]), pixel_values=self.pix[None],
                          image_sizes=self.sizes).logits[0, -1].float()
        pfad = os.path.join(self.tmp.name, "text.gguf")
        B.schreibe_gguf(pfad, *_sprachteil(self.hf))
        gg = GGUFFile(pfad)
        try:
            modell = build_model(gg, torch.device("cpu"), torch.float32, 64, quant=False)
            self.assertTrue(modell.supports_images)
            zeilen = [i for i, t in enumerate(ids) if t == IMG]
            tokens = [11 if t == IMG else t for t in ids]
            aus = modell.forward(tokens, 0, (torch.tensor(zeilen), emb))
            del modell
        finally:
            gc.collect()
            gg.close()
        self.assertLess(float((aus - ref).abs().max()), 2e-3 * max(1.0, float(ref.abs().max())))

    def test_raster_und_grenzen(self):
        """Seitenverhältnis bleibt, höchstens image_size/28 je Seite, kleine Bilder werden nicht vergrößert."""
        from ggufengine.vision import PixtralVision
        v = PixtralVision.__new__(PixtralVision)
        v.patch, v.merge, v.image_size, v.min_tokens = 14, 2, 1540, 4
        self.assertEqual(v.grid_for(1920, 1080, 1024), (42, 24))
        self.assertEqual(v.grid_for(8000, 2000, 100000), (55, 13))       # 1540 px = 55 Bild-Token je Seite
        self.assertEqual(v.grid_for(100, 50, 1024), (4, 2))
        nx, ny = v.grid_for(800, 1600, 64)
        self.assertLessEqual(nx * ny, 64)
        self.assertGreater(ny, nx)


if __name__ == "__main__":
    unittest.main()
