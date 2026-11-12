"""Terminal-Fenster und Vollbild: Links, Suche, Zeilenbilder, Leuchten, geteilte Ansicht, Antwort-Blöcke."""
import asyncio
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen
for d in ("", "core", "engines", "tools", "ui"):
    if str(R / d) not in sys.path:
        sys.path.insert(0, str(R / d))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyte  # noqa: E402
import terminal_fenster as TF  # noqa: E402
import terminal_text as TT  # noqa: E402

B = chr(92)                      # Backslash


def _zellen(text: str) -> list:
    return list(text)


class Zeilentext(unittest.TestCase):
    def test_breite_zeichen_und_bildstellen(self):
        zellen = ["a", "😀", "", "b", TF.platzhalter(0, 0), "c"]
        text, zelle = TT.zeilentext(zellen, TF.ist_platzhalter)
        self.assertEqual(text, "a😀b c")
        self.assertEqual(zelle, [0, 1, 3, 4, 5])


class Links(unittest.TestCase):
    def test_web_adresse_ohne_satzzeichen_am_ende(self):
        zeile = _zellen("Siehe https://example.org/seite. Und (https://example.org/a_(b)) hier")
        links = TT.finde_links(zeile, gibt_es=lambda p: False)
        self.assertEqual([(l[2], l[3]) for l in links],
                         [("web", "https://example.org/seite"), ("web", "https://example.org/a_(b)")])
        x0, x1 = links[0][:2]
        self.assertEqual("".join(zeile[x0:x1 + 1]), "https://example.org/seite")
        self.assertEqual(TT.link_an(zeile, x0 + 3, gibt_es=lambda p: False)[3], "https://example.org/seite")
        self.assertIsNone(TT.link_an(zeile, 0, gibt_es=lambda p: False))

    def test_unverschluesselte_adressen_sind_keine_links(self):
        zeile = _zellen("alt http://example.org/a und xhttps://example.org/b, neu https://example.org/c")
        self.assertEqual([l[3] for l in TT.finde_links(zeile, gibt_es=lambda p: False)], ["https://example.org/c"])

    def test_pfad_mit_leerzeichen_und_zeilennummer(self):
        da = {f"C:{B}Mein Ordner{B}datei eins.txt", f"C:{B}code{B}main.py"}
        zeile = _zellen(f"Gespeichert: C:{B}Mein Ordner{B}datei eins.txt und dann weiter")
        links = TT.finde_links(zeile, gibt_es=da.__contains__)
        self.assertEqual([(l[2], l[3]) for l in links], [("pfad", f"C:{B}Mein Ordner{B}datei eins.txt")])
        zeile = _zellen(f"  File C:{B}code{B}main.py:42: Fehler")
        self.assertEqual(TT.finde_links(zeile, gibt_es=da.__contains__)[0][3], f"C:{B}code{B}main.py")

    def test_kein_link_ohne_datei_und_keine_netzpfade(self):
        self.assertEqual(TT.finde_links(_zellen(f"C:{B}gibt{B}es{B}nicht.txt"), gibt_es=lambda p: False), [])
        gefragt = []
        TT.finde_links(_zellen(f"{B}{B}server{B}freigabe{B}datei.txt"), gibt_es=lambda p: gefragt.append(p) or True)
        self.assertEqual(gefragt, [])                        # Netzpfade werden nicht einmal angefasst

    def test_klick_startet_nie_ein_programm(self):
        with tempfile.TemporaryDirectory() as ordner:
            bild, programm = Path(ordner) / "a.PNG", Path(ordner) / "b.exe"
            bild.write_bytes(b"x")
            programm.write_bytes(b"x")
            self.assertEqual(TT.oeffnen_art(ordner), "ordner")
            self.assertEqual(TT.oeffnen_art(str(bild)), "datei")
            for name in ("b.exe", "c.bat", "d.ps1", "e.lnk", "f.js", "g.py", "h", "i.svg", "j.csv", "k.html"):
                self.assertEqual(TT.oeffnen_art(str(Path(ordner) / name)), "zeigen", name)
            with mock.patch.object(TT, "_starten") as starten, \
                    mock.patch.object(TT, "im_ordner_zeigen", return_value=True) as zeigen:
                self.assertTrue(TT.oeffnen("pfad", str(programm)))
                zeigen.assert_called_once_with(str(programm))
                starten.assert_not_called()
                self.assertFalse(TT.oeffnen("pfad", str(Path(ordner) / "fehlt.txt")))

    def test_nur_https_adressen_gehen_an_den_browser(self):
        with mock.patch("webbrowser.open", return_value=True) as browser:
            self.assertTrue(TT.oeffnen("web", "https://example.org/a?b=1"))
            for ziel in ("http://example.org/a", "javascript:alert(1)", "file:///C:/x", "https://example.org/a b",
                         "https://example.org/" + "x" * TT.LINK_MAX, "ftp://example.org"):
                self.assertFalse(TT.oeffnen("web", ziel), ziel)
            self.assertEqual(browser.call_count, 1)
        self.assertFalse(TT.oeffnen("anders", "https://example.org"))


class Suche(unittest.TestCase):
    def test_treffer_ohne_gross_klein_in_zellen(self):
        zeilen = [(10, _zellen("Hallo Welt")), (11, ["w", "😀", "", "W", "E", "L", "T"]), (12, _zellen("nichts"))]
        self.assertEqual(TT.suche(zeilen, "welt"), [(10, 6, 9), (11, 3, 6)])
        self.assertEqual(TT.suche(zeilen, "  "), [])
        self.assertEqual(TT.suche(zeilen, "a.c"), [])           # Suchtext ist kein Muster

    def test_naechster_laeuft_um(self):
        t = [1, 2, 3]
        self.assertEqual(TT.naechster(t, 2, 1), 0)
        self.assertEqual(TT.naechster(t, 0, -1), 2)
        self.assertEqual(TT.naechster(t, -1, -1), 2)
        self.assertEqual(TT.naechster([], 0, 1), -1)


class FensterGrundlagen(unittest.TestCase):
    def test_steuerwerte(self):
        self.assertEqual(TF.steuer_wert("grund=0C0D14"), ("grund", "#0c0d14"))
        self.assertEqual(TF.steuer_wert("puls=1"), ("puls", "1"))
        for name in ("leuchten", "weich", "geteilt"):
            self.assertEqual(TF.steuer_wert(f"{name}=0"), (name, "0"))
        for falsch in ("grund=0c0d1", "grund=zzzzzz", "puls=2", "puls=", "tray", "", "weich=an", "thema=1"):
            self.assertIsNone(TF.steuer_wert(falsch), falsch)

    def test_thema_aus_dem_farbschema(self):
        import design
        thema = TF.thema_aus_palette(design.PALETTEN["nachtglas"])
        self.assertEqual(thema["hintergrund"], design.PALETTEN["nachtglas"]["grund"])
        self.assertEqual(TF.akzente(thema), (design.PALETTEN["nachtglas"]["cyan"], design.PALETTEN["nachtglas"]["lila"]))
        self.assertEqual(len(thema["normal"]), 8)
        self.assertEqual(TF.akzente(TF.THEMEN["breeze"]), (TF.THEMEN["breeze"]["hell"][4], TF.THEMEN["breeze"]["hell"][5]))

    def test_einstellungen_auto_folgt_dem_theme(self):
        import config
        import design
        with mock.patch.object(config, "load", lambda: {"theme": "neon"}):
            e = TF._einstellungen()
        self.assertEqual(e["thema"]["hintergrund"], design.PALETTEN["neon"]["grund"])
        self.assertTrue(e["leuchten"] and e["weich"])
        self.assertFalse(e["geteilt"])
        with mock.patch.object(config, "load", lambda: {"terminal_thema": "mint", "terminal_leuchten": False}):
            e = TF._einstellungen()
        self.assertIs(e["thema"], TF.THEMEN["mint"])
        self.assertFalse(e["leuchten"])

    def test_ki_bildschirm_zaehlt_hinausgerollte_zeilen(self):
        s = TF.KiSchirm(20, 4)
        pyte.Stream(s).feed("".join(f"z{i}\r\n" for i in range(10)))
        self.assertEqual(s.geschoben, 7)                      # 10 Zeilen + leere letzte, 4 sichtbar
        self.assertEqual(len(s.history.top), 7)


@unittest.skipUnless(os.name == "nt", "Konsolentitel nur unter Windows")
class Steuerbefehle(unittest.TestCase):
    def test_befehle_gehen_einzeln_und_mit_abstand_hinaus(self):
        """Die Pseudokonsole reicht von zwei Titeln kurz hintereinander nur den zweiten weiter."""
        import ctypes
        import time
        import main_quelle
        quelle = main_quelle.text()
        raum = {"os": os, "time": time}
        exec(quelle[quelle.index("_STEUER_NR = 0"):quelle.index("def gui_oeffnen")], raum)
        gesetzt = []
        with mock.patch.dict(os.environ, {"NEMICLI_FENSTER": "1", "NEMICLI_STEUER_KEY": "k"}), \
                mock.patch.object(ctypes.windll.kernel32, "SetConsoleTitleW",
                                  lambda titel: gesetzt.append((time.monotonic(), titel))):
            for befehl in ("grund=0c0d14", "puls=0", "puls=1"):
                raum["_fenster_steuern"](befehl)
            ende = time.monotonic() + 3
            while len(gesetzt) < 3 and time.monotonic() < ende:
                time.sleep(0.01)
        self.assertEqual([titel for _zeit, titel in gesetzt],
                         ["nemicli-steuer:k:grund=0c0d14:1", "nemicli-steuer:k:puls=0:2", "nemicli-steuer:k:puls=1:3"])
        abstaende = [b[0] - a[0] for a, b in zip(gesetzt, gesetzt[1:])]
        self.assertGreaterEqual(min(abstaende), 0.05)

    def test_ohne_eigenes_fenster_bleibt_der_titel(self):
        import ctypes
        import time
        import main_quelle
        quelle = main_quelle.text()
        raum = {"os": os, "time": time}
        exec(quelle[quelle.index("_STEUER_NR = 0"):quelle.index("def gui_oeffnen")], raum)
        with mock.patch.dict(os.environ, {"NEMICLI_FENSTER": ""}), \
                mock.patch.object(ctypes.windll.kernel32, "SetConsoleTitleW") as titel:
            raum["_fenster_steuern"]("puls=1")
            time.sleep(0.15)
        titel.assert_not_called()
        self.assertIsNone(raum["_STEUER_SCHLANGE"])


def _standard() -> dict:
    return {"thema": TF.THEMEN["breeze"], "schrift": "", "groesse": 11, "deckkraft": 1.0, "hintergrund": 1.0,
            "unschaerfe": False, "ki_reiter": True, "leuchten": True, "weich": True, "geteilt": False}


@unittest.skipUnless(os.name == "nt", "Pseudokonsole nur unter Windows")
class FensterImLauf(unittest.TestCase):
    def test_zeilenbilder_leuchten_links_suche_und_geteilte_ansicht(self):
        from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["test"])
        programm = textwrap.dedent(f"""
            import ctypes, os, sys, time
            sys.path.insert(0, {str(R / "core")!r})
            import ki_terminal
            schluessel = os.environ["NEMICLI_STEUER_KEY"]
            def steuer(befehl, nr):
                ctypes.windll.kernel32.SetConsoleTitleW(f"nemicli-steuer:{{schluessel}}:{{befehl}}:{{nr}}")
            sys.stdout.write("\\x1b[48;2;12;13;20mSeite https://example.org/seite. Ende\\x1b[0m\\r\\n")
            sys.stdout.write("\\x1b[1;31mrot\\x1b[0m und \\x1b[44mblau\\x1b[0m\\r\\n")
            sys.stdout.flush()
            steuer("grund=0c0d14", 1)
            time.sleep(0.3)
            steuer("puls=1", 2)
            ki_terminal.start("Get-ChildItem", "C:/")
            ki_terminal.ausgabe("".join(f"eintrag {{i:03d}}\\n" for i in range(120)))
            ki_terminal.ende(0, 0.7)
            time.sleep(4)
        """)
        b = {}

        def pruefen():
            fenster = next(w for w in app.topLevelWidgets() if hasattr(w, "reiter"))
            haupt, ki = fenster.haupt, fenster.ki
            app.processEvents()
            b["grund"], b["puls"] = haupt.grund_programm, haupt.puls
            # Zeilenbilder: ein zweites Zeichnen ohne Änderung zeichnet keine Zeile neu
            haupt.puls = False
            haupt.repaint()
            laeufe = []
            echt = haupt._lauf
            haupt._lauf = lambda *a: (laeufe.append(1), echt(*a))
            haupt.repaint()
            b["laeufe_ohne_aenderung"] = len(laeufe)
            with haupt.lock:
                haupt.strom.feed("\x1b[5;1Hneu")
            haupt.repaint()
            b["laeufe_eine_zeile"] = len(laeufe)
            bild_gemerkt = haupt.grab().toImage()
            haupt.zeilen_bild.clear()
            haupt.zeilen_schluessel.clear()
            haupt.repaint()
            b["gleiches_bild"] = bild_gemerkt == haupt.grab().toImage()
            haupt._lauf = echt
            # Zellen in der Grundfarbe des Programms bleiben frei: nur „blau“ füllt
            zeile0 = haupt.zeilen_schluessel[0]
            b["grund_frei"] = (haupt._grund_frei(zeile0[0].bg), haupt._grund_frei("blue"))
            # Link unter der Maus und Strg+Klick
            punkt = QPointF(8 + 10 * haupt.zb, 8 + haupt.zh / 2)
            b["link"] = haupt._link_bei(punkt)
            with mock.patch.object(TT, "oeffnen") as oeffnen:
                QTest.mouseClick(haupt, Qt.LeftButton, Qt.ControlModifier, QPoint(int(punkt.x()), int(punkt.y())))
                b["geoeffnet"] = oeffnen.call_args[0] if oeffnen.called else None
            # Reiter „KI“: zurückrollen, suchen
            b["ki_zeilen"] = ki._oben() if ki is not None else 0
            ki.rollen(10 * ki.zh)
            for _ in range(60):
                ki._rollen_takt()
            b["roll"] = ki.roll / ki.zh
            ki.repaint()
            b["treffer"] = ki.suchen("EINTRAG 00")
            b["treffer_nr"] = ki.treffer_nr
            ki.such_sprung(-1)
            b["treffer_nr_danach"] = ki.treffer_nr
            ki.repaint()
            fenster.suche_oeffnen(ki)
            b["suchleiste"] = fenster.suchleiste.isVisibleTo(fenster)
            fenster.suchfeld.setText("eintrag 119")
            fenster._suche_starten()
            b["suchzahl"] = fenster.suchzahl.text()
            fenster.suche_schliessen()
            b["suche_zu"] = (fenster.suchleiste.isVisibleTo(fenster), ki.treffer)
            # Auswahl im zurückgerollten Verlauf kopiert die richtigen Zeilen
            with ki.lock:
                nummer = ki._versatz() + 40
                erwartet = "".join(ki._zellen(nummer)).rstrip()
            ki.auswahl = ((0, nummer), (ki.schirm.columns - 1, nummer))
            ki._kopieren()
            b["kopiert"] = (QApplication.clipboard().text(), erwartet)
            # Geteilte Ansicht
            fenster.teilen(True)
            b["geteilt"] = (ki in fenster.reiter, fenster.teiler.count(), fenster.teiler.indexOf(ki))
            fenster.teilen(False)
            b["zurueck"] = (ki in fenster.reiter, fenster.teiler.indexOf(ki))
            # Schalter von /terminal kommen als Steuerbefehl und wirken sofort
            for befehl in ("leuchten=0", "weich=0", "geteilt=1"):
                haupt._steuer_wert(*TF.steuer_wert(befehl))
            b["schalter"] = (fenster.leuchten, fenster.weich, fenster.geteilt, len(haupt.zeilen_bild))
            haupt.repaint()
            ki.rollen(5 * ki.zh)
            ki._rollen_takt()
            b["ohne_weich"] = ki.roll == ki.roll_ziel
            fenster.close()

        QTimer.singleShot(2500, pruefen)
        with mock.patch.object(TF, "_einstellungen", _standard):
            TF.starten([sys.executable, "-c", programm], titel="NemiCLI")
        self.assertEqual(b["grund"], "#0c0d14")
        self.assertTrue(b["puls"])
        self.assertEqual(b["laeufe_ohne_aenderung"], 0)
        self.assertEqual(b["laeufe_eine_zeile"], 1)
        self.assertTrue(b["gleiches_bild"])
        self.assertEqual(b["grund_frei"], (True, False))
        self.assertEqual(b["link"][3:], ("web", "https://example.org/seite"))
        self.assertEqual(b["geoeffnet"], ("web", "https://example.org/seite"))
        self.assertGreater(b["ki_zeilen"], 80)
        self.assertAlmostEqual(b["roll"], 10, delta=0.01)
        self.assertEqual(b["treffer"], 10)                    # eintrag 000 … 009
        self.assertEqual((b["treffer_nr"], b["treffer_nr_danach"]), (9, 8))
        self.assertTrue(b["suchleiste"])
        self.assertEqual(b["suchzahl"], "1 / 1")
        self.assertEqual(b["suche_zu"], (False, []))
        self.assertTrue(b["kopiert"][1].startswith("eintrag"))
        self.assertEqual(b["kopiert"][0], b["kopiert"][1])
        self.assertEqual(b["geteilt"], (False, 2, 1))
        self.assertEqual(b["zurueck"], (True, -1))
        self.assertEqual(b["schalter"], (False, False, True, 0))
        self.assertTrue(b["ohne_weich"])                      # ohne „weich“ springt das Rollen ans Ziel


class TerminalBefehl(unittest.TestCase):
    """/terminal: Schalter im Menü, auf der Einstellungsseite und als Steuerbefehl ans Fenster."""

    def _raum(self, gesendet):
        import main_quelle
        import ui
        quelle = main_quelle.text()
        raum = {"os": os, "ui": ui, "_fenster_steuern": gesendet.append}
        exec(quelle[quelle.index("# /terminal: Schalter -> "):quelle.index("def _schalter_an(")], raum)
        return raum

    def test_schalter_speichern_und_melden(self):
        import ui
        gesendet, gespeichert = [], {}
        raum = self._raum(gesendet)
        raum["config"] = mock.Mock(update=lambda **kw: gespeichert.update(kw), load=lambda: dict(gespeichert))
        with mock.patch.object(ui, "success"), mock.patch.object(ui, "info") as info:
            raum["terminal_befehl"]("leuchten aus")
            raum["terminal_befehl"]("geteilt an")
            raum["terminal_befehl"]("thema mint")
            raum["terminal_befehl"]("thema rosa")              # unbekannt: nur der Stand
            raum["terminal_befehl"]("leuchten=1; /exit")
        self.assertEqual(gespeichert, {"terminal_leuchten": False, "terminal_geteilt": True, "terminal_thema": "mint"})
        self.assertEqual(gesendet, ["leuchten=0", "geteilt=1"])   # die Farben gelten erst ab dem nächsten Start
        self.assertEqual(info.call_count, 4)
        for befehl in gesendet:
            self.assertIsNotNone(TF.steuer_wert(befehl))

    def test_menue_und_einstellungsseite(self):
        import commands
        import config
        import einstellseite
        werte = [z for z, _t, _f in commands.UNTERMENUES["/terminal"]]
        for schalter in ("leuchten", "weich", "geteilt"):
            self.assertIn(f"{schalter} an", werte)
            self.assertIn(f"{schalter} aus", werte)
        self.assertIn("/terminal", [c.name for c in commands.COMMAND_LIST])
        with mock.patch.object(config, "load", lambda: {"terminal_leuchten": False, "terminal_thema": "mint"}):
            seite = [e for e in einstellseite.seite() if e["befehl"] == "/terminal"]
            aktiv = {o["wert"] for e in seite for o in e["optionen"] if o["aktiv"]}
            self.assertEqual(aktiv, {"leuchten aus", "geteilt aus", "weich an", "thema mint"})
            self.assertEqual(einstellseite.befehl_fuer("/terminal", "geteilt an"), "/terminal geteilt an")
            self.assertIsNone(einstellseite.befehl_fuer("/terminal", "geteilt an; /exit"))


class _Ctx:
    backend = None
    model = "ollama_cloud:x"

    def sync_session(self):
        pass


class Vollbild(unittest.TestCase):
    """Suche, Antwort-Blöcke und Meldungen ans Fenster im Textual-Vollbild."""

    def setUp(self):
        import screen_tx
        self.kopiert = []
        echt = screen_tx._windows_clipboard
        screen_tx._windows_clipboard = self.kopiert.append
        self.addCleanup(lambda: setattr(screen_tx, "_windows_clipboard", echt))

    def _app(self, antworten=3, zeilen=12):
        import screen_tx
        import ui
        sc = screen_tx.Screen()
        sc.install()
        sc.ctx, sc.handler = _Ctx(), (lambda c, t: None)
        sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
        for a in range(antworten):
            ui.info(f"Frage {a}")
            ui.console.print(ui.assistant_panel("\n\n".join(f"Antwort {a} Absatz {z}" for z in range(zeilen))))
        app = screen_tx._NemiApp(sc)
        sc.app = app
        return sc, app

    def test_suche_markiert_und_rollt_zum_treffer(self):
        import screen_tx

        async def go():
            sc, app = self._app()
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    await pilot.press("ctrl+f")
                    await pilot.pause(0.1)
                    self.assertTrue(sc._suchzeile.display)
                    await pilot.press(*"absatz 3")
                    await pilot.pause(0.4)
                    self.assertEqual(len(sc._such_treffer), 3)
                    self.assertEqual(sc._such_nr, 2)                     # beginnt beim neuesten
                    self.assertEqual(app.screen.get_selected_text(), "Absatz 3")
                    self.assertIn("3 / 3", "".join(seg.text for strip in
                                                    app.screen._compositor.render_strips() for seg in strip))
                    await pilot.press("enter")                         # älterer Treffer
                    await pilot.pause(0.2)
                    self.assertEqual(sc._such_nr, 1)
                    block = sc._such_treffer[1][0]
                    self.assertIn(block, app.screen.selections)
                    self.assertFalse(sc._follow)
                    self.assertEqual(self.kopiert, [])                 # Suchen kopiert nichts
                    await pilot.press("escape")
                    await pilot.pause(0.1)
                    self.assertFalse(sc._suchzeile.display)
                    self.assertFalse(app.screen.selections)
                    self.assertIs(app.focused, sc._input)
                    self.assertEqual(screen_tx._treffer_in("aXa\nxA", "a"), [(0, 0, 1), (0, 2, 3), (1, 1, 2)])
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_rechtsklick_kopiert_den_block_und_titel_klappt_ein(self):
        import screen_tx

        async def go():
            sc, app = self._app(antworten=1, zeilen=4)
            try:
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause(0.3)
                    antwort = next(iter(app.query(screen_tx._AntwortBlock)))
                    await pilot.click(antwort, offset=(4, 2), button=3)
                    await pilot.pause(0.2)
                    self.assertEqual(len(self.kopiert), 1)
                    for z in range(4):
                        self.assertIn(f"Antwort 0 Absatz {z}", self.kopiert[0])
                    self.assertFalse(antwort.eingeklappt)
                    hoehe = antwort.size.height
                    await pilot.click(antwort, offset=(4, 2))          # Klick in den Text klappt nicht ein
                    await pilot.pause(0.2)
                    self.assertFalse(antwort.eingeklappt)
                    await pilot.click(antwort, offset=(4, antwort._titelzeile()))
                    await pilot.pause(0.2)
                    self.assertTrue(antwort.eingeklappt)
                    self.assertLess(antwort.size.height, hoehe)
                    self.assertIn(f"{hoehe} Zeilen", antwort.klartext())
                    await pilot.click(antwort, offset=(4, 1))
                    await pilot.pause(0.2)
                    self.assertFalse(antwort.eingeklappt)
                    self.assertEqual(antwort.size.height, hoehe)
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_alt_pfeil_springt_von_antwort_zu_antwort(self):
        import screen_tx

        async def go():
            sc, app = self._app(antworten=4, zeilen=14)
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    log = sc._log
                    lagen = [sc._verlauf.virtual_region.y + a.virtual_region.y + a._titelzeile()
                             for a in app.query(screen_tx._AntwortBlock)]
                    self.assertEqual(round(log.scroll_y), log.max_scroll_y)
                    await pilot.press("alt+up")
                    await pilot.pause(0.3)
                    self.assertEqual(round(log.scroll_y), lagen[-1])
                    self.assertFalse(sc._follow)
                    await pilot.press("alt+up")
                    await pilot.pause(0.3)
                    self.assertEqual(round(log.scroll_y), lagen[-2])
                    await pilot.press("alt+down")
                    await pilot.pause(0.3)
                    self.assertEqual(round(log.scroll_y), lagen[-1])
                    await pilot.press("alt+down")                      # keine weitere: ans Ende
                    await pilot.pause(0.3)
                    self.assertEqual(round(log.scroll_y), log.max_scroll_y)
                    self.assertTrue(sc._follow)
                    # Blättern in Schritten landet dort, wo der Sprung landete
                    seite = log.scrollable_content_region.height
                    await pilot.press("pageup")
                    await pilot.pause(0.3)
                    self.assertEqual(round(log.scroll_y), log.max_scroll_y - seite)
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_fenster_erfaehrt_grundfarbe_und_arbeit(self):
        import screen_tx
        import startseite
        befehle = []

        async def go():
            sc, app = self._app(antworten=1, zeilen=2)
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    self.assertEqual(befehle[:2], ["grund=0c0d14", "puls=0"])
                    sc.busy = True
                    sc._refresh_chrome()
                    sc.busy = False
                    sc._refresh_chrome()
                    sc._refresh_chrome()
                    self.assertEqual(befehle[2:], ["puls=1", "puls=0"])
                    app.exit()
            finally:
                sc.uninstall()
        with mock.patch.object(startseite, "STEUERN", befehle.append), \
                mock.patch.dict(os.environ, {"NEMICLI_FENSTER": "1"}):
            asyncio.run(go())
        self.assertEqual(screen_tx._SCHIRM_BG, "#0c0d14")


if __name__ == "__main__":
    unittest.main()
