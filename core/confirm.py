"""
confirm.py - Ein schönes Auswahl-Menü (wie bei Claude Code).

Pfeiltasten ↑/↓ oder Zahlen 1/2/3 zum Wählen, Enter zum Bestätigen,
Esc = sichere Option (die letzte). Wird inline angezeigt und danach wieder
eingeklappt.
"""

from __future__ import annotations

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style as PTStyle

import ui


def _tui_active():
    """Läuft gerade das Vollbild-TUI? Dann nutzen wir dessen In-App-Modal,
    statt eine zweite (verschachtelte) prompt_toolkit-App zu starten."""
    tui = ui.TUI
    if tui is not None and getattr(tui, "app", None) is not None and tui.app.is_running:
        return tui
    return None


def _review_app(question: str, preview: str):
    """Eigenständiger Vollbild-Leser für die klassische CLI; keine Textkürzung."""
    buffer = Buffer(document=Document(f"{question}\n\n{preview}", cursor_position=0),
                    read_only=True)
    reader = Window(BufferControl(buffer=buffer), wrap_lines=True,
                    get_line_prefix=lambda _n, _w: [("", " ")])
    bindings = KeyBindings()

    @bindings.add("f8", eager=True)
    def _approve(event):
        event.app.exit(result=True)

    @bindings.add("escape", eager=True)
    @bindings.add("c-c", eager=True)
    @bindings.add("c-d", eager=True)
    def _reject(event):
        event.app.exit(result=False)

    @bindings.add("enter", eager=True)
    @bindings.add("c-j", eager=True)
    def _read(event):
        buffer.cursor_down()

    @bindings.add("home", eager=True)
    @bindings.add("c-home", eager=True)
    def _top(event):
        buffer.cursor_position = 0

    @bindings.add("end", eager=True)
    @bindings.add("c-end", eager=True)
    def _bottom(event):
        buffer.cursor_position = len(buffer.text)

    header = Window(FormattedTextControl("  Aktion prüfen · vollständige Vorschau"),
                    height=1, style="bold")
    footer = Window(FormattedTextControl(lambda: ui.input_hints(
        app.output.get_size().columns, reviewing_action=True)),
        height=1, style="bg:#11131d")
    app = Application(
        layout=Layout(HSplit([header, reader, footer]), focused_element=reader),
        key_bindings=merge_key_bindings([load_key_bindings(), bindings]),
        style=PTStyle.from_dict({"": "fg:#d9deec bg:#11131d"}),
        full_screen=True, mouse_support=True)
    return app


async def review_action(question: str, preview: str) -> bool:
    """Nur ausdrückliches F8 autorisiert die vollständig sichtbare Aktion."""
    tui = _tui_active()
    if tui is not None:
        return await tui.review_action(question, preview)
    try:
        return (await _review_app(question, preview).run_async()) is True
    except (KeyboardInterrupt, EOFError):
        return False


async def ask_text(question: str, password: bool = False) -> str:
    """Fragt eine einzelne Texteingabe ab (z.B. einen API-Key).
    password=True zeigt nur Sternchen. Leer/Esc -> ''. """
    tui = _tui_active()
    if tui is not None:
        return (await tui.text_input(question, password)).strip()

    accent = ui.THEMES[ui.current_theme()]["accent"]
    style = PTStyle.from_dict({"prompt": f"{accent} bold", "": "#c8c4ff"})
    try:
        session = PromptSession()
        return (await session.prompt_async(
            [("class:prompt", f"  {question} ")],
            is_password=password, style=style)).strip()
    except (KeyboardInterrupt, EOFError):
        return ""


async def ask(question: str, options: list[tuple[str, str]]) -> str:
    """
    Zeigt eine Frage mit auswählbaren Optionen.
    options: Liste von (rückgabewert, anzeigetext). Gibt den gewählten Wert zurück.
    Esc/Ctrl-C wählt die letzte Option (sollte die sichere "Nein"-Variante sein).
    """
    tui = _tui_active()
    if tui is not None:
        return await tui.select(question, options)

    sel = [0]
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(event):
        sel[0] = (sel[0] - 1) % len(options)

    @kb.add("down")
    @kb.add("c-n")
    @kb.add("tab")
    def _down(event):
        sel[0] = (sel[0] + 1) % len(options)

    @kb.add("enter")
    def _enter(event):
        event.app.exit(result=options[sel[0]][0])

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(event):
        event.app.exit(result=options[-1][0])

    # Zahlen-Shortcuts nur 1..9 (sonst werden "11","12"... zu Mehrtasten-Bindings,
    # die das Drücken von "1" verzögern). Längere Listen: per Pfeiltasten wählen.
    for i in range(min(len(options), 9)):
        @kb.add(str(i + 1))
        def _pick(event, i=i):
            event.app.exit(result=options[i][0])

    def render():
        frags = [("class:q", f"  {question}\n")]
        for i, (_val, label) in enumerate(options):
            if i == sel[0]:
                frags.append(("class:sel", f"  ❯ {i + 1}. {label}\n"))
            else:
                frags.append(("class:opt", f"    {i + 1}. {label}\n"))
        return frags

    accent = ui.THEMES[ui.current_theme()]["accent"]
    style = PTStyle.from_dict({
        "q":   "bold",
        "sel": f"{accent} bold",
        "opt": "#7d8590",
    })

    app = Application(
        layout=Layout(HSplit([Window(FormattedTextControl(render), always_hide_cursor=True)])),
        key_bindings=kb,
        style=style,
        full_screen=False,
        mouse_support=False,
    )
    return await app.run_async()


SUCHEN = "__suchen__"
ALLE = "__alle__"


def treffer(optionen: list[tuple[str, str]], begriff: str) -> list[tuple[str, str]]:
    """Einträge, in deren Wert oder Text alle Wörter des Suchbegriffs vorkommen (ohne Groß/klein)."""
    worte = begriff.lower().split()
    return [o for o in optionen if all(w in f"{o[0]} {o[1]}".lower() for w in worte)]


async def ask_suche(question: str, options: list[tuple[str, str]], *, zeigen: int = 30, ab: int = 10,
                    frage_fn=None, text_fn=None) -> str:
    """Wie ask, ab `ab` Einträgen mit „🔍 Suchen“; höchstens `zeigen` Einträge auf einmal.
    Werte mit "__" am Anfang (Abbrechen, Zurück) stehen immer am Ende – Esc wählt weiter den letzten.
    frage_fn/text_fn ersetzen ask/ask_text (Hintergrund-Aufträge, Tests)."""
    frage_fn, text_fn = frage_fn or ask, text_fn or ask_text
    feste = [o for o in options if str(o[0]).startswith("__")]
    eintraege = [o for o in options if not str(o[0]).startswith("__")]
    if len(eintraege) < ab:
        return await frage_fn(question, options)
    liste, frage = eintraege, question
    while True:
        sicht = liste[:zeigen]
        rest = len(liste) - len(sicht)
        kopf = [(SUCHEN, f"🔍  Suchen …  ({len(eintraege)} insgesamt)")]
        if liste is not eintraege:
            kopf.append((ALLE, "↩  Alle zeigen"))
        mehr = f"  ·  {rest} weitere – 🔍 Suchen findet sie" if rest > 0 else ""
        wahl = await frage_fn(frage + mehr, kopf + sicht + feste)
        if wahl == SUCHEN:
            begriff = await text_fn("🔍 Suchen nach:")
            if not begriff:
                continue
            gefunden = treffer(eintraege, begriff)
            if not gefunden:
                ui.warn(f"🔍 Nichts zu „{begriff}“ gefunden.")
                continue
            liste, frage = gefunden, f"{question}  ·  🔍 „{begriff}“: {len(gefunden)} Treffer"
        elif wahl == ALLE:
            liste, frage = eintraege, question
        else:
            return wahl
