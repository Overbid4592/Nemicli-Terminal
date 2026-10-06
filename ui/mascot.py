"""
mascot.py - das kleine Maskottchen, das ab und zu was bubbelt. 💜

Beim Start ein Gruß, zwischendurch ein Tipp oder ein Spruch. Seit 19.09.2026
spricht hier die AKTIVE PERSÖNLICHKEIT, nicht mehr ein fester Spruchzettel
(„Dein Terminal-Kumpel ist wach“ passte zu keiner von ihnen). Jede
Persönlichkeit hat ihre eigene Datei `Persoenlichkeiten/<key>.sprueche.md`
mit zwei Abschnitten (## Begrüßung / ## Sprüche, eine Zeile je Satz,
{nutzer} und {name} werden ersetzt). Fehlt die Datei, schreibt sie die
Persönlichkeit beim ersten Start mit einem Modell selbst (sprueche_nachholen)
– und darf sie jederzeit mit datei_schreiben ändern. Die Tipps (TIPS) bleiben
neutral, das sind Bedienhinweise.
"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

from prompt_toolkit.formatted_text import FormattedText

import foldersense
import ui

# Ein paar Gesichter (werden zufällig gewählt)
FACES = ["(^‿^)", "(=^.^=)", "(•‿•)", "(o‿o)", "(¬‿¬)", "(^▽^)", "(｡◕‿◕｡)"]

# Solange eine Persönlichkeit noch keine eigenen Sprüche hat: ein neutraler Gruß
# mit ihrem Namen – kein Spruchzettel, der zu niemandem passt.
NEUTRAL_GRUSS = "{name} hier. Hallo, {nutzer}."

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


# ---------------------------------------------------------------------------
# Sprüche der Persönlichkeit
# ---------------------------------------------------------------------------

def _ps():
    import persoenlichkeiten as PS
    return PS


def sprueche_datei(p=None) -> Path:
    PS = _ps()
    p = p or PS.active()
    return PS.DIR / f"{p.key}.sprueche.md"


def _lesen(p=None) -> dict[str, list[str]]:
    """{'gruss': [...], 'spruch': [...]} aus der Datei – leer, wenn es sie nicht gibt."""
    out: dict[str, list[str]] = {"gruss": [], "spruch": []}
    try:
        text = sprueche_datei(p).read_text(encoding="utf-8")
    except OSError:
        return out
    ziel = "gruss"
    for z in text.splitlines():
        z = z.strip()
        if z.startswith("#"):
            kopf = z.lstrip("#").strip().lower()
            if kopf.startswith(("spr", "zwischen")):
                ziel = "spruch"
            elif kopf.startswith(("begr", "gru", "start")):
                ziel = "gruss"
            continue
        z = z.lstrip("-•* ").strip()
        if z:
            out[ziel].append(z)
    return out


def _fuellen(text: str) -> str:
    PS = _ps()
    return text.replace("{nutzer}", PS.nutzername() or "du").replace("{name}", PS.active().name)


def _say(text: str) -> None:
    face = random.choice(FACES)
    name = _ps().active().name
    ui.console.print(f"  [accent]{face}[/accent] [brand]💬 {name}[/brand] [muted]{text}[/muted]")


def greet() -> None:
    """Begrüßung beim Start – in der Stimme der aktiven Persönlichkeit."""
    zeilen = _lesen()["gruss"]
    _say(_fuellen(random.choice(zeilen) if zeilen else NEUTRAL_GRUSS))


def bubble() -> None:
    """Ein zufälliger Tipp oder Spruch (auf Wunsch / per /nemi)."""
    zeilen = _lesen()["spruch"]
    _say(_fuellen(random.choice(TIPS + zeilen)))


def hat_sprueche(p=None) -> bool:
    d = _lesen(p)
    return bool(d["gruss"]) and bool(d["spruch"])


_AUFTRAG = (
    "Schreib deine eigenen Sprüche fürs Terminal – in DEINER Stimme, so wie du bist. "
    "Zwei Abschnitte, genau in diesem Format, eine Zeile je Satz, keine Nummerierung, "
    "keine Anführungszeichen, kein Text davor oder danach:\n\n"
    "## Begrüßung\n<8 kurze Sätze, mit denen du {nutzer} beim Start begrüßt – jede unter 70 Zeichen>\n\n"
    "## Sprüche\n<10 kurze Sätze für zwischendurch: was Nettes, ein Augenzwinkern, ein Angebot zu helfen – "
    "jede unter 80 Zeichen>\n\n"
    "Du darfst {nutzer} und {name} als Platzhalter benutzen (werden ersetzt). Emojis sparsam. "
    "Nichts Generisches wie „Terminal-Kumpel“ – es soll klingen wie du."
)


async def sprueche_nachholen(backend, p=None) -> bool:
    """Fehlt der aktiven Persönlichkeit die Spruch-Datei, schreibt sie sie jetzt selbst
    (ein kurzer Modellaufruf, ohne Verlauf). True = Datei liegt danach da."""
    PS = _ps()
    p = p or PS.active()
    if backend is None or hat_sprueche(p):
        return hat_sprueche(p)
    try:
        system = PS.render_text(p) + "\n\nDu antwortest nur mit dem verlangten Text."
        antwort = await backend.ask_once(_AUFTRAG, system)
    except Exception:
        return False
    gruss, spruch = _parse_antwort(antwort)
    if len(gruss) < 3 or len(spruch) < 3:
        return False
    kopf = (f"# Sprüche von {p.name} fürs Terminal – eine Zeile je Satz.\n"
            "# {nutzer} = der Nutzer, {name} = du. Du darfst diese Datei jederzeit selbst neu schreiben.\n\n")
    try:
        sprueche_datei(p).parent.mkdir(parents=True, exist_ok=True)
        sprueche_datei(p).write_text(kopf + "## Begrüßung\n" + "\n".join(gruss) +
                                     "\n\n## Sprüche\n" + "\n".join(spruch) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _parse_antwort(text: str) -> tuple[list[str], list[str]]:
    gruss, spruch, ziel = [], [], None
    for z in (text or "").splitlines():
        z = z.strip().strip("`")
        if not z:
            continue
        if z.startswith("#"):
            kopf = z.lstrip("#").strip().lower()
            ziel = "spruch" if kopf.startswith(("spr", "zwischen")) else "gruss"
            continue
        z = re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", z).strip().strip('"„“')
        if not z or ziel is None:
            continue
        (spruch if ziel == "spruch" else gruss).append(z[:120])
    return gruss, spruch


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
