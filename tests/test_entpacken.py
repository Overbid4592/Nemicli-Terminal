"""Hülle um Unpackt (tools/entpacken.py): SHA-256-Prüfung beim Laden, Virenschutz-Abfrage ohne Hilfsprozess,
Entpacken einer kleinen ZIP, eindeutige Ziele, neue Archive im Profilordner, Modus, Ergebnis-Datei.

python -m unittest tests.test_entpacken
"""

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import entpacken as E
import paths
import profilordner


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.daten = self.tmp / "daten"
        self.daten.mkdir()
        p = [patch.object(paths, "DATEN", self.daten), patch.object(profilordner, "wurzel",
                                                                     lambda: self.daten / "Profile")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _zip(self, pfad: Path, dateien: dict) -> Path:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pfad, "w", zipfile.ZIP_DEFLATED) as z:
            for name, inhalt in dateien.items():
                z.writestr(name, inhalt)
        return pfad


class Laden(_Tmp):
    def test_mitgelieferte_datei_hat_die_feste_sha(self):
        self.assertEqual(hashlib.sha256(E.unpackt_pfad().read_bytes()).hexdigest(), E.UNPACKT_SHA256)

    def test_lizenz_liegt_daneben(self):
        self.assertIn("MIT License", (ROOT / "tools" / "unpackt_LICENSE.txt").read_text(encoding="utf-8"))

    def test_veraenderte_kopie_wird_nicht_geladen(self):
        kopie = self.tmp / "unpackt.py"
        kopie.write_bytes(E.unpackt_pfad().read_bytes() + b"\n# x\n")
        with patch.object(E, "_MODUL", None), patch.object(E, "unpackt_pfad", lambda: kopie):
            with self.assertRaises(RuntimeError) as f:
                E.laden()
        self.assertIn("SHA-256", str(f.exception))

    def test_fehlende_datei(self):
        with patch.object(E, "_MODUL", None), patch.object(E, "unpackt_pfad", lambda: self.tmp / "fehlt.py"):
            with self.assertRaises(RuntimeError):
                E.laden()

    def test_einmal_geladen(self):
        self.assertIs(E.laden(), E.laden())
        self.assertEqual(E.laden().__version__, E.UNPACKT_VERSION)

    def test_archiv_endungen_wie_unpackt(self):
        m = E.laden()
        self.assertEqual(E.ARCHIV_ENDUNGEN, frozenset("." + e for e in m.CATEGORIES["Archive"].split()))


class Virenschutz(_Tmp):
    def test_ersetzt_und_ohne_hilfsprozess(self):
        m = E.laden()
        self.assertIs(m.antivirus_products, E.virenschutz_aktiv)
        E.virenschutz_aktiv.cache_clear()
        with patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
            ergebnis = m.antivirus_products()
        run.assert_not_called()
        popen.assert_not_called()
        self.assertIn(ergebnis, ((), (("Virenschutz", True),)))

    def test_fehler_ergibt_leer(self):
        E.virenschutz_aktiv.cache_clear()
        self.addCleanup(E.virenschutz_aktiv.cache_clear)
        with patch("ctypes.WinDLL", side_effect=OSError("fehlt"), create=True):
            self.assertEqual(E.virenschutz_aktiv(), ())


class Entpacken(_Tmp):
    def test_kleine_zip_in_neues_ziel(self):
        quelle = self._zip(self.tmp / "eingang" / "probe.zip",
                           {"a.txt": "hallo", "bild.png": b"\x89PNG\r\n\x1a\n" + b"0" * 20,
                            "unter/daten.json": json.dumps({"x": 1})})
        ziel = E.ziel_fuer_chat(quelle)
        self.assertFalse(ziel.exists())
        meldungen = []
        with patch("subprocess.run") as run:
            code = E.laden().run(quelle, ziel, log=lambda t, a: meldungen.append((a, t)),
                                 confirm=lambda *a: False)
        self.assertEqual(code, 0, meldungen)
        run.assert_not_called()                      # Unpackt rief bisher PowerShell für den Virenschutz-Status
        self.assertTrue((ziel / "Texte" / "a.txt").is_file())
        self.assertTrue((ziel / "Bilder" / "bild.png").is_file())
        self.assertTrue((ziel / "JSON" / "daten.json").is_file())
        e = E.auswerten(ziel, code)
        self.assertEqual((e["dateien"], e["ordner"], e["fehler"], e["geprueft"]), (3, 3, 0, True))
        self.assertTrue(quelle.is_file())            # Archiv bleibt

    def test_volles_ziel_wird_nicht_angefasst(self):
        quelle = self._zip(self.tmp / "probe.zip", {"a.txt": "x"})
        ziel = self.tmp / "voll"
        ziel.mkdir()
        (ziel / "alt.txt").write_text("bleibt", encoding="utf-8")
        code = E.laden().run(quelle, ziel, log=lambda *a: None, confirm=lambda *a: False)
        self.assertEqual(code, 2)
        self.assertEqual(sorted(p.name for p in ziel.iterdir()), ["alt.txt"])


class Ziele(_Tmp):
    def test_chat_ziel_eindeutig(self):
        q = Path("C:/x/Mein Export (1).tar.gz")
        a, b = E.ziel_fuer_chat(q), E.ziel_fuer_chat(q)
        self.assertNotEqual(a, b)
        self.assertEqual(a.parent, self.daten / "Entpackt")
        self.assertTrue(a.name.startswith("Mein_Export_1_"))
        self.assertRegex(a.name, r"_\d{4}-\d{2}-\d{2}_\d{4}$")

    def test_vorhandener_ordner_wird_ausgelassen(self):
        q = Path("probe.zip")
        a = E.ziel_fuer_profil("Test", q)
        b_erwartet = a.parent / (a.name + "_2")
        b_erwartet.mkdir(parents=True)
        b = E.ziel_fuer_profil("Test", q)
        self.assertNotIn(b, (a, b_erwartet))
        self.assertEqual(a.parent, self.daten / "Profile" / "Test" / "Entpackt")

    def test_stamm_bereinigt(self):
        self.assertEqual(E.stamm(Path("../../äöü?*.zip")), "aeoeue")
        self.assertEqual(E.stamm(Path("CON.zip")), "CON_")
        self.assertEqual(E.stamm(Path("?*.zip")), "Archiv")
        self.assertLessEqual(len(E.stamm(Path("a" * 300 + ".zip"))), 60)

    def test_profilname_ohne_pfadsprung(self):
        z = E.ziel_fuer_profil("../../x", Path("a.zip"))
        self.assertIn(self.daten / "Profile", z.parents)


class NeueZips(_Tmp):
    def test_nur_direkt_und_nur_unbehandelt(self):
        prof = self.daten / "Profile" / "Test"
        a = self._zip(prof / "a.zip", {"x.txt": "1"})
        b = self._zip(prof / "b.7z", {"y.txt": "2"})
        self._zip(prof / "Unter" / "c.zip", {"z.txt": "3"})
        (prof / "notiz.txt").write_text("kein Archiv", encoding="utf-8")
        self.assertEqual(sorted(p.name for p in E.neue_zips("Test")), ["a.zip", "b.7z"])
        E.als_erledigt("Test", a, "abgelehnt")
        self.assertEqual([p.name for p in E.neue_zips("Test")], ["b.7z"])
        E.als_erledigt("Test", b, "entpackt")
        self.assertEqual(E.neue_zips("Test"), [])
        daten = json.loads((prof / "Entpackt" / "_erledigt.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(v["wie"] for v in daten.values()), ["abgelehnt", "entpackt"])

    def test_geaenderte_datei_gilt_als_neu(self):
        prof = self.daten / "Profile" / "Test"
        a = self._zip(prof / "a.zip", {"x.txt": "1"})
        E.als_erledigt("Test", a, "fehler")
        self._zip(a, {"x.txt": "anders und laenger"})
        self.assertEqual([p.name for p in E.neue_zips("Test")], ["a.zip"])

    def test_unbekannte_art(self):
        a = self._zip(self.daten / "Profile" / "Test" / "a.zip", {"x.txt": "1"})
        with self.assertRaises(ValueError):
            E.als_erledigt("Test", a, "vielleicht")

    def test_ohne_profilordner(self):
        self.assertEqual(E.neue_zips("Gibtsnicht"), [])


class Modus(unittest.TestCase):
    def test_lesen_und_setzen(self):
        import config
        stand = {}
        with patch.object(config, "load", lambda: dict(stand)), \
                patch.object(config, "update", lambda **kw: stand.update(kw) or True):
            self.assertEqual(E.modus(), "an")
            E.modus_setzen("auto")
            self.assertEqual(E.modus(), "auto")
            E.modus_setzen("AUS")
            self.assertEqual(stand["zip_modus"], "aus")
            with self.assertRaises(ValueError):
                E.modus_setzen("vielleicht")
            stand["zip_modus"] = "kaputt"
            self.assertEqual(E.modus(), "an")


class Ergebnis(_Tmp):
    def test_schreiben_lesen_nur_bekannte_felder(self):
        pfad = self.tmp / "e.json"
        E.ergebnis_schreiben(pfad, {"code": 0, "dateien": 3, "ordner": 2, "geprueft": True, "fehler": 0,
                                    "ziel": "Z", "eingelesen": 2, "inhalt": "geheim"})
        roh = json.loads(pfad.read_text(encoding="utf-8"))
        self.assertNotIn("inhalt", roh)
        e = E.ergebnis_lesen(pfad)
        self.assertEqual((e["code"], e["dateien"], e["eingelesen"], e["geprueft"]), (0, 3, 2, True))
        for feld in E.ERGEBNIS_FELDER:
            self.assertIn(feld, e)

    def test_fehlende_datei_ist_abbruch(self):
        self.assertEqual(E.ergebnis_lesen(self.tmp / "fehlt.json", "Z")["code"], 3)

    def test_zusammenfassung(self):
        t = E.zusammenfassung({"code": 0, "dateien": 3, "ordner": 2, "geprueft": True, "ziel": "Z"})
        self.assertIn("3 Dateien in 2 Ordnern", t)
        self.assertIn("geprüft", t)


class Fensterprozess(_Tmp):
    def test_befehl_quelltext(self):
        b = E.fenster_befehl(Path("q.zip"), Path("z"), Path("e.json"), "Test", True)
        self.assertTrue(b[1].endswith("main.py"))
        self.assertEqual(b[2:], ["--entpacken", "q.zip", "z", "e.json", "--profil", "Test", "--einlesen"])

    def test_befehl_exe(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(paths, "exe_ohne_fenster", lambda: Path("C:/P/NemiCLI.exe")):
            b = E.fenster_befehl(Path("q.zip"), Path("z"), Path("e.json"))
        self.assertEqual(b, [str(Path("C:/P/NemiCLI.exe")), "--entpacken", "q.zip", "z", "e.json"])

    def test_starten_liest_ergebnis_und_raeumt_auf(self):
        gesehen = {}

        class _Prozess:
            def __init__(self, befehl, **kw):
                gesehen["befehl"] = befehl
                E.ergebnis_schreiben(Path(befehl[-1]), {"code": 1, "dateien": 4, "ziel": befehl[-2]})

            def poll(self):
                return None                    # Fenster bleibt offen: der Aufrufer wartet nur aufs Ergebnis

        with patch("subprocess.Popen", _Prozess):
            e = E.fenster_starten(self.tmp / "q.zip", self.tmp / "z")
        self.assertEqual((e["code"], e["dateien"]), (1, 4))
        self.assertFalse(Path(gesehen["befehl"][-1]).exists())

    def test_ohne_ergebnis_code_3(self):
        class _Prozess:
            def __init__(self, befehl, **kw):
                pass

            def poll(self):
                return 1

        with patch("subprocess.Popen", _Prozess):
            self.assertEqual(E.fenster_starten(self.tmp / "q.zip", self.tmp / "z")["code"], 3)

    def test_main_kennt_den_schalter(self):
        code = (ROOT / "main_start.py").read_text(encoding="utf-8")
        self.assertIn('"--entpacken" in sys.argv', code)


class FensterLogik(unittest.TestCase):
    def test_argumente(self):
        try:
            import entpacken_fenster as F
        except ImportError as e:
            self.skipTest(f"PySide6 fehlt: {e}")
        a = F.argumente(["q.zip", "z", "e.json", "--profil", "Test", "--einlesen"])
        self.assertEqual((a.quelle, a.ziel, a.ergebnis, a.profil, a.einlesen), ("q.zip", "z", "e.json", "Test", True))
        a = F.argumente(["q.zip", "z", "e.json"])
        self.assertEqual((a.profil, a.einlesen), (None, False))

    def test_einlesen_auf_der_gpu(self):
        try:
            import entpacken_fenster as F
        except ImportError as e:
            self.skipTest(f"PySide6 fehlt: {e}")
        import memory
        vorher = memory.NUR_GPU
        self.addCleanup(setattr, memory, "NUR_GPU", vorher)
        festlegen = F.EntpackenFenster._geraet_festlegen
        with patch.object(memory, "hat_gpu", return_value=True), \
                patch.object(memory, "gpu_platz", return_value=(True, "")):
            self.assertEqual(festlegen(), "GPU")
            self.assertTrue(memory.NUR_GPU)
        with patch.object(memory, "hat_gpu", return_value=True), \
                patch.object(memory, "gpu_platz", return_value=(False, "gerade wird gemalt")):
            self.assertEqual(festlegen(), "CPU (gerade wird gemalt)")
            self.assertFalse(memory.NUR_GPU)
        with patch.object(memory, "hat_gpu", return_value=False):
            self.assertEqual(festlegen(), "CPU (keine Grafikkarte)")
            self.assertFalse(memory.NUR_GPU)


if __name__ == "__main__":
    unittest.main()
