"""Regelwerk R001–R014 und R016–R024 (R015 kommt aus absolvierung.py; R019–R024 bewerten den KI-Tracker): bekannte Muster, zuverlässig – das Modell findet Unbekanntes.

Regeln können vom Nutzer oder – in seinen
Grenzen – von der Persönlichkeit stumm geschaltet werden (`stumm`), nie gelöscht.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from . import herkunft, kitracker
from .ereignisse import DATEI, KI, NETZ, PROZESS, Alarm, Ereignis, Schwere

KI_LOESCHEN_AB = 10                 # R022: so viele Lösch-Taten einer KI im Fenster

_SKRIPT_HOSTS = {"powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe",
                 "mshta.exe", "rundll32.exe", "regsvr32.exe", "certutil.exe", "bitsadmin.exe"}
_UNGEWOEHNLICHE_ELTERN = {"winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "acrord32.exe"}
_SYSTEM_KINDER = {"svchost.exe", "lsass.exe", "services.exe", "csrss.exe", "smss.exe",
                  "winlogon.exe", "wininit.exe", "spoolsv.exe"}
_SYSTEM_ELTERN = {"services.exe", "wininit.exe", "smss.exe", "winlogon.exe", "system",
                  "userinit.exe", ""}
_LISTEN_AUFFAELLIG = {23, 445, 1433, 3306, 3389, 4444, 5432, 5900, 6667, 9001, 9050}
# Muster für Kodierung und verstecktes Fenster werden zur Laufzeit zusammengesetzt: so steht keins
# ausgeschrieben im Quelltext (Schutzsoftware wertet solche Zeichenketten als verdächtig).
_KODIER = "encoded" + "command"
_VERSTECKT = "hid" + "den"
_VERDAECHTIGE_FLAGS = ["-" + _KODIER[:3], "-" + _KODIER, "-e ", f"-w {_VERSTECKT}", f"-windowstyle {_VERSTECKT}",
                       "-nop", "-noprofile", "-executionpolicy bypass", "-ep bypass",
                       "downloadstring", "downloadfile", "iex", "invoke-expression",
                       "frombase64string", "-noninteractive"]
# NemiCLIs eigener PowerShell-Rahmen (actions.py `abfragen`/`befehl`, zeitplan.py): so beginnt
# jede Kommandozeile, die NemiCLI selbst startet. Ohne Ausnahme löste jede eigene Abfrage R001
# aus und weckte die Persönlichkeit erneut. Ein gefälschter Rahmen unter fremdem Elternprozess
# fällt weiter bei R002 und im Profil auf.
EIGENER_RAHMEN = "$progresspreference='silentlycontinue'; $outputencoding = [console]::outputencoding"
_EIGENE_ELTERN = {"python.exe", "pythonw.exe", "nemicli.exe", "nemiclic.exe"}   # nemiclic = exe mit Konsole
# Vor dem Rahmen steht nur der Programmname und diese Schalter (actions.py `_befehl_roh`).
_EIGENE_SCHALTER = {"-noprofile", "-noninteractive", "-command"}
_SHELL_NAMEN = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}


_wurzeln: list[str] = []


def _eigene_wurzeln() -> list[str]:
    """Ordner, aus denen NemiCLIs eigener Interpreter bzw. die exe läuft (Programm, venv, Basis-Python)."""
    if not _wurzeln:
        import sys
        kandidaten = {os.path.dirname(sys.executable)}
        if not getattr(sys, "frozen", False):          # in der exe zeigen prefix/__file__ ins Entpack-Verzeichnis
            kandidaten |= {sys.prefix, sys.base_prefix, os.path.dirname(getattr(sys, "_base_executable", "") or ""),
                           str(Path(__file__).resolve().parents[2])}
        _wurzeln.extend(sorted({os.path.normcase(os.path.abspath(k)) for k in kandidaten if k}))
    return _wurzeln


# Ablageorte, aus denen kein installiertes NemiCLI und kein eingerichtetes Python läuft.
_ABLAGE = re.compile(r"(?i)\\(temp|tmp|downloads|users\\public|appdata\\local\\temp|windows\\temp)\\")


def _eigener_elternort(e: Ereignis) -> bool:
    """Elternprozess am eigenen Ort (Programm, venv, Basis-Python) oder an einem gewöhnlichen
    Installationsort? Ein Interpreter bzw. eine exe aus Temp/Downloads ist nie NemiCLI. Andere
    Installationen (Test-exe, unsignierte Builds) bleiben erlaubt – sonst Fehlalarme für NemiCLI selbst.
    Ohne Pfad (Sensor kennt ihn nicht, fehlende Rechte) zählt wie bisher nur der Name."""
    pfad = str((e.extra or {}).get("eltern_exe") or "")
    skript = _eigenes_skript(e)
    if skript is not None:                     # Aufruf des Elternprozesses bekannt: er entscheidet
        return skript
    if not pfad:
        return True
    try:
        n = os.path.normcase(os.path.abspath(pfad))
    except (ValueError, OSError):
        return False
    if any(n.startswith(w + os.sep) for w in _eigene_wurzeln()):
        return True
    return not _ABLAGE.search(n)


def _programm_ordner() -> str:
    return os.path.normcase(str(Path(__file__).resolve().parents[2]))


_PY_SCHALTER_MIT_WERT = {"-x", "-w", "-q"}     # Schalter des Interpreters, die einen Wert nehmen (-X utf8, -W …)


def _eigenes_skript(e: Ereignis) -> bool | None:
    """Ein Python-Elternprozess ist nur dann NemiCLI, wenn er ein Skript aus NemiCLIs Programmordner ausführt
    (main.py, Kindprozesse) – nicht jedes python.exe mit nachgebautem Rahmen. None: Aufruf unbekannt (ältere
    Ereignisse, keine Rechte) oder kein Python-Elternprozess."""
    x = e.extra or {}
    argv = x.get("eltern_argv")
    if (e.eltern or "").lower() not in ("python.exe", "pythonw.exe") or not isinstance(argv, list) or not argv:
        return None
    rest = [str(a) for a in argv[1:]]
    while rest and rest[0].startswith("-"):
        schalter = rest.pop(0).lower()
        if schalter in ("-c", "-m", "-"):      # Code oder Modul statt Skript: nicht NemiCLIs Start
            return False
        if schalter in _PY_SCHALTER_MIT_WERT and rest:
            rest.pop(0)
    if not rest:
        return False
    skript = rest[0]
    try:
        if not os.path.isabs(skript):
            skript = os.path.join(str(x.get("eltern_cwd") or ""), skript) if x.get("eltern_cwd") else ""
        n = os.path.normcase(os.path.abspath(skript)) if skript else ""
    except (ValueError, OSError):
        return False
    return bool(n) and n.startswith(_programm_ordner() + os.sep)


def eigener_aufruf(e: Ereignis) -> bool:
    """Ist dieser Prozess NemiCLI selbst bei der Arbeit (abfragen/befehl/Zeitplan)? Der Rahmen muss
    den Befehl eröffnen – irgendwo in der Kommandozeile reicht nicht (etwa hinter dem Kodier-Schalter)."""
    if (e.prozess or "").lower() not in ("powershell.exe", "pwsh.exe"):
        return False
    if (e.eltern or "").lower() not in _EIGENE_ELTERN or not _eigener_elternort(e):
        return False
    vor, rahmen, _ = " ".join((e.cmdline or "").lower().split()).partition(EIGENER_RAHMEN)
    if not rahmen:
        return False
    # subprocess setzt das Skript-Argument in Anführungszeichen: `-Command "$ProgressPreference=…`
    teile = [t for t in (t.strip('"') for t in vor.split()) if t]
    if teile and teile[0].replace("/", "\\").rsplit("\\", 1)[-1] in _SHELL_NAMEN:
        teile = teile[1:]
    return all(t in _EIGENE_SCHALTER for t in teile)


def eigener_server(e: Ereignis) -> bool:
    """Ist das NemiCLIs llama.cpp-Reserve? Genau eine geprüfte llama-server.exe (Pfad eines eingerichteten
    Builds, jede Datei laut Prüfliste unverändert), gestartet von NemiCLI. Verbindungen nach außen zählen
    nie dazu – der Server läuft offline und nur auf 127.0.0.1."""
    if (e.prozess or "").lower() != "llama-server.exe" or (e.eltern or "").lower() not in _EIGENE_ELTERN:
        return False
    if (e.extra or {}).get("extern"):
        return False
    try:
        import llamareserve
        return bool(e.exe) and llamareserve.ist_geprueft(e.exe)
    except Exception:
        return False


def _flags_in(cmd: str) -> list[str]:
    """Welche verdächtigen Flags stehen in der Kommandozeile? Kurzformen wie -nop nur als
    ganzes Wort – sonst zählt -NoProfile doppelt (-nop UND -noprofile)."""
    low = cmd.lower()
    tokens = set(low.split())
    out = []
    for f in _VERDAECHTIGE_FLAGS:
        if f.startswith("-") and " " not in f.strip():
            if f.strip() in tokens:
                out.append(f.strip())
        elif f == "iex":                                    # nicht in msiexec, iexplore …
            if _IEX.search(low):
                out.append(f)
        elif f in low:
            out.append(f.strip())
    # Die Shell nimmt jede eindeutige Abkürzung eines Parameters an, auch mit / oder Gedankenstrich:
    # alle Präfixe des Kodier-Parameters zählen (ohne die des Richtlinien-Parameters).
    for t in tokens:
        if len(t) >= 2 and t[0] in "-/–—" and (_KODIER.startswith(t[1:]) or t[1:] == "ec") \
                and not any(o.startswith("-e") and _KODIER.startswith(o[1:]) for o in out):
            out.append("-" + _KODIER[:3])
    return out


_IEX = re.compile(r"(?<![\w-])iex(?![\w-])")


def _ist_shell(e: Ereignis) -> bool:
    """PowerShell nach Prozessname, Dateiname oder Versionsdaten (auch umbenannt)."""
    namen = {(e.prozess or "").lower(), (e.exe or "").replace("/", "\\").rsplit("\\", 1)[-1].lower()}
    if namen & _SHELL_NAMEN:
        return True
    try:
        return bool(e.exe) and herkunft.originalname(e.exe) in ("powershell.exe", "pwsh.exe")
    except Exception:
        return False


_TARNUNG = {
    ".scr": "Bildschirmschoner – in Wahrheit eine gewöhnliche EXE",
    ".pif": "Verknüpfung zu MS-DOS-Programm, wird als Programm ausgeführt",
    ".com": "altes Programmformat, heute fast nur noch zur Tarnung genutzt",
    ".hta": "HTML-Anwendung, läuft ohne Browser-Schutz",
    ".cpl": "Systemsteuerungselement, wird beim Öffnen ausgeführt",
}

BESCHREIBUNG = {
    "R001": f"Verdächtige Kommandozeile (-{_KODIER[:3]}, -w {_VERSTECKT}, IEX, DownloadString …)",
    "R002": "Ungewöhnliche Prozesskette (Office startet Skript-Host · Systemprozess mit falscher Herkunft)",
    "R003": "Ausführung aus Temp oder Downloads",
    "R004": "Prozess-Flut (≥ 25 Kinder eines Elternprozesses in 5 min)",
    "R005": "Ausführbare Datei im Autostart-Ordner",
    "R006": "Externe Verbindung auf C2-Port (4444, 6667, 9001, 9050, 23)",
    "R007": "Beaconing (≥ 6 Verbindungen zum selben Ziel, Jitter < 25 %)",
    "R008": "Viele Verbindungen (≥ 60 je Prozess in 5 min)",
    "R009": "Massenhafte Dateiänderungen (≥ 80 in 5 min)",
    "R010": "Neuer Autostart-Eintrag seit letztem Inventar",
    "R011": "Neuer Dienst / neue Aufgabe seit letztem Inventar",
    "R012": "Neuer Listener auf auffälligem Port",
    "R013": "Getarnte ausführbare Datei (.scr .pif .com .hta .cpl)",
    "R014": "Systemprogramm am falschen Ort (bekannter Name außerhalb von System32/SystemApps)",
    "R015": "Neuer Hintergrund-Prozess eines beobachteten Programms (Programm-Absolvierung)",
    "R016": "Umbenanntes Windows-Werkzeug (Versionsdaten nennen z. B. cmd.exe, die Datei heißt anders)",
    "R017": "Dauerhaftes WMI-Abo angelegt (Ereignisprotokoll WMI-Activity 5861)",
    "R018": "Programmstart aus einem Entpackt-Ordner von NemiCLI (Entpackt/, Profile/<Name>/Entpackt/, entpackt_*)",
    "R019": "KI greift auf Geheimnisse zu (Zugangsdaten, Schlüssel, Browser-Daten, fremde Persönlichkeiten)",
    "R020": "KI will Adminrechte",
    "R021": "KI richtet Autostart, Dienst oder geplante Aufgabe ein",
    "R022": f"KI löscht massenhaft (≥ {KI_LOESCHEN_AB} Lösch-Taten in 5 min)",
    "R023": "Protokoll einer KI gekürzt oder gelöscht, obwohl es gerade benutzt wurde",
    "R024": "KI startet ein Programm, das in ihrem eigenen Protokoll fehlt (Gegencheck, erst im Schattenmodus)",
    "VS-001": "Roter Eintrag im Systemstand des Vollscans (einmal je Fund und Dateistand)",
    "SIGMA": "Treffer einer Sigma-Regel (mitgeliefert oder aus dem Wache-Ordner sigma/)",
    "WS-001": "Wache-Dateien verändert (Modelldatei mit fremdem Stand, nicht geladen)",
    "CVE-001": "Installierte Software mit bekannter Schwachstelle (CVE-Liste, Version sicher betroffen, CVSS ≥ 7.0)",
}
# Selbstschutz der Wache: weder der Nutzer noch die Persönlichkeit schalten sie stumm, nie gedämpft
# (keine Mengen-Regel), der Vorprüfer schließt sie nicht (vorpruefer.ERLAUBT).
NIE_STUMM = frozenset({"WS-001"})

# MITRE ATT&CK: welche Technik eine Regel abdeckt (https://attack.mitre.org). Steht im Weckruf und beim Alarm.
ATTACK = {
    "R001": "T1059 Befehlsinterpreter · T1027 Verschleierung",
    "R002": "T1204.002 Ausführung durch den Nutzer · T1036 Tarnung",
    "R003": "T1204.002 Ausführung durch den Nutzer",
    "R005": "T1547.001 Autostart (Run/Startup)",
    "R006": "T1571 Nicht übliche Ports",
    "R007": "T1071 Protokoll der Anwendungsschicht (Beaconing)",
    "R009": "T1486 Daten verschlüsselt (Erpressung)",
    "R010": "T1547.001 Autostart (Run/Startup)",
    "R011": "T1543.003 Windows-Dienst · T1053.005 Geplante Aufgabe",
    "R012": "T1571 Nicht übliche Ports",
    "R013": "T1036.008 Getarnter Dateityp",
    "R014": "T1036.005 Tarnung als legitimer Name/Ort",
    "R016": "T1036.003 Umbenannte Systemwerkzeuge",
    "R017": "T1546.003 WMI-Ereignisabo (Persistenz)",
    "R018": "T1204.002 Ausführung durch den Nutzer",
    "R019": "T1555 Zugangsdaten aus Speichern · T1552 Ungeschützte Zugangsdaten",
    "R020": "T1548 Rechteausweitung",
    "R021": "T1547.001 Autostart · T1543.003 Windows-Dienst · T1053.005 Geplante Aufgabe",
    "R022": "T1485 Datenvernichtung",
    "R023": "T1070 Spuren beseitigen",
    "R024": "T1564 Artefakte verbergen",
    "WS-001": "T1562.001 Abwehr schwächen (Schutzwerkzeug verändern)",
    "CVE-001": "T1190 Ausnutzen öffentlich erreichbarer Anwendungen · T1203 Ausnutzen zur Ausführung beim Nutzer",
}


def _daten_ordner() -> str:
    try:
        import paths
        return os.path.normcase(os.path.abspath(str(paths.DATEN)))
    except Exception:
        return ""


def entpackt_ort(exe: str) -> bool:
    """Liegt die Datei in einem Entpackt-Ordner von NemiCLI: DATEN\\Entpackt\\…, DATEN\\Profile\\<Name>\\Entpackt\\…
    oder in einem Ordner entpackt_*? Ohne bekannten Daten-Ordner zählt jeder Ordner „Entpackt“."""
    try:
        n = os.path.normcase(os.path.abspath(exe))
    except (TypeError, ValueError, OSError):
        return False
    ordner = Path(n).parts[:-1]
    if any(t.startswith("entpackt_") for t in ordner):
        return True
    daten = _daten_ordner()
    if not daten:
        return "entpackt" in ordner
    if not n.startswith(daten + os.sep):
        return False
    rest = Path(n[len(daten) + 1:]).parts[:-1]
    return (len(rest) >= 1 and rest[0] == "entpackt") or (
        len(rest) >= 3 and rest[0] == "profile" and rest[2] == "entpackt")


# Mengen-Regeln fragen „wie viel?“ (Flut, viele Verbindungen, Beaconing, Dateiwelle, ML-Abweichung)
# – die machen Rauschen und dürfen gedämpft oder leiser werden, wenn dasselbe Subjekt wiederholt
# harmlos war. Muster-Regeln fragen „was?“ (Kodier-Schalter in der Kommandozeile, Start aus Temp, C2-Port,
# neuer Dienst …): da ist JEDER Treffer eine eigene Frage, auch beim bekannten Prozess. Beispiel:
# R001·powershell.exe 23× harmlos (Abfragen der KI selbst) darf einen echten kodierten Aufruf
# nicht stumm schalten.
MENGENREGELN = {"R004", "R007", "R008", "R009", "R022", "SIGMA", "ML-001", "DL-001", "ML-002", "DL-002", "ML-003"}
# Wird verdichtet, aber nie gedämpft: R009 hat ein festes Subjekt („überwachte Ordner“) –
# eine Dämpfung schaltete die Erkennung massenhafter Änderungen für alle Ordner ab.
# CVE-001 (bekannte Schwachstelle) kann die Gefahr nur erhöhen: harmlos-Urteile dämpfen sie nie.
NIE_GEDAEMPFT = {"R009", "CVE-001"}


@dataclass
class Treffer:
    regel: str
    titel: str
    text: str
    schwere: Schwere
    subjekt: str = ""       # leer = aus dem Ereignis ableiten (siehe subjekt_von)


def stumme_paare(e: dict) -> set[tuple[str, str]]:
    """Einstellung "stumme_paare" (["R008@chrome.exe", …]) → {(Regel, Subjekt klein)}."""
    out = set()
    for eintrag in (e or {}).get("stumme_paare") or []:
        regel, _, subjekt = str(eintrag).partition("@")
        if regel and subjekt:
            out.add((regel.strip().upper(), " ".join(subjekt.split()).lower()))
    return out


def subjekt_von(e: Ereignis) -> str:
    """Worum es bei einem Ereignis geht – der Schlüssel, unter dem gleiche Alarme
    zusammengefasst werden: Prozess (bei Netz „prozess → ziel“), sonst Datei,
    sonst der Text des Eintrags (Inventar: neuer Dienst, neue Aufgabe …)."""
    if e.kategorie == NETZ and e.prozess and e.ziel:
        return f"{e.prozess} → {e.ziel}"
    return e.prozess or e.datei or e.ziel or " ".join(str(e.text or "").split())[:120]


def subjekt_fuer(regel: str, e: Ereignis) -> str:
    """Subjekt aus Regel + Ereignis – dieselbe Zuordnung, die die Regeln selbst treffen.
    Gebraucht, um Alarme ohne Subjekt nachzurüsten."""
    if regel == "R004":
        return e.eltern or "?"
    if regel == "R008":
        return e.prozess or "?"
    if regel == "R009":
        return "überwachte Ordner"
    if regel == "ML-001":
        return f"{e.prozess or e.datei or e.ziel or 'Systemereignis'} · {e.aktion}"
    if regel == "ML-003":
        from .kimodell import subjekt
        return subjekt(e)
    return subjekt_von(e)


BEACON_MAX = 5000                   # (Prozess, Ziel)-Paare für R007
# R007 braucht sechs Verbindungen: im 5-Minuten-Fenster blieben Takte ab 60 s unsichtbar.
BEACON_FENSTER = 3600
BEACON_LAENGE = 50                  # je Paar nur die jüngsten Zeitpunkte


def _kuerzen(dq: deque, grenze: float) -> None:
    while dq and dq[0] < grenze:
        dq.popleft()


class Regelwerk:
    def __init__(self, fenster: int = 300, stumm: set[str] | None = None):
        self.fenster = fenster
        self._lock = threading.RLock()
        self.stumm: set[str] = set(stumm or ())
        self.stumm_paare: set[tuple[str, str]] = set()     # (Regel, Subjekt klein) – einzelne Paare still
        self._starts: dict[str, deque] = defaultdict(deque)
        self._conns: dict[str, deque] = defaultdict(deque)
        self._dateien: deque = deque()
        self._beacon: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=BEACON_LAENGE))
        self._seit_aufraeumen = 0
        self.treffer: dict[str, int] = defaultdict(int)
        self.fehler: dict[str, int] = defaultdict(int)       # Regel → Ausnahmen
        self.bei_fehler = None                                # Aufruf(wo, exc) beim 1. und jedem 100. Mal
        self.regeln = [self._r001, self._r002, self._r003, self._r004, self._r005, self._r006,
                       self._r007, self._r008, self._r009, self._r010, self._r011, self._r012,
                       self._r013, self._r014, self._r016, self._r017, self._r018,
                       self._r019, self._r020, self._r021, self._r022, self._r023, self._r024, self._sigma]
        self._ki_loeschen: dict[str, deque] = defaultdict(deque)
        self.sigma, self.sigma_uebersprungen = [], []
        self.sigma_laden()

    def pruefen(self, e: Ereignis) -> list[Alarm]:
        # Motor und Inventar-Thread prüfen gleichzeitig; die Fenster-Zähler sind gemeinsamer Zustand.
        with self._lock:
            return self._pruefen(e)

    def _pruefen(self, e: Ereignis) -> list[Alarm]:
        self._zustand(e)
        out = []
        for regel in self.regeln:
            try:
                t = regel(e)
            except Exception as exc:
                self._ausnahme(regel.__name__.lstrip("_").upper(), exc)
                continue
            if t is None:
                continue
            for t in (t if isinstance(t, list) else [t]):        # Sigma kann mehrere Regeln treffen
                self.treffer[t.regel] += 1
                subjekt = t.subjekt or subjekt_von(e)
                if t.regel in self.stumm or self.paar_stumm(t.regel, subjekt):
                    continue
                out.append(Alarm(titel=t.titel, text=t.text, schwere=t.schwere, regel=t.regel,
                                 ereignis_id=e.id, quelle="regel", zeit=e.zeit, subjekt=subjekt))
        return out

    def paar_stumm(self, regel: str, subjekt: str) -> bool:
        return bool(self.stumm_paare) and (regel, " ".join(str(subjekt or "").split()).lower()) in self.stumm_paare

    def _ausnahme(self, name: str, exc: Exception) -> None:
        """Eine Regel scheiterte: zählen und melden; die übrigen Regeln laufen weiter."""
        self.fehler[name] += 1
        n = self.fehler[name]
        if self.bei_fehler is not None and (n == 1 or n % 100 == 0):
            try:
                self.bei_fehler(f"regel {name} ({n}×)", exc)
            except Exception:
                pass

    def _zustand(self, e: Ereignis) -> None:
        jetzt = e.zeit or time.time()
        grenze = jetzt - self.fenster
        if e.kategorie == PROZESS and e.aktion == "process_start" and not (e.extra or {}).get("nachgeholt"):
            dq = self._starts[e.eltern or "?"]; dq.append(jetzt); _kuerzen(dq, grenze)
        elif e.kategorie == NETZ and e.aktion == "conn_open":
            dq = self._conns[e.prozess or "?"]; dq.append(jetzt); _kuerzen(dq, grenze)
            if e.ziel:
                bq = self._beacon[(e.prozess or "?", e.ziel)]; bq.append(jetzt); _kuerzen(bq, jetzt - BEACON_FENSTER)
        elif e.kategorie == DATEI and e.aktion in ("file_created", "file_modified", "file_deleted", "file_moved"):
            self._dateien.append(jetzt); _kuerzen(self._dateien, grenze)
        self._seit_aufraeumen += 1
        if self._seit_aufraeumen >= 1000 or len(self._beacon) > BEACON_MAX:
            self._aufraeumen(jetzt, grenze)

    def _aufraeumen(self, jetzt: float, grenze: float) -> None:
        """Gezielt nur Altes entfernen: alles zu leeren hieße, R007 sähe bei vielen Zielen nie sechs
        Verbindungen in Folge."""
        self._seit_aufraeumen = 0
        for d in (self._starts, self._conns):
            for k in [k for k, dq in d.items() if not dq or dq[-1] < grenze]:
                del d[k]
        for k in [k for k, dq in self._beacon.items() if not dq or dq[-1] < jetzt - BEACON_FENSTER]:
            del self._beacon[k]
        if len(self._beacon) > BEACON_MAX:                 # immer noch zu viele: die ältesten zuerst
            for k in sorted(self._beacon, key=lambda k: self._beacon[k][-1])[:len(self._beacon) - BEACON_MAX // 2]:
                del self._beacon[k]

    # ------------------------------------------------------------------ Regeln

    def _r001(self, e):
        if e.kategorie != PROZESS or e.aktion != "process_start" or not e.cmdline:
            return None
        if eigener_aufruf(e):
            self.treffer["eigen"] += 1
            return None
        gefunden = _flags_in(e.cmdline)
        if not gefunden or not _ist_shell(e):      # msiexec /i, robocopy /E … sind keine Shell-Schalter
            return None
        return Treffer("R001", "Verdächtige Kommandozeile",
                       f"{e.prozess} wurde mit auffälligen Parametern gestartet "
                       f"({', '.join(gefunden[:3])}). Kommandozeile: {e.cmdline[:200]}",
                       Schwere.HOCH if len(gefunden) >= 2 else Schwere.MITTEL)

    def _r002(self, e):
        if e.kategorie != PROZESS or e.aktion != "process_start":
            return None
        kind, eltern = (e.prozess or "").lower(), (e.eltern or "").lower()
        if eltern in _UNGEWOEHNLICHE_ELTERN and kind in _SKRIPT_HOSTS:
            return Treffer("R002", "Ungewöhnliche Prozesskette",
                           f"{e.eltern} hat {e.prozess} gestartet. Office- und PDF-Anwendungen "
                           "starten normalerweise keine Skript-Hosts – klassisches Makro-Muster.",
                           Schwere.HOCH)
        if kind in _SYSTEM_KINDER and eltern and eltern not in _SYSTEM_ELTERN:
            return Treffer("R002", "Systemprozess mit falscher Herkunft",
                           f"{e.prozess} wurde von {e.eltern} gestartet. Dieser Systemprozess "
                           "kommt normalerweise nur von services.exe/wininit.exe/smss.exe – "
                           "bekannter Name aus unbekannter Quelle ist ein Tarnmuster.",
                           Schwere.HOCH)
        return None

    def _r003(self, e):
        if e.kategorie != PROZESS or e.aktion != "process_start" or not e.exe:
            return None
        low = e.exe.lower()
        if "\\temp\\" in low or "\\appdata\\local\\temp" in low or "\\downloads\\" in low:
            return Treffer("R003", "Ausführung aus temporärem Pfad",
                           f"{e.prozess} läuft aus {e.exe}. Start direkt aus Temp/Downloads ist "
                           "ein häufiges Dropper-Muster.", Schwere.MITTEL)
        return None

    def _r004(self, e):
        if e.kategorie != PROZESS or e.aktion != "process_start" or (e.extra or {}).get("nachgeholt"):
            return None
        eltern = e.eltern or "?"
        n = len(self._starts[eltern])
        if n >= 25 and eltern != "?":
            return Treffer("R004", "Prozess-Flut",
                           f"{eltern} hat {n} Kindprozesse in {self.fenster // 60} Minuten gestartet.",
                           Schwere.MITTEL, subjekt=eltern)
        return None

    def _r005(self, e):
        if e.kategorie != DATEI or e.aktion not in ("file_created", "file_moved"):
            return None
        if "startup" not in (e.datei or "").lower():
            return None
        if (e.extra or {}).get("endung", "") in (".exe", ".ps1", ".bat", ".cmd", ".vbs", ".scr", ".lnk"):
            return Treffer("R005", "Autostart-Persistenz",
                           f"Ausführbare Datei im Autostart-Ordner angelegt: {e.datei}. "
                           "Startet bei jeder Anmeldung mit.", Schwere.HOCH)
        return None

    def _r006(self, e):
        if e.kategorie != NETZ or e.aktion != "conn_open" or not (e.extra or {}).get("extern"):
            return None
        if e.zielport in (4444, 6667, 9001, 9050, 23):
            return Treffer("R006", "Verbindung auf auffälligen Port",
                           f"{e.prozess or 'Unbekannter Prozess'} verbindet sich nach "
                           f"{e.ziel}:{e.zielport}. Dieser Port wird häufig für C2-Kanäle genutzt.",
                           Schwere.HOCH)
        return None

    def _r007(self, e):
        if e.kategorie != NETZ or e.aktion != "conn_open" or not e.ziel:
            return None
        if not (e.extra or {}).get("extern"):
            return None
        zeiten = self._beacon[(e.prozess or "?", e.ziel)]
        if len(zeiten) < 6:
            return None
        liste = list(zeiten)
        luecken = [b - a for a, b in zip(liste, liste[1:])]
        mittel = sum(luecken) / len(luecken)
        if mittel <= 0:
            return None
        varianz = sum((g - mittel) ** 2 for g in luecken) / len(luecken)
        jitter = (varianz ** 0.5) / mittel
        if jitter < 0.25:
            return Treffer("R007", "Mögliches Beaconing",
                           f"{e.prozess or 'Prozess'} kontaktiert {e.ziel} in sehr regelmäßigen "
                           f"Abständen (~{mittel:.0f}s, Jitter {jitter:.0%}, {len(zeiten)} "
                           "Verbindungen). Typisch für Command-and-Control.", Schwere.HOCH)
        return None

    def _r008(self, e):
        if e.kategorie != NETZ or e.aktion != "conn_open":
            return None
        n = len(self._conns[e.prozess or "?"])
        if n >= 60:
            return Treffer("R008", "Auffällig viele Verbindungen",
                           f"{e.prozess or '?'} hat {n} neue Verbindungen in {self.fenster // 60} "
                           "Minuten geöffnet. Kann Scanning oder Datenabfluss sein.", Schwere.MITTEL,
                           subjekt=e.prozess or "?")
        return None

    def _r009(self, e):
        if e.kategorie != DATEI:
            return None
        n = len(self._dateien)
        if n >= 80:
            return Treffer("R009", "Massenhafte Dateiänderungen",
                           f"{n} Dateiänderungen in {self.fenster // 60} Minuten in den überwachten "
                           "Ordnern – bei Verschlüsselungs-Trojanern ein typisches Bild.",
                           Schwere.HOCH, subjekt="überwachte Ordner")
        return None

    def _r010(self, e):
        if e.aktion != "inventory_new_autostart":
            return None
        ziel = f"{(e.extra or {}).get('befehl') or ''} {e.exe or ''}".lower()   # ganze Befehlszeile und Datei
        verdaechtig = any(h in ziel for h in ("\\temp\\", "\\appdata\\local\\temp", "\\downloads\\",
                                              "powershell", "cmd.exe", "mshta", "rundll32",
                                              "regsvr32", "wscript", "cscript"))
        return Treffer("R010", "Neuer Autostart-Eintrag",
                       f"Seit dem letzten Systemscan neu: {e.text}."
                       + (" Zielpfad/Programm zusätzlich auffällig." if verdaechtig else "")
                       + " Wird bei jeder Anmeldung ausgeführt – bewusste Installation?",
                       Schwere.KRITISCH if verdaechtig else Schwere.HOCH)

    def _r011(self, e):
        if e.aktion == "inventory_new_dienst":
            was, grund = "Dienst", "Dienste laufen mit Systemrechten"
        elif e.aktion == "inventory_new_aufgabe":
            was, grund = "geplante Aufgabe", "geplante Aufgaben starten zeitgesteuert"
        else:
            return None
        return Treffer("R011", f"Neuer {was}",
                       f"{e.text}. Seit dem letzten Systemscan neu – {grund}, ein beliebter Weg, "
                       "sich dauerhaft einzunisten.", Schwere.MITTEL)

    def _r012(self, e):
        if e.aktion != "inventory_new_port":
            return None
        port = (e.extra or {}).get("port", 0)
        if port in _LISTEN_AUFFAELLIG:
            return Treffer("R012", "Neuer Listener auf auffälligem Port",
                           f"{e.text}. Port {port} wird häufig für Fernzugriff oder Hintertüren "
                           "genutzt und war beim letzten Scan noch nicht offen.", Schwere.HOCH)
        return None

    def _r013(self, e):
        if e.kategorie != DATEI or e.aktion not in ("file_created", "file_moved"):
            return None
        ext = (e.extra or {}).get("endung", "").lower()
        grund = _TARNUNG.get(ext)
        if not grund:
            return None
        return Treffer("R013", "Getarnte ausführbare Datei",
                       f"{e.datei} wurde angelegt. {ext} = {grund}. In einem Benutzerordner fast "
                       "immer ein Täuschungsversuch.", Schwere.HOCH)

    def _r014(self, e):
        # Ein bekannter Systemname allein sagt nichts: als ML-Anomalie ist er nur
        # einer von vielen hohen Werten, und ein Urteil „signiert von Microsoft“
        # wäre aus dem Namen geschlossen statt geprüft. Hier zählt der Ort auf der
        # Platte: derselbe Name aus %TEMP% ist kein Rauschen, sondern ein harter
        # Treffer. R002 prüft die Herkunft über den Elternprozess, R014 über den
        # Pfad – ein getarntes Programm muss beide passieren.
        if e.kategorie not in (PROZESS, NETZ) or e.aktion not in (
                "process_start", "conn_open", "listen_open"):
            return None
        grund = herkunft.maskerade(e.prozess, e.exe)
        if not grund:
            return None
        return Treffer("R014", "Systemprogramm am falschen Ort", grund, Schwere.KRITISCH)

    def _r016(self, e):
        # Gegenstück zu R014: dort trägt ein fremdes Programm einen Systemnamen, hier trägt ein
        # Systemwerkzeug einen fremden Namen. Die Versionsdaten verraten den Originalnamen.
        if e.kategorie != PROZESS or e.aktion != "process_start" or not e.exe:
            return None
        grund = herkunft.umbenannt(e.prozess, e.exe)
        if not grund:
            return None
        return Treffer("R016", "Umbenanntes Windows-Werkzeug", grund, Schwere.HOCH)

    def _r017(self, e):
        # Ein dauerhaftes WMI-Abo startet Code bei einem Ereignis (Anmeldung, Uhrzeit …) – ohne Autostart-Eintrag.
        if e.aktion != "evtlog_wmi_dauerabo":
            return None
        ursache = f"\nUrsache laut Protokoll: {e.cmdline}" if e.cmdline else ""
        return Treffer("R017", "Dauerhaftes WMI-Abo", e.text + ursache, Schwere.HOCH,
                       subjekt=f"WMI · {e.extra.get('consumer', '?')}")

    def _r018(self, e):
        # NemiCLI entpackt Archive nur zum Ansehen; aus diesen Ordnern startet NemiCLI selbst nichts.
        if e.kategorie != PROZESS or e.aktion != "process_start" or not e.exe or not entpackt_ort(e.exe):
            return None
        return Treffer("R018", "Programmstart aus einem Entpackt-Ordner",
                       f"{e.prozess or e.exe} wurde aus {e.exe} gestartet (Elternprozess: {e.eltern or '?'}). "
                       "Der Ordner enthält entpackte Dateien aus einem Archiv; NemiCLI startet daraus nichts. "
                       "Bewusst gestartet?", Schwere.HOCH)

    # --- KI-Tracker (kitracker.py): was eine KI tut, nach ihrer eigenen Auskunft oder von außen gesehen

    @staticmethod
    def _ki_tat(e) -> bool:
        return (e.kategorie == KI and e.aktion in ("ki_tat", "ki_prozess")
                and not (e.extra or {}).get("nachgetragen"))

    @staticmethod
    def _ki_wer(e) -> str:
        x = e.extra or {}
        wer = x.get("ki") or e.prozess or "KI"
        wie = {"wache": "von der Wache gesehen", "claude_code": "laut Protokoll von Claude Code",
               "codex": "laut Protokoll von Codex", "nemicli": "laut Aktions-Protokoll"}.get(x.get("quelle"), "")
        status = f", Status: {x['status']}" if x.get("status") else ""
        return f"{wer} ({wie}{status})" if wie else wer

    def _r019(self, e):
        if not self._ki_tat(e):
            return None
        ki = (e.extra or {}).get("ki", "")
        text = kitracker.tat_text(e)
        m = kitracker.GEHEIM.search(text) or (
            None if ki.startswith("NemiCLI") else kitracker.PERSOENLICHKEITEN.search(text))
        if not m:
            return None
        return Treffer("R019", "KI greift auf Geheimnisse zu",
                       f"{self._ki_wer(e)}: {e.text}\nGetroffen: „{m.group(0).strip()}“ – Zugangsdaten, Schlüssel, "
                       "Browser-Daten oder fremde Persönlichkeiten. Gehörte das zu einer Aufgabe des Nutzers?",
                       Schwere.HOCH, subjekt=f"{ki} · {m.group(0).strip().lower()}")

    def _r020(self, e):
        if not self._ki_tat(e) or not (was := kitracker.admin(e)):
            return None
        return Treffer("R020", "KI will Adminrechte",
                       f"{self._ki_wer(e)}: {e.text}\nGetroffen: „{was.strip()}“.", Schwere.HOCH,
                       subjekt=f"{(e.extra or {}).get('ki', '')} · Adminrechte")

    def _r021(self, e):
        if not self._ki_tat(e) or not (was := kitracker.autostart(e)):
            return None
        return Treffer("R021", "KI richtet Autostart, Dienst oder Aufgabe ein",
                       f"{self._ki_wer(e)}: {e.text}\nGetroffen: „{was.strip()}“ – damit liefe etwas "
                       "dauerhaft weiter, auch ohne die KI.", Schwere.HOCH,
                       subjekt=f"{(e.extra or {}).get('ki', '')} · {was.strip().lower()}")

    def _r022(self, e):
        if not self._ki_tat(e):
            return None
        x = e.extra or {}
        if not (x.get("art") == "loeschen" or kitracker.LOESCHEN.search(e.cmdline or "")):
            return None
        ki = x.get("ki", "")
        dq = self._ki_loeschen[ki]
        dq.append(e.zeit)
        _kuerzen(dq, e.zeit - self.fenster)
        if len(dq) < KI_LOESCHEN_AB:
            return None
        return Treffer("R022", "KI löscht massenhaft",
                       f"{self._ki_wer(e)}: {len(dq)} Löschungen in {self.fenster // 60} min, zuletzt: {e.text}",
                       Schwere.HOCH, subjekt=f"{ki} · löscht")

    def _r023(self, e):
        if e.kategorie != KI or e.aktion != "ki_protokoll_weg":
            return None
        return Treffer("R023", "Protokoll einer KI gekürzt oder gelöscht",
                       f"{e.text} ({e.datei}). Das Protokoll war in den letzten Tagen in Benutzung – wer Spuren "
                       "beseitigt, will nicht nachvollziehbar sein. Hat der Nutzer es selbst entfernt?",
                       Schwere.HOCH, subjekt=f"{e.prozess} · Protokoll")

    def _r024(self, e):
        if e.kategorie != KI or e.aktion != "ki_ungemeldet":
            return None
        x = e.extra or {}
        return Treffer("R024", "KI startet ein Programm, das in ihrem Protokoll fehlt",
                       f"{e.text}.\nBefehlszeile: {e.cmdline or '(nicht lesbar)'}\nIm Protokoll von "
                       f"{x.get('protokoll', '?')} steht in der halben Stunde davor kein Befehl, der dazu passt. "
                       "Entweder ein Helfer des Programms – oder etwas, das die KI nicht meldet.",
                       Schwere.MITTEL, subjekt=f"{x.get('ki', '')} · {e.prozess}")

    def sigma_laden(self) -> None:
        """Sigma-Regeln (neu) laden: mitgelieferte und die *.yml im Wache-Ordner sigma/."""
        try:
            from . import ORDNER
            from .sigma import laden
            self.sigma, self.sigma_uebersprungen = laden(ORDNER / "sigma")
        except Exception as exc:                         # ohne Sigma laufen die festen Regeln weiter
            self.sigma, self.sigma_uebersprungen = [], [f"Sigma: {type(exc).__name__}: {exc}"[:200]]

    def _sigma(self, e):
        if e.kategorie != PROZESS or e.aktion != "process_start" or not self.sigma:
            return None
        from .sigma import trifft
        raus = []
        for r in self.sigma:
            try:
                if not trifft(r, e, herkunft.originalname):
                    continue
            except Exception as exc:
                self._ausnahme(f"SIGMA {r.kennung}", exc)
                continue
            zeilen = [f"{r.titel}: {r.beschreibung}",
                      f"Sigma-Regel {r.kennung}" + (f" · ATT&CK {r.technik}" if r.technik else "")]
            if r.fehlalarme:
                zeilen.append(f"Bekannte Fehlalarme laut Regel: {'; '.join(r.fehlalarme)}")
            text = "\n".join(zeilen)
            raus.append(Treffer("SIGMA", f"Sigma: {r.titel}", text, r.schwere,
                                subjekt=f"{r.kennung} · {e.prozess or e.exe}"))
        return raus or None
