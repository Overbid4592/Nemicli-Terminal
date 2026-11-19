"""Wächter-Scan aus dem Rechtsklick-Menü: Auswahl sammeln, Dateien finden, Ampel, Bericht, Menü-Einträge, Fenster.
Nur harmlose Testdateien; Programme und versteckte Programme werden nachgestellt, nicht angelegt."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qt_testhilfe  # noqa: E402
from wache import vollscan as VS  # noqa: E402
from wache import waechterscan as WS  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen


def herkunft(status="NotSigned", signierer="", systemort=False):
    return NS(signatur=lambda p: {"status": status, "signierer": signierer}, ist_systemort=lambda p: systemort,
              SCHUTZ_STATUS="Schutzsoftware")


class _Ordner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.o = Path(tmp.name)

    def datei(self, name, text="hallo", zone=None):
        p = self.o / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if zone is not None and os.name == "nt":
            with open(str(p) + ":Zone.Identifier", "w", encoding="utf-8") as f:
                f.write(f"[ZoneTransfer]\nZoneId={zone}\nHostUrl=https://downloads.example/x\n"
                        "ReferrerUrl=https://seite.example/\n")
        return p


class Sammeln(_Ordner):
    def test_ein_fenster_fuer_alle_markierten(self):
        ergebnisse = []
        faeden = [threading.Thread(target=lambda n=n: ergebnisse.append(
            WS.sammeln(f"C:\\Daten\\datei{n}.txt", ordner=self.o, ruhe_s=0.4))) for n in range(5)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join()
        sammler = [e for e in ergebnisse if e is not None]
        self.assertEqual(len(sammler), 1)
        self.assertEqual(sorted(sammler[0]), sorted(f"C:\\Daten\\datei{n}.txt" for n in range(5)))
        self.assertEqual(list(self.o.glob("*")), [])                     # nichts bleibt liegen

    def test_alte_sperre_blockiert_nicht(self):
        (self.o / WS.SPERRE).write_text("x")
        alt = os.path.getmtime(self.o / WS.SPERRE) - WS.SPERRE_ALT_S - 5
        os.utime(self.o / WS.SPERRE, (alt, alt))
        self.assertEqual(WS.sammeln("C:\\a.txt", ordner=self.o, ruhe_s=0.1), ["C:\\a.txt"])


class Finden(_Ordner):
    def test_ordner_rekursiv_und_schutzsoftware_aussen_vor(self):
        self.datei("a.txt")
        self.datei("unter/b.txt")
        self.datei("schutz/c.txt")
        gefunden = list(WS.dateien([str(self.o)], schutzrevier=lambda p: p.endswith("schutz")))
        namen = sorted(os.path.basename(p) for p, l in gefunden if l is None)
        self.assertEqual(namen, ["a.txt", "b.txt"])
        self.assertTrue(any(l and "Schutzsoftware" in l for _p, l in gefunden))

    def test_einzelne_datei_und_abbruch(self):
        p = self.datei("x.txt")
        self.assertEqual(list(WS.dateien([str(p)], schutzrevier=lambda p: False)), [(str(p), None)])
        self.assertEqual(list(WS.dateien([str(self.o)], abbruch=lambda: True, schutzrevier=lambda p: False)), [])


class Ampel(_Ordner):
    def test_doppelte_endung(self):
        self.assertTrue(WS.doppelte_endung("Rechnung.pdf.exe"))
        self.assertTrue(WS.doppelte_endung("foto.JPG.scr"))
        self.assertFalse(WS.doppelte_endung("setup.exe"))
        self.assertFalse(WS.doppelte_endung("bericht.final.pdf"))

    def test_dokument_unauffaellig(self):
        f = WS.pruefen(str(self.datei("notiz.txt")), herkunft_modul=herkunft())
        self.assertEqual((f.ampel, f.gruende), (WS.GRUEN, []))

    @unittest.skipUnless(os.name == "nt", "Datenströme gibt es nur unter Windows (NTFS)")
    def test_skript_aus_dem_internet_rot(self):
        p = self.datei("hilfe.ps1", "Write-Output 1", zone=3)
        f = WS.pruefen(str(p), herkunft_modul=herkunft())
        self.assertEqual(f.ampel, WS.ROT)
        self.assertIn("Skript ohne gültige Signatur aus dem Internet", f.gruende)
        self.assertTrue(f.sha256)
        self.assertIn("downloads.example", f.herkunft)

    @unittest.skipUnless(os.name == "nt", "Datenströme gibt es nur unter Windows (NTFS)")
    def test_herkunft_auf_der_schutzliste(self):
        p = self.datei("bild.png", zone=3)
        f = WS.pruefen(str(p), herkunft_modul=herkunft(),
                       schutzliste=lambda url: "Phishing" if "seite.example" in url else None)
        self.assertEqual(f.ampel, WS.ROT)
        self.assertIn("Schutzliste (Phishing)", f.gruende[0])

    def test_programm_ohne_signatur_gelb_signatur_kaputt_rot(self):
        p = str(self.datei("werkzeug.dll", "kein echter Code"))
        self.assertEqual(WS.pruefen(p, herkunft_modul=herkunft("NotSigned")).ampel, WS.GELB)
        self.assertEqual(WS.pruefen(p, herkunft_modul=herkunft("Valid", "Hersteller")).ampel, WS.GRUEN)
        kaputt = WS.pruefen(p, herkunft_modul=herkunft("HashMismatch"))
        self.assertEqual(kaputt.ampel, WS.ROT)
        self.assertIn("nach dem Signieren verändert", kaputt.gruende[0])

    def test_versteckter_datenstrom(self):
        p = str(self.datei("brief.txt"))
        with mock.patch.object(VS, "stroeme", return_value=[("Zone.Identifier", 30), ("geheim", 4096)]), \
                mock.patch.object(VS, "_strom_kopf", return_value=b"MZ"):
            f = WS.pruefen(p, herkunft_modul=herkunft())
        self.assertEqual(f.ampel, WS.ROT)
        self.assertIn("Programm versteckt im Datenstrom „geheim“", f.gruende)
        with mock.patch.object(VS, "stroeme", return_value=[("notiz", 12)]), \
                mock.patch.object(VS, "_strom_kopf", return_value=b"ha"):
            self.assertEqual(WS.pruefen(p, herkunft_modul=herkunft()).ampel, WS.GELB)

    def test_vergleich_mit_vollscan(self):
        import sqlite3
        p = str(self.datei("tool.dll", "inhalt"))
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.executescript(VS.SCHEMA)
        c.execute("INSERT INTO dateien (pfad) VALUES (?)", (p,))
        c.execute("INSERT INTO code (pfad, sha256) VALUES (?, ?)", (p, "0" * 64))
        f = WS.pruefen(p, vollscan_db=c, herkunft_modul=herkunft("Valid"))
        self.assertIn("Inhalt seit dem letzten Vollscan verändert", f.gruende)
        self.assertEqual(f.ampel, WS.GELB)


class Bericht(_Ordner):
    def test_lauf_und_bericht(self):
        self.datei("a.txt")
        self.datei("b.dll", "x")
        with mock.patch.object(WS, "_vollscan_verbinden", return_value=None), \
                mock.patch.object(WS, "_schutzliste", return_value=None), \
                mock.patch("wache.herkunft.signatur", return_value={"status": "NotSigned", "signierer": ""}), \
                mock.patch("wache.herkunft.im_schutzrevier", return_value=False):
            erg = WS.laufen([str(self.o)])
        self.assertEqual(erg.zaehlen(), {WS.ROT: 0, WS.GELB: 1, WS.GRUEN: 1})
        pfad = WS.bericht_speichern(erg, self.o / "berichte")
        text = pfad.read_text(encoding="utf-8")
        self.assertIn("🟡", text)
        self.assertIn("b.dll", text)
        self.assertIn("ersetzt den Virenschutz nicht", text)
        self.assertIn(str(pfad), WS.nachricht_fuer(pfad, erg))


class Menue(unittest.TestCase):
    def test_befehle_und_eintraege(self):
        import kontextmenue as K
        b = K.befehle()
        self.assertIn('waechterscan "%1"', b["scan"])
        self.assertIn('waechterscan "%V"', b["scan_hier"])
        orte = " ".join(K._KEY_SCAN + K._KEY_SCAN_HIER)
        for ort in ("\\*\\shell", "\\Directory\\shell", "\\Drive\\shell", "\\Directory\\Background\\shell"):
            self.assertIn(ort, orte)

    def test_eintragen_mit_mehrfachauswahl(self):
        import kontextmenue as K
        werte = {}

        class Schluessel:
            def __init__(self, pfad):
                self.pfad = pfad

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        reg = NS(HKEY_CURRENT_USER=0, REG_SZ=1, CreateKey=lambda h, pfad: Schluessel(pfad),
                 SetValueEx=lambda k, name, _r, _t, wert: werte.setdefault(k.pfad, {}).__setitem__(name, wert))
        with mock.patch.object(K, "_winreg", return_value=reg):
            labels = K.installieren()
        self.assertIn("🛡 Wächter-Scan", labels)
        datei = werte[K._KEY_SCAN[0]]
        self.assertEqual(datei["MultiSelectModel"], "Player")
        self.assertIn("waechterscan", werte[K._KEY_SCAN[0] + "\\command"][""])
        self.assertNotIn("MultiSelectModel", werte[K._KEY_SCAN_HIER[0]])

    def test_zweiter_prozess_oeffnet_kein_fenster(self):
        import kontextmenue as K
        with mock.patch.object(WS, "sammeln", return_value=None):
            self.assertEqual(K.waechterscan("C:\\x.txt"), 0)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import waechterscan_fenster
except ImportError:                                          # PySide6 fehlt
    waechterscan_fenster = None


@unittest.skipIf(waechterscan_fenster is None, "PySide6 fehlt")
class Fenster(_Ordner):
    def test_ergebnis_anzeigen(self):
        waechterscan_fenster.app_vorbereiten("NemiCLI.Test")
        f = waechterscan_fenster.ScanFenster([str(self.o)], starten=False)
        erg = WS.Ergebnis(pfade=[str(self.o)])
        rot = WS.Fund("C:\\x\\rechnung.pdf.exe")
        rot.melden(WS.ROT, "doppelte Endung")
        erg.funde = [rot, WS.Fund("C:\\x\\ok.txt")]
        erg.luecken = [("C:\\gesperrt", "Zugriff verweigert")]
        with mock.patch.object(WS, "bericht_speichern", return_value=self.o / "b.md"):
            f._fertig(erg)
        self.assertIn("🔴 1", f.zusammen.text())
        texte = [f.liste.item(i).text() for i in range(f.liste.count())]
        self.assertTrue(texte[0].startswith("🔴"))
        self.assertTrue(any("Zugriff verweigert" in t for t in texte))
        self.assertTrue(f.fragen_k.isEnabled())
        with mock.patch("kontextmenue.uebergeben", return_value=True) as weiter:
            f.fragen()
        self.assertIn("Wächter-Scan", weiter.call_args[0][0])
        self.assertFalse(f.fragen_k.isEnabled())


if __name__ == "__main__":
    unittest.main()
