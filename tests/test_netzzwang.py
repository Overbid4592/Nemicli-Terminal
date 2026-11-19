"""Netz-Zwang: nur https, Klartext nur zu freigegebenen Ports auf 127.0.0.1, 0.0.0.0 nie (ohne echtes Netz)."""
import asyncio
import http.server
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx                 # noqa: E402
import netzadresse           # noqa: E402
import netzzwang as Z        # noqa: E402


class Ziele(unittest.TestCase):
    def test_regeln(self):
        self.assertIsNone(Z.ziel_pruefen("https", "example.com", None))
        self.assertIsNone(Z.ziel_pruefen("wss", "example.com", 443))
        self.assertIn("https://", Z.ziel_pruefen("http", "example.com", 80))
        self.assertIn("https://", Z.ziel_pruefen("ws", "192.168.1.5", 8000))
        self.assertIn("localhost", Z.ziel_pruefen("http", "localhost", 11434))
        self.assertIsNone(Z.ziel_pruefen("http", "127.0.0.1", 11434))
        self.assertIsNone(Z.ziel_pruefen("http", "::1", 1234))
        self.assertIn("/web lokal 5555", Z.ziel_pruefen("http", "127.0.0.1", 5555))
        for h in ("0.0.0.0", "::", "[::]", ""):
            self.assertEqual(Z.ziel_pruefen("https", h, 443), Z.MELDUNG_NULL, h)
        self.assertIn("ftp://", Z.ziel_pruefen("ftp", "example.com", 21))

    def test_eigene_freigabe(self):
        with mock.patch.dict(Z._eigene, {}, clear=True):
            self.assertTrue(Z.ziel_pruefen("http", "127.0.0.1", 6001))
            Z.lokal_erlauben(6001, "Test")
            self.assertIsNone(Z.ziel_pruefen("http", "127.0.0.1", 6001))
        with mock.patch.object(Z, "ports", lambda: {**Z.STANDARD, 6002: "eigene Freigabe"}):
            self.assertIsNone(Z.ziel_pruefen("http", "127.0.0.1", 6002))


class Text(unittest.TestCase):
    def test_befehle(self):
        gesperrt = ["curl http://example.com", "Invoke-WebRequest HTTP://example.com/x", "iwr 'ws://example.com'",
                    "curl localhost:11434/api/tags", "irm 127.0.0.1:11434", "irm [::1]:1234", "lokal:5555",
                    "python -m http.server --bind 0.0.0.0", "curl http : //example.com"]
        for t in gesperrt:
            self.assertTrue(Z.text_pruefen(t), t)
        frei = ["iwr https://example.com", "Get-Date", "Write-Host 'Ordner lokal: C:\\x'", "irm lokal:11434/api/tags",
                "curl https://127.0.0.1:8443/", "wss://example.com", "ping 10.0.0.0x", "Get-Item 10.0.0.0"]
        for t in frei:
            self.assertIsNone(Z.text_pruefen(t), t)

    def test_codewort(self):
        self.assertEqual(Z.codewort_einsetzen("irm lokal:11434/api/tags; irm LOKAL:1234/v1/models"),
                         "irm http://127.0.0.1:11434/api/tags; irm http://127.0.0.1:1234/v1/models")
        self.assertEqual(Z.codewort_einsetzen("Ordner lokal: x"), "Ordner lokal: x")

    def test_sandbox_ohne_loopback(self):
        self.assertIn("Sandbox", Z.text_pruefen("urlopen('lokal:11434')", lokal_erreichbar=False))
        self.assertIn("Sandbox", Z.text_pruefen("urlopen('http://example.com')", lokal_erreichbar=False))


class _Leise(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class ImProzess(unittest.TestCase):
    def setUp(self):
        Z.installieren()
        self.addCleanup(Z.entfernen)

    def test_httpx_sperrt_klartext_vor_der_verbindung(self):
        with mock.patch("httpcore.ConnectionPool.handle_request") as netz:
            with self.assertRaises(httpx.ConnectError) as f:
                httpx.get("http://example.com/")
            netz.assert_not_called()
        self.assertIn("Netz-Zwang", str(f.exception))

    def test_httpx_async(self):
        async def holen():
            async with httpx.AsyncClient() as c:
                await c.get("http://example.com/")
        with self.assertRaises(httpx.ConnectError):
            asyncio.run(holen())

    def test_urllib(self):
        with self.assertRaises(urllib.error.URLError) as f:
            urllib.request.urlopen("http://example.com/", timeout=2)
        self.assertIsInstance(f.exception.reason, Z.NetzGesperrt)

    def test_eigener_lokaler_dienst_geht_fremder_port_nicht(self):
        p = mock.patch.dict(Z._eigene, {}, clear=True)                     # Freigaben anderer Tests zählen nicht
        p.start()
        self.addCleanup(p.stop)
        server = http.server.HTTPServer(("127.0.0.1", 0), _Leise)        # bind gibt den Port frei
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        self.assertEqual(httpx.get(f"http://127.0.0.1:{port}/", timeout=5).text, "ok")
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read(), b"ok")
        with self.assertRaises(httpx.ConnectError) as f:
            httpx.get("http://127.0.0.1:9/", timeout=2)                    # nicht freigegeben
        self.assertIn("nicht freigegeben", str(f.exception))
        with self.assertRaises(httpx.ConnectError):
            httpx.get(f"http://localhost:{port}/", timeout=2)

    def test_null_nie(self):
        for adresse in (("0.0.0.0", 0), ("", 0)):
            s = socket.socket()
            self.addCleanup(s.close)
            with self.assertRaises(Z.NetzGesperrt):
                s.bind(adresse)
        s = socket.socket()
        self.addCleanup(s.close)
        with self.assertRaises(Z.NetzGesperrt):
            s.connect(("0.0.0.0", 80))
        s6 = socket.socket(socket.AF_INET6)
        self.addCleanup(s6.close)
        with self.assertRaises(Z.NetzGesperrt):
            s6.bind(("::", 0))

    def test_entfernen_stellt_wieder_her(self):
        Z.entfernen()
        self.assertNotIn("bind", socket.socket.__dict__)
        self.assertFalse(Z.installiert())
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.close()


class Adressen(unittest.TestCase):
    def test_sicher(self):
        with mock.patch.dict(Z._eigene, {}, clear=True):
            self.assertEqual(netzadresse.sicher("localhost:8189"), "http://127.0.0.1:8189")
            self.assertIn(8189, Z.ports())
        self.assertEqual(netzadresse.sicher("http://example.com"), "https://example.com")
        self.assertEqual(netzadresse.sicher("example.com:7860"), "https://example.com:7860")
        self.assertEqual(netzadresse.sicher("http://LOCALHOST:7861/x"), "http://127.0.0.1:7861/x")

    def test_ollama_host(self):
        import providers
        with mock.patch.dict(Z._eigene, {}, clear=True):
            self.assertEqual(providers._norm_host("localhost:11500"), "http://127.0.0.1:11500")
            self.assertIn(11500, Z.ports())
        self.assertEqual(providers._norm_host("http://example.com:11434"), "https://example.com:11434")


class Kindprozess(unittest.TestCase):
    def test_python_kind_bekommt_den_zwang(self):
        import os
        import subprocess
        import tempfile
        import paths
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        code = ("import socket, urllib.request\n"
                "try:\n    urllib.request.urlopen('http://example.com/', timeout=3)\n    print('OFFEN')\n"
                "except Exception as e:\n    print('GESPERRT' if 'Netz-Zwang' in str(e) else 'ANDERS ' + repr(e))\n"
                "s = socket.socket()\n"
                "try:\n    s.bind(('0.0.0.0', 0))\n    print('NULL OFFEN')\n"
                "except Exception:\n    print('NULL GESPERRT')\n")
        with mock.patch.object(paths, "INSTALL", Path(tmp.name)):
            env = Z.kind_umgebung({k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
        self.assertTrue((Path(tmp.name) / Z.KIND_ORDNER / "sitecustomize.py").is_file())
        self.assertIn("11434", env[Z.UMGEBUNG_PORTS])
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.split(), ["GESPERRT", "NULL", "GESPERRT"], r.stdout + r.stderr)

    def test_freigaben_nicht_aus_befehlen(self):
        self.assertIn("Nutzer", Z.text_pruefen("$env:NEMICLI_NETZ_PORTS='5555'; python x.py"))
        self.assertIn("Nutzer", Z.text_pruefen("Remove-Item C:\\x\\_netzzwang\\sitecustomize.py"))


class Werkzeuge(unittest.TestCase):
    def test_befehl_gesperrt_und_codewort(self):
        import actions
        gelaufen = []

        def powershell(argv, ordner, anzeige=None):
            gelaufen.append(argv[-1])
            return mock.Mock(stdout="", stderr="", returncode=0)

        with mock.patch.object(actions, "_powershell", powershell), \
                mock.patch.object(actions, "_ist_admin", return_value=False):
            r = actions._befehl_roh({"befehl": "curl http://example.com"})
            self.assertFalse(r.ok)
            self.assertIn("Netz-Zwang", r.text if hasattr(r, "text") else str(r))
            self.assertEqual(gelaufen, [])
            actions._befehl_roh({"befehl": "Invoke-RestMethod lokal:11434/api/tags"})
        self.assertEqual(len(gelaufen), 1)
        self.assertIn("http://127.0.0.1:11434/api/tags", gelaufen[0])
        self.assertNotIn("lokal:11434", gelaufen[0])

    def test_code_in_der_sandbox(self):
        import actions
        import sandbox
        with mock.patch.object(sandbox, "python_ausfuehren") as lauf, \
                mock.patch.object(sandbox, "ausfuehren") as lauf2:
            r = actions._code_ausfuehren({"code": "import urllib.request\nurllib.request.urlopen('http://example.com')"})
            self.assertFalse(r.ok)
            lauf.assert_not_called()
            lauf2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
