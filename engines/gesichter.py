"""
gesichter.py - Gesichter in einem fertigen Bild finden und Ausschnitte weich einsetzen.

Finder: OpenCV-Haar-Cascades (frontal + Profil), Gegenprobe mit der Augen-Cascade.
OpenCV bekommt nur Pixel-Arrays, nie Dateien – Bilddateien öffnet Pillow.
OpenCV 5 enthält keine Haar-Cascades mehr; deshalb ist es auf <5 festgelegt.
"""

from __future__ import annotations

LUFT = 0.30                     # Rand um den Cascade-Kasten (Haaransatz, Kinn, Ohren)
HOECHSTENS = 3                  # Gesichter je Bild


def verfuegbar() -> bool:
    try:
        import cv2
        return hasattr(cv2, "CascadeClassifier")
    except Exception:
        return False


def _cascade(name: str):
    import cv2
    clf = cv2.CascadeClassifier(cv2.data.haarcascades + name)
    return None if clf.empty() else clf


def finden(arr) -> list[tuple[int, int, int, int]]:
    """Kästen (x0, y0, x1, y1) mit etwas Rand, größtes Gesicht zuerst. [] ohne Fund
    oder ohne OpenCV."""
    if not verfuegbar():
        return []
    import cv2

    grau = cv2.equalizeHist(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY))
    H, W = grau.shape[:2]

    funde: list[tuple] = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        clf = _cascade(name)
        if clf is None:
            continue
        # Unter 4 % der Bildbreite lohnt kein Nachmalen.
        for (x, y, w, h) in clf.detectMultiScale(grau, scaleFactor=1.1, minNeighbors=5,
                                                 minSize=(max(24, int(W * 0.04)),) * 2):
            funde.append((int(x), int(y), int(w), int(h)))
    if not funde:
        return []

    # Frontal- und Profil-Cascade finden oft dasselbe Gesicht: größter Kasten gewinnt.
    funde.sort(key=lambda f: f[2] * f[3], reverse=True)
    behalten: list[tuple] = []
    for (x, y, w, h) in funde:
        mx, my = x + w / 2, y + h / 2
        if any(abs(mx - (bx + bw / 2)) < bw * 0.5 and abs(my - (by + bh / 2)) < bh * 0.5
               for (bx, by, bw, bh) in behalten):
            continue
        behalten.append((x, y, w, h))

    # Gegenprobe: Hals und Dekolleté sehen für die Cascade oft wie ein Gesicht aus,
    # haben aber keine Augen. Ein Fehlalarm würde ein Gesicht ins Bild malen.
    augen = _cascade("haarcascade_eye.xml")
    if augen is not None:
        geprueft = []
        for (x, y, w, h) in behalten:
            roi = grau[y:y + int(h * 0.65), x:x + w]
            if roi.size and len(augen.detectMultiScale(
                    roi, 1.1, 4, minSize=(max(8, int(w * 0.10)),) * 2)):
                geprueft.append((x, y, w, h))
        behalten = geprueft

    boxen = []
    for (x, y, w, h) in behalten[:HOECHSTENS]:
        x0 = max(0, int(x - w * LUFT))
        y0 = max(0, int(y - h * LUFT * 1.3))         # oben mehr: Stirn und Haar
        x1 = min(W, int(x + w * (1 + LUFT)))
        y1 = min(H, int(y + h * (1 + LUFT)))
        if x1 - x0 >= 32 and y1 - y0 >= 32:
            boxen.append((x0, y0, x1, y1))
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
