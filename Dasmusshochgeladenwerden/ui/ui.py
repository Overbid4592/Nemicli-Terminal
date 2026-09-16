"""
ui.py - Das komplette Aussehen der CLI an einer Stelle.

Hier wohnt alles Visuelle: Farben/Themes, Logo-Banner, Panels, Spinner, Prompt.
main.py und agent.py rufen nur diese Funktionen auf und bleiben dadurch sauber.

Themes lassen sich zur Laufzeit per ui.set_theme(name) wechseln  (Befehl /theme).
"""

from __future__ import annotations

import random
import re
import time
from contextlib import contextmanager

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.style import Style
from rich.markdown import Markdown, CodeBlock
from rich.syntax import Syntax
from rich.theme import Theme
from rich.align import Align
from rich.rule import Rule
from rich.columns import Columns
from rich.table import Table
from rich.spinner import Spinner
from rich.box import ROUNDED, HEAVY
from rich.markup import escape

from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.utils import get_cwidth

# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

APP_NAME = "NemiCLI"
APP_SUBTITLE = "dein eigener Coding-Agent"

# ---------------------------------------------------------------------------
# Themes  -  jede Palette: brand, brand_dim, accent, accent_dim + Logo-Verlauf
# ---------------------------------------------------------------------------

THEMES: dict[str, dict] = {
    "cyan": {
        "label": "Cyan / Violett",
        "brand": "#8b7cff", "brand_dim": "#5b54a8",
        "accent": "#41e0d0", "accent_dim": "#2a8f86",
        "gradient": ["#41e0d0", "#5fd1e0", "#7cbef0", "#8badff", "#8b9cff", "#8b7cff"],
    },
    "matrix": {
        "label": "Grün / Matrix",
        "brand": "#22d267", "brand_dim": "#0a7a3a",
        "accent": "#7cffb2", "accent_dim": "#2a8f5a",
        "gradient": ["#7cffb2", "#5cf59a", "#39e87e", "#22d267", "#15b554", "#0a9444"],
    },
    "amber": {
        "label": "Orange / Bernstein",
        "brand": "#ff9f1c", "brand_dim": "#a8650a",
        "accent": "#ffd89b", "accent_dim": "#a87f3a",
        "gradient": ["#ffd89b", "#ffc46b", "#ffb347", "#ff9f1c", "#f08700", "#d97400"],
    },
    "cyber": {
        "label": "Pink / Cyberpunk",
        "brand": "#ff2bd6", "brand_dim": "#a01a8a",
        "accent": "#2de2e6", "accent_dim": "#1a8f92",
        "gradient": ["#2de2e6", "#6fd0ee", "#b07bf0", "#e25fe0", "#ff45c8", "#ff2bd6"],
    },
}

_active = "cyan"          # aktuelles Theme
_gradient: list[str] = THEMES[_active]["gradient"]


def _build_theme(name: str) -> Theme:
    p = THEMES[name]
    return Theme(
        {
            "brand":      f"bold {p['brand']}",
            "brand.dim":  p["brand_dim"],
            "accent":     p["accent"],
            "accent.dim": p["accent_dim"],
            "ok":         "bold #4ade80",
            "warn":       "bold #fbbf24",
            "err":        "bold #f87171",
            "muted":      "#7d8590",
            "user":       f"bold {p['accent']}",
            "assistant":  "#c8c4ff",
            "tool":       "bold #fbbf24",
        }
    )


console = Console(theme=_build_theme(_active))
_theme_pushed = False


def set_theme(name: str) -> bool:
    """Wechselt das aktive Theme. Gibt True zurück, wenn der Name existiert."""
    global _active, _gradient, PROMPT_STYLE, _theme_pushed
    if name not in THEMES:
        return False
    _active = name
    _gradient = THEMES[name]["gradient"]
    if _theme_pushed:
        console.pop_theme()
    console.push_theme(_build_theme(name))
    _theme_pushed = True
    PROMPT_STYLE = _make_prompt_style()
    return True


def current_theme() -> str:
    return _active


# ---------------------------------------------------------------------------
# Mini Block-Font  -  damit das Logo zu jedem Namen passt
# ---------------------------------------------------------------------------

_FONT: dict[str, list[str]] = {
    " ": ["  ", "  ", "  ", "  ", "  "],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "B": ["████ ", "█   █", "████ ", "█   █", "████ "],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "F": ["█████", "█    ", "████ ", "█    ", "█    "],
    "G": [" ████", "█    ", "█  ██", "█   █", " ████"],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "J": ["  ███", "   █ ", "   █ ", "█  █ ", " ██  "],
    "K": ["█   █", "█  █ ", "███  ", "█  █ ", "█   █"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "Q": [" ███ ", "█   █", "█   █", "█  ██", " ████"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "V": ["█   █", "█   █", "█   █", " █ █ ", "  █  "],
    "W": ["█   █", "█   █", "█ █ █", "██ ██", "█   █"],
    "X": ["█   █", " █ █ ", "  █  ", " █ █ ", "█   █"],
    "Y": ["█   █", " █ █ ", "  █  ", "  █  ", "  █  "],
    "Z": ["█████", "   █ ", "  █  ", " █   ", "█████"],
}


def _logo_rows(text: str) -> list[str]:
    """Die 5 Zeilen des Block-Logos. Der Abstand (2 Spalten) steht nur ZWISCHEN
    den Buchstaben – nicht hinter dem letzten. Sonst zählt rich die Nachlauf-
    Leerzeichen als Breite mit und das Logo sitzt beim Zentrieren schief."""
    glyphs = [_FONT.get(ch, _FONT[" "]) for ch in text.upper()]
    return ["  ".join(g[r] for g in glyphs) for r in range(5)]


def _logo_text() -> Text:
    """Linksbündig, ohne Umbruch (bei zu schmalem Fenster wird abgeschnitten
    statt umgebrochen – ein umgebrochenes Block-Logo ist Salat). Zentriert wird
    der Block als Ganzes über Align.center in _banner_panel: justify="center"
    würde jede Zeile einzeln zentrieren und dabei Nachlauf-Leerzeichen der
    Glyphen streichen – Zeilen mit " █ " am Ende rutschten dann um 1 Spalte."""
    return Text(justify="left", no_wrap=True, overflow="crop")


def _render_logo(text: str) -> Text:
    """Setzt den Namen aus dem Block-Font zusammen und färbt ihn mit dem Verlauf."""
    rows = _logo_rows(text)
    out = _logo_text()
    n = len(rows)
    for r, line in enumerate(rows):
        # Verlaufsfarbe je Zeile aus der Palette samplen
        idx = int(r / max(n - 1, 1) * (len(_gradient) - 1))
        out.append(line + "\n", style=_gradient[idx])
    return out


# ---------------------------------------------------------------------------
# Banner & Willkommen
# ---------------------------------------------------------------------------

def _logo_frame(text: str, phase: int) -> Text:
    """Logo, dessen Verlaufsfarben horizontal fließen (für die Schimmer-Animation)."""
    rows = _logo_rows(text)
    out = _logo_text()
    n = len(_gradient)
    for line in rows:
        for col, ch in enumerate(line):
            if ch == " ":
                out.append(" ")
            else:
                idx = int(col * 0.5 + phase) % n
                out.append(ch, style=_gradient[idx])
        out.append("\n")
    return out


_BANNER_PAD = 4          # Wunsch-Seitenabstand im Rahmen
_BANNER_PAD_MIN = 1      # wenn das Fenster dafür zu schmal ist


def _banner_pad(width: int | None) -> int:
    """Seitenabstand so, dass das Logo noch in die Innenbreite passt.
    Innenbreite = Terminal − 2 (Rahmen) − 2·Padding."""
    if width is None:
        return _BANNER_PAD
    logo_w = max(len(r) for r in _logo_rows(APP_NAME))
    return _BANNER_PAD if width - 2 - 2 * _BANNER_PAD >= logo_w else _BANNER_PAD_MIN


def _banner_panel(logo: Text, width: int | None = None) -> Panel:
    """Logo (als starrer Block via Align.center), Untertitel und Autorenzeile
    (per justify) zentrieren sich alle auf derselben Innenbreite des Panels und
    runden gleich (Überhang // 2 nach links) – eine gemeinsame Mittelachse.
    Rahmen und Padding rechnet rich dabei selbst mit ein."""
    tagline = Text(justify="center")
    tagline.append("✦ ", style="accent")
    tagline.append(APP_SUBTITLE, style="muted")
    tagline.append(" ✦", style="accent")
    credit = Text(justify="center")
    credit.append("gebaut mit Claude Opus 5", style="brand.dim")
    inner = Group(Align.center(logo), Text(""), tagline, credit)
    return Panel(inner, box=HEAVY, border_style="brand.dim",
                 padding=(1, _banner_pad(width)))


def banner_renderable(width: int | None = None) -> Panel:
    """Der ruhige Standard-Banner in der AKTUELLEN Konsolenbreite (oder `width`).
    Wird vom TUI bei jeder Breitenänderung neu abgerufen (Reflow)."""
    return _banner_panel(_render_logo(APP_NAME), width or console.width)


def banner(animate: bool = False) -> None:
    console.print()
    if TUI is not None:
        # Vollbild: als Reflow-Block in den Verlauf – das TUI rendert ihn bei
        # jeder Änderung der Terminalbreite neu, statt alte Zeilen stehen zu lassen.
        TUI.print_reflow(banner_renderable)
        return
    if animate and console.is_terminal:
        # Schimmer-Animation, die sich beim Verlassen löscht …
        with Live(console=console, refresh_per_second=24, transient=True) as live:
            for f in range(26):
                live.update(_banner_panel(_logo_frame(APP_NAME, f), console.width))
                time.sleep(0.04)
        # … danach der ruhige Standard-Banner, der stehen bleibt
        console.print(banner_renderable())
    else:
        console.print(banner_renderable())


def welcome(model: str, mode: str = "Normal", strength: str | None = None) -> None:
    sep = Text("   ·   ", style="brand.dim")
    status = Text("  ")
    status.append("● ", style="ok")
    status.append("bereit", style="muted")
    status.append_text(sep)
    status.append("⌬ ", style="muted")
    status.append(model, style="accent")
    if strength:
        status.append_text(sep)
        status.append("⚡ ", style="muted")
        status.append(strength, style="brand")
    status.append_text(sep)
    status.append("🎨 ", style="muted")
    status.append(_active, style="brand")
    console.print(status)

    hints = [
        _hint("/help", "alle Befehle"),
        _hint("/model", "Modell wechseln"),
        _hint("/resume", "Chat fortsetzen"),
        _hint("/wissen", "Gelerntes"),
        _hint("/exit", "Beenden"),
    ]
    console.print(Columns(hints, padding=(0, 3), align="left"))
    console.print(Rule(style="brand.dim"))


def _hint(cmd: str, desc: str) -> Text:
    t = Text()
    t.append(f"{cmd}", style="accent")
    t.append(f"  {desc}", style="muted")
    return t


def help_panel() -> None:
    # Die Befehle kommen aus commands.py – der EINZIGEN Liste. Vorher stand hier
    # eine zweite, handgepflegte Tabelle; die driftete zwangsläufig auseinander.
    import commands

    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Befehl", style="accent", no_wrap=True)
    table.add_column("Was es tut", style="muted")
    for usage, beschreibung in commands.help_rows():
        # escape: sonst hält Rich "[name]" für eine Style-Angabe und schluckt es
        table.add_row(escape(usage), escape(beschreibung))
    console.print(
        Panel(table, title="[brand]Hilfe[/brand]", title_align="left",
              box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )
    # Start-Tipps (früher beim Start angezeigt – jetzt hier, damit der Start sauber bleibt)
    console.print('  💡 Tippe "/" für Befehle, oder schreib einfach los.', style="muted")
    console.print("  📒 /resume <#> setzt einen früheren Chat fort.", style="muted")
    try:
        import keyvault
        if keyvault.available():
            console.print("  🔒 Deine API-Keys sind durch den Windows-Umschlag geschützt.",
                          style="muted")
    except Exception:
        pass


def _mini_bar(value: int, peak: int, width: int = 12) -> str:
    """Kleiner Balken aus █/░ für Verteilungen (Stunden, Wochentage)."""
    if peak <= 0:
        return "░" * width
    n = round(value / peak * width)
    return "█" * n + "░" * (width - n)


def stats_panel(rep: dict) -> None:
    """Nutzungs-Statistik (/statistik): was du mit NemiCLI so machst."""
    from datetime import datetime

    def _tag(iso):
        try:
            return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
        except Exception:
            return "–"

    p = THEMES[_active]
    body = Text()
    ov = Table.grid(padding=(0, 3))
    ov.add_column(style="muted", no_wrap=True); ov.add_column(style="accent", no_wrap=True)
    ov.add_column(style="muted", no_wrap=True); ov.add_column(style="accent", no_wrap=True)
    ov.add_row("Seit", _tag(rep["first_seen"]), "Sitzungen", str(rep["sessions"]))
    ov.add_row("Gesamtzeit", fmt_duration(rep["total_seconds"]), "Nachrichten", str(rep["messages"]))
    ov.add_row("Befehle", str(rep["commands_total"]), "Bilder gemalt", str(rep["images"]))
    ov.add_row("Tokens rein", fmt_tokens(rep["tokens_in"]), "Tokens raus", fmt_tokens(rep["tokens_out"]))
    ov.add_row("Kosten gesamt", fmt_cost(rep["cost"]), "Zuletzt", _tag(rep["last_seen"]))

    def _section(title: str) -> Text:
        return Text(f"\n{title}\n", style="brand")

    parts = [ov]

    if rep["top_models"]:
        t = Table(show_header=True, header_style="muted", box=None, padding=(0, 2))
        t.add_column("Modell", style="accent", no_wrap=True); t.add_column("Runden", justify="right")
        t.add_column("Tokens", justify="right"); t.add_column("Kosten", justify="right")
        for name, turns, tok, cost in rep["top_models"][:6]:
            t.add_row(escape(name), str(turns), fmt_tokens(tok), fmt_cost(cost) if cost else "–")
        parts += [_section("🤖 Modelle"), t]

    if rep["top_tools"]:
        t = Table(show_header=True, header_style="muted", box=None, padding=(0, 2))
        t.add_column("Werkzeug", style="accent", no_wrap=True); t.add_column("gesamt", justify="right")
        t.add_column("✓", justify="right", style="green"); t.add_column("✗", justify="right", style="red")
        for name, total, ok, fail in rep["top_tools"]:
            t.add_row(escape(name), str(total), str(ok), str(fail) if fail else "")
        parts += [_section("🛠 Werkzeuge"), t]

    if rep["top_commands"]:
        line = "   ".join(f"[accent]{escape(c)}[/accent] [muted]{n}[/muted]"
                          for c, n in rep["top_commands"])
        parts += [_section("⌨ Befehle"), line]

    if rep["top_personas"] and len(rep["top_personas"]) > 0:
        line = "   ".join(f"[accent]{escape(n)}[/accent] [muted]{c}[/muted]"
                          for n, c in rep["top_personas"])
        parts += [_section("🎭 Persönlichkeiten"), line]

    if rep.get("modes"):
        line = "   ".join(f"[accent]{escape(m)}[/accent] [muted]{c}[/muted]"
                          for m, c in sorted(rep["modes"].items(), key=lambda kv: kv[1], reverse=True))
        parts += [_section("🎛 Modi"), line]

    hours = rep["hours"]
    if any(hours):
        peak = max(hours)
        busiest = max(range(24), key=lambda h: hours[h])
        blocks = []
        for lo in (0, 6, 12, 18):
            seg = "".join("▁▂▃▄▅▆▇█"[min(7, round(hours[h] / peak * 7))] if peak else "▁"
                          for h in range(lo, lo + 6))
            blocks.append(f"[muted]{lo:02d}[/muted] {seg}")
        parts += [_section(f"🕒 Aktivste Zeit: {busiest:02d} Uhr"), "   ".join(blocks)]

    wd = rep["weekdays"]
    if any(wd):
        peak = max(wd)
        namen = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        line = "   ".join(f"[muted]{namen[i]}[/muted] {_mini_bar(wd[i], peak, 6)}" for i in range(7))
        parts += [_section("📅 Wochentage"), line]

    renderables = []
    for part in parts:
        renderables.append(part if not isinstance(part, str) else Text.from_markup(part))
    console.print(Panel(Group(*renderables),
                        title="[brand]📊 Deine NemiCLI-Statistik[/brand]  ·  alles lokal in learned/stats.json",
                        title_align="left", box=ROUNDED, border_style="brand.dim", padding=(1, 2)))


def kv_panel(title: str, rows: list[tuple[str, str]]) -> None:
    """Zweispaltige Übersicht (Bezeichnung · Wert), z.B. für /version."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="accent", no_wrap=True)
    table.add_column(style="muted")
    for k, v in rows:
        table.add_row(escape(str(k)), escape(str(v)))
    console.print(Panel(table, title=f"[brand]{escape(title)}[/brand]", title_align="left",
                        box=ROUNDED, border_style="brand.dim", padding=(1, 1)))


def text_panel(title: str, text: str) -> None:
    """Mehrzeiliger Rohtext (Berichte, Befehlsausgaben) im Rahmen – ohne Markup-Deutung."""
    console.print(Panel(escape(text.rstrip()) or "(leer)", title=f"[brand]{escape(title)}[/brand]",
                        title_align="left", box=ROUNDED, border_style="brand.dim",
                        padding=(0, 1)))


def model_menu(models: dict, current: str | None = None) -> None:
    """Zeigt die verfügbaren Modelle (Cloud + Lokal) für den /model-Befehl."""
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Modell", style="accent", no_wrap=True)
    table.add_column("Beschreibung", style="muted")
    for key, desc in models.items():
        marker = "  ← aktiv" if key == current else ""
        table.add_row(key, desc + marker)
    console.print(
        Panel(table, title="[brand]Modelle[/brand]  ·  /model <name>", title_align="left",
              box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


def cloud_setup_menu(rows: list) -> None:
    """Zeigt alle Cloud-Anbieter + welchen Key man wo (.env) einträgt.
    rows: Liste von (label, env_var, note, has_key)."""
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Anbieter", style="accent", no_wrap=True)
    table.add_column("Key in .env", no_wrap=True)
    table.add_column("Notiz", style="muted")
    for label, env, note, has in rows:
        status = Text("✓ gesetzt", style="ok") if has else Text(env, style="muted")
        table.add_row(label, status, note)
    console.print(
        Panel(table, title="[brand]☁ Cloud-Anbieter[/brand]  ·  Key in .env, dann /model",
              title_align="left", box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


def web_menu(entries: list) -> None:
    """Zeigt die Allowlist nach Vertrauens-Tier (nur https, lesend)."""
    _tier_label = {
        1: "🟢 Tier 1 · kanonisch", 2: "🟡 Tier 2 · user-generated",
        3: "🟠 Tier 3 · Presse", 4: "🔵 Tier 4 · Behörde/Recht",
    }
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Vertrauen", style="muted", no_wrap=True)
    table.add_column("Domain", style="accent", no_wrap=True)
    table.add_column("Notiz", style="muted")
    last = None
    for e in entries:
        t = e.get("tier")
        label = _tier_label.get(t, f"Tier {t}") if t != last else ""
        last = t
        table.add_row(label, e.get("domain", ""), e.get("note", ""))
    console.print(
        Panel(table, title="[brand]🌐 Internet-Allowlist[/brand]  ·  nur https, lesend",
              title_align="left", box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


def emoji_menu(emoticons: list, shortcodes: dict) -> None:
    """Zeigt die verfügbaren Smileys und :kürzel:."""
    smileys = Text()
    for token, emo in emoticons[:14]:
        smileys.append(f"{token} ", style="accent")
        smileys.append(f"{emo}   ", style="assistant")
    codes = Text()
    for name, emo in list(shortcodes.items())[:24]:
        codes.append(f":{name}: ", style="accent")
        codes.append(f"{emo}   ", style="assistant")
    body = Group(
        Text("Smileys (einfach so tippen):", style="brand"),
        smileys,
        Text(""),
        Text("Kürzel zwischen Doppelpunkten:", style="brand"),
        codes,
    )
    console.print(
        Panel(body, title="[brand]😊 Emojis[/brand]  ·  tippe sie einfach in den Chat",
              title_align="left", box=ROUNDED, border_style="brand.dim", padding=(1, 2))
    )


def knowledge_menu(skills: list, snippets: list) -> None:
    """Zeigt den Wissensspeicher: gelernte Skills + Code-Snippets."""
    if not skills and not snippets:
        info("Noch nichts gelernt. Sobald NemiCLI Code schreibt oder sich etwas merkt, "
             "taucht es hier auf. 🌱")
        return
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Art", style="muted", no_wrap=True)
    table.add_column("Inhalt", style="assistant")
    table.add_column("Datei", style="muted", no_wrap=True)
    for path, desc in skills:
        table.add_row("📘 Skill", desc, path.name)
    for path, title in snippets:
        table.add_row("🐍 Code", title, path.name)
    console.print(
        Panel(table, title="[brand]🧠 Wissensspeicher[/brand]  ·  gelernt aus früheren Chats",
              title_align="left", box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


def chats_menu(chats: list) -> None:
    """Zeigt die gespeicherten Chats für /resume."""
    if not chats:
        info("Noch keine gespeicherten Chats. Sobald du losschreibst, wird einer angelegt.")
        return
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("#", style="accent", no_wrap=True, justify="right")
    table.add_column("Titel", style="assistant")
    table.add_column("Modell", style="muted", no_wrap=True)
    table.add_column("Runden", style="muted", justify="right")
    table.add_column("Aktualisiert", style="muted", no_wrap=True)
    for c in chats:
        table.add_row(str(c["id"]), c["title"], c["model"], str(c["turns"]), c["updated"])
    console.print(
        Panel(table, title="[brand]Gespeicherte Chats[/brand]  ·  /resume <#>", title_align="left",
              box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


def strength_menu(strengths: dict, current: str | None = None) -> None:
    """Zeigt die Denk-Stärken für den /staerke-Befehl."""
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Stärke", style="accent", no_wrap=True)
    table.add_column("Bedeutung", style="muted")
    for key, desc in strengths.items():
        marker = "  ← aktiv" if key == current else ""
        table.add_row(key, desc + marker)
    console.print(
        Panel(table, title="[brand]Denken[/brand]  ·  /staerke <name>",
              title_align="left", box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


def theme_menu() -> None:
    """Zeigt die verfügbaren Themes für den /theme-Befehl."""
    table = Table(show_header=True, header_style="brand", box=None, padding=(0, 2))
    table.add_column("Name", style="accent", no_wrap=True)
    table.add_column("Palette", style="muted")
    table.add_column("", style="muted")
    for key, p in THEMES.items():
        swatch = Text()
        for c in p["gradient"]:
            swatch.append("██", style=c)
        marker = "  ← aktiv" if key == _active else ""
        table.add_row(key, p["label"] + marker, swatch)
    console.print(
        Panel(table, title="[brand]Themes[/brand]  ·  /theme <name>", title_align="left",
              box=ROUNDED, border_style="brand.dim", padding=(1, 1))
    )


# ---------------------------------------------------------------------------
# Nachrichten-Panels
# ---------------------------------------------------------------------------

def persona_name() -> str:
    """Name der aktiven Persönlichkeit (Lara, Nemi, …) – für Titel und Kopfzeile.
    Fällt auf „Nemi" zurück, wenn das Profil-Modul (noch) nicht erreichbar ist."""
    try:
        import persoenlichkeiten
        return persoenlichkeiten.active().name or "Nemi"
    except Exception:
        return "Nemi"



# ---------------------------------------------------------------------------
# Code im Chat: eigener Rahmen statt nackter Syntax-Block
# ---------------------------------------------------------------------------
# Rich rendert ```-Bloecke von Haus aus als farbigen Kasten ohne Kopf und ohne
# Zeilennummern. Im Gespraech geht Code dadurch im Fliesstext unter, und bei
# einem Fehler kann man nicht sagen "Zeile 12".
#
# Deshalb hier ein eigener Block: Kopfzeile mit Dateiname bzw. Sprache,
# Zeilennummern ab einer gewissen Laenge, Rahmen in der Theme-Farbe und ein
# durchsichtiger Hintergrund, damit er sich ins Terminal einfuegt statt einen
# fremdfarbigen Klotz zu setzen.
#
# Der Dateiname kommt aus der Info-Zeile des Zauns - beides geht:
#     ```python main.py
#     ```python:main.py

# Zu jedem Theme ein passender Syntax-Stil (alle in Pygments enthalten).
_CODE_THEMES = {
    "cyan": "one-dark",
    "matrix": "monokai",
    "amber": "gruvbox-dark",
    "cyber": "dracula",
}

# Ab so vielen Zeilen lohnen Zeilennummern. Bei einem Zweizeiler sind sie Krach.
_LINE_NUMBER_FROM = 4

_CODE_SPRACHEN = {
    "python": "Python", "py": "Python", "js": "JavaScript",
    "javascript": "JavaScript", "ts": "TypeScript", "typescript": "TypeScript",
    "html": "HTML", "css": "CSS", "json": "JSON", "yaml": "YAML", "yml": "YAML",
    "bash": "Bash", "sh": "Shell", "powershell": "PowerShell", "ps1": "PowerShell",
    "sql": "SQL", "md": "Markdown", "markdown": "Markdown", "text": "Text",
    "c": "C", "cpp": "C++", "cs": "C#", "java": "Java", "go": "Go",
    "rust": "Rust", "rs": "Rust", "toml": "TOML", "ini": "INI", "xml": "XML",
    "diff": "Diff", "aktion": "Aktion",
}


def code_theme_name() -> str:
    """Syntax-Stil passend zum aktuellen NemiCLI-Theme."""
    return _CODE_THEMES.get(_active, "one-dark")


def _code_kopf(info: str) -> tuple[str, str, str]:
    """Aus der Zaun-Info wird (Lexer, Titel, Sprachname).

    'python main.py' und 'python:main.py' ergeben beide main.py als Titel.
    Ohne Dateinamen ist der Titel die Sprache – dann bleibt der Untertitel
    leer, damit nicht zweimal dasselbe dasteht."""
    roh = (info or "").strip()
    if not roh:
        return "text", "Text", "Text"
    erst, _, rest = roh.partition(" ")
    if ":" in erst and not rest:                 # ```python:main.py
        erst, _, rest = erst.partition(":")
    lexer = erst.strip().lower() or "text"
    datei = rest.strip()
    sprache = _CODE_SPRACHEN.get(lexer) or lexer.capitalize() or "Text"
    return lexer, (datei or sprache), sprache


class NemiCodeBlock(CodeBlock):
    """Code-Block mit Kopfzeile, Zeilennummern und Theme-Rahmen."""

    @classmethod
    def create(cls, markdown, token):
        block = cls((token.info or "").partition(" ")[0] or "text",
                    markdown.code_theme)
        block.info = token.info or ""
        return block

    def __rich_console__(self, console, options):
        code = str(self.text).rstrip("\n")
        lexer, titel, sprache = _code_kopf(
            getattr(self, "info", "") or self.lexer_name)
        zeilen = code.count("\n") + 1
        p = THEMES[_active]
        syntax = Syntax(
            code, lexer, theme=code_theme_name(), word_wrap=True,
            line_numbers=zeilen >= _LINE_NUMBER_FROM,
            background_color="default",          # fuegt sich ins Terminal ein
            indent_guides=zeilen >= _LINE_NUMBER_FROM,
        )
        untertitel = sprache if titel != sprache else None
        yield Panel(
            syntax,
            title=f"[bold]{escape(titel)}[/bold]", title_align="left",
            subtitle=(f"[dim]{escape(untertitel)}[/dim]" if untertitel else None),
            subtitle_align="right",
            box=ROUNDED, border_style=p["accent_dim"], padding=(0, 1),
        )


class NemiMarkdown(Markdown):
    """Markdown wie bei Rich - nur mit unserem Code-Block.

    Eigene `elements`-Kopie, damit Rich selbst unangetastet bleibt: andere
    Programme im selben Prozess (und Rich-interne Markdown-Nutzung) sollen
    weiter den Standard bekommen."""

    elements = {**Markdown.elements,
                "fence": NemiCodeBlock, "code_block": NemiCodeBlock}


def assistant_panel(text: str):
    return Panel(
        NemiMarkdown(text or " ", code_theme=code_theme_name()),
        title=f"[brand]🐈 {escape(persona_name())}[/brand]", title_align="left",
        box=ROUNDED, border_style="accent", padding=(1, 2),
    )


def working_spinner(label: str = "denkt nach"):
    """Animiertes 'arbeitet gerade'-Panel, solange noch keine Antwort kommt."""
    spinner = Spinner(
        "dots",
        text=Text(f"  ✦ {APP_NAME} {label} …", style="muted"),
        style="accent",
    )
    return Panel(spinner, box=ROUNDED, border_style="brand.dim", padding=(0, 2))


# ---------------------------------------------------------------------------
# Lebendige Arbeits-Anzeige  (verspieltes Verb · Laufzeit · Token-Zähler)
# ---------------------------------------------------------------------------

# Quatsch-Verben, die während des Nachdenkens durchrotieren – wie bei Claude Code,
# aber auf NemiCLI-Deutsch. 😄
WHIMSY = [
    "grübelt", "tüftelt", "wuselt", "köchelt", "brütet", "zaubert", "werkelt",
    "bastelt", "knobelt", "sinniert", "brutzelt", "schnurpst", "klügelt",
    "fummelt", "spinnt Ideen", "sortiert Gedanken", "kramt im Hirn",
    "mischt Farben", "jongliert Bits", "faltet Gedanken", "poliert Wörter",
    "sammelt Funken", "rührt um", "lauscht den Bytes", "knetet Logik",
]


def new_verb_seed() -> int:
    """Zufälliger Startpunkt, damit jede Runde mit einem anderen Verb beginnt."""
    return random.randrange(len(WHIMSY))


def verb_at(seed: int, elapsed: float) -> str:
    """Wählt das aktuelle Verb – wechselt alle ~3 Sekunden durch."""
    return WHIMSY[(seed + int(elapsed // 3)) % len(WHIMSY)]


def fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _meter_text(verb: str, elapsed: float, tokens: int) -> Text:
    t = Text("  ")
    t.append("✦ ", style="accent")
    t.append(f"{APP_NAME} {verb} … ", style="muted")
    t.append(f"({fmt_elapsed(elapsed)} · ↓ {fmt_tokens(tokens)} Tokens)", style="brand.dim")
    t.append("   esc bricht ab", style="brand.dim")
    return t


def working_meter(verb: str, elapsed: float, tokens: int):
    """Panel mit Spinner + Verb/Zeit/Token – solange noch keine Antwort da ist."""
    spinner = Spinner("dots", text=_meter_text(verb, elapsed, tokens), style="accent")
    return Panel(spinner, box=ROUNDED, border_style="brand.dim", padding=(0, 2))


def stream_meter(verb: str, elapsed: float, tokens: int) -> Text:
    """Dezente Zeile unter der Antwort, während sie noch strömt."""
    return _meter_text(verb, elapsed, tokens)


def done_meter(elapsed: float, tokens: int, tps: float | None = None,
               in_tokens: int = 0) -> Text:
    """Kompakte Zusammenfassung nach der Antwort: Tokens · Zeit · Tempo."""
    t = Text("  ")
    t.append("⌁ ", style="accent.dim")
    parts = []
    if in_tokens:
        parts.append(f"↑ {fmt_tokens(in_tokens)}")
    parts.append(f"↓ {fmt_tokens(tokens)} Tokens")
    parts.append(fmt_elapsed(elapsed))
    if tps:
        parts.append(f"{tps:.0f} tok/s")
    t.append("  ·  ".join(parts), style="brand.dim")
    return t


THINK_TAIL = 4        # gerenderte Textzeilen; mit Rahmen höchstens 6 Zeilen
THINK_EXPANDED = 10   # erweiterte Ansicht; mit Rahmen höchstens 12 Zeilen


def _kurz(n: int) -> str:
    return f"{n/1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


class _ThinkingLines:
    """Begrenzt erst NACH dem Umbruch auf die tatsächliche Panel-Innenbreite."""

    def __init__(self, text: str, limit: int):
        self.text = text
        self.limit = limit

    def __rich_console__(self, render_console, options):
        # Frühere logische Zeilen können nicht mehr in den sichtbaren Schluss
        # passen. Eine lange letzte Zeile wird danach nach Terminalzellen gefaltet.
        tail = "\n".join(self.text.split("\n")[-self.limit:])
        # Nur die Vorschau umbrechen; der vollständige Text bleibt für F2 im RAM.
        tail = tail[-max(1024, options.max_width * self.limit * 4):]
        plain = Text(tail or "…", style="muted", tab_size=4)
        lines = plain.wrap(render_console, max(1, options.max_width),
                           overflow="fold", no_wrap=False)
        yield from lines[-self.limit:]


def thinking_panel(text: str, collapsed: bool = False,
                   seconds: float | None = None, *, done: bool = False,
                   expanded: bool = False):
    """Ruhige Vorschau echter Denk-Ausgabe; eingeklappt genau eine Textzeile.

    Der Inhalt bleibt Plain Text. Die verfügbare Breite bestimmt den Umbruch,
    statt dass ein fester Zeichenausschnitt die Panelhöhe schwanken lässt.
    """
    heading = "Nachgedacht" if done or collapsed else "Denkt nach"
    details = []
    if seconds is not None:
        details.append(fmt_elapsed(max(0, seconds)))
    details.append(f"{_kurz(len(text))} Zeichen")
    if collapsed:
        summary = Text("  ◇ ", style="accent.dim", no_wrap=True,
                       overflow="ellipsis")
        summary.append(heading, style="muted")
        summary.append("  ·  " + "  ·  ".join(details), style="brand.dim")
        if TUI is not None:
            summary.append("  ·  F2 anzeigen", style="accent.dim")
        # Group bewahrt no_wrap auch bei einem direkten console.print-Aufruf;
        # Rich würde einzelne Text-Objekte sonst mit den Print-Defaults verbinden.
        return Group(summary)

    title = Text("◇ ", style="accent")
    title.append(heading, style="muted bold")
    title.append("  ·  " + "  ·  ".join(details), style="brand.dim")
    subtitle = Text("Ausschnitt" if done else "Live-Ausschnitt", style="brand.dim")
    if TUI is not None:
        reading = done and not getattr(TUI, "_thinking_live", False)
        subtitle.append("  ·  F2 " + ("lesen" if reading else "weniger" if expanded else "mehr"),
                        style="accent.dim")
    return Panel(
        _ThinkingLines(text, THINK_EXPANDED if expanded else THINK_TAIL),
        title=title, title_align="left", subtitle=subtitle, subtitle_align="right",
        box=ROUNDED, border_style="brand.dim", padding=(0, 2),
    )


def tool_running(name: str, tool_input: dict):
    arg = ""
    if tool_input:
        key = next(iter(tool_input))
        val = str(tool_input[key])
        val = (val[:70] + "…") if len(val) > 70 else val
        arg = f"  [muted]{key}=[/muted][assistant]{val}[/assistant]"
    head = Text.from_markup(f"[tool]⚙ {name}[/tool]{arg}")
    return Panel(head, box=ROUNDED, border_style="warn", padding=(0, 2))


def action_request(desc: str, confirm: bool):
    """Panel: NemiCLI möchte eine Aktion ausführen."""
    head = "🔐 NemiCLI möchte ausführen" if confirm else "⚙ NemiCLI führt aus"
    border = "warn" if confirm else "accent.dim"
    return Panel(
        Text(desc, style="assistant"),
        title=f"[tool]{head}[/tool]", title_align="left",
        box=ROUNDED, border_style=border, padding=(0, 2),
    )


def subagent_panel(idx: int, rolle: str, task: str, result: str):
    """Panel für den Bericht eines Helfer-Agenten (Rolle im Titel)."""
    preview = result if len(result) <= 1500 else result[:1500] + "\n… (gekürzt)"
    return Panel(
        Text(preview, style="assistant"),
        title=f"[brand]🤝 Helfer {idx} · {escape(rolle[:30])}[/brand]  [muted]{escape(task[:60])}[/muted]",
        title_align="left", box=ROUNDED, border_style="accent.dim", padding=(0, 2),
    )


# ---------------------------------------------------------------------------
# Bild-Vorschau im Terminal: jede Zelle zeigt ZWEI Pixel übereinander – das
# Zeichen ▀ hat die obere Farbe als Vordergrund, die untere als Hintergrund.
# Braucht nur PIL (ist für die Bild-Pipeline ohnehin da) und ein Terminal mit
# Truecolor (Windows Terminal, cmd/PowerShell darin: ja).
# ---------------------------------------------------------------------------
PREVIEW_MAX_COLS = 60        # Breite der Vorschau in Zeichen
PREVIEW_MAX_ROWS = 24        # Höhe in Zeilen (= doppelt so viele Pixel)


def image_preview_text(path, max_cols: int = PREVIEW_MAX_COLS,
                       max_rows: int = PREVIEW_MAX_ROWS) -> Text | None:
    """Bild als rich.Text aus ▀-Zellen. None, wenn PIL fehlt oder das Bild
    nicht lesbar ist (dann gibt es einfach keine Vorschau, keinen Fehler)."""
    try:
        from PIL import Image
        img = Image.open(str(path)).convert("RGB")
    except Exception:
        return None
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    # Zellen sind etwa doppelt so hoch wie breit -> 1 Zelle = 1 Pixel breit, 2 Pixel hoch.
    cols = max(1, min(max_cols, w))
    rows = max(1, round(h / w * cols / 2))
    if rows > max_rows:
        rows = max_rows
        cols = max(1, round(w / h * rows * 2))
    try:
        small = img.resize((cols, rows * 2), Image.LANCZOS)
    except Exception:
        return None
    px = small.load()
    out = Text(no_wrap=True, overflow="crop")
    for r in range(rows):
        for c in range(cols):
            top = px[c, 2 * r]
            bot = px[c, 2 * r + 1]
            out.append("▀", style=Style(color=f"#{top[0]:02x}{top[1]:02x}{top[2]:02x}",
                                        bgcolor=f"#{bot[0]:02x}{bot[1]:02x}{bot[2]:02x}"))
        if r < rows - 1:
            out.append("\n")
    return out


def image_preview(path, title: str | None = None):
    """Panel mit der Bild-Vorschau (oder None, wenn keine möglich ist)."""
    from pathlib import Path as _P
    body = image_preview_text(path)
    if body is None:
        return None
    name = _P(str(path)).name
    return Panel(body, title=f"[muted]🖼 {escape(title or name)}[/muted]", title_align="left",
                 box=ROUNDED, border_style="brand.dim", padding=(0, 1), expand=False)


def show_image_preview(path, title: str | None = None) -> bool:
    """Vorschau in den Verlauf drucken. True, wenn etwas gezeigt wurde."""
    panel = image_preview(path, title)
    if panel is None:
        return False
    console.print(panel)
    return True


def action_result(result: str, ok: bool | None = None):
    """Panel: Ergebnis einer Aktion."""
    if ok is None:
        title = Text("◇ Ergebnis · Status ungeprüft", style="muted")
    else:
        title = Text("✓ Ergebnis" if ok else "✗ Ergebnis", style="ok" if ok else "err")
    return Panel(
        Text(str(result), style="assistant"),
        title=title, title_align="left",
        box=ROUNDED, border_style="accent.dim", padding=(0, 2),
    )


def execution_receipt(records):
    """Systembilanz der Werkzeugaufrufe; bewertet keine Modellbehauptungen."""
    if not records:
        return Text("  Keine Werkzeugaktion ausgeführt.", style="muted")
    labels = {
        "success": ("✓", "Ausführung erfolgreich", "ok"),
        "failed": ("✗", "Ausführung fehlgeschlagen", "err"),
        "unverified": ("◇", "Status ungeprüft", "muted"),
        "rejected": ("–", "Abgelehnt · nicht ausgeführt", "muted"),
        "not_run": ("–", "Nicht ausgeführt", "muted"),
    }
    body = Text()
    for index, record in enumerate(records):
        status = record.get("status", "unverified")
        if status not in labels or (record.get("tool") == "subagenten" and status == "success"):
            status = "unverified"
        icon, label, color = labels[status]
        if index:
            body.append("\n")
        body.append(f"{icon} {record.get('tool', 'Werkzeug')}", style=color)
        body.append(f"  ·  {label}", style="muted")
        if record.get("text"):
            detail = " ".join(str(record["text"]).split())
            body.append("\n  " + (detail[:157] + "…" if len(detail) > 160 else detail),
                        style="muted")
    return Panel(body, title=Text("Systembilanz · Werkzeugaktionen", style="brand"),
                 title_align="left", box=ROUNDED, border_style="brand.dim", padding=(0, 2))


def tool_result(name: str, result: str):
    preview = result if len(result) <= 800 else result[:800] + "\n… (gekürzt)"
    return Panel(
        Text(preview, style="assistant"),
        title=f"[ok]✓ {name}[/ok]", title_align="left",
        box=ROUNDED, border_style="accent.dim", padding=(0, 2),
    )


# ---------------------------------------------------------------------------
# Kleine Helfer
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    console.print(f"[muted]ℹ {msg}[/muted]")


def success(msg: str) -> None:
    console.print(f"[ok]✓ {msg}[/ok]")


def warn(msg: str) -> None:
    console.print(f"[warn]⚠ {msg}[/warn]")


def error(msg: str) -> None:
    console.print(f"[err]✗ {msg}[/err]")


def skill_activated(name: str) -> None:
    console.print(f"[brand]🧠 Skill aktiviert:[/brand] [accent]{name}[/accent]")


# ---------------------------------------------------------------------------
# TUI-Brücke  (Vollbild-Modus, ui/screen.py)
# ---------------------------------------------------------------------------
# Wenn NemiCLI im Vollbild-TUI läuft, setzt screen.py ui.TUI auf die Screen-
# Instanz und lenkt ui.console in einen Scroll-Puffer um. Damit die bestehenden
# Funktionen (Live-Anzeige, Spinner) nicht direkt auf stdout malen, gehen sie
# über diese kleine Abstraktion. Im klassischen Modus (TUI is None) bleibt alles
# wie bisher.

TUI = None   # wird von screen.py auf die Screen-Instanz gesetzt


@contextmanager
def live_view(initial):
    """Live-Anzeige für eine streamende Antwort – klassisch via rich.Live,
    im TUI über den Scroll-Puffer (kein direktes Malen auf stdout)."""
    if TUI is None:
        with Live(initial, console=console, refresh_per_second=12,
                  transient=True, vertical_overflow="crop") as live:
            yield live
    else:
        TUI.live_update(initial)

        class _TuiLive:
            def update(self, renderable):
                TUI.live_update(renderable)

        try:
            yield _TuiLive()
        finally:
            TUI.live_clear()


@contextmanager
def thinking(label: str = "denkt nach"):
    if TUI is not None:
        # Im Vollbild kein rich-Spinner (würde Steuerzeichen in den Puffer malen) –
        # stattdessen eine dezente Zeile im Verlauf.
        console.print(f"[muted]✦ {APP_NAME} {label} …[/muted]")
        yield
        return
    with console.status(f"[brand]✦ {APP_NAME}[/brand] [muted]{label} …[/muted]",
                        spinner="dots", spinner_style="accent"):
        yield


def setup_panel(gpu: dict, hint: dict, links: list[tuple[str, str]] | None = None) -> None:
    """Zeigt erkannte GPU + reine Fakten (welche Größe passt) + Links zum SELBER
    Aussuchen. NemiCLI gibt bewusst kein bestimmtes Modell vor."""
    body = Text()
    body.append("🎮 Erkannt: ", style="brand")
    body.append(f"{gpu.get('name', '?')}", style="accent")
    vram = gpu.get("vram_mb", 0)
    if vram:
        body.append(f"   ·   {vram / 1024:.0f} GB VRAM", style="muted")
    if gpu.get("vendor") == "nvidia" and gpu.get("cuda_max"):
        body.append(f"   ·   CUDA {gpu['cuda_max']}", style="muted")
    body.append("\n\n")
    body.append("📐 Was passt? ", style="brand")
    body.append(f"{hint['fits']}\n", style="muted")
    body.append("   (nur ein Anhaltspunkt – das Modell suchst du dir selbst aus)",
                style="muted")
    if links:
        body.append("\n\n🔎 Modelle stöbern:\n", style="brand")
        for label, url in links:
            body.append(f"   {label}  ", style="muted")
            body.append(f"{url}\n", style="accent")
    console.print(Panel(body, title="[brand]🚀 Lokale Einrichtung[/brand]",
                        title_align="left", box=ROUNDED, border_style="accent",
                        padding=(1, 2)))


_SCHRITT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _fortschritt_balken(i: int, n: int, cells: int = 30) -> Text:
    """Ein Füllbalken  ▓▓▓▓░░░░  42%  – Stil wie der Kontext-Balken unten."""
    frac = 0.0 if n <= 0 else max(0.0, min(1.0, i / n))
    filled = round(frac * cells)
    bar = Text("  ")
    bar.append("▓" * filled, style="accent")
    bar.append("░" * (cells - filled), style="brand.dim")
    bar.append(f"  {int(frac * 100)}%", style="muted")
    return bar


def progress_panel(title: str, msg: str):
    """Fortschritts-Panel. Steckt in der Meldung ein 'i/n' (z.B. 'Schritt 12/30'),
    zeigen wir zusätzlich einen echten Ladebalken – sonst nur den Spinner
    (z.B. beim Laden des Modells oder VAE-Dekodieren, wo es keine Schritte gibt)."""
    spinner = Spinner("dots", text=Text(f"  {msg}", style="muted"), style="accent")
    m = _SCHRITT_RE.search(msg)
    if m:
        i, n = int(m.group(1)), int(m.group(2))
        inhalt = Group(spinner, _fortschritt_balken(i, n))
    else:
        inhalt = spinner
    return Panel(inhalt, title=f"[brand]{title}[/brand]", title_align="left",
                 box=ROUNDED, border_style="brand.dim", padding=(0, 2))


def practice_header(thema: str):
    """Kopf einer Übungsrunde (Übungsmodus)."""
    return Panel(Text(thema, style="assistant"),
                 title="[brand]🎓 Übungsmodus[/brand]", title_align="left",
                 box=ROUNDED, border_style="accent", padding=(0, 2))


def practice_code(name: str, code: str):
    """Zeigt den geschriebenen Übungs-Code (gekürzt)."""
    preview = code if len(code) <= 1400 else code[:1400] + "\n… (gekürzt)"
    return Panel(Text(preview, style="muted"),
                 title=f"[muted]📝 {name}[/muted]", title_align="left",
                 box=ROUNDED, border_style="brand.dim", padding=(0, 2))


def goodbye() -> None:
    console.print()
    console.print(
        Panel(Align.center(Text("👋  Bis bald!", style="brand")),
              box=ROUNDED, border_style="brand.dim", padding=(0, 4))
    )


# ---------------------------------------------------------------------------
# Prompt (prompt_toolkit)
# ---------------------------------------------------------------------------

def _make_prompt_style() -> PTStyle:
    p = THEMES[_active]
    return PTStyle.from_dict({
        "prompt": f"{p['accent']} bold",
        "": "#c8c4ff",
        "bottom-toolbar": "noreverse bg:#15151f",
        "bottom-toolbar.text": "noreverse bg:#15151f",
    })


PROMPT_STYLE = _make_prompt_style()
PROMPT_FRAGMENTS = [("class:prompt", "  ❯ ")]


def input_divider() -> Rule:
    """Dünne Trennlinie über der Eingabezeile (rahmt die Eingabe oben ein)."""
    return Rule(style="brand.dim")


def ml_panel(stats: dict) -> None:
    """Zeigt den ML-Bericht (Ordner-Sinn) als hübsches Panel – für /ml."""
    cur = stats["current"]
    body = Text()

    # Kopf: aktueller Ordner + Einschätzung mit Balken
    body.append("📂 ", style="brand")
    body.append(f"{stats['path']}\n", style="muted")
    if cur["empty"]:
        body.append("   leer / nicht lesbar (keine Merkmale)\n", style="muted")
    else:
        conf = cur["confidence"]
        cells = 16
        filled = max(1, round(conf * cells))
        body.append("   ")
        body.append("▓" * filled, style="accent")
        body.append("░" * (cells - filled), style="brand.dim")
        body.append(f"  {round(conf * 100)} %  ", style="accent")
        body.append(f"{cur['name']}\n", style="brand")

    # Top-Einschätzungen
    if stats["top"]:
        body.append("\n  Wie sicher bin ich? (Top 4)\n", style="muted")
        for name, p in stats["top"]:
            cells = 16
            filled = max(0, round(p * cells))
            body.append(f"   {name:<30} ", style="default")
            body.append("▓" * filled, style="accent")
            body.append("░" * (cells - filled), style="brand.dim")
            body.append(f"  {round(p * 100):>3} %\n", style="muted")

    # Lern-Zustand
    body.append("\n  Mein Wissen\n", style="muted")
    body.append(f"   • {stats['classes']} Ordner-Typen, "
                f"{stats['vocab']} Merkmale im Vokabular\n", style="default")
    body.append(f"   • trainiert aus {stats['seed']} eingebauten + "
                f"{stats['learned']} selbst gelernten Beispielen\n", style="default")
    if stats["learned_by"]:
        teile = ", ".join(f"{k} ×{v}" for k, v in stats["learned_by"].items())
        body.append(f"   • selbst dazugelernt: {teile}\n", style="default")

    console.print(Panel(body, title="🧠 Ordner-Sinn (ML)", title_align="left",
                        box=ROUNDED, border_style="accent", padding=(1, 2)))


def fmt_duration(s: float) -> str:
    """Kompakte Sitzungs-Dauer: 42s · 5m · 1h 03m."""
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def fmt_cost(c: float) -> str:
    """Grobe Kosten als $-Betrag, lesbar gerundet."""
    if c <= 0:
        return "$0"
    if c < 0.01:
        return "<$0.01"
    if c < 1:
        return f"${c:.2f}"
    return f"${c:.2f}"


# Ampel-Farben für den Kontext-Balken: viel Platz → grün, fast voll → rot.
def _ctx_color(frac: float) -> str:
    if frac < 0.50:
        return "#4ade80"     # grün
    if frac < 0.80:
        return "#fbbf24"     # gelb
    if frac < 0.95:
        return "#fb923c"     # orange
    return "#f87171"         # rot


def _ctx_fragments(used: int, total: int, bg: str):
    """Farbiger Füllbalken fürs Modell-Gedächtnis: [████░░░░] 12.4k/200k."""
    total = max(1, total)
    frac = min(1.0, used / total)
    cells = 8
    filled = max(0, min(cells, round(frac * cells)))
    col = _ctx_color(frac)
    mut = f"fg:#7d8590 bg:{bg}"
    return [
        (mut, "["),
        (f"fg:{col} bg:{bg}", "█" * filled),
        (f"fg:#3a3a4a bg:{bg}", "░" * (cells - filled)),
        (mut, "] "),
        (f"fg:{col} bg:{bg}", f"{fmt_tokens(used)}/{fmt_tokens(total)}"),
    ]


def fit_fragments(fragments, width: int, ellipsis: str = "…"):
    """Kürzt eine formatierte Zeile nach Terminalspalten, auch mit breiten Zeichen."""
    fragments = list(to_formatted_text(fragments))
    width = max(0, width)
    if not width:
        return []
    if fragment_list_width(fragments) <= width:
        return fragments
    if get_cwidth(ellipsis) > width:
        ellipsis = ""
    room = width - get_cwidth(ellipsis)
    result = []
    for fragment in fragments:
        kept = ""
        for char in fragment[1]:
            cells = get_cwidth(char)
            if cells > room:
                if kept:
                    result.append((fragment[0], kept, *fragment[2:]))
                if ellipsis:
                    result.append((fragment[0], ellipsis))
                return result
            kept += char
            room -= cells
        if kept:
            result.append((fragment[0], kept, *fragment[2:]))
    return result


def _footer_line(fragments, width: int):
    """Färbt auch die freie Fläche bis zum rechten Fensterrand."""
    fragments = fit_fragments(fragments, width)
    remaining = max(0, width - fragment_list_width(fragments))
    return fragments + [("bg:#11131d", " " * remaining)]


def input_hints(width: int, busy=False, practicing=False, modal=False,
                has_thinking=False, reading_thinking=False, reviewing_action=False):
    """Eigene, bei kleinen Fenstern kürzere Tastaturhilfe unter der Eingabe."""
    p = THEMES[_active]
    key = f"fg:{p['accent']} bg:#11131d"
    muted = "fg:#7d8590 bg:#11131d"
    if reviewing_action:
        choices = [[("F8", "ausführen"), ("Esc", "ablehnen"), ("↑/↓ · Bild↑/↓", "lesen")],
                   [("F8", "ausführen"), ("Esc", "ablehnen")],
                   [("F8", "Ja"), ("Esc", "Nein")]]
    elif reading_thinking:
        choices = [[("↑/↓", "scrollen"), ("Bild↑/↓", "blättern"), ("F2 / Esc", "schließen")],
                   [("↑/↓", "scrollen"), ("F2 / Esc", "schließen")], [("Esc", "zurück")]]
    elif modal:
        choices = [[("Enter", "wählen"), ("Esc", "zurück")],
                   [("Enter", "OK"), ("Esc", "zurück")], [("Enter", "OK")]]
    else:
        actions = [("Enter", "senden"), ("Strg+J", "neue Zeile"), ("/", "Befehle"),
                   ("Shift+Tab", "Modus"), ("F12", "Chat sichern")]
        if has_thinking:
            actions.append(("F2", "Denktext"))
        stop = [("Esc", "Übung stoppen" if practicing else "abbrechen")]
        choices = [stop + actions[1:] if busy or practicing else actions,
                   stop + [("Strg+J", "Zeile")] if busy or practicing else
                   [("Enter", "senden"), ("Strg+J", "Zeile")],
                   stop if busy or practicing else [("Enter", "senden")]]
    for actions in choices:
        fragments = [(muted, "  ")]
        for i, (label, explanation) in enumerate(actions):
            if i:
                fragments.append((muted, "   ·   "))
            fragments += [(key, label), (muted, f" {explanation}")]
        if fragment_list_width(fragments) + 2 <= width:
            break
    return _footer_line(fragments, width)


_MODE_COLORS = {"chat": "#7dc4ff", "lesen": "#8fd18f", "normal": None, "auto": "#ffb454"}


def _mode_fragments(bg: str) -> list:
    """Der Arbeitsmodus – auffällig, denn er entscheidet, was ohne Rückfrage passiert.
    Auto blinkt in den letzten 30 s des Zünders."""
    import modes
    key, text = modes.status_fragment()
    p = THEMES[_active]
    color = _MODE_COLORS.get(key) or p["brand"]
    style = f"fg:{color} bg:{bg} bold"
    if key == "auto":
        rest = modes.auto_remaining() or 0
        invertiert = f"fg:{bg} bg:{color} bold"
        if rest <= 30 and int(time.monotonic() * 2) % 2 == 0:
            return [(style, f" {text} ")]             # Wechsel = Blinken kurz vor dem Zünder
        return [(invertiert, f" {text} ")]
    return [(style, text)]


def _responsive_toolbar(state: dict, width: int):
    p = THEMES[_active]
    bg = "#11131d"
    acc = f"fg:{p['accent']} bg:{bg}"
    brand = f"fg:{p['brand']} bg:{bg}"
    muted = f"fg:#7d8590 bg:{bg}"
    sep = [(muted, "  ·  " if width >= 80 else " · ")]
    model = " ".join(str(state.get("model") or "—").split())
    if width < 118 and ":" in model:
        model = model.split(":", 1)[1]
    chat = [(muted, "Chat " if width >= 65 else ""),
            (brand, f"#{state.get('chat', 0)}")]
    used, total = state.get("ctx", 0), state.get("ctx_max", 0) or 1
    if width >= 80:
        context = [(muted, "Kontext ")] + _ctx_fragments(used, total, bg)
    elif width >= 34:
        context = [(muted, "Ctx "),
                   (f"fg:{_ctx_color(used / total)} bg:{bg}",
                    f"{fmt_tokens(used)}/{fmt_tokens(total)}")]
    else:
        context = [(muted, "Ctx "),
                   (f"fg:{_ctx_color(used / total)} bg:{bg}",
                    f"{min(100, max(0, round(used / total * 100)))}%")]
    details = sep + _mode_fragments(bg) + sep + chat + sep + context
    model_room = max(1, width - 4 - fragment_list_width(details))
    model_room = min(model_room, max(12, width * 2 // 5))
    fragments = [(muted, "  ")] + fit_fragments([(acc, model)], model_room) + details
    extras = []
    if state.get("strength"):
        extras.append([(muted, "Stärke "), (brand, str(state["strength"]))])
    if state.get("cost", 0) > 0:
        extras.append([(brand, fmt_cost(state["cost"]))])
    if state.get("start"):
        extras.append([(muted, "Sitzung "),
                       (brand, fmt_duration(time.monotonic() - state["start"]))])
    for extra in extras:
        if fragment_list_width(fragments + sep + extra) + 2 <= width:
            fragments += sep + extra
    return _footer_line(fragments, width)


def bottom_toolbar(state: dict, width: int | None = None):
    """Statusleiste unter der Eingabe: Modell · Stärke · Chat · Kontext-Balken ·
    Kosten · Sitzungs-Dauer · Hinweise. Gibt prompt_toolkit-Fragmente zurück."""
    if width is not None:
        return _responsive_toolbar(state, max(0, width))
    p = THEMES[_active]
    bg = "#15151f"
    acc = f"fg:{p['accent']} bg:{bg}"
    brand = f"fg:{p['brand']} bg:{bg}"
    mut = f"fg:#7d8590 bg:{bg}"
    sep = (mut, "   ·   ")

    frags = [(mut, "  ⌬ "), (acc, state.get("model", "—"))]
    frags += [sep] + _mode_fragments(bg)
    if state.get("strength"):
        frags += [sep, (mut, "⚡ "), (brand, state["strength"])]
    frags += [sep, (mut, "📒 "), (brand, f"#{state.get('chat', 0)}")]
    # Kontext-Balken (Ampel): wie voll ist das Modell-Gedächtnis?
    frags += [sep] + _ctx_fragments(state.get("ctx", 0),
                                    state.get("ctx_max", 0) or 1, bg)
    # Kosten nur zeigen, wenn welche anfielen (Cloud); lokal bleibt es aus.
    if state.get("cost", 0) > 0:
        frags += [sep, (mut, "💲"), (brand, fmt_cost(state["cost"]).lstrip("$"))]
    # Sitzungs-Dauer
    if state.get("start"):
        frags += [sep, (mut, "⏱ "), (brand, fmt_duration(time.monotonic() - state["start"]))]
    frags += [(mut, "    "), (f"fg:{p['accent_dim']} bg:{bg}", "/help"),
              (mut, "  ·  ^J neue Zeile  ·  esc bricht ab  ")]
    return frags


# ---------------------------------------------------------------------------
# Systemcheck  (/systemcheck)
# ---------------------------------------------------------------------------

def system_panel(rep: dict, todos: list[str]) -> None:
    """Zeigt: welche Grafikkarte, was ist installiert, was fehlt noch,
    und den pip-Befehl, der zu genau diesem PC passt."""
    gpu, plan, st = rep["gpu"], rep["plan"], rep["stock"]
    body = Text()

    # --- Hardware ---------------------------------------------------------
    body.append("🎮 Grafikkarte\n", style="brand")
    body.append(f"   {gpu.get('name', '?')}", style="accent")
    if gpu.get("vram_mb"):
        body.append(f"   ·   {gpu['vram_mb'] / 1024:.0f} GB VRAM", style="muted")
    if rep.get("sm"):
        body.append(f"   ·   {rep['sm']}", style="muted")
    if gpu.get("cuda_max"):
        body.append(f"   ·   Treiber bis CUDA {gpu['cuda_max']}", style="muted")
    body.append(f"\n   {rep['hint']['fits']}\n", style="muted")

    # --- torch ------------------------------------------------------------
    t = rep.get("torch", {})
    body.append("\n🔥 Rechen-Motor (torch, fürs Bilder-Malen)\n", style="brand")
    if not t.get("da"):
        body.append("   nicht installiert\n", style="warn")
    else:
        body.append(f"   torch {t.get('version', '?')}", style="accent")
        if t.get("cuda"):
            body.append(f"  ·  CUDA {t['cuda']}", style="muted")
        body.append("\n   ")
        if t.get("gpu_nutzbar") and rep.get("torch_passt") is not False:
            body.append("✅ nutzt deine Grafikkarte\n", style="ok")
        elif t.get("gpu_nutzbar"):
            body.append(f"⚠ kennt {rep.get('sm')} nicht – rechnet falsch/langsam\n",
                        style="warn")
        else:
            body.append("⚠ sieht die Grafikkarte NICHT – rechnet auf der CPU\n",
                        style="warn")

    body.append("\n   Passender Befehl für diesen PC ", style="muted")
    body.append(f"({plan['grund']}):\n", style="muted")
    body.append(f"   {plan['cmd']}\n", style="accent")
    body.append(f"   {plan['extra']}\n", style="accent")
    if plan.get("warnung"):
        body.append(f"   ⚠ {plan['warnung']}\n", style="warn")
    if rep.get("frozen") and rep.get("extlibs"):
        body.append(f"   ℹ {rep['extlibs']}\n", style="muted")

    # --- Pakete -----------------------------------------------------------
    body.append("\n📦 Pakete\n", style="brand")
    for p in rep["pakete"]:
        zeichen, stil = ("✅", "ok") if p["da"] else (
            ("❌", "warn") if p["pflicht"] else ("–", "muted"))
        body.append(f"   {zeichen} {p['name']:<18}", style=stil)
        body.append(f"{p['version']:<15}", style="muted")
        body.append(f"{p['zweck']}\n", style="muted")
    if rep.get("transformers_ok") is False:
        body.append('   ⚠ transformers muss KLEINER als 5 sein, sonst gehen '
                    'Bild-Modelle nicht.\n', style="warn")

    # --- Modelle ----------------------------------------------------------
    body.append("\n🧠 Modelle auf diesem PC\n", style="brand")
    body.append(f"   Bild-Modelle (.safetensors):   {st['checkpoints']}\n", style="accent")
    body.append(f"      {st['ckpt_ordner']}\n", style="muted")
    body.append(f"   Ollama: {'✅ läuft' if st['ollama'] else '– nicht erreichbar'}"
                f"   ·   {st['frei_gb']} GB frei\n", style="muted")
    body.append("   Modelle bringt NemiCLI nicht mit – die suchst du dir selbst aus\n",
                style="muted")
    body.append("   (Anleitung liegt als LIES-MICH in den Ordnern).\n", style="muted")

    # --- Was fehlt --------------------------------------------------------
    if todos:
        body.append("\n📋 Das fehlt noch\n", style="brand")
        for i, td in enumerate(todos, 1):
            body.append(f"   {i}. {td}\n", style="default")
    else:
        body.append("\n✅ Alles da – nichts zu tun.\n", style="ok")

    console.print(Panel(body, title="[brand]🩺 Systemcheck[/brand]",
                        title_align="left", box=ROUNDED, border_style="accent",
                        padding=(1, 2)))


# ---------------------------------------------------------------------------
# Einrichtungs-Assistent  (/einrichten)
# ---------------------------------------------------------------------------

def wizard_panel(schritte: list[dict], titel: str = "🧰 Einrichtung") -> None:
    """Checkliste: was ist da, was fehlt."""
    body = Text()
    for s in schritte:
        if s["ok"]:
            body.append("  ✅ ", style="ok")
        elif s.get("pflicht"):
            body.append("  ❌ ", style="warn")
        else:
            body.append("  ⬜ ", style="muted")
        body.append(f"{s['titel']}\n", style="brand" if not s["ok"] else "default")
        body.append(f"      {s['info']}\n", style="muted")
    console.print(Panel(body, title=f"[brand]{titel}[/brand]", title_align="left",
                        box=ROUNDED, border_style="accent", padding=(1, 2)))


def wizard_models_panel(hinweise: list[tuple[str, str]], ordner: dict) -> None:
    """Wo bekommt man Modelle her – und wohin damit."""
    body = Text()
    body.append("Modelle bringt NemiCLI nicht mit – die suchst du dir selbst aus.\n\n",
                style="muted")
    for label, wo in hinweise:
        body.append(f"  • {label}\n", style="brand")
        body.append(f"    {wo}\n", style="accent")
    body.append("\n  Dateien gehören hierhin:\n", style="brand")
    body.append(f"    .safetensors  →  {ordner['checkpoints']}\n", style="muted")
    console.print(Panel(body, title="[brand]📦 Modelle besorgen[/brand]",
                        title_align="left", box=ROUNDED, border_style="accent",
                        padding=(1, 2)))
