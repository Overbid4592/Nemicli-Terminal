"""Tests: Coding-Sammlung – freigegebener Code aus dem Coding-Assistenten.

python -m unittest tests.test_codesammlung
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import actions      # noqa: E402
import codesammlung as CS      # noqa: E402
import coding       # noqa: E402
import snapshot     # noqa: E402


class Sammlung(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name)
        self.projekt = self.t / "projekt"
        self.projekt.mkdir()
        self.nr = 1
        self._p = [patch.object(CS, "DB", self.t / "sammlung.db"),
                   patch.object(coding, "aktiv", lambda: True),
                   patch.object(coding, "projekt", lambda: self.projekt),
                   patch.object(coding, "kontext", lambda: ("Kleines Notiz-Tool", "Speichern in SQLite", self.nr))]
        for p in self._p:
            p.start()
            self.addCleanup(p.stop)

    def test_nur_code_und_nur_bei_aktivem_assistenten(self):
        self.assertIsNotNone(CS.aufnehmen(self.projekt / "a.py", "schreiben", "", "print(1)\n", True))
        self.assertIsNone(CS.aufnehmen(self.projekt / "notiz.txt", "schreiben", "", "text", True))
        self.assertIsNone(CS.aufnehmen(self.projekt / "leer.py", "schreiben", "", "  ", True))
        with patch.object(coding, "aktiv", lambda: False):
            self.assertIsNone(CS.aufnehmen(self.projekt / "b.py", "schreiben", "", "x = 1\n", True))
        s = CS.stand()
        self.assertEqual(s["nutzbar"], 1)
        self.assertEqual(s["sprachen"], {"Python": 1})

    def test_suche_nimmt_nur_nutzbares_geprueft_zuerst(self):
        CS.aufnehmen(self.projekt / "db.py", "schreiben", "", "import sqlite3\nverbindung = sqlite3.connect('x.db')\n", True)
        CS.aufnehmen(self.projekt / "kaputt.py", "schreiben", "", "import sqlite3\ndef (:\n", False)
        self.nr = 2
        CS.aufnehmen(self.projekt / "speicher.py", "schreiben", "", "import sqlite3  # Notizen speichern\n", True)
        treffer = CS.suchen("sqlite3 Notizen")
        self.assertEqual({Path(b["datei"]).name for b in treffer}, {"db.py", "speicher.py"})
        self.assertEqual(CS.punkt_geprueft(self.projekt, 2), 1)
        treffer = CS.suchen("sqlite3")
        self.assertEqual(treffer[0]["datei"], "speicher.py")
        self.assertEqual(treffer[0]["status"], CS.GEPRUEFT)
        self.assertEqual(CS.suchen("sqlite3", sprache_filter="Rust"), [])

    def test_undo_verwirft(self):
        datei = self.projekt / "a.py"
        datei.write_text("alt = 1\n", encoding="utf-8")
        seit = time.strftime("%Y-%m-%d %H:%M:%S")
        CS.aufnehmen(datei, "schreiben", "", "neu = 2\n", True)
        self.assertEqual(CS.verwerfen(datei, seit), 1)
        self.assertEqual(CS.stand()["nutzbar"], 0)
        self.assertEqual(CS.stand()[CS.VERWORFEN], 1)

    def test_werkzeug_texte(self):
        self.assertIn("noch leer", CS.werkzeug({"aktion": "stand"}))
        CS.aufnehmen(self.projekt / "web.js", "bearbeiten", "let a = 1;", "const a = 1;", True)
        text = CS.werkzeug({"aktion": "suchen", "frage": "const"})
        self.assertIn("Beispiel #1 · JavaScript · web.js", text)
        self.assertIn("Auftrag: Kleines Notiz-Tool", text)
        self.assertIn("vorher:", text)
        self.assertIn("```javascript", text)
        self.assertIn("1 nutzbare Beispiele", CS.werkzeug({"aktion": "stand"}))
        self.assertIn("`frage` fehlt", CS.werkzeug({"aktion": "suchen"}))

    def test_datei_werkzeuge_sammeln_mit_sofortcheck(self):
        papierkorb = self.t / "Papierkorb"
        with patch.object(actions, "_guard", lambda *a: None), patch.object(snapshot, "ORDNER", papierkorb):
            actions._datei_schreiben({"pfad": str(self.projekt / "gut.py"), "inhalt": "wert = 1\n"})
            actions._datei_schreiben({"pfad": str(self.projekt / "schlecht.py"), "inhalt": "def (:\n"})
            actions._datei_bearbeiten({"pfad": str(self.projekt / "gut.py"), "suchen": "wert = 1",
                                       "ersetzen": "wert = 2"})
            s = CS.stand()
            self.assertEqual((s["nutzbar"], s.get(CS.FEHLER, 0)), (2, 1))
            # /undo der Bearbeitung: das Beispiel dazu gilt nicht mehr
            snapshot.wiederherstellen(snapshot.letzte(1)[0])
        self.assertEqual((self.projekt / "gut.py").read_text(encoding="utf-8"), "wert = 1\n")
        self.assertEqual(CS.stand()[CS.VERWORFEN], 1)

    def test_lese_werkzeug(self):
        import modes
        self.assertIn("coding_sammlung", actions.ACTIONS)
        self.assertIn("coding_sammlung", modes.READ_TOOLS)
        self.assertFalse(actions.needs_confirm({"tool": "coding_sammlung", "aktion": "suchen"}))


if __name__ == "__main__":
    unittest.main()
