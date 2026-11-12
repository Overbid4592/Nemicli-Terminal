"""Viele Cloud-Anbieter (Registry, Modell-Listen in beiden Formen), 🔍 Suchen in langen Menüs, Kimi K3.1 Denkstufen."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import confirm                                         # noqa: E402
import providers as P                                  # noqa: E402
import reasoning as R                                  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_jeder_anbieter_vollstaendig(self):
        self.assertGreaterEqual(sum(1 for p in P.PROVIDERS.values() if not p.keyless), 37)
        for pid, p in P.PROVIDERS.items():
            with self.subTest(pid=pid):
                self.assertEqual(pid, p.id)
                if p.keyless or p.kind == "chatgpt":                       # ChatGPT-Konto: Anmeldung statt Schlüssel
                    continue
                self.assertTrue(p.env.endswith("_API_KEY"), p.env)            # wird verschlüsselt und nie weitergegeben
                self.assertIn(pid, P.KEY_URLS)
                self.assertTrue(P.KEY_URLS[pid].startswith("https://"))
                if p.chat_base:
                    self.assertTrue(p.chat_base.startswith("https://"), p.chat_base)
        self.assertEqual(len({p.env for p in P.PROVIDERS.values()}), len(P.PROVIDERS))

    def test_lokale_server_nur_wenn_sie_laufen(self):
        lm = P.PROVIDERS["lmstudio"]
        self.assertEqual(("http://127.0.0.1:1234/v1", True), (lm.chat_base, lm.keyless))
        P._LOKAL_CACHE.clear()
        with mock.patch.object(P.httpx, "get", side_effect=P.httpx.ConnectError("aus")):
            self.assertFalse(lm.available())
        P._LOKAL_CACHE.clear()
        with mock.patch.object(P.httpx, "get", return_value=mock.Mock(status_code=200)) as get:
            self.assertTrue(lm.available())
            self.assertTrue(lm.available())                              # gemerkt: nur eine Anfrage
        get.assert_called_once()
        P._LOKAL_CACHE.clear()

    def test_modell_liste_in_beiden_formen(self):
        together = P.PROVIDERS["together"]
        liste = [{"id": "a/chat", "type": "chat"}, {"id": "a/embed", "type": "embedding"}, {"id": "b/ohne-art"}]
        self.assertEqual(["a/chat", "b/ohne-art"], P._parse_models(together, liste))
        self.assertEqual(["x"], P._parse_models(together, {"data": [{"id": "x"}]}))
        self.assertFalse(P._chat("BAAI/bge-m3-embed"))
        self.assertTrue(P._chat("meta-llama/Llama-4-Maverick"))


class SucheTests(unittest.TestCase):
    def lauf(self, optionen, antworten, texte=()):
        gezeigt, antworten, texte = [], list(antworten), list(texte)

        async def frage(f, o):
            gezeigt.append((f, o))
            return antworten.pop(0)

        async def text(f):
            return texte.pop(0)

        wahl = asyncio.run(confirm.ask_suche("Welches?", optionen, frage_fn=frage, text_fn=text))
        return wahl, gezeigt

    def test_kurze_liste_ohne_suche(self):
        wahl, gezeigt = self.lauf([("a", "A"), ("__cancel__", "Abbrechen")], ["a"])
        self.assertEqual("a", wahl)
        self.assertNotIn(confirm.SUCHEN, [w for w, _ in gezeigt[0][1]])

    def test_suchen_findet_auch_versteckte(self):
        optionen = [(f"m{i}", f"modell-{i}") for i in range(50)] + [("kimi", "Moonshot Kimi K3.1"),
                                                                     ("__cancel__", "Abbrechen")]
        wahl, gezeigt = self.lauf(optionen, [confirm.SUCHEN, "kimi"], ["moonshot kimi"])
        self.assertEqual("kimi", wahl)
        erste = [w for w, _ in gezeigt[0][1]]
        self.assertEqual(confirm.SUCHEN, erste[0])
        self.assertNotIn("kimi", erste)                                  # nur 30 auf einmal
        self.assertEqual("__cancel__", erste[-1])                        # Esc bleibt Abbrechen
        self.assertEqual(["__suchen__", "__alle__", "kimi", "__cancel__"], [w for w, _ in gezeigt[1][1]])

    def test_nichts_gefunden_zeigt_wieder_alles(self):
        optionen = [(f"m{i}", f"modell-{i}") for i in range(12)] + [("__cancel__", "Abbrechen")]
        with mock.patch.object(confirm.ui, "warn") as warn:
            wahl, gezeigt = self.lauf(optionen, [confirm.SUCHEN, "__cancel__"], ["gibtsnicht"])
        self.assertEqual("__cancel__", wahl)
        warn.assert_called_once()
        self.assertEqual(len(gezeigt[0][1]), len(gezeigt[1][1]))


class KimiTests(unittest.TestCase):
    def test_stufen_direkt_und_ueber_ollama_cloud(self):
        for st, soll in (("schnell", "low"), ("normal", "max"), ("stark", "high"), ("max", "max")):
            with self.subTest(st=st):
                self.assertEqual({"reasoning_effort": soll}, R.request_options("moonshot", "kimi-k3.1-pro", st))
                self.assertEqual(soll, R.request_options("ollama_cloud", "kimi-k3.1-pro-highspeed:cloud",
                                                         st)["reasoning_effort"])
        helfer = R.request_options("moonshot", "kimi-k3.1-pro", "normal", helper=True)
        self.assertEqual({"max_completion_tokens": R.CLOUD_HELPER_BUDGET, "reasoning_effort": "low"}, helfer)
        self.assertNotIn("none", [R.cloud_effort("kimi-k3.1-pro", s) for s in R.BUDGETS])   # nicht abschaltbar
        self.assertEqual({}, R.request_options("moonshot", "moonshot-v1-8k", "normal"))      # unbekannt: nichts raten


if __name__ == "__main__":
    unittest.main()
