"""Cloud-Motor, ChatGPT-Konto, Preise, Stimme und Musik: Abbruch, Fristen, Fehlermeldungen und Aufräumen.
Ohne Netz, ohne GPU, ohne Modelldateien – Anbieter, Token-Endpunkt und Modelle sind nachgebaut."""
import asyncio
import http.client
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import chatgpt_login as L  # noqa: E402
import cloud  # noqa: E402
import cloudprovider as CP  # noqa: E402
import config  # noqa: E402
import openai_responses as OR  # noqa: E402
import persoenlichkeiten as PS  # noqa: E402
import persona  # noqa: E402
import pricing  # noqa: E402
import providers as P  # noqa: E402
import stimme as S  # noqa: E402
from musik import gewichte, motor, netze, planer  # noqa: E402


async def _sammeln(gen):
    return [t async for t in gen]


async def _strom(teile):
    for t in teile:
        yield t


class _Antwort:
    def __init__(self, status, daten):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


# --- C-01: ESC während des Zusammenfassens ---------------------------------------------------------------

class PufferAbwarten(unittest.IsolatedAsyncioTestCase):
    def _chat(self, puffer):
        c = cloud.CloudChat.__new__(cloud.CloudChat)
        c._puffer, c._puffer_hinweis = puffer, ""
        return c

    async def _runde(self, c, weiter):
        async for _ in c._puffer_abwarten():
            pass
        weiter.append(True)

    async def _abbrechen(self, aufraeumen):
        puffer = asyncio.create_task(aufraeumen())
        c, weiter = self._chat(puffer), []
        runde = asyncio.create_task(self._runde(c, weiter))
        await asyncio.sleep(0.05)
        runde.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await runde
        self.assertEqual([], weiter)                           # danach wird keine Antwort mehr geholt
        self.assertTrue(puffer.done())
        self.assertIsNone(c._puffer)

    async def test_esc_auch_wenn_das_aufraeumen_den_abbruch_schluckt(self):
        async def aufraeumen():                                # wie _hintergrund
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return
        await self._abbrechen(aufraeumen)

    async def test_esc_mit_abbrechendem_aufraeumen(self):
        await self._abbrechen(lambda: asyncio.sleep(30))

    async def test_abbruch_nur_des_aufraeumens_wird_geschluckt(self):
        puffer = asyncio.create_task(asyncio.sleep(30))
        c, weiter = self._chat(puffer), []
        runde = asyncio.create_task(self._runde(c, weiter))
        await asyncio.sleep(0.05)
        puffer.cancel()                                        # reset() oder Modellwechsel
        await runde
        self.assertEqual([True], weiter)


# --- C-02 / C-03: ChatGPT-Konto ohne Netz in der Ereignisschleife, klare Meldungen ----------------------

class KontoZugang(unittest.IsolatedAsyncioTestCase):
    async def test_kontext_fragt_kein_token_ab(self):
        gestartet = []
        with mock.patch.object(L, "zugang", side_effect=AssertionError("Token in der Ereignisschleife")), \
                mock.patch.object(L, "nutzbar", return_value=True), \
                mock.patch.object(CP, "im_hintergrund", lambda k, _a: gestartet.append(k)):
            self.assertIsNone(CP.kontext("chatgpt", "tuev-modell-a"))
            self.assertEqual(["chatgpt:tuev-modell-a"], gestartet)
            with mock.patch.object(L, "nutzbar", return_value=False):
                self.assertIsNone(CP.kontext("chatgpt", "tuev-modell-b"))
        self.assertEqual(["chatgpt:tuev-modell-a"], gestartet)

    async def test_ohne_anmeldung_wird_nichts_gesendet(self):
        fehler = L.AnmeldungNoetig("Noch nicht mit ChatGPT angemeldet")
        with mock.patch.object(L, "zugang", side_effect=fehler), \
                mock.patch.object(P.httpx, "AsyncClient", side_effect=AssertionError("gesendet")):
            with self.assertRaises(L.AnmeldungNoetig) as f:
                await P.list_models("chatgpt", force=True)
        self.assertIn("angemeldet", str(f.exception))
        with mock.patch.object(L, "zugang", side_effect=fehler):
            with self.assertRaises(L.AnmeldungNoetig):
                CP._auth_headers(P.get("chatgpt"))

    def test_abgelehntes_konto_heisst_neu_anmelden(self):
        text = CP.status_text("ChatGPT-Konto", 401, konto=True)
        self.assertIn("neu anmelden", text)
        self.assertNotIn("API-Schlüssel", text)
        self.assertIn("API-Schlüssel", CP.status_text("OpenAI", 401))


# --- C-04 / C-09 / C-10: Anmeldung im Browser ------------------------------------------------------------

class Anmeldung(unittest.TestCase):
    def test_abbruch_beendet_das_warten_und_schliesst_den_empfaenger(self):
        gesehen = {}

        def browser(url):
            gesehen["q"] = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        start = time.monotonic()
        with mock.patch.object(L, "_lesen", return_value={}), mock.patch.object(L, "WUNSCH_PORT", 0), \
                mock.patch.object(L.httpx, "post", side_effect=AssertionError("kein Token-Tausch")):
            with self.assertRaises(L.AnmeldungAbgebrochen):
                L.anmelden(browser, warten_s=30, abbruch=lambda: time.monotonic() - start > 0.3)
        self.assertLess(time.monotonic() - start, 5)
        port = urllib.parse.urlsplit(gesehen["q"]["redirect_uri"]).port
        v = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        with self.assertRaises(OSError):                       # später Rückruf: niemand lauscht mehr
            v.request("GET", f"{L.RUECKRUF_PFAD}?code=c&state={gesehen['q']['state']}")
            v.getresponse()
        v.close()

    def test_empfaenger_mit_socket_frist(self):
        server = L._empfaenger(0, {}, threading.Event(), "s")
        try:
            self.assertEqual(5, server.RequestHandlerClass.timeout)
        finally:
            server.server_close()

    def test_port_direkt_exklusiv_gebunden(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", 0))
        s.listen()
        belegt = s.getsockname()[1]
        try:
            with mock.patch.object(L, "WUNSCH_PORT", belegt):
                server = L._empfaenger_starten({}, threading.Event(), "s")
            try:
                self.assertNotEqual(belegt, server.server_address[1])       # belegt: freier Port statt Fehler
                self.assertEqual("127.0.0.1", server.server_address[0])
            finally:
                server.server_close()
        finally:
            s.close()

    def test_toter_code_entfernt(self):
        self.assertFalse(hasattr(L, "modelle"))
        self.assertFalse(hasattr(L, "modelle_aus"))
        self.assertFalse(hasattr(L, "_freier_port"))


# --- C-05: Erneuern -------------------------------------------------------------------------------------

class Erneuern(unittest.TestCase):
    def setUp(self):
        self.ablage = {"client_id": "oaiapp_test", "access_token": "alt-access", "refresh_token": "alt-refresh",
                       "scope": L.SCOPES, "expires_at": time.time() - 10, "earliest_refresh_at": None}

        def schreiben(d):
            self.ablage = dict(d)
        for p in (mock.patch.object(L, "_lesen", side_effect=lambda: dict(self.ablage)),
                  mock.patch.object(L, "_schreiben", side_effect=schreiben)):
            p.start()
            self.addCleanup(p.stop)

    def test_unbekannter_400_loescht_nichts(self):
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(400, {"error": "invalid_request"})):
            with self.assertRaises(RuntimeError) as f:
                L._erneuern(dict(self.ablage))
        self.assertNotIsInstance(f.exception, L.AnmeldungNoetig)
        self.assertEqual("alt-refresh", self.ablage["refresh_token"])

    def test_bekannter_code_loescht(self):
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(401, {"error": "refresh_token_expired"})):
            with self.assertRaises(L.AnmeldungNoetig):
                L._erneuern(dict(self.ablage))
        self.assertNotIn("refresh_token", self.ablage)

    def test_neues_refresh_token_bleibt_auch_bei_fehlerhaftem_id_token(self):
        antwort = {"access_token": "neu-access", "refresh_token": "neu-refresh", "expires_in": 3600,
                   "id_token": "kein.gueltiges.token"}
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(200, antwort)):
            with self.assertRaises(L.AnmeldeFehler):
                L._erneuern(dict(self.ablage))
        self.assertEqual("neu-refresh", self.ablage["refresh_token"])


# --- C-06: Frist und Wiederholungen ----------------------------------------------------------------------

class Client(unittest.TestCase):
    def test_frist_und_wiederholungen(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-synthetisch"}):
            c = cloud.CloudChat("openai", "gpt-test")
        self.assertEqual(1, c.client.max_retries)
        self.assertEqual((cloud.VERBINDEN_S, cloud.FRIST_S), (c.client.timeout.connect, c.client.timeout.read))
        with mock.patch.object(L, "zugang", return_value="synthetisches-token"), \
                mock.patch.object(L, "nutzbar", return_value=True):
            konto = OR.ResponsesChat("chatgpt", "gpt-test")
        self.assertEqual(0, konto.client.max_retries)          # kein zweiter Versuch bei „Kontingent aufgebraucht“


# --- C-07: Inhaltsfilter --------------------------------------------------------------------------------

class _Responses:
    def __init__(self, ereignisse):
        self.ereignisse = ereignisse

    async def create(self, **_kwargs):
        return _strom(self.ereignisse)


class Inhaltsfilter(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completions(self):
        stueck = NS(usage=None, choices=[NS(finish_reason="content_filter", delta=NS(content=None))])
        self.assertIn({"type": "filter"}, await _sammeln(cloud.CloudChat._auswerten(None, _strom([stueck]))))

    async def test_responses_grund(self):
        for grund, art in (("content_filter", "filter"), ("max_output_tokens", "length")):
            ev = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": grund}}}
            self.assertIn({"type": art}, await _sammeln(OR.ResponsesChat._auswerten(None, _strom([ev]))))

    async def test_eigene_meldung_im_verlauf(self):
        async def anleitung(_text, **_k):
            return "SYNTHETISCHE ANLEITUNG"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-synthetisch"}), \
                mock.patch.object(persona, "build_system_prompt_async", anleitung):
            c = OR.ResponsesChat("openai", "gpt-test")
            c.client = NS(responses=_Responses([{"type": "response.incomplete", "response": {
                "incomplete_details": {"reason": "content_filter"}}}]), api_key="")
            teile = await _sammeln(c.stream("Hallo"))
            self.assertTrue(any(t["type"] == "note" and "Inhaltsfilter" in t["text"] for t in teile))
            self.assertFalse(any(t["type"] == "note" and "Tokenbudget" in t["text"] for t in teile))
            self.assertIn("Inhaltsfilter", c.messages[-1]["content"])
            with self.assertRaises(OR.ResponsesFehler):
                await c.ask_once("Kurz", "Du hilfst.")


# --- C-08: Preise kleiner Varianten ---------------------------------------------------------------------

class Preise(unittest.TestCase):
    def test_kleine_varianten(self):
        pm = pricing.price_per_million
        self.assertEqual((0.15, 0.60), pm("openai:gpt-4o-mini"))
        self.assertEqual((0.40, 1.60), pm("openai:gpt-4.1-mini"))
        self.assertEqual((1.10, 4.40), pm("openai:o4-mini"))
        for unbekannt in ("gpt-5-mini", "gpt-5-nano", "gpt-4.1-nano", "o3-mini", "gpt-5.1-mini-2026"):
            self.assertIsNone(pm(f"openai:{unbekannt}"), unbekannt)   # nicht der teure Grundpreis
        self.assertEqual((1.25, 10.0), pm("openai:gpt-5"))
        self.assertEqual((2.0, 8.0), pm("openai:o3"))
        self.assertEqual((0.30, 2.50), pm("google:gemini-2.5-flash"))   # „mini“ in „gemini“ zählt nicht
        self.assertEqual((0.40, 2.0), pm("mistral:mistral-small-latest"))


# --- S-01 bis S-04: Stimme -----------------------------------------------------------------------------

def _gpu_attrappen(talker):
    return {"gpuzustand": NS(sicherstellen=lambda: None, kaputt=lambda: False),
            "hintergrund": NS(gpu_belegt=lambda: None),
            "ggufengine.gguf": NS(GGUFFile=lambda p: p),
            "ggufengine.models.qwen3tts": NS(Qwen3TTSTalker=talker, Qwen3TTSCodec=lambda *a: object())}


class Stimme(unittest.TestCase):
    def setUp(self):
        self.addCleanup(S._zustand.update, reste=False)

    def test_gescheitertes_laden_leert_den_cache(self):
        def talker(*_a):
            raise RuntimeError("zu wenig Speicher")
        geleert = []
        with mock.patch.dict(sys.modules, _gpu_attrappen(talker)), \
                mock.patch.object(torch.cuda, "is_available", return_value=False), \
                mock.patch.object(S, "verfuegbar", return_value=True), \
                mock.patch.object(S, "_pfade", (Path("talker.gguf"), Path("decoder.gguf"))), \
                mock.patch.object(S, "_modell", None), \
                mock.patch.object(S, "_cache_leeren", lambda: geleert.append(1)):
            with self.assertRaises(RuntimeError) as f:
                S._laden()
            self.assertIn("zu wenig Speicher", str(f.exception))
            self.assertIsNone(f.exception.__context__)        # kein Traceback, der Tensoren festhält
            self.assertEqual(1, len(geleert))
            S.freigeben()                                      # ohne Modell, aber mit Resten: leert
            self.assertEqual(2, len(geleert))
            S.freigeben()                                      # nichts mehr übrig: kein Leeren vor jedem Bild
            self.assertEqual(2, len(geleert))

    def test_sagen_wartet_nicht_auf_das_laden(self):
        gehalten, los = threading.Event(), threading.Event()

        def laden():
            with S._lock:
                gehalten.set()
                los.wait(5)
        faden = threading.Thread(target=laden, daemon=True)
        faden.start()
        gehalten.wait(2)
        try:
            with mock.patch.object(S, "AKTIV", True), mock.patch.object(S, "modus", return_value="knopf"), \
                    mock.patch.object(S, "verfuegbar", return_value=True), mock.patch.object(S, "_faden", None), \
                    mock.patch.object(S, "_arbeiter", lambda: None):
                t = time.monotonic()
                self.assertTrue(S.sagen("Hallo.", S.STARTSTIMME))
                self.assertLess(time.monotonic() - t, 1.0)
        finally:
            los.set()
            faden.join(5)
            while not S._auftraege.empty():
                S._auftraege.get_nowait()

    def test_hz_sperre_nach_der_genannten_persoenlichkeit(self):
        maennlich = NS(name="Beta")
        with mock.patch.object(config, "load", return_value={}), \
                mock.patch.object(PS, "resolve", side_effect=lambda n: maennlich if n == "Beta" else None), \
                mock.patch.object(PS, "geschlecht", side_effect=lambda p=None: "m" if p is maennlich else "w"):
            self.assertEqual(0.0, S.min_hz("Beta"))
            self.assertEqual(float(S.MIN_HZ), S.min_hz("Unbekannt"))
            self.assertEqual(float(S.MIN_HZ), S.min_hz())
            self.assertIsNone(S.sperre(120.0, "Beta"))
            self.assertIn("Männerbereich", S.sperre(120.0))

    def test_probe_mit_sprache(self):
        with mock.patch.dict(S._PROBEN, clear=True):
            S.probe_merken("Hell und klar", 1, 210.0, "english")
            self.assertEqual((False, None), S._probe("Hell und klar", 1, "german"))
            self.assertEqual((True, 210.0), S._probe("Hell und klar", 1, "english"))
            S.probe_merken("Hell und klar", 2, None)                       # ohne Sprache: gilt für jede
            self.assertEqual((True, None), S._probe("Hell und klar", 2, "german"))

    def test_verfuegbar_merkt_fehlschlag_je_ordner(self):
        konfig, suchen = {"stimme_ordner": ""}, []
        with mock.patch.object(config, "load", side_effect=lambda: dict(konfig)), \
                mock.patch.object(S, "finden", side_effect=lambda: suchen.append(1)), \
                mock.patch.object(S, "_pfade", None), mock.patch.dict(S._suche, {"ordner": None, "fehl_bis": 0.0}):
            self.assertFalse(S.verfuegbar())
            self.assertFalse(S.verfuegbar())
            self.assertEqual(1, len(suchen))                   # Fehlschlag gemerkt
            konfig["stimme_ordner"] = "C:/example/stimme"
            self.assertFalse(S.verfuegbar())
            self.assertEqual(2, len(suchen))                   # anderer Ordner: neu gesucht
            S._suche["fehl_bis"] = 0.0                         # Frist abgelaufen: wieder gesucht
            S.verfuegbar()
            self.assertEqual(3, len(suchen))


# --- M-01 bis M-04: Musik ------------------------------------------------------------------------------

class Musik(unittest.TestCase):
    def test_wav_erst_als_part(self):
        with tempfile.TemporaryDirectory() as t:
            ziel = Path(t) / "s.wav"
            motor.wav_schreiben(np.zeros((2, 480), np.float32), ziel, {"INAM": "Test"})
            self.assertTrue(ziel.is_file())
            self.assertFalse((Path(t) / "s.wav.part").exists())
            with mock.patch.object(motor.os, "replace", side_effect=OSError("Platte voll")):
                with self.assertRaises(OSError):
                    motor.wav_schreiben(np.zeros((2, 480), np.float32), Path(t) / "b.wav")
            self.assertEqual([], list(Path(t).glob("b.wav*")))

    def test_abbruch_in_pruefung_und_vae(self):
        ja = lambda: True  # noqa: E731
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "x.gguf"
            f.write_bytes(b"\0" * 100)
            with self.assertRaises(motor.Abbruch):
                gewichte.sha256(f, ja)
        with self.assertRaises(motor.Abbruch):
            netze.VAEDecoder.dekodieren(NS(), torch.zeros(64, 10), abbruch=ja)
        with self.assertRaises(motor.Abbruch):
            netze.VAEEncoder.kodieren(NS(FAKTOR=1920), torch.zeros(2, 1920 * 3), abbruch=ja)

    def test_eine_abbruch_klasse(self):
        self.assertIs(planer.Abbruch, motor.Abbruch)
        self.assertIs(netze.Abbruch, motor.Abbruch)
        self.assertNotIsInstance(motor.Abbruch("x"), RuntimeError)

    def test_merker_mit_teil_hash(self):
        name = "acestep-v15-turbo-Q8_0.gguf"
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / name
            f.write_bytes(b"A" * 100)
            with mock.patch.dict(gewichte.SHA256, {name: gewichte.sha256(f)}):
                gewichte.pruefen(f)
                st = f.stat()
                f.write_bytes(b"B" * 100)                          # gleiche Größe, Zeit zurückgesetzt
                os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
                with self.assertRaises(RuntimeError):
                    gewichte.pruefen(f)


if __name__ == "__main__":
    unittest.main()
