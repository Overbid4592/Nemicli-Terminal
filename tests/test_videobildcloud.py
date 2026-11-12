"""Bilder und Videos aus der Cloud (engines/videobildcloud.py): Anfragen und Antworten wie in den Dokus der
Anbieter (OpenAI, Google Interactions, xAI), Wahl über die Config, Anschluss an imagegen und video_erstellen.
Gespielte Anbieter – ohne Netz, ohne echte Schlüssel."""
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions                                         # noqa: E402  (vor dem Nachspielen der Config)
import imagegen                                        # noqa: E402
import videobildcloud as VC                            # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"y" * 20


class _Antwort:
    def __init__(self, status=200, daten=None, inhalt=b""):
        self.status_code, self._daten, self.content, self.text = status, daten, inhalt, str(daten)

    def json(self):
        return self._daten

    is_redirect = False
    headers: dict = {}

    def iter_bytes(self):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Netz:
    """Wie httpx.Client: Antworten der Reihe nach, merkt sich die Anfragen."""
    def __init__(self, *antworten):
        self.antworten, self.anfragen = list(antworten), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _weiter(self, art, url, **kw):
        self.anfragen.append((art, url, kw))
        return self.antworten.pop(0)

    def post(self, url, **kw):
        return self._weiter("POST", url, **kw)

    def get(self, url, **kw):
        return self._weiter("GET", url, **kw)

    def stream(self, art, url, **kw):
        return self._weiter(art, url, **kw)


class Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ordner = Path(self._tmp.name)
        self.cfg = {}
        self.schluessel = {"openai": "sk-test", "google": "g-test", "xai": "x-test"}
        profil = NS(bild_pfad=lambda e=".png": self.ordner / f"bild{e}", video_pfad=lambda e=".mp4": self.ordner / f"film{e}")
        self._p = [mock.patch.object(VC, "_schluessel", lambda a, t: self.schluessel.get(t[a].get("provider") or a)),
                   mock.patch.object(VC, "_config", lambda: self.cfg),
                   mock.patch.dict(sys.modules, {"profilordner": profil,
                                                 "config": NS(load=lambda: self.cfg, update=self.cfg.update)}),
                   mock.patch.object(VC.time, "sleep", lambda s: None)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self._tmp.cleanup()

    def netz(self, *antworten):
        n = _Netz(*antworten)
        p = mock.patch.object(VC, "_client", lambda zeit: n)
        p.start()
        self.addCleanup(p.stop)
        return n


class WahlTests(Basis):
    def test_nur_mit_schluessel(self):
        self.schluessel = {"google": "g"}
        self.assertEqual(["google"], VC.bereit("bild"))
        self.assertEqual("google", VC.gewaehlt("bild"))
        with self.assertRaises(ValueError):
            VC.waehlen("bild", "openai")                                     # kein Schlüssel
        with self.assertRaises(ValueError):
            VC.waehlen("video", "openai")                                    # OpenAI-Videos gibt es nicht mehr
        self.assertNotIn("openai", VC.VIDEO)

    def test_waehlen_und_zurueck(self):
        VC.waehlen("bild", "xai")
        self.assertEqual(("cloud", "xai"), (self.cfg["bild_backend"], self.cfg["cloud_bild_anbieter"]))
        self.assertEqual("xai", VC.gewaehlt("bild"))
        VC.waehlen("video", "google")
        self.assertTrue(VC.video_aktiv())
        VC.waehlen("video", None)
        self.assertFalse(VC.video_aktiv())
        VC.waehlen("bild", None)
        self.assertEqual("krea", self.cfg["bild_backend"])

    def test_eigenes_modell_aus_der_config(self):
        self.cfg["cloud_bild_modell"] = {"openai": "gpt-image-neu"}
        self.assertEqual("gpt-image-neu", VC.modell("openai"))
        self.assertEqual(VC.BILD["xai"]["modell"], VC.modell("xai"))


class BildTests(Basis):
    def test_openai(self):
        n = self.netz(_Antwort(daten={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}))
        pfad = VC.generate("a fox", size=(832, 1216), anbieter="openai", neg="blur")
        self.assertEqual(PNG, Path(pfad).read_bytes())
        art, url, kw = n.anfragen[0]
        self.assertEqual("https://api.openai.com/v1/images/generations", url)
        self.assertEqual("Bearer sk-test", kw["headers"]["Authorization"])
        self.assertEqual("gpt-image-2.5-flare", kw["json"]["model"])
        w, h = map(int, kw["json"]["size"].split("x"))
        self.assertEqual((0, 0), (w % 16, h % 16))
        self.assertIn("Avoid: blur", kw["json"]["prompt"])

    def test_google_interactions(self):
        antwort = {"steps": [{"type": "thought", "content": []},
                             {"type": "model_output", "content": [
                                 {"type": "text", "text": "hier"},
                                 {"type": "image", "mime_type": "image/png", "data": base64.b64encode(PNG).decode()}]}]}
        n = self.netz(_Antwort(daten=antwort))
        self.assertEqual(PNG, Path(VC.generate("a fox", size=(1216, 832), anbieter="google")).read_bytes())
        _, url, kw = n.anfragen[0]
        self.assertTrue(url.endswith("/v1beta/interactions"))
        self.assertEqual("g-test", kw["headers"]["x-goog-api-key"])
        self.assertEqual("3:2", kw["json"]["response_format"]["aspect_ratio"])
        self.assertEqual("gemini-nano-banana-2.1", kw["json"]["model"])

    def test_xai_mit_adresse(self):
        n = self.netz(_Antwort(daten={"data": [{"url": "https://bild.example/a.jpg"}]}),
                      _Antwort(inhalt=b"\xff\xd8\xff" + b"z" * 10))
        pfad = VC.generate("a fox", anbieter="xai")
        self.assertTrue(pfad.endswith(".jpg"))
        self.assertEqual("https://bild.example/a.jpg", n.anfragen[1][1])

    def test_fehler_des_anbieters_ohne_schluessel(self):
        self.netz(_Antwort(400, {"error": {"message": "Your request was rejected by the safety system."}}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.generate("x", anbieter="openai")
        self.assertIn("safety system", str(f.exception))
        self.assertIn("400", str(f.exception))
        self.assertNotIn("sk-test", str(f.exception))

    def test_kein_bild_geliefert(self):
        self.netz(_Antwort(daten={"steps": []}))
        with self.assertRaises(VC.CloudFehler):
            VC.generate("x", anbieter="google")

    def test_ohne_anbieter(self):
        self.schluessel = {}
        with self.assertRaises(VC.CloudFehler):
            VC.generate("x")


class VideoTests(Basis):
    def test_google_mit_startbild(self):
        start = self.ordner / "start.png"
        start.write_bytes(PNG)
        antwort = {"steps": [{"type": "model_output", "content": [
            {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(MP4).decode()}]}]}
        n = self.netz(_Antwort(daten=antwort))
        pfad = VC.video_generate("waves", startbild=str(start), hochkant=True, anbieter="google")
        self.assertEqual(MP4, Path(pfad).read_bytes())
        eingabe = n.anfragen[0][2]["json"]["input"]
        self.assertEqual(("image", "image/png"), (eingabe[0]["type"], eingabe[0]["mime_type"]))
        self.assertEqual("waves", eingabe[1]["text"])
        self.assertEqual("9:16", n.anfragen[0][2]["json"]["response_format"]["aspect_ratio"])

    def test_xai_wartet_bis_fertig(self):
        n = self.netz(_Antwort(daten={"request_id": "r1"}), _Antwort(daten={"status": "pending"}),
                      _Antwort(daten={"status": "done", "video": {"url": "https://vidgen.example/v.mp4"}}),
                      _Antwort(inhalt=MP4))
        meldungen = []
        pfad = VC.video_generate("rocket", sekunden=40, anbieter="xai", on_status=meldungen.append)
        self.assertEqual(MP4, Path(pfad).read_bytes())
        koerper = n.anfragen[0][2]["json"]
        self.assertEqual((15, "grok-imagine-video-1.5", "16:9"), (koerper["duration"], koerper["model"],
                                                                 koerper["aspect_ratio"]))
        self.assertEqual("https://api.x.ai/v1/videos/r1", n.anfragen[1][1])
        self.assertTrue(any("wartet" in m for m in meldungen))

    def test_xai_fehlgeschlagen(self):
        self.netz(_Antwort(daten={"request_id": "r1"}),
                  _Antwort(daten={"status": "failed", "error": {"code": "permission_denied", "message": "nicht erlaubt"}}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.video_generate("x", anbieter="xai")
        self.assertIn("nicht erlaubt", str(f.exception))

    def test_xai_fehlgeschlagen_fehler_als_text(self):
        self.netz(_Antwort(daten={"request_id": "r1"}),
                  _Antwort(daten={"status": "failed", "error": "nicht erlaubt"}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.video_generate("x", anbieter="xai")
        self.assertIn("nicht erlaubt", str(f.exception))


class NeueAnbieterTests(Basis):
    """Black Forest Labs, Ideogram, Runway und fal.ai – Anfragen wie in den offiziellen Anleitungen."""

    def setUp(self):
        super().setUp()
        self.schluessel.update(bfl="b-test", ideogram="i-test", runway="r-test", fal="f-test", stability="s-test",
                               recraft="rc-test", together="t-test", replicate="rp-test", minimax="m-test",
                               luma="l-test")

    def test_bfl_fragt_polling_url_ab(self):
        n = self.netz(_Antwort(daten={"id": "1", "polling_url": "https://api.bfl.ai/v1/get_result?id=1"}),
                      _Antwort(daten={"status": "Pending"}),
                      _Antwort(daten={"status": "Ready", "result": {"sample": "https://delivery.example/b.png"}}),
                      _Antwort(inhalt=PNG))
        pfad = VC.generate("fox", size=(832, 1216), anbieter="bfl")
        self.assertEqual(PNG, Path(pfad).read_bytes())
        art, url, kw = n.anfragen[0]
        self.assertEqual(("POST", "https://api.bfl.ai/v1/flux-2-pro"), (art, url))
        self.assertEqual("b-test", kw["headers"]["x-key"])
        self.assertEqual((832, 1216), (kw["json"]["width"], kw["json"]["height"]))
        self.assertEqual("https://api.bfl.ai/v1/get_result?id=1", n.anfragen[1][1])     # nie selbst gebaut
        self.assertNotIn("x-key", n.anfragen[3][2].get("headers") or {})               # Schlüssel nicht zum Download

    def test_bfl_moderiert(self):
        self.netz(_Antwort(daten={"polling_url": "https://api.bfl.ai/v1/get_result?id=1"}),
                  _Antwort(daten={"status": "Content Moderated"}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.generate("x", anbieter="bfl")
        self.assertIn("Moderated", str(f.exception))

    def test_ideogram(self):
        n = self.netz(_Antwort(daten={"data": [{"url": "https://ideogram.example/i.png"}]}), _Antwort(inhalt=PNG))
        VC.generate("poster", anbieter="ideogram")
        art, url, kw = n.anfragen[0]
        self.assertEqual("https://api.ideogram.ai/v1/ideogram-v4/generate", url)
        self.assertEqual(({"Api-Key": "i-test"}, {"text_prompt": "poster"}), (kw["headers"], kw["json"]))

    def test_runway_bild_und_aufgabe(self):
        n = self.netz(_Antwort(daten={"id": "t1"}), _Antwort(daten={"status": "RUNNING"}),
                      _Antwort(daten={"status": "SUCCEEDED", "output": ["https://runway.example/o.png"]}),
                      _Antwort(inhalt=PNG))
        VC.generate("berg", size=(1920, 1080), anbieter="runway")
        kw = n.anfragen[0][2]
        self.assertEqual("https://api.dev.runwayml.com/v1/text_to_image", n.anfragen[0][1])
        self.assertEqual("2024-11-06", kw["headers"]["X-Runway-Version"])
        self.assertEqual({"model": "gen4_image", "promptText": "berg", "ratio": "1920:1080"}, kw["json"])
        self.assertEqual("https://api.dev.runwayml.com/v1/tasks/t1", n.anfragen[1][1])

    def test_runway_video_mit_startbild(self):
        bild = self.ordner / "start.png"
        bild.write_bytes(PNG)
        n = self.netz(_Antwort(daten={"id": "t2"}),
                      _Antwort(daten={"status": "SUCCEEDED", "output": ["https://runway.example/v.mp4"]}),
                      _Antwort(inhalt=MP4))
        pfad = VC.video_generate("welle", sekunden=12, startbild=str(bild), hochkant=True, anbieter="runway")
        self.assertEqual(MP4, Path(pfad).read_bytes())
        koerper = n.anfragen[0][2]["json"]
        self.assertEqual("https://api.dev.runwayml.com/v1/image_to_video", n.anfragen[0][1])
        self.assertEqual((10, "720:1280", "gen4.5"), (koerper["duration"], koerper["ratio"], koerper["model"]))
        self.assertTrue(koerper["promptImage"].startswith("data:image/png;base64,"))

    def test_runway_fehler(self):
        self.netz(_Antwort(daten={"id": "t3"}), _Antwort(daten={"status": "FAILED", "failure": "Inhalt abgelehnt"}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.video_generate("x", anbieter="runway")
        self.assertIn("Inhalt abgelehnt", str(f.exception))

    def test_fal_warteschlange(self):
        einreichen = {"request_id": "q", "status_url": "https://queue.fal.run/fal-ai/flux/requests/q/status",
                      "response_url": "https://queue.fal.run/fal-ai/flux/requests/q"}
        n = self.netz(_Antwort(daten=einreichen), _Antwort(daten={"status": "IN_QUEUE"}),
                      _Antwort(daten={"status": "COMPLETED"}),
                      _Antwort(daten={"images": [{"url": "https://fal.media/x.png"}]}), _Antwort(inhalt=PNG))
        VC.generate("katze", size=(1024, 1024), anbieter="fal")
        art, url, kw = n.anfragen[0]
        self.assertEqual("https://queue.fal.run/fal-ai/flux/schnell", url)
        self.assertEqual("Key f-test", kw["headers"]["Authorization"])
        self.assertEqual({"width": 1024, "height": 1024}, kw["json"]["image_size"])
        self.assertEqual(einreichen["response_url"], n.anfragen[3][1])

    def test_fal_video_mit_startbild_nimmt_image_to_video(self):
        bild = self.ordner / "start.png"
        bild.write_bytes(PNG)
        einreichen = {"status_url": "https://queue.fal.run/s", "response_url": "https://queue.fal.run/r"}
        n = self.netz(_Antwort(daten=einreichen), _Antwort(daten={"status": "COMPLETED"}),
                      _Antwort(daten={"video": {"url": "https://fal.media/v.mp4"}}), _Antwort(inhalt=MP4))
        VC.video_generate("tanz", startbild=str(bild), anbieter="fal")
        self.assertEqual("https://queue.fal.run/fal-ai/veo3.1/image-to-video", n.anfragen[0][1])
        self.assertIn("image_url", n.anfragen[0][2]["json"])

    def test_fal_fehler_im_ergebnis(self):
        einreichen = {"status_url": "https://queue.fal.run/s", "response_url": "https://queue.fal.run/r"}
        self.netz(_Antwort(daten=einreichen), _Antwort(daten={"status": "COMPLETED"}),
                  _Antwort(daten={"error": "Modell nicht gefunden", "error_type": "x"}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.generate("x", anbieter="fal")
        self.assertIn("Modell nicht gefunden", str(f.exception))

    def test_stability_formular_und_bytes(self):
        n = self.netz(_Antwort(inhalt=PNG))
        pfad = VC.generate("see", size=(1920, 1080), anbieter="stability")
        self.assertEqual(PNG, Path(pfad).read_bytes())
        art, url, kw = n.anfragen[0]
        self.assertEqual("https://api.stability.ai/v2beta/stable-image/generate/core", url)
        self.assertEqual("image/*", kw["headers"]["Accept"])
        self.assertEqual({"prompt": "see", "output_format": "png", "aspect_ratio": "16:9"}, kw["data"])
        self.assertIn("files", kw)                                         # multipart/form-data

    def test_stability_gefiltert(self):
        gefiltert = _Antwort(inhalt=PNG)
        gefiltert.headers = {"finish-reason": "CONTENT_FILTERED"}
        self.netz(gefiltert)
        with self.assertRaises(VC.CloudFehler):
            VC.generate("x", anbieter="stability")

    def test_recraft_und_together(self):
        n = self.netz(_Antwort(daten={"data": [{"url": "https://img.recraft.example/r.png"}]}), _Antwort(inhalt=PNG))
        VC.generate("fuchs", anbieter="recraft")
        self.assertEqual(("https://external.api.recraft.ai/v1/images/generations",
                          {"model": "recraftv4_1", "prompt": "fuchs"}), (n.anfragen[0][1], n.anfragen[0][2]["json"]))
        n = self.netz(_Antwort(daten={"data": [{"index": 0, "b64_json": base64.b64encode(PNG).decode()}]}))
        VC.generate("fuchs", size=(1024, 768), anbieter="together")
        koerper = n.anfragen[0][2]["json"]
        self.assertEqual("https://api.together.ai/v1/images/generations", n.anfragen[0][1])
        self.assertEqual(("black-forest-labs/FLUX.2-dev", 1024, 768, "base64"),
                         (koerper["model"], koerper["width"], koerper["height"], koerper["response_format"]))

    def test_replicate_wartet_und_holt_mit_schluessel(self):
        n = self.netz(_Antwort(daten={"id": "p1", "status": "starting",
                                      "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}}),
                      _Antwort(daten={"status": "processing"}),
                      _Antwort(daten={"status": "succeeded", "output": ["https://replicate.delivery/o.png"]}),
                      _Antwort(inhalt=PNG))
        VC.generate("berg", anbieter="replicate")
        art, url, kw = n.anfragen[0]
        self.assertEqual("https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions", url)
        self.assertEqual("wait", kw["headers"]["Prefer"])
        self.assertEqual({"input": {"prompt": "berg"}}, kw["json"])
        self.assertEqual("Bearer rp-test", n.anfragen[3][2]["headers"]["Authorization"])   # Dateien nur mit Schlüssel

    def test_minimax_video_v2(self):
        bild = self.ordner / "start.png"
        bild.write_bytes(PNG)
        n = self.netz(_Antwort(daten={"task_id": "m1"}), _Antwort(daten={"status": "processing"}),
                      _Antwort(daten={"status": "succeeded", "content": {"url": "https://minimax.example/v.mp4"}}),
                      _Antwort(inhalt=MP4))
        VC.video_generate("drohne", sekunden=30, startbild=str(bild), anbieter="minimax")
        koerper = n.anfragen[0][2]["json"]
        self.assertEqual("https://api.minimax.io/v2/video_generation", n.anfragen[0][1])
        self.assertEqual((15, "MiniMax-H3"), (koerper["duration"], koerper["model"]))
        self.assertEqual("first_frame", koerper["content"][1]["role"])
        self.assertEqual("https://api.minimax.io/v2/query/video_generation/m1", n.anfragen[1][1])

    def test_luma_bild(self):
        n = self.netz(_Antwort(daten={"id": "g1", "state": "queued", "output": []}),
                      _Antwort(daten={"state": "processing"}),
                      _Antwort(daten={"state": "completed", "output": [{"type": "image", "url": "https://luma.example/b.png"}]}),
                      _Antwort(inhalt=PNG))
        VC.generate("garten", size=(1920, 1080), anbieter="luma")
        art, url, kw = n.anfragen[0]
        self.assertEqual("https://agents.lumalabs.ai/v1/generations", url)
        self.assertEqual("Bearer l-test", kw["headers"]["Authorization"])
        self.assertEqual({"model": "uni-1", "type": "image", "prompt": "garten", "output_format": "png",
                          "aspect_ratio": "16:9"}, kw["json"])
        self.assertEqual("https://agents.lumalabs.ai/v1/generations/g1", n.anfragen[1][1])

    def test_luma_video_mit_startbild_bleibt_bei_5_s(self):
        bild = self.ordner / "start.png"
        bild.write_bytes(PNG)
        n = self.netz(_Antwort(daten={"id": "g2"}),
                      _Antwort(daten={"state": "completed", "output": [{"type": "video", "url": "https://luma.example/v.mp4"}]}),
                      _Antwort(inhalt=MP4))
        VC.video_generate("nebel", sekunden=10, startbild=str(bild), hochkant=True, anbieter="luma")
        koerper = n.anfragen[0][2]["json"]
        self.assertEqual(("ray-3.2", "video", "9:16"), (koerper["model"], koerper["type"], koerper["aspect_ratio"]))
        self.assertEqual("5s", koerper["video"]["duration"])                    # 10 s nicht mit Startbild
        self.assertEqual("image/png", koerper["video"]["start_frame"]["media_type"])

    def test_luma_fehlgeschlagen(self):
        self.netz(_Antwort(daten={"id": "g3"}), _Antwort(daten={"state": "failed", "failure_reason": "abgelehnt"}))
        with self.assertRaises(VC.CloudFehler) as f:
            VC.video_generate("x", sekunden=10, anbieter="luma")
        self.assertIn("abgelehnt", str(f.exception))

    def test_einrichten_liste_und_schluessel_namen(self):
        self.schluessel = {"openai": "sk"}
        self.assertEqual(["google", "xai", "bfl", "ideogram", "runway", "fal", "stability", "recraft", "together",
                          "replicate", "luma"], VC.ohne_schluessel("bild"))
        self.assertEqual("BFL_API_KEY", VC.env_name("bfl"))
        self.assertEqual("RUNWAY_API_KEY", VC.env_name("runway", "video"))
        for art, tabelle in (("bild", VC.BILD), ("video", VC.VIDEO)):
            for a, eintrag in tabelle.items():
                with self.subTest(art=art, a=a):
                    self.assertTrue(VC.env_name(a, art).endswith("_API_KEY"))          # verschlüsselt gespeichert
                    self.assertTrue(VC.schluessel_url(a, art).startswith("https://"))


class AnschlussTests(Basis):
    def test_imagegen_malt_in_der_cloud_ohne_gpu_zu_raeumen(self):
        self.cfg.update(bild_backend="cloud", cloud_bild_anbieter="openai")
        lokal = NS(gpu_fuer_bild=mock.MagicMock())
        with mock.patch.object(VC, "generate", return_value="cloud.png") as malen, \
                mock.patch.dict(sys.modules, {"gguflokal": lokal}):
            self.assertEqual("cloud", imagegen.backend())
            self.assertEqual("cloud.png", imagegen.paint("a fox", model="krea-modell", size=(832, 1216)))
        lokal.gpu_fuer_bild.assert_not_called()
        self.assertNotIn("model", malen.call_args.kwargs)                     # Krea-Name gilt in der Cloud nicht
        self.assertIn("Cloud", imagegen.active_label())

    def test_cloud_ohne_schluessel_faellt_auf_krea(self):
        self.cfg.update(bild_backend="cloud")
        self.schluessel = {}
        self.assertEqual("krea", imagegen.backend())

    def test_video_erstellen_geht_in_die_cloud(self):
        self.cfg.update(video_backend="cloud", cloud_video_anbieter="xai")
        with mock.patch.object(VC, "video_generate", return_value=str(self.ordner / "v.mp4")) as film, \
                mock.patch.object(actions.os, "startfile", create=True):
            res = actions._video_erstellen({"prompt": "a rocket", "sekunden": 8, "seed": 3, "schaerfe": "mittel"})
        self.assertIn("Video fertig", str(res))
        self.assertIn("weggelassen: seed, schaerfe", str(res))
        self.assertEqual(8.0, film.call_args.kwargs["sekunden"])


if __name__ == "__main__":
    unittest.main()
