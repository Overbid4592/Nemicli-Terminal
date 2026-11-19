"""Modell-Bereiche (core/modellorte.py): neue Orte, frühere Orte als Rückfall.

python -m unittest tests.test_modellorte
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import modellorte as MO                                # noqa: E402
import paths                                           # noqa: E402


class Bereiche(unittest.TestCase):
    def test_aufbau(self):
        self.assertEqual(MO.WURZEL / "KI-Chat" / "Chatmodell", MO.ordner("chat"))
        self.assertEqual(MO.WURZEL / "KI-Gedaechtnis" / "Suchmodell", MO.ordner("suche"))
        self.assertEqual(MO.WURZEL / "KI-Gedaechtnis" / "Sortiermodell", MO.ordner("sortieren"))
        self.assertEqual("KI-Modelle/KI-Vision/Sehmodell/", MO.anzeige("sehen"))
        for bereich, unter in MO.BEREICHE.values():
            with self.subTest(bereich=bereich):
                self.assertTrue(bereich.startswith("KI-"))
                self.assertTrue(bereich.isascii() and unter.isascii())

    def test_tests_sehen_keine_echten_modelle(self):
        self.assertEqual(paths.TESTDATEN, MO.BASIS)
        self.assertEqual([], MO.alte("chat"))
        self.assertEqual([MO.ordner("chat")], MO.orte("chat"))


class FruehereOrte(unittest.TestCase):
    """Außerhalb der Test-Suite liest NemiCLI die früheren Orte weiter."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.install = Path(tmp.name) / "programm"
        self.daten = Path(tmp.name) / "daten"
        for ziel, name, wert in ((paths, "INSTALL", self.install), (paths, "DATEN", self.daten),
                                 (paths, "_testlauf", lambda: False)):
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_alte_orte(self):
        self.assertEqual([self.install / "ModelGGUF"], MO.alte("chat"))
        self.assertEqual([self.install / "Vision", self.install / "Sinne"], MO.alte("stimme"))
        self.assertEqual([self.daten / "Models" / "Krea2", self.install / "Models" / "Krea2"], MO.alte("krea"))
        self.assertEqual(MO.ordner("bild"), MO.orte("bild")[0])

    def test_ohne_doppelte(self):
        with mock.patch.object(paths, "DATEN", self.install):
            self.assertEqual([self.install / "Models" / "Bildmodelle"], MO.alte("bild"))


class Motoren(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.neu = Path(tmp.name) / "neu"
        self.alt = Path(tmp.name) / "alt"

    def _gguf(self, ordner: Path, name: str) -> Path:
        ordner.mkdir(parents=True, exist_ok=True)
        p = ordner / name
        p.write_bytes(b"GGUF")
        return p

    def test_chat_neu_und_alt(self):
        import gguflokal as G
        neu = self._gguf(self.neu / "A", "a.gguf")
        self._gguf(self.alt / "A", "a-alt.gguf")
        alt = self._gguf(self.alt / "B", "b.gguf")
        with mock.patch.object(G, "ORDNER", self.neu), mock.patch.object(G, "ALTE_ORDNER", [self.alt]):
            self.assertEqual({"A": neu, "B": alt}, G.modelle())          # gleicher Name: der neue Ort gilt
            self.assertEqual([self.neu, self.alt], G.wurzeln())

    def test_sortiermodell_neu_und_alt(self):
        import reranker as R
        self._gguf(self.neu / "R1", "r1.gguf")
        self._gguf(self.alt / "R2", "r2.gguf")
        with mock.patch.object(R, "ORDNER", self.neu), mock.patch.object(R, "ALTE_ORDNER", [self.alt]):
            self.assertEqual(["R1", "R2"], [d.name for d in R.modelle()])

    def test_suchmodell_am_alten_ort(self):
        import memory
        self._gguf(self.alt / "Emb", "emb.gguf")
        with mock.patch.object(memory, "EMBED_ORDNER", self.neu), \
                mock.patch.object(memory, "ALTE_EMBED_ORDNER", [self.alt]), \
                mock.patch.object(memory, "_gewuenscht", lambda: "Emb"):
            self.assertEqual(self.alt / "Emb", memory.modell_ordner())

    def test_bewegung_bleibt_am_alten_ort(self):
        import bewegung as BW
        with mock.patch.object(BW, "_neuer_ordner", lambda: self.neu), \
                mock.patch.object(BW, "_alter_ordner", lambda: self.alt):
            self.assertEqual(self.neu, BW.modell_ordner())                 # nichts da: neuer Ort
            self._gguf(self.alt, BW.MODELL)
            self.assertEqual(self.alt, BW.modell_ordner())                 # nur am alten Ort: bleibt dort
            self._gguf(self.neu, BW.MODELL)
            self.assertEqual(self.neu, BW.modell_ordner())

    def test_helfer_am_alten_ort(self):
        sys.path.insert(0, str(ROOT))
        from einrichtungshelfer import gemmahelfer as GH
        datei = self._gguf(self.alt / "gemma4-e2b", GH.DATEI["gemma4-e2b"])
        with mock.patch.object(GH, "orte", lambda: [self.neu, self.alt]):
            self.assertEqual(datei, GH.grundmodell("gemma4-e2b"))


if __name__ == "__main__":
    unittest.main()
