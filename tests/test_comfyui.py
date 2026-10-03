"""Offline-Tests für die ComfyUI-Anbindung (engines/comfyui.py).

Kein Netz: ComfyUI wird nachgebaut. Getestet wird vor allem das, woran es
vorher scheiterte – dass NemiCLI die RICHTIGE Adresse fragt.

python -m unittest discover -s tests -p test_comfyui.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import comfyui as C      # noqa: E402
import sdwebui as SD     # noqa: E402


PNG = (b"\x89PNG\r\n\x1a\n"
       + b"\x00\x00\x00\rIHDR" + b"\x00" * 13 + b"\x00" * 4
       + b"\x00\x00\x00\x00IEND\xaeB`\x82")


class FakeAntwort:
    def __init__(self, status=200, daten=None, inhalt=b""):
        self.status_code = status
        self._daten = daten if daten is not None else {}
        self.content = inhalt
        self.text = json.dumps(self._daten)
        self.reason_phrase = "OK" if status == 200 else "Error"

    def json(self):
        return self._daten

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeComfy:
    """Eine ComfyUI, die man sich hinstellen kann."""

    def __init__(self, *, checkpoints=("modelA.safetensors", "ponyXL.safetensors"),
                 unets=(), clips=("qwen3vl.safetensors",),
                 vaes=("qwen_image_vae.safetensors", "pixel_space"),
                 stats=True, post_status=200, post_daten=None, history=None):
        self.checkpoints = list(checkpoints)
        self.unets = list(unets)
        self.clips = list(clips)
        self.vaes = list(vaes)
        self.stats = stats
        self.post_status = post_status
        self.post_daten = post_daten if post_daten is not None else {"prompt_id": "abc123"}
        self.history = history
        self.gefragt = []          # jede GET-Adresse
        self.geschickt = None      # der abgeschickte Ablaufplan

    def client(self, timeout=None):
        fake = self

        class Client:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False

            def get(self_, pfad, params=None, **k):
                fake.gefragt.append(pfad)
                if pfad == "/system_stats":
                    if not fake.stats:
                        return FakeAntwort(404)
                    return FakeAntwort(200, {
                        "system": {"comfyui_version": "0.35.1"},
                        "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 5060 Ti : cudaMallocAsync",
                                     "vram_total": 17071309824}]})
                if pfad == "/object_info/CheckpointLoaderSimple":
                    return FakeAntwort(200, {"CheckpointLoaderSimple": {"input": {
                        "required": {"ckpt_name": [fake.checkpoints]}}}})
                if pfad == "/object_info/UNETLoader":
                    return FakeAntwort(200, {"UNETLoader": {"input": {
                        "required": {"unet_name": [fake.unets]}}}})
                if pfad == "/object_info/CLIPLoader":
                    return FakeAntwort(200, {"CLIPLoader": {"input": {"required": {
                        "clip_name": [fake.clips],
                        "type": [["stable_diffusion", "krea2", "qwen_image", "flux2"]]}}}})
                if pfad == "/object_info/VAELoader":
                    return FakeAntwort(200, {"VAELoader": {"input": {
                        "required": {"vae_name": [fake.vaes]}}}})
                if pfad == "/object_info/KSampler":
                    return FakeAntwort(200, {"KSampler": {"input": {"required": {
                        "sampler_name": [["euler", "dpmpp_2m"]],
                        "scheduler": [["normal", "karras"]]}}}})
                if pfad.startswith("/history/"):
                    return FakeAntwort(200, fake.history or {})
                if pfad == "/prompt":
                    return FakeAntwort(200, {"exec_info": {"queue_remaining": 0}})
                if pfad == "/view":
                    return FakeAntwort(200, inhalt=PNG)
                return FakeAntwort(404)

            def post(self_, pfad, json=None, **k):
                fake.geschickt = (json or {}).get("prompt")
                return FakeAntwort(fake.post_status, fake.post_daten)

        return Client()


def mit(fake):
    return mock.patch.object(C, "_client", fake.client)


class DieRichtigeAdresse(unittest.TestCase):
    """Der Kern des alten Fehlers: NemiCLI fragte /sdapi/v1/sd-models, ComfyUI
    antwortete 404, und daraus wurde 'nicht erreichbar'."""

    def test_available_fragt_system_stats(self):
        fake = FakeComfy()
        with mit(fake):
            self.assertTrue(C.available())
        self.assertIn("/system_stats", fake.gefragt)

    def test_available_fragt_NIEMALS_die_forge_adressen(self):
        fake = FakeComfy()
        with mit(fake):
            C.available()
            C.models()
            C.samplers()
        for pfad in fake.gefragt:
            self.assertNotIn("/sdapi/", pfad, f"{pfad} ist Forge-Sprache")

    def test_im_code_steht_kein_sdapi(self):
        quelle = (ROOT / "engines" / "comfyui.py").read_text(encoding="utf-8")
        ohne_kommentar = "\n".join(z for z in quelle.splitlines()
                                   if not z.lstrip().startswith("#"))
        # Im Doc-Kommentar darf es erklaerend vorkommen, im Code nicht.
        self.assertNotIn('c.get("/sdapi', ohne_kommentar)
        self.assertNotIn('c.post("/sdapi', ohne_kommentar)

    def test_nicht_erreichbar_wenn_nichts_antwortet(self):
        with mit(FakeComfy(stats=False)):
            self.assertFalse(C.available())


class AdresseUndModell(unittest.TestCase):
    def setUp(self):
        self._daten = {}
        self._p1 = mock.patch.object(C.config, "load", lambda: dict(self._daten))
        self._p2 = mock.patch.object(C.config, "update", self._update)
        self._p1.start(); self._p2.start()

    def _update(self, **kw):
        self._daten.update(kw)

    def tearDown(self):
        self._p1.stop(); self._p2.stop()

    def test_standardadresse_ist_8188(self):
        self.assertEqual(C.host(), "http://127.0.0.1:8188")
        self.assertIn("8188", C.DEFAULT_HOST)

    def test_adresse_ohne_schema_bekommt_http(self):
        C.set_host("127.0.0.1:9999")
        self.assertEqual(C.host(), "http://127.0.0.1:9999")

    def test_leere_adresse_faellt_auf_die_vorgabe(self):
        C.set_host("")
        self.assertEqual(C.host(), C.DEFAULT_HOST)

    def test_modellwahl_bleibt_gespeichert(self):
        C.set_chosen_model("ponyXL.safetensors")
        self.assertEqual(C.chosen_model(), "ponyXL.safetensors")
        C.set_chosen_model(None)
        self.assertIsNone(C.chosen_model())


class WasComfyKann(unittest.TestCase):
    def test_checkpoints_werden_gelesen(self):
        with mit(FakeComfy()):
            self.assertEqual(list(C.models()),
                             ["modelA.safetensors", "ponyXL.safetensors"])

    def test_sampler_und_scheduler_werden_gelesen(self):
        with mit(FakeComfy()):
            self.assertEqual(C.samplers(), ["euler", "dpmpp_2m"])
            self.assertEqual(C.schedulers(), ["normal", "karras"])

    def test_info_zeigt_version_gpu_und_anzahl(self):
        with mit(FakeComfy()):
            i = C.info()
        self.assertEqual(i["version"], "0.35.1")
        self.assertEqual(i["gpu"], "NVIDIA GeForce RTX 5060 Ti")
        self.assertEqual(i["vram_gb"], 15.9)
        self.assertEqual(i["checkpoints"], 2)

    def test_gpu_name_ohne_speichermodus(self):
        self.assertEqual(C._gpu_name("cuda:0 NVIDIA GeForce RTX 5060 Ti : cudaMallocAsync"),
                         "NVIDIA GeForce RTX 5060 Ti")
        self.assertEqual(C._gpu_name("cpu"), "cpu")

    def test_modell_per_teilstring_finden(self):
        with mit(FakeComfy()):
            self.assertEqual(C.resolve_model("pony"), "ponyXL.safetensors")
            self.assertEqual(C.resolve_model("PONYXL.safetensors"), "ponyXL.safetensors")

    def test_ohne_checkpoints_gibt_es_keins(self):
        with mit(FakeComfy(checkpoints=())):
            self.assertIsNone(C.resolve_model("egal"))

    def test_unbekannter_sampler_faellt_weich_zurueck(self):
        liste = ["euler", "dpmpp_2m"]
        self.assertEqual(C._passend(liste, "dpmpp_2m", "euler"), "dpmpp_2m")
        self.assertEqual(C._passend(liste, "DPMPP_2M", "euler"), "dpmpp_2m")
        self.assertEqual(C._passend(liste, "gibtsnicht", "euler"), "euler")
        self.assertEqual(C._passend(liste, "gibtsnicht", "auchnicht"), "euler")


class DerAblaufplan(unittest.TestCase):
    def _plan(self, **kw):
        vorgabe = dict(ckpt="m.safetensors", neg="blurry", steps=25, cfg=7.0,
                       width=1024, height=768, seed=42, sampler="euler",
                       scheduler="normal")
        vorgabe.update(kw)
        return C.workflow("ein Fuchs", **vorgabe)

    def test_sieben_bausteine(self):
        plan = self._plan()
        arten = sorted(n["class_type"] for n in plan.values())
        self.assertEqual(arten, ["CLIPTextEncode", "CLIPTextEncode",
                                 "CheckpointLoaderSimple", "EmptyLatentImage",
                                 "KSampler", "SaveImage", "VAEDecode"])

    def test_die_bausteine_haengen_richtig_zusammen(self):
        plan = self._plan()
        k = plan["3"]["inputs"]
        self.assertEqual(k["model"], ["4", 0])        # Modell vom Checkpoint
        self.assertEqual(k["positive"], ["6", 0])
        self.assertEqual(k["negative"], ["7", 0])
        self.assertEqual(k["latent_image"], ["5", 0])
        self.assertEqual(plan["8"]["inputs"]["samples"], ["3", 0])
        self.assertEqual(plan["8"]["inputs"]["vae"], ["4", 2])
        self.assertEqual(plan["9"]["inputs"]["images"], ["8", 0])
        self.assertEqual(plan["6"]["inputs"]["clip"], ["4", 1])

    def test_werte_landen_im_plan(self):
        plan = self._plan(steps=12, cfg=1.5, width=832, height=1216, seed=7)
        self.assertEqual(plan["3"]["inputs"]["steps"], 12)
        self.assertEqual(plan["3"]["inputs"]["cfg"], 1.5)
        self.assertEqual(plan["3"]["inputs"]["seed"], 7)
        self.assertEqual(plan["5"]["inputs"]["width"], 832)
        self.assertEqual(plan["5"]["inputs"]["height"], 1216)
        self.assertEqual(plan["6"]["inputs"]["text"], "ein Fuchs")
        self.assertEqual(plan["7"]["inputs"]["text"], "blurry")


class Malen(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._alt = SD.OUT_DIR
        SD.OUT_DIR = Path(self._tmp.name)
        self._daten = {}
        self._p1 = mock.patch.object(C.config, "load", lambda: dict(self._daten))
        self._p2 = mock.patch.object(C.config, "update", lambda **kw: self._daten.update(kw))
        self._p1.start(); self._p2.start()

    def tearDown(self):
        SD.OUT_DIR = self._alt
        self._p1.stop(); self._p2.stop()
        self._tmp.cleanup()

    def _fertig(self):
        return {"abc123": {"status": {"status_str": "success", "completed": True},
                           "outputs": {"9": {"images": [
                               {"filename": "NemiCLI_00001_.png", "subfolder": "",
                                "type": "output"}]}}}}

    def test_bild_wird_gemalt_und_gespeichert(self):
        fake = FakeComfy(history=self._fertig())
        with mit(fake):
            pfad = C.generate("ein roter Fuchs", seed=42)
        p = Path(pfad)
        self.assertTrue(p.exists())
        self.assertRegex(p.name, r"^[A-Za-z0-9_]+_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(_\d+)?\.(png|jpg)$")  # Name_Datum_Uhrzeit
        # Der Ablaufplan ging wirklich raus, mit dem gewaehlten Checkpoint.
        self.assertEqual(fake.geschickt["4"]["inputs"]["ckpt_name"],
                         "modelA.safetensors")
        self.assertEqual(fake.geschickt["3"]["inputs"]["seed"], 42)

    def test_das_bild_bekommt_metadaten(self):
        with mit(FakeComfy(history=self._fertig())):
            pfad = C.generate("ein roter Fuchs", seed=7)
        roh = Path(pfad).read_bytes()
        self.assertIn(b"backend=comfyui", roh)
        self.assertIn(b"ein roter Fuchs", roh)

    def test_ohne_seed_wird_einer_gewuerfelt(self):
        fake = FakeComfy(history=self._fertig())
        with mit(fake):
            C.generate("fuchs")
        self.assertGreaterEqual(fake.geschickt["3"]["inputs"]["seed"], 0)

    def test_leerer_prompt_geht_nicht(self):
        with self.assertRaises(RuntimeError):
            C.generate("   ")

    def test_ohne_comfyui_kommt_ein_klarer_satz(self):
        with mit(FakeComfy(stats=False)):
            with self.assertRaises(RuntimeError) as e:
                C.generate("fuchs")
        self.assertIn("nicht erreichbar", str(e.exception))
        self.assertIn("8188", str(e.exception))

    def test_ohne_jedes_modell_kommt_ein_klarer_satz(self):
        with mit(FakeComfy(checkpoints=(), history=self._fertig())):
            with self.assertRaises(RuntimeError) as e:
                C.generate("fuchs")
        self.assertIn("kein einziges Modell", str(e.exception))
        self.assertIn("extra_model_paths", str(e.exception))

    def test_abgelehnter_plan_wird_erklaert(self):
        fake = FakeComfy(post_status=400, post_daten={
            "error": {"message": "Prompt has no outputs"},
            "node_errors": {"3": {"errors": [{"message": "value not in list"}]}}})
        with mit(fake):
            with self.assertRaises(RuntimeError) as e:
                C.generate("fuchs")
        self.assertIn("lehnt den Ablaufplan ab", str(e.exception))
        self.assertIn("Prompt has no outputs", str(e.exception))

    def test_fehler_beim_rechnen_wird_erklaert(self):
        kaputt = {"abc123": {"status": {"status_str": "error", "messages": [
            ["execution_error", {"node_type": "KSampler",
                                 "exception_message": "out of memory"}]]}}}
        with mit(FakeComfy(history=kaputt)):
            with self.assertRaises(RuntimeError) as e:
                C.generate("fuchs")
        self.assertIn("KSampler", str(e.exception))
        self.assertIn("out of memory", str(e.exception))

    def test_fertig_aber_ohne_bild_wird_gemeldet(self):
        leer = {"abc123": {"status": {"status_str": "success", "completed": True},
                           "outputs": {}}}
        with mit(FakeComfy(history=leer)):
            with self.assertRaises(RuntimeError) as e:
                C.generate("fuchs")
        self.assertIn("SaveImage", str(e.exception))


class GeteilteModelle(unittest.TestCase):
    """Krea/Qwen/Flux haben KEINEN Checkpoint – Modell, CLIP und VAE liegen
    als drei Dateien nebeneinander. Genau daran scheiterte der erste Anlauf:
    NemiCLI suchte nur Checkpoints und meldete 'keine Modelle'."""

    def _krea(self, **kw):
        vorgabe = dict(checkpoints=(), unets=("moodyKrea2Mix_v70.safetensors",),
                       clips=("qwen3vl_4b_fp8_scaled.safetensors",),
                       vaes=("qwen_image_vae.safetensors", "pixel_space"))
        vorgabe.update(kw)
        return FakeComfy(**vorgabe)

    def test_diffusions_modelle_zaehlen_als_modell(self):
        with mit(self._krea()):
            alle = C.models()
        self.assertEqual(alle, {"moodyKrea2Mix_v70.safetensors": "diffusion"})

    def test_beide_bauarten_nebeneinander(self):
        with mit(self._krea(checkpoints=("ponyXL.safetensors",))):
            alle = C.models()
        self.assertEqual(alle["ponyXL.safetensors"], "checkpoint")
        self.assertEqual(alle["moodyKrea2Mix_v70.safetensors"], "diffusion")

    def test_pixel_space_ist_keine_vae_datei(self):
        with mit(self._krea()):
            self.assertEqual(C.vaes(), ["qwen_image_vae.safetensors"])

    def test_clip_typ_wird_aus_dem_namen_geraten(self):
        with mit(self._krea()):
            self.assertEqual(C.clip_typ_fuer("moodyKrea2Mix_v70.safetensors"), "krea2")
            self.assertEqual(C.clip_typ_fuer("flux2-dev.safetensors"), "flux2")
            self.assertEqual(C.clip_typ_fuer("irgendwas.safetensors"), "stable_diffusion")

    def test_config_schlaegt_die_ratung(self):
        with mit(self._krea()), \
             mock.patch.object(C, "_ccfg", lambda k, f: "qwen_image" if k == "cliptype" else f):
            self.assertEqual(C.clip_typ_fuer("moodyKrea2Mix_v70.safetensors"), "qwen_image")

    def test_getrennte_modelle_bekommen_eigene_vorgaben(self):
        # 14 Schritte, CFG 1, hochkant - dieselben Werte wie ueber Forge.
        V = C.vorgaben("diffusion")
        self.assertEqual(V["steps"], 14)
        self.assertEqual(V["cfg"], 1.0)
        self.assertEqual(V["size"], (832, 1216))
        self.assertEqual(V["sampler"], "euler_ancestral")
        self.assertEqual(V["scheduler"], "simple")
        self.assertEqual(V["neg"], "")          # bei CFG 1 wirkungslos
        self.assertEqual(C.vorgaben("checkpoint"), C.DEFAULTS)


class DerGeteilteAblaufplan(unittest.TestCase):
    def _plan(self, **kw):
        vorgabe = dict(unet="krea.safetensors", clip="qwen.safetensors",
                       clip_typ="krea2", vae="vae.safetensors", neg="",
                       steps=14, cfg=1.0, width=832, height=1216, seed=1,
                       sampler="euler_ancestral", scheduler="simple")
        vorgabe.update(kw)
        return C.workflow_geteilt("eine Frau", **vorgabe)

    def test_drei_lader_statt_einem_checkpoint(self):
        plan = self._plan()
        arten = {n["class_type"] for n in plan.values()}
        self.assertIn("UNETLoader", arten)
        self.assertIn("CLIPLoader", arten)
        self.assertIn("VAELoader", arten)
        self.assertNotIn("CheckpointLoaderSimple", arten)

    def test_die_verdrahtung_stimmt(self):
        plan = self._plan()
        self.assertEqual(plan["3"]["inputs"]["model"], ["4", 0])    # UNET
        self.assertEqual(plan["6"]["inputs"]["clip"], ["10", 0])    # CLIP
        self.assertEqual(plan["7"]["inputs"]["clip"], ["10", 0])
        self.assertEqual(plan["8"]["inputs"]["vae"], ["11", 0])     # VAE
        self.assertEqual(plan["8"]["inputs"]["samples"], ["3", 0])
        self.assertEqual(plan["9"]["inputs"]["images"], ["8", 0])

    def test_der_clip_typ_landet_im_plan(self):
        self.assertEqual(self._plan(clip_typ="krea2")["10"]["inputs"]["type"], "krea2")

    def test_dieselben_knoten_nummern_wie_beim_checkpoint_plan(self):
        # Damit die Metadaten fuer beide Plaene gleich ausgelesen werden koennen.
        geteilt, einfach = self._plan(), C.workflow(
            "x", ckpt="a", neg="", steps=1, cfg=1.0, width=8, height=8, seed=1,
            sampler="euler", scheduler="normal")
        for knoten in ("3", "5", "6", "7", "8", "9"):
            self.assertEqual(geteilt[knoten]["class_type"],
                             einfach[knoten]["class_type"], knoten)


class MalenMitGeteiltemModell(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._alt = SD.OUT_DIR
        SD.OUT_DIR = Path(self._tmp.name)
        self._daten = {}
        self._p1 = mock.patch.object(C.config, "load", lambda: dict(self._daten))
        self._p2 = mock.patch.object(C.config, "update", lambda **kw: self._daten.update(kw))
        self._p1.start(); self._p2.start()

    def tearDown(self):
        SD.OUT_DIR = self._alt
        self._p1.stop(); self._p2.stop()
        self._tmp.cleanup()

    def _krea(self):
        return FakeComfy(
            checkpoints=(), unets=("moodyKrea2Mix_v70.safetensors",),
            clips=("qwen3vl_4b_fp8_scaled.safetensors",),
            vaes=("qwen_image_vae.safetensors", "pixel_space"),
            history={"abc123": {"status": {"status_str": "success", "completed": True},
                                "outputs": {"9": {"images": [
                                    {"filename": "NemiCLI_1_.png", "subfolder": "",
                                     "type": "output"}]}}}})

    def test_krea_wird_mit_dem_geteilten_plan_gemalt(self):
        fake = self._krea()
        with mit(fake):
            pfad = C.generate("eine Frau im Wald", seed=99)
        self.assertTrue(Path(pfad).exists())
        plan = fake.geschickt
        self.assertEqual(plan["4"]["class_type"], "UNETLoader")
        self.assertEqual(plan["4"]["inputs"]["unet_name"], "moodyKrea2Mix_v70.safetensors")
        self.assertEqual(plan["10"]["inputs"]["clip_name"], "qwen3vl_4b_fp8_scaled.safetensors")
        self.assertEqual(plan["10"]["inputs"]["type"], "krea2")
        self.assertEqual(plan["11"]["inputs"]["vae_name"], "qwen_image_vae.safetensors")

    def test_krea_bekommt_die_erprobten_werte(self):
        fake = self._krea()
        with mit(fake):
            C.generate("eine Frau")
        plan = fake.geschickt
        self.assertEqual(plan["3"]["inputs"]["steps"], 14)
        self.assertEqual(plan["3"]["inputs"]["cfg"], 1.0)
        self.assertEqual(plan["5"]["inputs"]["width"], 832)
        self.assertEqual(plan["5"]["inputs"]["height"], 1216)
        self.assertEqual(plan["7"]["inputs"]["text"], "")      # CFG 1 -> kein Negativ

    def test_ohne_clip_kommt_ein_klarer_satz(self):
        fake = self._krea()
        fake.clips = []
        with mit(fake):
            with self.assertRaises(RuntimeError) as e:
                C.generate("eine Frau")
        self.assertIn("CLIP", str(e.exception))

    def test_ohne_vae_kommt_ein_klarer_satz(self):
        fake = self._krea()
        fake.vaes = ["pixel_space"]          # keine echte Datei
        with mit(fake):
            with self.assertRaises(RuntimeError) as e:
                C.generate("eine Frau")
        self.assertIn("VAE", str(e.exception))

    def test_ein_checkpoint_nimmt_weiter_den_einfachen_plan(self):
        fake = self._krea()
        fake.unets = []
        fake.checkpoints = ["ponyXL.safetensors"]
        with mit(fake):
            C.generate("ein Fuchs")
        self.assertEqual(fake.geschickt["4"]["class_type"], "CheckpointLoaderSimple")

    def test_das_menue_unterscheidet_die_bauarten(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("ComfyUI · geteilt", code)

class EigeneStelleNebenForge(unittest.TestCase):
    """ComfyUI ist ein dritter Motor – gleichberechtigt, nicht hineingebogen."""

    def test_imagegen_kennt_drei_motoren(self):
        import imagegen
        self.assertEqual(set(imagegen._EXTERN), {"webui", "comfy"})
        self.assertEqual(imagegen._EXTERN["comfy"], "comfyui")

    def test_gleiche_aufrufform_wie_forge(self):
        import inspect
        eigen = set(inspect.signature(C.generate).parameters)
        forge = set(inspect.signature(SD.generate).parameters)
        self.assertEqual(eigen, forge)

    def test_eigene_config_schluessel(self):
        # Nicht die von Forge mitbenutzen - sonst reissen sie sich die Adresse weg.
        self.assertEqual(C._CONFIG_KEY_HOST, "bild_comfy_host")
        self.assertEqual(C._CONFIG_KEY_MODEL, "bild_comfy_model")
        self.assertNotEqual(C._CONFIG_KEY_HOST, SD._CONFIG_KEY_HOST)

    def test_bildmodel_menue_kennt_comfyui(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("import imagegen, sdwebui, comfyui", code)
        self.assertIn('config.update(bild_backend="comfy")', code)
        self.assertIn("__chost__", code)

    def test_selbsttest_prueft_das_modul_mit(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"sdwebui", "comfyui"', code)


if __name__ == "__main__":
    unittest.main()
