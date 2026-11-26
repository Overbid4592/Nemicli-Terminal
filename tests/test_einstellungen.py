"""Werkzeug einstellung_aendern: Domain-Prüfung, eigene Allowlist, Rückfrage in jedem Modus.

python -m unittest tests.test_einstellungen
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A                                    # noqa: E402
import einstellungen as E                              # noqa: E402
import modes                                           # noqa: E402
import snapshot                                        # noqa: E402
import webfetch                                        # noqa: E402
import coding                                          # noqa: E402


class DomainTests(unittest.TestCase):
    def test_gueltig(self):
        self.assertEqual(E.domain("golem.de"), "golem.de")
        self.assertEqual(E.domain("https://www.Golem.de/news/123.html"), "golem.de")
        self.assertEqual(E.domain("docs.example.org/"), "docs.example.org")

    def test_ungueltig(self):
        for roh in ("", "http://golem.de", "192.168.0.1", "localhost", "drucker.local", "*.golem.de",
                    "golem", "pastebin.com", "x.netlify.app", "bit.ly", "a b.de"):
            with self.subTest(roh=roh), self.assertRaises(ValueError):
                E.domain(roh)


class AendernTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.datei = Path(tmp.name) / "allowlist_eigen.json"
        for p in (mock.patch.object(E, "datei", lambda: self.datei),
                  mock.patch.object(snapshot, "sichern", lambda *a, **k: None),
                  mock.patch.object(A, "_web_taint", False)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(webfetch.neu_laden)
        webfetch.neu_laden()

    def test_erlauben_und_sperren(self):
        self.assertFalse(webfetch.is_allowed("https://nemi-test-domain.org/x"))
        vorschau = E.vorschau("internet_erlauben", "nemi-test-domain.org", "Test")
        self.assertIn("nemi-test-domain.org", vorschau)
        self.assertFalse(self.datei.exists())                  # Vorschau schreibt nichts
        E.aendern("internet_erlauben", "nemi-test-domain.org", "Test")
        self.assertTrue(webfetch.is_allowed("https://sub.nemi-test-domain.org/x"))
        self.assertEqual(json.loads(self.datei.read_text(encoding="utf-8"))["hinzu"][0]["tier"], 2)
        with self.assertRaises(ValueError):
            E.vorschau("internet_erlauben", "nemi-test-domain.org")      # schon erlaubt
        E.aendern("internet_sperren", "nemi-test-domain.org")
        self.assertFalse(webfetch.is_allowed("https://nemi-test-domain.org/x"))

    def test_mitgelieferte_domain_sperren_und_zurueck(self):
        d = sorted(webfetch.mitgeliefert())[0]
        E.aendern("internet_sperren", d)
        self.assertFalse(webfetch.is_allowed(f"https://{d}/"))
        E.aendern("internet_erlauben", d)
        self.assertTrue(webfetch.is_allowed(f"https://{d}/"))
        self.assertEqual(E.laden(), {"hinzu": [], "weg": []})

    def test_unbekannt_und_nicht_gelistet(self):
        with self.assertRaises(ValueError):
            E.vorschau("loesch_limit", "100")
        with self.assertRaises(ValueError):
            E.vorschau("internet_sperren", "nemi-test-domain.org")

    def test_netz_taint_sperrt(self):
        with mock.patch.object(A, "_web_taint", True):
            with self.assertRaises(ValueError):
                A.confirmation_preview({"tool": "einstellung_aendern", "was": "internet_erlauben",
                                        "wert": "nemi-test-domain.org"})
            r = asyncio.run(A.run({"tool": "einstellung_aendern", "was": "internet_erlauben",
                                   "wert": "nemi-test-domain.org"}))
        self.assertFalse(r.ok)
        self.assertFalse(self.datei.exists())


    def test_von_hand_auch_nach_netz_text(self):
        with mock.patch.object(A, "_web_taint", True):
            with self.assertRaises(ValueError):
                E.aendern("internet_erlauben", "nemi-test-domain.org")         # der Weg der KI bleibt gesperrt
            satz = E.aendern("internet_erlauben", "nemi-test-domain.org", "von Hand", vom_nutzer=True)
        self.assertIn("ist jetzt erlaubt", satz)
        self.assertTrue(webfetch.is_allowed("https://nemi-test-domain.org/"))
        self.assertIn("nemi-test-domain.org", E.eigene_text())
        with self.assertRaises(ValueError):
            E.aendern("internet_erlauben", "pastebin.com", vom_nutzer=True)    # nie erlaubt bleibt nie erlaubt
        E.aendern("internet_sperren", "nemi-test-domain.org", vom_nutzer=True)
        self.assertIn("Noch keine eigenen", E.eigene_text())


class WebBefehl(unittest.TestCase):
    """/web erlauben|sperren ändert nur, wenn die Zeile von Hand getippt wurde."""

    def setUp(self):
        import main
        self.main = main
        self.meldungen: list[str] = []
        self.geaendert: list[tuple] = []
        for p in (mock.patch.object(main.ui, "warn", side_effect=self.meldungen.append),
                  mock.patch.object(main.ui, "info", side_effect=self.meldungen.append),
                  mock.patch.object(main.ui, "success", side_effect=self.meldungen.append),
                  mock.patch.object(E, "aendern", side_effect=lambda *a, **k: self.geaendert.append((a, k))
                                    or "beispiel.org ist jetzt erlaubt.")):
            p.start()
            self.addCleanup(p.stop)

    def _laufen(self, arg, vom_nutzer):
        async def los():
            self.main.VOM_NUTZER.set(vom_nutzer)
            await self.main._web_eigene(None, arg)
        asyncio.run(los())

    def test_nicht_von_hand(self):
        self._laufen("erlauben beispiel.org", False)
        self.assertEqual([], self.geaendert)
        self.assertIn("selbst tippst", self.meldungen[-1])

    def test_von_hand(self):
        self._laufen("erlauben https://www.Beispiel.org/x", True)
        (args, kwargs), = self.geaendert
        self.assertEqual(("internet_erlauben", "beispiel.org"), args[:2])
        self.assertTrue(kwargs["vom_nutzer"])
        self._laufen("sperren beispiel.org", True)
        self.assertEqual("internet_sperren", self.geaendert[-1][0][0])

    def test_ungueltig_und_ohne_seite(self):
        self._laufen("erlauben bit.ly", True)
        self.assertEqual([], self.geaendert)
        self.assertIn("Kurz-Link", self.meldungen[-1])
        self._laufen("erlauben", True)
        self.assertIn("Nutzung", self.meldungen[-1])


class OllamaFehler(unittest.TestCase):
    """Ollama antwortet, die Zielseite liefert nichts: die Meldung sagt das, statt die API für kaputt zu halten."""

    def _antwort(self, code, body=None):
        r = mock.MagicMock(status_code=code)
        r.json.return_value = body if body is not None else {}
        return r

    def test_404_der_zielseite(self):
        with mock.patch.object(webfetch.os, "getenv", return_value="schluessel"), \
                mock.patch.object(webfetch.netzhilfe, "abrufen",
                                  return_value=self._antwort(404, {"error": "page not found"})):
            text = webfetch.fremd_laden("https://example.org/x")
        self.assertIn("Ollama konnte https://example.org/x nicht laden (404)", text)
        self.assertIn("page not found", text)
        self.assertIn("Ollama selbst läuft", text)
        self.assertIn("/web erlauben", text)

    def test_anderer_fehler_der_suche(self):
        with mock.patch.object(webfetch.os, "getenv", return_value="schluessel"), \
                mock.patch.object(webfetch.netzhilfe, "abrufen", return_value=self._antwort(500)):
            text = webfetch._ollama_post(webfetch._SEARCH_URL, {"query": "x"}, "Ollama-Suche")
        self.assertEqual("❌ Ollama-Suche: Ollama antwortet mit 500 – später noch einmal versuchen.", text)


class ModusTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(coding, "projekt", lambda: None)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(modes.set_mode, "normal")

    def test_fragt_in_jedem_modus(self):
        act = {"tool": "einstellung_aendern", "was": "internet_erlauben", "wert": "golem.de"}
        for modus, erwartet in (("chat", "ask"), ("normal", "ask"), ("auto", "ask"), ("autoan", "ask"),
                                ("lesen", "block")):
            modes.set_mode(modus)
            self.assertEqual(modes.decide(act, A.needs_confirm(act)), erwartet, modus)


if __name__ == "__main__":
    unittest.main()
