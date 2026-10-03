"""Offline-Tests: ein geschwätziger Suchtreffer darf die anderen nicht verdrängen.

Beispiel: Bei einer Frage nach API-Preisen
lieferte Treffer 1 eine komplette Preisseite – 6.832 Zeichen,
67 Tabellenzeilen. Damit war das Gesamtbudget (MAX_OUT) aufgebraucht und die
Treffer 2 bis 5 wurden abgeschnitten. Hätte die Antwort in Treffer 3
gestanden, wäre sie weg gewesen.

python -m unittest discover -s tests -p test_websuche_schnipsel.py
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import webfetch as W      # noqa: E402


# So sah der echte Treffer aus, der alles verdrängt hat.
PREISSEITE = ("Pricing | OpenAI API\n\n# Pricing\n\nPrices per 1M tokens.\n\n"
              + "| Model | Input | Cached input | Cache writes | Output |\n"
              + "| --- | --- | --- | --- | --- |\n"
              + "| gpt-6-astra | $10.00 | $1.00 | $12.50 | $50.00 |\n" * 60)


class SchnipselKuerzen(unittest.TestCase):
    def test_kurzes_bleibt_unveraendert(self):
        text = "Ein kurzer Schnipsel."
        self.assertEqual(W._kurz(text), text)

    def test_langes_wird_gekuerzt(self):
        gekuerzt = W._kurz(PREISSEITE)
        self.assertLess(len(gekuerzt), len(PREISSEITE))
        self.assertLessEqual(len(gekuerzt), W.MAX_SNIPPET + 80)  # + Hinweistext

    def test_der_hinweis_sagt_wie_man_weiterliest(self):
        self.assertIn("web_lesen", W._kurz(PREISSEITE))

    def test_zeilenumbrueche_fliegen_raus(self):
        # Schnipsel sind oft ganze Tabellen – die brauchen im Trefferblock keiner.
        self.assertNotIn("\n", W._kurz(PREISSEITE))

    def test_es_wird_nicht_mitten_im_wort_geschnitten(self):
        text = "Wort " * 400
        gekuerzt = W._kurz(text)
        sichtbar = gekuerzt.split(" … ")[0]
        self.assertTrue(sichtbar.endswith("Wort"), sichtbar[-20:])

    def test_an_der_satzgrenze_wird_bevorzugt_geschnitten(self):
        text = "Erster Satz. " * 100
        sichtbar = W._kurz(text).split(" … ")[0]
        self.assertTrue(sichtbar.endswith("."), sichtbar[-20:])

    def test_leeres_geht_auch(self):
        self.assertEqual(W._kurz(""), "")
        self.assertEqual(W._kurz(None), "")


class AlleTrefferKommenAn(unittest.TestCase):
    """Der eigentliche Punkt: fünf Treffer, einer davon riesig – trotzdem
    müssen alle fünf im Ergebnis stehen."""

    def _bauen(self, treffer):
        """Ruft search() mit vorgegebenen Rohtreffern auf.

        search() liest den Key direkt aus der Umgebung – für den Test genügt
        irgendeiner, das Netz wird ohnehin durch die Attrappe ersetzt."""
        with mock.patch.dict(W.os.environ, {W.SEARCH_ENV: "testkey"}), \
             mock.patch.object(W.httpx, "Client", self._client(treffer)):
            return W.search("GPT-6 Astra API Preis", count=5)

    def _client(self, treffer):
        class Antwort:
            status_code = 200

            def raise_for_status(self_): pass

            def json(self_): return {"results": treffer}

        class Client:
            def __init__(self_, *a, **k): pass
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def post(self_, *a, **k): return Antwort()
            def get(self_, *a, **k): return Antwort()
        return Client

    def _treffer(self):
        return [
            {"title": "Pricing | OpenAI API",
             "url": "https://developers.openai.com/api/docs/pricing",
             "content": PREISSEITE},
            {"title": "Zweiter Treffer", "url": "https://example.com/2",
             "content": "Hier steht die Antwort in Treffer zwei."},
            {"title": "Dritter Treffer", "url": "https://example.com/3",
             "content": "Und hier noch eine wichtige Zahl: 42."},
            {"title": "Vierter Treffer", "url": "https://example.com/4",
             "content": "Vierter Inhalt."},
            {"title": "Fünfter Treffer", "url": "https://example.com/5",
             "content": "Fünfter Inhalt."},
        ]

    def test_alle_fuenf_stehen_drin(self):
        aus = self._bauen(self._treffer())
        for nr in ("1.", "2.", "3.", "4.", "5."):
            self.assertIn(nr, aus, f"Treffer {nr} fehlt")
        self.assertIn("Antwort in Treffer zwei", aus)
        self.assertIn("wichtige Zahl: 42", aus)
        self.assertIn("Fünfter Inhalt", aus)

    def test_am_ende_wird_nichts_mehr_abgeschnitten(self):
        aus = self._bauen(self._treffer())
        self.assertNotIn("… (gekürzt)", aus)     # das war die alte Gesamt-Kappung

    def test_das_ganze_bleibt_im_budget(self):
        aus = self._bauen(self._treffer())
        self.assertLessEqual(len(aus), W.MAX_OUT + 1200)   # + Rahmen und Warnhinweis

    def test_der_riesentreffer_ist_gekuerzt_aber_da(self):
        aus = self._bauen(self._treffer())
        self.assertIn("gpt-6-astra", aus)        # die Antwort steht noch drin
        self.assertIn("Schnipsel gekürzt", aus)


if __name__ == "__main__":
    unittest.main()
