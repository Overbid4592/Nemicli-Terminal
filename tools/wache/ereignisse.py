"""Das gemeinsame Schema: Ereignis, Alarm, Schwere.

Alle Sensoren liefern Ereignisse in genau dieser Form; Regeln und Modell
sehen nie etwas anderes. Ein Ereignis entspricht einer Zeile in der Tabelle
`ereignisse`, ein Alarm einer Zeile in `alarme`.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum


class Schwere(IntEnum):
    INFO = 0
    NIEDRIG = 1
    MITTEL = 2
    HOCH = 3
    KRITISCH = 4

    @property
    def label(self) -> str:
        return {0: "Info", 1: "Niedrig", 2: "Mittel", 3: "Hoch", 4: "Kritisch"}[int(self)]

    @classmethod
    def aus_text(cls, text: str) -> "Schwere":
        t = (text or "").strip().lower()
        for s in cls:
            if s.label.lower() == t or s.name.lower() == t:
                return s
        try:
            return cls(int(t))
        except (ValueError, TypeError):
            return cls.HOCH


# Kategorien der Sensoren
PROZESS = "prozess"
NETZ = "netz"
DATEI = "datei"
SYSTEM = "system"


@dataclass
class Ereignis:
    kategorie: str
    aktion: str                          # process_start, conn_open, file_created, inventory_new_…
    text: str
    zeit: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    schwere: Schwere = Schwere.INFO

    # Prozess-Kontext
    prozess: str = ""
    pid: int = 0
    ppid: int = 0
    eltern: str = ""
    nutzer: str = ""
    exe: str = ""
    cmdline: str = ""

    # Netz-Kontext
    lokal: str = ""
    ziel: str = ""
    zielport: int = 0
    protokoll: str = ""

    # Datei-Kontext
    datei: str = ""

    # Bewertung (füllt der Motor)
    score: float = 0.0
    anomalie: bool = False

    extra: dict = field(default_factory=dict)

    SPALTEN = ("id", "zeit", "kategorie", "aktion", "schwere", "text", "prozess", "pid",
               "ppid", "eltern", "nutzer", "exe", "cmdline", "lokal", "ziel", "zielport",
               "protokoll", "datei", "score", "anomalie", "extra")

    def zeile(self) -> tuple:
        return (self.id, self.zeit, self.kategorie, self.aktion, int(self.schwere), self.text,
                self.prozess, self.pid, self.ppid, self.eltern, self.nutzer, self.exe,
                self.cmdline, self.lokal, self.ziel, self.zielport, self.protokoll, self.datei,
                self.score, int(self.anomalie),
                json.dumps(self.extra, ensure_ascii=False) if self.extra else "")

    @staticmethod
    def aus_zeile(r) -> "Ereignis":
        return Ereignis(
            id=r["id"], zeit=r["zeit"], kategorie=r["kategorie"], aktion=r["aktion"],
            schwere=Schwere(r["schwere"]), text=r["text"], prozess=r["prozess"], pid=r["pid"],
            ppid=r["ppid"], eltern=r["eltern"], nutzer=r["nutzer"], exe=r["exe"],
            cmdline=r["cmdline"], lokal=r["lokal"], ziel=r["ziel"], zielport=r["zielport"],
            protokoll=r["protokoll"], datei=r["datei"], score=r["score"],
            anomalie=bool(r["anomalie"]), extra=json.loads(r["extra"]) if r["extra"] else {})

    def kurz(self) -> str:
        """Eine Zeile für Berichte und die Persönlichkeit."""
        t = time.strftime("%d.%m. %H:%M:%S", time.localtime(self.zeit))
        return f"[{t}] {self.schwere.label:<8} {self.kategorie}/{self.aktion}: {self.text}"


@dataclass
class Alarm:
    titel: str
    text: str
    schwere: Schwere
    regel: str                           # R001…R014 oder ML-001
    ereignis_id: str = ""
    zeit: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    quelle: str = "regel"                # "regel" oder "ml"
    score: float = 0.0
    status: str = "offen"                # offen · gesehen · harmlos · echt
    urteil_von: str = ""                 # wer den Status gesetzt hat (Persönlichkeit, Nutzer)
    begruendung: str = ""
    # Worum es geht – der Prozess, bei R004 der Elternprozess, bei Dateien der Pfad, bei
    # Netz „prozess → ziel“. Regel + Subjekt ist der Schlüssel fürs Verdichten
    # (derselbe Vorgang soll nicht viele Einzelalarme geben) und fürs Dämpfen.
    subjekt: str = ""
    anzahl: int = 1                      # wie oft dasselbe innerhalb des Cooldowns wiederkam
    zuletzt: float = 0.0                 # Zeit des letzten Wiederkommens (0 = nur einmal)

    SPALTEN = ("id", "zeit", "titel", "text", "schwere", "regel", "ereignis_id", "quelle", "score",
               "status", "urteil_von", "begruendung", "subjekt", "anzahl", "zuletzt")

    def zeile(self) -> tuple:
        return (self.id, self.zeit, self.titel, self.text, int(self.schwere), self.regel,
                self.ereignis_id, self.quelle, self.score, self.status, self.urteil_von,
                self.begruendung, self.subjekt, self.anzahl, self.zuletzt)

    @staticmethod
    def aus_zeile(r) -> "Alarm":
        k = r.keys()
        return Alarm(id=r["id"], zeit=r["zeit"], titel=r["titel"], text=r["text"],
                     schwere=Schwere(r["schwere"]), regel=r["regel"],
                     ereignis_id=r["ereignis_id"], quelle=r["quelle"], score=r["score"],
                     status=r["status"], urteil_von=r["urteil_von"],
                     begruendung=r["begruendung"],
                     subjekt=r["subjekt"] if "subjekt" in k else "",
                     anzahl=int(r["anzahl"] or 1) if "anzahl" in k else 1,
                     zuletzt=float(r["zuletzt"] or 0.0) if "zuletzt" in k else 0.0)

    def kurz(self) -> str:
        t = time.strftime("%d.%m. %H:%M", time.localtime(self.zeit))
        if self.anzahl > 1 and self.zuletzt:
            t += f"→{time.strftime('%H:%M', time.localtime(self.zuletzt))} ×{self.anzahl}"
        s = f" ({self.score:.3f})" if self.score else ""
        return f"#{self.id} [{t}] {self.schwere.label:<8} {self.regel}{s} · {self.titel} · {self.status}"
