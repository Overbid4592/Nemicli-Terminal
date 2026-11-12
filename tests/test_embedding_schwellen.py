"""Kosinus-Schwellen je Embedding-Modell: EmbeddingGemma 2 liegt deutlich höher als Qwen3-Embedding,
die Suche, das Werkzeug und /aufraeumen nehmen die Werte des Modells, dessen Vektoren im Index liegen."""
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import agentrag     # noqa: E402
import embedder     # noqa: E402
import indexdb      # noqa: E402
import memory       # noqa: E402

GEMMA = "gemma-embedding2-768-24-262144-mean"
QWEN = "qwen3-1024-28-151669-last"


class Schwellen(unittest.TestCase):
    def test_gemma_eigene_werte(self):
        s = embedder.schwellen(GEMMA)
        self.assertEqual(s["kandidat"], 0.66)
        self.assertEqual(s["schwach"], 0.71)
        self.assertEqual(s["verschmelzen"], 0.93)
        self.assertEqual(set(s), set(embedder.SCHWELLEN_VORGABE))

    def test_andere_modelle_behalten_die_vorgabe(self):
        for kennung in (QWEN, "", None, "gemma-embedding-768-24-262144-mean"):
            self.assertEqual(embedder.schwellen(kennung), embedder.SCHWELLEN_VORGABE)

    def test_vorgabe_bleibt_unveraendert(self):
        embedder.schwellen(GEMMA)["kandidat"] = 0.0
        embedder.schwellen(QWEN)["kandidat"] = 0.0
        self.assertEqual(embedder.schwellen(GEMMA)["kandidat"], 0.66)
        self.assertEqual(embedder.SCHWELLEN_VORGABE["kandidat"], 0.315)

    def test_index_kennung_entscheidet(self):
        with mock.patch.dict(indexdb._CACHE, {"kennung": GEMMA}):
            self.assertEqual(indexdb.schwelle("kandidat"), 0.66)
        with mock.patch.dict(indexdb._CACHE, {"kennung": None}), \
                mock.patch.object(indexdb, "_kennung", return_value=QWEN):
            self.assertEqual(indexdb.schwelle("kandidat"), 0.315)


class Vielfalt(unittest.TestCase):
    IX = {"mat": np.asarray([[1.0, 0.0], [0.6, 0.8]], dtype="float32"),
          "zeile": {1: 0, 2: 1}, "mat_zeile": {0: 0, 1: 1}}
    A = {"ref": "note:1", "rowid": 1, "text": "eins"}
    B = {"ref": "note:2", "rowid": 2, "text": "zwei"}

    def test_naehe_ist_der_kosinus(self):
        self.assertAlmostEqual(indexdb._naehe(self.A, self.B, self.IX), 0.6, places=5)

    def test_kopie_nach_vektor_und_nach_worten(self):
        self.assertFalse(indexdb._kopie(self.A, self.B, self.IX, 0.97))
        self.assertTrue(indexdb._kopie(self.A, self.B, self.IX, 0.6))
        a = {"ref": "x", "rowid": 7, "text": "gleicher Satz hier"}
        b = {"ref": "y", "rowid": 8, "text": "gleicher Satz hier"}
        self.assertTrue(indexdb._kopie(a, b, None, 0.999))     # ohne Vektoren zählen die Wörter


class SchwacheTreffer(unittest.TestCase):
    TREFFER = [{"aehnlich": 0.68, "wie": "sinn"}]

    def test_gemma_068_ist_schwach_qwen_nicht(self):
        with mock.patch.dict(indexdb._CACHE, {"kennung": GEMMA}):
            self.assertTrue(agentrag._schwach(self.TREFFER))
        with mock.patch.dict(indexdb._CACHE, {"kennung": QWEN}):
            self.assertFalse(agentrag._schwach(self.TREFFER))

    def test_stichwort_treffer_nie_schwach(self):
        with mock.patch.dict(indexdb._CACHE, {"kennung": GEMMA}):
            self.assertFalse(agentrag._schwach([{"aehnlich": 0.1, "wie": "wort+sinn"}]))


class Vorgeschmack(unittest.TestCase):
    TREFFER = [{"kind": "note", "ref": "note:1", "text": "Fahrrad-Kette ölen", "wie": "sinn", "aehnlich": 0.673},
               {"kind": "note", "ref": "note:2", "text": "Reifen gewechselt", "wie": "wort+sinn", "aehnlich": 0.6},
               {"kind": "note", "ref": "note:3", "text": "Winterreifen im Keller", "wie": "sinn",
                "aehnlich": 0.74}]

    def _block(self, kennung):
        with mock.patch.dict(indexdb._CACHE, {"kennung": kennung}), \
                mock.patch.object(indexdb, "search", return_value=[dict(h) for h in self.TREFFER]):
            return indexdb._treffer_block("wie wechsle ich einen autoreifen?", "", [], 0)

    def test_gemma_laesst_unsichere_bedeutungs_treffer_weg(self):
        block = self._block(GEMMA)
        self.assertNotIn("Fahrrad", block)
        self.assertIn("Reifen gewechselt", block)              # Stichwort-Treffer bleibt
        self.assertIn("Winterreifen", block)

    def test_qwen_unveraendert(self):
        self.assertIn("Fahrrad", self._block(QWEN))

    def test_nichts_sicheres_kein_block(self):
        with mock.patch.dict(indexdb._CACHE, {"kennung": GEMMA}), \
                mock.patch.object(indexdb, "search", return_value=[dict(self.TREFFER[0])]):
            self.assertEqual("", indexdb._treffer_block("autoreifen", "", [], 0))


class Aufraeumen(unittest.TestCase):
    def test_schwelle_des_modells(self):
        gesehen = []
        with mock.patch.object(indexdb, "notiz_vektoren", return_value={}), \
                mock.patch.object(indexdb, "schwelle", return_value=0.93), \
                mock.patch.object(memory, "_dateien", return_value=[Path("a.json")]), \
                mock.patch.object(memory, "_verdichten",
                                  side_effect=lambda d, t, v: gesehen.append(t) or {"before": 0, "after": 0,
                                                                                     "merged": 0}):
            memory.consolidate()
            memory.consolidate(0.8)
        self.assertEqual(gesehen, [0.93, 0.8])


if __name__ == "__main__":
    unittest.main()
