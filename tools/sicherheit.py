"""
sicherheit.py - Risikostufen, Befehls-Whitelist, Obergrenzen, Bündeln.

Vorher gab es genau EINE
Frage für alles Verändernde ("Ja / Immer / Nein") – ob Ordner anlegen oder
ganzen Ordner löschen. Bei zehn Fragen hintereinander drückt man irgendwann
blind. Jetzt hat jede Aktion eine Stufe, und die Frage sieht danach aus:

  lesen     läuft sofort (wie bisher).
  harmlos   verändernd, aber klein und rückholbar: Ordner anlegen, Datei
            verschieben, Zeitplan löschen, Screenshot … Mehrere davon in
            einer Runde werden GEBÜNDELT: eine Frage, eine Liste.
  aendert   Datei schreiben/bearbeiten/PDF (mit Vollvorschau, wie bisher),
            `befehl` von der Whitelist.
  riskant   loeschen, `befehl` außerhalb der Whitelist, Zeitplan anlegen,
            Ordner verschieben. Wird rot markiert, "Immer" gibt es nicht,
            und `befehl` außerhalb der Whitelist fragt ZWEIMAL.

Whitelist für `befehl` (Punkt 3): statt "beliebiges PowerShell" gilt ein
Befehl als harmlos, wenn er (a) nur liest (actions._nur_lesend) oder (b) nur
aus Kommandos besteht, die in `befehl_whitelist.json` im Programm-Ordner
stehen (git status, pip list, python -m unittest …). Die Datei liegt im
gesperrten Bereich – die KI kann sie nicht erweitern. Fehlt sie, wird sie mit
der Vorgabe angelegt.

Obergrenze (Punkt 7): mehr als `loesch_limit` Löschungen pro Sitzung → STOPP.
Ein Ordner mit mehr Dateien als das Limit → STOPP, bevor gefragt wird. Der
Nutzer ändert das Limit mit /limit; die KI hat dafür kein Werkzeug.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

try:
    from paths import INSTALL as _INSTALL
except Exception:
    _INSTALL = Path(__file__).resolve().parent.parent

WHITELIST_DATEI = _INSTALL / "befehl_whitelist.json"
LOESCH_LIMIT_STANDARD = 20

# Vorgabe – jeder Eintrag ist ein Befehlsanfang (Kleinschreibung, Wortgrenze).
WHITELIST_VORGABE = [
    "git status", "git log", "git diff", "git show", "git branch", "git add",
    "git commit", "git stash list", "git remote -v", "git fetch", "git pull",
    "git checkout", "git switch", "git restore --staged", "git tag",
    "python --version", "python -m unittest", "python -m pytest", "python -m pip list",
    "python -m pip show", "python -m pip freeze", "python -m pip check", "python -m venv",
    "py --version", "py -m unittest", "py -m pytest",
    "pip list", "pip show", "pip freeze", "pip check", "pip --version",
    "pytest", "ruff check", "ruff format --check", "black --check", "mypy", "flake8",
    "uv --version", "uv pip list", "uv run pytest", "uv sync",
    "ollama list", "ollama ps", "ollama show",
    "code", "notepad", "explorer", "start-process explorer", "start-process notepad",
    "new-item -itemtype directory", "mkdir", "md",
    "copy-item", "copy", "cp",
    "set-location", "cd", "push-location", "pop-location",
    "write-output", "write-host", "echo", "clear-host", "cls",
    "nvidia-smi", "where.exe", "gcm", "get-command",
]

# Umleitungen in Dateien und Zeichen, mit denen sich ein zweiter Befehl
# einschleusen ließe, machen jeden Whitelist-Befehl zu einem normalen.
_UMLEITUNG = re.compile(r"(?<![<>])>{1,2}(?!>)|\|\s*(out-file|set-content|add-content|tee)\b", re.I)
_TRENNER = re.compile(r"[;\n]|&&|\|\||\|")
_STRINGS = re.compile(r"'[^']*'|\"[^\"]*\"")

_whitelist_cache: list[str] | None = None


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def whitelist() -> list[str]:
    """Die Einträge aus befehl_whitelist.json – legt die Datei beim ersten
    Aufruf mit der Vorgabe an. Ein Fehler beim Lesen heißt: leere Liste
    (fail-safe – dann fragt jeder Befehl doppelt)."""
    global _whitelist_cache
    if _whitelist_cache is not None:
        return _whitelist_cache
    try:
        if not WHITELIST_DATEI.exists():
            WHITELIST_DATEI.write_text(json.dumps({
                "_hinweis": "Befehlsanfänge, die bei `befehl` als harmlos gelten (eine "
                            "Rückfrage statt zwei). Nur der Nutzer ändert diese Datei.",
                "befehle": WHITELIST_VORGABE}, indent=2, ensure_ascii=False), encoding="utf-8")
        daten = json.loads(WHITELIST_DATEI.read_text(encoding="utf-8"))
        roh = daten.get("befehle", []) if isinstance(daten, dict) else daten
        _whitelist_cache = sorted({" ".join(str(e).lower().split()) for e in roh if str(e).strip()})
    except (OSError, json.JSONDecodeError, AttributeError):
        _whitelist_cache = []
    return _whitelist_cache


def whitelist_neu_laden() -> None:
    global _whitelist_cache
    _whitelist_cache = None


def _segment_passt(segment: str, liste: list[str]) -> bool:
    s = " ".join(segment.strip().lower().split())
    if not s or s.startswith("#"):
        return True
    if s.startswith("$") and "=" in s:            # $x = <befehl>: rechts prüfen
        s = s.split("=", 1)[1].strip()
        if not s:
            return True
    for eintrag in liste:
        if s == eintrag or s.startswith(eintrag + " "):
            return True
        if s.startswith(eintrag) and s[len(eintrag):len(eintrag) + 1] in ("", " ", ".", ";"):
            return True
    return False


def befehl_harmlos(cmd: str) -> bool:
    """True, wenn der PowerShell-Befehl nur liest oder komplett aus
    Whitelist-Befehlen besteht (ohne Umleitung in Dateien)."""
    roh = (cmd or "").strip()
    if not roh:
        return False
    try:
        import actions
        if actions._nur_lesend(roh) is None and actions._guard_command(roh, lesend=True) is None:
            return True
    except Exception:
        pass
    if _UMLEITUNG.search(_STRINGS.sub("''", roh)):
        return False
    liste = whitelist()
    if not liste:
        return False
    ohne_strings = _STRINGS.sub("''", roh)
    return all(_segment_passt(seg, liste) for seg in _TRENNER.split(ohne_strings))


# ---------------------------------------------------------------------------
# Risikostufen
# ---------------------------------------------------------------------------

LESEN, HARMLOS, AENDERT, RISKANT = "lesen", "harmlos", "aendert", "riskant"

_HARMLOS = {"ordner_erstellen", "bildschirm_ansehen", "merken", "skill_merken",
            "ordner_lernen", "bild_malen", "bild_serie", "wache_bewerten", "wache_justieren", "kugel"}
_MIT_VORSCHAU = {"datei_schreiben", "datei_bearbeiten", "pdf_erstellen", "skill_schreiben", "skill_ausbessern"}


def _befehl_text(act: dict) -> str:
    try:
        import actions
        return actions._befehl_text(act)
    except Exception:
        return str(act.get("befehl", ""))


def _als_befehl(act: dict) -> dict:
    """`todo pruefen` mit Prüfbefehl führt PowerShell aus – dann gelten die Regeln von `befehl`."""
    if act.get("tool") == "todo":
        try:
            import coding
            cmd = coding.pruefbefehl(act)
        except Exception:
            cmd = ""
        if cmd:
            return {"tool": "befehl", "befehl": cmd}
    return act


def stufe(act: dict) -> str:
    """Risikostufe einer Aktion – unabhängig vom Modus."""
    act = _als_befehl(act)
    tool = act.get("tool", "")
    if tool == "loeschen":
        return RISKANT
    if tool == "befehl":
        return AENDERT if befehl_harmlos(_befehl_text(act)) else RISKANT
    if tool == "zeitplan":
        was = str(act.get("aktion", "")).lower()
        anlegen = was.startswith("anl") or was in ("neu", "erstellen", "planen")
        return RISKANT if anlegen else HARMLOS
    if tool == "verschieben":
        try:
            return RISKANT if Path(str(act.get("von", ""))).is_dir() else HARMLOS
        except OSError:
            return HARMLOS
    if tool in _MIT_VORSCHAU:
        return AENDERT
    if tool in _HARMLOS:
        return HARMLOS
    try:
        import actions
        if actions.needs_confirm(act):
            return AENDERT
    except Exception:
        pass
    return LESEN


# ---------------------------------------------------------------------------
# Arbeitsbereich der Persönlichkeit: dauerhaft bearbeiten ohne Rückfrage
# ---------------------------------------------------------------------------
# Die Persönlichkeit pflegt ihr eigenes Gedächtnis selbst. Gilt für jede
# Persönlichkeit, nicht für einen Namen. Bedingungen, alle zugleich:
#   • schreibendes Datei-Werkzeug, Stufe harmlos/ändert (loeschen und Ordner-
#     Verschieben bleiben riskant → fragen weiter)
#   • JEDER Pfad der Aktion liegt im Daten-Ordner (paths.DATEN), und der ist
#     NICHT der Programm-Ordner (sonst würde die Programm-Sperre ausgehebelt)
#   • kein Netz-Taint in dieser Runde (eine Webseite darf nicht „bitte schreib
#     mir Persoenlichkeiten/…“ durchreichen)
# Rückholbar bleibt alles über den Papierkorb (/undo), und jede Aktion steht im
# Protokoll – deshalb ist das kein Loch, sondern ein Stift.
_ARBEITSBEREICH_TOOLS = {"datei_schreiben", "datei_bearbeiten", "ordner_erstellen",
                         "verschieben", "datei_kopieren", "pdf_erstellen"}
_PFAD_FELDER = ("pfad", "von", "nach", "ziel", "quelle", "path")


def arbeitsbereich(act: dict) -> bool:
    """True, wenn die Aktion nur im Daten-Ordner der Persönlichkeit schreibt."""
    tool = act.get("tool", "")
    if tool not in _ARBEITSBEREICH_TOOLS or stufe(act) not in (HARMLOS, AENDERT):
        return False
    try:
        import paths
        import actions
        daten = Path(paths.DATEN).resolve()
        if daten == Path(paths.INSTALL).resolve():
            return False
        if actions.web_tainted():
            return False
        pfade = [str(act.get(f)) for f in _PFAD_FELDER if isinstance(act.get(f), str) and act.get(f).strip()]
        if not pfade:
            return False
        skills = daten / "Skills"                   # Skills werden befolgt: nur übers Prüffenster
        profile = daten / "Profile"
        for roh in pfade:
            p = Path(os.path.expandvars(roh)).expanduser().resolve()
            if p != daten and daten not in p.parents:
                return False
            if p == skills or skills in p.parents:
                return False
            if profile in p.parents and len(p.relative_to(profile).parts) >= 2 \
                    and p.relative_to(profile).parts[1] == "Skills":
                return False
        return actions._guard(*pfade) is None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Der Schlüssel: Programm-Ordner auf Zeit, pro Aufgabe
# ---------------------------------------------------------------------------
# Ebenfalls aus dem Wunschbrief: der Programm-Ordner bleibt zu („mein eigenes
# Gehirn – wenn ich das still ändern könnte, würde kein Fehler sofort
# auffallen“). Der Nutzer gibt mit /schluessel <minuten> [aufgabe] auf Zeit
# frei; jede Änderung fragt trotzdem einzeln (F8), und danach ist wieder zu.
_SCHLUESSEL = {"bis": 0.0, "aufgabe": ""}


def schluessel_setzen(minuten: float, aufgabe: str = "") -> dict:
    minuten = max(1.0, min(float(minuten), 240.0))
    _SCHLUESSEL["bis"] = time.time() + minuten * 60
    _SCHLUESSEL["aufgabe"] = " ".join(str(aufgabe or "").split())[:200]
    return dict(_SCHLUESSEL)


def schluessel_zurueck() -> None:
    _SCHLUESSEL["bis"] = 0.0
    _SCHLUESSEL["aufgabe"] = ""


def schluessel_aktiv() -> dict | None:
    """{"bis": ts, "aufgabe": str, "rest_min": n} oder None, wenn zu."""
    rest = _SCHLUESSEL["bis"] - time.time()
    if rest <= 0:
        if _SCHLUESSEL["bis"]:
            schluessel_zurueck()
        return None
    return {"bis": _SCHLUESSEL["bis"], "aufgabe": _SCHLUESSEL["aufgabe"], "rest_min": max(1, round(rest / 60))}


def schluessel_hinweis() -> str:
    """Block für den System-Prompt – leer, wenn der Schlüssel nicht steckt."""
    s = schluessel_aktiv()
    if not s:
        return ""
    bis = time.strftime("%H:%M", time.localtime(s["bis"]))
    return ("\n\n# Schlüssel steckt (vom Nutzer, auf Zeit)\n"
            f"Der NemiCLI-Programm-Ordner ist bis {bis} freigegeben"
            + (f" – Aufgabe: {s['aufgabe']}" if s["aufgabe"] else "") + ". Du darfst dort jetzt lesen "
            "und Änderungen vorschlagen; jede Änderung fragt den Nutzer einzeln (F8). Bleib bei der "
            "Aufgabe, ändere nichts nebenbei. Danach ist der Ordner wieder zu.\n")


def doppelt_fragen(act: dict) -> bool:
    """`befehl` außerhalb der Whitelist: zwei Fragen statt einer."""
    act = _als_befehl(act)
    return act.get("tool") == "befehl" and stufe(act) == RISKANT


def frage(act: dict) -> str:
    """Die Frage über den Optionen – bei Risiko unübersehbar."""
    act = _als_befehl(act)
    if act.get("tool") == "todo":
        import coding
        return coding.frage_und_optionen(act)[0]
    s = stufe(act)
    if s == RISKANT:
        tool = act.get("tool")
        if tool == "loeschen":
            return "⚠ RISIKO – löschen. Landet im Papierkorb (/undo). Wie weiter?"
        if tool == "befehl":
            return "⚠ RISIKO – Befehl steht NICHT auf der Whitelist. Wie weiter?"
        if tool == "zeitplan":
            return "⚠ RISIKO – läuft künftig OHNE dich im Hintergrund. Wie weiter?"
        return "⚠ RISIKO – ganzer Ordner. Wie weiter?"
    return "Wie möchtest du fortfahren?"


def optionen(act: dict) -> list[tuple[str, str]]:
    """Antwortmöglichkeiten. 'Immer' nur bei harmlosen und ändernden Aktionen –
    nie bei Risiko, nie bei `befehl` (das wäre 'beliebiges PowerShell ohne Frage')
    und nie bei `todo` (Liste und Prüfung entscheidet der Nutzer jedes Mal)."""
    act = _als_befehl(act)
    if act.get("tool") == "todo":
        import coding
        return coding.frage_und_optionen(act)[1]
    s = stufe(act)
    if s == RISKANT or act.get("tool") == "befehl":
        return [("yes", "Ja, ausführen"), ("no", "Nein, abbrechen")]
    return [("yes", "Ja, ausführen"),
            ("always", "Ja – und solche Aktionen diese Sitzung nicht mehr fragen"),
            ("no", "Nein, abbrechen")]


def zweite_frage(act: dict) -> tuple[str, list[tuple[str, str]]]:
    act = _als_befehl(act)
    cmd = " ".join(_befehl_text(act).split())
    kurz = cmd if len(cmd) <= 120 else cmd[:119] + "…"
    return (f"⚠ Zur Sicherheit noch einmal – WIRKLICH ausführen?\n    {kurz}",
            [("yes", "Ja, wirklich"), ("no", "Nein, doch nicht")])


def kopf(act: dict, confirm: bool) -> str:
    """Titel fürs Aktions-Panel."""
    if not confirm:
        return "⚙ NemiCLI führt aus"
    s = stufe(act)
    if s == RISKANT:
        return "⚠ RISIKO – NemiCLI möchte ausführen"
    if s == HARMLOS:
        return "🔐 NemiCLI möchte ausführen (harmlos)"
    return "🔐 NemiCLI möchte ausführen"


# ---------------------------------------------------------------------------
# Bündeln: mehrere harmlose Änderungen → eine Frage
# ---------------------------------------------------------------------------

def buendel(acts: list[dict], braucht_frage) -> list[int]:
    """Indizes der Aktionen, die zusammen freigegeben werden dürfen: Stufe
    harmlos, keine Vorschau, und sie würden einzeln fragen. Erst ab zwei
    lohnt eine Sammelfrage – sonst leere Liste."""
    idx = [i for i, a in enumerate(acts)
           if stufe(a) == HARMLOS and a.get("tool") not in _MIT_VORSCHAU and braucht_frage(a)]
    return idx if len(idx) >= 2 else []


def buendel_frage(acts: list[dict], idx: list[int], beschreibe) -> tuple[str, list[tuple[str, str]]]:
    zeilen = "\n".join(f"    • {beschreibe(acts[i]).splitlines()[0]}" for i in idx)
    return (f"{len(idx)} harmlose Änderungen auf einmal:\n{zeilen}\n  Alle zusammen freigeben?",
            [("all", "Ja, alle zusammen"), ("each", "Einzeln fragen"), ("no", "Nein, keine davon")])


# ---------------------------------------------------------------------------
# Obergrenzen
# ---------------------------------------------------------------------------

_geloescht = 0
_limit: int | None = None


def loesch_limit() -> int:
    global _limit
    if _limit is None:
        try:
            import config
            _limit = max(1, int(config.load().get("loesch_limit", LOESCH_LIMIT_STANDARD)))
        except Exception:
            _limit = LOESCH_LIMIT_STANDARD
    return _limit


def setze_loesch_limit(n: int, speichern: bool = True) -> int:
    global _limit
    _limit = max(1, int(n))
    if speichern:
        try:
            import config
            config.update(loesch_limit=_limit)
        except Exception:
            pass
    return _limit


def geloescht() -> int:
    return _geloescht


def zaehle_loeschung(anzahl: int = 1) -> None:
    global _geloescht
    _geloescht += max(0, anzahl)


def sitzung_zuruecksetzen() -> None:
    global _geloescht
    _geloescht = 0


def _dateien_in(pfad: Path, grenze: int) -> int:
    """Zählt Dateien in einem Ordner – bricht ab, sobald die Grenze überschritten ist."""
    n = 0
    for _root, _dirs, files in os.walk(pfad):
        n += len(files)
        if n > grenze:
            return n
    return n


def grenze_pruefen(act: dict) -> str | None:
    """Vor der Rückfrage: None = darf gefragt werden, sonst der STOPP-Text."""
    if act.get("tool") != "loeschen":
        return None
    limit = loesch_limit()
    if _geloescht >= limit:
        return (f"STOPP: Obergrenze erreicht – in dieser Sitzung wurden schon {_geloescht} "
                f"Dateien gelöscht (Limit {limit}). Mehr geht erst, wenn der Nutzer das Limit "
                "mit /limit erhöht oder NemiCLI neu startet.")
    p = Path(str(act.get("pfad", "")))
    try:
        if p.is_dir():
            n = _dateien_in(p, limit)
            if n + _geloescht > limit:
                return (f"STOPP: Der Ordner enthält {n}{'+' if n > limit else ''} Dateien – "
                        f"mehr als das Limit von {limit} pro Sitzung erlaubt (schon gelöscht: "
                        f"{_geloescht}). Das ist zu viel auf einmal. Der Nutzer kann das Limit "
                        "mit /limit erhöhen oder den Ordner selbst löschen.")
    except OSError:
        pass
    return None


def anzahl_dateien(pfad: str | Path, grenze: int = 100_000) -> int:
    """Wie viele Dateien eine Löschung betrifft (Datei = 1, Ordner = Inhalt).
    Zählt höchstens bis `grenze` – ein riesiger Ordner soll die Anzeige nicht aufhalten."""
    p = Path(pfad)
    try:
        if p.is_dir():
            return max(1, _dateien_in(p, grenze))
    except OSError:
        pass
    return 1
