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
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np

from .ereignisse import Ereignis
from .wald import IsolationForest, Skalierer


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
        self.gedaempft: set[str] = set()              # Prozesse mit „harmlos“-Urteil
        self.laden()

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
        self.speichern()
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
        unbekannt = sk.unbekannte_achsen(np.array(v, dtype=np.float64))
        if len(unbekannt):
            neuheit = 1.0 - 0.6 ** len(unbekannt)
            if neuheit > score:
                score = neuheit
                namen = [self.merkmale.namen[i] for i in unbekannt[:4]]
                gruende = [f"im Training nie gesehen: {', '.join(namen)}"] + gruende
        self.bewertet += 1
        if e.prozess and e.prozess in self.gedaempft:
            score *= 0.35                             # gedämpft, nicht null: echte Ausreißer kommen durch
        return score, gruende

    def ist_anomalie(self, score: float) -> bool:
        return score >= self.schwelle

    def _kaltstart(self, v: list[float]) -> float:
        idx = {n: i for i, n in enumerate(self.merkmale.namen)}
        s = sum(v[idx[k]] * w for k, w in self.merkmale.kaltstart.items() if k in idx)
        return float(min(s, 1.0))

    def feedback(self, prozesse: set[str]) -> None:
        self.gedaempft = set(prozesse)

    # ------------------------------------------------------------ Persistenz

    def speichern(self) -> None:
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
            tmp.replace(self.pfad)
        except Exception:
            pass

    def laden(self) -> bool:
        if not self.pfad.exists():
            return False
        try:
            z = json.loads(self.pfad.read_text(encoding="utf-8"))
            if z.get("merkmale") != list(self.merkmale.namen):
                return False                          # Merkmalsliste passt nicht → neu lernen
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
        except Exception:
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
        try:
            self.pfad.unlink(missing_ok=True)
        except OSError:
            pass

    def stand(self) -> dict:
        return {"label": self.label, "trainiert": self.trainiert, "ereignisse": self.stichprobe,
                "laeufe": self.trainings, "zuletzt": self.zuletzt, "sekunden": round(self.dauer, 2),
                "bewertet": self.bewertet, "anomalien": self.anomalien, "schwelle": self.schwelle,
                "merkmale": len(self.merkmale.namen), "gedaempft": len(self.gedaempft)}
