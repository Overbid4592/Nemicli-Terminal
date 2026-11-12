"""Gesprächsspeicher mit 6 Bit (Schlüssel int8, Werte 4 Bit) und 4 Bit: gedrehte Lloyd-Max-Stufen.

Die Drehung ändert q·k und die gewichtete Summe der Werte nicht; sie macht Ausreißer-Kanäle
harmlos. Ein winziges Modell rechnet mit 6 und 4 Bit fast wie mit 16 Bit, auch im CUDA-Graphen.

python -m unittest tests.test_kv4bit
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    from ggufengine.models import common as C
except ImportError:                                   # pragma: no cover
    torch = None


def _daten(n=600, H=2, D=64, seed=0, ausreisser=True):
    g = torch.Generator().manual_seed(seed)
    k = torch.randn(n, H, D, generator=g)
    if ausreisser:
        k[:, :, [3, 17, 40]] *= 12                     # Ausreißer-Kanäle wie bei echten Schlüsseln
    return k, torch.randn(n, H, D, generator=g), torch.randn(1, 2 * H, D, generator=g)


def _attention(c, q, n):
    return c.o_zurueck(C.attention(c.q_drehen(q), *c.kv(n), torch.tensor([n - 1]), n, q.shape[-1] ** -0.5))


@unittest.skipIf(torch is None, "torch fehlt")
class Speicher4BitTests(unittest.TestCase):

    def _cache(self, bits, n=10_000, d=64, dtype=None):
        return C.KVCache(2, d, n, "cpu", dtype or torch.float32, bits)

    def test_formate_und_platz(self):
        for bits, (k_art, v_art) in ((6, (torch.int8, torch.uint8)), (4, (torch.uint8, torch.uint8))):
            c = self._cache(bits)
            self.assertEqual((c.k.dtype, c.v.dtype, c.bits, c.gepackt, c.int8), (k_art, v_art, bits, True, False))
            self.assertEqual(c.v.shape[-1], 32)                                   # zwei Werte je Byte
            self.assertEqual(c.head_dim, 64)
        self.assertFalse(self._cache(4, d=40).gepackt)                            # ohne 32er-Gruppen: 16 Bit

    def test_drehung_ist_orthogonal_und_fest(self):
        r = C.kv_drehung(64, "cpu")
        self.assertLess(float((r @ r.t() - torch.eye(64)).abs().max()), 1e-5)
        self.assertTrue(torch.equal(r, C.kv_drehung(64, "cpu")))

    def test_drehung_aendert_die_attention_nicht(self):
        """Gedrehte Schlüssel und Werte, gedrehte Abfrage, zurückgedrehtes Ergebnis: dasselbe wie ungedreht."""
        k, v, q = _daten()
        r = C.kv_drehung(64, "cpu")
        pos, sk = torch.tensor([599]), 64 ** -0.5
        soll = C.attention(q, k.transpose(0, 1), v.transpose(0, 1), pos, 600, sk)
        gedreht = C.attention(q @ r, (k @ r).transpose(0, 1), (v @ r).transpose(0, 1), pos, 600, sk) @ r.t()
        self.assertLess(float((gedreht - soll).abs().max()), 1e-4)
        c = self._cache(4)
        self.assertLess(float((c.o_zurueck(c.q_drehen(q)) - q).abs().max()), 1e-5)

    def test_fehler_klein_und_drehung_hilft(self):
        k, v, q = _daten()
        ref = self._cache(16)
        ref.write(k, v, torch.arange(600))
        soll = _attention(ref, q, 600)
        fehler = {}
        for bits, drehen in ((8, True), (6, True), (4, True), (4, False)):
            c = self._cache(bits)
            if not drehen:
                c.dreh = torch.eye(64)
            c.write(k, v, torch.arange(600))
            fehler[(bits, drehen)] = float((_attention(c, q, 600) - soll).norm() / soll.norm())
            if bits == 4:
                kk = c.kv_echt(600)[0].transpose(0, 1)
                fehler[("k", drehen)] = float((kk - k).norm() / k.norm())
        self.assertLess(fehler[(6, True)], 0.12)
        self.assertLess(fehler[("k", True)], 0.11)                                # 4 Bit Lloyd-Max: ~9,7 %
        self.assertLess(fehler[(4, True)] * 2, fehler[(4, False)])                # gedreht deutlich besser
        self.assertLess(fehler[(8, True)], fehler[(6, True)])

    def test_praefix_ungedreht_fuer_jede_stufe(self):
        """kv_echt liefert ungedrehte Werte: ein gespeicherter Stand passt in jede Bit-Stufe."""
        k, v, q = _daten(n=300)
        c4 = self._cache(4)
        c4.write(k, v, torch.arange(300))
        k4, v4 = c4.kv_echt(300)
        self.assertLess(float((k4.transpose(0, 1) - k).norm() / k.norm()), 0.11)
        voll = self._cache(16)
        voll.fill(k4, v4)
        self.assertLess(float((_attention(voll, q, 300) - _attention(c4, q, 300)).abs().max()), 1e-4)
        nochmal = self._cache(4)
        nochmal.fill(k4, v4)
        for a, b in zip(nochmal.kv_echt(300), (k4, v4)):
            self.assertLess(float((a - b).norm() / b.norm()), 0.03)

    def test_bf16_und_wachsen(self):
        c = self._cache(6, dtype=torch.bfloat16)
        k, v, _ = _daten(n=5)
        c.write(k, v, torch.arange(5))
        vorher = [t.clone() for t in c.kv(5)]
        self.assertTrue(c.ensure(C.KV_GROWTH + 1))
        self.assertEqual((c.v.shape[1], c.vs.shape[1]), (2 * C.KV_GROWTH,) * 2)
        for a, b in zip(vorher, c.kv(5)):
            self.assertTrue(torch.equal(a, b))


V, E, H, HKV, D = 96, 128, 4, 2, 64


@unittest.skipIf(torch is None, "torch fehlt")
class Modell4BitTests(unittest.TestCase):

    def _logits(self, bits, geraet, n=14, vorab=9):
        import gc
        import gguf_testbau as B
        import transformers as T
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        cfg = T.Qwen3Config(vocab_size=V, hidden_size=E, intermediate_size=192, num_hidden_layers=2,
                            num_attention_heads=H, num_key_value_heads=HKV, head_dim=D, max_position_embeddings=64,
                            rms_norm_eps=1e-6, tie_word_embeddings=False, attn_implementation="eager")
        torch.manual_seed(0)
        hf = T.Qwen3ForCausalLM(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        meta, t = B.umwandeln(hf, "qwen3")
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, meta, t)
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device(geraet), torch.float32, 48, quant=False, kv_bits=bits)
                from test_kv_ram import _caches
                self.assertEqual({c.bits for c in _caches(modell)}, {bits})
                ids = torch.randint(0, V, (n,), generator=torch.Generator().manual_seed(1))
                # Kopie je Schritt: auf der GPU liefert der Motor die Logits in einem festen Puffer (CUDA-Graph)
                aus = [modell.forward(ids[:vorab].tolist(), 0).float().cpu().clone()]
                aus += [modell.forward([int(ids[j])], j).float().cpu().clone() for j in range(vorab, n)]
                del modell
            finally:
                gc.collect()
                gg.close()
        finally:
            os.unlink(pfad)
        return torch.stack([a.reshape(-1) for a in aus])

    def _vergleich(self, geraet):
        ref = self._logits(16, geraet)
        for bits, grenze in ((6, 0.2), (4, 0.2)):          # Zufallsmodell: ~0,14 (8 Bit ~0,01)
            with self.subTest(bits=bits):
                lg = self._logits(bits, geraet)
                self.assertLess(float((lg - ref).abs().max() / ref.abs().max()), grenze)
                self.assertGreater(float((lg.argmax(-1) == ref.argmax(-1)).float().mean()), 0.6)

    def test_cpu(self):
        self._vergleich("cpu")

    @unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
    def test_gpu(self):
        self._vergleich("cuda")


if __name__ == "__main__":
    unittest.main()
