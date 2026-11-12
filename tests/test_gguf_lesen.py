"""Gewichte mit Datei-Reads statt über die Dateiabbildung: gleiche Bytes auf GPU und im
festgesetzten RAM, auch wenn ein Tensor über mehrere Lesepuffer verteilt ist.

python -m unittest tests.test_gguf_lesen
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
except ImportError:  # pragma: no cover
    torch = None

Q8_0 = 8           # Block: 32 Werte in 34 Bytes


def _roh(n_bytes: int, seed: int) -> bytes:
    g = torch.Generator().manual_seed(seed)
    return torch.randint(0, 256, (n_bytes,), dtype=torch.uint8, generator=g).numpy().tobytes()


@unittest.skipIf(torch is None, "torch fehlt")
class LesenTests(unittest.TestCase):
    def setUp(self):
        from gguf_testbau import schreibe_gguf
        from ggufengine.gguf import GGUFFile
        self.tmp = tempfile.TemporaryDirectory()
        pfad = os.path.join(self.tmp.name, "lesen.gguf")
        # Matrix 40x64 (40 Zeilen à 68 Bytes) und Experten 3x8x64
        self.matrix = _roh(40 * 68, 1)
        self.experten = _roh(3 * 8 * 68, 2)
        schreibe_gguf(pfad, {"general.architecture": "llama"},
                      {"w": (Q8_0, (40, 64), self.matrix), "e": (Q8_0, (3, 8, 64), self.experten)})
        self.gg = GGUFFile(pfad)

    def tearDown(self):
        gc.collect()
        self.gg.close()
        self.tmp.cleanup()

    def test_read_into_ganz_und_ab_versatz(self):
        ti = self.gg.tensors["w"]
        puffer = bytearray(ti.n_bytes + 10)
        self.assertEqual(self.gg.read_into(ti, puffer), ti.n_bytes)
        self.assertEqual(bytes(puffer[:ti.n_bytes]), self.matrix)
        teil = bytearray(100)
        self.assertEqual(self.gg.read_into(ti, teil, 2700), ti.n_bytes - 2700)
        self.assertEqual(bytes(teil[:ti.n_bytes - 2700]), self.matrix[2700:])
        self.assertEqual(bytes(self.gg.tensor_bytes(ti)), self.matrix)

    def test_read_into_ausserhalb(self):
        from ggufengine.gguf import GGUFError
        ti = self.gg.tensors["w"]
        with self.assertRaises(GGUFError):
            self.gg.read_into(ti, bytearray(8), ti.n_bytes + 1)
        with self.assertRaises(GGUFError):
            self.gg.read_into(ti, bytearray(8), -1)

    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), "braucht CUDA")
    def test_gpu_ueber_mehrere_puffer(self):
        from ggufengine.models.common import WeightLoader
        ld = WeightLoader(self.gg, torch.device("cuda"), torch.bfloat16)
        ld.LESE_STUECK, ld.LESE_PUFFER = 96, 2          # viele Stücke, Ring läuft mehrfach um
        for name, soll in (("w", self.matrix), ("e", self.experten), ("w", self.matrix)):
            roh = ld._roh(self.gg.tensors[name], "cuda")
            self.assertTrue(roh.is_cuda)
            self.assertEqual(bytes(roh.cpu().numpy()), soll)

    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), "braucht CUDA")
    def test_experten_im_festgesetzten_ram(self):
        from ggufengine.models.common import PackedExperts, WeightLoader
        ld = WeightLoader(self.gg, torch.device("cuda"), torch.bfloat16, experten_vram=0)
        ex = ld.experts("e")
        self.assertIsInstance(ex, PackedExperts)
        self.assertTrue(ex.raw.is_pinned())
        self.assertEqual(bytes(ex.raw.reshape(-1).numpy()), self.experten)


if __name__ == "__main__":
    unittest.main()
