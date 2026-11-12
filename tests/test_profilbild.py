"""Profilbild der Persönlichkeit im Terminal: selbst gewählt (Kugel-Stimmung, Lieblingsbild, eigenes Bild)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions        # noqa: E402
import modes          # noqa: E402
import plan           # noqa: E402
import profilbild     # noqa: E402
import profilordner   # noqa: E402
import startseite     # noqa: E402
import tagebuch       # noqa: E402


class Profilbild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        kugel = self.tmp / "Persoenlichkeiten" / "Ada"
        kugel.mkdir(parents=True)
        self.neutral, self.froh = kugel / "neutral.png", kugel / "froh.png"
        for b in (self.neutral, self.froh):
            b.write_bytes(b"\x89PNG")
        for ziel, name, wert in ((profilordner, "wurzel", lambda: self.tmp / "Profile"),
                                 (profilordner, "aktiv", lambda: "Ada"),
                                 (profilbild, "_kugelbilder", lambda: {"": self.neutral, "froh": self.froh})):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        alt = dict(startseite.STAND)
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(alt)))

    def _eigenes(self, name: str) -> Path:
        pfad = profilordner.ordner("Bilder") / name
        pfad.write_bytes(b"\x89PNG")
        return pfad

    def test_ohne_wahl_das_standardbild(self):
        self.assertEqual(profilbild.pfad(), self.neutral)

    def test_waehlen_kugel_liebling_eigenes_und_zurueck(self):
        self.assertIn("Kugel-Bild „froh“", profilbild.setzen("Froh"))
        self.assertEqual(profilbild.pfad(), self.froh)
        strand = self._eigenes("strand.png")
        tagebuch.markieren(str(strand), "mein Strandbild")
        self.assertIn("Lieblingsbild", profilbild.setzen("mein Strandbild"))
        self.assertEqual(profilbild.pfad(), strand)
        neu = self._eigenes("gerade_gemalt.png")
        profilbild.setzen(str(neu))
        self.assertEqual(profilbild.pfad(), neu)
        self.assertEqual(profilbild.setzen("standard"), "wieder das Standardbild")
        self.assertEqual(profilbild.pfad(), self.neutral)

    def test_fremde_bilder_nicht(self):
        fremd = self.tmp / "woanders.png"
        fremd.write_bytes(b"\x89PNG")
        with self.assertRaises(ValueError):
            profilbild.setzen(str(fremd))
        self.assertEqual(profilbild.pfad(), self.neutral)

    def test_geloeschtes_bild_faellt_auf_standard_zurueck(self):
        neu = self._eigenes("weg.png")
        profilbild.setzen(str(neu))
        neu.unlink()
        self.assertEqual(profilbild.pfad(), self.neutral)

    def test_werkzeug_wechselt_die_startseite_sofort(self):
        startseite.STAND["bild"] = self.neutral
        with patch.object(startseite, "auffrischen") as frisch:
            antwort = actions._profilbild({"bild": "froh"})
        self.assertIn("🖼 Profilbild", str(antwort))
        self.assertEqual(startseite.STAND["bild"], self.froh)
        frisch.assert_called_once()
        self.assertIn("Kugel-Bilder: froh", actions._profilbild({}))              # ohne bild: die Auswahl
        self.assertFalse(actions._profilbild({"bild": str(self.tmp / "x.png")}).ok)

    def test_ueberall_erlaubt_wie_lieblingsbild(self):
        self.assertIn("profilbild", actions.ACTIONS)
        for menge in (modes.MEMORY_TOOLS, modes.WACHE_URTEIL_TOOLS, plan.BEGLEITEN):
            self.assertIn("profilbild", menge)


if __name__ == "__main__":
    unittest.main()
