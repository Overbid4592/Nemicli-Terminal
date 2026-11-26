"""office_kugel.py – 🔮 OfficeAI: kleine Kugel mit dem Gesicht der Persönlichkeit unten rechts im Office-Fenster.

Drüberfahren oder Klicken klappt ein Eingabefeld aus; die Antwort und was gerade passiert, steht in einer Blase
über der Kugel. Beim Arbeiten zeigt sie das Gesicht „denkt“ und leuchtet, danach kurz „froh“. Die Teile liegen
einzeln über dem Fenster (keine große unsichtbare Fläche, die Klicks auf das Dokument abfängt).
"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QConicalGradient, QCursor, QMovie, QPainter, QPainterPath, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLineEdit,
                               QVBoxLayout, QWidget)

from design import FARBE, schrift
from gui_fenster import Knopf, Markdown, Tippen, label

KUGEL = 64
FELD_BREITE = 440
RAND = 18
LICHTHOF = 6                         # Platz um den Ring für Lichthof und Anheben (ohne die Kugel zu verschieben)
MAX_VERLAUF = 6
MAX_VERLAUF_ZEICHEN = 2000
BLASE_ZU_S = 30                      # Antwortblase blendet sich danach aus (außer die Maus ist darauf)

# Schnelle Aufträge je App: (Zeichen, Chip-Text, Auftrag)
AUFTRAEGE = {
    "schreiben": [
        ("✨", "Verbessern", "Verbessere Rechtschreibung, Grammatik und Stil direkt im Dokument – nur die Markierung, "
                            "ist nichts markiert, das ganze Dokument."),
        ("✂", "Kürzer", "Mach den Text kürzer und klarer, ohne Inhalt zu verlieren – direkt im Dokument."),
        ("🌸", "Freundlicher", "Formuliere den Text freundlicher und wärmer – direkt im Dokument."),
        ("📨", "Als Brief", "Mach aus dem Dokument einen ordentlichen Brief mit Anrede, klarer Gliederung und Gruß."),
        ("🎨", "Schön machen", "Gestalte das Dokument übersichtlich: Überschriften, wichtige Wörter hervorheben, "
                              "sparsam Farbe. Inhalt nicht ändern."),
        ("🔍", "Fehler finden", "Finde Fehler und liste sie kurz auf (Fundstelle → Vorschlag). Ändere nichts."),
    ],
    "tabelle": [
        ("🎨", "Schön machen", "Gestalte die Tabelle übersichtlich: Kopfzeile grau hinterlegt und fett, Summenzeile "
                              "hervorheben. Inhalte nicht ändern."),
        ("Σ", "Summen", "Ergänze unter den Zahlenspalten eine Summenzeile mit Formeln."),
        ("💡", "Erklären", "Erkläre kurz, was in dieser Tabelle steht und was die Formeln machen. Ändere nichts."),
        ("📊", "Auswerten", "Werte die Tabelle aus: Summen, Auffälligkeiten, Tipps. Kurz, ändere nichts."),
    ],
    "malen": [
        ("✏️", "Zeichne was", "Zeichne selbst etwas Schönes und Fröhliches auf die ganze Leinwand – Form für Form: "
                            "erst Hintergrund, dann große Flächen, dann Details. Danach mit anschauen prüfen."),
        ("👀", "Was ist das?", "Schau dir die Leinwand mit anschauen an und sag mir kurz, was darauf zu sehen ist. "
                             "Ändere nichts."),
        ("✨", "Verschönern", "Verschönere meine Zeichnung, ohne sie zu ersetzen: schau sie dir erst an, dann "
                             "ergänze einen passenden Hintergrund, Farbe und kleine Details."),
        ("🖼", "Als Gemälde", "Mach mit umzeichnen aus der Leinwand ein schönes Gemälde – schau sie dir vorher an "
                             "und wähle einen Stil, der zum Motiv passt."),
    ],
}


def leben_an() -> bool:
    """Schalter `kugel_leben` (wie bei der Desktop-Kugel): Atmen, Blick und bewegtes Porträt."""
    try:
        import config
        return bool(config.load().get("kugel_leben", True))
    except Exception:
        return True


def _quadrat(bild: QPixmap, kante: int) -> QPixmap:
    """Mittiges Quadrat, einmal vorskaliert – gezeichnet wird je Bild nur noch das kleine Stück."""
    s = min(bild.width(), bild.height())
    stueck = bild.copy((bild.width() - s) // 2, 0 if bild.height() > bild.width() else (bild.height() - s) // 2, s, s)
    return stueck.scaled(kante, kante, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


class _Kugel(QAbstractButton):
    """Runde Kugel: Gesicht im Kreis, Leuchtring. Beim Arbeiten dreht sich der Ring schnell; mit `leben` atmet sie in
    Ruhe (Lichthof, langsamer Ring), schaut leicht zur Maus, hebt sich unter ihr, blendet Gesichter weich über und
    spielt ein bewegtes Porträt ab. Minimiert oder versteckt ruht der Takt."""
    TAKT_MS, RUHE_MS, SCHLAF_MS = 40, 66, 500
    DREH_ARBEIT, DREH_RUHE = 150.0, 18.0             # Grad je Sekunde
    ATEM_S = 4.0
    WECHSEL_S = 0.35
    BLICK_PX = 2.5
    RUNDEN = 2                                       # so oft läuft ein bewegtes Porträt

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(KUGEL + 12 + 2 * LICHTHOF, KUGEL + 12 + 2 * LICHTHOF)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.bild: QPixmap | None = None
        self.buchstabe = "N"
        self.arbeitet = False
        self._gruende: set[str] = set()
        self.leben = leben_an()
        self.winkel = 0.0
        self.hebung = 0.0
        self.blick = [0.0, 0.0]
        self._start = time.monotonic()
        self._zuletzt = self._start
        self._alt: QPixmap | None = None
        self._wechsel = 0.0
        self._quadrate: dict[int, QPixmap] = {}
        self._film: QMovie | None = None
        self._film_rest = 0
        self._takt = QTimer(self)
        self._takt.timeout.connect(self._tick)
        self.setToolTip("OfficeAI – drüberfahren oder klicken")
        self._takt_planen()

    def sizeHint(self) -> QSize:
        return QSize(KUGEL + 12 + 2 * LICHTHOF, KUGEL + 12 + 2 * LICHTHOF)

    # -- Zustand ---------------------------------------------------------------------------
    def arbeiten(self, an: bool, grund: str = "ki") -> None:
        """Ring dreht schnell, solange ein Grund besteht (KI arbeitet, Bild entsteht …)."""
        (self._gruende.add if an else self._gruende.discard)(grund)
        self.arbeitet = bool(self._gruende)
        self._takt_planen()
        self.update()

    def bild_setzen(self, bild: QPixmap | None) -> None:
        if bild is self.bild:
            return
        if self.bild is not None and bild is not None and self.isVisible():
            self._alt, self._wechsel = self.bild, time.monotonic()
        self.bild = bild
        self._takt_planen()
        self.update()

    def film(self, pfad: str, runden: int = RUNDEN) -> bool:
        """Bewegtes Porträt (WebP) einige Runden in der Kugel zeigen, danach wieder das Bild."""
        if not self.leben or self._film is not None:
            return False
        film = QMovie(str(pfad), parent=self)
        if not film.isValid():
            film.deleteLater()
            return False
        self._film = film
        self._film_rest = runden * max(1, film.frameCount())
        film.frameChanged.connect(self._film_bild)
        film.start()
        return True

    def _film_bild(self, _nr: int) -> None:
        self._film_rest -= 1
        if self._film_rest <= 0:
            self.film_aus()
        self.update()

    def film_aus(self) -> None:
        if self._film is None:
            return
        film, self._film = self._film, None
        film.stop()
        film.deleteLater()                           # kann aus frameChanged des Films selbst kommen
        self.update()

    # -- Takt ------------------------------------------------------------------------------
    def _laeuft_etwas(self) -> bool:
        return self.arbeitet or self.leben or (self._alt is not None)

    def _takt_planen(self) -> None:
        if not self._laeuft_etwas():
            self._takt.stop()
            return
        ms = self.TAKT_MS if (self.arbeitet or self._alt is not None) else self.RUHE_MS
        if not self.isVisible() or self.window().isMinimized():
            ms = self.SCHLAF_MS
        if self._takt.interval() != ms or not self._takt.isActive():
            self._takt.start(ms)

    def _tick(self) -> None:
        jetzt = time.monotonic()
        dt, self._zuletzt = min(0.25, jetzt - self._zuletzt), jetzt
        self._takt_planen()
        if self._takt.interval() == self.SCHLAF_MS:
            return
        if self.arbeitet or self.leben:
            self.winkel = (self.winkel + dt * (self.DREH_ARBEIT if self.arbeitet else self.DREH_RUHE)) % 360
        if self.leben:
            ziel = 1.0 if self.underMouse() else 0.0
            self.hebung += (ziel - self.hebung) * min(1.0, dt * 9)
            self._blick_folgen(dt)
        if self._alt is not None and jetzt - self._wechsel >= self.WECHSEL_S:
            self._alt = None
        self.update()

    def _blick_folgen(self, dt: float) -> None:
        mitte = self.mapToGlobal(QPointF(self.width() / 2, self.height() / 2).toPoint())
        maus = QCursor.pos()
        dx, dy = maus.x() - mitte.x(), maus.y() - mitte.y()
        weite = math.hypot(dx, dy)
        ziel = (0.0, 0.0) if weite < 1 else (dx / weite * self.BLICK_PX * min(1.0, weite / 300),
                                              dy / weite * self.BLICK_PX * min(1.0, weite / 300))
        for i in (0, 1):
            self.blick[i] += (ziel[i] - self.blick[i]) * min(1.0, dt * 4)

    def atem(self) -> float:
        """0 … 1, sanfter Sinus mit ATEM_S Sekunden je Atemzug."""
        if not self.leben:
            return 0.0
        return 0.5 + 0.5 * math.sin(2 * math.pi * (time.monotonic() - self._start) / self.ATEM_S)

    def showEvent(self, e):
        super().showEvent(e)
        self._takt_planen()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._takt_planen()

    # -- Malen -----------------------------------------------------------------------------
    def _quadrat_von(self, bild: QPixmap) -> QPixmap:
        schluessel = bild.cacheKey()
        if schluessel not in self._quadrate:
            if len(self._quadrate) > 8:
                self._quadrate.clear()
            self._quadrate[schluessel] = _quadrat(bild, int(KUGEL * 2.4))
        return self._quadrate[schluessel]

    def _gesicht_malen(self, p: QPainter, bild: QPixmap, mitte: QPointF, r: float, quadrat: bool) -> None:
        q = self._quadrat_von(bild) if quadrat else _quadrat(bild, int(KUGEL * 2.4))
        rand = r * 1.08                              # etwas größer als der Kreis: Platz für den Blick
        p.drawPixmap(QRectF(mitte.x() - rand + self.blick[0], mitte.y() - rand + self.blick[1], 2 * rand, 2 * rand),
                     q, QRectF(q.rect()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        atem = self.atem()
        skala = 1.0 + 0.02 * atem + 0.05 * self.hebung
        mitte = QPointF(self.width() / 2, self.height() / 2 - 2.5 * self.hebung)
        r_aussen = (KUGEL / 2 + 4) * skala
        p.setPen(Qt.PenStyle.NoPen)
        if self.leben or self.arbeitet:                  # Lichthof hinter dem Ring
            weit = min(self.width() / 2 - 0.5, r_aussen + 7)
            hof = QRadialGradient(mitte, weit)
            a = 150 if self.arbeitet else int(45 + 70 * atem + 50 * self.hebung)
            hof.setColorAt(r_aussen / weit * 0.9, QColor(168, 85, 247, a))
            hof.setColorAt(1.0, QColor(168, 85, 247, 0))
            p.setBrush(hof)
            p.drawEllipse(mitte, weit, weit)
        ring = QConicalGradient(mitte, -self.winkel)
        for pos, c in ((0.0, "#22d3ee"), (0.33, "#a855f7"), (0.66, "#ec4899"), (1.0, "#22d3ee")):
            ring.setColorAt(pos, QColor(c))
        p.setBrush(ring)
        p.drawEllipse(mitte, r_aussen, r_aussen)
        r = KUGEL / 2 * skala
        kreis = QPainterPath()
        kreis.addEllipse(mitte, r, r)
        p.setClipPath(kreis)
        film = self._film.currentPixmap() if self._film is not None else None
        if film is not None and not film.isNull():
            p.fillPath(kreis, QColor(FARBE["flaeche"]))
            self._gesicht_malen(p, film, mitte, r, quadrat=False)
        elif self.bild is not None and not self.bild.isNull():
            t = (time.monotonic() - self._wechsel) / self.WECHSEL_S
            if self._alt is not None and t < 1.0:        # weicher Übergang zum neuen Gesicht
                self._gesicht_malen(p, self._alt, mitte, r, quadrat=True)
                p.setOpacity(max(0.0, t))
            self._gesicht_malen(p, self.bild, mitte, r, quadrat=True)
            p.setOpacity(1.0)
        else:
            g = QRadialGradient(mitte - QPointF(r / 3, r / 3), r * 1.4)
            g.setColorAt(0, QColor("#f472b6"))
            g.setColorAt(0.45, QColor("#8b5cf6"))
            g.setColorAt(1, QColor("#0ea5e9"))
            p.fillPath(kreis, g)
            p.setPen(QColor("#ffffff"))
            p.setFont(schrift(20, True))
            p.drawText(QRectF(mitte.x() - r, mitte.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, self.buchstabe)
        if self.underMouse() and not self.arbeitet:
            p.fillPath(kreis, QColor(255, 255, 255, 30))


class _Feld(QFrame):
    """Ausklappendes Eingabefeld (Pille) links neben der Kugel."""
    senden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("kugelfeld")
        self.setStyleSheet(f"QFrame#kugelfeld {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']};"
                           f" border-radius: 24px; }} QLineEdit {{ background: transparent; border: none; "
                           f"color: {FARBE['text']}; }}")
        self.setFixedHeight(48)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 4, 6, 4)
        self.eingabe = QLineEdit()
        self.eingabe.setFont(schrift(11))
        self.eingabe.setPlaceholderText("Frag mich oder sag, was ich machen soll …")
        self.eingabe.returnPressed.connect(self.senden.emit)
        h.addWidget(self.eingabe, 1)
        self.knopf = Knopf("", "➤", klein=True)
        self.knopf.akzent = True
        self.knopf.clicked.connect(self.senden.emit)
        h.addWidget(self.knopf)
        schatten = QGraphicsDropShadowEffect(self)
        schatten.setBlurRadius(24)
        schatten.setOffset(0, 4)
        schatten.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(schatten)


class _Blase(QFrame):
    """Blase über der Kugel: Name, was gerade passiert, Antwort, schnelle Aufträge."""
    schliessen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("kugelblase")
        self.setStyleSheet(f"QFrame#kugelblase {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']};"
                           f" border-radius: 16px; }}")
        self.setFixedWidth(FELD_BREITE + KUGEL)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 12, 12)
        v.setSpacing(6)
        kopf = QHBoxLayout()
        self.titel = label("OfficeAI", 10.5, "text", fett=True)
        kopf.addWidget(self.titel, 1)
        zu = Knopf("", "✕", klein=True)
        zu.setToolTip("Schließen")
        zu.clicked.connect(self.schliessen.emit)
        kopf.addWidget(zu)
        v.addLayout(kopf)
        zeile = QHBoxLayout()
        self.tippen = Tippen()
        zeile.addWidget(self.tippen)
        self.status = label("", 9.5, "dim")
        zeile.addWidget(self.status, 1)
        self.zeile = QWidget()
        self.zeile.setLayout(zeile)
        v.addWidget(self.zeile)
        self.text = Markdown()
        self.text.setFont(schrift(10.5))
        v.addWidget(self.text)
        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 2, 0, 0)
        self.chips_lay.setSpacing(4)
        v.addWidget(self.chips)
        schatten = QGraphicsDropShadowEffect(self)
        schatten.setBlurRadius(28)
        schatten.setOffset(0, 6)
        schatten.setColor(QColor(0, 0, 0, 150))
        self.setGraphicsEffect(schatten)

    def zeigen(self, status: str = "", text: str = "", laeuft: bool = False) -> None:
        self.zeile.setVisible(bool(status) or laeuft)
        self.status.setText(status)
        (self.tippen.start if laeuft else self.tippen.stop)()
        self.tippen.setVisible(laeuft)
        self.text.setVisible(bool(text))
        if text:
            self.text.setzen(text)
        self.adjustSize()


class OfficeAI(QWidget):
    """Unsichtbarer Träger der Signale; die sichtbaren Teile sind Kinder des Fensters."""
    frage_senden = Signal(dict)
    stopp = Signal(int)

    def __init__(self, fenster: QWidget, kontext_fn):
        super().__init__(fenster)
        self.hide()
        self.fenster = fenster
        self.kontext_fn = kontext_fn
        self.name = "Nemi"
        self.modell = True
        self.app = "start"
        self.gesichter: dict[str, QPixmap] = {}
        self.bewegt = ""                     # bewegtes Porträt (…_kugel.webp), falls vorhanden
        self._begruesst = False
        self.nr = 0
        self.laufend: int | None = None
        self.verlauf: list[dict] = []
        self.offen = False

        self.kugel = _Kugel(fenster)
        self.kugel.clicked.connect(self._klick)
        self.kugel.installEventFilter(self)
        self.feld = _Feld(fenster)
        self.feld.senden.connect(self._senden_oder_stopp)
        self.feld.installEventFilter(self)
        self.feld.eingabe.installEventFilter(self)
        self.feld.setFixedWidth(0)
        self.feld.hide()
        self.blase = _Blase(fenster)
        self.blase.schliessen.connect(self.blase.hide)
        self.blase.installEventFilter(self)
        self.blase.hide()
        self._breite = QPropertyAnimation(self.feld, b"maximumWidth", self)
        self._breite.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._breite.valueChanged.connect(lambda _w: self.anordnen())
        self._zu_uhr = QTimer(self)
        self._zu_uhr.setSingleShot(True)
        self._zu_uhr.timeout.connect(self._vielleicht_einklappen)
        self._blase_uhr = QTimer(self)
        self._blase_uhr.setSingleShot(True)
        self._blase_uhr.timeout.connect(self._blase_vielleicht_zu)
        self._gesicht_uhr = QTimer(self)
        self._gesicht_uhr.setSingleShot(True)
        self._gesicht_uhr.timeout.connect(lambda: self._gesicht(""))
        self.app_setzen("start")
        self.anordnen()

    # -- Stand -----------------------------------------------------------------------------
    def stand(self, name: str, modell: bool, gesichter: dict | None = None) -> None:
        self.name = name or "Nemi"
        self.modell = modell
        self.kugel.buchstabe = (self.name[:1] or "N").upper()
        self.gesichter = {}
        gesichter = dict(gesichter or {})
        bewegt = gesichter.pop("bewegt", "")
        self.bewegt = bewegt if isinstance(bewegt, str) and bewegt.lower().endswith("_kugel.webp") \
            and Path(bewegt).is_file() else ""
        self._begruesst = False
        for stimmung, pfad in gesichter.items():
            if isinstance(pfad, str) and Path(pfad).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif") \
                    and Path(pfad).is_file():
                bild = QPixmap(pfad)
                if not bild.isNull():
                    self.gesichter[str(stimmung)] = bild
        self.blase.titel.setText(f"{self.name} · OfficeAI")
        self.kugel.setToolTip(f"{self.name} – OfficeAI: drüberfahren oder klicken")
        self._gesicht("")

    def _gesicht(self, stimmung: str) -> None:
        self.kugel.bild_setzen(self.gesichter.get(stimmung) or self.gesichter.get(""))

    def film_zeigen(self) -> bool:
        """Das bewegte Porträt kurz abspielen (beim ersten Drüberfahren, nach einem fertigen Auftrag)."""
        return bool(self.bewegt) and self.kugel.film(self.bewegt)

    def app_setzen(self, app: str) -> None:
        self.app = app
        lay = self.blase.chips_lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for zeichen, text, auftrag in AUFTRAEGE.get(app, []):
            k = Knopf(text, zeichen, klein=True)
            k.setToolTip(auftrag)
            k.clicked.connect(lambda _=False, a=auftrag, t=text: self.fragen(a, t))
            lay.addWidget(k)
        lay.addStretch(1)
        self.blase.chips.setVisible(app in AUFTRAEGE)
        self.feld.eingabe.setPlaceholderText({
            "schreiben": "z. B. „Mach die Überschrift blau und den Brief freundlicher“",
            "tabelle": "z. B. „Kopfzeile grau und fett, darunter eine Summe“",
            "malen": "z. B. „Zeichne ein Haus mit Garten, Sonne und Regenbogen“"}.get(
            app, "Frag mich oder sag, was ich machen soll …"))

    # -- Lage ------------------------------------------------------------------------------
    def anordnen(self) -> None:
        f = self.fenster
        x_k = f.width() - self.kugel.width() - RAND + LICHTHOF
        y_k = f.height() - self.kugel.height() - RAND - 22 + LICHTHOF
        self.kugel.move(x_k, y_k)
        self.kugel.raise_()
        breite = self.feld.maximumWidth() if self.feld.isVisible() else 0
        self.feld.setFixedWidth(max(0, min(FELD_BREITE, breite)))
        self.feld.move(x_k - self.feld.width() - 8 + LICHTHOF, y_k + (self.kugel.height() - self.feld.height()) // 2)
        self.feld.raise_()
        if self.blase.isVisible():
            self.blase.adjustSize()
            self.blase.move(f.width() - self.blase.width() - RAND, y_k + LICHTHOF - self.blase.height() - 10)
            self.blase.raise_()

    def eventFilter(self, obj, ev):  # noqa: N802 – Qt-Name
        t = ev.type()
        if t == ev.Type.Enter:
            self._zu_uhr.stop()
            if obj is self.kugel:
                self.ausklappen()
        elif t == ev.Type.Leave:
            self._zu_uhr.start(1500)
            if obj is self.blase:
                self._blase_uhr.start(BLASE_ZU_S * 1000)
        elif t == ev.Type.FocusOut and obj is self.feld.eingabe:
            self._zu_uhr.start(1500)
        return False

    def ausklappen(self, fokus: bool = False) -> None:
        if not self._begruesst and not self.laufend:
            self._begruesst = True
            self.film_zeigen()
        if not self.offen:
            self.offen = True
            self.feld.show()
            self._breite.stop()
            self._breite.setDuration(220)
            self._breite.setStartValue(max(1, self.feld.width()))
            self._breite.setEndValue(FELD_BREITE)
            self.feld.setMaximumWidth(1)
            self._breite.start()
            if not self.blase.isVisible() and self.app in AUFTRAEGE and not self.laufend:
                self.blase.zeigen(status="", text=self._begruessung())
                self.blase.show()
            self.anordnen()
        if fokus:
            self.feld.eingabe.setFocus()

    def _begruessung(self) -> str:
        if not self.modell:
            return "Kein Modell verbunden – im Terminal mit /model eins wählen."
        return {"schreiben": "Sag mir, was ich am Text machen soll – oder nimm einen Knopf. Ich arbeite direkt im "
                             "Dokument, du siehst alles live. ESC hält mich an, Rückgängig holt es zurück.",
                "tabelle": "Sag mir, was ich an der Tabelle machen soll – oder nimm einen Knopf. Ich arbeite direkt "
                           "in den Zellen. ESC hält mich an, Rückgängig holt es zurück.",
                "malen": "Sag mir, was ich zeichnen soll – ich male Form für Form auf die Leinwand, du schaust zu. "
                         "Ich kann mir auch anschauen, was du gemalt hast. ESC hält mich an, Rückgängig holt es "
                         "zurück."}.get(self.app, "")

    def _vielleicht_einklappen(self) -> None:
        maus_drauf = any(w.underMouse() for w in (self.kugel, self.feld, self.blase) if w.isVisible())
        if maus_drauf or self.feld.eingabe.hasFocus() or self.feld.eingabe.text() or self.laufend:
            return
        self.einklappen()

    def einklappen(self) -> None:
        if not self.offen:
            return
        self.offen = False
        self._breite.stop()
        self.feld.hide()
        self.feld.setMaximumWidth(0)
        if not self.laufend and not self.blase.text.isVisible():
            self.blase.hide()
        self.anordnen()

    def _blase_vielleicht_zu(self) -> None:
        if not self.blase.underMouse() and not self.laufend and not self.offen:
            self.blase.hide()

    def _klick(self) -> None:
        if self.offen and not self.feld.eingabe.text() and not self.laufend:
            self.einklappen()
            self.blase.hide()
        else:
            self.ausklappen(fokus=True)

    # -- Fragen ----------------------------------------------------------------------------
    def _senden_oder_stopp(self) -> None:
        if self.laufend is not None and not self.feld.eingabe.text().strip():
            self.anhalten()
            return
        self.fragen()

    def fragen(self, auftrag: str | None = None, anzeige: str | None = None) -> None:
        text = auftrag if isinstance(auftrag, str) else self.feld.eingabe.text().strip()
        if not text:
            return
        if self.laufend is not None:
            self.anhalten()
        if auftrag is None:
            self.feld.eingabe.clear()
        self.nr += 1
        self.laufend = self.nr
        kontext = self.kontext_fn() or {"app": "start", "name": "", "kontext": "", "auswahl": ""}
        self.frage_senden.emit({"op": "frage", "id": self.nr, "text": text, **kontext,
                                "verlauf": self.verlauf[-MAX_VERLAUF:]})
        self.verlauf.append({"rolle": "du", "text": text[:MAX_VERLAUF_ZEICHEN]})
        self._arbeiten(True)
        self.blase.zeigen(status=f"„{(anzeige or text)[:60]}“ – {self.name} denkt nach …", laeuft=True)
        self.blase.show()
        self.ausklappen()
        self.anordnen()

    def anhalten(self) -> None:
        if self.laufend is not None:
            self.stopp.emit(self.laufend)

    def status(self, nr: int, text: str) -> None:
        if nr == self.laufend:
            self.blase.zeigen(status=f"{self.name} {text}", laeuft=True)
            self.blase.show()
            self.anordnen()

    def antwort(self, nr: int, text: str, geaendert: bool = False) -> None:
        if nr != self.laufend:
            return
        self.laufend = None
        self._arbeiten(False)
        sauber = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
        self.verlauf.append({"rolle": "nemi", "text": sauber[:MAX_VERLAUF_ZEICHEN]})
        self.blase.zeigen(status="✓ fertig – Rückgängig holt alles zurück" if geaendert else "",
                          text=sauber or "Fertig.")
        self.blase.show()
        if geaendert:
            self._gesicht("froh" if "froh" in self.gesichter else "stolz")
            self._gesicht_uhr.start(4000)
        self.film_zeigen()
        self._blase_uhr.start(BLASE_ZU_S * 1000)
        self.anordnen()

    def hinweis(self, text: str) -> None:
        """Meldung des Fensters (z. B. Datei nicht lesbar) – ein laufender Auftrag bleibt davon unberührt."""
        if self.laufend is not None:
            return
        self.blase.zeigen(status="", text="⚠ " + text)
        self.blase.show()
        self._blase_uhr.start(BLASE_ZU_S * 1000)
        self.anordnen()

    def fehler(self, nr: int | None, text: str) -> None:
        if nr is not None and nr != self.laufend:
            return
        self.laufend = None
        self._arbeiten(False)
        self.blase.zeigen(status="", text=("⚠ " if not text.startswith("⏹") else "") + text)
        self.blase.show()
        self._blase_uhr.start(BLASE_ZU_S * 1000)
        self.anordnen()

    def _arbeiten(self, an: bool) -> None:
        self.kugel.arbeiten(an)
        self._gesicht("denkt" if an else "")
        self.feld.knopf.symbol = "⏹" if an else "➤"
        self.feld.knopf.setToolTip("Anhalten (ESC)" if an else "Senden (Enter)")
        self.feld.knopf.update()

    def leeren(self) -> None:
        self.verlauf.clear()
