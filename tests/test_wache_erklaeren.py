"""Wache erklärt sich: Messwerte weit über dem Gelernten als Grund, Einordnung des Scores in Worten,
Erklär-Muster im Systemprompt."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for d in ("", "core", "tools", "engines"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

from wache import detektor, werkzeuge        # noqa: E402
from wache.wald import Skalierer             # noqa: E402


class Messwerte(unittest.TestCase):
    def setUp(self):
        self.namen = ["cpu", "mem", "cmd_laenge", "prozess_neu"]
        rng = np.random.default_rng(1)
        X = np.column_stack([rng.normal(0.1, 0.02, 500), rng.normal(0.2, 0.05, 500),
                             rng.normal(0.3, 0.05, 500), np.zeros(500)])
        self.sk = Skalierer().anpassen(X)

    def test_weit_ueber_dem_gewohnten(self):
        gruende = detektor.messwert_gruende(self.namen, [0.9, 0.21, 0.9, 0.0], self.sk)
        self.assertEqual(len(gruende), 1)
        self.assertIn("CPU-Last", gruende[0])
        self.assertIn("Länge der Kommandozeile", gruende[0])
        self.assertLess(gruende[0].index("CPU-Last"), gruende[0].index("Länge"))   # stärkste zuerst
        self.assertNotIn("Speicher", gruende[0])

    def test_normal_und_ohne_training(self):
        self.assertEqual(detektor.messwert_gruende(self.namen, [0.1, 0.2, 0.3, 0.0], self.sk), [])
        self.assertEqual(detektor.messwert_gruende(self.namen, [0.9, 0.2, 0.3, 0.0], None), [])
        self.assertEqual(detektor.messwert_gruende(["prozess_neu"], [1.0], self.sk), [])       # ja/nein-Merkmale nicht


class Einordnung(unittest.TestCase):
    def test_worte(self):
        self.assertIn("sehr ungewöhnlich", werkzeuge.einordnung_zeile(0.995))
        self.assertIn("auffälliger als 96 %", werkzeuge.einordnung_zeile(0.96))
        self.assertIn("etwas auffällig", werkzeuge.einordnung_zeile(0.5))
        self.assertEqual(werkzeuge.einordnung_zeile(None), "")
        self.assertEqual(werkzeuge.einordnung_zeile("x"), "")
        self.assertEqual(werkzeuge.einordnung_zeile(3), "")

    def test_erklaermuster_im_prompt(self):
        text = (ROOT / "core" / "persona.py").read_text(encoding="utf-8")
        self.assertIn("🟢 harmlos · 🟡 unklar · 🔴 echt", text)


if __name__ == "__main__":
    unittest.main()
