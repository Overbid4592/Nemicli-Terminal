"""Rolle halten: Erkennung der dritten Person, Stups, Rollen-Anker je Nachricht und die gesperrte Sprechweise."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import charakter          # noqa: E402
import paths              # noqa: E402
import persona            # noqa: E402
import profilordner       # noqa: E402
import rolle              # noqa: E402

KERN = "an adult woman with long red hair and green eyes"


class DrittePerson(unittest.TestCase):
    def treffer(self, text, frage=""):
        return rolle.dritte_person(text, "PersonA – Beispiel", frage)

    def test_erkennt_aktion_und_satz(self):
        self.assertIsNotNone(self.treffer("Oh! *PersonA lächelt dich an* Gern."))
        self.assertIsNotNone(self.treffer("Das klingt gut. PersonA findet die Idee super."))
        self.assertIsNotNone(self.treffer("**PersonA** grinst breit."))
        self.assertIsNotNone(self.treffer("PersonA ist für dich da."))

    def test_ich_form_und_anrede_bleiben_frei(self):
        for text in ("Ich bin **PersonA**, schön dich zu sehen.", "*lächelt* Ich finde das gut.",
                     "PersonA, ist das okay?", "Hier ist PersonA – was brauchst du?", "Ich mag PersonAs Bild."):
            self.assertIsNone(self.treffer(text), text)

    def test_code_zaehlt_nicht(self):
        self.assertIsNone(self.treffer("Hier:\n```\nPersonA ist ein Name\n```\nund `PersonA hat` fertig."))

    def test_gewuenschte_erzaehlung(self):
        self.assertIsNone(self.treffer("PersonA geht los.", "Erzähl es in der dritten Person"))

    def test_stups_nennt_stelle_und_ich_form(self):
        s = rolle.stups("PersonA lächelt", "PersonA – Beispiel")
        self.assertIn("„PersonA lächelt“", s)
        self.assertIn("Ich-Form", s)
        self.assertIn("*PersonA lächelt*", s)


class _Daten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(paths, "DATEN", Path(tmp.name)),
                  mock.patch.object(profilordner, "aktiv", lambda: "PersonA"),
                  mock.patch("persoenlichkeiten.active",
                             return_value=types.SimpleNamespace(name="PersonA", key="persona"))):
            p.start()
            self.addCleanup(p.stop)
        charakter.schreiben({"name": "PersonA", "kern": KERN, "seed_referenz": 42, "stil": "photo",
                             "outfits": {}, "posen": {}, "ausdruecke": {}, "frisuren": {}, "regeln": []}, "PersonA")


class Sprechweise(_Daten):
    def test_setzen_bleibt_bei_kern_aenderung(self):
        charakter.sprechweise_setzen("frech, duzt, kurze Sätze", "PersonA")
        charakter.kern_setzen("an adult woman with short black hair", "PersonA")
        self.assertEqual("frech, duzt, kurze Sätze", charakter.sprechweise("PersonA"))
        self.assertTrue(charakter.kern_sicherung("PersonA").is_file())

    def test_gesperrt_aendert_ki_nicht_ohne_ja(self):
        charakter.sprechweise_setzen("frech, duzt", "PersonA")
        charakter.kern_sperren(True, "PersonA")
        self.assertIn("Sprechweise", charakter.kern_aenderung({"sprechweise": "förmlich, siezt"}))
        self.assertIsNone(charakter.kern_aenderung({"sprechweise": "frech, duzt"}))
        charakter.aendern({"sprechweise": "förmlich, siezt"})
        self.assertEqual("frech, duzt", charakter.sprechweise())
        charakter.aendern({"sprechweise": "förmlich, siezt", "_kern_ja": True})
        self.assertEqual("förmlich, siezt", charakter.sprechweise())

    def test_offen_aendert_ki_mit_vorschau(self):
        self.assertIn("SPRECHWEISE ÄNDERN", charakter.vorschau({"sprechweise": "ruhig"}))
        charakter.aendern({"sprechweise": "ruhig"})
        self.assertEqual("ruhig", charakter.sprechweise())
        self.assertIn("sprechweise: ruhig", charakter.zeigen(charakter.laden()))

    def test_sicherheit_fragt_bei_gesperrter_sprechweise(self):
        import sicherheit
        charakter.sprechweise_setzen("frech", "PersonA")
        charakter.kern_sperren(True, "PersonA")
        self.assertIsNotNone(sicherheit.steuerdatei_grund({"tool": "charakter_aendern", "sprechweise": "brav"}))

    def test_anker_nimmt_name_und_sprechweise(self):
        self.assertIn("Du bist PersonA", persona.rollen_anker())
        self.assertNotIn("Sprechweise", persona.rollen_anker())
        charakter.sprechweise_setzen("frech, duzt", "PersonA")
        self.assertIn("Sprechweise: frech, duzt", persona.rollen_anker())


class Einbau(unittest.TestCase):
    def code(self, datei):
        return (ROOT / datei).read_text(encoding="utf-8")

    def test_grundregel(self):
        self.assertIn("in der Ich-Form", persona._GRUNDREGELN)

    def test_anker_in_jedem_motor_getrennt_vom_denken(self):
        cloud = self.code("engines/cloud.py")
        self.assertIn("anker, self.rollen_anker = self.rollen_anker, \"\"", cloud)
        self.assertIn("gebeten = bool(hinweis) and", cloud)        # der Anker ist kein erbetenes Nachdenken
        self.assertIn("anker, self.rollen_anker = self.rollen_anker, \"\"", self.code("engines/gguflokal.py"))
        self.assertIn("self.rollen_anker", self.code("engines/chat.py"))

    def test_schleife_stupst_einmal(self):
        m = self.code("main.py")
        self.assertIn("backend.rollen_anker = persona.rollen_anker()", m)
        self.assertIn("not rolle_gestupst and", m)
        self.assertIn("rolle.stups(stelle", m)

    def test_kern_menue_hat_sprechweise(self):
        self.assertIn('("sprechweise", "🗣 Sprechweise ändern")', self.code("main_dienste.py"))


if __name__ == "__main__":
    unittest.main()
