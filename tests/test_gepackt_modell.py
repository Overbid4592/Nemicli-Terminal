"""Ganzes Modell mit gepackten Schichten (Format ohne int4-Weg): gleiche Logits wie dasselbe
Modell mit den ausgepackten Werten als F32 – über Einbettung, Attention, FFN und Kopf, beim
Einlesen (Auspacken) und Schritt für Schritt (Kernel, auf der GPU auch im CUDA-Graph).

python -m unittest tests.test_gepackt_modell
"""

import gc
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
    import transformers as T
    from ggufengine.gguf import GGUFFile
    from ggufengine.models import build_model
    from ggufengine.models.common import PackedLinear
    from ggufengine import iqtabellen as IT
    from ggufengine.quant import dequantize
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None

IQ4_NL = 20
V, E, FF = 128, 64, 96


def iq4_nl_packen(w: "torch.Tensor") -> bytes:
    """Einfacher IQ4_NL-Quantisierer: je 32 Werte eine fp16-Skala, nächster Tabellenwert."""
    kv = torch.tensor(IT.KVALUES_IQ4NL, dtype=torch.float32)
    b = w.reshape(-1, 32).float()
    d = (b.abs().amax(1, keepdim=True) / 127).clamp_min(1e-8).half()
    idx = ((b / d.float()).unsqueeze(-1) - kv).abs().argmin(-1).to(torch.uint8)       # (n, 32)
    qs = idx[:, :16] | (idx[:, 16:] << 4)
    return torch.cat([d.view(torch.uint8).view(-1, 2), qs], dim=1).numpy().tobytes()


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class GepacktesModellTests(unittest.TestCase):

    def _dateien(self, tmp):
        import gguf_testbau as B
        torch.manual_seed(0)
        cfg = T.LlamaConfig(vocab_size=V, hidden_size=E, intermediate_size=FF, num_hidden_layers=2,
                            num_attention_heads=4, num_key_value_heads=2, head_dim=16, max_position_embeddings=64,
                            rms_norm_eps=1e-6, tie_word_embeddings=True, pad_token_id=0, bos_token_id=1,
                            eos_token_id=2)
        hf = T.LlamaForCausalLM(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        meta, t = B.umwandeln(hf, "llama")
        t.pop("output.weight", None)                     # gebundener Kopf: aus der Einbettung
        gepackt, ausgepackt = dict(t), dict(t)
        for name, w in t.items():
            if w.dim() == 2 and w.shape[1] % 32 == 0:
                roh = iq4_nl_packen(w)
                gepackt[name] = (IQ4_NL, tuple(w.shape), roh)
                werte = dequantize(torch.frombuffer(bytearray(roh), dtype=torch.uint8), IQ4_NL, w.numel())
                ausgepackt[name] = werte.view(w.shape)
        pfade = []
        for tensoren in (gepackt, ausgepackt):
            pfad = os.path.join(tmp, f"m{len(pfade)}.gguf")
            B.schreibe_gguf(pfad, meta, tensoren)
            pfade.append(pfad)
        return pfade

    def _logits(self, pfad, device, ids, vorab):
        gg = GGUFFile(pfad)
        try:
            modell = build_model(gg, torch.device(device), torch.float32 if device == "cpu" else torch.bfloat16,
                                 64, quant=device != "cpu")
            schichten = [l for l in vars(modell.layers[0]).values() if isinstance(l, PackedLinear)]
            aus = [modell.forward(ids[:vorab], 0).float().reshape(-1)[-V:].cpu()]
            aus += [modell.forward([ids[j]], j).float().reshape(-1)[-V:].cpu() for j in range(vorab, len(ids))]
            ergebnis = torch.stack(aus), len(schichten), modell.graph is not None
            del modell, schichten                        # gepackte Schichten halten die Datei (CPU: mmap)
            return ergebnis
        finally:
            import gc
            gc.collect()
            gg.close()

    def test_gepackt_gleich_ausgepackt(self):
        ids = torch.randint(3, V, (14,)).tolist()
        with tempfile.TemporaryDirectory() as tmp:
            gepackt, ausgepackt = self._dateien(tmp)
            for device in ("cpu", "cuda") if torch.cuda.is_available() else ("cpu",):
                with self.subTest(device):
                    a, n_gepackt, graph = self._logits(gepackt, device, ids, 9)
                    b, n_aus, _ = self._logits(ausgepackt, device, ids, 9)
                    self.assertGreater(n_gepackt, 0)
                    self.assertEqual(n_aus, 0)
                    if device == "cuda":
                        self.assertTrue(graph)
                    toleranz = 1e-4 if device == "cpu" else 3e-2      # GPU: bf16
                    self.assertLess(float((a - b).abs().max() / b.abs().max()), toleranz)


@unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
class ExpertenImRamTests(unittest.TestCase):
    """Mini-MoE (Qwen3-MoE) auf der GPU: Experten im VRAM, ganz im RAM (der Kernel liest über
    PCIe) und gemischt nach Budget rechnen dasselbe, auch im CUDA-Graph."""

    def _moe_datei(self, tmp):
        import gguf_testbau as B
        torch.manual_seed(0)
        cfg = T.Qwen3MoeConfig(vocab_size=V, hidden_size=64, intermediate_size=96, moe_intermediate_size=64,
                               num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, head_dim=16,
                               num_experts=4, num_experts_per_tok=2, max_position_embeddings=64,
                               tie_word_embeddings=False, decoder_sparse_step=1, mlp_only_layers=[])
        hf = T.Qwen3MoeForCausalLM(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        meta, t = B.umwandeln(hf, "qwen3moe")
        for name in [k for k in t if "_exps." in k]:     # Experten im Dateiformat Q8_0
            w = t[name]
            q = (w.reshape(-1, 32).abs().amax(1, keepdim=True) / 127).clamp_min(1e-8).half()
            werte = (w.reshape(-1, 32) / q.float()).round().clamp(-127, 127).to(torch.int8)
            roh = torch.cat([q.view(torch.uint8).view(-1, 2), werte.view(torch.uint8)], dim=1)
            t[name] = (8, tuple(w.shape), roh.numpy().tobytes())
        pfad = os.path.join(tmp, "moe.gguf")
        B.schreibe_gguf(pfad, meta, t)
        return pfad

    def test_vram_ram_gemischt(self):
        from unittest import mock
        from ggufengine.models import common as C
        from ggufengine.models.common import PackedExperts
        ids = torch.randint(3, V, (12,)).tolist()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(C, "LAGER", False):
            pfad = self._moe_datei(tmp)
            ergebnisse, orte = {}, {}
            je_schicht = 3 * 4 * 64 * 64 * 2                      # eine Schicht im VRAM (zu schmal für int4: bf16)
            for name, budget in (("vram", None), ("ram", 0), ("gemischt", je_schicht)):
                gg = GGUFFile(pfad)
                m = build_model(gg, torch.device("cuda"), torch.bfloat16, 32, experten_vram=budget)
                aus = [m.forward(ids[:8], 0).float().reshape(-1)[-V:].cpu()]
                aus += [m.forward([ids[j]], j).float().reshape(-1)[-V:].cpu() for j in range(8, 12)]
                ergebnisse[name] = torch.stack(aus)
                orte[name] = [isinstance(teil.experten, PackedExperts) and not teil.experten.raw.is_cuda
                              for l in m.layers for teil in (l.experten.gate, l.experten.up, l.experten.down)]
                self.assertIsNotNone(m.graph, name)
                del m
                gc.collect()
                gg.close()
        self.assertEqual(orte["vram"], [False] * 6)
        self.assertEqual(orte["ram"], [True] * 6)
        self.assertEqual(orte["gemischt"], [False] * 3 + [True] * 3)     # erste Schicht passt, zweite nicht
        ref = ergebnisse["vram"]
        for name in ("ram", "gemischt"):
            rel = float((ergebnisse[name] - ref).abs().max() / ref.abs().max())
            self.assertLess(rel, 3e-2, name)

    def test_lager_rechnet_gleich_und_lernt(self):
        """Lager mit 2 von 4 Plätzen je Schicht: gleiche Logits wie im VRAM, vor und nach dem
        Umsortieren (Graph nimmt die neuen Adressen), die meistgewählten Experten belegen die Plätze."""
        from ggufengine.models.common import lager_plaetze
        ids = torch.randint(3, V, (16,)).tolist()
        with tempfile.TemporaryDirectory() as tmp:
            pfad = self._moe_datei(tmp)
            ergebnisse, lager = {}, None
            for name in ("vram", "lager"):
                gg = GGUFFile(pfad)
                je_slot = 2 * 3 * (64 * 64 // 32 * 34)              # 2 Schichten × gate/up/down in Q8_0
                budget = None if name == "vram" else 2 * je_slot
                if budget is not None:
                    self.assertEqual(lager_plaetze(gg, budget), 2)
                m = build_model(gg, torch.device("cuda"), torch.bfloat16, 32, experten_vram=budget)
                aus = [m.forward(ids[:8], 0).float().reshape(-1)[-V:].cpu()]
                aus += [m.forward([ids[j]], j).float().reshape(-1)[-V:].cpu() for j in range(8, 12)]
                if name == "lager":
                    lager = [l.experten.lager for l in m.layers]
                    self.assertTrue(all(x is not None and x.plaetze == 2 for x in lager))
                    zaehler = [x.zaehler.clone() for x in lager]
                    for x, z in zip(lager, zaehler):
                        x.umsortieren()
                        self.assertEqual(set(x.belegt), set(z.topk(2).indices.tolist()))
                        t = x.teile[0]
                        for p, e in enumerate(x.belegt):          # Adresse zeigt auf den VRAM-Platz
                            self.assertEqual(int(t.adressen[e]), x.cache[0][p].data_ptr())
                        x.cache = [c.clone() for c in x.cache]    # wie nach Parken und Zurückholen
                        x.verankern()
                        self.assertEqual(int(t.adressen[x.belegt[0]]), x.cache[0][0].data_ptr())
                        frei = next(e for e in range(4) if e not in x.belegt)
                        self.assertEqual(int(t.adressen[frei]), t.raw[frei].data_ptr())
                    self.assertIsNotNone(m.graph)
                    from ggufengine.engine import Engine           # Zählung sichern und wieder setzen
                    e = Engine.__new__(Engine)
                    e.model = m
                    zaehlung = e.experten_zaehlung()
                    self.assertEqual([len(z) for z in zaehlung], [4, 4])
                    for x in lager:
                        x.zaehler.zero_()
                    zaehlung[0] = [0.0, 0.0, 5.0, 9.0]
                    self.assertTrue(e.experten_zaehlung_setzen(zaehlung))
                    self.assertEqual(set(lager[0].belegt), {2, 3})
                    self.assertFalse(e.experten_zaehlung_setzen([[1.0, 2.0]]))                 # anderes Modell
                    self.assertFalse(e.experten_zaehlung_setzen([[1.0, -1.0, 0.0, 0.0]] * 2))   # unsinnige Werte
                aus += [m.forward([ids[j]], j).float().reshape(-1)[-V:].cpu() for j in range(12, 16)]
                ergebnisse[name] = torch.stack(aus)
                del m
                gc.collect()
                gg.close()
        ref = ergebnisse["vram"]
        self.assertLess(float((ergebnisse["lager"] - ref).abs().max() / ref.abs().max()), 3e-2)


if __name__ == "__main__":
    unittest.main()
