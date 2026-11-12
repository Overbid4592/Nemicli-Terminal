"""Offline-Tests: Beim Einlesen gibt der Motor leeren Allocator-Vorrat zwischen den Stücken zurück.

python -m unittest discover -s tests -p test_einlesen_vorrat.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch  # noqa: E402

from ggufengine import engine as E  # noqa: E402


class _Modell:
    prefill_chunk = 2

    def __init__(self):
        self.aufrufe = 0

    def forward(self, tokens, pos):
        self.aufrufe += 1
        return torch.zeros(1, 4)


def _engine() -> E.Engine:
    e = object.__new__(E.Engine)
    e._moved, e._mrope, e._pos = None, False, 0
    e.model, e.device = _Modell(), torch.device("cuda")
    e._history, e._own, e._keys = [], [], []
    e._kv_room = lambda n: None
    return e


class VorratTests(unittest.TestCase):
    def test_grosser_vorrat_geht_zwischen_den_stuecken_zurueck(self):
        e = _engine()
        with patch.object(E, "_frei_reserviert", return_value=E.VORRAT_GRENZE + 1), \
                patch.object(E.Engine, "_release_scratch") as frei:
            e.feed([1, 2, 3, 4, 5])                       # drei Stücke: nach dem 1. und 2. prüfen
        self.assertEqual(3, e.model.aufrufe)
        self.assertEqual(2, frei.call_count)

    def test_kleiner_vorrat_bleibt(self):
        e = _engine()
        with patch.object(E, "_frei_reserviert", return_value=E.VORRAT_GRENZE // 2), \
                patch.object(E.Engine, "_release_scratch") as frei:
            e.feed([1, 2, 3, 4, 5])
        frei.assert_not_called()


if __name__ == "__main__":
    unittest.main()
