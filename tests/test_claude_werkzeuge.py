"""Claude mit echten Tool-Aufrufen: Denk-Blöcke (mit Signatur) gehen innerhalb einer
Werkzeug-Kette mit den Ergebnissen zurück, das übrige NemiCLI sieht weiter nur Text.
Ein Schein-Client ersetzt die API – kein Netz, keine Kosten.

python -m unittest tests.test_claude_werkzeuge
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import chat as C          # noqa: E402  (engines/chat.py, Anthropic)
except Exception:             # pragma: no cover - ohne anthropic-Paket
    C = None


class _Block(NS):
    def model_dump(self, exclude_none=False):
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


class _Strom:
    def __init__(self, texte, inhalt):
        self.texte, self.inhalt = texte, inhalt

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for t in self.texte:
                yield NS(type="content_block_delta", delta=NS(type="text_delta", text=t))
        return gen()

    async def get_final_message(self):
        return NS(content=self.inhalt)


class _Client:
    def __init__(self, antworten):
        self.antworten, self.aufrufe = list(antworten), []
        self.messages = NS(stream=self._stream)

    def _stream(self, **kw):
        self.aufrufe.append(kw)
        return _Strom(*self.antworten.pop(0))


@unittest.skipIf(C is None, "anthropic fehlt")
class ClaudeWerkzeugKetteTests(unittest.TestCase):

    def _lauf(self, chat, text):
        async def los():
            return [e async for e in chat.stream(text)]
        return asyncio.run(los())

    def test_kette_mit_denkbloecken_dann_wieder_text(self):
        denken = _Block(type="thinking", thinking="Erst die Datei lesen.", signature="sig1")
        aufruf = _Block(type="tool_use", id="t1", name="datei_lesen", input={"pfad": "a.txt"})
        client = _Client([
            (["Ich schaue nach."], [denken, _Block(type="text", text="Ich schaue nach."), aufruf]),
            (["Steht drin: Hallo."], [_Block(type="text", text="Steht drin: Hallo.")]),
            (["Gern."], [_Block(type="text", text="Gern.")]),
        ])
        with mock.patch.object(C, "AsyncAnthropic", lambda **kw: client), \
                mock.patch.object(C.persona, "build_system_prompt_async", mock.AsyncMock(return_value="SYS")), \
                mock.patch.object(C.pricing, "kuerzen", lambda b: None), \
                mock.patch.object(C.pricing, "aufraeum_hinweis", lambda b: None):
            chat = C.Chat(model="claude-test")
            text = "".join(e["text"] for e in self._lauf(chat, "Lies a.txt") if e["type"] == "text")
            self.assertIn('```aktion\n{"tool": "datei_lesen", "pfad": "a.txt"}\n```', text)
            self.assertIn("datei_lesen", {t["name"] for t in client.aufrufe[0]["tools"]})

            self._lauf(chat, "Ergebnis von 'datei_lesen' (erfolgreich):\nHallo")
            verlauf = client.aufrufe[1]["messages"]
            self.assertEqual([m["role"] for m in verlauf], ["user", "assistant", "user"])
            self.assertEqual(verlauf[1]["content"][0], {"type": "thinking", "thinking": "Erst die Datei lesen.",
                                                        "signature": "sig1"})
            self.assertEqual(verlauf[2]["content"], [{"type": "tool_result", "tool_use_id": "t1",
                                                      "content": "Ergebnis von 'datei_lesen' (erfolgreich):\nHallo"}])

            self._lauf(chat, "Danke!")                  # echte Nachricht: alles wieder Text
            verlauf = client.aufrufe[2]["messages"]
            self.assertTrue(all(isinstance(m["content"], str) for m in verlauf))
            self.assertEqual([m["role"] for m in chat.messages], ["user", "assistant"] * 3)

    def test_ergebnisse_den_aufrufen_zuordnen(self):
        text = "Ergebnis von 'a' (erfolgreich):\nx\n\nErgebnis von 'b' (fehlgeschlagen):\ny"
        r = C.ergebnisse_zuordnen(text, [("1", "a"), ("2", "b")])
        self.assertEqual([x["content"] for x in r], ["Ergebnis von 'a' (erfolgreich):\nx",
                                                    "Ergebnis von 'b' (fehlgeschlagen):\ny"])
        r = C.ergebnisse_zuordnen("Abgebrochen.", [("1", "a"), ("2", "b")])
        self.assertEqual([x["tool_use_id"] for x in r], ["1", "2"])
        self.assertEqual(r[0]["content"], "Abgebrochen.")


if __name__ == "__main__":
    unittest.main()
