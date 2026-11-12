"""Offline-Tests: Programm-Ordner-Sperre (exe), WebUI-Links, PowerShell-Teilausdrücke,
Bildbeschreiber ohne Deadlock, Kontext-Eingabe mit Nachkomma.

python -m unittest discover -s tests -p test_luecken_fixes.py
"""

import sys
import tempfile
import threading
import types
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


class ProgrammOrdnerTests(unittest.TestCase):
    def test_sperre_folgt_dem_programm_ordner_nicht_dem_modul(self):
        self.assertEqual(Path(paths.INSTALL).resolve(), actions._NEMI_ROOT)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        exe_ordner = Path(tmp.name) / ".ncli2"
        with patch.object(paths, "INSTALL", exe_ordner):
            self.assertEqual(exe_ordner.resolve(), actions._programm_ordner())

    def test_env_und_config_im_programm_ordner_gesperrt(self):
        for name in (".env", "nemicli.config.json", "ModelGGUF\\modell.gguf"):
            self.assertIsNotNone(actions._absolut_gesperrt(str(Path(paths.INSTALL) / name)), name)


class WebUITests(unittest.TestCase):
    def test_esc_maskiert_anfuehrungszeichen(self):
        import webui
        zeile = next(z for z in webui.PAGE.splitlines() if z.startswith("function esc("))
        self.assertIn("&quot;", zeile)
        self.assertIn("&#39;", zeile)


class PowerShellTests(unittest.TestCase):
    def test_teilausdruck_ist_nie_harmlos(self):
        for cmd in ('echo "$(rm C:\\x)"', 'Get-ChildItem "$(rm C:\\x)"', 'mkdir $(iex x)',
                    'Write-Output @(del x)', 'echo (rm x)', 'Get-ChildItem "@(del x)"'):
            self.assertFalse(sicherheit.befehl_harmlos(cmd), cmd)
        self.assertIsNotNone(actions._nur_lesend('Get-ChildItem "$(rm C:\\x)"'))

    def test_normale_befehle_bleiben_wie_sie_waren(self):
        self.assertTrue(sicherheit.befehl_harmlos("Get-ChildItem C:\\Users"))
        self.assertTrue(sicherheit.befehl_harmlos('Get-Counter "\\Processor(_Total)\\% Processor Time"'))
        self.assertIsNone(actions._nur_lesend('Get-ChildItem "C:\\Program Files (x86)"'))
        self.assertFalse(actions.ausdruck_in_text("echo '$(nur text)'"))


class BildbeschreiberTests(unittest.TestCase):
    def test_modellwechsel_haengt_nicht(self):
        import bildbeschreiber as B
        geschlossen = []

        class _GG:
            def close(self):
                geschlossen.append(1)

        class _Engine:
            def __init__(self, *a, **k):
                self.gg, self.vision = _GG(), None

            def enable_vision(self, _p):
                pass

        alt = _Engine()
        neu = (Path("neu.gguf"), Path("mmproj-neu.gguf"))
        fertig = []

        def lauf():
            with B._lock:
                fertig.append(B._engine())

        with patch.object(B, "_helfer", (Path("alt.gguf"), alt)), patch.object(B, "finden", lambda: neu), \
                patch.object(B.gguflokal, "_torch_bereit", lambda: None), \
                patch.dict(sys.modules, {"ggufengine": types.SimpleNamespace(Engine=_Engine)}):
            t = threading.Thread(target=lauf, daemon=True)
            t.start()
            t.join(5)
            self.assertFalse(t.is_alive(), "Deadlock: _engine() wartet auf die eigene Sperre")
            self.assertEqual(neu[0], B._helfer[0])
        self.assertEqual([1], geschlossen)
        self.assertEqual(1, len(fertig))


class KontextTests(unittest.TestCase):
    def test_eingaben(self):
        import main_befehle as MB
        for eingabe, token in (("25.6k", 25600), ("25,6k", 25600), ("32k", 32768), ("32 K", 32768),
                               ("32768", 32768), ("32.768", 32768), ("8k", 8192)):
            self.assertEqual(token, MB.kontext_zahl(eingabe), eingabe)
        with self.assertRaises(ValueError):
            MB.kontext_zahl("viel")


if __name__ == "__main__":
    unittest.main()
