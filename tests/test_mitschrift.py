"""Offline-Tests für F12: das ganze Gespräch als Markdown sichern.

python -m unittest discover -s tests -p test_mitschrift.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import mitschrift as MS      # noqa: E402


KOPF = {"chat": 7, "model": "testmodell", "persona": "Testperson",
        "modus": "Normal", "ordner": r"C:\irgendwo"}


class Mitschreiben(unittest.TestCase):
    def setUp(self):
        MS.leeren()
        MS.setze_quelle(lambda: dict(KOPF))

    def tearDown(self):
        MS.leeren()
        MS.setze_quelle(None)

    def test_am_anfang_ist_nichts_da(self):
        self.assertTrue(MS.leer())
        self.assertEqual(MS.anzahl(), 0)

    def test_leere_beitraege_werden_nicht_gesammelt(self):
        MS.nutzer("")
        MS.nutzer("   ")
        MS.assistent("", "")
        MS.notiz("")
        self.assertEqual(MS.anzahl(), 0)

    def test_antwort_ohne_text_aber_mit_denken_zaehlt(self):
        # Genau der Fall, den F2 zeigt: sie denkt, sagt aber (noch) nichts.
        MS.assistent("", "ich überlege noch")
        self.assertEqual(MS.anzahl(), 1)

    def test_leeren_setzt_zurueck(self):
        MS.nutzer("hallo")
        MS.leeren()
        self.assertTrue(MS.leer())

    def test_alles_landet_in_der_reihenfolge(self):
        MS.nutzer("frage")
        MS.assistent("antwort", "denken", 1.5)
        MS.aktion("datei_lesen", "Datei lesen: x.py", "Inhalt", True)
        MS.notiz("abgebrochen")
        self.assertEqual([e["art"] for e in MS._eintraege],
                         ["nutzer", "assistent", "aktion", "notiz"])


class MarkdownBauen(unittest.TestCase):
    def setUp(self):
        MS.leeren()
        MS.setze_quelle(lambda: dict(KOPF))

    def tearDown(self):
        MS.leeren()
        MS.setze_quelle(None)

    def test_kopfdaten_stehen_oben(self):
        MS.nutzer("hallo")
        md = MS.als_markdown()
        for wert in ("Testperson", "testmodell", "Normal", r"C:\irgendwo",
                     "/resume 7"):
            self.assertIn(wert, md)

    def test_denktext_ist_drin_und_zugeklappt(self):
        MS.nutzer("frage")
        MS.assistent("die Antwort", "SO DENKE ICH", 2.0)
        md = MS.als_markdown()
        self.assertIn("SO DENKE ICH", md)
        self.assertIn("<details><summary>💭 Denktext</summary>", md)
        self.assertIn("2.0 s gedacht", md)
        self.assertIn("die Antwort", md)

    def test_runden_werden_durchnummeriert(self):
        for i in range(3):
            MS.nutzer(f"frage {i}")
            MS.assistent(f"antwort {i}")
        md = MS.als_markdown()
        self.assertIn("## 1. Du", md)
        self.assertIn("## 3. Du", md)

    def test_aktionen_mit_haekchen_und_kreuz(self):
        MS.aktion("datei_lesen", "Datei lesen: x", "ok", True)
        MS.aktion("befehl", "Befehl: rm", "abgelehnt", False)
        MS.aktion("web_lesen", "Seite lesen", "unbekannt", None)
        md = MS.als_markdown()
        self.assertIn("✓ **Aktion** `datei_lesen`", md)
        self.assertIn("✗ **Aktion** `befehl`", md)
        self.assertIn("· **Aktion** `web_lesen`", md)

    def test_code_zaeune_im_text_zerreissen_die_datei_nicht(self):
        # Denktext mit eigenen ```-Zäunen muss sauber eingebettet werden.
        MS.assistent("egal", "hier ist code:\n```python\nprint(1)\n```\nfertig")
        md = MS.als_markdown()
        self.assertIn("````", md)            # der Zaun wurde verlängert
        self.assertIn("print(1)", md)

    def test_kopf_laesst_sich_ueberschreiben(self):
        MS.nutzer("x")
        md = MS.als_markdown({"persona": "Jemand anders"})
        self.assertIn("Jemand anders", md)

    def test_ohne_quelle_geht_es_auch(self):
        MS.setze_quelle(None)
        MS.nutzer("x")
        self.assertIn("Gesichert:", MS.als_markdown())

    def test_kaputte_quelle_kippt_nichts_um(self):
        def boese():
            raise RuntimeError("nope")
        MS.setze_quelle(boese)
        MS.nutzer("x")
        self.assertIn("Gesichert:", MS.als_markdown())


class Speichern(unittest.TestCase):
    def setUp(self):
        MS.leeren()
        MS.setze_quelle(lambda: dict(KOPF))
        self._tmp = tempfile.TemporaryDirectory()
        self.ordner = Path(self._tmp.name)

    def tearDown(self):
        MS.leeren()
        MS.setze_quelle(None)
        self._tmp.cleanup()

    def test_leeres_gespraech_legt_keine_datei_an(self):
        with self.assertRaises(ValueError):
            MS.speichern(ordner=self.ordner)
        self.assertEqual(list(self.ordner.iterdir()), [])

    def test_datei_wird_geschrieben(self):
        MS.nutzer("frage")
        MS.assistent("antwort", "denken")
        p = MS.speichern(ordner=self.ordner)
        self.assertTrue(p.exists())
        self.assertEqual(p.parent, self.ordner)
        text = p.read_text(encoding="utf-8")
        self.assertIn("frage", text)
        self.assertIn("denken", text)

    def test_dateiname_nennt_chat_und_zeit(self):
        name = MS.dateiname()
        self.assertTrue(name.startswith("Chat-007_"), name)
        self.assertTrue(name.endswith(".md"))

    def test_ohne_chatnummer_bleibt_nur_der_zeitstempel(self):
        MS.setze_quelle(lambda: {})
        name = MS.dateiname()
        self.assertFalse(name.startswith("Chat-"))
        self.assertTrue(name.endswith(".md"))

    def test_zweimal_speichern_gibt_zwei_dateien(self):
        MS.nutzer("frage")
        a = MS.speichern(ordner=self.ordner, kopf={"chat": 1})
        b = MS.speichern(ordner=self.ordner, kopf={"chat": 2})
        self.assertNotEqual(a, b)
        self.assertEqual(len(list(self.ordner.glob("*.md"))), 2)

    def test_ordner_wird_angelegt_wenn_er_fehlt(self):
        MS.nutzer("frage")
        tief = self.ordner / "neu" / "tiefer"
        p = MS.speichern(ordner=tief)
        self.assertTrue(p.exists())


class Verdrahtung(unittest.TestCase):
    """F12 muss in beiden Oberflächen ankommen, und der Ordner muss entstehen."""

    def test_f12_ist_in_der_textual_oberflaeche_gebunden(self):
        code = (ROOT / "ui" / "screen_tx.py").read_text(encoding="utf-8")
        self.assertIn('Binding("f12", "mitschnitt"', code)
        self.assertIn("def action_mitschnitt", code)

    def test_f12_ist_in_der_alten_oberflaeche_gebunden(self):
        code = (ROOT / "ui" / "screen.py").read_text(encoding="utf-8")
        self.assertIn('kb.add("f12"', code)

    def test_f12_steht_in_der_tastenzeile(self):
        code = (ROOT / "ui" / "ui.py").read_text(encoding="utf-8")
        self.assertIn('("F12", "Chat sichern")', code)

    def test_ordner_gehoert_zum_grundgeruest(self):
        import paths
        self.assertIn("Gespraeche", paths._DIRS)

    def test_gespraeche_sind_gitignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("Gespraeche/", ignore)

    def test_main_schreibt_frage_antwort_und_aktion_mit(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("mitschrift.nutzer(user_text)", code)
        self.assertIn("mitschrift.assistent(display, thinking_txt", code)
        self.assertIn("mitschrift.aktion(tool,", code)
        # Neuer/fortgesetzter Chat fängt einen neuen Mitschnitt an.
        self.assertIn("mitschrift.leeren()", code)


if __name__ == "__main__":
    unittest.main()
