"""Offline-Tests: automatische Sicherung (tools/autobackup.py) und sicheres Schreiben des Gedächtnisses.

python -m unittest discover -s tests -p test_autobackup.py
"""

import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import autobackup as AB   # noqa: E402
import backup             # noqa: E402
import memory             # noqa: E402
import paths              # noqa: E402


def notizen(n: int) -> str:
    return json.dumps({"next_id": n + 1, "entries": [{"id": i, "text": f"Notiz {i}"} for i in range(n)]})


class AutobackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.daten, self.install = t / "daten", t / "programm"
        for p in (patch.object(paths, "DATEN", self.daten), patch.object(paths, "INSTALL", self.install),
                  patch("protokoll.schreibe", lambda *a, **k: None)):
            p.start(); self.addCleanup(p.stop)
        d = self.daten
        (d / "learned").mkdir(parents=True)
        (d / "learned" / "memory.json").write_text(notizen(20), encoding="utf-8")
        (d / "learned" / "memory.db").write_bytes(b"SQLite format 3\x00" + b"0" * 100)
        (d / "Profile" / "Test" / "Erinnerungen").mkdir(parents=True)
        (d / "Profile" / "Test" / "Erinnerungen" / "memory.json").write_text(notizen(5), encoding="utf-8")
        (d / "Profile" / "Test" / "test.json").write_text("{}", encoding="utf-8")
        (d / "Profile" / "Test" / "Bilder").mkdir()
        (d / "Profile" / "Test" / "Bilder" / "bild.png").write_bytes(b"x" * 10)
        (d / "Persoenlichkeiten").mkdir()
        (d / "Persoenlichkeiten" / "test.md").write_text("# Test", encoding="utf-8")
        (d / "Wache" / "Erinnerungen").mkdir(parents=True)
        (d / "Wache" / "gruesse.md").write_text("Hallo", encoding="utf-8")
        (d / "Wache" / "wache.db").write_bytes(b"x")
        (d / "Wache" / "Erinnerungen" / "abc.json").write_text("{}", encoding="utf-8")
        (d / "Bilder").mkdir()
        (d / "Bilder" / "gross.png").write_bytes(b"x" * 10)
        self.install.mkdir()
        (self.install / "nemicli.config.json").write_text("{}", encoding="utf-8")

    def namen(self, zip_name):
        with zipfile.ZipFile(backup.ordner() / zip_name) as zf:
            return set(zf.namelist())

    def test_sichert_das_wichtige_ohne_bilder_und_datenbanken(self):
        self.assertTrue(AB.faellig())
        r = AB.sichern()
        self.assertTrue(r["ok"], r)
        n = self.namen(AB.AKTUELL)
        for soll in ("Daten/learned/memory.json", "Daten/Profile/Test/Erinnerungen/memory.json",
                     "Daten/Profile/Test/test.json", "Daten/Persoenlichkeiten/test.md", "Daten/Wache/gruesse.md",
                     "Daten/Wache/Erinnerungen/abc.json", "Programm/nemicli.config.json",
                     "BACKUP.json", "SHA256SUMS.txt"):
            self.assertIn(soll, n)
        for nie in ("Daten/learned/memory.db", "Daten/Profile/Test/Bilder/bild.png", "Daten/Wache/wache.db",
                    "Daten/Bilder/gross.png"):
            self.assertNotIn(nie, n)
        self.assertEqual("auto", backup.info(backup.ordner() / AB.AKTUELL)["art"])
        self.assertEqual(25, AB.stand()["notizen"])
        self.assertFalse(AB.faellig())
        self.assertEqual([(AB.AKTUELL, True, "Prüfsumme stimmt")], backup.pruefen(AB.AKTUELL))
        self.assertFalse((backup.ordner() / AB.SPERRE).exists())

    def test_zwei_staende_und_ueberschreiben(self):
        AB.sichern()
        sha1 = (backup.ordner() / (AB.AKTUELL + ".sha256")).read_text(encoding="utf-8").split()[0]
        (self.daten / "Agenten").mkdir()
        (self.daten / "Agenten" / "neu.md").write_text("neu", encoding="utf-8")
        AB.sichern(erzwingen=True)
        self.assertIn("Daten/Agenten/neu.md", self.namen(AB.AKTUELL))
        self.assertNotIn("Daten/Agenten/neu.md", self.namen(AB.VORHER))
        self.assertEqual(f"{sha1}  {AB.VORHER}\n",
                         (backup.ordner() / (AB.VORHER + ".sha256")).read_text(encoding="utf-8"))
        AB.sichern(erzwingen=True)
        zips = sorted(p.name for p in backup.ordner().glob("NemiCLI_auto*.zip"))
        self.assertEqual([AB.AKTUELL, AB.VORHER], zips)                    # nie mehr als zwei
        self.assertIn("NemiCLI_auto_aktuell.zip", backup.status_text())
        aus = backup.auspacken(AB.AKTUELL)
        self.assertTrue((aus.ordner / "Daten" / "learned" / "memory.json").exists())
        self.assertEqual([], aus.fehler)

    def test_haelt_an_bei_kaputtem_oder_halbem_gedaechtnis(self):
        AB.sichern()
        alt = (backup.ordner() / (AB.AKTUELL + ".sha256")).read_text(encoding="utf-8")
        (self.daten / "learned" / "memory.json").write_text('{"entries": [', encoding="utf-8")
        r = AB.sichern()
        self.assertFalse(r["ok"]); self.assertTrue(r["angehalten"]); self.assertIn("nicht lesbar", r["text"])
        self.assertEqual(alt, (backup.ordner() / (AB.AKTUELL + ".sha256")).read_text(encoding="utf-8"))
        self.assertIn("angehalten", AB.status_text().lower())
        self.assertFalse(AB.faellig())                                     # nicht stündlich wiederholen
        (self.daten / "learned" / "memory.json").write_text(notizen(2), encoding="utf-8")
        r = AB.sichern()
        self.assertTrue(r["angehalten"]); self.assertIn("nur noch 7 statt 25", r["text"])
        r = AB.sichern(erzwingen=True)
        self.assertTrue(r["ok"]); self.assertEqual(7, AB.stand()["notizen"])

    def test_sperre_eines_anderen_prozesses(self):
        backup.ordner().mkdir(parents=True)
        (backup.ordner() / AB.SPERRE).write_text("123", encoding="utf-8")
        self.assertIn("anderen", AB.sichern()["text"])
        alt = time.time() - AB.SPERRE_ALT_S - 10
        import os
        os.utime(backup.ordner() / AB.SPERRE, (alt, alt))                 # abgestürzt: Sperre verfällt
        self.assertTrue(AB.sichern()["ok"])

    def test_werkzeug_der_ki(self):
        import actions
        r = actions._backup_erstellen({"tool": "backup_erstellen", "art": "auto"})
        self.assertTrue(r.ok, r.text)
        self.assertTrue((backup.ordner() / AB.AKTUELL).exists())
        self.assertIn("automatische Sicherung", actions.describe({"tool": "backup_erstellen", "art": "auto"}))


class GedaechtnisSicherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.datei = Path(self.tmp.name) / "memory.json"

    def test_bak_und_rueckfall(self):
        memory._save({"next_id": 2, "entries": [{"id": 1, "text": "a"}]}, self.datei)
        self.assertFalse(self.datei.with_name("memory.json.bak").exists())        # erste Fassung: nichts zu sichern
        memory._save({"next_id": 3, "entries": [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]}, self.datei)
        self.assertEqual(1, len(json.loads(self.datei.with_name("memory.json.bak").read_text(encoding="utf-8"))["entries"]))
        self.datei.write_text('{"entries": [{"id"', encoding="utf-8")         # Absturz mitten im Schreiben
        geladen = memory._load(self.datei)
        self.assertEqual(1, len(geladen["entries"]))                           # die letzte gute Fassung
        memory._save(geladen, self.datei)                                      # kaputte überschreibt die .bak nicht
        self.assertEqual(1, len(json.loads(self.datei.with_name("memory.json.bak").read_text(encoding="utf-8"))["entries"]))
        self.assertFalse(self.datei.with_name("memory.json.tmp").exists())

    def test_nur_bak_vorhanden(self):
        self.datei.with_name("memory.json.bak").write_text(json.dumps({"entries": [{"id": 1}]}), encoding="utf-8")
        self.assertEqual(1, len(memory._load(self.datei)["entries"]))
        self.assertEqual([], memory._load(Path(self.tmp.name) / "fehlt.json")["entries"])


if __name__ == "__main__":
    unittest.main()
