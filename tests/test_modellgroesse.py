"""Größenprüfung fürs Selbst-Umschreiben: Cloud, GGUF-Kopf (MoE voll, ohne Per-Layer-Embeddings),
Namen-Rückfall, unbekannt.

python -m unittest tests.test_modellgroesse
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import gguflokal as G                                  # noqa: E402
import modellgroesse as MG                             # noqa: E402
from ggufengine import gguf as GG                      # noqa: E402

_TENSOR_INFO = GG.GGUFFile._make_tensor_info


def _info(name: str, *form: int):
    """TensorInfo mit Form in Zeilen-Reihenfolge (ohne Datei)."""
    return _TENSOR_INFO(name, tuple(reversed(form)), 0, 0)


class _ScheinGGUF:
    """Ersatz für GGUFFile: Tensoren je Datei aus einer Tabelle, ohne Daten."""
    tabelle: dict = {}
    geoeffnet: list = []

    def __init__(self, pfad):
        _ScheinGGUF.geoeffnet.append(Path(pfad).name)
        self.tensors = {ti.name: ti for ti in _ScheinGGUF.tabelle[Path(pfad).name]}

    def close(self):
        pass


class _Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        self.dateien: dict[str, Path] = {}
        MG._gemerkt.clear()
        for p in (patch.object(G, "modelle", side_effect=lambda: dict(self.dateien)),
                  patch.object(GG, "GGUFFile", _ScheinGGUF)):
            p.start()
            self.addCleanup(p.stop)
        _ScheinGGUF.tabelle, _ScheinGGUF.geoeffnet = {}, []

    def modell(self, name: str, tensoren: list, datei: str | None = None) -> Path:
        pfad = self.ordner / (datei or f"{name}.gguf")
        pfad.write_bytes(b"")
        _ScheinGGUF.tabelle[pfad.name] = tensoren
        self.dateien.setdefault(name, pfad)
        return pfad


def _dicht(schichten: int, breite: int, ffn: int, vocab: int = 32000) -> list:
    t = [_info("token_embd.weight", vocab, breite), _info("output.weight", vocab, breite)]
    for i in range(schichten):
        t += [_info(f"blk.{i}.attn_q.weight", breite, breite), _info(f"blk.{i}.attn_k.weight", breite, breite),
              _info(f"blk.{i}.attn_v.weight", breite, breite), _info(f"blk.{i}.attn_output.weight", breite, breite),
              _info(f"blk.{i}.ffn_gate.weight", ffn, breite), _info(f"blk.{i}.ffn_up.weight", ffn, breite),
              _info(f"blk.{i}.ffn_down.weight", breite, ffn)]
    return t


class Cloud(unittest.TestCase):
    def test_cloud_darf_immer(self):
        self.assertEqual(MG.darf_selbst_umschreiben("anthropic:claude-test"), (True, "Cloud-Modell"))
        self.assertEqual(MG.darf_selbst_umschreiben("openrouter:any/model-1b")[0], True)

    def test_aktives_modell_aus_config(self):
        import config
        with patch.object(config, "load", return_value={"model": "openai:test-model"}):
            self.assertEqual(MG.darf_selbst_umschreiben(), (True, "Cloud-Modell"))
        with patch.object(config, "load", return_value={}):
            self.assertFalse(MG.darf_selbst_umschreiben()[0])


class EigenerMotor(_Basis):
    def test_dicht_ueber_grenze(self):
        self.modell("gross", _dicht(32, 4096, 14336))           # rund 7,2B
        b = MG.parameter_milliarden("gguf:gross")
        self.assertGreater(b, 7.0)
        darf, grund = MG.darf_selbst_umschreiben("gguf:gross")
        self.assertTrue(darf)
        self.assertEqual(grund, f"{b:.1f}B".replace(".", ","))

    def test_dicht_unter_grenze(self):
        self.modell("klein", _dicht(28, 3072, 8192))            # rund 3,2B
        darf, grund = MG.darf_selbst_umschreiben("gguf:klein")
        self.assertFalse(darf)
        self.assertTrue(grund.endswith("– unter 6B"), grund)

    def test_moe_zaehlt_alle_experten(self):
        t = [_info("token_embd.weight", 262144, 2816)]
        for i in range(30):
            t += [_info(f"blk.{i}.attn_q.weight", 4096, 2816),
                  _info(f"blk.{i}.ffn_gate_exps.weight", 128, 704, 2816),
                  _info(f"blk.{i}.ffn_up_exps.weight", 128, 704, 2816),
                  _info(f"blk.{i}.ffn_down_exps.weight", 128, 2816, 704)]
        self.modell("moe", t)
        b = MG.parameter_milliarden("gguf:moe")
        self.assertGreater(b, 20.0)
        darf, grund = MG.darf_selbst_umschreiben("gguf:moe")
        self.assertTrue(darf)
        self.assertTrue(grund.endswith("(MoE, volle Größe)"), grund)

    def test_per_layer_embedding_zaehlt_nicht(self):
        rest = _dicht(42, 2560, 10240, vocab=262144)
        ple = _info("per_layer_token_embd.weight", 262144, 42 * 256)    # rund 2,8B
        self.modell("e4b", rest + [ple])
        ohne = MG._zaehlen(rest)[0] / 1e9
        self.assertGreater(ohne + ple.n_elements / 1e9, MG.GRENZE_B)  # mit Tabelle wäre es über der Grenze
        self.assertAlmostEqual(MG.parameter_milliarden("gguf:e4b"), ohne)
        darf, grund = MG.darf_selbst_umschreiben("gguf:e4b")
        self.assertFalse(darf)
        self.assertIn("rechnend – unter 6B", grund)

    def test_geteilte_datei_alle_teile(self):
        self.modell("teil", _dicht(16, 4096, 14336), "teil-00001-of-00002.gguf")
        self.modell("teil2", _dicht(16, 4096, 14336, vocab=1), "teil-00002-of-00002.gguf")
        ganz = MG._zaehlen(_dicht(16, 4096, 14336) + _dicht(16, 4096, 14336, vocab=1))[0] / 1e9
        self.assertAlmostEqual(MG.parameter_milliarden("gguf:teil"), ganz)

    def test_fehlender_teil_unbekannt(self):
        self.modell("teil", _dicht(32, 4096, 14336), "teil-00001-of-00002.gguf")
        self.assertIsNone(MG.parameter_milliarden("gguf:teil"))
        self.assertFalse(MG.darf_selbst_umschreiben("gguf:teil")[0])

    def test_kopf_einmal_je_datei(self):
        self.modell("gross", _dicht(32, 4096, 14336))
        MG.parameter_milliarden("gguf:gross")
        MG.parameter_milliarden("gguf:gross")
        self.assertEqual(_ScheinGGUF.geoeffnet, ["gross.gguf"])

    def test_unbekannt_darf_nicht(self):
        self.assertIsNone(MG.parameter_milliarden("gguf:fehlt-8b"))
        darf, grund = MG.darf_selbst_umschreiben("gguf:fehlt-8b")
        self.assertFalse(darf)
        self.assertIn("unbekannt", grund)
        kaputt = self.ordner / "kaputt.gguf"
        kaputt.write_bytes(b"")
        self.dateien["kaputt"] = kaputt                         # nicht in der Tabelle: Lesefehler
        self.assertFalse(MG.darf_selbst_umschreiben("gguf:kaputt")[0])


class EchteKopfdatei(unittest.TestCase):
    """Der vorhandene GGUF-Leser liest die Formen; per_layer_token_embd fällt heraus."""

    def test_synthetische_datei(self):
        import gguf_testbau as B
        import torch
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / "mini.gguf"
            B.schreibe_gguf(str(pfad), {"general.architecture": "gemma4"},
                            {"token_embd.weight": torch.zeros(50, 8), "output.weight": torch.zeros(50, 8),
                             "blk.0.ffn_up_exps.weight": torch.zeros(4, 16, 8),
                             "per_layer_token_embd.weight": torch.zeros(50, 64)})
            MG._gemerkt.clear()
            g = MG._aus_gguf(pfad)
        self.assertEqual(round(g.milliarden * 1e9), 50 * 8 * 2 + 4 * 16 * 8)
        self.assertEqual(g.art, "moe")


class NamenRueckfall(unittest.TestCase):
    def setUp(self):
        import providers
        p = patch.object(providers, "_ollama_tags", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_namen(self):
        self.assertEqual(MG.darf_selbst_umschreiben("ollama:qwen3:8b"), (True, "8,0B"))
        self.assertEqual(MG.darf_selbst_umschreiben("ollama:gemma4:e4b"), (False, "4,0B rechnend – unter 6B"))
        self.assertEqual(MG.darf_selbst_umschreiben("x:modell-26b-a4b"), (True, "26,0B (MoE, volle Größe)"))
        self.assertTrue(MG.darf_selbst_umschreiben("lmstudio:qwen2.5-14B-instruct")[0])
        self.assertFalse(MG.darf_selbst_umschreiben("ollama:llama3.2:3b")[0])
        self.assertFalse(MG.darf_selbst_umschreiben("ollama:smollm2:135m")[0])
        self.assertTrue(MG.darf_selbst_umschreiben("ollama:mixtral:8x7b")[0])

    def test_zahlen_im_namen(self):
        self.assertEqual(MG.parameter_milliarden("ollama:phi3:3.8b"), 3.8)
        self.assertEqual(MG.parameter_milliarden("jan:qwen3-30b-a3b"), 30.0)
        self.assertEqual(MG.parameter_milliarden("ollama:gemma3n:e2b"), 2.0)
        self.assertEqual(MG.parameter_milliarden("vllm:llama-3.1-8b-instruct"), 8.0)
        self.assertIsNone(MG.parameter_milliarden("ollama:phi-mini"))

    def test_ohne_groesse_darf_nicht(self):
        self.assertFalse(MG.darf_selbst_umschreiben("x:modell")[0])
        self.assertFalse(MG.darf_selbst_umschreiben("ollama:llama3:latest")[0])

    def test_ollama_tags(self):
        import providers
        tags = [{"name": "llama3:latest", "details": {"family": "llama", "parameter_size": "8.0B"}},
                {"name": "gemma4:latest", "details": {"family": "gemma4", "parameter_size": "8.0B"}}]
        with patch.object(providers, "_ollama_tags", return_value=tags), \
                patch.object(providers, "_ollama_lokal", return_value=True):
            self.assertEqual(MG.darf_selbst_umschreiben("ollama:llama3"), (True, "8,0B"))
            self.assertFalse(MG.darf_selbst_umschreiben("ollama:gemma4:latest")[0])
        with patch.object(providers, "_ollama_tags", return_value=tags), \
                patch.object(providers, "_ollama_lokal", return_value=False):
            self.assertFalse(MG.darf_selbst_umschreiben("ollama:llama3")[0])


if __name__ == "__main__":
    unittest.main()
