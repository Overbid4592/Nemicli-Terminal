"""Offline-Tests: /spiel (tools/spiele.py) – Parser, Zug-Holen mit Fake-Modell, Server.

python -m unittest discover -s tests -p test_spiele.py
"""

import asyncio
import json
import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import spiele  # noqa: E402


class FakeBackend:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.gesehen = []

    async def ask_messages(self, system, messages):
        self.gesehen.append((system, messages))
        return self.antworten.pop(0) if self.antworten else "{}"


class ParseTests(unittest.TestCase):
    def test_json_zug(self):
        zug, text, ok = spiele.parse('{"zug": "e2e4", "text": "Los geht’s."}', ["e2e4", "d2d4"])
        self.assertEqual(("e2e4", "Los geht’s.", True), (zug, text, ok))

    def test_zug_gross_klein_und_leerzeichen(self):
        zug, _, ok = spiele.parse('{"zug": "E2 E4", "text": ""}', ["e2e4"])
        self.assertEqual(("e2e4", True), (zug, ok))

    def test_zug_nur_im_text(self):
        zug, _, ok = spiele.parse("Ich spiele d1-d2xf4, das tut weh.", ["d1-d2", "d1-d2xf4"])
        self.assertEqual(("d1-d2xf4", True), (zug, ok))

    def test_ungueltig(self):
        zug, text, ok = spiele.parse('{"zug": "e2e5", "text": "hm"}', ["e2e4"])
        self.assertEqual((None, "hm", False), (zug, text, ok))

    def test_kein_zug_erwartet(self):
        zug, text, ok = spiele.parse('{"zug": null, "text": "Gut gespielt!"}', [])
        self.assertEqual((None, "Gut gespielt!", True), (zug, text, ok))

    def test_json_mit_drumherum(self):
        zug, text, ok = spiele.parse('Klar!\n```json\n{"zug": "a1", "text": "Mitte ist mir zu langweilig."}\n```', ["a1", "b2"])
        self.assertEqual(("a1", True), (zug, ok))


class ZugHolenTests(unittest.TestCase):
    def test_zweiter_versuch_dann_zufall(self):
        be = FakeBackend(['{"zug": "z9", "text": "erst"}', '{"zug": "z8", "text": "nochmal"}'])
        p = {"spiel": "tictactoe", "brett": "...", "zuege": ["a1", "b2"], "gespraech": []}
        d = asyncio.run(spiele.zug_holen(be, "sys", p))
        self.assertIn(d["zug"], ["a1", "b2"])
        self.assertTrue(d["zufall"])
        self.assertEqual(2, len(be.gesehen))
        self.assertIn("NICHT erlaubt", be.gesehen[1][1][-1]["content"])

    def test_gueltig_beim_ersten_mal(self):
        be = FakeBackend(['{"zug": "b2", "text": "Mitte."}'])
        d = asyncio.run(spiele.zug_holen(be, "sys", {"spiel": "tictactoe", "brett": "", "zuege": ["a1", "b2"]}))
        self.assertEqual({"zug": "b2", "text": "Mitte.", "zufall": False}, d)

    def test_verlauf_und_chat_landen_in_der_nachricht(self):
        be = FakeBackend(['{"zug": null, "text": "Danke!"}'])
        p = {"spiel": "schach", "brett": "8 r n b", "zuege": [], "verlauf": ["e2e4", "e7e5"],
             "gespraech": [{"von": "du", "text": "hi"}, {"von": "ki", "text": "hallo"}],
             "chat": "gut gespielt", "nutzer": "Max"}
        d = asyncio.run(spiele.zug_holen(be, "sys", p))
        self.assertEqual("Danke!", d["text"])
        msgs = be.gesehen[0][1]
        self.assertEqual(["user", "assistant", "user"], [m["role"] for m in msgs])
        self.assertIn("Max sagt: gut gespielt", msgs[-1]["content"])
        self.assertIn("Bisherige Züge: e2e4, e7e5", msgs[-1]["content"])
        self.assertIn("Kein Zug fällig", msgs[-1]["content"])

    def test_system_prompt(self):
        s = spiele.system_prompt("Ich bin Maia.", "Maia", "Max", "dame", "Schwarz")
        self.assertIn("Ich bin Maia.", s)
        self.assertIn("Du spielst Schwarz", s)
        self.assertIn("Schlagzwang", s)
        self.assertIn('"zug"', s)


class ServerTests(unittest.TestCase):
    def test_server_rundlauf(self):
        loop = asyncio.new_event_loop()
        import threading
        threading.Thread(target=loop.run_forever, daemon=True).start()

        async def denke(p):
            if p.get("wer"):
                return {"name": "Maia", "nutzer": "Max"}
            return {"zug": p["zuege"][0], "text": "ok", "zufall": False}

        srv = spiele.SpieleServer(loop, denke)
        url = srv.start()
        try:
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            html = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            self.assertIn("NemiCLI · Spiele", html)
            for name in ("class Schach", "class Dame", "class Muehle", "class TTT"):
                self.assertIn(name, html)
            base = url.split("/?")[0]

            def post(pfad, daten, token=srv.token):
                req = urllib.request.Request(base + pfad, data=json.dumps(daten).encode("utf-8"),
                                             headers={"Content-Type": "application/json", "X-Token": token})
                return urllib.request.urlopen(req, timeout=10)

            r = post("/zug", {"spiel": "tictactoe", "wer": True})
            self.assertEqual({"name": "Maia", "nutzer": "Max"}, json.loads(r.read()))
            r = post("/zug", {"spiel": "schach", "zuege": ["e2e4"], "brett": ""})
            self.assertEqual("e2e4", json.loads(r.read())["zug"])
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post("/zug", {"spiel": "schach", "zuege": ["e2e4"]}, token="falsch")
            self.assertEqual(403, cm.exception.code)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post("/zug", {"spiel": "poker", "zuege": []})
            self.assertEqual(400, cm.exception.code)
        finally:
            srv.stop()
            loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    unittest.main()
