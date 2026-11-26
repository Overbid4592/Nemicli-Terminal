"""office_teile.py – gemeinsame Bausteine von Nemi Office: Werkzeugleiste, Statuszeile, App-Kachel, Rückfrage."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QMenu, QToolButton,
                               QVBoxLayout, QWidget)

from design import FARBE, farbe, schrift

# Farben der Apps (Verlauf von → nach)
APP_FARBEN = {
    "schreiben": ("#8b5cf6", "#3b82f6"),
    "tabelle": ("#10b981", "#06b6d4"),
    "malen": ("#ec4899", "#f97316"),
    "oeffnen": ("#f59e0b", "#ef4444"),
}

WERKZEUG_STIL = f"""
QToolButton {{ color: {FARBE['text']}; background: transparent; border: none; border-radius: 7px;
               padding: 4px 9px; min-height: 26px; }}
QToolButton:hover {{ background: {FARBE['flaeche_hell']}; }}
QToolButton:checked {{ background: {FARBE['flaeche_hoch']}; color: {FARBE['cyan']}; }}
QToolButton:pressed {{ background: {FARBE['flaeche_hoch']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QMenu {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
         border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {FARBE['flaeche_hell']}; }}
"""


# Farbpalette: Grautöne, Pastell (gut für Flächen), kräftige Farben (gut für Schrift)
PALETTE = (
    ("#ffffff", "#f2f2f2", "#d9d9d9", "#bfbfbf", "#a6a6a6", "#808080", "#595959", "#262626", "#000000"),
    ("#fff2cc", "#fce4d6", "#f8cbad", "#ffd6e0", "#e4dfec", "#ddebf7", "#d9f2f7", "#e2efda", "#ededed"),
    ("#ffc000", "#ed7d31", "#e74c3c", "#e91e63", "#8e44ad", "#2f75b5", "#00a3b4", "#70ad47", "#7f6000"),
)
FARB_NAMEN = {"#ffffff": "Weiß", "#f2f2f2": "Hellgrau", "#d9d9d9": "Grau 1", "#bfbfbf": "Grau 2", "#a6a6a6": "Grau 3",
              "#808080": "Grau 4", "#595959": "Dunkelgrau", "#262626": "Fast schwarz", "#000000": "Schwarz",
              "#fff2cc": "Pastellgelb", "#fce4d6": "Pastellorange", "#f8cbad": "Lachs", "#ffd6e0": "Rosa",
              "#e4dfec": "Flieder", "#ddebf7": "Hellblau", "#d9f2f7": "Eisblau", "#e2efda": "Mintgrün",
              "#ededed": "Silber", "#ffc000": "Gold", "#ed7d31": "Orange", "#e74c3c": "Rot", "#e91e63": "Pink",
              "#8e44ad": "Lila", "#2f75b5": "Blau", "#00a3b4": "Türkis", "#70ad47": "Grün", "#7f6000": "Braun"}


class _Farbfeld(QAbstractButton):
    def __init__(self, farbe: str, parent=None):
        super().__init__(parent)
        self.farbe = farbe
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setToolTip(FARB_NAMEN.get(farbe, farbe))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setBrush(QColor(self.farbe))
        p.setPen(QPen(farbe("cyan") if self.underMouse() else QColor(0, 0, 0, 60), 2 if self.underMouse() else 1))
        p.drawRoundedRect(r, 5, 5)


def farb_menue(eltern: QWidget, waehlen, titel: str = "", keine: str = "Keine Farbe") -> QMenu:
    """Menü mit Farbraster; waehlen("#rrggbb") oder waehlen("") für „keine Farbe“."""
    from PySide6.QtWidgets import QColorDialog, QGridLayout, QWidgetAction
    menue = QMenu(eltern)
    menue.setStyleSheet(WERKZEUG_STIL)
    if titel:
        kopf = menue.addAction(titel)
        kopf.setEnabled(False)
    feld = QWidget()
    gitter = QGridLayout(feld)
    gitter.setContentsMargins(8, 6, 8, 6)
    gitter.setSpacing(3)
    for z, reihe in enumerate(PALETTE):
        for s, c in enumerate(reihe):
            k = _Farbfeld(c)
            k.clicked.connect(lambda _=False, c=c: (menue.close(), waehlen(c)))
            gitter.addWidget(k, z, s)
    aktion = QWidgetAction(menue)
    aktion.setDefaultWidget(feld)
    menue.addAction(aktion)
    menue.addSeparator()
    menue.addAction("⬜  " + keine, lambda: waehlen(""))

    def eigene():
        c = QColorDialog.getColor(QColor("#ffffff"), eltern, "Eigene Farbe")
        if c.isValid():
            waehlen(c.name())
    menue.addAction("🎨  Eigene Farbe …", eigene)
    return menue


class Werkzeugknopf(QToolButton):
    def __init__(self, text: str, tipp: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setToolTip(tipp)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(schrift(10.5))
        self.setAutoRaise(True)


class FlussLayout(QLayout):
    """Ordnet Widgets nebeneinander an und bricht in eine neue Zeile um, wenn der Platz nicht reicht –
    kein Knopf wird gequetscht. Ein Eintrag mit Eigenschaft „rechts“ rückt in seiner Zeile nach rechts."""

    def __init__(self, parent=None, abstand: int = 2):
        super().__init__(parent)
        self._eintraege: list = []
        self.setSpacing(abstand)

    def addItem(self, item):  # noqa: N802 – Qt-Name
        self._eintraege.append(item)

    def count(self):
        return len(self._eintraege)

    def itemAt(self, i):  # noqa: N802
        return self._eintraege[i] if 0 <= i < len(self._eintraege) else None

    def takeAt(self, i):  # noqa: N802
        return self._eintraege.pop(i) if 0 <= i < len(self._eintraege) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, breite):  # noqa: N802
        return self._legen(QRect(0, 0, breite, 0), nur_messen=True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._legen(rect, nur_messen=False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        groesse = QSize()
        for item in self._eintraege:
            groesse = groesse.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return groesse + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _legen(self, rect, nur_messen: bool) -> int:
        m = self.contentsMargins()
        bereich = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        zeilen: list[list] = [[]]
        x = 0
        for item in self._eintraege:
            b = item.sizeHint().width()
            if zeilen[-1] and x + b > bereich.width():
                zeilen.append([])
                x = 0
            zeilen[-1].append(item)
            x += b + self.spacing()
        y = bereich.y()
        for zeile in zeilen:
            hoehe = max((it.sizeHint().height() for it in zeile), default=0)
            if not nur_messen:
                rechts = [it for it in zeile if it.widget() is not None and it.widget().property("rechts")]
                breite_rechts = sum(it.sizeHint().width() + self.spacing() for it in rechts)
                x = bereich.x()
                for it in zeile:
                    if it in rechts:
                        continue
                    h = it.sizeHint()
                    it.setGeometry(QRect(QPoint(x, y + (hoehe - h.height()) // 2), h))
                    x += h.width() + self.spacing()
                x = max(x, bereich.right() + 1 - breite_rechts)
                for it in rechts:
                    h = it.sizeHint()
                    it.setGeometry(QRect(QPoint(x, y + (hoehe - h.height()) // 2), h))
                    x += h.width() + self.spacing()
            y += hoehe + self.spacing()
        return y - self.spacing() - rect.y() + m.bottom()


class Leiste(QFrame):
    """Werkzeugleiste: flache Knöpfe und Trenner; bricht bei wenig Platz in eine zweite Zeile um."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("leiste")
        self.setStyleSheet(f"QFrame#leiste {{ background: {FARBE['leiste']}; border-bottom: 1px solid {FARBE['rand']}; }}"
                           + WERKZEUG_STIL)
        self.h = FlussLayout(self)
        self.h.setContentsMargins(10, 5, 10, 5)
        self._rechts = False

    def knopf(self, text: str, tipp: str, fn, *, umschalt: bool = False, fett: bool = False, kursiv: bool = False,
              unter: bool = False, durch: bool = False, breit: bool = False) -> Werkzeugknopf:
        k = Werkzeugknopf(text, tipp)
        f = schrift(10.5, fett)
        f.setItalic(kursiv)
        f.setUnderline(unter)
        f.setStrikeOut(durch)
        k.setFont(f)
        if umschalt:
            k.setCheckable(True)
            k.toggled.connect(fn)
        else:
            k.clicked.connect(lambda _=False: fn())
        if not breit:
            k.setMinimumWidth(30)
        self.dazu(k)
        return k

    def dazu(self, w: QWidget) -> None:
        if self._rechts:
            w.setProperty("rechts", True)
        self.h.addWidget(w)

    def farbknopf(self, text: str, tipp: str, waehlen, titel: str = "", keine: str = "Keine Farbe") -> Werkzeugknopf:
        """Knopf mit Farbraster-Menü."""
        k = Werkzeugknopf(text, tipp)
        k.setMenu(farb_menue(k, waehlen, titel, keine))
        k.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.dazu(k)
        return k

    def trenner(self) -> None:
        t = QWidget()
        t.setFixedSize(11, 20)
        t.paintEvent = lambda _e, t=t: _trennstrich(t)
        self.dazu(t)

    def dehnen(self) -> None:
        """Alles danach rückt in seiner Zeile nach rechts."""
        self._rechts = True


def _trennstrich(w: QWidget) -> None:
    p = QPainter(w)
    p.fillRect(w.width() // 2, 0, 1, w.height(), farbe("rand"))


class Statuszeile(QFrame):
    """Unten: Meldung links (verblasst nach einer Weile), Zähler rechts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statuszeile")
        self.setStyleSheet(f"QFrame#statuszeile {{ background: {FARBE['leiste']}; border-top: 1px solid {FARBE['rand']}; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 3, 14, 3)
        self.links = QLabel("")
        self.links.setFont(schrift(9.5))
        self.rechts_lb = QLabel("")
        self.rechts_lb.setFont(schrift(9.5))
        self._farbe(self.rechts_lb, "blass")
        h.addWidget(self.links, 1)
        h.addWidget(self.rechts_lb)
        self._uhr = QTimer(self)
        self._uhr.setSingleShot(True)
        self._uhr.timeout.connect(lambda: self.links.setText(""))

    @staticmethod
    def _farbe(lb: QLabel, name: str) -> None:
        pal = lb.palette()
        pal.setColor(lb.foregroundRole(), farbe(name))
        lb.setPalette(pal)

    def zeigen(self, text: str, art: str = "dim", sekunden: float = 6.0) -> None:
        self.links.setText(text)
        self._farbe(self.links, {"ok": "ok", "warn": "warn", "fehler": "fehler"}.get(art, "dim"))
        if sekunden:
            self._uhr.start(int(sekunden * 1000))

    def rechts(self, text: str) -> None:
        self.rechts_lb.setText(text)


def melden(parent=None) -> Statuszeile:
    return Statuszeile(parent)


class AppKachel(QAbstractButton):
    """Große runde Kachel mit Farbverlauf, Zeichen (Text oder eigenes Symbol als QPixmap), Titel und Untertitel."""

    def __init__(self, zeichen: "str | QPixmap", titel: str, unter: str, farben: tuple[str, str], parent=None):
        super().__init__(parent)
        self.zeichen, self.titel, self.unter, self.farben = zeichen, titel, unter, farben
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedSize(212, 150)
        self.setToolTip(f"{titel} – {unter}")

    def sizeHint(self) -> QSize:
        return QSize(212, 150)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.underMouse():
            r = r.adjusted(-2, -2, 2, 2)
        pfad = QPainterPath()
        pfad.addRoundedRect(r, 20, 20)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        a, b = QColor(self.farben[0]), QColor(self.farben[1])
        if not self.underMouse():
            a.setAlpha(225)
            b.setAlpha(225)
        g.setColorAt(0, a)
        g.setColorAt(1, b)
        p.fillPath(pfad, g)
        if self.hasFocus():
            p.setPen(QPen(QColor(255, 255, 255, 200), 2))
            p.drawPath(pfad)
        p.setPen(QColor("#ffffff"))
        f = QFont(self.font())
        f.setPointSizeF(30)
        p.setFont(f)
        if isinstance(self.zeichen, QPixmap):
            p.drawPixmap(QRectF(r.left() + 16, r.top() + 12, 54, 54), self.zeichen, QRectF(self.zeichen.rect()))
        else:
            p.drawText(QRectF(r.left() + 18, r.top() + 12, 80, 56),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.zeichen)
        p.setFont(schrift(15, True))
        p.drawText(QRectF(r.left() + 20, r.top() + 72, r.width() - 30, 30), Qt.AlignmentFlag.AlignLeft, self.titel)
        p.setFont(schrift(9.5))
        p.setPen(QColor(255, 255, 255, 215))
        p.drawText(QRectF(r.left() + 20, r.top() + 102, r.width() - 30, 36),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.unter)


def waehlen(parent: QWidget, frage: str, knoepfe: list[tuple[str, str, bool]], standard: str) -> str:
    """Kleiner Dialog im Office-Aussehen; knoepfe = [(Text, Wert, Akzent)]. Schließen ergibt `standard`."""
    from gui_fenster import Knopf, _dunkler_rahmen, label
    d = QDialog(parent)
    d.setWindowTitle("Nemi Office")
    d.setStyleSheet(f"QDialog {{ background: {FARBE['grund']}; }}")
    v = QVBoxLayout(d)
    v.setContentsMargins(26, 22, 26, 20)
    v.setSpacing(14)
    v.addWidget(label(frage, 11))
    h = QHBoxLayout()
    wahl = {"w": standard}
    for text, wert, akzent in knoepfe:
        k = Knopf(text, mitte=True)
        k.akzent = akzent
        k.clicked.connect(lambda _=False, w=wert: (wahl.__setitem__("w", w), d.accept()))
        h.addWidget(k)
    v.addLayout(h)
    _dunkler_rahmen(d)
    d.exec()
    return wahl["w"]


def frage_speichern(parent: QWidget, name: str) -> str:
    """„speichern“, „verwerfen“ oder „abbrechen“ für ein geändertes Dokument."""
    return waehlen(parent, f"„{name}“ hat Änderungen, die noch nicht gespeichert sind.",
                   [("Speichern", "speichern", True), ("Nicht speichern", "verwerfen", False),
                    ("Abbrechen", "abbrechen", False)], "abbrechen")


def ja_nein(parent: QWidget, frage: str, ja: str = "Ja", nein: str = "Nein") -> bool:
    return waehlen(parent, frage, [(ja, "ja", False), (nein, "nein", True)], "nein") == "ja"
