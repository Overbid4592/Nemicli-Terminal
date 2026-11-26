"""Charakter-LoRA für Krea 2: an den Blöcken anbringen/einsetzen/abnehmen, NVFP4 entpacken für den Rückweg,
Nachrechnen statt Aufheben, Weg über paint(figur) und die Werkzeuge – ohne Modelldateien, auf der CPU."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import torch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools", "tests"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import krea  # noqa: E402
import lora as L  # noqa: E402
import lora_training as LT  # noqa: E402

D, KV, MLP = 32, 8, 48


def _linear(aus, ein, gen):
    return krea._Linear({"x.weight": torch.randn(aus, ein, generator=gen) * 0.1}, "x", torch.float32)


def _mini_dit(bloecke=2):
    gen = torch.Generator().manual_seed(0)
    dit = object.__new__(krea._DiT)
    dit.blocks = []
    for _ in range(bloecke):
        attn = SimpleNamespace(wq=_linear(D, D, gen), wk=_linear(KV, D, gen), wv=_linear(KV, D, gen),
                               gate=_linear(D, D, gen), wo=_linear(D, D, gen))
        mlp = SimpleNamespace(gate=_linear(MLP, D, gen), up=_linear(MLP, D, gen), down=_linear(D, MLP, gen))
        dit.blocks.append(SimpleNamespace(attn=attn, mlp=mlp))
    return dit


class Anbringen(unittest.TestCase):
    def test_alle_ziele_rechnen_erst_wie_vorher_und_lassen_sich_abnehmen(self):
        dit = _mini_dit()
        alt = dit.blocks[1].mlp.down
        x = torch.randn(3, MLP)
        vorher = alt(x)
        s = krea.lora_anbringen(dit, 4, 4.0, "cpu", torch.float32)
        self.assertEqual(len(s), 2 * len(krea.LORA_SCHICHTEN))
        self.assertIn("blocks.1.mlp.down", s)
        self.assertTrue(torch.equal(dit.blocks[1].mlp.down(x), vorher))           # B = 0
        e = krea.Eingabe(torch.randn(3, D))                                       # geteilte Eingabe geht auch
        self.assertEqual(tuple(dit.blocks[0].attn.wq(e).shape), (3, D))
        krea.lora_abnehmen(dit)
        self.assertIs(dit.blocks[1].mlp.down, alt)

    def test_einsetzen_aus_datei_mit_staerke(self):
        dit = _mini_dit()
        s = krea.lora_anbringen(dit, 4, 8.0, "cpu", torch.float32)
        with torch.no_grad():
            for sch in s.values():
                sch.B.normal_()
        with tempfile.TemporaryDirectory() as t:
            pfad = L.speichern(Path(t) / "krea2.safetensors", s, {"ziel": "krea2"})
            soll = s["blocks.0.attn.wq"]
            x = torch.randn(2, D)
            basis = soll.basis(x)
            zusatz = (x @ soll.A.t()) @ soll.B.t()
            krea.lora_abnehmen(dit)
            n = krea.lora_einsetzen(dit, pfad, 0.5, "cpu", torch.float32)
            self.assertEqual(n, len(s))
            y = dit.blocks[0].attn.wq(x)
            self.assertTrue(torch.allclose(y, basis + 0.5 * 2.0 * zusatz, atol=1e-5))   # skala = 8/4

            fremd = _mini_dit(bloecke=1)                                       # Schichten fehlen dort
            with self.assertRaises(RuntimeError):
                krea.lora_einsetzen(fremd, pfad, 1.0, "cpu", torch.float32)
            self.assertNotIsInstance(fremd.blocks[0].attn.wq, L.LoraLinear)      # aufgeräumt

    def test_lernt_durch_den_block(self):
        dit = _mini_dit(bloecke=1)
        s = krea.lora_anbringen(dit, 4, 4.0, "cpu", torch.float32)
        blk = dit.blocks[0]
        x = torch.randn(5, D, requires_grad=True)
        y = blk.mlp.down(torch.nn.functional.silu(blk.mlp.gate(x)) * blk.mlp.up(x)).sum()
        y.backward()
        self.assertIsNotNone(s["blocks.0.mlp.down"].B.grad)
        self.assertIsNotNone(x.grad)                                              # Rückweg durchs Grundgewicht


class Nvfp4(unittest.TestCase):
    def test_entpacken_wie_ohne_tensorkerne(self):
        gen = torch.Generator().manual_seed(1)
        aus, ein = 128, 64
        q = torch.randint(0, 256, (aus, ein // 2), generator=gen, dtype=torch.uint8)
        skalen = (torch.rand(aus, ein // 16, generator=gen) + 0.5).to(torch.float8_e4m3fn)
        ts = torch.tensor(0.25)
        a = object.__new__(krea._Linear)
        a.__dict__.update(art="nvfp4", q=q, out_f=aus, in_f=ein, dtype=torch.float32,
                          scale=(skalen.float() * ts).to(torch.float32))
        b = object.__new__(krea._Linear)
        b.__dict__.update(art="nvfp4_mm", q=q, out_f=aus, in_f=ein, dtype=torch.float32,
                          bs=krea._to_blocked(skalen).reshape(-1), ts=ts, scale=None)
        self.assertTrue(torch.equal(a.weight(), b.weight()))
        self.assertEqual(L._form(b), (aus, ein))
        self.assertTrue(torch.equal(L._entpackt(b, torch.float32, "cpu"), a.weight()))


class Sparsam(unittest.TestCase):
    def test_gleiche_gradienten_wie_ohne_nachrechnen(self):
        gen = torch.Generator().manual_seed(2)
        w = [torch.randn(8, 8, generator=gen, requires_grad=True) for _ in range(3)]
        bloecke = [lambda z, vec, rope, w=w_: torch.tanh(z @ w + vec) * rope[0] for w_ in w]
        z0, vec, rope = torch.randn(4, 8), torch.randn(8), (torch.tensor(0.9),)
        (LT._sparsam(bloecke, z0, vec, rope) ** 2).sum().backward()
        mit = [x.grad.clone() for x in w]
        for x in w:
            x.grad = None
        z = z0
        for blk in bloecke:
            z = blk(z, vec, rope)
        (z ** 2).sum().backward()
        for g1, x in zip(mit, w):
            self.assertTrue(torch.allclose(g1, x.grad, atol=1e-6))


class Weg(unittest.TestCase):
    def test_paint_nimmt_lora_nur_mit_figur(self):
        import imagegen
        with tempfile.TemporaryDirectory() as t:
            datei = Path(t) / "krea2.safetensors"
            datei.write_bytes(b"x")
            with mock.patch.object(imagegen, "backend", return_value="krea"), \
                    mock.patch.object(krea, "lora_fuer_figur", return_value=datei), \
                    mock.patch.object(krea, "generate", return_value="a.png") as gen:
                imagegen.paint.__wrapped__("a cat", figur=False)
                self.assertNotIn("lora", gen.call_args.kwargs)
                imagegen.paint.__wrapped__("me", figur=True)
                self.assertEqual(gen.call_args.kwargs["lora"], datei)

    def test_lora_fuer_figur_aus_und_ohne_datei(self):
        import profilordner
        with tempfile.TemporaryDirectory() as t:
            ziel = Path(t) / "krea2.safetensors"
            with mock.patch.object(profilordner, "lora_pfad", return_value=ziel), \
                    mock.patch.object(krea, "_cfg", side_effect=lambda k, s: s):
                self.assertIsNone(krea.lora_fuer_figur())
                ziel.write_bytes(b"x")
                self.assertEqual(krea.lora_fuer_figur(), ziel)
            with mock.patch.object(profilordner, "lora_pfad", return_value=ziel), \
                    mock.patch.object(krea, "_cfg", side_effect=lambda k, s: False if k == "bild_lora" else s):
                self.assertIsNone(krea.lora_fuer_figur())

    def test_bild_malen_meldet_figur(self):
        import actions
        import imagegen
        with mock.patch.object(imagegen, "missing_reason_aktiv", return_value=None), \
                mock.patch.object(imagegen, "modell_aufloesen", return_value=(None, "")), \
                mock.patch.object(imagegen, "paint", return_value="a.png") as paint, \
                mock.patch.object(imagegen, "gesicht_nachbessern", return_value=None), \
                mock.patch.object(actions, "_figur", return_value=("prompt", 5, None, "")), \
                mock.patch.object(actions.os, "startfile", create=True):
            actions._bild_malen({"prompt": "Strand", "figur": True})
            self.assertTrue(paint.call_args.kwargs["figur"])
            actions._bild_malen({"prompt": "Strand"})
            self.assertFalse(paint.call_args.kwargs["figur"])
            self.assertIsNone(paint.call_args.kwargs["lora"])

    def test_motor_namen(self):
        self.assertEqual(LT.motor_ziel(None), "wan22-5b")
        self.assertEqual(LT.motor_ziel("Bild"), "krea2")
        self.assertEqual(LT.motor_ziel("krea"), "krea2")
        with self.assertRaises(ValueError):
            LT.motor_ziel("ton")

    def test_stand_fuer_bilder(self):
        import actions
        import paths
        import profilordner
        with tempfile.TemporaryDirectory() as t, mock.patch.object(paths, "DATEN", Path(t)), \
                mock.patch.object(profilordner, "aktiv", return_value="test_user"):
            text = actions._lora({"motor": "bild"})
            self.assertIn("Bilder (Krea 2)", text)
            self.assertIn("Fertige LoRA: keine", text)
            self.assertFalse(actions._lora({"motor": "ton"}).ok)


if __name__ == "__main__":
    unittest.main()
