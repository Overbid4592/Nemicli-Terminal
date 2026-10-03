"""Tests für ggufengine ohne Modelldatei (CPU): Ringpuffer der Fenster-Schichten.

python -m unittest tests.test_ggufengine
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
    from ggufengine.models import common as C
except ImportError:                                   # pragma: no cover
    torch = None


@unittest.skipIf(torch is None, "torch fehlt")
class RingpufferTests(unittest.TestCase):
    """Die Aufmerksamkeit über den Ring muss exakt der über den vollen Cache mit
    Fenster-Maske entsprechen – auch über viele Umläufe und beim Neustart."""

    H, HKV, D, WINDOW = 4, 2, 8, 6

    def _vergleich(self, laenge, bloecke, seed=0):
        g = torch.Generator().manual_seed(seed)
        voll = C.KVCache(self.HKV, self.D, laenge, "cpu", torch.float32)
        ring = C.RingKVCache(self.HKV, self.D, self.WINDOW, "cpu", torch.float32)
        pos = 0
        for n in bloecke:
            q = torch.randn(n, self.H, self.D, generator=g)
            k = torch.randn(n, self.HKV, self.D, generator=g)
            v = torch.randn(n, self.HKV, self.D, generator=g)
            idx = torch.arange(pos, pos + n)
            voll.write(k, v, idx)
            ring.write(k, v, idx)
            a = C.attention(q, voll.k, voll.v, idx, pos + n, 1.0, self.WINDOW)
            b = C.attention(q, ring.k, ring.v, idx, pos + n, 1.0, self.WINDOW, key_pos=ring.pos)
            self.assertTrue(torch.allclose(a, b, atol=1e-5), (pos, n))
            pos += n
        return ring

    def test_einzelschritte_ueber_viele_umlaeufe(self):
        self._vergleich(200, [1] * 200)

    def test_bloecke_bis_zur_maximalgroesse(self):
        n = C.PREFILL_CHUNK
        bloecke = [n, 7, n, 1, 1, n, 42]
        self._vergleich(sum(bloecke), bloecke)

    def test_leere_plaetze_am_anfang_zaehlen_nicht(self):
        ring = self._vergleich(3, [1, 1, 1])
        self.assertEqual(int((ring.pos >= 0).sum()), 3)

    def test_ringgroesse(self):
        ring = C.RingKVCache(self.HKV, self.D, self.WINDOW, "cpu", torch.float32)
        self.assertEqual(ring.size, self.WINDOW + C.PREFILL_CHUNK)


@unittest.skipIf(torch is None, "torch fehlt")
class TempoGrenzeTests(unittest.TestCase):
    """max_tok_s: zwischen den Schritten warten, statt die Karte durchlaufen zu lassen."""

    def _engine(self, grenze):
        import time
        from types import SimpleNamespace as NS
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.max_tok_s, e.n_ctx, e._pos, e._history = grenze, 10_000, 0, []
        e.device = torch.device("cpu")
        e.tokenizer = NS(stop_ids=set())

        def feed(tokens, keys=None, own=None):
            e._pos += len(tokens)
            e._history.extend(tokens)
            logits = torch.full((8,), -10.0)
            logits[1] = 10.0
            return logits
        e.feed = feed
        return e, time

    def test_grenze_wird_eingehalten(self):
        from ggufengine.sampling import SamplerConfig
        e, time = self._engine(50)
        t0 = time.perf_counter()
        n = len(list(e.generate_tokens([1], max_tokens=26, sampler=SamplerConfig(temperature=0))))
        dauer = time.perf_counter() - t0
        self.assertEqual(n, 26)
        self.assertGreaterEqual(dauer, 25 / 50 * 0.95)          # 25 Pausen à 1/50 s

    def test_ohne_grenze_keine_pause(self):
        from ggufengine.sampling import SamplerConfig
        e, time = self._engine(None)
        t0 = time.perf_counter()
        list(e.generate_tokens([1], max_tokens=26, sampler=SamplerConfig(temperature=0)))
        self.assertLess(time.perf_counter() - t0, 0.3)


@unittest.skipIf(torch is None, "torch fehlt")
class DenkBudgetTests(unittest.TestCase):
    """Nach dem Budget wird der Denkblock geschlossen und das Modell antwortet (budget forcing)."""

    def _engine(self, folge):
        """Schein-Motor: nach dem Eingeben liegt jeweils das nächste Token aus `folge` vorn;
        ist die Folge leer, „denkt“ er endlos weiter (Token 11)."""
        from types import SimpleNamespace as NS
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.max_tok_s, e.n_ctx, e._pos, e._history = None, 10_000, 0, []
        e.device = torch.device("cpu")
        e.tokenizer = NS(stop_ids={2})
        rest = list(folge)

        def feed(tokens, keys=None, own=None):
            e._pos += len(tokens)
            e._history.extend(tokens)
            if tokens[-1] == 21:                     # nach der Schluss-Marke: Antwort, dann Stopp
                rest[:] = [30, 2]
            logits = torch.full((40,), -10.0)
            logits[rest.pop(0) if rest else 11] = 10.0
            return logits
        e.feed = feed
        return e

    def _lauf(self, e, budget, drin=False):
        from ggufengine.sampling import SamplerConfig
        return list(e.generate_tokens([1], max_tokens=40, sampler=SamplerConfig(temperature=0),
                                      denk_budget=(budget, [10], [20, 21], drin)))

    def test_nach_dem_budget_geschlossen(self):
        self.assertEqual(self._lauf(self._engine([10]), 3), [10, 11, 11, 11, 20, 21, 30])

    def test_prompt_hat_den_block_schon_geoeffnet(self):
        self.assertEqual(self._lauf(self._engine([]), 2, drin=True), [11, 11, 20, 21, 30])

    def test_eigenes_ende_vor_dem_budget(self):
        self.assertEqual(self._lauf(self._engine([10, 11, 20, 21]), 5), [10, 11, 20, 21, 30])


class BildSchluesselTests(unittest.TestCase):
    """Bild-Positionen tragen eigene Schlüssel: gleiche Platzhalter, anderes Bild =
    kein Cache-Treffer."""

    PLATZ = 900

    def _engine(self):
        from types import SimpleNamespace as NS
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.tokenizer = NS(special_id=lambda name: self.PLATZ)
        e.model = NS(supports_images=True, resume_from=lambda c, n: c, arch="gemma4")
        e.gg = NS(architecture="gemma4")
        e._keys, e._history, e._images, e._checkpoint, e._pos = [], [], {}, None, 0
        e._mrope, e.chat_format = False, NS(image_marks=("<a>", "<p>", "<e>"))
        e._own = []
        return e

    def test_schluessel_je_bild_und_zeile(self):
        from ggufengine.chat import Message
        e = self._engine()
        a, b = torch.zeros(2, 4), torch.zeros(1, 4)
        ids = [1, 2, 7, self.PLATZ, self.PLATZ, 8, 7, self.PLATZ, 8, 3]
        keys = e._image_keys([Message("user", "x", images=[a]), Message("user", "y", images=[b])], ids)
        self.assertEqual(keys[:3], [1, 2, 7])
        from ggufengine.engine import bild_nr
        self.assertEqual(keys[3], ("img", bild_nr(a), 0))
        self.assertEqual(keys[4], ("img", bild_nr(a), 1))
        self.assertEqual(keys[7], ("img", bild_nr(b), 0))
        self.assertIs(e._images[keys[4]][0], a)

    def test_anderes_bild_kein_cache_treffer(self):
        from ggufengine.chat import Message
        e = self._engine()
        ids = [1, 2, self.PLATZ, 5, 6]
        a, b = torch.zeros(1, 4), torch.zeros(1, 4)
        e._keys = e._image_keys([Message("user", "x", images=[a])], ids)
        e._history = list(ids)
        neu = e._image_keys([Message("user", "x", images=[b])], ids)
        self.assertEqual(e._resume(neu), 2)            # ab der Bild-Position neu lesen

    def test_neues_bild_mit_alter_kennung_ist_ein_anderes(self):
        """Python vergibt id() freigegebener Objekte neu – der Schlüssel darf daran nicht hängen."""
        from ggufengine.chat import Message
        e = self._engine()
        ids = [1, 2, self.PLATZ, 5]
        alt = torch.zeros(1, 4)
        alte_keys = e._image_keys([Message("user", "x", images=[alt])], ids)
        alte_id = id(alt)
        del alt
        for _ in range(50):                                   # bis ein neues Objekt die Kennung erbt
            neu = torch.ones(1, 4)
            if id(neu) == alte_id:
                break
        neue_keys = e._image_keys([Message("user", "x", images=[neu])], ids)
        self.assertNotEqual(alte_keys[2], neue_keys[2])

    def test_platzhalter_und_bilder_muessen_passen(self):
        from ggufengine.chat import Message
        e = self._engine()
        with self.assertRaises(ValueError):
            e._image_keys([Message("user", "x", images=[torch.zeros(3, 4)])], [self.PLATZ, self.PLATZ])


@unittest.skipIf(torch is None, "torch fehlt")
class BildRasterTests(unittest.TestCase):

    def test_seitenverhaeltnis_und_grenzen(self):
        from ggufengine.vision import Gemma4Vision, IMAGE_TOKENS, MAX_IMAGE_TOKENS
        raster = lambda w, h, n=IMAGE_TOKENS: Gemma4Vision.grid_for(None, w, h, n)
        for w, h in ((800, 600), (1920, 1080), (100, 2000), (5000, 40), (1, 1)):
            nx, ny = raster(w, h)
            self.assertLessEqual(nx * ny, IMAGE_TOKENS, (w, h))
            self.assertGreaterEqual(min(nx, ny), 1)
        nx, ny = raster(1600, 800)
        self.assertAlmostEqual(nx / ny, 2.0, delta=0.2)
        nx, ny = raster(4000, 4000, 99999)
        self.assertLessEqual(nx * ny, MAX_IMAGE_TOKENS)


@unittest.skipIf(torch is None, "torch fehlt")
class MRopeTests(unittest.TestCase):

    def test_bildpositionen_nach_raster(self):
        """Bild mit 2x3 Token ab Position s: (s, s+Zeile, s+Spalte); Text danach ab s+3."""
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        img = torch.zeros(6, 4)
        img.grid = (2, 3)
        keys = [10, 11] + [("img", 1, r) for r in range(6)] + [12]
        e._images = {k: (img, k[2]) for k in keys if isinstance(k, tuple)}
        pos, zustand = e._rope_positions(keys, (0, 0))
        self.assertEqual(pos[:2], [(0, 0, 0), (1, 1, 1)])
        self.assertEqual(pos[2:8], [(2, 2, 2), (2, 2, 3), (2, 2, 4), (2, 3, 2), (2, 3, 3), (2, 3, 4)])
        self.assertEqual(pos[8], (5, 5, 5))
        self.assertEqual(zustand[0], 6)
        # in Stücken gelesen ergibt dasselbe
        a, z = e._rope_positions(keys[:5], (0, 0))
        b, z = e._rope_positions(keys[5:], z)
        self.assertEqual(a + b, pos)

    def test_text_gleich_eindimensionaler_rope(self):
        from ggufengine.models.qwen35 import MRoPE
        from ggufengine.models.common import RoPE
        x = torch.randn(5, 2, 16)
        p = torch.arange(3, 8)
        m = MRoPE(8, 10000.0, [1, 1, 1], "cpu")(x, p.unsqueeze(1).expand(-1, 3))
        r = RoPE(8, 10000.0, "cpu", max_pos=16)(x, p)
        self.assertTrue(torch.allclose(m, r, atol=1e-5))


@unittest.skipIf(torch is None, "torch fehlt")
class ProjektorTests(unittest.TestCase):

    def test_typ_aus_beiden_schluesseln(self):
        from types import SimpleNamespace as NS
        from ggufengine.vision import ENCODERS, projector_type
        kopf = lambda d: NS(get=lambda k, s=None: d.get(k, s))
        self.assertEqual(projector_type(kopf({"clip.vision.projector_type": "gemma4v"})), "gemma4v")
        self.assertEqual(projector_type(kopf({"clip.projector_type": "qwen3vl_merger"})), "qwen3vl_merger")
        self.assertIsNone(projector_type(kopf({})))
        self.assertIn("gemma4v", ENCODERS)
        self.assertIn("qwen3vl_merger", ENCODERS)

    def test_merge_fenster_reihenfolge(self):
        from ggufengine.vision import Qwen3VLVision
        v = Qwen3VLVision.__new__(Qwen3VLVision)
        v.merge = 2
        x = torch.arange(16).view(1, 4, 4)                 # Zeilen 0..3, Spalten 0..3
        self.assertEqual(v._window_order(x).flatten().tolist()[:8], [0, 1, 4, 5, 2, 3, 6, 7])


@unittest.skipIf(torch is None, "torch fehlt")
class K2HorizonTests(unittest.TestCase):

    def test_gruppierte_rmsnorm(self):
        from ggufengine.models.common import group_rms_norm, rms_norm
        x, w = torch.randn(3, 8), torch.randn(8)
        erwartet = torch.cat([rms_norm(x[:, :4], w[:4], 1e-6), rms_norm(x[:, 4:], w[4:], 1e-6)], dim=-1)
        self.assertTrue(torch.allclose(group_rms_norm(x, w, 1e-6, 2), erwartet, atol=1e-6))
        self.assertTrue(torch.allclose(group_rms_norm(x, w, 1e-6, 1), rms_norm(x, w, 1e-6)))

    def test_zahlen_in_dreiergruppen(self):
        from ggufengine.tokenizer import _pretokenize_qwen
        self.assertEqual(_pretokenize_qwen("1234567", True, digits=3), ["123", "456", "7"])
        self.assertEqual(_pretokenize_qwen("1234", True), ["1", "2", "3", "4"])
        self.assertEqual(_pretokenize_qwen("a\u200db", True, joiners=True), ["a\u200db"])


class FloskelBremseTests(unittest.TestCase):
    """DRY: nur eigene frühere Antworten, nie über Trenner, nur am Wortanfang."""

    def setUp(self):
        import numpy as np
        from ggufengine.sampling import SamplerConfig
        self.cfg = SamplerConfig(dry_multiplier=2.5, dry_allowed_length=1)
        self.brk = np.zeros(100, bool)
        self.brk[99] = True                                  # 99 = Zeilenumbruch
        self.wort = np.ones(100, bool)
        self.h = [5, 10, 11, 12, 13, 14, 99, 20, 21, 99, 10, 11, 12]

    def test_eigene_wiederholung_wird_gebremst(self):
        from ggufengine.sampling import dry_penalties
        own = [0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
        strafe = dry_penalties(self.h, own, self.brk, self.cfg, word_start=self.wort)
        self.assertAlmostEqual(strafe[13], 2.5 * 1.75 ** 2)          # 3 gleiche Token davor

    def test_fremder_text_bleibt_frei(self):
        from ggufengine.sampling import dry_penalties
        own = [0] * 10 + [1, 1, 1]
        self.assertEqual(dry_penalties(self.h, own, self.brk, self.cfg, word_start=self.wort), {})

    def test_nicht_mitten_im_wort(self):
        from ggufengine.sampling import dry_penalties
        own = [0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
        wort = self.wort.copy()
        wort[13] = False                                     # 13 setzt ein Wort fort
        self.assertEqual(dry_penalties(self.h, own, self.brk, self.cfg, word_start=wort), {})

    def test_eigene_antworten_im_prompt_markiert(self):
        from ggufengine.chat import Message
        from ggufengine.engine import Engine
        ids = [1, 2, 3, 7, 8, 9, 4, 5, 7, 8]
        own = Engine._own_mask([Message("user", "x"), Message("assistant", "y", ids=[7, 8, 9]),
                                Message("user", "z")], ids)
        self.assertEqual(own, [False] * 3 + [True] * 3 + [False] * 4)


@unittest.skipIf(torch is None, "torch fehlt")
class EinlesenTests(unittest.TestCase):
    """Schnelles Einlesen: blockweise Delta Rule und ausgepackte Int4-Matrizen rechnen
    dasselbe wie die langsamen Wege."""

    def test_delta_blockweise_gleich_der_schleife(self):
        from ggufengine.models.qwen35 import delta_blockweise
        torch.manual_seed(0)
        L, H, Dk, Dv = 150, 4, 16, 8                       # 150: mit aufgefülltem letztem Block
        n = lambda x: x * torch.rsqrt(x.pow(2).sum(-1, keepdim=True) + 1e-6)
        q, k = n(torch.randn(L, H, Dk)) * Dk ** -0.5, n(torch.randn(L, H, Dk))
        v, g, beta = torch.randn(L, H, Dv), -torch.rand(L, H) * 0.5, torch.rand(L, H)
        S1 = torch.randn(H, Dk, Dv) * 0.1
        S2 = S1.clone()
        erwartet = torch.empty(L, H, Dv)
        for t in range(L):
            S1.mul_(g[t].exp().view(H, 1, 1))
            kt = k[t].unsqueeze(1)
            S1.baddbmm_(kt.transpose(1, 2), (v[t].unsqueeze(1) - torch.bmm(kt, S1)) * beta[t].view(H, 1, 1))
            erwartet[t] = torch.bmm(q[t].unsqueeze(1), S1)[:, 0]
        out = delta_blockweise(q, k, v, g, beta, S2, C=64)
        self.assertLess(float((out - erwartet).abs().max()), 1e-5)
        self.assertLess(float((S1 - S2).abs().max()), 1e-5)

    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), "braucht CUDA")
    def test_int4_auspacken_exakt(self):
        from ggufengine.qlinear import _pack, unpack_u4
        for n, k in ((8, 128), (64, 384)):
            u4 = torch.randint(0, 16, (n, k), dtype=torch.uint8, device="cuda")
            self.assertTrue(torch.equal(unpack_u4(_pack(u4), n, k), u4))

    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), "braucht CUDA")
    def test_ausgepackt_rechnet_wie_der_kernel(self):
        from ggufengine.qlinear import G, QuantLinear, _pack, _sz
        torch.manual_seed(1)
        n, k = 64, 256
        u4 = torch.randint(0, 16, (n, k), dtype=torch.uint8, device="cuda")
        s, z = torch.rand(n, k // G, device="cuda") * 0.01, torch.rand(n, k // G, device="cuda") * 0.01
        ql = QuantLinear([(_pack(u4), _sz(s, z))], n, k)
        x = torch.randn(300, k, device="cuda", dtype=torch.bfloat16)
        kern = torch._weight_int4pack_mm(x, ql.mats[0][0], G, ql.mats[0][1]).float()
        aus = (x @ ql.bf16_weight().t()).float()
        self.assertLess(float((kern - aus).norm() / kern.norm()), 1e-2)


@unittest.skipIf(torch is None, "torch fehlt")
class KVWachsenTests(unittest.TestCase):

    def test_waechst_in_schritten_und_behaelt_inhalt(self):
        from ggufengine.models import common as C
        c = C.KVCache(2, 4, 10_000, "cpu", torch.float32)
        self.assertEqual(c.k.shape[1], C.KV_GROWTH)
        c.write(torch.ones(3, 2, 4), torch.full((3, 2, 4), 2.0), torch.tensor([0, 1, 2]))
        self.assertFalse(c.ensure(C.KV_GROWTH))
        self.assertTrue(c.ensure(C.KV_GROWTH + 1))
        self.assertEqual(c.k.shape[1], min(10_000, 2 * C.KV_GROWTH))
        self.assertTrue(torch.equal(c.k[:, :3], torch.ones(2, 3, 4)))
        self.assertTrue(torch.equal(c.v[:, :3], torch.full((2, 3, 4), 2.0)))
        self.assertTrue(c.ensure(9_999))
        self.assertEqual(c.k.shape[1], 10_000)                   # nie über die Grenze
        with self.assertRaises(RuntimeError):
            c.ensure(10_001)


@unittest.skipIf(torch is None, "torch fehlt")
class BildBeidseitigTests(unittest.TestCase):
    """Gemma 4 12B liest die Token eines Bildes in beide Richtungen (llama.cpp: non-causal)."""

    def test_bereiche_aus_zeilen(self):
        from ggufengine.models.gemma4 import _laeufe
        self.assertEqual(_laeufe([2, 3, 4, 8, 9], 100), [(102, 105), (108, 110)])
        self.assertEqual(_laeufe([], 0), [])

    def test_bildtoken_sehen_sich_gegenseitig(self):
        g = torch.Generator().manual_seed(4)
        H, D, S = 2, 8, 6
        q, k, v = (torch.randn(S, H, D, generator=g), torch.randn(H, S, D, generator=g),
                   torch.randn(H, S, D, generator=g))
        pos = torch.arange(S)
        out = C.attention(q, k, v, pos, S, 0.5, spans=[(2, 5)])
        erlaubt = pos.unsqueeze(1) >= pos.unsqueeze(0)
        erlaubt[2:5, 2:5] = True                                      # innerhalb des Bildes beidseitig
        s = torch.einsum("ihd,hjd->hij", q, k) * 0.5
        ref = torch.einsum("hij,hjd->ihd", s.masked_fill(~erlaubt, float("-inf")).softmax(-1), v)
        self.assertTrue(torch.allclose(out, ref, atol=1e-5))
        self.assertFalse(torch.allclose(C.attention(q, k, v, pos, S, 0.5), ref, atol=1e-5))

    def test_bild_wird_nicht_geteilt(self):
        from ggufengine.engine import _bild_nicht_teilen
        keys = [1, 2, ("img", 7, 0), ("img", 7, 1), ("img", 7, 2), 3]
        self.assertEqual(_bild_nicht_teilen(keys, 0, 4), 2)           # Stück endete mitten im Bild
        self.assertEqual(_bild_nicht_teilen(keys, 0, 5), 5)
        self.assertEqual(_bild_nicht_teilen(keys, 0, 2), 2)
        with self.assertRaises(ValueError):
            _bild_nicht_teilen(keys, 2, 4)                            # Bild größer als ein Stück


class AntwortSicherungTests(unittest.TestCase):
    """Wird die letzte Antwort anders dargestellt (Denktext weg), geht es beim Sicherungspunkt
    vor der Antwort weiter – auch bei Modellen, deren Zustand nicht zurückspringen kann."""

    def _engine(self, alt):
        from types import SimpleNamespace as NS
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        self.geladen = []
        e.model = NS(resume_from=lambda c, n: c if c == n else None,        # wie rekurrente Modelle
                     restore_checkpoint=self.geladen.append, reset=lambda: None)
        e._keys, e._history, e._own = list(alt), list(alt), [False] * len(alt)
        e._checkpoint, e._mrope, e._pos = (2, [1, 2], "system"), False, len(alt)
        return e

    def test_weiter_ab_sicherung_vor_der_antwort(self):
        e = self._engine([1, 2, 3, 4, 50, 51, 52])           # 50..52: Antwort mit Denktext
        e._answer_checkpoint = (4, [1, 2, 3, 4], "vor der Antwort")
        self.assertEqual(e._resume([1, 2, 3, 4, 60, 5, 6]), 4)
        self.assertEqual(self.geladen, ["vor der Antwort"])
        self.assertEqual(e._keys, [1, 2, 3, 4])

    def test_frueher_abweichend_nimmt_systempunkt_und_verwirft_die_sicherung(self):
        e = self._engine([1, 2, 3, 4, 50])
        e._answer_checkpoint = (4, [1, 2, 3, 4], "vor der Antwort")
        self.assertEqual(e._resume([1, 2, 9, 9, 9]), 2)
        self.assertEqual(self.geladen, ["system"])
        self.assertIsNone(e._answer_checkpoint)


class DenktextFormatTests(unittest.TestCase):

    def _tok(self):
        from types import SimpleNamespace as NS
        spezial = {"<|im_start|>": 1, "<|im_end|>": 2, "<think>": 3, "</think>": 4}
        return NS(special=spezial, special_id=spezial.__getitem__, bos_id=None,
                  encode=lambda s: [9] * len(s))

    def test_granite4_format(self):
        from ggufengine.chat import ChatFormatter, Message, detect_format
        self.assertEqual(detect_format("<|im_start|> {%- set truncate_history_thinking = True %}", "granite"),
                         "granite4")
        self.assertEqual(detect_format("<|im_start|>", "qwen35"), "chatml")
        f = ChatFormatter(self._tok(), "granite4")
        ids = f.encode([Message("user", "a"), Message("assistant", "b")])
        # [S] "user\n" a [E] \n [S] "assistant\n" <think></think> b [E] \n [S] "assistant\n" <think></think>
        self.assertEqual(ids, [1, 9, 9, 9, 9, 9, 9, 2, 9, 1] + [9] * 10 + [3, 4, 9, 2, 9, 1] + [9] * 10 + [3, 4])
        f.thinking = True
        self.assertEqual(f.encode([Message("user", "a")])[-2:], [3, 9])          # <think>\n: offen
        self.assertTrue(f.opens_thinking)

    def test_welche_vorlagen_denktext_verwerfen(self):
        from ggufengine.chat import ChatFormatter
        for fmt, weg in (("chatml", True), ("granite4", True), ("gemma4", True), ("bailing", False)):
            self.assertEqual(ChatFormatter(self._tok(), fmt).drops_old_thinking, weg, fmt)


@unittest.skipIf(torch is None, "torch fehlt")
class KV8BitTests(unittest.TestCase):

    def _cache(self, n=10_000, d=64):
        return C.KVCache(2, d, n, "cpu", torch.float32, bits=8)

    def test_werte_bleiben_nah_und_halber_speicher(self):
        c = self._cache()
        self.assertTrue(c.int8)
        self.assertEqual(c.k.dtype, torch.int8)
        g = torch.Generator().manual_seed(1)
        k, v = torch.randn(50, 2, 64, generator=g), torch.randn(50, 2, 64, generator=g) * 30
        c.write(k, v, torch.arange(50))
        kk, vv = c.kv(50)
        for echt, zurueck in ((k, kk), (v, vv)):
            echt = echt.transpose(0, 1)
            self.assertLess(((zurueck - echt).norm() / echt.norm()).item(), 0.01)

    def test_wachsen_und_praefix_fuellen(self):
        c = self._cache()
        k = torch.randn(3, 2, 64)
        c.write(k, -k, torch.tensor([0, 1, 2]))
        vorher = [t.clone() for t in c.kv(3)]
        self.assertTrue(c.ensure(C.KV_GROWTH + 1))
        self.assertEqual((c.k.shape[1], c.ks.shape[1]), (2 * C.KV_GROWTH,) * 2)
        for a, b in zip(vorher, c.kv(3)):
            self.assertTrue(torch.equal(a, b))
        neu = self._cache()
        neu.fill(*vorher)
        for a, b in zip(vorher, neu.kv(3)):
            self.assertLess((a - b).abs().max().item(), 1e-6)

    def test_kopfbreite_ohne_32er_gruppen_bleibt_16_bit(self):
        c = self._cache(d=40)
        self.assertFalse(c.int8)
        self.assertEqual(c.k.dtype, torch.float32)


@unittest.skipIf(torch is None, "torch fehlt")
class EinzelschrittAufmerksamkeitTests(unittest.TestCase):

    def test_gleich_der_formel_mit_gqa_und_maske(self):
        g = torch.Generator().manual_seed(2)
        H, HKV, D, S = 8, 2, 16, 40
        k, v = torch.randn(HKV, S, D, generator=g), torch.randn(HKV, S, D, generator=g)
        q = torch.randn(1, H, D, generator=g)
        pos = torch.tensor([29])
        out = C.attention(q, k, v, pos, S, 0.25)
        kh, vh = k[:, :30].repeat_interleave(H // HKV, 0), v[:, :30].repeat_interleave(H // HKV, 0)
        ref = torch.softmax((q[0].unsqueeze(1) @ kh.transpose(1, 2)) * 0.25, -1) @ vh
        self.assertEqual(tuple(out.shape), (1, H, D))
        self.assertTrue(torch.allclose(out[0], ref[:, 0], atol=1e-5))


class ChatFormatTests(unittest.TestCase):

    def _k2_tok(self):
        from types import SimpleNamespace as NS
        spezial = {"<|ifm|im_start|>": 1, "<|ifm|im_end|>": 2, "<ifm|think>": 3, "</ifm|think>": 4}
        return NS(special=spezial, special_id=spezial.__getitem__, bos_id=0,
                  encode=lambda s: [9] * len(s))

    def test_k2_format(self):
        from ggufengine.chat import ChatFormatter, Message, detect_format
        self.assertEqual(detect_format("x '<|ifm|im_start|>' x", "k2-horizon"), "k2")
        f = ChatFormatter(self._k2_tok(), "k2")
        ids = f.encode([Message("user", "a"), Message("assistant", "b", ids=[7])])
        # <bos> [S] "user\na" [E] [S] "assistant\n" <think> \n </think> \n 7 [E] [S] "assistant\n" <think> \n </think> \n
        self.assertEqual(ids, [0, 1] + [9] * 6 + [2, 1] + [9] * 10 + [3, 9, 4, 9, 7, 2, 1] + [9] * 10 + [3, 9, 4, 9])
        self.assertFalse(f.opens_thinking)
        f.thinking = True
        self.assertEqual(f.encode([Message("user", "a")])[-2:], [3, 9])      # Denkblock bleibt offen
        self.assertTrue(f.opens_thinking)


    def test_deepseek_format(self):
        from types import SimpleNamespace as NS
        from ggufengine.chat import ChatFormatter, Message, detect_format
        self.assertEqual(detect_format("{{'<｜User｜>' + x}}", "qwen2"), "deepseek")
        spezial = {"<｜User｜>": 1, "<｜Assistant｜>": 2,
                   "<｜end▁of▁sentence｜>": 3, "</think>": 8}
        tok = NS(special=spezial, special_id=spezial.__getitem__, bos_id=0,
                 encode=lambda s: [9] * len(s))
        ids = ChatFormatter(tok, "deepseek").encode([
            Message("system", "S"), Message("user", "ab"),
            Message("assistant", "x", ids=[7, 7, 8, 5, 5]),     # Denkteil fällt weg
            Message("user", "c")])
        self.assertEqual(ids, [0, 9, 1, 9, 9, 2, 5, 5, 3, 1, 9, 2])

    def test_bildmarken_aus_dem_tokenizer(self):
        from types import SimpleNamespace as NS
        from ggufengine.chat import ChatFormatter, Message, detect_image_marks
        spezial = {"<|im_start|>": 1, "<|im_end|>": 2, "<|vision_start|>": 5,
                   "<|image_pad|>": 6, "<|vision_end|>": 7}
        tok = NS(special=spezial, special_id=spezial.__getitem__, encode=lambda s: [9] * len(s))
        self.assertEqual(detect_image_marks(tok)[1], "<|image_pad|>")
        self.assertIsNone(detect_image_marks(NS(special={})))
        bild = NS(shape=(3, 4))
        ids = ChatFormatter(tok, "chatml").encode([Message("user", "ab", images=[bild])], False)
        self.assertEqual(ids, [1] + [9] * 5 + [5, 6, 6, 6, 7] + [9, 9, 2, 9])

    def test_leerer_denkblock_mit_doppeltem_zeilenumbruch(self):
        """Qwen: zwei Zeilenumbrüche sind ein Token – zwei einzelne lesen sich wie ein
        offener Denkblock."""
        from types import SimpleNamespace as NS
        from ggufengine.chat import ChatFormatter, Message
        spezial = {"<|im_start|>": 1, "<|im_end|>": 2, "<think>": 3, "</think>": 4}
        nl = chr(10)

        def kodieren(s):
            if s == nl * 2:
                return [271]
            return [198] * s.count(nl) + [9] * len(s.replace(nl, ""))
        tok = NS(special=spezial, special_id=spezial.__getitem__, encode=kodieren)
        ids = ChatFormatter(tok, "chatml", thinking=False).encode([Message("user", "x")])
        self.assertEqual(ids[-4:], [3, 271, 4, 271])


if __name__ == "__main__":
    unittest.main()
