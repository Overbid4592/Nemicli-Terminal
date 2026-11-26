"""LoRA-Motor für Gemma 4: Lernweg rechnet wie der GGUF-Motor, LoRA an- und abklinken, Training lernt.

Winziges Zufalls-Gemma-4 mit allem, was E2B/E4B haben: Einbettung je Schicht (PLE), Sliding-Window- und
Voll-Schichten, geteilte K/V der letzten Schichten, V = K bei Voll-Schichten, Logit-Softcap. Kein echtes
Modell nötig.
python -m unittest tests.test_loramotor_gemma4
"""
import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests", "core", "tools", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import torch                                        # noqa: E402

import test_gemma4_familie as F                     # noqa: E402

EPL = 8                                             # Breite der Einbettung je Schicht


def _mit_ple(geteilt=2, seed=3):
    meta, t = F._baue(geteilt=geteilt, seed=seed)
    g = torch.Generator().manual_seed(seed + 1)
    r = lambda *s, sd=0.2: torch.randn(*s, generator=g) * sd
    n = meta["gemma4.block_count"]
    meta["gemma4.embedding_length_per_layer_input"] = EPL
    t["per_layer_token_embd.weight"] = r(F.V, n * EPL, sd=0.5)
    t["per_layer_model_proj.weight"] = r(n * EPL, F.E)
    t["per_layer_proj_norm.weight"] = 1.0 + r(EPL, sd=0.1)
    for i in range(n):
        p = f"blk.{i}."
        t[p + "inp_gate.weight"], t[p + "proj.weight"] = r(EPL, F.E), r(F.E, EPL)
        t[p + "post_norm.weight"] = 1.0 + r(F.E, sd=0.1)
    return meta, t


class LoraGemma4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        cls._tmp = tempfile.TemporaryDirectory()
        cls.gguf = os.path.join(cls._tmp.name, "mini-gemma4.gguf")
        B.schreibe_gguf(cls.gguf, *_mit_ple())
        cls.gg = GGUFFile(cls.gguf)
        cls.model = build_model(cls.gg, torch.device("cpu"), torch.float32, F.N_CTX, quant=False)

    @classmethod
    def tearDownClass(cls):
        del cls.model
        gc.collect()
        cls.gg.close()
        cls._tmp.cleanup()

    def _motor_logits(self, ids):
        self.model.reset()
        self.model.alle_logits = True
        try:
            with torch.inference_mode():
                return self.model.forward(ids.tolist(), 0).float().clone()
        finally:
            self.model.alle_logits = False
            self.model.reset()

    def test_lernweg_rechnet_wie_der_motor(self):
        from loramotor.lernweg import Gemma4Lernweg, fuer
        self.assertEqual("Gemma4Model", type(self.model).__name__)
        self.assertTrue(self.model.n_pl > 0 and any(not s.has_kv for s in self.model.layers))
        ids = torch.randint(0, F.V, (40,))                       # länger als das Fenster (4)
        lw = fuer(self.model, "gemma4", rang=4)
        self.assertIsInstance(lw, Gemma4Lernweg)
        with torch.no_grad():
            eigen = lw.logits(ids)
        ref = self._motor_logits(ids)
        self.assertLess(float((eigen - ref).abs().max()), 1e-3 * max(1.0, float(ref.abs().max())))

    def test_schichten_auch_bei_geteilten_kv(self):
        from loramotor import adapter as A
        namen = A.schichten(self.model)
        n = len(self.model.layers)
        self.assertIn(f"blk.{n - 1}.attn_q.weight", namen)
        self.assertNotIn(f"blk.{n - 1}.attn_k.weight", namen)           # geteilte Schicht: kein eigenes K
        self.assertIn("blk.0.attn_v.weight", namen)
        self.assertNotIn("blk.2.attn_v.weight", namen)                   # Voll-Schicht: V = K
        self.assertIn("blk.0.ffn_down.weight", namen)

    def test_anklinken_gleich_lernweg_und_abklinken_wie_vorher(self):
        from loramotor import adapter as A
        from loramotor.lernweg import fuer
        ids = torch.randint(0, F.V, (30,))
        vorher = self._motor_logits(ids)
        lw = fuer(self.model, "gemma4", rang=4)
        torch.manual_seed(1)
        with torch.no_grad():
            for b in lw.b.values():
                b.normal_(0, 0.05)
            erwartet = lw.logits(ids)
        datei = os.path.join(self._tmp.name, "mini.safetensors")
        A.speichern(datei, lw.gewichte(), {"basis": A.basis_kennung(self.gguf), "rang": 4, "alpha": lw.alpha})
        self.assertEqual(A.anklinken(self.model, datei, self.gguf), len(A.schichten(self.model)))
        try:
            mit = self._motor_logits(ids)
        finally:
            self.assertEqual(A.abklinken(self.model), len(A.schichten(self.model)))
        self.assertGreater(float((mit - vorher).abs().max()), 1e-2)
        self.assertLess(float((mit - erwartet).abs().max()), 1e-3 * max(1.0, float(erwartet.abs().max())))
        self.assertLess(float((self._motor_logits(ids) - vorher).abs().max()), 1e-5)

    def test_training_senkt_den_verlust(self):
        from loramotor.lernweg import fuer
        torch.manual_seed(2)
        lw = fuer(self.model, "gemma4", rang=4)
        ids = torch.randint(0, F.V, (36,))
        maske = torch.zeros(36, dtype=torch.bool)
        maske[14:] = True
        opt = torch.optim.AdamW(lw.parameter(), lr=1e-2)
        with torch.no_grad():
            anfang = float(lw.verlust(ids, maske))
        for _ in range(30):
            opt.zero_grad()
            v = lw.verlust(ids, maske)
            v.backward()
            opt.step()
        self.assertLess(float(v.detach()), anfang * 0.7)
        self.assertTrue(all(p.grad is not None for p in lw.parameter()))

    def test_halb_gespeichert_rechnet_gleich(self):
        from loramotor import adapter as A
        from loramotor.lernweg import fuer
        ids = torch.randint(0, F.V, (24,))
        lw = fuer(self.model, "gemma4", rang=4)
        with torch.no_grad():
            for b in lw.b.values():
                b.normal_(0, 0.05)
        voll, halb = (os.path.join(self._tmp.name, n) for n in ("voll.safetensors", "halb.safetensors"))
        meta = {"basis": A.basis_kennung(self.gguf), "rang": 4, "alpha": lw.alpha}
        A.speichern(voll, lw.gewichte(), meta)
        A.speichern(halb, lw.gewichte(), meta, halb=True)
        self.assertLess(os.path.getsize(halb), 0.6 * os.path.getsize(voll))
        ergebnis = []
        for datei in (voll, halb):
            A.anklinken(self.model, datei, self.gguf)
            try:
                ergebnis.append(self._motor_logits(ids))
            finally:
                A.abklinken(self.model)
        self.assertLess(float((ergebnis[0] - ergebnis[1]).abs().max()), 2e-2 * max(1.0, float(ergebnis[0].abs().max())))

    def test_gewichteter_verlust(self):
        from loramotor.lernweg import fuer
        lw = fuer(self.model, "gemma4", rang=2)
        ids = torch.randint(0, F.V, (20,))
        maske = torch.arange(20) >= 8
        with torch.no_grad():
            gleich = float(lw.verlust(ids, maske))
            eins = float(lw.verlust(ids, maske, torch.ones(20)))
            vorn = torch.where(torch.arange(20) < 12, 4.0, 1.0)
            betont = float(lw.verlust(ids, maske, vorn))
        self.assertAlmostEqual(gleich, eins, places=5)
        self.assertNotAlmostEqual(gleich, betont, places=3)

    def test_zu_lang_fuer_den_kontext(self):
        from loramotor.lernweg import fuer
        with self.assertRaises(ValueError):
            fuer(self.model, "gemma4", rang=2).logits(torch.zeros(F.N_CTX + 1, dtype=torch.long))


if __name__ == "__main__":
    unittest.main()
