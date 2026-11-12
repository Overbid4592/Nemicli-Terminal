"""Gemma 4 ohne Download: winzige Zufallsmodelle als GGUF, gerechnet vom Motor und von einer
unabhängigen Nachrechnung des llama.cpp-Graphen (src/models/gemma4.cpp).

Abgedeckt: Kopfzahlen je Schicht, K=V ohne attn_v, geteilte KV-Schichten, anteiliges RoPE
über rope_freqs, Softcap, MoE parallel zum MLP, Q/K/V und Gate/Up zusammengefasst.

python -m unittest tests.test_gemma4_familie
"""

import gc
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import torch                                        # noqa: E402
import torch.nn.functional as F                     # noqa: E402

V, E, H, D_SWA, D_VOLL, FF, FF_EXP, FENSTER, N_CTX = 96, 64, 4, 16, 32, 96, 48, 4, 48
MUSTER = [True, True, False, True, True, False]
KV = [2 if s else 1 for s in MUSTER]               # Voll-Schichten mit weniger KV-Köpfen


def _baue(moe=False, n_exp=4, n_used=2, geteilt=0, qkv_fused=False, gate_up_fused=False, seed=0):
    g = torch.Generator().manual_seed(seed)
    r = lambda *s, sd=0.2: torch.randn(*s, generator=g) * sd
    n1 = lambda n: 1.0 + r(n, sd=0.1)
    n = len(MUSTER)
    meta = {
        "general.architecture": "gemma4", "gemma4.block_count": n, "gemma4.embedding_length": E,
        "gemma4.attention.head_count": H, "gemma4.attention.head_count_kv": KV,
        "gemma4.attention.key_length": D_VOLL, "gemma4.attention.value_length": D_VOLL,
        "gemma4.attention.key_length_swa": D_SWA, "gemma4.attention.value_length_swa": D_SWA,
        "gemma4.attention.sliding_window": FENSTER, "gemma4.attention.sliding_window_pattern": MUSTER,
        "gemma4.attention.shared_kv_layers": geteilt, "gemma4.attention.layer_norm_rms_epsilon": 1e-6,
        "gemma4.rope.freq_base": 1000000.0, "gemma4.rope.freq_base_swa": 10000.0,
        "gemma4.final_logit_softcapping": 30.0, "gemma4.feed_forward_length": FF,
    }
    if moe:
        meta.update({"gemma4.expert_count": n_exp, "gemma4.expert_used_count": n_used,
                     "gemma4.expert_feed_forward_length": FF_EXP})
    anteil = D_VOLL // 4                              # halbe Rotation: Rest mit Faktor 1e30
    t = {"token_embd.weight": r(V, E, sd=0.5), "output_norm.weight": n1(E), "output.weight": r(V, E),
         "rope_freqs.weight": torch.tensor([1.0] * anteil + [1e30] * (D_VOLL // 2 - anteil))}
    for i, swa in enumerate(MUSTER):
        p, d, hkv = f"blk.{i}.", (D_SWA if swa else D_VOLL), KV[i]
        t[p + "attn_norm.weight"] = n1(E)
        q, k, v = r(H * d, E), r(hkv * d, E), r(hkv * d, E)
        t[p + "attn_q.weight"] = q
        if i < n - geteilt:
            t[p + "attn_k.weight"] = k
            if swa:                                   # Voll-Schichten: V = K (kein attn_v)
                t[p + "attn_v.weight"] = v
            t[p + "attn_k_norm.weight"] = n1(d)
        t[p + "attn_q_norm.weight"] = n1(d)
        t[p + "attn_output.weight"] = r(E, H * d)
        t[p + "post_attention_norm.weight"] = n1(E)
        t[p + "ffn_norm.weight"] = n1(E)
        t[p + "ffn_gate.weight"], t[p + "ffn_up.weight"], t[p + "ffn_down.weight"] = r(FF, E), r(FF, E), r(E, FF)
        t[p + "post_ffw_norm.weight"] = n1(E)
        t[p + "layer_output_scale.weight"] = torch.tensor([0.5 + 0.1 * i])
        if moe:
            t[p + "ffn_gate_inp.weight"] = r(n_exp, E, sd=1.0)
            t[p + "ffn_gate_inp.scale"] = n1(E)
            t[p + "pre_ffw_norm_2.weight"] = n1(E)
            t[p + "post_ffw_norm_1.weight"] = n1(E)
            t[p + "post_ffw_norm_2.weight"] = n1(E)
            t[p + "ffn_gate_exps.weight"] = r(n_exp, FF_EXP, E)
            t[p + "ffn_up_exps.weight"] = r(n_exp, FF_EXP, E)
            t[p + "ffn_down_exps.weight"] = r(n_exp, E, FF_EXP)
            t[p + "ffn_down_exps.scale"] = 0.5 + torch.rand(n_exp, generator=g)
    if qkv_fused:
        for i in range(n - geteilt):
            p = f"blk.{i}."
            kk = t[p + "attn_k.weight"]
            vv = t.pop(p + "attn_v.weight", kk)
            t[p + "attn_qkv.weight"] = torch.cat([t.pop(p + "attn_q.weight"), t.pop(p + "attn_k.weight"), vv])
    if gate_up_fused and moe:
        for i in range(n):
            p = f"blk.{i}."
            t[p + "ffn_gate_up_exps.weight"] = torch.cat([t.pop(p + "ffn_gate_exps.weight"),
                                                          t.pop(p + "ffn_up_exps.weight")], dim=1)
    return meta, t


# -- unabhängige Nachrechnung ----------------------------------------------------------
def _rms(x, w=None, eps=1e-6):
    y = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps)
    return y * w if w is not None else y


def _rope(x, base, faktoren=None):
    L, _, d = x.shape
    inv = base ** (-torch.arange(0, d, 2, dtype=torch.float64) / d)
    if faktoren is not None:
        inv = inv / faktoren.double()
    w = torch.arange(L, dtype=torch.float64)[:, None] * inv[None]
    c, s = w.cos().float()[:, None], w.sin().float()[:, None]
    a, b = x[..., :d // 2], x[..., d // 2:]
    return torch.cat([a * c - b * s, a * s + b * c], -1)


def _gelu(x):
    return F.gelu(x, approximate="tanh")


def _referenz(meta, t, ids):
    n = meta["gemma4.block_count"]
    geteilt = meta["gemma4.attention.shared_kv_layers"]
    L = len(ids)
    h = t["token_embd.weight"][ids] * math.sqrt(E)
    kv_von, letzte = {}, {}
    for i, swa in enumerate(MUSTER):
        p, d, hkv = f"blk.{i}.", (D_SWA if swa else D_VOLL), KV[i]
        x = _rms(h, t[p + "attn_norm.weight"])
        if p + "attn_qkv.weight" in t:
            q, k, v = (x @ t[p + "attn_qkv.weight"].T).split([H * d, hkv * d, hkv * d], -1)
        else:
            q = x @ t[p + "attn_q.weight"].T
            if i < n - geteilt:
                k = x @ t[p + "attn_k.weight"].T
                v = x @ t[p + "attn_v.weight"].T if p + "attn_v.weight" in t else k
        base = meta["gemma4.rope.freq_base_swa" if swa else "gemma4.rope.freq_base"]
        fk = None if swa else t["rope_freqs.weight"]
        q = _rope(_rms(q.view(L, H, d), t[p + "attn_q_norm.weight"]), base, fk)
        if i < n - geteilt:
            k = _rope(_rms(k.reshape(L, hkv, d), t[p + "attn_k_norm.weight"]), base, fk)
            v = _rms(v.reshape(L, hkv, d))
            kv_von[i] = (k, v)
            letzte[swa] = i
        k, v = kv_von[letzte[swa]]
        k, v = k.repeat_interleave(H // hkv, 1), v.repeat_interleave(H // hkv, 1)
        s = torch.einsum("qhd,khd->hqk", q, k)                        # Skala 1.0
        qi, ki = torch.arange(L)[:, None], torch.arange(L)[None]
        maske = (ki > qi) | ((qi - ki >= FENSTER) if swa else torch.zeros_like(ki > qi))
        o = torch.einsum("hqk,khd->qhd", s.masked_fill(maske, float("-inf")).softmax(-1), v).reshape(L, -1)
        h = h + _rms(o @ t[p + "attn_output.weight"].T, t[p + "post_attention_norm.weight"])
        y = _rms(h, t[p + "ffn_norm.weight"])
        f = (_gelu(y @ t[p + "ffn_gate.weight"].T) * (y @ t[p + "ffn_up.weight"].T)) @ t[p + "ffn_down.weight"].T
        if p + "ffn_gate_inp.weight" in t:
            rr = _rms(h) / math.sqrt(E) * t[p + "ffn_gate_inp.scale"]
            wk = (rr @ t[p + "ffn_gate_inp.weight"].T).softmax(-1)
            w, idx = wk.topk(meta["gemma4.expert_used_count"], -1)
            w = w / w.sum(-1, keepdim=True)
            xm = _rms(h, t[p + "pre_ffw_norm_2.weight"])
            moe = torch.zeros_like(h)
            for zeile in range(L):
                for j in range(idx.shape[1]):
                    e = int(idx[zeile, j])
                    if p + "ffn_gate_up_exps.weight" in t:
                        gg, uu = (t[p + "ffn_gate_up_exps.weight"][e] @ xm[zeile]).chunk(2)
                    else:
                        gg = t[p + "ffn_gate_exps.weight"][e] @ xm[zeile]
                        uu = t[p + "ffn_up_exps.weight"][e] @ xm[zeile]
                    y_e = t[p + "ffn_down_exps.weight"][e] @ (_gelu(gg) * uu)
                    moe[zeile] += w[zeile, j] * t[p + "ffn_down_exps.scale"][e] * y_e
            f = _rms(f, t[p + "post_ffw_norm_1.weight"]) + _rms(moe, t[p + "post_ffw_norm_2.weight"])
        h = (h + _rms(f, t[p + "post_ffw_norm.weight"])) * t[p + "layer_output_scale.weight"]
    logits = _rms(h, t["output_norm.weight"]) @ t["output.weight"].T
    return 30.0 * torch.tanh(logits / 30.0)


class Gemma4Familie(unittest.TestCase):

    def _motor(self, meta, t, ids, vorab=9):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFError, GGUFFile
        from ggufengine.models import build_model
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        fehler = None
        try:
            B.schreibe_gguf(pfad, meta, t)
            gg = GGUFFile(pfad)
            try:
                modell = build_model(gg, torch.device("cpu"), torch.float32, N_CTX, quant=False)
                self.assertEqual(type(modell).__name__, "Gemma4Model")
                aus = [modell.forward(ids[:vorab], 0)]
                aus += [modell.forward([ids[j]], j) for j in range(vorab, len(ids))]
                del modell
            except GGUFError as err:                  # ohne Traceback weiterreichen: er hält die Datei offen
                fehler = GGUFError(str(err))
            finally:
                gc.collect()
                gg.close()
        finally:
            os.unlink(pfad)
        if fehler is not None:
            raise fehler
        return torch.stack(aus)

    def _pruefe(self, name, **kw):
        meta, t = _baue(**kw)
        ids = torch.randint(0, V, (14,), generator=torch.Generator().manual_seed(1)).tolist()
        ref = _referenz(meta, t, ids)[8:]
        aus = self._motor(meta, t, ids)
        diff = float((aus - ref).abs().max())
        self.assertLess(diff, 2e-3 * max(1.0, float(ref.abs().max())), f"{name}: Abweichung {diff:.2e}")
        return aus

    def test_dicht_wie_12b_31b(self):
        self._pruefe("dicht")

    def test_geteilte_kv_schichten(self):
        self._pruefe("geteilt", geteilt=2)

    def test_moe_wie_26b_a4b(self):
        self._pruefe("moe", moe=True)

    def test_zusammengefasst_gleich(self):
        getrennt = self._pruefe("moe", moe=True)
        zusammen = self._pruefe("moe+fused", moe=True, qkv_fused=True, gate_up_fused=True)
        self.assertLess(float((getrennt - zusammen).abs().max()), 1e-4)

    def test_kaputte_experten_abgelehnt(self):
        from ggufengine.gguf import GGUFError
        meta, t = _baue(moe=True)
        t["blk.0.ffn_down_exps.scale"] = torch.ones(3)                # falsche Anzahl
        with self.assertRaises(GGUFError):
            self._motor(meta, t, [1, 2, 3], vorab=3)
        meta, t = _baue()
        meta["gemma4.attention.head_count_kv"] = [2, 2, 0, 2, 2, 1]   # 0 Köpfe
        with self.assertRaises(GGUFError):
            self._motor(meta, t, [1, 2, 3], vorab=3)


if __name__ == "__main__":
    unittest.main()
