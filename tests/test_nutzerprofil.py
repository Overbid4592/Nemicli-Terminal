"""„Über dich“ (/name, Desktop-Fenster): Speichern, Abschnitt im Prompt direkt nach der Persönlichkeit."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "tools", "engines", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import config          # noqa: E402
import nutzerprofil    # noqa: E402
import persona         # noqa: E402


class MitConfig(unittest.TestCase):
    def setUp(self):
        self.daten = {}
        for p in (mock.patch.object(config, "load", side_effect=lambda: dict(self.daten)),
                  mock.patch.object(config, "save", side_effect=lambda d: self.daten.update(d) or None)):
            p.start()
            self.addCleanup(p.stop)


class Profil(MitConfig):
    def test_leer_kein_abschnitt(self):
        self.assertTrue(nutzerprofil.leer())
        self.assertEqual(nutzerprofil.prompt_abschnitt(), "")
        self.assertNotIn("Dein Gegenüber", persona.base_prompt())

    def test_speichern_und_abschnitt(self):
        nutzerprofil.speichern("  Alex  ", "Alex,  Kumpel", "Mag kurze Antworten.\nMag Python.")
        self.assertEqual(self.daten["nutzername"], "Alex")
        self.assertEqual(self.daten["nutzer_anrede"], "Alex, Kumpel")
        abschnitt = nutzerprofil.prompt_abschnitt()
        for teil in ("- Name: Alex", "angesprochen werden: Alex, Kumpel", "Mag kurze Antworten.\nMag Python.",
                     "halte dich von Anfang an daran"):
            self.assertIn(teil, abschnitt)

    def test_steht_direkt_nach_der_persoenlichkeit(self):
        nutzerprofil.speichern("Alex", "", "Mag Listen.")
        basis = persona.base_prompt()
        pos = basis.index("# Dein Gegenüber")
        self.assertLess(pos, basis.index("# Grundregeln"))
        self.assertIn("Mag Listen.", persona.kompakt(basis))              # auch für lokale Modelle

    def test_grenzen_und_leeren(self):
        nutzerprofil.speichern("n" * 500, "a\nb", "x" * 10_000)
        p = nutzerprofil.laden()
        self.assertEqual(len(p["name"]), nutzerprofil.MAX_NAME)
        self.assertEqual(p["anrede"], "a b")
        self.assertEqual(len(p["ueber"]), nutzerprofil.MAX_UEBER)
        nutzerprofil.speichern("", "", "")
        self.assertTrue(nutzerprofil.leer())


class Dialog(unittest.TestCase):
    def test_felder_hin_und_zurueck(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            import gui_fenster
        except ImportError:
            self.skipTest("PySide6 fehlt")
        QApplication.instance() or QApplication(["test"])
        d = gui_fenster.ProfilDialog({"name": "Alex", "anrede": "Kumpel", "ueber": "Mag Tee."})
        self.assertEqual(d.werte(), {"name": "Alex", "anrede": "Kumpel", "ueber": "Mag Tee."})
        d.ueber.setPlainText("  Mag Kaffee.  ")
        self.assertEqual(d.werte()["ueber"], "Mag Kaffee.")


if __name__ == "__main__":
    unittest.main()
