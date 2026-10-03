"""Ereignis → Zahlenvektor. Zwei Merkmalssätze, zwei Modelle.

Warum zwei? Ein Datei-Ereignis kennt keinen Verursacher-Prozess (watchdog sagt
„eine Datei wurde angelegt“, nicht „wer“). Im Prozessmodell blieben 25 von 30
Achsen leer – und der Isolation Forest isoliert genau das, was strukturell aus
der Masse fällt: im SIEM galten 79 % der harmlosen Datei-Ereignisse als
Anomalie. Deshalb ein eigener Satz für Dateien (Ordner, Endung, Name, Zeit, Rate).

Regeln, die aus gemessenen Fehlern stammen:
- `extract()` ist eine reine Funktion: gleiches Ereignis, gleicher Profilstand
  → gleicher Vektor. Die Ereignisrate (`burst`) wird deshalb NICHT hier
  berechnet, sondern beim Aufnehmen gestempelt (sonst 6 Sigma Drift zwischen
  Training und Bewertung).
- Stundenhistogramm erst ab 100 Beobachtungen und mit Laplace-Glättung, sonst
  meldet es Rauschen.
- Zeit zyklisch (sin/cos), Seltenheit logarithmisch, Entropie normiert.
- Vollscan-Merkmale (`vollscan_*`) hängen nur vom Stand des letzten fertigen
  Vollscans ab, nicht vom Zustand der Datei jetzt – sonst sähe das Training
  andere Werte als die Bewertung. Ohne Vollscan sind sie 0.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime

from .ereignisse import DATEI, NETZ, PROZESS, Ereignis

PORTS_AUFFAELLIG = {22, 23, 445, 1433, 3306, 3389, 4444, 5432, 5900, 6667, 9001, 9050}
_EXEC = {".exe", ".dll", ".ps1", ".bat", ".cmd", ".vbs", ".js", ".scr"}

PROZESS_MERKMALE = [
    "stunde_sin", "stunde_cos", "wochenende", "nacht",
    "kat_prozess", "kat_netz", "kat_datei", "schwere",
    "seltenheit", "prozess_neu", "pfad_neu", "eltern_neu",
    "cpu", "mem", "cmd_laenge", "cmd_entropie", "name_entropie",
    "port", "port_auffaellig", "extern",
    "pfad_tiefe", "in_temp", "ausfuehrbar", "sensibel", "burst",
    "stunde_untypisch", "nutzer_neu", "port_neu_fuer_prozess", "kind_neu", "ausserhalb_software",
    "vollscan_bekannt", "vollscan_neu", "vollscan_signiert",
]
DATEI_MERKMALE = [
    "stunde_sin", "stunde_cos", "wochenende", "nacht",
    "angelegt", "geaendert", "geloescht", "verschoben",
    "ordner_seltenheit", "ordner_neu", "endung_seltenheit", "endung_neu", "endung_neu_im_ordner",
    "name_entropie", "name_laenge", "ziffern_anteil", "pfad_tiefe", "in_temp", "sensibel",
    "ausfuehrbar", "schwere", "burst",
    "vollscan_bekannt", "vollscan_neu", "vollscan_signiert", "vollscan_ordner",
]

# Gewichte für die Zeit vor dem ersten Training.
PROZESS_KALTSTART = {"prozess_neu": 0.25, "pfad_neu": 0.15, "eltern_neu": 0.15, "seltenheit": 0.10,
                     "extern": 0.08, "port_auffaellig": 0.10, "name_entropie": 0.07,
                     "in_temp": 0.05, "sensibel": 0.05}
DATEI_KALTSTART = {"ordner_neu": 0.20, "endung_neu_im_ordner": 0.15, "ausfuehrbar": 0.20,
                   "sensibel": 0.15, "name_entropie": 0.15, "nacht": 0.10, "burst": 0.15}

PROZESS_ERKLAERUNG = [
    ("prozess_neu", "Prozess noch nie gesehen"), ("pfad_neu", "ungewohnter Programmpfad"),
    ("eltern_neu", "ungewohnter Elternprozess"), ("seltenheit", "sehr seltener Prozess"),
    ("nacht", "Aktivität zur Nachtzeit"), ("extern", "Verbindung ins offene Internet"),
    ("port_auffaellig", "auffälliger Zielport"), ("name_entropie", "zufällig wirkender Name"),
    ("cmd_entropie", "verschleierte Kommandozeile"), ("in_temp", "läuft aus dem Temp-Ordner"),
    ("sensibel", "Änderung an sensiblem Ort"), ("ausfuehrbar", "ausführbare Datei betroffen"),
    ("burst", "auffällig viele Ereignisse in kurzer Zeit"), ("cpu", "hohe CPU-Last"),
    ("mem", "hoher Speicherverbrauch"), ("stunde_untypisch", "für diesen Prozess untypische Uhrzeit"),
    ("nutzer_neu", "ungewohnter Benutzerkontext"),
    ("port_neu_fuer_prozess", "dieser Prozess sprach dieses Ziel bisher nie an"),
    ("kind_neu", "dieser Elternprozess startete dieses Kind bisher nie"),
    ("ausserhalb_software", "läuft aus keinem bekannten Installationspfad"),
    ("vollscan_neu", "Programm kam erst nach dem Vollscan auf den Rechner"),
]
DATEI_ERKLAERUNG = [
    ("ordner_neu", "Ordner bisher unbekannt"), ("endung_neu", "Dateiendung bisher unbekannt"),
    ("endung_neu_im_ordner", "diese Dateiart gab es in diesem Ordner noch nie"),
    ("ausfuehrbar", "ausführbare Datei betroffen"), ("sensibel", "Änderung an sensiblem Ort"),
    ("in_temp", "liegt im Temp-Ordner"), ("name_entropie", "zufällig wirkender Dateiname"),
    ("ziffern_anteil", "auffällig viele Ziffern im Namen"), ("nacht", "Änderung zur Nachtzeit"),
    ("wochenende", "Änderung am Wochenende"), ("burst", "viele Dateiänderungen in kurzer Zeit"),
    ("ordner_seltenheit", "selten genutzter Ordner"), ("endung_seltenheit", "seltene Dateiendung"),
    ("geloescht", "Datei wurde gelöscht"),
]


def entropie(text: str) -> float:
    """Shannon-Entropie, normiert 0..1 – hoch bei a8f3k2p9.exe."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    h = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return min(h / (math.log2(min(n, 64)) or 1.0), 1.0)


def seltenheit(n: int) -> float:
    return 1.0 / (1.0 + math.log1p(n)) if n > 0 else 1.0


class Burst:
    """Ereignisse je Schlüssel in einem gleitenden 60-s-Fenster (wird beim Aufnehmen gestempelt)."""

    def __init__(self, fenster: float = 60.0, saettigung: float = 30.0):
        self.fenster, self.saettigung = fenster, saettigung
        self._k: dict[str, list[float]] = {}

    def beobachten(self, key: str, ts: float) -> float:
        if not key:
            return 0.0
        w = self._k.setdefault(key, [])
        w.append(ts)
        grenze = ts - self.fenster
        while w and w[0] < grenze:
            w.pop(0)
        if len(self._k) > 500:
            self._k.clear()
        return min(len(w) / self.saettigung, 1.0)


def _zeitmerkmale(ts: float) -> tuple[list[float], datetime]:
    dt = datetime.fromtimestamp(ts or time.time())
    h = dt.hour + dt.minute / 60.0
    return [math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24),
            1.0 if dt.weekday() >= 5 else 0.0, 1.0 if (h >= 22 or h < 6) else 0.0], dt


def _erklaeren(tabelle, namen, vektor, top=4) -> list[str]:
    idx = {n: i for i, n in enumerate(namen)}
    gefunden = [(vektor[idx[k]], t) for k, t in tabelle if vektor[idx[k]] > 0.5]
    gefunden.sort(reverse=True, key=lambda x: x[0])
    return [t for _, t in gefunden[:top]]


# ---------------------------------------------------------------------------
# Prozess- und Netz-Ereignisse
# ---------------------------------------------------------------------------

def _vollscan(baseline, pfad: str) -> list[float]:
    """[bekannt, neu, signiert] aus dem letzten Vollscan; ohne Vollscan 0."""
    if baseline is None or not pfad:
        return [0.0, 0.0, 0.0]
    return list(baseline.datei(pfad))


class ProzessMerkmale:
    namen = PROZESS_MERKMALE
    kaltstart = PROZESS_KALTSTART
    MIN_HISTOGRAMM = 100

    def __init__(self, baseline=None):
        self.profile: dict[str, dict] = {}
        self.software: set[str] = set()
        self.baseline = baseline

    def profile_setzen(self, profile: dict[str, dict], software: set[str] | None = None) -> None:
        self.profile = profile
        if software is not None:
            self.software = {p for p in software if p}

    def vektor(self, e: Ereignis) -> list[float]:
        zeit, dt = _zeitmerkmale(e.zeit)
        p = self.profile.get(e.prozess) if e.prozess else None
        n = p["anzahl"] if p else 0
        pfad_neu = eltern_neu = 0.0
        if p:
            if e.exe and e.exe not in p["exes"].split("|"):
                pfad_neu = 1.0
            if e.eltern and e.eltern not in p["eltern"].split("|"):
                eltern_neu = 1.0
        elif e.kategorie == PROZESS:
            pfad_neu = 1.0 if e.exe else 0.0
            eltern_neu = 1.0 if e.eltern else 0.0
        x = e.extra or {}
        pfad = e.datei or e.exe
        low = pfad.lower()
        ext = pfad[pfad.rfind("."):].lower() if "." in pfad else ""
        name = os.path.basename(e.datei) if e.datei else e.prozess
        return zeit + [
            1.0 if e.kategorie == PROZESS else 0.0, 1.0 if e.kategorie == NETZ else 0.0,
            1.0 if e.kategorie == DATEI else 0.0, int(e.schwere) / 4.0,
            seltenheit(n), 1.0 if (p is None and e.prozess) else 0.0, pfad_neu, eltern_neu,
            min(float(x.get("cpu", 0.0)) / 100.0, 1.0), min(float(x.get("mem_mb", 0.0)) / 2048.0, 1.0),
            min(len(e.cmdline) / 500.0, 1.0), entropie(e.cmdline[:200]), entropie(name),
            min(e.zielport / 65535.0, 1.0), 1.0 if e.zielport in PORTS_AUFFAELLIG else 0.0,
            1.0 if x.get("extern") else 0.0,
            min(low.count("\\") / 10.0, 1.0), 1.0 if ("\\temp" in low or "/tmp" in low) else 0.0,
            1.0 if ext in _EXEC else 0.0, 1.0 if x.get("sensibel") else 0.0,
            float(x.get("burst", 0.0)),
            self._stunde_untypisch(p, dt.hour), self._neu_in(p, "nutzer", e.nutzer),
            self._neu_in(p, "ports", str(e.zielport) if e.zielport else ""),
            self._kind_neu(e.eltern, e.prozess), self._ausserhalb(e.exe),
        ] + _vollscan(self.baseline, e.exe)

    @classmethod
    def _stunde_untypisch(cls, p, stunde: int) -> float:
        if not p:
            return 0.0
        hist = p.get("stunden") or []
        gesamt = sum(hist)
        if gesamt < cls.MIN_HISTOGRAMM:
            return 0.0
        n = hist[stunde] if 0 <= stunde < len(hist) else 0
        anteil = (n + 1.0) / (gesamt + 24.0)               # Laplace
        return float(min(max(1.0 - anteil * 24.0, 0.0), 1.0))

    @staticmethod
    def _neu_in(p, feld: str, wert: str) -> float:
        if not wert or not p:
            return 0.0
        return 0.0 if wert in (p.get(feld) or "").split("|") else 1.0

    def _kind_neu(self, eltern: str, kind: str) -> float:
        if not eltern or not kind:
            return 0.0
        p = self.profile.get(eltern)
        if not p:
            return 0.0
        return 0.0 if kind in (p.get("kinder") or "").split("|") else 1.0

    def _ausserhalb(self, exe: str) -> float:
        if not exe or not self.software:
            return 0.0
        low = exe.lower()
        for p in self.software:
            if low.startswith(p):
                return 0.0
        for sysdir in ("c:\\windows\\", "c:\\program files\\", "c:\\program files (x86)\\"):
            if low.startswith(sysdir):
                return 0.0
        return 1.0

    def erklaeren(self, vektor: list[float]) -> list[str]:
        return _erklaeren(PROZESS_ERKLAERUNG, self.namen, vektor)


# ---------------------------------------------------------------------------
# Datei-Ereignisse
# ---------------------------------------------------------------------------

class DateiMerkmale:
    namen = DATEI_MERKMALE
    kaltstart = DATEI_KALTSTART

    def __init__(self, baseline=None):
        self.ordner: dict[str, int] = {}
        self.endungen: dict[str, int] = {}
        self.paare: set[str] = set()
        self.gesamt = 0
        self.baseline = baseline

    @staticmethod
    def _teile(e: Ereignis) -> tuple[str, str]:
        if not e.datei:
            return "", ""
        x = e.extra or {}
        return ((x.get("ordner") or os.path.dirname(e.datei)).lower(),
                (x.get("endung") or os.path.splitext(e.datei)[1]).lower())

    def anpassen(self, ereignisse: list[Ereignis]) -> None:
        """Normalbild aus der Trainingsmenge – vor dem Extrahieren, mit dem Modell gespeichert."""
        self.ordner, self.endungen, self.paare, self.gesamt = {}, {}, set(), 0
        for e in ereignisse:
            o, x = self._teile(e)
            if o:
                self.ordner[o] = self.ordner.get(o, 0) + 1
            if x:
                self.endungen[x] = self.endungen.get(x, 0) + 1
            if o and x:
                self.paare.add(f"{o}|{x}")
            self.gesamt += 1

    def zustand(self) -> dict:
        return {"ordner": self.ordner, "endungen": self.endungen, "paare": sorted(self.paare),
                "gesamt": self.gesamt}

    def zustand_laden(self, z: dict) -> None:
        self.ordner = dict(z.get("ordner") or {})
        self.endungen = dict(z.get("endungen") or {})
        self.paare = set(z.get("paare") or [])
        self.gesamt = int(z.get("gesamt") or 0)

    def vektor(self, e: Ereignis) -> list[float]:
        zeit, _ = _zeitmerkmale(e.zeit)
        o, x = self._teile(e)
        no = self.ordner.get(o, 0) if o else 0
        nx = self.endungen.get(x, 0) if x else 0
        name = os.path.basename(e.datei or "")
        ziffern = sum(1 for ch in name if ch.isdigit())
        low = (e.datei or "").lower()
        ex = e.extra or {}
        return zeit + [
            1.0 if e.aktion == "file_created" else 0.0, 1.0 if e.aktion == "file_modified" else 0.0,
            1.0 if e.aktion == "file_deleted" else 0.0, 1.0 if e.aktion == "file_moved" else 0.0,
            seltenheit(no), 1.0 if (o and no == 0) else 0.0,
            seltenheit(nx), 1.0 if (x and nx == 0) else 0.0,
            1.0 if (o and x and no > 0 and f"{o}|{x}" not in self.paare) else 0.0,
            entropie(os.path.splitext(name)[0]), min(len(name) / 60.0, 1.0),
            (ziffern / len(name)) if name else 0.0,
            min(low.count("\\") / 10.0, 1.0), 1.0 if ("\\temp" in low or "/tmp" in low) else 0.0,
            1.0 if ex.get("sensibel") else 0.0, 1.0 if x in _EXEC else 0.0,
            int(e.schwere) / 4.0, float(ex.get("burst", 0.0)),
        ] + _vollscan(self.baseline, e.datei) + [
            self.baseline.ordner(os.path.dirname(e.datei)) if (self.baseline is not None and e.datei) else 0.0,
        ]

    def erklaeren(self, vektor: list[float]) -> list[str]:
        return _erklaeren(DATEI_ERKLAERUNG, self.namen, vektor)
