"""Schnelle Stichwortsuche: zweistufig (erst der Index, dann die Zeilen) und ohne Wörter, die nichts oder alles finden.

python -m unittest tests.test_stichwortsuche_schnell
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import indexdb                                         # noqa: E402


class StichwortTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(indexdb, "_db_path", return_value=Path(self._tmp.name) / "memory.db")
        p.start()
        self.addCleanup(p.stop)
        self.con = indexdb._connect()
        self.addCleanup(self.con.close)
        zeilen = []
        for i in range(1500):                                   # „katze“ überall, „krea“ selten, „regen“ nie
            text = f"katze spielt im garten {i}" + (" mit krea gemalt" if i % 100 == 0 else "")
            ref = "chat:Fremde:1" if i % 100 == 50 else f"chat:{i}"
            zeilen.append((f"h{i}", "chat", ref, text, "2026-10-01 10:00"))
        self.con.executemany("INSERT INTO chunks(hash, kind, ref, text, datum) VALUES (?,?,?,?,?)", zeilen)
        self.con.commit()

    def test_woerter_ohne_treffer_und_allerweltswoerter_fallen_weg(self):
        self.assertEqual(["krea"], indexdb._seltene_woerter(self.con, ["katze", "krea", "regen"]))

    def test_nur_haeufige_woerter_dann_die_seltensten(self):
        self.assertEqual(["garten", "katze", "spielt"],
                         sorted(indexdb._seltene_woerter(self.con, ["katze", "spielt", "garten", "regen"])))

    def test_nichts_im_index_bleibt_wie_es_ist(self):
        self.assertEqual(["regen", "sonne"], indexdb._seltene_woerter(self.con, ["regen", "sonne"]))

    def test_findet_das_seltene_wort(self):
        z = indexdb._stichwort_treffer(self.con, "Weißt du noch, die Katze, die wir mit Krea gemalt haben?",
                                       None, "", "", None)
        self.assertTrue(z)
        self.assertTrue(all("krea" in t[3] for t in z))
        self.assertEqual(15, len(z))                            # 15 Brocken mit „krea“

    def test_filter_wirken_auch_zweistufig(self):
        with mock.patch.object(indexdb, "_profil_bedingung", return_value=("c.ref NOT GLOB 'chat:*:*'", [])):
            z = indexdb._stichwort_treffer(self.con, "katze krea", None, "", "", None)
        self.assertTrue(z)
        self.assertFalse(any(t[2].startswith("chat:Fremde") for t in z))

    def test_zu_wenige_nach_dem_filter_dann_volle_abfrage(self):
        with mock.patch.object(indexdb, "VORAUSWAHL", 5), mock.patch.object(indexdb, "KANDIDATEN", 50):
            z = indexdb._stichwort_treffer(self.con, "katze", None, "", "", None)
        self.assertEqual(50, len(z))                            # die erste Stufe hatte nur 5, die volle holt 50


if __name__ == "__main__":
    unittest.main()
