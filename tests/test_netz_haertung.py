"""Netz-Härtung: Allowlist (genauester Eintrag, Archiv), Gesamtfristen, Dateisperre der ChatGPT-Anmeldung,
exklusive lokale Server, Recherche ohne Fremd-/Steuerseiten, private Adressen, WebUI-Schriften,
Warnungen bei freien Hostern, Prozess-Zeitgrenzen, Update-Quellen, HF-Downloads, Wiederholen bei 429/503,
CVSS-Stufen und die llama.cpp-Prüfung über OSV. Alles offline mit Attrappen."""
import asyncio
import hashlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import httpx  # noqa: E402

import aktualisieren as AK  # noqa: E402
import chatgpt_login as L  # noqa: E402
import einstellungen as E  # noqa: E402
import hfladen as H  # noqa: E402
import lokalserver  # noqa: E402
import luecken  # noqa: E402
import netzhilfe  # noqa: E402
import prozesslauf  # noqa: E402
import providers  # noqa: E402
import recherche as R  # noqa: E402
import updater  # noqa: E402
import webfetch as W  # noqa: E402
import webui  # noqa: E402

_Client = httpx.Client


def _transport(behandeln):
    return mock.patch.object(W.httpx, "Client", lambda **kw: _Client(transport=httpx.MockTransport(behandeln), **kw))


# --- N-02 / N-03 / Zusatz: Allowlist ------------------------------------------------------------------------

class Allowlist(unittest.TestCase):
    def setUp(self):
        self.basis = W._load_base()
        p = mock.patch.dict(W._ALLOW, self.basis, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_kein_eintrag_verdeckt(self):
        for d in self.basis:
            with self.subTest(domain=d):
                self.assertEqual(W._match(f"https://{d}/x")["domain"], d)
                self.assertEqual(W._match(f"https://sub.{d}/x")["domain"], d)

    def test_genauester_gewinnt(self):
        for host, tier in (("discourse.ubuntu.com", 2), ("discuss.python.org", 2),
                           ("forums.developer.nvidia.com", 2)):
            if host in W._ALLOW:
                with self.subTest(host=host):
                    self.assertEqual(W._match(f"https://{host}/t/1")["tier"], tier)

    def test_neue_sicherheitsquellen(self):
        for url in ("https://cveawg.mitre.org/api/cve/CVE-2025-0001", "https://osv.dev/vulnerability/X",
                    "https://api.osv.dev/v1/query"):
            with self.subTest(url=url):
                self.assertEqual(W._match(url)["tier"], 1)

    def test_archiv_tier_2(self):
        self.assertEqual(W._match("https://web.archive.org/web/2020/https://example.org/")["tier"], 2)


class Archiv(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(W, "zeigt_auf_privat", return_value=False),
                  mock.patch.object(W, "_schutz", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def test_save_gesperrt(self):
        for url in ("https://web.archive.org/save/https://example.org/?d=geheim",
                    "https://web.archive.org/save", "https://web.archive.org/%73ave/https://x.example/"):
            with self.subTest(url=url):
                self.assertIn("save", W._check(url) or "")

    def test_eingebettete_adresse_geprueft(self):
        for url in ("https://web.archive.org/web/2020/http://192.168.1.1/admin",
                    "https://web.archive.org/web/2020id_/http://localhost:8080/",
                    "https://web.archive.org/web/2020/https://pastebin.com/abc",
                    "https://web.archive.org/web/2020/bit.ly/xyz"):
            with self.subTest(url=url):
                self.assertTrue((W._check(url) or "").startswith("❌"), url)

    def test_normale_kopie_erlaubt(self):
        self.assertIsNone(W._check("https://web.archive.org/web/20200101000000/http://example.org/seite"))
        self.assertIsNone(W._check("https://archive.org/details/etwas"))

    def test_schutzliste_der_eingebetteten_adresse(self):
        with mock.patch.object(W, "_schutz", side_effect=lambda u: "⛔ gesperrt" if "boese.example" in u else None):
            self.assertEqual(W._check("https://web.archive.org/web/2020/https://boese.example/"), "⛔ gesperrt")


# --- N-04 / N-14: Gesamtfrist, Wiederholen, gemeinsamer Client ---------------------------------------------

class _Tropfen(httpx.SyncByteStream):
    def __iter__(self):
        for _ in range(50):
            time.sleep(0.02)
            yield b"x"


class Fristen(unittest.TestCase):
    def setUp(self):
        W._CACHE.clear()
        for p in (mock.patch.object(W, "zeigt_auf_privat", return_value=False),
                  mock.patch.object(W, "fremdseiten_an", return_value=False),
                  mock.patch.object(W, "LADEN_FRIST_S", 0.1)):
            p.start()
            self.addCleanup(p.stop)

    def test_tropfender_server_wird_abgebrochen(self):
        with _transport(lambda req: httpx.Response(200, stream=_Tropfen(), headers={"content-type": "text/plain"})):
            start = time.monotonic()
            text = W._laden("https://en.wikipedia.org/a")
        self.assertIsInstance(text, str)
        self.assertIn("zu langsam", text)
        self.assertLess(time.monotonic() - start, 0.9)

    def test_429_einmal_wiederholt(self):
        aufrufe = []

        def behandeln(req):
            aufrufe.append(req)
            if len(aufrufe) == 1:
                return httpx.Response(429, headers={"retry-after": "1"})
            return httpx.Response(200, text="da", headers={"content-type": "text/plain"})
        with mock.patch.object(W, "LADEN_FRIST_S", 30.0), \
                mock.patch.object(netzhilfe, "_schlafen") as schlafen, _transport(behandeln):
            geladen = W._laden("https://en.wikipedia.org/a")
        self.assertEqual(geladen[2], b"da")
        self.assertEqual(len(aufrufe), 2)
        schlafen.assert_called_once_with(1.0)

    def test_lange_pause_nicht_abgewartet(self):
        with mock.patch.object(W, "LADEN_FRIST_S", 30.0), mock.patch.object(netzhilfe, "_schlafen") as schlafen, \
                _transport(lambda req: httpx.Response(503, headers={"retry-after": "60"})):
            geladen = W._laden("https://en.wikipedia.org/a")
        self.assertIsInstance(geladen, W._Abgewiesen)
        schlafen.assert_not_called()

    def test_abrufen_frist_und_groesse(self):
        c = _Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=_Tropfen())))
        with self.assertRaises(netzhilfe.FristAbgelaufen):
            netzhilfe.abrufen(c, "GET", "https://example.org/", frist_s=0.1)
        c = _Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"y" * 100)))
        with self.assertRaises(netzhilfe.ZuGross):
            netzhilfe.abrufen(c, "GET", "https://example.org/", max_bytes=10)
        r = netzhilfe.abrufen(c, "GET", "https://example.org/")
        self.assertEqual(r.content, b"y" * 100)
        r.raise_for_status()

    def test_gemeinsamer_client_ohne_cookies(self):
        g = netzhilfe.GeteilterClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, headers={"set-cookie": "id=1; Path=/"}, text=req.headers.get("cookie", ""))))
        c = g()
        self.assertIs(g(), c)
        c.get("https://example.org/")
        self.assertEqual(c.get("https://example.org/").text, "")

    def test_pdf_zerlegen_begrenzt(self):
        class Seite:
            def extract_text(self):
                time.sleep(0.03)
                return "Text"

        class Leser:
            def __init__(self, _):
                self.pages = [Seite() for _ in range(40)]
        import types
        with mock.patch.dict(sys.modules, {"pypdf": types.SimpleNamespace(PdfReader=Leser)}), \
                mock.patch.object(W, "PDF_FRIST_S", 0.1):
            text = W._pdf_text(b"%PDF-")
        self.assertIn("nur die ersten", text)
        self.assertLess(text.count("[Seite "), 40)


# --- N-05: ChatGPT-Anmeldung über Prozesse hinweg -----------------------------------------------------------

class _Antwort:
    def __init__(self, status, daten):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


class ChatgptSperre(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(L, "KONTO_DATEI", Path(self.tmp.name) / "chatgpt_konto.json")
        p.start()
        self.addCleanup(p.stop)
        L._im_speicher.clear()
        self.addCleanup(L._im_speicher.clear)

    def _konto(self, refresh, access, ablauf_in):
        return {"client_id": "oaiapp_test", "access_token": access, "refresh_token": refresh,
                "scope": L.SCOPES, "expires_at": time.time() + ablauf_in, "earliest_refresh_at": None}

    def test_anderer_prozess_hat_schon_erneuert(self):
        L._schreiben(self._konto("neu-refresh", "neu-access", 3600))     # Stand des anderen Prozesses
        veraltet = self._konto("alt-refresh", "alt-access", -10)
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(400, {"error": "refresh_token_reused"})):
            with L._dateisperre():
                L._erneuern(veraltet)
        self.assertEqual(veraltet["access_token"], "neu-access")
        self.assertEqual(L._lesen()["refresh_token"], "neu-refresh")     # nichts gelöscht

    def test_wirklich_abgelaufen_loescht(self):
        L._schreiben(self._konto("alt-refresh", "alt-access", -10))
        d = L._lesen()
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(400, {"error": "invalid_grant"})):
            with self.assertRaises(L.AnmeldungNoetig), L._dateisperre():
                L._erneuern(d)
        self.assertNotIn("refresh_token", L._lesen())

    def test_zugang_liest_unter_der_sperre_neu(self):
        L._schreiben(self._konto("alt-refresh", "alt-access", -10))
        echt = L._lesen

        def lesen():
            d = echt()
            if d.get("refresh_token") == "alt-refresh" and lesen.n == 0:
                lesen.n += 1
                L._schreiben(self._konto("neu-refresh", "neu-access", 3600))   # anderes Fenster dazwischen
            return d
        lesen.n = 0
        with mock.patch.object(L, "_lesen", lesen), \
                mock.patch.object(L.httpx, "post", side_effect=AssertionError("kein zweites Erneuern")):
            self.assertEqual(L.zugang(), "neu-access")

    def test_sperre_ist_exklusiv(self):
        frei, fertig = threading.Event(), threading.Event()

        def halten():
            with L._dateisperre():
                frei.set()
                fertig.wait(5)
        t = threading.Thread(target=halten)
        t.start()
        try:
            frei.wait(5)
            with self.assertRaises(RuntimeError):
                with L._dateisperre(warten_s=0.2):
                    pass
        finally:
            fertig.set()
            t.join(5)
        with L._dateisperre(warten_s=1):
            pass

    def test_zwischendatei_eindeutig_und_aufgeraeumt(self):
        L._schreiben({"client_id": "x"})
        L._schreiben({"client_id": "y"})
        self.assertEqual([p.name for p in Path(self.tmp.name).glob("*.tmp")], [])
        self.assertEqual(L._lesen()["client_id"], "y")


# --- N-06: lokale Server exklusiv ---------------------------------------------------------------------------

class LokalServer(unittest.TestCase):
    def test_blocker_mit_reuseaddr_verhindert_start(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            with self.assertRaises(OSError):
                lokalserver.Server(("127.0.0.1", port), webui.BaseHTTPRequestHandler).server_close()
        finally:
            blocker.close()

    def test_zweiter_mit_reuseaddr_kommt_nicht_dazu(self):
        s = lokalserver.LeiserServer(("127.0.0.1", 0), webui.BaseHTTPRequestHandler)
        try:
            self.assertFalse(s.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR))
            zweit = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            zweit.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                with self.assertRaises(OSError):
                    zweit.bind(("127.0.0.1", s.server_address[1]))
            finally:
                zweit.close()
        finally:
            s.server_close()

    def test_nur_loopback(self):
        for host in ("0.0.0.0", "192.168.1.5", "localhost"):
            with self.subTest(host=host), self.assertRaises(OSError):
                lokalserver.EinzelServer((host, 0), webui.BaseHTTPRequestHandler)

    def test_alle_server_nutzen_die_klasse(self):
        for datei in ("tools/webui.py", "tools/chromebridge.py", "tools/spiele.py", "engines/chatgpt_login.py"):
            with self.subTest(datei=datei):
                text = (ROOT / datei).read_text(encoding="utf-8")
                self.assertIn("lokalserver.", text)
                self.assertNotRegex(text, r"(?<!lokalserver\.)\b(Threading)?HTTPServer\(\(")


# --- N-07: Recherche ----------------------------------------------------------------------------------------

class Recherche(unittest.TestCase):
    def _lauf(self, modus, verdaechtig=()):
        gelesen = []
        treffer = [{"titel": "fremd", "url": "https://fremd.example/a", "text": "", "tier": W.FREMD_TIER},
                   {"titel": "steuer", "url": "https://steuer.example/b", "text": "", "tier": 1},
                   {"titel": "gut", "url": "https://gut.example/c", "text": "", "tier": 1}]

        def holen(url):
            gelesen.append(url)
            return url, "Inhalt", 1
        stand = R.Stand("Frage", teilfragen=[{"frage": "f", "suche": "s", "fach": ""}])

        async def ask(prompt, system):
            return "- Fakt"
        umg = R.Umgebung(ask=ask)
        with mock.patch.object(W, "treffer", return_value=treffer), \
                mock.patch.object(W, "text_holen", holen), \
                mock.patch.object(W, "fremdseiten_modus", return_value=modus), \
                mock.patch.object(W, "verdaechtig", side_effect=lambda u: u in verdaechtig):
            asyncio.run(R._teilfrage_suchen(stand, umg, 0, time.monotonic() + 60))
        return gelesen

    def test_fragen_liest_keine_fremdseite(self):
        self.assertNotIn("https://fremd.example/a", self._lauf("fragen"))
        self.assertIn("https://fremd.example/a", self._lauf("frei"))

    def test_steuerseite_uebersprungen(self):
        gelesen = self._lauf("frei", verdaechtig={"https://steuer.example/b"})
        self.assertNotIn("https://steuer.example/b", gelesen)
        self.assertIn("https://gut.example/c", gelesen)

    def test_frist_um_den_faden(self):
        async def go():
            return await R._im_faden(0.05, time.sleep, 0.5)
        self.assertIn("keine Antwort", asyncio.run(go()))


# --- N-08 ----------------------------------------------------------------------------------------------------

class PrivateAdressen(unittest.TestCase):
    def test_nicht_global_ist_privat(self):
        for h in ("100.64.1.1", "100.127.255.254", "192.0.2.1", "198.18.0.1", "::ffff:10.0.0.1", "[::1]",
                  "fc00::1", "169.254.1.1", "0.0.0.0", "127.0.0.1", "224.0.0.1", "x.localhost"):
            with self.subTest(h=h):
                self.assertTrue(W.is_private(h))
        for h in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "example.org"):
            with self.subTest(h=h):
                self.assertFalse(W.is_private(h))


# --- N-09 ----------------------------------------------------------------------------------------------------

class WebuiSchriften(unittest.TestCase):
    def test_vorgabe_ohne_fremde_server(self):
        s = webui.seite(False)
        self.assertNotIn("fonts.googleapis", s)
        self.assertNotIn("cdnfonts", s)
        self.assertIn('<meta name="referrer" content="no-referrer">', s)
        self.assertNotIn("@@", s)
        self.assertIn('rel="noopener noreferrer"', s)

    def test_eingeschaltet_wie_bisher(self):
        s = webui.seite(True)
        self.assertIn("fonts.googleapis.com/css2?family=Atkinson+Hyperlegible", s)
        self.assertIn("https://fonts.gstatic.com", s)
        self.assertIn("fonts.cdnfonts.com/css/opendyslexic", s)

    def test_schalter_aus_config(self):
        with mock.patch("config.load", return_value={webui.SCHRIFTEN_SCHALTER: True}):
            self.assertTrue(webui.fremde_schriften())
        with mock.patch("config.load", return_value={}):
            self.assertTrue(webui.fremde_schriften())          # Vorgabe: gut lesbare Schriften an
        with mock.patch("config.load", return_value={webui.SCHRIFTEN_SCHALTER: False}):
            self.assertFalse(webui.fremde_schriften())


# --- N-10 ----------------------------------------------------------------------------------------------------

class HosterWarnung(unittest.TestCase):
    def test_warnungen(self):
        self.assertIn("öffentliches Suffix", E.warnung("co.uk"))
        self.assertIn("ALLER Kunden", E.warnung("workers.dev"))
        for d in ("x.workers.dev", "app.azurewebsites.net", "d1.cloudfront.net", "y.hf.space", "z.r2.dev",
                  "user.codeberg.page", "a.appspot.com", "b.fly.dev"):
            with self.subTest(d=d):
                self.assertIn("WARNUNG", E.warnung(d))
        self.assertEqual(E.warnung("example.org"), "")
        self.assertEqual(E.warnung("bbc.co.uk"), "")

    def test_keine_sperre_aber_im_pruefenster(self):
        self.assertEqual(E.domain("x.workers.dev"), "x.workers.dev")
        with mock.patch("actions.web_tainted", return_value=False), \
                mock.patch.object(E, "laden", return_value={"hinzu": [], "weg": []}), \
                mock.patch.object(W, "is_allowed", return_value=False), \
                mock.patch.object(W, "mitgeliefert", return_value=set()), \
                mock.patch.object(E, "datei", return_value=Path(tempfile.gettempdir()) / "gibt_es_nicht.json"):
            self.assertIn("WARNUNG", E.vorschau("internet_erlauben", "x.workers.dev"))


# --- N-11 ----------------------------------------------------------------------------------------------------

class Prozesse(unittest.TestCase):
    def test_enkel_mit_offener_pipe_haelt_nicht_auf(self):
        code = ("import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)")
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            prozesslauf.laufen([sys.executable, "-c", code], timeout=1.5)
        self.assertLess(time.monotonic() - start, 20)

    def test_ausgabe(self):
        code, aus, fehler = prozesslauf.laufen([sys.executable, "-c", "import sys; print('ja'); "
                                                "sys.stderr.write('f'); sys.exit(3)"], timeout=30)
        self.assertEqual((code, aus.strip(), fehler), (3, b"ja", b"f"))


# --- N-12 ----------------------------------------------------------------------------------------------------

class UpdateQuellen(unittest.TestCase):
    def test_remote_adressen(self):
        for url in ("https://github.com/x/y.git", "ssh://git@example.org/x.git", "git@github.com:x/y.git"):
            with self.subTest(url=url):
                self.assertTrue(updater.remote_sicher(url))
        for url in ("http://example.org/x.git", "git://example.org/x", "file:///C:/repo", "C:/repo", "../repo",
                    "ext::sh -c x", "ssh://-oProxyCommand=x/y", "", "https://a b"):
            with self.subTest(url=url):
                self.assertFalse(updater.remote_sicher(url))

    def _run(self, url, signiert=False):
        aufrufe = []

        def git(*args, timeout=120):
            aufrufe.append(args)
            if args[:2] == ("remote", "get-url"):
                return 0, url
            if args[0] == "status":
                return 0, ""
            if args[0] == "symbolic-ref":
                return 0, "main"
            if args[0] == "config":
                return 0, "origin"
            return 0, "abc"
        with mock.patch.object(updater, "_git", git), \
                mock.patch.object(updater, "_signaturen_pruefen", return_value=signiert):
            return updater.run(lambda s: None), aufrufe

    def test_unsicherer_remote_kein_pull(self):
        res, aufrufe = self._run("http://example.org/x.git")
        self.assertFalse(res["ok"])
        self.assertFalse(any(a[0] == "pull" for a in aufrufe))

    def test_signaturen_auf_wunsch(self):
        _, aufrufe = self._run("https://example.org/x.git")
        self.assertIn(("pull", "--ff-only"), aufrufe)
        _, aufrufe = self._run("https://example.org/x.git", signiert=True)
        self.assertIn(("pull", "--ff-only", "--verify-signatures"), aufrufe)

    def test_winget_quelle_aus_der_liste(self):
        tabelle = ("Name      Id               Version Verfügbar Quelle\n"
                   "------------------------------------------------------\n"
                   "Firefox   Mozilla.Firefox  131.0   132.0     winget\n")
        aufrufe = []

        def winget(argumente, timeout):
            aufrufe.append(argumente)
            return 0, tabelle
        with mock.patch.object(AK, "_winget", winget), mock.patch.dict(AK._QUELLEN, clear=True):
            AK.liste()
            AK.installieren("Mozilla.Firefox")
            AK.installieren("Andere.App")
        self.assertEqual(aufrufe[1][-2:], ["--source", "winget"])
        self.assertNotIn("--source", aufrufe[2])

    def test_require_hashes_nur_mit_hashes(self):
        import venvpflege as V
        with tempfile.TemporaryDirectory() as t:
            datei = Path(t) / "ausstehend.txt"
            for inhalt, erwartet in (("paket==1.0\n", False), ("paket==1.0 --hash=sha256:" + "a" * 64 + "\n", True)):
                datei.write_text(inhalt, encoding="utf-8")
                befehle = []
                with self.subTest(erwartet=erwartet), mock.patch.object(V, "AUSSTEHEND", datei), \
                        mock.patch.object(V.uvsetup, "venv_python", return_value=Path(sys.executable)), \
                        mock.patch.object(V, "_ausfuehren", side_effect=lambda cmd, t: befehle.append(cmd)
                                          or subprocess.CompletedProcess(cmd, 1)):
                    V.ausstehende_einspielen(lambda s: None)
                    self.assertEqual("--require-hashes" in befehle[0], erwartet)


# --- N-13 ----------------------------------------------------------------------------------------------------

class _HfAntwort:
    def __init__(self, daten, status, headers=None):
        self.daten, self.status_code, self.headers = daten, status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, n):
        for i in range(0, len(self.daten), 7):
            yield self.daten[i:i + 7]


class HfLaden(unittest.TestCase):
    INHALT = bytes(range(256)) * 4

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name) / "M"
        self.datei = H.Datei("M-Q4_K_M.gguf", len(self.INHALT), hashlib.sha256(self.INHALT).hexdigest(),
                             revision="c" * 40)

    def test_mehr_bytes_als_angegeben(self):
        class Netz:
            def stream(s, methode, url, headers=None):
                return _HfAntwort(self.INHALT + b"zuviel", 200)
        with self.assertRaises(H.PruefFehler):
            H.laden("x/M", self.datei, self.ordner, lambda *a: None, threading.Event(), session=Netz())
        self.assertEqual(list(self.ordner.iterdir()), [])

    def test_falscher_abschnitt_faengt_neu_an(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "M-Q4_K_M.gguf.part").write_bytes(self.INHALT[:100])
        aufrufe = []

        class Netz:
            def stream(s, methode, url, headers=None):
                aufrufe.append((url, dict(headers or {})))
                if headers:
                    return _HfAntwort(self.INHALT[50:], 206, {"content-range": f"bytes 50-{len(self.INHALT) - 1}/"
                                                                             f"{len(self.INHALT)}"})
                return _HfAntwort(self.INHALT, 200)
        ziel = H.laden("x/M", self.datei, self.ordner, lambda *a: None, threading.Event(), session=Netz())
        self.assertEqual(ziel.read_bytes(), self.INHALT)
        self.assertEqual([h for _, h in aufrufe], [{"Range": "bytes=100-"}, {}])
        self.assertIn("/resolve/" + "c" * 40 + "/", aufrufe[0][0])

    def test_revision_aus_dem_repo(self):
        abrufe = []

        def holen(url, params=None):
            abrufe.append(url)
            if url.endswith("/models/x/M-GGUF"):
                return {"sha": "d" * 40}
            return [{"type": "file", "path": "M-Q4_K_M.gguf", "lfs": {"oid": "a" * 64, "size": 5}}]
        sprach, _ = H.dateien("x/M-GGUF", holen)
        self.assertTrue(abrufe[1].endswith("/tree/" + "d" * 40))
        self.assertEqual(sprach[0].revision, "d" * 40)

    def test_ungueltige_revision(self):
        self.datei.revision = "../../x"
        with self.assertRaises(H.PruefFehler):
            H.laden("x/M", self.datei, self.ordner, lambda *a: None, threading.Event(), session=object())


# --- N-15 / Zusatz: Lücken ----------------------------------------------------------------------------------

class Cvss(unittest.TestCase):
    def test_basiswerte(self):
        for vektor, wert in (("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
                             ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H", 8.8),
                             ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
                             ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", 5.5),
                             ("CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:L/I:N/A:N", 3.7),
                             ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N", 6.4)):
            with self.subTest(vektor=vektor):
                self.assertEqual(luecken.cvss3_basiswert(vektor), wert)
        self.assertIsNone(luecken.cvss3_basiswert("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N"))
        self.assertIsNone(luecken.cvss3_basiswert("CVSS:3.1/AV:N"))

    def test_schwere_aus_vektor_und_fehlende_details(self):
        def post(url, daten):
            return {"results": [{"vulns": [{"id": "GHSA-1"}, {"id": "GHSA-2"}]}]}
        geholt = []

        def get(url):
            geholt.append(url)
            if url.endswith("GHSA-1"):
                return {"id": "GHSA-1", "summary": "x",
                        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}
            raise OSError("weg")
        funde = luecken.pruefen({"paket": "1.0"}, post=post, get=get)["paket"]
        nach_id = {lu.id: lu for lu in funde}
        self.assertEqual(nach_id["GHSA-1"].schwere, "kritisch")
        self.assertIn("Details nicht geladen", nach_id["GHSA-2"].text)
        self.assertEqual(len(geholt), 2)


def _llama(vid, fixed=None, versions=(), summary="", introduced="0"):
    rng = {"type": "GIT", "repo": luecken.LLAMACPP_REPO, "events": [{"introduced": "0"}, {"fixed": "f" * 40}]}
    if fixed:
        rng["database_specific"] = {"extracted_events": [
            {"range": [{"introduced": introduced}, {"fixed": fixed}], "source": "CPE_RANGE"}]}
    return {"id": vid, "summary": summary, "affected": [{"ranges": [rng], "versions": list(versions)}],
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H"}]}


class LlamaCpp(unittest.TestCase):
    def test_nur_echte_treffer(self):
        anfragen = []
        eintraege = [_llama("CVE-ALT", fixed="b5662"),
                     _llama("CVE-NEU", fixed="b13000", summary="Heap overflow in GGUF parsing"),
                     _llama("CVE-UNKLAR"),
                     _llama("CVE-GENANNT", versions=["b12199"]),
                     _llama("CVE-ZUKUNFT", fixed="b14000", introduced="b12500")]

        def post(url, daten):
            anfragen.append((url, daten))
            return {"vulns": eintraege}
        erg = luecken.llamacpp_pruefen("b12199", post=post, get=lambda url: {})
        self.assertEqual(anfragen[0], (luecken.ABFRAGE, {"package": {"ecosystem": "GIT",
                                                                     "name": "https://github.com/ggml-org/llama.cpp"},
                                                         "version": "b12199"}))
        ids = [lu.id for lu in erg["luecken"]]
        self.assertEqual(ids, ["CVE-NEU", "CVE-GENANNT"])              # GGUF zuerst
        neu = erg["luecken"][0]
        self.assertTrue(neu.gguf)
        self.assertEqual((neu.behoben, neu.schwere), ("b13000", "hoch"))
        self.assertEqual(erg["luecken"][1].behoben, "")
        self.assertEqual(erg["unklar"], 1)
        text = "\n".join(luecken.llamacpp_zeilen(erg))
        self.assertIn("behoben ab b13000", text)
        self.assertIn("⚠ GGUF", text)
        self.assertIn("noch ohne Fix", text)

    def test_aktiver_build(self):
        with mock.patch("llamareserve.aktiv", return_value="b12445"):
            erg = luecken.llamacpp_pruefen(post=lambda url, d: {"vulns": []}, get=lambda url: {})
        self.assertEqual(erg["build"], "b12445")
        self.assertIn("keine bekannten offenen Lücken", luecken.llamacpp_zeilen(erg)[0])

    def test_gekuerzte_eintraege_nachgeladen(self):
        voll = _llama("CVE-X", fixed="b99999")
        erg = luecken.llamacpp_pruefen("b100", post=lambda url, d: {"vulns": [{"id": "CVE-X"}]},
                                       get=lambda url: voll if url.endswith("CVE-X") else {})
        self.assertEqual([lu.behoben for lu in erg["luecken"]], ["b99999"])

    def test_osv_weg(self):
        def post(url, d):
            raise httpx.ConnectError("aus")
        with self.assertRaises(OSError):
            luecken.llamacpp_pruefen("b1", post=post, get=lambda url: {})


# --- N-16 ----------------------------------------------------------------------------------------------------

class ChatgptModellListe(unittest.TestCase):
    def test_schluessel_nicht_in_der_schleife(self):
        prov = providers.PROVIDERS["chatgpt"]
        faeden = []

        def key(self):
            faeden.append(threading.current_thread() is threading.main_thread())
            return "token"
        echt = httpx.AsyncClient

        def client(*a, **k):
            return echt(*a, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"models": []})), **k)
        with mock.patch.object(type(prov), "key", key), mock.patch.object(httpx, "AsyncClient", client), \
                mock.patch.dict(providers._models_cache, clear=True):
            asyncio.run(providers.list_models("chatgpt", force=True))
        self.assertEqual(faeden, [False])


# --- N-17 ----------------------------------------------------------------------------------------------------

class Wikipedia(unittest.TestCase):
    def test_sprache_geprueft(self):
        W._CACHE.clear()
        with mock.patch.object(W, "_client", side_effect=AssertionError("kein Abruf")):
            for lang in ("evil.example/x?", "de.evil", "DE", "", "a" * 30):
                with self.subTest(lang=lang):
                    self.assertTrue(W._wikipedia("Test", lang).startswith("❌"))

    def test_gueltige_sprache_geht_durch_check(self):
        W._CACHE.clear()
        with mock.patch.object(W, "_check", return_value="❌ gesperrt") as check:
            self.assertEqual(W._wikipedia("Test", "simple"), "❌ gesperrt")
        check.assert_called_once_with("https://simple.wikipedia.org/w/api.php")


if __name__ == "__main__":
    unittest.main()
