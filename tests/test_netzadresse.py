"""Adressen lokaler Dienste: http nur zum eigenen Rechner, sonst https (Ollama, ComfyUI, SD-WebUI)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import netzadresse as N   # noqa: E402
import providers as P     # noqa: E402


class Lokal(unittest.TestCase):
    def test_eigener_rechner(self):
        for name in ("localhost", "LOCALHOST", "127.0.0.1", "127.8.9.10", "::1", "[::1]"):
            self.assertTrue(N.lokal(name), name)
        for name in ("192.168.1.5", "10.0.0.2", "pc.local", "example.com", "", "0.0.0.0"):
            self.assertFalse(N.lokal(name), name)


class Sicher(unittest.TestCase):
    def test_ohne_schema(self):
        self.assertEqual("http://127.0.0.1:8188", N.sicher("127.0.0.1:8188"))
        self.assertEqual("http://localhost:7860", N.sicher("localhost:7860"))
        self.assertEqual("https://192.168.1.5:8188", N.sicher("192.168.1.5:8188"))
        self.assertEqual("https://pc.local:8188", N.sicher("pc.local:8188"))

    def test_http_zu_fremdem_rechner_wird_https(self):
        self.assertEqual("https://192.168.1.5:8188/x?a=1", N.sicher("http://192.168.1.5:8188/x?a=1"))
        self.assertEqual("http://[::1]:7860", N.sicher("http://[::1]:7860"))
        self.assertEqual("https://a.example", N.sicher("https://a.example"))
        self.assertEqual("", N.sicher(""))


class OllamaHost(unittest.TestCase):
    def test_normieren(self):
        self.assertEqual("http://127.0.0.1:11434", P._norm_host(""))
        self.assertEqual("http://127.0.0.1:11434", P._norm_host("http://0.0.0.0:11434"))
        self.assertEqual("http://localhost:11500", P._norm_host("localhost:11500"))
        self.assertEqual("http://[::1]:11434", P._norm_host("http://[::1]:11434"))
        self.assertEqual("https://192.168.1.9:11434", P._norm_host("192.168.1.9"))      # Port bleibt
        self.assertEqual("https://192.168.1.9:11434", P._norm_host("http://192.168.1.9:11434"))
        self.assertEqual("https://ollama.example.com", P._norm_host("https://ollama.example.com"))


class BildDienste(unittest.TestCase):
    def test_comfyui_und_sdwebui(self):
        import comfyui
        import config
        import sdwebui
        for modul in (comfyui, sdwebui):
            with mock.patch.object(config, "load", return_value={modul._CONFIG_KEY_HOST: "http://192.168.1.7:9"}):
                self.assertEqual("https://192.168.1.7:9", modul.host())
            with mock.patch.object(config, "load", return_value={modul._CONFIG_KEY_HOST: "127.0.0.1:9"}):
                self.assertEqual("http://127.0.0.1:9", modul.host())


if __name__ == "__main__":
    unittest.main()
