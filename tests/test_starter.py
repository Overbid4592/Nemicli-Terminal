"""Offline-Tests für nemicli.cmd: der Starter behält den aktuellen Ordner.

Wechselt der Starter in den Programmordner (`pushd %~dp0`), gilt der als
Arbeitsordner – Ordner-Erkennung und Auto-Modus arbeiten dann am falschen Ort.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StarterBehaeltDeinenOrdner(unittest.TestCase):
    def setUp(self):
        self.cmd = (ROOT / "nemicli.cmd").read_text(encoding="utf-8", errors="replace")
        self.befehle = [z.strip() for z in self.cmd.splitlines()
                        if z.strip() and not z.strip().upper().startswith("REM")]

    def test_der_starter_wechselt_den_ordner_nicht(self):
        for zeile in self.befehle:
            low = zeile.lower()
            self.assertFalse(low.startswith(("pushd", "cd ", "cd/", "chdir")),
                             f"nemicli.cmd darf den Ordner nicht wechseln: {zeile}")

    def test_main_py_wird_mit_vollem_pfad_gerufen(self):
        # Ohne pushd muss der Pfad absolut sein, sonst findet Python main.py nicht.
        start = [z for z in self.befehle if "main.py" in z]
        self.assertTrue(start, "nemicli.cmd startet kein main.py mehr")
        for zeile in start:
            self.assertIn("%~dp0main.py", zeile,
                          f"main.py braucht den vollen Pfad: {zeile}")

    def test_nemiclis_eigene_daten_haengen_nicht_am_aktuellen_ordner(self):
        """Chats, Bilder und Gedächtnis finden ihren Platz über den Pfad des
        Programms, nicht über den Ordner, aus dem gestartet wurde."""
        code = (ROOT / "core" / "paths.py").read_text(encoding="utf-8")
        self.assertIn("Path(__file__).resolve().parent.parent", code)
        self.assertNotIn("getcwd", code)


if __name__ == "__main__":
    unittest.main()
