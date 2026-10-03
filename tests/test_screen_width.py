"""Offline-Test: Emoji-Normalisierung fürs TUI (Zeichenbreite Terminal ↔ prompt_toolkit).

python -m unittest discover -s tests -p test_screen_width.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from prompt_toolkit.utils import get_cwidth      # noqa: E402
from rich.cells import cell_len                   # noqa: E402
import screen                                     # noqa: E402


class TerminalSafeTests(unittest.TestCase):
    def test_variation_selector_and_keycap_removed(self):
        self.assertEqual(screen.terminal_safe("❤️"), "❤")          # ❤️ -> ❤
        self.assertEqual(screen.terminal_safe("⚠️ x"), "⚠ x")      # ⚠️ -> ⚠
        self.assertEqual(screen.terminal_safe("1️⃣"), "1")              # 1️⃣ -> 1

    def test_zwj_and_skin_tone_collapse(self):
        self.assertEqual(screen.terminal_safe("\U0001F468‍\U0001F4BB"), "\U0001F468")   # 👨‍💻 -> 👨
        self.assertEqual(screen.terminal_safe("\U0001F44B\U0001F3FD"), "\U0001F44B")         # 👋🏽 -> 👋
        self.assertEqual(screen.terminal_safe("❤️‍\U0001F525"), "❤")     # ❤️‍🔥 -> ❤

    def test_plain_text_untouched(self):
        for s in ("", "plain ascii", "\U0001F408 Nemi", "Umlaute äöü ✓ ─│╭╮"):
            self.assertEqual(screen.terminal_safe(s), s)

    def test_normalized_widths_agree(self):
        """Nach der Normalisierung rechnen prompt_toolkit und rich gleich."""
        for s in ("❤️", "⚠️ Achtung", "\U0001F468‍\U0001F4BB",
                  "\U0001F44B\U0001F3FD", "\U0001F6E1️ Geschützt"):
            n = screen.terminal_safe(s)
            self.assertEqual(get_cwidth(n), cell_len(n), repr(n))

    def test_dirty_screen_differs_everywhere(self):
        d = screen._dirty_screen(30, 100)
        self.assertEqual((d.height, d.width), (30, 100))
        self.assertNotEqual(d.data_buffer[5][7].char, " ")
        self.assertNotEqual(d.data_buffer[29][99].char, "x")


if __name__ == "__main__":
    unittest.main()
