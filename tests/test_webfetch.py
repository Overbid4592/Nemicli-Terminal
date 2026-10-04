"""web_lesen/web_wiki offline: Weiterleitungen vor dem Aufruf geprüft, DNS-Schutz,
Hauptinhalt statt Menüs, Teile mit Zwischenspeicher, Größengrenze, PDF."""
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import webfetch as W      # noqa: E402

ERLAUBT = "https://en.wikipedia.org"
_Client = httpx.Client


def ipv4(a, b, c, d):
    return ".".join(str(x) for x in (a, b, c, d))


class Server:
    """Nachgebauter Server: zählt jeden Aufruf, antwortet je Pfad."""

    def __init__(self, antworten: dict):
        self.antworten = antworten
        self.aufrufe: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.aufrufe.append(str(request.url))
        return self.antworten.get(str(request.url), httpx.Response(404))


class Basis(unittest.TestCase):
    def setUp(self):
        W._CACHE.clear()
        self.dns = mock.patch.object(W, "zeigt_auf_privat", return_value=False)
        self.dns.start()

    def tearDown(self):
        self.dns.stop()
        W._CACHE.clear()

    def laden(self, server: Server, url: str, teil: int = 1) -> str:
        transport = httpx.MockTransport(server)
        with mock.patch.object(W.httpx, "Client", lambda **kw: _Client(transport=transport, **kw)):
            return W.fetch(url, teil)


class Weiterleitungen(Basis):
    def test_ziel_wird_vor_dem_aufruf_geprueft(self):
        for ziel in (f"https://{ipv4(192, 168, 1, 1)}/admin", "http://en.wikipedia.org/x", "https://example.com/x"):
            with self.subTest(ziel=ziel):
                W._CACHE.clear()
                server = Server({f"{ERLAUBT}/a": httpx.Response(302, headers={"location": ziel})})
                text = self.laden(server, f"{ERLAUBT}/a")
                self.assertTrue(text.startswith("❌ Weiterleitung"), text)
                self.assertEqual(server.aufrufe, [f"{ERLAUBT}/a"])      # Ziel nie aufgerufen

    def test_erlaubte_weiterleitung_wird_gelesen(self):
        server = Server({f"{ERLAUBT}/a": httpx.Response(301, headers={"location": "/b"}),
                         f"{ERLAUBT}/b": httpx.Response(200, text="Ziel erreicht",
                                                        headers={"content-type": "text/plain"})})
        self.assertIn("Ziel erreicht", self.laden(server, f"{ERLAUBT}/a"))

    def test_zu_viele_weiterleitungen(self):
        server = Server({f"{ERLAUBT}/a": httpx.Response(302, headers={"location": "/a"})})
        self.assertIn("Weiterleitungen", self.laden(server, f"{ERLAUBT}/a"))


class Dns(unittest.TestCase):
    def test_domain_auf_private_adresse_gesperrt(self):
        falsch = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ipv4(10, 0, 0, 5), 443))]
        with mock.patch.object(W.socket, "getaddrinfo", return_value=falsch):
            self.assertIn("private/lokale Adresse", W._check(f"{ERLAUBT}/x"))
        richtig = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ipv4(185, 15, 59, 224), 443))]
        with mock.patch.object(W.socket, "getaddrinfo", return_value=richtig):
            self.assertIsNone(W._check(f"{ERLAUBT}/x"))


class Inhalt(Basis):
    SEITE = """<html><head><title>Testseite</title><script>var x = 1;</script></head><body>
        <header>Logo Anmelden Suche</header>
        <nav><ul><li>Start</li><li>Menü</li></ul></nav>
        <main><article><header><h1>Überschrift im Artikel</h1></header>
        <p>""" + "Wichtiger Absatz mit Inhalt. " * 30 + """</p><ul><li>Punkt eins</li></ul></article></main>
        <aside>Werbung</aside><footer>Impressum Datenschutz</footer></body></html>"""

    def test_hauptinhalt_ohne_menues(self):
        text = W._html_to_text(self.SEITE)
        self.assertIn("# Überschrift im Artikel", text)
        self.assertIn("Wichtiger Absatz", text)
        self.assertIn("- Punkt eins", text)
        for weg in ("Logo", "Menü", "Werbung", "Impressum", "var x"):
            self.assertNotIn(weg, text)

    def test_kaputtes_html_verschluckt_nicht_alles(self):
        text = W._html_to_text("<html><body><nav>Menü<p>" + "Echter Text. " * 40)   # nav nie geschlossen
        self.assertIn("Echter Text", text)

    def test_teile_und_zwischenspeicher(self):
        lang = "\n\n".join(f"Absatz {i}: " + "Wort " * 40 for i in range(200))
        server = Server({f"{ERLAUBT}/lang": httpx.Response(200, text=lang,
                                                           headers={"content-type": "text/plain"})})
        erster = self.laden(server, f"{ERLAUBT}/lang")
        self.assertRegex(erster, r"Teil 1 von \d+ – weiterlesen: web_lesen mit url: .* und teil: 2")
        zweiter = self.laden(server, f"{ERLAUBT}/lang", teil=2)
        self.assertIn("Teil 2 von", zweiter)
        self.assertEqual(len(server.aufrufe), 1)                       # Teil 2 aus dem Zwischenspeicher
        stuecke = W._teile(lang)
        self.assertTrue(all(len(s) <= W.MAX_OUT for s in stuecke))
        self.assertEqual("".join(stuecke).replace("\n", "").replace(" ", ""),
                         lang.replace("\n", "").replace(" ", ""))       # nichts verloren

    def test_zu_gross(self):
        server = Server({f"{ERLAUBT}/gross": httpx.Response(
            200, content=b"x", headers={"content-type": "text/plain", "content-length": str(W.MAX_BYTES + 1)})})
        self.assertIn("größer als", self.laden(server, f"{ERLAUBT}/gross"))

    def test_pdf(self):
        import pdfgen
        with tempfile.TemporaryDirectory() as ordner:
            pfad = Path(ordner) / "t.pdf"
            pdfgen.markdown_to_pdf("# Titel\n\nText im PDF zum Lesen.", pfad)
            daten = pfad.read_bytes()
        server = Server({f"{ERLAUBT}/d.pdf": httpx.Response(200, content=daten,
                                                            headers={"content-type": "application/pdf"})})
        text = self.laden(server, f"{ERLAUBT}/d.pdf")
        self.assertIn("[Seite 1]", text)
        self.assertIn("Text im PDF", text)

    def test_binaerdatei_abgelehnt(self):
        server = Server({f"{ERLAUBT}/x.zip": httpx.Response(200, content=b"PK\x03\x04",
                                                            headers={"content-type": "application/zip"})})
        self.assertIn("Kein lesbares Format", self.laden(server, f"{ERLAUBT}/x.zip"))


if __name__ == "__main__":
    unittest.main()
