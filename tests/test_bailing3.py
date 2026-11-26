"""Ling 3.0 / bailingmoe3 ohne Modelldatei (CPU): KDA-Blockform, MLA in absorbierter Form,
Experten-Auswahl nach Gruppen, Chat-Format.

python -m unittest tests.test_bailing3
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
    from ggufengine.models import bailing3 as B
    from ggufengine.models import common as C
except ImportError:                                   # pragma: no cover
    torch = None


def _kda_referenz(q, k, v, g, beta, S):
    """Token für Token wie naive_recurrent_kda (fla)."""
    out = torch.empty_like(v)
    for t in range(q.shape[0]):
        S = S * g[t].exp()[..., None]
        S = S + torch.einsum("hk,hv->hkv", beta[t][:, None] * k[t], v[t] - (k[t][..., None] * S).sum(-2))
        out[t] = torch.einsum("hk,hkv->hv", q[t], S)
    return out, S


@unittest.skipIf(torch is None, "torch fehlt")
class KDATests(unittest.TestCase):

    def test_blockform_gleich_token_fuer_token(self):
        for L, streuung in ((150, 3.0), (300, 30.0), (7, 3.0)):     # 30: Zerfall fast an der Grenze -5
            g_ = torch.Generator().manual_seed(L)
            H, D = 3, 32
            q = B._l2norm(torch.randn(L, H, D, generator=g_)) * D ** -0.5
            k = B._l2norm(torch.randn(L, H, D, generator=g_))
            v = torch.randn(L, H, D, generator=g_)
            g = -5 * torch.sigmoid(torch.randn(L, H, D, generator=g_) * streuung)
            beta = torch.sigmoid(torch.randn(L, H, generator=g_))
            S0 = torch.randn(H, D, D, generator=g_) * 0.1
            ref, S_ref = _kda_referenz(q, k, v, g, beta, S0.clone())
            S = S0.clone()
            out = B.kda_blockweise(q, k, v, g, beta, S, 16)
            self.assertTrue(torch.isfinite(out).all())
            self.assertLess((out - ref).abs().max().item(), 1e-5, L)
            self.assertLess((S - S_ref).abs().max().item(), 1e-5, L)


@unittest.skipIf(torch is None, "torch fehlt")
class MLATests(unittest.TestCase):

    def test_absorbiert_gleich_ausgeschrieben(self):
        g_ = torch.Generator().manual_seed(3)
        H, E, nope, rd, lora, vd, qr, L = 4, 24, 8, 4, 16, 6, 12, 5
        r = lambda *s: torch.randn(*s, generator=g_) * 0.3
        m = B._MLALayer.__new__(B._MLALayer)
        m.H, m.eps, m.nope, m.rope_d, m.lora, m.vd = H, 1e-6, nope, rd, lora, vd
        m.rope = C.RoPE(rd, 10000.0, "cpu", max_pos=64, neox=False)
        m.q_a, m.q_a_norm, m.q_b = C.Linear(r(qr, E)), 1 + r(qr), C.Linear(r(H * (nope + rd), qr))
        m.kv_a, m.kv_a_norm = C.Linear(r(lora + rd, E)), 1 + r(lora)
        m.k_b, m.v_b = r(H, lora, nope), r(H, vd, lora)
        m.gate, m.o = C.Linear(r(H, E)), C.Linear(r(E, H * vd))
        m.cache = C.KVCache(1, lora + rd, 64, "cpu", torch.float32)
        m.scale = (nope + rd) ** -0.5
        x = r(L, E)
        pos = torch.arange(L)
        out = m(x, pos, L)
        # ausgeschrieben wie im HF-Modell: Schlüssel und Werte je Kopf aus dem Latent
        q = m.q_b(C.rms_norm(m.q_a(x), m.q_a_norm, 1e-6)).view(L, H, nope + rd)
        kv = m.kv_a(x)
        lat = C.rms_norm(kv[:, :lora], m.kv_a_norm, 1e-6)
        k_nope = torch.einsum("lc,hcn->lhn", lat, m.k_b)
        wert = torch.einsum("lc,hvc->lhv", lat, m.v_b)
        q_rot = m.rope(q[..., nope:].contiguous(), pos)
        k_rot = m.rope(kv[:, lora:].reshape(L, 1, rd), pos).expand(L, H, rd)
        qq, kk = torch.cat([q[..., :nope], q_rot], -1), torch.cat([k_nope, k_rot], -1)
        s = torch.einsum("ihd,jhd->hij", qq, kk) * m.scale
        s = s.masked_fill(torch.triu(torch.ones(L, L, dtype=torch.bool), 1), float("-inf"))
        o = torch.einsum("hij,jhv->ihv", s.softmax(-1), wert) * torch.sigmoid(m.gate(x)).unsqueeze(-1)
        self.assertTrue(torch.allclose(out, m.o(o.reshape(L, -1)), atol=1e-5))


@unittest.skipIf(torch is None, "torch fehlt")
class RouterTests(unittest.TestCase):

    def test_gruppenauswahl_wie_referenz(self):
        g_ = torch.Generator().manual_seed(5)
        n_exp, gruppen, E, L = 32, 4, 8, 20
        m = B._MoE.__new__(B._MoE)
        m.hp = dict(n_group=gruppen, group_used=2, n_used=4, norm_topk=True, weights_scale=2.5)
        m.router, m.bias = torch.randn(n_exp, E, generator=g_), torch.randn(n_exp, generator=g_) * 0.1
        x = torch.randn(L, E, generator=g_)
        idx, w = m._auswahl(x)
        # Referenz: BailingMoeV3Gate (group_limited_topk)
        scores = torch.sigmoid(x @ m.router.t())
        wahl = scores + m.bias
        gs = wahl.view(L, gruppen, -1).topk(2, dim=-1)[0].sum(-1)
        maske = torch.zeros_like(gs).scatter_(1, gs.topk(2, dim=-1)[1], 1)
        maske = maske.unsqueeze(-1).expand(L, gruppen, n_exp // gruppen).reshape(L, -1)
        ref_idx = wahl.masked_fill(~maske.bool(), float("-inf")).topk(4, dim=-1)[1]
        ref_w = scores.gather(1, ref_idx)
        ref_w = ref_w / (ref_w.sum(-1, keepdim=True) + 1e-20) * 2.5
        self.assertTrue(torch.equal(idx, ref_idx))
        self.assertTrue(torch.allclose(w, ref_w))


class ChatFormatTests(unittest.TestCase):

    def test_format_wie_vorlage(self):
        from types import SimpleNamespace as NS
        from ggufengine.chat import ChatFormatter, Message, detect_format
        self.assertEqual(detect_format("{{- '<role>HUMAN</role>' + message.content }}", "bailingmoe3"), "bailing")
        spezial = {"<role>": 1, "</role>": 2, "<|role_end|>": 3, "<think>": 4, "</think>": 5}
        text = []
        tok = NS(special=spezial, special_id=spezial.__getitem__, bos_id=None,
                 encode=lambda s: text.append(s) or [9])
        f = ChatFormatter(tok, "bailing")
        ids = f.encode([Message("system", "S"), Message("user", "a"), Message("assistant", "b", ids=[7])])
        # <role>SYSTEM</role>S\ndetailed thinking off<|role_end|><role>HUMAN</role>a<|role_end|>
        # <role>ASSISTANT</role>\n<think></think>7<|role_end|><role>ASSISTANT</role>\n<think></think>
        self.assertEqual(ids, [1, 9, 2, 9, 3, 1, 9, 2, 9, 3, 1, 9, 2, 9, 4, 5, 7, 3, 1, 9, 2, 9, 4, 5])
        self.assertEqual(text[:4], ["SYSTEM", "S\ndetailed thinking off", "HUMAN", "a"])
        self.assertFalse(f.opens_thinking)
        f.thinking = True
        self.assertEqual(f.encode([Message("user", "a")])[-2:], [9, 4])     # "\n" <think>: Denkblock offen
        self.assertTrue(f.opens_thinking)


if __name__ == "__main__":
    unittest.main()
