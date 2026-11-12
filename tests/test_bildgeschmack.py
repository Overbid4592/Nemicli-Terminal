"""Bild-Bewertung mit Warum, Prompt-Bausteine der Charakter-Datei, Serien-Prüfung durch den Bildbeschreiber und
gedaechtnis_status."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions      # noqa: E402
import charakter    # noqa: E402
import einlesen     # noqa: E402
import indexdb      # noqa: E402
import memory       # noqa: E402
import modes        # noqa: E402
import persona      # noqa: E402

FIGUR = {"name": "Testfigur", "kern": "one adult person with short red hair", "seed_referenz": 4242,
         "stil": "soft light", "outfits": {}, "posen": {},
         "ausdruecke": {"frecher_blick": "playful smirk, one eyebrow raised, looking straight into the camera"}}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name)
        self.figur = self.ordner / "testfigur.json"
        for p in (mock.patch.object(memory, "_FILE", self.ordner / "memory.json"),
                  mock.patch.object(memory, "_profil_datei", return_value=None),
                  mock.patch.object(charakter, "datei", return_value=self.figur),
                  mock.patch.object(charakter, "sicherung", return_value=self.ordner / "B" / "testfigur.json"),
                  mock.patch("snapshot.sichern", side_effect=lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def figur_schreiben(self, d=FIGUR):
        self.figur.write_text(json.dumps(d), encoding="utf-8")


class Bausteine(_Tmp):
    def test_einsetzen_und_unbekannt(self):
        text, unbekannt = charakter.bausteine_einsetzen("smiling {Frecher_Blick} at a cafe {neu}",
                                                        {"frecher_blick": "PLAYFUL"})
        self.assertEqual("smiling PLAYFUL at a cafe {neu}", text)
        self.assertEqual(["neu"], unbekannt)
        self.assertEqual(("a {x y} json", []), charakter.bausteine_einsetzen("a {x y} json", {}))

    def test_bild_prompt_bekommt_den_baustein(self):
        self.figur_schreiben()
        prompt, seed, _lora, _hinweis = actions._figur({"figur": True}, "{frecher_blick}, in a cafe")
        self.assertIn("one eyebrow raised", prompt)
        self.assertEqual(4242, seed)
        frei, *_ = actions._figur({"figur": False}, "a stranger with {frecher_blick}")
        self.assertEqual("a stranger with playful smirk, one eyebrow raised, looking straight into the camera", frei)

    def test_unbekannter_baustein_ist_ein_fehler_mit_der_liste(self):
        self.figur_schreiben()
        res = actions._figur({"figur": True}, "{berry_lippen} close-up")
        self.assertFalse(res.ok)
        self.assertIn("{berry_lippen}", res.text)
        self.assertIn("{frecher_blick}", res.text)

    def test_anlegen_entfernen_und_galerie_behaelt_sie(self):
        self.figur_schreiben()
        _text, ok = charakter.aendern({"ausdruecke": {"berry_lippen": "glossy berry red lips"}})
        self.assertTrue(ok)
        self.assertEqual({"frecher_blick", "berry_lippen"}, set(charakter.laden()["ausdruecke"]))
        charakter.aendern({"ausdruecke": {"frecher_blick": ""}})
        self.assertEqual(["berry_lippen"], list(charakter.ausdruecke()))
        self.assertIn("{berry_lippen}", charakter.kurzinfo())
        self.assertIn("berry_lippen", charakter._sauber(charakter.roh())["ausdruecke"])   # Galerie speichert über _sauber


class Bewerten(_Tmp):
    def test_ohne_warum_kein_eintrag(self):
        res = actions._bild_bewerten({"urteil": "gut"})
        self.assertFalse(res.ok)
        self.assertEqual([], memory.all_entries())

    def test_mit_warum_bild_und_prompt(self):
        from PIL import Image, PngImagePlugin
        bild = self.ordner / "bild.png"
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", "adult person, playful smirk, red lips")
        Image.new("RGB", (8, 8)).save(bild, pnginfo=info)
        with mock.patch.object(actions, "_read_guard", return_value=None):
            text = actions._bild_bewerten({"urteil": "👍", "warum": "Blick top, Make-up egal", "bild": str(bild)})
        self.assertIn("Gemerkt", text)
        [e] = memory.all_entries()
        self.assertEqual("geschmack", e["kind"])
        self.assertIn("👍", e["text"])
        self.assertIn("Blick top, Make-up egal", e["text"])
        self.assertIn("playful smirk", e["text"])

    def test_steht_vor_dem_malen_im_hinweis(self):
        for i in range(8):
            actions._bild_bewerten({"urteil": "gut", "warum": f"Punkt Nummer {i} gefällt"})
        h = persona._geschmack_hinweis()
        self.assertIn("Punkt Nummer 7", h)
        self.assertNotIn("Punkt Nummer 0", h)                   # nur die neuesten
        self.assertEqual(persona.GESCHMACK_ZEIGEN, h.count("Bildgeschmack"))

    def test_registriert_und_im_chat_frei(self):
        self.assertIn("bild_bewerten", actions.ACTIONS)
        self.assertIn("bild_bewerten", modes.MEMORY_TOOLS)
        self.assertIn("bild_bewerten", actions._MEMORY_TOOLS)


class SeriePruefen(unittest.TestCase):
    def test_kriterien(self):
        self.assertEqual([], actions._kriterien(None))
        self.assertEqual(["gaze at camera", "red lips"], actions._kriterien("gaze at camera; red lips"))
        self.assertFalse(actions._kriterien([f"p{i}" for i in range(9)]).ok)

    def test_tabelle_aus_den_antworten(self):
        antworten = iter(["1. yes – looks into the lens\n2. no – lips are pink",
                          "**1.** Yes\n2) yes", "I cannot tell."])
        bb = mock.MagicMock()
        bb.finden.return_value = ("m", "p")
        bb.beschreiben.side_effect = lambda p, f: next(antworten)
        with mock.patch.dict(sys.modules, {"bildbeschreiber": bb}):
            t = actions._serie_pruefen(["a.png", "b.png", "c.png"], ["gaze at camera", "red lips"])
        self.assertIn("| 1 | ✅ | ❌ |", t)
        self.assertIn("| 2 | ✅ | ✅ |", t)
        self.assertIn("| 3 | ? | ? |", t)
        self.assertNotIn("lips are pink", t)                    # Antwort selbst bleibt draußen
        self.assertIn("2. red lips", bb.beschreiben.call_args.args[1])

    def test_ohne_bildbeschreiber(self):
        bb = mock.MagicMock()
        bb.finden.return_value = None
        with mock.patch.dict(sys.modules, {"bildbeschreiber": bb}):
            self.assertIn("übersprungen", actions._serie_pruefen(["a.png"], ["x"]))


class Status(unittest.TestCase):
    def test_zahlen(self):
        with mock.patch.object(einlesen, "stand", return_value=61_500), \
                mock.patch.object(indexdb, "offen", return_value={"brocken": 12_000, "ohne_vektor": 900}), \
                mock.patch.object(indexdb, "_stand", return_value="2026-11-07 13:00, 21 s (voll): …"), \
                mock.patch("gedaechtnispflege.zuletzt", return_value="2026-11-07 09:00"):
            t = actions._gedaechtnis_status({}).text
        self.assertIn("61.500 von 100.000 Token (62 %, orange)", t)
        self.assertIn("ohne Vektor 900", t)
        self.assertIn("2026-11-07 13:00", t)
        self.assertIn("2026-11-07 09:00", t)

    def test_ohne_modell(self):
        with mock.patch.object(einlesen, "stand", return_value=None), \
                mock.patch.object(indexdb, "offen", side_effect=RuntimeError), \
                mock.patch.object(indexdb, "_stand", return_value=""), \
                mock.patch("gedaechtnispflege.zuletzt", return_value=""):
            t = actions._gedaechtnis_status({}).text
        self.assertIn("Kein Embedding-Modell", t)
        self.assertIn("noch keine", t)

    def test_registriert_und_nur_lesen(self):
        self.assertIn("gedaechtnis_status", actions.ACTIONS)
        self.assertIn("gedaechtnis_status", modes.READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
