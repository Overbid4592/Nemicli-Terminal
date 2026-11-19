"""Profil-Grenze: Sicherungen, Erinnerungen und Hintergrund-Aufgaben anderer Persönlichkeiten bleiben zu."""
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions                 # noqa: E402
import config                  # noqa: E402
import hintergrund             # noqa: E402
import paths                   # noqa: E402
import persoenlichkeiten as PS  # noqa: E402
from wache import erinnerungen as ER  # noqa: E402


class Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.daten = self.tmp / "Daten"
        programm = self.tmp / "Programm"
        for o in (self.daten / "Persoenlichkeiten", self.daten / "Backup", programm):
            o.mkdir(parents=True)
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(paths, "INSTALL", programm),
                  mock.patch.object(PS, "DIR", self.daten / "Persoenlichkeiten"),
                  mock.patch.object(config, "_FILE", self.tmp / "cfg.json"),
                  mock.patch.object(ER, "ORDNER", self.tmp / "Erinnerungen"),
                  mock.patch.object(actions, "_web_taint", False)):
            p.start()
            self.addCleanup(p.stop)
        actions._PERS_CACHE.update(schluessel=None, wert=None)
        self.addCleanup(actions._PERS_CACHE.update, schluessel=None, wert=None)
        PS.create("Alpha", "eins")
        PS.create("Beta", "zwei")
        PS.set_active("alpha")


class Sicherungen(Basis):
    def setUp(self):
        super().setUp()
        self.zip = self.daten / "Backup" / "backup_voll.zip"
        with zipfile.ZipFile(self.zip, "w") as f:
            f.writestr("Profile/Beta/Chats/001.json", "geheim")

    def test_backup_ordner_zu(self):
        for p in (self.zip, self.daten / "Backup", self.daten / "backup." / "backup_voll.zip"):
            self.assertIn("Backup/", actions._read_guard(str(p)) or "", p)
            self.assertIn("Backup/", actions._guard(str(p)) or "", p)
        self.assertFalse(getattr(actions._datei_lesen({"pfad": str(self.zip)}), "ok", True))

    def test_backup_in_befehlen_zu(self):
        g = actions._guard_command
        for c in (f"Expand-Archive {self.zip} {self.tmp / 'x'}", f"tar -xf {self.zip} -C {self.tmp / 'x'}",
                  f"Get-ChildItem {self.daten}\\Backup", f"$b = '{self.daten}\\Backup'; Get-ChildItem $b"):
            self.assertIn("Backup/", g(c, lesend=True) or "", c)
        with mock.patch.object(actions, "befehl_ordner", return_value=self.daten):
            self.assertIn("Backup/", g("Expand-Archive Backup\\backup_voll.zip x", lesend=True) or "")
        self.assertIsNone(g(f"Get-ChildItem {self.tmp / 'Backup'}", lesend=True))     # fremdes „Backup“ frei


class Erinnerungen(Basis):
    def test_nur_eigene_und_ohne_besitzer(self):
        zeit = time.time() + 3600
        eigen = ER.anlegen("Tee kochen", zeit, wer="Alpha")
        fremd = ER.anlegen("Geheimnis", zeit, wer="Beta")
        ER.anlegen("Allgemein", zeit, wer="")
        text = str(actions._erinnern({}))
        self.assertIn("Tee kochen", text)
        self.assertIn("Allgemein", text)
        self.assertNotIn("Geheimnis", text)
        self.assertFalse(getattr(actions._erinnern({"loeschen": fremd["id"]}), "ok", True))
        self.assertEqual(3, len(ER.offen()))
        self.assertIn("gelöscht", str(actions._erinnern({"loeschen": eigen["id"]})))
        self.assertTrue(ER.loeschen(fremd["id"]))                              # der Nutzer darf jede


class Aufgaben(Basis):
    def test_nur_eigene_aufgaben(self):
        hintergrund._test_reset()
        self.addCleanup(hintergrund._test_reset)
        hintergrund._AUFGABEN[1] = {"nr": 1, "titel": "Serie Alpha", "start": time.time(), "ende": time.time(),
                                    "ergebnis": "fertig", "ok": True, "wer": "alpha", "tool": "bild_serie"}
        hintergrund._AUFGABEN[2] = {"nr": 2, "titel": "Serie Beta", "start": time.time(), "ende": time.time(),
                                    "ergebnis": "Beta-Geheimnis", "ok": True, "wer": "beta", "tool": "bild_serie"}
        text = actions._aufgaben({})
        self.assertIn("Serie Alpha", text)
        self.assertNotIn("Beta", text)
        self.assertIn("1 weitere einer anderen", text)


if __name__ == "__main__":
    unittest.main()
