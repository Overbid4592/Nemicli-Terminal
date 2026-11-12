"""OpenAI über die Responses API: Umbau des Verlaufs, Anfrage-Regeln (API-Schlüssel und ChatGPT-Konto), Streaming,
Fehler, Helfer, Modellwechsel und Werkzeug-Protokoll. Keine echten Anfragen – der Client ist nachgebaut."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import httpx  # noqa: E402
import openai  # noqa: E402

import actions  # noqa: E402
import chatgpt_login  # noqa: E402
import cloudprovider as CP  # noqa: E402
import openai_responses as R  # noqa: E402
import persona  # noqa: E402
import providers as P  # noqa: E402


async def _anleitung(_text, **_k):
    return "SYNTHETISCHE ANLEITUNG (Persönlichkeit, Gedächtnis, Werkzeuge)"


class _Strom:
    def __init__(self, ereignisse):
        self.ereignisse = list(ereignisse)

    def __aiter__(self):
        return self._lauf()

    async def _lauf(self):
        for e in self.ereignisse:
            if isinstance(e, Exception):
                raise e
            yield e


def _fertig(eingang=11, ausgang=7):
    return {"type": "response.completed", "response": {"usage": {"input_tokens": eingang, "output_tokens": ausgang}}}


class _Responses:
    def __init__(self):
        self.aufrufe = []
        self.antworten = []

    async def create(self, **kwargs):
        self.aufrufe.append(kwargs)
        a = self.antworten.pop(0) if self.antworten else [{"type": "response.output_text.delta", "delta": "ok"},
                                                          _fertig()]
        if isinstance(a, Exception):
            raise a
        return _Strom(a)


def _status_fehler(status, code, message="", param=None):
    antwort = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    return openai.APIStatusError(message or code, response=antwort,
                                 body={"error": {"code": code, "message": message or code, "param": param}})


class Grundlage(unittest.IsolatedAsyncioTestCase):
    anbieter, modell = "openai", "gpt-6.1-sol"

    def setUp(self):
        for p in (mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-synthetisch"}),
                  mock.patch.object(persona, "build_system_prompt_async", _anleitung),
                  mock.patch.object(chatgpt_login, "zugang", return_value="synthetisches-zugangs-token"),
                  mock.patch.object(chatgpt_login, "nutzbar", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.chat = R.ResponsesChat(self.anbieter, self.modell, "normal")
        self.responses = _Responses()
        self.chat.client = NS(responses=self.responses, api_key="")

    async def sammeln(self, text="Hallo", bilder=None):
        return [t async for t in self.chat.stream(text, bilder)]


class Eingabe(unittest.TestCase):
    def test_umbau_des_verlaufs(self):
        anleitung, eingabe = R.als_eingabe([
            {"role": "system", "content": "Du bist Nemi."},
            {"role": "user", "content": [{"type": "text", "text": "Schau"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
            {"role": "assistant", "content": "Ein Fuchs!"},
            {"role": "system", "content": "Hinweis mitten im Verlauf"},
        ])
        self.assertEqual(anleitung, "Du bist Nemi.")
        self.assertEqual(eingabe[0], {"role": "user", "content": [
            {"type": "input_text", "text": "Schau"}, {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]})
        self.assertEqual(eingabe[1], {"role": "assistant", "content": [{"type": "output_text", "text": "Ein Fuchs!"}]})
        self.assertEqual(eingabe[2]["role"], "developer")                 # nie „system“ im input
        self.assertNotIn("system", [e["role"] for e in eingabe])


class MitSchluessel(Grundlage):
    async def test_anfrage_und_streaming(self):
        self.responses.antworten = [[
            {"type": "response.reasoning_summary_text.delta", "delta": "Ich überlege"},
            {"type": "response.output_text.delta", "delta": "Hallo "},
            {"type": "response.output_text.delta", "delta": "du"},
            _fertig(20, 5)]]
        teile = await self.sammeln()
        a = self.responses.aufrufe[0]
        self.assertEqual((a["model"], a["stream"], a["store"]), ("gpt-6.1-sol", True, False))
        self.assertTrue(a["instructions"].startswith("SYNTHETISCHE ANLEITUNG"))
        self.assertEqual(a["reasoning"], {"effort": "medium", "summary": "auto"})
        self.assertEqual(a["max_output_tokens"], 32768)
        self.assertEqual([t["type"] for t in teile if t["type"] in ("thinking", "usage")], ["thinking", "usage"])
        self.assertEqual("".join(t["text"] for t in teile if t["type"] == "text"), "Hallo du")
        self.assertEqual(self.chat.messages[-1], {"role": "assistant", "content": "Hallo du"})
        self.assertTrue(self.chat.denkt_selbst)

    async def test_budget_erreicht_wird_hinweis(self):
        self.responses.antworten = [[{"type": "response.output_text.delta", "delta": "Halb"},
                                     {"type": "response.incomplete", "response": {"usage": None}}]]
        teile = await self.sammeln()
        self.assertTrue(any(t["type"] == "note" and "Tokenbudget" in t["text"] for t in teile))

    async def test_abbruch_ohne_ende_ist_fehler(self):
        self.responses.antworten = [[{"type": "response.output_text.delta", "delta": "Halb"}]]
        with self.assertRaises(R.ResponsesFehler):
            await self.sammeln()
        self.assertEqual(self.chat.messages, [])                        # die Frage fällt wieder raus

    async def test_zusammenfassung_nicht_erlaubt_dann_ohne(self):
        self.responses.antworten = [_status_fehler(400, "unsupported_value",
                                                   "Your organization must be verified to generate reasoning summaries"),
                                    [{"type": "response.output_text.delta", "delta": "ok"}, _fertig()]]
        await self.sammeln()
        self.assertEqual(self.responses.aufrufe[1]["reasoning"], {"effort": "medium"})
        await self.sammeln("nochmal")
        self.assertNotIn("summary", self.responses.aufrufe[2]["reasoning"])

    async def test_helfer_und_zusammenfassen(self):
        self.responses.antworten = [[{"type": "response.output_text.delta", "delta": "Kurz."}, _fertig()]]
        self.assertEqual(await self.chat.ask_once("Fasse zusammen", "Du hilfst."), "Kurz.")
        a = self.responses.aufrufe[0]
        self.assertEqual(a["reasoning"], {"effort": "low"})
        self.assertEqual(a["instructions"], "Du hilfst.")
        self.assertTrue(a["stream"])

    async def test_werkzeug_protokoll_bleibt(self):
        antwort = 'Ich male dir einen Fuchs.\n```json\n{"tool": "bild_malen", "prompt": "a fox in snow"}\n```'
        self.responses.antworten = [[{"type": "response.output_text.delta", "delta": antwort[:20]},
                                     {"type": "response.output_text.delta", "delta": antwort[20:]}, _fertig()]]
        await self.sammeln("Mal mir einen Fuchs")
        acts, _ = actions.parse_actions(self.chat.messages[-1]["content"])
        self.assertEqual([a.get("tool") for a in acts], ["bild_malen"])   # ausgeführt wird lokal (Krea 2)

    async def test_bilder_gehen_als_input_image(self):
        await self.sammeln("Was siehst du?", ["data:image/png;base64,AAAA"])
        teile = self.responses.aufrufe[0]["input"][-1]["content"]
        self.assertIn({"type": "input_image", "image_url": "data:image/png;base64,AAAA"}, teile)

    async def test_modellwechsel_behaelt_den_verlauf(self):
        await self.sammeln("Erste Frage")
        neu = R.ResponsesChat("chatgpt", "gpt-6-luna", "normal")
        neu.client = NS(responses=self.responses, api_key="")
        neu.messages = self.chat.messages
        [t async for t in neu.stream("Zweite Frage")]
        eingabe = self.responses.aufrufe[-1]["input"]
        self.assertEqual([e["role"] for e in eingabe], ["user", "assistant", "user"])
        self.assertEqual(self.responses.aufrufe[-1]["model"], "gpt-6-luna")


class MitKonto(Grundlage):
    anbieter, modell = "chatgpt", "gpt-6.1-sol"

    async def test_regeln_des_kontos(self):
        self.chat.strength = "max"
        await self.sammeln()
        a = self.responses.aufrufe[0]
        self.assertEqual((a["stream"], a["store"]), (True, False))
        for verboten in ("max_output_tokens", "temperature", "top_p", "previous_response_id", "truncation",
                         "metadata", "user", "background", "conversation"):
            self.assertNotIn(verboten, a)
        self.assertEqual(a["reasoning"]["effort"], "max")
        self.assertNotIn("system", [e["role"] for e in a["input"]])
        self.assertEqual(self.chat.client.api_key, "synthetisches-zugangs-token")   # vor jeder Anfrage frisch

    async def test_kontingent_aufgebraucht(self):
        self.responses.antworten = [[{"type": "response.failed", "response": {"error": {
            "code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}]]
        with self.assertRaises(R.ResponsesFehler) as f:
            await self.sammeln()
        self.assertIn("Kontingent", str(f.exception))
        self.assertEqual(f.exception.code, "subscription_sharing_usage_limit_exceeded")
        self.assertEqual(self.chat.messages, [])

    async def test_fehler_vom_server_verstaendlich(self):
        self.responses.antworten = [_status_fehler(429, "subscription_sharing_usage_limit_exceeded")]
        with self.assertRaises(R.ResponsesFehler) as f:
            await self.sammeln()
        self.assertIn("Einstellungen", str(f.exception))
        self.responses.antworten = [_status_fehler(400, "subscription_sharing_unsupported_capability",
                                                   param="tools[0]")]
        with self.assertRaises(R.ResponsesFehler) as f:
            await self.sammeln()
        self.assertIn("tools[0]", str(f.exception))


class Anbieter(unittest.TestCase):
    def test_konto_als_anbieter(self):
        p = P.get("chatgpt")
        self.assertEqual(p.kind, "chatgpt")
        with mock.patch.object(chatgpt_login, "nutzbar", return_value=False):
            self.assertFalse(p.available())
        with mock.patch.object(chatgpt_login, "nutzbar", return_value=True), \
                mock.patch.object(chatgpt_login, "zugang", return_value="tok"):
            self.assertTrue(p.available())
            self.assertEqual(CP._auth_headers(p), {"Authorization": "Bearer tok"})

    def test_modell_liste_des_kontos(self):
        daten = {"models": [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list"},
                            {"slug": "intern", "visibility": "hide"}, {"slug": "gpt-6-luna", "visibility": "list"}]}
        self.assertEqual(CP._parse_models(P.get("chatgpt"), daten), ["gpt-6.1-sol", "gpt-6-luna"])
        self.assertEqual(chatgpt_login.modelle_aus(daten), [("gpt-6.1-sol", "GPT-6.1 Sol"), ("gpt-6-luna", "gpt-6-luna")])

    def test_neue_modelle_kennen_denkstufen(self):
        import reasoning
        for m in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-5.6-sol"):
            self.assertEqual(reasoning.profile(f"openai:{m}"), "openai_effort", m)
            self.assertEqual(reasoning.openai_effort(m, "max"), "max")
        self.assertEqual(reasoning.openai_effort("gpt-5.4", "max"), "high")
        self.assertIsNone(reasoning.profile("openai:o1"))                     # abgeschaltet


if __name__ == "__main__":
    unittest.main()
