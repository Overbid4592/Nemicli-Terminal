"""
models.py - Modell-Referenzen und Denk-Stärke.

Modell-Referenzen ("refs") sind einheitlich `<anbieter>:<modell>`:
    anthropic:claude-opus-4-8       -> Cloud (siehe providers.py)
    openai:gpt-4o                   -> Cloud (siehe providers.py)
    ollama:gemma3                   -> Ollama auf diesem PC

Bis zum 15.09.2026 stand hier auch der llama.cpp-Teil: eigene GGUF-Dateien
in Models/, ein selbst gestarteter llama-server, Vision-Helfer, Build- und
CVE-Prüfung. Das ist auf Wunsch des Nutzers komplett entfallen - lokale
Modelle laufen jetzt ausschließlich über Ollama.

`Models/` bleibt: dort liegen weiterhin die Bild-Modelle (Models/checkpoints)
und die Embeddings fürs Gedächtnis.
"""

from __future__ import annotations

from pathlib import Path

import reasoning

# --- Wo die Modelle liegen -------------------------------------------------
try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent      # Projekt-Wurzel
MODELS_DIR = _ROOT / "Models"            # Bild-Modelle, Embeddings

LLM_DIR = MODELS_DIR                     # Alias (Altcode-kompatibel)


STRENGTHS: dict[str, tuple[str, str]] = {
    "schnell": ("low",    "schnell & günstig, wenig Nachdenken"),
    "normal":  ("medium", "ausgewogen (Standard)"),
    "stark":   ("high",   "denkt gründlicher nach"),
    "max":     ("max",    "maximale Denkleistung, langsamer"),
}
DEFAULT_STRENGTH = "normal"


def effort_for(strength: str) -> str:
    return STRENGTHS.get(strength, STRENGTHS[DEFAULT_STRENGTH])[0]


def strength_supported(ref: str | None) -> bool:
    """Ob für dieses Backend eine Denkstärke oder ein Denkbudget bekannt ist."""
    return bool(ref and (ref.startswith("anthropic:") or reasoning.profile(ref)))


def strength_menu(ref: str | None = None) -> dict[str, str]:
    """Modellgerechte Beschreibungen; ohne Ref bleibt das klassische Menü."""
    if ref is None or ref.startswith("anthropic:"):
        return {k: desc for k, (_eff, desc) in STRENGTHS.items()}
    return reasoning.strength_menu(ref)


def strength_note(ref: str | None, strength: str) -> str:
    """Erläutert die wirksame Einstellung ohne unbelegte Denkstufen."""
    if ref and ref.startswith("anthropic:"):
        return STRENGTHS.get(strength, STRENGTHS[DEFAULT_STRENGTH])[1]
    return reasoning.strength_note(ref, strength)


# --- Referenz-Helfer -------------------------------------------------------

def make_ref(provider: str, model_id: str) -> str:
    return f"{provider}:{model_id}"


def split_ref(ref: str) -> tuple[str, str]:
    """'openai:gpt-4o' -> ('openai', 'gpt-4o'). Ohne ':' -> ('', ref)."""
    provider, sep, model_id = ref.partition(":")
    if not sep:
        return "", ref
    return provider, model_id


_GGUF_FMT = {0: "<b", 1: "<B", 2: "<h", 3: "<H", 4: "<i", 5: "<I",
             6: "<f", 7: "<?", 10: "<q", 11: "<Q", 12: "<d"}
_meta_cache: dict = {}

_MAX_KV = 8192            # so viele Key/Value-Paare lesen wir höchstens
_MAX_STR = 1 << 20        # eine einzelne Zeichenkette: max 1 MB
_MAX_ARR = 1 << 28        # Array-Länge: harte Obergrenze (gegen u64-Müll)
_MAX_DEPTH = 8            # Schachtelungstiefe von Arrays

