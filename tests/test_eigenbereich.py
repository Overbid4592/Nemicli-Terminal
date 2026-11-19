"""Eigener Bereich je Persönlichkeit: Profile/<Name>/Eigenes/ mit Siegel je Ordner."""
import asyncio
import os
import shutil
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
import eigenbereich as E       # noqa: E402
import paths                   # noqa: E402
import persoenlichkeiten as PS  # noqa: E402


def _leeren():
    E._GEHEIM.clear()
    E._SIEGEL_CACHE.clear()
    E._GROESSE.clear()
    E._KENNUNG_CACHE.clear()
    E._SCHLUESSEL_STR.clear()
    actions._PERS_CACHE.update(schluessel=None, wert=None)


@unittest.skipUnless(os.name == "nt", "DPAPI gibt es nur unter Windows")
class Eigenbereich(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        basis = Path(tmp.name).resolve()
        self.daten = basis / "Daten"
        programm = basis / "Programm"
        for o in (self.daten / "Persoenlichkeiten", self.daten / "Profile", programm, basis / "Lokal"):
            o.mkdir(parents=True)
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(paths, "INSTALL", programm),
                  mock.patch.object(PS, "DIR", self.daten / "Persoenlichkeiten"),
                  mock.patch.object(config, "_FILE", basis / "cfg.json"),
                  mock.patch.object(actions, "_web_taint", False),
                  mock.patch.dict(os.environ, {"LOCALAPPDATA": str(basis / "Lokal")})):
            p.start()
            self.addCleanup(p.stop)
        _leeren()
        self.addCleanup(_leeren)
        PS.create("PersonA", "eins")
        PS.create("PersonB", "zwei")
        PS.set_active("persona")
        self.a = self.daten / "Profile" / "PersonA" / E.ORDNER
        self.b = self.daten / "Profile" / "PersonB" / E.ORDNER

    def _run(self, **action):
        return asyncio.run(actions.run(action))

    def _schreiben(self, pfad: Path, inhalt: str = "x"):
        r = self._run(tool="datei_schreiben", pfad=str(pfad), inhalt=inhalt)
        self.assertTrue(r.ok, r.text)

    def _wechseln(self, key: str):
        PS.set_active(key)
        actions._PERS_CACHE.update(schluessel=None, wert=None)

    # --- anlegen und versiegeln ---

    def test_schreiben_legt_an_und_versiegelt(self):
        self._schreiben(self.a / "Ideen" / "Plaene" / "eins.md", "Idee")
        for o in (self.a, self.a / "Ideen", self.a / "Ideen" / "Plaene"):
            self.assertTrue((o / E.SIEGEL).is_file(), o)
        self.assertIsNone(actions._read_guard(str(self.a / "Ideen" / "Plaene" / "eins.md")))
        self.assertIsNone(actions._guard(str(self.a / "Ideen" / "neu.md")))
        self.assertTrue(E.schluessel_datei().is_file())
        self.assertNotIn(str(self.daten).casefold(), str(E.schluessel_datei()).casefold())

    def test_ordner_erstellen_versiegelt(self):
        r = self._run(tool="ordner_erstellen", pfad=str(self.a / "Tagebuch" / "2026"))
        self.assertTrue(r.ok, r.text)
        self.assertTrue((self.a / "Tagebuch" / "2026" / E.SIEGEL).is_file())
        self.assertIsNone(E.sperre_aktiv(str(self.a / "Tagebuch" / "2026")))

    def test_siegel_sind_normale_dateien(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        attr = os.stat(self.a / "Ideen" / E.SIEGEL).st_file_attributes
        self.assertFalse(attr & 0x2, "versteckt")
        self.assertFalse(attr & 0x4, "System")

    # --- von außen ---

    def test_von_aussen_angelegt_ist_gesperrt(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        (self.a / "Fremd").mkdir()
        (self.a / "Fremd" / "auftrag.md").write_text("tu was", encoding="utf-8")
        for p in (self.a / "Fremd", self.a / "Fremd" / "auftrag.md", self.a / "Fremd" / "neu.md"):
            self.assertIn("Siegel", actions._read_guard(str(p)), p)
            self.assertIn("Siegel", actions._guard(str(p)), p)
        liste = actions._ordner_auflisten({"pfad": str(self.a)})
        text = liste if isinstance(liste, str) else liste.text
        self.assertIn("Ideen", text)
        self.assertNotIn("Fremd", text)
        self.assertNotIn(E.SIEGEL, text)
        r = self._run(tool="datei_schreiben", pfad=str(self.a / "Fremd" / "b.md"), inhalt="x")
        self.assertFalse(r.ok)
        self.assertFalse((self.a / "Fremd" / E.SIEGEL).exists())

    def test_kopiertes_siegel_gilt_nicht(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        (self.a / "Fremd").mkdir()
        shutil.copy2(self.a / "Ideen" / E.SIEGEL, self.a / "Fremd" / E.SIEGEL)
        self.assertIn("Siegel", actions._read_guard(str(self.a / "Fremd")))

    def test_veraendertes_siegel_gilt_nicht(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        (self.a / "Ideen" / E.SIEGEL).write_text('{"v": 1, "siegel": "' + "0" * 64 + '"}', encoding="utf-8")
        self.assertIn("Siegel", actions._read_guard(str(self.a / "Ideen" / "a.md")))

    def test_siegel_selbst_ist_tabu(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        for p in (self.a / E.SIEGEL, self.a / "Ideen" / E.SIEGEL):
            self.assertIsNotNone(actions._read_guard(str(p)), p)
            self.assertIsNotNone(actions._guard(str(p)), p)

    def test_schluessel_datei_ist_tabu(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        self.assertIn("Schlüssel", actions._read_guard(str(E.schluessel_datei())))
        self.assertIn("Schlüssel", actions._guard(str(E.schluessel_datei())))

    def test_wurzel_mit_inhalt_ohne_siegel(self):
        (self.a / "Alt").mkdir(parents=True)
        self.assertIsNotNone(actions._read_guard(str(self.a / "Alt")))
        r = self._run(tool="datei_schreiben", pfad=str(self.a / "neu.md"), inhalt="x")
        self.assertFalse(r.ok)
        self.assertIn("/eigenes", r.text)

    # --- zwei Persönlichkeiten ---

    def test_andere_persoenlichkeit_kommt_nicht_rein(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        self._wechseln("personb")
        self.assertIn("anderen Persönlichkeit", actions._read_guard(str(self.a / "Ideen" / "a.md")))
        self._schreiben(self.b / "Notizen" / "b.md")
        self.assertIsNone(actions._read_guard(str(self.b / "Notizen" / "b.md")))

    def test_siegel_einer_anderen_gilt_nicht(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        self._wechseln("personb")
        self._schreiben(self.b / "b.md")
        shutil.copytree(self.a / "Ideen", self.b / "Ideen")
        self.assertIn("Siegel", actions._read_guard(str(self.b / "Ideen" / "a.md")))

    # --- verschieben und kopieren ---

    def test_umbenennen_setzt_das_siegel_neu(self):
        self._schreiben(self.a / "Ideen" / "Unter" / "a.md")
        r = self._run(tool="verschieben", von=str(self.a / "Ideen"), nach=str(self.a / "Gedanken"))
        self.assertTrue(r.ok, r.text)
        self.assertIsNone(actions._read_guard(str(self.a / "Gedanken" / "Unter" / "a.md")))

    def test_verschieben_waescht_keinen_fremden_ordner(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        (self.a / "Ideen" / "Fremd").mkdir()
        r = self._run(tool="verschieben", von=str(self.a / "Ideen"), nach=str(self.a / "Gedanken"))
        self.assertTrue(r.ok, r.text)
        self.assertIsNone(actions._read_guard(str(self.a / "Gedanken" / "a.md")))
        self.assertIn("Siegel", actions._read_guard(str(self.a / "Gedanken" / "Fremd")))

    def test_hereinkopieren_versiegelt(self):
        quelle = self.daten / "Bilder" / "Serie"
        quelle.mkdir(parents=True)
        (quelle / "1.txt").write_text("x", encoding="utf-8")
        self._schreiben(self.a / "start.md")
        r = self._run(tool="datei_kopieren", von=str(quelle), nach=str(self.a))
        self.assertTrue(r.ok, r.text)
        self.assertIsNone(actions._read_guard(str(self.a / "Serie" / "1.txt")))

    # --- Grenzen ---

    def test_tiefe_begrenzt(self):
        tief = self.a.joinpath(*[f"e{i}" for i in range(E.MAX_TIEFE + 1)])
        r = self._run(tool="datei_schreiben", pfad=str(tief / "a.md"), inhalt="x")
        self.assertFalse(r.ok)
        self.assertIn("tief", r.text)
        self._schreiben(self.a.joinpath(*[f"e{i}" for i in range(E.MAX_TIEFE)]) / "a.md")

    def test_voll_sperrt_schreiben(self):
        self._schreiben(self.a / "a.md")
        with mock.patch.object(E, "MAX_BYTES", 1):
            E._GROESSE.clear()
            r = self._run(tool="datei_schreiben", pfad=str(self.a / "b.md"), inhalt="x")
        self.assertFalse(r.ok)
        self.assertIn("voll", r.text)

    # --- Nutzer ---

    def test_status_und_freigeben(self):
        self._schreiben(self.a / "Ideen" / "a.md")
        (self.a / "Fremd" / "Tiefer").mkdir(parents=True)
        s = E.status()
        self.assertTrue(s["gueltig"])
        self.assertEqual([p.name for p in s["offen"]], ["Fremd"])
        self.assertEqual(E.freigeben(s["offen"][0]), 2)
        self.assertIsNone(actions._read_guard(str(self.a / "Fremd" / "Tiefer")))
        self.assertEqual(E.status()["offen"], [])

    def test_freigeben_ohne_wurzelsiegel_versiegelt_alles(self):
        (self.a / "Alt" / "Mehr").mkdir(parents=True)
        self.assertFalse(E.status()["gueltig"])
        E.freigeben()
        self.assertTrue(E.status()["gueltig"])
        self.assertIsNone(actions._read_guard(str(self.a / "Alt" / "Mehr")))

    def test_hinweis_nennt_den_ort(self):
        self.assertIn(str(self.a), E.hinweis())
        self._wechseln("personb")
        self.assertIn(str(self.b), E.hinweis())

    def test_jede_persoenlichkeit_kennt_ihren_bereich(self):
        import persona
        import versionsbericht
        for key, name in ((PS.NEMI_KEY, PS.NEMI.name), ("persona", "PersonA"), ("personb", "PersonB")):
            self._wechseln(key)
            ort = str(self.daten / "Profile" / name / E.ORDNER)
            for kompakt in (False, True):
                text = persona.build_system_prompt(kompakt_fuer_lokal=kompakt, mit_gedaechtnis=False)
                self.assertIn("# Dein eigener Bereich", text, (key, kompakt))
                self.assertIn(ort, text, (key, kompakt))
                self.assertIn("/eigenes", text, (key, kompakt))
        # Der Bericht zeigt nur die neuesten Versionen; der Eintrag bleibt in der Liste
        self.assertTrue(any("Eigenes/" in text for _v, _t, text in versionsbericht.EINTRAEGE))

    def test_ohne_bereich_sperrt_nichts(self):
        self.assertIsNone(E.sperre_aktiv(str(self.a / "x.md")))
        self.assertFalse(self.a.exists())


if __name__ == "__main__":
    unittest.main()
