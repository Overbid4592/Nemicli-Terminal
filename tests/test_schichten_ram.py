"""Schichten im RAM: Plan (welche Matrizen zuerst), Abbildung von festgesetztem RAM für die GPU
und ein Mini-Qwen3.5, das mit Schichten im VRAM, ganz im RAM und gemischt dasselbe rechnet.

python -m unittest tests.test_schichten_ram
"""

import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None

Q8_0, IQ3_S, F32 = 8, 21, 0


def _ti(name, form, typ=IQ3_S):
    n = 1
    for d in form:
        n *= d
    bytes_ = n * 4 if typ == F32 else n // 256 * 110
    return NS(name=name, shape=form, ggml_type=typ, n_elements=n, n_bytes=bytes_)


def _gg(tensoren, n_schichten=3, mtp=1):
    """Kopf eines dichten qwen35-artigen Modells; Formen wie GGUF (out, in)."""
    meta = {"qwen35.block_count": n_schichten + mtp, "qwen35.nextn_predict_layers": mtp}
    return NS(architecture="qwen35", tensors={t.name: t for t in tensoren}, get=lambda k, d=None: meta.get(k, d))


def _modell_tensoren(n=3, mtp=1):
    t = [_ti("token_embd.weight", (4096, 1024)), _ti("output.weight", (4096, 1024)),
         _ti("output_norm.weight", (1024,), F32)]
    for i in range(n + mtp):
        t += [_ti(f"blk.{i}.attn_qkv.weight", (1024, 2048)), _ti(f"blk.{i}.ffn_up.weight", (1024, 4096)),
              _ti(f"blk.{i}.ffn_down.weight", (4096, 1024)), _ti(f"blk.{i}.attn_norm.weight", (1024,), F32)]
    return t


@unittest.skipIf(torch is None, "torch fehlt")
class PlanTests(unittest.TestCase):
    def test_ohne_budget_oder_genug_platz_bleibt_alles_im_vram(self):
        from ggufengine import schichten as S
        gg = _gg(_modell_tensoren())
        self.assertEqual(S.planen(gg, None).ram, frozenset())
        self.assertEqual(S.planen(gg, S.gewichte_bytes(gg)).ram, frozenset())

    def test_reihenfolge_einbettung_dann_ffn_hinten_zuerst(self):
        from ggufengine import schichten as S
        gg = _gg(_modell_tensoren())
        voll = S.gewichte_bytes(gg)
        einbettung = gg.tensors["token_embd.weight"].n_bytes
        ffn = gg.tensors["blk.2.ffn_up.weight"].n_bytes
        band = S.BAND_PLAETZE * ffn                                     # VRAM-Plätze des Fließbands
        p = S.planen(gg, voll - einbettung)
        self.assertEqual(p.ram, {"token_embd.weight"})
        p = S.planen(gg, voll - einbettung - 1)
        self.assertEqual(p.ram, {"token_embd.weight"} | {f"blk.{i}.ffn_{a}.weight" for i in (2, 1) for a in ("up", "down")}
                         | {"blk.0.ffn_down.weight"})
        p = S.planen(gg, voll - einbettung - 6 * ffn + band)
        self.assertEqual(p.ram, {"token_embd.weight"} | {f"blk.{i}.ffn_{a}.weight" for i in range(3)
                                                          for a in ("up", "down")})
        self.assertLessEqual(p.vram_bytes, voll - einbettung - 6 * ffn + band)

    def test_alles_im_ram_laesst_kopf_normen_und_mtp_im_vram(self):
        from ggufengine import schichten as S
        gg = _gg(_modell_tensoren())
        p = S.planen(gg, 0)
        self.assertNotIn("output.weight", p.ram)
        self.assertFalse(any("norm" in n or n.startswith("blk.3.") for n in p.ram))
        self.assertEqual(len(p.ram), 1 + 3 * 3)
        self.assertEqual(p.ram_bytes, sum(gg.tensors[n].n_bytes for n in p.ram))
        band = S.BAND_PLAETZE * gg.tensors["blk.0.ffn_up.weight"].n_bytes
        self.assertEqual(p.vram_bytes, S.gewichte_bytes(gg) - p.ram_bytes + band)

    def test_gebundene_einbettung_bleibt(self):
        from ggufengine import schichten as S
        gg = _gg([t for t in _modell_tensoren() if t.name != "output.weight"])
        self.assertNotIn("token_embd.weight", S.planen(gg, 0).ram)

    def test_kleine_matrizen_bleiben(self):
        from ggufengine import schichten as S
        gg = _gg([_ti("blk.0.ssm_alpha.weight", (48, 5120), Q8_0)], n_schichten=1, mtp=0)
        self.assertEqual(S.planen(gg, 0).ram, frozenset())
        self.assertEqual(S.planen(gg, 0, min_werte=0).ram, {"blk.0.ssm_alpha.weight"})


@unittest.skipIf(torch is None or not torch.cuda.is_available(), "keine CUDA-GPU")
class AbbildenTests(unittest.TestCase):
    def test_gpu_liest_den_ram_an_derselben_adresse(self):
        from ggufengine import hostmem
        host = hostmem.empty(4096, "cuda")
        host.numpy()[:] = 5
        sicht = hostmem.abbilden(host, "cuda")
        self.assertTrue(sicht.is_cuda)
        self.assertTrue(hostmem.abgebildet(sicht) and hostmem.abgebildet(host[100:]))
        self.assertFalse(hostmem.abgebildet(torch.zeros(4, device="cuda")))
        self.assertEqual(int(sicht.float().sum()), 5 * 4096)
        with self.assertRaises(ValueError):
            hostmem.abbilden(torch.zeros(8, dtype=torch.uint8).pin_memory(), "cuda")
        del sicht, host
        gc.collect()

    def test_geparktes_modell_laesst_ram_schichten_liegen(self):
        from ggufengine import hostmem
        from ggufengine.engine import _im_vram
        host = hostmem.empty(1024, "cuda")
        self.assertFalse(_im_vram(hostmem.abbilden(host, "cuda")))
        self.assertTrue(_im_vram(torch.zeros(4, device="cuda")))
        self.assertFalse(_im_vram(torch.zeros(4)))


def _q8_0(w: "torch.Tensor") -> bytes:
    q = (w.reshape(-1, 32).abs().amax(1, keepdim=True) / 127).clamp_min(1e-8).half()
    werte = (w.reshape(-1, 32) / q.float()).round().clamp(-127, 127).to(torch.int8)
    return torch.cat([q.view(torch.uint8).view(-1, 2), werte.view(torch.uint8)], dim=1).numpy().tobytes()


@unittest.skipIf(torch is None or T is None or not torch.cuda.is_available(), "keine CUDA-GPU")
class MiniQwen35Tests(unittest.TestCase):
    """Matrizen in Q8_0: im VRAM, ganz im RAM und gemischt nah an der genauen Rechnung (float32 auf
    der CPU) – beim Einlesen (Auspacken), mit wenigen Zeilen (mm-Kernel) und Schritt für Schritt
    (mv-Kernel im CUDA-Graph). Die Abweichung ist bf16-Rundung, im VRAM wie im RAM."""

    def test_vram_ram_gemischt(self):
        import gguf_testbau as B
        from test_neue_bauarten import _q35_cfg

        from ggufengine import schichten as S
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        from ggufengine.models import common as C
        torch.manual_seed(0)
        hf = T.Qwen3_5ForCausalLM(_q35_cfg(T.Qwen3_5TextConfig)).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        meta, t = B.umwandeln(hf, "qwen35")
        for name, w in list(t.items()):
            if isinstance(w, torch.Tensor) and w.dim() == 2 and w.shape[1] % 32 == 0:
                t[name] = (Q8_0, tuple(w.shape), _q8_0(w))
        ids = torch.randint(3, 96, (30,)).tolist()
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, "q35.gguf")
            B.schreibe_gguf(pfad, meta, t)
            ergebnisse, im_ram = {}, {}
            for art in ("genau", "vram", "ram", "gemischt"):
                gg = GGUFFile(pfad)
                try:
                    voll = S.gewichte_bytes(gg)
                    budget = {"genau": None, "vram": None, "ram": 0, "gemischt": voll // 2}[art]
                    plan = S.planen(gg, budget, min_werte=0)
                    C.SCHICHTEN_RAM = plan.ram
                    try:
                        m = (build_model(gg, torch.device("cpu"), torch.float32, 64, quant=False) if art == "genau"
                             else build_model(gg, torch.device("cuda"), torch.bfloat16, 64))
                    finally:
                        C.SCHICHTEN_RAM = frozenset()
                    gepackt = [x for b in m.blocks for x in vars(b).values() if isinstance(x, C.PackedLinear)]
                    gepackt += [x for b in m.blocks for x in vars(b.mixer).values() if isinstance(x, C.PackedLinear)]
                    im_ram[art] = (len(plan.ram), sum(x.im_ram for x in gepackt))
                    aus = [m.forward(ids[:20], 0).float().cpu(), m.forward(ids[20:25], 20).float().cpu()]
                    aus += [m.forward([ids[j]], j).float().cpu() for j in range(25, 30)]
                    ergebnisse[art] = (torch.stack(aus), m.graph is not None)
                    del m, gepackt
                finally:
                    gc.collect()
                    gg.close()
        self.assertEqual(im_ram["vram"], (0, 0))
        self.assertGreater(im_ram["ram"][1], 0)
        self.assertGreater(im_ram["gemischt"][0], 0)
        self.assertGreater(im_ram["ram"][0], im_ram["gemischt"][0])
        ref = ergebnisse["genau"][0]
        for art in ("vram", "ram", "gemischt"):
            with self.subTest(art):
                a, graph = ergebnisse[art]
                self.assertTrue(graph)
                self.assertLess(float((a - ref).abs().max() / ref.abs().max()), 5e-2)


if __name__ == "__main__":
    unittest.main()
