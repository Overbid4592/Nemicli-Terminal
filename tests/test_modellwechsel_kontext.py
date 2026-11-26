"""Modellwechsel: die Kontext-Leiste zählt für das neue Modell neu und nimmt dafür nie den
Tokenizer eines noch geladenen anderen Modells."""
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402


def _funktionen(namen, namespace):
    tree = ast.parse(main_quelle.text())
    knoten = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in namen]
    if len(knoten) != len(namen):
        raise AssertionError("Test muss die aktuellen Originalfunktionen verwenden")
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), namespace)


class Modellwechsel(unittest.TestCase):
    def setUp(self):
        self.ns = {
            "M": NS(split_ref=lambda ref: tuple(ref.split(":", 1)) if ":" in ref else ("", ref)),
            "pricing": NS(context_window=lambda ref: {"gguf:Neu": 65536, "gguf:Alt": 8192}.get(ref, 1000),
                          est_tokens=lambda t: len(str(t or "")) // 4),
            "SESSION": {"ctx": 22300, "ctx_max": 8192},
        }
        _funktionen({"_kontext_teile", "_kontext_neu_zaehlen"}, self.ns)
        self.persona = NS(prompt_bilanz=lambda lokal: [("Anleitung", "eins zwei drei vier")])
        self.ctx = NS(model="gguf:Neu", backend=NS(messages=[{"role": "user", "content": "x" * 400}], stand=""))

    def _zaehlen(self, motor):
        gguflokal = NS(antwort_reserve=lambda n: 2048, _motor=motor,
                       modelle=lambda: {"Alt": "alt.gguf", "Neu": "neu.gguf"})
        with mock.patch.dict(sys.modules, {"persona": self.persona, "gguflokal": gguflokal}):
            self.ns["_kontext_neu_zaehlen"](self.ctx)
            return self.ns["_kontext_teile"](self.ctx)

    def test_nach_dem_wechsel_neu_gezaehlt(self):
        alter_motor = ("alt.gguf", "k", NS(tokenizer=NS(encode=lambda t: [0] * 999)))
        teile, fenster, reserve, genau = self._zaehlen(alter_motor)
        self.assertFalse(genau)                                     # nicht mit dem Tokenizer des alten Modells
        self.assertEqual(self.ns["SESSION"]["ctx"], len("eins zwei drei vier") // 4 + 100)
        self.assertEqual(self.ns["SESSION"]["ctx_max"], 65536)

    def test_genau_mit_dem_eigenen_motor(self):
        eigener = ("neu.gguf", "k", NS(tokenizer=NS(encode=lambda t: t.split())))
        teile, _, _, genau = self._zaehlen(eigener)
        self.assertTrue(genau)
        self.assertEqual(dict(teile)["Anleitung"], 4)
        self.assertEqual(self.ns["SESSION"]["ctx"], 4 + 100)


if __name__ == "__main__":
    unittest.main()
