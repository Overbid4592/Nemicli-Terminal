"""comic.py – aus gemalten Bildern eine Comicseite bauen: Titel, Felder im Raster, Sprechblasen und
Erzähltext. Nur Pillow; die Bilder selbst malt der Bild-Motor (actions._comic_malen)."""
from __future__ import annotations

from pathlib import Path

SEITE_BREITE = 1600
RAND = 40                      # Seitenrand
LUECKE = 28                    # Abstand zwischen den Feldern
RAHMEN = 6
FELD_VERHAELTNIS = 1.25        # Höhe : Breite eines Feldes (die Bilder werden zugeschnitten)
BREIT_HOEHE = 0.7              # ein Feld über beide Spalten: so hoch wie ein normales mal diesen Faktor
ARTEN = ("sprechblase", "erzaehler")
MAX_FELDER = 9
_SCHRIFTEN = ("comicbd.ttf", "comic.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf")


def _schrift(groesse: int):
    from PIL import ImageFont
    for name in _SCHRIFTEN:
        try:
            return ImageFont.truetype(name, groesse)
        except OSError:
            continue
    return ImageFont.load_default(groesse)


def _umbrechen(draw, text: str, schrift, breite: int) -> list[str]:
    zeilen, akt = [], ""
    for wort in text.split():
        probe = f"{akt} {wort}".strip()
        if draw.textlength(probe, font=schrift) <= breite or not akt:
            akt = probe
        else:
            zeilen.append(akt)
            akt = wort
    if akt:
        zeilen.append(akt)
    return zeilen


def _einpassen(draw, text: str, breite: int, max_zeilen: int, groesse: int = 34, mindest: int = 18):
    """Größte Schrift, mit der der Text in höchstens max_zeilen Zeilen passt."""
    while True:
        schrift = _schrift(groesse)
        zeilen = _umbrechen(draw, text, schrift, breite)
        if len(zeilen) <= max_zeilen or groesse <= mindest:
            return schrift, zeilen[:max_zeilen] if groesse <= mindest else zeilen
        groesse -= 2


def _textblock(draw, kasten: tuple[int, int, int, int], text: str, art: str) -> None:
    """Sprechblase (unten, weiß, mit Spitze) oder Erzählkasten (oben links, gelb) in ein Feld setzen."""
    x0, y0, x1, y1 = kasten
    breite = x1 - x0
    innen = int(breite * (0.84 if art == "sprechblase" else 0.72))
    schrift, zeilen = _einpassen(draw, text, innen - 36, max_zeilen=4)
    if art == "sprechblase":                     # kurze Sätze: Blase so breit wie der Text
        innen = max(int(breite * 0.3), int(max(draw.textlength(z, font=schrift) for z in zeilen)) + 40)
    zh = schrift.size + 8
    hoehe = zh * len(zeilen) + 30
    if art == "erzaehler":
        bx0, by0 = x0 + 16, y0 + 16
        bx1, by1 = bx0 + innen, by0 + hoehe
        draw.rectangle((bx0, by0, bx1, by1), fill=(255, 244, 194), outline=(0, 0, 0), width=4)
    else:
        bx0 = x0 + (breite - innen) // 2
        by1 = y1 - 22
        by0, bx1 = by1 - hoehe, bx0 + innen
        spitze_x = bx0 + innen // 3
        draw.polygon([(spitze_x, by0 + 4), (spitze_x + 46, by0 + 4), (spitze_x + 6, by0 - 40)],
                     fill=(255, 255, 255), outline=(0, 0, 0))
        draw.rounded_rectangle((bx0, by0, bx1, by1), radius=34, fill=(255, 255, 255), outline=(0, 0, 0), width=4)
        draw.line((spitze_x + 3, by0 + 2, spitze_x + 43, by0 + 2), fill=(255, 255, 255), width=6)
    y = by0 + 15
    for z in zeilen:
        tw = draw.textlength(z, font=schrift)
        x = bx0 + 18 if art == "erzaehler" else bx0 + (innen - tw) / 2
        draw.text((x, y), z, font=schrift, fill=(0, 0, 0))
        y += zh


def raster(n: int) -> list[tuple[int, int, int]]:
    """Felder als (Zeile, Spalte, Spaltenbreite in Feldern): zwei Spalten; ein übriges letztes Feld
    geht über die ganze Breite."""
    spalten = 1 if n <= 1 else 2
    aus = []
    for i in range(n):
        zeile, spalte = divmod(i, spalten)
        breit = spalten == 2 and i == n - 1 and n % 2 == 1
        aus.append((zeile, 0 if breit else spalte, 2 if breit else 1))
    return aus


def seite_bauen(felder: list[dict], ziel: Path, titel: str = "") -> Path:
    """felder: [{bild: Pfad, text: str, art: sprechblase|erzaehler}] -> PNG-Seite."""
    from PIL import Image, ImageDraw, ImageOps
    if not felder:
        raise ValueError("Keine Felder für die Comicseite.")
    lage = raster(len(felder))
    spalten = max(s + w for _z, s, w in lage)
    fb = (SEITE_BREITE - 2 * RAND - (spalten - 1) * LUECKE) // spalten
    fh = int(fb * FELD_VERHAELTNIS) if spalten == 2 else int(fb * 0.75)
    titel_h = 110 if titel else 0
    zeilen = max(z for z, _s, _w in lage) + 1
    # Ein breites Feld über beide Spalten wird flacher – sonst bliebe vom Hochkantbild nur ein Streifen
    zeilen_h = [int(fh * BREIT_HOEHE) if spalten == 2 and all(w == 2 for z2, _s, w in lage if z2 == z) else fh
                for z in range(zeilen)]
    oben = [RAND + titel_h + sum(zeilen_h[:z]) + z * LUECKE for z in range(zeilen)]
    hoehe = oben[-1] + zeilen_h[-1] + RAND
    seite = Image.new("RGB", (SEITE_BREITE, hoehe), (255, 255, 255))
    draw = ImageDraw.Draw(seite)
    if titel:
        schrift, zeilen_t = _einpassen(draw, titel, SEITE_BREITE - 2 * RAND, max_zeilen=1, groesse=72, mindest=36)
        tw = draw.textlength(zeilen_t[0], font=schrift)
        draw.text(((SEITE_BREITE - tw) / 2, RAND), zeilen_t[0], font=schrift, fill=(0, 0, 0))
    for f, (z, s, w) in zip(felder, lage):
        x0, y0, h = RAND + s * (fb + LUECKE), oben[z], zeilen_h[z]
        breite = fb * w + LUECKE * (w - 1)
        with Image.open(f["bild"]) as im:            # Ausschnitt eher oben: dort sind meist die Gesichter
            seite.paste(ImageOps.fit(im.convert("RGB"), (breite, h), Image.LANCZOS, centering=(0.5, 0.3)),
                        (x0, y0))
        kasten = (x0, y0, x0 + breite, y0 + h)
        if str(f.get("text") or "").strip():
            _textblock(draw, kasten, str(f["text"]).strip(), f.get("art") if f.get("art") in ARTEN else "sprechblase")
        draw.rectangle(kasten, outline=(0, 0, 0), width=RAHMEN)
    ziel = Path(ziel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    seite.save(ziel)
    return ziel
