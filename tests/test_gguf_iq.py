"""Tests für die Block-Formate ohne int4-Weg (K-Quants Q2/Q3, IQ-Familie, MXFP4), gepackte
Experten und die eigenen CUDA-Kernel, ohne Modelldatei.

Die schnelle Torch-Fassung wird gegen eine Wert-für-Wert-Nachrechnung der Blockformate
geprüft (Aufbau wie dequantize_row_* in ggml-quants.c).

python -m unittest tests.test_gguf_iq
"""

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
    from ggufengine import iqtabellen as IT
    from ggufengine import quant as Q
    from ggufengine.gguf import GGML_TYPES
    from ggufengine.models import common as C
except ImportError:                                   # pragma: no cover
    torch = None

TYP = {name: t for t, (name, _, _) in (GGML_TYPES.items() if torch else [])}


def _ksign(i):
    return i | ((bin(i).count("1") & 1) << 7)


def _grid(tab, i):
    return [(tab[i] >> (8 * j)) & 0xFF for j in range(8)]


def _f16(b, o):
    return struct.unpack_from("<e", b, o)[0]


def ref_iq2_xxs(b):
    out = []
    for o in range(0, len(b), 66):
        d = _f16(b, o)
        for ib in range(8):
            q = b[o + 2 + 8 * ib:o + 10 + 8 * ib]
            aux1 = struct.unpack_from("<I", q, 4)[0]
            db = d * (0.5 + (aux1 >> 28)) * 0.25
            for l in range(4):
                g, s = _grid(IT.IQ2XXS_GRID, q[l]), _ksign((aux1 >> (7 * l)) & 127)
                out += [db * g[j] * (-1 if s & (1 << j) else 1) for j in range(8)]
    return out


def ref_iq2_s(b):
    out = []
    for o in range(0, len(b), 82):
        d = _f16(b, o)
        qs, signs = b[o + 2:o + 34], b[o + 34:o + 66]
        qh, sc = b[o + 66:o + 74], b[o + 74:o + 82]
        for ib in range(8):
            db = [d * (0.5 + (sc[ib] & 0xF)) * 0.25, d * (0.5 + (sc[ib] >> 4)) * 0.25]
            for l in range(4):
                g = _grid(IT.IQ2S_GRID, qs[4 * ib + l] | ((qh[ib] << (8 - 2 * l)) & 0x300))
                s = signs[4 * ib + l]
                out += [db[l // 2] * g[j] * (-1 if s & (1 << j) else 1) for j in range(8)]
    return out


def ref_iq4_nl(b):
    out = []
    for o in range(0, len(b), 18):
        d = _f16(b, o)
        qs = b[o + 2:o + 18]
        out += [d * IT.KVALUES_IQ4NL[x & 0xF] for x in qs] + [d * IT.KVALUES_IQ4NL[x >> 4] for x in qs]
    return out


def zufallsbloecke(name, n_blocks, seed=0):
    """Zufällige Bytes; die fp16-Skalen (bzw. der Exponent) jedes Blocks werden auf kleine,
    endliche Werte gesetzt."""
    _, _, size = GGML_TYPES[TYP[name]]
    g = torch.Generator().manual_seed(seed)
    raw = torch.randint(0, 256, (n_blocks, size), generator=g, dtype=torch.uint8)
    def skala():
        return (torch.rand(n_blocks, generator=g) * 0.01 + 0.001).half().view(torch.uint8).view(n_blocks, 2)
    if name == "MXFP4":
        raw[:, 0] = torch.randint(110, 140, (n_blocks,), generator=g, dtype=torch.uint8)
    elif name == "IQ1_M":                     # fp16 aus den oberen Nibbles der vier Skalen-Wörter
        d = skala().to(torch.int32)
        d16 = d[:, 0] | (d[:, 1] << 8)
        for k in range(4):
            raw[:, 49 + 2 * k] = (raw[:, 49 + 2 * k] & 0x0F) | (((d16 >> (4 * k)) & 0x0F) << 4).to(torch.uint8)
    else:
        felder = {"Q2_K": (80, 82), "Q3_K": (108,), "Q6_K": (208,), "Q4_1": (0, 2), "Q5_1": (0, 2),
                  "Q4_K": (0, 2), "Q5_K": (0, 2)}
        for o in felder.get(name, (0,)):
            raw[:, o:o + 2] = skala()
    return raw.reshape(-1)


@unittest.skipIf(torch is None, "torch fehlt")
class IQFormatTests(unittest.TestCase):

    def _pruefe(self, name, ref, n_blocks=6):
        raw = zufallsbloecke(name, n_blocks)
        _, bs, _ = GGML_TYPES[TYP[name]]
        erwartet = torch.tensor(ref(bytes(raw.tolist())), dtype=torch.float32)
        f32 = Q.dequantize(raw, TYP[name], n_blocks * bs, torch.float32)
        self.assertTrue(torch.equal(f32, erwartet), name)
        b16 = Q.dequantize(raw, TYP[name], n_blocks * bs, torch.bfloat16).float()
        self.assertLess(float((b16 - erwartet).abs().max() / erwartet.abs().max()), 1e-2, name)

    def test_iq2_xxs(self):
        self._pruefe("IQ2_XXS", ref_iq2_xxs)

    def test_iq2_s(self):
        self._pruefe("IQ2_S", ref_iq2_s)

    def test_iq4_nl(self):
        self._pruefe("IQ4_NL", ref_iq4_nl, n_blocks=40)

    def test_tabellen_vollstaendig(self):
        laengen = [len(t) for t in (IT.IQ2XXS_GRID, IT.IQ2XS_GRID, IT.IQ2S_GRID, IT.IQ3XXS_GRID, IT.IQ3S_GRID,
                                    IT.IQ1S_GRID, IT.KVALUES_IQ4NL)]
        self.assertEqual(laengen, [256, 512, 1024, 256, 512, 2048, 16])

    def test_mxfp4_von_hand(self):
        # Exponent 127 -> Skala 2^-1; Nibble 7 = 12 (6.0 doppelt), Nibble 9 = -1 (-0.5 doppelt)
        raw = torch.tensor([127] + [0x97] + [0x00] * 15, dtype=torch.uint8)
        y = Q.dequantize(raw, TYP["MXFP4"], 32)
        self.assertEqual((float(y[0]), float(y[16]), float(y[1]), float(y[17])), (6.0, -0.5, 0.0, 0.0))


@unittest.skipIf(torch is None, "torch fehlt")
class GepackteExpertenTests(unittest.TestCase):

    def _experten(self, name, n_exp=3, n_out=8, n_in=512):
        _, bs, _ = GGML_TYPES[TYP[name]]
        raw = zufallsbloecke(name, n_exp * n_out * n_in // bs, seed=3)
        ex = C.PackedExperts(raw, TYP[name], n_exp, n_out, n_in, torch.float32)
        voll = Q.dequantize(raw, TYP[name], n_exp * n_out * n_in).view(n_exp, n_out, n_in)
        return ex, voll

    def test_auswahl_und_einzelner_experte(self):
        for name in ("IQ2_XXS", "IQ2_S", "IQ4_NL"):
            ex, voll = self._experten(name)
            ids = torch.tensor([2, 0])
            self.assertTrue(torch.equal(ex.weights(ids), voll[ids]), name)
            x = torch.randn(5, ex.n_in)
            self.assertTrue(torch.allclose(ex[1](x), x @ voll[1].t(), atol=1e-4), name)
            self.assertEqual(len(ex), 3)

    def test_matvec_und_matvec_je(self):
        for name in ("IQ2_XXS", "IQ2_S", "IQ4_NL"):
            ex, voll = self._experten(name)
            ids = torch.tensor([1, 2])
            x = torch.randn(ex.n_in)
            erwartet = voll[ids] @ x
            rel = (ex.matvec(ids, x) - erwartet).abs().max() / erwartet.abs().max()
            self.assertLess(float(rel), 5e-3, name)
            a = torch.randn(2, ex.n_in)
            erwartet = torch.einsum("koi,ki->ko", voll[ids], a)
            self.assertTrue(torch.allclose(ex.matvec_je(ids, a), erwartet, atol=1e-4), name)

    def test_gepackte_schicht(self):
        n_out, n_in = 40, 512
        bias = torch.randn(n_out)
        for name in ("Q3_K", "IQ4_XS", "MXFP4"):
            _, bs, _ = GGML_TYPES[TYP[name]]
            raw = zufallsbloecke(name, n_out * n_in // bs, seed=7)
            w = Q.dequantize(raw, TYP[name], n_out * n_in).view(n_out, n_in)
            for dev in ("cpu", "cuda") if torch.cuda.is_available() else ("cpu",):
                pl = C.PackedLinear(raw.to(dev), TYP[name], n_out, n_in, bias.to(dev), torch.float32)
                pl.ZEILEN_JE_STUECK = 7 * n_in                    # mehrere Stücke, letztes kürzer
                for M in (1, 3, 20):                               # Kernel und Auspacken
                    x = torch.randn(2, M, n_in)
                    erwartet = x @ w.t() + bias
                    y = pl(x.to(dev)).cpu()
                    self.assertEqual(tuple(y.shape), (2, M, n_out))
                    rel = float((y - erwartet).abs().max() / erwartet.abs().max())
                    self.assertLess(rel, 1e-2, f"{name} {dev} M={M}")

    def test_nur_iq_formate_bleiben_gepackt(self):
        self.assertTrue(C.packed_type(TYP["IQ2_XXS"]))
        self.assertFalse(C.packed_type(TYP["Q4_K"]))


@unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
class KernelTests(unittest.TestCase):
    """Eigene CUDA-Kernel (NVRTC) gegen die exakte Entpackung in float32, jedes Format."""

    def setUp(self):
        from ggufengine import cudakern
        if not cudakern.verfuegbar("cuda", "IQ4_NL", 32):
            self.skipTest("NVRTC nicht verfügbar")
        self.K = cudakern

    def _experten(self, name, n_exp, n_out, n_in, seed):
        _, bs, _ = GGML_TYPES[TYP[name]]
        raw = zufallsbloecke(name, n_exp * n_out * n_in // bs, seed=seed).cuda()
        voll = Q.dequantize(raw, TYP[name], n_exp * n_out * n_in).view(n_exp, n_out, n_in)
        return raw.view(n_exp, -1), voll

    def test_alle_formate(self):
        for name in self.K.FORMATE:
            with self.subTest(name):
                raw, voll = self._experten(name, 5, 12, 768, seed=4)        # 12 Zeilen: letzter Block halb voll
                ids = torch.tensor([4, 0, 2], device="cuda")
                x = torch.randn(768, device="cuda").to(torch.bfloat16)
                erwartet = voll[ids] @ x.float()
                y = self.K.mv(name, raw, ids, x, 12, 768)
                self.assertLess(float((y - erwartet).abs().max() / erwartet.abs().max()), 1e-5)
                a = torch.randn(3, 768, device="cuda").to(torch.bfloat16)    # eigene Eingabe je Experte
                erwartet = torch.einsum("koi,ki->ko", voll[ids], a.float())
                y = self.K.mv(name, raw, ids, a, 12, 768)
                self.assertLess(float((y - erwartet).abs().max() / erwartet.abs().max()), 1e-5)

    def test_im_cuda_graph(self):
        raw, voll = self._experten("IQ2_XXS", 3, 8, 512, seed=6)
        ids = torch.tensor([2, 1], device="cuda")
        x = torch.randn(512, device="cuda").to(torch.bfloat16)
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            self.K.mv("IQ2_XXS", raw, ids, x, 8, 512)
        torch.cuda.current_stream().wait_stream(s)
        g = torch.cuda.CUDAGraph()
        with torch.cuda.graph(g):
            y = self.K.mv("IQ2_XXS", raw, ids, x, 8, 512)
        ids.copy_(torch.tensor([0, 2]))                 # neue Auswahl, gleicher Graph
        g.replay()
        torch.cuda.synchronize()
        erwartet = voll[[0, 2]] @ x.float()
        self.assertLess(float((y - erwartet).abs().max() / erwartet.abs().max()), 1e-5)


if __name__ == "__main__":
    unittest.main()
