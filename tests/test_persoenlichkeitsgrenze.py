"""Jede Persönlichkeit sieht nur ihren eigenen Bereich in Persoenlichkeiten/ und Profile/."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions                 # noqa: E402
import config                  # noqa: E402
import paths                   # noqa: E402
import persoenlichkeiten as PS  # noqa: E402
import sicherheit              # noqa: E402


class Grenze(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.daten = Path(tmp.name).resolve() / "Daten"
        self.pers = self.daten / "Persoenlichkeiten"
        self.profile = self.daten / "Profile"
        programm = Path(tmp.name).resolve() / "Programm"
        for o in (self.pers / "Alpha", self.pers / "Beta", self.profile / "Alpha" / "Chats",
                  self.profile / "Beta" / "Chats", self.daten / "Bilder", programm):
            o.mkdir(parents=True)
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(paths, "INSTALL", programm),
                  mock.patch.object(PS, "DIR", self.pers),
                  mock.patch.object(config, "_FILE", Path(tmp.name) / "cfg.json"),
                  mock.patch.object(actions, "_web_taint", False)):
            p.start()
            self.addCleanup(p.stop)
        actions._PERS_CACHE.update(schluessel=None, wert=None)
        self.addCleanup(actions._PERS_CACHE.update, schluessel=None, wert=None)
        PS.create("Alpha", "eins")
        PS.create("Beta", "zwei")
        for name in ("alpha.sprueche.md", "beta.sprueche.md", "Alpha_neutral.png", "Beta_neutral.png",
                     "LIES-MICH.txt"):
            (self.pers / name).write_text("x", encoding="utf-8")
        (self.profile / "Beta" / "Chats" / "001.json").write_text("{}", encoding="utf-8")
        PS.set_active("alpha")

    def test_eigener_bereich_frei(self):
        for p in (self.pers / "alpha.md", self.pers / "alpha.sprueche.md", self.pers / "Alpha_neutral.png",
                  self.pers / "Alpha" / "neutral.png", self.profile / "Alpha" / "Chats" / "002.json",
                  self.pers / "LIES-MICH.txt", self.daten / "Bilder" / "a.png"):
            self.assertIsNone(actions._read_guard(str(p)), p)
            self.assertIsNone(actions._guard(str(p)), p)

    def test_fremder_bereich_gesperrt(self):
        for p in (self.pers / "beta.md", self.pers / "BETA.sprueche.md", self.pers / "Beta_neutral.png",
                  self.pers / "Beta" / "neutral.png", self.profile / "Beta",
                  self.profile / "Beta" / "Chats" / "001.json",
                  self.profile / "Alpha" / ".." / "Beta" / "Chats" / "001.json"):
            self.assertIn("anderen Persönlichkeit", actions._read_guard(str(p)), p)
            self.assertIn("anderen Persönlichkeit", actions._guard(str(p)), p)

    def test_sprueche_sind_keine_persoenlichkeit(self):
        self.assertEqual(set(PS.list_all()), {PS.NEMI_KEY, "alpha", "beta"})

    def test_wechsel_dreht_die_grenze(self):
        PS.set_active("beta")
        self.assertIsNone(actions._read_guard(str(self.pers / "beta.md")))
        self.assertIsNotNone(actions._read_guard(str(self.pers / "alpha.md")))

    def test_auflisten_und_suchen_zeigen_nur_eigenes(self):
        liste = actions._ordner_auflisten({"pfad": str(self.pers)})
        text = liste if isinstance(liste, str) else liste.text
        self.assertIn("alpha.md", text)
        self.assertNotIn("beta", text.lower())
        liste = actions._ordner_auflisten({"pfad": str(self.profile)})
        text = liste if isinstance(liste, str) else liste.text
        self.assertNotIn("Beta", text)
        treffer = actions._dateien_suchen({"pfad": str(self.daten), "muster": "*"})
        text = treffer if isinstance(treffer, str) else treffer.text
        self.assertIn("alpha.md", text)
        self.assertNotIn("beta", text.lower())

    def test_arbeitsbereich_schreibt_nicht_fremd(self):
        self.assertTrue(sicherheit.arbeitsbereich(
            {"tool": "datei_bearbeiten", "pfad": str(self.pers / "alpha.md"), "alt": "a", "neu": "b"}))
        self.assertFalse(sicherheit.arbeitsbereich(
            {"tool": "datei_bearbeiten", "pfad": str(self.pers / "beta.md"), "alt": "a", "neu": "b"}))

    def test_befehl(self):
        g = lambda c: actions._guard_command(c, lesend=True)       # noqa: E731
        self.assertIsNone(g(f'Get-Content "{self.pers / "alpha.md"}"'))
        self.assertIsNone(g(f"Get-ChildItem {self.profile / 'Alpha' / 'Chats'}"))
        self.assertIsNone(g(f"Get-ChildItem {self.daten / 'Bilder'} -Recurse"))
        self.assertIsNone(g("powershell -NoProfile -Command Get-Date"))
        for c in (f'Get-Content "{self.pers / "beta.md"}"',
                  f"Get-ChildItem {self.profile / 'Beta' / 'Chats'}",
                  f"Get-ChildItem {self.pers}",
                  f"Get-Content {self.pers}\\*.md",
                  f"Get-ChildItem {self.daten} -Recurse | Select-String x",
                  f"Select-String -Path {self.daten}\\*\\*.md -Pattern x",
                  f"Get-ChildItem {self.daten.anchor} -r -Filter *.md"):
            self.assertIsNotNone(g(c), c)

    def test_eigene_persoenlichkeit_nach_wechsel_bei_gleichem_cwd(self):
        with mock.patch.object(os, "getcwd", return_value=str(self.daten)):
            self.assertIsNotNone(actions._guard_command("Get-Content Persoenlichkeiten\\beta.md", lesend=True))
            self.assertIsNone(actions._guard_command("Get-Content Persoenlichkeiten\\alpha.md", lesend=True))


if __name__ == "__main__":
    unittest.main()
