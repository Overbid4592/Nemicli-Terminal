"""trtmotor: Bricht der Bau an einem fremden Prüfmodus ab ("called a synchronizing CUDA operation"), versucht
das nächste Bild/Video es neu; andere Fehler bleiben für die Sitzung gemerkt. Ohne Grafikkarte."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    from trtmotor import bild as TB, video as TV
except Exception:                                   # noqa: BLE001 – ohne torch kein trtmotor
    TB = TV = None


@unittest.skipIf(TB is None, "trtmotor nicht ladbar")
class Wiederholen(unittest.TestCase):
    def _lauf(self, modul, fehler):
        pfad = "probe.gguf"
        modul._stand[pfad] = {"baut": True}
        try:
            with patch.object(modul.laufzeit, "holen", side_effect=RuntimeError(fehler)), \
                    patch.object(modul, "_teile", lambda *a: {}, create=True), \
                    patch.object(modul, "plan", lambda d: {}, create=True), \
                    patch.object(modul, "gruppen", lambda d: [(0, 1)], create=True), \
                    patch.object(modul, "_DBG", create=True):
                if modul is TB:
                    TB._netz_laden_oder_bauen(pfad, None, None)
                else:
                    TV._laden_oder_bauen(pfad, {}, None, None, "fp8")
            return dict(modul._stand[pfad])
        finally:
            modul._stand.pop(pfad, None)

    def test_bild(self):
        st = self._lauf(TB, "called a synchronizing CUDA operation")
        self.assertFalse(st["baut"])
        self.assertIsNone(st["fehler"])
        self.assertEqual(self._lauf(TB, "kaputt")["fehler"], "kaputt")

    def test_video(self):
        self.assertIsNone(self._lauf(TV, "called a synchronizing CUDA operation")["fehler"])
        self.assertEqual(self._lauf(TV, "kaputt")["fehler"], "kaputt")


class HostLesenNurImEigenenFaden(unittest.TestCase):
    """Die Prüfung vor der Aufnahme mehrerer Token meldet Lesen zur CPU – aber nur im eigenen Faden.
    Früher galt sie prozessweit, und der TensorRT-Bau nebenan scheiterte daran."""

    def test_eigener_faden_gemeldet_anderer_frei(self):
        import threading
        import time
        try:
            import torch
            from ggufengine.models.common import _host_lesen_finden
        except Exception:                                   # noqa: BLE001
            self.skipTest("torch fehlt")
        if not torch.cuda.is_available():
            self.skipTest("keine Grafikkarte")
        x = torch.arange(4, device="cuda")
        anderer = {}

        def nebenan():
            time.sleep(0.1)
            anderer["wert"] = int(x[2])
            anderer["cpu"] = x.cpu().tolist()
        t = threading.Thread(target=nebenan)
        t.start()
        with _host_lesen_finden():
            time.sleep(0.3)
            with self.assertRaisesRegex(RuntimeError, "synchronizing"):
                int(x[1])
            with self.assertRaisesRegex(RuntimeError, "synchronizing"):
                x.cpu()
            self.assertEqual(int((x + 1).sum().numel()), 1)        # numel liest nichts von der Karte
        t.join()
        self.assertEqual(anderer, {"wert": 2, "cpu": [0, 1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
