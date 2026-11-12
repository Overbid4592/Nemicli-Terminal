"""Modell-Liste von Ollama Cloud (ollama.com/v1/models): nicht per API nutzbare Modelle fallen weg, die
Kontextlänge kommt aus dem Zusatzteil "models"; Bild-Erkennung und Denkstufen passen zu /api/show.

python -m unittest tests.test_ollama_cloud_liste
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import cloudprovider as CP                             # noqa: E402
import reasoning                                       # noqa: E402

ANTWORT = {
    "object": "list",
    "data": [{"id": "deepseek-v4.1-flash", "object": "model"}, {"id": "clef:27b", "object": "model"},
             {"id": "nemotron-3-super", "object": "model"}],
    "models": [
        {"slug": "deepseek-v4.1-flash", "supported_in_api": True, "context_window": 1048576,
         "max_context_window": 1048576, "input_modalities": ["text", "image"]},
        {"slug": "clef:27b", "supported_in_api": False, "visibility": "hide", "context_window": 65536},
    ],
}


class Liste(unittest.TestCase):
    def test_nicht_per_api_nutzbar_faellt_weg(self):
        ids = CP._parse_models(CP.CLOUD["ollama_cloud"], ANTWORT)
        self.assertEqual(ids, ["deepseek-v4.1-flash", "nemotron-3-super"])

    def test_kontext_aus_dem_zusatz(self):
        eintraege = dict(CP._eintraege(CP.CLOUD["ollama_cloud"], ANTWORT))
        self.assertEqual(CP.kontext_aus_eintrag(eintraege["deepseek-v4.1-flash"]), 1048576)
        self.assertIsNone(CP.kontext_aus_eintrag(eintraege["nemotron-3-super"]))

    def test_ohne_zusatz_wie_bisher(self):
        ids = CP._parse_models(CP.CLOUD["ollama_cloud"], {"data": [{"id": "a"}, {"id": "b"}]})
        self.assertEqual(ids, ["a", "b"])


class Tabelle(unittest.TestCase):
    """Stand der Tabelle wie ollama.com/api/show (gilt, wenn die Abfrage nicht geht)."""

    def test_neue_modelle_und_stufen(self):
        t = reasoning.OLLAMA_CLOUD
        self.assertEqual(t["kimi-k3.1-pro"]["think"], (False, "low", "medium", "high", "max"))
        self.assertEqual(t["minimax-m3"]["think"], (True,))
        self.assertEqual(t["minimax-m3.1-flash"]["ctx"], 1_000_000)
        for neu in ("beam", "mimo-v2.6-flash", "mimo-v2.6-pro", "mistral-large-4", "muse-spark-open"):
            self.assertIn(neu, t)
        self.assertEqual(reasoning.cloud_effort("beam", "stark"), "high")
        self.assertEqual(reasoning.cloud_effort("beam", "max"), "max")

    def test_bilder_am_namen(self):
        for sieht in ("mimo-v2.6-pro", "minimax-m3.1-flash", "mistral-large-4", "muse-spark-open", "kimi-k3.1-pro",
                      "glm-5.3-flash", "gemma4:31b"):
            self.assertTrue(CP.cloud_name_vision(sieht), sieht)
        for blind in ("beam", "glm-5.3", "minimax-m2.7", "nemotron-3-super", "deepseek-v4-pro:0813"):
            self.assertFalse(CP.cloud_name_vision(blind), blind)


if __name__ == "__main__":
    unittest.main()
