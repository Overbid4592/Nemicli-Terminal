"""Fremder Text in der Oberfläche: kein Markup in Auswahl-Knöpfen und Rich-Titeln, im Reiter „KI“ nur Farben,
Bild-Platzhalter nur in echten Bildstellen der Startseite."""
import asyncio
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rich.console import Console                  # noqa: E402
from rich.text import Text                        # noqa: E402
import screen_tx                                  # noqa: E402
import startseite                                 # noqa: E402
import terminal_text                              # noqa: E402
import ui                                         # noqa: E402

FREMD = ["[Q4_K_M]", "[/x]", "[@click=app.quit]weg"]


def gerendert(renderable) -> str:
    c = Console(file=io.StringIO(), width=80, color_system=None, theme=ui._build_theme(ui.current_theme()))
    c.print(renderable)
    return c.file.getvalue()


class NurFarbenTests(unittest.TestCase):
    def test_steuerfolgen_weg_farben_bleiben(self):
        roh = "ok\x1b[2K\x1b[1A\r✗ x\x1b]52;c;QQ==\x07\x1b[31mrot\x1b[0m\ue001\tende\n"
        self.assertEqual("ok✗ x\x1b[31mrot\x1b[0m\tende\n", terminal_text.nur_farben(roh))

    def test_ki_reiter_ausgabe_kann_exitcode_nicht_ueberdecken(self):
        import terminal_fenster as TF
        aus = TF.ki_text({"t": "aus", "text": "a\x1b[1A\x1b[2Kb\r\nc"})
        self.assertEqual("ab\r\nc", aus)
        kopf = TF.ki_text({"t": "start", "befehl": "echo\x1b[H", "ordner": "C:/x\x1b[2J"})
        self.assertNotIn("\x1b[H", kopf)
        self.assertNotIn("\x1b[2J", kopf)


class BildstellenTests(unittest.TestCase):
    def test_platzhalter_nur_in_bildstelle(self):
        self.assertEqual("ab", screen_tx.sanitize(Text("a\ue001b")).plain)
        self.assertEqual("ab", screen_tx.sanitize("a\ue040b"))
        echt = startseite.Bildstelle("\ue000\ue000")
        self.assertEqual("\ue000\ue000", screen_tx.sanitize(echt).plain)


class MarkupTests(unittest.TestCase):
    def test_rich_titel_und_denkzeile_ohne_markup(self):
        for name in FREMD:
            with self.subTest(name=name):
                self.assertIn(name, gerendert(ui.practice_code(name, "x = 1")))
                gedruckt = []
                with mock.patch.object(ui, "TUI", object()), \
                        mock.patch.object(ui.console, "print", side_effect=gedruckt.append):
                    with ui.thinking(name):
                        pass
                self.assertIn(name, gerendert(gedruckt[0]))

    def test_auswahl_knoepfe_zeigen_text_woertlich(self):
        from textual.app import App
        from textual.widgets import Button

        gewaehlt = []

        class Probe(App):
            def compose(self):
                yield screen_tx._InlineSelect("Welche?", [(i, t) for i, t in enumerate(FREMD)], gewaehlt.append)

        async def go():
            app = Probe()
            async with app.run_test(size=(80, 12)) as pilot:
                await pilot.pause(0.1)
                texte = [b.label.plain for b in app.query(Button)]
                for t in FREMD:
                    self.assertTrue(any(t in x for x in texte), (t, texte))
                await pilot.press("enter")
                await pilot.pause(0.1)
            return app.return_code
        asyncio.run(go())
        self.assertEqual([0], gewaehlt)


if __name__ == "__main__":
    unittest.main()
