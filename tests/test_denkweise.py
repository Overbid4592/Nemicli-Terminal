"""Denkweise je Persönlichkeit (core/denkweise.py, /denken, Werkzeug denkweise), Handlung und Ankündigung im
Denkwerk, Denk-Budget je Stärke beim eigenen Motor – ohne Modell.

python -m unittest tests.test_denkweise
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import denkweise as W   # noqa: E402
import denkwerk as D    # noqa: E402


class MitConfig(unittest.TestCase):
    def setUp(self):
        self.daten = {"persoenlichkeit": "test_user"}
        import config
        self.p = [mock.patch.object(config, "load", side_effect=lambda: dict(self.daten)),
                  mock.patch.object(config, "update", side_effect=lambda **kw: self.daten.update(kw) or True)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()


class Stufe(MitConfig):

    def test_vorgabe_auto_und_alte_config(self):
        self.assertEqual("auto", W.stufe())
        self.daten["gguf_denken"] = True
        self.assertEqual("an", W.stufe())

    def test_je_persoenlichkeit(self):
        self.assertEqual("an", W.setzen("immer"))
        self.assertEqual({"test_user": "an"}, self.daten["denkweise"])
        self.daten["persoenlichkeit"] = "example"
        self.assertEqual("auto", W.stufe())
        W.setzen("aus")
        self.assertEqual("aus", W.stufe())
        self.assertEqual("an", W.stufe("test_user"))

    def test_unbekannt(self):
        with self.assertRaises(ValueError):
            W.setzen("riesig")
        self.daten["denkweise"] = {"test_user": "x"}
        self.assertEqual("auto", W.stufe())

    def test_runde_folgt_der_denkweise(self):
        self.assertEqual("einfach", D.runde("hallo").stufe)
        W.setzen("an")
        self.assertEqual("mittel", D.runde("hallo").stufe)
        W.setzen("aus")
        self.assertIsNone(D.runde("Berechne 17*23 und erkläre den Rechenweg"))
        self.assertIsNone(D.nachfass_runde("Ich male jetzt."))


class Nativ(MitConfig):
    QWEN = NS(denken_schaltbar=True, denken_im_system=False)
    GEMMA = NS(denken_schaltbar=True, denken_im_system=True)
    OHNE = NS(denken_schaltbar=False, denken_im_system=False)

    def test_auto_nur_wenn_es_etwas_bringt(self):
        plaudern, bitte = D.Runde("hallo", "einfach"), D.Runde("zeig dich", "einfach", handlung=True)
        self.assertFalse(W.nativ(self.QWEN, plaudern))
        self.assertTrue(W.nativ(self.QWEN, bitte))
        self.assertTrue(W.nativ(self.QWEN, D.Runde("x", "schwer")))
        self.assertFalse(W.nativ(self.QWEN, None))
        self.assertTrue(W.nativ(self.GEMMA, plaudern))          # Schalter in der Anleitung: bleibt an
        self.assertFalse(W.nativ(self.OHNE, bitte))

    def test_an_und_aus(self):
        plaudern = D.Runde("hallo", "einfach")
        self.assertTrue(W.nativ(self.QWEN, plaudern, "an"))
        self.assertFalse(W.nativ(self.GEMMA, D.Runde("x", "schwer"), "aus"))

    def test_vorgabe(self):
        self.assertFalse(W.vorgabe(self.QWEN))
        self.assertTrue(W.vorgabe(self.GEMMA))
        self.assertTrue(W.vorgabe(self.QWEN, "an"))
        self.assertFalse(W.vorgabe(self.GEMMA, "aus"))


class Erkennen(unittest.TestCase):

    def test_handlung(self):
        for text in ("Ja perfekt dann zeig dich mal süße =)", "oh probier nochmal =)", "mal mir ein Bild",
                     "Also bisher sehe ich kein bild süße =)", "lies die Datei"):
            self.assertTrue(D.handlung(text), text)
        for text in ("Hast du mal Zeit?", "Und bist du noch wach =) <3", "Ja das sollst du entscheiden =)"):
            self.assertFalse(D.handlung(text), text)

    def test_ankuendigung(self):
        for text, satz in (("Klar! Ich male jetzt das Bild und zeige es dir! 🎨✨",
                            "Ich male jetzt das Bild und zeige es dir!"),
                           ("Oh nein. Ich werde das jetzt sofort machen.", "Ich werde das jetzt sofort machen."),
                           ("Ich werde jetzt ein Bild von mir malen.", "Ich werde jetzt ein Bild von mir malen."),
                           ("Perfekt! Ich lese jetzt die Anleitung! 📚", "Ich lese jetzt die Anleitung!")):
            self.assertEqual(satz, D.ankuendigung(text), text)
        for text in ("Ich male dir gleich eins. Soll es rot sein? ❤", "Ich habe jetzt gemalt.",
                     "Ich mag dich sehr! 💖", "<think>Ich male jetzt</think>Hallo!", ""):
            self.assertIsNone(D.ankuendigung(text), text)

    def test_absage_angebot_und_blick_sind_keine_ankuendigung(self):
        for text in ("Das lass ich sitzen – Szenen so direkt male ich nicht, das ist meine Grenze.",
                     "Sag mir einfach, was du möchtest, und ich mache es *sofort*!",
                     "Links wirft die Lampe Licht über die Szene, und ich schaue dich direkt an.",
                     "Ich schaue direkt in die Kamera.", "Das male ich jetzt nicht.",
                     "Wenn du magst, ich male sofort eins.", "I will not paint that now."):
            self.assertIsNone(D.ankuendigung(text), text)
        self.assertEqual("Ich mache es jetzt.", D.ankuendigung("Das male ich nicht. Ich mache es jetzt."))
        self.assertEqual("Ich male direkt ein Bild.", D.ankuendigung("Ich male direkt ein Bild."))
        self.assertEqual("Ich habe es nicht gemacht, ich mache es jetzt.",
                         D.ankuendigung("Ich habe es nicht gemacht, ich mache es jetzt."))

    def test_englisch(self):
        for text in ("show me a picture", "paint yourself", "try again please", "open the file"):
            self.assertTrue(D.handlung(text), text)
        self.assertFalse(D.handlung("How are you today?"))
        for text, satz in (("Sure! I'll paint it for you now! 🎨", "I'll paint it for you now!"),
                           ("Okay. Let me check the folder.", "Let me check the folder."),
                           ("I" + chr(0x2019) + "m painting your picture.", "I" + chr(0x2019) + "m painting your picture."),
                           ("I am going to draw you right away.", "I am going to draw you right away.")):
            self.assertEqual(satz, D.ankuendigung(text), text)
        for text in ("I'll paint it later if you like. Which color?", "I love you so much! 💖",
                     "I painted it yesterday.", "Let me know what you think."):
            self.assertIsNone(D.ankuendigung(text), text)

    def test_nachfassen_laesst_ehrlich_nein_sagen(self):
        t = D.nachfassen("Ich male jetzt.")
        self.assertIn("„Ich male jetzt.“", t)
        self.assertIn("ehrlich", t)


class Format(unittest.TestCase):

    def _fmt(self, fmt, special=(), chatml_denkt=False):
        from ggufengine.chat import ChatFormatter
        f = object.__new__(ChatFormatter)
        f.fmt, f.tok, f.chatml_denkt, f.thinking = fmt, NS(special=dict.fromkeys(special, 1)), chatml_denkt, False
        return f

    def test_schaltbar_und_im_system(self):
        self.assertTrue(self._fmt("chatml", chatml_denkt=True).denken_schaltbar)
        self.assertFalse(self._fmt("chatml").denken_schaltbar)               # Qwen3-VL Instruct
        self.assertFalse(self._fmt("chatml", chatml_denkt=True).denken_im_system)
        self.assertTrue(self._fmt("gemma4", ("<|think|>",)).denken_schaltbar)
        self.assertFalse(self._fmt("gemma4").denken_schaltbar)
        for fmt in ("harmony", "bailing", "smollm3"):
            self.assertTrue(self._fmt(fmt).denken_schaltbar and self._fmt(fmt).denken_im_system, fmt)
        self.assertFalse(self._fmt("deepseek").denken_schaltbar)


class Staerke(unittest.TestCase):

    def test_budget_je_staerke(self):
        import gguflokal as G
        with mock.patch.object(G, "_config", return_value={}):
            self.assertEqual(1024, G.denk_budget("schnell"))
            self.assertEqual(G.VORGABE_DENK_BUDGET, G.denk_budget(None))
            self.assertIsNone(G.denk_budget("max"))
        with mock.patch.object(G, "_config", return_value={"gguf_denk_budget": 500}):
            self.assertEqual(500, G.denk_budget("max"))                      # feste Config geht vor

    def test_staerke_beim_eigenen_motor(self):
        import models as M
        self.assertTrue(M.strength_supported("gguf:testmodell"))
        menue = M.strength_menu("gguf:testmodell")
        self.assertEqual(set(M.STRENGTHS), set(menue))
        self.assertIn("1.024", menue["schnell"])
        self.assertIn("ohne Grenze", menue["max"])
        self.assertIn("/denken", M.strength_note("gguf:testmodell", "normal"))


class Werkzeug(MitConfig):

    def test_werkzeug_menue_und_befehl(self):
        import actions
        import commands
        import plan
        self.assertIn("auto", str(actions._denkweise({})))
        self.assertTrue(str(actions._denkweise({"stufe": "an"})).startswith("Geändert."))
        self.assertEqual({"test_user": "an"}, self.daten["denkweise"])
        self.assertFalse(actions._denkweise({"stufe": "riesig"}).ok)
        self.assertFalse(actions.ACTIONS["denkweise"]["confirm"])
        self.assertIn("denkweise", plan.BEGLEITEN)
        self.assertIn("/denken", commands.menue_befehle())


if __name__ == "__main__":
    unittest.main()
