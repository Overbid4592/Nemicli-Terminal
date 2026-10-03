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

# Die exe bringt torch, numpy, Pillow und transformers nicht mit (NemiCLI.spec,
# EXCLUDES) – sie kommen aus einem installierten Python. Einmal hier einbinden,
# vor allen Modulen: Gedächtnis-Encoder, Wache, Kugel und beide Bild-Motoren
# brauchen sie, und nicht jedes Modul holt sie sich selbst.
if getattr(sys, "frozen", False):
    try:
        import extlibs
        extlibs.enable()
    except Exception:
        pass

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
import sicherheit
import snapshot
import workspace as WS
import coding
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
    if prov == "gguf":                               # eigener Motor: mmproj im Modell-Ordner
        import gguflokal
        return gguflokal.sieht(mid)
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
    import bildbeschreiber
    if bildbeschreiber.zustaendig(model):            # lokales Modell ohne Bild-Eingang
        try:
            bloecke = [bildbeschreiber.block(p if p in bilder else p.name,
                                             await asyncio.to_thread(bildbeschreiber.beschreiben, p))
                       for p in pfade]
            return (text + "\n\n" + "\n\n".join(bloecke)), None, (
                f"👁 Bildbeschreiber hat {len(pfade)} Bild(er) beschrieben")
        except Exception as e:
            return text, None, f"Bildbeschreiber-Fehler: {e}"
        finally:
            for p in tmp:
                try:
                    p.unlink()
                except Exception:
                    pass
    for p in tmp:
        try:
            p.unlink()
        except Exception:
            pass
    return text, None, ("Das aktive Modell sieht keine Bilder. Nimm mit /model ein "
                        "Vision-Modell (Cloud oder ein Ollama-Modell mit 👁).")
MAX_STEPS = 12                  # Bremse für Aktions-Ketten pro Nutzer-Nachricht (keine Endlosschleife)
# Solange in einer Runde NUR gelesen wurde (READ_TOOLS: datei_lesen, abfragen, web …),
# gilt eine weitere Grenze: Die Systemwache liest ihre Anleitung in drei Stücken und
# fragt dann acht Dinge ab – mit 12 war sie bei Schritt 2 am Ende. Sobald
# eine ändernde Aktion dabei war, gilt wieder die kurze Bremse.
MAX_LESE_STEPS = 30
# Im Coding-Assistenten zählt die Bremse pro Todo-Punkt: jeder Fortschritt (Punkt in
# Arbeit, Punkt geprüft) setzt sie zurück. Darüber liegt eine feste Gesamtgrenze.
CODING_MAX_STEPS = 80
_FRAGE_HALT_NOTE = ("\n\n[System] 🟠 Eine Frage ist offen. Stell sie dem Nutzer jetzt, kurz und "
                    "klar – ohne Aktion. Dann wartest du auf seine Antwort.")
# Lesende Werkzeuge, die mit identischen Feldern nichts Neues bringen: eine
# Wiederholung in derselben Runde wird nicht ausgeführt, sondern nur angemerkt.
# befehl/bild_malen/Helfer bewusst nicht – ein zweites `git status` kann Sinn haben.
_DEDUPE_TOOLS = {"datei_lesen", "ordner_auflisten", "dateien_suchen", "inhalt_suchen",
                 "ordner_erkennen", "web_lesen", "web_wiki", "web_suche", "ml_status",
                 "wache_status", "wache_alarme", "wache_ereignisse", "wache_inventar",
                 "gedaechtnis_suchen", "gedaechtnis_lesen"}
_STEP_LIMIT_NOTE = (
    "\n\n[System] ⏸ Du hast in dieser Runde die Obergrenze an Aktionen erreicht "
    f"({MAX_STEPS} mit Änderungen, {MAX_LESE_STEPS} nur lesend). Führe jetzt KEINE weitere Aktion aus. Fasse zusammen, was du "
    "bisher herausgefunden hast, und sag klar, was noch offen ist. Der Nutzer kann mit "
    "„weiter“ die nächste Runde starten."
)
_STEP_LIMIT_WARN = ("⏸ Viele Aktionen am Stück – ich halte kurz an, damit nichts endlos "
                    "läuft. Sag „weiter“, dann mache ich da weiter.")


def _schritt_grenze(nur_gelesen: bool) -> int:
    return MAX_LESE_STEPS if nur_gelesen else MAX_STEPS
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
GUI_BRUECKE = None              # Verbindung zum GUI-Fenster (None = kein /gui gestartet)
# Im GUI-Modus „Chat“ erlaubt: nichts am PC ändern – malen, Bilder ansehen, Gedächtnis, Skills (Prüffenster).
CHAT_WERKZEUGE = frozenset({"bild_malen", "bild_ansehen", "bild_fragen",
                            "gedaechtnis_suchen", "gedaechtnis_lesen", "merken",
                            "skill_laden", "skill_schreiben", "skill_ausbessern"})
_CHAT_GESPERRT = ("Werkzeug „{tool}“ ist im Chat-Modus gesperrt (dort gibt es nur Bilder malen/ansehen, "
                  "das Gedächtnis und Skills). Antworte ohne dieses Werkzeug oder bitte den Nutzer, oben auf "
                  "„Work“ umzuschalten.")
SPIELE_SERVER = None            # /spiel – Brettspiele im Browser (eigener Server)
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
    import bildbeschreiber
    if bildbeschreiber.zustaendig(model):            # lokales Modell ohne Bild-Eingang
        try:
            bloecke = [bildbeschreiber.block(p, await asyncio.to_thread(bildbeschreiber.beschreiben, p))
                       for p in pfade]
        except Exception as e:
            bloecke = [f"(Bildbeschreiber-Fehler: {e})"]
        return None, dataclasses.replace(res, text=res.text.replace(
            "wird dir jetzt als Bild gezeigt", "wurde vom Bildbeschreiber angesehen") + "\n" + "\n\n".join(bloecke))
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


# --- Freigabe einer Aktion (für Terminal, WebUI und Helfer dieselbe) --------
# Risikostufe bestimmt Frage und
# Optionen, `befehl` außerhalb der Whitelist fragt zweimal, Löschen hat eine
# Obergrenze, harmlose Änderungen einer Runde werden gebündelt. Vorher stand
# diese Logik dreimal im Code – und die WebUI schrieb kein Protokoll.

async def _freigabe(act: dict, *, frage, pruefe, zeige_aktion, wer: str,
                    frei: set | None = None, abgelehnt: set | None = None):
    """Entscheidet, ob `act` laufen darf.
    Gibt ("run", None) zurück, wenn ausgeführt werden soll; sonst
    ("block"/"stopp"/"rejected"/"no_run", ActionResult) – die Aktion läuft nicht,
    das Ergebnis geht ans Modell. Protokoll für die Nicht-Ausführung ist erledigt.
    frei/abgelehnt: ids der Aktionen aus der Sammelfrage (sicherheit.buendel)."""
    tool = act.get("tool")
    desc = actions.describe(act)
    stufe = sicherheit.stufe(act)
    urteil = modes.decide(act, actions.needs_confirm(act))
    if urteil == "block":                        # 👁 Nur Lesen: nicht mal fragen
        res = actions.ActionResult(modes.block_message(act), ok=False)
        zeige_aktion(desc, False, act)
        protokoll.schreibe(tool, desc, "blocked", veraendernd=actions.needs_confirm(act),
                           wer=wer, stufe=stufe)
        return "block", res
    if (stopp := sicherheit.grenze_pruefen(act)):     # Obergrenze: STOPP vor der Frage
        res = actions.ActionResult(stopp, ok=False)
        zeige_aktion(desc, False, act)
        protokoll.schreibe(tool, desc, "blocked", veraendernd=True, wer=wer, stufe=stufe,
                           ergebnis=stopp)
        return "stopp", res
    if abgelehnt and id(act) in abgelehnt:
        protokoll.schreibe(tool, desc, "rejected", veraendernd=True, wer=wer, stufe=stufe)
        return "rejected", None
    confirm = urteil == "ask" and tool not in AUTO_ALLOW and not (frei and id(act) in frei)
    if confirm and sicherheit.arbeitsbereich(act):
        # Der Stift der Persönlichkeit: im eigenen Daten-Ordner schreibt sie
        # ohne Rückfrage. Protokoll + Papierkorb bleiben.
        confirm = False
        act["_frei"] = "Arbeitsbereich"
        desc = actions.describe(act)
    zeige_aktion(desc, confirm, act)
    if not confirm:
        return "run", None
    try:
        preview = await asyncio.to_thread(actions.confirmation_preview, act)
    except (ValueError, OSError) as exc:
        res = actions.ActionResult(f"Vorschau nicht möglich: {exc}", ok=False)
        return "no_run", res
    if preview is not None:
        titel = "Skill prüfen" if tool in actions.SKILL_AENDERN else "Dateiänderung prüfen"
        choice = "yes" if await pruefe(titel, preview) else "no"
    else:
        choice = await frage(sicherheit.frage(act), sicherheit.optionen(act))
        if choice == "yes" and sicherheit.doppelt_fragen(act):
            zweite, optionen = sicherheit.zweite_frage(act)
            choice = await frage(zweite, optionen)
    if choice not in ("yes", "always"):
        protokoll.schreibe(tool, desc, "rejected", veraendernd=True, wer=wer, stufe=stufe)
        return "rejected", None
    if choice == "always":
        AUTO_ALLOW.add(tool)
    return "run", None


async def _buendel_freigabe(acts: list[dict], frage) -> tuple[set, set]:
    """Sammelfrage für mehrere harmlose Änderungen in einer Runde.
    Gibt (freigegebene ids, abgelehnte ids) zurück – leer, wenn es nichts zu bündeln gab
    oder der Nutzer einzeln gefragt werden will."""
    def _fragt(a: dict) -> bool:
        return (modes.decide(a, actions.needs_confirm(a)) == "ask"
                and a.get("tool") not in AUTO_ALLOW)
    idx = sicherheit.buendel(acts, _fragt)
    if not idx:
        return set(), set()
    text, optionen = sicherheit.buendel_frage(acts, idx, actions.describe)
    wahl = await frage(text, optionen)
    ids = {id(acts[i]) for i in idx}
    if wahl == "all":
        return ids, set()
    if wahl == "no":
        return set(), ids
    return set(), set()


_ABGELEHNT_TEXT = ("Aktion '{tool}' wurde vom Nutzer ABGELEHNT. "
                   "Führe sie nicht aus und frage, wie es weitergehen soll.")


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

        async def _pruefe(titel, preview):
            return (await pruefe(f"{lab}: {titel}", preview)) in ("yes", True)

        async def _frage(text, optionen):
            return await frage(f"{lab}: {text}", optionen)

        async with HELFER_LOCK:
            lage, res = await _freigabe(
                act, frage=_frage, pruefe=_pruefe,
                zeige_aktion=lambda d, c, _a: zeige_aktion(f"🤝 {lab}: {d}", c),
                wer=f"Helfer {lab}")
        if lage == "rejected":
            warne(f"{lab}: Aktion abgelehnt.")
            records.append({"tool": tool, "status": "rejected"})
            return (f"Aktion '{tool}' wurde vom Nutzer ABGELEHNT. "
                    "Führe sie nicht aus und vermerke das in deinem Bericht.")
        if lage != "run":
            zeige_ergebnis(res.text, None if lage == "block" else False, None)
            records.append({"tool": tool, "status": "not_run"})
            return _result_feedback(tool, res)
        record = {"tool": tool, "status": "running"}
        records.append(record)
        res = await actions.run(act)
        _finish_record(record, res)
        zeige_ergebnis(res.text, res.ok, res.returncode)
        protokoll.schreibe(tool, actions.describe(act), record["status"],
                           veraendernd=actions.needs_confirm(act), wer=f"Helfer {lab}",
                           ergebnis=res.text, stufe=sicherheit.stufe(act))
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


async def converse(backend, user_text: str, images: list[str] | None = None) -> list[dict]:
    """Eine Nutzer-Runde; gibt die Werkzeugbilanz zurück (tool, status)."""
    records = []
    actions.reset_taint()            # neue Nutzer-Runde: noch nichts aus dem Netz
    mitschrift.nutzer(user_text)     # F12 soll das ganze Gespräch sichern können
    if coding.aktiv() and coding.ist_ende_ansage(user_text):
        _coding_beenden()
    try:
        await _converse(backend, user_text, images, records)
    finally:
        _close_records(records)
        ui.console.print(ui.execution_receipt(records))
    return records


async def _converse(backend, user_text, images, records) -> None:
    """Ein Gespräch mit Aktions-Schleife: antworten → ggf. handeln → weiter.
    images (base64-Data-URIs) werden nur beim ERSTEN Schritt mitgeschickt."""
    next_input = user_text
    first = True
    seen: set[str] = set()                   # identische Lese-Aktionen nur einmal
    nur_gelesen = True                       # bisher nur Lese-Werkzeuge? -> längere Leine
    basis = 0                                # ab diesem Schritt zählt die Bremse
    marke = coding.marke()                   # Todo-Fortschritt (nur im Coding-Assistenten)
    frage_halt = False
    gesamt = CODING_MAX_STEPS if coding.aktiv() else MAX_LESE_STEPS
    for schritt in range(gesamt + 1):
        letzter = (schritt - basis >= _schritt_grenze(nur_gelesen) or schritt >= gesamt
                   or frage_halt)            # Extra-Runde: nur noch zusammenfassen / fragen
        if frage_halt:
            next_input = next_input + _FRAGE_HALT_NOTE
        elif letzter:
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
            ui.warn("🟠 Erst die offene Frage – die Aktion wartet auf deine Antwort."
                    if frage_halt else _STEP_LIMIT_WARN)
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
        frei, abgelehnt = await _buendel_freigabe(acts, ask_confirm)
        for act in acts:
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

            vorher = set(AUTO_ALLOW)
            lage, res = await _freigabe(
                act, frage=ask_confirm, pruefe=review_action_confirm,
                zeige_aktion=lambda d, c, a: ui.console.print(
                    ui.action_request(d, c, kopf=sicherheit.kopf(a, c),
                                      risiko=sicherheit.stufe(a) == sicherheit.RISKANT)),
                wer=PS.active().name, frei=frei, abgelehnt=abgelehnt)
            if lage == "rejected":
                ui.warn("Abgelehnt.")
                mitschrift.aktion(tool, actions.describe(act), "Vom Nutzer abgelehnt.", False)
                records.append({"tool": tool, "status": "rejected"})
                ergebnisse.append(_ABGELEHNT_TEXT.format(tool=tool))
                continue
            if lage != "run":
                ui.console.print(ui.action_result(res.text, ok=None if lage == "block" else res.ok))
                records.append({"tool": tool, "status": "not_run"})
                ergebnisse.append(_result_feedback(tool, res))
                continue
            if AUTO_ALLOW - vorher:
                ui.info(f"Aktionen vom Typ '{tool}' frage ich diese Sitzung nicht mehr ab.")
            record = {"tool": tool, "status": "running"}
            records.append(record)
            if tool not in modes.READ_TOOLS:
                nur_gelesen = False
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
            elif tool == "befehl" or coding.pruefbefehl(act):
                res = await _befehl_live(act)
            else:
                with ui.thinking("führe aus"):
                    res = await actions.run(act)
            _finish_record(record, res)
            uris, res = await _bilder_aus_ergebnis(res, SESSION.get("model"))
            ui.console.print(ui.action_result(res.text, ok=res.ok))
            if tool in ("todo", "coding_start") and res.ok:
                _todo_zeigen()
                if str(act.get("aktion", "")).lower() == "frage":
                    frage_halt = True
            mitschrift.aktion(tool, actions.describe(act), res.text, res.ok)
            protokoll.schreibe(tool, actions.describe(act), record["status"],
                               veraendernd=actions.needs_confirm(act), wer=PS.active().name,
                               ergebnis=res.text, stufe=sicherheit.stufe(act))
            if tool == "bild_malen" and res.ok:
                _bild_vorschau(res.text)
            if uris:
                naechste_bilder.extend(uris)
                for pf in res.bilder[:2]:
                    _bild_vorschau(str(pf), title=f"angesehen: {Path(pf).name}")
            ergebnisse.append(_result_feedback(tool, res))

        # 4) Ergebnisse zurück ans Modell, nächster Schritt
        next_input = "\n\n".join(ergebnisse)
        if (neu := coding.marke()) != marke:  # Todo-Fortschritt: Bremse beginnt neu
            marke, basis, nur_gelesen = neu, schritt + 1, True

    ui.warn(_STEP_LIMIT_WARN)                # Rückfall – normalerweise endet die Extra-Runde oben


async def _befehl_live(act: dict) -> "actions.ActionResult":
    """`befehl` mit Live-Anzeige: Befehl, Laufzeit, letzte Ausgabezeilen.
    Esc beendet den Befehl samt Unterprozessen."""
    befehl = actions._befehl_text(act) or coding.pruefbefehl(act)
    run_task = asyncio.create_task(actions.run(act))
    try:
        with ui.live_view(ui.befehl_panel(befehl, 0, [])) as live:
            while not run_task.done():
                if ui.TUI.consume_abort() if ui.TUI is not None else _esc_pressed():
                    actions.befehl_stoppen()
                st = actions.befehl_status() or {"sekunden": 0, "zeilen": []}
                live.update(ui.befehl_panel(befehl, st["sekunden"], st["zeilen"]))
                await asyncio.sleep(0.2)
        return await run_task
    except asyncio.CancelledError:
        actions.befehl_stoppen()
        raise


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


async def web_converse(backend, user_text, images, emit, confirm, erlaubt=None) -> None:
    """erlaubt: Menge von Werkzeugnamen (None = alle); andere werden nicht ausgeführt."""
    records = []
    actions.reset_taint()
    try:
        await _web_converse(backend, user_text, images, emit, confirm, records, erlaubt)
    finally:
        _close_records(records)
        emit({"t": "receipt", "records": records})


async def _web_converse(backend, user_text, images, emit, confirm, records, erlaubt=None) -> None:
    """Kopflose Variante von converse() fürs WebUI.

    Gleiche Aktions-Schleife, aber statt ins Terminal zu rendern, gibt sie
    strukturierte Ereignisse via emit(dict) an den Browser und fragt
    Bestätigungen via confirm(frage, optionen)->wahl (async). Nutzt dieselben
    Werkzeuge/Backends wie das Terminal – derselbe Verlauf, dasselbe Modell.
    """
    next_input = user_text
    first = True
    seen: set[str] = set()
    nur_gelesen = True
    for schritt in range(MAX_LESE_STEPS + 1):
        letzter = schritt >= _schritt_grenze(nur_gelesen)
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
        if erlaubt is not None:
            for act in [a for a in acts if a.get("tool") not in erlaubt]:
                emit({"t": "note", "text": f"🔒 im Chat-Modus gesperrt: {actions.describe(act)}"})
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
                ergebnisse.append(_CHAT_GESPERRT.format(tool=act.get("tool", "?")))
            acts = [a for a in acts if a.get("tool") in erlaubt]
        frei, abgelehnt = await _buendel_freigabe(acts, confirm)
        for act in acts:
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

            async def _web_pruefe(titel, preview):
                return (await confirm(
                    titel, [("yes", "Gezeigten Inhalt freigeben"), ("no", "Ablehnen")],
                    preview=preview)) == "yes"

            lage, res = await _freigabe(
                act, frage=confirm, pruefe=_web_pruefe,
                zeige_aktion=lambda d, c, a: emit({
                    "t": "action", "desc": d, "needs_confirm": c,
                    "risiko": sicherheit.stufe(a) == sicherheit.RISKANT,
                    "stufe": sicherheit.stufe(a)}),
                wer=PS.active().name, frei=frei, abgelehnt=abgelehnt)
            if lage == "rejected":
                emit({"t": "warn", "text": "Aktion abgelehnt."})
                records.append({"tool": tool, "status": "rejected"})
                ergebnisse.append(_ABGELEHNT_TEXT.format(tool=tool))
                continue
            if lage != "run":
                emit({"t": "action_result", "ok": None if lage == "block" else res.ok,
                      "text": res.text})
                records.append({"tool": tool, "status": "not_run"})
                ergebnisse.append(_result_feedback(tool, res))
                continue

            record = {"tool": tool, "status": "running"}
            records.append(record)
            if tool not in modes.READ_TOOLS:
                nur_gelesen = False
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
            protokoll.schreibe(tool, actions.describe(act), record["status"],
                               veraendernd=actions.needs_confirm(act), wer=PS.active().name,
                               ergebnis=res.text, stufe=sicherheit.stufe(act))
            ergebnisse.append(_result_feedback(tool, res))

        next_input = "\n\n".join(ergebnisse)

    emit({"t": "warn", "text": _STEP_LIMIT_WARN})


async def do_reflect(ctx: "Ctx") -> list[tuple[str, str]]:
    """/reflektieren: Reflexion mit Anzeige; danach gleich in den Index."""
    with ui.thinking("reflektiere über das Gespräch"):
        try:
            saved = await reflexion(ctx.backend)
        except Exception as e:
            ui.error(f"Reflexion fehlgeschlagen: {e}")
            return []
    if saved:
        await bibliothekar(ctx.current_chat)
        memory.release_encoder()
    return saved


_FREMD_MARKEN = ("EXTERNER WEB-INHALT", "DATEN aus", "AUSGABE von", "BILDBESCHREIBUNG von")


async def reflexion(backend, zusatz: str = "", nachrichten: list[dict] | None = None,
                    max_zeilen: int = 3) -> list[tuple[str, str]]:
    """Lässt das Modell auf das Gespräch schauen und Bleibendes festhalten: LEKTION, VORLIEBE,
    FAKT – oder einen vorhandenen Eintrag ERSETZEN bzw. VERGESSEN (Nummern aus dem
    Gedächtnis). `zusatz`: Anlass und Werkzeugbilanz; `nachrichten`: statt der letzten 12
    (z. B. was gleich herausgekürzt wird). Fremdinhalte (Web, Dateien, Ausgaben) werden nicht
    gelesen. Gibt (art, text)-Paare zurück."""
    quelle = nachrichten if nachrichten is not None else backend.messages[-12:]
    msgs = [m for m in quelle if m.get("role") in ("user", "assistant")
            and not any(marke in str(m.get("content") or "") for marke in _FREMD_MARKEN)]
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
        "Du bist NemiCLIs Reflexions-Modul. Schau auf das Gespräch und halte BLEIBENDES, "
        "allgemein Nützliches für die Zukunft fest – über den Nutzer, seinen PC oder die "
        f"richtige Arbeitsweise. Gib HÖCHSTENS {max_zeilen} Zeilen aus, jede beginnt mit "
        "'LEKTION:' (eine gelernte Lehre), 'VORLIEBE:' (wie der Nutzer etwas will), "
        "'FAKT:' (ein dauerhafter Fakt), 'ERSETZE #<Nummer>: <neuer Text>' (ein gemerkter Eintrag "
        "ist überholt oder widerspricht dem Gespräch) oder 'VERGISS #<Nummer>' (ein Eintrag ist falsch). "
        "Lieber ersetzen als einen zweiten, widersprüchlichen Eintrag anlegen. Keine Wegwerf-Details "
        "des Einzelfalls, keine Floskeln. Gibt es nichts wirklich Bleibendes, schreibe nur 'KEINE'."
    )
    gemerkt = [e for e in memory.all_entries() if e.get("kind") in ("vorliebe", "lektion", "fakt")][-40:]
    bestand = "\n".join(f"[#{e['id']}] ({e['kind']}) {memory.entschaerfen(e['text'])}" for e in gemerkt)
    prompt = (f"Gespräch:\n{transcript}\n\n"
              + (f"Bisher gemerkt:\n{bestand}\n\n" if bestand else "")
              + (f"{zusatz}\n\n" if zusatz else "")
              + f"Was du festhältst (höchstens {max_zeilen} Zeilen):")
    out = await backend.ask_once(prompt, system)

    saved: list[tuple[str, str]] = []
    for line in (out or "").splitlines()[:max_zeilen * 2]:
        line = line.strip().lstrip("-•* ").strip()
        if (m := re.match(r"(?i)ersetze\s*#?(\d+)\s*:\s*(.+)", line)):
            e = memory.ersetzen(int(m.group(1)), m.group(2).strip())
            if e is not None:
                saved.append((f"ersetzt #{e['id']}", e["text"]))
            continue
        if (m := re.match(r"(?i)vergiss\s*#?(\d+)", line)):
            if memory.vergessen(int(m.group(1)), "Reflexion"):
                saved.append((f"vergessen #{m.group(1)}", ""))
            continue
        kopf, _, txt = line.partition(":")
        art = {"lektion": "lektion", "vorliebe": "vorliebe", "fakt": "fakt"}.get(kopf.strip().lower())
        txt = txt.strip()
        if art is None or len(txt) < 4:
            continue
        if memory.remember(txt, art) is not None:        # None = leer, Dublette oder verdächtig
            saved.append((art, txt))
        if len(saved) >= max_zeilen:
            break
    return saved


_LERN_AUFGABEN: set = set()          # laufende Hintergrund-Aufgaben (Referenz halten)


def lernen_planen(ctx: "Ctx", nutzertext: str, records: list[dict], web: bool,
                  weg: list[dict] | None = None) -> None:
    """Automatisch reflektieren, wenn die Runde einen Anlass hat (lernschleife.anlass), und
    sichern, was gerade aus dem Kontext gekürzt wurde (`weg`). Läuft im Hintergrund unter
    TURN_LOCK – die nächste Runde wartet kurz, statt mit der Reflexion um das Modell zu
    konkurrieren. Nach Web-Inhalt in der Runde kein Rückblick auf sie."""
    import lernschleife as LS
    backend = ctx.backend
    if backend is None or not LS.an():
        return
    grund = None if web else LS.anlass(nutzertext, records)
    if grund is None and not weg:
        return

    async def lauf() -> None:
        saved = []
        async with TURN_LOCK:
            if ctx.backend is not backend:            # inzwischen Modell gewechselt
                return
            try:
                if weg:
                    saved += await reflexion(backend, LS.SICHERN, nachrichten=weg, max_zeilen=5)
                if grund:
                    saved += await reflexion(backend, LS.zusatz(grund, records))
            except Exception:
                pass
        if saved:
            ui.info("🪞 Gelernt: " + "  ·  ".join(f"{art}: {t}" if t else art for art, t in saved))
            await bibliothekar(ctx.current_chat)
            memory.release_encoder()

    aufgabe = asyncio.get_running_loop().create_task(lauf())
    _LERN_AUFGABEN.add(aufgabe)
    aufgabe.add_done_callback(_LERN_AUFGABEN.discard)


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
                await bibliothekar(ctx.current_chat)
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


def _gui_verlauf(ctx: "Ctx") -> list[dict]:
    """Der aktuelle Chat fürs GUI-Fenster: Nutzer, Antwort (ohne Aktionsblöcke) und
    Werkzeug-Ergebnisse (kurz) – in der Reihenfolge des Verlaufs."""
    if ctx.backend is None:
        return []
    eigene = set(ctx.current_prompts or [])
    raus = []

    def als_text(inhalt) -> str:
        if isinstance(inhalt, list):
            return " ".join(t.get("text", "") for t in inhalt if isinstance(t, dict))
        return str(inhalt or "")

    for m in ctx.backend.messages:
        rolle, text = m.get("role"), als_text(m.get("content"))
        if rolle == "user":
            if text in eigene:
                raus.append({"rolle": "nutzer", "text": text})
            elif text.strip():
                raus.append({"rolle": "werkzeug", "text": text[:600]})
        elif rolle == "assistant":
            acts, sauber = actions.parse_actions(text)
            if sauber.strip():
                raus.append({"rolle": "ki", "text": sauber})
            for a in acts:
                raus.append({"rolle": "aktion", "text": actions.describe(a)})
    return raus


async def skills_befehl(arg: str) -> None:
    """/skills – Liste · selbst an|aus · alte (Notizen aus learned/skills durchsehen)."""
    import skills
    teile = arg.split()
    was = teile[0].lower() if teile else ""
    if was == "selbst":
        if len(teile) > 1 and teile[1].lower() in ("an", "aus"):
            skills.selbst_setzen(teile[1].lower() == "an")
        ui.info("🧩 Skills selbst anlegen/ändern: "
                + ("AN – ohne Rückfrage (kam in der Runde etwas aus dem Netz, fragt es trotzdem)"
                   if skills.selbst() else "AUS – jeder neue oder geänderte Skill geht durchs Prüffenster"))
        return
    if was == "alte":
        await _alte_skills()
        return
    liste = skills.alle()
    if liste:
        ui.kv_panel(f"🧩 Skills von {PS.active().name} und für alle",
                    [(s.name + (" [für alle]" if s.fuer_alle else ""), f"{s.beschreibung}\nwann: {s.wann}")
                     for s in liste])
    else:
        ui.info("🧩 Noch keine Skills. Im Chat: „Lass uns einen Skill machen“.")
    if (offen := skills.alte()):
        ui.info(f"📝 {len(offen)} alte Notiz(en) aus learned/skills warten auf Durchsicht: /skills alte")
    ui.info(f"Ordner: {skills.profil_ordner()}  ·  für alle: {skills.ordner()}  ·  "
            f"selbst anlegen: {'an' if skills.selbst() else 'aus'} "
            "(/skills selbst an|aus)")


async def _alte_skills() -> None:
    """Alte Notizen einzeln zeigen: F8 übernimmt als Skill, sonst weglassen oder später."""
    import skills
    offen = skills.alte()
    if not offen:
        ui.info("📝 Keine alten Notizen mehr offen.")
        return
    for nr, pfad in enumerate(offen, 1):
        try:
            felder = skills.alt_zerlegen(pfad)
        except (OSError, UnicodeDecodeError) as e:
            ui.warn(f"{pfad.name}: nicht lesbar ({e})")
            continue
        if skills.finden(felder["name"]) is not None:
            felder["name"] += " (alt)"
        if skills.pruefen(**felder):
            skills.alt_gesehen(pfad)
            ui.info(f"📝 {pfad.name}: leer – übersprungen")
            continue
        vorschau = (f"Alte Notiz {nr}/{len(offen)}: {pfad}\nF8 übernimmt sie als Skill, Esc nicht.\n"
                    f"Beschreibung und „wann“ sind geraten – danach im Chat verbessern lassen.\n\n"
                    + skills.text_bauen(**felder))
        if await review_action_confirm("Alte Notiz als Skill übernehmen?", vorschau):
            ziel = skills.schreiben(**felder, fuer_alle=True)          # alte Notizen galten für alle
            skills.alt_gesehen(pfad)
            ui.success(f"🧩 Übernommen: {felder['name']}  →  {ziel}")
            continue
        wahl = await ask_confirm(f"„{felder['name']}“ nicht übernehmen –", [
            ("weg", "Weglassen (nicht mehr fragen; die Notiz bleibt in learned/skills)"),
            ("spaeter", "Später nochmal zeigen"),
            ("stopp", "Aufhören")])
        if wahl == "weg":
            skills.alt_gesehen(pfad)
        elif wahl == "stopp":
            break


async def name_befehl(arg: str) -> None:
    """/name – Name, Wunsch-Anrede und „über mich“; gilt ab der ersten Nachricht (nutzerprofil.py)."""
    import nutzerprofil as NP
    was = arg.strip().lower()
    alt = NP.laden()
    if was in ("loeschen", "löschen"):
        if await ask_confirm("Name, Anrede und „über mich“ wirklich löschen?",
                             [("ja", "Ja, löschen"), ("nein", "Nein")]) == "ja":
            NP.speichern("", "", "")
            ui.success("👤 Profil gelöscht.")
        return
    if was != "zeigen":
        ui.info("👤 Über dich – Enter behält den bisherigen Wert, ein einzelnes „-“ leert das Feld.")
        felder = {}
        for schluessel, frage in (("name", "Wie heißt du?"),
                                  ("anrede", "Wie möchtest du genannt werden? (mehrere mit Komma)"),
                                  ("ueber", "Erzähl etwas über dich – was du magst, was nicht, was die KI wissen soll:")):
            bisher = alt[schluessel]
            eingabe = (await ask_text(f"{frage}" + (f"  [bisher: {bisher[:60]}{'…' if len(bisher) > 60 else ''}]"
                                                    if bisher else ""))).strip()
            felder[schluessel] = "" if eingabe == "-" else (eingabe or bisher)
        alt = NP.speichern(**felder)
        ui.success("👤 Gespeichert – gilt ab der nächsten Nachricht, für jede Persönlichkeit.")
    if NP.leer(alt):
        ui.info("👤 Noch nichts eingetragen: /name")
        return
    ui.kv_panel("👤 Über dich", [("Name", alt["name"] or "–"), ("Anrede", alt["anrede"] or "–"),
                                  ("Über dich", alt["ueber"] or "–")])


def _gui_zustand(ctx: "Ctx") -> dict:
    import nutzerprofil
    import skills
    p = PS.active()
    return {"t": "state", "model": ctx.model or "—", "chat": ctx.current_chat,
            "chats": chatstore.list_chats()[:60], "modus": modes.label(), "ordner": str(Path.cwd()),
            "persona": {"key": p.key, "name": p.name, "kurz": p.kurz},
            "personas": [{"key": k, "name": v.name, "kurz": v.kurz, "aktiv": k == p.key}
                         for k, v in PS.list_all().items()],
            "skills": [{"kennung": s.kennung, "name": s.name, "beschreibung": s.beschreibung, "wann": s.wann,
                        "fuer_alle": s.fuer_alle} for s in skills.alle()],
            "skills_selbst": skills.selbst(),
            "profil": nutzerprofil.laden()}


def _gui_bilder(anzahl: int = 300) -> list[str]:
    """Galerie: Bilder der aktiven Persönlichkeit und die gemeinsamen von früher, neueste zuerst."""
    import profilordner
    dateien = []
    for ordner in profilordner.bilder_ordner_alle():
        try:
            dateien += [d for d in ordner.iterdir() if d.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        except OSError:
            continue
    dateien.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return [str(d) for d in dateien[:anzahl]]


def _gui_profil(ctx: "Ctx", bruecke, auftrag: dict) -> None:
    """„Über dich“ aus dem Desktop-Fenster speichern (auf der Sitzungs-Schleife)."""
    import nutzerprofil
    nutzerprofil.speichern(str(auftrag.get("name") or ""), str(auftrag.get("anrede") or ""),
                           str(auftrag.get("ueber") or ""))
    ui.info("🖥 GUI: „Über dich“ gespeichert – gilt ab der nächsten Nachricht.")
    bruecke.senden(_gui_zustand(ctx))


def _gui_skill(bruecke, kennung: str) -> None:
    import skills
    s = next((s for s in skills.alle() if s.kennung == kennung), None)
    if s is not None:
        bruecke.senden({"t": "skill", "name": s.name, "wann": s.wann, "text": s.anleitung})


def _gui_spiegeln(ctx: "Ctx") -> None:
    """Nach einem Zug oder Chatwechsel im Terminal: GUI-Fenster auf denselben Stand."""
    if GUI_BRUECKE is not None:
        GUI_BRUECKE.senden(_gui_zustand(ctx))
        GUI_BRUECKE.senden({"t": "verlauf", "chat": ctx.current_chat, "eintraege": _gui_verlauf(ctx)})


async def start_gui(ctx: "Ctx"):
    """Brücke zum GUI-Fenster aufbauen (einmal je Sitzung)."""
    import gui_bruecke
    loop = asyncio.get_running_loop()
    bruecke = None

    async def zug(text: str, modus: str) -> None:
        async with TURN_LOCK:
            if ctx.backend is None:
                bruecke.senden({"t": "warn", "text": "Kein Modell aktiv – im Terminal mit /model wählen."})
                bruecke.senden({"t": "turn_end"})
                return
            bruecke.senden({"t": "busy", "on": True})
            fertig = {"text": ""}

            def melden(ev: dict) -> None:
                if ev.get("t") == "answer_end" and ev.get("clean"):
                    fertig["text"] = ev["clean"]
                if ev.get("t") == "receipt":
                    fertig["records"] = ev.get("records") or []
                bruecke.senden(ev)
            try:
                txt = emoji.expand(text or "")
                dateien = anhang.anhaengen(txt)
                txt = dateien["text"]
                if (h := anhang.hinweis(dateien)):
                    bruecke.senden({"t": "info", "text": h})
                txt, imgs, hinweis = await _bilder_fuer_chat(ctx.model, txt)
                if hinweis:
                    bruecke.senden({"t": "info", "text": hinweis})
                if not imgs and not txt.strip():
                    return
                bruecke.senden({"t": "nutzer", "text": text, "quelle": "gui"})   # wie getippt, ohne Datenblöcke
                from rich.text import Text
                ui.console.print(Text(f"🖥 GUI ({'Chat' if modus == 'chat' else 'Work'}): {text}", style="bold"))
                await web_converse(ctx.backend, txt, imgs, melden, bruecke.fragen,
                                   erlaubt=CHAT_WERKZEUGE if modus == "chat" else None)
                if fertig["text"]:
                    ui.console.print(ui.assistant_panel(fertig["text"]))
                ctx.current_prompts.append(txt)
                chatstore.save(ctx.current_chat, ctx.backend.messages, ctx.current_prompts, ctx.model or "—")
                learn.capture_from_answer(txt, ctx.backend.messages, ctx.current_chat)
                await bibliothekar(ctx.current_chat)
                memory.release_encoder()
                lernen_planen(ctx, text, fertig.get("records") or [], actions.web_tainted(),
                              pricing.weggefallen_holen(ctx.backend))
            except Exception as e:
                bruecke.senden({"t": "error", "text": f"Fehler: {e}"})
            finally:
                bruecke.senden({"t": "busy", "on": False})
                bruecke.senden({"t": "turn_end"})
                bruecke.senden(_gui_zustand(ctx))
                ctx.sync_session()

    async def fortsetzen(cid: int) -> None:
        async with TURN_LOCK:
            data = chatstore.load(cid)
            if not data or ctx.backend is None:
                bruecke.senden({"t": "warn", "text": f"Chat #{cid} lässt sich nicht öffnen."})
                return
            ctx.backend.messages = data.get("messages", [])
            ctx.current_prompts = data.get("prompts", [])
            ctx.current_chat = cid
            ctx.sync_session()
            ui.info(f"🖥 GUI: Chat #{cid} fortgesetzt – {data.get('title') or ''}")
        _gui_spiegeln(ctx)

    async def neuer_chat() -> None:
        async with TURN_LOCK:
            if ctx.backend:
                ctx.backend.reset()
            ctx.current_prompts = []
            ctx.current_chat = chatstore.next_id()
            ctx.sync_session()
            ui.info(f"🖥 GUI: neuer Chat #{ctx.current_chat}")
        _gui_spiegeln(ctx)

    async def persona_setzen(key: str) -> None:
        async with TURN_LOCK:                    # nie mitten in einem Zug wechseln
            if key in PS.list_all() and key != PS.active().key:
                PS.set_active(key)
                ui.info(f"🖥 GUI: Persönlichkeit {PS.active().name}")
                _sprueche_im_hintergrund()
                _neuer_chat_nach_wechsel(ctx)
        _gui_spiegeln(ctx)                       # Zustand, Chats und Verlauf der neuen

    plane = lambda coro: asyncio.run_coroutine_threadsafe(coro, loop)
    handlers = {
        "senden": lambda a: plane(zug(str(a.get("text") or ""),
                                      "work" if a.get("modus") == "work" else "chat")),
        "zustand": lambda a: loop.call_soon_threadsafe(_gui_spiegeln, ctx),
        "fortsetzen": lambda a: plane(fortsetzen(int(a.get("chat")))) if str(a.get("chat", "")).isdigit() else None,
        "neu": lambda a: plane(neuer_chat()),
        "persona": lambda a: plane(persona_setzen(str(a.get("key") or ""))),
        "galerie": lambda a: bruecke.senden({"t": "galerie", "bilder": _gui_bilder()}),
        "skill": lambda a: _gui_skill(bruecke, str(a.get("kennung") or "")),
        "profil": lambda a: loop.call_soon_threadsafe(_gui_profil, ctx, bruecke, a),
    }
    bruecke = gui_bruecke.GuiBruecke(
        loop, handlers,
        bei_wechsel=lambda n: loop.call_soon_threadsafe(_fenster_steuern, "tray" if n else "zeigen"))
    return bruecke


_STEUER_NR = 0


def _fenster_steuern(befehl: str) -> None:
    """Befehl ans eigene Terminal-Fenster (terminal_fenster.py) über den Konsolentitel –
    nur dort; in anderen Terminals bliebe der Titel sonst sichtbar stehen."""
    global _STEUER_NR  # noqa: PLW0603
    if os.name != "nt" or os.environ.get("NEMICLI_FENSTER") != "1":
        return
    _STEUER_NR += 1
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(f"nemicli-steuer:{befehl}:{_STEUER_NR}")
    except Exception:
        pass


def gui_oeffnen(bruecke) -> None:
    """Startet das GUI-Fenster als eigenen Prozess; es verbindet sich über die Brücke."""
    import subprocess
    env = dict(os.environ, NEMICLI_GUI_PIPE=bruecke.adresse, NEMICLI_GUI_KEY=bruecke.schluessel.hex())
    if getattr(sys, "frozen", False):
        befehl = [str(paths.exe_ohne_fenster()), "--gui"]
    else:
        py = Path(sys.executable)
        fensterlos = py.with_name("pythonw.exe")
        befehl = [str(fensterlos if fensterlos.is_file() else py), str(Path(__file__).resolve()), "--gui"]
    subprocess.Popen(befehl, env=env, cwd=os.getcwd())


AKTIVER_CTX = None              # die laufende Sitzung – für Stellen ohne ctx-Parameter


def _neuer_chat_nach_wechsel(ctx: "Ctx | None" = None) -> None:
    """Die Persönlichkeit hat gewechselt: ein Chat gehört der, mit der er begann – der
    nächste läuft bei der neuen (eigener Ordner, eigenes Kern-Gedächtnis)."""
    ctx = ctx or AKTIVER_CTX
    if ctx is None or not ctx.current_prompts:
        if ctx is not None:
            ctx.sync_session()
        return
    if ctx.backend is not None:
        ctx.backend.reset()
    ctx.current_prompts = []
    ctx.current_chat = chatstore.next_id()
    ctx.sync_session()
    ui.info(f"📒 Neuer Chat #{ctx.current_chat} für {PS.active().name} – der alte bleibt bei der vorigen.")


def _sprueche_im_hintergrund(ctx: "Ctx | None" = None) -> None:
    """Hat die aktive Persönlichkeit noch keine eigenen Sprüche fürs Maskottchen,
    schreibt sie sie jetzt im Hintergrund (ein kurzer Aufruf). Beim nächsten Gruß
    spricht dann sie – nicht ein fester Spruchzettel."""
    ctx = ctx or AKTIVER_CTX
    if ctx is None or ctx.backend is None or mascot.hat_sprueche():
        return

    async def _lauf():
        try:
            if await mascot.sprueche_nachholen(ctx.backend):
                ui.info(f"💬 {PS.active().name} hat sich eigene Sprüche fürs Terminal geschrieben "
                        f"({mascot.sprueche_datei().name} – darf sie jederzeit ändern).")
        except Exception:
            pass
    try:
        asyncio.get_running_loop().create_task(_lauf())
    except RuntimeError:
        pass


async def start_spiele(ctx: "Ctx"):
    """/spiel – Schach, Mühle, Dame, TicTacToe im Browser gegen die Persönlichkeit.

    Eigener kleiner Server (tools/spiele.py), nichts mit /webui zu tun. Die
    Persönlichkeit wird je Zug über backend.ask_messages() gefragt – ein eigener
    Mini-Verlauf, keine Werkzeuge, der Terminal-Chat bleibt unberührt."""
    import spiele
    loop = asyncio.get_running_loop()

    async def denke(p: dict) -> dict:
        person = PS.active()
        nutzer = PS.nutzername() or "du"
        if p.get("wer"):                       # die Seite fragt beim Öffnen nur die Namen
            return {"name": person.name, "nutzer": nutzer}
        if ctx.backend is None:
            raise RuntimeError("Kein Modell aktiv – im Terminal mit /model eins wählen.")
        system = spiele.system_prompt(PS.render_text(person), person.name, nutzer,
                                      p.get("spiel", ""), p.get("farbe_ki", ""))
        p["nutzer"] = nutzer
        return await spiele.zug_holen(ctx.backend, system, p)

    server = spiele.SpieleServer(loop, denke)
    server.start()
    return server


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

    if provider != "gguf" and "gguflokal" in sys.modules:
        sys.modules["gguflokal"].entladen()          # anderes Modell: VRAM freigeben
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Für Claude brauchst du einen ANTHROPIC_API_KEY in der .env.")
        from chat import Chat
        backend = Chat(model=model_id, strength=strength)
    elif provider == "gguf":
        import gguflokal
        if model_id not in gguflokal.modelle():
            raise RuntimeError(f"Kein lokales Modell '{model_id}' in {gguflokal.ORDNER}.")
        backend = gguflokal.GgufChat(model_id)
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


# Der Bibliothekar (indexdb.after_turn) läuft nach jeder Runde und zeigt seinen Fortschritt
# als Balken. Er darf den Chat nicht blockieren (ein dicker Wache-Bericht = 60 Brocken
# ≈ 90 s auf der CPU). Deshalb zwei Wege:
#   TUI:      Hintergrund-Task, Fortschritt als Badge mit Mini-Balken in der Statuszeile
#             (screen_tx.set_bibliothekar); der Nutzer tippt derweil weiter. Läuft schon
#             einer, wird nur „nochmal“ vorgemerkt – nie zwei gleichzeitig.
#   Klassik:  wie gehabt inline mit Panel (rich.Live und Prompt vertragen sich nicht
#             gleichzeitig), aber portionsweise (indexdb-Grenzen je Runde).
_BIB: dict = {"task": None, "nochmal": None}


def _bibliothekar_lauf(chat_id, alles: bool, on_status) -> dict:
    """Blockierend (im Thread): after_turn + Ergebnis. `n` = neu gespeicherte Brocken,
    `mit_vektor` = wie viele Brocken in diesem Lauf einen Vektor bekommen haben."""
    out = {"n": 0, "err": None, "mit_vektor": 0, "neu": 0}
    try:
        vorher = indexdb.offen()
        out["n"] = indexdb.after_turn(chat_id, status=on_status, alles=alles)
        nachher = indexdb.offen()
        # Direkt gezählt: Brocken insgesamt und Brocken mit Vektor des aktiven Modells.
        out["neu"] = max(0, nachher["brocken"] - vorher["brocken"])
        out["mit_vektor"] = max(0, (nachher["brocken"] - nachher["ohne_vektor"])
                                - (vorher["brocken"] - vorher["ohne_vektor"]))
    except Exception as e:
        out["err"] = e
    return out


def _bibliothekar_text(res: dict) -> str:
    """Ehrliche Zeile: gespeichert ist nicht dasselbe wie mit Vektor versehen."""
    text = f"📚 Gedächtnis: {res.get('neu', 0)} Brocken neu, {res.get('mit_vektor', 0)} mit Vektor"
    if not res.get("mit_vektor"):
        try:
            text += f" – Suche nur nach Stichworten ({memory.encoder_status()})"
        except Exception:
            pass
    return text


async def _bibliothekar_hintergrund(chat_id, alles: bool) -> None:
    tui = ui.TUI
    try:
        while True:
            zuletzt = {"msg": ""}

            def on_status(m: str) -> None:
                zuletzt["msg"] = m
                if tui is not None:
                    try:
                        tui.app.call_from_thread(tui.set_bibliothekar, m)
                    except Exception:
                        pass

            res = await asyncio.to_thread(_bibliothekar_lauf, chat_id, alles, on_status)
            memory.release_encoder()
            if res["err"]:
                ui.info(f"📚 Bibliothekar: {res['err']}")
            elif res["n"]:
                rest = ""
                try:
                    o = indexdb.offen()
                    offen = o["berichte_offen"] + o["ohne_vektor"]
                    if offen and not alles:
                        rest = f" – noch {o['berichte_offen']} Berichte / {o['ohne_vektor']} Brocken offen, geht nächste Runde weiter (/gedaechtnis indexieren = alles jetzt)"
                except Exception:
                    pass
                ui.info(_bibliothekar_text(res) + rest + ".")
            if _BIB["nochmal"] is None:
                break
            chat_id, alles = _BIB["nochmal"]
            _BIB["nochmal"] = None
    finally:
        _BIB["task"] = None
        if tui is not None:
            try:
                tui.set_bibliothekar("")
            except Exception:
                pass


async def bibliothekar(chat_id: int | None = None, alles: bool = False) -> int:
    """Nach der Runde: Chat, Notizen, Skills, Berichte einbetten. Im TUI im Hintergrund
    (Badge in der Statuszeile, Rückgabe 0), in der Klassik-Oberfläche inline mit Panel."""
    if ui.TUI is not None:
        if _BIB["task"] is not None and not _BIB["task"].done():
            alt = _BIB["nochmal"] or (None, False)
            _BIB["nochmal"] = (chat_id or alt[0], alles or alt[1])
            return 0
        _BIB["task"] = asyncio.create_task(_bibliothekar_hintergrund(chat_id, alles))
        return 0

    state = {"msg": "", "done": False, "res": None}

    def on_status(m: str) -> None:
        state["msg"] = m

    def runner() -> None:
        try:
            state["res"] = _bibliothekar_lauf(chat_id, alles, on_status)
        finally:
            state["done"] = True

    task = asyncio.create_task(asyncio.to_thread(runner))
    titel = "📚 Bibliothekar · vektorisiere fürs Gedächtnis"
    live_cm = live = None
    try:
        while not state["done"]:
            if live is None and state["msg"]:            # Panel erst, wenn wirklich eingebettet wird
                live_cm = ui.live_view(ui.progress_panel(titel, state["msg"]))
                live = live_cm.__enter__()
            if live is not None:
                live.update(ui.progress_panel(titel, state["msg"]))
            await asyncio.sleep(0.15)
    finally:
        if live_cm is not None:
            live_cm.__exit__(None, None, None)
    await task
    res = state["res"] or {"n": 0, "err": None, "mit_vektor": 0}
    if res["err"]:
        ui.info(f"📚 Bibliothekar: {res['err']}")
    elif res["n"]:
        ui.info(_bibliothekar_text(res) + ".")
    return res["n"]


async def _kugel_befehl(ctx: "Ctx", arg: str) -> None:
    """/kugel · /kugel malen [beschreibung] · /kugel weg · /kugel ordner – siehe tools/kugelbilder.py."""
    import kugelbilder as KB
    name = PS.active().name
    teile = arg.split(maxsplit=1)
    sub = teile[0].lower() if teile else ""
    rest = teile[1].strip() if len(teile) > 1 else ""
    if sub in ("ordner", "oeffnen"):
        try:
            KB.ORDNER.mkdir(parents=True, exist_ok=True)
            os.startfile(str(KB.ORDNER))
        except Exception as e:
            ui.warn(f"Konnte den Ordner nicht öffnen: {e}")
        return
    if sub in ("weg", "entfernen", "kugel"):
        n = KB.entfernen(name)
        ui.success(f"🔮 {n} Bild(er) von {name} in den Papierkorb – die Kugel ist wieder eine Kugel."
                   if n else f"🔮 {name} hat keine Bilder – die Kugel ist schon eine Kugel.")
        return
    if sub != "malen":
        da = KB.vorhandene(name)
        ui.info(f"🔮 Kugel von {name}: " + (", ".join(sorted(k or "Standard" for k in da)) + " als Bild"
                                             if da else "die gemalte Kugel (kein Bild)"))
        try:
            from wache import steuerung
            z = steuerung.lesen()
            if z:
                ui.info(f"   Zustand: Stimmung {z.get('stimmung') or '–'} · "
                        f"{'versteckt' if z.get('versteckt') else 'sichtbar'} · Größe {z.get('groesse') or 64} px"
                        + (f" · zuletzt gesteuert von {z.get('wer')}" if z.get('wer') else ""))
        except Exception:
            pass
        ui.info("Nutzung:  /kugel malen [beschreibung]  ·  /kugel weg  ·  /kugel ordner  ·  "
                f"Dateien: {KB.ORDNER}\\{name}.png, {name}_froh.png, _ernst.png, _denkt.png")
        return
    import imagegen
    grund = imagegen.missing_reason_aktiv()
    if grund:
        ui.warn("Der Bild-Motor kann gerade nicht malen.")
        ui.info(grund)
        return
    beschreibung = rest
    if not beschreibung:
        if ctx.backend is None:
            ui.warn("Kein Modell verbunden – dann sag mir, wie sie aussieht: /kugel malen <beschreibung>")
            return
        with ui.thinking(f"{name} beschreibt sich"):
            try:
                beschreibung = (await ctx.backend.ask_once(
                    KB.SELBSTBESCHREIBUNG_PROMPT,
                    KB.selbstbeschreibung_system(name, PS.render_text(PS.active()),
                                                 sd=KB.sd_pipeline()))).strip()
            except Exception as e:
                ui.error(f"Selbstbeschreibung fehlgeschlagen: {e}")
                return
        beschreibung = beschreibung.strip().strip('"„“').splitlines()[0] if beschreibung else ""
        if not beschreibung:
            ui.warn("Keine Beschreibung bekommen – versuch /kugel malen <beschreibung>.")
            return
        ui.info(f"🔮 {name} über sich: „{beschreibung}“")
        wahl = await ask_confirm(f"Vier Stimmungsbilder mit dieser Beschreibung malen? ({imagegen.active_label()})",
                                 [("ja", "Ja, malen"), ("nein", "Nein, ich gebe selbst eine Beschreibung")])
        if wahl != "ja":
            ui.info("Dann: /kugel malen <deine Beschreibung>")
            return
    try:
        bilder = await _run_with_status(
            f"🔮 {name} malt sich ihr Gesicht",
            lambda on_status: KB.malen(name, beschreibung, on_status=on_status))
    except Exception as e:
        ui.error(f"Malen fehlgeschlagen: {e}")
        return
    ui.success(f"🔮 {len(bilder)} Bilder für {name}: " + ", ".join(p.name for p in bilder))
    for p in bilder[:2]:
        _bild_vorschau(str(p))
    ui.info("Die Kugel zeigt es beim nächsten Takt. Stimmung wechselt die Persönlichkeit selbst "
            "(Aktion kugel) – oder du sagst ihr „sei mal ernst“. Nicht gut? /kugel malen nochmal (neuer Seed).")


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
        if getattr(sys, "frozen", False) and any(s["id"] in ("venv", "torch", "pakete", "diffusers") for s in todo):
            # torch & Co. aus dem neuen venv greifen erst nach einem Neustart: das bisher
            # eingebundene Python ist in diesem Prozess schon geladen.
            ui.warn("🔄 Bitte NemiCLI einmal neu starten – erst dann nutzen Gedächtnis, "
                    "Krea und Wache das neue venv neben der exe.")
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
        ("Befehle", "✅ python, pip, pytest, git laufen direkt in diesem Ordner – live, Esc bricht ab"),
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


def _todo_zeigen() -> None:
    daten = coding.laden() if coding.aktiv() else None
    if daten is not None:
        ui.console.print(ui.todo_panel(daten))


def _coding_beenden() -> None:
    alt = coding.beenden()
    if alt is None:
        ui.info("Der Coding-Assistent war nicht an.")
        return
    ui.success(f"🟦 Coding-Assistent beendet. Die Ablage bleibt im Projekt: {alt / coding.ABLAGE}")
    mitschrift.notiz(f"Coding-Assistent beendet: {alt}")


async def code_befehl(ctx: "Ctx | None" = None, arg: str = "") -> None:
    """/code – Coding-Assistent starten oder Stand zeigen. /codeend schaltet ab."""
    unter = arg.strip()
    if unter.lower() in ("end", "ende", "aus", "stop", "off"):
        _coding_beenden()
        return
    if unter.lower() in ("status", "?", ""):
        if not coding.aktiv():
            ui.info("Der Coding-Assistent ist aus. Er startet von selbst bei einer Coding-Aufgabe "
                    "oder mit '/code <aufgabe>'.")
            return
        ui.info(f"🟦 {coding.status_text()}  ({coding.projekt()})")
        _todo_zeigen()
        return
    if ctx is None or getattr(ctx, "backend", None) is None:
        ui.warn("Kein Modell aktiv – wähle eins mit /model.")
        return
    mitschrift.notiz("Coding-Assistent per /code angefordert.")
    await converse(ctx.backend, "[System] Der Nutzer hat den Coding-Assistenten mit /code "
                   f"angefordert. Aufgabe: {unter}\nStarte ihn mit coding_start (Projektordner "
                   "absolut; ist er unklar, frag nach).")


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
                   "Ausgeführt wird in der Sandbox (AppContainer): nur NemiSandbox "
                   "und Internet, kein Zugriff auf deine Dateien.\n\n" + code)
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
    import gguflokal
    lokale = gguflokal.modelle()
    if lokale:                              # eigener GGUF-Motor: Modelle aus ModelGGUF/
        stufe1.insert(0, ("gguf", f"🧠  Lokal (eigener Motor)  ·  {len(lokale)} Modell(e)  ·  "
                                  "ohne Server, kein Key"))
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
    if wahl == "gguf":
        opts = [(M.make_ref("gguf", name), gguflokal.beschreibung(name)) for name in lokale]
        opts.append(("__cancel__", "Zurück / Abbrechen"))
        ref = await ask_confirm(f"Welches lokale Modell?  (Kontext {gguflokal.kontext():,} Token, "
                                f"höchstens {gguflokal.tok_s() or '∞'} Token/s)".replace(",", "."), opts)
        return None if ref == "__cancel__" else ref
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
    elif M.split_ref(ref)[0] == "gguf":
        where = "lokal im eigenen Motor – das erste Laden dauert ein paar Sekunden"
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
    _neuer_chat_nach_wechsel()
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
                    import profilordner
                    alt_ordner = profilordner.wurzel() / profilordner.ordnername(p.name)
                    neu_ordner = profilordner.wurzel() / profilordner.ordnername(neu)
                    if alt_ordner.is_dir() and not neu_ordner.exists():
                        alt_ordner.rename(neu_ordner)               # Chats, Bilder … ziehen mit
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
        wechsel = p.key != PS.active().key
        PS.set_active(p.key)
        ui.success(f"🎭 Persönlichkeit: {p.name} – gilt ab der nächsten Nachricht.")
        _sprueche_im_hintergrund()
        if wechsel:
            _neuer_chat_nach_wechsel()
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
        wechsel = p.key != PS.active().key
        PS.set_active(p.key)
        ui.success(f"🎭 Persönlichkeit: {p.name} – gilt ab der nächsten Nachricht.")
        _sprueche_im_hintergrund()
        if wechsel:
            _neuer_chat_nach_wechsel()
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
        import persona
        persona.chat_gewechselt(self.current_chat)          # Kern-Gedächtnis: neue Momentaufnahme je Chat
        SESSION.update(model=self.model or "—",
                       strength=self.strength if self.strength_active() else None,
                       chat=self.current_chat,
                       ctx_max=pricing.context_window(self.model))


def _untermenue_eintraege(ctx: Ctx, cmd: str) -> list[tuple[str, str, str | None]]:
    """Auswahl für einen Befehl ohne Zusatz – fest aus commands.UNTERMENUES oder
    nach aktuellem Stand (Modus, Theme, Denkstufe)."""
    import commands as C
    if cmd in C.UNTERMENUES:
        return C.UNTERMENUES[cmd]
    haken = lambda an: "✓ " if an else "   "
    if cmd == "/modus":
        return [(k, f"{haken(k == modes.current())}{sym} {name}", None) for k, sym, name in modes.MODES]
    if cmd == "/theme":
        return [(k, f"{haken(k == ui.current_theme())}{k}", None) for k in ui.THEMES]
    if cmd == "/kontext":
        prov, name = M.split_ref(ctx.model or "")
        if prov != "gguf":
            return []
        import gguflokal
        pro_token, grenze = gguflokal.kv_bytes_pro_token(name), gguflokal.kontext_max(name)
        aktuell = gguflokal.kontext(name)
        kosten = lambda n: f"  ·  Gesprächsspeicher ~{n * pro_token / 2**30:.1f} GB" if pro_token else ""
        stufen = [n for n in gguflokal.KONTEXT_STUFEN if not grenze or n < grenze]
        eintraege = [(str(n), f"{haken(n == aktuell)}{n // 1024}k{kosten(n)}", None) for n in stufen]
        if grenze:
            eintraege.append(("max", f"{haken(grenze == aktuell)}max ({grenze // 1024}k){kosten(grenze)}", None))
        acht = gguflokal.kv_bits() == 8
        eintraege += [("16bit", f"{haken(not acht)}Gesprächsspeicher 16 Bit – schneller", None),
                      ("8bit", f"{haken(acht)}Gesprächsspeicher 8 Bit – etwa halber VRAM, in langen Chats langsamer",
                       None)]
        return eintraege
    if cmd == "/embeddings":
        aktiv = memory.modell_name()
        eintraege = [(d.name, f"{haken(d.name == aktiv)}{d.name}  ·  {_embedding_art(d)}", None)
                     for d in memory.modelle()]
        return eintraege + [("huggingface", "   🤗 Weitere Modelle auf Hugging Face", None)]
    if cmd == "/staerke" and ctx.strength_active():
        return [(k, f"{haken(k == ctx.strength)}{k} – {text}", None)
                for k, text in M.strength_menu(ctx.model).items()]
    return []


HF_EMBEDDINGS = "https://huggingface.co/models?library=gguf&pipeline_tag=feature-extraction&sort=trending"


def _embedding_art(ordner) -> str:
    import embedder
    return "GGUF, gepackt auf der GPU" if embedder.gguf_datei(ordner) is not None else "safetensors (transformers)"


async def _untermenue(ctx: Ctx, cmd: str) -> str | None:
    """Zeigt die Auswahl und gibt den Zusatz zurück – None, wenn abgebrochen
    oder der Befehl keine Auswahl hat ("" = Befehl ohne Zusatz ausführen)."""
    import commands as C
    eintraege = _untermenue_eintraege(ctx, cmd)
    if not eintraege:
        return ""
    optionen = [(str(i), anzeige) for i, (_, anzeige, _) in enumerate(eintraege)]
    optionen.append((C.ZURUECK, "↩ Zurück"))
    wahl = await ask_confirm(f"{cmd} – was möchtest du?  (↑/↓ oder 1–9, Enter)", optionen)
    if wahl == C.ZURUECK or not str(wahl).isdigit():
        return None
    zusatz, _, frage = eintraege[int(wahl)]
    if "{}" in zusatz:
        antwort = (await ask_text(frage or "Angabe?")).strip()
        if not antwort and not zusatz.startswith("malen"):
            return None
        zusatz = zusatz.replace("{}", antwort)
    return " ".join(zusatz.split())


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
        if not arg.strip():
            zusatz = await _untermenue(ctx, cmd)
            if zusatz is None:
                return None
            arg = zusatz
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
            if sub in ("indexieren", "index", "vektorisieren"):
                o = indexdb.offen()
                ui.info(f"📚 Gedächtnis-DB: {o['brocken']} Brocken · {o['ohne_vektor']} ohne Vektor · "
                        f"{o['berichte_offen']} Berichte noch nicht drin – hole alles nach "
                        + ("(Balken in der Statuszeile, du kannst weiterchatten)." if ui.TUI is not None else "…"))
                await bibliothekar(ctx.current_chat, alles=True)
            elif sub in ("leeren", "loeschen", "clear"):
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
                smart = (f"🧠 Stichworte + Bedeutung ({memory.encoder_status()})"
                         if memory.embedding_ready()
                         else f"🔤 nur Stichworte – {memory.encoder_status()}")
                ui.info(f"🧠 Langzeitgedächtnis: {len(entries)} Erinnerung(en)  ·  Suche: {smart}")
                if not entries:
                    ui.info("Noch nichts gemerkt. Sag z.B. 'merk dir, ich mag knappe Antworten' "
                            "oder NemiCLI merkt sich Wichtiges von selbst.")
                else:
                    for e in entries:
                        ui.console.print(f"  [dim]#{e['id']} · {e['ts']} · {e['kind']}[/dim]  {e['text']}")
                    ui.info("Löschen: /gedaechtnis vergiss <nummer>  ·  alles: /gedaechtnis leeren")
                try:
                    o = indexdb.offen()
                    ui.info(f"📚 Vektor-DB: {o['brocken']} Brocken · {o['ohne_vektor']} ohne Vektor · "
                            f"{o['berichte_offen']} Berichte offen  →  /gedaechtnis indexieren holt alles nach")
                except Exception:
                    pass
        elif cmd == "/aufraeumen":
            n = memory.count()
            if n < 2:
                ui.info("Zu wenig im Gedächtnis zum Aufräumen (mindestens 2 Notizen).")
            else:
                with ui.thinking("räume das Gedächtnis auf"):
                    r = await asyncio.to_thread(memory.consolidate)
                await bibliothekar()
                memory.release_encoder()
                if r["merged"]:
                    ui.success(f"🗜️ Aufgeräumt: {r['merged']} ähnliche Notiz(en) verschmolzen "
                               f"({r['before']} → {r['after']}).")
                else:
                    ui.info(f"Alles schon ordentlich – nichts zu verschmelzen ({n} Notizen).")
        elif cmd == "/reflektieren" and arg.split()[:1] == ["auto"]:
            import lernschleife as LS
            wahl = (arg.split() + [""])[1].lower()
            if wahl in ("an", "aus"):
                LS.setzen(wahl == "an")
            ui.info("🪞 Automatisch reflektieren: " + (
                "AN – nach Korrekturen, Fehlern und größeren Aufgaben (nie nach Web-Inhalt)"
                if LS.an() else "AUS – nur noch mit /reflektieren"))
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
        elif cmd == "/name":
            await name_befehl(arg)
        elif cmd == "/skills":
            await skills_befehl(arg)
        elif cmd == "/gui":
            global GUI_BRUECKE  # noqa: PLW0603
            if GUI_BRUECKE is None:
                GUI_BRUECKE = await start_gui(ctx)
            if GUI_BRUECKE.verbunden():
                GUI_BRUECKE.senden({"t": "vorne"})
                ui.info("🖥 Das GUI-Fenster ist schon offen.")
            else:
                gui_oeffnen(GUI_BRUECKE)
                ui.success("🖥 Das GUI-Fenster öffnet sich – es ist mit dieser Sitzung verbunden.")
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
                config.update(uebung=False)                  # bleibt aus, auch nach Neustart
                ui.info("🎓 Übungsmodus aus – dauerhaft, bis du /uebung an sagst.")
            elif sub in ("an", "on"):
                if ui.TUI is not None:
                    ui.TUI.practice_on = True
                    ui.TUI._practice_consent = None      # Cloud-Frage neu stellen
                config.update(uebung=True)
                ui.info("🎓 Übungsmodus an – nach ~10 min Ruhe wird eine Übung vorbereitet. "
                        "Schreiben und Ausführen erst nach deiner Codefreigabe. Bleibt gespeichert.")
            elif sub in ("jetzt", "now"):
                if ctx.backend is None:
                    ui.warn("Kein Modell aktiv – erst mit /model eins wählen.")
                else:
                    if ui.TUI is not None:
                        await ui.TUI.run_practice_once(ctx, practice_round)
                    else:
                        await practice_round(ctx, _esc_pressed)
            else:
                an = (ui.TUI.practice_on if ui.TUI is not None
                      else bool(config.load().get("uebung", True)))
                ui.info(f"🎓 Übungsmodus ist {'an' if an else 'aus'} (gespeichert in der Config). "
                        "Nach ~10 min Ruhe bereitet NemiCLI Programme vor; vor jedem "
                        "Schreiben und Ausführen prüfst du den vollständigen Code. "
                        "/uebung an · aus · jetzt")
        elif cmd == "/sandbox":
            import sandbox
            teile = arg.strip().split(maxsplit=1)
            sub = teile[0].lower() if teile else ""
            ziel = teile[1].strip().strip('"') if len(teile) > 1 else ""
            try:
                if sub in ("freigeben", "frei", "add"):
                    if not ziel:
                        ui.warn("Welcher Ordner? /sandbox freigeben <Ordner>")
                    else:
                        schreiben = False
                        m = re.match(r'^(.*?)\s+(schreiben|rw)$', ziel, re.IGNORECASE)
                        if m:
                            ziel, schreiben = m.group(1).strip().strip('"'), True
                        if (grund := actions._read_guard(ziel) or
                                (actions._guard(ziel) if schreiben else None)):
                            ui.warn(grund)
                        else:
                            p = sandbox.projekt_freigeben(Path(ziel), schreiben=schreiben)
                            if schreiben:
                                ui.success(f"🧪 Freigegeben (lesen + schreiben): {p} – Code aus diesem "
                                           "Projekt läuft dort in der Sandbox und darf darin ändern.")
                            else:
                                ui.success(f"🧪 Freigegeben (nur lesen): {p} – Code aus diesem Projekt "
                                           "läuft dort in der Sandbox; schreiben kann er nur in seinen "
                                           "Lauf-Ordner. Mit '… schreiben' darf er auch dort ändern.")
                elif sub in ("entziehen", "weg", "remove"):
                    if not ziel:
                        ui.warn("Welcher Ordner? /sandbox entziehen <Ordner>")
                    else:
                        ui.success(f"🧪 Freigabe entzogen: {sandbox.projekt_entziehen(Path(ziel))}")
                else:
                    try:
                        py = str(sandbox.interpreter()[0])
                    except sandbox.SandboxFehler as exc:
                        py = f"– ({exc})"
                    projekte = [f"{p} ({'lesen + schreiben' if sandbox.schreibbar(p) else 'nur lesen'})"
                                for p in sandbox.projekte()]
                    pakete_dir = sandbox.ARBEIT / sandbox.PAKETE_ORDNER
                    pakete = sorted(d.name.split("-")[0] for d in pakete_dir.glob("*.dist-info")) \
                        if pakete_dir.is_dir() else []
                    ui.info("🧪 Sandbox (Windows-AppContainer „NemiCLI.Sandbox“): Code der KI läuft "
                            "isoliert – Internet ja, deine Dateien, Registry und andere Programme nein.\n"
                            f"  Arbeitsordner: {sandbox.ARBEIT}\n"
                            f"  Python:        {py}\n"
                            "  Projekte:     " + (", ".join(map(str, projekte)) or "keine") + "\n"
                            "  Pakete:        " + (", ".join(pakete) or "keine") + "\n"
                            "  /sandbox freigeben <Ordner> [schreiben] · /sandbox entziehen <Ordner>")
            except sandbox.SandboxFehler as exc:
                ui.warn(f"🧪 {exc}")
        elif cmd == "/embeddings":
            wahl = arg.strip()
            if wahl.lower() in ("huggingface", "hf"):
                webbrowser.open(HF_EMBEDDINGS)
                ui.info("🤗 Hugging Face ist offen. Ein Modell kommt als eigener Ordner nach "
                        f"{memory.EMBED_ORDNER} – und vor dem ersten Laden die SHA-256 der Datei "
                        "mit der Angabe auf der Seite vergleichen.")
            elif not wahl:
                ui.info(f"🧬 Embedding-Modell: {memory.encoder_status()}")
            else:
                ordner = {d.name.lower(): d for d in memory.modelle()}.get(wahl.lower())
                if ordner is None:
                    ui.warn(f"🧬 Kein vollständiges Modell „{wahl}“ in {memory.EMBED_ORDNER}.")
                    return None
                alt = memory.modell_kennung()
                config.update(embedding_modell=ordner.name)
                memory.unload_encoder()
                neu = memory.modell_kennung()
                ui.success(f"🧬 Embedding-Modell: {ordner.name} ({_embedding_art(ordner)}) – lädt bei Bedarf.")
                if alt and neu != alt:
                    ui.info("Andere Vektoren als bisher: der Bibliothekar rechnet das Gedächtnis nach und "
                            "nach neu um (/gedaechtnis indexieren = sofort). Bis dahin hilft die Stichwortsuche.")
        elif cmd == "/kontext":
            prov, name = M.split_ref(ctx.model or "")
            if prov != "gguf":
                ui.info("📏 /kontext gilt für lokale Modelle im eigenen Motor – Cloud-Modelle haben "
                        "ihren festen Kontext.")
                return None
            import gguflokal
            wahl = arg.strip().lower().replace(".", "")
            if not wahl:
                ui.info(f"📏 Kontext für {name}: {gguflokal.kontext(name):,} Token".replace(",", "."))
                return None
            if wahl in ("8bit", "16bit"):
                gguflokal.setze_kv_bits(8 if wahl == "8bit" else 16)
                ui.success(f"📏 Gesprächsspeicher: {wahl[:-3]} Bit" +
                           ("  ·  das Modell wird beim nächsten Senden neu geladen." if gguflokal.geladen() else ""))
                return None
            if wahl == "max":
                token = 0
            else:
                try:
                    token = int(float(wahl[:-1]) * 1024) if wahl.endswith("k") else int(wahl)
                except ValueError:
                    ui.warn("Kontext als Zahl (32768), mit k (32k) oder „max“ angeben.")
                    return None
            gguflokal.setze_kontext(token)
            neu = gguflokal.kontext(name)
            ui.success(f"📏 Kontext: {neu:,} Token".replace(",", ".") +
                       ("  ·  das Modell wird beim nächsten Senden neu geladen." if gguflokal.geladen() else ""))
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
                ui.warn("Unbekannter Modus. Zur Wahl: chat · lesen · normal · auto · autoan")
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
        elif cmd == "/wache":
            await _wache_befehl(ctx, arg.strip())
        elif cmd == "/kugel":
            await _kugel_befehl(ctx, arg.strip())
        elif cmd in ("/undo", "/rueckgaengig", "/papierkorb"):
            eintraege = snapshot.letzte(10)
            if not eintraege:
                ui.info(f"🗑 Papierkorb ist leer. Ordner: {snapshot.ORDNER}")
                return None
            opts = [(str(i), f"{e['zeit']} · {e['werkzeug']} · {e['original']}")
                    for i, e in enumerate(eintraege)]
            opts.append(("x", "Nichts wiederherstellen"))
            mb = snapshot.groesse() / 1_048_576
            wahl = await ask_confirm(
                f"🗑 Papierkorb ({mb:.1f} MB) – was soll zurück an seinen Platz?", opts)
            if wahl == "x" or not wahl.isdigit():
                return None
            e = eintraege[int(wahl)]
            if Path(e["original"]).exists():
                sicher = await ask_confirm(
                    f"Am Ziel liegt schon etwas: {e['original']}\n  Das wird vorher selbst "
                    "in den Papierkorb gesichert. Trotzdem zurückholen?",
                    [("ja", "Ja, zurückholen"), ("nein", "Nein")])
                if sicher != "ja":
                    return None
            try:
                ziel = snapshot.wiederherstellen(e)
                ui.success(f"↩ Wiederhergestellt: {ziel}")
                protokoll.schreibe("undo", f"Wiederhergestellt: {ziel}", "success",
                                   veraendernd=True, wer="Nutzer")
            except Exception as exc:
                ui.error(f"Wiederherstellen fehlgeschlagen: {exc}")
        elif cmd == "/limit":
            if arg.strip().isdigit():
                neu = sicherheit.setze_loesch_limit(int(arg))
                ui.success(f"🛑 Lösch-Limit: {neu} Dateien pro Sitzung (gespeichert).")
                protokoll.schreibe("limit", f"Lösch-Limit auf {neu} gesetzt", "success",
                                   veraendernd=True, wer="Nutzer")
            else:
                ui.kv_panel("🛑 Obergrenze fürs Löschen", [
                    ("Limit pro Sitzung", f"{sicherheit.loesch_limit()} Dateien"),
                    ("Diese Sitzung gelöscht", str(sicherheit.geloescht())),
                    ("Ändern", "/limit <zahl>  – nur du, die KI hat kein Werkzeug dafür"),
                    ("Wirkung", "mehr als das Limit auf einmal oder in Summe → STOPP, "
                                "auch ohne Rückfrage"),
                ])
        elif cmd == "/whitelist":
            sicherheit.whitelist_neu_laden()
            liste = sicherheit.whitelist()
            if arg.strip():
                w = arg.strip()
                ui.info(f"'{w}' → " + ("harmlos (eine Frage)" if sicherheit.befehl_harmlos(w)
                                       else "NICHT auf der Whitelist (fragt zweimal)"))
            else:
                ui.text_panel(
                    f"✅ Befehls-Whitelist ({len(liste)} Einträge) – {sicherheit.WHITELIST_DATEI}",
                    "Diese Befehlsanfänge gelten bei `befehl` als harmlos: eine Frage statt zwei.\n"
                    "Alles, was nur liest (wie bei `abfragen`), zählt automatisch dazu.\n"
                    "Bearbeiten: die JSON-Datei im Programm-Ordner – die KI kommt da nicht ran.\n\n"
                    + "\n".join(f"  • {e}" for e in liste))
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
        elif cmd in ("/code", "/codeend"):
            await code_befehl(ctx, "end" if cmd == "/codeend" else arg)
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
        elif cmd in ("/schluessel", "/schlüssel"):
            teile = arg.split(None, 1)
            sub = teile[0].lower() if teile else ""
            if sub in ("aus", "zu", "weg", "ende"):
                sicherheit.schluessel_zurueck()
                ui.info("🔒 Schlüssel abgezogen – der Programm-Ordner ist wieder zu.")
            elif sub.replace(",", ".").replace(".", "", 1).isdigit():
                s = sicherheit.schluessel_setzen(float(sub.replace(",", ".")),
                                                 teile[1] if len(teile) > 1 else "")
                bis = time.strftime("%H:%M", time.localtime(s["bis"]))
                ui.success(f"🔑 Schlüssel steckt bis {bis}: {PS.active().name} darf im Programm-Ordner "
                           "lesen und Änderungen vorschlagen – jede Änderung fragt dich einzeln."
                           + (f"  Aufgabe: {s['aufgabe']}" if s["aufgabe"] else ""))
                protokoll.schreibe("schluessel", f"Programm-Ordner bis {bis} freigegeben"
                                   + (f" – {s['aufgabe']}" if s["aufgabe"] else ""),
                                   "success", veraendernd=True, wer="Nutzer", stufe="riskant")
            else:
                s = sicherheit.schluessel_aktiv()
                if s:
                    ui.info(f"🔑 Schlüssel steckt noch {s['rest_min']} min"
                            + (f" – Aufgabe: {s['aufgabe']}" if s["aufgabe"] else "") + ".  /schluessel aus")
                else:
                    ui.info("🔒 Programm-Ordner ist zu. /schluessel <minuten> [aufgabe] gibt ihn auf Zeit frei "
                            "(jede Änderung fragt trotzdem), /schluessel aus schließt wieder.")
        elif cmd in ("/spiel", "/spiele"):
            global SPIELE_SERVER
            if SPIELE_SERVER is None:
                try:
                    SPIELE_SERVER = await start_spiele(ctx)
                except Exception as e:
                    ui.error(f"Spiele-Start fehlgeschlagen: {e}")
                    return None
                ui.success(f"🎲 Spiele offen: {SPIELE_SERVER.url}")
                ui.info("Schach · Mühle · Dame · TicTacToe – im Browser, gegen "
                        f"{PS.active().name}. Das Terminal bleibt der Motor; /exit beendet beides.")
            else:
                ui.info(f"🎲 Spiele laufen schon: {SPIELE_SERVER.url}")
            try:
                webbrowser.open(SPIELE_SERVER.url)
            except Exception:
                pass
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
                if akt_backend == "krea":
                    import krea
                    ui.info(f"🟣 Krea 2 – eigene Pipeline, kein Negativ-Prompt, CFG 1, "
                            f"{krea.DEFAULTS['steps']} Schritte. Modelle in {krea.krea_dir()}. "
                            "/bildmodel wechselt das Modell.")
                elif akt_backend == "webui":
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
            import krea
            krea_models = krea.discover()
            krea_aktiv = krea.chosen_model() if akt_backend == "krea" else None
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
                treffer = [n for n in krea_models if arg.strip().lower() in n.lower()]
                if len(treffer) == 1:
                    krea.set_chosen_model(treffer[0])
                    ui.success(f"🟣 Bild-Modell (Krea 2): {treffer[0]}")
                    return None
                # dann eigene SD-Checkpoints, dann WebUI-Modelle
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
            for name in krea_models:
                mark = "✓  " if (akt_backend == "krea" and name == krea_aktiv) else "   "
                opts.append((f"krea::{name}", f"{mark}🟣 {name}  (Krea 2)"))
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
            if not cks and not krea_models and not webui_models and not comfy_models:
                ui.warn("Noch kein Bild-Modell da. Entweder eine .safetensors hier ablegen:")
                ui.console.print(f"  [dim]{imagegen.CKPT_DIRS[0]}[/dim]")
                ui.info("… oder eine Forge/A1111-WebUI mit --api starten "
                        f"(erwartet unter {sdwebui.host()}).")
            wahl = await ask_confirm("Welches Modell zum Bildermalen?  "
                                     "(🖥 SD-Pipeline · 🟣 Krea 2 · 🌐 Forge/A1111 · 🧩 ComfyUI)",
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
            if art == "krea":
                krea.set_chosen_model(name)
                ui.success(f"🟣 Bild-Modell (Krea 2): {name}  ·  kein Negativ-Prompt, CFG 1, "
                           f"{krea.DEFAULTS['steps']} Schritte, "
                           f"{krea.DEFAULTS['size'][0]}×{krea.DEFAULTS['size'][1]}")
            elif art == "comfy":
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
        if GUI_BRUECKE is not None:
            _gui_spiegeln(ctx)
        return None

    # --- Normaler Chat ----------------------------------------------------
    if not text.strip():
        return None
    if ctx.backend is None:
        ui.warn("Kein Modell aktiv. Wähle eins mit /model (z.B. /model gemma-12b).")
        return None

    text = emoji.expand(text)   # =)  <3  :feuer:  ->  echte Emojis
    getippt = text              # fürs GUI-Fenster: wie eingegeben, ohne angehängte Datenblöcke

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
            if GUI_BRUECKE is not None:
                GUI_BRUECKE.senden({"t": "nutzer", "text": getippt, "quelle": "terminal"})
                GUI_BRUECKE.senden({"t": "busy", "on": True})
            records = await converse(ctx.backend, text, images=imgs)
            aus_dem_netz = actions.web_tainted()
            ctx.current_prompts.append(text)
            chatstore.save(ctx.current_chat, ctx.backend.messages, ctx.current_prompts, ctx.model or "—")
            n_code = learn.capture_from_answer(text, ctx.backend.messages, ctx.current_chat)
            await bibliothekar(ctx.current_chat)
            memory.release_encoder()
        lernen_planen(ctx, getippt, records, aus_dem_netz, pricing.weggefallen_holen(ctx.backend))
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
    finally:                         # auch nach Esc (CancelledError) oder Fehler
        if GUI_BRUECKE is not None:
            GUI_BRUECKE.senden({"t": "busy", "on": False})
            _gui_spiegeln(ctx)
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


# ---------------------------------------------------------------------------
# Hintergrund-Auftrag (Zeitplan): `main.py --auftrag <name>`
# ---------------------------------------------------------------------------
# Die Windows-Aufgabenplanung startet NemiCLI so – ohne Fenster, ohne Nutzer.
# Die Persönlichkeit bekommt den Auftrag aus Zeitplan/<name>.json als Nachricht,
# arbeitet ihn im Modus „lesen" ab (abfragen, datei_lesen, web_suche … laufen;
# alles Ändernde ist gesperrt, da drückt niemand F8) und ihre letzte Antwort
# wird als Bericht nach Berichte/ geschrieben. Beim nächsten normalen Start
# zeigt main() die erste Zeile jedes neuen Berichts.

async def _nie_freigeben(*_a, **_k):
    """Im Hintergrund gibt es keine Freigabe – jede Rückfrage ist ein Nein."""
    return "no"


async def _nie_pruefen(*_a, **_k):
    return False


async def auftrag_lauf(name: str) -> int:
    global ask_confirm, review_action_confirm
    import zeitplan
    auftrag = zeitplan.lade_auftrag(name)
    if not auftrag:
        print(f"Kein Auftrag namens {name!r} in {zeitplan.ORDNER}.")
        return 1
    name = auftrag["name"]
    ask_confirm = _nie_freigeben
    review_action_confirm = _nie_pruefen
    modes.set_mode("lesen")

    cfg = config.load()
    ref = _migrate_ref(cfg.get("model"))
    backend = None
    fehler = ""
    if ref:
        try:
            backend = await make_backend(ref, None, cfg.get("strength", M.DEFAULT_STRENGTH))
        except Exception as e:
            fehler = f"Modell '{ref}' ließ sich nicht laden: {e}"
    else:
        fehler = "Kein Modell eingestellt (/model)."

    mitschrift.leeren()
    mitschrift.setze_quelle(lambda: {"chat": f"Auftrag {name}", "model": ref or "-",
                                     "persona": PS.active().name, "modus": modes.label(),
                                     "ordner": str(Path.cwd())})
    stats.start_session(ref)
    antwort = ""
    if backend is not None:
        text = (f"[Hintergrund-Auftrag „{name}“ aus deinem Zeitplan, "
                f"{time.strftime('%d.%m.%Y %H:%M')}. Es ist niemand da, der antwortet oder "
                "etwas freigibt: Du kannst nur lesen und abfragen. Deine LETZTE Antwort wird "
                "als Bericht gespeichert – fang sie mit einer klaren Zeile an (✅ Alles OK / "
                "⚠️ n Auffälligkeiten) und schreib dann, was du gesehen hast.]\n\n"
                + auftrag["auftrag"])
        try:
            await asyncio.wait_for(converse(backend, text), timeout=25 * 60)
        except asyncio.TimeoutError:
            fehler = "Abgebrochen: der Auftrag lief länger als 25 Minuten."
        except Exception as e:
            fehler = f"Abgebrochen mit Fehler: {e}"
        antwort = mitschrift.letzte_antwort()
    stats.end_session()

    if not antwort:
        antwort = "❌ Kein Bericht – " + (fehler or "die Persönlichkeit hat nichts geantwortet.")
    elif fehler:
        antwort = f"⚠️ {fehler}\n\n{antwort}"
    pfad = zeitplan.bericht_pfad(name)
    pfad.write_text(
        f"<!-- NemiCLI-Bericht: {name} · {time.strftime('%d.%m.%Y %H:%M')} · {ref or '-'} -->\n\n"
        + antwort.strip() + "\n\n---\n\n## Ablauf\n\n" + mitschrift.als_markdown(),
        encoding="utf-8")
    print(f"Bericht: {pfad}")
    return 0 if backend is not None and not fehler else 1


# ---------------------------------------------------------------------------
# Systemwache im Hintergrund: `main.py --wache`
# ---------------------------------------------------------------------------
# Sensoren, Regeln und Modell laufen in tools/wache; hier steht nur, wie die
# aktive Persönlichkeit geweckt wird: derselbe Weg wie ein Zeitplan-Auftrag
# (Modus lesen, keine Freigaben), plus die Wache-Werkzeuge zum Bewerten und
# Justieren. Die letzte Antwort wird der Bericht in Berichte/Wache_….md.

async def _wache_befehl(ctx: "Ctx", arg: str) -> None:
    from wache import zugang as Z, justierung as J, werkzeuge as W, regeln as R
    teile = arg.split(None, 1)
    sub = (teile[0].lower() if teile else "")
    rest = teile[1].strip() if len(teile) > 1 else ""
    e = J.einstellungen()
    if sub in ("", "status"):
        info = Z.laeuft()
        sp = Z.speicher()
        st = sp.alarm_statistik()
        inv = sp.inventar_zaehlung()
        ui.kv_panel("🛡 Systemwache", [
            ("Hintergrund-Prozess", f"läuft (PID {info['pid']})" if info else "läuft NICHT – /wache start"),
            ("Autostart mit NemiCLI", "an" if e.get("aktiv", True) else "aus"),
            ("Autostart mit Windows", "an" if Z.autostart_aktiv() else "aus"),
            ("Ereignisse gespeichert", str(sp.anzahl_ereignisse())),
            ("Prozess-Profile", str(sp.anzahl_profile())),
            ("Inventar", ", ".join(f"{n} {k}" for k, n in sorted(inv.items())) or "noch keins"),
            ("Alarme", f"{st.get('offen', 0)} offen · {st.get('gesehen', 0)} gesehen · "
                       f"{st.get('harmlos', 0)} harmlos · {st.get('echt', 0)} ECHT"),
            ("ML-Schwelle", f"{e['schwelle']}  (Grenzen {e['grenzen']['schwelle_min']}–{e['grenzen']['schwelle_max']})"),
            ("Training", f"erstes ab {e['min_training']} Ereignissen, dann alle {e['training_alle']}"),
            ("Wecken ab", f"{e['trigger_ab']}" + (" + ML-Anomalien" if e.get("trigger_ml") else "")),
            ("Stumme Regeln", ", ".join(e.get("stumme_regeln") or []) or "keine"),
            ("Daten", str(Z.DB_PFAD)),
        ])
        ui.info("/wache alarme · ereignisse · regeln · justierungen · inventar · training · fullscan · "
                "schwelle <x> · trigger <stufe> · stumm <R00x> · laut <R00x> · start · stop · "
                "an · aus · autostart an|aus · kugel an|aus · reset · rueckgaengig")
    elif sub == "start":
        ui.info(Z.starten(paths.INSTALL))
    elif sub == "stop":
        ui.info(Z.stoppen())
    elif sub == "an":
        J.speichern({"aktiv": True}); ui.success("Wache startet künftig automatisch mit NemiCLI.")
        if not Z.laeuft():
            ui.info(Z.starten(paths.INSTALL))
    elif sub == "aus":
        J.speichern({"aktiv": False}); ui.info(Z.stoppen()); ui.info("Wache startet nicht mehr automatisch.")
    elif sub == "autostart":
        an = rest.lower() in ("an", "on", "ein", "ja")
        ui.info(Z.autostart_setzen(an, paths.INSTALL))
    elif sub == "kugel":
        if rest.lower() in ("an", "aus", "on", "off"):
            an = rest.lower() in ("an", "on")
            J.speichern({"kugel": an})
            ui.success(f"Schwebekugel {'an' if an else 'aus'} – gilt, sobald kein Terminal offen ist"
                       + ("" if an else "; das Schild neben der Uhr bleibt") + ".")
        else:
            from wache import kugel as K
            ui.kv_panel("🟢 Schwebekugel", [
                ("Zustand", "an" if e.get("kugel", True) else "aus"),
                ("Sichtbar", "nur wenn kein NemiCLI-Terminal offen ist"),
                ("Grüße", f"{K.GRUESSE_DATEI} – die Persönlichkeit darf sie selbst schreiben"),
                ("Klick", "kleines Chatfenster: reden · 📸 Screenshot · Bilder · Alles ok?"),
                ("Ändern", "/wache kugel an|aus"),
            ])
    elif sub == "alarme":
        ui.text_panel("🛡 Alarme", W.alarme({"anzahl": rest or 30}) if not rest or rest.isdigit()
                      else W.alarme({"id": rest}))
    elif sub == "ereignisse":
        ui.text_panel("🛡 Ereignisse", W.ereignisse({"anzahl": 40, "prozess": rest}))
    elif sub == "inventar":
        ui.text_panel("🛡 Inventar", W.inventar({"art": rest}))
    elif sub == "regeln":
        ui.text_panel("🛡 Regeln", W.regeln_text())
    elif sub in ("bekannt", "bekanntes"):
        if rest.lower().startswith("vergessen "):
            key = rest[10:].strip().lower()
            key = key if ":" in key else "prozess:" + key
            ui.info("Vergessen." if Z.speicher().bekannt_vergessen(key) else f"Nichts unter {key}.")
        else:
            ui.text_panel("🛡 Bekannt (harmlos, mit Bezeichnung)", W.bekannt_text(Z.speicher(), rest.lower()))
    elif sub == "justierungen":
        js = Z.speicher().justierungen(20)
        ui.text_panel("🛡 Justierungen", "\n".join(
            f"{time.strftime('%d.%m. %H:%M', time.localtime(j['zeit']))} · {j['wer']}: {j['was']} "
            f"{j['alt']} → {j['neu']} – {j['begruendung']}" for j in js) or "Noch keine.")
    elif sub == "rueckgaengig":
        ui.info(J.letzte_rueckgaengig(Z.speicher(), Z.motor))
    elif sub in ("schwelle", "trigger", "cooldown"):
        try:
            ui.success(J.anwenden(sub if sub != "trigger" else "trigger_ab", rest, wer="Nutzer",
                                  begruendung="vom Nutzer über /wache gesetzt", speicher=Z.speicher(),
                                  motor=Z.motor))
        except J.Grenzverletzung as exc:
            ui.warn(str(exc))
    elif sub in ("stumm", "laut"):
        try:
            ui.success(J.anwenden("regel_stumm", f"{rest}={'aus' if sub == 'stumm' else 'an'}",
                                  wer="Nutzer", begruendung="vom Nutzer über /wache gesetzt",
                                  speicher=Z.speicher(), motor=Z.motor))
        except J.Grenzverletzung as exc:
            ui.warn(str(exc))
    elif sub == "grenzen":
        ui.kv_panel("🛡 Grenzen für die Persönlichkeit", [(k, str(v)) for k, v in e["grenzen"].items()])
        ui.info("Ändern in nemicli.config.json unter wache.grenzen (nur du).")
    elif sub == "training":
        if Z.motor is not None:
            ui.info(str(Z.motor.trainieren("Nutzer")))
        else:
            ui.info("Training läuft im Hintergrund-Prozess: Tray-Menü → „Modell jetzt trainieren“. "
                    "Ohne laufende Wache trainiert der Selbsttest (/wache selbsttest).")
    elif sub in ("fullscan", "vollscan"):
        await _vollscan_befehl(rest.lower())
    elif sub == "selbsttest":
        from wache import selbsttest as ST
        ui.text_panel("🛡 Selbsttest", ST.laufen())
    elif sub == "reset":
        sicher = await ask_confirm("Wirklich Modell, Profile und Inventar der Wache löschen? "
                                   "Ereignisse und Alarme bleiben.", [("ja", "Ja, von null lernen"), ("nein", "Nein")])
        if sicher == "ja":
            if Z.motor is not None:
                Z.motor.lernen_zuruecksetzen()
            else:
                Z.speicher().lernen_zuruecksetzen()
                from wache import MODELL_ORDNER
                for p in MODELL_ORDNER.glob("*.json"):
                    p.unlink(missing_ok=True)
            ui.success("Wache lernt von null – Inventar beim nächsten Start neu.")
            protokoll.schreibe("wache", "Lernen zurückgesetzt (Nutzer)", "success", veraendernd=True, wer="Nutzer")
    else:
        ui.warn("Unbekannt. /wache zeigt die Möglichkeiten.")


async def _vollscan_befehl(rest: str) -> None:
    """/wache fullscan [status|stop]: alle Laufwerke als Baseline der Wache."""
    from wache import vollscan as VS
    if rest == "stop":
        ui.info(VS.stoppen())
        return
    if rest != "status" and not VS.laeuft():
        letzter = VS.stand_lesen()
        frage = ("Vollscan starten? Liest jede Datei aller lokalen Laufwerke (nur lesen, "
                 "niedrige Priorität, fortsetzbar). Der erste Lauf kann eine Stunde oder länger dauern.")
        if letzter.get("phase") == "abgebrochen":
            frage = "Der letzte Vollscan wurde angehalten – dort weitermachen?"
        if await ask_confirm(frage, [("ja", "Ja, starten"), ("nein", "Nein")]) != "ja":
            return
        ui.info(VS.starten(paths.INSTALL))
    z = VS.stand_lesen()
    if not z:
        ui.info("Noch kein Vollscan. /wache fullscan startet einen.")
        return
    titel = "🛡 Vollscan" + (" läuft" if VS.laeuft() else "")
    ui.console.print(ui.progress_panel(titel, VS.anzeige(z)))
    if z.get("fehlertext"):
        ui.warn(f"Fehler: {z['fehlertext']}")
    if VS.laeuft():
        ui.info("/wache fullscan status = Stand · /wache fullscan stop = anhalten (später weiter)")
        if _VOLLSCAN["task"] is None or _VOLLSCAN["task"].done():
            _VOLLSCAN["task"] = asyncio.create_task(_vollscan_melden())


_VOLLSCAN: dict = {"task": None}


def _vollscan_badge(text: str) -> None:
    tui = ui.TUI
    if tui is None:
        return
    try:
        tui.app.call_from_thread(tui.set_vollscan, text)
    except Exception:
        try:
            tui.set_vollscan(text)
        except Exception:
            pass


async def _vollscan_melden() -> None:
    """Fortschritt als Balken in der Statuszeile; Meldung im Chat, wenn eine
    Stufe fertig ist."""
    from wache import vollscan as VS
    phase = VS.stand_lesen().get("phase")
    try:
        while True:
            await asyncio.sleep(2)
            z = VS.stand_lesen()
            _vollscan_badge(VS.kurzanzeige(z))
            if z.get("phase") != phase:
                phase = z.get("phase")
                if phase == "code":
                    ui.info(f"🛡 Vollscan: Stufe 1 fertig ({z.get('dateien', 0):,} Dateien) – "
                            "jetzt Fingerabdrücke, Fortschritt unten in der Statuszeile.".replace(",", "."))
                elif phase == "fertig":
                    ui.success(f"🛡 Vollscan fertig – Bericht: {z.get('bericht', '')}")
                elif phase in ("abgebrochen", "fehler"):
                    ui.info(f"🛡 Vollscan {phase}.")
            if not VS.laeuft():
                return
    finally:
        _vollscan_badge("")


def vollscan_beobachten() -> None:
    """Beim Start von NemiCLI: läuft ein Vollscan, den Balken wieder anzeigen."""
    try:
        from wache import vollscan as VS
        if VS.laeuft() and (_VOLLSCAN["task"] is None or _VOLLSCAN["task"].done()):
            _VOLLSCAN["task"] = asyncio.create_task(_vollscan_melden())
    except Exception:
        pass


KUGEL_MODELL_HALTEN_S = 300      # lokales Modell nach dem letzten Kugel-Gespräch so lange halten


def wache_lauf() -> int:
    global ask_confirm, review_action_confirm
    from wache import dienst as _dienst, wecker as _wecker, zugang as _zugang
    import zeitplan as _ZP
    if _zugang.laeuft():
        print("Die Wache läuft schon.")
        return 0
    ask_confirm = _nie_freigeben
    review_action_confirm = _nie_pruefen
    modes.set_mode("lesen")
    modes.wache_dienst(True)
    stats.start_session(None)

    # Weckruf und Kugel-Gespräch teilen sich converse() und die Mitschrift –
    # deshalb läuft immer nur eines von beiden (Lock auf der Dienst-Schleife).
    gespraech = {"lock": None, "kugel_backend": None, "freigabe": None, "freigabe_nr": 0}

    def _lokales_modell_freigeben() -> None:
        """Ein lokales Sprachmodell bleibt nicht im Wache-Prozess: sein VRAM fehlt
        sonst dem Hauptprogramm. Beim nächsten Weckruf wird es neu geladen."""
        mod = sys.modules.get("gguflokal")
        if mod is not None and mod.geladen():
            mod.entladen()
            print(f"[{time.strftime('%H:%M:%S')}] Lokales Sprachmodell entladen.")

    def _freigabe_absagen() -> None:
        """Neues Gespräch: ein geplantes Entladen gilt nicht mehr."""
        gespraech["freigabe_nr"] += 1
        alt, gespraech["freigabe"] = gespraech["freigabe"], None
        if alt is not None:
            alt.cancel()

    def _freigabe_planen(sekunden: float) -> None:
        """Nach `sekunden` Ruhe entladen – unter der Gesprächssperre, damit nie ein
        laufendes Gespräch das Modell verliert; ein inzwischen neues Gespräch sagt es ab."""
        import threading
        _freigabe_absagen()
        nr, loop = gespraech["freigabe_nr"], asyncio.get_running_loop()

        async def _entladen():
            async with _lock():
                if gespraech["freigabe_nr"] == nr:
                    await asyncio.to_thread(_lokales_modell_freigeben)

        zeit = threading.Timer(sekunden, lambda: asyncio.run_coroutine_threadsafe(_entladen(), loop))
        zeit.daemon = True
        gespraech["freigabe"] = zeit
        zeit.start()

    def _lock() -> asyncio.Lock:
        if gespraech["lock"] is None:
            gespraech["lock"] = asyncio.Lock()
        return gespraech["lock"]

    async def _backend_neu():
        cfg = config.load()
        ref = _migrate_ref(cfg.get("model"))
        if not ref:
            raise RuntimeError("Kein Modell eingestellt (/model).")
        return ref, await make_backend(ref, None, cfg.get("strength", M.DEFAULT_STRENGTH))

    async def wecken(alarme) -> str:
        async with _lock():
            _freigabe_absagen()
            try:
                return await _wecken(alarme)
            finally:
                await asyncio.to_thread(_lokales_modell_freigeben)

    async def _wecken(alarme) -> str:
        try:
            ref, backend = await _backend_neu()
        except Exception as e:
            return f"❌ Die Wache kann niemanden wecken: {e}"
        name = PS.active().name
        mitschrift.leeren()
        mitschrift.setze_quelle(lambda: {"chat": "Wache", "model": ref, "persona": name,
                                         "modus": modes.label(), "ordner": str(Path.cwd())})
        text = _wecker.auftrag_text(alarme, _zugang.speicher(), name)
        fehler = ""
        try:
            await asyncio.wait_for(converse(backend, text), timeout=15 * 60)
        except asyncio.TimeoutError:
            fehler = "Abgebrochen: der Weckruf lief länger als 15 Minuten."
        except Exception as e:
            fehler = f"Abgebrochen mit Fehler: {e}"
        antwort = mitschrift.letzte_antwort() or ""
        if not antwort:
            antwort = "❌ Kein Bericht – " + (fehler or "die Persönlichkeit hat nichts geantwortet.")
        elif fehler:
            antwort = f"⚠️ {fehler}\n\n{antwort}"
        pfad = _ZP.bericht_pfad("Wache")
        pfad.write_text(
            f"<!-- NemiCLI-Bericht: Wache · {time.strftime('%d.%m.%Y %H:%M')} · {ref} · {name} -->\n\n"
            + antwort.strip() + "\n\n---\n\n## Alarme\n\n"
            + "\n".join(f"- {a.kurz()}" for a in alarme)
            + "\n\n## Ablauf\n\n" + mitschrift.als_markdown(), encoding="utf-8")
        protokoll.schreibe("wache_weckruf", f"{name} geweckt: {len(alarme)} Alarm(e)", "success",
                           veraendernd=False, wer="Wache", ergebnis=antwort)
        print(f"[{time.strftime('%H:%M:%S')}] Weckruf: {len(alarme)} Alarme → {pfad.name}")
        return antwort

    async def chat(text: str, bilder: list) -> tuple[str, list[str]]:
        """Gespräch über die Schwebekugel: EIN Verlauf, solange der Dienst läuft.
        Bilder (Screenshots) gehen als Data-URIs mit; neue Bilder aus Bilder/
        kommen als Pfade zurück, damit die Kugel sie zeigt."""
        async with _lock():
            _freigabe_absagen()
            try:
                return await _kugel_gespraech(text, bilder)
            finally:
                _freigabe_planen(KUGEL_MODELL_HALTEN_S)       # auch nach Fehlern: nicht im VRAM hängen

    async def _kugel_gespraech(text: str, bilder: list) -> tuple[str, list[str]]:
        if gespraech["kugel_backend"] is None:
            try:
                ref, backend = await _backend_neu()
            except Exception as e:
                return f"Ich kann gerade nicht antworten: {e}", []
            gespraech["kugel_backend"] = (ref, backend)
        ref, backend = gespraech["kugel_backend"]
        name = PS.active().name
        mitschrift.leeren()
        mitschrift.setze_quelle(lambda: {"chat": "Kugel", "model": ref, "persona": name,
                                         "modus": modes.label(), "ordner": str(Path.cwd())})
        uris = []
        for b in bilder or []:
            try:
                uris.append(vision.to_data_uri_small(Path(b)))
            except Exception:
                pass
        start = time.time()
        try:
            await asyncio.wait_for(converse(backend, text, images=uris or None), timeout=10 * 60)
        except Exception as e:
            return f"(abgebrochen: {e})", []
        antwort = mitschrift.letzte_antwort() or ""
        neue = []
        import profilordner
        for ordner in profilordner.bilder_ordner_alle():
            try:
                neue += [str(p) for p in ordner.glob("*.png") if p.stat().st_mtime >= start - 1]
            except OSError:
                pass
        protokoll.schreibe("kugel_chat", f"Kugel: {text[:120]}", "success", veraendernd=False,
                           wer=name, ergebnis=antwort)
        return antwort, sorted(neue)

    d = _dienst.Dienst(wecken, paths.INSTALL, chat=chat, name=PS.active().name,
                       nutzer=PS.nutzername() or "du", mit_tray="--ohne-tray" not in sys.argv,
                       mit_kugel="--ohne-kugel" not in sys.argv)
    print(f"[{time.strftime('%H:%M:%S')}] Wache läuft (PID {os.getpid()})")
    return d.laufen()


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
    sicherheit.sitzung_zuruecksetzen()
    try:                                     # Papierkorb: Sicherungen älter als 30 Tage weg
        weg = snapshot.aufraeumen()
        if weg:
            ui.info(f"🗑 Papierkorb: {weg} alte Sicherung(en) entfernt.")
    except Exception:
        pass

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
    # Berichte aus dem Zeitplan, die seit dem letzten Start dazukamen: erste Zeile zeigen
    try:
        import zeitplan as _ZP
        for _p in _ZP.neue_berichte()[-3:]:
            ui.info(f"📋 Bericht „{_p.stem}“: {_ZP.bericht_kopfzeile(_p) or '(leer)'}  →  {_p}")
    except Exception:
        pass
    # Systemwache: läuft sie? Wenn gewünscht und nicht da → im Hintergrund starten.
    try:
        from wache import zugang as _WZ, justierung as _WJ
        _we = _WJ.einstellungen()
        _alt = _WZ.laeuft()
        if _we.get("aktiv", True) and _WZ.veraltet(_alt):
            ui.info("🛡 Wache läuft noch mit altem Programmstand – starte sie neu …")
            _WZ.stoppen()
        if _WZ.autostart_auffrischen(paths.INSTALL):
            ui.info("🛡 Windows-Autostart der Wache zeigt jetzt auf die fensterlose NemiCLI.exe.")
        if not _WZ.laeuft() and _we.get("aktiv", True):
            ui.info(_WZ.starten(paths.INSTALL))
        _wi = _WZ.laeuft()
        _st = _WZ.speicher().alarm_statistik()
        if _wi:
            _lage = f"{_st.get('offen', 0)} offen"
            if _st.get("echt"):
                _lage = f"⚠ {_st['echt']} ECHT · " + _lage
            ui.info(f"🛡 Wache läuft (PID {_wi['pid']}) · Alarme: {_lage} · /wache zeigt mehr")
        elif not _we.get("aktiv", True):
            ui.info("🛡 Wache ist aus (/wache start · /wache an schaltet den Autostart wieder ein)")
    except Exception:
        pass
    vollscan_beobachten()
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
    global AKTIVER_CTX
    AKTIVER_CTX = ctx
    _sprueche_im_hintergrund(ctx)

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
        # loeschen verschiebt in den Papierkorb – die Probe muss dort nicht liegen bleiben.
        import shutil as _sh
        for e in snapshot.letzte(3):
            if e.get("werkzeug") == "loeschen" and Path(e["original"]) == box:
                _sh.rmtree(e["kopie"], ignore_errors=True)
                print(f"  ok   Papierkorb         Probe gesichert und wieder entfernt ({snapshot.ORDNER.name}/)")
                break
        else:
            fehler += 1
            print("  FEHLT Papierkorb: loeschen hat keine Sicherung angelegt")
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
    # Eigenes Terminal-Fenster: mit --fenster, oder NemiCLI.exe (Fenster-App) ohne Hintergrund-Auftrag.
    # Das Fenster startet NemiCLI selbst in einer Pseudokonsole – ohne Windows Terminal.
    _HINTERGRUND = ("--wache", "--vollscan", "--auftrag", "--sag", "--selftest", "--bild",
                    "--systemcheck")
    if "--gui" in sys.argv:              # GUI-Fenster (verbindet sich mit einer laufenden Sitzung)
        import gui_fenster
        raise SystemExit(gui_fenster.starten())
    if "--fenster" in sys.argv or (getattr(sys, "frozen", False) and sys.stdout is None
                                   and not any(a in sys.argv for a in _HINTERGRUND)):
        import terminal_fenster as _TF
        _rest = [a for a in sys.argv[1:] if a != "--fenster"]
        if getattr(sys, "frozen", False):
            _kind = [str(paths.exe_mit_fenster()), *_rest]
        else:
            _kind = [sys.executable, str(Path(__file__).resolve()), *_rest]
        raise SystemExit(_TF.starten(_kind, ordner=os.getcwd()))
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--bild" in sys.argv:             # Bild malen ohne Oberfläche (zum Testen)
        import imagegen
        _i = sys.argv.index("--bild")
        _prompt = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else "a red fox in a forest"
        _g = imagegen.missing_reason_aktiv()
        if _g:
            print(_g)
            raise SystemExit(1)
        _opts = imagegen.parse_opts(_prompt)      # --steps/--size/--seed ... erlaubt
        _text = _opts.pop("prompt", _prompt)
        print(f"male: {_text}  ({imagegen.device_info()})")
        _erlaubt = ("model", "neg", "steps", "cfg", "size", "seed", "sampler", "karras")
        _res, _ = imagegen.paint(_text, on_status=lambda m: print("  ", m),
                                 **{k: v for k, v in _opts.items() if k in _erlaubt})
        print("fertig:", _res)
        raise SystemExit(0)
    if "--vollscan" in sys.argv:         # Vollscan aller Laufwerke (eigener Prozess, ohne Fenster)
        from wache import vollscan as _VS
        raise SystemExit(_VS.hauptprogramm())
    if "--wache" in sys.argv:            # Systemwache als Hintergrund-Prozess (Tray)
        try:
            from wache import ORDNER as _WO
            _WO.mkdir(parents=True, exist_ok=True)
            if sys.stdout is None or "--ohne-tray" not in sys.argv:
                _lp = _WO / "dienst.log"
                if _lp.exists() and _lp.stat().st_size > 5_000_000:
                    _lp.replace(_lp.with_suffix(".log.1"))
                _log = open(_lp, "a", encoding="utf-8", errors="replace", buffering=1)

                class _OhneFarben:
                    """Farbcodes raus, bevor es in die Datei geht – sonst ist das
                    Log voller Steuerzeichen, die kein Editor lesbar zeigt."""
                    _re = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

                    def write(self, s):
                        return _log.write(self._re.sub("", s))

                    def flush(self):
                        _log.flush()

                    def __getattr__(self, name):
                        return getattr(_log, name)

                sys.stdout = sys.stderr = _OhneFarben()
                # Die Antworten der Persönlichkeit (Kästen, Nachgedacht-Zeilen …)
                # stehen komplett in Berichte/Wache_*.md – im Dienst-Log wären sie
                # nur Ballast. Hier bleiben nur die Zeilen des Dienstes selbst.
                ui.console.quiet = True
            print(f"\n===== {time.strftime('%d.%m.%Y %H:%M:%S')} Wache startet =====")
        except Exception:
            pass
        raise SystemExit(wache_lauf())
    if "--auftrag" in sys.argv:          # Zeitplan: ohne Fenster, Antwort wird Bericht
        _i = sys.argv.index("--auftrag")
        _name = " ".join(sys.argv[_i + 1:]).strip()
        if not _name:
            print("--auftrag braucht den Namen des Auftrags (Zeitplan/<name>.json).")
            raise SystemExit(2)
        # Unter pythonw.exe gibt es keine Konsole – alles, was NemiCLI sonst ins
        # Terminal malt, landet in einer Log-Datei neben den Berichten.
        try:
            import zeitplan as _ZP
            _ZP.BERICHTE.mkdir(parents=True, exist_ok=True)
            _log = open(_ZP.BERICHTE / f"{_ZP.sauberer_name(_name) or 'auftrag'}.log",
                        "a", encoding="utf-8", errors="replace")
            sys.stdout = sys.stderr = _log
            print(f"\n===== {time.strftime('%d.%m.%Y %H:%M:%S')} Auftrag {_name!r} =====")
        except Exception:
            pass
        raise SystemExit(asyncio.run(auftrag_lauf(_name)))
    if "--systemcheck" in sys.argv:      # Bericht ohne Oberfläche (auch zum Testen)
        import syscheck
        _rep = syscheck.report(True)
        ui.system_panel(_rep, syscheck.todo(_rep))
        if "--debug" in sys.argv:        # Rohdaten zum Fehlersuchen
            import json
            print(json.dumps(_rep["torch"], indent=2, ensure_ascii=False))
        raise SystemExit(0)
    asyncio.run(main())
