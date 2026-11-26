"""Tests: Terminal-Menüs zeigen An/Aus-Schalter als einen Eintrag (✔️ an, 🛑 aus) und den Stand der Kugel-Motive
(main_befehle._menue_aufbereiten).

python -m unittest tests.test_menue_schalter
"""

import ast
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import commands  # noqa: E402


def laden(**globale) -> dict:
    """_menue_aufbereiten samt Helfern aus main_befehle.py – mit vorgegebenen Namen statt main.py."""
    baum = ast.parse((ROOT / "main_befehle.py").read_text(encoding="utf-8-sig"))
    teile = [k for k in baum.body
             if (isinstance(k, ast.FunctionDef) and k.name in ("_menue_aufbereiten", "_motive_stand", "_wahl_stand"))
             or (isinstance(k, ast.Assign)
                 and any(getattr(z, "id", "") in ("_SCHALTER_NAME", "_SCHALTER_ZEICHEN") for z in k.targets))]
    ns = {"re": re}
    exec(compile(ast.Module(teile, []), "main_befehle.py", "exec"), ns)
    ns.update(globale)                  # vorgegebene Namen gehen vor den echten Helfern
    return ns


class Schalter(unittest.TestCase):
    def test_paar_wird_ein_schalter_der_umschaltet(self):
        stand = {"auto": True, "vorne": False}
        ns = laden(_schalter_an=lambda cmd, basis: stand.get(basis) if cmd == "/kugel" else None,
                   PS=types.SimpleNamespace(active=lambda: types.SimpleNamespace(name="Test")))
        ns["_motive_stand"] = lambda: "  ·  91 von 400 fertig"
        raus = ns["_menue_aufbereiten"]("/kugel", commands.UNTERMENUES["/kugel"])
        werte = [z for z, _, _ in raus]
        texte = dict((z, t) for z, t, _ in raus)
        self.assertNotIn("auto an", werte)                         # an ist an: die Wahl schaltet aus
        self.assertEqual(texte["auto aus"], "✨ AUTO  ✔️")
        self.assertEqual(texte["vorne an"], "📌 Immer vorne  🛑")
        self.assertEqual(werte.count("vorne an") + werte.count("vorne aus"), 1)
        self.assertTrue(texte["motive {}"].endswith("91 von 400 fertig"))
        self.assertIn("", werte)                                    # der Rest bleibt
        self.assertIn("ordner", werte)

    def test_ohne_bekannten_stand_bleiben_beide(self):
        ns = laden(_schalter_an=lambda cmd, basis: None)
        roh = commands.UNTERMENUES["/uebung"]
        self.assertEqual(ns["_menue_aufbereiten"]("/uebung", roh), roh)

    def test_stufen_menue_zeigt_die_aktive_stufe(self):
        ns = laden(_schalter_an=lambda cmd, basis: True, _wahl_stand=lambda cmd: "knopf")
        roh = commands.UNTERMENUES["/voice"]                         # an · knopf · aus
        raus = ns["_menue_aufbereiten"]("/voice", roh)
        self.assertEqual([z for z, _, _ in raus], [z for z, _, _ in roh])   # keine Stufe wird zum Schalter
        texte = dict((z, t) for z, t, _ in raus)
        self.assertTrue(texte["knopf"].endswith("✔️"))
        self.assertFalse(texte["an"].endswith("✔️"))

    def test_auswahl_haken_wandert_nach_hinten(self):
        ns = laden(_schalter_an=lambda cmd, basis: None, _wahl_stand=lambda cmd: None)
        raus = ns["_menue_aufbereiten"]("/theme", [("cyan", "✓ cyan  · Cyan", None), ("mint", "   mint  · Mint", None)])
        self.assertEqual([t for _, t, _ in raus], ["cyan  · Cyan  ✔️", "mint  · Mint"])

    def test_motive_zaehlen(self):
        import kugelmotive
        from unittest import mock
        from wache import steuerung
        ns = laden(PS=types.SimpleNamespace(active=lambda: types.SimpleNamespace(name="Test")))
        k1, k2 = list(kugelmotive.MOTIVE)[:2]
        with mock.patch.object(steuerung, "bilder", return_value={"": Path("a"), k1: Path("b"), k2: Path("c")}):
            self.assertEqual(ns["_motive_stand"](), f"  ·  2 von {len(kugelmotive.MOTIVE)} fertig")

    def test_alle_schalter_haben_einen_stand(self):
        code = (ROOT / "main_befehle.py").read_text(encoding="utf-8")
        self.assertIn("_menue_aufbereiten(cmd, _untermenue_eintraege(ctx, cmd))", code)
        for cmd in ("/kugel", "/debug", "/reflektieren", "/skills", "/kontextmenue", "/uebung", "/auto"):
            self.assertIn(f'"{cmd}"', code.split("def _schalter_an")[1].split("_SCHALTER_NAME")[0])


if __name__ == "__main__":
    unittest.main()
