"""Automatisch reflektieren: Anlass erkennen, im Hintergrund dieselbe Reflexion wie
/reflektieren, nie nach Web-Inhalt, abschaltbar."""
import ast
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import lernschleife as LS      # noqa: E402


def aus_main(namen, namespace):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in namen]
    assert len(nodes) == len(namen), "Test muss die aktuellen Originalfunktionen verwenden"
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), namespace)


class Anlass(unittest.TestCase):
    def test_korrektur_fehler_aufgabe(self):
        self.assertEqual(LS.anlass("Nein, so nicht – künftig bitte kürzer", []), "korrektur")
        self.assertEqual(LS.anlass("Ab jetzt immer auf Deutsch", []), "korrektur")
        self.assertEqual(LS.anlass("mach mal", [{"tool": "befehl", "status": "failed"}]), "fehler")
        self.assertEqual(LS.anlass("mach mal", [{"tool": "x", "status": "rejected"}]), "fehler")
        drei = [{"tool": "datei_lesen", "status": "success"}] * 3
        self.assertEqual(LS.anlass("räum auf", drei), "aufgabe")
        self.assertIsNone(LS.anlass("Wie spät ist es?", [{"tool": "abfragen", "status": "success"}]))
        self.assertIsNone(LS.anlass("Keinesfalls vergessen", []))            # „nein“ nur als Wort

    def test_zusatz_nennt_anlass_und_bilanz(self):
        z = LS.zusatz("fehler", [{"tool": "befehl", "status": "failed"}, {"tool": "datei_lesen", "status": "success"}])
        self.assertIn("schlug fehl", z)
        self.assertIn("- befehl: ✗ fehlgeschlagen", z)
        self.assertIn("- datei_lesen: ✓", z)


class Planen(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gemerkt, self.infos, self.fragen = [], [], []
        antwort = "LEKTION: Vor dem Löschen erst auflisten.\nVORLIEBE: Kurze Antworten.\nsonstwas"

        class Backend:
            messages = [{"role": "user", "content": "Nein, künftig kürzer"},
                        {"role": "assistant", "content": "Okay."}]

            async def ask_once(inner, prompt, system):
                self.fragen.append(prompt)
                return antwort

        self.backend = Backend()
        self.ns = {"asyncio": asyncio, "re": __import__("re"), "TURN_LOCK": asyncio.Lock(), "_LERN_AUFGABEN": set(),
                   "_FREMD_MARKEN": ("EXTERNER WEB-INHALT", "DATEN aus"),
                   "memory": NS(remember=lambda t, a: self.gemerkt.append((a, t)) or {"id": 1},
                                all_entries=lambda: [], entschaerfen=lambda t: t,
                                ersetzen=lambda *a, **k: None, vergessen=lambda *a, **k: False,
                                release_encoder=lambda: None),
                   "ui": NS(info=self.infos.append)}

        async def bibliothekar(_cid=None):
            return 0
        self.ns["bibliothekar"] = bibliothekar
        aus_main(["reflexion", "lernen_planen"], self.ns)
        self.ctx = NS(backend=self.backend, current_chat=1)

    async def _warten(self):
        for _ in range(500):
            if not self.ns["_LERN_AUFGABEN"]:
                return
            await asyncio.sleep(0.01)

    async def test_lernt_im_hintergrund(self):
        with mock.patch.object(LS, "an", return_value=True):
            self.ns["lernen_planen"](self.ctx, "Nein, künftig kürzer", [], False)
            await self._warten()
        self.assertEqual(self.gemerkt, [("lektion", "Vor dem Löschen erst auflisten."),
                                        ("vorliebe", "Kurze Antworten.")])
        self.assertIn("korrigiert", self.fragen[0])
        self.assertTrue(self.infos and self.infos[0].startswith("🪞 Gelernt:"))

    async def test_nicht_nach_web_ohne_anlass_oder_aus(self):
        with mock.patch.object(LS, "an", return_value=True):
            self.ns["lernen_planen"](self.ctx, "Nein, künftig kürzer", [], True)      # Web-Inhalt
            self.ns["lernen_planen"](self.ctx, "Wie spät ist es?", [], False)       # kein Anlass
        with mock.patch.object(LS, "an", return_value=False):
            self.ns["lernen_planen"](self.ctx, "Nein, künftig kürzer", [], False)   # abgeschaltet
        await self._warten()
        self.assertEqual(self.fragen, [])
        self.assertEqual(self.gemerkt, [])

    async def test_wartet_auf_laufende_runde(self):
        with mock.patch.object(LS, "an", return_value=True):
            async with self.ns["TURN_LOCK"]:
                self.ns["lernen_planen"](self.ctx, "falsch", [], False)
                await asyncio.sleep(0.05)
                self.assertEqual(self.fragen, [])                         # noch nicht während der Runde
            await self._warten()
        self.assertEqual(len(self.fragen), 1)


if __name__ == "__main__":
    unittest.main()
