"""Nach einem Update erfährt jede Persönlichkeit einmal, was neu ist (tools/neuigkeiten.py)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import config          # noqa: E402
import lernschleife    # noqa: E402
import neuigkeiten as N  # noqa: E402
import paths           # noqa: E402

LOG = """# Änderungen

### 7. Oktober 2026
- **🐈 Chrome-Seitenleiste.** Chat im Browser.
- **exe Build 6.7.295** (signiert): alles von heute.
- **Allowlist: `krea.ai`** – Blog lesen.
Fließtext ohne Überschrift.

### 6. Oktober 2026
- **💾 `/backup`.** Sicherungen.

### 5. Oktober 2026
- **Krea-Gesicht nachmalen.** Gesichter.
"""


class Neuigkeiten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        (self.ordner / "CHANGELOG.md").write_text(LOG, encoding="utf-8")
        self.cfg = {}
        for p in (mock.patch.object(paths, "ASSETS", self.ordner),
                  mock.patch.object(config, "load", side_effect=lambda: dict(self.cfg)),
                  mock.patch.object(config, "update", side_effect=lambda **k: self.cfg.update(k))):
            p.start()
            self.addCleanup(p.stop)
        N._cache["pfad"] = None

    def test_zerlegen_nur_ueberschriften_ohne_build(self):
        self.assertEqual([(0, "🐈 Chrome-Seitenleiste"), (0, "Allowlist: krea.ai"), (1, "💾 /backup"),
                          (2, "Krea-Gesicht nachmalen")], N.zerlegen(LOG))

    def test_erstes_mal_nur_die_letzten_tage_dann_nichts(self):
        notiz = N.hinweis("a")
        self.assertTrue(notiz.startswith(lernschleife.HINWEIS_MARKE))
        self.assertIn("- 🐈 Chrome-Seitenleiste", notiz)
        self.assertIn("- 💾 /backup", notiz)
        self.assertNotIn("Krea-Gesicht", notiz)                    # älter als ERSTE_TAGE
        self.assertNotIn("exe Build", notiz)
        self.assertEqual("", N.hinweis("a"))
        self.assertEqual("Hallo", lernschleife.ohne_hinweise("Hallo" + notiz))

    def test_nach_update_nur_das_neue_und_je_persoenlichkeit(self):
        N.hinweis("a")
        geschrieben = dict(self.cfg)
        self.assertEqual("", N.hinweis("a"))
        self.assertEqual(geschrieben, self.cfg)                     # ohne Neues kein Schreiben
        neu = LOG.replace("### 7. Oktober 2026\n", "### 8. Oktober 2026\n- **🧩 Skill-Wächter.** Neu.\n\n"
                          "### 7. Oktober 2026\n")
        (self.ordner / "CHANGELOG.md").write_text(neu, encoding="utf-8")
        N._cache["pfad"] = None
        notiz = N.hinweis("a")
        self.assertIn("- 🧩 Skill-Wächter", notiz)
        self.assertNotIn("Chrome-Seitenleiste", notiz)
        self.assertIn("Skill-Wächter", N.hinweis("b"))             # b ist neu: die letzten Tage
        self.assertIn("Chrome-Seitenleiste", N.hinweis("c"))

    def test_vergessen_und_viele_eintraege(self):
        N.hinweis("weg")
        N.vergessen("weg")
        self.assertNotIn("weg", self.cfg["neuigkeiten_gesehen"])
        viele = "### 9. Oktober 2026\n" + "".join(f"- **Neu {i}.** x\n" for i in range(20))
        (self.ordner / "CHANGELOG.md").write_text(viele, encoding="utf-8")
        N._cache["pfad"] = None
        notiz = N.hinweis("x")
        self.assertEqual(N.MAX_IN_NOTIZ + 1, notiz.count("\n- "))
        self.assertIn("… und 8 weitere", notiz)

    def test_ohne_changelog_still(self):
        (self.ordner / "CHANGELOG.md").unlink()
        N._cache["pfad"] = None
        self.assertEqual("", N.hinweis("a"))
        self.assertEqual({}, self.cfg)

    def test_eingebaut(self):
        main = main_quelle.text()
        self.assertEqual(2, main.count("user_text += _neuigkeiten(PS.active().key)"))
        self.assertIn('"CHANGELOG.md"', (ROOT / "NemiCLI.spec").read_text(encoding="utf-8"))
        self.assertIn("neuigkeiten.vergessen(p.key)", (ROOT / "core" / "persoenlichkeiten.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
