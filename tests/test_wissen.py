"""Wissen/ → Vektoren für alle Persönlichkeiten, wortgleiche Sätze nur einmal.

Alles in einem Temp-Ordner; der Encoder ist eine Zählfunktion."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import agentrag           # noqa: E402
import indexdb            # noqa: E402
import memory             # noqa: E402
import wissen             # noqa: E402


class EntdoppelnTests(unittest.TestCase):
    def test_nur_wortgleiche_saetze_sind_doppelt(self):
        absaetze, weg = wissen.entdoppeln([
            "Das Auto ist rot. Der Himmel ist heute grau.",
            "Das Auto ist blau. Der Himmel ist heute grau.",
        ])
        self.assertEqual(["Das Auto ist rot. Der Himmel ist heute grau."], absaetze[0])
        self.assertEqual(["Das Auto ist blau."], absaetze[1])
        self.assertEqual([0, 1], weg)

    def test_zeilenumbruch_zaehlt_wie_leerzeichen(self):
        absaetze, weg = wissen.entdoppeln(["Das Auto ist rot.", "Das Auto\nist   rot."])
        self.assertEqual([], absaetze[1])
        self.assertEqual(1, weg[1])

    def test_kurze_saetze_bleiben(self):
        absaetze, weg = wissen.entdoppeln(["## Zutaten\n\nJa.", "## Zutaten\n\nJa."])
        self.assertEqual(["## Zutaten", "Ja."], absaetze[1])
        self.assertEqual([0, 0], weg)

    def test_doppelt_in_derselben_datei(self):
        absaetze, _ = wissen.entdoppeln(["Bitte den Stecker ziehen.\n\nBitte den Stecker ziehen."])
        self.assertEqual(["Bitte den Stecker ziehen."], absaetze[0])

    def test_brocken_mit_kopf_und_grenze(self):
        absaetze = [("Satz Nummer %d ist hier. " % i) * 5 for i in range(20)]
        stuecke = wissen.brocken("📚 a.md", absaetze, 300)
        self.assertGreater(len(stuecke), 1)
        for s in stuecke:
            self.assertTrue(s.startswith("📚 a.md\n"))
            self.assertLessEqual(len(s), 300)


class _Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._alt = (indexdb._ROOT, indexdb._DB_NEW, indexdb._DB_OLD, memory._embed_texts)
        indexdb._ROOT = root
        indexdb._DB_NEW = root / "learned" / "memory.db"
        indexdb._DB_OLD = root / "learned" / "index.sqlite"
        self.eingebettet = 0

        def embed(texts, **_k):
            self.eingebettet += len(texts)
            return [[1.0, 0.0, float(len(t) % 7)] for t in texts]
        memory._embed_texts = embed
        p = patch.object(memory, "modell_kennung", lambda: "test-kennung")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._zurueck)
        self.ordner = root / "Wissen"
        self.ordner.mkdir()
        self.zeit = 1_789_000_000

    def _zurueck(self):
        indexdb._ROOT, indexdb._DB_NEW, indexdb._DB_OLD, memory._embed_texts = self._alt
        indexdb._status, indexdb._kontext = None, ""
        indexdb._invalidate()

    def _datei(self, rel: str, text: str) -> Path:
        p = self.ordner / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.zeit += 60
        os.utime(p, (self.zeit, self.zeit))
        return p

    def _refs(self) -> list[str]:
        con = indexdb._connect()
        refs = sorted(r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='wissen'"))
        con.close()
        return refs

    def _text(self, ref: str) -> str:
        q = indexdb.quelle_lesen(ref)
        return q["text"] if q else ""


class IngestTests(_Basis):
    def test_aeltere_datei_behaelt_den_satz(self):
        self._datei("alt.md", "Das Auto ist rot. Es steht in der Garage.")
        self._datei("Technik/neu.md", "Das Auto ist rot. Das Auto ist blau und schnell.")
        self.assertGreater(indexdb.ingest_wissen(None), 0)
        self.assertEqual(["wissen:Technik/neu.md", "wissen:alt.md"], self._refs())
        self.assertIn("Das Auto ist rot.", self._text("wissen:alt.md"))
        neu = self._text("wissen:Technik/neu.md")
        self.assertNotIn("Das Auto ist rot.", neu)
        self.assertIn("Das Auto ist blau und schnell.", neu)

    def test_zweiter_lauf_ohne_aenderung_tut_nichts(self):
        self._datei("a.md", "Erster Absatz mit genug Text darin.")
        indexdb.ingest_wissen(None)
        vorher = self.eingebettet
        self.assertEqual(0, indexdb.ingest_wissen(None))
        self.assertEqual(vorher, self.eingebettet)

    def test_loeschen_gibt_den_satz_zurueck(self):
        alt = self._datei("alt.md", "Das Auto ist rot.")
        self._datei("neu.md", "Das Auto ist rot. Noch ein ganz anderer Satz hier.")
        indexdb.ingest_wissen(None)
        alt.unlink()
        indexdb.ingest_wissen(None)
        self.assertEqual(["wissen:neu.md"], self._refs())
        self.assertIn("Das Auto ist rot.", self._text("wissen:neu.md"))

    def test_ganz_doppelte_datei_kommt_nicht_rein(self):
        self._datei("a.md", "Das Auto ist rot. Der Motor ist laut.")
        self._datei("kopie.md", "Das Auto ist rot. Der Motor ist laut.")
        indexdb.ingest_wissen(None)
        self.assertEqual(["wissen:a.md"], self._refs())

    def test_lies_mich_und_fremde_endungen_bleiben_draussen(self):
        self._datei("LIES-MICH.txt", "Hier liegt Wissen für alle Persönlichkeiten.")
        self._datei("bild.png", "kein Text")
        self._datei("notiz.txt", "Die Notiz enthält einen ganzen Satz.")
        indexdb.ingest_wissen(None)
        self.assertEqual(["wissen:notiz.txt"], self._refs())

    def test_reihenfolge_bleibt_und_vektoren_werden_uebernommen(self):
        absaetze = [f"Absatz {i}: " + ("Wort " * 150) + f"Ende {i}." for i in range(6)]
        p = self._datei("lang.md", "\n\n".join(absaetze))
        indexdb.ingest_wissen(None)
        vorher = self.eingebettet
        absaetze[2] = "Absatz 2 ist jetzt ganz kurz und neu."
        p.write_text("\n\n".join(absaetze), encoding="utf-8")
        os.utime(p, (self.zeit + 999, self.zeit + 999))
        indexdb.ingest_wissen(None)
        self.assertLess(self.eingebettet - vorher, 6)          # nur Geändertes neu eingebettet
        text = indexdb.quelle_lesen("wissen:lang.md", max_zeichen=100_000)["text"]
        stellen = [text.find(f"Ende {i}.") if i != 2 else text.find("Absatz 2 ist jetzt") for i in range(6)]
        self.assertTrue(all(s >= 0 for s in stellen))
        self.assertEqual(sorted(stellen), stellen)

    def test_portionen_je_runde(self):
        for i in range(4):
            self._datei(f"d{i}.md", f"Datei Nummer {i} hat einen eigenen Satz.")
        indexdb.ingest_wissen(2)
        self.assertEqual(2, len(self._refs()))
        indexdb.ingest_wissen(2)
        self.assertEqual(4, len(self._refs()))
        self.assertEqual(0, indexdb.ingest_wissen(2))


class WerkzeugTests(_Basis):
    def test_suche_mit_quelle_wissen(self):
        self._datei("Rezepte/brot.md", "Für das Brot braucht man Sauerteig und Roggenmehl.")
        indexdb.ingest_wissen(None)
        aus = agentrag.suchen({"frage": "Sauerteig", "quelle": "wissen"})
        self.assertIn("wissen:Rezepte/brot.md", aus)
        self.assertIn("Wissen", aus)

    def test_lesen_stueckweise(self):
        absaetze = [f"Absatz {i}: " + ("Inhalt " * 120) + "Schluss." for i in range(20)]
        self._datei("lang.md", "\n\n".join(absaetze))
        indexdb.ingest_wissen(None)
        erst = agentrag.lesen({"ref": "wissen:lang.md"})
        self.assertIn("weiter mit ab=", erst)
        self.assertNotIn("Absatz 19:", erst)
        ab = int(erst.split("weiter mit ab=")[1].split()[0])
        zweit = agentrag.lesen({"ref": "wissen:lang.md", "ab": ab})
        self.assertIn(f"hier Abschnitt {ab}–20", zweit)
        self.assertIn("Absatz 19:", zweit)
        self.assertNotIn("weiter mit ab=", zweit)


class ArtNachInhaltTests(_Basis):
    def test_pdf_mit_endung_md_wird_als_pdf_gelesen(self):
        import pdfgen
        p = self.ordner / "buch.md"
        pdfgen.markdown_to_pdf("# Kapitel\n\nIm Anfang war das Wort und das Wort war gut.", self.ordner / "buch.pdf")
        (self.ordner / "buch.pdf").rename(p)
        self.assertTrue(p.read_bytes().startswith(b"%PDF-"))
        indexdb.ingest_wissen(None)
        text = self._text("wissen:buch.md")
        self.assertIn("Im Anfang war das Wort", text)
        self.assertNotIn("%PDF", text)

    def test_binaeres_bleibt_draussen(self):
        (self.ordner / "kaputt.txt").write_bytes(bytes(range(256)) * 40)
        self._datei("gut.md", "Ein ganz normaler Satz steht hier.")
        indexdb.ingest_wissen(None)
        self.assertEqual(["wissen:gut.md"], self._refs())

    def test_utf16_mit_bom(self):
        (self.ordner / "breit.txt").write_bytes("Größe und Übermut gehören zusammen.".encode("utf-16"))
        indexdb.ingest_wissen(None)
        self.assertIn("Größe und Übermut", self._text("wissen:breit.txt"))

    def test_muell_aus_altem_lesen_wird_ersetzt(self):
        self._datei("text.md", "Der richtige Inhalt der Datei steht hier.")
        indexdb._upsert_geordnet("wissen", "wissen:text.md", ["📚 text.md\n%PDF-1.3 x\u009cí]Ë Zeichensalat"])
        indexdb._stand("wissen", "alter-stempel")
        indexdb.ingest_wissen(None)
        text = self._text("wissen:text.md")
        self.assertIn("Der richtige Inhalt", text)
        self.assertNotIn("Zeichensalat", text)


class PromptTests(_Basis):
    def test_titel_liste_aus_dem_gedaechtnis(self):
        self.assertEqual("", wissen.prompt_hinweis())
        self.assertFalse(indexdb._DB_NEW.exists())          # nur nachsehen legt keine DB an
        self._datei("LIES-MICH.txt", "Erklärung für alle hier.")
        self._datei("Technik/anleitung.md", "Ein Satz mit Inhalt.")
        self._datei("brot.md", "Ein anderer Satz mit Inhalt.")
        self.assertEqual("", wissen.prompt_hinweis())         # noch nicht eingelesen
        indexdb.ingest_wissen(None)
        hinweis = wissen.prompt_hinweis()
        self.assertIn("- Technik/anleitung.md", hinweis)
        self.assertIn("- brot.md", hinweis)
        self.assertIn("NICHT mit datei_lesen", hinweis)
        self.assertNotIn("LIES-MICH", hinweis)
        self.assertNotIn("Satz mit Inhalt", hinweis)

    def test_viele_dateien_nur_ordner(self):
        for i in range(wissen.LISTE_MAX + 1):
            self._datei(f"Technik/d{i}.md", f"Datei Nummer {i} mit eigenem Satz.")
        indexdb.ingest_wissen(None)
        hinweis = wissen.prompt_hinweis()
        self.assertIn(f"- Technik/ ({wissen.LISTE_MAX + 1} Dateien)", hinweis)
        self.assertNotIn("d0.md", hinweis)


if __name__ == "__main__":
    unittest.main()
