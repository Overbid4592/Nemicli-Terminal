"""malen.py – 🎨 Malen in Nemi Office ohne Oberfläche: Bildformate, Grenzen und die Zeichen-Befehle der OfficeAI.

Die KI zeichnet mit Formen in Bildpunkten der Leinwand ((0, 0) = oben links): Linie, Pfeil, Rechteck, Kreis, Ellipse,
Vieleck, freier Strich, weiche Kurve, Bogen, Stern, Herz, regelmäßiges Vieleck, Sprühfarbe und Text – mit Linienstil,
Farbverlauf, Drehung und Pinselart. Dazu Bereichs-Werkzeuge (radieren, verschieben, spiegeln, drehen, skalieren,
umfärben, zuschneiden, Farbe lesen). Hier wird geprüft und vereinheitlicht; gemalt wird im Office-Fenster
(ui/office_malen.py). Reines Python – läuft auch ohne eingerichtete Python-Umgebung.
"""
from __future__ import annotations

import math

from buero.textmodell import farbe_sauber

BILD_OEFFNEN = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WebP", ".bmp": "Bitmap"}
BILD_SPEICHERN = {".png": "PNG", ".jpg": "JPEG", ".webp": "WebP", ".bmp": "Bitmap"}
STANDARD_GROESSE = (1280, 800)
MIN_SEITE, MAX_SEITE = 16, 8000
MAX_FORMEN = 80              # je zeichnen-Aufruf
MAX_PUNKTE = 400             # je Form
MAX_PUNKTE_GESAMT = 6000     # je zeichnen-Aufruf
MAX_TEXT = 500               # Zeichen eines Text-Elements
MAX_DICKE = 200
MAX_ELEMENTE = 150           # so viele Einträge merkt sich die Leinwand für „ansehen“
ARTEN = ("linie", "pfeil", "rechteck", "kreis", "ellipse", "polygon", "strich", "kurve", "bogen", "stern", "herz",
         "n_eck", "spray", "text")
_ALIAS = {"line": "linie", "arrow": "pfeil", "rect": "rechteck", "rectangle": "rechteck", "circle": "kreis",
          "oval": "ellipse", "vieleck": "polygon", "dreieck": "polygon", "path": "strich", "pinsel": "strich",
          "freihand": "strich", "bezier": "kurve", "spline": "kurve", "arc": "bogen", "star": "stern", "heart": "herz",
          "sechseck": "n_eck", "fuenfeck": "n_eck", "achteck": "n_eck", "polygon_regel": "n_eck", "spruehen": "spray",
          "airbrush": "spray", "spruehdose": "spray"}
_ECKEN_ALIAS = {"fuenfeck": 5, "sechseck": 6, "achteck": 8}
FLAECHEN = ("rechteck", "kreis", "ellipse", "polygon", "stern", "herz", "n_eck")     # (kurve nur geschlossen)
LINIENSTILE = ("durchgezogen", "gestrichelt", "gepunktet")
PINSEL = ("normal", "bleistift", "marker", "kalligrafie", "weich")
DREHBAR = ("rechteck", "ellipse", "stern", "herz", "n_eck", "text")
MAX_SPRAY = 3000             # Tupfer einer Sprühform
MAX_BEREICH = 8_000_000      # Bildpunkte, die umfaerben auf einmal prüft

# Malfarben: kräftig, dazu Himmel/Haut/Erde – für Zeichnungen, nicht für Tabellenflächen
MAL_FARBEN = {
    "schwarz": "#1f1f24", "weiß": "#ffffff", "weiss": "#ffffff", "grau": "#8a8a94", "hellgrau": "#d4d4da",
    "dunkelgrau": "#4a4a52", "rot": "#e53935", "dunkelrot": "#9b1c1c", "orange": "#fb8c00", "gelb": "#fdd835",
    "gold": "#f2b705", "grün": "#43a047", "gruen": "#43a047", "hellgrün": "#9ccc65", "dunkelgrün": "#1b5e20",
    "türkis": "#00acc1", "blau": "#1e88e5", "hellblau": "#81d4fa", "himmelblau": "#87ceeb", "dunkelblau": "#0d2c6b",
    "lila": "#8e24aa", "violett": "#8e24aa", "flieder": "#c39bd3", "pink": "#ec407a", "rosa": "#f8bbd0",
    "braun": "#795548", "hellbraun": "#bc8a5f", "beige": "#f1e0c5", "haut": "#f2c9a0", "creme": "#fff8e7",
    "silber": "#c0c0c8", "nachtblau": "#141a3a",
}


def farbe(wert, leer_erlaubt: bool = False) -> str | None:
    """'#rrggbb' oder ein Farbwort -> '#rrggbb'; '' (keine) nur mit leer_erlaubt; None = ungültig."""
    t = str(wert if wert is not None else "").strip().lower()
    if t in ("", "keine", "kein", "none", "transparent", "aus"):
        return "" if leer_erlaubt else None
    if (c := farbe_sauber(t)):
        return c
    return MAL_FARBEN.get(t)


def _zahl(wert, lo: float, hi: float) -> float | None:
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    if math.isnan(z) or math.isinf(z):
        return None
    return min(max(z, lo), hi)


def _punkt(wert, b: int, h: int) -> tuple[float, float] | None:
    if isinstance(wert, dict):
        wert = [wert.get("x"), wert.get("y")]
    if not isinstance(wert, (list, tuple)) or len(wert) != 2:
        return None
    x, y = _zahl(wert[0], -b, 2 * b), _zahl(wert[1], -h, 2 * h)
    return None if x is None or y is None else (x, y)


def _verlauf(roh) -> dict | str | None:
    """{"von", "nach", "art" (linear | radial), "winkel"} -> vereinheitlicht; None = keiner; str = Fehler."""
    if roh in (None, "", {}):
        return None
    if not isinstance(roh, dict):
        return "verlauf: Objekt mit von, nach (Farben), art (linear | radial) und winkel (Grad, nur linear)."
    von, nach = farbe(roh.get("von")), farbe(roh.get("nach"))
    if von is None or nach is None:
        return "verlauf: von und nach als #rrggbb oder Farbwort."
    art = str(roh.get("art") or "linear").strip().lower()
    if art not in ("linear", "radial"):
        return "verlauf: art linear oder radial."
    return {"von": von, "nach": nach, "art": art, "winkel": _zahl(roh.get("winkel", 90), -360, 360) or 0.0}


def bereich_pruefen(aktion: dict, b: int, h: int, pflicht: bool = True) -> tuple[int, int, int, int] | str | None:
    """x, y, b, h eines Bereichs, auf die Leinwand beschnitten -> (x, y, b, h); None = keiner angegeben (wenn nicht
    Pflicht); str = Fehler."""
    werte = [aktion.get(k) for k in ("x", "y", "b", "h")]
    if all(w in (None, "") for w in werte):
        return "Bereich fehlt: x, y (oben links), b und h." if pflicht else None
    zahlen = [_zahl(w, -10 * max(b, h), 10 * max(b, h)) for w in werte]
    if any(z is None for z in zahlen):
        return "Bereich: x, y, b und h als Zahlen (Bildpunkte)."
    x0, y0 = max(0, int(zahlen[0])), max(0, int(zahlen[1]))
    x1, y1 = min(b, int(zahlen[0] + zahlen[2])), min(h, int(zahlen[1] + zahlen[3]))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return "Der Bereich liegt außerhalb der Leinwand oder ist leer."
    return x0, y0, x1 - x0, y1 - y0


WERKZEUG_FEHLER = {"spiegeln": "richtung: waagerecht (links ↔ rechts) oder senkrecht (oben ↔ unten)."}


def werkzeug_pruefen(name: str, aktion: dict, b: int, h: int) -> dict | str:
    """Die Bereichs-Werkzeuge der OfficeAI prüfen -> vereinheitlichtes dict oder Fehlertext."""
    if name == "radieren":
        if aktion.get("punkte") is not None:
            g = form_pruefen({"art": "strich", "punkte": aktion.get("punkte"), "farbe": "weiß",
                              "dicke": aktion.get("dicke", 20)}, b, h)
            return g if isinstance(g, str) else {"strich": g}
        r = bereich_pruefen(aktion, b, h)
        return r if isinstance(r, str) else {"bereich": r}
    if name in ("verschieben", "skalieren", "zuschneiden", "umfaerben", "spiegeln", "drehen"):
        pflicht = name in ("verschieben", "skalieren", "zuschneiden")
        r = bereich_pruefen(aktion, b, h, pflicht=pflicht)
        if isinstance(r, str):
            return r
        aus: dict = {"bereich": r}
        if name == "verschieben":
            nach = _punkt(aktion.get("nach"), b, h)
            if nach is None:
                return "verschieben: nach [x, y] (neue obere linke Ecke)."
            aus.update(nach=nach, kopieren=str(aktion.get("kopieren")).strip().lower() in ("true", "1", "ja"))
        elif name == "skalieren":
            nb, nh = _zahl(aktion.get("neu_b"), 1, MAX_SEITE), _zahl(aktion.get("neu_h"), 1, MAX_SEITE)
            if nb is None or nh is None:
                return "skalieren: neu_b und neu_h (neue Größe in Bildpunkten)."
            aus.update(neu_b=int(nb), neu_h=int(nh))
        elif name == "umfaerben":
            von, nach = farbe(aktion.get("von")), farbe(aktion.get("nach"))
            if von is None or nach is None:
                return "umfaerben: von und nach als #rrggbb oder Farbwort."
            flaeche = (r[2] * r[3]) if r else b * h
            if flaeche > MAX_BEREICH:
                return f"umfaerben: höchstens {MAX_BEREICH} Bildpunkte auf einmal – Bereich verkleinern."
            aus.update(von=von, nach=nach, toleranz=int(_zahl(aktion.get("toleranz", 30), 0, 255) or 0))
        elif name == "spiegeln":
            richtung = str(aktion.get("richtung") or "").strip().lower()
            if richtung not in ("waagerecht", "senkrecht"):
                return WERKZEUG_FEHLER["spiegeln"]
            aus["richtung"] = richtung
        elif name == "drehen":
            w = _zahl(aktion.get("winkel"), -360, 360)
            if w is None or not w:
                return "drehen: winkel in Grad (positiv = im Uhrzeigersinn); die ganze Leinwand nur 90, 180 oder 270."
            if r is None and int(w) % 90:
                return ("Die ganze Leinwand dreht sich nur um 90, 180 oder 270 Grad – für andere Winkel einen "
                        "Bereich angeben.")
            aus["winkel"] = w
        return aus
    if name == "farbe_lesen":
        pkt = _punkt([aktion.get("x"), aktion.get("y")], b, h)
        if pkt is None or not (0 <= pkt[0] < b and 0 <= pkt[1] < h):
            return "farbe_lesen: x, y innerhalb der Leinwand."
        return {"x": int(pkt[0]), "y": int(pkt[1])}
    return f"Unbekannt: {name}"


def groesse_pruefen(b, h) -> tuple[int, int] | str:
    """Leinwandgröße: (b, h) oder Fehlertext."""
    try:
        b, h = int(b), int(h)
    except (TypeError, ValueError):
        return "b und h als ganze Zahlen (Bildpunkte)."
    if not (MIN_SEITE <= b <= MAX_SEITE and MIN_SEITE <= h <= MAX_SEITE):
        return f"b und h zwischen {MIN_SEITE} und {MAX_SEITE} Bildpunkten."
    return b, h


def form_pruefen(f, b: int, h: int) -> dict | str:
    """Eine Form der KI -> vereinheitlichtes dict oder Fehlertext. Koordinaten bleiben innerhalb der dreifachen
    Leinwand (etwas darf über den Rand ragen)."""
    if not isinstance(f, dict):
        return "jede Form ist ein Objekt mit art."
    roh_art = str(f.get("art") or f.get("typ") or "").strip().lower()
    art = _ALIAS.get(roh_art, roh_art)
    if art not in ARTEN:
        return f"art '{f.get('art')}' gibt es nicht – erlaubt: {', '.join(ARTEN)}."
    aus: dict = {"art": art}
    geschlossen = art == "kurve" and str(f.get("geschlossen")).strip().lower() in ("true", "1", "ja", "yes")
    flaeche = art in FLAECHEN or geschlossen
    linie = farbe(f.get("farbe", "schwarz"), leer_erlaubt=flaeche)          # Fläche ohne Rand: farbe ""
    if linie is None:
        return f"farbe '{f.get('farbe')}' kenne ich nicht – #rrggbb oder ein Farbwort ({', '.join(list(MAL_FARBEN)[:12])} …)."
    aus["farbe"] = linie
    if flaeche:
        fuell = farbe(f.get("fuellung", ""), leer_erlaubt=True)
        if fuell is None:
            return f"fuellung '{f.get('fuellung')}' kenne ich nicht."
        verlauf = _verlauf(f.get("verlauf"))
        if isinstance(verlauf, str):
            return verlauf
        if not fuell and not linie and not verlauf:
            return "ohne farbe, fuellung und verlauf wäre die Form unsichtbar."
        aus["fuellung"] = fuell
        if verlauf:
            aus["verlauf"] = verlauf
    aus["dicke"] = _zahl(f.get("dicke", 3), 0.5, MAX_DICKE) or 3.0
    aus["deckkraft"] = _zahl(f.get("deckkraft", 1), 0.05, 1.0) or 1.0
    stil = str(f.get("linienstil") or "durchgezogen").strip().lower()
    if stil not in LINIENSTILE:
        return f"linienstil '{f.get('linienstil')}' gibt es nicht – {', '.join(LINIENSTILE)}."
    if stil != "durchgezogen":
        aus["linienstil"] = stil
    if art in DREHBAR and f.get("drehung") not in (None, ""):
        aus["drehung"] = _zahl(f.get("drehung"), -360, 360) or 0.0
    if art in ("linie", "pfeil"):
        von, nach = _punkt(f.get("von"), b, h), _punkt(f.get("nach"), b, h)
        if von is None or nach is None:
            return f"{art}: von [x, y] und nach [x, y] angeben."
        aus.update(von=von, nach=nach)
    elif art in ("rechteck", "ellipse"):
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        bb, hh = _zahl(f.get("b"), 1, 3 * b), _zahl(f.get("h"), 1, 3 * h)
        if None in (x, y, bb, hh):
            return f"{art}: x, y (oben links), b und h angeben."
        aus.update(x=x, y=y, b=bb, h=hh, rund=_zahl(f.get("rund", 0), 0, min(bb, hh) / 2) or 0.0)
    elif art == "kreis":
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        r = _zahl(f.get("r"), 0.5, 2 * max(b, h))
        if None in (x, y, r):
            return "kreis: x, y (Mitte) und r angeben."
        aus.update(x=x, y=y, r=r)
    elif art in ("polygon", "strich", "kurve"):
        roh = f.get("punkte")
        mindest = 3 if art == "polygon" or geschlossen else 2
        if not isinstance(roh, list) or len(roh) < mindest:
            return f"{art}: punkte [[x, y], …] (mindestens {mindest})."
        if len(roh) > MAX_PUNKTE:
            return f"{art}: höchstens {MAX_PUNKTE} Punkte je Form."
        punkte = [_punkt(p, b, h) for p in roh]
        if any(p is None for p in punkte):
            return f"{art}: jeder Punkt ist [x, y]."
        aus["punkte"] = punkte
        if art == "kurve":
            aus["geschlossen"] = geschlossen
        if art in ("strich", "kurve") and not geschlossen:
            pinsel = str(f.get("pinsel") or "normal").strip().lower()
            if pinsel not in PINSEL:
                return f"pinsel '{f.get('pinsel')}' gibt es nicht – {', '.join(PINSEL)}."
            if pinsel != "normal":
                aus["pinsel"] = pinsel
    elif art == "bogen":
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        bb, hh = _zahl(f.get("b"), 1, 3 * b), _zahl(f.get("h"), 1, 3 * h)
        start, winkel = _zahl(f.get("start", 0), -720, 720), _zahl(f.get("winkel"), -360, 360)
        if None in (x, y, bb, hh, start, winkel) or not winkel:
            return "bogen: x, y (Mitte), b, h (Größe der ganzen Ellipse), start und winkel in Grad (0 = rechts, " \
                   "positiv = gegen den Uhrzeigersinn)."
        aus.update(x=x, y=y, b=bb, h=hh, start=start, winkel=winkel)
    elif art in ("stern", "n_eck"):
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        r = _zahl(f.get("r"), 1, 2 * max(b, h))
        if None in (x, y, r):
            return f"{art}: x, y (Mitte) und r angeben."
        vorgabe = 5 if art == "stern" else _ECKEN_ALIAS.get(roh_art, 6)
        anzahl = int(_zahl(f.get("zacken" if art == "stern" else "ecken", vorgabe), 3, 24) or vorgabe)
        aus.update(x=x, y=y, r=r, ecken=anzahl)
        if art == "stern":
            aus["innen"] = _zahl(f.get("innen", 0.45), 0.15, 0.95) or 0.45
    elif art == "herz":
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        bb, hh = _zahl(f.get("b"), 2, 3 * b), _zahl(f.get("h"), 2, 3 * h)
        if None in (x, y, bb, hh):
            return "herz: x, y (Mitte), b und h angeben."
        aus.update(x=x, y=y, b=bb, h=hh)
    elif art == "spray":
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        r = _zahl(f.get("r", 30), 2, 600)
        if None in (x, y, r):
            return "spray: x, y (Mitte), r (Radius) und dichte (Tupfer) angeben."
        aus.update(x=x, y=y, r=r, dichte=int(_zahl(f.get("dichte", 300), 10, MAX_SPRAY) or 300))
        aus["dicke"] = _zahl(f.get("dicke", 2), 0.5, 20) or 2.0
    elif art == "text":
        x, y = _zahl(f.get("x"), -b, 2 * b), _zahl(f.get("y"), -h, 2 * h)
        text = " ".join(str(f.get("text") or "").split())[:MAX_TEXT]
        if x is None or y is None or not text:
            return "text: x, y (oben links) und text angeben."
        aus.update(x=x, y=y, text=text, groesse=_zahl(f.get("groesse", 32), 6, 600) or 32.0,
                   fett=str(f.get("fett")).strip().lower() in ("true", "1", "ja", "yes"))
    return aus


def formen_pruefen(formen, b: int, h: int) -> tuple[list[dict], str | None]:
    """Liste der KI -> (geprüfte Formen, Fehlertext der ersten falschen oder None)."""
    if not isinstance(formen, list) or not formen:
        return [], "formen: Liste mit mindestens einer Form."
    if len(formen) > MAX_FORMEN:
        return [], f"Höchstens {MAX_FORMEN} Formen je Aufruf – teil die Zeichnung auf mehrere Aufrufe auf."
    aus, punkte = [], 0
    for i, f in enumerate(formen, 1):
        g = form_pruefen(f, b, h)
        if isinstance(g, str):
            return [], f"Form {i}: {g}"
        punkte += len(g.get("punkte") or ()) or (g["dichte"] // 10 if g["art"] == "spray" else 2)
        aus.append(g)
    if punkte > MAX_PUNKTE_GESAMT:
        return [], f"Zusammen höchstens {MAX_PUNKTE_GESAMT} Punkte je Aufruf."
    return aus, None


def _farbname(c: str) -> str:
    for name, wert in MAL_FARBEN.items():
        if wert == c:
            return name
    return c


def beschreiben(f: dict) -> str:
    """Kurzer Eintrag für die Formenliste („ansehen“)."""
    art = f["art"]
    c = _farbname(f["farbe"]) if f.get("farbe") else "ohne Rand"
    if f.get("fuellung") and not f.get("farbe"):
        c = ""
    fuell = f"{' ' if c else ''}gefüllt {_farbname(f['fuellung'])}" if f.get("fuellung") else ""
    r = lambda v: int(round(v))                                                 # noqa: E731
    if art in ("linie", "pfeil"):
        return f"{art.capitalize()} {c} {r(f['dicke'])} px von ({r(f['von'][0])},{r(f['von'][1])}) nach " \
               f"({r(f['nach'][0])},{r(f['nach'][1])})"
    if art in ("rechteck", "ellipse"):
        return f"{art.capitalize()} {c}{fuell} bei ({r(f['x'])},{r(f['y'])}) {r(f['b'])}×{r(f['h'])}"
    if art == "kreis":
        return f"Kreis {c}{fuell} Mitte ({r(f['x'])},{r(f['y'])}) r {r(f['r'])}"
    if f.get("verlauf"):
        v = f["verlauf"]
        fuell = f"{' ' if c else ''}Verlauf {_farbname(v['von'])}→{_farbname(v['nach'])}"
    if art in ("polygon", "strich", "kurve"):
        xs = [p[0] for p in f["punkte"]]
        ys = [p[1] for p in f["punkte"]]
        name = {"polygon": "Vieleck", "strich": "Strich", "kurve": "Kurve" + (" geschlossen" if f.get("geschlossen")
                                                                               else "")}[art]
        return f"{name} {c}{fuell} {len(xs)} Punkte im Bereich ({r(min(xs))},{r(min(ys))})–({r(max(xs))},{r(max(ys))})"
    if art == "bogen":
        return (f"Bogen {c} Mitte ({r(f['x'])},{r(f['y'])}) {r(f['b'])}×{r(f['h'])} ab {r(f['start'])}° "
                f"über {r(f['winkel'])}°")
    if art in ("stern", "n_eck"):
        name = f"Stern ({f['ecken']} Zacken)" if art == "stern" else f"{f['ecken']}-Eck"
        return f"{name} {c}{fuell} Mitte ({r(f['x'])},{r(f['y'])}) r {r(f['r'])}"
    if art == "herz":
        return f"Herz {c}{fuell} Mitte ({r(f['x'])},{r(f['y'])}) {r(f['b'])}×{r(f['h'])}"
    if art == "spray":
        return f"Sprühfarbe {c} Mitte ({r(f['x'])},{r(f['y'])}) r {r(f['r'])}, {f['dichte']} Tupfer"
    return f"Text „{f['text'][:60]}“ {c} {r(f['groesse'])} px bei ({r(f['x'])},{r(f['y'])})"


def bildgroesse_fuer(b: float, h: float, megapixel: float = 1.0, raster: int = 64) -> tuple[int, int]:
    """Malgröße für den Bild-Motor mit dem Seitenverhältnis b:h, rund `megapixel`, Seiten auf `raster`."""
    b, h = max(float(b), 1.0), max(float(h), 1.0)
    f = math.sqrt(megapixel * 1_000_000 / (b * h))
    bb = max(raster * 4, int(round(b * f / raster)) * raster)
    hh = max(raster * 4, int(round(h * f / raster)) * raster)
    return min(bb, 2048), min(hh, 2048)
