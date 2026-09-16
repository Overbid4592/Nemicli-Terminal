"""
comfyui.py - Bilder über eine laufende ComfyUI (Standard: 127.0.0.1:8188).

Eigene Stelle neben `sdwebui.py` (Forge/A1111), weil ComfyUI eine völlig
andere Sprache spricht. Forge kennt `--api` und danach `/sdapi/v1/txt2img`:
ein Aufruf rein, fertiges Bild raus. ComfyUI hat diese Adressen NICHT – ein
`GET /sdapi/v1/sd-models` beantwortet es mit 404, und NemiCLI hielt das früher
für „nicht erreichbar". Der Port war immer da; die Frage war die falsche.

ComfyUI arbeitet mit einem **Ablaufplan** (Workflow): man schickt einen Graphen
aus Bausteinen, bekommt eine Auftragsnummer, fragt nach dem Ergebnis und holt
das Bild dann einzeln ab.

    POST /prompt              Ablaufplan abschicken  -> prompt_id
    GET  /history/<id>        fertig? welche Datei?
    GET  /view?filename=...   das Bild holen
    GET  /object_info/<node>  was kann dieses Gerät (Checkpoints, Sampler)
    GET  /system_stats        läuft es überhaupt

Der Ablaufplan hier ist der klassische txt2img-Weg, genau die sieben Bausteine,
die man in ComfyUI auch von Hand zusammenklickt:

    CheckpointLoaderSimple → CLIPTextEncode (positiv)
                           → CLIPTextEncode (negativ)
                           → KSampler ← EmptyLatentImage
                           → VAEDecode → SaveImage

NemiCLI startet ComfyUI NICHT und ändert dort nichts. Es redet nur mit einer
laufenden Instanz auf 127.0.0.1.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path

import httpx

import config
import sdwebui as _SD          # PNG-Metadaten und Bilderordner teilen wir uns

DEFAULT_HOST = "http://127.0.0.1:8188"

# Vorgaben – jede per nemicli.config.json überschreibbar (bild_comfy_*).
DEFAULTS: dict = {
    "steps": 25,
    "cfg": 7.0,
    "size": (1024, 1024),
    "sampler": "euler",
    "scheduler": "normal",
    "neg": ("worst quality, low quality, blurry, deformed, extra limbs, "
            "bad anatomy, bad hands, watermark, text"),
    "positiv": "",
}

# Getrennte Modelle (Krea/Qwen/Flux) wollen ganz andere Werte als ein SDXL-
# Checkpoint: wenige Schritte, CFG 1, hochkant. Dieselben Zahlen, die über
# Forge schon erprobt sind – und ein Negativ-Prompt bringt bei CFG 1 nichts.
DEFAULTS_GETEILT: dict = {
    "steps": 14,
    "cfg": 1.0,
    "size": (832, 1216),
    "sampler": "euler_ancestral",
    "scheduler": "simple",
    "neg": "",
    "positiv": "",
}


def vorgaben(art: str | None = None) -> dict:
    """Die Vorgaben für diese Bauart ('checkpoint' oder 'diffusion')."""
    return DEFAULTS_GETEILT if art == "diffusion" else DEFAULTS

_CONFIG_KEY_HOST = "bild_comfy_host"
_CONFIG_KEY_MODEL = "bild_comfy_model"


def _ccfg(key: str, fallback):
    """Wert aus nemicli.config.json (bild_comfy_<key>), sonst die Vorgabe."""
    try:
        wert = config.load().get(f"bild_comfy_{key}")
    except Exception:
        wert = None
    return fallback if wert in (None, "") else wert


# ===========================================================================
#  Adresse und Modellwahl
# ===========================================================================

def host() -> str:
    """ComfyUI-Adresse aus der Konfig (Standard 127.0.0.1:8188)."""
    try:
        h = str(config.load().get(_CONFIG_KEY_HOST) or "").strip()
    except Exception:
        h = ""
    if h:
        return h if h.startswith("http") else f"http://{h}"
    return DEFAULT_HOST


def set_host(url: str) -> None:
    sauber = (url or "").strip().rstrip("/")
    config.update(**{_CONFIG_KEY_HOST: sauber or DEFAULT_HOST})


def chosen_model() -> str | None:
    try:
        return str(config.load().get(_CONFIG_KEY_MODEL) or "").strip() or None
    except Exception:
        return None


def set_chosen_model(model_name: str | None) -> None:
    config.update(**{_CONFIG_KEY_MODEL: (model_name or "").strip()})


def _client(timeout: float):
    return httpx.Client(base_url=host(), timeout=timeout)


# ===========================================================================
#  Läuft es? Was kann es?
# ===========================================================================

def available(timeout: float = 2.5) -> bool:
    """Läuft ComfyUI und antwortet?

    Gefragt wird `/system_stats` – ComfyUIs eigene Adresse. NICHT
    `/sdapi/v1/...`: das ist die Forge-Sprache, darauf antwortet ComfyUI mit
    404, und daraus wurde früher fälschlich „nicht erreichbar"."""
    try:
        with _client(timeout) as c:
            return c.get("/system_stats").status_code == 200
    except Exception:
        return False


def _node_auswahl(node: str, feld: str) -> list[str]:
    """Die Auswahlliste eines Bausteins (z.B. welche Checkpoints es gibt)."""
    try:
        with _client(15.0) as c:
            r = c.get(f"/object_info/{node}")
            if r.status_code != 200:
                return []
            eintrag = r.json().get(node, {})
            feldwert = eintrag.get("input", {}).get("required", {}).get(feld)
    except Exception:
        return []
    if isinstance(feldwert, list) and feldwert and isinstance(feldwert[0], list):
        return [str(x) for x in feldwert[0]]
    return []


def checkpoints() -> list[str]:
    """Alles-in-einem-Modelle (CheckpointLoaderSimple)."""
    return _node_auswahl("CheckpointLoaderSimple", "ckpt_name")


def diffusion_models() -> list[str]:
    """Reine Diffusions-Modelle (UNETLoader) – der Krea/Qwen/Flux-Weg."""
    return _node_auswahl("UNETLoader", "unet_name")


def clips() -> list[str]:
    return _node_auswahl("CLIPLoader", "clip_name")


def clip_typen() -> list[str]:
    return _node_auswahl("CLIPLoader", "type")


def vaes() -> list[str]:
    """Nur echte VAE-Dateien – Einträge wie 'pixel_space' sind keine Datei."""
    return [v for v in _node_auswahl("VAELoader", "vae_name")
            if v.lower().endswith((".safetensors", ".ckpt", ".pt", ".pth", ".sft"))]


def models() -> dict[str, str]:
    """Alle Modelle, die ComfyUI malen kann – {Name: Art}.

    Zwei Bauarten, beide gleichwertig:
      'checkpoint'  alles in einer Datei (CheckpointLoaderSimple)
      'diffusion'   getrennt: Diffusions-Modell + CLIP + VAE (Krea/Qwen/Flux)

    Genau daran scheiterte es zuerst: NemiCLI suchte nur Checkpoints. Wer
    Krea oder Qwen fährt, hat gar keine - dort liegen Modell, Textteil und
    VAE als drei Dateien nebeneinander, so wie in ComfyUI auch drei Lade-
    Bausteine stehen."""
    raus = {n: "checkpoint" for n in checkpoints()}
    for n in diffusion_models():
        raus.setdefault(n, "diffusion")
    return raus


def model_art(name: str | None = None) -> str | None:
    """'checkpoint' · 'diffusion' · None (nicht gefunden)."""
    alle = models()
    if not alle:
        return None
    wunsch = (name or chosen_model() or "").strip()
    if not wunsch:
        return next(iter(alle.values()))
    if wunsch in alle:
        return alle[wunsch]
    tief = wunsch.lower()
    for n, art in alle.items():
        if tief in n.lower():
            return art
    return next(iter(alle.values()))


def samplers() -> list[str]:
    return _node_auswahl("KSampler", "sampler_name")


def schedulers() -> list[str]:
    return _node_auswahl("KSampler", "scheduler")


def _gpu_name(roh: str) -> str:
    """ComfyUI meldet 'cuda:0 NVIDIA GeForce RTX 5060 Ti : cudaMallocAsync'.
    Uns interessiert der Kartenname in der Mitte, nicht der Speicher-Modus."""
    teile = [t.strip() for t in roh.split(":")]
    if len(teile) >= 3:
        mitte = teile[1].lstrip("0123456789 ").strip()
        if mitte:
            return mitte
    return teile[-1] if teile else roh


def info() -> dict:
    """Kurzer Steckbrief fürs Menü: {version, gpu, vram_gb, checkpoints}."""
    raus = {"version": "?", "gpu": "?", "vram_gb": 0.0, "checkpoints": 0}
    try:
        with _client(5.0) as c:
            r = c.get("/system_stats")
            if r.status_code != 200:
                return raus
            d = r.json()
    except Exception:
        return raus
    raus["version"] = str(d.get("system", {}).get("comfyui_version") or "?")
    geraete = d.get("devices") or []
    if geraete:
        raus["gpu"] = _gpu_name(str(geraete[0].get("name") or "?"))
        raus["vram_gb"] = round(float(geraete[0].get("vram_total") or 0) / 1073741824, 1)
    raus["checkpoints"] = len(models())
    return raus


def _passend(liste: list[str], wunsch: str, fallback: str) -> str:
    """Nimmt den Wunsch, wenn ComfyUI ihn kennt – sonst den Rückfall."""
    if not liste:
        return wunsch
    if wunsch in liste:
        return wunsch
    tief = wunsch.strip().lower()
    for n in liste:
        if n.lower() == tief:
            return n
    return fallback if fallback in liste else liste[0]


def resolve_model(name: str | None) -> str | None:
    """Checkpoint-Name auflösen – auch als Teilstring ('pony' → …Pony….safetensors)."""
    vorhanden = list(models())
    if not vorhanden:
        return None
    wunsch = (name or chosen_model() or "").strip()
    if not wunsch:
        return vorhanden[0]
    for n in vorhanden:
        if n == wunsch:
            return n
    tief = wunsch.lower()
    treffer = [n for n in vorhanden if tief in n.lower()]
    return treffer[0] if treffer else vorhanden[0]


# ===========================================================================
#  Der Ablaufplan
# ===========================================================================

# Welcher CLIP-Typ gehört zu welchem Modell? ComfyUI kann das nicht sagen –
# in der Oberfläche stellt man es von Hand ein. Deshalb hier eine Ableitung aus
# dem Dateinamen, die man per `bild_comfy_cliptype` überstimmen kann.
_CLIP_TYP_HINWEISE = (
    ("krea", "krea2"), ("qwen", "qwen_image"), ("flux", "flux2"),
    ("wan", "wan"), ("chroma", "chroma"), ("hidream", "hidream"),
    ("lumina", "lumina2"), ("sd3", "sd3"), ("hunyuan", "hunyuan_image"),
)


def clip_typ_fuer(modell: str, clip: str = "") -> str:
    """Rät den CLIP-Typ aus den Dateinamen (Config schlägt das immer)."""
    fest = str(_ccfg("cliptype", "") or "").strip()
    vorhanden = clip_typen()
    if fest:
        return _passend(vorhanden, fest, fest)
    suche = f"{modell} {clip}".lower()
    for teil, typ in _CLIP_TYP_HINWEISE:
        if teil in suche and (not vorhanden or typ in vorhanden):
            return typ
    return _passend(vorhanden, "stable_diffusion", "stable_diffusion")


def _eins_von(liste: list[str], wunsch: str, was: str) -> str:
    """Nimmt den Wunsch aus der Config, sonst das einzig Vorhandene."""
    if wunsch and liste:
        return _passend(liste, wunsch, liste[0])
    if wunsch:
        return wunsch
    if not liste:
        raise RuntimeError(
            f"ComfyUI kennt keine {was}-Datei. Für ein getrenntes Modell "
            f"(Krea/Qwen/Flux) braucht es Diffusions-Modell, CLIP und VAE.")
    return liste[0]


def workflow_geteilt(prompt: str, *, unet: str, clip: str, clip_typ: str,
                     vae: str, neg: str, steps: int, cfg: float,
                     width: int, height: int, seed: int, sampler: str,
                     scheduler: str) -> dict:
    """Der Krea/Qwen/Flux-Weg: drei getrennte Lader statt einem Checkpoint.

    Genau der Aufbau, den man in ComfyUI auch von Hand klickt:
        Load Diffusion Model ─ model ──┐
        Load CLIP ─ clip ─ pos/neg ────┤ KSampler ─ VAEDecode ─ SaveImage
        Load VAE ─ vae ────────────────┘      ↑
        Empty Latent Image ─ latent ──────────┘
    """
    return {
        "4": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "10": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": clip, "type": clip_typ}},
        "11": {"class_type": "VAELoader",
               "inputs": {"vae_name": vae}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height),
                         "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["10", 0]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": neg or "", "clip": ["10", 0]}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": int(steps),
                         "cfg": float(cfg), "sampler_name": sampler,
                         "scheduler": scheduler, "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["11", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "NemiCLI", "images": ["8", 0]}},
    }


def workflow(prompt: str, *, ckpt: str, neg: str, steps: int, cfg: float,
             width: int, height: int, seed: int, sampler: str,
             scheduler: str) -> dict:
    """Baut den txt2img-Graphen – dieselben Bausteine wie von Hand geklickt."""
    return {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height),
                         "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": neg or "", "clip": ["4", 1]}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": int(steps),
                         "cfg": float(cfg), "sampler_name": sampler,
                         "scheduler": scheduler, "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "NemiCLI", "images": ["8", 0]}},
    }


# ===========================================================================
#  Malen
# ===========================================================================

def _bilder_aus_history(eintrag: dict) -> list[dict]:
    """Alle Bild-Angaben aus einem History-Eintrag ({filename, subfolder, type})."""
    raus = []
    for node in (eintrag.get("outputs") or {}).values():
        for bild in (node.get("images") or []):
            if bild.get("filename"):
                raus.append(bild)
    return raus


def _warte_auf_bild(prompt_id: str, on_status=None, grenze: float = 900.0) -> list[dict]:
    """Fragt, bis der Auftrag fertig ist. Gibt die Bild-Angaben zurück."""
    start = time.time()
    while time.time() - start < grenze:
        try:
            with _client(20.0) as c:
                r = c.get(f"/history/{prompt_id}")
                if r.status_code == 200:
                    eintrag = (r.json() or {}).get(prompt_id)
                    if eintrag:
                        status = (eintrag.get("status") or {})
                        if status.get("status_str") == "error":
                            raise RuntimeError(_fehlertext(status))
                        bilder = _bilder_aus_history(eintrag)
                        if bilder:
                            return bilder
                        if status.get("completed"):
                            raise RuntimeError(
                                "ComfyUI meldet fertig, hat aber kein Bild gespeichert. "
                                "Hat der Ablaufplan einen SaveImage-Baustein?")
                if on_status:
                    on_status(_wartetext(c, start))
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(0.7)
    raise RuntimeError(f"ComfyUI hat in {grenze:.0f} s kein Bild geliefert.")


def _wartetext(c: httpx.Client, start: float) -> str:
    """'male dein Bild (ComfyUI) … 12 s' – mit Warteschlange, wenn es eine gibt."""
    dauer = int(time.time() - start)
    davor = 0
    try:
        q = c.get("/prompt").json()
        davor = int((q.get("exec_info") or {}).get("queue_remaining") or 0)
    except Exception:
        pass
    if davor > 1:
        return f"ComfyUI: {davor - 1} Auftrag/Aufträge vor dir … {dauer} s"
    return f"male dein Bild (ComfyUI) … {dauer} s"


def _fehlertext(status: dict) -> str:
    """Aus ComfyUIs Fehlermeldung einen lesbaren Satz machen."""
    for art, daten in (status.get("messages") or []):
        if art == "execution_error" and isinstance(daten, dict):
            node = daten.get("node_type") or daten.get("node_id") or "?"
            grund = daten.get("exception_message") or daten.get("exception_type") or "?"
            return f"ComfyUI ist beim Baustein '{node}' ausgestiegen: {grund}"
    return "ComfyUI hat den Auftrag mit einem Fehler beendet."


def generate(prompt: str, *, model: str | None = None, neg: str | None = None,
             steps: int | None = None, cfg: float | None = None,
             size: tuple[int, int] | None = None, seed: int | None = None,
             sampler: str | None = None, on_status=None) -> str:
    """Malt EIN Bild über ComfyUI und gibt den gespeicherten Pfad zurück.

    Gleiche Aufrufform wie sdwebui.generate – imagegen kann beide gleich
    behandeln. Blockiert; im Thread aufrufen."""
    if not prompt or not prompt.strip():
        raise RuntimeError("Kein Prompt angegeben.")
    if not available():
        raise RuntimeError(
            f"ComfyUI ist unter {host()} nicht erreichbar. Starte ComfyUI und "
            "versuch es nochmal. Anderer Port? /bildmodel → ComfyUI-Adresse.")

    gewaehlt = resolve_model(model)
    if not gewaehlt:
        raise RuntimeError(
            "ComfyUI kennt kein einziges Modell – weder einen Checkpoint noch "
            "ein Diffusions-Modell. Leg eins in ComfyUIs models/checkpoints "
            "bzw. models/diffusion_models, oder zeig ComfyUI per "
            "extra_model_paths.yaml auf deinen vorhandenen Ordner.")
    art = model_art(gewaehlt)
    V = vorgaben(art)

    W, H = size or _ccfg("size", V["size"])
    if isinstance(W, str):                       # "832x1216" aus der Config
        W, H = (int(x) for x in W.lower().split("x"))
    echter_seed = int(seed) if seed is not None and int(seed) >= 0 \
        else random.randint(0, 2 ** 32 - 1)
    vorsatz = str(_ccfg("positiv", V["positiv"]) or "").strip()
    voll = f"{vorsatz}, {prompt}".strip(", ") if vorsatz else prompt

    gemeinsam = dict(
        neg=(neg if neg is not None else _ccfg("neg", V["neg"])) or "",
        steps=int(steps or _ccfg("steps", V["steps"])),
        cfg=float(_ccfg("cfg", V["cfg"]) if cfg is None else cfg),
        width=int(W), height=int(H), seed=echter_seed,
        sampler=_passend(samplers(), sampler or _ccfg("sampler", V["sampler"]),
                         V["sampler"]),
        scheduler=_passend(schedulers(), _ccfg("scheduler", V["scheduler"]),
                           V["scheduler"]),
    )

    if art == "diffusion":
        # Krea/Qwen/Flux: drei getrennte Lader. CLIP und VAE nimmt NemiCLI
        # selbst, wenn es nur eins gibt - sonst entscheidet die Config.
        clip = _eins_von(clips(), str(_ccfg("clip", "") or ""), "CLIP")
        vae = _eins_von(vaes(), str(_ccfg("vae", "") or ""), "VAE")
        plan = workflow_geteilt(voll, unet=gewaehlt, clip=clip,
                                clip_typ=clip_typ_fuer(gewaehlt, clip),
                                vae=vae, **gemeinsam)
    else:
        plan = workflow(voll, ckpt=gewaehlt, **gemeinsam)

    if on_status:
        on_status("schicke den Ablaufplan an ComfyUI …")
    try:
        with _client(60.0) as c:
            r = c.post("/prompt", json={"prompt": plan,
                                        "client_id": f"nemicli-{uuid.uuid4().hex[:8]}"})
            if r.status_code >= 400:
                raise RuntimeError(_abweisung(r))
            prompt_id = (r.json() or {}).get("prompt_id")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"ComfyUI nimmt den Auftrag nicht an: {e}")
    if not prompt_id:
        raise RuntimeError("ComfyUI hat keine Auftragsnummer zurückgegeben.")

    bilder = _warte_auf_bild(prompt_id, on_status)
    return _hole_und_speichere(bilder[0], plan, gewaehlt, echter_seed, on_status)


def _abweisung(r: httpx.Response) -> str:
    """ComfyUI lehnt einen Plan ab – die Begründung lesbar machen."""
    try:
        d = r.json()
        fehler = d.get("error") or {}
        text = fehler.get("message") or str(fehler) or r.text[:300]
        einzel = d.get("node_errors") or {}
        if einzel:
            erste = next(iter(einzel.values()))
            for e in (erste.get("errors") or [])[:1]:
                text += f" – {e.get('message', '')}"
        return f"ComfyUI lehnt den Ablaufplan ab: {text}"
    except Exception:
        return f"ComfyUI lehnt den Ablaufplan ab (HTTP {r.status_code})."


def _hole_und_speichere(bild: dict, plan: dict, ckpt: str, seed: int,
                        on_status=None) -> str:
    """Holt das fertige Bild von ComfyUI und legt es in Bilder/ ab."""
    if on_status:
        on_status("hole das fertige Bild …")
    try:
        with _client(120.0) as c:
            r = c.get("/view", params={"filename": bild.get("filename", ""),
                                       "subfolder": bild.get("subfolder", ""),
                                       "type": bild.get("type", "output")})
            r.raise_for_status()
            roh = r.content
    except Exception as e:
        raise RuntimeError(f"Bild ließ sich nicht abholen: {e}")
    if not roh:
        raise RuntimeError("ComfyUI hat eine leere Datei geliefert.")

    k = plan["3"]["inputs"]
    texte = {
        "prompt": plan["6"]["inputs"]["text"],
        "negative": plan["7"]["inputs"]["text"],
        "model": str(ckpt),
        "params": (f"steps={k['steps']}, cfg={k['cfg']}, "
                   f"size={plan['5']['inputs']['width']}x{plan['5']['inputs']['height']}, "
                   f"seed={seed}, sampler={k['sampler_name']}, "
                   f"scheduler={k['scheduler']}, backend=comfyui"),
        "workflow": json.dumps(plan, ensure_ascii=False)[:6000],
    }
    _SD.OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ist_png = roh[:8] == _SD._PNG_SIG
    out = _SD.OUT_DIR / f"nemi_comfy_{stamp}_{seed}{'.png' if ist_png else '.jpg'}"
    out.write_bytes(_SD._png_with_text(roh, texte) if ist_png else roh)
    return str(out)
