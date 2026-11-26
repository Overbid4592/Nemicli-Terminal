"""Umgefaltete GGUF-Tensoren (ComfyUI-GGUF: comfy.gguf.orig_shape.<Name>): der Bild-Lader nimmt die echte Form.

python -m unittest tests.test_gguf_umgefaltet
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import torch  # noqa: E402

from bildmotoren import gewichte as G  # noqa: E402
from gguf_testbau import schreibe_gguf  # noqa: E402

Q4_1 = 3


def _q4_1(werte: torch.Tensor) -> bytes:
    """Blöcke zu 32 Werten: d, m (f16) und 16 Bytes – Wert j im unteren, j+16 im oberen Halbbyte."""
    roh = b""
    for block in werte.reshape(-1, 32):
        m, d = float(block.min()), max(float(block.max() - block.min()) / 15, 1e-3)
        q = ((block - m) / d).round().clamp(0, 15).to(torch.uint8)
        roh += struct.pack("<ee", d, m) + bytes(int(q[j]) | (int(q[j + 16]) << 4) for j in range(16))
    return roh


class Umgefaltet(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pfad = Path(tmp.name) / "probe.gguf"
        # echte Form [16, 8] (wie first.weight [6144, 64]), gespeichert als [4, 32]
        self.echt = (torch.arange(128, dtype=torch.float32) % 16).reshape(16, 8)
        speicher = self.echt.reshape(4, 32)
        schreibe_gguf(self.pfad, {
            G.ORIG_FORM + "first.weight": [16, 8],
            G.ORIG_FORM + "dicht.weight": [16, 8],
            G.ORIG_FORM + "falsch.weight": [16, 9],              # passt nicht zur Anzahl: gespeicherte Form gilt
        }, {
            "first.weight": (Q4_1, (4, 32), _q4_1(speicher)),
            "dicht.weight": speicher.clone(),
            "falsch.weight": speicher.clone(),
            "normal.weight": speicher.clone(),
        })

    def test_namen_mit_echter_form(self):
        n = G.namen(self.pfad)
        self.assertEqual(n["first.weight"], (16, 8))
        self.assertEqual(n["dicht.weight"], (16, 8))
        self.assertEqual(n["falsch.weight"], (4, 32))
        self.assertEqual(n["normal.weight"], (4, 32))

    def test_gepackt_wird_dicht_in_echter_form(self):
        with G.Quelle(self.pfad) as q:
            lin = q.lin("first")
        self.assertEqual(lin.art, "dicht")
        self.assertEqual((lin.n_out, lin.n_in), (16, 8))
        self.assertTrue(torch.allclose(lin.gewicht(torch.float32), self.echt, atol=1e-2))
        x = torch.randn(3, 8)
        self.assertEqual(tuple(lin(x).shape), (3, 16))           # vorher: 8 gegen 32 passt nicht

    def test_dichter_tensor_und_lin(self):
        with G.Quelle(self.pfad) as q:
            self.assertTrue(torch.equal(q.tensor("dicht.weight"), self.echt))
            self.assertEqual((q.lin("dicht").n_out, q.lin("dicht").n_in), (16, 8))
            self.assertEqual(tuple(q.tensor("falsch.weight").shape), (4, 32))
            normal = q.lin("normal")
            self.assertEqual((normal.n_out, normal.n_in), (4, 32))

    def test_form_pruefung(self):
        ti = type("TI", (), {"name": "a", "dims": (32, 4), "n_elements": 128})()
        for meta in ({}, {G.ORIG_FORM + "a": "16x8"}, {G.ORIG_FORM + "a": [16, -8]}, {G.ORIG_FORM + "a": [1] * 6},
                     {G.ORIG_FORM + "a": [16, "x"]}):
            self.assertEqual(G.gguf_form(meta, ti), (4, 32))
        self.assertEqual(G.gguf_form({G.ORIG_FORM + "a": [2, 8, 8]}, ti), (2, 8, 8))


if __name__ == "__main__":
    unittest.main()
