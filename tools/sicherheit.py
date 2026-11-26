"""
sicherheit.py - Risikostufen, Befehls-Whitelist, Obergrenzen, Bündeln.

Jede Aktion hat eine Stufe, die Rückfrage richtet sich danach:

  lesen     läuft sofort.
  harmlos   verändernd, aber klein und rückholbar: Ordner anlegen, Datei
            verschieben, Zeitplan löschen, Screenshot … Mehrere davon in
            einer Runde werden GEBÜNDELT: eine Frage, eine Liste.
  aendert   Datei schreiben/bearbeiten/PDF (mit Vollvorschau),
            `befehl` von der Whitelist.
  riskant   loeschen, `befehl` außerhalb der Whitelist, Zeitplan anlegen,
            Ordner verschieben. Wird rot markiert, "Immer" gibt es nicht,
            und `befehl` außerhalb der Whitelist fragt ZWEIMAL.

Whitelist für `befehl`: statt "beliebiges PowerShell" gilt ein
Befehl als harmlos, wenn er (a) nur liest (actions._nur_lesend) oder (b) nur
aus Kommandos besteht, die in `befehl_whitelist.json` im Programm-Ordner
stehen (git status, pip list, python -m unittest …). Die Datei liegt im
gesperrten Bereich – die KI kann sie nicht erweitern. Fehlt sie, wird sie mit
der Vorgabe angelegt.

Obergrenze: mehr als `loesch_limit` Löschungen pro Sitzung → STOPP.
Ein Ordner mit mehr Dateien als das Limit → STOPP, bevor gefragt wird. Der
Nutzer ändert das Limit mit /limit; die KI hat dafür kein Werkzeug.
"""

from __future__ import annotations

import contextlib
import contextvars
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
    "code", "notepad", "start-process notepad",
    "new-item -itemtype directory", "mkdir", "md",
    "set-location", "cd", "push-location", "pop-location",
    "write-output", "write-host", "echo", "clear-host", "cls",
    "nvidia-smi", "where.exe", "gcm", "get-command",
]

# Umleitungen in Dateien und Zeichen, mit denen sich ein zweiter Befehl
# einschleusen ließe, machen jeden Whitelist-Befehl zu einem normalen.
_UMLEITUNG = re.compile(r"(?<![<>])>{1,2}(?!>)|\|\s*(out-file|set-content|add-content|tee)\b", re.I)
_TRENNER = re.compile(r"[;\r\n]|&&|\|\||\|")        # PowerShell trennt auch an einem einzelnen \r
# Schalter, mit denen ein Whitelist-Befehl Programme startet, Dateien schreibt oder Erweiterungen installiert.
_OPTION_GEFAEHRLICH = re.compile(
    r"(?i)(?<![\w-])(--upload-pack|--receive-pack|--exec|--config|--output|--ext-diff|--textconv|"
    r"--open-files-in-pager|--install-extension|--uninstall-extension|--extensions-dir|--user-data-dir|"
    r"--git-dir|--work-tree)(?![\w-])")
# VS Code (`code` steht auf der Whitelist): Server und Tunnel öffnen den Rechner nach außen, Entwicklungs-
# erweiterungen und der Debug-Port führen fremden Code aus, Remote-Adressen verbinden zu anderen Rechnern.
_CODE_BEFEHL = re.compile(r"""(?i)(?:^|[;|&\r\n({])\s*&?\s*["']?code(?:\.cmd|\.exe)?["']?(?![\w.-])""")
_CODE_GEFAEHRLICH = re.compile(
    r"(?i)(?<![\w-])(serve-web|tunnel|--extensiondevelopmentpath|--extensiontestspath|--inspect[\w-]*|"
    r"--enable-proposed-api|--remote|--folder-uri|--file-uri|--server-data-dir)(?![\w-])")


def _option_gefaehrlich(roh: str) -> bool:
    """Auch in Anführungszeichen und mit ` dazwischen: PowerShell reicht das Argument so weiter."""
    for text in (roh, roh.replace("`", "")):
        if _OPTION_GEFAEHRLICH.search(text):
            return True
        if _CODE_BEFEHL.search(text) and _CODE_GEFAEHRLICH.search(text):
            return True
    return False


_STRINGS = re.compile(r"'[^']*'|\"[^\"]*\"")
# PowerShell zählt auch typografische Anführungszeichen; ` maskiert das nächste Zeichen.
_EINFACH = "'\u2018\u2019\u201a\u201b"
_DOPPELT = '"\u201c\u201d\u201e'


def _ohne_strings(cmd: str) -> tuple[str, bool]:
    """(Befehl mit Zeichenketten als '' und `x als _, ob eine Zeichenkette in doppelten Anführungszeichen
    $( ) oder @( ) enthält). Getrennt wie PowerShell; eine offene Zeichenkette bleibt sichtbar stehen."""
    raus, ausdruck, i, n = [], False, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c == "`":
            raus.append("_")
            i += 2
            continue
        if c not in _EINFACH and c not in _DOPPELT:
            raus.append(c)
            i += 1
            continue
        gruppe = _EINFACH if c in _EINFACH else _DOPPELT
        j = i + 1
        while j < n:
            if gruppe is _DOPPELT and cmd[j] == "`":
                j += 2
                continue
            if cmd[j] in gruppe:
                if j + 1 < n and cmd[j + 1] in gruppe:     # verdoppelt = Zeichen, kein Ende
                    j += 2
                    continue
                break
            j += 1
        if j >= n:
            raus.append(cmd[i:])
            break
        if gruppe is _DOPPELT and ("$(" in cmd[i:j] or "@(" in cmd[i:j]):
            ausdruck = True
        raus.append("''")
        i = j + 1
    return "".join(raus), ausdruck

_whitelist_cache: list[str] | None = None


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

WHITELIST_FASSUNG = 2
# Aus älteren Vorgaben entfernt: startet Programme (explorer x.exe) bzw. schreibt überallhin (copy-item).
NICHT_MEHR_HARMLOS = {2: ("explorer", "start-process explorer", "copy-item", "copy", "cp")}


def _fassung_anheben(daten: dict) -> None:
    """Einmal je Fassung die dort entfernten Einträge aus einer vorhandenen Datei nehmen; später von Hand
    wieder eingetragene bleiben."""
    alt = int(daten.get("fassung") or 1)
    if alt >= WHITELIST_FASSUNG or not isinstance(daten.get("befehle"), list):
        return
    weg = {e for f, eintraege in NICHT_MEHR_HARMLOS.items() if f > alt for e in eintraege}
    daten["befehle"] = [e for e in daten["befehle"] if " ".join(str(e).lower().split()) not in weg]
    daten["fassung"] = WHITELIST_FASSUNG
    WHITELIST_DATEI.write_text(json.dumps(daten, indent=2, ensure_ascii=False), encoding="utf-8")


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
                "fassung": WHITELIST_FASSUNG,
                "befehle": WHITELIST_VORGABE,
                "_hinweis_auto": "Zusätzliche Befehlsanfänge, die im Modus Auto ohne Rückfrage "
                                 "laufen. Alles andere hält auch in Auto an.",
                "auto_freigabe": AUTO_VORGABE}, indent=2, ensure_ascii=False), encoding="utf-8")
        daten = json.loads(WHITELIST_DATEI.read_text(encoding="utf-8"))
        if isinstance(daten, dict):
            try:
                _fassung_anheben(daten)
            except (OSError, TypeError, ValueError):
                pass                        # nicht schreibbar: im Speicher ist schon gefiltert
        roh = daten.get("befehle", []) if isinstance(daten, dict) else daten
        _whitelist_cache = sorted({" ".join(str(e).lower().split()) for e in roh if str(e).strip()})
    except (OSError, json.JSONDecodeError, AttributeError):
        _whitelist_cache = []
    return _whitelist_cache


def whitelist_neu_laden() -> None:
    global _whitelist_cache, _auto_cache
    _whitelist_cache = None
    _auto_cache = None


# ---------------------------------------------------------------------------
# Auto-Freigabe: was in ⚡/🚀 Auto ohne Rückfrage läuft
# ---------------------------------------------------------------------------
# Gästeliste statt Sperrliste: In Auto läuft, was harmlos ist, auf der Whitelist
# oder der Auto-Liste steht oder ein Python-Skript im Arbeitsordner startet. Jede
# andere riskante Aktion hält an und fragt – es gibt bewusst keine Liste
# gefährlicher Befehle, die gepflegt werden müsste.
AUTO_VORGABE = [
    "python -m pip install -r", "python -m pip install -e", "pip install -r", "pip install -e",
    "py -m pip install -r", "uv pip install -r", "uv add", "uv lock",
    "ruff check --fix", "ruff format", "black",
    "git stash", "git merge", "git cherry-pick", "git init", "git rebase --continue",
]
_PYTHON_NAMEN = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}

_auto_cache: list[str] | None = None


def auto_liste() -> list[str]:
    """Einträge unter "auto_freigabe" in befehl_whitelist.json; fehlt der Abschnitt: die Vorgabe."""
    global _auto_cache
    if _auto_cache is not None:
        return _auto_cache
    whitelist()                                     # legt die Datei bei Bedarf an
    try:
        daten = json.loads(WHITELIST_DATEI.read_text(encoding="utf-8"))
        roh = daten.get("auto_freigabe", AUTO_VORGABE) if isinstance(daten, dict) else AUTO_VORGABE
        _auto_cache = sorted({" ".join(str(e).lower().split()) for e in roh if str(e).strip()})
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        _auto_cache = []
    return _auto_cache


def arbeitsordner() -> Path:
    """Projekt des Coding-Assistenten, sonst der aktuelle Ordner."""
    try:
        import coding
        if (p := coding.projekt()):
            return Path(p).resolve()
    except Exception:
        pass
    return Path(os.getcwd()).resolve()


def _pfad_grund(roh) -> str | None:
    """actions.pfad_vorpruefen: Freigabe eines anderen Rechners oder Gerätepfad – vor jedem Dateizugriff."""
    try:
        import actions
        return actions.pfad_vorpruefen(roh)
    except ImportError:
        s = str(roh or "").strip().strip("\"'").replace("/", "\\")
        return "Netzwerkpfad" if s.startswith(("\\\\", "\\??\\")) else None


def _aktion_pfad_grund(act: dict) -> str | None:
    try:
        import actions
        return actions.aktion_vorpruefen(act)
    except ImportError:
        return next((g for f in _PFAD_FELDER + ("ordner", "projekt")
                     if isinstance(act.get(f), str) and (g := _pfad_grund(act[f]))), None)


def _im_ordner(roh: str, basis: Path) -> bool:
    if _pfad_grund(roh):
        return False
    try:
        p = Path(os.path.expandvars(roh)).expanduser()
        p = (p if p.is_absolute() else basis / p).resolve()
    except (OSError, ValueError):
        return False
    return p == basis or basis in p.parents


def _fremder_ort(p: Path) -> bool:
    """Sandbox, Entpackt- oder Daten-Ordner (pyumgebung.fremder_ort): dort legt auch Code der KI Dateien ab."""
    try:
        import pyumgebung
        return pyumgebung.fremder_ort(p) is not None
    except ImportError:
        return ist_entpackt_pfad(p)


def _python_vertraut(roh: str, basis: Path | None) -> bool:
    """Gilt der Programmname als „python“? Der bloße Name (PowerShell sucht ihn über PATH, nie im aktuellen Ordner)
    oder das venv des Projekts: …\\Scripts\\python.exe unter `basis`, nicht in Sandbox, Entpackt- oder Daten-Ordner."""
    roh = roh.strip("\"'")
    name = roh.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    if name not in _PYTHON_NAMEN:
        return False
    if roh.lower() == name:
        return True
    if basis is None or _pfad_grund(roh) or not _im_ordner(roh, basis):
        return False
    try:
        p = Path(os.path.expandvars(roh)).expanduser()
        p = (p if p.is_absolute() else basis / p).resolve()
    except (OSError, ValueError):
        return False
    return p.parent.name.lower() == "scripts" and not _fremder_ort(p)


def _python_normal(segment: str, basis: Path | None = None) -> str:
    """`& .\\venv\\Scripts\\python.exe -m pytest` → `python -m pytest` (nur das venv unter `basis`)."""
    teile = segment.strip().split()
    if teile and teile[0] == "&":
        teile = teile[1:]
    if teile and _python_vertraut(teile[0], basis):
        teile[0] = "python"
    return " ".join(teile)


def ausfuehrungsordner() -> Path | None:
    """Ordner, in dem `befehl` wirklich läuft (actions.befehl_ordner). None, wenn er dorthin umgeleitet wird
    (Sandbox, Profil): dort schreibt auch Code aus code_ausfuehren – ein Skript darin ist keins des Projekts."""
    try:
        import actions
        import coding
        gewuenscht = coding.arbeitsordner() or Path(os.getcwd())
        echt = actions.befehl_ordner(gewuenscht)
    except Exception:
        return None
    return Path(echt).resolve() if echt == gewuenscht else None


def _nur_abfrage(roh: str) -> bool:
    """Liest der Befehl nur (wie die Lese-Stufe in befehl_harmlos)?"""
    ohne_strings, ausdruck = _ohne_strings(roh)
    try:
        import actions
        return (not ausdruck and not actions.ausdruck_in_text(roh) and _STRINGS.sub("''", roh) == ohne_strings
                and actions._nur_lesend(roh) is None and actions._guard_command(roh, lesend=True) is None)
    except Exception:
        return False


def _auto_segment(segment: str, liste: list[str], basis: Path | None) -> bool:
    s = _python_normal(segment, basis)
    if _segment_passt(s, liste):
        return True
    teile = s.split()
    return (basis is not None and len(teile) >= 2 and teile[0] == "python" and teile[1].lower().endswith(".py")
            and _im_ordner(teile[1], basis) and (basis / teile[1]).resolve().is_file()
            and not _fremder_ort((basis / teile[1]).resolve()))


def auto_befehl_frei(cmd: str, basis: Path | None = None) -> bool:
    """Darf `befehl` in Auto ohne Rückfrage laufen? `basis`: Ordner, in dem er läuft (Vorgabe: der echte
    Ausführungsordner; wird er umgeleitet, startet Auto dort keine Skripte)."""
    roh = (cmd or "").strip()
    if not roh:
        return False
    if befehl_harmlos(roh):
        return True
    try:
        import actions
        if actions.ausdruck_in_text(roh):
            return False
    except Exception:
        return False
    ohne_strings, ausdruck = _ohne_strings(roh)
    if ausdruck or _UMLEITUNG.search(ohne_strings) or "(" in ohne_strings or _option_gefaehrlich(roh):
        return False
    liste = whitelist() + auto_liste()
    basis = basis or ausfuehrungsordner()
    return all(_auto_segment(seg, liste, basis) for seg in _TRENNER.split(ohne_strings))


def auto_grund(act: dict) -> str | None:
    """Warum eine Aktion auch in Auto anhält – None, wenn sie laufen darf."""
    act = _als_befehl(act)
    tool = act.get("tool", "")
    if tool == "updates_installieren":
        return "installiert Programme für den ganzen Rechner"
    if tool == "llamacpp_holen":
        return "lädt ein Programm aus dem Netz (llama.cpp)"
    if tool == "coding_start" and _neues_projekt(act):
        return "das Projekt wird der Ordner, in dem Auto ohne Rückfrage arbeitet"
    if tool == "befehl" and ausfuehrungsordner() is None and not _nur_abfrage(_befehl_text(act)):
        # Umgeleitet (Sandbox): Whitelist-Befehle wie pytest oder git commit führten dort Code aus, den
        # code_ausfuehren geschrieben haben kann.
        return "der Befehl liefe in der Sandbox statt in einem Projekt"
    if stufe(act) != RISKANT:
        return None
    basis = arbeitsordner()
    if tool == "befehl":
        return None if auto_befehl_frei(_befehl_text(act)) else "der Befehl steht nicht auf der Auto-Liste"
    if tool == "loeschen":
        pfad = str(act.get("pfad") or "")
        if _pfad_grund(pfad):
            return "der Pfad ist eine Netzwerkfreigabe oder ein Gerätepfad"
        try:
            ordner = Path(pfad).is_dir()
        except OSError:
            ordner = True
        if ordner:
            return "löscht einen ganzen Ordner"
        return None if pfad and _im_ordner(pfad, basis) else "die Datei liegt außerhalb des Arbeitsordners"
    if tool == "zeitplan":
        return "läuft künftig ohne dich im Hintergrund"
    if tool == "verschieben":
        drin = all(_im_ordner(str(act.get(f) or ""), basis) for f in ("von", "nach"))
        return None if drin else "verschiebt einen Ordner über den Arbeitsordner hinaus"
    return "riskante Aktion"


# ---------------------------------------------------------------------------
# Coding-Freigabe „frei“: im Projekt läuft alles, nur die Rote Liste fragt.
# Die harten Sperren (_guard_command, sperr_grund, Netz-Zwang, Persönlichkeiten,
# Programm-Ordner, Schlüssel) gelten unabhängig davon beim Ausführen.
# ---------------------------------------------------------------------------
_GIT_ROT = re.compile(r"(?i)\bgit\b[^;|&\r\n]*?\s(push|reset\s+--hard|clean|filter-branch|filter-repo|update-ref|"
                      r"reflog\s+(?:expire|delete)|gc\s+--prune|rebase(?!\s+--(?:continue|abort)))\b")
_SYSTEM_INSTALL = re.compile(r"(?i)(?<![\w-])(winget|choco|chocolatey|scoop|msiexec|npm|npx|pnpm|yarn)(?:\.exe)?\b")
_PIP_INSTALL = re.compile(r"(?i)(?:^|[;|&\s])&?\s*[\"']?([^\s\"';|&]*?)(pip3?(?:\.exe)?|python3?(?:\.exe)?\s+-m\s+pip|"
                          r"py(?:\.exe)?\s+-m\s+pip)[\"']?\s+install\b")
# Python-Code, der schreibt oder löscht (Pfade außerhalb des Projekts fragen dann)
_PY_AENDERT = re.compile(r"(?i)\b(rmtree|os\.remove|os\.unlink|os\.rmdir|\.unlink\(|\.rmdir\(|write_text|write_bytes|"
                         r"shutil\.(?:copy\w*|move)|os\.rename|os\.replace|send2trash)\b|open\([^)]*[\"'][wax]")


def _ausserhalb_projekt_im_befehl(cmd: str) -> str | None:
    """Ein absoluter Pfad im Befehl, der nicht im Coding-Projekt liegt – sonst None."""
    try:
        import actions
        import coding
        _t, orte, verdaechtig = actions._befehl_orte(cmd)
    except Exception:
        return None
    if verdaechtig:
        return verdaechtig
    for ort in filter(None, orte.split("\n")):
        if not coding.drin(ort):
            return ort
    return None


def coding_grund(act: dict) -> str | None:
    """Rote Liste der Coding-Freigabe „frei“: warum diese Aktion trotzdem fragt – None, wenn sie läuft."""
    act = _als_befehl(act)
    tool = act.get("tool", "")
    if tool == "updates_installieren":
        return "installiert Programme für den ganzen Rechner"
    if tool == "llamacpp_holen":
        return "lädt ein Programm aus dem Netz (llama.cpp)"
    if tool == "zeitplan" and stufe(act) == RISKANT:
        return "läuft künftig ohne dich im Hintergrund"
    if tool == "coding_start" and _neues_projekt(act):
        return "das Projekt wird der Ordner, in dem NemiCLI frei arbeitet"
    if tool == "loeschen":
        pfad = str(act.get("pfad") or "")
        if _pfad_grund(pfad):
            return "der Pfad ist eine Netzwerkfreigabe oder ein Gerätepfad"
        try:
            if Path(pfad).is_dir():
                return "löscht einen ganzen Ordner"
        except OSError:
            return "löscht einen ganzen Ordner"
        return None
    if tool != "befehl":
        return None
    cmd = _befehl_text(act)
    if ausfuehrungsordner() is None and not _nur_abfrage(cmd):
        return "der Befehl liefe in der Sandbox statt im Projekt"
    if (m := _GIT_ROT.search(cmd)):
        return f"git {m.group(1).split()[0]} ändert das Repository dauerhaft oder schickt es weg"
    if (m := _SYSTEM_INSTALL.search(cmd)):
        return f"{m.group(1)} installiert etwas für den ganzen Rechner"
    if (m := _PIP_INSTALL.search(cmd)):
        praefix, prog = m.group(1), m.group(2).split()[0]
        exe = praefix + ("python.exe" if prog.lower().startswith("pip") else prog)
        if not praefix or not _python_vertraut(exe, ausfuehrungsordner()):   # bloßes pip/python: das des Rechners
            return "pip installierte ins Python des Rechners statt ins venv des Projekts"
    try:
        import actions
        if actions._NETZ_BEFEHL.search(cmd):
            return "der Befehl lädt etwas aus dem Netz"
        loescht = actions._DESTRUCTIVE.search(cmd)
        if loescht and actions._REKURSIV.search(cmd) and re.search(r"(?i)\b(remove-item|ri|rm|rmdir|rd|del|erase)\b",
                                                                    cmd):
            return "löscht einen ganzen Ordner"
    except Exception:
        return "der Befehl ließ sich nicht prüfen"
    if (loescht or _PY_AENDERT.search(cmd)) and (draussen := _ausserhalb_projekt_im_befehl(cmd)):
        return f"ändert etwas außerhalb des Projekts ({draussen})"
    return None


def _neues_projekt(act: dict) -> bool:
    """Setzt coding_start ein anderes Projekt als das laufende? Im Zweifel ja."""
    try:
        import coding
        neu, _fehler = coding.pruefe_projekt(str(act.get("projekt") or ""))
        alt = coding.projekt()
        return neu is None or alt is None or neu != alt.resolve()
    except Exception:
        return True


def _auto_vorsatz(act: dict) -> str:
    try:
        import coding
        if coding.frei():                            # Coding-Freigabe „frei“: nur die Rote Liste fragt
            grund = coding_grund(act)
            return f"🟦 Coding frei hält an: {grund}.\n" if grund else ""
        import modes
        if modes.current() not in modes.AUTO_MODES:
            return ""
    except Exception:
        return ""
    grund = auto_grund(act)
    return f"⚡ Auto hält an: {grund}.\n" if grund else ""


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
        rest = s[len(eintrag):] if s.startswith(eintrag) else None
        if rest is None:
            continue
        # Angehängt zählt nur ".exe": notepad.cmd oder code.bat wären beliebige Skripte gleichen Namens.
        if rest[:1] in ("", " ", ";") or (rest[:4] == ".exe" and rest[4:5] in ("", " ", ";")):
            return True
    return False


def befehl_harmlos(cmd: str) -> bool:
    """True, wenn der PowerShell-Befehl nur liest oder komplett aus
    Whitelist-Befehlen besteht (ohne Umleitung in Dateien)."""
    roh = (cmd or "").strip()
    if not roh:
        return False
    ohne_strings, ausdruck = _ohne_strings(roh)
    try:
        import actions
        if ausdruck or actions.ausdruck_in_text(roh):          # "$(…)" führt einen Befehl aus
            return False
        # Die Lese-Prüfung trennt nur an ASCII-Anführungszeichen – bei abweichender Trennung gilt die Whitelist.
        if (_STRINGS.sub("''", roh) == ohne_strings and actions._nur_lesend(roh) is None
                and actions._guard_command(roh, lesend=True) is None):
            return True
    except Exception:
        return False
    if _UMLEITUNG.search(ohne_strings) or _option_gefaehrlich(roh):
        return False
    liste = whitelist()
    if not liste:
        return False
    # Eine Klammer in den Argumenten ( (…), $(…), @(…) ) führt einen Befehl aus –
    # „mkdir $(…)“ ist kein harmloses mkdir mehr.
    if "(" in ohne_strings:
        return False
    return all(_segment_passt(seg, liste) for seg in _TRENNER.split(ohne_strings))


# ---------------------------------------------------------------------------
# Risikostufen
# ---------------------------------------------------------------------------

LESEN, HARMLOS, AENDERT, RISKANT = "lesen", "harmlos", "aendert", "riskant"

_HARMLOS = {"ordner_erstellen", "bildschirm_ansehen", "merken", "skill_merken",
            "ordner_lernen", "bild_malen", "bild_serie", "comic_malen", "tagesrueckblick", "musik_machen", "musik_cover", "wache_bewerten", "wache_justieren", "kugel",
            "kugel_anziehen", "erinnern", "figur_bewegen", "figur_justieren"}
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
    if tool == "loeschen" or _aktion_pfad_grund(act):     # Netzwerkpfad: ohne Blick ins Dateisystem riskant
        return RISKANT
    if tool == "befehl":
        return AENDERT if befehl_harmlos(_befehl_text(act)) else RISKANT
    if tool == "zeitplan":                  # öffnet nur das Zeitplan-Fenster; eintragen macht der Nutzer per Klick
        return HARMLOS
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
# Gilt für jede Persönlichkeit. Bedingungen, alle zugleich:
#   • schreibendes Datei-Werkzeug, Stufe harmlos/ändert (loeschen und Ordner-
#     Verschieben bleiben riskant → fragen weiter)
#   • JEDER Pfad der Aktion liegt im Daten-Ordner (paths.DATEN), und der ist
#     NICHT der Programm-Ordner (sonst würde die Programm-Sperre ausgehebelt)
#   • kein Netz-Taint in dieser Runde (eine Webseite darf nicht „bitte schreib
#     mir Persoenlichkeiten/…“ durchreichen)
# Rückholbar bleibt alles über den Papierkorb (/undo), jede Aktion steht im Protokoll.
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
            kandidaten = actions._kandidaten(roh)     # wie Windows öffnet: "Skills.\x" ist Skills\x
            if not kandidaten:
                return False
            for p in kandidaten:
                if p != daten and daten not in p.parents:
                    return False
                if p == skills or skills in p.parents:
                    return False
                if profile in p.parents and len(p.relative_to(profile).parts) >= 2 \
                        and p.relative_to(profile).parts[1].casefold() == "skills":   # Windows: Groß/Klein egal
                    return False
        if steuerdatei_grund(act):
            return False
        return actions._guard(*pfade) is None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Wächter für steuernde Dateien im Daten-Ordner
# ---------------------------------------------------------------------------
# Manche Dateien steuern die KI selbst oder laufen später ohne den Nutzer:
#   Persoenlichkeiten/*.md (ohne .sprueche.md), Agenten/*.md – der Wächter liest die Änderung
#     mit den Regeln des Skill-Wächters; harmlos → Stift, sonst Rückfrage mit Grund.
#   Zeitplan/, Befehle/ – immer Rückfrage (läuft von allein bzw. ist ein Programm).
# Gilt in jedem Modus (modes.decide), „Immer“ gibt es dafür nicht.
_STEUERND_IMMER = ("zeitplan", "befehle")
# Wache/ (Stopp-Datei, Modelle, Normalbild) und learned/ (Gedächtnis, Aktions-Protokoll):
# wer dort Dateien schreibt, schaltet die Wache ab oder umgeht die Filter von merken.
_WACHE_UND_PROTOKOLL = ("wache", "learned")
_WACHE_FREI = {("wache", "gruesse.md")}       # die Grüße der Kugel schreibt die Persönlichkeit selbst
_STEUERND_TEXT = ("persoenlichkeiten", "agenten")
_SICHERUNGEN = ("backup",)                     # Sicherungen (auch Punkt B der Charakter-Dateien)
_MIN_REST = 0.4


def _neuer_text(act: dict, p: Path) -> str | None:
    """Inhalt nach der Änderung – None, wenn er sich nicht vorher bestimmen lässt."""
    tool = act.get("tool")
    if tool == "datei_schreiben":
        return str(act.get("inhalt") or "")
    if tool == "datei_bearbeiten":
        try:
            alt = p.read_text(encoding="utf-8")
        except OSError:
            alt = ""
        return alt.replace(str(act.get("alt") or ""), str(act.get("neu") or ""), 1)
    return None


def _waechter_text(text: str, p: Path) -> str | None:
    import skillwaechter
    if (grund := skillwaechter._inhalt(text) or skillwaechter.riskante_aktion(text)):
        return grund
    try:
        alt = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        alt = ""
    if len(alt) >= 200 and len(text.strip()) < len(alt) * _MIN_REST:
        return "würde die Datei fast leeren"
    return None


def _kern_grund(act: dict) -> str | None:
    """charakter_aendern am gesperrten Kern: der Nutzer entscheidet (in jedem Modus)."""
    try:
        import charakter
        return charakter.kern_aenderung(act)
    except Exception:
        return "der Kern ließ sich nicht prüfen"


def steuerdatei_grund(act: dict) -> str | None:
    """Warum eine Änderung im Daten-Ordner trotz Stift fragen muss – oder None."""
    if act.get("tool") == "charakter_aendern":
        return _kern_grund(act)
    if act.get("tool") not in _ARBEITSBEREICH_TOOLS:
        return None
    try:
        import paths
        import actions
        daten = Path(paths.DATEN).resolve()
        pfade = [str(act.get(f)) for f in _PFAD_FELDER if isinstance(act.get(f), str) and act.get(f).strip()]
        for roh in pfade:
            for p in actions._kandidaten(roh) or []:
                if not p.is_relative_to(daten) or p == daten:
                    continue
                erster = p.relative_to(daten).parts[0].lower()
                if erster == "profile" and p.name.lower().endswith("_kern.json"):
                    return "die Kern-Datei hält das Aussehen der Figur – ändern entscheidest du"
                if erster == "profile" and p.name.lower() == "selbstentwurf.json":
                    return "den Selbstentwurf ändert die KI nur mit dem Werkzeug selbstentwurf – sonst entscheidest du"
                if erster in _STEUERND_IMMER:
                    return f"{p.relative_to(daten).parts[0]}/ läuft später von allein – das entscheidest du"
                if erster in _SICHERUNGEN:
                    return f"{p.relative_to(daten).parts[0]}/ hält die Sicherungen – Änderungen dort entscheidest du"
                if erster in _WACHE_UND_PROTOKOLL \
                        and tuple(t.lower() for t in p.relative_to(daten).parts) not in _WACHE_FREI:
                    return (f"{p.relative_to(daten).parts[0]}/ gehört der Wache bzw. dem Gedächtnis und "
                            "Protokoll – Änderungen dort entscheidest du")
                if erster in _STEUERND_TEXT and p.suffix.lower() == ".md" \
                        and not p.name.lower().endswith(".sprueche.md"):
                    if (text := _neuer_text(act, p)) is None:
                        return "eine Anleitung wird hierher kopiert oder verschoben – den Inhalt prüft der Wächter so nicht"
                    if (grund := _waechter_text(text, p)):
                        return f"die Änderung an {p.name} {grund}"
    except Exception:
        return "die Änderung ließ sich nicht prüfen"
    return None


# ---------------------------------------------------------------------------
# Der Schlüssel: Programm-Ordner auf Zeit, pro Aufgabe
# ---------------------------------------------------------------------------
# Der Programm-Ordner bleibt zu, damit Änderungen am Programm nie still geschehen. Der Nutzer
# gibt mit /schluessel <minuten> [aufgabe] auf Zeit frei; jede Änderung fragt trotzdem einzeln (F8).
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
    if _coding_frei_an():                  # Coding frei: was dann noch fragt, steht auf der Roten Liste – einmal
        return False
    return act.get("tool") == "befehl" and stufe(act) == RISKANT


def _coding_frei_an() -> bool:
    try:
        import coding
        return coding.frei()
    except Exception:
        return False


def frage(act: dict) -> str:
    """Die Frage über den Optionen – bei Risiko unübersehbar."""
    act = _als_befehl(act)
    if act.get("tool") == "todo":
        import coding
        return coding.frage_und_optionen(act)[0]
    return _auto_vorsatz(act) + _frage_text(act)


def _frage_text(act: dict) -> str:
    s = stufe(act)
    if s == RISKANT:
        tool = act.get("tool")
        if tool == "loeschen":
            return "⚠ RISIKO – löschen. Landet im Papierkorb (/undo). Wie weiter?"
        if tool == "befehl":
            if _coding_frei_an() and coding_grund(act):
                return "⚠ Wie weiter?"              # der Grund steht schon darüber (Rote Liste)
            return "⚠ RISIKO – Befehl steht NICHT auf der Whitelist. Wie weiter?"
        if tool == "zeitplan":
            return "⚠ RISIKO – läuft künftig OHNE dich im Hintergrund. Wie weiter?"
        return "⚠ RISIKO – ganzer Ordner. Wie weiter?"
    if (grund := _netz_grund(act)):
        return f"🛡 Netz-Sperre: {grund}. Trotzdem ausführen?"
    if act.get("tool") == "entpacken":
        return ("📦 Die KI möchte ein Archiv entpacken – ein Fenster zeigt den Fortschritt, jede Datei wird geprüft, "
                "nichts wird gestartet. Entpacken?")
    if act.get("tool") == "web_lesen" and _verdaechtig(act):
        return "⚠ Diese Seite hat schon einmal versucht, die KI zu steuern. Trotzdem laden?"
    if act.get("tool") == "web_lesen" and not _fremd_frei(act):
        return ("Unbekannte Seite, die aus keinem Suchtreffer stammt – über die Adresse könnten Daten "
                "nach draußen gehen. Prüf die Adresse. Laden?" if not _fremd_fragen()
                else "Unbekannte Seite außerhalb der Allowlist (Fremdseiten: fragen). Laden?")
    if (grund := steuerdatei_grund(act)):
        return f"🛡 Wächter: {grund}. Trotzdem ausführen?"
    return "Wie möchtest du fortfahren?"


def _fremd_frei(act: dict) -> bool:
    try:
        import webfetch
        return webfetch.fremd_ohne_nachfrage(str(act.get("url") or ""))
    except Exception:
        return False


def _fremd_fragen() -> bool:
    try:
        import webfetch
        return webfetch.fremdseiten_modus() == "fragen"
    except Exception:
        return False


def _verdaechtig(act: dict) -> bool:
    try:
        import webfetch
        return webfetch.verdaechtig(str(act.get("url") or ""))
    except Exception:
        return False


def _netz_grund(act: dict) -> str | None:
    try:
        import actions
        return actions.fremd_grund(act)
    except Exception:
        return None


def optionen(act: dict) -> list[tuple[str, str]]:
    """Antwortmöglichkeiten. 'Immer' nur bei harmlosen und ändernden Aktionen –
    nie bei Risiko, nie bei `befehl` (das wäre 'beliebiges PowerShell ohne Frage')
    und nie bei `todo` (Liste und Prüfung entscheidet der Nutzer jedes Mal)."""
    act = _als_befehl(act)
    if act.get("tool") == "todo":
        import coding
        return coding.frage_und_optionen(act)[1]
    s = stufe(act)
    if (s == RISKANT or act.get("tool") in ("befehl", "web_lesen", "coding_start", *NUR_VORDERGRUND)
            or steuerdatei_grund(act)
            or _netz_grund(act)):
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
    if (grund := _pfad_grund(act.get("pfad"))):
        return grund
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
    if _pfad_grund(str(pfad)):
        return 1
    p = Path(pfad)
    try:
        if p.is_dir():
            return max(1, _dateien_in(p, grenze))
    except OSError:
        pass
    return 1


# ---------------------------------------------------------------------------
# Entpacken: nur im Vordergrund, nichts aus Entpackt-Ordnern ausführen
# ---------------------------------------------------------------------------
# Gilt in jedem Modus und vor jeder Rückfrage (modes.decide → block) und noch einmal in actions.run.
NUR_VORDERGRUND = {"entpacken"}
# Prozesse ohne Gespräch im Vordergrund (main_start.py): Zeitplan-Auftrag, Wache samt Kugel und Weckruf,
# Kindprozesse für Bilder, Gedächtnis und das Entpack-Fenster selbst. --sag ist ein normales Gespräch.
HINTERGRUND_SCHALTER = ("--wache", "--vollscan", "--auftrag", "--selftest", "--bild", "--systemcheck",
                        "--kontext", "--malkind", "--bibliothekar", "--vorpruefer-lernen", "--entpacken")
# Werkzeuge, die Programme oder Code starten.
_AUSFUEHRENDE = {"befehl", "code_ausfuehren", "paket_installieren"}
# Ordnername wie in entpacken.ziel_fuer_chat/ziel_fuer_profil, dazu entpackt_* und der 8.3-Kurzname.
_ENTPACKT_NAME = re.compile(r"(?i)^(entpackt(_.*)?|entpac~\d+)$")
_ENTPACKT_TEIL = r"(?:entpackt(?:_[^\\/\s\"'`;|&)]*)?|entpac~\d+)"
_TRENN = r"[\\/]"
_ENTPACKT_IM_TEXT = re.compile(
    rf"(?i)(?:{_TRENN}{_ENTPACKT_TEIL}(?=$|[\\/\s\"'`;|&),])"           # …\Entpackt  …\Entpackt\x.exe
    rf"|(?<![\w.~-]){_ENTPACKT_TEIL}{_TRENN}"                              # Entpackt\x.exe
    rf"|(?:^|[\s;(&|])(?:cd|sl|chdir|pushd|set-location|push-location)\s+[\"']?{_ENTPACKT_TEIL}(?![\w~-]))")

_helfer_kontext = contextvars.ContextVar("sicherheit_helfer", default=False)


@contextlib.contextmanager
def als_helfer():
    """Markiert alles darin (auch Threads aus asyncio.to_thread) als Arbeit eines Helfer-Agenten."""
    token = _helfer_kontext.set(True)
    try:
        yield
    finally:
        _helfer_kontext.reset(token)


def vordergrund_grund() -> str | None:
    """Warum gerade niemand im Vordergrund sitzt – None im normalen Gespräch (Terminal, Fenster, Browser)."""
    import sys
    if (schalter := next((a for a in sys.argv[1:] if a in HINTERGRUND_SCHALTER), None)):
        return f"Hintergrund-Prozess ({schalter})"
    if _helfer_kontext.get():
        return "Helfer-Agent"
    try:
        import modes
        if modes._wache_dienst or modes._kugel_runde:
            return "Wache"
    except ImportError:
        pass
    try:
        import vonsichaus
        if vonsichaus.in_runde():
            return "freier Moment"
    except ImportError:
        pass
    return None


def _entpacken_modus() -> str:
    try:
        import entpacken
        return entpacken.modus()
    except Exception:
        return "aus"


def ist_entpackt_pfad(pfad: str | Path) -> bool:
    """Liegt der Pfad in einem Entpackt-Ordner (DATEN/Entpackt/…, Profile/<Name>/Entpackt/…, entpackt_*)?"""
    roh = str(pfad or "").strip().strip('"')
    if not roh:
        return False
    teile = list(Path(roh).parts)
    if not _pfad_grund(roh):                       # resolve() auf einer Freigabe schickte die Anmeldung dorthin
        try:
            teile += list(Path(os.path.expandvars(roh)).expanduser().resolve().parts)
        except (OSError, ValueError):
            pass
    return any(_ENTPACKT_NAME.match(t) for t in teile)


def _arbeitsordner_entpackt(tool: str) -> bool:
    orte: list = [os.getcwd()]
    try:
        import coding
        orte += [coding.projekt(), coding.arbeitsordner()]
    except Exception:
        pass
    if tool == "code_ausfuehren":                  # freigegebene Projekte liegen auf dem Python-Pfad der Sandbox
        try:
            import sandbox
            orte += sandbox.projekte()
        except Exception:
            pass
    return any(o and ist_entpackt_pfad(o) for o in orte)


def entpackt_grund(act: dict) -> str | None:
    """Werkzeuge, die etwas starten, dürfen nichts aus einem Entpackt-Ordner ausführen. Lesen bleibt frei."""
    act = _als_befehl(act)
    tool = act.get("tool", "")
    if tool == "oeffnen":                     # Standardprogramme führen Makros und Skripte in Dokumenten aus
        pfad = str(act.get("pfad") or "").strip().strip('"')
        if pfad and ist_entpackt_pfad(pfad) and (_pfad_grund(pfad) or not Path(pfad).is_dir()):
            return "die Datei liegt in einem Entpackt-Ordner (nur der Ordner lässt sich öffnen)"
        return None
    if tool not in _AUSFUEHRENDE:
        return None
    if _arbeitsordner_entpackt(tool):
        return "der Arbeitsordner liegt in einem Entpackt-Ordner"
    werte = [v for k, v in act.items() if k != "tool"]
    texte = [str(x) for v in werte for x in (v if isinstance(v, (list, tuple)) else [v]) if isinstance(x, str)]
    if any(_ENTPACKT_IM_TEXT.search(t) for t in texte):
        return "der Aufruf zeigt in einen Entpackt-Ordner"
    pfad = act.get("pfad")
    if isinstance(pfad, str) and ist_entpackt_pfad(pfad):           # auch relativ oder als Kurzname
        return "das Skript liegt in einem Entpackt-Ordner"
    return None


def sperr_grund(act: dict) -> str | None:
    """Harte Sperren vor jeder Rückfrage, in jedem Modus – None, wenn keine greift."""
    tool = act.get("tool", "")
    if (grund := _aktion_pfad_grund(act)):              # vor allem anderen: nichts davon fasst das Dateisystem an
        return grund
    try:                                                # Persönlichkeits-Datei: ändert nur der Nutzer
        import actions
        if (grund := actions.charakter_md_vorab(act)):
            return grund
    except ImportError:
        pass
    if tool in NUR_VORDERGRUND:
        if (wo := vordergrund_grund()):
            return (f"Abgelehnt: '{tool}' geht nur im Gespräch mit dem Nutzer, nicht hier ({wo}). "
                    "Nicht erneut versuchen – sag dem Nutzer, was du entpacken wolltest.")
        if _entpacken_modus() == "aus":
            return "Abgelehnt: /zip ist aus – Entpacken ist abgeschaltet. Nur der Nutzer schaltet es mit /zip an."
    if (grund := entpackt_grund(act)):
        return (f"Abgelehnt: {grund}. Aus Entpackt-Ordnern startet die KI keine Programme, Skripte oder "
                "Installationen. Ansehen geht mit datei_lesen, ordner_auflisten und den Such-Werkzeugen.")
    return None
