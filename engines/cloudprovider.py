"""
cloudprovider.py - Cloud-Anbieter mit API-Schlüssel: Registry, Modell-Listen, Kontextlänge.

Modellnamen werden NICHT im Code festgenagelt: NemiCLI fragt den Anbieter live ab (`/models`),
sobald dessen API-Key gesetzt ist. Dieselbe Liste nennt bei vielen Anbietern auch die
Kontextlänge je Modell – unter verschiedenen Namen (KONTEXT_FELDER). Sie wird beim Abholen
mitgelesen und gemerkt, auch auf der Platte; Ollama Cloud liefert sie über /api/show.

Die lokalen Server (Ollama, LM Studio, Jan, llama.cpp, vLLM) stehen in providers.py, das beide
Registries zusammenführt. Neuen Cloud-Anbieter ergänzen = einen Eintrag in CLOUD.
"""

from __future__ import annotations

import json
import os
import threading
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
        if self.kind == "chatgpt":                  # ChatGPT-Konto: Zugangs-Token statt Schlüssel (chatgpt_login)
            try:
                import chatgpt_login
                return chatgpt_login.zugang()
            except Exception:
                return None
        return os.getenv(self.env)

    def available(self) -> bool:
        if self.kind == "chatgpt":                  # ohne Netzanfrage: angemeldet und Plan-Nutzung freigegeben
            try:
                import chatgpt_login
                return chatgpt_login.nutzbar()
            except Exception:
                return False
        if self.keyless:
            # Keyless-Anbieter sind lokal: nur „verfügbar", wenn der Dienst läuft.
            import providers
            return providers.ollama_reachable() if self.id == "ollama" else providers.lokal_erreichbar(self.list_url)
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


def _offen(label, env, base, keep=_chat, note=""):
    """OpenAI-kompatibler Anbieter mit /models-Liste unter derselben Adresse."""
    pid = env.removesuffix("_API_KEY").lower()
    return Provider(pid, label, env, chat_base=base, list_url=base.rstrip("/") + "/models", keep=keep, note=note)


# --- Die Cloud-Registry -------------------------------------------------------
# Reihenfolge = Reihenfolge im Menü.
CLOUD: dict[str, Provider] = {
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
    "chatgpt": Provider(
        "chatgpt", "ChatGPT-Konto (Plus/Pro)", "NEMICLI_CHATGPT_KONTO",
        chat_base="https://api.openai.com/v1",
        kind="chatgpt",
        list_url="https://api.openai.com/v1/models",
        note="mit deinem ChatGPT-Plan, ohne API-Schlüssel – Anmeldung im Browser",
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


# --- Ollama Cloud: /api/show (Capabilities und Kontextlänge) ---------------------
CLOUD_SHOW_URL = "https://ollama.com/api/show"
CLOUD_NEUER_VERSUCH_S = 600.0        # nach einem Fehlschlag so lange wieder am Namen erkennen
_cloud_caps_cache: dict[str, list] = {}
_cloud_caps_fehler: dict[str, float] = {}


def _cloud_show(mid: str) -> dict | None:
    """Antwort von ollama.com/api/show für ein Modell (None, wenn die Abfrage nicht ging)."""
    p = CLOUD["ollama_cloud"]
    key = p.key() or ""
    try:
        r = httpx.post(CLOUD_SHOW_URL, json={"model": mid},
                       headers={"Authorization": f"Bearer {key}"} if key else None,
                       timeout=httpx.Timeout(3.0, connect=1.0))
        r.raise_for_status()
        daten = r.json()
        return daten if isinstance(daten, dict) else None
    except Exception:
        return None


def _show_kontext(daten: dict) -> int | None:
    """`model_info.<bauart>.context_length` aus einer /api/show-Antwort."""
    info = daten.get("model_info") or {}
    if not isinstance(info, dict):
        return None
    return next((n for k, v in info.items() if k.endswith(".context_length") and (n := _zahl(v))), None)


_cloud_denken: dict[str, dict] = {}     # Modellfamilie -> {"ctx", "think", "default"} wie reasoning.OLLAMA_CLOUD


def ollama_familie(model_id: str) -> str:
    """'deepseek-v4-flash:0731' → 'deepseek-v4-flash', 'kimi-k2.5:cloud' → 'kimi-k2.5' (wie reasoning)."""
    return (model_id or "").lower().removeprefix("library/").partition(":")[0].removesuffix("-cloud")


def _denken_merken(mid: str, daten: dict, caps: list) -> None:
    """Denkstufen aus /api/show (`thinking.values`/`default`). Meldet ein Modell nur „thinking“ ohne
    Stufen, gilt Denken als fest an – NemiCLI schickt dann keine Stufe, die es nicht kennt."""
    denken = daten.get("thinking") if isinstance(daten.get("thinking"), dict) else {}
    werte = denken.get("values")
    if isinstance(werte, list) and werte:
        think = tuple(werte)
        vorgabe = denken.get("default", werte[-1])
    elif "thinking" in caps:
        think, vorgabe = (True,), True
    else:
        think, vorgabe = None, None
    _cloud_denken[ollama_familie(mid)] = {"ctx": _show_kontext(daten) or 0, "think": think, "default": vorgabe}


def cloud_denken(familie: str) -> dict | None:
    """Werte eines Ollama-Cloud-Modells aus /api/show (None, solange nicht abgefragt)."""
    return _cloud_denken.get(familie)


def ollama_cloud_capabilities(model_id: str) -> list | None:
    """Capabilities eines Ollama-Cloud-Modells (vision, tools, thinking …) über /api/show bei ollama.com,
    gemerkt je Modell. None, wenn die Abfrage nicht ging (dann gilt die Namensliste). Dieselbe Antwort
    nennt Kontextlänge und Denkstufen – sie werden mit gemerkt."""
    mid = (model_id or "").strip()
    if not mid:
        return None
    if mid in _cloud_caps_cache:
        return _cloud_caps_cache[mid]
    if time.monotonic() < _cloud_caps_fehler.get(mid, 0.0):
        return None
    daten = _cloud_show(mid)
    caps = daten.get("capabilities") if daten else None
    if not isinstance(caps, list):
        _cloud_caps_fehler[mid] = time.monotonic() + CLOUD_NEUER_VERSUCH_S
        return None
    if (n := _show_kontext(daten)):
        kontext_merken("ollama_cloud", mid, n)
    _cloud_caps_cache[mid] = [str(c) for c in caps]
    _denken_merken(mid, daten, _cloud_caps_cache[mid])
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
    # Ollama Cloud mit „vision“ in /api/show
    "gemma4", "glm-5.3-flash", "kimi-k2.6", "kimi-k2.7", "kimi-k3", "mimo-v2.6", "minimax-m3",
    "mistral-large-3", "mistral-large-4", "muse-spark",
)


def cloud_name_vision(model_id: str) -> bool:
    """Erkennt an bekannten Namen, ob ein Cloud-Modell Bilder sehen kann.
    Für Anbieter wie Ollama Cloud, deren API keine Capability-Abfrage bietet."""
    m = (model_id or "").lower()
    return any(h in m for h in _CLOUD_VISION_HINTS)


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


def _eintraege(p: Provider, data: dict | list) -> list[tuple[str, dict]]:
    """(Modell-ID, Eintrag) aus der jeweiligen Antwort-Form."""
    aus: list[tuple[str, dict]] = []
    if not isinstance(data, (dict, list)):
        return aus
    if p.id == "google":
        # {"models":[{"name":"models/gemini-2.5-pro", "inputTokenLimit": …}]}
        for m in (data.get("models", []) if isinstance(data, dict) else []):
            methods = m.get("supportedGenerationMethods", [])
            if methods and "generateContent" not in methods:
                continue
            name = (m.get("name") or "").split("/")[-1]
            if name:
                aus.append((name, m))
    elif p.id == "chatgpt":
        # {"models":[{"slug":"…","display_name":"…","visibility":"list"}]} – nur „list“, Reihenfolge des Servers
        for m in (data.get("models", []) if isinstance(data, dict) else []):
            if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str):
                aus.append((m["slug"], m))
    elif p.id == "cohere":
        # {"models":[{"name":"command-r-plus","endpoints":[...], "context_length": …}]}
        for m in (data.get("models", []) if isinstance(data, dict) else []):
            eps = m.get("endpoints", [])
            if eps and "chat" not in eps:
                continue
            if m.get("name"):
                aus.append((m["name"], m))
    else:
        # OpenAI-Form: {"data":[{"id":"..."}]} (auch Anthropic, Mistral, ...); Together und GitHub: gleich die Liste
        eintraege = data if isinstance(data, list) else data.get("data", [])
        # Ollama Cloud: neben "data" steht "models" je slug mit Kontext und supported_in_api
        zusatz = ({m["slug"]: m for m in data.get("models", []) if isinstance(m, dict) and isinstance(m.get("slug"), str)}
                  if isinstance(data, dict) else {})
        for m in eintraege:
            if not isinstance(m, dict):
                continue
            art = str(m.get("type") or "").lower()
            # base/fine-tuned (Mistral) nennen die Herkunft, nicht die Art – den Rest filtert `keep`
            if art and art not in ("chat", "language", "code", "model", "llm", "text", "base", "fine-tuned"):
                continue                          # Embeddings, Bilder, Audio aus gemischten Katalogen
            mid = m.get("id") or m.get("name")
            if not mid:
                continue
            if (mehr := zusatz.get(str(mid))) is not None:
                if mehr.get("supported_in_api") is False:
                    continue                      # steht in der Liste, ist über die API aber nicht nutzbar
                m = {**mehr, **m}
            aus.append((str(mid), m))
    return aus


def _parse_models(p: Provider, data: dict | list) -> list[str]:
    """Holt die Modell-IDs aus der jeweiligen Antwort-Form."""
    return [mid for mid, _ in _eintraege(p, data)]


# --- Kontextlänge aus der Modell-Liste ---------------------------------------
# Die Felder heißen je Anbieter anders: OpenRouter/Together/Cohere `context_length`, Groq und Vercel
# `context_window`, Mistral `max_context_length`, vLLM `max_model_len`, Novita `context_size`, Gemini
# `inputTokenLimit`, GitHub `limits.max_input_tokens`, Venice `model_spec.availableContextTokens`,
# DeepInfra `metadata.context_length`, Hugging Face je Hoster unter `providers`.
KONTEXT_FELDER = ("context_length", "context_window", "max_context_length", "max_model_len", "context_size",
                  "ctx_len", "contextLength", "inputTokenLimit", "max_input_tokens", "availableContextTokens",
                  "max_context_window", "n_ctx")
_KONTEXT_UNTER = ("top_provider", "metadata", "meta", "limits", "model_spec", "info", "specs")
KONTEXT_MIN, KONTEXT_MAX = 512, 100_000_000


def _zahl(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if KONTEXT_MIN <= n <= KONTEXT_MAX else None


def kontext_aus_eintrag(m: dict) -> int | None:
    """Kontextlänge aus einem Eintrag der Modell-Liste (None: nicht genannt)."""
    for ort in (m, *(m.get(u) for u in _KONTEXT_UNTER)):
        if isinstance(ort, dict):
            n = next((n for f in KONTEXT_FELDER if (n := _zahl(ort.get(f)))), None)
            if n:
                return n
    hoster = [kontext_aus_eintrag(x) for x in m.get("providers") or [] if isinstance(x, dict)]
    return max((n for n in hoster if n), default=None)


_KONTEXT: dict[str, int] = {}          # "anbieter:modell" -> Tokens
_kontext_geladen = False
_kontext_sperre = threading.Lock()
_KONTEXT_LAEUFT: set[str] = set()       # Abfragen im Hintergrund
_KONTEXT_FEHLER: dict[str, float] = {}  # bis wann nicht erneut gefragt wird
KONTEXT_NEUER_VERSUCH_S = 600.0


def _kontext_datei():
    try:
        import paths
        return paths.DATEN / "Cache" / "kontextlaengen.json"
    except Exception:
        return None


def _kontext_laden() -> None:
    global _kontext_geladen
    if _kontext_geladen:
        return
    _kontext_geladen = True
    datei = _kontext_datei()
    try:
        alt = json.loads(datei.read_text(encoding="utf-8")) if datei and datei.is_file() else {}
    except Exception:
        alt = {}
    for k, v in (alt.items() if isinstance(alt, dict) else ()):
        if isinstance(k, str) and (n := _zahl(v)) and k not in _KONTEXT:
            _KONTEXT[k] = n


_speicher_sperre = threading.Lock()     # die Zwischendatei von atomar heißt je Prozess gleich


def _kontext_speichern() -> None:
    datei = _kontext_datei()
    if datei is None:
        return
    with _kontext_sperre:
        inhalt = json.dumps(dict(sorted(_KONTEXT.items())), indent=0)
    try:
        import atomar
        with _speicher_sperre:
            datei.parent.mkdir(parents=True, exist_ok=True)
            atomar.text_schreiben(datei, inhalt)
    except Exception:
        pass


def kontext_merken(provider_id: str, model_id: str, n: int, speichern: bool = True) -> None:
    if not (n := _zahl(n)):
        return
    with _kontext_sperre:
        _kontext_laden()
        schluessel = f"{provider_id}:{model_id}"
        if _KONTEXT.get(schluessel) == n:
            return
        _KONTEXT[schluessel] = n
    if speichern:
        _kontext_speichern()


def _liste_merken(p: Provider, data) -> None:
    neu = [(mid, n) for mid, m in _eintraege(p, data) if (n := kontext_aus_eintrag(m))]
    for mid, n in neu:
        kontext_merken(p.id, mid, n, speichern=False)
    if neu:
        _kontext_speichern()


def liste_abholen(p: Provider, timeout: float = 15.0):
    """Die rohe Modell-Liste eines Anbieters (synchron); merkt die genannten Kontextlängen."""
    r = httpx.get(p.list_url, headers=_auth_headers(p), timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(status_text(p.label, r.status_code))
    data = r.json()
    _liste_merken(p, data)
    return data


def nachfragen_erlaubt() -> bool:
    """In der Test-Suite fragt nichts von selbst im Netz nach (Tests schalten es gezielt frei)."""
    try:
        import paths
        return not paths._testlauf()
    except Exception:
        return True


def im_hintergrund(schluessel: str, arbeit: Callable[[], object]) -> None:
    """`arbeit` einmal in einem Hintergrund-Faden – gleichzeitige Anfragen für denselben Schlüssel warten
    nicht und starten keine zweite. Gibt `arbeit` nichts Wahres zurück und steht der Schlüssel danach
    nicht in _KONTEXT, gilt es als Fehlschlag: erst nach KONTEXT_NEUER_VERSUCH_S wieder."""
    if not nachfragen_erlaubt():
        return
    with _kontext_sperre:
        if schluessel in _KONTEXT_LAEUFT or time.monotonic() < _KONTEXT_FEHLER.get(schluessel, 0.0):
            return
        _KONTEXT_LAEUFT.add(schluessel)

    def lauf():
        ok = False
        try:
            ok = bool(arbeit())
        except Exception:
            pass
        finally:
            with _kontext_sperre:
                _KONTEXT_LAEUFT.discard(schluessel)
                if not ok and schluessel not in _KONTEXT:
                    _KONTEXT_FEHLER[schluessel] = time.monotonic() + KONTEXT_NEUER_VERSUCH_S

    threading.Thread(target=lauf, name="kontextlaenge", daemon=True).start()


def kontext(provider_id: str, model_id: str) -> int | None:
    """Kontextlänge eines Cloud-Modells, wie der Anbieter sie nennt – gemerkt oder None. Ist sie noch
    unbekannt, wird sie im Hintergrund nachgefragt (die Oberfläche wartet nie aufs Netz); bis dahin gilt
    der Richtwert in pricing."""
    schluessel = f"{provider_id}:{model_id}"
    p = CLOUD.get(provider_id)
    if provider_id == "ollama_cloud" and p is not None and p.key() and model_id not in _cloud_caps_cache:
        # einmal je Sitzung /api/show: Denkstufen und Fähigkeiten, auch wenn der Kontext schon gemerkt ist
        im_hintergrund("show:" + model_id, lambda: ollama_cloud_capabilities(model_id) is not None)
    with _kontext_sperre:
        _kontext_laden()
        if schluessel in _KONTEXT:
            return _KONTEXT[schluessel]
    if p is None or not p.key() or provider_id == "ollama_cloud":
        return None
    if p.list_url:
        im_hintergrund(schluessel, lambda: liste_abholen(p, timeout=8.0) and None)
    return None
