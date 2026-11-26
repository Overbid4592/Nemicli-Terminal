"""Sprechen 2.0: Mundformen und Körpersprache aus der Stimme, mitwandernde Freistell-Maske, Kugel mit Ton."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

from liveportrait import sprechen as SP   # noqa: E402

RATE = 24000
FPS = 25


def _vokal(f1: float, f2: float, sekunden: float = 0.6, hz: float = 200.0, oben: bool = True) -> np.ndarray:
    """Synthetischer Vokal: Pulsfolge durch zwei Resonatoren (F1, F2). oben=False: Nasal ohne Obertöne."""
    n = int(sekunden * RATE)
    x = np.zeros(n)
    x[::int(RATE / hz)] = 1.0
    for f, band in ((f1, 80.0), (f2, 120.0)):
        r = np.exp(-np.pi * band / RATE)
        a1, a2 = -2 * r * np.cos(2 * np.pi * f / RATE), r * r
        y = np.zeros(n)
        for i in range(n):
            y[i] = x[i] - a1 * (y[i - 1] if i else 0) - a2 * (y[i - 2] if i > 1 else 0)
        x = y
    if not oben:                                         # tiefpassen: kaum Energie über 1 kHz
        k = int(RATE / 600)
        x = np.convolve(x, np.ones(k) / k, mode="same")
    return (0.5 * x / np.abs(x).max()).astype(np.float32)


def _mitte(lippen: list[dict], von: float, bis: float) -> dict:
    teil = lippen[int(von * FPS):int(bis * FPS)]
    return {n: float(np.median([t[n] for t in teil])) for n in SP.LIPPEN}


class Formanten(unittest.TestCase):
    def test_vokale_werden_erkannt(self):
        for f1, f2 in ((850, 1400), (300, 2500), (350, 800)):
            k = SP.analyse(_vokal(f1, f2), RATE, FPS)
            mitte = [c for c in k[3:-3] if c.f1]
            self.assertLess(abs(np.median([c.f1 for c in mitte]) - f1), 90, (f1, f2))
            self.assertLess(abs(np.median([c.f2 for c in mitte]) - f2), 250, (f1, f2))
            self.assertGreater(np.median([c.hz for c in mitte]), 150)

    def test_mundformen(self):
        a = _mitte(SP.lippen(SP.analyse(_vokal(850, 1400), RATE, FPS), FPS), 0.2, 0.5)
        i = _mitte(SP.lippen(SP.analyse(_vokal(300, 2500), RATE, FPS), FPS), 0.2, 0.5)
        u = _mitte(SP.lippen(SP.analyse(_vokal(350, 800), RATE, FPS), FPS), 0.2, 0.5)
        self.assertGreater(a["mund"], 50)                                # A: weit offen
        self.assertGreater(i["grinsen"], 8)                              # I: breit
        self.assertGreater(u["spitzen"], 8)                              # U: rund
        self.assertLess(i["mund"], a["mund"] / 2)
        self.assertLess(a["spitzen"] + a["grinsen"], 3)

    def test_nasal_und_luecke_schliessen_die_lippen(self):
        nasal = SP.lippen(SP.analyse(_vokal(250, 1200, oben=False), RATE, FPS), FPS)
        self.assertLess(_mitte(nasal, 0.2, 0.5)["mund"], -10)            # M: Lippen zu
        stille = np.zeros(int(0.1 * RATE), np.float32)
        folge = np.concatenate([_vokal(850, 1400, 0.4), stille, _vokal(850, 1400, 0.4)])
        lp = SP.lippen(SP.analyse(folge, RATE, FPS), FPS)
        self.assertLess(min(v["mund"] for v in lp[10:13]), -15)          # B/P mitten im Satz
        self.assertEqual(lp[-1]["mund"] >= 0, True)

    def test_stille_und_zischlaut(self):
        self.assertTrue(all(v == {"mund": 0.0, "grinsen": 0.0, "spitzen": 0.0}
                            for v in SP.lippen(SP.analyse(np.zeros(RATE, np.float32), RATE, FPS), FPS)))
        rausch = np.random.default_rng(1).standard_normal(int(0.5 * RATE)).astype(np.float32)
        rausch = np.diff(rausch, prepend=0).astype(np.float32) * 0.3     # hochpass: S
        folge = np.concatenate([_vokal(850, 1400, 0.4), rausch])
        lp = SP.lippen(SP.analyse(folge, RATE, FPS), FPS)
        s = _mitte(lp, 0.6, 0.85)
        self.assertGreater(s["grinsen"], 4)
        self.assertLess(s["mund"], 15)


class Koerpersprache(unittest.TestCase):
    def setUp(self):
        stille = np.zeros(int(0.5 * RATE), np.float32)
        self.welle = np.concatenate([_vokal(850, 1400, 0.5), stille, _vokal(500, 2000, 0.5), stille])
        self.klang = SP.analyse(self.welle, RATE, FPS)

    def test_frage_hebt_die_brauen(self):
        k = SP.koerpersprache(self.klang, FPS, "Wie war dein Tag?", seed=1)
        self.assertTrue(any(s.get("augenbrauen") == 14.0 for s in k))
        k = SP.koerpersprache(self.klang, FPS, "Das war schön.", seed=1)
        self.assertFalse(any(s.get("augenbrauen") == 14.0 for s in k))

    def test_blinzeln_in_der_pause_und_nicken_auf_betonung(self):
        k = SP.koerpersprache(self.klang, FPS, "", seed=2)
        zu = [s["t"] for s in k if s.get("augen") == 0.0]
        self.assertTrue(any(0.5 < t < 1.1 for t in zu))                  # in der ersten Pause
        self.assertTrue(any(s.get("nicken", 0) > 1.5 for s in k))
        self.assertEqual(k, SP.koerpersprache(self.klang, FPS, "", seed=2))   # gleicher Seed, gleicher Ablauf

    def test_nachblinzeln_nach_jeder_pause(self):
        pause = [(int(0.8 * FPS), int(1.2 * FPS)), (int(7.8 * FPS), int(8.2 * FPS))]
        klang = [SP.Bildklang(0.0 if any(a <= i < b for a, b in pause) else 1.0, 0, 0, 0, 0)
                 for i in range(15 * FPS)]
        k = SP.koerpersprache(klang, FPS, "", seed=3)
        self.assertEqual(len([s for s in k if s.get("augen") == 0.0]), 4)   # je Pause und je einmal danach


class MaskeWandertMit(unittest.TestCase):
    def _quelle(self, alpha):
        import torch
        from liveportrait import motor
        g = 512
        a = torch.from_numpy(alpha)[None, None]
        return motor.Quelle(bild=torch.zeros(1, 3, g, g), merkmal=None, kp=None, ausdruck=None, skala=None, t=None,
                            winkel=None, x_s=None, augen=(0.3, 0.3), M_c2o=np.eye(3), maske=torch.ones(1, 1, g, g),
                            alpha=a, alpha_ausschnitt=a.clone())

    def test_ohne_bewegung_bleibt_die_maske(self):
        import torch
        from liveportrait import motor, netze
        alpha = np.zeros((512, 512), np.float32)
        alpha[100:400, 150:350] = 1
        q = self._quelle(alpha)
        eigen = netze.koordinatengitter(1, 64, 64, ref=torch.zeros(1))[0, ..., :2][None]
        aus = motor.alpha_mitbewegen(q, eigen)[0, 0].numpy()
        self.assertLess(np.abs(aus - alpha).mean(), 0.002)

    def test_verschiebung_verschiebt_die_maske(self):
        import torch
        from liveportrait import motor, netze
        alpha = np.zeros((512, 512), np.float32)
        alpha[:, :256] = 1
        q = self._quelle(alpha)
        eigen = netze.koordinatengitter(1, 64, 64, ref=torch.zeros(1))[0, ..., :2][None]
        schub = eigen.clone()
        schub[..., 0] -= 0.1                       # jede Zielstelle holt von 0,05 Bildbreiten weiter links
        aus = motor.alpha_mitbewegen(q, schub)[0, 0].numpy()
        kante = int(np.argmax(aus[256] < 0.5))
        self.assertLess(abs(kante - (256 + 0.05 * 512)), 2)

    def test_fluss_ohne_bewegung_ist_das_gitter_und_aendert_nichts(self):
        import torch
        from liveportrait import netze
        torch.manual_seed(0)
        w = netze.bauen()["W"].eval()
        f = torch.randn(1, 32, 16, 64, 64)
        kp = torch.rand(1, 21, 3) * 2 - 1
        with torch.no_grad():
            allein = w(f, kp, kp)
            mit, fluss = w(f, kp, kp, mit_fluss=True)
        self.assertTrue(torch.equal(allein, mit))                       # die Rechnung bleibt bit-genau
        eigen = netze.koordinatengitter(1, 64, 64, ref=f)[0, ..., :2]
        self.assertLess(float((fluss[0] - eigen).abs().max()), 1e-5)


class Bilder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_bild_mit_und_ohne_alpha(self):
        from PIL import Image
        from liveportrait import gesicht
        rgba = np.zeros((64, 48, 4), np.uint8)
        rgba[..., 0] = 255
        rgba[10:50, 10:40, 3] = 255
        Image.fromarray(rgba, "RGBA").save(self.tmp / "frei.png")
        rgb, alpha = gesicht.bild_und_alpha(self.tmp / "frei.png")
        self.assertEqual(alpha.shape, rgb.shape[:2])
        self.assertEqual(float(alpha[20, 20]), 1.0)
        self.assertEqual(tuple(rgb[0, 0]), (255, 0, 0))                  # durchsichtig: Randfarbe der Figur
        self.assertEqual(tuple(rgb[20, 20]), (255, 0, 0))
        Image.fromarray(rgba[..., :3]).save(self.tmp / "voll.png")
        self.assertIsNone(gesicht.bild_und_alpha(self.tmp / "voll.png")[1])

    def test_figur_kasten_und_freie_bilder(self):
        import torch
        from liveportrait import motor
        alpha = torch.zeros(1, 1, 100, 80)
        alpha[..., 20:90, 10:50] = 1
        l, o, r, u = motor._figur_kasten(alpha)
        self.assertEqual(r - l, u - o)                                   # quadratisch
        self.assertTrue(l <= 10 and r >= 50 and o <= 20 and u >= 90)
        ganz = torch.rand(2, 3, 100, 80)
        figur = motor._frei_bilder(ganz, alpha.expand(2, -1, -1, -1), (l, o, r, u), 32)
        self.assertEqual(figur.shape, (2, 32, 32, 4))
        self.assertEqual(int(figur[0, 0, 0, 3]), 0)                      # Ecke: durchsichtig
        self.assertEqual(int(figur[0, 16, 16, 3]), 255)

    def test_ton_neben_dem_video(self):
        import wave
        from liveportrait import motor
        p = motor.ton_schreiben(np.zeros(2400, np.float32), 24000, self.tmp / "v.wav")
        with wave.open(str(p)) as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()), (1, 24000, 2400))


class KugelMitTon(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_ton_nur_passend_zum_porträt(self):
        from wache import steuerung
        for n in ("v_frei_kugel.webp", "v_kugel.webp", "v.wav", "x.wav"):
            (self.tmp / n).write_bytes(b"x")
        self.assertIsNone(steuerung.ton_pruefen(str(self.tmp / "v.wav"), str(self.tmp / "v_frei_kugel.webp")))
        self.assertIsNone(steuerung.ton_pruefen(str(self.tmp / "v.wav"), str(self.tmp / "v_kugel.webp")))
        self.assertIsNotNone(steuerung.ton_pruefen(str(self.tmp / "x.wav"), str(self.tmp / "v_kugel.webp")))
        self.assertIsNotNone(steuerung.ton_pruefen(str(self.tmp / "v_kugel.webp"), str(self.tmp / "v_kugel.webp")))

    def test_frei_ohne_kreis_und_ton_spielt_mit(self):
        import time as _t
        from PIL import Image
        from PySide6.QtWidgets import QApplication
        from wache import kugel as K
        app = QApplication.instance() or QApplication([])
        folge = [Image.new("RGBA", (32, 32), (i * 60, 0, 0, 128)) for i in range(3)]
        folge[0].save(self.tmp / "f_frei_kugel.webp", save_all=True, append_images=folge[1:], duration=20, loop=0)
        gespielt = []
        winsound = type("W", (), {"SND_FILENAME": 1, "SND_ASYNC": 2, "SND_NODEFAULT": 4,
                                  "PlaySound": staticmethod(lambda p, f: gespielt.append(p))})
        f = K._Kugelfenster()
        fertig = []
        with patch.dict(sys.modules, {"winsound": winsound}):
            f.film_starten(self.tmp / "f_frei_kugel.webp", lambda: fertig.append(1), runden=1,
                           ton=str(self.tmp / "f.wav"))
            ende = _t.time() + 5
            rund = set()
            while not fertig and _t.time() < ende:
                app.processEvents()
                rund.add(f._rund)
                _t.sleep(0.01)
        self.assertEqual(fertig, [1])
        self.assertEqual(rund, {False})                                  # freigestellt: kein Kreis
        self.assertEqual(gespielt, [str(self.tmp / "f.wav"), None])      # Ton an, am Ende aus


if __name__ == "__main__":
    unittest.main()
