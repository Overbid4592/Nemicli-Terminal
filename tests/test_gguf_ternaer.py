"""Tests für die ternären und 1-Bit-Formate (TQ1_0, TQ2_0, Q1_0, Q2_0, PQ2_0, PTQ1_0), ohne Modelldatei.

Auspacken gegen eine Wert-für-Wert-Nachrechnung von dequantize_row_* (ggml-quants.c, PrismML-Fork
für PQ2_0/PTQ1_0); Packen wie quantize_row_*_ref, ternäre Gewichte kommen verlustfrei zurück.
Ein winziges Qwen3 mit ternären Gewichten rechnet im Motor wie in transformers.

python -m unittest tests.test_gguf_ternaer
"""

import os
import struct
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
    from ggufengine import quant as Q
    from ggufengine.gguf import GGML_TYPES, GGUFFile
except ImportError:                                   # pragma: no cover
    torch = None

TYP = {name: t for t, (name, _, _) in (GGML_TYPES.items() if torch else [])}
FORMATE = ("Q1_0", "Q2_0", "PQ2_0", "TQ2_0", "TQ1_0", "PTQ1_0")
SKALA = {"TQ2_0": 64, "TQ1_0": 52, "PTQ1_0": 26}      # Lage der fp16-Skala (sonst vorne)
POW3 = (1, 3, 9, 27, 81, 243)


def _f16(b, o):
    return struct.unpack_from("<e", b, o)[0]


def _trit(v, n):
    q = (v * POW3[n]) & 255
    return ((q * 3) >> 8) - 1


# -- Nachrechnung von dequantize_row_* -----------------------------------------------

def ref_q2_0(b, qk=64):
    out, ts = [], 2 + qk // 4
    for o in range(0, len(b), ts):
        d = _f16(b, o)
        out += [(((b[o + 2 + j // 4] >> ((j % 4) * 2)) & 3) - 1) * d for j in range(qk)]
    return out


def ref_q1_0(b):
    out = []
    for o in range(0, len(b), 18):
        d = _f16(b, o)
        out += [d if (b[o + 2 + j // 8] >> (j % 8)) & 1 else -d for j in range(128)]
    return out


def ref_tq2_0(b):
    out = []
    for o in range(0, len(b), 66):
        d = _f16(b, o + 64)
        for j in (0, 32):
            for l in range(4):
                out += [(((b[o + j + m] >> (2 * l)) & 3) - 1) * d for m in range(32)]
    return out


def ref_tq1_0(b):
    out = []
    for o in range(0, len(b), 54):
        d = _f16(b, o + 52)
        qs, qh = b[o:o + 48], b[o + 48:o + 52]
        for j in range(0, 48 - 48 % 32, 32):
            for n in range(5):
                out += [_trit(qs[j + m], n) * d for m in range(32)]
        for j in range(48 - 48 % 32, 48, 16):
            for n in range(5):
                out += [_trit(qs[j + m], n) * d for m in range(16)]
        for n in range(4):
            out += [_trit(qh[j], n) * d for j in range(4)]
    return out


def ref_ptq1_0(b):
    out = []
    for o in range(0, len(b), 28):
        d = _f16(b, o + 26)
        qs, qh = b[o:o + 24], b[o + 24:o + 26]
        j = 0
        for c in (32, 16, 8):
            while j + c <= 24:
                for n in range(5):
                    out += [_trit(qs[j + m], n) * d for m in range(c)]
                j += c
        for n in range(4):
            out += [_trit(qh[h], n) * d for h in range(2)]
    return out


REF = {"Q1_0": ref_q1_0, "Q2_0": ref_q2_0, "PQ2_0": lambda b: ref_q2_0(b, 128), "TQ2_0": ref_tq2_0,
       "TQ1_0": ref_tq1_0, "PTQ1_0": ref_ptq1_0}


# -- Packen wie quantize_row_*_ref --------------------------------------------------

def _rund(v):                                          # roundf/lroundf: halbe Werte von der Null weg
    return int(v + 0.5) if v >= 0 else -int(-v + 0.5)


def _skala(blk, mittel=False):
    d = sum(abs(v) for v in blk) / len(blk) if mittel else max(abs(v) for v in blk)
    return d, (1.0 / d if d else 0.0)


def pack_q2_0(x, qk=64):
    out = bytearray()
    for i in range(0, len(x), qk):
        blk = x[i:i + qk]
        d, idv = _skala(blk)
        qs = bytearray(qk // 4)
        for j, w in enumerate(blk):
            qs[j // 4] |= min(3, max(0, _rund(w * idv) + 1)) << ((j % 4) * 2)
        out += struct.pack("<e", d) + qs
    return bytes(out)


def pack_q1_0(x):
    out = bytearray()
    for i in range(0, len(x), 128):
        blk = x[i:i + 128]
        qs = bytearray(16)
        for j, w in enumerate(blk):
            if w >= 0:
                qs[j // 8] |= 1 << (j % 8)
        out += struct.pack("<e", _skala(blk, mittel=True)[0]) + qs
    return bytes(out)


def pack_tq2_0(x):
    out = bytearray()
    for i in range(0, len(x), 256):
        blk = x[i:i + 256]
        d, idv = _skala(blk)
        qs = bytearray(64)
        for j in (0, 32):
            for m in range(32):
                for n in range(4):
                    qs[j + m] |= ((_rund(blk[4 * j + m + 32 * n] * idv) + 1) & 3) << (2 * n)
        out += qs + struct.pack("<e", d)
    return bytes(out)


def _fuenf(werte):
    q = 0
    for v in werte:
        q = q * 3 + v
    return (q * 256 + 242) // 243                      # Aufrunden: 243 = 3^5


def pack_trits(x, n_qs, n_qh, qk):
    out = bytearray()
    for i in range(0, len(x), qk):
        blk = x[i:i + qk]
        d, idv = _skala(blk)
        t = [_rund(v * idv) + 1 for v in blk]
        qs, k, j = bytearray(n_qs), 0, 0
        for c in (32, 16, 8):
            while j + c <= n_qs:
                for m in range(c):
                    qs[j + m] = _fuenf(t[k + m + n * c] for n in range(5))
                k += 5 * c
                j += c
        qh = bytearray(_fuenf([t[k + h + m * n_qh] for m in range(4)] + [0]) for h in range(n_qh))
        out += qs + qh + struct.pack("<e", d)
    return bytes(out)


PACK = {"Q1_0": pack_q1_0, "Q2_0": pack_q2_0, "PQ2_0": lambda x: pack_q2_0(x, 128), "TQ2_0": pack_tq2_0,
        "TQ1_0": lambda x: pack_trits(x, 48, 4, 256), "PTQ1_0": lambda x: pack_trits(x, 24, 2, 128)}


def zufallsbloecke(name, n_blocks, seed=0):
    _, _, size = GGML_TYPES[TYP[name]]
    g = torch.Generator().manual_seed(seed)
    raw = torch.randint(0, 256, (n_blocks, size), generator=g, dtype=torch.uint8)
    o = SKALA.get(name, 0)
    raw[:, o:o + 2] = (torch.rand(n_blocks, generator=g) * 0.01 + 0.001).half().view(torch.uint8).view(n_blocks, 2)
    return raw.reshape(-1)


def ternaer(n_zeilen, n_ein, gruppe, binaer=False, seed=0):
    """Gewichte (n_zeilen, n_ein), je Gruppe d * {-1, 0, +1} (binär: d * {-1, +1}) mit fp16-Skala d;
    der erste Wert jeder Gruppe ist +d, damit max |w| = d."""
    g = torch.Generator().manual_seed(seed)
    t = (torch.randint(0, 2, (n_zeilen, n_ein), generator=g) * 2 - 1 if binaer
         else torch.randint(-1, 2, (n_zeilen, n_ein), generator=g)).float()
    t.view(n_zeilen, -1, gruppe)[:, :, 0] = 1
    d = (torch.rand(n_zeilen, n_ein // gruppe, 1, generator=g) * 0.08 + 0.02).half().float()
    return (t.view(n_zeilen, -1, gruppe) * d).view(n_zeilen, n_ein)


def packen(name, w):
    return torch.tensor(list(PACK[name](w.reshape(-1).tolist())), dtype=torch.uint8)


@unittest.skipIf(torch is None, "torch fehlt")
class FormatTests(unittest.TestCase):

    def test_bloecke_wie_ggml(self):
        for name, (bs, ts) in {"Q1_0": (128, 18), "Q2_0": (64, 18), "PQ2_0": (128, 34), "TQ2_0": (256, 66),
                               "TQ1_0": (256, 54), "PTQ1_0": (128, 28)}.items():
            self.assertEqual(GGML_TYPES[TYP[name]][1:], (bs, ts), name)
        self.assertEqual((TYP["Q1_0"], TYP["Q2_0"], TYP["PQ2_0"], TYP["PTQ1_0"]), (41, 42, 142, 143))

    def test_auspacken_wie_referenz(self):
        for name in FORMATE:
            with self.subTest(name):
                raw = zufallsbloecke(name, 7)
                n = 7 * GGML_TYPES[TYP[name]][1]
                erwartet = torch.tensor(REF[name](bytes(raw.tolist())), dtype=torch.float32)
                self.assertTrue(torch.equal(Q.dequantize(raw, TYP[name], n, torch.float32), erwartet))
                b16 = Q.dequantize(raw, TYP[name], n, torch.bfloat16).float()
                self.assertLess(float((b16 - erwartet).abs().max() / erwartet.abs().max()), 1e-2)
                if torch.cuda.is_available():
                    gpu = Q.dequantize(raw.cuda(), TYP[name], n, torch.float32).cpu()
                    self.assertTrue(torch.equal(gpu, erwartet))

    def test_trit_plan_wie_tq1_0(self):
        byte, pot = Q.trit_plan(48, 4)
        self.assertEqual(len(byte), 256)
        self.assertEqual((byte[159], pot[159], byte[160], byte[239], pot[239], byte[240], byte[255], pot[255]),
                         (31, 81, 32, 47, 81, 48, 51, 27))
        self.assertEqual(len(Q.trit_plan(24, 2)[0]), 128)

    def test_ternaer_verlustfrei(self):
        for name in FORMATE:
            with self.subTest(name):
                bs = GGML_TYPES[TYP[name]][1]
                w = ternaer(3, 2 * 256, bs, binaer=name == "Q1_0", seed=1)
                aus = Q.dequantize(packen(name, w), TYP[name], w.numel()).view_as(w)
                self.assertTrue(torch.equal(aus, w))


def _gguf_q2_0(pfad, gruppe):
    """Zwei Q2_0-Matrizen (Typ 42) mit 64er- oder 128er-Blöcken."""
    import gguf_testbau as B
    w = [ternaer(4, 512, gruppe, seed=s) for s in (2, 3)]
    name = "Q2_0" if gruppe == 64 else "PQ2_0"
    B.schreibe_gguf(pfad, {"general.architecture": "llama"},
                    {f"t{i}": (42, tuple(x.shape), bytes(packen(name, x).tolist())) for i, x in enumerate(w)})
    return w


@unittest.skipIf(torch is None, "torch fehlt")
class LeserTests(unittest.TestCase):

    def _lesen(self, gruppe):
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            w = _gguf_q2_0(pfad, gruppe)
            with GGUFFile(pfad) as gg:
                typen = [gg.tensors[f"t{i}"].type_name for i in range(2)]
                werte = [Q.dequantize(torch.frombuffer(bytearray(gg.tensor_bytes(gg.tensors[f"t{i}"])),
                                                       dtype=torch.uint8), gg.tensors[f"t{i}"].ggml_type, 2048)
                         for i in range(2)]
            return typen, werte, w
        finally:
            os.unlink(pfad)

    def test_q2_0_bleibt_64(self):
        typen, werte, w = self._lesen(64)
        self.assertEqual(typen, ["Q2_0", "Q2_0"])
        self.assertTrue(all(torch.equal(a.view(4, 512), b) for a, b in zip(werte, w)))

    def test_q2_0_mit_128er_bloecken(self):
        """Ältere PrismML-Dateien: Typ 42 mit 128 Werten je Block – gelesen als PQ2_0."""
        typen, werte, w = self._lesen(128)
        self.assertEqual(typen, ["PQ2_0", "PQ2_0"])
        self.assertTrue(all(torch.equal(a.view(4, 512), b) for a, b in zip(werte, w)))


@unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
class KernelTests(unittest.TestCase):

    def setUp(self):
        from ggufengine import cudakern
        if not cudakern.verfuegbar("cuda", "Q2_0", 64):
            self.skipTest("NVRTC nicht verfügbar")
        self.K = cudakern

    def test_mv_und_mm(self):
        for name in FORMATE:
            with self.subTest(name):
                bs = GGML_TYPES[TYP[name]][1]
                raw = zufallsbloecke(name, 3 * 12 * 768 // bs, seed=4).cuda()
                voll = Q.dequantize(raw, TYP[name], 3 * 12 * 768).view(3, 12, 768)
                raw = raw.view(3, -1)
                ids = torch.tensor([2, 0], device="cuda")
                x = torch.randn(768, device="cuda").to(torch.bfloat16)
                erwartet = voll[ids] @ x.float()
                y = self.K.mv(name, raw, ids, x, 12, 768)
                self.assertLess(float((y - erwartet).abs().max() / erwartet.abs().max()), 1e-5)
                for M in (2, 9, self.K.MM_ZEILEN):
                    a = torch.randn(M, 768, device="cuda").to(torch.bfloat16)
                    erwartet = a.float() @ voll[1].t()
                    y = self.K.mm(name, raw[1].contiguous(), a, 12, 768)
                    self.assertLess(float((y - erwartet).abs().max() / erwartet.abs().max()), 1e-5, M)


@unittest.skipIf(torch is None, "torch fehlt")
class Qwen3TernaerTests(unittest.TestCase):
    """Winziges Qwen3 (Zufall, nichts geladen) mit ternären Gewichten in allen Linearschichten."""

    def _vergleich(self, name, geraet):
        import gc
        import gguf_testbau as B
        import transformers as T
        from ggufengine.models import build_model
        V, n, vorab = 96, 12, 8
        cfg = T.Qwen3Config(vocab_size=V, hidden_size=256, intermediate_size=512, num_hidden_layers=2,
                            num_attention_heads=4, num_key_value_heads=2, head_dim=64, max_position_embeddings=64,
                            rms_norm_eps=1e-6, tie_word_embeddings=False, attn_implementation="eager")
        torch.manual_seed(0)
        hf = T.Qwen3ForCausalLM(cfg).eval().float()
        bs = GGML_TYPES[TYP[name]][1]
        with torch.no_grad():
            for i, (pn, p) in enumerate(hf.named_parameters()):
                if p.dim() == 2 and ".layers." in pn:
                    p.copy_(ternaer(*p.shape, bs, binaer=name == "Q1_0", seed=10 + i))
                elif "norm" in pn:
                    p.uniform_(0.8, 1.2)
                else:
                    p.normal_(0, 0.5)
        meta, t = B.umwandeln(hf, "qwen3")
        for k, w in list(t.items()):
            if k.startswith("blk.") and w.dim() == 2:
                t[k] = (TYP[name], tuple(w.shape), bytes(packen(name, w).tolist()))
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, meta, t)
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device(geraet), torch.float32, 48, quant=True)
                ids = torch.randint(0, V, (n,))
                with torch.no_grad():
                    ref = hf(ids[None]).logits[0].float()
                # Kopie je Schritt: auf der GPU liefert der Motor die Logits in einem festen Puffer (CUDA-Graph)
                aus = [modell.forward(ids[:vorab].tolist(), 0).float().cpu().clone()]
                aus += [modell.forward([int(ids[j])], j).float().cpu().clone() for j in range(vorab, n)]
                fehler = [float((a.reshape(-1) - ref[j]).abs().max() / ref[j].abs().max())
                          for j, a in zip(range(vorab - 1, n), aus)]
                del modell, aus
            finally:
                gc.collect()
                gg.close()
        finally:
            os.unlink(pfad)
        return fehler

    def test_cpu(self):
        for name in FORMATE:
            with self.subTest(name):
                self.assertLess(max(self._vergleich(name, "cpu")), 1e-4)

    @unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
    def test_gpu_kernel(self):
        """Auf der GPU rechnen die eigenen Kernel (Eingang bf16): kleine Abweichung erlaubt."""
        for name in FORMATE:
            with self.subTest(name):
                self.assertLess(max(self._vergleich(name, "cuda")), 3e-2)


if __name__ == "__main__":
    unittest.main()
