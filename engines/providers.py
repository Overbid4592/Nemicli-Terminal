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
            return ollama_reachable() if self.id == "ollama" else True
        return bool(self.key())


# --- Filter-Helfer ----------------------------------------------------------
def _any(*subs):
    subs = tuple(s.lower() for s in subs)
    return lambda mid: any(s in mid.lower() for s in subs)


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
}


def get(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


def detected() -> dict[str, Provider]:
    """Nur Anbieter, deren API-Key gesetzt ist (Reihenfolge der Registry)."""
    return {pid: p for pid, p in PROVIDERS.items() if p.available()}


def any_cloud() -> bool:
    return bool(detected())


# --- Ollama (lokal, OpenAI-kompatibel, ohne Key) ----------------------------
# Ollama spricht das OpenAI-Protokoll auf /v1 -> cloud.py kann es fahren.
# Die Modell-Liste holen wir aber aus /api/tags, weil die (anders als /v1/models)
# die Capabilities mitliefert: so filtern wir Embeddings raus und erkennen Vision.
def _norm_host(raw: str) -> str:
    host = (raw or "http://localhost:11434").strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


OLLAMA_HOST = _norm_host(os.getenv("OLLAMA_HOST", ""))
_ollama_cache: dict = {"t": -1e9, "tags": None}
_OLLAMA_TTL = 8.0          # Sekunden: so lange gilt eine Erreichbarkeits-Abfrage


def ollama_host() -> str:
    return OLLAMA_HOST


def ollama_base() -> str:
    """OpenAI-kompatible base_url für den Chat-Motor."""
    return f"{OLLAMA_HOST}/v1"


def _ollama_tags(force: bool = False) -> list | None:
    """Gecachte /api/tags-Antwort. None = Ollama nicht erreichbar."""
    now = time.monotonic()
    if not force and now - _ollama_cache["t"] < _OLLAMA_TTL:
        return _ollama_cache["tags"]
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=0.8)
        r.raise_for_status()
        tags = r.json().get("models") or []
    except Exception:
        tags = None
    _ollama_cache.update(t=now, tags=tags)
    return tags


def ollama_reachable() -> bool:
    return _ollama_tags() is not None


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
        r = httpx.post(f"{OLLAMA_HOST}/api/show", json={"model": model_id}, timeout=3.0)
        r.raise_for_status()
        caps = r.json().get("capabilities") or []
    except Exception:
        return []                  # Fehler nicht cachen – nächstes Mal neu versuchen
    _ollama_caps_cache[model_id] = caps
    return caps


def ollama_is_vision(model_id: str) -> bool:
    return "vision" in _ollama_capabilities(model_id)


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
    Für die wird bei Bildern auf das große Gegenstück (12b) umgeschaltet."""
    low = (model_id or "").lower()
    return "gemma" in low and any(_hat_groesse(low, k) for k in ("e2b", "e4b"))


def ollama_embedding_model() -> str | None:
    """Name eines Embedding-fähigen Ollama-Modells (z.B. embeddinggemma), sonst None.
    Wird vom Langzeitgedächtnis als „Bibliothekar" zum Finden passender Notizen genutzt."""
    for m in _ollama_tags() or []:
        if "embedding" in (m.get("capabilities") or []) and m.get("name"):
            return m["name"]
    return None


def ollama_embed(texts: list[str], model: str | None = None) -> list[list[float]] | None:
    """Wandelt Texte in Vektoren (Embeddings) über Ollama. None, wenn nicht möglich
    (kein Embedding-Modell / Ollama aus / Fehler)."""
    model = model or ollama_embedding_model()
    if not model or not texts:
        return None
    try:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(f"{OLLAMA_HOST}/api/embed",
                       json={"model": model, "input": texts})
            r.raise_for_status()
            embs = r.json().get("embeddings")
        if embs and len(embs) == len(texts):
            return embs
    except Exception:
        return None
    return None


# --- Modell-Liste live abfragen --------------------------------------------

def _auth_headers(p: Provider) -> dict:
    key = p.key() or ""
    if p.kind == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def _parse_models(p: Provider, data: dict) -> list[str]:
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
        # OpenAI-Form: {"data":[{"id":"..."}]} (auch Anthropic, Mistral, ...)
        for m in data.get("data", []):
            mid = m.get("id") or m.get("name")
            if mid:
                ids.append(mid)
    return ids


_models_cache: dict[str, tuple[float, list[str]]] = {}
_MODELS_TTL = 600.0        # 10 Minuten – Anbieter bringen selten stündlich neue Modelle


def clear_model_cache(provider_id: str | None = None) -> None:
    """Cache leeren – für einen Anbieter oder komplett."""
    if provider_id is None:
        _models_cache.clear()
        return
    for k in [k for k in _models_cache if k.startswith(f"{provider_id}:")]:
        del _models_cache[k]


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

    url = p.list_url
    params = {}
    if p.id == "google":                       # Gemini will den Key als ?key=
        params["key"] = p.key() or ""

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=_auth_headers(p), params=params)
        r.raise_for_status()
        data = r.json()

    ids = [mid for mid in _parse_models(p, data) if p.keep(mid)]
    ids = sorted(set(ids))
    # Lange Listen (OpenRouter!) kappen, sonst sprengt das Menü den Bildschirm.
    if len(ids) > limit:
        ids = ids[:limit]
    if ids:                                   # nur Brauchbares merken
        _models_cache[ck] = (time.monotonic(), list(ids))
    return ids
