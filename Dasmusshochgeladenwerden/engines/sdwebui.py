"""
sdwebui.py - Bilder über eine externe Forge/A1111-WebUI (Standard: 127.0.0.1:7860).

Das ist ein GANZ EIGENER Bild-Weg, getrennt von NemiCLIs eigener SD1.5/SDXL-
Pipeline (engines/imagegen.py). Grund: die WebUI ist anders gebaut. Sie fährt
z.B. Krea-/Qwen-Modelle, die die eigene Pipeline nicht kann, und will andere
Werte – standardmäßig **kein Negativ-Prompt, CFG 1, 1024×1024, 14 Schritte**.

Nur die REST-Schnittstelle wird genutzt (`/sdapi/v1/...`). NemiCLI startet die
WebUI nicht und steuert sie nicht fern – sie muss schon laufen. Erreichbar ist
nur `127.0.0.1` (lokal); nichts geht nach außen.

Was hier passiert:
  · available()      – läuft die WebUI überhaupt?
  · models()         – welche Checkpoints kennt sie?
  · current_model()  – welcher ist gerade geladen?
  · set_model()      – Checkpoint umschalten (dauert, lädt das Modell)
  · generate()       – EIN Bild malen, in Bilder/ speichern, Pfad zurückgeben
"""

from __future__ import annotations

import base64
import struct
import threading
import time
import zlib
from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

OUT_DIR = _ROOT / "Bilder"
DEFAULT_HOST = "http://127.0.0.1:7860"

# Bewusst ANDERE Vorgaben als die eigene Pipeline – passend zu Krea/Qwen & Co.
# Vorgaben = das erprobte Forge-Neo-Setup aus dem MCP-Skript des Entwicklers (Test, 13.09.2026):
# hochkant 832x1216, Euler a, fester Positiv-Vorsatz, fester Negativ-Prompt,
# Sperrwörter. Jeder Wert ist per nemicli.config.json überschreibbar (bild_webui_*).
DEFAULTS = {
    "steps": 14,
    "cfg": 1.0,
    "size": (832, 1216),
    "sampler": "Euler a",
    "scheduler": "Automatic",   # Forge entscheidet (wie im MCP-Skript, das keinen setzt)
    "neg": (
        "multiple heads, double head, duplicate head, extra face, cloned face, hydra, "
        "extra arms, extra legs, extra limbs, duplicate limbs, detached limbs, "
        "bad anatomy, deformed anatomy, malformed body, mutated body, twisted body, "
        "bad hands, malformed hands, extra fingers, missing fingers, fused fingers, "
        "bad feet, malformed feet, extra toes, missing toes, fused toes, "
        "face on body, eyes on body, eyes on feet, eyes on hands, "
        "facial features on limbs, duplicate body, cloned body, conjoined body, "
        "fused body, overlapping body, dislocated joints, broken joints, "
        "unnatural proportions, anatomical glitch, surreal anatomy, body horror"
    ),
    # Steht IMMER vorn im Prompt (Doppelte aus dem Modell-Prompt werden entfernt).
    "positiv": (
        "1girl, solo, woman, masterpiece, best quality, ultra detailed, high detail, "
        "photorealistic, sharp focus, detailed skin, highly detailed, 8k, "
        "beautiful detailed eyes, detailed pupils, symmetric eyes, sharp eyes"
    ),
}

# Diese Tags fliegen aus jedem Prompt – im Code, egal was das Modell schreibt
# (feste Bild-Regeln des Entwicklers: eine erwachsene Frau, keine Männer, keine Gruppen).
# Zusätzlich alles, was "pov" enthält.
STRIP_TAGS = {
    "male pov", "1boy", "2boys", "2girls", "3girls", "man", "men", "male", "boy", "boys",
    "hetero", "penetration", "penis", "cock", "dick", "couple", "multiple people",
    "group", "crowd",
}


def merge_prompt(prompt: str, positiv: str | None = None) -> str:
    """Positiv-Vorsatz + Modell-Prompt, kommagetrennt, ohne Doppelte, ohne Sperrwörter."""
    vorsatz = _wcfg("positiv", DEFAULTS["positiv"]) if positiv is None else positiv
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in (vorsatz or "", prompt or ""):
        for tag in chunk.split(","):
            t = tag.strip()
            if not t:
                continue
            key = t.lower()
            if key in seen or key in STRIP_TAGS or "pov" in key:
                continue
            seen.add(key)
            parts.append(t)
    return ", ".join(parts)


def stripped_tags(prompt: str) -> list[str]:
    """Welche Tags würden aus diesem Prompt gestrichen? (für Hinweise/Tests)"""
    out: list[str] = []
    for tag in (prompt or "").split(","):
        t = tag.strip()
        if t and (t.lower() in STRIP_TAGS or "pov" in t.lower()):
            out.append(t)
    return out

_CONFIG_KEY_HOST = "bild_webui_host"
_CONFIG_KEY_MODEL = "bild_webui_model"


def _wcfg(key: str, fallback):
    """Erlaubt, die WebUI-Werte pro Nutzer festzunageln (nemicli.config.json:
    'bild_webui_steps', '..._cfg', '..._sampler', '..._scheduler'). So kann jeder
    NemiCLI exakt an sein Forge-Setup anpassen, statt unsere Vorgaben zu erben."""
    try:
        import config
        v = config.load().get(f"bild_webui_{key}")
        return v if v not in (None, "") else fallback
    except Exception:
        return fallback


def host() -> str:
    """WebUI-Adresse aus der Konfig (Standard 127.0.0.1:7860)."""
    try:
        import config
        h = str(config.load().get(_CONFIG_KEY_HOST) or "").strip()
        if h:
            return h.rstrip("/")
    except Exception:
        pass
    return DEFAULT_HOST


def set_host(url: str) -> None:
    import config
    config.update(**{_CONFIG_KEY_HOST: (url or "").strip().rstrip("/") or DEFAULT_HOST})


def chosen_model() -> str | None:
    """Das per /bildmodel gewählte WebUI-Modell (model_name), falls eins."""
    try:
        import config
        return config.load().get(_CONFIG_KEY_MODEL) or None
    except Exception:
        return None


def set_chosen_model(model_name: str | None) -> None:
    import config
    config.update(**{_CONFIG_KEY_MODEL: model_name})


# ---------------------------------------------------------------------------
# HTTP (httpx ist ohnehin eine Kern-Abhängigkeit)
# ---------------------------------------------------------------------------

def _client(timeout: float):
    import httpx
    return httpx.Client(timeout=timeout, base_url=host())


def available(timeout: float = 2.5) -> bool:
    """Läuft die WebUI und antwortet ihre API?"""
    try:
        with _client(timeout) as c:
            r = c.get("/sdapi/v1/sd-models")
            return r.status_code == 200
    except Exception:
        return False


def models() -> dict[str, str]:
    """model_name -> Anzeigename (Titel mit Hash). Leer, wenn nicht erreichbar."""
    try:
        with _client(6.0) as c:
            r = c.get("/sdapi/v1/sd-models")
            r.raise_for_status()
            data = r.json()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for m in data:
        name = m.get("model_name") or m.get("title", "")
        if name:
            out[name] = m.get("title", name)
    return out


def current_model() -> str | None:
    try:
        with _client(6.0) as c:
            r = c.get("/sdapi/v1/options")
            r.raise_for_status()
            val = r.json().get("sd_model_checkpoint")
            return str(val) if val else None
    except Exception:
        return None


def _resolve_title(model_name: str | None) -> str | None:
    """Findet den vollständigen Titel (mit Hash) zu einem model_name."""
    if not model_name:
        return None
    mods = models()
    if model_name in mods:
        return mods[model_name]                       # exakter model_name
    for name, title in mods.items():
        if model_name.lower() in (name.lower(), title.lower()):
            return title
    for name, title in mods.items():                  # Teilstring
        if model_name.lower() in name.lower():
            return title
    return None


def set_model(model_name: str, on_status=None) -> bool:
    """Checkpoint in der WebUI umschalten (lädt das Modell – kann dauern)."""
    title = _resolve_title(model_name) or model_name
    try:
        if on_status:
            on_status(f"lade Modell {model_name} …")
        with _client(300.0) as c:
            r = c.post("/sdapi/v1/options", json={"sd_model_checkpoint": title})
            return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Malen
# ---------------------------------------------------------------------------

def _progress_poller(stop: threading.Event, on_status):
    """Fragt im Hintergrund den Fortschritt ab, während txt2img blockiert."""
    while not stop.is_set():
        try:
            with _client(4.0) as c:
                r = c.get("/sdapi/v1/progress")
                if r.status_code == 200:
                    p = r.json()
                    frac = p.get("progress") or 0.0
                    step = (p.get("state") or {}).get("sampling_step")
                    total = (p.get("state") or {}).get("sampling_steps")
                    if total:
                        on_status(f"male dein Bild … Schritt {step}/{total}  "
                                  f"({int(frac * 100)} %)")
                    else:
                        on_status(f"male dein Bild … ({int(frac * 100)} %)")
        except Exception:
            pass
        stop.wait(0.5)


def generate(prompt: str, *, model: str | None = None, neg: str | None = None,
             steps: int | None = None, cfg: float | None = None,
             size: tuple[int, int] | None = None, seed: int | None = None,
             sampler: str | None = None, on_status=None) -> str:
    """Malt EIN Bild über die WebUI und gibt den gespeicherten Pfad zurück.
    Blockiert – im Thread aufrufen. Wirft RuntimeError bei Problemen."""
    if not prompt or not prompt.strip():
        raise RuntimeError("Kein Prompt angegeben.")
    if not available():
        raise RuntimeError(
            f"Die Bild-WebUI ist unter {host()} nicht erreichbar. Starte Forge/A1111 "
            "(mit --api) und versuch es nochmal. Anderer Port? /bildmodel → WebUI-Adresse.")

    want = model or chosen_model()
    if want:
        # Nur umschalten, wenn nicht ohnehin schon geladen (Modellwechsel dauert).
        cur = current_model() or ""
        title = _resolve_title(want)
        if title and title.lower() not in cur.lower():
            if not set_model(want, on_status):
                raise RuntimeError(f"Konnte das WebUI-Modell '{want}' nicht laden.")

    W, H = size or _wcfg("size", DEFAULTS["size"])
    if isinstance((W, H), tuple) and isinstance(W, str):        # "832x1216" aus der Config
        W, H = (int(x) for x in W.lower().split("x"))
    payload = {
        "prompt": merge_prompt(prompt),
        "negative_prompt": (neg if neg is not None else _wcfg("neg", DEFAULTS["neg"])) or "",
        "steps": int(steps or _wcfg("steps", DEFAULTS["steps"])),
        "cfg_scale": float(_wcfg("cfg", DEFAULTS["cfg"]) if cfg is None else cfg),
        "width": int(W),
        "height": int(H),
        "sampler_name": sampler or _wcfg("sampler", DEFAULTS["sampler"]),
        "scheduler": _wcfg("scheduler", DEFAULTS["scheduler"]),
        "seed": int(seed) if seed is not None else -1,
        "batch_size": 1,
        "n_iter": 1,
        "save_images": False,
    }

    stop = threading.Event()
    poller = None
    if on_status:
        on_status("male dein Bild (WebUI) …")
        poller = threading.Thread(target=_progress_poller, args=(stop, on_status), daemon=True)
        poller.start()
    try:
        with _client(900.0) as c:
            r = c.post("/sdapi/v1/txt2img", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise RuntimeError(f"WebUI-Malen fehlgeschlagen: {e}")
    finally:
        stop.set()
        if poller is not None:
            poller.join(timeout=1.0)

    imgs = data.get("images") or []
    if not imgs:
        raise RuntimeError("Die WebUI hat kein Bild zurückgegeben.")
    return _save(imgs[0], payload, want or current_model() or "webui", data.get("info"), on_status)


_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_text_chunk(key: str, value: str) -> bytes:
    """Ein PNG-tEXt-Chunk (Länge + Typ + Daten + CRC) – reines Python, ohne PIL."""
    data = key.encode("latin-1", "replace") + b"\x00" + value.encode("latin-1", "replace")
    crc = zlib.crc32(b"tEXt" + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + b"tEXt" + data + struct.pack(">I", crc)


def _png_with_text(png: bytes, texte: dict[str, str]) -> bytes:
    """Fügt tEXt-Chunks direkt nach dem IHDR ein. Kein PIL nötig – die WebUI
    liefert bereits ein fertiges PNG, wir hängen nur unsere Notizen an."""
    if png[:8] != _PNG_SIG:
        return png                               # kein PNG: unverändert lassen
    # erster Chunk ist IHDR: 8 (Sig) + 4 (Länge) + 4 (Typ) + <länge> + 4 (CRC)
    ihdr_len = struct.unpack(">I", png[8:12])[0]
    ende_ihdr = 8 + 4 + 4 + ihdr_len + 4
    zusatz = b"".join(_png_text_chunk(k, v) for k, v in texte.items() if v)
    return png[:ende_ihdr] + zusatz + png[ende_ihdr:]


def _save(b64: str, payload: dict, model_name: str, info, on_status=None) -> str:
    # Die WebUI liefert ein FERTIGES PNG (base64). Wir brauchen weder PIL noch
    # torch – nur die Bytes speichern und unsere Metadaten anhängen. So läuft der
    # WebUI-Weg auch in der schlanken exe, in der PIL bewusst nicht mitkommt.
    raw = base64.b64decode(b64.split(",", 1)[-1])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    seed = payload.get("seed")
    if seed in (None, -1):                       # echten Seed aus der Antwort holen
        try:
            import json
            seed = json.loads(info).get("seed", seed)
        except Exception:
            pass
    texte = {
        "prompt": payload.get("prompt", ""),
        "negative": payload.get("negative_prompt", ""),
        "model": str(model_name),
        "params": (f"steps={payload['steps']}, cfg={payload['cfg_scale']}, "
                   f"size={payload['width']}x{payload['height']}, seed={seed}, "
                   f"sampler={payload['sampler_name']}, scheduler={payload.get('scheduler','')}, "
                   f"backend=webui"),
    }
    if isinstance(info, str):
        texte["webui_info"] = info[:4000]
    ext = ".png" if raw[:8] == _PNG_SIG else ".jpg"
    out = OUT_DIR / f"nemi_webui_{stamp}_{seed}{ext}"
    out.write_bytes(_png_with_text(raw, texte) if ext == ".png" else raw)
    return str(out)
