"""office_seiten.py – A4-Seiten in Nemi Office.

✍️ Schreiben: jede Seite ein eigenes A4-Blatt mit 2 cm Rand, Eckwinkeln und cm-Lineal; der Text bricht am
Bildschirm genauso um wie im PDF (gleiches Seitenformat, gleiche Ränder). 📊 Tabelle: gestrichelte Linien, wo eine
gedruckte A4-Seite endet; das PDF der Tabelle teilt die Zellen genau dort auf.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen)
from PySide6.QtWidgets import QWidget

from design import FARBE, schrift

MM = 96 / 25.4                     # Bildschirm-Pixel je Millimeter bei 100 % (96 dpi)
SEITE_W = round(210 * MM)          # 794
SEITE_H = round(297 * MM)          # 1123
RAND = round(20 * MM)              # 76 – 2 cm wie im PDF, in .docx und .odt
PLATZ_W = SEITE_W - 2 * RAND       # Textbereich einer Seite
PLATZ_H = SEITE_H - 2 * RAND
LUECKE = 20                        # Abstand zwischen zwei Blättern (liegt im Rand, verdeckt keinen Text)
LINEAL_H = 24
OBEN = LINEAL_H + 14               # Lineal plus Abstand zum ersten Blatt
GRENZE = QColor(139, 92, 246, 210)


# ---------------------------------------------------------------------------
# ✍️ Schreiben
# ---------------------------------------------------------------------------

class Lineal(QWidget):
    """cm-Lineal über dem Blatt: Zahlen ab dem linken Rand, der Rand selbst grau."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(LINEAL_H)
        self.versatz = 0

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(FARBE["grund"]))
        x0 = -self.versatz
        band = QRectF(x0, 4, SEITE_W, LINEAL_H - 8)
        p.fillRect(band, QColor("#3a3a44"))
        p.fillRect(QRectF(x0 + RAND, 4, PLATZ_W, LINEAL_H - 8), QColor("#e9e9ee"))
        f = QFont(schrift(7))
        p.setFont(f)
        for halb in range(-int(RAND / MM / 5), int(PLATZ_W / MM / 5) + int(RAND / MM / 5) + 1):
            x = x0 + RAND + halb * 5 * MM
            if x < x0 or x > x0 + SEITE_W:
                continue
            innen = 0 <= halb * 5 * MM <= PLATZ_W
            p.setPen(QColor("#55555f") if innen else QColor("#9a9aa6"))
            if halb % 2 == 0 and halb != 0:
                p.drawText(QRectF(x - 10, 4, 20, LINEAL_H - 8), Qt.AlignmentFlag.AlignCenter, str(abs(halb // 2)))
            elif halb % 2:
                p.drawLine(QPointF(x, LINEAL_H / 2 - 2), QPointF(x, LINEAL_H / 2 + 2))
        p.end()


def seiten_zahl(doc) -> int:
    return max(1, math.ceil(doc.size().height() / SEITE_H - 0.01))


def seite_von(feld) -> tuple[int, int]:
    """(aktuelle Seite, Seiten insgesamt) nach der Schreibmarke."""
    gesamt = seiten_zahl(feld.document())
    y = feld.cursorRect().center().y() + feld.verticalScrollBar().value()
    return min(gesamt, max(1, int(y // SEITE_H) + 1)), gesamt


def _eckwinkel(p: QPainter, x: float, y: float, dx: int, dy: int) -> None:
    p.drawLine(QPointF(x, y), QPointF(x + 12 * dx, y))
    p.drawLine(QPointF(x, y), QPointF(x, y + 12 * dy))


def blatt_malen(feld, p: QPainter, seiten: bool) -> None:
    """Dunkle Lücken zwischen den Blättern, Grund unter dem letzten, Eckwinkel am Textbereich jeder Seite."""
    vp = feld.viewport()
    w, h = vp.width(), vp.height()
    dx, dy = feld.horizontalScrollBar().value(), feld.verticalScrollBar().value()
    grund = QColor(FARBE["grund"])
    doc_h = feld.document().size().height()
    n = seiten_zahl(feld.document()) if seiten else 1
    ende = n * SEITE_H if seiten else max(doc_h, SEITE_H)
    if ende - dy < h:
        p.fillRect(QRectF(0, ende - dy, w, h - (ende - dy)), grund)
    if SEITE_W - dx < w:                                         # rechts neben dem Blatt
        p.fillRect(QRectF(SEITE_W - dx, 0, w - (SEITE_W - dx), h), grund)
    erste, letzte = int(dy // SEITE_H), min(n, int((dy + h) // SEITE_H) + 1)
    links, rechts = RAND - dx, SEITE_W - RAND - dx
    for k in range(erste, letzte + 1):                           # Seite k liegt bei [k·H, (k+1)·H)
        oben = k * SEITE_H - dy
        if seiten and 1 <= k < n:                                # Lücke zwischen Seite k-1 und k
            p.fillRect(QRectF(0, oben - LUECKE / 2, w, LUECKE), grund)
            schatten = QLinearGradient(0, oben - LUECKE / 2, 0, oben - LUECKE / 2 + 6)
            schatten.setColorAt(0, QColor(0, 0, 0, 120))
            schatten.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(0, oben - LUECKE / 2, w, 6), schatten)
        if k >= n or (not seiten and k > 0):
            continue
        p.setPen(QPen(QColor("#b4b4be"), 1))
        o, u = oben + RAND, (k + 1) * SEITE_H - dy - RAND
        _eckwinkel(p, links, o, -1, -1)
        _eckwinkel(p, rechts, o, 1, -1)
        if seiten:
            _eckwinkel(p, links, u, -1, 1)
            _eckwinkel(p, rechts, u, 1, 1)


# ---------------------------------------------------------------------------
# 📊 Tabelle
# ---------------------------------------------------------------------------

def umbrueche(groessen, platz: float) -> list[int]:
    """Stellen (Index), an denen eine neue Druckseite beginnt. Was allein zu groß ist, bekommt eine eigene Seite."""
    raus, summe = [], 0
    for i, g in enumerate(groessen):
        if summe > 0 and summe + g > platz:
            raus.append(i)
            summe = 0
        summe += g
    return raus


def gruppen(groessen: list[int], platz: float) -> list[tuple[int, int]]:
    grenzen = [0, *umbrueche(groessen, platz), len(groessen)]
    return [(a, b) for a, b in zip(grenzen, grenzen[1:]) if b > a]


def grenzen_malen(raster, p: QPainter) -> None:
    """Gestrichelte Linien im Raster, wo eine gedruckte A4-Seite endet (wie im PDF der Tabelle)."""
    m = raster.model()
    if m is None:
        return
    vp = raster.viewport()
    pen = QPen(GRENZE, 1.6, Qt.PenStyle.DashLine)
    p.setPen(pen)
    for s in umbrueche((raster.columnWidth(s) for s in range(m.columnCount())), PLATZ_W):
        x = raster.columnViewportPosition(s)
        if 0 <= x <= vp.width():
            p.drawLine(QPointF(x, 0), QPointF(x, vp.height()))
    letzte = raster.rowAt(vp.height())
    letzte = m.rowCount() - 1 if letzte < 0 else letzte
    for z in umbrueche((raster.rowHeight(z) for z in range(letzte + 1)), PLATZ_H):
        y = raster.rowViewportPosition(z)
        if 0 <= y <= vp.height():
            p.drawLine(QPointF(0, y), QPointF(vp.width(), y))


def tabelle_pdf_bytes(mappe, breite: int, hoehe: int) -> bytes:
    """Alle Blätter als PDF auf A4: Seiten wie die Grenzen im Raster (erst nach unten, dann nach rechts), Kopfzeile
    mit Blattname und Seitenzahl, Farben und Fett wie am Bildschirm."""
    from buero import formel as F
    puffer = QBuffer()
    puffer.open(QIODevice.OpenModeFlag.WriteOnly)
    w = QPdfWriter(puffer)
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter))
    w.setResolution(300)
    w.setCreator("Nemi Office")
    p = QPainter(w)
    p.scale(w.resolution() / 96, w.resolution() / 96)
    normal = schrift(10.5)
    normal.setPixelSize(14)
    fett = QFont(normal)
    fett.setBold(True)
    klein = QFont(normal)
    klein.setPixelSize(11)
    rechts = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    links = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    nr = 0
    for blatt in mappe.blaetter:
        zeilen, spalten = blatt.groesse()
        if not zeilen:
            continue
        breiten = [blatt.breiten.get(s, breite) for s in range(spalten)]
        for s0, s1 in gruppen(breiten, PLATZ_W):
            for z0, z1 in gruppen([hoehe] * zeilen, PLATZ_H):
                if nr:
                    w.newPage()
                nr += 1
                p.setFont(klein)
                p.setPen(QColor("#6b6b78"))
                kopf = QRectF(RAND, RAND - 30, PLATZ_W, 20)
                p.drawText(kopf, links, blatt.name)
                p.drawText(kopf, rechts, f"Seite {nr}")
                y = RAND
                for z in range(z0, z1):
                    x = RAND
                    for s in range(s0, s1):
                        r = QRectF(x, y, breiten[s], hoehe)
                        f = mappe.format(blatt, z, s)
                        if f.get("hg"):
                            p.fillRect(r, QColor(f["hg"]))
                        v = mappe.wert(blatt, z, s)
                        text = mappe.anzeige(blatt, z, s)
                        if text:
                            p.save()
                            p.setClipRect(r)
                            p.setFont(fett if f.get("fett") else normal)
                            p.setPen(QColor("#d93636") if isinstance(v, F.Fehler) else QColor(f.get("fg") or "#1f1f24"))
                            zahl = isinstance(v, (int, float)) and not isinstance(v, bool)
                            mitte = isinstance(v, (bool, F.Fehler))
                            p.drawText(r.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignCenter if mitte else
                                       rechts if zahl else links, text)
                            p.restore()
                        p.setPen(QPen(QColor("#c8c8d0"), 0.8))
                        p.drawRect(r)
                        x += breiten[s]
                    y += hoehe
    p.end()
    del w
    puffer.close()
    return bytes(puffer.data())
