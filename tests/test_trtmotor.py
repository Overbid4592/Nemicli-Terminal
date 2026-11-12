"""trtmotor: TensorRT liest den Prompt, der GGUF-Motor schreibt weiter – die Logits müssen zum reinen
GGUF-Motor passen. Je Familie ein winziges Zufallsmodell (nichts wird geladen), als GGUF geschrieben.
Läuft nur mit Grafikkarte und abgelegtem TensorRT-RTX.

python -m unittest tests.test_trtmotor
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests", "core"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
    from trtmotor import chat as C, laufzeit as L
    BEREIT = torch.cuda.is_available() and L.bereit()
except Exception:                                   # ohne torch/transformers/TensorRT: überspringen
    torch = T = C = L = None
    BEREIT = False

V, E, H, HKV, D, FF, N_CTX = 96, 256, 4, 2, 64, 384, 64
N_PROMPT, N_WEITER = 24, 5


def _klein(cls, **kw):
    basis = dict(vocab_size=V, hidden_size=E, intermediate_size=FF, num_hidden_layers=2,
                 num_attention_heads=H, num_key_value_heads=HKV, head_dim=D, max_position_embeddings=64,
                 rms_norm_eps=1e-6, tie_word_embeddings=False, attn_implementation="eager",
                 pad_token_id=0, bos_token_id=1, eos_token_id=2)
    basis.update(kw)
    return cls(**basis)


@unittest.skipUnless(BEREIT, "Grafikkarte oder TensorRT-RTX fehlt")
class SchnellLesen(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(L, "engine_ordner", lambda: Path(self.tmp.name) / "engines")
        p.start()
        self.addCleanup(p.stop)

    def _gguf(self, cfg, modell_cls, arch, meta=None, tensoren=None) -> str:
        import gguf_testbau as B
        torch.manual_seed(0)
        hf = modell_cls(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        m, t = B.umwandeln(hf, arch)
        m.update(meta or {})
        t.update(tensoren or {})
        pfad = str(Path(self.tmp.name) / f"{arch}.gguf")
        B.schreibe_gguf(pfad, m, t)
        return pfad

    @staticmethod
    def _engine(modell):
        def raum(n):
            for l in modell.layers:
                l.cache.ensure(n)
        tok = SimpleNamespace(encode=lambda text: [int(x) for x in torch.randint(3, V, (40,))])
        return SimpleNamespace(model=modell, tokenizer=tok, _kv_room=raum, offloaded=False)

    def _laeufe(self, pfad, ids, fmt=None):
        """Logits nach dem Prompt und für jedes weitere Token (fmt=None: nur GGUF-Motor)."""
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        gg = GGUFFile(pfad)
        try:
            modell = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
            engine = self._engine(modell)
            if fmt is not None:
                self.assertEqual("", C.anbinden(engine, pfad, fmt, warten=True))
                self.assertIsNotNone(modell.schnell)
                modell.schnell.ab = 1
                vorher = modell.schnell.laeufe
            engine._kv_room(len(ids))
            aus = [modell.forward(ids[:N_PROMPT], 0).float().cpu()]
            if fmt is not None:
                self.assertEqual(vorher + 1, modell.schnell.laeufe, "TensorRT hat den Prompt nicht gelesen")
            for j in range(N_PROMPT, len(ids)):
                aus.append(modell.forward([ids[j]], j).float().cpu())
            return aus
        finally:
            gg.close()

    def _vergleich(self, cfg, modell_cls, arch, meta=None, tensoren=None, formate=("bf16", "fp4")):
        pfad = self._gguf(cfg, modell_cls, arch, meta, tensoren)
        ids = [int(x) for x in torch.randint(3, V, (N_PROMPT + N_WEITER,))]
        ref = self._laeufe(pfad, ids)
        for fmt in formate:
            aus = self._laeufe(pfad, ids, fmt)
            for j, (a, r) in enumerate(zip(aus, ref)):
                if fmt == "bf16":
                    grenze = 5e-2 * max(1.0, float(r.abs().max()))
                    self.assertLess(float((a - r).abs().max()), grenze, f"{arch}/{fmt}: Schritt {j}")
                else:                   # 4/8-Bit-Rauschen; Zufallsgewichte (0,2) verstärken es Schicht für Schicht
                    cos = float(torch.nn.functional.cosine_similarity(a.view(1, -1), r.view(1, -1)))
                    self.assertGreater(cos, 0.97 if fmt == "fp8" else 0.85, f"{arch}/{fmt}: Schritt {j}")

    # -- unterstützte Familien
    def test_llama(self):
        self._vergleich(_klein(T.LlamaConfig), T.LlamaForCausalLM, "llama", formate=("bf16", "fp8", "fp4"))

    def test_qwen2_bias(self):
        self._vergleich(_klein(T.Qwen2Config), T.Qwen2ForCausalLM, "qwen2")

    def test_qwen3_qk_norm(self):
        self._vergleich(_klein(T.Qwen3Config), T.Qwen3ForCausalLM, "qwen3")

    def test_granite_skalen(self):
        cfg = _klein(T.GraniteConfig, embedding_multiplier=3.0, attention_multiplier=0.2,
                     residual_multiplier=0.5, logits_scaling=4.0)
        self._vergleich(cfg, T.GraniteForCausalLM, "granite",
                        meta={"granite.embedding_scale": 3.0, "granite.attention.scale": 0.2,
                              "granite.residual_scale": 0.5, "granite.logit_scale": 4.0})

    def test_gemma(self):
        # Einbettung mal sqrt(E): Rundungsrauschen wächst bei Zufallsgewichten zu stark – Aufbau über bf16
        self._vergleich(_klein(T.GemmaConfig, hidden_activation="gelu_pytorch_tanh"), T.GemmaForCausalLM, "gemma",
                        formate=("bf16",))

    def test_olmo2_nach_normen(self):
        self._vergleich(_klein(T.Olmo2Config), T.Olmo2ForCausalLM, "olmo2")

    def test_smollm3_ohne_rope_schichten(self):
        cfg = _klein(T.SmolLM3Config, num_hidden_layers=4, no_rope_layer_interval=2, rope_theta=5e6,
                     tie_word_embeddings=True)
        self._vergleich(cfg, T.SmolLM3ForCausalLM, "smollm3")

    def test_phi3_gate_up_teilrotation(self):
        rot = int(D * 0.75)
        kurz, lang = [1.0 + 0.1 * i for i in range(rot // 2)], [2.0 + 0.3 * i for i in range(rot // 2)]
        cfg = _klein(T.Phi3Config, partial_rotary_factor=0.75, max_position_embeddings=256,
                     original_max_position_embeddings=64,
                     rope_scaling={"type": "longrope", "short_factor": kurz, "long_factor": lang})
        self._vergleich(cfg, T.Phi3ForCausalLM, "phi3", meta={"phi3.rope.scaling.original_context_length": 64},
                        tensoren={"rope_factors_short.weight": torch.tensor(kurz),
                                  "rope_factors_long.weight": torch.tensor(lang)})

    def test_mistral3_yarn_temperatur(self):
        cfg = _klein(T.MistralConfig, rope_theta=1e6,
                     rope_scaling={"rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 8,
                                   "beta_fast": 32.0, "beta_slow": 1.0, "mscale": 1.0, "mscale_all_dim": 0.5})
        self._vergleich(cfg, T.MistralForCausalLM, "mistral3",
                        meta={"mistral3.rope.scaling.yarn_log_multiplier": 0.5,
                              "mistral3.attention.temperature_scale": 0.1})

    # -- abgelehnt, es rechnet der GGUF-Motor
    def test_moe_und_softcap_bleiben_beim_gguf_motor(self):
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        faelle = [(_klein(T.MixtralConfig, num_local_experts=4, num_experts_per_tok=2), T.MixtralForCausalLM,
                   "llama", None, "Mixture-of-Experts"),
                  (_klein(T.Gemma2Config, num_hidden_layers=2, query_pre_attn_scalar=D, attn_logit_softcapping=2.0,
                          sliding_window=4096), T.Gemma2ForCausalLM, "gemma2",
                   {"gemma2.attn_logit_softcapping": 2.0}, "Softcap")]
        for cfg, cls, arch, meta, grund in faelle:
            pfad = self._gguf(cfg, cls, arch, meta)
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
                antwort = C.anbinden(self._engine(modell), pfad, "bf16", warten=True)
                self.assertIn(grund, antwort)
                self.assertIsNone(modell.schnell)
            finally:
                gg.close()

    def test_lora_liest_nicht_geparkt_streamt(self):
        pfad = self._gguf(_klein(T.LlamaConfig), T.LlamaForCausalLM, "llama")
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        gg = GGUFFile(pfad)
        try:
            modell = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
            engine = self._engine(modell)
            C.anbinden(engine, pfad, "bf16", warten=True)
            leser = modell.schnell
            engine._kv_room(N_PROMPT)
            modell._nemicli_coding_lora = "test.lora|1.0"
            self.assertIsNone(leser(modell, list(range(3, 3 + N_PROMPT)), 0))
            modell._nemicli_coding_lora = ""
            voll = leser(modell, list(range(3, 3 + N_PROMPT)), 0)
            self.assertGreater(leser.netz.budget, 0)              # Gewichte im VRAM
            leser.parken()
            self.assertEqual(0, leser.netz.budget)
            leser.netz.neu_bemessen = False                       # geparkt rechnen: alles über PCIe
            geparkt = leser(modell, list(range(3, 3 + N_PROMPT)), 0)
            self.assertLess(float((geparkt - voll).abs().max()), 1e-3)
            leser.aufwecken()
            leser(modell, list(range(3, 3 + N_PROMPT)), 0)
            vorher = leser.netz.budget
            self.assertGreater(vorher, 0)
            self.assertIsNone(leser.netz._puffer)                 # Arbeitsspeicher nach dem Lesen zurück
            # VRAM wird knapp: erst der Arbeitsspeicher, das Budget schrumpft (mehr Streaming)
            echt = torch.cuda.mem_get_info
            knapp = lambda *a: (C.RESERVE + 1, echt()[1])
            with patch.object(torch.cuda, "mem_get_info", knapp):
                knapp_aus = leser(modell, list(range(3, 3 + N_PROMPT)), 0)
            self.assertLess(leser.netz.budget, vorher)
            self.assertLess(float((knapp_aus - voll).abs().max()), 1e-3)
        finally:
            gg.close()

    def test_wechselnde_stuecke_beim_streaming(self):
        """Stücke verschiedener Länge hintereinander, Gewichte teils gestreamt, Arbeitsspeicher je Lauf neu:
        der Kontext darf keine alte Speicheradresse behalten (sonst „invalid argument“ / Müll)."""
        pfad = self._gguf(_klein(T.LlamaConfig), T.LlamaForCausalLM, "llama")
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        gg = GGUFFile(pfad)
        try:
            ref = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
            modell = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
            engine = self._engine(modell)
            C.anbinden(engine, pfad, "bf16", warten=True)
            leser = modell.schnell
            leser.ab = 1
            leser.netz.reserve = None
            leser.netz.budget_setzen(leser.netz.engine.streamable_weights_size // 2)
            engine._kv_room(N_CTX)
            self._engine(ref)._kv_room(N_CTX)
            ids = [int(x) for x in torch.randint(3, V, (40,))]
            pos = 0
            for n in (17, 9, 13):
                a, r = modell.forward(ids[pos:pos + n], pos), ref.forward(ids[pos:pos + n], pos)
                self.assertTrue(bool(torch.isfinite(a).all()))
                self.assertLess(float((a.float() - r.float()).abs().max()), 5e-2 * max(1.0, float(r.abs().max())))
                pos += n
            self.assertEqual(3, leser.laeufe)
        finally:
            gg.close()

    def test_ungueltige_werte_schalten_den_leser_ab(self):
        pfad = self._gguf(_klein(T.LlamaConfig), T.LlamaForCausalLM, "llama")
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        from ggufengine.sampling import Sampler, SamplerConfig
        gg = GGUFFile(pfad)
        try:
            ref = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
            modell = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
            engine = self._engine(modell)
            C.anbinden(engine, pfad, "bf16", warten=True)
            modell.schnell.ab = 1
            echt = L.Netz.__call__

            def kaputt(netz, adr, formen):
                echt(netz, adr, formen)
                adr["aus"].fill_(float("nan"))
            engine._kv_room(N_CTX)
            self._engine(ref)._kv_room(N_CTX)
            ids = [int(x) for x in torch.randint(3, V, (N_PROMPT,))]
            with patch.object(L.Netz, "__call__", kaputt):
                a = modell.forward(ids, 0)
            r = ref.forward(ids, 0)
            self.assertIsNone(modell.schnell)                     # abgeschaltet, der GGUF-Motor las
            self.assertLess(float((a.float() - r.float()).abs().max()), 1e-3)
        finally:
            gg.close()
        smp = Sampler(SamplerConfig(), torch.device("cuda"))
        with self.assertRaises(RuntimeError):
            smp.sample(torch.full((V,), float("nan"), device="cuda"), [], None, None, None)

    def test_gespeichertes_netz_braucht_kein_einmessen(self):
        pfad = self._gguf(_klein(T.LlamaConfig), T.LlamaForCausalLM, "llama")
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        for runde in (1, 2):
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device("cuda"), torch.bfloat16, N_CTX, quant=False)
                with patch.object(C, "einmessen", wraps=C.einmessen) as mess:
                    C.anbinden(self._engine(modell), pfad, "fp4", warten=True)
                self.assertEqual(1 if runde == 1 else 0, mess.call_count, f"Runde {runde}")
            finally:
                gg.close()
        self.assertEqual(1, len(list((Path(self.tmp.name) / "engines").glob("chat-*.engine"))))


if __name__ == "__main__":
    unittest.main()
