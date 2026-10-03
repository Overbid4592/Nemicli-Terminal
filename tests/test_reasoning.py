"""Isolierte Parameter-Tests: keine echten Clients, Schlüssel oder Modellaufrufe.

Ausführen mit: python -B -m unittest discover -s tests -p test_reasoning.py
Alle Testnachrichten und Antworten entstehen hier im Arbeitsspeicher.
"""

import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_source(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REASONING = load_source("test_reasoning_rules", "engines/reasoning.py")
TEXTPROC = load_source("test_textproc", "tools/textproc.py")


class FakeError(Exception):
    def __init__(self, status, message, body=None):
        super().__init__(message)
        self.status_code = status
        self.body = body


def response(finish="stop", content="synthetische Testantwort"):
    return NS(choices=[NS(finish_reason=finish, message=NS(content=content))])


class FakeCompletions:
    def __init__(self):
        self.calls = []
        self.results = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0) if self.results else response()
        if isinstance(result, Exception):
            raise result
        return result


async def synthetic_system(_text):
    return "synthetischer Testkontext"


class ReasoningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.completions = FakeCompletions()
        fake_client = NS(chat=NS(completions=self.completions))
        provider = NS(chat_base="https://example.invalid/v1", keyless=False,
                      key=lambda: "synthetischer-testschluessel")
        self.dependencies = patch.dict(sys.modules, {
            "reasoning": REASONING,
            "textproc": TEXTPROC,
            "persona": NS(build_system_prompt_async=synthetic_system),
            "pricing": NS(trim_history=lambda messages, ref: messages, kuerzen=lambda backend: None,
                          aufraeum_hinweis=lambda backend: None),
            "providers": NS(get=lambda pid: provider,
                            ollama_base=lambda: "http://example.invalid/v1"),
            "vision": NS(user_content=lambda text, images: text),
            "config": NS(load=lambda: {}),
            "openai": NS(AsyncOpenAI=lambda **kwargs: fake_client),
        })
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)
        self.cloud = load_source("test_cloud_backend", "engines/cloud.py")

    def backend(self, provider="ollama_cloud", model="kimi-k2.6", strength="normal"):
        return self.cloud.CloudChat(provider, model, strength)

    def test_kimi_budgets_and_helper_floor(self):
        for strength, budget in (("schnell", 4096), ("normal", 8192),
                                 ("stark", 16384), ("max", 32768)):
            with self.subTest(strength=strength):
                self.assertEqual(REASONING.request_options("ollama_cloud", "kimi-k2.6", strength),
                                 {"max_tokens": budget, "reasoning_effort": "high"})
                helper = REASONING.request_options("ollama", "kimi-k2.5:cloud", strength, helper=True)
                self.assertEqual(helper, {"max_tokens": max(budget, 8192), "reasoning_effort": "high"})
        self.assertIn("keine getrennten Denkstufen", REASONING.strength_note("ollama_cloud:kimi-k2.6", "max"))

    def test_effort_levels_and_conservative_detection(self):
        for strength, effort in (("schnell", "low"), ("normal", "medium"),
                                 ("stark", "high"), ("max", "high")):
            opts = REASONING.request_options("ollama", "gpt-oss:20b", strength)
            self.assertEqual(opts["reasoning_effort"], effort)
        self.assertEqual(REASONING.request_options("openai", "o3-mini-2025-01-31", "max"),
                         {"max_completion_tokens": 32768, "reasoning_effort": "high"})
        for ref in ("openai:gpt-5-pro", "openai:gpt-5-chat-latest", "openai:o1-preview",
                    "openai:gpt-5.999", "openrouter:openai/o3", "ollama:qwen3:8b"):
            with self.subTest(ref=ref):
                self.assertIsNone(REASONING.profile(ref))
                provider, _, model = ref.partition(":")
                self.assertEqual(REASONING.request_options(provider, model, "max"), {"max_tokens": 65536})
        self.assertIn("keine zusätzliche Denkstufe", REASONING.strength_note("openai:o3", "max"))

    async def test_main_and_helper_requests_share_strength(self):
        backend = self.backend(strength="stark")
        await backend._open([{"role": "user", "content": "synthetische Testnachricht"}])
        await backend.ask_once("synthetische Helferaufgabe", "synthetischer Testkontext")
        for call in self.completions.calls:
            self.assertEqual(call["max_tokens"], 16384)
            self.assertEqual(call["reasoning_effort"], "high")
        self.assertEqual(backend.messages, [])
        unknown = self.backend("openrouter", "arbitrary-model", "max")
        self.assertIsNone(unknown.strength)
        await unknown.ask_once("synthetische Helferaufgabe", "synthetischer Testkontext")
        self.assertEqual(self.completions.calls[-1]["max_tokens"], 4096)
        self.assertNotIn("reasoning_effort", self.completions.calls[-1])

    async def test_openai_uses_completion_budget(self):
        backend = self.backend("openai", "gpt-5.2", "schnell")
        await backend._open([])
        await backend.ask_once("synthetische Helferaufgabe", "synthetischer Testkontext")
        self.assertEqual(self.completions.calls[0]["max_completion_tokens"], 4096)
        self.assertEqual(self.completions.calls[1]["max_completion_tokens"], 8192)
        self.assertTrue(all(call["reasoning_effort"] == "low" for call in self.completions.calls))
        self.assertTrue(all("max_tokens" not in call for call in self.completions.calls))

    async def test_usage_retry_keeps_reasoning_and_budget(self):
        self.completions.results = [FakeError(400, "Unknown parameter: 'stream_options'"), response()]
        await self.backend(strength="max")._open([])
        self.assertEqual(len(self.completions.calls), 2)
        first, second = self.completions.calls
        self.assertEqual({key: value for key, value in first.items() if key != "stream_options"}, second)
        self.assertEqual(second["reasoning_effort"], "high")
        self.assertEqual(second["max_tokens"], 32768)
        self.assertTrue(self.cloud._usage_option_rejected(FakeError(422, "Validation failed", {
            "detail": [{"loc": ["body", "stream_options"], "type": "extra_forbidden"}]})))

    async def test_unrelated_errors_are_not_retried(self):
        for error in (FakeError(401, "Unknown parameter: 'stream_options'"),
                      FakeError(429, "rate limit"), FakeError(500, "server error"),
                      FakeError(400, "Unsupported parameter: 'reasoning_effort'"),
                      FakeError(400, "Invalid value for stream_options"), OSError("test connection error")):
            with self.subTest(error=str(error)):
                self.completions.calls.clear()
                self.completions.results = [error]
                with self.assertRaises(type(error)):
                    await self.backend()._open([])
                self.assertEqual(len(self.completions.calls), 1)

    async def test_token_limit_is_visible_and_helper_raises(self):
        async def chunks():
            yield NS(usage=None, choices=[NS(finish_reason=None,
                delta=NS(content=None, reasoning="synthetisches Denkereignis"))])
            yield NS(usage=None, choices=[NS(finish_reason="length", delta=NS(content=None))])
        self.completions.results = [chunks(), response("length", "")]
        backend = self.backend()
        events = [event async for event in backend.stream("synthetische Testnachricht")]
        self.assertTrue(any(event["type"] == "note" and "Tokenbudget" in event["text"] for event in events))
        self.assertIn("Tokenbudget", backend.messages[-1]["content"])
        with self.assertRaisesRegex(RuntimeError, "Helferantwort ist unvollständig"):
            await backend.ask_once("synthetische Helferaufgabe", "synthetischer Testkontext")

    def test_public_model_helpers_without_bootstrap(self):
        tree = ast.parse((ROOT / "engines/models.py").read_text(encoding="utf-8"))
        names = {"STRENGTHS", "DEFAULT_STRENGTH", "effort_for", "strength_supported", "strength_menu", "strength_note"}
        nodes = [node for node in tree.body if getattr(node, "name", None) in names
                 or (isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) in names)
                 or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in names for t in node.targets))]
        scope = {"reasoning": REASONING}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "model_strength_helpers", "exec"), scope)
        self.assertEqual(scope["strength_menu"](), scope["strength_menu"]("anthropic:claude-sonnet-4-6"))
        self.assertEqual(scope["effort_for"]("max"), "max")
        self.assertTrue(scope["strength_supported"]("ollama_cloud:kimi-k2.6"))
        self.assertFalse(scope["strength_supported"]("local:unknown"))
        self.assertEqual(scope["strength_menu"]("local:unknown"), {})
        self.assertIn("Budget", scope["strength_note"]("ollama_cloud:kimi-k2.6", "stark"))


if __name__ == "__main__":
    unittest.main()


class OllamaCloudTabelle(unittest.TestCase):
    """Echte Kontextfenster und Denkstufen der Ollama-Cloud-Modelle (Stand 19.09.2026)."""

    def test_kontext_aus_tabelle(self):
        import sys
        for sub in ("core", "engines", "tools", "ui"):
            if str(ROOT / sub) not in sys.path:
                sys.path.insert(0, str(ROOT / sub))
        import pricing
        self.assertEqual(1_048_576, pricing.context_window("ollama_cloud:deepseek-v4.1-flash"))
        self.assertEqual(262_144, pricing.context_window("ollama_cloud:qwen3.5:397b"))
        self.assertEqual(512_000, pricing.context_window("ollama_cloud:minimax-m3"))
        self.assertEqual(131_072, pricing.context_window("ollama_cloud:deepseek-unbekannt"))   # Teilstring-Fallback

    def test_denkstufen_und_helferbudget(self):
        self.assertEqual({"max_tokens": 65536, "reasoning_effort": "high"},
                         REASONING.request_options("ollama_cloud", "deepseek-v4.1-flash", "normal"))
        self.assertEqual({"max_tokens": 65536, "reasoning_effort": "max"},
                         REASONING.request_options("ollama_cloud", "deepseek-v4.1-flash", "max"))
        self.assertEqual({"max_tokens": 16384, "reasoning_effort": "low"},
                         REASONING.request_options("ollama_cloud", "deepseek-v4.1-flash", "stark", helper=True))
        self.assertEqual({"max_tokens": 65536, "reasoning_effort": "none"},
                         REASONING.request_options("ollama_cloud", "qwen3.5:397b", "schnell"))
        self.assertEqual({"max_tokens": 65536}, REASONING.request_options("ollama_cloud", "qwen3.5:397b", "normal"))
        self.assertEqual({"max_tokens": 65536}, REASONING.request_options("ollama_cloud", "mistral-large-3:675b", "max"))
        self.assertIsNone(REASONING.profile("ollama:deepseek-v4.1-flash"))       # lokal: keine Cloud-Tabelle
        self.assertIn("1.048.576", REASONING.strength_menu("ollama_cloud:glm-5.3")["normal"])
