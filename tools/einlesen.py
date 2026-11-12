"""einlesen.py - Der Einlese-Speicher: Was noch keinen Vektor hat, wartet gesammelt auf einen Durchgang.

Der Bibliothekar schreibt Chats, Notizen, Skills, Berichte, Wissen und Bilder sofort ohne Modell in memory.db;
die Stichwortsuche findet sie gleich. Der Füllstand zählt alles ohne Vektor des aktiven Modells (Text: Zeichen / 4,
Bild: BILD_TOKEN). Ab AUTO_AB startet der Durchgang von selbst, sobald keine Runde läuft und die GPU Platz hat.
Der Durchgang rechnet nur auf der GPU (ohne Grafikkarte: CPU) und trennt beim Start einen festen Block ab; was danach
dazukommt, gehört zum nächsten Block. /einlesen und das Werkzeug gedaechtnis_einlesen starten ihn früher.
"""
from __future__ import annotations

VOLL = 100_000
ORANGE_AB = 50_000
ROT_AB = 80_000
AUTO_AB = 99_000
MELDEN_NACH_S = 600.0            # so lange darf ein voller Speicher warten, bevor die KI davon erfährt
BILD_TOKEN = 800                 # wie pricing.IMG_TOKENS
AUFSCHRIFT = "100k"
GRUEN, ORANGE, DUNKELROT = "#2e9e4f", "#e08a1e", "#8b0000"
LEER = "#3a3a3a"
ZELLEN = 14                      # Breite des Balkens in der Statuszeile; mit halben Zellen 28 Schritte (je ~3,6k)

_wunsch = {"an": False}


def farbe(token: int) -> str:
    if token >= ROT_AB:
        return DUNKELROT
    return ORANGE if token >= ORANGE_AB else GRUEN


def _mischen(a: str, b: str, anteil: float) -> str:
    """Farbe a, um `anteil` (0–1) in Richtung b verschoben."""
    x, y = (tuple(int(f[i:i + 2], 16) for i in (1, 3, 5)) for f in (a, b))
    return "#" + "".join(f"{round(p + (q - p) * anteil):02x}" for p, q in zip(x, y))


def halb(token: int) -> str:
    """Farbe einer halb vollen Zelle."""
    return _mischen(farbe(token), LEER, 0.55)


def hell(farbe_: str) -> str:
    """Kurzes Aufleuchten, wenn sich der Füllstand ändert."""
    return _mischen(farbe_, "#ffffff", 0.45)


def halbe(token: int, zellen: int = ZELLEN) -> int:
    """So viele halbe Zellen sind gefüllt; schon wenig Inhalt zeigt eine halbe Zelle."""
    if token <= 0:
        return 0
    return max(1, min(2 * zellen, round(token / VOLL * zellen * 2)))


def zahl(token: int) -> str:
    """Füllstand kurz: 300 · 4k · 74k."""
    return str(int(token)) if token < 1000 else f"{round(token / 1000)}k"


def zeichen(token: int | None = None, zellen: int = ZELLEN) -> str:
    """Die Aufschrift, mittig über die Zellen: der aktuelle Stand vor dem Maß („4k │ 100k“)."""
    text = AUFSCHRIFT if token is None else f"{zahl(token)} │ {AUFSCHRIFT}"
    return text.center(zellen)


def kurz(token: int) -> str:
    return f"{round(token / 1000)}k von {AUFSCHRIFT}"


def stand() -> int | None:
    """Füllstand in Token; None ohne Embedding-Modell (dann gibt es nichts abzuarbeiten)."""
    import indexdb
    return indexdb.ohne_vektor_token(BILD_TOKEN)


def hinweis(token: int, grund: str) -> str:
    """Notiz an die KI (lernschleife.melden), wenn ein voller Speicher länger warten muss."""
    return (f"Der Einlese-Speicher des Gedächtnisses ist voll ({kurz(token)}) und wartet: {grund}. Er startet von "
            "selbst, sobald es geht, nichts geht verloren. Sag es dem Nutzer kurz in deinen eigenen Worten.")


def wuenschen() -> None:
    """Das Werkzeug merkt den Durchgang vor; er startet, sobald es geht."""
    _wunsch["an"] = True


def abholen() -> bool:
    an, _wunsch["an"] = _wunsch["an"], False
    return an
