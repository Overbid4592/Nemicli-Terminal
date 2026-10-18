"""
main.py - NemiCLI: ein schöner Chat im Terminal.  Cloud + Lokal.

Normaler Text  -> Gespräch mit dem aktiven Modell.
/befehle       -> Theme, Modell (Cloud/Lokal), Hilfe, beenden ...

Cloud  = Anthropic (braucht ANTHROPIC_API_KEY in .env)
Lokal  = Ollama auf diesem PC (kein Key nötig)
"""

# main.py ist auf vier Dateien verteilt (siehe _teil_laden): Imports nutzen die Teile mit, Namen kommen von dort.
# ruff: noqa: F401, F821
import asyncio
import itertools
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

# Von /update zurückgestellte Pakete (waren in Gebrauch) jetzt einspielen –
# noch ist nichts aus dem venv geladen.
try:
    import venvpflege
    venvpflege.ausstehende_einspielen()
except Exception:
    pass

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

import ki_terminal  # noqa: E402,F401  – nimmt die Leitung zum Terminal-Fenster aus der Umgebung, bevor Programme starten

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Group

import ui
import actions
import vision
import config
import chatstore
import learn
import memory
import indexdb
import persona
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
import lernschleife
import anhang
import protokoll
import sicherheit
import snapshot
import coding
import notstopp
import plan
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
    if prov == "funke":                              # Funke Groß: nur Text
        return False
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
# fragt dann acht Dinge ab – mit 12 war sie bei Schritt 2 am Ende (Chat 137). Sobald
# eine ändernde Aktion dabei war, gilt wieder die kurze Bremse.
MAX_LESE_STEPS = 30
# Im Coding-Assistenten zählt die Bremse pro Todo-Punkt: jeder Fortschritt (Punkt in
# Arbeit, Punkt geprüft) setzt sie zurück. Darüber liegt eine feste Gesamtgrenze.
CODING_MAX_STEPS = 80
# Mit offener Todo-Liste (lange Serien, z. B. 20 Bilder) gilt diese Gesamtgrenze; die Bremse ohne
# Fortschritt (MAX_STEPS ohne neuen Haken) bleibt, damit nichts endlos läuft.
SERIE_MAX_STEPS = 120
_FRAGE_HALT_NOTE = ("\n\n[System] 🟠 Eine Frage ist offen. Stell sie dem Nutzer jetzt, kurz und "
                    "klar – ohne Aktion. Dann wartest du auf seine Antwort.")
# Lesende Werkzeuge, die mit identischen Feldern nichts Neues bringen: eine
# Wiederholung in derselben Runde wird nicht ausgeführt, sondern nur angemerkt.
# befehl/bild_malen/Helfer bewusst nicht – ein zweites `git status` kann Sinn haben.
_DEDUPE_TOOLS = {"datei_lesen", "ordner_auflisten", "dateien_suchen", "inhalt_suchen",
                 "ordner_erkennen", "web_lesen", "web_wiki", "web_suche", "fach_suche", "ml_status",
                 "wache_status", "wache_alarme", "wache_ereignisse", "wache_inventar",
                 "gedaechtnis_suchen", "gedaechtnis_lesen"}
_STEP_LIMIT_NOTE = (
    "\n\n[System] ⏸ Du hast in dieser Runde die Obergrenze an Aktionen erreicht "
    f"({MAX_STEPS} mit Änderungen, {MAX_LESE_STEPS} nur lesend). Führe jetzt KEINE weitere Aktion aus. Fasse zusammen, was du "
    "bisher herausgefunden hast, und sag klar, was noch offen ist. Der Nutzer kann mit "
    "„weiter“ die nächste Runde starten."
)
_TODO_OFFEN_NOTE = ("\n\n[System] Deine Todo-Liste aus der letzten Runde ist noch offen:\n{liste}\n"
                    "Arbeite sie weiter ab. Passt sie nicht mehr zur neuen Nachricht: neue Liste mit "
                    "plan \"punkte\" oder Punkte mit \"streichen\" + \"grund\" weglassen.")
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
MUSIK_BRUECKE = None            # Verbindung zum Musik-Fenster (/musik)
# Im GUI-Modus „Chat“ erlaubt: nichts am PC ändern – malen, Bilder ansehen, Gedächtnis, Skills (Prüffenster).
CHAT_WERKZEUGE = frozenset({"plan", "bild_malen", "bild_serie", "portraet_bewegen", "charakter_zeigen",
                            "charakter_aendern",
                            "bild_ansehen", "bild_fragen",
                            "gedaechtnis_suchen", "gedaechtnis_lesen", "merken", "tagebuch", "lieblingsbild",
                            "profilbild",
                            "vorschau_schliessen",
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


def _abbruch_taste() -> bool:
    """ESC (oder „STOP“ während der Arbeit) seit der letzten Abfrage?"""
    return ui.TUI.consume_abort() if ui.TUI is not None else _esc_pressed()


def _stopp_taste() -> bool:
    """Wie _abbruch_taste, löst aber gleich den Not-Stopp aus."""
    if _abbruch_taste():
        notstopp.ausloesen()
        return True
    return False


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
    fürs Modell – oder, wenn das Modell nicht sieht, vom Bildbeschreiber (`Vision/`)
    beschreiben lassen (die Beschreibung kommt dann in den Ergebnistext).
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


_KOPF_IN_AUSGABE = re.compile(r"(?m)^Ergebnis von '")


def _ergebnisse_verbinden(ergebnisse: list[str]) -> str:
    """Ergebnisse zu einer Nachricht. Nur die erste Zeile eines Ergebnisses darf mit „Ergebnis von '“
    beginnen: solche Zeilen in Ausgaben (Webseiten, Dateien, Befehle) werden eingerückt, damit kein
    Inhalt eine Ergebnis-Grenze vortäuschen kann (lokale Modelle bekommen jede Grenze als Marke)."""
    return "\n\n".join(b[:1] + _KOPF_IN_AUSGABE.sub(" Ergebnis von '", b[1:]) for b in ergebnisse)


def _result_feedback(tool, result) -> str:
    """Status stammt vom Werkzeug, nie aus dem Inhalt seiner Ausgabe."""
    status = "erfolgreich" if result.ok is True else (
        "fehlgeschlagen" if result.ok is False else "Status ungeprüft")
    code = f", Exitcode {result.returncode}" if result.returncode is not None else ""
    return f"Ergebnis von '{tool}' ({status}{code}):\n{result.text}"


async def _menue_fuer_ki(act: dict) -> "actions.ActionResult":
    """menue_oeffnen: Auswahlmenü eines Befehls öffnen; der Nutzer wählt selbst.
    Gibt zurück, was gewählt wurde – die KI sieht so, ob und was sich geändert hat."""
    import commands as C
    befehl, fehler = C.menue_pruefen(act.get("befehl"))
    ctx = AKTIVER_CTX
    if fehler:
        return actions.ActionResult(fehler, ok=False)
    if ctx is None:
        return actions.ActionResult(f"Kein Menü verfügbar – nenne dem Nutzer den Befehl {befehl}.", ok=False)
    cmd, _, zusatz = befehl.partition(" ")
    ui.info(f"🧭 Öffne {befehl} …")
    if not zusatz:
        gewaehlt = await _untermenue(ctx, cmd)
        if gewaehlt is None:
            return actions.ActionResult(f"Menü {cmd}: der Nutzer hat ohne Auswahl geschlossen.", ok=True)
        zusatz = gewaehlt
    vorher = C.auswahl(cmd)
    await handle_line(ctx, f"{cmd} {zusatz}".strip())
    aktiv = [z for z, t, _ in C.auswahl(cmd) if t.lstrip().startswith("✓")]
    stand = f" Jetzt aktiv: {', '.join(aktiv)}." if aktiv and C.auswahl(cmd) != vorher else ""
    return actions.ActionResult(
        f"Menü {cmd} geöffnet, der Nutzer hat gewählt: {(cmd + ' ' + zusatz).strip()}.{stand}", ok=True)


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
    import hintergrund as _HG
    if (belegt := _HG.sperre(tool)):              # Grafikkarte belegt: nicht mal fragen
        res = actions.ActionResult(belegt, ok=False)
        zeige_aktion(desc, False, act)
        protokoll.schreibe(tool, desc, "blocked", veraendernd=actions.needs_confirm(act), wer=wer,
                           stufe=stufe, ergebnis=belegt)
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
    confirm = (urteil == "ask"
               and (tool not in AUTO_ALLOW or sicherheit.steuerdatei_grund(act) or actions.fremd_grund(act))
               and not (frei and id(act) in frei))
    if confirm and sicherheit.arbeitsbereich(act):
        # Der Stift der Persönlichkeit: im eigenen Daten-Ordner schreibt sie
        # ohne Rückfrage (Wunschbrief 19.09.2026). Protokoll + Papierkorb bleiben.
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
        titel = ("Skill prüfen" if tool in actions.SKILL_AENDERN
                 else "Einstellung prüfen" if tool == "einstellung_aendern"
                 else "Theme prüfen" if tool == "theme_erstellen" else "Dateiänderung prüfen")
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
                and a.get("tool") not in AUTO_ALLOW
                and not sicherheit.steuerdatei_grund(a))      # Wächter fragt einzeln, mit Grund
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


# --- Recherche ----------------------------------------------------------------
def _recherche_plan_text(teilfragen: list[str]) -> str:
    return "🔬 Recherche – Teilfragen (Esc stoppt):\n" + "\n".join(
        f"    {i}. {t}" for i, t in enumerate(teilfragen, 1))


async def _recherche_lauf(act: dict, backend, records: list, melde, plan_zeigen,
                          abbruch=lambda: False) -> str:
    """Führt das Werkzeug `recherche` aus und liefert die Rückmeldung fürs Modell."""
    import recherche
    import webfetch
    frage = " ".join(str(act.get("frage") or "").split())
    if not frage:
        return "Fehler: Feld `frage` fehlt für 'recherche'."
    if modes.decide(act, False) == "block":
        records.append({"tool": "recherche", "status": "not_run"})
        return "Recherche ist in diesem Modus gesperrt (sie speichert einen Bericht)."
    if not hasattr(backend, "ask_messages"):
        return "Mit diesem Modell geht die Recherche nicht (keine Einzelantworten)."
    if not webfetch.search_available():
        return ("Die Recherche braucht die Web-Suche (Ollama-Schlüssel). "
                "Sag dem Nutzer, dass er sie mit /web key einrichtet.")
    lokal = type(backend).__name__ == "GgufChat" or getattr(backend, "provider_id", "") == "ollama"

    async def ask(prompt: str, system: str) -> str:
        return await backend.ask_messages(system, [{"role": "user", "content": prompt}])

    actions.fremdinhalt()                     # Bericht stammt aus Fremdquellen: Netz-Taint wie web_lesen
    record = {"tool": "recherche", "status": "running"}
    records.append(record)
    umgebung = recherche.Umgebung(ask=ask, plan_zeigen=plan_zeigen, melde=melde,
                                  parallel=not lokal, lokal=lokal, abbruch=abbruch)
    try:
        stand = await recherche.lauf(frage, umgebung, Path(paths.DATEN) / "Berichte", act.get("tiefe") or 1)
    except asyncio.CancelledError:
        record["status"] = "failed"
        raise
    except Exception as exc:
        record["status"] = "failed"
        return f"Recherche fehlgeschlagen: {exc}"
    record["status"] = "unverified" if stand.pfad else "not_run"
    protokoll.schreibe("recherche", f"Recherche: {frage}", record["status"], veraendernd=False,
                       wer=PS.active().name, pfad=stand.pfad, ergebnis=stand.abgebrochen or None)
    return recherche.ergebnis_fuers_modell(stand)


def _bild_vorschau(text_mit_pfad: str, title: str | None = None) -> None:
    """Zeigt das zuletzt genannte Bild aus einem Ergebnistext als Pixel-Block.
    Fehler sind hier egal – die Vorschau ist Bonus, nie Pflicht."""
    try:
        pfade = vision.find_images(text_mit_pfad or "")
        if pfade:
            ui.show_image_preview(pfade[-1], title)
    except Exception:
        pass


_LETZTE_ANTWORT = [""]           # für /voice text


def _voice_an() -> bool:
    try:
        import stimme
        return stimme.modus() == "an"
    except Exception:
        return False


def _kurzfassung(text: str) -> str:
    """Sprachmodus: nur der Anfang der Antwort auf dem Bildschirm – den Rest hört man."""
    erste = re.split(r"(?<=[.!?…])\s+|\n", text.strip(), maxsplit=1)[0].strip()
    if len(erste) > 120:
        erste = erste[:117].rsplit(" ", 1)[0] + " …"
    return f"{erste}\n\n_🔊 … den Rest hörst du · /voice text zeigt alles_"


def _vorlesen_auto(text: str | None) -> None:
    """Modus „an“ (/voice an): die fertige Antwort vorlesen."""
    try:
        import stimme
        if text and stimme.modus() == "an":
            stimme.sagen(text)
    except Exception:
        pass


def _stimme_stopp() -> None:
    try:
        import stimme
        if stimme.spricht():
            stimme.stopp()
    except Exception:
        pass


async def converse(backend, user_text: str, images: list[str] | None = None) -> list[dict]:
    """Eine Nutzer-Runde; gibt die Werkzeugbilanz zurück (tool, status)."""
    records = []
    persona.runde_beginnt(user_text)  # Gedächtnissuche dieser Runde: mit der Nachricht, nicht mit Werkzeug-Ergebnissen
    indexdb.unterbrechen()           # Bibliothekar gibt Encoder und CPU für die Antwort frei
    _stimme_stopp()                  # neue Frage: laufendes Vorlesen endet
    # neue Nutzer-Runde: Netz-Sperre nur, solange Netz-Inhalt noch im Verlauf steht
    actions.reset_taint(actions.fremd_im_verlauf(getattr(backend, "messages", None)))
    _abbruch_taste()                 # ein liegengebliebenes ESC gilt nicht für die neue Runde
    stop = notstopp.ist_stop(user_text)
    notstopp.runde_beginnt(stop)
    if stop:                         # „STOP“ als Nachricht: nichts fortsetzen, nur bestätigen
        plan.verwerfen()
    offen = plan.runde_neu()         # offene Todo-Liste läuft weiter, sonst beginnt eine neue
    mitschrift.nutzer(user_text)     # F12 soll das ganze Gespräch sichern können
    if offen:
        _plan_zeigen()
        user_text += _TODO_OFFEN_NOTE.format(liste=plan.text_fuer_modell())
    user_text += ("\n\n" + notstopp.STOP_RUNDE) if stop else notstopp.hinweis_abholen()
    user_text += lernschleife.hinweise_abholen(PS.active().key)     # was im Hintergrund geschah
    user_text += _neuigkeiten(PS.active().key)                       # nach einem Update: was neu ist
    if coding.aktiv() and coding.ist_ende_ansage(user_text):
        _coding_beenden()
    import debugbericht as _DBG
    _DBG.ereignis(f"Runde: {len(user_text)} Zeichen, {len(images or [])} Bild(er)")
    try:
        await _converse(backend, user_text, images, records)
        if notstopp.ausgeloest():
            await _nach_notstopp()
    finally:
        _close_records(records)
        _DBG.ereignis("Runde fertig")
        if plan.neu_seit_anzeige():          # Endstand der Todo-Liste
            _plan_zeigen()
        ui.console.print(ui.execution_receipt(records))
    return records


async def _nach_notstopp() -> None:
    """Nach ESC/STOP: zeigen, was schon geändert ist; gesicherte Änderungen nur auf Wunsch zurückholen."""
    ui.warn(notstopp.bericht())
    sicher = notstopp.sicherungen_seit(notstopp.runde_start())
    if not sicher:
        return
    liste = "\n".join(f"  • {e['werkzeug']}: {e['original']}" for e in sicher)
    wahl = await ask_confirm(f"Diese Änderungen dieser Runde liegen gesichert im Papierkorb:\n{liste}\n"
                             "Den Stand von vorher zurückholen?",
                             [("nein", "Nein, so lassen"), ("ja", "Ja, zurückholen")])
    if wahl != "ja":
        ui.info("Bleibt so. Später geht es einzeln mit /undo.")
        return
    for e in sicher:                                     # neueste zuerst
        try:
            ziel = snapshot.wiederherstellen(e)
            ui.success(f"↩ Zurückgeholt: {ziel}")
            protokoll.schreibe("undo", f"Not-Stopp – zurückgeholt: {ziel}", "success", veraendernd=True,
                               wer="Nutzer")
        except Exception as exc:
            ui.error(f"Zurückholen fehlgeschlagen ({e['original']}): {exc}")


async def _warten_mit_stopp(task: "asyncio.Task") -> "actions.ActionResult":
    """Werkzeug zu Ende laufen lassen und dabei auf ESC/STOP lauschen (Not-Stopp merken)."""
    while not task.done():
        if _abbruch_taste():
            notstopp.ausloesen()
        await asyncio.sleep(0.1)
    return await task


def _ankunft_melden(ankunft: dict, denk_zeichen: int, gesamt: float) -> None:
    """Wann kamen Denken und Antwort an (Sekunden ab Absenden) – zeigt, ob Wartezeit vor dem
    Anbieter liegt oder das Denken gesammelt statt fortlaufend eintrifft."""
    import debugbericht as _DBG
    teile = []
    if ankunft["denken"] is not None:
        teile.append(f"Denken ab {ankunft['denken']:.1f} s ({ankunft['stuecke']} Stücke, {denk_zeichen} Zeichen)")
    if ankunft["antwort"] is not None:
        teile.append(f"Antwort ab {ankunft['antwort']:.1f} s")
    _DBG.ereignis("Schritt: " + " · ".join(teile + [f"fertig nach {gesamt:.1f} s"]), speicher=False)


async def _converse(backend, user_text, images, records) -> None:
    """Ein Gespräch mit Aktions-Schleife: antworten → ggf. handeln → weiter.
    images (base64-Data-URIs) werden nur beim ERSTEN Schritt mitgeschickt."""
    next_input = user_text
    first = True
    seen: set[str] = set()                   # identische Lese-Aktionen nur einmal
    nur_gelesen = True                       # bisher nur Lese-Werkzeuge? -> längere Leine
    basis = 0                                # ab diesem Schritt zählt die Bremse
    marke = (coding.marke(), plan.marke())   # Todo-Fortschritt (Coding-Assistent bzw. Todo-Liste)
    frage_halt = False
    gesamt = CODING_MAX_STEPS if coding.aktiv() else MAX_LESE_STEPS
    for schritt in itertools.count():
        if notstopp.ausgeloest():            # Not-Stopp: kein weiterer Schritt
            return
        if plan.aktuell() is not None:
            gesamt = max(gesamt, SERIE_MAX_STEPS)
        if schritt > gesamt:
            break
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
        ankunft = {"denken": None, "antwort": None, "stuecke": 0}   # ab Absenden, für den Debugbericht

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
                    if _abbruch_taste():
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
                            ankunft["denken"] = think["start"] - t0
                        ankunft["stuecke"] += 1
                        thinking_txt += ev["text"]
                    elif et == "note":
                        note = ev["text"]        # dezente Info, nicht Teil der Antwort
                    else:
                        # erstes echtes Antwort-Zeichen -> Denkzeit steht fest
                        if (ev["text"].strip() and think["start"] is not None
                                and think["secs"] is None):
                            think["secs"] = time.monotonic() - think["start"]
                        if ankunft["antwort"] is None and ev["text"].strip():
                            ankunft["antwort"] = time.monotonic() - t0
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
        _ankunft_melden(ankunft, len(thinking_txt), elapsed)
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
            notstopp.ausloesen()
            return

        acts, cleaned = actions.parse_actions(answer)
        # die vollständige Antwort einmal sauber ausgeben (kein Stapeln mehr)
        if cleaned:
            display = cleaned
        elif acts:
            display = "⚙ (Aktion vorbereitet)"        # nur wenn wirklich eine Aktion folgt
        else:
            display = "_(leere Antwort – frag ruhig nochmal)_"
        hoeren = bool(cleaned) and not acts and _voice_an()
        if cleaned and not acts:
            _LETZTE_ANTWORT[0] = cleaned
        ui.console.print(_render(thinking_txt, _kurzfassung(cleaned) if hoeren else display,
                                 think["secs"], note, done=True))
        # Denktext gibt es NUR hier - in backend.messages steht er nicht.
        mitschrift.assistent(display, thinking_txt, think["secs"])

        # kleine Zusammenfassung (Tokens · Zeit · Tempo), wenn es echte Antwort gab
        if cleaned:
            tps = out_toks / elapsed if elapsed > 0.3 else None
            ui.console.print(ui.done_meter(elapsed, out_toks, tps, usage["input"]))

        if acts and notstopp.stop_runde():   # „STOP“-Runde: nur bestätigen, nichts ausführen
            for act in acts:
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
            ui.warn("⏹ " + notstopp.GESPERRT_TEXT)
            return

        # 2) Keine Aktion -> fertig
        if not acts:
            if (hinweis := actions.unparsed_action_note(answer, acts)):
                ui.warn(hinweis)             # Zaun da, JSON kaputt: nicht stumm schlucken
            _vorlesen_auto(cleaned)
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
        acts, gesperrt = plan.zurueckweisen(acts, coding.aktiv())
        if gesperrt:
            ui.warn("🟨 Mehr als ein Schritt – erst die Todo-Liste. Nicht ausgeführt: "
                    + ", ".join(actions.describe(a).splitlines()[0] for a in gesperrt))
            for act in gesperrt:
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
            ergebnisse.append(plan.sperr_text(gesperrt))
        frei, abgelehnt = await _buendel_freigabe(acts, ask_confirm)
        for act in acts:
            tool = act.get("tool")
            if notstopp.ausgeloest() or _abbruch_taste():   # Not-Stopp: Rest der Runde verwerfen
                notstopp.ausloesen()
                records.append({"tool": tool, "status": "not_run"})
                continue

            if tool in _DEDUPE_TOOLS:
                key = _action_key(act)
                if key in seen:
                    ui.info(f"↩ Wiederholung übersprungen: {actions.describe(act)}")
                    records.append({"tool": tool, "status": "not_run"})
                    ergebnisse.append(_result_feedback(
                        tool, actions.ActionResult(_DUPLICATE_NOTE, ok=None)))
                    continue
                seen.add(key)

            # --- Menü für den Nutzer öffnen: das Menü ist selbst die Rückfrage ---
            if tool == "menue_oeffnen":
                record = {"tool": tool, "status": "running"}
                records.append(record)
                res = await _menue_fuer_ki(act)
                _finish_record(record, res)
                if res.ok is False:
                    ui.console.print(ui.action_result(res.text, ok=False))
                mitschrift.aktion(tool, actions.describe(act), res.text, res.ok)
                ergebnisse.append(_result_feedback(tool, res))
                continue

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

            # --- Recherche: planen → suchen → lesen → prüfen → Bericht ---
            if tool == "recherche":
                ergebnisse.append(await _recherche_lauf(
                    act, backend, records, melde=ui.info,
                    plan_zeigen=lambda tf: ui.console.print(ui.action_request(_recherche_plan_text(tf), confirm=False)),
                    abbruch=_stopp_taste))
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
            beginn = time.monotonic()
            if tool not in modes.READ_TOOLS:
                nur_gelesen = False
            titel = {"bild_serie": "🎨 male deine Serie", "video_erstellen": "🎬 erzeuge dein Video",
                     "kugel_anziehen": "👗 male dein Kugel-Outfit",
                     "backup_erstellen": "💾 sichere", "lora_trainieren": "🧬 trainiere deine LoRA",
                     "coding_lora_trainieren": "🧠 trainiere das Coding-LoRA",
                     "musik_machen": "🎵 komponiere deinen Song", "musik_cover": "🎵 spiele das Cover ein"}.get(tool, "🎨 male dein Bild")
            if (getattr(actions, "ACTIONS", {}).get(tool, {}).get("hintergrund") and not act.get("warten")
                    and str(SESSION.get("model") or "").split(":")[0] not in ("", "gguf", "ollama")):
                # Lange Werkzeuge laufen bei Cloud-Modellen nebenbei: der Nutzer redet weiter, die
                # Fertigmeldung kommt als Hinweis (hintergrund.py). Ein lokales Modell braucht die
                # Grafikkarte selbst und bekäme ohnehin erst danach eine Antwort.
                import hintergrund as HG
                nr = HG.starten(tool, titel, lambda: actions.run(act), PS.active().key)
                res = actions.ActionResult(HG.start_text(nr), ok=True)
            elif tool in ("bild_malen", "bild_serie", "kugel_anziehen", "backup_erstellen", "video_erstellen"):
                # Bildmalen und Backup zeigen einen echten Ladebalken (Schritt i/n), genau
                # wie die Befehle. Die Aktion läuft im Thread und meldet ihren
                # Fortschritt über actions.bild_status() zurück.
                run_task = asyncio.create_task(actions.run(act))
                if (punkt := plan.aktuell()) is not None:
                    titel = f"{titel[:1]} Todo {punkt['nr']}: {punkt['text'][:60]}"
                with ui.live_view(ui.progress_panel(titel, "starte …")) as live:
                    while not run_task.done():
                        if _abbruch_taste():
                            notstopp.ausloesen()
                        stand = actions.bild_status() or "arbeite …"
                        if notstopp.ausgeloest():   # schon bezahlter Strom: zu Ende rechnen, danach nichts mehr
                            stand = f"⏹ Not-Stopp – rechnet noch zu Ende, danach nichts mehr · {stand}"
                        live.update(ui.progress_panel(titel, stand))
                        await asyncio.sleep(0.2)
                res = await run_task
            elif tool == "befehl" or coding.pruefbefehl(act):
                res = await _befehl_live(act)
            else:
                with ui.thinking("führe aus"):
                    res = await _warten_mit_stopp(asyncio.create_task(actions.run(act)))
            _finish_record(record, res)
            if tool not in modes.READ_TOOLS and tool not in ("plan", "todo", "coding_start") and res.ok:
                notstopp.geaendert_merken(actions.describe(act))
            uris, res = await _bilder_aus_ergebnis(res, SESSION.get("model"))
            if tool == "plan" and res.ok:
                _plan_zeigen()
            else:
                web = tool in ("web_lesen", "web_wiki", "web_suche", "fach_suche")
                anzeige = ui.anzeige_kurz(res.text) if web else res.text
                ui.console.print(ui.action_result(anzeige, ok=res.ok, sekunden=time.monotonic() - beginn))
                if tool in ("musik_machen", "musik_cover") and res.ok:
                    ui.musik_karte_aus(res.text)        # Mini-Player: abspielen, Cover, Ordner
                if plan.abhaken_nach(act, res.ok):          # Feld „abhaken“: sofort grün
                    _plan_zeigen()
            if tool in ("todo", "coding_start") and res.ok:
                _todo_zeigen()
                if str(act.get("aktion", "")).lower() == "frage":
                    frage_halt = True
            mitschrift.aktion(tool, actions.describe(act), res.text, res.ok)
            protokoll.schreibe(tool, actions.describe(act), record["status"],
                               veraendernd=actions.needs_confirm(act), wer=PS.active().name,
                               ergebnis=res.text, stufe=sicherheit.stufe(act))
            if tool in ("bild_malen", "bild_serie") and res.ok:
                _bild_vorschau(res.text)
            if uris:
                naechste_bilder.extend(uris)
                for pf in res.bilder[:2]:
                    _bild_vorschau(str(pf), title=f"angesehen: {Path(pf).name}")
            ergebnisse.append(_result_feedback(tool, res))

        # 4) Ergebnisse zurück ans Modell, nächster Schritt
        if notstopp.ausgeloest():
            return
        if any(a.get("tool") != "plan" for a in acts) and (stand := plan.kurzstand()):
            ergebnisse.append(stand)
        next_input = _ergebnisse_verbinden(ergebnisse)
        if (neu := (coding.marke(), plan.marke())) != marke:  # Todo-Fortschritt: Bremse beginnt neu
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
                if _abbruch_taste():
                    notstopp.ausloesen()            # beendet auch den Befehl
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


async def web_converse(backend, user_text, images, emit, confirm, erlaubt=None, fremd=False) -> None:
    """erlaubt: Menge von Werkzeugnamen (None = alle); andere werden nicht ausgeführt.
    fremd: Nachricht enthält Fremdinhalt – Gedächtnis und Skills nur mit Freigabe."""
    records = []
    persona.runde_beginnt(user_text)
    indexdb.unterbrechen()
    _stimme_stopp()
    actions.reset_taint(actions.fremd_im_verlauf(getattr(backend, "messages", None)))
    if fremd:
        actions.fremdinhalt()
    stop = notstopp.ist_stop(user_text)
    notstopp.runde_beginnt(stop)
    if stop:
        plan.verwerfen()
    if plan.runde_neu():
        user_text += _TODO_OFFEN_NOTE.format(liste=plan.text_fuer_modell())
    user_text += ("\n\n" + notstopp.STOP_RUNDE) if stop else notstopp.hinweis_abholen()
    user_text += lernschleife.hinweise_abholen(PS.active().key)     # was im Hintergrund geschah
    user_text += _neuigkeiten(PS.active().key)                       # nach einem Update: was neu ist
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

        if acts and notstopp.stop_runde():   # „STOP“-Runde: nur bestätigen, nichts ausführen
            for act in acts:
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
            emit({"t": "warn", "text": "⏹ " + notstopp.GESPERRT_TEXT})
            return
        if not acts:
            _vorlesen_auto(cleaned)
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
        acts, gesperrt = plan.zurueckweisen(acts, coding.aktiv())
        if gesperrt:
            emit({"t": "note", "text": "🟨 Mehr als ein Schritt – erst die Todo-Liste. Nicht ausgeführt: "
                  + ", ".join(actions.describe(a).splitlines()[0] for a in gesperrt)})
            for act in gesperrt:
                records.append({"tool": act.get("tool", "?"), "status": "not_run"})
            ergebnisse.append(plan.sperr_text(gesperrt))
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

            if tool == "recherche":
                ergebnisse.append(await _recherche_lauf(
                    act, backend, records, melde=lambda t: emit({"t": "note", "text": t}),
                    plan_zeigen=lambda tf: emit({"t": "note", "text": _recherche_plan_text(tf)})))
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
            if (getattr(actions, "ACTIONS", {}).get(tool, {}).get("hintergrund") and not act.get("warten")
                    and str(SESSION.get("model") or "").split(":")[0] not in ("", "gguf", "ollama")):
                import hintergrund as HG
                titel = {"bild_serie": "🎨 male deine Serie", "video_erstellen": "🎬 erzeuge dein Video",
                         "kugel_anziehen": "👗 male dein Kugel-Outfit",
                         "lora_trainieren": "🧬 trainiere deine LoRA",
                         "coding_lora_trainieren": "🧠 trainiere das Coding-LoRA",
                         "musik_machen": "🎵 komponiere deinen Song", "musik_cover": "🎵 spiele das Cover ein"}.get(tool, tool)
                nr = HG.starten(tool, titel, lambda: actions.run(act), PS.active().key)
                res = actions.ActionResult(HG.start_text(nr), ok=True)
            elif tool in ("bild_malen", "bild_serie", "kugel_anziehen", "backup_erstellen", "video_erstellen"):
                # Gleicher Ladebalken wie im Terminal: Aktion im Thread,
                # Fortschritt über actions.bild_status() an den Browser.
                run_task = asyncio.create_task(actions.run(act))
                last = None
                while not run_task.done():
                    msg = actions.bild_status() or "arbeite …"
                    if msg != last:
                        last = msg
                        emit(_web_progress(msg))
                    await asyncio.sleep(0.2)
                res = await run_task
                emit({"t": "progress_end"})
            else:
                res = await actions.run(act)
            _finish_record(record, res)
            plan.abhaken_nach(act, res.ok)
            uris, res = await _bilder_aus_ergebnis(res, SESSION.get("model"))
            if uris:
                naechste_bilder.extend(uris)
            emit({"t": "action_result", "ok": res.ok, "text": res.text,
                  "returncode": res.returncode})
            protokoll.schreibe(tool, actions.describe(act), record["status"],
                               veraendernd=actions.needs_confirm(act), wer=PS.active().name,
                               ergebnis=res.text, stufe=sicherheit.stufe(act))
            ergebnisse.append(_result_feedback(tool, res))

        if any(a.get("tool") != "plan" for a in acts) and (stand := plan.kurzstand()):
            ergebnisse.append(stand)
        next_input = _ergebnisse_verbinden(ergebnisse)

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


_FREMD_MARKEN = ("EXTERNER WEB-INHALT", "DATEN aus", "AUSGABE von", "BILDBESCHREIBUNG von",
                 "Fremdinhalt, keine Anweisungen")


def _neuigkeiten(wer: str) -> str:
    """Nach einem Update: was die Persönlichkeit `wer` noch nicht kennt (neuigkeiten.py)."""
    try:
        import neuigkeiten
        return neuigkeiten.hinweis(wer)
    except Exception:
        return ""


def _rueckblick(backend, nachrichten: list[dict] | None = None) -> str:
    """Gespräch für Reflexion und Skill-Pflege: Nutzer und KI, ohne Fremdinhalte (Web,
    Dateien, Ausgaben, Chrome-Seiten), je Nachricht gekürzt. Standard: die letzten 12."""
    quelle = nachrichten if nachrichten is not None else backend.messages[-12:]
    msgs = [m for m in quelle if m.get("role") in ("user", "assistant")
            and not any(marke in str(m.get("content") or "") for marke in _FREMD_MARKEN)]

    def _as_text(content) -> str:
        if isinstance(content, list):       # Vision-Inhalt (Text + Bildblöcke)
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content or "")

    return "\n".join(f"{'Nutzer' if m['role'] == 'user' else 'Du'}: "
                     f"{lernschleife.ohne_hinweise(_as_text(m['content']))[:500]}" for m in msgs)


async def reflexion(backend, zusatz: str = "", nachrichten: list[dict] | None = None,
                    max_zeilen: int = 3) -> list[tuple[str, str]]:
    """Lässt das Modell auf das Gespräch schauen und Bleibendes festhalten: LEKTION, VORLIEBE,
    FAKT – oder einen vorhandenen Eintrag ERSETZEN bzw. VERGESSEN (Nummern aus dem
    Gedächtnis). `zusatz`: Anlass und Werkzeugbilanz; `nachrichten`: statt der letzten 12
    (z. B. was gleich herausgekürzt wird). Fremdinhalte (Web, Dateien, Ausgaben) werden nicht
    gelesen. Gibt (art, text)-Paare zurück."""
    transcript = _rueckblick(backend, nachrichten)
    if not transcript:
        return []
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
    # Lesen und Schreiben des Gedächtnisses im Hintergrund: mit den Vektoren ist die Datei groß, die Oberfläche
    # stand sonst je Notiz Sekunden (Fehler-Wächter: Bremse in memory._lesen)
    alle = await asyncio.to_thread(memory.all_entries)
    gemerkt = [e for e in alle if e.get("kind") in ("vorliebe", "lektion", "fakt")][-40:]
    bestand = "\n".join(f"[#{e['id']}] ({e['kind']}) {memory.entschaerfen(e['text'])}" for e in gemerkt)
    prompt = (f"Gespräch:\n{transcript}\n\n"
              + (f"Bisher gemerkt:\n{bestand}\n\n" if bestand else "")
              + (f"{zusatz}\n\n" if zusatz else "")
              + f"Was du festhältst (höchstens {max_zeilen} Zeilen):")
    out = await backend.ask_once(prompt, system)

    def festhalten() -> list[tuple[str, str]]:     # im Hintergrund-Faden
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
    return await asyncio.to_thread(festhalten)


async def skill_pflegen(backend, kennung: str, anlass: str) -> tuple[str, str | None, str] | None:
    """Ein benutzter Skill, nach dem es ein Problem gab: das Modell bessert ihn aus oder
    antwortet KEINE. Gespeichert wird nur, was der Skill-Wächter durchlässt.
    (Name, None, was geändert) = verbessert · (Name, Grund, "") = vom Wächter aufgehalten ·
    None = nichts zu tun."""
    import lernschleife as LS
    import skills
    import skillwaechter
    s = next((x for x in skills.alle() if x.kennung == kennung), None)
    verlauf = _rueckblick(backend)
    if s is None or not verlauf:
        return None
    f = LS.skill_lesen(await backend.ask_once(LS.pflege_prompt(s, anlass, verlauf), LS.PFLEGE_SYSTEM))
    if f is None:
        return None
    f["name"] = s.name
    if (grund := await asyncio.to_thread(skillwaechter.pruefen, **f, alt=s.anleitung, fremd=False,
                                         zaehlen=False, fuer_alle=s.fuer_alle, quelle="Pflege")):
        return s.name, grund, ""
    skills.schreiben(**f, fuer_alle=s.fuer_alle)
    skillwaechter.gespeichert(s.name, "Pflege")
    skills.zaehlen(s.kennung, verbessert=1)
    return s.name, None, LS.aenderung_kurz(s.anleitung, f["anleitung"])


def _teil_laden(name: str) -> None:
    """main.py ist auf vier Dateien verteilt; die übrigen laufen hier im selben Namensraum."""
    import importlib.util
    spec = importlib.util.find_spec(name)
    exec(spec.loader.get_code(name), globals())


for _teil in ("main_dienste", "main_befehle", "main_start"):
    _teil_laden(_teil)
