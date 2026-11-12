"""Bausteine des Qwen3-TTS-Motors (ohne Modelldateien): kausale Faltungen, SnakeBeta, Abtastung."""
import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ggufengine.models import qwen3tts as T     # noqa: E402


class Faltungen(unittest.TestCase):
    def test_kausal_sieht_nicht_in_die_zukunft(self):
        torch.manual_seed(0)
        w, b = torch.randn(4, 3, 7), torch.randn(4)
        x = torch.randn(1, 3, 20)
        y = T._kausal(x, w, b, dilation=3)
        x2 = x.clone()
        x2[..., 12:] = 0                                      # Zukunft ab t=12 ändern
        y2 = T._kausal(x2, w, b, dilation=3)
        self.assertEqual(y.shape, (1, 4, 20))
        self.assertTrue(torch.allclose(y[..., :12], y2[..., :12]))

    def test_ein_kanal_ohne_eins_in_der_form(self):
        w = torch.randn(1, 5, 7)
        x = torch.randn(1, 5, 9)
        self.assertTrue(torch.allclose(T._kausal(x, w[0], torch.zeros(1)), T._kausal(x, w, torch.zeros(1))))

    def test_transponiert_vervielfacht_die_laenge(self):
        x = torch.randn(1, 6, 10)
        for stride in (2, 3, 5, 8):
            w = torch.randn(6, 4, 2 * stride)
            self.assertEqual(T._kausal_t(x, w, None, stride).shape, (1, 4, 10 * stride))

    def test_snake(self):
        x = torch.linspace(-2, 2, 11).view(1, 1, -1)
        a, b = torch.tensor([0.3]), torch.tensor([-0.2])
        soll = x + torch.sin(x * a.exp()).pow(2) / (b.exp() + 1e-9)
        self.assertTrue(torch.allclose(T._snake(x, a, b), soll))


class Abtastung(unittest.TestCase):
    def test_temperatur_null_ist_argmax(self):
        logits = torch.tensor([0.1, 3.0, 2.0])
        self.assertEqual(T._ziehen(logits, T.Abtastung(temperatur=0), None), 1)

    def test_top_k_und_seed(self):
        logits = torch.tensor([5.0, 4.9, -10.0, -10.0])
        gen = torch.Generator().manual_seed(1)
        gezogen = {T._ziehen(logits, T.Abtastung(top_k=2), gen) for _ in range(50)}
        self.assertTrue(gezogen <= {0, 1})
        a = [T._ziehen(logits, T.Abtastung(), torch.Generator().manual_seed(7)) for _ in range(3)]
        self.assertEqual(len(set(a)), 1)                      # gleicher Seed, gleiches Ergebnis


if __name__ == "__main__":
    unittest.main()
