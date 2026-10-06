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
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import foldersense
import fremddaten
import learn
import memory
import pdfgen
import sicherheit
import snapshot
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
#              umbauen, und die KI liest ihren eigenen Quelltext nicht.
#   ÄNDERN   – KreaWork.json. Ansehen ist erlaubt (die KI soll Fragen zum
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


def _schluessel_steckt() -> bool:
    """Hat der Nutzer den Programm-Ordner mit /schluessel auf Zeit freigegeben?"""
    try:
        import sicherheit
        return sicherheit.schluessel_aktiv() is not None
    except Exception:
        return False


def _absolut_bases() -> list[Path]:
    """Alles, was die KI nicht einmal ansehen darf."""
    roh = [b for b in _ABSOLUT_FEST if not (b == _NEMI_ROOT and _schluessel_steckt())]
    for e in _sperrliste().get("pfade_absolut", []) or []:
        roh.append(os.path.expandvars(str(e)))
    return _saubere_pfade(roh)


def _schreibsperre_bases() -> list[Path]:
    """Alles, was die KI nicht ändern darf – das Absolute gehört dazu."""
    roh = [b for b in _ABSOLUT_FEST if not (b == _NEMI_ROOT and _schluessel_steckt())] + list(_NUR_LESEN_FEST)
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
    for path in paths:
        if path and (schutz := _schutzsoftware()) and (o := schutz.betroffen(path)) is not None:
            return f"Fehler: 🛡️ {o} – {schutz.HINWEIS}"
    return None


def _schutzsoftware():
    """Modul schutzsoftware, falls ladbar (Tests laden actions ohne tools im Pfad)."""
    try:
        import schutzsoftware
        return schutzsoftware
    except ImportError:
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

# Werkzeuge ohne harmlosen Anwendungsfall für die KI: fremde Programme über
# Windows-Hosts starten, Dateien laden/dekodieren, Protokolle leeren, Autostart,
# versteckte Fenster, Dateien verstecken oder freischalten, Dienste und Firewall.
_SYSTEMWERKZEUG = re.compile(
    r"(?i)((?<![\w-])(mshta|rundll32|regsvr32|wscript|cscript|certutil|bitsadmin|"
    r"start-bitstransfer|wevtutil|clear-eventlog|remove-eventlog|limit-eventlog|"
    r"unblock-file|new-service|attrib|installutil|msbuild|regasm|regsvcs|odbcconf|"
    r"cmstp|msiexec|forfiles|pcalua)(\.exe)?(?![\w-])|"
    r"currentversion\\+(run|runonce|runservices|policies\\+explorer\\+run)\b|"
    r"\\start menu\\programs\\startup|shell:(common )?startup|"
    r"-w(indowstyle)?\s+[\"']?hidden|-noni(nteractive)?\s+-w|"
    r"netsh\s+(advfirewall|firewall|interface|wlan|winhttp)\s+[^;|\n]*\b(set|add|delete|reset)\b)"
)

# Adminrechte sind für die KI ausgeschlossen: kein Weg, Rechte zu erhöhen.
_ADMIN = re.compile(
    r"(?i)((?<![\w-])(runas(\.exe)?|sudo|gsudo|psexec(64)?(\.exe)?)(?![\w-])|"
    r"-verb\s*[:=]?\s*[\"']?runas|[\"']runas[\"']|"
    r"-requiredrunlevel|/rl\s+highest|runlevel\s*highest)"
)


def _ist_admin() -> bool:
    """Läuft NemiCLI selbst mit Adminrechten?"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# Registry-Zweige, die zum System gehören (HKCU = Nutzer, bleibt erlaubt).
_REG_SYSTEM = re.compile(
    r"(?i)(?<![\w:])(hklm|hkey_local_machine|hkcr|hkey_classes_root|"
    r"hku|hkey_users|registry::hkey_local_machine)(?![\w])"
)


def prozent_variablen(text: str) -> str:
    """Nur %NAME% durch Umgebungsvariablen ersetzen. os.path.expandvars ersetzt auch
    $name – in PowerShell sind das Variablen ($_, $path, $home), keine Umgebung."""
    return re.sub(r"%([A-Za-z_][\w()]*)%", lambda m: os.environ.get(m.group(1), m.group(0)), text or "")


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
    roh = prozent_variablen(cmd or "")
    if not roh.strip():
        return None

    if (schutz := _schutzsoftware()) and (o := schutz.nennt(roh)):
        return f"Fehler: 🛡️ Der Befehl nennt {o}. {schutz.HINWEIS}"

    if (m := _SYSTEMWERKZEUG.search(roh)):
        return ("Fehler: 🛡️ Geschützt – der Befehl enthält ein Werkzeug, das Programme "
                f"verdeckt startet, Dateien lädt, Protokolle löscht oder sich im Autostart "
                f"einträgt ('{m.group(0).strip()}'). NemiCLI führt so etwas grundsätzlich nicht aus. "
                "Für Dateien gibt es datei_kopieren, oeffnen und datei_schreiben.")

    if (m := _ADMIN.search(roh)):
        return ("Fehler: 🛡️ Keine Adminrechte – der Befehl will Rechte erhöhen "
                f"('{m.group(0).strip()}'). Admin-Aufgaben erledigt der Nutzer selbst; "
                "sag ihm, was zu tun wäre.")

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
    # lesend=True (Einstufung durch sicherheit.befehl_harmlos) darf Systemorte
    # nennen – _nur_lesend() hat vorher sichergestellt, dass nichts geändert wird.
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
    text = fremddaten.rahmen("".join(out).rstrip("\n"), "datei", p.name)
    if ende < len(lines):
        text += (f"\n… gekürzt: Zeilen {ende + 1}–{len(lines)} fehlen noch. Weiter mit "
                 f"datei_lesen, pfad wie eben, ab: {ende + 1}")
    return text


def _bild_ansehen(a: dict) -> ActionResult:
    """Ein Bild von der Platte ins Gespräch holen: main hängt es in der nächsten
    Runde als echtes Bild an (Vision) – oder lässt den Bildbeschreiber beschreiben."""
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


def _anleitung_lesen(a: dict) -> ActionResult:
    """Ausführliche Anleitung zu einem Thema (kompakte Anleitung lokaler Modelle)."""
    import persona
    thema = str(a.get("thema") or "").strip().lower()
    text = persona.anleitung(thema)
    if not text:
        return ActionResult(f"Unbekanntes Thema {thema!r}. Möglich: " + ", ".join(persona.ANLEITUNGEN), ok=False)
    return ActionResult(text, ok=True)


def _projektordner(a: dict) -> Path | None:
    """Ordner für Abfragen im Projekt-Python: Feld `projekt`, sonst Coding-Projekt, sonst Workspace."""
    roh = str(a.get("projekt") or "").strip().strip('"')
    if roh and Path(roh).is_dir():
        return Path(roh)
    import coding
    import workspace
    return coding.arbeitsordner() or (Path(workspace.pfad()) if workspace.pfad() else None)


def _api_nachschlagen(a: dict) -> ActionResult:
    """Signatur, Doku und Version eines Namens aus der INSTALLIERTEN Bibliothek."""
    import pyumgebung
    text, ok = pyumgebung.nachschlagen(a.get("name"), _projektordner(a))
    return ActionResult(fremddaten.rahmen(text, "ausgabe", "Installation") if ok else text, ok=ok)


def _code_pruefen(a: dict) -> ActionResult:
    """ruff über eine Datei oder einen Ordner (Standard: das Projekt)."""
    import pyumgebung
    projekt = _projektordner(a)
    ziel = str(a.get("pfad") or "").strip().strip('"') or (str(projekt) if projekt else "")
    if not ziel:
        return ActionResult("Feld 'pfad' fehlt (Datei oder Ordner) – und kein Projekt aktiv.", ok=False)
    if (blocked := _read_guard(ziel)):
        return ActionResult(blocked, ok=False)
    p = Path(ziel)
    if not p.exists():
        return ActionResult(f"Nicht gefunden: {p}", ok=False)
    ergebnis = pyumgebung.ruff_pruefen([p], p if p.is_dir() else p.parent,
                                       zielversion=pyumgebung.ziel_version(projekt))
    if ergebnis is None:
        return ActionResult("ruff ist nicht installiert – /update installiert es (requirements).", ok=False)
    text, anzahl = ergebnis
    if anzahl:
        text += ("\nRegeln: E9 Syntax · F Namen/Importe · UP veralteter Python-Stil · B typische Fehler. "
                 "Beheben und erneut prüfen.")
    return ActionResult(text, ok=True)


def _paket_info(a: dict) -> ActionResult:
    import paketinfo
    text, ok = paketinfo.info(a.get("name"), _projektordner(a))
    return ActionResult(text, ok=ok)


def _seite_ansehen(a: dict) -> ActionResult:
    import seite
    ziel = str(a.get("ziel") or a.get("pfad") or a.get("url") or "").strip()
    if ziel and not re.match(r"^https?://", ziel, re.I) and (blocked := _read_guard(ziel)):
        return ActionResult(blocked, ok=False)

    def _zahl(k, standard):
        try:
            return int(a[k]) if a.get(k) not in (None, "") else standard
        except (TypeError, ValueError):
            return standard

    bild, text = seite.ansehen(ziel, _zahl("breite", seite.BREITE), _zahl("hoehe", seite.HOEHE))
    return ActionResult(text, ok=bild is not None, bilder=[str(bild)] if bild else None)


def _doku_suchen(a: dict) -> ActionResult:
    import doku
    if a.get("id") not in (None, ""):
        return ActionResult(fremddaten.rahmen(doku.lesen(a.get("id")), "suche", "Offline-Doku"), ok=True)
    text = doku.suchen(str(a.get("frage") or a.get("suche") or ""), str(a.get("quelle") or ""))
    return ActionResult(fremddaten.rahmen(text, "suche", "Offline-Doku"), ok=True)


def _menue_oeffnen(a: dict) -> ActionResult:
    """Nur der Weg außerhalb des Terminals (Browser, GUI): dort gibt es keine Auswahlmenüs.
    Im Terminal öffnet main._converse das Menü selbst."""
    import commands
    befehl, fehler = commands.menue_pruefen(a.get("befehl"))
    if fehler:
        return ActionResult(fehler, ok=False)
    return ActionResult(f"Menüs öffnen geht nur im NemiCLI-Fenster. Nenne dem Nutzer den Befehl {befehl}.",
                        ok=False)


def _theme_felder(a: dict) -> tuple:
    import ui
    return (a.get("name"), a.get("label") or a.get("beschreibung"), a.get("brand"), a.get("accent"),
            a.get("verlauf") or a.get("gradient"), ui.EINGEBAUT)


def _theme_erstellen(a: dict) -> ActionResult:
    """Eigenes Farbschema anlegen; der Nutzer wählt es danach selbst (/theme)."""
    import themes_eigen
    import ui
    try:
        name, palette = themes_eigen.anlegen(*_theme_felder(a))
    except ValueError as e:
        return ActionResult(f"Theme nicht angelegt: {e}", ok=False)
    ui.THEMES[name] = palette
    return ActionResult(f"Theme '{name}' ({palette['label']}) angelegt. Wählbar mit /theme {name} – "
                        "biete an, das /theme-Menü mit menue_oeffnen zu öffnen.", ok=True)


def _einstellung_aendern(a: dict) -> ActionResult:
    """Feste Liste von Einstellungen (tools/einstellungen.py) – fragt in jedem Modus."""
    import einstellungen
    try:
        return ActionResult(einstellungen.aendern(str(a.get("was") or ""), a.get("wert"), a.get("grund") or ""),
                            ok=True)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)


def _bild_fragen(a: dict) -> ActionResult:
    """Gezielte Frage zu einem Bild an den Bildbeschreiber (lokale Modelle ohne Bild-Eingang)."""
    if (blocked := _read_guard(a.get("pfad", ""))):
        return ActionResult(blocked, ok=False)
    p = Path(str(a.get("pfad", "")).strip().strip('"'))
    frage = str(a.get("frage") or "").strip()
    if not p.is_file():
        return ActionResult(f"Bild nicht gefunden: {p}", ok=False)
    if p.suffix.lower() not in _IMAGE_EXT:
        return ActionResult(f"{p.name} ist keine Bilddatei (png/jpg/webp/gif/bmp).", ok=False)
    if not frage:
        return ActionResult("Feld 'frage' fehlt: was soll am Bild nachgesehen werden?", ok=False)
    try:
        import bildbeschreiber
        if bildbeschreiber.finden() is None:
            return ActionResult("Kein Bildbeschreiber eingerichtet (Ordner Vision/ im Programm-Ordner).",
                                ok=False)
        antwort = bildbeschreiber.beschreiben(p, frage)
    except Exception as e:
        return ActionResult(f"Bildbeschreiber-Fehler: {e}", ok=False)
    return ActionResult(bildbeschreiber.block(p, antwort, frage), ok=True)


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
    return kopf + fremddaten.rahmen(_cut("\n".join(out)), "ordner", str(p))


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
    if not treffer:
        return "Keine Dateien gefunden."
    return fremddaten.rahmen(_cut("\n".join(treffer)), "suche", muster)


def _web_lesen(a: dict) -> ActionResult:
    return ActionResult(webfetch.fetch(a["url"], a.get("teil") or 1), ok=None)


def _web_wiki(a: dict) -> ActionResult:
    return ActionResult(webfetch.wikipedia(a["suche"], teil=a.get("teil") or 1), ok=None)


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
    return fremddaten.rahmen(_cut("\n".join(out)), "suche", muster) if out else "Keine Treffer."


# ---------------------------------------------------------------------------
# Verändernde Werkzeuge (immer mit Nachfrage)
# ---------------------------------------------------------------------------

def _datei_schreiben(a: dict) -> str | ActionResult:
    p = a["pfad"]
    if (blocked := _guard(p)):
        return ActionResult(blocked, ok=False)
    inhalt = a.get("inhalt", "")
    kopie = snapshot.sichern(p, "datei_schreiben") if Path(p).is_file() else None
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(inhalt)
    return f"Datei geschrieben: {p} ({len(inhalt)} Zeichen)" + _undo_hinweis(kopie) + _sofortcheck(p, inhalt)


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
    kopie = snapshot.sichern(p, "datei_bearbeiten")
    Path(p).write_text(text, encoding="utf-8")
    return f"Datei bearbeitet: {p} ({n}× ersetzt)" + _undo_hinweis(kopie) + _sofortcheck(p, text)


def _sofortcheck(pfad, inhalt: str) -> str:
    """Syntax/Struktur der gerade geschriebenen Datei – Funde gehen mit dem Ergebnis an die KI."""
    try:
        import sofortcheck
        return sofortcheck.hinweis(pfad, inhalt)
    except Exception:
        return ""


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
    # Wird am Ziel eine Datei überschrieben? Die vorher sichern – die Quelle
    # selbst geht nicht verloren, sie liegt danach am neuen Ort.
    kopie = snapshot.sichern(nach, "verschieben") if Path(nach).is_file() else None
    shutil.move(von, nach)
    return f"Verschoben/umbenannt: {von} → {nach}" + _undo_hinweis(kopie)


def _datei_kopieren(a: dict) -> str | ActionResult:
    von, nach = str(a.get("von") or ""), str(a.get("nach") or "")
    if not von or not nach:
        return ActionResult("Fehler: 'von' und 'nach' angeben.", ok=False)
    if (blocked := _read_guard(von) or _guard(nach)):   # Quelle lesen, Ziel schreiben
        return ActionResult(blocked, ok=False)
    quelle, ziel = Path(von), Path(nach)
    if not quelle.exists():
        return ActionResult(f"Fehler: {quelle} gibt es nicht.", ok=False)
    if ziel.is_dir():
        ziel = ziel / quelle.name
    if quelle.is_dir():
        if ziel.exists():
            return ActionResult(f"Fehler: {ziel} gibt es schon – einen neuen Zielordner angeben.", ok=False)
        if (n := sicherheit.anzahl_dateien(quelle, 5001)) > 5000:
            return ActionResult(f"Fehler: {quelle} enthält mehr als 5000 Dateien.", ok=False)
        shutil.copytree(quelle, ziel)
        return f"Ordner kopiert: {quelle} → {ziel} ({n} Dateien)"
    kopie = snapshot.sichern(str(ziel), "datei_kopieren") if ziel.is_file() else None
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(quelle, ziel)
    return f"Kopiert: {quelle} → {ziel}" + _undo_hinweis(kopie)


# Dateiarten, die `oeffnen` an das Standardprogramm gibt: Anzeigen, nicht Ausführen.
_OEFFNEN_ENDUNGEN = frozenset("""
.png .jpg .jpeg .gif .webp .bmp .svg .ico .tif .tiff .pdf .txt .md .log .json .csv .xml .yaml .yml
.html .htm .mp3 .wav .flac .ogg .m4a .mp4 .mkv .webm .mov .avi .docx .xlsx .pptx .odt .ods .odp .rtf
""".split())


def _oeffnen(a: dict) -> str | ActionResult:
    """Datei mit ihrem Standardprogramm bzw. Ordner im Explorer öffnen – ohne Shell."""
    p = Path(str(a.get("pfad") or "").strip().strip('"'))
    if not str(p) or str(p) == ".":
        return ActionResult("Fehler: 'pfad' angeben.", ok=False)
    if (blocked := _read_guard(str(p))):
        return ActionResult(blocked, ok=False)
    if not p.exists():
        return ActionResult(f"Fehler: {p} gibt es nicht.", ok=False)
    if p.is_file() and p.suffix.lower() not in _OEFFNEN_ENDUNGEN:
        return ActionResult(f"Fehler: '{p.suffix or 'ohne Endung'}' öffne ich nicht – nur Bilder, "
                            "Dokumente, Text und Medien. Programme und Skripte startet oeffnen nie.",
                            ok=False)
    os.startfile(str(p.resolve()))
    return f"Geöffnet: {p}"


def _loeschen(a: dict) -> str | ActionResult:
    p = a["pfad"]
    if (blocked := _guard(p)):
        return ActionResult(blocked, ok=False)
    if (stopp := sicherheit.grenze_pruefen(a)):     # Obergrenze – auch hier, nicht nur vor der Frage
        return ActionResult(stopp, ok=False)
    war_ordner = os.path.isdir(p)
    anzahl = sicherheit.anzahl_dateien(p)
    # Nicht vernichten, sondern in den Papierkorb (DATEN/Papierkorb) verschieben –
    # /undo holt es zurück. Nur was schon im Papierkorb liegt, ist wirklich weg.
    kopie = snapshot.sichern(p, "loeschen", verschieben=True)
    if kopie is None:
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    sicherheit.zaehle_loeschung(anzahl)
    was = f"Ordner gelöscht: {p} ({anzahl} Dateien)" if war_ordner else f"Datei gelöscht: {p}"
    rest = f" Löschungen diese Sitzung: {sicherheit.geloescht()} von {sicherheit.loesch_limit()}."
    return was + (_undo_hinweis(kopie) if kopie else " (endgültig – kein Undo)") + rest


def _undo_hinweis(kopie: str | None) -> str:
    return " – alter Stand im Papierkorb, /undo stellt ihn wieder her." if kopie else ""


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


# Laufender `befehl`: Startzeit und letzte Ausgabezeilen für die Live-Anzeige,
# Abbruch per befehl_stoppen() (Esc). Keine Zeitgrenze – Downloads und
# Installationen dürfen so lange dauern, wie sie brauchen.
_BEFEHL_STATUS: dict = {"start": None, "zeilen": []}
_BEFEHL_STOPP = threading.Event()
_ZEILEN_LIVE = 4                                 # so viele Zeilen zeigt die Live-Anzeige
_AUSGABE_MAX = 400_000                           # Zeichen je Strom, danach nur noch das Ende


def befehl_status() -> dict | None:
    """{'sekunden', 'zeilen'} solange ein befehl läuft, sonst None."""
    start = _BEFEHL_STATUS["start"]
    if start is None:
        return None
    return {"sekunden": time.monotonic() - start, "zeilen": list(_BEFEHL_STATUS["zeilen"])}


def befehl_stoppen() -> None:
    _BEFEHL_STOPP.set()


def _prozessbaum_beenden(pid: int) -> None:
    try:
        import psutil
        eltern = psutil.Process(pid)
        for p in [*eltern.children(recursive=True), eltern]:
            try:
                p.kill()
            except psutil.Error:
                pass
    except Exception:
        pass


def _powershell(argv: list[str], ordner: Path, timeout: float | None = None):
    """Startet PowerShell, liest stdout/stderr zeilenweise mit (Live-Anzeige)
    und wartet. SimpleNamespace(returncode, stdout, stderr).
    subprocess.TimeoutExpired bei Abbruch (Esc) oder Zeitüberschreitung."""
    from types import SimpleNamespace
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    try:
        import coding
        if coding.aktiv():              # Python zeigt Veraltet-Warnungen sonst meist nicht an
            env["PYTHONWARNINGS"] = "default::DeprecationWarning,default::PendingDeprecationWarning"
    except Exception:
        pass
    p = subprocess.Popen(
        argv, cwd=str(ordner), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        # Ohne Konsole beim Elternprozess (Wache, Zeitplan) würde Windows
        # sonst für jede PowerShell ein eigenes Fenster aufmachen.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    teile: dict[str, list[str]] = {"out": [], "err": []}

    def lesen(strom, ziel: list[str]) -> None:
        groesse = 0
        for zeile in strom:
            groesse += len(zeile)
            ziel.append(zeile)
            while groesse > _AUSGABE_MAX and len(ziel) > 1:
                groesse -= len(ziel.pop(0))
            if zeile.strip():
                live = _BEFEHL_STATUS["zeilen"]
                live.append(zeile.rstrip()[-200:])
                del live[:-_ZEILEN_LIVE]

    leser = [threading.Thread(target=lesen, args=(p.stdout, teile["out"]), daemon=True),
             threading.Thread(target=lesen, args=(p.stderr, teile["err"]), daemon=True)]
    _BEFEHL_STOPP.clear()
    _BEFEHL_STATUS.update(start=time.monotonic(), zeilen=[])
    try:
        for t in leser:
            t.start()
        ende = None if timeout is None else time.monotonic() + timeout
        while p.poll() is None:
            if _BEFEHL_STOPP.is_set() or (ende is not None and time.monotonic() > ende):
                _prozessbaum_beenden(p.pid)
                p.wait(timeout=10)
                for t in leser:
                    t.join(timeout=2)
                raise subprocess.TimeoutExpired(argv, timeout or 0, output="".join(teile["out"]),
                                                stderr="".join(teile["err"]))
            time.sleep(0.1)
        for t in leser:
            t.join(timeout=5)
        return SimpleNamespace(returncode=p.returncode, stdout="".join(teile["out"]),
                               stderr="".join(teile["err"]))
    finally:
        _BEFEHL_STATUS.update(start=None, zeilen=[])


def _befehl(a: dict, lesend: bool = False) -> ActionResult:
    """`befehl`; im Coding-Assistenten mit Hinweis auf gemeldete Veraltet-Warnungen."""
    res = _befehl_roh(a, lesend)
    try:
        import coding
        if not coding.aktiv():
            return res
        import pyumgebung
        if re.search(r"\b(pip|uv)\b", _befehl_text(a), re.I):
            pyumgebung.vergessen()                   # Pakete geändert: Umgebung neu lesen
        if (hinweis := pyumgebung.veraltet_hinweis(res.text)):
            return ActionResult(res.text + hinweis, ok=res.ok, returncode=res.returncode)
    except Exception:
        pass
    return res


def _befehl_roh(a: dict, lesend: bool = False) -> ActionResult:
    cmd = _befehl_text(a)
    if not cmd.strip():
        return ActionResult("Fehler: kein Befehl angegeben. Schreib den auszuführenden Befehl ins "
                "Feld 'befehl', z.B. {\"tool\": \"befehl\", \"befehl\": \"Get-Date\"}.", ok=False)
    if _ist_admin():
        return ActionResult("Fehler: 🛡️ NemiCLI läuft mit Adminrechten – in diesem Zustand führt "
                            "die KI keine Befehle aus. NemiCLI ohne Adminrechte starten.", ok=False)
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
        # Text (z.B. Zeilen nativer Programme wie pip) geht sofort durch, damit
        # die Live-Anzeige ihn sieht; nur Objekte werden gruppenweise formatiert.
        "try {\n& {\n" + cmd + "\n} | ForEach-Object -Begin { $nemiG=@(); $nemiK=$null } "
        "-Process { if ($_ -is [string]) { if ($nemiG.Count) { $nemiG | Out-String -Width 200; "
        "$nemiG=@() }; $nemiK=$null; $_; return }; $k = if ($_.PSObject.TypeNames[0] -like "
        "'Microsoft.PowerShell.Commands.Internal.Format.*') { '__format__' } "
        "else { $_.PSObject.Properties.Name -join ',' }; "
        "if ($null -ne $nemiK -and $k -ne $nemiK) { $nemiG | Out-String -Width 200; $nemiG=@() }; "
        "$nemiK=$k; $nemiG+=$_ } -End { if ($nemiG.Count) { $nemiG | Out-String -Width 200 } }\n"
        "$nemiCommandOk=$?; $nemiNativeExitCode=$LASTEXITCODE;\n"
        "if ($nemiNativeExitCode -ne 0) { exit $nemiNativeExitCode };\n"
        "if (-not $nemiCommandOk) { exit 1 };\n"
        "if ($Error.Count -gt 0) { exit 250 };\n"
        "exit 0\n"
        "} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }"
    )
    import coding
    import workspace
    ordner = workspace.pfad() or coding.arbeitsordner() or Path(os.getcwd())
    try:
        r = _powershell(["powershell", "-NoProfile", "-Command", wrapped], ordner)
    except subprocess.TimeoutExpired as e:
        rest = "\n".join(t.strip() for t in (e.output, e.stderr) if t and t.strip())
        text = ("Befehl vom Nutzer abgebrochen (Esc). Nicht von selbst neu starten – frag, "
                "wie es weitergehen soll.")
        if rest:
            text += "\nAusgabe bis dahin:\n" + fremddaten.rahmen(_cut(rest), "ausgabe", "PowerShell")
        return ActionResult(text, ok=False, returncode=None)
    out = "\n".join(part.strip() for part in (r.stdout, r.stderr) if part and part.strip())
    # Was PowerShell zurückgibt (Prozessnamen, Dateiinhalte, Fehlertexte), sind
    # Daten – eingerahmt und auf Befehlsmuster geprüft, wie Datei- und Web-Inhalt.
    if out:
        out = fremddaten.rahmen(_cut(out), "ausgabe", "PowerShell")
    # 250 = der Befehl lief, aber unterwegs wurden Fehler unterdrückt
    # (-ErrorAction SilentlyContinue, z.B. Zugriff verweigert beim Rekursiv-
    # Suchen). Vorher zählte das als „fehlgeschlagen“, obwohl die Ausgabe da
    # war – die Persönlichkeit hat dann brauchbare Ergebnisse weggeworfen.
    if r.returncode == 250:
        note = ("(Hinweis: dabei wurden Fehler unterdrückt – Zugriff verweigert o.ä. – "
                "die Ausgabe kann unvollständig sein.)")
        return ActionResult((out + "\n" if out else "(kein Output)\n") + note, ok=True, returncode=0)
    if r.returncode != 0:
        text = f"Befehl fehlgeschlagen (Exitcode {r.returncode})."
        if out:
            text += "\n" + out
        elif r.returncode == 1:
            # Exitcode 1 ohne ein Wort Ausgabe: PowerShell selbst hätte den
            # Fehler in den catch-Zweig geschrieben. So sieht es aus, wenn der
            # Virenschutz die Befehlszeile abgefangen hat (Bitdefender: „Schädliche
            # Befehlszeile erkannt“ – trifft lange Einzeiler mit Prozesslisten
            # und Signaturprüfungen). Chat 19.09.2026: achtmal in einer Stunde.
            text += ("\nKeine Ausgabe und kein Fehlertext – so sieht es aus, wenn der Virenschutz "
                     "(Bitdefender) die Befehlszeile geblockt hat. Lange Einzeiler, die Prozesse "
                     "auflisten und Signaturen prüfen, treffen seine Muster. Teile den Befehl in "
                     "kleine Einzelbefehle auf (eine Sache pro Aufruf). Klappt es dann immer noch "
                     "nicht, sag dem Nutzer, dass Bitdefender geblockt hat – er sieht es unter "
                     "Bitdefender → Benachrichtigungen.")
        return ActionResult(text, ok=False, returncode=r.returncode)
    return ActionResult(out if out else "(kein Output)", ok=True, returncode=0)


# ---------------------------------------------------------------------------
# Lese-Prüfung für PowerShell-Befehle
# ---------------------------------------------------------------------------
# _nur_lesend() entscheidet, ob ein `befehl` nur liest (sicherheit.befehl_harmlos
# stuft ihn dann niedriger ein). Fail-safe: im Zweifel nein.
#   1. Jedes Cmdlet (Verb-Nomen) muss ein Lese-Verb tragen. Wörter mit Bindestrich,
#      deren erster Teil KEIN PowerShell-Verb ist ("svc-host"), sind Argumente.
#   2. Jeder Befehlsanfang muss ein Cmdlet, ein erlaubter Alias, ein erlaubtes
#      natives Programm, ein Schlüsselwort oder eine Variable sein.
#   3. Keine Umleitung in Dateien, kein & "pfad", kein Dot-Sourcing, keine
#      schreibenden .NET-Aufrufe, kein eigener Netz-Zugriff.

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
    """Feste Systemabfrage im eigenen Prozess (tools/systemabfrage.py)."""
    import systemabfrage
    if not (a.get("was") or a.get("art")):
        hinweis = ("Fehler: 'abfragen' braucht das Feld 'was'. " if not _befehl_text(a) else
                   "Fehler: 'abfragen' nimmt keine Befehle mehr, sondern das Feld 'was'. ")
        return ActionResult(hinweis + "Beispiel: {\"tool\": \"abfragen\", \"was\": \"prozesse\", "
                            "\"filter\": \"chrome\"}\n" + systemabfrage.hilfe(), ok=False)
    pfad = a.get("pfad") or ""
    if pfad and (blocked := _read_guard(pfad)):
        return ActionResult(blocked, ok=False)
    try:
        out = systemabfrage.ausfuehren(a)
    except systemabfrage.AbfrageFehler as e:
        return ActionResult(f"Fehler: {e}", ok=False)
    except Exception as e:
        return ActionResult(f"Abfrage fehlgeschlagen: {e}", ok=False)
    # Prozessnamen, Pfade, Registry-Werte sind Daten – eingerahmt wie Datei-Inhalt.
    return ActionResult(fremddaten.rahmen(_cut(out), "ausgabe", "Systemabfrage"), ok=True, returncode=0)


# ---------------------------------------------------------------------------
# code_ausfuehren – Python im AppContainer (sandbox.py)
# ---------------------------------------------------------------------------
_CODE_MAX = 1_000_000                            # Bytes je Datei


_MODULNAME = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*$")


def _sandbox_ergebnis(e, lauf: Path, timeout: int, was: str = "Ausgeführt") -> ActionResult:
    if e.zeit_ueberschritten:
        kopf = f"Zeitüberschreitung nach {timeout} s – Lauf beendet."
    else:
        kopf = f"Exitcode {e.code}."
    teile = [p for p in (e.ausgabe.strip(), e.fehler.strip()) if p]
    text = (f"{was} in der Sandbox (Ordner {lauf}). {kopf}"
            + ("\n" + fremddaten.rahmen(_cut("\n".join(teile)), "ausgabe", "Sandbox") if teile
               else " (keine Ausgabe)"))
    return ActionResult(text, ok=(e.code == 0), returncode=e.code if e.code is not None else -1)


def _code_ausfuehren(a: dict) -> ActionResult:
    """Python im AppContainer. Drei Formen:
      code   – Text wird in einen eigenen Lauf-Ordner geschrieben und ausgeführt
      pfad   – liegt die Datei in einem freigegebenen Projekt, läuft sie dort
               (Projekt nur lesbar), sonst als Kopie im Lauf-Ordner
      modul  – python -m <modul> <argumente> (z.B. pytest), Lauf-Ordner als Arbeitsordner
    Der Code sieht nur den Lauf-Ordner, freigegebene Projekte und das Internet."""
    import sandbox
    from datetime import datetime
    code, pfad = a.get("code"), str(a.get("pfad") or "").strip()
    modul = str(a.get("modul") or "").strip()
    argumente = a.get("argumente") or []
    if isinstance(argumente, str):
        argumente = argumente.split()
    argumente = [str(x) for x in argumente][:50]
    if not (isinstance(code, str) and code.strip()) and not pfad and not modul:
        return ActionResult("Fehler: 'code' (Python-Text), 'pfad' (eine .py-Datei) oder 'modul' "
                            "(z.B. pytest) angeben.", ok=False)
    if modul and not _MODULNAME.match(modul):
        return ActionResult(f"Fehler: '{modul}' ist kein Modulname.", ok=False)
    projekt = None
    if pfad:
        if (blocked := _read_guard(pfad)):
            return ActionResult(blocked, ok=False)
        quelle = Path(pfad)
        if not quelle.is_file() or quelle.suffix.lower() != ".py":
            return ActionResult(f"Fehler: {quelle} ist keine .py-Datei.", ok=False)
        if quelle.stat().st_size > _CODE_MAX:
            return ActionResult("Fehler: Datei größer als 1 MB.", ok=False)
        projekt = sandbox.projekt_von(quelle)
    try:
        timeout = max(5, min(int(a.get("timeout") or 60), 300))
    except (TypeError, ValueError):
        timeout = 60
    lauf = sandbox.ARBEIT / datetime.now().strftime("lauf_%Y%m%d_%H%M%S_%f")
    try:
        lauf.mkdir(parents=True)
        if modul:
            py = sandbox.vorbereiten()
            e = sandbox.ausfuehren([str(py), "-X", "utf8", "-m", modul, *argumente], lauf, timeout,
                                   pythonpfad=sandbox.projekte())
        elif pfad and projekt is not None:
            # beschreibbares Projekt: im Ordner des Skripts (wie `cd …; python x.py`)
            vor_ort = None if sandbox.schreibbar(projekt) else lauf
            e = sandbox.python_ausfuehren(quelle, timeout, ordner=vor_ort, argumente=argumente)
        else:
            ziel = lauf / (quelle.name if pfad else "programm.py")
            if pfad:
                shutil.copy2(quelle, ziel)
            else:
                ziel.write_text(code, encoding="utf-8")
            e = sandbox.python_ausfuehren(ziel, timeout, argumente=argumente)
    except sandbox.SandboxFehler as exc:
        return ActionResult(f"Fehler: Sandbox – {exc}", ok=False)
    return _sandbox_ergebnis(e, lauf, timeout)


def _paket_installieren(a: dict) -> ActionResult:
    """pip install im AppContainer – nur in den Paketordner der Sandbox."""
    import sandbox
    pakete = a.get("pakete") or a.get("paket") or []
    if isinstance(pakete, str):
        pakete = pakete.replace(",", " ").split()
    try:
        e = sandbox.pakete_installieren([str(p) for p in pakete][:20])
    except sandbox.SandboxFehler as exc:
        return ActionResult(f"Fehler: Sandbox – {exc}", ok=False)
    return _sandbox_ergebnis(e, sandbox.ARBEIT / sandbox.PAKETE_ORDNER, 300, "Installiert")


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


def _gedaechtnis_suchen(a: dict) -> ActionResult:
    import agentrag
    try:
        return ActionResult(agentrag.suchen(a), ok=None)
    except Exception as e:
        return ActionResult(f"Gedächtnis nicht durchsuchbar: {e}", ok=False)


def _gedaechtnis_lesen(a: dict) -> ActionResult:
    import agentrag
    try:
        return ActionResult(agentrag.lesen(a), ok=None)
    except Exception as e:
        return ActionResult(f"Gedächtnis nicht lesbar: {e}", ok=False)


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


def _wahr(wert) -> bool:
    """JSON-Feld als Ja/Nein – auch „ja“, "true", 1."""
    return wert is True or str(wert).strip().lower() in ("true", "ja", "1", "yes")


def _skill_laden(a: dict) -> ActionResult:
    import skills
    text = skills.laden_text(str(a.get("name") or ""))
    if text is None:
        namen = ", ".join(s.name for s in skills.alle()) or "noch keine"
        return ActionResult(f"Kein Skill „{a.get('name')}“. Vorhanden: {namen}", ok=False)
    return ActionResult(text)


def _skill_ausbessern(a: dict) -> ActionResult:
    import skills
    try:
        pfad = skills.ausbessern(a.get("name"), a.get("suchen"), a.get("ersetzen"))
    except (ValueError, OSError) as e:
        return ActionResult(f"Skill nicht geändert: {e}", ok=False)
    return ActionResult(f"Skill ausgebessert: {pfad}")


def _skill_schreiben(a: dict) -> ActionResult:
    import skills
    try:
        pfad = skills.schreiben(a.get("name"), a.get("beschreibung"), a.get("wann"), a.get("anleitung"),
                                fuer_alle=_wahr(a.get("fuer_alle")))
    except (ValueError, OSError) as e:
        return ActionResult(f"Skill nicht gespeichert: {e}", ok=False)
    return ActionResult(f"Skill gespeichert: {pfad}")


def _merken(a: dict) -> str:
    """Langzeitgedächtnis: einen dauerhaften Fakt über Nutzer/PC speichern.

    Läuft ein Workspace, landet das Gemerkte im PROJEKT (.workspace/memory.md)
    statt in NemiCLIs learned/ - so bleibt das Wissen beim Projekt und geht
    nicht verloren, wenn der Ordner den Rechner wechselt."""
    text = a.get("text") or a.get("inhalt") or a.get("fakt") or ""
    kind = a.get("art") or a.get("kind") or "fakt"
    if (grund := memory.verdaechtig(str(text))):
        return ActionResult(f"Nicht gemerkt: {grund}.", ok=False)
    alt = str(a.get("ersetzt") or "").strip().lstrip("#")
    if alt.isdigit():
        e = memory.ersetzen(int(alt), text, kind)
        if e is None:
            return ActionResult(f"Nicht ersetzt: {memory.letzter_grund or 'unbekannt'}.", ok=False)
        return f"Ersetzt ✅ #{e['id']} ({e['kind']}): {e['text']}"
    import workspace
    if workspace.aktiv():
        ziel = workspace.memory_anhaengen(text, str(kind))
        if ziel is None:
            return "Nichts gespeichert (leerer Text)."
        return f"Ins Projekt-Gedächtnis geschrieben ({kind}): {ziel}"
    e = memory.remember(text, kind)
    if e is None:
        if memory.letzter_grund:
            return ActionResult(f"Nicht gemerkt: {memory.letzter_grund}.", ok=False)
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


def _figur(a: dict, szene: str) -> tuple[str, int | None] | ActionResult:
    """Mit figur/outfit/pose: Prompt aus der Charakter-Datei (Kern · Outfit · Pose · Szene · Stil)
    und ihr Referenz-Seed (None bei neues_gesicht). Sonst bleibt der Prompt, wie er ist."""
    if not (_wahr(a.get("figur")) or a.get("outfit") or a.get("pose")):
        return szene, None
    import charakter
    d = charakter.laden()
    if d is None:
        return ActionResult("Fehler: Es gibt noch keine Charakter-Datei für diese Persönlichkeit. "
                            "Erst mit charakter_aendern anlegen (kern, stil, outfits, posen).", ok=False)
    return (charakter.prompt_bauen(d, szene, a.get("outfit"), a.get("pose")),
            charakter.seed(d, _wahr(a.get("neues_gesicht"))))


def _bild_malen(a: dict) -> str | ActionResult:
    """Malt ein Bild mit dem aktiven Bild-Motor (Krea 2, WebUI oder ComfyUI) und öffnet es.
    Felder: prompt (Pflicht); optional steps/size('1024x1024')/seed/model, bei WebUI/ComfyUI neg;
    figur/outfit/pose/neues_gesicht für die Figur aus der Charakter-Datei."""
    import imagegen
    prompt = (a.get("prompt") or a.get("beschreibung") or a.get("motiv")
              or a.get("text") or "").strip()
    if not prompt:
        return ActionResult("Fehler: kein Prompt. Feld 'prompt' mit der Bildbeschreibung füllen.", ok=False)
    if (reason := imagegen.missing_reason_aktiv()):
        return ActionResult("Fehler: Der Bild-Motor kann gerade nicht malen. " + reason, ok=False)

    def _int(k):
        try:
            return int(a[k]) if a.get(k) not in (None, "") else None
        except Exception:
            return None

    fig = _figur(a, prompt)
    if isinstance(fig, ActionResult):
        return fig
    prompt, figur_seed = fig
    seed = _int("seed") if _int("seed") is not None else figur_seed

    # Ein erfundener Modellname soll das Bild nicht verhindern: dann das gewählte Standard-Modell.
    modell, hinweis = imagegen.modell_aufloesen(a.get("model"))
    _BILD_STATUS["msg"] = "starte …"
    try:
        path = imagegen.paint(
            prompt,
            model=modell,
            neg=a.get("neg") or a.get("negativ"),
            steps=_int("steps"),
            size=_parse_size(a.get("size") or a.get("groesse")),
            seed=seed,
            on_status=lambda m: _BILD_STATUS.__setitem__("msg", m),
        )
    except Exception as e:
        _BILD_STATUS["msg"] = None               # Balken im Haupt-Loop beenden
        return ActionResult(f"Fehler beim Malen: {e}", ok=False)

    text = f"Bild fertig 🎨✅ gespeichert: {path}{hinweis}"
    try:
        besser = imagegen.gesicht_nachbessern(
            path, on_status=lambda m: _BILD_STATUS.__setitem__("msg", m))
    except Exception as e:
        besser = None
        text += f"\nGesicht nachbessern übersprungen: {e}"
    if besser:
        path = besser
        text += f"\nGesicht nachgebessert: {besser}"
    _BILD_STATUS["msg"] = None                   # Balken beenden

    try:
        os.startfile(path)                       # fertiges Bild anzeigen (Windows)
    except Exception as e:
        return ActionResult(text + f"\nÖffnen fehlgeschlagen: {e}", ok=False)
    return text + "\nÖffnen beim Anzeigeprogramm angefordert."


MAX_SERIE = 30


def _bild_serie(a: dict) -> ActionResult:
    """Mehrere Bilder in einem Auftrag, gleicher Seed für alle (gleiche Figur/Szene, andere Varianten).
    Felder: varianten (Liste: Text oder {prompt, outfit, pose, size}); optional figur, neues_gesicht,
    size, steps, seed, model."""
    import random
    import imagegen
    varianten = a.get("varianten") or a.get("bilder")
    if isinstance(varianten, str):
        varianten = [varianten]
    if not isinstance(varianten, list) or not varianten:
        return ActionResult("Fehler: 'varianten' fehlt – eine Liste, je Bild ein Text oder "
                            '{"prompt": …, "outfit": …, "pose": …}.', ok=False)
    if len(varianten) > MAX_SERIE:
        return ActionResult(f"Fehler: höchstens {MAX_SERIE} Bilder je Serie (hier {len(varianten)}).", ok=False)
    if (reason := imagegen.missing_reason_aktiv()):
        return ActionResult("Fehler: Der Bild-Motor kann gerade nicht malen. " + reason, ok=False)

    def _zahl(k):
        try:
            return int(a[k]) if a.get(k) not in (None, "") else None
        except (TypeError, ValueError):
            return None

    seed, steps = _zahl("seed"), _zahl("steps")
    modell, hinweis = imagegen.modell_aufloesen(a.get("model"))
    n, fertig, fehler = len(varianten), [], []
    for i, v in enumerate(varianten, 1):
        v = v if isinstance(v, dict) else {"prompt": str(v)}
        felder = {k: a.get(k) for k in ("figur", "neues_gesicht")} | v
        fig = _figur(felder, str(v.get("prompt") or v.get("szene") or "").strip())
        if isinstance(fig, ActionResult):
            _BILD_STATUS["msg"] = None
            return fig
        prompt, figur_seed = fig
        if seed is None:                          # einmal für die ganze Serie
            seed = figur_seed if figur_seed is not None else random.randint(1, 2 ** 31 - 1)
        melde = (lambda m, i=i: _BILD_STATUS.__setitem__("msg", f"Bild {i}/{n} · {m}"))
        melde("starte …")
        try:
            pfad = imagegen.paint(prompt, model=modell, neg=v.get("neg") or a.get("neg"), steps=steps,
                                  size=_parse_size(v.get("size") or a.get("size")), seed=seed, on_status=melde)
            try:
                pfad = imagegen.gesicht_nachbessern(pfad, on_status=melde) or pfad
            except Exception:
                pass
            fertig.append(pfad)
            try:
                os.startfile(pfad)                # jedes fertige Bild gleich anzeigen, wie bei bild_malen
            except Exception:
                pass
        except Exception as e:
            fehler.append(f"Bild {i}: {e}")
    _BILD_STATUS["msg"] = None
    text = (f"Serie fertig 🎨✅ {len(fertig)}/{n} Bilder, Seed {seed}{hinweis}:\n"
            + "\n".join(f"  {p}" for p in fertig) + ("\n" + "\n".join(fehler) if fehler else ""))
    return ActionResult(text, ok=bool(fertig) and not fehler)


def _charakter_zeigen(a: dict) -> ActionResult:
    import charakter
    return ActionResult(charakter.zeigen(charakter.laden()), ok=True)


def _charakter_aendern(a: dict) -> ActionResult:
    import charakter
    text, ok = charakter.aendern(a)
    return ActionResult(text, ok=ok)


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




# ---------------------------------------------------------------------------
# Systemwache – Werkzeuge der Persönlichkeit (Logik in tools/wache/werkzeuge.py)
# ---------------------------------------------------------------------------

def _coding_start(a: dict) -> str:
    import coding
    erg = coding.starten(str(a.get("projekt") or ""), str(a.get("aufgabe") or ""))
    text = f"🟦 Coding-Assistent ist an. Projekt: {erg['projekt']}"
    if not erg["projekt"].exists():
        text += " (Ordner gibt es noch nicht – leg ihn als ersten Schritt an)"
    if erg["liste"] is not None:
        return (text + "\nEs gibt schon eine Todo-Liste – mach dort weiter:\n"
                + coding.text_fuer_modell(erg["liste"]))
    return text + "\nNächster Schritt: verstehen, ggf. recherchieren, dann todo \"anlegen\"."


def _todo(a: dict) -> str:
    import coding

    def befehl_ausfuehren(cmd: str) -> tuple[bool, str]:
        res = _befehl({"befehl": cmd})
        return bool(res.ok), res.text
    return coding.ausfuehren(a, befehl_ausfuehren)


def _plan(a: dict) -> str | ActionResult:
    import plan
    text, ok = plan.ausfuehren(a)
    return text if ok else ActionResult(text, ok=False)


def _wache(name: str, a: dict) -> str | ActionResult:
    try:
        from wache import werkzeuge as W
    except Exception as exc:
        return ActionResult(f"Die Wache ist nicht verfügbar: {exc}", ok=False)
    fn = getattr(W, name)
    try:
        if name in ("bewerten", "justieren"):
            return fn(a, wer=_wer())
        return fn(a)
    except Exception as exc:
        return ActionResult(f"Fehler in wache_{name}: {exc}", ok=False)


def _wer() -> str:
    try:
        import persoenlichkeiten as PS
        return PS.active().name
    except Exception:
        return "Persönlichkeit"


def _kugel(a: dict) -> str | ActionResult:
    """Die Persönlichkeit steuert ihre Schwebekugel (Bild, Blase, Geste, Platz) – tools/wache/steuerung.py."""
    try:
        from wache import steuerung
        return steuerung.steuern(a, wer=_wer())
    except Exception as exc:
        return ActionResult(f"Kugel nicht erreichbar: {exc}", ok=False)


def _wache_status(a): return _wache("status", a)
def _wache_alarme(a): return _wache("alarme", a)
def _wache_ereignisse(a): return _wache("ereignisse", a)
def _wache_inventar(a): return _wache("inventar", a)
def _wache_bewerten(a): return _wache("bewerten", a)
def _wache_justieren(a): return _wache("justieren", a)


ACTIONS: dict[str, dict] = {
    # lesend
    "datei_lesen":      {"func": _datei_lesen,      "confirm": False, "felder": ["pfad"]},
    "bild_ansehen":     {"func": _bild_ansehen,     "confirm": False, "felder": ["pfad"]},
    "bild_fragen":      {"func": _bild_fragen,      "confirm": False, "felder": ["pfad", "frage"]},
    "anleitung_lesen":  {"func": _anleitung_lesen,  "confirm": False, "felder": ["thema"]},
    "menue_oeffnen":    {"func": _menue_oeffnen,    "confirm": False, "felder": ["befehl"]},
    # Coding: aktuell bleiben – alles nur lesend
    "api_nachschlagen": {"func": _api_nachschlagen, "confirm": False, "felder": ["name"]},
    "code_pruefen":     {"func": _code_pruefen,     "confirm": False, "felder": []},
    "paket_info":       {"func": _paket_info,       "confirm": False, "felder": ["name"]},
    "seite_ansehen":    {"func": _seite_ansehen,    "confirm": False, "felder": []},
    "doku_suchen":      {"func": _doku_suchen,      "confirm": False, "felder": []},
    "ordner_auflisten": {"func": _ordner_auflisten, "confirm": False, "felder": []},
    "dateien_suchen":   {"func": _dateien_suchen,   "confirm": False, "felder": ["muster"]},
    "inhalt_suchen":    {"func": _inhalt_suchen,    "confirm": False, "felder": ["muster"]},
    "ordner_erkennen":  {"func": _ordner_erkennen,  "confirm": False, "felder": []},
    # feste Systemabfragen im eigenen Prozess (systemabfrage.py)
    "abfragen":         {"func": _abfragen,         "confirm": False, "felder": []},
    "zeitplan_anzeigen": {"func": _zeitplan_anzeigen, "confirm": False, "felder": []},
    # eigenes Gedächtnis (memory.db): hybride Suche und ganze Quelle lesen
    "gedaechtnis_suchen": {"func": _gedaechtnis_suchen, "confirm": False, "felder": ["frage"]},
    "gedaechtnis_lesen":  {"func": _gedaechtnis_lesen,  "confirm": False, "felder": ["ref"]},
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
    "datei_kopieren":   {"func": _datei_kopieren,   "confirm": True,  "felder": ["von", "nach"]},
    # nur Anzeige-Dateiarten (Bilder, Dokumente, Medien) und Ordner – nichts Ausführbares
    "oeffnen":          {"func": _oeffnen,          "confirm": False, "felder": ["pfad"]},
    "loeschen":         {"func": _loeschen,         "confirm": True,  "felder": ["pfad"]},
    "befehl":           {"func": _befehl,           "confirm": True,  "felder": []},
    # NemiCLI-Einstellungen aus fester Liste (Internet-Allowlist) – Prüffenster in jedem Modus
    "einstellung_aendern": {"func": _einstellung_aendern, "confirm": True, "felder": ["was", "wert"]},
    "theme_erstellen":  {"func": _theme_erstellen,  "confirm": True, "felder": ["name", "brand", "accent"]},
    # Python im AppContainer: nur Lauf-Ordner, freigegebene Projekte + Internet
    "code_ausfuehren":  {"func": _code_ausfuehren,  "confirm": True,  "felder": []},
    "paket_installieren": {"func": _paket_installieren, "confirm": True, "felder": ["pakete"]},
    # Windows-Aufgabenplanung: NemiCLI zu einer Zeit mit einem Auftrag starten
    "zeitplan":         {"func": _zeitplan,         "confirm": True,  "felder": ["aktion", "name"]},
    # Bildschirm fotografieren – fragt, weil Privates zu sehen sein kann
    "bildschirm_ansehen": {"func": _bildschirm_ansehen, "confirm": True, "felder": []},
    # lernen (eigener Wissensspeicher – harmlos, ohne Nachfrage)
    "skill_merken":     {"func": _skill_merken,     "confirm": False, "felder": ["name", "inhalt"]},
    "skill_laden":      {"func": _skill_laden,      "confirm": False, "felder": ["name"]},
    # Fragt immer (Prüffenster) – außer /skills selbst an und nichts aus dem Netz in der Runde.
    "skill_schreiben":  {"func": _skill_schreiben,  "confirm": True,
                         "felder": ["name", "beschreibung", "wann", "anleitung"]},
    "skill_ausbessern": {"func": _skill_ausbessern, "confirm": True, "felder": ["name", "suchen", "ersetzen"]},
    "merken":           {"func": _merken,           "confirm": False, "felder": ["text"]},
    "ordner_lernen":    {"func": _ordner_lernen,    "confirm": False, "felder": ["pfad", "typ"]},
    "ml_status":        {"func": _ml_status,        "confirm": False, "felder": []},
    # Bild erzeugen (aktiver Bild-Motor: Krea 2, WebUI oder ComfyUI – harmlos)
    "bild_malen":       {"func": _bild_malen,       "confirm": False, "felder": []},
    "bild_serie":       {"func": _bild_serie,       "confirm": False, "felder": ["varianten"]},
    "charakter_zeigen": {"func": _charakter_zeigen, "confirm": False, "felder": []},
    "charakter_aendern": {"func": _charakter_aendern, "confirm": True, "felder": []},
    # Systemwache (tools/wache): lesen frei; bewerten/justieren schreiben nur in die
    # Wache-Datenbank – in Grenzen des Nutzers, protokolliert, rücknehmbar.
    "wache_status":     {"func": _wache_status,     "confirm": False, "felder": []},
    "wache_alarme":     {"func": _wache_alarme,     "confirm": False, "felder": []},
    "wache_ereignisse": {"func": _wache_ereignisse, "confirm": False, "felder": []},
    "wache_inventar":   {"func": _wache_inventar,   "confirm": False, "felder": []},
    "wache_bewerten":   {"func": _wache_bewerten,   "confirm": False, "felder": ["urteil"]},   # + id/ids/regel(+subjekt), begruendung, bezeichnung
    "wache_justieren":  {"func": _wache_justieren,  "confirm": False, "felder": ["was", "wert", "begruendung"]},
    # Die Schwebekugel steuern: stimmung (Bild), sagen, bewegung, ecke/position, versteckt, groesse
    "kugel":            {"func": _kugel,            "confirm": False, "felder": []},
    # Coding-Assistent: Projekt + Todo-Liste; was der Nutzer entscheidet, steht in coding.py
    "coding_start":     {"func": _coding_start,     "confirm": False, "felder": ["projekt"]},
    "todo":             {"func": _todo,             "confirm": False, "felder": ["aktion"]},
    # Gelbe Todo-Liste: vor jeder Aktion aufschreiben, was ansteht (plan.py)
    "plan":             {"func": _plan,             "confirm": False, "felder": []},
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


# Windows-Pfade im JSON: Modelle schreiben gern "C:\Users\…" mit EINEM Backslash.
# In JSON ist \U kein gültiges Escape – json.loads wirft, und der Block fiel bis
# 20.09.2026 stumm unter den Tisch (das Modell glaubte, die Datei sei gelesen).
# Reparatur in Stufen: erst ungültige Escapes verdoppeln (\U → \\U), dann zur
# Not alle Backslashes. Gültige Escapes wie \n bleiben dabei Zeilenumbrüche –
# in einem Pfad-Feld ("C:\neu\temp") wären das aber falsche Steuerzeichen,
# deshalb werden sie DORT anschließend zurück in Backslash + Buchstabe gedreht:
# ein Windows-Pfad enthält nie ein Steuerzeichen.
_UNGUELTIGES_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_PFAD_FELDER = ("pfad", "von", "nach", "ziel", "quelle", "path", "ordner")
_STEUER_ZURUECK = {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}


def _json_reparieren(roh: str):
    """json.loads mit Backslash-Reparatur – None, wenn es gar nicht geht."""
    for kandidat in (roh, _UNGUELTIGES_ESCAPE.sub(r"\\\\", roh), roh.replace("\\", "\\\\")):
        try:
            return json.loads(kandidat)
        except json.JSONDecodeError:
            continue
    return None


def _pfade_entsteuern(obj: dict) -> dict:
    for feld in _PFAD_FELDER:
        v = obj.get(feld)
        if isinstance(v, str) and re.match(r"^[A-Za-z]:", v) and any(c in v for c in _STEUER_ZURUECK):
            for c, ersatz in _STEUER_ZURUECK.items():
                v = v.replace(c, ersatz)
            obj[feld] = v
    return obj


def parse_actions(text: str) -> tuple[list[dict], str]:
    """Findet Aktions-Blöcke (JSON mit 'tool'). Gibt (Aktionen, Text ohne diese Blöcke)."""
    actions: list[dict] = []
    spans: list[tuple[int, int]] = []
    for m in _BLOCK.finditer(text):
        obj = _json_reparieren(m.group(1))
        if obj is None:
            continue
        if isinstance(obj, dict):
            obj = _pfade_entsteuern(obj)
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
_WEB_TOOLS = {"web_lesen", "web_wiki", "web_suche", "paket_info"}
SKILL_AENDERN = {"skill_schreiben", "skill_ausbessern"}      # Prüffenster, außer /skills selbst an
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
    if tool in SKILL_AENDERN:
        import skills
        return _web_taint or not skills.selbst()
    if tool == "todo":
        import coding
        return coding.braucht_bestaetigung(action)
    return bool(spec["confirm"])


def confirmation_preview(action: dict) -> str | None:
    """Vollständiger Zielinhalt vor einer Dateiänderung, ohne Schreibzugriff.

    Fehler beim Ermitteln der Vorschau werden an den Aufrufer weitergereicht:
    eine nicht prüfbare Änderung darf nicht durch einen Kurztext ersetzt werden.
    """
    tool = action.get("tool")
    if tool == "skill_schreiben":
        import skills
        return skills.vorschau(action.get("name"), action.get("beschreibung"),
                               action.get("wann"), action.get("anleitung"), fuer_alle=_wahr(action.get("fuer_alle")))
    if tool == "skill_ausbessern":
        import skills
        return skills.ausbessern_vorschau(action.get("name"), action.get("suchen"), action.get("ersetzen"))
    if tool == "einstellung_aendern":
        import einstellungen
        return einstellungen.vorschau(str(action.get("was") or ""), action.get("wert"), action.get("grund") or "")
    if tool == "theme_erstellen":
        import themes_eigen
        return themes_eigen.vorschau(*_theme_felder(action))
    if tool == "charakter_aendern":
        import charakter
        return charakter.vorschau(action)
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
    original = None
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
        if original is None and exists:
            try:
                original = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                original = None                  # nicht als Text lesbar: ganzer neuer Inhalt
        if original is not None:
            return f"{header}\n\n{vergleich(original, content)}"
    return f"{header}\n\n{label} ({len(content)} Zeichen):\n{content}"


VERGLEICH_KOPF = "Änderungen"


def vergleich(alt: str, neu: str) -> str:
    """Vollständige Datei als Vergleich, eine Zeile je Dateizeile:
    '+ <nr> │ text' neu, '- <nr> │ text' entfernt, '  <nr> │ text' unverändert.
    Nummern: neue Datei, bei entfernten Zeilen die alte."""
    a, b = alt.splitlines(), neu.splitlines()
    zeilen: list[str] = []
    plus = minus = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            zeilen += [f"  {j1 + k + 1:>5} │ {b[j1 + k]}" for k in range(i2 - i1)]
            continue
        zeilen += [f"- {i + 1:>5} │ {a[i]}" for i in range(i1, i2)]
        zeilen += [f"+ {j + 1:>5} │ {b[j]}" for j in range(j1, j2)]
        minus += i2 - i1
        plus += j2 - j1
    if not plus and not minus:
        return f"{VERGLEICH_KOPF}: keine – der Inhalt bleibt gleich.\n" + "\n".join(zeilen)
    return (f"{VERGLEICH_KOPF} (grün + neu, rot − entfernt): {plus} Zeile(n) neu, "
            f"{minus} entfernt – vollständige Datei:\n" + "\n".join(zeilen))


def _taint_hinweis() -> str:
    if _web_taint:
        return ("\n    ⚠ In dieser Runde kam Text aus dem Netz – prüfe, ob diese Notiz wirklich "
                "von dir stammt und nicht von einer Webseite.")
    return ""


def describe(action: dict) -> str:
    """Kurze, lesbare Beschreibung einer Aktion für die Bestätigung."""
    text = _describe(action)
    if action.get("_frei"):
        text += f"  [{action['_frei']} – ohne Rückfrage]"
    return text


def _describe(action: dict) -> str:
    t = action.get("tool", "?")
    g = action.get
    if t == "datei_lesen":      return f"Datei lesen:  {g('pfad', '?')}"
    if t == "bild_ansehen":     return f"Bild ansehen:  {g('pfad', '?')}"
    if t == "anleitung_lesen":  return f"Anleitung lesen:  {g('thema', '?')}"
    if t == "menue_oeffnen":    return f"Menü öffnen:  {g('befehl', '?')}"
    if t == "api_nachschlagen": return f"In der Installation nachschlagen:  {g('name', '?')}"
    if t == "code_pruefen":     return f"Code prüfen (ruff):  {g('pfad', 'Projekt')}"
    if t == "paket_info":       return f"Paket-Info (PyPI, Lücken):  {g('name', '?')}"
    if t == "seite_ansehen":    return f"Seite ansehen:  {g('ziel', g('pfad', g('url', '?')))}"
    if t == "doku_suchen":
        return f"Offline-Doku lesen:  #{g('id')}" if g("id") not in (None, "") else f"Offline-Doku suchen:  {g('frage', '?')}"
    if t == "theme_erstellen":  return f"Theme anlegen:  {g('name', '?')}  ({g('brand', '?')} / {g('accent', '?')})"
    if t == "bild_fragen":      return f"Bild nachfragen:  {g('pfad', '?')}  – {g('frage', '?')}"
    if t == "bildschirm_ansehen": return "Bildschirm fotografieren und ansehen (Screenshot)"
    if t == "ordner_auflisten": return f"Ordner auflisten:  {g('pfad', '.')}"
    if t == "ordner_erkennen":  return f"Ordner-Typ erkennen (ML):  {g('pfad', '.')}"
    if t == "dateien_suchen":   return f"Dateien suchen:  {g('muster', '?')}  in {g('pfad', '.')}"
    if t == "inhalt_suchen":    return f"Inhalt suchen:  '{g('muster', '?')}'  in {g('pfad', '.')}"
    if t == "web_lesen":        return f"Web lesen:  {g('url', '?')}" + (f"  (Teil {g('teil')})" if g("teil") else "")
    if t == "web_wiki":         return f"Wikipedia suchen:  {g('suche', '?')}"
    if t == "gedaechtnis_suchen":
        filter_ = " · ".join(f"{k} {g(k)}" for k in ("quelle", "seit", "bis") if g(k))
        return f"Gedächtnis durchsuchen:  {g('frage', '?')}" + (f"  ({filter_})" if filter_ else "")
    if t == "gedaechtnis_lesen": return f"Gedächtnis lesen:  {g('ref', '?')}" + (f"  (ab {g('ab')})" if g("ab") else "")
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
    if t == "datei_kopieren":   return f"Kopieren:  {g('von', '?')} → {g('nach', '?')}"
    if t == "oeffnen":          return f"Öffnen:  {g('pfad', '?')}"
    if t == "loeschen":
        pf = str(g('pfad', '?'))
        try:
            if Path(pf).is_dir():
                n = sicherheit.anzahl_dateien(pf, grenze=sicherheit.loesch_limit())
                viele = "+" if n > sicherheit.loesch_limit() else ""
                return f"LÖSCHEN (Ordner, {n}{viele} Dateien):  {pf}"
        except OSError:
            pass
        return f"LÖSCHEN:  {pf}"
    if t == "befehl":           return f"PowerShell ausführen:\n    {_befehl_text(action) or '?'}"
    if t == "einstellung_aendern":
        return (f"Einstellung ändern ⚙:  {g('was', '?')}  {g('wert', '?')}"
                + (f"\n    Grund: {g('grund')}" if g("grund") else "") + _taint_hinweis())
    if t == "paket_installieren":
        p = action.get("pakete") or action.get("paket") or "?"
        return "Pakete in der Sandbox installieren: " + (", ".join(map(str, p)) if isinstance(p, list) else str(p))
    if t == "code_ausfuehren":
        if action.get("modul"):
            args = action.get("argumente") or []
            return (f"In der Sandbox ausführen: python -m {action.get('modul')} "
                    + (" ".join(map(str, args)) if isinstance(args, list) else str(args)))
        if action.get("pfad"):
            return f"Python in der Sandbox ausführen (Kopie): {action.get('pfad')}"
        return "Python in der Sandbox ausführen:\n" + str(action.get("code") or "?")[:4000]
    if t == "abfragen":         return ("Abfragen (nur lesen): " + str(action.get("was") or "?")
                                        + "".join(f" · {action[k]}" for k in
                                                  ("filter", "pid", "pfad", "schluessel", "kanal")
                                                  if action.get(k)))
    if t == "zeitplan_anzeigen": return "Zeitplan anzeigen (NemiCLI-Aufgaben in Windows)"
    if t == "zeitplan":
        was = str(g("aktion", "?")).lower()
        if was.startswith("anl") or was in ("neu", "erstellen", "planen"):
            return (f"Zeitplan anlegen:  '{g('name', '?')}'  –  {g('wann', '?')}\n"
                    f"    Auftrag: {str(g('auftrag', ''))[:200]}")
        return f"Zeitplan {was}:  '{g('name', '?')}'"
    if t == "skill_laden":      return f"Skill laden:  {g('name', '?')}"
    if t == "skill_ausbessern":
        return f"Skill ausbessern 🧩:  {g('name', '?')}" + _taint_hinweis()
    if t == "skill_schreiben":
        return (f"Skill schreiben 🧩:  {g('name', '?')}" + ("  [für alle]" if _wahr(g("fuer_alle")) else "")
                + f"  –  {g('beschreibung', '')}" + _taint_hinweis())
    if t == "skill_merken":
        inhalt = str(g("inhalt", "")).strip().replace("\n", " ")
        kurz = inhalt if len(inhalt) <= 160 else inhalt[:159] + "…"
        return f"Skill merken:  {g('name', '?')}" + (f"\n    {kurz}" if kurz else "") + _taint_hinweis()
    if t == "merken":
        return f"Ins Langzeitgedächtnis:  {g('text', '?')}" + _taint_hinweis()
    if t == "ordner_lernen":    return f"Ordner-Typ lernen (ML):  {g('pfad', '?')} = {g('typ', '?')}"
    if t == "ml_status":        return "ML-Status / Ordner-Sinn-Bericht abrufen"
    if t == "bild_malen":
        figur = ""
        if _wahr(g("figur")) or g("outfit") or g("pose"):
            figur = "  [Figur" + "".join(f" · {x}" for x in (g("outfit"), g("pose")) if x) \
                + (" · neues Gesicht" if _wahr(g("neues_gesicht")) else "") + "]"
        return f"Bild malen 🎨:  {g('prompt', g('beschreibung', g('motiv', '?')))}{figur}"
    if t == "bild_serie":
        v = g("varianten") or g("bilder") or []
        return (f"Bilder-Serie 🎨:  {len(v) if isinstance(v, list) else 1} Bilder"
                + ("  [Figur]" if _wahr(g("figur")) else ""))
    if t == "charakter_zeigen": return "Charakter-Datei ansehen"
    if t == "charakter_aendern": return "Charakter-Datei ändern 🧬"
    if t == "wache_status":     return "Wache 🛡: Lage abfragen"
    if t == "wache_alarme":
        return f"Wache 🛡: Alarm {g('id')} ansehen" if g("id") else f"Wache 🛡: Alarme auflisten ({g('status', 'alle')})"
    if t == "wache_ereignisse": return f"Wache 🛡: Ereignisse ({g('prozess') or g('kategorie') or 'alle'})"
    if t == "wache_inventar":   return f"Wache 🛡: Inventar {g('art', 'Übersicht')}"
    if t == "wache_bewerten":
        ziel = (f"alle offenen {g('regel')}" + (f' zu "{g("subjekt")}"' if g("subjekt") else "")
                if g("regel") and not g("id") and not g("ids")
                else f"Alarm {g('id') or ', '.join(map(str, action.get('ids') or [])) or '?'}")
        return (f"Wache 🛡: {ziel} → {g('urteil', '?')}"
                + (f" = {g('bezeichnung', '')}" if g('bezeichnung', '') else "")
                + f"\n    {g('begruendung', '')}")
    if t == "wache_justieren":  return f"Wache 🛡 JUSTIEREN: {g('was', '?')} = {g('wert', '?')}\n    {g('begruendung', '')}"
    if t == "coding_start":
        return f"Coding-Assistent starten 🟦:  {g('projekt', '?')}" + (f"\n    {g('aufgabe')}" if g("aufgabe") else "")
    if t == "todo":
        import coding
        return coding.beschreibung(action)
    if t == "plan":
        teile = []
        if action.get("punkte"):
            teile.append("Todo-Liste anlegen")
        if action.get("neu"):
            teile.append("Punkt ergänzen")
        if action.get("erledigt") not in (None, ""):
            teile.append(f"abhaken: {g('erledigt')}")
        if action.get("streichen") not in (None, ""):
            teile.append(f"streichen: {g('streichen')}")
        return "🟨 Todo: " + (" · ".join(teile) or "ansehen")
    if t == "kugel":
        teile = [f"{k} {g(k)}" for k in ("stimmung", "bewegung", "ecke", "position", "groesse") if g(k)]
        if g("sagen"):
            teile.append(f"sagt „{str(g('sagen'))[:60]}“")
        if "versteckt" in action:
            teile.append("versteckt" if action.get("versteckt") in (True, "true", "ja", 1, "1") else "zeigt sich")
        return "Kugel 🔮: " + (", ".join(teile) or "?")
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
