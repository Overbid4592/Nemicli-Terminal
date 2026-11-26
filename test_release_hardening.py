"""Offline-Regressionstests fuer die Installer-Grenzen."""
from __future__ import annotations
import tempfile
import unittest
import zipfile
from pathlib import Path
from installer.aufspielen import aufspielen

class InstallerDatenschutz(unittest.TestCase):
    def test_unbekannter_zip_ordner_darf_chats_nicht_ersetzen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ziel = root / "installation"
            privat = ziel / "chats"
            privat.mkdir(parents=True)
            (privat / "verlauf.db").write_bytes(b"bleibt privat")
            paket = root / "paket.zip"
            with zipfile.ZipFile(paket, "w") as z:
                z.writestr("NemiCLI.exe", b"dummy")
                z.writestr("NemiCLIT2/build.txt", "test")
                z.writestr("chats/verlauf.db", b"ersetzt")
            with self.assertRaisesRegex(ValueError, "mehr als Programmteile: chats"):
                aufspielen(paket, ziel)
            self.assertEqual((privat / "verlauf.db").read_bytes(), b"bleibt privat")

    def test_bekannte_programmteile_bleiben_installierbar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paket = root / "paket.zip"
            with zipfile.ZipFile(paket, "w") as z:
                z.writestr("NemiCLI.exe", b"dummy")
                z.writestr("NemiCLIT2/build.txt", "test")
            teile = aufspielen(paket, root / "installation")
            self.assertIn("NemiCLI.exe", teile)
            self.assertTrue((root / "installation" / "NemiCLIT2" / "build.txt").exists())

if __name__ == "__main__":
    unittest.main()
