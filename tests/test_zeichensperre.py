"""Gesperrte Zeichen: keine KI gibt sie aus – Antwort, Denktext, Einzelfragen und Verlauf.

python -m unittest tests.test_zeichensperre
"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import zeichensperre as Z                     # noqa: E402

X = chr(0x1F60F)
VAR = chr(0xFE0F)


class Attrappe:
    def __init__(self):
        self.messages = []

    async def stream(self, text, images=None):
        for teil in ("Na ", X, " klar", VAR, "!"):
            yield {"type": "text", "text": teil}
        yield {"type": "thinking", "text": "denkt " + X}
        yield {"type": "usage", "output": 3}
        self.messages.append({"role": "assistant", "content": "Na " + X + " klar!"})

    async def ask_once(self, prompt, system):
        return "ok " + X + VAR + " gut"

    async def ask_messages(self, system, messages):
        return X


class Filter(unittest.TestCase):
    def test_ohne(self):
        self.assertEqual("Na klar!", Z.ohne("Na " + X + " klar!"))
        self.assertEqual("Na klar!", Z.ohne("Na " + X + VAR + " klar!"))
        self.assertEqual("Herz 💜 bleibt 😊", Z.ohne("Herz 💜 bleibt 😊"))
        self.assertEqual("", Z.ohne(""))

    def test_backend_gesperrt(self):
        b = Z.sperren(Attrappe())
        self.assertIs(b, Z.sperren(b))                              # nur einmal umlegen

        async def lauf():
            evs = [ev async for ev in b.stream("hi")]
            return evs, await b.ask_once("p", "s"), await b.ask_messages("s", [])
        evs, eins, mehr = asyncio.run(lauf())
        text = "".join(e["text"] for e in evs if e.get("type") == "text")
        self.assertNotIn(X, text)
        self.assertNotIn(X, "".join(e.get("text", "") for e in evs))
        self.assertEqual({"type": "usage", "output": 3}, evs[-1])
        self.assertEqual(("ok gut", ""), (eins, mehr))
        self.assertEqual("Na klar!", b.messages[-1]["content"])      # Verlauf aufgeräumt

    def test_verlauf_mit_bloecken_und_nutzer_bleibt(self):
        verlauf = [{"role": "user", "content": "du " + X},
                   {"role": "assistant", "content": [{"type": "text", "text": "a" + X}, {"type": "image"}]}]
        Z.verlauf_putzen(verlauf)
        self.assertEqual("du " + X, verlauf[0]["content"])           # was der Nutzer schreibt, bleibt
        self.assertEqual([{"type": "text", "text": "a"}, {"type": "image"}], verlauf[1]["content"])

    def test_quelltext_ohne_das_zeichen(self):
        self.assertNotIn(X, (ROOT / "core" / "zeichensperre.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
