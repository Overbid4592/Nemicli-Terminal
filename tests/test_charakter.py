"""Tests: Charakter-Datei je Persönlichkeit (tools/charakter.py), bild_malen mit Figur, bild_serie.

python -m unittest tests.test_charakter
"""

import json
import sys
import tempfile
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

FIGUR = {"name": "Testfigur", "kern": "one adult person with short red hair",
         "seed_referenz": 4242, "stil": "photorealistic, soft light",
         "outfits": {"sport": "a blue tracksuit"}, "posen": {"sitzend": "sits on a bench"},
         "regeln": ["always alone"]}


class MitDatei(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pfad = Path(self.tmp.name) / "testfigur.json"
        self.flick = mock.patch.object(charakter, "datei", return_value=self.pfad)
        self.flick.start()
        self.b = Path(self.tmp.name) / "Backup" / "testfigur.json"
        self.flick_b = mock.patch.object(charakter, "sicherung", return_value=self.b)
        self.flick_b.start()

    def tearDown(self):
        self.flick.stop()
        self.flick_b.stop()
        self.tmp.cleanup()

    def schreiben(self, d=FIGUR):
        self.pfad.write_text(json.dumps(d), encoding="utf-8")


class Absicherung(MitDatei):
    def test_schreiben_legt_die_kopie_an(self):
        text, ok = charakter.aendern({"kern": "one adult person"})
        self.assertTrue(ok, text)
        self.assertEqual(self.pfad.read_text(encoding="utf-8"), self.b.read_text(encoding="utf-8"))

    def test_fehlt_a_gilt_b_und_a_kommt_zurueck(self):
        charakter.aendern({"kern": "one adult person", "stil": "soft light"})
        self.pfad.unlink()
        d = charakter.laden()
        self.assertEqual(("one adult person", "soft light"), (d["kern"], d["stil"]))
        self.assertTrue(self.pfad.is_file())                                 # A wiederhergestellt
        self.assertEqual("one adult person", charakter.roh()["kern"])

    def test_kaputte_a_wird_aus_b_ersetzt(self):
        charakter.aendern({"kern": "one adult person"})
        self.pfad.write_text("{kaputt", encoding="utf-8")
        with mock.patch("snapshot.sichern") as gesichert:
            self.assertEqual("one adult person", charakter.laden()["kern"])
        gesichert.assert_called_once()                                       # kaputte A geht in den Papierkorb
        self.assertEqual("one adult person", json.loads(self.pfad.read_text(encoding="utf-8"))["kern"])

    def test_vorhandene_a_ohne_b_wird_beim_lesen_gesichert(self):
        self.schreiben()
        self.assertFalse(self.b.exists())
        charakter.laden()
        self.assertEqual(self.pfad.read_text(encoding="utf-8"), self.b.read_text(encoding="utf-8"))

    def test_ohne_a_und_b_nichts(self):
        self.assertIsNone(charakter.laden())
        self.assertEqual({}, charakter.roh())
        self.assertFalse(self.pfad.exists())

    def test_sicherung_liegt_ausserhalb_des_profils(self):
        self.flick_b.stop()
        try:
            with mock.patch("profilordner.wurzel", return_value=Path(self.tmp.name) / "Profile"):
                self.assertEqual(charakter.sicherung(),
                                 Path(self.tmp.name) / "Backup" / "Charaktere" / "testfigur.json")
        finally:
            self.flick_b.start()


class Datei(MitDatei):
    def test_dateiname_aus_der_persoenlichkeit(self):
        self.flick.stop()
        try:
            with mock.patch("profilordner.aktiv", return_value="Test_Figur"), \
                    mock.patch("profilordner.wurzel", return_value=Path(self.tmp.name)):
                self.assertEqual(charakter.datei(), Path(self.tmp.name) / "Test_Figur" / "test_figur.json")
        finally:
            self.flick.start()

    def test_ohne_datei_oder_ohne_kern_keine_figur(self):
        self.assertIsNone(charakter.laden())
        self.schreiben({"name": "x", "kern": ""})
        self.assertIsNone(charakter.laden())

    def test_anlegen_vergibt_seed_und_aendern_behaelt_ihn(self):
        text, ok = charakter.aendern({"kern": "one adult person", "outfits": {"a": "red coat"}})
        self.assertTrue(ok, text)
        d = charakter.laden()
        seed = d["seed_referenz"]
        self.assertIsInstance(seed, int)
        with mock.patch("snapshot.sichern") as sichern:
            charakter.aendern({"posen": {"steht": "stands"}, "outfits": {"a": ""}})
        sichern.assert_called_once()
        d = charakter.laden()
        self.assertEqual(seed, d["seed_referenz"])
        self.assertEqual({}, d["outfits"])                        # leerer Text entfernt
        self.assertEqual({"steht": "stands"}, d["posen"])
        with mock.patch("snapshot.sichern"):
            charakter.aendern({"neuer_seed": True})
        self.assertNotEqual(seed, charakter.laden()["seed_referenz"])

    def test_ohne_kern_wird_nicht_angelegt(self):
        text, ok = charakter.aendern({"stil": "nur Stil"})
        self.assertFalse(ok)
        self.assertIn("kern", text)
        self.assertFalse(self.pfad.exists())
        self.assertIn("NICHT", charakter.vorschau({"stil": "x"}))

    def test_prompt_reihenfolge_und_freier_text(self):
        self.schreiben()
        d = charakter.laden()
        p = charakter.prompt_bauen(d, "in a park at noon", "sport", "sitzend")
        self.assertEqual(p, "One adult person with short red hair. Wearing a blue tracksuit. Sits on a bench. "
                            "In a park at noon. Photorealistic, soft light.")
        frei = charakter.prompt_bauen(d, "", "a green dress", "leans on a wall")
        self.assertIn("Wearing a green dress.", frei)
        self.assertIn("Leans on a wall.", frei)

    def test_seed_fest_ausser_neues_gesicht(self):
        self.schreiben()
        d = charakter.laden()
        self.assertEqual(4242, charakter.seed(d))
        self.assertIsNone(charakter.seed(d, neues_gesicht=True))

    def test_kurzinfo_fuer_die_ki(self):
        self.assertEqual("", charakter.kurzinfo())
        self.schreiben()
        info = charakter.kurzinfo()
        self.assertIn("Outfits: sport", info)
        self.assertIn("Seed fest (4242)", info)
        self.assertIn("always alone", info)


class BildMitFigur(MitDatei):
    def malen(self, a):
        aufrufe = []

        def paint(prompt, **kw):
            aufrufe.append((prompt, kw))
            return f"bild{len(aufrufe)}.png"

        with mock.patch.object(imagegen, "missing_reason_aktiv", return_value=None), \
                mock.patch.object(imagegen, "modell_aufloesen", return_value=(None, "")), \
                mock.patch.object(imagegen, "paint", side_effect=paint), \
                mock.patch.object(imagegen, "gesicht_nachbessern", return_value=None), \
                mock.patch.object(actions.os, "startfile", create=True) as start:
            res = (actions._bild_serie if "varianten" in a else actions._bild_malen)(a)
        return res, aufrufe, start

    def test_bild_malen_baut_prompt_und_nimmt_den_seed(self):
        self.schreiben()
        res, auf, _ = self.malen({"prompt": "in a park", "figur": True, "outfit": "sport"})
        prompt, kw = auf[0]
        self.assertTrue(prompt.startswith("One adult person with short red hair. Wearing a blue tracksuit."))
        self.assertEqual(4242, kw["seed"])
        _, auf, _ = self.malen({"prompt": "x", "figur": True, "seed": 7})
        self.assertEqual(7, auf[0][1]["seed"])                     # ausdrücklicher Seed gewinnt
        _, auf, _ = self.malen({"prompt": "x", "figur": "ja", "neues_gesicht": True})
        self.assertIsNone(auf[0][1]["seed"])

    def test_ohne_figur_bleibt_der_prompt(self):
        _, auf, _ = self.malen({"prompt": "a red fox"})
        self.assertEqual("a red fox", auf[0][0])

    def test_figur_ohne_datei_meldet_es(self):
        res, auf, _ = self.malen({"prompt": "x", "figur": True})
        self.assertFalse(res.ok)
        self.assertIn("charakter_aendern", res.text)
        self.assertEqual([], auf)

    def test_serie_gleicher_seed_jedes_bild_gleich_anzeigen(self):
        self.schreiben()
        res, auf, start = self.malen({"figur": True, "varianten": [
            "reading a book", {"prompt": "on a beach", "outfit": "sport", "size": "1024x1024"},
            {"prompt": "in the rain", "pose": "sitzend"}]})
        self.assertTrue(res.ok, res.text)
        self.assertEqual(3, len(auf))
        self.assertEqual({4242}, {kw["seed"] for _, kw in auf})
        self.assertEqual((1024, 1024), auf[1][1]["size"])
        self.assertIn("Wearing a blue tracksuit", auf[1][0])
        self.assertIn("3/3 Bilder", res.text)
        self.assertEqual([mock.call("bild1.png"), mock.call("bild2.png"), mock.call("bild3.png")],
                         start.call_args_list)                     # jedes Bild, sobald es fertig ist

    def test_serie_ohne_figur_teilt_einen_zufallsseed(self):
        _, auf, _ = self.malen({"varianten": ["a cat", "a dog"]})
        self.assertEqual(1, len({kw["seed"] for _, kw in auf}))
        self.assertIsNotNone(auf[0][1]["seed"])

    def test_serie_grenzen(self):
        res, auf, _ = self.malen({"varianten": ["x"] * (actions.MAX_SERIE + 1)})
        self.assertFalse(res.ok)
        self.assertEqual([], auf)
        res, _, _ = self.malen({"varianten": []})
        self.assertFalse(res.ok)


class KIHinweis(MitDatei):
    def test_hinweis_mit_und_ohne_datei(self):
        import persona
        self.assertIn("noch keine Charakter-Datei", persona._figur_hinweis())
        self.schreiben()
        hinweis = persona._figur_hinweis()
        self.assertIn("Outfits: sport", hinweis)
        self.assertIn("bild_serie", hinweis)


class ImmerErwachsen(unittest.TestCase):

    def test_kern_wird_erwachsen(self):
        for kern, erwartet in (("sweet charming girl with warm smile", "adult sweet charming woman with warm smile"),
                               ("a boy with short hair", "an adult man with short hair"),
                               ("Teenager with freckles", "Adult with freckles"),
                               ("ein Mädchen mit roten Haaren", "adult eine Frau mit roten Haaren"),
                               ("Mädchen, rote Haare", "adult Frau, rote Haare"),
                               ("the person with silver hair", "an adult person with silver hair"),
                               ("kind eyes, long hair", "adult kind eyes, long hair")):
            self.assertEqual(erwartet, charakter.kern_erwachsen(kern), kern)

    def test_erwachsener_kern_bleibt(self):
        for kern in ("an adult woman around 25 years old, red hair", "mature man with a beard",
                     "a grown-up person, calm eyes"):
            self.assertEqual(kern, charakter.kern_erwachsen(kern))
        self.assertEqual("", charakter.kern_erwachsen(""))

    def test_prompt_und_datei(self):
        d = {"kern": "sweet girl, light hair", "stil": "photo", "outfits": {}, "posen": {}}
        self.assertTrue(charakter.prompt_bauen(d).startswith("Adult sweet woman, light hair."))
        self.assertEqual("sweet girl, light hair", d["kern"])               # die Datei bleibt, wie sie ist


if __name__ == "__main__":
    unittest.main()
