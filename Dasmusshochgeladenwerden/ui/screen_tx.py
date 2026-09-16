"""
screen_tx.py - Das Vollbild-TUI von NemiCLI auf Textual (Nachfolger von screen.py).

Gleiche Schnittstelle wie der alte Screen (install/run/select/text_input/
review_action/live_update/print_reflow/set_helfer/…), damit main.py, confirm.py
und ui.py nichts davon merken. Umschalten auf den alten Screen: NEMICLI_TUI=alt.

Was anders ist – und warum:
- Der Verlauf besteht aus echten Widgets (ein Static pro gedrucktem Block) statt
  aus vorgerenderten ANSI-Zeilen. Textual rendert jeden Block in der AKTUELLEN
  Breite neu, sobald sich das Fenster ändert – Panels, Banner, Markdown, alles.
  Kein Reflow-Sonderweg, keine Geister-Zeichen, kein Zellen-Diff von Hand.
- ui.console bleibt der Weg, wie alle Module ausgeben. Er ist hier eine
  Console, deren print() die Renderables abfängt und in den Verlauf hängt,
  statt sie zu Text zu rendern.
- Menüs, Texteingabe, Aktionsprüfung (F8) und Denktext (F2) sind ModalScreens.
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
import time
from typing import Callable

from rich.align import Align
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.style import Style
from rich.segment import Segment
from rich.styled import Styled
from rich.text import Text

from textual import events
from textual.app import App, ComposeResult
from textual.strip import Strip
from textual.visual import RichVisual
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent

import ui
import mascot
from commands import SlashCompleter
import models as M
import chatstore
from screen import terminal_safe, maus_tracking_aus, _checkpoint_menu, _persona_menu

_COMPOSER_BG = "#11131d"
_TOOLBAR_BG = "#15151f"
_MIN_INPUT_ROWS = 3
_MAX_INPUT_ROWS = 8
_MAX_BLOCKS = 600            # Verlauf im Speicher kappen (Widgets)
_MAX_COMPLETIONS = 8


# ---------------------------------------------------------------------------
# prompt_toolkit-Fragmente -> rich.Text  (Toolbar, Hinweise, Maskottchen bleiben
# unverändert; sie liefern (style, text)-Tupel, die wir hier übersetzen)
# ---------------------------------------------------------------------------
_STYLE_CACHE: dict[str, Style] = {}


def pt_style(spec: str) -> Style:
    """'fg:#41e0d0 bg:#11131d bold' -> rich.Style. Unbekannte Tokens werden ignoriert."""
    if spec in _STYLE_CACHE:
        return _STYLE_CACHE[spec]
    kw: dict = {}
    for tok in (spec or "").split():
        low = tok.lower()
        if low.startswith("fg:"):
            kw["color"] = tok[3:]
        elif low.startswith("bg:"):
            kw["bgcolor"] = tok[3:]
        elif low in ("bold", "italic", "underline", "reverse", "blink", "strike", "dim"):
            kw[low] = True
        elif low in ("nobold", "noitalic", "nounderline", "noreverse", "noblink"):
            kw[low[2:]] = False
        elif low.startswith("#") or low in ("ansiwhite", "white", "black"):
            kw["color"] = tok
        # class:… und ähnliches: kein Gegenstück in rich, wird übersprungen
    try:
        st = Style(**kw)
    except Exception:
        st = Style()
    _STYLE_CACHE[spec] = st
    return st


def fragments_to_text(fragments) -> Text:
    """Liste von (style, text[, handler]) -> rich.Text (Emoji-sicher)."""
    out = Text(no_wrap=True, overflow="crop")
    for frag in fragments or []:
        style, text = frag[0], frag[1]
        if text:
            out.append(terminal_safe(text), style=pt_style(style))
    return out


# ---------------------------------------------------------------------------
# Renderables Emoji-sicher machen (siehe terminal_safe in screen.py)
# ---------------------------------------------------------------------------
def sanitize(renderable):
    """Bringt die Texte in einem Renderable auf die Emoji-Form, deren Breite
    Terminal und rich gleich sehen. Deckt die Typen ab, die NemiCLI druckt."""
    try:
        if isinstance(renderable, str):
            return terminal_safe(renderable)
        if isinstance(renderable, Text):
            plain = terminal_safe(renderable.plain)
            if plain != renderable.plain:
                renderable.plain = plain
            return renderable
        if isinstance(renderable, Markdown):
            src = terminal_safe(renderable.markup)
            if src != renderable.markup:
                # type(...) statt Markdown: sonst verliert ein NemiMarkdown beim
                # Entschaerfen der Emoji seine schoenen Code-Bloecke.
                return type(renderable)(src, code_theme=renderable.code_theme,
                                justify=renderable.justify,
                                style=renderable.style, hyperlinks=renderable.hyperlinks,
                                inline_code_lexer=renderable.inline_code_lexer,
                                inline_code_theme=renderable.inline_code_theme)
            return renderable
        if isinstance(renderable, Panel):
            renderable.renderable = sanitize(renderable.renderable)
            if isinstance(renderable.title, str):
                renderable.title = terminal_safe(renderable.title)
            elif isinstance(renderable.title, Text):
                sanitize(renderable.title)
            return renderable
        if isinstance(renderable, Group):
            return Group(*[sanitize(r) for r in renderable.renderables], fit=renderable.fit)
        if isinstance(renderable, (Align, Padding, Styled)):
            renderable.renderable = sanitize(renderable.renderable)
            return renderable
    except Exception:
        pass
    return renderable


class _ReflowRenderable:
    """Hülle für print_reflow: ruft die Fabrik bei JEDEM Rendern mit der dann
    gültigen Breite auf (Banner-Padding richtet sich nach der Breite)."""

    def __init__(self, make: Callable):
        self.make = make

    def __rich_console__(self, console, options):
        try:
            r = self.make(width=options.max_width)
        except TypeError:
            r = self.make()
        yield sanitize(r)


# ---------------------------------------------------------------------------
# Verlaufs-Block mit Text-Markierung: Textual kann Text nur in reinen
# Text-Widgets markieren/kopieren. Unsere Blöcke sind rich-Renderables
# (Panels, Markdown) – darum hier: Auswahl selbst einfärben und den Text
# aus einem Klartext-Render der gleichen Breite herausziehen.
# ---------------------------------------------------------------------------
class _SelectableRich(RichVisual):
    """RichVisual, das die Maus-Markierung (Screen-Auswahl) mit einfärbt."""

    def render_strips(self, width, height, style, options):
        strips = super().render_strips(width, height, style, options)
        # Jedes Segment bekommt seine Zeichenposition als Meta-Info ("offset").
        # Daran erkennt Textual beim Klicken/Ziehen, WO im Text die Maus ist –
        # ohne das wird immer der ganze Block markiert.
        annotated = []
        for y, strip in enumerate(strips):
            x = 0
            segs = []
            for seg in strip:
                meta = Style.from_meta({"offset": (x, y)})
                segs.append(Segment(seg.text, (seg.style + meta) if seg.style else meta,
                                    seg.control))
                x += len(seg.text)
            annotated.append(Strip(segs, strip.cell_length))
        strips = annotated
        sel, sel_style = options.selection, options.selection_style
        if sel is None or sel_style is None:
            return strips
        sel_style = getattr(sel_style, "rich_style", sel_style)   # textual.Style -> rich.Style
        out = []
        for y, strip in enumerate(strips):
            span = sel.get_span(y)
            if span is None:
                out.append(strip)
                continue
            x0, x1 = span
            if x1 == -1:
                x1 = strip.cell_length
            x0 = max(0, min(x0, strip.cell_length))
            x1 = max(x0, min(x1, strip.cell_length))
            out.append(Strip.join([strip.crop(0, x0), strip.crop(x0, x1).apply_style(sel_style),
                                   strip.crop(x1)]))
        return out


class _Block(Static):
    """Ein gedruckter Block im Verlauf – markier- und kopierbar."""

    def __init__(self, renderable, **kw):
        super().__init__(renderable, **kw)
        self.nemi_renderable = renderable
        self._sel_visual = None

    def render(self):
        if self._sel_visual is None:
            self._sel_visual = _SelectableRich(self, self.nemi_renderable)
        return self._sel_visual

    def _plain_lines(self) -> str:
        """Klartext-Render in der aktuellen Breite (so, wie es auf dem Schirm liegt)."""
        w = max(1, self.content_size.width)
        sio = io.StringIO()
        con = Console(file=sio, width=w, color_system=None, force_terminal=False,
                      legacy_windows=False, theme=ui._build_theme(ui.current_theme()),
                      soft_wrap=False, highlight=False)
        con.print(self.nemi_renderable)
        return sio.getvalue()

    def get_selection(self, selection):
        try:
            raw = selection.extract(self._plain_lines())
        except Exception:
            return None
        # Kopiert wird nur der geschriebene Text: Panel-Ränder und Nachlauf-
        # Leerzeichen weg, Bild-Vorschauen (Zeilen aus ▀) ganz weg.
        lines = []
        for line in raw.split("\n"):
            line = line.rstrip()
            if line.endswith("│"):
                line = line[:-1].rstrip()
            if line.startswith("│"):
                line = line[1:]
                line = line[2:] if line.startswith("  ") else line.lstrip(" ")
            if line and sum(ch == "▀" for ch in line) >= 0.8 * len(line):
                continue
            lines.append(line)
        return "\n".join(lines).strip("\n"), "\n"


def _windows_clipboard(text: str) -> None:
    """Text in die Windows-Zwischenablage (PowerShell Set-Clipboard, UTF-8).
    Läuft im Hintergrund-Thread, damit die Oberfläche nicht ruckelt; Fehler
    sind egal – dann bleibt der OSC-52-Weg des Terminals."""
    import subprocess
    import sys

    if not sys.platform.startswith("win"):
        return

    def _run():
        try:
            cmd = ("[Console]::InputEncoding=[System.Text.Encoding]::UTF8; "
                   "Set-Clipboard -Value ([Console]::In.ReadToEnd())")
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           input=text.encode("utf-8"), capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Die abfangende Console: ui.console zeigt hierher
# ---------------------------------------------------------------------------
class _CaptureConsole(Console):
    """Alles, was per ui.console.print(...) kommt, landet als Block im Verlauf."""

    def __init__(self, screen: "Screen", width: int):
        super().__init__(file=io.StringIO(), force_terminal=True, color_system="truecolor",
                         width=width, theme=ui._build_theme(ui.current_theme()), soft_wrap=False)
        self._screen = screen

    def print(self, *objects, sep=" ", end="\n", style=None, justify=None, overflow=None,
              no_wrap=None, emoji=None, markup=None, highlight=None, width=None, height=None,
              crop=True, soft_wrap=None, new_line_start=False):
        if not objects:
            self._screen.append_block(Text(""))
            return
        renderables = self._collect_renderables(objects, sep, "", justify=justify, emoji=emoji,
                                                markup=markup, highlight=highlight)
        block = renderables[0] if len(renderables) == 1 else Group(*renderables)
        if style:
            block = Styled(block, style)
        self._screen.append_block(block)

    def push_theme(self, theme, inherit: bool = True) -> None:
        super().push_theme(theme, inherit=inherit)
        self._screen.apply_theme()

    def pop_theme(self) -> None:
        super().pop_theme()
        self._screen.apply_theme()

    def clear(self, home: bool = True) -> None:
        self._screen.clear()


# ---------------------------------------------------------------------------
# Modale Bildschirme
# ---------------------------------------------------------------------------
def _hints_text(**kw) -> Text:
    return fragments_to_text(ui.input_hints(200, **kw))


def _modus_text() -> Text:
    """Kleine Modus-Anzeige im Dialog (Shift+Tab wechselt auch dort)."""
    import modes
    p = ui.THEMES[ui.current_theme()]
    t = Text(no_wrap=True, overflow="crop")
    t.append("  Shift+Tab", style=f"{p['accent']}")
    t.append(f" Modus: {modes.label()}", style="#7d8590")
    return t


class _NemiModal(ModalScreen):
    """Gemeinsames für alle Dialoge: durchscheinender Hintergrund (der Verlauf
    dahinter bleibt lesbar – man muss ja sehen, WAS man freigibt) und
    Shift+Tab für den Arbeitsmodus, wie im Hauptbildschirm."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.add_class("nemi-modal")

    def on_key(self, event: events.Key) -> None:
        if event.key == "shift+tab":
            event.stop()
            self.app.action_mode()
            try:
                self.query_one("#modus", Static).update(_modus_text())
            except Exception:
                pass


class _SelectScreen(_NemiModal):
    """Auswahl-Menü: klickbare Knöpfe – oder ↑/↓/Tab · Enter · Esc = letzte Option · 1-9."""

    DEFAULT_CSS = """
    _SelectScreen > Vertical { width: 90%; max-width: 110; height: auto;
        background: #11131d; border: round #5b54a8; padding: 1 2; margin-bottom: 7; }
    _SelectScreen #q { margin-bottom: 1; }
    _SelectScreen Button { width: 100%; height: 1; min-height: 1; min-width: 10;
        margin: 0; padding: 0 1; content-align: left middle; text-align: left;
        background: #11131d; color: #9298ac; }
    _SelectScreen Button:hover { background: #1c1f30; color: #e4e7f2; }
    _SelectScreen Button.-sel { background: #1c1f30; color: #e4e7f2; text-style: bold; }
    _SelectScreen Button:focus { text-style: bold; }
    """
    BINDINGS = [Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
                Binding("tab", "move(1)", show=False), Binding("enter", "choose", show=False),
                Binding("escape", "cancel", show=False), Binding("ctrl+c", "cancel", show=False)]

    def __init__(self, question: str, options: list):
        super().__init__()
        self.question, self.options, self.sel = question, list(options), 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(terminal_safe(self.question), style="bold"), id="q")
            for i, (_val, label) in enumerate(self.options):
                yield Button(f"  {i + 1}. {terminal_safe(str(label))}", id=f"opt{i}",
                             compact=True)          # eine Zeile, ohne 3D-Rand
            yield Static(id="hint")
            yield Static(id="modus")

    def on_mount(self) -> None:
        self.query_one("#hint", Static).update(_hints_text(modal=True))
        self.query_one("#modus", Static).update(_modus_text())
        self._draw()

    def _draw(self) -> None:
        p = ui.THEMES[ui.current_theme()]
        for i, (_val, label) in enumerate(self.options):
            b = self.query_one(f"#opt{i}", Button)
            mark = "❯" if i == self.sel else " "
            b.label = Text(f"{mark} {i + 1}. {terminal_safe(str(label))}",
                           style=f"bold {p['accent']}" if i == self.sel else "")
            b.set_class(i == self.sel, "-sel")
        try:
            self.query_one(f"#opt{self.sel}", Button).focus()
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        idx = int(event.button.id[3:])
        self.dismiss(self.options[idx][0])

    def action_move(self, d: int) -> None:
        self.sel = (self.sel + d) % len(self.options)
        self._draw()

    def action_choose(self) -> None:
        self.dismiss(self.options[self.sel][0])

    def action_cancel(self) -> None:
        self.dismiss(self.options[-1][0])

    def on_key(self, event: events.Key) -> None:
        if event.key.isdigit() and event.key != "0":
            n = int(event.key) - 1
            if n < len(self.options):
                event.stop()
                self.dismiss(self.options[n][0])
            return
        super().on_key(event)


class _TextScreen(_NemiModal):
    """Eine Zeile Text (z.B. API-Key). Enter = OK, Esc = leer."""

    DEFAULT_CSS = """
    _TextScreen > Vertical { width: 90%; max-width: 110; height: auto;
        background: #11131d; border: round #5b54a8; padding: 1 2; margin-bottom: 7; }
    _TextScreen Input { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("ctrl+c", "cancel", show=False)]

    def __init__(self, question: str, password: bool):
        super().__init__()
        self.question, self.password = question, password

    def compose(self) -> ComposeResult:
        p = ui.THEMES[ui.current_theme()]
        with Vertical():
            yield Static(Text(terminal_safe(self.question), style=f"bold {p['accent']}"))
            yield Input(password=self.password, id="inp")
            yield Static(_hints_text(modal=True))

    def on_mount(self) -> None:
        self.query_one("#inp", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss("")


class _ReaderScreen(_NemiModal):
    """Langer Text zum Lesen: Aktionsprüfung (F8 gibt frei) oder Denktext (F2/Esc)."""

    DEFAULT_CSS = """
    _ReaderScreen > Vertical { width: 96%; height: 90%; background: #11131d;
        border: round #5b54a8; padding: 0 1; }
    _ReaderScreen VerticalScroll { height: 1fr; scrollbar-size-vertical: 1; }
    _ReaderScreen #hint { height: 1; }
    """
    BINDINGS = [
        Binding("escape", "close(False)", show=False),
        Binding("enter", "line(1)", show=False), Binding("ctrl+j", "line(1)", show=False),
        Binding("up", "line(-1)", show=False), Binding("down", "line(1)", show=False),
        Binding("pageup", "page(-1)", show=False), Binding("pagedown", "page(1)", show=False),
        Binding("home", "edge(0)", show=False), Binding("end", "edge(1)", show=False),
        Binding("ctrl+home", "edge(0)", show=False), Binding("ctrl+end", "edge(1)", show=False),
    ]

    def __init__(self, text: str, *, review: bool):
        super().__init__()
        self.text, self.review = text, review

    def compose(self) -> ComposeResult:
        with Vertical():
            with VerticalScroll(id="body"):
                yield Static(Text(terminal_safe(self.text), style="#d9deec" if self.review else "#b6bed3"))
            yield Static(id="hint")

    def on_mount(self) -> None:
        self.query_one("#hint", Static).update(
            _hints_text(modal=True, reviewing_action=self.review, reading_thinking=not self.review))
        self.query_one("#body", VerticalScroll).focus()

    def on_key(self, event: events.Key) -> None:
        if self.review and event.key == "f8":
            event.stop()
            self.dismiss(True)
        elif not self.review and event.key == "f2":
            event.stop()
            self.dismiss(False)
        else:
            super().on_key(event)

    def action_close(self, ok: bool) -> None:
        self.dismiss(bool(ok))

    def action_line(self, d: int) -> None:
        self.query_one("#body", VerticalScroll).scroll_relative(y=d, animate=False)

    def action_page(self, d: int) -> None:
        body = self.query_one("#body", VerticalScroll)
        (body.scroll_page_down if d > 0 else body.scroll_page_up)(animate=False)

    def action_edge(self, end: int) -> None:
        body = self.query_one("#body", VerticalScroll)
        (body.scroll_end if end else body.scroll_home)(animate=False)


# ---------------------------------------------------------------------------
# Auswahl & Texteingabe IM Eingaberahmen (kein Fenster darüber): Die Box
# „NemiCLI möchte ausführen“ steht direkt darüber im Verlauf und bleibt
# lesbar – man muss ja sehen, was man freigibt.
# ---------------------------------------------------------------------------
class _InlineSelect(Vertical, can_focus=True):
    """Frage + klickbare Knöpfe; Tastatur: ↑/↓/Tab · Enter · Esc = letzte · 1-9."""

    DEFAULT_CSS = """
    _InlineSelect { height: auto; background: #11131d; padding: 0 1; }
    _InlineSelect #q { margin-bottom: 0; }
    _InlineSelect Button { width: 100%; height: 1; min-height: 1; min-width: 10;
        margin: 0; padding: 0 1; content-align: left middle; text-align: left;
        background: #11131d; color: #9298ac; }
    _InlineSelect Button:hover { background: #1c1f30; color: #e4e7f2; }
    _InlineSelect Button.-sel { background: #1c1f30; color: #e4e7f2; text-style: bold; }
    _InlineSelect Button:focus { text-style: bold; }
    """
    BINDINGS = [Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False),
                Binding("tab", "move(1)", show=False), Binding("enter", "choose", show=False),
                Binding("escape", "cancel", show=False)]

    def __init__(self, question: str, options: list, done):
        super().__init__()
        self.question, self.options, self.sel, self._done = question, list(options), 0, done

    def compose(self) -> ComposeResult:
        yield Static(Text(terminal_safe(self.question), style="bold"), id="q")
        for i, (_val, label) in enumerate(self.options):
            yield Button(f"  {i + 1}. {terminal_safe(str(label))}", id=f"opt{i}", compact=True)

    def on_mount(self) -> None:
        self._draw()
        self.focus()

    def _draw(self) -> None:
        p = ui.THEMES[ui.current_theme()]
        for i, (_val, label) in enumerate(self.options):
            b = self.query_one(f"#opt{i}", Button)
            mark = "❯" if i == self.sel else " "
            b.label = Text(f"{mark} {i + 1}. {terminal_safe(str(label))}",
                           style=f"bold {p['accent']}" if i == self.sel else "")
            b.set_class(i == self.sel, "-sel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._done(self.options[int(event.button.id[3:])][0])

    def on_descendant_focus(self, event) -> None:
        # Klick auf einen Knopf gibt ihm den Fokus – die Tasten sollen trotzdem hier landen
        self.focus()

    def action_move(self, d: int) -> None:
        self.sel = (self.sel + d) % len(self.options)
        self._draw()

    def action_choose(self) -> None:
        self._done(self.options[self.sel][0])

    def action_cancel(self) -> None:
        self._done(self.options[-1][0])

    def on_key(self, event: events.Key) -> None:
        if event.key.isdigit() and event.key != "0":
            n = int(event.key) - 1
            if n < len(self.options):
                event.stop()
                self._done(self.options[n][0])
        elif event.key == "shift+tab":
            event.stop()
            self.app.action_mode()


class _InlineText(Vertical):
    """Frage + eine Eingabezeile (z.B. API-Key). Enter = OK, Esc = leer."""

    DEFAULT_CSS = """
    _InlineText { height: auto; background: #11131d; padding: 0 1; }
    _InlineText Input { border: none; height: 1; padding: 0 1; background: #1c1f30; }
    _InlineText Input:focus { border: none; }
    """
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, question: str, password: bool, done):
        super().__init__()
        self.question, self.password, self._done = question, password, done

    def compose(self) -> ComposeResult:
        p = ui.THEMES[ui.current_theme()]
        yield Static(Text(terminal_safe(self.question), style=f"bold {p['accent']}"))
        yield Input(password=self.password, id="inp")

    def on_mount(self) -> None:
        self.query_one("#inp", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._done(event.value)

    def action_cancel(self) -> None:
        self._done("")


# ---------------------------------------------------------------------------
# Eingabefeld: Enter sendet, Strg+J = neue Zeile, Tab = Vervollständigung
# ---------------------------------------------------------------------------
class _Composer(TextArea):
    _SCROLL = {"pageup": ("page", -1), "pagedown": ("page", 1), "ctrl+up": ("line", -1),
               "ctrl+down": ("line", 1), "ctrl+end": ("bottom", None)}

    async def _on_key(self, event: events.Key) -> None:
        nemi = self.app.nemi
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            nemi._submit()
            return
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            nemi._complete_next()
            return
        if event.key == "shift+tab":
            event.stop()
            event.prevent_default()
            self.app.action_mode()
            return
        if event.key in self._SCROLL:
            # Verlauf scrollen – die Eingabe ist ohnehin nur wenige Zeilen hoch
            event.stop()
            event.prevent_default()
            what, arg = self._SCROLL[event.key]
            nemi._scroll(what, arg)
            return
        await super()._on_key(event)


# ---------------------------------------------------------------------------
# Die App (Layout + Tasten)
# ---------------------------------------------------------------------------
class _Log(VerticalScroll):
    """Der Verlauf. Meldet seine Breite nach jedem Layout an den Screen und
    bleibt beim Fensterziehen unten, solange der Nutzer nicht hochgescrollt hat."""

    def on_resize(self, event: events.Resize) -> None:
        nemi = self.app.nemi
        nemi._sync_width()
        nemi._refresh_chrome()
        if nemi._follow:
            self.call_after_refresh(nemi._scroll_bottom)

    def on_mouse_scroll_up(self, event) -> None:
        self.app.nemi._follow = False

    def on_mouse_scroll_down(self, event) -> None:
        self.call_after_refresh(self.app.nemi._check_follow)


class _NemiApp(App):
    CSS = f"""
    Screen {{ background: #0c0d14; }}
    Screen.nemi-modal {{ background: rgba(0, 0, 0, 0.35); align: center bottom; }}
    _ReaderScreen.nemi-modal {{ align: center middle; }}
    #log {{ height: 1fr; scrollbar-size-vertical: 1; scrollbar-color: #2a2a36;
           scrollbar-color-hover: #3a3a4a; scrollbar-background: #0c0d14; }}
    #log > Static {{ width: 1fr; }}
    #log > _Block {{ width: 1fr; }}
    #live {{ width: 1fr; }}
    #header {{ height: 1; background: {_COMPOSER_BG}; }}
    #frame {{ height: auto; background: {_COMPOSER_BG}; border-left: round #5b54a8;
              border-right: round #5b54a8; border-bottom: round #5b54a8; }}
    #complete {{ height: auto; background: {_COMPOSER_BG}; display: none; padding-left: 3; }}
    #inline {{ height: auto; background: {_COMPOSER_BG}; }}
    #input {{ height: {_MIN_INPUT_ROWS}; background: {_COMPOSER_BG}; border: none; padding: 0 0 0 2;
              color: #e4e7f2; scrollbar-size-vertical: 1; }}
    #input:focus {{ border: none; }}
    #input .text-area--cursor-line {{ background: {_COMPOSER_BG}; }}
    #toolbar {{ height: 1; background: {_TOOLBAR_BG}; }}
    #hints {{ height: 1; background: {_COMPOSER_BG}; }}
    """
    # Esc/F2/Shift+Tab ohne priority: ein offenes Modal behandelt seine Tasten
    # selbst (Screen-Bindings kommen vor App-Bindings). Strg+C muss priority
    # haben (sonst kopiert das Textfeld) und reicht im Modal an dessen Abbruch weiter.
    BINDINGS = [
        Binding("escape", "esc", show=False),
        Binding("ctrl+c", "ctrl_c", show=False, priority=True),
        Binding("ctrl+d", "quit_app", show=False, priority=True),
        Binding("ctrl+q", "quit_app", show=False, priority=True),
        Binding("f2", "thinking", show=False),
        Binding("f12", "mitschnitt", show=False),
        Binding("shift+tab", "mode", show=False),
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("ctrl+up", "line(-1)", show=False),
        Binding("ctrl+down", "line(1)", show=False),
        Binding("ctrl+end", "bottom", show=False),
    ]

    def __init__(self, screen: "Screen"):
        super().__init__()
        self.nemi = screen

    def compose(self) -> ComposeResult:
        with _Log(id="log"):
            yield Static("", id="live")
        yield Static("", id="header")
        with Vertical(id="frame"):
            yield Static("", id="complete")
            yield Vertical(id="inline")
            yield _Composer("", id="input", soft_wrap=True, show_line_numbers=False,
                            tab_behavior="focus")
        yield Static("", id="toolbar")
        yield Static("", id="hints")

    def on_mount(self) -> None:
        self.nemi._bind(self)
        self.query_one("#live", Static).display = False
        self.query_one("#input", _Composer).focus()
        self.set_interval(0.2, self.nemi._tick)
        self.nemi._refresh_chrome()
        if self.nemi.start_text:
            self.set_timer(0.3, self.nemi._submit_start_text)

    def on_unmount(self) -> None:
        self.nemi._lebenszeichen_ende()          # Lebenszeichen weg, sobald das Fenster zu ist

    # --- Größe: Console-Breite mitziehen (Banner-Padding & Co) ---
    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self.nemi._sync_width)
        self.nemi._refresh_chrome()

    # --- Eingabe ---
    def on_text_area_changed(self, _event) -> None:
        self.nemi._on_text_changed()

    # --- Aktionen (Tasten) ---
    def action_esc(self) -> None:
        self.nemi._on_escape()

    def action_ctrl_c(self) -> None:
        # Markierter Text im Verlauf? Dann kopieren – das erwartet jeder von Strg+C.
        try:
            text = self.screen.get_selected_text()
        except Exception:
            text = None
        if text:
            self.copy_to_clipboard(text)          # Terminal-Weg (OSC 52), falls unterstützt
            _windows_clipboard(text)              # sicherer Weg unter Windows
            self.screen.clear_selection()
            ui.info(f"📋 {len(text)} Zeichen in die Zwischenablage kopiert.")
            return
        if self.nemi._modal is not None:
            scr = self.screen
            try:
                if hasattr(scr, "action_cancel"):
                    scr.action_cancel()
                elif hasattr(scr, "action_close"):
                    scr.action_close(False)
            except Exception:
                pass
            return
        self.nemi._on_ctrl_c()

    def action_quit_app(self) -> None:
        self.exit()

    def action_thinking(self) -> None:
        self.nemi._toggle_thinking()

    def action_mitschnitt(self) -> None:
        """F12: das ganze laufende Gespraech als Markdown sichern.

        Im Terminal laesst sich ein langer Verlauf kaum markieren, und der
        Denktext steht dort ohnehin nur zusammengeklappt. Die Datei hat alles."""
        import mitschrift
        try:
            ziel = mitschrift.speichern()
        except ValueError as e:
            ui.warn(f"F12: {e}")
            return
        except OSError as e:
            ui.error(f"F12: konnte nicht schreiben - {e}")
            return
        ui.success(f"F12 - Gespraech gesichert: {ziel.name} "
                   f"({mitschrift.anzahl()} Eintraege, mit Denktext)")
        ui.info(f"Liegt in: {ziel.parent}")

    def action_mode(self) -> None:
        import modes
        key = modes.cycle()
        ui.info(f"Modus: {modes.label()}" + (
            f"  ·  Zünder: nach {modes.AUTO_FUSE_S // 60} min zurück in Chatten"
            if key == "auto" else ""))
        self.nemi._refresh_chrome()

    def action_page(self, d: int) -> None:
        self.nemi._scroll("page", d)

    def action_line(self, d: int) -> None:
        self.nemi._scroll("line", d)

    def action_bottom(self) -> None:
        self.nemi._scroll("bottom", None)


# ---------------------------------------------------------------------------
# Der Screen: Zustand + Schnittstelle für main/confirm/ui
# ---------------------------------------------------------------------------
class Screen:
    def __init__(self):
        self.ctx = None
        self.handler = None
        self.session_state: dict = {}
        self._model_completions_fn = lambda: {}
        self.app: _NemiApp | None = None
        self.busy = False
        self._abort = False
        self.thinking_expanded = False
        self._last_thinking = None
        self._thinking_live = False
        self._modal: str | None = None        # "select" | "text" | "review" | "thinking" | None
        # --- Übungsmodus (Leerlauf) ---
        self._practice_fn = None
        self.practice_on = True
        self._practicing = False
        self._practice_task = None
        self._practice_stopping = False
        self._practice_consent = None
        self._idle_after = 600.0
        self._last_input = time.monotonic()
        # --- Helfer-Agenten ---
        self.helfer_aktiv = 0
        self._pulse_on = True
        # --- intern ---
        self._old_console = None
        self._loop_thread = None
        self._blocks = 0
        self._log = self._live = self._header = self._toolbar = self._hints = None
        self._input = self._complete = self._frame = self._inline = None
        self._pending: list = []              # Blöcke, die vor dem Start gedruckt wurden
        self._tx_theme_pushed = False
        self._follow = True                   # Verlauf hängt am unteren Ende?
        self.start_text: str | None = None    # --sag: erste Nachricht gleich abschicken
        self._comp_base = None                # (Basistext, Index) für Tab-Vervollständigung
        self._comp_items: list = []
        self._completer = SlashCompleter(
            get_themes=lambda: ui.THEMES,
            get_models=lambda: self._model_completions_fn(),
            get_strengths=lambda: M.strength_menu(self.session_state.get("model")),
            get_chats=chatstore.chat_args,
            get_checkpoints=_checkpoint_menu,
            get_personas=_persona_menu,
        )
        try:
            import os
            self.width = max(40, os.get_terminal_size().columns)
        except OSError:
            self.width = 100

    # -- Installation: ui umlenken ----------------------------------------
    def install(self) -> None:
        self._old_console = ui.console
        ui.console = _CaptureConsole(self, self._text_width())
        ui._theme_pushed = False
        ui.TUI = self
        try:
            import subagents
            subagents.on_aktiv(self.set_helfer)
        except Exception:
            pass

    def uninstall(self) -> None:
        if self._old_console is not None:
            ui.console = self._old_console
        ui.TUI = None
        ui._theme_pushed = False

    def _bind(self, app: _NemiApp) -> None:
        """Vom App-Mount: Widgets merken, gepufferte Blöcke einhängen."""
        self._loop_thread = threading.current_thread()
        self._log = app.query_one("#log", _Log)
        self._live = app.query_one("#live", Static)
        self._header = app.query_one("#header", Static)
        self._toolbar = app.query_one("#toolbar", Static)
        self._hints = app.query_one("#hints", Static)
        self._input = app.query_one("#input", _Composer)
        self._complete = app.query_one("#complete", Static)
        self._frame = app.query_one("#frame", Vertical)
        self._inline = app.query_one("#inline", Vertical)
        self.apply_theme()
        pending, self._pending = self._pending, []
        for r in pending:
            self._mount_block(r)
        self._sync_width()

    def apply_theme(self) -> None:
        """Theme der rich-Console auch der Textual-Console geben (Stilnamen wie
        'brand'/'muted' müssen dort auflösbar sein) und Rahmenfarben setzen."""
        if self.app is None:
            return
        try:
            if self._tx_theme_pushed:
                self.app.console.pop_theme()
            self.app.console.push_theme(ui._build_theme(ui.current_theme()))
            self._tx_theme_pushed = True
        except Exception:
            pass
        self._refresh_chrome()
        try:
            self.app.refresh()
        except Exception:
            pass

    # -- Breite ------------------------------------------------------------
    def _text_width(self) -> int:
        try:
            return max(40, self._log.content_size.width)
        except Exception:
            return max(40, self.width - 1)

    def _sync_width(self) -> None:
        w = self._text_width()
        try:
            if self._log.content_size.width <= 0:          # noch kein Layout
                w = max(40, self.app.size.width - 2)
        except Exception:
            pass
        self.width = w
        try:
            ui.console.width = w
        except Exception:
            pass

    def _terminal_width(self) -> int:
        try:
            return max(1, self.app.size.width)
        except Exception:
            return self.width

    # -- Verlauf -------------------------------------------------------------
    def append_block(self, renderable) -> None:
        """Ein gedruckter Block kommt in den Verlauf (Thread-sicher)."""
        renderable = sanitize(renderable)
        if self._log is None or self.app is None:
            self._pending.append(renderable)
            return
        if threading.current_thread() is not self._loop_thread:
            try:
                self.app.call_from_thread(self._mount_block, renderable)
            except Exception:
                self._pending.append(renderable)
            return
        self._mount_block(renderable)

    def _check_follow(self) -> None:
        """Ist der Verlauf (wieder) ganz unten? Dann hängen wir uns wieder ans Ende."""
        try:
            if self._log.is_vertical_scroll_end or self._log.max_scroll_y == 0:
                self._follow = True
        except Exception:
            pass

    def _mount_block(self, renderable) -> None:
        follow = self._follow
        w = _Block(renderable, classes="block")
        self._log.mount(w, before=self._live)
        self._blocks += 1
        if self._blocks > _MAX_BLOCKS:
            alt = self._log.query(".block")
            for old in list(alt)[: self._blocks - _MAX_BLOCKS]:
                old.remove()
            self._blocks = _MAX_BLOCKS
        if follow:
            self.app.call_after_refresh(self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        try:
            self._log.scroll_end(animate=False)
        except Exception:
            pass

    def _scroll(self, what: str, arg) -> None:
        if self._log is None:
            return
        if what == "page":
            (self._log.scroll_page_down if arg > 0 else self._log.scroll_page_up)(animate=False)
        elif what == "line":
            self._log.scroll_relative(y=arg, animate=False)
        else:
            self._log.scroll_end(animate=False)
            self._follow = True
            return
        if arg < 0:
            self._follow = False
        else:
            self.app.call_after_refresh(self._check_follow)

    def print_reflow(self, make) -> None:
        """Renderable, das bei jeder Breite frisch gebaut wird (Banner)."""
        self.append_block(_ReflowRenderable(make))

    def clear(self) -> None:
        self._last_thinking = None
        self._thinking_live = False
        self.live_clear()
        if self._log is None:
            self._pending.clear()
            return
        for w in list(self._log.query(".block")):
            w.remove()
        self._blocks = 0

    # -- Live-Region (streamende Antwort) -----------------------------------
    def live_update(self, renderable) -> None:
        if self._live is None:
            return
        self._live.update(sanitize(renderable))
        self._live.display = True
        if self._follow:
            self.app.call_after_refresh(self._scroll_bottom)

    def live_clear(self) -> None:
        if self._live is None:
            return
        self._live.update("")
        self._live.display = False

    # -- vom Chat-Loop genutzt ----------------------------------------------
    def consume_abort(self) -> bool:
        if self._abort:
            self._abort = False
            return True
        return False

    def set_thinking(self, text: str, seconds: float | None, live: bool) -> None:
        if text:
            self._last_thinking = {"text": text, "seconds": seconds}
        self._thinking_live = live and bool(text)

    def invalidate(self) -> None:
        self._refresh_chrome()

    def force_repaint(self) -> None:
        if self.app is not None:
            try:
                self.app.refresh(repaint=True, layout=True)
            except Exception:
                pass
        self._refresh_chrome()

    # -- Helfer-Agenten -------------------------------------------------------
    def set_helfer(self, n: int) -> None:
        self.helfer_aktiv = max(0, n)
        if self.helfer_aktiv == 0:
            self._pulse_on = True
        self._refresh_chrome()

    # -- Kopfzeile, Toolbar, Hinweise -----------------------------------------
    def _tick(self) -> None:
        """Alle 0,2 s: Animationen, Zünder, Puls, Lebenszeichen, Briefkasten."""
        import modes
        if modes.check_fuse():
            ui.warn(modes.fuse_message())
        self._lebenszeichen()
        if self.helfer_aktiv > 0:
            self._pulse_on = int(time.time() * 2) % 2 == 0      # halbe Sekunde hell/dunkel
        self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        if self._header is None or self.app is None:
            return
        try:
            if self.ctx is not None:
                self.ctx.sync_session()
            self._header.update(self._composer_header())
            self._toolbar.update(fragments_to_text(
                ui.bottom_toolbar(self.session_state, width=self._terminal_width())))
            self._hints.update(fragments_to_text(ui.input_hints(
                self._terminal_width(), busy=self.busy, practicing=self._practicing,
                modal=self._modal is not None, has_thinking=bool(self._last_thinking),
                reading_thinking=self._modal == "thinking",
                reviewing_action=self._modal == "review")))
            p = ui.THEMES[ui.current_theme()]
            color = p["accent_dim"] if self.busy else p["brand_dim"]
            for edge in ("border_left", "border_right", "border_bottom"):
                setattr(self._frame.styles, edge, ("round", color))
        except Exception:
            pass

    def _composer_header(self) -> Text:
        width = self._terminal_width()
        p = ui.THEMES[ui.current_theme()]
        color = p["accent_dim"] if self.busy else p["brand_dim"]
        border = Style(color=color, bgcolor=_COMPOSER_BG)
        muted = Style(color="#9298ac", bgcolor=_COMPOSER_BG)
        accent = Style(color=p["accent"], bgcolor=_COMPOSER_BG)
        status = "● bereit"
        if self._modal is not None:
            status = {"select": "◇ Auswahl", "text": "◇ Eingabe",
                      "thinking": "◇ Denktext", "review": "◇ Aktion prüfen"}[self._modal]
        elif self._practicing:
            status = "◇ beendet Übung …" if self._practice_stopping else "◇ übt"
        elif self.busy:
            status = "◌ arbeitet"
        left = Text(no_wrap=True, overflow="crop")
        left.append("╭─", border)
        left.append(f" {ui.persona_name()} ", accent + Style(bold=True))
        left.append("· ", muted)
        left.append(status, accent)
        left.append(" ", border)
        if self.helfer_aktiv > 0:
            punkt = (Style(color=p["accent"], bgcolor=_COMPOSER_BG, bold=True) if self._pulse_on
                     else Style(color=p["accent_dim"], bgcolor=_COMPOSER_BG))
            left.append("· ", muted)
            left.append("●", punkt)
            left.append(f" Subagenten aktiv: {self.helfer_aktiv} ", accent)
        # Workspace: gelb-fett, direkt hinter dem Status - solange einer
        # festgenagelt ist, soll man es nicht uebersehen koennen.
        ws = fragments_to_text(mascot.workspace_badge(width=max(20, width // 2)))
        if ws.cell_len:
            left.append("· ", muted)
            left.append_text(ws)
            left.append(" ", border)
        right = fragments_to_text(mascot.rprompt())
        right.append("─╮", border)
        if width >= 70 and self._modal is None:
            badge = fragments_to_text(mascot.folder_badge(compact=width < 105))
            candidate = Text(" ", muted) + badge + Text("  ", muted) + right
            if left.cell_len + candidate.cell_len + 3 <= width:
                right = candidate
        if left.cell_len + right.cell_len > width:
            right = Text("╮", border)
            left.truncate(max(0, width - 1), overflow="ellipsis")
        gap = max(0, width - left.cell_len - right.cell_len)
        out = Text(no_wrap=True, overflow="crop")
        out.append_text(left)
        out.append("─" * gap, border)
        out.append_text(right)
        # Hintergrund der ganzen Zeile
        out.stylize(Style(bgcolor=_COMPOSER_BG))
        return out

    # -- Eingabe ------------------------------------------------------------
    def _input_height(self) -> int:
        try:
            width = max(1, self._input.content_size.width)
        except Exception:
            width = 80
        rows = 0
        for line in (self._input.text or "").split("\n"):
            cells = Text(line.expandtabs(4)).cell_len + 1
            rows += max(1, -(-cells // width))
        return max(_MIN_INPUT_ROWS, min(_MAX_INPUT_ROWS, rows))

    def _on_text_changed(self) -> None:
        self._last_input = time.monotonic()
        self._stop_practice()
        try:
            self._input.styles.height = self._input_height()
        except Exception:
            pass
        self._update_completions()

    def _candidates(self, text: str) -> list:
        if not text.startswith("/") or "\n" in text:
            return []
        try:
            doc = Document(text, len(text))
            return list(self._completer.get_completions(doc, CompleteEvent()))[:_MAX_COMPLETIONS]
        except Exception:
            return []

    def _update_completions(self) -> None:
        if self._complete is None:
            return
        text = self._input.text
        if self._comp_base is not None and text != self._comp_base[2]:
            self._comp_base = None                  # Nutzer hat weitergetippt
        items = self._candidates(text)
        self._comp_items = items
        if not items:
            self._complete.display = False
            return
        p = ui.THEMES[ui.current_theme()]
        sel = self._comp_base[1] if self._comp_base is not None else -1
        t = Text(no_wrap=True, overflow="ellipsis")
        for i, c in enumerate(items):
            st = f"bold {p['accent']}" if i == sel else "#c8c4ff"
            t.append(f"{c.text:<22}", style=st)
            meta = getattr(c, "display_meta_text", "") or ""
            t.append(f" {meta}", style="#7d8590")
            if i < len(items) - 1:
                t.append("\n")
        self._complete.update(t)
        self._complete.display = True

    def _complete_next(self) -> None:
        """Tab: erste Vervollständigung einsetzen, weitere Tabs blättern."""
        text = self._input.text
        if self._comp_base is None:
            items = self._candidates(text)
            if not items:
                return
            self._comp_base = [text, 0, None]
        else:
            base, idx, _ = self._comp_base
            items = self._candidates(base)
            if not items:
                self._comp_base = None
                return
            self._comp_base[1] = (idx + 1) % len(items)
        base, idx, _ = self._comp_base
        c = items[idx]
        neu = base[: len(base) + c.start_position] + c.text
        self._comp_base[2] = neu
        self._input.load_text(neu)
        self._input.move_cursor(self._input.document.end)
        self._comp_items = items
        self._update_completions()

    def _echo_user(self, text: str) -> None:
        ui.console.print(Text(f"  ❯ {text}", style="user"))

    def _submit(self) -> None:
        self._last_input = time.monotonic()
        text = self._input.text
        if self.busy:
            return
        self._input.load_text("")
        self._comp_base = None
        self._update_completions()
        if not text.strip():
            return
        self._follow = True                   # beim Absenden ans untere Ende springen
        self._echo_user(text)
        self.busy = True
        self._refresh_chrome()
        self.app.call_after_refresh(self._scroll_bottom)

        async def _runner():
            result = None
            try:
                result = await self.handler(self.ctx, text)
            except Exception as e:
                ui.error(f"Fehler: {e}")
            finally:
                self.busy = self._practicing
                self._last_input = time.monotonic()
                self.force_repaint()
            if result == "exit" and self.app is not None:
                self.app.exit()

        asyncio.get_running_loop().create_task(_runner())

    # -- Lebenszeichen & Briefkasten (Rechtsklick-Menü -> offenes Fenster) ------
    _alive_stamp = 0.0
    _inbox_stamp = 0.0

    def _lebenszeichen(self) -> None:
        """Alle 2 s .nemicli.alive anfassen; jede Sekunde .nemicli.inbox prüfen."""
        now = time.monotonic()
        try:
            import kontextmenue as K
        except Exception:
            return
        if now - self._alive_stamp >= 2.0:
            self._alive_stamp = now
            try:
                K.ALIVE_DATEI.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
        if now - self._inbox_stamp >= 1.0:
            self._inbox_stamp = now
            if self.busy or self._modal is not None:
                return                              # erst, wenn Lara frei ist
            try:
                if not K.INBOX_DATEI.exists():
                    return
                text = K.INBOX_DATEI.read_text(encoding="utf-8").strip()
                K.INBOX_DATEI.unlink()
            except OSError:
                return
            if text:
                self._input.load_text(text)
                self._submit()

    def _lebenszeichen_ende(self) -> None:
        try:
            import kontextmenue as K
            K.ALIVE_DATEI.unlink()
        except Exception:
            pass

    def _submit_start_text(self) -> None:
        """--sag: Text ins Eingabefeld und abschicken, sobald die Oberfläche steht."""
        text, self.start_text = self.start_text, None
        if text and not self.busy:
            self._input.load_text(text)
            self._submit()

    def _on_escape(self) -> None:
        self._last_input = time.monotonic()
        if self._modal is not None:
            return                          # das Modal hat eigene Esc-Behandlung
        if self._practicing:
            self._stop_practice()
        elif self.busy:
            self._abort = True

    def _on_ctrl_c(self) -> None:
        if self._modal is not None:
            return
        if self._practicing:
            self._stop_practice()
        elif self.busy:
            self._abort = True
        else:
            self.app.exit()

    # -- Denktext (F2) ---------------------------------------------------------
    def _toggle_thinking(self) -> None:
        self._last_input = time.monotonic()
        if self._modal is not None:
            return
        if self._thinking_live and self.busy:
            self.thinking_expanded = not self.thinking_expanded
        elif self._last_thinking:
            asyncio.get_running_loop().create_task(self._show_thinking())
        self._refresh_chrome()

    async def _show_thinking(self) -> None:
        try:
            await self._modal_run("thinking",
                                  _ReaderScreen(self._last_thinking["text"], review=False))
        except RuntimeError:
            pass

    # -- Übungsmodus (Leerlauf) – unverändert übernommen ----------------------
    def _stop_practice(self) -> bool:
        if not self._practicing:
            return False
        if self._practice_stopping:
            return True
        self._practice_stopping = True
        if self._practice_task is not None and not self._practice_task.done():
            self._practice_task.cancel()
        self._refresh_chrome()
        return True

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if (not self.practice_on or self.busy or self._practicing
                    or self._modal is not None or self._practice_fn is None):
                continue
            if self.ctx is None or self.ctx.backend is None:
                continue
            if time.monotonic() - self._last_input < self._idle_after:
                continue
            await self._start_practice()

    async def _start_practice(self) -> None:
        prov = M.split_ref(self.ctx.model or "")[0]
        is_cloud = prov not in ("local", "ollama", "")
        if is_cloud and self._practice_consent is None:
            choice = await self.select(
                "⚠ Übungsmodus mit CLOUD-Modell starten? Das verbraucht laufend "
                "Tokens und kann ins Geld gehen.",
                [("ja",   "Ja, mit Cloud-Modell üben (kostet)"),
                 ("nein", "Nein – Übungsmodus aus")])
            self._practice_consent = (choice == "ja")
            self._last_input = time.monotonic()
            if not self._practice_consent:
                self.practice_on = False
                return
        if is_cloud and self._practice_consent is False:
            return
        self._launch_practice(self.ctx, self._practice_fn, repeat=True)

    def _launch_practice(self, ctx, practice_fn, *, repeat):
        previous_busy = self.busy
        owner = asyncio.current_task()
        self._practicing = True
        self._practice_stopping = False
        self.busy = True
        self._abort = False
        self._refresh_chrome()

        def should_stop() -> bool:
            return (self._practice_stopping or self._abort
                    or (repeat and not self.practice_on))

        def release():
            self._practicing = False
            self._practice_stopping = False
            self.busy = previous_busy and owner is not None and not owner.done()
            self._practice_task = None
            self._last_input = time.monotonic()
            self.force_repaint()

        async def _runner():
            result = "abgebrochen"
            try:
                while not should_stop():
                    result = await practice_fn(ctx, should_stop)
                    if not repeat or result in ("abgelehnt", "abgebrochen"):
                        break
                    for _ in range(15):
                        if should_stop():
                            break
                        await asyncio.sleep(0.2)
                return result
            except asyncio.CancelledError:
                return "abgebrochen"
            except Exception as e:
                ui.error(f"Übungsmodus-Fehler: {e}")
                return "fehler"
            finally:
                release()

        self._practice_task = asyncio.create_task(_runner())
        self._practice_task.add_done_callback(
            lambda task: release() if self._practice_task is task else None)
        return self._practice_task

    async def run_practice_once(self, ctx, practice_fn):
        if self._practicing:
            return "abgebrochen"
        task = self._launch_practice(ctx, practice_fn, repeat=False)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return "abgebrochen"
            self._stop_practice()
            await asyncio.shield(task)
            raise

    # -- Modale Menüs / Eingaben ------------------------------------------------
    async def _modal_run(self, kind: str, screen: ModalScreen):
        if self._modal is not None:
            raise RuntimeError("Eine andere Rückfrage ist bereits geöffnet.")
        self._modal = kind
        self._refresh_chrome()
        fut = asyncio.get_running_loop().create_future()

        def _done(result):
            if not fut.done():
                fut.set_result(result)

        try:
            self.app.push_screen(screen, callback=_done)
            return await fut
        finally:
            self._modal = None
            self._last_input = time.monotonic()
            try:
                self._input.focus()
            except Exception:
                pass
            self._refresh_chrome()

    async def _inline_run(self, kind: str, make_widget):
        """Auswahl/Texteingabe im Eingaberahmen: das Eingabefeld weicht solange,
        der Verlauf darüber bleibt komplett sichtbar."""
        if self._modal is not None:
            raise RuntimeError("Eine andere Rückfrage ist bereits geöffnet.")
        fut = asyncio.get_running_loop().create_future()

        def _done(result):
            if not fut.done():
                fut.set_result(result)

        widget = make_widget(_done)
        self._modal = kind
        self._input.display = False
        self._complete.display = False
        self._refresh_chrome()
        self._follow = True
        self.app.call_after_refresh(self._scroll_bottom)
        try:
            await self._inline.mount(widget)
            return await fut
        finally:
            try:
                await widget.remove()
            except Exception:
                pass
            self._modal = None
            self._input.display = True
            self._last_input = time.monotonic()
            try:
                self._input.focus()
            except Exception:
                pass
            self._refresh_chrome()

    async def review_action(self, title: str, preview: str) -> bool:
        """Vollständige Aktionsvorschau; nur F8 gibt diese einzelne Aktion frei."""
        return (await self._modal_run("review", _ReaderScreen(
            f"{title}\n\n{preview}", review=True))) is True

    async def select(self, question, options):
        """Auswahl-Menü im Eingaberahmen (ersetzt confirm.ask im TUI)."""
        return await self._inline_run(
            "select", lambda done: _InlineSelect(question, options, done))

    async def text_input(self, question, password=False):
        """Texteingabe (z.B. API-Key) im Eingaberahmen."""
        return await self._inline_run(
            "text", lambda done: _InlineText(question, password, done))

    # -- Start -------------------------------------------------------------
    async def run(self, ctx, handler, session_state=None, model_completions=None,
                  practice=None) -> None:
        self.ctx = ctx
        self.handler = handler
        if session_state is not None:
            self.session_state = session_state
        if model_completions is not None:
            self._model_completions_fn = model_completions
        self._practice_fn = practice
        self.app = _NemiApp(self)
        idle = asyncio.create_task(self._idle_loop())
        try:
            await self.app.run_async()
        finally:
            idle.cancel()
            self._lebenszeichen_ende()
            practice_task = self._practice_task
            if practice_task is not None:
                self._stop_practice()
                await asyncio.shield(practice_task)
            maus_tracking_aus()
