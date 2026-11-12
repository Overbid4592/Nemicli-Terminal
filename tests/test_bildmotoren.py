"""Bild-Motoren neben Krea 2 (engines/bildmotoren) – geprüft gegen diffusers und transformers
mit winzigen Zufallsmodellen. Ohne diffusers werden die Vergleiche übersprungen."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
try:
    import transformers
except Exception:                                    # pragma: no cover
    transformers = None

Q8_0 = 8


def q8_0(w) -> tuple:
    """Matrix -> (GGML-Typ, Form, Rohbytes) im Q8_0-Format (32 Werte: fp16-Skala + 32 int8)."""
    import numpy as np
    x = w.detach().float().reshape(-1, 32)
    d = x.abs().amax(1, keepdim=True) / 127.0
    q = torch.where(d > 0, (x / d.clamp(min=1e-12)).round(), torch.zeros_like(x)).clamp(-127, 127).to(torch.int8)
    roh = b"".join(np.float16(float(s)).tobytes() + r.numpy().tobytes() for s, r in zip(d[:, 0], q))
    return (Q8_0, tuple(w.shape), roh)


def _zufall(modul, staerke):
    with torch.no_grad():
        for p in modul.parameters():
            p.add_(torch.randn_like(p) * staerke)
    return modul


def _comfy_namen(sd: dict) -> dict:
    """diffusers-Namen eines Z-Image-DiT -> ComfyUI-Namen (fusioniertes qkv, Checkpoint-Vorsatz)."""
    aus = {}
    for k, v in sd.items():
        n = k.replace("all_x_embedder.2-1.", "x_embedder.").replace("all_final_layer.2-1.", "final_layer.")
        n = n.replace(".attention.to_out.0.", ".attention.out.").replace(".attention.norm_q.", ".attention.q_norm.")
        n = n.replace(".attention.norm_k.", ".attention.k_norm.")
        if ".attention.to_q." in n:
            aus[n.replace(".attention.to_q.weight", ".attention.qkv.weight")] = torch.cat(
                [sd[k], sd[k.replace("to_q", "to_k")], sd[k.replace("to_q", "to_v")]])
        elif ".attention.to_k." not in n and ".attention.to_v." not in n:
            aus[n] = v
    return aus


def _zimage(cap_dim=32):
    from diffusers import ZImageTransformer2DModel
    torch.manual_seed(0)
    m = ZImageTransformer2DModel(all_patch_size=(2,), all_f_patch_size=(1,), in_channels=16, dim=256, n_layers=2,
                                 n_refiner_layers=1, n_heads=2, n_kv_heads=2, cap_feat_dim=cap_dim,
                                 axes_dims=[32, 48, 48], axes_lens=[1536, 512, 512]).eval()
    return _zufall(m, 0.05)


def _vae():
    from diffusers import AutoencoderKL
    torch.manual_seed(1)
    v = AutoencoderKL(in_channels=3, out_channels=3, latent_channels=16, block_out_channels=(32, 32, 64, 64),
                      down_block_types=("DownEncoderBlock2D",) * 4, up_block_types=("UpDecoderBlock2D",) * 4,
                      layers_per_block=2, norm_num_groups=32, use_quant_conv=False, use_post_quant_conv=False).eval()
    return _zufall(v, 0.02)


def _qwen3(vocab=300, hidden=64):
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.manual_seed(2)
    cfg = Qwen3Config(vocab_size=vocab, hidden_size=hidden, intermediate_size=128, num_hidden_layers=4,
                      num_attention_heads=4, num_key_value_heads=2, head_dim=16, rope_theta=1_000_000.0,
                      rms_norm_eps=1e-6, tie_word_embeddings=True, max_position_embeddings=1024)
    return _zufall(Qwen3ForCausalLM(cfg).eval(), 0.1)


def _byte_tokens() -> list[str]:
    """GPT-2-Byte-Alphabet (256 Zeichen) als Tokenliste."""
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("\xa1"), ord("\xac") + 1)) + \
        list(range(ord("\xae"), ord("\xff") + 1))
    cs, n = bs[:], 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return [chr(c) for _, c in sorted(zip(bs, cs))]


def _qwen3_gguf(pfad, hf):
    """Mini-Qwen3 als GGUF mit Byte-Tokenizer und den Chat-Marken."""
    import gguf_testbau as B
    meta, t = B.umwandeln(hf, "qwen3")
    tokens = _byte_tokens() + ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]
    meta.update({"tokenizer.ggml.model": "gpt2", "tokenizer.ggml.pre": "qwen2", "tokenizer.ggml.tokens": tokens,
                 "tokenizer.ggml.token_type": [1] * 256 + [3, 3, 3], "tokenizer.ggml.merges": ["a b"]})
    B.schreibe_gguf(pfad, meta, t)


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class ZImageNetz(unittest.TestCase):
    """Das DiT rechnet wie diffusers – mit diffusers-, ComfyUI- und GGUF-Namen."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        cls.m = _zimage()
        cls.sd = {k: v.contiguous() for k, v in cls.m.state_dict().items()}
        torch.manual_seed(3)
        cls.lat = torch.randn(1, 16, 12, 12)          # 36 Bild-Token: Auffüllen auf 64 wird mitgeprüft
        cls.cap = torch.randn(7, 32)
        with torch.no_grad():
            cls.ref = cls.m([cls.lat[0].unsqueeze(1)], torch.tensor([0.3]), [cls.cap],
                            return_dict=False)[0][0].squeeze(1)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _netz(self, pfad):
        from bildmotoren import zimage
        ram = zimage.laden(pfad, torch.float32)
        with torch.no_grad():
            return zimage.Netz(ram, self.cap, torch.device("cpu"), torch.float32)(self.lat, 0.7)[0]

    def test_diffusers_namen(self):
        save_file(self.sd, str(self.d / "diff.safetensors"))
        self.assertLess(float((self._netz(self.d / "diff.safetensors") - self.ref).abs().max()), 1e-4)

    def test_comfy_namen_mit_vorsatz(self):
        sd = {"model.diffusion_model." + k: v for k, v in _comfy_namen(self.sd).items()}
        save_file(sd, str(self.d / "comfy.safetensors"))
        self.assertLess(float((self._netz(self.d / "comfy.safetensors") - self.ref).abs().max()), 1e-4)

    def test_gguf_q8_0(self):
        import gguf_testbau as B
        t = {k: (q8_0(v) if v.dim() == 2 and v.shape[1] % 32 == 0 and min(v.shape) > 1 else v)
             for k, v in _comfy_namen(self.sd).items()}
        B.schreibe_gguf(str(self.d / "z.gguf"), {"general.architecture": "lumina2"}, t)
        out = self._netz(self.d / "z.gguf")
        self.assertLess(float((out - self.ref).abs().max()), 0.03 * float(self.ref.abs().max()))

    def test_erkennen(self):
        from bildmotoren import zimage
        from bildmotoren.gewichte import namen, ohne_praefix
        save_file(self.sd, str(self.d / "e.safetensors"))
        self.assertTrue(zimage.erkennen(ohne_praefix(namen(self.d / "e.safetensors"))[0]))
        save_file({"model.diffusion_model.txtfusion.x": torch.zeros(2)}, str(self.d / "krea.safetensors"))
        self.assertFalse(zimage.erkennen(ohne_praefix(namen(self.d / "krea.safetensors"))[0]))

    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), "ohne CUDA")
    def test_bloecke_nachschieben_gleich_ganz(self):
        """Wenig Grafikspeicher: Blöcke werden nachgeschoben – das Ergebnis bleibt gleich."""
        from bildmotoren import zimage
        save_file(self.sd, str(self.d / "g.safetensors"))
        ram = zimage.laden(self.d / "g.safetensors", torch.float32)
        cuda = torch.device("cuda")
        with torch.no_grad():
            ganz = zimage.Netz(ram, self.cap, cuda, torch.float32)
            teil = zimage.Netz(ram, self.cap, cuda, torch.float32, platz=0)
            self.assertEqual(teil.schichten.bleiben, 0)
            a, b = ganz(self.lat.to(cuda), 0.7), teil(self.lat.to(cuda), 0.7)
        self.assertLess(float((a - b).abs().max()), 1e-5)


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class Zeitplan(unittest.TestCase):
    def test_sigmas_wie_flowmatch_euler(self):
        from diffusers import FlowMatchEulerDiscreteScheduler
        from bildmotoren.gemeinsam import sigmas_linear
        for n in (4, 9, 20):
            s = FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, use_dynamic_shifting=False, shift=3.0)
            s.set_timesteps(sigmas=torch.linspace(1.0, 1 / n, n).tolist())
            self.assertLess(float((s.sigmas - sigmas_linear(n, 3.0)).abs().max()), 1e-6)


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class FluxVAE(unittest.TestCase):
    def test_dekodieren_wie_diffusers(self):
        from bildmotoren import vae_flux
        v = _vae()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "vae.safetensors"
            save_file({k: x.contiguous() for k, x in v.state_dict().items()}, str(p))
            self.assertTrue(vae_flux.ist_flux_vae(p))
            z = torch.randn(1, 16, 8, 8)
            with torch.no_grad():
                ref = v.decode(z).sample
            out = vae_flux.dekodieren(vae_flux.laden(p), z)
        self.assertLess(float((out - ref).abs().max()), 1e-4)

    def test_original_namen(self):
        """diffusers-Namen werden zu den Namen von ae.safetensors (Stufen rückwärts gezählt)."""
        from bildmotoren.vae_flux import _original_name
        f = lambda k: _original_name(k, 4)                    # noqa: E731
        self.assertEqual(f("decoder.up_blocks.0.resnets.2.conv1.weight"), "decoder.up.3.block.2.conv1.weight")
        self.assertEqual(f("decoder.up_blocks.3.resnets.0.conv_shortcut.bias"), "decoder.up.0.block.0.nin_shortcut.bias")
        self.assertEqual(f("decoder.up_blocks.1.upsamplers.0.conv.weight"), "decoder.up.2.upsample.conv.weight")
        self.assertEqual(f("decoder.mid_block.resnets.1.norm2.bias"), "decoder.mid.block_2.norm2.bias")
        self.assertEqual(f("decoder.mid_block.attentions.0.to_out.0.weight"), "decoder.mid.attn_1.proj_out.weight")
        self.assertEqual(f("decoder.conv_norm_out.weight"), "decoder.norm_out.weight")
        self.assertEqual(f("decoder.up.3.block.0.conv1.weight"), "decoder.up.3.block.0.conv1.weight")


@unittest.skipIf(torch is None or transformers is None, "torch/transformers fehlen")
class Qwen3Text(unittest.TestCase):
    ids = [5, 17, 99, 3, 250, 11, 42]

    def test_safetensors_vorletzte_schicht(self):
        from bildmotoren import textenc
        hf = _qwen3()
        with torch.no_grad():
            ref = hf(torch.tensor([self.ids]), output_hidden_states=True).hidden_states[-2][0]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "te.safetensors"
            save_file({k: v.contiguous() for k, v in hf.state_dict().items() if k != "lm_head.weight"}, str(p))
            f = textenc.qwen3_form(p)
            self.assertEqual((f["hidden"], f["schichten"]), (64, 4))
            gew = textenc.qwen3_laden(p, f["schichten"] - 1, torch.float32)
            out = textenc.qwen3_zustand(gew, self.ids, torch.device("cpu"), torch.float32)
        self.assertLess(float((out - ref).abs().max()), 1e-4)

    def test_gguf_vorletzte_schicht(self):
        from bildmotoren import textenc
        hf = _qwen3()
        with torch.no_grad():
            ref = hf(torch.tensor([self.ids]), output_hidden_states=True).hidden_states[-2][0]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "te.gguf"
            _qwen3_gguf(p, hf)
            self.assertEqual(textenc.qwen3_form(p)["hidden"], 64)
            gew = textenc.qwen3_laden(p, 3, torch.float32)
            out = textenc.qwen3_zustand(gew, self.ids, torch.device("cpu"), torch.float32)
        self.assertLess(float((out - ref).abs().max()), 1e-4)


@unittest.skipIf(torch is None, "torch fehlt")
class Formate(unittest.TestCase):
    """Lin rechnet jedes gespeicherte Format wie die dichte Matrix."""

    def setUp(self):
        torch.manual_seed(4)
        self.w = torch.randn(64, 128)
        self.x = torch.randn(3, 128)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _lin(self, sd, meta=None, name="l"):
        from bildmotoren.gewichte import Quelle
        p = Path(self.tmp.name) / "f.safetensors"
        save_file(sd, str(p), metadata=meta)
        with Quelle(p) as q:
            return q.lin(name, torch.float32)

    def test_fp8_mit_skala(self):
        s = self.w.abs().amax() / 448.0
        lin = self._lin({"l.weight": (self.w / s).to(torch.float8_e4m3fn), "l.weight_scale": s.reshape(())})
        self.assertEqual(lin.art, "fp8")
        self.assertLess(float((lin(self.x) - self.x @ self.w.t()).abs().max()), 0.1 * float((self.x @ self.w.t()).abs().max()))

    def test_int8_je_zeile(self):
        s = self.w.abs().amax(1, keepdim=True) / 127.0
        q = (self.w / s).round().to(torch.int8)
        cq = torch.tensor(list(b'{"format": "int8_tensorwise"}'), dtype=torch.uint8)
        lin = self._lin({"l.weight": q, "l.weight_scale": s, "l.comfy_quant": cq})
        self.assertEqual(lin.art, "int8")
        self.assertTrue(torch.allclose(lin(self.x), self.x @ (q.float() * s).t(), atol=1e-4))

    def test_int8_convrot_wird_abgelehnt(self):
        from bildmotoren.gewichte import FormatFehler
        cq = torch.tensor(list(b'{"format": "int8_tensorwise", "convrot": true}'), dtype=torch.uint8)
        with self.assertRaises(FormatFehler):
            self._lin({"l.weight": torch.zeros(64, 128, dtype=torch.int8), "l.weight_scale": torch.ones(64, 1),
                       "l.comfy_quant": cq})

    def test_nvfp4_aus_dem_dateikopf(self):
        """NVFP4 wie in den ComfyUI-Dateien: Format steht im Kopf (_quantization_metadata), Skalen gekachelt."""
        import json
        import krea
        q, bs, gs = krea._fp4_quant(self.w)
        meta = {"_quantization_metadata": json.dumps({"layers": {"l": {"format": "nvfp4"}}})}
        lin = self._lin({"l.weight": q, "l.weight_scale": bs, "l.weight_scale_2": gs.reshape(())}, meta)
        self.assertEqual(lin.art, "nvfp4")
        ref = self.x @ self.w.t()
        self.assertLess(float((lin(self.x) - ref).abs().max()), 0.25 * float(ref.abs().max()))
        if torch.cuda.is_available() and krea._fp4_bereit(torch.device("cuda")):
            karte = lin.zu("cuda")                   # Blackwell: FP4-Kerne (scaled_mm) statt Entpacken
            x = self.x.to("cuda", torch.bfloat16)
            schnell = karte(x).float()
            entpackt = (x @ karte.gewicht(torch.bfloat16).t()).float()
            self.assertLess(float((schnell - entpackt).abs().max()), 0.25 * float(entpackt.abs().max()))


def _flux2(joint=96):
    from diffusers import Flux2Transformer2DModel
    torch.manual_seed(5)
    m = Flux2Transformer2DModel(patch_size=1, in_channels=128, num_layers=2, num_single_layers=2, attention_head_dim=128,
                                num_attention_heads=2, joint_attention_dim=joint, timestep_guidance_channels=256,
                                mlp_ratio=3.0, axes_dims_rope=(32, 32, 32, 32), rope_theta=2000, eps=1e-6,
                                guidance_embeds=False).eval()
    return _zufall(m, 0.05)


_F2_EINFACH = {"x_embedder.": "img_in.", "context_embedder.": "txt_in.",
               "time_guidance_embed.timestep_embedder.linear_1.": "time_in.in_layer.",
               "time_guidance_embed.timestep_embedder.linear_2.": "time_in.out_layer.",
               "double_stream_modulation_img.linear.": "double_stream_modulation_img.lin.",
               "double_stream_modulation_txt.linear.": "double_stream_modulation_txt.lin.",
               "single_stream_modulation.linear.": "single_stream_modulation.lin.", "proj_out.": "final_layer.linear."}
_F2_DOPPEL = {"attn.norm_q.weight": "img_attn.norm.query_norm.scale", "attn.norm_k.weight": "img_attn.norm.key_norm.scale",
              "attn.to_out.0.weight": "img_attn.proj.weight", "ff.linear_in.weight": "img_mlp.0.weight",
              "ff.linear_out.weight": "img_mlp.2.weight", "attn.norm_added_q.weight": "txt_attn.norm.query_norm.scale",
              "attn.norm_added_k.weight": "txt_attn.norm.key_norm.scale", "attn.to_add_out.weight": "txt_attn.proj.weight",
              "ff_context.linear_in.weight": "txt_mlp.0.weight", "ff_context.linear_out.weight": "txt_mlp.2.weight"}
_F2_EINZEL = {"attn.to_qkv_mlp_proj.weight": "linear1.weight", "attn.to_out.weight": "linear2.weight",
              "attn.norm_q.weight": "norm.query_norm.scale", "attn.norm_k.weight": "norm.key_norm.scale"}


def _flux2_original(sd: dict) -> dict:
    """diffusers-Namen eines FLUX.2-DiT -> Original-/ComfyUI-Namen (Umkehrung von diffusers' Umwandlung)."""
    aus = {}
    for k, v in sd.items():
        if k == "norm_out.linear.weight":
            scale, shift = v.chunk(2, 0)
            aus["final_layer.adaLN_modulation.1.weight"] = torch.cat([shift, scale])
        elif k.startswith("transformer_blocks."):
            _, i, rest = k.split(".", 2)
            p = f"transformer_blocks.{i}."
            if rest == "attn.to_q.weight":
                aus[f"double_blocks.{i}.img_attn.qkv.weight"] = torch.cat(
                    [v, sd[p + "attn.to_k.weight"], sd[p + "attn.to_v.weight"]])
            elif rest == "attn.add_q_proj.weight":
                aus[f"double_blocks.{i}.txt_attn.qkv.weight"] = torch.cat(
                    [v, sd[p + "attn.add_k_proj.weight"], sd[p + "attn.add_v_proj.weight"]])
            elif rest in _F2_DOPPEL:
                aus[f"double_blocks.{i}.{_F2_DOPPEL[rest]}"] = v
        elif k.startswith("single_transformer_blocks."):
            _, i, rest = k.split(".", 2)
            aus[f"single_blocks.{i}.{_F2_EINZEL[rest]}"] = v
        else:
            aus[next((n + k[len(a):] for a, n in _F2_EINFACH.items() if k.startswith(a)), k)] = v
    return aus


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class Flux2Netz(unittest.TestCase):
    """FLUX.2 rechnet wie diffusers – mit diffusers-, Original- und GGUF-Namen."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        cls.m = _flux2()
        cls.sd = {k: v.contiguous() for k, v in cls.m.state_dict().items()}
        torch.manual_seed(6)
        cls.h, cls.w = 3, 5
        cls.x = torch.randn(1, cls.h * cls.w, 128)
        cls.ctx = torch.randn(9, 96)
        img_ids = torch.cartesian_prod(torch.arange(1), torch.arange(cls.h), torch.arange(cls.w), torch.arange(1))
        txt_ids = torch.cartesian_prod(torch.arange(1), torch.arange(1), torch.arange(1), torch.arange(9))
        with torch.no_grad():
            cls.ref = cls.m(hidden_states=cls.x, encoder_hidden_states=cls.ctx[None], timestep=torch.tensor([0.6]),
                            img_ids=img_ids, txt_ids=txt_ids, return_dict=False)[0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _netz(self, pfad):
        from bildmotoren import flux2
        netz = flux2.Netz(flux2.laden(pfad, torch.float32), self.ctx, torch.device("cpu"), torch.float32)
        pos = torch.zeros(self.h * self.w, 4, dtype=torch.long)
        pos[:, 1] = torch.arange(self.h).repeat_interleave(self.w)
        pos[:, 2] = torch.arange(self.w).repeat(self.h)
        netz.bild_pos = pos
        with torch.no_grad():
            return netz(self.x, 0.6)

    def test_diffusers_namen(self):
        save_file(self.sd, str(self.d / "diff.safetensors"))
        self.assertLess(float((self._netz(self.d / "diff.safetensors") - self.ref).abs().max()), 1e-4)

    def test_original_namen(self):
        from bildmotoren import flux2
        from bildmotoren.gewichte import namen, ohne_praefix
        save_file(_flux2_original(self.sd), str(self.d / "orig.safetensors"))
        self.assertTrue(flux2.erkennen(ohne_praefix(namen(self.d / "orig.safetensors"))[0]))
        self.assertLess(float((self._netz(self.d / "orig.safetensors") - self.ref).abs().max()), 1e-4)

    def test_gguf_q8_0(self):
        import gguf_testbau as B
        t = {k: (q8_0(v) if v.dim() == 2 and v.shape[1] % 32 == 0 and min(v.shape) > 1 else v)
             for k, v in _flux2_original(self.sd).items()}
        B.schreibe_gguf(str(self.d / "f.gguf"), {"general.architecture": "flux"}, t)
        out = self._netz(self.d / "f.gguf")
        self.assertLess(float((out - self.ref).abs().max()), 0.03 * float(self.ref.abs().max()))

    def test_zeitplan_wie_diffusers(self):
        from diffusers import FlowMatchEulerDiscreteScheduler
        from diffusers.pipelines.flux2.pipeline_flux2_klein import compute_empirical_mu
        from bildmotoren import flux2
        from bildmotoren.gemeinsam import sigmas_flux
        for n_bild, n in ((256, 4), (4096, 4), (4096, 50), (6000, 20)):
            mu = flux2.empirisches_mu(n_bild, n)
            self.assertAlmostEqual(mu, compute_empirical_mu(n_bild, n), places=6)
            s = FlowMatchEulerDiscreteScheduler(use_dynamic_shifting=True, time_shift_type="exponential", shift=3.0)
            s.set_timesteps(sigmas=torch.linspace(1.0, 1 / n, n).tolist(), mu=mu)
            self.assertLess(float((s.sigmas - sigmas_flux(n, mu)).abs().max()), 1e-5)

    def test_vae_32_kanaele(self):
        from diffusers import AutoencoderKLFlux2
        from bildmotoren import vae_flux
        torch.manual_seed(7)
        v = _zufall(AutoencoderKLFlux2(block_out_channels=(32, 32, 64, 64), latent_channels=32, layers_per_block=2,
                                       norm_num_groups=32).eval(), 0.02)
        p = self.d / "f2vae.safetensors"
        save_file({k: x.contiguous() for k, x in v.state_dict().items()}, str(p))
        self.assertEqual(vae_flux.kanaele(p), 32)
        self.assertFalse(vae_flux.ist_flux_vae(p))
        sd = vae_flux.laden(p)
        self.assertIn("bn.running_mean", sd)
        z = torch.randn(1, 32, 8, 8)
        with torch.no_grad():
            ref = v.decode(z).sample
        self.assertLess(float((vae_flux.dekodieren(sd, z) - ref).abs().max()), 1e-4)

    def test_latents_zurueck_wie_pipeline(self):
        """Token -> Normierung (BatchNorm-Werte) -> 32 Kanäle in doppelter Größe, wie Flux2KleinPipeline."""
        from diffusers.pipelines.flux2.pipeline_flux2_klein import Flux2KleinPipeline as P
        h, w = 3, 5
        x = torch.randn(1, h * w, 128)
        mittel, var = torch.randn(128), torch.rand(128) + 0.5
        ids = torch.cartesian_prod(torch.arange(1), torch.arange(h), torch.arange(w), torch.arange(1))[None]
        ref = P._unpack_latents_with_ids(x, ids, h, w)
        ref = P._unpatchify_latents(ref * torch.sqrt(var.view(1, -1, 1, 1) + 1e-4) + mittel.view(1, -1, 1, 1))
        z = x.permute(0, 2, 1).reshape(1, 128, h, w) * torch.sqrt(var.view(1, -1, 1, 1) + 1e-4) + mittel.view(1, -1, 1, 1)
        z = z.reshape(1, 32, 2, 2, h, w).permute(0, 1, 4, 2, 5, 3).reshape(1, 32, 2 * h, 2 * w)
        self.assertTrue(torch.allclose(z, ref, atol=1e-6))


@unittest.skipIf(torch is None or transformers is None, "torch/transformers fehlen")
class Qwen3Abgriffe(unittest.TestCase):
    """FLUX.2: drei Zwischenzustände, Text auf feste Länge aufgefüllt – wie transformers mit attention_mask."""

    def test_aufgefuellt_safetensors_und_gguf(self):
        from bildmotoren import textenc
        hf = _qwen3()
        ids = [5, 17, 99, 3, 250, 11] + [0] * 10
        maske = torch.tensor([[1] * 6 + [0] * 10])
        with torch.no_grad():
            hs = hf(torch.tensor([ids]), attention_mask=maske, output_hidden_states=True).hidden_states
        ref = [hs[k][0] for k in (1, 2, 3)]
        with tempfile.TemporaryDirectory() as d:
            st = Path(d) / "te.safetensors"
            save_file({k: v.contiguous() for k, v in hf.state_dict().items() if k != "lm_head.weight"}, str(st))
            gg = Path(d) / "te.gguf"
            _qwen3_gguf(gg, hf)
            for p in (st, gg):
                gew = textenc.qwen3_laden(p, 3, torch.float32)
                aus = textenc.qwen3_zustaende(gew, ids, torch.device("cpu"), torch.float32, (1, 2, 3), echt=6)
                for a, r in zip(aus, ref):
                    self.assertLess(float((a - r).abs().max()), 1e-4, p.suffix)
            ids = textenc.gguf_tokenizer(gg)("<|im_start|>ab<|im_end|>")
        self.assertEqual((ids[0], ids[-1]), (256, 257))


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class QwenImageNetz(unittest.TestCase):
    """Qwen-Image rechnet wie diffusers (safetensors und GGUF); Zeitplan, Packen und CFG wie die Pipeline."""

    @classmethod
    def setUpClass(cls):
        from diffusers import QwenImageTransformer2DModel
        torch.manual_seed(9)
        cls.m = _zufall(QwenImageTransformer2DModel(patch_size=2, in_channels=64, out_channels=16, num_layers=2,
                                                    attention_head_dim=128, num_attention_heads=2, joint_attention_dim=64,
                                                    axes_dims_rope=(16, 56, 56)).eval(), 0.05)
        cls.sd = {k: v.contiguous() for k, v in cls.m.state_dict().items()}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        cls.h, cls.w = 3, 5
        cls.x = torch.randn(1, cls.h * cls.w, 64)
        cls.ctx = torch.randn(7, 64)
        with torch.no_grad():
            cls.ref = cls.m(hidden_states=cls.x, encoder_hidden_states=cls.ctx[None],
                            encoder_hidden_states_mask=torch.ones(1, 7, dtype=torch.long), timestep=torch.tensor([0.6]),
                            img_shapes=[[(1, cls.h, cls.w)]], return_dict=False)[0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _netz(self, pfad):
        from bildmotoren import qwenimage
        netz = qwenimage.Netz(qwenimage.laden(pfad, torch.float32), torch.device("cpu"), torch.float32)
        with torch.no_grad():
            return netz(self.x, 0.6, netz.kontext(self.ctx), self.h, self.w)

    def test_safetensors(self):
        from bildmotoren import qwenimage
        from bildmotoren.gewichte import namen
        save_file(self.sd, str(self.d / "qi.safetensors"))
        self.assertTrue(qwenimage.erkennen(namen(self.d / "qi.safetensors")))
        self.assertLess(float((self._netz(self.d / "qi.safetensors") - self.ref).abs().max()), 1e-4)

    def test_gguf_q8_0(self):
        import gguf_testbau as B
        t = {k: (q8_0(v) if v.dim() == 2 and v.shape[1] % 32 == 0 and min(v.shape) > 1 else v) for k, v in self.sd.items()}
        B.schreibe_gguf(str(self.d / "qi.gguf"), {"general.architecture": "qwen_image"}, t)
        out = self._netz(self.d / "qi.gguf")
        self.assertLess(float((out - self.ref).abs().max()), 0.03 * float(self.ref.abs().max()))

    def test_zeitplan_mit_streckung(self):
        from diffusers import FlowMatchEulerDiscreteScheduler
        from diffusers.pipelines.qwenimage.pipeline_qwenimage import calculate_shift
        from bildmotoren import qwenimage
        from bildmotoren.gemeinsam import mu_linear, sigmas_flux
        z = qwenimage.ZEIT
        for n_bild, n in ((256, 20), (6889, 30), (4096, 50)):
            mu = mu_linear(n_bild, z["basis_n"], z["max_n"], z["basis"], z["hoch"])
            self.assertAlmostEqual(mu, calculate_shift(n_bild, 256, 8192, 0.5, 0.9), places=6)
            s = FlowMatchEulerDiscreteScheduler(use_dynamic_shifting=True, time_shift_type="exponential",
                                                shift_terminal=0.02, base_image_seq_len=256, max_image_seq_len=8192,
                                                base_shift=0.5, max_shift=0.9, shift=1.0)
            s.set_timesteps(sigmas=torch.linspace(1.0, 1 / n, n).tolist(), mu=mu)
            self.assertLess(float((s.sigmas - sigmas_flux(n, mu, ende=z["ende"])).abs().max()), 1e-5)

    def test_packen_und_cfg_wie_pipeline(self):
        from diffusers.pipelines.qwenimage.pipeline_qwenimage import QwenImagePipeline as P
        from bildmotoren import qwenimage
        h, w = 3, 5
        lat = torch.randn(1, 16, 2 * h, 2 * w)
        x = lat.view(1, 16, h, 2, w, 2).permute(0, 2, 4, 1, 3, 5).reshape(1, h * w, 64)
        self.assertTrue(torch.equal(x, P._pack_latents(lat, 1, 16, 2 * h, 2 * w)))
        zurueck = x.view(1, h, w, 16, 2, 2).permute(0, 3, 1, 4, 2, 5).reshape(1, 16, 2 * h, 2 * w)
        self.assertTrue(torch.equal(zurueck, P._unpack_latents(x, 16 * h, 16 * w, 8)[:, :, 0]))
        pos, neg = torch.randn(1, 4, 8), torch.randn(1, 4, 8)
        v = qwenimage.cfg_mischen(pos, neg, 4.0)
        self.assertTrue(torch.allclose(v.norm(dim=-1), pos.norm(dim=-1), atol=1e-5))

    @unittest.skipIf(transformers is None, "transformers fehlt")
    def test_qwen2_text_nach_schluss_norm(self):
        """Qwen2.5-VL-Sprachteil (Qwen2-Bauart mit Bias): hidden_states[-1], als safetensors und GGUF."""
        import gguf_testbau as B
        from transformers import Qwen2Config, Qwen2ForCausalLM
        from bildmotoren import qwenimage, textenc
        torch.manual_seed(10)
        hf = _zufall(Qwen2ForCausalLM(Qwen2Config(vocab_size=300, hidden_size=256, intermediate_size=128,
                                                  num_hidden_layers=3, num_attention_heads=2, num_key_value_heads=1,
                                                  rope_theta=1_000_000.0, rms_norm_eps=1e-6,
                                                  max_position_embeddings=256)).eval(), 0.1)
        ids = [5, 17, 99, 3, 250, 11, 42]
        with torch.no_grad():
            ref = hf(torch.tensor([ids]), output_hidden_states=True).hidden_states[-1][0]
        st, gg = self.d / "q2.safetensors", self.d / "q2.gguf"
        save_file({k: v.contiguous() for k, v in hf.state_dict().items()}, str(st))
        meta, t = B.umwandeln(hf, "qwen2")
        B.schreibe_gguf(str(gg), meta, t)
        for p in (st, gg):
            f = textenc.qwen3_form(p)
            self.assertEqual((f["art"], f["hidden"]), ("qwen2", 256), p.suffix)
            self.assertTrue(qwenimage.text_passt(p, None) is False)        # 256 ist kein Qwen2.5-VL-7B
            gew = textenc.qwen3_laden(p, f["schichten"], torch.float32, schluss_norm=True)
            out = textenc.qwen3_zustaende(gew, ids, torch.device("cpu"), torch.float32, ["norm"])[0]
            self.assertLess(float((out - ref).abs().max()), 1e-4, p.suffix)


def _flux1_original(sd: dict) -> dict:
    """diffusers-Namen eines FLUX.1-DiT -> Original-/ComfyUI-Namen."""
    einfach = {"x_embedder.": "img_in.", "context_embedder.": "txt_in.",
               "time_text_embed.timestep_embedder.linear_1.": "time_in.in_layer.",
               "time_text_embed.timestep_embedder.linear_2.": "time_in.out_layer.",
               "time_text_embed.guidance_embedder.linear_1.": "guidance_in.in_layer.",
               "time_text_embed.guidance_embedder.linear_2.": "guidance_in.out_layer.",
               "time_text_embed.text_embedder.linear_1.": "vector_in.in_layer.",
               "time_text_embed.text_embedder.linear_2.": "vector_in.out_layer.", "proj_out.": "final_layer.linear."}
    doppel = {"norm1.linear": "img_mod.lin", "norm1_context.linear": "txt_mod.lin", "attn.to_out.0": "img_attn.proj",
              "attn.to_add_out": "txt_attn.proj", "ff.net.0.proj": "img_mlp.0", "ff.net.2": "img_mlp.2",
              "ff_context.net.0.proj": "txt_mlp.0", "ff_context.net.2": "txt_mlp.2"}
    normen = {"attn.norm_q": "img_attn.norm.query_norm", "attn.norm_k": "img_attn.norm.key_norm",
              "attn.norm_added_q": "txt_attn.norm.query_norm", "attn.norm_added_k": "txt_attn.norm.key_norm"}
    aus = {}
    for k, v in sd.items():
        if k.startswith("norm_out.linear."):
            scale, shift = v.chunk(2, 0)
            aus["final_layer.adaLN_modulation.1." + k.rsplit(".", 1)[1]] = torch.cat([shift, scale])
        elif k.startswith("transformer_blocks."):
            _, i, rest = k.split(".", 2)
            teil, art = rest.rsplit(".", 1)
            p, o = f"transformer_blocks.{i}.", f"double_blocks.{i}."
            if teil in ("attn.to_q", "attn.add_q_proj"):
                s, kk, vv = ("img", "attn.to_k", "attn.to_v") if teil == "attn.to_q" else ("txt", "attn.add_k_proj", "attn.add_v_proj")
                aus[f"{o}{s}_attn.qkv.{art}"] = torch.cat([v, sd[f"{p}{kk}.{art}"], sd[f"{p}{vv}.{art}"]])
            elif teil in doppel:
                aus[f"{o}{doppel[teil]}.{art}"] = v
            elif teil in normen:
                aus[f"{o}{normen[teil]}.scale"] = v
        elif k.startswith("single_transformer_blocks."):
            _, i, rest = k.split(".", 2)
            teil, art = rest.rsplit(".", 1)
            p, o = f"single_transformer_blocks.{i}.", f"single_blocks.{i}."
            if teil == "attn.to_q":
                aus[f"{o}linear1.{art}"] = torch.cat([v, sd[f"{p}attn.to_k.{art}"], sd[f"{p}attn.to_v.{art}"],
                                                      sd[f"{p}proj_mlp.{art}"]])
            elif teil == "norm.linear":
                aus[f"{o}modulation.lin.{art}"] = v
            elif teil == "proj_out":
                aus[f"{o}linear2.{art}"] = v
            elif teil in ("attn.norm_q", "attn.norm_k"):
                aus[f"{o}norm.{'query' if teil.endswith('q') else 'key'}_norm.scale"] = v
        else:
            aus[next((n + k[len(a):] for a, n in einfach.items() if k.startswith(a)), k)] = v
    return aus


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class Flux1Netz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from diffusers import FluxTransformer2DModel
        torch.manual_seed(11)
        cls.m = _zufall(FluxTransformer2DModel(patch_size=1, in_channels=64, num_layers=1, num_single_layers=2,
                                               attention_head_dim=128, num_attention_heads=2, joint_attention_dim=64,
                                               pooled_projection_dim=32, guidance_embeds=True,
                                               axes_dims_rope=(16, 56, 56)).eval(), 0.05)
        cls.sd = {k: v.contiguous() for k, v in cls.m.state_dict().items()}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        cls.h, cls.w = 3, 5
        cls.x, cls.t5, cls.pool = torch.randn(1, 15, 64), torch.randn(6, 64), torch.randn(32)
        img_ids = torch.zeros(15, 3)
        img_ids[:, 1] = torch.arange(3).repeat_interleave(5)
        img_ids[:, 2] = torch.arange(5).repeat(3)
        with torch.no_grad():
            cls.ref = cls.m(hidden_states=cls.x, encoder_hidden_states=cls.t5[None], pooled_projections=cls.pool[None],
                            timestep=torch.tensor([0.6]), img_ids=img_ids, txt_ids=torch.zeros(6, 3),
                            guidance=torch.tensor([3.5]), return_dict=False)[0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _netz(self, pfad):
        from bildmotoren import flux1
        netz = flux1.Netz(flux1.laden(pfad, torch.float32), torch.device("cpu"), torch.float32)
        with torch.no_grad():
            return netz(self.x, 0.6, netz.kontext(self.t5), self.pool, self.h, self.w, 3.5)

    def test_diffusers_und_original_namen(self):
        from bildmotoren import flux1
        from bildmotoren.gewichte import namen
        save_file(self.sd, str(self.d / "diff.safetensors"))
        save_file(_flux1_original(self.sd), str(self.d / "orig.safetensors"))
        for p in ("diff", "orig"):
            pfad = self.d / f"{p}.safetensors"
            self.assertTrue(flux1.erkennen(namen(pfad)), p)
            self.assertTrue(flux1.mit_guidance(pfad), p)
            self.assertLess(float((self._netz(pfad) - self.ref).abs().max()), 1e-4, p)

    def test_gguf_q8_0(self):
        import gguf_testbau as B
        t = {k: (q8_0(v) if v.dim() == 2 and v.shape[1] % 32 == 0 and min(v.shape) > 1 else v)
             for k, v in _flux1_original(self.sd).items()}
        B.schreibe_gguf(str(self.d / "f1.gguf"), {"general.architecture": "flux"}, t)
        out = self._netz(self.d / "f1.gguf")
        self.assertLess(float((out - self.ref).abs().max()), 0.03 * float(self.ref.abs().max()))


def _chroma_original(sd: dict) -> dict:
    """diffusers-Namen eines Chroma-DiT -> Original-Namen (Blöcke wie FLUX.1, dazu der Approximator)."""
    g = "distilled_guidance_layer."
    aus = {}
    for k, v in _flux1_original({k: v for k, v in sd.items() if not k.startswith(g)}).items():
        aus[k] = v
    for k, v in sd.items():
        if k.startswith(g):
            k = k.replace(".linear_1.", ".in_layer.").replace(".linear_2.", ".out_layer.")
            aus[k.replace(".weight", ".scale") if ".norms." in k else k] = v
    return aus


@unittest.skipIf(torch is None or diffusers is None, "torch/diffusers fehlen")
class ChromaNetz(unittest.TestCase):
    """Chroma: Modulation aus dem Approximator, Text-Schlüssel hinter dem ersten Auffüll-Token ausgeblendet."""

    @classmethod
    def setUpClass(cls):
        from diffusers import ChromaTransformer2DModel
        torch.manual_seed(15)
        cls.m = _zufall(ChromaTransformer2DModel(patch_size=1, in_channels=64, num_layers=1, num_single_layers=2,
                                                 attention_head_dim=128, num_attention_heads=2, joint_attention_dim=64,
                                                 axes_dims_rope=(16, 56, 56), approximator_num_channels=64,
                                                 approximator_hidden_dim=96, approximator_layers=2).eval(), 0.05)
        cls.sd = {k: v.contiguous() for k, v in cls.m.state_dict().items()}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        cls.x, cls.t5 = torch.randn(1, 15, 64), torch.randn(8, 64)
        img_ids = torch.zeros(15, 3)
        img_ids[:, 1] = torch.arange(3).repeat_interleave(5)
        img_ids[:, 2] = torch.arange(5).repeat(3)
        maske = torch.cat([torch.tensor([1.0] * 5 + [0.0] * 3), torch.ones(15)])[None]
        with torch.no_grad():
            cls.ref = cls.m(hidden_states=cls.x, encoder_hidden_states=cls.t5[None], timestep=torch.tensor([0.6]),
                            img_ids=img_ids, txt_ids=torch.zeros(8, 3), attention_mask=maske.bool(),
                            return_dict=False)[0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _netz(self, pfad):
        from bildmotoren import chroma
        netz = chroma.Netz(chroma.laden(pfad, torch.float32), torch.device("cpu"), torch.float32)
        with torch.no_grad():
            return netz(self.x, 0.6, netz.kontext(self.t5), 5, 3, 5)

    def test_diffusers_und_original_namen(self):
        from bildmotoren import chroma, flux1
        from bildmotoren.gewichte import namen
        save_file(self.sd, str(self.d / "diff.safetensors"))
        save_file(_chroma_original(self.sd), str(self.d / "orig.safetensors"))
        for p in ("diff", "orig"):
            pfad = self.d / f"{p}.safetensors"
            self.assertTrue(chroma.erkennen(namen(pfad)), p)
            self.assertFalse(flux1.erkennen(namen(pfad)), p)
            self.assertLess(float((self._netz(pfad) - self.ref).abs().max()), 1e-4, p)

    def test_gguf_q8_0(self):
        import gguf_testbau as B
        t = {k: (q8_0(v) if v.dim() == 2 and v.shape[1] % 32 == 0 and min(v.shape) > 1 else v)
             for k, v in _chroma_original(self.sd).items()}
        B.schreibe_gguf(str(self.d / "c.gguf"), {"general.architecture": "flux"}, t)
        out = self._netz(self.d / "c.gguf")
        self.assertLess(float((out - self.ref).abs().max()), 0.03 * float(self.ref.abs().max()))


@unittest.skipIf(torch is None or transformers is None, "torch/transformers fehlen")
class T5undClip(unittest.TestCase):
    def test_t5_safetensors_und_gguf(self):
        import gguf_testbau as B
        from transformers import T5Config, T5EncoderModel
        from bildmotoren import textenc
        torch.manual_seed(12)
        hf = _zufall(T5EncoderModel(T5Config(vocab_size=100, d_model=64, d_kv=16, num_heads=4, d_ff=96, num_layers=2,
                                             feed_forward_proj="gated-gelu", relative_attention_num_buckets=32)).eval(), 0.1)
        ids = [5, 17, 9, 3, 1, 0, 0, 0]
        with torch.no_grad():
            ref = hf(torch.tensor([ids])).last_hidden_state[0]
            ref_maske = hf(torch.tensor([ids]), attention_mask=torch.tensor([[1] * 5 + [0] * 3])).last_hidden_state[0]
        sd = {k: v.clone().contiguous() for k, v in hf.state_dict().items()}
        gg_namen = {"layer.0.SelfAttention.q": "attn_q", "layer.0.SelfAttention.k": "attn_k",
                    "layer.0.SelfAttention.v": "attn_v", "layer.0.SelfAttention.o": "attn_o",
                    "layer.1.DenseReluDense.wi_0": "ffn_gate", "layer.1.DenseReluDense.wi_1": "ffn_up",
                    "layer.1.DenseReluDense.wo": "ffn_down", "layer.0.layer_norm": "attn_norm", "layer.1.layer_norm": "ffn_norm"}
        t = {"token_embd.weight": sd["shared.weight"], "enc.output_norm.weight": sd["encoder.final_layer_norm.weight"],
             "enc.blk.0.attn_rel_b.weight": sd["encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight"]}
        for i in range(2):
            for a, n in gg_namen.items():
                t[f"enc.blk.{i}.{n}.weight"] = sd[f"encoder.block.{i}.{a}.weight"]
        meta = {"general.architecture": "t5encoder", "t5encoder.embedding_length": 64, "t5encoder.block_count": 2,
                "t5encoder.attention.head_count": 4, "t5encoder.attention.relative_buckets_count": 32}
        with tempfile.TemporaryDirectory() as d:
            st, gg = Path(d) / "t5.safetensors", Path(d) / "t5.gguf"
            save_file(sd, str(st))
            B.schreibe_gguf(str(gg), meta, t)
            for p in (st, gg):
                self.assertEqual(textenc.t5_form(p)["koepfe"], 4, p.suffix)
                gew = textenc.t5_laden(p, torch.float32)
                out = textenc.t5_zustand(gew, ids, torch.device("cpu"), torch.float32)
                self.assertLess(float((out - ref).abs().max()), 1e-4, p.suffix)
            out = textenc.t5_zustand(textenc.t5_laden(st, torch.float32), ids, torch.device("cpu"), torch.float32, echt=5)
            self.assertLess(float((out - ref_maske).abs().max()), 1e-4)

    def test_clip_gepoolt(self):
        from transformers import CLIPTextConfig, CLIPTextModel
        from bildmotoren import textenc
        torch.manual_seed(13)
        hf = _zufall(CLIPTextModel(CLIPTextConfig(vocab_size=49408, hidden_size=64, intermediate_size=128,
                                                  num_hidden_layers=2, num_attention_heads=4, max_position_embeddings=77,
                                                  eos_token_id=49407, bos_token_id=49406)).eval(), 0.05)
        ids = [49406, 320, 1125, 539, 49407] + [49407] * 72
        with torch.no_grad():
            ref = hf(torch.tensor([ids])).pooler_output[0]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "clip.safetensors"
            save_file({k: v.contiguous() for k, v in hf.state_dict().items()}, str(p))
            out = textenc.clip_gepoolt(textenc.clip_laden(p, torch.float32), ids, torch.device("cpu"), torch.float32, koepfe=4)
        self.assertLess(float((out - ref).abs().max()), 1e-4)

    def test_clip_tokenizer_wie_transformers(self):
        """CLIP-BPE aus vocab.json + merges.txt wie transformers' CLIPTokenizer (Kleinschreibung, </w>, Satzzeichen)."""
        import json
        from transformers import CLIPTokenizer
        from bildmotoren import textenc
        zeichen = _byte_tokens()
        vocab = zeichen + [z + "</w>" for z in zeichen]
        merges = ["a b</w>", "c a", "ca t</w>", "d o", "do g</w>", "a b", "ab c</w>"]
        for m in merges:
            vocab.append(m.replace(" ", ""))
        vocab = {t: i for i, t in enumerate(dict.fromkeys(vocab))}
        vocab["<|startoftext|>"], vocab["<|endoftext|>"] = 49406, 49407
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "vocab.json").write_text(json.dumps(vocab), encoding="utf-8")
            (Path(d) / "merges.txt").write_text("#version: 0.2\n" + "\n".join(merges) + "\n", encoding="utf-8")
            hf = CLIPTokenizer(str(Path(d) / "vocab.json"), str(Path(d) / "merges.txt"))
            eigen = textenc.clip_tokenizer(Path(d))
            for text in ("A cat and a DOG!", "ab  abc,cat's dog", "x1 2y"):
                ref = hf(text, padding="max_length", max_length=77, truncation=True)["input_ids"]
                self.assertEqual(eigen(text), ref, text)


@unittest.skipIf(torch is None or diffusers is None or transformers is None, "torch/diffusers/transformers fehlen")
class Weiche(unittest.TestCase):
    """Ordner durchsuchen, Zubehör finden, prüfen und ein ganzes Bild malen (alles in einem Testordner)."""

    def setUp(self):
        import bildmotoren
        self.tmp = tempfile.TemporaryDirectory()
        self.o = Path(self.tmp.name) / "Bildmodelle"
        (self.o / "ZTest").mkdir(parents=True)
        (self.o / "text_encoders").mkdir()
        (self.o / "vae").mkdir()
        sd = {k: v.contiguous() for k, v in _zimage(cap_dim=64).state_dict().items()}
        save_file(_comfy_namen(sd), str(self.o / "ZTest" / "ztest.safetensors"))
        save_file({k: v.contiguous() for k, v in _vae().state_dict().items()}, str(self.o / "vae" / "ae_test.safetensors"))
        _qwen3_gguf(self.o / "text_encoders" / "qwen3_test.gguf", _qwen3())
        bildmotoren._CACHE.clear()
        self.p = [mock.patch.object(bildmotoren, "ORDNER", [self.o]),
                  mock.patch("bildmotoren.zimage.text_passt", lambda p, dit=None: p.suffix == ".gguf")]
        for x in self.p:
            x.start()

    def tearDown(self):
        import bildmotoren
        for x in self.p:
            x.stop()
        bildmotoren._CACHE.clear()
        bildmotoren.entladen()
        self.tmp.cleanup()

    def test_finden_und_zubehoer(self):
        import bildmotoren
        m = bildmotoren.modelle()
        self.assertEqual(list(m), ["ZTest"])
        self.assertEqual(m["ZTest"]["bauart"], "zimage")
        t = bildmotoren.teile("ZTest")
        self.assertEqual((t["text"].name, t["vae"].name), ("qwen3_test.gguf", "ae_test.safetensors"))
        self.assertIsNone(bildmotoren.missing_reason("ZTest"))

    def test_fehlendes_zubehoer_wird_genannt(self):
        import bildmotoren
        (self.o / "vae" / "ae_test.safetensors").unlink()
        bildmotoren._CACHE.clear()
        self.assertIn("VAE fehlt", bildmotoren.missing_reason("ZTest"))

    def test_bekannte_datei_mit_falscher_groesse_wird_gesperrt(self):
        import bildmotoren
        p = self.o / "vae" / "ae.safetensors"
        (self.o / "vae" / "ae_test.safetensors").rename(p)
        bildmotoren._CACHE.clear()
        self.assertFalse(bildmotoren.geprueft(p))
        self.assertIn("Größe stimmt nicht", bildmotoren.missing_reason("ZTest"))
        with self.assertRaises(RuntimeError):
            bildmotoren.pruefen(p)

    def test_qwenimage_findet_vae_bei_krea(self):
        """Qwen-Image nimmt die VAE aus dem Krea-2-Ordner, wenn sie im eigenen fehlt; Text-Encoder nur Qwen2-Bauart."""
        import bildmotoren
        from diffusers import QwenImageTransformer2DModel
        (self.o / "QTest").mkdir()
        m = QwenImageTransformer2DModel(patch_size=2, in_channels=64, out_channels=16, num_layers=1, attention_head_dim=128,
                                        num_attention_heads=2, joint_attention_dim=64, axes_dims_rope=(16, 56, 56))
        save_file({k: v.contiguous() for k, v in m.state_dict().items()}, str(self.o / "QTest" / "qtest.safetensors"))
        krea_vae = Path(self.tmp.name) / "Krea2" / "vae"
        krea_vae.mkdir(parents=True)
        save_file({"decoder.middle.0.residual.0.gamma": torch.ones(4, 1, 1, 1), "conv2.weight": torch.zeros(16, 16, 1, 1, 1)},
                  str(krea_vae / "qwen_image_vae.safetensors"))
        bildmotoren._CACHE.clear()
        with mock.patch("krea.KREA_DIRS", [krea_vae.parent]):
            t = bildmotoren.teile("QTest")
        self.assertEqual(t["bauart"], "qwenimage")
        self.assertEqual(t["vae"], krea_vae / "qwen_image_vae.safetensors")
        self.assertIsNone(t["text"])                        # der Mini-Qwen3 passt nicht zu Qwen-Image
        self.assertIn("Text-Encoder fehlt", bildmotoren.missing_reason("QTest"))

    def test_flux1_bild_malen(self):
        """FLUX.1: T5 und CLIP im geteilten Ordner, Tokenizer in tokenizer_t5/ und tokenizer_clip/, ganzes Bild."""
        import json
        import bildmotoren
        from diffusers import FluxTransformer2DModel
        from PIL import Image
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import CLIPTextConfig, CLIPTextModel, T5Config, T5EncoderModel
        torch.manual_seed(14)
        (self.o / "F1Test").mkdir()
        dit = FluxTransformer2DModel(patch_size=1, in_channels=64, num_layers=1, num_single_layers=1, attention_head_dim=128,
                                     num_attention_heads=2, joint_attention_dim=64, pooled_projection_dim=128,
                                     guidance_embeds=False, axes_dims_rope=(16, 56, 56))
        save_file(_flux1_original({k: v.contiguous() for k, v in dit.state_dict().items()}),
                  str(self.o / "F1Test" / "flux1-schnell-test.safetensors"))
        t5 = T5EncoderModel(T5Config(vocab_size=100, d_model=64, d_kv=16, num_heads=4, d_ff=96, num_layers=1,
                                     feed_forward_proj="gated-gelu"))
        save_file({k: v.clone().contiguous() for k, v in t5.state_dict().items()}, str(self.o / "text_encoders" / "t5_test.safetensors"))
        clip = CLIPTextModel(CLIPTextConfig(vocab_size=49408, hidden_size=128, intermediate_size=128, num_hidden_layers=1,
                                            num_attention_heads=2, max_position_embeddings=77))
        save_file({k: v.contiguous() for k, v in clip.state_dict().items()}, str(self.o / "text_encoders" / "clip_test.safetensors"))
        (self.o / "tokenizer_t5").mkdir()
        tok = Tokenizer(models.WordLevel({"<pad>": 0, "</s>": 1, "<unk>": 2, "a": 3, "red": 4, "ball": 5}, unk_token="<unk>"))
        tok.pre_tokenizer = pre_tokenizers.Whitespace()
        tok.save(str(self.o / "tokenizer_t5" / "tokenizer.json"))
        (self.o / "tokenizer_clip").mkdir()
        vocab = {t: i for i, t in enumerate(_byte_tokens() + [z + "</w>" for z in _byte_tokens()])}
        vocab.update({"<|startoftext|>": 49406, "<|endoftext|>": 49407})
        (self.o / "tokenizer_clip" / "vocab.json").write_text(json.dumps(vocab), encoding="utf-8")
        (self.o / "tokenizer_clip" / "merges.txt").write_text("#version: 0.2\n", encoding="utf-8")
        bildmotoren._CACHE.clear()
        ziel = Path(self.tmp.name) / "f1.png"
        with mock.patch("bildmotoren.flux1.text_passt", lambda p, dit=None: p.name == "t5_test.safetensors"), \
                mock.patch("bildmotoren.flux1.clip_passt", lambda p, dit=None: p.name == "clip_test.safetensors"), \
                mock.patch("profilordner.bild_ziel", lambda *a, **k: ziel), \
                mock.patch("bildmotoren.gemeinsam.geraet", lambda: (torch.device("cpu"), torch.float32)), \
                mock.patch("bildmotoren._entladen_nach", lambda: 0):
            t = bildmotoren.teile("F1Test")
            self.assertEqual((t["bauart"], t["text"].name, t["clip"].name, t["vae"].name),
                             ("flux1", "t5_test.safetensors", "clip_test.safetensors", "ae_test.safetensors"))
            self.assertIsNone(bildmotoren.missing_reason("F1Test"))
            self.assertEqual(bildmotoren.schritte("F1Test"), 4)
            pfad = bildmotoren.generate("a red ball", model="F1Test", size=(256, 256), seed=5)
        with Image.open(pfad) as im:
            self.assertEqual(im.size, (256, 256))
            self.assertIn("backend=flux1", im.text["params"])

    def test_flux2_bild_malen(self):
        """FLUX.2 im eigenen Ordner neben Z-Image: eigenes Zubehör (VAE mit 32 Kanälen), ganzes Bild."""
        import bildmotoren
        from diffusers import AutoencoderKLFlux2
        from PIL import Image
        (self.o / "F2Test").mkdir()
        save_file({k: v.contiguous() for k, v in _flux2(joint=3 * 64).state_dict().items()},
                  str(self.o / "F2Test" / "f2test.safetensors"))
        torch.manual_seed(8)
        v = _zufall(AutoencoderKLFlux2(block_out_channels=(32, 32, 64, 64), latent_channels=32, layers_per_block=2,
                                       norm_num_groups=32).eval(), 0.02)
        save_file({k: x.contiguous() for k, x in v.state_dict().items()}, str(self.o / "vae" / "flux2_test.safetensors"))
        bildmotoren._CACHE.clear()
        ziel = Path(self.tmp.name) / "f2.png"
        with mock.patch("bildmotoren.flux2.TEXT_ABGRIFFE", (1, 2, 3)), \
                mock.patch("profilordner.bild_ziel", lambda *a, **k: ziel), \
                mock.patch("bildmotoren.gemeinsam.geraet", lambda: (torch.device("cpu"), torch.float32)), \
                mock.patch("bildmotoren._entladen_nach", lambda: 0):
            t = bildmotoren.teile("F2Test")
            self.assertEqual((t["bauart"], t["vae"].name, t["text"].name), ("flux2", "flux2_test.safetensors", "qwen3_test.gguf"))
            self.assertEqual(bildmotoren.teile("ZTest")["vae"].name, "ae_test.safetensors")
            self.assertEqual(bildmotoren.schritte("F2Test"), 4)
            pfad = bildmotoren.generate("a red ball", model="F2Test", size=(256, 256), seed=3)
        with Image.open(pfad) as im:
            self.assertEqual(im.size, (256, 256))
            self.assertIn("backend=flux2", im.text["params"])

    def test_ein_bild_malen(self):
        import bildmotoren
        from PIL import Image
        ziel = Path(self.tmp.name) / "bild.png"
        with mock.patch("profilordner.bild_ziel", lambda *a, **k: ziel), \
                mock.patch("bildmotoren.gemeinsam.geraet", lambda: (torch.device("cpu"), torch.float32)), \
                mock.patch("bildmotoren._entladen_nach", lambda: 0):
            pfad = bildmotoren.generate("ein roter Ball", model="ztest", steps=4, size=(256, 256), seed=7)
        self.assertEqual(Path(pfad), ziel)
        with Image.open(ziel) as im:
            self.assertEqual(im.size, (256, 256))
            self.assertIn("backend=zimage", im.text["params"])
        self.assertEqual(bildmotoren.im_ram(), [])


@unittest.skipIf(torch is None, "torch fehlt")
class QwenTokenizerOrdner(unittest.TestCase):
    def test_ohne_tokenizer_config_kein_stilles_zerlegen(self):
        import json

        from bildmotoren import textenc
        with tempfile.TemporaryDirectory() as d:
            o = Path(d)
            (o / "vocab.json").write_text(json.dumps({"a": 0, "b": 1, "ab": 2}), encoding="utf-8")
            (o / "merges.txt").write_text("#version: 0.2\na b\n", encoding="utf-8")
            self.assertFalse(textenc.tokenizer_da(o))
            with self.assertRaises(FileNotFoundError):
                textenc.qwen_tokenizer(o)


if __name__ == "__main__":
    unittest.main()
