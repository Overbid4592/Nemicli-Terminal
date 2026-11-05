"""
bildmotoren - Bild-Modelle neben Krea 2, je Bauart ein eigener Motor.

Ordner `Models/Bildmodelle/` (Daten- und Programmordner):

    <Name>/                Modell: eine .safetensors (auch in Teilen) oder .gguf mit dem DiT,
                           optional eigene text_encoders/, vae/, tokenizer/
    <datei>.gguf|.safetensors   ein DiT direkt im Ordner zählt ebenso als Modell
    text_encoders/ vae/ tokenizer/   geteilt von allen Modellen

Die Bauart wird an den Tensornamen erkannt, nicht am Dateinamen; Text-Encoder und VAE an ihrer
Form. Bekannte Dateien (Liste je Bauart) werden einmal je Dateistand per SHA-256 geprüft;
eine Abweichung sperrt die Datei. Unbekannte Dateien laufen, gelten aber als ungeprüft.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import chroma, flux1, flux2, qwenimage, zimage
from .gewichte import FormatFehler, ist_folgeteil, namen, ohne_praefix

try:
    from paths import INSTALL as _INSTALL, ROOT as _ROOT
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parents[2]

BAUARTEN = {"zimage": zimage, "flux2": flux2, "qwenimage": qwenimage, "flux1": flux1, "chroma": chroma}
ORDNER = [_ROOT / "Models" / "Bildmodelle", _INSTALL / "Models" / "Bildmodelle"]
GETEILT = ("text_encoders", "vae", "tokenizer", "tokenizer_t5", "tokenizer_clip")
GEPRUEFT = ".geprueft.json"
BACKEND = "motor"
MAX_KANTE = 2048
ENTLADEN_NACH = 300


# ===========================================================================
#  Finden
# ===========================================================================

def ordner() -> Path:
    """Der erste vorhandene Bildmodelle-Ordner (sonst der im Datenordner)."""
    for o in ORDNER:
        if o.is_dir():
            return o
    return ORDNER[0]


def bauart_von(pfad: Path) -> str | None:
    try:
        k = ("bauart", str(pfad), pfad.stat().st_mtime_ns)
    except OSError:
        return None
    if k not in _PASST:
        alle, _ = ohne_praefix(namen(pfad))
        _PASST[k] = next((name for name, modul in BAUARTEN.items() if modul.erkennen(alle)), None)
    return _PASST[k]


def _kandidaten(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return [p for p in sorted(d.iterdir())
            if p.is_file() and p.suffix.lower() in (".gguf", ".safetensors") and not ist_folgeteil(p)]


_CACHE: dict = {}


def modelle() -> dict[str, dict]:
    """Name -> {name, bauart, dit, ordner} aller erkannten Bild-Modelle."""
    stand = []
    for o in ORDNER:
        if o.is_dir():
            stand += [(str(p), p.stat().st_mtime_ns) for p in o.rglob("*") if p.suffix.lower()
                      in (".gguf", ".safetensors")]
    schluessel = tuple(sorted(stand))
    if _CACHE.get("schluessel") == schluessel:
        return _CACHE["modelle"]
    aus: dict[str, dict] = {}
    for o in ORDNER:
        if not o.is_dir():
            continue
        for p in _kandidaten(o):
            if (b := bauart_von(p)) and p.stem not in aus:
                aus[p.stem] = {"name": p.stem, "bauart": b, "dit": p, "ordner": o}
        for unter in sorted(x for x in o.iterdir() if x.is_dir() and x.name not in GETEILT):
            for p in _kandidaten(unter):
                if (b := bauart_von(p)) and unter.name not in aus:
                    aus[unter.name] = {"name": unter.name, "bauart": b, "dit": p, "ordner": unter}
                    break
    _CACHE.update(schluessel=schluessel, modelle=aus)
    return aus


_PASST: dict = {}                     # (pfad, mtime, prüfung) -> bool: Dateiköpfe nur einmal lesen


def _passt(p: Path, passt, dit: Path) -> bool:
    try:
        k = (str(p), p.stat().st_mtime_ns, passt, str(dit), dit.stat().st_mtime_ns)
        if k not in _PASST:
            _PASST[k] = bool(passt(p, dit))
        return _PASST[k]
    except Exception:
        return False


def _suche(m: dict, art: str, passt, extra: list | None = None) -> Path | None:
    """Zubehör (text_encoders/vae) im Modellordner, sonst im geteilten Ordner (und `extra`) – passend zum DiT."""
    orte = [m["ordner"] / art] if m["ordner"] not in ORDNER else []
    orte += [o / art for o in ORDNER] + list(extra or [])
    for o in orte:
        for p in _kandidaten(o):
            if _passt(p, passt, m["dit"]):
                return p
    return None


def teile(name: str | None = None) -> dict:
    """dit, text, vae, tokenizer und bauart des Modells."""
    m = modelle().get(name or gewaehlt() or "")
    if m is None:
        return {}
    modul = BAUARTEN[m["bauart"]]
    aus = {"name": m["name"], "bauart": m["bauart"], "dit": m["dit"],
           "text": _suche(m, "text_encoders", modul.text_passt),
           "vae": _suche(m, "vae", modul.vae_passt, getattr(modul, "vae_orte", list)())}
    if hasattr(modul, "clip_passt"):
        aus["clip"] = _suche(m, "text_encoders", modul.clip_passt)
    for art in ("tokenizer",) + tuple(getattr(modul, "TOKENIZER", {})):
        aus[art] = next((o / art for o in ([m["ordner"]] + ORDNER) if (o / art).is_dir()), ORDNER[0] / art)
    return aus


# ===========================================================================
#  Wahl
# ===========================================================================

def gewaehlt() -> str | None:
    try:
        import config
        g = config.load().get("bild_motor_modell") or None
    except Exception:
        g = None
    alle = modelle()
    return g if g in alle else next(iter(alle), None)


def waehlen(name: str) -> None:
    import config
    config.update(bild_motor_modell=name, bild_backend=BACKEND)


def titel(name: str | None = None) -> str:
    m = modelle().get(name or gewaehlt() or "")
    if m is None:
        return "?"
    modul = BAUARTEN[m["bauart"]]
    return f"{modul.SYMBOL} {modul.TITEL}"


def vorgaben(name: str | None = None) -> dict:
    """Schritte, Größe (und CFG) des Modells – je Fassung (z. B. destilliert oder „base“)."""
    m = modelle().get(name or gewaehlt() or "")
    if m is None:
        return dict(zimage.STANDARD)
    return BAUARTEN[m["bauart"]].vorgaben(m["dit"])


def schritte(name: str | None = None) -> int:
    """Gespeicherte Schrittzahl der Bauart (`bild_<bauart>_schritte`), sonst die Vorgabe des Modells."""
    m = modelle().get(name or gewaehlt() or "")
    bauart = m["bauart"] if m else "zimage"
    modul = BAUARTEN[bauart]
    standard = vorgaben(name)["schritte"]
    try:
        import config
        n = int(config.load().get(f"bild_{bauart}_schritte") or standard)
    except Exception:
        n = standard
    lo, hi = modul.SCHRITTE
    return min(max(n, lo), hi)


# ===========================================================================
#  Prüfen
# ===========================================================================

def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 22), b""):
            h.update(stueck)
    return h.hexdigest()


def _bekannt(pfad: Path) -> tuple | None:
    for modul in BAUARTEN.values():
        if pfad.name in modul.BEKANNT:
            return modul.BEKANNT[pfad.name]
    return None


def geprueft(pfad: Path) -> bool | None:
    """True: bekannt und geprüft · False: Abweichung · None: unbekannt oder noch nicht geprüft."""
    soll = _bekannt(pfad)
    if soll is None:
        return None
    if pfad.stat().st_size != soll[0]:
        return False
    try:
        stand = json.loads((pfad.parent / GEPRUEFT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    st = pfad.stat()
    return True if stand.get(pfad.name) == [st.st_size, st.st_mtime_ns, soll[1]] else None


def pruefen(pfad: Path, on_status=None) -> None:
    """Bekannte Datei: SHA-256 einmal je Dateistand prüfen; wirft bei Abweichung."""
    soll = _bekannt(pfad)
    if soll is None or geprueft(pfad):
        return
    st = pfad.stat()
    if st.st_size != soll[0]:
        raise RuntimeError(f"{pfad.name}: Größe stimmt nicht ({st.st_size:,} statt {soll[0]:,} Bytes) – "
                           "unvollständig oder eine andere Datei".replace(",", "."))
    if on_status:
        on_status(f"Prüfe {pfad.name} (SHA-256, einmalig) …")
    if _sha256(pfad) != soll[1]:
        raise RuntimeError(f"{pfad.name}: SHA-256 stimmt nicht – beschädigt oder verändert. Bitte neu laden.")
    merker = pfad.parent / GEPRUEFT
    try:
        stand = json.loads(merker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stand = {}
    stand[pfad.name] = [st.st_size, st.st_mtime_ns, soll[1]]
    try:
        merker.write_text(json.dumps(stand, indent=1), encoding="utf-8")
    except OSError:
        pass


def missing_reason(name: str | None = None) -> str | None:
    """None, wenn das Modell malen kann – sonst, was fehlt."""
    for mod, nett in (("torch", "PyTorch"), ("safetensors", "safetensors"), ("tokenizers", "tokenizers"),
                      ("PIL", "Pillow"), ("numpy", "numpy")):
        try:
            __import__(mod)
        except Exception:
            return f"Es fehlt: {nett}."
    if not modelle():
        return f"Kein Bild-Modell in {ordner()}."
    t = teile(name)
    if not t:
        return f"Bild-Modell '{name}' nicht gefunden."
    modul = BAUARTEN[t["bauart"]]
    if t["text"] is None:
        return f"Text-Encoder fehlt: {modul.ZUBEHOER['text']} nach {ordner() / 'text_encoders'}"
    if t["vae"] is None:
        return f"VAE fehlt: {modul.ZUBEHOER['vae']} nach {ordner() / 'vae'}"
    if "clip" in t and t["clip"] is None:
        return f"CLIP fehlt: {modul.ZUBEHOER['clip']} nach {ordner() / 'text_encoders'}"
    for art, inhalt in getattr(modul, "TOKENIZER", {}).items():
        if not any((t[art] / d).exists() for d in ("tokenizer.json", "vocab.json")):
            return f"Tokenizer fehlt: {t[art]} ({inhalt})"
    if not hasattr(modul, "TOKENIZER") and t["text"].suffix.lower() != ".gguf":
        from .textenc import tokenizer_da
        if not tokenizer_da(t["tokenizer"]):
            return (f"Tokenizer fehlt: {t['tokenizer']} (tokenizer.json) – oder den Text-Encoder als .gguf "
                    "nehmen, dort steckt er drin")
    for p in (t["dit"], t["text"], t["vae"], t.get("clip")):
        if p is not None and geprueft(p) is False:
            return f"{p.name}: Größe stimmt nicht – unvollständig oder eine andere Datei."
    return None


# ===========================================================================
#  Malen
# ===========================================================================

def _entladen_nach() -> float:
    try:
        import config
        return float(config.load().get("bild_motor_entladen", ENTLADEN_NACH))
    except Exception:
        return float(ENTLADEN_NACH)


def entladen() -> None:
    from . import gemeinsam
    gemeinsam.entladen()


def im_ram() -> list[str]:
    from . import gemeinsam
    return gemeinsam.im_ram()


def generate(prompt: str, *, model: str | None = None, steps: int | None = None,
             size: tuple[int, int] | None = None, seed: int | None = None, on_status=None) -> str:
    """Malt EIN Bild mit dem gewählten Bild-Modell und gibt den Pfad zurück. Blockiert."""
    from . import gemeinsam
    name = _aufloesen(model)
    grund = missing_reason(name)
    if grund:
        raise RuntimeError(grund)
    t = teile(name)
    modul = BAUARTEN[t["bauart"]]
    n = int(steps or schritte(name))
    lo, hi = modul.SCHRITTE
    n = min(max(n, lo), hi)
    W, H = size or vorgaben(name)["groesse"]
    W = (min(max(int(W), 256), MAX_KANTE) // 16) * 16
    H = (min(max(int(H), 256), MAX_KANTE) // 16) * 16
    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    fehler = None
    with gemeinsam.SPERRE:
        gemeinsam.uhr_halt()
        try:
            for p in (t["dit"], t["text"], t["vae"], t.get("clip")):
                if p is not None:
                    pruefen(p, on_status)
            device, dtype = gemeinsam.geraet()
            arr = modul.malen(prompt, t, schritte=n, breite=W, hoehe=H, seed=seed,
                              device=device, dtype=dtype, on_status=on_status)
        except FormatFehler as e:
            fehler = str(e)
        except Exception as e:
            fehler = f"{type(e).__name__}: {e}"         # nur der Text: der Traceback hielte Tensoren fest
        try:
            gemeinsam.aufraeumen()
        except Exception:
            pass
        gemeinsam.uhr_neu(_entladen_nach())
        if fehler:
            raise RuntimeError(f"{modul.TITEL}: {fehler}")

    from PIL import Image, PngImagePlugin
    import profilordner as _profil
    out = _profil.bild_ziel(_ROOT / "Bilder", ".png")
    meta = PngImagePlugin.PngInfo()
    meta.add_text("prompt", prompt)
    meta.add_text("model", t["name"])
    meta.add_text("params", f"steps={n}, size={W}x{H}, seed={seed}, backend={t['bauart']}")
    Image.fromarray(arr).save(out, pnginfo=meta)
    return str(out)


def _aufloesen(model: str | None) -> str | None:
    """Ein ungenauer Name soll das Bild nicht verhindern: Teilstring, sonst das gewählte Modell."""
    alle = modelle()
    if model and model not in alle:
        kurz = Path(str(model).replace("\\", "/")).stem.lower()
        treffer = [k for k in alle if kurz and kurz in k.lower()]
        model = treffer[0] if len(treffer) == 1 else None
    return model or gewaehlt()


# ===========================================================================
#  Ordner-Hinweis
# ===========================================================================

LIES_MICH = """\
=== Bild-Modelle neben Krea 2 ===

Ein Unterordner je Modell, darin die Modelldatei (.safetensors oder .gguf).
Text-Encoder und VAE einmal geteilt hier in text_encoders/ und vae/ – oder im
Modellordner. NemiCLI erkennt die Bauart an der Datei, nicht am Namen.

Z-Image (z. B. Z-Image-Turbo):
  ZImageTurbo/z_image_turbo_bf16.safetensors   (oder NVFP4, oder eine GGUF Q4…Q8)
  text_encoders/qwen_3_4b.safetensors          (oder Qwen3-4B-Q8_0.gguf)
  vae/ae.safetensors
  tokenizer/tokenizer.json                     (nur bei Text-Encoder als .safetensors)

FLUX.2 klein (4B oder 9B):
  Klein4B/flux-2-klein-4b.safetensors          (oder FP8, oder eine GGUF Q2…Q8)
  text_encoders/qwen_3_4b.safetensors          (9B: qwen_3_8b.safetensors oder Qwen3-8B-Q8_0.gguf)
  vae/flux2-vae.safetensors
  Fassungen mit „base“ im Namen rechnen mit 50 Schritten und CFG.

FLUX.1 (dev, schnell und Abwandlungen wie FLUX.1 Krea dev):
  FluxDev/flux1-dev.safetensors                (oder eine GGUF Q4…Q8)
  text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors   (oder t5-v1_1-xxl-encoder-Q8_0.gguf)
  text_encoders/clip_l.safetensors
  vae/ae.safetensors                           (dieselbe wie bei Z-Image)
  tokenizer_t5/tokenizer.json                  (aus FLUX.1-schnell, Ordner tokenizer_2)
  tokenizer_clip/vocab.json + merges.txt       (aus FLUX.1-schnell, Ordner tokenizer)

Chroma (z. B. Chroma1-HD): wie FLUX.1, aber ohne CLIP:
  Chroma/Chroma1-HD.safetensors                (oder eine GGUF)
  text_encoders/ T5-XXL, vae/ae.safetensors, tokenizer_t5/tokenizer.json

Qwen-Image (auch Qwen-Image-2512, 20B – mit wenig Grafikspeicher eine GGUF Q4_K_M):
  QwenImage/qwen_image_fp8_e4m3fn.safetensors  (oder NVFP4, oder eine GGUF)
  text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors   (oder Qwen2.5-VL-7B-Instruct-Q8_0.gguf)
  vae/qwen_image_vae.safetensors               (liegt sie schon bei Krea 2, wird sie dort gefunden)

Quellen: huggingface.co/Comfy-Org/z_image_turbo, huggingface.co/Comfy-Org/Flux-2-Klein-4B,
huggingface.co/Comfy-Org/Qwen-Image_ComfyUI, huggingface.co/QuantStack/Qwen-Image-GGUF,
huggingface.co/unsloth/FLUX.2-klein-4B-GGUF, huggingface.co/unsloth/Z-Image-Turbo-GGUF,
huggingface.co/unsloth/Qwen3-4B-GGUF, Tokenizer aus huggingface.co/Tongyi-MAI/Z-Image-Turbo.
Bekannte Dateien prüft NemiCLI vor dem ersten Laden per SHA-256.

Wenig Grafikspeicher? Eine GGUF (Q4_K_M) nehmen. Was nicht auf die Karte passt,
schiebt NemiCLI Block für Block aus dem Arbeitsspeicher nach.

Danach: /bildmodel -> Modell wählen.
"""


def ordner_anlegen() -> Path:
    o = ordner()
    for unter in GETEILT:
        (o / unter).mkdir(parents=True, exist_ok=True)
    zettel = o / "LIES-MICH.txt"
    if not zettel.exists():
        try:
            zettel.write_text(LIES_MICH, encoding="utf-8")
        except OSError:
            pass
    return o
