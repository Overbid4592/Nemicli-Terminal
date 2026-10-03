"""Offline-Tests: Maskottchen spricht mit der Stimme der aktiven Persönlichkeit."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import mascot            # noqa: E402
import persoenlichkeiten as PS   # noqa: E402


class FakeBackend:
    def __init__(self, antwort):
        self.antwort = antwort
        self.aufrufe = 0

    async def ask_once(self, prompt, system):
        self.aufrufe += 1
        return self.antwort


class MascotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = PS.Persoenlichkeit(key="maia", name="Maia", kurz="", text="Ich bin Maia.", path=None)
        self._patches = [patch.object(PS, "DIR", Path(self.tmp.name)),
                         patch.object(PS, "active", lambda: self.p),
                         patch.object(PS, "nutzername", lambda: "Max")]
        for p in self._patches:
            p.start(); self.addCleanup(p.stop)
        self.gesagt = []
        patch.object(mascot.ui.console, "print", lambda *a, **k: self.gesagt.append(" ".join(map(str, a)))).start()
        self.addCleanup(patch.stopall)

    def test_ohne_datei_neutraler_gruss_mit_namen(self):
        mascot.greet()
        self.assertIn("Maia hier. Hallo, Max.", self.gesagt[-1])
        self.assertIn("💬 Maia", self.gesagt[-1])
        self.assertFalse(mascot.hat_sprueche())

    def test_datei_wird_gelesen_und_platzhalter_ersetzt(self):
        (Path(self.tmp.name) / "maia.sprueche.md").write_text(
            "# Kopf\n## Begrüßung\nHey {nutzer}, {name} ist da.\n\n## Sprüche\n- Frag ruhig.\n", encoding="utf-8")
        self.assertTrue(mascot.hat_sprueche())
        mascot.greet()
        self.assertIn("Hey Max, Maia ist da.", self.gesagt[-1])
        with patch.object(mascot, "TIPS", []):
            mascot.bubble()
        self.assertIn("Frag ruhig.", self.gesagt[-1])

    def test_nachholen_schreibt_datei(self):
        be = FakeBackend("## Begrüßung\n1. Hallo {nutzer}!\n2. Da bin ich.\n- Na?\n\n## Sprüche\n„Ich helf dir.“\nMach weiter.\nGleich fertig.\n")
        ok = asyncio.run(mascot.sprueche_nachholen(be))
        self.assertTrue(ok)
        d = mascot._lesen()
        self.assertEqual(["Hallo {nutzer}!", "Da bin ich.", "Na?"], d["gruss"])
        self.assertEqual(["Ich helf dir.", "Mach weiter.", "Gleich fertig."], d["spruch"])
        # zweiter Aufruf: Datei da → kein Modellaufruf mehr
        asyncio.run(mascot.sprueche_nachholen(be))
        self.assertEqual(1, be.aufrufe)

    def test_nachholen_bei_muell_keine_datei(self):
        be = FakeBackend("Ich weiß nicht, was du meinst.")
        self.assertFalse(asyncio.run(mascot.sprueche_nachholen(be)))
        self.assertFalse(mascot.sprueche_datei().exists())


if __name__ == "__main__":
    unittest.main()
