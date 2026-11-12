"""
imagegen.py - Weiche für den Bild-Motor: wer malt, mit welchen Angaben.

  krea   eigene Krea-2-Pipeline (krea.py) – der Standard
  motor  weitere eigene Bild-Motoren, je Bauart einer (bildmotoren/: Z-Image …)
  webui  Forge/A1111 über /sdapi/v1/…    (sdwebui.py)
  comfy  ComfyUI über /prompt + /history  (comfyui.py)
  cloud  OpenAI, Google, xAI              (videobildcloud.py) – ohne eigene Grafikkarte

Eine externe Stelle gilt nur, wenn sie ausdrücklich gewählt ist UND gerade läuft –
sonst malt Krea 2.
"""

from __future__ import annotations

import os
import re
import sys


def _ollama_freigeben() -> None:
    """Ein lokales Ollama hält sein Modell nach jeder Antwort minutenlang im Grafikspeicher – daneben passt
    Krea 2 nicht, und unter WDDM gibt es dann keinen Fehler, sondern stilles Auslagern (Hänger)."""
    try:
        import config
        if not config.load().get("ollama_beim_malen_entladen", True):
            return
        import providers
        namen = providers.ollama_gpu_freigeben()
    except Exception as e:
        namen = []
        try:
            import debugbericht
            debugbericht.fehler("Ollama entladen", e)
        except Exception:
            pass
    if namen:
        try:
            import debugbericht
            debugbericht.ereignis(f"Ollama-Modell für die GPU-Arbeit entladen: {', '.join(namen)}")
        except Exception:
            pass


def _gpu_fuer_bild(fn):
    """Beim Malen hat das Bildmodell die GPU allein: ein geladenes lokales
    Sprachmodell (gguflokal) wartet solange im RAM, ein Ollama-Modell wird entladen.
    Andere NemiCLI-Prozesse (Mal-Prozess der Wache) malen nicht gleichzeitig (malsperre)."""
    import functools

    name = getattr(fn, "__name__", "")
    ist_paint = name == "paint"

    @functools.wraps(fn)
    def huelle(*args, **kw):
        if ist_paint and backend() == CLOUD:
            return fn(*args, **kw)                 # die Cloud braucht die Grafikkarte nicht
        import malkind
        import malsperre
        if name in ("paint", "gesicht_nachbessern") and malkind.aktiv():
            return _gpu_vorbereitet(fn, args, kw)  # die Wache gibt ab: ihr Mal-Prozess hält die Sperre
        im_kind = bool(os.environ.get(malkind.ENV))    # Mal-Prozess der Wache: nicht ewig warten
        with malsperre.halten(kw.get("on_status"), name or "Bild",
                              max_warten=malkind.MAX_WARTEN_S if im_kind else None):
            return _gpu_vorbereitet(fn, args, kw)
    return huelle


def _gpu_vorbereitet(fn, args, kw):
    zustand = sys.modules.get("gpuzustand")
    if zustand is not None:
        zustand.sicherstellen()                    # nach einem CUDA-Speicherfehler malt nichts mehr
    st = sys.modules.get("stimme")
    if st is not None:
        st.gpu_freigeben()                         # die Stimme lädt beim nächsten Satz neu
    _ollama_freigeben()                            # Ollama lädt beim nächsten Chat selbst neu
    ix = sys.modules.get("indexdb")
    if ix is not None:
        ix.unterbrechen()                          # der Bibliothekar gibt eine GPU-Fassung des Encoders frei
    lokal = sys.modules.get("gguflokal")
    if lokal is None:
        return fn(*args, **kw)
    with lokal.gpu_fuer_bild():
        return fn(*args, **kw)


_EXTERN = {"webui": "sdwebui", "comfy": "comfyui", "cloud": "videobildcloud"}
KREA = "krea"
MOTOR = "motor"
CLOUD = "cloud"


def _extern_modul(key: str):
    import importlib
    return importlib.import_module(_EXTERN[key])


def gewaehltes_backend() -> str:
    """Was der Nutzer gewählt hat – egal ob es gerade läuft ('krea' ohne Wahl)."""
    try:
        import config
        gewaehlt = str(config.load().get("bild_backend") or "")
        return gewaehlt if gewaehlt in _EXTERN or gewaehlt == MOTOR else KREA
    except Exception:
        return KREA


def _motor_bereit() -> bool:
    try:
        import bildmotoren
        return bildmotoren.missing_reason() is None
    except Exception:
        return False


def backend() -> str:
    """Der Motor, der JETZT malen würde: 'krea' · 'motor' · 'webui' · 'comfy' · 'cloud'.
    Ist eine externe Stelle oder ein Bild-Motor gewählt, aber nicht bereit, malt Krea 2."""
    gewaehlt = gewaehltes_backend()
    if gewaehlt == MOTOR:
        return MOTOR if _motor_bereit() else KREA
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
    if gewaehlt == MOTOR:
        import bildmotoren
        grund_motor = bildmotoren.missing_reason()
        if grund_motor is None or krea.missing_reason() is None:
            return None                       # sonst springt Krea 2 ein
        return f"{grund_motor}\nRichte das Bild-Modell ein oder wähle ein anderes (/bildmodel)."
    if gewaehlt in _EXTERN:
        try:
            if _extern_modul(gewaehlt).available():
                return None
        except Exception:
            pass
        grund = krea.missing_reason()
        if grund is None:
            return None                       # Krea 2 springt ein
        name = {"comfy": "ComfyUI", "cloud": "die Cloud (kein Schlüssel)"}.get(gewaehlt, "die WebUI")
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
    if art == CLOUD:
        import videobildcloud
        return videobildcloud.label("bild")
    if art == "webui":
        import sdwebui
        return f"🌐 WebUI · {sdwebui.chosen_model() or sdwebui.current_model() or '?'}"
    if art == "comfy":
        import comfyui
        return f"🧩 ComfyUI · {comfyui.chosen_model() or comfyui.resolve_model(None) or '?'}"
    if art == MOTOR:
        import bildmotoren
        return f"{bildmotoren.titel()} · {bildmotoren.gewaehlt() or '?'}"
    import krea
    return f"🟣 Krea 2 · {krea.chosen_model() or '?'}"


def menu() -> dict[str, str]:
    """Eigene Bild-Modelle für die Autovervollständigung (ohne Netzaufruf)."""
    aus: dict[str, str] = {}
    try:
        import krea
        aus.update({n: "🟣 Krea 2" for n in krea.discover()})
    except Exception:
        pass
    try:
        import bildmotoren
        aus.update({n: bildmotoren.titel(n) for n in bildmotoren.modelle()})
    except Exception:
        pass
    return aus


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
    elif art == MOTOR:
        import bildmotoren
        namen = list(bildmotoren.modelle())
    else:
        import krea
        namen = list(krea.discover())
    kurz = re.split(r"[\\/]", str(wunsch).strip().strip('"\''))[-1]
    kurz = re.sub(r"\.(safetensors|gguf)$", "", kurz, flags=re.I).lower()
    treffer = [n for n in namen if kurz and (kurz in n.lower() or n.lower() in kurz)]
    if len(treffer) == 1:
        return treffer[0], ""
    return None, f" (Modell '{wunsch}' kenne ich nicht – Standard-Modell genommen.)"


@_gpu_fuer_bild
def paint(prompt: str, *, model: str | None = None, neg: str | None = None,
          steps: int | None = None, cfg: float | None = None,
          size: tuple[int, int] | None = None, seed: int | None = None,
          sampler: str | None = None, on_status=None, vorlage: str | None = None,
          staerke: float | None = None, figur: bool = False, lora: str | None = None) -> str:
    """Malt EIN Bild mit dem aktiven Motor und gibt den Pfad zurück. Krea 2 und die Bild-Motoren
    nehmen keinen Negativ-Prompt und keine CFG; die externen Stellen schon. `vorlage`/`staerke`
    (Bild umzeichnen) nur mit Krea 2 – ist ein Bild-Motor gewählt, zeichnet Krea 2 um, wenn es da ist.
    `figur`: die Persönlichkeit selbst ist im Bild – Krea nimmt dann ihre Charakter-LoRA dazu, falls es eine gibt.
    `lora`: eine bestimmte Bild-LoRA (lorabild) – geht vor; nur Krea 2 kann sie (lora_moeglich).
    Ein geladenes lokales Sprachmodell wartet solange im RAM (gguflokal) – nicht bei der Cloud; dort gilt
    auch kein Modellname (er gehört zu Krea/WebUI/ComfyUI), der Anbieter nimmt sein Modell."""
    art = backend()
    if art == CLOUD:
        return _extern_modul(art).generate(prompt, neg=neg, size=size, on_status=on_status)
    if art in _EXTERN:
        return _extern_modul(art).generate(
            prompt, model=model, neg=neg, steps=steps, cfg=cfg,
            size=size, seed=seed, sampler=sampler, on_status=on_status)
    import malkind
    if malkind.aktiv():                        # Wache: im kurzen Kindprozess, ihr CUDA-Kontext endet mit ihm
        return malkind.auftrag("paint", {"prompt": prompt, "model": model, "steps": steps,
                                         "size": list(size) if size else None, "seed": seed,
                                         "sampler": sampler, "vorlage": vorlage, "staerke": staerke,
                                         "figur": figur, "lora": str(lora) if lora else None}, on_status)
    import krea
    if art == MOTOR and not (vorlage and krea.missing_reason() is None):
        import bildmotoren
        if krea.im_ram():
            krea.entladen()                    # zwei Bildnetze zugleich im Arbeitsspeicher wären zu viel
        return bildmotoren.generate(prompt, model=model, steps=steps, size=size, seed=seed, on_status=on_status)
    _motoren_entladen()
    extra ={"vorlage": vorlage, "staerke": staerke} if vorlage and staerke is not None else (
        {"vorlage": vorlage} if vorlage else {})
    if lora:
        extra["lora"] = lora
    elif figur and (eigene := krea.lora_fuer_figur()):
        extra["lora"] = eigene
    return krea.generate(prompt, model=model, steps=steps, size=size, seed=seed,
                         sampler=sampler if sampler in ("euler", "euler_ancestral") else None,
                         on_status=on_status, **extra)


def lora_moeglich(vorlage: str | None = None) -> bool:
    """Malt jetzt Krea 2? Nur dann wirkt eine Bild-LoRA – bei einem anderen Motor fehlte sonst das Aussehen."""
    art = backend()
    if art == KREA:
        return True
    if art == MOTOR and vorlage:                 # Umzeichnen übernimmt Krea 2, wenn es da ist
        import krea
        return krea.missing_reason() is None
    return False


def _motoren_entladen() -> None:
    """Krea 2 malt: die Bild-Motoren geben ihren Arbeitsspeicher frei (nur wenn sie geladen sind)."""
    mod = sys.modules.get("bildmotoren")
    if mod is not None and mod.im_ram():
        mod.entladen()


@_gpu_fuer_bild
def gesicht_nachbessern(path: str, on_status=None) -> str | None:
    """Gesichter eines Krea-2-Bildes nachmalen (nur Krea, nur wenn eingeschaltet).
    Gibt den Pfad der nachgebesserten Kopie zurück, sonst None."""
    import krea
    if "backend=krea2" not in _params(path) or not krea.gesicht_an():
        return None
    import malkind
    if malkind.aktiv():
        return malkind.auftrag("gesicht_nachbessern", {"path": path}, on_status)
    return krea.gesicht_nachbessern(path, on_status=on_status)


def _params(path: str) -> str:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return str((getattr(im, "text", {}) or {}).get("params", ""))
    except Exception:
        return ""


def parse_opts(text: str) -> dict:
    """Zieht --neg/--steps/--cfg/--size/--seed/--model/--sampler/--ohne-gesicht/--figur aus dem Text.
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
    take(r'(--figur)\b', lambda v: {"figur": True})

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
