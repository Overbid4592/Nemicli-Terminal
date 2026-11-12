"""Video-Motor (engines/video.py): umT5 gegen transformers mit Mini-Zufallsmodell, VAE und DiT mit
Zufallsgewichten (Formen, Kausalität, Determinismus), Sigmas, MP4 – alles ohne Modelldateien."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for sub in ("", "core", "engines"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import video as V              # noqa: E402

torch.manual_seed(0)


def _r(*shape, s=0.1):
    return torch.randn(*shape) * s


class UmT5(unittest.TestCase):
    def test_wie_transformers(self):
        from transformers import UMT5Config, UMT5EncoderModel
        cfg = UMT5Config(vocab_size=64, d_model=32, d_kv=8, d_ff=48, num_layers=2, num_heads=4,
                         relative_attention_num_buckets=32, relative_attention_max_distance=128,
                         feed_forward_proj="gated-gelu", dropout_rate=0.0, layer_norm_epsilon=1e-6,
                         is_encoder_decoder=False, use_cache=False, tie_word_embeddings=False)
        m = UMT5EncoderModel(cfg).eval()
        hf = m.state_dict()
        sd = {"__meta__": {"n_layer": 2, "n_head": 4, "d_kv": 8, "buckets": 32, "eps": 1e-6},
              "token_embd.weight": hf["shared.weight"], "enc.output_norm.weight": hf["encoder.final_layer_norm.weight"]}
        for i in range(2):
            b = f"encoder.block.{i}.layer."
            sd.update({f"enc.blk.{i}.attn_q.weight": hf[b + "0.SelfAttention.q.weight"],
                       f"enc.blk.{i}.attn_k.weight": hf[b + "0.SelfAttention.k.weight"],
                       f"enc.blk.{i}.attn_v.weight": hf[b + "0.SelfAttention.v.weight"],
                       f"enc.blk.{i}.attn_o.weight": hf[b + "0.SelfAttention.o.weight"],
                       f"enc.blk.{i}.attn_rel_b.weight": hf[b + "0.SelfAttention.relative_attention_bias.weight"],
                       f"enc.blk.{i}.attn_norm.weight": hf[b + "0.layer_norm.weight"],
                       f"enc.blk.{i}.ffn_gate.weight": hf[b + "1.DenseReluDense.wi_0.weight"],
                       f"enc.blk.{i}.ffn_up.weight": hf[b + "1.DenseReluDense.wi_1.weight"],
                       f"enc.blk.{i}.ffn_down.weight": hf[b + "1.DenseReluDense.wo.weight"],
                       f"enc.blk.{i}.ffn_norm.weight": hf[b + "1.layer_norm.weight"]})
        ids = [5, 17, 3, 40, 9, 1]
        with torch.no_grad():
            soll = m(input_ids=torch.tensor([ids])).last_hidden_state[0]
            ist = V.UmT5(sd)(ids, torch.float32)
        self.assertEqual(ist.shape, soll.shape)
        self.assertLess((ist - soll).abs().max().item(), 1e-4)

    def test_buckets(self):
        pos = torch.arange(6)
        b = V._rel_bucket(pos[None, :] - pos[:, None], 32)
        self.assertEqual(b.shape, (6, 6))
        self.assertTrue((b.diagonal() == 0).all())
        self.assertTrue((b[0, 1:] >= 16).all())              # Zukunft: obere Hälfte der Buckets
        self.assertTrue((b[1:, 0] < 16).all())


def _res_gewichte(sd, p, i, o):
    sd[p + ".residual.0.gamma"] = torch.ones(i, 1, 1, 1)
    sd[p + ".residual.2.weight"], sd[p + ".residual.2.bias"] = _r(o, i, 3, 3, 3), _r(o)
    sd[p + ".residual.3.gamma"] = torch.ones(o, 1, 1, 1)
    sd[p + ".residual.6.weight"], sd[p + ".residual.6.bias"] = _r(o, o, 3, 3, 3), _r(o)
    if i != o:
        sd[p + ".shortcut.weight"], sd[p + ".shortcut.bias"] = _r(o, i, 1, 1, 1), _r(o)


def _attn_gewichte(sd, p, c):
    sd[p + ".norm.gamma"] = torch.ones(c, 1, 1)
    sd[p + ".to_qkv.weight"], sd[p + ".to_qkv.bias"] = _r(3 * c, c, 1, 1), _r(3 * c)
    sd[p + ".proj.weight"], sd[p + ".proj.bias"] = _r(c, c, 1, 1), _r(c)


def dit_zufall(dim=32, heads=2, layers=2, ffn=48, text_dim=24, kanaele=48):
    sd = {"patch_embedding.weight": _r(dim, kanaele, 1, 2, 2), "patch_embedding.bias": _r(dim),
          "text_embedding.0.weight": _r(dim, text_dim), "text_embedding.0.bias": _r(dim),
          "text_embedding.2.weight": _r(dim, dim), "text_embedding.2.bias": _r(dim),
          "time_embedding.0.weight": _r(dim, 16), "time_embedding.0.bias": _r(dim),
          "time_embedding.2.weight": _r(dim, dim), "time_embedding.2.bias": _r(dim),
          "time_projection.1.weight": _r(6 * dim, dim), "time_projection.1.bias": _r(6 * dim),
          "head.head.weight": _r(4 * kanaele, dim), "head.head.bias": _r(4 * kanaele),
          "head.modulation": _r(1, 2, dim)}
    for i in range(layers):
        p = f"blocks.{i}."
        for art in ("self_attn", "cross_attn"):
            for n in ("q", "k", "v", "o"):
                sd[p + f"{art}.{n}.weight"], sd[p + f"{art}.{n}.bias"] = _r(dim, dim), _r(dim)
            sd[p + f"{art}.norm_q.weight"], sd[p + f"{art}.norm_k.weight"] = torch.ones(dim), torch.ones(dim)
        sd[p + "ffn.0.weight"], sd[p + "ffn.0.bias"] = _r(ffn, dim), _r(ffn)
        sd[p + "ffn.2.weight"], sd[p + "ffn.2.bias"] = _r(dim, ffn), _r(dim)
        sd[p + "norm3.weight"], sd[p + "norm3.bias"] = torch.ones(dim), torch.zeros(dim)
        sd[p + "modulation"] = _r(1, 6, dim)
    cfg = {"dim": dim, "num_heads": heads, "num_layers": layers, "ffn_dim": ffn, "freq_dim": 16, "out_dim": kanaele,
           "text_len": 8, "in_dim": kanaele, "eps": 1e-6}
    return sd, cfg


class DiT(unittest.TestCase):
    def test_formen_und_determinismus(self):
        torch.manual_seed(2)
        sd, cfg = dit_zufall()
        dit = V.WanDiT(sd, cfg)
        x, ctx = torch.randn(48, 3, 8, 8), torch.randn(5, 24)
        with torch.no_grad():
            v1 = dit(x, 500.0, ctx)
            v2 = dit(x, 500.0, ctx)
            v3 = dit(x, 500.0, torch.randn(5, 24))
            v4 = dit(x, 900.0, ctx)
        self.assertEqual(v1.shape, (48, 3, 8, 8))
        self.assertTrue(torch.isfinite(v1).all())
        self.assertTrue(torch.equal(v1, v2))
        self.assertFalse(torch.allclose(v1, v3))                   # der Text zählt
        self.assertFalse(torch.allclose(v1, v4))                   # die Zeit zählt
        self.assertEqual(dit.rope_dims, (8, 4, 4))

    def test_rope_dreht_nur(self):
        x = torch.randn(6, 2, 16)
        freqs = V.WanDiT(*dit_zufall())._freqs(1, 2, 3, torch.device("cpu"))
        y = V.WanDiT._rope(x, freqs)
        self.assertEqual(y.shape, x.shape)
        self.assertTrue(torch.allclose(y.norm(dim=-1), x.norm(dim=-1), atol=1e-5))
        self.assertTrue(torch.allclose(V.WanDiT._rope(x[:1], freqs[:1]), x[:1], atol=1e-6))   # Position 0: unverändert


class Bausteine(unittest.TestCase):
    def test_sigmas(self):
        s = V._sigmas(4, 1.0)
        self.assertTrue(torch.allclose(s, torch.tensor([1.0, 0.75, 0.5, 0.25, 0.0])))
        s3 = V._sigmas(10, 3.0)
        self.assertEqual((s3[0].item(), s3[-1].item()), (1.0, 0.0))
        self.assertTrue((s3[:-1] > s3[1:]).all())
        self.assertGreater(s3[5].item(), 0.5)                       # verschoben: länger bei viel Rauschen

    def test_zeit_einbettung(self):
        e = V._sinus(16, torch.tensor([0.0, 500.0]))
        self.assertEqual(e.shape, (2, 16))
        self.assertTrue(torch.allclose(e[0, :8], torch.ones(8)) and torch.allclose(e[0, 8:], torch.zeros(8)))

    def test_mp4(self):
        ziel = Path(tempfile.mkdtemp()) / "probe.mp4"
        V._mp4(np.random.randint(0, 255, (3, 64, 48, 3), dtype=np.uint8), ziel, 16)   # H.264: ab ~34 Zeilen
        self.assertGreater(ziel.stat().st_size, 0)
        try:
            import cv2
        except ImportError:
            return
        cap = cv2.VideoCapture(str(ziel))
        n = 0
        while cap.read()[0]:
            n += 1
        cap.release()
        self.assertEqual(n, 3)

    def test_teile_und_fehlende(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(V, "_cfg", side_effect=lambda k, s: str(d) if k == "video_ordner" else s), \
                mock.patch.object(V, "dit_config", return_value={"in_dim": 48}):
            self.assertIn("Kein Video-Modell", V.missing_reason())
            (d / "Wan").mkdir()
            (d / "Wan" / "wan.gguf").write_bytes(b"")
            self.assertIn("Text-Encoder fehlt", V.missing_reason())
            (d / "text_encoders").mkdir()
            (d / "text_encoders" / "u.gguf").write_bytes(b"")
            self.assertIn("VAE fehlt", V.missing_reason())
            (d / "Wan" / "Wan2.2_VAE.safetensors").write_bytes(b"")
            self.assertIn("Tokenizer fehlt", V.missing_reason())
            (d / "tokenizer").mkdir()
            (d / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
            self.assertIn("wan.gguf ist leer", V.missing_reason())      # das Modell selbst wird zuerst geprüft
            (d / "Wan" / "wan.gguf").write_bytes(b"x")
            with mock.patch.object(V, "_datei_kaputt", side_effect=lambda p: None if p.suffix == ".gguf"
                                   else ("ist leer" if p.stat().st_size == 0 else None)):
                self.assertIn("Wan2.2_VAE.safetensors ist leer", V.missing_reason())    # Platzhalter: 0 Byte
            with mock.patch.object(V, "_datei_kaputt", return_value=None):
                self.assertIsNone(V.missing_reason())
            self.assertEqual(V.teile()["dit"].name, "wan.gguf")

    def test_kaputte_dateien_erkennen(self):
        from safetensors.torch import save_file
        d = Path(tempfile.mkdtemp())
        gut = d / "vae.safetensors"
        save_file({"a": torch.zeros(64, 64)}, str(gut))
        self.assertIsNone(V._datei_kaputt(gut))
        halb = d / "halb.safetensors"
        halb.write_bytes(gut.read_bytes()[:-1000])                    # Download abgebrochen
        self.assertIn("unvollständig", V._datei_kaputt(halb))
        leer = d / "leer.json"
        leer.write_bytes(b"")
        self.assertEqual(V._datei_kaputt(leer), "ist leer")
        muell = d / "muell.gguf"
        muell.write_bytes(b"kein gguf" * 10)
        self.assertIn("beschädigt", V._datei_kaputt(muell))
        text = d / "tokenizer.json"
        text.write_text("{}", encoding="utf-8")
        self.assertIsNone(V._datei_kaputt(text))

    def test_clips_zaehlen(self):
        # 5 s = 121 Bilder = ein Clip; 30 s = 721 Bilder = 6 Clips (je 120 neue nach dem ersten)
        import math
        f = V.FAMILIEN["wan22-ti2v"]
        self.assertEqual(math.ceil((5 * f["fps"] - 1) / (f["bilder"] - 1)), 1)
        self.assertEqual(math.ceil((30 * f["fps"] - 1) / (f["bilder"] - 1)), 6)


def vae22_zufall(dim=8, z=48):
    """Zufallsgewichte in der Form der Wan-2.2-VAE: verschachtelte Stufen, Patch 2 (12 Kanäle), 48 Latent."""
    sd = {}
    dims = [dim, dim, dim * 2, dim * 4, dim * 4]
    sd["encoder.conv1.weight"], sd["encoder.conv1.bias"] = _r(dim, 12, 3, 3, 3), _r(dim)
    for lvl, (i, o) in enumerate(zip(dims[:-1], dims[1:])):
        p = f"encoder.downsamples.{lvl}.downsamples"
        for j in range(2):
            _res_gewichte(sd, f"{p}.{j}", i, o)
            i = o
        if lvl != 3:
            sd[f"{p}.2.resample.1.weight"], sd[f"{p}.2.resample.1.bias"] = _r(o, o, 3, 3), _r(o)
            if lvl in (1, 2):
                sd[f"{p}.2.time_conv.weight"], sd[f"{p}.2.time_conv.bias"] = _r(o, o, 3, 1, 1), _r(o)
    c = dims[-1]
    _res_gewichte(sd, "encoder.middle.0", c, c)
    _attn_gewichte(sd, "encoder.middle.1", c)
    _res_gewichte(sd, "encoder.middle.2", c, c)
    sd["encoder.head.0.gamma"] = torch.ones(c, 1, 1, 1)
    sd["encoder.head.2.weight"], sd["encoder.head.2.bias"] = _r(2 * z, c, 3, 3, 3), _r(2 * z)
    sd["conv1.weight"], sd["conv1.bias"] = _r(2 * z, 2 * z, 1, 1, 1), _r(2 * z)
    sd["conv2.weight"], sd["conv2.bias"] = _r(z, z, 1, 1, 1), _r(z)
    ddims = [dim * 4, dim * 4, dim * 4, dim * 2, dim]                 # wie 2.2: Halbierung im Residualblock
    sd["decoder.conv1.weight"], sd["decoder.conv1.bias"] = _r(ddims[0], z, 3, 3, 3), _r(ddims[0])
    _res_gewichte(sd, "decoder.middle.0", ddims[0], ddims[0])
    _attn_gewichte(sd, "decoder.middle.1", ddims[0])
    _res_gewichte(sd, "decoder.middle.2", ddims[0], ddims[0])
    for lvl, (i, o) in enumerate(zip(ddims[:-1], ddims[1:])):
        p = f"decoder.upsamples.{lvl}.upsamples"
        for j in range(3):
            _res_gewichte(sd, f"{p}.{j}", i, o)
            i = o
        if lvl != 3:
            sd[f"{p}.3.resample.1.weight"], sd[f"{p}.3.resample.1.bias"] = _r(o, o, 3, 3), _r(o)
            if lvl in (0, 1):
                sd[f"{p}.3.time_conv.weight"], sd[f"{p}.3.time_conv.bias"] = _r(2 * o, o, 3, 1, 1), _r(2 * o)
    sd["decoder.head.0.gamma"] = torch.ones(dim, 1, 1, 1)
    sd["decoder.head.2.weight"], sd["decoder.head.2.bias"] = _r(12, dim, 3, 3, 3), _r(12)
    return sd


class VAE22(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(3)
        self.vae = V.WanVAE22(vae22_zufall())

    def test_patch(self):
        x = torch.randn(1, 3, 2, 8, 6)
        p = V.WanVAE22._patch(x)
        self.assertEqual(p.shape, (1, 12, 2, 4, 3))
        self.assertTrue(torch.equal(V.WanVAE22._unpatch(p), x))
        # Reihenfolge (c, r, q): Kanal c*4 + r*2 + q bei (h-Versatz q, w-Versatz r)
        self.assertTrue(torch.equal(p[0, 1 * 4 + 1 * 2 + 0, 0, 0, 0], x[0, 1, 0, 0, 1]))

    def test_abkuerzungen(self):
        x = torch.arange(4 * 2 * 4 * 4, dtype=torch.float32).view(1, 4, 2, 4, 4)
        m = V.WanVAE22._mittel(x, 8, 2, 2)                       # 4·8 = 32 Werte -> 8 Kanäle à 4 Mittel
        self.assertEqual(m.shape, (1, 8, 1, 2, 2))
        d = V.WanVAE22._doppelt(torch.randn(1, 4, 1, 2, 2), 4, 2, 2, erstes=False)
        self.assertEqual(d.shape, (1, 4, 2, 4, 4))
        d1 = V.WanVAE22._doppelt(torch.randn(1, 4, 1, 2, 2), 4, 2, 2, erstes=True)
        self.assertEqual(d1.shape, (1, 4, 1, 4, 4))

    def test_bf16_rechnet_in_bf16(self):
        vae16 = V.WanVAE22({k: v.bfloat16() for k, v in vae22_zufall().items()})
        self.assertEqual(vae16.dtype, torch.bfloat16)
        torch.manual_seed(9)
        with torch.no_grad():
            lat = vae16.encode(torch.rand(3, 5, 32, 32) * 2 - 1)
            out = vae16.decode(lat)
        self.assertEqual((lat.dtype, out.dtype), (torch.float32, torch.float32))
        self.assertEqual(out.shape, (3, 5, 32, 32))
        self.assertTrue(torch.isfinite(out).all())

    def test_kacheln(self):
        self.assertEqual(V._kachel_starts(10, 4, 2), [0, 2, 4, 6])
        self.assertEqual(V._kachel_starts(4, 4, 2), [0])
        self.assertEqual(V._kachel_starts(9, 4, 1), [0, 3, 5])
        g = V._rampe(4, 1, True, True, 2)
        self.assertEqual(g.shape, (8,))
        self.assertTrue((g > 0).all() and g.max() == 1.0 and g[0] < 1 and g[-1] < 1)
        torch.manual_seed(8)
        lat = torch.randn(48, 2, 6, 10) * 0.5
        with torch.no_grad():
            ganz = self.vae.decode(lat)
            gleich = self.vae.decode_kacheln(lat, kachel=10)            # passt in eine Kachel: identisch
            kach = self.vae.decode_kacheln(lat, kachel=4, rand=2)
        self.assertTrue(torch.equal(gleich, ganz))
        self.assertEqual(kach.shape, ganz.shape)
        self.assertTrue(torch.isfinite(kach).all())
        self.assertLessEqual(kach.abs().max().item(), 1.0)

        def oertlich(latent, on_status=None):                       # jeder Bildpunkt hängt nur an seinem Latent
            T = latent.shape[1]
            z = latent[:3].repeat_interleave(16, 2).repeat_interleave(16, 3)
            return torch.cat([z[:, :1], z[:, 1:].repeat_interleave(4, 1)], 1)[:, :1 + 4 * (T - 1)]
        with mock.patch.object(self.vae, "decode", side_effect=oertlich):
            self.assertTrue(torch.allclose(self.vae.decode_kacheln(lat, kachel=4, rand=2), oertlich(lat), atol=1e-6))

    def test_formen_und_kausalitaet(self):
        video = torch.rand(3, 9, 64, 64) * 2 - 1
        with torch.no_grad():
            lat = self.vae.encode(video)
            self.assertEqual(lat.shape, (48, 3, 4, 4))
            self.assertTrue(torch.isfinite(lat).all())
            self.assertTrue(torch.allclose(self.vae.encode(video[:, :5]), lat[:, :2], atol=1e-5))
            out = self.vae.decode(lat)
            self.assertEqual(out.shape, (3, 9, 64, 64))
            self.assertTrue(torch.isfinite(out).all())
            self.assertTrue(torch.allclose(self.vae.decode(lat[:, :2]), out[:, :5], atol=1e-5))


def _q8_0(w):
    """(n_out, n_in) -> GGUF-Q8_0-Rohdaten: je 32 Werte eine fp16-Skala und 32 int8."""
    b = w.reshape(-1, 32).float()
    d = (b.abs().amax(1, keepdim=True) / 127).clamp(min=1e-8)
    q = (b / d).round().clamp(-127, 127).to(torch.int8)
    return torch.cat([d.half().view(torch.uint8), q.view(torch.uint8)], 1).reshape(-1)


def tae_zufall(nf=(8, 6, 4, 4), z=48, super_=False):
    """Zufallsgewichte im Aufbau von taew2_2 / taew2_2_super (Indizes wie im Original-Decoder)."""
    sd = {}

    def conv(i, o, n, k=3, bias=True):
        sd[f"decoder.{i}.weight"] = _r(o, n, k, k)
        if bias:
            sd[f"decoder.{i}.bias"] = _r(o)
    if not super_:
        conv(1, nf[0], z)
        for blocks, tg, cv, (a, b), s in (((3, 4, 5), 7, 8, (nf[0], nf[1]), 1), ((9, 10, 11), 13, 14, (nf[1], nf[2]), 2),
                                         ((15, 16, 17), 19, 20, (nf[2], nf[3]), 2)):
            for bl in blocks:
                for j, n in ((0, 2 * a), (2, a), (4, a)):
                    sd[f"decoder.{bl}.conv.{j}.weight"], sd[f"decoder.{bl}.conv.{j}.bias"] = _r(a, n, 3, 3), _r(a)
            sd[f"decoder.{tg}.conv.weight"] = _r(a * s, a, 1, 1)
            conv(cv, b, a, bias=False)
        conv(22, 12, nf[3])
    else:
        conv(0, nf[0], z, k=1, bias=False)
        for blocks, cv, tg, (a, b), s in (((1, 2, 3), 4, 7, (nf[0], nf[1]), 1), ((8, 9, 10), 11, 14, (nf[1], nf[2]), 2),
                                         ((15, 16, 17), 18, 21, (nf[2], nf[3]), 2)):
            for bl in blocks:
                sd[f"decoder.{bl}.conv.0.weight"] = _r(2 * a, 1, 7, 7)
                sd[f"decoder.{bl}.conv.1.weight"], sd[f"decoder.{bl}.conv.1.bias"] = _r(4 * a, 2 * a, 1, 1), _r(4 * a)
                sd[f"decoder.{bl}.conv.3.weight"] = _r(a, 4 * a, 1, 1)
            conv(cv, b * 4, a)
            sd[f"decoder.{tg}.conv.weight"] = _r(b * s, b, 1, 1)
        conv(22, 12, nf[3])
    return sd


class Loeser(unittest.TestCase):
    def test_unipc_trifft_geraden_weg_exakt(self):
        torch.manual_seed(12)
        x0, eps = torch.randn(4, 3, 5, dtype=torch.float64), torch.randn(4, 3, 5, dtype=torch.float64)
        for n in (2, 4, 10, 30):
            sig = V._sigmas(n, 5.0, 0.999)
            self.assertLess(float(sig[0]), 1.0)                      # UniPC beginnt unter 1 (log(1 − σ))
            up, s0 = V.UniPC(sig), float(sig[0])
            x = s0 * eps + (1 - s0) * x0
            for _ in range(n):
                x = up.schritt(x, eps - x0)
            self.assertLess((x - x0).abs().max().item(), 1e-10, n)

    def test_sampler_mit_unipc_und_cfg_nur_am_anfang(self):
        zweige = []

        class Dit:
            def __call__(self, x, t, ctx, **kw):
                zweige.append(ctx[0, 0].item())
                return torch.zeros_like(x)
        pos, neg = torch.ones(2, 8), torch.zeros(2, 8)
        out = V._sample(Dit(), torch.zeros(48, 2, 4, 4), pos, neg, V._sigmas(10, 5.0, 0.999), 5.0, 1,
                        loeser="unipc", cfg_bis=0.4)
        self.assertTrue(torch.isfinite(out).all())
        self.assertEqual(zweige.count(0.0), 4)                       # Negativ-Durchlauf nur in 4 von 10 Schritten
        self.assertEqual(zweige.count(1.0), 10)


class Nachschliff(unittest.TestCase):
    def test_zeitplan_und_raster(self):
        s = V._sigmas_ab(4, 5.0, 0.6)
        self.assertEqual(len(s), 5)
        self.assertAlmostEqual(float(s[0]), 0.6, places=5)            # beginnt genau bei der Stärke
        self.assertEqual(float(s[-1]), 0.0)
        self.assertTrue((s[:-1] > s[1:]).all())
        self.assertEqual((V._raster(416, 32), V._raster(608, 32), V._raster(10, 32)), (416, 608, 32))

    def test_bedingung_je_groesse(self):
        torch.manual_seed(13)
        vae = V.WanVAE22(vae22_zufall())
        start = torch.rand(3, 64, 32) * 2 - 1
        fest, y = V._bedingung(vae, V.FAMILIEN["wan22-ti2v"], start, None, 16, 32, 5, 2, 16, "cpu")
        self.assertIsNone(y)
        self.assertEqual(fest.shape, (48, 1, 2, 1))                   # Startbild auf 16×32 gebracht
        fest, y = V._bedingung(vae, V.FAMILIEN["wan22-fun-inp"], start, start, 32, 64, 5, 2, 16, "cpu")
        self.assertIsNone(fest)
        self.assertEqual(y.shape, (52, 2, 4, 2))
        self.assertTrue((y[:4, 0] == 1).all() and (y[:4, -1] == 1).all())

    def test_sampler_startet_am_latent(self):
        class Null:
            def __call__(self, x, t, ctx, **kw):
                return torch.zeros_like(x)
        ziel = torch.full((48, 2, 4, 4), 0.5)
        out = V._sample(Null(), torch.zeros_like(ziel), torch.zeros(2, 8), None, V._sigmas_ab(3, 5.0, 0.0001), 1.0, 1,
                        x_start=ziel)
        self.assertTrue(torch.allclose(out, ziel, atol=1e-3))          # kaum Rauschen: bleibt beim Latent


class StartUndEndbild(unittest.TestCase):
    def test_bauart_und_maske(self):
        self.assertEqual(V.familie({"in_dim": 100}), "wan22-fun-inp")
        self.assertEqual(V.FAMILIEN["wan22-fun-inp"]["bedingung"], "maske")
        m = V.maske(4, 3, 5, start=True, ende=True, device="cpu")
        self.assertEqual(m.shape, (4, 4, 3, 5))
        self.assertTrue((m[:, 0] == 1).all() and (m[:, -1] == 1).all() and (m[:, 1:3] == 0).all())
        self.assertEqual(V.maske(4, 3, 5, False, False, "cpu").sum().item(), 0)

    def test_dit_mit_bedingung(self):
        torch.manual_seed(11)
        sd, cfg = dit_zufall()
        sd["patch_embedding.weight"] = _r(32, 100, 1, 2, 2)              # 48 Rauschen + 4 Maske + 48 Latent
        cfg["in_dim"] = 100
        dit = V.WanDiT(sd, cfg)
        x, ctx, y = torch.randn(48, 2, 8, 8), torch.randn(5, 24), torch.randn(52, 2, 8, 8)
        with torch.no_grad():
            a = dit(x, 500.0, ctx, y=y)
            b = dit(x, 500.0, ctx, y=torch.randn(52, 2, 8, 8))
        self.assertEqual(a.shape, (48, 2, 8, 8))
        self.assertFalse(torch.allclose(a, b))                           # die Bedingung zählt

    def test_sampler_reicht_bedingung_durch(self):
        gesehen = []

        class Dit:
            def __call__(self, x, t, ctx, **kw):
                gesehen.append(kw.get("y"))
                return torch.zeros_like(x)
        y = torch.ones(52, 2, 4, 4)
        V._sample(Dit(), torch.zeros(48, 2, 4, 4), torch.zeros(2, 8), torch.zeros(2, 8), V._sigmas(2, 1.0), 5.0, 1, y=y)
        self.assertEqual(len(gesehen), 4)
        self.assertTrue(all(g is y for g in gesehen))

    def test_modell_mit_endbild_finden(self):
        d = Path(tempfile.mkdtemp())
        for name in ("A-Normal", "B-Fun"):
            (d / name).mkdir()
            (d / name / f"{name}.gguf").write_bytes(b"")
        cfgs = {"A-Normal.gguf": {"in_dim": 48}, "B-Fun.gguf": {"in_dim": 100}}
        with mock.patch.object(V, "_cfg", side_effect=lambda k, s: str(d) if k == "video_ordner" else s), \
                mock.patch.object(V, "dit_config", side_effect=lambda t: cfgs[t["dit"].name]):
            self.assertEqual(V._modell_mit_endbild(), "B-Fun")
            cfgs.update({"A-Normal.gguf": {"in_dim": 100}, "B-Fun.gguf": {"in_dim": 48}})
            self.assertEqual(V.gewaehlt(), "B-Fun")                      # ohne Einstellung: das ohne Endbild
            cfgs["A-Normal.gguf"] = {"in_dim": 48}
            self.assertIsNone(V._modell_mit_endbild())


class MiniVAETests(unittest.TestCase):
    def test_formen_stuecke_und_kausalitaet(self):
        for super_ in (False, True):
            torch.manual_seed(10)
            tae = V.TaeW22(tae_zufall(super_=super_))
            self.assertEqual(tae.super, super_)
            lat = torch.randn(48, 4, 3, 2)
            ganz = tae.decode(lat, stueck=10)
            self.assertEqual(ganz.shape, (3, 13, 48, 32))             # 1 + 4·3 Bilder, 16× Fläche
            self.assertLessEqual(ganz.abs().max().item(), 1.0)
            self.assertTrue(torch.allclose(tae.decode(lat, stueck=1), ganz, atol=1e-5))   # Gedächtnis über Stücke
            spaet = lat.clone()
            spaet[:, 3] += 1.0                                        # nur das letzte Latent-Bild ändern
            anders = tae.decode(spaet, stueck=2)
            self.assertTrue(torch.allclose(anders[:, :9], ganz[:, :9], atol=1e-5))         # frühere Bilder bleiben
            self.assertFalse(torch.allclose(anders[:, 9:], ganz[:, 9:]))

    def test_datei_finden(self):
        d = Path(tempfile.mkdtemp())
        (d / "Wan").mkdir()
        (d / "tae").mkdir()
        (d / "tae" / "taew2_2_super.safetensors").write_bytes(b"")
        with mock.patch.object(V, "_cfg", side_effect=lambda k, s: str(d) if k == "video_ordner" else s):
            t = {"ordner": d / "Wan"}
            self.assertEqual(V._tae_datei(t, "super"), d / "tae" / "taew2_2_super.safetensors")
            self.assertIsNone(V._tae_datei(t, "klein"))                # fehlt: große VAE
            self.assertIsNone(V._tae_datei(t, "gross"))
            (d / "Wan" / "taew2_2.safetensors").write_bytes(b"")
            self.assertEqual(V._tae_datei(t, " Klein "), d / "Wan" / "taew2_2.safetensors")
            self.assertNotIn("tae", V.modelle())                        # tae/ ist kein Modell


class FP8Tests(unittest.TestCase):
    def test_genauigkeit_auf_der_cpu(self):
        torch.manual_seed(6)
        w, x = torch.randn(64, 96) * 0.05, torch.randn(5, 96)
        f = V._F8(w)
        self.assertEqual(f.w8.dtype, torch.float8_e4m3fn)
        soll = x @ w.t()
        ist = f(x)
        self.assertEqual(ist.shape, (5, 64))
        self.assertLess(((ist - soll).norm() / soll.norm()).item(), 0.05)

    def test_gguf_wird_umgestellt(self):
        from ggufengine.gguf import GGML_TYPES
        q8 = next(k for k, v in GGML_TYPES.items() if v[0] == "Q8_0")
        torch.manual_seed(7)
        w = torch.randn(32, 64) * 0.1
        a = "blocks.0.ffn.0.weight"
        sd = {a: V._Q(_q8_0(w), q8, 32, 64), "blocks.0.ffn.2.weight": V._Q(_q8_0(torch.randn(8, 32)), q8, 8, 32),
              "time_projection.1.weight": V._Q(_q8_0(w), q8, 32, 64), "blocks.0.ffn.0.bias": torch.zeros(32)}
        self.assertEqual(V._als_fp8(sd, torch.float32), 1)
        self.assertIsInstance(sd[a], V._F8)
        self.assertIsInstance(sd["blocks.0.ffn.2.weight"], V._Q)    # 8 Zeilen: nicht durch 16 teilbar
        self.assertIsInstance(sd["time_projection.1.weight"], V._Q)  # Modulation bleibt genau
        x = torch.randn(3, 64)
        self.assertLess(((sd[a](x) - x @ w.t()).norm() / (x @ w.t()).norm()).item(), 0.06)

    def test_nur_mit_passender_karte(self):
        self.assertFalse(V._fp8_bereit("cpu"))


class MagCacheTests(unittest.TestCase):
    @staticmethod
    def _original(verh, steps, schwelle, k, behalten):
        """Ablauf des Originals (magcache_forward), über beide Zweige gezählt: welche Aufrufe springen."""
        r = np.array([1.0] * 2 + list(verh))
        if len(r) != steps * 2:
            def interp(a, n):
                return a[np.round(np.arange(n) * (len(a) - 1) / (n - 1)).astype(int)]
            r = np.concatenate([interp(r[0::2], steps).reshape(-1, 1), interp(r[1::2], steps).reshape(-1, 1)],
                               axis=1).reshape(-1)
        prod, fehler, folge, sprung = [1.0, 1.0], [0.0, 0.0], [0, 0], []
        for cnt in range(steps * 2):
            z, ueber = cnt % 2, False
            if cnt >= int(steps * 2 * behalten):
                prod[z] *= r[cnt]
                folge[z] += 1
                fehler[z] += abs(1 - prod[z])
                if fehler[z] < schwelle and folge[z] <= k:
                    ueber = True
                else:
                    prod[z], fehler[z], folge[z] = 1.0, 0.0, 0
            sprung.append(ueber)
        return sprung

    def test_listen_vollstaendig(self):
        self.assertEqual((len(V.MAG_T2V), len(V.MAG_I2V)), (98, 98))
        self.assertTrue(all(0.9 < v < 1.01 for v in V.MAG_T2V + V.MAG_I2V))

    def test_wie_das_original(self):
        for liste in (V.MAG_T2V, V.MAG_I2V):
            for steps in (50, 30, 20, 8):
                for schwelle, k in ((0.06, 2), (0.12, 3)):
                    mc = V.MagCache(liste, steps, schwelle, k, 0.2)
                    mc.residual = [object(), object()]                  # nach dem ersten vollen Durchlauf
                    ist = [mc.ueberspringen(cnt // 2, cnt % 2) for cnt in range(steps * 2)]
                    self.assertEqual(ist, self._original(liste, steps, schwelle, k, 0.2), (steps, schwelle))
                    self.assertEqual(mc.gespart, sum(ist))

    def test_spart_und_haelt_grenzen(self):
        mc = V.MagCache(V.MAG_T2V, 30, 0.06, 2, 0.2)
        mc.residual = [object(), object()]
        sprung = [mc.ueberspringen(cnt // 2, cnt % 2) for cnt in range(60)]
        self.assertFalse(any(sprung[:12]))                              # die ersten 20 % rechnen voll
        self.assertGreater(sum(sprung), 0)
        for z in (0, 1):                                                # höchstens k Sprünge in Folge je Zweig
            folge = 0
            for s in sprung[z::2]:
                folge = folge + 1 if s else 0
                self.assertLessEqual(folge, 2)
        self.assertFalse(V.MagCache(V.MAG_T2V, 30).ueberspringen(20, 0))   # ohne Residual nie springen

    def test_dit_mit_gemerktem_residual(self):
        torch.manual_seed(5)
        sd, cfg = dit_zufall()
        dit = V.WanDiT(sd, cfg)
        x, ctx = torch.randn(48, 2, 8, 8), torch.randn(5, 24)

        class Immer:
            residual = [None, None]

            def ueberspringen(self, schritt, zweig):
                return self.residual[zweig] is not None
        mc = Immer()
        with torch.no_grad():
            voll = dit(x, 500.0, ctx, cache=mc, schritt=0, zweig=0)        # rechnet und merkt sich den Residual
            self.assertIsNotNone(mc.residual[0])
            self.assertIsNone(mc.residual[1])
            gesprungen = dit(x, 500.0, ctx, cache=mc, schritt=1, zweig=0)  # gleiche Eingabe: gleiches Ergebnis
            ohne = dit(x, 500.0, ctx)
        self.assertTrue(torch.allclose(voll, gesprungen, atol=1e-5))
        self.assertTrue(torch.allclose(voll, ohne, atol=1e-5))

    def test_sampler_reicht_cache_durch(self):
        aufrufe = []

        class Dit:
            def __call__(self, x, t, ctx, **kw):
                aufrufe.append((kw.get("schritt"), kw.get("zweig")))
                return torch.zeros_like(x)
        mc = V.MagCache(V.MAG_T2V, 3)
        V._sample(Dit(), torch.zeros(48, 2, 4, 4), torch.zeros(2, 8), torch.zeros(2, 8), V._sigmas(3, 1.0), 5.0, 1,
                  cache=mc)
        self.assertEqual(aufrufe, [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)])


class Bauarten(unittest.TestCase):
    def test_familie(self):
        self.assertEqual(V.familie({"in_dim": 48}), "wan22-ti2v")
        for alt in (36, 16, 7):                                      # Wan 2.1 und Unbekanntes
            with self.assertRaises(RuntimeError):
                V.familie({"in_dim": alt})

    def test_dit_zeitschritt_je_bild(self):
        torch.manual_seed(4)
        sd, cfg = dit_zufall()
        dit = V.WanDiT(sd, cfg)
        x, ctx = torch.randn(48, 3, 8, 8), torch.randn(5, 24)
        with torch.no_grad():
            a = dit(x, torch.tensor([500.0, 500.0, 500.0]), ctx)
            b = dit(x, 500.0, ctx)
            c = dit(x, torch.tensor([0.0, 500.0, 500.0]), ctx)
        self.assertTrue(torch.allclose(a, b, atol=1e-5))            # gleiche Zeit je Bild = Zahl
        self.assertFalse(torch.allclose(a, c))                      # erstes Bild mit Zeit 0 rechnet anders

    def test_sampler_haelt_erstes_bild_fest(self):
        aufrufe = []

        class Dit:
            def __call__(self, x, t, ctx):
                aufrufe.append(t.clone() if torch.is_tensor(t) else t)
                return torch.zeros_like(x)
        fest = torch.full((48, 1, 4, 4), 0.25)
        x = torch.zeros(48, 3, 4, 4)
        out = V._sample(Dit(), x, torch.zeros(2, 8), None, V._sigmas(3, 1.0), 1.0, 1, fest=fest)
        self.assertTrue(torch.equal(out[:, :1], fest))
        self.assertEqual(aufrufe[0].tolist()[0], 0.0)
        self.assertGreater(aufrufe[0].tolist()[1], 0.0)

    def test_modelle_im_ordner(self):
        d = Path(tempfile.mkdtemp())
        (d / "wan.safetensors").write_bytes(b"")                    # oben im Ordner: kein Modell
        for name in ("Eins", "Zwei"):
            (d / name).mkdir()
            (d / name / f"{name}-Q4.gguf").write_bytes(b"")
        (d / "Zwei" / "Wan2.2_VAE.safetensors").write_bytes(b"")
        (d / "text_encoders").mkdir()
        (d / "text_encoders" / "u.gguf").write_bytes(b"")
        with mock.patch.object(V, "_cfg", side_effect=lambda k, s: str(d) if k == "video_ordner" else s):
            m = V.modelle()
            self.assertEqual(list(m), ["Eins", "Zwei"])
            self.assertTrue(m["Zwei"]["gguf"])
            self.assertIsNone(m["Eins"]["vae"])
            self.assertEqual(m["Zwei"]["vae"].name, "Wan2.2_VAE.safetensors")
            self.assertEqual(V.gewaehlt(), "Eins")
            self.assertEqual(V.teile("Zwei")["dit"].name, "Zwei-Q4.gguf")

    def test_vorgaben_je_modell(self):
        d = Path(tempfile.mkdtemp())
        for name, datei in (("Normal", "Wan2.2-TI2V-5B-Q4_K_M.gguf"), ("Turbo", "Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf"),
                            ("Eigen", "x.gguf"), ("Kaputt", "y-turbo.gguf")):
            (d / name).mkdir()
            (d / name / datei).write_bytes(b"")
        (d / "Eigen" / "video.json").write_text(json.dumps({"steps": 8, "cfg": 2, "fremd": 1}), encoding="utf-8")
        (d / "Kaputt" / "video.json").write_text("{kein json", encoding="utf-8")
        with mock.patch.object(V, "_cfg", side_effect=lambda k, s: str(d) if k == "video_ordner" else s):
            m = V.modelle()
        self.assertEqual(m["Normal"]["vorgaben"], {})
        self.assertEqual(m["Turbo"]["vorgaben"], {"steps": 4, "cfg": 1.0})
        self.assertEqual(m["Eigen"]["vorgaben"], {"steps": 8.0, "cfg": 2.0})      # Datei geht vor dem Namen
        self.assertEqual(m["Kaputt"]["vorgaben"], {})                              # kaputte Datei: keine Vorgaben

    def test_text_encoder_im_modell_ordner(self):
        d = Path(tempfile.mkdtemp())
        m = d / "Eins"
        for p in (m / "text_encoders", m / "tokenizer", d / "text_encoders"):
            p.mkdir(parents=True)
        (m / "Eins-Q4.gguf").write_bytes(b"")
        (m / "text_encoders" / "eigen.gguf").write_bytes(b"")
        (m / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
        (d / "text_encoders" / "geteilt.gguf").write_bytes(b"")
        with mock.patch.object(V, "_cfg", side_effect=lambda k, s: str(d) if k == "video_ordner" else s):
            self.assertEqual(list(V.modelle()), ["Eins"])                # Unterordner sind keine Modelle
            t = V.teile("Eins")
            self.assertEqual(t["dit"].name, "Eins-Q4.gguf")
            self.assertEqual(t["text"].name, "eigen.gguf")               # der eigene vor dem geteilten
            self.assertEqual(t["tokenizer"], m / "tokenizer" / "tokenizer.json")
            (m / "text_encoders" / "eigen.gguf").unlink()
            self.assertEqual(V.teile("Eins")["text"].name, "geteilt.gguf")

    def test_gguf_config_aus_echter_datei(self):
        p = ROOT / "Models" / "Video" / "WAN-GGUF" / "Wan2.2-TI2V-5B-Q4_K_M.gguf"
        if not p.exists():
            self.skipTest("Wan-2.2-GGUF nicht da")
        c = V._gguf_dit_config(p)
        self.assertEqual((c["dim"], c["in_dim"], c["out_dim"], c["num_heads"], c["num_layers"], c["ffn_dim"]),
                         (3072, 48, 48, 24, 30, 14336))
        self.assertEqual(V.familie(c), "wan22-ti2v")


class Werkzeug(unittest.TestCase):
    def setUp(self):
        for sub in ("tools", "ui"):
            p = str(ROOT / sub)
            if p not in sys.path:
                sys.path.insert(0, p)

    def test_registriert(self):
        import actions
        import commands
        import modes
        self.assertIn("video_erstellen", actions.ACTIONS)
        self.assertNotIn("video_erstellen", modes.READ_TOOLS)
        self.assertIn("/video", commands.COMMANDS)

    def test_fehler_und_erfolg(self):
        import actions
        r = actions._video_erstellen({})
        self.assertFalse(r.ok)
        self.assertIn("Prompt", r.text)
        with mock.patch.object(V, "missing_reason", return_value="Kein Video-Modell: x"):
            r = actions._video_erstellen({"prompt": "a cat walks"})
        self.assertFalse(r.ok)
        self.assertIn("Kein Video-Modell", r.text)
        with mock.patch.object(V, "missing_reason", return_value=None), \
                mock.patch.object(V, "generate", return_value="C:/x/v.mp4") as gen, \
                mock.patch("os.startfile", create=True):
            r = actions._video_erstellen({"prompt": "a cat walks", "startbild": "C:/nicht/da.png"})
            self.assertFalse(r.ok)
            self.assertIn("nicht gefunden", r.text)
            text = actions._video_erstellen({"prompt": "a cat walks", "sekunden": "10", "seed": 7})
        self.assertIn("Video fertig", text)
        gen.assert_called_once()
        self.assertEqual(gen.call_args.kwargs["sekunden"], 10.0)
        self.assertEqual(gen.call_args.kwargs["seed"], 7)
        self.assertIsNone(gen.call_args.kwargs["startbild"])

    def test_bild_malen_umzeichnen(self):
        import actions
        import imagegen
        vorlage = Path(tempfile.mkdtemp()) / "start.png"
        with mock.patch.object(imagegen, "missing_reason_aktiv", return_value=None):
            r = actions._bild_malen({"prompt": "a woman walks", "vorlage": str(vorlage)})
            self.assertFalse(r.ok)
            self.assertIn("vorlage nicht gefunden", r.text)
            vorlage.write_bytes(b"")
            with mock.patch.object(imagegen, "paint", return_value=str(vorlage)) as paint, \
                    mock.patch.object(imagegen, "gesicht_nachbessern", return_value=None), \
                    mock.patch("os.startfile", create=True):
                actions._bild_malen({"prompt": "a woman walks", "vorlage": str(vorlage), "staerke": "1.5"})
        self.assertEqual(paint.call_args.kwargs["vorlage"], str(vorlage))
        self.assertEqual(paint.call_args.kwargs["staerke"], 0.98)          # auf den erlaubten Bereich begrenzt

    def test_videos_je_persoenlichkeit(self):
        import profilordner
        p = profilordner.video_pfad(".mp4")
        self.assertEqual(p.parent.name, "Videos")
        self.assertEqual(p.parent.parent.parent.name, "Profile")
        self.assertTrue(p.name.endswith(".mp4"))
        self.assertIn("Videos", profilordner.UNTERORDNER)

    def test_video_opts(self):
        import ast
        quelle = main_quelle.text()
        knoten = [n for n in ast.parse(quelle).body if isinstance(n, ast.FunctionDef) and n.name == "_video_opts"]
        ns = {"re": __import__("re")}
        exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), ns)
        o = ns["_video_opts"]('a cat walks --sekunden 12 --start "C:/a b/x.png" --seed 5 --steps abc')
        self.assertEqual(o["prompt"], "a cat walks")
        self.assertEqual((o["sekunden"], o["start"], o["seed"]), (12.0, "C:/a b/x.png", 5))
        self.assertNotIn("steps", o)


class PruefungVorDemRechnen(unittest.TestCase):
    """Fehler, die vor dem ersten geladenen Gewicht auffallen müssen."""

    def test_zu_viele_schluesselbilder(self):
        from unittest import mock
        import video as V
        t = {"name": "fun", "vorgaben": {}}
        with mock.patch.object(V, "missing_reason", lambda *a: None), \
                mock.patch.object(V, "_mp4_weg", lambda: "windows"), \
                mock.patch.object(V, "teile", lambda *a: t), \
                mock.patch.object(V, "dit_config", lambda *a: {"in_dim": 100}), \
                mock.patch.object(V, "_lade_gguf_dit", side_effect=AssertionError("darf nicht laden")), \
                mock.patch.object(V, "_lade_safetensors", side_effect=AssertionError("darf nicht laden")):
            with self.assertRaises(RuntimeError) as fehler:
                V.generate("a fox", sekunden=20, schluesselbilder=[f"k{i}.png" for i in range(6)])
        self.assertIn("Höchstens 4 Schlüsselbilder", str(fehler.exception))

    def test_kaputtes_videomodell_faellt_zuerst_auf(self):
        import json
        import tempfile
        from unittest import mock
        import video as V
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            dit = d / "dit.safetensors"
            kopf = json.dumps({"w": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]}}).encode()
            dit.write_bytes(len(kopf).to_bytes(8, "little") + kopf + b"\0" * 4)       # 12 von 16 Bytes fehlen
            (d / "dit.json").write_text("{}")
            for n in ("vae.safetensors", "text.gguf"):
                (d / n).write_bytes(b"x")
            (d / "tok").mkdir()
            t = {"name": "kaputt", "dit": dit, "gguf": False, "config": d / "dit.json", "text": d / "text.gguf",
                 "vae": d / "vae.safetensors", "tokenizer": d / "tok", "ordner": d}
            with mock.patch.object(V, "teile", lambda *a: t):
                grund = V.missing_reason("kaputt")
        self.assertIn("dit.safetensors", grund)
        self.assertIn("unvollständig", grund)


if __name__ == "__main__":
    unittest.main()
