"""trtmotor.video: die Wan-Blöcke über TensorRT – gleiche Rechnung wie WanDiT.block_lauf. Zwei Blöcke mit
Zufallsgewichten, drei Latent-Bilder, nichts wird geladen. Läuft nur mit Grafikkarte und TensorRT-RTX.

python -m unittest tests.test_trtmotor_video
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import video as V
    from trtmotor import laufzeit as L, video as TV
    BEREIT = torch.cuda.is_available() and L.bereit()
except Exception:
    torch = V = TV = L = None
    BEREIT = False

D, HEADS, FF, LAYER = 256, 2, 512, 2
BILDER, HOCH, BREIT = 3, 4, 4                      # Token je Latent-Bild: 16
CFG = {"dim": D, "num_heads": HEADS, "num_layers": LAYER, "ffn_dim": FF, "eps": 1e-6, "text_len": 512,
       "patch_size": [1, 2, 2], "out_dim": 48}


def _sd():
    torch.manual_seed(3)
    sd = {}
    r = lambda *s, f=0.05: (torch.randn(*s) * f).to(torch.bfloat16)
    for i in range(LAYER):
        p = f"blocks.{i}."
        sd[p + "modulation"] = r(1, 6, D, f=0.1)
        for m in ("self_attn", "cross_attn"):
            for z in "qkvo":
                sd[p + f"{m}.{z}.weight"], sd[p + f"{m}.{z}.bias"] = r(D, D), r(D, f=0.02)
            sd[p + f"{m}.norm_q.weight"] = 1 + r(D, f=0.1)
            sd[p + f"{m}.norm_k.weight"] = 1 + r(D, f=0.1)
        sd[p + "norm3.weight"], sd[p + "norm3.bias"] = 1 + r(D, f=0.1), r(D, f=0.05)
        sd[p + "ffn.0.weight"], sd[p + "ffn.0.bias"] = r(FF, D), r(FF, f=0.02)
        sd[p + "ffn.2.weight"], sd[p + "ffn.2.bias"] = r(D, FF), r(D, f=0.02)
    return sd


@unittest.skipUnless(BEREIT, "Grafikkarte oder TensorRT-RTX fehlt")
class WanBloecke(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(L, "engine_ordner", lambda: Path(self.tmp.name) / "engines")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(TV.entladen)
        self.pfad = Path(self.tmp.name) / "wan_test.gguf"
        self.pfad.write_bytes(b"x")

    def _vergleich(self, fmt: str, grenze: float):
        ram = _sd()
        gpu = {k: v.cuda() for k, v in ram.items()}
        dit = V.WanDiT(gpu, CFG)
        torch.manual_seed(4)
        S = BILDER * HOCH * BREIT
        freqs = dit._freqs(BILDER, HOCH, BREIT, "cuda")
        c = (torch.randn(512, D, device="cuda") * 0.5).to(torch.bfloat16)
        faelle = [(torch.randn(BILDER, S // BILDER, D, device="cuda"), torch.randn(BILDER, 6, D, device="cuda") * 0.1),
                  (torch.randn(1, S, D, device="cuda"), torch.randn(1, 6, D, device="cuda") * 0.1)]
        with torch.no_grad():
            refs = [dit.block_lauf(x, e0, c, freqs, torch.bfloat16) for x, e0 in faelle]
        with patch.object(TV, "format_wahl", lambda: fmt):
            arbeit = {k: v.cuda() for k, v in ram.items()}
            TV.einmessen(self.pfad, arbeit, LAYER)
            with torch.no_grad():
                V.WanDiT(arbeit, CFG).block_lauf(*faelle[0], c, freqs, torch.bfloat16)
            TV.vorbereiten(self.pfad, CFG, ram, warten=True)
            rechnen = TV.bloecke(self.pfad)
        self.assertIsNotNone(rechnen, TV._stand[str(self.pfad)].get("fehler"))
        for (x, e0), ref in zip(faelle, refs):
            aus = rechnen(x, e0, c, freqs, BILDER)
            self.assertEqual(ref.shape, aus.shape)
            d_ref, d_aus = (ref - x).flatten(), (aus - x).flatten()
            cos = float(torch.nn.functional.cosine_similarity(d_aus, d_ref, 0))
            self.assertGreater(cos, grenze, f"{fmt}, Gruppen {x.shape[0]}")
        TV.nach_video(self.pfad)
        self.assertEqual(0, TV._stand[str(self.pfad)]["netz"].budget)

    def test_fp8_wie_der_videomotor(self):
        self._vergleich("fp8", 0.995)

    def test_fp4(self):
        self._vergleich("fp4", 0.97)


if __name__ == "__main__":
    unittest.main()
