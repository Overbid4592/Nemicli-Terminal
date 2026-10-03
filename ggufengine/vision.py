"""
Vision encoders for mmproj files.  `load_projector()` reads the projector type from
the file and picks the matching encoder:

* "gemma4v"         -> Gemma4Vision  (Gemma 4)
* "gemma4uv"        -> Gemma4UnifiedVision (Gemma 4 12B, no encoder layers)
* "qwen3vl_merger"  -> Qwen3VLVision (Qwen3-VL / Qwen3.5)

Every encoder exposes `marks` (begin, placeholder, end token of an image in the
prompt), `n_out` (width of the embeddings), `default_tokens`, `load()`, `unload()`
and `encode()`.

Gemma 4 vision encoder ("gemma4v" mmproj), following llama.cpp's
tools/mtmd/models/gemma4v.cpp and the transformers reference:

* patches of 16x16 pixels, pixel values scaled to [-1, 1], no patch bias
* learned 2-D position tables (one for x, one for y) added to the patch embeddings
* ViT blocks: RMSNorm -> attention (q/k RMSNorm with weight, v RMSNorm without,
  2-D NeoX RoPE with base 100: x on the first half of each head, y on the second,
  scale 1.0, bidirectional) -> RMSNorm -> residual -> RMSNorm -> gated GELU MLP
  -> RMSNorm -> residual
* "clippable" linears: inputs and outputs clamped to per-matrix limits from the file
* 3x3 average pooling over the patch grid, times sqrt(n_embd), weightless RMSNorm,
  projection to the language model width

The weights stay in the file mapping (RAM) and are only put on the GPU while an
image is encoded (`load()` / `unload()`).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import torch
import torch.nn.functional as F

from .gguf import GGUFError, GGUFFile
from .models.common import WeightLoader, rms_norm

IMAGE_TOKENS = 280          # soft tokens per image (transformers default)
MIN_IMAGE_TOKENS = 70       # llama.cpp limits for gemma4v
MAX_IMAGE_TOKENS = 1120


class _Clipped:
    """y = clamp(clamp(x, in_min, in_max) @ W^T, out_min, out_max)."""

    def __init__(self, ld: WeightLoader, name: str):
        self.w = ld.get(name + ".weight")
        lim = lambda s: float(ld.get(f"{name}.{s}", torch.float32).item()) if ld.has(f"{name}.{s}") else None
        self.in_min, self.in_max = lim("input_min"), lim("input_max")
        self.out_min, self.out_max = lim("output_min"), lim("output_max")

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        if self.in_min is not None or self.in_max is not None:
            x = x.clamp(self.in_min, self.in_max)
        y = F.linear(x.to(self.w.dtype), self.w)
        if self.out_min is not None or self.out_max is not None:
            y = y.clamp(self.out_min, self.out_max)
        return y


class _Block:
    def __init__(self, ld: WeightLoader, i: int, eps: float):
        p = f"v.blk.{i}."
        self.eps = eps
        self.ln1 = ld.norm(p + "ln1.weight")
        self.q, self.k, self.v = (_Clipped(ld, p + n) for n in ("attn_q", "attn_k", "attn_v"))
        self.o = _Clipped(ld, p + "attn_out")
        self.q_norm = ld.norm(p + "attn_q_norm.weight")
        self.k_norm = ld.norm(p + "attn_k_norm.weight")
        self.attn_post = ld.norm(p + "attn_post_norm.weight")
        self.ln2 = ld.norm(p + "ln2.weight")
        self.gate, self.up, self.down = (_Clipped(ld, p + n) for n in ("ffn_gate", "ffn_up", "ffn_down"))
        self.ffn_post = ld.norm(p + "ffn_post_norm.weight")


class Gemma4Vision:
    marks = ("<|image>", "<|image|>", "<image|>")
    default_tokens = IMAGE_TOKENS
    uses_grid = False

    def __init__(self, path: str, device, dtype=torch.bfloat16):
        self.gg = GGUFFile(path)
        g = self.gg
        if g.get("clip.vision.projector_type") != "gemma4v" or not g.get("clip.has_vision_encoder"):
            raise GGUFError("not a Gemma 4 vision projector (clip.vision.projector_type != gemma4v)")
        self.n_embd = int(g.require("clip.vision.embedding_length"))
        self.n_head = int(g.require("clip.vision.attention.head_count"))
        self.n_layer = int(g.require("clip.vision.block_count"))
        self.patch = int(g.require("clip.vision.patch_size"))
        self.n_out = int(g.require("clip.vision.projection_dim"))
        self.eps = float(g.get("clip.vision.attention.layer_norm_epsilon", 1e-6))
        self.merge = int(g.get("clip.vision.projector.scale_factor", 3))
        self.rope_base = 100.0
        if not (0 < self.n_layer <= 128 and 0 < self.patch <= 64 and self.n_embd % self.n_head == 0
                and 0 < self.merge <= 8):
            raise GGUFError("implausible vision hyper-parameters")
        self.head_dim = self.n_embd // self.n_head
        self.device = torch.device(device)
        self.dtype = dtype
        self.loaded = False

    # -- weights on the GPU only while needed ----------------------------------
    def load(self):
        if self.loaded:
            return
        ld = WeightLoader(self.gg, self.device, self.dtype, quant=False)
        pe = ld.get("v.patch_embd.weight", torch.float32)          # (E, 3, P, P)
        if tuple(pe.shape) != (self.n_embd, 3, self.patch, self.patch):
            raise GGUFError("v.patch_embd.weight has an unexpected shape")
        self.patch_embd = pe
        pos = ld.get("v.position_embd.weight", torch.float32)       # (2, n_pos, E)
        if pos.dim() != 3 or pos.shape[0] != 2 or pos.shape[2] != self.n_embd:
            raise GGUFError("v.position_embd.weight has an unexpected shape")
        self.pos_x, self.pos_y = pos[0], pos[1]
        self.blocks = [_Block(ld, i, self.eps) for i in range(self.n_layer)]
        self.proj = ld.get("mm.input_projection.weight")             # (n_out, E)
        if tuple(self.proj.shape) != (self.n_out, self.n_embd):
            raise GGUFError("mm.input_projection.weight has an unexpected shape")
        half = self.head_dim // 2
        self.inv_freq = 1.0 / (self.rope_base ** (torch.arange(0, half, 2, device=self.device,
                                                               dtype=torch.float32) / half))
        self.loaded = True

    def unload(self):
        for name in ("patch_embd", "pos_x", "pos_y", "blocks", "proj", "inv_freq"):
            self.__dict__.pop(name, None)
        self.loaded = False
        if self.device.type == "cuda":
            torch.cuda.empty_cache()

    # -- preprocessing -----------------------------------------------------------
    max_tokens = MAX_IMAGE_TOKENS

    def grid_for(self, width: int, height: int, tokens: int = IMAGE_TOKENS) -> Tuple[int, int]:
        """(tokens across, tokens down) keeping the aspect ratio, at most `tokens` in total."""
        tokens = max(MIN_IMAGE_TOKENS, min(int(tokens), getattr(self, "max_tokens", MAX_IMAGE_TOKENS)))
        if width <= 0 or height <= 0:
            raise ValueError("empty image")
        s = math.sqrt(tokens / (width * height))
        nx, ny = max(1, int(width * s)), max(1, int(height * s))
        while nx * ny > tokens:
            if nx >= ny:
                nx -= 1
            else:
                ny -= 1
        return nx, ny

    def pixels(self, image, tokens: int = IMAGE_TOKENS) -> torch.Tensor:
        """PIL image -> (3, H, W) float in [0, 1], H and W multiples of patch * merge."""
        from PIL import Image
        img = image.convert("RGB")
        nx, ny = self.grid_for(img.width, img.height, tokens)
        unit = self.patch * self.merge
        img = img.resize((nx * unit, ny * unit), Image.BICUBIC)
        raw = torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8)
        return raw.view(img.height, img.width, 3).permute(2, 0, 1).float() / 255.0

    # -- encoder -----------------------------------------------------------------
    def _rope(self, x: torch.Tensor, px: torch.Tensor, py: torch.Tensor) -> torch.Tensor:
        """x: (N, H, D).  NeoX rotation of dims [0, D/2) by x and [D/2, D) by y."""
        half = x.shape[-1] // 2

        def rot(part, pos):
            f = torch.outer(pos.float(), self.inv_freq)                 # (N, half/2)
            cos, sin = f.cos().unsqueeze(1), f.sin().unsqueeze(1)
            a, b = part[..., :half // 2], part[..., half // 2:]
            return torch.cat([a * cos - b * sin, a * sin + b * cos], dim=-1)

        return torch.cat([rot(x[..., :half], px), rot(x[..., half:], py)], dim=-1)

    @torch.inference_mode()
    def encode(self, image, tokens: int = IMAGE_TOKENS) -> torch.Tensor:
        """PIL image -> (n_tokens, projection_dim) float32 embeddings on the device."""
        if not self.loaded:
            raise RuntimeError("vision weights are not loaded")
        pix = self.pixels(image, tokens).to(self.device)
        pix = pix * 2.0 - 1.0
        x = F.conv2d(pix.unsqueeze(0), self.patch_embd, stride=self.patch)[0]      # (E, gy, gx)
        E, gy, gx = x.shape
        if gx > self.pos_x.shape[0] or gy > self.pos_y.shape[0]:
            raise GGUFError("image larger than the position tables")
        x = x.flatten(1).t()                                                          # (N, E), row-major
        py = torch.arange(gy, device=self.device).repeat_interleave(gx)
        px = torch.arange(gx, device=self.device).repeat(gy)
        h = x + self.pos_x.index_select(0, px) + self.pos_y.index_select(0, py)
        N, H, D = h.shape[0], self.n_head, self.head_dim
        for b in self.blocks:
            r = h
            y = rms_norm(h, b.ln1, self.eps)
            q = rms_norm(b.q(y).float().view(N, H, D), b.q_norm, self.eps)
            k = rms_norm(b.k(y).float().view(N, H, D), b.k_norm, self.eps)
            v = rms_norm(b.v(y).float().view(N, H, D), 1.0, self.eps)
            q, k = self._rope(q, px, py), self._rope(k, px, py)
            a = F.scaled_dot_product_attention(q.transpose(0, 1).unsqueeze(0).to(self.dtype),
                                               k.transpose(0, 1).unsqueeze(0).to(self.dtype),
                                               v.transpose(0, 1).unsqueeze(0).to(self.dtype),
                                               scale=1.0)[0].transpose(0, 1).reshape(N, E)
            h = r + rms_norm(b.o(a).float(), b.attn_post, self.eps)
            y = rms_norm(h, b.ln2, self.eps)
            y = b.down(F.gelu(b.gate(y).float(), approximate="tanh") * b.up(y).float()).float()
            h = h + rms_norm(y, b.ffn_post, self.eps)
        m = self.merge
        pooled = F.avg_pool2d(h.t().reshape(1, E, gy, gx), m, m)[0]                  # (E, gy/m, gx/m)
        pooled = pooled.flatten(1).t() * math.sqrt(E)
        pooled = rms_norm(pooled, 1.0, self.eps)
        return F.linear(pooled.to(self.proj.dtype), self.proj).float()


class Gemma4UnifiedVision(Gemma4Vision):
    """Gemma 4 12B "Unified" ("gemma4uv" mmproj, llama.cpp tools/mtmd/models/gemma4uv.cpp):
    no encoder layers.  Blocks of patch_size * 3 pixels (the 3x3 merge is built into
    the patch embedding) with values in [0, 1] -> LayerNorm -> linear (+bias) ->
    LayerNorm -> + x/y position tables -> LayerNorm -> weightless RMSNorm -> projection.
    The feature order of a block is (channel, row, column) as in ggml's im2col.

    The language model reads an image's tokens bidirectionally, so one image has to
    fit into one forward call of the sliding-window model (at most PREFILL_CHUNK)."""

    LN_EPS = 1e-5                # PyTorch LayerNorm default (Gemma4UnifiedVisionEmbedder)

    def __init__(self, path: str, device, dtype=torch.bfloat16):
        from .models.common import PREFILL_CHUNK
        self.gg = GGUFFile(path)
        g = self.gg
        if g.get("clip.vision.projector_type") != "gemma4uv" or not g.get("clip.has_vision_encoder"):
            raise GGUFError("not a Gemma 4 unified vision projector (clip.vision.projector_type != gemma4uv)")
        self.n_embd = int(g.require("clip.vision.embedding_length"))
        self.merge_in_patch = int(g.get("clip.vision.projector.scale_factor", 3))
        self.patch = int(g.require("clip.vision.patch_size")) * self.merge_in_patch
        self.merge = 1
        self.n_out = int(g.require("clip.vision.projection_dim"))
        self.eps = float(g.get("clip.vision.attention.layer_norm_epsilon", 1e-6))
        if not (0 < self.n_embd <= 16384 and 0 < self.patch <= 256 and 0 < self.n_out <= 16384):
            raise GGUFError("implausible vision hyper-parameters")
        self.max_tokens = min(MAX_IMAGE_TOKENS, PREFILL_CHUNK)
        self.device = torch.device(device)
        self.dtype = dtype
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        ld = WeightLoader(self.gg, self.device, self.dtype, quant=False)
        f32 = lambda n: ld.get(n, torch.float32)
        roh = 3 * self.patch * self.patch
        self.patch_embd = f32("v.patch_embd.weight")                  # (E, 3 * P * P)
        self.patch_bias = f32("v.patch_embd.bias")
        if tuple(self.patch_embd.shape) != (self.n_embd, roh) or self.patch_bias.numel() != self.n_embd:
            raise GGUFError("v.patch_embd has an unexpected shape")
        self.norms = [(f32(f"v.patch_norm.{i}.weight"), f32(f"v.patch_norm.{i}.bias")) for i in (1, 2, 3)]
        if self.norms[0][0].numel() != roh or any(w.numel() != self.n_embd for w, _ in self.norms[1:]):
            raise GGUFError("v.patch_norm has an unexpected shape")
        pos = f32("v.position_embd.weight")                            # (2, n_pos, E)
        if pos.dim() != 3 or pos.shape[0] != 2 or pos.shape[2] != self.n_embd:
            raise GGUFError("v.position_embd.weight has an unexpected shape")
        self.pos_x, self.pos_y = pos[0], pos[1]
        self.proj = f32("mm.input_projection.weight")                  # (n_out, E)
        if tuple(self.proj.shape) != (self.n_out, self.n_embd):
            raise GGUFError("mm.input_projection.weight has an unexpected shape")
        self.loaded = True

    def unload(self):
        for name in ("patch_embd", "patch_bias", "norms", "pos_x", "pos_y", "proj"):
            self.__dict__.pop(name, None)
        self.loaded = False
        if self.device.type == "cuda":
            torch.cuda.empty_cache()

    @torch.inference_mode()
    def encode(self, image, tokens: int = IMAGE_TOKENS) -> torch.Tensor:
        """PIL image -> (n_tokens, projection_dim) float32 embeddings on the device."""
        if not self.loaded:
            raise RuntimeError("vision weights are not loaded")
        pix = self.pixels(image, tokens).to(self.device)                              # [0, 1]
        P = self.patch
        gy, gx = pix.shape[1] // P, pix.shape[2] // P
        if gx > self.pos_x.shape[0] or gy > self.pos_y.shape[0]:
            raise GGUFError("image larger than the position tables")
        x = F.unfold(pix.unsqueeze(0), kernel_size=P, stride=P)[0].t()               # (N, 3*P*P), row-major
        (w1, b1), (w2, b2), (w3, b3) = self.norms
        x = F.layer_norm(x, (x.shape[-1],), w1, b1, self.LN_EPS)
        x = F.linear(x, self.patch_embd, self.patch_bias)
        x = F.layer_norm(x, (self.n_embd,), w2, b2, self.LN_EPS)
        py = torch.arange(gy, device=self.device).repeat_interleave(gx)
        px = torch.arange(gx, device=self.device).repeat(gy)
        x = x + self.pos_x.index_select(0, px) + self.pos_y.index_select(0, py)
        x = F.layer_norm(x, (self.n_embd,), w3, b3, self.LN_EPS)
        x = rms_norm(x, 1.0, self.eps)
        return F.linear(x, self.proj).float()


class Qwen3VLVision:
    """Qwen3-VL vision tower ("qwen3vl_merger" mmproj, also used by Qwen3.5):

    * 16x16 patches; the video patch embedding has two temporal taps, an image is
      both frames at once (conv with tap 0 + conv with tap 1 + bias); pixels to [-1, 1]
    * learned 48x48 position table, bilinearly interpolated to the patch grid
    * patches ordered in 2x2 merge windows (window row, window col, row, col)
    * ViT blocks: LayerNorm -> fused qkv -> 2-D rotary (row on the first half of the
      rotary frequencies, column on the second, NeoX pairs over the full head) ->
      bidirectional attention -> LayerNorm -> GELU(tanh) MLP, all linears with bias
    * merger: LayerNorm -> 4 neighbouring patches concatenated -> linear -> GELU -> linear
    The embeddings come out row-major over the merged grid; `encode` also returns its
    shape for the M-RoPE positions of the language model."""

    marks = ("<|vision_start|>", "<|image_pad|>", "<|vision_end|>")
    default_tokens = 1024       # merged tokens; enough for text on screenshots
    min_tokens, max_tokens = 64, 4096
    uses_grid = True

    def __init__(self, path: str, device, dtype=torch.bfloat16):
        self.gg = GGUFFile(path)
        g = self.gg
        if projector_type(g) != "qwen3vl_merger" or not g.get("clip.has_vision_encoder"):
            raise GGUFError("not a Qwen3-VL vision projector")
        self.n_embd = int(g.require("clip.vision.embedding_length"))
        self.n_head = int(g.require("clip.vision.attention.head_count"))
        self.n_layer = int(g.require("clip.vision.block_count"))
        self.patch = int(g.require("clip.vision.patch_size"))
        self.n_out = int(g.require("clip.vision.projection_dim"))
        self.eps = float(g.get("clip.vision.attention.layer_norm_epsilon", 1e-6))
        self.merge = int(g.get("clip.vision.spatial_merge_size", 2))
        self.mean = [float(x) for x in (g.get("clip.vision.image_mean") or [0.5] * 3)]
        self.std = [float(x) for x in (g.get("clip.vision.image_std") or [0.5] * 3)]
        if not (0 < self.n_layer <= 128 and 0 < self.patch <= 64 and self.n_embd % self.n_head == 0
                and 0 < self.merge <= 8 and len(self.mean) == 3 and len(self.std) == 3
                and all(s > 0 for s in self.std)):
            raise GGUFError("implausible vision hyper-parameters")
        self.head_dim = self.n_embd // self.n_head
        if self.head_dim % 4:
            raise GGUFError("vision head size must be a multiple of 4")
        self.device = torch.device(device)
        self.dtype = dtype
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        ld = WeightLoader(self.gg, self.device, self.dtype, quant=False)
        E, P = self.n_embd, self.patch
        f32 = torch.float32
        self.patch_w = [ld.get(n, f32) for n in ("v.patch_embd.weight", "v.patch_embd.weight.1")
                        if ld.has(n)]
        if not self.patch_w or any(tuple(w.shape) != (E, 3, P, P) for w in self.patch_w):
            raise GGUFError("v.patch_embd.weight has an unexpected shape")
        self.patch_b = ld.get("v.patch_embd.bias", f32) if ld.has("v.patch_embd.bias") else None
        pos = ld.get("v.position_embd.weight", f32)                      # (side*side, E)
        side = int(round(math.sqrt(pos.shape[0])))
        if pos.dim() != 2 or pos.shape[1] != E or side * side != pos.shape[0]:
            raise GGUFError("v.position_embd.weight has an unexpected shape")
        self.pos_table = pos.view(side, side, E).permute(2, 0, 1).unsqueeze(0)   # (1, E, side, side)
        opt = lambda n: ld.get(n) if ld.has(n) else None
        self.blocks = []
        for i in range(self.n_layer):
            p = f"v.blk.{i}."
            self.blocks.append({
                "ln1": (ld.get(p + "ln1.weight", f32), ld.get(p + "ln1.bias", f32)),
                "ln2": (ld.get(p + "ln2.weight", f32), ld.get(p + "ln2.bias", f32)),
                "qkv": (ld.get(p + "attn_qkv.weight"), opt(p + "attn_qkv.bias")),
                "out": (ld.get(p + "attn_out.weight"), opt(p + "attn_out.bias")),
                "up": (ld.get(p + "ffn_up.weight"), opt(p + "ffn_up.bias")),
                "down": (ld.get(p + "ffn_down.weight"), opt(p + "ffn_down.bias")),
            })
        if tuple(self.blocks[0]["qkv"][0].shape) != (3 * E, E):
            raise GGUFError("attn_qkv has an unexpected shape")
        self.post_ln = (ld.get("v.post_ln.weight", f32), ld.get("v.post_ln.bias", f32))
        self.mm0 = (ld.get("mm.0.weight"), opt("mm.0.bias"))
        self.mm2 = (ld.get("mm.2.weight"), opt("mm.2.bias"))
        if self.mm0[0].shape[1] != E * self.merge ** 2 or self.mm2[0].shape[0] != self.n_out:
            raise GGUFError("merger has an unexpected shape")
        rot = self.head_dim // 2                                          # rotary dims of one axis pair
        self.inv_freq = 1.0 / (10000.0 ** (torch.arange(0, rot, 2, device=self.device,
                                                         dtype=torch.float32) / rot))
        self.loaded = True

    def unload(self):
        for name in ("patch_w", "patch_b", "pos_table", "blocks", "post_ln", "mm0", "mm2", "inv_freq"):
            self.__dict__.pop(name, None)
        self.loaded = False
        if self.device.type == "cuda":
            torch.cuda.empty_cache()

    # -- preprocessing -----------------------------------------------------------
    def pixels(self, image, tokens: int) -> torch.Tensor:
        """PIL image -> (3, H, W) normalised, H and W multiples of patch * merge."""
        from PIL import Image
        img = image.convert("RGB")
        tokens = max(self.min_tokens, min(int(tokens), self.max_tokens))
        nx, ny = _fit_grid(img.width, img.height, tokens)
        unit = self.patch * self.merge
        img = img.resize((nx * unit, ny * unit), Image.BICUBIC)
        raw = torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8)
        x = raw.view(img.height, img.width, 3).permute(2, 0, 1).float() / 255.0
        return (x - torch.tensor(self.mean).view(3, 1, 1)) / torch.tensor(self.std).view(3, 1, 1)

    # -- encoder -----------------------------------------------------------------
    def _window_order(self, x: torch.Tensor) -> torch.Tensor:
        """(C, gy, gx) -> (N, C) in merge-window order."""
        C, gy, gx = x.shape
        m = self.merge
        return x.reshape(C, gy // m, m, gx // m, m).permute(1, 3, 2, 4, 0).reshape(-1, C)

    @staticmethod
    def _rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        half = x.shape[-1] // 2
        return x * cos + torch.cat([-x[..., half:], x[..., :half]], dim=-1) * sin

    @torch.inference_mode()
    def encode(self, image, tokens: Optional[int] = None):
        """PIL image -> ((n_tokens, projection_dim) float32 embeddings, (rows, cols))."""
        if not self.loaded:
            raise RuntimeError("vision weights are not loaded")
        pix = self.pixels(image, tokens or self.default_tokens).to(self.device).unsqueeze(0)
        x = sum(F.conv2d(pix, w, stride=self.patch) for w in self.patch_w)[0]    # (E, gy, gx)
        if self.patch_b is not None:
            x = x + self.patch_b.view(-1, 1, 1)
        E, gy, gx = x.shape
        pos = F.interpolate(self.pos_table, size=(gy, gx), mode="bilinear", align_corners=True)[0]
        h = self._window_order(x + pos)                                              # (N, E)
        coords = torch.stack(torch.meshgrid(torch.arange(gy, device=self.device),
                                            torch.arange(gx, device=self.device), indexing="ij"))
        rc = self._window_order(coords).float()                                      # (N, 2)
        ang = torch.cat([torch.outer(rc[:, 0], self.inv_freq), torch.outer(rc[:, 1], self.inv_freq)], dim=-1)
        ang = torch.cat([ang, ang], dim=-1)                                          # (N, D)
        cos, sin = ang.cos().unsqueeze(1), ang.sin().unsqueeze(1)
        N, H, D = h.shape[0], self.n_head, self.head_dim
        lin = lambda t, wb: F.linear(t.to(wb[0].dtype), wb[0], wb[1]).float()
        for b in self.blocks:
            y = F.layer_norm(h, (E,), *b["ln1"], self.eps)
            q, k, v = lin(y, b["qkv"]).view(N, 3, H, D).unbind(1)
            q, k = self._rope(q, cos, sin), self._rope(k, cos, sin)
            a = F.scaled_dot_product_attention(q.transpose(0, 1).unsqueeze(0).to(self.dtype),
                                               k.transpose(0, 1).unsqueeze(0).to(self.dtype),
                                               v.transpose(0, 1).unsqueeze(0).to(self.dtype)
                                               )[0].transpose(0, 1).reshape(N, E)
            h = h + lin(a, b["out"])
            y = F.layer_norm(h, (E,), *b["ln2"], self.eps)
            h = h + lin(F.gelu(lin(y, b["up"]), approximate="tanh"), b["down"])
        m = self.merge
        y = F.layer_norm(h, (E,), *self.post_ln, self.eps).reshape(-1, E * m * m)
        return lin(F.gelu(lin(y, self.mm0)), self.mm2), (gy // m, gx // m)


def _fit_grid(width: int, height: int, tokens: int) -> Tuple[int, int]:
    """(across, down) keeping the aspect ratio, at most `tokens` cells."""
    if width <= 0 or height <= 0:
        raise ValueError("empty image")
    s = math.sqrt(tokens / (width * height))
    nx, ny = max(1, int(width * s)), max(1, int(height * s))
    while nx * ny > tokens:
        if nx >= ny:
            nx -= 1
        else:
            ny -= 1
    return nx, ny


# -- automatic selection -------------------------------------------------------
ENCODERS = {"gemma4v": Gemma4Vision, "gemma4uv": Gemma4UnifiedVision, "qwen3vl_merger": Qwen3VLVision}


def projector_type(gg: GGUFFile) -> Optional[str]:
    """Projector type of an mmproj (some files use clip.projector_type)."""
    return gg.get("clip.vision.projector_type") or gg.get("clip.projector_type")


def load_projector(path: str, device, dtype=torch.bfloat16):
    """The encoder matching the mmproj at `path` (weights not loaded yet)."""
    gg = GGUFFile(path)
    try:
        typ = projector_type(gg)
    finally:
        gg.close()
    cls = ENCODERS.get(typ)
    if cls is None:
        raise GGUFError(f"unsupported vision projector {typ!r}")
    return cls(path, device, dtype)
