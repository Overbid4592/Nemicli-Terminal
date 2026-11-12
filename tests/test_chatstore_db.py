"""Chat-Datenbank je Persönlichkeit (core/chatstore.py): Speichern, Bilder nur einmal, nur Anhängen,
Übernahme alter Einzeldateien, Prüfen/Sichern/Reparieren."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "engines", "tools"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import chatstore  # noqa: E402

BILD = "data:image/png;base64," + "A" * 5000


def _verlauf(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Nachricht {i}"} for i in range(n)]


class ChatDB(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.ort = self.tmp / "chats"
        p = mock.patch.object(chatstore, "CHATS_DIR", self.ort)      # umgelenkt: nur dieser Ordner, kein Profil
        p.start()
        self.addCleanup(p.stop)
        self.assertEqual(chatstore.ordner(), self.ort)

    def _zeilen(self, nr=1):
        c = sqlite3.connect(str(chatstore.db_pfad(self.ort)))
        try:
            return c.execute("SELECT nr, inhalt FROM nachrichten WHERE chat=? ORDER BY nr", (nr,)).fetchall()
        finally:
            c.close()

    def test_speichern_laden_eine_datei(self):
        chatstore.save(1, _verlauf(4), ["Hallo"], "m")
        self.assertEqual(chatstore.load(1)["messages"], _verlauf(4))
        self.assertEqual(chatstore.load(1)["title"], "Hallo")
        self.assertEqual([p.name for p in self.ort.glob("*.json")], [])
        self.assertTrue(chatstore.db_pfad(self.ort).exists())
        self.assertEqual(chatstore.db_pfad(self.ort).name, "chat_gemeinsam.db")
        self.assertIsNone(chatstore.load(99))

    def test_letzter_ist_das_zuletzt_benutzte(self):
        self.assertIsNone(chatstore.letzter())
        chatstore.save(41, _verlauf(2), ["Urlaub am Meer planen"], "m")
        chatstore.save(42, _verlauf(2), ["Python Fehler suchen"], "m")
        c = sqlite3.connect(str(chatstore.db_pfad(self.ort)))
        c.execute("UPDATE chats SET aktualisiert='2026-10-11 09:05' WHERE id=41")
        c.execute("UPDATE chats SET aktualisiert='2026-10-10 18:30' WHERE id=42")
        c.commit()
        c.close()
        self.assertEqual(chatstore.letzter()["id"], 41)

    def test_ende_laden_und_weiterspeichern_behaelt_den_anfang(self):
        import pricing
        alles = _verlauf(40)
        chatstore.save(5, alles, ["a"], "m")
        ende = pricing.gespraechsende(chatstore.load(5)["messages"], budget=30)
        self.assertLess(len(ende), 40)
        self.assertEqual(ende[0]["role"], "user")
        weiter = ende + [{"role": "user", "content": "neu"}, {"role": "assistant", "content": "ok"}]
        chatstore.save(5, weiter, ["a", "neu"], "m")
        self.assertEqual(chatstore.load(5)["messages"], alles + weiter[len(ende):])   # Anfang bleibt

    def test_neue_runde_haengt_nur_an(self):
        chatstore.save(1, _verlauf(4), ["a"], "m")
        vorher = self._zeilen()
        chatstore.save(1, _verlauf(6), ["a", "b"], "m")
        nachher = self._zeilen()
        self.assertEqual(nachher[:4], vorher)
        self.assertEqual(len(nachher), 6)
        chatstore.save(1, _verlauf(6)[3:] + [{"role": "user", "content": "neu"}], ["a", "b", "c"], "m")
        self.assertEqual(len(chatstore.load(1)["messages"]), 7)            # aufgeräumter Kontext: Anfang bleibt

    def test_bilder_nur_einmal(self):
        bild = {"role": "user", "content": [{"type": "text", "text": "schau"},
                                             {"type": "image_url", "image_url": {"url": BILD}}]}
        chatstore.save(1, [bild, {"role": "assistant", "content": "ok"}, bild], ["schau"], "m")
        self.assertNotIn(BILD, "".join(z for _, z in self._zeilen()))
        c = sqlite3.connect(str(chatstore.db_pfad(self.ort)))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM bilder").fetchone()[0], 1)
        c.close()
        geladen = chatstore.load(1)["messages"]
        self.assertEqual(geladen[0]["content"][1]["image_url"]["url"], BILD)
        self.assertEqual(geladen[2], bild)

    def test_alte_dateien_werden_uebernommen(self):
        self.ort.mkdir()
        (self.ort / "007.json").write_text(json.dumps({"id": 7, "title": "alt", "model": "m", "updated": "2026-01-01",
                                                       "prompts": ["p"], "messages": _verlauf(2)}), encoding="utf-8")
        (self.ort / "007.md").write_text("# alt", encoding="utf-8")
        (self.ort / "008.json").write_text("kaputt", encoding="utf-8")
        self.assertEqual([c["id"] for c in chatstore.list_chats()], [7])
        self.assertEqual(chatstore.load(7)["messages"], _verlauf(2))
        self.assertTrue((self.ort / "vor_db" / "007.json").exists())     # verschoben, nicht gelöscht
        self.assertTrue((self.ort / "vor_db" / "007.md").exists())
        self.assertTrue((self.ort / "008.json").exists())                # unlesbar: bleibt liegen
        self.assertEqual(chatstore.next_id(), 8)

    def test_pruefen_sichern_reparieren(self):
        self.assertEqual(chatstore.pruefen(self.ort), "fehlt")
        chatstore.save(1, _verlauf(4), ["a"], "m")
        self.assertEqual(chatstore.pruefen(self.ort), "ok")
        self.assertIsNone(chatstore.zuletzt_gesichert(self.ort))
        ziel = chatstore.sichern(self.ort)
        self.assertEqual(ziel.name, "chat_backup_gemeinsam.db")
        self.assertIsNotNone(chatstore.zuletzt_gesichert(self.ort))
        self.assertEqual(chatstore.pruefen_datei(ziel), "ok")
        db = chatstore.db_pfad(self.ort)
        for endung in ("-wal", "-shm"):
            Path(str(db) + endung).unlink(missing_ok=True)
        db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4000)                # beschädigt
        self.assertNotEqual(chatstore.pruefen(self.ort), "ok")
        meldung = chatstore.reparieren(self.ort)
        self.assertIn("zurückgeholt", meldung)
        self.assertEqual(chatstore.pruefen(self.ort), "ok")
        self.assertEqual(chatstore.load(1)["messages"], _verlauf(4))
        self.assertTrue(any(p.name.startswith("chat_gemeinsam.kaputt-") for p in self.ort.iterdir()))
        self.assertIsNone(chatstore.reparieren(self.ort))                 # gesund: nichts zu tun


if __name__ == "__main__":
    unittest.main()


class Sicherung(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.ort = self.tmp / "chats"
        p = mock.patch.object(chatstore, "CHATS_DIR", self.ort)
        p.start()
        self.addCleanup(p.stop)
        self.assertEqual(chatstore.ordner(), self.ort)

    def test_faellig_werkzeug_und_hinweis(self):
        import actions
        import persona
        self.assertFalse(chatstore.faellig())                              # keine Chats: nichts zu sichern
        self.assertEqual(persona._chat_backup_hinweis(), "")
        chatstore.save(1, _verlauf(2), ["a"], "m")
        self.assertTrue(chatstore.faellig())
        self.assertIn("noch nie", persona._chat_backup_hinweis())
        self.assertTrue(actions.ACTIONS["chat_backup"]["confirm"])          # der Nutzer bestätigt
        self.assertIn("Chats gesichert", actions._chat_backup({}))
        self.assertTrue(chatstore.backup_pfad().exists())
        self.assertFalse(chatstore.faellig())
        self.assertEqual(persona._chat_backup_hinweis(), "")

    def test_beim_start_repariert(self):
        chatstore.save(1, _verlauf(2), ["a"], "m")
        chatstore.sichern()
        self.assertIsNone(chatstore.beim_start())
        db = chatstore.db_pfad()
        for endung in ("-wal", "-shm"):
            Path(str(db) + endung).unlink(missing_ok=True)
        db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4000)
        self.assertIn("zurückgeholt", chatstore.beim_start())
        self.assertEqual(chatstore.load(1)["messages"], _verlauf(2))

    def test_kugel_sichert_im_vollbild(self):
        from types import SimpleNamespace as NS
        from wache import kugel as K
        import profilordner
        chatstore.save(1, _verlauf(2), ["a"], "m")
        laeufe = []
        k = NS(_sicherung_versucht=0.0, name="Ada")
        with mock.patch.object(profilordner, "ordner", lambda *a, **kw: self.ort), \
                mock.patch.object(K, "vollbild", lambda: False), \
                mock.patch.object(K.threading, "Thread", lambda target, **kw: NS(start=lambda: laeufe.append(target))):
            K.Kugel._chats_sichern(k)
            self.assertEqual(laeufe, [])                                    # kein Vollbild: nichts
            with mock.patch.object(K, "vollbild", lambda: True):
                K.Kugel._chats_sichern(k)
                K.Kugel._chats_sichern(k)                                   # gleich danach: nicht nochmal
        self.assertEqual(len(laeufe), 1)
        laeufe[0]()
        self.assertTrue(chatstore.backup_pfad(self.ort).exists())
