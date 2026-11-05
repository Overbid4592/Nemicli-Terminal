"""
gewichte.py - Modelldateien der Bild-Motoren lesen: safetensors (einzeln oder in Teilen) und GGUF.

Formate der Matrizen (Klasse Lin):

    dicht   bf16 · fp16 · fp32
    fp8     float8_e4m3fn / e5m2, optional mit Skala (ComfyUI „scaled fp8“, „fp8_mixed“)
    nvfp4   4 Bit mit FP8-Blockskalen (ComfyUI); auf Blackwell direkt über scaled_mm
    int8    int8 mit Skala je Zeile (ComfyUI „int8_tensorwise“ ohne Drehung)
    gguf    Q2_K … Q8_0, bleibt gepackt und rechnet über die Kerne des GGUF-Motors

Alles wird zuerst in den Arbeitsspeicher gelesen; `zu(geraet)` liefert eine Kopie für die Karte.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path


class FormatFehler(RuntimeError):
    """Die Datei hat ein Format, das der Motor nicht rechnen kann."""


# ===========================================================================
#  Köpfe lesen, ohne Gewichte zu laden
# ===========================================================================

def st_kopf(pfad: Path) -> dict:
    """JSON-Kopf einer .safetensors-Datei (leer, wenn sie keine ist)."""
    try:
        with open(pfad, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > (200 << 20):
                return {}
            return json.loads(f.read(n).decode("utf-8", "replace"))
    except Exception:
        return {}


def teile_von(pfad: Path) -> list[Path]:
    """Alle Teile einer geteilten safetensors-Datei (…-00001-of-00003.safetensors), sonst nur sie selbst."""
    m = re.match(r"(.+)-(\d{5})-of-(\d{5})\.safetensors$", pfad.name)
    if not m:
        return [pfad]
    alle = [pfad.with_name(f"{m.group(1)}-{i:05d}-of-{m.group(3)}.safetensors")
            for i in range(1, int(m.group(3)) + 1)]
    return alle if all(p.is_file() for p in alle) else [pfad]


def ist_folgeteil(pfad: Path) -> bool:
    """Zweiter und weitere Teile einer geteilten Datei: zählen nicht als eigenes Modell."""
    m = re.match(r".+-(\d{5})-of-\d{5}\.safetensors$", pfad.name)
    return bool(m) and int(m.group(1)) > 1


def namen(pfad: Path) -> dict[str, tuple]:
    """Tensorname -> Form, für safetensors (alle Teile) und GGUF. Liest nur die Köpfe."""
    pfad = Path(pfad)
    if pfad.suffix.lower() == ".gguf":
        from ggufengine.gguf import GGUFFile
        try:
            with GGUFFile(str(pfad)) as gg:
                return {n: tuple(int(d) for d in reversed(ti.dims)) for n, ti in gg.tensors.items()}
        except Exception:
            return {}
    aus: dict[str, tuple] = {}
    for teil in teile_von(pfad):
        for k, v in st_kopf(teil).items():
            if k != "__metadata__" and isinstance(v, dict):
                aus[k] = tuple(v.get("shape", ()))
    return aus


def ohne_praefix(alle: dict, praefixe=("model.diffusion_model.", "diffusion_model.")) -> tuple[dict, str]:
    """Namen ohne das Vorsatzstück ganzer ComfyUI-Checkpoints; gibt (Namen, Vorsatz) zurück."""
    for p in praefixe:
        if any(k.startswith(p) for k in alle):
            return {k[len(p):]: v for k, v in alle.items() if k.startswith(p)}, p
    return alle, ""


# ===========================================================================
#  Matrizen
# ===========================================================================

def _nvfp4_gewicht(q, bs_blocked, ts, dtype):
    """NVFP4 -> dichte Matrix (für Karten ohne FP4-Kerne und für die CPU)."""
    import torch.nn.functional as F
    import krea as _k
    out_f, in_f = q.shape[0], q.shape[1] * 2
    skala = _k._from_blocked(bs_blocked.reshape(-1) if bs_blocked.dim() == 1 else bs_blocked,
                             out_f, in_f // 16).float() * ts.float()
    w = F.embedding(q.int(), _k._lut256(q.device, dtype))
    return (w.view(out_f, in_f // 16, 16) * skala.to(dtype).unsqueeze(-1)).view(out_f, in_f)


class Lin:
    """y = x @ W^T (+ b) für eine Matrix in ihrem gespeicherten Format."""

    __slots__ = ("art", "w", "skala", "skala2", "bias", "n_out", "n_in", "ggml", "_pl")

    def __init__(self, art: str, w, *, skala=None, skala2=None, bias=None, n_out=0, n_in=0, ggml=0):
        self.art, self.w, self.skala, self.skala2, self.bias = art, w, skala, skala2, bias
        self.n_out, self.n_in, self.ggml = int(n_out), int(n_in), int(ggml)
        self._pl = None

    # --- Speicher -----------------------------------------------------------

    def tensoren(self) -> list:
        return [t for t in (self.w, self.skala, self.skala2, self.bias) if t is not None]

    def bytes(self) -> int:
        return sum(t.numel() * t.element_size() for t in self.tensoren())

    def zu(self, geraet, pin: bool = False) -> "Lin":
        def bewegen(t):
            if t is None:
                return None
            if str(geraet) == "cpu":
                t = t.to("cpu")
                return t.pin_memory() if pin else t
            return t.to(geraet, non_blocking=True)
        return Lin(self.art, bewegen(self.w), skala=bewegen(self.skala), skala2=bewegen(self.skala2),
                   bias=bewegen(self.bias), n_out=self.n_out, n_in=self.n_in, ggml=self.ggml)

    # --- Rechnen ------------------------------------------------------------

    def gewicht(self, dtype):
        """Die Matrix dicht in `dtype` (n_out, n_in)."""
        if self.art == "dicht":
            return self.w.to(dtype)
        if self.art in ("fp8", "int8"):
            w = self.w.to(dtype)
            return w * self.skala.to(dtype) if self.skala is not None else w
        if self.art == "nvfp4":
            return _nvfp4_gewicht(self.w, self.skala, self.skala2, dtype)
        from ggufengine.quant import dequantize
        return dequantize(self.w.reshape(-1), self.ggml, self.n_out * self.n_in, dtype).view(self.n_out, self.n_in)

    def __call__(self, x):
        import torch.nn.functional as F
        if self.art == "gguf" and x.is_cuda:
            from ggufengine.models.common import PackedLinear
            if self._pl is None or self._pl.raw.device != self.w.device or self._pl.dtype != x.dtype:
                self._pl = PackedLinear(self.w, self.ggml, self.n_out, self.n_in, self.bias, x.dtype)
            return self._pl(x)
        if self.art == "nvfp4" and x.is_cuda and _fp4_mm_moeglich(x.device):
            return self._nvfp4_mm(x)
        b = None if self.bias is None else self.bias.to(x.dtype)
        return F.linear(x, self.gewicht(x.dtype), b)

    def _nvfp4_mm(self, x):
        import torch
        import torch.nn.functional as F
        import krea as _k
        x2 = x.reshape(-1, x.shape[-1])
        q, bs, gs = _k._fp4_quant(x2)
        S, W = F.ScalingType, F.SwizzleType
        y = F.scaled_mm(
            q.view(torch.float4_e2m1fn_x2), self.w.view(torch.float4_e2m1fn_x2).t(),
            [bs.view(-1), gs], [S.BlockWise1x16, S.TensorWise],
            [self.skala.reshape(-1), self.skala2.float()], [S.BlockWise1x16, S.TensorWise],
            swizzle_a=[W.SWIZZLE_32_4_4, W.NO_SWIZZLE], swizzle_b=[W.SWIZZLE_32_4_4, W.NO_SWIZZLE],
            bias=None if self.bias is None else self.bias.to(x.dtype), output_dtype=x.dtype)
        return y.view(*x.shape[:-1], self.n_out)


def _fp4_mm_moeglich(geraet) -> bool:
    try:
        import krea as _k
        return _k._fp4_bereit(geraet)
    except Exception:
        return False


# ===========================================================================
#  Dateien öffnen
# ===========================================================================

class Quelle:
    """Eine geöffnete Modelldatei. `lin(name)` liefert eine Matrix, `tensor(name)` einen dichten Tensor.

    Namen ohne Endung `.weight`/`.bias` bei `lin`; `vorsatz` wird vor jeden Namen gesetzt."""

    def __init__(self, pfad: Path, vorsatz: str = ""):
        self.pfad = Path(pfad)
        self.vorsatz = vorsatz
        self.gguf = self.pfad.suffix.lower() == ".gguf"
        self._gg = None
        self._st: list = []
        self._wo: dict[str, int] = {}
        self._quant: dict = {}
        if self.gguf:
            from ggufengine.gguf import GGUFFile
            self._gg = GGUFFile(str(self.pfad))
        else:
            from safetensors import safe_open
            for i, teil in enumerate(teile_von(self.pfad)):
                kopf = st_kopf(teil)
                meta = kopf.get("__metadata__") or {}
                try:
                    self._quant.update(json.loads(meta.get("_quantization_metadata") or "{}").get("layers", {}))
                except ValueError:
                    pass
                f = safe_open(str(teil), framework="pt", device="cpu")
                self._st.append(f)
                for k in f.keys():
                    self._wo[k] = i

    def schliessen(self) -> None:
        if self._gg is not None:
            self._gg.close()
        self._st, self._wo, self._gg = [], {}, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.schliessen()

    # --- Namen --------------------------------------------------------------

    def alle(self) -> list[str]:
        roh = list(self._gg.tensors) if self.gguf else list(self._wo)
        n = len(self.vorsatz)
        return [k[n:] for k in roh if k.startswith(self.vorsatz)]

    def hat(self, name: str) -> bool:
        k = self.vorsatz + name
        return k in (self._gg.tensors if self.gguf else self._wo)

    # --- Lesen --------------------------------------------------------------

    def _roh(self, name: str):
        k = self.vorsatz + name
        if self.gguf:
            from ggufengine.quant import raw_to_tensor
            ti = self._gg.tensors[k]
            return raw_to_tensor(self._gg.tensor_bytes(ti), "cpu").clone(), ti
        return self._st[self._wo[k]].get_tensor(k), None

    def tensor(self, name: str, dtype=None):
        """Dichter Tensor (Normen, Bias, Einbettungen, Faltungen). GGUF wird entpackt."""
        import torch
        t, ti = self._roh(name)
        if ti is not None:
            from ggufengine.quant import dequantize
            t = dequantize(t, ti.ggml_type, ti.n_elements, dtype or torch.float32).view(
                tuple(int(d) for d in reversed(ti.dims)))
        elif t.dtype in (torch.float8_e4m3fn, torch.float8_e5m2):
            s = self.vorsatz + name.rsplit(".", 1)[0]
            skala = None
            for endung in (".weight_scale", ".scale_weight"):
                if s + endung in self._wo:
                    skala = self._st[self._wo[s + endung]].get_tensor(s + endung).float()
            t = t.float() * skala if skala is not None else t.float()
        return t.to(dtype) if dtype is not None and t.is_floating_point() else t

    def lin(self, name: str, dtype=None) -> Lin:
        """Matrix `name.weight` (+ `name.bias`) in ihrem Format. Dichte Matrizen werden in `dtype` gespeichert."""
        import torch
        w, ti = self._roh(name + ".weight")
        bias = self.tensor(name + ".bias", torch.float32) if self.hat(name + ".bias") else None
        if ti is not None:
            from ggufengine.gguf import GGML_TYPES
            typ = GGML_TYPES[ti.ggml_type][0]
            n_in, n_out = int(ti.dims[0]), int(ti.dims[1])
            if typ in ("F32", "F16", "BF16"):
                from ggufengine.quant import dequantize
                w = dequantize(w, ti.ggml_type, ti.n_elements, dtype or torch.float32).view(n_out, n_in)
                return Lin("dicht", w, bias=bias, n_out=n_out, n_in=n_in)
            return Lin("gguf", w, bias=bias, n_out=n_out, n_in=n_in, ggml=ti.ggml_type)
        fmt = self._format(name)
        s = lambda endung: self.tensor(name + endung) if self.hat(name + endung) else None   # noqa: E731
        if fmt == "nvfp4":
            ts = s(".weight_scale_2")
            bs, _ = self._roh(name + ".weight_scale")
            return Lin("nvfp4", w, skala=bs, skala2=ts.float(), bias=bias, n_out=w.shape[0], n_in=w.shape[1] * 2)
        if fmt.startswith("int8") or w.dtype == torch.int8:
            if self._convrot(name):
                raise FormatFehler("int8 mit Drehung (convrot) wird nicht unterstützt – "
                                   "bitte die bf16-, FP8-, NVFP4- oder GGUF-Fassung nehmen")
            skala = s(".weight_scale")
            return Lin("int8", w, skala=None if skala is None else skala.float().reshape(-1, 1),
                       bias=bias, n_out=w.shape[0], n_in=w.shape[1])
        if w.dtype in (torch.float8_e4m3fn, torch.float8_e5m2):
            skala = s(".weight_scale")
            if skala is None:
                skala = s(".scale_weight")
            if skala is not None and skala.numel() > 1:
                skala = skala.float().reshape(-1, 1)
            return Lin("fp8", w, skala=None if skala is None else skala.float(), bias=bias,
                       n_out=w.shape[0], n_in=w.shape[1])
        if w.dtype not in (torch.bfloat16, torch.float16, torch.float32):
            raise FormatFehler(f"Datentyp {w.dtype} in {name} wird nicht unterstützt")
        return Lin("dicht", w.to(dtype) if dtype is not None else w, bias=bias, n_out=w.shape[0], n_in=w.shape[1])

    def _format(self, name: str) -> str:
        q = self._comfy_quant(name)
        if q:
            return str(q.get("format", ""))
        eintrag = self._quant.get(name) or self._quant.get(self.vorsatz + name) or {}
        return str(eintrag.get("format", ""))

    def _comfy_quant(self, name: str) -> dict:
        if not self.hat(name + ".comfy_quant"):
            return {}
        roh, _ = self._roh(name + ".comfy_quant")
        try:
            return json.loads(bytes(roh.tolist()).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _convrot(self, name: str) -> bool:
        return bool(self._comfy_quant(name).get("convrot"))


# ===========================================================================
#  Verschieben zwischen RAM und Karte
# ===========================================================================

def zu(obj, geraet, pin: bool = False):
    """Kopie von obj (Lin, Tensor, dict, list, tuple) auf `geraet`; das Original bleibt."""
    import torch
    if isinstance(obj, Lin):
        return obj.zu(geraet, pin)
    if isinstance(obj, torch.Tensor):
        if str(geraet) == "cpu":
            t = obj.to("cpu")
            return t.pin_memory() if pin else t
        return obj.to(geraet, non_blocking=True)
    if isinstance(obj, dict):
        return {k: zu(v, geraet, pin) for k, v in obj.items()}
    if isinstance(obj, list):
        return [zu(v, geraet, pin) for v in obj]
    if isinstance(obj, tuple):
        return tuple(zu(v, geraet, pin) for v in obj)
    return obj


def groesse(obj) -> int:
    """Bytes aller Tensoren in obj."""
    import torch
    if isinstance(obj, Lin):
        return obj.bytes()
    if isinstance(obj, torch.Tensor):
        return obj.numel() * obj.element_size()
    if isinstance(obj, dict):
        return sum(groesse(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(groesse(v) for v in obj)
    return 0
