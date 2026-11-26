"""konsolenzeichen.py – zusammengesetzte Emojis vor der Konsole glätten, damit nichts verrutscht.

Windows' Pseudokonsole (ConPTY – darunter das eigene Terminal-Fenster und Windows Terminal) zählt manche
Zeichenfolgen breiter als rich/Textual: Flaggen (zwei Regional-Indikatoren), Folgen mit Verbinder (👩\u200d💻), Hauttöne
(👍🏻), Ziffern-Tasten (1️\u20e3) und Tag-Folgen. Füllt so eine Zeile die Breite aus, bricht sie in der Konsole um – in
der letzten Zeile rollt dann die ganze Ansicht eine Zeile hoch, und die Leisten stehen doppelt.

glaetten() ersetzt genau diese Gruppen breitengleich (nach rich gezählt): Flagge → Ländercode („DE“), Verbinder-Folge
→ ihr erstes Zeichen, Hautton und Tasten-Kappe weg; fehlt Breite, füllen Leerzeichen auf. Alles andere bleibt Byte
für Byte gleich.
"""
from __future__ import annotations

import re

_HEIKEL = re.compile(r"[\U0001F1E6-\U0001F1FF\u200d\U0001F3FB-\U0001F3FF\u20e3\U000E0020-\U000E007F]")
_FLAGGE = re.compile(r"[\U0001F1E6-\U0001F1FF]")
_WEG = re.compile(r"[\U0001F3FB-\U0001F3FF\u20e3\U000E0020-\U000E007F]")


def _buchstabe(m: re.Match) -> str:
    return chr(ord(m.group(0)) - 0x1F1E6 + ord("A"))


def _ersatz(gruppe: str, breite: int) -> str:
    from rich.cells import cell_len
    if "\u20e3" in gruppe:                                    # Tasten-Kappe: nur das Zeichen auf der Taste
        neu = gruppe[0]
    else:
        neu = _WEG.sub("", gruppe.split("\u200d")[0])
        neu = _FLAGGE.sub(_buchstabe, neu)
    neu = neu or "?"
    w = cell_len(neu)
    if w > breite:
        neu = "?" * breite
    elif w < breite:
        neu += " " * (breite - w)
    return neu


def glaetten(text: str) -> str:
    """Text für die Konsole – nur heikle Gruppen geändert, breitengleich."""
    if not text or not _HEIKEL.search(text):
        return text
    try:
        from rich.cells import split_graphemes
        spannen, _ = split_graphemes(text)
    except Exception:
        return text
    teile: list[str] = []
    ende = 0
    for a, e, w in spannen:
        gruppe = text[a:e]
        if _HEIKEL.search(gruppe):
            teile.append(text[ende:a])
            teile.append(_ersatz(gruppe, w))
            ende = e
    teile.append(text[ende:])
    return "".join(teile)


def treiber_glaetten() -> None:
    """Die Ausgabe des Textual-Treibers unter Windows über glaetten() leiten (einmal je Prozess)."""
    try:
        from textual.drivers import windows_driver as W
    except Exception:
        return
    alt = W.WindowsDriver.write
    if getattr(alt, "_glatt", False):
        return

    def write(self, data: str) -> None:
        alt(self, glaetten(data))

    write._glatt = True
    W.WindowsDriver.write = write
