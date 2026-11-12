"""Adaptives Schärfen (CAS) für Videos: Stufen, Wirkung, nur die MP4-Kopie wird geschärft.

Läuft auf dem Prozessor (geraet="cpu") – die Grafikkarte bleibt frei.
python -m unittest tests.test_schaerfe
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import numpy as np    # noqa: E402

import schaerfe as S  # noqa: E402


def _weiche_kante(t=2, h=16, w=32):
    """Weicher Übergang dunkel → hell, wie ein leicht unscharfes Video."""
    zeile = np.clip((np.arange(w) - w / 2) * 20 + 128, 40, 215).astype(np.uint8)
    bild = np.repeat(np.repeat(zeile[None, :, None], h, 0), 3, 2)
    return np.repeat(bild[None], t, 0)


class Schaerfen(unittest.TestCase):
    def test_stufen(self):
        self.assertEqual(S.stufe("Mittel"), "mittel")
        self.assertEqual(S.stufe("strong"), "stark")
        self.assertEqual(S.stufe("keine"), "aus")
        self.assertIsNone(S.stufe("ultra"))
        self.assertEqual(list(S.STUFEN), ["aus", "leicht", "mittel", "stark"])

    def test_aus_laesst_alles(self):
        b = _weiche_kante()
        self.assertIs(S.schaerfen(b, "aus", geraet="cpu"), b)

    def test_glatte_flaeche_bleibt(self):
        b = np.full((2, 8, 8, 3), 120, np.uint8)
        self.assertTrue(np.array_equal(S.schaerfen(b, "stark", geraet="cpu"), b))

    def test_kante_wird_steiler_staerker_je_stufe(self):
        b = _weiche_kante()
        steil = lambda x: int(np.abs(np.diff(x[0, 8, :, 0].astype(int))).max())
        werte = [steil(S.schaerfen(b, s, geraet="cpu")) for s in ("aus", "leicht", "mittel", "stark")]
        self.assertEqual(werte, sorted(werte))
        self.assertGreater(werte[-1], werte[0])
        r = S.schaerfen(b, "stark", geraet="cpu")
        self.assertEqual((r.shape, r.dtype), (b.shape, np.uint8))

    def test_nur_mp4_geschaerft_png_bleibt_wie_es_war(self):
        import video
        b = _weiche_kante(t=3)
        gefangen = {}

        def mp4(bilder, pfad, fps, rate=1.0):
            gefangen.update(bilder=bilder.copy(), rate=rate)
        echt = S.schaerfen
        with tempfile.TemporaryDirectory() as t, mock.patch.object(video, "_mp4", mp4), \
                mock.patch.object(S, "schaerfen", lambda bilder, art: echt(bilder, art, geraet="cpu")):
            out = Path(t) / "v.mp4"
            art = video.ausgeben(b, out, 24, "mittel")
            from PIL import Image
            png = np.asarray(Image.open(out.with_suffix(".png")))
        self.assertEqual(art, "mittel")
        self.assertFalse(np.array_equal(gefangen["bilder"], b))          # MP4 geschärft
        self.assertTrue(np.array_equal(png, b[-1]))                      # Fortsetzungs-PNG unverändert
        self.assertEqual(gefangen["rate"], S.DATENRATE["mittel"])

    def test_werkzeug_prueft_die_stufe(self):
        import actions
        import video
        with mock.patch.object(video, "missing_reason", lambda *a: ""), \
                mock.patch.object(video, "generate", side_effect=AssertionError("darf nicht rechnen")):
            res = actions._video_erstellen({"prompt": "a fox", "schaerfe": "ultra"})
        self.assertFalse(res.ok)
        self.assertIn("leicht", res.text)


if __name__ == "__main__":
    unittest.main()
