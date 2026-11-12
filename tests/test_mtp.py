"""MTP-Kopf (Qwen 3.5/3.6): Vorschlags-Logits gleich einer Rechnung mit transformers-Bausteinen,
Hauptmodell unverändert, spekulative Läufe mit MTP-Vorschlägen gleich den Läufen ohne."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None

E = 64
TYPEN = ["linear_attention", "linear_attention", "linear_attention", "full_attention"]


def _cfg(n, typen):
    from test_neue_bauarten import _q35_cfg
    return _q35_cfg(T.Qwen3_5MoeTextConfig, num_hidden_layers=n, layer_types=typen, num_experts=8,
                    num_experts_per_tok=2, moe_intermediate_size=32, shared_expert_intermediate_size=48,
                    max_position_embeddings=256)


def _rms(x, w, eps=1e-6):
    return x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps) * w


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class MtpKopf(unittest.TestCase):
    N_CTX = 128

    @classmethod
    def setUpClass(cls):
        import gc
        import os
        import tempfile

        import gguf_testbau as B

        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        torch.manual_seed(0)
        hf5 = T.Qwen3_5MoeForCausalLM(_cfg(5, TYPEN + ["full_attention"])).eval().float()
        with torch.no_grad():
            for p in hf5.parameters():
                p.normal_(0, 0.2)
        m, t = B.umwandeln(hf5, "qwen35moe")
        m["qwen35moe.nextn_predict_layers"] = 1
        g = torch.Generator().manual_seed(1)
        cls.fc = torch.randn(E, 2 * E, generator=g) * 0.15
        cls.enorm, cls.hnorm, cls.kopfnorm = (torch.rand(E, generator=g) + 0.5 for _ in range(3))
        t.update({"blk.4.nextn.eh_proj.weight": cls.fc, "blk.4.nextn.enorm.weight": cls.enorm,
                  "blk.4.nextn.hnorm.weight": cls.hnorm, "blk.4.nextn.shared_head_norm.weight": cls.kopfnorm})
        sd = hf5.state_dict()
        # Hauptmodell: die ersten vier Schichten; MTP-Block: Schicht 4 als eigenes Modell
        cls.haupt = T.Qwen3_5MoeForCausalLM(_cfg(4, TYPEN)).eval().float()
        cls.haupt.load_state_dict({k: v for k, v in sd.items() if not k.startswith("model.layers.4.")})
        cls.block = T.Qwen3_5MoeForCausalLM(_cfg(1, ["full_attention"])).eval().float()
        eins = {k.replace("model.layers.4.", "model.layers.0."): v for k, v in sd.items()
                if k.startswith("model.layers.4.")}
        eins.update({"model.embed_tokens.weight": sd["model.embed_tokens.weight"], "lm_head.weight": sd["lm_head.weight"],
                     "model.norm.weight": cls.kopfnorm - 1})          # Gewichte +1 wie in der GGUF
        cls.block.load_state_dict(eins)
        cls.emb = sd["model.embed_tokens.weight"]
        fd, cls.pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        B.schreibe_gguf(cls.pfad, m, t)
        cls.gg = GGUFFile(cls.pfad)
        cls.modell = build_model(cls.gg, torch.device("cpu"), torch.float32, cls.N_CTX, quant=False)
        cls._gc = gc

    @classmethod
    def tearDownClass(cls):
        import os
        del cls.modell
        cls._gc.collect()
        cls.gg.close()
        os.unlink(cls.pfad)

    def _eingabe(self, h, tokens):
        e = _rms(self.emb[tokens], self.enorm)
        return torch.cat([e, _rms(h, self.hnorm)], dim=-1) @ self.fc.t()

    def test_mtp_gleich_referenz(self):
        mod = self.modell
        self.assertIsNotNone(mod.mtp)
        ids = torch.randint(0, 96, (30,), generator=torch.Generator().manual_seed(3))
        with torch.no_grad():
            H = self.haupt.model(ids[None]).last_hidden_state[0]           # nach der Schluss-Norm
            ref_haupt = self.haupt(ids[None]).logits[0]
            # MTP-Einträge (H_i, x_{i+1}) für i = 0..28; Zeile i sagt x_{i+2} voraus
            X = self._eingabe(H[:-1], ids[1:])
            aus = self.block.model(inputs_embeds=X[None]).last_hidden_state[0]
            ref = self.block.lm_head(aus)
            d = int(ref[-1].argmax())
            X2 = torch.cat([X, self._eingabe(aus[-1:], torch.tensor([d]))])
            ref2 = self.block(inputs_embeds=X2[None]).logits[0, -1]
        mod.reset()
        with torch.no_grad():
            # Einlesen in Stücken und Einzelschritten – das Hauptmodell bleibt wie ohne MTP
            haupt = mod.forward(ids[:20].tolist(), 0)
            torch.testing.assert_close(haupt, ref_haupt[19], rtol=2e-3, atol=2e-3)
            for j in range(20, 29):
                haupt = mod.forward([int(ids[j])], j)
            torch.testing.assert_close(haupt, ref_haupt[28], rtol=2e-3, atol=2e-3)
            s = mod._mtp_schritt
            s.h.copy_(mod._mtp_h)
            l1 = s.logits(torch.tensor([int(ids[29])]), torch.tensor([29]), 30)
            l2 = s.logits(torch.tensor([d]), torch.tensor([30]), 31)
        torch.testing.assert_close(l1, ref[-1], rtol=2e-3, atol=2e-3)
        torch.testing.assert_close(l2, ref2, rtol=2e-3, atol=2e-3)
        self.assertEqual(mod.mtp_vorschlag(int(ids[29]), 29, 2)[0], int(ref[-1].argmax()))

    def test_hauptausgang_voller_kopf_mit_sicherheit(self):
        """Der Vorschlag kommt aus dem vollen Kopf (bestes Token aller Wörter) samt seiner
        Wahrscheinlichkeit; weitere Vorschläge nur, solange der Kopf sicher ist."""
        mod = self.modell
        s = mod._mtp_schritt
        ids = torch.randint(0, 96, (20,), generator=torch.Generator().manual_seed(3)).tolist()
        with torch.no_grad():
            mod.reset()
            mod.forward(ids, 0)
            n = len(ids)
            s.h.copy_(mod._mtp_h)
            voll = s.logits(torch.tensor([5]), torch.tensor([n]), n + 1)
            s.h.copy_(mod._mtp_h)
            tid, sicher = s._run(torch.tensor([5]), torch.tensor([n]), n + 1).tolist()
            self.assertEqual(int(tid), int(voll.argmax()))
            self.assertAlmostEqual(sicher, float(torch.softmax(voll, -1).max()), places=5)
            with mock.patch.object(type(mod), "MTP_SICHER", 1.01):      # nie sicher genug: nur der erste
                self.assertEqual(len(mod.mtp_vorschlag(5, n, 3)), 1)
            with mock.patch.object(type(mod), "MTP_SICHER", 0.0):       # immer sicher: alle drei
                self.assertEqual(len(mod.mtp_vorschlag(5, n, 3)), 3)

    def test_spekulativ_gleich(self):
        from ggufengine.engine import Engine
        from ggufengine.sampling import SamplerConfig
        mod = self.modell

        def motor(sp):
            e = Engine.__new__(Engine)
            e.model, e.device, e.n_ctx, e.max_tok_s = mod, torch.device("cpu"), self.N_CTX, None
            e._pos, e._history, e._own, e._keys = 0, [], [], []
            e._mrope, e._rope_state = True, (0, 0)
            e.tokenizer = NS(stop_ids=set())
            e.spekulativ, e._vorrat = sp, []
            mod.reset()
            return e
        prompt = torch.randint(3, 96, (12,), generator=torch.Generator().manual_seed(4)).tolist()
        for cfg in (SamplerConfig(temperature=0), SamplerConfig(temperature=0.9, top_k=0, top_p=1.0, seed=2)):
            ref = list(motor(0).generate_tokens(prompt, max_tokens=30, sampler=cfg))
            e = motor(8)
            self.assertEqual(list(e.generate_tokens(prompt, max_tokens=30, sampler=cfg)), ref)
            self.assertGreater(e._spek_stat[0], 0)

    def test_nachholen_mit_mtp(self):
        """Mit MTP-Kopf gehört auch der letzte Zustand für den MTP-Kopf zum Nachholen."""
        from ggufengine.models.common import ZeilenMerken
        mod = self.modell
        ids = torch.randint(3, 96, (20,), generator=torch.Generator().manual_seed(8)).tolist()
        mod.reset()
        with torch.no_grad():
            mod.forward(ids[:12], 0)
            vorher = mod.snapshot_state()
            with ZeilenMerken():
                mod.alle_logits = True
                mod.forward(ids[12:15], 12)
                mod.alle_logits = False
            mod.restore_state(vorher)
            self.assertTrue(mod.zeilen_nachholen(3, 2))
            h_nach = mod._mtp_h.clone()
            a = mod.forward([ids[14]], 14).clone()
            mod.restore_state(vorher)
            mod.forward(ids[12:14], 12)
            torch.testing.assert_close(h_nach, mod._mtp_h, rtol=1e-4, atol=1e-4)
            torch.testing.assert_close(a, mod.forward([ids[14]], 14), rtol=1e-4, atol=1e-4)


if __name__ == "__main__":
    unittest.main()
