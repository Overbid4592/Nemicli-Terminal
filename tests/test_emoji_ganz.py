"""Emoji im eigenen NemiCLI-Fenster vollständig (bunt, Hautton, Verbinder) – Anzeige, Breite und Markieren.
Andere Terminals behalten die vereinfachte Form."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from rich.cells import cell_len                   # noqa: E402
from rich.console import Console                  # noqa: E402
from rich.text import Text                        # noqa: E402
from textual.geometry import Offset               # noqa: E402
from textual.selection import Selection           # noqa: E402
import screen                                     # noqa: E402
import screen_tx                                  # noqa: E402

HERZ = "❤️"
ENTWICKLERIN = "\U0001f469‍\U0001f4bb"
DAUMEN = "\U0001f44d\U0001f3fb"
EINS = "1️⃣"
FENSTER = {"NEMICLI_FENSTER": "1"}
FREMD = {"NEMICLI_FENSTER": ""}


class Anzeige(unittest.TestCase):
    def test_im_fenster_bleiben_emoji_ganz(self):
        with mock.patch.dict(os.environ, FENSTER):
            for e in (HERZ, ENTWICKLERIN, DAUMEN, EINS):
                self.assertEqual(screen_tx.terminal_safe(f"a {e} b"), f"a {e} b")

    def test_fremdes_terminal_wie_bisher(self):
        with mock.patch.dict(os.environ, FREMD):
            self.assertEqual(screen_tx.terminal_safe(HERZ), "❤")
            self.assertEqual(screen_tx.terminal_safe(ENTWICKLERIN), "\U0001f469")

    def test_steuerzeichen_fliegen_auch_im_fenster(self):
        with mock.patch.dict(os.environ, FENSTER):
            self.assertEqual(screen_tx.terminal_safe(f"a\x1b]52;c;eA==\x07{HERZ}"), f"a{HERZ}")

    def test_nacktes_herz_wird_bunt(self):
        with mock.patch.dict(os.environ, FENSTER):
            self.assertEqual(screen_tx.ueberhang("Klar ❤❤ x"), f"Klar {HERZ}{HERZ} x")
            self.assertEqual(screen_tx.ueberhang(f"schon {HERZ}"), f"schon {HERZ}")       # kein zweites FE0F
            self.assertEqual(screen_tx.ueberhang("\U0001f60a"), "\U0001f60a")              # echtes Emoji: unverändert
        with mock.patch.dict(os.environ, FREMD):
            self.assertEqual(screen_tx.ueberhang("❤ x"), "❤  x")                 # bisher: Lücke

    def test_fe0f_bleibt_im_abschnitt_seines_zeichens(self):
        """Getrennte Abschnitte würden ❤ (1 Zelle) und U+FE0F (0) einzeln zählen – zusammen sind es 2."""
        t = Text()
        t.append("❤", style="red")
        t.append(" rot", style="blue")
        with mock.patch.dict(os.environ, FENSTER):
            r = screen_tx.sanitize(t)
        self.assertEqual(r.plain, f"{HERZ} rot")
        segs = [s for s in r.render(Console(width=40)) if s.text]
        self.assertEqual(segs[0].text, HERZ)
        self.assertEqual(sum(cell_len(s.text) for s in segs), cell_len(r.plain))

    def test_breiten_stimmen_ueberein(self):
        import textual._cells as TC
        for e in (HERZ, ENTWICKLERIN, DAUMEN, EINS):
            self.assertEqual(cell_len(e), 2, ascii(e))
            self.assertEqual(TC.cell_len(e), 2, ascii(e))


class Markieren(unittest.TestCase):
    def test_gruppen_eigene_stuecke(self):
        self.assertEqual(screen_tx._graphem_stuecke("abc"), ["abc"])
        self.assertEqual(screen_tx._graphem_stuecke("ä \U0001f60a"), ["ä \U0001f60a"])
        self.assertEqual(screen_tx._graphem_stuecke(f"Hi {HERZ} du {ENTWICKLERIN}!"),
                         ["Hi ", HERZ, " du ", ENTWICKLERIN, "!"])

    def test_maus_spalte_trifft_das_richtige_zeichen(self):
        """Wie Textual (_compositor.get_widget_and_offset_at) aus Spalte und Offset-Stücken das Zeichen findet."""
        from rich.cells import get_character_cell_size
        zeile = f"{HERZ}{ENTWICKLERIN}ab"

        def offset_bei(x: int) -> int:
            start = 0
            pos = 0
            for stueck in screen_tx._graphem_stuecke(zeile):
                ende = start + cell_len(stueck)
                if start <= x < ende:
                    breite, n = 0, 0
                    for ch in stueck:
                        if breite >= x - start:
                            break
                        breite += get_character_cell_size(ch)
                        n += 1
                    return pos + n
                start, pos = ende, pos + len(stueck)
            return pos

        self.assertEqual(zeile[offset_bei(4)], "a")          # Spalten: ❤️=0–1, 👩‍💻=2–3, a=4, b=5
        self.assertEqual(zeile[offset_bei(5)], "b")

    def test_auswahl_nimmt_ganze_gruppen(self):
        text = f"x{ENTWICKLERIN}y"
        mitten = Selection(Offset(2, 0), Offset(3, 0))            # mitten in 👩‍💻
        self.assertEqual(screen_tx._ganze_gruppen(mitten, text).extract(text), ENTWICKLERIN)
        ganz = Selection(Offset(0, 0), Offset(len(text), 0))
        self.assertEqual(screen_tx._ganze_gruppen(ganz, text).extract(text), text)

    def test_fenster_kopiert_ganze_zelle(self):
        try:
            import terminal_fenster as TF
        except ImportError as e:
            self.skipTest(f"PySide6 fehlt: {e}")
        zeilen = [[HERZ, "", " ", "x"]]
        self.assertEqual(TF.auswahl_text(zeilen, (0, 0), (3, 0)), f"{HERZ} x")


class ImFenster(unittest.TestCase):
    def test_antwort_zeigen_und_markieren(self):
        import asyncio
        import ui
        from textual.selection import SELECT_ALL

        class Ctx:
            backend = None
            model = "ollama_cloud:x"

            def sync_session(self):
                pass

        async def go():
            sc = screen_tx.Screen()
            sc.install()
            try:
                sc.ctx, sc.handler = Ctx(), None
                sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
                app = screen_tx._NemiApp(sc)
                sc.app = app
                async with app.run_test(size=(80, 24)) as pilot:
                    ui.console.print(ui.assistant_panel(f"Klar ❤❤ und {ENTWICKLERIN} hier"))
                    await pilot.pause(0.3)
                    bild = "\n".join("".join(s.text for s in strip)
                                     for strip in app.screen._compositor.render_strips())
                    self.assertIn(f"Klar {HERZ}{HERZ} und {ENTWICKLERIN} hier", bild)
                    blk = list(app.query(".block"))[-1]
                    app.screen.selections = {blk: SELECT_ALL}
                    await pilot.pause(0.1)
                    self.assertIn(f"Klar {HERZ}{HERZ} und {ENTWICKLERIN} hier", app.screen.get_selected_text())
                    app.exit()
            finally:
                sc.uninstall() if hasattr(sc, "uninstall") else None

        with mock.patch.dict(os.environ, FENSTER):
            asyncio.run(go())


class AltesTui(unittest.TestCase):
    def test_screen_ohne_schalter_wie_bisher(self):
        self.assertEqual(screen.terminal_safe(HERZ), "❤")
        self.assertEqual(screen.terminal_safe(HERZ, emoji_ganz=True), HERZ)


if __name__ == "__main__":
    unittest.main()
