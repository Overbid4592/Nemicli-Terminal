"""tools/vision.py: umgewandelte Bilder bleiben unter der Größengrenze der Anbieter."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from PIL import Image   # noqa: E402

import vision           # noqa: E402


class UmwandelnTests(unittest.TestCase):
    def test_grosses_umgewandeltes_bild_wird_verkleinert(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rauschen.bmp"
            Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3)).save(p)   # PNG > GROSS_BYTES
            self.assertTrue(vision.to_data_uri(p).startswith("data:image/jpeg;base64,"))

    def test_kleines_umgewandeltes_bild_bleibt_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "klein.bmp"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(p)
            self.assertTrue(vision.to_data_uri(p).startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
