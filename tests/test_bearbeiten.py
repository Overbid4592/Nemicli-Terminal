"""datei_bearbeiten: Zeilenenden bleiben, Hinweis bei anderer Einrückung; gekürzte Web-Anzeige."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions                 # noqa: E402
import ui                      # noqa: E402


class Bearbeiten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.datei = Path(tmp.name) / "beispiel.py"
        for p in (mock.patch.object(actions, "_guard", return_value=None),
                  mock.patch.object(actions, "_sofortcheck", return_value=""),
                  mock.patch("snapshot.sichern", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def bearbeiten(self, suchen, ersetzen):
        return actions._datei_bearbeiten({"pfad": str(self.datei), "suchen": suchen, "ersetzen": ersetzen})

    def test_crlf_bleibt(self):
        self.datei.write_bytes(b"def f():\r\n    return 1\r\n")
        res = self.bearbeiten("    return 1\n", "    return 2\n")
        self.assertIn("1× ersetzt", str(res))
        self.assertEqual(self.datei.read_bytes(), b"def f():\r\n    return 2\r\n")

    def test_lf_bleibt(self):
        self.datei.write_bytes(b"a = 1\nb = 2\n")
        self.bearbeiten("b = 2", "b = 3")
        self.assertEqual(self.datei.read_bytes(), b"a = 1\nb = 3\n")

    def test_andere_einrueckung_wird_gezeigt(self):
        self.datei.write_bytes(b"class K:\n    def f(self):\n        return 1\n")
        res = self.bearbeiten("def f(self):\n    return 1", "x")
        self.assertFalse(res.ok)
        self.assertIn("ab Zeile 2", res.text)
        self.assertIn("····def f(self):", res.text)
        self.assertIn("········return 1", res.text)
        self.assertEqual(self.datei.read_bytes(), b"class K:\n    def f(self):\n        return 1\n")

    def test_ganz_fremder_text_ohne_hinweis(self):
        self.datei.write_bytes(b"a = 1\n")
        res = self.bearbeiten("gibt es nicht", "x")
        self.assertFalse(res.ok)
        self.assertNotIn("Einrückung", res.text)


class WebAnzeige(unittest.TestCase):
    def test_lange_seite_gekuerzt(self):
        text = "\n".join(f"Zeile {i}" for i in range(100))
        kurz = ui.anzeige_kurz(text)
        self.assertIn("Zeile 13", kurz)
        self.assertNotIn("Zeile 14\n", kurz)
        self.assertIn("86 weitere Zeilen", kurz)

    def test_kurze_seite_bleibt(self):
        self.assertEqual(ui.anzeige_kurz("eins\nzwei"), "eins\nzwei")


if __name__ == "__main__":
    unittest.main()
