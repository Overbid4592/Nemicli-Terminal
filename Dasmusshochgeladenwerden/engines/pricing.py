"""
pricing.py - Kontext-Größen & grobe Kosten je Modell.

Zwei kleine Nachschlage-Tabellen, nach Modell-Namen (Teilstring) gematcht:

  • context_window(ref) -> wie viele Tokens passen ins Modell-Gedächtnis
  • cost(ref, in_tok, out_tok) -> grobe Kosten EINES Zuges in US-Dollar

Lokale Modelle & Ollama kosten nichts (cost = 0). Die Cloud-Preise sind
Richtwerte (Stand 2026, $ je 1 Mio. Tokens) – nur als Orientierung gedacht,
keine exakte Abrechnung. Neue Modelle einfach unten in die Tabellen eintragen.
"""

from __future__ import annotations

import models as M

# --- Kontext-Größen (Tokens) ----------------------------------------------
# Reihenfolge zählt: das ERSTE passende Teilstück gewinnt.
_CTX: list[tuple[str, int]] = [
    ("claude",      200_000),
    ("gpt-5",       400_000),
    ("gpt-4.1",     1_000_000),
    ("gpt-4o",      128_000),
    ("o3",          200_000),
    ("o4",          200_000),
    ("gpt",         128_000),
    ("gemini",      1_000_000),
    ("grok",        131_072),
    ("deepseek",    131_072),
    ("mistral",     131_072),
    ("llama",       131_072),
    ("qwen",        131_072),
    ("glm",         131_072),
    ("gemma",       128_000),
]

# Fallback, wenn wir ein Cloud-Modell nicht kennen
DEFAULT_CTX = 128_000


def context_window(ref: str | None) -> int:
    """Wie viele Tokens passen ins Gedächtnis dieses Modells (Richtwert)."""
    if not ref:
        return DEFAULT_CTX
    _prov, mid = M.split_ref(ref)
    low = mid.lower()
    for needle, n in _CTX:
        if needle in low:
            return n
    return DEFAULT_CTX


# --- Preise ($ je 1 Mio. Tokens: Eingabe, Ausgabe) ------------------------
# Richtwerte, Stand 2026 – nur zur groben Orientierung.
_PRICE: list[tuple[str, float, float]] = [
    ("claude-opus",     15.0,   75.0),
    ("claude-sonnet",    3.0,   15.0),
    ("claude-haiku",     0.80,   4.0),
    ("claude",           3.0,   15.0),
    ("gpt-5",            1.25,  10.0),
    ("gpt-4.1-mini",     0.40,   1.60),
    ("gpt-4.1",          2.0,    8.0),
    ("gpt-4o-mini",      0.15,   0.60),
    ("gpt-4o",           2.50,  10.0),
    ("o3",               2.0,    8.0),
    ("o4-mini",          1.10,   4.40),
    ("gemini-2.5-pro",   1.25,  10.0),
    ("gemini-2.5-flash", 0.30,   2.50),
    ("gemini",           0.30,   2.50),
    ("grok",             3.0,   15.0),
    ("deepseek",         0.27,   1.10),
    ("mistral",          0.40,   2.0),
]


def price_per_million(ref: str | None) -> tuple[float, float] | None:
    """(Eingabe, Ausgabe) in $ je 1 Mio. Tokens – oder None (kostenlos/unbekannt)."""
    if not ref:
        return None
    prov, mid = M.split_ref(ref)
    if prov == "ollama":                 # Ollama ist lokal -> kostenlos
        return None
    if prov == "ollama_cloud":           # Ollama Cloud rechnet nicht je Token ab
        return None                      # (Abo/Kontingent) -> kein Token-Preis
    low = mid.lower()
    for needle, pin, pout in _PRICE:
        if needle in low:
            return pin, pout
    return None


# --- Verlauf kappen (nach Größe, nicht nach Anzahl) ------------------------
# Vorher kappten die Motoren stur bei den letzten 40 Nachrichten. Das ist in
# beide Richtungen falsch: 40 kurze Zeilen passen locker rein (das Modell
# vergisst grundlos), 40 lange sprengen den Kontext trotzdem. Also schauen wir
# auf die geschätzte Größe statt aufs Zählen.

IMG_TOKENS = 800        # grober Ansatz für EIN Bild im Verlauf
HISTORY_SHARE = 0.55    # so viel vom Kontextfenster darf der Verlauf belegen
MIN_KEEP = 4            # so viele Nachrichten bleiben immer stehen
HARD_MAX = 200          # Notbremse gegen endlos wachsende Listen


def est_tokens(content) -> int:
    """Grobe Token-Schätzung (~4 Zeichen = 1 Token). Bilder zählen pauschal.
    `content` ist entweder ein String oder eine Liste von Blöcken (Vision)."""
    if isinstance(content, str):
        return len(content) // 4 + 1
    total = 0
    for part in content or []:
        if not isinstance(part, dict):
            total += len(str(part)) // 4 + 1
            continue
        if part.get("type") in ("image_url", "image"):
            total += IMG_TOKENS
        else:
            total += len(str(part.get("text") or "")) // 4 + 1
    return total


def trim_history(messages: list[dict], ref: str | None,
                 share: float = HISTORY_SHARE) -> list[dict]:
    """Kappt den Verlauf von HINTEN (Neues bleibt), bis er ins Kontextfenster
    passt. Der Rest des Fensters bleibt für System-Prompt und Antwort frei."""
    if len(messages) <= MIN_KEEP:
        return messages
    if len(messages) > HARD_MAX:
        messages = messages[-HARD_MAX:]

    budget = max(2048, int(context_window(ref) * share))
    kept: list[dict] = []
    used = 0
    for msg in reversed(messages):
        used += est_tokens(msg.get("content"))
        if used > budget and len(kept) >= MIN_KEEP:
            break
        kept.append(msg)
    kept.reverse()

    # Nicht mit einer Antwort ohne zugehörige Frage anfangen – das verwirrt
    # manche Anbieter (Anthropic lehnt es sogar ab).
    while kept and kept[0].get("role") == "assistant":
        kept.pop(0)
    return kept or messages[-MIN_KEEP:]


def cost(ref: str | None, in_tok: int, out_tok: int) -> float:
    """Grobe Kosten eines Zuges in US-Dollar (0.0 bei lokal/unbekannt)."""
    pp = price_per_million(ref)
    if pp is None:
        return 0.0
    pin, pout = pp
    return (in_tok * pin + out_tok * pout) / 1_000_000
