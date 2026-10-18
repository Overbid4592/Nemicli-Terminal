"""Folgen-Netz: ungewöhnliche Abläufe in Prozess-Ketten erkennen (GRU, numpy).

Regeln und Isolation Forest bewerten jedes Ereignis für sich. Ein Angriff
besteht aber oft aus Schritten, die einzeln normal aussehen: ein Office-Programm
startet eine Shell, die Shell baut eine Verbindung auf. Das Folgen-Netz lernt,
welcher Schritt in einer Prozess-Kette üblicherweise auf welchen folgt, und
meldet Schritte, die es für sehr unwahrscheinlich hält.

Kette: genau ein Prozess, erkannt an PID + Startzeit (Windows vergibt PIDs neu).
Ihre Zeichen:

  ^  E:<Elternprozess>  S:<Prozess>       Kopf, wenn der Start beobachtet wurde
  ^?  S:<Prozess>                          Kopf, wenn der Prozess schon lief
  K:<Kind>                                 startet ein Kind (auch Kopf der Kind-Kette)
  N:<Portklasse>:<e|i>                     Verbindung nach extern / intern
  L:<Portklasse>  C  M                     Listener, CPU-Sprung, Speicher-Sprung

Gleiche Zeichen direkt hintereinander zählen einmal (ein Browser mit 300
Verbindungen auf 443 ist EIN Schritt). Nach FENSTER Zeichen beginnt die Kette
mit dem Kopf „^+ S:<Prozess>“ neu. Datei- und Inventar-Ereignisse haben keinen
Prozess und gehören zu keiner Kette.

Der Score ist wie beim Isolation Forest ein Perzentil: 0,995 heißt „überraschender
als 99,5 % der Schritte, die das Netz beim Prüfen auf zurückgehaltenen Folgen sah“.

Lernen und Erkennen sind getrennt: Das Netz bewertet mit festen Gewichten. Neue
Gewichte entstehen nur beim Training, und das läuft auf Ereignissen, die älter
als die Karenzzeit sind und mit keinem offenen, unklaren oder echten Alarm
zusammenhängen (Speicher.lern_ereignisse). Jedes Training ist eine eigene
Version; die letzten drei bleiben, `zurueckdrehen` wechselt auf die vorige.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .ereignisse import NETZ, PROZESS, Ereignis
from .gru import GRU

KOPF, KOPF_LIEF, KOPF_WEITER = "^", "^?", "^+"
FENSTER = 64
MAX_KETTEN = 5000
MIN_FOLGEN = 50
VERSIONEN = 3
SPEZIAL = ("<leer>", KOPF, KOPF_LIEF, KOPF_WEITER, "E:~", "S:~", "K:~", "?")
_PORTS_C2 = (4444, 6667, 9001, 9050, 23)
_PORTS_DIENST = (22, 445, 1433, 3306, 3389, 5432, 5900)


def portklasse(port: int) -> str:
    port = int(port or 0)
    if port in (443, 80, 53):
        return str(port)
    if port in _PORTS_C2:
        return "c2"
    if port in _PORTS_DIENST:
        return "dienst"
    if not port:
        return "0"
    return "hoch" if port >= 1024 else "tief"


def _name(n: str) -> str:
    return (n or "").strip().lower() or "?"


def zeichen_fuer(e: Ereignis) -> str | None:
    """Zeichen eines Ereignisses im Rumpf seiner Kette (Kopf und Kindstart regelt das Kettenbuch)."""
    if e.kategorie == NETZ and e.aktion == "conn_open":
        extern = bool((e.extra or {}).get("extern"))
        return f"N:{portklasse(e.zielport)}:{'e' if extern else 'i'}"
    if e.kategorie == NETZ and e.aktion == "listen_open":
        try:
            port = int(str(e.lokal).rsplit(":", 1)[-1])
        except ValueError:
            port = 0
        return f"L:{portklasse(port)}"
    if e.kategorie == PROZESS and e.aktion == "cpu_spike":
        return "C"
    if e.kategorie == PROZESS and e.aktion == "memory_spike":
        return "M"
    return None


def schluessel(pid: int, start: float) -> tuple[int, float] | None:
    if not pid or not start:
        return None
    return int(pid), round(float(start), 3)


@dataclass
class Schritt:
    kette: tuple[int, float]
    zeichen: list[str] = field(default_factory=list)
    bewerten: list[bool] = field(default_factory=list)
    neu: bool = False            # Zustand vorher auf null setzen
    ende: bool = False           # Kette ist zu Ende (Prozess beendet oder verdrängt)


class Kettenbuch:
    """Ordnet Ereignisse ihren Ketten zu und liefert die Zeichen, die einzuspeisen sind.
    Training und Bewertung benutzen dieselbe Klasse – damit sehen beide dieselben Folgen."""

    def __init__(self, fenster: int = FENSTER, max_ketten: int = MAX_KETTEN):
        self.fenster = fenster
        self.max_ketten = max_ketten
        self._k: OrderedDict[tuple, dict] = OrderedDict()

    def __len__(self) -> int:
        return len(self._k)

    def name(self, kette: tuple) -> str:
        k = self._k.get(kette)
        return k["name"] if k else ""

    def _neu(self, kette: tuple, name: str, kopf: list[str], bewerten: list[bool]) -> list[Schritt]:
        self._k[kette] = {"name": name, "letztes": kopf[-1], "laenge": len(kopf)}
        self._k.move_to_end(kette)
        out = [Schritt(kette, list(kopf), list(bewerten), neu=True)]
        while len(self._k) > self.max_ketten:
            alt, _ = self._k.popitem(last=False)
            out.append(Schritt(alt, ende=True))
        return out

    def _anhaengen(self, kette: tuple, zeichen: str) -> list[Schritt]:
        k = self._k[kette]
        self._k.move_to_end(kette)
        if zeichen == k["letztes"]:
            return []
        if k["laenge"] + 1 > self.fenster:
            k["laenge"], k["letztes"] = 3, zeichen
            return [Schritt(kette, [KOPF_WEITER, "S:" + k["name"], zeichen], [False, False, True], neu=True)]
        k["laenge"] += 1
        k["letztes"] = zeichen
        return [Schritt(kette, [zeichen], [True])]

    def schritte(self, e: Ereignis) -> list[Schritt]:
        kette = schluessel(e.pid, e.start)
        if kette is None:
            return []
        name = _name(e.prozess)
        if e.kategorie == PROZESS and e.aktion == "process_start":
            out: list[Schritt] = []
            eltern = schluessel(e.ppid, e.eltern_start)
            if eltern is not None and eltern in self._k:
                out += self._anhaengen(eltern, "K:" + name)
            out += self._neu(kette, name, [KOPF, "E:" + _name(e.eltern), "S:" + name], [False, False, True])
            return out
        if e.kategorie == PROZESS and e.aktion == "process_stop":
            if self._k.pop(kette, None) is not None:
                return [Schritt(kette, ende=True)]
            return []
        zeichen = zeichen_fuer(e)
        if zeichen is None:
            return []
        out = []
        if kette not in self._k:
            out += self._neu(kette, name, [KOPF_LIEF, "S:" + name], [False, False])
        return out + self._anhaengen(kette, zeichen)


def folgen_bauen(ereignisse: list[Ereignis], fenster: int = FENSTER) -> list[tuple[list[str], list[bool]]]:
    """Ereignisse → Folgen (Zeichen, zu bewerten?) in der Form, die die Bewertung später sieht."""
    buch = Kettenbuch(fenster)
    offen: dict[tuple, tuple[list[str], list[bool]]] = {}
    fertig: list[tuple[list[str], list[bool]]] = []
    for e in sorted(ereignisse, key=lambda x: x.zeit):
        for s in buch.schritte(e):
            if s.ende or s.neu:
                alt = offen.pop(s.kette, None)
                if alt is not None:
                    fertig.append(alt)
                if s.ende:
                    continue
                offen[s.kette] = ([], [])
            folge = offen.setdefault(s.kette, ([], []))
            folge[0].extend(s.zeichen)
            folge[1].extend(s.bewerten)
    fertig.extend(offen.values())
    return [f for f in fertig if len(f[0]) >= 2 and any(f[1][1:])]


class Vokabular:
    """Zeichen → Zahl. Seltene Programmnamen (unter `min_anzahl`) teilen sich je Art ein
    Sammelzeichen (E:~, S:~, K:~) – ein Name, den das Netz nie sah, ist „ein seltenes Programm“,
    nicht automatisch ein Alarm. Unbekannte Zeichen anderer Art werden zu „?“."""

    def __init__(self, zeichen: list[str]):
        self.zeichen = list(zeichen)
        self._id = {z: i for i, z in enumerate(self.zeichen)}

    def __len__(self) -> int:
        return len(self.zeichen)

    @classmethod
    def bauen(cls, folgen, min_anzahl: int = 3, max_groesse: int = 800) -> "Vokabular":
        anzahl: dict[str, int] = {}
        for zeichen, _ in folgen:
            for z in zeichen:
                anzahl[z] = anzahl.get(z, 0) + 1
        namen = ("E:", "S:", "K:")
        kandidaten = [z for z, n in anzahl.items() if z not in SPEZIAL
                      and (n >= min_anzahl or not z.startswith(namen))]
        kandidaten.sort(key=lambda z: (-anzahl[z], z))
        return cls(list(SPEZIAL) + kandidaten[: max_groesse - len(SPEZIAL)])

    def id(self, z: str) -> int:
        i = self._id.get(z)
        if i is not None:
            return i
        if z[:2] in ("E:", "S:", "K:"):
            return self._id[z[:2] + "~"]
        return self._id["?"]

    def ids(self, zeichen: list[str]) -> list[int]:
        return [self.id(z) for z in zeichen]


def _ueberraschungen(netz: GRU, folgen: list[tuple[list[int], list[bool]]]) -> list[float]:
    """−log p für jedes zu bewertende Zeichen (ab dem zweiten)."""
    out: list[float] = []
    for ids, bewerten in folgen:
        h = netz.anfang()
        for i, z in enumerate(ids):
            if i and bewerten[i]:
                p = float(netz.verteilung(h)[0, z])
                out.append(-math.log(max(p, 1e-12)))
            h = netz.schritt(h, z)
    return out


# ---------------------------------------------------------------- Versionen

def versionen(ordner: Path) -> list[Path]:
    return sorted(Path(ordner).glob("v*.json"))


def zeiger(ordner: Path) -> str:
    try:
        return (Path(ordner) / "aktuell.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def zeiger_setzen(ordner: Path, name: str) -> None:
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    tmp = ordner / "aktuell.tmp"
    tmp.write_text(name, encoding="utf-8")
    tmp.replace(ordner / "aktuell.txt")


def pause_marke(ordner: Path) -> Path:
    return Path(ordner) / "auto.pause"


def pausiert(ordner: Path) -> bool:
    """Nach einem Zurückdrehen: kein automatisches Training, bis der Nutzer selbst trainiert."""
    return pause_marke(ordner).exists()


def zurueckdrehen(ordner: Path) -> str:
    """Auf die vorige Version wechseln und das automatische Training pausieren – sonst ersetzte
    es das Zurückdrehen beim nächsten Takt. Gibt den Text für den Nutzer zurück."""
    alle = [p.stem for p in versionen(ordner)]
    jetzt = zeiger(ordner) or (alle[-1] if alle else "")
    if jetzt not in alle or alle.index(jetzt) == 0:
        return "Keine ältere Version vorhanden."
    vorige = alle[alle.index(jetzt) - 1]
    zeiger_setzen(ordner, vorige)
    pause_marke(ordner).write_text(str(time.time()), encoding="utf-8")
    return (f"Folgen-Netz: von {jetzt} auf {vorige} zurückgedreht – lernt erst nach "
            "deinem nächsten Training (Tray „Modell jetzt trainieren“ oder /wache training) wieder von selbst.")


def info(ordner: Path) -> dict:
    """Kopfdaten der aktiven Version, ohne die Gewichte zu laden."""
    alle = versionen(ordner)
    name = zeiger(ordner) or (alle[-1].stem if alle else "")
    pfad = Path(ordner) / f"{name}.json"
    if not name or not pfad.exists():
        return {"trainiert": False, "versionen": [p.stem for p in alle]}
    try:
        kopf = json.loads(pfad.read_text(encoding="utf-8")).get("kopf", {})
    except (OSError, ValueError):
        kopf = {}
    return {"trainiert": True, "version": name, "versionen": [p.stem for p in alle], **kopf}


# ---------------------------------------------------------------- Schatten-Protokoll

def schatten_pfad(ordner: Path) -> Path:
    return Path(ordner).parent / "folgen_schatten.log"


def schatten_lesen(ordner: Path, anzahl: int = 20) -> list[str]:
    try:
        zeilen = schatten_pfad(ordner).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return zeilen[-anzahl:]


def schatten_zaehlen(ordner: Path, seit: float) -> int:
    grenze = datetime.fromtimestamp(seit).strftime("%Y-%m-%d %H:%M:%S")
    try:
        zeilen = schatten_pfad(ordner).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    return sum(1 for z in zeilen if z[:19] >= grenze)


# ---------------------------------------------------------------- Detektor

class FolgenDetektor:
    def __init__(self, ordner: Path, *, schwelle: float = 0.995, fenster: int = FENSTER):
        self.ordner = Path(ordner)
        self.schwelle = schwelle
        self.fenster = fenster
        self._lock = threading.RLock()
        self.netz: GRU | None = None
        self.vokabular: Vokabular | None = None
        self.referenz: np.ndarray | None = None
        self.kettenbuch = Kettenbuch(fenster)
        self._h: dict[tuple, np.ndarray] = {}
        self.version = ""
        self.kopf: dict = {}
        self.bewertet = 0
        self.auffaellig = 0
        self._schatten_zuletzt: dict[tuple[str, str], float] = {}
        self.laden()

    @property
    def trainiert(self) -> bool:
        return self.netz is not None

    @property
    def zuletzt(self) -> float:
        return float(self.kopf.get("zuletzt", 0.0) or 0.0)

    # -------------------------------------------------------------- Bewerten

    def _perzentil(self, u: float) -> float:
        ref = self.referenz
        if ref is None or not len(ref):
            return 0.0
        return float(np.searchsorted(ref, u, side="left")) / len(ref)

    def bewerten(self, e: Ereignis) -> tuple[float, str]:
        """(Score, Beschreibung des überraschendsten Schritts). Score 0 = nichts bewertet."""
        with self._lock:
            schritte = self.kettenbuch.schritte(e)
            netz, vok = self.netz, self.vokabular
            if netz is None or vok is None:
                return 0.0, ""
            best, beschreibung = 0.0, ""
            for s in schritte:
                if s.ende:
                    self._h.pop(s.kette, None)
                    continue
                if s.neu:
                    h, gefuettert = netz.anfang(), False
                elif s.kette in self._h:
                    h, gefuettert = self._h[s.kette], True
                else:
                    continue                       # Kette aus der Zeit vor dem letzten Modellwechsel
                for z, bewerten in zip(s.zeichen, s.bewerten):
                    i = vok.id(z)
                    if bewerten and gefuettert:
                        p = float(netz.verteilung(h)[0, i])
                        score = self._perzentil(-math.log(max(p, 1e-12)))
                        if score > best:
                            name = self.kettenbuch.name(s.kette) or _name(e.prozess)
                            best, beschreibung = score, f"{name}: {z} (p={p:.4f})"
                    h = netz.schritt(h, i)
                    gefuettert = True
                self._h[s.kette] = h
            if best:
                self.bewertet += 1
            return best, beschreibung

    def ist_auffaellig(self, score: float) -> bool:
        return score >= self.schwelle

    def schatten_melden(self, e: Ereignis, score: float, beschreibung: str) -> None:
        """Schattenmodus: nur ins Protokoll – kein Alarm, kein Weckruf. Derselbe Schritt
        desselben Programms steht höchstens einmal pro Stunde drin."""
        key = (_name(e.prozess), beschreibung.split(" (p=")[0])
        jetzt = time.time()
        if jetzt - self._schatten_zuletzt.get(key, 0.0) < 3600:
            return
        self._schatten_zuletzt[key] = jetzt
        if len(self._schatten_zuletzt) > 2000:
            self._schatten_zuletzt.clear()
        pfad = schatten_pfad(self.ordner)
        try:
            pfad.parent.mkdir(parents=True, exist_ok=True)
            if pfad.exists() and pfad.stat().st_size > 2_000_000:
                pfad.replace(pfad.with_suffix(".log.1"))
            with open(pfad, "a", encoding="utf-8") as f:
                f.write(f"{datetime.fromtimestamp(e.zeit).strftime('%Y-%m-%d %H:%M:%S')} | "
                        f"{score:.4f} | {beschreibung} | PID {e.pid} | {e.text}\n")
        except OSError:
            pass

    # -------------------------------------------------------------- Lernen

    def trainieren(self, ereignisse: list[Ereignis], *, epochen: int = 6, zeitlimit: float = 120.0,
                   abbruch=None) -> dict:
        start = time.perf_counter()
        folgen = folgen_bauen(ereignisse, self.fenster)
        if len(folgen) < MIN_FOLGEN:
            return {"ok": False, "grund": f"nur {len(folgen)} Prozess-Ketten, {MIN_FOLGEN} nötig"}
        folgen = folgen[-6000:]
        vok = Vokabular.bauen(folgen)
        daten = [(vok.ids(z), b) for z, b in folgen]
        rng = np.random.default_rng(42)
        reihe = rng.permutation(len(daten))
        n_pruef = max(10, len(daten) // 5)
        pruef = [daten[i] for i in reihe[:n_pruef]]
        lern = [daten[i] for i in reihe[n_pruef:]]
        netz = GRU(len(vok))
        verlauf = netz.trainieren([d[0] for d in lern], epochen=epochen, zeitlimit=zeitlimit,
                                  abbruch=abbruch)
        if not verlauf or (abbruch is not None and abbruch()):
            return {"ok": False, "grund": "abgebrochen"}
        ref = _ueberraschungen(netz, pruef)
        if len(ref) < 200:
            ref += _ueberraschungen(netz, lern)
        referenz = np.sort(np.asarray(ref, dtype=np.float64))
        sekunden = round(time.perf_counter() - start, 1)
        alle = versionen(self.ordner)
        nummer = int(alle[-1].stem[1:]) + 1 if alle else 1
        kopf = {"zuletzt": time.time(), "folgen": len(folgen), "ereignisse": len(ereignisse),
                "vokabular": len(vok), "verlust": round(verlauf[-1], 4), "epochen": len(verlauf),
                "sekunden": sekunden, "referenz": len(referenz)}
        name = f"v{nummer:04d}"
        self._speichern(name, netz, vok, referenz, kopf)
        with self._lock:
            self._uebernehmen(name, netz, vok, referenz, kopf)
        return {"ok": True, "version": name, **kopf}

    def _uebernehmen(self, name, netz, vok, referenz, kopf) -> None:
        self.netz, self.vokabular, self.referenz = netz, vok, referenz
        self.version, self.kopf = name, dict(kopf)
        self.kettenbuch = Kettenbuch(self.fenster)     # Zustände gehören zum alten Netz
        self._h = {}

    # -------------------------------------------------------------- Speichern

    def _speichern(self, name: str, netz: GRU, vok: Vokabular, referenz: np.ndarray, kopf: dict) -> None:
        self.ordner.mkdir(parents=True, exist_ok=True)
        z = {"kopf": kopf, "netz": netz.zustand(), "vokabular": vok.zeichen,
             "referenz": referenz.tolist(), "fenster": self.fenster}
        tmp = self.ordner / f"{name}.tmp"
        tmp.write_text(json.dumps(z), encoding="utf-8")
        tmp.replace(self.ordner / f"{name}.json")
        zeiger_setzen(self.ordner, name)
        for alt in versionen(self.ordner)[:-VERSIONEN]:
            try:
                alt.unlink()
            except OSError:
                pass

    def laden(self) -> bool:
        alle = versionen(self.ordner)
        name = zeiger(self.ordner) or (alle[-1].stem if alle else "")
        pfad = self.ordner / f"{name}.json"
        if not name or not pfad.exists():
            return False
        try:
            z = json.loads(pfad.read_text(encoding="utf-8"))
            netz = GRU.aus_zustand(z["netz"])
            vok = Vokabular(z["vokabular"])
            if len(vok) != netz.V:
                return False
            referenz = np.asarray(z["referenz"], dtype=np.float64)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        with self._lock:
            self._uebernehmen(name, netz, vok, referenz, z.get("kopf", {}))
        return True

    def zeiger_pruefen(self) -> bool:
        """Hat jemand die Version umgestellt (/wache dl zurueck aus dem Hauptprogramm)?"""
        name = zeiger(self.ordner)
        if name and name != self.version:
            return self.laden()
        return False

    def zuruecksetzen(self) -> None:
        with self._lock:
            self.netz = self.vokabular = self.referenz = None
            self.version, self.kopf = "", {}
            self.kettenbuch = Kettenbuch(self.fenster)
            self._h = {}
            self.bewertet = self.auffaellig = 0
        for p in list(versionen(self.ordner)) + [self.ordner / "aktuell.txt", pause_marke(self.ordner)]:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def stand(self) -> dict:
        return {"trainiert": self.trainiert, "version": self.version, "schwelle": self.schwelle,
                "bewertet": self.bewertet, "auffaellig": self.auffaellig, "pausiert": pausiert(self.ordner),
                "ketten": len(self.kettenbuch), **{k: self.kopf.get(k) for k in
                                                   ("zuletzt", "folgen", "vokabular", "verlust", "sekunden")}}
