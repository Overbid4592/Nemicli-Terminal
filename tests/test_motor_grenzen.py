"""Grenzen im eigenen GGUF-Motor ohne GPU: Bildgröße aus der mmproj, RAM-Rest beim Festsetzen,
voller Grafikspeicher als klare Meldung, Modellkopf einer verschwundenen Datei.

python -m unittest tests.test_motor_grenzen
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import gguflokal as G                                  # noqa: E402

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None


@unittest.skipIf(torch is None, "braucht torch")
class BildGroesse(unittest.TestCase):
    def _vision(self, lo, hi):
        from ggufengine import vision
        v = object.__new__(vision.Lfm3Vision)
        v.patch, v.merge, v.min_tokens, v.max_tokens = 16, 2, 64, 1024
        v.min_pixels, v.max_pixels = lo, hi
        return v

    def test_untergrenze_nie_ueber_obergrenze(self):
        v = self._vision(10**9, 4 * 10**6)               # min aus der Datei größer als max
        w, h = v.groesse(4000, 3000, 256)
        self.assertLessEqual(w * h, 256 * 32 * 32)

    def test_deckel_64_megapixel(self):
        from ggufengine import vision
        v = self._vision(65536, 10**12)
        w, h = v.groesse(100000, 100000, 10**9)
        self.assertLessEqual(w * h, vision.MAX_PIXELS)


class RamRest(unittest.TestCase):
    def test_festsetzen_laesst_acht_gb(self):
        try:
            from ggufengine import hostmem
        except Exception as exc:                          # pragma: no cover
            self.skipTest(f"hostmem nicht ladbar: {exc}")
        import psutil
        with patch.object(psutil, "virtual_memory", return_value=NS(available=10 << 30)):
            self.assertTrue(hostmem.ram_reicht(1 << 30))
            self.assertFalse(hostmem.ram_reicht(3 << 30))
            with self.assertRaises(RuntimeError):
                hostmem.empty(3 << 30, "cpu")             # vor jedem Treiberaufruf abgelehnt


class VollerGrafikspeicher(unittest.TestCase):
    @unittest.skipIf(torch is None, "braucht torch")
    def test_oom_erkannt(self):
        self.assertTrue(G.ist_oom(torch.OutOfMemoryError("CUDA out of memory")))
        self.assertFalse(G.ist_oom(RuntimeError("anders")))

    def test_nach_oom_raeumt_auf(self):
        engine = NS(reset=lambda: aufgerufen.append("reset"), _release_scratch=lambda: aufgerufen.append("frei"),
                    fremd=False, geleert=False)
        aufgerufen = []
        G._nach_oom(engine)
        self.assertEqual(aufgerufen, ["reset", "frei"])
        self.assertIn("/kontext", G.OOM_MELDUNG)


class TensorRtSignatur(unittest.TestCase):
    """Ohne gültige NVIDIA-Signatur der DLL wird nichts geladen und nichts installiert."""

    def setUp(self):
        import os
        import tempfile
        from trtmotor import laufzeit
        self.L = laufzeit
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sdk = Path(self.tmp.name)
        (self.sdk / "bin").mkdir()
        (self.sdk / "bin" / "tensorrt_rtx_1_0.dll").write_bytes(b"x")
        alt = dict(laufzeit._zustand)
        laufzeit._zustand.clear()
        self.addCleanup(lambda: (laufzeit._zustand.clear(), laufzeit._zustand.update(alt)))
        for p in (patch.object(laufzeit, "sdk_ordner", return_value=self.sdk),
                  patch.object(laufzeit, "_signatur_pruefen", return_value=True),
                  patch.object(os, "name", "nt"),
                  patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")})):
            p.start()
            self.addCleanup(p.stop)

    def test_unsigniert_abgelehnt(self):
        from wache import herkunft
        with patch.object(herkunft, "signatur", return_value={"status": "NotSigned", "signierer": "",
                                                              "gueltig": False}), \
                patch.object(self.L, "_selbst_installieren") as installieren:
            self.assertIsNone(self.L.laden())
        installieren.assert_not_called()
        self.assertIn("nicht gültig von NVIDIA signiert", self.L._zustand["grund"])

    def test_fremder_signierer_abgelehnt(self):
        from wache import herkunft
        with patch.object(herkunft, "signatur", return_value={"status": "Valid", "signierer": "Beispiel GmbH",
                                                              "gueltig": True}):
            self.assertIn("Beispiel GmbH", self.L.signatur_fehler(self.sdk))

    def test_nvidia_signiert_geht_weiter(self):
        from wache import herkunft
        with patch.object(herkunft, "signatur", return_value={"status": "Valid", "signierer": "NVIDIA Corporation",
                                                              "gueltig": True}):
            self.assertIsNone(self.L.signatur_fehler(self.sdk))


class Modellkopf(unittest.TestCase):
    def test_verschwundene_datei(self):
        with patch.object(G, "modelle", return_value={"weg": Path("gibt-es-nicht-1234.gguf")}):
            self.assertEqual(G._kopf_werte("weg"), (None, None, None, None))


if __name__ == "__main__":
    unittest.main()
