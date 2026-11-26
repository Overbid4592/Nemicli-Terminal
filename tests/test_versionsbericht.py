"""Versionsbericht: Die KI kann jederzeit nachlesen, was neu ist."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import persona            # noqa: E402
import version            # noqa: E402
import versionsbericht    # noqa: E402


class Versionsbericht(unittest.TestCase):
    def test_aktuelle_version_hat_einen_eintrag(self):
        # Jedes Paket ergänzt seinen Eintrag – sonst weiß die KI nicht, was sie Neues kann.
        self.assertEqual(versionsbericht.EINTRAEGE[0][0], version.VERSION.split()[0])

    def test_neueste_zuerst_und_vollstaendig(self):
        nummern = [tuple(map(int, n.split("."))) for n, _, _ in versionsbericht.EINTRAEGE]
        self.assertEqual(nummern, sorted(nummern, reverse=True))
        for nummer, titel, text in versionsbericht.EINTRAEGE:
            self.assertTrue(titel and len(text) > 40, nummer)

    def test_bericht_ueber_anleitung_lesen(self):
        text = persona.anleitung("neu")
        self.assertIn(versionsbericht.EINTRAEGE[0][1], text)
        self.assertIn(version.VERSION, text)
        self.assertEqual(text.count("\n## "), min(versionsbericht.ANZAHL, len(versionsbericht.EINTRAEGE)))
        self.assertIn("neu", persona.ANLEITUNGEN)

    def test_kommt_bei_der_frage_von_selbst(self):
        for frage in ("Was ist neu bei dir?", "Was wurde gemacht?", "Was kannst du jetzt alles?",
                      "Gibt es eine neue Version?"):
            self.assertIn("neu", persona.passende_anleitungen(frage), frage)
        self.assertNotIn("neu", persona.passende_anleitungen("Wie geht es dir heute?"))

    def test_hinweise_zeigen_den_weg(self):
        self.assertIn('thema "neu"', persona.base_prompt())
        self.assertIn('thema \\"neu\\"', (ROOT / "tools" / "neuigkeiten.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
