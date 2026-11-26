"""tools/systemabfrage.py: ungültige PID ergibt eine Meldung statt einer Ausnahme."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import systemabfrage   # noqa: E402


class ProzessTests(unittest.TestCase):
    def test_negative_pid(self):
        self.assertIn("Kein Prozess", systemabfrage.prozess(-1))


if __name__ == "__main__":
    unittest.main()
