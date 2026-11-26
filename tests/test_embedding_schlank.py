"""Gedächtnis-Modell schlank: GGUF bei „auto“ im RAM mit zeitweiser GPU-Fassung, gepackte Durchgänge
(gleiche Vektoren wie einzeln), Suchanweisung je Aufgabe und Fragen-Speicher.

python -m unittest tests.test_embedding_schlank
"""

import gc
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import embedder   # noqa: E402
import indexdb    # noqa: E402
import memory     # noqa: E402

try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class GepacktWieEinzeln(unittest.TestCase):
    """Mehrere Folgen in einem Durchgang liefern dieselben Vektoren wie jede für sich."""

    def _modell(self, **extra):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        torch.manual_seed(0)
        cfg = T.Qwen3Config(vocab_size=96, hidden_size=64, intermediate_size=96, num_hidden_layers=2,
                            num_attention_heads=4, num_key_value_heads=2, head_dim=16,
                            max_position_embeddings=64, rms_norm_eps=1e-6, tie_word_embeddings=True,
                            attn_implementation="eager", pad_token_id=0, bos_token_id=1, eos_token_id=2)
        hf = T.Qwen3ForCausalLM(cfg).eval().float()
        with torch.no_grad():
            for p in hf.parameters():
                p.normal_(0, 0.2)
        meta, tensoren = B.umwandeln(hf, "qwen3")
        meta["qwen3.pooling_type"] = 3
        tensoren.pop("output.weight", None)
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        self.addCleanup(os.unlink, pfad)
        B.schreibe_gguf(pfad, meta, tensoren)
        gg = GGUFFile(pfad)
        self.addCleanup(gg.close)
        m = build_model(gg, torch.device("cpu"), torch.float32, 48, quant=False)
        self.addCleanup(gc.collect)
        return m

    def test_gleiche_vektoren(self):
        m = self._modell()
        self.assertEqual(m.stapel_platz(), 48)
        g = torch.Generator().manual_seed(1)
        folgen = [torch.randint(0, 96, (n,), generator=g).tolist() for n in (7, 12, 1, 5)]
        for art in ("last", "mean", "cls"):
            einzeln = [m.einbetten(f, art) for f in folgen]
            gepackt = m.einbetten_stapel(folgen, art)
            for a, b in zip(einzeln, gepackt):
                self.assertLess(float((a - b).abs().max()), 1e-4, art)

    def test_zu_viele_token(self):
        m = self._modell()
        with self.assertRaises(RuntimeError):
            m.einbetten_stapel([[3] * 30, [4] * 30])
        with self.assertRaises(ValueError):
            m.einbetten_stapel([[3], []])

    def test_maske_trennt_die_folgen(self):
        from ggufengine.models.common import attention
        q = torch.randn(4, 2, 8)
        k = torch.randn(2, 4, 8)
        v = torch.randn(2, 4, 8)
        pos = torch.arange(4)
        seg = torch.tensor([0, 0, 1, 1])
        o = attention(q, k, v, pos, 4, 0.3, segmente=seg)
        allein = attention(q[2:], k[:, 2:], v[:, 2:], torch.arange(2), 2, 0.3)
        self.assertLess(float((o[2:] - allein).abs().max()), 1e-5)


class Packen(unittest.TestCase):
    """Einbetter.vektoren: in Reihenfolge, so viele Texte je Durchgang, wie Platz ist."""

    def _einbetter(self, platz):
        from ggufengine.einbettung import Einbetter
        aufrufe = []

        class Modell:
            def stapel_platz(self):
                return platz

            def einbetten(self, f, art):
                aufrufe.append([len(f)])
                return torch.tensor([float(len(f)), 1.0])

            def einbetten_stapel(self, folgen, art):
                aufrufe.append([len(f) for f in folgen])
                return [torch.tensor([float(len(f)), 1.0]) for f in folgen]
        e = object.__new__(Einbetter)
        e.model, e.pooling, e.max_tokens, e.add_eos = Modell(), "last", 100, False
        e.tok = NS(encode=lambda t: [5] * len(t), eos_id=None)
        return e, aufrufe

    @unittest.skipIf(torch is None, "torch fehlt")
    def test_gruppen_und_reihenfolge(self):
        e, aufrufe = self._einbetter(10)
        vecs = e.vektoren(["aaaa", "bbb", "cc", "dddddddd", "e"], normieren=False)
        self.assertEqual(aufrufe, [[4, 3, 2], [8, 1]])
        self.assertEqual([int(v[0]) for v in vecs], [4, 3, 2, 8, 1])
        sperren = []

        class Sperre:
            def __enter__(self):
                sperren.append(1)

            def __exit__(self, *a):
                return False
        e.vektoren(["aaaa", "bbb"], sperre=Sperre)
        self.assertEqual(len(sperren), 1)                    # je Durchgang einmal

    @unittest.skipIf(torch is None, "torch fehlt")
    def test_ohne_platz_einzeln(self):
        e, aufrufe = self._einbetter(0)
        e.vektoren(["aa", "b"])
        self.assertEqual(aufrufe, [[2], [1]])


class ZweiteFassung(unittest.TestCase):
    """GGUF: Grundfassung im RAM; to("cuda") lädt eine GPU-Fassung dazu, to("cpu") schließt sie."""

    def setUp(self):
        self.gebaut, self.zu = [], []
        test = self

        class Fassung:
            def __init__(self, geraet):
                self.device = NS(type=geraet)

            def vektoren(self, texte, **kw):
                test.gebaut.append(("rechnet", self.device.type, kw.get("sperre") is not None))
                return [NS(cpu=lambda: NS(numpy=lambda: [1.0])) for _ in texte]

            def close(self):
                test.zu.append(self.device.type)
        e = object.__new__(embedder.GGUFEmbedder)
        e.einstellungen = {"query_prompt": "", "dokument_prompt": "", "pooling": "last"}
        e._lock = threading.RLock()
        e._grund, e._zweit, e.device = Fassung("cpu"), None, "cpu"
        e._bauen = lambda geraet: Fassung(geraet)
        self.e = e

    def test_hin_und_zurueck(self):
        self.e.to("cuda")
        self.assertEqual(self.e.device, "cuda")
        self.e.encode(["a", "b"])
        self.e.to("cpu")
        self.assertEqual((self.e.device, self.zu), ("cpu", ["cuda"]))
        self.e.encode(["c"])
        self.assertEqual(self.gebaut, [("rechnet", "cuda", True), ("rechnet", "cpu", False)])
        self.e.close()
        self.assertEqual(self.zu, ["cuda", "cpu"])


class Geraet(unittest.TestCase):
    def test_auto_auch_gguf_im_ram(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "modell.gguf").write_bytes(b"")
            with patch.object(memory, "geraet_gewuenscht", lambda: "auto"):
                self.assertEqual(memory._geraet_waehlen(Path(d)), "cpu")

    def test_freier_vram_ohne_cuda_kontext(self):
        import vonsichaus
        with patch.object(vonsichaus, "vram_frei_mb", lambda: memory.GPU_FREI_MB + 1):
            if torch is not None:
                with patch.object(torch.cuda, "mem_get_info", side_effect=AssertionError("CUDA-Kontext")):
                    self.assertTrue(memory._gpu_frei_genug())
            else:
                self.assertTrue(memory._gpu_frei_genug())
        with patch.object(vonsichaus, "vram_frei_mb", lambda: 100):
            self.assertFalse(memory._gpu_frei_genug())


class Suchanweisung(unittest.TestCase):
    QWEN = {"query_prompt": embedder.QUERY_PROMPT}

    def test_qwen_vorlage_je_aufgabe(self):
        p = embedder.such_prompt(self.QWEN, "chat")
        self.assertEqual(p, f"Instruct: {embedder.AUFGABEN['chat']}\nQuery:")
        self.assertEqual(embedder.such_prompt(self.QWEN, None), embedder.QUERY_PROMPT)
        self.assertEqual(embedder.such_prompt(self.QWEN, "gibtsnicht"), embedder.QUERY_PROMPT)

    def test_andere_modelle_und_eigene_vorgabe_bleiben(self):
        self.assertEqual(embedder.such_prompt({"query_prompt": "query: "}, "chat"), "query: ")
        eigen = dict(self.QWEN, prompt_eigen=True)
        self.assertEqual(embedder.such_prompt(eigen, "chat"), embedder.QUERY_PROMPT)

    def test_eigene_vorgabe_wird_erkannt(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config.json").write_text(json.dumps({"model_type": "qwen3"}))
            self.assertFalse(embedder.einstellungen_lesen(d)["prompt_eigen"])
            (Path(d) / "embedding.json").write_text(json.dumps({"query_prompt": "Frage: "}))
            self.assertTrue(embedder.einstellungen_lesen(d)["prompt_eigen"])

    def test_aufgabe_nach_arten(self):
        self.assertEqual(indexdb._aufgabe(None), "gedaechtnis")
        self.assertEqual(indexdb._aufgabe(("chat",)), "chat")
        self.assertEqual(indexdb._aufgabe(("chat", "chatmd")), "chat")
        self.assertEqual(indexdb._aufgabe(("note",)), "notizen")
        self.assertEqual(indexdb._aufgabe(("code", "skill")), "code")
        self.assertEqual(indexdb._aufgabe(("wissen",)), "dokumente")
        self.assertEqual(indexdb._aufgabe(("chat", "wissen")), "gedaechtnis")

    @unittest.skipIf(torch is None, "torch fehlt")
    def test_letztes_token_bei_linkem_auffuellen(self):
        e = object.__new__(embedder.Embedder)
        e._torch = torch
        hidden = torch.arange(2 * 4, dtype=torch.float32).view(2, 4, 1)
        rechts = torch.tensor([[1, 1, 0, 0], [1, 1, 1, 1]])
        links = torch.tensor([[0, 0, 1, 1], [1, 1, 1, 1]])
        self.assertEqual(e._letztes_token(hidden, rechts).view(-1).tolist(), [1.0, 7.0])
        self.assertEqual(e._letztes_token(hidden, links).view(-1).tolist(), [3.0, 7.0])


class FragenSpeicher(unittest.TestCase):
    def setUp(self):
        self.texte = []
        test = self

        class Enc:
            kennung = "modell-a"
            device = "cpu"
            _lock = threading.RLock()

            def encode(self, texte, **kw):
                test.texte.append((list(texte), kw.get("aufgabe")))
                return [NS(tolist=lambda t=t: [float(len(t))]) for t in texte]
        self.enc = Enc()
        memory._fragen.clear()
        self.addCleanup(memory._fragen.clear)

    def test_nur_neue_werden_gerechnet(self):
        self.assertEqual(memory._fragen_vektoren(self.enc, ["ab", "abc"], "chat"), [[2.0], [3.0]])
        self.assertEqual(memory._fragen_vektoren(self.enc, ["abc", "abcd"], "chat"), [[3.0], [4.0]])
        self.assertEqual(self.texte, [(["ab", "abc"], "chat"), (["abcd"], "chat")])
        memory._fragen_vektoren(self.enc, ["ab"], "notizen")                # andere Aufgabe: neu
        self.assertEqual(self.texte[-1], (["ab"], "notizen"))

    def test_obergrenze(self):
        with patch.object(memory, "FRAGEN_SPEICHER", 3):
            memory._fragen_vektoren(self.enc, ["a", "b", "c", "d"], "chat")
        self.assertEqual(len(memory._fragen), 3)
        self.assertIsNone(memory._aus_speicher(self.enc, "chat", ["a"])[0])  # die älteste ging

    def test_zeit_im_status(self):
        memory._fragen_vektoren(self.enc, ["xyz"], "chat")
        with patch.object(memory, "encoder_loaded", lambda: True), \
                patch.object(memory, "modell_name", lambda: "Modell"), \
                patch.object(memory, "encoder_geraet", lambda: "cpu"):
            self.assertRegex(memory.encoder_status(), r"Modell geladen \(CPU\) · Suchfrage zuletzt \d+ ms")


if __name__ == "__main__":
    unittest.main()
