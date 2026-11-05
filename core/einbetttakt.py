"""
einbetttakt.py - Wann und wie der Bibliothekar das Gedächtnis einbettet (/embeddingtimer).

Takt (Config `embedding_timer`, Minuten): Statt nach jeder Runde sammelt NemiCLI, was einzubetten ist, und
startet den Bibliothekar höchstens alle N Minuten. 0 = aus (nach der Runde wie bisher). Ein aufgeräumter
Kontext (/compact) und /gedaechtnis indexieren laufen immer sofort – sonst fehlt der ausgelagerte Teil.

Low-Modus (Config `embedding_low`): Der Bibliothekar hört bei Chat oder Bild nicht auf, sondern rechnet langsam
weiter – nur CPU, wenige Kerne, Leerlauf-Priorität, kleine Häppchen mit Pausen.

VRAM-Anteil (Config `embedding_vram_anteil`, Prozent vom gesamten Grafikspeicher): Obergrenze für den
Bibliothekar-Prozess, wenn er auf der GPU rechnet (große Aufträge, /gedaechtnis indexieren).
"""
from __future__ import annotations

STUFEN = (0, 15, 30, 45, 60, 90)
VORGABE = 30
LOW_VORGABE = True
VRAM_ANTEIL_VORGABE = 25
LOW_KERNE = 2                    # Rechen-Threads im Low-Modus
LOW_PAUSE_S = 0.5                # Pause zwischen zwei Häppchen im Low-Modus

_AUS = {"aus", "off", "0", "nie", "nein", "false"}
_AN = {"an", "ein", "on", "ja", "true"}


def normal(wert) -> int | None:
    """„30“, „30 min“, „aus“ → Minuten aus STUFEN; sonst None."""
    w = str(wert if wert is not None else "").strip().lower().removesuffix("min").strip()
    if w in _AUS:
        return 0
    try:
        n = int(w)
    except ValueError:
        return None
    return n if n in STUFEN else None


def text(minuten: int) -> str:
    return "⏸ aus – nach jeder Runde wie bisher" if not minuten else f"⏱ alle {minuten} min"


def _cfg() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def minuten() -> int:
    n = normal(_cfg().get("embedding_timer", VORGABE))
    return VORGABE if n is None else n


def setzen(wert) -> int:
    """Speichert den Takt. ValueError bei unbekanntem Wert."""
    n = normal(wert)
    if n is None:
        raise ValueError(f"Unbekannter Takt {wert!r}. Möglich: aus, {', '.join(str(s) for s in STUFEN[1:])}.")
    import config
    config.update(embedding_timer=n)
    return n


def low() -> bool:
    return _cfg().get("embedding_low", LOW_VORGABE) is not False


def low_setzen(wert=None) -> bool:
    """an/aus; ohne Wert umschalten. ValueError bei unbekanntem Wert."""
    w = str(wert or "").strip().lower()
    if not w:
        neu = not low()
    elif w in _AN:
        neu = True
    elif w in _AUS:
        neu = False
    else:
        raise ValueError(f"Unbekannter Wert {wert!r}. Möglich: an, aus.")
    import config
    config.update(embedding_low=neu)
    return neu


def low_text(an: bool) -> str:
    return "🐢 Low-Modus aktiv 💚" if an else "🐢 Low-Modus aus 🛑"


def vram_anteil() -> int:
    """Prozent vom gesamten VRAM, 5–90."""
    try:
        n = int(_cfg().get("embedding_vram_anteil", VRAM_ANTEIL_VORGABE))
    except (TypeError, ValueError):
        n = VRAM_ANTEIL_VORGABE
    return max(5, min(90, n))
