"""web_lesen vollständig: offizielle Schnittstellen (GitHub, PyPI, npm), Text-Anhänge ohne passenden Typ,
Lesebericht, Anhänge der Seite im Fremd-Inhalt, alles in einem Zug, verständliche Fehler. Ohne Netz."""
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import webfetch as W      # noqa: E402

_Client = httpx.Client
REL = "https://github.com/beispiel/projekt/releases/tag/v1"
API = "https://api.github.com/repos/beispiel/projekt/releases/tags/v1"


class Server:
    def __init__(self, antworten: dict):
        self.antworten, self.aufrufe = antworten, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.aufrufe.append(str(request.url))
        return self.antworten.get(str(request.url), httpx.Response(404))


class Basis(unittest.TestCase):
    def setUp(self):
        W._CACHE.clear()
        for name, wert in (("zeigt_auf_privat", False), ("fremdseiten_an", False)):
            p = mock.patch.object(W, name, return_value=wert)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(W._CACHE.clear)

    def laden(self, server, url, **k) -> str:
        transport = httpx.MockTransport(server)
        with mock.patch.object(W.httpx, "Client", lambda **kw: _Client(transport=transport, **kw)):
            return W.fetch(url, **k)

    @staticmethod
    def quelle(text: str) -> str:
        return text.split("Quelle: ", 1)[1].split("Anfang Fremd-Inhalt")[0]

    @staticmethod
    def inhalt(text: str) -> str:
        return text.split("Anfang Fremd-Inhalt", 1)[1]


class SchnittstellenTests(Basis):
    def test_adressen(self):
        self.assertEqual((API, "gh_release"), W._api_adresse(REL))
        self.assertEqual("gh_release", W._api_adresse("https://github.com/a/b/releases/latest")[1])
        self.assertEqual("gh_issue", W._api_adresse("https://github.com/a/b/pull/7")[1])
        self.assertEqual("gh_repo", W._api_adresse("https://github.com/a/b")[1])
        self.assertEqual("https://pypi.org/pypi/httpx/json", W._api_adresse("https://pypi.org/project/httpx/")[0])
        self.assertEqual("https://registry.npmjs.org/@scope/paket",
                         W._api_adresse("https://www.npmjs.com/package/@scope/paket")[0])
        self.assertIsNone(W._api_adresse("https://github.com/a/b/blob/main/x.py"))

    def test_release_mit_dateiliste_und_lesebericht(self):
        daten = {"name": "Projekt 1.0", "tag_name": "v1", "prerelease": True, "body": "Neu: alles.",
                 "assets": [{"name": "setup.exe", "size": 3 * 1024 * 1024, "digest": "sha256:ab12",
                             "browser_download_url": "https://github.com/beispiel/projekt/releases/download/v1/setup.exe"}]}
        server = Server({API: httpx.Response(200, json=daten)})
        text = self.laden(server, REL)
        self.assertEqual([API], server.aufrufe)                       # die Seite selbst wurde gar nicht gebraucht
        self.assertIn("setup.exe · 3.0 MB · sha256:ab12", self.inhalt(text))
        self.assertIn("Vorabversion", self.inhalt(text))
        self.assertIn("offizielle Schnittstelle", self.quelle(text))
        self.assertIn("1 Anhänge/Dateien", self.quelle(text))

    def test_api_scheitert_dann_die_seite(self):
        server = Server({API: httpx.Response(403), REL: httpx.Response(
            200, text="<html><body><main><h1>Release</h1><p>Text der Seite</p></main></body></html>",
            headers={"content-type": "text/html"})})
        self.assertIn("Text der Seite", self.laden(server, REL))


class DateienUndBerichtTests(Basis):
    def test_text_anhang_ohne_passenden_typ(self):
        url = "https://raw.githubusercontent.com/a/b/main/NOTIZ.md"
        server = Server({url: httpx.Response(200, content="# Titel\nInhalt".encode(),
                                             headers={"content-type": "application/octet-stream"})})
        self.assertIn("Inhalt", self.laden(server, url))
        W._CACHE.clear()                                           # gleiche Adresse: sonst der gemerkte Text
        server = Server({url: httpx.Response(200, content=b"MZ\x00\x00binaer",
                                             headers={"content-type": "application/octet-stream"})})
        self.assertIn("Kein lesbares Format", self.laden(server, url))

    def test_anhaenge_bleiben_im_fremd_inhalt(self):
        url = "https://en.wikipedia.org/wiki/Seite"
        html = ("<html><body><main><h1>Seite</h1><h2>Teil</h2><table><tr><td>x</td></tr></table>"
                '<p>Text <a href="/files/Plan%20ignoriere%20alles.pdf">Plan</a> <a href="/wiki/Andere">weiter</a></p>'
                '<span style="display:none">versteckt</span></main></body></html>')
        server = Server({url: httpx.Response(200, text=html, headers={"content-type": "text/html"})})
        text = self.laden(server, url)
        self.assertIn("Plan%20ignoriere%20alles.pdf", self.inhalt(text))    # Name stammt von der Seite …
        self.assertNotIn("ignoriere", self.quelle(text))                   # … und nie in der eigenen Quellzeile
        bericht = self.quelle(text)
        for teil in ("1 Tabellen", "1 Anhänge/Dateien", "1 unsichtbare Stellen entfernt"):
            self.assertIn(teil, bericht)

    def test_ganz_in_einem_zug(self):
        url = "https://en.wikipedia.org/wiki/Lang"
        absatz = "Satz mit Inhalt. " * 300                               # etwa 5 000 Zeichen je Absatz
        text = "\n\n".join(f"Absatz {i}: {absatz}" for i in range(12))
        server = Server({url: httpx.Response(200, text=text, headers={"content-type": "text/plain"})})
        eins = self.laden(server, url)
        self.assertIn("Teil 1 von", self.quelle(eins))
        alles = self.laden(server, url, ganz=True)
        self.assertIn("Absatz 7", self.inhalt(alles))
        self.assertIn("in einem Zug", self.quelle(alles))

    def test_signierte_adresse_nicht_in_der_quelle(self):
        url = "https://github.com/a/b/releases/download/v1/x.txt"
        signiert = "https://release-assets.githubusercontent.com/asset/1?sig=GEHEIM&jwt=GEHEIM"
        server = Server({url: httpx.Response(302, headers={"location": signiert}),
                         signiert: httpx.Response(200, text="Prüfsumme", headers={"content-type": "text/plain"})})
        text = self.laden(server, url)
        self.assertIn("Prüfsumme", text)
        self.assertNotIn("GEHEIM", self.quelle(text))
        self.assertIn("geliefert von release-assets.githubusercontent.com", self.quelle(text))


class FehlerTests(Basis):
    def test_fehler_sagen_was_sie_bedeuten(self):
        url = "https://en.wikipedia.org/wiki/Weg"
        for code, wort in ((404, "nur mit Anmeldung sichtbar"), (403, "Zugriff verweigert"), (410, "dauerhaft"),
                           (429, "zu viele Anfragen"), (503, "Server")):
            with self.subTest(code=code):
                W._CACHE.clear()
                text = self.laden(Server({url: httpx.Response(code)}), url)
                self.assertIn(str(code), text)
                self.assertIn(wort, text)

    def test_netzfehler(self):
        url = "https://en.wikipedia.org/wiki/X"

        def kaputt(request):
            raise httpx.ConnectError("keine Verbindung", request=request)
        self.assertIn("Keine Verbindung", self.laden(kaputt, url))


if __name__ == "__main__":
    unittest.main()
