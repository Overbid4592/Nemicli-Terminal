"""trtmotor und LoRA-Motor: Bau-Faden gegen Entladen, Bausperre und Zwischennamen, Rückfall bei Rechenfehlern,
Signaturprüfung, Aufräumen; Lern-LoRA-Abbruch, alpha-Vorgabe, Grundmodell-Kennung. Ohne TensorRT und ohne
Grafikkarte (alles Nötige ist ersetzt)."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import torch                                       # noqa: E402

from trtmotor import bild, chat, laufzeit, video   # noqa: E402

_NETZ = laufzeit.Netz                              # die Klasse selbst (Holen ersetzt sie)


def _netz():
    """Platzhalter eines gebauten Netzes mit den Teilen, die laufzeit.abbauen freigibt."""
    return NS(**{t: object() for t in ("ctx", "_puffer", "cache", "rc", "engine")}, name="krea-x")


def _abgebaut(n) -> bool:
    return all(getattr(n, t) is None for t in ("ctx", "_puffer", "cache", "rc", "engine"))


class _Ohne(unittest.TestCase):
    """Gemeinsam: GPU-Sperre ohne den GGUF-Motor, eigener engines-Ordner."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name) / "engines"
        for p in (mock.patch.object(laufzeit, "_sperre_gpu", threading.RLock()),
                  mock.patch.object(laufzeit, "engine_ordner", lambda: self.ordner)):
            p.start()
            self.addCleanup(p.stop)


# ----------------------------------------------------------------------------- T-01 Entladen während des Baus

class EntladenWaehrendDesBaus(_Ohne):

    def _bild(self, holen, mess=None, gruppen=((0, 1), (1, 2))):
        pfad = "probe.safetensors"
        bild._stand[pfad] = {"baut": True}
        self.addCleanup(bild._stand.clear)
        with mock.patch.object(laufzeit, "holen", side_effect=holen), \
                mock.patch.object(bild, "plan", lambda d: {}), \
                mock.patch.object(bild, "gruppen", lambda d: list(gruppen)), \
                mock.patch.object(bild, "_teile", lambda *a: {}), \
                mock.patch.object(bild, "_alte_netze_weg"), \
                mock.patch.object(laufzeit, "aufraeumen"):
            bild._netz_laden_oder_bauen(pfad, None, {"x": 1.0} if mess is None else mess)
        return pfad

    def test_bild_erfolg_nach_entladen_gibt_eigene_netze_frei(self):
        gebaut, waehrend = [], []

        def holen(*a, **k):
            waehrend.append(bild.baut())
            n = _netz()
            gebaut.append(n)
            bild.entladen()                                  # Ruheuhr oder Modellwechsel mitten im Bau
            return n
        pfad = self._bild(holen)
        self.assertEqual(bild._stand.get(pfad), None)
        self.assertEqual(len(gebaut), 1, "nach dem Entladen keine weiteren Gruppen")
        self.assertTrue(_abgebaut(gebaut[0]))
        self.assertEqual(waehrend, [True])
        self.assertFalse(bild.baut())

    def test_bild_fehler_nach_entladen_ohne_keyerror(self):
        gebaut = []

        def holen(*a, **k):
            if not gebaut:
                gebaut.append(_netz())
                return gebaut[0]
            bild.entladen()
            raise RuntimeError("kaputt")
        pfad = self._bild(holen)
        self.assertNotIn(pfad, bild._stand)
        self.assertTrue(_abgebaut(gebaut[0]), "schon gebaute Gruppe wird freigegeben")

    def test_bild_neuer_eintrag_wird_nicht_ueberschrieben(self):
        neu = {}

        def holen(*a, **k):
            bild.entladen()
            bild._stand["probe.safetensors"] = neu           # nächstes Bild hat schon neu angefangen
            return _netz()
        self._bild(holen)
        self.assertEqual(neu, {})

    def test_video_erfolg_nach_entladen(self):
        pfad, gebaut = "probe.gguf", []
        video._stand[pfad] = {"baut": True}
        self.addCleanup(video._stand.clear)

        def holen(*a, **k):
            gebaut.append(_netz())
            video.entladen()
            return gebaut[0]
        with mock.patch.object(laufzeit, "holen", side_effect=holen), \
                mock.patch.object(video, "_teile", lambda *a: {}), mock.patch.object(laufzeit, "aufraeumen"):
            video._laden_oder_bauen(pfad, {}, {}, {"x": 1.0}, "fp8")
        self.assertNotIn(pfad, video._stand)
        self.assertTrue(_abgebaut(gebaut[0]))
        self.assertFalse(video.baut())

    def test_ohne_einmessung_wird_nicht_gebaut(self):
        uebergeben = []
        holen = lambda art, teile, bauen, **k: uebergeben.append(bauen) or _netz()   # noqa: E731
        pfad = self._bild(holen, gruppen=[(0, 1)])
        self.assertIsNotNone(bild._stand[pfad].get("netz"))
        self._bild(holen, mess={}, gruppen=[(0, 1)])
        self.assertIsNotNone(uebergeben[0])
        self.assertIsNone(uebergeben[1], "ohne Messwerte nur laden")


# ----------------------------------------------------------------------------- T-02 Bausperre, Zwischennamen

class _Engine:
    streamable_weights_size = 0


class _Runtime:
    def __init__(self, log=None):
        pass

    def deserialize_cuda_engine(self, blob):
        return _Engine() if bytes(blob) == b"plan" else None


class _Logger:
    ERROR = 0

    def __init__(self, stufe=None):
        pass


def _trt(bauzeit=0.0, zaehler=None):
    """Ersatz für das Modul tensorrt_rtx: der Bau liefert den Plan b"plan"."""
    def build_serialized_network(netz, cfg):
        if zaehler is not None:
            zaehler.append(1)
        time.sleep(bauzeit)
        return b"plan"
    return NS(__version__="test", Logger=_Logger, Runtime=_Runtime,
              Builder=lambda log: NS(build_serialized_network=build_serialized_network))


class Holen(_Ohne):

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(laufzeit, "schluessel", lambda art, teile: f"{art}-test"),
                  mock.patch.object(laufzeit, "treiber_kennung", lambda: "t1"),
                  mock.patch.object(laufzeit, "Netz", lambda engine, cache, budget: NS(engine=engine, cache=cache))):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _trt(**kw):
        return _trt(**kw)

    def test_zwischennamen_eindeutig(self):
        e, c = laufzeit._zwischen(Path("a.engine")), laufzeit._zwischen(Path("a.t1.cache"))
        self.assertNotEqual(e, c)
        self.assertTrue(e.name.startswith("a.engine.") and e.name.endswith(".tmp"))
        self.assertIn(f".{os.getpid()}.", e.name)
        anderer = []
        t = threading.Thread(target=lambda: anderer.append(laufzeit._zwischen(Path("a.engine"))))
        t.start()
        t.join()
        self.assertNotEqual(anderer[0], e)

    def test_gleichzeitig_nur_ein_bau(self):
        gebaut = []
        trt = self._trt(bauzeit=0.3, zaehler=gebaut)
        ergebnisse = []
        with mock.patch.object(laufzeit, "laden", return_value=trt):
            faeden = [threading.Thread(target=lambda: ergebnisse.append(
                laufzeit.holen("chat", {}, lambda t, b: (None, None), quelle="m.gguf"))) for _ in range(3)]
            for f in faeden:
                f.start()
            for f in faeden:
                f.join()
        self.assertEqual(len(gebaut), 1)
        self.assertEqual(len(ergebnisse), 3)
        self.assertTrue((self.ordner / "chat-test.engine").is_file())
        self.assertEqual(sorted(p.name for p in self.ordner.iterdir()), ["chat-test.engine", "chat-test.json"])

    def test_verwaiste_sperre_wird_uebernommen_frische_abgewartet(self):
        self.ordner.mkdir(parents=True)
        sperre = self.ordner / "x.lock"
        sperre.write_text("1")
        alt = time.time() - laufzeit.SPERRE_ALT_S - 10
        os.utime(sperre, (alt, alt))
        with laufzeit._bausperre(self.ordner, "x"):
            self.assertGreater(sperre.stat().st_mtime, alt)       # eigene, neue Sperre
        self.assertFalse(sperre.exists())
        sperre.write_text("1")                                    # frisch: ein anderer Prozess baut
        drin = threading.Event()

        def warten():
            with laufzeit._bausperre(self.ordner, "x"):
                drin.set()
        t = threading.Thread(target=warten)
        t.start()
        time.sleep(0.3)
        self.assertFalse(drin.is_set())
        sperre.unlink()
        t.join(5)
        self.assertTrue(drin.is_set())

    def test_unlesbares_netz_geloescht_ohne_einmessung_kein_bau(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "chat-test.engine").write_bytes(b"kaputt")
        with mock.patch.object(laufzeit, "laden", return_value=self._trt()):
            with self.assertRaises(laufzeit.NichtGespeichert):
                laufzeit.holen("chat", {}, None)
            self.assertFalse((self.ordner / "chat-test.engine").exists())
            netz = laufzeit.holen("chat", {}, lambda t, b: (None, None))   # mit Einmessung: neu gebaut
        self.assertEqual(netz.cache.name, "chat-test.t1.cache")

    def test_alte_caches_und_zwischendateien(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "chat-test.engine").write_bytes(b"plan")
        for n in ("chat-test.cache", "chat-test.t0.cache", "chat-test.t1.cache", "alt.engine.1.2.tmp",
                  "neu.engine.1.2.tmp"):
            (self.ordner / n).write_bytes(b"x")
        alt = time.time() - laufzeit.TMP_ALTER_S - 10
        os.utime(self.ordner / "alt.engine.1.2.tmp", (alt, alt))
        laufzeit._aufgeraeumt.discard(self.ordner)
        with mock.patch.object(laufzeit, "laden", return_value=self._trt()):
            laufzeit.holen("chat", {}, None)
        self.assertEqual(sorted(p.name for p in self.ordner.iterdir()),
                         ["chat-test.engine", "chat-test.t1.cache", "neu.engine.1.2.tmp"])

    def test_aufraeumen_gleiche_modelldatei(self):
        self.ordner.mkdir(parents=True)
        for name, quelle in (("chat-a", "m.gguf"), ("chat-b", "m.gguf"), ("chat-c", "andere.gguf"),
                             ("wan-d", "m.gguf")):
            (self.ordner / f"{name}.engine").write_bytes(b"x")
            (self.ordner / f"{name}.t1.cache").write_bytes(b"x")
            (self.ordner / f"{name}.json").write_text(json.dumps({"art": name.split("-")[0], "quelle": quelle}))
        (self.ordner / "chat-e.engine").write_bytes(b"ohne Angabe")
        self.assertEqual(laufzeit.aufraeumen("chat", "m.gguf", {"chat-a"}), 1)
        self.assertEqual(sorted(p.stem.split(".")[0] for p in self.ordner.glob("*.engine")),
                         ["chat-a", "chat-c", "chat-e", "wan-d"])
        self.assertFalse(list(self.ordner.glob("chat-b*")))

    def test_cache_sichern_mit_eigenem_zwischennamen(self):
        self.ordner.mkdir(parents=True)
        n = object.__new__(_NETZ)
        n.cache_datei = self.ordner / "chat-test.t1.cache"
        n.cache = NS(serialize=lambda: memoryview(b"kerne"))
        geschrieben = []
        echt = Path.write_bytes
        with mock.patch.object(Path, "write_bytes", lambda p, d: (geschrieben.append(p.name), echt(p, d))[1]):
            n.cache_sichern()
        self.assertEqual(n.cache_datei.read_bytes(), b"kerne")
        self.assertTrue(geschrieben[0].startswith("chat-test.t1.cache.") and geschrieben[0].endswith(".tmp"))
        self.assertEqual([p.name for p in self.ordner.iterdir()], ["chat-test.t1.cache"])


# ----------------------------------------------------------------------------- T-03/T-08 Rechenfehler

class Rechenfehler(_Ohne):

    def test_bild_trtfehler_setzt_fehler_und_gibt_frei(self):
        pfad = "probe.safetensors"
        teil = _netz()

        class Kaputt(laufzeit.NetzKette):
            def __call__(self, *a):
                raise torch.cuda.OutOfMemoryError("voll")
        bild._stand[pfad] = {"netz": Kaputt([teil])}
        self.addCleanup(bild._stand.clear)
        rechnen = bild.bloecke(pfad)
        z = torch.zeros(1, 4, 8, dtype=torch.bfloat16)
        rope = (torch.zeros(1, 1, 4, 2), torch.zeros(1, 1, 4, 2))
        with mock.patch.object(torch.cuda, "synchronize"):
            with self.assertRaises(laufzeit.TrtFehler):
                rechnen(z, torch.zeros(1, 1, 48), rope)
        self.assertIn("voll", bild._stand[pfad]["fehler"])
        self.assertNotIn("netz", bild._stand[pfad])
        self.assertTrue(_abgebaut(teil))
        self.assertIsNone(bild.bloecke(pfad))
        self.assertTrue(issubclass(laufzeit.TrtFehler, RuntimeError))

    def test_video_trtfehler(self):
        pfad = "probe.gguf"
        netz = mock.Mock(side_effect=RuntimeError("TensorRT: Ausführung fehlgeschlagen"))
        video._stand[pfad] = {"netz": netz}
        self.addCleanup(video._stand.clear)
        rechnen = video.bloecke(pfad)
        x, e0 = torch.zeros(1, 4, 8), torch.zeros(1, 6, 8)
        freqs = torch.polar(torch.ones(4, 1, 2), torch.zeros(4, 1, 2))
        with mock.patch.object(torch.cuda, "synchronize"), mock.patch.object(laufzeit, "stand_abbauen") as ab:
            with self.assertRaises(laufzeit.TrtFehler):
                rechnen(x, e0, torch.zeros(512, 8), freqs, 1)
        self.assertTrue(video._stand[pfad]["fehler"])
        ab.assert_called_once()
        self.assertIsNone(video.bloecke(pfad))

    def test_chat_leser_faellt_auf_den_gguf_motor_zurueck(self):
        leser = object.__new__(chat.Leser)
        leser.netz = mock.Mock(side_effect=RuntimeError("TensorRT: kein Ausführungs-Kontext"))
        leser.plan, leser.tabellen, leser.laeufe, leser.raum = {"n_ctx": 8, "q_temp": None}, [], 0, None
        c = NS(gepackt=False, geteilt=False, k=torch.zeros(1, 8, 4, dtype=torch.bfloat16),
               v=torch.zeros(1, 8, 4, dtype=torch.bfloat16))
        modell = NS(device=torch.device("cpu"), n_ctx=8, layers=[NS(cache=c)], schnell=leser,
                    einbettung=lambda tok, inject: torch.zeros(len(tok), 4))
        self.assertIsNone(leser(modell, [1, 2, 3], 0))
        self.assertIsNone(modell.schnell)
        leser.netz.puffer_frei.assert_called_once()

    def test_kontext_none_ist_trtfehler(self):
        n = object.__new__(laufzeit.Netz)
        n.engine, n.rc = NS(create_execution_context=lambda rc: None), None
        with self.assertRaisesRegex(laufzeit.TrtFehler, "Ausführungs-Kontext"):
            n._kontext()

    def test_platz_unter_gpu_sperre(self):
        gesehen = []

        class Sperre:
            def __enter__(self):
                gesehen.append("rein")

            def __exit__(self, *a):
                gesehen.append("raus")
        n = object.__new__(laufzeit.Netz)
        n.reserve, n.neu_bemessen, n.streaming, n._puffer = 0, False, False, None
        with mock.patch.object(laufzeit, "_sperre_gpu", Sperre()), \
                mock.patch.object(torch.cuda, "empty_cache", side_effect=lambda: gesehen.append("leeren")), \
                mock.patch.object(torch.cuda, "mem_get_info", return_value=(10, 20)):
            n._platz(1, {})
        self.assertEqual(gesehen, ["rein", "leeren", "raus"])


# ----------------------------------------------------------------------------- T-07, T-11, T-13

class LadenUndMessen(_Ohne):

    def test_sofort_laden_nur_gespeichert(self):
        pfad = "probe.safetensors"
        self.addCleanup(bild._stand.clear)
        dit = NS(blocks=[1])
        with mock.patch.object(laufzeit, "bereit", return_value=True), \
                mock.patch.object(bild, "gespeichert", return_value=False), \
                mock.patch.object(laufzeit, "holen") as holen:
            self.assertFalse(bild.sofort_laden(pfad, dit))
        holen.assert_not_called()
        with mock.patch.object(laufzeit, "bereit", return_value=True), \
                mock.patch.object(bild, "gespeichert", return_value=True), \
                mock.patch.object(laufzeit, "holen", return_value=_netz()) as holen, \
                mock.patch.object(bild, "plan", lambda d: {}), mock.patch.object(bild, "_teile", lambda *a: {}), \
                mock.patch.object(bild, "gruppen", lambda d: [(0, 1)]), \
                mock.patch.object(bild, "_alte_netze_weg"), mock.patch.object(laufzeit, "aufraeumen"):
            self.assertTrue(bild.sofort_laden(pfad, dit))
        self.assertIsNone(holen.call_args.args[2], "sofort_laden baut nie")
        self.assertIsNotNone(bild.bloecke(pfad))

    def test_bild_misst_hoechstwert_aller_aufrufe(self):
        werte = {}
        m = bild._Messer(lambda x: x, "0.attn", werte)
        for w in (1.0, -3.0, 2.0):
            m(torch.tensor([w]))
        self.assertEqual(bild._zahlen(werte), {"0.attn": 3.0})
        v = video._Messer(lambda x: x, "0.ffn", werte)
        for w in (0.5, 4.0, -1.0):
            v(torch.tensor([w]))
        self.assertEqual(bild._zahlen(werte)["0.ffn"], 4.0)

    def test_video_braucht_jeden_eingang(self):
        pfad = "probe.gguf"
        self.addCleanup(video._stand.clear)
        n = 2
        video._stand[pfad] = {"mess": {f"{i}.{e}": 1.0 for i in range(n) for e in ("attn", "o", "cq", "ctx",
                                                                                     "co", "ffn")}}
        with mock.patch.object(laufzeit, "bereit", return_value=True), \
                mock.patch.object(video, "gespeichert", return_value=False), \
                mock.patch.object(video, "_laden_oder_bauen") as lauf:
            video.vorbereiten(pfad, {"num_layers": n}, {}, warten=True)
            lauf.assert_not_called()
            video._stand[pfad]["mess"].update({f"{i}.ffn2": 1.0 for i in range(n)})
            video.vorbereiten(pfad, {"num_layers": n}, {}, warten=True)
            lauf.assert_called_once()


# ----------------------------------------------------------------------------- T-06 Signaturen

class Signaturen(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sdk = Path(tmp.name)
        (self.sdk / "bin").mkdir()
        (self.sdk / "python").mkdir()
        for p in (mock.patch.object(laufzeit, "_signatur_pruefen", return_value=True),
                  mock.patch.object(os, "name", "nt")):
            p.start()
            self.addCleanup(p.stop)
        from wache import herkunft
        self.herkunft = herkunft

    def _signiert(self, unsigniert=()):
        def signatur(pfad):
            if any(n in pfad for n in unsigniert):
                return {"status": "NotSigned", "signierer": "", "gueltig": False}
            return {"status": "Valid", "signierer": "NVIDIA Corporation", "gueltig": True}
        return mock.patch.object(self.herkunft, "signatur", side_effect=signatur)

    def test_jede_dll_in_bin(self):
        (self.sdk / "bin" / "tensorrt_rtx_1_0.dll").write_bytes(b"x")
        (self.sdk / "bin" / "helfer.dll").write_bytes(b"x")
        with self._signiert(unsigniert=("helfer.dll",)):
            self.assertIn("helfer.dll", laufzeit.signatur_fehler(self.sdk))
        with self._signiert():
            self.assertIsNone(laufzeit.signatur_fehler(self.sdk))

    def _rad(self, mitglieder) -> Path:
        tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        whl = self.sdk / "python" / f"tensorrt_rtx-1.0-{tag}-none-win_amd64.whl"
        with zipfile.ZipFile(whl, "w") as z:
            for n in mitglieder:
                z.writestr(n, b"inhalt")
        return whl

    def test_wheel_vor_der_installation(self):
        whl = self._rad(["tensorrt_rtx/__init__.py", "tensorrt_rtx/tensorrt_rtx.pyd"])
        with self._signiert(unsigniert=("tensorrt_rtx.pyd",)):
            self.assertIn("tensorrt_rtx.pyd", laufzeit.rad_fehler(whl))
            with mock.patch.object(laufzeit.subprocess, "run") as pip:
                meldung = laufzeit._selbst_installieren(self.sdk)
            pip.assert_not_called()
            self.assertIn("nicht gültig von NVIDIA signiert", meldung)
        with self._signiert():
            self.assertIsNone(laufzeit.rad_fehler(whl))
        self.assertIn("keine signierte", laufzeit.rad_fehler(self._rad(["tensorrt_rtx/__init__.py"])))


# ----------------------------------------------------------------------------- R-01 Lern-LoRA-Abbruch

class LernLoraAbbruch(unittest.TestCase):

    def test_abbruch_laesst_alte_themen_stehen(self):
        from loramotor import adapter as A, lernlora as LL, training as T
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ziel = Path(tmp.name)
        (ziel / "thema_1.safetensors").write_bytes(b"alt 1")
        (ziel / "themen.json").write_text(json.dumps({"basis": "m.gguf|1", "themen": [{"datei": "thema_1.safetensors"}]}))
        runde = []

        def trainieren(**kw):
            runde.append(kw["ziel"].name)
            if len(runde) == 2:
                raise RuntimeError("Angehalten bei Schritt 3/90")
            kw["ziel"].write_bytes(b"neu")
            return kw["ziel"]
        bsp = [{"system": "s", "frage": f"Frage {i}", "antwort": "a" * 30, "gewicht": 1.0} for i in range(60)]
        themen = [{"name": "a", "stichworte": [], "mitte": None, "beispiele": bsp[:30]},
                  {"name": "b", "stichworte": [], "mitte": None, "beispiele": bsp[30:]}]
        with mock.patch.object(LL, "ordner", return_value=ziel), mock.patch.object(LL, "modell_pruefen"), \
                mock.patch.object(LL, "beispiele", return_value=bsp), \
                mock.patch.object(LL, "themen_bilden", return_value=themen), \
                mock.patch.object(A, "basis_kennung", return_value="m.gguf|1"), \
                mock.patch.object(T, "trainieren", side_effect=trainieren):
            with self.assertRaises(RuntimeError):
                LL.trainieren(ziel / "m.gguf")
        self.assertEqual(runde, ["thema_1.neu.safetensors", "thema_2.neu.safetensors"])
        self.assertEqual((ziel / "thema_1.safetensors").read_bytes(), b"alt 1")
        self.assertEqual(json.loads((ziel / "themen.json").read_text())["themen"], [{"datei": "thema_1.safetensors"}])
        self.assertEqual(list(ziel.glob("*.neu.safetensors")), [])

    def test_lohnt_meldet_ausnahme(self):
        from loramotor import adapter as A, lernlora as LL
        with mock.patch.object(LL, "themen_laden", return_value={"themen": [1]}), \
                mock.patch.object(LL, "stand", return_value={}), \
                mock.patch.object(LL, "beispiele", side_effect=OSError("Chats unlesbar")), \
                mock.patch.object(A, "_melden") as melden:
            self.assertFalse(LL.lohnt("m.gguf"))
        self.assertIn("Chats unlesbar", melden.call_args.args[0])


# ----------------------------------------------------------------------------- R-03, R-04

class LoraKennung(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_bild_lora_ohne_alpha(self):
        import lora as L
        from safetensors.torch import save_file
        pfad = self.tmp / "ohne_alpha.safetensors"
        save_file({"blocks.0.x.lora_A": torch.zeros(4, 8), "blocks.0.x.lora_B": torch.zeros(8, 4)}, str(pfad),
                  metadata={"rang": "4"})
        teile, _ = L.laden(pfad)
        self.assertEqual(teile["blocks.0.x"][2], 1.0)
        save_file({"blocks.0.x.lora_A": torch.zeros(4, 8), "blocks.0.x.lora_B": torch.zeros(8, 4)}, str(pfad),
                  metadata={"rang": "4", "alpha": "8"})
        self.assertEqual(L.laden(pfad)[0]["blocks.0.x"][2], 2.0)

    def test_kennung_mit_zeit_alte_bleiben_gueltig(self):
        from loramotor import adapter as A
        gguf = self.tmp / "m.gguf"
        gguf.write_bytes(b"1234")
        name, groesse, zeit = A.basis_kennung(gguf).split("|")
        self.assertEqual((name, groesse, zeit), ("m.gguf", "4", str(gguf.stat().st_mtime_ns)))
        self.assertTrue(A.basis_passt("m.gguf|4", gguf))                   # ältere Kennung ohne Zeit
        self.assertTrue(A.basis_passt(A.basis_kennung(gguf), gguf))
        self.assertFalse(A.basis_passt("m.gguf|4|1", gguf))
        self.assertTrue(A.basis_passt("m.gguf|4|1", gguf, zeit=False))      # mitgeliefertes LoRA
        self.assertFalse(A.basis_passt("m.gguf|5", gguf))
        self.assertFalse(A.basis_passt(None, gguf))

    def test_anklinken_ohne_treffer_ist_fehler(self):
        from loramotor import adapter as A
        gguf = self.tmp / "m.gguf"
        gguf.write_bytes(b"1234")
        datei = self.tmp / "fremd.safetensors"
        A.speichern(datei, {"blk.0.attn_q.weight": (torch.zeros(2, 8), torch.zeros(8, 2))},
                    {"basis": A.basis_kennung(gguf), "rang": 2})
        modell = NS()
        with self.assertRaisesRegex(ValueError, "keine Schicht"):
            A.anklinken(modell, datei, gguf)
        self.assertEqual(A.angeklinkt(modell), "")


if __name__ == "__main__":
    unittest.main()
