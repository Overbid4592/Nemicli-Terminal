"""Setup-Datei (installer/aufspielen.py): fester Programmordner, Aktualisieren nur der Programmteile, Rückweg bei
Fehlern, laufende Programme, Desktop-Verknüpfung – alles in Temp-Ordnern."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "installer") not in sys.path:
    sys.path.insert(0, str(ROOT / "installer"))

import aufspielen as A  # noqa: E402


def _paket(pfad: Path, build: str = "7.9.600") -> Path:
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr("NemiCLI.exe", b"MZneu")
        z.writestr("NemiCLIc.exe", b"MZneu-c")
        z.writestr("NemiCLIT2/build.txt", build + "\n")
        z.writestr("NemiCLIT2/neu.pyd", b"x")
        z.writestr("chrome-erweiterung/manifest.json", "{}")
        z.writestr("SHA256SUMS.txt", "abc  NemiCLI.exe\n")
    return pfad


def _alte_installation(ordner: Path) -> None:
    (ordner / "NemiCLIT2").mkdir(parents=True)
    (ordner / "NemiCLIT2" / "build.txt").write_text("7.8.541\n", encoding="utf-8")
    (ordner / "NemiCLIT2" / "alt.pyd").write_bytes(b"alt")
    (ordner / "NemiCLI.exe").write_bytes(b"MZalt")
    (ordner / "venv" / "Lib").mkdir(parents=True)
    (ordner / "venv" / "Lib" / "torch.txt").write_text("torch", encoding="utf-8")
    (ordner / "ModelGGUF" / "Gemma").mkdir(parents=True)
    (ordner / "ModelGGUF" / "Gemma" / "g.gguf").write_bytes(b"g")
    (ordner / "nemicli.config.json").write_text('{"a": 1}', encoding="utf-8")


class Aufspielen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.paket = _paket(self.tmp / "nutzlast.zip")
        self.ziel = self.tmp / ".nemicli"
        merkzettel = mock.patch.dict("os.environ", {"LOCALAPPDATA": str(self.tmp / "AppData")})
        merkzettel.start()                           # nie den echten Merkzettel des Rechners lesen
        self.addCleanup(merkzettel.stop)

    def test_neu_installieren(self):
        rein = A.aufspielen(self.paket, self.ziel)
        self.assertTrue(A.ist_installation(self.ziel))
        self.assertEqual(A.build_von(self.ziel), "7.9.600")
        self.assertIn("NemiCLIT2", rein)
        self.assertFalse((self.ziel / A.NEU).exists() or (self.ziel / A.ALT).exists())

    def test_aktualisieren_laesst_alles_andere_stehen(self):
        _alte_installation(self.ziel)
        A.aufspielen(self.paket, self.ziel)
        self.assertEqual((self.ziel / "NemiCLI.exe").read_bytes(), b"MZneu")
        self.assertFalse((self.ziel / "NemiCLIT2" / "alt.pyd").exists())      # alter Programmteil ganz ersetzt
        self.assertTrue((self.ziel / "NemiCLIT2" / "neu.pyd").exists())
        self.assertEqual((self.ziel / "venv" / "Lib" / "torch.txt").read_text(encoding="utf-8"), "torch")
        self.assertEqual((self.ziel / "ModelGGUF" / "Gemma" / "g.gguf").read_bytes(), b"g")
        self.assertEqual((self.ziel / "nemicli.config.json").read_text(encoding="utf-8"), '{"a": 1}')
        self.assertFalse((self.ziel / A.NEU).exists() or (self.ziel / A.ALT).exists())

    def test_alte_zettelordner_verschwinden_modelle_bleiben(self):
        _alte_installation(self.ziel)                                            # ModelGGUF mit Modell
        (self.ziel / "ModelGGUF" / "LIES-MICH.txt").write_text("alt", encoding="utf-8")
        (self.ziel / "Vision").mkdir()
        (self.ziel / "Vision" / "LIES-MICH.txt").write_text("alt", encoding="utf-8")
        A.aufspielen(self.paket, self.ziel)
        self.assertFalse((self.ziel / "Vision").exists())                        # nur Zettel: weg
        self.assertEqual((self.ziel / "ModelGGUF" / "Gemma" / "g.gguf").read_bytes(), b"g")
        self.assertTrue((self.ziel / "ModelGGUF" / "LIES-MICH.txt").exists())    # mit Modell: alles bleibt

        (self.ziel / "Vision").mkdir()                                           # leer oder fremde Datei
        (self.ziel / "ModelGGUF" / "Gemma" / "g.gguf").unlink()
        (self.ziel / "ModelGGUF" / "Gemma").rmdir()
        (self.ziel / "ModelGGUF" / "notiz.txt").write_text("eigen", encoding="utf-8")
        self.assertEqual(A.alte_zettelordner_entfernen(self.ziel), ["Vision"])
        self.assertTrue((self.ziel / "ModelGGUF" / "notiz.txt").exists())
        (self.ziel / "ModelGGUF" / "notiz.txt").unlink()
        self.assertEqual(A.alte_zettelordner_entfernen(self.ziel), ["ModelGGUF"])
        self.assertEqual(A.alte_zettelordner_entfernen(self.ziel), [])          # nichts mehr da: kein Fehler
        self.assertTrue(A.ist_installation(self.ziel))

    @unittest.skipUnless(sys.platform == "win32", "nur Windows")
    def test_alte_zettelordner_verknuepfung_wird_nicht_verfolgt(self):
        echt = self.tmp / "echt"
        echt.mkdir()
        (echt / "LIES-MICH.txt").write_text("x", encoding="utf-8")
        self.ziel.mkdir()
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.ziel / "ModelGGUF"), str(echt)],
                           capture_output=True)
        if r.returncode != 0:
            self.skipTest("Verbindungspunkt nicht anlegbar")
        self.assertEqual(A.alte_zettelordner_entfernen(self.ziel), [])
        self.assertTrue((echt / "LIES-MICH.txt").exists())

    def test_fehler_beim_tausch_bringt_das_alte_zurueck(self):
        _alte_installation(self.ziel)
        echt = Path.rename
        zaehler = {"n": 0}

        def rename(selbst, ziel):
            if Path(selbst).parent.name == A.NEU:          # beim Einsetzen der neuen Teile
                zaehler["n"] += 1
                if zaehler["n"] == 2:
                    raise PermissionError("gesperrt")
            return echt(selbst, ziel)
        with mock.patch.object(Path, "rename", rename), self.assertRaises(PermissionError):
            A.aufspielen(self.paket, self.ziel)
        self.assertEqual((self.ziel / "NemiCLI.exe").read_bytes(), b"MZalt")
        self.assertEqual(A.build_von(self.ziel), "7.8.541")
        self.assertTrue((self.ziel / "NemiCLIT2" / "alt.pyd").exists())
        self.assertTrue((self.ziel / "venv" / "Lib" / "torch.txt").exists())
        self.assertFalse((self.ziel / A.NEU).exists())

    def test_nicht_entfernbare_reste_brechen_klar_ab(self):
        _alte_installation(self.ziel)
        (self.ziel / A.ALT).mkdir()
        with mock.patch.object(A.shutil, "rmtree", lambda *a, **k: None), self.assertRaises(OSError) as fehler:
            A.aufspielen(self.paket, self.ziel)
        self.assertNotIsInstance(fehler.exception, FileExistsError)
        self.assertEqual((self.ziel / "NemiCLI.exe").read_bytes(), b"MZalt")

    def test_paket_ohne_programm_wird_abgelehnt(self):
        leer = self.tmp / "leer.zip"
        with zipfile.ZipFile(leer, "w") as z:
            z.writestr("liesmich.txt", "x")
        with self.assertRaises(ValueError):
            A.aufspielen(leer, self.ziel)
        self.assertFalse((self.ziel / "liesmich.txt").exists())

    def test_pfade_ausserhalb_des_ziels_werden_abgelehnt(self):
        boese = self.tmp / "boese.zip"
        with zipfile.ZipFile(boese, "w") as z:
            z.writestr("../draussen.txt", "x")
        with self.assertRaises(ValueError):
            A.aufspielen(boese, self.ziel)
        self.assertFalse((self.tmp / "draussen.txt").exists())

    def test_fester_programmordner(self):
        with mock.patch.object(Path, "home", return_value=self.tmp / "Benutzer"):
            self.assertEqual(A.zielordner(), self.tmp / "Benutzer" / ".nemicli")
            self.assertEqual(A.installation(), A.Installation(self.tmp / "Benutzer" / ".nemicli", ""))
        _alte_installation(self.ziel)
        self.assertEqual(A.installation(self.ziel), A.Installation(self.ziel, "7.8.541"))
        leer = self.tmp / "leer" / ".nemicli"
        leer.mkdir(parents=True)
        self.assertEqual(A.installation(leer).build, "")
        self.assertEqual(A.nutzlast_build(self.paket), "7.9.600")

    def test_alter_ordner_wird_weiter_aktualisiert(self):
        heim = self.tmp / "Benutzer"
        with mock.patch.object(Path, "home", return_value=heim):
            _alte_installation(heim / ".ncli2")
            self.assertEqual(A.installation(), A.Installation(heim / ".ncli2", "7.8.541"))   # nur .ncli2
            (heim / ".nemicli").mkdir()
            self.assertEqual(A.installation().ordner, heim / ".ncli2")                       # .nemicli leer
            _alte_installation(heim / ".nemicli")
            self.assertEqual(A.installation().ordner, heim / ".nemicli")                     # .nemicli zuerst

    def test_laeuft_erkennt_programme_aus_dem_ordner(self):
        _alte_installation(self.ziel)
        procs = [NS(info={"exe": str(self.ziel / "NemiCLI.exe"), "name": "NemiCLI.exe", "pid": 7}),
                 NS(info={"exe": str(self.tmp / "anderes.exe"), "name": "anderes.exe", "pid": 8}),
                 NS(info={"exe": None, "name": "System", "pid": 4})]
        with mock.patch("psutil.process_iter", return_value=procs):
            self.assertEqual(A.laeuft(self.ziel), ["NemiCLI.exe (7)"])

    @unittest.skipUnless(sys.platform == "win32", "nur Windows")
    def test_verknuepfung(self):
        exe = Path(sys.executable)
        lnk = A.verknuepfung(exe, self.tmp, "Probe")
        daten = lnk.read_bytes()
        self.assertEqual(daten[:4], b"L\x00\x00\x00")
        self.assertIn(exe.name.lower(), daten.decode("utf-16-le", "ignore").lower() + daten.decode("latin-1").lower())


if __name__ == "__main__":
    unittest.main()
