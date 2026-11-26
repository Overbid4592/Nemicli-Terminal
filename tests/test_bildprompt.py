"""Anleitung für bild_malen: jeder Bild-Motor bekommt nur seine eigene Prompt-Art.

python -m unittest tests.test_bildprompt
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import imagegen                                        # noqa: E402
import persona                                         # noqa: E402


class BildPromptTests(unittest.TestCase):

    def test_grundteil_ist_neutral(self):
        base = persona.base_prompt()
        self.assertIn("bild_malen", base)
        for wort in ("masterpiece", "Stable Diffusion", "Qualitäts-Tags", '"neg"'):
            self.assertNotIn(wort, base)

    def test_krea_nur_saetze(self):
        with patch.object(imagegen, "backend", return_value="krea"):
            text = persona._bild_hinweis()
        self.assertIn("ganzen Sätzen", text)
        self.assertIn('"tool": "bild_malen"', text)
        self.assertNotIn('"masterpiece', text)
        self.assertNotIn('"neg"', text)

    def test_webui_nur_saetze(self):
        import sdwebui
        with patch.object(imagegen, "backend", return_value="webui"), \
                patch.object(sdwebui, "chosen_model", return_value="krea2"):
            text = persona._bild_hinweis()
        self.assertIn('"tool": "bild_malen"', text)
        self.assertNotIn('"masterpiece', text)
        self.assertNotIn('"neg"', text)

    def test_comfyui_eigener_hinweis(self):
        import comfyui
        with patch.object(imagegen, "backend", return_value="comfy"), \
                patch.object(comfyui, "chosen_model", return_value="beispiel"):
            text = persona._bild_hinweis()
        self.assertIn("ComfyUI", text)
        self.assertIn("beispiel", text)
        self.assertNotIn("masterpiece", text)


if __name__ == "__main__":
    unittest.main()
