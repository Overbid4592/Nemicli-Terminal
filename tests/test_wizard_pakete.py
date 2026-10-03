"""Tests für den Paket-Schritt des Einrichtungs-Assistenten (ohne Installation)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import wizard as W   # noqa: E402

REQ = """\
anthropic
# Kommentar
textual>=8.2,<9
torch==2.14.0+cu132
torchvision
python_dotenv
rembg[cpu]
opencv-python<5   # mit Kommentar
--extra-index-url https://example.invalid/simple
PySide6-Essentials
"""


class WizardPaketeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        (self.t / "requirements.txt").write_text(REQ, encoding="utf-8")
        self._p = patch.object(W, "_requirements_datei", lambda: self.t / "requirements.txt")
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_torch_bleibt_draussen(self):
        pakete = W.requirements_ohne_torch()
        self.assertEqual(pakete, ["anthropic", "textual>=8.2,<9", "python_dotenv",
                                  "rembg[cpu]", "opencv-python<5", "PySide6-Essentials"])

    def test_torch_anzeigename(self):
        self.assertEqual(W.torch_anzeigename("cu132"), "PyTorch CUDA 13.2")
        self.assertEqual(W.torch_anzeigename("cu126"), "PyTorch CUDA 12.6")
        self.assertEqual(W.torch_anzeigename("cpu"), "PyTorch (CPU)")

    def test_fehlende_pakete_nach_dist_info(self):
        site = self.t / "venv" / "Lib" / "site-packages"
        site.mkdir(parents=True)
        (self.t / "venv" / "Scripts").mkdir()
        py = self.t / "venv" / "Scripts" / "python.exe"
        py.write_bytes(b"")
        for d in ("anthropic-0.70.0", "python_dotenv-1.1.0", "PySide6_Essentials-6.9.0"):
            (site / f"{d}.dist-info").mkdir()
        with patch.object(W, "_venv_python", lambda: py):
            fehlt = W._fehlende_pakete()
        self.assertEqual(fehlt, ["textual", "rembg", "opencv-python"])

    def test_pakete_installiert_torch_vorher_und_getrennt(self):
        aufrufe = []
        with patch.object(W, "_venv_python", lambda: Path("py")), \
             patch.object(W, "_torch_bau", lambda: ""), \
             patch.object(W.S, "gpu_info", lambda: {"vendor": "nvidia"}), \
             patch.object(W, "install_torch", lambda on_status=None: aufrufe.append("torch")), \
             patch.object(W.uvsetup, "pip_install",
                          lambda pakete, on_status=None, extra=None: aufrufe.append(pakete)):
            W.install_pakete()
        self.assertEqual(aufrufe[0], "torch")
        self.assertEqual(len(aufrufe), 2)
        self.assertFalse(any(W._paketname(p) in W._TORCH_PAKETE for p in aufrufe[1]))


if __name__ == "__main__":
    unittest.main()
