"""Kontext lokaler Modelle (Einpassen, Vorgabe, Anzeige-Bilanz) und Kugel nach der Charakter-Datei."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import gguflokal as G
import gespraechsstand as GS          # noqa: E402
import kugelbilder as KB       # noqa: E402
import paths                   # noqa: E402
import pricing                 # noqa: E402
import profilordner            # noqa: E402


class _Format:
    """Ein Token je Wort; jede Nachricht kostet 2 Token Rahmen."""
    drops_old_thinking = False
    fmt, opens_thinking, thinking = "chatml", False, False

    def encode(self, nachrichten, add_generation_prompt=True):
        return [0] * sum(len(str(m.content).split()) + 2 for m in nachrichten)


def _engine(n_ctx):
    return NS(n_ctx=n_ctx, chat_format=_Format(), tokenizer=NS(encode=lambda t: [0] * len(t.split())))


class Einpassen(unittest.TestCase):
    def chat(self, *texte):
        c = G.GgufChat("Test")
        rollen = ["user", "assistant"]
        c.messages = [{"role": rollen[i % 2], "content": t} for i, t in enumerate(texte)]
        return c

    def test_aelteste_fallen_heraus_und_werden_gemerkt(self):
        c = self.chat(*(["wort " * 1500] * 6), "neue Frage")    # 6 × 1500 + System: passt nicht in 8k
        c._einpassen(_engine(8192), "system " * 500)
        self.assertEqual(c.messages[-1]["content"], "neue Frage")
        self.assertEqual(c.messages[0]["role"], "user")
        self.assertTrue(c.weggefallen)
        self.assertIn("aufgeräumt", pricing.aufraeum_hinweis(c))

    def test_passt_alles_bleibt_alles(self):
        c = self.chat("kurz", "auch kurz", "Frage")
        c._einpassen(_engine(25600), "system " * 500)
        self.assertEqual(len(c.messages), 3)

    def test_anleitung_zu_gross_klare_meldung(self):
        c = self.chat("Frage")
        with self.assertRaises(RuntimeError) as e:
            c._einpassen(_engine(4096), "system " * 4000)
        self.assertIn("/kontext", str(e.exception))

    def test_vorgabe_und_reserve(self):
        with mock.patch.object(G, "_config", return_value={}),                 mock.patch.object(G, "kontext_max", return_value=262144):
            self.assertEqual(G.kontext("modell"), 262144)               # Vorgabe max: so viel, wie das Modell kann
        self.assertNotIn(25600, G.KONTEXT_STUFEN)
        self.assertEqual(G.antwort_reserve(25600), 4266)
        self.assertEqual(G.antwort_reserve(4096), 2048)


class StimmeMachtPlatz(unittest.TestCase):
    def test_nur_wenn_geladen(self):
        st = NS(_modell=None, gpu_freigeben=mock.Mock())
        with mock.patch.dict(sys.modules, {"stimme": st}):
            self.assertIsNone(G._stimme_platz_machen())
            st.gpu_freigeben.assert_not_called()
            st._modell = object()
            with mock.patch.object(G, "_frei_vram", return_value=123):
                self.assertEqual(G._stimme_platz_machen(), 123)
            st.gpu_freigeben.assert_called_once()


class PromptBilanz(unittest.TestCase):
    def test_teile_ergeben_den_prompt(self):
        import persona
        teile = persona.prompt_bilanz()
        self.assertEqual([n for n, _ in teile],
                         ["Persönlichkeit", "Regeln & Werkzeuge", "Hinweise", "Gedächtnis", "Skills"])
        self.assertEqual(sum(len(t) for _, t in teile), len(persona.build_system_prompt("")))


class AnleitungAufAbruf(unittest.TestCase):
    def test_kurzfassung(self):
        import actions
        import debugbericht
        import persona
        with mock.patch.object(debugbericht, "cloud_modell", return_value=True):   # Cloud-Werkzeuge im Prompt,
            kurz = persona.build_system_prompt("", kompakt_fuer_lokal=True)          # unabhängig von der Config
            voll = persona.build_system_prompt("")
            bilanz = persona.prompt_bilanz(True)
        self.assertLess(len(kurz), len(voll) * 0.45)
        for name, spec in actions.ACTIONS.items():           # jedes Werkzeug bleibt bekannt
            if spec.get("entwickler"):                       # außer Entwickler-Werkzeuge: nur bei Bedarf im Prompt
                continue
            self.assertRegex(kurz, rf"\b{name}\b")
        self.assertIn("ZUERST coding_start", kurz)
        self.assertIn("- datei_bearbeiten (pfad, suchen, ersetzen)", kurz)
        self.assertNotIn("Tonlage: Frauenstimmen", kurz)     # Stimme kommt auf Abruf
        self.assertIn("Tonlage: Frauenstimmen", persona.anleitung("stimme"))
        self.assertEqual(sum(len(t) for _, t in bilanz), len(kurz))

    def test_themen_kommen_von_selbst(self):
        import persona
        self.assertIn("bilder", persona.passende_anleitungen("Mal mir ein Bild von dir am Strand"))
        self.assertIn("coding", persona.passende_anleitungen("Schreib mir ein Python-Skript"))
        self.assertIn("stimme", persona.passende_anleitungen("Lies mir das bitte vor"))
        self.assertNotIn("bilder", persona.passende_anleitungen("Wie geht es dir heute?"))
        self.assertEqual(persona.thema_fuer_werkzeug("bild_malen"), "bilder")
        with mock.patch.object(persona, "_bild_hinweis", return_value="\n# Malregeln\n- groß\n"):
            self.assertEqual(persona.anleitung("bilder"), "# Malregeln\n- groß")


class ErgebnisseUndStand(unittest.TestCase):
    def test_ergebnis_kurz(self):
        lang = "Ergebnis von 'web_lesen' (ok):\n" + "x" * 5000
        kurz = G._ergebnis_kurz(lang)
        self.assertLess(len(kurz), 700)
        self.assertIn("gekürzt", kurz)
        self.assertEqual(G._ergebnis_kurz("Ergebnis von 'a' (ok):\nkurz"), "Ergebnis von 'a' (ok):\nkurz")
        mit_anleitung = "📘 Anleitung\n" + "a" * 3000 + "\n\n" + G.NACHRICHT_MARKE + lang
        k2 = G._ergebnis_kurz(mit_anleitung)
        self.assertTrue(k2.startswith("📘 Anleitung\n" + "a" * 3000))   # Anleitung bleibt ganz

    def test_nur_alte_ergebnisse_und_stand_vorne(self):
        c = G.GgufChat("Test")
        lang = "Ergebnis von 'web_lesen' (ok):\n" + "x" * 5000
        c.messages = [{"role": "user", "content": "Lies die Seite"}, {"role": "assistant", "content": "…"},
                      {"role": "user", "content": lang}, {"role": "assistant", "content": "Fertig"},
                      {"role": "user", "content": "Danke! Und jetzt?"}, {"role": "assistant", "content": "…"},
                      {"role": "user", "content": lang}]
        c.stand = "- Ziel: Seite lesen"
        n = c._nachrichten("SYS", c.messages, stand=True)
        self.assertIn("gekürzt", n[3].content)              # vor der letzten echten Nachricht
        self.assertNotIn("gekürzt", n[7].content)           # laufende Werkzeug-Kette: ganz
        self.assertTrue(n[1].content.startswith(G.STAND_MARKE + "- Ziel: Seite lesen"))
        self.assertFalse(c._nachrichten("SYS", c.messages)[1].content.startswith(G.STAND_MARKE))
        self.assertIn("gekürzt", c.sichtbar()[2]["content"])

    def test_stand_fortschreiben(self):
        import asyncio
        c = G.GgufChat("Test")
        c.stand = "- alt"
        aufrufe = []

        async def einmal(system, verlauf, max_tokens=2048, denken_aus=False, hintergrund=False):
            aufrufe.append((system, verlauf[0]["content"], denken_aus))
            return "- alt\n- neu: Pfad C:/x"
        with mock.patch.object(c, "_einmal", side_effect=einmal):
            asyncio.run(c._stand_fortschreiben([{"role": "user", "content": "Bitte C:/x anlegen"},
                                                {"role": "assistant", "content": "Erledigt"}], 25600))
        self.assertEqual(GS.teilen(c.stand), ("- alt\n- neu: Pfad C:/x", ["Bitte C:/x anlegen"]))
        system, text, aus = aufrufe[0]
        self.assertTrue(aus)
        self.assertIn("Bisheriger Stand:\n- alt", text)
        self.assertIn("Gegenüber: Bitte C:/x anlegen", text)
        c.reset()
        self.assertEqual(c.stand, "")


class GanzerAblauf(unittest.TestCase):
    """stream() mit nachgebautem Motor: voller Kontext → Stand schreiben, Ältestes auslagern, antworten."""

    def test_volles_gespraech(self):
        import asyncio
        class _Motor:
            pass
        engine = _Motor()
        engine.__dict__.update(n_ctx=4096, chat_format=_Format(), tokenizer=NS(encode=lambda t: [0] * len(t.split())),
                               vision=None, last_ids=[1, 2], _pos=100, last_cached=0, checkpoint_new=False,
                               kv_im_vram=None, offloaded=False)
        engine.chat_format.thinking = False
        c = G.GgufChat("Test")
        for i in range(8):
            c.messages += [{"role": "user", "content": f"Frage {i} " + "wort " * 300},
                           {"role": "assistant", "content": f"Antwort {i} " + "wort " * 300}]
        stand_aufrufe = []

        async def einmal(system, verlauf, max_tokens=2048, denken_aus=False, hintergrund=False):
            stand_aufrufe.append(verlauf[0]["content"])
            return "- Ziel: viele Fragen beantworten"

        async def erzeugen(engine_, nachrichten, max_tokens, abbruch, denken_aus=False, **_):
            self.assertTrue(nachrichten[1].content.startswith(G.STAND_MARKE))
            yield "Hallo zurück"

        async def system_prompt(*a, **k):
            return "system " * 400

        async def laden():
            return engine
        with mock.patch.object(c, "_laden", side_effect=laden), \
                mock.patch.object(c, "_einmal", side_effect=einmal), \
                mock.patch.object(c, "_erzeugen", side_effect=erzeugen), \
                mock.patch("persona.build_system_prompt_async", side_effect=system_prompt), \
                mock.patch.object(G, "_gedaechtnis", return_value=""), \
                mock.patch.object(G, "prefix_laden", return_value=False), \
                mock.patch.object(G, "kontext", return_value=4096), \
                mock.patch.object(G, "_motor", (None, None, engine)), \
                mock.patch.object(G, "modelle", return_value={}):
            async def lauf():
                return [e async for e in c.stream("Neue Frage")]
            ereignisse = asyncio.run(lauf())
        notizen = " ".join(e["text"] for e in ereignisse if e["type"] == "note")
        self.assertIn("Stand des Gesprächs", notizen)
        self.assertTrue(stand_aufrufe)
        self.assertIn("Frage 0", stand_aufrufe[0])              # das Älteste ist im Stand gelandet
        self.assertEqual(GS.teilen(c.stand)[0], "- Ziel: viele Fragen beantworten")
        self.assertEqual(c.messages[-1]["content"], "Hallo zurück")
        self.assertEqual(c.messages[-2]["content"], "Neue Frage")
        self.assertNotIn("Frage 0", " ".join(m["content"] for m in c.messages))


class RamSchalter(unittest.TestCase):
    def test_wie_viel_in_den_vram_passt(self):
        datei = NS(stat=lambda: NS(st_size=6 * 2**30))
        with mock.patch.object(G, "modelle", return_value={"K": datei}), \
                mock.patch.object(G, "kv_bytes_pro_token", return_value=2**20), \
                mock.patch.object(G, "gewichte_vram", return_value=None):
            frei = int(9.5 * 2**30)                        # 9,5 - 6 - 1,5 = 2 GB = 2048 Token
            self.assertEqual(G.kv_gpu_token("K", 65536, frei), 2048)
            self.assertIsNone(G.kv_gpu_token("K", 1024, frei))


class KugelNachCharakter(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(paths, "DATEN", Path(tmp.name)),
                  mock.patch.object(profilordner, "aktiv", return_value="Alpha"),
                  mock.patch.object(KB, "ORDNER", Path(tmp.name) / "Persoenlichkeiten")):
            p.start()
            self.addCleanup(p.stop)
        self.datei = Path(tmp.name) / "Profile" / "Alpha" / "alpha.json"

    def charakter(self, kern, stil="anime style", seed=42):
        self.datei.parent.mkdir(parents=True, exist_ok=True)
        self.datei.write_text(json.dumps({"name": "Alpha", "kern": kern, "stil": stil, "seed_referenz": seed}),
                              encoding="utf-8")

    def test_ohne_datei_frei(self):
        self.assertIsNone(KB.charakter_aussehen("Alpha"))
        self.assertFalse(KB.veraltet("Alpha"))

    def test_aussehen_und_seed_aus_der_datei(self):
        self.charakter("a girl with long silver hair")
        beschreibung, seed = KB.charakter_aussehen("Alpha")
        self.assertEqual(beschreibung, "An adult woman with long silver hair. Anime style.")   # immer erwachsen
        self.assertEqual(seed, 42)

    def test_geaendertes_aussehen_macht_die_kugel_veraltet(self):
        self.charakter("a girl with long silver hair")
        KB._figur_merken("Alpha", KB.charakter_aussehen("Alpha")[0], 42)
        self.assertFalse(KB.veraltet("Alpha"))
        self.charakter("a girl with short red hair")
        self.assertTrue(KB.veraltet("Alpha"))
        with self.assertRaises(RuntimeError):
            KB.motive_malen("Alpha", ["x"])


if __name__ == "__main__":
    unittest.main()
