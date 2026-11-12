"""Tests für engines/sehprotokoll.py und das Protokoll im Bildbeschreiber – ohne Modell und ohne GPU.

python -m unittest tests.test_sehprotokoll
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A                                    # noqa: E402
import bildbeschreiber as B                            # noqa: E402
import sehprotokoll as SP                              # noqa: E402


class ProtokollTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.datei = Path(self._tmp.name) / "protokoll.jsonl"
        self._p = patch.object(SP, "datei", lambda name=None, anlegen=True: self.datei)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_eintragen_und_nachschlagen(self):
        SP.eintragen("C:/x/a.png", "abc", None, "Ein Apfel.", 2.345, "Alpha")
        SP.eintragen("C:/x/a.png", "abc", "Wie viele?", "Einer.", 0.4, "Alpha")
        self.assertEqual(SP.nachschlagen("abc", None, "Alpha")["text"], "Ein Apfel.")
        self.assertEqual(SP.nachschlagen("abc", " Wie viele? ", "Alpha")["text"], "Einer.")
        self.assertIsNone(SP.nachschlagen("abc", None, "Beta"))          # anderes Seh-Modell
        self.assertIsNone(SP.nachschlagen("xyz", None, "Alpha"))         # anderes Bild
        self.assertIsNone(SP.nachschlagen("", None, "Alpha"))
        self.assertEqual(SP.lesen()[0]["dauer_s"], 2.35)

    def test_kaputte_zeile_und_kuerzen(self):
        self.datei.write_text("kein json\n", encoding="utf-8")
        with patch.object(SP, "MAX_EINTRAEGE", 10):
            for i in range(12):
                SP.eintragen(f"C:/x/{i}.png", str(i), None, "t", 1, "Alpha")
        alle = SP.lesen()
        self.assertEqual(len(alle), 10)
        self.assertEqual(alle[-1]["sha"], "11")

    def test_fuer_ki_gerahmt_und_gefiltert(self):
        self.assertIn("leer", SP.fuer_ki())
        SP.eintragen("C:/x/a.png", "1", None, "Ein langer Text " * 20, 1, "Alpha")
        SP.eintragen("C:/x/b.png", "2", None, "Ein Hund.", 1, "Alpha")
        t = SP.fuer_ki()
        self.assertIn("2 Einträge", t)
        self.assertIn("BILDBESCHREIBUNG", t)
        self.assertLess(t.index("b.png"), t.index("a.png"))              # neueste zuerst
        nur_a = SP.fuer_ki(bild="a.png")
        self.assertNotIn("b.png", nur_a)
        self.assertIn("Ein langer Text " * 5, nur_a)                    # voller Text
        self.assertIn("nichts", SP.fuer_ki(bild="c.png"))

    def test_werkzeug(self):
        SP.eintragen("C:/x/a.png", "1", None, "Ein Hund.", 1, "Alpha")
        r = A._bild_protokoll({"anzahl": "x"})
        self.assertTrue(r.ok)
        self.assertIn("a.png", r.text)
        self.assertIn("bild_protokoll", A.ACTIONS)


class BeschreibenMitProtokollTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.datei = Path(self._tmp.name) / "protokoll.jsonl"
        self.bild = Path(self._tmp.name) / "bild.png"
        from PIL import Image
        Image.new("RGB", (4, 4), "red").save(self.bild)
        for p in (patch.object(SP, "datei", lambda name=None, anlegen=True: self.datei),
                  patch.object(B, "aktiv", return_value="Alpha")):
            p.start()
            self.addCleanup(p.stop)

    def test_zweites_mal_aus_dem_protokoll(self):
        with patch.object(B, "fragen_roh", return_value="Rotes Quadrat.") as roh:
            self.assertEqual(B.beschreiben(self.bild), "Rotes Quadrat.")
            self.assertEqual(B.beschreiben(self.bild), "Rotes Quadrat.")
            self.assertEqual(roh.call_count, 1)                          # zweites Mal ohne Modell
            B.beschreiben(self.bild, "Welche Farbe?")
            self.assertEqual(roh.call_count, 2)                          # andere Frage: neu angesehen
        e = [json.loads(z) for z in self.datei.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["frage"] for x in e], ["", "Welche Farbe?"])
        self.assertEqual(e[0]["modell"], "Alpha")
        self.assertEqual(len(e[0]["sha"]), 64)

    def test_leere_antwort_nicht_gemerkt(self):
        with patch.object(B, "fragen_roh", return_value=""):
            self.assertEqual(B.beschreiben(self.bild), "(keine Beschreibung)")
        self.assertEqual(SP.lesen(), [])


class BerichtTests(unittest.TestCase):

    def test_bericht_nennt_ordner_dateien_und_fehler(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Alpha"
            d.mkdir()
            (d / "modell.gguf").write_bytes(b"x" * 1234)
            (d / "mmproj-F16.gguf").write_bytes(b"x")
            with patch.object(B, "ORDNER", Path(tmp)), patch.object(B.gguflokal, "projektor_bekannt", return_value=True), \
                    patch.object(B.gguflokal, "_config", return_value={}), patch.object(SP, "lesen", return_value=[]), \
                    patch.object(B, "_letzter_fehler", "12:00:00\nRuntimeError: kaputt"):
                t = B.bericht_text("ollama_cloud:minimax-m2.7", False)
        self.assertIn("[Alpha] Seh-Modell ✓", t)
        self.assertIn("1.234 Bytes", t)
        self.assertIn("mmproj-F16.gguf", t)
        self.assertIn("Beschreiber springt ein: ja", t)
        self.assertIn("RuntimeError: kaputt", t)


if __name__ == "__main__":
    unittest.main()
