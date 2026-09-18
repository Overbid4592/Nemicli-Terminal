"""
actions.py - Was NemiCLI wirklich tun kann (mit Bestätigung).

Damit es lokal UND in der Cloud gleich funktioniert, nutzen wir KEIN
modell-spezifisches Tool-Format, sondern ein einfaches Text-Protokoll:
Das Modell schreibt einen Block

    ```aktion
    {"tool": "datei_lesen", "pfad": "C:\\\\...\\\\main.py"}
    ```

Wir lesen den Block aus, fragen (bei verändernden Aktionen) den Nutzer,
führen aus und geben das Ergebnis ans Modell zurück.

Lesende Werkzeuge laufen ohne Nachfrage, verändernde immer erst nach "Ja".
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import foldersense
import learn
import memory
import pdfgen
import webfetch

MAX_OUT = 6000  # längere Ausgaben werden gekürzt (Token sparen)


@dataclass(frozen=True)
class ActionResult:
    """Werkzeugstatus getrennt von frei formulierter oder fremder Ausgabe.

    ``ok=None`` bedeutet: die Quelle liefert keinen prüfbaren Erfolgsstatus.
    Insbesondere darf der Inhalt einer Datei oder Webseite den Status nicht setzen.
    """

    text: str
    ok: bool | None = True
    returncode: int | None = None
    bilder: list | None = None      # Bildpfade, die das Modell in der nächsten Runde SIEHT

    @property
    def status(self) -> str:
        return "unverified" if self.ok is None else ("success" if self.ok else "error")


def _cut(text: str) -> str:
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n… (gekürzt)"


# --- Fortschritts-Kanal fürs Bildmalen -------------------------------------
# Die Aktion läuft in einem Hintergrund-Thread (actions.run -> to_thread), die
# Live-Anzeige aber im Haupt-Loop. Über dieses kleine Fach reicht das Malen
# seinen Fortschritt ("male … Schritt 12/30") an die Anzeige zurück, die es im
# Takt ausliest und als Ladebalken zeigt.
_BILD_STATUS: dict = {"msg": None}


def bild_status() -> str | None:
    """Aktuelle Fortschritts-Meldung des Bildmalens (oder None, wenn gerade
    nichts läuft). Wird vom Haupt-Loop gepollt."""
    return _BILD_STATUS["msg"]


# ---------------------------------------------------------------------------
# SCHUTZ: Windows-/Systemordner sind für die KI KOMPLETT tabu.
# ---------------------------------------------------------------------------
# Regel vom Nutzer (12.09.2026): „Da soll die KI erst gar nicht ran." Verändernde
# Werkzeuge prüfen über _guard(), lesende über _read_guard(), Befehle über
# _guard_command() – jeder Zielpfad wird gegen diese Liste geprüft, Pfade werden
# vorher aufgelöst (…/.., Links, \\?\-Präfix), damit kein Umweg
# (C:\Users\..\..\Windows) daran vorbeiführt.

def _protected_bases() -> list[Path]:
    sysdrive = (os.environ.get("SystemDrive") or "C:").rstrip("\\") + "\\"
    cands = [
        os.environ.get("SystemRoot"), os.environ.get("windir"),
        os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"), os.environ.get("ProgramData"),
        r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
        r"C:\ProgramData",
        # Nachgetragen: Ordner, an denen genauso wenig herumgeschrieben werden darf.
        # Windows.old enthält eine komplette alte Installation, Recovery die
        # Wiederherstellung, EFI/Boot den Startvorgang – alles Totalschaden-Kandidaten.
        sysdrive + "Windows.old",
        sysdrive + "Recovery",
        sysdrive + "System Volume Information",
        sysdrive + "$Recycle.Bin",
        sysdrive + "Boot",
        sysdrive + "EFI",
        sysdrive + "PerfLogs",
        os.path.join(sysdrive, "Users", "Default"),
        os.path.join(sysdrive, "Users", "Public", "Desktop"),
    ]
    bases: list[Path] = []
    for c in cands:
        if not c:
            continue
        try:
            r = Path(c).resolve()
        except Exception:
            continue
        if r not in bases:
            bases.append(r)
    return bases


def _entwirre(path: str) -> str:
    """Räumt einen Pfad auf, BEVOR er geprüft wird.

    Ohne das kommt man an der Sperre vorbei: `\\\\?\\C:\\Windows\\…` ist für
    Windows derselbe Ort wie `C:\\Windows\\…`, sieht für einen simplen
    Text-Vergleich aber ganz anders aus. Genauso Anführungszeichen und
    Umgebungsvariablen.
    """
    roh = os.path.expandvars(str(path or "")).strip().strip('"').strip("'")
    for prefix in ("\\\\?\\UNC\\", "\\\\?\\", "\\\\.\\"):
        if roh.upper().startswith(prefix.upper()):
            roh = roh[len(prefix):]
            break
    return roh


def _is_protected(path: str) -> bool:
    """True, wenn der Pfad in einem geschützten Windows-/Systemordner liegt –
    oder direkt im Laufwerks-Wurzelverzeichnis (z.B. C:\\etwas)."""
    roh = _entwirre(path)
    if not roh:
        return True
    try:
        p = Path(roh).expanduser().resolve()
    except Exception:
        return True                      # im Zweifel: blockieren
    pl = str(p).lower()
    for base in _protected_bases():
        bl = str(base).lower()
        if pl == bl or pl.startswith(bl + os.sep):
            return True
    # Schreiben/Anlegen direkt in der Laufwerks-Wurzel (C:\ipsum) ebenfalls sperren.
    if p.parent == Path(p.anchor):
        return True
    return False


# --- Eigene Sperren des Nutzers ---------------------------------------------
# Oben ging es um Windows. Hier geht es um Orte, die dem Nutzer gehören, an
# denen die KI aber nichts zu suchen hat. Anlass war der 15.09.2026: beim
# Einrichten des Musicnemi-Workspace kam ein Audio-VAE dazu, und das Bilder-
# malen fiel aus, weil NemiCLI seinen VAE per "nimm den ersten" wählte.
# Kaputtgeschrieben war nichts – der Nutzer will diese Orte trotzdem
# grundsätzlich aus der Hand der KI. Zwei Stufen:
#
#   ABSOLUT  – der NemiCLI-Ordner. Genau wie C:\Windows: nicht ändern und
#              auch nicht ansehen. Damit kann sich NemiCLI nicht mehr selbst
#              umbauen, und Lara liest ihren eigenen Quelltext nicht mehr.
#              Das ist gewollt und ausdrücklich so bestellt.
#   ÄNDERN   – KreaWork.json. Ansehen ist erlaubt (Lara soll Fragen zum
#              Workflow beantworten können), Schreiben nicht.
#
# Beide Stufen decken auch das `befehl`-Werkzeug ab, sonst ginge ein
# PowerShell-Einzeiler mit Set-Content daran vorbei.
#
# Erweitern ohne Codeänderung: schreibsperre.json im NemiCLI-Ordner,
#     {"pfade": ["…nur-lesen.json"], "pfade_absolut": ["…komplett-tabu"]}
# ACHTUNG: Diese Datei liegt selbst im NemiCLI-Ordner und ist damit für die
# KI unerreichbar – ändern kann sie nur der Nutzer von Hand.

_NEMI_ROOT = Path(__file__).resolve().parent.parent

# Stufe 1: komplett tabu – weder lesen noch ändern.
_ABSOLUT_FEST = [
    _NEMI_ROOT,                                   # NemiCLI selbst
]

# ... ABER: der Daten-Ordner ist NICHT das Programm.
#
# Solange der Nutzer noch keinen eigenen Ordner gewählt hat (/start), liegen
# seine Daten IM Programm-Ordner - chats/, Bilder/, NemiSandbox/ und der Rest.
# Ohne diese Ausnahme sperrt die Programm-Sperre genau das mit weg, was sie
# gar nicht meint: NemiCLI könnte dann nicht einmal mehr in ihre eigene
# Sandbox schreiben (der Selbsttest fiel deshalb durch).
#
# Sobald der Daten-Ordner woanders liegt, greift die Ausnahme ins Leere und
# die Sperre umfasst wirklich nur noch das Programm - so soll es am Ende sein.


def _daten_bereiche() -> list[Path]:
    """Die Ordner/Dateien des Nutzers unter dem Daten-Ordner (paths.DATEN_INHALT).

    Bewusst NICHT der Daten-Ordner als Ganzes: wäre er mit dem Programm-Ordner
    identisch, würde die Sperre sich damit selbst aufheben.
    """
    try:
        import paths                            # erst hier - tools/ lädt auch ohne
    except Exception:
        return []
    try:
        return [(paths.DATEN / n).resolve() for n in paths.DATEN_INHALT]
    except Exception:
        return []

# Stufe 2: ansehen ja, ändern nein.
_NUR_LESEN_FEST = [
    Path(os.path.expandvars(                      # der Krea-Workflow
        r"%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
        r"\user\default\workflows\KreaWork.json")),
]


def _saubere_pfade(roh: list) -> list[Path]:
    """Auflösen und doppelte wegwerfen – unauflösbare fliegen raus."""
    raus: list[Path] = []
    for b in roh:
        try:
            r = Path(b).expanduser().resolve()
        except Exception:
            continue
        if r not in raus:
            raus.append(r)
    return raus


def _sperrliste() -> dict:
    """Nachträge aus schreibsperre.json – fehlt sie, bleibt es bei der festen Liste."""
    try:
        d = json.loads((_NEMI_ROOT / "schreibsperre.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _absolut_bases() -> list[Path]:
    """Alles, was die KI nicht einmal ansehen darf."""
    roh = list(_ABSOLUT_FEST)
    for e in _sperrliste().get("pfade_absolut", []) or []:
        roh.append(os.path.expandvars(str(e)))
    return _saubere_pfade(roh)


def _schreibsperre_bases() -> list[Path]:
    """Alles, was die KI nicht ändern darf – das Absolute gehört dazu."""
    roh = list(_ABSOLUT_FEST) + list(_NUR_LESEN_FEST)
    for e in _sperrliste().get("pfade_absolut", []) or []:
        roh.append(os.path.expandvars(str(e)))
    for e in _sperrliste().get("pfade", []) or []:
        roh.append(os.path.expandvars(str(e)))
    return _saubere_pfade(roh)


def _nur_daten_gemeint(base: Path, norm: str) -> bool:
    """Meint JEDE Nennung von `base` in diesem Befehl einen Daten-Bereich darunter?

    Zwei Löcher, die dabei zugehen mussten:

      `Copy-Item <Bilder>\\a.png <KreaWork.json>`
          nennt einen Daten-Ordner - gemeint ist trotzdem eine gesperrte Datei.
          Deshalb zählen nur Daten-Bereiche, die unter GENAU dieser Wurzel liegen.

      `Copy-Item <Bilder>\\a.png <NemiCLI>\\main.py`
          nennt die Wurzel zweimal: einmal als Daten-Ordner, einmal als Programm.
          Deshalb reicht "kommt vor" nicht - jede einzelne Fundstelle muss in
          einen Daten-Bereich weiterlaufen. Eine, die das nicht tut, genügt zum
          Sperren.
    """
    bl = str(base).lower().replace("/", "\\")
    unter_base = [s for s in (str(f).lower().replace("/", "\\")
                              for f in _daten_bereiche())
                  if s == bl or s.startswith(bl + os.sep)]
    if not unter_base:
        return False
    i = norm.find(bl)
    if i == -1:
        return False
    while i != -1:
        if not any(norm.startswith(s, i) for s in unter_base):
            return False                  # diese Nennung meint das Programm
        i = norm.find(bl, i + 1)
    return True


def _liegt_unter(path: str, bases: list[Path]) -> Path | None:
    r"""Liegt der Pfad auf oder unter einer der Wurzeln? Dann diese Wurzel.

    Geht durch dasselbe _entwirre() wie die Systemsperre, damit weder ..\..\
    noch \?\ noch %VAR% daran vorbeiführen.
    """
    roh = _entwirre(path)
    if not roh:
        return None
    try:
        p = Path(roh).expanduser().resolve()
    except Exception:
        return None                               # Systemsperre greift ohnehin
    pl = str(p).lower()
    # Die Daten des Nutzers stechen die Sperre - siehe _daten_bereiche().
    for frei in _daten_bereiche():
        fl = str(frei).lower()
        if pl == fl or pl.startswith(fl + os.sep):
            return None
    for base in bases:
        bl = str(base).lower()
        if pl == bl or pl.startswith(bl + os.sep):
            return base
    return None


def _absolut_gesperrt(path: str) -> Path | None:
    return _liegt_unter(path, _absolut_bases())


def _gesperrt_zum_aendern(path: str) -> Path | None:
    return _liegt_unter(path, _schreibsperre_bases())


def _guard(*paths: str) -> str | None:
    """Gibt eine Fehlermeldung zurück, wenn EINER der Pfade geschützt ist
    (dann NICHT ausführen) – sonst None."""
    for path in paths:
        if path and _is_protected(path):
            return ("Fehler: 🛡️ Geschützt – NemiCLI darf in Windows-/Systemordnern "
                    f"nichts ändern. Verweigert: {path}.")
    for path in paths:
        if path and (base := _gesperrt_zum_aendern(path)) is not None:
            return ("Fehler: 🔒 Schreibgeschützt – der Nutzer hat diesen Bereich "
                    f"gesperrt: {base}. Ansehen darfst du ihn, ändern nicht. "
                    f"Verweigert: {path}. Sag dem Nutzer, was du ändern "
                    "würdest – er macht es selbst.")
    return None


# --- Systemordner sind komplett tabu – auch fürs Lesen -----------------------
# Ursprünglich durfte NemiCLI in C:\Windows & Co. *lesen* (nur nicht ändern).
# Entscheidung vom 12.09.2026: Die KI hat dort gar nichts zu suchen. Also
# sperren wir auch datei_lesen, ordner_auflisten, dateien_suchen, inhalt_suchen,
# ordner_erkennen/ordner_lernen und jeden Befehl, der einen Systemordner nennt.
# Die Laufwerks-Wurzel (C:\) darf weiterhin AUFGELISTET werden – nur die
# geschützten Ordner darunter nicht.

def _in_system_dir(path: str) -> bool:
    """Liegt der Pfad in einem geschützten Systemordner (oder ist einer)?"""
    roh = _entwirre(path)
    if not roh:
        return False
    try:
        p = Path(roh).expanduser().resolve()
    except Exception:
        return True                      # unklarer Pfad: lieber zu
    pl = str(p).lower()
    for base in _protected_bases():
        bl = str(base).lower()
        if pl == bl or pl.startswith(bl + os.sep):
            return True
    return False


def _read_guard(*paths: str) -> str | None:
    """Lese-Sperre: nur die absolut gesperrten Orte des Nutzers sind unsichtbar.

    Bis 18.09.2026 waren auch Windows-/Systemordner fürs Lesen tabu. Für die
    Systemwache (svchost am richtigen Ort? Signatur von C:\\Windows\\System32\\…?)
    muss die KI aber hinschauen dürfen. Der Nutzer hat entschieden: nachschauen
    ja, ändern nie – fürs Ändern bleibt _guard() so streng wie vorher."""
    for path in paths:
        if path and (base := _absolut_gesperrt(path)) is not None:
            return ("Fehler: 🔒 Komplett gesperrt – der Nutzer hat diesen Bereich für "
                    f"dich zugesperrt: {base}. Weder ansehen noch ändern. "
                    f"Verweigert: {path}. Sag dem Nutzer, dass du dort grundsätzlich "
                    "nicht hineinschaust; er sieht selbst nach.")
    return None


def _skip_system(p: Path, base: "Path | str | None" = None) -> bool:
    """Für Suchläufe: gesperrte Orte immer überspringen (sonst würde
    dateien_suchen den NemiCLI-Ordner auflisten, den _read_guard zusperrt).
    Systemordner nur dann, wenn die Suche NICHT ausdrücklich dort begann –
    `*.log` ab C:\\ soll nicht Windows durchwühlen, `*.log` ab C:\\Windows schon."""
    try:
        if _absolut_gesperrt(str(p)) is not None:
            return True
        if base is not None and _in_system_dir(str(base)):
            return False
        return _in_system_dir(str(p))
    except Exception:
        return True


# Verändernde PowerShell-Verben – im Befehl zusammen mit einem System-Ziel = Stopp.
# Die kurzen Alias-Formen sind wichtig: `rm`, `mv`, `cp`, `si`, `ni` tun dasselbe
# wie die ausgeschriebenen Cmdlets, wurden vorher aber nicht erkannt.
_DESTRUCTIVE = re.compile(
    r"(?i)(\b(remove-item|ri|rm|rmdir|rd|del|erase|move-item|move|mv|mi|"
    r"rename-item|ren|rni|set-content|sc|add-content|ac|clear-content|clc|"
    r"out-file|new-item|ni|copy-item|copy|cp|cpi|"
    r"set-itemproperty|sp|si|new-itemproperty|remove-itemproperty|rp|"
    r"clear-itemproperty|clp|rename-itemproperty|attrib|reg)\b|>>?)"
)

# Diese Befehle sind IMMER gesperrt – egal welcher Pfad dahinter steht.
# Begründung: Sie zielen von Natur aus auf das System selbst (Partitionen,
# Startvorgang, Schattenkopien, Rechte, Dienste, Virenschutz, Konten). Für einen
# Chat-Assistenten gibt es dafür keinen harmlosen Anwendungsfall, und ein
# Tippfehler oder eine halluzinierte Zeile richtet hier echten Schaden an.
_IMMER_STOPP = re.compile(
    r"(?i)(?<![\w-])("
    r"format(-volume)?|diskpart|bcdedit|vssadmin|wbadmin|"
    r"clear-disk|initialize-disk|set-partition|remove-partition|new-partition|"
    r"fsutil|mountvol|label|convert\.exe|chkdsk|"
    r"sfc|dism|takeown|icacls|cacls|xcacls|cipher|"
    r"set-executionpolicy|set-mppreference|add-mppreference|"
    r"disable-computerrestore|disable-windowsoptionalfeature|"
    r"remove-windowsfeature|uninstall-windowsfeature|"
    r"stop-service|set-service|remove-service|sc\.exe|"
    r"schtasks|register-scheduledtask|unregister-scheduledtask|"
    r"net\s+user|net\s+localgroup|"
    r"new-localuser|remove-localuser|add-localgroupmember|"
    r"wmic|bootrec|bootsect"
    r")(?![\w-])"
)

# Verschleierung. Alle Sperren hier sind Text-Prüfungen – wer den Befehl
# unkenntlich macht, läuft an ihnen vorbei. Beispiel: derselbe Löschbefehl als
# Base64 hinter `-EncodedCommand` sieht aus wie zufälliger Buchstabensalat.
# Deshalb: Wer sich versteckt, kommt gar nicht erst durch.
_VERSCHLEIERT = re.compile(
    r"(?i)(?<![\w-])("
    r"-e(nc|ncoded|ncodedcommand)?\s+[A-Za-z0-9+/=]{24,}|"     # -enc <base64>
    r"frombase64string|"
    r"invoke-expression|iex|invoke-command|"
    r"downloadstring|downloadfile|invoke-webrequest\s+[^|]*\|\s*iex|"
    r"start-process[^|]*-verb\s+runas|"                        # Rechte-Erhöhung
    r"powershell(\.exe)?\s+[^|]*-w(indowstyle)?\s+hidden"
    r")(?![\w-])"
)

# Registry-Zweige, die zum System gehören (HKCU = Nutzer, bleibt erlaubt).
_REG_SYSTEM = re.compile(
    r"(?i)(?<![\w:])(hklm|hkey_local_machine|hkcr|hkey_classes_root|"
    r"hku|hkey_users|registry::hkey_local_machine)(?![\w])"
)


def _guard_command(cmd: str, lesend: bool = False) -> str | None:
    """Sicherheitsnetz für freie PowerShell-Befehle.

    Zwei Stufen:
      1. `_IMMER_STOPP` – Werkzeuge, die am System selbst arbeiten. Sofort aus.
      2. Jede Nennung eines System-Ziels – auch nur zum Anschauen
         (Get-ChildItem C:\\Windows). Seit 12.09.2026 sind Systemordner für
         die KI komplett tabu, nicht nur fürs Schreiben.

    Als System-Ziel zählen die geschützten Ordner UND die System-Zweige der
    Registry. Vor dem Vergleich werden Schrägstriche vereinheitlicht: `C:/Windows`
    ist derselbe Ort wie `C:\\Windows`, rutschte vorher aber ungeprüft durch.
    """
    roh = os.path.expandvars(cmd or "")
    if not roh.strip():
        return None

    if (m := _VERSCHLEIERT.search(roh)):
        return ("Fehler: 🛡️ Geschützt – der Befehl versteckt, was er tut "
                f"('{m.group(0)}'): kodierter Text, nachgeladener Code oder eine "
                "Rechte-Erhöhung. Genau daran hängt jede Sperre vorbei. "
                "Schreib den Befehl bitte im Klartext.")

    if (m := _IMMER_STOPP.search(roh)):
        return ("Fehler: 🛡️ Geschützt – der Befehl enthält ein Werkzeug, das direkt am "
                f"System arbeitet ('{m.group(0)}'): Partitionen, Startvorgang, Rechte, "
                "Dienste oder Konten. NemiCLI führt so etwas grundsätzlich nicht aus.")

    # Schrägstriche angleichen, damit C:/Windows genauso erkannt wird wie C:\Windows
    norm = roh.lower().replace("/", "\\")
    bases = [str(b).lower() for b in _protected_bases()] + \
            [r"c:\windows", r"c:\program files", r"c:\programdata",
             "%systemroot%", "%windir%"]
    nennt_system = (
        any(b in norm for b in bases)
        or bool(re.search(r"(?i)\$env:(windir|systemroot|programfiles|programdata)", roh))
        or bool(_REG_SYSTEM.search(roh))
    )
    # `abfragen` (lesend=True) darf Systemorte nennen – es kann nichts ändern,
    # _nur_lesend() hat das vorher sichergestellt. `befehl` bleibt hier streng.
    if nennt_system and not lesend:
        return ("Fehler: 🛡️ Tabu – dieser Befehl nennt einen Windows-Systemordner oder die "
                "System-Registry. NemiCLI geht dort grundsätzlich nicht hin, auch nicht zum "
                "Anschauen. Sag dem Nutzer, dass er das selbst nachsehen muss.")

    # Absolut gesperrte Orte: wie bei Windows reicht die bloße Nennung.
    for base in _absolut_bases():
        if str(base).lower().replace("/", "\\") in norm \
                and not _nur_daten_gemeint(base, norm):
            return ("Fehler: 🔒 Komplett gesperrt – der Befehl nennt einen Bereich, den "
                    f"der Nutzer für dich zugesperrt hat: {base}. Dort gehst du "
                    "grundsätzlich nicht hin, auch nicht zum Anschauen.")

    # Nur-Lesen-Bereiche: nennen darf der Befehl sie (Get-ChildItem,
    # Get-Content), nur nicht zusammen mit einem verändernden Verb. Ohne diese
    # Stufe wäre die Sperre wertlos - "befehl" mit Set-Content ginge daran vorbei.
    if _DESTRUCTIVE.search(roh):
        for base in _schreibsperre_bases():
            if str(base).lower().replace("/", "\\") in norm \
                    and not _nur_daten_gemeint(base, norm):
                return ("Fehler: 🔒 Schreibgeschützt – der Befehl würde etwas in einem "
                        f"gesperrten Bereich ändern: {base}. Ansehen ist erlaubt, "
                        "ändern nicht. Sag dem Nutzer, was du tun würdest – er macht es selbst.")
    return None


# ---------------------------------------------------------------------------
# Lesende Werkzeuge (ohne Nachfrage)
# ---------------------------------------------------------------------------

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
_BINARY_EXT = {".pdf", ".zip", ".exe", ".dll", ".gguf", ".mp3", ".mp4", ".wav",
               ".ogg", ".bin", ".so", ".pyc", ".7z", ".rar"}


def _datei_lesen(a: dict) -> str | ActionResult:
    if (blocked := _read_guard(a.get("pfad", ""))):
        return ActionResult(blocked, ok=False)
    p = Path(a["pfad"])
    ext = p.suffix.lower()
    if ext in _IMAGE_EXT:
        return ActionResult(f"(Das ist eine Bilddatei: {p.name}. Nicht als Text lesbar – "
                "nutze dafür das Werkzeug bild_ansehen mit demselben Pfad.)", ok=False)
    if ext in _BINARY_EXT:
        return ActionResult(f"(Binärdatei {p.name} – kann nicht als Text gelesen werden.)", ok=False)
    # Heuristik: enthält der Anfang Null-Bytes? -> binär
    try:
        head = p.read_bytes()[:2048]
        if b"\x00" in head:
            return ActionResult(f"(Datei {p.name} sieht binär aus – nicht als Text lesbar.)", ok=False)
    except Exception as e:
        return ActionResult(f"Fehler beim Lesen: {e}", ok=False)
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if not lines:
        return "(leere Datei)"
    # `ab`: ab dieser Zeile lesen. Lange Dateien kommen so in Stücken, statt dass
    # das Modell nach der Kürzung zu Get-Content greift (das las UTF-8 falsch).
    try:
        ab = max(1, int(a.get("ab") or 1))
    except (TypeError, ValueError):
        ab = 1
    if ab > len(lines):
        return f"(Die Datei hat nur {len(lines)} Zeilen – ab Zeile {ab} steht nichts mehr.)"
    # mit Zeilennummern, damit das Modell gezielt bearbeiten kann
    out, laenge, ende = [], 0, len(lines)
    for i in range(ab - 1, len(lines)):
        zeile = f"{i + 1:>5}  {lines[i]}"
        if laenge + len(zeile) > MAX_OUT and out:
            ende = i
            break
        out.append(zeile)
        laenge += len(zeile)
    text = "".join(out)
    if ende < len(lines):
        text += (f"\n… gekürzt: Zeilen {ende + 1}–{len(lines)} fehlen noch. Weiter mit "
                 f"datei_lesen, pfad wie eben, ab: {ende + 1}")
    return text


def _bild_ansehen(a: dict) -> ActionResult:
    """Ein Bild von der Platte ins Gespräch holen: main hängt es in der nächsten
    Runde als echtes Bild an (Vision) – oder lässt den Qwen-Helfer beschreiben."""
    if (blocked := _read_guard(a.get("pfad", ""))):
        return ActionResult(blocked, ok=False)
    p = Path(str(a.get("pfad", "")).strip().strip('"'))
    if not p.is_file():
        return ActionResult(f"Bild nicht gefunden: {p}", ok=False)
    if p.suffix.lower() not in _IMAGE_EXT:
        return ActionResult(f"{p.name} ist keine Bilddatei (png/jpg/webp/gif/bmp).", ok=False)
    try:
        groesse = p.stat().st_size
    except OSError:
        groesse = 0
    return ActionResult(f"Bild {p.name} ({groesse // 1024} KB) wird dir jetzt als Bild gezeigt – "
                        "beschreibe oder lies, was du darauf siehst.", ok=True, bilder=[str(p)])


def _bildschirm_ansehen(a: dict) -> ActionResult:
    """Screenshot des ganzen Bildschirms (PNG) machen und ihn dem Modell zeigen.
    Fragt vorher (confirm), denn auf dem Schirm kann Privates sein."""
    try:
        from PIL import ImageGrab
    except Exception as e:
        return ActionResult(f"Screenshot nicht möglich (PIL fehlt): {e}", ok=False)
    from datetime import datetime
    try:
        from paths import ROOT
    except Exception:
        ROOT = Path(".")
    ordner = ROOT / "Bilder" / "Screenshots"
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        img = ImageGrab.grab(all_screens=bool(a.get("alle_monitore")))
        pfad = ordner / f"screen_{datetime.now():%Y%m%d_%H%M%S}.png"
        img.save(pfad)
    except Exception as e:
        return ActionResult(f"Screenshot fehlgeschlagen: {e}", ok=False)
    return ActionResult(f"Screenshot gemacht ({img.width}x{img.height}): {pfad}\n"
                        "Er wird dir jetzt als Bild gezeigt – lies/beschreibe, was drauf ist.",
                        ok=True, bilder=[str(pfad)])


def _ordner_auflisten(a: dict) -> str | ActionResult:
    p = a.get("pfad", ".")
    if (blocked := _read_guard(p)):
        return ActionResult(blocked, ok=False)
    out = []
    for name in sorted(os.listdir(p)):
        voll = os.path.join(p, name)
        if _absolut_gesperrt(voll) is not None:   # nur die Sperren des Nutzers bleiben unsichtbar
            continue
        marker = "[Ordner] " if os.path.isdir(voll) else "          "
        out.append(marker + name)
    if not out:
        return "(leer)"
    # ML-Einschätzung des Ordner-Typs voranstellen (Stufe 1: PC kennenlernen)
    kopf = f"[Ordner-Typ laut ML: {foldersense.describe(p)}]\n"
    return kopf + (_cut("\n".join(out)))


def _ordner_erkennen(a: dict) -> str | ActionResult:
    """Schätzt mit dem ML-Modell, was für ein Ordner das ist."""
    p = a.get("pfad", ".")
    if (blocked := _read_guard(p)):
        return ActionResult(blocked, ok=False)
    r = foldersense.classify(p)
    if r["empty"]:
        return f"{p}: leerer oder nicht lesbarer Ordner (keine Merkmale)."
    return (f"{p}\n  Ordner-Typ (ML): {r['name']}\n"
            f"  Sicherheit: {round(r['confidence']*100)} %  "
            f"({r['tokens']} Merkmale ausgewertet)")


def _dateien_suchen(a: dict) -> str | ActionResult:
    base = Path(a.get("pfad", "."))
    if (blocked := _read_guard(str(base))):
        return ActionResult(blocked, ok=False)
    muster = a.get("muster", "*")
    treffer = [str(p) for p in base.rglob(muster)
               if p.is_file() and not _skip_system(p, base)][:200]
    return _cut("\n".join(treffer)) if treffer else "Keine Dateien gefunden."


def _web_lesen(a: dict) -> ActionResult:
    return ActionResult(webfetch.fetch(a["url"]), ok=None)


def _web_wiki(a: dict) -> ActionResult:
    return ActionResult(webfetch.wikipedia(a["suche"]), ok=None)


def _web_suche(a: dict) -> ActionResult:
    return ActionResult(webfetch.search(a.get("suche") or a.get("query") or ""), ok=None)


def _inhalt_suchen(a: dict) -> str | ActionResult:
    base = Path(a.get("pfad", "."))
    if (blocked := _read_guard(str(base))):
        return ActionResult(blocked, ok=False)
    muster = a["muster"]
    glob = a.get("glob", "*")
    rx = re.compile(muster, re.IGNORECASE)
    out = []
    for p in base.rglob(glob):
        if not p.is_file() or _skip_system(p, base):
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    out.append(f"{p}:{i}: {line.strip()[:160]}")
                    if len(out) >= 200:
                        break
        except Exception:
            continue
        if len(out) >= 200:
            break
    return _cut("\n".join(out)) if out else "Keine Treffer."


# ---------------------------------------------------------------------------
# Verändernde Werkzeuge (immer mit Nachfrage)
# ---------------------------------------------------------------------------

def _datei_schreiben(a: dict) -> str | ActionResult:
    p = a["pfad"]
    if (blocked := _guard(p)):
        return ActionResult(blocked, ok=False)
    inhalt = a.get("inhalt", "")
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(inhalt)
    return f"Datei geschrieben: {p} ({len(inhalt)} Zeichen)"


def _datei_bearbeiten(a: dict) -> str | ActionResult:
    p = a["pfad"]
    if (blocked := _guard(p)):
        return ActionResult(blocked, ok=False)
    suchen = a["suchen"]
    ersetzen = a.get("ersetzen", "")
    text = Path(p).read_text(encoding="utf-8")
    n = text.count(suchen)
    if n == 0:
        return ActionResult(f"Fehler: Text nicht gefunden in {p}. Nichts geändert.", ok=False)
    text = text.replace(suchen, ersetzen)
    Path(p).write_text(text, encoding="utf-8")
    return f"Datei bearbeitet: {p} ({n}× ersetzt)"


def _ordner_erstellen(a: dict) -> str | ActionResult:
    p = a["pfad"]
    if (blocked := _guard(p)):
        return ActionResult(blocked, ok=False)
    os.makedirs(p, exist_ok=True)
    return f"Ordner erstellt: {p}"


def _verschieben(a: dict) -> str | ActionResult:
    von, nach = a["von"], a["nach"]
    if (blocked := _guard(von, nach)):       # Quelle UND Ziel prüfen
        return ActionResult(blocked, ok=False)
    shutil.move(von, nach)
    return f"Verschoben/umbenannt: {von} → {nach}"


def _loeschen(a: dict) -> str | ActionResult:
    p = a["pfad"]
    if (blocked := _guard(p)):
        return ActionResult(blocked, ok=False)
    if os.path.isdir(p):
        shutil.rmtree(p)
        return f"Ordner gelöscht: {p}"
    os.remove(p)
    return f"Datei gelöscht: {p}"


# Mögliche Feld-Namen für den Befehl (Modelle tippen sich gern mal – siehe auch
# resolve_tool für den Werkzeug-Namen). _befehl_text findet den Befehl auch dann,
# wenn das Feld z.B. 'command', 'cmd' oder vertippt ('bebefehl') heißt.
_CMD_KEYS = ("befehl", "command", "cmd", "kommando", "powershell", "ps",
             "shell", "code", "skript", "script")


def _befehl_text(a: dict) -> str:
    """Holt den auszuführenden Befehl aus a – tolerant gegenüber Feld-Namen."""
    for k in _CMD_KEYS:                          # bekannte Namen zuerst
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            return v
    # Sonst: ein Schlüssel, der einem Befehls-Namen stark ähnelt (z.B. 'bebefehl')
    for k, v in a.items():
        if k == "tool" or not isinstance(v, str) or not v.strip():
            continue
        if difflib.get_close_matches(k.lower(), _CMD_KEYS, n=1, cutoff=0.7):
            return v
    return ""


def _befehl(a: dict, lesend: bool = False) -> ActionResult:
    cmd = _befehl_text(a)
    if not cmd.strip():
        return ActionResult("Fehler: kein Befehl angegeben. Schreib den auszuführenden Befehl ins "
                "Feld 'befehl', z.B. {\"tool\": \"befehl\", \"befehl\": \"Get-Date\"}.", ok=False)
    # Konsole (und damit auch native Tools wie netstat/ipconfig) auf UTF-8 stellen,
    # bevor der eigentliche Befehl läuft. So stimmen Umlaute bei PowerShell-Cmdlets
    # UND nativen Befehlen – früher wurde alles als OEM-Codepage geraten, was bei
    # echten UTF-8/UTF-16-Cmdlets die Umlaute zerschoss.
    # $ProgressPreference='SilentlyContinue' unterdrückt PowerShells Fortschritts-
    # balken (Write-Progress), den z.B. Test-NetConnection/Invoke-WebRequest zeigen –
    # der malt sonst direkt auf die Konsole und zerschießt unser Vollbild-TUI.
    if (blocked := _guard_command(cmd, lesend)):     # System-Pfad + verändernd = Stopp
        return ActionResult(blocked, ok=False)
    # Cmdlet-Fehler sind standardmäßig nicht terminierend. Stop + catch macht
    # daraus einen Prozessfehler; $Error erfasst auch explizites -ErrorAction
    # Continue/SilentlyContinue. Native Programme liefern ihren eigenen Exitcode.
    # Zeilenumbrüche halten auch Befehle mit abschließendem Kommentar korrekt.
    # Ausgabe: PowerShells Tabellen-Formatierer sammelt Objekte erst (er misst
    # Spaltenbreiten) und schreibt verzögert – das `exit` direkt danach kam ihm
    # zuvor, und `Get-CimInstance … | Select-Object a, b` kam als "(kein Output)"
    # zurück. Out-String in der Pipeline zwingt die Formatierung. Und weil der
    # Formatierer die Spalten vom ERSTEN Objekt nimmt, wurden bei mehreren
    # Abfragen in einem Block (Select a,b; dann Select c,d) alle späteren Zeilen
    # leer – deshalb werden Objekte gruppenweise formatiert: sobald sich die
    # Eigenschaften ändern, beginnt eine neue Tabelle.
    # Ausnahme: Objekte aus Format-Table/-List (ein Strom aus Start…End) bleiben
    # als EIN Block zusammen – ein halber Format-Strom in Out-String ist eine
    # NullReferenceException („Der Objektverweis wurde nicht …“, Chat 137).
    # Get-Content liest in PowerShell 5.1 ohne BOM als Windows-1252 – UTF-8-
    # Dateien kamen als „stÃ¤rkste“ zurück. Deshalb UTF-8 als Vorgabe fürs Lesen
    # (nur Lesen: beim Schreiben würde 5.1 eine BOM voranstellen).
    wrapped = (
        "$ProgressPreference='SilentlyContinue'; "
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[System.Text.Encoding]::UTF8;\n"
        "$ErrorActionPreference='Stop'; $Error.Clear(); $global:LASTEXITCODE=0;\n"
        "$PSDefaultParameterValues['Get-Content:Encoding']='UTF8'; "
        "$PSDefaultParameterValues['Select-String:Encoding']='UTF8'; "
        "$PSDefaultParameterValues['Import-Csv:Encoding']='UTF8';\n"
        "try {\n& {\n" + cmd + "\n} | ForEach-Object -Begin { $nemiG=@(); $nemiK=$null } "
        "-Process { $k = if ($_.PSObject.TypeNames[0] -like "
        "'Microsoft.PowerShell.Commands.Internal.Format.*') { '__format__' } "
        "else { $_.PSObject.Properties.Name -join ',' }; "
        "if ($null -ne $nemiK -and $k -ne $nemiK) { $nemiG | Out-String -Width 200; $nemiG=@() }; "
        "$nemiK=$k; $nemiG+=$_ } -End { if ($nemiG.Count) { $nemiG | Out-String -Width 200 } }\n"
        "$nemiCommandOk=$?; $nemiNativeExitCode=$LASTEXITCODE;\n"
        "if ($nemiNativeExitCode -ne 0) { exit $nemiNativeExitCode };\n"
        "if ((-not $nemiCommandOk) -or $Error.Count -gt 0) { exit 1 };\n"
        "exit 0\n"
        "} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", wrapped],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    out = "\n".join(part.strip() for part in (r.stdout, r.stderr) if part and part.strip())
    if r.returncode != 0:
        text = f"Befehl fehlgeschlagen (Exitcode {r.returncode})."
        if out:
            text += "\n" + _cut(out)
        return ActionResult(text, ok=False, returncode=r.returncode)
    return ActionResult(_cut(out) if out else "(kein Output)", ok=True, returncode=0)


# ---------------------------------------------------------------------------
# abfragen – PowerShell, das nur liest (ohne Nachfrage)
# ---------------------------------------------------------------------------
# `befehl` fragt IMMER. Für eine Systemwache (Prozesse, Verbindungen, Dienste,
# Virenschutz, Netz-Tempo …) sind das zwanzig Rückfragen für zwanzig Blicke –
# und im Hintergrund-Lauf (Zeitplan) sitzt niemand da, der F8 drückt.
# `abfragen` lässt deshalb nur Befehle durch, die nichts verändern. Was genau
# abgefragt wird, steht NICHT hier – das entscheidet die Persönlichkeit.
#
# Wie entschieden wird (fail-safe: im Zweifel nein):
#   1. Alle Sperren von `befehl` (Systemordner, gesperrte Orte, Verschleierung).
#   2. Jedes Cmdlet (Verb-Nomen) muss ein Lese-Verb tragen – Get, Test, Measure,
#      Select, Sort … Trägt es ein anderes offizielles PowerShell-Verb (Set,
#      Remove, Start, Stop, Invoke, New …), ist Schluss. Wörter mit Bindestrich,
#      deren erster Teil KEIN PowerShell-Verb ist ("svc-host"), sind Argumente.
#   3. Jeder Befehlsanfang (nach ; | Zeilenumbruch { ( =) muss ein Cmdlet, ein
#      erlaubter Alias, ein erlaubtes natives Programm, ein Schlüsselwort oder
#      eine Variable sein. Ein fremdes Programm (cmd, powershell, x.exe) nicht.
#   4. Keine Umleitung in Dateien (>), kein & "pfad", kein Dot-Sourcing, keine
#      .NET-Aufrufe, die schreiben/starten/löschen, kein Add-Type/New-Object,
#      kein eigener Netz-Zugriff (Invoke-WebRequest & Co.) – fürs Web gibt es
#      web_lesen mit Allowlist.

# Offizielle PowerShell-Verben. Nur für DIESE gilt "Verb-Nomen ist ein Cmdlet".
_PS_VERBEN = frozenset("""
add clear close copy enter exit find format get hide join lock move new open optimize pop
push redo remove rename reset resize restore search select set show skip split step switch
undo unlock watch backup checkpoint compare compress convert convertfrom convertto dismount
edit expand export group import initialize limit merge mount out publish save sync unpublish
update debug measure ping repair resolve test trace connect disconnect read receive send
write block grant protect revoke unblock unprotect use invoke register request restart resume
start stop submit suspend uninstall unregister wait confirm deny approve assert complete
build deploy install
""".split())

# Verben, die nur lesen.
_LESE_VERBEN = frozenset("""
get find search select show measure test compare resolve convert convertfrom convertto
format group ping trace read
""".split())

# Cmdlets mit Lese-Verb, die trotzdem NICHT dürfen (schreiben, warten auf Eingabe, laden Code).
_LESE_AUSNAHMEN = frozenset("""
read-host get-credential test-scriptfile trace-command show-command
""".split())

# Cmdlets ohne Lese-Verb, die trotzdem harmlos sind.
_LESE_EXTRA = frozenset("""
sort-object where-object foreach-object out-string out-null out-default out-host
write-output write-host write-verbose write-warning write-information write-debug
join-path split-path join-string import-csv import-clixml select-xml
set-location push-location pop-location start-sleep
""".split())

# Aliase und Schlüsselwörter, die am Befehlsanfang stehen dürfen.
_LESE_ALIASE = frozenset("""
gci ls dir gc cat type gp gps ps gsv gm select sort where ? % foreach ft fl fw measure
group compare diff echo write oss sls gwmi gcim gdr gi gl pwd gv gcm gal gjb gu gtz gin
gsnp ghy h history cd sl pushd popd
if else elseif for while do switch try catch finally return param begin process end
throw break continue exit in
""".split())

# Native Programme, die nur lesen. Bei manchen entscheidet das Argument – siehe
# _NATIV_VERBOTEN: `ipconfig` liest, `ipconfig /flushdns` ändert.
_LESE_NATIV = frozenset("""
netstat ipconfig tasklist whoami systeminfo ping tracert pathping nslookup arp getmac
driverquery nvidia-smi hostname ver query route netsh powercfg tzutil where.exe reg
""".split())
_NATIV_VERBOTEN = re.compile(
    r"(?i)\b(ipconfig\s+/(release|renew|flushdns|registerdns|setclassid)|"
    r"arp\s+-[ds]\b|route\s+(add|delete|change)\b|"
    r"netsh\b(?![^;|\n]*\bshow\b)|powercfg\s+/(?!q|l|a\b|energy|batteryreport|devicequery)|"
    r"tzutil\s+/s|reg\s+(?!query\b)\w+)"
)

# Konstrukte, die ein reiner Lese-Befehl nicht braucht.
_NICHT_LESEND = re.compile(
    r"(?i)("
    r"(?<![\d*])>|"                                   # > und >> in Dateien (2>&1 bleibt erlaubt)
    r"(?<![\w$])&\s*[\"'$]|"                            # & "programm" / & $x
    r"(?:^|[;|\n{(])\s*\.\s+[\"'$.\\/]|"               # Dot-Sourcing
    r"\badd-type\b|\bnew-object\b|\bimport-module\b|\btee-object\b|\bout-file\b|\bexport-\w+|"
    r"\bfunction\s+\w|\bfilter\s+\w|"
    r"\[[\w.]*(reflection|diagnostics\.process|io\.file|io\.directory|activator|marshal|"
    r"runtime\.interop|net\.webclient|net\.http|net\.sockets|management\.automation)\b|"
    r"(?:\.|::)\s*(kill|delete|remove\w*|move\w*|copy\w*|create\w*|write\w*|append\w*|"
    r"start|stop|invoke\w*|load\w*|set\w+|terminate|dispose|exit)\s*\(|"
    r"\b(invoke-webrequest|invoke-restmethod|iwr|irm|curl|wget|certutil|bitsadmin)\b"
    r")"
)
_VERB_NOMEN = re.compile(r"(?<![\w$@.:\-])([A-Za-z]+)-([A-Za-z][\w]*)")
_SEGMENT_START = re.compile(r"(?:^|[;|\n{(]|(?<![=!<>])=(?!=))\s*([^\s;|{}()]+)")
_STRINGS = re.compile(r"'[^']*'|\"[^\"]*\"")


def _nur_lesend(cmd: str) -> str | None:
    """None, wenn der Befehl nur liest. Sonst der Grund, warum nicht."""
    roh = cmd or ""
    if (m := _NICHT_LESEND.search(roh)):
        return (f"'{m.group(0).strip()}' gehört nicht in eine reine Abfrage – das schreibt, "
                "startet, lädt Code oder geht selbst ins Netz.")
    for m in _VERB_NOMEN.finditer(roh):
        verb, ganz = m.group(1).lower(), m.group(0).lower()
        if ganz in _LESE_EXTRA:
            continue
        if ganz in _LESE_AUSNAHMEN:
            return f"'{m.group(0)}' wartet auf Eingabe oder lädt Code – das ist keine Abfrage."
        if verb in _LESE_VERBEN:
            continue
        if verb in _PS_VERBEN:
            return f"'{m.group(0)}' verändert etwas (Verb '{m.group(1)}')."
        # kein PowerShell-Verb -> ein Argument wie 'svc-host', kein Cmdlet
    # Befehlsanfänge: Strings vorher ausblenden – ein '(' oder ';' IN einem
    # Text ('\Processor(_Total)') ist kein neuer Befehl.
    for m in _SEGMENT_START.finditer(_STRINGS.sub("'…'", roh)):
        wort = m.group(1)
        w = wort.lower()
        if not w or w[0] in "$@\"'-[(" or w[0].isdigit() or w.startswith(("#", "..")):
            continue
        if "=" in w:                            # Zuweisung (n=$_.Name): rechts wird eigens geprüft
            continue
        if w.endswith(".exe") and w not in _LESE_NATIV:
            w = w[:-4]
        if w in _LESE_ALIASE or w in _LESE_NATIV or w in _LESE_EXTRA:
            continue
        if "-" in w and w.split("-", 1)[0] in _LESE_VERBEN:
            continue
        if re.fullmatch(r"[a-z]+-\w+", w) and w.split("-", 1)[0] not in _PS_VERBEN:
            continue                            # 'svc-host' als Wert nach einem =
        return (f"'{wort}' ist kein Lese-Befehl, den ich kenne. Erlaubt sind Cmdlets mit "
                "Get/Test/Measure/Select/Sort/Where/Format/Compare/Resolve/Convert… und "
                "diese Programme: " + ", ".join(sorted(_LESE_NATIV)) + ".")
    if (m := _NATIV_VERBOTEN.search(roh)):
        return f"'{m.group(0).strip()}' verändert etwas – das ist keine Abfrage."
    return None


def _abfragen(a: dict) -> ActionResult:
    cmd = _befehl_text(a)
    if not cmd.strip():
        return ActionResult("Fehler: kein Befehl angegeben. Schreib die Abfrage ins Feld "
                            "'befehl', z.B. {\"tool\": \"abfragen\", \"befehl\": \"Get-Process\"}.",
                            ok=False)
    if (grund := _nur_lesend(cmd)):
        return ActionResult("Fehler: 🔍 'abfragen' darf nur lesen. " + grund +
                            " Wenn es wirklich etwas ändern soll, nimm 'befehl' – "
                            "das fragt den Nutzer.", ok=False)
    if (blocked := _guard_command(cmd, lesend=True)):
        return ActionResult(blocked, ok=False)
    return _befehl({"befehl": cmd}, lesend=True)


# ---------------------------------------------------------------------------
# zeitplan – Aufgaben in der Windows-Aufgabenplanung
# ---------------------------------------------------------------------------

def _zeitplan(a: dict) -> ActionResult:
    import zeitplan
    was = str(a.get("aktion") or "").strip().lower()
    try:
        if was in ("anlegen", "neu", "erstellen", "planen"):
            return ActionResult(zeitplan.anlegen(a.get("name", ""), a.get("wann", ""),
                                                 a.get("auftrag", "")))
        if was in ("loeschen", "löschen", "entfernen"):
            return ActionResult(zeitplan.loeschen(a.get("name", "")))
        if was in ("anzeigen", "liste", "zeigen"):
            return ActionResult(zeitplan.liste())
        return ActionResult("Fehler: 'aktion' muss 'anlegen' oder 'loeschen' sein "
                            "(zum Ansehen: zeitplan_anzeigen).", ok=False)
    except ValueError as e:
        return ActionResult(f"Fehler: {e}", ok=False)
    except Exception as e:
        return ActionResult(f"Fehler in der Aufgabenplanung: {e}", ok=False)


def _zeitplan_anzeigen(a: dict) -> ActionResult:
    import zeitplan
    try:
        return ActionResult(zeitplan.liste())
    except Exception as e:
        return ActionResult(f"Fehler in der Aufgabenplanung: {e}", ok=False)


def _skill_merken(a: dict) -> str:
    # Im Workspace gehört Gelerntes zum PROJEKT und wandert mit ihm mit -
    # nicht in NemiCLIs eigenen Wissensspeicher.
    import workspace
    if workspace.aktiv():
        name = str(a.get("name") or "").strip() or "Notiz"
        ziel = workspace.memory_anhaengen(str(a.get("inhalt", "")), f"Skill: {name}")
        if ziel:
            return f"Ins Projekt-Gedächtnis geschrieben: {ziel}"
    path = learn.save_skill(a["name"], a.get("inhalt", ""))
    return f"Skill gemerkt: {path}"


def _merken(a: dict) -> str:
    """Langzeitgedächtnis: einen dauerhaften Fakt über Nutzer/PC speichern.

    Läuft ein Workspace, landet das Gemerkte im PROJEKT (.workspace/memory.md)
    statt in NemiCLIs learned/ - so bleibt das Wissen beim Projekt und geht
    nicht verloren, wenn der Ordner den Rechner wechselt."""
    text = a.get("text") or a.get("inhalt") or a.get("fakt") or ""
    kind = a.get("art") or a.get("kind") or "fakt"
    import workspace
    if workspace.aktiv():
        ziel = workspace.memory_anhaengen(text, str(kind))
        if ziel is None:
            return "Nichts gespeichert (leerer Text)."
        return f"Ins Projekt-Gedächtnis geschrieben ({kind}): {ziel}"
    e = memory.remember(text, kind)
    if e is None:
        return "Nichts gespeichert (leer oder schon sehr ähnlich gemerkt)."
    return f"Gemerkt ✅ ({e['kind']}): {e['text']}"


def _pdf_target(path: str) -> Path:
    """Derselbe endgültige Dateiname wie in pdfgen.markdown_to_pdf."""
    target = Path(path)
    return target if target.suffix.lower() == ".pdf" else target.with_suffix(".pdf")


def _pdf_erstellen(a: dict) -> str | ActionResult:
    """Erzeugt aus Markdown ein echtes PDF (eigener Generator, keine Fremd-Lib).
    Felder: pfad (Zieldatei .pdf), inhalt (Markdown), titel (optional)."""
    pfad = a.get("pfad") or a.get("datei") or ""
    inhalt = a.get("inhalt") or a.get("markdown") or a.get("text") or ""
    titel = a.get("titel") or a.get("title") or ""
    if not pfad:
        return ActionResult("Fehler: kein Ziel-Pfad (Feld 'pfad', z.B. bericht.pdf).", ok=False)
    if not inhalt.strip():
        return ActionResult("Fehler: kein Inhalt (Feld 'inhalt' mit Markdown).", ok=False)
    pfad = str(_pdf_target(pfad))
    if (blocked := _guard(pfad)):
        return ActionResult(blocked, ok=False)
    try:
        out = pdfgen.markdown_to_pdf(inhalt, pfad, titel=titel)
    except Exception as e:
        return ActionResult(f"Fehler beim PDF-Erstellen: {e}", ok=False)
    groesse = Path(out).stat().st_size
    return f"PDF erstellt ✅  {out}  ({groesse} Bytes)"


def _ml_status(a: dict) -> str:
    """Bericht über den eigenen Ordner-Sinn (ML): Einschätzung + Gelerntes."""
    return foldersense.report_text(a.get("pfad") or None)


def _parse_size(v):
    """'768x768' -> (768, 768). Alles andere/leer -> None (Standardgröße)."""
    if not v:
        return None
    try:
        w, h = str(v).lower().replace(" ", "").split("x")
        return (int(w), int(h))
    except Exception:
        return None


def _bild_malen(a: dict) -> str | ActionResult:
    """Erzeugt ein Bild mit NemiCLIs EIGENER Stable-Diffusion-Pipeline und öffnet es.
    Felder: prompt (Pflicht); optional neg/steps/cfg/size('768x768')/seed/model."""
    import imagegen
    # "extern" = ComfyUI ODER WebUI. Frueher stand hier nur webui - dadurch
    # galt ComfyUI als eigene Pipeline und musste diffusers mitbringen, obwohl
    # es nur ein HTTP-Aufruf ist. Siehe imagegen.missing_reason_aktiv().
    ist_extern = imagegen.backend() != "builtin"
    prompt = (a.get("prompt") or a.get("beschreibung") or a.get("motiv")
              or a.get("text") or "").strip()
    if not prompt:
        return ActionResult("Fehler: kein Prompt. Feld 'prompt' mit der Bildbeschreibung füllen.", ok=False)
    if (reason := imagegen.missing_reason_aktiv()):
        return ActionResult("Fehler: Der Bild-Motor kann gerade nicht malen. " + reason, ok=False)
    if not ist_extern:
        # Nur die eigene Pipeline braucht einen lokalen Checkpoint auf der Platte.
        if not imagegen.discover():
            return ActionResult("Fehler: kein Bild-Modell gefunden. Lege eine .safetensors-Datei in "
                    f"{imagegen.CKPT_DIRS[0]} (z.B. von Civitai).", ok=False)

    def _int(k):
        try:
            return int(a[k]) if a.get(k) not in (None, "") else None
        except Exception:
            return None

    def _float(k):
        try:
            return float(a[k]) if a.get(k) not in (None, "") else None
        except Exception:
            return None

    # Modellname großzügig auflösen. Passt er zu nichts, nehmen wir das per
    # /bildmodel gewählte Standard-Modell, statt abzubrechen – gewünscht ist
    # ein Bild, und ein erfundener Modellname soll das nicht verhindern.
    wunsch = a.get("model")
    hinweis = ""
    if wunsch and not ist_extern and imagegen.resolve(wunsch) is None:
        hinweis = f" (Modell '{wunsch}' kenne ich nicht – Standard-Modell genommen.)"
        wunsch = None

    _BILD_STATUS["msg"] = "starte …"
    try:
        path, nachbessern = imagegen.paint(
            prompt,
            model=wunsch,
            neg=a.get("neg") or a.get("negativ"),
            steps=_int("steps"),
            size=_parse_size(a.get("size") or a.get("groesse")),
            seed=_int("seed"),
            on_status=lambda m: _BILD_STATUS.__setitem__("msg", m),
        )
    except Exception as e:
        _BILD_STATUS["msg"] = None               # Balken im Haupt-Loop beenden
        return ActionResult(f"Fehler beim Malen: {e}", ok=False)

    # Automatisch nachbessern (Gesicht/Augen) – nur bei der eigenen Pipeline
    # sinnvoll; WebUI-Modelle (Krea/Qwen) macht der OpenCV-img2img-Weg nicht mit.
    nachgebessert = None
    if nachbessern:
        try:
            nachgebessert = imagegen.auto_nachbessern(
                path, on_status=lambda m: _BILD_STATUS.__setitem__("msg", m))
        except Exception:
            pass
    _BILD_STATUS["msg"] = None                   # Balken beenden

    ziel = nachgebessert or path
    oeffnen_fehler = None
    try:
        os.startfile(ziel)                       # fertiges Bild anzeigen (Windows)
    except Exception as e:
        oeffnen_fehler = str(e)

    if nachgebessert:
        text = (f"Bild fertig 🎨✅ gemalt: {path}\n"
                f"Automatisch nachgebessert (Gesicht/Augen): {nachgebessert}{hinweis}")
    else:
        text = f"Bild fertig 🎨✅ gespeichert: {path}{hinweis}"
    if oeffnen_fehler is not None:
        return ActionResult(text + f"\nÖffnen fehlgeschlagen: {oeffnen_fehler}", ok=False)
    return text + "\nÖffnen beim Anzeigeprogramm angefordert."


def _ordner_lernen(a: dict) -> str | ActionResult:
    """Bringt dem ML-Modell bei: dieser Ordner ist vom Typ X (trainiert neu)."""
    p = a.get("pfad", ".")
    typ = a.get("typ", "")
    if (blocked := _read_guard(p)):
        return ActionResult(blocked, ok=False)
    if typ not in foldersense.labels():
        gueltig = ", ".join(foldersense.labels())
        return ActionResult(f"Fehler: '{typ}' ist kein gültiger Typ. Erlaubt: {gueltig}", ok=False)
    if foldersense.learn_folder(p, typ):
        return (f"Gelernt: '{p}' ist ein '{foldersense.labels()[typ]}'. "
                "Modell wurde neu trainiert.")
    return ActionResult(f"Konnte aus '{p}' nichts lernen (leer oder nicht lesbar).", ok=False)


# name -> (funktion, braucht_bestätigung, pflichtfelder)




ACTIONS: dict[str, dict] = {
    # lesend
    "datei_lesen":      {"func": _datei_lesen,      "confirm": False, "felder": ["pfad"]},
    "bild_ansehen":     {"func": _bild_ansehen,     "confirm": False, "felder": ["pfad"]},
    "ordner_auflisten": {"func": _ordner_auflisten, "confirm": False, "felder": []},
    "dateien_suchen":   {"func": _dateien_suchen,   "confirm": False, "felder": ["muster"]},
    "inhalt_suchen":    {"func": _inhalt_suchen,    "confirm": False, "felder": ["muster"]},
    "ordner_erkennen":  {"func": _ordner_erkennen,  "confirm": False, "felder": []},
    # PowerShell, das nur liest (Prozesse, Verbindungen, Dienste …) – _nur_lesend wacht
    "abfragen":         {"func": _abfragen,         "confirm": False, "felder": []},
    "zeitplan_anzeigen": {"func": _zeitplan_anzeigen, "confirm": False, "felder": []},
    # Internet (nur Whitelist / Wikipedia – lesend, sicher)
    "web_lesen":        {"func": _web_lesen,        "confirm": False, "felder": ["url"]},
    "web_wiki":         {"func": _web_wiki,         "confirm": False, "felder": ["suche"]},
    "web_suche":        {"func": _web_suche,        "confirm": False, "felder": ["suche"]},
    # verändernd
    "datei_schreiben":  {"func": _datei_schreiben,  "confirm": True,  "felder": ["pfad", "inhalt"]},
    "datei_bearbeiten": {"func": _datei_bearbeiten, "confirm": True,  "felder": ["pfad", "suchen"]},
    "ordner_erstellen": {"func": _ordner_erstellen, "confirm": True,  "felder": ["pfad"]},
    "pdf_erstellen":    {"func": _pdf_erstellen,    "confirm": True,  "felder": ["pfad", "inhalt"]},
    "verschieben":      {"func": _verschieben,      "confirm": True,  "felder": ["von", "nach"]},
    "loeschen":         {"func": _loeschen,         "confirm": True,  "felder": ["pfad"]},
    "befehl":           {"func": _befehl,           "confirm": True,  "felder": []},
    # Windows-Aufgabenplanung: NemiCLI zu einer Zeit mit einem Auftrag starten
    "zeitplan":         {"func": _zeitplan,         "confirm": True,  "felder": ["aktion", "name"]},
    # Bildschirm fotografieren – fragt, weil Privates zu sehen sein kann
    "bildschirm_ansehen": {"func": _bildschirm_ansehen, "confirm": True, "felder": []},
    # lernen (eigener Wissensspeicher – harmlos, ohne Nachfrage)
    "skill_merken":     {"func": _skill_merken,     "confirm": False, "felder": ["name", "inhalt"]},
    "merken":           {"func": _merken,           "confirm": False, "felder": ["text"]},
    "ordner_lernen":    {"func": _ordner_lernen,    "confirm": False, "felder": ["pfad", "typ"]},
    "ml_status":        {"func": _ml_status,        "confirm": False, "felder": []},
    # Bild erzeugen (eigene Stable-Diffusion-Pipeline – läuft auf der GPU, harmlos)
    "bild_malen":       {"func": _bild_malen,       "confirm": False, "felder": []},
}


# ---------------------------------------------------------------------------
# Aktionen aus dem Modelltext auslesen
# ---------------------------------------------------------------------------

# Akzeptiert ```aktion, ```json oder einen Block ganz ohne Sprache –
# Hauptsache, das JSON enthält ein "tool"-Feld.
# Der schließende Zaun darf fehlen, wenn der Block die Antwort beendet: manche
# Modelle hören direkt nach dem `}` auf. Vorher fiel so ein Block stumm unter
# den Tisch – das Modell glaubte, geschrieben zu haben, die Datei fehlte.
_BLOCK = re.compile(r"```(?:aktion|json)?\s*\n(\{.*?\})\s*(?:```|\Z)", re.DOTALL)
_FENCE_OPEN = re.compile(r"```(?:aktion|json)\s*\n")


def parse_actions(text: str) -> tuple[list[dict], str]:
    """Findet Aktions-Blöcke (JSON mit 'tool'). Gibt (Aktionen, Text ohne diese Blöcke)."""
    actions: list[dict] = []
    spans: list[tuple[int, int]] = []
    for m in _BLOCK.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            actions.append(obj)
            spans.append(m.span())

    # nur die erkannten Aktions-Blöcke aus dem Anzeigetext entfernen
    cleaned = text
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + cleaned[end:]
    return actions, cleaned.strip()


def unparsed_action_note(text: str, actions: list[dict]) -> str | None:
    """Hinweis, wenn ein Aktions-Zaun da ist, aber kein Block lesbar war
    (z.B. abgeschnittenes JSON). Sonst None."""
    if actions or not _FENCE_OPEN.search(text):
        return None
    return ("⚠ Aktionsblock nicht lesbar (JSON unvollständig?) – NICHTS ausgeführt. "
            "Bitte das Modell, die Aktion noch einmal zu senden.")


def resolve_tool(name: str) -> str | None:
    """Korrigiert kleine Tippfehler im Werkzeugnamen (z.B. 'beehl' -> 'befehl')."""
    if not name:
        return None
    candidates = list(ACTIONS) + ["subagenten"]   # subagenten wird in main behandelt
    if name in candidates:
        return name
    match = difflib.get_close_matches(name, candidates, n=1, cutoff=0.75)
    return match[0] if match else None


# --- Netz-Taint: Gemerktes aus derselben Runde wie Web-Inhalt braucht Rückfrage ---
# merken/skill_merken laufen normalerweise ohne Rückfrage (Selbstlernen soll
# nicht nerven). Ihr Text landet aber in späteren System-Prompts. Kam in dieser
# Runde schon etwas aus dem Netz (web_lesen/web_wiki/web_suche), könnte eine
# präparierte Seite das Modell zu „merk dir: ab jetzt …" verleitet haben – dann
# soll der Nutzer die Notiz sehen und freigeben, bevor sie dauerhaft wird.
_WEB_TOOLS = {"web_lesen", "web_wiki", "web_suche"}
_MEMORY_TOOLS = {"merken", "skill_merken"}
_web_taint = False


def reset_taint() -> None:
    """Zu Beginn jeder Nutzer-Runde aufrufen."""
    global _web_taint
    _web_taint = False


def web_tainted() -> bool:
    return _web_taint


def needs_confirm(action: dict) -> bool:
    tool = action.get("tool", "")
    spec = ACTIONS.get(tool)
    if not spec:
        return False
    if tool in _MEMORY_TOOLS and _web_taint:
        return True
    return bool(spec["confirm"])


def confirmation_preview(action: dict) -> str | None:
    """Vollständiger Zielinhalt vor einer Dateiänderung, ohne Schreibzugriff.

    Fehler beim Ermitteln der Vorschau werden an den Aufrufer weitergereicht:
    eine nicht prüfbare Änderung darf nicht durch einen Kurztext ersetzt werden.
    """
    tool = action.get("tool")
    if tool not in ("datei_schreiben", "datei_bearbeiten", "pdf_erstellen"):
        return None
    raw_path = action.get("pfad")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Für die Vorschau fehlt ein gültiger Zielpfad.")
    path = _pdf_target(raw_path) if tool == "pdf_erstellen" else Path(raw_path)
    if path.is_dir():
        raise ValueError("Das Ziel ist ein Ordner, keine Datei.")
    exists = path.exists()
    header = (f"Ziel: {path.resolve()}\n"
              + ("Vorhandene Datei wird ÜBERSCHRIEBEN."
                 if exists else "Neue Datei wird angelegt."))
    if tool == "datei_bearbeiten":
        original = path.read_text(encoding="utf-8")
        search = action.get("suchen")
        replacement = action.get("ersetzen", "")
        if not isinstance(search, str) or not isinstance(replacement, str):
            raise ValueError("Suchtext und Ersatz müssen Text sein.")
        count = original.count(search)
        if count == 0:
            raise ValueError("Suchtext nicht gefunden; die Datei kann so nicht bearbeitet werden.")
        content = original.replace(search, replacement)
        header += f"\n{count} Fundstelle(n) werden ersetzt."
    else:
        content = action.get("inhalt")
        if not isinstance(content, str):
            raise ValueError("Für die Vorschau fehlt der vollständige Inhalt.")
    if tool == "pdf_erstellen":
        title = action.get("titel") or action.get("title") or ""
        if title:
            header += f"\nPDF-Titel: {title}"
        label = "Vollständiger Markdown-Inhalt des neuen PDFs"
    else:
        label = "Vollständiger neuer Dateiinhalt"
    return f"{header}\n\n{label} ({len(content)} Zeichen):\n{content}"


def _taint_hinweis() -> str:
    if _web_taint:
        return ("\n    ⚠ In dieser Runde kam Text aus dem Netz – prüfe, ob diese Notiz wirklich "
                "von dir stammt und nicht von einer Webseite.")
    return ""


def describe(action: dict) -> str:
    """Kurze, lesbare Beschreibung einer Aktion für die Bestätigung."""
    t = action.get("tool", "?")
    g = action.get
    if t == "datei_lesen":      return f"Datei lesen:  {g('pfad', '?')}"
    if t == "bild_ansehen":     return f"Bild ansehen:  {g('pfad', '?')}"
    if t == "bildschirm_ansehen": return "Bildschirm fotografieren und ansehen (Screenshot)"
    if t == "ordner_auflisten": return f"Ordner auflisten:  {g('pfad', '.')}"
    if t == "ordner_erkennen":  return f"Ordner-Typ erkennen (ML):  {g('pfad', '.')}"
    if t == "dateien_suchen":   return f"Dateien suchen:  {g('muster', '?')}  in {g('pfad', '.')}"
    if t == "inhalt_suchen":    return f"Inhalt suchen:  '{g('muster', '?')}'  in {g('pfad', '.')}"
    if t == "web_lesen":        return f"Web lesen:  {g('url', '?')}"
    if t == "web_wiki":         return f"Wikipedia suchen:  {g('suche', '?')}"
    if t == "web_suche":        return f"Web durchsuchen:  {g('suche', '?')}"
    if t == "datei_schreiben":
        inhalt = g("inhalt", "")
        vorschau = (inhalt[:80] + "…") if len(inhalt) > 80 else inhalt
        return f"Datei schreiben:  {g('pfad', '?')}\n    Inhalt: {vorschau!r}"
    if t == "datei_bearbeiten":
        return (f"Datei bearbeiten:  {g('pfad', '?')}\n"
                f"    ersetze {g('suchen', '?')!r}\n    durch   {g('ersetzen', '')!r}")
    if t == "ordner_erstellen": return f"Ordner anlegen:  {g('pfad', '?')}"
    if t == "pdf_erstellen":
        inhalt = g("inhalt", "")
        return f"PDF erstellen:  {g('pfad', '?')}  ({len(inhalt)} Zeichen Markdown)"
    if t == "verschieben":      return f"Verschieben:  {g('von', '?')} → {g('nach', '?')}"
    if t == "loeschen":         return f"LÖSCHEN:  {g('pfad', '?')}"
    if t == "befehl":           return f"PowerShell ausführen:\n    {_befehl_text(action) or '?'}"
    if t == "abfragen":         return f"Abfragen (nur lesen):\n    {_befehl_text(action) or '?'}"
    if t == "zeitplan_anzeigen": return "Zeitplan anzeigen (NemiCLI-Aufgaben in Windows)"
    if t == "zeitplan":
        was = str(g("aktion", "?")).lower()
        if was.startswith("anl") or was in ("neu", "erstellen", "planen"):
            return (f"Zeitplan anlegen:  '{g('name', '?')}'  –  {g('wann', '?')}\n"
                    f"    Auftrag: {str(g('auftrag', ''))[:200]}")
        return f"Zeitplan {was}:  '{g('name', '?')}'"
    if t == "skill_merken":
        inhalt = str(g("inhalt", "")).strip().replace("\n", " ")
        kurz = inhalt if len(inhalt) <= 160 else inhalt[:159] + "…"
        return f"Skill merken:  {g('name', '?')}" + (f"\n    {kurz}" if kurz else "") + _taint_hinweis()
    if t == "merken":
        return f"Ins Langzeitgedächtnis:  {g('text', '?')}" + _taint_hinweis()
    if t == "ordner_lernen":    return f"Ordner-Typ lernen (ML):  {g('pfad', '?')} = {g('typ', '?')}"
    if t == "ml_status":        return "ML-Status / Ordner-Sinn-Bericht abrufen"
    if t == "bild_malen":
        return f"Bild malen 🎨:  {g('prompt', g('beschreibung', g('motiv', '?')))}"
    return f"Unbekannte Aktion: {t}"


async def run(action: dict) -> ActionResult:
    """Führt eine Aktion aus (im Thread, damit nichts blockiert)."""
    tool = action.get("tool", "")
    spec = ACTIONS.get(tool)
    if not spec:
        near = difflib.get_close_matches(tool, list(ACTIONS), n=1)
        hint = f" Meintest du '{near[0]}'?" if near else ""
        return ActionResult(f"Fehler: unbekanntes Werkzeug '{tool}'.{hint} "
                f"Verfügbare Werkzeuge: {', '.join(ACTIONS)}", ok=False)
    fehlend = [f for f in spec["felder"] if f not in action]
    if fehlend:
        return ActionResult(f"Fehler: es fehlen Felder {fehlend} für '{tool}'.", ok=False)
    global _web_taint
    if tool in _WEB_TOOLS:
        _web_taint = True
    try:
        result = await asyncio.to_thread(spec["func"], action)
        if isinstance(result, ActionResult):
            return result
        # Unsere einfachen Werkzeuge liefern nur auf ihrem Erfolgsweg Text.
        # Fehler und Quellen ohne Erfolgsstatus liefern explizit ActionResult.
        if isinstance(result, str):
            return ActionResult(result, ok=True)
        return ActionResult("Fehler: Werkzeug lieferte kein gültiges Ergebnis.", ok=False)
    except Exception as e:
        return ActionResult(f"Fehler bei '{tool}': {e}", ok=False)
