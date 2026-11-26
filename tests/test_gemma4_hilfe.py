"""Gemma-4-Hilfskopf (MTP-Assistent): Vorschläge gleich transformers Gemma4AssistantForCausalLM, aus
safetensors (Zentroiden-Kopf) und GGUF (voller Kopf); spekulative Läufe mit dem Hilfskopf gleich den
Läufen ohne; Dateisuche im Modell-Ordner.

python -m unittest tests.test_gemma4_hilfe
"""
import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
    from safetensors.torch import save_file
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None

if torch is not None:
    from test_gemma4_familie import D_SWA, D_VOLL, E, FENSTER, KV, V, _baue

N_CTX, e, C = 48, 32, 8
GETEILT = 2


def _hf_hilfe(ordnung: bool):
    cfg = T.Gemma4AssistantConfig(
        backbone_hidden_size=E, num_centroids=C, centroid_intermediate_top_k=2, use_ordered_embeddings=ordnung,
        text_config=dict(hidden_size=e, intermediate_size=48, num_hidden_layers=4, num_attention_heads=4,
                         num_key_value_heads=KV[0], num_global_key_value_heads=KV[2], head_dim=D_SWA,
                         global_head_dim=D_VOLL, layer_types=["sliding_attention"] * 3 + ["full_attention"],
                         num_kv_shared_layers=4, sliding_window=FENSTER, vocab_size=V,
                         hidden_size_per_layer_input=0, vocab_size_per_layer_input=0,
                         rope_parameters={"full_attention": {"rope_type": "proportional", "partial_rotary_factor": 0.5,
                                                             "rope_theta": 1e6},
                                          "sliding_attention": {"rope_type": "default", "rope_theta": 1e4}},
                         max_position_embeddings=128, tie_word_embeddings=True))
    torch.manual_seed(5)
    m = T.Gemma4AssistantForCausalLM(cfg).eval().float()
    g = torch.Generator().manual_seed(6)
    with torch.no_grad():
        for name, p in m.named_parameters():
            if name.endswith("layer_scalar"):
                p.copy_(0.5 + torch.rand(p.shape, generator=g))
            elif "norm" in name:
                p.copy_(1.0 + 0.1 * torch.randn(p.shape, generator=g))
            else:
                p.copy_(0.3 * torch.randn(p.shape, generator=g))
        if ordnung:
            m.masked_embedding.token_ordering.copy_(torch.randperm(V, generator=g))
    return m


def _gguf_namen(sd: dict) -> dict:
    """Namen wie die llama.cpp-Umwandlung (ohne Zentroiden-Kopf)."""
    schicht = {"input_layernorm.weight": "attn_norm.weight", "self_attn.q_proj.weight": "attn_q.weight",
               "self_attn.q_norm.weight": "attn_q_norm.weight", "self_attn.o_proj.weight": "attn_output.weight",
               "post_attention_layernorm.weight": "post_attention_norm.weight",
               "pre_feedforward_layernorm.weight": "ffn_norm.weight", "mlp.gate_proj.weight": "ffn_gate.weight",
               "mlp.up_proj.weight": "ffn_up.weight", "mlp.down_proj.weight": "ffn_down.weight",
               "post_feedforward_layernorm.weight": "post_ffw_norm.weight", "layer_scalar": "layer_output_scale.weight"}
    t = {"token_embd.weight": sd["model.embed_tokens.weight"], "output_norm.weight": sd["model.norm.weight"],
         "nextn.pre_projection.weight": sd["pre_projection.weight"],
         "nextn.post_projection.weight": sd["post_projection.weight"]}
    for k, v in sd.items():
        if k.startswith("model.layers."):
            i, rest = k[len("model.layers."):].split(".", 1)
            t[f"blk.{i}.{schicht[rest]}"] = v
    meta = {"general.architecture": "gemma4-assistant", "gemma4-assistant.block_count": 4,
            "gemma4-assistant.embedding_length": e, "gemma4-assistant.embedding_length_out": E}
    return meta, t


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class Hilfskopf(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import gguf_testbau as B

        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        cls.tmp = tempfile.mkdtemp()
        meta, t = _baue(geteilt=GETEILT)
        cls.t = t
        cls.pfad = os.path.join(cls.tmp, "haupt.gguf")
        B.schreibe_gguf(cls.pfad, meta, t)
        cls.gg = GGUFFile(cls.pfad)
        cls.modell = build_model(cls.gg, torch.device("cpu"), torch.float32, N_CTX, quant=False)
        cls.hf = _hf_hilfe(True)
        sd = {k: v.contiguous() for k, v in cls.hf.state_dict().items() if k != "lm_head.weight"}
        cls.st = os.path.join(cls.tmp, "model.safetensors")
        save_file(sd, cls.st)
        Path(cls.tmp, "config.json").write_text('{"model_type": "gemma4_assistant", "centroid_intermediate_top_k": 2}')
        gm, gt = _gguf_namen(sd)
        cls.ggufkopf = os.path.join(cls.tmp, "gemma-4-test-it-assistant-F32.gguf")
        B.schreibe_gguf(cls.ggufkopf, gm, gt)

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.modell.hilfskopf_weg()
        del cls.modell
        gc.collect()
        cls.gg.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.modell.hilfskopf_weg()

    # -- Bezug: transformers mit den K/V des Motors -----------------------------------
    def _geteilt(self, n):
        """K/V der letzten KV-Schicht je Art für die Positionen vor n (Fenster: die letzten FENSTER - 1)."""
        lagen = self.modell.layers
        voll = next(l for l in reversed(lagen[:len(lagen) - GETEILT]) if not l.is_swa)
        swa = next(l for l in reversed(lagen[:len(lagen) - GETEILT]) if l.is_swa)
        k, v = voll.cache.kv(n)
        kv_voll = (k[:, :n].float()[None], v[:, :n].float()[None])
        pos = swa.cache.pos
        wahl = [int((pos == p).nonzero()[0]) for p in range(max(0, n - FENSTER + 1), n)]
        kv_swa = (swa.cache.k[:, wahl].float()[None], swa.cache.v[:, wahl].float()[None])
        return {"full_attention": kv_voll, "sliding_attention": kv_swa}

    def _hf_schritt(self, tok, h, n):
        x = torch.cat([self.t["token_embd.weight"][tok] * (E ** 0.5), h.reshape(-1)])[None, None]
        with torch.no_grad():
            aus = self.hf(inputs_embeds=x, position_ids=torch.tensor([[n]]), shared_kv_states=self._geteilt(n))
        return aus.logits[0, 0], aus.last_hidden_state[0, 0]

    def _vorbereiten(self, ids):
        m = self.modell
        m.reset()
        with torch.no_grad():
            logits = m.forward(ids, 0)
        return logits, m._h_zeilen[0:1].clone()

    def _vergleich(self, pfad, voller_kopf):
        m = self.modell
        m.hilfskopf_setzen(pfad)
        self.assertEqual(m.mtp.zentren is None, voller_kopf)
        ids = torch.randint(0, V, (11,), generator=torch.Generator().manual_seed(2)).tolist()
        n = len(ids)
        logits, h = self._vorbereiten(ids)
        # gemerkter Hidden-State = Eingang des Ausgabekopfs (nach der Schluss-Norm)
        lg = (h @ self.t["output.weight"].T)[0]
        torch.testing.assert_close(30.0 * torch.tanh(lg / 30.0), logits, rtol=1e-4, atol=1e-4)
        tok = 7
        self.hf.config.use_ordered_embeddings = not voller_kopf
        try:
            ref1, h1 = self._hf_schritt(tok, h, n)
            d1 = int(ref1.argmax())
            ref2, _ = self._hf_schritt(d1, h1, n)
        finally:
            self.hf.config.use_ordered_embeddings = True
        kopf = m.mtp
        with torch.no_grad():
            p = torch.tensor([n])
            l1, g1 = kopf.logits(torch.tensor([tok]), h, p, n + 1)
            l2, _ = kopf.logits(torch.tensor([d1]), g1, p, n + 1)
        for l, r in ((l1, ref1), (l2, ref2)):
            gerechnet = torch.isfinite(l)
            if voller_kopf:
                self.assertTrue(bool(gerechnet.all()))
            else:
                self.assertEqual(int(gerechnet.sum()), 2 * V // C)
                self.assertTrue(bool((r[gerechnet] > r[~gerechnet].max()).all()))   # bei HF die Füllwerte
            torch.testing.assert_close(l[gerechnet], r[gerechnet], rtol=1e-4, atol=1e-4)
        with mock.patch.object(type(m), "MTP_SICHER", 0.0):
            vorschlag = m.mtp_vorschlag(tok, n, 2)
        self.assertEqual(vorschlag, [d1, int(ref2.argmax())])

    def test_safetensors_wie_transformers(self):
        self._vergleich(self.st, voller_kopf=False)

    def test_gguf_voller_kopf_wie_transformers(self):
        self._vergleich(self.ggufkopf, voller_kopf=True)

    def test_ohne_passenden_lauf_kein_vorschlag(self):
        m = self.modell
        m.hilfskopf_setzen(self.st)
        ids = list(range(3, 12))
        self._vorbereiten(ids)
        self.assertEqual(m.mtp_vorschlag(5, len(ids) + 1, 3), [])      # pos - 1 nicht gerechnet
        m.reset()
        self.assertEqual(m.mtp_vorschlag(5, len(ids), 3), [])           # nach reset vergessen
        self.assertEqual(m.mtp_vorschlag(5, 0, 3), [])

    def test_sicherheit_begrenzt(self):
        m = self.modell
        m.hilfskopf_setzen(self.st)
        ids = list(range(3, 12))
        self._vorbereiten(ids)
        with mock.patch.object(type(m), "MTP_SICHER", 1.01):
            self.assertEqual(len(m.mtp_vorschlag(5, len(ids), 3)), 1)
        with mock.patch.object(type(m), "MTP_SICHER", 0.0):
            self.assertEqual(len(m.mtp_vorschlag(5, len(ids), 3)), 3)

    def test_falsche_breite_abgelehnt(self):
        import gguf_testbau as B

        from ggufengine.gguf import GGUFError
        sd = {k: v.contiguous() for k, v in self.hf.state_dict().items() if k != "lm_head.weight"}
        sd["pre_projection.weight"] = torch.zeros(e, 2 * E + 2)
        pfad = os.path.join(self.tmp, "kaputt.safetensors")
        save_file(sd, pfad)
        with self.assertRaises(GGUFError):
            self.modell.hilfskopf_setzen(pfad)
        self.assertIsNone(self.modell.mtp)
        gm, gt = _gguf_namen({k: v for k, v in sd.items() if k != "pre_projection.weight"}
                             | {"pre_projection.weight": self.hf.pre_projection.weight.detach()})
        gm["general.architecture"] = "gemma4"
        anders = os.path.join(self.tmp, "anders.gguf")
        B.schreibe_gguf(anders, gm, gt)
        with self.assertRaises(GGUFError):
            self.modell.hilfskopf_setzen(anders)

    def test_spekulativ_gleich(self):
        from ggufengine.engine import Engine
        from ggufengine.sampling import SamplerConfig
        m = self.modell

        def motor(sp):
            eng = Engine.__new__(Engine)
            eng.model, eng.device, eng.n_ctx, eng.max_tok_s = m, torch.device("cpu"), N_CTX, None
            eng._pos, eng._history, eng._own, eng._keys = 0, [], [], []
            eng._mrope, eng._rope_state = False, (0, 0)
            eng.tokenizer = NS(stop_ids=set())
            eng.spekulativ, eng._vorrat = sp, []
            m.reset()
            return eng
        prompt = torch.randint(3, V, (10,), generator=torch.Generator().manual_seed(4)).tolist()
        cfgs = (SamplerConfig(temperature=0), SamplerConfig(temperature=0.9, top_k=0, top_p=1.0, seed=2))
        refs = [list(motor(0).generate_tokens(prompt, max_tokens=30, sampler=c)) for c in cfgs]
        m.hilfskopf_setzen(self.st)
        aufrufe = []
        orig = m.mtp_vorschlag
        with mock.patch.object(m, "mtp_vorschlag", side_effect=lambda *a: aufrufe.append(a) or orig(*a)):
            for cfg, ref in zip(cfgs, refs):
                eng = motor(8)
                self.assertEqual(list(eng.generate_tokens(prompt, max_tokens=30, sampler=cfg)), ref)
                self.assertGreater(eng._spek_stat[0], 0)
        self.assertTrue(aufrufe)


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class DateiSuche(unittest.TestCase):

    def test_beiwerk_und_fund(self):
        import gguflokal as G
        with tempfile.TemporaryDirectory() as d:
            ordner = Path(d)
            modell = ordner / "gemma-4-E4B-it-Q4_K_M.gguf"
            modell.write_bytes(b"")
            self.assertFalse(G._ist_beiwerk(modell))
            self.assertTrue(G._ist_beiwerk(ordner / "gemma-4-E4B-it-assistant-Q8_0.gguf"))
            self.assertIsNone(G.hilfskopf_datei(modell))
            (ordner / "lora.safetensors").write_bytes(b"kein safetensors")
            unter = ordner / "assistant"
            unter.mkdir()
            save_file({"pre_projection.weight": torch.zeros(2, 4), "post_projection.weight": torch.zeros(2, 2)},
                      str(unter / "model.safetensors"))
            self.assertEqual(G.hilfskopf_datei(modell), unter / "model.safetensors")
            self.assertEqual(G._gguf_in(ordner), modell)


if __name__ == "__main__":
    unittest.main()
