"""SDPA-Kerne sind prozessweit: eine Umschaltung in einem Faden darf andere Fäden nicht ohne Kern lassen.

Anlass: musik_machen scheiterte mit „No available kernel“, während der eigene Motor (Embedding, Bildbeschreiber)
auf „nur cuDNN“ stand; zwei überkreuzte Umschaltungen hinterließen „nur cuDNN“ dauerhaft.

python -m unittest tests.test_sdpa_faeden
"""

import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import torch.nn.functional as F
    CUDA = torch.cuda.is_available()
except Exception:                                       # ohne torch
    torch = None
    CUDA = False


class KeinAusschlussImCode(unittest.TestCase):
    """Jede Umschaltung der SDPA-Kerne muss eine Reihenfolge sein (set_priority=True), kein Ausschluss."""

    def test_nur_reihenfolgen(self):
        funde = []
        for ordner in ("ggufengine", "engines", "trtmotor", "loramotor", "core", "tools"):
            for p in (ROOT / ordner).rglob("*.py"):
                text = p.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"sdpa_kernel\(", text):
                    aufruf = text[m.start():text.find(")", text.find("]", m.start()) + 1) + 1]
                    if "set_priority=True" not in aufruf:
                        funde.append(f"{p.relative_to(ROOT)}: {aufruf[:80]}")
                if re.search(r"enable_(math|mem_efficient|flash|cudnn)_sdp\(\s*False", text):
                    funde.append(f"{p.relative_to(ROOT)}: schaltet einen SDPA-Kern ab")
        self.assertEqual([], funde)


@unittest.skipUnless(CUDA, "CUDA fehlt")
class FaedenTests(unittest.TestCase):

    def setUp(self):
        from ggufengine.models.common import cudnn_zuerst
        self.cudnn_zuerst = cudnn_zuerst
        self.q = torch.randn(2, 16, 40, 64, device="cuda", dtype=torch.float32)       # fp32: kann cuDNN nicht
        self.kv = torch.randn(2, 8, 40, 64, device="cuda", dtype=torch.float32)

    def _alle_kerne_an(self):
        b = torch.backends.cuda
        return b.math_sdp_enabled() and b.mem_efficient_sdp_enabled() and b.cudnn_sdp_enabled()

    def test_anderer_faden_rechnet_weiter(self):
        drin, raus = threading.Event(), threading.Event()

        def motor():
            with self.cudnn_zuerst():
                drin.set()
                raus.wait(10)
        t = threading.Thread(target=motor)
        t.start()
        drin.wait(10)
        try:
            out = F.scaled_dot_product_attention(self.q, self.kv, self.kv, enable_gqa=True)   # wie die Musik
            self.assertEqual(tuple(out.shape), (2, 16, 40, 64))
        finally:
            raus.set()
            t.join(10)

    def test_ueberkreuztes_ende_laesst_alle_kerne_an(self):
        e1, e2, x1, x2, f1, f2 = (threading.Event() for _ in range(6))

        def lauf(rein, darf_raus, fertig):
            with self.cudnn_zuerst():
                rein.set()
                darf_raus.wait(10)
            fertig.set()
        a = threading.Thread(target=lauf, args=(e1, x1, f1))
        b = threading.Thread(target=lauf, args=(e2, x2, f2))
        a.start()
        e1.wait(10)
        b.start()
        e2.wait(10)
        x1.set()
        f1.wait(10)                                     # der erste endet zuerst, der zweite danach
        x2.set()
        f2.wait(10)
        a.join(10)
        b.join(10)
        self.assertTrue(self._alle_kerne_an())
        F.scaled_dot_product_attention(self.q, self.kv, self.kv, enable_gqa=True)

    def test_cudnn_bleibt_vorn(self):
        q = torch.randn(1, 8, 64, 64, device="cuda", dtype=torch.bfloat16)
        with self.cudnn_zuerst():
            out = F.scaled_dot_product_attention(q, q, q)
        self.assertEqual(out.dtype, torch.bfloat16)


if __name__ == "__main__":
    unittest.main()
