"""Ollama-Erkennung beim Einrichten (engines/setup.py).

python -m unittest tests.test_setup_ollama
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import setup as S                                      # noqa: E402


class OllamaErkennung(unittest.TestCase):
    def test_cli_gefunden(self):
        with mock.patch("shutil.which", return_value=r"C:\Programme\Ollama\ollama.exe"):
            self.assertEqual(S.ollama_cli(), r"C:\Programme\Ollama\ollama.exe")
            self.assertTrue(S.ollama_installed())

    def test_ohne_cli_fragt_den_dienst(self):
        with mock.patch("shutil.which", return_value=None), \
             mock.patch.object(S.P, "ollama_reachable", return_value=False):
            self.assertIsNone(S.ollama_cli())
            self.assertFalse(S.ollama_installed())


if __name__ == "__main__":
    unittest.main()
