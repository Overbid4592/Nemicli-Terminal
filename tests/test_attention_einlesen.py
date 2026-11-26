"""Einlesen mehrerer Token: unten rechts kausal (cuDNN bzw. effizienter Kern) = alte Rechnung mit Maske."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
    from ggufengine.models import common as C
    CUDA = torch.cuda.is_available()
except Exception:                                            # noqa: BLE001
    torch = C = None
    CUDA = False


def _mit_maske(q, k, v, q_pos, kv_len, scale):
    """Die bisherige Rechnung: ausgeschriebene Maske."""
    L, H, _ = q.shape
    kh, vh = k[:, :kv_len].unsqueeze(0), v[:, :kv_len].unsqueeze(0)
    maske = (q_pos.unsqueeze(1) - torch.arange(kv_len, device=q.device).unsqueeze(0)) >= 0
    qh = q.transpose(0, 1).unsqueeze(0).to(k.dtype)
    return torch.nn.functional.scaled_dot_product_attention(qh, kh, vh, attn_mask=maske, scale=scale,
                                                            enable_gqa=kh.shape[1] != H)[0].transpose(0, 1)


@unittest.skipUnless(CUDA, "braucht CUDA")
class KausalRechts(unittest.TestCase):
    def _vergleich(self, L, vorher, H, Hkv, D, Dv=None):
        torch.manual_seed(L + vorher + H)
        S = vorher + L
        q = torch.randn(L, H, D, device="cuda", dtype=torch.bfloat16)
        k = torch.randn(Hkv, S + 64, D, device="cuda", dtype=torch.bfloat16)        # Cache größer als kv_len
        v = torch.randn(Hkv, S + 64, Dv or D, device="cuda", dtype=torch.bfloat16)
        pos = torch.arange(vorher, S, device="cuda")
        neu = C.attention(q, k, v, pos, S, D ** -0.5)
        alt = _mit_maske(q, k, v, pos, S, D ** -0.5)
        self.assertEqual(neu.shape, alt.shape)
        self.assertTrue(torch.allclose(neu.float(), alt.float(), atol=2e-2, rtol=2e-2), (L, vorher, H, Hkv, D))

    def test_formen(self):
        for L, vorher, H, Hkv, D, Dv in ((64, 0, 32, 8, 128, None), (37, 500, 32, 8, 128, None),
                                         (200, 1000, 16, 16, 64, None), (48, 300, 16, 8, 256, None),
                                         (32, 100, 16, 1, 192, 128)):
            self._vergleich(L, vorher, H, Hkv, D, Dv)

    def test_ohne_cudnn_gleich(self):
        with mock.patch.dict(C._CUDNN_ATTENTION, {"geht": False}):
            self._vergleich(64, 700, 32, 8, 128)

    def test_grosse_kopie_nimmt_den_effizienten_kern(self):
        with mock.patch.object(C, "CUDNN_KOPIE_BIS", 1), mock.patch.dict(C._CUDNN_ATTENTION, {"geht": True}):
            self._vergleich(64, 700, 32, 8, 128)

    def test_graph_aufnahme_mehrerer_token_bleibt_beim_alten_weg(self):
        """In der Aufnahme ist kv_len aufgerundet: dort darf unten rechts nicht gelten."""
        q = torch.randn(4, 8, 64, device="cuda", dtype=torch.bfloat16)
        k = torch.randn(8, 512, 64, device="cuda", dtype=torch.bfloat16)
        pos = torch.arange(100, 104, device="cuda")
        with mock.patch.object(C, "_kausal_rechts", side_effect=AssertionError("falscher Weg")), \
                mock.patch.object(C._MEHRERE, "an", True, create=True):
            C.attention(q, k, k, pos, 256, 0.125)                                  # Maske: Positionen zählen


@unittest.skipIf(torch is None, "torch fehlt")
class WenigeZeilen(unittest.TestCase):
    """Bis WENIGE_ZEILEN Queries mit Maske (Ring-Cache, Fenster, Prüfen von Vorschlägen): Batch-Matmul
    statt SDPA, gleiches Ergebnis."""

    def test_gleich_sdpa(self):
        geraete = ["cpu"] + (["cuda"] if CUDA else [])
        for dev in geraete:
            for L, H, Hkv, S, fenster in ((2, 8, 2, 50, 0), (4, 8, 1, 300, 16), (C.WENIGE_ZEILEN, 4, 4, 33, 5)):
                torch.manual_seed(L + S)
                q = torch.randn(L, H, 16, device=dev)
                k, v = torch.randn(Hkv, S, 16, device=dev), torch.randn(Hkv, S, 24, device=dev)
                key_pos = torch.randperm(S, device=dev)                       # Ring: Plätze ungeordnet
                q_pos = torch.arange(S - L, S, device=dev)
                neu = C.attention(q, k, v, q_pos, S, 0.5, fenster, key_pos=key_pos)
                dist = q_pos.unsqueeze(1) - key_pos.unsqueeze(0)
                maske = (dist >= 0) & ((dist < fenster) if fenster else True)
                ref = torch.nn.functional.scaled_dot_product_attention(
                    q.transpose(0, 1)[None], k[None], v[None], attn_mask=maske, scale=0.5,
                    enable_gqa=Hkv != H)[0].transpose(0, 1)
                torch.testing.assert_close(neu.float(), ref, rtol=1e-4, atol=1e-4, msg=f"{dev} L={L}")


if __name__ == "__main__":
    unittest.main()
