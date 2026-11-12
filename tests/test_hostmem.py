"""Festgesetzter RAM in genauer Größe: GPU-lesbar, lebt solange eine Sicht lebt, wird erst in
flush() freigegeben; offload/restore eines Modells über diese Blöcke.

python -m unittest tests.test_hostmem
"""

import gc
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None

CUDA = torch is not None and torch.cuda.is_available()


@unittest.skipUnless(CUDA, "braucht CUDA")
class HostmemTests(unittest.TestCase):
    def setUp(self):
        from ggufengine import hostmem
        self.hm = hostmem
        gc.collect()
        hostmem.flush()

    def test_genaue_groesse_und_gpu_lesbar(self):
        n = 3 * 2**20 + 7
        vorher = torch.cuda.host_memory_stats().get("allocated_bytes.current", 0)
        t = self.hm.empty(n, "cuda")
        self.assertEqual(t.numel(), n)
        self.assertTrue(t.is_pinned())
        self.assertEqual(torch.cuda.host_memory_stats().get("allocated_bytes.current", 0), vorher)
        t[:5] = torch.arange(5, dtype=torch.uint8)
        self.assertEqual(t[:5].to("cuda", non_blocking=True).tolist(), [0, 1, 2, 3, 4])
        g = torch.arange(5, dtype=torch.uint8, device="cuda") + 10
        t[n - 5:].copy_(g)
        self.assertEqual(t[n - 5:].tolist(), [10, 11, 12, 13, 14])

    def test_lebt_mit_sicht_und_wird_erst_in_flush_frei(self):
        t = self.hm.empty(2**20, "cuda")
        sicht = t[100:200].view(torch.int16)
        del t
        gc.collect()
        self.assertEqual(self.hm.pending(), 0)          # die Sicht hält den Block
        sicht.fill_(7)
        self.assertEqual(int(sicht[0]), 7)
        del sicht
        gc.collect()
        self.assertEqual(self.hm.pending(), 1)          # tot, aber noch nicht freigegeben
        self.hm.flush()
        self.assertEqual(self.hm.pending(), 0)

    def test_flush_nicht_waehrend_graph_aufnahme(self):
        t = self.hm.empty(4096, "cuda")
        del t
        gc.collect()
        x = torch.zeros(4, device="cuda")
        g = torch.cuda.CUDAGraph()
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            x.add_(1)                                    # Aufwärmen außerhalb der Aufnahme
            torch.cuda.synchronize()
            with torch.cuda.graph(g, stream=s):
                x.add_(1)
                self.hm.flush()                          # darf nichts tun
                self.assertEqual(self.hm.pending(), 1)
        g.replay()
        torch.cuda.synchronize()
        self.hm.flush()
        self.assertEqual(self.hm.pending(), 0)


@unittest.skipUnless(CUDA, "braucht CUDA")
class AuslagernTests(unittest.TestCase):
    def test_offload_restore_gleiche_werte(self):
        from ggufengine import hostmem
        from ggufengine.engine import Engine

        class Modell:
            def __init__(self):
                self.gross = torch.randn(1024, 1024, device="cuda")      # 4 MB: hostmem
                self.klein = torch.randn(16, device="cuda")              # torch-Pinned
                self.halb = torch.randn(2048, 512, device="cuda").to(torch.bfloat16)

        e = Engine.__new__(Engine)
        e.device = torch.device("cuda")
        e.model = Modell()
        soll = {k: v.clone() for k, v in vars(e.model).items()}
        e.offload()
        self.assertTrue(e.offloaded)
        self.assertTrue(all(v.device.type == "cpu" and v.is_pinned() for v in vars(e.model).values()))
        self.assertEqual(e.model.gross.numel(), 1024 * 1024)
        e.restore()
        for k, v in vars(e.model).items():
            self.assertTrue(v.is_cuda, k)
            self.assertTrue(torch.equal(v, soll[k]), k)
        gc.collect()
        hostmem.flush()
        self.assertEqual(hostmem.pending(), 0)

    @staticmethod
    def _motor(modell):
        from ggufengine.engine import Engine
        e = Engine.__new__(Engine)
        e.device = torch.device("cuda")
        e.model = modell
        return e

    def test_ansichten_bleiben_ansichten(self):
        """Einbettung und Ausgabe-Kopf teilen sich Speicher – nach dem Zurückholen weiter einer, nicht zwei."""
        class Modell:
            def __init__(self):
                self.tabelle = torch.randn(4096, 256, device="cuda")
                self.kopf = self.tabelle.view(1, -1)
                self.stueck = self.tabelle[100:200]
        e = self._motor(Modell())
        soll = e.model.stueck.clone()
        e.offload()
        self.assertEqual(e.model.tabelle.untyped_storage().data_ptr(), e.model.kopf.untyped_storage().data_ptr())
        e.restore()
        m = e.model
        self.assertTrue(m.tabelle.is_cuda)
        self.assertEqual(m.tabelle.untyped_storage().data_ptr(), m.kopf.untyped_storage().data_ptr())
        self.assertEqual(m.tabelle.untyped_storage().data_ptr(), m.stueck.untyped_storage().data_ptr())
        self.assertTrue(torch.equal(m.stueck, soll))
        m.tabelle[150].fill_(7.0)                                     # gemeinsamer Speicher: sichtbar im Stück
        self.assertTrue(bool((m.stueck[50] == 7.0).all()))

    def test_pruefpunkte_kommen_mit(self):
        class Modell:
            def __init__(self):
                self.w = torch.randn(8, device="cuda")
        e = self._motor(Modell())
        zustand = torch.randn(1024, 512, device="cuda")
        e._checkpoint = (12, [1, 2], [zustand])
        e._answer_checkpoint = None
        soll = zustand.clone()
        del zustand
        e.offload()
        self.assertEqual(e._checkpoint[2][0].device.type, "cpu")
        e.restore()
        self.assertTrue(e._checkpoint[2][0].is_cuda)
        self.assertTrue(torch.equal(e._checkpoint[2][0], soll))
        self.assertEqual(e._checkpoint[:2], (12, [1, 2]))

    def test_alle_graphen_verworfen(self):
        """Jeder Graph-Satz am Modell wird geleert, auch einer, der nicht „graph“ heißt."""
        from ggufengine.models.common import GraphDecoder

        def decoder():
            d = GraphDecoder.__new__(GraphDecoder)
            d._haupt, d._name, d._graphs, d._pool, d.pool_mehr = None, None, {1: "alt"}, "pool", None
            return d

        class Modell:
            def __init__(self):
                self.w = torch.randn(8, device="cuda")
                self.graph, self._mtp_graph = decoder(), decoder()
        e = self._motor(Modell())
        e.offload()
        self.assertEqual(e.model.graph.graphs, {})
        self.assertEqual(e.model._mtp_graph.graphs, {})
        self.assertIsNone(e.model._mtp_graph.pool)
        e.restore()

    def test_alte_tensoren_in_kreisen_werden_frei(self):
        """Ein Referenz-Kreis hält den alten VRAM-Tensor: ohne Einsammeln bliebe der Speicher belegt."""
        class Modell:
            def __init__(self):
                self.w = torch.randn(4096, 4096, device="cuda")        # 64 MB
        e = self._motor(Modell())
        kreis = [e.model.w]
        kreis.append(kreis)
        del kreis
        gc.disable()
        try:
            vorher = torch.cuda.memory_allocated()
            e.offload()
            self.assertLess(torch.cuda.memory_allocated(), vorher - 60 * 2**20)
        finally:
            gc.enable()
        e.restore()


if __name__ == "__main__":
    unittest.main()
