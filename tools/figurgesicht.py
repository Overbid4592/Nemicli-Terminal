"""figurgesicht.py – Gesichtsausdrücke für die bewegliche Figur (tools/figurrig.py) mit dem Porträt-Motor.

Aus dem Vorderbild des Körpers (koerper_vorne.png) rechnet LivePortrait (engines/liveportrait) je Ausdruck ein Bild
in denselben Maßen – nur das Gesicht ändert sich. figurrig.bauen schneidet daraus den Kopf wie die Kopf-Röhre zu; das
Fenster tauscht nur den Kopf, wenn die Figur ein Gesicht zeigen soll (Werkzeug figur_bewegen, Feld gesicht).

Ablage neben dem Körper-Bild: gesicht_<name>.png. Ein Gesicht gilt, solange es nicht älter als das Körper-Bild ist.
"""
from __future__ import annotations

from pathlib import Path

# Name -> Regler des Porträt-Motors (engines/liveportrait/motor.py, NEUTRAL/GRENZEN); nicht genannt = wie im Bild
AUSDRUECKE: dict[str, dict[str, float]] = {
    "froh": {"laecheln": 0.9},
    "lacht": {"laecheln": 1.2, "mund": 30, "augenbrauen": 6},
    "grinst": {"laecheln": 0.5, "grinsen": 10},
    "zwinkert": {"zwinkern": 28, "laecheln": 0.45},
    "staunt": {"mund": 50, "augenbrauen": 22},
    "ernst": {"augenbrauen": -12, "laecheln": -0.2},
    "verliebt": {"laecheln": 0.9, "augen": 0.45},
    "schlaeft": {"augen": 0.0, "laecheln": 0.1},
}
DATEI = "gesicht_{}.png"
KOERPER = "koerper_vorne.png"


def vorhanden(ordner: Path) -> dict[str, Path]:
    """Die gültigen Gesichter neben dem Körper-Bild (nicht älter als es): Name -> Datei."""
    ordner = Path(ordner)
    koerper = ordner / KOERPER
    if not koerper.is_file():
        return {}
    stand = koerper.stat().st_mtime
    raus = {}
    for name in AUSDRUECKE:
        pfad = ordner / DATEI.format(name)
        if pfad.is_file() and pfad.stat().st_mtime >= stand:
            raus[name] = pfad
    return raus


def fehlen(ordner: Path) -> bool:
    """Gibt es ein Körper-Bild, aber nicht alle Gesichter dazu?"""
    return (Path(ordner) / KOERPER).is_file() and len(vorhanden(ordner)) < len(AUSDRUECKE)


def bereit() -> bool:
    """Ist der Porträt-Motor eingerichtet (Modelle, onnxruntime)?"""
    try:
        from liveportrait import motor as LP
        return LP.fehlt() is None
    except Exception:
        return False


def gesichts_kasten(koerper: Path) -> tuple[float, float, float, float]:
    """Wo das Gesicht im Ganzkörper-Bild liegt (x1, y1, x2, y2) – aus dem Skelett: zwischen Scheitel und Hals, um den
    Kopfpunkt. Im ganzen Bild ist das Gesicht zu klein, um es ohne Hinweis sicher zu finden."""
    from PIL import Image
    import figurrig
    with Image.open(koerper) as bild:
        gelenke = figurrig.gelenke_schaetzen(figurrig._maske(bild.convert("RGBA")), "vorne")
    oben, hals = gelenke["kopf_oben"][1], gelenke["neck"][1]
    hoehe = (hals - oben) * 0.8
    mx, my = gelenke["head"][0], oben + (hals - oben) * 0.55
    return mx - hoehe / 2, my - hoehe / 2, mx + hoehe / 2, my + hoehe / 2


def malen(ordner: Path, *, on_status=None) -> list[Path]:
    """Alle Gesichter aus koerper_vorne.png rechnen und ablegen (in einem Durchgang, danach wird der Motor entladen)."""
    import numpy as np
    from PIL import Image
    from liveportrait import motor as LP
    if (grund := LP.fehlt()):
        raise RuntimeError("Der Porträt-Motor fehlt: " + grund)
    ordner = Path(ordner)
    koerper = ordner / KOERPER
    if on_status:
        on_status("Gesichter · lege das Gesicht an …")
    try:
        motor = LP.motor()
        quelle = motor.vorbereiten(str(koerper), kasten=gesichts_kasten(koerper), frei=True)
        offen = sum(quelle.augen) / 2
        werte = [{**LP.NEUTRAL, "augen": offen, **{k: float(v) for k, v in regler.items()}}
                 for regler in AUSDRUECKE.values()]
        if on_status:
            on_status(f"Gesichter · rechne {len(werte)} Ausdrücke …")
        bilder, deckung = motor.bilder(quelle, werte, mit_alpha=True)
        fertig = []
        for i, name in enumerate(AUSDRUECKE):
            rgb = (bilder[i].permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8)
            a = (deckung[i, 0].cpu().numpy() * 255).round().astype(np.uint8)
            ziel = ordner / DATEI.format(name)
            neu = ziel.with_suffix(".neu.png")
            Image.fromarray(np.dstack([rgb, a]), "RGBA").save(neu)
            neu.replace(ziel)
            fertig.append(ziel)
        return fertig
    finally:
        LP.entladen()
