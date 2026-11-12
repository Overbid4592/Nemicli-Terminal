"""Der API-Schlüssel steht nie in einer Adresse oder Fehlermeldung (Modell-Liste der Anbieter)."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx       # noqa: E402
import providers   # noqa: E402

SCHLUESSEL = "TEST-SCHLUESSEL-123"


class Schluessel(unittest.TestCase):
    def setUp(self):
        self.google = providers.PROVIDERS["google"]
        p = patch.object(type(self.google), "key", lambda self: SCHLUESSEL)
        p.start()
        self.addCleanup(p.stop)
        providers._models_cache.clear()
        self.addCleanup(providers._models_cache.clear)

    def _abrufen(self, status: int, antwort: dict | None = None):
        anfragen = []

        def behandeln(req):
            anfragen.append(req)
            return httpx.Response(status, json=antwort or {})
        echt = httpx.AsyncClient

        def client(*a, **k):
            return echt(*a, transport=httpx.MockTransport(behandeln), **k)
        with patch.object(httpx, "AsyncClient", client):
            try:
                return asyncio.run(providers.list_models("google")), anfragen
            except Exception as e:
                return e, anfragen

    def test_schluessel_im_kopf_nicht_in_der_adresse(self):
        ergebnis, anfragen = self._abrufen(200, {"models": [{"name": "models/gemini-x",
                                                            "supportedGenerationMethods": ["generateContent"]}]})
        self.assertEqual(anfragen[0].headers.get("x-goog-api-key"), SCHLUESSEL)
        self.assertNotIn(SCHLUESSEL, str(anfragen[0].url))
        self.assertNotIn("Authorization", anfragen[0].headers)

    def test_401_ohne_schluessel_und_adresse(self):
        fehler, _ = self._abrufen(401)
        self.assertIsInstance(fehler, RuntimeError)
        self.assertNotIn(SCHLUESSEL, str(fehler))
        self.assertNotIn("googleapis", str(fehler))
        self.assertIn("lehnt den API-Schlüssel ab (401)", str(fehler))

    def test_andere_fehler_nur_der_status(self):
        self.assertEqual(providers.status_text("X", 500), "X antwortet mit Fehler 500.")


if __name__ == "__main__":
    unittest.main()
