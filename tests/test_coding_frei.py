"""Coding-Freigabe „frei“: im Projekt durcharbeiten, nur die Rote Liste fragt; Werkstatt .nemicli; Kreis-Bremse."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui", ""):
    p = str(ROOT / sub) if sub else str(ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions as A      # noqa: E402
import coding            # noqa: E402
import config            # noqa: E402
import modes             # noqa: E402
import sicherheit as S   # noqa: E402

BS = chr(92)             # Backslash, ohne Escapes im Quelltext


class _Projekt(unittest.TestCase):
    freigabe = "frei"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.projekt = self.tmp / "Rechner"
        self.projekt.mkdir()
        cfg = {"coding": {"aktiv": True, "projekt": str(self.projekt)}, coding.FREIGABE_SCHALTER: self.freigabe}
        for p in (mock.patch.object(config, "load", return_value=cfg),
                  mock.patch.object(S, "ausfuehrungsordner", return_value=self.projekt),
                  mock.patch.object(modes, "current", return_value="normal")):
            p.start()
            self.addCleanup(p.stop)

    def befehl(self, cmd: str) -> dict:
        return {"tool": "befehl", "befehl": cmd}


class RoteListe(_Projekt):
    def test_harmlose_befehle_laufen(self):
        venv = f"venv{BS}Scripts{BS}python.exe"
        for cmd in (f"{venv} -c \"from pathlib import Path; print('OK' if 'x' in Path('README.md').read_text() else 'NEIN')\"",
                    f"{venv} -m unittest discover -s .nemicli{BS}tests -t .",
                    f"{venv} .nemicli{BS}probe{BS}pruef_3.py",
                    f"{venv} -m pip install PySide6",
                    "git status", "git add .", "git commit -m test", "Get-ChildItem",
                    "Remove-Item alt.py"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(S.coding_grund(self.befehl(cmd)))

    def test_rote_liste_fragt(self):
        draussen = "C:" + BS + "Windows" + BS + "x.txt"
        for cmd, wort in (("git push origin main", "git"), ("git reset --hard HEAD~1", "git"),
                          ("Remove-Item -Recurse build", "Ordner"),
                          ("Invoke-WebRequest https://example.com -OutFile a.zip", "Netz"),
                          ("pip install requests", "pip"), ("python -m pip install requests", "pip"),
                          ("winget install x", "ganzen Rechner"),
                          (f"python -c \"open(r'{draussen}', 'w').write('x')\"", "außerhalb")):
            with self.subTest(cmd=cmd):
                grund = S.coding_grund(self.befehl(cmd))
                self.assertIsNotNone(grund)
                self.assertIn(wort, grund)

    def test_loeschen_und_zeitplan(self):
        (self.projekt / "ordner").mkdir()
        self.assertIsNone(S.coding_grund({"tool": "loeschen", "pfad": str(self.projekt / "alt.py")}))
        self.assertIn("Ordner", S.coding_grund({"tool": "loeschen", "pfad": str(self.projekt / "ordner")}))
        self.assertIsNone(S.coding_grund({"tool": "zeitplan", "aktion": "anlegen"}))   # nur Vorschlag ins Fenster


class Entscheidung(_Projekt):
    def test_frei_laeuft_ohne_frage(self):
        cmd = f"venv{BS}Scripts{BS}python.exe -c \"print(1)\""
        self.assertEqual("run", modes.decide(self.befehl(cmd), True))
        self.assertEqual("run", modes.decide({"tool": "datei_schreiben", "pfad": str(self.projekt / "a.py")}, True))
        self.assertFalse(S.doppelt_fragen(self.befehl(cmd)))

    def test_rote_liste_fragt_einmal_mit_grund(self):
        act = self.befehl("git push")
        self.assertEqual("ask", modes.decide(act, True))
        self.assertFalse(S.doppelt_fragen(act))
        self.assertIn("Coding frei hält an", S.frage(act))

    def test_ausserhalb_des_projekts_fragt(self):
        draussen = self.tmp / "anderswo.txt"
        self.assertEqual("ask", modes.decide({"tool": "datei_schreiben", "pfad": str(draussen)}, True))

    def test_todo_ohne_pruefung_bleibt_beim_nutzer(self):
        self.assertEqual("run", modes.decide({"tool": "todo", "aktion": "anlegen", "punkte": []}, True))
        with mock.patch.object(coding, "pruefbefehl", return_value=""), \
                mock.patch.object(coding, "pruefseite", return_value=""):
            self.assertEqual("ask", modes.decide({"tool": "todo", "aktion": "pruefen", "nr": 1}, True))

    def test_lesen_modus_bleibt(self):
        with mock.patch.object(modes, "current", return_value="lesen"):
            self.assertEqual("block", modes.decide({"tool": "datei_schreiben", "pfad": str(self.projekt / "a.py")},
                                                   True))


class Vorsicht(_Projekt):
    freigabe = "vorsicht"

    def test_vorsicht_fragt_wie_der_modus(self):
        self.assertFalse(coding.frei())
        self.assertEqual("ask", modes.decide({"tool": "datei_schreiben", "pfad": str(self.projekt / "a.py")}, True))


class Werkstatt(_Projekt):
    def test_werkstatt_und_cache(self):
        a = coding.werkstatt()
        for name in coding.WERKSTATT:
            self.assertTrue((a / name).is_dir(), name)
        self.assertEqual(str(self.projekt / coding.ABLAGE / "cache"), coding.befehl_umgebung()["PYTHONPYCACHEPREFIX"])

    def test_anleitung_nennt_freigabe_und_werkstatt(self):
        text = coding.prompt_hinweis()
        self.assertIn("Freigabe: **frei**", text)
        self.assertIn(".nemicli" + BS + "tests", text)


class AppAnsehen(_Projekt):
    def setUp(self):
        super().setUp()
        (self.projekt / "venv" / "Scripts").mkdir(parents=True)
        (self.projekt / "venv" / "Scripts" / "python.exe").write_bytes(b"")
        (self.projekt / "gui.py").write_text("print('x')", encoding="utf-8")

    def test_foto_wird_gezeigt(self):
        import appfoto
        erg = {"ok": True, "bild": "x.png", "titel": "Rechner", "groesse": (300, 400), "ausgabe": "", "endete": False,
               "code": None}
        with mock.patch.object(appfoto, "aufnehmen", return_value=erg) as auf:
            r = A._app_ansehen({"datei": "gui.py"})
        self.assertTrue(r.ok)
        self.assertTrue(r.bilder and r.bilder[0].endswith(".png"))
        argv, ordner, bild = auf.call_args.args[:3]
        self.assertEqual(str(self.projekt / "venv" / "Scripts" / "python.exe"), argv[0])
        self.assertEqual(self.projekt, ordner)
        self.assertIn(coding.ABLAGE, str(bild))

    def test_absturz_zeigt_die_ausgabe(self):
        import appfoto
        erg = {"ok": False, "bild": None, "titel": "", "groesse": None, "ausgabe": "Traceback: NameError",
               "endete": True, "code": 1}
        with mock.patch.object(appfoto, "aufnehmen", return_value=erg):
            r = A._app_ansehen({"datei": "gui.py"})
        self.assertFalse(r.ok)
        self.assertIn("NameError", r.text)

    def test_nur_skripte_im_projekt(self):
        draussen = self.tmp / "fremd.py"
        draussen.write_text("", encoding="utf-8")
        self.assertFalse(A._app_ansehen({"datei": str(draussen)}).ok)
        self.assertFalse(A._app_ansehen({"datei": "fehlt.py"}).ok)

    def test_frei_ohne_rueckfrage(self):
        self.assertEqual("run", modes.decide({"tool": "app_ansehen", "datei": "gui.py"}, True))

    def test_png_ist_gueltig(self):
        import struct
        import zlib
        import appfoto
        pfad = appfoto.png_schreiben(self.tmp / "t.png", 2, 1, bytes([1, 2, 3, 0, 4, 5, 6, 0]))
        daten = pfad.read_bytes()
        self.assertEqual(b"\x89PNG", daten[:4])
        breite, hoehe = struct.unpack(">II", daten[16:24])
        self.assertEqual((2, 1), (breite, hoehe))
        idat = daten[daten.index(b"IDAT") + 4:daten.index(b"IEND") - 8]
        self.assertEqual(bytes([0, 3, 2, 1, 255, 6, 5, 4, 255]), zlib.decompress(idat))


class Bereiche(unittest.TestCase):
    """Jeder Bereich hat seine Stufe: Chat (Arbeitsmodus), Coding (frei|vorsicht), Bilder (frei|fragen)."""

    def setUp(self):
        self.daten = {}
        for p in (mock.patch.object(config, "load", lambda: dict(self.daten)),
                  mock.patch.object(config, "update", lambda **kw: self.daten.update(kw))):
            p.start()
            self.addCleanup(p.stop)
        modes.set_mode("normal")
        self.addCleanup(modes.set_mode, "normal")

    def test_shift_tab_im_chat_schaltet_den_modus(self):
        self.assertEqual("chat", modes.bereich())
        self.assertEqual("auto", modes.cycle())

    def test_shift_tab_im_coding_schaltet_die_coding_stufe(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.daten["coding"] = {"aktiv": True, "projekt": tmp.name}
        self.assertEqual("coding", modes.bereich())
        self.assertEqual("coding_vorsicht", modes.cycle())
        self.assertEqual("normal", modes.current())                 # der Chat-Modus bleibt, wie er war
        self.assertIn("vorsicht", modes.anzeige())
        self.assertEqual("coding_frei", modes.cycle())
        self.assertIn("🚀 frei", coding.status_text())

    def test_bilder_haben_eine_eigene_stufe(self):
        modes.set_mode("chat")                                       # im Chat fragt sonst jede Aktion
        self.assertEqual("run", modes.decide({"tool": "bild_malen", "prompt": "x"}, False))
        modes.bild_stufe_setzen("fragen")
        self.assertEqual("ask", modes.decide({"tool": "bild_malen", "prompt": "x"}, False))
        modes.set_mode("lesen")
        self.assertEqual("block", modes.decide({"tool": "bild_malen", "prompt": "x"}, False))


class Freigabepfad(unittest.TestCase):
    def test_backslash_im_text_ist_keine_freigabe(self):
        cmd = "python -c \"print('Scripts" + BS * 2 + "python.exe')\""
        self.assertIsNone(A._befehl_orte(cmd)[2])
        echt = "Get-Content " + BS * 2 + "rechner" + BS + "freigabe" + BS + "x.txt"
        self.assertIsNotNone(A._befehl_orte(echt)[2])


class Kreis(unittest.TestCase):
    def test_nur_gleiche_fehler_zaehlen(self):
        import main
        spur = {}
        act = {"tool": "befehl", "befehl": "python test.py"}
        ok = NS(ok=True, text="OK")
        for _ in range(5):
            self.assertFalse(main._im_kreis(spur, act, ok))
        fehler = NS(ok=False, text="AssertionError: 1 != 2")
        self.assertFalse(main._im_kreis(spur, act, fehler))
        self.assertFalse(main._im_kreis(spur, act, fehler))
        self.assertTrue(main._im_kreis(spur, act, fehler))


if __name__ == "__main__":
    unittest.main()
