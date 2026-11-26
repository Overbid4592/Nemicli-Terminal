"""figurmass.py – Maße und Platz der stehenden Figur im Terminal-Fenster (Figur-Justierer).

Je Persönlichkeit eine Datei `Figur/justierung.json`. Das Fenster liest und schreibt sie (Regler), das Programm
ebenso (Werkzeug figur_justieren); beide prüfen die Werte hier.

  groesse   Höhe in Zeilen
  kopf, rumpf, arme, beine, fuesse   Länge als Faktor (1 = wie gemalt bzw. wie die Bewegung)
  x         Platz waagerecht: 0 = ganz links, 1 = ganz rechts
  y         Zeilen über dem Rahmen der Eingabe (0 = steht darauf, negativ = tiefer)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DATEI = "justierung.json"
VORGABE = {"groesse": 14, "kopf": 1.0, "rumpf": 1.0, "arme": 1.0, "beine": 1.0, "fuesse": 1.0, "x": 1.0, "y": 0}
GRENZEN = {"groesse": (8, 24), "kopf": (0.7, 1.3), "rumpf": (0.7, 1.3), "arme": (0.7, 1.3), "beine": (0.7, 1.3),
           "fuesse": (0.7, 1.3), "x": (0.0, 1.0), "y": (-2, 20)}
GANZZAHL = {"groesse", "y"}
MAX_BYTES = 4096
# Welche Länge ein Gelenk mitnimmt: der Knochen vom Elterngelenk bis hierher wird um diesen Faktor gestreckt
KETTE = {"l_elbow": "arme", "r_elbow": "arme", "l_wrist": "arme", "r_wrist": "arme",
         "l_knee": "beine", "r_knee": "beine", "l_ankle": "beine", "r_ankle": "beine",
         "l_foot": "fuesse", "r_foot": "fuesse",
         "spine1": "rumpf", "spine2": "rumpf", "spine3": "rumpf", "neck": "rumpf", "head": "kopf"}


def sauber(werte) -> dict:
    """Bekannte Werte in ihre Grenzen, Unbekanntes weg, Fehlendes aus VORGABE."""
    raus = dict(VORGABE)
    if not isinstance(werte, dict):
        return raus
    for k, (unten, oben) in GRENZEN.items():
        try:
            v = float(werte.get(k, raus[k]))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        v = min(oben, max(unten, v))
        raus[k] = int(round(v)) if k in GANZZAHL else round(v, 3)
    return raus


def lesen(pfad: Path) -> dict:
    try:
        pfad = Path(pfad)
        if pfad.name != DATEI or not pfad.is_file() or pfad.stat().st_size > MAX_BYTES:
            return dict(VORGABE)
        return sauber(json.loads(pfad.read_text(encoding="utf-8")))
    except Exception:
        return dict(VORGABE)


def schreiben(pfad: Path, werte: dict) -> dict:
    """Geprüft und in einem Schritt ablegen (erst .neu, dann ersetzen). Gibt die abgelegten Werte zurück."""
    pfad = Path(pfad)
    if pfad.name != DATEI:
        raise ValueError("Nur justierung.json")
    werte = sauber(werte)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    neu = pfad.with_name(DATEI + ".neu")
    neu.write_text(json.dumps(werte, indent=1), encoding="utf-8")
    neu.replace(pfad)
    return werte


def strecken(punkte: list, eltern: list, namen: list, werte: dict) -> list:
    """Gelenke (x, y, Tiefe im Fenster) mit den Längen aus `werte`: jeder Knochen wird ab seinem Elterngelenk um den
    Faktor seiner Kette gestreckt, alles darunter wandert mit. Danach steht die Figur wieder auf demselben Boden
    (größtes y bleibt)."""
    if not punkte:
        return punkte
    neu = list(punkte)
    for i, e in enumerate(eltern):
        if e < 0:
            continue
        f = float(werte.get(KETTE.get(namen[i], ""), 1.0))
        (px, py, pz), (ex, ey, ez), (nx, ny, nz) = punkte[i], punkte[e], neu[e]
        neu[i] = (nx + (px - ex) * f, ny + (py - ey) * f, nz + (pz - ez) * f)
    boden = max(p[1] for p in punkte) - max(p[1] for p in neu)
    return [(x, y + boden, z) for x, y, z in neu]
