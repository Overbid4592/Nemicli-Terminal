"""sprechen.py – aus der Stimme Mundformen und Körpersprache fürs Porträt.

Ohne Lippen-Modell und ohne Wörterbuch, deshalb in jeder Sprache: Je Bild wird der Klang vermessen
(Pegel, Formanten F1/F2 über LPC, Tonhöhe, Zischanteil) und auf die Lippen-Regler des Porträt-Motors
abgebildet:

  F1 hoch            → Mund weit auf (A)          Regler „mund“
  F2 hoch            → Lippen breit (I, E)        Regler „grinsen“
  F2 tief            → Lippen rund (O, U)         Regler „spitzen“
  Zischlaut          → Zähne zeigen (S, F, Z)     wenig „mund“, etwas „grinsen“
  kurze Lücke, Nasal → Lippen zu (M, B, P)        „mund“ negativ

Körpersprache aus derselben Analyse: kleines Nicken auf betonten Silben, Augenbrauen bei Betonung und
am Ende einer Frage, Blinzeln in Pausen, Wiegen des Kopfes passend zur Lautstärke.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

ANALYSE_RATE = 12000            # Formanten: Sprache bis 6 kHz reicht
FENSTER_S = 0.03                # Analysefenster je Bild
LPC_ORDNUNG = 14
STILLE = 0.06                   # Pegel (Anteil am lauten Pegel) darunter: still
LUECKE_MAX_S = 0.16             # kürzere Stille mitten im Satz: Verschlusslaut, Lippen zu

# Formant-Bereiche (Hz) für die Abbildung; gemittelt über Frauen- und Männerstimmen
F1_ZU, F1_AUF = 300.0, 900.0
F2_RUND, F2_MITTE_RUND = 850.0, 1350.0
F2_MITTE_BREIT, F2_BREIT = 1600.0, 2200.0

# Regler-Ausschläge (Werte des Porträt-Motors, siehe motor.GRENZEN)
MUND_GRUND, MUND_WEIT = 12.0, 78.0
GRINSEN_MAX = 13.0
SPITZEN_MAX = 14.0
VERSCHLUSS = -32.0              # M/B/P: Lippen aufeinander
ZISCH_MUND, ZISCH_GRINSEN = 8.0, 7.0
ANSPRECHEN, ABKLINGEN = 0.75, 0.45   # Lippen folgen dem Klang: schneller auf als zu
SCHLIESSEN = 0.8                # zum Verschluss (M/B/P) schnell – der Laut dauert nur 2–3 Bilder
NASAL_F1, NASAL_F2, NASAL_OBEN = 290.0, 1550.0, 0.035   # M, N: tiefes F1, kaum Obertöne

LIPPEN = ("mund", "grinsen", "spitzen")


@dataclass
class Bildklang:
    pegel: float                # 0–1, bezogen auf die lauten Stellen
    f1: float                   # Hz (0 = nicht bestimmbar)
    f2: float
    hz: float                   # Tonhöhe (0 = stimmlos)
    zisch: float                # Anteil hoher Frequenzen 0–1
    oben: float = 1.0           # Anteil über 1 kHz – bei Nasalen (M, N) fast nichts


def _neu_abtasten(w: np.ndarray, rate: int, ziel: int) -> np.ndarray:
    if rate == ziel:
        return w
    n = int(round(len(w) * ziel / rate))
    if n <= 1:
        return np.zeros(max(n, 0), np.float32)
    # Tiefpass vor dem Verkleinern (gleitender Mittelwert über das Verhältnis), dann linear
    k = max(1, int(round(rate / ziel)))
    if k > 1:
        w = np.convolve(w, np.ones(k, np.float32) / k, mode="same")
    x = np.linspace(0, len(w) - 1, n)
    return np.interp(x, np.arange(len(w)), w).astype(np.float32)


def _lpc(x: np.ndarray, ordnung: int) -> np.ndarray | None:
    """Levinson-Durbin über die Autokorrelation; None bei zu wenig Signal."""
    r = np.correlate(x, x, mode="full")[len(x) - 1:len(x) + ordnung]
    if r[0] <= 1e-9:
        return None
    a = np.zeros(ordnung + 1)
    a[0], fehler = 1.0, r[0]
    for i in range(1, ordnung + 1):
        k = -(r[i] + np.dot(a[1:i], r[i - 1:0:-1])) / fehler
        a[1:i] = a[1:i] + k * a[i - 1:0:-1]
        a[i] = k
        fehler *= 1 - k * k
        if fehler <= 1e-12:
            return None
    return a


def formanten(x: np.ndarray, rate: int = ANALYSE_RATE) -> tuple[float, float]:
    """(F1, F2) in Hz aus einem Fenster; (0, 0), wenn keine zwei Formanten zu finden sind."""
    if len(x) < LPC_ORDNUNG * 3:
        return 0.0, 0.0
    y = np.append(x[0], x[1:] - 0.97 * x[:-1]) * np.hamming(len(x))
    a = _lpc(y.astype(np.float64), LPC_ORDNUNG)
    if a is None:
        return 0.0, 0.0
    wurzeln = [z for z in np.roots(a) if z.imag > 0]
    kandidaten = []
    for z in wurzeln:
        f = math.atan2(z.imag, z.real) * rate / (2 * math.pi)
        band = -rate / math.pi * math.log(max(abs(z), 1e-9))
        if 200 < f < rate / 2 - 200 and band < 500:
            kandidaten.append(f)
    kandidaten.sort()
    if len(kandidaten) < 2:
        return 0.0, 0.0
    return float(kandidaten[0]), float(kandidaten[1])


def _tonhoehe(x: np.ndarray, rate: int) -> float:
    """Grundfrequenz über die Autokorrelation (70–500 Hz); 0 = stimmlos."""
    x = x - x.mean()
    if np.dot(x, x) <= 1e-9:
        return 0.0
    r = np.correlate(x, x, mode="full")[len(x) - 1:]
    lo, hi = rate // 500, min(len(r) - 1, rate // 70)
    if hi <= lo:
        return 0.0
    k = lo + int(np.argmax(r[lo:hi]))
    return rate / k if r[k] > 0.3 * r[0] else 0.0


def analyse(wellen, abtastrate: int, fps: int) -> list[Bildklang]:
    """Je Bild ein Bildklang (Fenster um die Bildmitte)."""
    w = np.asarray(wellen, dtype=np.float32).ravel()
    n = max(1, math.ceil(len(w) * fps / abtastrate))
    x = _neu_abtasten(w, abtastrate, ANALYSE_RATE)
    halb = int(FENSTER_S * ANALYSE_RATE / 2)
    roh = []
    for i in range(n):
        mitte = int((i + 0.5) / fps * ANALYSE_RATE)
        teil = x[max(0, mitte - halb):mitte + halb]
        if len(teil) < 8:
            roh.append((0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            continue
        pegel = float(np.sqrt(np.mean(teil * teil)))
        spektrum = np.abs(np.fft.rfft(teil * np.hanning(len(teil)))) ** 2
        freq = np.fft.rfftfreq(len(teil), 1 / ANALYSE_RATE)
        gesamt = float(spektrum.sum()) or 1.0
        zisch = float(spektrum[freq > 3500].sum()) / gesamt
        oben = float(spektrum[freq > 1000].sum()) / gesamt
        f1, f2 = formanten(teil)
        roh.append((pegel, f1, f2, _tonhoehe(teil, ANALYSE_RATE), zisch, oben))
    pegel = np.array([r[0] for r in roh], np.float32)
    laut = pegel[pegel > 0]
    bezug = float(np.percentile(laut, 90)) if len(laut) else 1.0
    return [Bildklang(min(1.0, p / max(bezug, 1e-6)), f1, f2, hz, z, o) for (p, f1, f2, hz, z, o) in roh]


def _klemmen(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _ziel(k: Bildklang) -> dict[str, float]:
    """Lippen-Ziel eines Bildes (vor dem Glätten)."""
    if k.pegel < STILLE:
        return {"mund": 0.0, "grinsen": 0.0, "spitzen": 0.0}
    staerke = math.sqrt(k.pegel)
    if k.hz and 0 < k.f1 < NASAL_F1 and k.f2 < NASAL_F2 and k.oben < NASAL_OBEN:   # Nasal: M, N
        return {"mund": VERSCHLUSS * 0.7, "grinsen": 0.0, "spitzen": 0.0}
    if k.hz == 0 and k.zisch > 0.35:                                # stimmloser Zischlaut: S, F, Z, SCH
        return {"mund": ZISCH_MUND * staerke, "grinsen": ZISCH_GRINSEN, "spitzen": 0.0}
    if not k.f1:                                                    # stimmhaft ohne Formanten: halb offen
        return {"mund": (MUND_GRUND + 20) * staerke, "grinsen": 0.0, "spitzen": 0.0}
    auf = _klemmen((k.f1 - F1_ZU) / (F1_AUF - F1_ZU))
    rund = _klemmen((F2_MITTE_RUND - k.f2) / (F2_MITTE_RUND - F2_RUND))
    breit = _klemmen((k.f2 - F2_MITTE_BREIT) / (F2_BREIT - F2_MITTE_BREIT))
    return {"mund": (MUND_GRUND + (MUND_WEIT - MUND_GRUND) * auf) * staerke,
            "grinsen": GRINSEN_MAX * breit * (1 - 0.5 * auf),
            "spitzen": SPITZEN_MAX * rund * (1 - 0.6 * auf)}


def lippen(klang: list[Bildklang], fps: int) -> list[dict[str, float]]:
    """Lippen je Bild: Ziel aus dem Klang, Verschluss in kurzen Lücken mitten im Satz, weich geglättet."""
    ziele = [_ziel(k) for k in klang]
    still = [k.pegel < STILLE for k in klang]
    luecke = max(1, int(LUECKE_MAX_S * fps))
    i = 0
    while i < len(still):                                           # kurze Stille zwischen Lauten: M/B/P
        if not still[i]:
            i += 1
            continue
        j = i
        while j < len(still) and still[j]:
            j += 1
        if 0 < i and j < len(still) and j - i <= luecke:
            for k in range(i, j):
                ziele[k]["mund"] = VERSCHLUSS
        i = j
    aus, y = [], {n: 0.0 for n in LIPPEN}
    for z in ziele:
        for n in LIPPEN:
            f = (SCHLIESSEN if n == "mund" and z[n] < 0 else ANSPRECHEN if abs(z[n]) > abs(y[n]) else ABKLINGEN)
            y[n] += (z[n] - y[n]) * f
        aus.append({n: round(y[n], 2) for n in LIPPEN})
    return aus


def _silben(klang: list[Bildklang], fps: int) -> list[int]:
    """Betonte Silben: deutliche Pegel-Spitzen, mindestens 0,35 s auseinander."""
    p = [k.pegel for k in klang]
    abstand = max(1, int(0.35 * fps))
    spitzen, letzte = [], -abstand
    for i in range(1, len(p) - 1):
        if p[i] > 0.75 and p[i] >= p[i - 1] and p[i] >= p[i + 1] and i - letzte >= abstand:
            spitzen.append(i)
            letzte = i
    return spitzen


def _pausen(klang: list[Bildklang], fps: int, mindestens_s: float = 0.25) -> list[tuple[int, int]]:
    aus, i = [], 0
    while i < len(klang):
        if klang[i].pegel >= STILLE:
            i += 1
            continue
        j = i
        while j < len(klang) and klang[j].pegel < STILLE:
            j += 1
        if j - i >= mindestens_s * fps:
            aus.append((i, j))
        i = j
    return aus


def koerpersprache(klang: list[Bildklang], fps: int, text: str = "", seed: int = 0) -> list[dict]:
    """Schlüsselbilder (motor.verlauf) zur Stimme: Wiegen, Nicken auf Betonung, Augenbrauen, Blinzeln."""
    r = random.Random(seed)
    d = len(klang) / fps
    k: list[dict] = [{"t": 0.0, "laecheln": 0.18}]
    t = 0.0
    while t < d:                                                    # ruhiges Wiegen, beim Reden lebhafter
        t += r.uniform(1.0, 1.8)
        i = min(len(klang) - 1, int(t * fps))
        lebhaft = 0.6 + 0.8 * float(np.mean([c.pegel for c in klang[max(0, i - fps):i + 1]]))
        k.append({"t": t, "drehen": r.uniform(-4, 4) * lebhaft, "neigen": r.uniform(-2.5, 2.5) * lebhaft,
                  "blick_x": r.uniform(-5, 5)})
    for i in _silben(klang, fps):                                   # Nicken und Brauen auf betonten Silben
        s = i / fps
        tiefe = r.uniform(2.0, 3.5)
        k += [{"t": max(0.0, s - 0.12), "nicken": 0.0}, {"t": s, "nicken": tiefe},
              {"t": s + 0.22, "nicken": 0.0}]
        if r.random() < 0.3:
            k += [{"t": max(0.0, s - 0.1), "augenbrauen": 0.0}, {"t": s, "augenbrauen": 9.0},
                  {"t": s + 0.35, "augenbrauen": 0.0}]
    if text.strip().endswith("?"):                                  # Frage: Brauen hoch, Kopf leicht schräg
        ende = max((j for j, c in enumerate(klang) if c.pegel >= STILLE), default=len(klang) - 1) / fps
        k += [{"t": max(0.0, ende - 0.6), "augenbrauen": 0.0, "neigen": 0.0},
              {"t": ende, "augenbrauen": 14.0, "neigen": 4.0}]
    blinzeln = [(a + b) / 2 / fps for a, b in _pausen(klang, fps)]
    if not blinzeln:
        blinzeln = [r.uniform(0.8, 1.6)]
    zuletzt = -9.0
    for s in blinzeln + [s + r.uniform(2.5, 4.0) for s in blinzeln]:
        if s < d - 0.3 and s - zuletzt > 1.5:
            k += [{"t": s, "augen": None}, {"t": s + 0.08, "augen": 0.0}, {"t": s + 0.2, "augen": None}]
            zuletzt = s
    return k
