"""Vision-Erkennung für Cloud-Modelle (providers.cloud_name_vision)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import providers as P   # noqa: E402


class CloudVisionTests(unittest.TestCase):
    def test_deepseek_v41_flash_is_vision(self):
        # DeepSeek V4.1 Flash ist nativ multimodal
        self.assertTrue(P.cloud_name_vision("deepseek-v4.1-flash"))

    def test_known_vision_families(self):
        for m in ("gpt-4o", "gpt-4.1-mini", "gemini-2.5-pro", "claude-opus-4-8",
                  "qwen3-vl-235b", "llama-4-scout", "pixtral-large", "grok-4"):
            self.assertTrue(P.cloud_name_vision(m), m)

    def test_text_only_models_are_not_vision(self):
        for m in ("deepseek-v3.1", "deepseek-chat", "kimi-k2.6", "gpt-oss-120b",
                  "llama-3.3-70b", "mistral-large"):
            self.assertFalse(P.cloud_name_vision(m), m)

    def test_empty(self):
        self.assertFalse(P.cloud_name_vision(""))
        self.assertFalse(P.cloud_name_vision(None))


if __name__ == "__main__":
    unittest.main()
