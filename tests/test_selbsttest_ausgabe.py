"""selftest(out=…) bekommt für /selbsttest eine list.append: jede Ausgabe darf nur ein Argument haben."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SelbsttestAusgabe(unittest.TestCase):
    def test_print_nur_mit_einem_argument(self):
        src = (ROOT / "main_start.py").read_text(encoding="utf-8")
        funk = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "selftest")
        falsch = [n.lineno for n in ast.walk(funk)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
                  and (len(n.args) != 1 or n.keywords)]
        self.assertEqual(falsch, [], f"print mit mehreren Argumenten in Zeile {falsch}")


if __name__ == "__main__":
    unittest.main()
