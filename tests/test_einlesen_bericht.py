"""Einlesen im Debugbericht (Anlass, Füllstand, Ergebnis) und das verkleinerte Spiel-Foto der Kugel."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for pfad in (ROOT, *(ROOT / sub for sub in ("core", "engines", "tools", "ui"))):
    sys.path.insert(0, str(pfad))

import bibkind        # noqa: E402
import debugbericht   # noqa: E402
import einlesen       # noqa: E402
import main as MD     # noqa: E402


class Bericht(unittest.TestCase):
    def setUp(self):
        self.zeilen = []
        for p in (mock.patch.object(debugbericht, "ereignis", lambda t, **k: self.zeilen.append(t)),
                  mock.patch.dict(MD._BIB, {"anlass": None, "gemeldet": None, "gewuenscht": False})):
            p.start()
            self.addCleanup(p.stop)

    def _lauf(self, **art):
        ergebnis = {"n": 3, "err": None, "mit_vektor": 240, "neu": 7}
        with mock.patch.object(bibkind, "laufen", return_value=ergebnis), \
                mock.patch.object(einlesen, "stand", side_effect=[99_500, 120]):
            self.assertIs(ergebnis, MD._bibliothekar_lauf(1, on_status=None, **art))
        return " | ".join(self.zeilen)

    def test_befehl_nennt_anlass_fuellstand_und_ergebnis(self):
        t = self._lauf(alles=True)
        self.assertIn("Einlesen startet (/einlesen), Füllstand 99.500 Token", t)
        self.assertIn("Füllstand 99.500 → 120 Token, 7 Brocken neu, 240 mit Vektor", t)

    def test_voller_speicher_und_aufgeraeumter_kontext(self):
        MD._BIB["anlass"] = "voll"
        self.assertIn("(voll)", self._lauf(alles=True))
        self.assertIsNone(MD._BIB["anlass"])
        self.zeilen.clear()
        self.assertIn("(Kontext aufgeräumt)", self._lauf(alles=False, erzwingen=True))

    def test_fehler_steht_im_bericht(self):
        with mock.patch.object(bibkind, "laufen", return_value={"n": 0, "err": "kaputt", "mit_vektor": 0, "neu": 0}), \
                mock.patch.object(einlesen, "stand", return_value=100_000):
            MD._bibliothekar_lauf(1, True, None)
        self.assertIn("Fehler: kaputt", self.zeilen[-1])

    def test_fuellstand_nur_bei_bewegung(self):
        with mock.patch.object(MD, "_speicher_zeigen"), mock.patch.object(MD, "einlesen_vormerken"):
            for token in (10_000, 12_000, 15_500):
                with mock.patch.object(einlesen, "stand", return_value=token):
                    asyncio.run(MD.speicher_pruefen())
        self.assertEqual(["Einlese-Speicher: 10.000 Token", "Einlese-Speicher: 15.500 Token"], self.zeilen)

    def test_sammeln_ohne_modell_bleibt_still(self):
        with mock.patch.object(bibkind, "lauf_hier", return_value={"n": 0, "err": None}):
            MD._bibliothekar_lauf(1, False, None)
        self.assertEqual([], self.zeilen)


class Foto(unittest.TestCase):
    def test_verkleinert_und_lesbar(self):
        from PIL import Image
        from PySide6.QtGui import QColor, QImage
        from wache import oberflaeche as O
        bild = QImage(3840, 2160, QImage.Format.Format_RGB32)
        bild.fill(QColor(90, 140, 60))
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "unter" / "foto.png"
            self.assertEqual(ziel, O.foto_speichern(bild, ziel))
            with Image.open(ziel) as im:
                self.assertEqual((1600, 900), im.size)
                self.assertEqual((90, 140, 60), im.convert("RGB").getpixel((800, 450)))


if __name__ == "__main__":
    unittest.main()
