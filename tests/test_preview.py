"""Offline-Test: Bild-Vorschau als Pixel-Block (ui.image_preview_text).

python -m unittest discover -s tests -p test_preview.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import ui                                         # noqa: E402


class PreviewTests(unittest.TestCase):
    def _bild(self, w, h, farbe_oben=(255, 0, 0), farbe_unten=(0, 0, 255)):
        from PIL import Image
        img = Image.new("RGB", (w, h), farbe_oben)
        for y in range(h // 2, h):
            for x in range(w):
                img.putpixel((x, y), farbe_unten)
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(f.name)
        return Path(f.name)

    def test_breites_bild_begrenzt_durch_spalten(self):
        p = self._bild(1200, 600)
        t = ui.image_preview_text(p, max_cols=60, max_rows=24)
        zeilen = t.plain.split("\n")
        self.assertEqual(len(zeilen[0]), 60)
        self.assertEqual(len(zeilen), 15)                 # 600/1200*60/2
        self.assertTrue(all(ch == "▀" for ch in zeilen[0]))

    def test_hohes_bild_begrenzt_durch_zeilen(self):
        p = self._bild(600, 1200)
        t = ui.image_preview_text(p, max_cols=60, max_rows=24)
        zeilen = t.plain.split("\n")
        self.assertEqual(len(zeilen), 24)
        self.assertEqual(len(zeilen[0]), 24)              # 600/1200*24*2

    def test_farben_oben_unten(self):
        """Erste Zeile: Vordergrund rot (oberer Pixel), letzte: Hintergrund blau."""
        p = self._bild(40, 40)
        t = ui.image_preview_text(p, max_cols=20, max_rows=10)
        spans = [sp for sp in t.spans if sp.start == 0]
        self.assertTrue(spans)
        st = spans[0].style
        self.assertEqual(st.color.triplet.hex, "#ff0000")
        letzte = [sp for sp in t.spans if sp.end == len(t.plain)]
        self.assertEqual(letzte[0].style.bgcolor.triplet.hex, "#0000ff")

    def test_kaputt_oder_fehlend_gibt_none(self):
        self.assertIsNone(ui.image_preview_text("gibt-es-nicht.png"))
        self.assertIsNone(ui.image_preview("gibt-es-nicht.png"))
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(b"kein bild"); f.close()
        self.assertIsNone(ui.image_preview_text(f.name))

    def test_panel_mit_titel(self):
        p = self._bild(100, 50)
        panel = ui.image_preview(p, title="angehängt: x.png")
        self.assertIsNotNone(panel)
        self.assertIn("angehängt: x.png", str(panel.title))


if __name__ == "__main__":
    unittest.main()
