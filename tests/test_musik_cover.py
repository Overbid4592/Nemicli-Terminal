"""Musik-Cover: Audiodateien lesen, VAE-Encoder in Stücken, FSQ-Tokenizer, Vorlage finden, Werkzeug, Fenster."""
import asyncio
import os
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen
for d in ("", "core", "engines", "tools", "ui"):
    sys.path.insert(0, str(R / d))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from musik import motor, netze, planer, tondatei  # noqa: E402


def _wav_roh(pfad: Path, daten: bytes, art: int, kanaele: int, rate: int, bits: int) -> None:
    breite = bits // 8
    fmt = struct.pack("<HHIIHH", art, kanaele, rate, rate * kanaele * breite, kanaele * breite, bits)
    pfad.write_bytes(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(daten)) + b"WAVE"
                     + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(daten)) + daten)


class Tondatei(unittest.TestCase):
    def test_wav_formate(self):
        welle = (0.5 * np.sin(np.linspace(0, 40 * np.pi, 4800))).astype(np.float32)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            _wav_roh(p / "f32.wav", welle.astype("<f4").tobytes(), 3, 1, 48000, 32)
            i24 = (welle * 8388607).astype(np.int32)
            roh24 = np.stack([i24 & 255, (i24 >> 8) & 255, (i24 >> 16) & 255], -1).astype(np.uint8).tobytes()
            _wav_roh(p / "i24.wav", roh24, 1, 1, 48000, 24)
            with wave.open(str(p / "i16.wav"), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes((np.stack([welle, -welle], -1) * 32767).astype("<i2").tobytes())
            for name in ("f32.wav", "i24.wav"):
                x = tondatei.lesen(p / name)
                self.assertEqual(x.shape, (2, 4800), name)
                self.assertLess(np.abs(x[0] - welle).max(), 1e-3, name)
                self.assertTrue(np.array_equal(x[0], x[1]), name)          # Mono auf beide Seiten
            x = tondatei.lesen(p / "i16.wav")
            self.assertLess(np.abs(x[1] + welle).max(), 1e-3)

    def test_umrechnen_und_kuerzen(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "k.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(np.zeros(22050 * 3, "<i2").tobytes())
            self.assertEqual(tondatei.lesen(p).shape, (2, 48000 * 3))
            self.assertEqual(tondatei.lesen(p, max_sekunden=2).shape, (2, 48000 * 2))
            with self.assertRaises(FileNotFoundError):
                tondatei.lesen(Path(t) / "fehlt.wav")

    def test_info_aus_eigenem_song(self):
        with tempfile.TemporaryDirectory() as t:
            p = motor.wav_schreiben(np.zeros((2, 100), np.float32), Path(t) / "s.wav",
                                    {"INAM": "Wellen", "ICMT": "[Verse]\nja"})
            self.assertEqual(tondatei.info(p), {"INAM": "Wellen", "ICMT": "[Verse]\nja"})
        self.assertEqual(motor._titel_aus(Path("Wellen_2026-10-18_12-30_2.wav"), {}), "Wellen")
        self.assertEqual(motor._titel_aus(Path("x.wav"), {"INAM": " Meer "}), "Meer")


class Encoder(unittest.TestCase):
    def test_stuecke_wie_am_stueck(self):
        """Ein rein örtliches Netz (Mittel je 1920 Proben): die Stücke ergeben genau das Ganze."""
        enc = netze.VAEEncoder.__new__(netze.VAEEncoder)
        enc.geraet = "cpu"
        mittel = lambda ton: ton.view(1, 2, -1, 1920).mean(-1).repeat(1, 32, 1)      # noqa: E731
        with mock.patch.object(netze.VAEEncoder, "__call__", lambda self, ton: mittel(ton)):
            ton = torch.randn(2, 1920 * 37 - 500)
            z = enc.kodieren(ton, stueck=10, ueberlapp=3)
            ganz = mittel(torch.nn.functional.pad(ton, (0, 500)))[0].T
        self.assertEqual(tuple(z.shape), (37, 64))
        self.assertTrue(torch.allclose(z, ganz))

    def test_netz_laenge(self):
        kanaele = [4, 4, 8, 8, 16, 32]

        def formen(name):
            teile = name.split(".")
            if name.startswith("encoder.conv1"):
                return {"weight_g": (4, 1, 1), "weight_v": (4, 2, 7), "bias": (4,)}[teile[-1]]
            if name.startswith("encoder.conv2"):
                return {"weight_g": (128, 1, 1), "weight_v": (128, 32, 3), "bias": (128,)}[teile[-1]]
            if name.startswith("encoder.snake1"):
                return (1, 32, 1)
            i = int(teile[2])
            ein, aus = kanaele[i], kanaele[i + 1]
            s = netze.VAEEncoder.SCHRITTE[i]
            if teile[3] == "snake1":
                return (1, ein, 1)
            if teile[3] == "conv1":
                return {"weight_g": (aus, 1, 1), "weight_v": (aus, ein, 2 * s), "bias": (aus,)}[teile[-1]]
            if teile[4].startswith("snake"):
                return (1, ein, 1)
            k = 7 if teile[4] == "conv1" else 1
            return {"weight_g": (ein, 1, 1), "weight_v": (ein, ein, k), "bias": (ein,)}[teile[-1]]

        class Datei:
            def tensor(self, name, geraet="cpu", dtype=torch.float32):
                return torch.randn(formen(name), dtype=dtype) * 0.05
        enc = netze.VAEEncoder(Datei(), "cpu")
        self.assertEqual(tuple(enc(torch.randn(1, 2, 1920 * 6)).shape), (1, 64, 6))
        self.assertEqual(tuple(enc.kodieren(torch.randn(2, 1920 * 6 - 7), stueck=4, ueberlapp=1).shape), (6, 64))


class Tokenizer(unittest.TestCase):
    def test_fsq_index_umkehrung(self):
        codes = torch.tensor([0, 1, 7, 8, 63, 511, 1000, 63999])
        werte = planer.fsq_werte(codes)
        roh = torch.atanh(werte.clamp(-0.999, 0.999))                  # tanh(roh) = werte
        self.assertEqual(planer.fsq_index(roh).tolist(), codes.tolist())

    def test_fuenf_bilder_je_code_rest_aus_stille(self):
        tok = planer.Tokenizer.__new__(planer.Tokenizer)
        H = 8
        tok.geraet, tok.dtype = "cpu", torch.float32
        tok.proj_w, tok.proj_b = torch.randn(H, 64), torch.zeros(H)
        tok.emb_w, tok.emb_b = torch.eye(H), torch.zeros(H)
        tok.cls, tok.norm, tok.schichten = torch.randn(H), torch.ones(H), []
        tok.fsq_w, tok.fsq_b = torch.randn(6, H), torch.zeros(6)
        lat, stille = torch.randn(12, 64), torch.randn(20, 64)
        codes = tok(lat, stille, stueck=2)
        self.assertEqual(len(codes), 3)
        self.assertTrue(all(0 <= c < 64000 for c in codes))
        self.assertEqual(tok(torch.cat([lat, stille[:3]]), stille), codes)    # Rest = Stille


class Vorlage(unittest.TestCase):
    def test_letzter_teil_des_titels_und_pfad(self):
        import actions
        import profilordner
        with tempfile.TemporaryDirectory() as t:
            o = Path(t)
            alt, neu = o / "Meer_2026-10-01_10-00.wav", o / "Sonne_2026-10-02_10-00.wav"
            for i, f in enumerate((alt, neu)):
                f.write_bytes(b"RIFF")
                os.utime(f, (time.time() + i, time.time() + i))
            with mock.patch.object(profilordner, "ordner", lambda *a, **k: o):
                self.assertEqual(actions._musik_vorlage("letzter"), neu)
                self.assertEqual(actions._musik_vorlage("meer"), alt)
                self.assertIn("Kein Song", actions._musik_vorlage("Regen"))
                self.assertIn("fehlt", actions._musik_vorlage(""))
                self.assertIn("nicht gefunden", actions._musik_vorlage(str(o / "weg.mp3")))
                self.assertEqual(actions._musik_vorlage(str(alt)), alt)


class Werkzeug(unittest.TestCase):
    def test_eingetragen(self):
        import actions
        import hintergrund
        import notstopp
        import persona
        self.assertTrue(actions.ACTIONS["musik_cover"].get("hintergrund"))
        self.assertIn("musik_cover", hintergrund.GPU_WERKZEUGE)
        self.assertIn("musik_cover", notstopp.ZU_ENDE)
        self.assertIn("- musik_cover", persona.base_prompt())

    def test_naehe_und_felder(self):
        import actions
        falsch = mock.Mock(fehlt=lambda: None)
        falsch.covern.return_value = {"pfad": "c.wav", "titel": "Meer (Cover)", "sekunden": 30.0, "seed": 3,
                                      "sekunden_rechnen": 6.0, "vorlage": "Meer.wav", "naehe": 0.3}
        with tempfile.TemporaryDirectory() as t:
            vorlage = Path(t) / "Meer.wav"
            vorlage.write_bytes(b"RIFF")
            for roh, soll in (("nah", 0.3), (40, 0.4), ("0.2", 0.2), (None, 0.0)):
                with mock.patch.object(actions, "_musik_motor", return_value=falsch), \
                        mock.patch.object(actions, "_read_guard", return_value=None), \
                        mock.patch.object(actions.os, "startfile", create=True):
                    r = asyncio.run(actions.run({"tool": "musik_cover", "quelle": str(vorlage),
                                                 "style": "acoustic folk", "naehe": roh}))
                self.assertTrue(r.ok, r.text)
                args, kw = falsch.covern.call_args
                self.assertEqual((args[0], args[1], args[2]), (vorlage, "acoustic folk", None))
                self.assertAlmostEqual(kw["naehe"], soll)
            r = actions._musik_cover({"vorlage": str(vorlage)})
            self.assertFalse(r.ok)
            self.assertIn("stil", r.text)

    def test_zu_kurze_vorlage(self):
        with tempfile.TemporaryDirectory() as t:
            p = motor.wav_schreiben(np.zeros((2, 48000 * 3), np.float32), Path(t) / "kurz.wav")
            with mock.patch.object(motor, "fehlt", lambda: None), self.assertRaises(ValueError):
                motor.covern(p, "pop")


class Fenster(unittest.TestCase):
    def test_cover_knopf(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(["test"])
        import musik_fenster

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, auftrag):
                self.gesendet.append(auftrag)
        lt = Leitung()
        f = musik_fenster.MusikFenster(lt)
        f.covern(None)
        self.assertEqual(lt.gesendet, [{"op": "songs"}])                    # nichts markiert
        lt.ereignis.emit({"t": "songs", "liste": [__file__]})
        f.liste.setCurrentRow(0)
        f.covern(None)
        self.assertEqual(len(lt.gesendet), 1)                               # kein Stil
        f.stil.setPlainText("jazz trio")
        f.naehe.setValue(30)
        f.covern(None)
        a = lt.gesendet[-1]
        self.assertEqual((a["op"], a["vorlage"], a["stil"], a["naehe"], a["text"]),
                         ("covern", __file__, "jazz trio", 0.3, None))
        self.assertFalse(f.cover_k.isEnabled())
        f.deleteLater()


if __name__ == "__main__":
    unittest.main()
