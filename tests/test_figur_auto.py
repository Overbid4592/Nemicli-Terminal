"""Tests: Figur automatisch dazu, wenn die Persönlichkeit sich malt und `figur` vergisst
(charakter.meint_sich, actions._figur_auto).

python -m unittest tests.test_figur_auto
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions  # noqa: E402
import charakter  # noqa: E402
import imagegen  # noqa: E402

FIGUR = {"name": "Testa – Die Probefigur", "seed_referenz": 4242, "stil": "photorealistic",
         "kern": "an adult woman with a sleek chestnut high ponytail with a bold red streak, piercing blue eyes, "
                 "sun-kissed skin and purple lips"}


class Erkennen(unittest.TestCase):
    def setUp(self):
        self.d = charakter._sauber(FIGUR)

    def grund(self, szene, name="Testa"):
        return charakter.meint_sich(szene, self.d, name)

    def test_name_selbstbezug_und_aussehen(self):
        self.assertIn("Testa", self.grund("Testa relaxing on a sofa"))
        self.assertIn("Testa", self.grund("a cozy evening", name="Andere") or self.grund("testa at the beach"))
        self.assertIn("selfie", self.grund("a mirror selfie in the gym"))
        self.assertIn("myself", self.grund("a photo of myself on a balcony"))
        grund = self.grund("a woman with a chestnut ponytail and a red streak, blue eyes, at a cafe")
        self.assertIn("Aussehen", grund)
        self.assertIn("chestnut", grund)

    def test_andere_bilder_bleiben_ohne_figur(self):
        for szene in ("a sunset over the sea", "a red fox in the snow",
                      "a red ribbon with bold letters on a blue gift box, chestnut wood",   # keine Person
                      "an old man reading a newspaper", "a woman with short black hair in a library",
                      "Testarossa sports car on a road"):                               # Name nur als Wortteil
            self.assertEqual("", self.grund(szene), szene)

    def test_ohne_charakter_datei_nichts(self):
        with mock.patch.object(charakter, "laden", return_value=None):
            self.assertEqual("", charakter.meint_sich("me in a park"))


class OhneAussehen(unittest.TestCase):
    def setUp(self):
        self.d = charakter._sauber(FIGUR)

    def test_reine_aussehenssaetze_fallen_weg(self):
        szene = ("A woman with a sleek chestnut ponytail, a bold red streak, piercing blue eyes and purple lips. "
                 "Her sun-kissed skin is smooth. Relaxing on a balcony at dusk.")
        neu, weg = charakter.ohne_aussehen(szene, self.d)
        self.assertEqual(3, weg)                                          # drei Stücke, der ganze erste Satz
        self.assertEqual("Her sun-kissed skin is smooth. Relaxing on a balcony at dusk.", neu)

    def test_aussehen_mitten_im_satz_faellt_weg(self):
        szene = "A photo of a woman, piercing blue eyes and purple lips, sitting at a desk in an office."
        self.assertEqual(("A photo of a woman, sitting at a desk in an office.", 1),
                         charakter.ohne_aussehen(szene, self.d))
        szene = "Bold red streak and blue eyes, reading a book."                    # erstes Stück weg: groß weiter
        self.assertEqual(("Reading a book.", 1), charakter.ohne_aussehen(szene, self.d))

    def test_stuecke_mit_szene_oder_eigenem_bleiben(self):
        for satz in ("Chestnut ponytail, bold red streak and blue eyes standing in a kitchen.",
                     "A bold red streak, piercing blue eyes looking at the camera.",
                     "Chestnut ponytail tied into a loose messy braid with small flowers.",       # Frisur fürs Bild
                     "Chestnut ponytail with a bold red streak, soft window light.",
                     "Chestnut ponytail, bold red streak and blue eyes reading a book.",
                     "Bold red streak and blue eyes, cozy cafe with plants and books."):
            szene = "A quiet evening. " + satz
            neu, _ = charakter.ohne_aussehen(szene, self.d)
            for teil in ("standing in a kitchen", "looking at the camera", "loose messy braid", "soft window light",
                         "reading a book", "cozy cafe with plants and books"):
                if teil in satz:
                    self.assertIn(teil, neu.lower(), satz)

    def test_nichts_bleibt_oder_ohne_datei(self):
        nur = "Chestnut ponytail, bold red streak, piercing blue eyes, purple lips."
        self.assertEqual((nur, 0), charakter.ohne_aussehen(nur, self.d))
        self.assertEqual(("x. y.", 0), charakter.ohne_aussehen("x. y.", None))


class Malen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pfad = Path(self.tmp.name) / "testa.json"
        self.pfad.write_text(json.dumps(FIGUR), encoding="utf-8")
        for p in (mock.patch.object(charakter, "datei", return_value=self.pfad),
                  mock.patch.object(charakter, "sicherung", return_value=Path(self.tmp.name) / "b" / "testa.json"),
                  mock.patch("persoenlichkeiten.active", return_value=types.SimpleNamespace(name="Testa"))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def malen(self, a):
        aufrufe = []

        def paint(prompt, **kw):
            aufrufe.append((prompt, kw))
            return f"bild{len(aufrufe)}.png"
        with mock.patch.object(imagegen, "missing_reason_aktiv", return_value=None), \
                mock.patch.object(imagegen, "modell_aufloesen", return_value=(None, "")), \
                mock.patch.object(imagegen, "paint", side_effect=paint), \
                mock.patch.object(imagegen, "gesicht_nachbessern", return_value=None), \
                mock.patch.object(actions.os, "startfile", create=True):
            res = (actions._bild_serie if "varianten" in a else actions._bild_malen)(a)
        return res, aufrufe

    def test_vergessene_figur_kommt_dazu(self):
        res, auf = self.malen({"prompt": "Testa doing yoga on a beach at sunrise"})
        prompt, kw = auf[0]
        self.assertTrue(prompt.startswith("An adult woman with a sleek chestnut high ponytail"))
        self.assertEqual(4242, kw["seed"])
        self.assertTrue(kw["figur"])                                  # Charakter-LoRA darf mit
        self.assertIn("🧍 Figur automatisch dazu", str(res))

    def test_figur_false_und_fremde_bilder_bleiben(self):
        _, auf = self.malen({"prompt": "Testa doing yoga", "figur": False})
        self.assertEqual("Testa doing yoga", auf[0][0])
        self.assertFalse(auf[0][1]["figur"])
        res, auf = self.malen({"prompt": "a sunset over the sea"})
        self.assertEqual("a sunset over the sea", auf[0][0])
        self.assertNotIn("automatisch", str(res))

    def test_aussehen_nicht_doppelt(self):
        szene = ("She has a sleek chestnut ponytail with a bold red streak, piercing blue eyes and sun-kissed skin. "
                 "She wears a green summer dress in a sunny cafe.")
        res, auf = self.malen({"prompt": szene, "figur": True})
        prompt = auf[0][0]
        self.assertEqual(1, prompt.count("chestnut"))                   # nur noch im Kern
        self.assertIn("green summer dress in a sunny cafe", prompt)
        self.assertIn("✂", str(res))
        self.assertNotIn("Dein Aussehen steht schon", str(res))           # figur selbst gesetzt: ✂ reicht

    def test_auto_wegen_aussehen_bittet_ums_weglassen(self):
        res, auf = self.malen({"prompt": "a woman with a chestnut ponytail and a red streak and blue eyes "
                                         "reading in a cafe"})
        self.assertIn("🧍 Figur automatisch dazu", str(res))
        self.assertIn("Dein Aussehen steht schon im Kern", str(res))
        self.assertIn("reading in a cafe", auf[0][0])

    def test_serie_je_bild_und_hinweis(self):
        res, auf = self.malen({"varianten": ["a selfie in a cafe", "a mountain lake at dawn"]})
        self.assertTrue(auf[0][0].startswith("An adult woman"))
        self.assertTrue(auf[0][1]["figur"])
        self.assertEqual("a mountain lake at dawn", auf[1][0])
        self.assertIn("Bild 1:", res.text)
        self.assertNotIn("Bild 2:", res.text.split("🧍")[1])


if __name__ == "__main__":
    unittest.main()
