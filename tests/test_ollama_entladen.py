"""Vor GPU-Arbeit entlädt NemiCLI die Modelle eines lokalen Ollama (keep_alive 0) – sonst passt Krea 2 nicht
daneben. Ollama ist eine Attrappe (httpx gemockt)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import providers as P                                  # noqa: E402


class FalschesOllama:
    def __init__(self, modelle):
        self.modelle = {m["name"]: dict(m) for m in modelle}
        self.entladen: list[str] = []

    def get(self, url, **_k):
        if url.endswith("/api/ps"):
            return NS(raise_for_status=lambda: None,
                      json=lambda: {"models": [m for m in self.modelle.values()]})
        raise AssertionError(url)

    def post(self, url, json=None, **_k):
        assert url.endswith("/api/generate") and json["keep_alive"] == 0
        self.entladen.append(json["model"])
        self.modelle.pop(json["model"], None)
        return NS(status_code=200)


class OllamaEntladenTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(P._ollama_cache, {"t": -1e9, "tags": [{"name": "x"}]})
        p.start()
        self.addCleanup(p.stop)

    def _mit(self, modelle, host="http://127.0.0.1:11434"):
        o = FalschesOllama(modelle)
        for ziel, wert in (("OLLAMA_HOST", host),):
            q = mock.patch.object(P, ziel, wert)
            q.start()
            self.addCleanup(q.stop)
        for name in ("get", "post"):
            q = mock.patch.object(P.httpx, name, getattr(o, name))
            q.start()
            self.addCleanup(q.stop)
        return o

    def test_entlaedt_alle_im_grafikspeicher(self):
        o = self._mit([{"name": "gemma4:12b", "size_vram": 9_000_000_000},
                       {"name": "qwen3.5:4b", "size_vram": 3_000_000_000},
                       {"name": "auf-cpu:7b", "size_vram": 0}])
        self.assertEqual(["gemma4:12b", "qwen3.5:4b"], P.ollama_gpu_freigeben(warten_s=0.5))
        self.assertEqual(["gemma4:12b", "qwen3.5:4b"], o.entladen)         # CPU-Modell bleibt

    def test_nichts_geladen_nichts_zu_tun(self):
        o = self._mit([])
        self.assertEqual([], P.ollama_gpu_freigeben(warten_s=0.1))
        self.assertEqual([], o.entladen)

    def test_entferntes_ollama_bleibt_unberuehrt(self):
        o = self._mit([{"name": "gross:70b", "size_vram": 40_000_000_000}], host="https://ollama.example.com")
        self.assertEqual([], P.ollama_gpu_freigeben(warten_s=0.1))
        self.assertEqual([], o.entladen)

    def test_nicht_erreichbar_kostet_keine_wartezeit(self):
        with mock.patch.dict(P._ollama_cache, {"t": P.time.monotonic(), "tags": None}), \
             mock.patch.object(P.httpx, "get", side_effect=AssertionError("kein Abruf")):
            self.assertEqual([], P.ollama_gpu_freigeben())


class MalenEntlaedtOllamaTests(unittest.TestCase):
    def test_paint_entlaedt_vorher_cloud_nicht(self):
        import imagegen
        reihenfolge = []

        @imagegen._gpu_fuer_bild
        def paint(*_a, **_k):
            reihenfolge.append("malen")
            return "bild"

        with mock.patch("providers.ollama_gpu_freigeben", side_effect=lambda: reihenfolge.append("ollama") or []), \
             mock.patch("config.load", return_value={}), \
             mock.patch.object(imagegen, "backend", return_value=imagegen.KREA), \
             mock.patch.dict(sys.modules, {"gguflokal": None, "stimme": None, "gpuzustand": None}):
            self.assertEqual("bild", paint())
            self.assertEqual(["ollama", "malen"], reihenfolge)
            reihenfolge.clear()
            with mock.patch.object(imagegen, "backend", return_value=imagegen.CLOUD):
                paint()
            self.assertEqual(["malen"], reihenfolge)

    def test_abschaltbar(self):
        import imagegen
        with mock.patch("config.load", return_value={"ollama_beim_malen_entladen": False}), \
             mock.patch("providers.ollama_gpu_freigeben") as f:
            imagegen._ollama_freigeben()
        f.assert_not_called()


if __name__ == "__main__":
    unittest.main()
