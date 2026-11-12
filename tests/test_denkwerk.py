"""Denkwerk: Weiche, Denk-Hinweis nur in der gesendeten Nachricht, <think> als Denktext, Prüfer – ohne Modell.

python -m unittest tests.test_denkwerk
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import cloud                                           # noqa: E402
import denkwerk as D                                   # noqa: E402
import gguflokal as G                                  # noqa: E402
import persona                                         # noqa: E402,F401  (vor den Patches laden)
import pricing                                         # noqa: E402
import providers as P                                  # noqa: E402


class Weiche(unittest.TestCase):

    def test_einfach(self):
        for text in ("hallo", "Danke dir!", "Hey <3", "Wie spät ist es?", "ok", ""):
            self.assertEqual("einfach", D.einschaetzen(text), text)

    def test_mittel(self):
        for text in ("Erkläre mir, warum der Himmel blau ist", "Was ist 17 * 23?",
                     "Ich war heute im Park und habe viele Enten gesehen, eine davon hatte nur ein Bein, "
                     "das fand ich seltsam, aber sie schwamm ganz normal. Ist das bei Enten häufig so?"):
            self.assertEqual("mittel", D.einschaetzen(text), text)

    def test_schwer(self):
        for text in ("Berechne 17*23 und erkläre den Rechenweg",
                     "Schreib ein Python-Skript, das Dateien sortiert, und erkläre warum es so schnell ist",
                     "Wie plane ich den Umzug? Was kostet er? Wer hilft mir?",
                     "Analysiere die Strategie und vergleiche sie genau"):
            self.assertEqual("schwer", D.einschaetzen(text), text)

    def test_wunsch_geht_vor(self):
        self.assertEqual("schwer", D.einschaetzen("Denk gründlich nach: hallo"))
        self.assertEqual("schwer", D.einschaetzen("überleg mal ganz genau"))
        self.assertEqual("einfach", D.einschaetzen("Kurz und knapp: berechne 17*23 und erkläre warum"))
        self.assertEqual("einfach", D.einschaetzen("Denk nicht so lange nach, analysiere den Code"))

    def test_aus(self):
        with mock.patch.object(D, "an", return_value=False):
            self.assertIsNone(D.runde("Berechne 17*23 und erkläre den Rechenweg"))
        with mock.patch.object(D, "an", return_value=True):
            self.assertEqual("schwer", D.runde("Berechne 17*23 und erkläre den Rechenweg").stufe)


class Hinweis(unittest.TestCase):

    def test_nach_stufe_und_modell(self):
        self.assertEqual("", D.hinweis(D.Runde("hi", "einfach"), False))
        self.assertEqual("", D.hinweis(None, False))
        mittel = D.hinweis(D.Runde("x", "mittel"), False)
        self.assertTrue(mittel.startswith(D.MARKE))
        self.assertIn("<think>", mittel)
        self.assertNotIn("Lösungswege", mittel)
        schwer = D.hinweis(D.Runde("x", "schwer"), False)
        self.assertIn("Lösungswege", schwer)
        self.assertIn("Aktionsblöcke", schwer)
        selbst = D.hinweis(D.Runde("x", "schwer"), True)
        self.assertNotIn("<think>", selbst)                 # eigenes Nachdenken: nur die Vorgehensweise
        self.assertIn("Lösungswege", selbst)


class Pruefer(unittest.IsolatedAsyncioTestCase):

    def test_befund_lesen(self):
        self.assertIsNone(D.befund_lesen("OK"))
        self.assertIsNone(D.befund_lesen("ok."))
        self.assertIsNone(D.befund_lesen("<think>hm, FEHLER: vielleicht</think>OK"))
        self.assertIsNone(D.befund_lesen("Sieht gut aus."))   # nicht lesbar: gilt als bestanden
        self.assertIsNone(D.befund_lesen("Sieht gut aus. Fehler: keine"))
        self.assertEqual("- 2+2 ist 4", D.befund_lesen("FEHLER:\n- 2+2 ist 4"))

    def test_urteil_in_der_letzten_zeile(self):
        # wie Gemma 4 12B es schrieb: erst die Prüfung, dann das Urteil – die Punkte auch in eigenen Zeilen
        self.assertIsNone(D.befund_lesen("17*23 = 391 stimmt, 48/6 = 8 stimmt.\nURTEIL: OK"))
        self.assertEqual("17*23 ist 391, nicht 381.", D.befund_lesen(
            "Nachgerechnet: 17*23 = 391.\nURTEIL: FEHLER: 17*23 ist 391, nicht 381."))
        self.assertEqual("1. 17*23 ist 391.\n2. Das Endergebnis ist 399, nicht 389.", D.befund_lesen(
            "Prüfung …\nURTEIL: FEHLER:\n1. 17*23 ist 391.\n2. Das Endergebnis ist 399, nicht 389."))
        self.assertIsNone(D.befund_lesen("Ein Urteil: zuerst FEHLER vermutet, dann doch richtig.\nURTEIL: OK"))

    def test_wann_geprueft_wird(self):
        lang = "x" * D.PRUEF_MIN_ZEICHEN
        self.assertTrue(D.pruefen_noetig(D.Runde("f", "schwer"), lang))
        self.assertFalse(D.pruefen_noetig(D.Runde("f", "mittel"), lang))
        self.assertFalse(D.pruefen_noetig(D.Runde("f", "schwer"), "kurz"))
        self.assertFalse(D.pruefen_noetig(D.Runde("f", "schwer", geprueft=True), lang))
        self.assertFalse(D.pruefen_noetig(None, lang))

    async def test_pruefen(self):
        gesehen = []

        async def ask_once(prompt, system):
            gesehen.append((prompt, system))
            return "FEHLER: 17 * 23 ist 391"
        r = D.Runde("Was ist 17 * 23?", "schwer")
        self.assertEqual("17 * 23 ist 391", await D.pruefen(NS(ask_once=ask_once), r, "17 * 23 = 381"))
        self.assertTrue(r.geprueft)
        self.assertIn("FRAGE:\nWas ist 17 * 23?", gesehen[0][0])
        self.assertIn("ANTWORT:\n17 * 23 = 381", gesehen[0][0])
        self.assertEqual(D.PRUEF_SYSTEM, gesehen[0][1])

    async def test_scheitern_gilt_als_bestanden(self):
        async def kaputt(prompt, system):
            raise RuntimeError("weg")
        self.assertIsNone(await D.pruefen(NS(ask_once=kaputt), D.Runde("f", "schwer"), "a"))

    def test_nachbessern_und_kurz(self):
        text = D.nachbessern("- erster Punkt\n- zweiter")
        self.assertTrue(text.startswith("[System] ⚖️"))
        self.assertIn("zweiter", text)
        self.assertEqual("erster Punkt", D.kurz("- erster Punkt\n- zweiter"))


class _Completions:
    def __init__(self, inhalt="Antwort", denken=None):
        self.calls, self.inhalt, self.denken = [], inhalt, denken

    async def create(self, **kw):
        self.calls.append(kw)

        async def stuecke():
            yield NS(usage=None, choices=[NS(finish_reason="stop",
                                             delta=NS(content=self.inhalt, reasoning_content=self.denken,
                                                      reasoning=None))])
        return stuecke()


class Cloud(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        async def system(*a, **k):
            return "SYS"
        for p in (mock.patch("persona.build_system_prompt_async", side_effect=system),
                  mock.patch("persona.gedaechtnis_treffer", return_value=""),
                  mock.patch.object(P, "ollama_kontext", return_value=32768)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(pricing._GEMESSEN.clear)

    def _chat(self, model="testmodell", **kw):
        c = cloud.CloudChat("ollama", model)
        c.client = NS(chat=NS(completions=_Completions(**kw)))
        return c

    async def test_hinweis_nur_gesendet_und_think_als_denktext(self):
        c = self._chat(inhalt="<think>17 mal 23 …</think>391")
        self.assertFalse(c.denkt_selbst)
        c.denk_hinweis = D.hinweis(D.Runde("x", "schwer"), c.denkt_selbst)
        ev = [e async for e in c.stream("Berechne 17*23")]
        gesendet = c.client.chat.completions.calls[0]["messages"][-1]["content"]
        self.assertTrue(gesendet.startswith("Berechne 17*23"))
        self.assertTrue(gesendet.endswith(D.hinweis(D.Runde("x", "schwer"), False)))   # dahinter
        self.assertEqual("Berechne 17*23", c.messages[0]["content"])        # gespeichert: nur die Nachricht
        self.assertEqual("", c.denk_hinweis)                                 # gilt nur einmal
        self.assertEqual("17 mal 23 …", "".join(e["text"] for e in ev if e["type"] == "thinking"))
        self.assertEqual("391", c.messages[-1]["content"])
        self.assertFalse(c.denkt_selbst)                                     # erbetenes Nachdenken zählt nicht
        [e async for e in c.stream("Danke")]
        self.assertIn(D.MARKE, c.client.chat.completions.calls[1]["messages"][1]["content"])
        self.assertEqual("Danke", c.client.chat.completions.calls[1]["messages"][-1]["content"])

    async def test_eigenes_nachdenken_wird_erkannt(self):
        c = self._chat(denken="hm")
        [e async for e in c.stream("Hallo")]
        self.assertTrue(c.denkt_selbst)
        unaufgefordert = self._chat(inhalt="<think>hm</think>Hallo")
        [e async for e in unaufgefordert.stream("Hallo")]
        self.assertTrue(unaufgefordert.denkt_selbst)

    def test_bekannte_denkmodelle(self):
        self.assertTrue(cloud.CloudChat("ollama", "gpt-oss:20b").denkt_selbst)
        self.assertFalse(cloud.CloudChat("ollama", "gpt-oss:20b", "schnell").denkt_selbst)
        self.assertFalse(cloud.CloudChat("ollama", "testmodell").denkt_selbst)


class EigenerMotor(unittest.TestCase):

    def _alles(self, trenner, stuecke):
        raus = []
        for s in stuecke:
            raus += trenner(s)
        raus += trenner.ende()
        return ("".join(t for a, t in raus if a == "thinking"), "".join(t for a, t in raus if a == "text"))

    def test_gemma_trennt_auch_erbetenes_think(self):
        t = G._Kettentrenner(G._Denktrenner("gemma4"))
        d, text = self._alles(t, ["<|channel>thought\neigen<channel|>", "<thi", "nk>erbeten</th", "ink>Antwort"])
        self.assertEqual("eigenerbeten", d)
        self.assertEqual("Antwort", text)

    def test_trenner_je_format(self):
        fmt = NS(fmt="gemma4", opens_thinking=False, thinking=False)
        self.assertIsInstance(G._trenner(NS(chat_format=fmt), False), G._Kettentrenner)
        fmt.fmt = "chatml"
        self.assertIsInstance(G._trenner(NS(chat_format=fmt), False), G._Denktrenner)   # <think> ist schon die eigene Marke
        self.assertTrue(G._trenner(NS(chat_format=fmt), True) and fmt.thinking)

    def test_denkt_selbst_ist_die_entscheidung_der_runde(self):
        chat = G.GgufChat("test")
        self.assertFalse(chat.denkt_selbst)
        chat._denkt_jetzt = True
        self.assertTrue(chat.denkt_selbst)
        self.assertEqual("", chat.denk_hinweis)
        self.assertIs(G._KEINE_RUNDE, chat.denk_runde)


class Claude(unittest.TestCase):

    def test_hinweis_vor_der_letzten_nachricht(self):
        import chat
        verlauf = [{"role": "user", "content": "alt"}, {"role": "assistant", "content": "a"},
                   {"role": "user", "content": "neu"}]
        raus = chat._mit_hinweis(verlauf, "[Denkwerk] x")
        self.assertEqual("neu\n\n[Denkwerk] x", raus[-1]["content"])
        self.assertEqual("neu", verlauf[-1]["content"])                       # der Verlauf selbst bleibt
        bild = [{"role": "user", "content": [{"type": "text", "text": "sieh"}, {"type": "image"}]}]
        self.assertEqual("[Denkwerk] x", chat._mit_hinweis(bild, "[Denkwerk] x")[-1]["content"][-1]["text"])
        ergebnis = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}]
        self.assertIs(ergebnis, chat._mit_hinweis(ergebnis, "[Denkwerk] x"))
        self.assertIs(verlauf, chat._mit_hinweis(verlauf, ""))
        self.assertTrue(chat.Chat.denkt_selbst)


if __name__ == "__main__":
    unittest.main()
