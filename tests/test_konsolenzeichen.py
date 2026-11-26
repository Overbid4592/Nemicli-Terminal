"""Zusammengesetzte Emojis vor der Konsole glätten: Flaggen, Verbinder-Folgen, Hauttöne und Tasten-Kappen zählt
Windows' Pseudokonsole breiter als rich/Textual – eine volle letzte Zeile brach um, die Ansicht rollte hoch.

python -m unittest tests.test_konsolenzeichen
"""
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import konsolenzeichen as KZ
from rich.cells import cell_len


class Glaetten(unittest.TestCase):
    def test_heikle_gruppen_breitengleich(self):
        for roh, erwartet in (("Flagge 🇩🇪 da", "Flagge DE da"), ("us 🇺🇸!", "us US!"), ("Dev 👩‍💻 ok", "Dev 👩 ok"),
                              ("Daumen 👍🏻 hoch", "Daumen 👍 hoch"), ("puh 😮‍💨", "puh 😮")):
            with self.subTest(roh=roh):
                neu = KZ.glaetten(roh)
                self.assertEqual(erwartet, neu)
                self.assertEqual(cell_len(roh), cell_len(neu))
        taste = KZ.glaetten("Nr 1️⃣ los")
        self.assertTrue(taste.startswith("Nr 1"))
        self.assertEqual(cell_len("Nr 1️⃣ los"), cell_len(taste))

    def test_anderes_bleibt_gleich(self):
        for text in ("", "nur Text", "Herz ❤️ und ⚙️ und 💜 🐈 ✨", "\x1b[38;2;1;2;3m🤖\x1b[0m ◆ ○ █ · „x“"):
            self.assertEqual(text, KZ.glaetten(text))

    def test_escape_folgen_bleiben(self):
        roh = "\x1b[12;1H\x1b[1mDE: 🇩🇪\x1b[0m"
        self.assertEqual("\x1b[12;1H\x1b[1mDE: DE\x1b[0m", KZ.glaetten(roh))

    def test_treiber_wird_einmal_umgeleitet(self):
        from textual.drivers import windows_driver as W
        alt = W.WindowsDriver.write
        geschrieben = []

        def roh(self, data):
            geschrieben.append(data)
        roh._glatt = False
        try:
            W.WindowsDriver.write = roh
            KZ.treiber_glaetten()
            KZ.treiber_glaetten()                                    # zweimal: nicht doppelt
            W.WindowsDriver.write(object(), "x 🇩🇪")
            self.assertEqual(["x DE"], geschrieben)
            self.assertTrue(W.WindowsDriver.write._glatt)
        finally:
            W.WindowsDriver.write = alt

    def test_oberflaeche_leitet_um(self):
        quelle = (ROOT / "ui" / "screen_tx.py").read_text(encoding="utf-8")
        self.assertIn("konsolenzeichen.treiber_glaetten()", quelle)


@unittest.skipUnless(os.name == "nt", "Pseudokonsole gibt es nur unter Windows")
class EchtePseudokonsole(unittest.TestCase):
    """Volle letzte Zeile mit Flagge durch eine echte ConPTY: ohne Glätten rollt die Ansicht, mit nicht."""

    def _lauf(self, zeichen: str) -> bool:
        import pyte
        import terminal_fenster as T
        import terminal_pty
        tmp = Path(tempfile.mkdtemp())
        kind = tmp / "kind.py"
        kind.write_text(textwrap.dedent(f"""
            import sys, time
            sys.path[:0] = {[str(ROOT / d) for d in ("ui", "core")]!r}
            from rich.cells import cell_len
            import konsolenzeichen
            z = sys.argv[1]
            sys.stdout.write("\\x1b[2J\\x1b[1;1HOBEN\\x1b[10;1H" + "a" * (40 - cell_len(z)) + konsolenzeichen.glaetten(z))
            sys.stdout.flush()
            time.sleep(1.0)
            """), encoding="utf-8")
        teile, fertig = [], threading.Event()
        p = terminal_pty.Pty([sys.executable, str(kind), zeichen], 40, 10, bei_ausgabe=teile.append,
                             bei_ende=lambda _c: fertig.set())
        fertig.wait(15)
        time.sleep(0.2)
        p.beenden(1)

        class Schirm(T._NullbreiteAnhaengen, pyte.Screen):
            pass
        s = Schirm(40, 10)
        pyte.Stream(s).feed("".join(teile))
        return "".join(s.buffer[0][x].data for x in range(4)) == "OBEN"

    def test_flagge_und_verbinder_rollen_nicht(self):
        for zeichen in ("🇩🇪", "👩‍💻", "👍🏻"):
            with self.subTest(zeichen=zeichen):
                self.assertTrue(self._lauf(zeichen))


if __name__ == "__main__":
    unittest.main()
