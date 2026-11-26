"""Startseite im Vollbild: Bild aus Halbblöcken, Karten, Gruß, Einbau in Banner und Start."""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT / "tests"))

import main_quelle   # noqa: E402
import startseite    # noqa: E402
import ui            # noqa: E402
import version       # noqa: E402


def _zeichnen(r, breite: int) -> str:
    c = ui.console
    alt = c.width
    c.width = breite
    try:
        with c.capture() as cap:
            c.print(r)
        return cap.get()
    finally:
        c.width = alt


class Grundlage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.alt = dict(startseite.STAND)
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(self.alt)))

    def test_bild_aus_halbbloecken(self):
        from PIL import Image
        bild = Image.new("RGBA", (2, 4), (0, 0, 0, 0))
        bild.putpixel((0, 0), (255, 0, 0, 255))
        bild.putpixel((0, 1), (0, 0, 255, 255))
        bild.putpixel((1, 1), (0, 255, 0, 255))
        pfad = self.tmp / "b.png"
        bild.save(pfad)
        t = startseite.bild_bloecke(pfad, 2, 2)
        self.assertEqual(t.plain, "▀▄\n  ")
        stile = [str(s.style) for s in t.spans]
        self.assertIn("#ff0000 on #0000ff", stile)            # oben rot, unten blau
        self.assertIn("#00ff00", stile)                       # nur unten: „▄“ in Grün
        self.assertIsNone(startseite.bild_bloecke(self.tmp / "fehlt.png", 4, 2))

    def test_breites_bild_ohne_leere_zeilen(self):
        from PIL import Image
        pfad = self.tmp / "breit.png"
        Image.new("RGB", (80, 20), (10, 20, 30)).save(pfad)
        t = startseite.bild_bloecke(pfad, 12, 5)
        self.assertEqual(len(t.plain.split("\n")), 2)         # 12×3 Bildpunkte → 2 Zeilen statt 5

    def test_echtes_bild_im_eigenen_fenster(self):
        import base64

        from PIL import Image
        pfad = self.tmp / "hoch.png"
        Image.new("RGB", (100, 200), (1, 2, 3)).save(pfad)
        befehle = []
        with patch.dict("os.environ", {"NEMICLI_FENSTER": "1"}), \
                patch.object(startseite, "STEUERN", befehle.append), patch.dict(startseite._GEMELDET, clear=True):
            t = startseite._bild(pfad, 18, 9, 0)
            startseite._bild(pfad, 18, 9, 0)                                 # neu gezeichnet: nicht noch mal melden
        zeilen = t.plain.split("\n")
        self.assertEqual((len(zeilen[0]), len(zeilen)), (9, 9))             # 9×18 Bildpunkte im Verhältnis 1:2
        self.assertEqual([set(z) for z in zeilen], [{chr(0xE000 + i)} for i in range(9)])   # je Zeile ihre Nummer
        self.assertEqual(befehle, ["bild0-9=" + base64.urlsafe_b64encode(str(pfad).encode()).decode()])
        with patch.dict("os.environ", {"NEMICLI_FENSTER": ""}):                # anderes Terminal: Blöcke
            self.assertIn("▀", startseite._bild(pfad, 18, 9, 0).plain)

    def test_wann(self):
        jetzt = datetime(2026, 10, 11, 12, 0)
        self.assertEqual(startseite.wann("2026-10-11 09:05", jetzt), "heute 09:05")
        self.assertEqual(startseite.wann("2026-10-10 18:30", jetzt), "gestern 18:30")
        self.assertEqual(startseite.wann("2026-10-02 07:00", jetzt), "02.10. 07:00")

    def test_gruss_bereinigen(self):
        self.assertEqual(startseite.gruss_bereinigen("<think>hm</think>\n „Hallo du!“ "), "Hallo du!")
        lang = startseite.gruss_bereinigen("wort " * 80)
        self.assertLessEqual(len(lang), startseite.GRUSS_MAX + 2)
        self.assertTrue(lang.endswith("…"))

    def test_gruss_prompt_kennt_letzten_chat(self):
        startseite.STAND["letzter"] = {"titel": "Urlaub am Meer", "wann": "gestern 18:30"}
        self.assertIn("„Urlaub am Meer“ (gestern 18:30)", startseite.gruss_prompt())
        startseite.STAND["letzter"] = None
        self.assertNotIn("letzter Chat", startseite.gruss_prompt())

    def test_wache_ampel(self):
        self.assertTrue(startseite.wache_text(True, {"offen": 0}).startswith("🟢"))
        self.assertIn("3 Alarme offen", startseite.wache_text(True, {"offen": 3}))
        self.assertIn("1 echter Alarm", startseite.wache_text(True, {"echt": 1, "offen": 4}))
        self.assertTrue(startseite.wache_text(False, {}).startswith("⚪"))

    def test_letzter_start(self):
        import paths
        with patch.object(paths, "DATEN", self.tmp):
            self.assertIsNone(startseite.letzter_start())
            self.assertIsNotNone(startseite.letzter_start())
        self.assertTrue((self.tmp / "learned" / "start_zuletzt.txt").exists())

    def test_letzter_chat_ist_das_gespraech_das_weitergeht(self):
        import chatstore
        with patch.object(chatstore, "letzter", lambda: {"id": 7, "title": "Python Fehler", "turns": 3,
                                                          "updated": "2026-10-10 18:30"}), \
                patch.object(chatstore, "faellig", lambda *a, **k: False):
            startseite.sammeln()
        self.assertEqual(startseite.STAND["letzter"]["titel"], "Python Fehler")


class Aufbau(unittest.TestCase):
    def setUp(self):
        self.alt = dict(startseite.STAND)
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(self.alt)))
        startseite.STAND.update(name="Ada", bild=None, status="bereit", gruss="Schön, dass du da bist.",
                                letzter={"titel": "Urlaub am Meer", "wann": "gestern 18:30"},
                                offen=["🎨 2 neue Bilder"], wache="🟢 alles ruhig", liebling=None)

    def test_breit_nebeneinander(self):
        text = _zeichnen(startseite.renderable(ui._banner_kompakt(), 110), 110)
        zeile = next(z for z in text.splitlines() if "Weiter, wo wir waren" in z)
        self.assertIn("┌", zeile.split("Weiter")[0][:30])                # links der Kasten der Persönlichkeit
        for teil in ("Ada", "● bereit", "„Urlaub am Meer“", "geht hier weiter", "📋 Offen", "🛡 Wache",
                     "„Schön, dass du da"):
            self.assertIn(teil, text)
        self.assertNotIn("Lieblingsbild", text)                          # leere Karte fällt weg

    def test_schmal_untereinander(self):
        text = _zeichnen(startseite.renderable(ui._banner_kompakt(), 50), 50)
        zeilen = text.splitlines()
        name = next(i for i, z in enumerate(zeilen) if "Ada" in z)
        weiter = next(i for i, z in enumerate(zeilen) if "Weiter, wo wir waren" in z)
        self.assertLess(name, weiter)

    def test_ohne_karten(self):
        startseite.STAND.update(letzter=None, offen=[], wache=None, gruss="")
        text = _zeichnen(startseite.renderable(ui._banner_kompakt(), 100), 100)
        self.assertIn("Ada", text)
        self.assertNotIn("Offen", text)

    def test_banner_im_vollbild_ist_startseite(self):
        alt_tui, alt_k = ui.TUI, ui.BANNER_KOMPAKT
        self.addCleanup(lambda: (setattr(ui, "TUI", alt_tui), setattr(ui, "BANNER_KOMPAKT", alt_k)))
        ui.BANNER_KOMPAKT = False
        ui.TUI = None
        self.assertNotIn("Weiter, wo wir waren", _zeichnen(ui.banner_renderable(100), 100))
        ui.TUI = type("Alt", (), {})()                                    # alte Vollbild-Fassung: Logo
        self.assertNotIn("Weiter, wo wir waren", _zeichnen(ui.banner_renderable(100), 100))
        ui.TUI = type("Neu", (), {"reflow_auffrischen": lambda self: None})()
        self.assertIn("Weiter, wo wir waren", _zeichnen(ui.banner_renderable(100), 100))
        ui.BANNER_KOMPAKT = True                                          # losgeschrieben: bleibt stehen
        self.assertIn("Weiter, wo wir waren", _zeichnen(ui.banner_renderable(100), 100))
        ui.TUI = type("Alt", (), {})()
        self.assertNotIn("Weiter, wo wir waren", _zeichnen(ui.banner_renderable(100), 100))


class ImVollbild(unittest.TestCase):
    def test_gruss_kommt_nach(self):
        import asyncio

        import screen_tx
        from test_screen_tx import _Ctx, screen_text
        alt = dict(startseite.STAND)
        alt_k = ui.BANNER_KOMPAKT
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(alt),
                                 setattr(ui, "BANNER_KOMPAKT", alt_k)))
        ui.BANNER_KOMPAKT = False
        startseite.STAND.update(name="Ada", bild=None, status="laedt", gruss="", letzter=None, offen=[],
                                wache=None, liebling=None)

        async def go():
            sc = screen_tx.Screen()
            sc.install()
            try:
                ui.banner()

                async def handler(ctx, text):
                    return None
                sc.ctx = _Ctx()
                sc.handler = handler
                sc.session_state = {"model": "x", "tokens": 0}
                app = screen_tx._NemiApp(sc)
                sc.app = app
                async with app.run_test(size=(100, 40)) as pilot:
                    await pilot.pause(0.2)
                    self.assertIn("◌ lädt", screen_text(app))
                    startseite.setzen(status="bereit", gruss="Hallo Welt, schön dich zu sehen",
                                      wache="🟢 alles ruhig")
                    await pilot.pause(0.2)
                    txt = screen_text(app)
                    self.assertIn("Hallo Welt", txt)
                    self.assertIn("● bereit", txt)
                    self.assertIn("alles ruhig", txt)
            finally:
                sc.uninstall()
        asyncio.run(go())


class ImVollbildLage(unittest.TestCase):
    def test_oben_und_symbole_mit_platz(self):
        import asyncio

        import screen_tx
        from test_screen_tx import _Ctx, screen_text
        alt = dict(startseite.STAND)
        alt_k = ui.BANNER_KOMPAKT
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(alt),
                                 setattr(ui, "BANNER_KOMPAKT", alt_k)))
        ui.BANNER_KOMPAKT = False
        startseite.STAND.update(name="Ada", bild=None, status="bereit", gruss="Da bist du ja 😏",
                                letzter={"titel": "hey 😊 🗨 du", "wann": "heute 09:33"},
                                offen=["🛟 Chat-Sicherung fällig"], wache="🟡 2 Alarme offen", liebling=None)

        async def go():
            sc = screen_tx.Screen()
            sc.install()
            try:
                ui.banner()

                async def handler(ctx, text):
                    return None
                sc.ctx = _Ctx()
                sc.handler = handler
                sc.session_state = {"model": "x", "tokens": 0}
                app = screen_tx._NemiApp(sc)
                sc.app = app
                async with app.run_test(size=(100, 50)) as pilot:
                    await pilot.pause(0.2)
                    zeilen = screen_text(app).split("\n")
                    kopf = next(i for i, z in enumerate(zeilen) if "NemiCLI" in z)
                    self.assertLess(kopf, 3)                                    # Startseite steht oben
                    self.assertTrue(any("🛡  Wache" in z for z in zeilen))       # Symbol bekommt seinen Platz
                    self.assertTrue(any("🗨  du" in z for z in zeilen))
                    await pilot.press(*"hallo")
                    await pilot.press("enter")
                    await pilot.pause(0.3)
                    zeilen = screen_text(app).split("\n")
                    self.assertLess(next(i for i, z in enumerate(zeilen) if "NemiCLI " + version.VERSION.split()[0] in z), 3)   # feste Leiste
                    self.assertLess(next(i for i, z in enumerate(zeilen) if "Ada" in z), 3)
                    self.assertTrue(any("Da bist du ja" in z for z in zeilen[:4]))          # Gruß in der Leiste
                    self.assertFalse(any("Weiter, wo wir waren" in z for z in zeilen))   # Karten weg
                    self.assertGreater(next(i for i, z in enumerate(zeilen) if "❯ hallo" in z), 20)   # Chat unten
                    for _ in range(40):                                          # langer Chat: Leiste bleibt
                        ui.console.print("Zeile")
                    await pilot.pause(0.3)
                    self.assertTrue(any("Ada" in z for z in screen_text(app).split("\n")[:3]))
            finally:
                sc.uninstall()
        asyncio.run(go())


class Einbau(unittest.TestCase):
    def test_start_und_wechsel(self):
        src = main_quelle.text()
        self.assertIn("startseite.sammeln(seit=startseite.letzter_start())", src)
        self.assertIn("_start_gruss(ctx)", src)
        self.assertIn("startseite.gruss_prompt()", src)
        self.assertIn("ctx.backend.ask_once(frage, system)", src)
        self.assertIn("if ui.BANNER_KOMPAKT:", src)                     # schon losgeschrieben: kein Gruß
        self.assertIn('config.load().get("start_gruss", True)', src)
        self.assertIn("_startseite_wechsel()", src)


if __name__ == "__main__":
    unittest.main()
