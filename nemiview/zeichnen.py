"""
zeichnen.py - NemiView zeichnet das Layout mit QPainter (Raster, ohne Grafikkarte).

Hintergründe (Farbe, linear-gradient), Ränder mit Rundung, einfache Schatten, Deckkraft, overflow-Zuschnitt,
Texte, Listenpunkte, Bilder (PNG/JPG/GIF/WebP, SVG) aus dem App-Ordner und Formular-Elemente
(Textfeld, Zahl, Knopf, Kästchen, Schieberegler, Farbwahl, Auswahlliste, Textbereich, Fortschritt).
"""
from __future__ import annotations

import math
import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen)

from . import nemicss
from .layout import Kasten, Messer

_FAMILIEN = {"system-ui": "Segoe UI", "sans-serif": "Segoe UI", "-apple-system": "Segoe UI", "arial": "Arial",
             "helvetica": "Arial", "serif": "Georgia", "georgia": "Georgia", "monospace": "Consolas",
             "ui-monospace": "Consolas", "consolas": "Consolas", "courier new": "Courier New",
             "segoe ui": "Segoe UI", "verdana": "Verdana", "tahoma": "Tahoma", "cursive": "Comic Sans MS",
             "comic sans ms": "Comic Sans MS", "times new roman": "Times New Roman", "impact": "Impact"}
_SCHRIFTEN: dict = {}


def schrift(s: dict) -> QFont:
    key = (s["family"], round(s["size"], 2), s["weight"], s["italic"], round(s.get("spacing", 0), 2))
    f = _SCHRIFTEN.get(key)
    if f is None:
        name = "Segoe UI"
        for teil in s["family"].split(","):
            t = teil.strip().strip("\"'").lower()
            if t in _FAMILIEN:
                name = _FAMILIEN[t]
                break
            if t:
                name = teil.strip().strip("\"'")
                break
        f = QFont(name)
        f.setPixelSize(max(1, int(round(s["size"]))))
        f.setWeight(QFont.Weight(min(900, max(100, int(s["weight"])))))
        f.setItalic(s["italic"])
        if s.get("spacing"):
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, s["spacing"])
        if len(_SCHRIFTEN) > 500:
            _SCHRIFTEN.clear()
        _SCHRIFTEN[key] = f
    return f


class QtMesser(Messer):
    def __init__(self):
        self._metrik: dict = {}

    def _m(self, s):
        f = schrift(s)
        m = self._metrik.get(id(f))
        if m is None:
            m = QFontMetricsF(f)
            self._metrik[id(f)] = m
        return m

    def breite(self, text, s):
        return self._m(s).horizontalAdvance(text)

    def hoehe(self, s):
        return self._m(s).height()


def qfarbe(t, aktuell=(0, 0, 0, 255)):
    f = nemicss.farbe(t, aktuell) if isinstance(t, str) else t
    if f is None:
        return None
    return QColor(f[0], f[1], f[2], f[3])


def _rund_pfad(x, y, w, h, r) -> QPainterPath:
    tl, tr, br, bl = (max(0.0, min(v, w / 2, h / 2)) for v in r)
    p = QPainterPath()
    p.moveTo(x + tl, y)
    p.lineTo(x + w - tr, y)
    if tr:
        p.arcTo(QRectF(x + w - 2 * tr, y, 2 * tr, 2 * tr), 90, -90)
    p.lineTo(x + w, y + h - br)
    if br:
        p.arcTo(QRectF(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br), 0, -90)
    p.lineTo(x + bl, y + h)
    if bl:
        p.arcTo(QRectF(x, y + h - 2 * bl, 2 * bl, 2 * bl), 270, -90)
    p.lineTo(x, y + tl)
    if tl:
        p.arcTo(QRectF(x, y, 2 * tl, 2 * tl), 180, -90)
    p.closeSubpath()
    return p


class Maler:
    def __init__(self, layout, bilder=None):
        self.layout = layout
        self.bilder = bilder               # Rückruf (el) → QImage | QSvgRenderer | None
        self.fokus = None

    def malen(self, p: QPainter, scroll_y: float = 0.0, breite=None, hoehe=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        w = self.layout.wurzel
        grund = self._grundfarbe()
        p.fillRect(QRectF(0, 0, breite or self.layout.breite, hoehe or self.layout.hoehe), grund)
        p.save()
        p.translate(0, -scroll_y)
        self.scroll_y = scroll_y
        self._kasten(p, w, 1.0)
        p.restore()

    def _grundfarbe(self) -> QColor:
        """Hintergrund der ganzen Fläche: vom html-, sonst vom body-Element (wie im Browser)."""
        w = self.layout.wurzel
        for k in [w] + [c for c in w.kinder if isinstance(c, Kasten) and c.el.tag == "body"]:
            f = qfarbe(k.stil.get("background-color"))
            if f is not None and f.alpha():
                return f
        return QColor(255, 255, 255)

    # -- Kästen ---------------------------------------------------------------------
    def _kasten(self, p: QPainter, k: Kasten, deckkraft: float):
        s = k.stil
        if s.get("visibility") == "hidden" and not k.kinder:
            return
        try:
            op = float(s.get("opacity", "1"))
        except ValueError:
            op = 1.0
        deck = deckkraft * max(0.0, min(1.0, op))
        if deck <= 0.003:
            return
        p.save()
        if k.fest:
            p.translate(0, self.scroll_y)
        p.setOpacity(deck)
        sichtbar = s.get("visibility") != "hidden"
        if sichtbar and k.el.tag not in ("html",) and not (k.el.tag == "body" and k is not self.layout.wurzel and
                                                          self._ist_grund(k)):
            self._flaeche(p, k)
        if sichtbar:
            self._ersetzt(p, k)
        clip = k.clip and k.el.tag not in ("html", "body")
        if clip:
            r = self._radien(k)
            p.setClipPath(_rund_pfad(k.x + k.rand[3], k.y + k.rand[0], k.w - k.rand[1] - k.rand[3],
                                     k.h - k.rand[0] - k.rand[2], r), Qt.ClipOperation.IntersectClip)
        normal = [c for c in k.kinder if isinstance(c, Kasten) and c.pos == "static"]
        oben = sorted([c for c in k.kinder if isinstance(c, Kasten) and c.pos != "static"], key=lambda c: c.z)
        for c in [c for c in oben if c.z < 0] + normal:
            self._kasten(p, c, deck)
        if sichtbar:
            self._texte(p, k)
        for c in [c for c in oben if c.z >= 0]:
            self._kasten(p, c, deck)
        p.restore()

    def _ist_grund(self, k):
        return qfarbe(self.layout.wurzel.stil.get("background-color")) is None

    def _radien(self, k):
        out = []
        for e in ("top-left", "top-right", "bottom-right", "bottom-left"):
            v = k.stil.get(f"border-{e}-radius", "0").split()[0]
            r = _laenge(v, min(k.w, k.h), k.schrift["size"], self.layout.ctx)
            out.append(r or 0.0)
        return out

    def _flaeche(self, p: QPainter, k: Kasten):
        s = k.stil
        x, y, w, h = k.x, k.y, k.w, k.h
        if w <= 0 or h <= 0:
            return
        r = self._radien(k)
        pfad = _rund_pfad(x, y, w, h, r)
        schatten = s.get("box-shadow")
        if schatten and schatten != "none":
            self._schatten(p, k, schatten, r)
        farbe = qfarbe(s.get("background-color"))
        if farbe is not None and farbe.alpha():
            p.fillPath(pfad, farbe)
        bild = s.get("background-image", "none")
        if "linear-gradient(" in bild:
            g = self._verlauf(bild, x, y, w, h)
            if g is not None:
                p.fillPath(pfad, QBrush(g))
        breiten = k.rand
        if any(breiten):
            fl = [qfarbe(s.get(f"border-{seite}-color", "currentcolor"), _farbe_tupel(s)) for seite in
                  ("top", "right", "bottom", "left")]
            if len(set(breiten)) == 1 and all(f is not None and f == fl[0] for f in fl):
                bw = breiten[0]
                pen = QPen(fl[0], bw)
                stil = s.get("border-top-style", "solid")
                if stil == "dashed":
                    pen.setStyle(Qt.PenStyle.DashLine)
                elif stil == "dotted":
                    pen.setStyle(Qt.PenStyle.DotLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(_rund_pfad(x + bw / 2, y + bw / 2, w - bw, h - bw, [max(0.0, v - bw / 2) for v in r]))
            else:
                for i, (bw, f) in enumerate(zip(breiten, fl)):
                    if bw and f is not None:
                        rect = [QRectF(x, y, w, bw), QRectF(x + w - bw, y, bw, h), QRectF(x, y + h - bw, w, bw),
                                QRectF(x, y, bw, h)][i]
                        p.fillRect(rect, f)
        umriss = s.get("outline")
        if umriss and umriss not in ("none", "0") and "none" not in umriss:
            teile = nemicss.werte(umriss)
            bw = next((_laenge(t, None, k.schrift["size"], self.layout.ctx) for t in teile
                       if re.match(r"[\d.]", t)), 2.0) or 2.0
            f = next((qfarbe(t) for t in teile if nemicss.ist_farbe(t)), QColor(255, 212, 0))
            off = _laenge(s.get("outline-offset", "0"), None, k.schrift["size"], self.layout.ctx) or 0.0
            p.setPen(QPen(f, bw))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(_rund_pfad(x - off - bw / 2, y - off - bw / 2, w + 2 * off + bw, h + 2 * off + bw, r))

    def _schatten(self, p, k, text, r):
        teile = nemicss.werte(text.split(",")[0])
        if "inset" in teile:
            return
        zahlen = [t for t in teile if re.match(r"-?[\d.]", t)]
        farbe = next((qfarbe(t) for t in teile if nemicss.ist_farbe(t)), QColor(0, 0, 0, 80))
        fs = k.schrift["size"]
        dx, dy, blur, ausbreit = ([_laenge(z, None, fs, self.layout.ctx) or 0.0 for z in zahlen] + [0, 0, 0, 0])[:4]
        if farbe is None:
            return
        schritte = max(1, min(8, int(blur / 3)))
        basis = farbe.alphaF()
        for i in range(schritte, 0, -1):
            f = QColor(farbe)
            f.setAlphaF(basis / schritte * 0.9)
            e = ausbreit + blur * i / schritte / 2
            p.fillPath(_rund_pfad(k.x + dx - e, k.y + dy - e, k.w + 2 * e, k.h + 2 * e, [v + e for v in r]), f)

    def _verlauf(self, text, x, y, w, h):
        m = re.search(r"linear-gradient\((.*)\)", text)
        if not m:
            return None
        teile = [t.strip() for t in nemicss._teilen(m.group(1), ",")]
        winkel = 180.0
        if teile and (teile[0].endswith("deg") or teile[0].startswith("to ")):
            t = teile.pop(0)
            if t.endswith("deg"):
                winkel = float(t[:-3])
            else:
                winkel = {"to top": 0, "to right": 90, "to bottom": 180, "to left": 270, "to top right": 45,
                          "to bottom right": 135, "to bottom left": 225, "to top left": 315}.get(t, 180)
        rad = math.radians(winkel)
        cx, cy = x + w / 2, y + h / 2
        halb = abs(w / 2 * math.sin(rad)) + abs(h / 2 * math.cos(rad))
        g = QLinearGradient(QPointF(cx - math.sin(rad) * halb, cy + math.cos(rad) * halb),
                            QPointF(cx + math.sin(rad) * halb, cy - math.cos(rad) * halb))
        stops = []
        for i, t in enumerate(teile):
            w_ = nemicss.werte(t)
            f = qfarbe(w_[0])
            pos = float(w_[1][:-1]) / 100 if len(w_) > 1 and w_[1].endswith("%") else None
            stops.append([f, pos])
        if not stops:
            return None
        for i, st in enumerate(stops):
            if st[1] is None:
                st[1] = i / max(1, len(stops) - 1)
        for f, pos in stops:
            if f is not None:
                g.setColorAt(max(0.0, min(1.0, pos)), f)
        return g

    # -- Texte ------------------------------------------------------------------------
    def _texte(self, p: QPainter, k: Kasten):
        deko = k.stil.get("text-decoration", "")
        for x, y, text, s, farbe in k.texte:
            f = schrift(s)
            if "underline" in deko:
                f = QFont(f)
                f.setUnderline(True)
            p.setFont(f)
            p.setPen(QColor(farbe[0], farbe[1], farbe[2], farbe[3]))
            p.drawText(QPointF(x, y + QFontMetricsF(f).ascent()), text)
        if k.marker:
            mx, my, mt = k.marker
            f = schrift(k.schrift)
            p.setFont(f)
            p.setPen(qfarbe(k.stil.get("color", "#000")) or QColor(0, 0, 0))
            p.drawText(QPointF(mx, my + QFontMetricsF(f).ascent()), mt)

    # -- Formular-Elemente und Bilder --------------------------------------------------------
    def _innen(self, k):
        return (k.x + k.rand[3] + k.polster[3], k.y + k.rand[0] + k.polster[0],
                max(0.0, k.w - k.rand[1] - k.rand[3] - k.polster[1] - k.polster[3]),
                max(0.0, k.h - k.rand[0] - k.rand[2] - k.polster[0] - k.polster[2]))

    def _ersetzt(self, p: QPainter, k: Kasten):
        el = k.el
        tag = el.tag
        if tag not in ("input", "select", "textarea", "img", "svg", "progress", "meter"):
            return
        x, y, w, h = self._innen(k)
        akzent = qfarbe(k.stil.get("accent-color", "#8b7cff")) or QColor(139, 124, 255)
        farbe = qfarbe(k.stil.get("color", "#000")) or QColor(0, 0, 0)
        if tag == "img":
            bild = self.bilder(el) if self.bilder else None
            if isinstance(bild, QImage) and not bild.isNull():
                p.drawImage(QRectF(x, y, w, h), bild)
            elif bild is not None and hasattr(bild, "render"):
                bild.render(p, QRectF(x, y, w, h))
            return
        if tag in ("progress", "meter"):
            try:
                v = float(el.attrs.get("value", "0")) / float(el.attrs.get("max", "1") or 1)
            except ValueError:
                v = 0.0
            p.fillPath(_rund_pfad(x, y, w, h, [h / 2] * 4), QColor(128, 128, 128, 70))
            p.fillPath(_rund_pfad(x, y, w * max(0.0, min(1.0, v)), h, [h / 2] * 4), akzent)
            return
        typ = el.attrs.get("type", "text").lower() if tag == "input" else tag
        if typ in ("checkbox", "radio"):
            box = QRectF(k.x + 1, k.y + 1, max(8.0, k.w - 2), max(8.0, k.h - 2))
            r = box.width() / 2 if typ == "radio" else 3.0
            pfad = _rund_pfad(box.x(), box.y(), box.width(), box.height(), [r] * 4)
            if el.checked:
                p.fillPath(pfad, akzent)
                p.setPen(QPen(QColor(255, 255, 255), 2))
                if typ == "radio":
                    p.setBrush(QColor(255, 255, 255))
                    c = box.center()
                    p.drawEllipse(c, box.width() / 5, box.width() / 5)
                else:
                    p.drawPolyline([QPointF(box.x() + box.width() * 0.22, box.y() + box.height() * 0.52),
                                    QPointF(box.x() + box.width() * 0.42, box.y() + box.height() * 0.72),
                                    QPointF(box.x() + box.width() * 0.78, box.y() + box.height() * 0.3)])
            else:
                p.setPen(QPen(QColor(140, 140, 140), 1.5))
                p.setBrush(QColor(255, 255, 255))
                p.drawPath(pfad)
            return
        if typ == "range":
            try:
                mn, mx = float(el.attrs.get("min", "0") or 0), float(el.attrs.get("max", "100") or 100)
                v = float(el.wert or (mn + mx) / 2)
            except ValueError:
                mn, mx, v = 0.0, 100.0, 50.0
            anteil = 0.0 if mx == mn else max(0.0, min(1.0, (v - mn) / (mx - mn)))
            mitte = k.y + k.h / 2
            p.fillPath(_rund_pfad(k.x, mitte - 3, k.w, 6, [3] * 4), QColor(128, 128, 128, 90))
            p.fillPath(_rund_pfad(k.x, mitte - 3, k.w * anteil, 6, [3] * 4), akzent)
            knopf = QPointF(k.x + 8 + (k.w - 16) * anteil, mitte)
            p.setPen(QPen(QColor(0, 0, 0, 60), 1))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(knopf, 8, 8)
            return
        if typ == "color":
            f = qfarbe(el.wert or "#000000") or QColor(0, 0, 0)
            r = max(0.0, (self._radien(k)[0] or 4) - 2)
            p.fillPath(_rund_pfad(x, y, w, h, [r] * 4), f)
            return
        if tag == "select":
            opt = el._gewaehlte_option()
            text = opt.text_inhalt().strip() if opt else ""
            self._text_in(p, k, text, x, y, w - 14, h, farbe)
            p.setPen(farbe)
            p.setFont(schrift(k.schrift))
            p.drawText(QRectF(x + w - 14, y, 14, h), Qt.AlignmentFlag.AlignCenter, "▾")
            return
        if typ in ("button", "submit", "reset"):
            self._text_in(p, k, el.attrs.get("value", "Senden" if typ == "submit" else ""), x, y, w, h, farbe,
                          mitte=True)
            return
        wert = el.wert
        if typ == "password":
            wert = "•" * len(wert)
        if not wert and el.attrs.get("placeholder"):
            grau = QColor(farbe)
            grau.setAlphaF(0.5)
            self._text_in(p, k, el.attrs["placeholder"], x, y, w, h, grau, mehrzeilig=tag == "textarea")
        else:
            self._text_in(p, k, wert, x, y, w, h, farbe, mehrzeilig=tag == "textarea",
                          caret=el is self.fokus)

    def _text_in(self, p, k, text, x, y, w, h, farbe, mitte=False, mehrzeilig=False, caret=False):
        f = schrift(k.schrift)
        p.save()
        p.setClipRect(QRectF(x, y - 1, w + 1, h + 2))
        p.setFont(f)
        p.setPen(farbe)
        m = QFontMetricsF(f)
        ausricht = k.stil.get("text-align", "left")
        if mehrzeilig:
            p.drawText(QRectF(x, y, w, h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) |
                       int(Qt.TextFlag.TextWordWrap), text)
            p.restore()
            return
        tw = m.horizontalAdvance(text)
        verschub = max(0.0, tw - w + 2) if caret else 0.0
        if mitte or ausricht == "center":
            tx = x + (w - tw) / 2 if tw <= w else x - verschub
        elif ausricht == "right":
            tx = x + w - tw
        else:
            tx = x - verschub
        ty = y + (h - m.height()) / 2 + m.ascent()
        p.drawText(QPointF(tx, ty), text)
        if caret:
            cx = tx + tw + 1
            p.setPen(QPen(farbe, 1.2))
            p.drawLine(QPointF(cx, y + (h - m.height()) / 2 + 1), QPointF(cx, y + (h + m.height()) / 2 - 1))
        p.restore()


def _laenge(v, bezug, fs, ctx):
    from .layout import laenge
    return laenge(v, bezug, fs, ctx)


def _farbe_tupel(s):
    return nemicss.farbe(s.get("color", "#000"), (0, 0, 0, 255)) or (0, 0, 0, 255)


def bild_laden(daten: bytes, name: str):
    """Bilddaten aus dem App-Ordner → QImage bzw. QSvgRenderer (für SVG)."""
    if name.lower().endswith(".svg") or daten[:200].lstrip().startswith((b"<svg", b"<?xml")):
        try:
            from PySide6.QtCore import QByteArray
            from PySide6.QtSvg import QSvgRenderer
            r = QSvgRenderer(QByteArray(daten))
            return r if r.isValid() else None
        except Exception:
            return None
    img = QImage()
    img.loadFromData(daten)
    return None if img.isNull() else img
