"""Eigener Ordner je Persönlichkeit: Ordnername, Bilder, Chats, Erinnerungen, Skills, Suchindex."""
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

import chatstore      # noqa: E402
import indexdb        # noqa: E402
import memory         # noqa: E402
import paths          # noqa: E402
import profilordner   # noqa: E402
import sicherheit     # noqa: E402
import skills         # noqa: E402


class Ordnername(unittest.TestCase):
    def test_ohne_sonderzeichen(self):
        self.assertEqual(profilordner.ordnername("Alpha"), "Alpha")
        self.assertEqual(profilordner.ordnername("Vera – Vollständiger Agenten-Systemprompt"),
                         "Vera_Vollstaendiger_Agenten_Systemprompt")
        self.assertEqual(profilordner.ordnername("Jörg Müßig!"), "Joerg_Muessig")
        self.assertEqual(profilordner.ordnername("../..\\x"), "x")
        self.assertEqual(profilordner.ordnername("???"), "Profil")


class Profile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.daten = Path(self._tmp.name).resolve()
        self.wer = ["Alpha"]
        gemeinsam = self.daten / "chats"
        mem = self.daten / "learned" / "memory.json"
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(profilordner, "aktiv", side_effect=lambda: self.wer[0]),
                  mock.patch.object(chatstore, "CHATS_DIR", gemeinsam),
                  mock.patch.object(chatstore, "_STANDARD", gemeinsam),
                  mock.patch.object(memory, "_FILE", mem), mock.patch.object(memory, "_STANDARD_FILE", mem),
                  mock.patch.object(indexdb, "drop_ref", lambda *_: None)):
            p.start()
            self.addCleanup(p.stop)

    def test_bilder(self):
        ziel = profilordner.bild_ziel(self.daten / "Bilder")
        self.assertEqual(ziel.parent, self.daten / "Profile" / "Alpha" / "Bilder")
        self.assertRegex(ziel.name, r"^Alpha_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.png$")
        ziel.write_bytes(b"x")
        self.assertNotEqual(profilordner.bild_ziel(self.daten / "Bilder"), ziel)       # eindeutig
        umgelenkt = profilordner.bild_ziel(self.daten / "anderswo", ".jpg")
        self.assertEqual(umgelenkt.parent, self.daten / "anderswo")

    def test_chats_je_persoenlichkeit_mit_gemeinsamen_alten(self):
        (self.daten / "chats").mkdir()
        chatstore.CHATS_DIR.joinpath("005.json").write_text('{"id": 5, "title": "alt", "updated": "1"}',
                                                             encoding="utf-8")
        nr = chatstore.next_id()
        self.assertEqual(nr, 6)
        chatstore.save(nr, [], ["Hallo Alpha"], "m")
        self.assertTrue((self.daten / "Profile" / "Alpha" / "Chats" / "006.json").exists())
        self.assertEqual(chatstore.index_ref(nr), "chat:Alpha:6")
        self.assertEqual(chatstore.index_ref(5), "chat:5")
        self.assertEqual({(c["id"], c["profil"]) for c in chatstore.list_chats()}, {(5, ""), (6, "Alpha")})
        self.wer[0] = "Beta"
        self.assertEqual({c["id"] for c in chatstore.list_chats()}, {5})               # Alphas Chat nicht
        self.assertEqual(chatstore.next_id(), 7)                                       # Nummern bleiben eindeutig
        self.assertTrue(indexdb._fremdes_profil("chat:Alpha:6"))
        self.assertFalse(indexdb._fremdes_profil("chat:Beta:7"))
        self.assertFalse(indexdb._fremdes_profil("chat:5"))

    def test_erinnerungen_lehren_eigen_vorlieben_gemeinsam(self):
        v = memory.remember("Mag kurze Antworten.", "vorliebe")
        lehre = memory.remember("Vor dem Löschen auflisten.", "lektion")
        self.assertNotEqual(v["id"], lehre["id"])
        self.assertTrue((self.daten / "Profile" / "Alpha" / "Erinnerungen" / "memory.json").exists())
        self.assertEqual({e["text"] for e in memory.all_entries()}, {"Mag kurze Antworten.", "Vor dem Löschen auflisten."})
        self.wer[0] = "Beta"
        self.assertEqual({e["text"] for e in memory.all_entries()}, {"Mag kurze Antworten."})
        n = memory.remember("Beta lernt etwas.", "lektion")
        self.assertGreater(n["id"], lehre["id"])                                         # Nummern über alle Dateien
        self.wer[0] = "Alpha"
        self.assertIsNotNone(memory.ersetzen(lehre["id"], "Vor dem Löschen immer auflisten."))
        self.assertIn("Vor dem Löschen immer auflisten.", memory.kern_block())

    def test_skills_eigen_und_fuer_alle(self):
        skills.schreiben("Alphas Trick", "nur für Alpha", "immer", "Schritt 1.")
        skills.schreiben("Gemeinsam", "für alle", "immer", "Schritt 1.", fuer_alle=True)
        self.assertEqual({(s.name, s.fuer_alle) for s in skills.alle()}, {("Alphas Trick", False), ("Gemeinsam", True)})
        self.wer[0] = "Beta"
        self.assertEqual({s.name for s in skills.alle()}, {"Gemeinsam"})
        self.assertIn("für alle", skills.index_fuer_prompt())
        with mock.patch.object(paths, "INSTALL", self.daten.parent / "programm"):
            pfad = str(self.daten / "Profile" / "Beta" / "Skills" / "x" / "SKILL.md")
            self.assertFalse(sicherheit.arbeitsbereich({"tool": "datei_schreiben", "pfad": pfad, "inhalt": "x"}))

    def test_lesen_legt_nichts_an(self):
        memory.all_entries()
        skills.alle()
        chatstore.list_chats()
        chatstore.next_id()
        profilordner.bilder_ordner_alle()
        self.assertFalse((self.daten / "Profile").exists())


if __name__ == "__main__":
    unittest.main()
