"""Offline-Tests für /persönlichkeiten: Dateien, Auswahl, Prompt-Zusammenbau.

python -m unittest discover -s tests -p test_persoenlichkeiten.py
Arbeitet nur in einem Temp-Ordner – die echte Konfig und Persoenlichkeiten/
werden nicht angefasst.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import config                                   # noqa: E402
import persoenlichkeiten as PS                  # noqa: E402


class PersoenlichkeitenTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._old = (PS.DIR, config._FILE)
        PS.DIR = tmp / "Persoenlichkeiten"
        config._FILE = tmp / "cfg.json"
        PS.ensure_dir()

    def tearDown(self):
        PS.DIR, config._FILE = self._old
        self._tmp.cleanup()

    def test_default_is_nemi(self):
        self.assertEqual(list(PS.list_all()), [PS.NEMI_KEY])
        self.assertIs(PS.active(), PS.NEMI)
        self.assertTrue(PS.NEMI.text.startswith("Du bist NemiCLI"))

    def test_create_parse_and_activate(self):
        pfad = PS.create("Luna Ärger", "ruhig, nüchtern", "w", "sachlich")
        self.assertEqual(pfad.name, "luna-aerger.md")
        PS.set_active("luna-aerger")
        a = PS.active()
        self.assertEqual((a.name, a.kurz, a.key), ("Luna Ärger", "ruhig, nüchtern", "luna-aerger"))
        # Kopfzeilen + Tipp-Kommentar gehen nicht ans Modell, Inhalt schon
        self.assertFalse(a.text.startswith("#"))
        self.assertNotIn("<!--", a.text)
        self.assertIn("weiblich", a.text)
        self.assertIn("Sachlich & knapp", a.text)

    def test_never_overwrites(self):
        p1 = PS.create("Kai", "", "n", "locker")
        p2 = PS.create("Kai", "", "n", "locker")
        self.assertNotEqual(p1, p2)
        self.assertTrue(p2.exists() and p1.exists())

    def test_headerless_file_uses_filename(self):
        (PS.DIR / "grumpy-admin.md").write_text("Du bist ein grummeliger Sysadmin.", encoding="utf-8")
        p = PS.list_all()["grumpy-admin"]
        self.assertEqual(p.name, "Grumpy Admin")
        self.assertEqual(p.kurz, "")

    def test_resolve(self):
        PS.create("Luna", "x", "w", "locker")
        self.assertEqual(PS.resolve("luna").key, "luna")
        self.assertEqual(PS.resolve("LUNA").key, "luna")
        self.assertEqual(PS.resolve("lu").key, "luna")
        self.assertIsNone(PS.resolve("gibtsnicht"))
        self.assertIsNone(PS.resolve(""))

    def test_missing_file_falls_back_to_nemi(self):
        PS.set_active("weg")
        self.assertIs(PS.active(), PS.NEMI)

    def test_prompt_uses_active_name(self):
        import persona
        PS.create("Luna", "x", "w", "locker")
        PS.set_active("luna")
        sp = persona.base_prompt()
        self.assertTrue(sp.startswith("Du bist Luna"))
        self.assertIn("Dein Name ist immer Luna", sp)
        self.assertIn("Bleib als Luna in deiner Rolle", sp)
        self.assertNotIn("«NAME»", sp)
        self.assertIn('{"tool": "ordner_erstellen"', sp)      # JSON-Beispiel unversehrt
        PS.set_active(PS.NEMI_KEY)
        self.assertIn("Dein Name ist immer NemiCLI", persona.base_prompt())

    def test_placeholders_rendered_fresh(self):
        from datetime import datetime
        (PS.DIR / "lara.md").write_text(
            "# Lara\n> c\nZeit {{current_time}} Datum {{date}} User {{user}} "
            "Ich {{char}} {{unbekannt}}\n",
            encoding="utf-8")
        p = PS.list_all()["lara"]
        config.update(nutzername="test_user")
        out = PS.render_text(p, datetime(2026, 9, 12, 4, 5))
        self.assertEqual(out.strip(), "Zeit 04:05 Datum 12.09.2026 User test_user Ich Lara {{unbekannt}}")
        self.assertIn("{{current_time}}", p.text)          # Rohtext bleibt roh

    def test_edit_operations(self):
        PS.create("Kai", "x", "m", "sachlich")
        k = PS.list_all()["kai"]
        PS.set_name(k, "Kai Nord"); PS.set_kurz(k, "grummelig")
        k = PS.list_all()["kai"]
        self.assertEqual((k.name, k.kurz), ("Kai Nord", "grummelig"))
        PS.set_geschlecht(k, "w"); PS.set_ton(k, "verspielt")
        t = PS.list_all()["kai"].text
        self.assertIn("**weiblich**", t); self.assertNotIn("**männlich**", t)
        self.assertIn("Verspielt", t); self.assertNotIn("Sachlich", t)
        self.assertEqual(t.count("- Du bist **"), 1)          # ersetzt, nicht verdoppelt
        k = PS.list_all()["kai"]
        PS.add_line(k, "Du nennst mich Boss")
        k = PS.list_all()["kai"]
        self.assertIn("- Du nennst mich Boss", k.text)
        self.assertTrue(k.path.read_text(encoding="utf-8").rstrip().endswith("-->"))  # Tipp bleibt hinten
        idx = [i for i, s in PS.body_lines(k) if "Boss" in s][0]
        PS.remove_line(k, idx)
        self.assertNotIn("Boss", PS.list_all()["kai"].text)

    def test_duplicate_and_delete(self):
        pfad = PS.duplicate(PS.NEMI, "Nemi Zwei")
        z = PS.list_all()[pfad.stem]
        self.assertEqual(z.name, "Nemi Zwei")
        self.assertTrue(z.text.startswith("Du bist Nemi Zwei"))
        PS.set_active(z.key)
        PS.delete(z)
        self.assertNotIn(pfad.stem, PS.list_all())
        self.assertIs(PS.active(), PS.NEMI)
        with self.assertRaises(ValueError):
            PS.delete(PS.NEMI)
        with self.assertRaises(ValueError):
            PS.set_name(PS.NEMI, "x")


if __name__ == "__main__":
    unittest.main()
