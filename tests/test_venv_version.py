"""Das eigene venv muss zum Python der exe passen (cp312-Pakete laden nicht in 3.14): sonst nicht einbinden,
beim Einrichten beiseitelegen und neu anlegen."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import extlibs   # noqa: E402
import uvsetup   # noqa: E402


def _venv(ordner: Path, cfg: str) -> Path:
    (ordner / "Scripts").mkdir(parents=True)
    (ordner / "Scripts" / "python.exe").write_bytes(b"")
    (ordner / "Lib" / "site-packages" / "torch").mkdir(parents=True)
    (ordner / "Lib" / "site-packages" / "torch" / "__init__.py").write_text("")
    (ordner / "pyvenv.cfg").write_text(cfg, encoding="utf-8")
    return ordner


class VenvVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.venv = self.tmp / "venv"
        for ziel, name, wert in ((uvsetup, "VENV_DIR", self.venv), (uvsetup, "PYVER", "3.14")):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_beide_schreibweisen(self):
        _venv(self.venv, "home = x\nversion = 3.14.8\n")
        self.assertEqual(uvsetup.venv_version(), "3.14")
        (self.venv / "pyvenv.cfg").write_text("home = x\nuv = 0.12.13\nversion_info = 3.12\n", encoding="utf-8")
        self.assertEqual(uvsetup.venv_version(), "3.12")

    def test_passendes_venv_wird_benutzt(self):
        _venv(self.venv, "version = 3.14.8\n")
        self.assertEqual(uvsetup.venv_python(), self.venv / "Scripts" / "python.exe")

    def test_falsches_python_gilt_als_nicht_da_und_wird_beiseitegelegt(self):
        _venv(self.venv, "version_info = 3.12\n")
        self.assertIsNone(uvsetup.venv_python())
        hinweis = uvsetup._altes_venv_beiseite()
        self.assertFalse(self.venv.exists())
        self.assertTrue((self.tmp / "venv_alt_py3.12" / "pyvenv.cfg").exists())    # umbenannt, nicht gelöscht
        self.assertIn("venv_alt_py3.12", hinweis)
        _venv(self.venv, "version_info = 3.12\n")
        uvsetup._altes_venv_beiseite()
        self.assertTrue((self.tmp / "venv_alt_py3.12_2").exists())

    def test_passendes_venv_bleibt_wo_es_ist(self):
        _venv(self.venv, "version = 3.14.8\n")
        self.assertEqual(uvsetup._altes_venv_beiseite(), "")
        self.assertTrue(self.venv.exists())


class ExeBindetNurPassendesEin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        extlibs._state.clear()
        self.addCleanup(extlibs._state.clear)
        alt = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), alt))

    def _enable(self, cfg: str) -> dict:
        venv = _venv(self.tmp / "venv", cfg)
        site = venv / "Lib" / "site-packages"
        with patch.object(extlibs, "FROZEN", True), patch.object(extlibs, "PYVER", "3.14"), \
                patch.object(extlibs, "eigenes_venv", lambda: site), patch("os.add_dll_directory", create=True):
            return extlibs.enable()

    def test_anderes_python_nicht_einbinden(self):
        st = self._enable("version_info = 3.12\n")
        self.assertFalse(st["aktiv"])
        self.assertIn("für Python 3.12", st["grund"])
        self.assertIn("/einrichten", st["grund"])
        self.assertNotIn(str(self.tmp / "venv" / "Lib" / "site-packages"), sys.path)

    def test_passendes_python_einbinden(self):
        st = self._enable("version = 3.14.8\n")
        self.assertTrue(st["aktiv"])
        self.assertIn(str(self.tmp / "venv" / "Lib" / "site-packages"), sys.path)


if __name__ == "__main__":
    unittest.main()
