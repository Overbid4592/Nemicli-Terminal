"""Kompakte Anleitung für lokale Modelle: Nachschlage-Abschnitte bei Bedarf.

python -m unittest tests.test_anleitung_kompakt
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A                                    # noqa: E402
import gguflokal as G                                  # noqa: E402
import modes                                           # noqa: E402
import persona                                         # noqa: E402


def _werkzeuge(text):
    return set(re.findall(r"(?m)^- (\w+)\s", text))


class KompaktTests(unittest.TestCase):

    def test_deutlich_kuerzer_und_alle_werkzeuge_bleiben(self):
        voll = persona.base_prompt()
        klein = persona.kompakt(voll)
        self.assertLess(len(klein), len(voll) * 0.6)
        self.assertEqual(_werkzeuge(klein), _werkzeuge(voll))
        for thema in persona.ANLEITUNGEN:
            self.assertIn(f'thema "{thema}"', klein)
        self.assertIn("# Grundregeln", klein)                         # Kern bleibt ganz
        self.assertIn("Systemordner: nachschauen ja", klein)

    def test_anleitung_liefert_den_ganzen_abschnitt(self):
        text = persona.anleitung("internet")
        self.assertIn("✗ = nicht in der Allowlist", text)
        self.assertNotIn("## Systemwache", text)
        self.assertEqual(persona.anleitung("gibtsnicht"), "")

    def test_themen_zuordnung(self):
        self.assertEqual(persona.thema_fuer_werkzeug("web_suche"), "internet")
        self.assertEqual(persona.thema_fuer_werkzeug("wache_alarme"), "wache")
        self.assertIsNone(persona.thema_fuer_werkzeug("datei_lesen"))
        self.assertEqual(persona.passende_anleitungen("Wie wird das Wetter?"), ["internet"])
        self.assertEqual(persona.passende_anleitungen("Erstell einen Ordner"), [])

    def test_wache_dienst_bekommt_die_wache_ganz(self):
        with patch.object(modes, "_wache_dienst", True):
            self.assertIn("wache_justieren    Felder: was", persona.build_system_prompt("", kompakt_fuer_lokal=True))

    def test_werkzeug_anleitung_lesen(self):
        self.assertIn("anleitung_lesen", A.ACTIONS)
        self.assertIn("anleitung_lesen", modes.READ_TOOLS)
        r = A._anleitung_lesen({"thema": "helfer"})
        self.assertTrue(r.ok)
        self.assertIn("subagenten", r.text)
        self.assertFalse(A._anleitung_lesen({"thema": "quatsch"}).ok)


class NachreichenTests(unittest.TestCase):

    def test_stichwort_und_werkzeug_je_einmal(self):
        c = G.GgufChat("Test")
        text = c._anleitungen_fuer("Such mal im Internet nach dem Wetter")
        self.assertIn(G.ANLEITUNG_MARKE.format("internet"), text)
        # schon im Verlauf: kommt nicht noch einmal
        c.messages = [{"role": "user", "content": "Frage"}]
        c._zusatz.von(c.messages[0])["gesendet"] = text + "\n\nFrage"
        self.assertEqual(c._anleitungen_fuer("Ergebnis von 'web_suche' (erfolgreich):\n…"), "")

    def test_werkzeug_ergebnis_ohne_stichwort_rauschen(self):
        c = G.GgufChat("Test")
        text = c._anleitungen_fuer("Ergebnis von 'datei_lesen' (erfolgreich):\nAlarm im Log, Wetter, Wache")
        self.assertEqual(text, "")                                     # Dateiinhalt löst nichts aus
        text = c._anleitungen_fuer("Ergebnis von 'wache_status' (erfolgreich):\n…")
        self.assertIn(G.ANLEITUNG_MARKE.format("wache"), text)


if __name__ == "__main__":
    unittest.main()
