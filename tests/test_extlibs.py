"""Die exe nimmt torch & Co. nur aus NemiCLIs eigenem venv – nie aus einem System-Python.

python -m unittest discover -s tests -p test_extlibs.py
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import extlibs  # noqa: E402
import wizard   # noqa: E402


class NurEigenesVenv(unittest.TestCase):
    def setUp(self):
        extlibs._state.clear()
        self.addCleanup(extlibs._state.clear)

    def test_einziger_kandidat_ist_das_eigene_venv(self):
        with mock.patch("paths.INSTALL", Path("C:/prog/NemiCLI")):
            kand = extlibs._candidates()
        self.assertEqual([Path("C:/prog/NemiCLI/venv/Lib/site-packages")], kand)

    def test_fragt_kein_system_python(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("paths.INSTALL", Path(d)), \
                mock.patch.object(extlibs, "FROZEN", True), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("System-Python gefragt")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("System-Python gefragt")):
            st = extlibs.enable()
        self.assertFalse(st["aktiv"])
        self.assertIn("/einrichten", st["grund"])

    def test_eigenes_venv_mit_torch_wird_eingebunden(self):
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "venv" / "Lib" / "site-packages"
            (site / "torch").mkdir(parents=True)
            (site / "torch" / "__init__.py").write_text("", encoding="utf-8")
            vorher = list(sys.path)
            self.addCleanup(lambda: sys.path.__setitem__(slice(None), vorher))
            with mock.patch("paths.INSTALL", Path(d)), mock.patch.object(extlibs, "FROZEN", True):
                st = extlibs.enable()
            self.assertTrue(st["aktiv"])
            self.assertEqual(str(site), sys.path[-1])


class TorchBau(unittest.TestCase):
    def _venv(self, d: Path, version_py: str | None) -> Path:
        py = d / "venv" / "Scripts" / "python.exe"
        py.parent.mkdir(parents=True)
        py.write_text("", encoding="utf-8")
        if version_py is not None:
            t = d / "venv" / "Lib" / "site-packages" / "torch"
            t.mkdir(parents=True)
            (t / "version.py").write_text(version_py, encoding="utf-8")
        return py

    def test_cpu_und_cuda_werden_unterschieden(self):
        with tempfile.TemporaryDirectory() as d:
            py = self._venv(Path(d), "__version__ = '2.14.0+cpu'\ncuda: Optional[str] = None\n")
            with mock.patch.object(wizard, "_venv_python", return_value=py):
                self.assertEqual("cpu", wizard._torch_bau())
        with tempfile.TemporaryDirectory() as d:
            py = self._venv(Path(d), "__version__ = '2.14.0+cu132'\ncuda: Optional[str] = '13.2'\n")
            with mock.patch.object(wizard, "_venv_python", return_value=py):
                self.assertEqual("13.2", wizard._torch_bau())
        with tempfile.TemporaryDirectory() as d:
            py = self._venv(Path(d), None)
            with mock.patch.object(wizard, "_venv_python", return_value=py):
                self.assertEqual("", wizard._torch_bau())


if __name__ == "__main__":
    unittest.main()
