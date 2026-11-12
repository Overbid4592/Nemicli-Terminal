"""SD-Video-Motor: Bildnetz von SD 1.5 und SDXL mit Zeit-Schichten, gegen die Referenz aus diffusers.

Gerechnet wird mit winzigen Zufallsnetzen im Arbeitsspeicher – keine Modelldatei, keine Grafikkarte.
"""
import re
import sys
import unittest
from pathlib import Path

R = Path(__file__).resolve().parent.parent
for d in ("", "core", "engines", "tools", "ui"):
    if str(R / d) not in sys.path:
        sys.path.insert(0, str(R / d))

try:
    import torch
    import diffusers  # noqa: F401
    REFERENZ = True
except Exception:
    REFERENZ = False

_RES = {"norm1": "in_layers.0", "conv1": "in_layers.2", "time_emb_proj": "emb_layers.1", "norm2": "out_layers.0",
        "conv2": "out_layers.3", "conv_shortcut": "skip_connection"}


def _res_name(rest: str) -> str:
    kopf, _, ende = rest.partition(".")
    return f"{_RES[kopf]}.{ende}"


def original_namen(unet) -> dict:
    """Gewichte eines diffusers-Bildnetzes unter den Namen ganzer Checkpoints (input_blocks.…)."""
    lagen = unet.config.layers_per_block
    stufen = len(unet.config.block_out_channels)
    aus = {}
    for k, v in unet.state_dict().items():
        if k.startswith("time_embedding.linear_"):
            neu = f"time_embed.{0 if k.split('.')[1] == 'linear_1' else 2}.{k.split('.')[-1]}"
        elif k.startswith("add_embedding.linear_"):
            neu = f"label_emb.0.{0 if k.split('.')[1] == 'linear_1' else 2}.{k.split('.')[-1]}"
        elif k.startswith("conv_in."):
            neu = "input_blocks.0.0." + k.split(".", 1)[1]
        elif k.startswith("conv_norm_out."):
            neu = "out.0." + k.split(".", 1)[1]
        elif k.startswith("conv_out."):
            neu = "out.2." + k.split(".", 1)[1]
        elif (m := re.match(r"down_blocks\.(\d+)\.(resnets|attentions)\.(\d+)\.(.+)", k)):
            b, art, lage, rest = int(m[1]), m[2], int(m[3]), m[4]
            i = 1 + b * (lagen + 1) + lage
            neu = f"input_blocks.{i}.0.{_res_name(rest)}" if art == "resnets" else f"input_blocks.{i}.1.{rest}"
        elif (m := re.match(r"down_blocks\.(\d+)\.downsamplers\.0\.conv\.(.+)", k)):
            neu = f"input_blocks.{(int(m[1]) + 1) * (lagen + 1)}.0.op.{m[2]}"
        elif (m := re.match(r"mid_block\.resnets\.(\d)\.(.+)", k)):
            neu = f"middle_block.{int(m[1]) * 2}.{_res_name(m[2])}"
        elif k.startswith("mid_block.attentions.0."):
            neu = "middle_block.1." + k.split(".", 3)[3]
        elif (m := re.match(r"up_blocks\.(\d+)\.(resnets|attentions)\.(\d+)\.(.+)", k)):
            b, art, lage, rest = int(m[1]), m[2], int(m[3]), m[4]
            i = b * (lagen + 1) + lage
            neu = f"output_blocks.{i}.0.{_res_name(rest)}" if art == "resnets" else f"output_blocks.{i}.1.{rest}"
        elif (m := re.match(r"up_blocks\.(\d+)\.upsamplers\.0\.conv\.(.+)", k)):
            b = int(m[1])
            hat_text = "CrossAttn" in unet.config.up_block_types[b]
            neu = f"output_blocks.{b * (lagen + 1) + lagen}.{2 if hat_text else 1}.conv.{m[2]}"
        else:
            raise AssertionError(f"unbekannter Name: {k}")
        aus[neu] = v
    assert stufen >= 2
    return aus


def bewegung_namen(adapter) -> dict:
    """Gewichte eines diffusers-Bewegungs-Moduls unter den Namen der Original-Dateien."""
    aus = {}
    for k, v in adapter.state_dict().items():
        if "pos_embed" in k:
            continue
        k = re.sub(r"(motion_modules\.\d+)\.", r"\1.temporal_transformer.", k)
        for alt, neu in ((".attn1.", ".attention_blocks.0."), (".attn2.", ".attention_blocks.1."),
                         (".norm1.", ".norms.0."), (".norm2.", ".norms.1."), (".norm3.", ".ff_norm.")):
            k = k.replace(alt, neu)
        aus[k] = v
    return aus


def _hotshot_name(k: str) -> str:
    return re.sub(r"\.motion_modules\.(\d+)\.temporal_transformer\.", r".temporal_attentions.\1.", k)


def _wie_das_original(ganz, unet) -> None:
    """Die Referenz an zwei Stellen richtigstellen: Sie übernimmt die SDXL-Zusatzwerte nicht aus dem Bildnetz,
    und sie normt in den Zeit-Schichten über alle Bilder – AnimateDiff und Hotshot-XL normen je Bild."""
    if getattr(unet, "add_embedding", None) is not None:
        ganz.add_embedding.load_state_dict(unet.add_embedding.state_dict())

    class JeBild(torch.nn.Module):
        def __init__(self, norm):
            super().__init__()
            self.norm = norm

        def forward(self, x):
            b, c, f, h, w = x.shape
            y = self.norm(x.permute(0, 2, 1, 3, 4).reshape(b * f, c, h, w))
            return y.reshape(b, f, c, h, w).permute(0, 2, 1, 3, 4)

    for modul in ganz.modules():
        if type(modul).__name__ == "AnimateDiffTransformer3D":
            modul.norm = JeBild(modul.norm)


def _zufall(modell) -> None:
    """Auch die mit Null beginnenden Ausgänge würfeln – sonst prüft der Vergleich die Zeit-Schichten nicht."""
    g = torch.Generator().manual_seed(7)
    with torch.no_grad():
        for p in modell.parameters():
            p.copy_(torch.randn(p.shape, generator=g) * 0.05)


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class Bildnetz(unittest.TestCase):
    def _sd15(self):
        from diffusers import UNet2DConditionModel
        unet = UNet2DConditionModel(
            sample_size=8, in_channels=4, out_channels=4, block_out_channels=(32, 64), layers_per_block=2,
            down_block_types=("CrossAttnDownBlock2D", "DownBlock2D"),
            up_block_types=("UpBlock2D", "CrossAttnUpBlock2D"), cross_attention_dim=768, attention_head_dim=8)
        _zufall(unet)
        return unet.eval()

    def _sdxl(self):
        from diffusers import UNet2DConditionModel
        unet = UNet2DConditionModel(
            sample_size=8, in_channels=4, out_channels=4, block_out_channels=(64, 128), layers_per_block=2,
            down_block_types=("DownBlock2D", "CrossAttnDownBlock2D"),
            up_block_types=("CrossAttnUpBlock2D", "UpBlock2D"), cross_attention_dim=2048,
            attention_head_dim=(1, 2), transformer_layers_per_block=(1, 2), use_linear_projection=True,
            addition_embed_type="text_time", addition_time_embed_dim=256,
            projection_class_embeddings_input_dim=2816)
        _zufall(unet)
        return unet.eval()

    def test_sd15_einzelbild_wie_die_referenz(self):
        from videomotoren import sd_netz
        unet = self._sd15()
        sd = original_namen(unet)
        self.assertEqual(sd_netz.bauart({k: tuple(v.shape) for k, v in sd.items()}), "sd15")
        g = torch.Generator().manual_seed(1)
        x, text = torch.randn(2, 4, 8, 8, generator=g), torch.randn(2, 77, 768, generator=g)
        t = torch.tensor([10, 900])
        with torch.no_grad():
            soll = unet(x, t, text).sample
            ist = sd_netz.Netz(sd)(x, t, text)
        self.assertLess((soll - ist).abs().max().item(), 2e-6)

    def test_sdxl_einzelbild_wie_die_referenz(self):
        from videomotoren import sd_netz
        unet = self._sdxl()
        sd = original_namen(unet)
        self.assertEqual(sd_netz.bauart({k: tuple(v.shape) for k, v in sd.items()}), "sdxl")
        g = torch.Generator().manual_seed(2)
        x, text = torch.randn(1, 4, 8, 8, generator=g), torch.randn(1, 77, 2048, generator=g)
        gepoolt = torch.randn(1, 1280, generator=g)
        t = torch.tensor([500])
        zeit_ids = torch.tensor([[64.0, 96.0, 0.0, 0.0, 64.0, 96.0]])
        with torch.no_grad():
            soll = unet(x, t, text, added_cond_kwargs={"text_embeds": gepoolt, "time_ids": zeit_ids}).sample
            zusatz = sd_netz.zusatz_sdxl(gepoolt, 64, 96, x.device, torch.float32)
            ist = sd_netz.Netz(sd)(x, t, text, zusatz=zusatz)
        self.assertLess((soll - ist).abs().max().item(), 2e-6)

    def _video(self, unet, kontext_breite, zusatz_fn=None, mitte=True):
        from diffusers import MotionAdapter, UNetMotionModel
        from videomotoren import sd_netz
        adapter = MotionAdapter(block_out_channels=tuple(unet.config.block_out_channels), motion_layers_per_block=2,
                                motion_num_attention_heads=8, motion_norm_num_groups=32, motion_max_seq_length=32,
                                use_motion_mid_block=mitte)
        _zufall(adapter)
        ganz = UNetMotionModel.from_unet2d(unet, adapter).eval()
        _wie_das_original(ganz, unet)
        zeit = bewegung_namen(adapter)
        self.assertTrue(sd_netz.ist_bewegung({k: tuple(v.shape) for k, v in zeit.items()}))
        bilder = 6
        g = torch.Generator().manual_seed(3)
        x = torch.randn(1, 4, bilder, 8, 8, generator=g)
        text = torch.randn(1, 77, kontext_breite, generator=g)
        t = torch.tensor([321])
        extra, zusatz = {}, None
        if zusatz_fn is not None:
            gepoolt = torch.randn(1, 1280, generator=g)
            extra = {"added_cond_kwargs": {"text_embeds": gepoolt,
                                           "time_ids": torch.tensor([[64.0, 64.0, 0.0, 0.0, 64.0, 64.0]])}}
            zusatz = zusatz_fn(gepoolt).expand(bilder, -1)
        with torch.no_grad():
            # die Referenz erwartet den Text schon je Bild
            soll = ganz(x, t, text.expand(bilder, -1, -1), **extra).sample   # (1, 4, Bilder, 8, 8)
            netz = sd_netz.Netz(original_namen(unet), zeit)
            stapel = x[0].transpose(0, 1)                                   # (Bilder, 4, 8, 8)
            ist = netz(stapel, t.expand(bilder), text.expand(bilder, -1, -1), bilder=bilder, zusatz=zusatz)
            ohne = netz(stapel, t.expand(bilder), text.expand(bilder, -1, -1), bilder=1, zusatz=zusatz)
        self.assertLess((soll[0].transpose(0, 1) - ist).abs().max().item(), 2e-6)
        self.assertGreater((ist - ohne).abs().max().item(), 1e-3)           # die Zeit-Schichten wirken wirklich
        # dieselben Gewichte unter den Namen von diffusers und von Hotshot-XL
        for umbenennen in (lambda k: k, _hotshot_name):
            roh = {umbenennen(k): v for k, v in adapter.state_dict().items()} if umbenennen is not _hotshot_name \
                else {_hotshot_name(k): v for k, v in zeit.items()}
            gelesen = {n: v for k, v in roh.items() if (n := sd_netz.bewegung_name(k))}
            self.assertEqual(set(gelesen), set(zeit))
            self.assertTrue(all(torch.equal(gelesen[k], zeit[k]) for k in zeit))

    def test_sd15_video_wie_die_referenz(self):
        self._video(self._sd15(), 768)

    def test_sd15_video_ohne_mittlere_zeit_schicht(self):
        self._video(self._sd15(), 768, mitte=False)

    def test_sdxl_video_wie_die_referenz(self):
        from videomotoren import sd_netz
        self._video(self._sdxl(), 2048, lambda p: sd_netz.zusatz_sdxl(p, 64, 64, "cpu", torch.float32))

    def test_bewegung_passt_zur_bauart(self):
        from videomotoren import sd_netz
        vier = {f"down_blocks.{b}.motion_modules.0.temporal_transformer.proj_in.weight": (1, 1) for b in range(4)}
        drei = {f"down_blocks.{b}.motion_modules.0.temporal_transformer.proj_in.weight": (1, 1) for b in range(3)}
        self.assertTrue(sd_netz.bewegung_passt(vier, 4))
        self.assertFalse(sd_netz.bewegung_passt(vier, 3))
        self.assertTrue(sd_netz.bewegung_passt(drei, 3))
        self.assertFalse(sd_netz.bewegung_passt(drei, 4))
        self.assertFalse(sd_netz.bewegung_passt({"input_blocks.0.0.weight": (1,)}, 4))
        self.assertEqual(sd_netz.stufen({k: () for k in original_namen(self._sd15())}), 2)
        hotshot = {f"down_blocks.{b}.temporal_attentions.0.proj_in.weight": (1, 1) for b in range(3)}
        hotshot["down_blocks.0.temporal_attentions.0.transformer_blocks.0.attention_blocks.0.pos_encoder"
                ".positional_encoding"] = (1, 24, 320)
        self.assertTrue(sd_netz.bewegung_passt(hotshot, 3))
        self.assertEqual(sd_netz.fenster_laenge(hotshot), 24)
        self.assertEqual(sd_netz.fenster_laenge(vier), 32)
        self.assertIsNone(sd_netz.bewegung_name(
            "down_blocks.0.motion_modules.0.temporal_transformer.transformer_blocks.0.attention_blocks.0.pos_encoder.pe"))


def _clip(breite: int, schichten: int, akt: str, projektion: bool = False):
    from transformers import CLIPTextConfig, CLIPTextModel, CLIPTextModelWithProjection
    cfg = CLIPTextConfig(vocab_size=49408, hidden_size=breite, intermediate_size=2 * breite,
                         num_hidden_layers=schichten, num_attention_heads=breite // 64, max_position_embeddings=77,
                         hidden_act=akt, projection_dim=breite, bos_token_id=0, eos_token_id=2, pad_token_id=1)
    modell = (CLIPTextModelWithProjection if projektion else CLIPTextModel)(cfg)
    _zufall(modell)
    return modell.eval()


def clip_namen(modell) -> dict:
    """Gewichte eines transformers-CLIP mit dem Vorsatz `text_model.`, wie sie im Checkpoint stehen
    (neuere transformers lassen ihn weg)."""
    return {(k if k.startswith(("text_model.", "text_projection.")) else "text_model." + k): v
            for k, v in modell.state_dict().items() if not k.endswith("position_ids")}


def openclip_namen(modell) -> dict:
    """Gewichte eines transformers-CLIP unter den Namen von OpenCLIP (wie CLIP-G im SDXL-Checkpoint)."""
    sd = clip_namen(modell)
    aus = {"token_embedding.weight": sd["text_model.embeddings.token_embedding.weight"],
           "positional_embedding": sd["text_model.embeddings.position_embedding.weight"],
           "ln_final.weight": sd["text_model.final_layer_norm.weight"],
           "ln_final.bias": sd["text_model.final_layer_norm.bias"],
           "text_projection": sd["text_projection.weight"].T.contiguous()}
    for i in range(modell.config.num_hidden_layers):
        a, n = f"text_model.encoder.layers.{i}.", f"transformer.resblocks.{i}."
        aus[n + "attn.in_proj_weight"] = torch.cat([sd[a + f"self_attn.{c}_proj.weight"] for c in "qkv"])
        aus[n + "attn.in_proj_bias"] = torch.cat([sd[a + f"self_attn.{c}_proj.bias"] for c in "qkv"])
        for alt, neu in (("self_attn.out_proj", "attn.out_proj"), ("layer_norm1", "ln_1"), ("layer_norm2", "ln_2"),
                         ("mlp.fc1", "mlp.c_fc"), ("mlp.fc2", "mlp.c_proj")):
            for teil in ("weight", "bias"):
                aus[f"{n}{neu}.{teil}"] = sd[f"{a}{alt}.{teil}"]
    return aus


def vae_namen(vae) -> dict:
    """Gewichte einer diffusers-VAE unter den Original-Namen (encoder.down.0.block.0 …)."""
    from bildmotoren import vae_flux
    stufen = len(vae.config.block_out_channels)
    aus = {}
    for k, v in vae.state_dict().items():
        if k.startswith("decoder."):
            k = vae_flux._original_name(k, stufen)
        elif k.startswith("encoder."):
            k = k.replace("encoder.conv_norm_out.", "encoder.norm_out.")
            k = re.sub(r"encoder\.mid_block\.resnets\.(\d)\.", lambda m: f"encoder.mid.block_{int(m[1]) + 1}.", k)
            k = k.replace("encoder.mid_block.attentions.0.group_norm.", "encoder.mid.attn_1.norm.")
            for alt, neu in (("to_q", "q"), ("to_k", "k"), ("to_v", "v"), ("to_out.0", "proj_out")):
                k = k.replace(f"encoder.mid_block.attentions.0.{alt}.", f"encoder.mid.attn_1.{neu}.")
            k = re.sub(r"encoder\.down_blocks\.(\d+)\.resnets\.(\d+)\.", r"encoder.down.\1.block.\2.", k)
            k = re.sub(r"encoder\.down_blocks\.(\d+)\.downsamplers\.0\.", r"encoder.down.\1.downsample.", k)
            k = k.replace(".conv_shortcut.", ".nin_shortcut.")
        aus[k] = v
    return aus


def _vae(stufen: int = 2):
    """Mini-VAE; mit vier Stufen verkleinert sie 8× wie die echte."""
    from diffusers import AutoencoderKL
    vae = AutoencoderKL(in_channels=3, out_channels=3, latent_channels=4,
                        block_out_channels=(32, 64) if stufen == 2 else (32,) * stufen,
                        down_block_types=("DownEncoderBlock2D",) * stufen,
                        up_block_types=("UpDecoderBlock2D",) * stufen, layers_per_block=1, norm_num_groups=32)
    _zufall(vae)
    return vae.eval()


IDS = [49406, 320, 1125, 539, 49407] + [49407] * 72


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class TextUndVae(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.ordner = Path(tempfile.mkdtemp(prefix="sdvideo_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.ordner, ignore_errors=True))

    def _datei(self, gewichte: dict) -> Path:
        from safetensors.torch import save_file
        pfad = self.ordner / "probe.safetensors"
        save_file({k: v.contiguous() for k, v in gewichte.items()}, str(pfad))
        return pfad

    def test_clip_l_wie_transformers(self):
        from videomotoren import sd_text
        modell = _clip(128, 3, "quick_gelu")
        pfad = self._datei({"cond_stage_model.transformer." + k: v for k, v in clip_namen(modell).items()})
        with torch.no_grad():
            soll = modell(torch.tensor([IDS]), output_hidden_states=True)
        letzter, vorletzter, gepoolt = sd_text.zustaende(sd_text.laden_l(pfad), IDS, "cpu")
        self.assertLess((soll.last_hidden_state[0] - letzter).abs().max().item(), 1e-5)
        self.assertLess((soll.hidden_states[-2][0] - vorletzter).abs().max().item(), 1e-5)
        self.assertIsNone(gepoolt)
        self.assertEqual(tuple(sd_text.text_sd15(sd_text.laden_l(pfad), IDS, "cpu", torch.float32).shape), (1, 77, 128))

    def test_clip_g_wie_transformers(self):
        from videomotoren import sd_text
        modell = _clip(128, 3, "gelu", projektion=True)
        pfad = self._datei({sd_text.VORSATZ_G + k: v for k, v in openclip_namen(modell).items()})
        ids = sd_text.ids_g(IDS)
        self.assertEqual(ids[:6], [49406, 320, 1125, 539, 49407, 0])
        with torch.no_grad():
            soll = modell(torch.tensor([ids]), output_hidden_states=True)
        _letzter, vorletzter, gepoolt = sd_text.zustaende(sd_text.laden_g(pfad), ids, "cpu")
        self.assertLess((soll.hidden_states[-2][0] - vorletzter).abs().max().item(), 1e-5)
        self.assertLess((soll.text_embeds[0] - gepoolt).abs().max().item(), 1e-5)

    def test_vae_ein_und_auspacken_wie_die_referenz(self):
        from videomotoren import sd_vae
        vae = _vae()
        pfad = self._datei({sd_vae.VORSATZ + k: v for k, v in vae_namen(vae).items()})
        sd = sd_vae.laden(pfad)
        g = torch.Generator().manual_seed(4)
        bild = (torch.rand(32, 48, 3, generator=g) * 255).round().to(torch.uint8)
        with torch.no_grad():
            x = bild.float().permute(2, 0, 1)[None] / 127.5 - 1.0
            soll_latent = vae.encode(x).latent_dist.mean * sd_vae.SKALA["sd15"]
            latent = sd_vae.einpacken(sd, bild.numpy(), "sd15", "cpu")
            self.assertLess((soll_latent - latent).abs().max().item(), 1e-5)
            stapel = torch.randn(5, 4, 16, 24, generator=g)                  # die Mini-VAE verkleinert nur 2×
            soll = vae.decode(stapel / sd_vae.SKALA["sdxl"]).sample
            soll = ((soll.permute(0, 2, 3, 1).clamp(-1, 1) + 1) * 127.5).round().to(torch.uint8).numpy()
            ist = sd_vae.auspacken(sd, stapel, "sdxl", "cpu")
        self.assertEqual(ist.shape, (5, 32, 48, 3))
        self.assertLessEqual(int(abs(soll.astype(int) - ist.astype(int)).max()), 1)


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class Plan(unittest.TestCase):
    def test_rauschplan_wie_die_referenz(self):
        from diffusers import EulerDiscreteScheduler
        from videomotoren import sdvideo
        for beta, name in (("linear", "linear"), ("skaliert", "scaled_linear")):
            for schritte in (4, 25):
                ref = EulerDiscreteScheduler(beta_start=0.00085, beta_end=0.012, beta_schedule=name,
                                             timestep_spacing="trailing")
                ref.set_timesteps(schritte)
                zeit, sig = sdvideo.rauschplan(schritte, beta)
                self.assertEqual(zeit.tolist(), [int(t) for t in ref.timesteps.tolist()])
                self.assertLess((sig - ref.sigmas).abs().max().item(), 1e-4)
                self.assertEqual(float(sig[-1]), 0.0)

    def test_fenster_decken_alle_bilder(self):
        from videomotoren import sdvideo
        self.assertEqual(sdvideo.fenster_plan(12, 16), [list(range(12))])
        for bilder, laenge in ((40, 16), (120, 16), (17, 16), (64, 8), (33, 32)):
            plan = sdvideo.fenster_plan(bilder, laenge)
            self.assertTrue(all(len(f) == laenge and f == list(range(f[0], f[0] + laenge)) for f in plan))
            self.assertEqual(sorted({i for f in plan for i in f}), list(range(bilder)))
            self.assertEqual(plan[-1][-1], bilder - 1)
            for a, b in zip(plan, plan[1:]):                      # Nachbarn überlappen sich
                self.assertGreaterEqual(a[-1] - b[0] + 1, min(sdvideo.UEBERLAPPUNG, laenge - 1))
        self.assertEqual(sdvideo.fenster_gewicht(6).tolist(), [1, 2, 3, 3, 2, 1])
        self.assertEqual(sdvideo.bilder_zahl(15), 120)

    def test_vorgaben_je_bewegungs_modul(self):
        from videomotoren import sdvideo
        v = sdvideo.vorgaben(Path("animatediff_lightning_8step_comfyui.safetensors"))
        self.assertEqual((v["schritte"], v["cfg"], v["beta"]), (8, 1.0, "linear"))
        self.assertEqual(sdvideo.vorgaben(Path("hsxl_temporal_layers.f16.safetensors"))["fenster"], 8)
        self.assertEqual(sdvideo.vorgaben(Path("v3_sd15_mm.safetensors"))["cfg"], 7.5)
        for name, (groesse, summe, art) in sdvideo.BEKANNT.items():
            self.assertTrue(groesse > 0 and len(summe) == 64 and art in ("sd15", "sdxl"), name)


@unittest.skipUnless(REFERENZ, "torch und diffusers nur in der Entwicklungs-Umgebung")
class GanzerLauf(unittest.TestCase):
    """Ein Mini-Checkpoint (Bildnetz + CLIP + VAE in einer Datei) und Mini-Zeit-Schichten: finden, prüfen, rechnen."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from diffusers import MotionAdapter
        from safetensors.torch import save_file
        cls.wurzel = Path(tempfile.mkdtemp(prefix="sdvideo_lauf_"))
        modell = cls.wurzel / "Mini"
        (modell / "tokenizer_clip").mkdir(parents=True)
        unet = Bildnetz()._sd15()
        gewichte = {"model.diffusion_model." + k: v for k, v in original_namen(unet).items()}
        clip = _clip(768, 1, "quick_gelu")
        gewichte.update({"cond_stage_model.transformer." + k: v for k, v in clip_namen(clip).items()})
        gewichte.update({"first_stage_model." + k: v for k, v in vae_namen(_vae(4)).items()})
        save_file({k: v.half().contiguous() for k, v in gewichte.items()}, str(modell / "mini.safetensors"))
        adapter = MotionAdapter(block_out_channels=(32, 64), motion_layers_per_block=2, motion_num_attention_heads=8,
                                motion_norm_num_groups=32, motion_max_seq_length=32, use_motion_mid_block=False)
        _zufall(adapter)
        zeit = {k: v.contiguous() for k, v in bewegung_namen(adapter).items()}
        zeit["down_blocks.0.motion_modules.0.temporal_transformer.transformer_blocks.0.attention_blocks.0"
             ".pos_encoder.pe"] = torch.zeros(1, 32, 32)
        cls.zeit_pfad = modell / "bewegung_mini.safetensors"
        save_file(zeit, str(cls.zeit_pfad))
        g = torch.Generator().manual_seed(5)
        from PIL import Image
        cls.start = cls.wurzel / "start.png"
        Image.fromarray((torch.rand(96, 80, 3, generator=g) * 255).to(torch.uint8).numpy()).save(cls.start)

    @classmethod
    def tearDownClass(cls):
        import shutil
        from bildmotoren import gemeinsam
        gemeinsam.entladen()
        shutil.rmtree(cls.wurzel, ignore_errors=True)

    def _umgebung(self):
        import videomotoren
        from unittest import mock
        from bildmotoren import gemeinsam
        from videomotoren import sdvideo
        videomotoren._PASST.clear()
        for flicken in (mock.patch.object(videomotoren, "ORDNER", [self.wurzel]),
                        mock.patch.object(sdvideo, "_tokenizer", lambda ordner: (lambda text: IDS)),
                        mock.patch.object(gemeinsam, "geraet", lambda: (torch.device("cpu"), torch.float32))):
            flicken.start()
            self.addCleanup(flicken.stop)
        return videomotoren, sdvideo

    def test_finden_und_was_fehlt(self):
        vm, sdvideo = self._umgebung()
        self.assertEqual({n: m["bauart"] for n, m in vm.modelle().items()}, {"Mini": "sdvideo"})
        t = vm.teile("Mini")
        self.assertEqual((t["art"], t["bewegung"], t["tokenizer"]), ("sd15", self.zeit_pfad, None))
        self.assertIn("CLIP-Tokenizer", vm.missing_reason("Mini"))
        (self.wurzel / "Mini" / "tokenizer_clip" / "tokenizer.json").write_text("{}", encoding="utf-8")
        self.addCleanup((self.wurzel / "Mini" / "tokenizer_clip" / "tokenizer.json").unlink)
        self.assertIsNone(vm.missing_reason("Mini"))
        self.assertEqual((vm.merkmal("Mini"), vm.hat_ton("Mini"), vm.nimmt_startbild("Mini")),
                         (sdvideo.MERKMAL, False, True))
        weg = self.zeit_pfad.with_suffix(".beiseite")
        self.zeit_pfad.rename(weg)
        self.addCleanup(lambda: weg.rename(self.zeit_pfad) if weg.exists() else None)
        vm._PASST.clear()
        self.assertIn("Bewegungs-Modul", vm.missing_reason("Mini"))
        weg.rename(self.zeit_pfad)

    def test_video_mit_fenstern_und_startbild(self):
        vm, sdvideo = self._umgebung()
        (self.wurzel / "Mini" / "tokenizer_clip" / "tokenizer.json").write_text("{}", encoding="utf-8")
        self.addCleanup((self.wurzel / "Mini" / "tokenizer_clip" / "tokenizer.json").unlink)
        t = vm.teile("Mini")
        meldungen = []
        kw = dict(sekunden=3.0, breite=64, hoehe=64, fps=8, seed=11, device=torch.device("cpu"), dtype=torch.float32,
                  on_status=meldungen.append)
        bilder, ton, _rate = sdvideo.malen("eine Probe", t, einstellungen={"schritte": 2, "cfg": 2.0}, **kw)
        self.assertEqual((bilder.shape, str(bilder.dtype), ton), ((24, 64, 64, 3), "uint8", None))   # 3 s × 8 Bilder
        self.assertIn(f"{sdvideo.TITEL}: Schritt 2/2", meldungen)
        nochmal, _, _ = sdvideo.malen("eine Probe", t, einstellungen={"schritte": 2, "cfg": 2.0}, **kw)
        self.assertTrue((bilder == nochmal).all())                                # gleicher Seed, gleiches Video
        anders, _, _ = sdvideo.malen("eine Probe", t, einstellungen={"schritte": 2, "cfg": 2.0}, **{**kw, "seed": 12})
        self.assertFalse((bilder == anders).all())
        # Startbild: das erste Bild ist das ein- und wieder ausgepackte Startbild
        from videomotoren import sd_vae
        mit, _, _ = sdvideo.malen("eine Probe", t, einstellungen={"schritte": 4, "cfg": 1.0, "start": str(self.start)},
                                  **kw)
        vae = sd_vae.laden(t["dit"])
        erwartet = sd_vae.auspacken(vae, sd_vae.einpacken(vae, sdvideo._startbild(self.start, 64, 64), "sd15", "cpu"),
                                    "sd15", "cpu")[0]
        self.assertLessEqual(int(abs(mit[0].astype(int) - erwartet.astype(int)).max()), 1)
        self.assertFalse((mit[5] == erwartet).all())
        with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
            sdvideo.malen("eine Probe", t, einstellungen={"schritte": 2}, **{**kw, "abbruch": lambda: True})

    def test_ueber_die_gemeinsame_tuer_mit_acht_bildern_je_sekunde(self):
        from unittest import mock
        vm, sdvideo = self._umgebung()
        (self.wurzel / "Mini" / "tokenizer_clip" / "tokenizer.json").write_text("{}", encoding="utf-8")
        self.addCleanup((self.wurzel / "Mini" / "tokenizer_clip" / "tokenizer.json").unlink)
        import profilordner
        import videodatei
        geschrieben = {}

        def schreiben(bilder, pfad, fps, ton=None, **kw):
            geschrieben.update(bilder=bilder.shape, fps=fps, ton=ton)

        gesehen = {}
        echt = sdvideo.malen

        def malen(prompt, teile, **kw):
            gesehen.update(groesse=(kw["breite"], kw["hoehe"]), start=kw["einstellungen"].get("start"))
            return echt(prompt, teile, **{**kw, "breite": 64, "hoehe": 64})

        with mock.patch.object(videodatei, "schreiben", schreiben), mock.patch.object(sdvideo, "malen", malen), \
                mock.patch.object(profilordner, "video_pfad", lambda endung: self.wurzel / ("aus" + endung)), \
                mock.patch.object(vm, "_config", lambda: {"video_motor_entladen": 300}):
            vm.generate("eine Probe", sekunden=1.0, seed=3, steps=1, modell="Mini",
                        extra={"format": "quer", "start": str(self.start)})
        self.assertEqual(geschrieben, {"bilder": (8, 64, 64, 3), "fps": 8, "ton": None})
        self.assertEqual(gesehen, {"groesse": (768, 512), "start": str(self.start)})

    def test_nur_gepruefte_bewegungs_module(self):
        from videomotoren import sdvideo
        falsch = self.wurzel / "animatediff_lightning_4step_comfyui.safetensors"
        falsch.write_bytes(b"x" * 100)
        with self.assertRaisesRegex(RuntimeError, "Größe stimmt nicht"):
            sdvideo.pruefen(falsch)
        fremd = self.wurzel / "irgendwas.ckpt"
        fremd.write_bytes(b"x" * 100)
        with self.assertRaisesRegex(RuntimeError, "unbekannte Pickle-Datei"):
            sdvideo._umschreiben(fremd)
        self.assertFalse(sdvideo._bewegung_passt(fremd, "sd15", 4))
        sdvideo.pruefen(self.zeit_pfad)                              # unbekannter Name: nichts zu prüfen


class StartbildVonSelbst(unittest.TestCase):
    """Nimmt der Video-Motor ein Startbild und ist keines da, malt der Bild-Motor zuerst eines."""

    def _lauf(self, *, startbild=None, einstellung="auto", malen=None, bild_motor_fehlt=None, auftrag=None,
              plan=None):
        from unittest import mock
        import imagegen
        import video
        import videomotoren
        gemalt, gerechnet = [], {}

        def paint(prompt, **kw):
            gemalt.append((prompt, kw))
            if malen is not None:
                return malen()
            return "C:/bilder/start.png"

        def generate(prompt, **kw):
            gerechnet.update(prompt=prompt, extra=kw.get("extra"))
            return "C:/videos/aus.mp4"

        huelle = mock.Mock(side_effect=AssertionError("die Grafikkarte gehört schon dem Video-Auftrag"))
        huelle.__wrapped__ = paint
        with mock.patch.object(video, "motor_aktiv", lambda name=None: True), \
                mock.patch.object(videomotoren, "nimmt_startbild", lambda name=None: True), \
                mock.patch.object(videomotoren, "einstellung", lambda k: einstellung), \
                mock.patch.object(videomotoren, "plan", lambda *a, **k: plan or {"breite": 512, "hoehe": 768}), \
                mock.patch.object(videomotoren, "generate", generate), \
                mock.patch.object(imagegen, "paint", huelle), \
                mock.patch.object(imagegen, "missing_reason_aktiv", lambda: bild_motor_fehlt):
            pfad = video.generate("a woman walking", sekunden=3, startbild=startbild, modell="Mini",
                                  start_malen=auftrag if auftrag is not None else {"prompt": "kern, a woman walking",
                                                                                   "seed": 7, "lora": "C:/l.safetensors",
                                                                                   "figur": True})
        return pfad, gemalt, gerechnet, list(video.GENUTZT), list(video.HINWEISE)

    def test_malt_im_format_des_videos_und_gibt_das_bild_weiter(self):
        pfad, gemalt, gerechnet, genutzt, hinweise = self._lauf()
        self.assertEqual(pfad, "C:/videos/aus.mp4")
        self.assertEqual(len(gemalt), 1)
        prompt, kw = gemalt[0]
        self.assertEqual(prompt, "kern, a woman walking")                  # der Bild-Prompt, nicht der Video-Prompt
        self.assertEqual((kw["size"], kw["seed"], kw["figur"], kw["lora"]), ((832, 1216), 7, True, "C:/l.safetensors"))
        self.assertEqual(gerechnet, {"prompt": "a woman walking", "extra": {"start": "C:/bilder/start.png"}})
        self.assertEqual(genutzt, ["gemaltes Startbild", "Startbild"])
        self.assertEqual(hinweise, [])
        _, gemalt, _, _, _ = self._lauf(plan={"breite": 768, "hoehe": 512})
        self.assertEqual(gemalt[0][1]["size"], (1216, 832))

    def test_eigenes_startbild_und_abgeschaltet_malen_nichts(self):
        _, gemalt, gerechnet, genutzt, _ = self._lauf(startbild="C:/eigen.png")
        self.assertEqual((gemalt, gerechnet["extra"], genutzt), ([], {"start": "C:/eigen.png"}, ["Startbild"]))
        _, gemalt, gerechnet, genutzt, hinweise = self._lauf(einstellung="aus")
        self.assertEqual((gemalt, gerechnet["extra"], genutzt, hinweise), ([], None, [], []))
        _, gemalt, gerechnet, _, _ = self._lauf(auftrag={})
        self.assertEqual((gemalt, gerechnet["extra"]), ([], None))

    def test_ohne_bild_motor_oder_bei_fehler_entsteht_das_video_aus_dem_text(self):
        def kaputt():
            raise RuntimeError("Speicher voll")

        pfad, gemalt, gerechnet, genutzt, hinweise = self._lauf(malen=kaputt)
        self.assertEqual((pfad, gerechnet["extra"], genutzt), ("C:/videos/aus.mp4", None, []))
        self.assertIn("Speicher voll", hinweise[0])
        _, gemalt, gerechnet, _, hinweise = self._lauf(bild_motor_fehlt="Krea fehlt")
        self.assertEqual((gemalt, gerechnet["extra"]), ([], None))
        self.assertIn("Bild-Motor", hinweise[0])

    def test_einstellung_und_schalter_figur(self):
        import videomotoren
        import main_quelle
        self.assertEqual(videomotoren.EINSTELLUNGEN["startbild"], ("auto", ("auto", "aus")))
        quelle = main_quelle.text()
        raum = {"re": re}
        exec(quelle[quelle.index("def _video_opts("):quelle.index("async def _funke_vorgaben_waehlen")], raum)
        o = raum["_video_opts"]('a woman dancing --figur --sekunden 15 --start "C:/a b.png"')
        self.assertEqual(o, {"figur": True, "sekunden": 15.0, "start": "C:/a b.png", "prompt": "a woman dancing"})
        self.assertNotIn("figur", raum["_video_opts"]("a --figurine in a shop"))


if __name__ == "__main__":
    unittest.main()
