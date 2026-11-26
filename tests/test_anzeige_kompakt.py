"""Kompakte Werkzeug-Anzeige: „● Name(Ziel)“, „⎿ Ergebnis“, Diff mit Zeilennummern."""

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from rich.console import Console   # noqa: E402
from rich.panel import Panel       # noqa: E402

import ui   # noqa: E402


def _text(item, breite: int = 100) -> str:
    out = io.StringIO()
    Console(file=out, width=breite, theme=ui._build_theme("cyan"), color_system=None).print(item)
    return out.getvalue()


class _Basis(unittest.TestCase):
    def setUp(self):
        ui._anzeige = "kompakt"
        ui.TUI = None

    def tearDown(self):
        ui._anzeige = None
        ui.TUI = None


class WerkzeugZeile(_Basis):
    def test_befehl_pfad_und_adresse(self):
        self.assertEqual("● Befehl(git status)", ui.werkzeug_zeile({"tool": "befehl", "befehl": "git status"}).plain)
        pfad = os.path.join(os.getcwd(), "core", "modes.py")
        self.assertEqual(f"● Lesen({os.path.join('core', 'modes.py')})",
                         ui.werkzeug_zeile({"tool": "datei_lesen", "pfad": pfad}).plain)
        self.assertEqual("● Web(https://example.org/a)",
                         ui.werkzeug_zeile({"tool": "web_lesen", "url": "https://example.org/a"}).plain)
        self.assertEqual("● unbekannt_neu", ui.werkzeug_zeile({"tool": "unbekannt_neu"}).plain)

    def test_verschieben_zeigt_beide_enden(self):
        z = ui.werkzeug_zeile({"tool": "verschieben", "von": "a.txt", "nach": "b.txt"}).plain
        self.assertEqual("● Verschieben(a.txt → b.txt)", z)

    def test_ohne_aktion_erste_zeile(self):
        self.assertEqual("● Helfer losschicken:", ui.werkzeug_zeile(None, "Helfer losschicken:\n • a").plain)

    def test_rueckfrage_bleibt_panel(self):
        self.assertIsInstance(ui.action_request("Löschen?", confirm=True, act={"tool": "loeschen"}), Panel)
        self.assertNotIsInstance(ui.action_request("Lesen", confirm=False, act={"tool": "datei_lesen"}), Panel)


class Ergebnis(_Basis):
    def test_voller_text_ohne_vollbild(self):
        text = _text(ui.action_result("\n".join(f"Zeile {i}" for i in range(10)) + "\nENDE", ok=True, sekunden=1.5))
        self.assertIn("⎿", text)
        self.assertIn("ENDE", text)
        self.assertIn("✓", text)

    def test_ungeprueft_ohne_haken(self):
        text = _text(ui.action_result("x", ok=None))
        self.assertIn("Status ungeprüft", text)
        self.assertNotIn("✓", text)

    def test_vollbild_klappt_ein_fehler_offen(self):
        ui.TUI = object()
        k = ui.action_result("\n".join(f"Zeile {i}" for i in range(10)), ok=True)
        self.assertIsInstance(k, ui.Klapp)
        kurz = _text(k.kurz)
        self.assertIn("Zeile 2", kurz)
        self.assertNotIn("Zeile 3", kurz)
        self.assertIn("+7 Zeilen", kurz)
        self.assertIn("Zeile 9", _text(k.lang))
        self.assertTrue(ui.action_result("kaputt", ok=False).offen)

    def test_einzug_bei_langen_zeilen(self):
        text = _text(ui.action_result("wort " * 40, ok=True), breite=60)
        zeilen = [z for z in text.splitlines() if z.strip()]
        self.assertTrue(zeilen[1].startswith("     "), zeilen[:2])


class Diff(_Basis):
    def test_aenderung_mit_zeilennummern(self):
        alt = "\n".join(f"z{i}" for i in range(1, 11))
        neu = alt.replace("z5", "z5 neu")
        text = _text(ui.diff_anzeige("datei.py", alt, neu))
        self.assertIn("1 hinzugefügt, 1 entfernt", text)
        self.assertIn("5 - z5", text)
        self.assertIn("5 + z5 neu", text)
        self.assertIn("3   z3", text)
        self.assertNotIn("z1\n", text)                 # nur zwei Zeilen Umfeld

    def test_neue_datei_und_ohne_unterschied(self):
        text = _text(ui.diff_anzeige("neu.txt", None, "a\nb"))
        self.assertIn("2 Zeilen geschrieben", text)
        self.assertIn("1 + a", text)
        self.assertIsNone(ui.diff_anzeige("x", "gleich", "gleich"))

    def test_lange_aenderung_gekuerzt(self):
        text = _text(ui.diff_anzeige("gross.txt", None, "\n".join(str(i) for i in range(100))))
        self.assertIn(f"+{100 - ui._DIFF_ZEILEN} Zeilen", text)


class Ablauf(_Basis):
    def test_diff_nach_dateiaenderung(self):
        import tempfile
        sys.path.insert(0, str(ROOT))
        import main
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.txt"
            p.write_text("eins\nzwei\n", encoding="utf-8")
            act = {"tool": "datei_bearbeiten", "pfad": str(p), "suchen": "zwei", "ersetzen": "drei"}
            vorher = main._vorher_stand(act)
            p.write_text("eins\ndrei\n", encoding="utf-8")
            gedruckt = []
            with mock.patch.object(ui, "console", mock.Mock(print=gedruckt.append)):
                main._diff_zeigen(act, vorher)
            self.assertEqual(1, len(gedruckt))
            self.assertIn("2 + drei", _text(gedruckt[0]))
        self.assertIsNone(main._vorher_stand({"tool": "datei_lesen", "pfad": "x"}))

    def test_umschalten_speichert(self):
        with mock.patch("config.update") as upd:
            self.assertTrue(ui.anzeige_setzen("rahmen"))
            upd.assert_called_once_with(anzeige="rahmen")
        self.assertIsInstance(ui.action_result("ok", ok=True), Panel)
        self.assertFalse(ui.anzeige_setzen("bunt"))


if __name__ == "__main__":
    unittest.main()
