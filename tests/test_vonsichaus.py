"""Von sich aus: freier Moment mit Grafikspeicher-Regel (Bild nur mit mehr als 5 GB frei), Nachrichten für den
NemiCLI-Chat (Ablage, Abholen, Vorspann für die Antwort). Grafikkarte und Kugel sind Attrappen."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import modes                                           # noqa: E402
import vonsichaus as V                                 # noqa: E402

GB = 1024


class RegelTests(unittest.TestCase):
    def setUp(self):
        V.runde(False)
        self.addCleanup(V.runde, False)
        p = mock.patch.object(V, "bild_erlaubt", wraps=V.bild_erlaubt)
        p.start()
        self.addCleanup(p.stop)
        c = mock.patch("config.load", return_value={})               # lokal malen, nicht in der Cloud
        c.start()
        self.addCleanup(c.stop)

    def frei(self, mb):
        return mock.patch.object(V, "vram_frei_mb", return_value=mb)

    def test_grenze_5_gb(self):
        with self.frei(5 * GB + 1):
            self.assertEqual((True, ""), V.bild_erlaubt())
        with self.frei(5 * GB):
            ok, grund = V.bild_erlaubt()
            self.assertFalse(ok)
            self.assertIn("5,0 GB", grund)
        with self.frei(None):
            self.assertFalse(V.bild_erlaubt()[0])                    # nicht messbar: lieber nicht

    def test_nur_in_spontanen_runden(self):
        bild = {"tool": "bild_malen", "prompt": "x"}
        with self.frei(1 * GB):
            self.assertNotEqual("block", modes.decide(bild, False))   # normaler Auftrag: Regel gilt nicht
            V.runde(True)
            self.assertEqual("block", modes.decide(bild, False))
            self.assertIn("1,0 GB", modes.block_message(bild))
        with self.frei(8 * GB):
            self.assertNotEqual("block", modes.decide(bild, False))
            self.assertEqual("block", modes.decide({"tool": "video_erstellen", "prompt": "x"}, False))
            self.assertEqual("block", modes.decide({"tool": "bild_serie", "prompt": "x"}, False))
            self.assertNotEqual("block", modes.decide({"tool": "merken", "text": "x"}, False))

    def test_cloud_braucht_keinen_grafikspeicher(self):
        with self.frei(0), mock.patch("config.load", return_value={"bild_backend": "cloud"}), \
             mock.patch("videobildcloud.available", return_value=True):
            self.assertTrue(V.bild_erlaubt()[0])

    def test_hinweis_fuer_den_auftrag(self):
        with self.frei(7 * GB):
            self.assertIn("geht", V.hinweis())
        with self.frei(2 * GB):
            self.assertIn("kein Bild", V.hinweis())


class AblageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(V, "DATEI", Path(self._tmp.name) / ".nemicli.vonsichaus")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_schreiben_abholen_leert(self):
        V.schreiben("PersonA", "Schau mal aus dem Fenster!", [])
        V.schreiben("PersonA", "", ["C:/bilder/a.png"])
        V.schreiben("PersonA", "  ", [])                             # leer: nichts
        e = V.abholen()
        self.assertEqual(["Schau mal aus dem Fenster!", ""], [x["text"] for x in e])
        self.assertEqual(["C:/bilder/a.png"], e[1]["bilder"])
        self.assertEqual([], V.abholen())

    def test_alte_verfallen_und_hoechstens(self):
        with mock.patch.object(V.time, "time", return_value=time.time() - V.ALTER_S - 10):
            V.schreiben("PersonA", "von gestern", [])
        for i in range(V.HOECHSTENS + 5):
            V.schreiben("PersonB", f"n{i}", [])
        e = V.abholen()
        self.assertEqual(V.HOECHSTENS, len(e))
        self.assertNotIn("von gestern", [x["text"] for x in e])
        self.assertEqual(f"n{V.HOECHSTENS + 4}", e[-1]["text"])

    def test_vorspann_fuer_die_antwort(self):
        k = V.kontext([{"zeit": time.time(), "text": "Magst du Regen?", "bilder": ["C:/x/regen.png"]}])
        self.assertIn("„Magst du Regen? (dazu ein Bild: regen.png)“", k)
        self.assertEqual("", V.kontext([]))


if __name__ == "__main__":
    unittest.main()
