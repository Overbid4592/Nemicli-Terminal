"""LoRA-Motor: Lernweg gleich GGUF-Motor, LoRA an- und abklinken, Training lernt.

Mini-Qwen3.5 mit Zufallsgewichten aus transformers, als GGUF geschrieben – kein echtes Modell nötig.
python -m unittest tests.test_loramotor
"""

import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests", "core", "tools", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import transformers as T
except Exception:                                   # ohne torch/transformers: überspringen
    torch = T = None


def _mini_gguf(pfad: str) -> "T.PreTrainedModel":
    import gguf_testbau as B
    torch.manual_seed(0)
    cfg = T.Qwen3_5TextConfig(vocab_size=96, hidden_size=64, num_hidden_layers=4, num_attention_heads=4,
                              num_key_value_heads=2, head_dim=16, linear_num_key_heads=2, linear_num_value_heads=4,
                              linear_key_head_dim=16, linear_value_head_dim=16, linear_conv_kernel_dim=4,
                              intermediate_size=96, max_position_embeddings=256, rms_norm_eps=1e-6,
                              tie_word_embeddings=False)
    hf = T.Qwen3_5ForCausalLM(cfg).eval().float()
    with torch.no_grad():
        for p in hf.parameters():
            p.normal_(0, 0.2)
    m, t = B.umwandeln(hf, "qwen35")
    B.schreibe_gguf(pfad, m, t)
    return hf


@unittest.skipIf(torch is None, "torch/transformers fehlen")
class LoraMotor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        cls._tmp = tempfile.TemporaryDirectory()
        cls.gguf = os.path.join(cls._tmp.name, "mini.gguf")
        cls.hf = _mini_gguf(cls.gguf)
        cls.gg = GGUFFile(cls.gguf)
        cls.model = build_model(cls.gg, torch.device("cpu"), torch.float32, 128, quant=False)

    @classmethod
    def tearDownClass(cls):
        del cls.model
        gc.collect()
        cls.gg.close()
        cls._tmp.cleanup()

    def _motor_logits(self, ids):
        """Logits aller Positionen über den Rechenweg des GGUF-Motors."""
        self.model.reset()
        self.model.alle_logits = True
        try:
            with torch.inference_mode():
                return self.model.forward(ids.tolist(), 0).float().clone()
        finally:
            self.model.alle_logits = False
            self.model.reset()

    def test_lernweg_rechnet_wie_der_motor(self):
        from loramotor.lernweg import Lernweg
        ids = torch.randint(0, 96, (70,))
        lw = Lernweg(self.model, "qwen35", rang=4)
        with torch.no_grad():
            eigen = lw.logits(ids)
            ref = self.hf(ids[None]).logits[0].float()
        self.assertLess(float((eigen - ref).abs().max()), 2e-3 * max(1.0, float(ref.abs().max())))

    def test_anklinken_gleich_lernweg_und_abklinken_wie_vorher(self):
        from loramotor import adapter as A
        from loramotor.lernweg import Lernweg
        ids = torch.randint(0, 96, (40,))
        vorher = self._motor_logits(ids)
        lw = Lernweg(self.model, "qwen35", rang=4)
        torch.manual_seed(1)
        with torch.no_grad():
            for b in lw.b.values():
                b.normal_(0, 0.05)
            erwartet = lw.logits(ids)
        datei = os.path.join(self._tmp.name, "mini.safetensors")
        A.speichern(datei, lw.gewichte(), {"basis": A.basis_kennung(self.gguf), "rang": 4, "alpha": lw.alpha})
        self.assertEqual(A.anklinken(self.model, datei, self.gguf), len(A.schichten(self.model)))
        try:
            mit = self._motor_logits(ids)
        finally:
            self.assertEqual(A.abklinken(self.model), len(A.schichten(self.model)))
        self.assertGreater(float((mit - vorher).abs().max()), 1e-2)
        self.assertLess(float((mit - erwartet).abs().max()), 2e-3 * max(1.0, float(erwartet.abs().max())))
        self.assertLess(float((self._motor_logits(ids) - vorher).abs().max()), 1e-5)

    def test_falsches_grundmodell_wird_abgelehnt(self):
        from loramotor import adapter as A
        from loramotor.lernweg import Lernweg
        lw = Lernweg(self.model, "qwen35", rang=2)
        datei = os.path.join(self._tmp.name, "fremd.safetensors")
        A.speichern(datei, lw.gewichte(), {"basis": "anderes.gguf|123", "rang": 2})
        with self.assertRaises(ValueError):
            A.anklinken(self.model, datei, self.gguf)
        self.assertEqual(A.angeklinkt(self.model), "")

    def test_training_senkt_den_verlust(self):
        from loramotor.lernweg import Lernweg
        torch.manual_seed(2)
        lw = Lernweg(self.model, "qwen35", rang=4)
        ids = torch.randint(0, 96, (48,))
        maske = torch.zeros(48, dtype=torch.bool)
        maske[20:] = True
        opt = torch.optim.AdamW(lw.parameter(), lr=1e-2)
        with torch.no_grad():
            anfang = float(lw.verlust(ids, maske))
        for _ in range(30):
            opt.zero_grad()
            v = lw.verlust(ids, maske)
            v.backward()
            opt.step()
        self.assertLess(float(v.detach()), anfang * 0.7)
        self.assertTrue(all(p.grad is not None for p in lw.parameter()))

    def test_training_von_anfang_bis_ende_und_abgleich(self):
        import coding
        from loramotor import adapter as A, training as LT
        ordner = Path(self._tmp.name) / "coding_lora"

        class Tok:
            special = {"<|im_end|>": 5}
            eos_id = 2

            def encode(self, text):
                return [10 + (ord(c) % 80) for c in text[:60]]

        class Fmt:
            def __init__(self, *a, **k):
                pass

            def encode(self, nachrichten, add_generation_prompt=True):
                return [1] + [10 + (ord(c) % 80) for m in nachrichten for c in m.content[:20]] + [3]

        beispiele = [{"auftrag": "Notiz-Tool", "punkt": f"Schritt {i}", "datei": f"m{i}.py", "sprache": "Python",
                      "art": "schreiben", "vorher": "", "nachher": f"def f{i}():\n    return {i}\n",
                      "status": "geprueft" if i % 2 else "sauber"} for i in range(12)]
        meldungen = []
        with patch.object(A, "ORDNER", ordner), patch("ggufengine.tokenizer.Tokenizer", lambda gg: Tok()), \
                patch("ggufengine.chat.ChatFormatter", Fmt):
            pfad = LT.trainieren(gguf=self.gguf, beispiele=beispiele, schritte=12, rang=2, geraet="cpu",
                                 on_status=meldungen.append)
            self.assertEqual(pfad, A.datei_fuer(self.gguf))
            meta = A.info(pfad)
            self.assertEqual(meta["basis"], A.basis_kennung(self.gguf))
            self.assertEqual((meta["schritte"], meta["beispiele"]), ("12", "12"))
            self.assertTrue(any("Schritt 10/12" in m for m in meldungen))
            self.assertFalse(LT.laeuft())

            class Engine:
                model = self.model
                _checkpoint = "alt"
                zurueck = 0

                def reset(self):
                    Engine.zurueck += 1

            e = Engine()
            with patch.object(coding, "aktiv", lambda: True), patch.object(A, "_config", lambda: {}):
                self.assertTrue(A.abgleich(e, self.gguf))                   # Coding an → angeklinkt
                self.assertTrue(A.angeklinkt(self.model))
                self.assertIsNone(e._checkpoint)
                self.assertFalse(A.abgleich(e, self.gguf))                  # nichts geändert
            with patch.object(coding, "aktiv", lambda: False):
                self.assertTrue(A.abgleich(e, self.gguf))                   # Coding aus → abgeklinkt
            self.assertEqual(A.angeklinkt(self.model), "")
            self.assertEqual(Engine.zurueck, 2)

    def test_coding_lora_fortsetzen_wie_ohne_pause(self):
        from loramotor import adapter as A, training as LT
        ordner = Path(self._tmp.name) / "coding_lora_pause"

        class Tok:
            special = {"<|im_end|>": 5}
            eos_id = 2

            def encode(self, text):
                return [10 + (ord(c) % 80) for c in text[:40]]

        class Fmt:
            def __init__(self, *a, **k):
                pass

            def encode(self, nachrichten, add_generation_prompt=True):
                return [1] + [10 + (ord(c) % 80) for m in nachrichten for c in m.content[:12]] + [3]

        beispiele = [{"auftrag": "A", "punkt": f"P{i}", "datei": f"m{i}.py", "sprache": "Python", "art": "schreiben",
                      "vorher": "", "nachher": f"x = {i}\n", "status": "sauber"} for i in range(10)]

        def lauf(abbruch=None):
            torch.manual_seed(5)
            return LT.trainieren(gguf=self.gguf, beispiele=beispiele, schritte=8, rang=2, geraet="cpu",
                                 abbruch=abbruch)
        with patch.object(A, "ORDNER", ordner), patch("ggufengine.tokenizer.Tokenizer", lambda gg: Tok()), \
                patch("ggufengine.chat.ChatFormatter", Fmt):
            durchgehend = A.laden(lauf())[0]
            A.datei_fuer(self.gguf).unlink()
            zaehler = {"n": 0}

            def abbruch():
                zaehler["n"] += 1
                return zaehler["n"] > 3
            with self.assertRaises(RuntimeError):
                lauf(abbruch)
            self.assertEqual(A.info(LT._stand_datei(self.gguf))["adam_schritt"], "3")
            mit_pause = A.laden(lauf())[0]
        for k, (a, b) in durchgehend.items():
            self.assertTrue(torch.allclose(a, mit_pause[k][0], atol=1e-6), k)
            self.assertTrue(torch.allclose(b, mit_pause[k][1], atol=1e-6), k)

    def test_zu_wenig_beispiele(self):
        from loramotor import training as LT
        with self.assertRaises(ValueError):
            LT.trainieren(gguf=self.gguf, beispiele=[{}] * 3, geraet="cpu")
        self.assertFalse(LT.laeuft())

    def test_gespraech_aus_beispiel(self):
        from loramotor import training as LT
        frage, antwort = LT.gespraech({"auftrag": "A", "punkt": "P", "datei": "x.js", "sprache": "JavaScript",
                                       "art": "bearbeiten", "vorher": "let a", "nachher": "const a"})
        self.assertIn("Ändere diese Stelle:\n```javascript\nlet a\n```", frage)
        self.assertEqual(antwort, "```javascript\nconst a\n```")


if __name__ == "__main__":
    unittest.main()
