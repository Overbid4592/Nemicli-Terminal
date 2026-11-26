"""seite_ansehen: nur lokale Dateien, geladen mit NemiView – nie ein Browser (ein Chromium-Start mit frischem
Profil erzeugt einen fehlgeschlagenen Windows-Anmeldeversuch)."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import seite                                           # noqa: E402


class SeiteTests(unittest.TestCase):

    def test_nur_lokale_dateien(self):
        self.assertIsNone(seite.adresse("https://example.com/")[0])
        self.assertIsNone(seite.adresse("http://localhost:8000/")[0])
        self.assertIsNone(seite.adresse("\\\\server\\freigabe\\a.html")[0])

    def test_startet_nemiview_statt_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "index.html"
            html.write_text("<h1>x</h1>", encoding="utf-8")
            gestartet = []

            def run(befehl, **_k):
                gestartet.append(befehl)
                png = Path(befehl[-1])
                png.write_bytes(b"png")
                png.with_suffix(".json").write_text(json.dumps(
                    {"ok": True, "fehler": ["❌ index.html: ReferenceError: x"], "konsole": ["hallo"],
                     "hinweise": ["<canvas> zeigt NemiView nicht an"], "titel": ""}), encoding="utf-8")
                return NS(stderr="", stdout="")
            with patch.object(seite.subprocess, "run", run):
                bild, text = seite.ansehen(str(html), 400, 300)
        befehl = gestartet[0]
        self.assertIn("--seitenbild", befehl)
        self.assertEqual([str(html.resolve()), "400", "300"], befehl[-4:-1])
        self.assertFalse(any(re.search(r"chrome|msedge|brave|vivaldi|headless|user-data-dir", a, re.I)
                             for a in befehl))
        self.assertIsNotNone(bild)
        self.assertIn("❌ index.html: ReferenceError: x", text)
        self.assertIn("· hallo", text)
        self.assertIn("⚠ <canvas>", text)
        self.assertFalse(bild.with_suffix(".json").exists())          # Ergebnisdatei aufgeräumt

    def test_kein_browser_im_quelltext(self):
        quelle = (ROOT / "tools" / "seite.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"chrome|msedge|brave\.exe|vivaldi|--headless|--user-data-dir", quelle, re.I))


class VerweisTests(unittest.TestCase):
    """Bei file:// meldet der Browser fehlende Dateien nicht – fehlende_dateien findet sie."""

    def test_fehlende_lokale_dateien(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "da.css").write_text("", encoding="utf-8")
            (d / "unter ordner").mkdir()
            (d / "unter ordner" / "index.html").write_text("", encoding="utf-8")
            (d / "seite.html").write_text(
                '<link href="da.css"><script src="weg.js"></script><a href="unter%20ordner/">u</a>'
                '<a href="#oben">o</a><a href="https://example.com/x.js">n</a><img src="data:image/png;base64,AA">'
                "<a href='weg.html?x=1'>w</a><a href=\"mailto:user@example.com\">m</a>", encoding="utf-8")
            self.assertEqual(["weg.js", "weg.html"], seite.fehlende_dateien(d / "seite.html"))

    def test_pruefen_faellt_bei_fehlender_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "index.html"
            html.write_text('<script src="weg.js"></script>', encoding="utf-8")
            bild = Path(tmp) / "b.png"
            bild.write_bytes(b"x")
            with patch.object(seite, "_lauf", return_value=(bild, "Seite", [], [])):
                ok, text = seite.pruefen(str(html))
            self.assertFalse(ok)
            self.assertIn("weg.js", text)
            with patch.object(seite, "_lauf", return_value=(None, "Kein Chromium-Browser gefunden", [], [])):
                self.assertFalse(seite.pruefen(str(html))[0])


if __name__ == "__main__":
    unittest.main()
