"""
modes.py - Arbeitsmodus: Chatten · Nur Lesen · Normal · Auto · Auto ON  (Shift+Tab / /modus)

Der Modus entscheidet, ob eine Aktion sofort läuft, erst gefragt wird oder gar
nicht darf. Die harten Schutzmechanismen (Systemordner-Sperre, Befehlsfilter,
Web-Allowlist, Netz-Taint fürs Merken) hängen NICHT am Modus – die gelten immer.

  💬 chat    Reines Gespräch, Recherche, Planen. Jede Aktion fragt – auch Lesen.
             Merken bleibt frei: was man beim Reden herausfindet, soll bleiben,
             damit man später gemeinsam bauen kann.
  👁 lesen   Alles lesen, nichts anrühren. Verändernde Aktionen sind gesperrt
             (nicht mal eine Rückfrage), Lesen läuft sofort.
  ⚙ normal  Der klassische Weg: Lesen sofort, Verändern mit Rückfrage (F8).
  ⚡ auto    Ohne Rückfragen – Dateiaktionen im aktuellen Ordner und Befehle
             laufen direkt; Dateiaktionen AUSSERHALB des Ordners fragen.
             Dazu der Zünder: nach AUTO_FUSE_S Sekunden fällt NemiCLI von
             selbst in „chat" zurück, damit Auto nie versehentlich anbleibt.
  🚀 autoan  Wie Auto, aber ohne Zünder: bleibt an, bis der Nutzer umschaltet.

Der Modus gilt nur für die laufende Sitzung; jeder Start beginnt in „normal".
"""

from __future__ import annotations

import os
import time
from pathlib import Path

AUTO_FUSE_S = 5 * 60          # Zünder: so lange darf Auto am Stück laufen

# Reihenfolge = Reihenfolge beim Durchschalten mit Shift+Tab.
MODES: list[tuple[str, str, str]] = [
    # key       Symbol  Anzeige
    ("chat",    "💬",   "Chatten"),
    ("lesen",   "👁",   "Nur Lesen"),
    ("normal",  "⚙",   "Normal"),
    ("auto",    "⚡",   "Auto"),
    ("autoan",  "🚀",   "Auto ON"),
]
AUTO_MODES = {"auto", "autoan"}       # beide laufen ohne Rückfragen
_KEYS = [m[0] for m in MODES]
ALIASES = {"chatten": "chat", "reden": "chat", "plan": "chat", "planen": "chat",
           "lese": "lesen", "read": "lesen", "nur-lesen": "lesen", "nurlesen": "lesen",
           "std": "normal", "default": "normal", "kontrolliert": "normal",
           "automatisch": "auto", "automode": "auto",
           "auto on": "autoan", "auto-on": "autoan", "autoon": "autoan", "auto an": "autoan",
           "dauer": "autoan", "dauerauto": "autoan", "on": "autoan"}

DEFAULT = "normal"
_current = DEFAULT
_auto_until: float | None = None     # monotonic-Zeit, zu der der Zünder auslöst

# Werkzeuge, die auch im Chat-Modus frei bleiben (schreiben nur nach learned/).
MEMORY_TOOLS = {"merken", "skill_merken"}

# Reine LESE-Werkzeuge: NUR diese dürfen im Modus „lesen" laufen. Alles andere
# blockt dort – ausdrücklich als Allowlist, damit ein neues oder unbekanntes
# Werkzeug automatisch gesperrt ist (fail-safe) statt versehentlich durchzurutschen.
# WICHTIG: Das ist die Wahrheit über „liest nur", NICHT das confirm-Flag. Einige
# schreibende Werkzeuge haben confirm=False (bild_malen → Bilder/, merken/
# skill_merken → learned/, ordner_lernen → learned/); die gehören NICHT hierher.
READ_TOOLS = {
    "datei_lesen", "bild_ansehen", "bild_fragen", "anleitung_lesen", "ordner_auflisten", "ordner_erkennen",
    "charakter_zeigen",
    "api_nachschlagen", "code_pruefen", "paket_info", "seite_ansehen", "doku_suchen",
    "dateien_suchen", "inhalt_suchen",
    "web_lesen", "web_wiki", "web_suche", "ml_status",
    "gedaechtnis_suchen", "gedaechtnis_lesen", "skill_laden",
    # feste Systemabfragen (systemabfrage.py) + Zeitplan ansehen
    "abfragen", "zeitplan_anzeigen",
    # Systemwache: nachsehen ist lesen
    "wache_status", "wache_alarme", "wache_ereignisse", "wache_inventar",
}
# Im Hintergrund-Prozess der Wache (Modus lesen, niemand drückt F8) sind ein
# paar schreibende Werkzeuge trotzdem frei: Urteilen und Justieren schreiben
# nur in die Wache-Datenbank; Merken schreibt nach learned/; bild_malen malt
# nach Bilder/ – das Kugel-Fenster soll Bilder liefern können. Dateien, Befehle,
# Löschen bleiben gesperrt: dafür gibt es das große NemiCLI.
WACHE_URTEIL_TOOLS = {"wache_bewerten", "wache_justieren", "merken", "skill_merken", "bild_malen",
                      "bildschirm_ansehen", "kugel"}
_wache_dienst = False


def wache_dienst(an: bool) -> None:
    global _wache_dienst
    _wache_dienst = bool(an)
# Pfad-Felder verändernder Werkzeuge, für die Ordner-Grenze im Auto-Modus.
_PATH_FIELDS = ("pfad", "von", "nach", "ziel", "quelle", "path")


# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------

def current() -> str:
    check_fuse()
    return _current


def label(key: str | None = None) -> str:
    """'⚡ Auto' – Symbol + Name."""
    key = key or current()
    for k, sym, name in MODES:
        if k == key:
            return f"{sym} {name}"
    return key


def name(key: str | None = None) -> str:
    key = key or current()
    return next((n for k, _, n in MODES if k == key), key)


def resolve(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    t = ALIASES.get(t, t)
    if t in _KEYS:
        return t
    hits = [k for k in _KEYS if k.startswith(t)]
    return hits[0] if len(hits) == 1 else None


def set_mode(key: str) -> str:
    """Setzt den Modus; bei 'auto' wird der Zünder scharf gemacht. Gibt den Key zurück."""
    global _current, _auto_until
    if key not in _KEYS:
        raise ValueError(f"Unbekannter Modus: {key}")
    _current = key
    _auto_until = (time.monotonic() + AUTO_FUSE_S) if key == "auto" else None
    return key


def cycle() -> str:
    """Nächster Modus (Shift+Tab): chat → lesen → normal → auto → autoan → chat …"""
    i = _KEYS.index(_current)
    return set_mode(_KEYS[(i + 1) % len(_KEYS)])


def auto_remaining() -> int | None:
    """Sekunden bis der Zünder auslöst – None, wenn nicht im Auto-Modus."""
    if _current != "auto" or _auto_until is None:
        return None
    return max(0, int(round(_auto_until - time.monotonic())))


def check_fuse() -> bool:
    """Ist die Auto-Zeit um? Dann zurück nach 'chat'. True, wenn gerade umgeschaltet wurde."""
    global _current, _auto_until
    if _current == "auto" and _auto_until is not None and time.monotonic() >= _auto_until:
        _current = "chat"
        _auto_until = None
        return True
    return False


def fuse_message() -> str:
    return (f"⏱ Auto-Zünder: {AUTO_FUSE_S // 60} Minuten um – zurück im Modus "
            f"{label('chat')}. Shift+Tab oder /modus auto startet Auto neu.")


# ---------------------------------------------------------------------------
# Entscheidung pro Aktion
# ---------------------------------------------------------------------------

def _inside_cwd(action: dict) -> bool:
    """Liegen ALLE Pfade der Aktion im aktuellen Ordner (oder darunter)?
    Ohne Pfadfeld (z.B. befehl) gilt: ja."""
    import workspace
    ws = workspace.pfad()
    cwd = ws if ws else Path(os.getcwd()).resolve()
    for f in _PATH_FIELDS:
        v = action.get(f)
        if not isinstance(v, str) or not v.strip():
            continue
        try:
            p = Path(os.path.expandvars(v)).expanduser().resolve()
        except Exception:
            return False
        if p != cwd and cwd not in p.parents:
            return False
    return True


def _ausserhalb_workspace(action: dict) -> str | None:
    """Bricht diese Aktion aus dem festgenagelten Workspace aus?

    Gibt das erste verletzende Pfadfeld zurueck, sonst None. Ohne aktiven
    Workspace immer None. Werkzeuge OHNE Pfadfeld (z.B. `befehl`) fangen wir
    hier nicht ab - die haben ihren eigenen Filter und laufen im Arbeitsordner."""
    import workspace
    if not workspace.aktiv():
        return None
    for f in _PATH_FIELDS:
        v = action.get(f)
        if not isinstance(v, str) or not v.strip():
            continue
        if not workspace.drin(v):
            return v
    return None


def decide(action: dict, needs_confirm: bool) -> str:
    """'run' | 'ask' | 'block' – abhängig vom Modus.

    needs_confirm ist das Urteil aus actions.needs_confirm (inkl. Netz-Taint);
    es kann nur VERSCHÄRFEN, nie lockern – ein getaintetes merken fragt in
    jedem Modus."""
    mode = current()
    tool = action.get("tool", "")
    if tool == "plan":                 # die Todo-Liste liegt nur im Speicher – in jedem Modus frei
        return "run"
    if tool == "menue_oeffnen":        # das Menü IST die Rückfrage: der Nutzer wählt selbst
        return "run"
    # Der Workspace-Riegel steht VOR den Modi: hat der Nutzer einen Ordner
    # festgenagelt, kommt da nichts raus - auch nicht im Auto-Modus.
    if _ausserhalb_workspace(action) is not None:
        return "block"
    if mode == "lesen":
        # Nur echte Lese-Werkzeuge laufen – ausdrücklich per Allowlist. Alles,
        # was irgendwo schreibt (auch confirm=False wie bild_malen, merken,
        # skill_merken, ordner_lernen, subagenten), wird blockiert: „nix anrühren".
        if tool in WACHE_URTEIL_TOOLS and _wache_dienst:
            return "run"
        if tool not in READ_TOOLS:
            return "block"
        return "ask" if needs_confirm else "run"        # Netz-Taint kann selbst Lesen bremsen
    if tool in ("skill_schreiben", "skill_ausbessern"):
        # Skills werden später befolgt: Prüffenster in jedem Modus, auch Auto –
        # frei nur mit /skills selbst an (steckt in needs_confirm, samt Netz-Taint).
        return "ask" if needs_confirm else "run"
    if tool == "einstellung_aendern":
        return "ask"                    # Prüffenster in jedem Modus, auch Auto
    if tool == "todo":
        # Mit Prüfbefehl gelten die Regeln von `befehl`. Sonst entscheidet der
        # Nutzer in jedem Modus: Liste abnicken, Kriterium ändern, streichen,
        # ohne Befehl prüfen – sonst setzt sich die KI ihr Grün selbst.
        import coding
        cmd = coding.pruefbefehl(action)
        if cmd:
            return decide({"tool": "befehl", "befehl": cmd}, True)
        return "ask" if needs_confirm else "run"
    if mode == "chat":
        if tool in MEMORY_TOOLS:
            return "ask" if needs_confirm else "run"
        return "ask"
    if mode in AUTO_MODES:
        if tool in MEMORY_TOOLS and needs_confirm:      # Netz-Taint bleibt
            return "ask"
        if needs_confirm and tool != "befehl" and not _inside_cwd(action):
            return "ask"                                 # Datei außerhalb des Ordners
        return "run"
    return "ask" if needs_confirm else "run"             # normal


def block_message(action: dict) -> str:
    draussen = _ausserhalb_workspace(action)
    if draussen is not None:
        import workspace
        return (f"'{draussen}' liegt außerhalb des Workspace. "
                + workspace.grenze_text())
    return (f"Im Modus {label('lesen')} ist '{action.get('tool', '?')}' gesperrt – "
            "hier wird nur gelesen, nichts angerührt. Schlag dem Nutzer vor, was du tun "
            "würdest; zum Ausführen schaltet er mit Shift+Tab auf Normal oder Auto.")


# ---------------------------------------------------------------------------
# Für Prompt und Anzeige
# ---------------------------------------------------------------------------

_PROMPT_HINTS = {
    "chat": ("Modus 💬 Chatten: Gespräch, Recherche, Planen. Jede Aktion – auch Lesen – wird "
             "dem Nutzer erst zur Freigabe vorgelegt; setz sie nur ein, wenn sie wirklich "
             "gebraucht wird. Merken (merken/skill_merken) ist frei und ausdrücklich erwünscht: "
             "was ihr beim Reden herausfindet und entscheidet, halte fest, damit ihr später "
             "gemeinsam darauf bauen könnt."),
    "lesen": ("Modus 👁 Nur Lesen: Du darfst alles lesen, suchen und ansehen, aber NICHTS "
              "verändern – schreibende Werkzeuge und Befehle sind gesperrt und werden gar nicht "
              "erst gefragt. Willst du etwas ändern, beschreib es als Vorschlag; der Nutzer "
              "schaltet dann um."),
    "normal": ("Modus ⚙ Normal: Lesen läuft sofort, verändernde Aktionen fragen den Nutzer "
               "vorher (F8)."),
    "auto": ("Modus ⚡ Auto: Dateiaktionen im aktuellen Ordner und Befehle laufen OHNE Rückfrage "
             "– arbeite deshalb besonders sorgfältig, in kleinen Schritten, und prüfe Ergebnisse. "
             "Dateien außerhalb des aktuellen Ordners fragen weiterhin. Der Modus schaltet sich "
             "nach 5 Minuten selbst ab."),
    "autoan": ("Modus 🚀 Auto ON: Dateiaktionen im aktuellen Ordner und Befehle laufen OHNE "
               "Rückfrage – dauerhaft, bis der Nutzer umschaltet. Arbeite deshalb besonders "
               "sorgfältig, in kleinen Schritten, und prüfe Ergebnisse. Dateien außerhalb des "
               "aktuellen Ordners fragen weiterhin."),
}


def prompt_hint() -> str:
    """Kurzer Block für den System-Prompt, damit das Modell den Modus kennt."""
    return "\n\n# Arbeitsmodus (vom Nutzer per Shift+Tab gewählt)\n" + _PROMPT_HINTS[current()] + "\n"


def status_fragment() -> tuple[str, str]:
    """(Key, Text) für die Statusleiste: '⚡ Auto 4:12' bzw. '👁 Nur Lesen'."""
    m = current()
    text = label(m)
    rest = auto_remaining()
    if rest is not None:
        text += f" {rest // 60}:{rest % 60:02d}"
    return m, text
