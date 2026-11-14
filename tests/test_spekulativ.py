"""Spekulatives Dekodieren: gleiche Ausgabe wie ohne Vorschläge, gleicher Modellzustand danach."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None


class NGramTests(unittest.TestCase):
    def test_fortsetzung_der_letzten_stelle(self):
        from ggufengine.spekulativ import NGramEntwurf
        e = NGramEntwurf([1, 2, 3, 4, 5, 9, 1, 2, 3, 7, 8, 0, 1, 2], n=3, max_neu=4)
        self.assertEqual(e.vorschlag(10), [])             # (0, 1, 2) kam noch nicht vor
        e.zufuegen(3)
        self.assertEqual(e.vorschlag(10), [7, 8, 0, 1])   # jüngste Stelle von (1, 2, 3)
        self.assertEqual(e.vorschlag(2), [7, 8])          # Platz im Kontext begrenzt

    def test_laufend_wie_neu_gebaut(self):
        import random

        from ggufengine.spekulativ import NGramEntwurf
        rnd = random.Random(1)
        folge = [rnd.randrange(5) for _ in range(300)]
        e = NGramEntwurf(folge[:20])
        for i, t in enumerate(folge[20:], 21):
            e.zufuegen(t)
            self.assertEqual(e.vorschlag(8), NGramEntwurf(folge[:i]).vorschlag(8))

    def test_fenster_bleibt_begrenzt(self):
        """Rolling N-gram index must agree with a freshly built index for the last window."""
        import random

        from ggufengine.spekulativ import NGramEntwurf
        fenster = 24
        rnd = random.Random(42)
        folge = [rnd.randrange(7) for _ in range(600)]
        e = NGramEntwurf(folge[:10], fenster=fenster)
        for i, tid in enumerate(folge[10:], 11):
            e.zufuegen(tid)
            ref = NGramEntwurf(folge[:i], fenster=fenster)
            self.assertEqual(e.vorschlag(8), ref.vorschlag(8))
            self.assertLessEqual(len(e._toks), fenster + min(1024, fenster))
            self.assertLessEqual(len(e._index), fenster + min(1024, fenster))

    def test_nutzen(self):
        from ggufengine.spekulativ import Nutzen
        P = Nutzen.PRUEFUNGEN

        def pruefung(n, k, sekunden, tokens):
            n.start(k)
            n.pruefung(sekunden, tokens)
            return n.pause()

        def runde(n, zeit_je_laenge, tokens=1):
            """Bis jede Länge P-mal geprüft ist; gibt die Pausen zurück."""
            pausen = []
            for _ in range(4 * P):
                k = n.erlaubt(8)
                if not k:
                    break
                p = pruefung(n, k, zeit_je_laenge[k], tokens)
                if p:
                    pausen.append(p)
                    break
            return pausen
        n = Nutzen()
        self.assertEqual([pruefung(n, 8, 0.01, 3) for _ in range(P)][-1], Nutzen.MESSEN)   # erst messen
        n = Nutzen()
        n.einzeln(0.010)                                     # Einzelschritt 10 ms
        self.assertEqual(runde(n, {8: 0.020, 4: 0.015, 3: 0.020, 2: 0.020, 1: 0.025}, 5), [])   # 8er treffen 4 von 8
        self.assertEqual(n.erlaubt(8), 8)                    # lohnt und trifft gut: kürzere nicht probiert
        self.assertEqual(n._je[4][1], 0)
        for _ in range(4):                                   # 8er werden teuer: 30 ms je Token
            pruefung(n, 8, 0.030, 1)
        self.assertEqual(n.erlaubt(8), 4)                    # jetzt wird 4 probiert
        pausen = []
        for _ in range(3):                                   # keine Länge lohnt: Pause, verdoppelt
            pausen += runde(n, {8: 0.030, 4: 0.030, 3: 0.030, 2: 0.030, 1: 0.030})
            self.assertEqual(n.erlaubt(8), 8)                # nach der Pause alles von vorn
        self.assertEqual(pausen, [Nutzen.PAUSE, 2 * Nutzen.PAUSE, 4 * Nutzen.PAUSE])
        runde(n, {8: 0.015, 4: 0.015, 3: 0.015, 2: 0.015, 1: 0.015}, 3)   # lohnt wieder: Pause von vorn
        self.assertEqual(runde(n, {8: 0.030, 4: 0.030, 3: 0.030, 2: 0.030, 1: 0.030}), [Nutzen.PAUSE])

    def test_nutzen_trennt_angenommene_von_nachberechneten_tokens(self):
        """A rejected proposal can require one extra model token, but is still a miss."""
        from ggufengine.spekulativ import Nutzen

        n = Nutzen()
        n.einzeln(0.010)
        n.start(1)
        n.pruefung(0.012, 1)     # original sampled token
        n.pruefung(0.020, 1)     # replacement sampled after a rejected proposal
        n.pause(angenommen=0)
        self.assertEqual(n._je[1][2], 0.0)

        n.start(3)
        n.pruefung(0.014, 4)     # one original, two accepted, one replacement
        n.pause(angenommen=2)
        self.assertAlmostEqual(n._je[3][2], 2 / 3)

    def test_nutzen_waehlt_die_schnellste_laenge(self):
        """MTP schlägt 2 Token vor: probiert werden 2 und 1, genommen wird die schnellere."""
        from ggufengine.spekulativ import Nutzen
        n = Nutzen()
        n.einzeln(0.020)
        gesehen = []
        for _ in range(2 * Nutzen.PRUEFUNGEN):
            k = n.erlaubt(2)
            gesehen.append(k)
            n.start(k)
            n.pruefung(0.016 if k == 2 else 0.027, 1 if k == 2 else 2)   # 2er treffen nichts: 16 ms/Token, 1er 13,5
            n.pause()
        self.assertEqual(gesehen, [2] * Nutzen.PRUEFUNGEN + [1] * Nutzen.PRUEFUNGEN)
        self.assertEqual(n.erlaubt(2), 1)
        self.assertEqual(n.erlaubt(8), 8)                    # längere Vorschläge: 8 und 4 erst probieren

    def test_nutzen_pause_bei_kurzen_vorschlaegen(self):
        """Kommen nur kurze Vorschläge (2 Token) und lohnen 2 und 1 nicht, kommt die Pause –
        ohne auf Längen zu warten, die nie vorgeschlagen werden."""
        from ggufengine.spekulativ import Nutzen
        n = Nutzen()
        n.einzeln(0.010)
        n.erlaubt(8)                                         # früher einmal ein langer Vorschlag
        pausen = []
        for _ in range(4 * Nutzen.PRUEFUNGEN):
            k = n.erlaubt(2)
            n.start(k)
            n.pruefung(0.030, 1)
            if (p := n.pause()):
                pausen.append(p)
                break
        self.assertEqual(pausen, [Nutzen.PAUSE])

    def test_nutzen_ohne_aufnahme(self):
        """Eine Prüfung, in der ein CUDA-Graph aufgenommen wurde, zählt nicht."""
        from ggufengine.spekulativ import Nutzen
        n = Nutzen()
        n.einzeln(0.010)
        n.start(8)
        n.pruefung(1.0, 9)                                   # eine Sekunde Aufnahme
        n.ungueltig()
        n.pause()
        self.assertEqual(n._je[8][1], 0)
        n.start(8)
        n.pruefung(0.018, 9)
        n.pause()
        self.assertEqual(n._je[8][:2], [0.002, 1])

    def test_mtp_wenn_keine_wiederholung(self):
        from ggufengine.spekulativ import MtpEntwurf
        aufrufe = []
        modell = NS(mtp_vorschlag=lambda tok, pos, n: aufrufe.append((tok, pos, n)) or [7] * n)
        e = MtpEntwurf([5, 6, 1, 2, 3, 4, 9, 1, 2, 3], modell, lambda: 10)
        self.assertEqual(e.vorschlag(8), [4, 9, 1, 2, 3])        # Wiederholung: n-Gramm
        e.zufuegen(8)
        self.assertEqual(e.vorschlag(8), [7, 7, 7])              # sonst der MTP-Kopf (bis zu 3)
        self.assertEqual(aufrufe, [(8, 10, 3)])
        e.ergebnis(0)                                            # falscher MTP-Vorschlag: keine Pause
        self.assertEqual(e.vorschlag(1), [7])

    def test_pause_nach_fehlschlag(self):
        from ggufengine.spekulativ import NGramEntwurf
        e = NGramEntwurf([1, 2, 3, 4, 1, 2, 3])
        self.assertEqual(e.vorschlag(8), [4, 1, 2, 3])
        e.ergebnis(0)                                # kein Token davon war richtig
        self.assertEqual([e.vorschlag(8) for _ in range(4)], [[]] * 4)
        self.assertEqual(e.vorschlag(8), [4, 1, 2, 3])


def _ohne_zeitmessung():
    """Zeitmessung, die jede Vorschlagslänge erlaubt und nie pausiert."""
    from ggufengine.spekulativ import Nutzen

    class OhneZeitmessung(Nutzen):
        def erlaubt(self, laenge=0):
            return 8

        def pause(self, angenommen=None):
            return 0
    return OhneZeitmessung()


class _Orakel:
    """Schlägt die Fortsetzung aus einem Referenzlauf vor, bei jedem dritten Mal mit einem
    falschen zweiten Token – so laufen Annehmen und Verwerfen beide."""

    def __init__(self, tokens, ref, vocab):
        self.toks, self.ref, self.vocab, self.n, self.fehl = list(tokens), ref, vocab, 0, 0

    def zufuegen(self, tid):
        self.toks.append(tid)

    def vorschlag(self, platz):
        v = list(self.ref[len(self.toks):len(self.toks) + min(5, platz)])
        self.n += 1
        if self.n % 3 == 0 and len(v) > 1:
            v[1] = (v[1] + 1) % self.vocab
        return v

    def ergebnis(self, angenommen):
        self.fehl += angenommen < self.n

    def pausieren(self, tokens):
        pass


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class GleicheAusgabe(unittest.TestCase):
    N_CTX = 160
    GERAET = "cpu"
    umbau = None                          # optional: verändert das Modell vor den Läufen

    def _pruefen(self, hf, arch):
        import gc
        import os
        import tempfile

        import gguf_testbau as B

        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        torch.manual_seed(0)
        hf = hf.eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.3)
        m, t = B.umwandeln(hf, arch)
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, m, t)
            gg = GGUFFile(pfad)
            try:
                self._laeufe(self._umbauen(build_model(gg, torch.device(self.GERAET), torch.float32, self.N_CTX,
                                                       quant=False)), arch)
            finally:
                gc.collect()
                gg.close()
        finally:
            os.unlink(pfad)

    def _umbauen(self, modell):
        if self.umbau is not None:
            self.umbau(modell)
        self._nachgeholt = 0
        f = getattr(modell, "zeilen_nachholen", None)
        if f is not None:
            def zaehlen(L, n, f=f):
                ok = f(L, n)
                self._nachgeholt += ok
                return ok
            modell.zeilen_nachholen = zaehlen
        return modell

    def _motor(self, modell, spekulativ):
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.model, e.device, e.n_ctx, e.max_tok_s = modell, torch.device(self.GERAET), self.N_CTX, None
        e._pos, e._history, e._own, e._keys = 0, [], [], []
        e._mrope, e._rope_state = bool(getattr(modell, "uses_mrope", False)), (0, 0)
        e.tokenizer = NS(stop_ids=set())
        e.spekulativ, e._vorrat = spekulativ, []
        e._nutzen = _ohne_zeitmessung()            # Gleichheit prüfen, nicht Tempo: Vorschläge nie gekürzt
        modell.reset()
        return e

    def _laeufe(self, modell, arch):
        from ggufengine.sampling import SamplerConfig
        g = torch.Generator().manual_seed(5)
        stueck = torch.randint(3, 96, (10,), generator=g).tolist()
        prompt = stueck * 3 + stueck[:4]
        for name, cfg in (("gierig", SamplerConfig(temperature=0)),
                          ("Zufall", SamplerConfig(temperature=0.9, top_k=0, top_p=1.0, repeat_penalty=1.1, seed=7))):
            with self.subTest(arch=arch, sampler=name):
                ref_motor = self._motor(modell, 0)
                ref = list(ref_motor.generate_tokens(prompt, max_tokens=40, sampler=cfg))
                weiter_ref = ref_motor.feed([5]).clone()
                # n-Gramm-Vorschläge
                e = self._motor(modell, 8)
                self.assertEqual(list(e.generate_tokens(prompt, max_tokens=40, sampler=cfg)), ref)
                # Vorschläge aus der Referenz, teils falsch
                e = self._motor(modell, 8)
                orakel = _Orakel(prompt, prompt + ref, 96)
                e._entwurf = lambda o=orakel: o
                self.assertEqual(list(e.generate_tokens(prompt, max_tokens=40, sampler=cfg)), ref)
                self.assertGreater(e._spek_stat[1], 10)
                self.assertGreater(orakel.fehl, 0)
                if e._rekurrent():                       # Zustand nachgeholt, nicht neu gelesen
                    self.assertGreater(self._nachgeholt, 0)
                self.assertEqual(e._pos, len(prompt) + 40)
                self.assertEqual(e._history, prompt + ref)
                # Zustand danach wie ohne Vorschläge (verworfene Vorschläge hinterlassen nichts)
                torch.testing.assert_close(e.feed([5]), weiter_ref, rtol=1e-4, atol=1e-4)
                # Abbruch, während noch Vorschläge offen sind
                e = self._motor(modell, 8)
                orakel = _Orakel(prompt, prompt + ref, 96)
                e._entwurf = lambda o=orakel: o
                gen = e.generate_tokens(prompt, max_tokens=40, sampler=cfg)
                k = 0
                for k, _ in enumerate(gen, 1):
                    if k >= 10 and e._vorrat:
                        break
                self.assertTrue(e._vorrat)
                gen.close()
                self.assertEqual((e._pos, e._vorrat), (len(prompt) + k - 1, []))
                danach = e.feed([5]).clone()
                vgl = self._motor(modell, 0)                  # dasselbe Modell, neu angefangen
                vgl.feed(prompt + ref[:k - 1])
                torch.testing.assert_close(danach, vgl.feed([5]), rtol=1e-4, atol=1e-4)
                # Denk-Budget: nach 6 Denk-Token wird der Block zwangsweise geschlossen – auch wenn
                # gerade Vorschläge offen sind
                offen = 0
                frei = [x for x in range(3, 96) if x not in ref and x not in prompt]
                zu = frei[-2:]                               # Ende-Marken, die das Modell nicht selbst schreibt
                for grenze in range(5, 13):
                    budget = (grenze, [prompt[0]], zu, True)
                    ref_motor = self._motor(modell, 0)
                    ref_d = list(ref_motor.generate_tokens(prompt, max_tokens=30, sampler=cfg, denk_budget=budget))
                    weiter_d = ref_motor.feed([5]).clone()
                    self.assertIn(zu[0], ref_d)
                    e = self._motor(modell, 8)
                    orakel = _Orakel(prompt, prompt + ref_d, 96)
                    e._entwurf = lambda o=orakel: o
                    verwerfen = e._verwerfen

                    def mitzaehlen(v=verwerfen, e=e):
                        nonlocal offen
                        offen += bool(e._vorrat) and zu[0] not in e._history
                        v()
                    e._verwerfen = mitzaehlen
                    aus = list(e.generate_tokens(prompt, max_tokens=30, sampler=cfg, denk_budget=budget))
                    self.assertEqual(aus, ref_d, grenze)
                    torch.testing.assert_close(e.feed([5]), weiter_d, rtol=1e-4, atol=1e-4)
                self.assertGreater(offen, 0)                 # mindestens einmal mit offenen Vorschlägen geschlossen

    def test_attention(self):
        cfg = T.LlamaConfig(vocab_size=96, hidden_size=64, intermediate_size=96, num_hidden_layers=2,
                            num_attention_heads=4, num_key_value_heads=2, head_dim=16, max_position_embeddings=256,
                            rms_norm_eps=1e-6, tie_word_embeddings=False)
        self._pruefen(T.LlamaForCausalLM(cfg), "llama")

    def test_rekurrent_qwen35(self):
        from test_neue_bauarten import _q35_cfg
        self._pruefen(T.Qwen3_5ForCausalLM(_q35_cfg(T.Qwen3_5TextConfig, max_position_embeddings=256)), "qwen35")

    def test_hybrid_lfm2(self):
        from test_neue_bauarten import Lfm2
        cfg = dict(Lfm2.BASIS, max_position_embeddings=256)
        self._pruefen(T.Lfm2ForCausalLM(T.Lfm2Config(**cfg)), "lfm2")


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class Nachholen(unittest.TestCase):
    """Nach einer Prüfung von 5 Token und Zurücksetzen auf n behaltene Token baut jede Schicht
    mit Zustand ihn aus den gemerkten Zeilen nach – das nächste Token kommt heraus, als hätte
    das Modell nur die n Token gelesen."""

    def _pruefen(self, hf, arch):
        def lauf(modell, _arch):
            from ggufengine.models.common import ZeilenMerken
            ids = torch.randint(3, 96, (20,), generator=torch.Generator().manual_seed(9)).tolist()
            modell.reset()
            with torch.no_grad():
                modell.forward(ids[:12], 0)
                vorher = modell.snapshot_state()
                with ZeilenMerken():
                    modell.alle_logits = True
                    modell.forward(ids[12:17], 12)
                    modell.alle_logits = False
                for n in (1, 2, 4):
                    with self.subTest(arch=arch, behalten=n):
                        modell.restore_state(vorher)
                        self.assertTrue(modell.zeilen_nachholen(5, n))
                        a = modell.forward([ids[12 + n]], 12 + n).clone()
                        modell.restore_state(vorher)
                        modell.forward(ids[12:12 + n], 12)
                        b = modell.forward([ids[12 + n]], 12 + n)
                        torch.testing.assert_close(a, b, rtol=1e-4, atol=1e-4)
        g = GleicheAusgabe()
        g._laeufe = lauf
        g.umbau = None
        g._pruefen(hf, arch)

    def test_jedes_modell_mit_zustand_holt_nach(self):
        """Jede Bauart, deren Zustand nicht zurückspringen kann, baut ihn nach einer Prüfung nach."""
        from ggufengine.models import _MODELS
        mit_zustand = {"Qwen35Model", "HybridModel", "BailingMoe3Model", "DeepSeek2Model"}
        for cls in _MODELS:
            if cls.__name__ in mit_zustand:
                self.assertTrue(hasattr(cls, "zeilen_nachholen"), cls.__name__)

    def test_deltanet_qwen35(self):
        from test_neue_bauarten import _q35_cfg
        self._pruefen(T.Qwen3_5ForCausalLM(_q35_cfg(T.Qwen3_5TextConfig, max_position_embeddings=256)), "qwen35")

    def test_mamba2_granite4h(self):
        from test_neue_bauarten import MambaHybride
        self._pruefen(MambaHybride()._granite(num_local_experts=0, position_embedding_type="rope"), "granitehybrid")

    def test_kurzfaltung_lfm2(self):
        from test_neue_bauarten import Lfm2
        self._pruefen(T.Lfm2ForCausalLM(T.Lfm2Config(**dict(Lfm2.BASIS, max_position_embeddings=256))), "lfm2")

    def test_kda_kimi(self):
        self._pruefen(T.KimiLinearForCausalLM(T.KimiLinearConfig(
            vocab_size=96, hidden_size=64, num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=4,
            intermediate_size=96, max_position_embeddings=256, pad_token_id=0, bos_token_id=1, eos_token_id=2,
            layer_types=["linear_attention", "full_attention", "linear_attention", "linear_attention"],
            mlp_layer_types=["dense", "dense", "dense", "dense"], linear_num_heads=4, linear_head_dim=16,
            kv_lora_rank=32, qk_nope_head_dim=16, qk_rope_head_dim=8, v_head_dim=16, rms_norm_eps=1e-6)),
            "kimi-linear")

    def test_kda_ling(self):
        """Ling 3.0 (bailingmoe3) hat kein Mini-Modell in transformers: die KDA-Schicht allein."""
        from ggufengine.models import bailing3 as B
        from ggufengine.models.common import Linear, ZeilenMerken
        g = torch.Generator().manual_seed(4)
        H, D, E, K = 2, 8, 32, 4
        n = H * D
        t = {f"blk.0.{x}.weight": torch.randn(n, E, generator=g) * 0.3
             for x in ("attn_q", "attn_k", "attn_v", "ssm_f_a", "ssm_g_a")}
        t["blk.0.attn_output.weight"] = torch.randn(E, n, generator=g) * 0.3
        t["blk.0.ssm_beta.weight"] = torch.randn(H, E, generator=g)
        t["blk.0.ssm_a"] = torch.rand(H, generator=g)
        t["blk.0.ssm_dt.bias"] = torch.randn(n, generator=g)
        for x in "qkv":
            t[f"blk.0.ssm_conv1d_{x}.weight"] = torch.randn(n, K, generator=g)
        t["blk.0.ssm_norm.weight"] = torch.rand(D, generator=g) + 0.5
        ld = NS(linear=lambda name: Linear(t[name]), norm=lambda name: t[name])
        schicht = B._KDALayer(ld, 0, {"n_heads": H, "kda_dim": D, "conv_kernel": K, "eps": 1e-6, "kda_lower": -5.0})
        x = torch.randn(12, E, generator=g)
        with torch.no_grad():
            schicht(x[:6], None, 0)
            vorher = schicht.snapshot()
            with ZeilenMerken():
                schicht(x[6:11], None, 0)
            for behalten in (1, 3, 5):
                schicht.restore(vorher)
                schicht.nachholen(5, behalten)
                a = schicht(x[6 + behalten:7 + behalten], None, 0)
                nachher = [z.clone() for z in schicht.snapshot()]
                schicht.restore(vorher)
                schicht(x[6:6 + behalten], None, 0)
                b = schicht(x[6 + behalten:7 + behalten], None, 0)
                torch.testing.assert_close(a, b, rtol=1e-5, atol=1e-5)
                for u, w in zip(nachher, schicht.snapshot()):
                    torch.testing.assert_close(u, w, rtol=1e-5, atol=1e-5)


@unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
class GleicheAusgabeGrafikkarte(GleicheAusgabe):
    """Dieselben Läufe mit CUDA-Graphen (ein Token und mehrere Token je Prüfung)."""
    GERAET = "cuda"

    def tearDown(self):
        self.umbau = None

    def test_vor_graph_vor_jedem_abspielen(self):
        """Füllt ein Modell vor dem Rechnen Puffer auf dem Host (Gemma 4: Per-Layer-Zeilen aus dem
        RAM), muss der Graph für mehrere Token das vor jedem Abspielen anstoßen."""
        from ggufengine.models.common import GraphDecoder

        class Modell:
            device = torch.device("cuda")
            alle_logits = False

            def __init__(self):
                self.puffer = torch.zeros(8, device="cuda")

            def vor_graph(self, tokens):
                self.puffer[:len(tokens)].copy_(torch.tensor(tokens, dtype=torch.float32) * 2)

            def snapshot_state(self):
                return None

            def restore_state(self, s):
                pass

            def _run(self, tok, pos_idx, kv_len):
                return (self.puffer[:tok.numel()] + 0 * tok).unsqueeze(1).repeat(1, 3)

        g = GraphDecoder(Modell(), 64)
        self.assertEqual(g.mehrere([1, 2, 3], 0)[:, 0].tolist(), [2, 4, 6])
        self.assertEqual(g.mehrere([5, 6, 7], 0)[:, 0].tolist(), [10, 12, 14])   # derselbe Graph, neue Zeilen

    def test_neuer_mehrfach_bereich_nimmt_alles_neu_auf(self):
        """Wechselt der Bereich der Graphen für mehrere Token, geht deren Speicher-Topf mit ihnen – und mit
        ihm jeder Ein-Token-Graph, der während seiner Zeit aufgenommen wurde und hineinzeigen kann."""
        from ggufengine.models.common import MEHRERE_BUCKET, GraphDecoder

        class Modell:
            device = torch.device("cuda")
            alle_logits = False

            def snapshot_state(self):
                return None

            def restore_state(self, s):
                pass

            def _run(self, tok, pos_idx, kv_len):
                return (tok.float() * 2 + pos_idx.float()).unsqueeze(1).repeat(1, 3)

        g = GraphDecoder(Modell(), 4 * MEHRERE_BUCKET)
        self.assertEqual(g.mehrere([1, 2], 0)[:, 0].tolist(), [2, 5])
        self.assertEqual(g(3, 10)[0, 0].item(), 16)
        alt = g.pool_mehr
        self.assertEqual(len(g.graphs), 2)
        pos = MEHRERE_BUCKET + 5                                 # nächster Bereich
        self.assertEqual(g.mehrere([1, 2], pos)[:, 0].tolist(), [2 + pos, 5 + pos])
        self.assertEqual(list(g.graphs), [(2, 2 * MEHRERE_BUCKET)])
        self.assertIsNot(g.pool_mehr, alt)
        self.assertEqual(g(3, 10)[0, 0].item(), 16)              # neu aufgenommen, gleiches Ergebnis

    def test_aufnahme_scheitert(self):
        """Lässt sich ein Modell mit mehreren Token nicht als Graph aufnehmen (Host-Lesen in der
        Aufnahme), rechnet die Prüfung ohne Graph weiter – gleiche Ausgabe, kein Absturz."""
        from ggufengine.models.common import mehrere_aufnahme
        modelle = []

        def umbau(modell):
            alt = modell._run

            def _run(tok, pos_idx, kv_len, *a, **k):
                if tok.numel() > 1 and mehrere_aufnahme():
                    int(tok[0])                          # Host-Lesen: in einem Graph verboten
                return alt(tok, pos_idx, kv_len, *a, **k)
            modell._run = _run
            modelle.append(modell)
        self.umbau = umbau
        self.test_attention()
        self.assertTrue(modelle[0].graph.mehrere_aus)
        self.assertTrue(any(isinstance(k, int) for k in modelle[0].graph.graphs))   # Einzel-Graphen laufen


if __name__ == "__main__":
    unittest.main()
