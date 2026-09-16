"""
main.py - NemiCLI: ein schöner Chat im Terminal.  Cloud + Lokal.

Normaler Text  -> Gespräch mit dem aktiven Modell.
/befehle       -> Theme, Modell (Cloud/Lokal), Hilfe, beenden ...

Cloud  = Anthropic (braucht ANTHROPIC_API_KEY in .env)
Lokal  = Ollama auf diesem PC (kein Key nötig)
"""

import asyncio
import os
import re
import sys
import time
import webbrowser
from pathlib import Path

# --- Ausgabe hart auf UTF-8 -----------------------------------------------
# Ohne das stirbt NemiCLI am ersten Emoji, sobald die Ausgabe NICHT direkt in
# der Konsole landet (z.B. `nemicli > log.txt` oder in einer Pipe): Windows
# nimmt dann cp1252 und wirft einen UnicodeEncodeError. `errors="replace"`
# ist das Sicherheitsnetz für Terminals, die ein Zeichen wirklich nicht können.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- Bootstrap: die Kategorie-Ordner auf den Suchpfad legen ---------------
# So bleiben alle bestehenden Imports (import ui, import chat, …) gültig,
# obwohl die Module jetzt sauber in Unterordnern liegen.
# Als gepackte exe (PyInstaller) liegen die Module schon flach im Bündel –
# dann darf hier NICHTS an sys.path geschraubt werden.
_ROOT = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    for _sub in ("core", "engines", "tools", "ui"):
        sys.path.insert(0, str(_ROOT / _sub))

import paths                 # weiß, wo gespeichert wird (neben der exe)
paths.ensure_layout()        # fehlende Ordner + LIES-MICH-Dateien anlegen

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.live import Live
from rich.console import Group

import ui
import actions
import vision
import config
import chatstore
import learn
import memory
import indexdb
import emoji
import mascot
import subagents
import models as M
import providers as P
import pricing
import setup as S
import practice as PRAC
import webui
import keyvault
import persoenlichkeiten as PS
import version as VER
import updater
import mitschrift
import anhang
import protokoll
import workspace as WS
import modes
import stats
from commands import SlashCompleter, parse, PERSONA_CMDS
from confirm import ask as ask_confirm, ask_text
from confirm import review_action as review_action_confirm

load_dotenv(paths.INSTALL / ".env")  # gehoert zum Programm, nicht zu den Daten
config.decrypt_env()        # verschlüsselte API-Keys (Windows-Umschlag) nutzbar machen
PS.ensure_dir()             # Persoenlichkeiten/ + LIES-MICH anlegen

try:
    import msvcrt              # Windows: Tastendruck (Esc) abfragen, ohne zu blockieren
except ImportError:
    msvcrt = None

def _modell_sieht(model: str | None) -> bool:
    """True, wenn das Chat-Modell Bilder selbst verarbeiten kann."""
    if not model:
        return False
    prov, mid = M.split_ref(model)
    if prov == "ollama":
        return P.ollama_is_vision(mid) and not P.ist_vision_schwach(mid)
    if prov in ("openai", "google", "anthropic", "openrouter"):
        return True
    # Ollama Cloud & andere OpenAI-kompatible Anbieter: Vision am Namen erkennen
    # (deren API meldet die Fähigkeit nicht). z.B. deepseek-v4.1-flash = multimodal.
    return P.cloud_name_vision(mid)


def _uris_nach_temp(uris: list[str]) -> list:
    """Data-URIs aus der WebUI als Temp-Dateien, damit der Helfer sie lesen kann."""
    import tempfile
    import base64
    from pathlib import Path
    out = []
    for uri in uris:
        if not uri.startswith("data:") or "," not in uri:
            continue
        head, b64 = uri.split(",", 1)
        ext = ".png"
        if "jpeg" in head or "jpg" in head:
            ext = ".jpg"
        elif "webp" in head:
            ext = ".webp"
        fd, name = tempfile.mkstemp(suffix=ext, prefix="nemi_vis_")
        try:
            os.write(fd, base64.b64decode(b64))
        finally:
            os.close(fd)
        out.append(Path(name))
    return out


async def _bilder_fuer_chat(model: str | None, text: str,
                            extra_uris: list[str] | None = None
                            ) -> tuple[str, list[str] | None, str | None]:
    """Hängt Bilder ans Chat-Modell ODER lässt Qwen 4B kurz gucken und entladen.

    Gibt (text, image_uris_oder_None, hinweis) zurück.
    """
    bilder = vision.find_images(text)
    imgs = list(extra_uris or [])
    if _modell_sieht(model):
        if bilder:
            text = vision.strip_paths(text, bilder)
            imgs += [vision.to_data_uri(p) for p in bilder]
        return text, imgs or None, (
            f"🖼 {len(bilder)} Bild(er) angehängt: " + ", ".join(p.name for p in bilder)
            if bilder else None
        )
    pfade = list(bilder)
    tmp: list = []
    if imgs and not pfade:
        tmp = _uris_nach_temp(imgs)
        pfade = tmp
        imgs = []
    if not pfade:
        return text, imgs or None, None
    if bilder:
        text = vision.strip_paths(text, bilder)
    # Frueher sprang hier ein lokaler Qwen-4B-Helfer ueber llama.cpp ein, wenn
    # das Chat-Modell selbst keine Bilder lesen kann. Mit llama.cpp ist der
    # Helfer entfallen (15.09.2026) - jetzt braucht es ein Modell, das sieht.
    for p in tmp:
        try:
            p.unlink()
        except Exception:
            pass
    return text, None, ("Das aktive Modell sieht keine Bilder. Nimm mit /model ein "
                        "Vision-Modell (Cloud oder ein Ollama-Modell mit 👁).")
MAX_STEPS = 12                  # Bremse für Aktions-Ketten pro Nutzer-Nachricht (keine Endlosschleife)
# Lesende Werkzeuge, die mit identischen Feldern nichts Neues bringen: eine
# Wiederholung in derselben Runde wird nicht ausgeführt, sondern nur angemerkt.
# befehl/bild_malen/Helfer bewusst nicht – ein zweites `git status` kann Sinn haben.
_DEDUPE_TOOLS = {"datei_lesen", "ordner_auflisten", "dateien_suchen", "inhalt_suchen",
                 "ordner_erkennen", "web_lesen", "web_wiki", "web_suche", "ml_status"}
_STEP_LIMIT_NOTE = (
    f"\n\n[System] ⏸ Du hast in dieser Runde {MAX_STEPS} Aktionen ausgeführt – das ist die "
    "Obergrenze pro Nachricht. Führe jetzt KEINE weitere Aktion aus. Fasse zusammen, was du "
    "bisher herausgefunden hast, und sag klar, was noch offen ist. Der Nutzer kann mit "
    "„weiter“ die nächste Runde starten."
)
_STEP_LIMIT_WARN = (f"⏸ {MAX_STEPS} Aktionen am Stück – ich halte kurz an, damit nichts endlos "
                    "läuft. Sag „weiter“, dann mache ich da weiter.")
_DUPLICATE_NOTE = ("Diese Aktion hattest du in dieser Runde schon mit genau denselben Angaben "
                   "ausgeführt – das Ergebnis steht oben. Nutze es, statt es erneut abzurufen.")


def _action_key(act: dict) -> str:
    import json
    try:
        return json.dumps(act, sort_keys=True, ensure_ascii=False)
    except Exception:
        return repr(sorted(act.items()))
AUTO_ALLOW: set[str] = set()    # Werkzeuge, die diese Sitzung nicht mehr abgefragt werden

WEB_BRIDGE = None               # aktive WebUI-Brücke (None = kein /webui gestartet)
CHROME_BRIDGE = None            # Empfangsstelle für die Chrome-Erweiterung (127.0.0.1:9000)
# Nur EIN Chat-Zug gleichzeitig – egal ob aus Terminal oder Browser. Schützt den
# gemeinsamen Verlauf (backend.messages) vor Verschachtelung.
TURN_LOCK = asyncio.Lock()

# Sitzungs-Status für die Eingabe-Statusleiste (unten an der Eingabezeile)
#   ctx/ctx_max  -> wie voll das Modell-Gedächtnis ist (Kontext-Balken)
#   cost         -> grob geschätzte Kosten dieser Sitzung in US-Dollar
#   start        -> Startzeit (monotonic) für die Sitzungs-Dauer
SESSION = {"model": "—", "strength": None, "chat": 0, "tokens": 0,
           "ctx": 0, "ctx_max": pricing.DEFAULT_CTX, "cost": 0.0,
           "start": time.monotonic()}


def _esc_pressed() -> bool:
    """True, wenn der Nutzer gerade Esc gedrückt hat (nicht blockierend, nur Windows)."""
    if not msvcrt:
        return False
    hit = False
    while msvcrt.kbhit():            # alle wartenden Tasten leeren
        if msvcrt.getch() in (b"\x1b",):   # Esc
            hit = True
    return hit


def _update_session_meters(in_toks: int, out_toks: int) -> None:
    """Nach einem Zug die Statusleisten-Werte angleichen: Kontext-Füllung
    (aktuelle Eingabe-Größe ≈ wie voll das Gedächtnis ist) und Kosten."""
    ref = SESSION.get("model")
    SESSION["ctx_max"] = pricing.context_window(ref)
    if in_toks:                                  # echte Zahl vom Anbieter
        SESSION["ctx"] = in_toks + out_toks
    SESSION["cost"] += pricing.cost(ref, in_toks, out_toks)


def _render(thinking_txt: str, answer: str, think_secs: float | None = None,
            note: str = "", *, done: bool = False):
    """Antwort-Panel, bei Denk-Modellen mit gedimmtem Denk-Panel darüber.

    Das Denk-Panel klappt sich VON SELBST zu, sobald die eigentliche Antwort
    anfängt – sonst schiebt langes Nachdenken die Antwort vom Bildschirm.

    `note` = eine dezente Info-Zeile GANZ OBEN (z.B. Auto-Stark hat auf das
    große Modell umgeschaltet). Bleibt sichtbar, wird nicht zugeklappt.
    """
    from rich.text import Text
    teile = []
    if note:
        teile.append(Text(note, style="italic #7d8590"))
    if thinking_txt:
        expanded = bool(ui.TUI is not None and ui.TUI.thinking_expanded)
        if ui.TUI is not None:
            ui.TUI.set_thinking(thinking_txt, think_secs, live=not done)
        zu = (done or bool(answer.strip())) and not expanded
        teile.append(ui.thinking_panel(thinking_txt, collapsed=zu, seconds=think_secs,
                                       done=done or bool(answer.strip()), expanded=expanded))
    if answer.strip() or not teile:
        teile.append(ui.assistant_panel(answer))
    return teile[0] if len(teile) == 1 else Group(*teile)


async def _bilder_aus_ergebnis(res, model: str | None):
    """Werkzeug hat Bilder geliefert (bild_ansehen/bildschirm_ansehen): als Data-URIs
    fürs Modell – oder, wenn das Modell nicht sieht, vom Qwen-Helfer beschreiben
    lassen (die Beschreibung kommt dann in den Ergebnistext).
    Gibt (uris_oder_None, ergebnis) zurück – ActionResult ist unveränderlich."""
    import dataclasses
    pfade = [Path(x) for x in (getattr(res, "bilder", None) or []) if Path(x).is_file()]
    if not pfade:
        return None, res
    if _modell_sieht(model):
        return [vision.to_data_uri_small(p) for p in pfade], res
    return None, dataclasses.replace(res, text=res.text + (
        "(Das aktive Modell sieht keine Bilder – nimm mit /model ein Vision-Modell, "
        "dann kann ich es ansehen.)"))


def _result_feedback(tool, result) -> str:
    """Status stammt vom Werkzeug, nie aus dem Inhalt seiner Ausgabe."""
    status = "erfolgreich" if result.ok is True else (
        "fehlgeschlagen" if result.ok is False else "Status ungeprüft")
    code = f", Exitcode {result.returncode}" if result.returncode is not None else ""
    return f"Ergebnis von '{tool}' ({status}{code}):\n{result.text}"


def _finish_record(record, result) -> None:
    record["status"] = "success" if result.ok is True else (
        "failed" if result.ok is False else "unverified")


def _close_records(records) -> None:
    for record in records:
        if record["status"] == "running":
            record["status"] = "unverified"
    try:
        stats.record_tools(records)
    except Exception:
        pass


# --- Helfer-Agenten ---------------------------------------------------------
# Bis zu 5 parallel. Ihre Aktionen laufen über dieselben Regeln wie die des
# Haupt-Agenten (Modus, Bestätigung, AUTO_ALLOW) – nur die Anzeige trägt den
# Helfer-Namen. Damit sich fünf Helfer nicht gleichzeitig mit Rückfragen ins
# Wort fallen, fragt immer nur EINER (Lock); reine Lese-Aktionen laufen parallel.
HELFER_LOCK = asyncio.Lock()
_HELFER_NUR_CLOUD = ("Helfer-Agenten gibt es nur mit einem Cloud-Modell – ein lokales Modell "
                     "hat keinen Platz für einen zweiten Kontext. Mach die Aufgabe selbst, "
                     "Schritt für Schritt.")


def _helfer_executor(records: list, *, zeige_aktion, zeige_ergebnis, frage, pruefe, warne
                     ) -> "subagents.Executor":
    """Baut den Ausführer für Helfer-Aktionen. Die fünf Callbacks kapseln nur die
    Anzeige (Terminal-Panels bzw. WebUI-Ereignisse); die Entscheidungslogik ist
    für beide gleich."""

    async def execute(act: dict, lab: str) -> str:
        tool = act.get("tool")
        desc = f"🤝 {lab}: {actions.describe(act)}"
        urteil = modes.decide(act, actions.needs_confirm(act))
        if urteil == "block":                    # 👁 Nur Lesen: nicht mal fragen
            res = actions.ActionResult(modes.block_message(act), ok=False)
            zeige_aktion(desc, False)
            zeige_ergebnis(res.text, None, None)
            records.append({"tool": tool, "status": "not_run"})
            protokoll.schreibe(tool, actions.describe(act), "blocked",
                               veraendernd=actions.needs_confirm(act), wer=f"Helfer {lab}")
            return _result_feedback(tool, res)
        confirm = urteil == "ask" and tool not in AUTO_ALLOW
        async with HELFER_LOCK:
            zeige_aktion(desc, confirm)
            if confirm:
                try:
                    preview = await asyncio.to_thread(actions.confirmation_preview, act)
                except (ValueError, OSError) as exc:
                    res = actions.ActionResult(f"Vorschau nicht möglich: {exc}", ok=False)
                    zeige_ergebnis(res.text, False, None)
                    records.append({"tool": tool, "status": "not_run"})
                    return _result_feedback(tool, res)
                if preview is not None:
                    choice = await pruefe(f"{lab}: Dateiänderung prüfen", preview)
                else:
                    choice = await frage(
                        f"{lab} möchte das ausführen – wie weiter?",
                        [("yes",    "Ja, ausführen"),
                         ("always", "Ja – solche Aktionen diese Sitzung nicht mehr fragen"),
                         ("no",     "Nein, abbrechen")])
                if choice not in ("yes", "always"):
                    warne(f"{lab}: Aktion abgelehnt.")
                    records.append({"tool": tool, "status": "rejected"})
                    protokoll.schreibe(tool, actions.describe(act), "rejected",
                                       veraendernd=True, wer=f"Helfer {lab}")
                    return (f"Aktion '{tool}' wurde vom Nutzer ABGELEHNT. "
                            "Führe sie nicht aus und vermerke das in deinem Bericht.")
                if choice == "always":
                    AUTO_ALLOW.add(tool)
        record = {"tool": tool, "status": "running"}
        records.append(record)
        res = await actions.run(act)
        _finish_record(record, res)
        zeige_ergebnis(res.text, res.ok, res.returncode)
        protokoll.schreibe(tool, actions.describe(act), record["status"],
                           veraendernd=actions.needs_confirm(act), wer=f"Helfer {lab}")
        return _result_feedback(tool, res)

    return execute


async def _helfer_lauf(act: dict, backend, records: list, execute, zeige_start, zeige_bericht,
                       frage) -> str:
    """Führt die Aktion `subagenten` aus und liefert die Rückmeldung fürs Modell.
    Sicherheitsstufe (/subagenten): off -> gesperrt, an -> Nutzer fragen, auto -> los."""
    helfer = subagents.normalize(act)
    if not helfer:
        return "Keine Helfer angegeben (Feld `helfer`: Liste aus {rolle, aufgabe})."
    if not hasattr(backend, "ask_messages"):
        return _HELFER_NUR_CLOUD
    stufe = subagents.stufe()
    if stufe == "off":
        records.append({"tool": "subagenten", "status": "not_run"})
        return ("Helfer-Agenten sind AUS (der Nutzer hat sie mit /subagenten off gesperrt). "
                "Mach die Aufgabe selbst, Schritt für Schritt.")
    zeige_start(helfer)
    if stufe == "an":
        choice = await frage(
            f"{len(helfer)} Helfer losschicken?",
            [("yes", "Ja, losschicken"),
             ("auto", "Ja – und künftig nicht mehr fragen (/subagenten auto)"),
             ("no", "Nein")])
        if choice == "auto":
            subagents.set_stufe("auto")
        elif choice != "yes":
            records.append({"tool": "subagenten", "status": "rejected"})
            return ("Der Nutzer hat das Losschicken der Helfer ABGELEHNT. "
                    "Mach die Aufgabe selbst oder frag, wie es weitergehen soll.")
    record = {"tool": "subagenten", "status": "running"}
    records.append(record)
    res_list = await subagents.run(helfer, backend, execute)
    record["status"] = "unverified"      # Berichte sind Modelltext; die Aktionen selbst stehen einzeln in records
    teile = []
    for i, (h, bericht) in enumerate(res_list, 1):
        zeige_bericht(i, h, bericht)
        teile.append(f"{subagents.label(i, h)}\nAuftrag: {h['aufgabe']}\nBericht:\n{bericht}")
    return "Berichte der Helfer-Agenten:\n\n" + "\n\n".join(teile)


def _bild_vorschau(text_mit_pfad: str, title: str | None = None) -> None:
    """Zeigt das zuletzt genannte Bild aus einem Ergebnistext als Pixel-Block.
    Fehler sind hier egal – die Vorschau ist Bonus, nie Pflicht."""
    try:
        pfade = vision.find_images(text_mit_pfad or "")
        if pfade:
            ui.show_image_preview(pfade[-1], title)
    except Exception:
        pass


async def converse(backend, user_text: str, images: list[str] | None = None) -> None:
    records = []
    actions.reset_taint()            # neue Nutzer-Runde: noch nichts aus dem Netz
    mitschrift.nutzer(user_text)     # F12 soll das ganze Gespräch sichern können
    try:
        await _converse(backend, user_text, images, records)
    finally:
        _close_records(records)
        ui.console.print(ui.execution_receipt(records))


async def _converse(backend, user_text, images, records) -> None:
    """Ein Gespräch mit Aktions-Schleife: antworten → ggf. handeln → weiter.
    images (base64-Data-URIs) werden nur beim ERSTEN Schritt mitgeschickt."""
    next_input = user_text
    first = True
    seen: set[str] = set()                   # identische Lese-Aktionen nur einmal
    for schritt in range(MAX_STEPS + 1):
        letzter = schritt == MAX_STEPS       # Extra-Runde: nur noch zusammenfassen
        if letzter:
            next_input = next_input + _STEP_LIMIT_NOTE
        # 1) Antwort streamen (Denken + Text getrennt)
        answer = ""
        thinking_txt = ""
        note = ""                        # dezente Info-Zeile (z.B. Auto-Stark)
        usage = {"input": 0, "output": 0}
        have_usage = False
        seed = ui.new_verb_seed()
        t0 = time.monotonic()
        think = {"start": None, "secs": None}    # wie lange wurde nachgedacht?
        last_paint = 0.0

        def _tokens() -> int:
            if have_usage and usage["output"]:
                return usage["output"]
            return (len(answer) + len(thinking_txt)) // 4   # Schätzung, bis echte Zahl da ist

        def _view():
            el = time.monotonic() - t0
            verb = ui.verb_at(seed, el)
            if thinking_txt or answer or note:
                think_secs = think["secs"]
                if think_secs is None and think["start"] is not None:
                    think_secs = time.monotonic() - think["start"]
                return Group(_render(thinking_txt, answer, think_secs, note),
                             ui.stream_meter(verb, el, _tokens()))
            return ui.working_meter(verb, el, _tokens())

        # Den Stream in einer Hintergrund-Task konsumieren und Ereignisse in eine
        # Queue legen. So kann die Live-Anzeige im Takt (alle 0.2s) aktualisiert
        # werden – auch wenn gerade KEIN Token kommt (Zeit/Verb laufen weiter),
        # und wir können nebenbei auf Esc lauschen.
        queue: asyncio.Queue = asyncio.Queue()

        imgs = images if first else (naechste_bilder or None)
        first = False
        naechste_bilder = []

        async def _produce():
            try:
                async for ev in backend.stream(next_input, images=imgs):
                    await queue.put(("ev", ev))
            except Exception as exc:        # Fehler durchreichen
                await queue.put(("err", exc))
                return
            await queue.put(("done", None))

        producer = asyncio.create_task(_produce())
        err = None
        aborted = False
        with ui.live_view(_view()) as live:
            try:
                while True:
                    # Auch bei lückenlosem Tokenstrom reagieren. Im Vollbild
                    # liest nur prompt_toolkit die Tastatur, nie parallel msvcrt.
                    abort_now = (ui.TUI.consume_abort() if ui.TUI is not None
                                 else _esc_pressed())
                    if abort_now:
                        aborted = True
                        break
                    try:
                        kind, payload = await asyncio.wait_for(queue.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        live.update(_view())      # Zeit/Verb weiterlaufen lassen
                        continue
                    if kind == "done":
                        break
                    if kind == "err":
                        err = payload
                        break
                    ev = payload
                    et = ev.get("type")
                    if et == "usage":
                        if "input" in ev:
                            usage["input"] = ev["input"]
                        if "output" in ev:
                            usage["output"] = ev["output"]
                        have_usage = True
                    elif et == "thinking":
                        if think["start"] is None:
                            think["start"] = time.monotonic()
                        thinking_txt += ev["text"]
                    elif et == "note":
                        note = ev["text"]        # dezente Info, nicht Teil der Antwort
                    else:
                        # erstes echtes Antwort-Zeichen -> Denkzeit steht fest
                        if (ev["text"].strip() and think["start"] is not None
                                and think["secs"] is None):
                            think["secs"] = time.monotonic() - think["start"]
                        answer += ev["text"]
                    now = time.monotonic()
                    if now - last_paint >= 1 / 12:
                        live.update(_view())
                        last_paint = now
            except KeyboardInterrupt:              # Ctrl-C -> ebenfalls abbrechen
                aborted = True

        # Stream-Task sauber beenden
        if not producer.done():
            producer.cancel()
            try:
                await producer
            except (asyncio.CancelledError, Exception):
                pass
        if err:
            if ui.TUI is not None:
                ui.TUI.set_thinking(thinking_txt, think["secs"], live=False)
            raise err

        elapsed = time.monotonic() - t0
        if think["start"] is not None and think["secs"] is None:
            think["secs"] = time.monotonic() - think["start"]
        out_toks = _tokens()
        SESSION["tokens"] += out_toks
        _update_session_meters(usage["input"], out_toks)
        try:
            stats.record_turn(SESSION.get("model"), PS.active().name, modes.current(),
                              usage["input"], out_toks,
                              pricing.cost(SESSION.get("model"), usage["input"], out_toks))
        except Exception:
            pass

        # Bei Abbruch: Verlauf konsistent halten (Backend hat den User-Turn schon
        # angehängt, aber keine Assistenten-Antwort) und hier stoppen.
        if aborted:
            if backend.messages and backend.messages[-1].get("role") == "user":
                backend.messages.append(
                    {"role": "assistant", "content": answer or "(abgebrochen)"})
            if answer.strip() or thinking_txt:
                ui.console.print(_render(thinking_txt, answer, think["secs"], done=True))
            mitschrift.assistent(answer or "(abgebrochen)", thinking_txt, think["secs"])
            mitschrift.notiz("⎋ Vom Nutzer abgebrochen.")
            ui.warn("⎋ Abgebrochen.")
            return

        acts, cleaned = actions.parse_actions(answer)
        # die vollständige Antwort einmal sauber ausgeben (kein Stapeln mehr)
        if cleaned:
            display = cleaned
        elif acts:
            display = "⚙ (Aktion vorbereitet)"        # nur wenn wirklich eine Aktion folgt
        else:
            display = "_(leere Antwort – frag ruhig nochmal)_"
        ui.console.print(_render(thinking_txt, display, think["secs"], note, done=True))
        # Denktext gibt es NUR hier - in backend.messages steht er nicht.
        mitschrift.assistent(display, thinking_txt, think["secs"])

        # kleine Zusammenfassung (Tokens · Zeit · Tempo), wenn es echte Antwort gab
        if cleaned:
            tps = out_toks / elapsed if elapsed > 0.3 else None
            ui.console.print(ui.done_meter(elapsed, out_toks, tps, usage["input"]))

        # 2) Keine Aktion -> fertig
        if not acts:
            if (hinweis := actions.unparsed_action_note(answer, acts)):
                ui.warn(hinweis)             # Zaun da, JSON kaputt: nicht stumm schlucken
            return
        if letzter:                          # Limit erreicht: geplante Aktion nicht mehr starten
            for act in acts:
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
            ui.warn(_STEP_LIMIT_WARN)
            return

        # 3) Aktionen abarbeiten (Bestätigung bei verändernden)
        ergebnisse = []
        for act in acts:
            # Tippfehler im Werkzeugnamen sanft korrigieren (z.B. 'beehl' -> 'befehl')
            raw_tool = act.get("tool")
            fixed = actions.resolve_tool(raw_tool)
            if fixed and fixed != raw_tool:
                act["tool"] = fixed
                ui.info(f"(kleiner Tippfehler korrigiert: '{raw_tool}' → '{fixed}')")
            tool = act.get("tool")

            if tool in _DEDUPE_TOOLS:
                key = _action_key(act)
                if key in seen:
                    ui.info(f"↩ Wiederholung übersprungen: {actions.describe(act)}")
                    records.append({"tool": tool, "status": "not_run"})
                    ergebnisse.append(_result_feedback(
                        tool, actions.ActionResult(_DUPLICATE_NOTE, ok=None)))
                    continue
                seen.add(key)

            # --- Helfer-Agenten (max 5, parallel, nur auf Abruf) ---
            if tool == "subagenten":
                async def _pruefe(titel, preview):
                    return "yes" if await review_action_confirm(titel, preview) else "no"

                execute = _helfer_executor(
                    records,
                    zeige_aktion=lambda d, c: ui.console.print(ui.action_request(d, c)),
                    zeige_ergebnis=lambda t, ok, rc: ui.console.print(ui.action_result(t, ok=ok)),
                    frage=ask_confirm, pruefe=_pruefe, warne=ui.warn)
                ergebnisse.append(await _helfer_lauf(
                    act, backend, records, execute,
                    zeige_start=lambda hs: ui.console.print(ui.action_request(
                        f"{len(hs)} Helfer-Agent(en) losschicken:\n"
                        + "\n".join(f"    • {h['rolle']}: {h['aufgabe']}" for h in hs),
                        confirm=False)),
                    zeige_bericht=lambda i, h, b: ui.console.print(
                        ui.subagent_panel(i, h["rolle"], h["aufgabe"], b)),
                    frage=ask_confirm))
                continue

            urteil = modes.decide(act, actions.needs_confirm(act))
            if urteil == "block":                    # 👁 Nur Lesen: nicht mal fragen
                res = actions.ActionResult(modes.block_message(act), ok=False)
                ui.console.print(ui.action_request(actions.describe(act), False))
                ui.console.print(ui.action_result(res.text, ok=None))
                records.append({"tool": tool, "status": "not_run"})
                protokoll.schreibe(tool, actions.describe(act), "blocked",
                                   veraendernd=actions.needs_confirm(act), wer=PS.active().name)
                ergebnisse.append(_result_feedback(tool, res))
                continue
            confirm = urteil == "ask" and tool not in AUTO_ALLOW
            ui.console.print(ui.action_request(actions.describe(act), confirm))
            if confirm:
                try:
                    preview = await asyncio.to_thread(actions.confirmation_preview, act)
                except (ValueError, OSError) as exc:
                    res = actions.ActionResult(f"Vorschau nicht möglich: {exc}", ok=False)
                    ui.console.print(ui.action_result(res.text, ok=res.ok))
                    records.append({"tool": tool, "status": "not_run"})
                    ergebnisse.append(_result_feedback(tool, res))
                    continue
                if preview is not None:
                    approved = await review_action_confirm("Dateiänderung prüfen", preview)
                    choice = "yes" if approved else "no"
                else:
                    choice = await ask_confirm(
                        "Wie möchtest du fortfahren?",
                        [
                            ("yes",    "Ja, ausführen"),
                            ("always", "Ja – und solche Aktionen nicht mehr fragen (diese Sitzung)"),
                            ("no",     "Nein, abbrechen"),
                        ],
                    )
                if choice not in ("yes", "always"):
                    ui.warn("Abgelehnt.")
                    mitschrift.aktion(tool, actions.describe(act),
                                      "Vom Nutzer abgelehnt.", False)
                    protokoll.schreibe(tool, actions.describe(act), "rejected",
                                       veraendernd=True, wer=PS.active().name)
                    records.append({"tool": tool, "status": "rejected"})
                    ergebnisse.append(
                        f"Aktion '{tool}' wurde vom Nutzer ABGELEHNT. "
                        "Führe sie nicht aus und frage, wie es weitergehen soll."
                    )
                    continue
                if choice == "always":
                    AUTO_ALLOW.add(tool)
                    ui.info(f"Aktionen vom Typ '{tool}' frage ich diese Sitzung nicht mehr ab.")
            record = {"tool": tool, "status": "running"}
            records.append(record)
            if tool == "bild_malen":
                # Bildmalen zeigt einen echten Ladebalken (Schritt i/n), genau wie
                # der /bild-Befehl. Die Aktion läuft im Thread und meldet ihren
                # Fortschritt über actions.bild_status() zurück.
                run_task = asyncio.create_task(actions.run(act))
                titel = "🎨 male dein Bild"
                with ui.live_view(ui.progress_panel(titel, "starte …")) as live:
                    while not run_task.done():
                        live.update(ui.progress_panel(
                            titel, actions.bild_status() or "male dein Bild …"))
                        await asyncio.sleep(0.2)
                res = await run_task
            else:
                with ui.thinking("führe aus"):
                    res = await actions.run(act)
            _finish_record(record, res)
            uris, res = await _bilder_aus_ergebnis(res, SESSION.get("model"))
            ui.console.print(ui.action_result(res.text, ok=res.ok))
            mitschrift.aktion(tool, actions.describe(act), res.text, res.ok)
            protokoll.schreibe(tool, actions.describe(act), record["status"],
                               veraendernd=actions.needs_confirm(act), wer=PS.active().name)
            if tool == "bild_malen" and res.ok:
                _bild_vorschau(res.text)
            if uris:
                naechste_bilder.extend(uris)
                for pf in res.bilder[:2]:
                    _bild_vorschau(str(pf), title=f"angesehen: {Path(pf).name}")
            ergebnisse.append(_result_feedback(tool, res))

        # 4) Ergebnisse zurück ans Modell, nächster Schritt
        next_input = "\n\n".join(ergebnisse)

    ui.warn(_STEP_LIMIT_WARN)                # Rückfall – normalerweise endet die Extra-Runde oben


_WEB_SCHRITT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _web_progress(msg: str) -> dict:
    """SSE-Ereignis für den WebUI-Ladebalken (Schritt i/n → Prozent)."""
    ev = {"t": "progress", "title": "🎨 male dein Bild", "msg": msg or "male dein Bild …"}
    m = _WEB_SCHRITT_RE.search(msg or "")
    if m:
        i, n = int(m.group(1)), int(m.group(2))
        if n > 0:
            ev["i"], ev["n"] = i, n
            ev["pct"] = int(100 * i / n)
    return ev


async def web_converse(backend, user_text, images, emit, confirm) -> None:
    records = []
    actions.reset_taint()
    try:
        await _web_converse(backend, user_text, images, emit, confirm, records)
    finally:
        _close_records(records)
        emit({"t": "receipt", "records": records})


async def _web_converse(backend, user_text, images, emit, confirm, records) -> None:
    """Kopflose Variante von converse() fürs WebUI.

    Gleiche Aktions-Schleife, aber statt ins Terminal zu rendern, gibt sie
    strukturierte Ereignisse via emit(dict) an den Browser und fragt
    Bestätigungen via confirm(frage, optionen)->wahl (async). Nutzt dieselben
    Werkzeuge/Backends wie das Terminal – derselbe Verlauf, dasselbe Modell.
    """
    next_input = user_text
    first = True
    seen: set[str] = set()
    for schritt in range(MAX_STEPS + 1):
        letzter = schritt == MAX_STEPS
        if letzter:
            next_input = next_input + _STEP_LIMIT_NOTE
        answer = ""
        thinking_txt = ""
        usage = {"input": 0, "output": 0}
        have_usage = False
        t0 = time.monotonic()
        imgs = images if first else (naechste_bilder or None)
        first = False
        naechste_bilder = []
        try:
            async for ev in backend.stream(next_input, images=imgs):
                et = ev.get("type")
                if et == "usage":
                    usage["input"] = ev.get("input", usage["input"])
                    usage["output"] = ev.get("output", usage["output"])
                    have_usage = True
                elif et == "thinking":
                    thinking_txt += ev["text"]
                    emit({"t": "thinking", "delta": ev["text"]})
                elif et == "note":
                    emit({"t": "note", "text": ev["text"]})   # dezente Info, nicht Teil der Antwort
                else:
                    answer += ev["text"]
                    emit({"t": "answer", "delta": ev["text"]})
        except Exception as exc:
            emit({"t": "error", "text": f"Fehler bei der Antwort: {exc}"})
            return

        elapsed = time.monotonic() - t0
        out_toks = usage["output"] if (have_usage and usage["output"]) \
            else (len(answer) + len(thinking_txt)) // 4
        SESSION["tokens"] += out_toks
        _update_session_meters(usage["input"], out_toks)
        try:
            stats.record_turn(SESSION.get("model"), PS.active().name, modes.current(),
                              usage["input"], out_toks,
                              pricing.cost(SESSION.get("model"), usage["input"], out_toks))
        except Exception:
            pass
        acts, cleaned = actions.parse_actions(answer)
        emit({"t": "answer_end",
              "clean": cleaned if cleaned else (None if acts else ""),
              "input": usage["input"], "output": out_toks,
              "elapsed": round(elapsed, 2),
              "tps": round(out_toks / elapsed, 1) if elapsed > 0.3 else None})

        if not acts:
            if (hinweis := actions.unparsed_action_note(answer, acts)):
                emit({"t": "note", "text": hinweis})
            return
        if letzter:
            for act in acts:
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
            emit({"t": "warn", "text": _STEP_LIMIT_WARN})
            return

        ergebnisse = []
        for act in acts:
            raw_tool = act.get("tool")
            fixed = actions.resolve_tool(raw_tool)
            if fixed and fixed != raw_tool:
                act["tool"] = fixed
            tool = act.get("tool")

            if tool in _DEDUPE_TOOLS:
                key = _action_key(act)
                if key in seen:
                    emit({"t": "note", "text": f"↩ Wiederholung übersprungen: {actions.describe(act)}"})
                    records.append({"tool": tool, "status": "not_run"})
                    ergebnisse.append(_result_feedback(
                        tool, actions.ActionResult(_DUPLICATE_NOTE, ok=None)))
                    continue
                seen.add(key)

            if tool == "subagenten":
                execute = _helfer_executor(
                    records,
                    zeige_aktion=lambda d, c: emit({"t": "action", "desc": d, "needs_confirm": c}),
                    zeige_ergebnis=lambda t, ok, rc: emit(
                        {"t": "action_result", "ok": ok, "text": t, "returncode": rc}),
                    frage=confirm,
                    pruefe=lambda titel, preview: confirm(
                        titel, [("yes", "Gezeigten Inhalt freigeben"), ("no", "Ablehnen")],
                        preview=preview),
                    warne=lambda t: emit({"t": "warn", "text": t}))
                ergebnisse.append(await _helfer_lauf(
                    act, backend, records, execute,
                    zeige_start=lambda hs: emit({
                        "t": "action", "needs_confirm": False,
                        "desc": f"{len(hs)} Helfer-Agent(en) losschicken:\n"
                                + "\n".join(f"• {h['rolle']}: {h['aufgabe']}" for h in hs)}),
                    zeige_bericht=lambda i, h, b: emit({
                        "t": "action_result", "ok": None,
                        "text": f"🤝 {subagents.label(i, h)} – {h['aufgabe']}\n\n{b}"}),
                    frage=confirm))
                continue

            urteil = modes.decide(act, actions.needs_confirm(act))
            if urteil == "block":
                res = actions.ActionResult(modes.block_message(act), ok=False)
                emit({"t": "action", "desc": actions.describe(act), "needs_confirm": False})
                emit({"t": "action_result", "ok": None, "text": res.text})
                records.append({"tool": tool, "status": "not_run"})
                ergebnisse.append(_result_feedback(tool, res))
                continue
            need = urteil == "ask" and tool not in AUTO_ALLOW
            emit({"t": "action", "desc": actions.describe(act), "needs_confirm": need})
            if need:
                try:
                    preview = await asyncio.to_thread(actions.confirmation_preview, act)
                except (ValueError, OSError) as exc:
                    res = actions.ActionResult(f"Vorschau nicht möglich: {exc}", ok=False)
                    emit({"t": "action_result", "ok": False, "text": res.text})
                    records.append({"tool": tool, "status": "not_run"})
                    ergebnisse.append(_result_feedback(tool, res))
                    continue
                if preview is not None:
                    choice = await confirm(
                        "Dateiänderung prüfen",
                        [("yes", "Gezeigten Inhalt freigeben"), ("no", "Ablehnen")],
                        preview=preview)
                else:
                    choice = await confirm(
                        "Wie möchtest du fortfahren?",
                        [("yes", "Ja, ausführen"),
                         ("always", "Ja – solche Aktionen diese Sitzung nicht mehr fragen"),
                         ("no", "Nein, abbrechen")])
                if choice not in ("yes", "always"):
                    emit({"t": "warn", "text": "Aktion abgelehnt."})
                    records.append({"tool": tool, "status": "rejected"})
                    ergebnisse.append(
                        f"Aktion '{tool}' wurde vom Nutzer ABGELEHNT. "
                        "Führe sie nicht aus und frage, wie es weitergehen soll.")
                    continue
                if choice == "always":
                    AUTO_ALLOW.add(tool)

            record = {"tool": tool, "status": "running"}
            records.append(record)
            if tool == "bild_malen":
                # Gleicher Ladebalken wie im Terminal: Aktion im Thread,
                # Fortschritt über actions.bild_status() an den Browser.
                run_task = asyncio.create_task(actions.run(act))
                last = None
                while not run_task.done():
                    msg = actions.bild_status() or "male dein Bild …"
                    if msg != last:
                        last = msg
                        emit(_web_progress(msg))
                    await asyncio.sleep(0.2)
                res = await run_task
                emit({"t": "progress_end"})
            else:
                res = await actions.run(act)
            _finish_record(record, res)
            uris, res = await _bilder_aus_ergebnis(res, SESSION.get("model"))
            if uris:
                naechste_bilder.extend(uris)
            emit({"t": "action_result", "ok": res.ok, "text": res.text,
                  "returncode": res.returncode})
            ergebnisse.append(_result_feedback(tool, res))

        next_input = "\n\n".join(ergebnisse)

    emit({"t": "warn", "text": _STEP_LIMIT_WARN})


async def do_reflect(ctx: "Ctx") -> list[tuple[str, str]]:
    """Reflexion: lässt das aktive Modell auf das letzte Gespräch schauen und
    bleibende Lehren ziehen (LEKTION/VORLIEBE), die dauerhaft gemerkt werden.
    Gibt die gespeicherten (art, text)-Paare zurück."""
    backend = ctx.backend
    msgs = [m for m in backend.messages if m.get("role") in ("user", "assistant")][-12:]
    if not msgs:
        return []

    def _as_text(content) -> str:
        if isinstance(content, list):       # Vision-Inhalt (Text + Bildblöcke)
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content or "")

    transcript = "\n".join(
        f"{'Nutzer' if m['role'] == 'user' else 'Du'}: {_as_text(m['content'])[:500]}"
        for m in msgs
    )
    system = (
        "Du bist NemiCLIs Reflexions-Modul. Schau auf das Gespräch und ziehe BLEIBENDE, "
        "allgemein nützliche Lehren für die Zukunft – über den Nutzer, seinen PC oder die "
        "richtige Arbeitsweise. Gib HÖCHSTENS 3 Zeilen aus. Jede Zeile beginnt mit "
        "'LEKTION:' (eine gelernte Lehre) oder 'VORLIEBE:' (wie der Nutzer etwas will). "
        "Keine Wegwerf-Details des Einzelfalls, keine Floskeln. Wenn es nichts wirklich "
        "Bleibendes gibt, schreibe nur 'KEINE'."
    )
    prompt = f"Gespräch:\n{transcript}\n\nDeine Lehren (höchstens 3 Zeilen):"
    with ui.thinking("reflektiere über das Gespräch"):
        try:
            out = await backend.ask_once(prompt, system)
        except Exception as e:
            ui.error(f"Reflexion fehlgeschlagen: {e}")
            return []

    saved: list[tuple[str, str]] = []
    for line in (out or "").splitlines():
        line = line.strip().lstrip("-•* ").strip()
        low = line.lower()
        if low.startswith("lektion:"):
            art, txt = "lektion", line.split(":", 1)[1].strip()
        elif low.startswith("vorliebe:"):
            art, txt = "vorliebe", line.split(":", 1)[1].strip()
        else:
            continue
        if len(txt) < 4:
            continue
        e = memory.remember(txt, art)
        if e is not None:                   # None = leer/Dublette
            saved.append((art, txt))
    if saved:
        await asyncio.to_thread(indexdb.after_turn, ctx.current_chat)
        memory.release_encoder()
    return saved


def start_chrome_bridge(ctx: "Ctx") -> None:
    """Empfangsstelle für die Chrome-Erweiterung starten – nur 127.0.0.1, mit Schlüssel.
    Läuft still mit; schlägt der Port fehl, sagt /chrome warum."""
    global CHROME_BRIDGE
    import chromebridge

    async def beantworten(nachricht: str) -> str:
        """Ein Chat-Zug im NemiCLI-Fenster; zurück kommt nur der gesäuberte Antworttext."""
        if ctx.backend is None:
            raise RuntimeError("Kein Modell aktiv – in NemiCLI /model wählen.")
        import kontextmenue
        n_vorher = len(ctx.backend.messages)
        if ui.TUI is not None and hasattr(ui.TUI, "_echo_user"):
            ui.TUI._echo_user(nachricht.split("\n", 1)[0][:160] + " …")
        await handle_line(ctx, nachricht)
        neu = ctx.backend.messages[n_vorher:]
        antworten = [m.get("content") for m in neu
                     if m.get("role") == "assistant" and isinstance(m.get("content"), str)]
        return kontextmenue.antwort_text(antworten[-1]) if antworten else ""

    try:
        loop = asyncio.get_running_loop()
        CHROME_BRIDGE = chromebridge.Bridge(loop, beantworten, chromebridge.key())
        if not CHROME_BRIDGE.start():
            ui.warn(f"🐈 Chrome-Empfang nicht gestartet: {CHROME_BRIDGE.letzter_fehler}")
    except Exception as e:
        CHROME_BRIDGE = None
        ui.warn(f"🐈 Chrome-Empfang nicht gestartet: {e}")


async def start_webui(ctx: "Ctx") -> "webui.WebBridge":
    """Startet den WebUI-Server und verbindet ihn mit der aktuellen Sitzung (ctx).
    Gibt die Brücke zurück (oder wirft bei Fehler)."""
    loop = asyncio.get_running_loop()
    bridge = webui.WebBridge(loop)

    subagents.on_aktiv(lambda n: bridge.broadcast({"t": "helfer", "n": n}))

    def web_state() -> dict:
        return {
            "t": "state",
            "helfer": subagents.aktiv(),
            "model": ctx.model or "—",
            "chat": ctx.current_chat,
            "tokens": SESSION["tokens"],
            "strength": ctx.strength if ctx.strength_active() else None,
            "ctx": SESSION.get("ctx") or 0,
            "ctx_max": SESSION.get("ctx_max") or 0,
            "cost": SESSION.get("cost") or 0.0,
            "chats": chatstore.list_chats()[:40],
        }

    async def do_turn(text: str, image_uris: list) -> None:
        async with TURN_LOCK:
            if ctx.backend is None:
                bridge.broadcast({"t": "warn", "text": "Kein Modell aktiv – oben eins wählen."})
                bridge.broadcast({"t": "turn_end"})
                return
            bridge.broadcast({"t": "busy", "on": True})
            try:
                txt = emoji.expand(text or "")
                dateien = anhang.anhaengen(txt)
                txt = dateien["text"]
                if (h := anhang.hinweis(dateien)):
                    bridge.broadcast({"t": "info", "text": h})
                txt, imgs, hinweis = await _bilder_fuer_chat(
                    ctx.model, txt, extra_uris=list(image_uris or []))
                if hinweis:
                    bridge.broadcast({"t": "info", "text": hinweis})
                if not imgs and not txt.strip():
                    return
                await web_converse(ctx.backend, txt, imgs,
                                   bridge.broadcast, bridge.ask)
                ctx.current_prompts.append(txt)
                chatstore.save(ctx.current_chat, ctx.backend.messages,
                               ctx.current_prompts, ctx.model or "—")
                learn.capture_from_answer(txt, ctx.backend.messages, ctx.current_chat)
                await asyncio.to_thread(indexdb.after_turn, ctx.current_chat)
                memory.release_encoder()
            except Exception as e:
                bridge.broadcast({"t": "error", "text": f"Fehler: {e}"})
            finally:
                bridge.broadcast({"t": "busy", "on": False})
                bridge.broadcast({"t": "turn_end"})
                bridge.broadcast(web_state())
                ctx.sync_session()

    async def do_switch(ref: str) -> None:
        if not ref:
            return
        ctx.backend, ctx.model = await switch_model(ref, ctx.backend, ctx.model, ctx.strength)
        bridge.broadcast({"t": "info", "text": f"Modell aktiv: {ctx.model}"})
        bridge.broadcast(web_state())
        ctx.sync_session()

    async def do_resume(cid: int) -> None:
        async with TURN_LOCK:
            data = chatstore.load(cid)
            if not data:
                bridge.broadcast({"t": "warn", "text": f"Chat #{cid} gibt es nicht."})
                return
            if ctx.backend is None:
                bridge.broadcast({"t": "warn", "text": "Erst ein Modell wählen, dann fortsetzen."})
                return
            ctx.backend.messages = data.get("messages", [])
            ctx.current_prompts = data.get("prompts", [])
            ctx.current_chat = cid
            ctx.sync_session()
            titel = data.get("title") or ""
            bridge.broadcast({"t": "chat_switch", "chat": cid, "title": titel})
            bridge.broadcast({"t": "info", "text": f"Chat #{cid} fortgesetzt: {titel}"})
            bridge.broadcast(web_state())

    async def do_newchat() -> None:
        async with TURN_LOCK:
            if ctx.backend:
                ctx.backend.reset()
            ctx.current_prompts = []
            ctx.current_chat = chatstore.next_id()
            ctx.sync_session()
            bridge.broadcast({"t": "chat_switch", "chat": ctx.current_chat, "title": ""})
            bridge.broadcast({"t": "info", "text": f"Neuer Chat #{ctx.current_chat} gestartet."})
            bridge.broadcast(web_state())

    async def list_all_models() -> list:
        out = []
        if P.get("ollama").available():
            for mid in P.ollama_models():
                v = " 👁" if P.ollama_is_vision(mid) else ""
                out.append({"ref": M.make_ref("ollama", mid), "label": mid + v, "group": "Ollama"})
        for pid, p in P.detected().items():
            if p.keyless:
                continue
            try:
                ids = await P.list_models(pid)
            except Exception:
                ids = []
            for mid in ids:
                out.append({"ref": M.make_ref(pid, mid), "label": mid, "group": "☁ " + p.label})
        return out

    def schedule(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def models_blocking() -> list:
        try:
            return schedule(list_all_models()).result(timeout=25)
        except Exception:
            return []

    handlers = {
        "send":    lambda text, images: schedule(do_turn(text, images)),
        "switch":  lambda ref: schedule(do_switch(ref)),
        "models":  models_blocking,
        "state":   web_state,
        "chats":   lambda: {"current": ctx.current_chat, "chats": chatstore.list_chats()[:40]},
        "resume":  lambda cid: schedule(do_resume(int(cid))) if cid not in (None, "") else None,
        "newchat": lambda: schedule(do_newchat()),
    }
    url = webui.start(bridge, handlers)
    return bridge


def _migrate_ref(old: str | None) -> str | None:
    """Alte gemerkte Modellnamen (ohne Anbieter-Präfix) ins neue Ref-Schema heben."""
    if not old:
        return None
    if ":" in old:                       # schon im neuen Format
        return old
    if old.startswith("claude"):         # früher: bare Anthropic-Name
        return M.make_ref("anthropic", old)
    # Frueher stand hier noch die Umschreibung alter GGUF-Kurznamen
    # ("gemma-12b" -> local:...). Mit llama.cpp ist die entfallen.
    return None


async def make_backend(ref: str, carry: list | None, strength: str = M.DEFAULT_STRENGTH):
    """Baut das passende Backend für eine Modell-Referenz. Überträgt den Verlauf."""
    provider, model_id = M.split_ref(ref)

    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Für Claude brauchst du einen ANTHROPIC_API_KEY in der .env.")
        from chat import Chat
        backend = Chat(model=model_id, strength=strength)
    else:
        prov = P.get(provider)
        if prov is None:
            raise RuntimeError(f"Unbekannter Anbieter: '{provider}'.")
        if not prov.available():
            raise RuntimeError(f"Kein Key für {prov.label} – {prov.env} in der .env setzen.")
        from cloud import CloudChat
        backend = CloudChat(provider, model_id, strength=strength)

    if carry:
        backend.messages = carry
    return backend


def _fmt_bytes(n: int) -> str:
    return f"{n / 1_048_576:.0f} MB" if n >= 1_048_576 else f"{n / 1024:.0f} KB"


async def _run_with_status(title: str, work) -> str:
    """Führt blockierende Arbeit (work(on_status) -> str) in einem Thread aus und
    zeigt dabei eine Live-Fortschrittsanzeige. Gibt das Ergebnis zurück."""
    state = {"msg": "starte …", "done": False, "result": "", "err": None}

    def on_status(m: str) -> None:
        state["msg"] = m

    def runner() -> None:
        try:
            state["result"] = work(on_status)
        except Exception as e:                  # an den Aufrufer durchreichen
            state["err"] = e
        finally:
            state["done"] = True

    task = asyncio.create_task(asyncio.to_thread(runner))
    with ui.live_view(ui.progress_panel(title, state["msg"])) as live:
        while not state["done"]:
            live.update(ui.progress_panel(title, state["msg"]))
            await asyncio.sleep(0.2)
        live.update(ui.progress_panel(title, state["msg"]))
    await task
    if state["err"]:
        raise state["err"]
    return state["result"] or ""


async def run_reich(erststart: bool = False) -> None:
    """Wo soll der NemiCLI-Ordner liegen?  (/start – und einmalig beim ersten Start)

    Trennt den Ordner des Nutzers vom Programm-Ordner. Warum das sein muss,
    steht in core/paths.py; wie das Fenster arbeitet, in core/reich.py.

    Drei Dinge sind hier Absicht:
      * Es wird KEIN Ordner vorgeschlagen – das ist die Entscheidung des Nutzers.
      * Vor dem Kopieren steht, was mitkommt und wie groß es ist. Bei mehreren
        Gigabyte Modellen soll niemand blind auf "Ja" drücken.
      * Es wird nichts gelöscht. Das Original bleibt liegen.
    """
    import reich

    if erststart:
        ui.info("📁 Einmalige Frage: wo soll dein NemiCLI-Ordner liegen? "
                "Ich hab dir ein Fenster aufgemacht.")

    wahl = await asyncio.to_thread(reich.fenster, paths.GEWAEHLT)

    if wahl is None:
        ui.info("Nichts geändert. Mit  /start  kommst du jederzeit wieder hierher.")
        return

    if wahl == "":
        reich.speichern("")
        ui.success(f"Alles bleibt beim Programm: {paths.INSTALL}")
        ui.info("Mit  /start  kannst du das später jederzeit ändern.")
        return

    ziel = Path(wahl)
    if (fehler := reich.pruefe_ziel(ziel)):
        ui.error(fehler)
        return

    posten = reich.inhalt()
    kopiert = False
    if posten:
        gesamt = sum(g for _n, g in posten)
        ui.info(f"Das liegt gerade in {paths.DATEN} und käme mit:")
        for name, g in posten:
            ui.console.print(f"   [dim]{reich.lesbare_groesse(g):>12}[/dim]  {name}")
        ui.console.print(f"   [dim]{'─' * 12}[/dim]  "
                         f"zusammen {reich.lesbare_groesse(gesamt)}")
        wahl2 = await ask_confirm(
            f"Nach {ziel} kopieren?  (Das Original bleibt liegen – es wird nichts gelöscht.)",
            [("ja", "Ja, kopieren"),
             ("nur", "Nur den Ort merken, nichts kopieren"),
             ("__cancel__", "Abbrechen – nichts ändern")])
        if wahl2 == "__cancel__":
            ui.info("Abgebrochen. Es wurde nichts geändert.")
            return
        if wahl2 == "ja":
            bericht = await _run_with_status(
                "Umzug",
                lambda on_status: "\n".join(reich.umziehen(ziel, melde=on_status)))
            for zeile in bericht.splitlines():
                if zeile.startswith("ok"):
                    ui.console.print(f"   [green]{zeile}[/green]")
                else:
                    ui.console.print(f"   [red]{zeile}[/red]")
            schlecht = [z for z in bericht.splitlines() if not z.startswith("ok")]
            if schlecht:
                ui.error("Nicht alles ist sauber angekommen. Ich stelle den Ordner "
                         "NICHT um – deine Daten liegen unverändert am alten Ort.")
                return
            kopiert = True

    reich.speichern(str(ziel))
    ui.success(f"Dein NemiCLI-Ordner ist jetzt: {ziel}")
    if kopiert:
        ui.info(f"Die alten Dateien liegen weiter in {paths.DATEN} – "
                "die kannst du wegräumen, wenn du dich überzeugt hast.")
    ui.warn("Bitte NemiCLI einmal neu starten, damit der neue Ordner überall gilt.")


async def run_wizard(nur_offene: bool = False) -> None:
    """Einrichtungs-Assistent (/einrichten): prüft, fragt, installiert.

    Modelle werden NICHT geladen – nur gezeigt, wo man sie herbekommt und
    wohin die Dateien gehören. Alles andere (Python, torch, Bild-Bausteine)
    erledigt der Assistent auf Wunsch selbst."""
    import wizard as W

    ui.info("🧰 Ich schaue nach, was auf diesem PC schon da ist …")
    schritte = await asyncio.to_thread(W.check_all)
    ui.wizard_panel(schritte)

    todo = W.offene(schritte)
    if not todo:
        ui.success("Alles installiert – nichts zu tun. ✨")
    for s in todo:
        wahl = await ask_confirm(
            f"{s['titel']} fehlt – jetzt installieren?",
            [("ja", "Ja, mach das für mich"),
             ("nein", "Nein, überspringen"),
             ("__cancel__", "Assistent beenden")])
        if wahl == "__cancel__":
            ui.info("Abgebrochen – /einrichten führt dich jederzeit wieder hierher.")
            return
        if wahl != "ja":
            continue
        try:
            ergebnis = await _run_with_status(
                s["titel"],
                lambda on_status, _id=s["id"]: W.erledige(_id, on_status))
            ui.success(f"✅ {ergebnis}")
        except Exception as e:
            ui.error(f"Hat nicht geklappt: {e}")
            ui.info("Du kannst es später mit /einrichten nochmal versuchen.")

    # Modelle: nur erklären – die sucht sich jeder selbst aus.
    fehlt_modell = [s for s in schritte
                    if s["id"] in ("sprachmodell", "bildmodell") and not s["ok"]]
    if fehlt_modell or nur_offene is False:
        ui.wizard_models_panel(W.MODELL_HINWEISE, W.modell_ordner())
    if todo:
        ui.info("Zum Nachsehen, was jetzt läuft:  /systemcheck")


_SCHWERE_ZEICHEN = {"critical": "🛑 kritisch", "high": "⚠ hoch",
                    "moderate": "• mittel", "medium": "• mittel", "low": "· niedrig"}


def _cve_text(cves: list) -> str:
    """Die offenen Sicherheitshinweise als Text für die Vorschau (F8)."""
    zeilen = []
    for c in cves:
        grad = _SCHWERE_ZEICHEN.get(c["schwere"], c["schwere"])
        punkte = f"  ·  CVSS {c['punkte']}" if c.get("punkte") else ""
        zustand = ("betrifft den neuen Build" if c["offen"] else
                   "Grenze ist ein Commit, keine Build-Nummer – nicht vergleichbar")
        zeilen.append(f"{grad}{punkte}   {c['cve']}   ({zustand})")
        zeilen.append(f"    {c['titel']}")
        zeilen.append(f"    gemeldet {c.get('datum') or '?'} · betrifft "
                      f"{c['bereich'] or '?'} · behoben ab {c['fix'] or '?'}")
        zeilen.append(f"    {c['url']}")
        zeilen.append("")
    return "\n".join(zeilen).rstrip()


async def workspace_befehl(ctx: "Ctx | None" = None, arg: str = "") -> None:
    """/workspace – einen Projektordner festnageln. /workspaceend hebt ihn auf.

    Geschaltet wird das NUR hier, vom Nutzer. Die Persoenlichkeit hat dafuer
    kein Werkzeug: sie soll sich nicht selbst einsperren oder befreien."""
    unter = arg.strip()
    if unter.lower() in ("end", "ende", "aus", "stop", "off"):
        alt = WS.beenden()
        if alt is None:
            ui.info("Es war kein Workspace aktiv.")
        else:
            ui.success(f"Workspace beendet: {alt}")
            ui.info("NemiCLI arbeitet wieder wie immer – überall, außer in den "
                    "gesperrten Systemordnern.")
            mitschrift.notiz(f"Workspace beendet: {alt}")
        return

    if unter.lower() in ("status", "wo", "?"):
        p = WS.pfad()
        ui.info(f"Workspace: {p}" if p else "Kein Workspace aktiv.")
        return

    try:
        erg = WS.setzen(unter or None)
    except ValueError as e:
        ui.warn(f"Workspace nicht gesetzt: {e}")
        return

    ui.kv_panel("📌 Workspace", [
        ("Ordner", str(erg["pfad"])),
        ("Projekt-Ablage", str(erg["ordner"])
         + (f"  (neu: {', '.join(erg['angelegt'])})" if erg["angelegt"] else "")),
        ("Gilt für", "Lesen, Schreiben, Suchen, Befehle – nur hier und darunter"),
        ("Gedächtnis", "merken/skill_merken schreiben ins Projekt, nicht in NemiCLI"),
        ("Beenden", "/workspaceend"),
    ])
    mitschrift.notiz(f"Workspace gesetzt: {erg['pfad']}")

    auftrag = _auftrag_lesen("workspace")
    if auftrag is None:
        ui.warn(f"Anleitung fehlt: {AGENTEN_DIR / 'workspace.md'} – "
                "der Riegel gilt trotzdem.")
        return

    # Die Anweisung zusaetzlich als Datei ins Projekt: steht sie nur im Chat,
    # ist sie weg, sobald der Verlauf abreisst - und dann wird geraten.
    try:
        kopie = WS.schreibe_auftrag(auftrag)
        if kopie:
            ui.info(f"📄 Anweisung liegt als Datei im Projekt: {kopie}")
    except OSError as e:
        ui.warn(f"Anweisung konnte nicht als Datei abgelegt werden: {e}")
    if ctx is None or getattr(ctx, "backend", None) is None:
        ui.warn("Kein Modell aktiv – der Riegel gilt, aber es kann niemand loslegen. "
                "Wähle ein Modell mit /model.")
        return
    ui.info(f"📌 {ui.persona_name()} liest die Anleitung (Agenten/workspace.md) "
            "und sieht sich den Ordner an.")
    await converse(ctx.backend, auftrag)


AGENTEN_DIR = paths.ROOT / "Agenten"


def _auftrag_lesen(name: str) -> str | None:
    """Lädt eine Agenten-Anleitung und setzt {{char}}/{{user}} ein.

    Keine feste Persönlichkeit: wer gerade spricht, steht in der Anleitung als
    Platzhalter – so gilt dieselbe Datei für Nemi, Lara oder jedes eigene Profil."""
    p = AGENTEN_DIR / f"{name}.md"
    if not p.exists():
        return None
    return PS.render(p.read_text(encoding="utf-8"))


def _do_ollama_pull(name: str, on_status) -> str:
    """Lädt ein Ollama-Modell (blockierend) mit Fortschritts-Meldung."""
    def prog(status: str, completed: int, total: int) -> None:
        if total:
            on_status(f"{status} … {completed * 100 // total}% "
                      f"({_fmt_bytes(completed)}/{_fmt_bytes(total)})")
        else:
            on_status(status or "lädt …")
    for _ in S.ollama_pull(name, prog):
        pass
    return f"{name} geladen"


async def _ollama_pull_and_done(name: str) -> None:
    """Lädt ein Ollama-Modell mit Live-Fortschritt und meldet das Ergebnis."""
    try:
        await _run_with_status(f"lade Ollama-Modell {name}",
                               lambda on_status: _do_ollama_pull(name, on_status))
    except Exception as e:
        ui.error(f"Download fehlgeschlagen: {e}")
        return
    P._ollama_tags(force=True)                   # Cache auffrischen -> Modell taucht auf
    ui.success(f"🦙 {name} ist da. Wähle es jetzt über /model → Ollama.")


async def setup_ollama() -> None:
    """Ollama-Modell laden: erst GRÖSSE wählen, dann ein Modell dieser Größe aus
    der LIVE-Liste von ollama.com (immer aktuell, nichts vorgegeben). Bei nicht
    installiertem/laufendem Ollama: Hinweis."""
    if not S.ollama_installed():
        ui.warn("🦙 " + S.ollama_install_hint())
        return
    if not S.ollama_running():
        ui.warn("🦙 Ollama ist installiert, läuft aber nicht. Starte die Ollama-App "
                "und versuch es nochmal.")
        return

    with ui.thinking("hole die aktuelle Ollama-Modell-Liste"):
        catalog = await asyncio.to_thread(S.ollama_catalog)
    with ui.thinking("erkenne deine Grafikkarte"):
        gpu = await asyncio.to_thread(S.gpu_info)
    ui.setup_panel(gpu, S.hardware_hint(gpu),
                   links=[("🦙 Ollama-Bibliothek:", S.OLLAMA_LIBRARY_URL)])

    # Netz weg / Liste leer -> wenigstens manuell eingeben lassen.
    if not catalog:
        ui.warn(f"Konnte die Live-Liste nicht laden. Stöbere selbst: {S.OLLAMA_LIBRARY_URL}")
        name = (await ask_text("Modellname zum Laden (Format name:größe, leer = abbrechen):")).strip()
        if name:
            await _ollama_pull_and_done(name)
        return

    # --- Stufe A: Größe wählen (ab 4B; alles darunter ist zu schwach für Logik) ---
    sizes = S.ollama_sizes(catalog, lo=4.0, hi=14.0)        # 4B … 14B
    big = S.ollama_models_for(catalog, min_b=14.0, limit=99)    # > 14B
    opts: list[tuple[str, str]] = []
    opts += [(s, f"{s.upper()}  ·  {n} Modell(e)") for s, n in sizes]
    if big:
        opts.append(("__big__", f"groß  > 14B  ·  {len(big)} Modell(e)"))
    opts.append(("__manual__", "Name selbst eingeben"))
    opts.append(("__cancel__", "Abbrechen"))
    pick = await ask_confirm("Welche Größe?  (Ollama lädt standardmäßig Q4_K_M)", opts)
    if pick == "__cancel__":
        return
    if pick == "__manual__":
        name = (await ask_text("Modellname (Format name:größe, leer = abbrechen):")).strip()
        if name:
            await _ollama_pull_and_done(name)
        return

    # --- Stufe B: konkretes Modell dieser Größe wählen ---
    if pick == "__big__":
        models = S.ollama_models_for(catalog, min_b=14.0, limit=18)
        titel = "Modelle größer als 14B  ·  Q4_K_M"
    else:
        models = S.ollama_models_for(catalog, size=pick, limit=18)
        titel = f"{pick.upper()}-Modelle  ·  Q4_K_M"
    mopts = [(m, m) for m in models]
    mopts.append(("__cancel__", "Abbrechen / zurück"))
    chosen = await ask_confirm(titel, mopts)
    if chosen == "__cancel__":
        return
    await _ollama_pull_and_done(chosen)


# Sitzungs-Zähler für den Übungsmodus (wie viele Runden bisher gelaufen sind)
PRACTICE_STATE = {"count": 0}


def _practice_emit(kind: str, payload) -> None:
    """Übersetzt die Schritt-Ereignisse einer Übungsrunde in hübsche Ausgaben."""
    if kind == "start":
        ui.console.print(ui.practice_header(payload))
    elif kind == "code":
        path, code = payload
        ui.console.print(ui.practice_code(path.name, code))
    elif kind == "run":
        ui.info(f"▶ {payload}")
    elif kind == "ok":
        ui.console.print(ui.action_result(payload, ok=True))
    elif kind == "fail":
        ui.console.print(ui.action_result(payload, ok=False))
    elif kind == "learned":
        ui.success(payload)
    elif kind == "error":
        ui.error(payload)
    elif kind in ("giveup", "cancelled"):
        ui.warn(payload)


async def practice_round(ctx: "Ctx", should_stop) -> str:
    """Eine freigegebene Übungsrunde im Arbeitsordner.
    Teilt sich den TURN_LOCK mit echten Chats – nie beides gleichzeitig."""
    if ctx.backend is None:
        return "abgebrochen"

    async def approve(path, code):
        if should_stop():
            return False
        preview = (f"Ziel: {path}\n\n"
                   "Diesen Code als Datei speichern und danach mit Python ausführen.\n"
                   "NemiSandbox ist ein Arbeitsordner. Das Programm läuft mit deinen "
                   "Benutzerrechten.\n\n" + code)
        return await review_action_confirm("Übung: Code prüfen und freigeben", preview)

    async with TURN_LOCK:
        if should_stop():
            return "abgebrochen"
        PRACTICE_STATE["count"] += 1
        return await PRAC.one_round(ctx.backend, _practice_emit, should_stop,
                                    n=PRACTICE_STATE["count"], approve=approve)


async def pick_model(current: str | None) -> str | None:
    """Zweistufige Auswahl: erst Cloud-Anbieter ODER Lokal, dann das Modell.
    Gibt eine Modell-Referenz zurück oder None (abgebrochen)."""
    provs = {pid: p for pid, p in P.detected().items() if not p.keyless}
    ollama_on = P.get("ollama").available()
    # Anbieter ohne Key, die man einrichten kann (keyless wie Ollama ausgenommen).
    keyless = [p for pid, p in P.PROVIDERS.items() if not p.available() and not p.keyless]

    # --- Stufe 1: Wohin? ---
    stufe1: list[tuple[str, str]] = []
    for pid, p in provs.items():            # Anbieter MIT Key: einzeln wählbar
        extra = f"  ·  {p.note}" if p.note else ""
        stufe1.append((f"cloud:{pid}", f"☁  {p.label}{extra}"))
    if keyless:                             # Anbieter OHNE Key: hinzufügen (Key eintippen)
        stufe1.append(("cloudadd",
                       f"☁  Cloud-Anbieter hinzufügen  ·  Key eintragen ({len(keyless)})"))
    if ollama_on:                           # lokales Ollama läuft -> eigener Zweig
        n = len(P.ollama_models())
        stufe1.append(("ollama", f"🦙  Ollama  ·  {n} Modell(e)  ·  lokal, kein Key"))
    # Ollama-Modell laden – bzw. einrichten, wenn Ollama noch nicht da/aus ist.
    stufe1.append(("ollama_get",
                   "🦙  Ollama-Modell herunterladen" if ollama_on
                   else "🦙  Ollama einrichten  ·  installieren & Modell laden"))
    stufe1.append(("__cancel__", "Abbrechen"))

    wahl = await ask_confirm("Cloud-Anbieter oder Lokal?", stufe1)
    if wahl == "__cancel__":
        return None

    # --- Einrichtungs-Zweige (laden/installieren, dann zurück ins /model) ---
    if wahl == "ollama_get":
        await setup_ollama()
        return None
    # Neuen Anbieter einrichten: auswählen -> Key eintippen -> speichern -> weiter
    if wahl == "cloudadd":
        opts = [(pid, f"☁  {p.label}  ·  {p.env}")
                for pid, p in P.PROVIDERS.items()
                if not p.available() and not p.keyless]
        opts.append(("__cancel__", "Abbrechen"))
        pid = await ask_confirm("Welchen Anbieter möchtest du einrichten?", opts)
        if pid == "__cancel__":
            return None
        prov = P.get(pid)
        ui.info(f"{prov.label}: Key holen unter  {P.KEY_URLS.get(pid, '(siehe Anbieter-Website)')}")
        key = await ask_text(f"{prov.label} API-Key einfügen (leer = abbrechen):", password=True)
        if not key:
            ui.warn("Kein Key eingegeben – abgebrochen.")
            return None
        if not config.set_env(prov.env, key):
            ui.error("Konnte den Key nicht in die .env schreiben.")
            return None
        wie = "verschlüsselt & sofort aktiv" if keyvault.available() else "gespeichert & sofort aktiv"
        ui.success(f"{prov.label}-Key {wie}. 🔑  {keyvault.mask(key)}")
        wahl = f"cloud:{pid}"            # direkt weiter zur Modellauswahl

    # --- Stufe 2: konkretes Modell ---
    if wahl == "ollama":
        ids = P.ollama_models()
        if not ids:
            ui.warn("Ollama läuft, hat aber keine Chat-Modelle (nur Embeddings?). "
                    "Mit  ollama pull <modell>  eins holen.")
            return None
        opts = [(M.make_ref("ollama", mid),
                 mid + ("  👁 Vision" if P.ollama_is_vision(mid) else ""))
                for mid in ids]
        opts.append(("__cancel__", "Zurück / Abbrechen"))
        ref = await ask_confirm("Welches Ollama-Modell?", opts)
        return None if ref == "__cancel__" else ref

    pid = wahl.split(":", 1)[1]
    prov = P.get(pid)
    try:
        with ui.thinking(f"frage {prov.label} nach verfügbaren Modellen"):
            ids = await P.list_models(pid)
    except Exception as e:
        ui.error(f"Modell-Liste von {prov.label} fehlgeschlagen: {e}")
        return None
    if not ids:
        ui.warn(f"{prov.label} hat keine passenden Chat-Modelle geliefert.")
        return None

    opts = [(M.make_ref(pid, mid), mid) for mid in ids]
    opts.append(("__cancel__", "Zurück / Abbrechen"))
    ref = await ask_confirm(f"Welches Modell von {prov.label}?", opts)
    return None if ref == "__cancel__" else ref


async def switch_model(ref: str, backend, model, strength):
    """Wechselt das aktive Modell. Gibt (backend, model) zurück (alt bei Fehler)."""
    carry = backend.messages if backend else None
    try:
        new_backend = await make_backend(ref, carry, strength)
    except Exception as e:
        ui.error(str(e))
        return backend, model
    config.update(model=ref)
    if M.split_ref(ref)[0] == "ollama":
        where = "lokal über Ollama"
    else:
        where = "in der Cloud"
    ui.success(f"Modell aktiv: {ref}  ({where})")
    return new_backend, ref


async def neue_persoenlichkeit() -> "PS.Persoenlichkeit | None":
    """Fragt Name, Kurzbeschreibung, Geschlecht und Tonfall ab und legt die
    Datei an. Gibt die neue Persönlichkeit zurück (oder None bei Abbruch)."""
    name = await ask_text("Wie soll die Persönlichkeit heißen? (leer = abbrechen)")
    if not name:
        ui.info("Abgebrochen.")
        return None
    kurz = await ask_text("In ein paar Worten: wie ist sie? (z.B. „ruhig, nüchtern, präzise“)")
    geschlecht = await ask_confirm("Wie spricht sie von sich?",
                                   PS.GESCHLECHT_OPTIONEN + [("__cancel__", "Abbrechen")])
    if geschlecht == "__cancel__":
        ui.info("Abgebrochen.")
        return None
    ton = await ask_confirm("Welcher Tonfall?",
                            PS.TON_OPTIONEN + [("__cancel__", "Abbrechen")])
    if ton == "__cancel__":
        ui.info("Abgebrochen.")
        return None
    pfad = PS.create(name, kurz, geschlecht, ton)
    neu = PS.list_all().get(pfad.stem)
    if neu is None:
        ui.error("Die Datei konnte nicht gelesen werden – seltsam. Schau in Persoenlichkeiten/.")
        return None
    PS.set_active(neu.key)
    ui.success(f"🎭 {neu.name} angelegt und aktiv – ab der nächsten Nachricht.")
    ui.info(f"Datei: {pfad}")
    oeffnen = await ask_confirm("Die Datei jetzt im Editor öffnen und weiter ausfeilen?",
                                [("ja", "Ja – öffnen"),
                                 ("nein", "Nein – erstmal so lassen")])
    if oeffnen == "ja":
        try:
            os.startfile(pfad)
        except Exception:
            ui.info(str(pfad))
        ui.info("Änderungen in der Datei gelten sofort – kein Neustart, kein /reset nötig.")
    return neu


def _ps_kurz(s: str, n: int = 70) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def _ps_bearbeiten(p: "PS.Persoenlichkeit") -> None:
    """Untermenü „Bearbeiten": Kopfzeilen, Geschlecht, Tonfall, Zeilen, Editor."""
    while True:
        p = PS.list_all().get(p.key)              # nach jeder Änderung frisch lesen
        if p is None:
            return
        wahl = await ask_confirm(f"✏️ {p.name} bearbeiten – was?", [
            ("name", f"Name ändern           ({p.name})"),
            ("kurz", f"Kurzbeschreibung      ({_ps_kurz(p.kurz or '–', 40)})"),
            ("geschlecht", "Geschlecht            (weiblich · männlich · neutral)"),
            ("ton", "Tonfall               (locker · sachlich · förmlich · verspielt)"),
            ("add", "Zeile hinzufügen      (z.B. „Du nennst mich Boss“)"),
            ("del", "Zeile entfernen"),
            ("editor", "Im Editor öffnen      (für größere Umbauten)"),
            ("__back__", "Zurück"),
        ])
        if wahl == "__back__":
            return
        try:
            if wahl == "name":
                neu = await ask_text(f"Neuer Name (leer = bleibt „{p.name}“):")
                if neu:
                    PS.set_name(p, neu)
                    if PS.active().key == p.key:
                        PS.set_active(p.key)
                    ui.success(f"🎭 Heißt jetzt {neu.strip()}.")
            elif wahl == "kurz":
                neu = await ask_text("Kurzbeschreibung fürs Menü (leer = entfernen):")
                PS.set_kurz(p, neu)
                ui.success("Kurzbeschreibung gespeichert.")
            elif wahl == "geschlecht":
                g = await ask_confirm("Wie spricht sie/er von sich?",
                                      PS.GESCHLECHT_OPTIONEN + [("__cancel__", "Abbrechen")])
                if g != "__cancel__":
                    PS.set_geschlecht(p, g)
                    ui.success("Geschlecht gesetzt.")
            elif wahl == "ton":
                tn = await ask_confirm("Welcher Tonfall?",
                                       PS.TON_OPTIONEN + [("__cancel__", "Abbrechen")])
                if tn != "__cancel__":
                    PS.set_ton(p, tn)
                    ui.success("Tonfall gesetzt.")
            elif wahl == "add":
                zeile = await ask_text("Neue Zeile (leer = abbrechen):")
                if zeile:
                    PS.add_line(p, zeile)
                    ui.success("Zeile angehängt.")
            elif wahl == "del":
                zeilen = PS.body_lines(p)
                if not zeilen:
                    ui.info("Da ist noch keine Zeile zum Entfernen.")
                    continue
                opts = [(str(i), _ps_kurz(text)) for i, text in zeilen[:40]]
                opts.append(("__cancel__", "Abbrechen"))
                z = await ask_confirm("Welche Zeile soll weg?", opts)
                if z != "__cancel__":
                    PS.remove_line(p, int(z))
                    ui.success("Zeile entfernt.")
            elif wahl == "editor":
                try:
                    os.startfile(p.path)
                except Exception:
                    ui.info(str(p.path))
                ui.info("Speichern genügt – gilt ab der nächsten Nachricht.")
                return
        except Exception as e:
            ui.error(f"Bearbeiten fehlgeschlagen: {e}")


async def _ps_profil(p: "PS.Persoenlichkeit") -> None:
    """Untermenü für ein einzelnes Profil: aktivieren, bearbeiten, kopieren, löschen."""
    aktiv = PS.active().key == p.key
    opts = [("on", "Aktivieren" + ("   (ist schon aktiv)" if aktiv else ""))]
    if p.eingebaut:
        opts.append(("copy", "Als Kopie anlegen     (die Eingebaute ist schreibgeschützt)"))
    else:
        opts += [("edit", "Bearbeiten"),
                 ("copy", "Als Kopie anlegen"),
                 ("del", "Löschen")]
    opts.append(("__back__", "Zurück"))
    wahl = await ask_confirm(f"🎭 {p.label}", opts)
    if wahl == "on":
        PS.set_active(p.key)
        ui.success(f"🎭 Persönlichkeit: {p.name} – gilt ab der nächsten Nachricht.")
    elif wahl == "edit":
        await _ps_bearbeiten(p)
    elif wahl == "copy":
        name = await ask_text(f"Name der Kopie (leer = „{p.name} Kopie“):")
        pfad = PS.duplicate(p, name)
        neu = PS.list_all().get(pfad.stem)
        ui.success(f"🎭 Kopie angelegt: {neu.name if neu else pfad.name}")
        if neu is not None:
            await _ps_bearbeiten(neu)
    elif wahl == "del":
        sicher = await ask_confirm(f"{p.name} wirklich löschen? (Datei {p.path.name})",
                                   [("ja", "Ja, löschen"), ("nein", "Nein")])
        if sicher == "ja":
            PS.delete(p)
            ui.success(f"🎭 {p.name} gelöscht." + ("  Nemi übernimmt." if aktiv else ""))


async def persoenlichkeiten_menu(arg: str) -> None:
    """/persönlichkeiten [name | neu] – Menü, direkt setzen oder neue anlegen."""
    arg = arg.strip()
    if arg.lower() in ("neu", "new", "anlegen", "+"):
        await neue_persoenlichkeit()
        return
    if arg:                                      # direkt gesetzt: /persönlichkeiten <name>
        p = PS.resolve(arg)
        if p is None:
            ui.warn(f"Keine Persönlichkeit gefunden, die zu '{arg}' passt. "
                    "/persönlichkeiten zeigt die Liste.")
            return
        PS.set_active(p.key)
        ui.success(f"🎭 Persönlichkeit: {p.name} – gilt ab der nächsten Nachricht.")
        return

    while True:
        alle = PS.list_all()
        aktiv = PS.active().key
        opts = [(k, ("✓  " if k == aktiv else "   ") + p.label) for k, p in alle.items()]
        opts.append(("__neu__", "➕  Neue Persönlichkeit anlegen (ein paar Fragen)"))
        opts.append(("__ordner__", "📂  Ordner öffnen (Dateien von Hand bearbeiten)"))
        opts.append(("__cancel__", "Fertig"))
        wahl = await ask_confirm("Wer soll sprechen?  (Profil wählen → aktivieren · bearbeiten · löschen)", opts)
        if wahl == "__cancel__":
            return
        if wahl == "__neu__":
            await neue_persoenlichkeit()
            continue
        if wahl == "__ordner__":
            PS.ensure_dir()
            try:
                os.startfile(PS.DIR)
            except Exception:
                ui.info(str(PS.DIR))
            ui.info("Eine .md-Datei pro Persönlichkeit – erste Überschrift = Name, "
                    "erste „> Zeile“ = Kurzbeschreibung. Platzhalter wie {{user}} und "
                    "{{current_time}} werden eingesetzt.")
            return
        await _ps_profil(alle[wahl])


def model_completions() -> dict[str, str]:
    """Vorschläge für /model: die Anbieter, für die ein Key gesetzt ist."""
    out: dict[str, str] = {}
    for pid, p in P.detected().items():
        out[pid] = p.label
    return out


class Ctx:
    """Veränderlicher Sitzungs-Status, den sich Klassik-Loop und TUI teilen."""

    def __init__(self, backend, model, strength, current_chat, current_prompts):
        self.backend = backend
        self.model = model
        self.strength = strength
        self.current_chat = current_chat
        self.current_prompts = current_prompts

    def strength_active(self) -> bool:
        """Nur tatsächlich angebundene Denk- oder Budgetsteuerung anzeigen."""
        return self.backend is not None and M.strength_supported(self.model)

    def sync_session(self) -> None:
        """Statusleisten-Daten (SESSION) an den aktuellen Stand angleichen."""
        SESSION.update(model=self.model or "—",
                       strength=self.strength if self.strength_active() else None,
                       chat=self.current_chat,
                       ctx_max=pricing.context_window(self.model))


async def handle_line(ctx: Ctx, text: str) -> str | None:
    """Verarbeitet EINE Eingabezeile (Befehl oder Chat). Gibt 'exit' zurück,
    wenn NemiCLI beendet werden soll – sonst None. Von Klassik-Loop UND TUI genutzt."""
    # „Nemi antwortet" (Rechtsklick): Auftrag trägt das Zielfenster im Präfix –
    # nach der Antwort wird der Text dort eingefügt.
    import kontextmenue
    einfuegen_hwnd, text = kontextmenue.antwort_tag(text)
    if einfuegen_hwnd is not None:
        n_vorher = len(ctx.backend.messages) if ctx.backend is not None else 0
        ergebnis = await handle_line(ctx, text)
        await _antwort_einfuegen(ctx, einfuegen_hwnd, n_vorher)
        return ergebnis
    cmd, arg = parse(text)

    # --- Slash-Befehle ----------------------------------------------------
    if cmd is not None:
        try:
            stats.record_command(cmd)
        except Exception:
            pass
        if cmd in ("/exit", "/quit"):
            return "exit"
        elif cmd == "/help":
            ui.help_panel()
        elif cmd == "/clear":
            # Wie beim Start: Banner + Status-Zeile + Chat-Hinweis + Maskottchen
            if ui.TUI is not None:
                ui.TUI.clear()
            else:
                ui.console.clear()
            ui.banner(animate=False)
            ui.welcome(model=ctx.model or "—", mode="Chat",
                       strength=ctx.strength if ctx.strength_active() else None)
            ui.info(f"📒 Chat #{ctx.current_chat}  ·  /resume <#> setzt einen alten fort")
            mascot.greet()
            ui.console.print()
        elif cmd == "/wissen":
            ui.knowledge_menu(learn.list_skills(), learn.list_snippets())
        elif cmd == "/gedaechtnis":
            sub = arg.strip().lower()
            if sub in ("leeren", "loeschen", "clear"):
                if not memory.count():
                    ui.info("Das Langzeitgedächtnis ist schon leer.")
                else:
                    ja = await ask_confirm(
                        f"Wirklich ALLE {memory.count()} Erinnerungen löschen?",
                        [("nein", "Nein, behalten"), ("ja", "Ja, alles löschen")])
                    if ja == "ja":
                        ui.success(f"{memory.clear()} Erinnerung(en) gelöscht.")
                    else:
                        ui.info("Abgebrochen – nichts gelöscht.")
            elif sub.startswith("vergiss") or sub.startswith("vergessen"):
                rest = sub.split(maxsplit=1)
                if len(rest) > 1 and rest[1].isdigit() and memory.forget(int(rest[1])):
                    ui.success(f"Erinnerung #{rest[1]} vergessen.")
                else:
                    ui.warn("Konnte das nicht. Nutzung: /gedaechtnis vergiss <nummer>")
            else:
                entries = memory.all_entries()
                smart = ("🧠 schlau (Qwen3-Embedding-0.6B, CPU)"
                         if memory.embedding_ready()
                         else "🔤 einfach (Stichwort) – sentence-transformers fehlt")
                ui.info(f"🧠 Langzeitgedächtnis: {len(entries)} Erinnerung(en)  ·  Suche: {smart}")
                if not entries:
                    ui.info("Noch nichts gemerkt. Sag z.B. 'merk dir, ich heisse Max' "
                            "oder NemiCLI merkt sich Wichtiges von selbst.")
                else:
                    for e in entries:
                        ui.console.print(f"  [dim]#{e['id']} · {e['ts']} · {e['kind']}[/dim]  {e['text']}")
                    ui.info("Löschen: /gedaechtnis vergiss <nummer>  ·  alles: /gedaechtnis leeren")
        elif cmd == "/aufraeumen":
            n = memory.count()
            if n < 2:
                ui.info("Zu wenig im Gedächtnis zum Aufräumen (mindestens 2 Notizen).")
            else:
                with ui.thinking("räume das Gedächtnis auf"):
                    r = await asyncio.to_thread(memory.consolidate)
                    await asyncio.to_thread(indexdb.after_turn)
                    memory.release_encoder()
                if r["merged"]:
                    ui.success(f"🗜️ Aufgeräumt: {r['merged']} ähnliche Notiz(en) verschmolzen "
                               f"({r['before']} → {r['after']}).")
                else:
                    ui.info(f"Alles schon ordentlich – nichts zu verschmelzen ({n} Notizen).")
        elif cmd == "/reflektieren":
            if ctx.backend is None:
                ui.warn("Kein Modell aktiv – mit /model eins wählen.")
            else:
                saved = await do_reflect(ctx)
                if saved:
                    ui.success(f"🪞 {len(saved)} Lehre(n) gemerkt:")
                    for art, t in saved:
                        ui.console.print(f"  [dim]{art}[/dim]  {t}")
                else:
                    ui.info("🪞 Nichts Bleibendes zum Lernen gefunden – alles gut.")
        elif cmd == "/start":
            await run_reich()
        elif cmd == "/einrichten":
            await run_wizard()
        elif cmd == "/systemcheck":
            import syscheck
            ui.info("🩺 Schaue mir deinen PC an … (ein paar Sekunden)")
            rep = await asyncio.to_thread(syscheck.report, True)
            ui.system_panel(rep, syscheck.todo(rep))
        elif cmd == "/ml":
            import foldersense
            ui.ml_panel(foldersense.stats(arg or os.getcwd()))
        elif cmd == "/ordner":
            import foldersense
            ziel = arg or os.getcwd()
            r = foldersense.classify(ziel)
            ui.info(f"📂 {ziel}")
            if r["empty"]:
                ui.warn("Leerer oder nicht lesbarer Ordner – keine Merkmale.")
            else:
                ui.success(f"Ordner-Typ (ML): {r['name']}  "
                           f"· {round(r['confidence']*100)} % sicher  "
                           f"· {r['tokens']} Merkmale")
            ui.info("Bekannte Typen: " + ", ".join(foldersense.labels().values()))
        elif cmd == "/emoji":
            ui.emoji_menu(emoji.EMOTICONS, emoji.SHORTCODES)
        elif cmd == "/web":
            import webfetch
            if arg.strip().lower() == "key":      # /web key -> Ollama-API-Key (Cloud + Suche)
                ui.info("Ollama-API-Key holen unter  https://ollama.com/settings/keys")
                key = await ask_text("Ollama-API-Key einfügen (leer = abbrechen):", password=True)
                if not key:
                    ui.warn("Kein Key eingegeben – abgebrochen.")
                elif config.set_env(webfetch.SEARCH_ENV, key):
                    wie = "verschlüsselt & " if keyvault.available() else ""
                    ui.success(f"Ollama-Suche aktiv 🔎  ({wie}sofort nutzbar)  {keyvault.mask(key)}")
                else:
                    ui.error("Konnte den Key nicht speichern.")
            else:
                status = ("🔎 Web-Suche (Ollama): aktiv" if webfetch.search_available()
                          else "🔎 Web-Suche (Ollama): aus – mit  /web key  einrichten")
                ui.info(status)
                eintraege = webfetch.entries()
                filt = arg.strip().lower()
                # deutsche Suchwörter auf die Kategorie-Namen der Liste abbilden
                filt = {"presse": "press", "behörde": "gov", "behoerde": "gov", "recht": "law",
                        "doku": "docs", "wissenschaft": "science", "sicherheit": "sec"}.get(filt, filt)
                if filt:                       # /web python  → nur passende Domains/Notizen
                    eintraege = [e for e in eintraege
                                 if filt in e.get("domain", "").lower()
                                 or filt in e.get("note", "").lower()
                                 or filt in e.get("category", "").lower()]
                    if not eintraege:
                        ui.warn(f"Keine Domain passt zu '{arg.strip()}'. /web zeigt alle "
                                f"{len(webfetch.entries())}.")
                        return None
                    ui.info(f"{len(eintraege)} von {len(webfetch.entries())} Domains passen zu "
                            f"'{arg.strip()}'.")
                else:
                    ui.info(f"{len(eintraege)} Domains · Filter: /web <wort>  (z.B. /web python, /web presse)")
                ui.web_menu(eintraege)
        elif cmd == "/uebung":
            sub = arg.strip().lower()
            if sub in ("aus", "off", "stop"):
                if ui.TUI is not None:
                    ui.TUI.practice_on = False
                    ui.TUI._stop_practice()
                ui.info("🎓 Übungsmodus aus.")
            elif sub in ("an", "on"):
                if ui.TUI is not None:
                    ui.TUI.practice_on = True
                    ui.TUI._practice_consent = None      # Cloud-Frage neu stellen
                ui.info("🎓 Übungsmodus an – nach ~10 min Ruhe wird eine Übung vorbereitet. "
                        "Schreiben und Ausführen erst nach deiner Codefreigabe.")
            elif sub in ("jetzt", "now"):
                if ctx.backend is None:
                    ui.warn("Kein Modell aktiv – erst mit /model eins wählen.")
                else:
                    if ui.TUI is not None:
                        await ui.TUI.run_practice_once(ctx, practice_round)
                    else:
                        await practice_round(ctx, _esc_pressed)
            else:
                an = ui.TUI is not None and getattr(ui.TUI, "practice_on", False)
                ui.info(f"🎓 Übungsmodus ist {'an' if an else 'aus'}. "
                        "Nach ~10 min Ruhe bereitet NemiCLI Programme vor; vor jedem "
                        "Schreiben und Ausführen prüfst du den vollständigen Code. "
                        "/uebung an · aus · jetzt")
        elif cmd == "/kontextmenue":
            sub = arg.strip().lower()
            import kontextmenue
            try:
                if sub in ("an", "on", "ein"):
                    labels = kontextmenue.installieren()
                    ui.success("🖱 Eingetragen: " + "  ·  ".join(labels))
                    ui.info("Windows 11 zeigt sie unter „Weitere Optionen anzeigen“ (Shift+F10). "
                            "Ein Klick öffnet NemiCLI und schickt „Schau dir dieses Bild an“ gleich ab.")
                elif sub in ("aus", "off"):
                    n = kontextmenue.entfernen()
                    ui.info(f"🖱 Rechtsklick-Einträge entfernt ({n} Schlüssel)." if n
                            else "🖱 Es war nichts eingetragen.")
                else:
                    st = kontextmenue.status()
                    an = st["bildschirm"] and st["bild"]
                    ui.info(f"🖱 Rechtsklick-Menü ist {'an' if an else 'aus'}.  "
                            "/kontextmenue an · aus")
            except Exception as e:
                ui.warn(f"Rechtsklick-Menü: {e}")
        elif cmd == "/subagenten":
            sub = arg.strip().lower()
            texte = {"an": "🤝 Helfer an – vor jedem Losschicken wirst du gefragt.",
                     "auto": "🤝 Helfer auto – Lara nimmt sich Helfer nach Bedarf, ohne Frage.",
                     "off": "🤝 Helfer aus – das Werkzeug ist gesperrt."}
            if sub:
                neu = subagents.set_stufe(sub)
                if neu is None:
                    ui.warn("Unbekannte Stufe. /subagenten an · auto · off")
                else:
                    ui.info(texte[neu] + " (gespeichert)")
            else:
                st = subagents.stufe()
                n = subagents.aktiv()
                laufen = f"  ·  gerade aktiv: {n}" if n else ""
                ui.info(f"{texte[st]}{laufen}  /subagenten an · auto · off")
        elif cmd == "/auto":
            sub = arg.strip().lower()
            if sub in ("an", "on", "aus", "off"):
                an = sub in ("an", "on")
                config.update(auto_stark=an)
                # am laufenden Backend sofort wirksam machen (kein Neustart nötig)
                if ctx.backend is not None and getattr(ctx.backend, "provider_id", "") == "ollama":
                    ziel = P.gemma_upgrade_ziel(ctx.backend.model_id) if an else None
                    ctx.backend.auto_stark = bool(an and ziel)
                    ctx.backend.auto_ziel = ziel
                if not an:
                    ui.info("🧠 Auto-Stark aus – es antwortet immer das gewählte Modell.")
                else:
                    ziel = (P.gemma_upgrade_ziel(ctx.backend.model_id)
                            if ctx.backend is not None
                            and getattr(ctx.backend, "provider_id", "") == "ollama" else None)
                    if ziel:
                        ui.info(f"🧠 Auto-Stark an – bei schweren Fragen übernimmt {ziel}.")
                    else:
                        ui.info("🧠 Auto-Stark an (gespeichert). Greift, sobald ein leichtes "
                                "Gemma aktiv ist, zu dem ein größeres installiert ist.")
            else:
                an = bool(config.load().get("auto_stark"))
                aktiv = (ctx.backend is not None
                         and getattr(ctx.backend, "auto_stark", False))
                zusatz = f" · aktiv → {ctx.backend.auto_ziel}" if aktiv else ""
                ui.info(f"🧠 Auto-Stark ist {'an' if an else 'aus'}{zusatz}.  "
                        "Schwere Frage → automatisch großes Gemma.  /auto an · aus")
        elif cmd in PERSONA_CMDS:
            await persoenlichkeiten_menu(arg)
        elif cmd == "/modus":
            sub = arg.strip().lower()
            if not sub:
                zeilen = [(("✓ " if k == modes.current() else "  ") + f"{sym} {n}",
                           {"chat": "Gespräch & Planen – jede Aktion fragt, Merken frei",
                            "lesen": "alles lesen, nichts anrühren – Ändern gesperrt",
                            "normal": "Lesen sofort, Ändern mit Rückfrage (F8)",
                            "auto": f"ohne Rückfragen im aktuellen Ordner · Zünder {modes.AUTO_FUSE_S // 60} min"}[k])
                          for k, sym, n in modes.MODES]
                ui.kv_panel(f"Arbeitsmodus: {modes.label()}   ·   Shift+Tab schaltet weiter", zeilen)
                return None
            key = modes.resolve(sub)
            if key is None:
                ui.warn("Unbekannter Modus. Zur Wahl: chat · lesen · normal · auto")
                return None
            modes.set_mode(key)
            ui.success(f"Modus: {modes.label()}" + (
                f"  ·  Zünder läuft: nach {modes.AUTO_FUSE_S // 60} min zurück in Chatten"
                if key == "auto" else ""))
        elif cmd == "/statistik":
            if arg.strip().lower() in ("reset", "leeren", "loeschen", "löschen"):
                sicher = await ask_confirm("Wirklich alle gesammelten Zahlen löschen? "
                                           "(die Chats selbst bleiben)",
                                           [("ja", "Ja, zurücksetzen"), ("nein", "Nein")])
                if sicher == "ja":
                    stats.reset()
                    ui.success("📊 Statistik zurückgesetzt – wir fangen frisch an.")
                return None
            ui.stats_panel(stats.report())
        elif cmd == "/protokoll":
            n = int(arg) if arg.strip().isdigit() else 30
            zeilen = protokoll.letzte(n)
            if not zeilen:
                ui.info(f"📜 Noch keine Einträge. Datei: {protokoll.PFAD}")
            else:
                ui.text_panel(f"📜 Aktions-Protokoll – letzte {len(zeilen)}",
                              "\n".join(zeilen))
                ui.info(f"Datei: {protokoll.PFAD}")
        elif cmd == "/version":
            ui.kv_panel("NemiCLI", VER.report(model=ctx.model, persona=PS.active().name))
        elif cmd == "/selbsttest":
            zeilen: list[str] = []
            try:
                await _run_with_status("🔧 Selbsttest läuft …",
                                       lambda st: selftest(out=zeilen.append))
            except Exception as e:
                ui.error(f"Selbsttest abgebrochen: {e}")
            ui.text_panel("🔧 Selbsttest", "\n".join(zeilen))
        elif cmd == "/update":
            grund = updater.why_not()
            if grund:
                ui.warn(f"⬇ /update geht hier nicht: {grund}")
                return None
            try:
                res = await _run_with_status("⬇ Update", updater.run)
            except Exception as e:
                ui.error(f"Update fehlgeschlagen: {e}")
                return None
            if not res["ok"]:
                ui.warn("⬇ Update nicht durchgeführt.")
                ui.text_panel("Git sagt", res["output"] or "(keine Ausgabe)")
                return None
            if not res["changed"]:
                ui.success(f"⬇ Schon aktuell ({res['before']}).")
                return None
            ui.success(f"⬇ Aktualisiert: {res['before']} → {res['after']}  "
                       f"({len(res['files'])} Datei(en))")
            if res["files"]:
                ui.text_panel("Geändert", "\n".join(res["files"][:40]))
            if res["requirements"]:
                w = await ask_confirm("requirements.txt hat sich geändert – Abhängigkeiten jetzt "
                                      "nachinstallieren (pip)?",
                                      [("ja", "Ja, installieren"), ("nein", "Später selbst")])
                if w == "ja":
                    ok, tail = await _run_with_status("📦 pip install", updater.install_requirements)
                    (ui.success if ok else ui.error)("📦 " + ("Abhängigkeiten aktuell." if ok
                                                           else "pip meldet Fehler:"))
                    if tail:
                        ui.text_panel("pip", tail)
            ui.info("Neustart nötig, damit der neue Code läuft: /exit, dann nemicli.")
        elif cmd in ("/workspace", "/workspaceend"):
            await workspace_befehl(ctx, "end" if cmd == "/workspaceend" else arg)
        elif cmd == "/nemi":
            mascot.bubble()
        elif cmd == "/chrome":
            import chromebridge
            sub = arg.strip().lower()
            if sub == "neu":
                k = chromebridge.key(neu=True)
                if CHROME_BRIDGE is not None:
                    CHROME_BRIDGE.key = k
                ui.success("🐈 Neuer Schlüssel erzeugt – in der Erweiterung neu eintragen.")
            k = chromebridge.key()
            laeuft = CHROME_BRIDGE is not None and CHROME_BRIDGE.laeuft
            ordner = paths.INSTALL / "chrome-erweiterung"
            ui.kv_panel("🐈 Chrome-Erweiterung", [
                ("Empfang", ("läuft auf " + CHROME_BRIDGE.adresse) if laeuft else
                 ("AUS – " + (CHROME_BRIDGE.letzter_fehler if CHROME_BRIDGE else "nicht gestartet"))),
                ("Schlüssel", k),
                ("Ordner", str(ordner)),
                ("Einrichten", "Chrome → chrome://extensions → Entwicklermodus an → "
                               "„Entpackte Erweiterung laden“ → diesen Ordner wählen"),
                ("Dann", "Erweiterung → Details → Erweiterungsoptionen → Port + Schlüssel eintragen"),
                ("Nutzen", "Rechtsklick auf einer Seite → „Nemi antwortet“ (Cursor im Textfeld) "
                           "oder Text markieren → „Nemi, erklär mir das“"),
            ])
        elif cmd == "/webui":
            global WEB_BRIDGE
            if WEB_BRIDGE is not None:
                ui.info(f"🌐 WebUI läuft schon: {WEB_BRIDGE.url}")
                try:
                    webbrowser.open(WEB_BRIDGE.url)
                except Exception:
                    pass
            else:
                try:
                    WEB_BRIDGE = await start_webui(ctx)
                except Exception as e:
                    ui.error(f"WebUI-Start fehlgeschlagen: {e}")
                else:
                    ui.success(f"🌐 WebUI offen: {WEB_BRIDGE.url}")
                    ui.info("Im Browser bedienbar – das Terminal bleibt verbunden und macht "
                            "die Arbeit. Lesehilfen (Schrift/Größe/Kontrast/Vorlesen) oben rechts. "
                            "/exit beendet beides.")
                    try:
                        webbrowser.open(WEB_BRIDGE.url)
                    except Exception:
                        pass
        elif cmd == "/reset":
            if ctx.backend:
                ctx.backend.reset()
            ctx.current_prompts = []
            ctx.current_chat = chatstore.next_id()
            mitschrift.leeren()
            ui.success(f"Neuer Chat #{ctx.current_chat} gestartet.")
        elif cmd == "/resume":
            if not arg:
                ui.chats_menu(chatstore.list_chats())
            elif not arg.isdigit():
                ui.warn("Bitte eine Chat-Nummer angeben, z.B. /resume 1.")
            else:
                data = chatstore.load(int(arg))
                if not data:
                    ui.warn(f"Chat #{arg} gibt es nicht. /resume zeigt die Liste.")
                elif ctx.backend is None:
                    ui.warn("Erst ein Modell wählen (/model), dann fortsetzen.")
                else:
                    ctx.backend.messages = data.get("messages", [])
                    ctx.current_prompts = data.get("prompts", [])
                    ctx.current_chat = int(arg)
                    mitschrift.leeren()
                    mitschrift.notiz(
                        f"Chat #{arg} fortgesetzt - {len(ctx.current_prompts)} fruehere "
                        "Runden sind in chats/ gespeichert, ihr Denktext aber nicht.")
                    ui.success(f"Chat #{ctx.current_chat} fortgesetzt: {data.get('title', '')}")
                    ui.info(f"{len(ctx.current_prompts)} frühere Runden geladen.")
        elif cmd == "/theme":
            if not arg:
                ui.theme_menu()
            elif ui.set_theme(arg):
                config.update(theme=arg)
                if ui.TUI is not None:
                    ui.TUI.clear()
                else:
                    ui.console.clear()
                ui.banner(animate=False)
                ui.welcome(model=ctx.model or "—", mode="Chat",
                           strength=ctx.strength if ctx.strength_active() else None)
            else:
                ui.warn(f"Theme '{arg}' gibt es nicht. Verfügbar: {', '.join(ui.THEMES)}")
        elif cmd == "/model":
            ref = None
            if not arg:
                ref = await pick_model(ctx.model)
            else:
                tokens = arg.split()
                head = tokens[0].lower()
                if ":" in arg:                       # direkte Referenz: anbieter:modell
                    ref = arg.strip()
                elif len(tokens) > 1 and P.get(head):   # "openai gpt-4o"
                    ref = M.make_ref(head, tokens[1])
                elif P.get(head):
                    if not P.get(head).available():
                        prov = P.get(head)
                        ui.warn(f"Für {prov.label} ist noch kein Key gesetzt.")
                        ui.info("Tippe /model (ohne Text) und wähle "
                                "'Cloud-Anbieter hinzufügen', um den Key direkt einzugeben.")
                        picked = "__cancel__"
                    else:
                        try:
                            with ui.thinking(f"frage {P.get(head).label}"):
                                ids = await P.list_models(head)
                            opts = [(M.make_ref(head, m), m) for m in ids]
                            opts.append(("__cancel__", "Abbrechen"))
                            picked = await ask_confirm(f"Welches Modell von {P.get(head).label}?", opts)
                        except Exception as e:
                            ui.error(str(e)); picked = "__cancel__"
                    ref = None if picked == "__cancel__" else picked
                else:
                    ui.warn(f"Unbekannt: '{arg}'. Tippe /model (ohne Text) für die Auswahl.")
            if ref:
                ctx.backend, ctx.model = await switch_model(ref, ctx.backend, ctx.model, ctx.strength)
        elif cmd == "/bild":
            import imagegen
            # Nur den AKTIVEN Motor pruefen: laeuft ComfyUI/WebUI, ist diffusers
            # voellig gleichgueltig. Vorher hing beides an derselben Pruefung.
            reason = imagegen.missing_reason_aktiv()
            if reason:
                ui.warn("Der Bild-Motor kann gerade nicht malen.")
                ui.info(reason)
                return None
            import sdwebui
            akt_backend = imagegen.backend()
            opts = imagegen.parse_opts(arg)
            cks = imagegen.menu()
            # Ohne Prompt: Status + Hilfe zeigen
            if not opts["prompt"]:
                ui.info(f"🎨 Aktiver Bild-Motor: {imagegen.active_label()}")
                if akt_backend == "webui":
                    ui.info(f"🌐 WebUI ({sdwebui.host()}) – kein Negativ-Prompt, CFG 1, "
                            "1024×1024, 14 Schritte. /bildmodel wechselt das Modell.")
                elif cks:
                    ui.info("Checkpoints (in Models/checkpoints/):")
                    for ref, label in cks.items():
                        ui.console.print(f"  [dim]·[/dim] {label}")
                else:
                    ui.warn("Noch kein Modell da. Lege eine .safetensors (z.B. von Civitai) in:")
                    ui.console.print(f"  [dim]{imagegen.CKPT_DIRS[0]}[/dim]")
                ui.info('Nutzung:  /bild <beschreibung>  [--neg "..."] [--steps 28] '
                        '[--cfg 7] [--size 768x768] [--seed 123] [--model name]')
                return None
            if akt_backend == "builtin" and not cks:
                ui.warn("Kein Checkpoint gefunden. Lege eine .safetensors-Datei in:")
                ui.console.print(f"  [dim]{imagegen.CKPT_DIRS[0]}[/dim]")
                return None
            try:
                # --no-face schaltet die Gesichts-Nachbesserung ganz ab; sonst an.
                will_face = opts.get("adetailer")
                will_face = True if will_face is None else will_face
                path, nachbessern = await _run_with_status(
                    "🎨 male dein Bild",
                    lambda on_status: imagegen.paint(
                        opts["prompt"], model=opts.get("model"), neg=opts.get("neg"),
                        steps=opts.get("steps"), cfg=opts.get("cfg"),
                        size=opts.get("size"), seed=opts.get("seed"),
                        sampler=opts.get("sampler"), karras=opts.get("karras"),
                        on_status=on_status))
                ui.success(f"🖼 Fertig: {path}")
                _bild_vorschau(str(path))
                # Automatisch nachbessern (Gesicht/Augen) – nur bei der eigenen
                # Pipeline sinnvoll (OpenCV-img2img passt nicht zu WebUI-Modellen).
                anzeigen = path
                if will_face and nachbessern:
                    try:
                        gespeichert = await _run_with_status(
                            "✨ bessere Gesicht & Augen nach",
                            lambda on_status: imagegen.auto_nachbessern(path, on_status=on_status))
                        if gespeichert:
                            anzeigen = gespeichert
                            ui.success(f"✨ Nachgebessert: {gespeichert}")
                    except Exception as e:
                        ui.warn(f"Nachbessern übersprungen ({e}).")
                try:
                    os.startfile(anzeigen)        # fertiges Bild anzeigen (Windows)
                except Exception:
                    pass
            except Exception as e:
                ui.error(f"Bild fehlgeschlagen: {e}")
        elif cmd == "/bildmodel":
            import imagegen, sdwebui, comfyui
            cks = imagegen.menu()
            aktuell = imagegen.default_model()
            akt_backend = imagegen.backend()
            webui_da = sdwebui.available()
            webui_models = sdwebui.models() if webui_da else {}
            webui_aktiv = sdwebui.chosen_model() if akt_backend == "webui" else None
            comfy_da = comfyui.available()
            comfy_models = comfyui.models() if comfy_da else {}
            comfy_aktiv = comfyui.chosen_model() if akt_backend == "comfy" else None
            if arg:                                  # direkt gesetzt: /bildmodel <name>
                if arg.strip().lower() in ("comfy-adresse", "comfyui-adresse", "comfy"):
                    neu_adr = await ask_text(f"ComfyUI-Adresse (jetzt: {comfyui.host()}):")
                    if neu_adr:
                        comfyui.set_host(neu_adr)
                        ui.success(f"🧩 ComfyUI-Adresse: {comfyui.host()}  "
                                   f"({'erreichbar' if comfyui.available() else 'nicht erreichbar'})")
                    return None
                if arg.strip().lower() in ("webui-adresse", "host", "url"):
                    neu = await ask_text(f"WebUI-Adresse (jetzt: {sdwebui.host()}):")
                    if neu:
                        sdwebui.set_host(neu)
                        ui.success(f"🌐 WebUI-Adresse: {sdwebui.host()}  "
                                   f"({'erreichbar' if sdwebui.available() else 'nicht erreichbar'})")
                    return None
                # erst eigene Checkpoints, dann WebUI-Modelle
                ck = imagegen.resolve(arg)
                if ck is not None:
                    imagegen.set_default_model(ck.ref)
                    ui.success(f"🎨 Bild-Modell (eigene Pipeline): {ck.label}")
                    return None
                treffer = [n for n in webui_models if arg.strip().lower() in n.lower()]
                if len(treffer) == 1:
                    sdwebui.set_chosen_model(treffer[0])
                    config.update(bild_backend="webui")
                    ui.success(f"🌐 Bild-Modell (WebUI): {treffer[0]}")
                    return None
                treffer = [n for n in comfy_models if arg.strip().lower() in n.lower()]
                if len(treffer) == 1:
                    comfyui.set_chosen_model(treffer[0])
                    config.update(bild_backend="comfy")
                    ui.success(f"🧩 Bild-Modell (ComfyUI): {treffer[0]}")
                    return None
                ui.warn(f"Kein Modell gefunden, das zu '{arg}' passt "
                        "(weder eigene Pipeline noch WebUI noch ComfyUI).")
                return None
            opts = [(f"builtin::{ref}",
                     ("✓  " if (akt_backend == "builtin" and ref == aktuell) else "   ")
                     + f"🖥 {label}")
                    for ref, label in cks.items()]
            for name, title in webui_models.items():
                mark = "✓  " if (akt_backend == "webui" and name == webui_aktiv) else "   "
                opts.append((f"webui::{name}", f"{mark}🌐 {name}  (WebUI)"))
            for name, art in comfy_models.items():
                mark = "✓  " if (akt_backend == "comfy" and name == comfy_aktiv) else "   "
                # Getrennte Modelle (Krea/Qwen/Flux) sind KEINE Checkpoints –
                # dazu gehören CLIP und VAE als eigene Dateien.
                zusatz = "ComfyUI · geteilt" if art == "diffusion" else "ComfyUI"
                opts.append((f"comfy::{name}", f"{mark}🧩 {name}  ({zusatz})"))
            opts.append(("__ordner__", "📂  Ordner öffnen (eigene .safetensors ablegen)"))
            opts.append(("__host__", f"🌐  WebUI-Adresse ändern  ({sdwebui.host()})"))
            opts.append(("__chost__", f"🧩  ComfyUI-Adresse ändern  ({comfyui.host()})"
                                      + ("" if comfy_da else "  – nicht erreichbar")))
            opts.append(("__cancel__", "Abbrechen"))
            if comfy_da and not comfy_models:
                i = comfyui.info()
                ui.warn(f"🧩 ComfyUI {i['version']} läuft ({i['gpu']}, {i['vram_gb']} GB), "
                        "kennt aber keinen Checkpoint.")
                ui.info("Leg eins in ComfyUIs models/checkpoints – oder zeig ComfyUI "
                        "per extra_model_paths.yaml auf deinen vorhandenen Ordner:")
                ui.console.print(f"  [dim]{imagegen.CKPT_DIRS[0]}[/dim]")
            if not cks and not webui_models and not comfy_models:
                ui.warn("Noch kein Bild-Modell da. Entweder eine .safetensors hier ablegen:")
                ui.console.print(f"  [dim]{imagegen.CKPT_DIRS[0]}[/dim]")
                ui.info("… oder eine Forge/A1111-WebUI mit --api starten "
                        f"(erwartet unter {sdwebui.host()}).")
            wahl = await ask_confirm("Welches Modell zum Bildermalen?  "
                                     "(🖥 eigene Pipeline · 🌐 Forge/A1111 · 🧩 ComfyUI)",
                                     opts)
            if wahl == "__cancel__":
                return None
            if wahl == "__ordner__":
                imagegen.CKPT_DIRS[0].mkdir(parents=True, exist_ok=True)
                try:
                    os.startfile(imagegen.CKPT_DIRS[0])
                except Exception:
                    ui.info(str(imagegen.CKPT_DIRS[0]))
                ui.info("Datei reinlegen, dann nochmal /bildmodel – sie taucht "
                        "automatisch auf.")
                return None
            if wahl == "__host__":
                neu = await ask_text(f"WebUI-Adresse (jetzt: {sdwebui.host()}):")
                if neu:
                    sdwebui.set_host(neu)
                    ui.success(f"🌐 WebUI-Adresse: {sdwebui.host()}  "
                               f"({'erreichbar' if sdwebui.available() else 'nicht erreichbar'})")
                return None
            if wahl == "__chost__":
                neu = await ask_text(f"ComfyUI-Adresse (jetzt: {comfyui.host()}):")
                if neu:
                    comfyui.set_host(neu)
                    ui.success(f"🧩 ComfyUI-Adresse: {comfyui.host()}  "
                               f"({'erreichbar' if comfyui.available() else 'nicht erreichbar'})")
                return None
            art, _, name = wahl.partition("::")
            if art == "comfy":
                comfyui.set_chosen_model(name)
                config.update(bild_backend="comfy")
                i = comfyui.info()
                ui.success(f"🧩 Bild-Modell (ComfyUI): {name}")
                ui.info(f"ComfyUI {i['version']} · {i['gpu']} · "
                        f"{comfyui.DEFAULTS['steps']} Schritte, CFG "
                        f"{comfyui.DEFAULTS['cfg']}, "
                        f"{comfyui.DEFAULTS['size'][0]}×{comfyui.DEFAULTS['size'][1]}  "
                        "(per bild_comfy_* in der Config änderbar)")
            elif art == "webui":
                sdwebui.set_chosen_model(name)
                config.update(bild_backend="webui")
                ui.success(f"🌐 Bild-Modell (WebUI): {name}  ·  kein Negativ-Prompt, "
                           "CFG 1, 1024×1024, 14 Schritte")
            else:
                imagegen.set_default_model(name)
                ui.success(f"🎨 Bild-Modell (eigene Pipeline): {cks.get(name, name)}")
        elif cmd == "/bearbeiten":
            import imgwin
            # Hier bleibt die harte Pruefung mit Absicht: /bearbeiten malt einen
            # Bildbereich mit imagegen.repaint_region() neu - das IST die eigene
            # img2img-Pipeline. ComfyUI hilft dabei nicht, also braucht es hier
            # wirklich torch/diffusers.
            reason = None
            try:
                import imagegen
                reason = imagegen.missing_reason()
            except Exception as e:
                reason = str(e)
            if reason:
                ui.warn("Fürs Bearbeiten fehlen noch Bibliotheken.")
                ui.info(reason)
                return None
            pfad = arg.strip('"') if arg else imgwin.neuestes_bild()
            if not pfad or not Path(pfad).exists():
                ui.warn("Kein Bild gefunden. Erst eins mit /bild malen – oder "
                        "/bearbeiten <pfad> angeben.")
                return None
            ui.info(f"🖼 Fenster geöffnet: {Path(pfad).name}  ·  "
                    "Rahmen ziehen → Neu malen → Speichern")
            try:
                gespeichert = await imgwin.open_editor(pfad)
            except Exception as e:
                ui.error(f"Bearbeiten-Fenster fehlgeschlagen: {e}")
                return None
            if gespeichert:
                ui.success(f"🖼 Gespeichert: {gespeichert}")
            else:
                ui.info("Fenster geschlossen – nichts gespeichert.")
        elif cmd == "/staerke":
            options = M.strength_menu(ctx.model)
            if not ctx.strength_active() or not options:
                ui.info("Für das aktive Modell ist noch keine Denksteuerung angebunden. "
                        "Seine vorhandene Denk-Ausgabe wird weiterhin angezeigt.")
                return None
            if not arg:
                ui.strength_menu(options, current=ctx.strength)
                ui.info(M.strength_note(ctx.model, ctx.strength))
            elif arg in options:
                ctx.strength = arg
                config.update(strength=arg)
                ctx.backend.strength = arg
                ctx.sync_session()
                ui.success(f"Denken: {arg}")
                ui.info(M.strength_note(ctx.model, arg))
            else:
                ui.warn(f"Unbekannte Stärke. Optionen: {', '.join(options)}")
        else:
            ui.warn(f"Unbekannter Befehl: {cmd}. Tippe /help.")
        return None

    # --- Normaler Chat ----------------------------------------------------
    if not text.strip():
        return None
    if ctx.backend is None:
        ui.warn("Kein Modell aktiv. Wähle eins mit /model (z.B. /model gemma-12b).")
        return None

    text = emoji.expand(text)   # =)  <3  :feuer:  ->  echte Emojis

    # Drag & Drop: Windows Terminal setzt nur den PFAD in die Eingabe. Text,
    # Markdown, Quelltext, PDF haengen wir als Daten an; Bilder holt sich
    # vision gleich danach; Binaeres (exe, zip, safetensors) bleibt liegen.
    dateien = anhang.anhaengen(text)
    text = dateien["text"]
    if (h := anhang.hinweis(dateien)):
        for zeile in h.splitlines():
            (ui.warn if "übersprungen" in zeile else ui.info)(zeile)

    angehaengt = vision.find_images(text)
    text, imgs, hinweis = await _bilder_fuer_chat(ctx.model, text)
    if hinweis:
        (ui.warn if "fehlt" in hinweis or "Helfer:" in hinweis else ui.info)(hinweis)
    for _p in angehaengt[:3]:
        _bild_vorschau(str(_p), title=f"angehängt: {_p.name}")

    try:
        async with TURN_LOCK:        # nur ein Zug gleichzeitig (Terminal oder Browser)
            await converse(ctx.backend, text, images=imgs)
            ctx.current_prompts.append(text)
            chatstore.save(ctx.current_chat, ctx.backend.messages, ctx.current_prompts, ctx.model or "—")
            n_code = learn.capture_from_answer(text, ctx.backend.messages, ctx.current_chat)
            await asyncio.to_thread(indexdb.after_turn, ctx.current_chat)
            memory.release_encoder()
        if n_code:
            ui.info(f"🐍 {n_code} Code-Snippet(s) in den Wissensspeicher gelernt.")
        # Terminal-Antwort auch im offenen Browser spiegeln (Status & Hinweis)
        if WEB_BRIDGE is not None:
            WEB_BRIDGE.broadcast({"t": "info", "text": "(im Terminal beantwortet)"})
            WEB_BRIDGE.broadcast({"t": "state", "model": ctx.model or "—",
                                  "chat": ctx.current_chat, "tokens": SESSION["tokens"],
                                  "strength": ctx.strength if ctx.strength_active() else None})
        mascot.maybe()
    except Exception as e:
        ui.error(f"Fehler bei der Antwort: {e}")
    return None


async def _antwort_einfuegen(ctx: "Ctx", hwnd: int, n_vorher: int) -> None:
    """Letzte Assistenten-Antwort dieser Runde säubern und ins Zielfenster tippen
    (hwnd 0: nur Zwischenablage)."""
    import kontextmenue
    if ctx.backend is None:
        return
    neu = ctx.backend.messages[n_vorher:]
    antworten = [m.get("content") for m in neu
                 if m.get("role") == "assistant" and isinstance(m.get("content"), str)]
    if not antworten:
        return
    text = kontextmenue.antwort_text(antworten[-1])
    if not text:
        return
    if hwnd:
        ok = await asyncio.to_thread(kontextmenue.einfuegen, hwnd, text)
        titel = kontextmenue.fenster_titel(hwnd) if ok else ""
        if ok:
            ui.success(f"✍ Antwort eingefügt in: {titel[:60] or 'Zielfenster'}")
            return
    kontextmenue.in_zwischenablage(text)
    ui.info("📋 Antwort in der Zwischenablage (Zielfenster nicht erreichbar) – Strg+V zum Einfügen.")


def _start_nachricht() -> str | None:
    """`nemicli --sag "…"`: Text, der beim Start als erste Nachricht abgeschickt wird."""
    if "--sag" not in sys.argv:
        return None
    i = sys.argv.index("--sag")
    text = " ".join(sys.argv[i + 1:]).strip()
    return text or None


async def run_classic(ctx: Ctx) -> None:
    """Klassischer scrollender Loop (Eingabe inline, prompt_toolkit-Session)."""
    session = PromptSession(
        history=FileHistory(str(paths.ROOT / ".cli_history")),
        completer=SlashCompleter(get_themes=lambda: ui.THEMES, get_models=model_completions,
                                 get_strengths=lambda: M.strength_menu(ctx.model),
                                 get_chats=chatstore.chat_args,
                                 get_checkpoints=lambda: __import__("imagegen").menu(),
                                 get_personas=PS.menu),
        complete_while_typing=True,
        rprompt=mascot.rprompt,
        bottom_toolbar=lambda: ui.bottom_toolbar(SESSION),
        refresh_interval=0.3,
    )
    while True:
        ctx.sync_session()
        ui.console.print(ui.input_divider())
        try:
            text = await session.prompt_async(mascot.lprompt, style=ui.PROMPT_STYLE)
        except (KeyboardInterrupt, EOFError):
            break
        ui.console.print(ui.input_divider())   # zweite Linie -> Eingabe eingerahmt
        if await handle_line(ctx, text) == "exit":
            break


async def main():
    cfg = config.load()

    # Sicherheitsnetz: Maus-Tracking beim Programm-Ende IMMER zurücksetzen – auch
    # wenn NemiCLI unerwartet endet. Sonst flutet das Terminal die Shell danach mit
    # Maus-Zeichen (`[555;73;46M`). Läuft zusätzlich zum Cleanup in screen.run().
    import atexit
    try:
        import screen as _sc
        atexit.register(_sc.maus_tracking_aus)
    except Exception:
        pass

    # Noch im Klartext liegende API-Keys in den Windows-Umschlag packen (einmalig).
    secured_keys = config.secure_existing_keys()

    # Gemerktes Theme wiederherstellen (vor dem Banner, damit es passt)
    if cfg.get("theme") in ui.THEMES:
        ui.set_theme(cfg["theme"])

    # Vollbild-TUI (Standard) oder klassischer Loop (NEMICLI_CLASSIC=1 als Fallback)
    tui = None
    if os.getenv("NEMICLI_CLASSIC") != "1":
        try:
            # Standard: Textual-Oberfläche (ui/screen_tx.py). NEMICLI_TUI=alt
            # schaltet auf die bisherige prompt_toolkit-Fassung (ui/screen.py) zurück.
            if os.getenv("NEMICLI_TUI", "").lower() in ("alt", "old", "pt"):
                import screen as _screenmod
            else:
                import screen_tx as _screenmod
            tui = _screenmod.Screen()
            tui.install()          # ui.console -> Scroll-Puffer, ui.TUI = tui
        except Exception as e:
            tui = None
            ui.TUI = None
            ui.error(f"Vollbild-TUI konnte nicht starten ({e}) – nutze klassischen Modus.")

    ui.banner(animate=(tui is None))   # klassisch animiert, im TUI statisch in den Puffer

    backend = None
    model = None
    strength = cfg.get("strength", M.DEFAULT_STRENGTH)
    if strength not in M.STRENGTHS:
        strength = M.DEFAULT_STRENGTH

    # 1) Gemerktes Modell vom letzten Mal wiederherstellen (ins neue Schema heben)
    gemerkt = _migrate_ref(cfg.get("model"))
    if gemerkt:
        try:
            backend = await make_backend(gemerkt, None, strength)
            model = gemerkt
        except Exception as e:
            ui.error(f"Gemerktes Modell '{gemerkt}' ließ sich nicht laden: {e}")

    # 2) Sonst: mit Cloud-Sonnet starten, wenn ein Anthropic-Key da ist
    if backend is None and os.getenv("ANTHROPIC_API_KEY"):
        try:
            from chat import Chat
            model = M.make_ref("anthropic", "claude-sonnet-4-6")
            backend = Chat(model="claude-sonnet-4-6", strength=strength)
        except Exception as e:
            ui.error(f"Cloud-Start fehlgeschlagen: {e}")

    ctx = Ctx(backend=backend, model=model, strength=strength,
              current_chat=chatstore.next_id(), current_prompts=[])
    stats.start_session(model)

    # F12 sichert das ganze Gespraech - der Mitschnitt holt sich die Kopfdaten
    # hier ab, damit er nichts ueber main wissen muss.
    mitschrift.setze_quelle(lambda: {
        "chat": ctx.current_chat,
        "model": ctx.model or "-",
        "persona": PS.active().name,
        "modus": modes.label(),
        "ordner": str(Path.cwd()),
    })

    ui.welcome(model=ctx.model or "—", mode="Chat",
               strength=ctx.strength if ctx.strength_active() else None)
    ui.info(f"📒 Chat #{ctx.current_chat}  ·  /resume <#> setzt einen alten fort")
    if ctx.backend is None:
        ui.warn("Noch kein Modell aktiv – tippe /model zum Wählen.")
        provs = {pid: p for pid, p in P.detected().items() if not p.keyless}
        if provs:
            ui.info(f"☁ Cloud-Anbieter erkannt: {', '.join(p.label for p in provs.values())}")
        if P.get("ollama").available():
            ui.info(f"🦙 Ollama läuft: {len(P.ollama_models())} Modell(e) – lokal, kein Key")
        if not provs and not P.get("ollama").available():
            ui.info("Cloud: einen API-Key in die .env (z.B. OPENAI_API_KEY). "
                    "Lokal: Ollama installieren und starten.")
    # Key-Schutz nur als einmaliges Ereignis melden (wenn gerade verschlüsselt wurde).
    # Der dauerhafte „geschützt"-Hinweis steht jetzt unter /help (sauberer Start).
    if secured_keys:
        ui.success(f"🔒 {secured_keys} API-Key(s) verschlüsselt (Windows-Umschlag) – "
                   "in der .env steht jetzt nur noch Buchstabensalat.")

    # Der eingestellte Daten-Ordner ist nicht erreichbar (Laufwerk weg,
    # Buchstabe verrutscht). NICHT stillschweigend mit leerem Gedaechtnis
    # weiterlaufen - das sieht aus wie Datenverlust und ist keiner.
    if paths.PROBLEM:
        ui.error("📁 " + paths.PROBLEM)

    # Ganz erster Start: einmalig fragen, wo der NemiCLI-Ordner liegen soll.
    # Danach steht die Antwort in der Config und es fragt nie wieder; ueber
    # /start kommt man jederzeit zurueck.
    try:
        import reich as _R
        if not _R.schon_gefragt():
            await run_reich(erststart=True)
    except Exception as e:
        ui.warn(f"Konnte das Ordner-Fenster nicht zeigen ({e}). "
                "Mit /start kannst du es jederzeit nachholen.")

    # Erster Start auf diesem PC? Dann auf den Assistenten hinweisen.
    # (Kein Menü hier – die Oberfläche läuft an dieser Stelle noch nicht.)
    try:
        import wizard as _W
        if _W.erster_start():
            ui.info("👋 Zum ersten Mal hier? Tippe  /einrichten  – ich prüfe deinen "
                    "PC und installiere alles Nötige selbst.")
    except Exception:
        pass

    # Tipps stehen jetzt unter /help – Start bleibt sauber.
    mascot.greet()
    ui.console.print()

    start_chrome_bridge(ctx)     # Chrome-Erweiterung: Empfang auf 127.0.0.1:9000

    # --sag "<text>": erste Nachricht gleich abschicken (z.B. aus dem Rechtsklick-Menü)
    start_text = _start_nachricht()
    if start_text and tui is not None:
        tui.start_text = start_text
    elif start_text:
        await handle_line(ctx, start_text)

    try:
        if tui is not None:
            try:
                await tui.run(ctx, handle_line, session_state=SESSION,
                              model_completions=model_completions,
                              practice=practice_round)
            except Exception as e:
                # Vollbild im echten Terminal gescheitert -> sauber zurückfallen
                tui.uninstall()
                tui = None
                ui.error(f"Vollbild-TUI abgestürzt: {e} – wechsle in den klassischen Modus.")
                await run_classic(ctx)
        else:
            await run_classic(ctx)
    finally:
        stats.end_session()       # gesammelte Zahlen sichern
        if CHROME_BRIDGE is not None:
            CHROME_BRIDGE.stop()

    if tui is not None:
        tui.uninstall()
    ui.goodbye()


def selftest(out=print) -> int:
    """`NemiCLI.exe --selftest` – prüft ohne Oberfläche, ob alles an Bord ist.
    Gedacht fürs Testen der gepackten exe (das Vollbild braucht ein echtes
    Terminal und lässt sich so nicht prüfen). `out` nimmt die Zeilen entgegen –
    print für die Konsole, oder eine Liste.append für /selbsttest im Chat."""
    print = out                      # alle print() unten laufen über `out`
    print(f"NemiCLI Selbsttest  ·  {VER.build_label()}  ·  Python {sys.version.split()[0]}  ·  "
          f"{'exe' if getattr(sys, 'frozen', False) else 'Skript'}")
    print(f"Datenordner : {paths.ROOT}")
    fehler = 0
    for mod in ("ui", "screen", "actions", "vision", "config", "chatstore",
                "learn", "emoji", "mascot", "subagents", "models", "providers",
                "pricing", "setup", "syscheck", "extlibs", "practice", "webui",
                "keyvault", "commands", "confirm", "chat", "cloud", "wizard",
                "persona", "memory", "foldersense", "webfetch", "imagegen", "sdwebui", "comfyui",
                "stats", "modes", "uvsetup",
                "imgwin", "textproc", "pdfgen", "winjob", "paths"):
        try:
            __import__(mod)
            print(f"  ok   {mod}")
        except Exception as e:
            fehler += 1
            print(f"  FEHLT {mod}: {e}")
    for rel in ("Models", "Models/checkpoints", "Bilder", "chats", "learned"):
        d = paths.ROOT / rel
        print(f"  {'ok  ' if d.exists() else 'FEHLT'} Ordner {rel}")
        fehler += 0 if d.exists() else 1
    try:
        import webfetch
        print(f"  ok   Allowlist: {len(webfetch.entries())} Domains")
    except Exception as e:
        fehler += 1
        print(f"  FEHLT Allowlist: {e}")
    import imagegen
    grund = imagegen.missing_reason_aktiv()
    motor = imagegen.active_label()
    print(f"  {'ok   Bilder-Malen bereit  ' + motor if not grund else 'info Bilder-Malen: ' + grund.splitlines()[0]}")
    # Werkzeug-Rundlauf in NemiSandbox/: Ordner anlegen → Datei schreiben → lesen →
    # löschen. Direkt über die Werkzeug-Funktionen (ohne Rückfrage – es ist die
    # Sandbox), damit auffällt, wenn eines davon kaputt ist oder der Schutz greift.
    print("\nWerkzeuge:")
    try:
        import actions as A
        box = paths.ROOT / "NemiSandbox" / "_selbsttest"
        datei = box / "probe.txt"
        schritte = [
            ("ordner_erstellen", lambda: A._ordner_erstellen({"pfad": str(box)})),
            ("datei_schreiben", lambda: A._datei_schreiben({"pfad": str(datei),
                                                            "inhalt": "NemiCLI Selbsttest äöü"})),
            ("datei_lesen", lambda: A._datei_lesen({"pfad": str(datei)})),
            ("loeschen", lambda: A._loeschen({"pfad": str(box)})),
        ]
        for name, fn in schritte:
            r = fn()
            text = r.text if isinstance(r, A.ActionResult) else str(r)
            ok = (r.ok is not False) if isinstance(r, A.ActionResult) else True
            if name == "datei_lesen" and "äöü" not in text:
                ok = False
            fehler += 0 if ok else 1
            print(f"  {'ok  ' if ok else 'FEHLT'} {name:<18} {text.splitlines()[0][:60]}")
        if box.exists():
            fehler += 1
            print("  FEHLT Aufräumen: _selbsttest-Ordner ist noch da")
    except Exception as e:
        fehler += 1
        print(f"  FEHLER Werkzeuge: {e}")
    try:
        import wizard as W
        print("\nEinrichtung:")
        for s in W.check_all():
            print(f"  [{'x' if s['ok'] else ' '}] {s['titel']:<45} {s['info']}")
    except Exception as e:
        fehler += 1
        print(f"  FEHLER Assistent: {e}")
    print("\nErgebnis:", "alles gut ✅" if not fehler else f"{fehler} Problem(e) ❌")
    return 0 if not fehler else 1


if __name__ == "__main__":
    if "--version" in sys.argv:
        for k, v in VER.report():
            print(f"{k:<14} {v}")
        raise SystemExit(0)
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--bild" in sys.argv:             # Bild malen ohne Oberfläche (zum Testen)
        import imagegen
        _i = sys.argv.index("--bild")
        _prompt = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else "a red fox in a forest"
        _g = imagegen.missing_reason()
        if _g:
            print(_g)
            raise SystemExit(1)
        _opts = imagegen.parse_opts(_prompt)      # --steps/--size/--seed ... erlaubt
        _text = _opts.pop("prompt", _prompt)
        print(f"male: {_text}  ({imagegen.device_info()})")
        _res = imagegen.generate(_text, on_status=lambda m: print("  ", m), **_opts)
        print("fertig:", _res)
        raise SystemExit(0)
    if "--systemcheck" in sys.argv:      # Bericht ohne Oberfläche (auch zum Testen)
        import syscheck
        _rep = syscheck.report(True)
        ui.system_panel(_rep, syscheck.todo(_rep))
        if "--debug" in sys.argv:        # Rohdaten zum Fehlersuchen
            import json
            print(json.dumps(_rep["torch"], indent=2, ensure_ascii=False))
        raise SystemExit(0)
    asyncio.run(main())
