"""Detektor: Merkmale + Skalierer + Isolation Forest + Kaltstart + Perzentil + Feedback.

Der Score ist ein PERZENTIL gegen die gelernte Baseline: 0,98 heißt „auffälliger
als 98 % aller Ereignisse, die das Modell als normal kennt“. Der rohe Wald-Wert
spannt nur wenige Hundertstel und wäre als Schwelle ein Ratespiel; das Perzentil
macht die Schwelle direkt zur Stellschraube für die Alarmrate.

`side="right"` beim Einordnen ist kein Detail: identische Ereignisse erzeugen
identische Rohwerte. Mit "left" bekäme so eine Gruppe den Rang ihres ersten
Elements – ein Muster, das 20 % der Baseline ausmacht, gälte als maximal
auffällig (im SIEM gemessen: 24 % Fehlalarme).

Zwei Instanzen laufen nebeneinander: Prozesse/Netz und Dateien.

Jedes Training legt das vorige Modell als Vorversion ab (`prozesse.v1.json` … `v3`).
`zurueckdrehen` holt die vorige zurück und setzt eine Pause-Marke: Bis der Nutzer selbst
trainiert, lernt das Modell nicht automatisch nach – sonst wäre das Zurückdrehen beim
nächsten Takt wieder weg.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import TRAINING_LOG
from .ereignisse import Ereignis
from .wald import IsolationForest, Skalierer

VERSIONEN = 3                  # so viele Vorversionen bleiben
AUSREISSER = 0.995             # ab hier dämpft kein „harmlos“-Urteil mehr
DAEMPFUNG = 0.35


def _log(text: str) -> None:
    try:
        TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {text}\n")
    except OSError:
        pass


def _version(pfad: Path, n: int) -> Path:
    return pfad.with_name(f"{pfad.stem}.v{n}{pfad.suffix}")


def pause_marke(pfad: Path) -> Path:
    return Path(pfad).with_suffix(".pause")


def zurueckdrehen(pfad: Path) -> str:
    """Vorige Version wird aktiv (die aktuelle fällt weg), danach lernt das Modell erst nach einem
    Training durch den Nutzer wieder automatisch. Gibt den Text für den Nutzer zurück."""
    pfad = Path(pfad)
    if not _version(pfad, 1).exists():
        return f"{pfad.stem}: keine ältere Version vorhanden."
    os.replace(_version(pfad, 1), pfad)
    for n in range(2, VERSIONEN + 1):
        if _version(pfad, n).exists():
            os.replace(_version(pfad, n), _version(pfad, n - 1))
    pause_marke(pfad).write_text(str(time.time()), encoding="utf-8")
    _log(f"{pfad.stem}: auf die vorige Version zurückgedreht, automatisches Lernen pausiert")
    return f"{pfad.stem}: vorige Version aktiv – lernt erst nach deinem nächsten Training (Tray „Modell jetzt trainieren“ oder /wache training) wieder von selbst."


# Messwerte (nicht ja/nein), die ein Mensch versteht: weit über dem Gelernten -> eigener Grund am Alarm.
MESSWERTE = {"cpu": "CPU-Last", "mem": "Speicherverbrauch", "cmd_laenge": "Länge der Kommandozeile",
             "cmd_entropie": "Zufälligkeit der Kommandozeile", "name_entropie": "Zufälligkeit des Namens",
             "pfad_tiefe": "Tiefe des Pfads", "burst": "Ereignisse pro Minute", "name_laenge": "Länge des Dateinamens",
             "ziffern_anteil": "Ziffern im Namen"}
MESSWERT_AB = 3.0              # so viele Standardabweichungen über dem Mittel des Trainings


def messwert_gruende(namen: list[str], v: list[float], sk) -> list[str]:
    """„weit über dem Gewohnten: CPU-Last, Länge der Kommandozeile“ – höchstens drei, stärkste zuerst."""
    if sk is None or sk.mittel is None:
        return []
    z = (np.asarray(v, dtype=np.float64) - sk.mittel) / sk.streuung
    treffer = sorted(((z[i], MESSWERTE[n]) for i, n in enumerate(namen)
                      if n in MESSWERTE and z[i] >= MESSWERT_AB
                      and not (sk.konstant is not None and sk.konstant[i])), reverse=True)
    return ["weit über dem Gewohnten: " + ", ".join(t for _z, t in treffer[:3])] if treffer else []


class Detektor:
    def __init__(self, pfad: Path, merkmale, *, schwelle: float = 0.97,
                 kontamination: float = 0.02, label: str = "Prozesse & Netz"):
        self.pfad = Path(pfad)
        self.merkmale = merkmale                      # ProzessMerkmale oder DateiMerkmale
        self.schwelle = schwelle
        self.kontamination = kontamination
        self.label = label
        self._lock = threading.RLock()
        self._wald: IsolationForest | None = None
        self._skalierer: Skalierer | None = None
        self._referenz: np.ndarray | None = None     # sortierte Rohwerte des Trainings
        self.trainiert = False
        self.trainings = 0
        self.stichprobe = 0
        self.zuletzt = 0.0
        self.dauer = 0.0
        self.bewertet = 0
        self.anomalien = 0
        self.gedaempft: set[str] = set()              # Programmpfade (klein) mit „harmlos“-Urteil
        self.fehler = ""                              # letzter Speicher-/Ladefehler, für stand()
        self._geladen_mtime = 0.0
        self.laden()

    @property
    def pausiert(self) -> bool:
        """Nach einem Zurückdrehen: kein automatisches Nachtrainieren."""
        return pause_marke(self.pfad).exists()

    def pause_aufheben(self) -> None:
        pause_marke(self.pfad).unlink(missing_ok=True)

    def _fehler(self, text: str) -> None:
        self.fehler = text
        _log(f"Modell {self.label}: {text}")

    # -------------------------------------------------------------- Training

    def trainieren(self, ereignisse: list[Ereignis]) -> dict:
        if len(ereignisse) < 20:
            return {"ok": False, "grund": f"zu wenige Ereignisse ({len(ereignisse)})"}
        start = time.perf_counter()
        if hasattr(self.merkmale, "anpassen"):
            self.merkmale.anpassen(ereignisse)
        X = np.array([self.merkmale.vektor(e) for e in ereignisse], dtype=np.float64)
        sk = Skalierer().anpassen(X)
        Xs = sk.anwenden(X)
        wald = IsolationForest(baeume=100, stichprobe=256, zufall=42).anpassen(Xs)
        referenz = np.sort(wald.rohwert(Xs))
        with self._lock:
            self._wald, self._skalierer, self._referenz = wald, sk, referenz
            self.trainiert = True
            self.stichprobe = len(ereignisse)
            self.trainings += 1
            self.zuletzt = time.time()
            self.dauer = time.perf_counter() - start
        self.speichern(neue_version=True)
        return {"ok": True, "ereignisse": len(ereignisse), "merkmale": len(self.merkmale.namen),
                "label": self.label, "sekunden": round(self.dauer, 2), "laeufe": self.trainings}

    # -------------------------------------------------------------- Bewerten

    def bewerten(self, e: Ereignis) -> tuple[float, list[str]]:
        v = self.merkmale.vektor(e)
        with self._lock:
            wald, sk, ref = self._wald, self._skalierer, self._referenz
        if wald is None or sk is None or ref is None or not len(ref):
            return self._kaltstart(v), self.merkmale.erklaeren(v)
        try:
            roh = float(wald.rohwert(sk.anwenden(np.array([v], dtype=np.float64)))[0])
        except Exception:
            return self._kaltstart(v), self.merkmale.erklaeren(v)
        rang = float(np.searchsorted(ref, roh, side="right")) / len(ref)
        score = float(np.clip(1.0 - rang, 0.0, 1.0))
        gruende = self.merkmale.erklaeren(v)
        # Achsen, die im Training nie variierten, kann der Wald nicht sehen –
        # weicht ein Ereignis genau dort ab, hat das Modell so etwas noch nie
        # gesehen. Jede solche Achse hebt den Score: 1 → 0,40 · 3 → 0,78 · 5 → 0,92.
        gruende += messwert_gruende(self.merkmale.namen, v, sk)
        unbekannt = sk.unbekannte_achsen(np.array(v, dtype=np.float64))
        if len(unbekannt):
            neuheit = 1.0 - 0.6 ** len(unbekannt)
            if neuheit > score:
                score = neuheit
                namen = [self.merkmale.namen[i] for i in unbekannt[:4]]
                gruende = [f"im Training nie gesehen: {', '.join(namen)}"] + gruende
        self.bewertet += 1
        # Gedämpft wird nur dasselbe Programm (Pfad) und nur unterhalb der Ausreißer-Grenze:
        # nie Gesehenes und extreme Abweichungen melden auch nach einem „harmlos“.
        if (e.exe and e.exe.lower() in self.gedaempft and score < AUSREISSER
                and not len(unbekannt)):
            score *= DAEMPFUNG
        return score, gruende

    def ist_anomalie(self, score: float) -> bool:
        return score >= self.schwelle

    def _kaltstart(self, v: list[float]) -> float:
        idx = {n: i for i, n in enumerate(self.merkmale.namen)}
        s = sum(v[idx[k]] * w for k, w in self.merkmale.kaltstart.items() if k in idx)
        return float(min(s, 1.0))

    def feedback(self, pfade: set[str]) -> None:
        """Programmpfade mit „harmlos“-Urteil (Speicher.fehlalarm_pfade)."""
        self.gedaempft = {p.lower() for p in pfade if p}

    # ------------------------------------------------------------ Persistenz

    def speichern(self, neue_version: bool = False) -> None:
        """`neue_version`: nach einem Training – das bisherige Modell wird Vorversion."""
        try:
            with self._lock:
                z = {"wald": self._wald.zustand() if self._wald else None,
                     "skalierer": self._skalierer.zustand() if self._skalierer else None,
                     "referenz": self._referenz.tolist() if self._referenz is not None else None,
                     "merkmale": list(self.merkmale.namen),
                     "merkmale_zustand": self.merkmale.zustand() if hasattr(self.merkmale, "zustand") else None,
                     "stichprobe": self.stichprobe, "trainings": self.trainings,
                     "zuletzt": self.zuletzt, "kontamination": self.kontamination}
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.pfad.with_suffix(".tmp")
            tmp.write_text(json.dumps(z), encoding="utf-8")
            if neue_version and self.pfad.exists():
                for n in range(VERSIONEN, 1, -1):
                    if _version(self.pfad, n - 1).exists():
                        os.replace(_version(self.pfad, n - 1), _version(self.pfad, n))
                os.replace(self.pfad, _version(self.pfad, 1))
            tmp.replace(self.pfad)
            self._geladen_mtime = self.pfad.stat().st_mtime
            self.fehler = ""
        except Exception as exc:
            self._fehler(f"Speichern fehlgeschlagen: {type(exc).__name__}: {exc}")

    def neu_laden_wenn_geaendert(self) -> bool:
        """Hat ein anderer Prozess die Modelldatei getauscht (/wache ml zurueck)? Dann neu laden."""
        try:
            mtime = self.pfad.stat().st_mtime
        except OSError:
            return False
        if mtime != self._geladen_mtime:
            return self.laden()
        return False

    def laden(self) -> bool:
        if not self.pfad.exists():
            return False
        try:
            self._geladen_mtime = self.pfad.stat().st_mtime
            z = json.loads(self.pfad.read_text(encoding="utf-8"))
            if z.get("merkmale") != list(self.merkmale.namen):
                self._fehler("gespeichertes Modell passt nicht zu den Merkmalen – lernt neu")
                return False
            if z.get("merkmale_zustand") and hasattr(self.merkmale, "zustand_laden"):
                self.merkmale.zustand_laden(z["merkmale_zustand"])
            with self._lock:
                self._wald = IsolationForest.aus_zustand(z["wald"]) if z.get("wald") else None
                self._skalierer = Skalierer.aus_zustand(z["skalierer"]) if z.get("skalierer") else None
                self._referenz = np.asarray(z["referenz"], dtype=np.float64) if z.get("referenz") else None
                self.stichprobe = int(z.get("stichprobe", 0))
                self.trainings = int(z.get("trainings", 0))
                self.zuletzt = float(z.get("zuletzt", 0.0))
                self.trainiert = (self._wald is not None and self._referenz is not None
                                  and len(self._referenz) > 0)
            return self.trainiert
        except Exception as exc:
            self._fehler(f"Laden fehlgeschlagen ({type(exc).__name__}: {exc}) – Kaltstart")
            return False

    def zuruecksetzen(self) -> None:
        with self._lock:
            self._wald = self._skalierer = self._referenz = None
            self.trainiert = False
            self.stichprobe = self.trainings = 0
            self.zuletzt = 0.0
            self.bewertet = self.anomalien = 0
            if hasattr(self.merkmale, "zustand_laden"):
                self.merkmale.zustand_laden({})
        for p in [self.pfad, pause_marke(self.pfad)] + [_version(self.pfad, n) for n in range(1, VERSIONEN + 1)]:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def stand(self) -> dict:
        return {"label": self.label, "trainiert": self.trainiert, "ereignisse": self.stichprobe,
                "laeufe": self.trainings, "zuletzt": self.zuletzt, "sekunden": round(self.dauer, 2),
                "bewertet": self.bewertet, "anomalien": self.anomalien, "schwelle": self.schwelle,
                "merkmale": len(self.merkmale.namen), "gedaempft": len(self.gedaempft),
                "pausiert": self.pausiert, "fehler": self.fehler,
                "versionen": sum(_version(self.pfad, n).exists() for n in range(1, VERSIONEN + 1))}
