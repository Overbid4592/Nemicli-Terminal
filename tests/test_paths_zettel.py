"""LIES-MICH-Dateien in den Modell-Ordnern (core/paths.ensure_layout).

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

import paths                                           # noqa: E402


class ModellZettel(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.install = Path(tmp.name) / "programm"
        self.daten = Path(tmp.name) / "daten"
        self.install.mkdir()
        self.daten.mkdir()
        for name, wert in (("INSTALL", self.install), ("DATEN", self.daten)):
            p = mock.patch.object(paths, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_zettel_im_programm_und_daten_ordner(self):
        paths.ensure_layout()
        gguf = (self.install / "ModelGGUF" / "LIES-MICH.txt").read_text(encoding="utf-8")
        vision = (self.install / "Vision" / "LIES-MICH.txt").read_text(encoding="utf-8")
        models = (self.daten / "Models" / "LIES-MICH.txt").read_text(encoding="utf-8")
        self.assertIn("mmproj", gguf)
        self.assertIn("/model huggingface", gguf)
        self.assertIn("bild_fragen", vision)
        self.assertIn("/vision", vision)
        self.assertIn("ModelGGUF/", models)
        self.assertNotIn("Ollama", models)

    def test_alter_zettel_wird_ersetzt(self):
        alt = self.daten / "Models" / paths._ALTER_MODELS_NAME
        alt.parent.mkdir(parents=True)
        alt.write_text(paths._ALTER_MODELS_KOPF + "\n\nueber llama.cpp ...\n", encoding="utf-8")
        paths.ensure_layout()
        self.assertFalse(alt.exists())
        self.assertTrue((self.daten / "Models" / "LIES-MICH.txt").is_file())

    def test_geaenderter_alter_zettel_bleibt(self):
        alt = self.daten / "Models" / paths._ALTER_MODELS_NAME
        alt.parent.mkdir(parents=True)
        alt.write_text("eigene Notizen\n", encoding="utf-8")
        paths.ensure_layout()
        self.assertEqual(alt.read_text(encoding="utf-8"), "eigene Notizen\n")

    def test_vorhandener_zettel_wird_nicht_ueberschrieben(self):
        z = self.install / "Vision" / "LIES-MICH.txt"
        z.parent.mkdir(parents=True)
        z.write_text("eigener Text\n", encoding="utf-8")
        paths.ensure_layout()
        self.assertEqual(z.read_text(encoding="utf-8"), "eigener Text\n")


if __name__ == "__main__":
    unittest.main()
