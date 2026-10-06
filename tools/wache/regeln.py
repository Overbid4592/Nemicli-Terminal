"""Regelwerk R001–R014: bekannte Muster, zuverlässig – das Modell findet Unbekanntes.

Regeln können vom Nutzer oder – in seinen
Grenzen – von der Persönlichkeit stumm geschaltet werden (`stumm`), nie gelöscht.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from . import herkunft
from .ereignisse import DATEI, NETZ, PROZESS, Alarm, Ereignis, Schwere

_SKRIPT_HOSTS = {"powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe",
                 "mshta.exe", "rundll32.exe", "regsvr32.exe", "certutil.exe", "bitsadmin.exe"}
_UNGEWOEHNLICHE_ELTERN = {"winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "acrord32.exe"}
_SYSTEM_KINDER = {"svchost.exe", "lsass.exe", "services.exe", "csrss.exe", "smss.exe",
                  "winlogon.exe", "wininit.exe", "spoolsv.exe"}
_SYSTEM_ELTERN = {"services.exe", "wininit.exe", "smss.exe", "winlogon.exe", "system",
                  "userinit.exe", ""}
_LISTEN_AUFFAELLIG = {23, 445, 1433, 3306, 3389, 4444, 5432, 5900, 6667, 9001, 9050}
_VERDAECHTIGE_FLAGS = ["-enc", "-encodedcommand", "-e ", "-w hidden", "-windowstyle hidden",
                       "-nop", "-noprofile", "-executionpolicy bypass", "-ep bypass",
                       "downloadstring", "downloadfile", "iex", "invoke-expression",
                       "frombase64string", "-noninteractive"]
# NemiCLIs eigener PowerShell-Rahmen (actions.py `abfragen`/`befehl`, zeitplan.py): so beginnt
# jede Kommandozeile, die NemiCLI selbst startet. Die Wache erkannte ihn nicht und schlug mit
# R001 „Hoch“ an (-NoProfile = drei „verdächtige Flags“) – weckte die Persönlichkeit, die machte
# `abfragen`, neuer Alarm … (20.09.2026, 23 falsche Urteile zu powershell.exe). Wer diesen
# Rahmen fälscht, um durchzurutschen, muss ihn erst mal kennen – und das Kind sitzt dann trotzdem
# unter einem fremden Elternprozess, was R002/Profil sehen.
EIGENER_RAHMEN = "$progresspreference='silentlycontinue'; $outputencoding = [console]::outputencoding"
_EIGENE_ELTERN = {"python.exe", "pythonw.exe", "nemicli.exe"}


def eigener_aufruf(e: Ereignis) -> bool:
    """Ist dieser Prozess NemiCLI selbst bei der Arbeit (abfragen/befehl/Zeitplan)?"""
    if (e.prozess or "").lower() not in ("powershell.exe", "pwsh.exe"):
        return False
    if (e.eltern or "").lower() not in _EIGENE_ELTERN:
        return False
    return EIGENER_RAHMEN in " ".join((e.cmdline or "").lower().split())


def _flags_in(cmd: str) -> list[str]:
    """Welche verdächtigen Flags stehen in der Kommandozeile? Kurzformen wie -nop nur als
    ganzes Wort – sonst zählt -NoProfile doppelt (-nop UND -noprofile)."""
    low = cmd.lower()
    tokens = set(low.split())
    out = []
    for f in _VERDAECHTIGE_FLAGS:
        if f.startswith("-") and " " not in f.strip():
            if f in tokens:
                out.append(f)
        elif f in low:
            out.append(f.strip())
    return out
_TARNUNG = {
    ".scr": "Bildschirmschoner – in Wahrheit eine gewöhnliche EXE",
    ".pif": "Verknüpfung zu MS-DOS-Programm, wird als Programm ausgeführt",
    ".com": "altes Programmformat, heute fast nur noch zur Tarnung genutzt",
    ".hta": "HTML-Anwendung, läuft ohne Browser-Schutz",
    ".cpl": "Systemsteuerungselement, wird beim Öffnen ausgeführt",
}

BESCHREIBUNG = {
    "R001": "Verdächtige Kommandozeile (-enc, -w hidden, IEX, DownloadString …)",
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
}


# Mengen-Regeln fragen „wie viel?“ (Flut, viele Verbindungen, Beaconing, Dateiwelle, ML-Abweichung)
# – die machen Rauschen und dürfen gedämpft oder leiser werden, wenn dasselbe Subjekt wiederholt
# harmlos war. Muster-Regeln fragen „was?“ (-enc in der Kommandozeile, Start aus Temp, C2-Port,
# neuer Dienst …): da ist JEDER Treffer eine eigene Frage, auch beim bekannten Prozess. Beispiel:
# R001·powershell.exe 23× harmlos (Abfragen der KI selbst) darf ein echtes `powershell -enc`
# nicht stumm schalten.
MENGENREGELN = {"R004", "R007", "R008", "R009", "ML-001"}


@dataclass
class Treffer:
    regel: str
    titel: str
    text: str
    schwere: Schwere
    subjekt: str = ""       # leer = aus dem Ereignis ableiten (siehe subjekt_von)


def subjekt_von(e: Ereignis) -> str:
    """Worum es bei einem Ereignis geht – der Schlüssel, unter dem gleiche Alarme
    zusammengefasst werden: Prozess (bei Netz „prozess → ziel“), sonst Datei,
    sonst der Text des Eintrags (Inventar: neuer Dienst, neue Aufgabe …)."""
    if e.kategorie == NETZ and e.prozess and e.ziel:
        return f"{e.prozess} → {e.ziel}"
    return e.prozess or e.datei or e.ziel or " ".join(str(e.text or "").split())[:120]


def subjekt_fuer(regel: str, e: Ereignis) -> str:
    """Subjekt aus Regel + Ereignis – dieselbe Zuordnung, die die Regeln selbst treffen.
    Gebraucht, um Alarme von vor dem 20.09.2026 (ohne Subjekt) nachzurüsten."""
    if regel == "R004":
        return e.eltern or "?"
    if regel == "R008":
        return e.prozess or "?"
    if regel == "R009":
        return "überwachte Ordner"
    if regel == "ML-001":
        return f"{e.prozess or e.datei or e.ziel or 'Systemereignis'} · {e.aktion}"
    return subjekt_von(e)


def _kuerzen(dq: deque, grenze: float) -> None:
    while dq and dq[0] < grenze:
        dq.popleft()


class Regelwerk:
    def __init__(self, fenster: int = 300, stumm: set[str] | None = None):
        self.fenster = fenster
        self.stumm: set[str] = set(stumm or ())
        self._starts: dict[str, deque] = defaultdict(deque)
        self._conns: dict[str, deque] = defaultdict(deque)
        self._dateien: deque = deque()
        self._beacon: dict[tuple[str, str], deque] = defaultdict(deque)
        self.treffer: dict[str, int] = defaultdict(int)
        self.regeln = [self._r001, self._r002, self._r003, self._r004, self._r005, self._r006,
                       self._r007, self._r008, self._r009, self._r010, self._r011, self._r012,
                       self._r013, self._r014]

    def pruefen(self, e: Ereignis) -> list[Alarm]:
        self._zustand(e)
        out = []
        for regel in self.regeln:
            try:
                t = regel(e)
            except Exception:
                continue
            if t is None:
                continue
            self.treffer[t.regel] += 1
            if t.regel in self.stumm:
                continue
            out.append(Alarm(titel=t.titel, text=t.text, schwere=t.schwere, regel=t.regel,
                             ereignis_id=e.id, quelle="regel", zeit=e.zeit,
                             subjekt=t.subjekt or subjekt_von(e)))
        return out

    def _zustand(self, e: Ereignis) -> None:
        jetzt = e.zeit or time.time()
        grenze = jetzt - self.fenster
        if e.kategorie == PROZESS and e.aktion == "process_start":
            dq = self._starts[e.eltern or "?"]; dq.append(jetzt); _kuerzen(dq, grenze)
        elif e.kategorie == NETZ and e.aktion == "conn_open":
            dq = self._conns[e.prozess or "?"]; dq.append(jetzt); _kuerzen(dq, grenze)
            if e.ziel:
                bq = self._beacon[(e.prozess or "?", e.ziel)]; bq.append(jetzt); _kuerzen(bq, grenze)
        elif e.kategorie == DATEI and e.aktion in ("file_created", "file_modified", "file_deleted"):
            self._dateien.append(jetzt); _kuerzen(self._dateien, grenze)
        if len(self._beacon) > 500:
            self._beacon.clear()

    # ------------------------------------------------------------------ Regeln

    def _r001(self, e):
        if e.kategorie != PROZESS or e.aktion != "process_start" or not e.cmdline:
            return None
        if eigener_aufruf(e):
            self.treffer["eigen"] += 1
            return None
        gefunden = _flags_in(e.cmdline)
        if not gefunden:
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
        if e.kategorie != PROZESS or e.aktion != "process_start":
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
        ziel = (e.exe or "").lower()
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
