"""gui_fenster.py – NemiCLIs Desktop-Oberfläche (PySide6, selbst gezeichnet).

Ein eigener Prozess, gestartet mit /gui aus einer laufenden Sitzung. Er verbindet
sich über die Named Pipe aus NEMICLI_GUI_PIPE / NEMICLI_GUI_KEY (gui_bruecke.py);
Chat, Modell, Werkzeuge und Gedächtnis bleiben in der Sitzung – das Fenster zeigt
nur an und schickt Eingaben. Nachrichten sind JSON.

Oben „Chat | Work“: Chat nur mit Bildern und Gedächtnis, Work mit allen Werkzeugen
(Nachfragen erscheinen als Karte). Farben liegen in FARBE, gezeichnet wird mit QPainter.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from multiprocessing.connection import Client
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImageReader,
                           QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QScrollBar,
                               QSizePolicy,
                               QStackedWidget, QStyle, QStyleOptionSlider, QTextBrowser, QVBoxLayout, QWidget)

MAX_NACHRICHT = 48 * 1024 * 1024
SPALTE = 760                                  # Breite der Chat-Spalte

FARBE = {
    "grund": "#0c0c10", "leiste": "#111116", "flaeche": "#1b1b22", "flaeche_hell": "#26262f",
    "rand": "#2c2c36", "text": "#ececf1", "dim": "#a3a3ae", "blass": "#6f6f7b",
    "cyan": "#22d3ee", "lila": "#a855f7", "blase_a": "#4c1d95", "blase_b": "#155e75",
    "ok": "#34d399", "warn": "#fbbf24", "fehler": "#f87171",
}

VORSCHLAEGE = {
    "chat": [("🎨", "Male mir ein Bild von einem Sonnenuntergang am Meer"),
             ("🧠", "Woran erinnerst du dich von unserem letzten Gespräch?"),
             ("✦", "Erzähl mir etwas über dich")],
    "work": [("📂", "Schau dir den Arbeitsordner an und fasse ihn kurz zusammen"),
             ("🔎", "Suche in meinen Dateien nach Notizen zu einem Thema"),
             ("🛡", "Wie geht es meinem System gerade?")],
}

_BILDPFAD = re.compile(r"[A-Za-z]:\\[^\n\"<>|*?]+?\.(?:png|jpe?g|webp)", re.IGNORECASE)


def farbe(name: str, alpha: int | None = None) -> QColor:
    c = QColor(FARBE[name])
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def verlauf_farbe(r: QRectF, a: str = "cyan", b: str = "lila") -> QLinearGradient:
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0.0, farbe(a))
    g.setColorAt(1.0, farbe(b))
    return g


_FAMILIE: list[str] = []


def schrift(groesse: float = 10.5, fett: bool = False) -> QFont:
    if not _FAMILIE:
        familien = set(QFontDatabase.families())
        _FAMILIE.append(next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in familien), ""))
    f = QFont(_FAMILIE[0]) if _FAMILIE[0] else QFont()
    f.setPointSizeF(groesse)
    if fett:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def label(text: str = "", groesse: float = 10.5, farbname: str = "text", fett: bool = False,
          umbruch: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setFont(schrift(groesse, fett))
    pal = lb.palette()
    pal.setColor(QPalette.ColorRole.WindowText, farbe(farbname))
    lb.setPalette(pal)
    lb.setWordWrap(umbruch)
    lb.setTextFormat(Qt.TextFormat.PlainText)
    return lb


def vorschau(pfad: str, max_seite: int) -> QPixmap:
    """Bild verkleinert laden (liest nur so viel, wie nötig)."""
    leser = QImageReader(pfad)
    leser.setAutoTransform(True)
    groesse = leser.size()
    if groesse.isValid() and max(groesse.width(), groesse.height()) > max_seite:
        leser.setScaledSize(groesse.scaled(max_seite, max_seite, Qt.AspectRatioMode.KeepAspectRatio))
    return QPixmap.fromImage(leser.read())


# ---------------------------------------------------------------------------
# Verbindung zur Sitzung
# ---------------------------------------------------------------------------

class Leitung(QObject):
    ereignis = Signal(object)
    getrennt = Signal()

    def __init__(self, adresse: str, schluessel: bytes):
        super().__init__()
        self.conn = Client(adresse, family="AF_PIPE", authkey=schluessel)
        self._lock = threading.Lock()
        threading.Thread(target=self._lesen, name="gui-lesen", daemon=True).start()

    def _lesen(self) -> None:
        try:
            while True:
                roh = self.conn.recv_bytes(MAX_NACHRICHT)
                try:
                    ev = json.loads(roh.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    self.ereignis.emit(ev)
        except (EOFError, OSError):
            self.getrennt.emit()

    def senden(self, auftrag: dict) -> None:
        daten = json.dumps(auftrag, ensure_ascii=False).encode("utf-8")
        with self._lock:
            try:
                self.conn.send_bytes(daten)
            except (OSError, ValueError):
                self.getrennt.emit()


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------

class Knopf(QAbstractButton):
    """Flacher Knopf: Symbol + Text, runder Hover-Hintergrund."""

    def __init__(self, text: str, symbol: str = "", parent=None, *, klein: bool = False, mitte: bool = False):
        super().__init__(parent)
        self.setText(text)
        self.symbol = symbol
        self.aktiv = False
        self.akzent = False
        self.klein = klein
        self.mitte = mitte
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(schrift(9.5 if klein else 10.5))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        breite = fm.horizontalAdvance(self._anzeige())
        return QSize(breite + 26, 28 if self.klein else 34)

    def _anzeige(self) -> str:
        if self.symbol and self.text():
            return f"{self.symbol}   {self.text()}"
        return self.symbol or self.text()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        if self.akzent and self.isEnabled():
            p.setBrush(verlauf_farbe(r))
            p.drawRoundedRect(r, 9, 9)
        elif self.aktiv or self.isDown() or (self.underMouse() and self.isEnabled()):
            p.setBrush(farbe("flaeche_hell" if (self.aktiv or self.isDown()) else "flaeche"))
            p.drawRoundedRect(r, 9, 9)
        hell = self.isEnabled() and (self.aktiv or self.akzent or self.underMouse())
        p.setPen(farbe("text") if hell else farbe("dim" if self.isEnabled() else "blass"))
        p.setFont(self.font())
        mitte = self.mitte or not self.text()
        rt = r.adjusted(4 if mitte else 12, 0, -6, 0)
        text = self.fontMetrics().elidedText(self._anzeige(), Qt.TextElideMode.ElideRight, int(rt.width()))
        ausricht = Qt.AlignmentFlag.AlignCenter if mitte else \
            (Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        p.drawText(rt, int(ausricht), text)


class RundKnopf(QAbstractButton):
    """Runder Knopf: „senden“ (Verlauf Cyan→Lila mit Pfeil) oder „plus“."""

    def __init__(self, art: str, parent=None):
        super().__init__(parent)
        self.art = art
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        m = r.center()
        if self.art == "senden":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(verlauf_farbe(r) if self.isEnabled() else farbe("flaeche_hell"))
            p.drawEllipse(r)
            stift = QPen(QColor("#ffffff") if self.isEnabled() else farbe("blass"), 2.2)
            stift.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(stift)
            p.drawLine(QPointF(m.x(), m.y() + 7), QPointF(m.x(), m.y() - 7))
            p.drawLine(QPointF(m.x() - 6, m.y() - 1), QPointF(m.x(), m.y() - 7))
            p.drawLine(QPointF(m.x() + 6, m.y() - 1), QPointF(m.x(), m.y() - 7))
            return
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(farbe("flaeche_hell"))
            p.drawEllipse(r)
        stift = QPen(farbe("text"), 1.8)
        stift.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(stift)
        p.drawLine(QPointF(m.x() - 7, m.y()), QPointF(m.x() + 7, m.y()))
        p.drawLine(QPointF(m.x(), m.y() - 7), QPointF(m.x(), m.y() + 7))


class DuenneLeiste(QScrollBar):
    """Schmale Bildlaufleiste: nur ein runder Griff."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setFixedWidth(10)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _):
        if self.maximum() <= self.minimum():
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        griff = self.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, opt,
                                            QStyle.SubControl.SC_ScrollBarSlider, self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(farbe("rand" if not self.underMouse() else "blass"))
        r = QRectF(griff).adjusted(3, 2, -3, -2)
        p.drawRoundedRect(r, r.width() / 2, r.width() / 2)


class SanfterBereich(QScrollArea):
    """Rollbereich mit gleitendem Mausrad; Touchpads (Pixel-Schritte) scrollen direkt."""
    nutzer_ziel = Signal(int)                      # wohin der Nutzer gescrollt hat

    SCHRITT = 110                                  # Pixel je Rasterstufe des Mausrads

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBar(DuenneLeiste())
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def sanft_zu(self, ziel: int, dauer: int = 170) -> None:
        leiste = self.verticalScrollBar()
        ziel = max(leiste.minimum(), min(leiste.maximum(), int(ziel)))
        if self._anim.state() == QPropertyAnimation.State.Running:
            if self._anim.endValue() == ziel:
                return
            self._anim.stop()
        if ziel == leiste.value():
            return
        self._anim.setDuration(dauer)
        self._anim.setStartValue(leiste.value())
        self._anim.setEndValue(ziel)
        self._anim.start()

    def wheelEvent(self, e):
        leiste = self.verticalScrollBar()
        pixel = e.pixelDelta().y()
        if pixel:
            self._anim.stop()
            leiste.setValue(leiste.value() - pixel)
            ziel = leiste.value()
        else:
            laeuft = self._anim.state() == QPropertyAnimation.State.Running
            basis = int(self._anim.endValue()) if laeuft else leiste.value()
            ziel = max(leiste.minimum(), min(leiste.maximum(),
                                              int(basis - e.angleDelta().y() / 120 * self.SCHRITT)))
            self.sanft_zu(ziel)
        self.nutzer_ziel.emit(ziel)
        e.accept()


def rollbereich(inhalt: QWidget) -> SanfterBereich:
    s = SanfterBereich()
    s.setWidget(inhalt)
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.Shape.NoFrame)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    s.viewport().setAutoFillBackground(False)
    inhalt.setAutoFillBackground(False)
    return s


class Flaeche(QWidget):
    """Runde Fläche als Hintergrund für Karten und die Eingabe."""

    def __init__(self, parent=None, *, radius: float = 14, farbname: str = "flaeche", rand: bool = True):
        super().__init__(parent)
        self.radius = radius
        self.farbname = farbname
        self.rand = rand
        self.leuchten = False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(farbe(self.farbname))
        if self.leuchten:
            p.setPen(QPen(verlauf_farbe(r), 1.2))
        elif self.rand:
            p.setPen(QPen(farbe("rand"), 1))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, self.radius, self.radius)


class Logo(QWidget):
    """„NemiCLI“ in Cyan→Lila."""

    def __init__(self, groesse: float = 15, parent=None):
        super().__init__(parent)
        self.f = schrift(groesse, fett=True)
        self.setFixedHeight(int(groesse * 2.4))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pfad = QPainterPath()
        pfad.addText(QPointF(2, self.height() * 0.72), self.f, "NemiCLI")
        r = pfad.boundingRect()
        p.fillPath(pfad, verlauf_farbe(QRectF(r.left(), r.top(), r.width(), r.height() * 0.2)))


class Avatar(QWidget):
    """Runder Verlauf mit Anfangsbuchstaben."""

    def __init__(self, groesse: int = 30, parent=None):
        super().__init__(parent)
        self.setFixedSize(groesse, groesse)
        self.buchstabe = "N"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(verlauf_farbe(r))
        p.drawEllipse(r)
        p.setPen(QColor("#ffffff"))
        p.setFont(schrift(self.width() / 2.6, fett=True))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self.buchstabe)


class Umschalter(QWidget):
    """Pille mit zwei Hälften: Chat | Work."""
    gewechselt = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.teile = [("chat", "Chat"), ("work", "Work")]
        self.wert = "chat"
        self.setFixedSize(190, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setzen(self, wert: str) -> None:
        if wert != self.wert:
            self.wert = wert
            self.update()
            self.gewechselt.emit(wert)

    def mousePressEvent(self, e):
        self.setzen(self.teile[0 if e.position().x() < self.width() / 2 else 1][0])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(farbe("flaeche"))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        halb = r.width() / 2
        for i, (wert, text) in enumerate(self.teile):
            teil = QRectF(r.left() + i * halb, r.top(), halb, r.height()).adjusted(3, 3, -3, -3)
            gewaehlt = wert == self.wert
            if gewaehlt:
                p.setBrush(farbe("flaeche_hell"))
                p.setPen(QPen(verlauf_farbe(teil), 1))
                p.drawRoundedRect(teil, teil.height() / 2, teil.height() / 2)
            p.setPen(farbe("text" if gewaehlt else "dim"))
            p.setFont(schrift(10, fett=gewaehlt))
            p.drawText(teil, int(Qt.AlignmentFlag.AlignCenter), text)


class Tippen(QWidget):
    """Drei pulsierende Punkte, solange die KI arbeitet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 22)
        self.phase = 0
        self.uhr = QTimer(self)
        self.uhr.timeout.connect(self._weiter)

    def start(self):
        self.show()
        self.uhr.start(160)

    def stop(self):
        self.uhr.stop()
        self.hide()

    def _weiter(self):
        self.phase = (self.phase + 1) % 6
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            an = (self.phase // 2) == i
            p.setBrush(farbe("cyan" if an else "blass") if i < 2 else farbe("lila" if an else "blass"))
            d = 7 if an else 5
            p.drawEllipse(QPointF(10 + i * 16, 11), d / 2, d / 2)


# ---------------------------------------------------------------------------
# Inhalt der Chat-Spalte
# ---------------------------------------------------------------------------

class Markdown(QTextBrowser):
    """Antworttext: Markdown von Qt dargestellt, Höhe wächst mit dem Inhalt.

    Der Text kommt vom Modell und kann fremde Inhalte enthalten: kein HTML, keine
    nachgeladenen Ressourcen (ein file://-Pfad auf einen Netzrechner würde eine
    Windows-Anmeldung auslösen), Links öffnen nur mit http/https.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._link)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFont(schrift(11))
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Text, farbe("text"))
        pal.setColor(QPalette.ColorRole.Link, farbe("cyan"))
        self.setPalette(pal)
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)
        self.roh = ""

    def setzen(self, text: str) -> None:
        self.roh = text
        self.document().setMarkdown(text, QTextDocument.MarkdownFeature(
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub.value
            | QTextDocument.MarkdownFeature.MarkdownNoHTML.value))
        self._hoehe()

    def loadResource(self, typ, name):
        return None

    @staticmethod
    def _link(url: QUrl) -> None:
        if url.scheme().lower() in ("http", "https") and url.host():
            QDesktopServices.openUrl(url)

    def _hoehe(self):
        self.document().setTextWidth(max(50, self.viewport().width()))
        self.setFixedHeight(int(self.document().size().height()) + 6)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._hoehe()


class NutzerBlase(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        zeile = QHBoxLayout(self)
        zeile.setContentsMargins(0, 6, 0, 6)
        zeile.addStretch(1)
        self.blase = QWidget()
        self.blase.paintEvent = self._malen
        innen = QVBoxLayout(self.blase)
        innen.setContentsMargins(16, 10, 16, 10)
        lb = label(text, 11, "text")
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lb.setMaximumWidth(int(SPALTE * 0.68))
        innen.addWidget(lb)
        zeile.addWidget(self.blase)

    def _malen(self, _):
        p = QPainter(self.blase)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.blase.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(verlauf_farbe(r, "blase_a", "blase_b"))
        p.drawRoundedRect(r, 18, 18)


class KiBlock(QWidget):
    """Eine Antwort: aufklappbare Gedanken, Text, darunter Kopieren."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fertig = False
        self.gedanken_text = ""
        self.antwort = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(6)
        self.gedanken_knopf = Knopf("Gedanken", "💭", klein=True)
        self.gedanken_knopf.clicked.connect(self._gedanken_umschalten)
        self.gedanken_knopf.hide()
        kopf = QHBoxLayout()
        kopf.setContentsMargins(0, 0, 0, 0)
        kopf.addWidget(self.gedanken_knopf)
        kopf.addStretch(1)
        v.addLayout(kopf)
        self.gedanken = label("", 9.5, "blass")
        self.gedanken.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.gedanken.hide()
        v.addWidget(self.gedanken)
        self.text = Markdown()
        v.addWidget(self.text)
        self.leiste = QHBoxLayout()
        self.leiste.setContentsMargins(0, 0, 0, 0)
        self.kopieren = Knopf("Kopieren", "⧉", klein=True)
        self.kopieren.clicked.connect(self._kopieren)
        self.kopieren.hide()
        self.leiste.addWidget(self.kopieren)
        self.leiste.addStretch(1)
        v.addLayout(self.leiste)
        self._uhr = QTimer(self)
        self._uhr.setSingleShot(True)
        self._uhr.timeout.connect(self._zeichnen)
        self._takt = 80                            # ms; wächst mit der Zeichendauer langer Antworten

    def denken(self, delta: str) -> None:
        self.gedanken_text += delta
        self.gedanken_knopf.show()
        self._planen()

    def schreiben(self, delta: str) -> None:
        self.antwort += delta
        self._planen()

    def _planen(self) -> None:
        if not self._uhr.isActive():
            self._uhr.start(self._takt)

    def _zeichnen(self) -> None:
        """Neu setzen, was seit dem letzten Takt kam. Dauert das Setzen länger (lange
        Antworten), wird seltener gezeichnet – das Fenster bleibt flüssig."""
        t0 = time.perf_counter()
        if self.gedanken.isVisible():
            self.gedanken.setText(self.gedanken_text[-4000:])
        if self.antwort:
            self.text.setzen(self.antwort)
        dauer_ms = (time.perf_counter() - t0) * 1000
        self._takt = int(min(400, max(80, dauer_ms * 4)))

    def abschliessen(self, sauber: str | None) -> None:
        self._uhr.stop()
        self.fertig = True
        if sauber is not None:
            self.antwort = sauber
        self.text.setzen(self.antwort)
        self.text.setVisible(bool(self.antwort.strip()))
        self.kopieren.setVisible(bool(self.antwort.strip()))
        if self.gedanken_text:
            self.gedanken.setText(self.gedanken_text[-4000:])
            self.gedanken_knopf.setText(f"Gedanken ({len(self.gedanken_text) // 4} Token)")

    def _gedanken_umschalten(self):
        if not self.gedanken.isVisible():
            self.gedanken.setText(self.gedanken_text[-4000:])
        self.gedanken.setVisible(not self.gedanken.isVisible())

    def _kopieren(self):
        QApplication.clipboard().setText(self.antwort)
        self.kopieren.setText("Kopiert")
        QTimer.singleShot(1500, lambda: self.kopieren.setText("Kopieren"))


class BildKachel(QLabel):
    geklickt = Signal(str)

    def __init__(self, pfad: str, seite: int, parent=None):
        super().__init__(parent)
        self.pfad = pfad
        self.seite = seite
        self.bild = vorschau(pfad, seite)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self.bild.isNull():
            self.setFixedSize(self.bild.size())

    def paintEvent(self, _):
        if self.bild.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pfad = QPainterPath()
        pfad.addRoundedRect(QRectF(self.rect()), 12, 12)
        p.setClipPath(pfad)
        p.drawPixmap(0, 0, self.bild)

    def mousePressEvent(self, _):
        self.geklickt.emit(self.pfad)


class Karte(Flaeche):
    """Aktion, Ergebnis oder Nachfrage der KI."""
    antwort = Signal(object, str)

    def __init__(self, titel: str, symbol: str = "⚙", parent=None):
        super().__init__(parent, radius=12)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(14, 10, 14, 10)
        self.v.setSpacing(6)
        self.kopf = label(f"{symbol}  {titel}", 10, "dim")
        self.kopf.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.v.addWidget(self.kopf)

    def ergebnis(self, text: str, ok) -> list[str]:
        zeichen = {True: "✓", False: "✗"}.get(ok, "•")
        farbname = {True: "ok", False: "fehler"}.get(ok, "dim")
        kurz = text.strip()
        if len(kurz) > 900:
            kurz = kurz[:900] + " …"
        lb = label(f"{zeichen}  {kurz}" if kurz else zeichen, 9.5, farbname)
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.v.addWidget(lb)
        return [m.group(0) for m in _BILDPFAD.finditer(text or "") if Path(m.group(0)).is_file()]

    def frage(self, fid, optionen: list, vorschau_text: str | None) -> None:
        self.leuchten = True
        self.update()
        if vorschau_text:                              # vollständig wie im Terminal – nie gekürzt freigeben
            vs = QPlainTextEdit()
            vs.setReadOnly(True)
            vs.setFrameShape(QFrame.Shape.NoFrame)
            vs.setFont(QFont("Consolas", 9))
            vs.setVerticalScrollBar(DuenneLeiste())
            pal = vs.palette()
            pal.setColor(QPalette.ColorRole.Base, farbe("grund"))
            pal.setColor(QPalette.ColorRole.Text, farbe("dim"))
            vs.setPalette(pal)
            vs.setPlainText(vorschau_text)
            zeilen = len(vorschau_text.splitlines()) or 1
            vs.setFixedHeight(int(min(360, zeilen * vs.fontMetrics().lineSpacing() + 16)))
            self.v.addWidget(vs)
        zeile = QHBoxLayout()
        zeile.setContentsMargins(0, 4, 0, 0)
        self.knoepfe = []
        for i, (wert, text) in enumerate(optionen):
            k = Knopf(text, mitte=True, klein=True)
            k.akzent = i == 0
            k.clicked.connect(lambda _=False, w=wert, t=text: self._gewaehlt(fid, w, t))
            zeile.addWidget(k)
            self.knoepfe.append(k)
        zeile.addStretch(1)
        self.v.addLayout(zeile)

    def _gewaehlt(self, fid, wert: str, text: str) -> None:
        for k in self.knoepfe:
            k.setEnabled(False)
            k.akzent = False
            k.aktiv = k.text() == text
            k.update()
        self.leuchten = False
        self.update()
        self.antwort.emit(fid, wert)


class Fortschritt(Flaeche):
    def __init__(self, titel: str, parent=None):
        super().__init__(parent, radius=12)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        self.text = label(titel, 10, "dim")
        v.addWidget(self.text)
        self.balken = QWidget()
        self.balken.setFixedHeight(6)
        self.balken.paintEvent = self._malen
        v.addWidget(self.balken)
        self.anteil = 0.0

    def setzen(self, text: str, anteil: float | None) -> None:
        self.text.setText(text)
        if anteil is not None:
            self.anteil = max(0.0, min(1.0, anteil))
        self.balken.update()

    def _malen(self, _):
        p = QPainter(self.balken)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.balken.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(farbe("flaeche_hell"))
        p.drawRoundedRect(r, 3, 3)
        if self.anteil > 0:
            voll = QRectF(r.left(), r.top(), r.width() * self.anteil, r.height())
            p.setBrush(verlauf_farbe(r))
            p.drawRoundedRect(voll, 3, 3)


class BildDialog(QDialog):
    """Bild groß ansehen; Klick oder Esc schließt."""

    def __init__(self, pfad: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(pfad).name)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, farbe("grund"))
        self.setPalette(pal)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        bildschirm = QApplication.primaryScreen().availableGeometry()
        bild = BildKachel(pfad, int(min(bildschirm.width(), bildschirm.height()) * 0.8))
        bild.geklickt.connect(lambda _: self.accept())
        v.addWidget(bild, alignment=Qt.AlignmentFlag.AlignCenter)
        _dunkler_rahmen(self)


# ---------------------------------------------------------------------------
# Eingabe
# ---------------------------------------------------------------------------

class Textfeld(QPlainTextEdit):
    abschicken = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFont(schrift(11))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Text, farbe("text"))
        pal.setColor(QPalette.ColorRole.PlaceholderText, farbe("blass"))
        self.setPalette(pal)
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(4)
        self.textChanged.connect(self._hoehe)
        self._hoehe()

    def _hoehe(self):
        zeilen = max(1, min(8, int(self.document().size().height())))
        self.setFixedHeight(int(zeilen * self.fontMetrics().lineSpacing() + 14))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.abschicken.emit()
            return
        super().keyPressEvent(e)


class Eingabe(QWidget):
    """Runde Eingabe mit +, Textfeld und Senden; darunter (Work) Ordner und Dateien."""
    senden = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.anhaenge: list[str] = []
        self.setAcceptDrops(True)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.chips = QHBoxLayout()
        self.chips.setContentsMargins(6, 0, 0, 0)
        self.chips.addStretch(1)
        v.addLayout(self.chips)
        self.pille = Flaeche(radius=24)
        zeile = QHBoxLayout(self.pille)
        zeile.setContentsMargins(10, 7, 8, 7)
        zeile.setSpacing(8)
        self.plus = RundKnopf("plus")
        self.plus.setToolTip("Bilder oder Dateien anhängen")
        self.plus.clicked.connect(self._waehlen)
        self.feld = Textfeld()
        self.feld.abschicken.connect(self._abschicken)
        self.feld.installEventFilter(self)
        self.knopf = RundKnopf("senden")
        self.knopf.clicked.connect(self._abschicken)
        zeile.addWidget(self.plus, alignment=Qt.AlignmentFlag.AlignBottom)
        zeile.addWidget(self.feld, 1)
        zeile.addWidget(self.knopf, alignment=Qt.AlignmentFlag.AlignBottom)
        v.addWidget(self.pille)
        self.extra = QHBoxLayout()
        self.extra.setContentsMargins(10, 0, 10, 0)
        self.ordner = Knopf("", "📁", klein=True)
        self.ordner.setEnabled(False)
        self.dateien = Knopf("Dateien", "📎", klein=True)
        self.dateien.clicked.connect(self._waehlen)
        self.extra.addWidget(self.ordner)
        self.extra.addWidget(self.dateien)
        self.extra.addStretch(1)
        self.extra_w = QWidget()
        self.extra_w.setLayout(self.extra)
        v.addWidget(self.extra_w)
        self.modus("chat")

    def eventFilter(self, obj, e):
        if obj is self.feld and e.type() in (e.Type.FocusIn, e.Type.FocusOut):
            self.pille.leuchten = e.type() == e.Type.FocusIn
            self.pille.update()
        return False

    def modus(self, wert: str, ordner: str = "") -> None:
        self.feld.setPlaceholderText("Mit NemiCLI arbeiten …" if wert == "work" else "Frag NemiCLI …")
        self.extra_w.setVisible(wert == "work")
        if ordner:
            self.ordner.setText(Path(ordner).name or ordner)
            self.ordner.setToolTip(ordner)
            self.ordner.updateGeometry()

    def beschaeftigt(self, an: bool) -> None:
        self.knopf.setEnabled(not an)

    # -- Anhänge -------------------------------------------------------------
    def _waehlen(self):
        dateien, _ = QFileDialog.getOpenFileNames(self, "Anhängen")
        for d in dateien:
            self._anhaengen(d)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self._anhaengen(url.toLocalFile())

    def _anhaengen(self, pfad: str) -> None:
        if pfad in self.anhaenge:
            return
        self.anhaenge.append(pfad)
        chip = Knopf(Path(pfad).name, "✕", klein=True)
        chip.aktiv = True
        chip.setToolTip("Entfernen")
        chip.clicked.connect(lambda _=False, c=chip, p=pfad: self._entfernen(c, p))
        self.chips.insertWidget(self.chips.count() - 1, chip)

    def _entfernen(self, chip, pfad):
        if pfad in self.anhaenge:
            self.anhaenge.remove(pfad)
        chip.deleteLater()

    def _abschicken(self):
        if not self.knopf.isEnabled():
            return
        text = self.feld.toPlainText().strip()
        # Anhänge gehen als Pfad wie beim Drag & Drop ins Terminal: die Sitzung liest
        # Text/PDF, wandelt Bildformate und nutzt bei Bedarf den Bildbeschreiber.
        pfade = [f'"{os.path.normpath(p)}"' for p in self.anhaenge if os.path.exists(p)]
        if pfade:
            text = (text + "\n" + " ".join(pfade)).strip()
        if not text:
            return
        self.senden.emit(text)
        self.feld.clear()
        for i in reversed(range(self.chips.count() - 1)):
            w = self.chips.itemAt(i).widget()
            if w is not None:
                w.deleteLater()
        self.anhaenge.clear()


# ---------------------------------------------------------------------------
# Seiten
# ---------------------------------------------------------------------------

class ChatSeite(QWidget):
    def __init__(self, fenster: "Fenster"):
        super().__init__()
        self.fenster = fenster
        self.aktuell: KiBlock | None = None
        self.karten: dict = {}
        self.fortschritt: Fortschritt | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 12)
        v.setSpacing(10)

        self.oben = QWidget()                       # Platzhalter: leerer Chat -> Eingabe mittig
        v.addWidget(self.oben, 1)
        self.gruss = QWidget()
        g = QHBoxLayout(self.gruss)
        g.addStretch(1)
        self.gruss_text = label("", 20, "text", umbruch=False)
        g.addWidget(self.gruss_text)
        g.addStretch(1)
        v.addWidget(self.gruss)

        self.spalte = QWidget()
        self.liste = QVBoxLayout(self.spalte)
        self.liste.setContentsMargins(0, 18, 0, 18)
        self.liste.setSpacing(4)
        self.liste.addStretch(1)
        self.tippen = Tippen()
        self.tippen.hide()
        huelle = QWidget()
        h = QHBoxLayout(huelle)
        h.setContentsMargins(24, 0, 24, 0)
        h.addStretch(1)
        self.spalte.setMaximumWidth(SPALTE)
        self.spalte.setMinimumWidth(min(SPALTE, 420))
        h.addWidget(self.spalte, 10)
        h.addStretch(1)
        self.rollen = rollbereich(huelle)
        v.addWidget(self.rollen, 20)
        self._folgen = True
        leiste = self.rollen.verticalScrollBar()
        leiste.rangeChanged.connect(self._bereich_gewachsen)
        leiste.sliderMoved.connect(self._folgen_setzen)
        leiste.actionTriggered.connect(lambda _a: QTimer.singleShot(0, lambda: self._folgen_setzen(leiste.value())))
        self.rollen.nutzer_ziel.connect(self._folgen_setzen)

        self.eingabe = Eingabe()
        self.eingabe.senden.connect(self.fenster.senden)
        v.addWidget(self._mittig(self.eingabe))

        self.vorschlaege = QWidget()
        vv = QVBoxLayout(self.vorschlaege)
        vv.setContentsMargins(0, 8, 0, 0)
        vv.setSpacing(2)
        self.vorschlag_knoepfe = [Knopf("", "") for _ in range(3)]
        for k in self.vorschlag_knoepfe:
            k.clicked.connect(lambda _=False, k=k: self._vorschlag(k))
            vv.addWidget(k)
        v.addWidget(self._mittig(self.vorschlaege))
        self.unten = QWidget()
        v.addWidget(self.unten, 1)
        self.hinweis = label("NemiCLI kann Fehler machen. Prüfe wichtige Infos.", 8.5, "blass")
        self.hinweis.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.hinweis)
        self.leer(True)

    def _mittig(self, w: QWidget) -> QWidget:
        huelle = QWidget()
        h = QHBoxLayout(huelle)
        h.setContentsMargins(24, 0, 24, 0)
        h.addStretch(1)
        w.setMaximumWidth(SPALTE)
        h.addWidget(w, 10)
        h.addStretch(1)
        return huelle

    def _vorschlag(self, k: Knopf):
        self.eingabe.feld.setPlainText(k.text())
        self.eingabe.feld.setFocus()

    def begruessung(self, modus: str, name: str) -> None:
        if modus == "work":
            self.gruss_text.setText("Woran sollen wir arbeiten?")
        else:
            self.gruss_text.setText(f"Bereit, wenn du es bist – {name} hört zu.")
        for k, (sym, text) in zip(self.vorschlag_knoepfe, VORSCHLAEGE[modus]):
            k.setText(text)
            k.symbol = sym
            k.update()

    def leer(self, ja: bool) -> None:
        for w in (self.oben, self.unten, self.gruss, self.vorschlaege):
            w.setVisible(ja)
        self.rollen.setVisible(not ja)
        self.hinweis.setVisible(not ja)

    # -- Einträge -------------------------------------------------------------
    def _folgen_setzen(self, wert: int) -> None:
        """Der Nutzer hat gescrollt: unten = mitgleiten, weiter oben = stehen bleiben."""
        self._folgen = wert >= self.rollen.verticalScrollBar().maximum() - 60

    def _bereich_gewachsen(self, _unten: int, oben: int) -> None:
        if self._folgen:
            self.rollen.sanft_zu(oben, 140)

    def _unten_bleiben(self) -> bool:
        return self._folgen

    def _hinzu(self, w: QWidget) -> None:
        self.leer(False)
        self.liste.insertWidget(self.liste.count() - 1, w)
        self.liste.removeWidget(self.tippen)
        self.liste.insertWidget(self.liste.count() - 1, self.tippen)

    def ans_ende(self):
        """Sofort ans Ende und ab jetzt mitgleiten (Chat öffnen, Nachfrage)."""
        self._folgen = True
        leiste = self.rollen.verticalScrollBar()
        leiste.setValue(leiste.maximum())

    def leeren(self) -> None:
        while self.liste.count() > 1:
            w = self.liste.takeAt(0).widget()
            if w is not None and w is not self.tippen:
                w.deleteLater()
        self.liste.insertWidget(0, self.tippen)
        self.aktuell = None
        self.karten.clear()
        self.fortschritt = None

    def nutzer(self, text: str) -> None:
        self.aktuell = None
        self._hinzu(NutzerBlase(text))

    def hinweis_zeile(self, text: str, farbname: str = "blass") -> None:
        lb = label(text, 9.5, farbname)
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._hinzu(lb)

    def ki(self) -> KiBlock:
        if self.aktuell is None or self.aktuell.fertig:
            self.aktuell = KiBlock()
            self._hinzu(self.aktuell)
        return self.aktuell

    def bilder_zeigen(self, pfade: list[str]) -> None:
        for pfad in pfade[:4]:
            kachel = BildKachel(pfad, 360)
            kachel.geklickt.connect(self.fenster.bild_gross)
            self._hinzu(kachel)

    def verlauf(self, eintraege: list) -> None:
        self.leeren()
        for e in eintraege:
            rolle, text = e.get("rolle"), str(e.get("text") or "")
            if rolle == "nutzer":
                self.nutzer(text)
            elif rolle == "ki":
                block = self.ki()
                block.abschliessen(text)
            elif rolle == "aktion":
                self.hinweis_zeile(f"⚙  {text}")
            elif rolle == "werkzeug":
                pfade = [m.group(0) for m in _BILDPFAD.finditer(text) if Path(m.group(0)).is_file()]
                if pfade:
                    self.bilder_zeigen(pfade)
        self.leer(not eintraege)
        QTimer.singleShot(60, self.ans_ende)


class GalerieSeite(QWidget):
    def __init__(self, fenster: "Fenster"):
        super().__init__()
        self.fenster = fenster
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 16, 32, 16)
        v.addWidget(label("Galerie", 18, "text", fett=True))
        self.info = label("", 9.5, "blass")
        v.addWidget(self.info)
        self.innen = QWidget()
        self.raster = QGridLayout(self.innen)
        self.raster.setSpacing(12)
        self.raster.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        v.addWidget(rollbereich(self.innen), 1)

    def fuellen(self, bilder: list[str]) -> None:
        while self.raster.count():
            w = self.raster.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._lauf = getattr(self, "_lauf", 0) + 1                 # ältere Ladeläufe brechen ab
        self._stueck(self._lauf, list(bilder), 0, max(2, (self.width() - 64) // 232))
        self.info.setText(f"{len(bilder)} Bilder aus dem Ordner „Bilder“" if bilder else
                          "Noch keine Bilder – im Chat „Male mir …“ schreiben.")

    def _stueck(self, lauf: int, bilder: list[str], ab: int, spalten: int) -> None:
        """Je Takt ein paar Vorschaubilder – das Fenster bleibt bedienbar."""
        if lauf != self._lauf:
            return
        for i in range(ab, min(ab + 8, len(bilder))):
            kachel = BildKachel(bilder[i], 220)
            kachel.geklickt.connect(self.fenster.bild_gross)
            self.raster.addWidget(kachel, i // spalten, i % spalten)
        if ab + 8 < len(bilder):
            QTimer.singleShot(0, lambda: self._stueck(lauf, bilder, ab + 8, spalten))


class PersonaKarte(Flaeche):
    geklickt = Signal(str)

    def __init__(self, daten: dict, parent=None):
        super().__init__(parent, radius=14)
        self.key = daten.get("key", "")
        self.leuchten = bool(daten.get("aktiv"))
        self.setFixedSize(250, 120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        kopf = QHBoxLayout()
        av = Avatar(34)
        av.buchstabe = (daten.get("name") or "?")[:1].upper()
        kopf.addWidget(av)
        kopf.addWidget(label(daten.get("name") or self.key, 12, "text", fett=True, umbruch=False), 1)
        v.addLayout(kopf)
        v.addWidget(label(daten.get("kurz") or "", 9.5, "dim"), 1)

    def mousePressEvent(self, _):
        self.geklickt.emit(self.key)


class PersonaSeite(QWidget):
    def __init__(self, fenster: "Fenster"):
        super().__init__()
        self.fenster = fenster
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 16, 32, 16)
        v.addWidget(label("Persönlichkeiten", 18, "text", fett=True))
        v.addWidget(label("Wer dir antwortet. Ein Klick wechselt – für Terminal und Fenster.", 9.5, "blass"))
        self.innen = QWidget()
        self.raster = QGridLayout(self.innen)
        self.raster.setSpacing(12)
        self.raster.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        v.addWidget(rollbereich(self.innen), 1)

    def fuellen(self, personas: list) -> None:
        while self.raster.count():
            w = self.raster.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        spalten = max(1, (self.width() - 64) // 262)
        for i, d in enumerate(personas):
            k = PersonaKarte(d)
            k.geklickt.connect(lambda key: self.fenster.leitung.senden({"op": "persona", "key": key}))
            self.raster.addWidget(k, i // spalten, i % spalten)


class SkillKarte(Flaeche):
    geklickt = Signal(str)

    def __init__(self, daten: dict, parent=None):
        super().__init__(parent, radius=14)
        self.kennung = str(daten.get("kennung") or "")
        self.setFixedWidth(360)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(4)
        v.addWidget(label("🧩  " + str(daten.get("name") or self.kennung)
                          + ("   · für alle" if daten.get("fuer_alle") else ""), 12, "text", fett=True))
        v.addWidget(label(str(daten.get("beschreibung") or ""), 9.5, "dim"))
        v.addWidget(label("Wann: " + str(daten.get("wann") or "–"), 9, "blass"))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)

    def mousePressEvent(self, _):
        self.geklickt.emit(self.kennung)


class SkillSeite(QWidget):
    """Freigegebene Skills; neue entstehen im Gespräch und gehen durchs Prüffenster."""

    def __init__(self, fenster: "Fenster"):
        super().__init__()
        self.fenster = fenster
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 16, 32, 16)
        kopf = QHBoxLayout()
        kopf.addWidget(label("Skills", 18, "text", fett=True), 1)
        neu = Knopf("Neuen Skill besprechen", "✎", mitte=True)
        neu.akzent = True
        neu.clicked.connect(self._neu)
        kopf.addWidget(neu)
        v.addLayout(kopf)
        v.addWidget(label("Anleitungen, die NemiCLI bei Bedarf lädt. Neue entstehen im Gespräch – "
                          "du siehst jeden Skill im Prüffenster und gibst ihn frei.", 9.5, "blass"))
        self.status = label("", 9, "blass")
        v.addWidget(self.status)
        self.innen = QWidget()
        self.raster = QGridLayout(self.innen)
        self.raster.setSpacing(12)
        self.raster.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        v.addWidget(rollbereich(self.innen), 1)

    def _neu(self):
        self.fenster.seite("chat")
        feld = self.fenster.chat.eingabe.feld
        feld.setPlainText("Lass uns einen Skill machen: ")
        feld.moveCursor(QTextCursor.MoveOperation.End)
        feld.setFocus()

    def fuellen(self, skills: list, selbst: bool) -> None:
        while self.raster.count():
            w = self.raster.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.status.setText(("Selbst anlegen: an – ohne Rückfrage, außer nach Web-Inhalt"
                             if selbst else "Selbst anlegen: aus – jeder Skill geht durchs Prüffenster")
                            + "   ·   im Terminal: /skills selbst an|aus")
        if not skills:
            self.raster.addWidget(label("Noch keine Skills.", 10, "dim"), 0, 0)
            return
        spalten = max(1, (self.width() - 64) // 372)
        for i, d in enumerate(skills):
            k = SkillKarte(d)
            k.geklickt.connect(lambda kennung: self.fenster.leitung.senden({"op": "skill", "kennung": kennung}))
            self.raster.addWidget(k, i // spalten, i % spalten)


class SkillDialog(QDialog):
    """Einen Skill ganz lesen (nur ansehen – geändert wird im Gespräch)."""

    def __init__(self, name: str, wann: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Skill: {name}")
        self.resize(760, 640)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, farbe("grund"))
        self.setPalette(pal)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.addWidget(label("🧩  " + name, 15, "text", fett=True))
        v.addWidget(label("Wann: " + (wann or "–"), 9.5, "blass"))
        inhalt = Markdown()
        inhalt.setzen(text)
        huelle = QWidget()
        h = QVBoxLayout(huelle)
        h.setContentsMargins(0, 8, 0, 8)
        h.addWidget(inhalt)
        h.addStretch(1)
        v.addWidget(rollbereich(huelle), 1)
        zu = Knopf("Schließen", mitte=True)
        zu.clicked.connect(self.accept)
        v.addWidget(zu, alignment=Qt.AlignmentFlag.AlignRight)
        _dunkler_rahmen(self)


# ---------------------------------------------------------------------------
# Seitenleiste und Fenster
# ---------------------------------------------------------------------------

class Klickflaeche(QWidget):
    """Bereich, der als Ganzes anklickbar ist (Hover-Hintergrund)."""
    geklickt = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def mousePressEvent(self, _):
        self.geklickt.emit()

    def paintEvent(self, _):
        if self.underMouse():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(farbe("flaeche"))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)


class ProfilDialog(QDialog):
    """„Über dich“: Name, Wunsch-Anrede, freie Beschreibung – gilt ab der ersten Nachricht."""

    def __init__(self, profil: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Über dich")
        self.resize(620, 560)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, farbe("grund"))
        self.setPalette(pal)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(6)
        v.addWidget(label("👤  Über dich", 15, "text", fett=True))
        v.addWidget(label("Das liest jede Persönlichkeit ab der ersten Nachricht. Nur du kannst es ändern.",
                          9.5, "blass"))
        self.name = self._zeile(v, "Name", profil.get("name"), "z. B. Alex")
        self.anrede = self._zeile(v, "So möchtest du genannt werden (mehrere mit Komma)",
                                  profil.get("anrede"), "z. B. Alex, Kumpel")
        v.addSpacing(6)
        v.addWidget(label("Über dich – was du magst, was nicht, was die KI wissen soll", 9.5, "dim"))
        self.ueber = QPlainTextEdit()
        self.ueber.setFont(schrift(10.5))
        pal = self.ueber.palette()
        pal.setColor(QPalette.ColorRole.Base, farbe("flaeche"))
        pal.setColor(QPalette.ColorRole.Text, farbe("text"))
        self.ueber.setPalette(pal)
        self.ueber.setPlainText(str(profil.get("ueber") or ""))
        self.ueber.setPlaceholderText("z. B. Ich mag kurze Antworten und Listen. Ich programmiere gern in Python.")
        v.addWidget(self.ueber, 1)
        knoepfe = QHBoxLayout()
        knoepfe.addStretch(1)
        abbruch = Knopf("Abbrechen", mitte=True)
        abbruch.clicked.connect(self.reject)
        ok = Knopf("Speichern", mitte=True)
        ok.akzent = True
        ok.clicked.connect(self.accept)
        knoepfe.addWidget(abbruch)
        knoepfe.addWidget(ok)
        v.addLayout(knoepfe)
        _dunkler_rahmen(self)

    @staticmethod
    def _zeile(v: QVBoxLayout, titel: str, wert, platzhalter: str) -> QLineEdit:
        v.addSpacing(6)
        v.addWidget(label(titel, 9.5, "dim"))
        feld = QLineEdit(str(wert or ""))
        feld.setFont(schrift(10.5))
        feld.setPlaceholderText(platzhalter)
        feld.setMinimumHeight(34)
        v.addWidget(feld)
        return feld

    def werte(self) -> dict:
        return {"name": self.name.text().strip(), "anrede": self.anrede.text().strip(),
                "ueber": self.ueber.toPlainText().strip()}


class Seitenleiste(QWidget):
    def __init__(self, fenster: "Fenster"):
        super().__init__()
        self.fenster = fenster
        self.setFixedWidth(264)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 12, 10, 10)
        v.setSpacing(2)
        kopf = QHBoxLayout()
        kopf.setContentsMargins(6, 0, 0, 6)
        kopf.addWidget(Logo(15), 1)
        zu = Knopf("", "⟨", klein=True, mitte=True)
        zu.setFixedWidth(30)
        zu.setToolTip("Seitenleiste einklappen")
        zu.clicked.connect(lambda: self.fenster.leiste_umschalten(False))
        kopf.addWidget(zu)
        v.addLayout(kopf)
        self.neu = Knopf("Neuer Chat", "✎")
        self.neu.clicked.connect(lambda: self.fenster.seite("chat", neu=True))
        self.galerie = Knopf("Galerie", "🖼")
        self.galerie.clicked.connect(lambda: self.fenster.seite("galerie"))
        self.skills = Knopf("Skills", "🧩")
        self.skills.clicked.connect(lambda: self.fenster.seite("skills"))
        self.personas = Knopf("Persönlichkeiten", "✦")
        self.personas.clicked.connect(lambda: self.fenster.seite("personas"))
        for k in (self.neu, self.galerie, self.skills, self.personas):
            v.addWidget(k)
        v.addSpacing(14)
        titel = label("Letzte", 9, "blass")
        titel.setContentsMargins(12, 0, 0, 4)
        v.addWidget(titel)
        self.chats_w = QWidget()
        self.chats = QVBoxLayout(self.chats_w)
        self.chats.setContentsMargins(0, 0, 0, 0)
        self.chats.setSpacing(1)
        self.chats.setAlignment(Qt.AlignmentFlag.AlignTop)
        v.addWidget(rollbereich(self.chats_w), 1)
        self.unten = Klickflaeche()
        self.unten.setToolTip("Über dich – Name, Anrede und was die KI wissen soll")
        self.unten.geklickt.connect(lambda: self.fenster.profil_oeffnen())
        unten = QHBoxLayout(self.unten)
        unten.setContentsMargins(8, 8, 8, 8)
        self.avatar = Avatar(32)
        unten.addWidget(self.avatar)
        namen = QVBoxLayout()
        namen.setSpacing(0)
        self.name = label("NemiCLI", 10.5, "text", fett=True, umbruch=False)
        self.modell = label("", 8.5, "blass", umbruch=False)
        namen.addWidget(self.name)
        namen.addWidget(self.modell)
        unten.addLayout(namen, 1)
        unten.addWidget(label("👤 Über dich", 8.5, "blass", umbruch=False))
        v.addWidget(self.unten)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), farbe("leiste"))
        p.setPen(farbe("rand"))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

    def zustand(self, z: dict) -> None:
        persona = z.get("persona") or {}
        self.name.setText(persona.get("name") or "NemiCLI")
        self.avatar.buchstabe = (persona.get("name") or "N")[:1].upper()
        self.avatar.update()
        self.modell.setText(str(z.get("model") or ""))
        while self.chats.count():
            w = self.chats.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        aktiv = z.get("chat")
        for c in z.get("chats") or []:
            k = Knopf(c.get("title") or f"Chat #{c.get('id')}", klein=False)
            k.aktiv = c.get("id") == aktiv
            k.setToolTip(f"Chat #{c.get('id')} · {c.get('model') or ''}")
            k.clicked.connect(lambda _=False, cid=c.get("id"): self.fenster.chat_oeffnen(cid))
            self.chats.addWidget(k)


class Fenster(QWidget):
    def __init__(self, leitung: Leitung):
        super().__init__()
        self.leitung = leitung
        self.modus_wert = "chat"
        self.persona_name = "NemiCLI"
        self.ordner = ""
        self.beschaeftigt = False
        self.setWindowTitle("NemiCLI")
        self.resize(1320, 840)
        self.setMinimumSize(900, 600)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, farbe("grund"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.leiste = Seitenleiste(self)
        h.addWidget(self.leiste)
        rechts = QVBoxLayout()
        rechts.setContentsMargins(0, 0, 0, 0)
        oben = QHBoxLayout()
        oben.setContentsMargins(12, 10, 16, 4)
        self.auf = Knopf("", "☰", klein=True, mitte=True)
        self.auf.setFixedWidth(34)
        self.auf.clicked.connect(lambda: self.leiste_umschalten(True))
        self.auf.hide()
        oben.addWidget(self.auf)
        oben.addStretch(1)
        self.umschalter = Umschalter()
        self.umschalter.gewechselt.connect(self.modus)
        oben.addWidget(self.umschalter)
        oben.addStretch(1)
        self.status = label("", 9, "blass", umbruch=False)
        oben.addWidget(self.status)
        rechts.addLayout(oben)
        self.stapel = QStackedWidget()
        self.chat = ChatSeite(self)
        self.galerie = GalerieSeite(self)
        self.skills = SkillSeite(self)
        self.personas = PersonaSeite(self)
        for s in (self.chat, self.galerie, self.skills, self.personas):
            self.stapel.addWidget(s)
        rechts.addWidget(self.stapel, 1)
        h.addLayout(rechts, 1)
        self._letzter_zustand: dict = {}

        leitung.ereignis.connect(self.ereignis)
        leitung.getrennt.connect(self.getrennt)
        self.modus("chat")
        leitung.senden({"op": "zustand"})

    # -- Aktionen --------------------------------------------------------------
    def leiste_umschalten(self, auf: bool) -> None:
        self.leiste.setVisible(auf)
        self.auf.setVisible(not auf)

    def modus(self, wert: str) -> None:
        self.modus_wert = wert
        self.chat.eingabe.modus(wert, self.ordner)
        self.chat.begruessung(wert, self.persona_name)

    def seite(self, name: str, neu: bool = False) -> None:
        self.stapel.setCurrentWidget({"chat": self.chat, "galerie": self.galerie, "skills": self.skills,
                                      "personas": self.personas}[name])
        for k, seite in ((self.leiste.galerie, "galerie"), (self.leiste.skills, "skills"),
                         (self.leiste.personas, "personas")):
            k.aktiv = name == seite
            k.update()
        if name == "galerie":
            self.leitung.senden({"op": "galerie"})
        elif name == "skills":
            self.skills.fuellen(self._letzter_zustand.get("skills") or [],
                                bool(self._letzter_zustand.get("skills_selbst")))
        elif name == "personas":
            self.personas.fuellen(self._letzter_zustand.get("personas") or [])
        if neu:
            self.leitung.senden({"op": "neu"})

    def profil_oeffnen(self) -> None:
        dialog = ProfilDialog(self._letzter_zustand.get("profil") or {}, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.leitung.senden({"op": "profil", **dialog.werte()})

    def chat_oeffnen(self, cid) -> None:
        self.seite("chat")
        self.leitung.senden({"op": "fortsetzen", "chat": cid})

    def senden(self, text: str) -> None:
        self.seite("chat")
        self.leitung.senden({"op": "senden", "text": text, "modus": self.modus_wert})

    def bild_gross(self, pfad: str) -> None:
        BildDialog(pfad, self).exec()

    def getrennt(self) -> None:
        """Die Sitzung ist der Motor: ohne sie schließt sich das Fenster."""
        self.status.setText("● NemiCLI wurde beendet")
        self.chat.eingabe.beschaeftigt(True)
        self.chat.tippen.stop()
        QTimer.singleShot(1500, QApplication.quit)

    # -- Ereignisse der Sitzung -------------------------------------------------
    def ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        c = self.chat
        if t == "state":
            self._letzter_zustand = ev
            self.leiste.zustand(ev)
            self.persona_name = (ev.get("persona") or {}).get("name") or "NemiCLI"
            self.ordner = str(ev.get("ordner") or "")
            self.status.setText(f"{ev.get('model') or ''}  ·  {ev.get('modus') or ''}")
            self.modus(self.modus_wert)
            if self.stapel.currentWidget() is self.personas:
                self.personas.fuellen(ev.get("personas") or [])
            elif self.stapel.currentWidget() is self.skills:
                self.skills.fuellen(ev.get("skills") or [], bool(ev.get("skills_selbst")))
        elif t == "verlauf":
            c.verlauf(ev.get("eintraege") or [])
        elif t == "nutzer":
            c.nutzer(str(ev.get("text") or ""))
        elif t == "busy":
            self.beschaeftigt = bool(ev.get("on"))
            c.eingabe.beschaeftigt(self.beschaeftigt)
            if self.beschaeftigt:
                c.leer(False)
                c.tippen.start()
            else:
                c.tippen.stop()
        elif t == "thinking":
            c.ki().denken(str(ev.get("delta") or ""))
        elif t == "answer":
            c.ki().schreiben(str(ev.get("delta") or ""))
        elif t == "answer_end":
            if c.aktuell is not None and not c.aktuell.fertig:
                c.aktuell.abschliessen(ev.get("clean"))
                if not c.aktuell.antwort.strip() and not c.aktuell.gedanken_text:
                    c.aktuell.hide()
        elif t == "action":
            karte = Karte(str(ev.get("desc") or ""), "⚠" if ev.get("risiko") else "⚙")
            c.karten["letzte"] = karte
            c._hinzu(karte)
        elif t == "action_result":
            karte = c.karten.pop("letzte", None)
            if karte is None:
                karte = Karte("Ergebnis")
                c._hinzu(karte)
            bilder = karte.ergebnis(str(ev.get("text") or ""), ev.get("ok"))
            if bilder:
                c.bilder_zeigen(bilder)
        elif t == "confirm":
            karte = Karte(str(ev.get("question") or "Freigeben?"), "❓")
            karte.frage(ev.get("id"), [tuple(o) for o in ev.get("options") or []], ev.get("preview"))
            karte.antwort.connect(lambda fid, wahl: self.leitung.senden({"op": "antwort", "id": fid, "wahl": wahl}))
            c._hinzu(karte)
            QTimer.singleShot(80, c.ans_ende)                  # Knöpfe immer sichtbar
            self.raise_()
            self.activateWindow()
        elif t == "progress":
            if c.fortschritt is None:
                c.fortschritt = Fortschritt(str(ev.get("title") or ""))
                c._hinzu(c.fortschritt)
            pct = ev.get("pct")
            c.fortschritt.setzen(str(ev.get("msg") or ""), pct / 100 if isinstance(pct, (int, float)) else None)
        elif t == "progress_end":
            if c.fortschritt is not None:
                c.fortschritt.setzen("fertig", 1.0)
            c.fortschritt = None
        elif t in ("note", "info"):
            c.hinweis_zeile(str(ev.get("text") or ""))
        elif t == "warn":
            c.hinweis_zeile("⚠  " + str(ev.get("text") or ""), "warn")
        elif t == "error":
            c.hinweis_zeile("✗  " + str(ev.get("text") or ""), "fehler")
        elif t == "skill":
            SkillDialog(str(ev.get("name") or ""), str(ev.get("wann") or ""), str(ev.get("text") or ""),
                        self).exec()
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()
        elif t == "galerie":
            self.galerie.fuellen([b for b in ev.get("bilder") or [] if isinstance(b, str)])


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def _dunkler_rahmen(fenster: QWidget) -> None:
    """Dunkle Titelleiste in der Farbe der Seitenleiste (Windows 11)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes as w
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        hwnd = int(fenster.winId())
        an = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(an), 4)         # DWMWA_USE_IMMERSIVE_DARK_MODE
        c = farbe("leiste")
        ref = ctypes.c_uint(c.red() | (c.green() << 8) | (c.blue() << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ref), 4)        # DWMWA_CAPTION_COLOR
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ref), 4)        # DWMWA_BORDER_COLOR
    except Exception:
        pass


def starten() -> int:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NemiCLI.GUI")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    pal = app.palette()
    for rolle, name in ((QPalette.ColorRole.Window, "grund"), (QPalette.ColorRole.Base, "flaeche"),
                        (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
                        (QPalette.ColorRole.Button, "flaeche"), (QPalette.ColorRole.ButtonText, "text"),
                        (QPalette.ColorRole.Highlight, "lila"), (QPalette.ColorRole.ToolTipBase, "flaeche"),
                        (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.PlaceholderText, "blass")):
        pal.setColor(rolle, farbe(name))
    app.setPalette(pal)
    app.setFont(schrift(10.5))
    for icon in (Path(getattr(sys, "_MEIPASS", "")) / "nemicli.ico", Path(__file__).resolve().parent.parent / "nemicli.ico"):
        if icon.is_file():
            app.setWindowIcon(QIcon(str(icon)))
            break

    adresse = os.environ.pop("NEMICLI_GUI_PIPE", "")       # pop: nicht an geöffnete Programme vererben
    schluessel = os.environ.pop("NEMICLI_GUI_KEY", "")
    try:
        leitung = Leitung(adresse, bytes.fromhex(schluessel))
    except Exception:
        hinweis = QDialog()
        hinweis.setWindowTitle("NemiCLI")
        v = QVBoxLayout(hinweis)
        v.setContentsMargins(24, 20, 24, 20)
        v.addWidget(label("Das Fenster startet aus einer laufenden NemiCLI-Sitzung:\n"
                          "im Terminal  /gui  eingeben.", 11))
        ok = Knopf("OK", mitte=True)
        ok.akzent = True
        ok.clicked.connect(hinweis.accept)
        v.addWidget(ok)
        _dunkler_rahmen(hinweis)
        hinweis.exec()
        return 1
    fenster = Fenster(leitung)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
