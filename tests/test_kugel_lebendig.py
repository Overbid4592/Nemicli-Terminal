"""Offline-Tests: lebendigere Kugel und Spiel-Begleiterin (tools/wache/lebendig.py, kugel.py, dienst.py).

python -m unittest discover -s tests -p test_kugel_lebendig.py
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import erinnerungen as ER, lebendig as L   # noqa: E402

STEAM = r"D:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\Cyberpunk2077.exe"


class Erkennen(unittest.TestCase):
    def test_spiel_aus_dem_spieleordner(self):
        self.assertEqual(L.spiel_name(STEAM), "Cyberpunk 2077")
        self.assertEqual(L.spiel_name(r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\x.exe"),
                         "Fortnite")
        self.assertIsNone(L.spiel_name(r"C:\Program Files (x86)\Steam\steam.exe"))          # der Launcher selbst
        self.assertIsNone(L.spiel_name(r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\a.exe"))
        self.assertIsNone(L.spiel_name(r"C:\Windows\explorer.exe"))

    def test_taetigkeit(self):
        self.assertEqual(L.taetigkeit(STEAM, "Cyberpunk 2077", False), ("spiel", "Cyberpunk 2077"))
        self.assertEqual(L.taetigkeit(r"C:\Spiele\x\game.exe", "Mein Spiel", True), ("spiel", "Mein Spiel"))
        self.assertEqual(L.taetigkeit(r"C:\Tools\Code.exe", "main.py", False)[0], "code")
        self.assertEqual(L.taetigkeit(r"C:\Browser\chrome.exe", "Katzen - YouTube", False)[0], "video")
        self.assertIsNone(L.taetigkeit(r"C:\Browser\chrome.exe", "Nachrichten", False))
        self.assertEqual(L.taetigkeit(r"C:\VLC\vlc.exe", "film.mkv", True)[0], "video")   # Vollbild-Video ist kein Spiel

    def test_taetigkeit_erst_nach_einer_weile(self):
        t = L.Taetigkeit()
        t.fuettern(("code", "code.exe"), 100.0)
        self.assertIsNone(t.stabil(120.0))
        self.assertEqual(t.stabil(100.0 + L.TAETIGKEIT_AB_S), ("code", "code.exe"))
        t.fuettern(("spiel", "X"), 150.0)                              # Wechsel: Uhr beginnt neu
        self.assertIsNone(t.stabil(160.0))


class Anfassen(unittest.TestCase):
    def test_wild_hin_und_her_macht_schwindelig(self):
        b, art = L.Beruehrung(), None
        for i in range(14):
            art = art or b.fuettern(100 + (60 if i % 2 else -60), 100, i * 0.05)
        self.assertEqual(art, "schwindelig")

    def test_langsam_streichen_geniesst_sie(self):
        b, art = L.Beruehrung(), None
        for i in range(30):
            art = art or b.fuettern(100 + i * 10, 100 + (i % 3), i * 0.08)
        self.assertEqual(art, "sanft")

    def test_kurz_drueber_ist_nichts_und_pause_dazwischen(self):
        b = L.Beruehrung()
        self.assertIsNone(b.fuettern(1, 1, 0.0))
        for i in range(14):
            b.fuettern(100 + (60 if i % 2 else -60), 100, 10 + i * 0.05)
        zweit = [b.fuettern(100 + (60 if i % 2 else -60), 100, 11 + i * 0.05) for i in range(14)]
        self.assertNotIn("schwindelig", zweit)                         # höchstens alle PAUSE_S Sekunden


class BlickUndRhythmus(unittest.TestCase):
    def test_blick_zur_maus(self):
        self.assertEqual(L.blick((500, 500), (510, 505)), (0.0, 0.0))  # direkt drüber
        self.assertEqual(L.blick((500, 500), (2000, 500)), (0.0, 0.0))  # zu weit weg
        rechts, links = L.blick((500, 500), (800, 500)), L.blick((500, 500), (200, 500))
        self.assertGreater(rechts[0], 0)
        self.assertLess(links[0], 0)
        self.assertLessEqual(abs(rechts[0]), 6.0)

    def test_atem_und_nacht(self):
        self.assertLess(L.atem_faktor(7), L.atem_faktor(12))
        self.assertLess(L.atem_faktor(2), L.atem_faktor(20))
        self.assertTrue(L.nachtzeit(23) and L.nachtzeit(3) and not L.nachtzeit(12))
        self.assertTrue(L.morgens(7) and not L.morgens(11))

    def test_schlafanzug_finden(self):
        self.assertEqual(L.nacht_outfit({"sport": "leggings", "nacht": "soft pajamas"}), "nacht")
        self.assertEqual(L.nacht_outfit({"gemuetlich": "cozy pyjama with stars"}), "gemuetlich")
        self.assertIsNone(L.nacht_outfit({"abend": "black dress"}))


class TasteUndFoto(unittest.TestCase):
    def test_taste_lesen(self):
        self.assertEqual(L.taste_lesen("strg+alt+n"), (0x0002 | 0x0001, ord("N")))
        self.assertEqual(L.taste_lesen("Umschalt+Win+F9"), (0x0004 | 0x0008, 0x78))
        for falsch in ("n", "strg+", "hallo+n", "strg+alt+enter", ""):
            self.assertIsNone(L.taste_lesen(falsch), falsch)

    def test_schwarzes_foto_erkennen(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            schwarz, bunt = Path(d) / "s.png", Path(d) / "b.png"
            Image.new("RGB", (320, 180), (2, 2, 2)).save(schwarz)
            Image.new("RGB", (320, 180), (90, 140, 60)).save(bunt)
            self.assertTrue(L.dunkel(schwarz))
            self.assertFalse(L.dunkel(bunt))

    def test_texte_fuer_die_ki(self):
        self.assertIn("„Cyberpunk 2077“", L.spiel_text("Cyberpunk 2077"))
        self.assertIn("(still)", L.spiel_text("X"))
        self.assertIn("web_suche", L.spiel_kontext("X"))
        self.assertEqual(L.spiel_kontext(None), "")


class AutoUndVorne(unittest.TestCase):
    def test_auto_schaltet_alles_und_kennt_gemischt(self):
        daten = {}
        with patch("config.load", side_effect=lambda: dict(daten)), \
                patch("config.update", side_effect=lambda **kw: daten.update(kw) or True):
            self.assertIsNone(L.auto_stand())                           # Vorgaben sind gemischt
            L.auto_setzen(True)
            self.assertTrue(L.auto_stand())
            self.assertEqual(set(daten), set(L.AUTO_SCHALTER))
            daten["kugel_musik"] = False
            self.assertIsNone(L.auto_stand())
            L.auto_setzen(False)
            self.assertIs(L.auto_stand(), False)
            self.assertTrue(L.vorne_an())                               # Vorgabe: immer vorne
            daten["kugel_vorne"] = False
            self.assertFalse(L.vorne_an())

    def test_menue_kurz_mit_haken(self):
        import commands
        werte = [z for z, _, _ in commands.UNTERMENUES["/kugel"]]
        for weg in ("impuls an", "ankommen aus", "leben an", "musik aus", "zuschauen an", "erinnerungen"):
            self.assertNotIn(weg, werte)
        for da in ("", "malen {}", "motive", "auto an", "auto aus", "vorne an", "vorne aus", "weg", "ordner"):
            self.assertIn(da, werte)
        code = main_quelle.text()
        self.assertIn('if cmd == "/kugel":', code)
        self.assertIn('if sub == "auto":', code)
        self.assertIn('if sub == "vorne":', code)

    def test_ganz_vorne_ohne_fenster_scheitert_leise(self):
        self.assertFalse(L.ganz_vorne(0))


class Sichtbarkeit(unittest.TestCase):
    def dienst(self):
        from wache import dienst
        d = dienst.Dienst.__new__(dienst.Dienst)
        d.kugel_erlaubt, d._ende = True, threading.Event()
        return d

    def test_beim_spielen_auch_wenn_nemicli_offen_ist(self):
        import kontextmenue
        d = self.dienst()
        with patch.object(kontextmenue, "nemicli_laeuft", return_value=True), \
                patch.object(L, "zuschauen_an", return_value=True), \
                patch.object(L, "spiel_im_vordergrund", return_value="Cyberpunk 2077"):
            self.assertTrue(d.kugel_sichtbar())
        with patch.object(kontextmenue, "nemicli_laeuft", return_value=True), \
                patch.object(L, "zuschauen_an", return_value=False):
            self.assertFalse(d.kugel_sichtbar())
        with patch.object(kontextmenue, "nemicli_laeuft", return_value=False):
            self.assertTrue(d.kugel_sichtbar())


class KugelQt(unittest.TestCase):
    """Die Kugel ohne Bildschirm: Reaktionen, Bildwahl, Zuschauen, Tastenkürzel, Spiel-Kontext."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from wache import kugel as K, oberflaeche as O
        self.K, self.O = K, O
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        for name, wert in (("GRUESSE_DATEI", t / "g.md"), ("LAGE_DATEI", t / "k.json"),
                           ("VERLAUF_DATEI", t / "v.json"), ("ZULETZT_DATEI", t / "z.txt"), ("ORDNER", t)):
            p = patch.object(K, name, wert); p.start(); self.addCleanup(p.stop)
        p = patch.object(ER, "ORDNER", t / "Erinnerungen"); p.start(); self.addCleanup(p.stop)
        self.motive = {"": self.bild("grund.png"), "kichert": self.bild("kichert.png"),
                       "spielt_konzentriert": self.bild("spielt.png")}
        for ziel, name, wert in ((K.steuerung, "bilder", lambda name, outfit=True: dict(self.motive)),
                                 (L, "vordergrund", lambda eigene=None: None)):
            p = patch.object(ziel, name, wert); p.start(); self.addCleanup(p.stop)
        self.app = O.anwendung()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_forever()),
                         daemon=True).start()
        self.addCleanup(lambda: self.loop.call_soon_threadsafe(self.loop.stop))
        self.lage = {"farbe": "gruen", "zahl": 0, "text": "ruhig"}
        self.gesendet, self.geschaut = [], []

        async def chat(text, bilder):
            self.gesendet.append((text, list(bilder)))
            return "Gesehen.", []

        async def zuschauen(text, pfad):
            self.geschaut.append((text, pfad, Path(pfad).exists()))
            return "Uii, ein Bosskampf!", [], ""

        self.k = K.Kugel(stand=lambda: self.lage, chat=chat, schleife=self.loop, sichtbar=lambda: True, name="Test",
                         nutzer="user", nemicli_oeffnen=lambda: None, screenshot=lambda: None, leben=True,
                         zuschauen=zuschauen)
        self.k._takt_tick()
        self.addCleanup(self.k._panel_schliessen)
        self.addCleanup(self.k._blase_weg)

    def bild(self, name):
        from PIL import Image
        pfad = Path(self.tmp.name) / name
        Image.new("RGB", (40, 30), (200, 50, 50)).save(pfad)
        return pfad

    def warten(self, bedingung):
        for _ in range(150):
            self.app.processEvents(); time.sleep(0.02)
            if bedingung():
                return True
        return False

    def test_anstupsen_zeigt_kurz_das_kicher_motiv(self):
        self.assertEqual(self.k._gezeigtes_bild, self.motive[""])
        self.k.fenster.gestupst.emit()
        self.assertEqual(self.k._gezeigtes_bild, self.motive["kichert"])
        self.assertEqual(self.k.fenster._bewegung, "huepfen")
        self.k._reaktion = (self.motive["kichert"], time.time() - 1)   # abgelaufen
        self.k._bild_anzeigen()
        self.assertEqual(self.k._gezeigtes_bild, self.motive[""])

    def test_schwindelig_ohne_motiv_nur_geste(self):
        self.k.fenster.beruehrt.emit("schwindelig")
        self.assertEqual(self.k.fenster._bewegung, "wackeln")
        self.assertTrue(self.k.fenster._funken)
        self.assertEqual(self.k._gezeigtes_bild, self.motive[""])     # kein „schielt“ gemalt

    def test_taetigkeit_nur_wenn_die_persoenlichkeit_nichts_gewaehlt_hat(self):
        with patch.object(L, "vordergrund", lambda eigene=None: (STEAM, "Cyberpunk 2077", False)), \
                patch.object(L, "TAETIGKEIT_AB_S", 0.0):
            self.k._takt_tick()
            self.assertEqual(self.k._spiel, "Cyberpunk 2077")
            self.assertEqual(self.k._gezeigtes_bild, self.motive["spielt_konzentriert"])
            self.k._gesteuert["stimmung"] = "froh"                      # sie hat selbst eine Stimmung gewählt
            self.k._bild_anzeigen()
            self.assertEqual(self.k._gezeigtes_bild, self.k._gesteuert["bild_pfad"])

    def test_zuschauen_schickt_foto_und_zeigt_die_antwort(self):
        self.k._spiel = "Cyberpunk 2077"
        self.k._naechstes_zuschauen = 0
        foto = Path(self.tmp.name) / "foto.png"

        def knipsen(_bild, pfad):
            from PIL import Image
            pfad.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (320, 180), (90, 140, 60)).save(pfad)
            return pfad
        with patch.object(L, "zuschauen_an", return_value=True), patch.object(self.K, "leerlauf_s", lambda: 3), \
                patch.object(self.O, "foto_aufnehmen", return_value=object()), \
                patch.object(self.O, "foto_speichern", side_effect=knipsen):
            self.k._spiel_pruefen(self.lage)
            self.assertTrue(self.warten(lambda: self.k._blase is not None))
        text, pfad, da = self.geschaut[0]
        self.assertIn("Cyberpunk 2077", text)
        self.assertTrue(da)
        self.assertFalse(Path(pfad).exists())                          # Foto danach gelöscht
        self.assertIn("Bosskampf", self.k._blase.label.text())
        self.assertFalse(foto.exists())

    def test_kein_zuschauen_in_der_pause_oder_bei_schwarzem_bild(self):
        self.k._spiel, self.k._naechstes_zuschauen = "X", 0
        with patch.object(L, "zuschauen_an", return_value=True), patch.object(self.K, "leerlauf_s", lambda: 600):
            self.k._spiel_pruefen(self.lage)                            # weg vom Rechner
        self.k._naechstes_zuschauen = 0

        def schwarz(_bild, pfad):
            from PIL import Image
            pfad.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (320, 180), (0, 0, 0)).save(pfad)
            return pfad
        with patch.object(L, "zuschauen_an", return_value=True), patch.object(self.K, "leerlauf_s", lambda: 3), \
                patch.object(self.O, "foto_aufnehmen", return_value=object()), \
                patch.object(self.O, "foto_speichern", side_effect=schwarz):
            self.k._spiel_pruefen(self.lage)
            self.assertTrue(self.k._beschaeftigt)                       # während des Packens keine zweite Runde
            self.assertTrue(self.warten(lambda: not self.k._beschaeftigt))
        self.assertEqual(self.geschaut, [])

    def test_frage_beim_spielen_traegt_den_spiel_kontext(self):
        self.k._spiel = "Cyberpunk 2077"
        self.k._frage_taste()
        self.assertIsNotNone(self.k._panel)
        self.assertTrue(self.k._taste_offen)
        self.k.senden("Wo finde ich eine gute Schrotflinte?")
        self.assertTrue(self.warten(lambda: bool(self.gesendet)))
        self.assertTrue(self.gesendet[0][0].startswith("[Gerade läuft das Spiel „Cyberpunk 2077“."))
        self.assertTrue(self.warten(lambda: self.k.fenster._bewegung == "nicken"))   # Antwort da: sie nickt
        self.k._frage_taste()                                           # zweiter Druck schließt
        self.assertIsNone(self.k._panel)
        self.assertFalse(self.k._taste_offen)


if __name__ == "__main__":
    unittest.main()
