"""
schaerfe.py - Adaptives Schärfen für fertige Videos (CAS, Contrast Adaptive Sharpening).

Verfahren nach AMD FidelityFX CAS (MIT-Lizenz): Je Bildpunkt wird nur so stark geschärft, wie der
Kontrast der Umgebung (Kreuz aus 5 Punkten) Platz lässt. Weiche, feine Details gewinnen; harte
Kanten bekommen kaum etwas dazu (keine Halos), glatte Flächen bleiben unverändert.

Stufen: aus · leicht · mittel · stark. Geschärft wird nur, was ins MP4 geht – Bilder, die weiter-
verwendet werden (Fortsetzungs-PNG, Startbild des nächsten Clips), bleiben ungeschärft, sonst
schaukelt sich das Schärfen von Video zu Video auf.
"""

from __future__ import annotations

STUFEN = {"aus": 0.0, "leicht": 0.35, "mittel": 0.65, "stark": 1.0}
# Geschärfte Videos tragen mehr feine Struktur: so viel mehr Datenrate bekommt das MP4
DATENRATE = {"aus": 1.0, "leicht": 1.15, "mittel": 1.3, "stark": 1.45}
_ANDERE = {"0": "aus", "keine": "aus", "nein": "aus", "off": "aus", "none": "aus",
           "schwach": "leicht", "wenig": "leicht", "low": "leicht", "light": "leicht",
           "normal": "mittel", "medium": "mittel", "mid": "mittel",
           "hoch": "stark", "viel": "stark", "high": "stark", "strong": "stark"}


def stufe(wert) -> str | None:
    """Stufe aus einer Angabe („Mittel“, „strong“, „aus“) – None, wenn unbekannt."""
    w = str(wert or "").strip().lower()
    w = _ANDERE.get(w, w)
    return w if w in STUFEN else None


def schaerfen(bilder, art: str, stueck: int = 8, geraet: str | None = None):
    """bilder (T, H, W, 3) uint8 RGB -> geschärfte Kopie (gleiche Form). Bei „aus“ unverändert zurück."""
    import numpy as np
    import torch
    import torch.nn.functional as F
    s = STUFEN.get(stufe(art) or "aus", 0.0)
    if s <= 0 or len(bilder) == 0:
        return bilder
    dev = torch.device(geraet or ("cuda" if torch.cuda.is_available() else "cpu"))
    peak = -1.0 / (8.0 - 3.0 * s)                        # zwischen −1/8 (sanft) und −1/5 (kräftig)
    raus = np.empty_like(bilder)
    with torch.no_grad():
        for i in range(0, len(bilder), stueck):
            x = torch.from_numpy(np.ascontiguousarray(bilder[i:i + stueck])).to(dev).permute(0, 3, 1, 2).float() / 255
            p = F.pad(x, (1, 1, 1, 1), mode="replicate")
            mitte, oben, unten = p[..., 1:-1, 1:-1], p[..., :-2, 1:-1], p[..., 2:, 1:-1]
            links, rechts = p[..., 1:-1, :-2], p[..., 1:-1, 2:]
            kleinst = torch.minimum(torch.minimum(torch.minimum(oben, unten), torch.minimum(links, rechts)), mitte)
            groesst = torch.maximum(torch.maximum(torch.maximum(oben, unten), torch.maximum(links, rechts)), mitte)
            # Platz bis Schwarz bzw. Weiß im Verhältnis zur hellsten Stelle: viel Platz → kräftiger schärfen
            platz = (torch.minimum(kleinst, 1.0 - groesst) / groesst.clamp_min(1e-5)).clamp(0.0, 1.0).sqrt()
            w = platz * peak
            y = (mitte + w * (oben + unten + links + rechts)) / (1.0 + 4.0 * w)
            raus[i:i + stueck] = (y.clamp(0.0, 1.0) * 255.0 + 0.5).to(torch.uint8).permute(0, 2, 3, 1).cpu().numpy()
    return raus
