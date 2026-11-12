"""main.py ist auf vier Dateien verteilt, die im selben Namensraum laufen (main._teil_laden). Ein Name, den
zwei Teile definieren, überschreibt den anderen still – so ersetzte einmal ein neues `_teil_laden` die
Ladefunktion von main.py, und die letzten Teile wurden nicht mehr geladen."""
import ast
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import main_quelle                                     # noqa: E402


class MainTeile(unittest.TestCase):
    def test_kein_name_doppelt(self):
        wo = defaultdict(list)
        for teil in main_quelle.TEILE:
            for knoten in ast.parse((ROOT / teil).read_text(encoding="utf-8-sig")).body:
                if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    wo[knoten.name].append(teil)
        doppelt = {name: teile for name, teile in wo.items() if len(teile) > 1}
        self.assertEqual({}, doppelt)

    def test_ladefunktion_bleibt(self):
        for teil in main_quelle.TEILE[1:]:
            namen = {k.name for k in ast.parse((ROOT / teil).read_text(encoding="utf-8-sig")).body
                     if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            self.assertNotIn("_teil_laden", namen, teil)


if __name__ == "__main__":
    unittest.main()
