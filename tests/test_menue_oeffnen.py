"""Tests: Werkzeug menue_oeffnen – die KI öffnet ein Auswahlmenü, der Nutzer wählt selbst.

python -m unittest tests.test_menue_oeffnen
"""

import ast
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions  # noqa: E402
import commands as C  # noqa: E402
import modes  # noqa: E402

import plan  # noqa: E402


def _menue_fuer_ki(namespace):
    tree = ast.parse(main_quelle.text())
    knoten = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_menue_fuer_ki"]
    assert len(knoten) == 1
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), namespace)
    return namespace["_menue_fuer_ki"]


class Pruefen(unittest.TestCase):
    def test_nur_menue_ohne_wert(self):
        self.assertEqual(C.menue_pruefen("/theme"), ("/theme", ""))
        self.assertEqual(C.menue_pruefen("theme")[0], "/theme")
        befehl, grund = C.menue_pruefen("/theme matrix")
        self.assertIsNone(befehl)
        self.assertIn("würde direkt etwas ändern", grund)

    def test_unterpunkte_die_selbst_ein_menue_sind(self):
        self.assertEqual(C.menue_pruefen("/bild schritte")[0], "/bild schritte")
        self.assertEqual(C.menue_pruefen("/model huggingface")[0], "/model huggingface")

    def test_sicherheit_und_modus_bleiben_beim_nutzer(self):
        for b in ("/schluessel", "/limit", "/whitelist", "/sandbox", "/modus", "/auto", "/wache", "/exit", "/reset"):
            self.assertIsNone(C.menue_pruefen(b)[0], b)
        self.assertIsNone(C.menue_pruefen("")[0])


class Freigabe(unittest.TestCase):
    def test_in_jedem_modus_ohne_rueckfrage(self):
        for m in ("chat", "lesen", "normal", "auto"):
            with mock.patch.object(modes, "current", return_value=m):
                self.assertEqual(modes.decide({"tool": "menue_oeffnen", "befehl": "/theme"}, False), "run", m)

    def test_ohne_todo_liste_erlaubt(self):
        with mock.patch.object(plan, "_liste", None), mock.patch.object(plan, "_ohne_liste", plan.EINZELN):
            laufen, gesperrt = plan.zurueckweisen([{"tool": "anleitung_lesen"}, {"tool": "menue_oeffnen"},
                                                   {"tool": "befehl"}])
        self.assertEqual([a["tool"] for a in laufen], ["anleitung_lesen", "menue_oeffnen"])
        self.assertEqual([a["tool"] for a in gesperrt], ["befehl"])

    def test_ki_kennt_das_werkzeug(self):
        self.assertIn("menue_oeffnen", actions.ACTIONS)
        self.assertIn("- menue_oeffnen", (ROOT / "core" / "persona.py").read_text(encoding="utf-8"))
        self.assertIn("menue_oeffnen", C.hilfe_fuer_ki())

    def test_ausserhalb_des_terminals_nur_hinweis(self):
        res = actions._menue_oeffnen({"befehl": "/theme"})
        self.assertFalse(res.ok)
        self.assertIn("/theme", res.text)


class Ablauf(unittest.TestCase):
    def lauf(self, act, wahl, stand_nachher=None):
        aufgerufen = []
        stand = {"jetzt": [("cyan", "✓ cyan", None), ("matrix", "   matrix", None)]}

        async def untermenue(ctx, cmd):
            return wahl

        async def handle_line(ctx, text):
            aufgerufen.append(text)
            if stand_nachher:
                stand["jetzt"] = stand_nachher

        alt = C.AUSWAHL_JETZT
        C.AUSWAHL_JETZT = lambda cmd: stand["jetzt"]
        try:
            fn = _menue_fuer_ki({"C": C, "actions": actions, "ui": NS(info=lambda t: None),
                                 "AKTIVER_CTX": object(), "_untermenue": untermenue,
                                 "handle_line": handle_line})
            return asyncio.run(fn(act)), aufgerufen
        finally:
            C.AUSWAHL_JETZT = alt

    def test_wahl_wird_ausgefuehrt_und_gemeldet(self):
        res, auf = self.lauf({"befehl": "/theme"}, "matrix",
                             [("cyan", "   cyan", None), ("matrix", "✓ matrix", None)])
        self.assertEqual(auf, ["/theme matrix"])
        self.assertTrue(res.ok)
        self.assertIn("gewählt: /theme matrix", res.text)
        self.assertIn("Jetzt aktiv: matrix", res.text)

    def test_zurueck_aendert_nichts(self):
        res, auf = self.lauf({"befehl": "/theme"}, None)
        self.assertEqual(auf, [])
        self.assertIn("ohne Auswahl geschlossen", res.text)

    def test_unterpunkt_oeffnet_direkt(self):
        res, auf = self.lauf({"befehl": "/bild schritte"}, "egal")
        self.assertEqual(auf, ["/bild schritte"])

    def test_gesperrt_oeffnet_nichts(self):
        res, auf = self.lauf({"befehl": "/schluessel"}, "aus")
        self.assertEqual(auf, [])
        self.assertFalse(res.ok)


if __name__ == "__main__":
    unittest.main()
