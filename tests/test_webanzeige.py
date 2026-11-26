"""Anzeige der Web-Werkzeuge: Daten (webanzeige), Text fürs Modell unverändert, Terminal-Karten."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from rich.console import Console   # noqa: E402

import actions                     # noqa: E402
import fachquellen as F            # noqa: E402
import ui                          # noqa: E402
import webanzeige as WA            # noqa: E402
import webfetch as W               # noqa: E402

_VERDACHT = tempfile.TemporaryDirectory()
TREFFER = [
    {"titel": "Treiber 1.2 – Release Notes", "url": "https://www.example.com/a", "text": "Neu in 1.2: schneller.", "notiz": "Hersteller",
     "tier": 1},
    {"titel": "Forum", "url": "https://forum.example.org/t/9", "text": "Seit dem Update …", "tier": W.FREMD_TIER},
    {"titel": "", "url": "https://shop.example.net/x", "text": "", "tier": None},
]


def setUpModule():
    p = mock.patch.object(W, "_verdaechtig_pfad", lambda: Path(_VERDACHT.name) / W.VERDAECHTIG_DATEI)
    p.start()
    unittest.addModuleCleanup(p.stop)
    unittest.addModuleCleanup(_VERDACHT.cleanup)


def _text(renderable, breite: int = 110) -> str:
    c = Console(theme=ui._build_theme(ui._active), width=breite, record=True, color_system=None)
    if isinstance(renderable, ui.Klapp):
        renderable = renderable.lang
    c.print(renderable)
    return c.export_text()


class Daten(unittest.TestCase):
    def test_sauber_nimmt_steuerzeichen_und_unsichtbares(self):
        roh = "Titel" + chr(0x1B) + "[31m rot" + chr(0x200B) + " und\nweiter" + chr(0x202E) + "x"
        s = WA.sauber(roh, 200)
        for z in (chr(0x1B), chr(0x200B), chr(0x202E), "\n"):
            self.assertNotIn(z, s)
        self.assertTrue(s.startswith("Titel"))

    def test_sauber_kuerzt_an_wortgrenze(self):
        s = WA.sauber("eins zwei drei vier fünf sechs sieben acht", 20)
        self.assertLessEqual(len(s), 20)
        self.assertTrue(s.endswith("…"))

    def test_host_und_ampel(self):
        self.assertEqual(WA.host("https://www.Example.com/pfad?x=1"), "example.com")
        self.assertEqual(WA.host("kaputt"), "?")
        self.assertEqual([WA.ampel(1), WA.ampel(W.FREMD_TIER), WA.ampel(None), WA.ampel(1, warn=True)],
                         ["gruen", "gelb", "grau", "warn"])

    def test_suche(self):
        d = WA.suche("treiber", TREFFER, warn=lambda u: "forum" in u)
        self.assertEqual((d["gesamt"], d["vertraut"]), (3, 1))
        self.assertEqual([z["ampel"] for z in d["treffer"]], ["gruen", "warn", "grau"])
        self.assertEqual(d["treffer"][2]["titel"], "shop.example.net")     # ohne Titel: die Domain
        self.assertEqual(WA.kopf(d), "3 Treffer · 1 vertrauenswürdig")
        self.assertIn("nur Schnipsel", WA.legende(d))
        self.assertEqual(WA.legende(WA.suche("x", TREFFER[:1])), "")       # nur grün: keine Legende

    def test_lesen(self):
        d = WA.lesen("https://www.example.com/a", "\n\n  Überschrift der Seite\n" + "wort " * 4200, 1, 1, 3)
        self.assertEqual(d["titel"], "Überschrift der Seite")
        self.assertEqual(WA.lese_zeile(d), "example.com · Teil 1 von 3 · ≈ 4.203 Wörter")

    def test_laeuft(self):
        self.assertEqual(WA.laeuft({"tool": "web_suche", "suche": "rtx"})["ziel"], "„rtx“")
        self.assertEqual(WA.laeuft({"tool": "web_lesen", "url": "https://www.example.com/x"})["ziel"], "example.com")
        self.assertIn("arXiv", WA.laeuft({"tool": "fach_suche", "quelle": "arxiv", "suche": "q"})["verb"])
        self.assertNotEqual(WA.radar(0.0), WA.radar(0.2))

    def test_schritte(self):
        zustand = lambda **k: [z for _n, z, _c in WA.schritt_zustand(WA.recherche_stand(**k))]  # noqa: E731
        self.assertEqual(zustand(phase="plan"), ["laeuft", "offen", "offen", "offen"])
        self.assertEqual(zustand(phase="suchen", teilfragen=3), ["fertig", "laeuft", "offen", "offen"])
        self.assertEqual(zustand(phase="suchen", teilfragen=3, gelesen=1), ["fertig", "laeuft", "laeuft", "offen"])
        self.assertEqual(zustand(phase="bericht"), ["fertig", "fertig", "fertig", "laeuft"])
        self.assertEqual(zustand(phase="fertig"), ["fertig"] * 4)
        zaehler = [c for _n, _z, c in WA.schritt_zustand(WA.recherche_stand("suchen", 5, 3, 2, 4))]
        self.assertEqual(zaehler, ["", "3/5", "2/4", ""])


class TextFuersModell(unittest.TestCase):
    """Die Anzeige kommt dazu – der Text, den die KI bekommt, bleibt genau gleich."""

    def test_suche(self):
        with mock.patch.object(W, "treffer", return_value=TREFFER):
            text, anzeige = W.search_ergebnis("treiber")
            nochmal = W.search("treiber")
        self.assertEqual(_ohne_kennung(text), _ohne_kennung(nochmal))
        self.assertEqual(anzeige["gesamt"], 3)

    def test_suche_fehler_ohne_anzeige(self):
        with mock.patch.object(W, "treffer", return_value="❌ kein Schlüssel"):
            self.assertEqual(W.search_ergebnis("x"), ("❌ kein Schlüssel", None))

    def test_lesen(self):
        geladen = ("https://example.com/a  ·  Tier 1", "Kopf\n\n" + ("Absatz " * 400 + "\n\n") * 30, 1)
        with mock.patch.object(W, "text_holen", return_value=geladen):
            text, anzeige = W.fetch_ergebnis("https://example.com/a", 2)
            alt = W.fetch("https://example.com/a", 2)
            ganz, anz_ganz = W.fetch_ergebnis("https://example.com/a", ganz=True)
        self.assertEqual(_ohne_kennung(text), _ohne_kennung(alt))
        self.assertEqual((anzeige["teil"], anzeige["host"], anzeige["titel"]), (2, "example.com", "Kopf"))
        self.assertGreater(anzeige["teile"], 1)
        self.assertTrue(anz_ganz["ganz"])
        self.assertIn("in einem Zug", WA.lese_zeile(anz_ganz))

    def test_lesen_fehler(self):
        with mock.patch.object(W, "text_holen", return_value="❌ gesperrt"):
            self.assertEqual(W.fetch_ergebnis("https://x.example"), ("❌ gesperrt", None))

    def test_fachsuche(self):
        fach = [{**t, "info": "2026 · Autor"} for t in TREFFER[:2]]
        with mock.patch.object(F, "treffer", return_value=fach):
            text, anzeige = F.suchen_ergebnis("arxiv", "q")
            alt = F.suchen("arxiv", "q")
        self.assertEqual(_ohne_kennung(text), _ohne_kennung(alt))
        self.assertEqual(anzeige["quelle"], "arXiv")
        self.assertTrue(WA.kopf(anzeige).startswith("arXiv · 2 Treffer"))

    def test_werkzeug_traegt_anzeige(self):
        with mock.patch.object(W, "treffer", return_value=TREFFER):
            res = actions._web_suche({"suche": "treiber"})
        self.assertIsNone(res.ok)
        self.assertEqual(res.anzeige["gesamt"], 3)
        self.assertNotIn("anzeige", res.text)


def _ohne_kennung(text: str) -> str:
    """Die Rahmenlinien tragen je Aufruf eine neue Zufallskennung."""
    import re
    return re.sub(r"\b[0-9a-f]{6}\b", "K", text)


class Terminal(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ui, "TUI", None)
        p.start()
        self.addCleanup(p.stop)

    def test_karte(self):
        d = WA.suche("treiber", TREFFER)
        out = _text(ui.web_karte(d, 1.84))
        for teil in ("3 Treffer · 1 vertrauenswürdig", "Treiber 1.2 – Release Notes", "example.com",
                     "unbekannte Seite", "„treiber“", "1,8 s", "nur Schnipsel"):
            self.assertIn(teil, out)

    def test_markup_aus_dem_netz_bleibt_text(self):
        d = WA.suche("x", [{"titel": "[bold red]Hallo[/bold red]", "url": "https://a.example", "text": "", "tier": 1}])
        self.assertIn("[bold red]Hallo[/bold red]", _text(ui.web_karte(d)))

    def test_keine_treffer(self):
        self.assertIn("keine Treffer", _text(ui.web_karte(WA.suche("nichts", []))))

    def test_jeder_stil_ist_gueltig_in_jedem_theme(self):
        """Rich übergeht unbekannte Stile in Textstücken still; das Vollbild prüft streng. Hier streng wie dort."""
        original = Console.get_style

        def streng(self, name, *, default=None):
            return original(self, name)                 # ohne Ersatz: ein ungültiger Name wirft
        d = WA.suche("x", TREFFER, warn=lambda u: "forum" in u)
        teile = [ui.web_laeuft(WA.laeuft({"tool": "web_suche", "suche": "x"}), 1.0), ui.web_karte(d, 1.0),
                 ui.web_karte(WA.suche("x", [])),
                 ui.lese_karte(WA.lesen("https://a.example", "Kopf\ntext", W.FREMD_TIER), 0.5),
                 ui.lese_karte(WA.lesen("https://a.example", "Kopf\ntext", 1, warn=True))]
        teile += [ui.recherche_leiste(WA.recherche_stand(p, 3, 1, 1, 3), 5.0)
                  for p in ("plan", "suchen", "pruefen", "bericht", "fertig")]
        with mock.patch.object(Console, "get_style", streng):
            for theme in ui.THEMES:
                c = Console(theme=ui._build_theme(theme), width=100, record=True)
                for teil in teile:
                    with self.subTest(theme=theme):
                        c.print(teil)

    def test_lese_karte(self):
        d = WA.lesen("https://www.example.com/a", "Überschrift\n" + "wort " * 10, W.FREMD_TIER)
        out = _text(ui.web_ergebnis(d, 0.8))
        for teil in ("gelesen", "example.com", "unbekannte Seite", "„Überschrift“", "0,8 s"):
            self.assertIn(teil, out)

    def test_laufende_zeile(self):
        out = _text(ui.web_laeuft(WA.laeuft({"tool": "web_suche", "suche": "rtx"}), 2.1))
        self.assertIn("sucht im Netz", out)
        self.assertIn("„rtx“", out)
        self.assertIn("2,1 s", out)

    def test_recherche_leiste(self):
        out = _text(ui.recherche_leiste(WA.recherche_stand("suchen", 5, 3, 2, 4), 62))
        for teil in ("Recherche", "Plan", "Suchen 3/5", "Lesen 2/4", "Bericht", "1m 02s"):
            self.assertIn(teil, out)
        fertig = _text(ui.recherche_leiste(WA.recherche_stand("fertig", 5, 5, 4, 4, quellen=3), 90))
        self.assertIn("3 Quellen", fertig)


if __name__ == "__main__":
    unittest.main()
