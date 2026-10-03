"""Isolation Forest auf numpy – kein scikit-learn.

Warum selbst gebaut: scikit-learn bringt eine unsignierte DLL mit, die Windows
Smart App Control auf diesem PC blockiert (siehe tools/embedder.py). Der
Algorithmus (Liu, Ting, Zhou 2008) ist klein genug, um ihn hier hinzuschreiben:

  Training:  T Bäume, jeder auf einer Zufallsstichprobe von ψ Punkten. Ein
             Baum teilt rekursiv an einer zufälligen Achse bei einem zufälligen
             Wert zwischen min und max, bis ein Punkt allein ist oder die
             Höhengrenze ceil(log2 ψ) erreicht ist.
  Bewertung: Pfadlänge h(x) = Tiefe des Blattes + c(Blattgröße) (Korrektur für
             nicht weiter geteilte Blätter). s(x) = 2^(−E[h(x)] / c(ψ)).
             Anomalien haben kurze Pfade → s nahe 1; Normales s um 0,5 oder
             darunter.

Die Bäume liegen als Arrays (Merkmal, Schwelle, links, rechts, Größe) – so
läuft die Bewertung für alle Punkte gleichzeitig durch den Baum, statt Punkt
für Punkt in Python. 20.000 Ereignisse × 100 Bäume dauern damit unter einer
Sekunde.

Der Skalierer (Mittelwert/Standardabweichung) steht mit drin, weil Modell und
Skalierung zusammengehören – ein Modell mit fremder Skalierung ist wertlos.
"""

from __future__ import annotations

import math

import numpy as np

_EULER = 0.5772156649


def _c(n: float) -> float:
    """Mittlere Pfadlänge einer erfolglosen Suche im BST mit n Knoten."""
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1) + _EULER) - 2.0 * (n - 1) / n


class Skalierer:
    def __init__(self):
        self.mittel: np.ndarray | None = None
        self.streuung: np.ndarray | None = None
        self.konstant: np.ndarray | None = None   # Achsen, die im Training nie variierten

    def anpassen(self, X: np.ndarray) -> "Skalierer":
        self.mittel = X.mean(axis=0)
        s = X.std(axis=0)
        self.konstant = s == 0
        s[s == 0] = 1.0                       # konstante Achsen nicht durch 0 teilen
        self.streuung = s
        return self

    def unbekannte_achsen(self, x: np.ndarray) -> np.ndarray:
        """Indizes der Achsen, die im Training konstant waren und hier abweichen.
        Der Wald kann dort nicht trennen (nie eine Spaltung gelernt) – für die
        Bewertung ist so eine Abweichung aber starke Evidenz: nie gesehen."""
        if self.konstant is None:
            return np.zeros(0, dtype=np.int64)
        return np.flatnonzero(self.konstant & (np.abs(x - self.mittel) > 1e-9))

    def anwenden(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mittel) / self.streuung

    def zustand(self) -> dict:
        return {"mittel": self.mittel.tolist(), "streuung": self.streuung.tolist(),
                "konstant": self.konstant.tolist() if self.konstant is not None else None}

    @classmethod
    def aus_zustand(cls, z: dict) -> "Skalierer":
        s = cls()
        s.mittel = np.asarray(z["mittel"], dtype=np.float64)
        s.streuung = np.asarray(z["streuung"], dtype=np.float64)
        s.konstant = np.asarray(z["konstant"], dtype=bool) if z.get("konstant") is not None else None
        return s


class _Baum:
    """Ein Isolation Tree als Arrays. Knoten i: merkmal[i] < 0 → Blatt mit groesse[i]."""

    def __init__(self):
        self.merkmal: list[int] = []
        self.schwelle: list[float] = []
        self.links: list[int] = []
        self.rechts: list[int] = []
        self.groesse: list[int] = []

    def _neu(self) -> int:
        self.merkmal.append(-1); self.schwelle.append(0.0)
        self.links.append(-1); self.rechts.append(-1); self.groesse.append(0)
        return len(self.merkmal) - 1

    def bauen(self, X: np.ndarray, idx: np.ndarray, hoehe: int, grenze: int, rng: np.random.Generator) -> int:
        knoten = self._neu()
        n = len(idx)
        if hoehe >= grenze or n <= 1:
            self.groesse[knoten] = n
            return knoten
        teil = X[idx]
        lo, hi = teil.min(axis=0), teil.max(axis=0)
        kandidaten = np.flatnonzero(hi > lo)
        if len(kandidaten) == 0:                # alle Punkte identisch
            self.groesse[knoten] = n
            return knoten
        m = int(rng.choice(kandidaten))
        s = float(rng.uniform(lo[m], hi[m]))
        maske = teil[:, m] < s
        self.merkmal[knoten] = m
        self.schwelle[knoten] = s
        self.links[knoten] = self.bauen(X, idx[maske], hoehe + 1, grenze, rng)
        self.rechts[knoten] = self.bauen(X, idx[~maske], hoehe + 1, grenze, rng)
        return knoten

    def einfrieren(self) -> None:
        self.merkmal = np.asarray(self.merkmal, dtype=np.int64)
        self.schwelle = np.asarray(self.schwelle, dtype=np.float64)
        self.links = np.asarray(self.links, dtype=np.int64)
        self.rechts = np.asarray(self.rechts, dtype=np.int64)
        self.groesse = np.asarray(self.groesse, dtype=np.int64)

    def pfadlaengen(self, X: np.ndarray) -> np.ndarray:
        """Alle Punkte gleichzeitig durch den Baum schicken."""
        n = X.shape[0]
        knoten = np.zeros(n, dtype=np.int64)
        tiefe = np.zeros(n, dtype=np.float64)
        aktiv = np.ones(n, dtype=bool)
        while aktiv.any():
            k = knoten[aktiv]
            innen = self.merkmal[k] >= 0
            if not innen.any():
                break
            ai = np.flatnonzero(aktiv)
            ai_innen = ai[innen]
            k_innen = k[innen]
            geht_links = X[ai_innen, self.merkmal[k_innen]] < self.schwelle[k_innen]
            knoten[ai_innen] = np.where(geht_links, self.links[k_innen], self.rechts[k_innen])
            tiefe[ai_innen] += 1.0
            aktiv[ai[~innen]] = False
        g = self.groesse[knoten].astype(np.float64)
        korrektur = np.array([_c(x) for x in g])
        return tiefe + korrektur

    def zustand(self) -> dict:
        return {"merkmal": self.merkmal.tolist(), "schwelle": self.schwelle.tolist(),
                "links": self.links.tolist(), "rechts": self.rechts.tolist(),
                "groesse": self.groesse.tolist()}

    @classmethod
    def aus_zustand(cls, z: dict) -> "_Baum":
        b = cls()
        b.merkmal, b.schwelle = z["merkmal"], z["schwelle"]
        b.links, b.rechts, b.groesse = z["links"], z["rechts"], z["groesse"]
        b.einfrieren()
        return b


class IsolationForest:
    def __init__(self, baeume: int = 100, stichprobe: int = 256, zufall: int = 42):
        self.n_baeume = baeume
        self.stichprobe = stichprobe
        self.zufall = zufall
        self._baeume: list[_Baum] = []
        self._psi = 0

    def anpassen(self, X: np.ndarray) -> "IsolationForest":
        X = np.asarray(X, dtype=np.float64)
        n = X.shape[0]
        psi = min(self.stichprobe, n)
        grenze = int(math.ceil(math.log2(max(psi, 2))))
        rng = np.random.default_rng(self.zufall)
        self._baeume = []
        for _ in range(self.n_baeume):
            idx = rng.choice(n, size=psi, replace=False)
            b = _Baum()
            b.bauen(X, idx, 0, grenze, rng)
            b.einfrieren()
            self._baeume.append(b)
        self._psi = psi
        return self

    def anomalie(self, X: np.ndarray) -> np.ndarray:
        """s(x) in 0..1 – hoch = auffällig."""
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X[None, :]
        if not self._baeume:
            return np.full(X.shape[0], 0.5)
        h = np.zeros(X.shape[0], dtype=np.float64)
        for b in self._baeume:
            h += b.pfadlaengen(X)
        h /= len(self._baeume)
        return np.power(2.0, -h / (_c(self._psi) or 1.0))

    def rohwert(self, X: np.ndarray) -> np.ndarray:
        """Wie sklearns decision_function: hoch = normal, niedrig = auffällig."""
        return 0.5 - self.anomalie(X)

    def zustand(self) -> dict:
        return {"baeume": [b.zustand() for b in self._baeume], "psi": self._psi,
                "n_baeume": self.n_baeume, "stichprobe": self.stichprobe, "zufall": self.zufall}

    @classmethod
    def aus_zustand(cls, z: dict) -> "IsolationForest":
        f = cls(z.get("n_baeume", 100), z.get("stichprobe", 256), z.get("zufall", 42))
        f._baeume = [_Baum.aus_zustand(b) for b in z.get("baeume", [])]
        f._psi = int(z.get("psi", 0))
        return f
