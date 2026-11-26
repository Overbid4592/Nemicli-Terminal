"""Regeln nach dem Geschlecht der Persönlichkeit: Bildregel 2, Tonlage und Hz-Sperre der Stimme, Männer-Wörter
der externen WebUI.

python -m unittest tests.test_geschlecht_regeln
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import persoenlichkeiten as PS                         # noqa: E402
import persona                                         # noqa: E402


def _person(text: str, name: str = "PersonA") -> PS.Persoenlichkeit:
    return PS.Persoenlichkeit(key=name.lower(), name=name, kurz="", text=text, path=None)


class Geschlecht(unittest.TestCase):

    def test_aus_der_geschlechtszeile(self):
        for g in ("w", "m", "n"):
            with self.subTest(g=g):
                self.assertEqual(g, PS.geschlecht(_person("# PersonA\n" + PS._GESCHLECHT[g] + "\n")))

    def test_aus_der_charakter_datei_oder_unbekannt(self):
        with mock.patch("charakter.roh", return_value={"aussehen": {"geschlecht": "m"}}):
            self.assertEqual("m", PS.geschlecht(_person("# PersonB\nOhne Zeile.")))
        with mock.patch("charakter.roh", return_value={}):
            self.assertEqual("", PS.geschlecht(_person("# PersonB\nOhne Zeile.")))


class Anleitung(unittest.TestCase):

    def _basis(self, g):
        with mock.patch.object(PS, "geschlecht", return_value=g):
            return persona.base_prompt(), persona.anleitung("stimme"), persona.helfer_prompt("Leser", "Lies")

    def test_weiblich_und_unbekannt_wie_bisher(self):
        for g in ("w", ""):
            basis, stimme, helfer = self._basis(g)
            with self.subTest(g=g):
                self.assertIn("2. Immer **weiblich** – keine Männer.", basis)
                self.assertIn("Unter 150 Hz klingt es männlich – das sperrt", stimme)
                self.assertNotIn("«", basis + stimme + helfer)

    def test_maennlich(self):
        basis, stimme, helfer = self._basis("m")
        self.assertIn("2. **Weiblich oder männlich**", basis)
        self.assertNotIn("Immer **weiblich**", basis)
        self.assertIn("Männerstimmen", stimme)
        self.assertIn("Eine Sperre nach", stimme)
        self.assertNotIn("«", basis + stimme + helfer)

    def test_nichtbinaer(self):
        basis, stimme, _ = self._basis("n")
        self.assertIn("2. **Weiblich oder androgyn**", basis)
        self.assertIn("eher neutral", stimme)
        self.assertIn("3. Immer **erwachsen (ab 18)**", basis)                 # die übrigen Regeln bleiben


class Stimme(unittest.TestCase):

    def test_hz_sperre_nur_weiblich(self):
        import stimme
        with mock.patch("config.load", return_value={}):
            with mock.patch.object(PS, "geschlecht", return_value="w"):
                self.assertEqual(float(stimme.MIN_HZ), stimme.min_hz())
            with mock.patch.object(PS, "geschlecht", return_value=""):
                self.assertEqual(float(stimme.MIN_HZ), stimme.min_hz())
            for g in ("m", "n"):
                with mock.patch.object(PS, "geschlecht", return_value=g):
                    self.assertEqual(0.0, stimme.min_hz())


class WebUI(unittest.TestCase):

    def test_maenner_woerter_nur_bei_maennlicher_persoenlichkeit(self):
        import sdwebui
        vorsatz = "1girl, solo, woman, masterpiece"
        with mock.patch.object(PS, "geschlecht", return_value="w"):
            text = sdwebui.merge_prompt("1boy, man, beach", vorsatz)
        self.assertNotIn("1boy", text)
        self.assertIn("1girl", text)
        with mock.patch.object(PS, "geschlecht", return_value="m"):
            text = sdwebui.merge_prompt("1boy, man, beach, group", vorsatz)
            self.assertNotIn("1girl", text)
            self.assertNotIn("group", text)                                     # Gruppen bleiben gesperrt
            self.assertIn("1boy", text)
            self.assertIn("solo", text)
            self.assertEqual(sdwebui.merge_prompt("beach", vorsatz), "1girl, solo, woman, masterpiece, beach")


if __name__ == "__main__":
    unittest.main()
