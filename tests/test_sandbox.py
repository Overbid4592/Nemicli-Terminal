"""Tests für tools/sandbox.py – echte Läufe im Windows-AppContainer.

python -m unittest tests.test_sandbox
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import sandbox as S                                    # noqa: E402

_WIN = os.name == "nt"


@unittest.skipUnless(_WIN and sys.prefix != sys.base_prefix, "nur Windows mit venv")
class SandboxTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._p = patch.object(S, "ARBEIT", Path(cls.tmp.name) / "NemiSandbox")
        cls._p.start()
        S.vorbereiten()
        cls.geheim = Path(cls.tmp.name) / "geheim.txt"
        cls.geheim.write_text("nicht lesbar", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._p.stop()
        cls.tmp.cleanup()

    def _lauf(self, code, timeout=30, stopp=None):
        ordner = S.ARBEIT / f"t_{time.time_ns()}"
        ordner.mkdir(parents=True)
        (ordner / "t.py").write_text(code, encoding="utf-8")
        return S.python_ausfuehren(ordner / "t.py", timeout, stopp)

    def test_ausgabe_und_exitcode(self):
        e = self._lauf("import sys; print('hallo ä'); print('fehler', file=sys.stderr); sys.exit(3)")
        self.assertEqual(e.code, 3)
        self.assertIn("hallo ä", e.ausgabe)
        self.assertIn("fehler", e.fehler)

    def test_arbeitsordner_schreibbar(self):
        e = self._lauf("import pathlib; pathlib.Path('a.txt').write_text('x'); print('ok')")
        self.assertEqual(e.code, 0, e.fehler)

    def test_dateien_ausserhalb_gesperrt(self):
        for ziel in (self.geheim, ROOT / "main.py", Path.home() / "Desktop"):
            e = self._lauf(f"import os\np = r'{ziel}'\n"
                           "try:\n    os.listdir(p) if os.path.isdir(p) else open(p).read()\n"
                           "    print('OFFEN')\nexcept PermissionError:\n    print('GESPERRT')")
            self.assertIn("GESPERRT", e.ausgabe, (ziel, e.ausgabe, e.fehler))

    def test_registry_schreiben_gesperrt(self):
        e = self._lauf("import winreg\ntry:\n    winreg.CreateKey(winreg.HKEY_CURRENT_USER, "
                       "r'Software\\\\NemiSandboxTest')\n    print('OFFEN')\n"
                       "except PermissionError:\n    print('GESPERRT')")
        self.assertIn("GESPERRT", e.ausgabe, e.fehler)

    def test_zeitueberschreitung_beendet(self):
        t0 = time.monotonic()
        e = self._lauf("import time; time.sleep(60)", timeout=2)
        self.assertTrue(e.zeit_ueberschritten)
        self.assertIsNone(e.code)
        self.assertLess(time.monotonic() - t0, 15)

    def test_stopp_von_aussen(self):
        stopp = threading.Event()
        threading.Timer(1.0, stopp.set).start()
        e = self._lauf("import time; time.sleep(60)", timeout=30, stopp=stopp)
        self.assertTrue(e.abgebrochen)

    def test_ordner_ausserhalb_der_sandbox_abgewiesen(self):
        with self.assertRaises(S.SandboxFehler):
            S.ausfuehren(["x"], Path(self.tmp.name))

    def test_tempfile_im_container(self):
        e = self._lauf("import tempfile, pathlib\nd = tempfile.mkdtemp()\n"
                       "pathlib.Path(d, 'x.txt').write_text('x'); print('ok', d)")
        self.assertEqual(e.code, 0, e.fehler)

    def test_projekt_vor_ort_nur_lesen_und_entziehen(self):
        projekt = Path(self.tmp.name) / "projekt"
        (projekt / "pkg").mkdir(parents=True)
        (projekt / "pkg" / "__init__.py").write_text("WERT = 42\n", encoding="utf-8")
        (projekt / "daten.txt").write_text("hallo", encoding="utf-8")
        skript = projekt / "start.py"
        skript.write_text("import pkg, pathlib\nprint('wert', pkg.WERT)\n"
                          f"print(pathlib.Path(r'{projekt}', 'daten.txt').read_text())\n"
                          "try:\n"
                          f"    pathlib.Path(r'{projekt}', 'neu.txt').write_text('x'); print('SCHREIBEN OFFEN')\n"
                          "except PermissionError:\n    print('SCHREIBEN GESPERRT')\n", encoding="utf-8")
        with patch.object(S, "_projekt_verboten", return_value=None):
            S.projekt_freigeben(projekt)
        self.assertEqual(S.projekt_von(skript), projekt)
        lauf = S.ARBEIT / f"p_{time.time_ns()}"
        lauf.mkdir()
        e = S.python_ausfuehren(skript, 30, ordner=lauf)
        self.assertEqual(e.code, 0, e.fehler)
        self.assertIn("wert 42", e.ausgabe)
        self.assertIn("hallo", e.ausgabe)
        self.assertIn("SCHREIBEN GESPERRT", e.ausgabe)

        S.projekt_entziehen(projekt)
        self.assertIsNone(S.projekt_von(skript))
        kopie = lauf / "lies.py"
        kopie.write_text(f"open(r'{projekt / 'daten.txt'}').read()", encoding="utf-8")
        e = S.python_ausfuehren(kopie, 30)
        self.assertIn("PermissionError", e.fehler)

    def test_projekt_lesen_und_schreiben(self):
        projekt = Path(self.tmp.name) / "wissen"
        projekt.mkdir()
        skript = projekt / "scanner.py"
        skript.write_text("import os, pathlib\npathlib.Path('stand.db').write_text('ok')\n"
                          "print('cwd', os.getcwd())\n", encoding="utf-8")
        with patch.object(S, "_projekt_verboten", return_value=None):
            S.projekt_freigeben(projekt, schreiben=True)
        self.assertTrue(S.schreibbar(projekt))
        e = S.python_ausfuehren(skript, 30)
        self.assertEqual(e.code, 0, e.fehler)
        self.assertEqual((projekt / "stand.db").read_text(), "ok")
        self.assertIn(str(projekt), e.ausgabe)

        with patch.object(S, "_projekt_verboten", return_value=None):
            S.projekt_freigeben(projekt)                  # zurück auf nur lesen
        self.assertFalse(S.schreibbar(projekt))
        lauf = S.ARBEIT / f"w_{time.time_ns()}"
        lauf.mkdir()
        e = S.python_ausfuehren(skript, 30, ordner=lauf)
        self.assertEqual(e.code, 0, e.fehler)             # schreibt jetzt in den Lauf-Ordner
        (lauf / "x.py").write_text(f"open(r'{projekt / 'neu.txt'}', 'w')", encoding="utf-8")
        self.assertIn("PermissionError", S.python_ausfuehren(lauf / "x.py", 30).fehler)
        S.projekt_entziehen(projekt)

    def test_verbotene_projektordner(self):
        for ordner in (Path("C:/"), Path.home(), Path.home() / "Desktop", S._INSTALL,
                       Path(os.environ.get("SystemRoot", r"C:\Windows"))):
            if ordner.is_dir():
                with self.assertRaises(S.SandboxFehler, msg=str(ordner)):
                    S.projekt_freigeben(ordner)

    def test_paketnamen_ohne_optionen(self):
        for falsch in ("--index-url=http://x", "-r req.txt", "paket; rm", "../x", "git+https://x"):
            with self.assertRaises(S.SandboxFehler, msg=falsch):
                S.pakete_installieren([falsch])
        for gut in ("requests", "numpy>=1.26", "pydantic[email]==2.8.2", "tomli-w"):
            self.assertTrue(S._PAKETNAME.match(gut), gut)


if __name__ == "__main__":
    unittest.main()
