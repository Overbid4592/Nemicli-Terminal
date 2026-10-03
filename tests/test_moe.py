"""Tests für den gemeinsamen Experten-Block (ggufengine.models.moe) mit allen Speicherformen.

Ein Token (graph-fähiger Weg), mehrere Tokens (nach Experten sortiert) und eine einfache
Nachrechnung müssen übereinstimmen; das Ergebnis ist bei jedem Lauf gleich.

python -m unittest tests.test_moe
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
    import torch.nn.functional as F
    from ggufengine.gguf import GGML_TYPES
    from ggufengine.models import common as C
    from ggufengine.models.moe import Experten
    from ggufengine.qlinear import build_quant_linear, kernel_available
    from ggufengine.quant import dequantize
    from tests.test_gguf_iq import zufallsbloecke
except ImportError:                                   # pragma: no cover
    torch = None

TYP = {name: t for t, (name, _, _) in (GGML_TYPES.items() if torch else [])}
N_EXP, E, FF = 6, 256, 128


class _Lader:
    """Stellt fertige Expertenlisten unter GGUF-Namen bereit."""

    def __init__(self, teile):
        self.teile = teile

    def has(self, name):
        return name in self.teile

    def experts(self, name):
        return self.teile[name]


def _matrizen(seed):
    """Volle float32-Gewichte (n_exp, out, in) für gate, up und down."""
    g = torch.Generator().manual_seed(seed)
    return {k: torch.randn(N_EXP, o, i, generator=g) * 0.05
            for k, (o, i) in {"gate": (FF, E), "up": (FF, E), "down": (E, FF)}.items()}


def _nachrechnung(teile, x, idx, w):
    out = torch.zeros(x.shape[0], E)
    for t in range(x.shape[0]):
        for j in range(idx.shape[1]):
            e = int(idx[t, j])
            h = F.silu(teile["gate"][e] @ x[t]) * (teile["up"][e] @ x[t])
            out[t] += w[t, j] * (teile["down"][e] @ h)
    return out


def _auswahl(L, k=2, seed=0):
    g = torch.Generator().manual_seed(seed)
    idx = torch.stack([torch.randperm(N_EXP, generator=g)[:k] for _ in range(L)])
    w = torch.rand(L, k, generator=g)
    return idx, w / w.sum(-1, keepdim=True)


@unittest.skipIf(torch is None, "torch fehlt")
class ExpertenTests(unittest.TestCase):

    def _pruefe(self, ex, voll, device, toleranz):
        self.assertTrue(ex.pruefen())
        x = torch.randn(7, E)
        idx, w = _auswahl(7)
        erwartet = _nachrechnung(voll, x, idx, w)
        xd, idxd, wd = x.to(device), idx.to(device), w.to(device)
        viele = ex(xd, idxd, wd).cpu()
        self.assertTrue(torch.equal(viele, ex(xd, idxd, wd).cpu()), "nicht bei jedem Lauf gleich")
        einzeln = torch.cat([ex(xd[t:t + 1], idxd[t:t + 1], wd[t:t + 1]).cpu() for t in range(7)])
        for name, y in (("mehrere", viele), ("einzeln", einzeln)):
            rel = float((y - erwartet).abs().max() / erwartet.abs().max())
            self.assertLess(rel, toleranz, name)

    def test_dichte_experten(self):
        voll = _matrizen(1)
        teile = {f"blk.0.ffn_{k}_exps.weight": [C.Linear(m[e].clone()) for e in range(N_EXP)] for k, m in voll.items()}
        ex = Experten(_Lader(teile), "blk.0.", F.silu)
        self.assertTrue(ex.graphfaehig)
        self._pruefe(ex, voll, "cpu", 1e-5)

    def test_gemischte_liste_ohne_stapel(self):
        voll = _matrizen(2)
        teile = {f"blk.0.ffn_{k}_exps.weight": [C.Linear(m[e].clone()) for e in range(N_EXP)] for k, m in voll.items()}
        teile["blk.0.ffn_up_exps.weight"][0] = C.Linear(voll["up"][0].clone(), torch.zeros(FF))   # mit Bias
        ex = Experten(_Lader(teile), "blk.0.", F.silu)
        self.assertFalse(ex.graphfaehig)
        self._pruefe(ex, voll, "cpu", 1e-5)

    @unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
    def test_int4_stapel(self):
        if not kernel_available(torch.device("cuda")):
            self.skipTest("int4-Kernel fehlt")
        teile, voll = {}, {}
        for k, (o, i) in {"gate": (FF, E), "up": (FF, E), "down": (E, FF)}.items():
            raw = zufallsbloecke("Q4_0", N_EXP * o * i // 32, seed=len(k)).cuda()
            per = raw.numel() // N_EXP
            teile[f"blk.0.ffn_{k}_exps.weight"] = [
                build_quant_linear(raw[e * per:(e + 1) * per], TYP["Q4_0"], o, i) for e in range(N_EXP)]
            voll[k] = dequantize(raw.cpu(), TYP["Q4_0"], N_EXP * o * i).view(N_EXP, o, i)
        ex = Experten(_Lader(teile), "blk.0.", F.silu)
        self.assertTrue(ex.graphfaehig)
        self._pruefe(ex, voll, "cuda", 2e-2)

    @unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
    def test_iq_gepackt_mit_gate_up(self):
        teile, voll = {}, {}
        formen = {"gate_up": ("IQ2_XXS", 2 * FF, E), "down": ("IQ4_NL", E, FF)}
        for k, (art, o, i) in formen.items():
            _, bs, _ = GGML_TYPES[TYP[art]]
            raw = zufallsbloecke(art, N_EXP * o * i // bs, seed=o).cuda()
            teile[f"blk.0.ffn_{k}_exps.weight"] = C.PackedExperts(raw, TYP[art], N_EXP, o, i, torch.bfloat16)
            voll[k] = dequantize(raw.cpu(), TYP[art], N_EXP * o * i).view(N_EXP, o, i)
        voll["gate"], voll["up"] = voll.pop("gate_up").chunk(2, dim=1)
        ex = Experten(_Lader(teile), "blk.0.", F.silu)
        self.assertTrue(ex.graphfaehig)
        self._pruefe(ex, voll, "cuda", 2e-2)


if __name__ == "__main__":
    unittest.main()
