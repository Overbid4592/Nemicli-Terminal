"""absolvierung.py – Programm-Absolvierung: ein Programm auf Anweisung beobachten und sein Normalbild lernen.

Der Nutzer (oder die Persönlichkeit auf seinen Wunsch) gibt ein Programm in Auftrag („pass auf Steam auf“). Die Wache
merkt sich dann alles, was von dessen Prozessen abstammt – Kinder und Enkel, also die Hintergrund-Prozesse, die das
Programm startet. In der Lernphase kommt jeder davon ins Normalbild. Danach wacht sie: Startet das Programm etwas, das
nicht im Normalbild steht, gibt es den Alarm R015, und die Persönlichkeit urteilt wie bei jedem Alarm. Nach einem
„harmlos“ zu genau diesem Prozess kommt er beim nächsten Start ins Normalbild; „echt“ lässt ihn weiter melden.

Zwei Dateien je Programm unter Wache/programme/: <name>.auftrag.json schreibt nur das Hauptprogramm (Werkzeug
wache_programm, /wache programm), <name>.bild.json nur der Wache-Prozess – keiner überschreibt den anderen.
Gemerkt werden Name und Pfad der Prozesse, nie Kommandozeilen. Ohne Adminrechte: Wie alle Sensoren sieht die Wache
nur, was der Prozess-Schnappschuss zeigt (alle 3 s) – wer schneller wieder weg ist, fehlt.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import psutil

from . import ORDNER as _WACHE
from .ereignisse import PROZESS, Alarm, Ereignis, Schwere

ORDNER = _WACHE / "programme"
REGEL = "R015"
LERNEN_TAGE = 3                     # Vorgabe der Lernphase
LERNEN_MAX_TAGE = 60
PRUEFEN_S = 5.0                     # so oft schaut der Wache-Prozess nach neuen Aufträgen
SPEICHERN_S = 30.0                  # Normalbild höchstens so oft schreiben
MAX_KINDER = 400                    # Einträge je Normalbild
MAX_PIDS = 5000
_NAME = re.compile(r"^[\w .+()-]{1,80}\.exe$", re.IGNORECASE)


def schluessel(programm: str) -> str:
    """„Steam“, „steam.exe“, „C:\\…\\steam.exe“ → „steam.exe“. ValueError bei Unbrauchbarem."""
    name = os.path.basename(str(programm or "").strip().strip('"')).lower()
    if name and "." not in name:
        name += ".exe"
    if not _NAME.match(name):
        raise ValueError(f"„{programm}“ ist kein Programmname (z. B. steam oder steam.exe).")
    return name


def kind_schluessel(name: str, exe: str) -> str:
    return (exe or name or "?").lower()


def _datei(key: str, art: str, ordner: Path | None = None) -> Path:
    return (ordner or ORDNER) / f"{key}.{art}.json"


def _lesen(pfad: Path) -> dict:
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        return daten if isinstance(daten, dict) else {}
    except (OSError, ValueError):
        return {}


def _schreiben(pfad: Path, daten: dict) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, pfad)


def auftrag(key: str, ordner: Path | None = None) -> dict:
    return _lesen(_datei(key, "auftrag", ordner))


def bild(key: str, ordner: Path | None = None) -> dict:
    return _lesen(_datei(key, "bild", ordner))


def auftraege(ordner: Path | None = None) -> list[dict]:
    o = ordner or ORDNER
    if not o.is_dir():
        return []
    return [a for p in sorted(o.glob("*.auftrag.json")) if (a := _lesen(p)).get("programm")]


def lernt(a: dict, jetzt: float | None = None) -> bool:
    return (jetzt if jetzt is not None else time.time()) < float(a.get("lernen_bis") or 0)


# --- Hauptprogramm: Aufträge -------------------------------------------------------------------------------

def starten(programm: str, tage: float = LERNEN_TAGE, von: str = "", anzeige: str = "",
            ordner: Path | None = None) -> dict:
    """Programm in Auftrag geben (oder die Lernphase neu setzen). Das Normalbild bleibt erhalten."""
    key = schluessel(programm)
    tage = max(0.0, min(float(tage), LERNEN_MAX_TAGE))
    jetzt = time.time()
    alt = auftrag(key, ordner)
    neu = {"programm": key, "anzeige": (anzeige or alt.get("anzeige") or str(programm)).strip()[:60],
           "seit": alt.get("seit") if alt and not alt.get("aus") else jetzt,
           "lernen_bis": jetzt + tage * 86400, "aus": False, "erlaubt": list(alt.get("erlaubt") or []),
           "von": von or alt.get("von") or ""}
    _schreiben(_datei(key, "auftrag", ordner), neu)
    return neu


def stoppen(programm: str, ordner: Path | None = None) -> dict | None:
    key = schluessel(programm)
    a = auftrag(key, ordner)
    if not a:
        return None
    a["aus"] = True
    _schreiben(_datei(key, "auftrag", ordner), a)
    return a


def erlauben(programm: str, prozess: str, ordner: Path | None = None) -> dict | None:
    """Einen Prozess von Hand ins Normalbild (Name wie „foo.exe“ oder voller Pfad)."""
    key = schluessel(programm)
    a = auftrag(key, ordner)
    if not a:
        return None
    p = str(prozess or "").strip().strip('"').lower()
    if not p:
        raise ValueError("Welcher Prozess? (Name oder Pfad)")
    if p not in a.setdefault("erlaubt", []):
        a["erlaubt"].append(p)
    _schreiben(_datei(key, "auftrag", ordner), a)
    return a


def laufende(key: str) -> list[dict]:
    """Was gerade von dem Programm abstammt (für den Status – im Hauptprogramm gemessen)."""
    raus, gesehen = [], set()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info.get("name") or "").lower() != key:
                continue
            for kind in p.children(recursive=True):
                if kind.pid in gesehen:
                    continue
                gesehen.add(kind.pid)
                try:
                    raus.append({"pid": kind.pid, "name": kind.name(), "exe": kind.exe() or ""})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return raus


def _erlaubt(a: dict, k: str, name: str) -> bool:
    erlaubt = {str(x).lower() for x in a.get("erlaubt") or []}
    return k in erlaubt or (name or "").lower() in erlaubt


# --- Wache-Prozess: beobachten ------------------------------------------------------------------------------

class Absolvierung:
    """Hängt im Motor an jedem Prozess-Ereignis. `urteile(regel, subjekt)` → {status: anzahl} aus der Datenbank."""

    def __init__(self, ordner: Path | None = None, urteile=None):
        self.ordner = ordner or ORDNER
        self.urteile = urteile
        self._auftraege: dict[str, dict] = {}
        self._stempel: dict[str, float] = {}
        self._bilder: dict[str, dict] = {}
        self._pids: dict[int, tuple[str, float]] = {}       # PID → (Programm, Startzeit)
        self._geaendert: set[str] = set()
        self._geprueft = 0.0
        self._gespeichert = 0.0

    # Aufträge kommen aus dem Hauptprogramm als Datei
    def auftraege_pruefen(self, jetzt: float | None = None, *, erzwingen: bool = False) -> None:
        jetzt = jetzt if jetzt is not None else time.time()
        if not erzwingen and jetzt - self._geprueft < PRUEFEN_S:
            return
        self._geprueft = jetzt
        dateien = {p.name.removesuffix(".auftrag.json"): p for p in self.ordner.glob("*.auftrag.json")} \
            if self.ordner.is_dir() else {}
        for key in list(self._auftraege):
            if key not in dateien:
                self._weg(key)
        for key, pfad in dateien.items():
            try:
                stempel = pfad.stat().st_mtime
            except OSError:
                continue
            if self._stempel.get(key) == stempel:
                continue
            self._stempel[key] = stempel
            a = _lesen(pfad)
            if not a.get("programm") or a.get("aus"):
                self._weg(key)
                continue
            neu = key not in self._auftraege
            self._auftraege[key] = a
            if neu:
                self._bilder[key] = _lesen(_datei(key, "bild", self.ordner)) or {"programm": key, "kinder": {}}
                self._seeden(key, jetzt)

    def _weg(self, key: str) -> None:
        self.speichern(nur=key)
        self._auftraege.pop(key, None)
        self._bilder.pop(key, None)
        self._pids = {pid: v for pid, v in self._pids.items() if v[0] != key}

    def _seeden(self, key: str, jetzt: float) -> None:
        """Läuft das Programm schon: es und alles, was davon abstammt, sofort mitzählen."""
        try:
            for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
                if (p.info.get("name") or "").lower() != key:
                    continue
                self._wurzel(key, p.info["pid"], float(p.info.get("create_time") or 0), p.info.get("exe") or "")
                for kind in p.children(recursive=True):
                    try:
                        eltern = psutil.Process(kind.ppid()).name()
                        self._pids[kind.pid] = (key, kind.create_time())
                        self._eintragen(key, kind.name(), kind.exe() or "", eltern, jetzt)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        except Exception:
            pass

    def _wurzel(self, key: str, pid: int, start: float, exe: str) -> None:
        self._pids[pid] = (key, start)
        b = self._bilder[key]
        if exe and exe.lower() not in [x.lower() for x in b.setdefault("pfade", [])]:
            b["pfade"].append(exe)
            self._geaendert.add(key)

    def _eintragen(self, key: str, name: str, exe: str, eltern: str, jetzt: float, quelle: str = "") -> bool:
        """Im Normalbild zählen oder (Lernphase, erlaubt, nach „harmlos“) neu aufnehmen. False = neu und fremd."""
        b, a = self._bilder[key], self._auftraege[key]
        kinder = b.setdefault("kinder", {})
        k = kind_schluessel(name, exe)
        eintrag = kinder.get(k)
        if eintrag is not None:
            eintrag["anzahl"] = int(eintrag.get("anzahl", 0)) + 1
            eintrag["zuletzt"] = jetzt
            self._geaendert.add(key)
            return True
        if not quelle:
            if lernt(a, jetzt):
                quelle = "gelernt"
            elif _erlaubt(a, k, name):
                quelle = "erlaubt"
            elif self._harmlos(key, name):
                quelle = "harmlos geurteilt"
        if not quelle or len(kinder) >= MAX_KINDER:
            return False
        kinder[k] = {"name": name, "exe": exe, "eltern": eltern, "anzahl": 1, "zuerst": jetzt, "zuletzt": jetzt,
                     "quelle": quelle}
        self._geaendert.add(key)
        return True

    def _harmlos(self, key: str, name: str) -> bool:
        if self.urteile is None:
            return False
        try:
            u = self.urteile(REGEL, subjekt(key, name)) or {}
        except Exception:
            return False
        return bool(u.get("harmlos")) and not u.get("echt")

    def verarbeiten(self, e: Ereignis) -> list[Alarm]:
        jetzt = e.zeit or time.time()
        self.auftraege_pruefen(jetzt)
        if not self._auftraege or e.kategorie != PROZESS:
            return []
        if e.aktion == "process_stop":
            self._pids.pop(e.pid, None)
            self._vielleicht_speichern(jetzt)
            return []
        if e.aktion != "process_start" or not e.pid:
            return []
        name = (e.prozess or "").lower()
        if name in self._auftraege:
            self._wurzel(name, e.pid, e.start, e.exe or "")
            self._bilder[name]["laeufe"] = int(self._bilder[name].get("laeufe", 0)) + 1
            return []
        eltern = self._pids.get(e.ppid)
        if eltern is None and (e.eltern or "").lower() in self._auftraege:
            # Programm und Kind im selben Schnappschuss: das Kind kam zuerst an
            eltern = (e.eltern.lower(), e.eltern_start)
            self._pids[e.ppid] = eltern
        if eltern is None:
            return []
        key, eltern_start = eltern
        if e.eltern_start and eltern_start and abs(e.eltern_start - eltern_start) > 1.0:
            return []                                       # dieselbe PID gehört inzwischen einem anderen
        if len(self._pids) < MAX_PIDS:
            self._pids[e.pid] = (key, e.start)
        bekannt = self._eintragen(key, e.prozess or "?", e.exe or "", e.eltern or "", jetzt)
        self._vielleicht_speichern(jetzt)
        return [] if bekannt else [self._alarm(key, e)]

    def _alarm(self, key: str, e: Ereignis) -> Alarm:
        a, b = self._auftraege[key], self._bilder[key]
        anzeige = a.get("anzeige") or key
        seit = time.strftime("%d.%m.%Y", time.localtime(float(a.get("seit") or 0) or time.time()))
        return Alarm(
            titel=f"Neuer Hintergrund-Prozess von {anzeige}: {e.prozess or '?'}",
            text=(f"{anzeige} ({key}) hat einen Prozess gestartet, der nicht in seinem Normalbild steht "
                  f"(beobachtet seit {seit}, {len(b.get('kinder') or {})} bekannte Hintergrund-Prozesse). "
                  f"Prozess: {e.prozess or '?'} · Pfad: {e.exe or 'nicht lesbar'} · gestartet von: "
                  f"{e.eltern or '?'}. Ist er in Ordnung, nimmt „harmlos“ ihn ins Normalbild auf."),
            schwere=Schwere.NIEDRIG, regel=REGEL, ereignis_id=e.id, quelle="regel", zeit=e.zeit,
            subjekt=subjekt(key, e.prozess or "?"))

    def _vielleicht_speichern(self, jetzt: float) -> None:
        if self._geaendert and jetzt - self._gespeichert >= SPEICHERN_S:
            self.speichern()

    def speichern(self, nur: str | None = None) -> None:
        for key in [nur] if nur else list(self._geaendert):
            if key in self._geaendert and key in self._bilder:
                b = self._bilder[key]
                b["programm"], b["aktualisiert"] = key, time.time()
                try:
                    _schreiben(_datei(key, "bild", self.ordner), b)
                except OSError:
                    continue
                self._geaendert.discard(key)
        self._gespeichert = time.time()

    def stand(self) -> dict:
        return {k: {"lernt": lernt(a), "kinder": len((self._bilder.get(k) or {}).get("kinder") or {}),
                    "laufend": sum(1 for v in self._pids.values() if v[0] == k)}
                for k, a in self._auftraege.items()}


def subjekt(key: str, name: str) -> str:
    return f"{key} → {(name or '?').lower()}"
