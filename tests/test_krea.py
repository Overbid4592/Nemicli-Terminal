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

    def test_paint_geht_an_krea_ohne_negativ(self):
        import imagegen
        with mock.patch.object(imagegen, "backend", return_value="krea"), \
                mock.patch.object(krea, "generate", return_value="bild.png") as gen:
            pfad = imagegen.paint("a fox", neg="egal", cfg=7, sampler="dpmpp_2m")
        self.assertEqual("bild.png", pfad)
        self.assertNotIn("neg", gen.call_args.kwargs)
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


def _falscher_tokenizer(text):
    """Vorlage als Token: Systemteil, <|im_start|>user\\n, je Wort ein Token, 5 Schluss-Token."""
    prompt = text.split("<|im_start|>user\n", 1)[1].split("<|im_end|>", 1)[0]
    return ([krea._IM_START, 1, 2, krea._IM_START, krea._TOK_USER, krea._TOK_NL]
            + [7] * len(prompt.split()) + [9, 9, 9, 9, 9])


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
