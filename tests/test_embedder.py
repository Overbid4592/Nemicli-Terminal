"""Tests für tools/embedder.py – Qwen3-Embedding ohne sentence-transformers.

    python -I -S -B -m unittest discover -s tests -p test_embedder.py

Der schnelle Teil läuft immer: Platzhalter-Logik und der Query-Prompt werden
gegen die Konfigurationsdatei des Modells geprüft.

Der LANGSAME Teil (echtes Modell laden, Vektoren gegen die gespeicherten
sentence-transformers-Vektoren halten) läuft nur mit

    NEMICLI_SLOW_TESTS=1

weil das Modell ~14 s zum Laden braucht und die übrige Suite in ~25 s durch ist.
Genau dieser Vergleich war der Beweis beim Umbau: alle Proben kamen mit
Ähnlichkeit 1.000000 zurück, die Vektoren sind also austauschbar.
"""

import importlib.util
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_embedder():
    name = "isolated_embedder_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/embedder.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


E = load_embedder()


def _snapshot() -> Path | None:
    """Der lokal liegende Modell-Ordner – oder None."""
    try:
        sys.path.insert(0, str(ROOT / "core"))
        sys.path.insert(0, str(ROOT / "tools"))
        import memory
        return memory.modell_ordner()
    except Exception:
        return None


class PlatzhalterTests(unittest.TestCase):
    """Der sklearn-Platzhalter darf nur einspringen, wenn er muss."""

    def test_platzhalter_nur_bei_blockade(self):
        import importlib as il
        try:
            il.import_module("sklearn.metrics")
            echt_da = True
        except Exception:
            echt_da = False
        gesetzt = E._sklearn_blockade_umgehen()
        # Läuft sklearn, darf NICHTS angefasst werden.
        self.assertEqual(gesetzt, not echt_da)

    def test_platzhalter_rechnet_nicht_heimlich(self):
        """Wird eine Platzhalter-Funktion doch aufgerufen, muss es knallen –
        ein stilles falsches Ergebnis wäre das Schlimmste."""
        E._sklearn_blockade_umgehen()
        # NICHT am Rückgabewert festmachen: lief ein Test davor, steht der
        # Platzhalter schon und der zweite Aufruf meldet "nichts getan".
        if not getattr(sys.modules.get("sklearn"), "__nemicli_platzhalter__", False):
            self.skipTest("echtes sklearn vorhanden – kein Platzhalter im Spiel")
        from sklearn.metrics import roc_curve
        with self.assertRaises(NotImplementedError):
            roc_curve([0, 1], [0.1, 0.9])


class ConfigPruefungTests(unittest.TestCase):
    """Konfigurationen, die transformers Code nachladen ließen, werden abgelehnt."""

    def test_unsichere_config_abgelehnt(self):
        import json
        import tempfile
        faelle = [({"model_type": "qwen3"}, True),
                  ({"_attn_implementation_internal": "angreifer/kernel"}, False),
                  ({"text_config": {"_attn_implementation": "x/y"}}, False),
                  ({"auto_map": {"AutoModel": "modeling.Model"}}, False),
                  ({"_attn_implementation": "sdpa"}, True)]
        for inhalt, ok in faelle:
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "config.json").write_text(json.dumps(inhalt), encoding="utf-8")
                if ok:
                    E.config_pruefen(tmp)
                else:
                    with self.assertRaises(RuntimeError, msg=str(inhalt)):
                        E.config_pruefen(tmp)


class PromptTests(unittest.TestCase):
    """Der Query-Prompt muss Zeichen für Zeichen dem Modell entsprechen –
    ein abweichender Prompt ergibt andere Vektoren als die gespeicherten."""

    def test_query_prompt_stimmt_mit_modell_ueberein(self):
        snap = _snapshot()
        if snap is None:
            self.skipTest("Modell liegt nicht lokal")
        cfg = Path(snap) / "config_sentence_transformers.json"
        if not cfg.exists():
            self.skipTest("config_sentence_transformers.json fehlt")
        erwartet = json.loads(cfg.read_text(encoding="utf-8"))["prompts"]["query"]
        self.assertEqual(E.QUERY_PROMPT, erwartet)

    def test_pooling_ist_letztes_token(self):
        snap = _snapshot()
        if snap is None:
            self.skipTest("Modell liegt nicht lokal")
        cfg = Path(snap) / "1_Pooling" / "config.json"
        if not cfg.exists():
            self.skipTest("1_Pooling/config.json fehlt")
        d = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertTrue(d["pooling_mode_lasttoken"],
                        "Modell poolt anders – embedder.py muss nachgezogen werden")
        for anders in ("pooling_mode_cls_token", "pooling_mode_mean_tokens"):
            self.assertFalse(d[anders])


@unittest.skipUnless(os.environ.get("NEMICLI_SLOW_TESTS") == "1",
                     "langsam – mit NEMICLI_SLOW_TESTS=1 einschalten")
class VektorTests(unittest.TestCase):
    """Die Nagelprobe: passen die neuen Vektoren zu den alten in der Datenbank?"""

    def test_gleiche_vektoren_wie_sentence_transformers(self):
        snap = _snapshot()
        if snap is None:
            self.skipTest("Modell liegt nicht lokal")
        sys.path.insert(0, str(ROOT / "core"))
        sys.path.insert(0, str(ROOT / "tools"))
        import memory
        db = memory._ROOT / "learned" / "memory.db"
        if not db.exists():
            self.skipTest("noch keine gespeicherten Vektoren")
        with sqlite3.connect(db) as c:
            proben = [(t, json.loads(v))
                      for (v, t) in c.execute("select vec, text from chunks limit 5")]
        if not proben:
            self.skipTest("Tabelle chunks ist leer")

        enc = E.QwenEmbedder(str(snap), local_files_only=True, device="cpu")
        neu = enc.encode([t for t, _ in proben], normalize_embeddings=True)
        for (text, alt), n in zip(proben, neu):
            self.assertEqual(len(n), len(alt), f"andere Dimension bei: {text[:40]}")
            cos = float(sum(a * float(b) for a, b in zip(alt, n)))
            self.assertGreater(cos, 0.999,
                               f"Vektor weicht ab (cos={cos:.6f}): {text[:40]}")

    def test_gpu_bfloat16_passt_zu_den_gespeicherten(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("keine CUDA-GPU")
        snap = _snapshot()
        if snap is None:
            self.skipTest("Modell liegt nicht lokal")
        sys.path.insert(0, str(ROOT / "core"))
        sys.path.insert(0, str(ROOT / "tools"))
        import memory
        db = memory._ROOT / "learned" / "memory.db"
        if not db.exists():
            self.skipTest("noch keine gespeicherten Vektoren")
        with sqlite3.connect(db) as c:
            proben = [(t, json.loads(v))
                      for (v, t) in c.execute("select vec, text from chunks limit 20")]
        if not proben:
            self.skipTest("Tabelle chunks ist leer")

        enc = E.QwenEmbedder(str(snap), local_files_only=True, device="cuda")
        try:
            self.assertEqual(next(enc.model.parameters()).dtype, torch.bfloat16)
            neu = enc.encode([t for t, _ in proben], normalize_embeddings=True)
            self.assertEqual(neu[0].dtype.name, "float32")
            for (text, alt), n in zip(proben, neu):
                cos = float(sum(a * float(b) for a, b in zip(alt, n)))
                self.assertGreater(cos, 0.999,
                                   f"bfloat16-Vektor weicht ab (cos={cos:.6f}): {text[:40]}")
        finally:
            del enc
            torch.cuda.empty_cache()


if __name__ == "__main__":
    unittest.main()


class GeraetTests(unittest.TestCase):
    """Config `embedding_geraet`: auto (RAM, große Aufträge auf der GPU) · cuda · cpu."""

    def _mit_config(self, wert):
        for d in ("core", "tools", "engines", "ui"):
            if str(ROOT / d) not in sys.path:
                sys.path.insert(0, str(ROOT / d))
        import config, memory
        alt = config.load
        config.load = lambda: {"embedding_geraet": wert}
        self.addCleanup(lambda: setattr(config, "load", alt))
        return memory

    def test_config_cpu_erzwingt_cpu(self):
        memory = self._mit_config("cpu")
        self.assertEqual("cpu", memory.geraet_gewuenscht())
        self.assertEqual("cpu", memory._geraet_waehlen())

    def test_auto_laedt_in_den_ram(self):
        # auto: geladen wird immer auf die CPU; die GPU nur für große Aufträge (memory.auftrag).
        memory = self._mit_config("auto")
        self.assertEqual("cpu", memory._geraet_waehlen())
        self.assertEqual("auto", self._mit_config("quatsch").geraet_gewuenscht())

    def test_cuda_laedt_auf_die_gpu_wenn_da(self):
        import torch
        memory = self._mit_config("cuda")
        self.assertEqual("cuda" if torch.cuda.is_available() else "cpu", memory._geraet_waehlen())

    def test_encoder_geraet_ohne_geladenen_encoder(self):
        memory = self._mit_config("cpu")
        if memory._encoder is None:
            self.assertEqual("cpu", memory.encoder_geraet())
