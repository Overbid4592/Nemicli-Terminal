"""Gesprächsspeicher teils im RAM: geteilter KV-Cache liefert genau dasselbe wie der ganze."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
    from ggufengine.models import common as C
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = C = None


def _caches(obj, gefunden=None, gesehen=None):
    gefunden, gesehen = ([] if gefunden is None else gefunden), (set() if gesehen is None else gesehen)
    if id(obj) in gesehen:
        return gefunden
    gesehen.add(id(obj))
    if isinstance(obj, C.KVCache):
        gefunden.append(obj)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _caches(x, gefunden, gesehen)
    elif isinstance(obj, torch.nn.Module) or hasattr(obj, "__dict__"):
        for x in vars(obj).values():
            if isinstance(x, (torch.nn.Module, list, tuple, C.KVCache)) or hasattr(x, "cache"):
                _caches(x, gefunden, gesehen)
    return gefunden


@unittest.skipIf(torch is None, "torch fehlt")
class GeteilterCache(unittest.TestCase):
    def _paar(self, bits, gpu):
        with mock.patch.object(C, "KV_GROWTH", 3), mock.patch.object(C, "KV_GPU_SLOTS", gpu):
            geteilt = C.KVCache(2, 32, 20, torch.device("cpu"), torch.float32, bits)
        with mock.patch.object(C, "KV_GROWTH", 3):
            ganz = C.KVCache(2, 32, 20, torch.device("cpu"), torch.float32, bits)
        return geteilt, ganz

    def test_gleiche_werte(self):
        for bits in (16, 8, 6, 4):
            with self.subTest(bits=bits), mock.patch.object(C, "KV_GROWTH", 3), torch.inference_mode():
                geteilt, ganz = self._paar(bits, 4)
                self.assertTrue(geteilt.geteilt)
                self.assertFalse(ganz.geteilt)
                torch.manual_seed(bits)
                for pos in ([0, 1, 2, 3, 4, 5, 6], [7], [8], list(range(9, 17))):
                    n = pos[-1] + 1
                    for c in (geteilt, ganz):
                        c.ensure(n)
                    k, v = torch.randn(len(pos), 2, 32), torch.randn(len(pos), 2, 32)
                    idx = torch.tensor(pos)
                    geteilt.write(k, v, idx)
                    ganz.write(k, v, idx)
                    for a, b in zip(geteilt.kv(n), ganz.kv(n)):
                        self.assertTrue(torch.equal(a, b))
                self.assertLessEqual(geteilt.k.shape[1], 4)          # nur 4 Positionen im „VRAM“
                self.assertGreater(len(geteilt.ram), 0)

    def test_fuellen(self):
        with mock.patch.object(C, "KV_GROWTH", 3), torch.inference_mode():
            geteilt, ganz = self._paar(16, 5)
            k, v = torch.randn(2, 12, 32), torch.randn(2, 12, 32)
            for c in (geteilt, ganz):
                c.ensure(12)
                c.fill(k, v)
            for a, b in zip(geteilt.kv(12), ganz.kv(12)):
                self.assertTrue(torch.equal(a, b))


@unittest.skipIf(torch is None or T is None, "torch/transformers fehlen")
class GeteiltesModell(unittest.TestCase):
    def test_llama_rechnet_gleich(self):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        torch.manual_seed(0)
        cfg = T.LlamaConfig(vocab_size=96, hidden_size=64, intermediate_size=96, num_hidden_layers=2,
                            num_attention_heads=4, num_key_value_heads=2, head_dim=16, max_position_embeddings=64,
                            rms_norm_eps=1e-6, tie_word_embeddings=False, attn_implementation="eager",
                            pad_token_id=0, bos_token_id=1, eos_token_id=2)
        hf = T.LlamaForCausalLM(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        m, t = B.umwandeln(hf, "llama")
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, m, t)
            gg = GGUFFile(pfad)
            try:
                ids = torch.randint(0, 96, (14,)).tolist()
                ergebnisse = []
                for teilung in (None, 5):
                    with mock.patch.object(C, "KV_GPU_SLOTS", teilung), mock.patch.object(C, "KV_GROWTH", 4):
                        modell = build_model(gg, torch.device("cpu"), torch.float32, 48, quant=False)
                        caches = _caches(modell)
                        self.assertTrue(caches)
                        self.assertEqual(all(c.geteilt for c in caches), teilung is not None)
                        with torch.inference_mode():
                            for c in caches:
                                c.ensure(14)
                        with torch.inference_mode():
                            aus = [modell.forward(ids[:9], 0)] + [modell.forward([ids[j]], j) for j in range(9, 14)]
                    ergebnisse.append(torch.stack([a.reshape(-1) for a in aus]))
                    del modell
                self.assertLess(float((ergebnisse[0] - ergebnisse[1]).abs().max()), 1e-5)
            finally:
                import gc
                gc.collect()
                gg.close()
        finally:
            os.unlink(pfad)


if __name__ == "__main__":
    unittest.main()
