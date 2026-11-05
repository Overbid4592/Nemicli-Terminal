"""
emojistufe.py - Wie viele Emojis die Persönlichkeit in Antworten nimmt (/emoji, Werkzeug emoji_stufe).

Config `emoji_stufe`: low · middle · strong; fehlt sie (frei), entscheidet die Persönlichkeit.
Die Stufe gibt nur das Maß vor – welche Emojis passen, wählt das Modell aus dem Inhalt.
"""
from __future__ import annotations

STUFEN = ("low", "middle", "strong")
FREI = "frei"
_ALIAS = {"wenig": "low", "niedrig": "low", "leicht": "low", "mittel": "middle", "medium": "middle",
          "mid": "middle", "viel": "strong", "viele": "strong", "stark": "strong", "high": "strong",
          "frei": FREI, "aus": FREI, "auto": FREI, "standard": FREI}
TEXT = {"low": "🙂 low – selten, höchstens eins", "middle": "😊 middle – ab und zu, an passenden Stellen",
        "strong": "🥰 strong – gerne und lebendig, aber passend", FREI: "✨ frei – die Persönlichkeit entscheidet"}

_REGEL = {
    "low": "Emojis selten: höchstens eins pro Antwort und nur, wenn ein Gefühl wirklich zählt. Viele Antworten "
           "kommen ganz ohne aus.",
    "middle": "Emojis ab und zu: etwa ein bis drei pro Antwort, dort, wo sie Stimmung oder Ton tragen.",
    "strong": "Emojis gerne und lebendig: Sie dürfen Gefühle, Dinge und Stimmungen öfter zeigen. Trotzdem nicht "
              "an jedem Satz und nie dasselbe mehrmals hintereinander.",
}


def normal(wert) -> str | None:
    """Eingabe → low · middle · strong · frei; None, wenn sie nicht passt."""
    w = str(wert or "").strip().lower()
    w = _ALIAS.get(w, w)
    return w if w in STUFEN or w == FREI else None


def stufe() -> str:
    """Die eingestellte Stufe – 'frei', wenn keine gesetzt ist."""
    try:
        import config
        return normal(config.load().get("emoji_stufe")) or FREI
    except Exception:
        return FREI


def setzen(wert) -> str:
    """Speichert die Stufe ('frei' entfernt sie). ValueError bei unbekanntem Wert."""
    w = normal(wert)
    if w is None:
        raise ValueError(f"Unbekannte Stufe {wert!r}. Möglich: {', '.join(STUFEN)}, {FREI}.")
    import config
    if not config.update(emoji_stufe=None if w == FREI else w):
        raise ValueError("Die Einstellung ließ sich nicht speichern.")
    return w


def prompt_hinweis() -> str:
    """Abschnitt für den Systemprompt; leer bei 'frei'."""
    s = stufe()
    if s == FREI:
        return ""
    return (f"\n\n# Emojis: {s} (/emoji)\n{_REGEL[s]} Jedes Emoji passt zum Inhalt und zur Stimmung der Stelle, "
            "an der es steht – nicht zur Deko. Keine Emojis in Code, Befehlen, Pfaden und Dateiinhalten; bei "
            "ernsten Themen (Fehler, Sorgen, Trauer) zurückhaltend. Ändern: Werkzeug emoji_stufe.\n")
