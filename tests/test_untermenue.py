"""Tests: Auswahl, wenn ein Slash-Befehl ohne Zusatz getippt wird.

python -m unittest tests.test_untermenue
"""

import ast
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import commands as C                                   # noqa: E402


def _funktionen(namespace):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
    namen = {"_untermenue", "_untermenue_eintraege"}
    knoten = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name in namen]
    assert len(knoten) == len(namen)
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), namespace)
    return namespace


class UntermenueTests(unittest.TestCase):

    def lauf(self, cmd, wahl, antwort="", ctx=None):
        gefragt = {}

        async def ask_confirm(frage, optionen):
            gefragt["optionen"] = optionen
            return wahl(optionen) if callable(wahl) else wahl

        async def ask_text(frage):
            gefragt["frage"] = frage
            return antwort

        ns = _funktionen({"Ctx": object, "ask_confirm": ask_confirm, "ask_text": ask_text,
                          "modes": NS(current=lambda: "normal", MODES=[("chat", "💬", "Chatten"),
                                                                         ("normal", "⚙", "Normal")]),
                          "ui": NS(current_theme=lambda: "cyan", THEMES={"cyan": {}, "amber": {}}),
                          "M": NS(strength_menu=lambda ref: {"schnell": "flott", "stark": "gründlich"})})
        ctx = ctx or NS(strength_active=lambda: True, strength="stark", model="x")
        return asyncio.run(ns["_untermenue"](ctx, cmd)), gefragt

    def test_feste_auswahl_mit_zurueck(self):
        zusatz, g = self.lauf("/uebung", "1")
        self.assertEqual(zusatz, "jetzt")
        self.assertEqual(g["optionen"][-1][0], C.ZURUECK)
        self.assertEqual(self.lauf("/uebung", C.ZURUECK)[0], None)

    def test_status_eintrag_fuehrt_ohne_zusatz_aus(self):
        self.assertEqual(self.lauf("/sandbox", "0")[0], "")

    def test_nachfrage_setzt_angabe_ein(self):
        zusatz, g = self.lauf("/sandbox", "2", r"F:\Projekt X")
        self.assertEqual(zusatz, r"freigeben F:\Projekt X schreiben")
        self.assertEqual(g["frage"], "Welcher Ordner?")
        self.assertIsNone(self.lauf("/sandbox", "1", "")[0])          # leere Angabe = abbrechen
        self.assertEqual(self.lauf("/kugel", "1", "")[0], "malen")     # malen geht auch ohne

    def test_auswahl_nach_aktuellem_stand(self):
        zusatz, g = self.lauf("/modus", "0")
        self.assertEqual(zusatz, "chat")
        self.assertTrue(g["optionen"][1][1].startswith("✓"))
        self.assertEqual(self.lauf("/theme", "1")[0], "amber")
        zusatz, g = self.lauf("/staerke", "1")
        self.assertEqual(zusatz, "stark")
        self.assertIn("✓", g["optionen"][1][1])

    def test_befehl_ohne_menue_bleibt_unveraendert(self):
        self.assertEqual(self.lauf("/help", "0")[0], "")

    def test_alle_menues_gueltig(self):
        for cmd, eintraege in C.UNTERMENUES.items():
            self.assertTrue(eintraege, cmd)
            for zusatz, anzeige, frage in eintraege:
                self.assertTrue(anzeige, cmd)
                self.assertEqual("{}" in zusatz, frage is not None, (cmd, zusatz))
            self.assertIn(cmd, C.COMMANDS if cmd != "/schlüssel" else {"/schlüssel": 1}, cmd)


if __name__ == "__main__":
    unittest.main()
