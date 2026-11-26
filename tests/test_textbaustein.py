"""Fester Textbaustein (core/textbaustein.py): entpackt, prüfsummengesichert, vor jeder Anleitung.

python -m unittest tests.test_textbaustein
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import textbaustein as T  # noqa: E402


class Baustein(unittest.TestCase):
    def tearDown(self):
        T.einleitung.cache_clear()

    def test_entpackt_und_geprueft(self):
        e = T.einleitung()
        self.assertGreater(len(e), 200)
        self.assertTrue(e.endswith("\n\n"))

    def test_veraendert_faellt_weg(self):
        T.einleitung.cache_clear()
        with mock.patch.object(T, "_S", "0" * 64):
            self.assertEqual("", T.einleitung())
        T.einleitung.cache_clear()
        with mock.patch.object(T, "_D", ("kaputt",)):
            self.assertEqual("", T.einleitung())

    def test_im_quelltext_nicht_lesbar(self):
        quelle = (ROOT / "core" / "textbaustein.py").read_text(encoding="utf-8")
        for zeile in T.einleitung().splitlines():
            for stueck in (zeile[i:i + 24] for i in range(0, max(1, len(zeile) - 24), 24)):
                if stueck.strip():
                    self.assertNotIn(stueck, quelle)

    def test_vor_dem_gesetzbuch(self):
        import gesetzbuch as GB
        self.assertTrue(GB.vorspann().startswith(T.einleitung()))
        self.assertTrue(GB.voran(lambda: "Anleitung")().startswith(T.einleitung()))


if __name__ == "__main__":
    unittest.main()
