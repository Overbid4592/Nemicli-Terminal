"""Eigenes Terminal: Tasten/Maus → Folgen, Farben, pyte-Bildschirm, Pseudokonsole, Emojis, Ablegen."""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import pyte                          # noqa: E402
import terminal_fenster as TF        # noqa: E402


class Tasten(unittest.TestCase):
    def test_pfeile_und_modi(self):
        self.assertEqual(TF.taste_zu_folge("up"), "\x1b[A")
        self.assertEqual(TF.taste_zu_folge("up", anwendungs_cursor=True), "\x1bOA")
        self.assertEqual(TF.taste_zu_folge("left", strg=True), "\x1b[1;5D")
        self.assertEqual(TF.taste_zu_folge("home"), "\x1b[H")
        self.assertEqual(TF.taste_zu_folge("delete"), "\x1b[3~")
        self.assertEqual(TF.taste_zu_folge("f1"), "\x1bOP")
        self.assertEqual(TF.taste_zu_folge("f5"), "\x1b[15~")

    def test_steuertasten(self):
        self.assertEqual(TF.taste_zu_folge("enter"), "\r")
        self.assertEqual(TF.taste_zu_folge("backspace"), "\x7f")
        self.assertEqual(TF.taste_zu_folge("tab", shift=True), "\x1b[Z")
        self.assertEqual(TF.taste_zu_folge("", "c", strg=True), "\x03")
        self.assertEqual(TF.taste_zu_folge("", "x", alt=True), "\x1bx")
        self.assertEqual(TF.taste_zu_folge("", "ä"), "ä")

    def test_maus(self):
        self.assertEqual(TF.maus_folge(0, 4, 2), "\x1b[<0;5;3M")
        self.assertEqual(TF.maus_folge(0, 4, 2, losgelassen=True), "\x1b[<0;5;3m")
        self.assertEqual(TF.maus_folge(64, 0, 0), "\x1b[<64;1;1M")
        self.assertEqual(TF.maus_folge(0, 0, 0, bewegung=True, strg=True), "\x1b[<48;1;1M")
        self.assertEqual(TF.maus_folge(0, 0, 0, sgr=False), "\x1b[M" + chr(32) + chr(33) + chr(33))


class Markieren(unittest.TestCase):
    def test_auswahl_text(self):
        zeilen = [list("Hallo Welt   "), list("zweite Zeile "), ["a", "🥰", "", "b", " "]]
        self.assertEqual(TF.auswahl_text(zeilen, (6, 0), (5, 1)), "Welt\nzweite")
        self.assertEqual(TF.auswahl_text(zeilen, (5, 1), (6, 0)), "Welt\nzweite")   # rückwärts gezogen
        self.assertEqual(TF.auswahl_text(zeilen, (0, 2), (4, 2)), "a🥰b")


class Steuertitel(unittest.TestCase):
    def test_steuertitel_wird_befehl_normaler_titel_bleibt(self):
        befehle = []
        schirm = TF.Schirm(80, 24, steuer=befehle.append)
        strom = pyte.Stream(schirm)
        strom.feed("\x1b]0;nemicli-steuer:tray:1\x07\x1b]0;nemicli-steuer:zeigen:2\x07")
        strom.feed("\x1b]0;Mein Titel\x07")
        self.assertEqual(befehle, ["tray", "zeigen"])
        self.assertEqual(schirm.title, "Mein Titel")


class Farben(unittest.TestCase):
    def test_namen_hell_hex_standard(self):
        t = TF.THEMEN["breeze"]
        self.assertEqual(TF.farbe("red", t, True), t["normal"][1])
        self.assertEqual(TF.farbe("brightblue", t, True), t["hell"][4])
        self.assertEqual(TF.farbe("ff8800", t, True), "#ff8800")
        self.assertEqual(TF.farbe("default", t, True), t["vordergrund"])
        self.assertEqual(TF.farbe("default", t, False), t["hintergrund"])


class Bildschirm(unittest.TestCase):
    def test_modi_und_antworten(self):
        antworten = []
        s = TF.Schirm(40, 10, antworten.append)
        strom = pyte.Stream(s)
        strom.feed("\x1b[?1003h\x1b[?1006h\x1b[?2004hHallo \x1b[31mrot\x1b[0m")
        self.assertTrue(s.maus_an() and s.maus_bewegung() and s.maus_sgr() and s.einfuegen_geklammert())
        self.assertEqual("".join(c.data for c in s.buffer[0].values()).strip()[:10], "Hallo rot")
        self.assertEqual(s.buffer[0][6].fg, "red")
        strom.feed("\x1b[6n")                                          # Cursor-Position erfragt
        self.assertTrue(antworten and antworten[-1].startswith("\x1b[1;"))
        strom.feed("\x1b[?1003l\x1b[?1000l")
        self.assertFalse(s.maus_an())

    def test_wahres_rgb(self):
        s = TF.Schirm(20, 3)
        pyte.Stream(s).feed("\x1b[38;2;10;20;30mX")
        self.assertEqual(TF.farbe(s.buffer[0][0].fg, TF.THEMEN["mint"], True), "#0a141e")


@unittest.skipUnless(os.name == "nt", "Pseudokonsole gibt es nur unter Windows")
class Pseudokonsole(unittest.TestCase):
    def test_ausgabe_kommt_im_raster_an(self):
        import terminal_pty
        s = TF.Schirm(80, 24)
        strom = pyte.Stream(s)
        lock, fertig = threading.Lock(), threading.Event()

        def rein(text):
            with lock:
                strom.feed(text)
        cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        p = terminal_pty.Pty([cmd, "/c", "echo NemiCLI-Terminal-Test"], 80, 24,
                             bei_ausgabe=rein, bei_ende=lambda c: fertig.set())
        self.assertTrue(fertig.wait(15))
        self.assertEqual(p.exitcode, 0)
        with lock:
            bild = "\n".join("".join(c.data for c in s.buffer[y].values()) for y in range(s.lines))
        self.assertIn("NemiCLI-Terminal-Test", bild)
        self.assertIn("cmd.exe", s.title)                              # Fenstertitel per OSC

    def test_eingabe_und_groesse(self):
        import terminal_pty
        aus, fertig, bereit = [], threading.Event(), threading.Event()

        def rein(text):
            aus.append(text)
            if "cmd.exe" in text:                     # Fenstertitel gesetzt = cmd ist gestartet
                bereit.set()
        cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        p = terminal_pty.Pty([cmd, "/q", "/k"], 60, 20, bei_ausgabe=rein, bei_ende=lambda c: fertig.set())
        self.assertTrue(bereit.wait(15))
        p.groesse(100, 30)
        p.schreiben("echo getippt\r")
        p.schreiben("exit\r")
        self.assertTrue(fertig.wait(15))
        self.assertIn("getippt", "".join(aus))


class Ablegen(unittest.TestCase):
    def test_pfade_wie_in_der_konsole(self):
        self.assertEqual(TF.abgelegt_zu_text([r"C:\a\b.png"]), r"C:\a\b.png ")
        self.assertEqual(TF.abgelegt_zu_text([r"C:\mit leer\x.png", r"C:\y.txt"]),
                         '"C:\\mit leer\\x.png" C:\\y.txt ')
        self.assertEqual(TF.abgelegt_zu_text([]), "")


def _taste(down, vk, zeichen, zustand):
    from textual.drivers.win32 import INPUT_RECORD
    r = INPUT_RECORD()
    r.EventType = 1
    k = r.Event.KeyEvent
    k.bKeyDown, k.wVirtualKeyCode, k.dwControlKeyState = down, vk, zustand
    k.uChar.UnicodeChar = zeichen
    return r


@unittest.skipUnless(os.name == "nt", "Konsolen-Eingabe gibt es nur unter Windows")
class EmojiEingabe(unittest.TestCase):
    def _folge(self, zeichen):
        """Wie die Pseudokonsole ein Zeichen ohne Taste liefert: Alt+Ziffernblock."""
        return [_taste(1, 0x12, "\x00", 2), _taste(1, 0x66, "\x00", 2), _taste(0, 0x66, "\x00", 2),
                _taste(0, 0x12, zeichen, 0)]

    def test_alt_ziffernblock_wird_zeichen(self):
        import eingabe_win
        from textual.drivers.win32 import INPUT_RECORD
        roh = self._folge("\ud83d") + self._folge("\ude00") + [_taste(1, 0x41, "a", 0)]
        puffer = (INPUT_RECORD * len(roh))(*roh)
        self.assertEqual(eingabe_win.umwandeln(puffer, len(roh)), 2)
        # was Textual daraus liest: nur gedrückte Tasten, ausgeblendete zählen nicht
        gelesen = "".join(r.Event.KeyEvent.uChar.UnicodeChar for r in puffer
                          if r.EventType == 1 and r.Event.KeyEvent.bKeyDown)
        self.assertEqual(gelesen.encode("utf-16", "surrogatepass").decode("utf-16"), "\U0001F600a")

    def test_normales_alt_bleibt(self):
        import eingabe_win
        from textual.drivers.win32 import INPUT_RECORD
        roh = [_taste(1, 0x12, "\x00", 2), _taste(0, 0x12, "\x00", 0)]
        puffer = (INPUT_RECORD * 2)(*roh)
        self.assertEqual(eingabe_win.umwandeln(puffer, 2), 0)
        self.assertEqual([r.EventType for r in puffer], [1, 1])

    def test_emoji_durch_pseudokonsole_in_textual(self):
        import tempfile
        import terminal_pty
        with tempfile.TemporaryDirectory() as tmp:
            erg = Path(tmp) / "wert.txt"
            app = (
                "import sys; sys.path.insert(0, sys.argv[1]); import eingabe_win; eingabe_win.einschalten()\n"
                "from pathlib import Path\n"
                "from textual.app import App\n"
                "from textual.widgets import Input\n"
                "erg = Path(sys.argv[2])\n"
                "class A(App):\n"
                "    def compose(self): yield Input()\n"
                "    def on_mount(self): self.query_one(Input).focus(); erg.write_text('BEREIT')\n"
                "    def on_input_submitted(self, ev): erg.write_text('WERT:' + ascii(ev.value)); self.exit()\n"
                "A().run()\n")
            fertig = threading.Event()
            p = terminal_pty.Pty([sys.executable, "-c", app, str(ROOT / "ui"), str(erg)], 80, 24,
                                 bei_ausgabe=lambda t: None, bei_ende=lambda c: fertig.set(),
                                 env=dict(os.environ))
            try:
                for _ in range(150):
                    if erg.exists() and erg.read_text() == "BEREIT":
                        break
                    time.sleep(0.1)
                time.sleep(0.3)
                p.schreiben("A\u2764\ufe0fB\U0001F600\u00e4\r")
                self.assertTrue(fertig.wait(15))
                self.assertEqual(erg.read_text(), "WERT:" + ascii("A\u2764\ufe0fB\U0001F600\u00e4"))
            finally:
                if p.laeuft():
                    p.beenden()


if __name__ == "__main__":
    unittest.main()
