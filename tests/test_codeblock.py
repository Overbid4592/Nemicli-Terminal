"""Offline-Tests für die Code-Darstellung im Chat (ui.NemiCodeBlock).

Code soll im Gespräch sofort als Code erkennbar sein: Kopfzeile mit
Dateiname oder Sprache, Zeilennummern ab einer gewissen Länge, Rahmen in
der Theme-Farbe.

python -m unittest discover -s tests -p test_codeblock.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from rich.markdown import Markdown    # noqa: E402

import ui                             # noqa: E402


def rendere(text: str, width: int = 70) -> str:
    """Antwort-Panel in Text rendern, wie es im Terminal aussähe.

    Über NemiCLIs eigene Konsole – eine frische kennt die Theme-Farben
    ('accent', 'brand') nicht und würde beim Rahmen aussteigen."""
    alt = ui.console.width
    try:
        ui.console.width = width
        with ui.console.capture() as gefangen:
            ui.console.print(ui.assistant_panel(text))
        return gefangen.get()
    finally:
        ui.console.width = alt


class Zaunkopf(unittest.TestCase):
    """Aus ```python main.py wird Lexer + Titel + Sprachname."""

    def test_nur_sprache(self):
        self.assertEqual(ui._code_kopf("python"), ("python", "Python", "Python"))

    def test_sprache_mit_dateiname(self):
        self.assertEqual(ui._code_kopf("python main.py"),
                         ("python", "main.py", "Python"))

    def test_doppelpunkt_schreibweise(self):
        self.assertEqual(ui._code_kopf("python:main.py"),
                         ("python", "main.py", "Python"))

    def test_leer_wird_text(self):
        self.assertEqual(ui._code_kopf(""), ("text", "Text", "Text"))
        self.assertEqual(ui._code_kopf(None), ("text", "Text", "Text"))

    def test_unbekannte_sprache_wird_gross_geschrieben(self):
        lexer, titel, sprache = ui._code_kopf("brainfuck")
        self.assertEqual(lexer, "brainfuck")
        self.assertEqual(titel, "Brainfuck")
        self.assertEqual(sprache, "Brainfuck")   # Titel == Sprache -> kein Untertitel

    def test_kuerzel_werden_ausgeschrieben(self):
        for kurz, lang in (("py", "Python"), ("js", "JavaScript"),
                           ("ps1", "PowerShell"), ("yml", "YAML")):
            self.assertEqual(ui._code_kopf(kurz)[2], lang, kurz)

    def test_grossschreibung_ist_egal(self):
        self.assertEqual(ui._code_kopf("PYTHON")[2], "Python")


class Darstellung(unittest.TestCase):
    def test_dateiname_steht_im_kopf(self):
        aus = rendere("```python run.py\nx = 1\n```")
        self.assertIn("run.py", aus)
        self.assertIn("Python", aus)      # Sprache als Untertitel

    def test_ohne_dateiname_steht_die_sprache_nur_einmal(self):
        aus = rendere("```python\nx = 1\n```")
        self.assertEqual(aus.count("Python"), 1)

    def test_kurzer_block_ohne_zeilennummern(self):
        aus = rendere("```python\nx = 1\n```")
        self.assertNotIn(" 1 x = 1", aus)

    def test_langer_block_mit_zeilennummern(self):
        code = "\n".join(f"zeile_{i} = {i}" for i in range(1, 7))
        aus = rendere(f"```python\n{code}\n```")
        for nummer in ("1", "6"):
            self.assertIn(nummer, aus)
        self.assertIn("zeile_6", aus)

    def test_die_grenze_liegt_bei_vier_zeilen(self):
        self.assertEqual(ui._LINE_NUMBER_FROM, 4)

    def test_code_bleibt_vollstaendig(self):
        aus = rendere("```python\nimport sys\nprint(sys.version)\n```", width=90)
        self.assertIn("import sys", aus)
        self.assertIn("print(sys.version)", aus)

    def test_fliesstext_bleibt_fliesstext(self):
        aus = rendere("Das ist ein Satz.\n\n```python\nx = 1\n```")
        self.assertIn("Das ist ein Satz.", aus)

    def test_eingerueckter_block_geht_auch(self):
        aus = rendere("Absatz:\n\n    eingerueckt = True\n")
        self.assertIn("eingerueckt", aus)
        self.assertIn("Text", aus)


class ThemeFarben(unittest.TestCase):
    def tearDown(self):
        ui.set_theme("cyan")

    def test_jedes_theme_hat_einen_syntax_stil(self):
        from pygments.styles import get_all_styles
        vorhanden = set(get_all_styles())
        for theme in ui.THEMES:
            ui.set_theme(theme)
            stil = ui.code_theme_name()
            self.assertIn(stil, vorhanden, f"{theme} -> {stil} gibt es nicht")

    def test_unbekanntes_theme_faellt_weich_zurueck(self):
        alt = ui._active
        try:
            ui._active = "gibtsnicht"
            self.assertEqual(ui.code_theme_name(), "one-dark")
        finally:
            ui._active = alt


class RichBleibtUnangetastet(unittest.TestCase):
    """Wir ersetzen den Code-Block nur für UNS, nicht global in Rich."""

    def test_standard_markdown_behaelt_seinen_codeblock(self):
        from rich.markdown import CodeBlock
        self.assertIs(Markdown.elements["fence"], CodeBlock)
        self.assertIs(ui.NemiMarkdown.elements["fence"], ui.NemiCodeBlock)

    def test_nemimarkdown_ist_ein_markdown(self):
        self.assertTrue(issubclass(ui.NemiMarkdown, Markdown))

    def test_die_antwort_benutzt_unser_markdown(self):
        panel = ui.assistant_panel("Text")
        self.assertIsInstance(panel.renderable, ui.NemiMarkdown)

    def test_tui_baut_beim_umbauen_dieselbe_klasse_neu(self):
        # screen_tx rendert Markdown bei Fensterwechsel neu. Wird dort hart
        # Markdown(...) gebaut, verliert die Antwort ihre Code-Rahmen.
        code = (ROOT / "ui" / "screen_tx.py").read_text(encoding="utf-8")
        self.assertIn("type(renderable)(src, code_theme=renderable.code_theme",
                      code)


if __name__ == "__main__":
    unittest.main()
