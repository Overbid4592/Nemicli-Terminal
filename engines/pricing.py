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

# Gemessene Kontexte (lokales Ollama: so, wie Ollama das Modell geladen hat) gelten vor den Richtwerten.
_GEMESSEN: dict[str, int] = {}


def kontext_merken(ref: str, n: int) -> None:
    if ref and n > 0:
        _GEMESSEN[ref] = int(n)


def context_window(ref: str | None) -> int:
    """Wie viele Tokens passen ins Gedächtnis dieses Modells (Richtwert)."""
    if not ref:
        return DEFAULT_CTX
    if ref in _GEMESSEN:
        return _GEMESSEN[ref]
    _prov, mid = M.split_ref(ref)
    low = mid.lower()
    if _prov == "gguf":                       # eigener Motor: eingestellter Kontext
        import gguflokal
        return gguflokal.wirksamer_kontext(mid)
    if _prov == "funke":                      # Funke Groß kürzt den Verlauf selbst auf sein Gedächtnis
        return 512
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
    if prov in ("ollama", "gguf", "funke"):  # lokal (Ollama, eigener GGUF-Motor, Funke) -> kostenlos
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
START_TOKENS = 20_000   # so viel vom letzten Gespräch lädt der Start; der Rest bleibt durchsuchbar

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


def _frage(m: dict) -> bool:
    """Eine Nachricht des Nutzers (kein Werkzeug-Ergebnis) – dort darf ein Verlauf beginnen."""
    if m.get("role") != "user":
        return False
    inhalt = m.get("content")
    return isinstance(inhalt, str) or not any(isinstance(t, dict) and t.get("type") == "tool_result"
                                              for t in inhalt or [])


def gespraechsende(messages: list[dict], budget: int = START_TOKENS) -> list[dict]:
    """Das Ende eines gespeicherten Gesprächs: höchstens `budget` Tokens, beginnend bei einer Frage des
    Nutzers. Ist schon die letzte Runde größer, ab ihrer Frage."""
    start, summe = len(messages), 0
    for i in range(len(messages) - 1, -1, -1):
        summe += est_tokens(messages[i].get("content"))
        if summe > budget:
            break
        start = i
    while start < len(messages) and not _frage(messages[start]):
        start += 1
    if start >= len(messages):
        start = next((i for i in range(len(messages) - 1, -1, -1) if _frage(messages[i])), len(messages))
    return list(messages[start:])


# Bilder zählen bei den Tokens pauschal, gehen aber als Data-URI mit (je Bild bis über 1 MB).
# Cloud-Anbieter lehnen zu große Anfragen ab (HTTP 413) – deshalb eine eigene Grenze in Bytes.
BILDER_MAX_BYTES = 6 * 1024 * 1024
BILD_ERSATZ = "[Bild aus dem älteren Verlauf entfernt – die Anfrage wäre sonst zu groß]"


def _bild_groesse(part) -> int:
    if not isinstance(part, dict) or part.get("type") not in ("image_url", "image"):
        return 0
    return len(str(part))


def bilder_begrenzen(messages: list[dict], max_bytes: int = BILDER_MAX_BYTES) -> int:
    """Ersetzt die ältesten Bilder im Verlauf durch einen Hinweis, bis alle Bilder zusammen
    höchstens max_bytes groß sind. Das neueste Bild bleibt immer. Gibt die Anzahl zurück."""
    bilder = [(i, j, g) for i, m in enumerate(messages) if isinstance(m.get("content"), list)
              for j, part in enumerate(m["content"]) if (g := _bild_groesse(part))]
    summe = sum(g for _, _, g in bilder)
    ersetzt = 0
    for i, j, g in bilder[:-1]:
        if summe <= max_bytes:
            break
        inhalt = list(messages[i]["content"])          # Kopie: gespeicherte Chats nicht mitändern
        inhalt[j] = {"type": "text", "text": BILD_ERSATZ}
        messages[i] = dict(messages[i], content=inhalt)
        summe -= g
        ersetzt += 1
    return ersetzt


def zu_gross(exc: Exception) -> bool:
    """Lehnt der Anbieter die Anfrage wegen ihrer Größe ab (HTTP 413)?"""
    text = str(exc).lower()
    return (getattr(exc, "status_code", None) == 413 or "error code: 413" in text
            or "too large" in text)


def kuerzen(backend) -> None:
    """trim_history für ein Backend. Was vorne herausfällt, sammelt `backend.weggefallen` –
    das Hauptprogramm sichert daraus Bleibendes, bevor es vergessen ist. Danach die
    Bilder-Grenze (bilder_begrenzen)."""
    alt = backend.messages
    neu = trim_history(alt, backend.model)
    if (n_bilder := bilder_begrenzen(neu)):
        backend.bilder_entfernt = getattr(backend, "bilder_entfernt", 0) + n_bilder
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


def schnitt(messages: list, n: int) -> int:
    """Wie viele der ältesten Nachrichten `abschneiden(n)` wirklich auslagert: nie alle, und der
    Verlauf beginnt danach nicht mit einer Antwort."""
    n = max(0, min(n, len(messages) - 1))
    while n < len(messages) - 1 and messages[n].get("role") == "assistant":
        n += 1
    return n


def abschneiden(backend, n: int) -> int:
    """Die ältesten `n` Nachrichten auslagern (wie kuerzen: sie landen in `weggefallen`).
    Der Verlauf beginnt danach nie mit einer Antwort. Gibt die Zahl der ausgelagerten zurück."""
    alt = backend.messages
    n = schnitt(alt, n)
    if n <= 0:
        return 0
    backend.weggefallen = (list(getattr(backend, "weggefallen", None) or []) + list(alt[:n]))[-60:]
    backend.aufgeraeumt = getattr(backend, "aufgeraeumt", 0) + n
    backend.messages = alt[n:]
    try:
        import chatstore
        chatstore.im_kontext_setzen(backend.messages)
    except Exception:
        pass
    return n


def aufraeum_hinweis(backend) -> str | None:
    """Einmaliger Hinweis für die Oberfläche, nachdem kuerzen aufgeräumt hat."""
    n = getattr(backend, "aufgeraeumt", 0)
    b = getattr(backend, "bilder_entfernt", 0)
    if not n and not b:
        return None
    backend.aufgeraeumt = backend.bilder_entfernt = 0
    teile = []
    if n:
        teile.append(f"🧹 Kontext voll – aufgeräumt: {n} ältere Nachrichten ausgelagert. Das Wichtigste "
                     "kommt ins Gedächtnis, alles bleibt in diesem Chat auffindbar.")
    if b:
        teile.append(f"🖼 {b} ältere(s) Bild(er) aus dem Verlauf genommen – sonst wäre die Anfrage zu groß.")
    return " ".join(teile)


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
