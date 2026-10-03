"""Berichte → Vektoren, nur die drei neuesten bleiben als Datei.

Alles in einem Temp-Ordner: memory.db, Berichte/, Papierkorb/. Der Encoder wird
durch eine Zählfunktion ersetzt – mal liefert sie Vektoren, mal nichts (Encoder
nicht da). Ohne Vektor darf keine Datei verschwinden."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import indexdb            # noqa: E402
import memory             # noqa: E402
import snapshot           # noqa: E402


class _Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._alt = (indexdb._ROOT, indexdb._DB_NEW, indexdb._DB_OLD, snapshot.ORDNER, memory._embed_texts)
        indexdb._ROOT = root
        indexdb._DB_NEW = root / "learned" / "memory.db"
        indexdb._DB_OLD = root / "learned" / "index.sqlite"
        snapshot.ORDNER = root / "Papierkorb"
        self.encoder_da = True
        self.eingebettet = 0

        def embed(texts, **_k):
            if not self.encoder_da:
                return None
            self.eingebettet += len(texts)
            return [[1.0, 0.0, float(len(t) % 7)] for t in texts]
        memory._embed_texts = embed
        from unittest.mock import patch
        p = patch.object(memory, "modell_kennung", lambda: "test-kennung")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._zurueck)
        self.berichte = root / "Berichte"; self.berichte.mkdir()
        for i in range(6):
            p = self.berichte / f"Wache_2026091{i}-1200.md"
            p.write_text(f"<!-- Bericht {i} -->\n\n✅ Alles harmlos\n\nBericht Nummer {i}: chrome hatte {i * 10} Verbindungen.\n",
                         encoding="utf-8")
            os.utime(p, (1_789_000_000 + i * 3600, 1_789_000_000 + i * 3600))

    def _zurueck(self):
        indexdb._ROOT, indexdb._DB_NEW, indexdb._DB_OLD, snapshot.ORDNER, memory._embed_texts = self._alt
        indexdb._status, indexdb._kontext = None, ""
        indexdb._invalidate()

    def _dateien(self):
        return sorted(p.name for p in self.berichte.glob("*.md"))


class BerichteIndexTests(_Basis):
    def test_vektorisieren_und_nur_drei_behalten(self):
        n, weg = indexdb.ingest_berichte(3, je_runde=None)
        self.assertEqual(6, n)                      # ein Brocken je Bericht
        self.assertEqual(3, weg)
        self.assertEqual(["Wache_20260913-1200.md", "Wache_20260914-1200.md", "Wache_20260915-1200.md"],
                         self._dateien())
        # die alten liegen im Papierkorb, nicht im Nirwana – und sind im Gedächtnis
        self.assertEqual(3, len(list(snapshot.ORDNER.glob("*_Wache_*.md"))))
        con = indexdb._connect()
        refs = sorted(r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'"))
        con.close()
        self.assertEqual([f"bericht:Wache_2026091{i}-1200.md" for i in range(6)], refs)
        # zweiter Lauf: nichts Neues, nichts weg
        self.assertEqual((0, 0), indexdb.ingest_berichte(3, je_runde=None))

    def test_portionen_neueste_zuerst(self):
        """Je Runde nur 2 Berichte, die neuesten zuerst – ein dicker Wache-Bericht kostet ~90 s CPU."""
        self.assertEqual({"brocken": 0, "ohne_vektor": 0, "berichte_offen": 6}, indexdb.offen())
        n, weg = indexdb.ingest_berichte(3)                 # Standard: BERICHTE_JE_RUNDE = 2
        self.assertEqual((2, 0), (n, weg))                  # die drei neuesten bleiben eh – nichts weg
        self.assertEqual(4, indexdb.offen()["berichte_offen"])
        con = indexdb._connect()
        refs = sorted(r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'"))
        con.close()
        self.assertEqual(["bericht:Wache_20260914-1200.md", "bericht:Wache_20260915-1200.md"], refs)
        self.assertEqual((2, 1), indexdb.ingest_berichte(3))    # 13+12 drin → 12 ist alt und vollständig → weg
        self.assertEqual((2, 2), indexdb.ingest_berichte(3))    # 11+10 drin → beide weg
        self.assertEqual((0, 0), indexdb.ingest_berichte(3))
        self.assertEqual(3, len(self._dateien()))

    def test_ohne_encoder_bleibt_alles_liegen_und_wird_spaeter_nachgeholt(self):
        self.encoder_da = False
        n, weg = indexdb.ingest_berichte(3, je_runde=None)
        self.assertEqual((6, 0), (n, weg))          # gespeichert, aber ohne Vektor → keine Datei weg
        self.assertEqual(6, len(self._dateien()))
        self.assertFalse(indexdb._ref_vollstaendig("bericht:Wache_20260910-1200.md"))
        self.assertEqual(6, indexdb.offen()["ohne_vektor"])
        self.encoder_da = True
        self.assertEqual(6, indexdb.nachvektorisieren())
        self.assertTrue(indexdb._ref_vollstaendig("bericht:Wache_20260910-1200.md"))
        self.assertEqual((0, 3), indexdb.ingest_berichte(3, je_runde=None))
        self.assertEqual(3, len(self._dateien()))

    def test_behalten_null_und_after_turn(self):
        self.assertEqual((6, 6), indexdb.ingest_berichte(0, je_runde=None))
        self.assertEqual([], self._dateien())
        # after_turn ruft es mit auf (Standard 3) – und stolpert nicht über leere Ordner
        (self.berichte / "Zeitplan_neu.md").write_text("Bericht", encoding="utf-8")
        self.assertGreaterEqual(indexdb.after_turn(), 1)
        self.assertEqual(["Zeitplan_neu.md"], self._dateien())

    def test_after_turn_alles_ohne_grenzen(self):
        self.assertGreaterEqual(indexdb.after_turn(alles=True), 6)
        self.assertEqual(0, indexdb.offen()["berichte_offen"])
        self.assertEqual(3, len(self._dateien()))


class FortschrittTests(_Basis):
    """Der Bibliothekar meldet, was er tut – mit „i/n“, das die Oberfläche zum Balken macht."""

    def test_meldungen_mit_balken(self):
        meldungen = []
        n = indexdb.after_turn(status=meldungen.append, alles=True)
        self.assertGreaterEqual(n, 6)
        self.assertTrue(any("Berichte 1/6" in m for m in meldungen), meldungen[:5])
        self.assertTrue(any("Bericht Wache_20260915-1200.md · Brocken 1/1" in m for m in meldungen), meldungen[-5:])
        self.assertIsNone(indexdb._status)                  # nach dem Lauf wieder abgehängt

    def test_portionen_beim_einbetten(self):
        alt = indexdb.EMBED_BATCH
        indexdb.EMBED_BATCH = 2
        self.addCleanup(lambda: setattr(indexdb, "EMBED_BATCH", alt))
        meldungen = []
        indexdb._status, indexdb._kontext = meldungen.append, ""
        try:
            vecs = indexdb._einbetten([f"Text {i}" for i in range(5)])
        finally:
            indexdb._status = None
        self.assertEqual(5, len(vecs))
        self.assertEqual(["Brocken 0/5", "Brocken 2/5", "Brocken 4/5", "Brocken 5/5"],
                         [m for m in meldungen if m.startswith("Brocken")])
        self.encoder_da = False
        self.assertIsNone(indexdb._einbetten(["x"]))


if __name__ == "__main__":
    unittest.main()
