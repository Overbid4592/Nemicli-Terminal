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
    if _prov == "gguf":                       # eigener Motor: eingestellter Kontext
        import gguflokal
        return gguflokal.wirksamer_kontext(mid)
    if _prov == "ollama_cloud":               # echte Werte je Modell (reasoning.OLLAMA_CLOUD)
        import reasoning
        info = reasoning.OLLAMA_CLOUD.get(reasoning.ollama_family(low))
        if info:
            return int(info["ctx"])
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
    if prov in ("ollama", "gguf"):       # lokal (Ollama, eigener GGUF-Motor) -> kostenlos
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
MIN_KEEP = 3            # so viele Nachrichten bleiben nach dem Aufräumen stehen
HARD_MAX = 200          # Notbremse gegen endlos wachsende Listen

# Aufräumen statt gleitend kürzen: Der Verlauf wächst, bis er das Budget sprengt, dann
# bleiben nur die letzten MIN_KEEP Nachrichten. Gleitendes Kürzen änderte fast jede
# Runde den Anfang des Gesprächs – der Präfix-Cache musste dann alles neu lesen. Der
# Rest bleibt im Chat gespeichert und im Gedächtnis dieses Chats durchsuchbar.


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
    """Räumt den Verlauf auf, sobald er das Budget (Anteil `share` des Kontextfensters)
    übersteigt: dann bleiben die letzten MIN_KEEP Nachrichten. Der Rest des Fensters
    bleibt für System-Prompt und Antwort frei."""
    if len(messages) <= MIN_KEEP:
        return messages
    budget = max(2048, int(context_window(ref) * share))
    if len(messages) <= HARD_MAX and sum(est_tokens(m.get("content")) for m in messages) <= budget:
        return messages
    kept = messages[-MIN_KEEP:]
    # Nicht mit einer Antwort ohne zugehörige Frage anfangen – das verwirrt
    # manche Anbieter (Anthropic lehnt es sogar ab).
    while len(kept) > 1 and kept[0].get("role") == "assistant":
        kept = kept[1:]
    return kept


def kuerzen(backend) -> None:
    """trim_history für ein Backend. Was vorne herausfällt, sammelt `backend.weggefallen` –
    das Hauptprogramm sichert daraus Bleibendes, bevor es vergessen ist."""
    alt = backend.messages
    neu = trim_history(alt, backend.model)
    n = len(alt) - len(neu)
    if n > 0:
        backend.weggefallen = (list(getattr(backend, "weggefallen", None) or []) + list(alt[:n]))[-60:]
        backend.aufgeraeumt = n
    backend.messages = neu
    try:                                             # für „vorhin in diesem Chat“ (indexdb)
        import chatstore
        chatstore.im_kontext_setzen(neu)
    except Exception:
        pass


def aufraeum_hinweis(backend) -> str | None:
    """Einmaliger Hinweis für die Oberfläche, nachdem kuerzen aufgeräumt hat."""
    n = getattr(backend, "aufgeraeumt", 0)
    if not n:
        return None
    backend.aufgeraeumt = 0
    return (f"🧹 Kontext voll – aufgeräumt: {n} ältere Nachrichten ausgelagert, die letzten "
            f"{MIN_KEEP} bleiben. Alles bleibt im Gedächtnis dieses Chats auffindbar.")


def weggefallen_holen(backend) -> list[dict]:
    """Seit dem letzten Abholen herausgekürzte Nachrichten (und leeren)."""
    weg = list(getattr(backend, "weggefallen", None) or [])
    if backend is not None:
        backend.weggefallen = []
    return weg


def cost(ref: str | None, in_tok: int, out_tok: int) -> float:
    """Grobe Kosten eines Zuges in US-Dollar (0.0 bei lokal/unbekannt)."""
    pp = price_per_million(ref)
    if pp is None:
        return 0.0
    pin, pout = pp
    return (in_tok * pin + out_tok * pout) / 1_000_000
