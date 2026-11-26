"""Video- und Porträt-Motoren: Abbruch bis in die Motoren, Bildsperre an jeder Video-Tür, keine halben Dateien,
geprüfte Einstellungen, Schlüssel nur an den Anbieter, LoRA-Bestätigung mit Bildern und Ordnern.
Ohne Modelle und ohne Netz (Motoren und Anbieter nachgespielt)."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import notstopp            # noqa: E402
import video               # noqa: E402
import videobildcloud as VC  # noqa: E402
import videodatei as VD    # noqa: E402
import videomotoren as VM  # noqa: E402
from videomotoren import sdvideo  # noqa: E402


def _ordner(test: unittest.TestCase) -> Path:
    t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    test.addCleanup(t.cleanup)
    return Path(t.name)


def _quelle(*teile: str) -> str:
    return (ROOT.joinpath(*teile)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Bildsperre an den Video-Türen
# ---------------------------------------------------------------------------

class BildsperreVideo(unittest.TestCase):
    def test_fester_gegenprompt_an_jeder_video_tuer(self):
        self.assertIn("bildsperre.gegenprompt(NEGATIV if negativ is None else negativ)", _quelle("engines", "video.py"))
        self.assertIn('bildsperre.gegenprompt(einstellungen.get("negativ"))',
                      _quelle("engines", "videomotoren", "sdvideo.py"))
        self.assertIn('bildsperre.gegenprompt(einstellungen.get("negativ"))',
                      _quelle("engines", "videomotoren", "ltx2.py"))
        self.assertIn('bildsperre.pruefen(prompt, (extra or {}).get("negativ") or None)',
                      _quelle("engines", "videomotoren", "__init__.py"))

    def _motor(self, test, malen, bereit=True):
        """videomotoren.generate mit nachgespieltem Motor; gibt (Mocks) zurück."""
        import bildmotoren.gemeinsam as G
        import profilordner
        uhr = mock.Mock()
        schreiben = mock.Mock()
        modul = NS(TITEL="Probe", malen=malen)
        ziel = _ordner(test) / "v.mp4"
        p = [mock.patch.object(VM, "modelle", lambda: {"M": {}}),
             mock.patch.object(VM, "gewaehlt", lambda: "M"),
             mock.patch.object(VM, "missing_reason", lambda name=None: None),
             mock.patch.object(VM, "teile", lambda name=None: {"name": "M", "bauart": "probe"}),
             mock.patch.dict(VM.BAUARTEN, {"probe": modul}),
             mock.patch.object(VM, "plan", lambda *a, **k: {"breite": 64, "hoehe": 64, "fps": 8, "schritte": None}),
             mock.patch.object(VM, "_config", lambda: {}),
             mock.patch.object(VD, "bereit", lambda: bereit),
             mock.patch.object(VD, "schreiben", schreiben),
             mock.patch.object(profilordner, "video_pfad", lambda e=".mp4": ziel),
             mock.patch.object(G, "uhr_halt", lambda: None),
             mock.patch.object(G, "geraet", lambda: ("cpu", None)),
             mock.patch.object(G, "aufraeumen", lambda: None),
             mock.patch.object(G, "uhr_neu", uhr)]
        for x in p:
            x.start()
            test.addCleanup(x.stop)
        return uhr, schreiben

    def test_gegenprompt_wird_geprueft_und_weitergegeben(self):
        gesehen = {}

        def malen(prompt, t, **kw):
            gesehen.update(prompt=prompt, neg=kw["einstellungen"].get("negativ"), abbruch=kw["abbruch"])
            return np.zeros((8, 64, 64, 3), np.uint8), None, 0
        self._motor(self, malen)
        halt = lambda: False  # noqa: E731
        VM.generate("a girl walking", sekunden=1, extra={"negativ": "adult, blurry"}, abbruch=halt)
        self.assertEqual(gesehen, {"prompt": "a woman walking", "neg": "blurry", "abbruch": halt})

    def test_ohne_mp4_weg_wird_nicht_gerechnet(self):
        malen = mock.Mock(side_effect=AssertionError("zu spät"))
        self._motor(self, malen, bereit=False)
        with self.assertRaisesRegex(RuntimeError, "Media Foundation"):
            VM.generate("a ball", sekunden=1)
        malen.assert_not_called()

    def test_abbruch_stellt_die_entlade_uhr_trotzdem(self):
        def malen(prompt, t, **kw):
            raise notstopp.Abgebrochen()
        uhr, schreiben = self._motor(self, malen)
        with self.assertRaises(notstopp.Abgebrochen):
            VM.generate("a ball", sekunden=1)
        uhr.assert_called_once()
        schreiben.assert_not_called()

    def test_schritte_begrenzt(self):
        gesehen = {}

        def malen(prompt, t, **kw):
            gesehen["schritte"] = kw["einstellungen"]["schritte"]
            return np.zeros((8, 64, 64, 3), np.uint8), None, 0
        self._motor(self, malen)
        VM.generate("a ball", sekunden=1, steps=5000)
        self.assertEqual(gesehen["schritte"], VM.GRENZEN["schritte"][1])


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

class Einstellungen(unittest.TestCase):
    def _cfg(self, werte):
        import config
        return mock.patch.object(config, "load", lambda: werte)

    def test_wan_bool_aus_text(self):
        for wert, soll in (("false", False), ("aus", False), ("0", False), ("true", True), (True, True), (False, False)):
            with self.subTest(wert=wert), self._cfg({"video_trt": wert}):
                self.assertIs(video._cfg("video_trt", True), soll)

    def test_wan_groesse_abgesichert(self):
        self.assertEqual(video._groesse_cfg("704x1024"), (704, 1024))
        self.assertEqual(video._groesse_cfg(" 832 × 480 "), (832, 480))
        for falsch in ("704x", "x", "", None, "groß", "1x2"):
            with self.subTest(falsch=falsch):
                self.assertIsNone(video._groesse_cfg(falsch))

    def test_motor_einstellung_gegen_erlaubt(self):
        with mock.patch.object(VM, "_config", lambda: {"video_motor_fps": 31, "video_motor_kacheln": "riesig",
                                                       "video_motor_schritte": 500, "video_motor_cfg": -3,
                                                       "video_motor_entladen": "bald"}):
            self.assertEqual(VM.einstellung("fps"), 24)
            self.assertEqual(VM.einstellung("kacheln"), "auto")
            self.assertEqual(VM.einstellung("schritte"), 0)
            self.assertEqual(VM.einstellung("cfg"), 0.0)
            self.assertEqual(VM._entladen_nach(), 300.0)
        with mock.patch.object(VM, "_config", lambda: {"video_motor_schritte": "12", "video_motor_cfg": 4.5}):
            self.assertEqual((VM.einstellung("schritte"), VM.einstellung("cfg")), (12, 4.5))

    def test_setzen_mit_grenzen(self):
        import config
        with mock.patch.object(config, "update", mock.Mock()) as upd:
            for k, w in (("schritte", 101), ("schritte", -1), ("cfg", 20.5)):
                with self.subTest(k=k, w=w), self.assertRaises(ValueError):
                    VM.setzen(k, w)
            upd.assert_not_called()
            self.assertEqual(VM.setzen("schritte", "40"), 40)
            self.assertEqual(VM.setzen("cfg", 7), 7.0)


# ---------------------------------------------------------------------------
# SD-Video: Rauschplan, Prüf-Merker, Umschreiben
# ---------------------------------------------------------------------------

class SdVideo(unittest.TestCase):
    def test_rauschplan_genau_so_viele_schritte(self):
        for schritte in (1, 2, 3, 4, 8, 25, 30, 61, 100, 103, 121, 122):
            with self.subTest(schritte=schritte):
                zeit, sig = sdvideo.rauschplan(schritte)
                self.assertEqual(len(zeit), schritte)
                self.assertEqual(len(sig), schritte + 1)
                self.assertEqual(int(zeit[0]), 999)
                self.assertTrue(all(int(a) > int(b) for a, b in zip(zeit, zeit[1:])))
                self.assertGreaterEqual(int(zeit[-1]), 0)
        self.assertEqual(sdvideo.rauschplan(4)[0].tolist(), [999, 749, 499, 249])

    def test_merker_bemerkt_ersetzte_datei(self):
        d = _ordner(self)
        pfad = d / "probe_bewegung.safetensors"
        echt = b"a" * 4096
        pfad.write_bytes(echt)
        soll = (len(echt), hashlib.sha256(echt).hexdigest(), "sd15")
        with mock.patch.dict(sdvideo.BEKANNT, {pfad.name: soll}):
            sdvideo.pruefen(pfad)
            eintrag = __import__("json").loads((d / sdvideo.GEPRUEFT).read_text(encoding="utf-8"))[pfad.name]
            self.assertEqual(len(eintrag), 4)
            sdvideo.pruefen(pfad)                                     # Merker passt: ohne volle Prüfung
            st = pfad.stat()
            pfad.write_bytes(b"b" * 4096)                             # gleiche Größe, gleicher Zeitstempel
            os.utime(pfad, ns=(st.st_atime_ns, st.st_mtime_ns))
            with self.assertRaisesRegex(RuntimeError, "SHA-256 stimmt nicht"):
                sdvideo.pruefen(pfad)

    def test_umschreiben_atomar(self):
        import safetensors.torch as ST
        import torch
        d = _ordner(self)
        ckpt = d / "probe.ckpt"
        ckpt.write_bytes(b"x")

        def halb(sd, pfad, metadata=None):
            Path(pfad).write_bytes(b"halb")
            raise OSError("Platte voll")
        with mock.patch.dict(sdvideo.BEKANNT, {ckpt.name: (1, "0" * 64, "sd15")}), \
                mock.patch.object(sdvideo, "pruefen", lambda p, s=None: None), \
                mock.patch.object(torch, "load", lambda *a, **k: {"down.motion.weight": torch.zeros(2)}), \
                mock.patch.object(sdvideo.sd_netz, "bewegung_name", lambda k: k), \
                mock.patch.object(ST, "save_file", halb), self.assertRaises(OSError):
            sdvideo._umschreiben(ckpt)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["probe.ckpt"])


# ---------------------------------------------------------------------------
# Wan: Abbruch, TensorRT, Aufräumen
# ---------------------------------------------------------------------------

class Wan(unittest.TestCase):
    def test_abbruch_beim_auspacken(self):
        import torch
        tae = object.__new__(video.TaeW22)
        tae.dtype, tae.plan = torch.float32, []
        with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
            tae.decode(torch.zeros(48, 2, 1, 1), abbruch=lambda: True)
        vae = object.__new__(video.WanVAE22)
        vae.decode = mock.Mock(return_value="aus")
        halt = lambda: False  # noqa: E731
        self.assertEqual(vae.decode_kacheln(torch.zeros(48, 2, 4, 4), None, 32, abbruch=halt), "aus")
        self.assertIs(vae.decode.call_args.kwargs["abbruch"], halt)

    def _clip(self, **kw):
        import torch
        fam = video.FAMILIEN["wan22-ti2v"]
        return video._clip("p", "n", None, {"name": "M", "dit": Path("m.gguf"), "vae": Path("v")}, 4, 1.0, 5.0,
                           64, 64, 1, "cpu", torch.float32, None, kw.pop("abbruch", None),
                           {"ctx": torch.zeros(1, 4), "neg": None}, fam, **kw)

    def test_trt_wird_auch_nach_fehler_aufgeraeumt(self):
        nach = mock.Mock()
        tv = NS(bloecke=lambda d: None, einmessen=lambda *a: None)
        with mock.patch.object(video, "dit_config", lambda t: {}), mock.patch.object(video, "_trt", lambda: tv), \
                mock.patch.object(video, "_auf_gpu", mock.Mock(side_effect=RuntimeError("kaputt"))), \
                mock.patch.object(video, "_trt_nach_video", nach), mock.patch.dict(video._RAM, clear=True), \
                self.assertRaisesRegex(RuntimeError, "kaputt"):
            self._clip()
        nach.assert_called_once()
        self.assertFalse(nach.call_args.kwargs["bauen"])

    def test_abbruch_vor_dem_modell(self):
        laden = mock.Mock()
        with mock.patch.object(video, "dit_config", lambda t: {}), mock.patch.object(video, "_auf_gpu", laden), \
                self.assertRaisesRegex(RuntimeError, "abgebrochen"):
            self._clip(abbruch=lambda: True)
        laden.assert_not_called()

    def test_trt_fehler_einmal_ohne_trt_wiederholt(self):
        import profilordner

        class TrtFehler(RuntimeError):
            pass
        aufrufe = []

        def clip(*a, trt=True, **kw):
            aufrufe.append(trt)
            if trt:
                raise TrtFehler("Engine kaputt")
            return np.zeros((121, 8, 8, 3), np.uint8)
        ausgeben = mock.Mock()
        ziel = _ordner(self) / "v.mp4"
        with mock.patch.object(video, "motor_aktiv", lambda m=None: False), \
                mock.patch.object(video, "missing_reason", lambda m=None: None), \
                mock.patch.object(video, "_mp4_weg", lambda: "windows"), \
                mock.patch.object(video, "teile", lambda m=None: {"name": "M", "dit": Path("m.gguf")}), \
                mock.patch.object(video, "dit_config", lambda t: {}), \
                mock.patch.object(video, "familie", lambda c: "wan22-ti2v"), \
                mock.patch.object(video, "_lora_fuer", lambda l: None), \
                mock.patch.object(video, "_cfg", lambda k, s: s), \
                mock.patch.object(video, "_trt_fehler", lambda: (TrtFehler,)), \
                mock.patch.object(video, "_clip", clip), mock.patch.object(video, "_frei", lambda: None), \
                mock.patch.object(video, "_uhr_neu", lambda: None), mock.patch.object(video, "ausgeben", ausgeben), \
                mock.patch.object(profilordner, "video_pfad", lambda e=".mp4": ziel):
            self.assertEqual(video.generate("a fox", sekunden=5), str(ziel))
        self.assertEqual(aufrufe, [True, False])
        ausgeben.assert_called_once()

    def test_trt_fehler_ohne_trtmotor(self):
        with mock.patch.dict(sys.modules, {"trtmotor": None}):
            self.assertEqual(video._trt_fehler(), ())


# ---------------------------------------------------------------------------
# MP4 ohne halbe Dateien
# ---------------------------------------------------------------------------

@unittest.skipUnless(VD.bereit(), "Windows Media Foundation fehlt")
class Mp4(unittest.TestCase):
    def test_abbruch_laesst_keine_datei_liegen(self):
        d = _ordner(self)

        def folge():
            for _ in range(3):
                yield np.zeros((64, 48, 3), np.uint8)
            raise RuntimeError("abgebrochen")
        with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
            VD.schreiben(folge(), d / "v.mp4", 24)
        self.assertEqual(list(d.iterdir()), [])

    def test_fertig_unter_dem_zielnamen(self):
        d = _ordner(self)
        VD.schreiben(np.zeros((4, 64, 48, 3), np.uint8), d / "v.mp4", 24)
        self.assertEqual([p.name for p in d.iterdir()], ["v.mp4"])
        self.assertEqual((d / "v.mp4").read_bytes()[4:8], b"ftyp")


class Mp4Start(unittest.TestCase):
    def test_mfstartup_fehler_meldet_com_ab(self):
        ole = NS(CoInitializeEx=lambda *a: 0, CoUninitialize=mock.Mock())
        plat = NS(MFStartup=lambda *a: -1)
        with mock.patch.object(VD, "_dlls", lambda: (plat, NS())), \
                mock.patch.object(VD.ctypes, "WinDLL", lambda name: ole), self.assertRaises(OSError):
            VD.schreiben(np.zeros((1, 4, 4, 3), np.uint8), _ordner(self) / "v.mp4", 24)
        ole.CoUninitialize.assert_called_once()


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------

class _Antwort:
    def __init__(self, inhalt=b"", status=200, daten=None, fehler=None):
        self.content, self.status_code, self._daten, self._fehler = inhalt, status, daten, fehler
        self.headers, self.is_redirect, self.text = {}, False, ""

    def json(self):
        if self._fehler:
            raise self._fehler
        return self._daten

    def iter_bytes(self):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Netz:
    def __init__(self, *antworten):
        self.antworten, self.anfragen = list(antworten), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, art, url, **kw):
        self.anfragen.append((url, kw))
        return self.antworten.pop(0)

    def get(self, url, **kw):
        self.anfragen.append((url, kw))
        return self.antworten.pop(0)


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"y" * 20


class Cloud(unittest.TestCase):
    def _netz(self, *antworten):
        n = _Netz(*antworten)
        p = mock.patch.object(VC, "_client", lambda zeit: n)
        p.start()
        self.addCleanup(p.stop)
        return n

    def test_schluessel_nur_an_den_anbieter(self):
        kopf = {"Authorization": "Bearer geheim"}
        for url, mit in (("https://replicate.delivery/o.png", True), ("https://api.replicate.com/v1/f", True),
                         ("https://fremd.example/o.png", False), ("https://replicate.com.fremd.example/o.png", False)):
            with self.subTest(url=url):
                n = self._netz(_Antwort(PNG))
                VC._laden(url, kopf, domains=VC.SCHLUESSEL_HOSTS["replicate"])
                self.assertEqual(bool(n.anfragen[0][1]["headers"]), mit)

    def test_video_nur_mit_video_kopf(self):
        self._netz(_Antwort(PNG))
        with self.assertRaisesRegex(VC.CloudFehler, "kein Video"):
            VC._laden("https://cdn.example/v.mp4", art="video")
        self._netz(_Antwort(MP4))
        self.assertEqual(VC._laden("https://cdn.example/v.mp4", art="video"), MP4)
        self._netz(_Antwort(b"RIFF\x00\x00\x00\x00WAVEfmt "))
        with self.assertRaisesRegex(VC.CloudFehler, "kein Bild"):
            VC._laden("https://cdn.example/b.png")

    def test_endungen(self):
        self.assertEqual(VC._bild_endung(b"GIF89a" + b"x" * 10), ".gif")
        self.assertEqual(VC._video_endung(b"\x1aE\xdf\xa3" + b"x" * 10), ".webm")
        self.assertEqual(VC._video_endung(MP4), ".mp4")

    def test_antwort_ohne_json(self):
        with self.assertRaisesRegex(VC.CloudFehler, "kein JSON"):
            VC._pruefen(_Antwort(fehler=ValueError("Expecting value")), "Probe")

    def test_abbruch_beim_warten(self):
        stand = iter([False, False, True])
        n = self._netz(_Antwort(daten={"status": "läuft"}))
        with mock.patch.object(VC.time, "sleep", lambda s: None), \
                self.assertRaisesRegex(VC.CloudFehler, "Abgebrochen"):
            VC._warten(n, "https://api.example/t", {}, "Probe", lambda j: None, zeit=60, warten=5.0,
                       abbruch=lambda: next(stand))
        self.assertEqual(len(n.anfragen), 1)


# ---------------------------------------------------------------------------
# LivePortrait
# ---------------------------------------------------------------------------

class Portraet(unittest.TestCase):
    def test_none_nur_fuer_augen(self):
        from liveportrait import motor
        werte = motor.verlauf([{"t": 0, "laecheln": 0.5}, {"t": 0.1, "laecheln": None, "augen": None}],
                              0.2, 10, augen_quelle=0.3)
        self.assertEqual(werte[-1]["laecheln"], 0.5)
        self.assertEqual(werte[-1]["augen"], 0.3)

    def test_abbruch_vor_dem_laden(self):
        from liveportrait import motor
        with mock.patch.object(motor, "fehlt", lambda: None), \
                mock.patch.object(motor, "motor", mock.Mock(side_effect=AssertionError("zu spät"))), \
                self.assertRaisesRegex(RuntimeError, "abgebrochen"):
            motor.animieren("x.png", "blinzeln", sekunden=1, geraet="cpu", abbruch=lambda: True)

    def test_gewichte_neben_der_pth(self):
        from liveportrait import gewichte
        d = _ordner(self)
        for teil in ("a", "b"):
            (d / teil).mkdir()
        (d / "a" / "warping_module.safetensors").write_bytes(b"alt")
        (d / "b" / "warping_module.pth").write_bytes(b"x")
        (d / "b" / "warping_module.safetensors").write_bytes(b"neu")
        self.assertEqual(gewichte._safetensors_pfad(d, "warping_module"), d / "b" / "warping_module.safetensors")
        self.assertEqual(gewichte._safetensors_pfad(d / "a", "warping_module"), d / "a" / "warping_module.safetensors")

    def test_umwandeln_atomar(self):
        import safetensors.torch as ST
        from liveportrait import gewichte
        d = _ordner(self)
        (d / "warping_module.pth").write_bytes(b"x")

        def halb(sd, pfad, metadata=None):
            Path(pfad).write_bytes(b"halb")
            raise OSError("Platte voll")
        with mock.patch.object(gewichte, "_pth_lesen", lambda p, t: {}), mock.patch.object(ST, "save_file", halb), \
                self.assertRaises(OSError):
            gewichte.umwandeln(d, pruefen=False)
        self.assertEqual([p.name for p in d.iterdir()], ["warping_module.pth"])


# ---------------------------------------------------------------------------
# Bewegung (SprongMotion)
# ---------------------------------------------------------------------------

class Bewegung(unittest.TestCase):
    def test_unbrauchbares_modell_wird_entladen(self):
        import bewegung as BW
        for gueltig in (False, True):                       # ungültig · gültig, aber falsche Gelenkzahl
            with self.subTest(gueltig=gueltig):
                lib = mock.MagicMock()
                lib.sm_is_model_valid.return_value = gueltig
                lib.sm_get_error.return_value = b"kaputt"
                with mock.patch.object(BW, "_datei_stimmt", lambda *a: True), \
                        mock.patch.object(BW.C, "CDLL", lambda pfad, lib=lib: lib), self.assertRaises(RuntimeError):
                    BW.Motor()._laden()
                lib.sm_unload_model.assert_called_once()

    def test_ungueltige_bewegung_wird_freigegeben(self):
        import bewegung as BW
        m = BW.Motor()
        m._lib, m._modell = mock.MagicMock(), object()
        m._lib.sm_is_motion_valid.return_value = False
        m._lib.sm_get_error.return_value = b"nichts"
        with self.assertRaisesRegex(RuntimeError, "nichts"):
            m.rechnen("wave")
        m._lib.sm_unload_motion.assert_called_once()


# ---------------------------------------------------------------------------
# Werkzeuge (tools/actions.py)
# ---------------------------------------------------------------------------

class Werkzeuge(unittest.TestCase):
    def setUp(self):
        import actions
        self.A = actions

    def test_cloud_video_bekommt_den_abbruch(self):
        gesehen = {}

        def erzeugen(prompt, **kw):
            gesehen.update(kw)
            return "C:/videos/v.mp4"
        with mock.patch.object(VC, "video_generate", erzeugen), mock.patch.object(VC, "label", lambda art="bild": "☁"), \
                mock.patch.object(self.A.os, "startfile", lambda p: None):
            self.A._video_cloud({}, "waves")
        self.assertIs(gesehen["abbruch"], self.A._esc)

    def test_cloud_abbruch_als_esc(self):
        with mock.patch.object(VC, "video_generate", mock.Mock(side_effect=VC.CloudFehler("Abgebrochen."))), \
                mock.patch.object(self.A, "_esc", lambda: True), self.assertRaises(notstopp.Abgebrochen):
            self.A._video_cloud({}, "waves")

    def test_portraet_entlaedt_erst_mit_der_sperre(self):
        import imagegen
        from liveportrait import motor as LP
        d = _ordner(self)
        bild = d / "ich.png"
        bild.write_bytes(b"x")
        ablauf = []

        def gpu(fn):
            def huelle(*a, **k):
                ablauf.append("sperre")
                return fn(*a, **k)
            return huelle

        def animieren(*a, **kw):
            ablauf.append(("animieren", kw.get("abbruch")))
            return {"pfad": str(d / "v.mp4"), "vorschau": "", "kugel": "k.webp", "bilder": 25,
                    "sekunden_rechnen": 1.0, "geraet": "cpu"}
        entladen = {n: mock.Mock(side_effect=lambda n=n: ablauf.append(f"entladen:{n}"))
                    for n in ("krea", "video", "videomotoren")}
        module = {n: NS(entladen=f) for n, f in entladen.items()}
        with mock.patch.object(LP, "fehlt", lambda: None), mock.patch.object(LP, "animieren", animieren), \
                mock.patch.object(LP, "entladen", lambda: None), mock.patch.object(imagegen, "_gpu_fuer_bild", gpu), \
                mock.patch.dict(sys.modules, module), mock.patch.object(self.A.os, "startfile", lambda p: None):
            text = self.A._portraet_bewegen({"bild": str(bild), "zeigen": "player"})
        self.assertIn("Porträt", text)
        self.assertEqual(ablauf[0], "sperre")
        self.assertEqual(set(ablauf[1:4]), {"entladen:krea", "entladen:video", "entladen:videomotoren"})
        self.assertEqual(ablauf[4], ("animieren", self.A._esc))

    def test_lora_bestaetigung_nennt_bilder_und_ordner(self):
        import profilordner
        d = _ordner(self)
        profil = d / "Profile" / "P" / "Bilder"
        fremd = d / "Woanders"
        with mock.patch.object(profilordner, "ordner", lambda unter="", name=None, anlegen=True: profil):
            text = self.A.describe({"tool": "lora_trainieren", "bilder": [str(profil / "a.png"), str(profil / "b.png"),
                                                                        str(fremd / "c.png")]})
            ohne = self.A.describe({"tool": "lora_trainieren"})
        self.assertIn("3 Bilder aus 2 Ordnern", text)
        self.assertEqual(text.count("außerhalb des Profils"), 2)            # Kopfzeile und der fremde Ordner
        self.assertIn(str(fremd.resolve()), text)
        zeile_profil = next(z for z in text.splitlines() if str(profil.resolve()) in z)
        self.assertNotIn("⚠", zeile_profil)
        self.assertIn("Vorschlag", ohne)


if __name__ == "__main__":
    unittest.main()
