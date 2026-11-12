"""Selbstdiagnose: alle Abschnitte, ein kaputter Abschnitt nimmt die anderen nicht mit, keine Config-Werte im
Text, Werkzeug nur für Cloud-Modelle, Systemcheck kennt die GGUF-Modelle."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import debugbericht as D          # noqa: E402
import selbstdiagnose as S        # noqa: E402


def _abschnitte(**ersatz):
    """ABSCHNITTE mit festen Zeilen statt echter Abfragen (schnell, ohne Rechner-Abhängigkeit)."""
    return tuple((titel, ersatz.get(titel, lambda t=titel: [f"- Inhalt {t}"])) for titel, _ in S.ABSCHNITTE)


class Bericht(unittest.TestCase):
    def test_alle_abschnitte_und_kaputter_abschnitt(self):
        def kaputt():
            raise RuntimeError("Treiber antwortet nicht")
        with mock.patch.object(S, "ABSCHNITTE", _abschnitte(Rechner=kaputt)):
            text = S.bericht()
        for titel, _ in S.ABSCHNITTE:
            self.assertIn(f"## {titel}", text)
        self.assertIn("nicht ermittelbar: RuntimeError: Treiber antwortet nicht", text)
        self.assertIn("- Inhalt Systemcheck", text)                    # die anderen Abschnitte bleiben

    def test_programm_ohne_config_werte(self):
        cfg = {"model": "anthropic:claude-test", "api_key": "GEHEIM-123", "openai_key": "sk-GEHEIM"}
        with mock.patch("config.load", return_value=cfg):
            text = "\n".join(S._programm())
        self.assertIn("anthropic:claude-test", text)
        self.assertNotIn("GEHEIM", text)

    def test_fehler_bei_abgeschaltetem_bericht(self):
        with mock.patch.object(D, "an", return_value=False):
            self.assertIn("/debug an", "\n".join(S._fehler()))

    def test_echter_lauf(self):
        """Ein Durchlauf auf diesem Rechner: jeder Abschnitt liefert etwas, keiner scheitert."""
        text = S.bericht()
        self.assertNotIn("nicht ermittelbar", text)
        self.assertIn("Version:", text)


class Werkzeug(unittest.TestCase):
    def test_nur_cloud(self):
        import actions
        import modes
        import persona
        self.assertIn("selbstdiagnose", actions.ACTIONS)
        self.assertIn("selbstdiagnose", modes.READ_TOOLS)
        with mock.patch.object(D, "cloud_modell", return_value=False), \
                mock.patch.object(S, "bericht", return_value="# Selbstdiagnose x"):
            r = actions._selbstdiagnose({})
            self.assertFalse(r.ok)
            self.assertIn("Cloud-Modell", r.text)
            self.assertEqual(persona._diagnose_hinweis(), "")
        with mock.patch.object(D, "cloud_modell", return_value=True), \
                mock.patch.object(S, "bericht", return_value="# Selbstdiagnose x\n## Programm\n- Version: 9"):
            text = actions._selbstdiagnose({})
            self.assertIn("selbstdiagnose", persona._diagnose_hinweis())
        self.assertIsInstance(text, str)
        self.assertIn("- Version: 9", text)
        self.assertIn("Selbstdiagnose", text)                           # als Fremd-Inhalt gerahmt


class Systemcheck(unittest.TestCase):
    def _rep(self, gguf, ollama=False):
        return {"stock": {"ollama": ollama, "gguf": gguf, "bildmodelle": 1, "bild_ordner": "x", "frei_gb": 500},
                "torch": {"da": True, "gpu_nutzbar": True}, "gpu": {}, "transformers_ok": True, "torch_passt": True}

    def test_gguf_modelle_zaehlen_als_lokales_modell(self):
        import syscheck
        self.assertFalse(any("lokales Modell" in t for t in syscheck.todo(self._rep(gguf=2))))
        self.assertFalse(any("lokales Modell" in t for t in syscheck.todo(self._rep(gguf=0, ollama=True))))
        self.assertTrue(any("ModelGGUF" in t for t in syscheck.todo(self._rep(gguf=0))))


if __name__ == "__main__":
    unittest.main()
