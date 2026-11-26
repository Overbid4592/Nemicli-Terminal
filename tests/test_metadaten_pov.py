"""Dateimetadaten (Zeichen ≠ Bytes), Tokenanzeige, Ich-Perspektive im Figur-Prompt, Skill-Strichliste.

python -m unittest tests.test_metadaten_pov
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions      # noqa: E402
import anhang       # noqa: E402
import charakter    # noqa: E402
import paths        # noqa: E402
import pricing      # noqa: E402
import skills       # noqa: E402
import ui           # noqa: E402

# Umlaute (2 Bytes), Emoji (4 Bytes), letzte Zeile ohne Zeilenumbruch.
TEXT = "# Überschrift 🎭\nÄrger über Größe\nletzte Zeile"


class Dateimasse(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def datei(self, name: str, roh: bytes) -> Path:
        p = self.t / name
        p.write_bytes(roh)
        return p

    def test_zeichen_bytes_zeilen_getrennt(self):
        roh = TEXT.encode("utf-8")
        p = self.datei("text.md", roh)
        inhalt, zeichen, zeilen = anhang.lese_text(p, 10_000)
        self.assertEqual(inhalt, TEXT)
        self.assertEqual(zeichen, len(TEXT))
        self.assertEqual(zeilen, 3)                       # wie ein Editor: letzte Zeile ohne \n zählt mit
        self.assertGreater(len(roh), zeichen)             # UTF-8: mehr Bytes als Zeichen
        self.assertEqual(len(roh) - zeichen, 5 + 3)       # Ü Ä ü ö ß je +1, Emoji +3

    def test_bom_ist_kein_zeichen(self):
        p = self.datei("bom.txt", b"\xef\xbb\xbf" + TEXT.encode("utf-8"))
        inhalt, zeichen, _ = anhang.lese_text(p, 10_000)
        self.assertEqual(inhalt, TEXT)
        self.assertEqual(zeichen, len(TEXT))

    def test_cp1252_rueckfall(self):
        p = self.datei("alt.txt", "Größe".encode("cp1252"))
        inhalt, zeichen, zeilen = anhang.lese_text(p, 10_000)
        self.assertEqual((inhalt, zeichen, zeilen), ("Größe", 5, 1))

    def test_kopfzeile_nennt_zeichen_und_bytes(self):
        roh = TEXT.encode("utf-8")
        p = self.datei("guide.md", roh)
        e = anhang.anhaengen(f'"{p}" lies das', grenze=100_000)
        kopf = e["text"].split("\n", 2)[1]
        self.assertIn(f"{len(TEXT)} Zeichen · {len(roh)} Bytes · 3 Zeilen", kopf)
        self.assertIn(TEXT, e["text"])                    # vollständig, nicht gekürzt
        self.assertEqual(anhang.text_masse(83_677, 84_246, 2_105),
                         "83.677 Zeichen · 84.246 Bytes · 2.105 Zeilen")

    def test_kuerzung_zaehlt_zeichen(self):
        text = "ä" * 3000
        p = self.datei("lang.txt", text.encode("utf-8"))
        e = anhang.anhaengen(f'"{p}"', grenze=1000)
        self.assertIn("Zeichen 1.000–3.000 fehlen (von 3.000 gesamt)", e["text"])
        self.assertIn("3.000 Zeichen · 6.000 Bytes · 1 Zeilen", e["text"])


class Tokenanzeige(unittest.TestCase):
    def test_fmt_tokens(self):
        self.assertEqual(ui.fmt_tokens(950), "950")
        self.assertEqual(ui.fmt_tokens(82_200), "82.2k")
        self.assertEqual(ui.fmt_tokens(100_000), "100.0k")
        self.assertEqual(ui.fmt_tokens(1_048_576), "1.05M")
        self.assertEqual(ui.fmt_tokens(1_000_000), "1M")
        self.assertEqual(ui.fmt_tokens(2_000_000), "2M")

    def test_schaetzung_ist_zeichen_durch_vier(self):
        self.assertEqual(pricing.est_tokens("x" * 400), 101)
        self.assertEqual(pricing.est_tokens([{"type": "text", "text": "x" * 40}]), 11)

    def test_statusleiste_mit_millionen_fenster(self):
        frags = ui._ctx_fragments(82_200, 1_048_576, "#000000")
        self.assertEqual(frags[-1][1], "82.2k/1.05M")


KERN = ("Adult woman, approx. 170 cm tall. Athletic hourglass silhouette and toned legs. "
        "Bright green eyes, full lips and an open smile. Long dark hair with a side part. "
        "Short rounded nails with clear polish. Expressive makeup with smoky eyes.")
FIGUR = {"name": "Testfigur", "kern": KERN, "seed_referenz": 4242, "stil": "photorealistic, soft light",
         "outfits": {"standard": "a white shirt"}, "posen": {"sitzend": "sits on a bench"},
         "frisuren": {"zopf": "a high ponytail"}, "regeln": []}


class Perspektiven(unittest.TestCase):
    def test_fuenf_perspektiven(self):
        faelle = {
            "POV, first-person viewpoint, my hands hold a phone": True,          # Ich-Perspektive
            "a 35mm film photograph, first-person view looking down at my legs": True,
            "POV photograph, my hand raises a phone toward a bathroom mirror": True,   # Spiegel-Selfie
            "a woman reading in a cafe, eye level shot": False,                   # Außensicht
            "selfie, arm extended, looking into the front camera": False,         # Selfie: Gesicht gewollt
            "POV selfie at the beach": False,
            "over-the-shoulder shot from behind, she reads a letter": False,      # Over-the-Shoulder
        }
        for szene, erwartet in faelle.items():
            with self.subTest(szene=szene):
                self.assertIs(charakter.ich_perspektive(szene), erwartet)

    def test_sichtbarer_kern_ohne_kopf(self):
        k = charakter.sichtbarer_kern(KERN)
        for weg in ("eyes", "lips", "smile", "hair", "makeup"):
            self.assertNotIn(weg, k.lower())
        for bleibt in ("Adult woman", "hourglass", "nails"):
            self.assertIn(bleibt, k)

    def test_sichtbarer_kern_bleibt_erwachsen(self):
        self.assertTrue(charakter.sichtbarer_kern("A girl with blue eyes.").lower().startswith("a"))
        self.assertIn("woman", charakter.sichtbarer_kern("A girl with blue eyes."))
        self.assertIn("adult", charakter.sichtbarer_kern("Slim body. Blue eyes."))

    def test_pov_prompt_reihenfolge(self):
        szene = "POV, first-person viewpoint, looking down, my hands hold a phone, keep the image POV."
        p = charakter.prompt_bauen(FIGUR, szene, None, "sitzend", "zopf")
        self.assertTrue(p.startswith("POV, first-person viewpoint"))
        self.assertTrue(p.endswith("Keep the image POV."))
        self.assertEqual(p.lower().count("keep the image pov"), 1)
        self.assertIn("My own body: adult woman", p)
        self.assertIn("Wearing a white shirt.", p)
        self.assertIn("Sits on a bench.", p)
        for weg in ("eyes", "lips", "smile", "hair", "ponytail", "makeup"):
            self.assertNotIn(weg, p.lower())
        self.assertLess(p.index("POV"), p.index("My own body"))

    def test_aussensicht_unveraendert(self):
        p = charakter.prompt_bauen(FIGUR, "in a park at noon")
        self.assertTrue(p.startswith("Adult woman, approx. 170 cm tall."))
        self.assertIn("Bright green eyes", p)
        self.assertNotIn("Keep the image POV", p)

    def test_figur_meldet_ich_perspektive(self):
        with mock.patch.object(charakter, "laden", return_value=FIGUR), \
                mock.patch("lorabild.fuer_charakter", return_value=(None, None, "")):
            prompt, seed, lora, hinweis = actions._figur({"figur": True}, "POV, first-person viewpoint, my hands")
            self.assertTrue(prompt.startswith("POV"))
            self.assertEqual(seed, 4242)
            self.assertIsNone(lora)
            self.assertIn("Ich-Perspektive", hinweis)
            _p, _s, _l, hinweis = actions._figur({"figur": True}, "reading in a cafe")
            self.assertNotIn("Ich-Perspektive", hinweis)

    def test_pov_mit_bild_lora(self):
        import lorabild
        szene = "POV, first-person viewpoint, my hands hold a phone. Keep the image POV."
        p = lorabild.prompt_bauen("figur_a", szene, "a white shirt", "Sits on a bench", "photo",
                                  kern=FIGUR["kern"], frisur="a high ponytail")
        self.assertTrue(p.startswith("POV, first-person viewpoint"))
        self.assertTrue(p.endswith("Keep the image POV."))
        self.assertEqual(p.lower().count("keep the image pov"), 1)
        self.assertLess(p.index("My own body: adult woman"), p.index("Seen through the eyes of figur_a"))
        self.assertIn("Wearing a white shirt.", p)
        self.assertNotIn("A photo of figur_a", p)
        for weg in ("eyes,", "lips", "smile", "hair", "ponytail", "makeup"):
            self.assertNotIn(weg, p.lower())
        aussen = lorabild.prompt_bauen("figur_a", "reading in a cafe", kern=FIGUR["kern"])
        self.assertTrue(aussen.startswith("Adult woman"))
        self.assertIn("A photo of figur_a, an adult.", aussen)

    def test_figur_mit_lora_meldet_ich_perspektive(self):
        bindung = {"name": "figur_a", "datei": "x.safetensors", "sha256": "0" * 64}
        with mock.patch.object(charakter, "laden", return_value=FIGUR), \
                mock.patch("lorabild.fuer_charakter", return_value=(bindung, Path("x.safetensors"), "")), \
                mock.patch("imagegen.lora_moeglich", return_value=True):
            prompt, _s, lora, hinweis = actions._figur({"figur": True}, "POV, first-person viewpoint, my hands")
            self.assertTrue(prompt.startswith("POV"))
            self.assertIsNotNone(lora)
            self.assertIn("Bild-LoRA", hinweis)
            self.assertIn("Ich-Perspektive", hinweis)
            _p, _s, _l, hinweis = actions._figur({"figur": True}, "reading in a cafe")
            self.assertNotIn("Ich-Perspektive", hinweis)


SKILL = {"name": "Bild-Prompting", "beschreibung": "Prompts für den Bild-Motor",
         "wann": "wenn ein Bild gemalt wird", "anleitung": "1. Medium zuerst.\n2. Kamera nennen."}


class SkillKette(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [mock.patch.object(paths, "DATEN", Path(self._tmp.name).resolve()),
                         mock.patch.object(skills, "selbst", return_value=False),
                         mock.patch("profilordner.aktiv", return_value="Test"),
                         mock.patch("skillwaechter.pruefen", return_value=None),
                         mock.patch("skillwaechter.gespeichert")]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_laden_ausbessern_neu_laden(self):
        pfad = skills.schreiben(**SKILL)
        vorher = skills.laden_text("Bild-Prompting")
        self.assertIn("2. Kamera nennen.", vorher)
        neu = "2. Kamera nennen.\n2b. POV: Perspektive zuerst, am Ende keep the image POV."
        with mock.patch("snapshot.sichern"):
            erg = actions._skill_ausbessern({"name": "Bild-Prompting", "suchen": "2. Kamera nennen.",
                                             "ersetzen": neu})
        self.assertTrue(erg.ok, erg.text)
        nachher = skills.laden_text("Bild-Prompting")
        self.assertIn("2b. POV: Perspektive zuerst", nachher)
        self.assertIn("1. Medium zuerst.", nachher)                  # Rest bleibt
        self.assertIn("2b. POV", pfad.read_text(encoding="utf-8"))
        st = json.loads((pfad.parent / skills.STATISTIK).read_text(encoding="utf-8"))
        self.assertEqual(st["verbessert"], 1)

    def test_fehlgeschlagenes_ausbessern_zaehlt_nicht(self):
        pfad = skills.schreiben(**SKILL)
        erg = actions._skill_ausbessern({"name": "Bild-Prompting", "suchen": "steht nicht drin", "ersetzen": "x"})
        self.assertFalse(erg.ok)
        self.assertFalse((pfad.parent / skills.STATISTIK).exists())

    def test_ueberschreiben_zaehlt_als_verbessert(self):
        pfad = skills.schreiben(**SKILL)
        erg = actions._skill_schreiben(dict(SKILL, anleitung="1. Medium zuerst.\n2. Licht nennen."))
        self.assertTrue(erg.ok, erg.text)
        st = json.loads((pfad.parent / skills.STATISTIK).read_text(encoding="utf-8"))
        self.assertEqual(st["verbessert"], 1)


if __name__ == "__main__":
    unittest.main()
