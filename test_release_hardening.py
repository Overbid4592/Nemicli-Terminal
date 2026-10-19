"""Offline-Regressionstests fuer Modellintegritaet und Installer-Grenzen."""
from __future__ import annotations
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from einrichtungshelfer.laufzeit import stuecke_zusammensetzen
from installer.aufspielen import aufspielen

class ModellIntegritaet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.datei = Path(self.tmp.name) / "helfer.bin"
        self.inhalt = b"NEMIH1" + b"nur synthetische Testgewichte"
        for i, teil in enumerate((self.inhalt[:8], self.inhalt[8:]), 1):
            (self.datei.parent / f"helfer.bin.{i:03d}").write_bytes(teil)

    def soll_schreiben(self):
        digest = hashlib.sha256(self.inhalt).hexdigest()
        (self.datei.parent / "helfer.bin.sha256").write_text(
            f"{digest}  helfer.bin\n", encoding="utf-8")

    def test_korrekte_stuecke_und_vorhandene_datei(self):
        self.soll_schreiben()
        self.assertTrue(stuecke_zusammensetzen(self.datei))
        self.assertEqual(self.datei.read_bytes(), self.inhalt)
        self.assertTrue(stuecke_zusammensetzen(self.datei))

    def test_fehlende_pruefsumme(self):
        self.assertFalse(stuecke_zusammensetzen(self.datei))
        self.assertFalse(self.datei.exists())

    def test_manipulierte_stuecke(self):
        self.soll_schreiben()
        (self.datei.parent / "helfer.bin.002").write_bytes(b"veraendert")
        self.assertFalse(stuecke_zusammensetzen(self.datei))
        self.assertFalse(self.datei.exists())

    def test_bereits_vorhandene_manipulierte_datei(self):
        self.soll_schreiben()
        self.datei.write_bytes(b"falsches Modell")
        self.assertFalse(stuecke_zusammensetzen(self.datei))

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
            with self.assertRaisesRegex(ValueError, "Unzulaessige Paketbestandteile"):
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
