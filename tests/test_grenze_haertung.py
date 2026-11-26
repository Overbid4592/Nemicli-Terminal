"""Härtung der Sicherheits-Grenze: Freigaben, Trenner, Arbeitsordner, Auto-Grenze, Schlüssel, Verknüpfungen."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions as A            # noqa: E402
import coding                  # noqa: E402
import eigenbereich as E       # noqa: E402
import paths                   # noqa: E402
import persoenlichkeiten as PS  # noqa: E402
import schutzsoftware          # noqa: E402
import shellfrei               # noqa: E402
import sicherheit as S         # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

    def patch(self, ziel, name, wert):
        p = mock.patch.object(ziel, name, wert)
        p.start()
        self.addCleanup(p.stop)


class Freigaben(unittest.TestCase):
    def test_nur_exakte_lokale_namen(self):
        for h in ("localhost", "127.0.0.1", "127.5.5.5", "::1"):
            self.assertTrue(A._lokaler_host(h), h)
        for h in ("localhost.example", "localhostx", "127.0.0.1.example", "127.example"):
            self.assertFalse(A._lokaler_host(h), h)
        self.assertIsNone(A._kandidaten(r"\\localhost.example\C$\x.txt"))


class Trenner(unittest.TestCase):
    def test_wagenruecklauf_trennt(self):
        with mock.patch.object(S, "whitelist", return_value=sorted(S.WHITELIST_VORGABE)):
            self.assertFalse(S.befehl_harmlos("git status\rRemove-Item x.txt"))
            self.assertTrue(S.befehl_harmlos("git status"))
        self.assertIsNotNone(A._nur_lesend("Get-Date\rcalc"))

    def test_umleitung_mit_nummer(self):
        for c in ("Get-Date 1> x.txt", "Get-Date 2> x.txt", "Get-Date *> x.txt", "Get-Date 2>> x.txt"):
            self.assertIsNotNone(A._nur_lesend(c), c)
        for c in ("Get-ChildItem 2>&1", "Get-ChildItem 2>$null"):
            self.assertIsNone(A._nur_lesend(c), c)


class Optionen(unittest.TestCase):
    def test_whitelist_ohne_gefaehrliche_schalter(self):
        with mock.patch.object(S, "whitelist", return_value=sorted(S.WHITELIST_VORGABE)):
            for c in ("git fetch --upload-pack=prog.exe", 'git fetch "--upload-pack=prog.exe"',
                      "git fetch --upload`-pack=prog.exe", "code --install-extension x.vsix",
                      "git diff --output=x.txt", "git diff --ext-diff"):
                self.assertFalse(S.befehl_harmlos(c), c)
            self.assertTrue(S.befehl_harmlos("git fetch"))
            self.assertTrue(S.befehl_harmlos("code ."))


class Git(unittest.TestCase):
    def test_git_ohne_programme_aus_der_konfiguration(self):
        self.assertEqual(f"git {A.GIT_HAERTUNG} status --short", A._git_gehaertet("git status --short"))
        self.assertEqual(f"git {A.GIT_HAERTUNG} log --no-ext-diff --no-textconv -3",
                         A._git_gehaertet("git log -3"))
        for teil in ("core.hooksPath=NUL", "core.fsmonitor=false", "log.showSignature=false", "--no-optional-locks"):
            self.assertIn(teil, A.GIT_HAERTUNG)
        self.assertEqual("Get-Date", A._git_gehaertet("Get-Date"))
        self.assertEqual("git push", A._git_gehaertet("git push"))
        self.assertIsNotNone(shellfrei._nativ_grund("git", ["diff", "--textconv"]))


class Arbeitsordner(_Tmp):
    def setUp(self):
        super().setUp()
        self.projekt = self.tmp / "projekt"
        self.gesperrt = self.projekt / "gesperrt"
        self.gesperrt.mkdir(parents=True)
        (self.projekt / "skript.py").write_text("print(1)\n", encoding="utf-8")
        self.frei = self.tmp / "frei"
        self.frei.mkdir()
        (self.frei / "skript.py").write_text("print(1)\n", encoding="utf-8")
        self.patch(A, "_absolut_bases", lambda: [self.gesperrt])

    def test_ordner_ueber_einer_sperre_wird_umgeleitet(self):
        self.assertNotEqual(self.projekt, A.befehl_ordner(self.projekt))
        self.assertEqual(self.frei, A.befehl_ordner(self.frei))

    def test_coding_start_nicht_ueber_einer_sperre(self):
        with mock.patch.object(coding, "starten") as starten:
            r = A._coding_start({"projekt": str(self.projekt)})
        self.assertFalse(r.ok)
        starten.assert_not_called()

    def test_auto_prueft_den_echten_ausfuehrungsordner(self):
        with mock.patch.object(coding, "arbeitsordner", return_value=self.projekt):
            self.assertIsNone(S.ausfuehrungsordner())
        with mock.patch.object(coding, "arbeitsordner", return_value=self.frei):
            self.assertEqual(self.frei, S.ausfuehrungsordner())
        with mock.patch.object(S, "whitelist", return_value=[]), mock.patch.object(S, "auto_liste", return_value=[]):
            with mock.patch.object(S, "ausfuehrungsordner", return_value=None):
                self.assertFalse(S.auto_befehl_frei("python skript.py"))
            with mock.patch.object(S, "ausfuehrungsordner", return_value=self.frei):
                self.assertTrue(S.auto_befehl_frei("python skript.py"))

    def test_auto_haelt_in_der_sandbox_an(self):
        with mock.patch.object(S, "ausfuehrungsordner", return_value=None), \
                mock.patch.object(S, "whitelist", return_value=sorted(S.WHITELIST_VORGABE)):
            self.assertIn("Sandbox", S.auto_grund({"tool": "befehl", "befehl": "git add ."}))
            self.assertIsNone(S.auto_grund({"tool": "befehl", "befehl": "Get-ChildItem"}))


class AutoGrenze(_Tmp):
    def test_coding_start_mit_neuem_projekt_fragt(self):
        ziel = self.tmp / "app"
        with mock.patch.object(coding, "projekt", return_value=None):
            self.assertIsNotNone(S.auto_grund({"tool": "coding_start", "projekt": str(ziel)}))
        with mock.patch.object(coding, "projekt", return_value=ziel):
            self.assertIsNone(S.auto_grund({"tool": "coding_start", "projekt": str(ziel)}))
        self.assertEqual(["yes", "no"], [k for k, _ in S.optionen({"tool": "coding_start", "projekt": str(ziel)})])


class Arbeitsbereich(_Tmp):
    def test_punkt_am_ordnernamen_zaehlt_wie_ohne(self):
        daten = self.tmp / "Daten"
        daten.mkdir()
        self.patch(paths, "DATEN", daten)
        self.patch(paths, "INSTALL", self.tmp / "Programm")
        self.patch(A, "_web_taint", False)
        act = {"tool": "datei_schreiben", "inhalt": "x"}
        self.assertTrue(S.arbeitsbereich({**act, "pfad": str(daten / "notiz.txt")}))
        self.assertFalse(S.arbeitsbereich({**act, "pfad": str(daten) + "\\Skills.\\x\\SKILL.md"}))


class FailClosed(unittest.TestCase):
    def setUp(self):
        A._PERS_CACHE.update(schluessel=None, wert=None)
        self.addCleanup(A._PERS_CACHE.update, schluessel=None, wert=None)

    def test_unlesbare_persoenlichkeiten_sperren_alles(self):
        with mock.patch.object(PS, "active", side_effect=RuntimeError("kaputt")):
            b = A._persoenlichkeits_bereiche()
            self.assertTrue(b is not None and b.gesperrt)
            self.assertEqual("fremd", A._sperrgrund(str(PS.DIR / "x.md"))[1])


class Verknuepfungen(_Tmp):
    def test_hardlink_befehle_gesperrt(self):
        for c in ("mklink /H a.txt b.txt", "cmd /c mklink a b", "New-Item -ItemType HardLink -Path a -Target b"):
            self.assertIsNotNone(A._guard_command(c), c)

    def test_datei_mit_zwei_namen_wird_nicht_geaendert(self):
        datei = self.tmp / "a.txt"
        datei.write_text("x", encoding="utf-8")
        self.assertIsNone(A._guard(str(datei)))
        os.link(datei, self.tmp / "b.txt")
        self.assertIn("Hardlink", A._guard(str(datei)))

    @unittest.skipUnless(os.name == "nt", "Junctions gibt es nur unter Windows")
    def test_ordner_mit_junction_wird_nicht_kopiert(self):
        import _winapi
        quelle, ziel = self.tmp / "quelle", self.tmp / "anderswo"
        quelle.mkdir()
        ziel.mkdir()
        _winapi.CreateJunction(str(ziel), str(quelle / "link"))
        r = A._datei_kopieren({"von": str(quelle), "nach": str(self.tmp / "kopie")})
        self.assertFalse(r.ok)
        self.assertIn("Verknüpfung", r.text)
        self.assertFalse((self.tmp / "kopie").exists())


class Verschleiert(unittest.TestCase):
    def test_abgekuerzte_schalter(self):
        wert = "A" * 32
        for zeichen in ("-", "\u2013", "/"):
            for name in ("ec", "en", "enco", "encodedc"):
                self.assertIsNotNone(A._VERSCHLEIERT.search(f"powershell {zeichen}{name} {wert}"), zeichen + name)
        self.assertIsNone(A._VERSCHLEIERT.search("powershell -ExecutionPolicy Bypass -File x.ps1"))


class TodoStand(_Tmp):
    def test_umwege_zur_todo_liste(self):
        for p in (r"C:\Users\user\app\.nemicli\.\todo.json", r"C:\Users\user\app\.nemicli.\todo.json"):
            self.assertEqual(A._TODO_MELDUNG, A._guard(p), p)
        with mock.patch.object(coding, "arbeitsordner", return_value=self.tmp):
            self.assertEqual(A._TODO_MELDUNG, A._relativ_sperre("cd .nemicli; Set-Content todo.json x"))


class Schluessel(_Tmp):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp)})
        p.start()
        self.addCleanup(p.stop)
        E._GEHEIM.clear()
        E._SCHLUESSEL_STR.clear()
        self.addCleanup(E._GEHEIM.clear)

    def test_befehl_nennt_die_schluesseldatei(self):
        datei = E.schluessel_datei()
        for c in (f'Get-Content "{datei}"', r"Get-Content $env:LOCALAPPDATA\NemiCLI\bereiche.json",
                  r"Get-ChildItem $env:LOCALAPPDATA\Nemi*\*.json", "Get-Content bereiche.json"):
            self.assertIn("Schlüssel", A._guard_command(c, lesend=True) or "", c)

    @unittest.skipUnless(os.name == "nt", "DPAPI gibt es nur unter Windows")
    def test_unlesbare_datei_wird_nie_ersetzt(self):
        datei = E.schluessel_datei()
        datei.parent.mkdir(parents=True)
        datei.write_text("{kaputt", encoding="utf-8")
        self.assertIsNone(E.geheimnis("persona", anlegen=True))
        self.assertEqual("{kaputt", datei.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "DPAPI gibt es nur unter Windows")
    def test_nicht_entschluesselbar_bleibt_none_und_andere_bleiben(self):
        datei = E.schluessel_datei()
        datei.parent.mkdir(parents=True)
        datei.write_text(json.dumps({"v": 1, "schluessel": {"persona": "AAAA"}}), encoding="utf-8")
        self.assertIsNone(E.geheimnis("persona", anlegen=True))
        self.assertIsNotNone(E.geheimnis("personb", anlegen=True))
        register = json.loads(datei.read_text(encoding="utf-8"))["schluessel"]
        self.assertEqual("AAAA", register["persona"])
        self.assertIn("personb", register)
        self.assertEqual([datei.name], [p.name for p in datei.parent.iterdir()])   # keine Temp- oder Sperrdatei


class Schutzsoftware(unittest.TestCase):
    def test_platzhalter_mitten_im_pfad(self):
        with mock.patch.object(schutzsoftware, "ordner", return_value=[Path(r"C:\Program Files\Hersteller")]):
            r = A._guard_command(r"Get-ChildItem 'C:\Progra*\Herst*\x'", lesend=True)
        self.assertIn(schutzsoftware.HINWEIS, r or "")


class Eigenbereich(_Tmp):
    def test_verschieben_von_aussen_zaehlt_zur_grenze(self):
        root = self.tmp / "Eigenes"
        root.mkdir()
        (root / "innen.txt").write_text("x" * 5, encoding="utf-8")
        draussen = self.tmp / "gross.txt"
        draussen.write_text("x" * 20, encoding="utf-8")
        E._GROESSE.clear()
        self.addCleanup(E._GROESSE.clear)
        self.patch(E, "_aktiv", lambda: ("persona", root))
        self.patch(E, "bereit", lambda key, r: (root, b"g" * 32, "k" * 32))
        self.patch(E, "MAX_BYTES", 10)
        fehler, _ = E.vorbereiten({"tool": "verschieben", "von": str(draussen), "nach": str(root / "neu.txt")})
        self.assertIn("voll", fehler or "")
        fehler, _ = E.vorbereiten({"tool": "verschieben", "von": str(root / "innen.txt"),
                                   "nach": str(root / "neu.txt")})
        self.assertIsNone(fehler)


class Sonstiges(_Tmp):
    def test_pdf_vorschau_zeigt_den_geschriebenen_inhalt(self):
        vorschau = A.confirmation_preview({"tool": "pdf_erstellen", "pfad": str(self.tmp / "x.pdf"),
                                           "inhalt": "", "markdown": "ANDERER TEXT"})
        self.assertIn("ANDERER TEXT", vorschau)

    def test_netz_auch_bei_dynamischem_import(self):
        self.assertTrue(A._holt_aus_dem_netz({"tool": "code_ausfuehren", "code": "m = __import__('url' + 'lib')"}))
        self.assertTrue(A._holt_aus_dem_netz({"tool": "code_ausfuehren", "code": "import importlib"}))

    def test_aufwendiges_muster_haelt_nicht_fest(self):
        (self.tmp / "a.txt").write_text("a" * 40 + "b\n", encoding="utf-8")
        self.patch(A, "SUCHE_ZEIT_S", 1.0)
        start = time.monotonic()
        # Absichtlich rückverfolgendes Muster, zur Laufzeit gebaut (sonst meldet es die Code-Prüfung).
        boese = "(" + "|".join(["a", "aa"]) + ")+$"
        A._inhalt_suchen({"pfad": str(self.tmp), "muster": boese})
        self.assertLess(time.monotonic() - start, 15)

    def test_hinweis_nennt_keinen_bestimmten_virenschutz(self):
        with mock.patch.object(A, "_ist_admin", return_value=False), \
                mock.patch.object(coding, "arbeitsordner", return_value=self.tmp), \
                mock.patch.object(A, "_powershell", return_value=NS(returncode=1, stdout="", stderr="")):
            r = A._befehl_roh({"befehl": "Get-Date"})
        self.assertIn("der Virenschutz", r.text)


if __name__ == "__main__":
    unittest.main()
