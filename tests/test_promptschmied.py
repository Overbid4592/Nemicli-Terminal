"""Prompt-Schmied: Erkenner für Krea-2-Prompts, einmalige Rückgabe an die KI, Lerner aus Bildurteilen."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image, PngImagePlugin   # noqa: E402

import actions                           # noqa: E402
import imagegen                          # noqa: E402
import krea                              # noqa: E402
import promptschmied as PS               # noqa: E402

GUT = ("A portrait photograph of an adult woman with long wavy hair sitting by a café window. Soft window "
       "light falls across her face, sharp focus on the eyes, shallow depth of field, 85mm lens.")


def _kennungen(prompt):
    return [k for k, _h in PS.pruefen(prompt)]


class Erkenner(unittest.TestCase):
    def test_guter_prompt_ohne_funde(self):
        self.assertEqual(_kennungen(GUT), [])

    def test_harte_funde(self):
        self.assertIn("liste", _kennungen("woman, red hair, smile, cafe, window, bokeh, soft light, 85mm"))
        self.assertIn("verneinung", _kennungen(GUT + " There is no blur anywhere."))
        self.assertIn("gewicht", _kennungen(GUT + " (freckles:1.4)"))
        self.assertIn("qualitaet", _kennungen("masterpiece, " + GUT))
        self.assertIn("kurz", _kennungen("a cat in soft light, sharp focus"))
        self.assertIn("sprache", _kennungen("Eine Frau mit roten Haaren sitzt im Café und trägt eine Brille, "
                                            "das Licht ist weich und die Kamera ist nah dran"))

    def test_ansicht_von_hinten_widerspricht_vorne(self):
        hinten = ("A photograph of an adult woman kneeling on a bed, viewed from behind, soft lamp light, "
                  "sharp focus, 50mm lens.")
        self.assertNotIn("ansicht", _kennungen(hinten))
        self.assertNotIn("ansicht", _kennungen(hinten + " She is looking back over her shoulder with a smile."))
        for vorne in (" Her breasts are clearly visible through the fabric.",
                      " The dress is showing her bare chest.",
                      " She is looking directly at the camera.",
                      " Warm eye contact."):
            funde = dict(PS.pruefen(hinten + vorne))
            self.assertIn("ansicht", funde, vorne)
            self.assertIn("from behind", funde["ansicht"])
        self.assertNotIn("ansicht", _kennungen(GUT + " Her chest is visible above the table."))   # ohne „hinten“

    def test_text_im_bild_zaehlt_nicht(self):
        self.assertNotIn("verneinung", _kennungen(GUT + ' A sign on the wall reads "NO ENTRY".'))

    def test_licht_und_fokus_weich(self):
        prompt = "An adult woman with long wavy hair sits at a wooden table in a small café and reads a book."
        self.assertEqual(set(_kennungen(prompt)), {"licht", "fokus"})
        gezeichnet = "A watercolor illustration of a fox in a snowy forest at dawn with pine trees around it."
        self.assertNotIn("fokus", _kennungen(gezeichnet))


class Rueckgabe(unittest.TestCase):
    def setUp(self):
        PS._offen.clear()
        p = mock.patch.object(PS, "modus", return_value="an")
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(PS, "hinweise", return_value="")
        q.start()
        self.addCleanup(q.stop)

    def test_einmal_zurueck_dann_malen(self):
        text, vorher = PS.vorpruefen(["cat, cute, fluffy, bed, pillow, cozy"], "m")
        self.assertIn("noch nicht gemalt", text)
        self.assertIn("Schlagwortliste", text)
        text2, vorher2 = PS.vorpruefen(["cat, cute, fluffy, bed, pillow, cozy"], "m")
        self.assertIsNone(text2)                       # zweiter Aufruf malt in jedem Fall
        self.assertEqual(vorher2, "cat, cute, fluffy, bed, pillow, cozy")

    def test_weiche_funde_halten_nicht_auf(self):
        prompt = "An adult woman with long wavy hair sits at a wooden table in a small café and reads a book."
        self.assertEqual(PS.vorpruefen([prompt], "m"), (None, ""))
        self.assertIn("Kein Licht", PS.nachher([prompt]))

    def test_alte_rueckgabe_verfaellt(self):
        PS.vorpruefen(["cat, cute, fluffy, bed, pillow, cozy"], "m")
        PS._offen["zeit"] = time.time() - PS.OFFEN_S - 1
        text, _ = PS.vorpruefen(["cat, cute, fluffy, bed, pillow, cozy"], "m")
        self.assertIsNotNone(text)

    def test_aus_und_zeigen(self):
        with mock.patch.object(PS, "modus", return_value="aus"):
            self.assertEqual(PS.vorpruefen(["cat, cute, fluffy, bed, pillow, cozy"], "m"), (None, ""))
            self.assertEqual(PS.nachher(["cat"]), "")
        with mock.patch.object(PS, "modus", return_value="zeigen"):
            self.assertEqual(PS.vorpruefen(["cat"], "m"), (None, ""))
            self.assertIn("Sehr kurz", PS.nachher(["cat"]))


class Lerner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "schmied.db"

    def test_bausteine(self):
        b = PS.bausteine("A photo of a woman, shot on 85mm f/1.8 with shallow depth of field.")
        for teil in ("85mm", "f/1.8", "shallow depth", "depth field", "woman"):
            self.assertIn(teil, b)
        self.assertNotIn("of", b)

    def test_lernt_je_modell_und_ersetzt_urteil(self):
        for i in range(3):
            PS.merken([f"C:/b/gut{i}.png"], "v8", "soft window light, 85mm lens portrait", db=self.db)
            PS.merken([f"C:/b/schlecht{i}.png"], "v8", "harsh flash lighting portrait", db=self.db)
            PS.urteil(f"C:/b/gut{i}.png", "gut", db=self.db)
            PS.urteil(f"C:/b/schlecht{i}.png", "schlecht", db=self.db)
        gut, schlecht = PS.gelernt("v8", db=self.db)
        self.assertIn("window light", gut)
        self.assertIn("harsh flash", schlecht)
        self.assertEqual(PS.gelernt("v7", db=self.db), ([], []))        # anderes Modell weiß nichts
        self.assertIn("bei v8", PS.hinweise("v8", db=self.db))
        for i in range(3):                                                # Meinung geändert: zählt um
            PS.urteil(f"C:/b/gut{i}.png", "schlecht", db=self.db)
        gut, _schlecht = PS.gelernt("v8", db=self.db)
        self.assertNotIn("window light", gut)
        with PS._verbinden(self.db) as c:
            zaehler = c.execute("SELECT gut, schlecht FROM baustein WHERE modell = 'v8' AND baustein = "
                                "'window light'").fetchone()
        self.assertEqual(tuple(zaehler), (0, 3))

    def test_unbekanntes_bild_aus_png_daten(self):
        self.assertFalse(PS.urteil("C:/b/x.png", "gut", db=self.db))
        self.assertTrue(PS.urteil("C:/b/x.png", "gut", "v7", "neon glow street", db=self.db))
        self.assertFalse(PS.urteil("C:/b/x.png", "egal", db=self.db))


class PngDaten(unittest.TestCase):
    def test_ergaenzen_ohne_neu_schreiben(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.png"
            meta = PngImagePlugin.PngInfo()
            meta.add_text("prompt", "kern + szene")
            Image.new("RGB", (8, 8), (10, 20, 30)).save(p, pnginfo=meta)
            vorher = p.read_bytes()
            self.assertTrue(PS.png_ergaenzen(p, {"prompt_ki": "Szene mit „Umlaut“ ä", "prompt_vorher": ""}))
            t = PS.png_text(p)
            self.assertEqual((t["prompt"], t["prompt_ki"]), ("kern + szene", "Szene mit „Umlaut“ ä"))
            self.assertNotIn("prompt_vorher", t)
            self.assertTrue(p.read_bytes().startswith(vorher[:-12]))
            with Image.open(p) as im:
                self.assertEqual(im.getpixel((0, 0)), (10, 20, 30))

    def test_kein_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.png"
            p.write_bytes(b"kein bild")
            self.assertFalse(PS.png_ergaenzen(p, {"a": "b"}))
            self.assertEqual(p.read_bytes(), b"kein bild")


class Werkzeug(unittest.TestCase):
    """bild_malen: erst zurück zur Überarbeitung, der zweite Aufruf malt und merkt sich den Vorgänger."""

    def setUp(self):
        PS._offen.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bild = Path(self.tmp.name) / "bild.png"
        meta = PngImagePlugin.PngInfo()
        meta.add_text("model", "krea-test")
        Image.new("RGB", (8, 8)).save(self.bild, pnginfo=meta)
        for ziel, attr, wert in ((imagegen, "missing_reason_aktiv", lambda: None),
                                 (imagegen, "backend", lambda: imagegen.KREA),
                                 (imagegen, "paint", mock.Mock(return_value=str(self.bild))),
                                 (imagegen, "gesicht_nachbessern", lambda *a, **k: None),
                                 (krea, "chosen_model", lambda: "krea-test"),
                                 (actions, "_figur_auto", lambda a, s: ""),
                                 (actions, "_figur", lambda a, s: (s, None, None, "")),
                                 (actions, "_ueberlang", lambda p: 0),
                                 (actions.os, "startfile", lambda p: None),
                                 (PS, "modus", lambda: "an"),
                                 (PS, "DB", Path(self.tmp.name) / "schmied.db")):
            p = mock.patch.object(ziel, attr, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_zurueck_dann_gemalt(self):
        erst = actions._bild_malen({"prompt": "girl, cafe, smile, window, bokeh, coffee"})
        self.assertIsInstance(erst, actions.ActionResult)
        self.assertFalse(erst.ok)
        self.assertIn("noch nicht gemalt", erst.text)
        imagegen.paint.assert_not_called()
        dann = actions._bild_malen({"prompt": GUT})
        self.assertIn("Bild fertig", str(getattr(dann, "text", dann)))
        imagegen.paint.assert_called_once()
        t = PS.png_text(self.bild)
        self.assertEqual(t["prompt_ki"], GUT)
        self.assertEqual(t["prompt_vorher"], "girl, cafe, smile, window, bokeh, coffee")
        self.assertTrue(PS.urteil(self.bild, "gut"))

    def test_anderer_motor_ungeprueft(self):
        with mock.patch.object(imagegen, "backend", lambda: "webui"):
            erg = actions._bild_malen({"prompt": "girl, cafe, smile, window, bokeh, coffee"})
        self.assertIn("Bild fertig", str(getattr(erg, "text", erg)))


if __name__ == "__main__":
    unittest.main()
