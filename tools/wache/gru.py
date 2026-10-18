"""GRU auf numpy – ein kleines rekurrentes Netz, das Abfolgen von Zeichen lernt.

Warum selbst gebaut: wie der Isolation Forest (wald.py) ohne torch und ohne
scikit-learn – die Wache läuft in der exe ohne beides, und eine unsignierte DLL
blockiert Windows Smart App Control.

Aufgabe: Wahrscheinlichkeit des nächsten Zeichens einer Folge vorhersagen.
Eine GRU-Zelle (Cho et al. 2014) hält einen Zustand h, der die bisherige Folge
zusammenfasst:

  z = σ(x·Wz + h·Uz + bz)            Update-Tor
  r = σ(x·Wr + h·Ur + br)            Reset-Tor
  n = tanh(x·Wn + (r⊙h)·Un + bn)     Kandidat
  h' = (1 − z)⊙n + z⊙h
  p(nächstes) = softmax(h'·Wo + bo)

x ist die gelernte Einbettung des aktuellen Zeichens. Gelernt wird mit
Kreuzentropie, Backpropagation durch die Zeit und Adam. Klein gehalten
(Einbettung 16, Zustand 32): Training auf einigen Tausend Folgen dauert
Sekunden, die Bewertung eines Zeichens Mikrosekunden.
"""

from __future__ import annotations

import time

import numpy as np


def _sig(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


class GRU:
    NAMEN = ("emb", "Wzr", "Uzr", "bzr", "Wn", "Un", "bn", "Wo", "bo")

    def __init__(self, vokabular: int, einbettung: int = 16, versteckt: int = 32, zufall: int = 42):
        self.V, self.E, self.H = int(vokabular), int(einbettung), int(versteckt)
        rng = np.random.default_rng(zufall)
        V, E, H = self.V, self.E, self.H

        def gewicht(a: int, b: int) -> np.ndarray:
            return rng.normal(0.0, 1.0 / np.sqrt(a), (a, b))

        self.p: dict[str, np.ndarray] = {
            "emb": rng.normal(0.0, 0.1, (V, E)),
            "Wzr": gewicht(E, 2 * H), "Uzr": gewicht(H, 2 * H), "bzr": np.zeros(2 * H),
            "Wn": gewicht(E, H), "Un": gewicht(H, H), "bn": np.zeros(H),
            "Wo": gewicht(H, V), "bo": np.zeros(V),
        }

    # ------------------------------------------------------------- Vorwärts

    def anfang(self, anzahl: int = 1) -> np.ndarray:
        return np.zeros((anzahl, self.H))

    def schritt(self, h: np.ndarray, zeichen) -> np.ndarray:
        """Ein Zeichen (oder je Zeile eines) einspeisen, neuer Zustand."""
        p, H = self.p, self.H
        x = p["emb"][np.atleast_1d(zeichen)]
        a = x @ p["Wzr"] + h @ p["Uzr"] + p["bzr"]
        z, r = _sig(a[:, :H]), _sig(a[:, H:])
        n = np.tanh(x @ p["Wn"] + (r * h) @ p["Un"] + p["bn"])
        return (1.0 - z) * n + z * h

    def verteilung(self, h: np.ndarray) -> np.ndarray:
        """Wahrscheinlichkeiten des nächsten Zeichens zum Zustand h."""
        return _softmax(h @ self.p["Wo"] + self.p["bo"])

    # ------------------------------------------------------------- Lernen

    def verlust_und_gradient(self, X: np.ndarray, Y: np.ndarray, M: np.ndarray):
        """X, Y: (B, T) Zeichen-IDs (Eingabe, Ziel), M: (B, T) Maske 0/1.
        Gibt (mittlerer Verlust, Gradienten) zurück. Aufgefüllt wird nur am Ende –
        aufgefüllte Schritte tragen nichts zum Verlust und damit nichts zum Gradienten bei."""
        p, H = self.p, self.H
        B, T = X.shape
        h = np.zeros((B, H))
        speicher = []
        for t in range(T):
            x = p["emb"][X[:, t]]
            a = x @ p["Wzr"] + h @ p["Uzr"] + p["bzr"]
            z, r = _sig(a[:, :H]), _sig(a[:, H:])
            rh = r * h
            n = np.tanh(x @ p["Wn"] + rh @ p["Un"] + p["bn"])
            hn = (1.0 - z) * n + z * h
            speicher.append((x, h, z, r, rh, n))
            h = hn
        hs = np.stack([s[1] for s in speicher[1:]] + [h]) if T else np.zeros((0, B, H))
        # hs[t] = Zustand NACH Schritt t
        flach = hs.reshape(T * B, H)
        P = _softmax(flach @ p["Wo"] + p["bo"])
        y = Y.T.reshape(-1)
        m = M.T.reshape(-1).astype(np.float64)
        anzahl = max(float(m.sum()), 1.0)
        verlust = float(-(np.log(np.maximum(P[np.arange(T * B), y], 1e-12)) * m).sum() / anzahl)

        g = {k: np.zeros_like(v) for k, v in p.items()}
        dL = P
        dL[np.arange(T * B), y] -= 1.0
        dL *= (m / anzahl)[:, None]
        g["Wo"] = flach.T @ dL
        g["bo"] = dL.sum(axis=0)
        dH = (dL @ p["Wo"].T).reshape(T, B, H)
        dh_weiter = np.zeros((B, H))
        for t in range(T - 1, -1, -1):
            x, h_vor, z, r, rh, n = speicher[t]
            dh = dH[t] + dh_weiter
            dz = dh * (h_vor - n)
            dn = dh * (1.0 - z)
            dh_vor = dh * z
            dan = dn * (1.0 - n * n)
            g["Wn"] += x.T @ dan
            g["bn"] += dan.sum(axis=0)
            dx = dan @ p["Wn"].T
            drh = dan @ p["Un"].T
            g["Un"] += rh.T @ dan
            dr = drh * h_vor
            dh_vor += drh * r
            da = np.concatenate([dz * z * (1.0 - z), dr * r * (1.0 - r)], axis=1)
            g["Wzr"] += x.T @ da
            g["Uzr"] += h_vor.T @ da
            g["bzr"] += da.sum(axis=0)
            dx += da @ p["Wzr"].T
            dh_vor += da @ p["Uzr"].T
            np.add.at(g["emb"], X[:, t], dx)
            dh_weiter = dh_vor
        return verlust, g

    def trainieren(self, folgen: list[list[int]], *, epochen: int = 6, lernrate: float = 0.01,
                   stapel: int = 32, zufall: int = 7, zeitlimit: float = 120.0, abbruch=None) -> list[float]:
        """Folgen von Zeichen-IDs (je mindestens 2). Gibt den mittleren Verlust je Epoche zurück."""
        folgen = [f for f in folgen if len(f) >= 2]
        if not folgen:
            return []
        rng = np.random.default_rng(zufall)
        m = {k: np.zeros_like(v) for k, v in self.p.items()}
        v = {k: np.zeros_like(w) for k, w in self.p.items()}
        b1, b2, eps = 0.9, 0.999, 1e-8
        schritt = 0
        start = time.perf_counter()
        verlauf: list[float] = []
        for _ in range(epochen):
            reihenfolge = rng.permutation(len(folgen))
            summe, teile = 0.0, 0
            for i in range(0, len(reihenfolge), stapel):
                if abbruch is not None and abbruch():
                    return verlauf
                teil = [folgen[j] for j in reihenfolge[i:i + stapel]]
                T = max(len(f) for f in teil) - 1
                X = np.zeros((len(teil), T), dtype=np.int64)
                Y = np.zeros((len(teil), T), dtype=np.int64)
                M = np.zeros((len(teil), T))
                for zeile, f in enumerate(teil):
                    n = len(f) - 1
                    X[zeile, :n] = f[:-1]
                    Y[zeile, :n] = f[1:]
                    M[zeile, :n] = 1.0
                verlust, g = self.verlust_und_gradient(X, Y, M)
                norm = np.sqrt(sum(float((x * x).sum()) for x in g.values()))
                if norm > 5.0:
                    for k in g:
                        g[k] *= 5.0 / norm
                schritt += 1
                for k in self.p:
                    m[k] = b1 * m[k] + (1 - b1) * g[k]
                    v[k] = b2 * v[k] + (1 - b2) * g[k] * g[k]
                    mh = m[k] / (1 - b1 ** schritt)
                    vh = v[k] / (1 - b2 ** schritt)
                    self.p[k] -= lernrate * mh / (np.sqrt(vh) + eps)
                summe += verlust
                teile += 1
            verlauf.append(summe / max(teile, 1))
            if time.perf_counter() - start > zeitlimit:
                break
        return verlauf

    # ------------------------------------------------------------- Speichern

    def zustand(self) -> dict:
        return {"V": self.V, "E": self.E, "H": self.H,
                "p": {k: self.p[k].tolist() for k in self.NAMEN}}

    @classmethod
    def aus_zustand(cls, z: dict) -> "GRU":
        g = cls(z["V"], z["E"], z["H"])
        for k in cls.NAMEN:
            wert = np.asarray(z["p"][k], dtype=np.float64)
            if wert.shape != g.p[k].shape:
                raise ValueError(f"GRU-Gewicht {k}: Form {wert.shape} statt {g.p[k].shape}")
            g.p[k] = wert
        return g
