"""Offline-Tests für den WebUI-Bild-Motor (engines/sdwebui.py) – ohne echtes HTTP."""

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import sdwebui as W   # noqa: E402


def _png_b64() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Minimaler httpx.Client-Ersatz mit vorbereiteten Antworten."""
    routes_get = {}
    posts = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path):
        return FakeResp(self.routes_get.get(path, {}))

    def post(self, path, json=None):
        FakeClient.posts.append((path, json))
        if path == "/sdapi/v1/txt2img":
            return FakeResp({"images": [_png_b64()],
                             "info": '{"seed": 4242}'})
        return FakeResp({}, 200)


class SdWebUITests(unittest.TestCase):
    def setUp(self):
        FakeClient.posts = []
        FakeClient.routes_get = {
            "/sdapi/v1/sd-models": [
                {"model_name": "moodyKrea2Mix_v70", "title": "moodyKrea2Mix_v70.safetensors [abc]"},
                {"model_name": "waiIllustrious", "title": "waiIllustrious.safetensors [def]"},
            ],
            "/sdapi/v1/options": {"sd_model_checkpoint": "moodyKrea2Mix_v70.safetensors [abc]"},
            "/sdapi/v1/progress": {"progress": 0.5, "state": {"sampling_step": 7, "sampling_steps": 14}},
        }
        self._patch = patch.object(W, "_client", lambda timeout: FakeClient())
        self._patch.start()
        self._tmp = tempfile.TemporaryDirectory()
        W.OUT_DIR = Path(self._tmp.name)

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_available_and_models(self):
        self.assertTrue(W.available())
        self.assertEqual(set(W.models()), {"moodyKrea2Mix_v70", "waiIllustrious"})
        self.assertEqual(W.current_model(), "moodyKrea2Mix_v70.safetensors [abc]")

    def test_resolve_title_partial(self):
        self.assertIn("moodyKrea2Mix", W._resolve_title("krea") or "")
        self.assertIn("wai", (W._resolve_title("waiIllustrious") or "").lower())

    def test_generate_uses_krea_defaults_and_saves(self):
        path = W.generate("a fox", model="moodyKrea2Mix_v70")
        self.assertTrue(Path(path).exists())
        # kein Modellwechsel nötig (schon geladen) -> nur txt2img gepostet
        posts = dict((p, j) for p, j in FakeClient.posts)
        self.assertIn("/sdapi/v1/txt2img", posts)
        payload = posts["/sdapi/v1/txt2img"]
        self.assertEqual(payload["cfg_scale"], 1.0)
        self.assertEqual(payload["steps"], 14)
        self.assertEqual(payload["sampler_name"], "Euler a")
        self.assertEqual(payload["scheduler"], "Automatic")
        self.assertEqual((payload["width"], payload["height"]), (832, 1216))
        self.assertIn("bad anatomy", payload["negative_prompt"])
        # Positiv-Vorsatz steht vorn, der eigene Prompt dahinter
        self.assertTrue(payload["prompt"].startswith("1girl, solo, woman, masterpiece"))
        self.assertTrue(payload["prompt"].endswith(", a fox"))
        # echter Seed aus info im Dateinamen
        self.assertRegex(Path(path).name, r"^[A-Za-z0-9_]+_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(_\d+)?\.(png|jpg)$")     # <Persönlichkeit>_Datum_Uhrzeit (Seed in den Metadaten)

    def test_generate_switches_model_when_different(self):
        W.generate("a fox", model="waiIllustrious")
        self.assertIn("/sdapi/v1/options", [p for p, _ in FakeClient.posts])

    def test_generate_size_als_text_aus_der_config(self):
        with patch.object(W, "_wcfg", lambda key, fb: "640x960" if key == "size" else fb):
            W.generate("a fox", model="moodyKrea2Mix_v70")
        payload = dict(FakeClient.posts)["/sdapi/v1/txt2img"]
        self.assertEqual((payload["width"], payload["height"]), (640, 960))

    def test_generate_without_prompt_raises(self):
        with self.assertRaises(RuntimeError):
            W.generate("   ")


if __name__ == "__main__":
    unittest.main()


class PngMetadataTests(unittest.TestCase):
    """Speichern ohne PIL: gültiges PNG + tEXt-Metadaten (reines Python)."""

    def test_png_text_chunk_roundtrip(self):
        import struct
        png = W._PNG_SIG + struct.pack(">I", 13) + b"IHDR" + b"\x00" * 13 + struct.pack(">I", 0)
        png += struct.pack(">I", 0) + b"IEND" + struct.pack(">I", 0)
        out = W._png_with_text(png, {"prompt": "ein fuchs", "leer": ""})
        self.assertTrue(out.startswith(W._PNG_SIG))
        # tEXt nach IHDR vorhanden, leerer Wert übersprungen
        self.assertIn(b"tEXtprompt\x00ein fuchs", out)
        self.assertNotIn(b"tEXtleer", out)
        # IHDR bleibt erster Chunk
        self.assertEqual(out[12:16], b"IHDR")

    def test_non_png_left_untouched(self):
        self.assertEqual(W._png_with_text(b"notapng", {"x": "y"}), b"notapng")


class MergePromptTests(unittest.TestCase):
    def test_sperrwoerter_und_doppelte(self):
        out = W.merge_prompt("masterpiece, red dress, 1boy, man, couple, group, hetero, smiling")
        self.assertNotIn("1boy", out)
        self.assertNotIn("hetero", out)
        self.assertNotIn("group", out)
        self.assertEqual(out.count("masterpiece"), 1)
        self.assertTrue(out.endswith("red dress, smiling"))
        self.assertIn("solo", out)
        self.assertEqual(W.stripped_tags("a, 1boy, man, b"), ["1boy", "man"])

    def test_ich_perspektive_ohne_kopf_und_gesicht(self):
        out = W.merge_prompt("red dress, male pov, POV shot, hetero, 1boy, man, group, smiling")
        teile = [t.strip() for t in out.split(",")]
        for bleibt in ("male pov", "POV shot", "hetero", "faceless male", "head out of frame"):
            self.assertIn(bleibt, teile)
        for weg in ("solo", "1boy", "man", "group"):              # keine ganze zweite Figur, keine Gruppe
            self.assertNotIn(weg, teile)
        self.assertEqual(W.stripped_tags("male pov, hetero, 1boy"), ["1boy"])

    def test_erkennt_die_ich_perspektive(self):
        self.assertTrue(W.ist_pov("POV shot, red dress"))
        self.assertTrue(W.ist_pov("first-person view"))
        self.assertFalse(W.ist_pov("red dress, smiling"))
        self.assertFalse(W.ist_pov(""))

    def test_vorsatz_abschaltbar(self):
        self.assertEqual(W.merge_prompt("x, y", positiv=""), "x, y")


if __name__ == "__main__":
    unittest.main()
