"""tools/pdfgen.py: kaputte Bildköpfe lassen nur das Bild aus, nicht das PDF."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import pdfgen   # noqa: E402


class BildkopfTests(unittest.TestCase):
    def test_abgeschnittener_bildkopf_liefert_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, roh in (("kurz.jpg", b"\xff\xd8\xff\xc0\x00\x11\x08"),
                              ("null.jpg", b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x00\x00\x00\x03"),
                              ("kurz.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR\x00\x00")):
                p = Path(tmp) / name
                p.write_bytes(roh)
                self.assertIsNone(pdfgen._load_image(p), name)


if __name__ == "__main__":
    unittest.main()
