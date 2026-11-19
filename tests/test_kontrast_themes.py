"""Lesbare Farben in allen Themes (WCAG AA), Theme tigra, Status der KI, Erinnerungen und Wache auf der Startseite."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import design        # noqa: E402
import kontrast      # noqa: E402
import startseite    # noqa: E402
import themes_eigen  # noqa: E402
import ui            # noqa: E402

TEXTROLLEN = ("brand", "brand.dim", "accent", "accent.dim", "leise", "assistant", "ok", "warn", "err")


def _zeichnen(r, breite: int) -> str:
    c = ui.console
    alt = c.width
    c.width = breite
    try:
        with c.capture() as cap:
            c.print(r)
        return cap.get()
    finally:
        c.width = alt


class Rechnung(unittest.TestCase):
    def test_kontrast_grenzen(self):
        self.assertAlmostEqual(kontrast.kontrast("#ffffff", "#000000"), 21.0, places=2)
        self.assertAlmostEqual(kontrast.kontrast("#123456", "#123456"), 1.0, places=5)

    def test_lesbar_hebt_nur_so_weit_wie_noetig(self):
        grund = "#0c0d14"
        hell = "#e4e7f2"
        self.assertEqual(kontrast.lesbar(hell, grund), hell)            # schafft es schon: bleibt
        neu = kontrast.lesbar("#5b54a8", grund)
        self.assertGreaterEqual(kontrast.kontrast(neu, kontrast.bezug(grund)), kontrast.MIN_TEXT)
        self.assertNotEqual(neu, "#ffffff")                             # Farbton bleibt, kein reines Weiß

    def test_unter_weiss(self):
        for farbe in ("#2a8f86", "#7cffb2", "#5b54a8"):
            self.assertGreaterEqual(kontrast.kontrast("#ffffff", kontrast.unter_weiss(farbe)), kontrast.MIN_TEXT)

    def test_bezug_heller_als_grund(self):
        self.assertGreater(kontrast.helligkeit(kontrast.bezug("#0c0d14")), kontrast.helligkeit("#0c0d14"))


class TerminalThemes(unittest.TestCase):
    def setUp(self):
        self.alt = ui.current_theme()
        self.addCleanup(lambda: ui.set_theme(self.alt))

    def test_jede_textfarbe_schafft_aa_auf_durchscheinendem_grund(self):
        for name in ui.THEMES:
            hinten = kontrast.bezug(ui._grund(name))
            for rolle, farbe in ui._farben(name).items():
                with self.subTest(theme=name, rolle=rolle):
                    self.assertGreaterEqual(kontrast.kontrast(farbe, hinten), kontrast.MIN_TEXT)
            self.assertEqual(set(ui._farben(name)), set(TEXTROLLEN))

    def test_eigenes_theme_wird_auch_lesbar(self):
        _, palette = themes_eigen.bauen("probe", "Probe", "#9d8cff", "#41c9b8")
        with patch.dict(ui.THEMES, {"probe": palette}):
            hinten = kontrast.bezug(ui._grund("probe"))
            for farbe in ui._farben("probe").values():
                self.assertGreaterEqual(kontrast.kontrast(farbe, hinten), kontrast.MIN_TEXT)

    def test_rahmen_bleiben_gedaempft_text_wird_lesbar(self):
        for name in ui.THEMES:
            ui.set_theme(name)
            p = ui.THEMES[name]
            stil = ui.console.get_style
            with self.subTest(theme=name):
                self.assertEqual(stil("brand.rand").color.triplet.hex, p["brand_dim"])
                self.assertEqual(stil("accent.rand").color.triplet.hex, p["accent_dim"])
                self.assertEqual(stil("muted").color.triplet.hex, ui._farben(name)["leise"])
                self.assertEqual(ui.leise(), ui._farben(name)["leise"])

    def test_tigra_ueberall(self):
        self.assertIn("tigra", ui.THEMES)
        self.assertIn("tigra", design.PALETTEN)
        self.assertIn("tigra", ui.EINGEBAUT)
        self.assertTrue(ui.set_theme("tigra"))
        self.assertEqual(ui.code_theme_name(), "dracula")
        with patch("config.load", return_value={"theme": "tigra"}):
            self.assertEqual(design.palette_name(), "tigra")

    def test_eigenes_theme_darf_tigra_nicht_ueberschreiben(self):
        with self.assertRaises(ValueError):
            themes_eigen.bauen("tigra", "x", "#9d8cff", "#41c9b8", eingebaut=ui.EINGEBAUT)

    def test_fester_leiser_ton(self):
        self.assertEqual(ui.LEISE, kontrast.lesbar(ui.AUS, "#0c0d14"))


class FensterPaletten(unittest.TestCase):
    def test_gleiche_rollen_wie_klassik(self):
        for name, p in design.PALETTEN.items():
            with self.subTest(palette=name):
                self.assertEqual(set(p), set(design.PALETTEN["klassik"]))

    def test_text_dim_blass_lesbar_und_rangfolge(self):
        for name, p in design.PALETTEN.items():
            for rolle in ("text", "dim", "blass"):            # Textstufen; cyan/lila sind Akzentflächen
                with self.subTest(palette=name, rolle=rolle):
                    self.assertGreaterEqual(kontrast.kontrast(p[rolle], p["flaeche_hell"]), kontrast.MIN_TEXT)
            with self.subTest(palette=name, rangfolge=True):
                h = kontrast.helligkeit
                self.assertGreater(h(p["text"]), h(p["dim"]))
                self.assertGreater(h(p["dim"]), h(p["blass"]))


class Status(unittest.TestCase):
    def setUp(self):
        self.alt = dict(startseite.STAND)
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(self.alt)))
        p = patch.object(startseite, "auffrischen")
        self.auffrischen = p.start()
        self.addCleanup(p.stop)

    def test_zug_denkt_prueft_arbeitet_und_zurueck(self):
        startseite.setzen(status="bereit")
        for art in ("denkt", "prueft", "arbeitet", "denkt"):
            startseite.taetig(art)
            self.assertEqual(startseite.STAND["status"], art)
        startseite.taetig(None)
        self.assertEqual(startseite.STAND["status"], "bereit")

    def test_ohne_modell_bleibt_ohne(self):
        startseite.setzen(status="ohne")
        startseite.taetig("denkt")
        startseite.taetig(None)
        self.assertEqual(startseite.STAND["status"], "ohne")

    def test_nur_bei_wechsel_neu_zeichnen(self):
        startseite.setzen(status="bereit")
        self.auffrischen.reset_mock()
        startseite.taetig("denkt")
        startseite.taetig("denkt")
        self.assertEqual(self.auffrischen.call_count, 1)

    def test_status_mit_wort_nicht_nur_farbe(self):
        for art in startseite.TAETIG | {"bereit", "ohne", "laedt"}:
            startseite.STAND["status"] = art
            zeichen, _, wort = startseite.STATUS[art]
            text = startseite._status_text().plain
            self.assertIn(wort, text)
            self.assertIn(zeichen, text)


class Karten(unittest.TestCase):
    def setUp(self):
        self.alt = dict(startseite.STAND)
        self.addCleanup(lambda: (startseite.STAND.clear(), startseite.STAND.update(self.alt)))
        self.theme = ui.current_theme()
        self.addCleanup(lambda: ui.set_theme(self.theme))

    def _erinnerungen(self, name):
        from wache import erinnerungen as ER
        daten = [
            {"id": "a1", "zeit": 4_102_444_800.0, "text": "Wäsche aufhängen", "fuer": ER.FUER_NUTZER, "wer": ""},
            {"id": "b2", "zeit": 4_102_448_400.0, "text": "Seite nachsehen", "fuer": ER.FUER_KI, "wer": "PersonA"},
            {"id": "c3", "zeit": 4_102_452_000.0, "text": "Bild malen", "fuer": ER.FUER_KI, "wer": "PersonB",
             "alle": 3600},
        ]
        startseite.STAND["name"] = name
        with patch.object(ER, "offen", return_value=daten):
            return startseite.erinnerungen_zeilen()

    def test_erinnerungen_je_persoenlichkeit(self):
        a = "\n".join(self._erinnerungen("PersonA"))
        self.assertIn("Wäsche aufhängen", a)
        self.assertIn("Seite nachsehen", a)
        self.assertNotIn("Bild malen", a)
        b = "\n".join(self._erinnerungen("PersonB"))
        self.assertIn("Bild malen", b)
        self.assertIn("🔁", b)
        self.assertNotIn("Seite nachsehen", b)

    def test_erinnerungen_gekappt(self):
        from wache import erinnerungen as ER
        viele = [{"id": str(i), "zeit": 4_102_444_800.0 + i, "text": f"Punkt {i}", "fuer": ER.FUER_NUTZER}
                 for i in range(6)]
        with patch.object(ER, "offen", return_value=viele):
            zeilen = startseite.erinnerungen_zeilen()
        self.assertEqual(len(zeilen), startseite.ERINNERUNGEN_MAX + 1)
        self.assertIn("3 weitere", zeilen[-1])

    def test_wache_alarm_bleibt_deutlich(self):
        rot = startseite._wache_karte("🔴 2 echte Alarme  ·  /wache")
        self.assertEqual(rot.border_style, "err")
        gelb = startseite._wache_karte("🟡 4 Alarme offen  ·  /wache")
        self.assertEqual(gelb.border_style, "warn")
        ruhig = startseite._wache_karte("🟢 alles ruhig")
        self.assertEqual(ruhig.border_style, "brand.rand")

    def test_kacheln_weiss_lesbar(self):
        for name in ui.THEMES:
            ui.set_theme(name)
            for seg in startseite._kacheln().spans:
                stil = seg.style
                if getattr(stil, "bgcolor", None) is not None and stil.color is not None:
                    with self.subTest(theme=name):
                        self.assertGreaterEqual(kontrast.kontrast(stil.color.triplet.hex, stil.bgcolor.triplet.hex),
                                                kontrast.MIN_TEXT)

    def test_herz_nur_bei_tigra(self):
        startseite.STAND.update(name="PersonA", bild=None, status="bereit", gruss="", wache=None, offen=[])
        ui.set_theme("tigra")
        self.assertIn("♥", _zeichnen(startseite.leiste(80), 80))
        ui.set_theme("cyan")
        self.assertNotIn("♥", _zeichnen(startseite.leiste(80), 80))

    def test_startseite_jede_breite(self):
        startseite.STAND.update(name="PersonA", bild=None, status="denkt", gruss="Hallo", wache="🟡 1 Alarm offen",
                                offen=["🎨 2 neue Bilder"], letzter={"titel": "Probe", "wann": "gestern"},
                                erinnerungen=["⏰ heute 18:00  Probe"], liebling=None)
        for name in ("tigra", "cyan"):
            ui.set_theme(name)
            for breite in (30, 50, 72, 120):
                with self.subTest(theme=name, breite=breite):
                    text = _zeichnen(startseite.renderable(ui._banner_kompakt(), breite), breite)
                    self.assertIn("denkt", text)
                    self.assertIn("Erinnerungen", text)
                    self.assertTrue(all(len(z) <= breite for z in text.splitlines()))


class Glanz(unittest.TestCase):
    def test_verlauf(self):
        import terminal_fenster as TF
        self.assertEqual(TF.glanz_staerke(-0.1), 0.0)
        self.assertEqual(TF.glanz_staerke(0.0), 0.0)
        self.assertAlmostEqual(TF.glanz_staerke(TF.GLANZ_S / 2), 1.0, places=5)
        self.assertEqual(TF.glanz_staerke(TF.GLANZ_S), 0.0)
        self.assertEqual(TF.glanz_staerke(5.0), 0.0)


class Hilfe(unittest.TestCase):
    def test_theme_hilfe_kennt_tigra(self):
        import commands
        text = commands.hilfe_fuer_ki("/theme")
        self.assertIn("tigra", text)
        self.assertIn("4,5", text)


if __name__ == "__main__":
    unittest.main()
