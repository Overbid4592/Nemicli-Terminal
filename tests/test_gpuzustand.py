"""CUDA-Sitzung unbrauchbar: Erkennen, keine GPU-Arbeit und kein Freigeben mehr, klare Meldung."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import gpuzustand as Z                                 # noqa: E402

ILLEGAL = ("CUDA error: an illegal memory access was encountered\n"
           "Returning 700 (CUDA_ERROR_ILLEGAL_ADDRESS) from cuStreamSynchronize")


class Grundlage(unittest.TestCase):
    def setUp(self):
        Z._zuruecksetzen()
        self.addCleanup(Z._zuruecksetzen)
        p = patch("fehlerwaechter.befund")
        self.befund = p.start()
        self.addCleanup(p.stop)


class ErkennenTests(Grundlage):

    def test_speicherfehler_sperrt(self):
        self.assertTrue(Z.pruefen(RuntimeError(ILLEGAL)))
        self.assertTrue(Z.kaputt())
        self.assertIn("illegal memory access", Z.grund())
        with self.assertRaises(Z.GpuKaputt) as fall:
            Z.sicherstellen()
        self.assertIn("neu starten", str(fall.exception))

    def test_auch_als_ursache(self):
        try:
            try:
                raise RuntimeError(ILLEGAL)
            except RuntimeError as innen:
                raise ValueError("Antwort ging nicht") from innen
        except ValueError as aussen:
            self.assertTrue(Z.pruefen(aussen))

    def test_andere_fehler_sperren_nicht(self):
        for text in ("CUDA out of memory. Tried to allocate 2.00 GiB", "No available kernel. Aborting execution.",
                     "context window of 8192 tokens exceeded"):
            self.assertFalse(Z.pruefen(RuntimeError(text)))
        self.assertFalse(Z.kaputt())
        Z.sicherstellen()

    def test_befund_nur_einmal(self):
        Z.pruefen(RuntimeError(ILLEGAL))
        Z.pruefen("unspecified launch failure")
        self.assertEqual(self.befund.call_count, 1)
        self.assertEqual(self.befund.call_args[0][0], "gpu")

    def test_eigene_meldung_zaehlt_nicht(self):
        self.assertFalse(Z.pruefen(Z.GpuKaputt()))
        self.assertFalse(Z.kaputt())

    def test_debugbericht_erkennt_mit(self):
        import debugbericht as D
        D.fehler("Erzeugen", RuntimeError(ILLEGAL))
        self.assertTrue(Z.kaputt())


class SperreTests(Grundlage):

    def test_sprachmodell_wird_nicht_freigegeben(self):
        import gguflokal as G

        class Motor:
            def leeren(self):
                raise AssertionError("darf nicht freigegeben werden")
        Z.pruefen(ILLEGAL)
        m = ("datei", 1, Motor())
        with patch.object(G, "_motor", m):
            G.entladen("Leerlauf")
            self.assertIs(G._motor, m)

    def test_kein_auslagern_fuers_bild(self):
        import gguflokal as G

        class Motor:
            def offload(self):
                raise AssertionError("keine GPU-Arbeit mehr")
        Z.pruefen(ILLEGAL)
        with patch.object(G, "_motor", ("x", 1, Motor())):
            with self.assertRaises(Z.GpuKaputt):
                with G.gpu_fuer_bild():
                    pass

    def test_kein_laden(self):
        import gguflokal as G
        Z.pruefen(ILLEGAL)
        with patch.object(G, "modelle", return_value={"m": "m.gguf"}), patch.object(G, "kontext", return_value=4096), \
                patch.object(G, "_motor", None), patch.object(G, "_torch_bereit",
                                                              side_effect=AssertionError("lädt nicht")):
            with self.assertRaises(Z.GpuKaputt):
                G._motor_laden("m")

    def test_kein_malen(self):
        import imagegen
        Z.pruefen(ILLEGAL)
        with self.assertRaises(Z.GpuKaputt):
            imagegen._gpu_fuer_bild(lambda: self.fail("malt nicht"))()

    def test_bildmotor_behaelt_den_ram(self):
        import krea
        Z.pruefen(ILLEGAL)
        with patch.dict(krea._RAM, {"dit": ("pfad", object())}, clear=True):
            krea.entladen()
            self.assertIn("dit", krea._RAM)


if __name__ == "__main__":
    unittest.main()
