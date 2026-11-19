"""Taskleisten-Identität der Fenster (ui/taskleiste.py).

python -m unittest tests.test_taskleiste
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import taskleiste as T                                 # noqa: E402


class Werte(unittest.TestCase):
    def test_kennung_zuletzt_und_alles_dabei(self):
        w = T.werte("NemiCLI.Terminal")
        self.assertEqual(T._PID["kennung"], w[-1][0])
        self.assertEqual("NemiCLI.Terminal", w[-1][1])
        self.assertEqual({"befehl", "symbol", "name", "kennung"}, {n for n, p in T._PID.items() if p in dict(w)})
        self.assertIn((T._PID["name"], "NemiCLI"), w)

    def test_nur_das_terminal_laesst_sich_anheften(self):
        self.assertNotIn(T._PID["nicht_anheften"], dict(T.werte("NemiCLI.Terminal")))
        self.assertIs(True, dict(T.werte("NemiCLI.GUI"))[T._PID["nicht_anheften"]])

    def test_symbol_im_quelltext_und_in_der_exe(self):
        self.assertTrue(T.symbol_quelle().endswith("nemicli.ico,0"))
        self.assertTrue((ROOT / "nemicli.ico").is_file())
        with mock.patch.object(T.sys, "frozen", True, create=True), \
                mock.patch.object(T.sys, "executable", r"C:\Programm\NemiCLI.exe"):
            self.assertEqual(r"C:\Programm\NemiCLI.exe,0", T.symbol_quelle())
            self.assertEqual(r"C:\Programm\NemiCLI.exe", T.startbefehl())

    def test_ohne_fenster_nichts(self):
        self.assertFalse(T.kennzeichnen(0, "NemiCLI.Test"))


if __name__ == "__main__":
    unittest.main()
