"""Gespeicherter Stand nach der Anleitung (Engine.load_prefix): der Modellzustand aus der Datei muss
genau den Aufbau des geladenen Modells haben, Positionen liegen im Kontext.

python -m unittest tests.test_zwischenstand
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch                                           # noqa: E402

from ggufengine.engine import _gleiche_form            # noqa: E402


def _ring(pos):
    return (torch.zeros(2, 4), torch.zeros(2, 4), torch.tensor(pos, dtype=torch.long))


class GleicheForm(unittest.TestCase):
    def test_passender_stand(self):
        soll = [_ring([0, 1, -1]), _ring([3, 4, 5])]
        self.assertTrue(_gleiche_form([_ring([5, 6, 7]), _ring([-1, -1, -1])], soll, 128))
        self.assertTrue(_gleiche_form(None, None, 128))
        self.assertTrue(_gleiche_form([None, torch.zeros(3)], (None, torch.ones(3)), 128))

    def test_andere_form_oder_art(self):
        soll = [_ring([0, 1, 2])]
        self.assertFalse(_gleiche_form([(torch.zeros(2, 5), torch.zeros(2, 4), torch.zeros(3, dtype=torch.long))],
                                       soll, 128))
        self.assertFalse(_gleiche_form([(torch.zeros(2, 4, dtype=torch.float16), torch.zeros(2, 4),
                                         torch.zeros(3, dtype=torch.long))], soll, 128))
        self.assertFalse(_gleiche_form([_ring([0, 1, 2]), _ring([0, 1, 2])], soll, 128))
        self.assertFalse(_gleiche_form(None, soll, 128))
        self.assertFalse(_gleiche_form(soll, None, 128))
        self.assertFalse(_gleiche_form("text", torch.zeros(1), 128))

    def test_positionen_ausserhalb_des_kontexts(self):
        soll = [_ring([0, 1, 2])]
        self.assertFalse(_gleiche_form([_ring([0, 1, 1 << 40])], soll, 128))
        self.assertFalse(_gleiche_form([_ring([0, -2, 1])], soll, 128))
        self.assertTrue(_gleiche_form([_ring([0, 1, 128])], soll, 128))


if __name__ == "__main__":
    unittest.main()
