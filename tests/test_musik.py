"""Musik-Motor: Wunsch-Übersetzung, Eingabeformat, Zeitplan, WAV, Modellprüfung, VAE-Längen, Werkzeug."""
import json
import os
import sys
import tempfile
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

from musik import gewichte, motor, netze, wunsch  # noqa: E402


class Wunsch(unittest.TestCase):
    def test_json_mit_text_drumherum(self):
        antwort = ('<think>überlege</think>Hier: {"titel": "Meer", "stil": "upbeat pop, female vocals", '
                   '"text": "[Verse]\\nWellen", "sprache": "DE", "bpm": "118", "tonart": "A minor", '
                   '"sekunden": 500} Viel Spaß!')
        e = wunsch.lesen(antwort)
        self.assertEqual(e["stil"], "upbeat pop, female vocals")
        self.assertEqual(e["text"], "[Verse]\nWellen")
        self.assertEqual((e["sprache"], e["bpm"], e["sekunden"]), ("de", 118, wunsch.MAX_SEKUNDEN))

    def test_ohne_gesang_instrumental(self):
        e = wunsch.lesen('{"stil": "lofi", "text": "[Verse]\\nla"}', gesang=False, sekunden=45)
        self.assertEqual(e["text"], wunsch.INSTRUMENTAL)
        self.assertEqual(e["sekunden"], 45)

    def test_stil_nur_lateinisch(self):
        fremd = chr(0x97F3) + chr(0x4E50)
        e = wunsch.lesen(json.dumps({"stil": f"dream pop {fremd}, café"}))
        self.assertEqual(e["stil"], "dream pop , café")

    def test_ohne_json_oder_stil(self):
        with self.assertRaises(ValueError):
            wunsch.lesen("Ich mache dir gern einen Song!")
        with self.assertRaises(ValueError):
            wunsch.lesen('{"text": "[Verse]"}')

    def test_anfrage_mit_entwurf(self):
        a = wunsch.anfrage("traurig", 60, False, {"stil": "piano"})
        self.assertIn("Gesang: nein", a)
        self.assertIn('"stil": "piano"', a)


class Eingabe(unittest.TestCase):
    def test_prompt_format(self):
        t, l = motor.prompts("rock", "[Verse]\nja", 30, bpm=120, sprache="de")
        self.assertEqual(t, "# Instruction\n" + motor.ANWEISUNG + "\n\n# Caption\nrock\n\n# Metas\n- bpm: 120\n"
                            "- timesignature: N/A\n- keyscale: N/A\n- duration: 30 seconds\n<|endoftext|>\n")
        self.assertEqual(l, "# Languages\nde\n\n# Lyric\n[Verse]\nja<|endoftext|>")

    def test_kodieren_sondertoken(self):
        class Tok:
            def encode(self, s):
                return [len(s)]
        self.assertEqual(motor.kodieren(Tok(), "ab<|endoftext|>\n"), [2, motor.ENDE_ID, 1, motor.ENDE_ID])

    def test_zeitplan_und_laenge(self):
        plan = motor.zeitplan()
        self.assertEqual(len(plan), 8)
        self.assertAlmostEqual(plan[0], 1.0)
        self.assertAlmostEqual(plan[-1], 0.3)
        self.assertTrue(all(a > b for a, b in zip(plan, plan[1:])))
        self.assertEqual(motor.latente(30), 750)
        self.assertEqual(motor.latente(10.1) % 2, 0)


class Wav(unittest.TestCase):
    def test_stereo_und_spitze(self):
        with tempfile.TemporaryDirectory() as t:
            ton = np.zeros((2, 4800), np.float32)
            ton[0, 10] = 2.0
            p = motor.wav_schreiben(ton, Path(t) / "a" / "s.wav")
            with wave.open(str(p)) as w:
                self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()), (2, 48000, 4800))
                d = np.frombuffer(w.readframes(4800), "<i2").reshape(-1, 2)
            self.assertLessEqual(int(d.max()), 32767)
            self.assertGreater(int(d[10, 0]), 32000)


class Erkennbar(unittest.TestCase):
    def test_titel_vorschlag(self):
        text = "[Verse]" + chr(10) + "Die Sonne steht hoch" + chr(10) + "[Chorus]" + chr(10) + "Sommer, bleib bei mir!"
        self.assertEqual(wunsch.titel_vorschlag(text), "Sommer, bleib bei mir")
        self.assertEqual(wunsch.titel_vorschlag("[Verse]" + chr(10) + "Nur eine Zeile."), "Nur eine Zeile")
        self.assertEqual(wunsch.titel_vorschlag("", "dark synthwave, driving bass, neon"), "Dark Synthwave Driving Bass")

    def test_titel_im_dateinamen(self):
        import profilordner
        with tempfile.TemporaryDirectory() as t, mock.patch.object(profilordner, "ordner", lambda u="": Path(t)):
            p = profilordner.musik_pfad(titel='Sommer: am "Meer"?')
            self.assertTrue(p.name.startswith("Sommer am Meer_"), p.name)
            p.write_bytes(b"")
            self.assertNotEqual(profilordner.musik_pfad(titel='Sommer: am "Meer"?'), p)
            self.assertNotIn("Sommer", profilordner.musik_pfad().name)

    def test_info_in_der_wav(self):
        with tempfile.TemporaryDirectory() as t:
            p = motor.wav_schreiben(np.zeros((2, 480), np.float32), Path(t) / "s.wav",
                                    {"INAM": "Größer als wir", "IART": "Ada", "ICMT": ""})
            with wave.open(str(p)) as w:
                self.assertEqual(w.getnframes(), 480)
            roh = p.read_bytes()
            self.assertEqual(int.from_bytes(roh[4:8], "little"), len(roh) - 8)
            self.assertIn("Größer als wir".encode("cp1252"), roh)
            self.assertIn(b"IART", roh)
            self.assertNotIn(b"ICMT", roh)


class StilleAmEnde(unittest.TestCase):
    def test_lange_stille_weg_kurze_bleibt(self):
        sr = motor.ABTASTRATE
        laut = (np.sin(np.arange(3 * sr) / 20) * 0.3).astype(np.float32)
        ton = np.stack([np.concatenate([laut, np.zeros(5 * sr, np.float32)])] * 2)
        kurz = motor.stille_kuerzen(ton)
        self.assertAlmostEqual(kurz.shape[-1] / sr, 4.0, delta=0.06)              # 3 s + 1 s Ausklang
        self.assertEqual(float(np.abs(kurz[:, -1]).max()), 0.0)
        ton2 = np.stack([np.concatenate([laut, np.zeros(sr, np.float32)])] * 2)
        self.assertEqual(motor.stille_kuerzen(ton2).shape, ton2.shape)
        self.assertEqual(motor.stille_kuerzen(np.zeros((2, 4 * sr), np.float32)).shape, (2, 4 * sr))


class Modellpruefung(unittest.TestCase):
    def test_nur_gelistete_und_merker(self):
        with tempfile.TemporaryDirectory() as t:
            o = Path(t)
            (o / "acestep-v15-turbo-Q8_0.gguf").write_bytes(b"dit")
            (o / "acestep-v15-fremd.gguf").write_bytes(b"x")
            soll = gewichte.sha256(o / "acestep-v15-turbo-Q8_0.gguf")
            with mock.patch.object(gewichte, "ORDNER", [o]), \
                    mock.patch.dict(gewichte.SHA256, {"acestep-v15-turbo-Q8_0.gguf": soll}):
                self.assertEqual(gewichte.dateien(), {"dit": o / "acestep-v15-turbo-Q8_0.gguf"})
                self.assertIn("vae-BF16.gguf", gewichte.fehlt())
                gewichte.pruefen(o / "acestep-v15-turbo-Q8_0.gguf")
                with mock.patch.object(gewichte, "sha256", side_effect=AssertionError("neu gerechnet")):
                    gewichte.pruefen(o / "acestep-v15-turbo-Q8_0.gguf")
            with mock.patch.dict(gewichte.SHA256, {"acestep-v15-turbo-Q8_0.gguf": "0" * 64}):
                with self.assertRaises(RuntimeError):
                    gewichte.pruefen(o / "acestep-v15-turbo-Q8_0.gguf")


class _FalscheDatei:
    """Liefert für jeden Namen einen Zufallstensor der passenden Form (kleine Kanäle)."""

    def __init__(self, formen):
        self.formen = formen

    def tensor(self, name, geraet="cpu", dtype=torch.float32):
        return torch.randn(self.formen(name), dtype=dtype) * 0.05


class Netze(unittest.TestCase):
    def test_vae_laenge(self):
        kanaele = [32, 16, 8, 8, 4, 4]

        def formen(name):
            teile = name.split(".")
            if name.startswith("decoder.conv1"):
                return {"weight_g": (32, 1, 1), "weight_v": (32, 64, 7), "bias": (32,)}[teile[-1]]
            if name.startswith("decoder.conv2"):
                return {"weight_g": (2, 1, 1), "weight_v": (2, 4, 7)}[teile[-1]]
            if name.startswith("decoder.snake1"):
                return (1, 4, 1)
            i = int(teile[2])
            ein, aus = kanaele[i], kanaele[i + 1]
            s = netze.VAEDecoder.SCHRITTE[i]
            if teile[3] == "snake1":
                return (1, ein, 1)
            if teile[3] == "conv_t1":
                return {"weight_g": (ein, 1, 1), "weight_v": (ein, aus, 2 * s), "bias": (aus,)}[teile[-1]]
            if teile[4].startswith("snake"):
                return (1, aus, 1)
            k = 7 if teile[4] == "conv1" else 1
            return {"weight_g": (aus, 1, 1), "weight_v": (aus, aus, k), "bias": (aus,)}[teile[-1]]
        vae = netze.VAEDecoder(_FalscheDatei(formen), "cpu")
        z = torch.randn(64, 12)
        self.assertEqual(tuple(vae(z[None]).shape), (1, 2, 12 * 1920))
        self.assertEqual(tuple(vae.dekodieren(z, stueck=5, ueberlapp=2).shape), (2, 12 * 1920))

    def test_fenster_und_rope(self):
        m = netze.fenster_maske(300, "cpu")
        self.assertTrue(m[0, 128] and not m[0, 129] and m[299, 171])
        cos, sin = netze.rope_tabelle(4, 8, "cpu", torch.float32)
        x = torch.randn(1, 4, 2, 8)
        self.assertTrue(torch.allclose(netze._rope(x, cos, sin)[:, 0], x[:, 0]))


class Werkzeug(unittest.TestCase):
    def test_eingetragen(self):
        import actions
        import hintergrund
        import notstopp
        import persona
        self.assertTrue(actions.ACTIONS["musik_machen"].get("hintergrund"))
        self.assertIn("musik_machen", hintergrund.GPU_WERKZEUGE)
        self.assertIn("musik_machen", notstopp.ZU_ENDE)
        self.assertIn("- musik_machen", persona.base_prompt())

    def test_ohne_stil_und_ohne_modelle(self):
        import actions
        r = actions._musik_machen({})
        self.assertFalse(r.ok)
        self.assertIn("stil", r.text)
        falsch = mock.Mock(fehlt=lambda: "Es fehlen unter Models/Musik: vae-BF16.gguf")
        with mock.patch.object(actions, "_musik_motor", return_value=falsch):
            r = actions._musik_machen({"stil": "pop"})
        self.assertFalse(r.ok)
        self.assertIn("vae-BF16.gguf", r.text)
        falsch.komponieren.assert_not_called()

    def test_andere_feldnamen(self):
        import asyncio
        import actions
        falsch = mock.Mock(fehlt=lambda: None, INSTRUMENTAL="[Instrumental]")
        falsch.komponieren.return_value = {"pfad": "x.wav", "sekunden": 30.0, "seed": 1, "sekunden_rechnen": 5.0}
        with mock.patch.object(actions, "_musik_motor", return_value=falsch), \
                mock.patch.object(actions.os, "startfile", create=True):
            r = asyncio.run(actions.run({"tool": "musik_machen", "prompt": "pop", "lyrics": "[Verse]\nja",
                                         "duration": 40, "language": "de"}))
        self.assertTrue(r.ok, r.text)
        args, kw = falsch.komponieren.call_args
        self.assertEqual(args[:3], ("pop", "[Verse]\nja", 40.0))
        self.assertEqual(kw["sprache"], "de")


class Zahlen(unittest.TestCase):
    def test_zahlen_wie_von_der_ki(self):
        import actions
        z = actions._zahl_aus
        self.assertEqual([z(120), z("120 BPM"), z("92,5"), z("3:00"), z("2 Minuten"), z("180 Sekunden")],
                         [120.0, 120.0, 92.5, 180.0, 120.0, 180.0])
        self.assertIsNone(z("zufällig"))
        self.assertIsNone(z(None))

    def test_werkzeug_mit_textzahlen(self):
        import asyncio
        import actions
        falsch = mock.Mock(fehlt=lambda: None, INSTRUMENTAL="[Instrumental]")
        falsch.komponieren.return_value = {"pfad": "x.wav", "sekunden": 30.0, "seed": 1, "sekunden_rechnen": 5.0}
        with mock.patch.object(actions, "_musik_motor", return_value=falsch), \
                mock.patch.object(actions.os, "startfile", create=True):
            r = asyncio.run(actions.run({"tool": "musik_machen", "stil": "pop", "bpm": "120 BPM",
                                         "sekunden": "3:00", "seed": "zufällig"}))
        self.assertTrue(r.ok, r.text)
        args, kw = falsch.komponieren.call_args
        self.assertEqual((args[2], kw["bpm"], kw["seed"]), (180.0, 120, None))


class Hintergrund(unittest.TestCase):
    def test_sperrt_sich_nicht_selbst(self):
        import time
        import hintergrund
        hintergrund._AUFGABEN[990] = {"nr": 990, "tool": "musik_machen", "titel": "Song", "start": time.time(),
                                      "ende": None}
        try:
            with mock.patch.object(motor, "fehlt", lambda: None), \
                    mock.patch.object(motor.gewichte, "dateien", side_effect=RuntimeError("weiter bis zum Motor")):
                with self.assertRaisesRegex(RuntimeError, "weiter bis zum Motor"):
                    motor.komponieren("pop", geraet="cpu")
        finally:
            hintergrund._AUFGABEN.pop(990, None)


class Planer(unittest.TestCase):
    def test_cot_wie_yaml(self):
        from musik import planer
        y = planer.cot("lofi " * 30, 60, bpm=85, tonart="A minor", sprache="de")
        zeilen = y.splitlines()
        self.assertEqual(zeilen[0], "bpm: 85")
        self.assertTrue(zeilen[1].startswith("caption: lofi"))
        self.assertTrue(zeilen[2].startswith("  lofi"))                     # Umbruch nach Spalte 80
        self.assertEqual(zeilen[-3:], ["duration: 60", "keyscale: A minor", "language: de"])

    def test_prompt_aufbau(self):
        from musik import planer
        bed, unbed = planer.prompt_ids(lambda t: [len(t)], "pop", "[Verse]", "bpm: 90\n")
        self.assertEqual(bed[0], planer.IM_START)
        self.assertIn(planer.THINK, bed)
        self.assertEqual(bed[-2], planer.THINK_END)
        self.assertEqual(unbed.count(planer.IM_START), 3)

    def test_fsq_stufen(self):
        from musik import planer
        w = planer.fsq_werte(torch.tensor([0, 1, 8, 65534]))
        self.assertEqual(w[0].tolist(), [-1.0] * 6)
        self.assertAlmostEqual(float(w[1, 0]), -1 + 1 / 3.5, places=5)
        self.assertAlmostEqual(float(w[2, 1]), -1 + 1 / 3.5, places=5)
        self.assertTrue(torch.all(w[3] <= 1.0))

    def test_cache_wie_ganz_neu_gerechnet(self):
        """Schritt für Schritt mit KV-Cache = jedes Mal die ganze Folge neu (kleines Zufallsmodell, CPU)."""
        from musik import planer
        torch.manual_seed(0)
        H, V = 64, planer.CODE_BASIS + planer.CODE_ZAHL

        def formen(name):
            n = name.split(".")[-2]
            return {"embed_tokens": (V, H), "norm": (H,), "input_layernorm": (H,),
                    "post_attention_layernorm": (H,), "q_proj": (2048, H), "k_proj": (1024, H),
                    "v_proj": (1024, H), "o_proj": (H, 2048), "q_norm": (128,), "k_norm": (128,),
                    "gate_proj": (96, H), "up_proj": (96, H), "down_proj": (H, 96)}[n]

        class Datei(_FalscheDatei):
            def meta(self, k, vorgabe=None):
                return 2
        pl = planer.Planer(Datei(formen), "cpu", torch.float32)
        for s in pl.schichten:
            for n in ("ln1", "ln2", "qn", "kn"):
                setattr(s, n, torch.ones_like(getattr(s, n)))
        pl.norm = torch.ones_like(pl.norm)
        bed, unbed = [1, 2, 3, 4, 5], [7, 8, 9]
        codes = pl.codes(bed, unbed, 6, seed=1, cfg=2.0)
        self.assertLessEqual(len(codes), 6)

        def ganz(folge_b, folge_u):
            zeilen = [folge_b, folge_u]
            L = max(map(len, zeilen))
            pol = torch.tensor([L - len(z) for z in zeilen])
            ids = torch.tensor([[0] * (L - len(z)) + z for z in zeilen])
            k = torch.arange(L)
            m = ((k[None] >= pol[:, None])[:, None, None] & (k[None, None] <= k[:, None])[:, None]) | \
                torch.eye(L, dtype=torch.bool)[None, None]
            cache = [(torch.zeros(2, 8, L, 128), torch.zeros(2, 8, L, 128)) for _ in pl.schichten]
            pl._rope = planer.rope_tabelle(L, 128, "cpu", torch.float32)
            lg = pl._vorwaerts(ids, cache, 0, pol, m)
            return lg[1] + 2.0 * (lg[0] - lg[1])
        gezogen = []
        alt = planer.top_p_ziehen

        def mitschreiben(lg, *a):
            gezogen.append(lg.clone())
            return alt(lg, *a)
        with mock.patch.object(planer, "top_p_ziehen", mitschreiben):
            codes2 = pl.codes(bed, unbed, 6, seed=1, cfg=2.0)
        self.assertEqual(codes, codes2)
        for i in range(1, len(gezogen)):
            dazu = [planer.CODE_BASIS + c for c in codes2[:i]]
            self.assertTrue(torch.allclose(gezogen[i], ganz(bed + dazu, unbed + dazu), atol=1e-4))

    def test_detokenizer_fuenf_je_code(self):
        from musik import planer

        def formen(name):
            n = name.split(".")[-2] if name.split(".")[-1] in ("weight", "bias") else name.split(".")[-1]
            if name.endswith("project_out.weight"):
                return (2048, 6)
            if name.endswith("project_out.bias") or name.endswith("embed_tokens.bias"):
                return (2048,)
            if name == "detokenizer.proj_out.weight":
                return (64, 2048)
            if name == "detokenizer.proj_out.bias":
                return (64,)
            return {"embed_tokens": (2048, 2048), "special_tokens": (5, 2048), "norm": (2048,),
                    "input_layernorm": (2048,), "post_attention_layernorm": (2048,), "q_proj": (2048, 2048),
                    "k_proj": (1024, 2048), "v_proj": (1024, 2048), "o_proj": (2048, 2048), "q_norm": (128,),
                    "k_norm": (128,), "gate_proj": (64, 2048), "up_proj": (64, 2048), "down_proj": (2048, 64)}[n]
        d = planer.Detokenizer(_FalscheDatei(formen), "cpu", torch.float32)
        self.assertEqual(tuple(d([1, 2, 3], stueck=2).shape), (15, 64))


class Fenster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(["test"])

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, auftrag):
                self.gesendet.append(auftrag)
        cls.Leitung = Leitung

    def test_entwurf_komponieren_fertig(self):
        import musik_fenster
        lt = self.Leitung()
        f = musik_fenster.MusikFenster(lt)
        self.assertEqual(lt.gesendet, [{"op": "songs"}])
        f.wunsch.setPlainText("Sommer am Meer")
        f.ausarbeiten()
        self.assertEqual(lt.gesendet[-1]["op"], "ausarbeiten")
        self.assertFalse(f.komponieren_k.isEnabled())
        lt.ereignis.emit({"t": "entwurf", "titel": "Meer", "stil": "pop", "text": "[Verse]\nja", "bpm": 120,
                          "sprache": "de", "tonart": "", "sekunden": 40})
        self.assertTrue(f.komponieren_k.isEnabled())
        f.komponieren()
        a = lt.gesendet[-1]
        self.assertEqual((a["op"], a["stil"], a["bpm"], a["sekunden"], a["titel"]), ("komponieren", "pop", 120, 40, "Meer"))
        song = Path(tempfile.mkdtemp()) / "meer.wav"
        song.write_bytes(b"RIFF")
        with mock.patch.object(musik_fenster.QDesktopServices, "openUrl") as offen:
            lt.ereignis.emit({"t": "fertig", "pfad": str(song), "sekunden": 40, "rechnen": 6.1, "seed": 1})
        offen.assert_called_once()
        with mock.patch.object(musik_fenster.QDesktopServices, "openUrl") as offen:
            musik_fenster.MusikFenster._oeffnen(__file__)                       # keine Musik (.py): nie öffnen
        offen.assert_not_called()
        self.assertEqual(f.liste.count(), 1)
        f.gesang.setChecked(False)
        f.komponieren()
        self.assertEqual(lt.gesendet[-1]["text"], "[Instrumental]")
        f.deleteLater()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    unittest.main()
