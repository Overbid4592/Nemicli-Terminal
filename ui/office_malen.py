"""office_malen.py – 🎨 Malen in Nemi Office: eine Leinwand mit Pinsel, Sprühdose, Radierer, Linie, Pfeil, Rechteck,
Ellipse, Stern, Herz, Vieleck, Farbeimer, Pipette, Text und Auswahl (verschieben, ausschneiden, kopieren, einfügen,
zuschneiden), Drehen, Spiegeln, Rückgängig und Zoom.

Die OfficeAI zeichnet selbst Form für Form (Werkzeuge in buero/werkzeuge.py, geprüft in buero/malen.py): Der lila
Zeiger fliegt zu jeder Form, sie wächst sichtbar auf der Leinwand. Bilder vom Bild-Motor und das Umzeichnen kommen
aus der Sitzung (bild_setzen, leinwand_export). Alles rein mit Qt – ohne numpy und Pillow.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QIODevice, QPoint, QPointF, QRect, QRectF, QSaveFile, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QIcon, QImage, QImageReader, QKeySequence,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
                           QShortcut, QTransform)
from PySide6.QtWidgets import QFileDialog, QFrame, QInputDialog, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from buero import ablage
from buero import malen as M
from buero.sicher import MAX_DATEI
from design import FARBE, schrift
from office_teile import Leiste, Statuszeile, frage_speichern

MAX_RUECK = 40                    # Rückgängig-Schritte (ganze Bilder im Speicher)
ZOOM_STUFEN = (0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
TOLERANZ = 40                     # Farbeimer: so weit darf ein Farbkanal abweichen (Kantenglättung)
FORM_MS = 90                      # KI zeichnet: Pause je Form
TEIL_MS = 28                      # … und je Wachstumsschritt einer Linie oder eines Strichs
TEILE = 6
FORM_WERKZEUGE = ("linie", "pfeil", "rechteck", "ellipse", "stern", "herz", "n_eck")
WACHSEN = ("linie", "pfeil", "strich", "polygon", "kurve", "bogen", "spray", "text")    # Formen, die sichtbar wachsen
BEREICH_WERKZEUGE = ("radieren", "verschieben", "spiegeln", "drehen", "skalieren", "umfaerben", "zuschneiden",
                     "farbe_lesen")
WERKZEUGE = (("pinsel", "🖌", "Pinsel – frei malen"), ("spruehen", "💨", "Sprühdose – weiche Farbtupfer"),
             ("radierer", "🧽", "Radierer – mit der Hintergrundfarbe"),
             ("linie", "╱", "Linie (mit Umschalt: gerade)"), ("pfeil", "➜", "Pfeil"),
             ("rechteck", "▭", "Rechteck (mit Umschalt: Quadrat)"), ("ellipse", "◯", "Ellipse (mit Umschalt: Kreis)"),
             ("stern", "★", "Stern"), ("herz", "♥", "Herz"), ("n_eck", "⬡", "Sechseck"),
             ("fuellen", "🪣", "Farbeimer – Fläche füllen"), ("pipette", "💧", "Pipette – Farbe aufnehmen"),
             ("text", "🔤", "Text schreiben"), ("auswahl", "⬚", "Auswahl – verschieben, ausschneiden, zuschneiden"))


# ---------------------------------------------------------------------------
# Das eigene Symbol: Farbpalette mit Pinsel
# ---------------------------------------------------------------------------

def mal_icon(groesse: int = 64) -> QPixmap:
    """Symbol von 🎨 Malen: cremefarbene Palette mit vier Farbklecksen und ein Pinsel quer darüber."""
    pm = QPixmap(groesse, groesse)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = groesse / 64.0
    p.scale(s, s)
    palette = QPainterPath()
    palette.moveTo(32, 6)
    palette.cubicTo(50, 6, 61, 18, 60, 31)
    palette.cubicTo(59, 42, 51, 44, 45, 42)
    palette.cubicTo(39, 40, 35, 44, 37, 50)
    palette.cubicTo(39, 56, 34, 59, 28, 58)
    palette.cubicTo(13, 56, 4, 45, 5, 30)
    palette.cubicTo(6, 16, 17, 6, 32, 6)
    loch = QPainterPath()
    loch.addEllipse(QPointF(45, 33), 3.6, 3.6)
    g = QLinearGradient(QPointF(10, 8), QPointF(54, 56))
    g.setColorAt(0, QColor("#fff7e6"))
    g.setColorAt(1, QColor("#f3d9b1"))
    p.setPen(QPen(QColor("#b07a45"), 1.6))
    p.setBrush(QBrush(g))
    p.drawPath(palette.subtracted(loch))
    for (x, y), c in zip(((19, 18), (31, 13), (14, 31), (22, 44)), ("#e53935", "#fdd835", "#1e88e5", "#43a047")):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(x, y), 5.2, 5.2)
        p.setBrush(QColor(255, 255, 255, 120))
        p.drawEllipse(QPointF(x - 1.6, y - 1.8), 1.6, 1.4)
    p.translate(40, 40)
    p.rotate(-45)
    p.setPen(QPen(QColor("#5b3a1e"), 0.8))
    p.setBrush(QColor("#8d5a2b"))
    p.drawRoundedRect(QRectF(-2.6, -2, 5.2, 24), 2.4, 2.4)                    # Stiel
    p.setBrush(QColor("#c9ccd6"))
    p.drawRect(QRectF(-3, -7, 6, 6))                                         # Zwinge
    spitze = QPainterPath()
    spitze.moveTo(-3, -7)
    spitze.cubicTo(-3.4, -12, -1, -17, 0, -19)
    spitze.cubicTo(1, -17, 3.4, -12, 3, -7)
    spitze.closeSubpath()
    p.setBrush(QColor("#ec407a"))
    p.drawPath(spitze)
    p.end()
    return pm


# ---------------------------------------------------------------------------
# Formen malen (Nutzer-Werkzeuge und KI teilen sich das)
# ---------------------------------------------------------------------------

def _glatt(punkte: list[tuple[float, float]], schliessen: bool = False) -> QPainterPath:
    """Weicher Pfad durch die Punkte (Mittelpunkte als Stützen) – oder ein Vieleck."""
    pfad = QPainterPath(QPointF(*punkte[0]))
    if schliessen:
        for q in punkte[1:]:
            pfad.lineTo(QPointF(*q))
        pfad.closeSubpath()
        return pfad
    if len(punkte) == 2:
        pfad.lineTo(QPointF(*punkte[1]))
        return pfad
    for i in range(1, len(punkte) - 1):
        a, b = QPointF(*punkte[i]), QPointF(*punkte[i + 1])
        pfad.quadTo(a, (a + b) / 2)
    pfad.lineTo(QPointF(*punkte[-1]))
    return pfad


def _pfeilspitze(p: QPainter, a: QPointF, b: QPointF, dicke: float, farbe: QColor) -> None:
    import math
    w = math.atan2(b.y() - a.y(), b.x() - a.x())
    lang = max(12.0, dicke * 4)
    breit = math.radians(26)
    l = QPointF(b.x() - lang * math.cos(w - breit), b.y() - lang * math.sin(w - breit))
    r = QPointF(b.x() - lang * math.cos(w + breit), b.y() - lang * math.sin(w + breit))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(farbe)
    p.drawPolygon(QPolygonF([b, l, r]))


def _kurve(punkte: list[tuple[float, float]], geschlossen: bool = False) -> QPainterPath:
    """Weiche Kurve genau durch alle Punkte (Catmull-Rom als kubische Bézier-Stücke)."""
    pts = [QPointF(*q) for q in punkte]
    n = len(pts)
    pfad = QPainterPath(pts[0])
    if n == 2:
        pfad.lineTo(pts[1])
        return pfad
    stuecke = n if geschlossen else n - 1
    for i in range(stuecke):
        p0 = pts[(i - 1) % n] if geschlossen or i > 0 else pts[0]
        p1, p2 = pts[i % n], pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if geschlossen or i + 2 < n else pts[-1]
        pfad.cubicTo(p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6, p2)
    if geschlossen:
        pfad.closeSubpath()
    return pfad


def _stern(f: dict) -> QPainterPath:
    import math
    n, innen = int(f["ecken"]), float(f.get("innen", 0.45))
    punkte = []
    for i in range(2 * n):
        r = f["r"] if i % 2 == 0 else f["r"] * innen
        w = -math.pi / 2 + i * math.pi / n
        punkte.append(QPointF(f["x"] + r * math.cos(w), f["y"] + r * math.sin(w)))
    pfad = QPainterPath()
    pfad.addPolygon(QPolygonF(punkte))
    pfad.closeSubpath()
    return pfad


def _n_eck(f: dict) -> QPainterPath:
    import math
    n = int(f["ecken"])
    punkte = [QPointF(f["x"] + f["r"] * math.cos(-math.pi / 2 + i * 2 * math.pi / n),
                      f["y"] + f["r"] * math.sin(-math.pi / 2 + i * 2 * math.pi / n)) for i in range(n)]
    pfad = QPainterPath()
    pfad.addPolygon(QPolygonF(punkte))
    pfad.closeSubpath()
    return pfad


def _herz(f: dict) -> QPainterPath:
    x, y, b, h = f["x"], f["y"], f["b"], f["h"]
    links, rechts, oben, unten = x - b / 2, x + b / 2, y - h / 2, y + h / 2
    kerbe = oben + 0.28 * h
    pfad = QPainterPath(QPointF(x, kerbe))
    pfad.cubicTo(QPointF(x, oben), QPointF(links, oben), QPointF(links, kerbe))
    pfad.cubicTo(QPointF(links, oben + 0.62 * h), QPointF(x, oben + 0.78 * h), QPointF(x, unten))
    pfad.cubicTo(QPointF(x, oben + 0.78 * h), QPointF(rechts, oben + 0.62 * h), QPointF(rechts, kerbe))
    pfad.cubicTo(QPointF(rechts, oben), QPointF(x, oben), QPointF(x, kerbe))
    pfad.closeSubpath()
    return pfad


def _flaeche(f: dict) -> QPainterPath | None:
    """Umriss einer Flächen-Form (für Füllung, Verlauf und Rand)."""
    art = f["art"]
    pfad = QPainterPath()
    if art == "rechteck":
        pfad.addRoundedRect(QRectF(f["x"], f["y"], f["b"], f["h"]), f.get("rund", 0), f.get("rund", 0))
    elif art == "ellipse":
        pfad.addEllipse(QRectF(f["x"], f["y"], f["b"], f["h"]))
    elif art == "kreis":
        pfad.addEllipse(QPointF(f["x"], f["y"]), f["r"], f["r"])
    elif art == "polygon":
        pfad = _glatt(f["punkte"], schliessen=True)
    elif art == "kurve" and f.get("geschlossen"):
        pfad = _kurve(f["punkte"], geschlossen=True)
    elif art == "stern":
        pfad = _stern(f)
    elif art == "n_eck":
        pfad = _n_eck(f)
    elif art == "herz":
        pfad = _herz(f)
    else:
        return None
    return pfad


def _verlauf_pinsel(v: dict, r: QRectF) -> QBrush:
    import math
    c = r.center()
    if v["art"] == "radial":
        g = QRadialGradient(c, max(r.width(), r.height()) / 2)
    else:
        w = math.radians(float(v.get("winkel", 90)))
        d = QPointF(math.cos(w) * r.width() / 2, math.sin(w) * r.height() / 2)
        g = QLinearGradient(c - d, c + d)
    g.setColorAt(0, QColor(v["von"]))
    g.setColorAt(1, QColor(v["nach"]))
    return QBrush(g)


def _mitte(f: dict) -> QPointF:
    if f["art"] in ("rechteck", "ellipse"):
        return QPointF(f["x"] + f["b"] / 2, f["y"] + f["h"] / 2)
    return QPointF(f["x"], f["y"])


def _spray(p: QPainter, f: dict, anteil: float = 1.0) -> None:
    """Sprühfarbe: Tupfer zufällig im Kreis, dichter zur Mitte – mit festem Zufall je Form (gleich beim Neumalen)."""
    import math
    import random
    zufall = random.Random(int(f["x"] * 7919 + f["y"] * 104729 + f["r"] * 31 + f["dichte"]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(f["farbe"] or "#1f1f24"))
    d = float(f.get("dicke", 2))
    for _ in range(max(1, int(f["dichte"] * anteil))):
        w, r = zufall.uniform(0, 2 * math.pi), f["r"] * math.sqrt(zufall.random()) * zufall.uniform(0.6, 1.0)
        p.drawEllipse(QPointF(f["x"] + r * math.cos(w), f["y"] + r * math.sin(w)), d / 2, d / 2)


_STIL = {"gestrichelt": Qt.PenStyle.DashLine, "gepunktet": Qt.PenStyle.DotLine}


def _pinsel_strich(p: QPainter, pfad: QPainterPath, f: dict, farbe: QColor) -> None:
    """Strich oder offene Kurve mit Pinselart: normal, bleistift, marker, kalligrafie, weich."""
    import math
    d, art = float(f.get("dicke", 3)), f.get("pinsel", "normal")
    stil = _STIL.get(f.get("linienstil", ""), Qt.PenStyle.SolidLine)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if art == "bleistift":
        p.setOpacity(p.opacity() * 0.9)
        p.setPen(QPen(farbe, max(1.0, min(d, 2.5)), stil, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
        p.drawPath(pfad)
    elif art == "marker":
        p.setOpacity(p.opacity() * 0.55)
        p.setPen(QPen(farbe, d * 1.2, stil, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.BevelJoin))
        p.drawPath(pfad)
    elif art == "kalligrafie":                       # schräge Feder: viele dünne, versetzte Züge
        schritte = max(3, int(d))
        p.setPen(QPen(farbe, 1.6, stil, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for i in range(schritte):
            k = (i / (schritte - 1) - 0.5) * d
            p.drawPath(pfad.translated(k * math.cos(math.radians(45)), -k * math.sin(math.radians(45))))
    elif art == "weich":                             # weicher Rand: breit und zart, dann schmal und kräftiger
        basis = p.opacity()
        for breite, deck in ((1.9, 0.10), (1.5, 0.16), (1.1, 0.28), (0.7, 0.5)):
            p.setOpacity(basis * deck)
            p.setPen(QPen(farbe, d * breite, stil, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(pfad)
    else:
        p.setPen(QPen(farbe, d, stil, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(pfad)


def form_malen(p: QPainter, f: dict, anteil: float = 1.0) -> None:
    """Eine geprüfte Form (buero.malen.form_pruefen) malen; `anteil` < 1 zeigt sie erst teilweise (sie wächst)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setOpacity(float(f.get("deckkraft", 1.0)))
    art = f["art"]
    if f.get("drehung"):
        m = _mitte(f)
        p.translate(m)
        p.rotate(float(f["drehung"]))
        p.translate(-m)
    linie = QColor(f["farbe"]) if f.get("farbe") else None
    stil = _STIL.get(f.get("linienstil", ""), Qt.PenStyle.SolidLine)
    stift = QPen(linie, float(f.get("dicke", 3)), stil, Qt.PenCapStyle.RoundCap,
                 Qt.PenJoinStyle.RoundJoin) if linie is not None else Qt.PenStyle.NoPen
    p.setPen(stift)
    p.setBrush(QColor(f["fuellung"]) if f.get("fuellung") else Qt.BrushStyle.NoBrush)
    if art in ("linie", "pfeil"):
        a, b = QPointF(*f["von"]), QPointF(*f["nach"])
        e = a + (b - a) * anteil
        p.drawLine(a, e)
        if art == "pfeil" and anteil >= 1 and linie is not None:
            _pfeilspitze(p, a, b, float(f.get("dicke", 3)), linie)
    elif art in ("strich", "kurve") and not f.get("geschlossen"):
        punkte = f["punkte"]
        n = max(2, int(round(len(punkte) * anteil)))
        pfad = _kurve(punkte[:n]) if art == "kurve" else _glatt(punkte[:n])
        _pinsel_strich(p, pfad, f, linie or QColor("#1f1f24"))
    elif art in ("polygon", "kurve") and anteil < 1:            # Umriss wächst, gefüllt wird am Ende
        punkte = f["punkte"]
        n = max(2, int(round(len(punkte) * anteil)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if linie is None:
            p.setPen(QPen(QColor(f.get("fuellung") or "#1f1f24"), 2.0, stil))
        p.drawPath(_kurve(punkte[:n]) if art == "kurve" else _glatt(punkte[:n]))
    elif art == "bogen":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(f["x"] - f["b"] / 2, f["y"] - f["h"] / 2, f["b"], f["h"]), int(f["start"] * 16),
                  int(f["winkel"] * 16 * anteil))
    elif art == "spray":
        _spray(p, f, anteil)
    elif art == "text":
        font = QFont(schrift(10))
        font.setPixelSize(max(6, int(f["groesse"])))
        font.setBold(bool(f.get("fett")))
        p.setFont(font)
        p.setPen(linie or QColor("#1f1f24"))
        zeichen = f["text"][:max(1, int(round(len(f["text"]) * anteil)))]
        p.drawText(QRectF(f["x"], f["y"], 20000, f["groesse"] * 1.6),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, zeichen)
    elif (pfad := _flaeche(f)) is not None:
        if f.get("verlauf"):
            p.setBrush(_verlauf_pinsel(f["verlauf"], pfad.boundingRect()))
        p.drawPath(pfad)
    p.restore()


def farbe_ersetzen(bild: QImage, r: QRect, von: QColor, nach: QColor, toleranz: int) -> tuple[QImage, int]:
    """Alle Bildpunkte im Bereich, die `von` bis auf `toleranz` je Kanal gleichen, werden `nach` (reines Python).
    Gibt das neue Bild und die Zahl der geänderten Punkte zurück."""
    img = bild.convertToFormat(QImage.Format.Format_ARGB32)
    b = img.width()
    px = memoryview(img.bits()).cast("I")
    vr, vg, vb = von.red(), von.green(), von.blue()
    neu = nach.rgba() & 0xFFFFFFFF
    tol = max(0, int(toleranz))
    zahl = 0
    for y in range(r.top(), r.top() + r.height()):
        zeile = y * b
        for i in range(zeile + r.left(), zeile + r.left() + r.width()):
            v = px[i]
            if (abs(((v >> 16) & 255) - vr) <= tol and abs(((v >> 8) & 255) - vg) <= tol
                    and abs((v & 255) - vb) <= tol):
                px[i] = neu
                zahl += 1
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), zahl


def flaeche_fuellen(bild: QImage, x: int, y: int, farbe: QColor, toleranz: int = TOLERANZ) -> QImage | None:
    """Farbeimer: zusammenhängende Fläche ähnlicher Farbe ab (x, y) füllen (Zeilen-Verfahren, reines Python).
    Gibt das neue Bild zurück – None, wenn der Punkt außerhalb liegt oder schon diese Farbe hat."""
    b, h = bild.width(), bild.height()
    if not (0 <= x < b and 0 <= y < h):
        return None
    img = bild.convertToFormat(QImage.Format.Format_ARGB32)
    px = memoryview(img.bits()).cast("I")
    ziel = px[y * b + x]
    neu = farbe.rgba() & 0xFFFFFFFF
    if ziel == neu:
        return None
    za, zr, zg, zb = ziel >> 24, (ziel >> 16) & 255, (ziel >> 8) & 255, ziel & 255
    tol = max(0, int(toleranz))

    def passt(v: int) -> bool:
        return v == ziel or (abs((v >> 24) - za) <= tol and abs(((v >> 16) & 255) - zr) <= tol
                             and abs(((v >> 8) & 255) - zg) <= tol and abs((v & 255) - zb) <= tol)
    besucht = bytearray(b * h)
    stapel = [(x, y)]
    while stapel:
        sx, sy = stapel.pop()
        zeile = sy * b
        if besucht[zeile + sx] or not passt(px[zeile + sx]):
            continue
        links = sx
        while links > 0 and not besucht[zeile + links - 1] and passt(px[zeile + links - 1]):
            links -= 1
        rechts = sx
        while rechts < b - 1 and not besucht[zeile + rechts + 1] and passt(px[zeile + rechts + 1]):
            rechts += 1
        for i in range(zeile + links, zeile + rechts + 1):
            px[i] = neu
            besucht[i] = 1
        for ny in (sy - 1, sy + 1):
            if 0 <= ny < h:
                nz = ny * b
                drin = False
                for nx in range(links, rechts + 1):
                    if not besucht[nz + nx] and passt(px[nz + nx]):
                        if not drin:
                            stapel.append((nx, ny))
                            drin = True
                    else:
                        drin = False
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def bild_lesen(pfad: str) -> QImage:
    """Bild sicher lesen: nur bekannte Endung, Größe begrenzt, Seiten höchstens buero.malen.MAX_SEITE."""
    p = Path(pfad)
    if p.suffix.lower() not in M.BILD_OEFFNEN or not p.is_file():
        raise ValueError("kein lesbares Bild (PNG, JPEG, WebP oder BMP)")
    if p.stat().st_size > MAX_DATEI:
        raise ValueError(f"{p.name} ist zu groß")
    leser = QImageReader(str(p))
    leser.setAutoTransform(True)
    g = leser.size()
    if g.isValid() and (g.width() > M.MAX_SEITE or g.height() > M.MAX_SEITE):
        raise ValueError(f"{p.name} ist größer als {M.MAX_SEITE}×{M.MAX_SEITE} Bildpunkte")
    bild = leser.read()
    if bild.isNull():
        raise ValueError(f"{p.name} ließ sich nicht lesen: {leser.errorString()}")
    return bild.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


# ---------------------------------------------------------------------------
# Die Leinwand
# ---------------------------------------------------------------------------

class Leinwand(QWidget):
    veraendert = Signal()
    position = Signal(str)
    farbe_aufgenommen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hintergrund = QColor("#ffffff")
        self.bild = QImage(*M.STANDARD_GROESSE, QImage.Format.Format_ARGB32_Premultiplied)
        self.bild.fill(self.hintergrund)
        self.zoom = 1.0
        self.werkzeug = "pinsel"
        self.farbe = QColor("#1f1f24")
        self.dicke = 6
        self.gefuellt = False
        self.rueck: list[tuple[QImage, list[str]]] = []
        self.vor: list[tuple[QImage, list[str]]] = []
        self.elemente: list[str] = []
        self.vorschau: tuple[dict, float] | None = None       # Form, die gerade wächst (nicht im Bild)
        self.auswahl: QRect | None = None
        self._schwebend: QImage | None = None                 # verschobener oder eingefügter Ausschnitt
        self._schwebend_pos = QPoint()
        self._zug: dict | None = None
        self._groesse_anpassen()

    # -- Größe und Zoom --------------------------------------------------------------------
    def _groesse_anpassen(self) -> None:
        self.setFixedSize(QSize(max(1, int(self.bild.width() * self.zoom)), max(1, int(self.bild.height() * self.zoom))))
        self.update()

    def zoom_setzen(self, z: float) -> None:
        self.zoom = min(max(z, ZOOM_STUFEN[0]), ZOOM_STUFEN[-1])
        self._groesse_anpassen()

    def bildpunkt(self, pos) -> QPointF:
        return QPointF(pos.x() / self.zoom, pos.y() / self.zoom)

    def im_fenster(self, x: float, y: float, fenster: QWidget) -> QPoint:
        return self.mapTo(fenster, QPoint(int(x * self.zoom), int(y * self.zoom)))

    # -- Verlauf ---------------------------------------------------------------------------
    def merken(self) -> None:
        self.schwebend_ablegen()
        self.rueck.append((self.bild.copy(), list(self.elemente)))
        del self.rueck[:-MAX_RUECK]
        self.vor.clear()

    def rueckgaengig(self) -> bool:
        self.schwebend_ablegen()
        if not self.rueck:
            return False
        self.vor.append((self.bild, self.elemente))
        self.bild, self.elemente = self.rueck.pop()
        self._nach_wechsel()
        return True

    def wiederholen(self) -> bool:
        if not self.vor:
            return False
        self.rueck.append((self.bild, self.elemente))
        self.bild, self.elemente = self.vor.pop()
        self._nach_wechsel()
        return True

    def _nach_wechsel(self) -> None:
        self.auswahl = None
        self._groesse_anpassen()
        self.veraendert.emit()

    def eintrag(self, text: str) -> None:
        self.elemente.append(text)
        del self.elemente[:-M.MAX_ELEMENTE]

    # -- Ändern (auch für die KI) ----------------------------------------------------------
    def neu(self, b: int, h: int, farbe: QColor | None = None) -> None:
        self.hintergrund = QColor(farbe or "#ffffff")
        self.bild = QImage(b, h, QImage.Format.Format_ARGB32_Premultiplied)
        self.bild.fill(self.hintergrund)
        self.elemente, self.rueck, self.vor, self.auswahl = [], [], [], None
        self._schwebend = None
        self._groesse_anpassen()

    def form(self, f: dict) -> None:
        p = QPainter(self.bild)
        form_malen(p, f)
        p.end()
        self.update()

    def groesse_aendern(self, b: int, h: int) -> None:
        neu = QImage(b, h, QImage.Format.Format_ARGB32_Premultiplied)
        neu.fill(self.hintergrund)
        p = QPainter(neu)
        p.drawImage(0, 0, self.bild)
        p.end()
        self.bild = neu
        self.auswahl = None
        self._groesse_anpassen()

    def hintergrund_setzen(self, farbe: QColor) -> None:
        self.hintergrund = QColor(farbe)
        self.bild.fill(self.hintergrund)
        self.elemente = [f"Hintergrund {farbe.name()}"]
        self.update()

    def fuellen(self, x: int, y: int, farbe: QColor) -> bool:
        neu = flaeche_fuellen(self.bild, x, y, farbe)
        if neu is None:
            return False
        self.bild = neu
        self.update()
        return True

    def bild_setzen(self, bild: QImage, ziel: QRectF | None = None, ersetzen: bool = False) -> QRectF:
        """Bild einsetzen: `ersetzen` = die ganze Leinwand (auf ihre Größe gestreckt); sonst passend in `ziel`
        (ohne: die ganze Leinwand), Seitenverhältnis bleibt, mittig."""
        voll = QRectF(0, 0, self.bild.width(), self.bild.height())
        if ersetzen:
            r = voll
        else:
            rahmen = ziel or voll
            f = min(rahmen.width() / bild.width(), rahmen.height() / bild.height())
            r = QRectF(0, 0, bild.width() * f, bild.height() * f)
            r.moveCenter(rahmen.center())
        p = QPainter(self.bild)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(r, bild)
        p.end()
        self.update()
        return r

    # -- Auswahl ---------------------------------------------------------------------------
    def schwebend_ablegen(self) -> None:
        if self._schwebend is None:
            return
        p = QPainter(self.bild)
        p.drawImage(self._schwebend_pos, self._schwebend)
        p.end()
        self.auswahl = QRect(self._schwebend_pos, self._schwebend.size())
        self._schwebend = None
        self.update()
        self.veraendert.emit()

    def kopieren(self) -> bool:
        if self.auswahl is None:
            return False
        QGuiApplication.clipboard().setImage(self._schwebend if self._schwebend is not None
                                             else self.bild.copy(self.auswahl))
        return True

    def auswahl_leeren(self, ausschneiden: bool = False) -> bool:
        if self.auswahl is None:
            return False
        if ausschneiden:
            self.kopieren()
        if self._schwebend is not None:
            self._schwebend = None
        else:
            self.merken()
            p = QPainter(self.bild)
            p.fillRect(self.auswahl, self.hintergrund)
            p.end()
        self.eintrag(f"Bereich {self._rechteck_text(self.auswahl)} {'ausgeschnitten' if ausschneiden else 'gelöscht'}")
        self.auswahl = None
        self.update()
        self.veraendert.emit()
        return True

    def einfuegen(self) -> bool:
        bild = QGuiApplication.clipboard().image()
        if bild.isNull():
            return False
        if bild.width() > M.MAX_SEITE or bild.height() > M.MAX_SEITE:
            return False
        self.merken()
        self._schwebend = bild.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._schwebend_pos = QPoint(0, 0)
        self.auswahl = QRect(self._schwebend_pos, bild.size())
        self.eintrag(f"Bild aus der Zwischenablage eingefügt ({bild.width()}×{bild.height()})")
        self.werkzeug = "auswahl"
        self.update()
        self.veraendert.emit()
        return True

    def zuschneiden(self) -> bool:
        self.schwebend_ablegen()
        if self.auswahl is None:
            return False
        r = self.auswahl.intersected(self.bild.rect())
        if r.width() < M.MIN_SEITE or r.height() < M.MIN_SEITE:
            return False
        self.merken()
        self.bild = self.bild.copy(r)
        self.eintrag(f"Auf {r.width()}×{r.height()} zugeschnitten")
        self.auswahl = None
        self._groesse_anpassen()
        self.veraendert.emit()
        return True

    def bereich_werkzeug(self, name: str, g: dict) -> str | None:
        """Ein Bereichs-Werkzeug (geprüft in buero.malen.werkzeug_pruefen) ausführen – Text für die Formenliste,
        None = nichts geändert. Das Loch beim Verschieben, Drehen und Skalieren bekommt die Hintergrundfarbe."""
        self.schwebend_ablegen()
        r = QRect(*g["bereich"]) if g.get("bereich") else None
        voll = r is None
        if r is None:
            r = self.bild.rect()
        if name == "radieren":
            if "strich" in g:
                f = dict(g["strich"], farbe=self.hintergrund.name())
                self.form(f)
                xs, ys = [q[0] for q in f["punkte"]], [q[1] for q in f["punkte"]]
                return (f"Radiert {int(f['dicke'])} px im Bereich ({int(min(xs))},{int(min(ys))})–"
                        f"({int(max(xs))},{int(max(ys))})")
            p = QPainter(self.bild)
            p.fillRect(r, self.hintergrund)
            p.end()
            return f"Bereich {self._rechteck_text(r)} radiert"
        if name == "zuschneiden":
            if r.width() < M.MIN_SEITE or r.height() < M.MIN_SEITE:
                return None
            self.bild = self.bild.copy(r)
            self.auswahl = None
            self._groesse_anpassen()
            return f"Auf {r.width()}×{r.height()} zugeschnitten (ab {r.x()},{r.y()})"
        if name == "umfaerben":
            self.bild, zahl = farbe_ersetzen(self.bild, r, QColor(g["von"]), QColor(g["nach"]), g["toleranz"])
            if not zahl:
                return None
            self.update()
            wo = "ganze Leinwand" if voll else self._rechteck_text(r)
            return f"{g['von']} → {g['nach']} umgefärbt ({zahl} Bildpunkte, {wo})"
        if name == "drehen" and voll:
            self.bild = self.bild.transformed(QTransform().rotate(float(g["winkel"])))
            self._groesse_anpassen()
            return f"Leinwand um {int(g['winkel'])}° gedreht, jetzt {self.bild.width()}×{self.bild.height()}"
        stueck = self.bild.copy(r)
        if name == "spiegeln":
            t = QTransform().scale(-1, 1) if g["richtung"] == "waagerecht" else QTransform().scale(1, -1)
            neu, ziel = stueck.transformed(t), r.topLeft()
            text = f"{'Leinwand' if voll else 'Bereich ' + self._rechteck_text(r)} {g['richtung']} gespiegelt"
        elif name == "drehen":
            neu = stueck.transformed(QTransform().rotate(float(g["winkel"])), Qt.TransformationMode.SmoothTransformation)
            ziel = r.center() - QPoint(neu.width() // 2, neu.height() // 2)
            text = f"Bereich {self._rechteck_text(r)} um {int(g['winkel'])}° gedreht"
        elif name == "skalieren":
            neu = stueck.scaled(g["neu_b"], g["neu_h"], Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            ziel = r.topLeft()
            text = f"Bereich {self._rechteck_text(r)} auf {g['neu_b']}×{g['neu_h']} skaliert"
        else:                                                                       # verschieben
            neu, ziel = stueck, QPoint(int(g["nach"][0]), int(g["nach"][1]))
            text = (f"Bereich {self._rechteck_text(r)} nach ({ziel.x()},{ziel.y()}) "
                    + ("kopiert" if g.get("kopieren") else "verschoben"))
        p = QPainter(self.bild)
        if not (name == "verschieben" and g.get("kopieren")):
            p.fillRect(r, self.hintergrund)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(ziel, neu)
        p.end()
        self.update()
        return text

    def farbe_an(self, x: int, y: int) -> str:
        c = self.bild.pixelColor(x, y)
        return QColor(c.red(), c.green(), c.blue()).name()

    @staticmethod
    def _rechteck_text(r: QRect) -> str:
        return f"({r.x()},{r.y()}) {r.width()}×{r.height()}"

    # -- Maus ------------------------------------------------------------------------------
    def _stift_form(self, art: str, a: QPointF, b: QPointF, gerade: bool) -> dict:
        farbe = self.farbe.name()
        if art in ("linie", "pfeil"):
            if gerade:                                     # waagerecht, senkrecht oder 45°
                dx, dy = b.x() - a.x(), b.y() - a.y()
                if abs(dx) > 2 * abs(dy):
                    b = QPointF(b.x(), a.y())
                elif abs(dy) > 2 * abs(dx):
                    b = QPointF(a.x(), b.y())
                else:
                    d = max(abs(dx), abs(dy))
                    b = QPointF(a.x() + (d if dx >= 0 else -d), a.y() + (d if dy >= 0 else -d))
            return {"art": art, "farbe": farbe, "dicke": float(self.dicke), "deckkraft": 1.0,
                    "von": (a.x(), a.y()), "nach": (b.x(), b.y())}
        x, y = min(a.x(), b.x()), min(a.y(), b.y())
        w, h = abs(b.x() - a.x()), abs(b.y() - a.y())
        if gerade:
            w = h = max(w, h)
        basis = {"art": art, "farbe": farbe, "fuellung": farbe if self.gefuellt else "", "dicke": float(self.dicke),
                 "deckkraft": 1.0}
        if art in ("stern", "n_eck"):
            return {**basis, "x": x + w / 2, "y": y + h / 2, "r": max(1.0, min(w, h) / 2),
                    "ecken": 5 if art == "stern" else 6, "innen": 0.45}
        if art == "herz":
            return {**basis, "x": x + w / 2, "y": y + h / 2, "b": max(2.0, w), "h": max(2.0, h)}
        return {**basis, "x": x, "y": y, "b": max(1.0, w), "h": max(1.0, h), "rund": 0}

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        q = self.bildpunkt(e.position())
        w = self.werkzeug
        if w != "auswahl":
            self.schwebend_ablegen()
            self.auswahl = None
        if w in ("pinsel", "radierer"):
            self.merken()
            self._zug = {"art": w, "punkte": [(q.x(), q.y())]}
            self._strich_stueck(q, q)
        elif w == "spruehen":
            self.merken()
            self._zug = {"art": w, "punkte": [(q.x(), q.y())]}
            self._spruehen(q)
        elif w in FORM_WERKZEUGE:
            self._zug = {"art": w, "start": q}
        elif w == "fuellen":
            self.merken()
            if self.fuellen(int(q.x()), int(q.y()), self.farbe):
                self.eintrag(f"Fläche bei ({int(q.x())},{int(q.y())}) mit {self.farbe.name()} gefüllt")
                self.veraendert.emit()
            else:
                self.rueck.pop()
        elif w == "pipette":
            if self.bild.rect().contains(int(q.x()), int(q.y())):
                c = self.bild.pixelColor(int(q.x()), int(q.y()))
                self.farbe = QColor(c.red(), c.green(), c.blue())
                self.farbe_aufgenommen.emit(self.farbe.name())
        elif w == "text":
            self._text_setzen(q)
        elif w == "auswahl":
            punkt = q.toPoint()
            if self.auswahl is not None and self.auswahl.contains(punkt):
                if self._schwebend is None:                # herausheben, das Loch bekommt die Hintergrundfarbe
                    self.merken()
                    self._schwebend = self.bild.copy(self.auswahl)
                    self._schwebend_pos = self.auswahl.topLeft()
                    p = QPainter(self.bild)
                    p.fillRect(self.auswahl, self.hintergrund)
                    p.end()
                self._zug = {"art": "verschieben", "griff": punkt - self._schwebend_pos}
            else:
                self.schwebend_ablegen()
                self._zug = {"art": "auswahl", "start": punkt}
                self.auswahl = None
        self.update()

    def _spruehen(self, q: QPointF) -> None:
        f = {"art": "spray", "x": q.x(), "y": q.y(), "r": max(6.0, self.dicke * 3.0), "dichte": 24,
             "dicke": max(1.0, self.dicke / 4), "farbe": self.farbe.name(), "deckkraft": 1.0}
        self.form(f)

    def _strich_stueck(self, a: QPointF, b: QPointF) -> None:
        p = QPainter(self.bild)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        farbe = self.hintergrund if self._zug and self._zug["art"] == "radierer" else self.farbe
        p.setPen(QPen(farbe, self.dicke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(a, b)
        p.end()

    def mouseMoveEvent(self, e):
        q = self.bildpunkt(e.position())
        self.position.emit(f"{int(q.x())}, {int(q.y())}")
        z = self._zug
        if z is None:
            return
        gerade = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if z["art"] in ("pinsel", "radierer"):
            letzter = QPointF(*z["punkte"][-1])
            self._strich_stueck(letzter, q)
            z["punkte"].append((q.x(), q.y()))
        elif z["art"] == "spruehen":
            self._spruehen(q)
            z["punkte"].append((q.x(), q.y()))
        elif z["art"] in FORM_WERKZEUGE:
            self.vorschau = (self._stift_form(z["art"], z["start"], q, gerade), 1.0)
        elif z["art"] == "auswahl":
            self.auswahl = QRect(z["start"], q.toPoint()).normalized().intersected(self.bild.rect())
        elif z["art"] == "verschieben":
            self._schwebend_pos = q.toPoint() - z["griff"]
            self.auswahl = QRect(self._schwebend_pos, self._schwebend.size())
        self.update()

    def mouseReleaseEvent(self, e):
        z, self._zug = self._zug, None
        if z is None:
            return
        q = self.bildpunkt(e.position())
        gerade = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if z["art"] in ("pinsel", "radierer", "spruehen"):
            pk = z["punkte"]
            xs, ys = [p[0] for p in pk], [p[1] for p in pk]
            name = {"pinsel": "Pinselstrich", "radierer": "Radiert", "spruehen": "Sprühfarbe"}[z["art"]]
            self.eintrag(f"{name} {self.farbe.name() if z['art'] != 'radierer' else ''} {self.dicke} px im Bereich "
                         f"({int(min(xs))},{int(min(ys))})–({int(max(xs))},{int(max(ys))})".replace("  ", " "))
            self.veraendert.emit()
        elif z["art"] in FORM_WERKZEUGE:
            f = self._stift_form(z["art"], z["start"], q, gerade)
            self.vorschau = None
            if (z["start"] - q).manhattanLength() >= 2:
                self.merken()
                self.form(f)
                self.eintrag(M.beschreiben(f))
                self.veraendert.emit()
        elif z["art"] == "auswahl" and self.auswahl is not None and self.auswahl.width() < 2:
            self.auswahl = None
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            stufe = next((i for i, s in enumerate(ZOOM_STUFEN) if s >= self.zoom - 1e-6), 7)
            stufe = min(len(ZOOM_STUFEN) - 1, stufe + 1) if e.angleDelta().y() > 0 else max(0, stufe - 1)
            self.zoom_setzen(ZOOM_STUFEN[stufe])
            e.accept()
            return
        super().wheelEvent(e)

    def _text_setzen(self, q: QPointF) -> None:
        text, ok = QInputDialog.getText(self, "🔤 Text", "Text auf die Leinwand:")
        text = " ".join(text.split())[:M.MAX_TEXT]
        if not ok or not text:
            return
        f = {"art": "text", "farbe": self.farbe.name(), "dicke": 1.0, "deckkraft": 1.0, "x": q.x(), "y": q.y(),
             "text": text, "groesse": float(max(12, self.dicke * 4)), "fett": False}
        self.merken()
        self.form(f)
        self.eintrag(M.beschreiben(f))
        self.veraendert.emit()

    # -- Zeichnen --------------------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.scale(self.zoom, self.zoom)
        if self.zoom < 1:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(0, 0, self.bild)
        if self._schwebend is not None:
            p.drawImage(self._schwebend_pos, self._schwebend)
        if self.vorschau is not None:
            form_malen(p, *self.vorschau)
        if self.auswahl is not None:
            stift = QPen(QColor("#8b5cf6"), 1.5 / self.zoom, Qt.PenStyle.DashLine)
            p.setPen(stift)
            p.setBrush(QColor(139, 92, 246, 28))
            p.drawRect(QRectF(self.auswahl))


# ---------------------------------------------------------------------------
# Die Seite im Office-Fenster
# ---------------------------------------------------------------------------

class MalSeite(QWidget):
    titel_geaendert = Signal(str)
    gespeichert = Signal(str)
    bild_wunsch = Signal(str)

    app = "malen"

    def __init__(self, ordner: str, parent=None):
        super().__init__(parent)
        self.ordner = ordner
        self.pfad: str | None = None
        self._veraendert = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.leiste = Leiste()
        v.addWidget(self.leiste)
        self.leinwand = Leinwand()
        self.leinwand.veraendert.connect(self._geaendert)
        self.leinwand.position.connect(self._position)
        self.leinwand.farbe_aufgenommen.connect(self._farbe_gesetzt)
        self.flaeche = QScrollArea()
        self.flaeche.setObjectName("malflaeche")
        self.flaeche.setWidget(self.leinwand)
        self.flaeche.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.flaeche.setFrameShape(QFrame.Shape.NoFrame)
        self.flaeche.setStyleSheet(f"QScrollArea#malflaeche {{ background: {FARBE['grund']}; }}"
                                   f"QScrollArea#malflaeche > QWidget > QWidget {{ background: {FARBE['grund']}; }}")
        v.addWidget(self.flaeche, 1)
        self.status = Statuszeile()
        v.addWidget(self.status)
        self._knoepfe()
        for kurz, fn in (("Ctrl+Z", self.rueckgaengig), ("Ctrl+Y", self.wiederholen), ("Ctrl+S", self.speichern),
                         ("Ctrl+C", self.leinwand.kopieren), ("Ctrl+X", lambda: self.leinwand.auswahl_leeren(True)),
                         ("Ctrl+V", self._einfuegen), ("Delete", self.leinwand.auswahl_leeren),
                         ("Ctrl++", lambda: self._zoom(+1)), ("Ctrl+-", lambda: self._zoom(-1)),
                         ("Ctrl+0", self.einpassen)):
            QShortcut(QKeySequence(kurz), self, fn, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._position("")
        QTimer.singleShot(0, self.einpassen)

    # -- Leiste ----------------------------------------------------------------------------
    def _knoepfe(self) -> None:
        L = self.leiste
        L.knopf("💾", "Speichern (Strg+S)", self.speichern)
        L.knopf("Speichern unter …", "Als PNG, JPEG, WebP oder BMP speichern", lambda: self.speichern(True), breit=True)
        L.knopf("📄 Neu", "Neue Leinwand (Größe wählen)", self.neue_leinwand, breit=True)
        L.trenner()
        L.knopf("↶", "Rückgängig (Strg+Z)", self.rueckgaengig)
        L.knopf("↷", "Wiederholen (Strg+Y)", self.wiederholen)
        L.trenner()
        self.werkzeug_knoepfe = {}
        for name, zeichen, tipp in WERKZEUGE:
            k = L.knopf(zeichen, tipp, lambda _an, n=name: self.werkzeug_waehlen(n), umschalt=True)
            self.werkzeug_knoepfe[name] = k
        self.werkzeug_knoepfe["pinsel"].setChecked(True)
        L.trenner()
        self.farbknopf = L.farbknopf("Farbe", "Mal- und Schriftfarbe",
                                     lambda c: self._farbe_gesetzt(c or "#1f1f24"), "Farbe", "Schwarz")
        self.k_gefuellt = L.knopf("◼ gefüllt", "Rechteck und Ellipse gefüllt malen", self._gefuellt, umschalt=True,
                                  breit=True)
        self.dicke = QSpinBox()
        self.dicke.setRange(1, M.MAX_DICKE)
        self.dicke.setValue(self.leinwand.dicke)
        self.dicke.setSuffix(" px")
        self.dicke.setToolTip("Strichstärke (Text: viermal so groß)")
        self.dicke.setStyleSheet(f"QSpinBox {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px "
                                 f"solid {FARBE['rand']}; border-radius: 7px; padding: 2px 6px; }}")
        self.dicke.valueChanged.connect(lambda n: setattr(self.leinwand, "dicke", n))
        L.dazu(self.dicke)
        L.trenner()
        L.knopf("✂ Zuschneiden", "Leinwand auf die Auswahl zuschneiden", self._zuschneiden, breit=True)
        L.knopf("⟳", "Drehen um 90° (Auswahl oder ganze Leinwand)", lambda: self._bereich_knopf("drehen", winkel=90))
        L.knopf("⇋", "Waagerecht spiegeln (Auswahl oder ganze Leinwand)",
                lambda: self._bereich_knopf("spiegeln", richtung="waagerecht"))
        L.knopf("⇅", "Senkrecht spiegeln (Auswahl oder ganze Leinwand)",
                lambda: self._bereich_knopf("spiegeln", richtung="senkrecht"))
        L.knopf("📐 Größe", "Leinwandgröße ändern", self.groesse_dialog, breit=True)
        L.trenner()
        L.knopf("−", "Verkleinern (Strg+−, Strg+Mausrad)", lambda: self._zoom(-1))
        self.k_zoom = L.knopf("100 %", "Originalgröße", lambda: self._zoom_auf(1.0), breit=True)
        L.knopf("+", "Vergrößern (Strg++)", lambda: self._zoom(+1))
        L.knopf("⤢", "Einpassen (Strg+0)", self.einpassen)
        L.dehnen()
        L.knopf("🎨 KI malt …", "Die OfficeAI malt oder zeichnet etwas auf die Leinwand", self._ki_malt, breit=True)
        self._farbe_gesetzt(self.leinwand.farbe.name())

    def werkzeug_waehlen(self, name: str) -> None:
        self.leinwand.schwebend_ablegen()
        self.leinwand.werkzeug = name
        for n, k in self.werkzeug_knoepfe.items():
            k.blockSignals(True)
            k.setChecked(n == name)
            k.blockSignals(False)
        self.leinwand.setCursor(Qt.CursorShape.CrossCursor if name not in ("auswahl", "pipette")
                                else Qt.CursorShape.ArrowCursor)

    def _farbe_gesetzt(self, c: str) -> None:
        self.leinwand.farbe = QColor(c)
        klecks = QPixmap(20, 20)                       # runder Farbklecks mit hellem Rand – auch Schwarz bleibt sichtbar
        klecks.fill(Qt.GlobalColor.transparent)
        p = QPainter(klecks)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#ffffff"), 1.6))
        p.setBrush(QColor(c))
        p.drawEllipse(QRectF(2, 2, 16, 16))
        p.end()
        self.farbknopf.setIcon(QIcon(klecks))
        self.farbknopf.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.farbknopf.setToolTip(f"Mal- und Schriftfarbe: {c}")
        if self.leinwand.werkzeug == "pipette":
            self.werkzeug_waehlen("pinsel")

    def _gefuellt(self, an: bool) -> None:
        self.leinwand.gefuellt = bool(an)

    def _zoom(self, richtung: int) -> None:
        z = self.leinwand.zoom
        if richtung > 0:
            ziel = next((s for s in ZOOM_STUFEN if s > z + 1e-6), ZOOM_STUFEN[-1])
        else:
            ziel = next((s for s in reversed(ZOOM_STUFEN) if s < z - 1e-6), ZOOM_STUFEN[0])
        self._zoom_auf(ziel)

    def _zoom_auf(self, z: float) -> None:
        self.leinwand.zoom_setzen(z)
        self.k_zoom.setText(f"{round(self.leinwand.zoom * 100)} %")
        self._position("")

    def einpassen(self) -> None:
        r = self.flaeche.viewport().size()
        b, h = self.leinwand.bild.width(), self.leinwand.bild.height()
        if r.width() < 50 or r.height() < 50:
            return
        self._zoom_auf(min(1.0, (r.width() - 24) / b, (r.height() - 24) / h))

    def _position(self, text: str) -> None:
        b, h = self.leinwand.bild.width(), self.leinwand.bild.height()
        self.status.rechts(f"{text + ' · ' if text else ''}{b}×{h} · {round(self.leinwand.zoom * 100)} %")

    def _einfuegen(self) -> None:
        if self.leinwand.einfuegen():
            self.werkzeug_waehlen("auswahl")
            self.status.zeigen("Eingefügt – mit der Maus verschieben, ein Klick daneben legt es ab.", "ok")
        else:
            self.status.zeigen("In der Zwischenablage ist kein Bild.", "warn")

    def _bereich_knopf(self, name: str, **felder) -> None:
        """Drehen oder Spiegeln aus der Leiste: die Auswahl – sonst die ganze Leinwand."""
        lw = self.leinwand
        lw.schwebend_ablegen()
        if lw.auswahl is not None:
            r = lw.auswahl.intersected(lw.bild.rect())
            felder.update(x=r.x(), y=r.y(), b=r.width(), h=r.height())
        g = M.werkzeug_pruefen(name, felder, lw.bild.width(), lw.bild.height())
        if isinstance(g, str):
            return
        lw.merken()
        text = lw.bereich_werkzeug(name, g)
        if text is None:
            lw.rueck.pop()
            return
        lw.eintrag(text)
        lw.auswahl = None
        self._geaendert()
        if name == "drehen" and "bereich" not in felder:
            self.einpassen()

    def _zuschneiden(self) -> None:
        if not self.leinwand.zuschneiden():
            self.status.zeigen("Erst mit ⬚ Auswahl einen Bereich markieren.", "warn")
        else:
            self._position("")

    def _ki_malt(self) -> None:
        text, ok = QInputDialog.getText(self, "🎨 KI malt", "Was soll die OfficeAI malen oder zeichnen?")
        if ok and text.strip():
            self.bild_wunsch.emit(text.strip())

    def neue_leinwand(self) -> None:
        if self.ungespeichert():
            wahl = frage_speichern(self, self.name())
            if wahl == "speichern" and not self.speichern():
                return
            if wahl not in ("speichern", "verwerfen"):
                return
        g = self._groesse_fragen("Neue Leinwand", M.STANDARD_GROESSE)
        if g is None:
            return
        self.leinwand.neu(*g)
        self.pfad = None
        self._veraendert = False
        self._titel()
        self.einpassen()

    def groesse_dialog(self) -> None:
        g = self._groesse_fragen("Leinwandgröße", (self.leinwand.bild.width(), self.leinwand.bild.height()))
        if g is None:
            return
        self.leinwand.merken()
        self.leinwand.groesse_aendern(*g)
        self.leinwand.eintrag(f"Leinwand auf {g[0]}×{g[1]} geändert")
        self._geaendert()
        self.einpassen()

    def _groesse_fragen(self, titel: str, jetzt: tuple[int, int]) -> tuple[int, int] | None:
        text, ok = QInputDialog.getText(self, titel, "Breite × Höhe in Bildpunkten:", text=f"{jetzt[0]} x {jetzt[1]}")
        if not ok:
            return None
        teile = [t for t in text.replace("×", "x").replace("*", "x").lower().split("x") if t.strip()]
        g = M.groesse_pruefen(*(teile[:2] if len(teile) == 2 else (0, 0)))
        if isinstance(g, str):
            self.status.zeigen(g, "warn")
            return None
        return g

    def rueckgaengig(self) -> None:
        if self.leinwand.rueckgaengig():
            self._position("")

    def wiederholen(self) -> None:
        if self.leinwand.wiederholen():
            self._position("")

    # -- Datei -----------------------------------------------------------------------------
    def name(self) -> str:
        return Path(self.pfad).name if self.pfad else "Neues Bild"

    def _titel(self) -> None:
        self.titel_geaendert.emit(("● " if self._veraendert else "") + self.name())

    def _geaendert(self) -> None:
        self._veraendert = True
        self._titel()

    def ungespeichert(self) -> bool:
        return self._veraendert

    def laden(self, pfad: str) -> None:
        bild = bild_lesen(pfad)
        self.leinwand.neu(bild.width(), bild.height())
        self.leinwand.bild = bild
        self.leinwand.eintrag(f"Bild {Path(pfad).name} geöffnet ({bild.width()}×{bild.height()})")
        self.leinwand.update()
        self.pfad = pfad if Path(pfad).suffix.lower() in M.BILD_SPEICHERN else None
        self._veraendert = False
        self._titel()
        QTimer.singleShot(0, self.einpassen)

    def speichern(self, unter: bool = False) -> bool:
        self.leinwand.schwebend_ablegen()
        pfad = self.pfad
        if unter or not pfad:
            vorschlag = pfad or str(ablage.neuer_pfad(self.ordner, "Bild", ".png"))
            pfad, _ = QFileDialog.getSaveFileName(self, "Speichern unter", vorschlag,
                                                  ablage.filter_text(M.BILD_SPEICHERN, alle=False))
            if not pfad:
                return False
            if Path(pfad).suffix.lower() not in M.BILD_SPEICHERN:
                pfad += ".png"
        try:
            self.bild_speichern(pfad)
        except Exception as e:
            self.status.zeigen(f"Nicht gespeichert: {e}", "fehler")
            return False
        self.pfad = pfad
        self._veraendert = False
        self._titel()
        self.status.zeigen(f"Gespeichert: {Path(pfad).name}", "ok")
        self.gespeichert.emit(pfad)
        return True

    def bild_speichern(self, pfad: str) -> None:
        """Atomar speichern (QSaveFile); JPEG und BMP ohne Durchsichtigkeit auf Hintergrund."""
        end = Path(pfad).suffix.lower()
        if end not in M.BILD_SPEICHERN:
            raise ValueError(f"Als {end or '(ohne Endung)'} kann Malen nicht speichern")
        bild = self.leinwand.bild
        if end in (".jpg", ".bmp"):
            bild = bild.convertToFormat(QImage.Format.Format_RGB32)
        datei = QSaveFile(pfad)
        if not datei.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(datei.errorString())
        if not bild.save(datei, end.lstrip(".").upper().replace("JPG", "JPEG"), 92 if end in (".jpg", ".webp") else -1):
            datei.cancelWriting()
            raise OSError("Bild ließ sich nicht schreiben")
        if not datei.commit():
            raise OSError(datei.errorString())

    def schliessen_ok(self) -> bool:
        if not self.ungespeichert():
            return True
        wahl = frage_speichern(self, self.name())
        if wahl == "speichern":
            return self.speichern()
        return wahl == "verwerfen"

    # -- für die OfficeAI ------------------------------------------------------------------
    def kontext(self, grenze: int = 24000) -> dict:
        lw = self.leinwand
        b, h = lw.bild.width(), lw.bild.height()
        zeilen = [f"Leinwand {b}×{h} Bildpunkte, (0,0) = oben links, Hintergrund {lw.hintergrund.name()}.",
                  f"Formen und Schritte bisher ({len(lw.elemente)}):" if lw.elemente else "Die Leinwand ist leer."]
        zeilen += [f"{i}. {t}" for i, t in enumerate(lw.elemente, 1)]
        text = "\n".join(zeilen)
        if len(text) > grenze:
            text = text[-grenze:]
        auswahl = f"Markiert: {Leinwand._rechteck_text(lw.auswahl)}" if lw.auswahl is not None else ""
        return {"app": self.app, "name": f"{self.name()} · {b}×{h} Bildpunkte", "kontext": text, "auswahl": auswahl,
                "groesse": [b, h]}

    def ziel(self, aktion: dict, fenster: QWidget) -> QPoint | None:
        """Wohin der Zeiger zuerst fliegt: Startpunkt der ersten Form, der Füllpunkt – sonst die Mitte."""
        lw = self.leinwand
        a = aktion
        if a.get("tool") == "zeichnen" and isinstance(a.get("formen"), list) and a["formen"]:
            g = M.form_pruefen(a["formen"][0], lw.bild.width(), lw.bild.height())
            if isinstance(g, dict):
                x, y = _startpunkt(g)
                return self._sichtbar(x, y, fenster)
        zahl = lambda k: isinstance(a.get(k), (int, float))                       # noqa: E731
        if all(zahl(k) for k in ("x", "y", "b", "h")):
            return self._sichtbar(float(a["x"]) + float(a["b"]) / 2, float(a["y"]) + float(a["h"]) / 2, fenster)
        if zahl("x") and zahl("y"):
            return self._sichtbar(float(a["x"]), float(a["y"]), fenster)
        return self.flaeche.viewport().mapTo(fenster, QPoint(self.flaeche.viewport().width() // 2,
                                                              self.flaeche.viewport().height() // 3))

    def _sichtbar(self, x: float, y: float, fenster: QWidget) -> QPoint:
        lw = self.leinwand
        self.flaeche.ensureVisible(int(x * lw.zoom), int(y * lw.zoom), 60, 60)
        return lw.im_fenster(x, y, fenster)

    def ki_aktion(self, aktion: dict, ausfuehrer, fertig) -> None:
        """Ein Werkzeug der OfficeAI (oder ein internes der Sitzung) ausführen; fertig(ok, text) danach."""
        lw = self.leinwand
        name = aktion.get("tool")
        b, h = lw.bild.width(), lw.bild.height()
        if name == "ansehen":
            fertig(True, self.kontext()["kontext"] + (f"\n{self.kontext()['auswahl']}" if lw.auswahl else ""))
            return
        if name == "zeichnen":
            formen, fehler = M.formen_pruefen(aktion.get("formen"), b, h)
            if fehler:
                fertig(False, fehler)
                return
            lw.merken()
            self._formen_nacheinander(formen, ausfuehrer, fertig)
            return
        if name == "fuellen":
            farbe = QColor(M.farbe(aktion["farbe"]))
            x, y = int(aktion["x"]), int(aktion["y"])
            lw.merken()
            if not lw.fuellen(x, y, farbe):
                lw.rueck.pop()
                fertig(False, f"An ({x},{y}) gibt es nichts zu füllen (außerhalb oder schon diese Farbe).")
                return
            lw.eintrag(f"Fläche bei ({x},{y}) mit {farbe.name()} gefüllt")
            self._geaendert()
            fertig(True, f"Fläche bei ({x},{y}) gefüllt.")
            return
        if name == "hintergrund":
            lw.merken()
            lw.hintergrund_setzen(QColor(M.farbe(aktion["farbe"])))
            self._geaendert()
            fertig(True, f"Leinwand ganz in {lw.hintergrund.name()}.")
            return
        if name == "groesse":
            g = M.groesse_pruefen(aktion.get("b"), aktion.get("h"))
            lw.merken()
            lw.groesse_aendern(*g)
            lw.eintrag(f"Leinwand auf {g[0]}×{g[1]} geändert")
            self._geaendert()
            self.einpassen()
            fertig(True, f"Leinwand jetzt {g[0]}×{g[1]}.")
            return
        if name in BEREICH_WERKZEUGE:
            g = M.werkzeug_pruefen(name, aktion, b, h)
            if isinstance(g, str):
                fertig(False, g)
                return
            if name == "farbe_lesen":
                fertig(True, f"Farbe bei ({g['x']},{g['y']}): {lw.farbe_an(g['x'], g['y'])}")
                return
            lw.merken()
            text = lw.bereich_werkzeug(name, g)
            if text is None:
                lw.rueck.pop()
                fertig(False, {"umfaerben": "Im Bereich gibt es diese Farbe nicht (Toleranz erhöhen?).",
                               "zuschneiden": f"Zu klein – mindestens {M.MIN_SEITE}×{M.MIN_SEITE}."}.get(name,
                                                                                            "Nichts geändert."))
                return
            lw.eintrag(text)
            self._geaendert()
            if (lw.bild.width(), lw.bild.height()) != (b, h):
                self.einpassen()
            fertig(True, text + ".")
            return
        if name == "leinwand_export":
            fertig(True, self.leinwand_exportieren())
            return
        if name == "bild_setzen":
            self._bild_setzen(aktion, fertig)
            return
        fertig(False, f"Unbekannt: {name}")

    def _formen_nacheinander(self, formen: list[dict], ausfuehrer, fertig) -> None:
        lw = self.leinwand
        fenster = ausfuehrer.fenster
        zustand = {"i": 0, "teil": 0}

        def schritt():
            i = zustand["i"]
            if ausfuehrer.angehalten:
                lw.vorschau = None
                lw.update()
                self._geaendert()
                fertig(False, f"angehalten nach {i} von {len(formen)} Formen (Rückgängig holt alles zurück).")
                return
            if i >= len(formen):
                lw.vorschau = None
                self._geaendert()
                fertig(True, f"{len(formen)} Form(en) gezeichnet.")
                return
            f = formen[i]
            teile = TEILE if f["art"] in WACHSEN else 1
            if zustand["teil"] == 0:
                x, y = _startpunkt(f)
                ausfuehrer.zeiger.fliegen(self._sichtbar(x, y, fenster), f"zeichnet {_art_name(f['art'])}")
            zustand["teil"] += 1
            if zustand["teil"] < teile:
                lw.vorschau = (f, zustand["teil"] / teile)
                lw.update()
                QTimer.singleShot(TEIL_MS, self, schritt)
                return
            lw.vorschau = None
            lw.form(f)
            lw.eintrag(M.beschreiben(f))
            zustand["i"], zustand["teil"] = i + 1, 0
            QTimer.singleShot(FORM_MS, self, schritt)
        schritt()

    def leinwand_exportieren(self) -> str:
        """Die Leinwand als PNG im Temp-Ordner (für Seh-Modell und Umzeichnen) – der Pfad geht an die Sitzung."""
        self.leinwand.schwebend_ablegen()
        ordner = Path(tempfile.gettempdir()) / "nemicli_office"
        ordner.mkdir(parents=True, exist_ok=True)
        ziel = ordner / f"leinwand_{os.getpid()}_{id(self) & 0xFFFF:x}.png"
        if not self.leinwand.bild.convertToFormat(QImage.Format.Format_RGB32).save(str(ziel), "PNG"):
            raise OSError("Leinwand ließ sich nicht exportieren")
        return str(ziel)

    def _bild_setzen(self, aktion: dict, fertig) -> None:
        try:
            bild = bild_lesen(str(aktion.get("pfad") or ""))
        except ValueError as e:
            fertig(False, str(e))
            return
        lw = self.leinwand
        ziel = None
        if all(isinstance(aktion.get(k), (int, float)) for k in ("x", "y", "b", "h")):
            ziel = QRectF(float(aktion["x"]), float(aktion["y"]), max(1.0, float(aktion["b"])),
                          max(1.0, float(aktion["h"])))
        lw.merken()
        r = lw.bild_setzen(bild, ziel, ersetzen=bool(aktion.get("ersetzen")))
        art = "umgezeichnet" if aktion.get("ersetzen") else "eingesetzt"
        lw.eintrag(f"Bild {art} bei ({int(r.x())},{int(r.y())}) {int(r.width())}×{int(r.height())}"
                   + (f": {str(aktion.get('beschreibung'))[:80]}" if aktion.get("beschreibung") else ""))
        self._geaendert()
        fertig(True, f"Bild {art} ({int(r.width())}×{int(r.height())}).")


def _startpunkt(f: dict) -> tuple[float, float]:
    if f["art"] in ("linie", "pfeil"):
        return f["von"]
    if f["art"] in ("polygon", "strich", "kurve"):
        return f["punkte"][0]
    return f["x"], f["y"]


def _art_name(art: str) -> str:
    return {"linie": "Linie", "pfeil": "Pfeil", "rechteck": "Rechteck", "kreis": "Kreis", "ellipse": "Ellipse",
            "polygon": "Fläche", "strich": "Strich", "kurve": "Kurve", "bogen": "Bogen", "stern": "Stern",
            "herz": "Herz", "n_eck": "Vieleck", "spray": "Sprühfarbe", "text": "Text"}.get(art, art)
