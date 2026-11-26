"""Prüfung des GGUF-Motors: Auslagern ganz oder gar nicht, Kernel-Cache mit Prüfsumme, Grenzen der
Ein-Token-Kerne, Stopp-Tokens, Masken und Bauarten-Sonderfälle.

python -m unittest tests.test_tuev_motor
"""

import gc
import hashlib
import math
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None

CUDA = torch is not None and torch.cuda.is_available()


def _motor(modell, device="cuda"):
    from ggufengine.engine import Engine
    e = Engine.__new__(Engine)
    e.device = torch.device(device)
    e.model = modell
    return e


def _gguf(meta: dict) -> str:
    import gguf_testbau as B
    fd, pfad = tempfile.mkstemp(suffix=".gguf")
    os.close(fd)
    B.schreibe_gguf(pfad, meta, {})
    return pfad


# ---------------------------------------------------------------------------------------------
# G-01 / G-04: Auslagern und Zurückholen
# ---------------------------------------------------------------------------------------------

@unittest.skipIf(torch is None, "torch fehlt")
class UmziehenTests(unittest.TestCase):
    def test_scheitert_eine_kopie_bleibt_alles_wie_es_war(self):
        from ggufengine.engine import _umziehen

        class Modell:
            def __init__(self):
                self.a = torch.randn(64)
                self.b = torch.randn(32)
                self.sicht = self.a[8:16]
                self.liste = [torch.randn(4)]
        m = Modell()
        vorher = {k: v for k, v in vars(m).items()}
        zahl = [0]

        def kopie(ganz):
            zahl[0] += 1
            if zahl[0] == 2:
                raise MemoryError("test")
            return ganz.clone()
        with self.assertRaises(MemoryError):
            _umziehen((m,), lambda t: True, kopie)
        for k in ("a", "b", "sicht"):
            self.assertIs(getattr(m, k), vorher[k], k)
        self.assertIs(m.liste[0], vorher["liste"][0])

    def test_gelingt_es_bleiben_ansichten_ansichten(self):
        from ggufengine.engine import _umziehen

        class Modell:
            def __init__(self):
                self.a = torch.randn(64)
                self.sicht = self.a[8:16]
        m = Modell()
        soll = m.sicht.clone()
        neu = _umziehen((m,), lambda t: True, lambda ganz: ganz.clone())
        self.assertEqual(len(neu), 2)
        self.assertEqual(m.a.untyped_storage().data_ptr(), m.sicht.untyped_storage().data_ptr())
        self.assertTrue(torch.equal(m.sicht, soll))


@unittest.skipUnless(CUDA, "braucht CUDA")
class AuslagernTests(unittest.TestCase):
    class _Modell:
        def __init__(self):
            self.a = torch.randn(256, 256, device="cuda")
            self.b = torch.randn(1024, device="cuda")
            self.c = torch.randn(512, device="cuda").to(torch.bfloat16)
            self.sicht = self.a[10:20]

    def tearDown(self):
        from ggufengine import hostmem
        gc.collect()
        hostmem.flush()

    def test_restore_mit_vollem_vram_bleibt_ausgelagert_und_klappt_danach(self):
        e = _motor(self._Modell())
        soll = {k: v.clone() for k, v in vars(e.model).items()}
        e.offload()
        echt = torch.Tensor.to
        zahl = [0]

        def to(t, *a, **k):
            if t.device.type == "cpu" and a and isinstance(a[0], torch.device) and a[0].type == "cuda":
                zahl[0] += 1
                if zahl[0] == 2:
                    raise torch.cuda.OutOfMemoryError("test")
            return echt(t, *a, **k)
        with mock.patch.object(torch.Tensor, "to", to):
            with self.assertRaises(torch.cuda.OutOfMemoryError):
                e.restore()
        self.assertTrue(e.offloaded)
        self.assertTrue(all(v.device.type == "cpu" for v in vars(e.model).values()))
        e.restore()                                                  # zweiter Versuch holt alles
        self.assertFalse(e.offloaded)
        m = e.model
        for k, v in vars(m).items():
            self.assertTrue(v.is_cuda, k)
            self.assertTrue(torch.equal(v, soll[k]), k)
        self.assertEqual(m.a.untyped_storage().data_ptr(), m.sicht.untyped_storage().data_ptr())

    def test_offload_ohne_ram_bleibt_auf_der_gpu(self):
        from ggufengine import engine as E
        e = _motor(self._Modell())
        vorher = dict(vars(e.model))
        zahl = [0]

        def ram_reicht(n):
            zahl[0] += 1
            if zahl[0] == 2:
                raise MemoryError("test")
            return True
        with mock.patch.object(E.hostmem, "ram_reicht", ram_reicht):
            with self.assertRaises(MemoryError):
                e.offload()
        self.assertFalse(e.offloaded)
        for k, v in vars(e.model).items():
            self.assertIs(v, vorher[k], k)
        e.offload()
        self.assertTrue(e.offloaded)
        e.restore()
        self.assertTrue(e.model.a.is_cuda)


# ---------------------------------------------------------------------------------------------
# G-11 / G-12 / G-13 / G-14: Engine
# ---------------------------------------------------------------------------------------------

@unittest.skipIf(torch is None, "torch fehlt")
class EngineTests(unittest.TestCase):
    def test_fehler_beim_laden_schliesst_die_datei(self):
        from ggufengine.engine import Engine
        from ggufengine.gguf import GGUFError
        pfad = _gguf({"general.architecture": "llama"})               # ohne Tokenizer
        try:
            with self.assertRaises(GGUFError):
                Engine(pfad, device="cpu", verbose=False)
        finally:
            os.remove(pfad)                                           # unter Windows nur ohne offenen Griff

    def test_complete_entfernt(self):
        from ggufengine.engine import Engine
        self.assertFalse(hasattr(Engine, "complete"))

    def test_abbruch_vor_der_antwort_setzt_statistik_zurueck(self):
        e = _motor(types.SimpleNamespace(), "cpu")
        e.chat_format = types.SimpleNamespace(encode=lambda m, add_generation_prompt: [1, 2, 3, 4, 5])
        e._image_keys = lambda m, ids: list(ids)
        e._own_mask = lambda m, ids: [False] * len(ids)
        e._resume = lambda keys: 0
        e._system_end = lambda m, ids: 3
        e.feed = lambda *a, **k: None                                 # Einlesen abgebrochen: nichts gelesen
        e._history = []
        e.last_stats = {"generated": 99}
        self.assertEqual(list(e.chat([], abbruch=lambda: True)), [])
        self.assertEqual(e.last_stats["generated"], 0)
        self.assertEqual(e.last_ids, [])

    def test_leeren_gibt_tabellen_nur_ohne_graphen_frei(self):
        from ggufengine import engine as E
        for aktiv, aufrufe in ((False, 1), (True, 0)):
            e = _motor(types.SimpleNamespace(w=torch.ones(3)), "cpu")
            with mock.patch.object(E, "caches_leeren") as leeren, \
                    mock.patch.object(E, "graphen_aktiv", lambda: aktiv):
                e.leeren()
            self.assertEqual(leeren.call_count, aufrufe)

    def test_caches_leeren(self):
        from ggufengine import qlinear, quant
        quant._table("IQ4_NL", torch.device("cpu"), torch.float32)
        self.assertTrue(quant._TABLES)
        qlinear.caches_leeren()
        self.assertFalse(quant._TABLES or quant._U8 or quant._PLAENE or qlinear._TILE_ORDER)

    def test_graphen_aktiv(self):
        import weakref
        from ggufengine.models import common as C
        halter = type("Halter", (), {})()
        halter.graphs = {1: "g"}
        with mock.patch.object(C, "GRAPH_HALTER", weakref.WeakSet()):
            self.assertFalse(C.graphen_aktiv())
            C.GRAPH_HALTER.add(halter)
            self.assertTrue(C.graphen_aktiv())
            halter.graphs = {}                                        # geleert: zählt nicht mehr
            self.assertFalse(C.graphen_aktiv())
            ohne = type("Ohne", (), {})()                             # ohne `graphs`: zählt, solange es lebt
            C.GRAPH_HALTER.add(ohne)
            self.assertTrue(C.graphen_aktiv())
            del ohne
            gc.collect()
            self.assertFalse(C.graphen_aktiv())


# ---------------------------------------------------------------------------------------------
# G-03 / G-09 / M-04: Log, Strom-Dekoder, Stopp-Tokens
# ---------------------------------------------------------------------------------------------

class LogTests(unittest.TestCase):
    def test_schreibfehler_schaltet_das_log_ab(self):
        from ggufengine import debuglog as dbg
        alt = dbg._path
        with tempfile.TemporaryDirectory() as d:
            try:
                dbg._path = os.path.join(d, "fehlt", "log.md")          # Ordner gibt es nicht
                dbg.event("x")
                self.assertIsNone(dbg.path())
                dbg.event("y")                                          # danach still
            finally:
                dbg._path = alt


class _GG:
    def __init__(self, meta):
        self.m = meta

    def get(self, k, d=None):
        return self.m.get(k, d)

    def require(self, k):
        return self.m[k]


class TokenizerTests(unittest.TestCase):
    def _tok(self, **extra):
        from ggufengine.tokenizer import Tokenizer
        meta = {"tokenizer.ggml.model": "gpt2",
                "tokenizer.ggml.tokens": ["a", "<|endoftext|>", "<|role_end|>", "<|eom_id|>", "x", "y"],
                "tokenizer.ggml.token_type": [1, 3, 3, 3, 1, 1],
                "tokenizer.ggml.eos_token_id": 1}
        meta.update(extra)
        return Tokenizer(_GG(meta))

    def test_eot_eom_und_namen(self):
        t = self._tok(**{"tokenizer.ggml.eot_token_id": 4, "tokenizer.ggml.eom_token_id": 5})
        self.assertEqual(t.stop_ids, {1, 2, 3, 4, 5})

    def test_falsche_eot_angabe_bricht_nicht_ab(self):
        t = self._tok(**{"tokenizer.ggml.eot_token_id": 99})
        self.assertEqual(t.stop_ids, {1, 2, 3})

    def test_angefangenes_zeichen_vor_sichtbarer_marke(self):
        from ggufengine.tokenizer import StreamDecoder

        class Tok:
            tokens = ["<think>", "x", "<|ende|>"]

            def is_control(self, t):
                return t in (0, 2)

            def token_bytes(self, t):
                return b"\xc3"
        d = StreamDecoder(Tok())
        self.assertEqual(d.push(1), "")
        self.assertEqual(d.push(2), "")                               # unsichtbare Marke: Puffer bleibt
        self.assertEqual(d.push(0), "�<think>")
        self.assertEqual(d.flush(), "")


# ---------------------------------------------------------------------------------------------
# G-15 / G-05: Kernel-Cache
# ---------------------------------------------------------------------------------------------

@unittest.skipIf(torch is None, "torch fehlt")
class KernelCacheTests(unittest.TestCase):
    def test_pruefsumme(self):
        from ggufengine import cudakern as ck
        with tempfile.TemporaryDirectory() as d:
            datei = os.path.join(d, "k.cubin")
            Path(datei).write_bytes(b"abc")
            buf, ohne = ck._cache_lesen(datei)
            self.assertEqual((buf.raw, ohne), (b"abc", True))        # älterer Cache ohne Summe
            ck._summe_schreiben(datei, b"abc")
            buf, ohne = ck._cache_lesen(datei)
            self.assertEqual((buf.raw, ohne), (b"abc", False))
            Path(datei + ".sha256").write_text("0" * 64, encoding="ascii")
            self.assertEqual(ck._cache_lesen(datei), (None, False))    # Abweichung: neu übersetzen
            self.assertEqual(ck._cache_lesen(os.path.join(d, "fehlt.cubin")), (None, False))

    def _bauen(self, d, laden, uebersetzen):
        from ggufengine import cudakern as ck
        pfad = os.path.join(d, "nvrtc64_120_0.dll")
        with mock.patch.object(ck, "_nvrtc_pfad", lambda: pfad), \
                mock.patch.object(ck, "_cache_ordner", lambda: d), \
                mock.patch.object(ck.torch.cuda, "get_device_capability", lambda dev: (12, 0)):
            return ck.bauen(torch.device("cuda", 0), "QUELLE", laden, uebersetzen, "Test")

    def _datei(self, d):
        arch = "--gpu-architecture=sm_120"
        schluessel = hashlib.sha256(("QUELLE" + arch + "nvrtc64_120_0.dll" + str(torch.version.cuda)).encode())
        return os.path.join(d, schluessel.hexdigest() + ".cubin")

    def test_alter_cache_bekommt_summe(self):
        with tempfile.TemporaryDirectory() as d:
            datei = self._datei(d)
            Path(datei).write_bytes(b"bild")
            neu = []
            self.assertEqual({"ok": 1}, self._bauen(d, lambda dev, c: {"ok": 1}, lambda *a: neu.append(a)))
            self.assertEqual(neu, [])
            self.assertEqual(Path(datei + ".sha256").read_text(encoding="ascii"), hashlib.sha256(b"bild").hexdigest())

    def test_abweichende_summe_wird_neu_uebersetzt(self):
        with tempfile.TemporaryDirectory() as d:
            datei = self._datei(d)
            Path(datei).write_bytes(b"bild")
            Path(datei + ".sha256").write_text("f" * 64, encoding="ascii")
            geladen = []

            def laden(dev, cubin):
                geladen.append(bytes(cubin))
                return {"ok": 1}
            self._bauen(d, laden, lambda p, q, a, ziel: b"frisch")
            self.assertEqual(geladen, [b"frisch"])

    @unittest.skipUnless(CUDA, "braucht CUDA")
    def test_mmvq_nutzt_den_cache(self):
        from ggufengine import cudakern as ck
        from ggufengine import mmvq
        if ck._nvrtc_pfad() is None:
            self.skipTest("NVRTC fehlt")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(ck, "_cache_ordner", lambda: d):
                erst = mmvq._uebersetzen(torch.device("cuda", 0))
                dateien = sorted(os.listdir(d))
                self.assertEqual(len(dateien), 2, dateien)
                cubin = os.path.join(d, next(n for n in dateien if n.endswith(".cubin")))
                self.assertEqual(Path(cubin + ".sha256").read_text(encoding="ascii"),
                                 hashlib.sha256(Path(cubin).read_bytes()).hexdigest())
                with mock.patch.object(ck, "_nvrtc_uebersetzen", side_effect=AssertionError("neu übersetzt")):
                    zweit = mmvq._uebersetzen(torch.device("cuda", 0))
            self.assertEqual(set(erst), set(zweit))


# ---------------------------------------------------------------------------------------------
# G-02 / G-06 / G-07 / G-08: Kerne und Ersatzwege
# ---------------------------------------------------------------------------------------------

@unittest.skipIf(torch is None, "torch fehlt")
class KernTests(unittest.TestCase):
    def test_eingang_nur_ganze_warps(self):
        from ggufengine import mmvq
        with self.assertRaises(ValueError):
            mmvq.Eingang(n=40, device="cpu")
        with self.assertRaises(ValueError):
            mmvq.Eingang(torch.zeros(33))

    def test_ersatzweg_einmal_gemeldet(self):
        from ggufengine.models import common as C
        C._ERSATZ_GEMELDET.discard(("Q5_K", 1 << 20))
        with mock.patch.object(C.cudakern, "zu_lang", lambda dev, f, n: True), \
                mock.patch.object(C.dbg, "event") as ereignis:
            C._ersatzweg_melden(torch.device("cuda"), "Q5_K", 1 << 20)
            C._ersatzweg_melden(torch.device("cuda"), "Q5_K", 1 << 20)
        self.assertEqual(ereignis.call_count, 1)
        self.assertIn("Q5_K", ereignis.call_args[0][0])

    def test_zu_lang(self):
        from ggufengine import cudakern as ck
        fns = {"_max_n_in": 1024}
        with mock.patch.object(ck, "_funktionen", lambda dev: fns):
            fmt = next(iter(ck.FORMATE))
            bs = ck.FORMATE[fmt][0]
            self.assertTrue(ck.zu_lang("cuda", fmt, 1024 + bs))
            self.assertFalse(ck.zu_lang("cuda", fmt, 1024))
            self.assertFalse(ck.zu_lang("cpu", fmt, 1024 + bs))

    @unittest.skipUnless(CUDA, "braucht CUDA")
    def test_ringplaetze_ausgerichtet(self):
        from ggufengine.models.common import Fliessband
        fb = Fliessband(torch.device("cuda"))
        fb.anmelden(torch.zeros(1000, dtype=torch.uint8))
        fb.anmelden(torch.zeros(300, dtype=torch.uint8))
        fb._anlegen()
        self.assertEqual(fb._ring.shape[1], 1024)

    def _mmvq(self):
        from ggufengine import mmvq
        if not CUDA or mmvq._funktionen(torch.device("cuda")) is None:
            self.skipTest("MMVQ-Kerne fehlen")
        return mmvq

    def test_rope_kv_schreibt_nicht_hinter_den_cache(self):
        mmvq = self._mmvq()
        H = Hkv = 1
        D, cap = 64, 4
        puffer = torch.zeros(2 * Hkv * cap * D + 2 * D, dtype=torch.bfloat16, device="cuda")
        kc = puffer[:Hkv * cap * D].view(Hkv, cap, D)
        vc = puffer[Hkv * cap * D + D:2 * Hkv * cap * D + D].view(Hkv, cap, D)
        q, k, v = (torch.ones(n * D, device="cuda") for n in (H, Hkv, Hkv))
        pos = torch.tensor([cap], dtype=torch.long, device="cuda")
        mmvq.rope_kv(q, k, v, (None, None, None), None, pos, (None, None), 1e-6, None, kc, vc, H, Hkv, D)
        torch.cuda.synchronize()
        self.assertEqual(int(puffer.count_nonzero()), 0)
        pos.fill_(cap - 1)                                            # gültiger Platz: geschrieben
        mmvq.rope_kv(q, k, v, (None, None, None), None, pos, (None, None), 1e-6, None, kc, vc, H, Hkv, D)
        self.assertEqual(float(kc[0, cap - 1].float().sum()), D)

    def test_attention1_liest_nicht_hinter_den_cache(self):
        mmvq = self._mmvq()
        H, Hkv, D, cap = 2, 1, 64, 40
        g = torch.Generator().manual_seed(0)
        k = torch.randn(Hkv, cap, D, generator=g).to(torch.bfloat16)
        v = torch.randn(Hkv, cap, D, generator=g).to(torch.bfloat16)
        q = torch.randn(H * D, generator=g)
        # hinter dem Cache große Werte: wer sie liest, verfälscht das Ergebnis deutlich
        kb = torch.full((Hkv * cap * D + 64 * D,), 50.0, dtype=torch.bfloat16)
        vb = kb.clone()
        kb[:Hkv * cap * D] = k.reshape(-1)
        vb[:Hkv * cap * D] = v.reshape(-1)
        kb, vb = kb.cuda(), vb.cuda()
        kc, vc = kb[:Hkv * cap * D].view(Hkv, cap, D), vb[:Hkv * cap * D].view(Hkv, cap, D)
        pos = torch.tensor([cap + 10], dtype=torch.long, device="cuda")
        scale = D ** -0.5
        out, _ = mmvq.attention1(q.cuda(), kc, vc, pos, cap, scale, 0, None, H, Hkv, D)
        s = (q.view(H, D) @ k[0].float().T) * scale                     # (H, cap)
        soll = (torch.softmax(s, -1) @ v[0].float()).reshape(-1)
        self.assertLess(float((out.cpu() - soll).abs().max()), 2e-2)


# ---------------------------------------------------------------------------------------------
# M-02: CUDA-Graphs
# ---------------------------------------------------------------------------------------------

@unittest.skipUnless(CUDA, "braucht CUDA")
class GraphTests(unittest.TestCase):
    def test_gescheiterte_aufnahme_faellt_zurueck(self):
        from ggufengine.models.common import GraphDecoder

        class Modell:
            device = torch.device("cuda")

            def _run(self, tok, pos, kv_len):
                return tok.float() * 2 + pos.float()
        g = GraphDecoder(Modell(), 64)
        vorher = torch.cuda.current_stream()
        anderer = torch.cuda.Stream()

        def kaputt(kv_len, L=1):
            torch.cuda.set_stream(anderer)                            # wie eine abgebrochene Aufnahme
            raise RuntimeError("operation not permitted when stream is capturing")
        try:
            with mock.patch.object(g, "_capture", kaputt):
                self.assertEqual(float(g(3, 5)), 11.0)
                self.assertTrue(g.einzeln_aus)
                self.assertEqual(torch.cuda.current_stream().cuda_stream, vorher.cuda_stream)
                self.assertEqual(float(g(4, 6)), 14.0)                # ohne neuen Versuch
        finally:
            torch.cuda.set_stream(vorher)

    def test_gepackte_experten_im_ram_ohne_kernel_nicht_graphfaehig(self):
        from ggufengine.gguf import GGML_TYPES
        from ggufengine.models import common as C
        from ggufengine.models.moe import _GepacktTeil
        typ = next(i for i, v in GGML_TYPES.items() if v[0] == "IQ4_NL")
        raw = torch.zeros(4 * 32 * 18, dtype=torch.uint8)
        with mock.patch.object(C.cudakern, "verfuegbar", lambda *a: False), \
                mock.patch.object(C.cudakern, "zu_lang", lambda *a: False):
            im_ram = C.PackedExperts(raw.pin_memory(), typ, 4, 32, 32, torch.bfloat16, geraet="cuda")
            im_vram = C.PackedExperts(raw.cuda(), typ, 4, 32, 32, torch.bfloat16)
        self.assertFalse(_GepacktTeil(im_ram).graphfaehig)
        self.assertTrue(_GepacktTeil(im_vram).graphfaehig)
        self.assertTrue(_GepacktTeil(C.PackedExperts(raw, typ, 4, 32, 32, torch.float32)).graphfaehig)


# ---------------------------------------------------------------------------------------------
# M-03: Masken und Sinks
# ---------------------------------------------------------------------------------------------

def _referenz(q, k, v, q_pos, key_pos, scale, window=0, sinks=None):
    L, H, _ = q.shape
    G = H // k.shape[0]
    out = torch.zeros(L, H, v.shape[-1])
    for l in range(L):
        for h in range(H):
            s = (k[h // G] @ q[l, h]) * scale
            m = (key_pos >= 0) & (key_pos <= q_pos[l])
            if window > 0:
                m &= key_pos > q_pos[l] - window
            s = s.masked_fill(~m, float("-inf"))
            if sinks is not None:
                s = torch.cat([s, sinks[h].view(1)])
            p = torch.softmax(s, -1)[:k.shape[1]]
            out[l, h] = p @ v[h // G]
    return out


@unittest.skipIf(torch is None, "torch fehlt")
class MaskenTests(unittest.TestCase):
    def setUp(self):
        g = torch.Generator().manual_seed(1)
        self.L, self.H, self.Hkv, self.D, self.S = 12, 4, 2, 8, 20
        self.q = torch.randn(self.L, self.H, self.D, generator=g)
        self.k = torch.randn(self.Hkv, self.S, self.D, generator=g)
        self.v = torch.randn(self.Hkv, self.S, self.D, generator=g)
        self.q_pos = torch.arange(self.S - self.L, self.S)

    def test_fenster_voller_cache(self):
        from ggufengine.models.common import attention
        o = attention(self.q, self.k, self.v, self.q_pos, self.S, 0.3, window=5)
        soll = _referenz(self.q, self.k, self.v, self.q_pos, torch.arange(self.S), 0.3, 5)
        self.assertLess(float((o.float() - soll).abs().max()), 1e-4)

    def test_fenster_ring(self):
        from ggufengine.models.common import attention
        key_pos = torch.arange(self.S)
        key_pos[:3] = -1                                              # leere Plätze
        o = attention(self.q, self.k, self.v, self.q_pos, self.S, 0.3, window=6, key_pos=key_pos)
        soll = _referenz(self.q, self.k, self.v, self.q_pos, key_pos, 0.3, 6)
        self.assertLess(float((o.float() - soll).abs().max()), 1e-4)

    def test_sinks_in_kleinen_bloecken_gleich(self):
        from ggufengine.models import common as C
        sinks = torch.randn(self.H)
        soll = _referenz(self.q, self.k, self.v, self.q_pos, torch.arange(self.S), 0.3, 0, sinks)
        for budget in (C.SENKEN_BUDGET, 1):                            # 1: eine Zeile je Block
            with mock.patch.object(C, "SENKEN_BUDGET", budget):
                o = C.attention(self.q, self.k, self.v, self.q_pos, self.S, 0.3, sinks=sinks)
            self.assertLess(float((o.float() - soll).abs().max()), 1e-4, budget)


# ---------------------------------------------------------------------------------------------
# M-01 / M-05 / M-06 / M-08 / M-09 / M-10 / M-11: Bauarten
# ---------------------------------------------------------------------------------------------

class _Ld:
    device = torch.device("cpu") if torch is not None else None
    dtype = torch.float32 if torch is not None else None
    kv_bits = 16

    def __init__(self, namen):
        self.namen = set(namen)

    def has(self, n):
        return n in self.namen

    def linear(self, n, b=None):
        return types.SimpleNamespace(n_out=2)

    def norm(self, n):
        return torch.ones(8)


@unittest.skipIf(torch is None, "torch fehlt")
class BauartTests(unittest.TestCase):
    def test_post_attention_norm_vor_dem_ffn(self):
        from ggufengine import ausfuellen
        for arch in ("gpt-oss", "glm4moe", "seed_oss"):
            self.assertTrue(ausfuellen.bauart(arch).get("ffn_norm_heisst_post_attn"), arch)
        self.assertFalse(ausfuellen.bauart("glm4").get("ffn_norm_heisst_post_attn"))

    def test_command_r_plus_qk_norm_je_kopf(self):
        try:
            import transformers as T
            import test_baukasten as TB
        except Exception:
            self.skipTest("transformers fehlt")
        cfg = TB._klein(T.CohereConfig, logit_scale=0.5, use_qk_norm=True, tie_word_embeddings=True)
        TB.BaukastenGegenTransformers._vergleich(self, cfg, T.CohereForCausalLM, "command-r")

    def test_yarn_ohne_original_laenge(self):
        from ggufengine.models import common as C
        from ggufengine.models.deepseek2 import DeepSeek2Model
        meta = {"deepseek2.rope.scaling.type": "yarn", "deepseek2.rope.scaling.factor": 4.0,
                "deepseek2.context_length": 4096}
        gesehen = []
        echt = C._yarn_inv_freq

        def yarn(inv, dim, base, factor, orig_ctx, *a):
            gesehen.append(orig_ctx)
            return echt(inv, dim, base, factor, orig_ctx, *a)
        with mock.patch.object(C, "_yarn_inv_freq", yarn):
            _, mscale = DeepSeek2Model._rope(_GG(meta), lambda s: "deepseek2." + s, 16, 32, torch.device("cpu"))
        self.assertEqual(gesehen, [4096])
        self.assertEqual(mscale, 1.0)                                 # ohne yarn_log_multiplier wie llama.cpp
        meta["deepseek2.rope.scaling.yarn_log_multiplier"] = 0.1
        with mock.patch.object(C, "_yarn_inv_freq", yarn):
            _, mscale = DeepSeek2Model._rope(_GG(meta), lambda s: "deepseek2." + s, 16, 32, torch.device("cpu"))
        self.assertAlmostEqual(mscale, 1 + 0.1 * math.log(4.0))

    def test_baukasten_yarn_ohne_original_laenge(self):
        from ggufengine.models import common as C
        from ggufengine.models.baukasten import BaukastenModel
        meta = {"qwen3.rope.scaling.type": "yarn", "qwen3.rope.scaling.factor": 4.0, "qwen3.context_length": 2048}
        gesehen = []
        echt = C._yarn_inv_freq

        def yarn(inv, dim, base, factor, orig_ctx, *a):
            gesehen.append(orig_ctx)
            return echt(inv, dim, base, factor, orig_ctx, *a)
        with mock.patch.object(C, "_yarn_inv_freq", yarn):
            BaukastenModel._rope(_GG(meta), _Ld(()), "qwen3", {}, 16, True, 32, torch.device("cpu"))
        self.assertEqual(gesehen, [2048])

    def test_lfm3_seitenverhaeltnis(self):
        from ggufengine import vision
        v = vision.Lfm3Vision.__new__(vision.Lfm3Vision)
        v.patch, v.merge, v.min_pixels, v.max_pixels = 16, 2, 65536, 16777216
        with self.assertRaises(ValueError):
            v.groesse(10000, 10, 1024)
        w, h = v.groesse(800, 600, 1024)
        self.assertEqual((w % 32, h % 32), (0, 0))
        self.assertLessEqual(w * h, 1024 * 32 * 32)

    def test_vision_schliesst_bei_fehler(self):
        from ggufengine import vision
        from ggufengine.gguf import GGUFError
        for cls in (vision.Gemma4Vision, vision.Gemma4UnifiedVision, vision.Qwen3VLVision,
                    vision.PixtralVision, vision.Lfm3Vision):
            pfad = _gguf({"general.architecture": "clip", "clip.vision.projector_type": "anders"})
            try:
                with self.assertRaises(GGUFError):
                    cls(pfad, "cpu")
            finally:
                os.remove(pfad)

    def test_altes_qwen3next_layout(self):
        from ggufengine.gguf import GGUFError
        from ggufengine.models.qwen35 import _GatedDeltaNetLayer
        with self.assertRaisesRegex(GGUFError, "ssm_in"):
            _GatedDeltaNetLayer(_Ld({"blk.0.ssm_in.weight", "blk.0.ssm_a"}), 0, {})

    def test_hybrid_k_norm_fehlt(self):
        from ggufengine.gguf import GGUFError
        from ggufengine.models.hybrid import _Attention
        namen = {f"blk.0.attn_{n}.weight" for n in ("q", "k", "v", "output", "q_norm")}
        hp = {"n_heads": 2, "kv_heads": [1], "head_dim": 8, "eps": 1e-6, "attn_scale": 0, "window": 0,
              "swa": [False]}
        with self.assertRaisesRegex(GGUFError, "attn_k_norm"):
            _Attention(_Ld(namen), 0, hp, None, 16)


if __name__ == "__main__":
    unittest.main()
