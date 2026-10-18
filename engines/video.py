"""
video.py - NemiCLIs eigener Video-Motor (Wan 2.2 TI2V), getrennt von Krea 2 und vom Chat-Motor.
Erwarteter Ordner `VideoGen/` im Programm-Ordner (oder Daten-Ordner; `Models/Video/` geht auch):
    <Modell>/*.gguf                      Wan-DiT (GGUF) – oder *.safetensors mit gleichnamiger .json
    <Modell>/*vae*.safetensors           Wan-2.2-VAE (3D, kausal: 4× Zeit, 16× Fläche, 48 Kanäle), auch in vae/
    text_encoders/*.gguf                 umT5-XXL-Encoder (GGUF), im Modell-Ordner oder geteilt hier oben
    tokenizer/tokenizer.json             umT5-Tokenizer (Unigram), ebenso
    tae/taew2_2[_super].safetensors      Mini-VAE zum schnellen Auspacken (optional, sonst die große VAE)
Ablauf je Clip (121 Bilder = 5 s bei 24 fps):
    Prompt -> umT5 -> Kontext · Startbild -> VAE-Latent (bleibt erstes Latent-Bild)
    -> DiT (Flow-Matching, Euler, CFG) -> VAE-Decoder Stück für Stück -> MP4 (H.264 über Windows, videodatei.py).
Längere Videos (bis MAX_SEKUNDEN): Clips aneinander – das letzte Bild wird das Startbild des nächsten.
Speicher wie bei Krea: jedes Teil ist nur während seines Rechenschritts im VRAM und wartet sonst im
Arbeitsspeicher.
Alle Netze sind von Hand nachgebaut. Es wird nichts aus dem Netz geladen.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

try:
    import debugbericht as _DBG
except Exception:                                    # Selbsttest ohne core/ im Pfad
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

        @staticmethod
        def fehler(*a, **k): pass

try:
    from paths import DATEN as _ROOT, INSTALL as _INSTALL
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = "VideoGen"                     # im Programm-Ordner (wie Vision/); Models/Video geht auch
MAX_SEKUNDEN = 20
HINWEISE: list[str] = []                # Hinweise des laufenden Videos (z. B. LoRA übersprungen) fürs Werkzeug
GENUTZT: list[str] = []                 # was ins laufende Video eingemischt wurde (z. B. die Charakter-LoRA)
_LORA_STAND: dict = {"zeit": 0.0, "name": None, "stand": {}}
LORA_STAND_S = 60                       # so lange gilt ein gelesener Stand (die Anleitung fragt jede Runde)
CACHE_T = 2                             # Bilder, die ein kausaler 3D-Conv vom vorigen Stück behält
DEFAULTS = {"steps": 30, "entladen_nach": 120, "loeser": "euler", "cfg_bis": 1.0, "vorstufe": 0.0,
            "nachschliff": 0.6}
# Negativ-Prompt: gegen Standbild, Matsch, Untertitel, kaputte Hände
NEGATIV = ("grelle Farben, überbelichtet, statisch, verschwommene Details, Untertitel, Stil, Kunstwerk, Gemälde, "
           "Standbild, insgesamt grau, schlechteste Qualität, niedrige Qualität, JPEG-Artefakte, hässlich, verstümmelt, "
           "zusätzliche Finger, schlecht gezeichnete Hände, schlecht gezeichnetes Gesicht, missgebildet, entstellt, "
           "verformte Gliedmaßen, verschmolzene Finger, regungsloses Bild, unordentlicher Hintergrund, drei Beine, "
           "viele Menschen im Hintergrund, rückwärts gehen")
TEXT_LEN = 512
# Wan-2.2-VAE: 48 Latent-Kanäle, 16× Fläche (2×2-Patch vor der 8×-VAE), 4× Zeit (Werte aus der Modell-Config)
LATENT_MEAN = [-0.2289, -0.0052, -0.1323, -0.2339, -0.2799, 0.0174, 0.1838, 0.1557, -0.1382, 0.0542, 0.2813, 0.0891,
               0.157, -0.0098, 0.0375, -0.1825, -0.2246, -0.1207, -0.0698, 0.5109, 0.2665, -0.2108, -0.2158, 0.2502,
               -0.2055, -0.0322, 0.1109, 0.1567, -0.0729, 0.0899, -0.2799, -0.123, -0.0313, -0.1649, 0.0117, 0.0723,
               -0.2839, -0.2083, -0.052, 0.3748, 0.0152, 0.1957, 0.1433, -0.2944, 0.3573, -0.0548, -0.1681, -0.0667]
LATENT_STD = [0.4765, 1.0364, 0.4514, 1.1677, 0.5313, 0.499, 0.4818, 0.5013, 0.8158, 1.0344, 0.5894, 1.0901,
              0.6885, 0.6165, 0.8454, 0.4978, 0.5759, 0.3523, 0.7135, 0.6804, 0.5833, 1.4146, 0.8986, 0.5659,
              0.7069, 0.5338, 0.4889, 0.4917, 0.4069, 0.4999, 0.6866, 0.4093, 0.5709, 0.6065, 0.6415, 0.4944,
              0.5726, 1.2042, 0.5458, 1.6887, 0.3971, 1.06, 0.3943, 0.5537, 0.5444, 0.4089, 0.7468, 0.7744]
# Bauarten (am Eingang des DiT erkannt): Bilder je Clip, Takt, Vorgabe-Größe, Zeitverschiebung, CFG, VAE-Teiler,
# Art der Bedingung. Vorgabe hochkant 704x1024 – Seitenverhältnis wie Krea 2 (832x1216): ein gemaltes Startbild
# wird nur verkleinert.
#   fest:  TI2V – das Startbild wird das erste Latent-Bild und bleibt stehen (nur Startbild)
#   maske: Fun InP – 48 Rauschen + 4 Maske + 48 Latent aus Start-/Endbild (Start- UND Endbild)
FAMILIEN = {
    "wan22-ti2v": {"fps": 24, "bilder": 121, "size": (704, 1024), "shift": 5.0, "cfg": 5.0, "teiler": 16, "in_dim": 48,
                   "bedingung": "fest"},
    "wan22-fun-inp": {"fps": 24, "bilder": 121, "size": (704, 1024), "shift": 5.0, "cfg": 5.0, "teiler": 16,
                      "in_dim": 100, "bedingung": "maske"},
}


# ===========================================================================
#  Dateien finden
# ===========================================================================

def _cfg(key: str, standard):
    try:
        import config
        wert = config.load().get(key)
        if wert is None:
            return standard
        return type(standard)(wert)
    except Exception:
        return standard


def video_dir() -> Path:
    """Config `video_ordner`, sonst `Models/Video` im Daten-Ordner – oder im Programm-Ordner (wie Krea)."""
    eigen = _cfg("video_ordner", "")
    if eigen:
        return Path(eigen)
    kandidaten = [Path(_INSTALL) / ORDNER, Path(_ROOT) / ORDNER,
                  Path(_ROOT) / "Models" / "Video", Path(_INSTALL) / "Models" / "Video"]
    for d in kandidaten:
        if d.is_dir() and any(_dit_in(u) is not None for u in d.iterdir() if u.is_dir() and u.name not in _GETEILT):
            return d
    return kandidaten[0]


def _erste(d: Path, muster: str) -> Path | None:
    if not d.is_dir():
        return None
    treffer = sorted(p for p in d.glob(muster) if p.is_file())
    return treffer[0] if treffer else None


_GETEILT = ("text_encoders", "tokenizer", "tae")


def _dit_in(ordner: Path) -> Path | None:
    """DiT eines Modell-Ordners: eine .gguf, sonst eine .safetensors, die keine VAE ist."""
    return _erste(ordner, "*.gguf") or next((p for p in sorted(ordner.glob("*.safetensors"))
                                            if "vae" not in p.name.lower()), None)


def _vae_in(ordner: Path) -> Path | None:
    """VAE eines Modell-Ordners: eine *vae*.safetensors daneben oder in vae/."""
    if ordner.is_dir():
        for p in sorted(ordner.glob("*.safetensors")):
            if "vae" in p.name.lower():
                return p
    return _erste(ordner / "vae", "*.safetensors")


# Vorgaben für Tempo-Fassungen (wenige Schritte, ohne Negativ-Durchlauf), wenn der Modell-Ordner keine
# video.json mitbringt und der Dateiname sie als Turbo ausweist.
TURBO_VORGABEN = {"steps": 4, "cfg": 1.0}


def _vorgaben(ordner: Path, dit: Path) -> dict:
    """Vorgaben eines Modells: video.json im Modell-Ordner (steps, cfg, shift), sonst Turbo nach Dateiname."""
    datei = ordner / "video.json"
    if datei.is_file():
        try:
            roh = json.loads(datei.read_text(encoding="utf-8"))
            return {k: float(roh[k]) for k in ("steps", "cfg", "shift") if k in roh}
        except (ValueError, TypeError, OSError):
            return {}
    return dict(TURBO_VORGABEN) if "turbo" in dit.name.lower() else {}


def modelle() -> dict[str, dict]:
    """Alle Video-Modelle, je Unterordner eines (eine .gguf – oder .safetensors mit .json – samt ihrer
    VAE); Text-Encoder und Tokenizer im Modell-Ordner oder geteilt oben im Video-Ordner."""
    d = video_dir()
    aus: dict[str, dict] = {}
    if d.is_dir():
        for unter in sorted(p for p in d.iterdir() if p.is_dir() and p.name not in _GETEILT):
            dit = _dit_in(unter)
            if dit is None:
                continue
            cfg = dit.with_suffix(".json")
            aus[unter.name] = {"name": unter.name, "ordner": unter, "dit": dit, "gguf": dit.suffix.lower() == ".gguf",
                               "config": cfg if cfg.exists() else None, "vae": _vae_in(unter),
                               "vorgaben": _vorgaben(unter, dit)}
    return aus


def _modell_mit_endbild() -> str | None:
    """Erstes vorhandene Modell, das Start- und Endbild kann (Fun InP)."""
    for name, m in modelle().items():
        try:
            if FAMILIEN[familie(dit_config(m))]["bedingung"] == "maske":
                return name
        except Exception:
            continue
    return None


def _modell_ohne_endbild() -> str | None:
    """Erstes vorhandene Modell, das aus Text beginnen kann (TI2V) – Turbo zuletzt (ohne Startbild Gittermuster)."""
    kandidaten = []
    for name, m in modelle().items():
        try:
            if FAMILIEN[familie(dit_config(m))]["bedingung"] != "maske":
                kandidaten.append((m.get("dit") is not None and "turbo" in Path(m["dit"]).name.lower(), name))
        except Exception:
            continue
    return min(kandidaten)[1] if kandidaten else None


def gewaehlt() -> str | None:
    """Name des gewählten Modells (Config `video_modell`), sonst das erste ohne Endbild-Bedingung (Fun InP ist
    für Schlüsselbilder da und wird dafür von selbst genommen), sonst das erste überhaupt."""
    alle = modelle()
    wunsch = str(_cfg("video_modell", "")).strip()
    if wunsch in alle:
        return wunsch
    for name, m in alle.items():
        try:
            if FAMILIEN[familie(dit_config(m))]["bedingung"] != "maske":
                return name
        except Exception:
            continue
    return next(iter(alle), None)


def set_modell(name: str) -> str:
    alle = modelle()
    if name not in alle:
        raise ValueError(f"Kein Video-Modell '{name}' – vorhanden: {', '.join(alle) or 'keins'}")
    import config
    config.update(video_modell=name)
    return name


def teile(name: str | None = None) -> dict:
    d = video_dir()
    m = modelle().get(name or gewaehlt() or "", {"name": None, "ordner": None, "dit": None, "gguf": False,
                                                 "config": None, "vae": None, "vorgaben": {}})
    orte = ([m["ordner"]] if m["ordner"] else []) + [d]          # Modell-Ordner zuerst, dann geteilt
    text = next((p for o in orte if (p := _erste(o / "text_encoders", "*.gguf"))), None)
    tok = next((p for o in orte if (p := o / "tokenizer" / "tokenizer.json").exists()), d / "tokenizer" / "tokenizer.json")
    return {**m, "text": text, "tokenizer": tok}


def _gguf_dit_config(pfad: Path) -> dict:
    """Konfiguration eines Wan-DiT aus den Tensorformen der GGUF (sie hat keine Hyperparameter im Kopf)."""
    from ggufengine.gguf import GGUFFile
    with GGUFFile(str(pfad)) as gg:
        if gg.architecture != "wan":
            raise RuntimeError(f"{pfad.name}: Bauart {gg.architecture!r}, erwartet wan")
        pe = gg.tensors["patch_embedding.weight"].dims                 # GGUF-Reihenfolge: (pw, ph, pt, in, dim)
        dim, in_dim = int(pe[-1]), int(pe[-2])
        patch = tuple(int(v) for v in reversed(pe[:3]))
        layers = 1 + max(int(n.split(".")[1]) for n in gg.tensors if n.startswith("blocks."))
        ffn = int(gg.tensors["blocks.0.ffn.0.weight"].dims[1])
        out = int(gg.tensors["head.head.weight"].dims[1]) // (patch[0] * patch[1] * patch[2])
        freq = int(gg.tensors["time_embedding.0.weight"].dims[0])
        text_dim = int(gg.tensors["text_embedding.0.weight"].dims[0])
    return {"dim": dim, "in_dim": in_dim, "out_dim": out, "num_heads": dim // 128, "num_layers": layers,
            "ffn_dim": ffn, "freq_dim": freq, "text_dim": text_dim, "text_len": TEXT_LEN, "patch_size": patch,
            "eps": 1e-6}


def dit_config(t: dict) -> dict:
    if t.get("gguf"):
        return _gguf_dit_config(t["dit"])
    return json.loads(t["config"].read_text(encoding="utf-8"))


def familie(cfg: dict) -> str:
    """Bauart am Eingang des DiT (48 = Wan 2.2 TI2V, 100 = Wan 2.2 Fun InP)."""
    in_dim = int(cfg.get("in_dim", 16))
    for name, f in FAMILIEN.items():
        if f["in_dim"] == in_dim:
            return name
    raise RuntimeError(f"Unbekannte Video-Bauart (in_dim {in_dim})")


def missing_reason(name: str | None = None) -> str | None:
    """None, wenn alles da ist – sonst, was fehlt (für die Oberfläche)."""
    d = video_dir()
    t = teile(name)
    if t["dit"] is None:
        return f"Kein Video-Modell in {d}: je Unterordner eine Wan-2.2-.gguf mit ihrer VAE"
    if not t["gguf"] and t["config"] is None:
        return f"Konfiguration fehlt: {t['dit'].with_suffix('.json')} (dim, num_layers …)"
    if t["text"] is None:
        return f"Text-Encoder fehlt: eine umT5-XXL-.gguf nach {t['ordner'] / 'text_encoders'} oder {d / 'text_encoders'}"
    if t["vae"] is None:
        return f"VAE fehlt für {t['name']}: eine *vae*.safetensors neben dem Modell"
    if not t["tokenizer"].exists():
        return f"Tokenizer fehlt: {t['tokenizer']}"
    for datei in (t["dit"], t["vae"], t["text"], t["tokenizer"]):   # das Modell zuerst: vor jeder Vorarbeit
        if (grund := _datei_kaputt(datei)):
            return f"{datei.name} {grund} – bitte neu laden: {datei}"
    try:
        familie(dit_config(t))
    except Exception as exc:
        return f"Modell {t['name']} nicht lesbar: {exc}"
    return None


def _datei_kaputt(pfad: Path) -> str | None:
    """Leere oder abgeschnittene Modelldatei erkennen, ohne sie zu laden: safetensors und GGUF über ihre
    Kopfzeilen (die angekündigten Daten müssen in die Datei passen), alles andere nur auf 0 Byte."""
    import struct
    try:
        groesse = pfad.stat().st_size
        if groesse == 0:
            return "ist leer"
        if pfad.suffix == ".safetensors":
            with open(pfad, "rb") as f:
                n = struct.unpack("<Q", f.read(8))[0]
                if n <= 0 or 8 + n > groesse:
                    return "ist unvollständig (Kopf fehlt)"
                kopf = json.loads(f.read(n))
            ende = max((v["data_offsets"][1] for k, v in kopf.items() if k != "__metadata__"), default=0)
            if 8 + n + ende > groesse:
                return f"ist unvollständig ({groesse:,} von {8 + n + ende:,} Bytes)".replace(",", ".")
        elif pfad.suffix == ".gguf":
            from ggufengine.gguf import GGUFFile
            with GGUFFile(str(pfad)):
                pass                                           # prüft beim Öffnen, dass alle Tensoren passen
    except Exception as exc:
        return f"ist beschädigt ({type(exc).__name__})"
    return None


def verfuegbar() -> bool:
    return missing_reason() is None


# ===========================================================================
#  Gewichte laden
# ===========================================================================

def _lade_safetensors(pfad: Path, dtype=None) -> dict:
    from safetensors.torch import load_file
    sd = load_file(str(pfad))
    if dtype is not None:
        sd = {k: (v.to(dtype) if v.is_floating_point() else v) for k, v in sd.items()}
    return sd


class _Q:
    """Eine Matrix aus einer GGUF-Datei, die in ihren Blöcken bleibt (Q8_0 …): auf der Karte rechnet sie
    über die Kerne des GGUF-Motors (PackedLinear), Zeilen holt sie einzeln entpackt (Einbettung)."""

    def __init__(self, raw, ggml_type: int, n_out: int, n_in: int):
        self.raw, self.ggml_type, self.n_out, self.n_in = raw, ggml_type, n_out, n_in
        self._pl = None

    def __call__(self, x):
        from ggufengine.models.common import PackedLinear
        if self._pl is None or self._pl.raw.device != self.raw.device:
            self._pl = PackedLinear(self.raw, self.ggml_type, self.n_out, self.n_in, None, x.dtype)
        return self._pl(x)

    def zeilen(self, ids, dtype):
        from ggufengine.gguf import GGML_TYPES
        from ggufengine.quant import dequantize
        import torch
        _, bs, ts = GGML_TYPES[self.ggml_type]
        je = self.n_in // bs * ts
        idx = torch.as_tensor(ids, dtype=torch.long, device=self.raw.device)
        roh = self.raw.view(self.n_out, je)[idx].reshape(-1)
        return dequantize(roh, self.ggml_type, len(ids) * self.n_in, dtype).view(len(ids), self.n_in)


_FP8_OK: dict = {}


def _fp8_bereit(device) -> bool:
    """FP8-Tensorkerne mit einer Skala je Tensor (Ada/Blackwell, sm_89+) – einmal ausprobiert."""
    key = str(device)
    if key not in _FP8_OK:
        ok = False
        try:
            import torch
            d = torch.device(device)
            if d.type == "cuda" and torch.cuda.get_device_capability(d) >= (8, 9):
                a = torch.ones(16, 16, device=d).to(torch.float8_e4m3fn)
                eins = torch.ones((), device=d)
                torch._scaled_mm(a, a.t(), scale_a=eins, scale_b=eins, out_dtype=torch.bfloat16)
                ok = True
        except Exception:
            ok = False
        _FP8_OK[key] = ok
    return _FP8_OK[key]


class _F8:
    """Matrix als FP8 (e4m3) mit einer Skala je Tensor, einmal beim Laden aus dem GGUF-Block ausgepackt statt
    in jedem Schritt. Auf der Karte multiplizieren die FP8-Tensorkerne (torch._scaled_mm, wie ComfyUIs
    fp8_e4m3fn_fast); die Aktivierung wird je Aufruf mit ihrem Betragsmaximum skaliert statt abgeschnitten."""

    MAX = 448.0                                          # größter endlicher e4m3-Wert

    def __init__(self, w):
        import torch
        self.n_out, self.n_in = w.shape
        self.dtype = w.dtype if w.dtype != torch.float32 else torch.bfloat16    # Ausgabe der Kerne
        self.ws = (w.abs().amax().float() / self.MAX).clamp(min=1e-12)
        self.w8 = (w.float() / self.ws).clamp(-self.MAX, self.MAX).to(torch.float8_e4m3fn)

    @classmethod
    def aus_gguf(cls, q: "_Q", dtype) -> "_F8":
        from ggufengine.quant import dequantize
        return cls(dequantize(q.raw.reshape(-1), q.ggml_type, q.n_out * q.n_in, dtype).view(q.n_out, q.n_in))

    @staticmethod
    def passt(q: "_Q") -> bool:
        return q.n_in % 16 == 0 and q.n_out % 16 == 0         # Bedingung der FP8-Kerne

    ZIEL = 440.0                                         # Luft bis 448: die bf16-Rundung beim Skalieren bleibt darunter

    @classmethod
    def quant(cls, x2):
        """Aktivierung (M, K) -> (FP8, Skala, M). Einmal je Eingabe, auch wenn mehrere Matrizen sie lesen
        (q, k, v); zwei Durchgänge: Betragsmaximum, dann skalieren und umwandeln (ohne Abschneiden)."""
        import torch
        xs = (torch.linalg.vector_norm(x2, float("inf")).float() / cls.ZIEL).clamp(min=1e-12)
        return (x2 * (1.0 / xs).to(x2.dtype)).to(torch.float8_e4m3fn), xs, x2.shape[0]

    def __call__(self, x, bias=None, q=None):
        import torch
        x2 = x.reshape(-1, self.n_in)
        if not x2.is_cuda:
            y = x2.to(self.dtype) @ (self.w8.to(self.dtype) * self.ws.to(self.dtype)).t()
            y = y if bias is None else y + bias.to(self.dtype)
            return y.reshape(*x.shape[:-1], self.n_out)
        x8, xs, M = q or self.quant(x2)
        y = torch._scaled_mm(x8, self.w8.t(), scale_a=xs, scale_b=self.ws, out_dtype=self.dtype,
                             bias=None if bias is None else bias.to(self.dtype))[:M]
        return y.reshape(*x.shape[:-1], self.n_out)


# Nur die Matrizen, die über alle Bild-Token laufen: dort liegt die Rechenzeit. Zeit-, Text- und
# Kontext-Projektionen (wenige Zeilen) steuern Modulation und Cross-Attention jeder Schicht und bleiben genau.
_FP8_TEIL = re.compile(r"blocks\.\d+\.(self_attn\.[qkvo]|cross_attn\.[qo]|ffn\.[02])\.weight$")


def _als_fp8(sd: dict, dtype, teil=_FP8_TEIL) -> int:
    """Ersetzt die Bild-Matrizen eines DiT auf der Karte nacheinander durch _F8 (so liegt nie alles doppelt
    im VRAM): gepackte GGUF-Matrizen und dichte (z. B. mit eingemischter LoRA). Gibt die Zahl zurück."""
    import torch
    n = 0
    for k in list(sd):
        v = sd[k]
        if teil is not None and not teil.search(k):
            continue
        if isinstance(v, _Q) and _F8.passt(v):
            sd[k] = _F8.aus_gguf(v, dtype)
        elif isinstance(v, torch.Tensor) and v.dim() == 2 and v.shape[0] % 16 == 0 and v.shape[1] % 16 == 0:
            sd[k] = _F8(v.to(dtype))
        else:
            continue
        del v
        n += 1
    return n


def _lade_gguf(pfad: Path, dtype) -> dict:
    """umT5-Encoder aus der GGUF: große Matrizen als _Q (bleiben gepackt), Normen entpackt."""
    from ggufengine.gguf import GGML_TYPES, GGUFFile
    from ggufengine.quant import dequantize, raw_to_tensor
    sd: dict = {}
    with GGUFFile(str(pfad)) as gg:
        if gg.architecture not in ("t5encoder", "t5"):
            raise RuntimeError(f"{pfad.name}: Bauart {gg.architecture!r}, erwartet umT5-Encoder (t5encoder)")
        sd["__meta__"] = {"n_layer": int(gg.get("t5encoder.block_count") or gg.get("t5.block_count") or 24),
                          "n_head": int(gg.get("t5encoder.attention.head_count") or 64),
                          "d_kv": int(gg.get("t5encoder.attention.key_length") or 64),
                          "buckets": int(gg.get("t5encoder.attention.relative_buckets_count") or 32),
                          "eps": float(gg.get("t5encoder.attention.layer_norm_rms_epsilon") or 1e-6)}
        for name, ti in gg.tensors.items():
            raw = raw_to_tensor(gg.tensor_bytes(ti), "cpu").clone()
            typ = GGML_TYPES[ti.ggml_type][0]
            if len(ti.dims) == 2 and typ not in ("F32", "F16", "BF16"):
                n_in, n_out = int(ti.dims[0]), int(ti.dims[1])
                sd[name] = _Q(raw, ti.ggml_type, n_out, n_in)
            else:
                shape = tuple(int(d) for d in reversed(ti.dims))
                sd[name] = dequantize(raw, ti.ggml_type, ti.n_elements, dtype).view(shape)
    return sd


def _lade_gguf_dit(pfad: Path, dtype) -> dict:
    """Wan-DiT aus einer GGUF (ComfyUI-GGUF-Format, Namen wie im Original): Matrizen bleiben gepackt (_Q),
    alles andere (Bias, Normen, Patch-Conv, Modulation) entpackt in `dtype`."""
    from ggufengine.gguf import GGML_TYPES, GGUFFile
    from ggufengine.quant import dequantize, raw_to_tensor
    sd: dict = {}
    with GGUFFile(str(pfad)) as gg:
        for name, ti in gg.tensors.items():
            raw = raw_to_tensor(gg.tensor_bytes(ti), "cpu").clone()
            typ = GGML_TYPES[ti.ggml_type][0]
            if len(ti.dims) == 2 and typ not in ("F32", "F16", "BF16"):
                sd[name] = _Q(raw, ti.ggml_type, int(ti.dims[1]), int(ti.dims[0]))
            else:
                shape = tuple(int(d) for d in reversed(ti.dims))
                sd[name] = dequantize(raw, ti.ggml_type, ti.n_elements, dtype).view(shape)
    return sd


# ===========================================================================
#  umT5-XXL-Encoder (Text -> 4096er Kontext)
# ===========================================================================

def _tokenizer():
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(teile()["tokenizer"]))
    return lambda text: tok.encode(text).ids[:TEXT_LEN]        # mit </s> am Ende


def _rel_bucket(rel, buckets: int, max_distance: int = 128):
    """T5-Buckets für den Abstand (bidirektional): nah genau, fern logarithmisch."""
    import torch
    n = buckets // 2
    out = (rel > 0).long() * n
    rel = rel.abs()
    exakt = n // 2
    gross = exakt + (torch.log(rel.float().clamp(min=1) / exakt) / math.log(max_distance / exakt)
                     * (n - exakt)).long()
    gross = torch.minimum(gross, torch.full_like(gross, n - 1))
    return out + torch.where(rel < exakt, rel, gross)


def _rms(x, w, eps):
    """RMS-Norm in float32 (ein Kern), Ergebnis im Typ der Eingabe."""
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=w.float(), eps=eps).to(x.dtype)


def _rms_rope(x, w, eps, freqs, n: int):
    """q/k: RMS-Norm über alle Köpfe und RoPE in einem float32-Durchgang, erst danach zurück nach bf16."""
    import torch
    import torch.nn.functional as F
    S, D = x.shape
    y = F.rms_norm(x.float(), (D,), weight=w.float(), eps=eps).view(S, n, D // n // 2, 2)
    return torch.view_as_real(torch.view_as_complex(y) * freqs).flatten(2).to(x.dtype)


def _lin(x, w, b=None):
    import torch.nn.functional as F
    if isinstance(w, _F8):
        return w(x, b)
    if callable(w):
        y = w(x)
        return y if b is None else y + b
    return F.linear(x, w.to(x.dtype), None if b is None else b.to(x.dtype))


def _lin_mehrere(x, paare):
    """Mehrere Matrizen auf dieselbe Eingabe; FP8-Matrizen teilen sich die quantisierte Eingabe."""
    if x.is_cuda and all(isinstance(w, _F8) for w, _ in paare):
        q = _F8.quant(x.reshape(-1, x.shape[-1]))
        return [w(x, b, q) for w, b in paare]
    return [_lin(x, w, b) for w, b in paare]


class UmT5:
    def __init__(self, sd: dict):
        self.sd = sd
        m = sd["__meta__"]
        self.n_layer, self.n_head, self.d_kv, self.buckets, self.eps = (m["n_layer"], m["n_head"], m["d_kv"],
                                                                        m["buckets"], m["eps"])

    def __call__(self, ids: list[int], dtype):
        """(L, d_model) – nur die echten Token, kein Auffüllen."""
        import torch
        import torch.nn.functional as F
        sd = self.sd
        embd = sd["token_embd.weight"]
        x = embd.zeilen(ids, dtype) if isinstance(embd, _Q) else embd[torch.as_tensor(ids, device=embd.device)].to(dtype)
        L = x.shape[0]
        pos = torch.arange(L, device=x.device)
        bucket = _rel_bucket(pos[None, :] - pos[:, None], self.buckets)        # Speicher - Anfrage
        bias0 = None
        for i in range(self.n_layer):
            p = f"enc.blk.{i}."
            h = _rms(x, sd[p + "attn_norm.weight"], self.eps)
            q = _lin(h, sd[p + "attn_q.weight"]).view(L, self.n_head, self.d_kv).transpose(0, 1)
            k = _lin(h, sd[p + "attn_k.weight"]).view(L, self.n_head, self.d_kv).transpose(0, 1)
            v = _lin(h, sd[p + "attn_v.weight"]).view(L, self.n_head, self.d_kv).transpose(0, 1)
            rel = sd.get(p + "attn_rel_b.weight")
            if rel is not None:
                bias0 = rel.float()[bucket].permute(2, 0, 1)                   # (Köpfe, L, L)
            punkte = torch.matmul(q.float(), k.float().transpose(-1, -2)) + bias0   # T5: ohne 1/sqrt(d)
            a = torch.matmul(F.softmax(punkte, dim=-1).to(v.dtype), v).transpose(0, 1).reshape(L, -1)
            x = x + _lin(a, sd[p + "attn_o.weight"])
            h = _rms(x, sd[p + "ffn_norm.weight"], self.eps)
            g = F.gelu(_lin(h, sd[p + "ffn_gate.weight"]), approximate="tanh") * _lin(h, sd[p + "ffn_up.weight"])
            x = x + _lin(g, sd[p + "ffn_down.weight"])
        return _rms(x, sd["enc.output_norm.weight"], self.eps)


# ===========================================================================
#  Wan-2.2-VAE (3D, kausal) – Stück für Stück mit Zeit-Cache wie das Original
# ===========================================================================

class WanVAE22:
    """2×2-Patch davor/danach (16× Fläche), 48 Latent-Kanäle, je Stufe eine parameterfreie Abkürzung
    (Mittel beim Verkleinern, Verdoppeln beim Vergrößern), Namen `encoder.downsamples.{Stufe}.downsamples.{j}`.
    Encoder: Bild für Bild (das erste allein, dann je 4) -> 1 + 4k Bilder werden 1 + k Latent-Bilder.
    Decoder: je Latent-Bild. Jeder kausale 3D-Conv behält die letzten CACHE_T Bilder seiner Eingabe
    als Cache (`caches[idx]`), die Reihenfolge der Convs zählt `idx` hoch – genau wie im Original."""

    def __init__(self, sd: dict):
        self.sd = sd
        self.dim = sd["encoder.conv1.weight"].shape[0]
        self.z_dim = sd["conv2.weight"].shape[0]
        self.temporal = [False, True, True]                # Zeit-Verkleinerung je Stufe (Original)
        self.dtype = sd["decoder.conv1.weight"].dtype      # gerechnet wird in der Genauigkeit der Gewichte

    # --- Bausteine ---------------------------------------------------------
    def _cconv(self, x, name, cache=None, stride=(1, 1, 1), zeitpad: bool = True):
        """Kausaler 3D-Conv: vorn in der Zeit um k−1 auffüllen (oder mit dem Cache), in der Fläche mittig.
        `zeitpad=False` für den Zeit-Conv beim Halbieren (Original: padding 0)."""
        import torch
        import torch.nn.functional as F
        w, b = self.sd[name + ".weight"], self.sd.get(name + ".bias")
        kt, kh, kw = w.shape[2:]
        pt, ph, pw = (kt - 1 if zeitpad else 0), kh // 2, kw // 2
        if cache is not None and pt > 0:
            x = torch.cat([cache.to(x.dtype), x], 2)
            pt -= cache.shape[2]
        if pt or ph or pw:
            x = F.pad(x, (pw, pw, ph, ph, max(pt, 0), 0))
        return F.conv3d(x, w.to(x.dtype), None if b is None else b.to(x.dtype), stride=stride)

    def _cconv_cache(self, x, name, caches, idx, stride=(1, 1, 1)):
        import torch
        if caches is None:
            return self._cconv(x, name, None, stride)
        i = idx[0]
        idx[0] += 1
        cache_x = x[:, :, -CACHE_T:].clone()
        if cache_x.shape[2] < 2 and caches.get(i) is not None:
            cache_x = torch.cat([caches[i][:, :, -1:].to(cache_x.device), cache_x], 2)
        y = self._cconv(x, name, caches.get(i), stride)
        caches[i] = cache_x
        return y

    def _norm(self, x, name):
        import torch.nn.functional as F
        g = self.sd[name + ".gamma"]
        form = [1, -1] + [1] * (x.dim() - 2)
        return F.normalize(x, dim=1) * (g.shape[0] ** 0.5) * g.reshape(form).to(x.dtype)

    def _res(self, x, p, caches, idx):
        import torch.nn.functional as F
        h = self._cconv(x, p + ".shortcut") if (p + ".shortcut.weight") in self.sd else x
        x = self._cconv_cache(F.silu(self._norm(x, p + ".residual.0")), p + ".residual.2", caches, idx)
        x = self._cconv_cache(F.silu(self._norm(x, p + ".residual.3")), p + ".residual.6", caches, idx)
        return x + h

    def _attn(self, x, p):
        import torch.nn.functional as F
        b, c, t, h, w = x.shape
        y = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
        y = self._norm(y, p + ".norm")
        qkv = F.conv2d(y, self.sd[p + ".to_qkv.weight"].to(y.dtype), self.sd[p + ".to_qkv.bias"].to(y.dtype))
        q, k, v = qkv.reshape(b * t, 1, c * 3, -1).permute(0, 1, 3, 2).chunk(3, dim=-1)   # (bt, 1, hw, c)
        y = F.scaled_dot_product_attention(q, k, v)
        y = y.squeeze(1).permute(0, 2, 1).reshape(b * t, c, h, w)
        y = F.conv2d(y, self.sd[p + ".proj.weight"].to(y.dtype), self.sd[p + ".proj.bias"].to(y.dtype))
        return x + y.reshape(b, t, c, h, w).permute(0, 2, 1, 3, 4)

    def _flaeche(self, x, p, hoch: bool):
        """Der 2D-Teil eines Resample: je Bild verdoppeln (nearest + Conv) oder halbieren (Pad + Conv s2)."""
        import torch.nn.functional as F
        b, c, t, h, w = x.shape
        y = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
        wt, bs = self.sd[p + ".resample.1.weight"].to(y.dtype), self.sd[p + ".resample.1.bias"].to(y.dtype)
        if hoch:
            y = F.conv2d(F.interpolate(y, scale_factor=2.0, mode="nearest-exact"), wt, bs, padding=1)
        else:
            y = F.conv2d(F.pad(y, (0, 1, 0, 1)), wt, bs, stride=2)
        c2, h2, w2 = y.shape[1:]
        return y.reshape(b, t, c2, h2, w2).permute(0, 2, 1, 3, 4)

    def _resample(self, x, p, modus, caches, idx):
        import torch
        if modus == "upsample3d" and caches is not None:
            i = idx[0]
            idx[0] += 1
            if caches.get(i) is None:
                caches[i] = "Rep"                              # das erste Bild bleibt einzeln
            else:
                cache_x = x[:, :, -CACHE_T:].clone()
                if cache_x.shape[2] < 2 and caches[i] != "Rep":
                    cache_x = torch.cat([caches[i][:, :, -1:].to(cache_x.device), cache_x], 2)
                if cache_x.shape[2] < 2 and caches[i] == "Rep":
                    cache_x = torch.cat([torch.zeros_like(cache_x), cache_x], 2)
                x = self._cconv(x, p + ".time_conv", None if caches[i] == "Rep" else caches[i])
                caches[i] = cache_x
                b, c2, t, h, w = x.shape
                x = x.reshape(b, 2, c2 // 2, t, h, w)
                x = torch.stack((x[:, 0], x[:, 1]), 3).reshape(b, c2 // 2, t * 2, h, w)
        x = self._flaeche(x, p, hoch=modus.startswith("up"))
        if modus == "downsample3d" and caches is not None:        # Zeit erst NACH der Fläche (Original)
            i = idx[0]
            idx[0] += 1
            if caches.get(i) is None:
                caches[i] = x.clone()
            else:
                cache_x = x[:, :, -1:].clone()
                x = self._cconv(torch.cat([caches[i][:, :, -1:].to(x.device), x], 2), p + ".time_conv",
                                None, stride=(2, 1, 1), zeitpad=False)
                caches[i] = cache_x
        return x

    @staticmethod
    def _patch(x):
        """(B, C, T, H, W) -> (B, 4C, T, H/2, W/2) – Kanalreihenfolge (c, r, q) wie im Original."""
        B, C, T, H, W = x.shape
        x = x.view(B, C, T, H // 2, 2, W // 2, 2)                  # (B, C, T, h, q, w, r)
        return x.permute(0, 1, 6, 4, 2, 3, 5).reshape(B, C * 4, T, H // 2, W // 2)

    @staticmethod
    def _unpatch(x):
        B, C4, T, h, w = x.shape
        C = C4 // 4
        x = x.view(B, C, 2, 2, T, h, w)                            # (B, C, r, q, T, h, w)
        return x.permute(0, 1, 4, 5, 3, 6, 2).reshape(B, C, T, h * 2, w * 2)

    @staticmethod
    def _mittel(x, out_c: int, ft: int, fs: int):
        """Abkürzung beim Verkleinern: Mittel über ft×fs×fs Nachbarn, Kanäle zu out_c gruppiert."""
        import torch.nn.functional as F
        pad_t = (ft - x.shape[2] % ft) % ft
        if pad_t:
            x = F.pad(x, (0, 0, 0, 0, pad_t, 0))
        B, C, T, H, W = x.shape
        x = x.view(B, C, T // ft, ft, H // fs, fs, W // fs, fs).permute(0, 1, 3, 5, 7, 2, 4, 6)
        x = x.reshape(B, C * ft * fs * fs, T // ft, H // fs, W // fs)
        return x.view(B, out_c, -1, T // ft, H // fs, W // fs).mean(2)

    @staticmethod
    def _doppelt(x, out_c: int, ft: int, fs: int, erstes: bool):
        """Abkürzung beim Vergrößern: Kanäle wiederholen und auf ft×fs×fs Nachbarn verteilen."""
        B, C, T, H, W = x.shape
        x = x.repeat_interleave(out_c * ft * fs * fs // C, dim=1)
        x = x.view(B, out_c, ft, fs, fs, T, H, W).permute(0, 1, 5, 2, 6, 3, 7, 4)
        x = x.reshape(B, out_c, T * ft, H * fs, W * fs)
        return x[:, :, ft - 1:] if erstes else x

    def _stufe(self, x, p: str, n_res: int, resample: str | None, caches, idx, kurz, erstes=False):
        """Eine Stufe: n_res Residualblöcke (+ Resample), dazu die Abkürzung `kurz(x_kopie)`."""
        kopie = x
        for j in range(n_res):
            x = self._res(x, f"{p}.{j}", caches, idx)
        if resample is not None:
            x = self._resample(x, f"{p}.{n_res}", resample, caches, idx)
        return x + kurz(kopie) if kurz is not None else x

    def _encoder(self, x, caches, idx):
        import torch.nn.functional as F
        x = self._cconv_cache(x, "encoder.conv1", caches, idx)
        dims = [self.dim, self.dim, self.dim * 2, self.dim * 4, self.dim * 4]
        for lvl in range(4):
            p = f"encoder.downsamples.{lvl}.downsamples"
            if lvl < 3:
                ft = 2 if self.temporal[lvl] else 1
                modus = "downsample3d" if ft == 2 else "downsample2d"
                out = dims[lvl + 1]
                x = self._stufe(x, p, 2, modus, caches, idx, lambda k, o=out, t=ft: self._mittel(k, o, t, 2))
            else:                                                  # letzte Stufe: Abkürzung = Eingang selbst
                x = self._stufe(x, p, 2, None, caches, idx, lambda k: k)
        x = self._res(x, "encoder.middle.0", caches, idx)
        x = self._attn(x, "encoder.middle.1")
        x = self._res(x, "encoder.middle.2", caches, idx)
        return self._cconv_cache(F.silu(self._norm(x, "encoder.head.0")), "encoder.head.2", caches, idx)

    def _decoder(self, x, caches, idx, erstes=False):
        import torch.nn.functional as F
        x = self._cconv_cache(x, "decoder.conv1", caches, idx)
        x = self._res(x, "decoder.middle.0", caches, idx)
        x = self._attn(x, "decoder.middle.1")
        x = self._res(x, "decoder.middle.2", caches, idx)
        hoch = list(reversed(self.temporal))                       # [True, True, False]
        for lvl in range(4):
            p = f"decoder.upsamples.{lvl}.upsamples"
            if lvl < 3:
                ft = 2 if hoch[lvl] else 1
                modus = "upsample3d" if ft == 2 else "upsample2d"
                out = self.sd[f"{p}.0.residual.6.weight"].shape[0]
                x = self._stufe(x, p, 3, modus, caches, idx,
                                lambda k, o=out, t=ft: self._doppelt(k, o, t, 2, erstes), erstes)
            else:
                x = self._stufe(x, p, 3, None, caches, idx, None)
        return self._cconv_cache(F.silu(self._norm(x, "decoder.head.0")), "decoder.head.2", caches, idx)

    def encode(self, video):
        """video (3, T, H, W) in [-1, 1], T = 1 + 4k, H/W Vielfache von 16 -> Latent (48, 1 + k, H/16, W/16)."""
        import torch
        import torch.nn.functional as F
        x = self._patch(video[None].to(self.dtype))
        caches: dict = {}
        stuecke = []
        t = x.shape[2]
        for i in range(1 + (t - 1) // 4):
            idx = [0]
            teil = x[:, :, :1] if i == 0 else x[:, :, 1 + 4 * (i - 1):1 + 4 * i]
            stuecke.append(self._encoder(teil, caches, idx))
        h = torch.cat(stuecke, 2).float()
        mu = F.conv3d(h, self.sd["conv1.weight"].float(), self.sd["conv1.bias"].float()).chunk(2, dim=1)[0]
        mean = torch.tensor(LATENT_MEAN, device=mu.device).view(1, -1, 1, 1, 1)
        std = torch.tensor(LATENT_STD, device=mu.device).view(1, -1, 1, 1, 1)
        return ((mu - mean) / std)[0]

    def decode(self, latent, on_status=None):
        import torch
        import torch.nn.functional as F
        mean = torch.tensor(LATENT_MEAN, device=latent.device).view(1, -1, 1, 1, 1)
        std = torch.tensor(LATENT_STD, device=latent.device).view(1, -1, 1, 1, 1)
        z = latent[None].float() * std + mean
        z = F.conv3d(z, self.sd["conv2.weight"].float(), self.sd["conv2.bias"].float()).to(self.dtype)
        caches: dict = {}
        bilder = []
        for i in range(z.shape[2]):
            idx = [0]
            bilder.append(self._decoder(z[:, :, i:i + 1], caches, idx, erstes=(i == 0)))
            if on_status:
                on_status(f"Video: fertige Bilder auspacken (VAE) {i + 1}/{z.shape[2]}")
        return self._unpatch(torch.cat(bilder, 2)).float().clamp(-1, 1)[0]

    def decode_kacheln(self, latent, on_status=None, kachel: int = 32, rand: int = 8):
        """Wie decode, aber in überlappenden Kacheln von `kachel`×`kachel` Latent-Punkten (wie ComfyUIs
        gekacheltes Auspacken): jede Kachel läuft mit eigenem Zeit-Cache durch alle Bilder, die Ränder werden
        über `rand` Punkte linear überblendet. Das Ergebnis sammelt sich im Arbeitsspeicher – im VRAM liegt
        nur eine Kachel. Bei kleinen Latents (oder kachel=0) ein Stück."""
        import torch
        _, T, h, w = latent.shape
        if kachel <= 0 or (h <= kachel and w <= kachel):
            return self.decode(latent, on_status)
        f = 16                                                     # 2×2-Patch × 8 der VAE
        ys, xs = _kachel_starts(h, kachel, rand), _kachel_starts(w, kachel, rand)
        out = torch.zeros(3, 1 + 4 * (T - 1), h * f, w * f)
        gew = torch.zeros(1, 1, h * f, w * f)
        n, k = len(ys) * len(xs), 0
        for y0 in ys:
            y1 = min(y0 + kachel, h)
            for x0 in xs:
                x1 = min(x0 + kachel, w)
                k += 1
                teil = self.decode(latent[:, :, y0:y1, x0:x1],
                                   (lambda m, k=k: on_status(f"{m} · Kachel {k}/{n}")) if on_status else None)
                m = (_rampe(y1 - y0, rand, y0 > 0, y1 < h, f)[:, None]
                     * _rampe(x1 - x0, rand, x0 > 0, x1 < w, f)[None, :])
                out[:, :, y0 * f:y1 * f, x0 * f:x1 * f] += teil.float().cpu() * m
                gew[:, :, y0 * f:y1 * f, x0 * f:x1 * f] += m
                del teil
        return out / gew


class TaeW22:
    """Kleiner Video-Decoder für Wan-2.2-Latents (TAEHV „taew2_2“ / „taew2_2_super“, MIT-Lizenz, Aufbau nach
    github.com/madebyollin/taehv): nur 2D-Faltungen; je Stufe drei Gedächtnis-Blöcke, die zusätzlich das vorige
    Zeitbild sehen, und eine Zeitverdopplung (TGrow). Liest das normierte Latent direkt, wie das DiT es ausgibt.
    Ein Vielfaches schneller als die große VAE, dafür etwas weicher. Die Super-Fassung hat breitere Blöcke
    (7×7-Tiefenfaltung + 1×1-Engpass) und ist schärfer."""

    def __init__(self, sd: dict):
        self.sd = {k[len("decoder."):]: v for k, v in sd.items() if k.startswith("decoder.")}
        self.enc = {k[len("encoder."):]: v for k, v in sd.items() if k.startswith("encoder.")}
        self.super = "0.weight" in self.sd                    # Super beginnt mit einer 1×1-Faltung statt Clamp
        self.dtype = self.sd["22.weight"].dtype
        self.plan = self._plan_super() if self.super else self._plan_normal()

    # --- Bausteine (jeder bekommt die Zeitbilder eines Stücks und den Gedächtnis-Speicher) -------------
    def _conv(self, i):
        import torch.nn.functional as F
        w, b = self.sd[f"{i}.weight"], self.sd.get(f"{i}.bias")
        return lambda x, mem: F.conv2d(x, w, b, padding=w.shape[-1] // 2)

    @staticmethod
    def _vorher(x, mem, i):
        """Eingang des vorigen Zeitbilds je Bild dieses Stücks (am Anfang Nullen); merkt sich das letzte."""
        import torch
        alt = mem.get(i)
        alt = torch.zeros_like(x[:1]) if alt is None else alt
        mem[i] = x[-1:]
        return torch.cat([alt, x[:-1]], 0)

    def _mem(self, i):
        import torch
        import torch.nn.functional as F
        w = [(self.sd[f"{i}.conv.{j}.weight"], self.sd[f"{i}.conv.{j}.bias"]) for j in (0, 2, 4)]

        def f(x, mem):
            h = F.relu(F.conv2d(torch.cat([x, self._vorher(x, mem, i)], 1), *w[0], padding=1))
            h = F.conv2d(F.relu(F.conv2d(h, *w[1], padding=1)), *w[2], padding=1)
            return F.relu(h + x)
        return f

    def _super_mem(self, i):
        import torch
        import torch.nn.functional as F
        tief = self.sd[f"{i}.conv.0.weight"]
        w1, b1, w3 = self.sd[f"{i}.conv.1.weight"], self.sd[f"{i}.conv.1.bias"], self.sd[f"{i}.conv.3.weight"]

        def f(x, mem):
            h = F.conv2d(torch.cat([x, self._vorher(x, mem, i)], 1), tief, padding=3, groups=tief.shape[0])
            return F.conv2d(F.relu(F.conv2d(h, w1, b1)), w3) + x
        return f

    def _tgrow(self, i, stride: int):
        import torch.nn.functional as F
        w = self.sd[f"{i}.conv.weight"]
        C = w.shape[1]
        w = w[-C * stride:]                                    # wie das Original: die letzten Kanäle, falls mehr
        return lambda x, mem: F.conv2d(x, w).reshape(-1, C, *x.shape[2:])

    def _plan_normal(self):
        import torch
        import torch.nn.functional as F
        relu = lambda x, mem: F.relu(x)                                          # noqa: E731
        hoch = lambda x, mem: F.interpolate(x, scale_factor=2.0, mode="nearest")  # noqa: E731
        plan = [lambda x, mem: torch.tanh(x / 3) * 3, self._conv(1), relu]
        for blocks, tgrow, conv, stride in (((3, 4, 5), 7, 8, 1), ((9, 10, 11), 13, 14, 2), ((15, 16, 17), 19, 20, 2)):
            plan += [self._mem(b) for b in blocks] + [hoch, self._tgrow(tgrow, stride), self._conv(conv)]
        return plan + [relu, self._conv(22)]

    def _plan_super(self):
        import torch.nn.functional as F
        relu = lambda x, mem: F.relu(x)                                          # noqa: E731
        mischen = lambda x, mem: F.pixel_shuffle(x, 2)                           # noqa: E731
        plan = [self._conv(0)]
        for blocks, conv, tgrow, stride in (((1, 2, 3), 4, 7, 1), ((8, 9, 10), 11, 14, 2), ((15, 16, 17), 18, 21, 2)):
            plan += [self._super_mem(b) for b in blocks] + [self._conv(conv), relu, mischen, self._tgrow(tgrow, stride)]
        return plan + [self._conv(22)]

    # --- Encoder: Bild -> Latent (2×2-Patch, drei Stufen mit Zeit-Bündeln und halber Fläche) ---------------
    def _enc_plan(self):
        import torch.nn.functional as F
        e = self.enc

        def conv(i, stride=1):
            w, b = e[f"{i}.weight"], e.get(f"{i}.bias")
            return lambda x, mem: F.conv2d(x, w, b, stride=stride, padding=w.shape[-1] // 2)

        def mem(i):
            w = [(e[f"{i}.conv.{j}.weight"], e[f"{i}.conv.{j}.bias"]) for j in (0, 2, 4)]

            def f(x, m):
                import torch
                h = F.relu(F.conv2d(torch.cat([x, self._vorher(x, m, ("e", i))], 1), *w[0], padding=1))
                h = F.conv2d(F.relu(F.conv2d(h, *w[1], padding=1)), *w[2], padding=1)
                return F.relu(h + x)
            return f

        def tpool(i):
            w = e[f"{i}.conv.weight"]
            stride = w.shape[1] // w.shape[0]
            return lambda x, m: F.conv2d(x.reshape(-1, stride * x.shape[1], *x.shape[2:]), w)
        plan = [conv(0), lambda x, m: F.relu(x)]
        for pool, cv, blocks in ((2, 3, (4, 5, 6)), (7, 8, (9, 10, 11)), (12, 13, (14, 15, 16))):
            plan += [tpool(pool), conv(cv, stride=2)] + [mem(b) for b in blocks]
        return plan + [conv(17)]

    def encode(self, video, stueck: int = 16):
        """Video (3, T, H, W) in [−1, 1], T = 1 + 4k -> normiertes Latent (48, 1 + k, H/16, W/16). Wie im Original
        wird hinten mit dem letzten Bild auf ein Vielfaches von 4 aufgefüllt (Rundweg mit decode: ~35 dB)."""
        import torch
        import torch.nn.functional as F
        plan = self._enc_plan()
        x = (video.permute(1, 0, 2, 3).to(self.dtype) + 1) / 2                 # (T, 3, H, W) in [0, 1]
        if x.shape[0] % 4:
            x = torch.cat([x, x[-1:].expand(4 - x.shape[0] % 4, -1, -1, -1)], 0)
        mem: dict = {}
        out = []
        with torch.no_grad():
            for a in range(0, x.shape[0], stueck):                              # stueck ist ein Vielfaches von 4
                h = F.pixel_unshuffle(x[a:a + stueck], 2)
                for schritt in plan:
                    h = schritt(h, mem)
                out.append(h.float())
        return torch.cat(out, 0).permute(1, 0, 2, 3)

    def decode(self, latent, on_status=None, stueck: int = 4):
        """Latent (48, T, h, w), normiert -> Video (3, 1 + 4(T−1), 16h, 16w) in [−1, 1] im Arbeitsspeicher.
        Läuft in Stücken von `stueck` Latent-Bildern; das Gedächtnis reicht über die Stücke (gleiches Ergebnis
        wie alles auf einmal)."""
        import torch
        import torch.nn.functional as F
        z = latent.permute(1, 0, 2, 3).to(self.dtype)                       # (T, C, h, w)
        mem: dict = {}
        bilder = []
        with torch.no_grad():
            for a in range(0, z.shape[0], stueck):
                x = z[a:a + stueck]
                for schritt in self.plan:
                    x = schritt(x, mem)
                bilder.append(F.pixel_shuffle(x, 2).clamp(0, 1).float().cpu())
                if on_status:
                    on_status(f"Video: Bilder auspacken (Mini-VAE) {min(a + stueck, z.shape[0])}/{z.shape[0]}")
        video = torch.cat(bilder, 0)[3:]                                    # die ersten 3 sind Anlauf (Original)
        return (video * 2 - 1).permute(1, 0, 2, 3)


TAE_DATEIEN = {"super": "taew2_2_super.safetensors", "klein": "taew2_2.safetensors"}


def _tae_datei(t: dict, art: str) -> Path | None:
    """Mini-VAE zum Auspacken: `art` super / klein (sonst die große VAE). Gesucht im Modell-Ordner, dessen
    tae/ und im geteilten tae/ des Video-Ordners; fehlt die Datei, bleibt es bei der großen VAE."""
    name = TAE_DATEIEN.get(art.strip().lower())
    if name is None:
        return None
    orte = ([t["ordner"], t["ordner"] / "tae"] if t.get("ordner") else []) + [video_dir() / "tae"]
    return next((o / name for o in orte if (o / name).is_file()), None)


def _kachel_starts(n: int, kachel: int, rand: int) -> list[int]:
    """Anfänge überlappender Kacheln über n Punkte; die letzte endet genau am Rand."""
    if n <= kachel:
        return [0]
    schritt = max(1, kachel - rand)
    starts = list(range(0, n - kachel, schritt))
    return starts + [n - kachel]


def _rampe(laenge: int, rand: int, links: bool, rechts: bool, f: int):
    """Gewicht je Bildpunkt einer Kachel: 1 in der Mitte, linear ansteigend über rand·f Punkte an Seiten mit
    Nachbar-Kachel (nie 0, damit sich die Gewichte überall zu > 0 addieren)."""
    import torch
    L, R = laenge * f, min(rand * f, laenge * f // 2)
    g = torch.ones(L)
    rampe = (torch.arange(R, dtype=torch.float32) + 1) / (R + 1)
    if links and R:
        g[:R] = rampe
    if rechts and R:
        g[-R:] = torch.minimum(g[-R:], rampe.flip(0))
    return g


# ===========================================================================
#  Wan-DiT (Flow-Matching) – Text und Startbild als festes erstes Latent-Bild (TI2V)
# ===========================================================================

def _rope_tabelle(n: int, dim: int, device, theta: float = 10000.0):
    import torch
    freqs = 1.0 / (theta ** (torch.arange(0, dim, 2, dtype=torch.float32, device=device) / dim))
    return torch.polar(torch.ones_like(freqs).expand(n, -1), torch.outer(torch.arange(n, device=device).float(), freqs))


def _sinus(dim: int, t):
    """Zeit-Einbettung wie im Original: erst cos, dann sin."""
    import torch
    half = dim // 2
    x = torch.outer(t.float(), torch.pow(10000.0, -torch.arange(half, device=t.device).float() / half))
    return torch.cat([torch.cos(x), torch.sin(x)], 1)


class WanDiT:
    def __init__(self, sd: dict, cfg: dict):
        self.sd = sd
        self.dim = int(cfg["dim"])
        self.n_head = int(cfg["num_heads"])
        self.n_layer = int(cfg["num_layers"])
        self.freq_dim = int(cfg.get("freq_dim", 256))
        self.out_dim = int(cfg.get("out_dim", 48))
        self.eps = float(cfg.get("eps", 1e-6))
        self.text_len = int(cfg.get("text_len", TEXT_LEN))
        self.patch = tuple(cfg.get("patch_size", (1, 2, 2)))
        d = self.dim // self.n_head
        self.rope_dims = (d - 4 * (d // 6), 2 * (d // 6), 2 * (d // 6))
        self._tabellen = None                        # (Gerät, RoPE-Tabellen je Achse)
        self.lernen = False                          # Training: Blöcke mit Gradient-Checkpointing
        self._kein_cudnn = False

    def _freqs(self, F_, H, W, device):
        import torch
        if self._tabellen is None or self._tabellen[0] != device:
            self._tabellen = (device, [_rope_tabelle(1024, n, device) for n in self.rope_dims])
        t_, h_, w_ = self._tabellen[1]
        f = torch.cat([t_[:F_].view(F_, 1, 1, -1).expand(F_, H, W, -1),
                       h_[:H].view(1, H, 1, -1).expand(F_, H, W, -1),
                       w_[:W].view(1, 1, W, -1).expand(F_, H, W, -1)], -1)
        return f.reshape(F_ * H * W, 1, -1)

    @staticmethod
    def _rope(x, freqs):
        import torch
        s, n, d = x.shape
        xc = torch.view_as_complex(x.float().reshape(s, n, d // 2, 2))
        return torch.view_as_real(xc * freqs).flatten(2).to(x.dtype)

    def _attn(self, q, k, v):
        """Attention über alle Bildtoken (bis ~33k): cuDNN ist auf Blackwell 2,5× schneller als der
        speichereffiziente Kern (Flash fehlt im Windows-Bau von torch); ohne cuDNN der Standardweg."""
        import torch.nn.functional as F
        q, k, v = (t.transpose(0, 1)[None] for t in (q, k, v))                  # (1, Köpfe, S, d), ohne Kopie
        if q.is_cuda and not self._kein_cudnn:
            try:
                from torch.nn.attention import SDPBackend, sdpa_kernel
                with sdpa_kernel(SDPBackend.CUDNN_ATTENTION):       # nur cuDNN – eine Liste nähme torch' Reihenfolge
                    out = F.scaled_dot_product_attention(q, k, v)
                return out[0].transpose(0, 1).flatten(1)
            except Exception:
                self._kein_cudnn = True                               # einmal gescheitert: Standardweg
        return F.scaled_dot_product_attention(q, k, v)[0].transpose(0, 1).flatten(1)

    def __call__(self, x, t, ctx, cache=None, schritt: int = 0, zweig: int = 0, y=None):
        """x (C, F, H, W) Latent, t Zeitschritt (0…1000) – eine Zahl oder je Latent-Bild (F,), ctx (L, 4096)
        Text, y (52, F, H, W) Bedingung (Maske + Latent, Fun InP) oder None -> Geschwindigkeit (C_out, F, H, W).
        `cache` (MagCache): darf die Blöcke im Schritt `schritt` für Zweig `zweig` (0 = mit Text, 1 = Negativ)
        durch den gemerkten Residual ersetzen."""
        import torch
        import torch.nn.functional as F
        sd = self.sd
        dtype = sd["patch_embedding.weight"].dtype
        D = self.dim
        x = (torch.cat([x, y], 0) if y is not None else x)[None].to(dtype)
        x = F.conv3d(x, sd["patch_embedding.weight"], sd["patch_embedding.bias"], stride=self.patch)
        _, _, F_, H, W = x.shape
        self._bilder = F_                                                               # für block_lauf je Bild
        x = x.flatten(2).transpose(1, 2)[0]                                             # (S, D)
        freqs = self._freqs(F_, H, W, x.device)
        tt = torch.as_tensor(t, dtype=torch.float32, device=x.device).reshape(-1)       # (1,) oder (F,)
        e = _sinus(self.freq_dim, tt).to(dtype)
        e = _lin(F.silu(_lin(e, sd["time_embedding.0.weight"], sd["time_embedding.0.bias"])),
                 sd["time_embedding.2.weight"], sd["time_embedding.2.bias"])            # (1 oder F, D)
        e0 = _lin(F.silu(e), sd["time_projection.1.weight"], sd["time_projection.1.bias"]).view(-1, 6, D).float()

        if cache is not None and cache.ueberspringen(schritt, zweig):
            x = x.float() + cache.residual[zweig]
        else:
            x = self._bloecke(x, e0, ctx, freqs, dtype, cache, zweig)
        mk = sd["head.modulation"].float().view(1, 2, D) + e.float().view(-1, 1, D)    # (1 oder F, 2, D)
        xg = x.view(mk.shape[0], -1, D)                                                 # je Latent-Bild
        h = torch.addcmul(mk[:, 0:1], F.layer_norm(xg, (D,), eps=self.eps), 1 + mk[:, 1:2]).to(dtype)
        out = _lin(h.view(-1, D), sd["head.head.weight"], sd["head.head.bias"]).float()  # (S, C·p)
        pt, ph, pw = self.patch
        out = out.view(F_, H, W, pt, ph, pw, self.out_dim)
        return out.permute(6, 0, 3, 1, 4, 2, 5).reshape(self.out_dim, F_ * pt, H * ph, W * pw)

    def _bloecke(self, x, e0, ctx, freqs, dtype, cache, zweig):
        """Text-Einbettung und alle Blöcke. Der Residual-Strom läuft wie im Original in float32, nur die
        Eingänge der Matrizen sind bf16; Modulationen wirken je Latent-Bild per Broadcast (Token sind nach
        Bild geordnet). Mit `cache` wird der Residual (Ausgang − Eingang) gemerkt."""
        import torch
        import torch.nn.functional as F
        sd = self.sd
        D = self.dim
        S, G = x.shape[0], e0.shape[0]                    # G = 1 oder Zahl der Latent-Bilder
        x = x.float().view(G, S // G, D)
        eingang = x
        c = torch.zeros(self.text_len, ctx.shape[1], device=x.device, dtype=dtype)
        c[:ctx.shape[0]] = ctx.to(dtype)
        c = _lin(F.gelu(_lin(c, sd["text_embedding.0.weight"], sd["text_embedding.0.bias"]), approximate="tanh"),
                 sd["text_embedding.2.weight"], sd["text_embedding.2.bias"])
        x = self.block_lauf(x, e0, c, freqs, dtype)
        if cache is not None:
            cache.residual[zweig] = (x - eingang).view(S, D)
        return x.view(S, D)

    def block_lauf(self, x, e0, c, freqs, dtype):
        """Alle Blöcke auf x (G, S/G, D) float32 – fast die ganze Rechenzeit (trtmotor ersetzt nur das)."""
        import torch
        lernen = self.lernen and torch.is_grad_enabled()
        for i in range(self.n_layer):
            if lernen:                                   # Training: Zwischenwerte je Block erst rückwärts neu rechnen
                from torch.utils.checkpoint import checkpoint
                x = checkpoint(self._block, i, x, e0, c, freqs, dtype, use_reentrant=False)
            else:
                x = self._block(i, x, e0, c, freqs, dtype)
        return x

    def _block(self, i, x, e0, c, freqs, dtype):
        """Ein DiT-Block auf dem Residual-Strom x (G, S/G, D) in float32."""
        import torch
        import torch.nn.functional as F
        sd = self.sd
        n, D = self.n_head, self.dim
        G = x.shape[0]
        S = G * x.shape[1]
        p = f"blocks.{i}."
        m = (sd[p + "modulation"].float().view(1, 6, D) + e0).unsqueeze(2)       # (G, 6, 1, D)
        h = torch.addcmul(m[:, 0], F.layer_norm(x, (D,), eps=self.eps), 1 + m[:, 1]).to(dtype).view(S, D)
        q, k, v = _lin_mehrere(h, [(sd[p + f"self_attn.{z}.weight"], sd[p + f"self_attn.{z}.bias"])
                                   for z in "qkv"])
        q = _rms_rope(q, sd[p + "self_attn.norm_q.weight"], self.eps, freqs, n)
        k = _rms_rope(k, sd[p + "self_attn.norm_k.weight"], self.eps, freqs, n)
        a = self._attn(q, k, v.view(S, n, D // n))
        y = _lin(a, sd[p + "self_attn.o.weight"], sd[p + "self_attn.o.bias"])
        x = torch.addcmul(x, y.view(G, S // G, D), m[:, 2])
        h = F.layer_norm(x, (D,), sd[p + "norm3.weight"].float(), sd[p + "norm3.bias"].float(),
                         self.eps).to(dtype).view(S, D)
        q = _rms(_lin(h, sd[p + "cross_attn.q.weight"], sd[p + "cross_attn.q.bias"]), sd[p + "cross_attn.norm_q.weight"], self.eps).view(-1, n, D // n)
        k = _rms(_lin(c, sd[p + "cross_attn.k.weight"], sd[p + "cross_attn.k.bias"]), sd[p + "cross_attn.norm_k.weight"], self.eps).view(-1, n, D // n)
        v = _lin(c, sd[p + "cross_attn.v.weight"], sd[p + "cross_attn.v.bias"]).view(-1, n, D // n)
        a = self._attn(q, k, v)
        x = x + _lin(a, sd[p + "cross_attn.o.weight"], sd[p + "cross_attn.o.bias"]).view(G, S // G, D)
        h = torch.addcmul(m[:, 3], F.layer_norm(x, (D,), eps=self.eps), 1 + m[:, 4]).to(dtype).view(S, D)
        h = _lin(F.gelu(_lin(h, sd[p + "ffn.0.weight"], sd[p + "ffn.0.bias"]), approximate="tanh"),
                 sd[p + "ffn.2.weight"], sd[p + "ffn.2.bias"])
        x = torch.addcmul(x, h.view(G, S // G, D), m[:, 5])
        return x


# ===========================================================================
#  Sampler
# ===========================================================================

def _sigmas_ab(steps: int, shift: float, staerke: float):
    """Zeitplan von Rauschstärke `staerke` bis 0 in `steps` Schritten, gleich verteilt wie der volle Plan
    (zurückgerechnet durch die Zeitverschiebung)."""
    import torch
    t0 = staerke / (shift - (shift - 1) * staerke)              # Umkehrung von σ' = s·t / (1 + (s − 1)·t)
    t = torch.linspace(t0, 0.0, steps + 1)[:-1]
    return torch.cat([shift * t / (1 + (shift - 1) * t), torch.zeros(1)])


def _raster(n: float, raster: int) -> int:
    return max(raster, int(round(n / raster)) * raster)


def maske(n_lat: int, lat_h: int, lat_w: int, start: bool, ende: bool, device):
    """(4, n_lat, h, w) für Fun InP: 1 = Latent-Bild bekannt (Startbild = erstes, Endbild = letztes), 0 = zu
    erzeugen; die 4 Kanäle stehen für die 4 Bilder je Latent-Bild und tragen dieselbe Maske."""
    import torch
    m = torch.zeros(4, n_lat, lat_h, lat_w, device=device)
    if start:
        m[:, 0] = 1
    if ende:
        m[:, -1] = 1
    return m


def _sigmas(steps: int, shift: float, start: float = 1.0):
    """Rauschstärken start → 0, zeitverschoben wie bei Wan: σ' = s·σ / (1 + (s − 1)·σ). UniPC beginnt wie das
    Original bei 0,999 (bei σ = 1 wäre log(1 − σ) unendlich)."""
    import torch
    s = torch.linspace(start, 0.0, steps + 1)[:-1]
    s = shift * s / (1 + (shift - 1) * s)
    return torch.cat([s, torch.zeros(1)])


# MagCache (Ma et al., NeurIPS 2025): Betragsverhältnis der Residuals aufeinanderfolgender Schritte, kalibriert
# für Wan 2.2 TI2V 5B bei 50 Schritten, je Schritt abwechselnd mit Text / Negativ. Werte aus dem Original
# (github.com/Zehong-Ma/MagCache, MagCache4Wan2.2/magcache_generate.py): Text-Start und Bild-Start.
MAG_T2V = [0.99505, 0.99389, 0.99441, 0.9957, 0.99558, 0.99551, 0.99499, 0.9945, 0.99534, 0.99548, 0.99468,
           0.9946, 0.99463, 0.99458, 0.9946, 0.99453, 0.99408, 0.99404, 0.9945, 0.99441, 0.99409, 0.99398,
           0.99403, 0.99397, 0.99382, 0.99377, 0.99349, 0.99343, 0.99377, 0.99378, 0.9933, 0.99328, 0.99303,
           0.99301, 0.99217, 0.99216, 0.992, 0.99201, 0.99201, 0.99202, 0.99133, 0.99132, 0.99112, 0.9911,
           0.99155, 0.99155, 0.98958, 0.98957, 0.98959, 0.98958, 0.98838, 0.98835, 0.98826, 0.98825, 0.9883,
           0.98828, 0.98711, 0.98709, 0.98562, 0.98561, 0.98511, 0.9851, 0.98414, 0.98412, 0.98284, 0.98282,
           0.98104, 0.98101, 0.97981, 0.97979, 0.97849, 0.97849, 0.97557, 0.97554, 0.97398, 0.97395, 0.97171,
           0.97166, 0.96917, 0.96913, 0.96511, 0.96507, 0.96263, 0.96257, 0.95839, 0.95835, 0.95483, 0.95475,
           0.94942, 0.94936, 0.9468, 0.94678, 0.94583, 0.94594, 0.94843, 0.94872, 0.96949, 0.97015]
MAG_I2V = [0.99512, 0.99559, 0.99559, 0.99561, 0.99595, 0.99577, 0.99512, 0.99512, 0.99546, 0.99534, 0.99543,
           0.99531, 0.99496, 0.99491, 0.99504, 0.99499, 0.99444, 0.99449, 0.99481, 0.99481, 0.99435, 0.99435,
           0.9943, 0.99431, 0.99411, 0.99406, 0.99373, 0.99376, 0.99413, 0.99405, 0.99363, 0.99359, 0.99335,
           0.99331, 0.99244, 0.99243, 0.99229, 0.99229, 0.99239, 0.99236, 0.99163, 0.9916, 0.99149, 0.99151,
           0.99191, 0.99192, 0.9898, 0.98981, 0.9899, 0.98987, 0.98849, 0.98849, 0.98846, 0.98846, 0.98861,
           0.98861, 0.9874, 0.98738, 0.98588, 0.98589, 0.98539, 0.98534, 0.98444, 0.98439, 0.9831, 0.98309,
           0.98119, 0.98118, 0.98001, 0.98, 0.97862, 0.97859, 0.97555, 0.97558, 0.97392, 0.97388, 0.97152,
           0.97145, 0.96871, 0.9687, 0.96435, 0.96434, 0.96129, 0.96127, 0.95639, 0.95638, 0.95176, 0.95175,
           0.94446, 0.94452, 0.93972, 0.93974, 0.93575, 0.9359, 0.93537, 0.93552, 0.96655, 0.96616]
MAG_MIN_SCHRITTE = 10
MAG_VORGABE = {"schwelle": 0.06, "k": 2, "behalten": 0.2}       # Vorgabe des Originals für TI2V 5B (E006K2R02)


def _naechste(werte, n: int) -> list[float]:
    """Nächster-Nachbar-Umrechnung einer Liste auf n Einträge (wie das Original, mit numpy-Rundung)."""
    import numpy as np
    werte = np.asarray(werte, dtype=np.float64)
    if n == 1:
        return [float(werte[-1])]
    idx = np.round(np.arange(n) * (len(werte) - 1) / (n - 1)).astype(int)
    return [float(v) for v in werte[idx]]


class MagCache:
    """Überspringt die DiT-Blöcke, solange sich der Residual kaum ändert: je Zweig wird das Produkt der
    Betragsverhältnisse seit dem letzten vollen Durchlauf gebildet und |1 − Produkt| aufsummiert. Unter
    `schwelle` und höchstens `k` Schritte in Folge kommt der gemerkte Residual zum Einsatz. Die ersten
    `behalten` (Anteil aller Durchläufe) rechnen immer voll – dort entsteht das Grundbild."""

    def __init__(self, verhaeltnisse, steps: int, schwelle: float = 0.06, k: int = 2, behalten: float = 0.2):
        r = [1.0, 1.0] + list(verhaeltnisse)
        self.verh = [_naechste(r[z::2], steps) for z in (0, 1)]
        self.schwelle, self.k = schwelle, k
        self.voll_bis = int(2 * steps * behalten)                 # Zählung über beide Zweige wie im Original
        self.residual = [None, None]
        self._prod, self._fehler, self._folge = [1.0, 1.0], [0.0, 0.0], [0, 0]
        self.gespart = 0

    def ueberspringen(self, schritt: int, zweig: int) -> bool:
        if 2 * schritt + zweig < self.voll_bis or self.residual[zweig] is None:
            return False
        self._prod[zweig] *= self.verh[zweig][schritt]
        self._folge[zweig] += 1
        self._fehler[zweig] += abs(1.0 - self._prod[zweig])
        if self._fehler[zweig] < self.schwelle and self._folge[zweig] <= self.k:
            self.gespart += 1
            return True
        self._prod[zweig], self._fehler[zweig], self._folge[zweig] = 1.0, 0.0, 0
        return False


class UniPC:
    """UniPC-Löser für Flow-Matching (Ordnung 2, B(h) = e^h − 1, mit Korrektor), nachgebaut nach Wans
    FlowUniPCMultistepScheduler (predict_x0, lower_order_final). Er rechnet mit der Schätzung des fertigen
    Latents x0 = x − σ·v, merkt sich die vorige und legt durch beide eine Kurve statt einer Geraden (Prädiktor);
    der Korrektor bessert den letzten Schritt mit der neuen Schätzung nach. Dadurch reichen weniger Schritte."""

    def __init__(self, sigmas, ordnung: int = 2):
        self.s = [float(v) for v in sigmas]                   # n + 1 Werte, der letzte ist 0
        self.n = len(self.s) - 1
        self.ordnung = ordnung
        self.m: list = []                                      # letzte x0-Schätzungen
        self.vorher = None                                     # x vor dem letzten Prädiktor
        self.p = 1                                             # Ordnung des letzten Prädiktors
        self.k = 0

    @staticmethod
    def _lam(s: float) -> float:
        """λ = log(α) − log(σ) mit α = 1 − σ (Flow-Matching)."""
        if s <= 0:
            return math.inf
        return math.log(1 - s) - math.log(s)

    def _phi(self, s_t: float, s_0: float):
        h = self._lam(s_t) - self._lam(s_0)
        hh = -h
        h_phi_1 = math.expm1(hh)
        return h, hh, h_phi_1, h_phi_1                         # B(h) = e^h − 1 (bh2) = h·φ1

    def schritt(self, x, v):
        """x bei σ_k und Modell-Ausgabe v -> x bei σ_{k+1}."""
        k = self.k
        m_t = x - self.s[k] * v
        if k > 0 and self.vorher is not None:
            x = self._korrektor(m_t, x)
        self.m = (self.m + [m_t])[-self.ordnung:]
        self.p = min(self.ordnung, self.n - k, len(self.m))  # zum Ende und am Anfang niedriger
        self.vorher = x
        neu = self._praediktor(x, self.p)
        self.k += 1
        return neu

    def _praediktor(self, x, p: int):
        k = self.k
        s_t, s_0 = self.s[k + 1], self.s[k]
        h, _, h_phi_1, B_h = self._phi(s_t, s_0)
        m0 = self.m[-1]
        x_t = (s_t / s_0) * x - (1 - s_t) * h_phi_1 * m0
        if p == 2:
            rk = (self._lam(self.s[k - 1]) - self._lam(s_0)) / h
            x_t = x_t - (1 - s_t) * B_h * 0.5 * (self.m[-2] - m0) / rk
        return x_t

    def _korrektor(self, m_t, x_t):
        import numpy as np
        k, p = self.k, self.p
        s_t, s_0 = self.s[k], self.s[k - 1]
        h, hh, h_phi_1, B_h = self._phi(s_t, s_0)
        m0 = self.m[-1]
        rks, D1s = [], []
        for i in range(1, p):
            rk = (self._lam(self.s[k - (i + 1)]) - self._lam(s_0)) / h
            rks.append(rk)
            D1s.append((self.m[-(i + 1)] - m0) / rk)
        rks.append(1.0)
        R, b = [], []
        h_phi_k, fakultaet = h_phi_1 / hh - 1, 1
        for i in range(1, p + 1):
            R.append([r ** (i - 1) for r in rks])
            b.append(h_phi_k * fakultaet / B_h)
            fakultaet *= i + 1
            h_phi_k = h_phi_k / hh - 1 / fakultaet
        rhos = [0.5] if p == 1 else list(np.linalg.solve(np.array(R), np.array(b)))
        korrektur = sum(r * d for r, d in zip(rhos[:-1], D1s)) if D1s else 0
        return ((s_t / s_0) * self.vorher - (1 - s_t) * h_phi_1 * m0
                - (1 - s_t) * B_h * (korrektur + rhos[-1] * (m_t - m0)))


def _sample(dit, x, ctx, ctx_neg, sigmas, cfg: float, seed: int, on_status=None, abbruch=None, fest=None,
            cache: MagCache | None = None, y=None, loeser: str = "euler", cfg_bis: float = 1.0, x_start=None):
    """Euler oder UniPC über die Sigmas mit CFG. `fest` (C, 1, h, w): das erste Latent-Bild bleibt das Startbild
    – es bekommt Zeitschritt 0 und wird nach jedem Schritt wieder eingesetzt. `cache`: MagCache.
    `y`: Bedingung für Fun InP (Maske + Latent), geht unverändert in jeden Durchlauf.
    `cfg_bis`: Anteil der Schritte mit Negativ-Durchlauf (CFG); danach nur noch der Durchlauf mit Text.
    `x_start`: vorhandenes Latent (Nachschliff) – es bekommt Rauschen der ersten Stärke statt reinem Rauschen."""
    import torch
    gen = torch.Generator(device=x.device).manual_seed(seed)
    x = torch.randn(x.shape, generator=gen, device=x.device, dtype=torch.float32)
    if x_start is not None:
        s0 = float(sigmas[0])
        x = (1 - s0) * x_start.float() + s0 * x
    if fest is not None:
        x[:, :1] = fest
    n = len(sigmas) - 1
    up = UniPC(sigmas) if loeser == "unipc" else None
    cfg_schritte = math.ceil(n * max(0.0, min(1.0, cfg_bis)))
    beginn = time.time()
    for i in range(n):
        if abbruch is not None and abbruch():
            raise RuntimeError("abgebrochen")
        s, s_next = float(sigmas[i]), float(sigmas[i + 1])
        t = s * 1000.0
        if fest is not None:
            t = torch.full((x.shape[1],), s * 1000.0, device=x.device)
            t[0] = 0.0
        def extra(z, i=i):
            kw = {"cache": cache, "schritt": i, "zweig": z} if cache is not None else {}
            return {**kw, "y": y} if y is not None else kw
        with torch.no_grad():
            v = dit(x, t, ctx, **extra(0))
            if cfg != 1.0 and ctx_neg is not None and i < cfg_schritte:
                v_neg = dit(x, t, ctx_neg, **extra(1))
                v = v_neg + cfg * (v - v_neg)
        x = up.schritt(x, v) if up is not None else x + (s_next - s) * v
        if fest is not None:
            x[:, :1] = fest
        if on_status:
            bisher = time.time() - beginn
            on_status(f"Video: Schritt {i + 1}/{n} · {bisher / (i + 1):.1f} s je Schritt · seit Beginn "
                      f"{_uhr(bisher)} · noch etwa {_uhr(bisher / (i + 1) * (n - i - 1))}")
    return x


def _uhr(sek: float) -> str:
    """Sekunden als m:ss."""
    s = int(round(sek))
    return f"{s // 60}:{s % 60:02d}"


# ===========================================================================
#  Speicher: jedes Teil nur während seines Schritts im VRAM
# ===========================================================================

_LOCK = threading.RLock()
_RAM: dict = {}                         # art -> (pfad, objekt) im Arbeitsspeicher
_TIMER: threading.Timer | None = None


def _frei():
    import gc
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()


def _kopie(obj, device, pin: bool = False):
    import torch
    if isinstance(obj, torch.Tensor):
        if device == "cpu":
            t = obj.to("cpu")
            return t.pin_memory() if pin and torch.cuda.is_available() else t
        return obj.to(device, non_blocking=True)
    if isinstance(obj, dict):
        return {k: _kopie(v, device, pin) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_kopie(v, device, pin) for v in obj)
    if isinstance(obj, _Q):
        return _Q(_kopie(obj.raw, device, pin), obj.ggml_type, obj.n_out, obj.n_in)
    return obj


def _trt():
    """trtmotor.video, wenn TensorRT-RTX abgelegt ist und Config video_trt nicht aus – sonst None."""
    try:
        import torch
        if not _cfg("video_trt", True) or not torch.cuda.is_available():
            return None
        from trtmotor import laufzeit, video as tv
        return tv if laufzeit.bereit() else None
    except Exception:
        return None


def _trt_lauf(dit, rechnen):
    """block_lauf über TensorRT (rechnet je Latent-Bild, die Zahl kennt der Aufruf von `dit`)."""
    return lambda x, e0, c, freqs, dtype: rechnen(x, e0, c, freqs, dit._bilder)


def _trt_nach_video(t: dict, cfg: dict, lora) -> None:
    """Gewichte des TensorRT-Netzes aus dem VRAM; ist die Einmessung fertig, im Hintergrund bauen."""
    tv = _trt() if lora is None else None
    if tv is None:
        return
    try:
        tv.nach_video(t["dit"])
        ram = _RAM.get("dit:" + t["name"])
        if ram is not None and ram[0] == t["dit"]:
            tv.vorbereiten(t["dit"], cfg, ram[1])
    except Exception as exc:
        _DBG.ereignis(f"TensorRT-Wan: {exc}")


def _auf_gpu(art: str, pfad: Path, bauen, device):
    """Arbeitskopie für die Karte: aus dem RAM, beim ersten Mal von der Platte (bleibt im RAM)."""
    eintrag = _RAM.get(art)
    if eintrag is not None and eintrag[0] == pfad:
        _DBG.ereignis(f"Videomotor: {art} aus dem RAM auf die GPU")
        return _kopie(eintrag[1], device)
    _RAM.pop(art, None)
    _DBG.ereignis(f"Videomotor: {art} von der Platte")
    obj = bauen()
    _RAM[art] = (pfad, _kopie(obj, "cpu", pin=True))
    return _kopie(obj, device)


def im_ram() -> list[str]:
    return sorted(_RAM)


def entladen() -> None:
    """Gibt auch die RAM-Kopien frei; das nächste Video lädt wieder von der Platte."""
    global _TIMER
    with _LOCK:
        if _TIMER is not None:
            _TIMER.cancel()
            _TIMER = None
        _RAM.clear()
        try:
            from trtmotor import video as _tv
            _tv.entladen()
        except Exception:
            pass
        _frei()
    _DBG.ereignis("Videomotor entladen")


def _uhr_neu() -> None:
    global _TIMER
    if _TIMER is not None:
        _TIMER.cancel()
    sek = float(_cfg("video_entladen", DEFAULTS["entladen_nach"]))
    if sek <= 0:
        _TIMER = None
        entladen()
        return
    _TIMER = threading.Timer(sek, entladen)
    _TIMER.daemon = True
    _TIMER.start()


# ===========================================================================
#  Ein Video
# ===========================================================================

def _bild_laden(pfad, W: int, H: int):
    """Bild -> (3, H, W) in [-1, 1], auf die Videogröße gebracht (Beschnitt statt Verzerrung)."""
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    with Image.open(pfad) as im:
        im = ImageOps.fit(im.convert("RGB"), (W, H), Image.LANCZOS)
        arr = np.asarray(im).astype("float32") / 127.5 - 1.0
    return torch.from_numpy(arr).permute(2, 0, 1)


def _mp4_weg() -> str | None:
    """Womit das MP4 entsteht: "windows" (Media Foundation, videodatei.py), sonst "opencv", falls noch
    installiert – None: keiner da. Wird VOR dem Rechnen geprüft, damit kein fertiges Video verloren geht."""
    import importlib.util
    import videodatei
    if videodatei.bereit():
        return "windows"
    return "opencv" if importlib.util.find_spec("cv2") is not None else None


def _mp4(bilder, pfad: Path, fps: int, rate: float = 1.0) -> None:
    """bilder (T, H, W, 3) uint8 RGB -> MP4: H.264 über Windows Media Foundation; nur wenn das fehlt,
    OpenCV mit „mp4v“ (MPEG-4 Part 2, kein H.264)."""
    if _mp4_weg() == "windows":
        import videodatei
        videodatei.schreiben(bilder, pfad, fps, rate)
        return
    import cv2
    pfad.parent.mkdir(parents=True, exist_ok=True)
    T, H, W, _ = bilder.shape
    w = cv2.VideoWriter(str(pfad), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    try:
        for b in bilder:
            w.write(cv2.cvtColor(b, cv2.COLOR_RGB2BGR))
    finally:
        w.release()


def ausgeben(frames, out: Path, fps: int, schaerfe: str | None = None, on_status=None) -> str:
    """Fertige Bilder speichern: MP4 (geschärft nach Stufe, mit etwas mehr Datenrate) und das letzte Bild
    UNgeschärft als .png – es wird Startbild einer Fortsetzung, dort soll sich nichts aufschaukeln."""
    from PIL import Image

    import schaerfe as _S
    art = _S.stufe(schaerfe) or _S.stufe(_cfg("video_schaerfe", "leicht")) or "aus"
    if art != "aus" and on_status:
        on_status(f"Video: schärfe ({art}) …")
    _mp4(_S.schaerfen(frames, art), out, fps, _S.DATENRATE[art])
    Image.fromarray(frames[-1]).save(out.with_suffix(".png"))          # letztes Bild: Startbild fürs nächste
    return art


def _geraet():
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return device, (torch.bfloat16 if device.type == "cuda" else torch.float32)


def _clip(prompt, negativ, start, t, steps, cfg, shift, W, H, seed, device, dtype, on_status, abbruch,
          kontext_cache: dict, fam: dict, magcache: float = 0.0, ende=None, loeser: str = "euler",
          cfg_bis: float = 1.0, vorstufe: float = 0.0, nachschliff: float = 0.6, nachschliff_schritte: int = 0,
          lora: Path | None = None, lora_staerke: float = 1.0):
    """Ein Clip mit fam["bilder"] Bildern -> (T, H, W, 3) uint8. Mit `vorstufe` (z. B. 0.5) wird erst in
    kleinerer Größe gerechnet, das Latent auf volle Größe gezogen und mit Rauschstärke `nachschliff` in
    `nachschliff_schritte` Schritten vom selben Modell in voller Größe fertig gerechnet (weniger Bildteile in
    den vielen Schritten, Details im kurzen Nachschliff)."""
    import torch
    bilder_n, teiler = fam["bilder"], fam["teiler"]
    n_lat = 1 + (bilder_n - 1) // 4

    # 1) Text (einmal je Video – der Kontext bleibt über alle Clips gleich)
    if "ctx" not in kontext_cache:
        if on_status:
            on_status("Video: Text lesen (umT5) …")
        tok = _tokenizer()
        sd = _auf_gpu("text", t["text"], lambda: _lade_gguf(t["text"], dtype), device)
        enc = UmT5(sd)
        with torch.no_grad():
            kontext_cache["ctx"] = enc(tok(prompt), dtype).float().cpu()
            kontext_cache["neg"] = enc(tok(negativ), dtype).float().cpu() if cfg != 1.0 else None
        del sd, enc
        _frei()
    ctx = kontext_cache["ctx"].to(device)
    ctx_neg = kontext_cache["neg"].to(device) if kontext_cache["neg"] is not None else None

    # 2) Bedingung je Größe: TI2V = Startbild als festes erstes Latent-Bild; Fun InP = Start-/Endbild + Maske
    vae_art = "vae:" + str(t["vae"])
    groessen = [(W, H)]
    if vorstufe > 0 and _tae_datei(t, "super") is None and _tae_datei(t, "klein") is None:
        vorstufe = 0.0                                   # ohne Mini-VAE kein schnelles Umpacken
    if vorstufe > 0:
        groessen.insert(0, (_raster(W * vorstufe, teiler * 2), _raster(H * vorstufe, teiler * 2)))
    bedingungen = {g: (None, None) for g in groessen}
    if start is not None or (ende is not None and fam["bedingung"] == "maske"):
        if on_status:
            on_status("Video: Start-/Endbild packen (VAE) …")
        vae = WanVAE22(_auf_gpu(vae_art, t["vae"], lambda: _lade_safetensors(t["vae"], dtype), device))
        for gw, gh in groessen:
            bedingungen[(gw, gh)] = _bedingung(vae, fam, start, ende, gw, gh, bilder_n, n_lat, teiler, device)
        del vae
        _frei()

    # 3) DiT (GGUF oder safetensors + .json)
    if on_status:
        on_status("Video: Modell laden …")
    cfg_json = dit_config(t)
    laden = _lade_gguf_dit if t.get("gguf") else _lade_safetensors
    tv = _trt() if lora is None else None             # mit Charakter-LoRA rechnet torch
    ram = _RAM.get("dit:" + t["name"])
    rechnen = tv.bloecke(t["dit"]) if tv is not None and ram is not None and ram[0] == t["dit"] else None
    if rechnen is not None:                           # TensorRT: nur die kleinen Teile auf die Karte
        dit_sd = _kopie({k: v for k, v in ram[1].items() if not k.startswith("blocks.")}, device)
        _DBG.ereignis("Videomotor: Blöcke über TensorRT")
    else:
        dit_sd = _auf_gpu("dit:" + t["name"], t["dit"], lambda: laden(t["dit"], dtype), device)
    fp8 = _cfg("video_fp8", True) and _fp8_bereit(device) and rechnen is None
    if lora is not None:                                 # Charakter-LoRA vor FP8 einmischen: danach gleich schnell
        if on_status:
            on_status("Video: Charakter-LoRA einmischen …")
        n, grund = _lora_einmischen(dit_sd, lora, lora_staerke, dtype, fp8, familie(cfg_json))
        if grund:
            HINWEISE.append(f"Charakter-LoRA {Path(lora).name} nicht eingemischt: {grund} – Video ohne LoRA")
            _DBG.ereignis(f"Videomotor: LoRA {Path(lora).name} NICHT eingemischt – {grund}")
            if on_status:
                on_status("Video: Charakter-LoRA passt nicht zu diesem Modell – weiter ohne LoRA")
        else:
            herkunft = lora_stand_familie(lora)
            GENUTZT.append(f"Charakter-LoRA {Path(lora).name} (Stärke {lora_staerke:g}"
                           + (f", trainiert für {herkunft}, hier auf {familie(cfg_json)}"
                              if herkunft and herkunft != familie(cfg_json) else "") + ")")
            _DBG.ereignis(f"Videomotor: LoRA {Path(lora).name} in {n} Matrizen")
    if fp8:
        if on_status:
            on_status("Video: Modell für die FP8-Kerne auspacken …")
        _DBG.ereignis(f"Videomotor: {_als_fp8(dit_sd, dtype)} Matrizen als FP8")
        _frei()
    dit = WanDiT(dit_sd, cfg_json)
    if rechnen is not None:
        dit.block_lauf = _trt_lauf(dit, rechnen)
    elif tv is not None:
        try:
            tv.einmessen(t["dit"], dit_sd, dit.n_layer)
        except Exception as exc:
            _DBG.ereignis(f"TensorRT-Einmessung übersprungen: {exc}")
    kanaele = int(cfg_json.get("out_dim", 48))
    (gw, gh), lat = groessen[0], None
    fest, y = bedingungen[(gw, gh)]
    cache = (MagCache(MAG_I2V if fest is not None or y is not None else MAG_T2V, steps, magcache, MAG_VORGABE["k"],
                      MAG_VORGABE["behalten"]) if magcache > 0 else None)
    x = torch.zeros(kanaele, n_lat, gh // teiler, gw // teiler, device=device)
    sigmas = _sigmas(steps, shift, 0.999 if loeser == "unipc" else 1.0)
    vor = ((lambda m: on_status(m.replace("Video:", f"Video: Vorstufe {gw}x{gh} ·"))) if on_status and vorstufe > 0
           else on_status)
    lat = _sample(dit, x, ctx, ctx_neg, sigmas, cfg, seed, vor, abbruch, fest=fest,
                  cache=cache, y=y, loeser=loeser, cfg_bis=cfg_bis)
    if cache is not None:
        _DBG.ereignis(f"Videomotor: MagCache sparte {cache.gespart} von {steps * (2 if ctx_neg is not None else 1)} "
                      "DiT-Durchläufen")
    if vorstufe > 0:                                     # Bilder groß ziehen, wieder einpacken, nachschleifen
        import torch.nn.functional as F
        tae_p = _tae_datei(t, "super") or _tae_datei(t, "klein")
        tae_v = TaeW22(_auf_gpu("tae:" + str(tae_p), tae_p, lambda: _lade_safetensors(tae_p, dtype), device))
        klein = tae_v.decode(lat)                                               # (3, T, h, w) im RAM
        teile_ = []
        for a in range(0, klein.shape[1], 16):
            stueck = klein[:, a:a + 16].to(device).permute(1, 0, 2, 3)
            teile_.append(F.interpolate(stueck, size=(H, W), mode="bicubic", align_corners=False,
                                        antialias=True).clamp(-1, 1).permute(1, 0, 2, 3))
        gross = tae_v.encode(torch.cat(teile_, 1))
        del klein, teile_, tae_v
        fest, y = bedingungen[(W, H)]
        n_r = nachschliff_schritte or max(2, round(steps * nachschliff / 2))
        nach = (lambda m: on_status(m.replace("Video:", "Video: Nachschliff ·"))) if on_status else None
        lat = _sample(dit, gross, ctx, ctx_neg, _sigmas_ab(n_r, shift, nachschliff), cfg, seed + 1, nach, abbruch,
                      fest=fest, y=y, loeser=loeser, cfg_bis=cfg_bis, x_start=gross)
        del gross
    del dit_sd, dit, ctx, ctx_neg, fest, cache, y, bedingungen
    _trt_nach_video(t, cfg_json, lora)
    _frei()

    # 4) Auspacken: Mini-VAE (Vorgabe) oder die große VAE in Kacheln
    tae = _tae_datei(t, str(_cfg("video_vae", "super")))
    if tae is not None:
        vae_sd = _auf_gpu("tae:" + str(tae), tae, lambda: _lade_safetensors(tae, dtype), device)
        out = TaeW22(vae_sd).decode(lat, on_status)
    else:
        vae_sd = _auf_gpu(vae_art, t["vae"], lambda: _lade_safetensors(t["vae"], dtype), device)
        with torch.no_grad():
            out = WanVAE22(vae_sd).decode_kacheln(lat, on_status, int(_cfg("video_vae_kachel", 32)))
    del vae_sd, lat
    _frei()
    return ((out.permute(1, 2, 3, 0).cpu().numpy() + 1.0) * 127.5).round().clip(0, 255).astype("uint8")


# Bauarten mit demselben Rückgrat (Wan 2.2 5B): Fun InP ist aus TI2V nachtrainiert, die Block-Matrizen sind gleich
# groß – eine Charakter-LoRA der einen wirkt auch auf der anderen.
VERWANDT = (frozenset({"wan22-ti2v", "wan22-fun-inp"}),)


def lora_passt_zu(lora_familie: str, familie_jetzt: str) -> bool:
    if not lora_familie or not familie_jetzt or lora_familie == familie_jetzt:
        return True
    return any({lora_familie, familie_jetzt} <= gruppe for gruppe in VERWANDT)


def _lora_einmischen(sd: dict, pfad: Path, staerke: float, dtype, fp8: bool, familie_jetzt: str = ""
                     ) -> tuple[int, str]:
    """Matrix für Matrix einmischen und gleich nach FP8 – so liegt nie das ganze Modell dicht auf der Karte.
    Vorher geprüft: Bauart (aus den Metadaten) und Form jeder Matrix. Passt etwas nicht, bleibt das Modell
    unverändert: (0, Grund)."""
    import lora as _L
    teile_lora, meta = _L.laden(pfad)
    if not lora_passt_zu(str(meta.get("familie") or ""), familie_jetzt):
        return 0, f"für {meta['familie']} trainiert, das Modell ist {familie_jetzt}"
    if (grund := _L.passt(sd, teile_lora)):
        return 0, grund
    n = 0
    for name in list(teile_lora):
        n += _L.einmischen(sd, {name: teile_lora.pop(name)}, staerke, dtype)
        if fp8 and name in sd:
            einzeln = {name: sd[name]}
            _als_fp8(einzeln, dtype)
            sd[name] = einzeln[name]
    return n, ""


def lora_stand_familie(pfad) -> str:
    """Bauart, für die eine LoRA trainiert wurde (Metadaten), sonst ''."""
    try:
        from safetensors import safe_open
        with safe_open(str(pfad), framework="pt") as f:
            return str((f.metadata() or {}).get("familie") or "")
    except Exception:
        return ""


def lora_stand(name: str | None = None) -> dict:
    """Charakter-LoRA einer Persönlichkeit (Vorgabe: die aktive): da, an (video_lora), pfad, beschreibung
    (Satz aus dem Training), turbo (das gewählte Video-Modell braucht ein Startbild). Ohne Grafikkarte."""
    jetzt = time.time()
    if _LORA_STAND["name"] == name and jetzt - _LORA_STAND["zeit"] < LORA_STAND_S:
        return dict(_LORA_STAND["stand"])
    stand = {"da": False, "an": bool(_cfg("video_lora", True)), "pfad": "", "beschreibung": "", "turbo": False,
             "familie": "", "modell_familie": "", "passt": True}
    try:
        import profilordner
        p = profilordner.ordner("LoRA", name, anlegen=False) / "wan22-5b.safetensors"
        if p.is_file():
            stand.update(da=True, pfad=str(p))
            from safetensors import safe_open
            with safe_open(str(p), framework="pt") as f:
                meta = f.metadata() or {}
            stand["beschreibung"] = str(meta.get("beschreibung") or "")
            stand["familie"] = str(meta.get("familie") or "")
    except Exception:
        pass
    try:
        t = teile()
        stand["turbo"] = bool(t.get("dit")) and "turbo" in Path(t["dit"]).name.lower()
        stand["modell_familie"] = familie(dit_config(t))
    except Exception:
        pass
    stand["passt"] = lora_passt_zu(stand["familie"], stand["modell_familie"])
    _LORA_STAND.update(zeit=jetzt, name=name, stand=dict(stand))
    return stand


def _lora_fuer(lora) -> Path | None:
    """Welche LoRA ins Modell kommt: die ausdrücklich genannte, sonst die Charakter-LoRA der aktiven
    Persönlichkeit (Profile/<Name>/LoRA/wan22-5b.safetensors), wenn `video_lora` an ist."""
    if lora:
        p = Path(lora)
        if not p.is_file():
            raise RuntimeError(f"LoRA-Datei nicht gefunden: {p}")
        return p
    if not _cfg("video_lora", True):
        return None
    try:
        import profilordner
        p = profilordner.lora_pfad("wan22-5b")
        return p if p.is_file() else None
    except Exception:
        return None


def _bedingung(vae, fam: dict, start, ende, W: int, H: int, bilder_n: int, n_lat: int, teiler: int, device):
    """(fest, y) für eine Größe: TI2V packt das Startbild als erstes Latent-Bild, Fun InP Start-/Endbild samt
    Maske. Start/Ende (3, H0, W0) werden dafür auf W×H gebracht."""
    import torch
    import torch.nn.functional as F

    def passend(bild):
        if bild is None or tuple(bild.shape[1:]) == (H, W):
            return None if bild is None else bild.to(device)
        return F.interpolate(bild[None].to(device), size=(H, W), mode="bicubic", align_corners=False,
                             antialias=True)[0].clamp(-1, 1)
    start, ende = passend(start), passend(ende)
    with torch.no_grad():
        if fam["bedingung"] == "maske":
            video = torch.zeros(3, bilder_n, H, W, device=device)              # 0 = Grau: zu erzeugen
            if start is not None:
                video[:, 0] = start
            if ende is not None:
                video[:, -1] = ende
            bed = vae.encode(video)                                              # (48, n_lat, h, w)
            m = maske(n_lat, H // teiler, W // teiler, start is not None, ende is not None, device)
            return None, torch.cat([m, bed], 0)
        return (vae.encode(start[:, None]) if start is not None else None), None


def generate(prompt: str, *, sekunden: float = 5.0, startbild: str | None = None, negativ: str | None = None,
             steps: int | None = None, cfg: float | None = None, shift: float | None = None,
             size: tuple[int, int] | None = None, seed: int | None = None, modell: str | None = None,
             magcache: float | None = None, schluesselbilder: list[str] | None = None,
             loeser: str | None = None, cfg_bis: float | None = None, vorstufe: float | None = None,
             lora: str | Path | None = None, schaerfe: str | None = None,
             on_status=None,
             abbruch=None) -> str:
    """Erzeugt ein Video (bis MAX_SEKUNDEN) und gibt den MP4-Pfad zurück. Blockiert.
    Clips (5 s) hängen aneinander: das letzte Bild wird das Startbild des nächsten.
    `magcache`: Fehlerschwelle für MagCache (Config `video_magcache`, 0 = aus).
    `schluesselbilder`: ein Bild je Clip-Ende (alle 5 s). Dann rechnet ein Modell mit Start- und Endbild
    (Fun InP; ist ein anderes gewählt, wird ein vorhandenes Fun-InP-Modell genommen), und jeder Clip startet
    am Schlüsselbild des vorigen – die Figur kann nicht von Clip zu Clip wegdriften."""
    grund = missing_reason(modell)
    if grund:
        raise RuntimeError(grund)
    if _mp4_weg() is None:
        raise RuntimeError("Kein Weg, ein MP4 zu schreiben: Windows Media Foundation fehlt (Windows-N-Ausgabe? "
                           "Media Feature Pack installieren).")
    import numpy as np
    import torch
    from PIL import Image

    HINWEISE.clear()
    GENUTZT.clear()
    ziele = [str(p) for p in (schluesselbilder or []) if p]
    t = teile(modell)
    fam = FAMILIEN[familie(dit_config(t))]
    if ziele and fam["bedingung"] != "maske":
        vorher = t.get("name")
        modell = _modell_mit_endbild()
        if modell is None:
            raise RuntimeError("Schlüsselbilder brauchen ein Video-Modell mit Start- und Endbild (Wan 2.2 Fun InP) "
                               f"als Unterordner in {video_dir()}")
        t = teile(modell)
        fam = FAMILIEN[familie(dit_config(t))]
        HINWEISE.append(f"Mit Schlüsselbildern rechnet dieses Video mit {modell} (Start- und Endbild) statt mit "
                        f"{vorher} – die Einstellung bleibt {vorher}")
        _DBG.ereignis(f"Videomotor: Schlüsselbilder – {modell} statt {vorher}")
    elif not ziele and not startbild and fam["bedingung"] == "maske":
        # Fun InP rechnet nur von einem Bild aus – ohne Startbild ein Modell, das aus Text beginnt
        vorher = t.get("name")
        modell = _modell_ohne_endbild()
        if modell is None:
            raise RuntimeError(f"{vorher} braucht ein Startbild (startbild) – ein Video nur aus Text kann es nicht.")
        t = teile(modell)
        fam = FAMILIEN[familie(dit_config(t))]
        HINWEISE.append(f"Ohne Startbild rechnet dieses Video mit {modell} statt mit {vorher} (das braucht ein "
                        f"Startbild) – die Einstellung bleibt {vorher}")
        _DBG.ereignis(f"Videomotor: ohne Startbild – {modell} statt {vorher}")
    fps, bilder_n = fam["fps"], fam["bilder"]
    vorg = t.get("vorgaben") or {}                   # Vorgaben des Modells vor den allgemeinen Einstellungen
    steps = int(steps or vorg.get("steps") or _cfg("video_schritte", DEFAULTS["steps"]))
    cfg = float(vorg.get("cfg", fam["cfg"]) if cfg is None else cfg)
    shift = float(vorg.get("shift", fam["shift"]) if shift is None else shift)
    eigen = str(_cfg("video_groesse", "")).lower()
    W, H = size or (tuple(int(v) for v in eigen.split("x")) if "x" in eigen else fam["size"])
    raster = fam["teiler"] * 2
    W, H = (max(int(W), 128) // raster) * raster, (max(int(H), 128) // raster) * raster
    sekunden = min(max(float(sekunden), 1.0), MAX_SEKUNDEN)
    max_clips = max(1, math.ceil((MAX_SEKUNDEN * fps - 1) / (bilder_n - 1)))
    if len(ziele) > max_clips:                       # sonst würden Clips gerechnet und danach abgeschnitten
        raise RuntimeError(f"Höchstens {max_clips} Schlüsselbilder (eines je 5 s, bis {MAX_SEKUNDEN} s) – "
                           f"{len(ziele)} angegeben.")
    n_clips = max(1, math.ceil((sekunden * fps - 1) / (bilder_n - 1)))
    if ziele:
        n_clips = max(n_clips, len(ziele))
        sekunden = max(sekunden, min(MAX_SEKUNDEN, n_clips * (bilder_n - 1) / fps))
    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF
    negativ = NEGATIV if negativ is None else negativ
    magcache = max(0.0, float(_cfg("video_magcache", MAG_VORGABE["schwelle"]) if magcache is None else magcache))
    loeser = str(loeser or vorg.get("loeser") or _cfg("video_loeser", DEFAULTS["loeser"])).lower()
    loeser = loeser if loeser in ("euler", "unipc") else "euler"
    cfg_bis = float(_cfg("video_cfg_bis", DEFAULTS["cfg_bis"]) if cfg_bis is None else cfg_bis)
    vorstufe = float(_cfg("video_vorstufe", DEFAULTS["vorstufe"]) if vorstufe is None else vorstufe)
    nachschliff = float(_cfg("video_nachschliff", DEFAULTS["nachschliff"]))
    nachschliff_schritte = int(_cfg("video_nachschliff_schritte", 0))
    lora = _lora_fuer(lora)
    lora_staerke = float(_cfg("video_lora_staerke", 1.0))
    if steps < MAG_MIN_SCHRITTE:
        magcache = 0.0                                 # kalibriert für viele Schritte; bei Tempo-Fassungen aus
    device, dtype = _geraet()

    fehler = None
    with _LOCK:
        if _TIMER is not None:
            _TIMER.cancel()
        try:
            t0 = time.time()
            start = _bild_laden(startbild, W, H) if startbild else None
            alle = []
            kontext: dict = {}
            for c in range(n_clips):
                if on_status:
                    on_status(f"Video: Clip {c + 1}/{n_clips} …")
                ende = _bild_laden(ziele[c], W, H) if c < len(ziele) else None
                bilder = _clip(prompt, negativ, start, t, steps, cfg, shift, W, H, seed + c, device, dtype,
                               on_status, abbruch, kontext, fam, magcache, ende=ende, loeser=loeser,
                               cfg_bis=cfg_bis, vorstufe=vorstufe, nachschliff=nachschliff,
                               nachschliff_schritte=nachschliff_schritte, lora=lora, lora_staerke=lora_staerke)
                alle.append(bilder if c == 0 else bilder[1:])          # das Startbild gibt es schon
                # weiter am Schlüsselbild selbst (scharf und genau die Figur), sonst am letzten Bild
                start = ende if ende is not None else torch.from_numpy(
                    bilder[-1].astype("float32") / 127.5 - 1.0).permute(2, 0, 1)
            frames = np.concatenate(alle, 0)[:int(round(sekunden * fps))]
            dauer = time.time() - t0
        except Exception as e:                                         # nur Text: kein Tensor am Traceback
            fehler = f"{type(e).__name__}: {e}"
        _frei()
        _uhr_neu()
        if fehler:
            raise RuntimeError(f"Video: {fehler}")

    import profilordner as _profil                   # Profile/<Persönlichkeit>/Videos/<Name>_Datum_Uhrzeit.mp4
    out = _profil.video_pfad(".mp4")
    ausgeben(frames, out, fps, schaerfe, on_status)
    _DBG.ereignis(f"Video fertig ({t['name']}): {len(frames)} Bilder {W}x{H} in {dauer:.0f} s -> {out.name}")
    return str(out)
