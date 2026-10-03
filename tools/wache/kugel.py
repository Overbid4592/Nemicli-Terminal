"""Die Schwebekugel: die Persönlichkeit auf dem Desktop, wenn kein Terminal offen ist.

Das Schild neben der Uhr bleibt. Zusätzlich
schwebt eine kleine Leuchtkugel frei über dem Desktop – immer oben, ohne
Rahmen, verschiebbar. Sie ist da, wenn NemiCLI gerade nicht im Terminal läuft.

  Farbe      🟢 ruhig · 🟠 offene Alarme · 🔴 die Persönlichkeit sagt „echt“
  Blase      ab und an ein kurzer Gruß („Hey, alles ok 🙂“), 2–3× am Tag,
             nie wenn rot; bei rot eine ernste Blase.
  Klick      Chatfenster neben der Kugel: reden, Bilder bekommen,
             📸 Screenshot (sie sieht ihn), „Alles ok?“, „NemiCLI öffnen“.
  Ziehen     Kugel verschieben – die Stelle wird gemerkt (Wache/kugel.json).

Technik (Qt, siehe oberflaeche.py): ein
rahmenloses, durchsichtiges Fenster; die Kugel wird mit QPainter gemalt
(Radialverlauf, Glanzpunkt, weicher Lichthof), atmet über einen QTimer und
hebt sich unter der Maus leicht an. Das Chatfenster ist eine dunkle Karte mit
Cyan-Rand: Sprechblasen, Markdown (Codeblöcke, Listen, Links), Bilder inline.
Alles läuft im GUI-Thread; das Gespräch mit der Persönlichkeit ist eine
Coroutine aus main.py (`chat(text, bilder)`) auf der asyncio-Schleife des
Dienstes – die Antwort kommt über `oberflaeche.im_gui` zurück.

Die Grüße stehen in Wache/gruesse.md – eine Zeile je Gruß. Die Persönlichkeit
darf die Datei selbst schreiben (datei_schreiben, sie liegt im Daten-Ordner);
bis dahin gelten die Vorgaben unten. {name} = Persönlichkeit, {nutzer} = Nutzer.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QGuiApplication, QPainter, QPen, QPixmap, QRadialGradient,
                           QTextDocument)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import ORDNER
from . import oberflaeche as O
from . import steuerung
from .oberflaeche import THEME

GRUESSE_DATEI = ORDNER / "gruesse.md"
LAGE_DATEI = ORDNER / "kugel.json"
GROESSE = 64                 # Durchmesser der Kugel
RAND = 18                    # Platz für den Lichthof rundherum
PANEL_BREITE, PANEL_HOEHE = 420, 580

VORGABE_GRUESSE = [
    "Hey {nutzer} 🙂 alles ruhig hier.",
    "Ich bin da. Alles ok bei dir?",
    "Kurz gemeldet: nichts Auffälliges. 🌙",
    "Hey {nutzer} – {name} hier. Alles im grünen Bereich.",
    "Nur ein Hallo. Ich passe auf. 🙂",
    "Alles gut. Wenn du mich brauchst: einmal klicken.",
]

# (Kern, Glanz) je Lage – dieselben Töne wie das Schild im Tray
FARBEN = {
    "gruen": ((63, 185, 80), (160, 255, 170)),
    "gelb":  ((210, 153, 34), (255, 220, 130)),
    "rot":   ((229, 72, 77), (255, 170, 170)),
    "grau":  ((110, 118, 129), (190, 195, 205)),
}


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def _misch(a, b, t: float):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _qc(rgb, alpha: int = 255) -> QColor:
    return QColor(*(max(0, min(255, int(v))) for v in rgb), alpha)


def gruesse(name: str, nutzer: str) -> list[str]:
    zeilen: list[str] = []
    try:
        if GRUESSE_DATEI.exists():
            for z in GRUESSE_DATEI.read_text(encoding="utf-8").splitlines():
                z = z.strip().lstrip("-•* ").strip()
                if z and not z.startswith("#"):
                    zeilen.append(z)
    except OSError:
        pass
    if not zeilen:
        zeilen = list(VORGABE_GRUESSE)
        try:
            GRUESSE_DATEI.parent.mkdir(parents=True, exist_ok=True)
            GRUESSE_DATEI.write_text(
                "# Grüße der Kugel – eine Zeile je Gruß. {name} = du, {nutzer} = der Nutzer.\n"
                "# Die Persönlichkeit darf diese Datei selbst neu schreiben.\n\n"
                + "\n".join(zeilen) + "\n", encoding="utf-8")
        except OSError:
            pass
    return [z.replace("{name}", name).replace("{nutzer}", nutzer) for z in zeilen]


def _schirm(punkt: QPoint):
    """Der Bildschirm, auf dem ein Punkt liegt – sonst der Hauptbildschirm."""
    s = QGuiApplication.screenAt(punkt) or QGuiApplication.primaryScreen()
    return s.availableGeometry()


# ================================================================ Die Kugel

class _Kugelfenster(QWidget):
    """Rahmenlos, durchsichtig, immer oben. Malt die Kugel und meldet Klick/Ziehen."""

    geklickt = Signal()
    gezogen = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.groesse = GROESSE
        self.setFixedSize(GROESSE + 2 * RAND, GROESSE + 2 * RAND)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip("")
        self.farbe = "gruen"
        self.phase = 0.0
        self.beschaeftigt = False
        self.schwebt = False                        # Maus drüber
        self._hebung = 0.0                          # 0 … 1, weich
        self._druck: QPoint | None = None
        self._bewegt = False
        self.bild: QPixmap | None = None            # statt der Kugel: das Bild der Persönlichkeit
        self._bewegung = ""                         # huepfen · wackeln · nicken
        self._bewegung_start = 0.0

    def groesse_setzen(self, g: int) -> None:
        g = max(steuerung.GROESSE_MIN, min(steuerung.GROESSE_MAX, int(g)))
        if g != self.groesse:
            self.groesse = g
            self.setFixedSize(g + 2 * RAND, g + 2 * RAND)
            self.update()

    def bild_setzen(self, pfad: Path | None) -> None:
        pix = QPixmap(str(pfad)) if pfad else QPixmap()
        self.bild = None if pix.isNull() else pix
        self.update()

    def bewegen(self, art: str) -> None:
        """Kurze Geste (1,2 s): huepfen (hoch und runter), wackeln (kippen), nicken (ducken)."""
        self._bewegung, self._bewegung_start = art, time.time()

    def _geste(self) -> tuple[float, float, float]:
        """(Hebung in px, Drehung in Grad, Stauchung 0…1) der laufenden Geste."""
        if not self._bewegung:
            return 0.0, 0.0, 0.0
        t = time.time() - self._bewegung_start
        if t > 1.2:
            self._bewegung = ""
            return 0.0, 0.0, 0.0
        if self._bewegung == "huepfen":
            return abs(math.sin(t * math.pi / 0.6)) * RAND * 0.9 * (1 - t / 1.4), 0.0, 0.0
        if self._bewegung == "wackeln":
            return 0.0, math.sin(t * math.pi * 5) * 12 * (1 - t / 1.2), 0.0
        if self._bewegung == "nicken":
            return 0.0, 0.0, abs(math.sin(t * math.pi / 0.6)) * 0.18
        return 0.0, 0.0, 0.0

    # --- Zeichnen
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        kern, glanz = FARBEN.get(self.farbe, FARBEN["gruen"])
        m = self.width() / 2
        atem = 0.5 + 0.5 * math.sin(self.phase)
        hoch, dreh, stauch = self._geste()
        if self.bild is not None:
            self._bild_malen(p, kern, m, atem, hoch, dreh, stauch)
            p.end()
            return
        if hoch or dreh or stauch:
            p.translate(m, m - hoch); p.rotate(dreh); p.scale(1 + stauch * 0.4, 1 - stauch); p.translate(-m, -m)
        r = self.groesse / 2 * (0.93 + 0.05 * atem + 0.06 * self._hebung)
        # Lichthof: weich nach außen auslaufend, bei rot kräftiger
        hof = QRadialGradient(m, m, r + RAND)
        a = 120 if self.farbe == "rot" else 80
        hof.setColorAt(0.0, _qc(kern, a))
        hof.setColorAt(r / (r + RAND), _qc(kern, int(a * 0.55)))
        hof.setColorAt(1.0, _qc(kern, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(hof))
        p.drawEllipse(QRectF(m - r - RAND, m - r - RAND, 2 * (r + RAND), 2 * (r + RAND)))
        # Kugel: Licht von oben links, dunkler Rand
        kugel = QRadialGradient(m - r * 0.35, m - r * 0.4, r * 1.6)
        kugel.setColorAt(0.0, _qc(_misch(glanz, (255, 255, 255), 0.35)))
        kugel.setColorAt(0.35, _qc(_misch(kern, glanz, 0.45)))
        kugel.setColorAt(0.8, _qc(kern))
        kugel.setColorAt(1.0, _qc(_misch(kern, (0, 0, 0), 0.45)))
        p.setBrush(QBrush(kugel))
        p.drawEllipse(QRectF(m - r, m - r, 2 * r, 2 * r))
        # Glanzpunkt
        p.setBrush(QColor(255, 255, 255, 150))
        gr = r * 0.26
        p.drawEllipse(QRectF(m - r * 0.5 - gr, m - r * 0.5 - gr, 2 * gr, 2 * gr))
        # feiner Rand
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_qc(glanz, 90), 1.2))
        p.drawEllipse(QRectF(m - r, m - r, 2 * r, 2 * r))
        if self.beschaeftigt:                       # drei Punkte: sie denkt
            for k in range(3):
                an = int((self.phase * 2.5) % 3) == k
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 235 if an else 110))
                p.drawEllipse(QRectF(m + (k - 1) * 9 - 3, m + r * 0.5 - 3, 6, 6))
        p.end()

    def _bild_malen(self, p: QPainter, kern, m: float, atem: float, hoch: float, dreh: float, stauch: float) -> None:
        """Das Bild der Persönlichkeit: Lichthof in Lagenfarbe dahinter, leichtes Atmen,
        Anheben unter der Maus, Gesten; die drei Denk-Punkte unten wie bei der Kugel."""
        g = self.groesse
        skala = 0.96 + 0.03 * atem + 0.05 * self._hebung
        hof = QRadialGradient(m, m, g / 2 + RAND)
        a = 110 if self.farbe == "rot" else 70
        hof.setColorAt(0.0, _qc(kern, a))
        hof.setColorAt(0.6, _qc(kern, int(a * 0.6)))
        hof.setColorAt(1.0, _qc(kern, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(hof))
        p.drawEllipse(QRectF(RAND * 0.2, RAND * 0.2, self.width() - RAND * 0.4, self.height() - RAND * 0.4))
        p.save()
        p.translate(m, m - hoch)
        p.rotate(dreh)
        p.scale(skala * (1 + stauch * 0.4), skala * (1 - stauch))
        pix = self.bild.scaled(QSize(g, g), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(QRectF(-pix.width() / 2, -pix.height() / 2, pix.width(), pix.height()).toRect(), pix)
        p.restore()
        if self.beschaeftigt:
            for k in range(3):
                an = int((self.phase * 2.5) % 3) == k
                p.setBrush(QColor(255, 255, 255, 235 if an else 110))
                p.setPen(QPen(_qc(kern, 200), 1))
                p.drawEllipse(QRectF(m + (k - 1) * 9 - 3, self.height() - RAND - 2, 6, 6))

    def takt(self) -> None:
        self.phase += 0.055 if self.farbe != "rot" else 0.16
        ziel = 1.0 if self.schwebt else 0.0
        self._hebung += (ziel - self._hebung) * 0.18
        self.update()

    # --- Maus
    def enterEvent(self, _e) -> None:
        self.schwebt = True

    def leaveEvent(self, _e) -> None:
        self.schwebt = False

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._druck = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._bewegt = False

    def mouseMoveEvent(self, e) -> None:
        if self._druck is None:
            return
        neu = e.globalPosition().toPoint() - self._druck
        if (neu - self.pos()).manhattanLength() > 2:
            self._bewegt = True
        self.move(neu)
        self.gezogen.emit()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        bewegt, self._druck = self._bewegt, None
        if bewegt:
            self.gezogen.emit()
        else:
            self.geklickt.emit()


# ================================================================ Die Blase

class _Blase(QWidget):
    geklickt = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(280)
        self.label.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self.label)
        self.ernst = False
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def zeigen(self, text: str, ernst: bool) -> None:
        self.ernst = ernst
        self.label.setText(text)
        self.adjustSize()
        self.setWindowOpacity(0.0)
        self.show()
        self._anim.stop(); self._anim.setStartValue(0.0); self._anim.setEndValue(1.0); self._anim.start()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(THEME["flaeche"]))
        p.setPen(QPen(QColor(THEME["rot"] if self.ernst else THEME["cyan"]), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 12, 12)
        p.end()

    def mousePressEvent(self, _e) -> None:
        self.geklickt.emit()


# ================================================================ Das Chatfenster

class _Eingabe(QPlainTextEdit):
    senden = Signal()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.senden.emit()
            return
        super().keyPressEvent(e)


class _Bild(QLabel):
    def __init__(self, pfad: Path) -> None:
        super().__init__()
        self.pfad = pfad
        pix = QPixmap(str(pfad))
        if pix.isNull():
            self.setText(f"(Bild {pfad.name} ließ sich nicht laden)")
        else:
            self.setPixmap(pix.scaled(QSize(320, 320), Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(f"{pfad.name} – Klick öffnet")
        self.setStyleSheet("background: transparent; border: none;")

    def mousePressEvent(self, _e) -> None:
        try:
            os.startfile(str(self.pfad))
        except OSError:
            pass


class _Panel(QWidget):
    """Die Karte: Kopf (Name, Status, ✕), Verlauf mit Sprechblasen, Eingabe, Knöpfe."""

    geschlossen = Signal()
    senden = Signal()
    screenshot = Signal()
    alles_ok = Signal()
    nemicli = Signal()

    def __init__(self, name: str) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(PANEL_BREITE, PANEL_HOEHE)
        aussen = QVBoxLayout(self)
        aussen.setContentsMargins(0, 0, 0, 0)
        self.karte = QFrame(); self.karte.setObjectName("panel")
        aussen.addWidget(self.karte)
        lay = QVBoxLayout(self.karte)
        lay.setContentsMargins(1, 1, 1, 10)
        lay.setSpacing(6)

        self.kopf = QFrame(); self.kopf.setObjectName("kopf")
        self.kopf.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        k = QHBoxLayout(self.kopf); k.setContentsMargins(16, 10, 8, 8)
        self.punkt = QLabel("●"); self.punkt.setStyleSheet(f"color: {THEME['gruen']}; font-size: 12pt;")
        self.name = QLabel(name); self.name.setObjectName("name")
        self.status = QLabel(""); self.status.setObjectName("status")
        zu = QPushButton("✕"); zu.setObjectName("zu"); zu.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        zu.clicked.connect(self.geschlossen.emit)
        k.addWidget(self.punkt); k.addWidget(self.name); k.addStretch(1); k.addWidget(self.status); k.addWidget(zu)
        lay.addWidget(self.kopf)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inhalt = QWidget()
        self.verlauf = QVBoxLayout(self.inhalt)
        self.verlauf.setContentsMargins(12, 6, 12, 6); self.verlauf.setSpacing(8)
        self.verlauf.addStretch(1)
        self.scroll.setWidget(self.inhalt)
        # Wächst der Inhalt, folgt der Verlauf nach unten – wie in jedem Chat.
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _lo, hi: bar.setValue(hi))
        lay.addWidget(self.scroll, 1)

        self.eingabe = _Eingabe(); self.eingabe.setObjectName("eingabe")
        self.eingabe.setPlaceholderText("Schreib ihr … (Enter sendet, Shift+Enter neue Zeile)")
        self.eingabe.setFixedHeight(74)
        self.eingabe.senden.connect(self.senden.emit)
        e = QHBoxLayout(); e.setContentsMargins(12, 0, 12, 0); e.addWidget(self.eingabe)
        lay.addLayout(e)

        knoepfe = QHBoxLayout(); knoepfe.setContentsMargins(12, 0, 12, 0); knoepfe.setSpacing(6)
        for text, sig, obj in (("📸 Screenshot", self.screenshot, ""), ("Alles ok?", self.alles_ok, ""),
                               ("NemiCLI öffnen", self.nemicli, ""), ("Senden", self.senden, "senden")):
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.clicked.connect(sig.emit)
            knoepfe.addWidget(b)
        knoepfe.insertStretch(3, 1)
        lay.addLayout(knoepfe)

        self._druck: QPoint | None = None
        self._punkte = 0
        self._denk_timer = QTimer(self); self._denk_timer.setInterval(400)
        self._denk_timer.timeout.connect(self._denken_tick)

    # --- Kopf ziehen
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.kopf.geometry().contains(e.position().toPoint()):
            self._druck = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._druck is not None:
            self.move(e.globalPosition().toPoint() - self._druck)

    def mouseReleaseEvent(self, _e) -> None:
        self._druck = None

    # --- Inhalt
    def _anhaengen(self, w: QWidget, rechts: bool = False) -> None:
        zeile = QHBoxLayout(); zeile.setContentsMargins(0, 0, 0, 0)
        if rechts:
            zeile.addStretch(1); zeile.addWidget(w)
        else:
            zeile.addWidget(w); zeile.addStretch(1)
        self.verlauf.insertLayout(self.verlauf.count() - 1, zeile)
        QTimer.singleShot(0, self._runter)

    def _runter(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _textbreite(text: str, markdown: bool, maximal: int) -> int:
        """So breit wie der Text, höchstens `maximal` – QLabel mit Umbruch rät sonst zu schmal."""
        doc = QTextDocument()
        doc.setDefaultFont(QApplication.font())
        if markdown:
            doc.setMarkdown(text)
        else:
            doc.setPlainText(text)
        return max(40, min(maximal, math.ceil(doc.idealWidth()) + 6))

    def nachricht(self, wer: str, text: str, name: str) -> None:
        du = wer == "du"
        blase = QFrame(); blase.setObjectName("blase_du" if du else "blase_sie")
        b = QVBoxLayout(blase); b.setContentsMargins(12, 7, 12, 8); b.setSpacing(2)
        kopf = QLabel("Du" if du else name); kopf.setObjectName("wer_du" if du else "wer_sie")
        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.PlainText if du else Qt.TextFormat.MarkdownText)
        lbl.setText(text)
        lbl.setWordWrap(True)
        lbl.setFixedWidth(self._textbreite(text, not du, int(PANEL_BREITE * 0.78) - 26))
        lbl.setOpenExternalLinks(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                    | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        b.addWidget(kopf); b.addWidget(lbl)
        self._anhaengen(blase, rechts=du)

    def hinweis(self, text: str) -> None:
        lbl = QLabel(text); lbl.setObjectName("hinweis"); lbl.setWordWrap(True)
        lbl.setMaximumWidth(PANEL_BREITE - 40)
        self._anhaengen(lbl)

    def bild(self, pfad: Path) -> None:
        rahmen = QFrame(); rahmen.setObjectName("blase_sie")
        b = QVBoxLayout(rahmen); b.setContentsMargins(6, 6, 6, 6)
        b.addWidget(_Bild(pfad))
        self._anhaengen(rahmen)

    def denkt(self, an: bool) -> None:
        if an:
            self._punkte = 0
            self.status.setText("denkt")
            self._denk_timer.start()
        else:
            self._denk_timer.stop()
            self.status.setText("")

    def _denken_tick(self) -> None:
        self._punkte = (self._punkte + 1) % 4
        self.status.setText("denkt" + " ." * self._punkte)

    def lage(self, farbe: str) -> None:
        self.punkt.setStyleSheet(f"color: {THEME.get(farbe, THEME['gruen'])}; font-size: 12pt;")

    def text_nehmen(self) -> str:
        t = self.eingabe.toPlainText().strip()
        self.eingabe.clear()
        return t


# ================================================================ Der Dirigent

class Kugel:
    """Die Kugel samt Blase und Chatfenster. `stand()` liefert farbe/zahl/text
    (wie beim Tray), `chat(text, bilder)` ist die Coroutine für das Gespräch,
    `sichtbar()` sagt, ob die Kugel gerade gezeigt werden soll.
    Im Hauptthread anlegen, `laufen()` blockiert; aus anderen Threads nur
    `im_gui(...)`, `stop()`, `blase_spaeter(...)`."""

    def __init__(self, *, stand, chat, schleife: asyncio.AbstractEventLoop, sichtbar,
                 name: str, nutzer: str, nemicli_oeffnen, screenshot):
        self.app = O.anwendung()
        self._stand = stand
        self._chat = chat
        self._loop = schleife
        self._sichtbar = sichtbar
        self.name = name
        self.nutzer = nutzer
        self._nemicli = nemicli_oeffnen
        self._screenshot = screenshot

        self.fenster = _Kugelfenster()
        self.fenster.geklickt.connect(self.panel_umschalten)
        self.fenster.gezogen.connect(self._gezogen)
        self._blase: _Blase | None = None
        self._blase_bis = 0.0
        self._panel: _Panel | None = None
        self._anhaenge: list[Path] = []
        self._beschaeftigt = False
        self._gezeigt = False
        self._versteckt_fuer_foto = False
        self._farbe = "gruen"
        self._naechster_gruss = self._gruss_zeit()
        gruesse(name, nutzer)                        # legt Wache/gruesse.md mit Vorgaben an
        # Steuerung durch die Persönlichkeit (Wache/kugel_zustand.json): was beim Start schon
        # drinsteht (Stimmung, Größe, versteckt, Lage) gilt; alte Blasen/Gesten werden nicht nachgeholt.
        z = steuerung.lesen()
        self._gesteuert = {"sagen": float(z.get("sagen_zeit") or 0), "bewegung": float(z.get("bewegung_zeit") or 0),
                       "lage": 0.0, "stimmung": None, "groesse": None, "bild_pfad": None}
        self._versteckt = bool(z.get("versteckt"))
        self._zustand_anwenden(z)
        self.fenster.move(*self._lage_laden())

        self._puls = QTimer(); self._puls.setInterval(33); self._puls.timeout.connect(self._tick)
        self._takt = QTimer(); self._takt.setInterval(1500); self._takt.timeout.connect(self._takt_tick)
        self._puls.start(); self._takt.start()
        QTimer.singleShot(0, self._takt_tick)

    # ------------------------------------------------------------- Takt

    def _tick(self) -> None:
        if self._gezeigt:
            self.fenster.takt()

    def _zustand_anwenden(self, z: dict) -> None:
        """Was die Persönlichkeit über die Aktion `kugel` will – nur Neues wird umgesetzt."""
        st = self._gesteuert
        stimmung = str(z.get("stimmung") or "")
        if self.fenster.beschaeftigt and stimmung != "kugel":
            stimmung = "denkt"                     # während sie denkt: das Denk-Bild, falls es eins gibt
        try:
            g = int(z.get("groesse") or GROESSE)
        except (TypeError, ValueError):
            g = GROESSE
        if g != st["groesse"]:
            st["groesse"] = g
            self.fenster.groesse_setzen(g)
        if stimmung != st["stimmung"]:
            st["stimmung"] = stimmung
            pfad = None
            if stimmung != "kugel":
                da = steuerung.bilder(self.name)
                pfad = da.get(stimmung) or da.get("")
            if pfad != st["bild_pfad"]:
                st["bild_pfad"] = pfad
                self.fenster.bild_setzen(pfad)
        self._versteckt = bool(z.get("versteckt"))
        t = float(z.get("bewegung_zeit") or 0)
        if t > st["bewegung"]:
            st["bewegung"] = t
            self.fenster.bewegen(str(z.get("bewegung") or ""))
        t = float(z.get("sagen_zeit") or 0)
        if t > st["sagen"] and z.get("sagen"):
            st["sagen"] = t
            if self._panel is None:
                self.blase(str(z["sagen"]), float(z.get("sagen_sekunden") or 10))
        t = float(z.get("lage_zeit") or 0)
        if t > st["lage"]:
            st["lage"] = t
            if z.get("ecke"):
                self._in_ecke(str(z["ecke"]))
            elif isinstance(z.get("position"), list) and len(z["position"]) == 2:
                g = _schirm(QPoint(int(z["position"][0]), int(z["position"][1])))
                x = max(g.left(), min(int(z["position"][0]), g.right() - self.fenster.width()))
                y = max(g.top(), min(int(z["position"][1]), g.bottom() - self.fenster.height()))
                self.fenster.move(x, y)
                self._gezogen()

    def _in_ecke(self, ecke: str) -> None:
        g = _schirm(self.fenster.geometry().center())
        w, h = self.fenster.width(), self.fenster.height()
        x = {"oben_links": g.left() + 24, "unten_links": g.left() + 24,
             "mitte": g.center().x() - w // 2}.get(ecke, g.right() - w - 24)
        y = {"oben_links": g.top() + 24, "oben_rechts": g.top() + 24,
             "mitte": g.center().y() - h // 2}.get(ecke, g.bottom() - h - 60)
        self.fenster.move(x, y)
        self._gezogen()

    def _takt_tick(self) -> None:
        try:
            s = self._stand()
            self._farbe = s.get("farbe", "gruen")
            self.fenster.farbe = self._farbe
            self.fenster.setToolTip(str(s.get("text", "")))
            if self._panel is not None:
                self._panel.lage(self._farbe)
            try:
                self._zustand_anwenden(steuerung.lesen())
            except Exception:
                pass
            soll = bool(self._sichtbar()) and not self._versteckt_fuer_foto and not self._versteckt
            if soll and not self._gezeigt:
                self.fenster.show()
                self._gezeigt = True
            elif not soll and self._gezeigt:
                self.fenster.hide()
                self._gezeigt = False
                self._blase_weg()
                if self._panel is not None and not self._versteckt_fuer_foto:
                    self._panel_schliessen()
            if self._gezeigt:
                self._gruss_pruefen(s)
            if self._blase is not None and time.time() > self._blase_bis:
                self._blase_weg()
        except Exception:
            pass

    # ------------------------------------------------------------- Bewegen

    def _gezogen(self) -> None:
        self._panel_platzieren()
        if self._blase is not None:
            self._blase_platzieren()
        self._lage_speichern()

    def _lage_laden(self) -> tuple[int, int]:
        g = _schirm(QCursor.pos())
        try:
            d = json.loads(LAGE_DATEI.read_text(encoding="utf-8"))
            x, y = int(d["x"]), int(d["y"])
            g = _schirm(QPoint(x, y))
            return (max(g.left(), min(x, g.right() - self.fenster.width())),
                    max(g.top(), min(y, g.bottom() - self.fenster.height())))
        except Exception:
            return g.right() - self.fenster.width() - 24, g.bottom() - self.fenster.height() - 60

    def _lage_speichern(self) -> None:
        try:
            LAGE_DATEI.parent.mkdir(parents=True, exist_ok=True)
            LAGE_DATEI.write_text(json.dumps({"x": self.fenster.x(), "y": self.fenster.y()}), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------- Blase / Grüße

    def _gruss_zeit(self) -> float:
        return time.time() + random.uniform(2.5, 7.0) * 3600

    def _gruss_pruefen(self, s: dict) -> None:
        jetzt = time.time()
        if s.get("farbe") == "rot" and jetzt >= self._naechster_gruss - 3 * 3600:
            self.blase("Ich hab was gefunden – klick mich. 🔴", 12)
            self._naechster_gruss = jetzt + 1800
            return
        if jetzt < self._naechster_gruss:
            return
        self._naechster_gruss = self._gruss_zeit()
        stunde = datetime.now().hour
        if s.get("farbe") != "gruen" or stunde < 8 or stunde >= 23:
            return
        self.blase(random.choice(gruesse(self.name, self.nutzer)), 9)

    def blase(self, text: str, sekunden: float = 8.0) -> None:
        """Kurze Sprechblase neben der Kugel (GUI-Thread)."""
        self._blase_weg()
        b = _Blase()
        b.geklickt.connect(self.panel_umschalten)
        b.zeigen(text, ernst=self._farbe == "rot")
        self._blase = b
        self._blase_platzieren()
        self._blase_bis = time.time() + sekunden

    def blase_spaeter(self, text: str, sekunden: float = 8.0) -> None:
        """Dasselbe aus einem fremden Thread."""
        O.im_gui(lambda: self.blase(text, sekunden))

    def _blase_platzieren(self) -> None:
        if self._blase is None:
            return
        k = self.fenster.geometry()
        g = _schirm(k.center())
        x = k.left() + RAND - self._blase.width() - 6
        if x < g.left():
            x = k.right() - RAND + 6
        y = max(g.top(), k.top() + RAND - 6)
        self._blase.move(x, y)

    def _blase_weg(self) -> None:
        if self._blase is not None:
            try:
                self._blase.close(); self._blase.deleteLater()
            except Exception:
                pass
            self._blase = None

    # ------------------------------------------------------------- Chatfenster

    def panel_umschalten(self) -> None:
        if self._panel is not None:
            self._panel_schliessen()
        else:
            self._panel_oeffnen()

    def _panel_platzieren(self) -> None:
        if self._panel is None:
            return
        k = self.fenster.geometry()
        g = _schirm(k.center())
        w, h = PANEL_BREITE, PANEL_HOEHE
        x = k.left() + RAND - w - 12
        if x < g.left():
            x = min(k.right() - RAND + 12, g.right() - w)
        y = max(g.top(), min(k.bottom() - RAND - h, g.bottom() - h))
        self._panel.move(x, y)

    def _panel_oeffnen(self) -> None:
        p = _Panel(self.name)
        p.geschlossen.connect(self._panel_schliessen)
        p.senden.connect(self._senden_klick)
        p.screenshot.connect(self._screenshot_klick)
        p.alles_ok.connect(lambda: self.senden("Alles ok bei dir? Gib mir kurz die Lage der Wache."))
        p.nemicli.connect(self._nemicli)
        p.lage(self._farbe)
        self._panel = p
        self._panel_platzieren()
        p.show()
        p.hinweis("Klick auf die Kugel schließt das Fenster. Enter sendet. Hier wird nur gelesen und "
                  "geredet – Änderungen am PC machst du im großen NemiCLI.")
        if self._beschaeftigt:
            p.denkt(True)
        p.eingabe.setFocus()
        p.raise_(); p.activateWindow()
        self._blase_weg()

    def _panel_schliessen(self) -> None:
        if self._panel is not None:
            try:
                self._panel.close(); self._panel.deleteLater()
            except Exception:
                pass
            self._panel = None
            self._anhaenge = []

    def _schreiben(self, wer: str, text: str) -> None:
        if self._panel is None:
            return
        if wer in ("du", "sie"):
            self._panel.nachricht(wer, text, self.name)
        else:
            self._panel.hinweis(text)

    def _bild_zeigen(self, pfad: Path) -> None:
        if self._panel is not None:
            self._panel.bild(pfad)

    def _senden_klick(self) -> None:
        if self._panel is None:
            return
        text = self._panel.text_nehmen()
        if not text and not self._anhaenge:
            return
        self.senden(text or "Schau dir bitte den Screenshot an.")

    def _screenshot_klick(self) -> None:
        try:
            pfad = self._screenshot()
        except Exception as exc:
            self._schreiben("hinweis", f"Screenshot fehlgeschlagen: {exc}")
            return
        if pfad:
            self._anhaenge.append(Path(pfad))
            self._schreiben("hinweis", f"📸 Screenshot angehängt: {Path(pfad).name} – schreib dazu, "
                                       "was sie sehen soll, oder drück Enter.")
            self._bild_zeigen(Path(pfad))

    def senden(self, text: str) -> None:
        if self._beschaeftigt:
            self._schreiben("hinweis", "(sie antwortet gerade noch …)")
            return
        if self._panel is None:
            self._panel_oeffnen()
        anhaenge, self._anhaenge = self._anhaenge, []
        self._schreiben("du", text)
        self._beschaeftigt = True
        self.fenster.beschaeftigt = True
        self._panel.denkt(True)
        self._zustand_anwenden(steuerung.lesen())          # Denk-Bild, falls vorhanden
        fut = asyncio.run_coroutine_threadsafe(self._chat(text, anhaenge), self._loop)

        def fertig(f):
            try:
                antwort, bilder = f.result()
            except Exception as exc:
                antwort, bilder = f"(Fehler: {exc})", []
            O.im_gui(lambda: self._antwort(antwort, bilder))

        fut.add_done_callback(fertig)

    def _antwort(self, antwort: str, bilder: list) -> None:
        self._beschaeftigt = False
        self.fenster.beschaeftigt = False
        self._zustand_anwenden(steuerung.lesen())
        if self._panel is not None:
            self._panel.denkt(False)
        self._schreiben("sie", antwort.strip() or "(keine Antwort)")
        for b in bilder:
            self._bild_zeigen(Path(b))
        if self._panel is None:                      # Fenster inzwischen zu → Blase
            kurz = antwort.strip().splitlines()[0][:120] if antwort.strip() else "Fertig."
            self.blase(kurz, 10)

    # ------------------------------------------------------------- Screenshot-Hilfe

    def verstecken(self, ja: bool) -> None:
        """Kugel und Fenster kurz weg, damit sie nicht selbst auf dem Foto sind (GUI-Thread)."""
        self._versteckt_fuer_foto = ja
        if ja:
            self.fenster.hide()
            if self._panel is not None:
                self._panel.hide()
            self._blase_weg()
        else:
            if self._gezeigt:
                self.fenster.show()
            if self._panel is not None:
                self._panel.show()
        QApplication.processEvents()

    # ------------------------------------------------------------- Laufen

    def im_gui(self, fn) -> None:
        O.im_gui(fn)

    def laufen(self) -> None:
        """Blockiert (Qt-Hauptschleife). Im Hauptthread aufrufen."""
        self.app.exec()

    def stop(self) -> None:
        O.im_gui(self.app.quit)
