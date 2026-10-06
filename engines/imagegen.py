"""
imagegen.py - Weiche für den Bild-Motor: wer malt, mit welchen Angaben.

  krea   eigene Krea-2-Pipeline (krea.py) – der Standard
  webui  Forge/A1111 über /sdapi/v1/…    (sdwebui.py)
  comfy  ComfyUI über /prompt + /history  (comfyui.py)

Eine externe Stelle gilt nur, wenn sie ausdrücklich gewählt ist UND gerade läuft –
sonst malt Krea 2. Die frühere eigene SD-1.5/SDXL-Pipeline ist entfernt.
"""

from __future__ import annotations

import re


def _gpu_fuer_bild(fn):
    """Beim Malen hat das Bildmodell die GPU allein: ein geladenes lokales
    Sprachmodell (gguflokal) wartet solange im RAM."""
    import functools
    import sys

    @functools.wraps(fn)
    def huelle(*args, **kw):
        lokal = sys.modules.get("gguflokal")
        if lokal is None:
            return fn(*args, **kw)
        with lokal.gpu_fuer_bild():
            return fn(*args, **kw)
    return huelle


_EXTERN = {"webui": "sdwebui", "comfy": "comfyui"}
KREA = "krea"


def _extern_modul(key: str):
    import importlib
    return importlib.import_module(_EXTERN[key])


def gewaehltes_backend() -> str:
    """Was der Nutzer gewählt hat – egal ob es gerade läuft ('krea' ohne Wahl)."""
    try:
        import config
        gewaehlt = str(config.load().get("bild_backend") or "")
        return gewaehlt if gewaehlt in _EXTERN else KREA
    except Exception:
        return KREA


def backend() -> str:
    """Der Motor, der JETZT malen würde: 'krea' · 'webui' · 'comfy'.
    Ist eine externe Stelle gewählt, aber nicht erreichbar, malt Krea 2."""
    gewaehlt = gewaehltes_backend()
    if gewaehlt in _EXTERN:
        try:
            if _extern_modul(gewaehlt).available():
                return gewaehlt
        except Exception:
            pass
    return KREA


def missing_reason_aktiv() -> str | None:
    """Fehlt dem Motor, der jetzt malen würde, etwas? None = er kann malen.
    WebUI/ComfyUI brauchen hier nichts (eigenes Programm per HTTP); Krea 2 braucht
    torch und seine Modelldateien."""
    import krea
    gewaehlt = gewaehltes_backend()
    if gewaehlt in _EXTERN:
        try:
            if _extern_modul(gewaehlt).available():
                return None
        except Exception:
            pass
        grund = krea.missing_reason()
        if grund is None:
            return None                       # Krea 2 springt ein
        name = "ComfyUI" if gewaehlt == "comfy" else "die WebUI"
        try:
            adresse = f" ({_extern_modul(gewaehlt).host()})"
        except Exception:
            adresse = ""
        return (f"{name} ist gewählt, aber nicht erreichbar{adresse} – und Krea 2 kann auch nicht "
                f"malen: {grund}\nStarte {name}, oder richte Krea 2 ein (/bildmodel).")
    return krea.missing_reason()


def active_label() -> str:
    """Menschlicher Name des aktiven Bild-Modells (für Statusausgaben)."""
    art = backend()
    if art == "webui":
        import sdwebui
        return f"🌐 WebUI · {sdwebui.chosen_model() or sdwebui.current_model() or '?'}"
    if art == "comfy":
        import comfyui
        return f"🧩 ComfyUI · {comfyui.chosen_model() or comfyui.resolve_model(None) or '?'}"
    import krea
    return f"🟣 Krea 2 · {krea.chosen_model() or '?'}"


def menu() -> dict[str, str]:
    """Krea-2-Modelle für die Autovervollständigung (ohne Netzaufruf)."""
    try:
        import krea
        return {n: "🟣 Krea 2" for n in krea.discover()}
    except Exception:
        return {}


def modell_aufloesen(wunsch: str | None) -> tuple[str | None, str]:
    """Modellname großzügig auflösen (Teilstring, ohne Pfad/Endung). Passt er beim
    aktiven Motor zu nichts, wird das gewählte Standard-Modell genommen.
    Rückgabe: (Name oder None, Hinweis für die Antwort)."""
    if not wunsch or not str(wunsch).strip():
        return None, ""
    art = backend()
    if art == "webui":
        import sdwebui
        namen = list(sdwebui.models())
    elif art == "comfy":
        import comfyui
        namen = list(comfyui.models())
    else:
        import krea
        namen = list(krea.discover())
    kurz = re.split(r"[\\/]", str(wunsch).strip().strip('"\''))[-1]
    kurz = re.sub(r"\.safetensors$", "", kurz, flags=re.I).lower()
    treffer = [n for n in namen if kurz and (kurz in n.lower() or n.lower() in kurz)]
    if len(treffer) == 1:
        return treffer[0], ""
    return None, f" (Modell '{wunsch}' kenne ich nicht – Standard-Modell genommen.)"


@_gpu_fuer_bild
def paint(prompt: str, *, model: str | None = None, neg: str | None = None,
          steps: int | None = None, cfg: float | None = None,
          size: tuple[int, int] | None = None, seed: int | None = None,
          sampler: str | None = None, on_status=None) -> str:
    """Malt EIN Bild mit dem aktiven Motor und gibt den Pfad zurück. Krea 2 nimmt
    keinen Negativ-Prompt und keine CFG; die externen Stellen schon.
    Ein geladenes lokales Sprachmodell wartet solange im RAM (gguflokal)."""
    art = backend()
    if art in _EXTERN:
        return _extern_modul(art).generate(
            prompt, model=model, neg=neg, steps=steps, cfg=cfg,
            size=size, seed=seed, sampler=sampler, on_status=on_status)
    import krea
    return krea.generate(prompt, model=model, steps=steps, size=size, seed=seed,
                         sampler=sampler if sampler in ("euler", "euler_ancestral") else None,
                         on_status=on_status)


@_gpu_fuer_bild
def gesicht_nachbessern(path: str, on_status=None) -> str | None:
    """Gesichter eines Krea-2-Bildes nachmalen (nur Krea, nur wenn eingeschaltet).
    Gibt den Pfad der nachgebesserten Kopie zurück, sonst None."""
    import krea
    if "backend=krea2" not in _params(path) or not krea.gesicht_an():
        return None
    return krea.gesicht_nachbessern(path, on_status=on_status)


def _params(path: str) -> str:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return str((getattr(im, "text", {}) or {}).get("params", ""))
    except Exception:
        return ""


def parse_opts(text: str) -> dict:
    """Zieht --neg/--steps/--cfg/--size/--seed/--model/--sampler/--ohne-gesicht aus dem Text.
    Rest = Prompt. --neg und --cfg wirken nur bei WebUI/ComfyUI."""
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
    take(r'(--ohne-gesicht|--no-face)\b', lambda v: {"gesicht": False})

    opts["prompt"] = re.sub(r"\s+", " ", text).strip()
    return opts


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
