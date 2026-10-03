"""Anleitung für bild_malen: jeder Bild-Motor bekommt nur seine eigene Prompt-Art.

python -m unittest tests.test_bildprompt
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import imagegen                                        # noqa: E402
import persona                                         # noqa: E402


class BildPromptTests(unittest.TestCase):

    def setUp(self):
        persona._BILD_CACHE.update(ref=None, text="")

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

    def test_stable_diffusion_mit_tags_und_neg(self):
        with patch.object(imagegen, "backend", return_value="builtin"), \
                patch.object(imagegen, "resolve", return_value=NS(ref="beispielXL", kind="sdxl")):
            text = persona._bild_hinweis()
        self.assertIn('"prompt": "masterpiece', text)
        self.assertIn('"neg"', text)
        self.assertNotIn("ganzen Sätzen", text)


if __name__ == "__main__":
    unittest.main()
