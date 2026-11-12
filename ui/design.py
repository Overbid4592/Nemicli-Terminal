"""design.py – das gemeinsame Aussehen aller NemiCLI-Fenster (Desktop-Fenster, Charakter, Musik, Kugel, App-Store,
Vorschau): Farben, Rundungen und Schrift an einer Stelle.

Schriftgröße je Rolle (/schriftgröße, Config `schriftgroesse` = {rolle: px}): die ganze Oberfläche, die Antworten
der KI, die eigenen Nachrichten und das Terminal-Fenster lassen sich einzeln größer oder kleiner stellen. Neue
Texte nehmen eine Änderung sofort, schon gezeichnete Fenster beim nächsten Öffnen.
"""
from __future__ import annotations

import time

# Farbschemata der Fenster. Rollen: grund … flaeche_hoch (Flächen von dunkel nach hell), rand, text/dim/blass,
# cyan = Akzent der Persönlichkeit, lila = Akzent des Nutzers, blase_a/blase_b = Verlauf der Nutzer-Blase.
# Gewählt wird mit /theme: heißt das Terminal-Theme wie eins hier, färbt es auch die Fenster, sonst gilt „klassik“.
PALETTEN: dict[str, dict[str, str]] = {
    "klassik": {
        "grund": "#0c0c10", "leiste": "#111116", "flaeche": "#1b1b22", "flaeche_hell": "#26262f",
        "flaeche_hoch": "#30303b", "rand": "#2c2c36", "text": "#ececf1", "dim": "#a3a3ae", "blass": "#6f6f7b",
        "cyan": "#22d3ee", "lila": "#a855f7", "blase_a": "#4c1d95", "blase_b": "#155e75",
        "ok": "#34d399", "warn": "#fbbf24", "fehler": "#f87171",
    },
    "nachtglas": {
        "grund": "#0a0e1a", "leiste": "#0e1424", "flaeche": "#141b2e", "flaeche_hell": "#1c2540",
        "flaeche_hoch": "#263152", "rand": "#27324f", "text": "#e8ecf8", "dim": "#9aa6c4", "blass": "#67718e",
        "cyan": "#5ee7ff", "lila": "#9d7bff", "blase_a": "#3b2a7a", "blase_b": "#134a66",
        "ok": "#4ade80", "warn": "#fcd34d", "fehler": "#fb7185",
    },
    "pastell": {
        "grund": "#14111a", "leiste": "#1a1622", "flaeche": "#221d2c", "flaeche_hell": "#2c2639",
        "flaeche_hoch": "#372f47", "rand": "#3a3247", "text": "#f3eef8", "dim": "#b9aec8", "blass": "#857a96",
        "cyan": "#8be3c9", "lila": "#f5a6d0", "blase_a": "#6b3a63", "blase_b": "#2f5f57",
        "ok": "#9be7b0", "warn": "#f8d58c", "fehler": "#f49aa6",
    },
    "neon": {
        "grund": "#050507", "leiste": "#09090d", "flaeche": "#111117", "flaeche_hell": "#1a1a22",
        "flaeche_hoch": "#24242e", "rand": "#2e2e3a", "text": "#f5f5fa", "dim": "#a8a8b8", "blass": "#70707e",
        "cyan": "#00f0ff", "lila": "#ff2fd0", "blase_a": "#5a0f6e", "blase_b": "#00525c",
        "ok": "#39ff88", "warn": "#ffe14d", "fehler": "#ff4d6d",
    },
}


def palette_name() -> str:
    """Name des Farbschemas der Fenster nach dem Terminal-Theme (Config `theme`)."""
    try:
        import config
        theme = config.load().get("theme")
    except Exception:
        theme = None
    return theme if theme in PALETTEN else "klassik"


# Ein Dict für alle: die Fenster lesen FARBE beim Aufbau. Das Schema gilt ab dem Öffnen eines Fensters.
FARBE: dict[str, str] = dict(PALETTEN[palette_name()])

RUND = {"klein": 8, "mittel": 12, "gross": 16}          # Ecken in px

# Rolle: (Zeichen, Titel, Vorgabe in px). „fenster“ skaliert die ganze Oberfläche (Vorgabe 14 px = 10,5 pt).
ROLLEN: dict[str, tuple[str, str, int]] = {
    "fenster": ("🪟", "Ganze Oberfläche", 14),
    "ki": ("🤖", "Antworten der KI", 15),
    "du": ("💬", "Deine Nachrichten", 15),
    "terminal": ("🖥", "Terminal-Fenster", 15),
}
GROESSEN = (12, 13, 14, 15, 16, 18, 20, 22, 24, 28)    # Auswahl im Menü
KLEINSTE, GROESSTE = 10, 36
_FAMILIEN = ("Segoe UI Variable Text", "Segoe UI")

_stand: dict = {}
_LESEN_ALLE_S = 2.0                                    # Config höchstens so oft neu lesen


def _einstellung() -> dict:
    if time.monotonic() - _stand.get("gelesen", -99.0) > _LESEN_ALLE_S:
        try:
            import config
            wert = config.load().get("schriftgroesse")
        except Exception:
            wert = None
        _stand["werte"] = wert if isinstance(wert, dict) else {}
        _stand["gelesen"] = time.monotonic()
    return _stand["werte"]


def px(rolle: str) -> int:
    """Schriftgröße der Rolle in px (eigene Wahl, sonst die Vorgabe)."""
    vorgabe = ROLLEN[rolle][2]
    try:
        wert = int(_einstellung().get(rolle, vorgabe))
    except (TypeError, ValueError):
        wert = vorgabe
    return max(KLEINSTE, min(GROESSTE, wert))


def faktor() -> float:
    """Wie viel größer die ganze Oberfläche ist als die Vorgabe."""
    return px("fenster") / ROLLEN["fenster"][2]


def groesse(punkt: float) -> float:
    return punkt * faktor()


def css_px(wert: int) -> int:
    """Pixelgröße für Stylesheets (font-size: …px) nach der Größe der Oberfläche."""
    return round(wert * faktor())


def familie() -> str:
    """Schriftfamilie für alle Fenster (leer = Qt-Vorgabe). Braucht eine laufende QApplication."""
    if "familie" not in _stand:
        from PySide6.QtGui import QFontDatabase
        da = set(QFontDatabase.families())
        _stand["familie"] = next((f for f in _FAMILIEN if f in da), "")
    return _stand["familie"]


def _font(fett: bool):
    from PySide6.QtGui import QFont
    name = familie()
    f = QFont(name) if name else QFont()
    if fett:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def schrift(punkt: float = 10.5, fett: bool = False):
    """QFont in der gemeinsamen Familie, mit der Größe der Oberfläche mitgewachsen."""
    f = _font(fett)
    f.setPointSizeF(groesse(punkt))
    return f


def schrift_rolle(rolle: str, fett: bool = False):
    """QFont für eine Rolle (z. B. „ki“ für Antworten) in genau ihrer Pixelgröße."""
    f = _font(fett)
    f.setPixelSize(px(rolle))
    return f


def farbe(name: str, alpha: int | None = None):
    from PySide6.QtGui import QColor
    c = QColor(FARBE[name])
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def verlauf_farbe(r, a: str = "cyan", b: str = "lila"):
    from PySide6.QtGui import QLinearGradient
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0.0, farbe(a))
    g.setColorAt(1.0, farbe(b))
    return g
