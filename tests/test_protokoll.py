"""Offline-Tests: Aktions-Protokoll (tools/protokoll.py) – Laras Frage 5.

Es gab Werkzeugbilanz (nur Anzeige), Zähler (stats.json) und F12 (manuell),
aber nichts, in dem automatisch steht: wann lief welche Aktion mit welchem
Befehl. Jetzt schreibt jede Aktion eine Zeile nach learned/aktionen.log.

python -m unittest discover -s tests -p test_protokoll.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import protokoll as P     # noqa: E402


class Schreiben(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "learned" / "aktionen.log"

    def tearDown(self):
        self._tmp.cleanup()

    def test_eine_zeile_pro_aktion(self):
        P.schreibe("befehl", "Befehl: git status", "success", veraendernd=True,
                   wer="Lara", pfad=self.log)
        P.schreibe("datei_lesen", "Datei lesen: x.py", "success", veraendernd=False,
                   wer="Lara", pfad=self.log)
        zeilen = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(zeilen), 2)
        self.assertIn("ÄNDERT", zeilen[0])
        self.assertIn("befehl", zeilen[0])
        self.assertIn("ausgeführt", zeilen[0])
        self.assertIn("Lara", zeilen[0])
        self.assertIn("git status", zeilen[0])
        self.assertIn("liest", zeilen[1])

    def test_der_ordner_wird_angelegt(self):
        self.assertFalse(self.log.parent.exists())
        P.schreibe("x", "y", "success", veraendernd=False, pfad=self.log)
        self.assertTrue(self.log.exists())

    def test_status_wird_uebersetzt(self):
        for status, wort in (("rejected", "ABGELEHNT"), ("blocked", "GESPERRT"),
                             ("failed", "FEHLGESCHLAGEN"), ("not_run", "nicht gelaufen")):
            P.schreibe("t", "b", status, veraendernd=True, pfad=self.log)
        text = self.log.read_text(encoding="utf-8")
        for wort in ("ABGELEHNT", "GESPERRT", "FEHLGESCHLAGEN", "nicht gelaufen"):
            self.assertIn(wort, text)

    def test_zeilenumbrueche_in_der_beschreibung_fliegen_raus(self):
        # Sonst zerreisst eine mehrzeilige Beschreibung das Zeilenformat.
        P.schreibe("befehl", "Befehl:\n  Get-ChildItem\n  | Sort", "success",
                   veraendernd=True, pfad=self.log)
        self.assertEqual(len(self.log.read_text(encoding="utf-8").splitlines()), 1)

    def test_lange_beschreibung_wird_gekappt(self):
        P.schreibe("befehl", "x" * 5000, "success", veraendernd=True, pfad=self.log)
        self.assertLess(len(self.log.read_text(encoding="utf-8")), 500)

    def test_zeitstempel_vorn(self):
        import re
        P.schreibe("t", "b", "success", veraendernd=False, pfad=self.log)
        self.assertRegex(self.log.read_text(encoding="utf-8"),
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \|")

    def test_fehler_beim_schreiben_werfen_nicht(self):
        # Ein kaputtes Protokoll darf nie eine Aktion verhindern.
        unmoeglich = Path(self._tmp.name) / "datei.txt" / "unter" / "log"
        (Path(self._tmp.name) / "datei.txt").write_text("x")   # Datei, kein Ordner
        P.schreibe("t", "b", "success", veraendernd=False, pfad=unmoeglich)   # kein Fehler

    def test_letzte_gibt_die_juengsten(self):
        for i in range(50):
            P.schreibe("t", f"nr {i}", "success", veraendernd=False, pfad=self.log)
        z = P.letzte(5, pfad=self.log)
        self.assertEqual(len(z), 5)
        self.assertIn("nr 49", z[-1])
        self.assertIn("nr 45", z[0])

    def test_letzte_ohne_datei_ist_leer(self):
        self.assertEqual(P.letzte(pfad=self.log), [])


class Drehen(unittest.TestCase):
    def test_ab_grenze_wird_gedreht(self):
        with tempfile.TemporaryDirectory() as d:
            alt_pfad, alt_max = P.PFAD, P.MAX_BYTES
            try:
                P.PFAD = Path(d) / "aktionen.log"
                P.MAX_BYTES = 200
                for i in range(20):
                    P.schreibe("t", "x" * 50, "success", veraendernd=False)
                self.assertTrue(P.PFAD.with_suffix(".log.1").exists())
                self.assertLess(P.PFAD.stat().st_size, 400)
            finally:
                P.PFAD, P.MAX_BYTES = alt_pfad, alt_max


class Verdrahtung(unittest.TestCase):
    def test_alle_wege_schreiben_mit(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        # Hauptweg: ausgefuehrt, abgelehnt, gesperrt; Helfer: dasselbe.
        self.assertGreaterEqual(code.count("protokoll.schreibe("), 6)
        self.assertIn('"rejected"', code)
        self.assertIn('"blocked"', code)

    def test_helfer_schreiben_mit_ihrem_namen(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('wer=f"Helfer {lab}"', code)

    def test_befehl_ist_registriert(self):
        from commands import COMMAND_LIST
        self.assertIn("/protokoll", [c.name for c in COMMAND_LIST])

    def test_tests_schreiben_nie_ins_echte_protokoll(self):
        # Die Flow- und Helfer-Tests fuehren den echten Ablauf aus - sie
        # muessen das Protokoll umleiten, sonst landen Testzeilen im echten Protokoll.
        for name in ("test_execution_flow.py", "test_subagents.py"):
            code = (ROOT / "tests" / name).read_text(encoding="utf-8")
            self.assertIn("protokoll.PFAD =", code, name)


if __name__ == "__main__":
    unittest.main()
