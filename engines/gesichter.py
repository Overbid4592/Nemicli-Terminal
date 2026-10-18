"""
gesichter.py - Gesichter in einem fertigen Bild finden und Ausschnitte weich einsetzen.

Finder: das Seh-Modell des Bildbeschreibers (Vision/, bildbeschreiber.py) nennt die Gesichter
als Kästen (ganze Zahlen 0–1000 relativ zum Bild). Nur sichere Funde zählen: plausible Größe
und Form, und eine Gegenfrage am Ausschnitt („ein Gesicht mit beiden Augen?“) muss Ja sagen –
im Zweifel bleibt das Original. Ohne Seh-Modell wird nicht nachgebessert (grund()).
Kein OpenCV.
"""

from __future__ import annotations

import re

LUFT = 0.30                     # Rand um den Kasten (Haaransatz, Kinn, Ohren)
HOECHSTENS = 3                  # Gesichter je Bild
MIN_ANTEIL = 0.03               # schmaler als 3 % der Bildbreite: zu klein für sicheres Nachmalen
MAX_ANTEIL = 0.80
FORM = (0.6, 2.2)               # Höhe/Breite eines Gesichtskastens

FINDEN = ("Find every human face in this image. Answer only with JSON, one entry per face: "
          '[{"box": [x1, y1, x2, y2]}] with whole numbers from 0 to 1000 relative to the image '
          "(0,0 = top left corner, 1000,1000 = bottom right corner). Each box covers the face from "
          "forehead to chin. If no face is clearly visible (for example seen from behind), answer [].")
PRUEFEN = ("Does this picture show one human face from the front or the side with both eyes visible? "
           "Answer only yes or no.")

_ZAHLEN = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,"
                     r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")


def verfuegbar() -> bool:
    try:
        import bildbeschreiber
        return bildbeschreiber.finden() is not None
    except Exception:
        return False


def grund() -> str | None:
    """Warum nicht nachgebessert werden kann – oder None."""
    if verfuegbar():
        return None
    return ("Gesicht nachmalen braucht ein Seh-Modell im Ordner Vision/ (eine .gguf und eine "
            "mmproj-*.gguf, z. B. der Bildbeschreiber)")


def kaesten_lesen(antwort: str, W: int, H: int) -> list[tuple[int, int, int, int]]:
    """Kästen aus der Antwort des Seh-Modells → Pixel (x0, y0, x1, y1), ohne Rand.
    0–1000 gilt als relativ; Werte bis 1 als Anteil; größere als ungültig."""
    out = []
    for m in _ZAHLEN.finditer(antwort or ""):
        a, b, c, d = (float(z) for z in m.groups())
        groesste = max(a, b, c, d)
        if min(a, b, c, d) < 0 or groesste > 1000:
            continue
        teiler = 1.0 if groesste <= 1.0 else 1000.0
        x0, x1 = sorted((a / teiler * W, c / teiler * W))
        y0, y1 = sorted((b / teiler * H, d / teiler * H))
        if x1 - x0 >= 1 and y1 - y0 >= 1:
            out.append((int(x0), int(y0), int(round(x1)), int(round(y1))))
    return out


def _plausibel(kasten, W: int) -> bool:
    x0, y0, x1, y1 = kasten
    w, h = x1 - x0, y1 - y0
    return MIN_ANTEIL * W <= w <= MAX_ANTEIL * W and FORM[0] <= h / w <= FORM[1]


def _mit_rand(kasten, W: int, H: int) -> tuple[int, int, int, int]:
    x, y, x1, y1 = kasten
    w, h = x1 - x, y1 - y
    return (max(0, int(x - w * LUFT)), max(0, int(y - h * LUFT * 1.3)),   # oben mehr: Stirn und Haar
            min(W, int(x1 + w * LUFT)), min(H, int(y1 + h * LUFT)))


def _ueberlappt(a, b) -> bool:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    kleiner = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return kleiner > 0 and ix * iy > 0.5 * kleiner


def _ja(antwort: str) -> bool:
    return (antwort or "").strip().lower().lstrip("\"'*„ ").startswith(("yes", "ja"))


def finden(arr) -> list[tuple[int, int, int, int]]:
    """Kästen (x0, y0, x1, y1) mit Rand, größtes Gesicht zuerst; [] ohne sicheren Fund oder ohne
    Seh-Modell. Blockiert (je Bild eine Frage, je Kandidat eine Gegenfrage)."""
    if not verfuegbar():
        return []
    import bildbeschreiber
    from PIL import Image
    bild = Image.fromarray(arr)
    W, H = bild.size
    kandidaten = [k for k in kaesten_lesen(bildbeschreiber.fragen_roh(bild, FINDEN, 200), W, H)
                  if _plausibel(k, W)]
    kandidaten.sort(key=lambda k: (k[2] - k[0]) * (k[3] - k[1]), reverse=True)
    boxen: list[tuple[int, int, int, int]] = []
    for k in kandidaten:
        if len(boxen) >= HOECHSTENS or any(_ueberlappt(k, b) for b in boxen):
            continue
        box = _mit_rand(k, W, H)
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            continue
        if _ja(bildbeschreiber.fragen_roh(bild.crop(box), PRUEFEN, 4)):
            boxen.append(box)
    return boxen


def maske(h: int, w: int, rand: float = 0.18):
    """Elliptische weiche Maske: 1 in der Mitte, zum Rand sanft 0. Eine rechteckige
    Maske hinterließe an den Ecken eine sichtbare Kante."""
    import numpy as np
    yy = (np.arange(h) - (h - 1) / 2) / max(1e-6, (h - 1) / 2)
    xx = (np.arange(w) - (w - 1) / 2) / max(1e-6, (w - 1) / 2)
    r = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2)
    return np.clip((1.0 - r) / (rand * 2), 0.0, 1.0)


def einsetzen(arr, box, neu):
    """`neu` (beliebige Größe) an die Stelle `box` weich einblenden. Gibt ein neues Array zurück."""
    import numpy as np
    from PIL import Image
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    if neu.shape[:2] != (h, w):
        neu = np.asarray(Image.fromarray(neu).resize((w, h), Image.LANCZOS))
    m = maske(h, w)[..., None]
    out = arr.astype(np.float32).copy()
    out[y0:y1, x0:x1] = neu.astype(np.float32) * m + out[y0:y1, x0:x1] * (1 - m)
    return out.round().clip(0, 255).astype(np.uint8)
