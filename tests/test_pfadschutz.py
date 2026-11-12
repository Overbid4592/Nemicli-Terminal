"""Windows-Pfade bleiben in Markdown ganz; ein halb geladenes torch bringt den Debugbericht nicht zum Absturz."""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import pfadschutz                                      # noqa: E402

FEHLER = (r"partially initialized module 'torch' from 'C:\Users\user\.ncli2\venv\Lib\site-packages\torch\__init__.py'"
          r" has no attribute 'cuda'")


class PfadTests(unittest.TestCase):
    def test_pfad_wird_code(self):
        s = pfadschutz.schuetzen(FEHLER)
        self.assertIn(r"`C:\Users\user\.ncli2\venv\Lib\site-packages\torch\__init__.py`", s)
        self.assertEqual(s, pfadschutz.schuetzen(s))                     # zweimal ändert nichts

    def test_satzzeichen_und_fett_bleiben_aussen(self):
        self.assertEqual(r"Liegt in **`C:\a\b_c_.txt`**.", pfadschutz.schuetzen(r"Liegt in **C:\a\b_c_.txt**."))
        self.assertEqual(r"Freigabe `\\server\share`, ok", pfadschutz.schuetzen(r"Freigabe \\server\share, ok"))

    def test_code_bleibt_unberuehrt(self):
        text = "```\nC:\\x\\__y__\n```\nund `C:\\z`"
        self.assertEqual(text, pfadschutz.schuetzen(text))
        self.assertEqual(r"Escape \* bleibt", pfadschutz.schuetzen(r"Escape \* bleibt"))

    def test_terminal_zeigt_den_ganzen_pfad(self):
        import io
        from rich.console import Console
        import ui
        out = io.StringIO()
        Console(file=out, width=200, color_system=None).print(ui.NemiMarkdown(FEHLER))
        self.assertIn(r"C:\Users\user\.ncli2\venv\Lib\site-packages\torch\__init__.py", out.getvalue())


class TorchHalbGeladenTests(unittest.TestCase):
    def test_debugbericht_fasst_halbes_torch_nicht_an(self):
        import debugbericht
        halb = types.ModuleType("torch")
        halb.__spec__ = types.SimpleNamespace(_initializing=True)        # wie mitten im Import, noch ohne .cuda
        with mock.patch.dict(sys.modules, {"torch": halb}):
            self.assertIsNone(debugbericht.torch_speicher())
        ohne_cuda = types.ModuleType("torch")                            # fertig, aber kaputt: auch kein Absturz
        with mock.patch.dict(sys.modules, {"torch": ohne_cuda}):
            self.assertIsNone(debugbericht.torch_speicher())


if __name__ == "__main__":
    unittest.main()
