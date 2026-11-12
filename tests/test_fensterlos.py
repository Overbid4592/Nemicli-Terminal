"""Fenster-App (NemiCLI.exe) für Wache, Vollscan und Zeitplan-Aufträge."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import paths   # noqa: E402
import zeitplan   # noqa: E402
from wache import vollscan, zugang   # noqa: E402


class Fensterlos(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name)
        (self.ordner / "NemiCLIc.exe").write_bytes(b"MZ")
        p = patch.object(paths, "INSTALL", self.ordner)
        p.start()
        self.addCleanup(p.stop)

    def test_ohne_fenster_nimmt_nemicliw_wenn_da(self):
        with patch.object(sys, "executable", str(self.ordner / "NemiCLIc.exe")):
            self.assertEqual(paths.exe_ohne_fenster(), Path(sys.executable))   # noch keine Fenster-App
            (self.ordner / "NemiCLI.exe").write_bytes(b"MZ")
            self.assertEqual(paths.exe_ohne_fenster(), self.ordner / "NemiCLI.exe")
            self.assertEqual(paths.exe_mit_fenster(), self.ordner / "NemiCLIc.exe")

    def test_wache_vollscan_auftrag_starten_fensterlos(self):
        (self.ordner / "NemiCLI.exe").write_bytes(b"MZ")
        w = str(self.ordner / "NemiCLI.exe")
        with patch.object(sys, "frozen", True, create=True), patch.object(zeitplan, "_FROZEN", True):
            self.assertEqual(zugang.wache_befehl(self.ordner), [w, "--wache"])
            self.assertEqual(vollscan.befehl(self.ordner), [w, "--vollscan"])
            self.assertEqual(zeitplan.programm(), (w, "--auftrag"))

    def test_autostart_nur_aus_der_exe_auffrischen(self):
        # Aus dem Quelltext darf der Windows-Autostart nie umgeschrieben werden.
        with patch.object(zugang, "autostart_setzen") as setzen:
            self.assertFalse(zugang.autostart_auffrischen(self.ordner))
            setzen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
