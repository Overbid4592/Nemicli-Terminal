"""Bild-Motor Stable Diffusion (engines/bildmotoren/sd.py): Sampler gegen diffusers, lange Prompts in Stücken,
ganzer Mini-Checkpoint (Bildnetz + CLIP + VAE) finden und malen. Winzige Zufallsnetze, keine Grafikkarte."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

R = Path(__file__).resolve().parent.parent
for d in ("", "core", "engines", "tools", "ui", "tests"):
    if str(R / d) not in sys.path:
        sys.path.insert(0, str(R / d))

try:
    import torch
    import diffusers  # noqa: F401
    REFERENZ = True
except Exception:                                    # pragma: no cover
    REFERENZ = False


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class Sampler(unittest.TestCase):
    def test_zeitschritt_trifft_die_tabelle(self):
        from bildmotoren import sd
        alle = sd.rauschstaerken_alle()
        for t in (0, 1, 500, 998, 999):
            self.assertAlmostEqual(sd.zeitschritt(float(alle[t]), alle), t, places=6)

    def test_karras_von_gross_nach_klein(self):
        from bildmotoren import sd
        alle = sd.rauschstaerken_alle()
        s = sd.karras(10, alle)
        self.assertEqual(len(s), 11)
        self.assertAlmostEqual(s[0], float(alle[-1]), places=5)
        self.assertAlmostEqual(s[-2], float(alle[0]), places=5)
        self.assertEqual(s[-1], 0.0)
        self.assertTrue(all(a > b for a, b in zip(s, s[1:])))

    def test_dpmpp_2m_wie_diffusers(self):
        """Gleiches Rauschen, gleiche Vorhersage -> gleiches Ergebnis wie DPMSolverMultistep (dpmsolver++, Karras)."""
        from diffusers import DPMSolverMultistepScheduler
        from bildmotoren import sd
        schritte = 20
        g = torch.Generator().manual_seed(3)
        rauschen = torch.randn(1, 4, 8, 8, generator=g, dtype=torch.float64)
        ziel = torch.randn(1, 4, 8, 8, generator=g, dtype=torch.float64)

        def eps(x_vp, alpha, sigma_vp):              # Vorhersage aus dem VP-Latent, für beide Rechenwege gleich
            return (x_vp - alpha * ziel) / sigma_vp * 0.9 + 0.1 * torch.tanh(x_vp)

        plan = DPMSolverMultistepScheduler(beta_schedule="scaled_linear", beta_start=0.00085, beta_end=0.012,
                                           algorithm_type="dpmsolver++", solver_order=2, use_karras_sigmas=True,
                                           final_sigmas_type="zero", lower_order_final=False)
        plan.set_timesteps(schritte)
        x = rauschen.clone() * plan.init_noise_sigma
        for i, t in enumerate(plan.timesteps):
            s = float(plan.sigmas[i])
            alpha = 1.0 / (s * s + 1.0) ** 0.5
            x = plan.step(eps(x, alpha, s * alpha), t, x).prev_sample
        soll = x

        alle = sd.rauschstaerken_alle()
        sig = sd.karras(schritte, alle)

        def entrauschen(x_ve, s):
            alpha = 1.0 / (s * s + 1.0) ** 0.5
            return x_ve - s * eps(x_ve * alpha, alpha, s * alpha)
        ist = sd.dpmpp_2m(entrauschen, rauschen * (1.0 + sig[0] ** 2) ** 0.5, sig)
        self.assertLess((soll - ist).abs().max().item(), 1e-4)


class LangePrompts(unittest.TestCase):
    def _stuecke(self, n: int, hoechstens: int = 3):
        from bildmotoren import textenc
        with mock.patch.object(textenc, "_clip_roh", lambda ordner: (lambda text: list(range(1, n + 1)))):
            return textenc.clip_stuecke(Path("."), hoechstens)("egal")

    def test_teilt_in_stuecke_zu_75(self):
        from bildmotoren.textenc import CLIP_BOS, CLIP_EOS
        st = self._stuecke(160)
        self.assertEqual([len(s) for s in st], [77, 77, 77])
        self.assertEqual(st[0][:2], [CLIP_BOS, 1])
        self.assertEqual(st[0][76], CLIP_EOS)
        self.assertEqual(st[1][1], 76)
        self.assertEqual(st[2][1:12], list(range(151, 161)) + [CLIP_EOS])

    def test_leer_ein_stueck_und_hoechstens(self):
        self.assertEqual(len(self._stuecke(0)), 1)
        self.assertEqual(len(self._stuecke(500)), 3)
        self.assertEqual(len(self._stuecke(500, 1)), 1)

    def test_kurz_wie_bisher(self):
        from bildmotoren import textenc
        with mock.patch.object(textenc, "_clip_roh", lambda ordner: (lambda text: [5, 6])):
            self.assertEqual(textenc.clip_tokenizer(Path("."))("x"), [49406, 5, 6] + [49407] * 74)


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class GanzerCheckpoint(unittest.TestCase):
    """Mini-SDXL und Mini-SD-1.5 als ganzer Checkpoint im Bildordner: erkennen, Zubehör aus der Datei, malen."""

    @classmethod
    def setUpClass(cls):
        from safetensors.torch import save_file
        import test_sdvideo as T
        cls.tmp = tempfile.TemporaryDirectory()
        cls.o = Path(cls.tmp.name) / "Bildmodelle"
        (cls.o / "tokenizer_clip").mkdir(parents=True)
        (cls.o / "tokenizer_clip" / "vocab.json").write_text("{}", encoding="utf-8")
        vae = {"first_stage_model." + k: v for k, v in T.vae_namen(T._vae(4)).items()}

        xl = {"model.diffusion_model." + k: v for k, v in T.original_namen(T.Bildnetz()._sdxl()).items()}
        xl.update({"conditioner.embedders.0.transformer." + k: v
                   for k, v in T.clip_namen(T._clip(768, 1, "quick_gelu")).items()})
        xl.update({"conditioner.embedders.1.model." + k: v
                   for k, v in T.openclip_namen(T._clip(1280, 1, "gelu", projektion=True)).items()})
        (cls.o / "MiniXL").mkdir()
        save_file({k: v.half().contiguous() for k, v in {**xl, **vae}.items()}, str(cls.o / "MiniXL" / "minixl.safetensors"))
        nur_netz = {k: v for k, v in xl.items() if k.startswith("model.diffusion_model.")}
        (cls.o / "NurNetz").mkdir()
        save_file({k: v.half().contiguous() for k, v in nur_netz.items()}, str(cls.o / "NurNetz" / "unet.safetensors"))

        v15 = {"model.diffusion_model." + k: v for k, v in T.original_namen(T.Bildnetz()._sd15()).items()}
        v15.update({"cond_stage_model.transformer." + k: v
                    for k, v in T.clip_namen(T._clip(768, 1, "quick_gelu")).items()})
        (cls.o / "Mini15").mkdir()
        save_file({k: v.half().contiguous() for k, v in {**v15, **vae}.items()}, str(cls.o / "Mini15" / "mini15.safetensors"))

    @classmethod
    def tearDownClass(cls):
        import bildmotoren
        bildmotoren._CACHE.clear()
        bildmotoren.entladen()
        cls.tmp.cleanup()

    def setUp(self):
        import bildmotoren
        from bildmotoren import textenc
        bildmotoren._CACHE.clear()
        ids = [49406, 320, 1125, 539, 49407] + [49407] * 72
        for flicken in (mock.patch.object(bildmotoren, "ORDNER", [self.o]),
                        mock.patch.object(textenc, "clip_stuecke", lambda ordner, n=3: (lambda text: [list(ids)])),
                        mock.patch("bildmotoren.gemeinsam.geraet", lambda: (torch.device("cpu"), torch.float32)),
                        mock.patch("bildmotoren._entladen_nach", lambda: 0)):
            flicken.start()
            self.addCleanup(flicken.stop)

    def test_finden_und_zubehoer_aus_der_datei(self):
        import bildmotoren
        m = bildmotoren.modelle()
        self.assertEqual({n: x["bauart"] for n, x in m.items()}, {"MiniXL": "sd", "Mini15": "sd", "NurNetz": "sd"})
        t = bildmotoren.teile("MiniXL")
        self.assertEqual((t["text"], t["vae"]), (t["dit"], t["dit"]))
        self.assertIsNone(bildmotoren.missing_reason("MiniXL"))
        self.assertIsNone(bildmotoren.missing_reason("Mini15"))
        self.assertEqual(bildmotoren.vorgaben("MiniXL")["groesse"], (1024, 1024))
        self.assertEqual(bildmotoren.vorgaben("Mini15")["cfg"], 7.0)
        self.assertIn("ganzen Checkpoint", bildmotoren.missing_reason("NurNetz"))

    def test_ohne_tokenizer_wird_er_genannt(self):
        import bildmotoren
        (self.o / "tokenizer_clip" / "vocab.json").rename(self.o / "tokenizer_clip" / "beiseite")
        self.addCleanup(lambda: (self.o / "tokenizer_clip" / "beiseite").rename(self.o / "tokenizer_clip" / "vocab.json"))
        self.assertIn("Tokenizer fehlt", bildmotoren.missing_reason("MiniXL"))

    def _malen(self, name: str, size):
        import bildmotoren
        from PIL import Image
        ziel = Path(self.tmp.name) / f"{name}.png"
        with mock.patch("profilordner.bild_ziel", lambda *a, **k: ziel):
            pfad = bildmotoren.generate("ein roter Ball", model=name, steps=4, size=size, seed=7)
        with Image.open(pfad) as im:
            self.assertEqual(im.size, (256, 320))
            self.assertIn("backend=sd", im.text["params"])
            arr = __import__("numpy").asarray(im)
        self.assertGreater(arr.std(), 0.0)
        self.assertEqual(bildmotoren.im_ram(), [])

    def test_sdxl_malt_auf_64er_raster(self):
        self._malen("MiniXL", (300, 330))

    def test_sd15_malt(self):
        self._malen("Mini15", (256, 320))

    def test_cfg_und_gegen_prompt(self):
        """Die Gegen-Seite trägt immer den festen Gegen-Prompt der Bildsperre (nie Nullen); mit CFG 1 rechnet das Netz
        nur einmal je Schritt."""
        import bildmotoren
        from bildmotoren import sd
        from videomotoren import sd_netz
        aufrufe = []
        echt = sd_netz.Netz.__call__

        def zaehlen(selbst, x, t, kontext, **k):
            aufrufe.append(float(kontext.abs().sum()))
            return echt(selbst, x, t, kontext, **k)
        t = bildmotoren.teile("MiniXL")
        with mock.patch.object(sd_netz.Netz, "__call__", zaehlen):
            sd.malen("x", t, schritte=3, breite=256, hoehe=256, seed=1, device=torch.device("cpu"),
                     dtype=torch.float32, cfg=4.0, negativ="")
            self.assertEqual(len(aufrufe), 6)
            self.assertTrue(all(x > 0 for x in aufrufe[1::2]))
            aufrufe.clear()
            sd.malen("x", t, schritte=3, breite=256, hoehe=256, seed=1, device=torch.device("cpu"),
                     dtype=torch.float32, cfg=1.0)
            self.assertEqual(len(aufrufe), 3)
        bildmotoren.entladen()


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class GegenPromptDerKI(GanzerCheckpoint):
    """`neg` der KI kommt zum festgenagelten dazu, nur Stable Diffusion nimmt ihn, er steht im Bild."""

    def test_neg_und_festgenagelt_zusammen(self):
        import bildmotoren
        from bildmotoren import sd
        gesehen = []
        echt = sd.anwenden

        def merken(prompt, negativ, namen_=None):
            gesehen.append(negativ)
            return echt(prompt, negativ, namen_)
        t = bildmotoren.teile("MiniXL")
        with mock.patch.object(sd, "anwenden", merken), \
                mock.patch.object(sd, "_einstellung", lambda k, s: "watermark, text" if k == "bild_sd_negativ" else s):
            sd.malen("x", t, schritte=2, breite=256, hoehe=256, seed=1, device=torch.device("cpu"),
                     dtype=torch.float32, cfg=1.0, neg="nude,  nipples")
            sd.malen("x", t, schritte=2, breite=256, hoehe=256, seed=1, device=torch.device("cpu"),
                     dtype=torch.float32, cfg=1.0)
        self.assertEqual(gesehen, ["nude, nipples, watermark, text", "watermark, text"])
        bildmotoren.entladen()

    def test_weiche_gibt_neg_nur_an_sd_und_ins_bild(self):
        import bildmotoren
        from bildmotoren import sd
        from PIL import Image
        aufrufe = []
        echt = sd.malen

        def malen(*a, **k):
            aufrufe.append(k.get("neg"))
            return echt(*a, **k)
        ziel = Path(self.tmp.name) / "neg.png"
        with mock.patch.object(sd, "malen", malen), mock.patch("profilordner.bild_ziel", lambda *a, **k: ziel):
            bildmotoren.generate("ein Ball", model="MiniXL", steps=2, size=(256, 256), seed=3, neg="blurry")
        self.assertEqual(aufrufe, ["blurry"])
        with Image.open(ziel) as im:
            self.assertEqual(im.text.get("neg"), "blurry")

    def test_imagegen_reicht_neg_an_den_motor(self):
        import imagegen
        import krea
        with mock.patch.object(imagegen, "backend", lambda: imagegen.MOTOR), \
                mock.patch.object(imagegen, "_gpu_vorbereitet", lambda fn, a, k: fn(*a, **k)), \
                mock.patch("malkind.aktiv", lambda: False), mock.patch.object(krea, "im_ram", lambda: False), \
                mock.patch("bildmotoren.generate", return_value="x.png") as gen:
            imagegen.paint("ein Ball", neg="blurry")
        self.assertEqual(gen.call_args.kwargs["neg"], "blurry")


class KiHinweis(unittest.TestCase):
    def test_sd_hinweis_mit_neg_und_1024(self):
        import bildmotoren
        import imagegen
        import persona
        from bildmotoren import sd
        with mock.patch.object(imagegen, "backend", lambda: "motor"), \
                mock.patch.object(bildmotoren, "modelle", lambda: {"XL": {"bauart": "sd", "dit": Path("x")}}), \
                mock.patch.object(bildmotoren, "gewaehlt", lambda: "XL"), \
                mock.patch.object(bildmotoren, "titel", lambda n=None: "🟫 Stable Diffusion"), \
                mock.patch.object(sd, "art_von", lambda dit: "sdxl"), \
                mock.patch.object(sd, "_einstellung", lambda k, s: "watermark" if k == "bild_sd_negativ" else s):
            text = persona._bild_hinweis_motor()
        self.assertIn("`neg` (Gegen-Prompt) gehört dazu", text)
        self.assertIn("`1024x1024`", text)
        self.assertIn("„watermark“", text)
        self.assertIn("optional `neg`, `seed`", text)
        self.assertNotIn("Keinen Negativ-Prompt", text)
        self.assertIn(f"{sd.STUECKE * 75} Token", text)

    def test_andere_motoren_ohne_neg(self):
        import bildmotoren
        import imagegen
        import persona
        with mock.patch.object(imagegen, "backend", lambda: "motor"), \
                mock.patch.object(bildmotoren, "modelle", lambda: {"Z": {"bauart": "zimage", "dit": Path("x")}}), \
                mock.patch.object(bildmotoren, "gewaehlt", lambda: "Z"), \
                mock.patch.object(bildmotoren, "titel", lambda n=None: "🟩 Z-Image"):
            text = persona._bild_hinweis_motor()
        self.assertIn("Keinen Negativ-Prompt", text)
        self.assertNotIn("`neg`", text)


class _Daten(unittest.TestCase):
    """Config im Speicher, Vorlagen-Datei im Testordner."""

    def setUp(self):
        import config
        from bildmotoren import sd
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg: dict = {}
        for p in (mock.patch.object(config, "load", side_effect=lambda *a, **k: dict(self.cfg)),
                  mock.patch.object(config, "update", side_effect=lambda **k: self.cfg.update(k) or True),
                  mock.patch.object(sd, "_vorlagen_pfad", lambda: Path(self.tmp.name) / sd.VORLAGEN_DATEI)):
            p.start()
            self.addCleanup(p.stop)

    def _csv(self, text: str) -> Path:
        p = Path(self.tmp.name) / "styles.csv"
        p.write_text(text, encoding="utf-8-sig")
        return p


class Vorlagen(_Daten):
    CSV = ('name,prompt,negative_prompt\n'
           'Foto,"{prompt}, analog photo, film grain","cartoon, painting"\n'
           'Haut,"detailed skin texture",\n'
           'Nur Negativ,,"watermark, text"\n')

    def test_einlesen_und_anwenden(self):
        from bildmotoren import sd
        self.assertEqual(sd.vorlagen_einlesen(self._csv(self.CSV)), 3)
        self.assertEqual([v["name"] for v in sd.vorlagen()], ["Foto", "Haut", "Nur Negativ"])
        self.assertEqual(sd.anwenden("a cat", "blurry"), ("a cat", "blurry"))          # nichts an
        sd.vorlage_schalten("Foto", True)
        sd.vorlage_schalten("Haut", True)
        sd.vorlage_schalten("Nur Negativ", True)
        prompt, neg = sd.anwenden("a cat", "blurry")
        self.assertEqual(prompt, "a cat, analog photo, film grain, detailed skin texture")
        self.assertEqual(neg, "blurry, cartoon, painting, watermark, text")
        sd.vorlage_schalten("Haut", False)
        self.assertEqual(sd.aktive(), ["Foto", "Nur Negativ"])

    def test_erneut_einlesen_ersetzt_gleichnamige(self):
        from bildmotoren import sd
        sd.vorlagen_einlesen(self._csv(self.CSV))
        sd.vorlagen_einlesen(self._csv('name,prompt,negative_prompt\nHaut,"pores",\nNeu,"x",\n'))
        nach = {v["name"]: v for v in sd.vorlagen()}
        self.assertEqual(len(nach), 4)
        self.assertEqual(nach["Haut"]["prompt"], "pores")

    def test_falsche_datei_wird_abgelehnt(self):
        from bildmotoren import sd
        with self.assertRaises(ValueError):
            sd.vorlagen_einlesen(self._csv("a;b\n1;2\n"))
        p = Path(self.tmp.name) / "styles.txt"
        p.write_text("name,prompt\nx,y\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            sd.vorlagen_einlesen(p)
        self.assertEqual(sd.vorlagen(), [])

    def test_geloeschte_vorlage_zaehlt_nicht(self):
        from bildmotoren import sd
        sd.vorlagen_einlesen(self._csv(self.CSV))
        self.cfg["bild_sd_vorlagen"] = ["Foto", "Gibt es nicht"]
        self.assertEqual(sd.aktive(), ["Foto"])


class Controllcenter(_Daten):
    def test_seite_im_baum_und_fuer_die_ki(self):
        import kontrollzentrum as KZ
        self.assertIn("sd", [a["id"] for a in KZ.baum()])
        self.assertIn("sd", KZ._SEITEN)

    def test_werte_pruefen(self):
        import kontrollzentrum as KZ
        werte, fehler = KZ.sd_werte_pruefen({"negativ": "  blurry,\n  text ", "cfg_sdxl": 4.5, "cfg_sd15": "7",
                                             "schritte": 0})
        self.assertIsNone(fehler)
        self.assertEqual(werte, {"bild_sd_negativ": "blurry, text", "bild_sd_sdxl_cfg": 4.5, "bild_sd_sd15_cfg": 7.0,
                                 "bild_sd_schritte": None})
        self.assertIsNotNone(KZ.sd_werte_pruefen({"cfg_sdxl": 50})[1])
        self.assertIsNotNone(KZ.sd_werte_pruefen({"schritte": 2})[1])
        self.assertIsNotNone(KZ.sd_werte_pruefen({"negativ": "x" * 5000})[1])

    def test_daten_mit_vorlagen_und_gelerntem(self):
        import bildmotoren
        import kontrollzentrum as KZ
        import promptschmied
        from bildmotoren import sd
        sd.vorlagen_einlesen(self._csv(Vorlagen.CSV))
        sd.vorlage_schalten("Haut", True)
        self.cfg.update(bild_sd_negativ="blurry", bild_backend="motor", bild_motor_modell="XL")
        import imagegen
        with mock.patch.object(bildmotoren, "modelle", lambda: {"XL": {"bauart": "sd", "dit": Path("x")}}), \
                mock.patch.object(imagegen, "backend", lambda: "motor"), \
                mock.patch.object(sd, "art_von", lambda dit: "sdxl"), \
                mock.patch.object(promptschmied, "gelernt", lambda m, db=None: (["film grain"], ["anime"])):
            d = KZ.sd_daten()
        self.assertEqual(d["modelle"], [{"name": "XL", "art": "sdxl"}])
        self.assertTrue(d["aktiv"])
        self.assertEqual(d["negativ"], "blurry")
        self.assertEqual([v["name"] for v in d["vorlagen"] if v["an"]], ["Haut"])
        self.assertEqual(d["gelernt"], {"XL": {"gut": ["film grain"], "schlecht": ["anime"]}})

    def test_seite_im_fenster(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import control_fenster as CF
        gesendet: list[dict] = []
        leitung = mock.MagicMock()
        leitung.senden.side_effect = gesendet.append
        f = CF.ControlFenster(leitung)
        self.addCleanup(f.deleteLater)
        f.waehlen_id("sd")
        self.assertEqual(f.stapel.currentIndex(), CF.SEITEN["sd"])
        self.assertIn({"op": "sd"}, gesendet)
        vorlagen = [{"name": f"Stil {i}", "prompt": "p", "negativ": "", "an": i == 70} for i in range(80)]
        f._ereignis({"t": "sd", "modelle": [{"name": "XL", "art": "sdxl"}], "aktiv": True, "gewaehlt": "XL",
                     "negativ": "blurry", "cfg_sdxl": 5.0, "cfg_sd15": 7.0, "schritte": 0, "schritte_vorgabe": 28,
                     "vorlagen": vorlagen, "gelernt": {}})
        s = f.sd
        self.assertEqual(s.negativ.toPlainText(), "blurry")
        self.assertEqual(s.liste.itemAt(0).widget().text(), "Stil 70")            # eingeschaltete zuerst
        self.assertEqual(s.liste.count(), CF.SD_VORLAGEN_ZEIGEN + 1)             # + Hinweis auf weitere
        s.suche.setText("Stil 7")
        self.assertEqual(s.liste.count(), 11)
        s.negativ.setPlainText("text, watermark")
        s.cfg_xl.setValue(4.5)
        s._speichern()
        self.assertEqual(gesendet[-1], {"op": "sd_setzen", "negativ": "text, watermark", "cfg_sdxl": 4.5,
                                        "cfg_sd15": 7.0, "schritte": 0})
        s.liste.itemAt(1).widget().setChecked(True)
        self.assertEqual(gesendet[-1]["op"], "sd_vorlage")
        self.assertTrue(gesendet[-1]["an"])


class Bruecke(unittest.TestCase):
    def test_sd_ops_in_der_sitzung(self):
        quelle = (R / "main_dienste.py").read_text(encoding="utf-8")
        for op in ('"sd": sd_senden', '"sd_setzen": sd_setzen', '"sd_vorlage": sd_vorlage',
                   '"sd_vorlagen_einlesen": sd_vorlagen_einlesen'):
            self.assertIn(op, quelle)


if __name__ == "__main__":
    unittest.main()
