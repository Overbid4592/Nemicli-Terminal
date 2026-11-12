"""Mal-Sperre über Prozessgrenzen: Hauptprogramm und Mal-Prozess der Wache malen nie gleichzeitig,
ruhende Gewichte werden frei, sobald ein anderer Prozess malen will."""

import contextlib
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import malsperre  # noqa: E402

HALTER = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[2])
    import malsperre
    malsperre.ordner = lambda: Path(sys.argv[1])
    with malsperre.halten():
        print("bereit", flush=True)
        time.sleep(float(sys.argv[3]))
""")


class _Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name) / "malsperre"
        p = mock.patch.object(malsperre, "ordner", return_value=self.ordner)
        p.start()
        self.addCleanup(p.stop)

    def fremder_halter(self, sekunden: float) -> subprocess.Popen:
        self.ordner.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([sys.executable, "-c", HALTER, str(self.ordner), str(ROOT / "engines"),
                                 str(sekunden)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self._beenden, proc)
        self.assertEqual("bereit", proc.stdout.readline().strip())
        return proc

    @staticmethod
    def _beenden(proc: subprocess.Popen) -> None:
        """Mit dem venv-Starter auch den Python-Prozess dahinter beenden."""
        import psutil
        try:
            kinder = psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            kinder = []
        for k in kinder:
            with contextlib.suppress(psutil.Error):
                k.kill()
        proc.kill()
        proc.wait(timeout=10)
        psutil.wait_procs(kinder, timeout=10)
        proc.stdout.close()


class Sperre(_Basis):
    def test_marke_und_verschachtelt(self):
        with malsperre.halten():
            self.assertTrue((self.ordner / f"malt_{os.getpid()}").exists())
            with malsperre.halten():                                   # derselbe Prozess: kein Warten
                pass
            self.assertFalse(malsperre.fremd_will_malen())             # die eigene Marke zählt nicht
        self.assertFalse((self.ordner / f"malt_{os.getpid()}").exists())

    def test_wartet_auf_anderen_prozess(self):
        proc = self.fremder_halter(1.5)
        self.assertEqual(["malt"], [art for art, _ in malsperre.fremde()])   # PID: venv-Starter ≠ Python
        meldungen = []
        start = time.monotonic()
        with malsperre.halten(meldungen.append):
            gewartet = time.monotonic() - start
        self.assertGreater(gewartet, 0.8)
        self.assertEqual(1, len(meldungen))
        self.assertIn("wartet", meldungen[0])
        proc.wait(timeout=10)
        self.assertEqual([], malsperre.fremde())

    def test_grenze_beim_warten(self):
        self.fremder_halter(3)
        start = time.monotonic()
        with self.assertRaises(malsperre.Belegt):
            with malsperre.halten(max_warten=0.3):
                pass
        self.assertLess(time.monotonic() - start, 2)
        self.assertFalse((self.ordner / f"wartet_{os.getpid()}").exists())     # Marke wieder weg

    def test_tote_marken_fallen_weg(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "malt_999999").write_text("x")
        (self.ordner / "sperre").write_text("")
        with mock.patch("psutil.pid_exists", return_value=False):
            self.assertEqual([], malsperre.fremde())
        self.assertFalse((self.ordner / "malt_999999").exists())


class Einbindung(_Basis):
    def test_huelle_nimmt_die_sperre(self):
        import imagegen
        import malkind

        def paint(prompt, on_status=None):
            return malsperre.fremde(), (self.ordner / f"malt_{os.getpid()}").exists()
        with mock.patch.object(imagegen, "backend", return_value=imagegen.KREA), \
                mock.patch.object(imagegen, "_ollama_freigeben"), \
                mock.patch.object(malkind, "aktiv", return_value=False):
            self.assertEqual(([], True), imagegen._gpu_fuer_bild(paint)("x"))
        with mock.patch.object(imagegen, "backend", return_value=imagegen.KREA), \
                mock.patch.object(imagegen, "_ollama_freigeben"), \
                mock.patch.object(malkind, "aktiv", return_value=True), \
                mock.patch.object(malsperre, "halten") as halten:
            imagegen._gpu_fuer_bild(paint)("x")                        # Wache: ihr Mal-Prozess sperrt
            halten.assert_not_called()

    def test_serie_haelt_die_sperre_am_stueck(self):
        import actions
        import imagegen
        import malkind
        gesehen = []

        def bilder(a, varianten, anzeigen):
            gesehen.append((self.ordner / f"malt_{os.getpid()}").exists())
            return {"fertig": []}
        with mock.patch.object(actions, "_serie_bilder", side_effect=bilder), \
                mock.patch.object(imagegen, "backend", return_value=imagegen.KREA), \
                mock.patch.object(malkind, "aktiv", return_value=False):
            actions._serie_malen({}, ["a", "b"], anzeigen=False)
        with mock.patch.object(actions, "_serie_bilder", side_effect=bilder), \
                mock.patch.object(imagegen, "backend", return_value=imagegen.KREA), \
                mock.patch.object(malkind, "aktiv", return_value=True):
            actions._serie_malen({}, ["a"], anzeigen=False)
        self.assertEqual([True, False], gesehen)                  # Wache: ihr Mal-Prozess sperrt

    def test_mal_prozess_endet_wenn_fremd(self):
        import malkind
        auftraege = Path(self._tmp.name) / "auftraege"
        auftraege.mkdir()
        with mock.patch.object(malsperre, "fremd_will_malen", return_value=True), \
                mock.patch.object(malkind, "_ruhe_s", return_value=600.0), \
                mock.patch.dict(os.environ):                     # das Kind setzt NEMICLI_MALKIND
            start = time.monotonic()
            self.assertEqual(0, malkind.hauptprogramm([str(auftraege), str(os.getpid())]))
        self.assertLess(time.monotonic() - start, 2)

    def test_krea_gibt_ruhende_gewichte_frei(self):
        import krea
        with mock.patch.object(krea, "RUHE_TAKT_S", 0.05), \
                mock.patch.object(krea, "_beschaeftigt", return_value=False), \
                mock.patch.object(krea, "_fremd_malt", return_value=True), \
                mock.patch.object(krea, "entladen") as entladen:
            uhr = krea._Ruheuhr(600)
            uhr.start()
            uhr.join(timeout=5)
        entladen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
