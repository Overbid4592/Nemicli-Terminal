"""Offline-Tests: Kugel – Erinnerungen, Musik-Gespür, Desktop-Leben, Ablegen, fertige Bilder.

python -m unittest discover -s tests -p test_kugel_leben.py
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import erinnerungen as ER, klang as KL   # noqa: E402

JETZT = datetime(2026, 10, 15, 14, 0)


class ZeitTests(unittest.TestCase):
    def z(self, **k):
        return datetime.fromtimestamp(ER.zeit_aus(jetzt=JETZT, **k))

    def test_angaben(self):
        self.assertEqual(datetime(2026, 10, 15, 14, 20), self.z(in_minuten=20))
        self.assertEqual(datetime(2026, 10, 15, 14, 30), self.z(um="14:30"))
        self.assertEqual(datetime(2026, 10, 16, 9, 0), self.z(um="9 Uhr"))            # schon vorbei → morgen
        self.assertEqual(datetime(2026, 10, 16, 8, 0), self.z(um="morgen 8:00"))
        self.assertEqual(datetime(2026, 10, 17, 7, 30), self.z(um="übermorgen 7.30"))
        self.assertEqual(datetime(2026, 12, 1, 18, 0), self.z(um="1.12. 18:00"))
        self.assertEqual(datetime(2026, 12, 1, 18, 0), self.z(um="2026-12-01 18:00"))
        self.assertEqual(datetime(2026, 11, 2, 10, 0), self.z(um="2.11.2026 10:00"))

    def test_fehler(self):
        for k in ({}, {"in_minuten": "bald"}, {"in_minuten": 0}, {"in_minuten": 61 * 1440},
                  {"um": "25:00"}, {"um": "irgendwann"}, {"um": "24.12. 18:00"},     # > 60 Tage
                  {"um": "2.10. 10:00"}):                                            # vorbei → nächstes Jahr
            with self.assertRaises(ER.Zeitfehler, msg=k):
                ER.zeit_aus(jetzt=JETZT, **k)


class SpeicherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = patch.object(ER, "ORDNER", Path(self.tmp.name) / "Erinnerungen"); p.start(); self.addCleanup(p.stop)

    def test_anlegen_abholen_loeschen(self):
        jetzt = time.time()
        a = ER.anlegen("Wäsche", jetzt + 600)
        b = ER.anlegen("  Tee   ziehen  ", jetzt + 60)
        self.assertEqual(["Tee ziehen", "Wäsche"], [d["text"] for d in ER.offen()])
        self.assertEqual([], ER.abholen(jetzt))
        self.assertEqual(["Tee ziehen"], [d["text"] for d in ER.abholen(jetzt + 61)])
        self.assertEqual([], ER.abholen(jetzt + 61))                    # nur einmal
        self.assertTrue(ER.loeschen(a["id"][:4]))
        self.assertFalse(ER.loeschen(b["id"]))                           # schon abgeholt
        self.assertEqual([], ER.offen())
        with self.assertRaises(ER.Zeitfehler):
            ER.anlegen("   ", jetzt + 600)

    def test_verspaetet_und_obergrenze(self):
        d = {"text": "Pause", "zeit": time.time() - 3600}
        self.assertIn("(war für", ER.meldung(d))
        self.assertEqual("⏰ Pause", ER.meldung({"text": "Pause", "zeit": time.time()}))
        with patch.object(ER, "OFFEN_MAX", 2):
            ER.anlegen("a", time.time() + 100); ER.anlegen("b", time.time() + 100)
            with self.assertRaises(ER.Zeitfehler):
                ER.anlegen("c", time.time() + 100)

    def test_werkzeug_erinnern(self):
        import actions
        from wache import zugang
        with patch.object(zugang, "laeuft", lambda: {"pid": 1}):
            r = actions._erinnern({"tool": "erinnern", "text": "Wäsche", "in_minuten": 20})
        self.assertIn("Wäsche", str(r)); self.assertNotIn("Achtung", str(r))
        with patch.object(zugang, "laeuft", lambda: None):
            self.assertIn("Achtung", str(actions._erinnern({"text": "Tee", "in_minuten": 30})))
        liste = str(actions._erinnern({}))
        self.assertIn("Wäsche", liste)
        eid = ER.offen()[0]["id"]
        self.assertIn("gelöscht", str(actions._erinnern({"loeschen": eid})))
        self.assertFalse(actions._erinnern({"text": "x", "um": "quatsch"}).ok)
        self.assertIn("erinnern", actions.ACTIONS)
        self.assertIn("in 20 min", actions.describe({"tool": "erinnern", "text": "a", "in_minuten": 20}))
        import modes, plan, sicherheit
        self.assertEqual("harmlos", sicherheit.stufe({"tool": "erinnern", "text": "a", "in_minuten": 5}))
        self.assertIn("erinnern", modes.WACHE_URTEIL_TOOLS)
        self.assertIn("erinnern", plan.BEGLEITEN)

    def test_kugel_schalter_leben_und_musik(self):
        import actions
        import config
        gesetzt = {}
        with patch.object(config, "update", lambda **k: gesetzt.update(k)):
            r = actions._kugel({"tool": "kugel", "leben": "false", "musik": True})
        self.assertEqual({"kugel_leben": False, "kugel_musik": True}, gesetzt)
        self.assertIn("Desktop-Leben aus", str(r)); self.assertIn("Musik-Gespür an", str(r))


class KlangTests(unittest.TestCase):
    def test_takt_und_musik(self):
        g = KL.Musikgespuer()
        t, schlaege = 0.0, 0
        for i in range(30 * 25):                       # 30 s, Schlag alle 0,5 s
            t += 0.04
            pegel = 0.5 if i % 12 == 0 else 0.08
            schlaege += g.fuettern(pegel, t)
        self.assertGreater(schlaege, 30)
        self.assertTrue(g.musik(t))
        for _ in range(8 * 25):                        # 8 s Stille: Stück vorbei
            t += 0.04
            g.fuettern(0.0, t)
        self.assertEqual(0.0, g.laeuft_seit(t))

    def test_stille_ist_keine_musik(self):
        g = KL.Musikgespuer()
        t = 0.0
        for _ in range(60 * 25):
            t += 0.04
            self.assertFalse(g.fuettern(0.005, t))
        self.assertFalse(g.musik(t))

    def test_titel(self):
        self.assertIsNone(KL.titel_aus([("spotify.exe", "Spotify Premium"), ("code.exe", "a - b")]))
        self.assertEqual(("Spotify", "Daft Punk - Veridis Quo"),
                         KL.titel_aus([("Spotify.exe", "Daft Punk - Veridis Quo")]))
        self.assertEqual(("YouTube", "Lofi Mix"),
                         KL.titel_aus([("chrome.exe", "(3) Lofi Mix - YouTube - Google Chrome")]))
        self.assertEqual(("YouTube Music", "Song"), KL.titel_aus([("msedge.exe", "Song - YouTube Music - Edge")]))
        self.assertEqual(("VLC", "lied.mp3"), KL.titel_aus([("vlc.exe", "lied.mp3 - VLC media player")]))
        self.assertIsNone(KL.titel_aus([("vlc.exe", "VLC media player"), ("chrome.exe", "Nachrichten - Google Chrome")]))


class HelferTests(unittest.TestCase):
    def test_kante_und_bilder(self):
        from wache import kugel as K
        schirm = (0, 0, 1919, 1039)
        x, y = K.kante_position((300, 300, 1300, 900), 100, 100, schirm, 0.5)
        self.assertEqual(300 + 2 - 100 + K.RAND, y)
        self.assertTrue(300 <= x + 50 <= 1300 - 200)                 # links der Fenster-Knöpfe
        self.assertIsNone(K.kante_position((300, 10, 1300, 900), 100, 100, schirm, 0.5))    # kein Platz oben
        self.assertIsNone(K.kante_position((300, 300, 500, 900), 100, 100, schirm, 0.5))    # zu schmal
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        bild = Path(tmp.name) / "serie 1.png"; bild.write_bytes(b"x")
        video = Path(tmp.name) / "film.mp4"; video.write_bytes(b"x")
        text = f"Fertig: {bild} und {video} sowie C:\\gibt\\es\\nicht.png"
        self.assertEqual([bild], K.bilder_aus(text))

    def test_hintergrund_ruft_zurueck(self):
        import hintergrund
        kam = []
        hintergrund.BEI_FERTIG.append(kam.append)
        self.addCleanup(lambda: hintergrund.BEI_FERTIG.remove(kam.append))

        async def arbeit():
            return "ok C:\\x.png"

        async def lauf():
            nr = hintergrund.starten("bild_serie", "Serie", arbeit, "Test")
            await hintergrund._AUFGABEN[nr]["task"]
        with patch("ui.success", lambda *a: None), patch("lernschleife.melden", lambda *a: None):
            asyncio.run(lauf())
        self.assertEqual(1, len(kam)); self.assertTrue(kam[0]["ok"]); self.assertEqual("Serie", kam[0]["titel"])


class KugelQtTests(unittest.TestCase):
    """Kugel ohne Bildschirm: Erinnerung, fertige Bilder, Ablegen, Schlafen, Ausflug, Streicheln, Ankündigen."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from wache import kugel as K, oberflaeche as O
        self.K, self.O = K, O
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        for name, wert in (("GRUESSE_DATEI", t / "g.md"), ("LAGE_DATEI", t / "k.json"),
                           ("VERLAUF_DATEI", t / "v.json"), ("ZULETZT_DATEI", t / "z.txt")):
            p = patch.object(K, name, wert); p.start(); self.addCleanup(p.stop)
        p = patch.object(ER, "ORDNER", t / "Erinnerungen"); p.start(); self.addCleanup(p.stop)
        p = patch.object(K, "ANKUENDIGUNG_S", 0.05); p.start(); self.addCleanup(p.stop)
        self.app = O.anwendung()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_forever()),
                         daemon=True).start()
        self.addCleanup(lambda: self.loop.call_soon_threadsafe(self.loop.stop))
        self.lage = {"farbe": "gruen", "zahl": 0, "text": "ruhig"}
        self.gesendet: list = []
        self.gemeldet: list = []

        async def chat(text, bilder):
            self.gesendet.append((text, list(bilder)))
            return "Gesehen.", []

        self.k = K.Kugel(stand=lambda: self.lage, chat=chat, schleife=self.loop, sichtbar=lambda: True, name="Test",
                         nutzer="user", nemicli_oeffnen=lambda: None, screenshot=lambda: None,
                         erinnerungen=True, leben=True, melden=lambda t, x: self.gemeldet.append(x))
        self.k._takt_tick()
        self.addCleanup(self.k._panel_schliessen)
        self.addCleanup(self.k._blase_weg)

    def warten(self, bedingung):
        for _ in range(150):
            self.app.processEvents(); time.sleep(0.02)
            if bedingung():
                return True
        return False

    def bild(self, name="b.png"):
        from PySide6.QtGui import QColor, QPixmap
        pix = QPixmap(40, 30); pix.fill(QColor(200, 50, 50))
        pfad = Path(self.tmp.name) / name
        pix.save(str(pfad))
        return pfad

    def test_erinnerung_an_der_kugel_und_neben_der_uhr(self):
        ER.anlegen("Wäsche aufhängen", time.time() + 40)
        with patch.object(ER, "abholen", lambda: [{"text": "Wäsche aufhängen", "zeit": time.time()}]):
            self.k._erinnerungen_pruefen()
        self.assertTrue(self.k.fenster.beschaeftigt)                   # Regenbogen statt Hüpfen
        self.assertEqual("", self.k.fenster._bewegung)
        self.assertTrue(self.warten(lambda: self.k._blase is not None))
        self.assertIn("Wäsche aufhängen", self.k._blase.label.text())
        self.k._gezeigt = False
        self.k.erinnern("⏰ Tee")
        self.assertEqual(["⏰ Tee"], self.gemeldet)

    def test_gleichzeitige_erinnerungen_in_einer_blase(self):
        jetzt = time.time()
        with patch.object(ER, "abholen", lambda: [{"text": "Erste", "zeit": jetzt}, {"text": "Zweite", "zeit": jetzt}]):
            self.k._erinnerungen_pruefen()
        self.assertTrue(self.warten(lambda: self.k._blase is not None))
        text = self.k._blase.label.text()
        self.assertIn("Erste", text)
        self.assertIn("Zweite", text)

    def test_nur_ein_wache_fund_huepft(self):
        self.k._gruss_pruefen({"farbe": "rot"})
        self.assertEqual("huepfen", self.k.fenster._bewegung)
        self.assertIn("gefunden", self.k._blase.label.text())

    def test_fertiges_bild_in_der_blase_videos_nicht(self):
        b = self.bild()
        self.k.aufgabe_fertig({"titel": "Serie", "ok": True, "ergebnis": f"gespeichert: {b}"})
        self.assertTrue(self.warten(lambda: self.k._blase is not None))
        self.assertIn("🎨 Fertig: Serie", self.k._blase.label.text())
        self.assertTrue(self.k._blase.vorschau.isVisibleTo(self.k._blase))
        self.k._blase_weg()
        self.k.aufgabe_fertig({"titel": "Video", "ok": True, "ergebnis": "C:\\x\\film.mp4"})
        self.assertTrue(self.warten(lambda: self.k._blase is not None))
        self.assertIn("✅ Fertig: Video", self.k._blase.label.text())
        self.assertFalse(self.k._blase.vorschau.isVisibleTo(self.k._blase))

    def test_ablegen_schickt_bild_und_pfad(self):
        b = self.bild()
        doku = Path(self.tmp.name) / "notiz.txt"; doku.write_text("x", encoding="utf-8")
        self.k._abgelegt([b, doku])
        self.assertTrue(self.warten(lambda: bool(self.gesendet) and not self.k._beschaeftigt))
        text, bilder = self.gesendet[0]
        self.assertEqual([b], bilder)
        self.assertIn(str(doku), text)

    def test_schlafen_aufwachen_ausflug_streicheln(self):
        k = self.k
        k._leben_an = True
        with patch.object(self.K, "leerlauf_s", lambda: 900):
            k._leben_pruefen(self.lage)
        self.assertTrue(k.fenster.schlaeft)
        self.assertFalse(k.fenster.grab().isNull())                    # malt „z Z“
        with patch.object(self.K, "leerlauf_s", lambda: 1):
            k._leben_pruefen(self.lage)
        self.assertFalse(k.fenster.schlaeft)
        heim = k.fenster.pos()
        with patch.object(self.K, "vordergrund_fenster", lambda eigene: None):
            k._ausflug_starten()
        self.assertEqual("spaziergang", k._ausflug["art"])
        self.assertTrue(self.warten(lambda: k.fenster.pos() != heim))
        k._heimkehren()
        self.assertIsNone(k._ausflug)
        self.assertTrue(self.warten(lambda: k.fenster.pos() == heim))
        kam = []
        k.fenster.gestreichelt.connect(lambda: kam.append(1))
        from PySide6.QtCore import QPoint
        for i in range(40):
            k.fenster._streicheln(QPoint(100 + (i % 2) * 30, 100))
        self.assertEqual([1], kam)
        self.assertTrue(k.fenster._herzen)
        self.assertFalse(k.fenster.grab().isNull())

    def test_leben_aus_holt_heim(self):
        k = self.k
        with patch.object(self.K, "vordergrund_fenster", lambda eigene: None):
            k._ausflug_starten()
        k._leben_an = False
        k._leben_pruefen(self.lage)
        self.assertIsNone(k._ausflug)
        self.assertFalse(k.fenster.schlaeft)


if __name__ == "__main__":
    unittest.main()
