"""/-Menü-Suche und Bedienhilfe für das Modell.

python -m unittest tests.test_befehle_hilfe
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A                                    # noqa: E402
import commands as C                                   # noqa: E402
import persona                                         # noqa: E402


class SucheTests(unittest.TestCase):
    def test_nur_slash_liefert_alle(self):                # bis auf die versteckten Gegenstücke
        self.assertEqual(len(C.find_commands("/")), len(C.COMMAND_LIST) - len(C.VERSTECKT))

    def test_namensanfang_zuerst(self):
        namen = [n for n, _ in C.find_commands("/bild")]
        self.assertEqual(namen[:2], ["/bild", "/bildmodel"])

    def test_findet_ueber_kurztext(self):
        self.assertIn("/model", [n for n, _ in C.find_commands("/modell")])

    def test_umlaute_egal(self):
        self.assertIn("/persönlichkeiten", [n for n, _ in C.find_commands("/persoen")])

    def test_kein_treffer(self):
        self.assertEqual(C.find_commands("/xyzq"), [])


class EinfacheSucheTests(unittest.TestCase):
    """„/“ + ein Wort findet auch Unterpunkte jedes Befehls – ohne den Befehl davor zu kennen, mit Tippfehlern."""

    def erster(self, frage):
        return C.vorschlaege(frage)[0][0].strip()

    def test_unterpunkt_ohne_befehl_davor(self):
        self.assertEqual(self.erster("/fullscan"), "/wache fullscan")
        self.assertEqual(self.erster("/vollscan"), "/wache fullscan")       # über die Beschreibung
        self.assertEqual(self.erster("/termin"), "/wache termin")           # vor /terminal
        self.assertEqual(self.erster("/schutzliste"), "/web schutz")

    def test_tippfehler_und_mehrere_woerter(self):
        self.assertEqual(self.erster("/volscan"), "/wache fullscan")
        self.assertEqual(self.erster("/wachter fullscann"), "/wache fullscan")
        self.assertEqual(self.erster("/ki tracker"), "/wache ki")
        self.assertEqual(self.erster("/musick"), "/musik")

    def test_befehle_selbst_bleiben_vorne(self):
        self.assertEqual([b for b, _ in C.vorschlaege("/bild")][:2], ["/bild", "/bildmodel"])
        self.assertEqual(self.erster("/wache"), "/wache")
        self.assertEqual(C.vorschlaege("/xyzq"), [])
        self.assertEqual(len(C.vorschlaege("/")), len(C.find_commands("/")))

    def test_menue_beim_tippen(self):
        from prompt_toolkit.document import Document
        comp = C.SlashCompleter(get_themes=lambda: {}, get_models=lambda: {})

        def vorschlag(text):
            return [c.text.strip() for c in comp.get_completions(Document(text, len(text)), None)]

        self.assertEqual(vorschlag("/fullscan")[0], "/wache fullscan")
        self.assertEqual(vorschlag("/wachter fullscann")[0], "/wache fullscan")  # unbekannter Befehl + Wort
        c = next(iter(comp.get_completions(Document("/fullscan", 9), None)))
        self.assertEqual(c.start_position, -len("/fullscan"))                    # ersetzt die ganze Eingabe
        self.assertEqual(vorschlag("/kontextmenue a"), ["an", "aus"])           # bekannter Befehl: wie bisher


class HilfeTests(unittest.TestCase):
    def test_uebersicht_hat_alle_befehle(self):
        text = persona.anleitung("nemicli")
        for c in C.COMMAND_LIST:
            self.assertIn(c.name, text)

    def test_einzelner_befehl_mit_auswahl(self):
        text = persona.anleitung("/wache")
        self.assertIn("## /wache", text)
        self.assertIn("/wache alarme", text)

    def test_ueber_werkzeug(self):
        self.assertTrue(A._anleitung_lesen({"thema": "nemicli"}).ok)
        self.assertFalse(A._anleitung_lesen({"thema": "/xyzq"}).ok)

    def test_stichwort_schickt_hilfe_mit(self):
        self.assertIn("nemicli", persona.passende_anleitungen("Wie stelle ich das Farbschema um?"))


if __name__ == "__main__":
    unittest.main()
