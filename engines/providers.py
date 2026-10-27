"""
providers.py - Cloud-Anbieter: erkennen, Modelle live abfragen, anbinden.

Kerngedanke: Modellnamen werden NICHT im Code festgenagelt. Stattdessen fragt
NemiCLI den jeweiligen Anbieter live ab (`/models`-Endpunkt), sobald dessen
API-Key in der Umgebung gesetzt ist. Neues Modell beim Anbieter -> erscheint
automatisch. Kein Key -> der Anbieter taucht gar nicht erst auf.

Anbindung: eigene schlanke Adapter, kein Zusatzpaket.
Fast alle sprechen das OpenAI-Protokoll (nur eine andere base_url) -> ein
einziger Cloud-Motor (cloud.py) deckt sie ab. Anthropic ist nativ (chat.py).

Neuen Anbieter ergänzen = einen Eintrag in PROVIDERS. Das war's.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx


@dataclass
class Provider:
    id: str                      # kurzer Schlüssel, z.B. "openai"
    label: str                   # Anzeigename, z.B. "OpenAI"
    env: str                     # Name der Umgebungsvariable mit dem Key
    chat_base: str | None        # base_url für den Chat (None = nativ, siehe kind)
    kind: str = "openai"         # "openai" (kompatibel) | "anthropic" (nativ)
    list_url: str | None = None  # Endpunkt für die Modell-Liste (None = statisch)
    # Filter: welche Modell-IDs sind echte Chat-Modelle? (gegen TTS/Embeddings-Müll)
    keep: Callable[[str], bool] = lambda mid: True
    static_models: list[str] = field(default_factory=list)  # Fallback ohne Liste
    note: str = ""               # kurzer Hinweis fürs Menü
    keyless: bool = False         # braucht keinen API-Key (z.B. lokales Ollama)

    def key(self) -> str | None:
        return os.getenv(self.env)

    def available(self) -> bool:
        if self.keyless:
            # Keyless-Anbieter sind lokal: nur „verfügbar", wenn der Dienst läuft.
            return ollama_reachable() if self.id == "ollama" else lokal_erreichbar(self.list_url)
        return bool(self.key())


# --- Filter-Helfer ----------------------------------------------------------
def _any(*subs):
    subs = tuple(s.lower() for s in subs)
    return lambda mid: any(s in mid.lower() for s in subs)


_KEIN_CHAT = ("embed", "whisper", "tts", "rerank", "guard", "moderation", "transcri", "speech", "flux",
              "stable-diffusion", "sdxl", "image-gen", "imagen", "video")


def _chat(mid: str) -> bool:
    """Hoster mit gemischtem Katalog: alles außer Embeddings, Sprache, Bild- und Video-Modellen."""
    return not any(x in mid.lower() for x in _KEIN_CHAT)


def _lokal(pid, label, port):
    """Lokaler OpenAI-kompatibler Server ohne Schlüssel – erscheint nur, wenn er gerade antwortet."""
    base = f"http://127.0.0.1:{port}/v1"
    return Provider(pid, label, f"{pid.upper()}_LOCAL_UNUSED", chat_base=base, list_url=base + "/models",
                    keep=_chat, keyless=True, note=f"lokal (Port {port}), kein Key")


def _offen(label, env, base, keep=_chat, note=""):
    """OpenAI-kompatibler Anbieter mit /models-Liste unter derselben Adresse."""
    pid = env.removesuffix("_API_KEY").lower()
    return Provider(pid, label, env, chat_base=base, list_url=base.rstrip("/") + "/models", keep=keep, note=note)


# --- Die Anbieter-Registry --------------------------------------------------
# Reihenfolge = Reihenfolge im Menü.
PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(
        "anthropic", "Anthropic (Claude)", "ANTHROPIC_API_KEY",
        chat_base=None, kind="anthropic",
        list_url="https://api.anthropic.com/v1/models",
        keep=_any("claude"),
        note="adaptives Thinking mit Denk-Stärke (/staerke)",
    ),
    "openai": Provider(
        "openai", "OpenAI (GPT)", "OPENAI_API_KEY",
        chat_base="https://api.openai.com/v1",
        list_url="https://api.openai.com/v1/models",
        keep=_any("gpt", "o1", "o3", "o4", "chatgpt"),
    ),
    "google": Provider(
        "google", "Google (Gemini)", "GEMINI_API_KEY",
        chat_base="https://generativelanguage.googleapis.com/v1beta/openai/",
        list_url="https://generativelanguage.googleapis.com/v1beta/models",
        keep=_any("gemini"),
    ),
    "mistral": Provider(
        "mistral", "Mistral", "MISTRAL_API_KEY",
        chat_base="https://api.mistral.ai/v1",
        list_url="https://api.mistral.ai/v1/models",
        keep=lambda mid: not any(x in mid.lower() for x in ("embed", "moderation", "ocr")),
    ),
    "cohere": Provider(
        "cohere", "Cohere (Command)", "COHERE_API_KEY",
        chat_base="https://api.cohere.com/compatibility/v1",
        list_url="https://api.cohere.com/v1/models",
        keep=_any("command"),
    ),
    "perplexity": Provider(
        "perplexity", "Perplexity (Sonar)", "PERPLEXITY_API_KEY",
        chat_base="https://api.perplexity.ai",
        list_url=None,   # kein Listen-Endpunkt -> statisch
        static_models=["sonar", "sonar-pro", "sonar-reasoning",
                       "sonar-reasoning-pro", "sonar-deep-research"],
        note="hat Internet-Zugang eingebaut",
    ),
    "deepseek": Provider(
        "deepseek", "DeepSeek", "DEEPSEEK_API_KEY",
        chat_base="https://api.deepseek.com",
        list_url="https://api.deepseek.com/models",
        keep=_any("deepseek"),
    ),
    "xai": Provider(
        "xai", "xAI (Grok)", "XAI_API_KEY",
        chat_base="https://api.x.ai/v1",
        list_url="https://api.x.ai/v1/models",
        keep=_any("grok"),
    ),
    "groq": Provider(
        "groq", "Groq (schnell)", "GROQ_API_KEY",
        chat_base="https://api.groq.com/openai/v1",
        list_url="https://api.groq.com/openai/v1/models",
        keep=lambda mid: not any(x in mid.lower()
                                 for x in ("whisper", "tts", "guard", "embed")),
        note="extrem schnelle Inferenz",
    ),
    # --- Weitere Hersteller (OpenAI-kompatibel) ---
    "moonshot": _offen("Moonshot (Kimi)", "MOONSHOT_API_KEY", "https://api.moonshot.ai/v1", _any("kimi", "moonshot")),
    "zai": _offen("Z.ai (GLM)", "ZAI_API_KEY", "https://api.z.ai/api/paas/v4", _any("glm")),
    "dashscope": _offen("Alibaba (Qwen)", "DASHSCOPE_API_KEY",
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", _any("qwen", "qwq")),
    "minimax": _offen("MiniMax", "MINIMAX_API_KEY", "https://api.minimax.io/v1", _any("minimax", "abab")),
    "stepfun": _offen("StepFun (Step)", "STEPFUN_API_KEY", "https://api.stepfun.ai/v1", _any("step")),
    "inception": _offen("Inception (Mercury)", "INCEPTION_API_KEY", "https://api.inceptionlabs.ai/v1",
                        _any("mercury"), note="Diffusions-Sprachmodell, sehr schnell"),
    # --- Hoster offener Modelle ---
    "together": _offen("Together AI", "TOGETHER_API_KEY", "https://api.together.ai/v1"),
    "fireworks": _offen("Fireworks AI", "FIREWORKS_API_KEY", "https://api.fireworks.ai/inference/v1"),
    "deepinfra": _offen("DeepInfra", "DEEPINFRA_API_KEY", "https://api.deepinfra.com/v1/openai"),
    "cerebras": _offen("Cerebras", "CEREBRAS_API_KEY", "https://api.cerebras.ai/v1", note="extrem schnell"),
    "sambanova": _offen("SambaNova", "SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1", note="sehr schnell"),
    "nebius": _offen("Nebius Token Factory", "NEBIUS_API_KEY", "https://api.tokenfactory.nebius.com/v1"),
    "novita": _offen("Novita AI", "NOVITA_API_KEY", "https://api.novita.ai/openai/v1"),
    "hyperbolic": _offen("Hyperbolic", "HYPERBOLIC_API_KEY", "https://api.hyperbolic.xyz/v1"),
    "siliconflow": _offen("SiliconFlow", "SILICONFLOW_API_KEY", "https://api.siliconflow.com/v1"),
    "chutes": _offen("Chutes", "CHUTES_API_KEY", "https://llm.chutes.ai/v1"),
    "featherless": _offen("Featherless", "FEATHERLESS_API_KEY", "https://api.featherless.ai/v1",
                          note="sehr viele offene Modelle"),
    "nvidia": _offen("NVIDIA NIM", "NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1"),
    "huggingface": _offen("Hugging Face (Inference)", "HUGGINGFACE_API_KEY", "https://router.huggingface.co/v1"),
    "github": Provider(
        "github", "GitHub Models", "GITHUB_MODELS_API_KEY",
        chat_base="https://models.github.ai/inference",
        list_url="https://models.github.ai/catalog/models",
        keep=_chat,
        note="Zugangs-Token von GitHub",
    ),
    "venice": _offen("Venice", "VENICE_API_KEY", "https://api.venice.ai/api/v1", note="ohne Speichern der Chats"),
    # --- Rechenzentren in Europa ---
    "ionos": _offen("IONOS AI Model Hub", "IONOS_API_KEY", "https://openai.inference.de-txl.ionos.com/v1",
                    note="Rechenzentrum in Deutschland"),
    "scaleway": _offen("Scaleway", "SCALEWAY_API_KEY", "https://api.scaleway.ai/v1",
                       note="Rechenzentrum in Frankreich"),
    "ovh": _offen("OVHcloud AI Endpoints", "OVH_API_KEY", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
                  note="Rechenzentrum in Frankreich"),
    # --- Sammel-Zugänge (ein Key, viele Anbieter) ---
    "ai_gateway": _offen("Vercel AI Gateway", "AI_GATEWAY_API_KEY", "https://ai-gateway.vercel.sh/v1",
                         note="ein Key, viele Anbieter"),
    "aimlapi": _offen("AI/ML API", "AIMLAPI_API_KEY", "https://api.aimlapi.com/v1", note="ein Key, viele Anbieter"),
    "openrouter": Provider(
        "openrouter", "OpenRouter (alles)", "OPENROUTER_API_KEY",
        chat_base="https://openrouter.ai/api/v1",
        list_url="https://openrouter.ai/api/v1/models",
        keep=lambda mid: True,
        note="ein Key, viele Anbieter",
    ),
    "ollama_cloud": Provider(
        "ollama_cloud", "Ollama Cloud", "OLLAMA_API_KEY",
        chat_base="https://ollama.com/v1",
        list_url="https://ollama.com/v1/models",
        keep=lambda mid: not any(x in mid.lower()
                                 for x in ("embed", "guard", "-rerank")),
        note="Ollama-Modelle auf fremder Hardware, Key nötig",
    ),
    # --- Weitere lokale Server (laufen sie, stehen sie unter /model) ---
    "lmstudio": _lokal("lmstudio", "LM Studio", 1234),
    "jan": _lokal("jan", "Jan", 1337),
    "llamacpp": _lokal("llamacpp", "llama.cpp-Server / LocalAI", 8080),
    "vllm": _lokal("vllm", "vLLM", 8000),
    "ollama": Provider(
        "ollama", "Ollama (lokal)", "OLLAMA_LOCAL_UNUSED",  # Platzhalter; kein Key nötig
        chat_base=None,                          # wird dynamisch gesetzt (ollama_host)
        kind="openai",
        list_url=None,                           # Modelle kommen aus /api/tags (Sonderweg)
        keyless=True,
        note="lokal & offline, kein Key nötig",
    ),
}


# Wo man den jeweiligen API-Key bekommt (für die Einrichtung in der CLI).
KEY_URLS: dict[str, str] = {
    "anthropic":  "https://console.anthropic.com/settings/keys",
    "openai":     "https://platform.openai.com/api-keys",
    "google":     "https://aistudio.google.com/apikey",
    "mistral":    "https://console.mistral.ai/api-keys",
    "cohere":     "https://dashboard.cohere.com/api-keys",
    "perplexity": "https://www.perplexity.ai/settings/api",
    "deepseek":   "https://platform.deepseek.com/api_keys",
    "xai":        "https://console.x.ai",
    "groq":       "https://console.groq.com/keys",
    "openrouter": "https://openrouter.ai/keys",
    "ollama_cloud": "https://ollama.com/settings/keys",
    "moonshot":   "https://platform.kimi.ai",
    "zai":        "https://z.ai/manage-apikey/apikey-list",
    "dashscope":  "https://modelstudio.console.alibabacloud.com",
    "minimax":    "https://www.minimax.io/platform",
    "stepfun":    "https://platform.stepfun.ai",
    "inception":  "https://platform.inceptionlabs.ai",
    "together":   "https://api.together.ai/settings/api-keys",
    "fireworks":  "https://fireworks.ai/account/api-keys",
    "deepinfra":  "https://deepinfra.com/dash/api_keys",
    "cerebras":   "https://cloud.cerebras.ai",
    "sambanova":  "https://cloud.sambanova.ai",
    "nebius":     "https://tokenfactory.nebius.com",
    "novita":     "https://novita.ai/settings/key-management",
    "hyperbolic": "https://app.hyperbolic.xyz",
    "siliconflow": "https://cloud.siliconflow.com",
    "chutes":     "https://chutes.ai",
    "featherless": "https://featherless.ai",
    "nvidia":     "https://build.nvidia.com",
    "huggingface": "https://huggingface.co/settings/tokens",
    "github":     "https://github.com/settings/personal-access-tokens",
    "venice":     "https://venice.ai/settings/api",
    "ionos":      "https://dcd.ionos.com",
    "scaleway":   "https://console.scaleway.com/iam/api-keys",
    "ovh":        "https://endpoints.ai.cloud.ovh.net",
    "ai_gateway": "https://vercel.com/dashboard",
    "aimlapi":    "https://aimlapi.com",
}


def get(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


_LOKAL_CACHE: dict[str, tuple[float, bool]] = {}
_LOKAL_TTL = 15.0


def lokal_erreichbar(url: str | None) -> bool:
    """Antwortet ein lokaler Server auf /models? Kurz gemerkt, damit /model nicht bei jedem Öffnen wartet."""
    if not url:
        return False
    hit = _LOKAL_CACHE.get(url)
    if hit and time.monotonic() - hit[0] < _LOKAL_TTL:
        return hit[1]
    try:
        ok = httpx.get(url, timeout=0.4).status_code < 400
    except Exception:
        ok = False
    _LOKAL_CACHE[url] = (time.monotonic(), ok)
    return ok


def lokale_vorpruefen() -> None:
    """Alle lokalen Server gleichzeitig ansprechen – unter Windows dauert ein „hier läuft nichts“ fast eine
    Sekunde je Port, nacheinander summierte sich das."""
    offen = [p.list_url for p in PROVIDERS.values() if p.keyless and p.id != "ollama" and p.list_url
             and not (p.list_url in _LOKAL_CACHE and time.monotonic() - _LOKAL_CACHE[p.list_url][0] < _LOKAL_TTL)]
    if len(offen) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(len(offen)) as pool:
            list(pool.map(lokal_erreichbar, offen))


def detected() -> dict[str, Provider]:
    """Nur Anbieter, deren API-Key gesetzt ist (Reihenfolge der Registry)."""
    lokale_vorpruefen()
    return {pid: p for pid, p in PROVIDERS.items() if p.available()}


# --- Ollama (lokal, OpenAI-kompatibel, ohne Key) ----------------------------
# Ollama spricht das OpenAI-Protokoll auf /v1 -> cloud.py kann es fahren.
# Die Modell-Liste holen wir aber aus /api/tags, weil die (anders als /v1/models)
# die Capabilities mitliefert: so filtern wir Embeddings raus und erkennen Vision.
def _norm_host(raw: str) -> str:
    host = (raw or "http://127.0.0.1:11434").strip().rstrip("/")   # nicht localhost: Ollama lauscht nur auf IPv4
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    from urllib.parse import urlparse
    teile = urlparse(host)
    name = teile.hostname or "127.0.0.1"
    if name in ("0.0.0.0", "::", "[::]"):               # Lausch-Adresse, als Ziel unter Windows nicht erreichbar
        name = "127.0.0.1"
    if teile.port is not None:
        port = f":{teile.port}"
    else:                                              # ohne Port: Ollama-Vorgabe – außer bei https (Vermittler)
        port = "" if teile.scheme == "https" else ":11434"
    return f"{teile.scheme}://{name}{port}{teile.path}".rstrip("/")


OLLAMA_HOST = _norm_host(os.getenv("OLLAMA_HOST", ""))
_ollama_cache: dict = {"t": -1e9, "tags": None}
_OLLAMA_TTL = 8.0          # Sekunden: so lange gilt eine Erreichbarkeits-Abfrage
_OLLAMA_TTL_AUS = 30.0     # „nicht erreichbar“ gilt länger: jede neue Abfrage kostet Wartezeit


def ollama_host() -> str:
    return OLLAMA_HOST


def ollama_base() -> str:
    """OpenAI-kompatible base_url für den Chat-Motor."""
    return f"{OLLAMA_HOST}/v1"


def _ollama_tags(force: bool = False) -> list | None:
    """Gecachte /api/tags-Antwort. None = Ollama nicht erreichbar."""
    now = time.monotonic()
    ttl = _OLLAMA_TTL if _ollama_cache["tags"] is not None else _OLLAMA_TTL_AUS
    if not force and now - _ollama_cache["t"] < ttl:
        return _ollama_cache["tags"]
    try:
        # Kurzer Verbindungsaufbau: ein laufendes Ollama nimmt sofort an. Ein abgelehnter
        # Aufbau zu localhost dauert unter Windows sonst rund 2 s je Adresse (::1, 127.0.0.1).
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=httpx.Timeout(0.8, connect=0.25))
        r.raise_for_status()
        tags = r.json().get("models") or []
    except Exception:
        tags = None
    _ollama_cache.update(t=now, tags=tags)
    return tags


def ollama_reachable() -> bool:
    return _ollama_tags() is not None


def ollama_kontext(model_id: str) -> int | None:
    """Kontext, mit dem Ollama das Modell gerade geladen hat (/api/ps, `context_length`) – die Einstellung
    von Ollama gilt, nicht der Höchstwert des Modells. None = nicht geladen oder unbekannt."""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/ps", timeout=httpx.Timeout(1.0, connect=0.25))
        r.raise_for_status()
        geladen = r.json().get("models") or []
    except Exception:
        return None
    gesucht = {model_id, model_id if ":" in model_id else model_id + ":latest"}
    for m in geladen:
        if {m.get("name"), m.get("model")} & gesucht:
            try:
                n = int(m.get("context_length") or 0)
            except (TypeError, ValueError):
                return None
            return n if n > 0 else None
    return None


def _ollama_lokal() -> bool:
    from urllib.parse import urlparse
    return (urlparse(OLLAMA_HOST).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


def _ollama_im_vram() -> list[str] | None:
    """Namen der Modelle, die Ollama gerade im Grafikspeicher hält (/api/ps, size_vram > 0). None = keine Antwort."""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/ps", timeout=httpx.Timeout(1.0, connect=0.25))
        r.raise_for_status()
        geladen = r.json().get("models") or []
    except Exception:
        return None
    return [str(m.get("name") or m.get("model")) for m in geladen
            if (m.get("name") or m.get("model")) and int(m.get("size_vram") or 0) > 0]


def ollama_gpu_freigeben(warten_s: float = 5.0) -> list[str]:
    """Vor GPU-Arbeit (Bild, Video, Musik, LoRA): alle Modelle entladen, die das lokale Ollama im Grafikspeicher
    hält (keep_alive 0). Ollama kann ein Modell nicht in den RAM verschieben; die Datei bleibt aber im
    Datei-Zwischenspeicher von Windows, der nächste Chat lädt sie von dort zügig neu. Ein entferntes Ollama
    bleibt unberührt, ebenso reine CPU- und Cloud-Modelle. Rückgabe: entladene Modelle."""
    if not _ollama_lokal():
        return []
    if _ollama_cache["tags"] is None and time.monotonic() - _ollama_cache["t"] < _OLLAMA_TTL_AUS:
        return []                                      # eben erst nicht erreichbar: nicht erneut warten
    namen = _ollama_im_vram()
    if not namen:
        return []
    for name in namen:
        try:
            httpx.post(f"{OLLAMA_HOST}/api/generate", json={"model": name, "keep_alive": 0},
                       timeout=httpx.Timeout(10.0, connect=0.25))
        except Exception:
            pass
    ende = time.monotonic() + warten_s                 # Ollama gibt den Speicher kurz nach der Antwort frei
    while time.monotonic() < ende:
        noch = _ollama_im_vram()
        if not noch or not set(noch) & set(namen):
            break
        time.sleep(0.25)
    return namen


def _ollama_chatable(m: dict) -> bool:
    caps = m.get("capabilities") or []
    if caps:                              # moderne Ollama: Capabilities nutzen
        return "completion" in caps and "embedding" not in caps
    return "embed" not in (m.get("name") or "").lower()   # ältere: am Namen raten


def ollama_models() -> list[str]:
    """Namen der nutzbaren Chat-Modelle (Embeddings rausgefiltert)."""
    tags = _ollama_tags() or []
    return sorted(m["name"] for m in tags if m.get("name") and _ollama_chatable(m))


# Reihenfolge = Vorliebe: das GRÖSSTE installierte Gegenstück gewinnt.
_GEMMA_GROSS = ("27b", "12b", "9b")
_GEMMA_KLEIN = ("e2b", "e4b", "1b", "2b", "4b")


def _hat_groesse(text: str, groesse: str) -> bool:
    """Prüft eine Modellgröße mit Ziffern-Grenze, damit '2b' NICHT in '12b'
    trifft (sonst hielte die Erkennung ein 12B-Modell für ein 2B-Modell)."""
    return re.search(r"(?<![0-9])" + re.escape(groesse) + r"(?![0-9])", text) is not None


def gemma_upgrade_ziel(model_id: str) -> str | None:
    """Für den Auto-Stark-Modus: zu einem LEICHTEN Gemma (e2b/e4b/…) das
    installierte SCHWERE Gegenstück (27b/12b/9b) finden.

    Gibt die model_id des schweren Modells zurück – oder None, wenn das aktive
    Modell schon groß ist oder gar kein passendes zweites Gemma da ist. So greift
    der Modus von allein nur beim leichten Modell.
    """
    low = (model_id or "").lower()
    if "gemma" not in low:
        return None
    if not any(_hat_groesse(low, k) for k in _GEMMA_KLEIN):
        return None                              # schon ein großes Modell
    # familien-Stamm (z.B. "gemma4") grob festhalten, damit gemma3 nicht gemma4 upgradet
    stamm = re.match(r"[a-z]*gemma[0-9]*", low)
    stamm = stamm.group(0) if stamm else "gemma"

    best_rang, best = 99, None
    for m in ollama_models():
        ml = m.lower()
        if stamm not in ml or m == model_id:
            continue
        if any(_hat_groesse(ml, k) for k in _GEMMA_KLEIN):   # anderes kleines Modell -> weg
            continue
        for rang, size in enumerate(_GEMMA_GROSS):
            if _hat_groesse(ml, size) and rang < best_rang:
                best_rang, best = rang, m
    return best


_ollama_caps_cache: dict = {}      # model_id -> capabilities-Liste (pro Modell gecacht)


def _ollama_capabilities(model_id: str) -> list:
    """Die VOLLSTÄNDIGEN Capabilities eines Ollama-Modells.

    Wichtig: /api/tags meldet vision/audio NICHT zuverlässig – dort steht z.B. bei
    Gemma nur `completion, tools, thinking`, obwohl das Modell Bilder sehen kann.
    Die volle Liste (inkl. `vision`) gibt es nur über /api/show. Früher hielt
    NemiCLI deshalb ALLE Ollama-Modelle für „blind" und hängte Bilder nie an –
    das Modell hat die Beschreibung dann frei halluziniert.
    """
    if model_id in _ollama_caps_cache:
        return _ollama_caps_cache[model_id]
    try:
        r = httpx.post(f"{OLLAMA_HOST}/api/show", json={"model": model_id},
                       timeout=httpx.Timeout(3.0, connect=0.25))
        r.raise_for_status()
        caps = r.json().get("capabilities") or []
    except Exception:
        return []                  # Fehler nicht cachen – nächstes Mal neu versuchen
    _ollama_caps_cache[model_id] = caps
    return caps


def ollama_is_vision(model_id: str) -> bool:
    return "vision" in _ollama_capabilities(model_id)


CLOUD_SHOW_URL = "https://ollama.com/api/show"
CLOUD_NEUER_VERSUCH_S = 600.0        # nach einem Fehlschlag so lange wieder am Namen erkennen
_cloud_caps_cache: dict[str, list] = {}
_cloud_caps_fehler: dict[str, float] = {}


def ollama_cloud_capabilities(model_id: str) -> list | None:
    """Capabilities eines Ollama-Cloud-Modells (vision, tools, thinking …) über /api/show bei ollama.com,
    gemerkt je Modell. None, wenn die Abfrage nicht ging (dann gilt die Namensliste)."""
    mid = (model_id or "").strip()
    if not mid:
        return None
    if mid in _cloud_caps_cache:
        return _cloud_caps_cache[mid]
    if time.monotonic() < _cloud_caps_fehler.get(mid, 0.0):
        return None
    p = get("ollama_cloud")
    key = p.key() if p else ""
    try:
        r = httpx.post(CLOUD_SHOW_URL, json={"model": mid},
                       headers={"Authorization": f"Bearer {key}"} if key else None,
                       timeout=httpx.Timeout(3.0, connect=1.0))
        r.raise_for_status()
        caps = r.json().get("capabilities")
        if not isinstance(caps, list):
            raise ValueError("keine capabilities")
    except Exception:
        _cloud_caps_fehler[mid] = time.monotonic() + CLOUD_NEUER_VERSUCH_S
        return None
    _cloud_caps_cache[mid] = [str(c) for c in caps]
    return _cloud_caps_cache[mid]


# Cloud-Modelle (OpenAI-Protokoll) melden ihre Vision-Fähigkeit nicht abfragbar.
# Deshalb erkennen wir bekannte multimodale Familien am Namen. Lieber knapp und
# treffsicher als breit – ein falsches „sieht Bilder" schickt einem reinen
# Text-Modell ein Bild, das es nicht verarbeiten kann.
_CLOUD_VISION_HINTS = (
    "deepseek-v4.1", "deepseek-flash", "deepseek-v4-flash-vision",   # DeepSeek nativ multimodal
    "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4",                  # OpenAI
    "gemini",                                                        # Google
    "claude-3", "claude-opus", "claude-sonnet", "claude-haiku",     # Anthropic
    "grok-2-vision", "grok-4", "grok-vision",                       # xAI
    "pixtral", "mistral-small-3", "mistral-medium-3",               # Mistral
    "-vl", "vision", "llava", "internvl", "qwen2-vl", "qwen2.5-vl",  # generische VLMs
    "qwen3-vl", "minicpm-v", "molmo", "moondream", "cogvlm",
    "llama-3.2-11b", "llama-3.2-90b", "llama-4",                    # Llama-Vision
)


def cloud_name_vision(model_id: str) -> bool:
    """Erkennt an bekannten Namen, ob ein Cloud-Modell Bilder sehen kann.
    Für Anbieter wie Ollama Cloud, deren API keine Capability-Abfrage bietet."""
    m = (model_id or "").lower()
    return any(h in m for h in _CLOUD_VISION_HINTS)


def ist_vision_schwach(model_id: str) -> bool:
    """True für kleine Gemmas (e2b/e4b): Die MELDEN zwar 'vision', sind aber zu
    klein, um Bilder wirklich brauchbar zu lesen (Text/Zahlen/Details gehen unter).
    Sie gelten deshalb als nicht sehend."""
    low = (model_id or "").lower()
    return "gemma" in low and any(_hat_groesse(low, k) for k in ("e2b", "e4b"))


# --- Modell-Liste live abfragen --------------------------------------------

def _auth_headers(p: Provider) -> dict:
    key = p.key() or ""
    if p.kind == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if p.id == "google":                       # Gemini: Schlüssel im Kopf – in der Adresse stünde er in Fehlermeldungen
        return {"x-goog-api-key": key}
    return {"Authorization": f"Bearer {key}"}


def status_text(anbieter: str, status: int) -> str:
    """Fehler einer Anfrage ohne Adresse und Schlüssel – nur der Status, bei 401/403 mit Hinweis."""
    if status in (401, 403):
        return (f"{anbieter} lehnt den API-Schlüssel ab ({status}) – ist er gültig und für diese Schnittstelle "
                "freigeschaltet? Neu eintragen unter /model.")
    return f"{anbieter} antwortet mit Fehler {status}."


def _parse_models(p: Provider, data: dict | list) -> list[str]:
    """Holt die Modell-IDs aus der jeweiligen Antwort-Form."""
    ids: list[str] = []
    if p.id == "google":
        # {"models":[{"name":"models/gemini-2.5-pro", ...}]}
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            if methods and "generateContent" not in methods:
                continue
            name = (m.get("name") or "").split("/")[-1]
            if name:
                ids.append(name)
    elif p.id == "cohere":
        # {"models":[{"name":"command-r-plus","endpoints":[...]}]}
        for m in data.get("models", []):
            eps = m.get("endpoints", [])
            if eps and "chat" not in eps:
                continue
            if m.get("name"):
                ids.append(m["name"])
    else:
        # OpenAI-Form: {"data":[{"id":"..."}]} (auch Anthropic, Mistral, ...); Together und GitHub: gleich die Liste
        eintraege = data if isinstance(data, list) else data.get("data", [])
        for m in eintraege:
            if not isinstance(m, dict):
                continue
            art = str(m.get("type") or "").lower()
            if art and art not in ("chat", "language", "code", "model", "llm", "text"):
                continue                          # Embeddings, Bilder, Audio aus gemischten Katalogen
            mid = m.get("id") or m.get("name")
            if mid:
                ids.append(mid)
    return ids


LISTE_MAX = 2000           # /model zeigt davon 30 auf einmal, 🔍 Suchen findet den Rest
_models_cache: dict[str, tuple[float, list[str]]] = {}
_MODELS_TTL = 600.0        # 10 Minuten – Anbieter bringen selten stündlich neue Modelle


async def list_models(provider_id: str, limit: int = 40,
                      force: bool = False) -> list[str]:
    """
    Fragt die verfügbaren Chat-Modelle eines Anbieters live ab.
    Bei Anbietern ohne Listen-Endpunkt: die statische Fallback-Liste.
    Wirft bei Netz-/Auth-Fehlern eine Exception (vom Aufrufer angezeigt).

    Ergebnisse werden 10 Minuten gecacht: sonst wartet man bei JEDEM Öffnen
    von /model erneut aufs Netz. `force=True` holt frisch.
    """
    p = PROVIDERS.get(provider_id)
    if p is None:
        raise ValueError(f"Unbekannter Anbieter: {provider_id}")

    if provider_id == "ollama":               # Sonderweg: /api/tags mit Capabilities
        return ollama_models()[:limit]        # (hat schon einen eigenen Kurz-Cache)

    if not p.list_url:
        return list(p.static_models)

    ck = f"{provider_id}:{limit}"
    hit = _models_cache.get(ck)
    if not force and hit and time.monotonic() - hit[0] < _MODELS_TTL:
        return list(hit[1])

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(p.list_url, headers=_auth_headers(p))
        if r.status_code >= 400:
            raise RuntimeError(status_text(p.label, r.status_code))
        data = r.json()

    ids = [mid for mid in _parse_models(p, data) if p.keep(mid)]
    ids = sorted(set(ids))
    # Lange Listen (OpenRouter!) kappen, sonst sprengt das Menü den Bildschirm.
    if len(ids) > limit:
        ids = ids[:limit]
    if ids:                                   # nur Brauchbares merken
        _models_cache[ck] = (time.monotonic(), list(ids))
    return ids
