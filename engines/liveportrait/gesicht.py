"""gesicht.py – Gesicht finden, zuschneiden und wieder einsetzen (ohne OpenCV und InsightFace).

Gesichtspunkte: landmark.onnx von LivePortrait (203 Punkte) über onnxruntime auf der CPU. Den
ersten groben Platz liefert ein Lauf über das ganze Bild; zwei weitere Läufe auf dem jeweils neuen
Ausschnitt ziehen nach. Findet das nicht zusammen (kleines Gesicht im Ganzkörperbild), nennt das
Seh-Modell den Kasten (gesichter.finden). Zuschnitt und Zurücksetzen sind Ähnlichkeits-
transformationen wie im Original (crop.py), gerechnet mit torch.grid_sample.
"""
from __future__ import annotations

from math import acos, cos, sin
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

LANDMARK_GROESSE = 224
QUELLE_GROESSE = 512           # Ausschnitt fürs Zurücksetzen; das Netz bekommt ihn auf 256 verkleinert
QUELLE_SKALA = 2.3             # Ausschnitt = 2,3 × Gesicht (crop_config.py)
QUELLE_VY = -0.125             # etwas mehr Stirn
MAX_KANTE = 1280               # größere Bilder werden verkleinert (wie load_img_online)


def bild_laden(pfad) -> np.ndarray:
    """RGB uint8, längste Kante höchstens MAX_KANTE, Höhe und Breite gerade."""
    from PIL import Image
    with Image.open(pfad) as im:
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > MAX_KANTE:
            f = MAX_KANTE / max(w, h)
            im = im.resize((round(w * f), round(h * f)), Image.LANCZOS)
        arr = np.array(im)
    return np.ascontiguousarray(arr[: arr.shape[0] & ~1, : arr.shape[1] & ~1])


# ---------------------------------------------------------------------------
# Transformationen (wie crop.py, in numpy)
# ---------------------------------------------------------------------------

def _zwei_punkte(pts: np.ndarray) -> np.ndarray:
    """Augenmitte und Mundmitte aus 203 Punkten – hebt die Neigung auf."""
    links = pts[[0, 6, 12, 18]].mean(0)
    rechts = pts[[24, 30, 36, 42]].mean(0)
    return np.stack([(links + rechts) / 2, (pts[48] + pts[66]) / 2])


def rechteck(pts: np.ndarray, skala: float, vx: float = 0.0, vy: float = 0.0) -> tuple[np.ndarray, float, float]:
    """Mitte, Kantenlänge (quadratisch) und Drehwinkel des Gesichtsausschnitts."""
    p2 = _zwei_punkte(pts)
    uy = p2[1] - p2[0]
    laenge = np.linalg.norm(uy)
    uy = np.array([0.0, 1.0]) if laenge <= 1e-3 else uy / laenge
    ux = np.array([uy[1], -uy[0]])
    winkel = acos(float(np.clip(ux[0], -1, 1)))
    if ux[1] < 0:
        winkel = -winkel
    mitte0 = pts.mean(0)
    rpts = (pts - mitte0) @ np.array([ux, uy]).T
    lo, ru = rpts.min(0), rpts.max(0)
    mitte1 = (lo + ru) / 2
    groesse = float(max(ru - lo)) * skala
    mitte = mitte0 + ux * mitte1[0] + uy * mitte1[1] + ux * (vx * groesse) + uy * (vy * groesse)
    return mitte, groesse, winkel


def matrix(mitte, groesse: float, winkel: float, ziel: int) -> np.ndarray:
    """3×3 Original -> Ausschnitt (ziel × ziel)."""
    s = ziel / groesse
    c, n = cos(winkel), sin(winkel)
    cx, cy = float(mitte[0]), float(mitte[1])
    t = ziel / 2
    return np.array([[s * c, s * n, t - s * (c * cx + n * cy)],
                     [-s * n, s * c, t - s * (-n * cx + c * cy)],
                     [0.0, 0.0, 1.0]])


def punkte_abbilden(pts: np.ndarray, M: np.ndarray) -> np.ndarray:
    return pts @ M[:2, :2].T + M[:2, 2]


def verziehen(bild: torch.Tensor, M: np.ndarray, breite: int, hoehe: int) -> torch.Tensor:
    """Wie cv2.warpAffine (bilinear, Rand schwarz): bild (B, C, H, W), M bildet Quelle -> Ziel ab."""
    inv = torch.tensor(np.linalg.inv(M), dtype=torch.float32, device=bild.device)
    ys, xs = torch.meshgrid(torch.arange(hoehe, device=bild.device, dtype=torch.float32),
                            torch.arange(breite, device=bild.device, dtype=torch.float32), indexing="ij")
    sx = inv[0, 0] * xs + inv[0, 1] * ys + inv[0, 2]
    sy = inv[1, 0] * xs + inv[1, 1] * ys + inv[1, 2]
    H, W = bild.shape[-2:]
    gitter = torch.stack([(2 * sx + 1) / W - 1, (2 * sy + 1) / H - 1], dim=-1)[None].to(bild.dtype)
    return F.grid_sample(bild, gitter.expand(bild.shape[0], -1, -1, -1), mode="bilinear",
                         padding_mode="zeros", align_corners=False)


def als_tensor(arr: np.ndarray, geraet="cpu") -> torch.Tensor:
    return torch.from_numpy(arr).permute(2, 0, 1)[None].float().div(255).to(geraet)


# ---------------------------------------------------------------------------
# Gesichtspunkte
# ---------------------------------------------------------------------------

class Gesichtspunkte:
    def __init__(self, pfad: Path):
        import onnxruntime
        opt = onnxruntime.SessionOptions()
        opt.intra_op_num_threads = 4
        self.sitzung = onnxruntime.InferenceSession(str(pfad), sess_options=opt,
                                                    providers=["CPUExecutionProvider"])

    def __call__(self, bild: np.ndarray, M: np.ndarray) -> np.ndarray:
        """203 Punkte im Originalbild; M = Original -> 224er-Ausschnitt."""
        aus = verziehen(als_tensor(bild), M, LANDMARK_GROESSE, LANDMARK_GROESSE).numpy()
        roh = self.sitzung.run(None, {"input": aus.astype(np.float32)})[2]
        pts = np.asarray(roh[0], dtype=np.float64).reshape(-1, 2) * LANDMARK_GROESSE
        return punkte_abbilden(pts, np.linalg.inv(M))


def _ganzes_bild(bild: np.ndarray) -> np.ndarray:
    h, w = bild.shape[:2]
    return matrix((w / 2, h / 2), max(w, h), 0.0, LANDMARK_GROESSE)


def _aus_kasten(kasten) -> np.ndarray:
    x1, y1, x2, y2 = kasten
    return matrix(((x1 + x2) / 2, (y1 + y2) / 2), max(x2 - x1, y2 - y1) * 1.5, 0.0, LANDMARK_GROESSE)


def _nachziehen(gp: Gesichtspunkte, bild: np.ndarray, M: np.ndarray, laeufe: int = 3) -> tuple[np.ndarray, float]:
    """Punkte suchen und den Ausschnitt nachziehen. Rückgabe: Punkte, Sprung im letzten Lauf (Anteil)."""
    pts = gp(bild, M)
    sprung = 1.0
    for _ in range(laeufe):
        mitte, groesse, winkel = rechteck(pts, 1.5, vy=-0.1)
        neu = gp(bild, matrix(mitte, groesse, winkel, LANDMARK_GROESSE))
        sprung = float(np.linalg.norm(neu.mean(0) - pts.mean(0)) / max(groesse, 1.0))
        pts = neu
    return pts, sprung


def finden(bild: np.ndarray, gp: Gesichtspunkte, kasten=None) -> np.ndarray:
    """203 Gesichtspunkte. Wackelt das Nachziehen, fragt es das Seh-Modell nach dem Kasten."""
    if kasten is None:
        pts, sprung = _nachziehen(gp, bild, _ganzes_bild(bild))
        if sprung < 0.03:
            return pts
        try:
            import gesichter
            kaesten = gesichter.finden(bild)
        except Exception:
            kaesten = []
        if not kaesten:
            raise RuntimeError("Kein Gesicht sicher gefunden.")
        kasten = kaesten[0]
    pts, sprung = _nachziehen(gp, bild, _aus_kasten(kasten))
    if sprung >= 0.03:
        raise RuntimeError("Kein Gesicht sicher gefunden.")
    return pts


# ---------------------------------------------------------------------------
# Verhältnisse (retargeting_utils.py) und Maske
# ---------------------------------------------------------------------------

def _verhaeltnis(pts, a, b, c, d) -> float:
    return float(np.linalg.norm(pts[a] - pts[b]) / (np.linalg.norm(pts[c] - pts[d]) + 1e-6))


def augen_offen(pts: np.ndarray) -> tuple[float, float]:
    return _verhaeltnis(pts, 6, 18, 0, 12), _verhaeltnis(pts, 30, 42, 24, 36)


def mund_offen(pts: np.ndarray) -> float:
    return _verhaeltnis(pts, 90, 102, 48, 66)


def maske(groesse: int = QUELLE_GROESSE, rand: float = 0.04, weich: float = 0.12) -> torch.Tensor:
    """Weiche Maske für den Ausschnitt: innen 1, zum Rand hin sanft 0 – keine Kante im Bild."""
    i = torch.arange(groesse, dtype=torch.float32)
    abstand = torch.minimum(i, groesse - 1 - i) / groesse
    a = ((abstand - rand) / weich).clamp(0, 1)
    a = a * a * (3 - 2 * a)
    return (a[:, None] * a[None, :])[None, None]
