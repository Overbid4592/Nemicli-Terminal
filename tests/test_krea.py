"""Offline-Tests für die Krea-2-Pipeline (engines/krea.py).

Ohne Modell und ohne GPU: geprüft werden Sigma-Plan, Skalen-Kachelung,
NVFP4-Packen, Modellerkennung und die Trennung von der SD-Pipeline.

python -m unittest discover -s tests -p test_krea.py
"""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import krea  # noqa: E402

try:
    import torch
except Exception:                                   # pragma: no cover
    torch = None


def _safetensors(pfad: Path, keys: list[str]) -> None:
    """Nur ein Kopf, keine Daten – reicht für die Erkennung."""
    kopf = json.dumps({k: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]} for k in keys})
    kopf = kopf.encode() + b" " * (-len(kopf) % 8)
    pfad.write_bytes(struct.pack("<Q", len(kopf)) + kopf + b"\x00\x00")


class Erkennung(unittest.TestCase):
    def test_krea_und_andere_modelle(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _safetensors(d / "k.safetensors", ["model.diffusion_model.txtfusion.projector.weight"])
            _safetensors(d / "sd.safetensors", ["model.diffusion_model.input_blocks.0.weight"])
            self.assertTrue(krea.ist_krea2(d / "k.safetensors"))
            self.assertFalse(krea.ist_krea2(d / "sd.safetensors"))
            with mock.patch.object(krea, "KREA_DIRS", [d / "fehlt", d]):
                self.assertEqual(krea.krea_dir(), d)
                self.assertEqual(list(krea.discover()), ["k"])
                self.assertIn("Text-Encoder fehlt", krea.missing_reason() or "")

    def test_ohne_ordner(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(krea, "KREA_DIRS", [Path(d) / "nichts"]):
                self.assertEqual(krea.discover(), {})


@unittest.skipIf(torch is None, "torch fehlt")
class Rechenteile(unittest.TestCase):
    def test_sigmas(self):
        s = krea._sigmas(14, 1.15)
        self.assertEqual(len(s), 15)
        self.assertAlmostEqual(float(s[0]), 1.0, places=5)
        self.assertEqual(float(s[-1]), 0.0)
        self.assertTrue(all(float(a) > float(b) for a, b in zip(s[:-1], s[1:])))

    def test_kachelung_hin_und_zurueck(self):
        m = torch.arange(256 * 12, dtype=torch.float32).view(256, 12)
        self.assertTrue(torch.equal(krea._from_blocked(krea._to_blocked(m), 256, 12), m))
        klein = torch.arange(100 * 3, dtype=torch.float32).view(100, 3)
        self.assertTrue(torch.equal(krea._from_blocked(krea._to_blocked(klein), 100, 3), klein))

    def test_fp4_packen_trifft_darstellbare_werte(self):
        werte = torch.tensor(krea._E2M1[:8] + [-v for v in krea._E2M1[1:8]] + [0.0])
        x = werte.repeat(4, 2)[:, :32].to(torch.bfloat16)    # (4, 32): 2 Blöcke je Zeile
        q, bs, gs = krea._fp4_quant(x)
        self.assertEqual(q.dtype, torch.uint8)
        self.assertEqual(tuple(q.shape), (4, 16))
        lut = krea._lut256("cpu", torch.float32)
        roh = torch.nn.functional.embedding(q.int(), lut).view(4, 32)
        skala = krea._from_blocked(bs, 4, 2).float() * gs
        zurueck = roh.view(4, 2, 16) * skala.unsqueeze(-1)
        self.assertTrue(torch.allclose(zurueck.view(4, 32), x.float(), atol=1e-2))


@unittest.skipUnless((krea.teile()["tokenizer"] / "vocab.json").exists(), "kein lokaler Tokenizer")
class Tokenizer(unittest.TestCase):
    def test_ohne_transformers(self):
        with mock.patch.dict(sys.modules, {"transformers": None}):
            tok = krea._tokenizer()
        ids = tok("<|im_start|>user\na red fox<|im_end|>")
        self.assertEqual(ids[:3], [krea._IM_START, krea._TOK_USER, krea._TOK_NL])
        self.assertEqual(ids[-1], 151645)                   # <|im_end|> als ein Token


@unittest.skipIf(torch is None, "torch fehlt")
class Speicher(unittest.TestCase):
    """VRAM/RAM-Verwaltung ohne GPU: "meta" steht für die Karte."""

    def setUp(self):
        krea.entladen()
        self.addCleanup(krea.entladen)

    def test_kopie_erreicht_verschachtelte_gewichte(self):
        sd = {"a.weight": torch.ones(4, 4), "a.bias": torch.ones(4)}
        lin = krea._Linear(sd, "a", torch.float32)
        paket = {"schichten": [lin, (torch.zeros(2),)]}
        kopie = krea._kopie(paket, "meta")
        self.assertEqual(kopie["schichten"][0].w.device.type, "meta")
        self.assertEqual(kopie["schichten"][0].bias.device.type, "meta")
        self.assertEqual(kopie["schichten"][1][0].device.type, "meta")
        self.assertEqual(lin.w.device.type, "cpu")              # Original unberührt

    def test_ram_fassung_bleibt_arbeitskopie_ist_neu(self):
        bauen = mock.Mock(side_effect=lambda: {"w": torch.ones(2)})
        erste = krea._auf_gpu("dit", Path("m.safetensors"), bauen, "meta")
        zweite = krea._auf_gpu("dit", Path("m.safetensors"), bauen, "meta")
        self.assertEqual(bauen.call_count, 1)                   # nur einmal von der Platte
        self.assertIsNot(erste, zweite)
        self.assertEqual(zweite["w"].device.type, "meta")       # Arbeitskopie auf der "Karte"
        self.assertEqual(krea._RAM["dit"][1]["w"].device.type, "cpu")
        krea._auf_gpu("dit", Path("anders.safetensors"), bauen, "meta")
        self.assertEqual(bauen.call_count, 2)
        self.assertEqual(krea.im_ram(), ["dit"])

    def _male_attrappe(self, warte: float):
        import numpy as np
        return (mock.patch.object(krea, "missing_reason", return_value=None),
                mock.patch.object(krea, "discover", return_value={"k2": Path("k2.safetensors")}),
                mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}),
                mock.patch.object(krea, "_entladen_nach", return_value=warte),
                mock.patch.object(krea, "_male", side_effect=lambda *a, **k: (
                    krea._RAM.__setitem__("dit", (Path("m"), {})), np.zeros((16, 16, 3), "uint8"))[1]))

    def test_ohne_neues_bild_wird_der_ram_geraeumt(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(krea, "OUT_DIR", Path(d)):
            patches = self._male_attrappe(0.05)
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])
            krea.generate("a fox", seed=1)
            self.assertEqual(krea.im_ram(), ["dit"])
            krea._TIMER.join(2)
            self.assertEqual(krea.im_ram(), [])

    def test_neues_bild_setzt_die_uhr_zurueck(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(krea, "OUT_DIR", Path(d)):
            patches = self._male_attrappe(30)
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])
            krea.generate("a fox", seed=1)
            erste = krea._TIMER
            krea.generate("a cat", seed=2)
            self.assertIsNot(krea._TIMER, erste)
            self.assertFalse(erste.is_alive())
            self.assertEqual(krea.im_ram(), ["dit"])

    def test_fehler_gibt_vram_frei_und_behaelt_nur_den_text(self):
        with mock.patch.object(krea, "missing_reason", return_value=None), \
                mock.patch.object(krea, "discover", return_value={"k2": Path("k2.safetensors")}), \
                mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}), \
                mock.patch.object(krea, "_frei") as frei, \
                mock.patch.object(krea, "_male", side_effect=ValueError("kaputt")):
            with self.assertRaises(RuntimeError) as e:
                krea.generate("a fox", seed=1)
        self.assertIn("kaputt", str(e.exception))
        self.assertIsNone(e.exception.__context__)
        frei.assert_called()


class GetrenntVonSD(unittest.TestCase):
    """Krea ist ein eigener Motor – die SD-Pipeline kennt Krea nicht und wird nie ersatzweise genommen."""

    def test_sd_erkennung_haelt_krea_nicht_fuer_sd15(self):
        import imagegen
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _safetensors(d / "k2.safetensors", ["model.diffusion_model.txtfusion.projector.weight",
                                                "model.diffusion_model.blocks.0.attn.wq.weight"])
            _safetensors(d / "sd.safetensors", ["model.diffusion_model.input_blocks.0.weight"])
            self.assertEqual("fremd", imagegen.detect_kind(d / "k2.safetensors"))
            self.assertEqual("sd15", imagegen.detect_kind(d / "sd.safetensors"))
            with mock.patch.object(imagegen, "CKPT_DIRS", [d]):
                self.assertEqual(["sd"], list(imagegen.discover()))    # Krea-Datei taucht nicht auf

    def test_gewaehltes_krea_faellt_nie_auf_sd_zurueck(self):
        import imagegen
        with mock.patch("config.load", return_value={"bild_backend": "krea"}),                 mock.patch.object(krea, "missing_reason", return_value="Es fehlt: PyTorch."):
            self.assertEqual("krea", imagegen.backend())
            self.assertEqual("Es fehlt: PyTorch.", imagegen.missing_reason_aktiv())

    def test_paint_geht_an_krea_ohne_negativ_und_ohne_nachbessern(self):
        import imagegen
        with mock.patch.object(imagegen, "backend", return_value="krea"),                 mock.patch.object(imagegen, "generate") as sd,                 mock.patch.object(krea, "generate", return_value="bild.png") as gen:
            pfad, nachbessern = imagegen.paint("a fox", neg="egal", cfg=7, sampler="dpmpp_2m")
        self.assertEqual("bild.png", pfad)
        self.assertFalse(nachbessern)
        sd.assert_not_called()
        self.assertNotIn("neg", gen.call_args.kwargs)
        self.assertIsNone(gen.call_args.kwargs["sampler"])       # SD-Sampler gilt für Krea nicht

    def test_modellwahl_merkt_sich_krea_als_motor(self):
        with mock.patch("config.update") as upd:
            krea.set_chosen_model("k2")
        upd.assert_called_once_with(bild_krea_model="k2", bild_backend="krea")

    def test_kein_nachbessern_auf_bildern_anderer_motoren(self):
        import imagegen
        from PIL import Image, PngImagePlugin
        with tempfile.TemporaryDirectory() as d:
            bild = Path(d) / "k.png"
            meta = PngImagePlugin.PngInfo()
            meta.add_text("model", "k2")
            meta.add_text("params", "steps=14, cfg=1, backend=krea2")
            Image.new("RGB", (64, 64)).save(bild, pnginfo=meta)
            with mock.patch.object(imagegen, "missing_reason", return_value=None),                     mock.patch.object(imagegen, "auto_regions") as regionen:
                self.assertIsNone(imagegen.auto_nachbessern(bild))
            regionen.assert_not_called()
            with mock.patch.object(imagegen, "missing_reason", return_value=None),                     mock.patch.object(imagegen, "resolve") as resolve:
                with self.assertRaises(RuntimeError) as e:
                    imagegen.repaint_region(bild, (0, 0, 8, 8))
            resolve.assert_not_called()
        self.assertIn("nicht aus der SD-Pipeline", str(e.exception))


if __name__ == "__main__":
    unittest.main()
