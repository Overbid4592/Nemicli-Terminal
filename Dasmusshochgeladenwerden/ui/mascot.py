"""
mascot.py - Nemis kleines Maskottchen, das ab und zu was bubbelt. 💜

Ein kleiner Begleiter, der beim Start grüßt und zwischendurch mal einen Tipp,
einen Spruch oder einfach was Liebes von sich gibt. Rein zur Freude.
"""

from __future__ import annotations

import random
import time

from prompt_toolkit.formatted_text import FormattedText

import foldersense
import ui

# Ein paar Gesichter (werden zufällig gewählt)
FACES = ["(^‿^)", "(=^.^=)", "(•‿•)", "(o‿o)", "(¬‿¬)", "(^▽^)", "(｡◕‿◕｡)"]

# Begrüßungen beim Start
GREETINGS = [
    "Na, da bist du ja! Schön. 💜",
    "Hey! Was basteln wir heute? 😊",
    "Bereit, wenn du es bist! ✨",
    "Dein Terminal-Kumpel ist wach. ☕→⚡",
    "Lass uns was Cooles machen! 🚀",
]

# Tipps (helfen nebenbei)
TIPS = [
    "💡 Mit /resume holst du alte Chats zurück.",
    "💡 /staerke stark lässt mich gründlicher nachdenken.",
    "💡 /theme cyber – probier mal Pink! 💖",
    "💡 Tippe nur /  und ich zeig dir alle Befehle.",
    "💡 /wissen zeigt, was ich schon gelernt hab.",
    "💡 /web zeigt, welche Seiten ich lesen darf.",
    "💡 Schreib =) oder <3 – ich mach echte Emojis draus.",
    "💡 /model wechselt zwischen lokal (GPU) und Cloud.",
]

# Sprüche / Persönlichkeit
QUIPS = [
    "Psst… ich lerne mit jedem Chat dazu. 🌱",
    "Alles läuft lokal bei dir – niemand schaut zu. 😎",
    "Ich bin gern deine Süße im Terminal. 💜",
    "Brauchst du was? Ich bin da! ✨",
    "Kaffee? ☕ Ich nehm Strom. ⚡",
    "Frag mich ruhig was Schwieriges. 🧠",
    "Du baust, ich helfe – gutes Team. 🤝",
]


def _say(text: str) -> None:
    face = random.choice(FACES)
    ui.console.print(f"  [accent]{face}[/accent] [brand]💬[/brand] [muted]{text}[/muted]")


def greet() -> None:
    """Begrüßung beim Start."""
    _say(random.choice(GREETINGS))


def bubble() -> None:
    """Ein zufälliger Tipp oder Spruch (auf Wunsch / per /nemi)."""
    _say(random.choice(TIPS + QUIPS))


def maybe(chance: float = 0.13) -> None:
    """Bubbelt mit kleiner Wahrscheinlichkeit etwas (nach einer Chat-Runde)."""
    if random.random() < chance:
        bubble()


# ---------------------------------------------------------------------------
# Animiertes Maskottchen unten rechts (während du tippst)
# ---------------------------------------------------------------------------

# Gleich breite (5-Zeichen) Frames für eine ruckelfreie Animation:
# meistens fröhlich, ab und zu blinzeln/zwinkern/umschauen.
_ANIM = [
    "(^_^)", "(^_^)", "(^_^)", "(^_^)", "(^_^)", "(^_^)",
    "(-_-)",                      # blinzeln
    "(^_^)", "(^_^)", "(^_^)", "(^_^)",
    "(~_^)",                      # zwinkern
    "(^_^)", "(^_^)", "(o_o)",    # kurz umschauen
    "(^_^)", "(^_^)", "(^_^)",
]


def rprompt() -> FormattedText:
    """Wird von prompt_toolkit rechts neben der Eingabe gezeigt – animiert."""
    frame = _ANIM[int(time.time() * 3) % len(_ANIM)]   # ~3 Frames/Sekunde
    accent = ui.THEMES[ui.current_theme()]["accent"]
    return FormattedText([(f"fg:{accent} bold", f" {frame} ")])


# ---------------------------------------------------------------------------
# ML-Balken links neben der Eingabe (zeigt Nemis Ordner-Sinn live)
# ---------------------------------------------------------------------------

_BAR_CELLS = 10   # Breite des Balkens


# Gelb, fett, mit vollem Pfad: solange ein Workspace festgenagelt ist, soll auf
# einen Blick klar sein, dass gerade NUR in diesem Ordner gearbeitet wird.
WORKSPACE_GELB = "#ffcc00"


def workspace_badge(width: int | None = None) -> FormattedText:
    r"""'workspace C:\...\HTMLTEST' in Gelb-Fett - leer, wenn keiner aktiv ist.

    Bei wenig Platz schrumpft der Pfad von links (die letzten Ordner sagen mehr
    als das Laufwerk), der Ordnername bleibt immer lesbar."""
    import workspace
    ws = workspace.pfad()
    if ws is None:
        return FormattedText([])
    stil = f"fg:{WORKSPACE_GELB} bold"
    pfad = str(ws)
    if width is not None:
        platz = max(12, width - len("workspace  "))
        if len(pfad) > platz:
            teile = ws.parts
            kurz = ws.name
            for i in range(len(teile) - 2, 0, -1):
                kandidat = "…\\" + "\\".join(teile[i:])
                if len(kandidat) > platz:
                    break
                kurz = kandidat
            pfad = kurz
    return FormattedText([(stil, f"workspace {pfad}")])


def folder_badge(compact: bool = False) -> FormattedText:
    """Kompakte Ordner-Erkennung für den Rahmen der Vollbild-Eingabe."""
    theme = ui.THEMES[ui.current_theme()]
    accent = f"fg:{theme['accent']}"
    dim = f"fg:{theme['accent_dim']}"
    muted = "fg:#9298ac"
    try:
        result = foldersense.current()
    except Exception:
        result = None
    if not result or result.get("empty"):
        return FormattedText([(muted, "Ordner · —")])
    confidence = max(0.0, min(1.0, result["confidence"]))
    name = (foldersense.LABEL_SHORT.get(result["label"], "?") if compact
            else result.get("name", result["label"]))
    cells = 5
    filled = round(confidence * cells)
    return FormattedText([
        (muted, "Ordner · "), (accent, str(name)), (muted, "  "),
        (accent, "▓" * filled), (dim, "░" * (cells - filled)),
        (muted, f" {round(confidence * 100)}% "),
    ])


def lprompt() -> FormattedText:
    """Linker Prompt: kleiner ML-Balken (Ordner-Typ + Sicherheit) + das ❯.

    Das Gegenstück zum Maskottchen rechts – so SIEHT man Nemis ML arbeiten.
    """
    theme = ui.THEMES[ui.current_theme()]
    accent = theme["accent"]
    dim = theme.get("accent_dim", "#555")
    try:
        r = foldersense.current()
    except Exception:
        r = None

    frags: list[tuple[str, str]] = [(f"fg:{accent}", " 🧠 ")]
    if not r or r.get("empty"):
        frags.append((f"fg:{dim}", "—— "))
    else:
        conf = r["confidence"]
        short = foldersense.LABEL_SHORT.get(r["label"], "?")
        filled = max(1, round(conf * _BAR_CELLS))
        bar_full = "▓" * filled
        bar_empty = "░" * (_BAR_CELLS - filled)
        frags += [
            (f"fg:{accent}", f"{short:<4}"),
            (f"fg:{accent} bold", bar_full),
            (f"fg:{dim}", bar_empty),
            (f"fg:{accent}", f" {round(conf * 100):>3}% "),
        ]
    frags.append((f"fg:{accent} bold", "❯ "))
    return FormattedText(frags)
