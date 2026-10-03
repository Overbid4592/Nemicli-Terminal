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


# Ollama Cloud – Stand 19.09.2026, je Modell über https://ollama.com/api/show
# abgefragt (model_info.*.context_length und "thinking.values"/"default").
# Schlüssel = Modellfamilie (alles vor dem ":"). ctx = Kontextfenster in Tokens,
# think = welche Denk-Werte reasoning_effort annimmt (None = an/aus geht nicht,
# False in der Liste = abschaltbar → "none"), default = Voreinstellung.
# Die OpenAI-Schnittstelle von Ollama kennt reasoning_effort = none/low/medium/high/max
# (https://docs.ollama.com/api/openai-compatibility).
# Neue Modelle: python -c "…/api/show" – oder einfach hier eintragen.
OLLAMA_CLOUD: dict[str, dict] = {
    "deepseek-v4-flash":   {"ctx": 1_048_576, "think": (False, "low", "high", "max"), "default": "low"},
    "deepseek-v4-pro":     {"ctx": 1_048_576, "think": (False, "low", "high", "max"), "default": "low"},
    "deepseek-v4.1-flash": {"ctx": 1_048_576, "think": (False, "low", "high", "max"), "default": "high"},
    "gemma4":              {"ctx":   262_144, "think": (False, True), "default": False},
    "glm-5.1":             {"ctx":   202_752, "think": (False, True), "default": True},
    "glm-5.2":             {"ctx": 1_048_576, "think": (False, "high", "max"), "default": "high"},
    "glm-5.3":             {"ctx": 1_048_576, "think": ("low", "high", "max"), "default": "max"},
    "glm-5.3-flash":       {"ctx": 1_048_576, "think": ("low", "high", "max"), "default": "max"},
    "gpt-oss":             {"ctx":   131_072, "think": ("low", "medium", "high"), "default": "medium"},
    "kimi-k2.5":           {"ctx":   262_144, "think": (False, True), "default": True},
    "kimi-k2.6":           {"ctx":   262_144, "think": (False, True), "default": True},
    "kimi-k2.7-code":      {"ctx":   262_144, "think": (False, True), "default": True},
    "kimi-k3":             {"ctx": 1_048_576, "think": (False, "low", "high", "max"), "default": "max"},
    "minimax-m2.7":        {"ctx":   196_608, "think": (True,), "default": True},
    "minimax-m3":          {"ctx":   512_000, "think": None, "default": None},
    "mistral-large-3":     {"ctx":   262_144, "think": None, "default": None},
    "nemotron-3-nano":     {"ctx":   262_144, "think": (False, True), "default": True},
    "nemotron-3-super":    {"ctx":   262_144, "think": (False, True), "default": True},
    "nemotron-3-ultra":    {"ctx":   262_144, "think": (False, True), "default": True},
    "qwen3.5":             {"ctx":   262_144, "think": (False, True), "default": True},
}
# Helfer (Bibliothekar, Zeitplan, Spielzug …) auf Denk-Modellen: das Nachdenken
# zählt mit ins Budget – mit 4096 war nach dem Denken kein Platz mehr für die
# Antwort („Tokenbudget erreicht“ bei jedem Mühle-Zug). Deshalb mehr Luft und
# die niedrigste Denkstufe.
CLOUD_HELPER_BUDGET = 16384


def ollama_family(model: str) -> str:
    """'deepseek-v4-flash:0731' → 'deepseek-v4-flash', 'kimi-k2.5:cloud' → 'kimi-k2.5'."""
    family = model.lower().removeprefix("library/").partition(":")[0]
    return family.removesuffix("-cloud")


def cloud_effort(family: str, strength: str, *, helper: bool = False) -> str | None:
    """reasoning_effort für ein Ollama-Cloud-Modell – oder None (nichts schicken).

    Stufen-Modelle (low/high/max): schnell → niedrigste Stufe, normal → die
    Voreinstellung des Modells, stark → high, max → max (oder high, wenn es
    kein max gibt). An/aus-Modelle: schnell → "none" (Denken aus), sonst wie
    voreingestellt. Helfer nehmen immer die niedrigste Stufe."""
    info = OLLAMA_CLOUD.get(family)
    if not info or not info["think"]:
        return None
    werte = info["think"]
    stufen = [w for w in werte if isinstance(w, str)]
    if not stufen:                                   # nur an/aus
        if False in werte and (helper is False and strength == "schnell"):
            return "none"
        return None
    if helper or strength == "schnell":
        return stufen[0]
    if strength == "normal":
        d = info["default"]
        return d if isinstance(d, str) else stufen[0]
    if strength == "stark":
        return "high" if "high" in stufen else stufen[-1]
    return "max" if "max" in stufen else ("high" if "high" in stufen else stufen[-1])


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
        if provider == "ollama_cloud" and family in OLLAMA_CLOUD:
            return "ollama_cloud"
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
    if kind == "ollama_cloud":
        # Flatrate-Modelle mit riesigem Fenster: der Haupt-Agent behält den hohen
        # Deckel (Denken zählt mit), Helfer bekommen genug Luft und Stufe niedrig.
        strength = normalize_strength(strength)
        opts: dict[str, int | str] = {"max_tokens": CLOUD_HELPER_BUDGET if helper else UNKNOWN_BUDGET}
        effort = cloud_effort(ollama_family(model), strength, helper=helper)
        if effort:
            opts["reasoning_effort"] = effort
        return opts
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
    if kind == "ollama_cloud":
        _prov, _, model = (ref or "").partition(":")
        fam = ollama_family(model)
        info = OLLAMA_CLOUD[fam]
        ctx = f"{info['ctx']:,}".replace(",", ".")
        out = {}
        for key in BUDGETS:
            e = cloud_effort(fam, key)
            if info["think"] is None:
                txt = "kein Denk-Modell"
            elif e == "none":
                txt = "Denken aus"
            elif e is None:
                txt = "Denken an (Voreinstellung)"
            else:
                txt = f"Denkstufe {e}"
            out[key] = f"{txt} · Kontext {ctx} Tokens · Antwort bis 65.536"
        return out
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
    if kind == "ollama_cloud":
        return description + " (Ollama Cloud, Stand 19.09.2026)."
    if strength == "max":
        return description + " für Denken und Antwort; keine zusätzliche Denkstufe."
    return description + "."
