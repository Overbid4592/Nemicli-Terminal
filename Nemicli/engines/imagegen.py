"""
imagegen.py - NemiCLIs EIGENE Bild-Pipeline (Stable Diffusion, Tiefe 1).

Die Modell-Gewichte (.safetensors von z.B. Civitai) legst du selbst in den Ordner
`Models/checkpoints/`.  Die komplette PIPELINE-LOGIK hier ist handgeschrieben:

    Checkpoint laden  ->  Prompt in Text-Embeddings  ->  Start-Rauschen
    ->  eigener Euler-Sampling-Loop (mit Classifier-Free-Guidance)
    ->  Latents per VAE in Pixel  ->  PNG speichern.

Die reinen Netz-Bausteine (UNet/VAE/CLIP) kommen aus PyTorch/diffusers - das IST
das Modell. Den Denoise-Loop, den Scheduler, die Guidance und alles drumherum
rechnen wir selbst (siehe `_euler_sigmas`, `generate`).

Torch & Co. werden ERST beim Generieren importiert, damit NemiCLI auch ohne sie
normal startet. Fehlt was, sagt `missing_reason()` freundlich, was zu tun ist.
"""

from __future__ import annotations

import json
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path

# --- Wo Checkpoints liegen und wohin die Bilder kommen ----------------------
try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent      # Projekt-Wurzel
MAX_EDGE = 2048                          # harte Obergrenze je Kante – mehr geht nicht
MODELS_DIR = _ROOT / "Models"
CKPT_DIRS = [MODELS_DIR / "checkpoints", MODELS_DIR]     # hier wird gesucht
OUT_DIR = _ROOT / "Bilder"                               # hier landen die Bilder

# Standard-Werte fürs Generieren
DEFAULTS = {
    "steps": 30,
    "cfg": 6.5,
    "size": (768, 768),             # nur der Rückfallwert, wenn keine Größe kommt
    "sampler": "dpmpp_2m",          # vom Modell empfohlen (besser als simples Euler)
    "karras": True,                 # Karras-Rauschplan: feinere Details
    "adetailer": True,              # Gesicht automatisch nachschärfen (eigener Finder)
    "face_strength": 0.45,          # wie stark das Gesicht neu gemalt wird (0..1)
    # Am Anfang bewusst die festen Motiv-Ausschlüsse (gelten für JEDES Bild, auch
    # bei direktem /bild): nur eine einzelne erwachsene Frau, klar volljährig.
    "neg": "child, kid, children, minor, teen, teenager, underage, young girl, loli, "
           "man, men, male, boy, penis, cock, "
           "group, multiple people, crowd, two people, couple, "
           "lowres, bad anatomy, bad hands, text, error, missing fingers, "
           "extra digit, fewer digits, cropped, worst quality, low quality, "
           "jpeg artifacts, signature, watermark, blurry, "
           "red eyes, glowing eyes, oversaturated, overexposed, high contrast, "
           "plastic skin, oily skin, shiny skin, deep fried, oversharpened, "
           "airbrushed, waxy skin, watermark, logo, signature, text, username",
}


# ===========================================================================
#  Verfügbarkeit  (sind torch / diffusers / transformers da?)
# ===========================================================================

def missing_reason() -> str | None:
    """None, wenn alles installiert ist. Sonst ein kurzer Hinweistext."""
    try:                      # exe-Betrieb: extern installiertes torch einbinden
        import extlibs
        extlibs.enable()
    except Exception:
        pass
    missing = []
    for mod, nice in (("torch", "PyTorch"), ("diffusers", "diffusers"),
                      ("transformers", "transformers"), ("safetensors", "safetensors"),
                      ("PIL", "Pillow")):
        try:
            __import__(mod)
        except Exception:
            missing.append(nice)
    if not missing:
        return None
    tipp = "Tippe /systemcheck – da steht der Befehl, der zu deinem PC passt."
    return ("Es fehlt: " + ", ".join(missing) + ".\n" + tipp)


def available() -> bool:
    return missing_reason() is None


def device_info() -> str:
    """Kurzinfo, ob auf GPU oder CPU gerechnet wird (für die Anzeige)."""
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            return f"GPU · {name} ({vram:.0f} GB)"
        return "CPU (keine CUDA-GPU erkannt – wird langsam)"
    except Exception:
        return "unbekannt"


# ===========================================================================
#  Checkpoints finden  +  SD1.5 / SDXL automatisch erkennen
# ===========================================================================

def _safetensors_keys(path: Path) -> list[str]:
    """Liest NUR den JSON-Kopf einer .safetensors-Datei (die Tensor-Namen),
    ohne die Gewichte zu laden. Format: u64 Kopflänge | JSON-Kopf | Daten."""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > (200 << 20):          # >200 MB Kopf = unplausibel
                return []
            head = json.loads(f.read(n).decode("utf-8", "replace"))
        return [k for k in head.keys() if k != "__metadata__"]
    except Exception:
        return []


def detect_kind(path: Path) -> str:
    """'sdxl', 'sd15' oder 'unknown' - anhand der Tensor-Namen im Checkpoint.
    SDXL hat einen zweiten Text-Encoder (conditioner.embedders.1) bzw. einen
    Klassen-Embedder (label_emb) im UNet; SD1.5 hat das nicht."""
    keys = _safetensors_keys(path)
    if not keys:
        return "unknown"
    blob = "\n".join(keys)
    if ("conditioner.embedders.1" in blob
            or "add_embedding" in blob
            or "label_emb" in blob
            or "text_encoders.clip_g" in blob):
        return "sdxl"
    if ("cond_stage_model" in blob
            or "model.diffusion_model" in blob
            or "down_blocks.0.resnets" in blob):
        return "sd15"
    return "unknown"


@dataclass
class Checkpoint:
    ref: str            # kurzer Name (Dateiname ohne Endung)
    path: Path
    kind: str           # 'sd15' | 'sdxl' | 'unknown'

    @property
    def label(self) -> str:
        tag = {"sdxl": "SDXL", "sd15": "SD 1.5", "unknown": "?"}[self.kind]
        return f"{self.ref}  ({tag})"


def discover() -> dict[str, Checkpoint]:
    """ref -> Checkpoint für alle .safetensors in den CKPT_DIRS (ohne Doppelte)."""
    out: dict[str, Checkpoint] = {}
    seen: set[Path] = set()
    for d in CKPT_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.safetensors")):
            rp = f.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            ref = f.stem
            if ref not in out:                      # erster Fund gewinnt
                out[ref] = Checkpoint(ref=ref, path=f, kind=detect_kind(f))
    return out


def menu() -> dict[str, str]:
    """ref -> label für die Anzeige/Autovervollständigung."""
    return {c.ref: c.label for c in discover().values()}


def default_model() -> str | None:
    """Das per /bildmodel gemerkte Standard-Modell (oder None)."""
    try:
        import config
        return config.load().get("bildmodel") or None
    except Exception:
        return None


def set_default_model(ref: str | None) -> None:
    """Merkt sich das Standard-Modell über Neustarts hinweg. Wählt zugleich
    die EIGENE Pipeline als Bild-Motor (nicht die externe WebUI)."""
    import config
    config.update(bildmodel=ref, bild_backend="builtin")


# --- Welcher Bild-Motor ist aktiv? ------------------------------------------
# Drei Stellen, jede mit eigener Sprache:
#   builtin  eigene SD1.5/SDXL-Pipeline (imagegen.generate)
#   webui    Forge/A1111 ueber /sdapi/v1/...   (sdwebui.py)
#   comfy    ComfyUI ueber /prompt + /history  (comfyui.py)
# Eine externe Stelle gilt nur, wenn sie ausdruecklich gewaehlt wurde UND
# gerade laeuft - sonst faellt es auf die eigene Pipeline zurueck.
_EXTERN = {"webui": "sdwebui", "comfy": "comfyui"}


def _extern_modul(key: str):
    import importlib
    return importlib.import_module(_EXTERN[key])


def backend() -> str:
    """Der Motor, der JETZT malen würde: 'builtin' · 'webui' · 'comfy'.

    Ist eine externe Stelle gewählt, aber gerade nicht erreichbar, steht hier
    'builtin' – der Rückfall. Wer wissen will, was der Nutzer GEWÄHLT hat,
    nimmt gewaehltes_backend().
    """
    try:
        import config
        gewaehlt = str(config.load().get("bild_backend") or "")
        if gewaehlt in _EXTERN and _extern_modul(gewaehlt).available():
            return gewaehlt
    except Exception:
        pass
    return "builtin"


def gewaehltes_backend() -> str:
    """Was der Nutzer gewählt hat – egal ob es gerade läuft."""
    try:
        import config
        gewaehlt = str(config.load().get("bild_backend") or "")
        return gewaehlt if gewaehlt in _EXTERN else "builtin"
    except Exception:
        return "builtin"


def missing_reason_aktiv() -> str | None:
    """Fehlt dem Motor, der jetzt malen würde, etwas? None = er kann malen.

    KLARE TRENNUNG – das ist der ganze Zweck dieser Funktion:

        eigene Pipeline   braucht torch, diffusers, transformers, Pillow.
        ComfyUI / WebUI   brauchen NICHTS davon. Das ist ein HTTP-Aufruf an
                          ein Programm, das schon läuft und sein eigenes torch
                          mitbringt.

    Vorher fragte `/bild`, `bild_malen` und der Selbsttest pauschal
    missing_reason() – also nach diffusers – selbst wenn per /bildmodel ein
    ComfyUI-Modell gewählt war. Ergebnis: "Es fehlt: diffusers, Pillow",
    obwohl ComfyUI danebenstand und einwandfrei malen konnte. Zwei Dinge, die
    nichts miteinander zu tun haben, hingen an derselben Prüfung.

    Der Rückfall auf die eigene Pipeline bleibt: Ist ComfyUI gewählt, aber
    gerade aus, und die eigene Pipeline ist eingerichtet, wird eben die
    genommen (wie bisher). Erst wenn BEIDES nicht geht, kommt eine Meldung –
    und die nennt dann den wahren Grund, nicht "diffusers fehlt".
    """
    gewaehlt = gewaehltes_backend()
    if gewaehlt in _EXTERN:
        try:
            if _extern_modul(gewaehlt).available():
                return None                  # läuft – hier ist nichts zu installieren
        except Exception:
            pass
        if missing_reason() is None:
            return None                      # Rückfall auf die eigene Pipeline greift
        name = "ComfyUI" if gewaehlt == "comfy" else "die WebUI"
        try:
            adresse = f" ({_extern_modul(gewaehlt).host()})"
        except Exception:
            adresse = ""
        return (f"{name} ist gewählt, aber nicht erreichbar{adresse} – und die eigene "
                "Pipeline ist nicht eingerichtet.\n"
                f"Starte {name}, oder wähle mit /bildmodel ein eigenes Modell.")
    return missing_reason()


def active_label() -> str:
    """Menschlicher Name des aktiven Bild-Modells (für Statusausgaben)."""
    art = backend()
    if art == "webui":
        import sdwebui
        return f"🌐 WebUI · {sdwebui.chosen_model() or sdwebui.current_model() or '?'}"
    if art == "comfy":
        import comfyui
        return f"🧩 ComfyUI · {comfyui.chosen_model() or comfyui.resolve_model(None) or '?'}"
    ck = resolve(None)
    return ck.label if ck else "—"


def paint(prompt: str, *, model: str | None = None, neg: str | None = None,
          steps: int | None = None, cfg: float | None = None,
          size: tuple[int, int] | None = None, seed: int | None = None,
          sampler: str | None = None, karras: bool | None = None,
          on_status=None) -> tuple[str, bool]:
    """Malt EIN Bild mit dem AKTIVEN Motor (eigene Pipeline oder externe WebUI).
    Gibt (pfad, nachbessern_sinnvoll) zurück – die OpenCV-Nachbesserung passt nur
    zur eigenen SD1.5/SDXL-Pipeline, nicht zu WebUI-Modellen (Krea/Qwen)."""
    art = backend()
    if art in _EXTERN:
        # Beide externen Stellen haben absichtlich dieselbe Aufrufform.
        p = _extern_modul(art).generate(
            prompt, model=model, neg=neg, steps=steps, cfg=cfg,
            size=size, seed=seed, sampler=sampler, on_status=on_status)
        return p, False
    p = generate(prompt, model=model, neg=neg, steps=steps, cfg=cfg, size=size,
                 seed=seed, sampler=sampler, karras=karras, adetailer=False,
                 on_status=on_status)
    return p, True


def resolve(name: str | None) -> Checkpoint | None:
    """Sucht einen Checkpoint per Name (auch Teilstring).

    Ohne Namen: das per /bildmodel gewählte Modell – und wenn keins gewählt
    (oder die Datei inzwischen weg) ist, der erste gefundene.

    Beim Namen sind wir bewusst großzügig: Das Modell schickt hier mal einen
    ganzen Pfad, mal den Dateinamen mit `.safetensors`, mal Bindestriche statt
    Unterstriche. Das ist alles offensichtlich gemeint – daran soll das Malen
    nicht scheitern.
    """
    cks = discover()
    if not cks:
        return None
    if not name or not str(name).strip():
        gemerkt = default_model()
        if gemerkt and gemerkt in cks:
            return cks[gemerkt]
        return next(iter(cks.values()))

    name = str(name).strip().strip('"').strip("'")
    if name in cks:
        return cks[name]

    # Pfad und Endung abwerfen:  C:\...\realvisxl.safetensors  ->  realvisxl
    kurz = Path(name.replace("\\", "/")).name
    if kurz.lower().endswith(".safetensors"):
        kurz = kurz[: -len(".safetensors")]
    if kurz in cks:
        return cks[kurz]

    def blank(s: str) -> str:
        """Nur Buchstaben/Ziffern, klein – für den unempfindlichen Vergleich."""
        return "".join(ch for ch in s.lower() if ch.isalnum())

    def suche(text: str) -> Checkpoint | None:
        low = text.lower()
        for ref, c in cks.items():                  # Teilstring, egal welche Richtung
            rl = ref.lower()
            if low in rl or rl in low:
                return c
        bl = blank(text)                            # Unterstriche/Bindestriche egal
        if bl:
            for ref, c in cks.items():
                rb = blank(ref)
                if bl in rb or rb in bl:
                    return c
        return None

    treffer = suche(kurz)
    if treffer:
        return treffer

    # Letzter Versuch ohne fremde Schriftzeichen: kleine Modelle hängen gern mal
    # ein chinesisches Wort an den Dateinamen ("realvisxl…Lightning模型").
    # Der lateinische Teil ist eindeutig genug.
    nur_ascii = "".join(ch for ch in kurz if ord(ch) < 128).strip(" _-.")
    if nur_ascii and nur_ascii != kurz:
        return suche(nur_ascii)
    return None


# ===========================================================================
#  Prompt-Optionen parsen:  "ein fuchs --steps 30 --cfg 6 --size 768x768 ..."
# ===========================================================================

def saubere_worte(text: str) -> str:
    """Wirft Schriftzeichen raus, mit denen CLIP nichts anfangen kann.

    Kleine mehrsprachige Modelle rutschen im englischen Prompt gern mal in eine
    andere Schrift ("smiling诱情ly at the viewer"). CLIP ist auf Englisch
    trainiert – solche Zeichen werden zu Rausch-Tokens und ziehen Aufmerksamkeit
    von den echten Wörtern ab.

    Behalten wird alles Lateinische inklusive Umlauten und Akzenten (bis U+02FF),
    weg fliegen CJK, Kyrillisch, Arabisch, Emoji & Co. Emoji im Prompt sind eh
    nutzlos, die kosten nur Tokens.
    """
    if not text:
        return text
    sauber = "".join(ch for ch in text if ord(ch) < 0x300 or ch.isspace())
    sauber = re.sub(r"[ \t]{2,}", " ", sauber)
    sauber = re.sub(r"\s+([,.])", r"\1", sauber)        # " ," -> ","
    sauber = re.sub(r"(,\s*){2,}", ", ", sauber)        # ", ," -> ","
    return sauber.strip(" ,\t")


def parse_opts(text: str) -> dict:
    """Zieht --neg/--steps/--cfg/--size/--seed/--model aus dem Text. Rest = Prompt."""
    opts: dict = {}

    def take(pattern, conv):
        nonlocal text
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                val = conv(m.group(1))
            except Exception:
                return
            text = (text[:m.start()] + text[m.end():])
            opts.update(val)

    # --neg "..."  oder  --neg bis zum nächsten --flag / Zeilenende
    take(r'--neg\s+"([^"]*)"', lambda v: {"neg": v})
    take(r'--neg\s+(.+?)(?=\s--|\s*$)', lambda v: {"neg": v.strip()})
    take(r'--model\s+"([^"]+)"', lambda v: {"model": v})
    take(r'--model\s+(\S+)', lambda v: {"model": v})
    take(r'--steps\s+(\d+)', lambda v: {"steps": max(1, min(150, int(v)))})
    take(r'--cfg\s+([\d.]+)', lambda v: {"cfg": max(1.0, min(30.0, float(v)))})
    take(r'--seed\s+(\d+)', lambda v: {"seed": int(v)})
    take(r'--size\s+(\d+x\d+)', lambda v: {"size": tuple(int(x) for x in v.lower().split("x"))})
    take(r'--sampler\s+(\S+)', lambda v: {"sampler": v.lower()})
    take(r'(--euler)\b', lambda v: {"sampler": "euler"})
    take(r'(--no-?karras)\b', lambda v: {"karras": False})
    take(r'(--no-?face|--no-?adetailer)\b', lambda v: {"adetailer": False})
    take(r'--face-?strength\s+([\d.]+)', lambda v: {"face_strength": max(0.1, min(0.9, float(v)))})

    opts["prompt"] = re.sub(r"\s+", " ", text).strip()
    return opts


# ===========================================================================
#  Der eigene Scheduler:  Euler (deterministisch), k-diffusion-Stil
# ===========================================================================

def _sigma_schedule(num_steps: int, num_train: int, alphas_cumprod, karras: bool = True):
    """Baut die Rausch-Stärken (sigmas) für 'num_steps' Schritte selbst.

    sigma(t) = sqrt((1 - alphas_cumprod[t]) / alphas_cumprod[t]).
    - karras=True: Karras-Rauschplan (rho=7) zwischen sigma_min und sigma_max –
      verteilt die Schritte schlauer, mehr feine Details, weniger Matsch.
    - karras=False: einfaches gleichmäßiges Abtasten (alt).
    Am Ende eine 0 (vollständig entrauscht). Für jede sigma wird der passende
    (kontinuierliche) Trainings-Zeitschritt für die UNet-Eingabe mitgegeben.
    Rückgabe: (sigmas[num_steps+1], timesteps[num_steps])."""
    import numpy as np
    import torch

    ac = alphas_cumprod.detach().float().cpu().numpy().astype(np.float64)
    sig_train = np.sqrt((1.0 - ac) / ac)                    # steigt mit t
    ts = np.arange(num_train)
    s_min, s_max = float(sig_train[0]), float(sig_train[-1])

    if karras:
        rho = 7.0
        ramp = np.linspace(0, 1, num_steps)
        min_ir, max_ir = s_min ** (1 / rho), s_max ** (1 / rho)
        sigmas = (max_ir + ramp * (min_ir - max_ir)) ** rho     # hoch -> niedrig
    else:
        t = np.linspace(num_train - 1, 0, num_steps)
        sigmas = np.interp(t, ts, sig_train)

    # jede sigma auf ihren Trainings-Zeitschritt zurückrechnen (für die UNet-Eingabe)
    times = np.interp(sigmas, sig_train, ts)
    timesteps = torch.from_numpy(times.copy()).float()
    sigmas = torch.from_numpy(np.append(sigmas, 0.0)).float()
    return sigmas, timesteps


# ===========================================================================
#  Modell laden (Komponenten) – gecacht, damit zweites Bild schnell kommt
# ===========================================================================

_CACHE: dict = {"ref": None, "parts": None}


def _drop_gpu_parts(parts: dict | None) -> None:
    """Schiebt geladene Netze auf die CPU und gibt VRAM frei.

    Sonst bleibt beim Modellwechsel (SDXL ↔ SD1.5) das alte Checkpoint
    oft noch auf der GPU, bis Python zufällig aufräumt.
    """
    if not parts:
        return
    import gc
    import torch
    for k in ("unet", "vae", "text_encoder", "text_encoder_2"):
        m = parts.get(k)
        if m is None:
            continue
        try:
            m.to("cpu")
        except Exception:
            pass
        parts[k] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _quiet_loaders() -> None:
    """Fortschritts-Balken & Warnungen von diffusers/transformers/HuggingFace
    abschalten.

    Die schreiben mit tqdm DIREKT aufs Terminal – am Vollbild-TUI vorbei. Das
    sieht nicht nur unruhig aus: prompt_toolkit weiß nichts von diesen Zeichen,
    hält seine Bildschirm-Kopie für aktuell und übermalt sie nie wieder. Die
    Reste bleiben dann quer im Bild stehen. Wir haben mit `on_status` sowieso
    unsere eigene Fortschritts-Anzeige.
    """
    import os
    import warnings

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    warnings.filterwarnings("ignore", message=".*should be kept in float32.*")
    for mod, fns in (
        ("huggingface_hub.utils", ("disable_progress_bars",)),
        ("diffusers.utils.logging", ("set_verbosity_error", "disable_progress_bar")),
        ("transformers.utils.logging", ("set_verbosity_error", "disable_progress_bar")),
    ):
        try:
            m = __import__(mod, fromlist=["x"])
            for fn in fns:
                getattr(m, fn, lambda: None)()
        except Exception:
            pass        # nicht installiert / andere Version -> egal


def _infer_dtype():
    """bf16 auf Ampere+ (RTX 30/40/50): gleiche Reichweite wie fp32, VAE braucht
    kein extra-fp32 mehr. Ältere Karten: fp16. CPU: fp32."""
    import torch
    if not torch.cuda.is_available():
        return torch.float32
    major, _minor = torch.cuda.get_device_capability(0)
    if major >= 8:
        return torch.bfloat16
    return torch.float16


def _load_parts(ck: Checkpoint, on_status=None):
    """Lädt UNet/VAE/Text-Encoder aus dem Einzeldatei-Checkpoint und legt sie auf
    die GPU. Wir nutzen diffusers NUR zum Laden der Netze – den Pipeline-Ablauf
    (unten in `generate`) schreiben wir selbst."""
    _quiet_loaders()
    import torch
    from diffusers import StableDiffusionPipeline, StableDiffusionXLPipeline

    if _CACHE["ref"] == ck.ref and _CACHE["parts"] is not None:
        return _CACHE["parts"]

    # Nur EIN Checkpoint auf der GPU: alten Kram (z.B. SD1.5) erst runter.
    _drop_gpu_parts(_CACHE.get("parts"))
    _CACHE["ref"], _CACHE["parts"] = None, None

    use_cuda = torch.cuda.is_available()
    dtype = _infer_dtype()
    device = "cuda" if use_cuda else "cpu"

    if on_status:
        tag = {torch.bfloat16: "bf16", torch.float16: "fp16"}.get(dtype, "fp32")
        on_status(f"lade Checkpoint ({ck.kind.upper()}, {tag}) …")

    loader = StableDiffusionXLPipeline if ck.kind == "sdxl" else StableDiffusionPipeline
    pipe = loader.from_single_file(str(ck.path), torch_dtype=dtype, safety_checker=None)

    # Komponenten rausziehen. Die Pipeline-Hülle danach wegwerfen – sonst hält
    # `pipe` dieselben Netze zusätzlich fest (VRAM wirkt wie 2× geladen).
    unet = pipe.unet
    vae = pipe.vae
    text_encoder = pipe.text_encoder
    text_encoder_2 = getattr(pipe, "text_encoder_2", None)
    tokenizer = pipe.tokenizer
    tokenizer_2 = getattr(pipe, "tokenizer_2", None)
    alphas = pipe.scheduler.alphas_cumprod
    num_train = pipe.scheduler.config.num_train_timesteps
    vae_scale = float(pipe.vae.config.scaling_factor)
    vae_factor = 2 ** (len(pipe.vae.config.block_out_channels) - 1)
    for attr in ("unet", "vae", "text_encoder", "text_encoder_2",
                 "tokenizer", "tokenizer_2", "scheduler"):
        if hasattr(pipe, attr):
            setattr(pipe, attr, None)
    del pipe

    parts = {
        "kind": ck.kind,
        "device": device,
        "dtype": dtype,
        "unet": unet.to(device=device, dtype=dtype),
        # bf16 hat fp32-Exponent – VAE muss nicht extra in fp32.
        "vae": vae.to(device=device, dtype=dtype),
        "tokenizer": tokenizer,
        "text_encoder": text_encoder.to(device=device, dtype=dtype),
        "tokenizer_2": tokenizer_2,
        "text_encoder_2": (text_encoder_2.to(device=device, dtype=dtype)
                           if text_encoder_2 is not None else None),
        "alphas_cumprod": alphas.to(device),
        "num_train": num_train,
        "vae_scale": vae_scale,
        "vae_factor": vae_factor,
    }
    for m in ("unet", "vae", "text_encoder", "text_encoder_2"):
        if parts[m] is not None:
            parts[m].eval()

    import gc
    gc.collect()
    if use_cuda:
        torch.cuda.empty_cache()

    _CACHE["ref"], _CACHE["parts"] = ck.ref, parts
    return parts


# ===========================================================================
#  Text -> Embeddings  (selbst gesteuert, SD1.5 & SDXL)
# ===========================================================================

# --- Lange Prompts: CLIPs 77-Token-Grenze umgehen --------------------------
# CLIP verarbeitet nur 77 Tokens am Stück. Wer einfach abschneidet, verliert
# stillschweigend alles dahinter – der Standard-Negativ-Prompt hier oben ist
# allein schon 82 Tokens lang, „airbrushed, waxy skin" kam also nie beim Modell
# an. Deshalb: langen Text in 75er-Häppchen zerlegen, jedes einzeln durch CLIP
# schicken und die Ergebnisse aneinanderhängen. So machen es A1111/ComfyUI auch.

def _tok_ids(tok, text: str) -> list[int]:
    return tok(text, truncation=False, add_special_tokens=False).input_ids


def _chunk_count(tok, *texte: str) -> int:
    """Wie viele 75er-Blöcke braucht der längste dieser Texte? (mindestens 1)"""
    nutz = tok.model_max_length - 2
    return max(1, max(-(-len(_tok_ids(tok, t)) // nutz) for t in texte))


def _chunk_ids(tok, text: str, n_chunks: int) -> list[list[int]]:
    """Text -> genau n_chunks Blöcke à model_max_length Tokens (mit BOS/EOS)."""
    roh = _tok_ids(tok, text)
    nutz = tok.model_max_length - 2
    bloecke = [roh[i:i + nutz] for i in range(0, len(roh), nutz)] or [[]]
    while len(bloecke) < n_chunks:
        bloecke.append([])                     # leerer Block = reines Padding
    bos, eos = tok.bos_token_id, tok.eos_token_id
    return [[bos] + b + [eos] * (nutz - len(b) + 1) for b in bloecke[:n_chunks]]


def _embed_long(parts, tok, enc, text: str, n_chunks: int,
                penultimate: bool, want_pooled: bool):
    """Alle Blöcke durch CLIP schicken und zu EINER langen Sequenz verketten.
    Das 'pooled' Embedding kommt (wie bei A1111) aus dem ERSTEN Block."""
    import torch
    seqs, pooled = [], None
    for k, ids in enumerate(_chunk_ids(tok, text, n_chunks)):
        t = torch.tensor([ids], device=parts["device"])
        out = enc(t, output_hidden_states=True)
        seqs.append(out.hidden_states[-2] if penultimate else out.last_hidden_state)
        if k == 0 and want_pooled:
            pooled = out[0]                     # text_embeds (1280)
    return torch.cat(seqs, dim=1), pooled


def _encode_sd15(parts, prompt, neg):
    import torch
    tok, enc = parts["tokenizer"], parts["text_encoder"]
    n = _chunk_count(tok, prompt, neg)          # beide Seiten gleich lang halten
    with torch.no_grad():
        pe, _ = _embed_long(parts, tok, enc, prompt, n, False, False)
        ne, _ = _embed_long(parts, tok, enc, neg, n, False, False)
    return pe, ne, None, None


def _encode_sdxl(parts, prompt, neg):
    """SDXL nutzt ZWEI Text-Encoder. Wir verketten die vorletzten Hidden-States
    (768+1280=2048) und nehmen aus Encoder 2 zusätzlich das 'pooled' Embedding."""
    import torch
    tok1, enc1 = parts["tokenizer"], parts["text_encoder"]
    tok2, enc2 = parts["tokenizer_2"], parts["text_encoder_2"]
    # Beide Encoder UND beide Prompts brauchen dieselbe Blockzahl, sonst passen
    # die Sequenzen nachher nicht zusammen.
    n = max(_chunk_count(tok1, prompt, neg), _chunk_count(tok2, prompt, neg))

    def embed(txt):
        a, _ = _embed_long(parts, tok1, enc1, txt, n, True, False)
        b, pooled = _embed_long(parts, tok2, enc2, txt, n, True, True)
        return torch.cat([a, b], dim=-1), pooled

    with torch.no_grad():
        pe, pooled = embed(prompt)
        ne, npooled = embed(neg)
    return pe, ne, pooled, npooled


def _make_emb(parts, prompt, neg, H, W, orig=None, crop=(0, 0)):
    """Baut die Embeddings (uncond+cond) + bei SDXL die Zusatz-Konditionierung
    (pooled + time_ids für die Zielgröße H×W). Für SD1.5 ist `added` None.

    `orig`/`crop` sind SDXLs Mikro-Konditionierung: „so groß war das Original,
    und hier saß der Ausschnitt darin". SDXL wurde damit trainiert und richtet
    sein Detailniveau danach. Sagt man ihm bei einem Augen-Ausschnitt schlicht
    „Original = 245×100", liefert es entsprechend grobes Material. Sagt man die
    WAHRHEIT – der Ausschnitt stammt aus einem großen Bild –, malt es feiner.
    """
    import torch
    device, dtype = parts["device"], parts["dtype"]
    if parts["kind"] == "sdxl":
        pe, ne, pooled, npooled = _encode_sdxl(parts, prompt, neg)
        add_text = torch.cat([npooled, pooled]).to(dtype)
        oh, ow = orig if orig else (H, W)
        ct, cl = crop
        time_ids = torch.tensor([oh, ow, ct, cl, H, W], device=device, dtype=dtype)
        added = {"text_embeds": add_text, "time_ids": torch.stack([time_ids, time_ids])}
    else:
        pe, ne, _, _ = _encode_sd15(parts, prompt, neg)
        added = None
    emb = torch.cat([ne, pe]).to(dtype)                     # [uncond, cond]
    return emb, added


# ===========================================================================
#  Wiederverwendbarer Sampling-Loop  (für txt2img UND img2img)
# ===========================================================================

def _sample(parts, latents, emb, added, cfg, sampler, sigmas, timesteps,
            start_i=0, on_status=None, label="male"):
    """Unser Denoise-Loop ab Schritt `start_i` (für img2img > 0). DPM++ 2M
    (2. Ordnung) oder Euler. Gibt die fertigen Latents zurück."""
    import math
    import torch
    unet, device, dtype = parts["unet"], parts["device"], parts["dtype"]
    kw = {"added_cond_kwargs": added} if added else {}

    def denoise(x, sigma, t_val):
        model_in = x / ((sigma**2 + 1) ** 0.5)             # k-diffusion-Skalierung
        model_in = torch.cat([model_in, model_in]).to(dtype)
        t = t_val.to(device).expand(2)
        with torch.no_grad():
            noise = unet(model_in, t, encoder_hidden_states=emb, **kw).sample
        n_uncond, n_cond = noise.float().chunk(2)
        return x - sigma * (n_uncond + cfg * (n_cond - n_uncond))   # x0-Schätzung

    n = len(sigmas) - 1
    old = None
    for i in range(start_i, n):
        sigma = sigmas[i]
        denoised = denoise(latents, sigma, timesteps[i])
        si, sn = float(sigmas[i]), float(sigmas[i + 1])
        if sampler == "dpmpp_2m" and old is not None and sn > 0:
            h = math.log(si) - math.log(sn)
            h_last = math.log(float(sigmas[i - 1])) - math.log(si)
            r = h_last / h
            d = (1 + 1 / (2 * r)) * denoised - (1 / (2 * r)) * old
            ratio = sn / si
            latents = ratio * latents - (ratio - 1) * d
        elif sampler == "dpmpp_2m":
            ratio = (sn / si) if si > 0 else 0.0
            latents = ratio * latents - (ratio - 1) * denoised
        else:
            d = (latents - denoised) / sigma
            latents = latents + d * (sigmas[i + 1] - sigma)
        old = denoised
        if on_status:
            on_status(f"{label} … Schritt {i - start_i + 1}/{n - start_i}")
    return latents


def _vae_decode(parts, latents, on_status=None):
    """Latents -> Pixel-Array (HWC uint8). VAE im gleichen dtype wie der Rest (bf16).

    Der VAE-Schritt ist der speicherhungrigste der ganzen Pipeline. Reicht der
    VRAM nicht (bei dir belegt z.B. ein laufender Ollama-Server einiges), gab es
    vorher schlicht einen Absturz. Jetzt schalten wir im Notfall auf kachelweises
    Dekodieren um. BEWUSST erst im Notfall: Kacheln können feine Nähte im Bild
    hinterlassen, das wollen wir nicht ohne Not.
    """
    import numpy as np
    import torch

    def _dec():
        with torch.no_grad():
            x = (latents / parts["vae_scale"]).to(dtype=parts["vae"].dtype)
            return parts["vae"].decode(x).sample

    try:
        img = _dec()
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        if on_status:
            on_status("VRAM knapp – dekodiere kachelweise …")
        getattr(parts["vae"], "enable_tiling", lambda: None)()
        try:
            img = _dec()
        finally:
            getattr(parts["vae"], "disable_tiling", lambda: None)()

    img = (img / 2 + 0.5).clamp(0, 1)
    return (img[0].permute(1, 2, 0).float().cpu().numpy() * 255).round().astype(np.uint8)


def _vram_hinweis() -> str | None:
    """Warnung, wenn kaum freier VRAM da ist (typisch: ein Modell-Server hält ihn)."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        frei, gesamt = torch.cuda.mem_get_info()
    except Exception:
        return None
    frei_gb, ges_gb = frei / 2**30, gesamt / 2**30
    if frei_gb >= 6.0:
        return None
    return (f"nur {frei_gb:.1f} von {ges_gb:.0f} GB VRAM frei – andere Modelle "
            f"(z.B. ein laufender Ollama-Server) belegen den Rest. "
            f"Das kann Größe und Tempo begrenzen.")


def _vae_encode(parts, arr):
    """Pixel-Array (HWC uint8) -> Latents (für img2img)."""
    import torch
    t = torch.from_numpy(arr.astype("float32") / 255.0).permute(2, 0, 1).unsqueeze(0)
    t = (t * 2 - 1).to(device=parts["device"], dtype=parts["vae"].dtype)
    with torch.no_grad():
        lat = parts["vae"].encode(t).latent_dist.mean
    return lat * parts["vae_scale"]


# ===========================================================================
#  ADetailer – eigener Gesichts-Finder (numpy) + img2img-Nachschärfen
# ===========================================================================

def _faces_opencv(arr) -> list[tuple] | None:
    """Gesichter mit OpenCVs Haar-Cascade finden (der zuverlässige Weg).

    Gibt eine Liste von (x0,y0,x1,y1) zurück, größtes Gesicht zuerst – oder
    None, wenn OpenCV nicht installiert ist. Dann greift unten die
    Hautton-Heuristik als Notlösung.

    Warum überhaupt: Die Hautton-Regel hält bei warmem Licht auch Wände und
    Bettwäsche für Haut. Bei einem Ganzkörperbild verschmilzt dann alles zu
    EINER Fläche über das halbe Bild – und die Notbremse dort wirft das
    Ergebnis weg, das Gesicht bleibt matschig. Genau der Fall, den du hattest.
    """
    try:
        import cv2
    except Exception:
        return None

    import numpy as np

    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.equalizeHist(gray)                    # Kontrast angleichen
    H, W = gray.shape[:2]

    def cascade(name):
        clf = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        return None if clf.empty() else clf

    funde: list[tuple] = []
    # Frontal UND Profil – SD malt gern halb abgewandte Köpfe.
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        clf = cascade(name)
        if clf is None:
            continue
        # minSize: alles unter 4% der Bildbreite ist zu klein zum Nachbessern
        roh = clf.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                   minSize=(max(24, int(W * 0.04)),) * 2)
        for (x, y, w, h) in roh:
            funde.append((int(x), int(y), int(w), int(h)))

    if not funde:
        return []

    # Doppelte zusammenwerfen (Frontal- und Profil-Cascade finden oft dasselbe
    # Gesicht): stark überlappende Kästen fallen weg, größter gewinnt.
    funde.sort(key=lambda f: f[2] * f[3], reverse=True)
    behalten: list[tuple] = []
    for (x, y, w, h) in funde:
        mitte = (x + w / 2, y + h / 2)
        if any(abs(mitte[0] - (bx + bw / 2)) < bw * 0.5
               and abs(mitte[1] - (by + bh / 2)) < bh * 0.5
               for (bx, by, bw, bh) in behalten):
            continue
        behalten.append((x, y, w, h))

    # --- Gegenprobe: sind da überhaupt Augen? -----------------------------
    # Die Cascade hält gern mal Hals und Dekolleté für ein Gesicht (glatte,
    # helle Fläche mit Schatten – genau das Muster). Würde ADetailer da ein
    # Gesicht hineinmalen, wäre das Bild ruiniert. Über deinen Bildern getestet:
    # echte Gesichter kommen auf 2-3 Augenfunde, jeder Fehlalarm auf 0.
    augen = cascade("haarcascade_eye.xml")
    if augen is not None:
        geprueft = []
        for (x, y, w, h) in behalten:
            roi = gray[y:y + int(h * 0.65), x:x + w]        # obere Gesichtshälfte
            if roi.size == 0:
                continue
            n = len(augen.detectMultiScale(roi, 1.1, 4,
                                           minSize=(max(8, int(w * 0.10)),) * 2))
            if n >= 1:
                geprueft.append((x, y, w, h))
        behalten = geprueft

    boxen = []
    for (x, y, w, h) in behalten[:3]:                # höchstens 3 Gesichter
        # Cascade liefert einen engen Kasten. Etwas Luft drumherum gibt dem
        # Modell Kontext (Haaransatz, Kinn) – sonst malt es ein Gesicht ohne Rand.
        luft = 0.22
        x0 = max(0, int(x - w * luft))
        y0 = max(0, int(y - h * luft * 1.3))         # oben mehr (Stirn/Haar)
        x1 = min(W, int(x + w * (1 + luft)))
        y1 = min(H, int(y + h * (1 + luft)))
        if x1 - x0 >= 32 and y1 - y0 >= 32:
            boxen.append((x0, y0, x1, y1))
    return boxen


def _detect_face_box(arr):
    """Findet das Gesicht per Hautton-Heuristik (reines numpy/PIL, kein Modell).
    Idee: Haut-Pixel im oberen Bildteil finden, größte zusammenhängende Fläche
    nehmen, davon den oberen (Kopf-)Bereich als quadratische Box. Gibt
    (x0, y0, x1, y1) im Originalbild zurück – oder None, wenn kein Gesicht."""
    import numpy as np
    from PIL import Image

    H, W = arr.shape[:2]
    sw = 200                                                # klein rechnen = schnell
    sh = max(1, round(H * sw / W))
    small = np.asarray(Image.fromarray(arr).resize((sw, sh), Image.BILINEAR)).astype(int)
    R, G, B = small[:, :, 0], small[:, :, 1], small[:, :, 2]

    # Haut in YCbCr (klassische, einfache Regel)
    Cb = 128 - 0.168736 * R - 0.331264 * G + 0.5 * B
    Cr = 128 + 0.5 * R - 0.418688 * G - 0.081312 * B
    skin = (Cb >= 77) & (Cb <= 127) & (Cr >= 133) & (Cr <= 173) & (R > 50)
    # Zusätzliche RGB-Regel: YCbCr allein hält auch blonde Haare und warme
    # Hintergründe für Haut – dann verschmilzt alles zu EINER Riesenfläche und
    # die „Kopfbreite" wird zur Bildbreite. Echte Haut hat deutlich mehr Rot
    # als Grün; das siebt einen guten Teil davon aus.
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    skin &= ((R > 95) & (G > 40) & (B > 20) & ((mx - mn) > 15)
             & (np.abs(R - G) > 15) & (R > G) & (R > B))
    skin[int(sh * 0.85):, :] = False                        # ganz unten ignorieren

    # größte zusammenhängende Haut-Fläche per Flood-Fill (4er-Nachbarschaft)
    visited = np.zeros_like(skin, dtype=bool)
    best = None
    best_area = 0
    ys, xs = np.where(skin)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        stack = [(sy, sx)]
        visited[sy, sx] = True
        pix = []
        while stack:
            y, x = stack.pop()
            pix.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < sh and 0 <= nx < sw and skin[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if len(pix) > best_area:
            best_area = len(pix)
            best = pix

    if best is None or best_area < (sw * sh) * 0.012:        # zu klein -> kein Gesicht
        return None

    comp = np.array(best)                                    # (N,2): y,x
    cy0, cx0 = comp.min(axis=0)
    cy1, cx1 = comp.max(axis=0)
    # Kopfbreite aus dem oberen Viertel der Fläche (da sitzt das Gesicht)
    top_band = comp[comp[:, 0] <= cy0 + 0.28 * (cy1 - cy0 + 1)]
    hx0, hx1 = int(top_band[:, 1].min()), int(top_band[:, 1].max())
    headw = max(hx1 - hx0 + 1, 8)
    cx = (hx0 + hx1) / 2.0
    side = headw * 1.5                                       # Box etwas größer als Kopf
    fx0 = cx - side / 2.0
    fy0 = cy0 - side * 0.15                                  # etwas über den Scheitel
    # in Originalkoordinaten zurückrechnen
    sx_full, sy_full = W / sw, H / sh
    x0 = int(max(0, fx0 * sx_full))
    y0 = int(max(0, fy0 * sy_full))
    x1 = int(min(W, (fx0 + side) * sx_full))
    y1 = int(min(H, (fy0 + side * 1.15) * sy_full))
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None
    # Notbremse: Ein Gesicht ist nie das halbe Bild. Wenn die Box so groß wird,
    # hat die Heuristik Haare/Haut/Hintergrund zusammengeworfen – dann würde
    # ADetailer das GANZE Bild verkleinern, neu malen und wieder hochskalieren.
    # Das kostet überall Schärfe (Augen zuerst). Lieber gar nicht nachschärfen.
    if (x1 - x0) > W * 0.55 or (y1 - y0) > H * 0.55:
        return None
    return (x0, y0, x1, y1)


def _feather_mask(h, w, border=0.18):
    """Weiche Maske (1 in der Mitte, sanft auf 0 zum Rand) – fürs nahtlose Einkleben.

    Elliptisch statt rechteckig: Ein Kopf ist rund, ein Rechteck hinterlässt an
    den Ecken eine sichtbare weiche Kante im fertigen Bild.
    """
    import numpy as np
    yy = (np.arange(h) - (h - 1) / 2) / ((h - 1) / 2)        # -1 … +1
    xx = (np.arange(w) - (w - 1) / 2) / ((w - 1) / 2)
    r = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2)         # Abstand von der Mitte
    innen = 1.0 - border * 2                                 # bis hierhin voll deckend
    m = (1.0 - r) / max(1e-6, 1.0 - innen)
    return np.clip(m, 0.0, 1.0)


def _img2img(parts, arr, prompt, neg, cfg, sampler, strength, steps, seed,
             on_status=None, label="Gesicht", orig=None, crop=(0, 0)):
    """Malt ein bestehendes Bild-Stück teilweise neu (img2img). `strength` 0..1 =
    wie viel verändert wird. Gibt das neue Pixel-Array zurück."""
    import torch
    device = parts["device"]
    H, W = arr.shape[0], arr.shape[1]
    emb, added = _make_emb(parts, prompt, neg, H, W, orig, crop)
    sigmas, timesteps = _sigma_schedule(steps, parts["num_train"],
                                        parts["alphas_cumprod"], karras=True)
    sigmas = sigmas.to(device)
    init_lat = _vae_encode(parts, arr)
    start_i = max(0, min(steps - 1, steps - int(round(steps * strength))))
    gen = torch.Generator(device="cpu").manual_seed(seed)
    noise = torch.randn(init_lat.shape, generator=gen, dtype=torch.float32).to(device)
    latents = init_lat + noise * sigmas[start_i]            # teilweise verrauschen
    latents = _sample(parts, latents, emb, added, cfg, sampler, sigmas, timesteps,
                      start_i=start_i, on_status=on_status, label=label)
    return _vae_decode(parts, latents)


def _repaint_box(parts, arr, box, prompt, neg, cfg, sampler, strength, steps, seed,
                 on_status=None, label="male neu"):
    """Malt GENAU den Bereich `box` neu und klebt ihn weich zurück.

    Der gemeinsame Kern von ADetailer (Box kommt vom Finder) und dem
    Bearbeiten-Fenster (Box kommt von dir). Seitenverhältnis bleibt erhalten.
    """
    import numpy as np
    from PIL import Image

    x0, y0, x1, y1 = box
    crop = arr[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]

    f = parts["vae_factor"]
    target = 1024 if parts["kind"] == "sdxl" else 512
    ratio = cw / ch
    if ratio >= 1.0:
        work_w, work_h = target, round(target / ratio)
    else:
        work_w, work_h = round(target * ratio), target
    work_w = max(f * 8, (work_w // f) * f)
    work_h = max(f * 8, (work_h // f) * f)

    crop_rs = np.asarray(Image.fromarray(crop).resize((work_w, work_h), Image.LANCZOS))

    # SDXL die WAHRHEIT über den Ausschnitt sagen: er stammt aus einem großen
    # Bild und saß dort an dieser Stelle. Ohne das hält SDXL einen 245×100-Krümel
    # für das ganze Original und malt entsprechend grob. (Bei 2048 gedeckelt –
    # darüber wird es überschärft.)
    lupe = work_w / cw
    H_full, W_full = arr.shape[:2]
    orig = (min(2048, round(H_full * lupe)), min(2048, round(W_full * lupe)))
    crop_xy = (round(y0 * lupe), round(x0 * lupe))

    neu = _img2img(parts, crop_rs, prompt, neg, cfg, sampler,
                   strength, steps, seed, on_status, label,
                   orig=orig, crop=crop_xy)
    neu = np.asarray(Image.fromarray(neu).resize((cw, ch), Image.LANCZOS))

    mask = _feather_mask(ch, cw)[..., None]
    out = arr.astype(np.float32).copy()
    out[y0:y1, x0:x1] = neu.astype(np.float32) * mask + out[y0:y1, x0:x1] * (1 - mask)
    return out.clip(0, 255).astype(np.uint8)


def read_meta(path) -> dict:
    """Prompt/Negativ/Modell aus den PNG-Metadaten eines NemiCLI-Bildes lesen."""
    from PIL import Image
    try:
        info = Image.open(path).info
    except Exception:
        return {}
    return {k: info.get(k, "") for k in ("prompt", "negative", "model", "params")}


def region_defaults(path) -> dict:
    """Vorschlagswerte fürs Bearbeiten-Fenster (aus den Metadaten des Bildes)."""
    meta = read_meta(path)
    stil = ", ".join((meta.get("prompt") or "").split(",")[:3]).strip()
    # Wortwahl bewusst auf Augen-Details getrimmt – das war im Test der
    # sichtbarste Unterschied (Iris-Fasern, einzelne Wimpern, Lichtreflex).
    vorschlag = (f"{stil}, " if stil else "") + \
                ("detailed symmetric eyes, clear sharp irises, detailed iris texture, "
                 "defined eyelashes, natural catchlight, natural skin texture, sharp focus")
    return {
        "prompt": vorschlag,
        "neg": meta.get("negative") or DEFAULTS["neg"],
        "model": meta.get("model") or None,
        "strength": DEFAULTS["face_strength"],
    }


def find_faces(arr) -> list[tuple]:
    """Alle Gesichter im Bild – erst OpenCV, sonst die Hautton-Heuristik.
    Liste von (x0,y0,x1,y1), größtes zuerst; leer, wenn keins gefunden wurde."""
    boxen = _faces_opencv(arr)
    if boxen is None:                       # OpenCV nicht installiert
        eins = _detect_face_box(arr)        # Notlösung: Hautton-Heuristik
        return [eins] if eins else []
    # OpenCV ist da: seinem „nichts gefunden" vertrauen wir. Die Hautton-
    # Heuristik als zweite Meinung anzuwerfen brächte hier nur die Fehlgriffe
    # zurück, die sie bei warmem Licht produziert (Wand = Haut).
    return boxen


def auto_regions(path, arr=None) -> list[dict]:
    """Sucht SELBST die Stellen, die eine Nachbesserung lohnen.

    Das ist der autonome Ersatz fürs Rahmen-Ziehen von Hand: erst das Gesicht
    (Hautton-Heuristik), und wenn das groß genug ist, danach nochmal gezielt die
    Augenpartie. Die Augen zuletzt und extra, weil sie im vollen Gesichts-
    Ausschnitt nur wenige Pixel abbekommen – erst als eigener Ausschnitt werden
    Iris und Wimpern wirklich scharf.

    Rückgabe: Liste von {"name", "box", "strength", "cfg", "prompt"} in der
    Reihenfolge, in der gemalt werden soll. Leer = hier ist nichts zu holen.

    Zu den Werten: Stärke 0.4 bei Prompt-Treue 6.0 ist ausprobiert und abgesegnet.
    Höher (0.5) sah zwar detailreicher aus, malte aber ein anderes Gesicht –
    Sommersprossen und Augenfarbe stimmten nicht mehr. Nachbessern heißt schärfen,
    nicht austauschen.
    """
    import numpy as np
    from PIL import Image

    if arr is None:
        arr = np.asarray(Image.open(path).convert("RGB"))

    gesichter = find_faces(arr)
    if not gesichter:
        return []

    vor = region_defaults(path)
    stil = ", ".join((read_meta(path).get("prompt") or "").split(",")[:3]).strip()
    face_prompt = ((f"{stil}, " if stil else "")
                   + "close-up portrait of a face, detailed face, "
                     "detailed symmetric eyes, clear sharp irises, "
                     "natural skin texture, sharp focus")

    plan: list[dict] = []
    for i, (x0, y0, x1, y1) in enumerate(gesichter):
        fw, fh = x1 - x0, y1 - y0
        nr = f" {i + 1}" if len(gesichter) > 1 else ""
        plan.append({
            "name": f"Gesicht{nr}",
            "box": (x0, y0, x1, y1),
            "strength": 0.4,
            "cfg": 6.0,
            "prompt": face_prompt,
        })

        # Augenband: grob das obere Drittel der Gesichts-Box, seitlich enger.
        # Nur bei großen Gesichtern – bei einem 120-Pixel-Kopf wäre der
        # Augen-Ausschnitt so klein, dass das Hochskalieren nur Matsch vergrößert.
        if fw >= 220 and fh >= 220:
            ex0, ex1 = x0 + int(fw * 0.08), x1 - int(fw * 0.08)
            ey0, ey1 = y0 + int(fh * 0.30), y0 + int(fh * 0.60)
            if ex1 - ex0 >= 32 and ey1 - ey0 >= 32:
                plan.append({
                    "name": f"Augen{nr}",
                    "box": (ex0, ey0, ex1, ey1),
                    "strength": 0.35,         # vorsichtiger – der Blick soll bleiben
                    "cfg": 6.0,
                    "prompt": vor["prompt"],  # schon auf Augen-Details getrimmt
                })

    return plan


def auto_nachbessern(path, *, steps=30, seed=None, on_status=None) -> str | None:
    """Bessert ein fertiges Bild AUTOMATISCH nach – Gesicht(er) + Augen – und
    speichert das Ergebnis als neue Datei. Läuft komplett OHNE Fenster (headless),
    blockierend, also im Thread aufrufen.

    Das ist der stabile Ersatz fürs frühere Auto-Fenster: tkinter aus einem
    Hintergrund-Thread heraus zu öffnen brachte den ganzen Prozess zum Absturz.
    Die eigentliche Arbeit (Stellen finden, neu malen) braucht aber gar kein
    Fenster – nur das manuelle Nachhelfen (`/bearbeiten`) tut das noch.

    Gibt den Pfad der nachgebesserten Datei zurück, oder None, wenn nichts zu tun
    war (kein Gesicht gefunden) bzw. die Bibliotheken fehlen.
    """
    if missing_reason():
        return None
    import numpy as np
    from PIL import Image, PngImagePlugin

    try:
        arr = np.asarray(Image.open(path).convert("RGB"))
    except Exception:
        return None

    plan = auto_regions(path, arr)
    if not plan:
        return None                              # nichts Auffälliges – Original bleibt

    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    for i, schritt in enumerate(plan):
        if on_status:
            on_status(f"{schritt['name']} nachbessern … ({i + 1}/{len(plan)})")
        try:
            arr = repaint_region(
                path, schritt["box"], prompt=schritt["prompt"],
                strength=schritt["strength"], cfg=schritt.get("cfg"),
                steps=steps, seed=seed + i, arr=arr, on_status=on_status)
        except Exception:
            continue                             # eine Stelle misslingt? Rest trotzdem machen

    # neben dem Original speichern (Original bleibt unangetastet), Metadaten mitnehmen
    p = Path(path)
    ziel = p.with_name(f"{p.stem}_edit{int(time.time()) % 10000}.png")
    meta = read_meta(path)
    info = PngImagePlugin.PngInfo()
    for k, v in meta.items():
        if v:
            info.add_text(k, v)
    info.add_text("bearbeitet", "automatisch nachgebessert (Gesicht/Augen)")
    try:
        Image.fromarray(arr).save(ziel, pnginfo=info)
    except Exception:
        return None
    return str(ziel)


def repaint_region(path, box, *, prompt=None, neg=None, model=None,
                   strength=None, steps=20, cfg=None, sampler=None,
                   seed=None, on_status=None, arr=None):
    """Malt im Bild `path` den Bereich `box` (x0,y0,x1,y1) neu.

    Gibt das neue Bild als Array zurück (nichts wird überschrieben – speichern
    entscheidest du im Fenster). Läuft blockierend, also im Thread aufrufen.

    `arr` überschreibt den Bildinhalt: Ohne wird `path` von der Platte gelesen.
    Beim autonomen Durchlauf werden mehrere Bereiche NACHEINANDER gemalt – da
    muss der zweite Durchgang auf dem Ergebnis des ersten aufsetzen, sonst wäre
    die erste Verbesserung wieder weg. `path` dient dann nur noch als Quelle für
    die Metadaten (Prompt/Modell).
    """
    reason = missing_reason()
    if reason:
        raise RuntimeError(reason)
    import numpy as np
    from PIL import Image

    vor = region_defaults(path)
    prompt = prompt or vor["prompt"]
    neg = neg if neg is not None else vor["neg"]
    strength = vor["strength"] if strength is None else strength
    cfg = DEFAULTS["cfg"] if cfg is None else cfg
    sampler = (sampler or DEFAULTS["sampler"]).lower()
    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    ck = resolve(model or vor["model"])
    if ck is None:
        raise RuntimeError("Kein passender Checkpoint gefunden.")

    if arr is None:
        arr = np.asarray(Image.open(path).convert("RGB"))
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = box
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 32 or y1 - y0 < 32:
        raise RuntimeError("Der markierte Bereich ist zu klein (mindestens 32×32).")

    parts = _load_parts(ck, on_status)
    return _repaint_box(parts, arr, (x0, y0, x1, y1), prompt, neg, cfg, sampler,
                        strength, steps, seed, on_status, "male Bereich")


def _enhance_faces(parts, arr, prompt, neg, cfg, sampler, strength, seed, on_status=None):
    """ADetailer: Gesicht finden, in hoher Auflösung neu malen, weich zurückkleben.
    Gibt (neues_array, anzahl_gesichter) zurück."""
    import numpy as np
    from PIL import Image

    box = _detect_face_box(arr)
    if box is None:
        return arr, 0

    if on_status:
        on_status("ADetailer: male Gesicht neu …")
    # Nur die Stil-/Qualitäts-Angaben vom Anfang übernehmen, NICHT die ganze
    # Szenenbeschreibung: Auf Gesichtsgröße heruntergebrochen versucht das Modell
    # sonst, Kleidung, Vorhänge und Hintergrund mit ins Gesicht zu malen.
    stil = ", ".join(prompt.split(",")[:3]).strip()
    face_prompt = (f"{stil}, close-up portrait of a face, detailed face, "
                   "detailed symmetric eyes, clear sharp irises, natural skin texture, "
                   "sharp focus")
    out = _repaint_box(parts, arr, box, face_prompt, neg, cfg, sampler,
                       strength, 20, seed + 1, on_status, "Gesicht")
    return out, 1


# ===========================================================================
#  DER KERN:  generate()  – unser Sampling-Loop
# ===========================================================================

def generate(prompt: str, *, model: str | None = None, neg: str | None = None,
             steps: int | None = None, cfg: float | None = None,
             size: tuple[int, int] | None = None, seed: int | None = None,
             sampler: str | None = None, karras: bool | None = None,
             adetailer: bool | None = None, face_strength: float | None = None,
             on_status=None) -> str:
    """Erzeugt EIN Bild und gibt den gespeicherten Pfad zurück.
    Läuft blockierend (im Thread aufrufen). `on_status(text)` für Fortschritt."""
    reason = missing_reason()
    if reason:
        raise RuntimeError(reason)

    import numpy as np
    import torch
    from PIL import Image, PngImagePlugin

    ck = resolve(model)
    if ck is None:
        # Zwei sehr verschiedene Fälle, die früher beide „Kein Checkpoint
        # gefunden" hießen – und dich zu Recht ratlos gemacht haben, weil die
        # Datei ja da lag.
        vorhanden = discover()
        if vorhanden:
            raise RuntimeError(
                f"Das Bild-Modell '{model}' kenne ich nicht. Vorhanden sind:\n"
                + "\n".join(f"  · {ref}" for ref in vorhanden)
                + "\nOhne Angabe nehme ich das per /bildmodel gewählte.")
        raise RuntimeError(
            "Kein Checkpoint gefunden. Lege eine .safetensors-Datei in:\n"
            f"  {CKPT_DIRS[0]}\n(z.B. ein Modell von Civitai) und versuch's nochmal.")
    if ck.kind == "unknown":
        raise RuntimeError(f"'{ck.ref}' sieht nicht nach SD1.5/SDXL aus (unbekanntes Format).")

    steps = steps or DEFAULTS["steps"]
    cfg = DEFAULTS["cfg"] if cfg is None else cfg
    neg = DEFAULTS["neg"] if neg is None else neg
    prompt = saubere_worte(prompt)              # fremde Schriftzeichen raus (CLIP kann nur Latein)
    neg = saubere_worte(neg)
    sampler = (sampler or DEFAULTS["sampler"]).lower()
    karras = DEFAULTS["karras"] if karras is None else karras
    adetailer = DEFAULTS["adetailer"] if adetailer is None else adetailer
    face_strength = DEFAULTS["face_strength"] if face_strength is None else face_strength
    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    parts = _load_parts(ck, on_status)
    device, dtype = parts["device"], parts["dtype"]

    if on_status:
        warnung = _vram_hinweis()
        if warnung:
            on_status(f"⚠ {warnung}")

    if size is None:
        size = DEFAULTS["size"]
    W, H = size
    W, H = min(max(int(W), 64), MAX_EDGE), min(max(int(H), 64), MAX_EDGE)
    f = parts["vae_factor"]
    W, H = (W // f) * f, (H // f) * f                        # auf VAE-Raster runden
    latent_h, latent_w = H // f, W // f

    # 1) Prompt -> Embeddings (+ SDXL-Zusatzkonditionierung)
    if on_status:
        on_status("verarbeite Prompt …")
    emb, added = _make_emb(parts, prompt, neg, H, W)

    # 2) Start-Rauschen + eigene sigmas (Karras-Plan)
    sigmas, timesteps = _sigma_schedule(steps, parts["num_train"],
                                        parts["alphas_cumprod"], karras=karras)
    sigmas = sigmas.to(device)
    gen = torch.Generator(device="cpu").manual_seed(seed)
    latents = torch.randn((1, parts["unet"].config.in_channels, latent_h, latent_w),
                          generator=gen, dtype=torch.float32).to(device)
    latents = latents * sigmas[0]                           # auf erste sigma skalieren

    # 3) Unser Denoise-Loop (DPM++ 2M / Euler)
    latents = _sample(parts, latents, emb, added, cfg, sampler,
                      sigmas, timesteps, on_status=on_status, label="male")

    # 4) Latents -> Pixel (VAE, gleicher dtype wie UNet)
    if on_status:
        on_status("VAE: rechne in ein Bild …")
    arr = _vae_decode(parts, latents, on_status)

    # 5) ADetailer: Gesicht finden und schärfer neu malen (Bonus, bei Fehlern egal)
    n_faces = 0
    if adetailer:
        if on_status:
            on_status("ADetailer: suche Gesicht …")
        try:
            arr, n_faces = _enhance_faces(parts, arr, prompt, neg, cfg, sampler,
                                          face_strength, seed, on_status)
        except Exception:
            n_faces = 0

    # 6) Speichern (mit Prompt-Infos in den PNG-Metadaten)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"nemi_{stamp}_{seed}.png"
    meta = PngImagePlugin.PngInfo()
    meta.add_text("prompt", prompt)
    meta.add_text("negative", neg)
    meta.add_text("model", ck.ref)
    meta.add_text("params", f"steps={steps}, cfg={cfg}, size={W}x{H}, seed={seed}, "
                            f"sampler={sampler}{'+karras' if karras else ''}, "
                            f"adetailer={'on' if (adetailer and n_faces) else 'off'}")
    Image.fromarray(arr).save(out, pnginfo=meta)
    return str(out)
