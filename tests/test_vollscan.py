"""Tests für den Vollscan der Wache – auf einem Temp-Ordner statt echter Laufwerke."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import systemstand as SY, vollscan as VS   # noqa: E402


class Vollscan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)           # läuft als letztes, nach allen Verbindungen
        t = Path(self._tmp.name)
        self.wurzel = t / "platte"
        (self.wurzel / "Programme" / "Werkzeug").mkdir(parents=True)
        (self.wurzel / "Daten").mkdir()
        (self.wurzel / "Sperre" / "innen").mkdir(parents=True)
        (self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe").write_bytes(b"MZ" + b"\0" * 100)
        (self.wurzel / "Programme" / "Werkzeug" / "hilfe.py").write_text("print(1)\n")
        (self.wurzel / "Daten" / "notiz.txt").write_text("text")
        (self.wurzel / "Sperre" / "innen" / "geheim.exe").write_bytes(b"MZ")
        daten = t / "wache"
        daten.mkdir()
        self._p = [patch.object(VS, "DB", daten / "vollscan.db"),
                   patch.object(VS, "STAND", daten / "vollscan.json"),
                   patch.object(VS, "STOPP", daten / "vollscan_stopp"),
                   patch.object(VS, "BERICHTE", t / "Berichte"),
                   patch.object(VS, "ordner_anlegen", lambda: None),
                   patch.object(VS, "ausgelassene_ordner", lambda: [str(self.wurzel / "Sperre")]),
                   patch.object(VS, "windows_stand",
                                lambda: {"build": "26200.1", "version": "25H2",
                                         "updates": ["x | Windows Update installiert: Test (KB1234567)"],
                                         "kbs": ["KB1234567"]}),
                   # Stufe 3 mit festen Einträgen statt echter Dienste, Aufgaben, Treiber
                   patch.object(SY, "SAMMLER", [("Autostart", lambda: [
                       SY._eintrag("autostart", "Werkzeug", str(self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe")),
                       SY._eintrag("autostart", "Weg", str(self.wurzel / "fehlt.exe"))])])]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()

    def _lauf(self) -> int:
        c = VS._verbinden()
        try:
            return VS.Lauf(c, [str(self.wurzel)]).starten()
        finally:
            c.close()

    def test_erster_lauf_erfasst_alles_ausser_gesperrtem_ordner(self):
        self.assertEqual(self._lauf(), 0)
        c = VS._verbinden()
        pfade = {Path(p).name for (p,) in c.execute("SELECT pfad FROM dateien")}
        code = {Path(p).name: s for p, s in c.execute("SELECT pfad, status FROM code")}
        c.close()
        self.assertEqual(pfade, {"werkzeug.exe", "hilfe.py", "notiz.txt"})
        self.assertEqual(set(code), {"werkzeug.exe", "hilfe.py"})
        self.assertEqual(code["hilfe.py"], "Skript")
        z = VS.stand_lesen()
        self.assertEqual(z["phase"], "fertig")
        bericht = Path(z["bericht"]).read_text(encoding="utf-8")
        self.assertIn("Windows 26200.1 · 25H2 · zuletzt KB1234567", bericht)
        self.assertIn("Erster Lauf", bericht)
        self.assertIn("Sperre", bericht)

    def test_zweiter_lauf_zaehlt_neu_geaendert_entfernt(self):
        self._lauf()
        (self.wurzel / "Daten" / "notiz.txt").unlink()
        (self.wurzel / "Daten" / "neu.dll").write_bytes(b"MZ")
        exe = self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe"
        exe.write_bytes(b"MZ" + b"\1" * 300)
        os.utime(exe, (time.time() + 5, time.time() + 5))
        self._lauf()
        c = VS._verbinden()
        neu, geaendert, entfernt = c.execute(
            "SELECT neu, geaendert, entfernt FROM laeufe ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
        self.assertEqual((neu, geaendert, entfernt), (1, 1, 1))

    def test_zeile_fuer_alarme(self):
        self.assertEqual(VS.zeile(str(self.wurzel / "Daten" / "notiz.txt")), "")   # noch kein Lauf
        self._lauf()
        exe = str(self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe")
        self.assertIn("erfasst", VS.zeile(exe))
        self.assertIn("erfasst", VS.zeile(exe.upper()))                  # Groß/klein egal
        neu = self.wurzel / "Daten" / "spaeter.exe"
        neu.write_bytes(b"MZ")
        self.assertIn("seitdem hinzugekommen", VS.zeile(str(neu)))

    def test_stopp_und_fortsetzen(self):
        VS.STOPP.write_text("x")
        self.assertEqual(self._lauf(), 2)
        self.assertEqual(VS.stand_lesen()["phase"], "abgebrochen")
        VS.STOPP.unlink()
        self.assertEqual(self._lauf(), 0)
        c = VS._verbinden()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0], 1)
        c.close()

    def test_baseline_fuer_das_modell(self):
        b = VS.Baseline(VS.DB)
        self.addCleanup(b.schliessen)
        self.assertFalse(b.aktualisieren())                             # noch kein Lauf
        self.assertEqual(b.datei(str(self.wurzel / "Daten" / "notiz.txt")), (0.0, 0.0, 0.0))
        self._lauf()
        self.assertTrue(b.aktualisieren())
        self.assertFalse(b.aktualisieren())                             # Stand unverändert
        self.assertEqual(b.datei(str(self.wurzel / "Daten" / "notiz.txt")), (1.0, 0.0, 0.0))
        self.assertEqual(b.datei(str(self.wurzel / "Daten" / "spaeter.exe")), (0.0, 1.0, 0.0))
        # ausgelassener Ordner und nicht gescanntes Laufwerk: „weiß nicht“, nicht „neu“
        self.assertEqual(b.datei(str(self.wurzel / "Sperre" / "innen" / "geheim.exe")), (0.0, 0.0, 0.0))
        self.assertEqual(b.datei("Q:\\irgendwo\\x.exe"), (0.0, 0.0, 0.0))
        self.assertEqual(b.ordner(str(self.wurzel / "Programme")), 1.0)
        self.assertEqual(b.ordner(str(self.wurzel / "Neu")), 0.0)

    def test_merkmale_nutzen_baseline(self):
        from wache import ereignisse as E, merkmale as M
        self._lauf()
        b = VS.Baseline(VS.DB)
        self.addCleanup(b.schliessen)
        b.aktualisieren()
        exe = str(self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe")
        v = M.ProzessMerkmale(b).vektor(E.Ereignis(E.PROZESS, "process_start", "x",
                                                   prozess="werkzeug.exe", exe=exe))
        self.assertEqual(len(v), len(M.PROZESS_MERKMALE))
        i = M.PROZESS_MERKMALE.index("vollscan_bekannt")
        self.assertEqual(v[i:i + 3], [1.0, 0.0, 0.0])
        neu = str(self.wurzel / "Daten" / "neu.txt")
        v = M.DateiMerkmale(b).vektor(E.Ereignis(E.DATEI, "file_created", "x", datei=neu))
        self.assertEqual(len(v), len(M.DATEI_MERKMALE))
        i = M.DATEI_MERKMALE.index("vollscan_bekannt")
        self.assertEqual(v[i:i + 4], [0.0, 1.0, 0.0, 1.0])            # neu, Ordner bekannt
        v = M.ProzessMerkmale().vektor(E.Ereignis(E.PROZESS, "process_start", "x", prozess="a", exe=exe))
        self.assertEqual(v[-3:], [0.0, 0.0, 0.0])                      # ohne Vollscan: 0

    def test_fortsetzung_zaehlt_vorhandene_dateien(self):
        c = VS._verbinden()
        with patch.object(VS.Lauf, "_fingerabdruecke", lambda self: False):
            VS.Lauf(c, [str(self.wurzel)]).starten()                   # Stufe 2 abgebrochen
        c.close()
        self.assertEqual(self._lauf(), 0)
        z = VS.stand_lesen()
        self.assertTrue(z["fortsetzung"])
        self.assertEqual(z["dateien"], 3)

    def test_hash_vergleich_findet_inhalt_ohne_neues_datum(self):
        from wache import herkunft
        p = patch.object(herkunft, "signatur", lambda pfad, *a, **k: {"status": "NotSigned", "signierer": ""})
        p.start()
        self.addCleanup(p.stop)
        self._lauf()
        exe = self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe"
        alt = exe.stat()
        exe.write_bytes(b"MZ" + b"\7" * 100)                             # gleiche Größe
        os.utime(exe, (alt.st_atime, alt.st_mtime))                     # gleiches Datum
        self._lauf()
        c = VS._verbinden()
        sha, vorher, lauf = c.execute("SELECT sha256, sha_vorher, veraendert_lauf FROM code "
                                      "WHERE pfad=?", (str(exe),)).fetchone()
        c.close()
        self.assertTrue(vorher and vorher != sha)
        self.assertEqual(lauf, 2)
        bericht = Path(VS.stand_lesen()["bericht"]).read_text(encoding="utf-8")
        self.assertIn("Code mit verändertem Inhalt seit dem letzten Lauf (1)", bericht)
        self.assertIn("werkzeug.exe", bericht)

    def test_systemstand_im_bericht(self):
        self._lauf()
        bericht = Path(VS.stand_lesen()["bericht"]).read_text(encoding="utf-8")
        self.assertIn("## Systemstand mit Ampel", bericht)
        self.assertIn("## Laufwerke", bericht)
        self.assertIn("SHA-256 gespeichert für 2 Code-Dateien", bericht)
        self.assertIn("🔴 **Weg**", bericht)                             # Programm fehlt
        c = VS._verbinden()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM system").fetchone()[0], 2)
        c.close()
        self._lauf()                                                    # zweiter Lauf: nichts neu
        c = VS._verbinden()
        self.assertEqual(c.execute("SELECT SUM(neu) FROM system WHERE lauf=2").fetchone()[0], 0)
        c.close()

    def test_anzeige_liefert_balken_werte(self):
        z = {"phase": "liste", "start": time.time(), "laufwerk": "C:\\", "bytes": 300e9,
             "belegt": 700e9, "dateien": 1234567}
        self.assertIn("300/700 GB", VS.anzeige(z))
        self.assertIn("1.234.567 Dateien", VS.anzeige(z))
        z = {"phase": "code", "start": time.time(), "code_fertig": 12, "code_gesamt": 400}
        self.assertIn("12/400", VS.anzeige(z))

    def test_kurzanzeige_fuer_statuszeile(self):
        self.assertEqual(VS.kurzanzeige({"phase": "liste", "laufwerk": "C:\\", "bytes": 300e9,
                                         "belegt": 743e9}), "Vollscan C:\\ 300/743 GB")
        self.assertEqual(VS.kurzanzeige({"phase": "code", "code_fertig": 5, "code_gesamt": 9}),
                         "Vollscan Fingerabdruck 5/9")
        self.assertEqual(VS.kurzanzeige({"phase": "fertig"}), "")


class Hilfen(unittest.TestCase):
    def test_gruppe(self):
        self.assertEqual(VS.gruppe(r"C:\Program Files\Hersteller\bin\x.dll"),
                         str(Path(r"C:\Program Files\Hersteller")))
        self.assertEqual(VS.gruppe(r"C:\Users\user\AppData\Local\App\a\b.exe"),
                         str(Path(r"C:\Users\user\AppData\Local\App")))
        self.assertEqual(VS.gruppe(r"E:\Spiele\x\y.exe"), str(Path(r"E:\Spiele\x")))
        self.assertEqual(VS.gruppe(r"E:\Neu\setup.exe"), str(Path(r"E:\Neu")))
        v = "\\\\?\\Volume{1234}\\"
        self.assertEqual(VS.gruppe(v + r"ventoy\a.efi"), VS.gruppe(v + r"ventoy\b.efi"))

    def test_produktordner(self):
        with tempfile.TemporaryDirectory() as t:
            pf = Path(t) / "Programme"
            (pf / "Hersteller" / "Produkt").mkdir(parents=True)
            ordner = VS._produktordner(str(pf / "Hersteller" / "Produkt" / "p.exe"), [str(pf)])
            self.assertEqual(ordner, {str(pf / "Hersteller")})
        self.assertEqual(VS._produktordner("schema://", []), set())

    def test_volumes_mit_und_ohne_buchstaben(self):
        lesbar, gesperrt = VS.volumes()
        self.assertTrue(lesbar)
        for w in lesbar + gesperrt:
            self.assertRegex(w, r"^([A-Z]:\\|\\\\\?\\Volume\{.+\}\\$|[A-Z]:\\.+\\$)"
                             if os.name == "nt" else "/")

    def test_programmpfad(self):
        self.assertEqual(SY.programmpfad(r'"C:\Program Files\A B\a.exe" --start'), r"C:\Program Files\A B\a.exe")
        self.assertEqual(SY.programmpfad(r"C:\Program Files\A B\a.exe -x"), r"C:\Program Files\A B\a.exe")
        self.assertEqual(SY.programmpfad(r"rundll32.exe C:\Tools\x.dll,Start").lower(), r"c:\tools\x.dll")
        self.assertEqual(SY.programmpfad(r'C:\Windows\System32\cmd.exe /c "C:\Temp\y.bat"'), r"C:\Temp\y.bat")
        self.assertTrue(SY.programmpfad(r"\SystemRoot\system32\drivers\x.sys").lower().endswith(
            r"\system32\drivers\x.sys"))
        self.assertFalse(SY.programmpfad(r"\SystemRoot\x.sys").startswith("\\"))

    def test_ampel(self):
        e = lambda **k: dict({"art": "autostart", "name": "n", "pfad": r"C:\Program Files\a.exe",
                              "detail": "", "status": "Valid", "neu": 0}, **k)
        self.assertEqual(SY.ampel(e())[0], SY.GRUEN)
        self.assertEqual(SY.ampel(e(status="fehlt"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="HashMismatch"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSigned", pfad=r"C:\Users\user\AppData\x.exe"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSigned", neu=1))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSigned"))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(neu=1))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(pfad=r"C:\Program Files\a.cmd"))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(pfad=r"C:\Windows\a.cmd", status="Skript"))[1], SY.AMPEL_REGELN[6][1])
        self.assertEqual(SY.ampel(e(pfad=r"C:\Users\user\a.cmd", status="Skript"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(art="hosts", name="x.example", pfad="", detail="93.184.216.34"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(art="hosts", name="x.example", pfad="", detail="0.0.0.0"))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(art="hosts", name="localhost", pfad="", detail="127.0.0.1"))[0], SY.GRUEN)


if __name__ == "__main__":
    unittest.main()
