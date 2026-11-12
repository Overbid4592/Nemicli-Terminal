"""Kontextlänge live: aus der Modell-Liste der Cloud-Anbieter, von ollama.com/api/show und von lokalen
Servern – vor dem Richtwert nach dem Namen. Kein echtes Netz: httpx ist ersetzt.

python -m unittest tests.test_kontextlaenge
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import cloudprovider as CP
import pricing
import providers as P


def _antwort(daten, status=200):
    return mock.Mock(status_code=status, json=lambda: daten, raise_for_status=lambda: None)


def _warten():
    for t in [t for t in threading.enumerate() if t.name == "kontextlaenge"]:
        t.join(5)


class _Sauber(unittest.TestCase):
    """Leerer Merkzettel, eigene Datei, Nachfragen erlaubt (mit ersetztem httpx)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        datei = Path(self.tmp.name) / "kontextlaengen.json"
        for p in (mock.patch.object(CP, "_kontext_datei", lambda: datei),
                  mock.patch.object(CP, "nachfragen_erlaubt", lambda: True),
                  mock.patch.dict(CP._KONTEXT, clear=True), mock.patch.dict(CP._KONTEXT_FEHLER, clear=True),
                  mock.patch.dict(CP._cloud_caps_cache, clear=True), mock.patch.dict(CP._cloud_caps_fehler, clear=True),
                  mock.patch.dict(CP._cloud_denken, clear=True),
                  mock.patch.dict(P._LOKAL_KONTEXT, clear=True), mock.patch.object(CP, "_kontext_geladen", True)):
            p.start()
            self.addCleanup(p.stop)
        self.datei = datei


class EintragTests(unittest.TestCase):

    def test_felder_der_anbieter(self):
        faelle = {
            "openrouter": {"id": "x", "context_length": 1048576, "top_provider": {"context_length": 65536}},
            "groq": {"id": "x", "context_window": 131072},
            "mistral": {"id": "x", "max_context_length": 262144},
            "vllm": {"id": "x", "max_model_len": 32768},
            "novita": {"id": "x", "context_size": 200000},
            "gemini": {"name": "models/x", "inputTokenLimit": 1048576},
            "github": {"id": "x", "limits": {"max_input_tokens": 128000, "max_output_tokens": 4096}},
            "venice": {"id": "x", "model_spec": {"availableContextTokens": 524288}},
            "deepinfra": {"id": "x", "metadata": {"context_length": "163840"}},
        }
        erwartet = {"openrouter": 1048576, "groq": 131072, "mistral": 262144, "vllm": 32768, "novita": 200000,
                    "gemini": 1048576, "github": 128000, "venice": 524288, "deepinfra": 163840}
        for name, eintrag in faelle.items():
            self.assertEqual(CP.kontext_aus_eintrag(eintrag), erwartet[name], name)

    def test_hugging_face_hoster_und_unsinn(self):
        hf = {"id": "x", "providers": [{"provider": "a", "context_length": 32768},
                                       {"provider": "b", "context_length": 131072}, {"provider": "c"}]}
        self.assertEqual(CP.kontext_aus_eintrag(hf), 131072)
        for unsinn in ({"id": "x"}, {"id": "x", "context_length": 8}, {"id": "x", "context_length": "viel"},
                       {"id": "x", "context_length": 10**12}, {"id": "x", "max_tokens": 4096}):
            self.assertIsNone(CP.kontext_aus_eintrag(unsinn), unsinn)

    def test_api_show(self):
        daten = {"model_info": {"general.architecture": "mistral3", "mistral3.context_length": 1048576,
                                "mistral3.embedding_length": 12288}}
        self.assertEqual(CP._show_kontext(daten), 1048576)
        self.assertIsNone(CP._show_kontext({"model_info": {}}))


class CloudTests(_Sauber):

    def test_ollama_cloud_mistral_large_4(self):
        """Der gemeldete Fehler: ohne Tabellen-Eintrag galt „mistral“ = 131.072, ollama.com nennt 1 Mio."""
        ref = "ollama_cloud:mistral-large-4:675b"
        show = {"capabilities": ["completion", "tools"],
                "model_info": {"general.architecture": "mistral3", "mistral3.context_length": 1048576}}
        with mock.patch.dict("os.environ", {"OLLAMA_API_KEY": "test"}), \
                mock.patch.object(CP.httpx, "post", return_value=_antwort(show)) as post:
            self.assertIn(pricing.context_window(ref), (131072, 1048576))  # erste Frage wartet nie aufs Netz
            _warten()
            self.assertEqual(pricing.context_window(ref), 1048576)
            self.assertEqual(post.call_count, 1)
        self.assertIn('"ollama_cloud:mistral-large-4:675b": 1048576', self.datei.read_text(encoding="utf-8"))

    def test_denkstufen_live(self):
        """Neue Modelle brauchen keinen Tabellen-Eintrag: Stufen und Fähigkeiten kommen aus /api/show."""
        import reasoning
        stufen = {"capabilities": ["completion", "thinking", "tools"], "thinking": {"values": ["low", "high", "max"],
                  "default": "max"}, "model_info": {"glm.context_length": 1048576}}
        fest = {"capabilities": ["completion", "thinking", "tools", "vision"],
                "model_info": {"mistral3.context_length": 1048576}}
        with mock.patch.dict(CP._cloud_denken, clear=True), mock.patch.dict("os.environ", {"OLLAMA_API_KEY": "test"}):
            with mock.patch.object(CP.httpx, "post", return_value=_antwort(stufen)):
                CP.ollama_cloud_capabilities("glm-9:cloud")
            with mock.patch.object(CP.httpx, "post", return_value=_antwort(fest)):
                CP.ollama_cloud_capabilities("mistral-large-4")
            self.assertEqual(reasoning.profile("ollama_cloud:glm-9:cloud"), "ollama_cloud")
            self.assertEqual(reasoning.cloud_effort("glm-9", "stark"), "high")
            self.assertEqual(reasoning.cloud_effort("glm-9", "normal"), "max")
            self.assertEqual(reasoning.profile("ollama_cloud:mistral-large-4"), "ollama_cloud")
            self.assertIsNone(reasoning.cloud_effort("mistral-large-4", "schnell"))   # keine Stufe, die es nicht kennt
            opts = reasoning.request_options("ollama_cloud", "mistral-large-4", "schnell")
            self.assertNotIn("reasoning_effort", opts)
            self.assertIn("1.048.576", reasoning.strength_menu("ollama_cloud:mistral-large-4")["normal"])
            self.assertEqual(CP.cloud_denken("gpt-oss"), None)
            self.assertEqual(reasoning.cloud_info("gpt-oss")["ctx"], 131072)              # bekannt: Tabelle

    def test_modell_liste_merkt_alle(self):
        liste = {"data": [{"id": "llama-4-scout", "context_window": 131072},
                          {"id": "kimi-k3", "context_window": 1048576}, {"id": "ohne-angabe"}]}
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": "test"}), \
                mock.patch.object(CP.httpx, "get", return_value=_antwort(liste)) as get:
            self.assertIsNone(P.kontext("groq", "kimi-k3"))
            _warten()
            self.assertEqual(P.kontext("groq", "kimi-k3"), 1048576)
            self.assertEqual(P.kontext("groq", "llama-4-scout"), 131072)
            self.assertEqual(get.call_count, 1)
            self.assertIsNone(P.kontext("groq", "ohne-angabe"))           # ganze Liste schon da: kein Abruf
            _warten()
            self.assertIsNone(P.kontext("groq", "ohne-angabe"))
            self.assertLessEqual(get.call_count, 2)
            _warten()
            P.kontext("groq", "ohne-angabe")                              # Fehlschlag gemerkt: Ruhe
            self.assertLessEqual(get.call_count, 2)

    def test_gemerkt_auf_der_platte(self):
        CP.kontext_merken("mistral", "mistral-large-4", 1048576)
        CP._KONTEXT.clear()
        with mock.patch.object(CP, "_kontext_geladen", False):
            self.assertEqual(CP.kontext("mistral", "mistral-large-4"), 1048576)

    def test_ohne_schluessel_und_in_der_test_suite_kein_netz(self):
        with mock.patch.dict("os.environ", {"MISTRAL_API_KEY": ""}), \
                mock.patch.object(CP.httpx, "get", side_effect=AssertionError("kein Abruf")):
            self.assertIsNone(CP.kontext("mistral", "x"))
        with mock.patch.dict("os.environ", {"MISTRAL_API_KEY": "test"}), \
                mock.patch.object(CP, "nachfragen_erlaubt", lambda: False), \
                mock.patch.object(threading, "Thread", side_effect=AssertionError("kein Faden")):
            self.assertIsNone(CP.kontext("mistral", "x"))

    def test_model_menue_merkt_mit(self):
        import asyncio
        liste = {"data": [{"id": "mistral-large-4", "max_context_length": 1048576}]}

        class Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **k):
                return _antwort(liste)
        with mock.patch.dict("os.environ", {"MISTRAL_API_KEY": "test"}), \
                mock.patch.object(P.httpx, "AsyncClient", Client), mock.patch.dict(P._models_cache, clear=True):
            self.assertEqual(asyncio.run(P.list_models("mistral", force=True)), ["mistral-large-4"])
        self.assertEqual(pricing.context_window("mistral:mistral-large-4"), 1048576)


class LokalTests(_Sauber):

    def _lauf(self, pid, model, antworten):
        def get(url, **k):
            for ende, daten in antworten.items():
                if url.endswith(ende):
                    return _antwort(daten)
            raise OSError("nicht da")
        with mock.patch.object(CP.httpx, "get", side_effect=get):
            self.assertIsNone(P.kontext(pid, model))
            _warten()
            return P.kontext(pid, model)

    def test_lm_studio_geladener_kontext(self):
        n = self._lauf("lmstudio", "qwen3-8b", {"/api/v0/models": {"data": [
            {"id": "qwen3-8b", "max_context_length": 131072, "loaded_context_length": 16384}]}})
        self.assertEqual(n, 16384)

    def test_llamacpp_props_und_vllm(self):
        self.assertEqual(self._lauf("llamacpp", "modell", {"/props": {"default_generation_settings": {"n_ctx": 8192}}}),
                         8192)
        self.assertEqual(self._lauf("vllm", "Qwen/Qwen3-8B", {"/v1/models": {"data": [
            {"id": "Qwen/Qwen3-8B", "max_model_len": 40960}]}}), 40960)

    def test_lokal_nur_kurz_gemerkt(self):
        P._LOKAL_KONTEXT["vllm:m"] = (time.monotonic() - P.LOKAL_KONTEXT_TTL - 1, 4096)
        with mock.patch.object(CP.httpx, "get", return_value=_antwort({"data": [{"id": "m", "max_model_len": 8192}]})):
            self.assertEqual(P.kontext("vllm", "m"), 4096)               # alter Wert, Abfrage läuft
            _warten()
            self.assertEqual(P.kontext("vllm", "m"), 8192)

    def test_lokales_ollama_bleibt_beim_messen(self):
        with mock.patch.object(CP.httpx, "get", side_effect=AssertionError("kein Abruf")):
            self.assertIsNone(P.kontext("ollama", "gemma4:12b"))


class AufteilungTests(unittest.TestCase):

    def test_registry_und_alte_namen(self):
        self.assertTrue(set(CP.CLOUD) <= set(P.PROVIDERS))
        self.assertTrue(set(P.LOKAL) <= set(P.PROVIDERS))
        self.assertFalse(set(CP.CLOUD) & set(P.LOKAL))
        self.assertTrue(all(p.keyless for p in P.LOKAL.values()))
        self.assertFalse(any(p.keyless for p in CP.CLOUD.values()))
        self.assertEqual(len(CP.CLOUD), 38)                       # 37 mit API-Schlüssel + ChatGPT-Konto
        self.assertEqual(list(P.PROVIDERS)[-5:], ["lmstudio", "jan", "llamacpp", "vllm", "ollama"])
        for name in ("Provider", "KEY_URLS", "_parse_models", "_chat", "status_text", "cloud_name_vision",
                     "ollama_cloud_capabilities", "_cloud_caps_cache", "ollama_kontext", "ollama_models"):
            self.assertTrue(hasattr(P, name), name)


if __name__ == "__main__":
    unittest.main()
