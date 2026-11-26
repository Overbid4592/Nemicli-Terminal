"""Aktionsblöcke lesen: Feldnamen mit Umlaut, mehrere Aktionen in einem Block.

python -m unittest tests.test_aktion_lesen
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions                                         # noqa: E402

ZAUN = "```aktion\n{}\n```"


class Umlaute(unittest.TestCase):
    def test_feldnamen_ohne_umlaut(self):
        acts, _ = actions.parse_actions(ZAUN.format('{"tool": "wache_bewerten", "id": "a1", "urteil": "harmlos", '
                                                    '"begründung": "Signiert, bekannter Ort.", "Größe": 3}'))
        self.assertEqual(acts[0]["begruendung"], "Signiert, bekannter Ort.")
        self.assertEqual(acts[0]["Groesse"], 3)
        self.assertNotIn("begründung", acts[0])

    def test_vorhandenes_feld_bleibt(self):
        acts, _ = actions.parse_actions(ZAUN.format('{"tool": "x", "begruendung": "echt", "begründung": "anders"}'))
        self.assertEqual(acts[0]["begruendung"], "echt")

    def test_werte_bleiben_unveraendert(self):
        acts, _ = actions.parse_actions(ZAUN.format('{"tool": "merken", "text": "Grüße an Jürgen"}'))
        self.assertEqual(acts[0]["text"], "Grüße an Jürgen")


class MehrereJeBlock(unittest.TestCase):
    def test_objekte_hintereinander(self):
        block = ('{"tool": "wache_bewerten", "id": "a"}\n{"tool": "wache_bewerten", "id": "b"},\n'
                 '{"tool": "kugel", "stimmung": "froh"}')
        acts, rest = actions.parse_actions("Ich urteile.\n" + ZAUN.format(block))
        self.assertEqual([a["tool"] for a in acts], ["wache_bewerten", "wache_bewerten", "kugel"])
        self.assertEqual([a.get("id") for a in acts[:2]], ["a", "b"])
        self.assertEqual(rest, "Ich urteile.")

    def test_ein_objekt_ohne_tool_macht_den_block_unlesbar(self):
        text = ZAUN.format('{"tool": "a"}\n{"id": "b"}')
        acts, _ = actions.parse_actions(text)
        self.assertEqual(acts, [])
        self.assertIn("mehrere JSON-Objekte", actions.unlesbar_grund(text))

    def test_kaputtes_ende_macht_den_block_unlesbar(self):
        acts, _ = actions.parse_actions(ZAUN.format('{"tool": "a"}\n{"tool": "b", "x": '))
        self.assertEqual(acts, [])

    def test_hoechstens_acht(self):
        block = "\n".join('{"tool": "a"}' for _ in range(actions.MAX_JE_BLOCK + 1))
        self.assertEqual(actions.parse_actions(ZAUN.format(block))[0], [])

    def test_interne_felder_bleiben_gesperrt(self):
        acts, _ = actions.parse_actions(ZAUN.format('{"tool": "a", "_frei": true}\n{"tool": "b", "_x": 1}'))
        self.assertTrue(all(not any(k.startswith("_") for k in a) for a in acts))


class InvokeForm(unittest.TestCase):
    """Werkzeugaufrufe in Auszeichnungsform werden wie ```aktion-Blöcke gelesen."""

    def test_blosser_wert_geht_ins_einzige_feld(self):
        text = 'Gern.\n<ns:invoke name="bild_ansehen"> C:\\Bilder\\bild_1.png </ns:invoke>'
        acts, rest = actions.parse_actions(text)
        self.assertEqual(acts, [{"tool": "bild_ansehen", "pfad": "C:\\Bilder\\bild_1.png"}])
        self.assertEqual(rest, "Gern.")

    def test_parameter_und_huelle(self):
        text = ('Moment.\n<function_calls>\n<invoke name="web_suche">\n<parameter name="suche">wetter morgen'
                '</parameter>\n<parameter name="anzahl">3</parameter>\n</invoke>\n</function_calls>')
        acts, rest = actions.parse_actions(text)
        self.assertEqual(acts, [{"tool": "web_suche", "suche": "wetter morgen", "anzahl": 3}])
        self.assertEqual(rest, "Moment.")

    def test_marken_vorsatz(self):
        s = chr(0xFF5C)
        v = f"{s}DSML{s}"
        text = (f'<{v}function_calls>\n<{v}invoke name="bild_ansehen">\n<{v}parameter name="pfad" string="true">'
                f'123</{v}parameter>\n</{v}invoke>\n</{v}function_calls>')
        acts, rest = actions.parse_actions(text)
        self.assertEqual(acts, [{"tool": "bild_ansehen", "pfad": "123"}])
        self.assertEqual(rest, "")

    def test_ohne_inhalt_und_mit_json(self):
        acts, _ = actions.parse_actions('<invoke name="bild_malen"></invoke> '
                                        '<invoke name="datei_lesen">{"pfad": "a.txt"}</invoke>')
        self.assertEqual(acts, [{"tool": "bild_malen"}, {"tool": "datei_lesen", "pfad": "a.txt"}])

    def test_reihenfolge_mit_aktionsblock(self):
        text = '<invoke name="bild_malen"></invoke>\n' + ZAUN.format('{"tool": "bild_ansehen", "pfad": "x.png"}')
        acts, rest = actions.parse_actions(text)
        self.assertEqual([a["tool"] for a in acts], ["bild_malen", "bild_ansehen"])
        self.assertEqual(rest, "")

    def test_interne_felder_gesperrt(self):
        acts, _ = actions.parse_actions('<invoke name="a"><parameter name="_frei">true</parameter></invoke>')
        self.assertEqual(acts, [{"tool": "a"}])

    def test_unklarer_wert_wird_nicht_geraten(self):
        text = '<invoke name="bild_fragen">irgendwas</invoke>'
        acts, rest = actions.parse_actions(text)
        self.assertEqual(acts, [])
        self.assertIn("invoke", rest)
        self.assertIn("NICHTS ausgeführt", actions.unparsed_action_note(text, acts))
        self.assertIn("<invoke>", actions.unlesbar_fuer_ki(text))

    def test_offener_aufruf_meldet_sich(self):
        text = '<invoke name="bild_ansehen">C:\\x.png'
        self.assertEqual(actions.parse_actions(text)[0], [])
        self.assertIsNotNone(actions.unparsed_action_note(text, []))

    def test_normaler_text_bleibt(self):
        text = "Ich rufe dich gleich an, versprochen."
        self.assertEqual(actions.parse_actions(text), ([], text))
        self.assertIsNone(actions.unparsed_action_note(text, []))


if __name__ == "__main__":
    unittest.main()
