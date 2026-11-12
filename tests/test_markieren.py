"""Markieren im Vollbild: Ziehen über den Rand rollt den Verlauf mit – nach oben und nach unten,
auch wenn die Maus unter den Verlauf (Eingabe, Leisten) gerät. Loslassen kopiert."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from textual import events                        # noqa: E402

import ui                                         # noqa: E402
import screen_tx                                  # noqa: E402
import terminal_fenster as TF                     # noqa: E402


class _Ctx:
    backend = None
    model = "ollama_cloud:x"

    def sync_session(self):
        pass


def _bewegung(x, y, dy):
    return events.MouseMove(None, x, y, 0, dy, 1, False, False, False, screen_x=x, screen_y=y)


class Markieren(unittest.TestCase):
    def lauf(self, pruefung):
        async def go():
            sc = screen_tx.Screen()
            sc.install()
            try:
                for i in range(150):
                    ui.info(f"Zeile {i:03d} mit etwas Text zum Markieren")
                sc.ctx = _Ctx()
                sc.handler = None
                sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
                app = screen_tx._NemiApp(sc)
                sc.app = app
                with mock.patch.object(screen_tx, "_windows_clipboard") as ablage:
                    async with app.run_test(size=(100, 32)) as pilot:
                        await pilot.pause(0.3)
                        await pruefung(app, pilot, ablage)
            finally:
                sc.uninstall() if hasattr(sc, "uninstall") else None
        asyncio.run(go())

    def test_nach_unten_auch_unter_dem_verlauf(self):
        async def pruefung(app, pilot, ablage):
            log = app.query_one("#log")
            app.nemi._follow = False
            log.scroll_home(animate=False)
            await pilot.pause(0.1)
            self.assertEqual(log.scroll_y, 0)
            block = app.query(".block")[3]
            await pilot.mouse_down(block, offset=(4, 0))
            unten = app.query_one("#toolbar").region.y            # unter dem Verlauf
            app.post_message(_bewegung(10, unten, 3))
            await pilot.pause(0.6)
            self.assertGreater(log.scroll_y, 5)                    # rollt von selbst weiter
            await pilot.mouse_up(app.query_one("#toolbar"))
            await pilot.pause(0.1)
            text = app.screen.get_selected_text() or ""
            import re
            nummern = [int(n) for n in re.findall(r"Zeile (\d{3})", text)]
            self.assertIn("ile 003", text)                          # Start ab Spalte 4
            self.assertGreater(len(nummern), 20)
            self.assertEqual(nummern, list(range(nummern[0], nummern[0] + len(nummern))))   # keine fehlt
            self.assertEqual(nummern[0], 4)                         # nichts über dem Start
            self.assertNotIn("bereit", text)                        # Ende bleibt im Verlauf
            ablage.assert_called()                                  # Loslassen kopiert
        self.lauf(pruefung)

    def test_nach_oben(self):
        async def pruefung(app, pilot, ablage):
            log = app.query_one("#log")
            log.scroll_end(animate=False)
            await pilot.pause(0.1)
            start = log.scroll_y
            self.assertGreater(start, 50)
            await pilot.mouse_down(log, offset=(4, 15))
            startzeile = [b for b in app.query(".block") if b.region.y == log.region.y + 15][0]
            start_nr = int(str(startzeile.nemi_renderable).split("Zeile ")[1][:3])
            app.post_message(_bewegung(10, 0, -5))                  # ganz oben (Fensterrand)
            await pilot.pause(0.6)
            self.assertLess(log.scroll_y, start - 10)
            await pilot.mouse_up(log, offset=(4, 0))
            await pilot.pause(0.1)
            import re
            nummern = [int(n) for n in re.findall(r"Zeile (\d{3})", app.screen.get_selected_text() or "")]
            self.assertGreater(len(nummern), 25)                    # mehr als ein Bildschirm
            self.assertEqual(nummern, list(range(nummern[0], nummern[0] + len(nummern))))
            self.assertEqual(nummern[-1], start_nr - 1)              # nichts unter dem Start
            ablage.assert_called()
        self.lauf(pruefung)


class MarkierungSichtbar(unittest.TestCase):
    def test_halbdurchsichtige_farbe_wird_aufgemischt(self):
        # Textual liefert die Markierfarbe mit Alpha; unter dem Segmentstil wäre sie unsichtbar.
        from rich.style import Style
        from textual.color import Color
        from textual.style import Style as TxStyle
        grund = TxStyle(background=Color(12, 13, 20), foreground=Color(228, 231, 242))
        sel = TxStyle(background=Color(1, 120, 212, a=0.5), foreground=Color(0, 0, 0, a=0))
        markieren = screen_tx._markier_stil(sel, grund)
        neu = markieren(Style(color="#e4e7f2", bgcolor="#0c0d14"))
        self.assertNotEqual(tuple(neu.bgcolor.get_truecolor()), (12, 13, 20))
        self.assertEqual(tuple(neu.color.get_truecolor()), (228, 231, 242))
        self.assertIsNotNone(markieren(None).bgcolor)                # ungestylter Text bekommt Grundfarbe + Markierung

    def test_markierung_sitzt_auch_hinter_emojis(self):
        # Die Auswahl zählt Zeichen, gemalt wird in Spalten: hinter 💜 (ein Zeichen, zwei Spalten)
        # rutschte die Farbe sonst je Emoji eine Spalte nach links.
        from rich.segment import Segment
        from textual.strip import Strip
        zeile = "💜 Hallo 😄🌙 du da ✓ äöü"
        strip = Strip([Segment(zeile[:8]), Segment(zeile[8:])])
        for a, b in ((2, 7), (9, 13), (0, len(zeile)), (12, 16)):
            x0, x1 = screen_tx._zellen(strip, a), screen_tx._zellen(strip, b)
            self.assertEqual(zeile[a:b], "".join(s.text for s in strip.crop(x0, x1)), (a, b))
        self.assertEqual(strip.cell_length, screen_tx._zellen(strip, 999))

    def test_bloecke_im_eigenen_behaelter(self):
        async def go():
            sc = screen_tx.Screen()
            sc.install()
            try:
                ui.info("ein Block")
                sc.ctx = _Ctx()
                sc.handler = None
                sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
                app = screen_tx._NemiApp(sc)
                sc.app = app
                async with app.run_test(size=(80, 20)) as pilot:
                    await pilot.pause(0.2)
                    verlauf = app.query_one("#verlauf")
                    self.assertTrue(app.query(".block"))
                    self.assertTrue(all(b.parent is verlauf for b in app.query(".block")))
                    # Die laufende Antwort ändert die Anordnung des Verlaufs nicht.
                    stand = verlauf._layout_updates
                    sc.live_update("läuft …")
                    await pilot.pause(0.1)
                    self.assertEqual(stand, verlauf._layout_updates)
            finally:
                sc.uninstall() if hasattr(sc, "uninstall") else None
        asyncio.run(go())


class MausradZeilen(unittest.TestCase):
    def test_vorgabe_und_config(self):
        import config
        with mock.patch.object(config, "load", return_value={}):
            self.assertEqual(screen_tx._scroll_zeilen(), screen_tx.SCROLL_ZEILEN)
        with mock.patch.object(config, "load", return_value={"scroll_zeilen": 7}):
            self.assertEqual(screen_tx._scroll_zeilen(), 7)
        with mock.patch.object(config, "load", return_value={"scroll_zeilen": 999}):
            self.assertEqual(screen_tx._scroll_zeilen(), 20)
        with mock.patch.object(config, "load", return_value={"scroll_zeilen": "quatsch"}):
            self.assertEqual(screen_tx._scroll_zeilen(), screen_tx.SCROLL_ZEILEN)
        app = screen_tx._NemiApp(screen_tx.Screen())
        self.assertGreater(app.scroll_sensitivity_y, 2)


class FensterGibtZiehenWeiter(unittest.TestCase):
    def test_maus_folgen(self):
        self.assertEqual(TF.maus_folge(0, 4, 0), "\x1b[<0;5;1M")
        self.assertEqual(TF.maus_folge(0, 4, 2, bewegung=True), "\x1b[<32;5;3M")
        self.assertEqual(TF.maus_folge(0, 4, 2, losgelassen=True), "\x1b[<0;5;3m")


if __name__ == "__main__":
    unittest.main()
