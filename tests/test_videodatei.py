"""MP4 über Windows Media Foundation (engines/videodatei.py): NV12-Umrechnung, echte Datei, Vorab-Prüfung im
Video-Motor – ohne OpenCV im Schreibweg."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import video as V          # noqa: E402
import videodatei as VD    # noqa: E402


class Umrechnung(unittest.TestCase):
    def test_nv12_grenzwerte(self):
        bild = np.zeros((4, 4, 3), np.uint8)
        bild[:2] = 255
        d = VD.nv12(bild)
        self.assertEqual(len(d), 4 * 4 * 3 // 2)
        self.assertEqual(d[:8].tolist(), [235] * 8)                 # weiß -> Y 235
        self.assertEqual(d[8:16].tolist(), [16] * 8)                # schwarz -> Y 16
        self.assertEqual(d[16:].tolist(), [128] * 8)                # grau ohne Farbe

    def test_ungerade_groesse_wird_beschnitten(self):
        self.assertEqual(len(VD.nv12(np.zeros((5, 7, 3), np.uint8))), 4 * 6 * 3 // 2)


@unittest.skipUnless(VD.bereit(), "Windows Media Foundation fehlt")
class Schreiben(unittest.TestCase):
    def test_echte_datei(self):
        T, H, W = 12, 64, 48
        b = np.zeros((T, H, W, 3), np.uint8)
        b[:, :H // 2] = (255, 0, 0)                                 # oben rot, unten blau
        b[:, H // 2:] = (0, 0, 255)
        ziel = Path(tempfile.mkdtemp()) / "unter" / "probe.mp4"
        VD.schreiben(b, ziel, 24)
        daten = ziel.read_bytes()
        self.assertEqual(daten[4:8], b"ftyp")
        self.assertIn(b"avcC", daten)                               # H.264
        if importlib.util.find_spec("cv2") is None:
            return
        import cv2
        cap = cv2.VideoCapture(str(ziel))
        self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), T)
        ok, f = cap.read()
        cap.release()
        self.assertTrue(ok)
        self.assertGreater(int(f[8, 24, 2]), 200)                   # oben rot (BGR) -> richtig herum
        self.assertGreater(int(f[56, 24, 0]), 200)                  # unten blau

    def test_tonspur(self):
        """Sprechendes Porträt: AAC-Ton neben dem Bild, auch wenn der Ton länger ist als die Bilder."""
        b = np.zeros((25, 64, 48, 3), np.uint8)
        t = np.arange(24000 * 2) / 24000                            # 2 s Ton zu 1 s Bild
        ziel = Path(tempfile.mkdtemp()) / "ton.mp4"
        VD.schreiben(b, ziel, 25, ton=((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 24000))
        daten = ziel.read_bytes()
        self.assertIn(b"avcC", daten)
        self.assertIn(b"mp4a", daten)                               # AAC-Tonspur
        self.assertNotIn(b"mp4a", (lambda p: (VD.schreiben(b, p, 25), p.read_bytes())[1])(ziel.with_name("ohne.mp4")))

    def test_ton_auf_48_khz(self):
        pcm = VD.ton_vorbereiten(np.ones(24000, np.float32) * 2.0, 24000)       # 1 s, übersteuert
        self.assertEqual(len(pcm), VD.TON_RATE * 2)
        self.assertEqual(np.frombuffer(pcm, "<i2").max(), 32767)

    def test_folge_statt_block(self):
        """Bilder, die erst beim Schreiben entstehen (langes Video, wenig Speicher)."""
        erzeugt = []

        def folge():
            for i in range(9):
                erzeugt.append(i)
                yield np.full((64, 48, 3), i * 25, np.uint8)
        ziel = Path(tempfile.mkdtemp()) / "folge.mp4"
        VD.schreiben(folge(), ziel, 24)
        self.assertEqual(len(erzeugt), 9)
        self.assertEqual(ziel.read_bytes()[4:8], b"ftyp")
        if importlib.util.find_spec("cv2") is None:
            return
        import cv2
        cap = cv2.VideoCapture(str(ziel))
        self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 9)
        cap.release()

    def test_motor_nimmt_windows(self):
        self.assertEqual(V._mp4_weg(), "windows")

    def test_motor_schreibt_ohne_opencv(self):
        ziel = Path(tempfile.mkdtemp()) / "ohne_cv2.mp4"
        with mock.patch.dict(sys.modules, {"cv2": None}):           # wie in der exe: kein OpenCV
            V._mp4(np.random.randint(0, 255, (5, 64, 64, 3), dtype=np.uint8), ziel, 24)
        self.assertIn(b"avcC", ziel.read_bytes())


class VorabPruefung(unittest.TestCase):
    def test_ohne_schreiber_kein_rechnen(self):
        with mock.patch.object(V, "missing_reason", return_value=None), \
                mock.patch.object(V, "_mp4_weg", return_value=None), \
                mock.patch.object(V, "teile", side_effect=AssertionError("darf nicht laden")):
            with self.assertRaises(RuntimeError) as fehler:
                V.generate("a test", sekunden=1)
        self.assertIn("MP4", str(fehler.exception))


if __name__ == "__main__":
    unittest.main()
