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
            erste.join(1)
            self.assertFalse(erste.is_alive())
            self.assertEqual(krea.im_ram(), ["dit"])
            krea._TIMER.cancel()

    def test_uhr_zaehlt_nur_in_ruhe(self):
        import notstopp
        import time
        with tempfile.TemporaryDirectory() as d, mock.patch.object(krea, "OUT_DIR", Path(d)), \
                mock.patch.object(krea, "RUHE_TAKT_S", 0.02):
            patches = self._male_attrappe(0.1)
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])
            notstopp.runde_aktiv(True)
            try:
                krea.generate("a fox", seed=1)
                time.sleep(0.4)                         # Runde läuft: Uhr steht
                self.assertEqual(krea.im_ram(), ["dit"])
            finally:
                notstopp.runde_aktiv(False)
            self.assertFalse(notstopp.runde_laeuft())
            krea._TIMER.join(2)                         # danach Ruhe: entladen
            self.assertEqual(krea.im_ram(), [])

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


class Weiche(unittest.TestCase):
    """imagegen ist nur noch die Weiche: Krea 2 ist Standard, externe Stellen nur, wenn sie laufen."""

    def test_gewaehltes_krea_meldet_was_fehlt(self):
        import imagegen
        with mock.patch("config.load", return_value={"bild_backend": "krea"}), \
                mock.patch.object(krea, "missing_reason", return_value="Es fehlt: PyTorch."):
            self.assertEqual("krea", imagegen.backend())
            self.assertEqual("Es fehlt: PyTorch.", imagegen.missing_reason_aktiv())

    def test_ohne_wahl_und_alter_wert_malen_mit_krea(self):
        import imagegen
        for cfg in ({}, {"bild_backend": "builtin"}):
            with mock.patch("config.load", return_value=cfg):
                self.assertEqual("krea", imagegen.backend())

    def test_webui_aus_faellt_auf_krea(self):
        import imagegen
        import sdwebui
        with mock.patch("config.load", return_value={"bild_backend": "webui"}), \
                mock.patch.object(sdwebui, "available", return_value=False), \
                mock.patch.object(krea, "missing_reason", return_value=None):
            self.assertEqual("krea", imagegen.backend())
            self.assertIsNone(imagegen.missing_reason_aktiv())

    def test_paint_geht_an_krea_mit_negativ_ohne_cfg(self):
        import imagegen
        with mock.patch.object(imagegen, "backend", return_value="krea"), \
                mock.patch.object(krea, "generate", return_value="bild.png") as gen:
            pfad = imagegen.paint("a fox", neg="egal", cfg=7, sampler="dpmpp_2m")
        self.assertEqual("bild.png", pfad)
        self.assertEqual("egal", gen.call_args.kwargs["neg"])          # Krea führt ihn mit der Schaltzentrale zusammen
        self.assertNotIn("cfg", gen.call_args.kwargs)
        self.assertIsNone(gen.call_args.kwargs["sampler"])       # fremde Sampler gelten für Krea nicht

    def test_modellwahl_merkt_sich_krea_als_motor(self):
        with mock.patch("config.update") as upd:
            krea.set_chosen_model("k2")
        upd.assert_called_once_with(bild_krea_model="k2", bild_backend="krea")

    def test_modellname_grosszuegig(self):
        import imagegen
        with mock.patch.object(imagegen, "backend", return_value="krea"), \
                mock.patch.object(krea, "discover", return_value={"krea2-turbo": Path("a"), "anderes": Path("b")}):
            self.assertEqual(("krea2-turbo", ""), imagegen.modell_aufloesen(r"C:\x\Krea2-Turbo.safetensors"))
            name, hinweis = imagegen.modell_aufloesen("gibtsnicht")
        self.assertIsNone(name)
        self.assertIn("gibtsnicht", hinweis)


class Schritte(unittest.TestCase):
    def test_ohne_einstellung_standard(self):
        with mock.patch("config.load", return_value={}):
            self.assertEqual(krea.schritte(), krea.DEFAULTS["steps"])

    def test_gespeicherter_wert_begrenzt_auf_8_bis_16(self):
        for gespeichert, erwartet in ((9, 9), (16, 16), (3, 8), (40, 16), ("12", 12)):
            with mock.patch("config.load", return_value={"bild_krea_schritte": gespeichert}):
                self.assertEqual(krea.schritte(), erwartet)

    def test_speichern_begrenzt_und_merkt_dauerhaft(self):
        with mock.patch("config.update") as upd:
            self.assertEqual(krea.set_schritte(10), 10)
            upd.assert_called_with(bild_krea_schritte=10)
            self.assertEqual(krea.set_schritte(20), 16)
            upd.assert_called_with(bild_krea_schritte=16)

    def test_malen_nimmt_den_gespeicherten_wert(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as d, mock.patch.object(krea, "OUT_DIR", Path(d)), \
                mock.patch.object(krea, "missing_reason", return_value=None), \
                mock.patch.object(krea, "discover", return_value={"k2": Path("k2.safetensors")}), \
                mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}), \
                mock.patch.object(krea, "_uhr_neu"), \
                mock.patch.object(krea, "_male", return_value=np.zeros((8, 8, 3), np.uint8)) as male, \
                mock.patch("profilordner.bild_ziel", return_value=Path(d) / "b.png"), \
                mock.patch("config.load", return_value={"bild_krea_schritte": 11}):
            krea.generate("x")
            self.assertEqual(male.call_args.args[4], 11)
            krea.generate("x", steps=9)                     # --steps gilt nur für dieses Bild
            self.assertEqual(male.call_args.args[4], 9)

    def _malen_mit(self, config: dict, model="k2"):
        import numpy as np
        with tempfile.TemporaryDirectory() as d, mock.patch.object(krea, "OUT_DIR", Path(d)), \
                mock.patch.object(krea, "missing_reason", return_value=None), \
                mock.patch.object(krea, "discover", return_value={model: Path(f"{model}.safetensors")}), \
                mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}), \
                mock.patch.object(krea, "_uhr_neu"), \
                mock.patch.object(krea, "_male", return_value=np.zeros((8, 8, 3), np.uint8)) as male, \
                mock.patch("profilordner.bild_ziel", return_value=Path(d) / "b.png"), \
                mock.patch("config.load", return_value=config):
            pfad = krea.generate("x", seed=1)
            from PIL import Image
            with Image.open(pfad) as im:
                meta = dict(im.text)
        return male.call_args, meta

    def test_schaltzentrale_cfg_gegenprompt_sampler(self):
        import bildsperre
        aufruf, meta = self._malen_mit({})                    # Vorgabe: wie bisher, ein Durchlauf
        self.assertEqual((0.0, ""), (aufruf.kwargs["cfg"], aufruf.kwargs["gegen"]))
        self.assertEqual(("euler_ancestral", 1.15), (aufruf.args[5], aufruf.args[6]))
        self.assertNotIn("negative_prompt", meta)
        aufruf, meta = self._malen_mit({"bild_krea_cfg": 1.5, "bild_krea_negativ": "worst quality,\n  text",
                                        "bild_krea_sampler": "euler", "bild_krea_shift": 2.0})
        self.assertAlmostEqual(0.5, aufruf.kwargs["cfg"])
        self.assertTrue(aufruf.kwargs["gegen"].startswith("worst quality, text"))
        self.assertIn(bildsperre.FESTER_GEGENPROMPT, aufruf.kwargs["gegen"])   # Bildsperre rechnet immer mit
        self.assertEqual(("euler", 2.0), (aufruf.args[5], aufruf.args[6]))
        self.assertIn("cfg=1.5", meta["params"])
        self.assertEqual(aufruf.kwargs["gegen"], meta["negative_prompt"])
        aufruf, _ = self._malen_mit({"bild_krea_negativ": "text"})       # CFG 1: der Gegen-Prompt ruht
        self.assertEqual((0.0, ""), (aufruf.kwargs["cfg"], aufruf.kwargs["gegen"]))
        aufruf, _ = self._malen_mit({"bild_krea_cfg": 1.4})              # ohne eigenen: nur die Bildsperre
        self.assertEqual(bildsperre.FESTER_GEGENPROMPT, aufruf.kwargs["gegen"])

    def test_schaltzentrale_grenzen(self):
        with mock.patch("config.load", return_value={"bild_krea_cfg": 99, "bild_krea_shift": -3,
                                                     "bild_krea_sampler": "dpm", "bild_krea_negativ": "x" * 5000}):
            ein = krea.einstellungen()
        self.assertEqual((krea.CFG_BEREICH[1], krea.SHIFT_BEREICH[0], "euler_ancestral", krea.NEGATIV_MAX),
                         (ein["cfg"], ein["shift"], ein["sampler"], len(ein["negativ"])))
        with mock.patch("config.load", return_value={"bild_krea_cfg": "abc"}):
            self.assertEqual(1.0, krea.einstellungen()["cfg"])


def _q8_0(w) -> tuple:
    """Matrix -> (GGML-Typ Q8_0, Form, Rohbytes): je 32 Werte eine fp16-Skala und 32 int8."""
    import numpy as np
    x = w.detach().float().reshape(-1, 32)
    d = x.abs().amax(1, keepdim=True) / 127.0
    q = torch.where(d > 0, (x / d.clamp(min=1e-12)).round(), torch.zeros_like(x)).clamp(-127, 127).to(torch.int8)
    return (8, tuple(w.shape), b"".join(np.float16(float(s)).tobytes() + r.numpy().tobytes() for s, r in zip(d[:, 0], q)))


@unittest.skipIf(torch is None, "torch fehlt")
class AndereModelle(unittest.TestCase):
    """Krea-2-Dateien anderer Herkunft: GGUF, ohne Vorsatz, geteilt, MXFP8, INT8 ConvRot, Raw."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        krea._ERKANNT.clear()
        torch.manual_seed(3)
        self.w = torch.randn(64, 128)
        self.b = torch.randn(64)
        self.x = torch.randn(2, 5, 128)
        self.ref = self.x @ self.w.t() + self.b

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, name, sd, meta=None):
        from safetensors.torch import save_file
        save_file(sd, str(self.d / name), metadata=meta)
        return self.d / name

    def test_erkennung_gguf_ohne_vorsatz_und_geteilt(self):
        if str(ROOT / "tests") not in sys.path:
            sys.path.insert(0, str(ROOT / "tests"))
        import gguf_testbau as B
        p = torch.zeros(4, 4)
        B.schreibe_gguf(str(self.d / "q.gguf"), {"general.architecture": "krea2"}, {"txtfusion.projector.weight": p})
        self._save("nackt.safetensors", {"txtfusion.projector.weight": p})
        self._save("teil-00001-of-00002.safetensors", {"model.diffusion_model.txtfusion.projector.weight": p})
        self._save("teil-00002-of-00002.safetensors", {"model.diffusion_model.blocks.0.x": p})
        self._save("fremd.safetensors", {"model.diffusion_model.input_blocks.0.weight": p})
        with mock.patch.object(krea, "KREA_DIRS", [self.d]):
            self.assertEqual(sorted(krea.discover()), ["nackt", "q", "teil"])

    def test_bewaehrter_lader_nur_fuer_bekannte_dateien(self):
        alt = self._save("alt.safetensors", {"model.diffusion_model.l.weight": self.w.to(torch.float8_e4m3fn),
                                             "model.diffusion_model.l.weight_scale": torch.ones(())})
        nackt = self._save("nackt.safetensors", {"l.weight": self.w})
        cq = torch.tensor(list(b'{"format": "mxfp8"}'), dtype=torch.uint8)
        mx = self._save("mx.safetensors", {"model.diffusion_model.l.weight": self.w.to(torch.float8_e4m3fn),
                                           "model.diffusion_model.l.comfy_quant": cq})
        self.assertTrue(krea._bisheriger_weg(alt))
        self.assertFalse(krea._bisheriger_weg(nackt))
        self.assertFalse(krea._bisheriger_weg(mx))

    def test_int8_convrot_und_gguf_rechnen_wie_dicht(self):
        from bildmotoren.gewichte import hadamard
        h = hadamard(64, "cpu", torch.float32)
        gedreht = (self.w.view(64, 2, 64) @ h.t()).view(64, 128)
        s = gedreht.abs().amax(1, keepdim=True) / 127.0
        cq = torch.tensor(list(b'{"format": "int8_tensorwise", "convrot": true, "convrot_groupsize": 64}'),
                          dtype=torch.uint8)
        proj = torch.randn(8, 4)
        p = self._save("cr.safetensors", {"l.weight": (gedreht / s).round().to(torch.int8), "l.weight_scale": s,
                                          "l.comfy_quant": cq, "l.bias": self.b,
                                          "txtfusion.projector.weight": proj, "n.scale": torch.ones(64)})
        if str(ROOT / "tests") not in sys.path:
            sys.path.insert(0, str(ROOT / "tests"))
        import gguf_testbau as B
        g = self.d / "g.gguf"
        B.schreibe_gguf(str(g), {"general.architecture": "krea2"},
                        {"l.weight": _q8_0(self.w), "l.bias": self.b, "txtfusion.projector.weight": proj})
        for pfad in (p, g):
            sd = krea._lade_dit(pfad, "cpu")
            self.assertTrue(torch.allclose(sd["txtfusion.projector.weight"].float(), proj, atol=2e-2))
            lin = krea._Linear(sd, "l", torch.float32)
            self.assertEqual((lin.art, lin.out_f, lin.in_f), ("lin", 64, 128))
            self.assertLess(float((lin(self.x) - self.ref).abs().max()), 0.05 * float(self.ref.abs().max()), pfad.name)
            self.assertEqual(tuple(lin.weight().shape), (64, 128))          # LoRA-Rückweg
            kopie = krea._kopie(lin, "cpu")
            self.assertIsNot(kopie.lin, lin.lin)

    def test_nvfp4_ohne_format_angabe(self):
        """„Bereinigte“ NVFP4-Dateien ohne comfy_quant: gepackte Bytes + zweite Skala sind NVFP4
        (vorher als dichte Matrix gelesen: mat1 (…x128) und mat2 (64x128) passen nicht)."""
        from bildmotoren.gewichte import Quelle
        q, bs, gs = krea._fp4_quant(self.w)
        sd = {"l.weight": q, "l.weight_scale": bs, "l.weight_scale_2": gs.reshape(()), "l.bias": self.b}
        p = self._save("clean.safetensors", {"model.diffusion_model." + k: v for k, v in sd.items()})
        self.assertTrue(krea._bisheriger_weg(p))
        lin = krea._Linear(krea._lade_dit(p, "cpu"), "l", torch.float32)
        self.assertEqual((lin.out_f, lin.in_f), (64, 128))
        grenze = 0.25 * float(self.ref.abs().max())
        self.assertLess(float((lin(self.x) - self.ref).abs().max()), grenze)
        with Quelle(self._save("nackt.safetensors", sd)) as quelle:
            g = quelle.lin("l", torch.float32)
        self.assertEqual(g.art, "nvfp4")
        self.assertLess(float((g(self.x) - self.ref).abs().max()), grenze)

    def test_raw_erkennen_und_verschiebung(self):
        for name, ja in (("krea2_raw_bf16", True), ("Krea-2-Raw", True), ("krea2_turbo_fp8", False),
                         ("strawberryMix", False), (None, False)):
            self.assertEqual(krea.ist_raw(name), ja, name)
        self.assertAlmostEqual(krea.raw_mu(256, 256), 0.5)
        self.assertAlmostEqual(krea.raw_mu(1280, 1280), 1.15)

    def test_raw_malt_mit_cfg_und_vielen_schritten(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as d, mock.patch.object(krea, "OUT_DIR", Path(d)), \
                mock.patch.object(krea, "missing_reason", return_value=None), \
                mock.patch.object(krea, "discover", return_value={"krea2_raw": Path("r.safetensors"),
                                                                  "turbo": Path("t.safetensors")}), \
                mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}), \
                mock.patch.object(krea, "_uhr_neu"), \
                mock.patch.object(krea, "_male", return_value=np.zeros((8, 8, 3), np.uint8)) as male, \
                mock.patch("profilordner.bild_ziel", return_value=Path(d) / "b.png"), \
                mock.patch("config.load", return_value={}):
            krea.generate("x", model="krea2_raw", size=(1024, 1024))
            a, kw = male.call_args.args, male.call_args.kwargs
            self.assertEqual((a[4], a[5], kw["cfg"]), (52, "euler", 3.5))
            self.assertAlmostEqual(a[6], krea.raw_mu(1024, 1024))
            krea.generate("x", model="krea2_raw", steps=9)                   # zu wenig für Raw
            self.assertEqual(male.call_args.args[4], 52)
            krea.generate("x", model="krea2_raw", steps=30)
            self.assertEqual(male.call_args.args[4], 30)
            krea.generate("x", model="turbo")
            self.assertEqual((male.call_args.kwargs["cfg"], male.call_args.args[6]), (0.0, krea.DEFAULTS["shift"]))

    def test_cfg_rechnet_gegen_den_leeren_prompt(self):
        modell = lambda x, t, txt: torch.full_like(x, float(txt))           # noqa: E731
        sig = torch.tensor([1.0, 0.0])
        x = torch.zeros(1, 4)
        self.assertTrue(torch.allclose(krea._sample(modell, x, 2.0, sig, "euler", 0), x - 2.0))
        self.assertTrue(torch.allclose(krea._sample(modell, x, 2.0, sig, "euler", 0, ohne=1.0, cfg=3.5),
                                       x - 5.5))                             # 2 + 3,5 · (2 − 1)


def _falscher_tokenizer(text):
    """Vorlage als Token: Systemteil, <|im_start|>user\\n, je Wort ein Token, 5 Schluss-Token."""
    prompt = text.split("<|im_start|>user\n", 1)[1].split("<|im_end|>", 1)[0]
    return ([krea._IM_START, 1, 2, krea._IM_START, krea._TOK_USER, krea._TOK_NL]
            + [7] * len(prompt.split()) + [9, 9, 9, 9, 9])


class DetailSatz(unittest.TestCase):
    """Detail-Satz der Schaltzentrale: nur mit Person, nicht gezeichnet, ohne Doppeltes, nur wenn er passt."""
    SATZ = "Sharp focus on the face and eyes, natural skin texture with visible pores, no retouching."

    def setUp(self):
        p = mock.patch.object(krea, "ueberlang", return_value=0)
        p.start()
        self.addCleanup(p.stop)

    def test_person_bekommt_satz(self):
        self.assertEqual("A woman reading in a cafe. " + self.SATZ,
                         krea.mit_detail("A woman reading in a cafe.", self.SATZ))

    def test_ohne_person_oder_gezeichnet_bleibt(self):
        for prompt in ("A red fox in the snow.", "An anime girl on a rooftop.", "A watercolor painting of a woman."):
            self.assertEqual(prompt, krea.mit_detail(prompt, self.SATZ), prompt)

    def test_schon_da_faellt_weg(self):
        neu = krea.mit_detail("A woman at a desk, sharp focus on face and eyes", self.SATZ)
        self.assertEqual(1, neu.lower().count("sharp focus"))
        self.assertTrue(neu.endswith("Natural skin texture with visible pores, no retouching."))

    def test_von_hinten_ohne_gesicht(self):
        neu = krea.mit_detail("A woman kneeling on a bed, viewed from behind.", self.SATZ)
        self.assertNotIn("eyes", neu.lower())
        self.assertTrue(neu.endswith("Natural skin texture with visible pores, no retouching."))
        self.assertEqual("A woman facing away.",
                         krea.mit_detail("A woman facing away.", "Sharp focus on the face and eyes, crisp irises."))

    def test_zu_lang_bleibt_weg(self):
        with mock.patch.object(krea, "ueberlang", return_value=3):
            self.assertEqual("A woman in a cafe.", krea.mit_detail("A woman in a cafe.", self.SATZ))

    def test_schalter_und_eigener_satz(self):
        with mock.patch("config.load", return_value={"bild_krea_detail": False}):
            self.assertEqual("", krea.detail_satz())
            self.assertEqual("A woman.", krea.mit_detail("A woman."))
        with mock.patch("config.load", return_value={"bild_krea_detail_satz": "  Crisp   eyes. "}):
            self.assertEqual("Crisp eyes.", krea.detail_satz())
        with mock.patch("config.load", return_value={}):
            self.assertEqual(krea.DETAIL_SATZ, krea.detail_satz())


class TextGrenze(unittest.TestCase):
    """Wie die Referenz-Pipeline: höchstens 512 Text-Token, der Schluss der Vorlage bleibt."""

    def test_kurz_bleibt_lang_wird_gekuerzt(self):
        with mock.patch.object(krea, "_tokenizer", return_value=_falscher_tokenizer):
            ids, ende, zuviel = krea._ids("a red fox")
            self.assertEqual((ende, zuviel, len(ids) - ende), (6, 0, 8))
            ids, ende, zuviel = krea._ids("wort " * 600)
            self.assertEqual(zuviel, 93)
            self.assertEqual(len(ids) - ende, krea.MAX_TEXT)
            self.assertEqual(ids[-5:], [9] * 5)
            self.assertEqual(93, krea.ueberlang("wort " * 600))
            self.assertEqual(0, krea.ueberlang("a red fox"))

    def test_hinweis_fuer_die_ki_nur_bei_krea(self):
        import actions
        with mock.patch.object(krea, "ueberlang", return_value=40), \
                mock.patch("imagegen.backend", return_value="krea"):
            self.assertEqual(40, actions._ueberlang("x"))
        with mock.patch.object(krea, "ueberlang", return_value=40), \
                mock.patch("imagegen.backend", return_value="webui"):
            self.assertEqual(0, actions._ueberlang("x"))
        self.assertIn("Bild 2: 40 Token zu viel", actions._kuerzung(["Bild 2: 40 Token zu viel"]))
        self.assertEqual("", actions._kuerzung([]))

    def test_ohne_tokenizer_kein_fehler(self):
        with mock.patch.object(krea, "_tokenizer", side_effect=FileNotFoundError):
            self.assertEqual(0, krea.ueberlang("a red fox"))

    def test_tokenizer_wird_einmal_gebaut(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(krea, "teile", return_value={"tokenizer": Path(d)}), \
                mock.patch.object(krea, "_tokenizer_bauen", return_value=_falscher_tokenizer) as bauen:
            krea._TOK_CACHE.clear()
            krea._tokenizer()
            krea._tokenizer()
        krea._TOK_CACHE.clear()
        self.assertEqual(1, bauen.call_count)


@unittest.skipIf(torch is None, "torch fehlt")
class TextZwischenspeicher(unittest.TestCase):
    """Gleicher Prompt (z. B. der Gesichts-Satz je Gesicht): kein zweites Mal Text-Encoder."""

    def setUp(self):
        krea._TEXT_CACHE.clear()
        self.addCleanup(krea._TEXT_CACHE.clear)

    def test_gleicher_prompt_ohne_encoder(self):
        with tempfile.TemporaryDirectory() as d:
            te = Path(d) / "te.safetensors"
            te.write_bytes(b"x")
            with mock.patch.object(krea, "_auf_gpu", return_value=object()) as gpu, \
                    mock.patch.object(krea, "_encode", side_effect=lambda p, t, dev: torch.ones(1, len(p), 2)), \
                    mock.patch.object(krea, "_frei"):
                a = krea._text_einbetten("ein satz", te, "cpu", torch.float32)
                b = krea._text_einbetten("ein satz", te, "cpu", torch.float32)
                self.assertEqual(1, gpu.call_count)
                self.assertTrue(torch.equal(a, b))
                krea._text_einbetten("anderer satz", te, "cpu", torch.float32)
                self.assertEqual(2, gpu.call_count)
                for i in range(krea._TEXT_CACHE_MAX + 2):
                    krea._text_einbetten(f"satz {i}", te, "cpu", torch.float32)
                self.assertEqual(krea._TEXT_CACHE_MAX, len(krea._TEXT_CACHE))
                te.write_bytes(b"neu und laenger")              # anderer Text-Encoder: neu rechnen
                n = gpu.call_count
                krea._text_einbetten(f"satz {krea._TEXT_CACHE_MAX + 1}", te, "cpu", torch.float32)
                self.assertEqual(n + 1, gpu.call_count)

    def test_entladen_leert_ihn(self):
        krea._TEXT_CACHE[("x", 0, 0, "p")] = torch.ones(1)
        krea.entladen()
        self.assertEqual({}, krea._TEXT_CACHE)


if __name__ == "__main__":
    unittest.main()
