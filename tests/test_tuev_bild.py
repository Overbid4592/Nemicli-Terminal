"""Bild-Motoren: Aufräumen nach ESC, Abbruch externer Dienste, Grenzen und Speicher (ohne Modelle, ohne GPU)."""

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui", ""):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import krea  # noqa: E402
import notstopp  # noqa: E402

try:
    import numpy as np
    import torch
except Exception:                                    # pragma: no cover
    np = torch = None


@contextlib.contextmanager
def esc():
    """ESC im Vordergrund dieses Fadens."""
    token = notstopp.vordergrund(True)
    notstopp._zustand["ausgeloest"] = True
    try:
        yield
    finally:
        notstopp._test_reset()
        notstopp.vordergrund_ende(token)


def _krea_attrappe(male, **weitere):
    """Patches, mit denen krea.generate ohne Modell läuft. `male`: Rückgabe von _male oder dessen side_effect
    (Funktion, Ausnahme oder Ausnahmeklasse)."""
    wirkt = callable(male) or isinstance(male, BaseException)
    return [mock.patch.object(krea, "missing_reason", return_value=None),
            mock.patch.object(krea, "discover", return_value={"k2": Path("k2.safetensors")}),
            mock.patch.object(krea, "teile", return_value={"text": None, "vae": None}),
            mock.patch.object(krea, "_male", **({"side_effect": male} if wirkt else {"return_value": male}))] + [
        mock.patch.object(krea, k, v) for k, v in weitere.items()]


class KreaNachEsc(unittest.TestCase):
    """B-01: Aufräumen und Entlade-Uhr laufen auch nach ESC; ESC geht ohne Traceback weiter."""

    def test_generate_raeumt_auf_und_wirft_abgebrochen(self):
        frei, uhr = mock.Mock(), mock.Mock()
        with contextlib.ExitStack() as st:
            for p in _krea_attrappe(notstopp.Abgebrochen, _frei=frei, _uhr_neu=uhr):
                st.enter_context(p)
            st.enter_context(mock.patch("config.load", return_value={}))
            with self.assertRaises(notstopp.Abgebrochen) as e:
                krea.generate("a fox", seed=1)
        frei.assert_called()
        uhr.assert_called_once()
        self.assertIsNone(e.exception.__context__)          # kein Traceback, der Tensoren festhielte

    def test_fehler_bleibt_nur_text(self):
        with contextlib.ExitStack() as st:
            for p in _krea_attrappe(ValueError("kaputt"), _frei=mock.Mock(), _uhr_neu=mock.Mock()):
                st.enter_context(p)
            st.enter_context(mock.patch("config.load", return_value={}))
            with self.assertRaisesRegex(RuntimeError, "kaputt"):
                krea.generate("a fox", seed=1)

    def test_gesicht_raeumt_nach_esc_auf(self):
        import gesichter
        from PIL import Image, PngImagePlugin
        frei, uhr = mock.Mock(), mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            quelle = Path(d) / "b.png"
            info = PngImagePlugin.PngInfo()
            info.add_text("model", "k2")
            info.add_text("params", "steps=14, seed=41, sampler=euler, backend=krea2")
            Image.new("RGB", (256, 256), (100, 100, 100)).save(quelle, pnginfo=info)
            with contextlib.ExitStack() as st:
                for p in _krea_attrappe(notstopp.Abgebrochen, _frei=frei, _uhr_neu=uhr):
                    st.enter_context(p)
                st.enter_context(mock.patch.object(krea, "_geraet", return_value=("cpu", None)))
                st.enter_context(mock.patch.object(gesichter, "grund", return_value=None))
                st.enter_context(mock.patch.object(gesichter, "finden", return_value=[(100, 80, 180, 180)]))
                st.enter_context(mock.patch("config.load", return_value={}))
                with self.assertRaises(notstopp.Abgebrochen):
                    krea.gesicht_nachbessern(str(quelle))
        frei.assert_called()
        uhr.assert_called_once()

    @unittest.skipIf(torch is None, "torch fehlt")
    def test_male_gibt_tensorrt_gewichte_nach_esc_frei(self):
        dit = mock.Mock()
        dit.text.return_value = torch.zeros(1)
        with mock.patch.object(krea, "_text_einbetten", return_value=torch.zeros(1)), \
                mock.patch.object(krea, "_dit_holen", return_value=dit), \
                mock.patch.object(krea, "_sample", side_effect=notstopp.Abgebrochen), \
                mock.patch.object(krea, "_trt_nach_bild") as nach:
            with self.assertRaises(notstopp.Abgebrochen):
                krea._male("p", "k2", Path("m"), {"text": None, "vae": None}, 8, "euler", 1.0, 256, 256, 1,
                           "cpu", torch.float32, None)
        nach.assert_called_once_with(Path("m"), bauen=False)


class KreaGrenzen(unittest.TestCase):
    """B-10, B-13, B-16: Schritte begrenzt, Gegen-Prompt des Aufrufs, Kante beim Nachmalen."""

    def _malen(self, config: dict, model="k2", **kw):
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as st:
            for p in _krea_attrappe(np.zeros((8, 8, 3), np.uint8), _uhr_neu=mock.Mock(), OUT_DIR=Path(d)):
                st.enter_context(p)
            st.enter_context(mock.patch.object(krea, "discover", return_value={model: Path(f"{model}.safetensors")}))
            st.enter_context(mock.patch("profilordner.bild_ziel", return_value=Path(d) / "b.png"))
            st.enter_context(mock.patch("config.load", return_value=config))
            krea.generate("x", seed=1, **kw)
            return krea._male.call_args

    @unittest.skipIf(np is None, "numpy fehlt")
    def test_schritte_werden_begrenzt(self):
        self.assertEqual(krea.SCHRITTE_MIN, self._malen({}, steps=-5).args[4])
        self.assertEqual(krea.SCHRITTE_MAX, self._malen({}, steps=150).args[4])
        self.assertEqual(krea.RAW_SCHRITTE_MAX, self._malen({}, model="krea2_raw", steps=500).args[4])

    @unittest.skipIf(np is None, "numpy fehlt")
    def test_gegen_prompt_des_aufrufs_kommt_dazu(self):
        aufruf = self._malen({"bild_krea_cfg": 1.5, "bild_krea_negativ": "blurry"}, neg="watermark")
        self.assertTrue(aufruf.kwargs["gegen"].startswith("watermark, blurry"))
        aufruf = self._malen({}, neg="watermark")                  # CFG 1: ruht
        self.assertEqual("", aufruf.kwargs["gegen"])

    @unittest.skipIf(np is None, "numpy fehlt")
    def test_imagegen_reicht_neg_an_krea(self):
        import imagegen
        with mock.patch.object(imagegen, "backend", return_value="krea"), \
                mock.patch.object(krea, "generate", return_value="b.png") as gen:
            imagegen.paint.__wrapped__("a fox", neg="watermark")
        self.assertEqual("watermark", gen.call_args.kwargs["neg"])

    def test_gesicht_kante_begrenzt(self):
        import gesichter
        from PIL import Image, PngImagePlugin
        with tempfile.TemporaryDirectory() as d:
            quelle = Path(d) / "b.png"
            info = PngImagePlugin.PngInfo()
            info.add_text("model", "k2")
            info.add_text("params", "seed=41, backend=krea2")
            Image.new("RGB", (256, 256), (100, 100, 100)).save(quelle, pnginfo=info)
            for kante, erwartet in ((99999, krea.MAX_EDGE), (10, 256)):
                with contextlib.ExitStack() as st:
                    for p in _krea_attrappe(lambda *a, **k: np.zeros((a[8], a[7], 3), np.uint8),
                                            _frei=mock.Mock(), _uhr_neu=mock.Mock()):
                        st.enter_context(p)
                    st.enter_context(mock.patch.object(krea, "_geraet", return_value=("cpu", None)))
                    st.enter_context(mock.patch.object(gesichter, "grund", return_value=None))
                    st.enter_context(mock.patch.object(gesichter, "finden", return_value=[(100, 80, 180, 180)]))
                    st.enter_context(mock.patch.object(gesichter, "einsetzen", side_effect=lambda arr, box, neu: arr))
                    groesse = st.enter_context(mock.patch.object(krea, "_arbeitsgroesse", wraps=krea._arbeitsgroesse))
                    st.enter_context(mock.patch("config.load", return_value={"bild_krea_gesicht_kante": kante}))
                    krea.gesicht_nachbessern(str(quelle))
                self.assertEqual(erwartet, groesse.call_args.args[2])


class KreaTokenizer(unittest.TestCase):
    """B-05: ohne tokenizer_config.json kein stilles Zerlegen der Sondermarken."""

    def test_ohne_tokenizer_config(self):
        with tempfile.TemporaryDirectory() as d:
            o = Path(d)
            (o / "vocab.json").write_text(json.dumps({"a": 0, "b": 1, "ab": 2}), encoding="utf-8")
            (o / "merges.txt").write_text("#version: 0.2\na b\n", encoding="utf-8")
            self.assertFalse(krea._tokenizer_da(o))
            with self.assertRaises(FileNotFoundError):
                krea._tokenizer_bauen(o)
            (o / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            self.assertTrue(krea._tokenizer_da(o))


class KreaLoraAusBild(unittest.TestCase):
    """B-21: eine LoRA aus PNG-Angaben nur aus den eigenen LoRA-Ordnern."""

    def test_nur_aus_lora_ordnern(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as fremd:
            erlaubt = Path(d) / "LoRA"
            erlaubt.mkdir()
            gut = erlaubt / "figur.safetensors"
            gut.write_bytes(b"x")
            boese = Path(fremd) / "x.safetensors"
            boese.write_bytes(b"x")
            meta = {"params": "lora=figur:0.7"}
            with mock.patch.object(krea, "_lora_orte", return_value=[erlaubt]):
                self.assertEqual((gut, 0.7), krea._bild_lora({**meta, "lora_datei": str(gut)}))
                self.assertEqual((None, 1.0), krea._bild_lora({**meta, "lora_datei": str(boese)}))
                umweg = erlaubt / ".." / ".." / Path(fremd).name / "x.safetensors"
                self.assertEqual((None, 1.0), krea._bild_lora({**meta, "lora_datei": str(umweg)}))


@unittest.skipIf(torch is None, "torch fehlt")
class KreaRam(unittest.TestCase):
    """B-15: Ersetzen eines RAM-Eintrags gibt den alten gepinnten Speicher frei."""

    def setUp(self):
        krea._RAM.clear()
        self.addCleanup(krea._RAM.clear)

    def test_ersetzen_leert_den_host_speicher(self):
        bauen = mock.Mock(side_effect=lambda: {"w": torch.ones(2)})
        with mock.patch.object(krea, "_frei"), mock.patch.object(krea, "_host_leeren") as leeren:
            krea._auf_gpu("dit", Path("a"), bauen, "meta")
            leeren.assert_not_called()
            krea._auf_gpu("dit", Path("a"), bauen, "meta")
            leeren.assert_not_called()
            krea._auf_gpu("dit", Path("b"), bauen, "meta")
            leeren.assert_called_once()


class TensorRT(unittest.TestCase):
    """Schnittstelle zu trtmotor: laufender Bau zählt als beschäftigt, TrtFehler -> einmal ohne TensorRT."""

    def test_bau_haelt_die_ruheuhr_an(self):
        leer = types.SimpleNamespace(laufend=lambda: [])
        for baut, erwartet in ((True, True), (False, False)):
            with mock.patch.dict(sys.modules, {"trtmotor.bild": types.SimpleNamespace(baut=lambda b=baut: b),
                                               "hintergrund": leer}), \
                    mock.patch.object(notstopp, "runde_laeuft", return_value=False):
                self.assertEqual(erwartet, krea._beschaeftigt())
        with mock.patch.dict(sys.modules, {"trtmotor.bild": types.SimpleNamespace(), "hintergrund": leer}), \
                mock.patch.object(notstopp, "runde_laeuft", return_value=False):
            self.assertFalse(krea._beschaeftigt())                 # ältere Fassung ohne baut()

    def _tb(self, baut: bool, laedt: bool):
        stand = {"netz": None}
        tb = types.SimpleNamespace(
            baut=lambda: baut,
            bloecke=lambda pfad: stand["netz"],
            sofort_laden=mock.Mock(side_effect=lambda pfad, ram: stand.update(netz="trt-bloecke" if laedt else None)
                                   or laedt),
            einmessen=mock.Mock())
        return tb

    def test_gespeichertes_netz_wird_sofort_geladen(self):
        ram = object.__new__(krea._DiT)
        ram.dtype, ram.blocks = None, ["block"]
        krea._RAM["dit"] = (Path("m"), ram)
        self.addCleanup(krea._RAM.clear)
        tb = self._tb(baut=False, laedt=True)
        with mock.patch.object(krea, "_trt", return_value=tb), mock.patch.object(krea, "_auf_gpu") as eigen:
            dit = krea._dit_holen(Path("m"), "cpu", None, None)
        self.assertEqual("trt-bloecke", dit.bloecke)
        self.assertEqual([], dit.blocks)                           # die Blöcke kommen nicht auf die Karte
        eigen.assert_not_called()
        tb = self._tb(baut=True, laedt=True)                       # Bau läuft: nicht dazwischen laden
        with mock.patch.object(krea, "_trt", return_value=tb), \
                mock.patch.object(krea, "_auf_gpu", return_value="eigen"):
            self.assertEqual("eigen", krea._dit_holen(Path("m"), "cpu", None, None))
        tb.sofort_laden.assert_not_called()
        tb = self._tb(baut=False, laedt=False)                     # nichts gespeichert: wie bisher
        with mock.patch.object(krea, "_trt", return_value=tb), \
                mock.patch.object(krea, "_auf_gpu", return_value="eigen"):
            self.assertEqual("eigen", krea._dit_holen(Path("m"), "cpu", None, None))
        tb.einmessen.assert_called_once()

    def test_trtfehler_einmal_ohne_tensorrt(self):
        class TrtFehler(RuntimeError):
            pass
        laufzeit = types.SimpleNamespace(TrtFehler=TrtFehler)
        with mock.patch.dict(sys.modules, {"trtmotor.laufzeit": laufzeit}), mock.patch.object(krea, "_frei"):
            with mock.patch.object(krea, "_male", side_effect=[TrtFehler("Netz"), "bild"]) as male:
                self.assertEqual("bild", krea._male_mit_ersatz(1, 2, cfg=0.0))
            self.assertEqual(2, male.call_count)
            self.assertIs(False, male.call_args.kwargs["trt"])
            with mock.patch.object(krea, "_male", side_effect=[TrtFehler("a"), TrtFehler("b")]) as male:
                with self.assertRaisesRegex(TrtFehler, "b"):
                    krea._male_mit_ersatz(1)
            self.assertEqual(2, male.call_count)                   # genau einmal wiederholt
            with mock.patch.object(krea, "_male", side_effect=ValueError("anders")) as male:
                with self.assertRaises(ValueError):
                    krea._male_mit_ersatz(1)
            self.assertEqual(1, male.call_count)
        with mock.patch.dict(sys.modules, {"trtmotor.laufzeit": types.SimpleNamespace()}), \
                mock.patch.object(krea, "_male", side_effect=RuntimeError("x")) as male:
            with self.assertRaises(RuntimeError):
                krea._male_mit_ersatz(1)                           # ohne TrtFehler-Klasse: wie bisher
            self.assertEqual(1, male.call_count)


class Bildmotoren(unittest.TestCase):
    """B-02, B-07, B-08, B-14, B-15: bildmotoren/ und gemeinsam.py."""

    def _teile(self):
        return {"name": "m", "bauart": "zimage", "dit": None, "text": None, "vae": None}

    def test_generate_raeumt_nach_esc_auf(self):
        import bildmotoren
        from bildmotoren import gemeinsam, zimage
        with mock.patch.object(bildmotoren, "_aufloesen", return_value="m"), \
                mock.patch.object(bildmotoren, "missing_reason", return_value=None), \
                mock.patch.object(bildmotoren, "teile", return_value=self._teile()), \
                mock.patch.object(bildmotoren, "schritte", return_value=8), \
                mock.patch.object(bildmotoren, "vorgaben", return_value={"groesse": (512, 512)}), \
                mock.patch.object(gemeinsam, "geraet", return_value=("cpu", None)), \
                mock.patch.object(gemeinsam, "aufraeumen") as auf, \
                mock.patch.object(gemeinsam, "uhr_neu") as uhr, \
                mock.patch.object(gemeinsam, "fremde_freigeben") as fremd, \
                mock.patch.object(zimage, "malen", side_effect=notstopp.Abgebrochen):
            with self.assertRaises(notstopp.Abgebrochen) as e:
                bildmotoren.generate("a fox", seed=1)
        auf.assert_called()
        uhr.assert_called_once()
        fremd.assert_not_called()                                  # nach ESC nichts Gültiges wegwerfen
        self.assertIsNone(e.exception.__context__)

    @unittest.skipIf(torch is None, "torch fehlt")
    def test_anheften_an_ort_und_stelle(self):
        from bildmotoren import gemeinsam
        from bildmotoren.gewichte import Lin
        lin = Lin("dicht", torch.ones(2, 2), bias=torch.zeros(2), n_out=2, n_in=2)
        lin._pl = "alt"
        a, b = torch.ones(3), torch.ones(4)
        obj = {"a": a, "l": [lin, (b,)]}
        with mock.patch.object(torch.Tensor, "pin_memory", lambda self: self.clone()):
            raus = gemeinsam.anheften(obj)
        self.assertIs(raus, obj)
        self.assertIsNot(obj["a"], a)                              # Original ersetzt, nicht daneben behalten
        self.assertIs(obj["l"][0], lin)
        self.assertIsNone(lin._pl)
        self.assertIsNot(obj["l"][1][0], b)
        self.assertTrue(torch.equal(obj["l"][1][0], b))

    @unittest.skipIf(torch is None, "torch fehlt")
    def test_aus_ram_ersetzt_und_raeumt_fremde_weg(self):
        from bildmotoren import gemeinsam
        gemeinsam._RAM.clear()
        self.addCleanup(gemeinsam._RAM.clear)
        with mock.patch.object(gemeinsam, "aufraeumen"), mock.patch.object(gemeinsam, "_host_leeren") as leeren, \
                mock.patch("torch.cuda.is_available", return_value=False):
            gemeinsam.aus_ram("clip", "alt", lambda: {"w": torch.ones(1)})
            gemeinsam.aus_ram("dit", "a", lambda: {"w": torch.ones(1)})
            leeren.assert_not_called()
            gemeinsam.durchgang_beginnt()
            gemeinsam.aus_ram("dit", "b", lambda: {"w": torch.ones(1)})
            leeren.assert_called_once()                            # alter Eintrag gepinnt: zurück an Windows
            self.assertEqual(["clip"], gemeinsam.fremde_freigeben())
            self.assertEqual(["dit"], gemeinsam.im_ram())

    def test_entladen_nicht_bei_kaputter_cuda_sitzung(self):
        from bildmotoren import gemeinsam
        gemeinsam._RAM["x"] = ("k", object())
        self.addCleanup(gemeinsam._RAM.clear)
        with mock.patch.dict(sys.modules, {"gpuzustand": types.SimpleNamespace(kaputt=lambda: True)}), \
                mock.patch.object(gemeinsam, "aufraeumen") as auf:
            gemeinsam.entladen()
        self.assertEqual(["x"], gemeinsam.im_ram())
        auf.assert_not_called()
        with mock.patch.dict(sys.modules, {"gpuzustand": types.SimpleNamespace(kaputt=lambda: False)}), \
                mock.patch.object(gemeinsam, "aufraeumen"), mock.patch.object(gemeinsam, "_host_leeren"):
            gemeinsam.entladen()
        self.assertEqual([], gemeinsam.im_ram())

    def test_sha256_meldet_fortschritt_und_bricht_ab(self):
        import hashlib
        import bildmotoren
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.safetensors"
            p.write_bytes(b"abc" * 1000)
            meldungen = []
            with mock.patch.object(bildmotoren, "PRUEF_TAKT", 1):
                self.assertEqual(hashlib.sha256(b"abc" * 1000).hexdigest(), bildmotoren._sha256(p, meldungen.append))
                self.assertTrue(meldungen and meldungen[-1].endswith("100 %"))
                with esc(), self.assertRaises(notstopp.Abgebrochen):
                    bildmotoren._sha256(p)


class Quelltext(unittest.TestCase):
    """B-06, B-12, B-17: Stellen, die sich ohne Modell nur am Quelltext prüfen lassen."""

    def test_lora_proben_ueber_die_gpu_huelle(self):
        quelle = (ROOT / "main_dienste.py").read_text(encoding="utf-8")
        teil = quelle[quelle.index("def _proben("):quelle.index("def _training(")]
        self.assertIn("imagegen._gpu_fuer_bild(krea.generate)", teil)
        self.assertIn("halt.is_set()", teil)
        self.assertIn("on_status=melden", teil)
        self.assertNotIn("krea.generate(prompt", teil)

    def test_sd_ohne_nullen_zweig(self):
        quelle = (ROOT / "engines" / "bildmotoren" / "sd.py").read_text(encoding="utf-8")
        self.assertNotIn("zeros_like(kontext", quelle)

    def test_qwenimage_vae_aus_der_ablage(self):
        quelle = (ROOT / "engines" / "bildmotoren" / "qwenimage.py").read_text(encoding="utf-8")
        self.assertIn('aus_ram("vae"', quelle)


class Malsperre(unittest.TestCase):
    """B-04, B-20: Warten meldet sich je Takt und hört auf ESC; Marken mit Startzeit."""

    def setUp(self):
        import malsperre
        self.m = malsperre
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name) / "malsperre"
        p = mock.patch.object(malsperre, "ordner", return_value=self.ordner)
        p.start()
        self.addCleanup(p.stop)

    def _anderer_faden(self):
        drin, los = threading.Event(), threading.Event()

        def halten():
            with self.m.halten():
                drin.set()
                los.wait(10)
        t = threading.Thread(target=halten, daemon=True)
        t.start()
        self.assertTrue(drin.wait(5))

        def ende():
            los.set()
            t.join(5)
        self.addCleanup(ende)
        return ende

    def test_zweiter_faden_meldet_und_hoert_auf_esc(self):
        self._anderer_faden()
        meldungen = []

        def melde(text):
            meldungen.append(text)
            if len(meldungen) >= 2:
                raise notstopp.Abgebrochen()
        start = time.monotonic()
        with self.assertRaises(notstopp.Abgebrochen):
            with self.m.halten(melde):
                self.fail("darf nicht malen, solange der andere Faden hält")
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(all("wartet" in m for m in meldungen))

    def test_zweiter_faden_esc_ohne_on_status_und_grenze(self):
        self._anderer_faden()
        with esc(), self.assertRaises(notstopp.Abgebrochen):
            with self.m.halten():
                pass
        with self.assertRaises(self.m.Belegt):
            with self.m.halten(max_warten=0.3):
                pass

    def test_nach_dem_anderen_faden_frei(self):
        ende = self._anderer_faden()
        threading.Timer(0.4, ende).start()
        with self.m.halten():
            self.assertTrue((self.ordner / f"malt_{os.getpid()}").exists())

    def test_marke_traegt_die_startzeit(self):
        with self.m.halten():
            inhalt = json.loads((self.ordner / f"malt_{os.getpid()}").read_text(encoding="utf-8"))
        self.assertAlmostEqual(self.m.startzeit(), inhalt["start"], delta=1.0)

    def test_neu_vergebene_pid_zaehlt_nicht(self):
        self.ordner.mkdir(parents=True)
        lebend = os.getppid()
        (self.ordner / f"malt_{lebend}").write_text(json.dumps({"zeit": 1.0, "start": 1.0}), encoding="utf-8")
        self.assertEqual([], self.m.fremde())                       # andere Startzeit: anderer Prozess
        self.assertFalse((self.ordner / f"malt_{lebend}").exists())
        (self.ordner / f"malt_{lebend}").write_text("1700000000.0", encoding="utf-8")   # alte Marke ohne Startzeit
        self.assertEqual([("malt", lebend)], self.m.fremde())
        self.m.marken_weg(lebend)
        self.assertFalse((self.ordner / f"malt_{lebend}").exists())


class _Antwort:
    def __init__(self, daten=None, status=200):
        self._daten, self.status_code = daten or {}, status

    def json(self):
        return self._daten

    def raise_for_status(self):
        pass


class WebUI(unittest.TestCase):
    """B-03, B-11, B-19: ESC während txt2img, Sperre für Config-Texte, UTF-8 in PNG-Texten."""

    def _client(self, posts, frei):
        class Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, pfad):
                return _Antwort({"progress": 0.5, "state": {"sampling_step": 1, "sampling_steps": 2}})

            def post(self, pfad, json=None):
                posts.append(pfad)
                if pfad == "/sdapi/v1/txt2img":
                    frei.wait(5)
                    return _Antwort({"images": []})
                if pfad == "/sdapi/v1/interrupt":
                    frei.set()
                return _Antwort()
        return Client

    def test_esc_unterbricht_die_webui(self):
        import sdwebui
        posts, frei = [], threading.Event()
        with mock.patch.object(sdwebui, "_client", self._client(posts, frei)):
            start = time.monotonic()
            with esc(), self.assertRaises(notstopp.Abgebrochen):
                sdwebui._txt2img({"prompt": "x"})
        self.assertLess(time.monotonic() - start, 2)
        self.assertIn("/sdapi/v1/interrupt", posts)

    def test_ohne_esc_mit_fortschritt(self):
        import sdwebui
        posts, frei = [], threading.Event()
        threading.Timer(0.8, frei.set).start()
        meldungen = []
        with mock.patch.object(sdwebui, "_client", self._client(posts, frei)):
            self.assertEqual({"images": []}, sdwebui._txt2img({"prompt": "x"}, meldungen.append))
        self.assertNotIn("/sdapi/v1/interrupt", posts)
        self.assertTrue(any("Schritt 1/2" in m for m in meldungen))

    def test_config_texte_gehen_durch_die_sperre(self):
        import bildsperre
        import sdwebui
        gesehen = []
        echt = bildsperre.pruefen

        def spion(prompt, neg=None):
            gesehen.append((prompt, neg))
            return echt(prompt, neg)
        werte = {"positiv": "VORSATZ", "neg": "GEGENTEIL"}
        with mock.patch.object(sdwebui, "_wcfg", lambda k, fb: werte.get(k, fb)), \
                mock.patch.object(sdwebui, "available", return_value=True), \
                mock.patch.object(sdwebui, "chosen_model", return_value=None), \
                mock.patch.object(sdwebui, "current_model", return_value=None), \
                mock.patch.object(sdwebui, "_txt2img", return_value={"images": []}), \
                mock.patch.object(bildsperre, "pruefen", side_effect=spion):
            with self.assertRaisesRegex(RuntimeError, "kein Bild"):
                sdwebui.generate("a fox")
        self.assertTrue(any(p.startswith("VORSATZ") and n and "GEGENTEIL" in n for p, n in gesehen))

    def test_png_text_utf8(self):
        import io
        import sdwebui
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, format="PNG")
        text = "Gedankenstrich – „Zitat“ ✓"
        roh = sdwebui._png_with_text(buf.getvalue(), {"prompt": text, "model": "äöü"})
        self.assertIn(b"iTXtprompt", roh)
        self.assertIn(b"tEXtmodel", roh)                          # Latin-1 bleibt tEXt
        with Image.open(io.BytesIO(roh)) as im:
            im.load()
            self.assertEqual(text, im.text["prompt"])
            self.assertEqual("äöü", im.text["model"])


class ComfyUI(unittest.TestCase):
    """B-09, B-11: Abbruch nimmt den Auftrag zurück; Config-Texte durch die Sperre."""

    def _client(self, posts):
        class Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, pfad, params=None):
                return _Antwort({})

            def post(self, pfad, json=None):
                posts.append((pfad, json))
                return _Antwort({"prompt_id": "abc"} if pfad == "/prompt" else {})
        return lambda timeout=None: Client()

    def _generate(self, posts, on_status, werte=None):
        import comfyui as C
        werte = werte or {}
        with mock.patch.object(C, "_client", self._client(posts)), \
                mock.patch.object(C, "available", return_value=True), \
                mock.patch.object(C, "resolve_model", return_value="m.safetensors"), \
                mock.patch.object(C, "model_art", return_value="checkpoint"), \
                mock.patch.object(C, "samplers", return_value=[]), \
                mock.patch.object(C, "schedulers", return_value=[]), \
                mock.patch.object(C, "_ccfg", lambda k, fb: werte.get(k, fb)):
            return C.generate("a fox", seed=1, on_status=on_status)

    def test_esc_nimmt_den_auftrag_zurueck(self):
        posts = []

        def melde(text):
            if "Ablaufplan" not in text:
                raise notstopp.Abgebrochen()
        with self.assertRaises(notstopp.Abgebrochen):
            self._generate(posts, melde)
        pfade = [p for p, _ in posts]
        self.assertIn(("/queue", {"delete": ["abc"]}), posts)
        self.assertIn("/interrupt", pfade)

    def test_esc_ohne_on_status(self):
        posts = []
        with esc(), self.assertRaises(notstopp.Abgebrochen):
            self._generate(posts, None)
        self.assertIn("/interrupt", [p for p, _ in posts])

    def test_config_texte_gehen_durch_die_sperre(self):
        import bildsperre
        gesehen = []
        echt = bildsperre.pruefen

        def spion(prompt, neg=None):
            gesehen.append((prompt, neg))
            return echt(prompt, neg)
        posts = []

        def melde(text):
            if "Ablaufplan" not in text:
                raise notstopp.Abgebrochen()
        with mock.patch.object(bildsperre, "pruefen", side_effect=spion), self.assertRaises(notstopp.Abgebrochen):
            self._generate(posts, melde, {"positiv": "VORSATZ", "neg": "GEGENTEIL"})
        self.assertTrue(any(p.startswith("VORSATZ") and n == "GEGENTEIL" for p, n in gesehen))


if __name__ == "__main__":
    unittest.main()
