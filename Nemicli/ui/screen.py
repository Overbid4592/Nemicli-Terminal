"""
screen.py - Das Vollbild-TUI von NemiCLI (Eingabe fest unten, Verlauf scrollt oben).

Idee: prompt_toolkit übernimmt den ganzen Bildschirm. Oben ein Verlaufsbereich,
der den letzten Teil des Gesprächs zeigt; unten – fest verankert – die Eingabe
mit ML-Balken links und Maskottchen rechts, darunter die Statusleiste.

Der Trick, damit die bestehende, rich-basierte Ausgabe weiter funktioniert:
`ui.console` wird auf einen Scroll-Puffer umgelenkt. Jeder `console.print(...)`
(Panels, Antworten, Infos) landet als fertig gefärbte ANSI-Zeilen im Verlauf.
Streamende Antworten gehen über eine „Live-Region" (live_update/live_clear).
Interaktive Untermenüs (Bestätigung, Modellauswahl) laufen via `in_terminal`
kurz außerhalb des Vollbilds und kehren danach zurück.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import time

from rich.console import Console

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, FormattedTextControl
from prompt_toolkit.layout.containers import (Float, FloatContainer,
                                              ConditionalContainer)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import ConditionalProcessor, PasswordProcessor
from prompt_toolkit.layout.screen import Char, Screen as _PTScreen
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import DynamicStyle
from prompt_toolkit.utils import get_cwidth

import ui
import mascot
from commands import SlashCompleter
import models as M
import chatstore


def _checkpoint_menu() -> dict:
    """Lazy: Bild-Checkpoints für die Autovervollständigung (ohne Startlast)."""
    try:
        import imagegen
        return imagegen.menu()
    except Exception:
        return {}


def _persona_menu() -> dict:
    """Persönlichkeiten für /persönlichkeiten <Tab> (eingebaute + eigene Dateien)."""
    try:
        import persoenlichkeiten
        return persoenlichkeiten.menu()
    except Exception:
        return {}


def maus_tracking_aus() -> None:
    """Schaltet ALLE Maus-Tracking-Modi im Terminal hart ab.

    prompt_toolkit aktiviert sie für Klick/Scroll (`mouse_support=True`). Auf
    manchen Windows-Terminals bleiben sie nach dem Beenden hängen – dann schreibt
    das Terminal bei JEDER Mausbewegung Zeichen wie `[555;73;46M` in die Shell
    (und PowerShell versucht sie als Befehl zu parsen → Fehlerflut). Diese
    Sequenzen setzen 1000/1002/1003 (Klick/Button/Bewegung) und die
    Erweiterungen 1006/1015 zurück. Als atexit-Handler registriert, damit es
    auch bei einem unerwarteten Ende greift.
    """
    import sys
    for stream in (sys.__stdout__, sys.stdout):
        try:
            if stream and stream.isatty():
                stream.write("\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l")
                stream.flush()
                return
        except Exception:
            pass

# Fester Eingabebereich: Rahmen oben/unten + Status + Tastenkürzel.
_CHROME_ROWS = 4
_COMPOSER_BG = "#11131d"
_MIN_INPUT_ROWS = 3        # so hoch ist die Eingabe MINDESTENS (Luft zum Tippen)
_MAX_INPUT_ROWS = 8        # so hoch darf die Eingabe höchstens werden
_MAX_LINES = 6000          # Verlauf im Speicher kappen


# --- Zeichenbreite: Terminal und prompt_toolkit müssen sich einig sein --------
# Gemessen (Windows Terminal, 12.09.2026): alle nackten Symbole der UI stimmen
# überein. Auseinander laufen sie nur bei
#   · Symbol + U+FE0F (Variation Selector-16, „zeig mich als buntes Emoji"):
#     ❤️ ⚠️ 🛡️ …   Terminal 2 Zellen, prompt_toolkit 1
#   · ZWJ-Ketten (👨‍💻), Hauttönen (👋🏽), Keycaps (1️⃣): Terminal 2, prompt_toolkit 4
# Ein solches Zeichen verschiebt den Rest der Zeile um eine Zelle; beim nächsten
# Hochrutschen des Verlaufs bleiben dann Buchstaben stehen, die prompt_toolkit
# für „unverändertes Leerzeichen" hält (die Geister-Zeichen). Häufigster
# Auslöser: „<3" → ❤️ aus emoji.py. Deshalb wird alles, was auf den Bildschirm
# geht, auf die nackte Form gebracht – Terminal, rich und prompt_toolkit
# rechnen dann gleich. (Flaggen 🇩🇪 bleiben; die fängt _mark_dirty ab.)
_VS_KEYCAP = re.compile("[︎️⃣]")
_SKIN = re.compile("[\U0001F3FB-\U0001F3FF]")
_ZWJ_TAIL = re.compile("‍.")


def terminal_safe(text: str) -> str:
    """Emoji-Varianten auf die Form bringen, deren Breite überall gleich ist."""
    if not text or text.isascii():
        return text
    text = _VS_KEYCAP.sub("", text)
    text = _SKIN.sub("", text)
    return _ZWJ_TAIL.sub("", text)


def _dirty_screen(height: int, width: int) -> _PTScreen:
    """Ein „vorheriger Bildschirm", der in JEDER Zelle etwas anderes enthält
    als jede echte Ausgabe. prompt_toolkit vergleicht damit und schreibt
    folglich jede Zelle neu – ohne erase_screen, also ohne Flackern. Das
    überschreibt Reste, die der Diff sonst für unverändert hielte."""
    s = _PTScreen(default_char=Char("\x00", "class:nemi-dirty"))
    s.height, s.width = height, width
    return s


class _ReflowBlock:
    """Ein Verlaufs-Block, der sich bei neuer Terminalbreite selbst neu rendert
    (z.B. der Banner). Merkt sich die Fabrik fürs Renderable und die Breite,
    in der zuletzt gerendert wurde – nur bei ANDERER Breite wird neu gerendert."""

    def __init__(self, make):
        self.make = make            # () -> rich-Renderable
        self.width = None           # Breite des letzten Renderns


class _ReflowLine(str):
    """Eine Verlaufszeile, die zu einem _ReflowBlock gehört. Verhält sich
    überall wie ein normaler str; nur der Reflow erkennt daran den Block."""
    __slots__ = ("block",)

    def __new__(cls, text: str, block: _ReflowBlock):
        obj = super().__new__(cls, text)
        obj.block = block
        return obj


class _Sink:
    """Datei-artiges Ziel für die rich-Console: schneidet die Ausgabe in Zeilen
    und hängt sie an den Verlauf des Screens (committed) an."""

    def __init__(self, screen: "Screen"):
        self.screen = screen
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += terminal_safe(text)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.screen.transcript.append(line)
        if len(self.screen.transcript) > _MAX_LINES:
            del self.screen.transcript[:-_MAX_LINES]
        self.screen.invalidate()
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


class _ScrollWindow(Window):
    """Verlaufsfenster, das das Mausrad selbst abfängt und unseren Scroll-Offset
    bewegt (das normale Window würde Scroll-Events schlucken)."""

    def __init__(self, screen: "Screen", **kw):
        super().__init__(**kw)
        self._screen = screen

    def _mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._screen._scroll_by(3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._screen._scroll_by(-3)
            return None
        return super()._mouse_handler(mouse_event)


class Screen:
    def __init__(self):
        self.transcript: list[str] = []      # fertige ANSI-Zeilen (Verlauf)
        self.live_text: str = ""             # aktuelle Streaming-Ansicht (transient)
        self.scroll = 0                      # 0 = unten verankert; >0 = nach oben gescrollt
        self._out_rows = 40                  # zuletzt bekannte Höhe des Verlaufsbereichs
        self._layout_key = None              # wann sind Zeilen verrutscht? (s. _mark_dirty)
        self._modal = None                   # aktives Menü/Eingabe-Modal (siehe select/text_input)
        self.ctx = None
        self.handler = None
        self.session_state: dict = {}        # von main durchgereicht (Tokens etc.)
        self._model_completions_fn = lambda: {}
        self.app: Application | None = None
        self.busy = False
        self._abort = False
        self.thinking_expanded = False
        self._last_thinking = None           # nur im RAM; keine Chat-/Prompt-Datei
        self._thinking_live = False
        # --- Übungsmodus (Leerlauf) ---
        self._practice_fn = None             # von main: async practice_round(ctx, should_stop)
        self.practice_on = True              # Auto-Üben im Leerlauf erlaubt?
        self._practicing = False             # läuft gerade eine Übungsrunde?
        self._practice_task = None           # die laufende Übungs-Task (abbrechbar)
        self._practice_stopping = False     # genau ein Cancel bis Cleanup fertig ist
        self._practice_consent = None        # Cloud-Kosten ok? None=ungefragt
        self._idle_after = 600.0            # Sekunden Ruhe bis Übung startet (10 Min)
        self._last_input = time.monotonic()  # letzte Nutzer-Aktivität
        self._old_console = None
        self._sink = _Sink(self)
        # --- Helfer-Agenten: Anzeige „● Subagenten aktiv: n" mit pulsierendem Punkt
        self.helfer_aktiv = 0
        self._pulse_on = True                # Punkt hell/dunkel im Wechsel
        self._pulse_task = None
        try:
            self.width = max(40, os.get_terminal_size().columns)
        except OSError:
            self.width = 100

    # -- Theme-treue rich-Console, die in den Puffer schreibt --------------
    def _text_width(self) -> int:
        """Nutzbare Breite des Verlaufsbereichs: Terminal MINUS Scrollbalken.

        Wird bei jedem Rendern neu bestimmt. Vorher stand die Breite einmal beim
        Start fest – nach dem Ziehen am Fenster rechnete rich dann dauerhaft mit
        der alten Breite (Ränder verrutschen, Zeilen brechen falsch um).
        """
        try:
            cols = self.app.output.get_size().columns
        except Exception:
            cols = self.width
        return max(40, cols - 1)        # 1 Spalte gehört dem Scrollbalken

    def _make_console(self) -> Console:
        return Console(file=self._sink, force_terminal=True,
                       color_system="truecolor", width=self._text_width(),
                       theme=ui._build_theme(ui.current_theme()),
                       soft_wrap=False)

    def _render_ansi(self, renderable) -> str:
        sio = io.StringIO()
        c = Console(file=sio, force_terminal=True, color_system="truecolor",
                    width=self._text_width(), theme=ui._build_theme(ui.current_theme()),
                    soft_wrap=False)
        c.print(renderable)
        return sio.getvalue()

    # -- Installation: ui umlenken ----------------------------------------
    # -- Helfer-Agenten: Zähler + Puls --------------------------------------
    def set_helfer(self, n: int) -> None:
        """Von subagents gerufen, wenn ein Helfer startet oder fertig ist.
        Solange welche laufen, blinkt der Punkt in der Kopfzeile (halbe Sekunde)."""
        self.helfer_aktiv = max(0, n)
        if self.helfer_aktiv > 0 and (self._pulse_task is None or self._pulse_task.done()):
            try:
                self._pulse_task = asyncio.get_running_loop().create_task(self._pulse())
            except RuntimeError:
                self._pulse_task = None
        self.invalidate()

    async def _pulse(self) -> None:
        while self.helfer_aktiv > 0:
            self._pulse_on = not self._pulse_on
            self.invalidate()
            await asyncio.sleep(0.5)
        self._pulse_on = True
        self.invalidate()

    def _helfer_fragments(self, muted: str, accent: str) -> list:
        """Kopfzeilen-Stück „· ● Subagenten aktiv: n" (leer, wenn keiner läuft)."""
        n = self.helfer_aktiv
        if n <= 0:
            return []
        p = ui.THEMES[ui.current_theme()]
        punkt = (f"fg:{p['accent']} bg:{_COMPOSER_BG} bold" if self._pulse_on
                 else f"fg:{p['accent_dim']} bg:{_COMPOSER_BG}")
        return [(muted, "· "), (punkt, "●"), (accent, f" Subagenten aktiv: {n}"), (muted, " ")]

    def install(self) -> None:
        self._old_console = ui.console
        ui.console = self._make_console()
        ui._theme_pushed = False        # Theme ist Basis der neuen Console
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

    # -- Reflow-Blöcke (Banner & Co) --------------------------------------
    def _render_block_lines(self, block: _ReflowBlock) -> list[_ReflowLine]:
        """Rendert den Block in der aktuellen Breite zu Verlaufszeilen."""
        block.width = self._text_width()
        text = terminal_safe(self._render_ansi(block.make()))
        lines = text.split("\n")
        if lines and lines[-1] == "":          # rich schließt mit Zeilenumbruch ab
            lines.pop()
        return [_ReflowLine(l, block) for l in lines]

    def print_reflow(self, make) -> None:
        """Hängt ein Renderable an den Verlauf, das bei Breitenänderung neu
        gerendert wird. `make` ist eine Fabrik, damit der Block jedes Mal frisch
        in der dann gültigen Konsolenbreite entsteht."""
        block = _ReflowBlock(make)
        self.transcript.extend(self._render_block_lines(block))
        if len(self.transcript) > _MAX_LINES:
            del self.transcript[:-_MAX_LINES]
        self.invalidate()

    def _reflow(self, width: int) -> None:
        """Alle Reflow-Blöcke im Verlauf neu rendern, deren letzte Render-Breite
        nicht `width` ist. Ersetzt wird über die Block-Referenz der Zeilen (Start
        bis Ende des Blocks), nicht über eine feste Zeilenzahl – die Höhe kann
        sich mit der Breite ändern."""
        if not any(isinstance(l, _ReflowLine) for l in self.transcript):
            return
        neu: list[str] = []
        done: set[int] = set()
        for line in self.transcript:
            block = getattr(line, "block", None)
            if block is None:
                neu.append(line)
                continue
            if id(block) in done:
                continue                       # Rest des Blocks: schon ersetzt
            done.add(id(block))
            if block.width == width:
                # Unverändert: alle Zeilen dieses Blocks 1:1 übernehmen
                neu.extend(l for l in self.transcript
                           if getattr(l, "block", None) is block)
            else:
                neu.extend(self._render_block_lines(block))
        self.transcript = neu

    # -- Live-Region (für converse) ---------------------------------------
    def live_update(self, renderable) -> None:
        self.live_text = terminal_safe(self._render_ansi(renderable))
        self.invalidate()

    def live_clear(self) -> None:
        self.live_text = ""
        self.invalidate()

    # -- vom Chat-Loop genutzt --------------------------------------------
    def consume_abort(self) -> bool:
        if self._abort:
            self._abort = False
            return True
        return False

    def clear(self) -> None:
        self.transcript.clear()
        self.live_text = ""
        self._last_thinking = None
        self._thinking_live = False
        self.invalidate()

    def set_thinking(self, text: str, seconds: float | None, live: bool) -> None:
        if text:
            self._last_thinking = {"text": text, "seconds": seconds}
        self._thinking_live = live and bool(text)

    def _thinking_height(self) -> int:
        try:
            rows = self.app.output.get_size().rows
        except Exception:
            rows = 40
        return max(1, min(12, rows - _CHROME_ROWS - 3))

    def _review_height(self) -> int:
        try:
            rows = self.app.output.get_size().rows
        except Exception:
            rows = 40
        return max(1, rows - _CHROME_ROWS - 3)

    def _toggle_thinking(self) -> None:
        self._last_input = time.monotonic()
        if self._modal is not None:
            if self._modal["kind"] == "thinking":
                self._modal_resolve("")
            return
        if self._thinking_live and self.busy:
            self.thinking_expanded = not self.thinking_expanded
        elif self._last_thinking:
            self._thinking_buffer.set_document(
                Document(terminal_safe(self._last_thinking["text"]), cursor_position=0),
                bypass_readonly=True)
            self._modal = {"kind": "thinking"}
            self.app.layout.focus(self._thinking_win)
        self.invalidate()

    def _mark_dirty(self, _app=None) -> None:
        """Vor jedem Zeichnen: Ist der Verlauf verrutscht (neue Zeilen, Scroll,
        andere Live-Höhe, Resize)? Dann soll prompt_toolkit diesmal JEDE Zelle
        neu schreiben statt nur die vermeintlich geänderten – Sicherheitsnetz
        gegen Geister-Zeichen, egal welches Zeichen den Versatz verursacht hat.
        Beim reinen Streaming (nichts verrutscht) bleibt der schnelle Diff."""
        live_rows = self.live_text.count("\n") + 1 if self.live_text else 0
        key = (len(self.transcript), self.scroll, live_rows, self._text_width())
        if key == self._layout_key:
            return
        self._layout_key = key
        try:
            r = self.app.renderer
            last = r._last_screen
            if last is not None:
                r._last_screen = _dirty_screen(last.height, last.width)
        except Exception:
            pass

    def invalidate(self) -> None:
        if self.app is not None:
            try:
                self.app.invalidate()
            except Exception:
                pass

    def force_repaint(self) -> None:
        """Zeichnet den ganzen Bildschirm neu (überschreibt Fremd-Ausgabe, die
        ein Befehl evtl. direkt auf die Konsole gemalt hat – kein 'Bleeding')."""
        if self.app is not None:
            try:
                self.app.renderer.clear()
            except Exception:
                pass
            self.invalidate()

    # -- Eingabe: wächst mit dem Text mit ---------------------------------
    def _prefix_width(self) -> int:
        """Feste Einrückung; die Ordner-Anzeige sitzt separat im Rahmen."""
        return 3

    def _line_prefix(self, lineno, wrap):
        """Ein ruhiger Schreibanfang, auch bei mehrzeiligen Nachrichten."""
        if lineno == 0 and not wrap:
            accent = ui.THEMES[ui.current_theme()]["accent"]
            return [(f"fg:{accent} bold", " ❯ ")]
        return [("", " " * self._prefix_width())]

    def _terminal_width(self) -> int:
        try:
            return max(1, self.app.output.get_size().columns)
        except Exception:
            return self.width

    def _input_width(self) -> int:
        # Zwei Rahmenkanten, ein Abstand rechts und der feste Schreibanfang.
        return max(1, self._terminal_width() - 3 - self._prefix_width())

    def _border_style(self) -> str:
        p = ui.THEMES[ui.current_theme()]
        color = p["accent_dim"] if self.busy else p["brand_dim"]
        return f"fg:{color} bg:{_COMPOSER_BG}"

    def _composer_header(self):
        width = self._terminal_width()
        p = ui.THEMES[ui.current_theme()]
        border = self._border_style()
        muted = f"fg:#9298ac bg:{_COMPOSER_BG}"
        accent = f"fg:{p['accent']} bg:{_COMPOSER_BG}"
        status = "● bereit"
        if self._modal is not None:
            status = {"select": "◇ Auswahl", "text": "◇ Eingabe",
                      "thinking": "◇ Denktext", "review": "◇ Aktion prüfen"}[self._modal["kind"]]
        elif self._practicing:
            status = "◇ beendet Übung …" if self._practice_stopping else "◇ übt"
        elif self.busy:
            status = "◌ arbeitet"
        left = [(border, "╭─"), (accent + " bold", f" {ui.persona_name()} "),
                (muted, "· "), (accent, status), (border, " ")]
        left += self._helfer_fragments(muted, accent)
        right = list(mascot.rprompt()) + [(border, "─╮")]
        if width >= 70 and self._modal is None:
            badge = list(mascot.workspace_badge(width=max(20, width // 2))) or                 list(mascot.folder_badge(compact=width < 105))
            candidate = [(muted, " ")] + badge + [(muted, "  ")] + right
            if fragment_list_width(left + candidate) + 3 <= width:
                right = candidate
        if fragment_list_width(left + right) > width:
            right = [(border, "╮")]
            left = ui.fit_fragments(left, max(0, width - 1))
        gap = max(0, width - fragment_list_width(left + right))
        return left + [(border, "─" * gap)] + right

    def _composer_bottom(self):
        width = self._terminal_width()
        return [(self._border_style(), "╰" + "─" * max(0, width - 2) + "╯")]

    def _input_height(self) -> int:
        """Wie viele Zeilen braucht die Eingabe gerade?
        _MIN_INPUT_ROWS bis _MAX_INPUT_ROWS.

        Die Mindesthöhe gibt beim Tippen Luft: früher klebte die eine Eingabe-
        zeile direkt zwischen Trennlinie und Statusleiste – das wirkte eng.
        """
        width = self._input_width()
        rows = 0
        for line in (self.buffer.text or "").split("\n"):
            cells = get_cwidth(line.expandtabs(4)) + 1    # inklusive Cursor-Zelle
            rows += max(1, -(-cells // width))
        return max(_MIN_INPUT_ROWS, min(_MAX_INPUT_ROWS, rows))

    # -- sichtbarer Ausschnitt (unten verankert) --------------------------
    def _visible(self):
        try:
            rows = self.app.output.get_size().rows
        except Exception:
            rows = 40
        editor_rows = self._input_height()
        if self._modal is not None:
            if self._modal["kind"] == "thinking":
                editor_rows = self._thinking_height()
            elif self._modal["kind"] == "review":
                editor_rows = self._review_height()
            else:
                editor_rows = (len(self._modal["options"]) + 2
                               if self._modal["kind"] == "select" else 1)
        out_rows = max(1, rows - _CHROME_ROWS - editor_rows)

        # Fenstergröße geändert? Dann muss auch die rich-Console mitwachsen,
        # sonst rendert sie neue Ausgaben weiter in der alten Breite.
        breite = self._text_width()
        if breite != self.width:
            self.width = breite
            try:
                ui.console.width = breite
            except Exception:
                pass
            self._reflow(breite)     # Banner & Co in der neuen Breite

        self._out_rows = out_rows
        lines = self.transcript[:]
        if self.live_text:
            lines.extend(self.live_text.split("\n"))
        total = len(lines)
        max_scroll = max(0, total - out_rows)
        if self.scroll > max_scroll:
            self.scroll = max_scroll
        if self.scroll < 0:
            self.scroll = 0
        if self.scroll > 0:
            # eine Zeile für den Hinweis reservieren, sichtbare Höhe bleibt gleich
            body_rows = max(1, out_rows - 1)
            end = total - self.scroll
            start = max(0, end - body_rows)
            tail = lines[start:end]
            tail.append(f"\x1b[2m  ↑ {self.scroll} Zeilen hoch · Ende / Strg+Ende springt nach unten\x1b[0m")
        else:
            tail = lines[total - out_rows:] if total > out_rows else lines
        return ANSI("\n".join(self._pad(l) for l in tail))

    def _pad(self, line: str) -> str:
        """Zeile mit Leerzeichen bis zum rechten Rand auffüllen.

        Klingt nach Kosmetik, ist aber das Gegenmittel gegen die verstreuten
        Zeichen-Reste: prompt_toolkit zeichnet nur Zellen neu, die sich in SEINER
        Bildschirm-Kopie geändert haben. Alles, was ein anderer Prozess direkt
        aufs Terminal geschrieben hat (tqdm-Balken & Co), steht deshalb ewig
        herum. Eine bis zum Rand gefüllte Zeile überschreibt es beim nächsten
        Bild von selbst.
        """
        try:
            breite = fragment_list_width(to_formatted_text(ANSI(line)))
        except Exception:
            return line
        fehlt = self._text_width() - breite
        return line + " " * fehlt if fehlt > 0 else line

    def _scrollbar(self):
        """Eigener, sichtbarer Scrollbalken rechts neben dem Verlauf."""
        rows = self._out_rows
        total = len(self.transcript) + (self.live_text.count("\n") + 1 if self.live_text else 0)
        accent = ui.THEMES[ui.current_theme()]["accent"]
        if total <= rows:
            return [("fg:#2a2a36", "│\n") for _ in range(rows)]
        max_scroll = max(1, total - rows)
        thumb = max(1, round(rows * rows / total))
        # scroll=0 -> Daumen ganz unten; scroll=max -> ganz oben
        frac_from_top = (max_scroll - min(self.scroll, max_scroll)) / max_scroll
        top = round(frac_from_top * (rows - thumb))
        out = []
        for i in range(rows):
            if top <= i < top + thumb:
                out.append((f"fg:{accent}", "█\n"))
            else:
                out.append(("fg:#2a2a36", "│\n"))
        return out

    def _scroll_by(self, delta: int) -> None:
        """delta>0 = nach oben (in den Verlauf), delta<0 = nach unten."""
        total = len(self.transcript) + (self.live_text.count("\n") + 1 if self.live_text else 0)
        max_scroll = max(0, total - self._out_rows)
        self.scroll = min(max_scroll, max(0, self.scroll + delta))
        self.invalidate()

    def _scroll_to_bottom(self) -> None:
        self.scroll = 0
        self.invalidate()

    def _echo_user(self, text: str) -> None:
        from rich.text import Text
        ui.console.print(Text(f"  ❯ {text}", style="user"))

    # -- Eingabe abschicken ------------------------------------------------
    def _submit(self) -> None:
        self._last_input = time.monotonic()
        text = self.buffer.text
        if self.busy:
            return                      # läuft noch eine Antwort -> ignorieren
        self.buffer.reset()
        if not text.strip():
            return
        self.scroll = 0                 # beim Absenden ans untere Ende springen
        self._echo_user(text)
        self.busy = True
        self.invalidate()

        async def _runner():
            result = None
            try:
                result = await self.handler(self.ctx, text)
            except Exception as e:
                ui.error(f"Fehler: {e}")
            finally:
                self.busy = self._practicing
                self._last_input = time.monotonic()   # Leerlauf-Uhr neu starten
                self.force_repaint()      # evtl. Fremd-Ausgabe vom Schirm wischen
            if result == "exit" and self.app is not None:
                self.app.exit()

        self.app.create_background_task(_runner())

    # -- Übungsmodus (Leerlauf) -------------------------------------------
    def _on_text_changed(self, _buffer) -> None:
        """Tippt der Nutzer, gilt das als Aktivität – und stoppt eine Übung sofort."""
        self._last_input = time.monotonic()
        self._stop_practice()

    def _stop_practice(self) -> bool:
        """Bricht eine laufende Übungsrunde ab. True, wenn eine lief."""
        if not self._practicing:
            return False
        if self._practice_stopping:
            return True
        self._practice_stopping = True
        if self._practice_task is not None and not self._practice_task.done():
            self._practice_task.cancel()
        self.invalidate()
        return True

    async def _idle_loop(self) -> None:
        """Wacht über den Leerlauf: nach ~10 min Ruhe eine Übungsrunde anstoßen."""
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
        """Startet den Übungsmodus – bei Cloud-Modellen erst nach Kosten-Nachfrage."""
        prov = M.split_ref(self.ctx.model or "")[0]
        is_cloud = prov not in ("local", "ollama", "")
        if is_cloud and self._practice_consent is None:
            choice = await self.select(
                "⚠ Übungsmodus mit CLOUD-Modell starten? Das verbraucht laufend "
                "Tokens und kann ins Geld gehen.",
                [("ja",   "Ja, mit Cloud-Modell üben (kostet)"),
                 ("nein", "Nein – Übungsmodus aus")])
            self._practice_consent = (choice == "ja")
            self._last_input = time.monotonic()       # nach dem Dialog Uhr zurücksetzen
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
        self.invalidate()

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
                    for _ in range(15):                # kleine Pause zwischen Runden
                        if should_stop():
                            break
                        await asyncio.sleep(0.2)
                return result
            except asyncio.CancelledError:
                # practice_fn kehrt bei Cancel erst nach dem Prozess-Cleanup zurück.
                return "abgebrochen"
            except Exception as e:
                ui.error(f"Übungsmodus-Fehler: {e}")
                return "fehler"
            finally:
                release()

        # Eigener Task: ein App-Shutdown darf nicht gleichzeitig Eltern- und
        # Übungs-Task mehrfach canceln, während der Prozess noch aufgeräumt wird.
        self._practice_task = asyncio.create_task(_runner())
        self._practice_task.add_done_callback(
            lambda task: release() if self._practice_task is task else None)
        return self._practice_task

    async def run_practice_once(self, ctx, practice_fn):
        """Manuelle Runde mit derselben Abbruch- und Cleanup-Sperre wie Auto-Üben."""
        if self._practicing:
            return "abgebrochen"
        task = self._launch_practice(ctx, practice_fn, repeat=False)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():       # Abbruch noch vor dem ersten Coroutine-Schritt
                return "abgebrochen"
            self._stop_practice()
            await asyncio.shield(task)
            raise

    # -- Modale Menüs / Eingaben (statt verschachtelter Apps) -------------
    async def review_action(self, title: str, preview: str) -> bool:
        """Vollständige Aktionsvorschau; nur F8 gibt diese einzelne Aktion frei."""
        if self._modal is not None and self._modal["kind"] == "thinking":
            self._modal_resolve("")
        if self._modal is not None:
            raise RuntimeError("Eine andere Rückfrage ist bereits geöffnet.")
        future = asyncio.get_running_loop().create_future()
        self._review_buffer.set_document(
            Document(f"{title}\n\n{preview}", cursor_position=0), bypass_readonly=True)
        modal = {"kind": "review", "future": future}
        self._modal = modal
        self.app.layout.focus(self._review_win)
        self.invalidate()
        try:
            return (await future) is True
        finally:
            if self._modal is modal:
                self._modal = None
                self.app.layout.focus(self._input_win)
            self._last_input = time.monotonic()
            self.invalidate()

    async def select(self, question, options):
        """Auswahl-Menü direkt in der Vollbild-App (ersetzt confirm.ask im TUI)."""
        fut = asyncio.get_event_loop().create_future()
        self._modal = {"kind": "select", "q": question,
                       "options": list(options), "sel": 0, "future": fut}
        if self.app is not None:
            self.app.layout.focus(self._out_win)
        self.invalidate()
        try:
            return await fut
        finally:
            self._modal = None
            if self.app is not None:
                self.app.layout.focus(self._input_win)
            self.invalidate()

    async def text_input(self, question, password=False):
        """Texteingabe (z.B. API-Key) direkt in der Vollbild-App."""
        fut = asyncio.get_event_loop().create_future()
        self._modal_buffer.reset()
        self._modal = {"kind": "text", "q": question,
                       "password": password, "future": fut}
        if self.app is not None:
            self.app.layout.focus(self._modal_text_win)
        self.invalidate()
        try:
            return await fut
        finally:
            self._modal = None
            if self.app is not None:
                self.app.layout.focus(self._input_win)
            self.invalidate()

    def _modal_resolve(self, value) -> None:
        m = self._modal
        if m and m["kind"] == "thinking":
            self._modal = None
            self.app.layout.focus(self._input_win)
            self.invalidate()
            return
        if m and not m["future"].done():
            m["future"].set_result(value)

    def _render_modal_select(self):
        m = self._modal
        if not m or m["kind"] != "select":
            return []
        accent = ui.THEMES[ui.current_theme()]["accent"]
        frags = [("bold", f"  {m['q']}\n")]
        for i, (_val, label) in enumerate(m["options"]):
            if i == m["sel"]:
                frags.append((f"fg:{accent} bold", f"  ❯ {i + 1}. {label}\n"))
            else:
                frags.append(("fg:#7d8590", f"    {i + 1}. {label}\n"))
        return frags

    def _render_modal_question(self):
        m = self._modal
        if not m or m["kind"] != "text":
            return []
        accent = ui.THEMES[ui.current_theme()]["accent"]
        return [(f"fg:{accent} bold", f"  {m['q']} ")]

    # -- Layout + Tastatur -------------------------------------------------
    def _build_app(self) -> Application:
        completer = SlashCompleter(
            get_themes=lambda: ui.THEMES,
            get_models=self._model_completions,
            get_strengths=lambda: M.strength_menu(self.session_state.get("model")),
            get_chats=chatstore.chat_args,
            get_checkpoints=_checkpoint_menu,
            get_personas=_persona_menu,
        )
        # multiline=True erlaubt Zeilenumbrüche im Text (Alt+Enter, siehe unten).
        # Enter schickt trotzdem ab – dafür sorgt die eigene Tastenbelegung.
        self.buffer = Buffer(completer=completer, complete_while_typing=True,
                             multiline=True, on_text_changed=self._on_text_changed)

        out_win = _ScrollWindow(self, content=FormattedTextControl(self._visible),
                                wrap_lines=False)
        scrollbar_win = _ScrollWindow(self, content=FormattedTextControl(self._scrollbar),
                                      width=1)

        def _toolbar():
            # In die ECHTE SESSION schreiben (nicht in eine Kopie), damit converse
            # mit dem richtigen Modell rechnet (Kontext-Größe & Kosten).
            if self.ctx is not None:
                self.ctx.sync_session()
            return ui.bottom_toolbar(self.session_state, width=self._terminal_width())

        # Die Eingabe wächst mit dem Text (bis _MAX_INPUT_ROWS) und bricht um,
        # statt seitlich wegzuscrollen – so sieht man, was man geschrieben hat.
        input_win = Window(
            BufferControl(buffer=self.buffer),
            get_line_prefix=self._line_prefix,
            height=self._input_height, wrap_lines=True,
            style=f"fg:#e4e7f2 bg:{_COMPOSER_BG}",
        )
        header_win = Window(FormattedTextControl(self._composer_header), height=1,
                            style=f"bg:{_COMPOSER_BG}")
        bottom_win = Window(FormattedTextControl(self._composer_bottom), height=1)
        toolbar_win = Window(FormattedTextControl(_toolbar), height=1,
                             style=f"bg:{_COMPOSER_BG}")
        hints_win = Window(FormattedTextControl(lambda: ui.input_hints(
            self._terminal_width(), busy=self.busy, practicing=self._practicing,
            modal=self._modal is not None, has_thinking=bool(self._last_thinking),
            reading_thinking=bool(self._modal and self._modal["kind"] == "thinking"),
            reviewing_action=bool(self._modal and self._modal["kind"] == "review"))),
            height=1, style=f"bg:{_COMPOSER_BG}")
        self._out_win = out_win
        self._input_win = input_win

        # --- Modal (Menü / Texteingabe) statt verschachtelter App ---
        has_modal = Condition(lambda: self._modal is not None)
        is_select = Condition(lambda: self._modal is not None and self._modal["kind"] == "select")
        is_text = Condition(lambda: self._modal is not None and self._modal["kind"] == "text")
        is_thinking = Condition(lambda: self._modal is not None
                                and self._modal["kind"] == "thinking")
        is_review = Condition(lambda: self._modal is not None
                              and self._modal["kind"] == "review")
        is_reader = is_thinking | is_review

        self._modal_buffer = Buffer(multiline=False)
        modal_select = ConditionalContainer(
            Window(FormattedTextControl(self._render_modal_select), dont_extend_height=True),
            filter=is_select)
        modal_q_win = Window(FormattedTextControl(self._render_modal_question),
                             height=1, dont_extend_width=True)
        self._modal_text_win = Window(
            BufferControl(buffer=self._modal_buffer,
                          input_processors=[ConditionalProcessor(
                              PasswordProcessor(),
                              Condition(lambda: bool(self._modal) and self._modal.get("password")))]),
            height=1)
        modal_text = ConditionalContainer(
            VSplit([modal_q_win, self._modal_text_win]), filter=is_text)
        self._thinking_buffer = Buffer(read_only=True)
        self._thinking_win = Window(
            BufferControl(buffer=self._thinking_buffer), wrap_lines=True,
            height=self._thinking_height, get_line_prefix=lambda _n, _w: [("", " ")],
            style="fg:#b6bed3")
        modal_thinking = ConditionalContainer(self._thinking_win, filter=is_thinking)
        self._review_buffer = Buffer(read_only=True)
        self._review_win = Window(
            BufferControl(buffer=self._review_buffer), wrap_lines=True,
            height=self._review_height, get_line_prefix=lambda _n, _w: [("", " ")],
            style="fg:#d9deec")
        modal_review = ConditionalContainer(self._review_win, filter=is_review)

        editor = VSplit([
            Window(width=1, char="│", style=self._border_style),
            HSplit([
                ConditionalContainer(input_win, filter=~has_modal),
                modal_select,
                modal_text,
                modal_thinking,
                modal_review,
            ], style=f"bg:{_COMPOSER_BG}"),
            Window(width=1, style=f"bg:{_COMPOSER_BG}"),
            Window(width=1, char="│", style=self._border_style),
        ])

        root = FloatContainer(
            content=HSplit([
                VSplit([out_win, scrollbar_win]),
                header_win,
                editor,
                bottom_win,
                toolbar_win,
                hints_win,
            ]),
            floats=[Float(xcursor=True, ycursor=True,
                          content=CompletionsMenu(max_height=8, scroll_offset=1))],
        )

        kb = KeyBindings()

        @kb.add("f8", filter=is_review, eager=True)
        def _(event):
            self._modal_resolve(True)

        @kb.add("escape", filter=is_review, eager=True)
        def _(event):
            self._modal_resolve(False)

        @kb.add("enter", filter=is_review, eager=True)
        @kb.add("c-j", filter=is_review, eager=True)
        def _(event):
            # Enter bleibt eine Lesetaste und kann niemals die Aktion freigeben.
            self._review_buffer.cursor_down()

        @kb.add("home", filter=is_review, eager=True)
        @kb.add("c-home", filter=is_review, eager=True)
        def _(event):
            self._review_buffer.cursor_position = 0

        @kb.add("end", filter=is_review, eager=True)
        @kb.add("c-end", filter=is_review, eager=True)
        def _(event):
            self._review_buffer.cursor_position = len(self._review_buffer.text)

        @kb.add("f2", eager=True)
        def _(event):
            self._toggle_thinking()

        @kb.add("f12", eager=True)
        def _(event):
            """F12: ganzes Gespraech als Markdown sichern (mit Denktext)."""
            import mitschrift
            try:
                ziel = mitschrift.speichern()
            except ValueError as e:
                ui.warn(f"F12: {e}")
            except OSError as e:
                ui.error(f"F12: konnte nicht schreiben - {e}")
            else:
                ui.success(f"F12 - Gespraech gesichert: {ziel.name} "
                           f"({mitschrift.anzahl()} Eintraege, mit Denktext)")
                ui.info(f"Liegt in: {ziel.parent}")
            self.invalidate()

        @kb.add("s-tab", eager=True, filter=~has_modal)
        def _(event):
            import modes
            key = modes.cycle()
            ui.info(f"Modus: {modes.label()}" + (
                f"  ·  Zünder: nach {modes.AUTO_FUSE_S // 60} min zurück in Chatten"
                if key == "auto" else ""))
            self.invalidate()

        @kb.add("escape", filter=is_thinking, eager=True)
        @kb.add("enter", filter=is_thinking, eager=True)
        def _(event):
            self._modal_resolve("")

        # --- Modal-Tasten (nur aktiv, wenn ein Menü/Eingabe offen ist) ---
        @kb.add("up", filter=is_select, eager=True)
        def _(event):
            self._modal["sel"] = (self._modal["sel"] - 1) % len(self._modal["options"])

        @kb.add("down", filter=is_select, eager=True)
        @kb.add("tab", filter=is_select, eager=True)
        def _(event):
            self._modal["sel"] = (self._modal["sel"] + 1) % len(self._modal["options"])

        @kb.add("enter", filter=is_select, eager=True)
        def _(event):
            self._modal_resolve(self._modal["options"][self._modal["sel"]][0])

        @kb.add("escape", filter=is_select, eager=True)
        def _(event):
            self._modal_resolve(self._modal["options"][-1][0])

        for _n in range(1, 10):
            @kb.add(str(_n), filter=is_select, eager=True)
            def _(event, _n=_n):
                opts = self._modal["options"]
                if _n - 1 < len(opts):
                    self._modal_resolve(opts[_n - 1][0])

        @kb.add("enter", filter=is_text, eager=True)
        def _(event):
            self._modal_resolve(self._modal_buffer.text)

        @kb.add("escape", filter=is_text, eager=True)
        def _(event):
            self._modal_resolve("")

        # --- normale Eingabe (nur ohne Modal) ---
        # Strg+J macht einen Zeilenumbruch, statt abzuschicken.
        # BEWUSST nicht Alt+Enter: "escape" ist weiter unten eager auf Abbrechen
        # gelegt und würde die Escape-Enter-Folge abfangen, bevor sie fertig ist.
        # Enter selbst sendet c-m, deshalb ist c-j sauber unterscheidbar.
        @kb.add("c-j", eager=True, filter=~has_modal)
        def _(event):
            self._last_input = time.monotonic()
            self.buffer.insert_text("\n")

        @kb.add("enter", eager=True, filter=~has_modal)
        def _(event):
            self._submit()

        @kb.add("c-c")
        def _(event):
            if self._modal is not None:
                self._modal_resolve(self._modal["options"][-1][0] if self._modal["kind"] == "select" else "")
            elif self._practicing:
                self._stop_practice()
            elif self.busy:
                self._abort = True
            else:
                event.app.exit()

        @kb.add("escape", eager=True, filter=~has_modal)
        def _(event):
            self._last_input = time.monotonic()
            if self._practicing:
                self._stop_practice()
            elif self.busy:
                self._abort = True

        @kb.add("c-d")
        def _(event):
            event.app.exit()

        # --- Verlauf scrollen (eager = Vorrang vor Standard-Bindings) ---
        @kb.add("pageup", eager=True, filter=~is_reader)
        def _(event):
            self._scroll_by(max(1, self._out_rows - 1))

        @kb.add("pagedown", eager=True, filter=~is_reader)
        def _(event):
            self._scroll_by(-max(1, self._out_rows - 1))

        @kb.add("c-up", eager=True, filter=~is_reader)
        def _(event):
            self._scroll_by(1)

        @kb.add("c-down", eager=True, filter=~is_reader)
        def _(event):
            self._scroll_by(-1)

        @kb.add("c-end", eager=True, filter=~is_reader)
        def _(event):
            self._scroll_to_bottom()

        @kb.add(Keys.ScrollUp, eager=True, filter=~is_reader)
        def _(event):
            self._scroll_by(3)

        @kb.add(Keys.ScrollDown, eager=True, filter=~is_reader)
        def _(event):
            self._scroll_by(-3)

        return Application(
            layout=Layout(root, focused_element=input_win),
            key_bindings=merge_key_bindings([load_key_bindings(), kb]),
            style=DynamicStyle(lambda: ui.PROMPT_STYLE),
            full_screen=True,
            mouse_support=True,
            before_render=self._mark_dirty,
        )

    def _model_completions(self) -> dict:
        return self._model_completions_fn()

    async def _ticker(self) -> None:
        """Hält Animation (ML-Balken, Maskottchen) + Live-Anzeige in Bewegung."""
        import modes
        while True:
            await asyncio.sleep(0.2)
            if modes.check_fuse():                 # Auto-Zünder abgelaufen
                ui.warn(modes.fuse_message())
            self.invalidate()

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
        self.app = self._build_app()
        ticker = asyncio.create_task(self._ticker())
        idle = asyncio.create_task(self._idle_loop())
        try:
            await self.app.run_async()
        finally:
            ticker.cancel()
            idle.cancel()
            practice_task = self._practice_task
            if practice_task is not None:
                self._stop_practice()
                await asyncio.shield(practice_task)
            maus_tracking_aus()      # Maus-Modus nicht im Terminal hängen lassen
