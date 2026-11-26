"""Tests: eigene Farbschemata (ui/themes_eigen.py, Werkzeug theme_erstellen).

python -m unittest tests.test_themes_eigen
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions  # noqa: E402
import themes_eigen as T  # noqa: E402
import ui  # noqa: E402


class Bauen(unittest.TestCase):
    def test_zwei_farben_ergeben_die_ganze_palette(self):
        name, p = T.bauen("Mitternacht", "Blau / Gold", "#6fa8ff", "FFD166")
        self.assertEqual(name, "mitternacht")
        self.assertEqual((p["brand"], p["accent"]), ("#6fa8ff", "#ffd166"))
        self.assertEqual(len(p["gradient"]), 6)
        self.assertEqual((p["gradient"][0], p["gradient"][-1]), ("#ffd166", "#6fa8ff"))
        self.assertLess(T._helligkeit(p["brand_dim"]), T._helligkeit(p["brand"]))

    def test_verlauf_aus_mehreren_stufen(self):
        _, p = T.bauen("regenbogen", "", "#ff8fab", "#8fd3ff", verlauf="#ff8fab, #ffe066 #8fd3ff")
        self.assertEqual(len(p["gradient"]), 6)
        self.assertEqual((p["gradient"][0], p["gradient"][-1]), ("#ff8fab", "#8fd3ff"))
        with self.assertRaises(ValueError):
            T.bauen("x1", "", "#ff8fab", "#8fd3ff", verlauf=["#ffffff"])

    def test_unlesbar_falsch_oder_eingebaut_wird_abgelehnt(self):
        for args, wort in ((("dunkel", "", "#1a1a2e", "#ffd166"), "schlecht lesbar"),
                           (("rot", "", "rot", "#ffd166"), "kein Hex"),
                           (("1abc", "", "#6fa8ff", "#ffd166"), "Name"),
                           (("cyan", "", "#6fa8ff", "#ffd166", None, ui.EINGEBAUT), "eingebautes")):
            with self.assertRaises(ValueError) as fehler:
                T.bauen(*args)
            self.assertIn(wort, str(fehler.exception))

    def test_eingebaute_themes_bestehen_die_lesbarkeit(self):
        for name in ui.EINGEBAUT:
            for rolle in ("brand", "accent"):
                self.assertGreaterEqual(T.kontrast(ui.THEMES[name][rolle]), T.MIN_KONTRAST, (name, rolle))


class Speichern(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.datei = Path(self.tmp.name) / "themes_eigen.json"
        self.flick = mock.patch.object(T, "datei", return_value=self.datei)
        self.flick.start()

    def tearDown(self):
        self.flick.stop()
        self.tmp.cleanup()

    def test_anlegen_laden_und_ueberschreiben_mit_sicherung(self):
        T.anlegen("wald", "Grün", "#7ddf9a", "#e9f5a1")
        self.assertEqual(list(T.laden()), ["wald"])
        with mock.patch("snapshot.sichern") as sichern:
            T.anlegen("wald", "Grün 2", "#7ddf9a", "#ffffff")
        sichern.assert_called_once()
        self.assertEqual(T.laden()["wald"]["label"], "Grün 2")

    def test_kaputte_eintraege_werden_uebersprungen(self):
        self.datei.write_text(json.dumps({"gut": {"label": "x", "brand": "#7ddf9a", "accent": "#ffffff"},
                                          "kaputt": {"brand": "lila"}}), encoding="utf-8")
        self.assertEqual(list(T.laden()), ["gut"])

    def test_hoechstzahl(self):
        with mock.patch.object(T, "HOECHSTENS", 1):
            T.anlegen("eins", "", "#7ddf9a", "#ffffff")
            with self.assertRaises(ValueError):
                T.anlegen("zwei", "", "#7ddf9a", "#ffffff")
            T.anlegen("eins", "neu", "#7ddf9a", "#ffffff")            # überschreiben geht

    def test_werkzeug_legt_an_und_macht_es_waehlbar(self):
        try:
            with mock.patch("snapshot.sichern"):
                res = actions._theme_erstellen({"tool": "theme_erstellen", "name": "sonne", "label": "Gelb",
                                                "brand": "#ffb347", "accent": "#fff1a8"})
            self.assertTrue(res.ok, res.text)
            self.assertIn("/theme sonne", res.text)
            self.assertIn("sonne", ui.THEMES)
            vorschau = actions.confirmation_preview({"tool": "theme_erstellen", "name": "sonne",
                                                      "brand": "#ffb347", "accent": "#fff1a8"})
            self.assertIn("überschreibt", vorschau)
            schlecht = actions._theme_erstellen({"name": "nacht", "brand": "#101010", "accent": "#fff1a8"})
            self.assertFalse(schlecht.ok)
        finally:
            ui.THEMES.pop("sonne", None)


if __name__ == "__main__":
    unittest.main()
