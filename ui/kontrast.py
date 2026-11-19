"""kontrast.py – Kontrast nach WCAG 2 und lesbare Textfarben für alle Themes.

Text muss mindestens 4,5 : 1 zum Hintergrund haben (WCAG AA). Das eigene Terminal-Fenster lässt den
Hintergrund durchscheinen (Vorgabe 82 % deckend, `terminal_hintergrund`). Gerechnet wird deshalb gegen
einen Bezug: die Grundfarbe mit 18 % Weiß gemischt – so hell wird es höchstens über einem weißen Desktop.
Gedämpfte Töne bleiben für Rahmen und Flächen; Text bekommt mit `lesbar()` die nächste hellere Stufe
derselben Farbe, die den Bezug schafft. So bleibt die Rangfolge (Text > gedämpfter Text > Rahmen).
"""
from __future__ import annotations

import re

MIN_TEXT = 4.5                       # WCAG AA für normalen Text
DURCHSICHT = 0.18                    # Anteil des Desktops hinter dem Fenster (1 − Vorgabe 0,82)
_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")


def rgb(h: str) -> tuple[int, int, int]:
    if not _HEX.match(str(h or "")):
        raise ValueError(f"Farbe {h!r} ist kein Hex-Wert wie #8b7cff.")
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def helligkeit(h: str) -> float:
    """Relative Leuchtdichte nach WCAG 2."""
    def kanal(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (kanal(c) for c in rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def kontrast(a: str, b: str) -> float:
    la, lb = sorted((helligkeit(a), helligkeit(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mischen(a: str, b: str, anteil: float) -> str:
    """`anteil` 0 = a, 1 = b."""
    anteil = min(1.0, max(0.0, anteil))
    return "#" + "".join(f"{round(x + (y - x) * anteil):02x}" for x, y in zip(rgb(a), rgb(b)))


def bezug(grund: str) -> str:
    """Hellster Hintergrund, auf dem Text im durchscheinenden Fenster noch stehen kann."""
    return mischen(grund, "#ffffff", DURCHSICHT)


def unter_weiss(farbe: str, ziel: float = MIN_TEXT) -> str:
    """Fläche für weiße Schrift (Knöpfe): `farbe` oder die erste dunklere Stufe mit dem Zielkontrast."""
    for schritt in range(101):
        kandidat = mischen(farbe, "#000000", schritt / 100)
        if kontrast("#ffffff", kandidat) >= ziel:
            return kandidat
    return "#000000"


def lesbar(farbe: str, grund: str, ziel: float = MIN_TEXT) -> str:
    """`farbe`, wenn sie auf dem Bezug von `grund` den Zielkontrast schafft – sonst die erste hellere Stufe
    zu Weiß hin, die ihn schafft. Der Farbton bleibt erkennbar."""
    hinten = bezug(grund)
    for schritt in range(101):
        kandidat = mischen(farbe, "#ffffff", schritt / 100)
        if kontrast(kandidat, hinten) >= ziel:
            return kandidat
    return "#ffffff"
