"""Gedächtnissuche und Embedding halten den Chat nicht auf: die Suche läuft ab Rundenbeginn im Hintergrund,
der Prompt wartet höchstens GEDAECHTNIS_WARTEN_S; Encoder laden und Index bauen beginnen erst nach der Runde.

python -m unittest tests.test_gedaechtnis_im_hintergrund
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import indexdb                                         # noqa: E402
import memory                                          # noqa: E402
import notstopp                                        # noqa: E402
import persona                                         # noqa: E402


class PromptWartetNichtTests(unittest.TestCase):

    def setUp(self):
        self.addCleanup(persona._runde.update, frage=None, block=None, fuer=None, suche=None)
        self.losgelassen = threading.Event()

    def _langsam(self, frage, **_k):
        self.losgelassen.wait(5)
        return f"# Gedächtnis zu {frage}"

    def test_langsame_suche_haelt_den_prompt_nicht_auf(self):
        with mock.patch.object(indexdb, "guide_block", side_effect=self._langsam):
            persona.runde_beginnt("Wie hieß der Hund?")
            t = time.perf_counter()
            self.assertEqual("", persona._gedaechtnis_block(""))
            self.assertLess(time.perf_counter() - t, persona.GEDAECHTNIS_WARTEN_S + 0.3)
            self.losgelassen.set()                                   # Suche wird fertig …
            persona._runde["suche"].fertig.wait(5)
            self.assertEqual("# Gedächtnis zu Wie hieß der Hund?", persona._gedaechtnis_block(""))   # … nächster Schritt

    def test_suche_beginnt_schon_mit_der_runde(self):
        gestartet = threading.Event()
        with mock.patch.object(indexdb, "guide_block", side_effect=lambda f, **_k: gestartet.set() or "x"):
            persona.runde_beginnt("Frage")
            self.assertTrue(gestartet.wait(2))                      # ohne dass der Prompt schon fragt
            self.assertEqual("x", persona._gedaechtnis_block(""))

    def test_einmal_je_runde(self):
        with mock.patch.object(indexdb, "guide_block", return_value="treffer") as suche:
            persona.runde_beginnt("Frage")
            for _ in range(3):                                       # Werkzeug-Schritte derselben Runde
                self.assertEqual("treffer", persona._gedaechtnis_block("Ergebnis von 'datei_lesen' …"))
            self.assertEqual(1, suche.call_count)

    def test_fehler_der_suche_bricht_nichts(self):
        with mock.patch.object(indexdb, "guide_block", side_effect=RuntimeError("db zu")):
            persona.runde_beginnt("Frage")
            persona._runde["suche"].fertig.wait(2)
            self.assertEqual("", persona._gedaechtnis_block(""))


class HintergrundNachDerRundeTests(unittest.TestCase):

    def setUp(self):
        notstopp._zustand["laufend"] = 0
        self.addCleanup(notstopp._zustand.__setitem__, "laufend", 0)

    def test_warten_bis_ruhe(self):
        notstopp.runde_aktiv(True)
        fertig = threading.Event()
        with mock.patch.object(memory, "RUHE_TAKT_S", 0.02):
            f = threading.Thread(target=lambda: (memory.warten_bis_ruhe(), fertig.set()))
            f.start()
            self.assertFalse(fertig.wait(0.3))                      # Runde läuft: wartet
            notstopp.runde_aktiv(False)
            self.assertTrue(fertig.wait(2))
            f.join(2)

    def test_encoder_laedt_erst_nach_der_runde(self):
        geladen = threading.Event()
        notstopp.runde_aktiv(True)
        with mock.patch.object(memory, "RUHE_TAKT_S", 0.02), mock.patch.object(memory, "_encoder", None), \
                mock.patch.object(memory, "_warm_thread", None), mock.patch.object(memory, "gpu_pause", return_value=False), \
                mock.patch.object(memory, "_get_encoder", side_effect=lambda: geladen.set()):
            memory.warm_encoder()
            self.assertFalse(geladen.wait(0.3))
            notstopp.runde_aktiv(False)
            self.assertTrue(geladen.wait(2))
            memory._warm_thread.join(2)

    def test_index_baut_erst_nach_der_runde(self):
        reihenfolge = []
        with mock.patch.object(memory, "warten_bis_ruhe", side_effect=lambda: reihenfolge.append("ruhe")), \
                mock.patch.object(indexdb, "_index", side_effect=lambda *a, **k: reihenfolge.append("bauen")):
            indexdb._index_nach_der_runde()
        self.assertEqual(["ruhe", "bauen"], reihenfolge)


if __name__ == "__main__":
    unittest.main()
