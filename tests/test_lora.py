"""LoRA-Kern (engines/lora.py) mit einem Mini-Wan-DiT aus Zufallsgewichten: neutral ohne Training, Gradienten
durch eingefrorene (auch FP8-)Gewichte, lernt eine bekannte Änderung nach, Speichern/Laden/Einmischen – ohne
Modelldateien."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tests"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import lora as L                 # noqa: E402
import video as V                # noqa: E402
from test_video import dit_zufall  # noqa: E402


class LoraKern(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(20)
        self.sd, self.cfg = dit_zufall()
        self.x = torch.randn(48, 2, 4, 4)
        self.ctx = torch.randn(5, 24)

    def test_ohne_training_neutral(self):
        dit = V.WanDiT(self.sd, self.cfg)
        with torch.no_grad():
            vorher = dit(self.x, 400.0, self.ctx)
        schichten = L.anbringen(self.sd, rang=4)
        self.assertEqual(len(schichten), 2 * 10)                      # 2 Blöcke × (4 + 4 Attention + 2 FFN)
        with torch.no_grad():
            nachher = V.WanDiT(self.sd, self.cfg)(self.x, 400.0, self.ctx)
        self.assertTrue(torch.allclose(vorher, nachher, atol=1e-6))
        L.abnehmen(self.sd)
        self.assertFalse(any(isinstance(v, L.LoraLinear) for v in self.sd.values()))

    def test_gradient_durch_eingefrorenes_gewicht(self):
        w = torch.randn(6, 8)
        x = torch.randn(3, 8, requires_grad=True)
        L._basis_mit_gradient(w, x).sum().backward()
        self.assertTrue(torch.allclose(x.grad, torch.ones(3, 6) @ w, atol=1e-6))
        f8 = V._F8(w)                                                  # FP8-Gewicht: rückwärts entpackt
        x2 = torch.randn(3, 8, requires_grad=True)
        L._basis_mit_gradient(f8, x2).sum().backward()
        soll = torch.ones(3, 6) @ (f8.w8.float() * f8.ws)
        self.assertLess(((x2.grad.float() - soll).norm() / soll.norm()).item(), 1e-2)   # bf16 gerechnet

    def test_lernt_bekannte_aenderung(self):
        lehrer_sd = copy.deepcopy(self.sd)                             # Lehrer: kleine Rang-2-Änderung in einem Block
        k = "blocks.1.self_attn.o.weight"
        lehrer_sd[k] = lehrer_sd[k] + 0.3 * torch.randn(32, 2) @ torch.randn(2, 32)
        x0, eps = torch.randn(48, 2, 4, 4), torch.randn(48, 2, 4, 4)
        xs = 0.5 * x0 + 0.5 * eps
        with torch.no_grad():
            ziel = V.WanDiT(lehrer_sd, self.cfg)(xs, 500.0, self.ctx)
        schichten = L.anbringen(self.sd, rang=4, alpha=4)
        dit = V.WanDiT(self.sd, self.cfg)
        dit.lernen = True
        opt = torch.optim.AdamW(L.parameter(schichten), lr=3e-3, weight_decay=0.0)
        verluste = []
        for _ in range(150):
            opt.zero_grad()
            verlust = torch.nn.functional.mse_loss(dit(xs, 500.0, self.ctx), ziel)
            verlust.backward()
            opt.step()
            verluste.append(verlust.item())
        self.assertLess(verluste[-1], 0.1 * verluste[0], verluste[::30])

    def test_flow_verlust_mit_gradienten(self):
        schichten = L.anbringen(self.sd, rang=4)
        dit = V.WanDiT(self.sd, self.cfg)
        dit.lernen = True
        verlust = L.flow_verlust(dit, torch.randn(48, 2, 4, 4), self.ctx, 0.7, torch.randn(48, 2, 4, 4))
        verlust.backward()
        self.assertEqual(verlust.dim(), 0)
        self.assertTrue(all(p.grad is not None for p in L.parameter(schichten)))

    def test_speichern_laden_einmischen(self):
        schichten = L.anbringen(self.sd, rang=4)
        with torch.no_grad():
            for s in schichten.values():
                s.B.normal_(0, 0.05)                                   # wie nach dem Training
            mit_lora = V.WanDiT(self.sd, self.cfg)(self.x, 400.0, self.ctx)
        pfad = Path(tempfile.mkdtemp()) / "LoRA" / "wan22-5b.safetensors"
        L.speichern(pfad, schichten, {"ziel": "wan22-5b"})
        self.assertTrue(pfad.is_file() and not pfad.with_suffix(".part").exists())
        lora, meta = L.laden(pfad)
        self.assertEqual((len(lora), meta["rang"], meta["ziel"]), (20, "4", "wan22-5b"))
        L.abnehmen(self.sd)
        self.assertEqual(L.einmischen(self.sd, lora, dtype=torch.float32), 20)
        with torch.no_grad():
            eingemischt = V.WanDiT(self.sd, self.cfg)(self.x, 400.0, self.ctx)
        self.assertTrue(torch.allclose(mit_lora, eingemischt, atol=1e-4))

    def test_ablage_je_profil(self):
        import profilordner
        self.assertIn("LoRA", profilordner.UNTERORDNER)
        p = profilordner.lora_pfad("wan22-5b", name="Test Profil")
        self.assertEqual((p.name, p.parent.name, p.parent.parent.name), ("wan22-5b.safetensors", "LoRA", "Test_Profil"))


if __name__ == "__main__":
    unittest.main()
