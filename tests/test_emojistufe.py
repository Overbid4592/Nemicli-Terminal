"""Offline-Tests: Emoji-Stufe (tools/emojistufe.py, /emoji, Werkzeug emoji_stufe).

python -m unittest discover -s tests -p test_emojistufe.py
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import emojistufe   # noqa: E402


class MitConfig(unittest.TestCase):
    def setUp(self):
        self.daten = {}
        import config
        self.p = [mock.patch.object(config, "load", side_effect=lambda: dict(self.daten)),
                  mock.patch.object(config, "update", side_effect=lambda **kw: self.daten.update(kw) or True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()


class Stufe(MitConfig):
    def test_ohne_einstellung_frei_und_kein_hinweis(self):
        self.assertEqual(emojistufe.stufe(), "frei")
        self.assertEqual(emojistufe.prompt_hinweis(), "")

    def test_deutsche_namen_und_grossschreibung(self):
        for roh, erwartet in (("Low", "low"), ("wenig", "low"), ("mittel", "middle"), ("viel", "strong"),
                              ("STRONG", "strong"), ("aus", "frei"), ("quatsch", None), ("", None)):
            self.assertEqual(emojistufe.normal(roh), erwartet, roh)

    def test_setzen_und_wieder_frei(self):
        self.assertEqual(emojistufe.setzen("viel"), "strong")
        self.assertEqual(self.daten["emoji_stufe"], "strong")
        self.assertIn("# Emojis: strong", emojistufe.prompt_hinweis())
        emojistufe.setzen("frei")
        self.assertIsNone(self.daten["emoji_stufe"])
        self.assertEqual(emojistufe.stufe(), "frei")

    def test_unbekannt_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            emojistufe.setzen("riesig")
        self.assertEqual(self.daten, {})

    def test_kaputter_wert_in_der_config_gilt_als_frei(self):
        self.daten["emoji_stufe"] = "x"
        self.assertEqual(emojistufe.stufe(), "frei")

    def test_hinweis_je_stufe_mit_passend_und_ohne_code(self):
        for s in emojistufe.STUFEN:
            self.daten["emoji_stufe"] = s
            h = emojistufe.prompt_hinweis()
            self.assertIn("passt zum Inhalt", h)
            self.assertIn("Keine Emojis in Code", h)

    def test_hinweis_steht_im_systemprompt(self):
        import persona
        self.daten["emoji_stufe"] = "low"
        self.assertIn("# Emojis: low", "".join(persona._hinweise()))


class Werkzeug(MitConfig):
    def test_stand_setzen_und_fehler(self):
        import actions
        self.assertIn("frei", actions._emoji_stufe({}))
        self.assertTrue(actions._emoji_stufe({"stufe": "middle"}).startswith("Geändert."))
        self.assertEqual(self.daten["emoji_stufe"], "middle")
        self.assertFalse(actions._emoji_stufe({"stufe": "riesig"}).ok)

    def test_angemeldet_ohne_rueckfrage_und_als_begleiten(self):
        import actions
        import plan
        self.assertFalse(actions.ACTIONS["emoji_stufe"]["confirm"])
        self.assertIn("emoji_stufe", plan.BEGLEITEN)

    def test_menue_darf_die_ki_oeffnen(self):
        import commands
        self.assertEqual(commands.menue_pruefen("/emoji"), ("/emoji", ""))


if __name__ == "__main__":
    unittest.main()
