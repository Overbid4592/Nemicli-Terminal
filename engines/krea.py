"""
krea.py - NemiCLIs eigene Pipeline für Krea 2 (getrennt von der SD-Pipeline).

Erwarteter Ordner `Models/Krea2/`:

    *.safetensors                 Diffusionsmodell (ComfyUI-Format, NVFP4 oder bf16)
    text_encoders/*.safetensors   Qwen3-VL-4B (FP8 skaliert oder bf16)
    vae/*.safetensors             Qwen-Image-VAE
    tokenizer/                    Qwen-Tokenizer (vocab.json + merges.txt oder tokenizer.json)

Ablauf:

    Prompt -> Qwen3-VL (12 abgegriffene Schichten) -> Single-Stream-DiT
    (Flow-Matching, eigener Sampler) -> VAE-Decoder -> PNG

Krea 2 kennt keinen Negativ-Prompt und läuft mit CFG 1: ein Modelldurchlauf
pro Schritt. Speicher wie ComfyUI mit --disable-smart-memory: jedes Teil ist
nur während seines Rechenschritts im VRAM und wartet sonst im Arbeitsspeicher.
Kommt 2 Minuten kein Bild (Config: `bild_krea_entladen`), wird auch der
Arbeitsspeicher geräumt.

Alle Netze sind hier von Hand nachgebaut. Es wird nichts aus dem Netz geladen.
"""

from __future__ import annotations

import json
import math
import struct
import threading
import time
from pathlib import Path

try:
    from paths import ROOT as _ROOT, INSTALL as _INSTALL
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parent.parent

# Datenordner zuerst, danach der Programmordner (als exe: der Ordner der exe)
KREA_DIRS = [_ROOT / "Models" / "Krea2", _INSTALL / "Models" / "Krea2"]
OUT_DIR = _ROOT / "Bilder"
MAX_EDGE = 2048

DEFAULTS = {
    "steps": 14,
    "size": (832, 1216),
    "sampler": "euler_ancestral",       # oder "euler"
    "shift": 1.15,
    "entladen_nach": 120,               # Sekunden ohne Bild, dann auch RAM frei
    "gesicht": True,                    # Gesichter nach dem Malen vergrößert nachmalen
    "gesicht_staerke": 0.45,            # Rauschstärke beim Nachmalen (0 = nichts, 1 = neu)
    "gesicht_schritte": 10,
    "gesicht_kante": 1024,              # Arbeitsgröße des Ausschnitts (längere Kante)
}

# Vorsatz beim Nachmalen; danach folgt der ursprüngliche Prompt (Figur, Licht, Stil).
GESICHT_SATZ = ("A close-up of the face with clear, detailed eyes, sharp irises, "
                "natural skin texture and fine detail, in sharp focus.")

# Qwen3-VL-Eingabe: dieselbe Systemvorlage wie Qwen-Image. Vor dem Weiterreichen
# wird alles bis einschließlich "<|im_start|>user\n" abgeschnitten.
_TEMPLATE = ("<|im_start|>system\nDescribe the image by detailing the color, shape, size, "
             "texture, quantity, text, spatial relationships of the objects and "
             "background:<|im_end|>\n<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n")
_TAPS = [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]     # hidden_states[k] = Eingang von Schicht k
_IM_START, _TOK_USER, _TOK_NL = 151644, 872, 198

# Latent-Normierung des Qwen-Image-VAE (identisch mit Wan 2.1)
_LAT_MEAN = [-0.7571, -0.7089, -0.9113, 0.1075, -0.1745, 0.9653, -0.1517, 1.5508,
             0.4134, -0.0715, 0.5517, -0.3632, -0.1922, -0.9497, 0.2503, -0.2921]
_LAT_STD = [2.8184, 1.4541, 2.3275, 2.6558, 1.2196, 1.7708, 2.6052, 2.0743,
            3.2687, 2.1526, 2.8652, 1.5579, 1.6382, 1.1253, 2.8251, 1.9160]

_E2M1 = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0,
         -0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -4.0, -6.0]


# ===========================================================================
#  Dateien finden
# ===========================================================================

def _kopf(path: Path) -> dict:
    """JSON-Kopf einer .safetensors-Datei, ohne die Gewichte zu laden."""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > (200 << 20):
                return {}
            return json.loads(f.read(n).decode("utf-8", "replace"))
    except Exception:
        return {}


def ist_krea2(path: Path) -> bool:
    keys = _kopf(path)
    return any(k.startswith("model.diffusion_model.txtfusion.") for k in keys)


def _erste(d: Path) -> Path | None:
    dateien = sorted(d.glob("*.safetensors")) if d.is_dir() else []
    return dateien[0] if dateien else None


def krea_dir() -> Path:
    """Der erste Krea2-Ordner, der ein Modell enthält (sonst der Datenordner)."""
    for d in KREA_DIRS:
        if d.is_dir() and any(ist_krea2(f) for f in d.glob("*.safetensors")):
            return d
    return KREA_DIRS[0]


def discover() -> dict[str, Path]:
    """ref -> Pfad aller Krea-2-Diffusionsmodelle."""
    d = krea_dir()
    if not d.is_dir():
        return {}
    return {f.stem: f for f in sorted(d.glob("*.safetensors")) if ist_krea2(f)}


def teile() -> dict[str, Path | None]:
    d = krea_dir()
    return {"text": _erste(d / "text_encoders"),
            "vae": _erste(d / "vae"),
            "tokenizer": d / "tokenizer"}


# --- Eigener Bild-Motor: gewähltes Modell ------------------------------------

def chosen_model() -> str | None:
    """Das per /bildmodel gewählte Krea-Modell; sonst das erste gefundene."""
    try:
        import config
        gemerkt = config.load().get("bild_krea_model") or None
    except Exception:
        gemerkt = None
    modelle = discover()
    if gemerkt and gemerkt in modelle:
        return gemerkt
    return next(iter(modelle), None)


SCHRITTE_MIN, SCHRITTE_MAX = 8, 16


def schritte() -> int:
    """Gespeicherte Schrittzahl (Config `bild_krea_schritte`, 8–16); sonst der Standard."""
    try:
        import config
        n = int(config.load().get("bild_krea_schritte") or DEFAULTS["steps"])
    except Exception:
        n = DEFAULTS["steps"]
    return min(max(n, SCHRITTE_MIN), SCHRITTE_MAX)


def set_schritte(n: int) -> int:
    """Schrittzahl dauerhaft speichern (auf 8–16 begrenzt). Gibt den gespeicherten Wert zurück."""
    import config
    n = min(max(int(n), SCHRITTE_MIN), SCHRITTE_MAX)
    config.update(bild_krea_schritte=n)
    return n


def set_chosen_model(ref: str) -> None:
    """Krea-Modell merken und Krea als Bild-Motor wählen."""
    import config
    config.update(bild_krea_model=ref, bild_backend="krea")


def _extlibs() -> None:
    """Als exe: torch & Co. aus einem installierten Python einbinden (extlibs.py).
    Die exe bringt sie nicht mit – ohne diesen Schritt fehlen sie immer."""
    try:
        import extlibs
        extlibs.enable()
    except Exception:
        pass


def missing_reason() -> str | None:
    """None, wenn Krea 2 malen kann. Sonst ein kurzer Hinweis."""
    _extlibs()
    fehlt = []
    for mod, nice in (("torch", "PyTorch"), ("safetensors", "safetensors"),
                      ("tokenizers", "tokenizers"), ("PIL", "Pillow"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except Exception:
            fehlt.append(nice)
    if fehlt:
        return "Es fehlt: " + ", ".join(fehlt) + "."
    d = krea_dir()
    if not discover():
        return f"Kein Krea-2-Modell in {d}."
    t = teile()
    if t["text"] is None:
        return f"Text-Encoder fehlt: {d / 'text_encoders'}"
    if t["vae"] is None:
        return f"VAE fehlt: {d / 'vae'}"
    if not _tokenizer_da(t["tokenizer"]):
        return f"Tokenizer fehlt: {t['tokenizer']} (vocab.json + merges.txt)"
    return None


def _tokenizer_da(d: Path) -> bool:
    return (d / "tokenizer.json").exists() or (
        (d / "vocab.json").exists() and (d / "merges.txt").exists())


# Vorzerlegung des Qwen-BPE (Qwen 2 bis 3 teilen Wortliste und Regeln)
_QWEN_SPLIT = (r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}"
               r"| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+")


def _tokenizer():
    """Qwen-Tokenizer aus dem lokalen Ordner: tokenizer.json oder vocab.json + merges.txt."""
    from tokenizers import AddedToken, Regex, Tokenizer, decoders, models, normalizers, pre_tokenizers
    d = teile()["tokenizer"]
    if (d / "tokenizer.json").exists():
        tok = Tokenizer.from_file(str(d / "tokenizer.json"))
    else:
        tok = Tokenizer(models.BPE.from_file(str(d / "vocab.json"), str(d / "merges.txt"),
                                             continuing_subword_prefix="", end_of_word_suffix="",
                                             fuse_unk=False, byte_fallback=False))
        tok.normalizer = normalizers.NFC()
        tok.pre_tokenizer = pre_tokenizers.Sequence([
            pre_tokenizers.Split(Regex(_QWEN_SPLIT), behavior="isolated", invert=False),
            pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
        tok.decoder = decoders.ByteLevel()
        cfg = d / "tokenizer_config.json"
        if cfg.exists():
            dec = json.loads(cfg.read_text(encoding="utf-8")).get("added_tokens_decoder", {})
            tok.add_special_tokens([AddedToken(v["content"], special=True, normalized=False)
                                    for _, v in sorted(dec.items(), key=lambda kv: int(kv[0]))])
    return lambda text: tok.encode(text, add_special_tokens=False).ids


# ===========================================================================
#  Gewichte: bf16, FP8 (skaliert) und NVFP4
# ===========================================================================

def _lade(path: Path, device, prefix: str = "", skip: tuple = ()) -> dict:
    from safetensors import safe_open
    out = {}
    with safe_open(str(path), framework="pt", device="cpu") as f:
        for k in f.keys():
            if not k.startswith(prefix) or k.startswith(skip):
                continue
            out[k[len(prefix):]] = f.get_tensor(k).to(device)
    return out


def _from_blocked(blocked, rows: int, cols: int):
    """cuBLAS-Kachelanordnung (128x4-Blöcke) der NVFP4-Skalen zurück in (rows, cols)."""
    nr, nc = -(-rows // 128), -(-cols // 4)
    x = blocked.reshape(-1, 32, 4, 4).transpose(1, 2)
    x = x.reshape(nr, nc, 128, 4).permute(0, 2, 1, 3)
    return x.reshape(nr * 128, nc * 4)[:rows, :cols]


_LUT_CACHE: dict = {}


def _lut256(device, dtype):
    """Byte -> (hohes Nibble, niedriges Nibble) als zwei E2M1-Werte."""
    key = (str(device), dtype)
    if key not in _LUT_CACHE:
        import torch
        e = torch.tensor(_E2M1, dtype=torch.float32)
        b = torch.arange(256)
        _LUT_CACHE[key] = torch.stack((e[b >> 4], e[b & 15]), -1).to(device, dtype)
    return _LUT_CACHE[key]


def _to_blocked(m):
    """(rows, cols) -> cuBLAS-Kachelanordnung, aufgefüllt auf 128x4."""
    import torch
    r, c = m.shape
    nr, nc = -(-r // 128), -(-c // 4)
    if (r, c) != (nr * 128, nc * 4):
        p = torch.zeros(nr * 128, nc * 4, device=m.device, dtype=m.dtype)
        p[:r, :c] = m
        m = p
    b = m.view(nr, 128, nc, 4).permute(0, 2, 1, 3)
    return b.reshape(-1, 4, 32, 4).transpose(1, 2).reshape(nr * 128, nc * 4)


_FP4_TAB: dict = {}


def _fp4_tabellen(device, dtype):
    """Grenzen zwischen den 15 E2M1-Werten und Paar-Index -> gepacktes Byte."""
    key = (str(device), dtype)
    if key not in _FP4_TAB:
        import torch
        e = torch.tensor([-6, -4, -3, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3, 4, 6.0])
        code = torch.tensor([15, 14, 13, 12, 11, 10, 9, 0, 1, 2, 3, 4, 5, 6, 7], dtype=torch.int32)
        paar = (code[:, None] * 16 + code[None, :]).to(torch.uint8).flatten()
        _FP4_TAB[key] = (((e[1:] + e[:-1]) / 2).to(device, dtype), paar.to(device))
    return _FP4_TAB[key]


def _fp4_quant(x):
    """Aktivierung (M, K) -> NVFP4: gepackte Werte, Blockskalen (gekachelt), globale Skala."""
    import torch
    M, K = x.shape
    blk = x.view(M, K // 16, 16)
    am = torch.linalg.vector_norm(blk, ord=float("inf"), dim=-1).float()
    gs = (am.amax() / 2688.0).clamp(min=1e-12)             # 448 (FP8) * 6 (E2M1)
    bs = (am / (6.0 * gs)).clamp(max=448.0).to(torch.float8_e4m3fn)
    bsf = bs.float()
    inv = torch.where(bsf == 0, 0.0, 1.0 / (gs * bsf)).to(x.dtype)
    v = (blk * inv.unsqueeze(-1)).view(M, K)
    grenzen, paar = _fp4_tabellen(x.device, x.dtype)
    i = torch.bucketize(v, grenzen, out_int32=True)
    q = paar[i[:, 0::2] * 15 + i[:, 1::2]]                  # erstes Element im hohen Nibble
    return q, _to_blocked(bs), gs


_FP4_OK: dict = {}


def _fp4_bereit(device) -> bool:
    """Kann die Karte NVFP4 direkt multiplizieren (Blackwell, torch mit scaled_mm)?"""
    key = str(device)
    if key not in _FP4_OK:
        import torch
        import torch.nn.functional as F
        ok = (torch.device(device).type == "cuda" and hasattr(F, "scaled_mm")
              and hasattr(torch, "float4_e2m1fn_x2")
              and torch.cuda.get_device_capability(device) >= (10, 0))
        _FP4_OK[key] = ok
    return _FP4_OK[key]


class Eingabe:
    """Eine Aktivierung, die mehrere Schichten teilen: NVFP4-Fassung nur einmal rechnen."""

    def __init__(self, x):
        self.x = x
        self._q = None

    def fp4(self):
        if self._q is None:
            self._q = _fp4_quant(self.x.reshape(-1, self.x.shape[-1]))
        return self._q


class _Linear:
    """Linear-Schicht für bf16-, FP8- und NVFP4-Gewichte."""

    def __init__(self, sd: dict, name: str, dtype):
        import torch
        w = sd.pop(name + ".weight")
        self.bias = sd.pop(name + ".bias", None)
        if self.bias is not None:
            self.bias = self.bias.to(dtype)
        quant = sd.pop(name + ".comfy_quant", None)
        fmt = json.loads(bytes(quant.cpu().tolist())).get("format") if quant is not None else None
        self.dtype = dtype
        if fmt == "nvfp4":
            ts = sd.pop(name + ".weight_scale_2").float()
            bs = sd.pop(name + ".weight_scale")
            self.out_f, self.in_f = w.shape[0], w.shape[1] * 2
            self.q = w                                      # uint8, zwei Werte je Byte
            if _fp4_bereit(w.device):
                self.art = "nvfp4_mm"
                self.bs, self.ts = bs.reshape(-1), ts
            else:
                self.art = "nvfp4"
                bs = _from_blocked(bs, self.out_f, self.in_f // 16)
                self.scale = (bs.float() * ts).to(dtype)
        elif w.dtype in (torch.float8_e4m3fn, torch.float8_e5m2):
            s = sd.pop(name + ".weight_scale", None)
            self.w = w
            self.scale = s.float().to(dtype) if s is not None else None
            self.art = "fp8"
        else:
            self.w = w.to(dtype)
            self.art = "plain"

    def weight(self):
        import torch.nn.functional as F
        if self.art == "nvfp4":
            w = F.embedding(self.q.int(), _lut256(self.q.device, self.dtype))
            w = w.view(self.out_f, self.in_f // 16, 16) * self.scale.unsqueeze(-1)
            return w.view(self.out_f, self.in_f)
        if self.art == "fp8":
            w = self.w.to(self.dtype)
            return w * self.scale if self.scale is not None else w
        return self.w

    def __call__(self, x):
        import torch
        import torch.nn.functional as F
        ein = x if isinstance(x, Eingabe) else None
        x = ein.x if ein else x
        if self.art != "nvfp4_mm":
            return F.linear(x, self.weight(), self.bias)
        q, bs, gs = (ein or Eingabe(x)).fp4()
        S, W = F.ScalingType, F.SwizzleType
        y = F.scaled_mm(
            q.view(torch.float4_e2m1fn_x2), self.q.view(torch.float4_e2m1fn_x2).t(),
            [bs.view(-1), gs], [S.BlockWise1x16, S.TensorWise],
            [self.bs, self.ts], [S.BlockWise1x16, S.TensorWise],
            swizzle_a=[W.SWIZZLE_32_4_4, W.NO_SWIZZLE], swizzle_b=[W.SWIZZLE_32_4_4, W.NO_SWIZZLE],
            bias=self.bias, output_dtype=self.dtype)
        return y.view(*x.shape[:-1], self.out_f)


_SDPA_REIHENFOLGE = None


def _sdpa(q, k, v, **kw):
    """scaled_dot_product_attention mit cuDNN zuerst (auf Blackwell deutlich schneller)."""
    global _SDPA_REIHENFOLGE
    import torch.nn.functional as F
    from torch.nn.attention import SDPBackend, sdpa_kernel
    if _SDPA_REIHENFOLGE is None:
        _SDPA_REIHENFOLGE = [SDPBackend.CUDNN_ATTENTION, SDPBackend.FLASH_ATTENTION,
                             SDPBackend.EFFICIENT_ATTENTION, SDPBackend.MATH]
    with sdpa_kernel(_SDPA_REIHENFOLGE, set_priority=True):
        return F.scaled_dot_product_attention(q, k, v, **kw)


def _rms(x, w, eps):
    """RMSNorm mit fertigem float32-Gewicht."""
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w, eps=eps).to(x.dtype)


# ===========================================================================
#  Text: Qwen3-VL-4B (nur der Sprachteil)
# ===========================================================================

class _Qwen3Text:
    HEADS, KV_HEADS, HEAD_DIM, EPS, THETA = 32, 8, 128, 1e-6, 5_000_000.0

    def __init__(self, path: Path, device, dtype):
        n_layers = max(_TAPS)                      # Schicht 35 und die Schluss-Norm braucht es nicht
        skip = ("model.visual.", "lm_head.", "model.norm.") + tuple(
            f"model.layers.{i}." for i in range(n_layers, 64))
        sd = _lade(path, device, prefix="model.", skip=skip)
        self.dtype = dtype
        self.embed = sd.pop("embed_tokens.weight").to(dtype)
        self.layers = []
        for i in range(n_layers):
            p = f"layers.{i}."
            L = {n: _Linear(sd, p + n, dtype) for n in
                 ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
                  "self_attn.o_proj", "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj")}
            for n in ("input_layernorm", "post_attention_layernorm",
                      "self_attn.q_norm", "self_attn.k_norm"):
                L[n] = sd.pop(p + n + ".weight").float()
            self.layers.append(L)

    def _rope(self, n, device):
        import torch
        inv = 1.0 / (self.THETA ** (torch.arange(0, self.HEAD_DIM, 2, device=device).float()
                                    / self.HEAD_DIM))
        f = torch.arange(n, device=device).float()[:, None] * inv[None]
        emb = torch.cat((f, f), -1)
        return emb.cos()[None, None], emb.sin()[None, None]

    @staticmethod
    def _rot(x, cos, sin):
        import torch
        h = x.shape[-1] // 2
        xf = x.float()
        rot = torch.cat((-xf[..., h:], xf[..., :h]), -1)
        return (xf * cos + rot * sin).to(x.dtype)

    def taps(self, ids):
        """(1, seq, 12, 2560): die abgegriffenen Zwischenzustände."""
        import torch
        import torch.nn.functional as F
        x = self.embed[ids][None]
        n = x.shape[1]
        cos, sin = self._rope(n, x.device)
        out = []
        H, KV, D = self.HEADS, self.KV_HEADS, self.HEAD_DIM
        for i in range(max(_TAPS) + 1):
            if i in _TAPS:
                out.append(x)
            if i == max(_TAPS):
                break
            L = self.layers[i]
            h = _rms(x, L["input_layernorm"], self.EPS)
            q = L["self_attn.q_proj"](h).view(1, n, H, D).transpose(1, 2)
            k = L["self_attn.k_proj"](h).view(1, n, KV, D).transpose(1, 2)
            v = L["self_attn.v_proj"](h).view(1, n, KV, D).transpose(1, 2)
            q = self._rot(_rms(q, L["self_attn.q_norm"], self.EPS), cos, sin)
            k = self._rot(_rms(k, L["self_attn.k_norm"], self.EPS), cos, sin)
            a = _sdpa(q, k, v, is_causal=True, enable_gqa=True)
            x = x + L["self_attn.o_proj"](a.transpose(1, 2).reshape(1, n, H * D))
            h = _rms(x, L["post_attention_layernorm"], self.EPS)
            x = x + L["mlp.down_proj"](F.silu(L["mlp.gate_proj"](h)) * L["mlp.up_proj"](h))
        return torch.stack(out, 2)


def _encode(prompt: str, te, device):
    import torch
    tok = _tokenizer()
    ids = tok(_TEMPLATE.format(prompt))
    ende, gesehen = 0, 0
    for i, t in enumerate(ids):
        if t == _IM_START and gesehen < 2:
            ende, gesehen = i, gesehen + 1
    if len(ids) > ende + 3 and ids[ende + 1] == _TOK_USER and ids[ende + 2] == _TOK_NL:
        ende += 3
    with torch.no_grad():
        h = te.taps(torch.tensor(ids, device=device))
    return h[:, ende:].contiguous()


# ===========================================================================
#  Diffusionsmodell: Single-Stream-DiT
# ===========================================================================

class _Attn:
    def __init__(self, sd, p, heads, kvheads, dtype):
        self.wq, self.wk, self.wv, self.gate, self.wo = (
            _Linear(sd, p + n, dtype) for n in ("wq", "wk", "wv", "gate", "wo"))
        self.qn = sd.pop(p + "qknorm.qnorm.scale").float() + 1.0
        self.kn = sd.pop(p + "qknorm.knorm.scale").float() + 1.0
        self.heads, self.kvheads = heads, kvheads

    def __call__(self, x, rope=None):
        import torch
        import torch.nn.functional as F
        B, L, _ = x.shape
        e = Eingabe(x)
        q = self.wq(e).view(B, L, self.heads, -1).transpose(1, 2)
        k = self.wk(e).view(B, L, self.kvheads, -1).transpose(1, 2)
        v = self.wv(e).view(B, L, self.kvheads, -1).transpose(1, 2)
        q, k = _rms(q, self.qn, 1e-5), _rms(k, self.kn, 1e-5)
        if rope is not None:
            q, k = _rope_apply(q, rope), _rope_apply(k, rope)
        a = _sdpa(q, k, v, enable_gqa=self.kvheads != self.heads)
        a = a.transpose(1, 2).reshape(B, L, -1)
        return self.wo(a * torch.sigmoid(self.gate(e)))


class _SwiGLU:
    def __init__(self, sd, p, dtype):
        self.gate, self.up, self.down = (_Linear(sd, p + n, dtype) for n in ("gate", "up", "down"))

    def __call__(self, x):
        import torch.nn.functional as F
        e = Eingabe(x)
        return self.down(F.silu(self.gate(e)) * self.up(e))


class _FusionBlock:
    def __init__(self, sd, p, dtype):
        self.pre = sd.pop(p + "prenorm.scale").float() + 1.0
        self.post = sd.pop(p + "postnorm.scale").float() + 1.0
        self.attn = _Attn(sd, p + "attn.", 20, 20, dtype)
        self.mlp = _SwiGLU(sd, p + "mlp.", dtype)

    def __call__(self, x):
        x = x + self.attn(_rms(x, self.pre, 1e-5))
        return x + self.mlp(_rms(x, self.post, 1e-5))


class _Block:
    def __init__(self, sd, p, dtype):
        self.mod = sd.pop(p + "mod.lin").to(dtype)
        self.pre = sd.pop(p + "prenorm.scale").float() + 1.0
        self.post = sd.pop(p + "postnorm.scale").float() + 1.0
        self.attn = _Attn(sd, p + "attn.", 48, 12, dtype)
        self.mlp = _SwiGLU(sd, p + "mlp.", dtype)

    def __call__(self, x, vec, rope):
        pscale, pshift, pgate, qscale, qshift, qgate = (vec + self.mod).chunk(6, dim=-1)
        x = x + pgate * self.attn((1 + pscale) * _rms(x, self.pre, 1e-5) + pshift, rope)
        return x + qgate * self.mlp((1 + qscale) * _rms(x, self.post, 1e-5) + qshift)


def _rope_tab(pos, axes=(32, 48, 48), theta=1000.0):
    """(cos, sin) je Token und Frequenz für die drei Achsen (t, h, w)."""
    import torch
    teile_ = []
    for i, d in enumerate(axes):
        omega = 1.0 / (theta ** (torch.arange(0, d, 2, dtype=torch.float64, device=pos.device) / d))
        teile_.append(pos[:, i:i + 1].double() * omega[None])
    ang = torch.cat(teile_, -1).float()
    return ang.cos()[None, None], ang.sin()[None, None]


def _rope_apply(x, rope):
    """Paarweise Rotation (x0, x1) benachbarter Kanäle."""
    import torch
    cos, sin = rope
    xf = x.float().unflatten(-1, (-1, 2))
    x0, x1 = xf[..., 0], xf[..., 1]
    return torch.stack((cos * x0 - sin * x1, sin * x0 + cos * x1), -1).flatten(-2).to(x.dtype)


class _DiT:
    PATCH, CH = 2, 16

    def __init__(self, path: Path, device, dtype, on_status=None):
        sd = _lade(path, device, prefix="model.diffusion_model.")
        self.dtype = dtype
        n = 1 + max(int(k.split(".")[1]) for k in sd if k.startswith("blocks."))
        self.first = _Linear(sd, "first", dtype)
        self.tmlp = (_Linear(sd, "tmlp.0", dtype), _Linear(sd, "tmlp.2", dtype))
        self.tproj = _Linear(sd, "tproj.1", dtype)
        self.txt_layer = [_FusionBlock(sd, f"txtfusion.layerwise_blocks.{i}.", dtype) for i in range(2)]
        self.txt_proj = sd.pop("txtfusion.projector.weight").to(dtype)
        self.txt_ref = [_FusionBlock(sd, f"txtfusion.refiner_blocks.{i}.", dtype) for i in range(2)]
        self.txt_norm = sd.pop("txtmlp.0.scale").float() + 1.0
        self.txtmlp = (_Linear(sd, "txtmlp.1", dtype), _Linear(sd, "txtmlp.3", dtype))
        self.blocks = []
        for i in range(n):
            self.blocks.append(_Block(sd, f"blocks.{i}.", dtype))
            if on_status and i % 7 == 6:
                on_status(f"Krea 2: Modell {i + 1}/{n} Blöcke bereit …")
        self.last_norm = sd.pop("last.norm.scale").float() + 1.0
        self.last_mod = sd.pop("last.modulation.lin").to(dtype)
        self.last = _Linear(sd, "last.linear", dtype)

    @staticmethod
    def _t_emb(t, dim=256):
        import torch
        t = t * 1000.0
        half = dim // 2
        f = torch.exp(-math.log(10000) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
        a = t[:, None].float() * f[None]
        return torch.cat((a.cos(), a.sin()), -1)

    def text(self, ctx):
        """(B, seq, 12, 2560) -> (B, seq, 6144); einmal je Bild."""
        import torch.nn.functional as F
        b, l, n, d = ctx.shape
        x = ctx.to(self.dtype).reshape(b * l, n, d)
        for blk in self.txt_layer:
            x = blk(x)
        x = x.reshape(b, l, n, d).transpose(2, 3)                # b l d n
        x = F.linear(x, self.txt_proj).squeeze(-1)
        for blk in self.txt_ref:
            x = blk(x)
        x = _rms(x, self.txt_norm, 1e-5)
        return self.txtmlp[1](F.gelu(self.txtmlp[0](x), approximate="tanh"))

    def __call__(self, x, sigma, txt):
        import torch
        import torch.nn.functional as F
        B, C, H, W = x.shape
        p = self.PATCH
        h, w = H // p, W // p
        img = x.to(self.dtype).reshape(B, C, h, p, w, p).permute(0, 2, 4, 1, 3, 5).reshape(B, h * w, C * p * p)
        img = self.first(img)

        t = self._t_emb(sigma).unsqueeze(1).to(self.dtype)
        t = self.tmlp[1](F.gelu(self.tmlp[0](t), approximate="tanh"))
        vec = self.tproj(F.gelu(t, approximate="tanh"))

        L = txt.shape[1]
        pos = torch.zeros(L + h * w, 3, device=x.device)
        pos[L:, 1] = torch.arange(h, device=x.device).repeat_interleave(w).float()
        pos[L:, 2] = torch.arange(w, device=x.device).repeat(h).float()
        rope = _rope_tab(pos)

        z = torch.cat((txt, img), 1)
        for blk in self.blocks:
            z = blk(z, vec, rope)
        scale, shift = (t + self.last_mod.unsqueeze(0)).chunk(2, dim=1)
        z = self.last((1 + scale) * _rms(z, self.last_norm, 1e-5) + shift)
        z = z[:, L:].reshape(B, h, w, C, p, p).permute(0, 3, 1, 4, 2, 5).reshape(B, C, H, W)
        return z.float()


# ===========================================================================
#  VAE (Qwen-Image / Wan 2.1, als reiner 2D-Encoder/-Decoder für Einzelbilder)
# ===========================================================================
#
# Die kausalen 3D-Faltungen sehen bei einem einzelnen Bild vor sich nur Nullen.
# Übrig bleibt die letzte Zeitscheibe des Kerns als gewöhnliche 2D-Faltung.
# Die zeitliche Verkleinerung (time_conv) greift erst ab dem zweiten Bild.

class _VAEBasis:
    def __init__(self, path: Path, device, dtype, skip: tuple):
        sd = _lade(path, device, skip=skip)
        self.sd = {k: v.to(dtype) for k, v in sd.items()}
        self.dtype = dtype

    def _conv(self, x, p, pad, stride=1):
        import torch.nn.functional as F
        w = self.sd[p + ".weight"]
        if w.ndim == 5:
            w = w[:, :, -1]
        return F.conv2d(x, w, self.sd.get(p + ".bias"), stride=stride, padding=pad)

    def _norm(self, x, p):
        import torch.nn.functional as F
        g = self.sd[p + ".gamma"].reshape(1, -1, 1, 1)
        return F.normalize(x.float(), dim=1).to(x.dtype) * (x.shape[1] ** 0.5) * g

    def _res(self, x, p):
        import torch.nn.functional as F
        h = self._conv(F.silu(self._norm(x, p + ".residual.0")), p + ".residual.2", 1)
        h = self._conv(F.silu(self._norm(h, p + ".residual.3")), p + ".residual.6", 1)
        sc = self._conv(x, p + ".shortcut", 0) if (p + ".shortcut.weight") in self.sd else x
        return h + sc

    def _attn(self, x, p):
        import torch.nn.functional as F
        b, c, hh, ww = x.shape
        q, k, v = self._conv(self._norm(x, p + ".norm"), p + ".to_qkv", 0).chunk(3, dim=1)
        q, k, v = (t.reshape(b, 1, c, hh * ww).transpose(-1, -2) for t in (q, k, v))
        a = _sdpa(q, k, v)
        a = a.transpose(-1, -2).reshape(b, c, hh, ww)
        return x + self._conv(a, p + ".proj", 0)


class _VAEEncoder(_VAEBasis):
    """Bild [-1, 1] (B, 3, H, W) -> Mittelwert des Latents (B, 16, H/8, W/8), unnormiert."""

    def __init__(self, path: Path, device, dtype):
        super().__init__(path, device, dtype, skip=("decoder.", "conv2."))

    def __call__(self, x):
        import torch.nn.functional as F
        x = self._conv(x.to(self.dtype), "encoder.conv1", 1)
        i = 0
        while f"encoder.downsamples.{i}.residual.0.gamma" in self.sd or \
                f"encoder.downsamples.{i}.resample.1.weight" in self.sd:
            p = f"encoder.downsamples.{i}"
            if (p + ".resample.1.weight") in self.sd:
                x = self._conv(F.pad(x, (0, 1, 0, 1)), p + ".resample.1", 0, stride=2)
            else:
                x = self._res(x, p)
            i += 1
        x = self._res(x, "encoder.middle.0")
        x = self._attn(x, "encoder.middle.1")
        x = self._res(x, "encoder.middle.2")
        x = self._conv(F.silu(self._norm(x, "encoder.head.0")), "encoder.head.2", 1)
        x = self._conv(x, "conv1", 0)
        return x[:, :16].float()                     # Mittelwert; die zweite Hälfte ist log var


class _VAEDecoder(_VAEBasis):
    def __init__(self, path: Path, device, dtype):
        super().__init__(path, device, dtype, skip=("encoder.", "conv1."))

    def __call__(self, z):
        import torch.nn.functional as F
        x = self._conv(z.to(self.dtype), "conv2", 0)
        x = self._conv(x, "decoder.conv1", 1)
        x = self._res(x, "decoder.middle.0")
        x = self._attn(x, "decoder.middle.1")
        x = self._res(x, "decoder.middle.2")
        i = 0
        while f"decoder.upsamples.{i}.residual.0.gamma" in self.sd or \
                f"decoder.upsamples.{i}.resample.1.weight" in self.sd:
            p = f"decoder.upsamples.{i}"
            if (p + ".resample.1.weight") in self.sd:
                x = F.interpolate(x, scale_factor=2.0, mode="nearest-exact")
                x = self._conv(x, p + ".resample.1", 1)
            else:
                x = self._res(x, p)
            i += 1
        x = self._conv(F.silu(self._norm(x, "decoder.head.0")), "decoder.head.2", 1)
        return x.float().clamp(-1, 1)


# ===========================================================================
#  Sampler
# ===========================================================================

def _sigmas(steps: int, shift: float):
    """'simple'-Plan über die Flux-Zeitverschiebung (10000 Stützstellen)."""
    import torch
    n = 10000

    def sig(t):
        return math.exp(shift) / (math.exp(shift) + (1.0 / t - 1.0))

    ss = n / steps
    s = [sig((n - int(i * ss)) / n) for i in range(steps)]
    return torch.tensor(s + [0.0], dtype=torch.float32)


def _sigmas_ab(steps: int, shift: float, start: float):
    """Wie _sigmas, aber beginnend bei Rauschstärke `start` (Nachmalen eines Bildes).
    Der Zeitabschnitt bis `start` wird gleichmäßig in `steps` Schritte geteilt."""
    import torch
    k = math.exp(shift)
    start = min(max(float(start), 0.01), 1.0)
    t0 = 1.0 / (1.0 + k * (1.0 / start - 1.0))          # Umkehrung der Zeitverschiebung
    s = [k / (k + (1.0 / t - 1.0)) for t in (t0 * (1 - i / steps) for i in range(steps))]
    return torch.tensor(s + [0.0], dtype=torch.float32)


def _sample(model, x, txt, sigmas, sampler, seed, on_status=None):
    import torch
    gen = torch.Generator(device=x.device).manual_seed(seed)
    n = len(sigmas) - 1
    for i in range(n):
        s, s_next = float(sigmas[i]), float(sigmas[i + 1])
        t0 = time.time()
        with torch.no_grad():
            v = model(x, torch.full((x.shape[0],), s, device=x.device), txt)
        den = x - s * v
        if s_next == 0.0:
            x = den
        elif sampler == "euler":
            x = x + (s_next - s) * v
        else:                                   # euler_ancestral (Flow-Variante, eta = 1)
            s_down = s_next * (s_next / s)
            a_next, a_down = 1.0 - s_next, 1.0 - s_down
            renoise = math.sqrt(max(s_next ** 2 - s_down ** 2 * a_next ** 2 / a_down ** 2, 0.0))
            r = s_down / s
            x = r * x + (1.0 - r) * den
            noise = torch.randn(x.shape, generator=gen, device=x.device, dtype=x.dtype)
            x = (a_next / a_down) * x + noise * renoise
        if on_status:
            on_status(f"Krea 2: Schritt {i + 1}/{n} ({time.time() - t0:.1f}s)")
    return x


# ===========================================================================
#  Ein Bild malen
# ===========================================================================

def _frei():
    """Python-Referenzen einsammeln, dann Cache und cuBLAS-Arbeitsspeicher zurückgeben."""
    import gc
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        leeren = getattr(torch._C, "_cuda_clearCublasWorkspaces", None)
        if leeren:
            leeren()
        torch.cuda.empty_cache()


# Wie ComfyUI mit --disable-smart-memory: Jedes Teil kommt nur für seinen
# Rechenschritt in den VRAM und wandert danach in den Arbeitsspeicher. Das
# nächste Bild holt es von dort zurück statt von der Platte. Nach dem Bild
# belegt Krea im VRAM nur noch den CUDA-Grundbedarf.
_LOCK = threading.RLock()
_RAM: dict = {}                      # art -> (pfad, objekt), Gewichte fest im Arbeitsspeicher


def _kopie(obj, device, pin: bool = False):
    """Kopie von obj, deren Tensoren auf device liegen; das Original bleibt unberührt."""
    import torch
    if isinstance(obj, torch.Tensor):
        if device == "cpu":
            t = obj.to("cpu")
            return t.pin_memory() if pin and torch.cuda.is_available() else t
        return obj.to(device, non_blocking=True)
    if isinstance(obj, list):
        return [_kopie(x, device, pin) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_kopie(x, device, pin) for x in obj)
    if isinstance(obj, dict):
        return {k: _kopie(v, device, pin) for k, v in obj.items()}
    if type(obj).__module__ == __name__ and hasattr(obj, "__dict__"):
        neu = object.__new__(type(obj))
        neu.__dict__.update({k: _kopie(v, device, pin) for k, v in vars(obj).items()})
        return neu
    return obj


def _auf_gpu(art: str, pfad: Path, bauen, device):
    """Arbeitskopie für die Karte: aus dem RAM, beim ersten Mal von der Platte.

    Die Gewichte ändern sich nie. Deshalb bleibt die RAM-Fassung stehen, und
    nach dem Rechenschritt wird die Kopie auf der Karte nur verworfen.
    """
    eintrag = _RAM.get(art)
    if eintrag is not None and eintrag[0] == pfad:
        return _kopie(eintrag[1], device)
    _RAM.pop(art, None)
    obj = bauen()
    _RAM[art] = (pfad, _kopie(obj, "cpu", pin=True))
    return obj


def im_ram() -> list[str]:
    return sorted(_RAM)


def entladen() -> None:
    """Gibt auch die RAM-Kopien frei; das nächste Bild lädt wieder von der Platte."""
    global _TIMER
    with _LOCK:
        if _TIMER is not None:
            _TIMER.cancel()
            _TIMER = None
        _RAM.clear()
        _frei()
        try:                                # gepinnten RAM an Windows zurückgeben
            import torch
            leeren = getattr(torch._C, "_host_emptyCache", None)
            if leeren and torch.cuda.is_available():
                leeren()
        except Exception:
            pass


# Kommt eine Weile kein Bild mehr, wird auch der Arbeitsspeicher geräumt.
_TIMER: threading.Timer | None = None


def _entladen_nach() -> float:
    try:
        import config
        return float(config.load().get("bild_krea_entladen", DEFAULTS["entladen_nach"]))
    except Exception:
        return float(DEFAULTS["entladen_nach"])


def _uhr_neu() -> None:
    """Nach einem Bild: Entlade-Uhr (neu) starten."""
    global _TIMER
    if _TIMER is not None:
        _TIMER.cancel()
    sek = _entladen_nach()
    if sek <= 0:
        _TIMER = None
        entladen()
        return
    _TIMER = threading.Timer(sek, entladen)
    _TIMER.daemon = True
    _TIMER.start()


def _male(prompt, ref, pfad, t, steps, sampler, shift, W, H, seed, device, dtype, on_status,
          bild=None, staerke: float = 1.0):
    """Die eigentliche Rechnung. Gibt das Bild als uint8-Array zurück.
    Mit `bild` (uint8, H×W×3) wird es ab Rauschstärke `staerke` nachgemalt statt neu."""
    import numpy as np
    import torch

    if on_status:
        on_status("Krea 2: Text-Encoder …")
    te = _auf_gpu("text", t["text"], lambda: _Qwen3Text(t["text"], device, dtype), device)
    ctx = _encode(prompt, te, device)
    del te
    _frei()

    mean = torch.tensor(_LAT_MEAN, device=device).view(1, 16, 1, 1)
    std = torch.tensor(_LAT_STD, device=device).view(1, 16, 1, 1)
    start = None
    if bild is not None:
        if on_status:
            on_status("Krea 2: VAE liest das Bild …")
        enc = _auf_gpu("vae_enc", t["vae"], lambda: _VAEEncoder(t["vae"], device, dtype), device)
        x = torch.from_numpy(np.ascontiguousarray(bild)).to(device).permute(2, 0, 1)[None]
        with torch.no_grad():
            start = (enc(x.float() / 127.5 - 1.0) - mean) / std
        del enc, x
        _frei()

    if on_status:
        on_status("Krea 2: Modell in den VRAM …")
    dit = _auf_gpu("dit", pfad, lambda: _DiT(pfad, device, dtype, on_status), device)
    with torch.no_grad():
        txt = dit.text(ctx)
    del ctx
    noise = torch.randn((1, 16, H // 8, W // 8),
                        generator=torch.Generator().manual_seed(seed)).to(device)
    if start is None:
        sig = _sigmas(steps, shift).to(device)
        x0 = noise * sig[0]
    else:
        sig = _sigmas_ab(steps, shift, staerke).to(device)
        x0 = (1.0 - sig[0]) * start + sig[0] * noise       # Flow-Matching: Bild + Rauschen
    lat = _sample(dit, x0, txt, sig, sampler, seed, on_status)
    del dit, txt, noise, sig, x0, start
    _frei()

    if on_status:
        on_status("Krea 2: VAE …")
    vae = _auf_gpu("vae", t["vae"], lambda: _VAEDecoder(t["vae"], device, dtype), device)
    with torch.no_grad():
        img = vae(lat * std + mean)
    del vae, lat
    _frei()
    return ((img[0].permute(1, 2, 0).float().cpu().numpy() + 1.0) * 127.5
            ).round().clip(0, 255).astype(np.uint8)


def _modell(model: str | None, modelle: dict) -> str | None:
    """Ein erfundener oder ungenauer Name soll das Bild nicht verhindern."""
    if model and model not in modelle:
        name = Path(str(model).replace("\\", "/")).stem.lower()
        treffer = [k for k in modelle if name and name in k.lower()]
        model = treffer[0] if treffer else None
    return model or chosen_model()


def _geraet():
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return device, (torch.bfloat16 if device.type == "cuda" else torch.float32)


def generate(prompt: str, *, model: str | None = None, steps: int | None = None,
             size: tuple[int, int] | None = None, seed: int | None = None,
             sampler: str | None = None, shift: float | None = None,
             on_status=None) -> str:
    """Malt EIN Bild mit Krea 2 und gibt den Pfad zurück. Blockiert."""
    grund = missing_reason()
    if grund:
        raise RuntimeError(grund)

    from PIL import Image, PngImagePlugin

    modelle = discover()
    ref = _modell(model, modelle)
    t = teile()

    steps = int(steps or schritte())
    sampler = (sampler or DEFAULTS["sampler"]).lower()
    shift = DEFAULTS["shift"] if shift is None else float(shift)
    W, H = size or DEFAULTS["size"]
    W = (min(max(int(W), 256), MAX_EDGE) // 16) * 16
    H = (min(max(int(H), 256), MAX_EDGE) // 16) * 16
    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    device, dtype = _geraet()

    fehler = None
    with _LOCK:
        if _TIMER is not None:
            _TIMER.cancel()                 # neues Bild: nicht mittendrin entladen
        try:
            arr = _male(prompt, ref, modelle[ref], t, steps, sampler, shift, W, H, seed,
                        device, dtype, on_status)
        except Exception as e:
            # Nur den Text behalten: der Traceback hielte sonst alle Tensoren fest.
            fehler = f"{type(e).__name__}: {e}"
        _frei()                             # bei Fehlern hingen die Arbeitskopien am Traceback
        _uhr_neu()
        if fehler:
            raise RuntimeError(f"Krea 2: {fehler}")

    import profilordner as _profil                   # Profile/<Persönlichkeit>/Bilder/<Name>_Datum_Uhrzeit.png
    out = _profil.bild_ziel(OUT_DIR, ".png")
    meta = PngImagePlugin.PngInfo()
    meta.add_text("prompt", prompt)
    meta.add_text("model", ref)
    meta.add_text("params", f"steps={steps}, cfg=1, size={W}x{H}, seed={seed}, "
                            f"sampler={sampler}, shift={shift}, backend=krea2")
    Image.fromarray(arr).save(out, pnginfo=meta)
    return str(out)


# ===========================================================================
#  Gesichter nachmalen
# ===========================================================================

def _cfg(key: str, standard):
    try:
        import config
        wert = config.load().get(key)
        return standard if wert is None else type(standard)(wert)
    except Exception:
        return standard


def gesicht_an() -> bool:
    """Gesichts-Nachbesserung eingeschaltet (Config `bild_krea_gesicht`)?"""
    return bool(_cfg("bild_krea_gesicht", DEFAULTS["gesicht"]))


def _arbeitsgroesse(cw: int, ch: int, kante: int) -> tuple[int, int, float]:
    """(W, H, Lupe) für den Ausschnitt: längere Kante = `kante`, Vielfache von 16."""
    lupe = kante / max(cw, ch)
    W = max(256, (round(cw * lupe) // 16) * 16)
    H = max(256, (round(ch * lupe) // 16) * 16)
    return W, H, lupe


def gesicht_nachbessern(path, *, staerke: float | None = None, steps: int | None = None,
                        on_status=None) -> str | None:
    """Gesichter eines Krea-Bildes vergrößert nachmalen und weich einsetzen.

    Das Original bleibt; das Ergebnis liegt daneben als `<name>_gesicht.png`.
    None, wenn kein Gesicht gefunden wurde, alle schon groß genug sind oder
    OpenCV fehlt. Blockiert."""
    import gesichter
    if not gesichter.verfuegbar() or missing_reason():
        return None

    import re
    import numpy as np
    from PIL import Image, PngImagePlugin

    with Image.open(path) as im:
        meta = dict(getattr(im, "text", {}) or {})
        arr = np.asarray(im.convert("RGB"))
    boxen = gesichter.finden(arr)
    if not boxen:
        return None

    staerke = _cfg("bild_krea_gesicht_staerke", DEFAULTS["gesicht_staerke"]) if staerke is None else staerke
    steps = int(steps or _cfg("bild_krea_gesicht_schritte", DEFAULTS["gesicht_schritte"]))
    kante = int(_cfg("bild_krea_gesicht_kante", DEFAULTS["gesicht_kante"]))
    params = meta.get("params", "")
    m = re.search(r"sampler=(\w+)", params)
    sampler = m.group(1) if m and m.group(1) in ("euler", "euler_ancestral") else DEFAULTS["sampler"]
    m = re.search(r"seed=(\d+)", params)
    seed = (int(m.group(1)) if m else int(time.time() * 1000)) + 1
    prompt = f"{GESICHT_SATZ} {meta.get('prompt', '')}".strip()

    modelle = discover()
    ref = _modell(meta.get("model"), modelle)
    t = teile()
    device, dtype = _geraet()

    fertig, fehler = 0, None
    with _LOCK:
        if _TIMER is not None:
            _TIMER.cancel()
        try:
            for i, box in enumerate(boxen):
                x0, y0, x1, y1 = box
                W, H, lupe = _arbeitsgroesse(x1 - x0, y1 - y0, kante)
                if lupe < 1.3:
                    continue                  # Gesicht hat schon genug Pixel
                ausschnitt = np.asarray(Image.fromarray(arr[y0:y1, x0:x1]).resize((W, H), Image.LANCZOS))
                melde = None
                if on_status:
                    melde = (lambda s, i=i: on_status(f"Gesicht {i + 1}/{len(boxen)} · {s}"))
                neu = _male(prompt, ref, modelle[ref], t, steps, sampler, DEFAULTS["shift"],
                            W, H, seed + i, device, dtype, melde, bild=ausschnitt, staerke=staerke)
                arr = gesichter.einsetzen(arr, box, neu)
                fertig += 1
        except Exception as e:
            fehler = f"{type(e).__name__}: {e}"
        _frei()
        _uhr_neu()
        if fehler:
            raise RuntimeError(f"Krea 2 (Gesicht): {fehler}")
    if not fertig:
        return None

    p = Path(path)
    ziel = p.with_name(f"{p.stem}_gesicht.png")
    info = PngImagePlugin.PngInfo()
    for k, v in meta.items():
        info.add_text(k, str(v))
    info.add_text("nachgebessert", f"gesicht x{fertig}, staerke={staerke}, steps={steps}")
    Image.fromarray(arr).save(ziel, pnginfo=info)
    return str(ziel)
