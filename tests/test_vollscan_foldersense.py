"""Vollscan-Grenze (höchstens 3 neue in 7 Tagen) und FolderSense bei großen oder kaputten Ordnern."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import foldersense as FS  # noqa: E402
from wache import vollscan as VS  # noqa: E402


class VollscanGrenze(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        for name, wert in (("STARTS", self.ordner / "starts.json"), ("STAND", self.ordner / "stand.json"),
                           ("STOPP", self.ordner / "stopp")):
            p = patch.object(VS, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_drei_pro_woche_fortsetzen_zaehlt_nicht(self):
        jetzt = time.time()
        self.assertTrue(VS.woche_frei(jetzt)[0])
        for t in (jetzt - 8 * 86400, jetzt - 3 * 86400, jetzt - 2 * 86400):
            VS._start_merken(t)
        self.assertTrue(VS.woche_frei(jetzt)[0])                     # älter als 7 Tage zählt nicht
        VS._start_merken(jetzt - 3600)
        frei, ab = VS.woche_frei(jetzt)
        self.assertFalse(frei)
        self.assertAlmostEqual(jetzt - 3 * 86400 + VS.WOCHE, ab)    # frei, wenn der älteste herausfällt
        self.assertIn("Höchstens 3 Vollscans", VS.sperre_text(jetzt))
        with patch.object(VS, "laeuft", lambda: None), patch.object(VS.subprocess, "Popen") as popen:
            self.assertIn("Höchstens 3", VS.starten(self.ordner))
            popen.assert_not_called()
            VS.STAND.write_text(json.dumps({"phase": "abgebrochen"}), encoding="utf-8")
            with patch.object(VS.time, "sleep", lambda s: None):
                VS.starten(self.ordner)                              # Fortsetzen geht immer …
            popen.assert_called_once()
        self.assertEqual(3, len(VS.starts_lesen()))                  # … und zählt nicht

    def test_kaputte_startliste(self):
        VS.STARTS.write_text("{kaputt", encoding="utf-8")
        self.assertEqual([], VS.starts_lesen())
        self.assertTrue(VS.woche_frei()[0])


class FolderSense(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for name, wert in (("LEARN_DIR", self.tmp / "learned"), ("MODEL_PATH", self.tmp / "learned" / "m.json"),
                           ("EXAMPLES_PATH", self.tmp / "learned" / "ex.json"), ("_MODEL_CACHE", None)):
            p = patch.object(FS, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_liest_hoechstens_max_entries(self):
        ordner = self.tmp / "viel"
        ordner.mkdir()
        for i in range(FS.MAX_ENTRIES + 300):
            (ordner / f"f{i}.py").touch()
        gelesen = []
        echt = os.scandir

        class Zaehler:
            def __init__(self, pfad):
                self._it = echt(pfad)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self._it.close()

            def __iter__(self):
                for e in self._it:
                    gelesen.append(e)
                    yield e

        with patch.object(FS.os, "scandir", Zaehler):
            toks = FS.features(ordner)
        self.assertEqual(FS.MAX_ENTRIES, len(toks))
        self.assertEqual(FS.MAX_ENTRIES, len(gelesen))               # nicht erst alles einlesen

    def test_fehlender_ordner_und_verknuepfung(self):
        self.assertEqual([], FS.features(self.tmp / "gibtsnicht"))
        ziel = self.tmp / "ziel"
        (ziel / ".git").mkdir(parents=True)
        projekt = self.tmp / "projekt"
        projekt.mkdir()
        try:
            os.symlink(ziel / ".git", projekt / ".git", target_is_directory=True)
        except OSError:
            self.skipTest("Verknüpfungen brauchen hier Entwicklermodus/Rechte")
        self.assertNotIn("dir:.git", FS.features(projekt))           # Verknüpfung wird nicht verfolgt

    def test_kaputte_beispiele_werden_nicht_ueberschrieben(self):
        FS.LEARN_DIR.mkdir(parents=True)
        FS.EXAMPLES_PATH.write_text("[{kaputt", encoding="utf-8")
        ordner = self.tmp / "musik"
        ordner.mkdir()
        (ordner / "a.mp3").touch()
        self.assertTrue(FS.learn_folder(ordner, "musik"))
        self.assertTrue(FS.EXAMPLES_PATH.with_suffix(".kaputt").exists())   # alte Datei beiseitegelegt
        self.assertEqual(1, len(json.loads(FS.EXAMPLES_PATH.read_text(encoding="utf-8"))))

    def test_ungueltige_eintraege_werden_ignoriert(self):
        FS.LEARN_DIR.mkdir(parents=True)
        FS.EXAMPLES_PATH.write_text(json.dumps([{"tokens": ["ext:.py"]}, {"label": "musik", "tokens": ["ext:.mp3"]},
                                                {"label": "gibtsnicht", "tokens": []}]), encoding="utf-8")
        self.assertEqual(1, FS.stats(self.tmp)["learned"])
        FS.MODEL_PATH.write_text(json.dumps({"labels": {}}), encoding="utf-8")
        FS._MODEL_CACHE = None
        self.assertIn("vocab", FS._get_model())                      # unvollständiges Modell neu gebaut


if __name__ == "__main__":
    unittest.main()
