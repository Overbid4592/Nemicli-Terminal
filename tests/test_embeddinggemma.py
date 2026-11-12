"""EmbeddingGemma 2 (Bauart gemma-embedding2): Modell-Ordner mit mmproj, Such- und Dokument-Vorsatz,
Anfangs-Token. Der langsame Teil (echtes Modell gegen transformers, Bild-Vektoren) läuft nur mit
NEMICLI_SLOW_TESTS=1 und EMBEDDINGGEMMA2_ORDNER=<Ordner mit der .gguf und mmproj>.

python -m unittest tests.test_embeddinggemma
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in (str(ROOT), str(ROOT / "tools")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import embedder as E  # noqa: E402

ORDNER = os.environ.get("EMBEDDINGGEMMA2_ORDNER", "")
LANGSAM = os.environ.get("NEMICLI_SLOW_TESTS") == "1" and ORDNER and Path(ORDNER).is_dir()


class Ordner(unittest.TestCase):

    def test_mmproj_zaehlt_nicht_als_modell(self):
        with tempfile.TemporaryDirectory() as tmp:
            o = Path(tmp)
            (o / "embeddinggemma-2-Q8_0.gguf").write_bytes(b"x")
            (o / "mmproj-F16.gguf").write_bytes(b"x")
            self.assertEqual("embeddinggemma-2-Q8_0.gguf", E.gguf_datei(o).name)
            self.assertEqual("mmproj-F16.gguf", E.mmproj_datei(o).name)
            (o / "zweites.gguf").write_bytes(b"x")
            self.assertIsNone(E.gguf_datei(o))                     # zwei Modelle: nicht eindeutig

    def test_ohne_mmproj(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "qwen3-embedding.gguf").write_bytes(b"x")
            self.assertIsNone(E.mmproj_datei(tmp))


class Vorsatz(unittest.TestCase):
    KOPF = {"arch": "gemma-embedding2", "dim": 768, "schichten": 24, "vokabular": 262144, "pooling": "mean",
            "familie": "embeddinggemma", "kontext": 262144}

    def test_gemma_vorsatz_aus_dem_kopf(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m.gguf").write_bytes(b"x")
            with mock.patch.object(E, "_gguf_kopf", return_value=dict(self.KOPF)):
                e = E.einstellungen_lesen(tmp)
                self.assertEqual("task: search result | query: ", e["query_prompt"])
                self.assertEqual("title: none | text: ", e["dokument_prompt"])
                self.assertEqual("mean", e["pooling"])
                self.assertEqual("gemma-embedding2-768-24-262144-mean", E.kennung(tmp))

    def test_aufgabe_waehlt_die_gemma_anweisung(self):
        e = {"query_prompt": "task: search result | query: ", "prompt_eigen": False}
        self.assertEqual("task: code retrieval | query: ", E.such_prompt(e, "code"))
        self.assertEqual("task: search result | query: ", E.such_prompt(e, "chat"))
        self.assertEqual("task: search result | query: ", E.such_prompt(e, None))
        self.assertEqual(e["query_prompt"], E.such_prompt({**e, "prompt_eigen": True}, "code"))

    def test_qwen_bleibt(self):
        e = {"query_prompt": E.QUERY_PROMPT, "prompt_eigen": False}
        self.assertTrue(E.such_prompt(e, "code").startswith("Instruct: Given a programming question"))


@unittest.skipUnless(LANGSAM, "echtes Modell: NEMICLI_SLOW_TESTS=1 und EMBEDDINGGEMMA2_ORDNER setzen")
class EchtesModell(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from ggufengine.einbettung import Einbetter
        cls.e = Einbetter(str(E.gguf_datei(ORDNER)), device="cpu")

    def test_bos_und_eos(self):
        ids = self.e.tokens("hallo")
        self.assertEqual((self.e.tok.bos_id, self.e.tok.eos_id), (ids[0], ids[-1]))
        self.assertEqual(768, self.e.dim)

    def test_gepackt_wie_einzeln(self):
        texte = ["title: none | text: Krea malt.", "task: search result | query: Wer malt?"]
        for a, b in zip(self.e.vektoren(texte), [self.e.vektor(t) for t in texte]):
            self.assertGreater(float((a * b).sum()), 0.99999)

    def test_bild_neben_dem_passenden_satz(self):
        from PIL import Image
        self.e.sehen_laden(str(E.mmproj_datei(ORDNER)))
        rot = self.e.bild_vektor(Image.new("RGB", (448, 448), "red"))
        saetze = [self.e.vektor("task: search result | query: " + s)
                  for s in ("a completely red image", "a completely blue image")]
        self.assertGreater(float((rot * saetze[0]).sum()), float((rot * saetze[1]).sum()))


if __name__ == "__main__":
    unittest.main()
