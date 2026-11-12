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


if __name__ == "__main__":
    unittest.main()
