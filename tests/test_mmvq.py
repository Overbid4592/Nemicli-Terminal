"""Tests für die Schreib-Kerne (ggufengine/mmvq.py): int8-Matrix-Vektor-Produkte auf Q4_K/Q6_K,
Auspacken nach bf16 und die verschmolzenen Kleinschritte eines Tokens (Norm, Aktivierung, RoPE +
KV-Cache, Attention) – jeweils gegen den Torch-Weg des Motors, ohne Modelldatei.

python -m unittest tests.test_mmvq
"""

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
    import torch.nn.functional as F
    from ggufengine import mmvq
    from ggufengine.gguf import GGML_TYPES
    from ggufengine.models import common as C
    from ggufengine.quant import dequantize
except ImportError:                                   # pragma: no cover
    torch = None

TYP = {name: t for t, (name, _, _) in (GGML_TYPES.items() if torch else [])}


def _bereit() -> bool:
    return torch is not None and torch.cuda.is_available() and mmvq._funktionen(torch.device("cuda")) is not None


def roh(name, n, k, seed=0):
    """Zufällige Blöcke mit kleinen Skalen (Werte im üblichen Bereich)."""
    g = torch.Generator().manual_seed(seed)
    _, bs, ts = GGML_TYPES[TYP[name]]
    nb = n * k // bs
    r = torch.randint(0, 256, (nb, ts), dtype=torch.uint8, generator=g)
    if name == "Q4_K":
        r[:, 0:2] = torch.full((nb, 1), 0.002, dtype=torch.float16).view(torch.uint8)
        r[:, 2:4] = torch.full((nb, 1), 0.001, dtype=torch.float16).view(torch.uint8)
    else:
        r[:, 208:210] = torch.full((nb, 1), 0.001, dtype=torch.float16).view(torch.uint8)
    return r.view(-1).cuda()


def rel(a, b) -> float:
    return float((a.float() - b.float()).norm() / b.float().norm())


@unittest.skipUnless(_bereit(), "keine CUDA-GPU oder NVRTC")
class MatrixTest(unittest.TestCase):
    def test_mv_wie_entpackt(self):
        torch.manual_seed(0)
        for name in mmvq.FORMATE:
            for n, k in ((256, 512), (1024, 4096), (300, 1280)):
                r = roh(name, n, k)
                w = dequantize(r, TYP[name], n * k, torch.float32).view(n, k)
                x = torch.randn(k, device="cuda")
                for xe in (x.to(torch.bfloat16), x, mmvq.Eingang(x)):
                    y = mmvq.mv(name, r, xe, n, k)
                    self.assertLess(rel(y, w @ x), 0.02, (name, n, k))

    def test_mmq_wie_entpackt(self):
        """Tensor-Kern-Weg: auch krumme Zeilen- und Tokenzahlen, Ausreißer im Eingang."""
        torch.manual_seed(1)
        for name in mmvq.FORMATE:
            for n, k in ((300, 1280), (1024, 4096)):
                r = roh(name, n, k, seed=7)
                w = dequantize(r, TYP[name], n * k, torch.float32).view(n, k)
                for M in (2, 9, 13, 33, 64):
                    x = torch.randn(M, k, device="cuda")
                    x[:, 5] = 300.0
                    self.assertLess(rel(mmvq.mm(name, r, x, n, k), x @ w.t()), 2e-3, (name, n, k, M))

    def test_mmq_geteilter_eingang(self):
        """In eingang_teilen() wird der Eingang einmal vorbereitet – beide Matrizen rechnen richtig."""
        r1, r2 = roh("Q4_K", 512, 1024, seed=11), roh("Q6_K", 256, 1024, seed=12)
        w1 = dequantize(r1, TYP["Q4_K"], 512 * 1024, torch.float32).view(512, 1024)
        w2 = dequantize(r2, TYP["Q6_K"], 256 * 1024, torch.float32).view(256, 1024)
        x = torch.randn(20, 1024, device="cuda", dtype=torch.bfloat16)
        starts = []
        alt = mmvq._starten
        with unittest.mock.patch.object(mmvq, "_starten", lambda fn, *a, **k: (starts.append(fn), alt(fn, *a, **k))):
            with mmvq.eingang_teilen():
                y1, y2 = mmvq.mm("Q4_K", r1, x, 512, 1024), mmvq.mm("Q6_K", r2, x, 256, 1024)
        self.assertEqual(starts.count(mmvq._funktionen(x.device)["mmq_eingang"]), 1)
        self.assertLess(rel(y1, x.float() @ w1.t()), 2e-3)
        self.assertLess(rel(y2, x.float() @ w2.t()), 2e-3)

    def test_entpacken(self):
        for name in mmvq.FORMATE:
            r = roh(name, 64, 1024, seed=3)
            w = dequantize(r, TYP[name], 64 * 1024, torch.float32).view(64, 1024)
            self.assertLess(rel(mmvq.entpacken(name, r, 64, 1024), w), 0.004, name)

    def test_packedlinear_wege(self):
        """Wenige Token über die int8-Kerne (auch mehrere Durchgänge), viele über das Auspacken."""
        r = roh("Q4_K", 512, 1024, seed=5)
        pl = C.PackedLinear(r, TYP["Q4_K"], 512, 1024, None, torch.bfloat16)
        self.assertTrue(pl.mmvq)
        w = dequantize(r, TYP["Q4_K"], 512 * 1024, torch.float32).view(512, 1024)
        for M in (1, 9, 20, 40, 100, 130):              # 20–100: Tensor-Kerne, 130: Auspacken
            x = torch.randn(M, 1024, device="cuda", dtype=torch.bfloat16)
            self.assertLess(rel(pl(x), x.float() @ w.t()), 0.02, M)


@unittest.skipUnless(_bereit(), "keine CUDA-GPU oder NVRTC")
class KleinschrittTest(unittest.TestCase):
    def test_norm(self):
        torch.manual_seed(1)
        for n in (4096, 2880):
            h = torch.randn(n, device="cuda") * 3
            r = torch.randn(n, device="cuda")
            w = torch.rand(n, device="cuda") + 0.5
            xn, e, h2 = mmvq.norm_q8(h, w, 1e-5)
            self.assertIs(h2, h)
            self.assertLess(rel(xn, C.rms_norm(h, w, 1e-5)), 1e-5)
            xn, e, h2 = mmvq.norm_q8(h, w, 1e-6, rest=r, rs=0.5)
            self.assertLess(rel(h2, h + 0.5 * r), 1e-6)
            self.assertLess(rel(xn, C.rms_norm(h + 0.5 * r, w, 1e-6)), 1e-5)
            deq = e.xq.float().view(-1, 32) * e.xd.view(-1, 1)
            self.assertLess(rel(deq.view(-1), xn), 0.01)
            deq2 = deq + e.xq2.float().view(-1, 32) * (e.xd.view(-1, 1) / 254)
            self.assertLess(rel(deq2.view(-1), xn), 1e-4)                      # zweite Stufe: etwa 15 Bit
            self.assertLess(rel(e.xs, xn.view(-1, 32).sum(1)), 1e-5)            # exakte Summe je 32

    def test_aktivierung(self):
        g = torch.randn(3072, device="cuda") * 2
        u = torch.randn(3072, device="cuda")
        a, _ = mmvq.akt_q8(g, u, False)
        self.assertLess(rel(a, F.silu(g) * u), 1e-5)
        a, _ = mmvq.akt_q8(g, None, True)
        self.assertLess(rel(a, F.gelu(g, approximate="tanh")), 1e-5)

    def _rope_ref(self, q, k, v, bias, rope, pos, norms, eps, q_temp, H, Hkv, D):
        q = (q + bias[0] if bias[0] is not None else q).view(1, H, D)
        k = (k + bias[1] if bias[1] is not None else k).view(1, Hkv, D)
        v = (v + bias[2] if bias[2] is not None else v).view(1, Hkv, D)
        if norms[0] is not None:
            q, k = C.rms_norm(q, norms[0], eps), C.rms_norm(k, norms[1], eps)
        if rope is not None:
            q, k = rope(q, pos), rope(k, pos)
        if q_temp is not None:
            beta, n = q_temp
            q = q * (1 + beta * torch.log1p(torch.div(pos, n, rounding_mode="floor").float())).view(1, 1, 1)
        return q.reshape(-1), k[0], v[0]

    def test_rope_kv(self):
        torch.manual_seed(2)
        H, Hkv, D, cap = 8, 2, 128, 300
        for neox, rd, mit_norm, mit_bias, temp in ((True, 128, False, False, None), (False, 128, False, True, None),
                                                    (True, 64, True, False, (0.1, 8)), (False, 96, True, True, (0.2, 4)),
                                                    (True, 0, False, False, None)):
            rope = C.RoPE(rd, 10000.0, "cuda", max_pos=cap, neox=neox, mscale=1.1) if rd else None
            q = torch.randn(H * D, device="cuda")
            k = torch.randn(Hkv * D, device="cuda")
            v = torch.randn(Hkv * D, device="cuda")
            bias = tuple(torch.randn(n, device="cuda") if mit_bias else None for n in (H * D, Hkv * D, Hkv * D))
            norms = (torch.rand(D, device="cuda") + 0.5, torch.rand(D, device="cuda") + 0.5) if mit_norm else (None, None)
            pos = torch.tensor([217], device="cuda")
            kc = torch.zeros(Hkv, cap, D, device="cuda", dtype=torch.bfloat16)
            vc = torch.zeros_like(kc)
            qo = mmvq.rope_kv(q, k, v, bias, rope, pos, norms, 1e-6, temp, kc, vc, H, Hkv, D)
            rq, rk, rv = self._rope_ref(q, k, v, bias, rope, pos, norms, 1e-6, temp, H, Hkv, D)
            fall = (neox, rd, mit_norm, mit_bias, temp)
            self.assertLess(rel(qo, rq), 1e-5, fall)
            self.assertLess(rel(kc[:, 217], rk), 0.005, fall)
            self.assertLess(rel(vc[:, 217], rv), 0.005, fall)
            self.assertEqual(float(kc[:, :217].abs().sum() + kc[:, 218:].abs().sum()), 0.0)

    def test_attention(self):
        torch.manual_seed(3)
        for H, Hkv, D, window, sinks, pos in ((8, 2, 128, 0, False, 0), (8, 2, 128, 0, False, 63),
                                              (8, 2, 128, 0, False, 64), (32, 8, 128, 0, False, 517),
                                              (4, 4, 64, 0, True, 200), (16, 2, 256, 100, False, 333),
                                              (6, 1, 64, 0, False, 129), (8, 2, 128, 0, False, 5000),
                                              (16, 4, 64, 3000, True, 9500)):
            cap = max(640, pos + 100)
            kv_len = cap + 128
            kc = torch.randn(Hkv, cap, D, device="cuda").to(torch.bfloat16)
            vc = torch.randn(Hkv, cap, D, device="cuda").to(torch.bfloat16)
            q = torch.randn(H * D, device="cuda")
            s = torch.randn(H, device="cuda") if sinks else None
            p = torch.tensor([pos], device="cuda")
            out, e = mmvq.attention1(q, kc, vc, p, kv_len, D ** -0.5, window, s, H, Hkv, D)
            n = min(kv_len, cap)
            dist = p.unsqueeze(1) - torch.arange(n, device="cuda").unsqueeze(0)
            mask = dist >= 0
            if window:
                mask = mask & (dist < window)
            ref = C._attention_step(q.view(1, H, D), kc[:, :n], vc[:, :n], mask, D ** -0.5, s)
            fall = (H, Hkv, D, window, sinks, pos)
            self.assertLess(rel(out, ref.reshape(-1)), 0.01, fall)
            deq = e.xq.float().view(-1, 32) * e.xd.view(-1, 1)
            self.assertLess(rel(deq.view(-1), out), 0.01, fall)

    def test_im_cuda_graph(self):
        """pos kommt aus einem GPU-Puffer: ein aufgenommener Graph rechnet jede Position richtig."""
        H, Hkv, D, cap = 8, 2, 64, 256
        kc = torch.randn(Hkv, cap, D, device="cuda").to(torch.bfloat16)
        vc = torch.randn(Hkv, cap, D, device="cuda").to(torch.bfloat16)
        q = torch.randn(H * D, device="cuda")
        pos = torch.zeros(1, dtype=torch.long, device="cuda")
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            mmvq.attention1(q, kc, vc, pos, cap, 0.125, 0, None, H, Hkv, D)
        torch.cuda.current_stream().wait_stream(s)
        g = torch.cuda.CUDAGraph()
        with torch.cuda.graph(g):
            out, _ = mmvq.attention1(q, kc, vc, pos, cap, 0.125, 0, None, H, Hkv, D)
        for p in (5, 70, 255):
            pos.fill_(p)
            g.replay()
            mask = (torch.tensor([p], device="cuda").unsqueeze(1) - torch.arange(cap, device="cuda")) >= 0
            ref = C._attention_step(q.view(1, H, D), kc, vc, mask, 0.125)
            self.assertLess(rel(out, ref.reshape(-1)), 0.01, p)


if __name__ == "__main__":
    unittest.main()
