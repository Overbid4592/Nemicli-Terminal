"""Tests für den Vollscan der Wache – auf einem Temp-Ordner statt echter Laufwerke."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import datentraeger as DT, systemstand as SY, vollscan as VS   # noqa: E402


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
                   patch.object(DT, "uebersicht", lambda: {
                       "platten": [{"nummer": 0, "modell": "Testplatte", "anschluss": "NVMe", "ssd": True,
                                    "wechselbar": False, "groesse": 2e12, "stil": "GPT", "partitionen": [
                                        {"nummer": 1, "start": 1, "groesse": 2e12, "art": "Basisdaten",
                                         "name": "", "volume": str(self.wurzel)}]}],
                       "volumes": [{"wurzel": str(self.wurzel), "pfade": [str(self.wurzel)], "art": 3,
                                    "name": "Test", "dateisystem": "NTFS", "groesse": 2e12, "frei": 1e12}]}),
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

    def test_alle_laufwerke_nacheinander(self):
        zweite = Path(self._tmp.name) / "zweite"
        zweite.mkdir()
        (zweite / "film.mp4").write_bytes(b"x")
        stand = []
        orig = VS._stand_schreiben
        with patch.object(VS, "_stand_schreiben", lambda z: (stand.append(dict(z)), orig(z))):
            c = VS._verbinden()
            try:
                self.assertEqual(VS.Lauf(c, [str(self.wurzel), str(zweite)]).starten(), 0)
                je = dict(c.execute("SELECT laufwerk, COUNT(*) FROM dateien GROUP BY laufwerk").fetchall())
            finally:
                c.close()
        self.assertEqual(je.get(str(zweite)), 1)
        self.assertEqual(je.get(str(self.wurzel)), 3)            # ohne den gesperrten Ordner
        self.assertIn((2, 2), {(z.get("nr"), z.get("anzahl")) for z in stand if z.get("laufwerk") == str(zweite)})

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

    @unittest.skipUnless(os.name == "nt", "NTFS-Funktionen nur unter Windows")
    def test_genaue_beschreibung_im_bericht(self):
        import ctypes
        import _winapi
        exe = self.wurzel / "Programme" / "Werkzeug" / "werkzeug.exe"
        with open(str(exe) + ":Zone.Identifier", "w", encoding="utf-8") as f:
            f.write("[ZoneTransfer]\nZoneId=3\nHostUrl=https://example.com/werkzeug.exe\n")
        notiz = self.wurzel / "Daten" / "notiz.txt"
        with open(str(notiz) + ":versteck", "wb") as f:
            f.write(b"MZ" + b"\0" * 50)
        if not VS.stroeme(str(notiz)):
            self.skipTest("Temp-Ordner liegt nicht auf NTFS")
        versteckt = self.wurzel / "Daten" / "dienst.exe"
        versteckt.write_bytes(b"MZ")
        ctypes.windll.kernel32.SetFileAttributesW(str(versteckt), 0x2)
        _winapi.CreateJunction(str(self.wurzel / "Programme"), str(self.wurzel / "Abzweig"))
        tief = VS._lang(str(self.wurzel / "Tief" / ("x" * 120) / ("y" * 120)))
        os.makedirs(tief)
        with open(tief + "\\lang.txt", "w") as f:
            f.write("x")
        self.assertEqual(self._lauf(), 0)
        bericht = Path(VS.stand_lesen()["bericht"]).read_text(encoding="utf-8")
        self.assertIn("## Datenträger", bericht)
        self.assertIn("Testplatte", bericht)
        self.assertIn("Partition 1 · Basisdaten", bericht)
        self.assertIn("Internet · https://example.com/werkzeug.exe", bericht)
        self.assertIn("🔴 " + str(notiz) + ":versteck", bericht)
        self.assertIn("Programmcode (MZ)", bericht)
        self.assertIn("dienst.exe", bericht.split("## Versteckte")[1].split("##")[0])
        self.assertIn(str(self.wurzel / "Abzweig") + " → ", bericht)
        self.assertIn("## Nicht lesbar (0)", bericht)
        self.assertIn("Dateiarten: ", bericht)
        c = VS._verbinden()
        pfade = {Path(p).name for (p,) in c.execute("SELECT pfad FROM dateien")}
        s = json.loads(c.execute("SELECT daten FROM volumen").fetchone()[0])
        c.close()
        self.assertIn("lang.txt", pfade)                                 # Pfad über 260 Zeichen
        self.assertNotIn("werkzeug.exe", {p for p in pfade if "Abzweig" in p})
        self.assertEqual(s["verweise"], 1)
        self.assertEqual(s["versteckt"], 1)

    def test_luecke_mit_grund(self):
        orig = os.scandir

        def scandir(p):
            if str(p).endswith("Daten"):
                raise PermissionError(13, "Zugriff verweigert", str(p), 5)
            return orig(p)
        with patch.object(VS.os, "scandir", scandir):
            self.assertEqual(self._lauf(), 0)
        bericht = Path(VS.stand_lesen()["bericht"]).read_text(encoding="utf-8")
        self.assertIn("## Nicht lesbar (1)", bericht)
        self.assertIn(str(self.wurzel / "Daten") + " (Zugriff verweigert)", bericht)

    def test_anzeige_liefert_balken_werte(self):
        z = {"phase": "liste", "start": time.time(), "laufwerk": "C:\\", "bytes": 300e9,
             "belegt": 700e9, "dateien": 1234567}
        self.assertIn("300/700 GB", VS.anzeige(z))
        self.assertIn("1.234.567 Dateien", VS.anzeige(z))
        z = {"phase": "code", "start": time.time(), "code_fertig": 12, "code_gesamt": 400}
        self.assertIn("12/400", VS.anzeige(z))

    def test_fester_stand_ohne_spinner_und_kein_startfoto(self):
        """Gedruckter Stand bewegt sich nicht – also kein Spinner; direkt nach dem Start gar kein Kasten."""
        import ui
        from rich.spinner import Spinner
        kasten = ui.progress_panel("🛡 Vollscan", "Stufe 1 · 0/876 GB", live=False)
        self.assertNotIsInstance(kasten.renderable.renderables[0], Spinner)
        quelle = main_quelle.text()
        teil = quelle[quelle.index("async def _vollscan_befehl"):quelle.index("_VOLLSCAN: dict")]
        self.assertIn("if not gestartet:", teil)
        self.assertIn("live=False", teil)

    def test_kurzanzeige_fuer_statuszeile(self):
        self.assertEqual(VS.kurzanzeige({"phase": "liste", "laufwerk": "C:\\", "bytes": 300e9,
                                         "belegt": 743e9}), "Vollscan C:\\ 300/743 GB")
        self.assertEqual(VS.kurzanzeige({"phase": "liste", "laufwerk": "E:\\", "bytes": 1e9, "belegt": 2e9,
                                         "nr": 3, "anzahl": 4}), "Vollscan E:\\ (Laufwerk 3 von 4) 1/2 GB")
        self.assertIn("Volume b73d7b1c (Laufwerk 4 von 4)",
                      VS.anzeige({"phase": "liste", "start": time.time(), "nr": 4, "anzahl": 4,
                                  "laufwerk": "\\\\?\\Volume{b73d7b1c-6e4d-4e19-9543-ccf170bc596f}\\"}))
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

    @unittest.skipUnless(os.name == "nt", "nur unter Windows")
    def test_datentraeger_uebersicht(self):
        u = DT.uebersicht()
        self.assertTrue(u["platten"])
        self.assertTrue(any(t.get("volume") for p in u["platten"] for t in p["partitionen"]))
        self.assertEqual(DT.groesse_text(2_000_000_000_000), "2,0 TB")
        self.assertEqual(DT.groesse_text(537e9), "537 GB")

    def test_download_herkunft_und_grund(self):
        self.assertEqual(VS.grund(PermissionError(13, "x", "p", 5)), "Zugriff verweigert")
        self.assertEqual(VS.dateiart(".gguf"), "KI-Modelle")
        self.assertEqual(VS.dateiart(".xyz"), "Andere")

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
        self.assertEqual(SY.ampel(e(status="fehlt"))[0], SY.GELB)                         # verwaist
        self.assertEqual(SY.ampel(e(status="fehlt", pfad=r"C:\Users\user\AppData\x.exe"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSupportedFileFormat",
                                    pfad=r"C:\Users\user\Startup\a.lnk"))[0], SY.GELB)     # Ziel nicht lesbar
        self.assertEqual(SY.ampel(e(status="HashMismatch"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSigned", pfad=r"C:\Users\user\AppData\x.exe"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSigned", neu=1))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(status="NotSigned"))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(neu=1))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(pfad=r"C:\Program Files\a.cmd"))[0], SY.GELB)
        self.assertIn(SY.PUNKTE["skript"][1], SY.ampel(e(pfad=r"C:\Windows\a.cmd", status="Skript"))[1])
        # alle Gründe zählen: unsigniert + Nutzerordner + neu – alle drei stehen im Grund
        farbe, grund = SY.ampel(e(status="NotSigned", pfad=r"C:\Users\user\x.exe", neu=1))
        self.assertEqual(SY.ROT, farbe)
        for g in ("unsigniert", "nutzerort", "neu"):
            self.assertIn(SY.PUNKTE[g][1], grund)
        # HashMismatch an einem geschützten Systemort, nicht neu: nicht freigesprochen, aber gelb statt rot
        self.assertEqual(SY.GELB, SY.ampel(e(status="NotTrusted", pfad=r"C:\Windows\System32\x.dll"))[0])
        self.assertEqual(SY.ROT, SY.ampel(e(status="NotTrusted", pfad=r"C:\Windows\System32\x.dll", neu=1))[0])
        self.assertEqual(SY.ROT, SY.ampel(e(status="HashMismatch", pfad=r"C:\Program Files\a.exe"))[0])
        self.assertEqual(SY.ampel(e(pfad=r"C:\Users\user\a.cmd", status="Skript"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(art="hosts", name="x.example", pfad="", detail="93.184.216.34"))[0], SY.ROT)
        self.assertEqual(SY.ampel(e(art="hosts", name="x.example", pfad="", detail="0.0.0.0"))[0], SY.GELB)
        self.assertEqual(SY.ampel(e(art="hosts", name="localhost", pfad="", detail="127.0.0.1"))[0], SY.GRUEN)


def _lnk(ziel: str = "", argumente: str = "", umgebung: str = "", relativ: str = "") -> bytes:
    """Kleinste .lnk nach MS-SHLLINK: Kopf, LinkInfo mit lokalem Pfad, Textfelder, Umgebungsblock."""
    import struct
    flags = 0x80 | (0x2 if ziel else 0) | (0x8 if relativ else 0) | (0x20 if argumente else 0) \
        | (0x200 if umgebung else 0)
    d = struct.pack("<I", 0x4C) + bytes.fromhex("0114020000000000c000000000000046") + struct.pack("<I", flags)
    d += b"\0" * (0x4C - len(d))
    if ziel:
        basis = ziel.encode("latin-1") + b"\0"
        d += struct.pack("<7I", 28 + len(basis) + 1, 28, 1, 0, 28, 0, 28 + len(basis)) + basis + b"\0"
    for text in (relativ, argumente):
        if text:
            d += struct.pack("<H", len(text)) + text.encode("utf-16-le")
    if umgebung:
        block = struct.pack("<II", 0x314, 0xA0000001) + umgebung.encode("latin-1").ljust(260, b"\0") \
            + umgebung.encode("utf-16-le").ljust(520, b"\0")
        d += block
    return d + b"\0\0\0\0"


class VerknuepfungTests(unittest.TestCase):
    """Autostart-Verknüpfungen: geprüft wird ihr Ziel, nicht die .lnk (die hat kein Signaturformat)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def _datei(self, name, daten):
        p = self.d / name
        p.write_bytes(daten)
        return str(p)

    def test_lokales_ziel_und_argumente(self):
        from wache import verknuepfung as V
        v = V.lesen(self._datei("Start Menu.lnk", _lnk(r"C:\Programme\App\app.exe", "--leise")))
        self.assertEqual(r"C:\Programme\App\app.exe", v["ziel"])
        self.assertEqual("--leise", v["argumente"])

    def test_umgebungsblock_und_relativ(self):
        from wache import verknuepfung as V
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            v = V.lesen(self._datei("e.lnk", _lnk(umgebung=r"%SystemRoot%\System32\cmd.exe")))
        self.assertEqual(r"C:\Windows\System32\cmd.exe", v["ziel"])
        v = V.lesen(self._datei("r.lnk", _lnk(relativ=r"..\Tool\t.exe")))
        self.assertEqual(os.path.normpath(str(self.d / ".." / "Tool" / "t.exe")), v["ziel"])

    def test_kaputt_oder_keine_lnk(self):
        from wache import verknuepfung as V
        self.assertIsNone(V.lesen(self._datei("a.lnk", b"kein Link")))
        self.assertIsNone(V.lesen(self._datei("b.lnk", _lnk()[:30])))
        self.assertIsNone(V.lesen(str(self.d / "fehlt.lnk")))

    def test_autostart_ordner_nimmt_das_ziel(self):
        from wache import inventar as I
        ordner = self.d / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        ordner.mkdir(parents=True)
        (ordner / "App.lnk").write_bytes(_lnk(r"C:\Programme\App\app.exe", "--leise"))
        with mock.patch.dict(os.environ, {"APPDATA": str(self.d), "PROGRAMDATA": str(self.d / "leer")}), \
             mock.patch.object(I, "winreg", None, create=True):
            eintraege = [e for e in I.Inventar(mit_aufgaben=False).autostart() if e.name == "App.lnk"]
        self.assertEqual(1, len(eintraege))
        self.assertEqual(str(ordner / "App.lnk"), eintraege[0].detail)             # Kennung bleibt gleich
        self.assertEqual(r"C:\Programme\App\app.exe", eintraege[0].extra["ziel"])
        ev = I.neue_als_ereignisse(eintraege, set())
        self.assertEqual(r"C:\Programme\App\app.exe", ev[0].exe)                     # nicht „…\Start“


class DienstJeAnmeldungTests(unittest.TestCase):
    def test_neue_endung_ist_nicht_neu(self):
        alt = VS.system_schluessel("dienst", "CDPUserSvc_dbf95", r"C:\Windows\system32\svchost.exe")
        self.assertEqual(alt, VS.system_schluessel("dienst", "CDPUserSvc_4a1c7", r"C:\Windows\System32\svchost.exe"))
        self.assertNotEqual(alt, VS.system_schluessel("dienst", "CDPUserSvc_4a1c7", r"C:\Users\user\x.exe"))
        self.assertNotEqual(VS.system_schluessel("autostart", "App_dbf95", ""),
                            VS.system_schluessel("autostart", "App_4a1c7", ""))        # nur bei Diensten



class FundeAlsAlarmeTests(unittest.TestCase):
    """Rote Systemstand-Einträge werden einmal je Fund ein Alarm – nicht bei jedem Lauf neu."""

    def setUp(self):
        from wache import speicher as S
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.sp = S.Speicher(Path(self.tmp.name) / "w.db"); self.addCleanup(self.sp.schliessen)

    def _eintraege(self):
        return [{"art": "autostart", "name": "Fremd", "pfad": "C:/Users/user/fehlt.exe", "detail": "HKCU Run",
                 "status": "fehlt", "signierer": "", "ampel": SY.ROT, "grund": "Programm fehlt …"},
                {"art": "dienst", "name": "CDPUserSvc_dbf95", "pfad": "C:/Windows/system32/svchost.exe",
                 "detail": "", "status": "Valid", "signierer": "Microsoft", "ampel": SY.GELB, "grund": "neu"}]

    def test_einmal_je_fund(self):
        self.assertEqual(1, VS.funde_als_alarme(self.sp, self._eintraege()))
        alarme = self.sp.alarme(status="offen")
        self.assertEqual([VS.VS_REGEL], [a.regel for a in alarme])
        self.assertIn("fehlt.exe", alarme[0].text)
        self.assertEqual(0, VS.funde_als_alarme(self.sp, self._eintraege()))        # nächster Lauf: still
        self.sp.alarm_urteil(alarme[0].id, "harmlos", "Persönlichkeit", "Überbleibsel einer Deinstallation")
        self.assertEqual(0, VS.funde_als_alarme(self.sp, self._eintraege()))        # auch nach dem Urteil


if __name__ == "__main__":
    unittest.main()
