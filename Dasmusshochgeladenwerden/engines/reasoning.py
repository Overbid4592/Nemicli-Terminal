"""Reine Auswahl von Denkparametern und Gesamtbudgets, ohne Modellaufrufe.

Ollama: https://docs.ollama.com/api/openai-compatibility
GPT-OSS: https://docs.ollama.com/capabilities/thinking
Kimi: https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart
Kimi 2.5: https://huggingface.co/moonshotai/Kimi-K2.5
OpenAI: https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

Kimi bietet Thinking an/aus, aber keine belegten getrennten Denkstufen.
Die vier NemiCLI-Einstellungen ändern dort nur das Gesamtbudget.
"""

from __future__ import annotations

import re


DEFAULT_STRENGTH = "normal"
BUDGETS = {"schnell": 4096, "normal": 8192, "stark": 16384, "max": 32768}
UNKNOWN_BUDGET = 65536      # Deckel für Modelle ohne Denkstufen-Profil (Haupt-Agent)
HELPER_BUDGET = 4096        # Helfer schreiben keine Romane – kurz helfen, Bericht abgeben
EFFORTS = {"schnell": "low", "normal": "medium", "stark": "high", "max": "high"}

# Bewusst eine Liste bekannter Chat-Modelle: pro/chat/preview/codex und
# unbekannte Varianten bekommen nicht aufgrund eines Präfixes Parameter.
# Neuere Modellstufen und der gpt-5.6-Alias sind in den API-Modellseiten belegt:
# https://developers.openai.com/api/docs/models/gpt-5.4-nano
# https://developers.openai.com/api/docs/models/gpt-5.5
# https://developers.openai.com/api/docs/models/gpt-5.6-sol
_OPENAI_MODELS = {
    "o1", "o3", "o3-mini", "o4-mini",
    "gpt-5", "gpt-5-mini", "gpt-5-nano",
    "gpt-5.1", "gpt-5.2",
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.5", "gpt-5.6",
}


def normalize_strength(strength: str | None) -> str:
    return strength if strength in BUDGETS else DEFAULT_STRENGTH


def profile(ref: str | None) -> str | None:
    """Nur belegte Kombinationen von Anbieter und Modell freischalten."""
    provider, _, model = (ref or "").partition(":")
    model = model.lower()
    if provider in ("ollama", "ollama_cloud"):
        # :cloud / :20b / Quantisierungs-Tags ändern nicht die Modellfamilie.
        family = model.removeprefix("library/").partition(":")[0]
        family = family.removesuffix("-cloud")
        if family in ("kimi-k2.5", "kimi-k2.6"):
            return "kimi"
        if family in ("gpt-oss", "gpt-oss-20b", "gpt-oss-120b"):
            return "ollama_effort"
    if provider == "openai":
        # Offizielle datierte Snapshots derselben bekannten Modellfamilien.
        base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
        if base in _OPENAI_MODELS:
            return "openai_effort"
    return None


def request_options(provider: str, model: str, strength: str | None,
                    *, helper: bool = False) -> dict[str, int | str]:
    """API-Optionen. Unbekannte Modelle (z.B. DeepSeek bei Ollama Cloud) bekommen
    einen hohen Deckel: Denk-Modelle zählen ihr Nachdenken mit, und beim Coden
    brach die Antwort mit 4096 mitten im Code ab. Der Deckel kostet nichts –
    bezahlt wird nur, was wirklich geschrieben wird."""
    kind = profile(f"{provider}:{model}")
    if kind is None:
        return {"max_tokens": HELPER_BUDGET if helper else UNKNOWN_BUDGET}
    strength = normalize_strength(strength)
    budget = max(BUDGETS[strength], 8192) if helper else BUDGETS[strength]
    token_key = "max_completion_tokens" if kind == "openai_effort" else "max_tokens"
    # Bei Kimi fordert high Thinking an; es bezeichnet keine zusätzliche
    # modellinterne Stufe. Die Unterscheidung der Menüpunkte ist das Budget.
    effort = "high" if kind == "kimi" else EFFORTS[strength]
    return {token_key: budget, "reasoning_effort": effort}


def strength_menu(ref: str | None) -> dict[str, str]:
    kind = profile(ref)
    if kind is None:
        return {}
    if kind == "kimi":
        return {key: "Thinking an · bis zu " + f"{budget:,}".replace(",", ".")
                + " Tokens für Denken und Antwort" for key, budget in BUDGETS.items()}
    return {
        "schnell": "niedrige Denkstufe · bis zu 4.096 Tokens für Denken und Antwort",
        "normal": "mittlere Denkstufe · bis zu 8.192 Tokens für Denken und Antwort",
        "stark": "hohe Denkstufe · bis zu 16.384 Tokens für Denken und Antwort",
        "max": "hohe Denkstufe wie stark · größeres Budget: 32.768 Tokens",
    }


def strength_note(ref: str | None, strength: str | None) -> str:
    kind = profile(ref)
    if kind is None:
        return ""
    strength = normalize_strength(strength)
    description = strength_menu(ref)[strength]
    if kind == "kimi":
        return description + ". Kimi bietet keine getrennten Denkstufen; geändert wird das Budget."
    if strength == "max":
        return description + " für Denken und Antwort; keine zusätzliche Denkstufe."
    return description + "."
