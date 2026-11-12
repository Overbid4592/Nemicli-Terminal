"""Offline-Test: Header/Banner ist responsiv und exakt zentriert.

python -m unittest discover -s tests -p test_banner.py
"""

import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from prompt_toolkit.buffer import Buffer          # noqa: E402
from rich.cells import cell_len                   # noqa: E402
from rich.console import Console                  # noqa: E402
import ui                                         # noqa: E402
import screen                                     # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _render(width: int) -> list[str]:
    """Banner in gegebener Breite rendern, ANSI entfernen, Zeilen zurückgeben."""
    sio = io.StringIO()
    c = Console(file=sio, force_terminal=True, color_system="truecolor",
                width=width, theme=ui._build_theme(ui.current_theme()), soft_wrap=False)
    c.print(ui._banner_panel(ui._render_logo(ui.APP_NAME), width))
    return [_ANSI.sub("", l) for l in sio.getvalue().rstrip("\n").split("\n")]


def _achse(line: str) -> tuple[float, int, int]:
    """Mittelachse des sichtbaren Inhalts (ohne Rahmen ┃) in Terminal-Spalten,
    dazu die Leerspalten links/rechts innerhalb des Rahmens."""
    body = line[1:-1]                        # Rahmenzeichen links/rechts weg
    left = len(body) - len(body.lstrip(" "))
    right = len(body) - len(body.rstrip(" "))
    inner = cell_len(body.strip(" "))
    return 1 + left + inner / 2, left, right


class BannerCenteringTests(unittest.TestCase):
    def test_logo_rows_have_no_trailing_gap(self):
        for row in ui._logo_rows(ui.APP_NAME):
            self.assertFalse(row.endswith("  "), repr(row))
        self.assertEqual(len({len(r) for r in ui._logo_rows(ui.APP_NAME)}), 1)

    def test_glyphs_unchanged(self):
        """Das Block-Logo selbst bleibt exakt wie vorher (nur der Nachlauf fehlt)."""
        alt = ["", "", "", "", ""]
        for ch in ui.APP_NAME.upper():
            for r in range(5):
                alt[r] += ui._FONT[ch][r] + "  "
        self.assertEqual([a.rstrip(" ") for a in alt],
                         [n.rstrip(" ") for n in ui._logo_rows(ui.APP_NAME)])

    def test_same_axis_for_logo_subtitle_credit(self):
        """Logo-Block, Untertitel und Autorenzeile liegen auf einer Mittelachse.

        Auf einem Zellraster lässt sich ein Text nur auf eine halbe Spalte genau
        zentrieren; rich rundet dabei immer gleich (Überhang links = floor).
        Deshalb: jede Zeile höchstens 0,5 Spalten neben der Fenstermitte und
        rechts nie weniger Luft als links.
        """
        logo_rows = ui._logo_rows(ui.APP_NAME)
        for width in (60, 79, 80, 100, 101, 120, 157, 200):
            lines = _render(width)
            self.assertTrue(all(cell_len(l) == width for l in lines), width)
            content = [l for l in lines[1:-1] if l[1:-1].strip()]
            self.assertGreaterEqual(len(content), 5 + 2)
            # Logo-Block ist starr: alle 5 Zeilen beginnen in derselben Spalte
            # (die Glyphen selbst haben innen Leerspalten – die zählen mit)
            starts = {lines[2 + r].index(logo_rows[r]) for r in range(5)}
            self.assertEqual(len(starts), 1, (width, starts))
            start = starts.pop()
            logo_w = len(logo_rows[0])
            self.assertAlmostEqual(start + logo_w / 2, width / 2, delta=0.5, msg=width)
            # Untertitel/Autorenzeile: gleiche Achse, gleiche Rundung
            for l in content[5:]:
                ax, left, right = _achse(l)
                self.assertIn(right - left, (0, 1), (width, l))
                self.assertAlmostEqual(ax, width / 2, delta=0.5, msg=(width, l))
            # Parität gleich -> exakt dieselbe Achse (z.B. Breite 120: alle 60.0)
            if (width - 2 - 2 * ui._banner_pad(width) - logo_w) % 2 == 0:
                sub = content[5]
                self.assertEqual(_achse(sub)[0], start + logo_w / 2, (width, sub))

    def test_padding_shrinks_when_narrow(self):
        logo_w = max(len(r) for r in ui._logo_rows(ui.APP_NAME))
        self.assertEqual(ui._banner_pad(logo_w + 2 + 8), 4)
        self.assertEqual(ui._banner_pad(logo_w + 2 + 7), 1)
        self.assertEqual(ui._banner_pad(None), 4)
        lines = _render(logo_w + 4)            # Rahmen + 1 Padding je Seite
        self.assertTrue(all(cell_len(l) == logo_w + 4 for l in lines))
        self.assertIn("█", "".join(lines))


class _FakeOutput:
    def __init__(self, cols):
        self.cols = cols

    def get_size(self):
        class S:
            pass
        s = S()
        s.columns, s.rows = self.cols, 40
        return s


class _FakeApp:
    def __init__(self, cols):
        self.output = _FakeOutput(cols)


class BannerReflowTests(unittest.TestCase):
    def _screen(self, cols):
        sc = screen.Screen()
        sc.app = _FakeApp(cols)
        sc.buffer = Buffer()                  # _visible() misst die Eingabehöhe
        sc.invalidate = lambda: None
        return sc

    def test_reflow_replaces_block_by_reference_not_by_line_count(self):
        sc = self._screen(120)
        sc.transcript.append("davor")
        sc.print_reflow(ui.banner_renderable)
        sc.transcript.append("danach")
        first = [l for l in sc.transcript if isinstance(l, screen._ReflowLine)]
        n_first = len(first)
        self.assertTrue(all(cell_len(_ANSI.sub("", l)) == 119 for l in first))

        # sehr schmal: Höhe des Blocks ändert sich (Untertitel/Credit brechen um)
        sc.app.output.cols = 46
        sc._reflow(sc._text_width())
        second = [l for l in sc.transcript if isinstance(l, screen._ReflowLine)]
        self.assertNotEqual(len(second), n_first)
        self.assertTrue(all(cell_len(_ANSI.sub("", l)) == 45 for l in second))
        self.assertEqual(sc.transcript[0], "davor")
        self.assertEqual(sc.transcript[-1], "danach")
        self.assertEqual(len(sc.transcript), len(second) + 2)   # kein Rest vom alten Block

    def test_no_rerender_when_width_unchanged(self):
        sc = self._screen(100)
        calls = []

        def make():
            calls.append(1)
            return ui.banner_renderable()

        sc.print_reflow(make)
        self.assertEqual(len(calls), 1)
        before = list(sc.transcript)
        sc._reflow(sc._text_width())          # gleiche Breite -> nichts rendern
        self.assertEqual(len(calls), 1)
        self.assertEqual(sc.transcript, before)
        sc.app.output.cols = 90
        sc._reflow(sc._text_width())          # andere Breite -> genau ein Rendern
        self.assertEqual(len(calls), 2)
        sc._reflow(sc._text_width())
        self.assertEqual(len(calls), 2)

    def test_visible_triggers_reflow_on_resize(self):
        sc = self._screen(100)
        sc.install()
        try:
            ui.banner()
            sc._visible()
            w1 = [cell_len(_ANSI.sub("", l)) for l in sc.transcript
                  if isinstance(l, screen._ReflowLine)]
            sc.app.output.cols = 80
            sc._visible()
            w2 = [cell_len(_ANSI.sub("", l)) for l in sc.transcript
                  if isinstance(l, screen._ReflowLine)]
            self.assertEqual(set(w1), {99})
            self.assertEqual(set(w2), {79})
            self.assertEqual(ui.console.width, 79)
        finally:
            sc.uninstall()


if __name__ == "__main__":
    unittest.main()
