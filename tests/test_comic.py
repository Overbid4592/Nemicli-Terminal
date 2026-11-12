"""Comic: Raster, Comicseite mit Sprechblasen und Erzähltext, Werkzeug comic_malen (Bild-Motor gespielt)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("", "core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image                   # noqa: E402

import actions                          # noqa: E402
import comic                            # noqa: E402


def _bild(ordner: Path, name: str, farbe=(120, 170, 220)) -> Path:
    p = ordner / name
    Image.new("RGB", (832, 1216), farbe).save(p)
    return p


class Seite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_raster(self):
        self.assertEqual(comic.raster(1), [(0, 0, 1)])
        self.assertEqual(comic.raster(4), [(0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 1)])
        self.assertEqual(comic.raster(3)[-1], (1, 0, 2))           # übriges Feld über die ganze Breite

    def test_seite_mit_texten(self):
        felder = [{"bild": _bild(self.tmp, f"{i}.png"), "text": t, "art": a} for i, (t, a) in enumerate(
            [("Es war einmal.", "erzaehler"), ("Hallo!", "sprechblase"), ("", None)])]
        ziel = comic.seite_bauen(felder, self.tmp / "unter" / "seite.png", "Titel")
        with Image.open(ziel) as im:
            self.assertEqual(im.width, comic.SEITE_BREITE)
            self.assertGreater(im.height, 1500)
            # Erzählkasten oben links im ersten Feld ist hellgelb
            self.assertEqual(im.getpixel((comic.RAND + 30, comic.RAND + 110 + 30))[:3], (255, 244, 194))

    def test_lange_texte_passen_hinein(self):
        lang = "Ein sehr langer Satz, der niemals in eine einzige Zeile passt, " * 6
        ziel = comic.seite_bauen([{"bild": _bild(self.tmp, "a.png"), "text": lang}], self.tmp / "s.png")
        self.assertTrue(ziel.exists())


class Werkzeug(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gemalt = []
        import imagegen
        import profilordner

        def paint(prompt, **kw):
            self.gemalt.append((prompt, kw.get("seed"), kw.get("size")))
            return str(_bild(self.tmp, f"bild{len(self.gemalt)}.png"))
        for ziel, name, wert in ((imagegen, "missing_reason_aktiv", lambda: None),
                                 (imagegen, "modell_aufloesen", lambda m: (None, "")),
                                 (imagegen, "paint", paint),
                                 (imagegen, "gesicht_nachbessern", lambda p, on_status=None: None),
                                 (actions, "_figur", lambda felder, szene: (szene, 1234, None, "")),
                                 (profilordner, "bild_pfad", lambda endung=".png": self.tmp / "comic.png"),
                                 (os, "startfile", lambda p: None)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_comic_malen(self):
        res = actions._comic_malen({"titel": "Pfannkuchen", "panels": [
            {"szene": "a woman in a kitchen", "text": "Es war Morgen.", "art": "erzaehler"},
            {"szene": "she smiles", "text": "Pfannkuchen!"}]})
        self.assertTrue(res.ok, res.text)
        self.assertIn("Comic fertig", res.text)
        self.assertTrue((self.tmp / "comic.png").exists())
        self.assertEqual({s for _p, s, _g in self.gemalt}, {1234})      # gleicher Seed in allen Feldern
        self.assertEqual(self.gemalt[0][2], (832, 1040))                # Vorgabe passt zum Feld

    def test_fehler(self):
        self.assertFalse(actions._comic_malen({}).ok)
        self.assertFalse(actions._comic_malen({"panels": [{"text": "ohne Szene"}]}).ok)
        self.assertFalse(actions._comic_malen({"panels": ["x"] * (actions.MAX_COMIC + 1)}).ok)

    def test_eingebunden(self):
        import hintergrund
        import notstopp
        self.assertTrue(actions.ACTIONS["comic_malen"].get("hintergrund"))
        self.assertIn("comic_malen", hintergrund.GPU_WERKZEUGE)
        self.assertIn("comic_malen", notstopp.ZU_ENDE)
        self.assertIn("- comic_malen ", (ROOT / "core" / "persona.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
