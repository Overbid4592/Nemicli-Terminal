"""rueckblick.py – Tagesrückblick als Video: die Bilder eines Tages mit sanftem Zoom und Überblendung,
vorne eine Titelkarte, dazu die erzählende Stimme. Nur Pillow und numpy; das MP4 schreibt Windows
(videodatei)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

FPS = 25
MAX_BILDER = 12
MIN_JE_BILD = 3.0              # Sekunden je Bild, auch wenn die Stimme kürzer ist
MAX_SEKUNDEN = 120
UEBERGANG = 0.6                # Sekunden Überblendung zwischen zwei Bildern
TITEL_SEKUNDEN = 2.5
ZOOM = 0.08                    # so viel wächst ein Bild während es zu sehen ist
ENDUNGEN = {".png", ".jpg", ".jpeg", ".webp"}


def bilder_von(ordner: Path, tag: date | None = None, max_n: int = MAX_BILDER) -> list[Path]:
    """Bilder eines Tages (Änderungszeit) aus dem Bilder-Ordner, älteste zuerst; bei mehr als max_n
    gleichmäßig verteilt ausgewählt."""
    tag = tag or date.today()
    try:
        kandidaten = [p for p in Path(ordner).iterdir() if p.is_file() and p.suffix.lower() in ENDUNGEN]
    except OSError:
        return []
    heute = sorted((p for p in kandidaten if datetime.fromtimestamp(p.stat().st_mtime).date() == tag),
                   key=lambda p: p.stat().st_mtime)
    if len(heute) <= max_n:
        return heute
    return [heute[round(i * (len(heute) - 1) / (max_n - 1))] for i in range(max_n)]


def format_waehlen(bilder: list[Path]) -> tuple[int, int]:
    """Hochkant, wenn die meisten Bilder hochkant sind (720×1280), sonst quer (1280×720)."""
    from PIL import Image
    hoch = 0
    for p in bilder:
        with Image.open(p) as im:
            hoch += im.height > im.width
    return (720, 1280) if hoch * 2 >= len(bilder) else (1280, 720)


def _schrift(groesse: int):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, groesse)
        except OSError:
            continue
    return ImageFont.load_default(groesse)


def titelkarte(groesse: tuple[int, int], titel: str, untertitel: str):
    """Dunkle Karte mit Titel und Datum."""
    from PIL import Image, ImageDraw
    W, H = groesse
    karte = Image.new("RGB", groesse, (14, 14, 24))
    d = ImageDraw.Draw(karte)
    for text, gr, y in ((titel, max(36, W // 14), H * 0.42), (untertitel, max(22, W // 28), H * 0.42 + W // 9)):
        f = _schrift(gr)
        while d.textlength(text, font=f) > W * 0.9 and gr > 16:
            gr -= 2
            f = _schrift(gr)
        d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=(240, 236, 255))
    return karte


def _vorbereiten(pfad: Path, groesse: tuple[int, int]):
    """Bild so groß zuschneiden, dass es den Rahmen auch beim Hineinzoomen füllt."""
    from PIL import Image, ImageOps
    W, H = groesse
    with Image.open(pfad) as im:
        return ImageOps.fit(im.convert("RGB"), (int(W * (1 + ZOOM)) + 2, int(H * (1 + ZOOM)) + 2),
                            Image.LANCZOS, centering=(0.5, 0.4))


def _ausschnitt(bild, groesse: tuple[int, int], f: float, richtung: int):
    """Ken-Burns: f = 0 zeigt das ganze vorbereitete Bild, f = 1 ist um ZOOM hineingezoomt; dabei
    wandert der Ausschnitt leicht zur Seite (richtung ±1)."""
    from PIL import Image
    W, H = groesse
    bw, bh = bild.size
    w, h = bw / (1 + ZOOM * f), bh / (1 + ZOOM * f)
    x = (bw - w) * (0.5 + 0.35 * richtung * (f - 0.5))
    y = (bh - h) * 0.45
    return bild.resize((W, H), Image.BILINEAR, box=(x, y, x + w, y + h))


def bilderfolge(bilder: list[Path], sekunden: float, groesse: tuple[int, int], titel: str, untertitel: str,
                fps: int = FPS):
    """Bilder (uint8 RGB) nacheinander: Titelkarte, dann jedes Bild mit Zoom, Überblendung dazwischen."""
    import numpy as np
    from PIL import Image
    karte = titelkarte(groesse, titel, untertitel)
    t_titel = min(TITEL_SEKUNDEN, sekunden * 0.2)
    je_bild = max(MIN_JE_BILD, (sekunden - t_titel) / max(1, len(bilder)))
    n_titel, n_bild, n_ueber = round(t_titel * fps), round(je_bild * fps), round(UEBERGANG * fps)
    for _ in range(n_titel):
        yield np.asarray(karte)
    vorher = karte
    for i, p in enumerate(bilder):
        quelle = _vorbereiten(p, groesse)
        richtung = 1 if i % 2 == 0 else -1
        for k in range(n_bild):
            bild = _ausschnitt(quelle, groesse, k / max(1, n_bild - 1), richtung)
            if k < n_ueber and vorher is not None:             # weich aus dem vorigen Bild herein
                bild = Image.blend(vorher, bild, (k + 1) / (n_ueber + 1))
            yield np.asarray(bild)
        vorher = bild


def dauer(bilder_n: int, ton_sekunden: float) -> float:
    """Länge des Videos: die Stimme bestimmt sie, jedes Bild bleibt aber mindestens MIN_JE_BILD."""
    return min(MAX_SEKUNDEN, max(ton_sekunden + 0.8, TITEL_SEKUNDEN + bilder_n * MIN_JE_BILD))


def erstellen(bilder: list[Path], ziel: Path, titel: str, untertitel: str = "", ton=None) -> dict:
    """Das Video schreiben. ton = (Wellenform, Abtastrate) oder None."""
    import videodatei
    if not bilder:
        raise ValueError("Keine Bilder für den Rückblick.")
    ton_s = len(ton[0]) / ton[1] if ton is not None else 0.0
    sekunden = dauer(len(bilder), ton_s)
    groesse = format_waehlen(bilder)
    videodatei.schreiben(bilderfolge(bilder, sekunden, groesse, titel, untertitel), ziel, FPS, ton=ton)
    return {"pfad": str(ziel), "sekunden": round(sekunden, 1), "bilder": len(bilder),
            "format": f"{groesse[0]}x{groesse[1]}"}


def datum_text(tag: date | None = None) -> str:
    tag = tag or date.today()
    monate = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
              "November", "Dezember")
    return f"{tag.day}. {monate[tag.month - 1]} {tag.year}"

