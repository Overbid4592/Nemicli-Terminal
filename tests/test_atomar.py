"""Atomares Schreiben: ein Fehler mitten im Schreiben lässt die alte Datei ganz und keine Zwischendatei liegen."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

import atomar                                          # noqa: E402


class AtomarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / "todo.json"

    def test_schreibt_und_ersetzt(self):
        atomar.text_schreiben(self.p, "alt")
        atomar.text_schreiben(self.p, "neu äöü")
        self.assertEqual("neu äöü", self.p.read_text(encoding="utf-8"))
        self.assertEqual(["todo.json"], [x.name for x in Path(self.tmp.name).iterdir()])

    def test_abbruch_laesst_alte_datei_ganz(self):
        atomar.text_schreiben(self.p, "alt")
        with mock.patch.object(atomar.os, "replace", side_effect=OSError("Platte voll")):
            with self.assertRaises(OSError):
                atomar.text_schreiben(self.p, "neu")
        self.assertEqual("alt", self.p.read_text(encoding="utf-8"))
        self.assertEqual(["todo.json"], [x.name for x in Path(self.tmp.name).iterdir()])

    def test_kurz_gesperrt_wird_wiederholt(self):
        echt = atomar.os.replace
        versuche = []

        def replace(a, b):
            versuche.append(1)
            if len(versuche) < 3:
                raise PermissionError("Virenscanner hält die Datei")
            echt(a, b)
        with mock.patch.object(atomar.os, "replace", replace), mock.patch.object(atomar.time, "sleep"):
            atomar.text_schreiben(self.p, "da")
        self.assertEqual("da", self.p.read_text(encoding="utf-8"))
        self.assertEqual(3, len(versuche))


if __name__ == "__main__":
    unittest.main()
