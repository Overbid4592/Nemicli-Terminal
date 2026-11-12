"""Offline-Tests für das Nachmalen von Gesichtern (engines/gesichter.py, krea.gesicht_nachbessern).

Ohne Modell und ohne GPU: Rauschplan ab Startstärke, VAE-Encoder gegen eine
3D-Referenz, Einblenden, Ablauf mit nachgebildetem Malen und die Einbindung.

python -m unittest discover -s tests -p test_gesichter.py
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import gesichter  # noqa: E402
import imagegen  # noqa: E402
import krea  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, PngImagePlugin  # noqa: E402

try:
    import torch
except Exception:                                   # pragma: no cover
    torch = None


def _krea_png(pfad: Path, params="steps=14, cfg=1, size=256x256, seed=41, sampler=euler, "
                                 "shift=1.15, backend=krea2", lora_datei: str = "") -> None:
    meta = PngImagePlugin.PngInfo()
    meta.add_text("prompt", "A woman in a garden.")
    meta.add_text("model", "k2")
    meta.add_text("params", params)
    if lora_datei:
        meta.add_text("lora_datei", lora_datei)
    Image.fromarray(np.full((256, 256, 3), 100, np.uint8)).save(pfad, pnginfo=meta)


@unittest.skipIf(torch is None, "torch fehlt")
class Rauschplan(unittest.TestCase):
    def test_beginnt_bei_der_staerke_und_endet_bei_null(self):
        s = krea._sigmas_ab(10, 1.15, 0.45)
        self.assertEqual(len(s), 11)
        self.assertAlmostEqual(float(s[0]), 0.45, places=5)
        self.assertEqual(float(s[-1]), 0.0)
        self.assertTrue(bool((s[:-1] > s[1:]).all()))

    def test_volle_staerke_gleicht_dem_normalen_plan(self):
        # _sigmas rundet auf ein Raster von 10000 Stützstellen
        self.assertTrue(torch.allclose(krea._sigmas_ab(14, 1.15, 1.0), krea._sigmas(14, 1.15), atol=5e-4))


@unittest.skipIf(torch is None, "torch fehlt")
class VAEEncoder(unittest.TestCase):
    """Wan-Encoder in Originalbenennung (3D-Kerne) gegen eine kausale 3D-Rechnung."""

    def _gewichte(self):
        g = torch.Generator().manual_seed(0)

        def r(*form):
            return torch.randn(*form, generator=g) * 0.3

        sd = {}

        def conv(p, cin, cout, k=3):
            sd[p + ".weight"], sd[p + ".bias"] = r(cout, cin, k, k, k) if k > 1 else r(cout, cin, 1, 1, 1), r(cout)

        def res(p, cin, cout):
            sd[p + ".residual.0.gamma"] = 1 + r(cin, 1, 1, 1)
            conv(p + ".residual.2", cin, cout)
            sd[p + ".residual.3.gamma"] = 1 + r(cout, 1, 1, 1)
            conv(p + ".residual.6", cout, cout)
            if cin != cout:
                conv(p + ".shortcut", cin, cout, 1)

        conv("encoder.conv1", 3, 8)
        res("encoder.downsamples.0", 8, 8)
        sd["encoder.downsamples.1.resample.1.weight"], sd["encoder.downsamples.1.resample.1.bias"] = r(8, 8, 3, 3), r(8)
        res("encoder.downsamples.2", 8, 16)
        sd["encoder.downsamples.3.resample.1.weight"], sd["encoder.downsamples.3.resample.1.bias"] = r(16, 16, 3, 3), r(16)
        sd["encoder.downsamples.3.time_conv.weight"], sd["encoder.downsamples.3.time_conv.bias"] = r(16, 16, 3, 1, 1), r(16)
        sd["encoder.downsamples.4.resample.1.weight"], sd["encoder.downsamples.4.resample.1.bias"] = r(16, 16, 3, 3), r(16)
        res("encoder.middle.0", 16, 16)
        sd["encoder.middle.1.norm.gamma"] = 1 + r(16, 1, 1)
        sd["encoder.middle.1.to_qkv.weight"], sd["encoder.middle.1.to_qkv.bias"] = r(48, 16, 1, 1), r(48)
        sd["encoder.middle.1.proj.weight"], sd["encoder.middle.1.proj.bias"] = r(16, 16, 1, 1), r(16)
        res("encoder.middle.2", 16, 16)
        sd["encoder.head.0.gamma"] = 1 + r(16, 1, 1, 1)
        conv("encoder.head.2", 16, 32)
        conv("conv1", 32, 32, 1)
        return sd

    @staticmethod
    def _referenz(sd, x):
        """Ein Bild als Video mit einem Bild: kausale 3D-Faltung mit zwei Null-Bildern davor."""
        import torch.nn.functional as F
        x = x[:, :, None]

        def conv(p, x):
            w = sd[p + ".weight"]
            if w.shape[2] == 3:
                x = F.pad(x, (1, 1, 1, 1, 2, 0))
            return F.conv3d(x, w, sd[p + ".bias"])

        def norm(p, x):
            return F.normalize(x, dim=1) * (x.shape[1] ** 0.5) * sd[p + ".gamma"]

        def res(p, x):
            h = conv(p + ".residual.2", F.silu(norm(p + ".residual.0", x)))
            h = conv(p + ".residual.6", F.silu(norm(p + ".residual.3", h)))
            return h + (conv(p + ".shortcut", x) if p + ".shortcut.weight" in sd else x)

        def runter(p, x):
            b, c, t, h, w = x.shape
            y = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
            y = F.conv2d(F.pad(y, (0, 1, 0, 1)), sd[p + ".resample.1.weight"], sd[p + ".resample.1.bias"], stride=2)
            return y.reshape(b, t, c, *y.shape[-2:]).permute(0, 2, 1, 3, 4)

        x = conv("encoder.conv1", x)
        x = res("encoder.downsamples.0", x)
        x = runter("encoder.downsamples.1", x)
        x = res("encoder.downsamples.2", x)
        x = runter("encoder.downsamples.3", x)
        x = runter("encoder.downsamples.4", x)
        x = res("encoder.middle.0", x)
        b, c, t, h, w = x.shape
        y = x[:, :, 0]
        q, k, v = F.conv2d(F.normalize(y, dim=1) * c ** 0.5 * sd["encoder.middle.1.norm.gamma"],
                           sd["encoder.middle.1.to_qkv.weight"], sd["encoder.middle.1.to_qkv.bias"]).chunk(3, 1)
        q, k, v = (z.reshape(b, c, h * w).transpose(1, 2) for z in (q, k, v))
        a = torch.softmax(q @ k.transpose(1, 2) / c ** 0.5, -1) @ v
        a = F.conv2d(a.transpose(1, 2).reshape(b, c, h, w), sd["encoder.middle.1.proj.weight"],
                     sd["encoder.middle.1.proj.bias"])
        x = (y + a)[:, :, None]
        x = res("encoder.middle.2", x)
        x = conv("encoder.head.2", F.silu(norm("encoder.head.0", x)))
        return conv("conv1", x)[:, :16, 0]

    def test_gleich_der_kausalen_3d_rechnung(self):
        from safetensors.torch import save_file
        sd = self._gewichte()
        with tempfile.TemporaryDirectory() as d:
            pfad = Path(d) / "vae.safetensors"
            save_file(sd, str(pfad))
            enc = krea._VAEEncoder(pfad, "cpu", torch.float32)
            x = torch.rand(1, 3, 48, 64) * 2 - 1
            with torch.no_grad():
                mein, ref = enc(x), self._referenz(sd, x)
        self.assertEqual(tuple(mein.shape), (1, 16, 6, 8))
        self.assertTrue(torch.allclose(mein, ref, atol=1e-4, rtol=1e-4), float((mein - ref).abs().max()))


class Einsetzen(unittest.TestCase):
    def test_maske_mitte_voll_ecken_leer(self):
        m = gesichter.maske(41, 41)
        self.assertEqual(m[20, 20], 1.0)
        self.assertEqual(m[0, 0], 0.0)

    def test_nur_der_kasten_aendert_sich(self):
        arr = np.zeros((100, 100, 3), np.uint8)
        neu = np.full((40, 40, 3), 200, np.uint8)
        out = gesichter.einsetzen(arr, (30, 20, 70, 60), neu)
        self.assertEqual(out[40, 50].tolist(), [200, 200, 200])
        self.assertEqual(int(out[:20].max()), 0)
        self.assertEqual(int(out[:, :30].max()), 0)



class Finden(unittest.TestCase):
    """Gesichter findet das Seh-Modell (Vision/) – hier nachgebildet, ohne Modell."""

    def _finden(self, antworten, arr=None):
        fragen = []

        def fragen_roh(bild, text, max_tokens=0):
            fragen.append((bild.size, text))
            return antworten.pop(0) if antworten else "no"
        with mock.patch("bildbeschreiber.finden", return_value=("m.gguf", "mmproj.gguf")),                 mock.patch("bildbeschreiber.fragen_roh", side_effect=fragen_roh):
            boxen = gesichter.finden(arr if arr is not None else np.zeros((1000, 800, 3), np.uint8))
        return boxen, fragen

    def test_kasten_relativ_mit_rand_und_gegenprobe(self):
        boxen, fragen = self._finden(['[{"box": [400, 200, 600, 450]}]', "Yes."])
        self.assertEqual([(272, 102, 528, 525)], boxen)          # 800×1000, plus 30 % Rand (oben ×1,3)
        self.assertEqual(gesichter.FINDEN, fragen[0][1])
        self.assertEqual((gesichter.PRUEFEN, (256, 423)), (fragen[1][1], fragen[1][0]))

    def test_gegenprobe_nein_laesst_das_original(self):
        boxen, _ = self._finden(['[{"box": [400, 200, 600, 450]}]', "no"])
        self.assertEqual([], boxen)

    def test_unplausible_und_doppelte_kaesten(self):
        boxen, fragen = self._finden(['[{"box": [10, 10, 15, 14]}, {"box": [0, 0, 1000, 300]}, '
                                      '{"box": [400, 200, 600, 450]}, {"box": [405, 210, 595, 440]}]', "ja"])
        self.assertEqual(1, len(boxen))                          # winzig, falsche Form, doppelt: raus
        self.assertEqual(2, len(fragen))

    def test_kaesten_lesen(self):
        self.assertEqual([(40, 20, 60, 45)], gesichter.kaesten_lesen("[400, 200, 600, 450]", 100, 100))
        self.assertEqual([(40, 20, 60, 45)], gesichter.kaesten_lesen("[0.6, 0.45, 0.4, 0.2]", 100, 100))
        self.assertEqual([], gesichter.kaesten_lesen("[1200, 50, 1400, 300] und []", 100, 100))
        self.assertEqual([], gesichter.kaesten_lesen("Kein Gesicht zu sehen.", 100, 100))

    def test_ohne_seh_modell_nichts(self):
        with mock.patch("bildbeschreiber.finden", return_value=None):
            self.assertFalse(gesichter.verfuegbar())
            self.assertIn("Vision", gesichter.grund())
            self.assertEqual([], gesichter.finden(np.zeros((100, 100, 3), np.uint8)))

    def test_kein_opencv_mehr(self):
        quelle = (ROOT / "engines" / "gesichter.py").read_text(encoding="utf-8")
        self.assertNotIn("cv2", quelle)
        self.assertNotIn("opencv", (ROOT / "requirements.txt").read_text(encoding="utf-8").lower())


class Nachbessern(unittest.TestCase):
    def _flicken(self, male):
        return (mock.patch.object(krea, "missing_reason", return_value=None),
                mock.patch.object(krea, "discover", return_value={"k2": Path("k2.safetensors")}),
                mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}),
                mock.patch.object(krea, "_geraet", return_value=("cpu", None)),
                mock.patch.object(krea, "_uhr_neu"),
                mock.patch.object(krea, "_frei"),
                mock.patch.object(gesichter, "grund", return_value=None),
                mock.patch.object(gesichter, "finden", return_value=[(100, 80, 180, 180)]),
                mock.patch.object(krea, "_male", side_effect=male))

    def test_malt_ausschnitt_vergroessert_nach_und_behaelt_das_original(self):
        aufrufe = []

        def male(prompt, ref, pfad, t, steps, sampler, shift, W, H, seed, device, dtype, on_status,
                 bild=None, staerke=1.0, lora=None, lora_staerke=1.0):
            aufrufe.append(dict(prompt=prompt, W=W, H=H, seed=seed, sampler=sampler, staerke=staerke,
                                bild=bild.shape, steps=steps, lora=lora))
            return np.full((H, W, 3), 220, np.uint8)

        with tempfile.TemporaryDirectory() as d:
            quelle = Path(d) / "bild.png"
            _krea_png(quelle)
            fl = self._flicken(male)
            with fl[0], fl[1], fl[2], fl[3], fl[4], fl[5], fl[6], fl[7], fl[8], \
                    mock.patch("config.load", return_value={}):
                ziel = krea.gesicht_nachbessern(str(quelle))
            self.assertEqual((Path(ziel).parent.name, Path(ziel).name), ("Gesichtszuege", "bild.png"))
            self.assertTrue(quelle.exists())                    # das Original bleibt daneben
            with Image.open(ziel) as im:
                neu = np.asarray(im.convert("RGB"))
                self.assertIn("gesicht x1", im.text["nachgebessert"])
                self.assertIn("backend=krea2", im.text["params"])
            with Image.open(quelle) as im:
                self.assertEqual(int(np.asarray(im).max()), 100)          # Original unverändert
        a = aufrufe[0]
        self.assertEqual(max(a["W"], a["H"]), 1024)
        self.assertEqual(a["bild"], (a["H"], a["W"], 3))
        self.assertEqual(a["seed"], 42)
        self.assertEqual(a["sampler"], "euler")
        self.assertEqual(a["staerke"], krea.DEFAULTS["gesicht_staerke"])
        self.assertEqual(krea.GESICHT_SATZ, a["prompt"])         # nur das Gesicht, nicht die Szene
        self.assertNotIn("garden", a["prompt"])
        self.assertEqual(neu[130, 140].tolist(), [220, 220, 220])
        self.assertEqual(neu[5, 5].tolist(), [100, 100, 100])
        self.assertIsNone(a["lora"])                             # ohne LoRA gemalt: ohne nachgemalt

    def test_malt_mit_der_lora_des_bildes(self):
        aufrufe = []

        def male(*args, lora=None, lora_staerke=1.0, **kw):
            aufrufe.append((lora, lora_staerke))
            return np.full((args[8], args[7], 3), 220, np.uint8)

        with tempfile.TemporaryDirectory() as d:
            lora = Path(d) / "figur.safetensors"
            lora.write_bytes(b"x")
            quelle = Path(d) / "bild.png"
            _krea_png(quelle, "steps=14, cfg=1, size=256x256, seed=41, sampler=euler, shift=1.15, backend=krea2, "
                              "lora=figur:0.8", lora_datei=str(lora))
            fl = self._flicken(male)
            with fl[0], fl[1], fl[2], fl[3], fl[4], fl[5], fl[6], fl[7], fl[8], \
                    mock.patch("config.load", return_value={}):
                ziel = krea.gesicht_nachbessern(str(quelle))
            with Image.open(ziel) as im:
                self.assertIn("lora=figur:0.8", im.text["nachgebessert"])
            self.assertEqual([(lora, 0.8)], aufrufe)
            lora.unlink()                                       # LoRA weg: ohne sie, statt abzubrechen
            aufrufe.clear()
            with fl[0], fl[1], fl[2], fl[3], fl[4], fl[5], fl[6], fl[7], fl[8], \
                    mock.patch("config.load", return_value={}):
                krea.gesicht_nachbessern(str(quelle))
            self.assertEqual([(None, 1.0)], aufrufe)

    def test_grosses_gesicht_bleibt_wie_es_ist(self):
        male = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            quelle = Path(d) / "bild.png"
            _krea_png(quelle)
            fl = self._flicken(male)
            with fl[0], fl[1], fl[2], fl[3], fl[4], fl[5], fl[6], fl[7], fl[8], \
                    mock.patch("config.load", return_value={"bild_krea_gesicht_kante": 100}):
                self.assertIsNone(krea.gesicht_nachbessern(str(quelle)))
        male.assert_not_called()

    def test_ohne_seh_modell_einmal_sagen_dann_still(self):
        with mock.patch.object(krea, "missing_reason", return_value=None),                 mock.patch.object(gesichter, "grund", return_value="braucht ein Seh-Modell in Vision/"),                 mock.patch.object(krea, "_GESICHT_HINWEIS", False):
            with self.assertRaisesRegex(RuntimeError, "Seh-Modell"):
                krea.gesicht_nachbessern("egal.png")
            self.assertIsNone(krea.gesicht_nachbessern("egal.png"))

    def test_schalter_als_text(self):
        for wert, an in (("false", False), ("aus", False), ("0", False), ("true", True), (False, False), (True, True)):
            with self.subTest(wert=wert), mock.patch("config.load", return_value={"bild_krea_gesicht": wert}):
                self.assertEqual(an, krea.gesicht_an())
        with mock.patch("config.load", return_value={"bild_krea_gesicht_staerke": "0.3"}):
            self.assertEqual(0.3, krea._cfg("bild_krea_gesicht_staerke", 0.35))

    def test_staerke_sanfter(self):
        self.assertEqual(0.35, krea.DEFAULTS["gesicht_staerke"])


class Einbindung(unittest.TestCase):
    def test_nur_krea_bilder_und_nur_eingeschaltet(self):
        with tempfile.TemporaryDirectory() as d:
            fremd, eigen = Path(d) / "a.png", Path(d) / "b.png"
            _krea_png(fremd, params="steps=14, backend=webui")
            _krea_png(eigen)
            with mock.patch.object(krea, "gesicht_nachbessern", return_value="neu.png") as nb:
                with mock.patch("config.load", return_value={}):
                    self.assertIsNone(imagegen.gesicht_nachbessern(str(fremd)))
                    self.assertEqual(imagegen.gesicht_nachbessern(str(eigen)), "neu.png")
                with mock.patch("config.load", return_value={"bild_krea_gesicht": False}):
                    self.assertIsNone(imagegen.gesicht_nachbessern(str(eigen)))
            self.assertEqual(nb.call_count, 1)

    def test_bild_option_ohne_gesicht(self):
        o = imagegen.parse_opts("eine Katze --ohne-gesicht --seed 3")
        self.assertEqual((o["prompt"], o["gesicht"], o["seed"]), ("eine Katze", False, 3))
        self.assertNotIn("gesicht", imagegen.parse_opts("eine Katze"))

    def test_bild_malen_oeffnet_die_nachgebesserte_fassung(self):
        import actions
        with mock.patch.object(imagegen, "missing_reason_aktiv", return_value=None), \
                mock.patch.object(imagegen, "modell_aufloesen", return_value=(None, "")), \
                mock.patch.object(imagegen, "paint", return_value="a.png"), \
                mock.patch.object(imagegen, "gesicht_nachbessern", return_value="a_gesicht.png"), \
                mock.patch.object(actions.os, "startfile", create=True) as start:
            text = actions._bild_malen({"prompt": "A woman."})
        start.assert_called_once_with("a_gesicht.png")
        self.assertIn("Gesicht nachgebessert: a_gesicht.png", str(text))


if __name__ == "__main__":
    unittest.main()
