"""Bibliothekar: Er sammelt ohne Modell in den Einlese-Speicher; Vektoren gibt es bei /einlesen und für den laufenden
Chat nach einem aufgeräumten Kontext."""

import contextlib
import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui", "tests"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import chatstore                       # noqa: E402
import indexdb                         # noqa: E402
import memory                          # noqa: E402
from test_berichte_index import _Basis  # noqa: E402


def _nachrichten(token: int) -> list[dict]:
    satz = "Ein Satz über das Wetter und den Garten. "
    n = max(1, token * 4 // len(satz))
    return [{"role": "user", "content": "".join(f"{i}: {satz}" for i in range(n))}]


class Takt(_Basis):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        self.chats: dict[int, dict] = {}
        self.stand: dict[int, str] = {}
        self.chatordner = root / "chats"
        self.chatordner.mkdir()
        for name, wert in (("load", lambda cid: self.chats.get(int(cid))),
                           ("md_path", lambda cid: root / "fehlt.md"),
                           ("index_ref", lambda cid: f"chat:{cid}"),
                           ("list_chats", lambda: [{"id": nr, "updated": self.stand.get(nr, "1")}
                                                   for nr in sorted(self.chats, reverse=True)])):
            p = patch.object(chatstore, name, wert)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(memory, "auftrag", lambda n: contextlib.nullcontext(False))   # kein echtes Modell laden
        p.start()
        self.addCleanup(p.stop)
        indexdb._chat_stand.clear()
        indexdb._nachgeholt.clear()
        self.addCleanup(indexdb._chat_stand.clear)
        self.addCleanup(indexdb._nachgeholt.clear)

    def _chat(self, cid: int, token: int) -> None:
        self.chats[cid] = {"id": cid, "title": "", "prompts": [], "messages": _nachrichten(token)}

    def _brocken(self, ref: str) -> tuple[int, int]:
        con = sqlite3.connect(indexdb._DB_NEW)
        try:
            return con.execute("SELECT COUNT(*), SUM(vec IS NOT NULL) FROM chunks WHERE ref=?", (ref,)).fetchone()
        finally:
            con.close()

    def test_sammeln_schreibt_ohne_vektor_und_ohne_modell(self):
        self._chat(1, 2_000)
        with patch.object(memory, "_embed_texts", side_effect=AssertionError("kein Modell beim Sammeln")):
            indexdb.after_turn(1)
        n, mit = self._brocken("chat:1")
        self.assertGreater(n, 0)
        self.assertFalse(mit)
        self.assertGreater(indexdb.ohne_vektor_token(), 1_000)

    def test_einlesen_arbeitet_alles_ab_und_schreibt_stueckweise(self):
        self._chat(1, 6_000)
        indexdb.after_turn(1)
        vorher = indexdb.ohne_vektor_token()
        self.assertGreater(vorher, 3_000)
        schritte = []
        echt = indexdb.nachvektorisieren
        with patch.object(indexdb, "NACHRECHNEN_STUECK", 4), \
                patch.object(indexdb, "nachvektorisieren",
                             lambda limit, kontext, bis: (schritte.append(kontext), echt(limit, kontext, bis))[1]):
            indexdb.after_turn(1, alles=True)
        n, mit = self._brocken("chat:1")
        self.assertEqual(n, mit)
        self.assertEqual(indexdb.ohne_vektor_token(), 0)
        self.assertGreater(len(schritte), 2)                       # der Balken läuft sichtbar leer
        self.assertTrue(schritte[0].startswith("Einlesen 0/"))

    def test_block_wird_abgetrennt_neues_wartet_im_naechsten(self):
        self._chat(1, 2_000)
        echt = indexdb.nachvektorisieren

        def waehrenddessen(limit, kontext, bis):
            con = sqlite3.connect(indexdb._DB_NEW)
            con.execute("INSERT OR IGNORE INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell, schnitt) "
                        "VALUES ('neu', 'chat', 'chat:1', 'Nutzer: danach geschrieben', NULL, '', '', '', 2)")
            con.commit()
            con.close()
            return echt(limit, kontext, bis)
        with patch.object(indexdb, "nachvektorisieren", waehrenddessen):
            indexdb.after_turn(1, alles=True)
        con = sqlite3.connect(indexdb._DB_NEW)
        try:
            neu = con.execute("SELECT vec FROM chunks WHERE hash='neu'").fetchone()[0]
            andere = con.execute("SELECT COUNT(*) FROM chunks WHERE hash != 'neu' AND vec IS NULL").fetchone()[0]
        finally:
            con.close()
        self.assertIsNone(neu)                                     # gehört zum nächsten Block
        self.assertEqual(andere, 0)
        self.assertGreater(indexdb.ohne_vektor_token(), 0)

    def test_speichermangel_halbiert_den_stapel(self):
        self._chat(1, 4_000)
        indexdb.after_turn(1)
        echt = memory._embed_texts
        groessen = []

        def embed(texts, **k):
            groessen.append(len(texts))
            return None if len(texts) > 4 else echt(texts, **k)
        with patch.object(memory, "_embed_texts", embed), patch.object(memory, "encoder_loaded", return_value=True):
            indexdb.after_turn(1, alles=True)
        self.assertEqual(groessen[:3], [indexdb.EMBED_BATCH, indexdb.EMBED_BATCH // 2, 4])
        n, mit = self._brocken("chat:1")
        self.assertEqual(n, mit)

    def test_passt_gar_nicht_bleibt_im_speicher(self):
        self._chat(1, 1_000)
        indexdb.after_turn(1)
        with patch.object(memory, "_embed_texts", return_value=None), \
                patch.object(memory, "encoder_loaded", return_value=True):
            indexdb.after_turn(1, alles=True)
        self.assertFalse(self._brocken("chat:1")[1])
        self.assertGreater(indexdb.ohne_vektor_token(), 0)

    def test_nach_dem_aufraeumen_sofort(self):
        self._chat(1, 1_000)
        indexdb.after_turn(1)                                      # schon gesammelt, noch ohne Vektor
        indexdb.after_turn(1, chat_erzwingen=True)
        n, mit = self._brocken("chat:1")
        self.assertGreater(n, 0)
        self.assertEqual(n, mit)

    def test_aufraeumen_rechnet_nur_den_laufenden_chat(self):
        self._chat(1, 500)
        self._chat(2, 500)
        indexdb.after_turn(1, chat_erzwingen=True)
        self.assertEqual(self._brocken("chat:1")[0], self._brocken("chat:1")[1])
        self.assertFalse(self._brocken("chat:2")[1])               # der andere Chat wartet im Speicher

    def test_nur_kurz_laesst_berichte_und_andere_chats_liegen(self):
        self._chat(1, 500)
        self._chat(2, 500)
        indexdb.after_turn(1, nur_kurz=True)
        self.assertGreater(self._brocken("chat:1")[0], 0)
        self.assertEqual(self._brocken("chat:2")[0], 0)
        self.assertEqual(indexdb.offen()["berichte_offen"], 6)
        indexdb.after_turn(1)
        self.assertGreater(self._brocken("chat:2")[0], 0)
        self.assertEqual(indexdb.offen()["berichte_offen"], 0)

    def test_bild_zaehlt_pauschal(self):
        indexdb._connect().close()
        con = sqlite3.connect(indexdb._DB_NEW)
        con.execute("INSERT INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell, schnitt) "
                    "VALUES ('b1', 'bild', 'bild:x:1.png', 'Bild 1.png', NULL, '', '', '', 2)")
        con.commit()
        con.close()
        import profilordner
        with patch.object(profilordner, "aktiv", return_value="x"):
            self.assertEqual(indexdb.ohne_vektor_token(800), 800)

    def test_bilder_anderer_persoenlichkeiten_zaehlen_nicht(self):
        import profilordner
        indexdb._connect().close()
        con = sqlite3.connect(indexdb._DB_NEW)
        for h, ref in (("b1", "bild:Eins:a.png"), ("b2", "bild:Zwei:b.png"), ("b3", "bild:Zwei:c.png")):
            con.execute("INSERT INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell, schnitt) "
                        "VALUES (?, 'bild', ?, 'Bild', NULL, '', '', '', 2)", (h, ref))
        con.commit()
        con.close()
        with patch.object(profilordner, "aktiv", return_value="Eins"):
            self.assertEqual(indexdb.ohne_vektor_token(800), 800)
        with patch.object(profilordner, "aktiv", return_value="Zwei"):
            self.assertEqual(indexdb.ohne_vektor_token(800), 1600)

    def test_ohne_modell_kein_fuellstand(self):
        with patch.object(indexdb, "_kennung", return_value=""):
            self.assertIsNone(indexdb.ohne_vektor_token())

    def test_laufender_chat_wird_einmal_geladen(self):
        self._chat(1, 2_000)
        geladen, echt = [], chatstore.load
        with patch.object(chatstore, "load", lambda cid: (geladen.append(int(cid)), echt(cid))[1]):
            indexdb.after_turn(1)
        self.assertGreater(self._brocken("chat:1")[0], 0)
        self.assertEqual(1, geladen.count(1))

    def test_neue_runde_unterbricht_rueckstaende(self):
        self._chat(1, 20_000)                                      # deutlich mehr als ein Stapel
        echt = memory._embed_texts

        def embed(texts, **k):
            indexdb.unterbrechen()                                 # Nutzer schickt mitten im Stapel ab
            return echt(texts, **k)
        memory._embed_texts = embed
        indexdb.after_turn(1, chat_erzwingen=True)
        n, mit = self._brocken("chat:1")
        self.assertEqual(mit, indexdb.EMBED_BATCH)                 # der laufende Stapel wird fertig
        self.assertGreater(n, mit)                                 # der Rest liegt ohne Vektor bereit
        self.assertEqual(indexdb.offen()["berichte_offen"], 6)     # Rückstände nicht angefasst
        memory._embed_texts = echt
        indexdb.after_turn(1)                                      # nächste Runde: sammelt weiter
        self.assertEqual(self._brocken("chat:1")[1], indexdb.EMBED_BATCH)
        self.assertLess(indexdb.offen()["berichte_offen"], 6)

    def test_unterbrechen_ohne_laufenden_bibliothekar_wirkt_nicht_nach(self):
        indexdb.unterbrechen()
        self._chat(1, 1_000)
        indexdb.after_turn(1, chat_erzwingen=True)
        n, mit = self._brocken("chat:1")
        self.assertEqual(n, mit)
        self.assertLess(indexdb.offen()["berichte_offen"], 6)

    def test_andere_chats_werden_nachgeholt(self):
        self._chat(1, 500)
        self._chat(2, 500)
        aufrufe = []
        echt = indexdb.chat_einbetten
        with patch.object(indexdb, "chat_einbetten",
                          lambda cid, data=None: (aufrufe.append(cid), echt(cid, data))[1]):
            indexdb.after_turn(1)
            indexdb.after_turn(1)                                  # unverändert: kein zweites Mal
            self.stand[2] = "2"
            indexdb.after_turn(1)                                  # weitergeschrieben: nochmal
        self.assertEqual(aufrufe, [1, 2, 2])
        self.assertGreater(self._brocken("chat:2")[0], 0)


class FrageWartetNie(unittest.TestCase):
    def setUp(self):
        class Enc:
            _lock = threading.RLock()
            device = "cpu"

            def encode(self, texte, **_k):
                import numpy as np
                return np.ones((len(texte), 3))
        self.enc = Enc()
        alt = memory._encoder
        memory._encoder = self.enc
        self.addCleanup(setattr, memory, "_encoder", alt)
        memory._fragen.clear()
        self.addCleanup(memory._fragen.clear)

    def test_frei_liefert_vektor(self):
        self.assertEqual(memory.frage_einbetten("Garten"), [[1.0, 1.0, 1.0]])

    def _belegt(self, pruefen):
        belegt, los = threading.Event(), threading.Event()

        def bibliothekar():
            with self.enc._lock:
                belegt.set()
                los.wait(5)
        t = threading.Thread(target=bibliothekar)
        t.start()
        try:
            belegt.wait(5)
            pruefen()
        finally:
            los.set()
            t.join()

    def test_belegt_liefert_sofort_none(self):
        self._belegt(lambda: self.assertIsNone(memory.frage_einbetten("Garten")))   # nur Stichworte

    def test_gemerkte_frage_kommt_auch_belegt(self):
        memory.frage_einbetten("Garten")
        self._belegt(lambda: self.assertEqual(memory.frage_einbetten("Garten"), [[1.0, 1.0, 1.0]]))
        self.assertIsNone(memory._aus_speicher(self.enc, "chat", ["Garten"])[0])    # je Aufgabe getrennt

    def test_suche_im_chat_nutzt_den_wartefreien_weg(self):
        quelle = (ROOT / "tools" / "indexdb.py").read_text(encoding="utf-8")
        self.assertIn("memory.frage_einbetten(fragen[0], aufgabe=aufgabe)", quelle)


class ChatJetzt(unittest.TestCase):
    def test_nur_nach_dem_aufraeumen(self):
        from types import SimpleNamespace as NS
        if str(ROOT) not in sys.path:
            sys.path.append(str(ROOT))
        import main
        ctx = NS(backend=NS(weggefallen=[{"role": "user", "content": "alt"}], model="m"), current_chat=5)
        self.assertTrue(main._chat_jetzt(ctx))                     # Kontext aufgeräumt: sofort
        ctx.backend.weggefallen = []
        self.assertFalse(main._chat_jetzt(ctx))                    # sonst wartet er im Einlese-Speicher


if __name__ == "__main__":
    unittest.main()
