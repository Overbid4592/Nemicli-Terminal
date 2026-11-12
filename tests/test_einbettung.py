"""Embedding-Modelle als GGUF: Baukasten.einbetten gegen transformers (winziges Qwen3,
nichts wird geladen) und die Ordner-Erkennung des Embedders.

python -m unittest tests.test_einbettung
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
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class EinbettenGegenTransformers(unittest.TestCase):

    def test_pooling_wie_transformers(self):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        torch.manual_seed(0)
        cfg = T.Qwen3Config(vocab_size=96, hidden_size=64, intermediate_size=96, num_hidden_layers=2,
                            num_attention_heads=4, num_key_value_heads=2, head_dim=16,
                            max_position_embeddings=64, rms_norm_eps=1e-6, tie_word_embeddings=True,
                            attn_implementation="eager", pad_token_id=0, bos_token_id=1, eos_token_id=2)
        hf = T.Qwen3ForCausalLM(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        meta, tensoren = B.umwandeln(hf, "qwen3")
        meta["qwen3.pooling_type"] = 3
        tensoren.pop("output.weight", None)             # Embedding-Modelle haben keinen eigenen Kopf
        ids = torch.randint(0, 96, (20,))
        with torch.no_grad():
            ref = hf.model(ids[None]).last_hidden_state[0].float()     # nach der Schluss-Norm
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, meta, tensoren)
            gg = GGUFFile(pfad)
            try:
                m = build_model(gg, torch.device("cpu"), torch.float32, 32, quant=False)
                m.prefill_chunk = 8                     # mehrere Stücke: Mittel und CLS über Grenzen
                aus = {art: m.einbetten(ids.tolist(), art) for art in ("last", "mean", "cls")}
                with self.assertRaises(RuntimeError):
                    m.einbetten(list(range(40)))        # länger als der Kontext
                del m
            finally:
                gc.collect()
                gg.close()
        finally:
            os.unlink(pfad)
        for art, erwartet in (("last", ref[-1]), ("mean", ref.mean(0)), ("cls", ref[0])):
            self.assertLess(float((aus[art] - erwartet).abs().max()), 1e-4, art)


class OrdnerErkennung(unittest.TestCase):

    def test_gguf_oder_safetensors(self):
        import embedder
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertIsNone(embedder.gguf_datei(d))
            (d / "modell.gguf").write_bytes(b"")
            self.assertEqual(embedder.gguf_datei(d), d / "modell.gguf")
            (d / "zweites.gguf").write_bytes(b"")
            self.assertIsNone(embedder.gguf_datei(d))                   # nicht eindeutig
            (d / "zweites.gguf").unlink()
            (d / "model.safetensors").write_bytes(b"")
            self.assertIsNone(embedder.gguf_datei(d))                   # safetensors geht vor


if __name__ == "__main__":
    unittest.main()
