"""Selbstentwurf im Takt: drei Zeitfenster am Tag, je Fenster eine Runde, nur im Vordergrund mit erlaubtem
Modell, im Leerlauf unter TURN_LOCK; /reflektieren startet die Runde, Untermenü und Unterbefehle.

python -m unittest tests.test_selbstentwurf_takt
"""
import ast
import asyncio
import contextlib
import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import commands as C           # noqa: E402
import lernschleife as LS      # noqa: E402
import profilordner            # noqa: E402

NAMEN = ["_rueckblick", "_selbstentwurf_darf", "selbstentwurf_runde", "_selbstentwurf_melden", "_terminal_frei",
         "selbstentwurf_takt_lauf", "selbstentwurf_planen", "selbstentwurf_jetzt", "reflektieren_befehl"]


_CODE = {}


def aus_main(namen, namespace):
    if "code" not in _CODE:                      # einmal übersetzen: main.py ist groß
        tree = ast.parse(main_quelle.text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in namen]
        assert len(nodes) == len(namen), "Test muss die aktuellen Originalfunktionen verwenden"
        _CODE["code"] = compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec")
    exec(_CODE["code"], namespace)


class _Ort(unittest.TestCase):
    """Profile/ in einem leeren Testordner."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(profilordner, "wurzel", return_value=self.tmp)
        p.start()
        self.addCleanup(p.stop)


class Fenster(_Ort):
    def test_grenzen(self):
        tag = datetime(2026, 3, 4)
        for stunde, minute, soll in ((0, 0, "morgen"), (10, 59, "morgen"), (11, 0, "mittag"),
                                     (16, 59, "mittag"), (17, 0, "abend"), (23, 59, "abend")):
            self.assertEqual(LS.fenster(tag.replace(hour=stunde, minute=minute)), soll, (stunde, minute))
        self.assertEqual(LS.fenster_schluessel(tag.replace(hour=12)), "2026-03-04:mittag")

    def test_je_fenster_hoechstens_einmal(self):
        neun = datetime(2026, 3, 4, 9)
        self.assertTrue(LS.selbstentwurf_faellig("Test", neun))
        LS.selbstentwurf_erledigt("Test", neun)
        self.assertFalse(LS.selbstentwurf_faellig("Test", neun.replace(hour=10, minute=30)))
        self.assertTrue(LS.selbstentwurf_faellig("Test", neun.replace(hour=12)))          # Mittag
        self.assertTrue(LS.selbstentwurf_faellig("Test", datetime(2026, 3, 5, 9)))        # nächster Morgen
        self.assertTrue(LS.selbstentwurf_faellig("Andere", neun))                          # je Persönlichkeit
        self.assertTrue((self.tmp / "Test" / LS.TAKT_DATEI).is_file())

    def test_kaputte_datei_gilt_als_faellig(self):
        (self.tmp / "Test").mkdir()
        (self.tmp / "Test" / LS.TAKT_DATEI).write_text("{kaputt", encoding="utf-8")
        self.assertTrue(LS.selbstentwurf_faellig("Test"))


class _SE:
    def __init__(self):
        self.zeilen = ["Selbstentwurf von Test · Stand 2026-03-04 09:00"]
        self.zurueck_gerufen = 0
        self.verlauf_gerufen = 0
        self.zurueck_fehler = None

    def reflexions_auftrag(self, profil=None):
        return "AUFTRAG: Leitfragen ansehen"

    def stand_zeilen(self, profil=None):
        return list(self.zeilen)

    def verlauf(self, profil=None):
        self.verlauf_gerufen += 1
        return [{"nr": 1, "zeit": "2026-03-03 18:00", "grund": "älter", "felder": "wer: x"}]

    def zurueck(self, profil=None):
        self.zurueck_gerufen += 1
        if self.zurueck_fehler:
            raise ValueError(self.zurueck_fehler)
        return "↩ zurückgesetzt"


_BLOCK = re.compile(r"```aktion\s*\n(\{.*?\})\s*```", re.DOTALL)


class Takt(_Ort, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.se = _SE()
        self.darf = (True, "")
        self.vordergrund = None
        self.fragen, self.gelaufen, self.infos, self.warn, self.ausgaben = [], [], [], [], []
        self.antwort = ('Ich sehe nach.\n```aktion\n{"tool": "selbstentwurf", "felder": {"mag": "Regen"}, '
                        '"grund": "Ich mag Regen inzwischen."}\n```')

        class Backend:
            messages = [{"role": "user", "content": "Wie war dein Tag?"},
                        {"role": "assistant", "content": "Ruhig."}]

            async def ask_once(inner, prompt, system):
                self.fragen.append((prompt, system))
                return self.antwort

        async def run(act):
            self.gelaufen.append(act)
            if act.get("felder"):
                self.se.zeilen.append(f"  {act['felder']}")
            return NS(ok=True, text="✍ gespeichert")

        def parse_actions(text):
            return [json.loads(m) for m in _BLOCK.findall(text)], text

        async def do_reflect(ctx):
            self.reflektiert = True
            return []

        self.reflektiert = False
        self.protokoll = []
        self.backend = Backend()
        self.ns = {"asyncio": asyncio, "time": time, "TURN_LOCK": asyncio.Lock(), "_LERN_AUFGABEN": set(),
                   "SE_RUHE_S": 120.0, "_FREMD_MARKEN": ("EXTERNER WEB-INHALT",), "lernschleife": LS,
                   "PS": NS(active=lambda: NS(key="test", name="Test")),
                   "sicherheit": NS(vordergrund_grund=lambda: self.vordergrund),
                   "actions": NS(parse_actions=parse_actions, run=run, describe=lambda a: f"Selbstentwurf: {a}"),
                   "protokoll": NS(schreibe=lambda *a, **k: self.protokoll.append((a, k))),
                   "do_reflect": do_reflect,
                   "ui": NS(info=self.infos.append, warn=self.warn.append, success=self.infos.append,
                            error=self.warn.append, TUI=None,
                            thinking=lambda *a, **k: contextlib.nullcontext(),
                            console=NS(print=lambda *a, **k: self.ausgaben.append(" ".join(map(str, a)))))}
        aus_main(NAMEN, self.ns)
        self.ctx = NS(backend=self.backend, model="anbieter:modell", current_chat=1)
        mg = NS(darf_selbst_umschreiben=lambda ref=None: self.darf)
        p = mock.patch.dict(sys.modules, {"selbstentwurf": self.se, "modellgroesse": mg})
        p.start()
        self.addCleanup(p.stop)
        for name, wert in (("selbstentwurf_an", True), ("an", True)):
            q = mock.patch.object(LS, name, return_value=wert)
            q.start()
            self.addCleanup(q.stop)
        LS.hinweise_abholen("test")

    async def test_runde_passt_an_und_meldet_kurz(self):
        self.assertTrue(await self.ns["selbstentwurf_takt_lauf"](self.ctx))
        self.assertEqual(len(self.fragen), 1)
        prompt, system = self.fragen[0]
        self.assertIn("AUFTRAG", prompt)
        self.assertIn("Wie war dein Tag?", prompt)                      # Gespräch als Anlass
        self.assertIn("selbstentwurf", system)
        self.assertEqual(self.gelaufen[0]["tool"], "selbstentwurf")
        self.assertEqual(self.infos, ["🪞 Test hat den Selbstentwurf angepasst: Ich mag Regen inzwischen."])
        self.assertIn("Selbstentwurf angepasst", LS.hinweise_abholen("test"))
        self.assertEqual(self.backend.messages[-1]["content"], "Ruhig.")   # nichts ins Gespräch
        (tool, _beschreibung, status), art = self.protokoll[0]             # steht im Aktions-Protokoll
        self.assertEqual((tool, status, art["veraendernd"], art["wer"]), ("selbstentwurf", "success", True, "Test"))

    async def test_hoechstens_eine_runde_je_fenster_und_nachholen_nur_einmal(self):
        (self.tmp / "Test").mkdir()
        (self.tmp / "Test" / LS.TAKT_DATEI).write_text(json.dumps({"zuletzt": "2000-01-01:morgen"}),
                                                       encoding="utf-8")
        self.assertTrue(await self.ns["selbstentwurf_takt_lauf"](self.ctx))      # verpasst: einmal nachholen
        self.assertFalse(await self.ns["selbstentwurf_takt_lauf"](self.ctx))
        self.assertFalse(await self.ns["selbstentwurf_takt_lauf"](self.ctx))
        self.assertEqual(len(self.fragen), 1)

    async def test_nicht_im_hintergrund(self):
        self.vordergrund = "Hintergrund-Prozess (--auftrag)"
        self.assertFalse(await self.ns["selbstentwurf_takt_lauf"](self.ctx))
        self.assertEqual(self.fragen, [])
        self.assertTrue(LS.selbstentwurf_faellig("Test"))                  # bleibt fürs Gespräch fällig

    async def test_nicht_ohne_erlaubtes_modell_oder_wenn_aus(self):
        self.darf = (False, "lokal erst ab 6B")
        self.assertFalse(await self.ns["selbstentwurf_takt_lauf"](self.ctx))
        self.darf = (True, "")
        with mock.patch.object(LS, "selbstentwurf_an", return_value=False):
            self.assertFalse(await self.ns["selbstentwurf_takt_lauf"](self.ctx))
        self.assertEqual(self.fragen, [])
        self.assertEqual(self.infos, [])

    async def test_wartet_auf_laufende_runde(self):
        async with self.ns["TURN_LOCK"]:
            self.assertFalse(self.ns["_terminal_frei"](None))
            lauf = asyncio.create_task(self.ns["selbstentwurf_takt_lauf"](self.ctx))
            await asyncio.sleep(0.05)
            self.assertEqual(self.fragen, [])
        self.assertTrue(await lauf)
        self.assertEqual(len(self.fragen), 1)

    async def test_nur_das_werkzeug_selbstentwurf(self):
        self.antwort = '```aktion\n{"tool": "befehl", "befehl": "dir"}\n```'
        await self.ns["selbstentwurf_takt_lauf"](self.ctx)
        self.assertEqual(self.gelaufen, [])
        self.assertEqual(self.infos, [])

    async def test_terminal_frei_nur_im_leerlauf(self):
        frei = self.ns["_terminal_frei"]
        alt = time.monotonic() - 10_000
        self.assertTrue(frei(NS(busy=False, _modal=None, _input=NS(text=""), _last_input=alt)))
        self.assertFalse(frei(NS(busy=True, _modal=None, _input=NS(text=""), _last_input=alt)))
        self.assertFalse(frei(NS(busy=False, _modal=object(), _input=NS(text=""), _last_input=alt)))
        self.assertFalse(frei(NS(busy=False, _modal=None, _input=NS(text="Hal"), _last_input=alt)))
        self.assertFalse(frei(NS(busy=False, _modal=None, _input=NS(text=""), _last_input=time.monotonic())))

    async def test_reflektieren_startet_die_runde(self):
        await self.ns["reflektieren_befehl"](self.ctx, "")
        self.assertTrue(self.reflektiert)
        self.assertEqual(len(self.fragen), 1)
        self.assertTrue(LS.selbstentwurf_faellig("Test"))                  # zusätzlich: die Runde im Fenster bleibt

    async def test_reflektieren_ohne_erlaubtes_modell_nennt_den_grund(self):
        self.darf = (False, "lokal erst ab 6B")
        await self.ns["reflektieren_befehl"](self.ctx, "")
        self.assertTrue(self.reflektiert)
        self.assertEqual(self.fragen, [])
        self.assertTrue(any("lokal erst ab 6B" in i for i in self.infos))

    async def test_zurueck_und_verlauf_rufen_den_selbstentwurf(self):
        await self.ns["reflektieren_befehl"](self.ctx, "selbst zurueck")
        self.assertEqual(self.se.zurueck_gerufen, 1)
        self.assertIn("↩ zurückgesetzt", self.infos)
        self.se.zurueck_fehler = "kein früherer Stand im Verlauf"
        await self.ns["reflektieren_befehl"](self.ctx, "selbst zurück")
        self.assertTrue(self.warn and "kein früherer Stand" in self.warn[-1])
        await self.ns["reflektieren_befehl"](self.ctx, "selbst verlauf")
        self.assertEqual(self.se.verlauf_gerufen, 1)
        self.assertTrue(any("älter" in a for a in self.ausgaben))
        await self.ns["reflektieren_befehl"](self.ctx, "selbst")
        self.assertTrue(any("Selbstentwurf von Test" in a for a in self.ausgaben))
        self.assertEqual(self.fragen, [])

    async def test_schalter(self):
        with mock.patch.object(LS, "selbstentwurf_setzen") as setzen:
            await self.ns["reflektieren_befehl"](self.ctx, "selbst aus")
        setzen.assert_called_once_with(False)

    async def test_klassik_plant_nach_der_runde(self):
        self.ns["selbstentwurf_planen"](self.ctx)
        for _ in range(200):
            if not self.ns["_LERN_AUFGABEN"]:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.fragen), 1)
        self.ns["ui"].TUI = object()                                        # Vollbild: der Leerlauf-Takt
        (self.tmp / "Test" / LS.TAKT_DATEI).unlink()
        self.ns["selbstentwurf_planen"](self.ctx)
        self.assertEqual(self.ns["_LERN_AUFGABEN"], set())


class Menue(unittest.TestCase):
    def test_untermenue_enthaelt_die_neuen_eintraege(self):
        eintraege = {z: t for z, t, _ in C.UNTERMENUES["/reflektieren"]}
        self.assertIn("Selbstentwurf ansehen", eintraege["selbst"])
        self.assertIn("alten Stand zurück", eintraege["selbst zurueck"])
        self.assertIn("selbst verlauf", eintraege)
        self.assertIn("selbst an", eintraege)
        self.assertIn("selbst aus", eintraege)

    def test_schalter_wird_ein_eintrag(self):
        code = main_quelle.text()
        teil = code.split("def _schalter_an")[1].split("_SCHALTER_NAME")[0]
        self.assertIn('("/reflektieren", "selbst")', teil)
        self.assertIn('("/reflektieren", "selbst"): ', code.split("_SCHALTER_NAME = ")[1].split("_SCHALTER_ZEICHEN")[0])
        self.assertIn("selbstentwurf_takt(ctx)", code)                     # Takt startet mit der Sitzung


if __name__ == "__main__":
    unittest.main()
