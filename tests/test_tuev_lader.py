"""Lader des lokalen Motors: Bild und Laden, Sperren, llama.cpp-Reserve (fremde ggml-Typen, Pause-Zettel,
Paketwahl, Start-Abbruch), abgerissene Werkzeugaufrufe, Grenzen des GGUF-Lesers.

python -m unittest tests.test_tuev_lader
"""

import asyncio
import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import gguflokal as G                                  # noqa: E402
import llamamotor as LM                                # noqa: E402
import llamareserve as R                               # noqa: E402
import werkzeugsprache as W                            # noqa: E402
from ggufengine import gguf as GG                      # noqa: E402
from ggufengine.gguf import GGUFError, GGUFFile        # noqa: E402

FREMD_TYP = 40                                         # in llama.cpp vorhanden, im eigenen Leser nicht


def _tokenizer_meta() -> dict:
    from ggufengine.tokenizer import UNI_TO_BYTE
    zu_zeichen = {b: c for c, b in UNI_TO_BYTE.items()}
    tokens = [zu_zeichen[b] for b in range(256)] + ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]
    return {"general.architecture": "zukunftsbau", "general.alignment": 32, "tokenizer.ggml.model": "gpt2",
            "tokenizer.ggml.pre": "default", "tokenizer.ggml.tokens": tokens,
            "tokenizer.ggml.token_type": [1] * 256 + [3] * 3, "tokenizer.ggml.merges": ["Ġ a"],
            "tokenizer.ggml.eos_token_id": 257,
            "tokenizer.chat_template": "{% for m in messages %}<|im_start|>{{ m.role }}\n"
                                       "{{ m.content }}<|im_end|>\n{% endfor %}"}


def _fremde_gguf(pfad: Path) -> None:
    """Modell mit einem Tensor unbekannten Typs (40) und einem bekannten F32-Tensor dahinter."""
    import gguf_testbau as B
    import torch
    B.schreibe_gguf(str(pfad), _tokenizer_meta(), {
        "blk.0.ffn_down_exps.weight": (FREMD_TYP, (2, 64), b"\x01" * 36),
        "output_norm.weight": torch.ones(4)})


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


# ---------------------------------------------------------------------------
# L-04: unbekannter ggml-Typ – eigener Motor lehnt ab, die Reserve öffnet die Datei
# ---------------------------------------------------------------------------
class FremdeTypen(_Tmp):
    def setUp(self):
        super().setUp()
        self.modell = self.tmp / "fremd.gguf"
        _fremde_gguf(self.modell)

    def test_eigener_leser_lehnt_ab_und_die_meldung_ruft_die_reserve(self):
        with self.assertRaises(GGUFError) as f:
            GGUFFile(self.modell)
        self.assertIn(f"unknown ggml type {FREMD_TYP}", str(f.exception))
        self.assertTrue(G._RESERVE_FEHLER.search(str(f.exception)))

    def test_mit_fremden_typen_nur_kopf_und_grenzen(self):
        with GGUFFile(self.modell, fremde_typen=True) as gg:
            ti = gg.tensors["blk.0.ffn_down_exps.weight"]
            norm = gg.tensors["output_norm.weight"]
            self.assertFalse(ti.bekannt)
            self.assertEqual(ti.type_name, f"unknown({FREMD_TYP})")
            self.assertEqual(ti.n_bytes, norm.offset - ti.offset)          # bis zum nächsten Tensor
            self.assertEqual(len(gg.get("tokenizer.ggml.tokens")), 259)
            with self.assertRaises(GGUFError):
                gg.tensor_bytes(ti)                                         # Daten nicht lesbar
            with self.assertRaises(GGUFError):
                gg.read_into(ti, bytearray(8))
            self.assertEqual(bytes(gg.tensor_bytes(norm)), struct.pack("<4f", 1, 1, 1, 1))

    def _roh(self, tensoren: list[tuple[str, int, int]], daten: int = 64) -> Path:
        """GGUF von Hand: (Name, Typ, Offset) je eindimensionalem Tensor mit 32 Werten."""
        kopf = b"GGUF" + struct.pack("<IQQ", 3, len(tensoren), 0)
        for name, typ, off in tensoren:
            n = name.encode()
            kopf += struct.pack("<Q", len(n)) + n + struct.pack("<IQIQ", 1, 32, typ, off)
        kopf += b"\0" * ((-len(kopf)) % 32)
        p = self.tmp / "roh.gguf"
        p.write_bytes(kopf + b"\0" * daten)
        return p

    def test_grenzen_bleiben_auch_fuer_fremde_typen(self):
        for fall in ([("a", FREMD_TYP, 128)],                              # beginnt hinter dem Dateiende
                     [("a", FREMD_TYP, 0), ("b", FREMD_TYP, 0)],           # gleicher Platz
                     [("a", 0, 0), ("b", FREMD_TYP, 96)],                  # F32 (128 Byte) reicht in b hinein
                     [("a", FREMD_TYP, 8)]):                               # nicht ausgerichtet
            with self.subTest(fall=fall), self.assertRaises(GGUFError):
                GGUFFile(self._roh(fall, daten=256 if fall[0][1] == 0 else 64), fremde_typen=True).close()
        GGUFFile(self._roh([("a", FREMD_TYP, 0), ("b", FREMD_TYP, 32)]), fremde_typen=True).close()

    def test_motor_laden_fragt_nach_der_reserve(self):
        class Engine:                                                       # der eigene Motor liest zuerst die Datei
            def __init__(self, pfad, **k):
                GGUFFile(pfad).close()
        modell = self.modell
        self.addCleanup(setattr, G, "_motor", G._motor)
        G._motor = None
        with patch.object(G, "modelle", return_value={"Neu": modell}), \
                patch.object(G, "kontext", return_value=8192), patch.object(G, "_torch_bereit"), \
                patch.object(G, "_frei_vram", return_value=None), patch.object(G, "_config", return_value={}), \
                patch.object(G, "verteilen", return_value=G.Verteilung(16, None, None, 8192, None)), \
                patch.object(G, "_nach_fehlschlag_aufraeumen"), patch.object(G, "entladen"), \
                patch.object(R, "bereit", return_value=True), \
                patch.dict(sys.modules, {"ggufengine": types.SimpleNamespace(Engine=Engine)}):
            with self.assertRaises(R.ReserveFrage) as f:
                G._motor_laden("Neu")
        self.assertIn(f"unknown ggml type {FREMD_TYP}", f.exception.grund)

    def test_reserve_oeffnet_die_datei(self):
        gesehen = []

        def starten(server, abbruch=None, melden=None):
            gesehen.append((abbruch, melden))
            return 0.0
        stopp, melden = (lambda: False), (lambda t: None)
        with patch.object(R, "_daten", return_value=self.tmp), patch.object(R.Server, "starten", starten):
            e = LM.LlamaCppEngine(str(self.modell), n_ctx=2048, abbruch=stopp, melden=melden)
        try:
            self.assertEqual(e.tokenizer.n_vocab, 259)
            self.assertEqual(e.n_ctx, 2048)
            self.assertEqual(gesehen, [(stopp, melden)])                    # Abbruch und Hinweis beim ersten Start
        finally:
            e.gg.close()

    def test_gescheiterter_start_gibt_die_datei_frei(self):
        def starten(server, abbruch=None, melden=None):
            raise RuntimeError("Start abgebrochen")
        with patch.object(R, "_daten", return_value=self.tmp), patch.object(R.Server, "starten", starten):
            with self.assertRaises(RuntimeError):
                LM.LlamaCppEngine(str(self.modell))
        self.modell.unlink()                                                # unter Windows gesperrt, wäre sie offen

    def test_bekannter_typ_ohne_rechenweg_ruft_die_reserve(self):
        self.assertTrue(G._RESERVE_FEHLER.search("tensor type NVFP4 is not supported by this engine"))
        self.assertFalse(G._RESERVE_FEHLER.search("tensor 'x' extends beyond file"))


class WahlErstNachDemLaden(unittest.TestCase):
    """main._antworten_mit_reserve: llama.cpp bleibt nur eingestellt, wenn das Laden gelingt."""

    def _lauf(self, zweiter):
        import main as M
        aufrufe, gesetzt = [], []

        async def antworten(backend, text, imgs):
            aufrufe.append(text)
            if len(aufrufe) == 1:
                raise R.ReserveFrage("Neu", "tensor 'x': unknown ggml type 40")
            return zweiter()

        async def frage(text, optionen):
            return "ja"
        aus = {"antworten": antworten, "frage": frage, "info": lambda t: None, "warn": lambda t: None}
        with patch.object(G, "motor_setzen", side_effect=lambda n, w: gesetzt.append((n, w))), \
                patch.object(G, "_motor", None):
            try:
                return asyncio.run(M._antworten_mit_reserve(aus, None, "Hallo", None)), gesetzt
            except RuntimeError as exc:
                return exc, gesetzt

    def test_gelingt(self):
        r, gesetzt = self._lauf(lambda: {"ok": True})
        self.assertEqual(r, {"ok": True})
        self.assertEqual(gesetzt, [("Neu", G.LLAMACPP)])

    def test_scheitert_wahl_zurueck(self):
        def kaputt():
            raise RuntimeError("llama-server hat sich beendet")
        r, gesetzt = self._lauf(kaputt)
        self.assertIsInstance(r, RuntimeError)
        self.assertEqual(gesetzt, [("Neu", G.LLAMACPP), ("Neu", G.EIGEN)])

    def test_noch_eine_frage_wahl_zurueck(self):
        def frage():
            raise R.ReserveFrage("Neu", "x")
        r, gesetzt = self._lauf(frage)
        self.assertIsNone(r)
        self.assertEqual(gesetzt[-1], ("Neu", G.EIGEN))


# ---------------------------------------------------------------------------
# L-01 / L-02 / L-06: Bild, Laden und Rechnen
# ---------------------------------------------------------------------------
class BildUndLaden(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(G, "_bild_tiefe", 0), patch.object(G, "_ausgelagert", None),
                  patch.object(G.gpuzustand, "sicherstellen"), patch.dict(sys.modules, {"memory": MagicMock()}),
                  patch.object(G, "_hinweis", "")):
            p.start()
            self.addCleanup(p.stop)

    def test_bild_ohne_modell_zaehlt_und_laden_wartet(self):
        geladen, ergebnis = [], []
        with patch.object(G, "_motor", None), \
                patch.object(G, "_motor_laden", side_effect=lambda n: geladen.append(n) or "engine"):
            with G.gpu_fuer_bild():
                self.assertEqual(G._bild_tiefe, 1)
                faden = threading.Thread(target=lambda: ergebnis.append(G.motor("m")))
                faden.start()
                time.sleep(0.5)
                self.assertEqual(geladen, [])                               # kein Nachladen neben dem Bild
                self.assertEqual(G.lade_hinweis(), G.BILD_WARTEN)
            faden.join(5)
        self.assertEqual((geladen, ergebnis), (["m"], ["engine"]))
        self.assertEqual(G._bild_tiefe, 0)
        self.assertEqual(G.lade_hinweis(), "")

    def test_esc_bricht_das_warten_ab(self):
        with patch.object(G, "_motor", None), patch.object(G, "_esc", return_value=True), \
                patch.object(G, "_motor_laden", side_effect=AssertionError("lädt nicht")):
            with G.gpu_fuer_bild():
                with self.assertRaises(G.MotorWeg):
                    G.motor("m")

    def test_bild_wartet_auf_ein_laufendes_laden(self):
        drin = threading.Event()

        def malen():
            with G.gpu_fuer_bild():
                drin.set()
        with patch.object(G, "_motor", None):
            G._lade_lock.acquire()
            try:
                faden = threading.Thread(target=malen)
                faden.start()
                time.sleep(0.3)
                self.assertFalse(drin.is_set())
            finally:
                G._lade_lock.release()
            faden.join(5)
        self.assertTrue(drin.is_set())

    def test_rechnen_prueft_nach_der_sperre_erneut(self):
        engine = NS(offloaded=False)
        lief = threading.Event()

        def arbeit():
            with G._rechnen(engine):
                lief.set()
        with patch.object(G, "_motor", ("x", 1, engine)):
            G._lock.acquire()
            try:
                faden = threading.Thread(target=arbeit)
                faden.start()
                time.sleep(0.3)                                             # wartet auf die Sperre
                G._bild_tiefe = 1                                           # ein Bild beginnt genau jetzt
            finally:
                G._lock.release()
            time.sleep(0.5)
            self.assertFalse(lief.is_set())                                 # Sperre wieder gelöst, wartet
            G._bild_tiefe = 0
            faden.join(5)
        self.assertTrue(lief.is_set())

    def test_rechnen_holt_ein_ausgelagertes_modell_zurueck(self):
        log = []
        engine = NS(offloaded=True, restore=lambda: log.append("rein"))
        with patch.object(G, "_motor", ("x", 1, engine)):
            with G._rechnen(engine):
                pass
        self.assertEqual(log, ["rein"])

    def test_kodieren_wartet_auf_das_bild(self):
        engine = NS(offloaded=False, encode_image=lambda b: "emb")
        with patch.object(G, "_motor", ("x", 1, engine)), patch.object(G, "_esc", return_value=True), \
                patch.object(G, "_bild_aus_uri", side_effect=lambda u: u):
            G._bild_tiefe = 1
            with self.assertRaises(G.MotorWeg):                             # ESC: nicht neben dem Bild rechnen
                G._kodieren(engine, ["a"])
            G._bild_tiefe = 0
            self.assertEqual(G._kodieren(engine, ["a"]), ["emb"])

    def _fremd(self, log, fremd=True):
        return NS(fremd=fremd, offloaded=False, offload=lambda: log.append("raus"),
                  restore=lambda: log.append("rein"))

    def test_entladen_waehrend_des_bildes_beendet_die_pause(self):
        log = []
        m = self._fremd(log)
        with patch.object(G, "_motor", ("x", 1, m)):
            with G.gpu_fuer_bild():
                G._motor = None                                             # mitten im Bild entladen
        self.assertEqual(log, ["raus", "rein"])                             # restore() räumt den Pause-Zettel

    def test_eigener_motor_entladen_wird_nicht_zurueckgeholt(self):
        log = []
        m = self._fremd(log, fremd=False)
        with patch.object(G, "_motor", ("x", 1, m)):
            with G.gpu_fuer_bild():
                G._motor = None
        self.assertEqual(log, ["raus"])


class PauseZettel(_Tmp):
    """llama.cpp: der eigene Pause-Zettel endet mit dem Bild, auch nach dem Entladen."""

    def test_restore_nach_leeren_raeumt_den_zettel(self):
        modell = self.tmp / "m.gguf"
        modell.write_bytes(b"GGUF")
        with patch.object(R, "_daten", return_value=self.tmp):
            s = R.Server(modell)
            s.stoppen(fuer_bild=True)
            self.assertTrue(R._zettel_lesen(modell)["pause"])
            e = object.__new__(LM.LlamaCppEngine)
            e.server, e._aus, e._geleert, e._keys, e._pos = s, True, False, [], 0
            e.leeren()                                                      # entladen mitten im Bild
            self.assertTrue(R._zettel_pfad(modell).exists())                # das Bild läuft noch: Pause bleibt
            e.restore()                                                     # Bild fertig
            self.assertFalse(R._zettel_pfad(modell).exists())

    def test_warten_auf_pause_meldet_und_ist_abbrechbar(self):
        modell = self.tmp / "m.gguf"
        modell.write_bytes(b"GGUF")
        gemeldet, fragen = [], []
        with patch.object(R, "_daten", return_value=self.tmp), patch.object(R, "_lebt_als", return_value=True):
            R._zettel_schreiben(modell, {"build": R.aktiv(), "kennung": R._kennung(modell), "pause": True,
                                         "pause_von": os.getpid() + 1, "pause_seit": time.time()})
            s = R.Server(modell)
            with self.assertRaises(RuntimeError):
                s._pause_abwarten(lambda: fragen.append(1) or len(fragen) > 1, gemeldet.append)
        self.assertEqual(gemeldet, [R.PAUSE_HINWEIS])

    def test_zettel_ohne_besitzer(self):
        import inspect
        self.assertNotIn("besitzer", inspect.getsource(R.Server))


# ---------------------------------------------------------------------------
# L-03: Fehler von llama-server sperren die CUDA-Sitzung nicht
# ---------------------------------------------------------------------------
class FremdeFehler(unittest.TestCase):
    TEXT = "llama-server: CUDA error: an illegal memory access was encountered"

    def test_erzeugen_ohne_gpu_sperre(self):
        text = self.TEXT

        class Motor:
            fremd = True
            offloaded = False
            chat_format = type("F", (), {"fmt": "chatml", "thinking": False})()

            def chat(self, *a, **k):
                raise RuntimeError(text)
                yield ""

        async def lauf():
            c = G.GgufChat("Test")
            m = Motor()
            with patch.object(G, "denken", return_value=False), patch.object(G, "_motor", ("x", 1, m)), \
                    patch.object(G, "_lora_abgleich"):
                return [s async for s in c._erzeugen(m, [], 10, threading.Event())]
        with patch.object(G.gpuzustand, "pruefen", side_effect=AssertionError("nicht prüfen")), \
                patch.object(G._DBG, "fehler", side_effect=AssertionError("nicht über debugbericht.fehler")):
            with self.assertRaises(RuntimeError) as f:
                asyncio.run(lauf())
        self.assertEqual(str(f.exception), text)

    def test_entladen_beendet_den_server_auch_bei_kaputter_sitzung(self):
        geleert = []
        m = NS(fremd=True, leeren=lambda: geleert.append(1))
        self.addCleanup(setattr, G, "_motor", G._motor)
        G._motor = ("x/m.gguf", 1, m)
        with patch.object(G.gpuzustand, "kaputt", return_value=True), patch.object(G, "experten_speichern"):
            G.entladen("Test")
        self.assertEqual(geleert, [1])
        self.assertIsNone(G._motor)


# ---------------------------------------------------------------------------
# L-05: Werkzeugaufruf ohne Schluss nur nach echtem Ende ergänzen
# ---------------------------------------------------------------------------
class AbgerissenerAufruf(unittest.TestCase):
    d = W.dialekt("qwen_xml")
    TEIL = "Mach ich.\n<tool_call>\n<function=merken>\n<parameter=text>\ngrüner Tee\n"

    def test_strom(self):
        import actions
        ganz = W.Strom(self.d)
        self.assertEqual(actions.parse_actions(ganz(self.TEIL) + ganz.ende())[0],
                         [{"tool": "merken", "text": "grüner Tee"}])
        ab = W.Strom(self.d)
        raus = ab(self.TEIL) + ab.ende(abgeschnitten=True)
        self.assertEqual(actions.parse_actions(raus)[0], [])
        self.assertIn("abgeschnitten", raus)
        self.assertEqual(ab.aufrufe, 0)

    def _erzeugen(self, erzeugt: int, max_tokens: int = 100) -> str:
        teil = self.TEIL

        class Motor:
            chat_format = type("F", (), {"fmt": "chatml", "thinking": False})()

            def chat(self, *a, **k):
                self.last_ids = [1] * erzeugt
                yield teil

        async def lauf():
            c = G.GgufChat("Test")
            m = Motor()
            with patch.object(G, "denken", return_value=False), patch.object(G, "_motor", ("x", 1, m)), \
                    patch.object(G, "_lora_abgleich"):
                return "".join([s async for s in c._erzeugen(m, [], max_tokens, threading.Event(), sprache=self.d)])
        return asyncio.run(lauf())

    def test_erzeugen_mit_stopp_ergaenzt(self):
        import actions
        self.assertEqual(actions.parse_actions(self._erzeugen(5))[0], [{"tool": "merken", "text": "grüner Tee"}])

    def test_erzeugen_am_laengenlimit_fuehrt_nichts_aus(self):
        import actions
        raus = self._erzeugen(100)
        self.assertEqual(actions.parse_actions(raus)[0], [])
        self.assertIn("abgeschnitten", raus)

    def test_kontextende(self):
        self.assertTrue(G.am_limit(NS(last_ids=[1], _pos=4095, n_ctx=4096), 100))
        self.assertFalse(G.am_limit(NS(last_ids=[1], _pos=100, n_ctx=4096), 100))
        self.assertFalse(G.am_limit(NS(), 100))


# ---------------------------------------------------------------------------
# L-07 / L-12: Paketwahl beim Einrichten, --help und Kontext ohne --fit-ctx
# ---------------------------------------------------------------------------
def _zip(pfad: Path, dateien: dict) -> str:
    with zipfile.ZipFile(pfad, "w") as z:
        for name, inhalt in dateien.items():
            z.writestr(name, inhalt)
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


class Paketwahl(_Tmp):
    CPU = f"llama-{R.BUILD}-bin-win-cpu-x64.zip"
    VULKAN = f"llama-{R.BUILD}-bin-win-vulkan-x64.zip"

    def setUp(self):
        super().setUp()
        p = patch.object(R, "_daten", return_value=self.tmp)
        p.start()
        self.addCleanup(p.stop)
        R._GEPRUEFT.clear()
        R.ablage().mkdir(parents=True)
        shas = {n: _zip(R.ablage() / n, {"llama-server.exe": n.encode(), f"{n}.dll": b"x"})
                for n in (self.CPU, self.VULKAN)}
        p = patch.dict(R.PAKETE, shas)
        p.start()
        self.addCleanup(p.stop)

    def test_nur_die_gewaehlte_art(self):
        ok, zeilen = R.einrichten(art="cpu")
        self.assertTrue(ok, zeilen)
        self.assertEqual(R._liste()["pakete"], [self.CPU])
        self.assertEqual((R.ziel() / "llama-server.exe").read_bytes(), self.CPU.encode())
        self.assertFalse((R.ziel() / f"{self.VULKAN}.dll").exists())
        self.assertTrue(any(self.VULKAN in z and "übersprungen" in z for z in zeilen), zeilen)
        self.assertIsNone(R.pruefen())

    def test_gewaehlte_art_fehlt_beste_vorhandene(self):
        cuda = f"llama-{R.BUILD}-bin-win-cuda-13.5-x64.zip"
        with patch.object(R, "passende_pakete", return_value=([cuda], "Testkarte")):
            ok, zeilen = R.einrichten()
        self.assertTrue(ok, zeilen)
        self.assertEqual(R._liste()["pakete"], [self.VULKAN])


class HilfeUndKontext(_Tmp):
    def test_fehlschlag_von_help_wird_gemerkt(self):
        exe = self.tmp / "llama-server.exe"
        exe.write_bytes(b"s")
        aufrufe = []

        def hilfe(server):
            aufrufe.append(server)
            raise OSError("startet nicht")
        with patch.object(R, "server_pfad", return_value=exe), patch.object(R, "_hilfe", hilfe), \
                patch.dict(R._KANN, clear=True):
            self.assertEqual(R.kann(), frozenset())
            self.assertEqual(R.kann(), frozenset())
        self.assertEqual(len(aufrufe), 1)

    def test_ohne_fit_ctx_begrenzt(self):
        for n_ctx_max, soll in ((262144, R.OHNE_FIT_CTX), (0, R.OHNE_FIT_CTX), (4096, 4096)):
            s = R.Server(Path("m.gguf"), 0, n_ctx_max=n_ctx_max)
            s.port, s._kann = 1234, frozenset()
            a = s.argumente()
            self.assertEqual(a[a.index("-c") + 1], str(soll), n_ctx_max)


# ---------------------------------------------------------------------------
# L-08 / L-10 / L-11 / L-13 / G-10
# ---------------------------------------------------------------------------
class LadenUndZustand(unittest.TestCase):
    def test_reserve_laden_mit_abbruch_zuletzt_neu(self):
        gesehen = {}
        engine = NS(fremd=True, n_ctx=4096, server=None)

        def reserve(name, datei, ctx, abbruch=None):
            gesehen["abbruch"] = abbruch
            return engine
        self.addCleanup(setattr, G, "_motor", G._motor)
        G._motor = None
        alt = time.monotonic() - 10 * 3600
        with patch.object(G, "modelle", return_value={"Neu": Path("x/Neu.gguf")}), \
                patch.object(G, "kontext", return_value=8192), patch.object(G, "_torch_bereit"), \
                patch.object(G, "_config", return_value={}), patch.object(G, "motor_wahl", return_value=G.LLAMACPP), \
                patch.object(G, "_stimme_platz_machen"), patch.object(G, "entladen"), \
                patch.object(G, "_leerlauf_starten"), patch.object(G, "_reserve_laden", reserve), \
                patch.object(G, "_zuletzt", alt):
            self.assertIs(G._motor_laden("Neu"), engine)
            self.assertLess(time.monotonic() - G._zuletzt, 60)              # Leerlauf zählt ab dem Laden
            self.assertFalse(gesehen["abbruch"]())
            G._abbestellt += 1                                              # Modellwechsel während des Ladens
            self.assertTrue(gesehen["abbruch"]())

    def test_reserve_laden_reicht_abbruch_und_hinweis_weiter(self):
        gesehen = {}

        class Engine:
            motor_name = "llama.cpp"

            def __init__(self, pfad, **kw):
                gesehen.update(kw)
                self.n_ctx, self.server = 4096, None
        stopp = lambda: False                                               # noqa: E731
        with patch.object(G, "kontext_einstellung", return_value=8192), \
                patch.object(G, "sieht", return_value=False), patch.object(G, "_frei_vram", return_value=None), \
                patch.object(G, "kv_bytes_pro_token", return_value=None), patch.object(LM, "LlamaCppEngine", Engine):
            G._reserve_laden("m", Path("m.gguf"), 8192, abbruch=stopp)
        self.assertIs(gesehen["abbruch"], stopp)
        self.assertIs(gesehen["melden"], G._hinweis_setzen)

    def test_trenner_laesst_das_format_unberuehrt(self):
        from ggufengine.chat import ChatFormatter
        f = ChatFormatter(NS(special={"<think>": 1, "</think>": 2}), "chatml", thinking=False)
        t = G._trenner(NS(chat_format=f), True)
        self.assertTrue(t.denkt)                                            # Denkblock offen wie gewünscht
        self.assertFalse(f.thinking)

    def test_toter_code_weg(self):
        import models
        for name in ("_GGUF_FMT", "_meta_cache", "_MAX_KV", "_MAX_STR", "_MAX_ARR", "_MAX_DEPTH"):
            self.assertFalse(hasattr(models, name), name)


class TokenizerGrenze(_Tmp):
    def _datei(self, schluessel: str, n: int) -> Path:
        k = schluessel.encode()
        roh = (b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", len(k)) + k
               + struct.pack("<IIQ", GG.T_ARRAY, GG.T_STRING, n))
        p = self.tmp / "t.gguf"
        p.write_bytes(roh)
        return p

    def test_vokabular_hoechstens_eine_million(self):
        n = GG.MAX_TOKENIZER_ARRAY + 1
        with self.assertRaises(GGUFError) as f:
            GGUFFile(self._datei("tokenizer.ggml.tokens", n))
        self.assertIn(f"limit {GG.MAX_TOKENIZER_ARRAY}", str(f.exception))
        with self.assertRaises(GGUFError) as f:                             # andere Listen: die alte Grenze
            GGUFFile(self._datei("allgemein.liste", n))
        self.assertNotIn("exceeds limit", str(f.exception))


if __name__ == "__main__":
    unittest.main()
