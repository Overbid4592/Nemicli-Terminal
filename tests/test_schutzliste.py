"""Schutzliste bekannter Schadadressen, Allowlist-Seiten über Ollama (Netz gemockt, nur .example-Adressen)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import schutzliste as S        # noqa: E402
import webfetch as W           # noqa: E402

KURATIERT = ({"gut.example"}, {"gut.example", "example"})

LISTE_A = "\n".join([
    "! Kommentar",
    "||schaden.example^$doc",
    "||ganz.example^",
    "||mitopt.example^$doc,reason=Betrug",
    "||kontext.example^$3p",                   # nur im Zusammenhang: gilt nicht
    "||nurskript.example^$script",
    "||weg.example^$all",
    "@@||weg.example^$all",                    # Ausnahme hebt auf
    "example.org##.werbung",                   # kosmetisch
    "/regex[a-z]+/",
    "||stern*.example^",
    "||gut.example^$all",                      # kuratiert: Host-Regel gilt nicht
    "||sub.gut.example^",                      # Unterdomain der kuratierten auch nicht
    "||gut.example/boese/datei.exe^$document", # Pfad auf kuratierter Domain gilt
])
LISTE_B = "\n".join(["# Kommentar", "nackt.example", "||1.2.3.4/bins/x.sh^$document",
                     "||pfad.example/a/b^$all"])


class Regeln(unittest.TestCase):
    def test_formen(self):
        self.assertEqual(S.regel("||schaden.example^$doc"), ("schaden.example", False))
        self.assertEqual(S.regel("@@||schaden.example^"), ("schaden.example", True))
        self.assertEqual(S.regel("||Pfad.example/A/b^$all"), ("pfad.example/a/b", False))
        self.assertEqual(S.regel("nackt.example"), ("nackt.example", False))
        self.assertEqual(S.regel("||1.2.3.4^"), ("1.2.3.4", False))
        for z in ("", "! x", "# x", "[Adblock Plus]", "a.example##.x", "||a.example^$3p",
                  "||a.example^$domain=b.example", "||a*.example^", "/regex/", "|https://a.example/",
                  "||a.example^b^", "0.0.0.0 a.example", "keinpunkt"):
            self.assertIsNone(S.regel(z), z)

    def test_auswerten(self):
        k = S.auswerten(LISTE_A, KURATIERT)
        for a in ("schaden.example", "ganz.example", "mitopt.example", "gut.example/boese/datei.exe"):
            self.assertIn(S.kuerzel(a), k, a)
        for a in ("kontext.example", "nurskript.example", "weg.example", "stern.example", "gut.example",
                  "sub.gut.example"):
            self.assertNotIn(S.kuerzel(a), k, a)

    def test_kandidaten(self):
        k = S.kandidaten("https://a.b.schaden.example/x/y.zip?q=1#frag")
        for erwartet in ("a.b.schaden.example", "b.schaden.example", "schaden.example",
                         "schaden.example/x", "schaden.example/x/y.zip", "schaden.example/x/y.zip?q=1"):
            self.assertIn(erwartet, k)
        self.assertNotIn("example", k)                      # die Endung allein nie
        self.assertNotIn("schaden.example/x/y", k)          # „.“ trennt nicht
        self.assertFalse(any("frag" in x for x in k))
        self.assertEqual(S.kandidaten("https://1.2.3.4/bins/x.sh"),
                         ["1.2.3.4", "1.2.3.4/bins", "1.2.3.4/bins/x.sh"])
        self.assertEqual(S.kandidaten("kaputt"), [])
        self.assertLessEqual(len(S.kandidaten("https://a.example/" + "x/" * 500)), 2 * (S.MAX_TRENNER + 2))


class Speicher(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        self.antworten = {"badware": LISTE_A, "urlhaus": LISTE_B, "phishing": "||phish.example^$all"}
        for p in (mock.patch.object(S, "_ordner", lambda: self.ordner),
                  mock.patch.object(S, "an", lambda: True),
                  mock.patch.object(S, "_allowlist_schutz", lambda: KURATIERT),
                  mock.patch.object(S, "_holen", self._holen),
                  mock.patch.object(S, "_geladen", None),
                  mock.patch.object(S, "_moment_geprueft", False),
                  mock.patch.object(S, "_mitgeliefert", lambda: (self.ordner / "fehlt.bin", self.ordner / "fehlt.json"))):
            p.start()
            self.addCleanup(p.stop)

    def _holen(self, adressen):
        for name, (_, a) in S.QUELLEN.items():
            if a == adressen:
                wert = self.antworten[name]
                if isinstance(wert, Exception):
                    raise wert
                return wert
        raise AssertionError(adressen)

    def test_sperren_und_meldung(self):
        stand = S.aktualisieren()
        self.assertEqual(stand["quellen"]["badware"]["eintraege"], 4)
        self.assertEqual(S.gesperrt("https://www.schaden.example/irgendwas"), "uBlock: Badware")
        self.assertEqual(S.gesperrt("https://1.2.3.4/bins/x.sh"), "URLhaus: Schadsoftware")
        self.assertIsNone(S.gesperrt("https://1.2.3.4/bins/anders.sh"))
        self.assertEqual(S.gesperrt("https://pfad.example/a/b/c"), "URLhaus: Schadsoftware")
        self.assertIsNone(S.gesperrt("https://pfad.example/a/bc"))
        self.assertIsNone(S.gesperrt("https://gut.example/"))
        self.assertTrue(S.gesperrt("https://gut.example/boese/datei.exe"))
        self.assertEqual(S.gesperrt("https://phish.example/login"), "Phishing")
        self.assertIn("nicht geladen", S.meldung("https://phish.example/login", "Phishing"))

    def test_nur_kuerzel_auf_der_platte(self):
        S.aktualisieren()
        roh = (self.ordner / "schutzliste.bin").read_bytes()
        self.assertEqual(len(roh) % 9, 0)
        self.assertNotIn(b"schaden", roh)
        self.assertNotIn("schaden", (self.ordner / "schutzliste.json").read_text(encoding="utf-8"))

    def test_fehlende_quelle_behaelt_alten_stand(self):
        S.aktualisieren()
        self.antworten["phishing"] = RuntimeError("HTTP 503")
        stand = S.aktualisieren()
        self.assertEqual(S.gesperrt("https://phish.example/"), "Phishing")
        self.assertEqual(stand["quellen"]["phishing"]["fehler"], "HTTP 503")
        self.assertIn("alter Stand bleibt", S.status_text())

    def test_ohne_liste_und_ausgeschaltet(self):
        self.assertIsNone(S.gesperrt("https://schaden.example/"))       # noch nichts geladen
        S.aktualisieren()
        with mock.patch.object(S, "an", lambda: False):
            self.assertIsNone(S.gesperrt("https://schaden.example/"))
            self.assertIn("aus", S.status_text())

    def test_einmal_gesperrt_immer_gesperrt(self):
        S.aktualisieren()
        self.antworten["phishing"] = "||anders.example^$all"           # phish.example fällt aus der Quelle
        stand = S.aktualisieren()
        self.assertEqual(S.gesperrt("https://phish.example/"), "Phishing")
        self.assertEqual(S.gesperrt("https://anders.example/"), "Phishing")
        self.assertEqual(stand["frueher"], 1)
        self.assertEqual(stand["quellen"]["phishing"]["eintraege"], 1)
        self.assertIn("aus früheren Ständen: 1", S.status_text())

    def test_kuratiert_gilt_auch_fuer_alte_eintraege(self):
        alt = {S.kuerzel("gut.example"): 1, S.kuerzel("gut.example/alt"): 1}
        S._schreiben(self.ordner / "schutzliste.bin", S._vereinigen([alt]))
        self.assertIsNone(S.gesperrt("https://gut.example/"))
        self.assertIsNone(S.gesperrt("https://sub.gut.example/"))
        self.assertEqual(S.gesperrt("https://gut.example/alt"), "uBlock: Badware")

    def test_ungeordnete_datei_wird_vereint(self):
        roh = b"".join(S.kuerzel(a).to_bytes(8, "big") + bytes([b])
                       for a, b in (("b.example", 1), ("a.example", 2), ("b.example", 4)))
        (self.ordner / "x.bin").write_bytes(roh)
        daten = S._lesen(self.ordner / "x.bin")
        self.assertEqual(len(daten) // S.SATZ, 2)
        self.assertLess(S._schluessel(daten, 0), S._schluessel(daten, 1))
        self.assertEqual(S._suchen(daten, S.kuerzel("b.example")), 5)
        self.assertEqual(S._suchen(daten, S.kuerzel("a.example")), 2)
        self.assertEqual(S._suchen(daten, S.kuerzel("c.example")), 0)

    def test_ohne_numpy(self):
        """Die exe bringt numpy nicht mit (kommt erst mit der Python-Umgebung) – die Schutzliste braucht es nie."""
        import importlib
        with mock.patch.dict(sys.modules, {"numpy": None}):
            neu = importlib.reload(S)
            try:
                with mock.patch.object(neu, "_ordner", lambda: self.ordner), \
                        mock.patch.object(neu, "an", lambda: True), \
                        mock.patch.object(neu, "_allowlist_schutz", lambda: KURATIERT), \
                        mock.patch.object(neu, "_holen", self._holen), \
                        mock.patch.object(neu, "_mitgeliefert",
                                          lambda: (self.ordner / "f.bin", self.ordner / "f.json")):
                    neu.aktualisieren()
                    self.assertEqual(neu.gesperrt("https://phish.example/"), "Phishing")
            finally:
                importlib.reload(S)

    def test_momentaufnahme_schuetzt_ab_dem_ersten_start(self):
        bau = self.ordner / "bau"
        bau.mkdir()
        S.momentaufnahme(bau)
        self.assertTrue((bau / S.MOMENT_BIN).is_file())
        self.assertNotIn(b"phish", (bau / S.MOMENT_BIN).read_bytes())
        neu = self.ordner / "neu"                                        # frische Installation, kein Netz
        with mock.patch.object(S, "_ordner", lambda: neu), \
                mock.patch.object(S, "_mitgeliefert", lambda: (bau / S.MOMENT_BIN, bau / S.MOMENT_META)), \
                mock.patch.object(S, "_geladen", None), mock.patch.object(S, "_moment_geprueft", False):
            self.assertEqual(S.gesperrt("https://phish.example/"), "Phishing")
            self.assertTrue(S.meta().get("momentaufnahme"))
            self.assertIn("Momentaufnahme im Programm", S.status_text())
            mtime = (neu / "schutzliste.bin").stat().st_mtime_ns
            with mock.patch.object(S, "_moment_geprueft", False):
                S._laden()                                               # dieselbe Momentaufnahme nur einmal
            self.assertEqual((neu / "schutzliste.bin").stat().st_mtime_ns, mtime)

    def test_kein_hintergrund_im_test(self):
        with mock.patch.object(S, "_hintergrund_lief", False), \
                mock.patch.object(S.threading, "Thread") as faden:
            S._hintergrund_pruefen()
        faden.assert_not_called()


class Webzugang(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(S, "gesperrt", lambda url: "Phishing" if "phish.example" in url else None)
        p.start()
        self.addCleanup(p.stop)

    def test_lesen_gesperrt(self):
        with mock.patch.object(W, "_laden") as laden, mock.patch.object(W, "fremd_laden") as fremd:
            antwort = W.text_holen("https://phish.example/login")
        self.assertIn("⛔", antwort)
        laden.assert_not_called()
        fremd.assert_not_called()

    def test_weiterleitung_auf_gesperrte_seite(self):
        self.assertIn("⛔", W._check("https://phish.example/x"))

    def test_suchtreffer_ausgeblendet(self):
        daten = {"results": [{"title": "A", "url": "https://phish.example/a", "content": "x"},
                             {"title": "B", "url": "https://gut.example/b", "content": "y"}]}
        with mock.patch.object(W, "_ollama_post", return_value=daten), \
                mock.patch.object(W, "fremdseiten_an", return_value=True):
            text, _ = W.search_ergebnis("frage")
        self.assertNotIn("phish.example", text)
        self.assertIn("gut.example", text)
        self.assertIn("⛔ 1 Treffer auf der Schutzliste", text)

    def test_anhaenge_ohne_gesperrte(self):
        links = ["https://phish.example/x.exe", "https://gut.example/y.pdf"]
        self.assertEqual(W._anhaenge(links, "https://gut.example/"), ["https://gut.example/y.pdf"])


class AllowlistUeberOllama(unittest.TestCase):
    URL = "https://openai.com/news/"

    def _holen(self, laden, fremd):
        with mock.patch.object(W, "_laden", return_value=laden), \
                mock.patch.object(W, "fremd_laden", return_value=fremd), \
                mock.patch.object(W, "fremdseiten_an", return_value=True), \
                mock.patch.object(W, "_gemerkt", return_value=None), \
                mock.patch.object(W, "_merken"), \
                mock.patch.object(W, "_ueber_api", return_value=None), \
                mock.patch.object(W, "zeigt_auf_privat", return_value=False):
            return W.text_holen(self.URL)

    def test_abgewiesen_liest_ueber_ollama_als_fremd(self):
        quelle, text, tier = self._holen(W._Abgewiesen("❌ openai.com meldet 403"), ("x", "Inhalt"))
        self.assertEqual((text, tier), ("Inhalt", W.FREMD_TIER))      # kam nicht direkt vom Server der Domain
        self.assertIn("über Ollama gelesen", quelle)
        self.assertIn("Tier 1", quelle)

    def test_ollama_scheitert_alte_meldung(self):
        self.assertEqual(self._holen(W._Abgewiesen("❌ 403"), "❌ Ollama kaputt"), "❌ 403")

    def test_anderer_fehler_ohne_umweg(self):
        with mock.patch.object(W, "fremd_laden") as fremd:
            with mock.patch.object(W, "_laden", return_value="❌ 404"), \
                    mock.patch.object(W, "fremdseiten_an", return_value=True), \
                    mock.patch.object(W, "_gemerkt", return_value=None), \
                    mock.patch.object(W, "_ueber_api", return_value=None), \
                    mock.patch.object(W, "zeigt_auf_privat", return_value=False):
                self.assertEqual(W.text_holen(self.URL), "❌ 404")
        fremd.assert_not_called()


class Allowlist(unittest.TestCase):
    def test_offizielle_anbieter(self):
        eintraege = {e["domain"]: e for e in json.loads((ROOT / "allowlist.json").read_text(encoding="utf-8"))["allow"]}
        for d in ("openai.com", "anthropic.com", "blog.google", "deepmind.google", "mistral.ai", "x.ai",
                  "ai.meta.com", "qwen.ai", "deepseek.com", "lmstudio.ai", "vllm.ai", "comfy.org"):
            self.assertEqual(eintraege[d]["tier"], 1, d)
        self.assertNotIn("platform.openai.com", eintraege)               # steckt in openai.com
        self.assertIn("qwenlm.github.io", eintraege)
        self.assertNotIn("github.io", eintraege)                         # freie Hosting-Wildcard bleibt draußen
        self.assertTrue(W._match("https://developers.openai.com/api"))


if __name__ == "__main__":
    unittest.main()
