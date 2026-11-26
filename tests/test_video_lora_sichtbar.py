"""Charakter-LoRA im Video sichtbar: Ergebnis nennt, was eingemischt wurde; mit fertiger LoRA braucht die
Persönlichkeit fürs eigene Aussehen kein Startbild (außer beim Turbo-Modell). Ohne Grafikkarte."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for d in ("core", "engines", "tools", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import persona       # noqa: E402
import profilordner  # noqa: E402
import video         # noqa: E402


class LoraStand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.turbo = False
        self.modell = "wan22-ti2v"
        for ziel, name, wert in ((profilordner, "wurzel", lambda: self.tmp / "Profile"),
                                 (profilordner, "aktiv", lambda: "Ada"),
                                 (video, "teile", lambda name=None: {"dit": Path(
                                     "wan_turbo.gguf" if self.turbo else "wan_ti2v.gguf")}),
                                 (video, "dit_config", lambda t: {}),
                                 (video, "familie", lambda cfg: self.modell)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        video._LORA_STAND.update(zeit=0.0, name=None, stand={})
        self.addCleanup(video._LORA_STAND.update, zeit=0.0, name=None, stand={})

    def _lora(self, beschreibung="Ada, short red hair, green eyes"):
        import torch
        from safetensors.torch import save_file
        ordner = self.tmp / "Profile" / "Ada" / "LoRA"
        ordner.mkdir(parents=True)
        save_file({"a": torch.zeros(1)}, str(ordner / "wan22-5b.safetensors"),
                  metadata={"beschreibung": beschreibung, "familie": "wan22-ti2v"})

    def test_ohne_lora_kein_hinweis(self):
        self.assertFalse(video.lora_stand()["da"])
        self.assertEqual(persona._video_lora_hinweis(), "")
        self.assertFalse((self.tmp / "Profile" / "Ada" / "LoRA").exists())    # nur gelesen, nichts angelegt

    def test_mit_lora_kein_startbild_noetig(self):
        self._lora()
        s = video.lora_stand()
        self.assertTrue(s["da"] and s["an"])
        self.assertEqual(s["beschreibung"], "Ada, short red hair, green eyes")
        h = persona._video_lora_hinweis()
        self.assertIn("kein Startbild", h)
        self.assertIn("„Ada, short red hair, green eyes“", h)
        self.assertNotIn("Turbo", h)
        self.assertIn("Eingemischt", h)

    def test_schluesselbilder_ohne_lora(self):
        self._lora()
        self.assertIn("Mit schluesselbilder wechselt der Motor auf Fun InP – die LoRA wirkt dort auch",
                      persona._video_lora_hinweis())

    def test_verwandtes_modell_gewaehlt(self):
        self._lora()
        self.modell = "wan22-fun-inp"
        s = video.lora_stand()
        self.assertTrue(s["passt"])                              # gleiche Grundlage: LoRA wirkt auch dort
        h = persona._video_lora_hinweis()
        self.assertIn("Trainiert ist sie für wan22-ti2v, das gewählte Modell ist wan22-fun-inp", h)
        self.assertIn("kein Startbild", h)

    def test_fremde_bauart_passt_nicht(self):
        self._lora()
        self.modell = "wan21-t2v"
        s = video.lora_stand()
        self.assertFalse(s["passt"])
        h = persona._video_lora_hinweis()
        self.assertIn("für wan22-ti2v trainiert, das gewählte Video-Modell ist wan21-t2v", h)
        self.assertIn("erst ein Startbild", h)

    def test_turbo_braucht_startbild(self):
        self._lora()
        self.turbo = True
        self.assertIn("Gittermuster, also erst bild_malen", persona._video_lora_hinweis())

    def test_ausgeschaltet(self):
        self._lora()
        with patch.object(video, "_cfg", lambda k, d=None: False if k == "video_lora" else d):
            self.assertIn("video_lora ist aus", persona._video_lora_hinweis())

    def test_kurz_gemerkt(self):
        self._lora()
        video.lora_stand()
        (self.tmp / "Profile" / "Ada" / "LoRA" / "wan22-5b.safetensors").unlink()
        self.assertTrue(video.lora_stand()["da"])                # innerhalb LORA_STAND_S kein neues Lesen
        video._LORA_STAND["zeit"] = 0.0
        self.assertFalse(video.lora_stand()["da"])

    def test_feste_anleitung_widerspricht_nicht(self):
        quelle = (ROOT / "core" / "persona.py").read_text(encoding="utf-8")
        self.assertIn("hast\n  du noch keine Charakter-LoRA", quelle)


class Ergebnis(unittest.TestCase):
    def test_eingemischt_steht_im_ergebnis(self):
        import actions
        import imagegen

        def generate(prompt, **kw):
            video.GENUTZT.append("Charakter-LoRA wan22-5b.safetensors (Stärke 1)")
            return "C:/x/v.mp4"
        with patch.object(video, "missing_reason", lambda *a: None), \
                patch.object(video, "modelle", lambda: {}), \
                patch.object(video, "generate", generate), \
                patch.object(imagegen, "_gpu_fuer_bild", lambda fn: fn), \
                patch.object(actions.os, "startfile", lambda p: None):
            video.GENUTZT.clear()
            text = actions._video_erstellen({"prompt": "a fox"})
        self.assertIn("🧬 Eingemischt: Charakter-LoRA wan22-5b.safetensors (Stärke 1).", text)
        quelle = main_quelle.text()
        self.assertIn('ui.info("🧬 Eingemischt: " + " · ".join(video.GENUTZT))', quelle)       # auch bei /video
        self.assertIn("video.lora_stand()", quelle)                                              # /video zeigt den Stand


if __name__ == "__main__":
    unittest.main()


class Wechsel(unittest.TestCase):
    def test_schluesselbilder_wechsel_wird_gemeldet(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        bilder = []
        for i in range(2):
            p = tmp / f"k{i}.png"
            Image.new("RGB", (64, 64)).save(p)
            bilder.append(str(p))
        teile = {"WAN-Turbo": {"name": "WAN-Turbo", "vorgaben": {}, "in": 48},
                 "WAN-FunInP": {"name": "WAN-FunInP", "vorgaben": {}, "in": 100}}

        def stopp(*a, **k):
            raise RuntimeError("stopp vor dem Laden")
        with patch.object(video, "missing_reason", lambda *a: None), \
                patch.object(video, "_mp4_weg", lambda: "windows"), \
                patch.object(video, "teile", lambda name=None: teile[name or "WAN-Turbo"]), \
                patch.object(video, "dit_config", lambda t: {"in_dim": t["in"]}), \
                patch.object(video, "_modell_mit_endbild", lambda: "WAN-FunInP"), \
                patch.object(video, "_lade_gguf_dit", stopp), patch.object(video, "_lade_safetensors", stopp):
            try:
                video.generate("a fox", sekunden=10, schluesselbilder=bilder)
            except Exception:
                pass
        self.assertTrue(any("WAN-FunInP" in h and "statt mit WAN-Turbo" in h for h in video.HINWEISE),
                        video.HINWEISE)


class Verwandt(unittest.TestCase):
    def test_lora_auf_verwandter_bauart(self):
        self.assertTrue(video.lora_passt_zu("wan22-ti2v", "wan22-fun-inp"))
        self.assertTrue(video.lora_passt_zu("wan22-fun-inp", "wan22-ti2v"))
        self.assertTrue(video.lora_passt_zu("", "wan22-ti2v"))                 # ältere LoRA ohne Angabe
        self.assertFalse(video.lora_passt_zu("wan21-t2v", "wan22-ti2v"))

    def test_fun_inp_ohne_startbild_nimmt_ti2v(self):
        teile = {"WAN-FunInP": {"name": "WAN-FunInP", "dit": Path("fun.gguf"), "vorgaben": {}, "in": 100},
                 "WAN-Turbo": {"name": "WAN-Turbo", "dit": Path("ti2v_turbo.gguf"), "vorgaben": {}, "in": 48},
                 "WAN-GGUF": {"name": "WAN-GGUF", "dit": Path("ti2v.gguf"), "vorgaben": {}, "in": 48}}

        def stopp(*a, **k):
            raise RuntimeError("stopp vor dem Laden")
        geladen = []
        with patch.object(video, "missing_reason", lambda *a: None),                 patch.object(video, "_mp4_weg", lambda: "windows"),                 patch.object(video, "modelle", lambda: teile),                 patch.object(video, "teile", lambda name=None: (geladen.append(name or "WAN-FunInP"),
                                                                teile[name or "WAN-FunInP"])[1]),                 patch.object(video, "dit_config", lambda t: {"in_dim": t["in"]}),                 patch.object(video, "_lade_gguf_dit", stopp), patch.object(video, "_lade_safetensors", stopp):
            try:
                video.generate("a fox", sekunden=5)
            except Exception:
                pass
        self.assertIn("WAN-GGUF", geladen)                                      # TI2V, Turbo nur als letztes
        self.assertNotIn("WAN-Turbo", geladen)
        self.assertTrue(any("Ohne Startbild rechnet dieses Video mit WAN-GGUF statt mit WAN-FunInP" in h
                            for h in video.HINWEISE), video.HINWEISE)
