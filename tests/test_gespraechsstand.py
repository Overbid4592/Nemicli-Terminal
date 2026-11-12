"""Stand des Gesprächs (engines/gespraechsstand.py): feste Abschnitte + wörtliche Nachrichten, lokales Ollama
räumt damit auf (Hintergrund, Notfall, /compact), der echte Ollama-Kontext gilt, der Stand wird mit dem Chat
gespeichert – ohne Modell.

python -m unittest tests.test_gespraechsstand
"""

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import chatstore                                       # noqa: E402
import cloud                                           # noqa: E402
import gespraechsstand as GS                           # noqa: E402
import gguflokal as G                                  # noqa: E402
import persona                                         # noqa: E402,F401  (vor den Patches laden)
import pricing                                         # noqa: E402
import providers as P                                  # noqa: E402


class Bausteine(unittest.TestCase):

    def test_zitate_nur_eigene_nachrichten_gekuerzt(self):
        weg = [{"role": "user", "content": "Bitte   die Datei\nanlegen"},
               {"role": "assistant", "content": "Mache ich"},
               {"role": "user", "content": "Ergebnis von 'datei_schreiben' (ok):\nfertig"},
               {"role": "user", "content": "[System] Hinweis"},
               {"role": "user", "content": [{"type": "text", "text": "Schau das Bild an"},
                                            {"type": "image_url", "image_url": {"url": "data:x"}}]},
               {"role": "user", "content": "x" * 500}]
        z = GS.zitate(weg)
        self.assertEqual(z[:2], ["Bitte die Datei anlegen", "Schau das Bild an"])
        self.assertEqual(len(z), 3)
        self.assertEqual(len(z[2]), GS.ZITAT_LAENGE)
        self.assertTrue(z[2].endswith("…"))

    def test_zitate_zusammen_deckel_und_doppelte(self):
        alle = GS.zitate_zusammen(["a", "b"], ["b", "c"])
        self.assertEqual(alle, ["a", "b", "c"])
        viele = GS.zitate_zusammen([f"{i:03d} " + "w" * 100 for i in range(30)], ["neu"])
        self.assertLessEqual(sum(len(z) + 3 for z in viele), GS.ZITATE_MAX)
        self.assertEqual(viele[-1], "neu")                 # die ältesten fallen zuerst

    def test_bereinigen_und_teilen(self):
        antwort = ("<think>erst überlegen</think>## Ziel\n- testen\n\n" + GS.ZITATE_KOPF + "\n- erfunden")
        self.assertEqual(GS.bereinigen(antwort), "## Ziel\n- testen")
        stand = GS.zusammensetzen("## Ziel\n- testen", ["Hallo", "Mach weiter"])
        self.assertEqual(GS.teilen(stand), ("## Ziel\n- testen", ["Hallo", "Mach weiter"]))
        self.assertEqual(GS.teilen("- nur alt"), ("- nur alt", []))
        self.assertEqual(GS.vorsatz(""), "")
        self.assertTrue(GS.vorsatz(stand).startswith(GS.MARKE))

    def test_fortschreiben_behaelt_alte_zitate_und_gibt_nur_den_alten_text(self):
        gesehen = []

        async def fragen(system, text):
            gesehen.append((system, text))
            return "## Offen\n- weiter"
        alt = GS.zusammensetzen("## Ziel\n- alt", ["erste Bitte"])
        neu = asyncio.run(GS.fortschreiben(fragen, alt, [{"role": "user", "content": "zweite Bitte"},
                                                         {"role": "assistant", "content": "ok"}], 10_000))
        self.assertEqual(GS.teilen(neu), ("## Offen\n- weiter", ["erste Bitte", "zweite Bitte"]))
        system, text = gesehen[0]
        self.assertEqual(system, GS.SYSTEM)
        self.assertIn("Bisheriger Stand:\n## Ziel\n- alt", text)
        self.assertNotIn(GS.ZITATE_KOPF, text)             # die Zitate führt NemiCLI selbst

        async def leer(system, text):
            return "<think>nur gedacht</think>"
        self.assertIsNone(asyncio.run(GS.fortschreiben(leer, alt, [{"role": "user", "content": "x"}], 10_000)))
        self.assertLessEqual(len(GS.zusammensetzen("z" * 9000, ["a" * 200] * 20)), GS.MAX_ZEICHEN)

    def test_abschnitte_in_der_anleitung(self):
        for kopf in ("## Ziel", "## Offen", "## Fehler & Lösungen", "## Zuletzt & nächster Schritt", "## Fakten"):
            self.assertIn(kopf, GS.SYSTEM)


class _Completions:
    def __init__(self):
        self.calls = []
        self.zusammenfassung = "## Ziel\n- testen"
        self.fehler = None

    async def create(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            async def stuecke():
                yield NS(usage=None, choices=[NS(finish_reason="stop", delta=NS(content="Antwort",
                                                                                 reasoning_content=None,
                                                                                 reasoning=None))])
            return stuecke()
        if self.fehler:
            raise self.fehler
        return NS(choices=[NS(message=NS(content=self.zusammenfassung))])


def _verlauf(n, laenge):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Nachricht {i} " + "x" * laenge}
            for i in range(n)]


class OllamaStand(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.completions = _Completions()

        async def system(*a, **k):
            return "SYS"
        self.anleitung = mock.patch("persona.build_system_prompt_async", side_effect=system)
        self.treffer = mock.patch("persona.gedaechtnis_treffer", return_value="")
        for p in (self.anleitung, self.treffer, mock.patch.object(P, "ollama_kontext", return_value=4096),
                  mock.patch.object(GS, "an", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(pricing._GEMESSEN.clear)
        self.c = cloud.CloudChat("ollama", "testmodell")
        self.c.client = NS(chat=NS(completions=self.completions))

    async def _runde(self, text="Neu"):
        return [e async for e in self.c.stream(text)]

    def _gesendet(self):
        return [k["messages"] for k in self.completions.calls if k.get("stream")]

    async def test_hintergrund_fasst_zusammen_und_naechste_nachricht_traegt_den_stand(self):
        self.c.messages = _verlauf(10, 720)                # ~1.800 Token: über 3/4 des Budgets (2.252)
        await self._runde()
        self.assertEqual(pricing.context_window(self.c.model), 4096)   # Ollamas Kontext gilt, nicht 128k
        self.assertIsNotNone(self.c._puffer)
        notizen = " ".join(e["text"] for e in await self._runde("Noch was") if e["type"] == "note")
        self.assertIn("im Hintergrund aufgeräumt", notizen)
        self.assertEqual(GS.teilen(self.c.stand)[0], "## Ziel\n- testen")
        self.assertIn("Nachricht 0 x", GS.teilen(self.c.stand)[1][0])
        self.assertLess(len(self.c.messages), 12)
        self.assertGreater(len(self.c.messages), 3)
        zweite = self._gesendet()[1]
        self.assertEqual(zweite[0], {"role": "system", "content": "SYS"})
        self.assertTrue(zweite[1]["content"].startswith(GS.MARKE + "## Ziel"))
        self.assertFalse(self.c.messages[0]["content"].startswith(GS.MARKE))   # nur gesendet, nicht gespeichert
        ruf = next(k for k in self.completions.calls if not k.get("stream"))
        self.assertEqual(ruf["extra_body"], {"think": False})
        self.assertEqual(len(pricing.weggefallen_holen(self.c)), 12 - len(self.c.messages) + 2)

    async def test_voll_beim_senden_erst_der_stand(self):
        self.c.messages = _verlauf(30, 300)                # ~2.300 Token: über dem Budget
        notizen = " ".join(e["text"] for e in await self._runde() if e["type"] == "note")
        self.assertIn("Kontext voll", notizen)
        self.assertIn("stehen jetzt im Stand", notizen)
        self.assertNotIn("🧹", notizen)
        self.assertTrue(self.c.stand)
        self.assertGreater(len(self.c.messages), pricing.MIN_KEEP + 4)   # nicht nur die letzten drei

    async def test_zusammenfassung_scheitert_dann_wie_bisher(self):
        self.completions.fehler = RuntimeError("Ollama weg")
        self.c.messages = _verlauf(16, 800)
        await self._runde()
        self.assertEqual(self.c.stand, "")
        self.assertEqual(len(self.c.messages), pricing.MIN_KEEP + 1)   # Notbremse kuerzen + Antwort

    async def test_kompakt(self):
        self.c.messages = _verlauf(10, 800)
        text = await self.c.kompakt()
        self.assertIn("Zusammengefasst", text)
        self.assertEqual(len(self.c.messages), 2)
        self.assertEqual(len(GS.teilen(self.c.stand)[1]), 4)
        self.assertEqual(await self.c.kompakt(), "")       # zu kurz: nichts zu tun
        self.c.messages = _verlauf(10, 800)
        self.completions.fehler = RuntimeError("Ollama weg")
        with self.assertRaises(RuntimeError):
            await self.c.kompakt()
        self.assertEqual(len(self.c.messages), 10)         # Verlauf bleibt, wie er war

    async def test_andere_anbieter_raeumen_nicht_selbst_auf(self):
        self.c._lokal = False
        self.c.messages = _verlauf(10, 720)
        await self._runde()
        self.assertIsNone(self.c._puffer)
        self.c.stand = "## Ziel\n- mitgenommen"            # mitgebracht vom vorigen Modell: wird gesendet
        await self._runde()
        self.assertTrue(self._gesendet()[-1][1]["content"].startswith(GS.MARKE))

    async def test_kompakte_anleitung_treffer_an_der_nachricht(self):
        persona.gedaechtnis_treffer.return_value = "# Gedächtnis\n- Der Hund heißt Bello"
        await self._runde("Wie heißt eigentlich mein Hund?")
        self.assertEqual(mock.call("", kompakt_fuer_lokal=True, mit_gedaechtnis=False),
                         persona.build_system_prompt_async.call_args)          # gleich jede Runde: Präfix-Cache
        gesendet = self._gesendet()[0][1]["content"]
        self.assertTrue(gesendet.startswith("# Gedächtnis\n- Der Hund heißt Bello"))
        self.assertTrue(gesendet.endswith(GS.NACHRICHT_MARKE + "Wie heißt eigentlich mein Hund?"))
        self.assertEqual("Wie heißt eigentlich mein Hund?", self.c.messages[0]["content"])   # gespeichert: nur sie
        await self._runde("Ergebnis von 'datei_lesen' (ok):\nWas steht da? Viel Text hier")
        self.assertEqual(1, persona.gedaechtnis_treffer.call_count)            # Ergebnisse suchen nicht
        self.assertTrue(self._gesendet()[1][1]["content"].startswith("# Gedächtnis"))   # die erste bleibt, wie sie war

    async def test_anleitung_nur_einmal_nachgereicht(self):
        marke = GS.ANLEITUNG_MARKE.format("internet")
        await self._runde("Such mal im Internet nach dem Wetter")
        self.assertIn(marke, self._gesendet()[0][1]["content"])
        await self._runde("Ergebnis von 'web_suche' (ok):\nSonne")
        self.assertEqual(1, sum(m["content"].count(marke) for m in self._gesendet()[1] if m["role"] == "user"))

    async def test_cloud_ueber_ollama_bleibt_wie_cloud(self):
        c = cloud.CloudChat("ollama", "kimi-k2.5:cloud")
        c.client = NS(chat=NS(completions=self.completions))
        self.assertFalse(c._lokal)
        _ = [e async for e in c.stream("Wie heißt eigentlich mein Hund?")]
        self.assertEqual(mock.call("Wie heißt eigentlich mein Hund?", kompakt_fuer_lokal=False, mit_gedaechtnis=True),
                         persona.build_system_prompt_async.call_args)
        persona.gedaechtnis_treffer.assert_not_called()
        self.assertIsNone(c._puffer)

    async def test_reset_vergisst_den_stand(self):
        self.c.stand = "x"
        self.c.reset()
        self.assertEqual(self.c.stand, "")


class OllamaKontext(unittest.TestCase):

    def _antwort(self, modelle):
        return NS(raise_for_status=lambda: None, json=lambda: {"models": modelle})

    def test_kontext_aus_api_ps(self):
        geladen = [{"name": "lfm:latest", "model": "lfm:latest", "context_length": 65536}]
        with mock.patch.object(P.httpx, "get", return_value=self._antwort(geladen)):
            self.assertEqual(P.ollama_kontext("lfm"), 65536)
            self.assertEqual(P.ollama_kontext("lfm:latest"), 65536)
            self.assertIsNone(P.ollama_kontext("anderes"))
        with mock.patch.object(P.httpx, "get", side_effect=OSError("aus")):
            self.assertIsNone(P.ollama_kontext("lfm"))

    def test_gemessen_vor_richtwert(self):
        self.addCleanup(pricing._GEMESSEN.clear)
        self.assertEqual(pricing.context_window("ollama:qwen-test"), 131_072)
        pricing.kontext_merken("ollama:qwen-test", 32768)
        self.assertEqual(pricing.context_window("ollama:qwen-test"), 32768)


class StandImChat(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(chatstore, "CHATS_DIR", self.tmp / "chats")
        p.start()
        self.addCleanup(p.stop)

    def test_speichern_und_laden(self):
        verlauf = [{"role": "user", "content": "Hallo"}, {"role": "assistant", "content": "Hi"}]
        chatstore.save(1, verlauf, ["Hallo"], "m")
        self.assertEqual(chatstore.load(1)["stand"], "")
        chatstore.save(1, verlauf, ["Hallo"], "m", stand="## Ziel\n- x")
        chatstore.save(1, verlauf, ["Hallo"], "m", stand=None)        # ohne Stand: der alte bleibt
        chatstore.save(1, verlauf, ["Hallo"], "m", stand="")
        self.assertEqual(chatstore.load(1)["stand"], "## Ziel\n- x")
        chatstore.save(2, verlauf, ["Hallo"], "m")
        self.assertEqual(chatstore.load(2)["stand"], "")
        self.assertEqual(chatstore.load(1)["messages"], verlauf)


class EigenerMotor(unittest.TestCase):

    def test_gleiche_anleitung_wie_ollama(self):
        self.assertIs(G.STAND_SYSTEM, GS.SYSTEM)
        self.assertIs(G.STAND_MARKE, GS.MARKE)


if __name__ == "__main__":
    unittest.main()
