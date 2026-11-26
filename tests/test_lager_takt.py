"""MoE-Lager während einer Antwort: alle LAGER_TAKT Token sortiert der Motor die VRAM-Plätze nach,
ohne dabei ins Debug-Log zu schreiben; nach dem Einlesen weiter mit Meldung."""
import sys
import unittest
from pathlib import Path
from unittest import mock

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ggufengine import engine as E  # noqa: E402


def _motor():
    e = E.Engine.__new__(E.Engine)
    e.device = torch.device("cpu")
    e.tokenizer = mock.Mock(stop_ids=[0])
    e._pos, e.n_ctx, e.max_tok_s = 0, 10_000, None
    e._history, e._own = [], []
    e.feed = mock.Mock(return_value=torch.zeros(5))
    e._weiter = mock.Mock(side_effect=lambda tid, entwurf, vorschlagen=True: torch.zeros(5))
    e._entwurf = mock.Mock(return_value=None)
    e._verwerfen = mock.Mock()
    e._lager_umsortieren = mock.Mock()
    return e


class LagerTakt(unittest.TestCase):
    def test_nachsortieren_im_takt(self):
        e = _motor()
        with mock.patch.object(E, "Sampler") as S:
            S.return_value.sample.return_value = 7
            n = len(list(e.generate_tokens([1, 2], max_tokens=3 * E.LAGER_TAKT + 5)))
        self.assertEqual(n, 3 * E.LAGER_TAKT + 5)
        aufrufe = e._lager_umsortieren.call_args_list
        self.assertEqual(aufrufe[0], mock.call())                                   # nach dem Einlesen
        self.assertEqual(aufrufe[1:], [mock.call(melden=False)] * 3)                # in der Antwort

    def test_abschaltbar(self):
        e = _motor()
        with mock.patch.object(E, "Sampler") as S, mock.patch.object(E, "LAGER_TAKT", 0):
            S.return_value.sample.return_value = 7
            list(e.generate_tokens([1], max_tokens=100))
        self.assertEqual(e._lager_umsortieren.call_args_list, [mock.call()])

    def test_ohne_meldung_kein_log(self):
        e = E.Engine.__new__(E.Engine)
        lager = mock.Mock()
        lager.umsortieren.return_value = 3
        lager.treffer.return_value = 0.9
        e._lager = mock.Mock(return_value=[lager])
        with mock.patch.object(E.dbg, "event") as log:
            e._lager_umsortieren(melden=False)
            log.assert_not_called()
            e._lager_umsortieren()
            log.assert_called_once()
        self.assertEqual(lager.umsortieren.call_count, 2)


if __name__ == "__main__":
    unittest.main()
