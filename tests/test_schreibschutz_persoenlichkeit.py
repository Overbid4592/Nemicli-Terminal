"""Schreibschutz der Persönlichkeits-Dateien (Persoenlichkeiten/<Name>.md): ändert nur der Nutzer – außer die KI
hat die Datei selbst angelegt und seitdem als Einzige geändert. .sprueche.md bleibt frei."""
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


class Schreibschutz(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.daten = Path(tmp.name).resolve() / "Daten"
        self.pers = self.daten / "Persoenlichkeiten"
        programm = Path(tmp.name).resolve() / "Programm"
        for o in (self.pers, self.daten / "Profile", self.daten / "learned", programm):
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
        (self.pers / "alpha.sprueche.md").write_text("## Sprüche\nHallo\n", encoding="utf-8")
        PS.set_active("alpha")
        self.datei = self.pers / "alpha.md"

    def test_vom_nutzer_angelegt_ist_gesperrt(self):
        alt = self.datei.read_text(encoding="utf-8")
        for a in ({"tool": "datei_schreiben", "pfad": str(self.datei), "inhalt": "neu"},
                  {"tool": "datei_bearbeiten", "pfad": str(self.datei), "suchen": "eins", "ersetzen": "x"}):
            self.assertIn("Schreibgeschützt", sicherheit.sperr_grund(a) or "", a["tool"])     # vor der Rückfrage
            res = actions.ACTIONS[a["tool"]]["func"](a)
            self.assertFalse(res.ok)
        self.assertEqual(alt, self.datei.read_text(encoding="utf-8"))
        self.assertIn("Schreibgeschützt", actions._guard(str(self.datei).upper()))           # Groß/Klein egal

    def test_lesen_und_kopieren_von_bleiben(self):
        self.assertIsNone(actions._read_guard(str(self.datei)))
        self.assertIsNone(actions.charakter_md_vorab({"tool": "datei_lesen", "pfad": str(self.datei)}))
        self.assertIsNone(actions.charakter_md_vorab(
            {"tool": "datei_kopieren", "von": str(self.datei), "nach": str(self.daten / "kopie.md")}))
        self.assertIsNotNone(actions.charakter_md_vorab(
            {"tool": "datei_kopieren", "von": str(self.daten / "x.md"), "nach": str(self.datei)}))

    def test_sprueche_und_kern_bleiben_frei(self):
        self.assertIsNone(actions._guard(str(self.pers / "alpha.sprueche.md")))
        self.assertIsNone(actions._guard(str(self.daten / "Profile" / "Alpha" / "notiz.md")))

    def test_selbst_angelegt_darf_die_ki_aendern(self):
        neu = self.pers / "gamma.md"
        res = actions._datei_schreiben({"pfad": str(neu), "inhalt": "## Gamma\n- freundlich\n"})
        self.assertIn("Datei geschrieben", res if isinstance(res, str) else res.text)
        self.assertIn("anderen Persönlichkeit", actions._guard(str(neu)))   # gehört jetzt gamma, nicht alpha
        PS.set_active("gamma")
        self.assertIsNone(actions._guard(str(neu)))
        res = actions._datei_bearbeiten({"pfad": str(neu), "suchen": "freundlich", "ersetzen": "fröhlich"})
        self.assertIn("Datei bearbeitet", res if isinstance(res, str) else res.text)
        self.assertIsNone(actions._guard(str(neu)))                    # nach der eigenen Änderung weiter frei
        neu.write_text("## Gamma\n- vom Nutzer geändert\n", encoding="utf-8")
        self.assertIn("Schreibgeschützt", actions._guard(str(neu)))    # ab jetzt gehört sie dem Nutzer

    def test_liste_der_ki_dateien_ist_gesperrt(self):
        liste = self.daten / "learned" / actions.KI_DATEIEN_NAME
        a = {"tool": "datei_schreiben", "pfad": str(liste), "inhalt": '{"alpha.md": "x"}'}
        self.assertIn("NemiCLI selbst", sicherheit.sperr_grund(a) or "")
        self.assertIn("NemiCLI selbst", actions._guard(str(liste)))

    def test_befehl_mit_der_datei_gesperrt(self):
        for cmd in (f'Set-Content "{self.datei}" "neu"', f"Remove-Item {self.datei}",
                    f"python -c \"open(r'{self.datei}','w')\""):
            self.assertIn("Schreibgeschützt", actions.charakter_md_vorab({"tool": "befehl", "befehl": cmd}) or "", cmd)
        with mock.patch.object(actions, "_befehl_ordner", return_value=self.pers):
            self.assertIn("Schreibgeschützt", actions.charakter_md_vorab(
                {"tool": "befehl", "befehl": "Set-Content alpha.md neu"}) or "")
            self.assertIsNone(actions.charakter_md_vorab({"tool": "befehl", "befehl": "Get-ChildItem"}))
            self.assertIsNone(actions.charakter_md_vorab({"tool": "befehl", "befehl": "Get-Content alpha.md"}))
        self.assertIsNone(actions.charakter_md_vorab(
            {"tool": "befehl", "befehl": f'Add-Content "{self.pers / "alpha.sprueche.md"}" "Hi"'}))


if __name__ == "__main__":
    unittest.main()
