"""Anleitungen in den Modell-Bereichen und alte LIES-MICH-Dateien (core/paths.ensure_layout).

python -m unittest tests.test_paths_zettel
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

import modellorte                                      # noqa: E402
import paths                                           # noqa: E402


class ModellZettel(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.install = Path(tmp.name) / "programm"
        self.daten = Path(tmp.name) / "daten"
        self.install.mkdir()
        self.daten.mkdir()
        self.wurzel = self.install / modellorte.WURZEL_NAME
        for ziel, name, wert in ((paths, "INSTALL", self.install), (paths, "DATEN", self.daten),
                                 (modellorte, "WURZEL", self.wurzel)):
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_anleitung_je_bereich(self):
        paths.ensure_layout()
        for art in modellorte.BEREICHE:
            with self.subTest(art=art):
                self.assertTrue((modellorte.ordner(art) / modellorte.ANLEITUNG).is_file())
        chat = (modellorte.ordner("chat") / modellorte.ANLEITUNG).read_text(encoding="utf-8")
        sehen = (modellorte.ordner("sehen") / modellorte.ANLEITUNG).read_text(encoding="utf-8")
        self.assertIn("mmproj", chat)
        self.assertIn("/model huggingface", chat)
        self.assertIn("mmproj-<modell>.gguf", sehen)
        self.assertIn("/vision", sehen)
        self.assertIn("KI-Chat/Chatmodell/", (self.wurzel / modellorte.ANLEITUNG).read_text(encoding="utf-8"))

    def test_keine_alten_modellordner_mehr(self):
        paths.ensure_layout()
        for alt in (self.install / "ModelGGUF", self.install / "Vision", self.daten / "Models"):
            with self.subTest(alt=alt.name):
                self.assertFalse(alt.exists())

    def test_alter_zettel_wird_ersetzt(self):
        alt = self.daten / "Models" / paths._ALTER_MODELS_NAME
        alt.parent.mkdir(parents=True)
        alt.write_text(paths._ALTER_MODELS_KOPF + "\n\nueber llama.cpp ...\n", encoding="utf-8")
        paths.ensure_layout()
        self.assertFalse(alt.exists())

    def test_geaenderter_alter_zettel_bleibt(self):
        alt = self.daten / "Models" / paths._ALTER_MODELS_NAME
        alt.parent.mkdir(parents=True)
        alt.write_text("eigene Notizen\n", encoding="utf-8")
        paths.ensure_layout()
        self.assertEqual(alt.read_text(encoding="utf-8"), "eigene Notizen\n")

    def test_vorhandene_anleitung_wird_nicht_ueberschrieben(self):
        z = modellorte.ordner("sehen") / modellorte.ANLEITUNG
        z.parent.mkdir(parents=True)
        z.write_text("eigener Text\n", encoding="utf-8")
        paths.ensure_layout()
        self.assertEqual(z.read_text(encoding="utf-8"), "eigener Text\n")


if __name__ == "__main__":
    unittest.main()
