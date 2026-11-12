"""Offline-Tests: Jede NemiCLI-Kopie (Quelltext oder exe) ist für die KI komplett gesperrt.

python -m unittest discover -s tests -p test_nemicli_kopien.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import actions      # noqa: E402
import paths        # noqa: E402
import sicherheit   # noqa: E402


def _quelltext(ordner: Path) -> Path:
    for datei in ("main_start.py", "core/version.py", "tools/actions.py"):
        (ordner / datei).parent.mkdir(parents=True, exist_ok=True)
        (ordner / datei).write_text("# test\n", encoding="utf-8")
    return ordner


class _Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.register = self.tmp / "kopien.json"
        for name, wert in (("_KOPIEN_DATEI", self.register),):
            p = patch.object(actions, name, wert)
            p.start()
            self.addCleanup(p.stop)
        actions._KENNZEICHEN_CACHE.clear()
        actions._KOPIEN_CACHE.update(zeit=0.0, liste=[])
        self.addCleanup(actions._KOPIEN_CACHE.update, zeit=0.0, liste=[])
        self.addCleanup(sicherheit.schluessel_zurueck)


class KennzeichenTests(_Basis):
    def test_quelltext_kopie_gesperrt(self):
        kopie = _quelltext(self.tmp / "NemiCli")
        ziel = str(kopie / "core" / "persona.py")
        self.assertEqual(kopie, actions._absolut_gesperrt(ziel))
        self.assertEqual(kopie, actions._gesperrt_zum_aendern(str(kopie / "neu.py")))
        self.assertIn("Komplett gesperrt", actions._read_guard(ziel))
        self.assertIn("Schreibgeschützt", actions._guard(ziel))

    def test_installierte_exe_gesperrt(self):
        exe = self.tmp / "inst"
        (exe / "NemiCLIT2").mkdir(parents=True)
        (exe / "NemiCLI.exe").write_bytes(b"")
        self.assertEqual(exe, actions._absolut_gesperrt(str(exe / "NemiCLIT2" / "x.py")))

    def test_gewoehnlicher_ordner_frei(self):
        (self.tmp / "projekt" / "core").mkdir(parents=True)
        (self.tmp / "projekt" / "main_start.py").write_text("", encoding="utf-8")
        self.assertIsNone(actions._absolut_gesperrt(str(self.tmp / "projekt" / "main_start.py")))

    def test_eigene_daten_bleiben_frei(self):
        kopie = _quelltext(self.tmp / "NemiCli")                 # Daten-Ordner liegt in einer Kopie
        with patch.object(paths, "DATEN", kopie):
            for teil in ("Profile/example/example.json", "learned/notiz.md", "Persoenlichkeiten/example/x.md"):
                self.assertIsNone(actions._gesperrt_zum_aendern(str(kopie / teil)), teil)
            self.assertIsNotNone(actions._gesperrt_zum_aendern(str(kopie / "core" / "x.py")))

    def test_schluessel_oeffnet_nur_den_eigenen_ordner(self):
        kopie = _quelltext(self.tmp / "NemiCli")
        sicherheit.schluessel_setzen(5, "Test")
        self.assertIsNone(actions._absolut_gesperrt(str(actions._NEMI_ROOT / "main.py")))
        self.assertIsNotNone(actions._absolut_gesperrt(str(kopie / "main_start.py")))

    def test_befehl_nennt_kopie(self):
        kopie = _quelltext(self.tmp / "NemiCli")
        for cmd in (f'Get-Content "{kopie}\\main_start.py"', f"Set-Content {kopie}\\core\\x.py 1",
                    f"cd {kopie}; Get-ChildItem"):
            self.assertIn("Komplett gesperrt", actions._guard_command(cmd) or "", cmd)
        self.assertIsNone(actions._guard_command(f"Get-ChildItem {self.tmp}"))

    def test_befehl_laeuft_nie_in_einer_kopie(self):
        kopie = _quelltext(self.tmp / "NemiCli")
        ordner = actions.befehl_ordner(kopie / "core")
        self.assertIsNone(actions._absolut_gesperrt(str(ordner)))
        self.assertEqual(self.tmp, actions.befehl_ordner(self.tmp))


class RegisterTests(_Basis):
    def test_register_sperrt_auch_den_ordner_darueber(self):
        kopie = self.tmp / "eltern" / "NemiCli"
        kopie.mkdir(parents=True)                                # ohne Kennzeichen, nur eingetragen
        self.register.write_text(json.dumps({"kopien": [str(kopie)]}), encoding="utf-8")
        self.assertEqual(kopie, actions._absolut_gesperrt(str(kopie / "a.txt")))
        self.assertEqual(kopie, actions._sperre_darunter(str(self.tmp / "eltern")))
        self.assertIsNotNone(actions._guard_command(f"Get-Content {kopie}\\a.txt"))

    def test_eintragen_einmal_und_nicht_im_testlauf(self):
        kopie = _quelltext(self.tmp / "NemiCli")
        actions._kopie_eintragen(kopie)
        self.assertFalse(self.register.exists())                # Testlauf: nichts schreiben
        with patch.object(paths, "_testlauf", return_value=False):
            actions._kopie_eintragen(kopie)
            actions._kopie_eintragen(kopie)
            actions._kopie_eintragen(self.tmp)                   # ohne Kennzeichen: nicht eintragen
        self.assertEqual([str(kopie)], json.loads(self.register.read_text(encoding="utf-8"))["kopien"])

    def test_register_selbst_gesperrt(self):
        self.assertIsNotNone(actions._absolut_gesperrt(str(self.register)))


if __name__ == "__main__":
    unittest.main()
