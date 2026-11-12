"""Wie ein Messenger: Das letzte Gespräch der Persönlichkeit geht beim Start weiter (nur sein Ende im Kontext),
/resume gibt es nicht mehr."""

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui", "tests"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import chatstore     # noqa: E402
import commands      # noqa: E402
import main_quelle   # noqa: E402
import pricing       # noqa: E402


def _runde(i: int, laenge: int = 40) -> list[dict]:
    return [{"role": "user", "content": f"Frage {i} " + "x" * laenge},
            {"role": "assistant", "content": f"Antwort {i} " + "y" * laenge}]


class Gespraechsende(unittest.TestCase):
    def test_nur_das_ende_ab_einer_frage(self):
        verlauf = [m for i in range(50) for m in _runde(i)]
        ende = pricing.gespraechsende(verlauf, budget=200)
        self.assertLess(len(ende), len(verlauf))
        self.assertEqual(ende[0]["role"], "user")
        self.assertEqual(ende[-1], verlauf[-1])
        self.assertLessEqual(sum(pricing.est_tokens(m["content"]) for m in ende), 200)

    def test_kurzes_gespraech_ganz(self):
        verlauf = _runde(1)
        self.assertEqual(pricing.gespraechsende(verlauf), verlauf)

    def test_nie_mit_werkzeug_ergebnis_beginnen(self):
        verlauf = (_runde(1) + [{"role": "assistant", "content": [{"type": "tool_use", "id": "a"}]},
                                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a",
                                                              "content": "ok"}]}] + _runde(2))
        ende = pricing.gespraechsende(verlauf, budget=40)
        self.assertEqual(ende[0]["content"].split()[0:2], ["Frage", "2"])

    def test_riesige_letzte_runde_ab_ihrer_frage(self):
        verlauf = _runde(1) + _runde(2, laenge=100_000)
        self.assertEqual(pricing.gespraechsende(verlauf, budget=100), verlauf[2:])


def _funktion(name: str, namensraum: dict):
    """Eine Funktion aus main*.py für sich ausführen (die Teile laufen sonst im Namensraum von main)."""
    quelle = main_quelle.text()
    knoten = next(k for k in ast.walk(ast.parse(quelle))
                  if isinstance(k, ast.FunctionDef) and k.name == name)
    exec(compile(ast.Module([knoten], []), name, "exec"), namensraum)
    return namensraum[name]


class Fortsetzen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        p = mock.patch.object(chatstore, "CHATS_DIR", self.tmp / "chats")    # nur dieser Ordner, kein Profil
        p.start()
        self.addCleanup(p.stop)
        self.notizen = []
        self.ns = {"chatstore": chatstore, "pricing": pricing,
                   "plan": NS(verwerfen=lambda: None),
                   "mitschrift": NS(leeren=lambda: None, notiz=self.notizen.append)}
        self.fortsetzen = _funktion("_gespraech_fortsetzen", self.ns)

    def _ctx(self, backend=True):
        return NS(backend=NS(messages=[]) if backend else None, current_prompts=[], current_chat=99,
                  sync_session=lambda: None)

    def test_letztes_gespraech_geht_weiter(self):
        verlauf = [m for i in range(30) for m in _runde(i)]
        chatstore.save(7, verlauf, ["Urlaub planen"], "m")
        ctx = self._ctx()
        with mock.patch.object(pricing, "START_TOKENS", 300), \
                mock.patch.object(pricing.gespraechsende, "__defaults__", (300,)):
            weiter = self.fortsetzen(ctx)
        self.assertEqual(weiter["id"], 7)
        self.assertEqual(ctx.current_chat, 7)
        self.assertEqual(ctx.current_prompts, ["Urlaub planen"])
        self.assertLess(len(ctx.backend.messages), len(verlauf))                # nur das Ende
        self.assertEqual(ctx.backend.messages[-1], verlauf[-1])
        self.assertTrue(self.notizen)

    def test_ohne_modell_oder_ohne_gespraech_nichts(self):
        self.assertIsNone(self.fortsetzen(self._ctx()))                          # noch kein Gespräch
        chatstore.save(7, _runde(1), ["a"], "m")
        ctx = self._ctx(backend=False)
        self.assertIsNone(self.fortsetzen(ctx))
        self.assertEqual(ctx.current_chat, 99)


class OhneResume(unittest.TestCase):
    def test_befehl_ist_weg(self):
        self.assertNotIn("/resume", commands.COMMANDS)
        src = main_quelle.text()
        self.assertNotIn('cmd == "/resume"', src)
        self.assertFalse(hasattr(chatstore, "chat_args"))

    def test_start_wechsel_und_spaetes_modell_machen_weiter(self):
        src = main_quelle.text()
        self.assertIn("_weiter = _gespraech_fortsetzen(ctx)", src)                 # Start
        self.assertIn("weiter = _gespraech_fortsetzen(ctx)", src.split("def _neuer_chat_nach_wechsel")[1][:1200])
        self.assertGreaterEqual(src.count("_FORTSETZEN_NACH_MODELL[0] and ctx.backend is not None"), 2)


if __name__ == "__main__":
    unittest.main()
