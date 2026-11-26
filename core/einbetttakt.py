"""
einbetttakt.py - Grafikspeicher für den Bibliothekar (Einlese-Speicher: einlesen.py).

VRAM-Anteil (Config `embedding_vram_anteil`, Prozent vom gesamten Grafikspeicher): Obergrenze für den
Bibliothekar-Prozess, der nur auf der GPU rechnet.
"""
from __future__ import annotations

VRAM_ANTEIL_VORGABE = 25


def _cfg() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def vram_anteil() -> int:
    """Prozent vom gesamten VRAM, 5–90."""
    try:
        n = int(_cfg().get("embedding_vram_anteil", VRAM_ANTEIL_VORGABE))
    except (TypeError, ValueError):
        n = VRAM_ANTEIL_VORGABE
    return max(5, min(90, n))
