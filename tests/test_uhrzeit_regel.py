"""Die Uhrzeit sagt nichts über den Tagesablauf: Die Regel steht im Prompt jeder Persönlichkeit, auch in der
kompakten Fassung für lokale Modelle, und in den Kugel-Nachrichten mit Tageszeit."""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import persona       # noqa: E402

REGEL = "Die Uhrzeit sagt nichts über den Tagesablauf"


def _kugel_text(name: str) -> str:
    baum = ast.parse((ROOT / "tools" / "wache" / "kugel.py").read_text(encoding="utf-8"))
    knoten = next(n for n in baum.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name)
    return ast.literal_eval(knoten.value)


class UhrzeitRegel(unittest.TestCase):
    def test_steht_in_den_grundregeln(self):
        self.assertIn(REGEL, persona._GRUNDREGELN)
        self.assertIn("keine gute Nacht", persona._GRUNDREGELN)

    def test_bleibt_in_der_kompakten_fassung(self):
        self.assertIn(REGEL, persona.kompakt(persona._GRUNDREGELN + persona._REGELN))

    def test_kugel_nachrichten_mit_tageszeit(self):
        for name in ("ANKOMMEN_TEXT", "IMPULS_TEXT"):
            text = _kugel_text(name)
            self.assertIn("{tageszeit}", text)
            self.assertIn("keine gute Nacht", text)


if __name__ == "__main__":
    unittest.main()
