"""Rückmeldungen aus dem Alltag einer Persönlichkeit: Todo-Pflicht nachvollziehbar (neuer Versuch, Urteile frei),
Sehen bei Ollama-Cloud-Modellen aus der Abfrage statt aus der Namensliste, Kleidung im kern der Charakter-Datei
wird angemerkt, Fertigmeldungen von Hintergrund-Aufgaben kommen vollständig bei der KI an."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui", "tests"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import plan                                            # noqa: E402
import providers as P                                  # noqa: E402


class TodoPflicht(unittest.TestCase):

    def setUp(self):
        plan.verwerfen()
        self.addCleanup(plan.verwerfen)
        plan.runde_neu()

    def _zug(self, *tools):
        return plan.zurueckweisen([{"tool": t} for t in tools])

    def test_neuer_versuch_nach_fehlschlag_ist_frei(self):
        laufen, gesperrt = self._zug("portraet_bewegen")
        self.assertEqual(([{"tool": "portraet_bewegen"}], []), (laufen, gesperrt))
        plan.abhaken_nach({"tool": "portraet_bewegen"}, False)
        self.assertEqual([], self._zug("portraet_bewegen")[1])        # ein neuer Versuch läuft
        self.assertEqual(1, len(self._zug("portraet_bewegen")[1]))    # ein zweiter ohne neuen Fehlschlag nicht

    def test_geklappte_aktion_gibt_keinen_freischein(self):
        self._zug("bild_malen")
        plan.abhaken_nach({"tool": "bild_malen"}, True)
        self.assertEqual(1, len(self._zug("bild_malen")[1]))

    def test_urteile_sind_frei(self):
        for _ in range(5):
            self.assertEqual([], self._zug("wache_bewerten")[1])
        self.assertEqual([], self._zug("datei_schreiben")[1])          # der freie Arbeitsschritt bleibt da

    def test_sperrmeldung_erklaert_die_regel(self):
        self._zug("datei_schreiben")
        _laufen, gesperrt = self._zug("loeschen")
        text = plan.sperr_text(gesperrt)
        self.assertIn("Pro Nachricht", text)
        self.assertIn("datei_schreiben", text)                         # was den freien Schritt schon verbraucht hat
        self.assertIn("neuer Versuch", text)

    def test_neue_nachricht_setzt_zurueck(self):
        self._zug("bild_malen")
        plan.abhaken_nach({"tool": "portraet_bewegen"}, False)
        plan.runde_neu()
        self.assertEqual([], self._zug("datei_schreiben")[1])
        self.assertEqual(1, len(self._zug("portraet_bewegen")[1]))     # alter Fehlschlag gilt nicht mehr


class SehenOllamaCloud(unittest.TestCase):

    def setUp(self):
        P._cloud_caps_cache.clear()
        P._cloud_caps_fehler.clear()
        self.addCleanup(P._cloud_caps_cache.clear)
        self.addCleanup(P._cloud_caps_fehler.clear)

    def test_abfrage_und_zwischenspeicher(self):
        aufrufe = []

        def post(url, **kw):
            aufrufe.append((url, kw["json"]))
            return NS(raise_for_status=lambda: None, json=lambda: {"capabilities": ["completion", "vision"]})
        with mock.patch.object(P.httpx, "post", post):
            self.assertEqual(["completion", "vision"], P.ollama_cloud_capabilities("glm-5.3-flash"))
            P.ollama_cloud_capabilities("glm-5.3-flash")
        self.assertEqual([(P.CLOUD_SHOW_URL, {"model": "glm-5.3-flash"})], aufrufe)

    def test_fehler_dann_namensliste_und_pause(self):
        aufrufe = []

        def kaputt(url, **kw):
            aufrufe.append(url)
            raise OSError("kein Netz")
        with mock.patch.object(P.httpx, "post", kaputt):
            self.assertIsNone(P.ollama_cloud_capabilities("x-modell"))
            self.assertIsNone(P.ollama_cloud_capabilities("x-modell"))
        self.assertEqual(1, len(aufrufe))                               # nicht bei jeder Frage neu warten

    def test_hauptprogramm_nutzt_die_abfrage(self):
        import ast
        baum = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
        knoten = [n for n in baum.body if isinstance(n, ast.FunctionDef) and n.name == "_modell_sieht"]
        ns = {"M": __import__("models"), "P": P}
        exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), ns)
        with mock.patch.object(P, "ollama_cloud_capabilities", lambda m: ["vision"]):
            self.assertTrue(ns["_modell_sieht"]("ollama_cloud:glm-5.3-flash"))
        with mock.patch.object(P, "ollama_cloud_capabilities", lambda m: ["completion"]):
            self.assertFalse(ns["_modell_sieht"]("ollama_cloud:deepseek-v4.1-flash"))   # Abfrage schlägt Name
        with mock.patch.object(P, "ollama_cloud_capabilities", lambda m: None):
            self.assertTrue(ns["_modell_sieht"]("ollama_cloud:deepseek-v4.1-flash"))    # ohne Antwort: Name


class KleidungImKern(unittest.TestCase):

    def test_erkennt_kleidung_nicht_aber_alltag(self):
        import charakter
        self.assertEqual(["jacket", "boots"], charakter.kleidung_im_kern(
            "young woman, long silver hair, green eyes, black leather jacket and boots"))
        self.assertEqual(["kleid"], charakter.kleidung_im_kern("Sie hat rote Haare und trägt ein Kleid."))
        self.assertEqual([], charakter.kleidung_im_kern(
            "Sie hat lange Haare, ein Lächeln on top, Augen wie Rock-Musik"))   # mehrdeutige Wörter zählen nicht


class InventarBelege(unittest.TestCase):
    """Neue Autostarts und Dienste: exe ist die Datei (für Signatur und Vollscan), die ganze Befehlszeile bleibt."""

    def test_befehlszeile_wird_zur_datei(self):
        from wache import inventar as I, regeln as R
        neu = I.neue_als_ereignisse([
            I.Eintrag(I.AUTOSTART, "Werkzeug", r'"C:\Program Files\Werkzeug\werkzeug.exe" --minimized'),
            I.Eintrag(I.DIENST, "Dienst", r"C:\WINDOWS\system32\svchost.exe -k netsvcs -p"),
            I.Eintrag(I.AUTOSTART, "Versteckt", r"powershell.exe -w hidden -enc ZQBjAGgAbwA=")], set())
        werkzeug, dienst, versteckt = neu
        self.assertEqual(r"C:\Program Files\Werkzeug\werkzeug.exe".lower(), werkzeug.exe.lower())
        self.assertIn("--minimized", werkzeug.extra["befehl"])
        self.assertIn("--minimized", werkzeug.text)                    # der Alarm zeigt weiter die ganze Zeile
        self.assertTrue(dienst.exe.lower().endswith(r"system32\svchost.exe"))
        treffer = R.Regelwerk()._r010(versteckt)
        self.assertEqual("R010", treffer.regel)
        self.assertIn("zusätzlich auffällig", treffer.text)             # die Regel sieht weiter die ganze Zeile


class WacheLebenszeichen(unittest.TestCase):
    """Eine laufende, aber hängende Wache schreibt kein Lebenszeichen mehr – das muss sichtbar werden."""

    def _lock(self, vor_s: float):
        import json
        import os
        import tempfile
        import time
        from wache import zugang as Z
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lock = Path(tmp.name) / "wache.lock"
        import psutil
        start = psutil.Process().create_time()             # echter Start dieses Prozesses (PID-Prüfung)
        lock.write_text(json.dumps({"pid": os.getpid(), "start": start,
                                    "zuletzt": time.time() - vor_s}), encoding="utf-8")
        p = mock.patch.object(Z, "LOCK", lock)
        p.start()
        self.addCleanup(p.stop)
        return Z

    def test_frisch_ohne_hinweis(self):
        Z = self._lock(5)
        info = Z.laeuft()
        self.assertIsNotNone(info)
        self.assertLess(info["still_s"], Z.STILL_AB_S)
        self.assertEqual("", Z.still_hinweis(info))

    def test_haengt_mit_hinweis(self):
        Z = self._lock(300)
        info = Z.laeuft()
        self.assertIsNotNone(info)                                      # läuft noch – wird nicht weggeräumt
        self.assertIn("kein Lebenszeichen seit 5 min", Z.still_hinweis(info))
        self.assertEqual("", Z.still_hinweis(None))


class FertigmeldungVollstaendig(unittest.TestCase):

    def test_alle_bildpfade_kommen_an(self):
        import hintergrund as H
        import lernschleife
        H._test_reset()
        self.addCleanup(H._test_reset)
        lernschleife._hinweise.clear()
        pfade = " ".join(f"F:/Bilder/serie/bild_{i:02d}.png" for i in range(12))

        async def arbeit():
            return NS(text=f"Serie fertig: {pfade}", ok=True)

        async def lauf():
            with mock.patch("ui.success", lambda *a: None):
                nr = H.starten("bild_serie", "🎨 Serie", arbeit, "ada")
                await H._AUFGABEN[nr]["task"]
        asyncio.run(lauf())
        hinweis = lernschleife.hinweise_abholen("ada")
        self.assertIn("bild_11.png", hinweis)                           # vorher nach 300 Zeichen abgeschnitten
        lernschleife.melden("x" * 1000, "ada")
        zeile = next(z for z in lernschleife.hinweise_abholen("ada").splitlines() if z.startswith("- x"))
        self.assertEqual(300 + len("- "), len(zeile))                  # andere Meldungen bleiben kurz


if __name__ == "__main__":
    unittest.main()
