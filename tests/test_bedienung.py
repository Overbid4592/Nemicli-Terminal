"""Einfachere Bedienung: Befehle nach Themen, ⚙ im Terminal, Schriftgröße je Rolle, Startart, Kacheln, Design."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import commands as C                                   # noqa: E402
import config                                          # noqa: E402
import design                                          # noqa: E402
import einstellseite                                   # noqa: E402
import persona                                         # noqa: E402
import startseite                                      # noqa: E402


class Themen(unittest.TestCase):
    def test_jeder_befehl_hat_genau_ein_thema(self):
        namen = [n for _, _, befehle in C.THEMEN for n in befehle]
        self.assertEqual(len(namen), len(set(namen)), "ein Befehl steht in zwei Themen")
        alle = {c.name for c in C.COMMAND_LIST}
        self.assertEqual(set(namen) - alle, set(), "Thema nennt einen Befehl, den es nicht gibt")
        self.assertEqual(alle - set(namen) - C.VERSTECKT, set(), "Befehl ohne Thema – in THEMEN eintragen")

    def test_slash_menue_nach_themen(self):
        liste = C.find_commands("/")
        self.assertEqual(liste[0][0], "/help")
        self.assertTrue(liste[0][1].startswith("💬 "))
        self.assertNotIn("/codeend", [n for n, _ in liste])
        self.assertEqual(C.find_commands("/codeend")[0][0], "/codeend")      # getippt weiter auffindbar

    def test_hilfe_gruppen(self):
        gruppen = C.help_gruppen()
        self.assertEqual(len(gruppen), 6)
        self.assertEqual(gruppen[0][0], "💬 Chat & Bedienung")
        self.assertIn("/einstellungen", [u.split()[0] for u, _ in gruppen[0][1]])


class Einstellungen(unittest.TestCase):
    def _seite(self, werte):
        design._stand.clear()
        with mock.patch.object(config, "load", return_value=werte),                 mock.patch.object(C, "AUSWAHL_JETZT", lambda cmd: []),                 mock.patch("stimme.modus", return_value="aus"):
            return [e for e in einstellseite.seite()]

    def test_schrift_und_startart_auf_der_seite(self):
        s = self._seite({"schriftgroesse": {"ki": 18}, "startart": "fenster"})
        schrift = {e["titel"]: e for e in s if e["befehl"] == "/schriftgröße"}
        ki = schrift["🔠 Schrift: Antworten der KI"]
        self.assertEqual([o["wert"] for o in ki["optionen"] if o["aktiv"]], ["ki 18"])
        oben = schrift["🔠 Schrift: ganze Oberfläche"]
        self.assertEqual([o["wert"] for o in oben["optionen"] if o["aktiv"]], ["oberflaeche 14"])
        start = next(e for e in s if e["befehl"] == "/startart")
        self.assertEqual([o["wert"] for o in start["optionen"] if o["aktiv"]], ["fenster"])
        design._stand.clear()

    def test_nur_angebotene_werte(self):
        design._stand.clear()
        with mock.patch.object(config, "load", return_value={}),                 mock.patch.object(C, "AUSWAHL_JETZT", lambda cmd: []),                 mock.patch("stimme.modus", return_value="aus"):
            self.assertEqual(einstellseite.befehl_fuer("/startart", "fenster"), "/startart fenster")
            self.assertEqual(einstellseite.befehl_fuer("/schriftgröße", "ki 18"), "/schriftgröße ki 18")
            self.assertIsNone(einstellseite.befehl_fuer("/startart", "fenster; /exit"))
            self.assertIsNone(einstellseite.befehl_fuer("/schriftgröße", "ki 999"))
        design._stand.clear()


class Schriftgroesse(unittest.TestCase):
    def setUp(self):
        design._stand.clear()
        self.addCleanup(design._stand.clear)

    def test_rollen_einzeln(self):
        with mock.patch.object(config, "load", return_value={"schriftgroesse": {"ki": 20, "fenster": 21}}):
            self.assertEqual(design.px("ki"), 20)
            self.assertEqual(design.px("du"), 15)                 # Vorgabe
            self.assertAlmostEqual(design.groesse(10), 15.0)      # Oberfläche 21 statt 14 px
            self.assertEqual(design.css_px(14), 21)

    def test_grenzen_und_unsinn(self):
        with mock.patch.object(config, "load", return_value={"schriftgroesse": {"ki": 500, "du": "x"}}):
            self.assertEqual(design.px("ki"), design.GROESSTE)
            self.assertEqual(design.px("du"), 15)

    def test_kein_besonderer_hinweis_an_die_ki(self):
        self.assertFalse(hasattr(persona, "_lesbar_hinweis"))

    def test_terminal_punkt(self):
        import terminal_fenster as T
        self.assertEqual(T._terminal_punkt({}), 11)
        self.assertEqual(T._terminal_punkt({"schriftgroesse": {"terminal": 20}}), 15)


class Startseite(unittest.TestCase):
    def test_kacheln_sind_echte_befehle(self):
        self.assertLessEqual(startseite.KACHEL_BEFEHLE, set(C.COMMANDS))

    def test_kacheln_klickbar(self):
        t = startseite._kacheln()
        klicks = {s.style.meta.get("@click") for s in t.spans if s.style and getattr(s.style, "meta", None)}
        self.assertIn("app.kachel('/einstellungen')", klicks)


class Themes(unittest.TestCase):
    def test_paletten_vollstaendig_und_im_terminal(self):
        import ui
        rollen = set(design.PALETTEN["klassik"])
        for name, palette in design.PALETTEN.items():
            self.assertEqual(set(palette), rollen, name)
            if name != "klassik":
                self.assertIn(name, ui.THEMES)                       # /theme <name> färbt auch die Fenster

    def test_theme_waehlt_die_palette(self):
        with mock.patch.object(config, "load", return_value={"theme": "pastell"}):
            self.assertEqual(design.palette_name(), "pastell")
        with mock.patch.object(config, "load", return_value={"theme": "matrix"}):
            self.assertEqual(design.palette_name(), "klassik")       # reines Terminal-Theme


class Design(unittest.TestCase):
    def test_wache_und_store_nehmen_das_gemeinsame_design(self):
        from wache import oberflaeche as O
        self.assertEqual(O.THEME["nacht"], design.FARBE["grund"])
        self.assertEqual(O.THEME["cyan"], design.FARBE["cyan"])
        import gui_fenster  # noqa: F401  (PySide6 nötig)
        self.assertIs(gui_fenster.FARBE, design.FARBE)


if __name__ == "__main__":
    unittest.main()
