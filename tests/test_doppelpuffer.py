"""Doppelpuffer (Preview): nach einer Antwort ab 50 % Kontext im Hintergrund aufräumen und den neuen
Kontext vorlesen – die nächste Nachricht liest nur sich selbst."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import gguflokal as G
import gespraechsstand as GS          # noqa: E402


class _Format:
    """Ein Token je Wort (Wortlänge als Nummer), jede Nachricht 2 Token Rahmen; Antwortkopf 999."""
    drops_old_thinking = False
    fmt, opens_thinking, thinking = "chatml", False, False

    def encode(self, nachrichten, add_generation_prompt=True):
        ids = []
        for m in nachrichten:
            ids += [1, {"system": 2, "user": 3, "assistant": 4}[m.role]]
            ids += [10 + len(w) for w in str(m.content).split()]
        return ids + ([999] if add_generation_prompt else [])


class Vorlesen(unittest.TestCase):
    """Engine.vorlesen: danach liest der nächste Chat nur seine neue Nachricht."""

    def _engine(self):
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.chat_format = _Format()
        e.model = NS(resume_from=lambda c, n: c, checkpoint_state=lambda: None, reset=lambda: None)
        e._rope_state = (0, 0)
        e._pos, e._history, e._own, e._keys = 0, [], [], []
        e._checkpoint = e._answer_checkpoint = None
        e._mrope, e._images = False, {}
        e.gelesen = []

        def feed(tokens, keys=None, own=None, chunk=None, abbruch=None):
            e.gelesen.append(len(tokens))
            e._pos += len(tokens)
            e._history.extend(tokens)
            e._own.extend(own or [False] * len(tokens))
            e._keys.extend(keys if keys is not None else tokens)
        e.feed = feed
        return e

    def test_naechste_nachricht_liest_nur_sich(self):
        from ggufengine.chat import Message
        e = self._engine()
        verlauf = [Message("system", "du bist hilfreich"), Message("user", "erste frage hier"),
                   Message("assistant", "eine antwort darauf")]
        gelesen = e.vorlesen(verlauf)
        self.assertEqual(gelesen, len(_Format().encode(verlauf, add_generation_prompt=False)))
        self.assertIsNotNone(e._checkpoint)                     # Stand nach der Anleitung gemerkt
        naechste = verlauf + [Message("user", "zweite frage")]
        ids = _Format().encode(naechste)
        self.assertEqual(e._resume(ids), gelesen)               # alles bis zur neuen Nachricht im Speicher


class Abbruch(unittest.TestCase):
    """Engine.feed/vorlesen hören zwischen zwei Stücken auf; nur das Gelesene zählt."""

    def _engine(self):
        import torch
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.chat_format = _Format()
        e.device = torch.device("cpu")
        e.model = NS(forward=lambda part, pos: None, prefill_chunk=4, resume_from=lambda c, n: c,
                     checkpoint_state=lambda: None, reset=lambda: None)
        e._pos, e._history, e._own, e._keys = 0, [], [], []
        e._checkpoint = e._answer_checkpoint = None
        e._mrope, e._images, e._moved, e._rope_state = False, {}, None, (0, 0)
        e._kv_room = lambda n: None
        return e

    def test_feed_bricht_zwischen_stuecken_ab(self):
        e = self._engine()
        stop = [False]
        e.feed(list(range(10)), abbruch=lambda: stop[0])
        self.assertEqual(len(e._history), 10)
        stop[0] = True
        e.feed(list(range(10, 20)), abbruch=lambda: stop[0])
        self.assertEqual((len(e._history), e._pos, len(e._keys)), (10, 10, 10))

    def test_vorlesen_bricht_ab_ohne_checkpoint(self):
        from ggufengine.chat import Message
        e = self._engine()
        verlauf = [Message("system", "du bist hilfreich und nett"), Message("user", "erste frage hier")]
        self.assertEqual(e.vorlesen(verlauf, abbruch=lambda: True), 0)
        self.assertIsNone(e._checkpoint)
        self.assertEqual(e.vorlesen(verlauf), len(_Format().encode(verlauf, add_generation_prompt=False)))
        self.assertIsNotNone(e._checkpoint)


class _Motor:
    pass


def _engine(pos=5000):
    engine = _Motor()
    engine.__dict__.update(n_ctx=8192, chat_format=_Format(), tokenizer=NS(encode=lambda t: t.split()),
                           vision=None, last_ids=[1, 2], _pos=pos, last_cached=0, checkpoint_new=False,
                           kv_im_vram=None, offloaded=False)
    engine.vorgelesen = []

    def vorlesen(nachrichten, abbruch=None):
        engine.vorgelesen.append(list(nachrichten))
        engine._pos = len(engine.chat_format.encode(nachrichten, add_generation_prompt=False))
        return engine._pos
    engine.vorlesen = vorlesen
    return engine


class Hintergrund(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        self.c = G.GgufChat("Test")
        for i in range(8):
            self.c.messages += [{"role": "user", "content": f"Frage{i} " + "wortlang " * 120},
                                {"role": "assistant", "content": f"Antwort{i} " + "wortlang " * 120}]
        self.gesendet = []

        async def einmal(system, verlauf, max_tokens=2048, denken_aus=False, hintergrund=False):
            if system == G.STAND_SYSTEM:
                self.assertTrue(hintergrund)                           # der Stand im Hintergrund: nie nachladen
            return "- Ziel: viele Fragen beantworten"

        async def erzeugen(engine_, nachrichten, max_tokens, abbruch, denken_aus=False, **_):
            self.gesendet.append(list(nachrichten))
            yield "Hallo zurück"

        async def system_prompt(*a, **k):
            return "system " * 50

        async def laden():
            return self.engine
        self.patches = [mock.patch.object(self.c, "_laden", side_effect=laden),
                        mock.patch.object(self.c, "_einmal", side_effect=einmal),
                        mock.patch.object(self.c, "_erzeugen", side_effect=erzeugen),
                        mock.patch("persona.build_system_prompt_async", side_effect=system_prompt),
                        mock.patch.object(G, "_gedaechtnis", return_value=""),
                        mock.patch.object(G, "prefix_laden", return_value=False),
                        mock.patch.object(G, "kontext", return_value=8192),
                        mock.patch.object(G, "_motor", ("datei", None, self.engine)),
                        mock.patch.object(G, "modelle", return_value={"Test": "datei"}),
                        mock.patch.object(G, "denken", return_value=False)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def zwei_runden(self):
        async def lauf():
            erste = [e async for e in self.c.stream("Neue Frage")]
            zweite = [e async for e in self.c.stream("Noch eine Frage")]
            if self.c._puffer is not None:
                await self.c._puffer
            return erste, zweite
        return asyncio.run(lauf())

    def test_aufraeumen_und_vorlesen_ab_der_haelfte(self):
        _, zweite = self.zwei_runden()
        notizen = " ".join(e["text"] for e in zweite if e["type"] == "note")
        self.assertIn("im Hintergrund aufgeräumt", notizen)
        self.assertEqual(GS.teilen(self.c.stand)[0], "- Ziel: viele Fragen beantworten")
        self.assertTrue(self.engine.vorgelesen)
        vorgelesen = self.engine.vorgelesen[0]
        # die zweite Runde beginnt genau mit dem, was vorgelesen wurde: nur die neue Nachricht ist neu
        self.assertEqual(self.gesendet[1][:len(vorgelesen)], vorgelesen)
        self.assertEqual(len(self.gesendet[1]), len(vorgelesen) + 1)
        self.assertTrue(vorgelesen[1].content.startswith(G.STAND_MARKE))
        behalten = sum(len(m["content"]) // 4 for m in self.c.messages[:-2])
        self.assertLessEqual(behalten, int(G.PUFFER_BEHALTEN * 8192) + 400)
        self.assertNotIn("Frage0 ", " ".join(m["content"] for m in self.c.messages))

    def test_kompakt_behaelt_nur_die_neuesten(self):
        text = asyncio.run(self.c.kompakt())
        self.assertIn("Zusammengefasst", text)
        self.assertEqual(len(self.c.messages), 2)
        self.assertEqual(GS.teilen(self.c.stand)[0], "- Ziel: viele Fragen beantworten")
        zitate = GS.teilen(self.c.stand)[1]                           # wörtlich (gekürzt), die ältesten fallen
        self.assertEqual([z.split()[0] for z in zitate], ["Frage2", "Frage3", "Frage4", "Frage5", "Frage6"])
        self.assertEqual(self.c._puffer_hinweis, "")                  # kein Hintergrund-Hinweis hinterher

    def test_kompakt_fehler_laesst_den_verlauf(self):
        async def kaputt(*a, **k):
            raise RuntimeError("Motor weg")
        with mock.patch.object(self.c, "_einmal", side_effect=kaputt):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.c.kompakt())
        self.assertEqual(len(self.c.messages), 16)
        self.assertEqual(self.c.stand, "")

    def test_unter_der_haelfte_nur_vorlesen(self):
        self.engine._pos = 3000
        self.c.messages = self.c.messages[-2:]
        _, zweite = self.zwei_runden()
        self.assertEqual(self.c.stand, "")
        self.assertEqual(len(self.engine.vorgelesen), 2)          # nach jeder Antwort vorgelesen

    def test_platz_zaehlt_ab_der_anleitung(self):
        anleitung, platz = self.c._platz(self.engine, "system " * 50)
        self.assertGreater(anleitung, 50)
        self.assertEqual(platz, 8192 - anleitung - G.antwort_reserve(8192))

    def test_vorbereiten_raeumt_gleich_auf(self):
        async def lauf():
            fertig = []
            gestartet = self.c.vorbereiten(fertig=lambda: fertig.append(True))
            await self.c._puffer
            return gestartet, fertig
        gestartet, fertig = asyncio.run(lauf())
        self.assertTrue(gestartet)
        self.assertEqual(fertig, [True])
        self.assertEqual(GS.teilen(self.c.stand)[0], "- Ziel: viele Fragen beantworten")
        self.assertTrue(self.engine.vorgelesen)
        self.assertNotIn("Frage0 ", " ".join(m["content"] for m in self.c.messages))

    def test_vorbereiten_nicht_bei_belegter_grafikkarte(self):
        import hintergrund

        async def lauf():
            with mock.patch.object(hintergrund, "gpu_belegt", return_value={"tool": "video_erstellen"}):
                return self.c.vorbereiten()
        self.assertFalse(asyncio.run(lauf()))
        self.assertIsNone(self.c._puffer)

    def test_nach_einer_nebenfrage_wieder_vorgelesen(self):
        async def lauf():
            [e async for e in self.c.stream("Neue Frage")]
            await self.c._puffer
            anzahl = len(self.engine.vorgelesen)
            await self.c.ask_once("Was bleibt?", "Reflexion")
            await self.c._puffer
            return anzahl, len(self.engine.vorgelesen)
        vorher, nachher = asyncio.run(lauf())
        self.assertEqual(nachher, vorher + 1)

    def test_entladen_laedt_nichts_neu(self):
        async def lauf():
            [e async for e in self.c.stream("Neue Frage")]
            geladen = self.c._laden.call_count
            with mock.patch.object(G, "_motor", None):           # z. B. Wechsel zur Cloud
                await self.c._puffer
            return geladen, self.c._laden.call_count
        vorher, nachher = asyncio.run(lauf())
        self.assertEqual(nachher, vorher)
        self.assertEqual(self.engine.vorgelesen, [])
        self.assertEqual(self.c.stand, "")

    def test_waehrend_ein_bild_entsteht_nichts(self):
        with mock.patch.object(G, "_bild_tiefe", 1):
            self.zwei_runden()
        self.assertEqual(self.engine.vorgelesen, [])
        self.assertEqual(len(self.c.messages), 16 + 4)                 # nichts abgeschnitten

    def test_ausgelagertes_modell_nicht_zurueckholen(self):
        self.engine.offloaded = True                                   # liegt im RAM (Bild in Arbeit)
        self.zwei_runden()
        self.assertEqual(self.engine.vorgelesen, [])

    def test_erst_der_stand_dann_der_schnitt(self):
        self.c._einmal.side_effect = RuntimeError("Modell inzwischen entladen")
        _, zweite = self.zwei_runden()
        self.assertEqual(self.c.stand, "")
        self.assertEqual(len(self.c.messages), 16 + 4)                 # ohne Stand kein Schnitt
        self.assertNotIn("aufgeräumt", " ".join(e["text"] for e in zweite if e["type"] == "note"))

    def test_einmal_im_hintergrund_laedt_nie_und_merkt_entladen(self):
        c = G.GgufChat("Test")

        async def erzeugen(engine_, nachrichten, max_tokens, abbruch, denken_aus=False, hintergrund=False, **_):
            self.assertTrue(hintergrund)
            yield "Stand"
            G._motor = None                                            # entladen, während es läuft
        with mock.patch.object(c, "_erzeugen", side_effect=erzeugen), \
                mock.patch.object(c, "_laden", side_effect=AssertionError("darf nicht laden")):
            with mock.patch.object(G, "_motor", None):
                with self.assertRaises(RuntimeError):
                    asyncio.run(c._einmal("sys", [{"role": "user", "content": "x"}], hintergrund=True))
            with mock.patch.object(G, "_motor", ("datei", G._schluessel("Test"), self.engine)):
                with self.assertRaises(RuntimeError):                   # Ergebnis nach dem Entladen ungültig
                    asyncio.run(c._einmal("sys", [{"role": "user", "content": "x"}], hintergrund=True))

    def test_schalter_aus(self):
        with mock.patch.object(G, "_config", return_value={"gguf_doppelpuffer": False}):
            self.zwei_runden()
        self.assertEqual(self.engine.vorgelesen, [])
        self.assertEqual(self.c.stand, "")


if __name__ == "__main__":
    unittest.main()
