"""Vollbild-Paket: Befehls-Palette (Strg+K, verzeiht Tippfehler), Cockpit (F3), aufklappbare Werkzeug-Chips,
Lichtschweif beim Arbeiten und die Musik-Karte mit Knöpfen."""
import asyncio
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np
from rich.panel import Panel

ROOT = Path(__file__).resolve().parents[1]
for d in ("", "core", "tools", "engines", "ui", "tests"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import cockpit  # noqa: E402
import palette  # noqa: E402
import ui  # noqa: E402


def _song(pfad: Path, sekunden: float = 2.0) -> Path:
    t = np.arange(int(48000 * sekunden)) / 48000
    x = (np.sin(2 * np.pi * 220 * t) * t / sekunden * 20000).astype("<i2")
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(x.tobytes())
    return pfad


class Palette(unittest.TestCase):
    def setUp(self):
        self.liste = palette.eintraege(auswahl=lambda c: {
            "/voice": [("", "Status", None), ("an", "An – jede Antwort hören", None),
                       ("knopf", "Knopf – nur auf Anfrage", None)],
            "/kugel": [("malen {}", "Gesicht malen", "Wie?")]}.get(c, []),
            modelle={"anthropic": "Anthropic"}, personas=[("juna", "Juna")])

    def test_tippfehler_und_umlaute(self):
        self.assertEqual(palette.suchen(self.liste, "musick")[0].zeile, "/musik")
        self.assertEqual(palette.suchen(self.liste, "stime")[0].zeile, "/voice")
        self.assertEqual(palette.suchen(self.liste, "stimme knopf")[0].zeile, "/voice knopf")
        self.assertEqual(palette.suchen(self.liste, "farbe")[0].zeile, "/theme")
        self.assertEqual(palette.suchen(self.liste, "modell")[0].zeile, "/model")
        self.assertEqual(palette.suchen(self.liste, "persoenlichkeit")[0].zeile, "/persönlichkeiten")
        self.assertEqual(palette.suchen(self.liste, "xyzq"), [])

    def test_eintraege_und_eingabe(self):
        malen = next(e for e in self.liste if e.zeile == "/kugel malen …")
        self.assertTrue(malen.braucht_eingabe)
        self.assertEqual(malen.befehl, "/kugel malen ")
        self.assertIn("/model anthropic", [e.zeile for e in self.liste])
        self.assertIn("/persönlichkeiten juna", [e.zeile for e in self.liste])
        self.assertEqual(palette.suchen(self.liste, "")[0].zeile, "/model")      # ohne Frage: das Wichtigste


class Cockpit(unittest.TestCase):
    def test_kurve_und_wellenform(self):
        self.assertEqual(cockpit.funke([0, 1, 2, 4], 4), "▁▃▅█")
        self.assertEqual(len(cockpit.funke(range(100), 10)), 10)
        self.assertEqual(cockpit.funke([], 5), "")
        with tempfile.TemporaryDirectory() as t:
            w = cockpit.wellenform(_song(Path(t) / "a.wav"), 20)
        self.assertEqual(len(w), 20)
        self.assertLess(cockpit.FUNKEN.index(w[0]), cockpit.FUNKEN.index(w[-1]))   # wird lauter
        self.assertEqual(cockpit.wellenform(Path("fehlt.wav")), "")

    def test_render_mit_und_ohne_karte(self):
        from rich.console import Console
        con = Console(width=40, record=True, color_system=None)
        cockpit.TEMPO.clear()
        cockpit.tempo_merken(50)
        cockpit.tempo_merken(None)
        stand = {"model": "ollama:lfm", "ctx": 8000, "ctx_max": 32000, "wache": "🟢 alles ruhig",
                 "plan": {"punkte": [{"text": "Lesen", "status": "erledigt", "zeichen": "🟢"},
                                     {"text": "Bauen", "status": "offen", "zeichen": "🟠"}]},
                 "aufgaben": [{"nr": 2, "titel": "🎵 Song"}], "fortschritt": "Musik: Schritt 3/8"}
        with mock.patch.object(cockpit, "gpu", return_value={"name": "RTX", "last": 40, "belegt": 4 * 2**30,
                                                              "gesamt": 16 * 2**30, "temp": 50}):
            con.print(cockpit.render(stand, "#ff00ff"))
        text = con.export_text()
        for teil in ("VRAM", "4.0/16G", "zuletzt 50", "8.0k/32k", "Todo  1/2", "Bauen", "#2", "3/8", "alles ruhig"):
            self.assertIn(teil, text)
        with mock.patch.object(cockpit, "gpu", return_value=None):
            con.print(cockpit.render({}, "#ff00ff"))
        self.assertIn("keine NVIDIA-Karte", con.export_text())


class Chips(unittest.TestCase):
    def setUp(self):
        ui._anzeige = "rahmen"

    def tearDown(self):
        ui.TUI = None
        ui._anzeige = None

    def test_ohne_vollbild_bleibt_das_panel(self):
        ui.TUI = None
        self.assertIsInstance(ui.action_result("ok", ok=True), Panel)
        self.assertIsInstance(ui.action_request("Datei lesen", confirm=False), Panel)

    def test_im_vollbild_chip_fehler_offen_rueckfrage_gross(self):
        ui.TUI = object()
        k = ui.action_result("Erste Zeile\nzwei\ndrei", ok=True, sekunden=6.2)
        self.assertIsInstance(k, ui.Klapp)
        self.assertFalse(k.offen)
        self.assertIn("Erste Zeile", k.kurz.plain)
        self.assertIn("2 Zeilen mehr", k.kurz.plain)
        self.assertIn("6", k.kurz.plain)
        self.assertTrue(ui.action_result("kaputt", ok=False).offen)
        self.assertIsInstance(ui.action_request("Löschen?", confirm=True), Panel)
        self.assertIsInstance(ui.action_request("Datei lesen", confirm=False), ui.Klapp)


class MusikKarte(unittest.TestCase):
    def test_karte_aus_ergebnis(self):
        with tempfile.TemporaryDirectory() as t:
            song = _song(Path(t) / "Neon Nights_2026-10-18_20-45.wav")
            gedruckt = []
            with mock.patch.object(ui, "console", mock.Mock(print=gedruckt.append)):
                self.assertTrue(ui.musik_karte_aus(f"Song fertig 🎵✅ „Neon Nights“ {song} – 2 s"))
                self.assertFalse(ui.musik_karte_aus(f"Song fertig {Path(t) / 'weg.wav'} – 2 s"))
            self.assertEqual(len(gedruckt), 1)
            from rich.console import Console
            con = Console(width=70, record=True, color_system=None, theme=ui._build_theme(ui.current_theme()))
            con.print(gedruckt[0])
            text = con.export_text()
        self.assertIn("„Neon Nights“", text)
        self.assertIn("0:02", text)
        self.assertIn("Abspielen", text)
        self.assertIn(str(song), ui.MUSIK_KARTEN.values())


class Vollbild(unittest.TestCase):
    def _app(self, handler):
        import screen_tx
        from test_screen_tx import _Ctx
        sc = screen_tx.Screen()
        sc.install()
        self.addCleanup(sc.uninstall)
        sc.ctx = _Ctx()
        sc.handler = handler
        sc.session_state = {"model": "x", "ctx": 100, "ctx_max": 1000}
        app = screen_tx._NemiApp(sc)
        sc.app = app
        return sc, app

    def test_cockpit_palette_chip_und_knoepfe(self):
        import screen_tx
        eingaben = []

        async def handler(ctx, text):
            eingaben.append(text)
            ui.console.print(ui.action_result("Zeile eins\nZeile zwei", ok=True))

        async def go():
            sc, app = self._app(handler)
            with mock.patch.object(screen_tx, "_cockpit_gewuenscht", return_value=False), \
                    mock.patch("config.update"):
                async with app.run_test(size=(120, 30)) as pilot:
                    await pilot.pause(0.2)
                    voll = sc._terminal_width()
                    await pilot.press("f3")
                    await pilot.pause(0.2)
                    self.assertTrue(sc._cockpit.display)
                    self.assertEqual(sc._terminal_width(), voll - screen_tx._COCKPIT_SPALTEN)
                    await pilot.press("f3")
                    self.assertFalse(sc._cockpit.display)

                    await pilot.press("ctrl+k")
                    await pilot.pause(0.2)
                    self.assertEqual(sc._modal, "palette")
                    await pilot.press(*"musick")
                    await pilot.press("enter")
                    await pilot.pause(0.4)
                    self.assertIsNone(sc._modal)
                    self.assertEqual(eingaben, ["/musik"])

                    chip = list(app.query(screen_tx._KlappBlock))[-1]
                    self.assertFalse(chip.klapp.offen)
                    chip.umschalten()
                    self.assertTrue(chip.klapp.offen)
                    self.assertIs(chip.nemi_renderable, chip.klapp.lang)

                    with tempfile.TemporaryDirectory() as t:
                        song = _song(Path(t) / "Lied.wav")
                        ui.musik_karte(song)
                        kennung = next(k for k, v in ui.MUSIK_KARTEN.items() if v == str(song))
                        with mock.patch("os.startfile", create=True) as start:
                            app.action_musik(kennung, "spielen")
                            start.assert_called_once_with(str(song))
                        app.action_musik(kennung, "cover")
                        self.assertIn("Cover von „Lied“", sc._input.text)
        asyncio.run(go())

    def test_lichtschweif_nur_beim_arbeiten(self):
        async def handler(ctx, text):
            return None

        async def go():
            sc, app = self._app(handler)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause(0.2)
                self.assertNotIn("━", sc._composer_header().plain)
                sc.busy = True
                import screen_tx
                with mock.patch.object(screen_tx.time, "monotonic", return_value=1.0):   # Spitze mitten auf der Linie
                    self.assertIn("━", sc._composer_header().plain)
                sc.busy = False
        asyncio.run(go())

    def test_farben_mischen(self):
        import screen_tx
        self.assertEqual(screen_tx._mischen("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(screen_tx._mischen("#ff0000", "#0000ff", 0), "#ff0000")
        self.assertEqual(screen_tx._mischen("rot", "#000000", 0.5), "rot")


class Wissensspeicher(unittest.TestCase):
    def test_eckige_klammern_bleiben_text(self):
        with ui.console.capture() as cap:
            ui.knowledge_menu([(Path("skill.md"), "Liste [/x] und [bold]")], [(Path("a[1].py"), "[/code]")])
        text = cap.get()
        self.assertIn("[/x]", text)
        self.assertIn("[/code]", text)


if __name__ == "__main__":
    unittest.main()
