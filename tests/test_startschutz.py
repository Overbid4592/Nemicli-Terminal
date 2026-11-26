"""Start mit Stoppuhr und Schutz: ein kaputter, verzichtbarer Schritt hält den Start nicht auf, alles wird gemessen.

python -m unittest tests.test_startschutz
"""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import main                                             # noqa: E402


class StartSchutzTests(unittest.TestCase):

    def setUp(self):
        self.start = main._Start()

    def test_fehler_bricht_den_start_nicht_ab(self):
        weiter = []
        with mock.patch.object(main.ui, "warn") as warn, mock.patch("debugbericht.fehler") as fehler:
            with self.start.schritt("Gespräch fortsetzen"):
                raise ValueError("Chat-Datei kaputt")
            weiter.append("danach")                              # der Start geht weiter
        self.assertEqual(["danach"], weiter)
        self.assertEqual(["Gespräch fortsetzen"], self.start.fehler)
        self.assertIn("Chat-Datei kaputt", warn.call_args[0][0])
        self.assertEqual("Start: Gespräch fortsetzen", fehler.call_args[0][0])

    def test_jeder_schritt_wird_gemessen(self):
        with self.start.schritt("schnell"):
            pass
        with mock.patch.object(main._Start, "LANGSAM_S", 0.05):
            with self.start.schritt("Wache"):
                time.sleep(0.08)
            bericht = self.start.bericht()
        self.assertEqual(["schnell", "Wache"], [n for n, _ in self.start.zeiten])
        self.assertIn("Start fertig:", bericht)
        self.assertIn("langsam: Wache", bericht)
        self.assertNotIn("schnell", bericht)
        self.assertNotIn("gescheitert", bericht)

    def test_gescheiterte_schritte_im_bericht(self):
        with mock.patch.object(main.ui, "warn"), mock.patch("debugbericht.fehler"):
            with self.start.schritt("Auto-Backup"):
                raise OSError("Platte voll")
        self.assertIn("gescheitert: Auto-Backup", self.start.bericht())

    def test_sicherheit_bleibt_ungeschuetzt(self):
        quelle = (ROOT / "main_start.py").read_text(encoding="utf-8")
        zeile = next(z for z in quelle.splitlines() if "sicherheit.sitzung_zuruecksetzen()" in z)
        self.assertTrue(zeile.startswith("    sicherheit."), zeile)          # direkt in main(), in keinem Schutz-Block


if __name__ == "__main__":
    unittest.main()
