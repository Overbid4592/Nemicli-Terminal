"""Bau: nur die neuesten Setup-Dateien (nach Versionsnummer, nicht nach Text) bleiben liegen."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_exe                                       # noqa: E402


class SetupsAufraeumen(unittest.TestCase):
    def test_behaelt_die_drei_neuesten(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for v in ("8.2.570", "8.9.625", "8.10.630", "8.22.737", "8.22.739", "8.23.748"):
                (d / f"NemiCLI-Setup-{v}.exe").write_bytes(b"x")
                (d / f"NemiCLI-Setup-{v}.exe.sha256").write_text("h", encoding="utf-8")
            (d / "NemiCLI-Setup-test.exe").write_bytes(b"x")              # ohne Versionsnummer: bleibt
            weg = build_exe.alte_setups_weg(d)
            self.assertEqual(6, len(weg))
            self.assertEqual({"NemiCLI-Setup-8.22.737.exe", "NemiCLI-Setup-8.22.739.exe",
                              "NemiCLI-Setup-8.23.748.exe", "NemiCLI-Setup-test.exe"},
                             {p.name for p in d.glob("*.exe")})
            self.assertEqual(3, len(list(d.glob("*.sha256"))))

    def test_stabiler_stand_bleibt_und_zaehlt_nicht_mit(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for v in ("9.46.880", "9.47.888", "9.48.891", "9.49.900", "9.50.910"):
                (d / f"NemiCLI-Setup-{v}.exe").write_bytes(b"x")
                (d / f"NemiCLI-Setup-{v}.exe.sha256").write_text("h", encoding="utf-8")
            build_exe.alte_setups_weg(d)
            self.assertEqual({"NemiCLI-Setup-9.46.880.exe", "NemiCLI-Setup-9.48.891.exe",
                              "NemiCLI-Setup-9.49.900.exe", "NemiCLI-Setup-9.50.910.exe"},
                             {p.name for p in d.glob("*.exe")})
            self.assertTrue((d / "NemiCLI-Setup-9.46.880.exe.sha256").is_file())


if __name__ == "__main__":
    unittest.main()
