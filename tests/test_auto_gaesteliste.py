"""Auto-Modus mit Gästeliste: Riskantes läuft nur, wenn es freigegeben ist."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import actions     # noqa: E402
import coding      # noqa: E402
import modes       # noqa: E402
import sicherheit as S   # noqa: E402


class _Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name).resolve() / "projekt"
        self.ordner.mkdir()
        (self.ordner / "skript.py").write_text("print(1)\n", encoding="utf-8")
        (self.ordner / "alt.txt").write_text("x", encoding="utf-8")
        (self.ordner / "unter").mkdir()
        alt = S.WHITELIST_DATEI
        S.WHITELIST_DATEI = Path(self._tmp.name) / "befehl_whitelist.json"
        S.whitelist_neu_laden()
        self.addCleanup(lambda: (setattr(S, "WHITELIST_DATEI", alt), S.whitelist_neu_laden()))
        for ziel, name, wert in ((coding, "projekt", lambda: self.ordner), (actions, "_web_taint", False)):
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(modes.set_mode, "normal")
        modes.set_mode("auto")

    def urteil(self, act: dict) -> str:
        return modes.decide(act, actions.needs_confirm(act))

    def befehl(self, cmd: str) -> str:
        return self.urteil({"tool": "befehl", "befehl": cmd})


class Befehle(_Basis):
    def test_whitelist_und_auto_liste_laufen(self):
        for cmd in ("git status", "git commit -m test", "python -m pip install -r requirements.txt",
                    "ruff format .", "git stash"):
            self.assertEqual("run", self.befehl(cmd), cmd)

    def test_unbekannte_befehle_halten_an(self):
        for cmd in ("git push", "pip install requests", "winget install x", "irgendein-programm"):
            self.assertEqual("ask", self.befehl(cmd), cmd)

    def test_python_im_venv_wie_python(self):
        self.assertEqual("run", self.befehl("& .\\venv\\Scripts\\python.exe -m pytest -q"))

    def test_skript_nur_im_arbeitsordner(self):
        self.assertEqual("run", self.befehl("python skript.py --schnell"))
        self.assertEqual("ask", self.befehl("python fehlt.py"))
        self.assertEqual("ask", self.befehl("python ..\\anderes.py"))

    def test_umleitung_und_verkettung(self):
        self.assertEqual("ask", self.befehl("git stash > liste.txt"))
        self.assertEqual("run", self.befehl("git add . && git commit -m test"))
        self.assertEqual("ask", self.befehl("git add . && git push"))

    def test_frage_nennt_den_grund(self):
        self.assertIn("Auto hält an", S.frage({"tool": "befehl", "befehl": "git push"}))
        modes.set_mode("normal")
        self.assertNotIn("Auto hält an", S.frage({"tool": "befehl", "befehl": "git push"}))

    def test_normal_unveraendert(self):
        modes.set_mode("normal")
        self.assertEqual("ask", self.befehl("git stash"))


class Dateien(_Basis):
    def test_loeschen(self):
        self.assertEqual("run", self.urteil({"tool": "loeschen", "pfad": str(self.ordner / "alt.txt")}))
        self.assertEqual("ask", self.urteil({"tool": "loeschen", "pfad": str(self.ordner / "unter")}))
        self.assertEqual("ask", self.urteil({"tool": "loeschen", "pfad": str(self.ordner.parent / "x.txt")}))

    def test_ordner_verschieben_im_arbeitsordner(self):
        act = {"tool": "verschieben", "von": str(self.ordner / "unter"), "nach": str(self.ordner / "neu")}
        self.assertEqual("run", self.urteil(act))

    def test_zeitplan_und_programm_updates_halten_an(self):
        self.assertEqual("ask", self.urteil({"tool": "zeitplan", "aktion": "anlegen", "name": "t"}))
        self.assertEqual("ask", self.urteil({"tool": "updates_installieren", "ids": ["x"]}))
        self.assertIn("Auto hält an", S.frage({"tool": "zeitplan", "aktion": "anlegen", "name": "t"}))

    def test_auto_on_gleiche_regeln(self):
        modes.set_mode("autoan")
        self.assertEqual("ask", self.befehl("git push"))
        self.assertEqual("run", self.befehl("git status"))


class Datei(_Basis):
    def test_neue_datei_hat_auto_abschnitt(self):
        S.whitelist()
        daten = json.loads(S.WHITELIST_DATEI.read_text(encoding="utf-8"))
        self.assertEqual(S.AUTO_VORGABE, daten["auto_freigabe"])

    def test_ohne_abschnitt_gilt_die_vorgabe(self):
        S.WHITELIST_DATEI.write_text(json.dumps({"befehle": ["git status"]}), encoding="utf-8")
        S.whitelist_neu_laden()
        self.assertIn("git stash", S.auto_liste())

    def test_eigener_eintrag(self):
        S.WHITELIST_DATEI.write_text(json.dumps({"befehle": [], "auto_freigabe": ["mein-tool"]}),
                                     encoding="utf-8")
        S.whitelist_neu_laden()
        self.assertEqual("run", self.befehl("mein-tool --los"))
        self.assertEqual("ask", self.befehl("git stash"))

    def test_explorer_und_kopieren_fragen(self):
        S.whitelist()
        for cmd in ("explorer C:\\x\\prog.exe", "start-process explorer", "copy-item a.txt C:\\b.txt",
                    "copy a b", "cp a b"):
            self.assertEqual("ask", self.befehl(cmd), cmd)
        self.assertEqual("run", self.befehl("code ."))

    def test_alte_datei_wird_einmal_angehoben(self):
        S.WHITELIST_DATEI.write_text(json.dumps({"befehle": ["git status", "explorer", "Copy-Item", "cp"]}),
                                     encoding="utf-8")
        S.whitelist_neu_laden()
        self.assertEqual(["git status"], S.whitelist())
        daten = json.loads(S.WHITELIST_DATEI.read_text(encoding="utf-8"))
        self.assertEqual(S.WHITELIST_FASSUNG, daten["fassung"])
        daten["befehle"].append("explorer")                     # von Hand wieder eingetragen: bleibt
        S.WHITELIST_DATEI.write_text(json.dumps(daten), encoding="utf-8")
        S.whitelist_neu_laden()
        self.assertIn("explorer", S.whitelist())


if __name__ == "__main__":
    unittest.main()
