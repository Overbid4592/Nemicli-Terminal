"""Video-Motoren neben Wan 2.2 5B (engines/videomotoren) – geprüft gegen diffusers mit winzigen
Zufallsmodellen. Ohne diffusers werden die Vergleiche übersprungen."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("engines", "core", "", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    from safetensors.torch import save_file
except Exception:                                    # pragma: no cover
    torch = None
try:
    import diffusers
except Exception:                                    # pragma: no cover
    diffusers = None


def _zufall(modul, staerke):
    with torch.no_grad():
        for p in modul.parameters():
            p.add_(torch.randn_like(p) * staerke)
    return modul


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class LTX2Netz(unittest.TestCase):
    """Video-und-Ton-Transformer mit den Schaltern von LTX-2.5 (split-RoPE, Gates, Text-Modulation)."""

    @classmethod
    def setUpClass(cls):
        from diffusers import LTX2VideoTransformer3DModel
        torch.manual_seed(0)
        cls.m = _zufall(LTX2VideoTransformer3DModel(
            in_channels=128, out_channels=128, num_attention_heads=2, attention_head_dim=32, cross_attention_dim=64,
            audio_in_channels=128, audio_out_channels=128, audio_num_attention_heads=2, audio_attention_head_dim=16,
            audio_cross_attention_dim=32, num_layers=2, caption_channels=64, gated_attn=True, cross_attn_mod=True,
            audio_gated_attn=True, audio_cross_attn_mod=True, rope_type="split", ff_bias=False, audio_ff_bias=False,
            use_prompt_adaln_single=True, use_prompt_embeddings=False).eval(), 0.05)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pfad = Path(cls.tmp.name) / "ltx.safetensors"
        save_file({k: v.contiguous() for k, v in cls.m.state_dict().items()}, str(cls.pfad))
        cls.form = (2, 3, 4)
        cls.x, cls.a = torch.randn(1, 24, 128), torch.randn(1, 5, 128)
        cls.ctx, cls.actx = torch.randn(1, 7, 64), torch.randn(1, 7, 32)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _ref(self, t, fps):
        with torch.no_grad():
            return self.m(hidden_states=self.x, audio_hidden_states=self.a, encoder_hidden_states=self.ctx,
                          audio_encoder_hidden_states=self.actx, timestep=torch.tensor([t]), sigma=torch.tensor([t]),
                          num_frames=2, height=3, width=4, fps=fps, audio_num_frames=5, return_dict=False)

    def test_wie_diffusers(self):
        from videomotoren import ltx2_netz as L
        netz = L.Netz(L.laden(self.pfad, torch.float32), torch.device("cpu"), torch.float32,
                      {"koepfe": 2, "audio_koepfe": 2, "rope_type": "split"})
        for sigma, fps in ((0.6, 24.0), (0.25, 30.0)):
            rv, ra = self._ref(sigma * 1000.0, fps)
            with torch.no_grad():
                ov, oa = netz(self.x[0], self.a[0], self.ctx[0], self.actx[0], sigma, self.form, fps)
            self.assertLess(float((ov - rv[0]).abs().max()), 1e-4)
            self.assertLess(float((oa - ra[0]).abs().max()), 1e-4)
        with torch.no_grad():
            anders, _ = netz(self.x[0], self.a[0], self.ctx[0], self.actx[0], 0.5, self.form, 24.0)
        self.assertGreater(float((anders - self._ref(600.0, 24.0)[0][0]).abs().max()), 1e-2)

    def test_original_namen(self):
        """Lightricks-/GGUF-Namen werden auf diffusers-Namen abgebildet."""
        from videomotoren.ltx2_netz import diffusers_name
        for alt, neu in (("adaln_single.linear.weight", "time_embed.linear.weight"),
                         ("audio_adaln_single.emb.timestep_embedder.linear_1.bias",
                          "audio_time_embed.emb.timestep_embedder.linear_1.bias"),
                         ("prompt_adaln_single.linear.bias", "prompt_adaln.linear.bias"),
                         ("patchify_proj.weight", "proj_in.weight"), ("audio_patchify_proj.bias", "audio_proj_in.bias"),
                         ("transformer_blocks.3.scale_shift_table_a2v_ca_audio",
                          "transformer_blocks.3.audio_a2v_cross_attn_scale_shift_table"),
                         ("transformer_blocks.0.attn1.q_norm.weight", "transformer_blocks.0.attn1.norm_q.weight"),
                         ("audio_embeddings_connector.transformer_1d_blocks.1.attn1.to_gate_logits.weight",
                          "audio_connector.transformer_blocks.1.attn1.to_gate_logits.weight"),
                         ("av_ca_v2a_gate_adaln_single.linear.weight", "av_cross_attn_audio_v2a_gate.linear.weight")):
            self.assertEqual(diffusers_name(alt), neu)


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class LTX2Verbinder(unittest.TestCase):
    """Gemma-Zustände -> Projektion -> Verbinder mit Registern, links aufgefüllter Prompt."""

    def test_wie_diffusers(self):
        from diffusers.pipelines.ltx2.connectors import LTX2TextConnectors
        from bildmotoren.gewichte import Lin, Quelle
        from videomotoren import ltx2_netz as N
        torch.manual_seed(1)
        c = _zufall(LTX2TextConnectors(
            caption_channels=16, text_proj_in_factor=3, video_connector_num_attention_heads=2,
            video_connector_attention_head_dim=32, video_connector_num_layers=2, video_connector_num_learnable_registers=4,
            video_gated_attn=True, audio_connector_num_attention_heads=2, audio_connector_attention_head_dim=16,
            audio_connector_num_layers=2, audio_connector_num_learnable_registers=4, audio_gated_attn=True,
            rope_type="split", per_modality_projections=True, video_hidden_dim=64, audio_hidden_dim=32).eval(), 0.05)
        L, n = 8, 5
        hs = torch.randn(1, L, 16, 3)
        with torch.no_grad():
            rv, ra, _ = c(hs, torch.tensor([[0] * (L - n) + [1] * n]), padding_side="left")
        sd = {k: v.contiguous() for k, v in c.state_dict().items()}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.safetensors"
            save_file({k: v for k, v in sd.items() if not k.endswith("_text_proj_in.weight")}, str(p))
            with Quelle(p) as q:
                V = N._verbinder(N.Namen(q), "video_connector.", torch.float32)
                A = N._verbinder(N.Namen(q), "audio_connector.", torch.float32)
        echt = hs[0, L - n:]
        with torch.no_grad():
            xv = N.text_projektion(echt, Lin("dicht", sd["video_text_proj_in.weight"], n_out=64, n_in=48), 64, torch.float32)
            xa = N.text_projektion(echt, Lin("dicht", sd["audio_text_proj_in.weight"], n_out=32, n_in=48), 32, torch.float32)
            ov, oa = N.verbinder(V, xv, n, L, 2, torch.float32), N.verbinder(A, xa, n, L, 2, torch.float32)
        self.assertLess(float((ov - rv[0]).abs().max()), 1e-4)
        self.assertLess(float((oa - ra[0]).abs().max()), 1e-4)


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class LTX2VAE(unittest.TestCase):
    """Faltungs-VAE wie der Decoder von diffusers; Kacheln landen an der richtigen Stelle."""

    BLOECKE = [["res_x", {"num_layers": 1}], ["compress_space", {"multiplier": 2, "residual": True}],
               ["res_x", {"num_layers": 2}], ["compress_time", {"multiplier": 2, "residual": True}],
               ["res_x", {"num_layers": 2}], ["compress_all", {"multiplier": 1, "residual": True}],
               ["res_x", {"num_layers": 1}], ["compress_all", {"multiplier": 2, "residual": True}],
               ["res_x", {"num_layers": 1}]]

    @classmethod
    def setUpClass(cls):
        from diffusers import AutoencoderKLLTX2Video
        from videomotoren import vae_ltx2 as V
        torch.manual_seed(2)
        # Aufbau wie die Faltungs-VAE von LTX-2.5 (Raum, Zeit, alles ×1, alles ×2), Kanäle verkleinert
        cls.v = _zufall(AutoencoderKLLTX2Video(
            latent_channels=16, block_out_channels=(16, 32, 64, 64), decoder_block_out_channels=(16, 16, 32, 64),
            layers_per_block=(1, 1, 1, 1, 1), decoder_layers_per_block=(1, 1, 2, 2, 1),
            decoder_spatio_temporal_scaling=(True,) * 4, decoder_inject_noise=(False,) * 5,
            upsample_type=("spatial", "temporal", "spatiotemporal", "spatiotemporal"), upsample_residual=(True,) * 4,
            upsample_factor=(2, 1, 2, 2), patch_size=4, decoder_causal=False,
            decoder_spatial_padding_mode="reflect").eval(), 0.02)
        cls.tmp = tempfile.TemporaryDirectory()
        p = Path(cls.tmp.name) / "vae.safetensors"
        save_file({k: t.contiguous() for k, t in cls.v.state_dict().items()}, str(p))
        cls.ist = V.ist_ltx2_vae(p)
        cls.vae = V.laden(p)
        cls.vae["cfg"]["bloecke"] = cls.BLOECKE

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_dekodieren_wie_diffusers(self):
        from videomotoren import vae_ltx2 as V
        self.assertTrue(self.ist)
        z = torch.randn(1, 16, 3, 2, 3)
        with torch.no_grad():
            ref = self.v.decoder(z, None, causal=False)
        out = V.dekodieren(self.vae, z)
        self.assertEqual(tuple(out.shape), (1, 3, 17, 64, 96))
        self.assertLess(float((out - ref).abs().max()), 1e-4)

    def test_eine_kachel_gleich_ganz(self):
        from videomotoren import vae_ltx2 as V
        z = torch.randn(1, 16, 6, 6, 7)
        k = V.dekodieren_kacheln(self.vae, z, torch.device("cpu"), torch.float32, kachel=(6, 6, 7), ueber=(2, 2, 2))
        self.assertLess(float((k - V.dekodieren(self.vae, z)).abs().max()), 1e-5)

    def test_kacheln_form(self):
        from videomotoren import vae_ltx2 as V
        z = torch.randn(1, 16, 6, 6, 7)
        k = V.dekodieren_kacheln(self.vae, z, torch.device("cpu"), torch.float32, kachel=(4, 4, 4), ueber=(2, 2, 2))
        self.assertEqual(tuple(k.shape), (1, 3, 41, 192, 224))
        self.assertTrue(bool(torch.isfinite(k).all()))

    def test_original_namen(self):
        from videomotoren.vae_ltx2 import _original
        self.assertEqual(_original("decoder.mid_block.resnets.3.conv1.conv.weight"), "decoder.up_blocks.0.res_blocks.3.conv1.conv.weight")
        self.assertEqual(_original("decoder.up_blocks.0.upsamplers.0.conv.conv.bias"), "decoder.up_blocks.1.conv.conv.bias")
        self.assertEqual(_original("decoder.up_blocks.1.resnets.0.conv2.conv.weight"), "decoder.up_blocks.4.res_blocks.0.conv2.conv.weight")
        self.assertEqual(_original("latents_std"), "per_channel_statistics.std-of-means")


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class LTX2Ton(unittest.TestCase):
    """Ton-VAE (kausal, Pixel-Norm, Stereo), Vocoder mit SnakeBeta und Erweiterung auf 48 kHz wie diffusers."""

    def test_wie_diffusers(self):
        import json
        from diffusers import AutoencoderKLLTX2Audio
        from diffusers.pipelines.ltx2.vocoder import LTX2VocoderWithBWE
        from videomotoren import ton_ltx2 as T
        torch.manual_seed(3)
        a = _zufall(AutoencoderKLLTX2Audio(base_channels=32, output_channels=2, ch_mult=(1, 2), num_res_blocks=1,
                                           latent_channels=8, norm_type="pixel", causality_axis="height",
                                           mel_bins=16).eval(), 0.05)
        voc = _zufall(LTX2VocoderWithBWE(
            in_channels=32, hidden_channels=64, out_channels=2, upsample_kernel_sizes=[4, 4], upsample_factors=[2, 2],
            resnet_kernel_sizes=[3, 5], resnet_dilations=[[1, 3], [1, 3]], bwe_in_channels=32, bwe_hidden_channels=32,
            bwe_out_channels=2, bwe_upsample_kernel_sizes=[8, 5, 4, 4], bwe_upsample_factors=[4, 3, 2, 2],
            bwe_resnet_kernel_sizes=[3], bwe_resnet_dilations=[[1, 3]], filter_length=64, hop_length=16,
            window_length=64, num_mel_channels=16).eval(), 0.05)
        with torch.no_grad():
            voc.mel_stft.stft_fn.forward_basis.normal_(0, 0.1)
            voc.mel_stft.mel_basis.uniform_(0, 0.1)
        cfg = {"audio_vae": {"model": {"params": {"ddconfig": {"ch_mult": [1, 2], "mel_bins": 16, "out_ch": 2,
                                                               "causality_axis": "height"}}}},
               "vocoder": {"vocoder": {"upsample_rates": [2, 2], "resblock_kernel_sizes": [3, 5],
                                       "resblock_dilation_sizes": [[1, 3], [1, 3]]},
                           "bwe": {"upsample_rates": [4, 3, 2, 2], "resblock_kernel_sizes": [3],
                                   "resblock_dilation_sizes": [[1, 3]], "hop_length": 16,
                                   "input_sampling_rate": 16000, "output_sampling_rate": 48000}}}
        sd = {"audio_vae." + k: v.contiguous() for k, v in a.state_dict().items() if not k.startswith("encoder")}
        sd.update({"vocoder." + k: v.contiguous() for k, v in voc.state_dict().items()})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ton.safetensors"
            save_file(sd, str(p), metadata={"config": json.dumps(cfg)})
            self.assertTrue(T.ist_ltx2_ton(p))
            ton = T.laden(p)
        z = torch.randn(1, 8, 5, 4)
        with torch.no_grad():
            ref_mel = a.decode(z).sample
        self.assertLess(float((T.mel(ton, z) - ref_mel).abs().max()), 1e-4)
        m = torch.randn(1, 2, 12, 16)
        with torch.no_grad():
            ref = voc(m)[0]
        w, rate = T.wellenform(ton, m)
        self.assertEqual(rate, 48000)
        self.assertGreater(float((ref.abs() < 0.99).float().mean()), 0.5)       # nicht nur am Anschlag
        self.assertLess(float((w - ref).abs().max()), 1e-4)
        self.assertGreater(float((T.wellenform(ton, m * 1.01)[0] - ref).abs().max()), 1e-3)


try:
    import transformers
except Exception:                                    # pragma: no cover
    transformers = None


@unittest.skipIf(torch is None or transformers is None, "torch/transformers fehlen")
class Gemma4Text(unittest.TestCase):
    """Alle Zwischenzustände wie transformers – mit Fenster-Schichten, globaler Schicht (Schlüssel = Werte,
    proportionale RoPE) und Skalar je Schicht; der letzte Zustand nach der Schluss-Norm."""

    def test_wie_transformers(self):
        import json
        from transformers.models.gemma4_unified.configuration_gemma4_unified import Gemma4UnifiedTextConfig
        from transformers.models.gemma4_unified.modeling_gemma4_unified import Gemma4UnifiedTextModel
        from videomotoren import gemma_text as G
        typen = ["sliding_attention"] * 5 + ["full_attention"] + ["sliding_attention", "full_attention"]
        cfg = dict(vocab_size=300, hidden_size=64, intermediate_size=96, num_hidden_layers=8, num_attention_heads=2,
                   num_key_value_heads=1, head_dim=16, global_head_dim=32, num_global_key_value_heads=1,
                   attention_k_eq_v=True, sliding_window=4, layer_types=typen, max_position_embeddings=128,
                   hidden_activation="gelu_pytorch_tanh", num_kv_shared_layers=0, use_double_wide_mlp=False,
                   enable_moe_block=False, hidden_size_per_layer_input=0, vocab_size_per_layer_input=300,
                   rope_parameters={"full_attention": {"partial_rotary_factor": 0.25, "rope_theta": 1000000.0,
                                                       "rope_type": "proportional"},
                                    "sliding_attention": {"rope_theta": 10000.0, "rope_type": "default"}})
        torch.manual_seed(4)
        m = _zufall(Gemma4UnifiedTextModel(Gemma4UnifiedTextConfig(**dict(cfg, layer_types=list(typen)))).eval(), 0.1)
        with torch.no_grad():
            for n, b in m.named_buffers():
                if n.endswith("layer_scalar"):
                    b.uniform_(0.5, 1.5)
        ids = [2, 17, 99, 3, 250, 11, 42, 7, 8]                    # länger als das Fenster
        with torch.no_grad():
            ref = torch.stack([h[0] for h in m(torch.tensor([ids]), output_hidden_states=True).hidden_states], -1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.safetensors"
            save_file({"model." + k: v.contiguous() for k, v in m.state_dict().items()}, str(p),
                      metadata={"gemma_config": json.dumps({"text_config": cfg})})
            self.assertTrue(G.ist_gemma4(p))
            gew = G.laden(p, torch.float32)
        out = G.zustaende(gew, ids, torch.device("cpu"), torch.float32)
        self.assertEqual(tuple(out.shape), (9, 64, 9))
        self.assertLess(float((out - ref).abs().max()), 1e-4)

    def test_tokenizer_fehlt_klare_meldung(self):
        from videomotoren import gemma_text as G
        with tempfile.TemporaryDirectory() as d:
            for ordner in (None, Path(d)):
                with self.assertRaises(FileNotFoundError):
                    G.tokenizer({}, ordner)


@unittest.skipIf(torch is None or diffusers is None or transformers is None, "torch/diffusers/transformers fehlen")
class LTX2GanzesVideo(unittest.TestCase):
    """Alle fünf Teile als Mini-Dateien im Ordner: Weiche findet sie, rechnet ein Video mit Ton und schreibt ein MP4."""

    def setUp(self):
        import json
        from unittest import mock
        from diffusers import AutoencoderKLLTX2Audio, AutoencoderKLLTX2Video, LTX2VideoTransformer3DModel
        from diffusers.pipelines.ltx2.connectors import LTX2TextConnectors
        from diffusers.pipelines.ltx2.vocoder import LTX2VocoderWithBWE
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers.models.gemma4_unified.configuration_gemma4_unified import Gemma4UnifiedTextConfig
        from transformers.models.gemma4_unified.modeling_gemma4_unified import Gemma4UnifiedTextModel
        import videomotoren
        torch.manual_seed(5)
        self.tmp = tempfile.TemporaryDirectory()
        o = Path(self.tmp.name) / "Videomodelle"
        for u in ("LTXTest", "text_encoders", "vae"):
            (o / u).mkdir(parents=True)
        dit = _zufall(LTX2VideoTransformer3DModel(
            in_channels=128, out_channels=128, num_attention_heads=2, attention_head_dim=32, cross_attention_dim=64,
            audio_in_channels=128, audio_out_channels=128, audio_num_attention_heads=2, audio_attention_head_dim=16,
            audio_cross_attention_dim=32, num_layers=1, caption_channels=64, gated_attn=True, cross_attn_mod=True,
            audio_gated_attn=True, audio_cross_attn_mod=True, rope_type="split", ff_bias=False, audio_ff_bias=False,
            use_prompt_adaln_single=True, use_prompt_embeddings=False).eval(), 0.02)
        con = _zufall(LTX2TextConnectors(
            caption_channels=32, text_proj_in_factor=3, video_connector_num_attention_heads=2,
            video_connector_attention_head_dim=32, video_connector_num_layers=1, video_connector_num_learnable_registers=8,
            video_gated_attn=True, audio_connector_num_attention_heads=2, audio_connector_attention_head_dim=16,
            audio_connector_num_layers=1, audio_connector_num_learnable_registers=8, audio_gated_attn=True,
            rope_type="split", per_modality_projections=True, video_hidden_dim=64, audio_hidden_dim=32).eval(), 0.02)
        sd = {k: v.contiguous() for k, v in dit.state_dict().items()}
        sd.update({k: v.contiguous() for k, v in con.state_dict().items() if "_text_proj_in" not in k})
        kopf = {"transformer": {"num_attention_heads": 2, "audio_num_attention_heads": 2, "rope_type": "split",
                                "connector_num_attention_heads": 2, "audio_connector_num_attention_heads": 2}}
        save_file(sd, str(o / "LTXTest" / "ltx-test-distilled.safetensors"), metadata={"config": json.dumps(kopf)})
        # Gemma 4 mit Projektion und eingebettetem Tokenizer (2 Schichten -> 3 Zustände)
        gcfg = dict(vocab_size=50, hidden_size=32, intermediate_size=48, num_hidden_layers=2, num_attention_heads=2,
                    num_key_value_heads=1, head_dim=16, global_head_dim=16, num_global_key_value_heads=1,
                    attention_k_eq_v=True, sliding_window=8, layer_types=["sliding_attention", "full_attention"],
                    max_position_embeddings=64, hidden_activation="gelu_pytorch_tanh", num_kv_shared_layers=0,
                    use_double_wide_mlp=False, enable_moe_block=False, hidden_size_per_layer_input=0,
                    vocab_size_per_layer_input=50,
                    rope_parameters={"full_attention": {"partial_rotary_factor": 0.25, "rope_theta": 1e6,
                                                        "rope_type": "proportional"},
                                     "sliding_attention": {"rope_theta": 1e4, "rope_type": "default"}})
        g = _zufall(Gemma4UnifiedTextModel(Gemma4UnifiedTextConfig(**gcfg)).eval(), 0.05)
        tok = Tokenizer(models.WordLevel({"<pad>": 0, "<bos>": 2, "<unk>": 3, "a": 4, "red": 5, "ball": 6}, unk_token="<unk>"))
        tok.pre_tokenizer = pre_tokenizers.Whitespace()
        gsd = {"model." + k: v.contiguous() for k, v in g.state_dict().items()}
        cs = con.state_dict()
        gsd["text_embedding_projection.video_aggregate_embed.weight"] = cs["video_text_proj_in.weight"].contiguous()
        gsd["text_embedding_projection.video_aggregate_embed.bias"] = torch.zeros(64)
        gsd["text_embedding_projection.audio_aggregate_embed.weight"] = cs["audio_text_proj_in.weight"].contiguous()
        gsd["text_embedding_projection.audio_aggregate_embed.bias"] = torch.zeros(32)
        gsd["tokenizer_json"] = torch.tensor(list(tok.to_str().encode("utf-8")), dtype=torch.uint8)
        save_file(gsd, str(o / "text_encoders" / "gemma4-test-with-proj.safetensors"),
                  metadata={"gemma_config": json.dumps({"text_config": gcfg})})
        # Video-VAE (Faltungs-Fassung) mit 128 Latent-Kanälen
        v = _zufall(AutoencoderKLLTX2Video(
            latent_channels=128, block_out_channels=(16, 32, 64, 64), decoder_block_out_channels=(16, 16, 32, 64),
            layers_per_block=(1, 1, 1, 1, 1), decoder_layers_per_block=(1, 1, 1, 1, 1),
            decoder_spatio_temporal_scaling=(True,) * 4, decoder_inject_noise=(False,) * 5,
            upsample_type=("spatial", "temporal", "spatiotemporal", "spatiotemporal"), upsample_residual=(True,) * 4,
            upsample_factor=(2, 1, 2, 2), patch_size=4, decoder_causal=False,
            decoder_spatial_padding_mode="reflect").eval(), 0.02)
        blk = [["res_x", {"num_layers": 1}], ["compress_space", {"multiplier": 2, "residual": True}],
               ["res_x", {"num_layers": 1}], ["compress_time", {"multiplier": 2, "residual": True}],
               ["res_x", {"num_layers": 1}], ["compress_all", {"multiplier": 1, "residual": True}],
               ["res_x", {"num_layers": 1}], ["compress_all", {"multiplier": 2, "residual": True}],
               ["res_x", {"num_layers": 1}]]
        save_file({k: x.contiguous() for k, x in v.state_dict().items()}, str(o / "vae" / "ltx-test-video-vae-conv.safetensors"),
                  metadata={"config": json.dumps({"vae": {"decoder_blocks": blk, "spatial_padding_mode": "reflect",
                                                          "causal_decoder": False, "patch_size": 4}})})
        # Ton-VAE + Vocoder
        a = _zufall(AutoencoderKLLTX2Audio(base_channels=32, output_channels=2, ch_mult=(1, 2, 4), num_res_blocks=1,
                                           latent_channels=8, norm_type="pixel", causality_axis="height",
                                           mel_bins=64).eval(), 0.02)
        voc = _zufall(LTX2VocoderWithBWE(
            in_channels=128, hidden_channels=64, out_channels=2, upsample_kernel_sizes=[10, 4, 4, 4],
            upsample_factors=[5, 2, 2, 4], resnet_kernel_sizes=[3], resnet_dilations=[[1, 3]], bwe_in_channels=128,
            bwe_hidden_channels=32, bwe_out_channels=2, bwe_upsample_kernel_sizes=[12, 11, 4, 4, 4],
            bwe_upsample_factors=[6, 5, 2, 2, 2], bwe_resnet_kernel_sizes=[3], bwe_resnet_dilations=[[1, 3]],
            filter_length=512, hop_length=80, window_length=512, num_mel_channels=64).eval(), 0.01)
        with torch.no_grad():
            voc.mel_stft.stft_fn.forward_basis.normal_(0, 0.01)
            voc.mel_stft.mel_basis.uniform_(0, 0.01)
        tsd = {"audio_vae." + k: x.contiguous() for k, x in a.state_dict().items() if not k.startswith("encoder")}
        tsd.update({"vocoder." + k: x.contiguous() for k, x in voc.state_dict().items()})
        # Normierung je gepacktem Ton-Kanal (8 Kanäle × 16 Mel) wie im echten Modell
        tsd["audio_vae.latents_mean"] = torch.randn(128) * 0.1
        tsd["audio_vae.latents_std"] = torch.rand(128) + 0.5
        tcfg = {"audio_vae": {"model": {"params": {"ddconfig": {"ch_mult": [1, 2, 4], "mel_bins": 64, "out_ch": 2,
                                                                "causality_axis": "height"}}}},
                "vocoder": {"vocoder": {"upsample_rates": [5, 2, 2, 4], "resblock_kernel_sizes": [3],
                                        "resblock_dilation_sizes": [[1, 3]]},
                            "bwe": {"upsample_rates": [6, 5, 2, 2, 2], "resblock_kernel_sizes": [3],
                                    "resblock_dilation_sizes": [[1, 3]], "hop_length": 80,
                                    "input_sampling_rate": 16000, "output_sampling_rate": 48000}}}
        save_file(tsd, str(o / "vae" / "ltx-test-audio-vae.safetensors"), metadata={"config": json.dumps(tcfg)})
        self.o = o
        self.p = [mock.patch.object(videomotoren, "ORDNER", [o])]
        for x in self.p:
            x.start()
        videomotoren._PASST.clear()

    def tearDown(self):
        import videomotoren
        for x in self.p:
            x.stop()
        videomotoren._PASST.clear()
        videomotoren.entladen()
        self.tmp.cleanup()

    def test_finden(self):
        import videomotoren
        t = videomotoren.teile("LTXTest")
        self.assertEqual(t["bauart"], "ltx2")
        self.assertEqual((t["text"].name, t["vae"].name, t["ton"].name),
                         ("gemma4-test-with-proj.safetensors", "ltx-test-video-vae-conv.safetensors",
                          "ltx-test-audio-vae.safetensors"))
        self.assertIsNone(videomotoren.missing_reason("LTXTest"))

    def test_fehlende_ton_vae_wird_genannt(self):
        import videomotoren
        (self.o / "vae" / "ltx-test-audio-vae.safetensors").unlink()
        videomotoren._PASST.clear()
        self.assertIn("Ton-VAE", videomotoren.missing_reason("LTXTest"))

    def test_video_mit_ton(self):
        from unittest import mock
        import videomotoren
        ziel = Path(self.tmp.name) / "film.mp4"
        with mock.patch("profilordner.video_pfad", lambda *a, **k: ziel), \
                mock.patch("bildmotoren.gemeinsam.geraet", lambda: (torch.device("cpu"), torch.float32)), \
                mock.patch.object(videomotoren, "einstellung", lambda s: videomotoren.EINSTELLUNGEN[s][0]):
            pfad = videomotoren.generate("a red ball", sekunden=1.0, size=(64, 64), seed=1, modell="LTXTest")
        self.assertEqual(Path(pfad), ziel)
        self.assertGreater(ziel.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
