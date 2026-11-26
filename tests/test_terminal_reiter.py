"""Terminal-Fenster: Reiter, Reiter „KI“ (Befehle der KI live) und die Leitung von NemiCLI dorthin."""
import os
import queue
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen
for d in ("", "core", "ui", "tools"):
    if str(R / d) not in sys.path:
        sys.path.insert(0, str(R / d))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import terminal_fenster as TF        # noqa: E402


def _bildschirm_text(schirm) -> str:
    return "\n".join("".join(schirm.buffer[y][x].data for x in range(schirm.columns)).rstrip()
                     for y in range(schirm.lines))


class KiText(unittest.TestCase):
    def test_befehl_ausgabe_ende(self):
        kopf = TF.ki_text({"t": "start", "befehl": "Get-Date\nGet-Process", "ordner": "C:/projekt"})
        self.assertIn("C:/projekt", kopf)
        self.assertIn("Get-Date\r\n   Get-Process", kopf)
        self.assertEqual(TF.ki_text({"t": "aus", "text": "a\nb\r\n"}), "a\r\nb\r\n")
        self.assertTrue(TF.ki_text({"t": "aus", "text": "x", "fehler": True}).startswith("\x1b[31m"))
        self.assertIn("✓ fertig", TF.ki_text({"t": "ende", "code": 0, "sekunden": 1.5}))
        self.assertIn("1,5 s", TF.ki_text({"t": "ende", "code": 0, "sekunden": 1.5}))
        self.assertIn("Exitcode 2", TF.ki_text({"t": "ende", "code": 2, "sekunden": 0.1}))
        self.assertIn("abgebrochen", TF.ki_text({"t": "ende", "abgebrochen": True, "sekunden": 3}))
        self.assertEqual(TF.ki_text({"t": "unbekannt"}), "")


class ReiterName(unittest.TestCase):
    def test_pfade_kurz(self):
        b = chr(92)
        self.assertEqual(TF.reiter_name(f"C:{b}WINDOWS{b}system32{b}cmd.exe"), "cmd.exe")
        self.assertEqual(TF.reiter_name(f"C:{b}WINDOWS{b}system32{b}cmd.exe - python  main.py"),
                         "cmd.exe - python  main.py")
        self.assertEqual(TF.reiter_name("git log"), "git log")
        lang = TF.reiter_name("x" * 50)
        self.assertEqual(len(lang), TF.REITER_ZEICHEN)
        self.assertTrue(lang.endswith("…"))


class KiBildschirm(unittest.TestCase):
    def test_verlauf_zum_zurueckrollen_und_keine_antworten(self):
        import pyte
        s = TF.KiSchirm(40, 5)
        strom = pyte.Stream(s)
        strom.feed("".join(f"zeile {i}\r\n" for i in range(30)))
        self.assertNotIn("zeile 3\n", _bildschirm_text(s) + "\n")
        s.prev_page()
        s.prev_page()
        self.assertIn("zeile 2", _bildschirm_text(s))
        strom.feed("\x1b[6n\x1b]0;nemicli-steuer:tray:1\x07")      # Anfrage und Steuertitel: wirkungslos
        self.assertFalse(s.maus_an() or s.einfuegen_geklammert() or s.anwendungs_cursor())


class Leitung(unittest.TestCase):
    def test_nemicli_meldet_befehle_ans_fenster(self):
        import ki_terminal
        leitung = TF.KiLeitung()
        umgebung = leitung.umgebung()
        with mock.patch.object(ki_terminal, "_ADRESSE", umgebung["NEMICLI_KI_PIPE"]), \
                mock.patch.object(ki_terminal, "_SCHLUESSEL", umgebung["NEMICLI_KI_KEY"]), \
                mock.patch.dict(ki_terminal._zustand, {"faden": None, "weg": False}), \
                mock.patch.object(ki_terminal, "_schlange", queue.Queue()):
            ki_terminal.start("dir", "C:/")
            ki_terminal.ausgabe("eins\n")
            ki_terminal.ausgabe("kaputt\n", fehler=True)
            ki_terminal.ende(0, 0.42)
            ereignisse = []
            ende = time.monotonic() + 5
            while len(ereignisse) < 4 and time.monotonic() < ende:
                ereignisse += leitung.holen()
                time.sleep(0.02)
        self.assertEqual([e["t"] for e in ereignisse], ["start", "aus", "aus", "ende"])
        self.assertTrue(ereignisse[2]["fehler"])
        self.assertEqual(ereignisse[3]["sekunden"], 0.4)

    def test_falscher_schluessel_kommt_nicht_durch(self):
        import ki_terminal
        leitung = TF.KiLeitung()
        with mock.patch.object(ki_terminal, "_ADRESSE", leitung.adresse), \
                mock.patch.object(ki_terminal, "_SCHLUESSEL", "00" * 32), \
                mock.patch.dict(ki_terminal._zustand, {"faden": None, "weg": False}), \
                mock.patch.object(ki_terminal, "_schlange", queue.Queue()):
            ki_terminal.start("dir")
            time.sleep(0.5)
            self.assertEqual(leitung.holen(), [])

    def test_ohne_fenster_still(self):
        import ki_terminal
        with mock.patch.object(ki_terminal, "_ADRESSE", ""):
            self.assertFalse(ki_terminal.aktiv())
            ki_terminal.start("dir")                               # tut nichts, wirft nichts

    def test_befehlsweg_meldet_start_ausgabe_ende(self):
        import actions
        import ki_terminal
        gemeldet = []
        programm = "import sys; print('hallo'); print('kaputt', file=sys.stderr); sys.exit(3)"
        with mock.patch.object(ki_terminal, "start", lambda b, o="": gemeldet.append(("start", b))), \
                mock.patch.object(ki_terminal, "ausgabe", lambda t, fehler=False: gemeldet.append(("aus", t, fehler))), \
                mock.patch.object(ki_terminal, "ende", lambda c, s, abgebrochen=False: gemeldet.append(("ende", c))):
            r = actions._powershell([sys.executable, "-c", programm], Path.cwd(), anzeige="Get-Date")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(gemeldet[0], ("start", "Get-Date"))
        self.assertIn(("aus", "hallo\n", False), gemeldet)
        self.assertIn(("aus", "kaputt\n", True), gemeldet)
        self.assertEqual(gemeldet[-1], ("ende", 3))

    def test_befehle_der_ki_erben_die_leitung_nicht(self):
        import config
        with mock.patch.dict(os.environ, {"NEMICLI_KI_PIPE": "x", "NEMICLI_KI_KEY": "y"}):
            env = config.umgebung_ohne_geheimnisse()
        self.assertNotIn("NEMICLI_KI_PIPE", env)
        self.assertNotIn("NEMICLI_KI_KEY", env)


@unittest.skipUnless(os.name == "nt", "Pseudokonsole nur unter Windows")
class FensterMitReitern(unittest.TestCase):
    def test_ki_reiter_und_kommandozeile(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["test"])
        programm = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(R / "core")!r})
            import ki_terminal
            time.sleep(0.3)
            ki_terminal.start("Get-ChildItem", "C:/")
            ki_terminal.ausgabe("datei_eins.txt\\n")
            ki_terminal.ende(0, 0.7)
            time.sleep(2.5)
        """)
        befund = {}

        def pruefen():
            fenster = next(w for w in app.topLevelWidgets() if hasattr(w, "reiter"))
            befund["reiter_vorher"] = len(fenster.reiter)
            befund["leiste"] = fenster.leiste.isVisibleTo(fenster)
            fenster.resize(520, 260)                               # kleiner: Bildschirm wird neu aufgebaut
            app.processEvents()
            befund["ki_text"] = _bildschirm_text(fenster.ki.schirm) if fenster.ki is not None else ""
            fenster.neue_shell()
            befund["mit_shell"] = len(fenster.reiter)
            fenster.reiter_schliessen(fenster.reiter[-1])
            fenster.reiter_schliessen(fenster.haupt)               # NemiCLI selbst bleibt
            befund["nachher"] = len(fenster.reiter)
            befund["titel_haupt"] = fenster.tabs.tabText(0)

        QTimer.singleShot(1500, pruefen)
        with mock.patch.object(TF, "_einstellungen", lambda: {**_standard(), "ki_reiter": True}):
            code = TF.starten([sys.executable, "-c", programm], titel="NemiCLI")
        self.assertEqual(code, 0)
        self.assertEqual(befund["reiter_vorher"], 2)                # NemiCLI + KI (von selbst aufgegangen)
        self.assertTrue(befund["leiste"])
        self.assertIn("Get-ChildItem", befund["ki_text"])
        self.assertIn("datei_eins.txt", befund["ki_text"])
        self.assertIn("✓ fertig", befund["ki_text"])
        text = befund["ki_text"]                                   # Reihenfolge bleibt nach dem Verkleinern
        self.assertLess(text.index("Get-ChildItem"), text.index("datei_eins.txt"))
        self.assertLess(text.index("datei_eins.txt"), text.index("✓ fertig"))
        self.assertEqual(befund["mit_shell"], 3)
        self.assertEqual(befund["nachher"], 2)
        self.assertEqual(befund["titel_haupt"], "NemiCLI")


def _standard() -> dict:
    return {"thema": TF.THEMEN["breeze"], "schrift": "", "groesse": 11, "deckkraft": 1.0, "hintergrund": 1.0,
            "unschaerfe": False}


if __name__ == "__main__":
    unittest.main()
