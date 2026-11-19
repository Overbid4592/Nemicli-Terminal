"""Medien im Gedächtnis ohne Modell: Ton-Merkmale, Video lesen, Titel und Kürzen im Einbetter, Einlesen von
Videos und Ton (nur Text), Werkzeug medien_suchen, Profil-Grenze.

python -m unittest tests.test_medien_gedaechtnis
"""
import json
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions      # noqa: E402
import embedder     # noqa: E402
import indexdb      # noqa: E402
import memory       # noqa: E402
import profilordner  # noqa: E402
from ggufengine import ton  # noqa: E402


def _wav(pfad: Path, sekunden: float = 1.0, info: dict | None = None) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(sekunden * 16000)) / 16000
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes())
    teile = b""
    for k, v in (info or {}).items():
        roh = v.encode("cp1252") + b"\0"
        teile += k.encode() + struct.pack("<I", len(roh)) + roh + (b"\0" if len(roh) % 2 else b"")
    if teile:
        with open(pfad, "r+b") as f:
            f.seek(0, 2)
            f.write(b"LIST" + struct.pack("<I", 4 + len(teile)) + b"INFO" + teile)
            groesse = f.tell() - 8
            f.seek(4)
            f.write(struct.pack("<I", groesse))


class TonMerkmale(unittest.TestCase):
    def test_form_maske_und_token(self):
        w = np.zeros(int(7.3 * 16000), np.float32)
        f, m = ton.merkmale(w)
        self.assertEqual((730, 128), f.shape)
        self.assertEqual(183, ton.token_zahl(int(m.sum())))          # 25 Token je Sekunde
        self.assertEqual(750, ton.token_zahl(ton.merkmale(np.zeros(31 * 16000, np.float32))[1].sum()))   # höchstens 30 s

    def test_stuecke_verteilt(self):
        lang = np.zeros(20 * 60 * 16000, np.float32)                  # 20 Minuten
        st = ton.stuecke(lang, 8)
        self.assertEqual(8, len(st))
        self.assertTrue(all(len(s) == ton.MAX_PROBEN for s in st))
        self.assertEqual(1, len(ton.stuecke(np.zeros(16000, np.float32), 8)))

    def test_gleich_wie_transformers(self):
        try:
            from transformers.models.gemma4.feature_extraction_gemma4 import Gemma4AudioFeatureExtractor
        except Exception:
            self.skipTest("transformers fehlt (requirements-dev.txt)")
        fe = Gemma4AudioFeatureExtractor(feature_size=128, sampling_rate=16000, frame_length_ms=20.0,
                                         hop_length_ms=10.0, max_frequency=8000.0, mel_floor=1e-3)
        rng = np.random.default_rng(0)
        w = (0.2 * rng.standard_normal(int(3.3 * 16000))).astype(np.float32)
        ref = fe([w], return_tensors="np")
        f, m = ton.merkmale(w)
        self.assertTrue((m == ref["input_features_mask"][0]).all())
        self.assertLess(float(np.abs(f - ref["input_features"][0]).max()), 1e-5)


class TonEncoder(unittest.TestCase):
    """Nachbau gegen transformers' Gemma4AudioModel mit Zufallsgewichten (kleine Form)."""

    def test_wie_transformers(self):
        try:
            import torch
            from transformers.models.gemma4.configuration_gemma4 import Gemma4AudioConfig
            from transformers.models.gemma4.modeling_gemma4 import Gemma4AudioModel
        except Exception:
            self.skipTest("transformers fehlt (requirements-dev.txt)")
        # erste Kanalzahl 128: transformers leitet daraus die Breite nach den Faltungen ab (128 Mel-Bänder / 4)
        c = dict(hidden_size=64, num_attention_heads=4, num_hidden_layers=2, subsampling_conv_channels=[128, 8],
                 output_proj_dims=48, attention_chunk_size=12, attention_context_left=13, attention_context_right=0,
                 conv_kernel_size=5, residual_weight=0.5, use_clipped_linears=True, attention_logit_cap=50.0,
                 rms_norm_eps=1e-6, hidden_act="silu", gradient_clipping=1e10)
        cfg = Gemma4AudioConfig(**c)
        cfg._attn_implementation = "eager"
        torch.manual_seed(0)
        ref = Gemma4AudioModel(cfg).eval()
        with torch.no_grad():
            for n, p in ref.named_parameters():
                p.uniform_(0.5, 1.5) if ("norm" in n and p.dim() == 1) else p.normal_(0, 0.1)
            for n, b in ref.named_buffers():
                for ende, wert in (("input_min", -3.0), ("input_max", 3.0), ("output_min", -2.5), ("output_max", 2.5)):
                    if n.endswith(ende):
                        b.fill_(wert)
        embed = torch.randn(32, 48) * 0.1
        sd = {**ref.state_dict(), "embed_audio.embedding_projection.weight": embed}
        h = ton.Hoeren(lambda n: sd[n], c, "cpu")
        w = (0.3 * np.sin(np.arange(int(2.6 * 16000)) / 16000 * 2 * np.pi * 300)).astype(np.float32)
        f, m = ton.merkmale(w)
        with torch.no_grad():
            o = ref(torch.tensor(f)[None], torch.tensor(m)[None])
            x = o.last_hidden_state[0][o.attention_mask[0]]
            erwartet = (x * torch.pow(x.pow(2).mean(-1, keepdim=True) + 1e-6, -0.5)) @ embed.T
        mine = h.encode(f, m)
        self.assertEqual(tuple(erwartet.shape), tuple(mine.shape))
        self.assertGreater(float(torch.nn.functional.cosine_similarity(mine, erwartet, dim=-1).min()), 0.9999)


@unittest.skipUnless(sys.platform == "win32", "Media Foundation gibt es nur unter Windows")
class VideoLesen(unittest.TestCase):
    def test_ein_bild_je_sekunde_richtig_herum(self):
        import videodatei as VD
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        fps, H, W = 12, 96, 128

        def bilder():
            for i in range(fps * 3):
                b = np.zeros((H, W, 3), np.uint8)
                b[: H // 2, :, 0] = 220
                b[H // 2:, :, 2] = 220
                b[:, : W // 4, 1] = 60 * (i // fps)
                yield b
        VD.schreiben(bilder(), d / "t.mp4", fps)
        raus = VD.bilder_lesen(d / "t.mp4")
        self.assertEqual(3, len(raus))
        self.assertEqual((H, W, 3), raus[0].shape)
        self.assertGreater(int(raus[0][5, W // 2, 0]), 150)            # oben rot
        self.assertGreater(int(raus[0][H - 5, W // 2, 2]), 150)        # unten blau
        self.assertAlmostEqual(120, int(raus[2][H // 4, 4, 1]), delta=12)

    def test_auswahl_wie_transformers(self):
        import videodatei as VD
        self.assertEqual([0, 24, 48], VD.auswahl(3.0, 24, 72))
        lang = VD.auswahl(100, 30, 3000)
        self.assertEqual(32, len(lang))
        self.assertEqual(0, lang[0])


class EinbetterVorsatz(unittest.TestCase):
    def test_titel_im_gemma_vorsatz(self):
        e = {"dokument_prompt": "title: none | text: "}
        self.assertEqual("title: Rezept Brot | text: ", embedder.dokument_vorsatz(e, "Rezept Brot"))
        self.assertEqual("title: none | text: ", embedder.dokument_vorsatz(e, ""))
        self.assertEqual("title: a b | text: ", embedder.dokument_vorsatz(e, "a | b"))     # kein zweiter Trenner
        self.assertEqual("", embedder.dokument_vorsatz({"dokument_prompt": ""}, "X"))     # andere Modelle

    def test_aufgaben_vorsatz(self):
        e = {"query_prompt": "task: search result | query: "}
        self.assertEqual("task: search result | query: ", embedder.such_prompt(e, "dokumente"))   # gemessen besser
        self.assertEqual("task: code retrieval | query: ", embedder.such_prompt(e, "code"))
        self.assertEqual("task: search result | query: ", embedder.such_prompt(e, "chat"))

    def test_kuerzen_normiert(self):
        v = np.arange(1, 9, dtype=np.float32)
        k = embedder.kuerzen([v, None], 4)
        self.assertEqual(4, len(k[0]))
        self.assertAlmostEqual(1.0, float(np.linalg.norm(k[0])), places=6)
        self.assertIsNone(k[1])
        self.assertIs(embedder.kuerzen([v], 0)[0], v)

    def test_kennung_mit_titel_und_laenge(self):
        kopf = {"arch": "gemma-embedding2", "dim": 768, "schichten": 24, "vokabular": 262144, "pooling": "mean",
                "familie": "embeddinggemma", "kontext": 8192}
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.gguf").write_bytes(b"GGUF")
            with mock.patch.object(embedder, "_gguf_kopf", lambda _p: kopf), \
                    mock.patch("config.load", lambda: {"embedding_dim": 256}):
                self.assertTrue(embedder.kennung(d).endswith("-mean-t1-d256"))
                self.assertEqual(256, embedder.mrl_laenge(d))
            with mock.patch.object(embedder, "_gguf_kopf", lambda _p: kopf), \
                    mock.patch("config.load", lambda: {"embedding_dim": 300}):
                self.assertTrue(embedder.kennung(d).endswith("-mean-t1"))         # keine erlaubte Länge
            with mock.patch.object(embedder, "_gguf_kopf", lambda _p: dict(kopf, arch="qwen3")), \
                    mock.patch("config.load", lambda: {"embedding_dim": 256}):
                self.assertTrue(embedder.kennung(d).endswith("-mean"))             # nur EmbeddingGemma 2


class Titel(unittest.TestCase):
    def test_titel_aus_dem_dateinamen(self):
        self.assertEqual("Rezept Brot", indexdb._titel("wissen", "wissen:Kochen/Rezept_Brot.md"))
        self.assertEqual("abc", indexdb._titel("skill", "skill:abc.md"))
        self.assertEqual("", indexdb._titel("chat", "chat:test_user:3"))
        self.assertEqual("", indexdb._titel("note", "note:12"))


class MedienEinlesen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        profil = self.tmp / "Profile" / "test_user"
        _wav(profil / "Musik" / "lied.wav", 1.0, {"INAM": "Regen am Fenster", "IGNR": "lo-fi, Klavier",
                                                  "ICMT": "Tropfen fallen leise"})
        _wav(profil / "Stimme" / "probe.wav", 0.5)
        (profil / "Videos").mkdir(parents=True)
        (profil / "Videos" / "tanz.mp4").write_bytes(b"\0" * 64)
        db = self.tmp / "memory.db"
        self.profil = "test_user"
        self.patches = [mock.patch.object(indexdb, "_db_path", lambda: db),
                        mock.patch.object(profilordner, "aktiv", lambda: self.profil),
                        mock.patch.object(profilordner, "wurzel", lambda: self.tmp / "Profile"),
                        mock.patch.object(memory, "kann_bilder", lambda: False),
                        mock.patch.object(memory, "_embed_texts", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        indexdb._invalidate()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        indexdb._invalidate()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ton_und_video_nur_als_text(self):
        self.assertEqual(3, indexdb.ingest_medien(None))
        self.assertEqual(0, indexdb.ingest_medien(None))
        refs = sorted(h["ref"] for h in indexdb.medien_suchen("Regen", 10))
        self.assertIn("ton:test_user:Musik/lied.wav", refs)
        self.assertEqual([], indexdb.suchen("Regen", 5, nur_worte=True))           # Medien nur in der Medien-Suche
        self.assertEqual(["video:test_user:Videos/tanz.mp4"],
                         [h["ref"] for h in indexdb.medien_suchen("tanz", 5, ("video",))])

    def test_werkzeug_und_profil_grenze(self):
        indexdb.ingest_medien(None)
        r = actions._medien_suchen({"frage": "Regen", "art": "ton"})
        self.assertTrue(r.ok)
        self.assertIn("Titel: Regen am Fenster", r.text)
        self.assertIn("Stil: lo-fi", r.text)
        self.assertIn("EmbeddingGemma 2", r.text)                                 # Hinweis: ohne Modell nur Text
        self.assertFalse(actions._medien_suchen({"frage": "x", "art": "foto"}).ok)
        self.assertFalse(actions.ACTIONS["medien_suchen"]["confirm"])
        self.profil = "example"
        self.assertEqual([], indexdb.medien_suchen("Regen", 5))
        self.assertTrue(indexdb._fremdes_profil("ton:test_user:Musik/lied.wav", "example"))

    def test_geloeschtes_verschwindet(self):
        indexdb.ingest_medien(None)
        (self.tmp / "Profile" / "test_user" / "Musik" / "lied.wav").unlink()
        indexdb.ingest_medien(None)
        self.assertEqual([], indexdb.medien_suchen("Regen", 5, ("ton",)))

    def test_medien_vektor_teile(self):
        p = self.tmp / "Profile" / "test_user" / "Musik" / "lied.wav"
        text, _datum, teile = indexdb._medien_text("ton", p)
        self.assertEqual([("ton", str(p)), ("text", " Regen am Fenster · lo-fi, Klavier")], teile)   # Medium vor Text
        self.assertIn("Liedtext: Tropfen fallen leise", text)
        probe = self.tmp / "Profile" / "test_user" / "Stimme" / "probe.wav"
        self.assertEqual(("text", " probe"), indexdb._medien_text("ton", probe)[2][1])    # ohne Titel: Dateiname


if __name__ == "__main__":
    unittest.main()
