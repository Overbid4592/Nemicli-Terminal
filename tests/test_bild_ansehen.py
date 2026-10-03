"""Offline-Test: bild_ansehen / bildschirm_ansehen – Bilder aus Werkzeug-Ergebnissen
landen in der nächsten Modell-Runde.

python -m unittest discover -s tests -p test_bild_ansehen.py
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))
sys.path.insert(0, str(ROOT))

import actions as A                               # noqa: E402
import modes                                      # noqa: E402
import vision                                     # noqa: E402


def _png(w=1800, h=900):
    from PIL import Image
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    Image.new("RGB", (w, h), (10, 200, 30)).save(f.name)
    return Path(f.name)


class WerkzeugTests(unittest.TestCase):
    def test_bild_ansehen_liefert_bildpfad(self):
        p = _png(20, 20)
        res = A._bild_ansehen({"pfad": str(p)})
        self.assertTrue(res.ok)
        self.assertEqual(res.bilder, [str(p)])
        self.assertIn(p.name, res.text)

    def test_bild_ansehen_fehler(self):
        self.assertFalse(A._bild_ansehen({"pfad": "C:/gibt/es/nicht.png"}).ok)
        f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False); f.close()
        self.assertFalse(A._bild_ansehen({"pfad": f.name}).ok)

    def test_registriert_und_lesend(self):
        self.assertIn("bild_ansehen", A.ACTIONS)
        self.assertFalse(A.ACTIONS["bild_ansehen"]["confirm"])
        self.assertIn("bild_ansehen", modes.READ_TOOLS)
        self.assertIn("bildschirm_ansehen", A.ACTIONS)
        self.assertTrue(A.ACTIONS["bildschirm_ansehen"]["confirm"])   # fragt vorher
        self.assertNotIn("bildschirm_ansehen", modes.READ_TOOLS)
        self.assertIn("Bild ansehen", A.describe({"tool": "bild_ansehen", "pfad": "x.png"}))
        self.assertIn("Screenshot", A.describe({"tool": "bildschirm_ansehen"}))

    def test_datei_lesen_verweist_auf_bild_ansehen(self):
        p = _png(8, 8)
        res = A._datei_lesen({"pfad": str(p)})
        self.assertFalse(res.ok)
        self.assertIn("bild_ansehen", res.text)


class DataUriTests(unittest.TestCase):
    def test_grosses_bild_wird_verkleinert(self):
        p = _png(1800, 900)
        uri = vision.to_data_uri_small(p, max_side=800)
        self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
        import base64, io
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        self.assertEqual(img.size, (800, 400))

    def test_kleines_jpeg_bleibt(self):
        from PIL import Image
        f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        Image.new("RGB", (50, 50), (1, 2, 3)).save(f.name, "JPEG")
        self.assertEqual(vision.to_data_uri_small(Path(f.name)), vision.to_data_uri(Path(f.name)))


class NaechsteRundeTests(unittest.TestCase):
    """main._bilder_aus_ergebnis: sehendes Modell -> Data-URIs; sonst Helfer/Hinweis."""

    def test_sehendes_modell_bekommt_uris(self):
        import main
        p = _png(30, 30)
        res = A.ActionResult("ok", ok=True, bilder=[str(p)])
        uris, res2 = asyncio.run(main._bilder_aus_ergebnis(res, "ollama_cloud:deepseek-v4.1-flash"))
        self.assertIs(res2, res)
        self.assertEqual(len(uris), 1)
        self.assertTrue(uris[0].startswith("data:image/"))

    def test_ohne_bilder_nichts(self):
        import main
        res = A.ActionResult("ok", ok=True)
        self.assertEqual(asyncio.run(main._bilder_aus_ergebnis(res, "ollama_cloud:deepseek-v4.1-flash")),
                         (None, res))

    def test_blindes_modell_ohne_helfer_hinweis(self):
        # Ohne llama.cpp gibt es keinen lokalen
        # Vision-Helfer mehr - ein blindes Modell bekommt nur noch den Hinweis.
        import main
        p = _png(30, 30)
        res = A.ActionResult("ok", ok=True, bilder=[str(p)])
        uris, res2 = asyncio.run(main._bilder_aus_ergebnis(res, "ollama_cloud:kimi-k2.6"))
        self.assertIsNone(uris)
        self.assertIn("sieht keine Bilder", res2.text)
        self.assertEqual(res2.bilder, res.bilder)


if __name__ == "__main__":
    unittest.main()
