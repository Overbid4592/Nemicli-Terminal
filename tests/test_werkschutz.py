"""Erzeugte Werke (Bilder, Videos, Musik, Stimme): ansehen ja, ändern, verschieben und löschen nur der Nutzer."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions                 # noqa: E402
import config                  # noqa: E402
import paths                   # noqa: E402
import persoenlichkeiten as PS  # noqa: E402


class Werkschutz(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.daten = self.tmp / "Daten"
        self.profil = self.daten / "Profile" / "Alpha"
        self.draussen = self.tmp / "Ablage"
        programm = self.tmp / "Programm"
        for o in (self.daten / "Persoenlichkeiten", self.daten / "Bilder", self.draussen, programm):
            o.mkdir(parents=True)
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(paths, "INSTALL", programm),
                  mock.patch.object(PS, "DIR", self.daten / "Persoenlichkeiten"),
                  mock.patch.object(config, "_FILE", self.tmp / "cfg.json"),
                  mock.patch.object(actions, "_web_taint", False)):
            p.start()
            self.addCleanup(p.stop)
        actions._PERS_CACHE.update(schluessel=None, wert=None)
        self.addCleanup(actions._PERS_CACHE.update, schluessel=None, wert=None)
        PS.create("Alpha", "eins")
        PS.set_active("alpha")
        for o in ("Bilder", "Videos", "Musik", "Stimme", "Chats"):
            (self.profil / o).mkdir(parents=True, exist_ok=True)
        self.bild = self.profil / "Bilder" / "krea_1.png"
        self.bild.write_bytes(b"png")
        (self.daten / "Bilder" / "alt.png").write_bytes(b"png")

    def test_werke_nicht_aendern(self):
        for p in (self.bild, self.profil / "Videos" / "a.mp4", self.profil / "Musik" / "a.wav",
                  self.profil / "Stimme" / "a.wav", self.daten / "Bilder" / "alt.png", self.profil / "BILDER",
                  self.profil / "Bilder." / "krea_1.png", self.profil / "Chats" / ".." / "Bilder" / "krea_1.png"):
            self.assertIn("🖼️ Geschützt", actions._guard(str(p)) or "", p)
            self.assertIsNone(actions._read_guard(str(p)), p)            # ansehen bleibt frei

    def test_anderes_bleibt_frei(self):
        for p in (self.profil / "Chats" / "1.json", self.daten / "Zeitplan" / "a.json",
                  self.draussen / "Bilder" / "x.png"):                    # „Bilder“ außerhalb der Daten
            self.assertIsNone(actions._guard(str(p)), p)

    def test_werkzeuge(self):
        def ok(r):
            return getattr(r, "ok", True) and not str(r).startswith("Fehler")
        self.assertFalse(ok(actions._loeschen({"pfad": str(self.bild)})))
        self.assertFalse(ok(actions._loeschen({"pfad": str(self.profil)})))          # Profil enthält Werke
        self.assertFalse(ok(actions._verschieben({"von": str(self.bild), "nach": str(self.draussen / "a.png")})))
        self.assertFalse(ok(actions._verschieben({"von": str(self.profil / "Bilder"),
                                                  "nach": str(self.draussen / "B")})))
        self.assertFalse(ok(actions._datei_schreiben({"pfad": str(self.bild), "inhalt": "x"})))
        self.assertTrue(self.bild.exists())
        r = actions._datei_kopieren({"von": str(self.bild), "nach": str(self.draussen / "kopie.png")})
        self.assertTrue(ok(r), r)                                            # kopieren an einen anderen Ort
        self.assertTrue((self.draussen / "kopie.png").exists())

    def test_befehle(self):
        g = actions._guard_command
        for c in (f"Remove-Item '{self.bild}'",
                  f"Move-Item {self.profil}\\Bilder\\*.png {self.draussen}",
                  f"Remove-Item {self.profil} -Recurse",
                  f"Set-Content {self.profil}\\Musik\\a.wav x",
                  f"robocopy {self.profil}\\Bilder {self.draussen} /MOV",
                  f"[System.IO.File]::Delete('{self.bild}')",
                  f"$o = '{self.profil}\\Bilder'; Remove-Item $o\\krea_1.png"):
            self.assertIn("🖼️ Geschützt", g(c) or "", c)
        self.assertIsNotNone(g(f"Remove-Item {self.daten}\\Profile\\*\\Bilder\\* -Force"))   # auch Profil-Grenze
        self.assertIsNone(g(f"Get-ChildItem {self.profil}\\Bilder", lesend=True))
        self.assertIsNone(g(f"Remove-Item {self.draussen}\\x.txt"))

    def test_befehle_relativ_im_profil(self):
        with mock.patch.object(actions, "befehl_ordner", return_value=self.profil):
            self.assertIn("🖼️ Geschützt", actions._guard_command("Remove-Item Bilder\\krea_1.png") or "")
            self.assertIn("🖼️ Geschützt", actions._guard_command("Remove-Item * -Recurse") or "")
        with mock.patch.object(actions, "befehl_ordner", return_value=self.profil / "Bilder"):
            self.assertIn("🖼️ Geschützt", actions._guard_command("del krea_1.png") or "")


if __name__ == "__main__":
    unittest.main()
