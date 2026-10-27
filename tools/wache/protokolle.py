"""protokolle.py - Sensor für Windows-Ereignisprotokolle, die ohne Adminrechte lesbar sind.

Vorbild: Hayabusa und Chainsaw (Erkennung aus Ereignisprotokollen). Gelesen wird über wevtapi.dll im eigenen
Prozess (herkunft._evt_xml), nie das Security-Protokoll und nie Protokolle der Schutzsoftware.

  System 7045                 Dienst installiert       → Inventar sofort neu (R011 meldet mit allen Angaben)
  TaskScheduler 106           Aufgabe angelegt         → Inventar sofort neu
  WMI-Activity 5861           dauerhaftes WMI-Abo      → R017
  PowerShell 4104 (Warnung)   von Windows als verdächtig markiertes Skript → nur Beleg, kein Alarm
                              (auf einem Alltagsrechner Hunderte am Tag, fast alle von normalen Werkzeugen)

Beim ersten Durchlauf wird nur der Stand gemerkt – Altes wird nicht nachgemeldet. Ein Kanal, der sich nicht
lesen lässt (abgeschaltet, kein Zugriff), liefert einfach nichts.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from . import herkunft
from .ereignisse import SYSTEM, Ereignis

QUELLEN = (
    ("System", 7045, "", "dienst_installiert"),
    ("Microsoft-Windows-TaskScheduler/Operational", 106, "", "aufgabe_angelegt"),
    ("Microsoft-Windows-WMI-Activity/Operational", 5861, "", "wmi_dauerabo"),
    ("Microsoft-Windows-PowerShell/Operational", 4104, " and (Level=3)", "powershell_verdaechtig"),
)
INSTALLATION = {"evtlog_dienst_installiert", "evtlog_aufgabe_angelegt"}
_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
SKRIPT_MAX = 1500


def lesen(xml: str) -> dict | None:
    """{rec, zeit, felder} aus dem XML eines Ereignisses – Felder aus EventData oder UserData."""
    try:
        wurzel = ET.fromstring(xml)
    except ET.ParseError:
        return None
    system = wurzel.find(f"{_NS}System")
    if system is None:
        return None
    try:
        rec = int(system.findtext(f"{_NS}EventRecordID") or 0)
    except ValueError:
        return None
    zeit_el = system.find(f"{_NS}TimeCreated")
    zeit = herkunft._zeit_lokal(zeit_el.get("SystemTime", ""))[0] if zeit_el is not None else 0.0
    felder: dict[str, str] = {}
    daten = wurzel.find(f"{_NS}EventData")
    if daten is not None:
        for d in daten:
            if d.get("Name"):
                felder[d.get("Name")] = (d.text or "").strip()
    nutzer = wurzel.find(f"{_NS}UserData")
    if nutzer is not None:
        for el in nutzer.iter():
            name = el.tag.split("}")[-1]
            if len(el) == 0 and (el.text or "").strip():
                felder[name] = el.text.strip()
    return {"rec": rec, "zeit": zeit or time.time(), "felder": felder}


def ereignis(art: str, d: dict) -> Ereignis | None:
    f = d["felder"]
    if art == "dienst_installiert":
        name, bild = f.get("ServiceName", "?"), f.get("ImagePath", "")
        from .systemstand import programmpfad
        return Ereignis(SYSTEM, "evtlog_dienst_installiert", f"Dienst installiert (Protokoll): {name} ({bild})",
                        zeit=d["zeit"], exe=programmpfad(bild) if bild else "", cmdline=bild[:500],
                        nutzer=f.get("AccountName", ""), extra={"dienst": name, "start": f.get("StartType", "")})
    if art == "aufgabe_angelegt":
        name = f.get("TaskName", "?")
        return Ereignis(SYSTEM, "evtlog_aufgabe_angelegt", f"Geplante Aufgabe angelegt (Protokoll): {name}",
                        zeit=d["zeit"], nutzer=f.get("UserContext", ""), extra={"aufgabe": name})
    if art == "wmi_dauerabo":
        consumer = f.get("CONSUMER") or f.get("Consumer") or "?"
        return Ereignis(SYSTEM, "evtlog_wmi_dauerabo",
                        f"Dauerhaftes WMI-Abo angelegt: {consumer} · Filter: {f.get('ESS', '?')} · "
                        f"Namensraum {f.get('Namespace', '?')}",
                        zeit=d["zeit"], cmdline=(f.get("PossibleCause") or "")[:500],
                        extra={"consumer": consumer[:200]})
    if art == "powershell_verdaechtig":
        skript = f.get("ScriptBlockText", "")
        from .regeln import EIGENER_RAHMEN
        if EIGENER_RAHMEN in skript.lower():           # NemiCLIs eigener PowerShell-Rahmen
            return None
        pfad = f.get("Path", "")
        return Ereignis(SYSTEM, "evtlog_powershell_verdaechtig",
                        "PowerShell-Skript, das Windows als verdächtig markiert hat"
                        + (f": {pfad}" if pfad else ""),
                        zeit=d["zeit"], prozess="powershell.exe", cmdline=" ".join(skript.split())[:SKRIPT_MAX],
                        datei=pfad, extra={"skriptblock": f.get("ScriptBlockId", "")})
    return None


class ProtokollSensor:
    name = "protokolle"

    def __init__(self, intervall: float = 30.0, abfragen=None):
        self.intervall = intervall
        self._abfragen = abfragen or herkunft._evt_xml
        self._stand: dict[str, int] = {}                # Kanal → höchste gesehene EventRecordID

    def sammeln(self) -> list[Ereignis]:
        out: list[Ereignis] = []
        for kanal, eid, zusatz, art in QUELLEN:
            roh = self._abfragen(kanal, f"*[System[(EventID={eid}){zusatz}]]", 50)
            eintraege = [d for d in (lesen(x) for x in roh) if d is not None]
            hoechste = max((d["rec"] for d in eintraege), default=0)
            if kanal not in self._stand:                # erster Durchlauf: nur den Stand merken
                self._stand[kanal] = hoechste
                continue
            neu = sorted((d for d in eintraege if d["rec"] > self._stand[kanal]), key=lambda d: d["rec"])
            self._stand[kanal] = max(self._stand[kanal], hoechste)
            for d in neu:
                e = ereignis(art, d)
                if e is not None:
                    out.append(e)
        return out
