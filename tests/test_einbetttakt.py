"""Takt des Sammelns und VRAM-Budget im Bibliothekar-Prozess."""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import config        # noqa: E402
import einbetttakt   # noqa: E402
import indexdb       # noqa: E402
import memory        # noqa: E402

GB = 2 ** 30


class Einstellung(unittest.TestCase):
    def setUp(self):
        self.cfg = {}
        for name, wert in (("load", lambda: self.cfg), ("update", lambda **kw: self.cfg.update(kw) or True)):
            p = patch.object(config, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_vorgaben(self):
        self.assertEqual(einbetttakt.vram_anteil(), 25)

    def test_vram_anteil_begrenzt(self):
        self.cfg["embedding_vram_anteil"] = 300
        self.assertEqual(einbetttakt.vram_anteil(), 90)
        self.cfg["embedding_vram_anteil"] = "x"
        self.assertEqual(einbetttakt.vram_anteil(), 25)


class VramBudget(unittest.TestCase):
    def setUp(self):
        self.gpu = {"gesamt": 16 * GB, "belegt": 2 * GB}
        self.cfg = {}
        for ziel, name, wert in ((config, "load", lambda: self.cfg),):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(sys.modules, {"cockpit": NS(gpu=lambda _t: self.gpu)})
        p.start()
        self.addCleanup(p.stop)

    def test_ein_viertel_vom_gesamten_vram(self):
        self.assertEqual(memory.gpu_budget_mb(), 4096)

    def test_hoechstens_der_freie_minus_reserve(self):
        self.gpu["belegt"] = 14 * GB
        self.assertEqual(memory.gpu_budget_mb(), 2048 - memory.GPU_RESERVE_MB)

    def test_im_bibliothekar_zaehlt_das_budget(self):
        with patch.dict(os.environ, {"NEMICLI_BIBKIND": "1"}):
            self.assertTrue(memory._gpu_frei_genug())
            self.gpu["belegt"] = 15 * GB                        # nur noch ~1 GB frei
            self.assertFalse(memory._gpu_frei_genug())

    def test_kleine_karte(self):
        self.gpu.update(gesamt=4 * GB, belegt=0)               # 25 % = 1 GB: lohnt nicht
        with patch.dict(os.environ, {"NEMICLI_BIBKIND": "1"}):
            self.assertFalse(memory._gpu_frei_genug())

    def test_im_bibliothekar_wechselt_der_auftrag_nicht(self):
        class Enc:
            device = "cpu"
            _lock = __import__("threading").RLock()

            def to(s, geraet):
                raise AssertionError("das Modell liegt schon auf der GPU")
        with patch.object(memory, "NUR_GPU", True), patch.object(memory, "_get_encoder", lambda: Enc()), \
                patch.object(memory, "_gpu_frei_genug", lambda: True):
            with memory.auftrag(500) as auf_gpu:
                self.assertFalse(auf_gpu)


def _takt_funktionen(tui=None):
    """Takt-Teil aus main_dienste.py mit eigener Uhr und Attrappen für den Lauf."""
    baum = ast.parse((ROOT / "main_dienste.py").read_text(encoding="utf-8-sig"))
    namen = ("_takt_rest_s", "_vormerken", "_takt_schleife", "bibliothekar")
    knoten = [n for n in baum.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in namen]
    uhr = {"t": 1000.0}
    laeufe = []

    async def hintergrund(chat_id, alles, erzwingen):
        laeufe.append((chat_id, alles, erzwingen))
        uhr["letzter_lauf"] = uhr["t"]

    async def sammeln(chat_id):
        await hintergrund(chat_id, False, False)

    async def schlafen(s):
        uhr["t"] += s
        await asyncio.sleep(0)

    vorgemerkt = []
    ns = {"_sammeln_hintergrund": sammeln, "memory": NS(gpu_platz=lambda: (True, "")),
          "einlesen_vormerken": lambda c=None, gewuenscht=False: vorgemerkt.append((c, gewuenscht)),
          "_vorgemerkt": vorgemerkt,
          "asyncio": NS(sleep=schlafen, create_task=asyncio.ensure_future), "time": NS(monotonic=lambda: uhr["t"]),
          "ui": NS(TUI=tui), "TAKT_PRUEF_S": 30.0, "SAMMELN_ALLE_S": 120.0, "_bibliothekar_hintergrund": hintergrund,
          "_BIB": {"task": None, "nochmal": None, "wartet": False, "wartet_chat": None, "takt": None,
                   "letzter": 1000.0, "sammler": None, "chat": None}}
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main_dienste.py", "exec"), ns)
    ns["_uhr"], ns["_laeufe"] = uhr, laeufe
    return ns


class Takt(unittest.TestCase):
    def setUp(self):
        import einlesen
        self.einlesen = einlesen
        einlesen.abholen()
        self.addCleanup(einlesen.abholen)

    def test_runde_wird_vorgemerkt_und_gesammelt(self):
        ns = _takt_funktionen(tui=NS())

        async def ablauf():
            self.assertEqual(await ns["bibliothekar"](7), 0)
            self.assertEqual(ns["_laeufe"], [])
            self.assertTrue(ns["_BIB"]["wartet"])
            await ns["_BIB"]["takt"]
            await asyncio.sleep(0)
        asyncio.run(ablauf())
        self.assertEqual(ns["_laeufe"], [(7, False, False)])
        self.assertGreaterEqual(ns["_uhr"]["letzter_lauf"] - 1000.0, 120)
        self.assertFalse(ns["_BIB"]["wartet"])

    def test_aufgeraeumt_einlesen_und_befehle_sofort(self):
        ns = _takt_funktionen(tui=NS())

        async def ablauf():
            for kw in ({"erzwingen": True}, {"alles": True}, {"sofort": True}):
                await ns["bibliothekar"](1, **kw)
                await asyncio.sleep(0)
        asyncio.run(ablauf())
        self.assertEqual(ns["_laeufe"][0], (1, False, True))
        self.assertEqual(len(ns["_laeufe"]), 3)

    def test_sofortlauf_nimmt_das_vorgemerkte_mit(self):
        ns = _takt_funktionen(tui=NS())

        async def ablauf():
            await ns["bibliothekar"](5)
            ns["_BIB"]["takt"].cancel()
            await ns["bibliothekar"](None, sofort=True)
            await asyncio.sleep(0)
        asyncio.run(ablauf())
        self.assertEqual(ns["_laeufe"], [(5, False, False)])
        self.assertFalse(ns["_BIB"]["wartet"])

    def test_werkzeug_merkt_das_einlesen_vor(self):
        ns = _takt_funktionen(tui=NS())
        self.einlesen.wuenschen()

        async def ablauf():
            await ns["bibliothekar"](9)
        asyncio.run(ablauf())
        self.assertEqual(ns["_vorgemerkt"], [(9, True)])
        self.assertFalse(self.einlesen.abholen())

    def test_aufgeraeumt_ohne_gpu_platz_wartet_im_speicher(self):
        ns = _takt_funktionen(tui=NS())
        ns["memory"] = NS(gpu_platz=lambda: (False, "voll"))

        async def ablauf():
            await ns["bibliothekar"](4, erzwingen=True, sofort=True)
            await asyncio.sleep(0)
        asyncio.run(ablauf())
        self.assertEqual(ns["_laeufe"], [(4, False, False)])       # nur gesammelt


if __name__ == "__main__":
    unittest.main()
