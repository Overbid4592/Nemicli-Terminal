"""Gedächtnis im Takt des Chats: Zwischenstand aus Stichworten, wenn die volle Suche das Zeitbudget
überschreitet; Stichwort-Treffer nur einmal je Suche; Zeitgrenze des Rerankers; Statistik; Index vorwärmen;
gemerkte Notizen überstehen einen Neustart vor dem Einbetten.

python -m unittest tests.test_gedaechtnis_vorab
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import indexdb                                         # noqa: E402
import memory                                          # noqa: E402
import notstopp                                        # noqa: E402
import persona                                         # noqa: E402
from test_agent_rag import _Basis                      # noqa: E402


def _statistik_leeren():
    persona._SUCHE_STATISTIK.update(rechtzeitig=0, vorab=0, ohne=0, dauer_ms=[])


class Zwischenstand(unittest.TestCase):

    def setUp(self):
        self.addCleanup(persona._runde.update, frage=None, block=None, fuer=None, suche=None)
        _statistik_leeren()
        self.addCleanup(_statistik_leeren)
        self.los = threading.Event()
        self.addCleanup(self.los.set)

    def _langsam(self, frage, **_k):
        indexdb.VORAB.melden("# nur Stichworte")
        self.los.wait(5)
        return "# voll"

    def test_vorab_statt_nichts_dann_voll(self):
        with mock.patch.object(indexdb, "guide_block", side_effect=self._langsam):
            persona.runde_beginnt("Wie hieß der Hund?")
            time.sleep(0.05)
            t = time.perf_counter()
            self.assertEqual("# nur Stichworte", persona._gedaechtnis_block(""))
            self.assertLess(time.perf_counter() - t, persona.GEDAECHTNIS_WARTEN_S + 0.3)
            self.los.set()
            persona._runde["suche"].fertig.wait(5)
            self.assertEqual("# voll", persona._gedaechtnis_block(""))          # späterer Schritt
        self.assertEqual(1, persona._SUCHE_STATISTIK["vorab"])
        self.assertEqual(0, persona._SUCHE_STATISTIK["rechtzeitig"])
        self.assertIn("1 nur Stichworte", persona.gedaechtnis_statistik())

    def test_rechtzeitig_zaehlt_einmal_je_runde(self):
        with mock.patch.object(indexdb, "guide_block", return_value="treffer"):
            persona.runde_beginnt("Frage")
            for _ in range(3):
                self.assertEqual("treffer", persona._gedaechtnis_block(""))
        self.assertEqual(1, persona._SUCHE_STATISTIK["rechtzeitig"])
        # Suchfäden früherer Tests dürfen nachträglich eintragen: nur die eigene Dauer prüfen.
        self.assertIn(persona._runde["suche"].dauer_ms, persona._SUCHE_STATISTIK["dauer_ms"])
        self.assertIn("1 von 1 Runden vollständig", persona.gedaechtnis_statistik())

    def test_ohne_zwischenstand_leer(self):
        with mock.patch.object(indexdb, "guide_block", side_effect=lambda f, **_k: self.los.wait(5) and "x"):
            persona.runde_beginnt("Frage")
            self.assertEqual("", persona._gedaechtnis_block(""))
        self.assertEqual(1, persona._SUCHE_STATISTIK["ohne"])

    def test_melder_nur_im_suchfaden(self):
        with mock.patch.object(indexdb, "guide_block", return_value=""):
            persona.runde_beginnt("Frage")
            persona._runde["suche"].fertig.wait(2)
        self.assertIsNone(getattr(indexdb.VORAB, "melden", None))


class StufenDerSuche(_Basis):

    def test_nur_worte_ohne_index_und_encoder(self):
        self.fuellen(mit_vektoren=True)
        self.encoder = True
        with mock.patch.object(indexdb, "_index", side_effect=AssertionError("Index im Zwischenstand")), \
                mock.patch.object(memory, "frage_einbetten", side_effect=AssertionError("Encoder")):
            treffer = indexdb.suchen("KB5124010", nur_worte=True)
        self.assertEqual("bericht:Wache_20260920-1200.md", treffer[0]["ref"])
        self.assertEqual("wort", treffer[0]["wie"])

    def test_guide_block_meldet_vorab_und_fragt_stichworte_einmal(self):
        self.fuellen(mit_vektoren=False)
        indexdb._upsert("note", "note:1", ["Der Hund heißt Bello und mag Krea-Bilder vom Wald."])
        gemeldet = []
        echte = indexdb._stichwort_treffer
        aufrufe = []
        indexdb.VORAB.melden = gemeldet.append
        try:
            with mock.patch.object(indexdb, "_stichwort_treffer",
                                   side_effect=lambda *a: aufrufe.append(a[1]) or echte(*a)):
                block = indexdb.guide_block("Wie heißt der Hund Bello?")
        finally:
            indexdb.VORAB.melden = None
        self.assertEqual(1, len(gemeldet))
        self.assertIn("Bello", gemeldet[0])
        self.assertIn("Bello", block)
        self.assertEqual(1, len(aufrufe))                     # die volle Suche nimmt die gemerkten Treffer
        self.assertIsNone(getattr(indexdb._MERK, "worte", None))

    def test_ohne_melder_wie_bisher(self):
        self.fuellen(mit_vektoren=False)
        with mock.patch.object(indexdb, "search", lambda q, k: []):
            self.assertEqual("", indexdb.guide_block("KB5124010"))


class _Langsam:
    device = type("D", (), {"type": "cpu"})()

    def __init__(self, dauer):
        self.dauer, self.bewertet = dauer, 0

    def bewerten(self, frage, dokumente):
        time.sleep(self.dauer * len(dokumente))
        self.bewertet += len(dokumente)
        return [0.9 if "richtig" in d else 0.1 for d in dokumente]


class RerankerZeitgrenze(unittest.TestCase):

    def setUp(self):
        import reranker
        self.r = reranker
        self.modell = _Langsam(0.05)
        for name, wert in (("_laden", lambda: self.modell), ("verfuegbar", lambda: True),
                           ("_nachlauf", lambda: None), ("RERANK_BUDGET_S", 0.15)):
            p = mock.patch.object(reranker, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def _treffer(self, n):
        return [{"kind": "chat", "ref": f"chat:{i}", "text": "richtig" if i == 1 else f"falsch {i}",
                 "score": 1.0 - i / 100, "wie": "wort", "rowid": i, "aehnlich": None} for i in range(n)]

    def test_bricht_nach_budget_ab_und_behaelt_alle(self):
        t = time.perf_counter()
        raus = self.r.nachsortieren("frage", self._treffer(16))
        self.assertLess(time.perf_counter() - t, 0.6)
        self.assertLess(self.modell.bewertet, 16)
        self.assertEqual(16, len(raus))
        self.assertEqual("richtig", raus[0]["text"])
        bewertet = [h for h in raus if "relevanz" in h]
        self.assertEqual(self.modell.bewertet, len(bewertet))
        self.assertEqual(raus[:len(bewertet)], bewertet)          # Unbewertete stehen dahinter …
        rest = [h["ref"] for h in raus[len(bewertet):]]
        self.assertEqual(sorted(rest, key=lambda r: int(r.split(":")[1])), rest)   # … in Fusions-Reihenfolge

    def test_schnell_genug_alle_bewertet(self):
        self.modell.dauer = 0.0
        raus = self.r.nachsortieren("frage", self._treffer(16))
        self.assertTrue(all("relevanz" in h for h in raus))


class Vorwaermen(_Basis):

    def test_baut_nach_der_runde(self):
        self.fuellen(mit_vektoren=True)
        indexdb._invalidate()
        laeuft = {"ja": True}
        with mock.patch.object(notstopp, "runde_laeuft", lambda: laeuft["ja"]), \
                mock.patch.object(memory, "RUHE_TAKT_S", 0.02):
            indexdb.vorwaermen()
            time.sleep(0.15)
            self.assertIsNone(indexdb._CACHE["stamp"])           # Runde läuft: noch nichts gebaut
            laeuft["ja"] = False
            indexdb._index_bau.join(5)
        self.assertIsNotNone(indexdb._CACHE["stamp"])
        self.assertTrue(indexdb._CACHE["meta"])


class Neustart(_Basis):

    def test_gemerkte_notiz_kommt_nach_neustart_in_den_index(self):
        datei = Path(self.tmp.name) / "learned" / "memory.json"
        with mock.patch.object(memory, "_FILE", datei):              # umgelenkt: eine Datei, kein Profil
            e = memory.remember("Der Kater heißt Mikesch und schläft gern auf dem Drucker.", "fakt")
            self.assertIsNotNone(e)
            memory._gelesen.clear()                              # „Neustart“: kein Stand im Speicher
            indexdb._invalidate()
            indexdb.after_turn(None)
            treffer = indexdb.suchen("Mikesch")
        self.assertTrue(treffer)
        self.assertEqual(f"note:{e['id']}", treffer[0]["ref"])


if __name__ == "__main__":
    unittest.main()
