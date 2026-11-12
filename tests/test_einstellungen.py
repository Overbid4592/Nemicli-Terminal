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
