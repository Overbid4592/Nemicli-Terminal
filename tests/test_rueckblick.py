"""Tagesrückblick: Bilder des Tages, Format, Länge, Bildfolge mit Titelkarte, Werkzeug tagesrueckblick."""
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("", "core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np                     # noqa: E402
from PIL import Image                  # noqa: E402

import actions                         # noqa: E402
import rueckblick as R                 # noqa: E402
import videodatei                      # noqa: E402


def _bild(ordner: Path, name: str, groesse=(832, 1216), alter_tage: int = 0) -> Path:
    p = ordner / name
    Image.new("RGB", groesse, (100, 150, 200)).save(p)
    if alter_tage:
        t = time.time() - alter_tage * 86400
        os.utime(p, (t, t))
    return p


class Grundlagen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_bilder_von_heute(self):
        heute = [_bild(self.tmp, f"h{i}.png") for i in range(3)]
        _bild(self.tmp, "gestern.png", alter_tage=1)
        (self.tmp / "notiz.txt").write_text("x")
        self.assertEqual(set(R.bilder_von(self.tmp)), set(heute))
        self.assertEqual([p.name for p in R.bilder_von(self.tmp, date.today() - timedelta(days=1))], ["gestern.png"])
        viele = [_bild(self.tmp, f"v{i:02d}.png") for i in range(20)]
        self.assertEqual(len(R.bilder_von(self.tmp, max_n=5)), 5)
        self.assertTrue(viele)

    def test_format_und_dauer(self):
        hoch = [_bild(self.tmp, f"{i}.png") for i in range(2)]
        quer = _bild(self.tmp, "q.png", (1216, 832))
        self.assertEqual(R.format_waehlen(hoch + [quer]), (720, 1280))
        self.assertEqual(R.format_waehlen([quer]), (1280, 720))
        self.assertEqual(R.dauer(4, 0), R.TITEL_SEKUNDEN + 4 * R.MIN_JE_BILD)   # ohne Stimme: Mindestzeit
        self.assertEqual(R.dauer(2, 40), 40.8)                                   # Stimme bestimmt die Länge
        self.assertEqual(R.dauer(2, 999), R.MAX_SEKUNDEN)

    def test_bildfolge(self):
        bilder = [_bild(self.tmp, f"{i}.png") for i in range(2)]
        folge = list(R.bilderfolge(bilder, 10.0, (360, 640), "Titel", "17. Oktober 2026"))
        self.assertEqual(folge[0].shape, (640, 360, 3))
        self.assertEqual(len(folge), round(R.TITEL_SEKUNDEN * R.FPS) + 2 * round(((10 - R.TITEL_SEKUNDEN) / 2) * R.FPS))
        self.assertLess(int(folge[0].mean()), 60)                  # Titelkarte ist dunkel
        self.assertGreater(int(folge[-1][320, 180, 2]), 150)        # zuletzt das blaue Bild

    def test_tag_lesen(self):
        self.assertEqual(actions._tag_lesen("heute"), date.today())
        self.assertEqual(actions._tag_lesen("gestern"), date.today() - timedelta(days=1))
        self.assertEqual(actions._tag_lesen("2026-10-17"), date(2026, 10, 17))
        self.assertIsNone(actions._tag_lesen("irgendwann"))
        self.assertEqual(R.datum_text(date(2026, 10, 17)), "17. Oktober 2026")


class Werkzeug(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.geschrieben = {}
        import profilordner

        def schreiben(bilder, pfad, fps, rate=1.0, ton=None):
            self.geschrieben = {"bilder": sum(1 for _ in bilder), "ton": ton, "pfad": pfad}
        self.stimme = SimpleNamespace(modus=lambda: "knopf", verfuegbar=lambda: True, ABTASTRATE=24000,
                                      freigeben=lambda: None, saetze=lambda t: [t],
                                      erzeugen=lambda s: np.zeros(24000 * 5, np.float32) + 0.1)
        for ziel, name, wert in ((profilordner, "ordner", lambda art: self.tmp),
                                 (profilordner, "video_pfad", lambda endung=".mp4": self.tmp / "rb.mp4"),
                                 (videodatei, "schreiben", schreiben), (os, "startfile", lambda p: None),
                                 (actions, "_stimme", lambda: self.stimme)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_mit_und_ohne_stimme(self):
        for i in range(3):
            _bild(self.tmp, f"{i}.png")
        res = actions._tagesrueckblick({"text": "Heute haben wir viel gemalt.", "titel": "Unser Tag"})
        self.assertTrue(res.ok, res.text)
        self.assertIn("mit deiner Stimme", res.text)
        self.assertEqual(self.geschrieben["ton"][1], 24000)
        self.stimme.modus = lambda: "aus"
        bilder = [str(self.tmp / f"{i}.png") for i in range(3)]     # fest: über Mitternacht wäre „heute“ leer
        res = actions._tagesrueckblick({"text": "Noch mal.", "bilder": bilder})
        self.assertIn("aus", res.text)
        self.assertIsNone(self.geschrieben["ton"])

    def test_ohne_bilder_und_falscher_tag(self):
        self.assertFalse(actions._tagesrueckblick({"text": "x"}).ok)
        self.assertFalse(actions._tagesrueckblick({"tag": "nächsten Dienstag"}).ok)
        self.assertFalse(actions._tagesrueckblick({"bilder": [str(self.tmp / "fehlt.png")]}).ok)

    def test_eingebunden(self):
        import hintergrund
        self.assertTrue(actions.ACTIONS["tagesrueckblick"].get("hintergrund"))
        self.assertIn("tagesrueckblick", hintergrund.GPU_WERKZEUGE)
        self.assertIn("- tagesrueckblick ", (ROOT / "core" / "persona.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
