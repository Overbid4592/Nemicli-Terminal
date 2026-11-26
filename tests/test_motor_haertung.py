"""Härtung der Motoren (engines/, ggufengine/) ohne GPU, ohne Modelldatei und ohne Netz.

python -m unittest tests.test_motor_haertung
"""

import asyncio
import hashlib
import http.client
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import torch  # noqa: E402

import chatgpt_login  # noqa: E402
import cloud  # noqa: E402
import gguflokal as GL  # noqa: E402
import llamareserve as LR  # noqa: E402
import malkind  # noqa: E402
import openai_responses as OR  # noqa: E402
import pricing  # noqa: E402
import providers as P  # noqa: E402
import videobildcloud as VC  # noqa: E402
from ggufengine import cudakern, gguf, tokenizer as TK  # noqa: E402
from ggufengine.einbettung import Einbetter  # noqa: E402
from ggufengine.engine import Engine  # noqa: E402
from ggufengine.nachsortierer import Nachsortierer  # noqa: E402
from ggufengine.sampling import Sampler, SamplerConfig  # noqa: E402


def _strom(ereignisse):
    async def lauf():
        for e in ereignisse:
            yield e
    return lauf()


async def _sammeln(agen):
    return [x async for x in agen]


# --- Abbruch beim Einlesen ------------------------------------------------------------------------------

class EinlesenAbbrechen(unittest.TestCase):
    def _engine(self):
        """Echte feed()-Schleife über einen Schein-Rechner: zwei Token je Stück."""
        e = Engine.__new__(Engine)
        e._mrope, e.device = False, torch.device("cpu")
        e._pos, e._history, e._own, e._keys = 0, [], [], []
        e.max_tok_s, e.n_ctx = None, 10_000
        e.tokenizer = NS(stop_ids=set())
        e.gelesen = []

        def forward(teil, pos, *a, **k):
            e.gelesen.append(list(teil))
            logits = torch.full((8,), -10.0)
            logits[1] = 10.0
            return logits
        e.model = NS(prefill_chunk=2, images_bidirectional=False, forward=forward)
        e._kv_room = lambda n: None
        e._lager_umsortieren = lambda melden=True: None
        e._entwurf = lambda: None
        e._verwerfen = lambda: None
        return e

    def test_abbruch_stoppt_zwischen_stuecken_ohne_antwort(self):
        e = self._engine()
        aus = list(e.generate_tokens([3, 4, 5, 6, 7, 8], max_tokens=5, sampler=SamplerConfig(temperature=0),
                                     abbruch=lambda: len(e.gelesen) >= 1))
        self.assertEqual([], aus)
        self.assertEqual([[3, 4]], e.gelesen)                   # nur das erste Stück gelesen
        self.assertEqual([3, 4], e._history)                    # das Gelesene bleibt gecacht

    def test_ohne_abbruch_wie_bisher(self):
        e = self._engine()
        e._weiter = lambda tid, entwurf, vorschlagen=True: e.feed([tid])
        aus = list(e.generate_tokens([3, 4, 5], max_tokens=2, sampler=SamplerConfig(temperature=0),
                                     abbruch=lambda: False))
        self.assertEqual([1, 1], aus)

    def test_erzeugen_reicht_den_abbruch_an_den_motor(self):
        """Esc mitten im Einlesen: der Faden hört auf, statt das ganze Einlesen abzuwarten."""
        gesehen = {}

        class Motor:
            chat_format = NS(thinking=False)

            def chat(self, nachrichten, max_tokens=0, sampler=None, abbruch=None):
                gesehen["abbruch"] = abbruch
                while not abbruch():                            # „liest ein“, bis abgebrochen wird
                    time.sleep(0.01)
                yield from ()

        motor = Motor()
        chat = GL.GgufChat.__new__(GL.GgufChat)
        chat.strength = None
        chat._sampler = lambda engine: None

        async def lauf():
            abbruch = threading.Event()
            agen = chat._erzeugen(motor, [], 10, abbruch, denkt=False)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(agen.__anext__(), 0.2)
            await agen.aclose()
            return abbruch

        with mock.patch.object(GL, "_motor", ("datei", "schluessel", motor)), \
                mock.patch.object(GL, "_lora_abgleich", lambda *a: None), \
                mock.patch.object(GL.gpuzustand, "sicherstellen", lambda: None), \
                mock.patch.object(GL, "FADEN_WARTEN_S", 3.0):
            t0 = time.monotonic()
            abbruch = asyncio.run(lauf())
            dauer = time.monotonic() - t0
        self.assertTrue(abbruch.is_set())
        self.assertIsNotNone(gesehen.get("abbruch"))
        self.assertLess(dauer, 2.0)


# --- Sampling -------------------------------------------------------------------------------------------

class Sampling(unittest.TestCase):
    def _sampler(self, **kw):
        return Sampler(SamplerConfig(repeat_penalty=1.0, **kw), torch.device("cpu"))

    def test_gierig_mit_nan_bricht_ab(self):
        logits = torch.tensor([0.0, float("nan"), 1.0])
        with self.assertRaises(RuntimeError):
            self._sampler(temperature=0).sample(logits, [])

    def test_gierig_normal(self):
        self.assertEqual(2, self._sampler(temperature=0).sample(torch.tensor([0.0, float("-inf"), 3.0]), []))

    def test_top_p_null_heisst_nur_das_beste(self):
        s = self._sampler(temperature=1.0, top_k=0, top_p=0.0, seed=1)
        logits = torch.tensor([1.0, 1.2, 0.9, 1.1])
        self.assertEqual({1}, {s.sample(logits, []) for _ in range(30)})


# --- Cloud ----------------------------------------------------------------------------------------------

class Cloud(unittest.TestCase):
    def test_chatgpt_konto_kostet_nichts(self):
        self.assertIsNone(pricing.price_per_million("chatgpt:gpt-5"))
        self.assertIsNotNone(pricing.price_per_million("openai:gpt-5"))

    def test_ablehnung_kommt_als_text_responses(self):
        teile = asyncio.run(_sammeln(OR.ResponsesChat._auswerten(None, _strom([
            {"type": "response.refusal.delta", "delta": "Dabei helfe ich nicht."},
            {"type": "response.completed", "response": {}}]))))
        self.assertIn({"type": "text", "text": "Dabei helfe ich nicht."}, teile)

    def test_ablehnung_kommt_als_text_chat_completions(self):
        stueck = NS(usage=None, choices=[NS(finish_reason="stop", delta=NS(content=None, refusal="Nein."))])
        teile = asyncio.run(_sammeln(cloud.CloudChat._auswerten(None, _strom([stueck]))))
        self.assertEqual([{"type": "text", "text": "Nein."}], teile)

    def test_zugangs_token_ausserhalb_der_ereignisschleife(self):
        faeden = []

        def zugang():
            faeden.append(threading.current_thread() is threading.main_thread())
            return "synthetisches-token"
        c = OR.ResponsesChat.__new__(OR.ResponsesChat)
        c._konto, c.client = True, NS(api_key=None)
        with mock.patch.object(chatgpt_login, "zugang", zugang):
            asyncio.run(c._client())
        self.assertEqual([False], faeden)
        self.assertEqual("synthetisches-token", c.client.api_key)

    def test_konto_fehler_statt_env_hinweis(self):
        prov = NS(kind="chatgpt", keyless=False, env="UNBENUTZT", key=lambda: None)
        fehler = chatgpt_login.AnmeldungNoetig("Noch nicht angemeldet")
        with mock.patch.object(cloud.P, "get", lambda pid: prov), \
                mock.patch.object(chatgpt_login, "zugang", side_effect=fehler):
            with self.assertRaises(chatgpt_login.AnmeldungNoetig):
                cloud.CloudChat("chatgpt", "gpt-5")

    def test_proxy_nur_fuer_fremde_rechner(self):
        self.assertFalse(P.proxy_erlaubt("http://127.0.0.1:11434"))
        self.assertFalse(P.proxy_erlaubt("http://localhost:1234/v1/models"))
        self.assertTrue(P.proxy_erlaubt("https://api.example.com/v1"))
        self.assertTrue(P.proxy_erlaubt(None))


class VideoBildCloud(unittest.TestCase):
    def test_keine_automatische_weiterleitung(self):
        with VC._client(5.0) as c:
            self.assertFalse(c.follow_redirects)

    def test_weiterleitung_bei_api_aufruf_abgelehnt(self):
        with self.assertRaises(VC.CloudFehler):
            VC._pruefen(NS(is_redirect=True, status_code=302), "Test")

    def test_abfrage_adresse_nur_https_beim_anbieter(self):
        self.assertTrue(VC._abfrage_url("https://api.us1.bfl.ai/v1/get_result?id=1", VC.BFL, "x"))
        self.assertTrue(VC._abfrage_url("https://queue.fal.run/s", VC.FAL, "x"))
        for url in ("http://api.bfl.ai/v1/x", "https://bfl.ai.example.com/x", "https://example.com/x", None):
            with self.assertRaises(VC.CloudFehler):
                VC._abfrage_url(url, VC.BFL, "x")


class Anmeldung(unittest.TestCase):
    def test_falscher_state_beendet_das_warten_nicht(self):
        ergebnis, fertig = {}, threading.Event()
        server = chatgpt_login._empfaenger(0, ergebnis, fertig, "richtig")
        faden = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        faden.start()
        try:
            port = server.server_address[1]

            def holen(state):
                v = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                v.request("GET", f"{chatgpt_login.RUECKRUF_PFAD}?code=c&state={state}")
                status = v.getresponse().status
                v.close()
                return status
            self.assertEqual(400, holen("fremd"))
            self.assertFalse(fertig.is_set())
            self.assertEqual({}, ergebnis)
            self.assertEqual(200, holen("richtig"))
            self.assertTrue(fertig.is_set())
        finally:
            server.shutdown()
            server.server_close()


# --- Prozesse ---------------------------------------------------------------------------------------------

class Prozesse(unittest.TestCase):
    def test_stoppen_beendet_den_server_auch_ohne_zettel_sperre(self):
        s = LR.Server(Path("modell.gguf"))
        s.proc, s.server_pid = NS(poll=lambda: None), 4242
        beendet = []

        def sperre(*a):
            raise RuntimeError("Sperre belegt")
        with mock.patch.object(LR.Server, "_sperre", sperre), \
                mock.patch.object(LR, "_proc_beenden", beendet.append):
            with self.assertRaises(RuntimeError):
                s.stoppen()
        self.assertEqual(1, len(beendet))
        self.assertIsNone(s.proc)

    def test_haengendes_malkind_wird_beendet(self):
        with tempfile.TemporaryDirectory() as d:
            beendet = []
            with mock.patch.object(malkind, "ordner", lambda pid=None: Path(d)), \
                    mock.patch.object(malkind, "_starten", lambda o: None), \
                    mock.patch.object(malkind, "_kind", NS(poll=lambda: None, returncode=None)), \
                    mock.patch.object(malkind, "beenden", lambda: beendet.append(True)), \
                    mock.patch.object(malkind, "STILL_MAX_S", 0.2), mock.patch.object(malkind, "TAKT_S", 0.02):
                with self.assertRaises(RuntimeError) as f:
                    malkind.auftrag("paint", {"prompt": "test"})
            self.assertIn("meldet sich", str(f.exception))
            self.assertEqual([True], beendet)


# --- GGUF-Motor -----------------------------------------------------------------------------------------

class _Tok:
    special = {"<|im_start|>": 1, "<|im_end|>": 2, "<think>": 3}

    def encode(self, text):
        return [ord(c) + 100 for c in text]

    def special_id(self, name):
        return self.special[name]


class GgufMotor(unittest.TestCase):
    def test_lange_frage_laesst_die_vorlage_ganz(self):
        n = Nachsortierer.__new__(Nachsortierer)
        n.tok, n.max_tokens = _Tok(), 64
        n.vorlage = "<|im_start|>Q:{query}\nD:{document}<|im_end|><think>"
        ids = n.tokens("x" * 500, "dokument")
        self.assertLessEqual(len(ids), 64)
        self.assertEqual([1, ord("Q") + 100], ids[:2])              # Anfang der Vorlage bleibt
        self.assertEqual([2, 3], ids[-2:])
        self.assertIn(ord("d") + 100, ids)                           # das Dokument hat Platz

    def test_kaputtes_cubin_wird_geloescht_und_neu_uebersetzt(self):
        with tempfile.TemporaryDirectory() as d:
            pfad = os.path.join(d, "nvrtc64_120_0.dll")
            arch = "--gpu-architecture=sm_120"
            schluessel = hashlib.sha256(("QUELLE" + arch + os.path.basename(pfad)
                                         + str(torch.version.cuda)).encode()).hexdigest()
            datei = os.path.join(d, schluessel + ".cubin")
            Path(datei).write_bytes(b"kaputt")
            geladen, neu = [], []

            def laden(device, cubin):
                geladen.append(bytes(cubin.raw if hasattr(cubin, "raw") else cubin))
                if len(geladen) == 1:
                    raise cudakern._ImageKaputt("cuModuleLoadData")
                return {"ok": True}

            def uebersetzen(p, quelle, a, ziel):
                neu.append(ziel)
                return b"frisch"
            with mock.patch.object(cudakern, "_nvrtc_pfad", lambda: pfad), \
                    mock.patch.object(cudakern, "_quelle", lambda: "QUELLE"), \
                    mock.patch.object(cudakern, "_cache_ordner", lambda: d), \
                    mock.patch.object(cudakern.torch.cuda, "get_device_capability", lambda dev: (12, 0)), \
                    mock.patch.object(cudakern, "_laden", laden), \
                    mock.patch.object(cudakern, "_nvrtc_uebersetzen", uebersetzen):
                self.assertEqual({"ok": True}, cudakern._uebersetzen(torch.device("cuda", 0)))
            self.assertEqual([datei], neu)
            self.assertFalse(os.path.exists(datei))
            self.assertEqual(b"frisch", geladen[-1])

    def test_ohne_regex_paket_kein_stiller_rueckfall(self):
        with mock.patch.dict(sys.modules, {"regex": None}):
            with self.assertRaises(gguf.GGUFError):
                TK.regeln_fuer("llama-bpe")
            self.assertIsNone(TK.regeln_fuer("qwen2"))              # eigener Scanner braucht es nicht

    def _spm(self, unk):
        t = TK.Tokenizer.__new__(TK.Tokenizer)
        t.spm_style, t.vocab, t.ranks, t._bpe_cache, t.unk_id = True, {"a": 5}, {}, {}, unk
        return t

    def test_spm_ohne_bytetoken_nimmt_unk(self):
        self.assertEqual([0, 0], self._spm(0)._bpe("ä"))           # zwei UTF-8-Bytes

    def test_spm_ohne_bytetoken_und_unk_meldet(self):
        with self.assertRaises(gguf.GGUFError):
            self._spm(None)._bpe("ä")

    def test_close_verdeckt_keinen_fehler(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.gguf"
            p.write_bytes(b"\0" * 32)
            g = gguf.GGUFFile.__new__(gguf.GGUFFile)
            g._fh = open(p, "rb")

            class Abbild:
                def close(self):
                    raise BufferError("noch eine Sicht")
            g._mm = Abbild()
            g.close()
            self.assertTrue(g._fh.closed)

    def test_mmap_fehler_schliesst_die_datei(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.gguf"
            p.write_bytes(b"\0" * 32)
            offen = []
            echt = open

            def oeffnen(*a, **k):
                f = echt(*a, **k)
                offen.append(f)
                return f
            with mock.patch("builtins.open", oeffnen), \
                    mock.patch.object(gguf.mmap, "mmap", side_effect=OSError("kein Abbild")):
                with self.assertRaises(OSError):
                    gguf.GGUFFile(p)
            self.assertTrue(offen and offen[0].closed)

    def test_einbetten_ohne_bos_und_eos(self):
        e = Einbetter.__new__(Einbetter)
        gesehen = []
        e.tok = NS(bos_id=None, eos_id=None, encode=lambda t: [5, 6])
        e.model = NS(n_ctx=100, einbetten=lambda ids, pooling, inject=None: gesehen.append(ids) or torch.ones(4))
        e.pooling, e.device = "mean", torch.device("cpu")
        e.medien_vektor([("text", "x")], normieren=False)
        self.assertEqual([[5, 6]], gesehen)


if __name__ == "__main__":
    unittest.main()
