"""control_fenster.py – 🎛 Nemi Controllcenter (/control): Einstellungen, Gesundheit, System und Favoriten.

Oben eine schlanke Kopfleiste, links der Einstellungsbaum in Gruppen (Übersicht · Einstellungen · Daten &
Werkzeuge · Info mit 🔢 Version & Updates zuletzt; Befehle zweizeilig: Name und Kurztext), in der Mitte zuerst die
KI mit ihrem Eingabefeld und Kacheln für den Schnellzugriff. Beim ersten Klick im Baum (oder der ersten Frage)
fliegt das Feld als kleine Kugel nach unten rechts, die Mitte zeigt die gewählte Einstellung. Die Kugel bleibt
bereit für Fragen.

Eigener Prozess an der Sitzung (Leitung wie gui_fenster). Ausgeführt wird nur, was hier angeklickt wird; die KI
schlägt vor (Ja/Nein in der Blase) und bewegt den lila Zeiger nur, um etwas zu zeigen.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                               QScrollArea, QSpinBox,
                               QStackedWidget, QStyle, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

import design
from design import FARBE, schrift
from gui_fenster import Knopf, _dunkler_rahmen, app_vorbereiten, label
from office_agent import FLUG_MS, Geistzeiger
from office_kugel import LICHTHOF, OfficeAI

ROLLE_ID = Qt.ItemDataRole.UserRole
ROLLE_ART = Qt.ItemDataRole.UserRole + 1          # gruppe · ast (Thema) · seite · befehl
ROLLE_NAME = Qt.ItemDataRole.UserRole + 2
ROLLE_KURZ = Qt.ItemDataRole.UserRole + 3
ROLLE_ZAHL = Qt.ItemDataRole.UserRole + 4
KACHELN = (("gesundheit", "🩺", "Gesundheit", "Alles prüfen und reparieren"),
           ("system", "💻", "System", "Grafikkarte, Speicher, Python"),
           ("favoriten", "⭐", "Favoriten", "Deine Befehle im Terminal"),
           ("modelle", "📦", "Heruntergeladenes", "Modelle und ihre Größe"),
           ("tresor", "🔐", "Tresor", "Schlüssel und Konten"),
           ("befehl:/version", "🔢", "Version & Updates", "Stand und Aktualisieren"))
SCHRIFT_SCHRITT = 2                    # A−/A+ ändern die Schrift um so viele px
SCHRIFT_MIN, SCHRIFT_MAX = 12, 28
_OFFEN: list = []                      # das aktuelle Fenster (nach dem Neuaufbau ein neues Objekt)
SEITEN = {"start": 0, "befehl": 1, "gesundheit": 2, "system": 3, "modelle": 4, "terminal": 5, "tresor": 6, "sd": 7,
          "hub": 8, "rangliste": 9, "gesetzbuch": 10, "krea": 11}
HUB_ALS_MODELL = {"chat": "/model", "bild": "/bildmodel", "sehen": "/vision"}    # „Als Modell wählen“ je Art
HUB_FRAGE = ("NemiCLI verbindet sich jetzt mit huggingface.co, damit du Modelle suchen und laden kannst.\n\n"
             "Die Verbindung bleibt nur offen, solange das Controllcenter offen ist – oder bis du sie schließt.")
SD_VORLAGEN_ZEIGEN = 60                # so viele Vorlagen stehen auf einmal in der Liste (Suche grenzt ein)
ZEICHEN = {"ok": "✅", "warn": "⚠️", "fehler": "❌"}
AUFTRAEGE = [
    ("🩺", "Alles prüfen", "Mach eine Selbstdiagnose (Werkzeug pruefen) und erkläre mir kurz, was gut ist und was "
                          "ich tun sollte."),
    ("💡", "Was ist das?", "Erkläre mir einfach, was ich gerade ausgewählt habe und was die Knöpfe bewirken."),
    ("⭐", "Favoriten", "Welche Befehle sollte ich als Favoriten im Terminal haben? Frag mich kurz, wofür ich "
                       "NemiCLI am meisten nutze."),
]


def _feld_stil() -> str:
    return (f"QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {FARBE['flaeche']}; "
            f"color: {FARBE['text']}; border: 1px solid {FARBE['rand']}; border-radius: 8px; padding: 4px 6px; }}")


def _stil_rahmen(name: str, rund: int = 16, hell: bool = False) -> str:
    return (f"QFrame#{name} {{ background: {FARBE['flaeche_hell' if hell else 'flaeche']}; "
            f"border: 1px solid {FARBE['rand']}; border-radius: {rund}px; }}")


def _groesser(f: QFont, faktor: float) -> QFont:
    """Schrift um `faktor` größer oder kleiner – egal, ob sie in Punkt oder Pixel angegeben ist."""
    f = QFont(f)
    if f.pixelSize() > 0:
        f.setPixelSize(max(6, round(f.pixelSize() * faktor)))
    else:
        f.setPointSizeF(max(5.0, f.pointSizeF() * faktor))
    return f


class _BaumZeichner(QStyledItemDelegate):
    """Zeichnet den Baum modern: Gruppen als kleine Überschrift, Themen mit Zähler, Befehle zweizeilig (Name fett,
    Kurztext gedämpft darunter), die Auswahl als runde Fläche mit lila Strich."""

    def sizeHint(self, option, index):
        h = QFontMetrics(option.font).height()
        art = index.data(ROLLE_ART)
        hoehe = {"gruppe": h * 2.0, "befehl": h * 2.75}.get(art, h * 2.05)
        return QSize(super().sizeHint(option, index).width(), int(hoehe))

    def paint(self, p: QPainter, option, index) -> None:
        art = index.data(ROLLE_ART)
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gewaehlt = bool(option.state & QStyle.StateFlag.State_Selected)
        drueber = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if art != "gruppe" and (gewaehlt or drueber):
            box = r.adjusted(2, 2, -6, -2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(FARBE["flaeche_hoch" if gewaehlt else "flaeche_hell"]))
            p.drawRoundedRect(box, 10, 10)
            if gewaehlt:
                p.setBrush(QColor(FARBE["lila"]))
                p.drawRoundedRect(QRectF(box.left() + 3, box.top() + 9, 3.5, box.height() - 18), 1.75, 1.75)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        links = r.adjusted(14, 0, -12, 0)
        if art == "gruppe":
            f = _groesser(option.font, 0.78)
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
            p.setFont(f)
            p.setPen(QColor(FARBE["blass"]))
            p.drawText(r.adjusted(6, 0, -8, -5), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       text.upper())
        elif art == "befehl":
            name, kurz = str(index.data(ROLLE_NAME) or text), str(index.data(ROLLE_KURZ) or "")
            oben, unten = QRectF(links.left(), r.top() + 5, links.width(), r.height() / 2 - 3), \
                QRectF(links.left(), r.top() + r.height() / 2, links.width(), r.height() / 2 - 5)
            f = QFont(option.font)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(FARBE["text"] if gewaehlt else FARBE["cyan"]))
            p.drawText(oben, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, name)
            klein = _groesser(option.font, 0.86)
            p.setFont(klein)
            p.setPen(QColor(FARBE["dim"]))
            p.drawText(unten, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       QFontMetrics(klein).elidedText(kurz, Qt.TextElideMode.ElideRight, int(unten.width())))
        else:
            f = QFont(option.font)
            f.setBold(True)
            p.setFont(f)
            zahl = index.data(ROLLE_ZAHL)
            platz = 0
            if zahl:
                klein = _groesser(option.font, 0.8)
                fm = QFontMetrics(klein)
                breit = fm.horizontalAdvance(str(zahl)) + 16
                pille = QRectF(links.right() - breit, r.center().y() - fm.height() / 2 - 2, breit, fm.height() + 4)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(FARBE["flaeche_hell"] if not gewaehlt else FARBE["flaeche"]))
                p.drawRoundedRect(pille, pille.height() / 2, pille.height() / 2)
                p.setFont(klein)
                p.setPen(QColor(FARBE["blass"]))
                p.drawText(pille, Qt.AlignmentFlag.AlignCenter, str(zahl))
                p.setFont(f)
                platz = breit + 8
            p.setPen(QColor(FARBE["text"]))
            p.drawText(links.adjusted(0, 0, -platz, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f).elidedText(text, Qt.TextElideMode.ElideRight, int(links.width() - platz)))
        p.restore()


class _Kachel(QFrame):
    """Kachel für den Schnellzugriff: Zeichen, Titel, eine Zeile Beschreibung; leuchtet unter der Maus."""
    geklickt = Signal()

    def __init__(self, zeichen: str, titel: str, text: str):
        super().__init__()
        self.setObjectName("kachel")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"QFrame#kachel {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; "
                           f"border-radius: 16px; }} QFrame#kachel:hover {{ border: 1px solid {FARBE['lila']}; "
                           f"background: {FARBE['flaeche_hell']}; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(12)
        symbol = QLabel(zeichen)
        symbol.setFont(schrift(16))
        symbol.setFixedSize(44, 44)
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setStyleSheet(f"background: {FARBE['flaeche_hoch']}; border-radius: 12px; border: none;")
        h.addWidget(symbol)
        texte = QVBoxLayout()
        texte.setSpacing(1)
        t = label(titel, 11, "text", fett=True)
        t.setStyleSheet("border: none; background: transparent;")
        u = label(text, 9.5, "dim")
        u.setStyleSheet("border: none; background: transparent;")
        texte.addWidget(t)
        texte.addWidget(u)
        h.addLayout(texte, 1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.geklickt.emit()
        super().mouseReleaseEvent(e)


class _Wahl(Knopf):
    """Auswahl-Knopf als Karte: immer mit Fläche und Rand, die aktive Wahl mit lila Rand."""

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        return QSize(s.width() + 12, max(s.height(), 44))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        p.setPen(QPen(QColor(FARBE["lila" if self.aktiv else "rand"]), 1.6 if self.aktiv else 1.0))
        p.setBrush(QColor(FARBE["flaeche_hoch" if self.aktiv else "flaeche"]))
        p.drawRoundedRect(r, 11, 11)
        p.end()
        super().paintEvent(e)


class ControlAI(OfficeAI):
    """Die Kugel des Controllcenters: wie die OfficeAI, mit eigenen Aufträgen und Vorschlägen (Ja/Nein)."""

    def __init__(self, fenster: QWidget, kontext_fn, ja_fn):
        super().__init__(fenster, kontext_fn)
        self.ja_fn = ja_fn

    def stand(self, name: str, modell: bool, gesichter: dict | None = None) -> None:
        super().stand(name, modell, gesichter)
        self.blase.titel.setText(f"{self.name} · Controllcenter")
        self.kugel.setToolTip(f"{self.name} – fragen, prüfen, einstellen: drüberfahren oder klicken")

    def _chips_leeren(self) -> None:
        lay = self.blase.chips_lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def app_setzen(self, app: str) -> None:
        self.app = app
        self._chips_leeren()
        for zeichen, text, auftrag in AUFTRAEGE:
            k = Knopf(text, zeichen, klein=True)
            k.setToolTip(auftrag)
            k.clicked.connect(lambda _=False, a=auftrag, t=text: self.fragen(a, t))
            self.blase.chips_lay.addWidget(k)
        self.blase.chips_lay.addStretch(1)
        self.blase.chips.setVisible(True)
        self.feld.eingabe.setPlaceholderText("z. B. „Warum malt NemiCLI so langsam?“ oder „Mach es heller“")

    def _begruessung(self) -> str:
        if not self.modell:
            return "Kein Modell verbunden – im Terminal mit /model eins wählen."
        return ("Ich helfe, berate und stelle ein. Frag mich alles rund um deine Einstellungen – Änderungen schlage "
                "ich vor, du sagst Ja. ESC hält mich an.")

    def ausklappen(self, fokus: bool = False) -> None:
        if not self.offen and not self.blase.isVisible() and not self.laufend:
            self.blase.zeigen(status="", text=self._begruessung())
            self.blase.show()
        super().ausklappen(fokus)

    def vorschlag(self, befehl: str, zusatz: str, grund: str) -> None:
        """Vorschlag der KI: in der Blase mit „Ja, einstellen“ und „Nein“ – erst das Ja führt aus."""
        zeile = f"{befehl} {zusatz}".strip()
        self.blase.zeigen(status="", text=f"💡 **Vorschlag:** `{zeile}`" + (f"\n\n{grund}" if grund else ""))
        self._chips_leeren()
        ja = Knopf("Ja, einstellen", "✔", klein=True)
        ja.akzent = True
        nein = Knopf("Nein", "✕", klein=True)

        def fertig(an: bool) -> None:
            if an:
                self.ja_fn(befehl, zusatz)
                self.blase.zeigen(status=f"✓ eingestellt: {zeile}", text="")
            else:
                self.blase.zeigen(status="", text="Okay, nichts geändert.")
            self.app_setzen(self.app)
            self.anordnen()

        ja.clicked.connect(lambda: fertig(True))
        nein.clicked.connect(lambda: fertig(False))
        self.blase.chips_lay.addWidget(ja)
        self.blase.chips_lay.addWidget(nein)
        self.blase.chips_lay.addStretch(1)
        self.blase.chips.setVisible(True)
        self.blase.show()
        self.anordnen()


# ---------------------------------------------------------------------------
# Seiten der Mitte
# ---------------------------------------------------------------------------

class _Seite(QScrollArea):
    """Scrollbare Seite mit Rand; Inhalt in self.lay."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget "
                           "{ background: transparent; }")
        innen = QWidget()
        self.lay = QVBoxLayout(innen)
        self.lay.setContentsMargins(36, 26, 36, 110)
        self.lay.setSpacing(10)
        self.setWidget(innen)

    def leeren(self, ab: int = 0) -> None:
        while self.lay.count() > ab:
            teil = self.lay.takeAt(ab)
            w = teil.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif teil.layout() is not None:
                _layout_leeren(teil.layout())


def _layout_leeren(lay) -> None:
    while lay.count():
        teil = lay.takeAt(0)
        if teil.widget() is not None:
            teil.widget().deleteLater()
        elif teil.layout() is not None:
            _layout_leeren(teil.layout())


class StartSeite(QWidget):
    """Am Anfang: die KI in der Mitte mit großem Eingabefeld."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        v = QVBoxLayout(self)
        v.addStretch(2)
        self.gruss = label("Hallo! Frag mich alles rund um deine Einstellungen.", 17, "text", fett=True)
        self.gruss.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.gruss)
        hinweis = label("Ich helfe, berate und stelle ein – oder wähle links im Baum, was du ansehen möchtest.",
                        11, "dim")
        hinweis.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hinweis)
        v.addSpacing(18)
        zeile = QHBoxLayout()
        zeile.addStretch(1)
        self.pille = QFrame()
        self.pille.setObjectName("startpille")
        self.pille.setStyleSheet(f"QFrame#startpille {{ background: {FARBE['flaeche']}; border: 1px solid "
                                 f"{FARBE['lila']}; border-radius: 28px; }} QLineEdit {{ background: transparent; "
                                 f"border: none; color: {FARBE['text']}; }}")
        self.pille.setFixedSize(660, 58)
        h = QHBoxLayout(self.pille)
        h.setContentsMargins(24, 6, 8, 6)
        self.eingabe = QLineEdit()
        self.eingabe.setFont(schrift(12))
        self.eingabe.setPlaceholderText("z. B. „Prüf mal, ob alles okay ist“ oder „Wie mache ich das Terminal heller?“")
        self.eingabe.returnPressed.connect(self._senden)
        h.addWidget(self.eingabe, 1)
        k = Knopf("", "➤")
        k.akzent = True
        k.clicked.connect(self._senden)
        h.addWidget(k)
        zeile.addWidget(self.pille)
        zeile.addStretch(1)
        v.addLayout(zeile)
        chips = QHBoxLayout()
        chips.addStretch(1)
        for zeichen, text, auftrag in AUFTRAEGE:
            c = Knopf(text, zeichen, klein=True)
            c.setToolTip(auftrag)
            c.clicked.connect(lambda _=False, a=auftrag, t=text: self.fenster.erste_frage(a, t))
            chips.addWidget(c)
        chips.addStretch(1)
        v.addLayout(chips)
        v.addSpacing(34)
        titel = label("Schnellzugriff", 10, "blass", fett=True)
        titel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(titel)
        v.addSpacing(6)
        breite = QHBoxLayout()
        breite.addStretch(1)
        kasten = QWidget()
        kasten.setFixedWidth(820)
        raster = QGridLayout(kasten)
        raster.setContentsMargins(0, 0, 0, 0)
        raster.setHorizontalSpacing(12)
        raster.setVerticalSpacing(12)
        self.kacheln: dict[str, _Kachel] = {}
        for i, (kennung, zeichen, name, text) in enumerate(KACHELN):
            k = _Kachel(zeichen, name, text)
            k.geklickt.connect(lambda kennung=kennung: self.fenster.waehlen_id(kennung))
            raster.addWidget(k, i // 3, i % 3)
            self.kacheln[kennung] = k
        breite.addWidget(kasten)
        breite.addStretch(1)
        v.addLayout(breite)
        v.addStretch(3)

    def _senden(self) -> None:
        text = self.eingabe.text().strip()
        if text:
            self.eingabe.clear()
            self.fenster.erste_frage(text)


class BefehlSeite(_Seite):
    """Ein Befehl: Erklärung, Auswahl als Knöpfe (mit Stand), Favorit, Ausführen."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.e: dict = {}

    def zeigen(self, e: dict) -> None:
        self.e = e
        self.leeren()
        karte = QFrame()
        karte.setObjectName("befehlkopf")
        karte.setStyleSheet(f"QFrame#befehlkopf {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 "
                            f"{FARBE['flaeche_hoch']}, stop:1 {FARBE['flaeche']}); border: 1px solid {FARBE['rand']}; "
                            f"border-left: 4px solid {FARBE['lila']}; border-radius: 16px; }}")
        kv = QVBoxLayout(karte)
        kv.setContentsMargins(20, 14, 20, 14)
        kv.setSpacing(4)
        titel = label(e["name"], 20, "text", fett=True)
        kv.addWidget(titel)
        kv.addWidget(label(e.get("kurz", ""), 12, "dim"))
        marken = [m for m in (e.get("thema", ""), "🔒 gesperrter Bereich – nur du schaltest das" if e.get("gesperrt")
                              else "", "⭐ Favorit im Terminal" if e.get("favorit") else "") if m]
        if marken:
            reihe = QHBoxLayout()
            reihe.setSpacing(6)
            for m in marken:
                pille = label(m, 9.5, "text", umbruch=False)
                pille.setStyleSheet(f"background: {FARBE['flaeche_hell']}; border-radius: 10px; padding: 3px 10px;")
                reihe.addWidget(pille)
            reihe.addStretch(1)
            kv.addLayout(reihe)
        self.lay.addWidget(karte)
        knoepfe = QHBoxLayout()
        fav = Knopf("Favorit entfernen" if e.get("favorit") else "Als Favorit ins Terminal", "★" if e.get("favorit")
                    else "⭐", klein=True)
        fav.clicked.connect(lambda: self.fenster.senden({"op": "favorit", "befehl": e["name"],
                                                         "an": not e.get("favorit")}))
        knoepfe.addWidget(fav)
        frag = Knopf("Frag die KI dazu", "💬", klein=True)
        frag.clicked.connect(lambda: self.fenster.erste_frage(
            f"Erkläre mir {e['name']} einfach: wofür ist das, und was bewirken die Knöpfe?", f"💬 {e['name']}"))
        knoepfe.addWidget(frag)
        knoepfe.addStretch(1)
        self.lay.addLayout(knoepfe)
        text = QLabel(e.get("lang", ""))
        text.setWordWrap(True)
        text.setFont(schrift(10.5))
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setStyleSheet(f"color: {FARBE['text']}; padding: 12px; background: {FARBE['flaeche']}; "
                           f"border: 1px solid {FARBE['rand']}; border-radius: 12px;")
        self.lay.addWidget(text)
        auswahl = e.get("auswahl") or []
        if auswahl:
            self.lay.addWidget(label("Auswahl", 12, "text", fett=True))
            raster = QGridLayout()
            raster.setHorizontalSpacing(8)
            raster.setVerticalSpacing(6)
            for i, a in enumerate(auswahl):
                k = _Wahl(a.get("text", "").strip(), "", klein=False)
                k.setToolTip(f"{e['name']} {a.get('zusatz', '')}".strip())
                k.aktiv = "✔️" in a.get("text", "")
                k.clicked.connect(lambda _=False, a=a: self._waehlen(a))
                raster.addWidget(k, i // 2, i % 2)
            self.lay.addLayout(raster)
        else:
            zeile = QHBoxLayout()
            self.zusatz = QLineEdit()
            self.zusatz.setFont(schrift(10.5))
            self.zusatz.setPlaceholderText(f"Zusatz (optional) – {e.get('aufruf', '')}")
            self.zusatz.setStyleSheet(f"QLineEdit {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; "
                                      f"border: 1px solid {FARBE['rand']}; border-radius: 10px; padding: 6px 10px; }}")
            self.zusatz.returnPressed.connect(self._ausfuehren)
            zeile.addWidget(self.zusatz, 1)
            k = Knopf("Ausführen", "▶")
            k.akzent = True
            k.clicked.connect(self._ausfuehren)
            zeile.addWidget(k)
            self.lay.addLayout(zeile)
        self.ergebnis = label("", 10, "dim")
        self.lay.addWidget(self.ergebnis)
        self.lay.addStretch(1)

    def _waehlen(self, a: dict) -> None:
        zusatz = a.get("zusatz", "")
        if "{}" in zusatz:
            wert, ok = QInputDialog.getText(self, self.e.get("name", ""), a.get("frage") or "Angabe?")
            if not ok or not wert.strip():
                return
            zusatz = zusatz.replace("{}", " ".join(wert.split()))
        self.fenster.ausfuehren(self.e["name"], zusatz)

    def _ausfuehren(self) -> None:
        self.fenster.ausfuehren(self.e["name"], self.zusatz.text().strip())

    def ergebnis_setzen(self, text: str) -> None:
        if getattr(self, "ergebnis", None) is not None:
            try:
                self.ergebnis.setText(text)
            except RuntimeError:                     # Seite gerade neu aufgebaut
                pass


class GesundheitSeite(_Seite):
    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.lay.addWidget(label("🩺 Gesundheit", 20, "text", fett=True))
        self.lay.addWidget(label("Feste Prüfungen – die KI erklärt sie und behebt nach deinem Ja.", 11, "dim"))
        knoepfe = QHBoxLayout()
        neu = Knopf("Neu prüfen", "🔄", klein=True)
        neu.clicked.connect(lambda: self.fenster.senden({"op": "gesundheit"}))
        knoepfe.addWidget(neu)
        ki = Knopf("Selbstdiagnose durch die KI", "🤖", klein=True)
        ki.clicked.connect(lambda: self.fenster.erste_frage(AUFTRAEGE[0][2], "🩺 Selbstdiagnose"))
        knoepfe.addWidget(ki)
        self.test = Knopf("Regenbogen-Testbild", "🌈", klein=True)
        self.test.clicked.connect(self._testbild)
        knoepfe.addWidget(self.test)
        knoepfe.addStretch(1)
        self.lay.addLayout(knoepfe)
        self.liste = QVBoxLayout()
        self.liste.setSpacing(6)
        self.lay.addLayout(self.liste)
        self.bild = QLabel()
        self.bild.hide()
        self.lay.addWidget(self.bild)
        self.bild_text = label("", 10, "dim")
        self.lay.addWidget(self.bild_text)
        self.lay.addStretch(1)
        self.zeilen([{"bereich": "…", "stufe": "", "text": "wird geprüft"}])

    def _testbild(self) -> None:
        self.test.setEnabled(False)
        self.fenster.senden({"op": "testbild"})

    def testbild_moeglich(self, an: bool) -> None:
        self.test.setEnabled(an)
        self.test.setToolTip("Malt einmal ein festes Regenbogen-Testbild und misst Zeit und Grafikspeicher."
                             if an else "Das Testbild ist in diesem Controllcenter schon gemacht.")

    def zeilen(self, befunde: list[dict]) -> None:
        _layout_leeren(self.liste)
        for b in befunde:
            rahmen = QFrame()
            rahmen.setObjectName("befund")
            rahmen.setStyleSheet(_stil_rahmen("befund", 12))
            h = QHBoxLayout(rahmen)
            h.setContentsMargins(14, 8, 10, 8)
            h.addWidget(label(ZEICHEN.get(b.get("stufe", ""), "•"), 12))
            h.addWidget(label(b.get("bereich", ""), 10.5, "text", fett=True))
            t = label(b.get("text", ""), 10.5, "dim")
            t.setWordWrap(True)
            h.addWidget(t, 1)
            if b.get("zusatz") is not None and b.get("befehl"):
                tun = Knopf(b.get("knopf") or "Jetzt ausführen", "", klein=True)
                tun.akzent = True
                tun.setToolTip(f"{b['befehl']} {b['zusatz']}".strip())
                tun.clicked.connect(lambda _=False, n=b["befehl"], z=b["zusatz"]: self.fenster.ausfuehren(n, z))
                h.addWidget(tun)
            if b.get("befehl"):
                k = Knopf(b["befehl"], "↗", klein=True)
                k.setToolTip("Im Baum zeigen")
                k.clicked.connect(lambda _=False, n=b["befehl"]: self.fenster.waehlen_id(f"befehl:{n}"))
                h.addWidget(k)
            self.liste.addWidget(rahmen)

    def testbild(self, ev: dict) -> None:
        pfad = str(ev.get("pfad") or "")
        bild = QPixmap(pfad) if pfad and Path(pfad).is_file() else QPixmap()
        if not bild.isNull():
            self.bild.setPixmap(bild.scaled(320, 320, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
            self.bild.show()
        self.bild_text.setText(f"🌈 Testbild in {ev.get('sekunden', '?')} s" +
                               (f" · Grafikspeicher danach: {ev['vram']}" if ev.get("vram") else ""))


class SystemSeite(_Seite):
    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.lay.addWidget(label("💻 System", 20, "text", fett=True))
        k = Knopf("Aktualisieren", "🔄", klein=True)
        k.clicked.connect(lambda: self.fenster.senden({"op": "system"}))
        zeile = QHBoxLayout()
        zeile.addWidget(k)
        zeile.addStretch(1)
        self.lay.addLayout(zeile)
        self.raster = QGridLayout()
        self.raster.setHorizontalSpacing(18)
        self.raster.setVerticalSpacing(8)
        self.lay.addLayout(self.raster)
        self.lay.addStretch(1)

    def werte(self, liste: list) -> None:
        _layout_leeren(self.raster)
        for i, paar in enumerate(liste):
            if not isinstance(paar, (list, tuple)) or len(paar) != 2:
                continue
            self.raster.addWidget(label(str(paar[0]), 10.5, "dim"), i, 0, Qt.AlignmentFlag.AlignTop)
            wert = label(str(paar[1]), 10.5, "text")
            wert.setWordWrap(True)
            wert.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.raster.addWidget(wert, i, 1)


class ModelleSeite(_Seite):
    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.lay.addWidget(label("📦 Heruntergeladenes", 20, "text", fett=True))
        self.lay.addWidget(label("Was unter KI-Modelle/ liegt – Doppelklick öffnet den Ordner.", 11, "dim"))
        self.baum = QTreeWidget()
        self.baum.setHeaderLabels(["Modell", "Größe", "Bereich"])
        self.baum.setRootIsDecorated(False)
        self.baum.setMinimumHeight(420)
        self.baum.setStyleSheet(f"QTreeWidget {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: "
                                f"1px solid {FARBE['rand']}; border-radius: 10px; }} QHeaderView::section {{ "
                                f"background: {FARBE['flaeche_hell']}; color: {FARBE['dim']}; border: none; "
                                f"padding: 4px; }}")
        self.baum.itemDoubleClicked.connect(self._oeffnen)
        self.lay.addWidget(self.baum)
        self.summe = label("", 10, "dim")
        self.lay.addWidget(self.summe)

    def liste(self, eintraege: list[dict]) -> None:
        self.baum.clear()
        gesamt = 0.0
        for m in eintraege:
            it = QTreeWidgetItem([m.get("name", ""), f"{m.get('gb', 0):.2f} GB", m.get("bereich", "")])
            it.setData(0, ROLLE_ID, m.get("ordner", ""))
            self.baum.addTopLevelItem(it)
            gesamt += float(m.get("gb") or 0)
        for i in range(3):
            self.baum.resizeColumnToContents(i)
        self.summe.setText(f"{len(eintraege)} Modellordner · zusammen {gesamt:.1f} GB")

    def _oeffnen(self, it: QTreeWidgetItem, _spalte: int) -> None:
        pfad = it.data(0, ROLLE_ID)
        if pfad:
            self.fenster.senden({"op": "ordner", "pfad": pfad})


class TerminalSeite(_Seite):
    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.lay.addWidget(label("⌨ Terminal-Befehle", 20, "text", fett=True))
        self.lay.addWidget(label("Welche /befehle kannst du im Terminal tippen? /control ist immer da.", 11, "dim"))
        zeile = QHBoxLayout()
        self.nur = Knopf("Nur /control und Favoriten", "⭐")
        self.nur.clicked.connect(lambda: self.fenster.senden({"op": "modus", "modus": "favoriten"}))
        self.alle = Knopf("Alle Befehle", "📋")
        self.alle.clicked.connect(lambda: self.fenster.senden({"op": "modus", "modus": "alle"}))
        zeile.addWidget(self.nur)
        zeile.addWidget(self.alle)
        zeile.addStretch(1)
        self.lay.addLayout(zeile)
        self.lay.addWidget(label("⭐ Deine Favoriten", 12, "text", fett=True))
        self.favs = QVBoxLayout()
        self.lay.addLayout(self.favs)
        self.lay.addWidget(label("Favoriten fügst du bei jedem Befehl im Baum mit ⭐ hinzu.", 10, "blass"))
        self.lay.addStretch(1)

    def setzen(self, modus: str, favoriten: list[str]) -> None:
        self.nur.aktiv, self.alle.aktiv = modus != "alle", modus == "alle"
        self.nur.update()
        self.alle.update()
        _layout_leeren(self.favs)
        if not favoriten:
            self.favs.addWidget(label("– noch keine –", 10.5, "dim"))
        for n in favoriten:
            h = QHBoxLayout()
            h.addWidget(label(n, 11, "text"))
            weg = Knopf("", "✕", klein=True)
            weg.setToolTip("Aus den Favoriten nehmen")
            weg.clicked.connect(lambda _=False, n=n: self.fenster.senden({"op": "favorit", "befehl": n, "an": False}))
            h.addWidget(weg)
            h.addStretch(1)
            self.favs.addLayout(h)


class TresorSeite(_Seite):
    """🔐 Tresor: alle Schlüssel nach Anbieter. Hier sieht man nur, ob einer gesetzt ist – nie den Wert. Eingaben
    gehen direkt an die Sitzung (in den Tresor, neues Siegel); dieses Fenster behält keinen Wert."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.wunsch = ""
        self.leeren()
        self.lay.addWidget(label("🔐 Tresor", 20, "text", fett=True))
        self.lay.addWidget(label("wird geöffnet …", 11, "dim"))

    def setzen(self, daten: dict) -> None:
        self.leeren()
        self.lay.addWidget(label("🔐 Tresor", 20, "text", fett=True))
        info = label("Alle Schlüssel verschlüsselt (AES-256 und das Windows-Schloss). Jede Änderung bekommt ein neues "
                     "Siegel; wird der Tresor von außen verändert, vernichtet der Wächter sofort alle Schlüssel. "
                     "Die KI sieht hier nichts.", 10.5, "dim")
        info.setWordWrap(True)
        self.lay.addWidget(info)
        st = daten.get("stand") or {}
        if st.get("vernichtet"):
            warn = label(f"🚨 Vernichtet: {st['vernichtet']} – bitte die Schlüssel neu eintragen.", 11, "fehler",
                         fett=True)
            warn.setWordWrap(True)
            self.lay.addWidget(warn)
        elif not st.get("verfuegbar", True):
            self.lay.addWidget(label("⚠ Ohne Windows-Kryptografie: Schlüssel gelten nur bis zum Beenden.", 10.5, "warn"))
        else:
            self.lay.addWidget(label(f"🔒 Siegel {st.get('siegel') or '–'} · {st.get('schluessel', 0)} Schlüssel"
                                     + (f" · geändert {st['geaendert']}" if st.get("geaendert") else ""), 10, "blass"))
        cg = daten.get("chatgpt") or {}
        self.lay.addWidget(label("🤖 ChatGPT-Konto", 12, "text", fett=True))
        zeile = QHBoxLayout()
        zeile.addWidget(label(("✅ angemeldet" + (f" ({cg['email']})" if cg.get("email") else "")
                               + ("" if cg.get("plan") else " · Plan-Nutzung nicht freigegeben"))
                              if cg.get("angemeldet") else "– nicht angemeldet", 10.5, "text"))
        zeile.addStretch(1)
        k = Knopf("Abmelden" if cg.get("angemeldet") else "Im Browser anmelden", "🚪" if cg.get("angemeldet") else "🌐",
                  klein=True)
        k.clicked.connect(lambda: self.fenster.senden({"op": "chatgpt_konto",
                                                       "was": "abmelden" if cg.get("angemeldet") else "anmelden"}))
        zeile.addWidget(k)
        self.lay.addLayout(zeile)
        for g in daten.get("gruppen") or []:
            self.lay.addWidget(label(g.get("titel", ""), 12, "text", fett=True))
            for e in g.get("eintraege") or []:
                self.lay.addWidget(self._zeile(e))
        unten = QHBoxLayout()
        eigen = Knopf("Eigenen Schlüssel eintragen", "➕", klein=True)
        eigen.clicked.connect(self._eigener)
        unten.addWidget(eigen)
        unten.addStretch(1)
        weg = Knopf("Alle Schlüssel vernichten", "🧨", klein=True)
        weg.clicked.connect(self._vernichten)
        unten.addWidget(weg)
        self.lay.addLayout(unten)
        self.lay.addStretch(1)
        if self.wunsch:
            name, self.wunsch = self.wunsch, ""
            treffer = next((e for g in daten.get("gruppen") or [] for e in g.get("eintraege") or []
                            if e.get("name") == name), None)
            if treffer is not None:
                QTimer.singleShot(250, lambda: self._eintragen(treffer))

    def _zeile(self, e: dict) -> QFrame:
        rahmen = QFrame()
        rahmen.setObjectName("tresorzeile")
        rahmen.setStyleSheet(_stil_rahmen("tresorzeile", 12))
        h = QHBoxLayout(rahmen)
        h.setContentsMargins(14, 6, 10, 6)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(label(e.get("titel", ""), 10.5, "text", fett=True))
        text.addWidget(label(e.get("name", ""), 9, "blass"))
        h.addLayout(text, 1)
        h.addWidget(label("🔑 gesetzt" if e.get("gesetzt") else "– leer", 10, "ok" if e.get("gesetzt") else "dim"))
        if str(e.get("url") or "").startswith("https://"):
            link = Knopf("", "🔗", klein=True)
            link.setToolTip(f"Schlüssel holen: {e['url']}")
            link.clicked.connect(lambda _=False, u=e["url"]: QDesktopServices.openUrl(QUrl(u)))
            h.addWidget(link)
        rein = Knopf("Ändern" if e.get("gesetzt") else "Eintragen", "✏️", klein=True)
        rein.clicked.connect(lambda _=False, e=e: self._eintragen(e))
        h.addWidget(rein)
        if e.get("gesetzt"):
            raus = Knopf("", "🗑", klein=True)
            raus.setToolTip("Entfernen")
            raus.clicked.connect(lambda _=False, e=e: self._entfernen(e))
            h.addWidget(raus)
        return rahmen

    def _eintragen(self, e: dict) -> None:
        wert, ok = QInputDialog.getText(self, f"🔐 {e.get('titel', '')}", "Schlüssel einfügen (wird verschlüsselt):",
                                        QLineEdit.EchoMode.Password)
        if ok and wert.strip():
            self.fenster.senden({"op": "tresor_setzen", "name": e["name"], "wert": wert.strip()})

    def _entfernen(self, e: dict) -> None:
        if QMessageBox.question(self, "🔐 Tresor", f"{e.get('titel', '')} aus dem Tresor entfernen?") \
                == QMessageBox.StandardButton.Yes:
            self.fenster.senden({"op": "tresor_setzen", "name": e["name"], "wert": ""})

    def _eigener(self) -> None:
        name, ok = QInputDialog.getText(self, "🔐 Eigener Schlüssel", "Name (z. B. MEINDIENST_API_KEY):")
        name = name.strip().upper()
        if ok and name:
            self._eintragen({"name": name, "titel": name})

    def _vernichten(self) -> None:
        if QMessageBox.question(self, "🧨 Tresor", "Wirklich ALLE Schlüssel und das ChatGPT-Konto vernichten? Das "
                                                  "lässt sich nicht rückgängig machen.") == QMessageBox.StandardButton.Yes:
            self.fenster.senden({"op": "tresor_vernichten"})


def _zahlfeld(lay, titel: str, wert, lo, hi, schritt, widget: QWidget | None = None):
    """Beschriftetes Zahlenfeld (Komma-Schritt -> QDoubleSpinBox) oder ein anderes Feld unter einer Überschrift."""
    box = QVBoxLayout()
    box.setSpacing(2)
    box.addWidget(label(titel, 9.5, "blass"))
    if widget is None:
        widget = QDoubleSpinBox() if isinstance(schritt, float) else QSpinBox()
        widget.setRange(lo, hi)
        widget.setSingleStep(schritt)
        widget.setValue(wert)
    widget.setFont(schrift(10.5))
    widget.setMinimumWidth(110)
    widget.setStyleSheet(_feld_stil())
    box.addWidget(widget)
    lay.addLayout(box)
    return widget


class KreaSeite(_Seite):
    """🎛 Krea 2: CFG, fester Gegen-Prompt, Sampler, Verschiebung und Schritte – zum Ausprobieren."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.daten: dict = {}
        self.lay.addWidget(label("🎛 Krea 2", 20, "text", fett=True))
        self.lay.addWidget(label("wird geladen …", 11, "dim"))

    def setzen(self, daten: dict) -> None:
        self.daten = daten
        self.leeren()
        self.lay.addWidget(label("🎛 Krea 2", 20, "text", fett=True))
        modelle = daten.get("modelle") or []
        if modelle:
            stand = (f"✅ malt gerade mit {daten.get('gewaehlt')}" if daten.get("aktiv")
                     else f"– gewählt ist {daten.get('gewaehlt')}, gerade malt ein anderer Bild-Motor (/bildmodel)")
            text = f"Gefunden: {' · '.join(modelle)}\n{stand}"
            if daten.get("raw"):
                text += ("\nRaw-Modell: Schritte und CFG kommen aus seinen eigenen Einstellungen, der Gegen-Prompt "
                         "wirkt trotzdem.")
            info = label(text, 10.5, "dim")
        else:
            info = label("Noch kein Krea-2-Modell im Bildordner (KI-Modelle/KI-Bild/BildgenKI/Krea2/).", 10.5, "warn")
        info.setWordWrap(True)
        self.lay.addWidget(info)

        b = daten.get("bereich") or {}
        self.lay.addWidget(label("🚫 Gegen-Prompt (fest, für jedes Krea-Bild)", 12, "text", fett=True))
        self.negativ = QPlainTextEdit(str(daten.get("negativ") or ""))
        self.negativ.setPlaceholderText("englisch, mit Kommas – z. B. worst quality, low quality, text, watermark, "
                                        "mutant hands, malformed limbs")
        self.negativ.setFixedHeight(110)
        self.negativ.setFont(schrift(10.5))
        self.negativ.setStyleSheet(_feld_stil())
        self.negativ.textChanged.connect(self._hinweis)
        self.lay.addWidget(self.negativ)

        self.detail = QCheckBox("✨ Detail-Satz anhängen (Bilder mit Person, nicht bei gezeichneten Stilen)")
        self.detail.setChecked(bool(daten.get("detail", True)))
        self.detail.setFont(schrift(10.5))
        self.detail.setStyleSheet(f"QCheckBox {{ color: {FARBE['text']}; }}")
        self.lay.addWidget(self.detail)
        self.detail_satz = QPlainTextEdit(str(daten.get("detail_satz") or ""))
        self.detail_satz.setPlaceholderText("englisch, als Satz – z. B. Sharp focus on the face and eyes, natural skin "
                                            "texture with visible pores, no smoothing or retouching.")
        self.detail_satz.setFixedHeight(80)
        self.detail_satz.setFont(schrift(10.5))
        self.detail_satz.setStyleSheet(_feld_stil())
        self.detail.toggled.connect(self.detail_satz.setEnabled)
        self.detail_satz.setEnabled(self.detail.isChecked())
        self.lay.addWidget(self.detail_satz)

        werte = QHBoxLayout()
        lo, hi = b.get("cfg", (1.0, 5.0))
        self.cfg = _zahlfeld(werte, "CFG (1 = aus)", float(daten.get("cfg") or 1.0), float(lo), float(hi), 0.1)
        self.cfg.valueChanged.connect(self._hinweis)
        self.sampler = QComboBox()
        for s in daten.get("sampler_liste") or ["euler_ancestral", "euler"]:
            self.sampler.addItem(s)
        self.sampler.setCurrentText(str(daten.get("sampler") or "euler_ancestral"))
        _zahlfeld(werte, "Sampler", None, 0, 0, 0, widget=self.sampler)
        self.zeitplan = QComboBox()
        for z in daten.get("zeitplan_liste") or ["simple", "beta"]:
            self.zeitplan.addItem(z)
        self.zeitplan.setCurrentText(str(daten.get("zeitplan") or "simple"))
        _zahlfeld(werte, "Zeitplan", None, 0, 0, 0, widget=self.zeitplan)
        lo, hi = b.get("shift", (0.5, 3.0))
        self.shift = _zahlfeld(werte, "Verschiebung", float(daten.get("shift") or 1.15), float(lo), float(hi), 0.05)
        lo, hi = b.get("schritte", (8, 16))
        self.schritte = _zahlfeld(werte, "Schritte", int(daten.get("schritte") or 14), int(lo), int(hi), 1)
        werte.addStretch(1)
        self.lay.addLayout(werte)

        knoepfe = QHBoxLayout()
        atelier = Knopf("Atelier öffnen", "🎨", klein=True)
        atelier.setToolTip("Das Krea-Atelier: alle Einstellungen, großes Bild, Galerie und A/B-Vergleich (/atelier)")
        atelier.clicked.connect(lambda: self.fenster.senden({"op": "atelier"}))
        knoepfe.addWidget(atelier)
        knoepfe.addStretch(1)
        standard = Knopf("Standard", "↺", klein=True)
        standard.setToolTip("Alles auf die Vorgabe zurück (CFG 1, kein Gegen-Prompt) – noch nicht gespeichert")
        standard.clicked.connect(self._standard)
        knoepfe.addWidget(standard)
        speichern = Knopf("Speichern", "💾", klein=True)
        speichern.clicked.connect(self._speichern)
        knoepfe.addWidget(speichern)
        self.lay.addLayout(knoepfe)

        self.warnung = label("", 10.5, "warn")
        self.warnung.setWordWrap(True)
        self.lay.addWidget(self.warnung)
        erklaerung = label(
            "CFG 1 malt wie bisher mit einem Durchlauf je Schritt – der Gegen-Prompt ruht. Ab 1,1 rechnet Krea gegen "
            "den Gegen-Prompt (dazu immer der feste Teil der Bildsperre): jeder Schritt dauert etwa doppelt so lange. "
            "Für Turbo-Modelle 1,3–1,8 probieren; höher wird es kontrastreich und hart. Das Gesicht kann sich dabei "
            "auch bei gleichem Seed ändern.\n"
            "Sampler: euler_ancestral gibt je Schritt etwas frisches Rauschen dazu (lebendiger), euler rechnet ruhig "
            "und gleichmäßig.\n"
            "Zeitplan: simple verteilt die Schritte gleichmäßig, beta legt mehr an den Anfang (Bildaufbau) und ans "
            "Ende (feine Einzelheiten).\n"
            "Verschiebung: höher legt mehr Gewicht auf den groben Bildaufbau, niedriger auf feine Einzelheiten.\n"
            "Detail-Satz: kommt ans Ende des Prompts. Was davon schon im Prompt steht, fällt weg; passt er nicht mehr "
            "in die 512 Token von Krea, bleibt er weg und die Szene geht vor.", 10,
            "dim")
        erklaerung.setWordWrap(True)
        self.lay.addWidget(erklaerung)
        self._hinweis()
        self.lay.addStretch(1)

    def _hinweis(self) -> None:
        cfg, neg = self.cfg.value(), self.negativ.toPlainText().strip()
        if neg and cfg <= 1.0:
            text = "⚠ Der Gegen-Prompt wirkt erst ab CFG über 1."
        elif cfg > 1.0 and not neg:
            text = "Ohne eigenen Gegen-Prompt rechnet Krea nur gegen den festen Teil der Bildsperre."
        else:
            text = ""
        self.warnung.setText(text)
        self.warnung.setVisible(bool(text))

    def _standard(self) -> None:
        s = self.daten.get("standard") or {}
        self.negativ.setPlainText(str(s.get("negativ") or ""))
        self.cfg.setValue(float(s.get("cfg") or 1.0))
        self.sampler.setCurrentText(str(s.get("sampler") or "euler_ancestral"))
        self.zeitplan.setCurrentText(str(s.get("zeitplan") or "simple"))
        self.shift.setValue(float(s.get("shift") or 1.15))
        self.schritte.setValue(int(s.get("schritte") or 14))
        self.detail.setChecked(bool(s.get("detail", True)))
        self.detail_satz.setPlainText(str(s.get("detail_satz") or ""))

    def _speichern(self) -> None:
        self.fenster.senden({"op": "krea_setzen", "negativ": self.negativ.toPlainText(), "cfg": self.cfg.value(),
                             "sampler": self.sampler.currentText(), "zeitplan": self.zeitplan.currentText(),
                             "shift": self.shift.value(),
                             "schritte": self.schritte.value(), "detail": self.detail.isChecked(),
                             "detail_satz": self.detail_satz.toPlainText()})


class SdSeite(_Seite):
    """🟫 Stable Diffusion: festgenagelter Gegen-Prompt, CFG und Schritte, Stil-Vorlagen (aus einer styles.csv von
    Forge/A1111) zum Anschalten und was der Lerner aus den Bildurteilen je Modell behalten hat."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.filter = ""
        self.daten: dict = {}
        self.lay.addWidget(label("🟫 Stable Diffusion", 20, "text", fett=True))
        self.lay.addWidget(label("wird geladen …", 11, "dim"))

    def setzen(self, daten: dict) -> None:
        self.daten = daten
        self.leeren()
        self.lay.addWidget(label("🟫 Stable Diffusion", 20, "text", fett=True))
        modelle = daten.get("modelle") or []
        if modelle:
            namen = " · ".join(f"{m['name']} ({'SDXL' if m.get('art') == 'sdxl' else 'SD 1.5'})" for m in modelle)
            stand = (f"✅ malt gerade mit {daten['gewaehlt']}" if daten.get("aktiv")
                     else "– gerade ist ein anderer Bild-Motor gewählt (/bildmodel)")
            info = label(f"Gefunden: {namen}\n{stand}", 10.5, "dim")
        else:
            info = label("Noch kein Stable-Diffusion-Checkpoint im Bildordner (KI-Modelle/KI-Bild/BildgenKI/<Name>/).",
                         10.5, "warn")
        info.setWordWrap(True)
        self.lay.addWidget(info)

        self.lay.addWidget(label("🚫 Gegen-Prompt (gilt für jedes Bild)", 12, "text", fett=True))
        self.negativ = QPlainTextEdit(str(daten.get("negativ") or ""))
        self.negativ.setPlaceholderText("z. B. blurry, low quality, extra fingers, watermark, text")
        self.negativ.setFixedHeight(110)
        self.negativ.setFont(schrift(10.5))
        self.negativ.setStyleSheet(_feld_stil())
        self.lay.addWidget(self.negativ)

        werte = QHBoxLayout()
        self.cfg_xl = self._zahl(werte, "CFG SDXL", float(daten.get("cfg_sdxl") or 5.0), 1.0, 20.0, 0.5)
        self.cfg_15 = self._zahl(werte, "CFG SD 1.5", float(daten.get("cfg_sd15") or 7.0), 1.0, 20.0, 0.5)
        self.schritte = self._zahl(werte, f"Schritte (0 = {daten.get('schritte_vorgabe', 28)})",
                                   int(daten.get("schritte") or 0), 0, 80, 1)
        werte.addStretch(1)
        speichern = Knopf("Speichern", "💾", klein=True)
        speichern.clicked.connect(self._speichern)
        werte.addWidget(speichern)
        self.lay.addLayout(werte)

        vorlagen = daten.get("vorlagen") or []
        an = [v for v in vorlagen if v.get("an")]
        kopf = QHBoxLayout()
        kopf.addWidget(label(f"🎨 Stil-Vorlagen ({len(an)} von {len(vorlagen)} an)", 12, "text", fett=True, umbruch=False))
        kopf.addStretch(1)
        holen = Knopf("Aus Forge/A1111 übernehmen", "📥", klein=True)
        holen.setToolTip("Eine styles.csv wählen (Spalten name, prompt, negative_prompt)")
        holen.clicked.connect(self._einlesen)
        kopf.addWidget(holen)
        self.lay.addLayout(kopf)
        if vorlagen:
            hinweis = label("Eingeschaltete Vorlagen kommen bei jedem Bild dazu: ihr Prompt-Teil hinter deinen Prompt "
                            "(oder an die Stelle von {prompt}), ihr Gegen-Prompt hinter den festgenagelten.", 10, "dim")
            hinweis.setWordWrap(True)
            self.lay.addWidget(hinweis)
            self.suche = QLineEdit(self.filter)
            self.suche.setPlaceholderText("🔍 Vorlage suchen …")
            self.suche.setFont(schrift(10.5))
            self.suche.setStyleSheet(_feld_stil())
            self.suche.textChanged.connect(self._suchen)
            self.lay.addWidget(self.suche)
            self.liste = QVBoxLayout()
            self.liste.setSpacing(4)
            self.lay.addLayout(self.liste)
            self._liste_fuellen()
        else:
            self.lay.addWidget(label("Noch keine Vorlagen – mit 📥 eine styles.csv übernehmen.", 10.5, "dim"))

        self.lay.addWidget(label("🧠 Gelernt aus deinen 👍/👎", 12, "text", fett=True))
        gelernt = daten.get("gelernt") or {}
        if not gelernt:
            leer = label("Noch nichts – bewerte Bilder (👍/👎 oder der KI sagen, was gefällt). Ab drei Urteilen je "
                         "Baustein merkt sich NemiCLI, was bei welchem Modell gute Bilder bringt.", 10, "dim")
            leer.setWordWrap(True)
            self.lay.addWidget(leer)
        for name, g in gelernt.items():
            zeile = label(f"{name}\n✅ bewährt: {', '.join(g.get('gut') or []) or '–'}\n"
                          f"❌ eher schlecht: {', '.join(g.get('schlecht') or []) or '–'}", 10, "text")
            zeile.setWordWrap(True)
            self.lay.addWidget(zeile)
        self.lay.addStretch(1)

    def _zahl(self, lay, titel: str, wert, lo, hi, schritt):
        return _zahlfeld(lay, titel, wert, lo, hi, schritt)

    def _liste_fuellen(self) -> None:
        _layout_leeren(self.liste)
        such = self.filter.lower()
        treffer = [v for v in self.daten.get("vorlagen") or []
                   if not such or such in v["name"].lower() or such in str(v.get("prompt") or "").lower()]
        treffer.sort(key=lambda v: not v.get("an"))
        for v in treffer[:SD_VORLAGEN_ZEIGEN]:
            box = QCheckBox(v["name"])
            box.setChecked(bool(v.get("an")))
            box.setFont(schrift(10.5))
            tipp = "\n".join(x for x in (f"+ {v['prompt']}" if v.get("prompt") else "",
                                         f"− {v['negativ']}" if v.get("negativ") else "") if x)
            box.setToolTip(tipp[:800])
            box.setStyleSheet(f"QCheckBox {{ color: {FARBE['text']}; }}")
            box.toggled.connect(lambda an, n=v["name"]: self.fenster.senden({"op": "sd_vorlage", "name": n, "an": an}))
            self.liste.addWidget(box)
        if len(treffer) > SD_VORLAGEN_ZEIGEN:
            self.liste.addWidget(label(f"… und {len(treffer) - SD_VORLAGEN_ZEIGEN} weitere – Suche eingrenzen.", 9.5,
                                       "blass"))

    def _suchen(self, text: str) -> None:
        self.filter = text
        self._liste_fuellen()

    def _speichern(self) -> None:
        self.fenster.senden({"op": "sd_setzen", "negativ": self.negativ.toPlainText(), "cfg_sdxl": self.cfg_xl.value(),
                             "cfg_sd15": self.cfg_15.value(), "schritte": self.schritte.value()})

    def _einlesen(self) -> None:
        pfad, _ = QFileDialog.getOpenFileName(self, "styles.csv übernehmen", "", "Stil-Vorlagen (*.csv)")
        if pfad:
            self.fenster.senden({"op": "sd_vorlagen_einlesen", "pfad": pfad})


# ---------------------------------------------------------------------------
# Das Fenster
# ---------------------------------------------------------------------------

def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.0f} KB"


def _balken() -> QProgressBar:
    b = QProgressBar()
    b.setRange(0, 1000)
    b.setTextVisible(False)
    b.setFixedHeight(14)
    b.setStyleSheet(f"QProgressBar {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; "
                    f"border-radius: 7px; }} QProgressBar::chunk {{ border-radius: 6px; background: "
                    f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {FARBE['lila']}, stop:1 {FARBE['cyan']}); }}")
    return b


class HubSeite(_Seite):
    """⬇️ Hugging Face: Verbindung erst nach dem OK, suchen, Dateien wählen, laden mit Balken – alles geprüft."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.zustand = {"offen": False, "mit_schluessel": False, "hat_schluessel": False, "laeuft": False}
        self.repo, self.art, self.ziel = "", "", ""
        self.haken: dict[str, QCheckBox] = {}
        self.groessen: dict[str, int] = {}
        self._takt = (0.0, 0)
        self.lay.addWidget(label("⬇️ Hugging Face", 20, "text", fett=True))
        self.lay.addWidget(label("Modelle suchen und laden. Die Verbindung öffnet sich erst nach deinem OK und "
                                 "schließt sich mit diesem Fenster. Jede Datei wird vor dem Ablegen geprüft.", 11, "dim"))
        karte = QFrame()
        karte.setObjectName("hubstatus")
        karte.setStyleSheet(_stil_rahmen("hubstatus"))
        kh = QHBoxLayout(karte)
        kh.setContentsMargins(16, 10, 12, 10)
        self.status = label("🔒 Verbindung geschlossen", 11, "text", fett=True, umbruch=False)
        kh.addWidget(self.status, 1)
        self.k_frei = Knopf("Öffnen – nur freie Modelle", "🔓", klein=True)
        self.k_frei.clicked.connect(lambda: self._oeffnen(False))
        self.k_schluessel = Knopf("Mit meinem Schlüssel", "🔑", klein=True)
        self.k_schluessel.clicked.connect(lambda: self._oeffnen(True))
        self.k_zu = Knopf("Schließen", "🔒", klein=True)
        self.k_zu.clicked.connect(lambda: self.fenster.senden({"op": "hub_schliessen"}))
        for k in (self.k_frei, self.k_schluessel, self.k_zu):
            kh.addWidget(k)
        self.lay.addWidget(karte)
        self.schluessel_hinweis = label("Für Modelle mit Bedingungen: deinen Schlüssel als HF_TOKEN im 🔐 Tresor "
                                        "eintragen und den Bedingungen auf huggingface.co zustimmen.", 9.5, "blass")
        self.lay.addWidget(self.schluessel_hinweis)

        self.suchbereich = QWidget()
        sh = QHBoxLayout(self.suchbereich)
        sh.setContentsMargins(0, 6, 0, 0)
        self.suche = QLineEdit()
        self.suche.setFont(schrift(10.5))
        self.suche.setPlaceholderText("Modell suchen, z. B. „Qwen3“ oder „Krea“ …")
        self.suche.setStyleSheet(_feld_stil())
        self.suche.returnPressed.connect(self._suchen)
        self.art_box = QComboBox()
        self.art_box.setFont(schrift(10.5))
        self.art_box.addItem("🔎 Alle", "")
        self.art_box.setStyleSheet(_feld_stil())
        k = Knopf("Suchen", "🔍")
        k.akzent = True
        k.clicked.connect(self._suchen)
        sh.addWidget(self.suche, 1)
        sh.addWidget(self.art_box)
        sh.addWidget(k)
        self.lay.addWidget(self.suchbereich)
        self.meldung = label("", 10, "dim")
        self.lay.addWidget(self.meldung)

        self.treffer_w = QWidget()
        self.treffer_lay = QGridLayout(self.treffer_w)
        self.treffer_lay.setContentsMargins(0, 0, 0, 0)
        self.treffer_lay.setSpacing(10)
        self.lay.addWidget(self.treffer_w)

        self.dateien_w = QFrame()
        self.dateien_w.setObjectName("hubdateien")
        self.dateien_w.setStyleSheet(_stil_rahmen("hubdateien"))
        self.dateien_lay = QVBoxLayout(self.dateien_w)
        self.dateien_lay.setContentsMargins(18, 14, 18, 14)
        self.dateien_w.hide()
        self.lay.addWidget(self.dateien_w)

        self.lade_w = QFrame()
        self.lade_w.setObjectName("hublade")
        self.lade_w.setStyleSheet(_stil_rahmen("hublade", hell=True))
        lv = QVBoxLayout(self.lade_w)
        lv.setContentsMargins(18, 14, 18, 14)
        lv.setSpacing(6)
        self.datei_text = label("", 10, "text", umbruch=False)
        self.balken_datei = _balken()
        self.gesamt_text = label("", 10, "dim", umbruch=False)
        self.balken_gesamt = _balken()
        zeile = QHBoxLayout()
        zeile.addWidget(self.gesamt_text, 1)
        self.k_stopp = Knopf("Anhalten (ESC)", "⏹", klein=True)
        self.k_stopp.clicked.connect(lambda: self.fenster.senden({"op": "hub_stopp"}))
        zeile.addWidget(self.k_stopp)
        for w in (self.datei_text, self.balken_datei):
            lv.addWidget(w)
        lv.addLayout(zeile)
        lv.addWidget(self.balken_gesamt)
        self.lade_w.hide()
        self.lay.addWidget(self.lade_w)
        self.fertig_w = QWidget()
        fh = QHBoxLayout(self.fertig_w)
        fh.setContentsMargins(0, 0, 0, 0)
        self.fertig_text = label("", 10.5, "text")
        fh.addWidget(self.fertig_text, 1)
        self.k_modell = Knopf("Als Modell wählen", "✅", klein=True)
        self.k_modell.akzent = True
        self.k_modell.clicked.connect(self._als_modell)
        self.k_ordner = Knopf("Ordner öffnen", "📂", klein=True)
        self.k_ordner.clicked.connect(lambda: self.fenster.senden({"op": "ordner", "pfad": self.ziel}))
        fh.addWidget(self.k_modell)
        fh.addWidget(self.k_ordner)
        self.fertig_w.hide()
        self.lay.addWidget(self.fertig_w)
        self.lay.addStretch(1)
        self.stand({})

    # -- Verbindung ------------------------------------------------------------------------------
    def _oeffnen(self, mit_schluessel: bool) -> None:
        antwort = QMessageBox.question(self, "⬇️ Hugging Face", HUB_FRAGE,
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                       QMessageBox.StandardButton.Yes)
        if antwort == QMessageBox.StandardButton.Yes:
            self.meldung.setText("⏳ verbinde …")
            self.fenster.senden({"op": "hub_oeffnen", "mit_schluessel": mit_schluessel})

    def stand(self, ev: dict) -> None:
        self.zustand.update({k: bool(ev.get(k)) for k in self.zustand if k in ev})
        if ev.get("arten") and self.art_box.count() == 1:
            for schluessel, text in ev["arten"]:
                self.art_box.addItem(text, schluessel)
        offen, mit = self.zustand["offen"], self.zustand["mit_schluessel"]
        self.status.setText("🟢 Verbunden – mit deinem Schlüssel" if mit else "🟢 Verbunden – freie Modelle"
                            if offen else "🔒 Verbindung geschlossen")
        self.k_frei.setVisible(not offen)
        self.k_schluessel.setVisible(not offen)
        self.k_schluessel.setEnabled(self.zustand["hat_schluessel"])
        self.k_schluessel.setToolTip("" if self.zustand["hat_schluessel"] else
                                     "Erst einen Schlüssel als HF_TOKEN im 🔐 Tresor eintragen")
        self.k_zu.setVisible(offen)
        self.schluessel_hinweis.setVisible(not self.zustand["hat_schluessel"])
        self.suchbereich.setEnabled(offen)
        self.k_stopp.setEnabled(self.zustand["laeuft"])
        if not offen:
            self.meldung.setText("Erst die Verbindung öffnen – dann suchen und laden.")
        elif self.meldung.text().startswith(("⏳ verbinde", "Erst die Verbindung")):
            self.meldung.setText("Was suchst du? Tipp einen Namen und wähle die Art.")

    # -- Suchen und Dateien ------------------------------------------------------------------------
    def _suchen(self) -> None:
        if not self.zustand["offen"]:
            return
        self.meldung.setText("🔍 sucht …")
        self.fenster.senden({"op": "hub_suchen", "text": self.suche.text().strip(),
                             "art": self.art_box.currentData() or ""})

    def treffer(self, liste: list[dict]) -> None:
        _layout_leeren(self.treffer_lay)
        self.meldung.setText(f"{len(liste)} Treffer – klick eins an, um seine Dateien zu sehen." if liste else
                             "Nichts gefunden – anderer Name oder eine andere Art?")
        for i, t in enumerate(liste):
            zeile = f"⬇ {t.get('downloads', 0):,} · ♥ {t.get('likes', 0)}".replace(",", ".")
            if t.get("gated"):
                zeile += " · 🔑 mit Bedingungen"
            k = _Kachel("🤗", str(t["id"]), zeile)
            k.geklickt.connect(lambda repo=str(t["id"]): self._dateien_holen(repo))
            self.treffer_lay.addWidget(k, i // 2, i % 2)

    def _dateien_holen(self, repo: str) -> None:
        self.repo, self.art = repo, self.art_box.currentData() or ""
        self.meldung.setText(f"📄 holt die Dateien von {repo} …")
        self.fenster.senden({"op": "hub_dateien", "repo": repo, "art": self.art})

    def dateien(self, ev: dict) -> None:
        _layout_leeren(self.dateien_lay)
        self.repo, self.art, self.ziel = str(ev.get("repo") or ""), str(ev.get("art") or ""), str(ev.get("ziel") or "")
        self.haken, self.groessen = {}, {}
        self.dateien_lay.addWidget(label(f"📄 {self.repo}", 13, "text", fett=True))
        lizenz = str(ev.get("lizenz") or "keine Angabe")
        zeile = f"📜 Lizenz: {lizenz}" + (f" – {ev['hinweis']}" if ev.get("hinweis") else "")
        self.dateien_lay.addWidget(label(zeile, 10, "dim"))
        if ev.get("gated") and not self.zustand["mit_schluessel"]:
            self.dateien_lay.addWidget(label("🔑 Dieses Modell hat Bedingungen – mit deinem Schlüssel öffnen, wenn du "
                                             "ihnen auf huggingface.co zugestimmt hast.", 10, "blass"))
        for e in ev.get("eintraege") or []:
            pfad = str(e.get("pfad") or "")
            text = f"{pfad}  ·  {_gb(int(e.get('groesse') or 0))}"
            if e.get("gesperrt"):
                text += f"  ·  {e['gesperrt']}"
            h = QCheckBox(text)
            h.setFont(schrift(10))
            h.setStyleSheet(f"QCheckBox {{ color: {FARBE['text' if not e.get('gesperrt') else 'blass']}; }}")
            h.setChecked(bool(e.get("vorgewaehlt")) and not e.get("gesperrt"))
            h.setEnabled(not e.get("gesperrt"))
            h.toggled.connect(lambda _an: self._summe())
            self.dateien_lay.addWidget(h)
            if not e.get("gesperrt"):
                self.haken[pfad] = h
                self.groessen[pfad] = int(e.get("groesse") or 0)
        unten = QHBoxLayout()
        self.summe = label("", 10, "dim")
        unten.addWidget(self.summe, 1)
        k = Knopf("Laden", "⬇️")
        k.akzent = True
        k.clicked.connect(self._laden)
        unten.addWidget(k)
        self.dateien_lay.addLayout(unten)
        self.dateien_lay.addWidget(label(f"Ziel: {self.ziel}", 9.5, "blass"))
        self.dateien_w.show()
        self.fertig_w.hide()
        self._summe()
        self.meldung.setText("Häkchen setzen, was du brauchst – vorgewählt ist eine passende Auswahl.")

    def _summe(self) -> None:
        wahl = [p for p, h in self.haken.items() if h.isChecked()]
        self.summe.setText(f"Ausgewählt: {len(wahl)} Datei(en) · {_gb(sum(self.groessen[p] for p in wahl))}")

    def _laden(self) -> None:
        wahl = [p for p, h in self.haken.items() if h.isChecked()]
        if not wahl:
            self.meldung.setText("Erst mindestens eine Datei anhaken.")
            return
        self._takt = (0.0, 0)
        self.balken_datei.setValue(0)
        self.balken_gesamt.setValue(0)
        self.datei_text.setText("⏳ startet …")
        self.gesamt_text.setText("")
        self.lade_w.show()
        self.fertig_w.hide()
        self.k_stopp.setEnabled(True)
        self.fenster.senden({"op": "hub_laden", "repo": self.repo, "art": self.art, "pfade": wahl})

    # -- Fortschritt -------------------------------------------------------------------------------
    def fortschritt(self, ev: dict) -> None:
        import time
        groesse, erledigt = max(1, int(ev.get("groesse") or 0)), int(ev.get("erledigt") or 0)
        gesamt = max(1, int(ev.get("gesamt") or 0))
        fertig = int(ev.get("davor") or 0) + (erledigt if ev.get("phase") == "lade" else 0)
        phase = {"lade": "lädt", "pruefe": "prüft Angefangenes", "warte": "wartet kurz (viele Anfragen)"}.get(
            str(ev.get("phase")), "")
        name = str(ev.get("pfad") or "").rsplit("/", 1)[-1]
        self.datei_text.setText(f"Datei {ev.get('nr')} von {ev.get('anzahl')} · {name} · {phase}")
        self.balken_datei.setValue(int(1000 * min(erledigt, groesse) / groesse))
        self.balken_gesamt.setValue(int(1000 * min(fertig, gesamt) / gesamt))
        jetzt = time.monotonic()
        t0, b0 = self._takt
        if not t0:
            self._takt = (jetzt, fertig)
            tempo = ""
        else:
            rate = (fertig - b0) / max(0.5, jetzt - t0)
            rest = (gesamt - fertig) / rate if rate > 0 else 0
            tempo = f" · {rate / 1e6:.1f} MB/s · noch ~{int(rest // 60)}:{int(rest % 60):02d} min" if rate > 0 else ""
        self.gesamt_text.setText(f"Gesamt {_gb(fertig)} von {_gb(gesamt)}{tempo}")

    def fertig(self, ev: dict) -> None:
        self.lade_w.hide()
        self.k_stopp.setEnabled(False)
        self.ziel = str(ev.get("ordner") or self.ziel)
        self.fertig_text.setText(f"✅ Fertig – {ev.get('anzahl')} Datei(en) geprüft und abgelegt.")
        self.k_modell.setVisible(self.art in HUB_ALS_MODELL)
        self.fertig_w.show()

    def fehler(self, text: str) -> None:
        self.lade_w.hide()
        self.meldung.setText(f"⚠ {text}")

    def _als_modell(self) -> None:
        befehl = HUB_ALS_MODELL.get(self.art)
        if befehl:
            self.fenster.ausfuehren(befehl, "")


THEMA_ZEICHEN = {"bilder": "🎨", "coding": "💻", "internet": "🌐", "gedaechtnis": "🧠", "stimme": "🗣",
                 "wache": "🛡", "nemicli": "🎛", "allgemein": "💬"}
MEDAILLEN = ("🥇", "🥈", "🥉")


class _WertGrafik(QWidget):
    """Ein Modell als Grafik: links der ➕/➖-Balken mit Quote, rechts der Punkte-Verlauf als Kurve."""

    def __init__(self, m: dict):
        super().__init__()
        self.m = m
        self.setMinimumHeight(64)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        plus, minus = int(self.m.get("plus") or 0), int(self.m.get("minus") or 0)
        n = max(1, plus + minus)
        breite = int(w * 0.55)
        balken = QRectF(0, h / 2 - 9, breite, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(FARBE["flaeche_hoch"]))
        p.drawRoundedRect(balken, 9, 9)
        gruen = QRectF(balken.x(), balken.y(), breite * plus / n, balken.height())
        p.setBrush(QColor(FARBE["ok"]))
        if plus:
            p.drawRoundedRect(gruen, 9, 9)
        if minus:
            p.setBrush(QColor(FARBE["fehler"]))
            p.drawRoundedRect(QRectF(gruen.right(), balken.y(), breite * minus / n, balken.height()), 9, 9)
        p.setPen(QColor(FARBE["text"]))
        p.setFont(schrift(9, True))
        p.drawText(balken.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"➕ {plus}")
        p.drawText(balken.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   f"{minus} ➖")
        verlauf = [0] + [int(v) for v in self.m.get("verlauf") or []]
        kurve = QRectF(breite + 24, 6, w - breite - 30, h - 12)
        if kurve.width() < 30:
            return
        lo, hi = min(verlauf), max(verlauf)
        spanne = max(1, hi - lo)

        def punkt(i: int, v: int) -> QPoint:
            x = kurve.x() + kurve.width() * i / max(1, len(verlauf) - 1)
            return QPoint(int(x), int(kurve.bottom() - kurve.height() * (v - lo) / spanne))
        null = punkt(0, 0).y()
        p.setPen(QPen(QColor(FARBE["rand"]), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(kurve.x()), null, int(kurve.right()), null)
        stift = QPen(QColor(FARBE["ok"] if verlauf[-1] >= 0 else FARBE["fehler"]), 2.4)
        p.setPen(stift)
        for i in range(1, len(verlauf)):
            p.drawLine(punkt(i - 1, verlauf[i - 1]), punkt(i, verlauf[i]))
        p.setBrush(QColor(FARBE["lila"]))
        p.setPen(Qt.PenStyle.NoPen)
        ende = punkt(len(verlauf) - 1, verlauf[-1])
        p.drawEllipse(ende, 4, 4)
        p.end()


class RanglisteSeite(_Seite):
    """🏆 Toplist-KI: die Modelle nach den Wertungen des Nutzers (/wächter) – mit Balken, Verlauf, Themen und
    Temperatur. Die Daten kommen nur hierher, nie zur KI des Controllcenters."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.lay.addWidget(label("🏆 Toplist-KI", 20, "text", fett=True))
        self.lay.addWidget(label("So kamen die Antworten der Modelle bei dir an. Werten tust du im Terminal mit "
                                 "/wächter + oder /wächter - (gern mit Grund). Die KI sieht nur ihr ➕ oder ➖, "
                                 "nie diese Seite.", 10.5, "dim"))
        zeile = QHBoxLayout()
        self.schalter = QCheckBox("🌡 Temperatur aus meinen Wertungen wählen (nur Cloud-Modelle)")
        self.schalter.setFont(schrift(10.5))
        self.schalter.setStyleSheet(f"QCheckBox {{ color: {FARBE['text']}; }}")
        self.schalter.toggled.connect(lambda an: self.fenster.senden({"op": "rangliste_temperatur", "an": an}))
        zeile.addWidget(self.schalter)
        zeile.addStretch(1)
        k = Knopf("Aktualisieren", "🔄", klein=True)
        k.clicked.connect(lambda: self.fenster.senden({"op": "rangliste"}))
        zeile.addWidget(k)
        self.lay.addLayout(zeile)
        self.erklaerung = label("", 9.5, "blass")
        self.lay.addWidget(self.erklaerung)
        self.anfang = self.lay.count()
        self.lay.addStretch(1)

    def setzen(self, ev: dict) -> None:
        self.schalter.blockSignals(True)
        self.schalter.setChecked(bool(ev.get("temperatur_an")))
        self.schalter.blockSignals(False)
        self.erklaerung.setText(
            "An: NemiCLI probiert je Modell die Stufen 0,3 · 0,5 · 0,7 · 0,9 und nimmt immer öfter die, die die meisten "
            "➕ bekommt. Aus: jedes Modell läuft mit der Vorgabe seines Anbieters.")
        self.leeren(self.anfang)
        liste = [m for m in ev.get("liste") or [] if isinstance(m, dict)]
        if not liste:
            self.lay.addWidget(label("Noch keine Wertungen. Nach einer Antwort im Terminal /wächter + oder "
                                     "/wächter - tippen – dann erscheint das Modell hier.", 11, "dim"))
        for i, m in enumerate(liste):
            self.lay.addWidget(self._karte(i, m))
        self.lay.addStretch(1)

    def _karte(self, i: int, m: dict) -> QFrame:
        karte = QFrame()
        karte.setObjectName("wertkarte")
        rand = FARBE["lila"] if i == 0 else FARBE["rand"]
        karte.setStyleSheet(f"QFrame#wertkarte {{ background: {FARBE['flaeche']}; border: 1px solid {rand}; "
                            f"border-radius: 16px; }}")
        v = QVBoxLayout(karte)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)
        kopf = QHBoxLayout()
        platz = label(MEDAILLEN[i] if i < len(MEDAILLEN) else f"{i + 1}.", 16, "text", fett=True, umbruch=False)
        kopf.addWidget(platz)
        kopf.addWidget(label(str(m.get("modell") or "?"), 12.5, "text", fett=True, umbruch=False))
        kopf.addStretch(1)
        punkte = int(m.get("punkte") or 0)
        kopf.addWidget(label(f"{punkte:+d} Punkte · {round(100 * float(m.get('quote') or 0))} % ➕", 11,
                             "ok" if punkte >= 0 else "fehler", fett=True, umbruch=False))
        v.addLayout(kopf)
        v.addWidget(_WertGrafik(m))
        themen = m.get("themen") if isinstance(m.get("themen"), dict) else {}
        teile = [f"{THEMA_ZEICHEN.get(t, '•')} {t} {p}➕ {mi}➖" for t, (p, mi) in
                 sorted(themen.items(), key=lambda x: -(x[1][0] + x[1][1]))[:6]]
        if teile:
            v.addWidget(label("Themen:  " + "   ·   ".join(teile), 9.5, "dim"))
        temp = m.get("beste_temperatur")
        v.addWidget(label(f"🌡 Am besten kam an: Temperatur {temp:.1f}" if temp is not None else
                          "🌡 Temperatur: noch zu wenig Wertungen mit eingeschaltetem Schalter", 9.5, "blass"))
        return karte


GESETZ_FARBE = {1: "lila", 2: "lila", 3: "warn", 4: "fehler", 5: "fehler"}    # Rand je Gewicht


class GesetzbuchSeite(_Seite):
    """⚖️ Gesetzbuch: Richtlinien für alle KIs. Eintragen, gewichten, ändern und streichen nur hier per Klick;
    die KI hilft beim Formulieren und schlägt vor – übernehmen tut der Nutzer."""

    def __init__(self, fenster: "ControlFenster"):
        super().__init__()
        self.fenster = fenster
        self.erinnern_ab = 3
        self.lay.addWidget(label("⚖️ Gesetzbuch", 20, "text", fett=True))
        self.lay.addWidget(label("Deine Richtlinien für alle KIs von NemiCLI. Sie stehen ganz oben in jeder Anleitung "
                                 "und gehen vor allem anderen. Die KIs können sie lesen, aber nicht ändern.", 10.5,
                                 "dim"))
        self.entwurf = QPlainTextEdit()
        self.entwurf.setPlaceholderText("Neues Gesetz – einfach so schreiben, wie es dir einfällt, z. B. "
                                        "„keine bilder von der seite von der person“")
        self.entwurf.setFixedHeight(84)
        self.entwurf.setFont(schrift(11))
        self.entwurf.setStyleSheet(_feld_stil())
        self.lay.addWidget(self.entwurf)
        zeile = QHBoxLayout()
        self.formulieren = Knopf("Klarer formulieren", "✨", klein=True)
        self.formulieren.setToolTip("Die KI macht aus deinem Entwurf ein klares Gesetz – du entscheidest danach")
        self.formulieren.clicked.connect(self._formulieren)
        eintragen = Knopf("Eintragen", "➕", klein=True)
        eintragen.clicked.connect(self._eintragen)
        zeile.addWidget(self.formulieren)
        zeile.addWidget(eintragen)
        zeile.addStretch(1)
        self.vorschlag_knopf = Knopf("Vorschläge holen", "🔍", klein=True)
        self.vorschlag_knopf.setToolTip("Die KI liest neutral deine letzten Gespräche und schlägt Gesetze vor")
        self.vorschlag_knopf.clicked.connect(self._vorschlaege)
        zeile.addWidget(self.vorschlag_knopf)
        self.lay.addLayout(zeile)
        self.status = label("", 10, "cyan")
        self.lay.addWidget(self.status)
        self.vorschlag_lay = QVBoxLayout()
        self.lay.addLayout(self.vorschlag_lay)
        self.gruppen_lay = QVBoxLayout()
        self.gruppen_lay.setSpacing(8)
        self.lay.addLayout(self.gruppen_lay)
        self.lay.addWidget(label("Gesetze", 13, "text", fett=True))
        self.hinweis_gewicht = label("", 9.5, "blass")
        self.lay.addWidget(self.hinweis_gewicht)
        self.gesetze_lay = QVBoxLayout()
        self.gesetze_lay.setSpacing(8)
        self.lay.addLayout(self.gesetze_lay)
        self.lay.addStretch(1)

    # -- Eingaben ---------------------------------------------------------------------------
    def _text(self) -> str:
        return " ".join(self.entwurf.toPlainText().split())

    def _formulieren(self) -> None:
        if not self._text():
            self.status.setText("Erst etwas hineinschreiben – dann formuliere ich es klarer.")
            return
        self.status.setText("✨ Die KI formuliert …")
        self.formulieren.setEnabled(False)
        self.fenster.senden({"op": "gesetz_ki", "art": "formulieren", "text": self._text()})

    def _eintragen(self) -> None:
        if not self._text():
            self.status.setText("Das Feld ist leer.")
            return
        self.fenster.senden({"op": "gesetz_aendern", "art": "neu", "text": self._text(), "gewicht": 1})
        self.entwurf.clear()
        self.status.setText("")

    def _vorschlaege(self) -> None:
        self.status.setText("🔍 Die KI liest deine letzten Gespräche …")
        self.vorschlag_knopf.setEnabled(False)
        self.fenster.senden({"op": "gesetz_ki", "art": "vorschlaege"})

    # -- Vom Hauptprogramm -------------------------------------------------------------------
    def formuliert(self, text: str) -> None:
        self.formulieren.setEnabled(True)
        if text:
            self.entwurf.setPlainText(text)
            self.status.setText("✨ So wäre es klar. Passt es? Dann „Eintragen“ – oder vorher noch ändern.")
        else:
            self.status.setText("Die KI hatte keinen Vorschlag – schreib es gern noch etwas anders.")

    def hinweis(self, text: str) -> None:
        self.formulieren.setEnabled(True)
        self.vorschlag_knopf.setEnabled(True)
        self.status.setText(text)

    def vorschlaege(self, liste: list) -> None:
        self.vorschlag_knopf.setEnabled(True)
        _layout_leeren(self.vorschlag_lay)
        eintraege = [v for v in liste if isinstance(v, dict) and v.get("gesetz")]
        self.status.setText(f"🔍 {len(eintraege)} Vorschläge – übernimm, was dir passt." if eintraege else
                            "🔍 Keine neuen Vorschläge gefunden.")
        for v in eintraege:
            karte = QFrame()
            karte.setObjectName("vorschlag")
            karte.setStyleSheet(f"QFrame#vorschlag {{ background: {FARBE['flaeche']}; border: 1px dashed "
                                f"{FARBE['cyan']}; border-radius: 12px; }}")
            h = QHBoxLayout(karte)
            h.setContentsMargins(14, 8, 10, 8)
            texte = QVBoxLayout()
            texte.addWidget(label(str(v["gesetz"]), 11, "text", fett=True))
            if v.get("grund"):
                texte.addWidget(label(str(v["grund"]), 9.5, "dim"))
            h.addLayout(texte, 1)
            ja = Knopf("Übernehmen", "✅", klein=True)
            ja.clicked.connect(lambda _=False, t=str(v["gesetz"]), k=karte: self._uebernehmen(t, k))
            nein = Knopf("", "✖", klein=True)
            nein.setToolTip("Verwerfen")
            nein.clicked.connect(lambda _=False, k=karte: k.deleteLater())
            h.addWidget(ja)
            h.addWidget(nein)
            self.vorschlag_lay.addWidget(karte)

    def _uebernehmen(self, text: str, karte: QFrame) -> None:
        self.fenster.senden({"op": "gesetz_aendern", "art": "neu", "text": text, "gewicht": 1})
        karte.deleteLater()

    def nutzer_aktiv(self) -> None:
        """Der Nutzer hat das Gesetzbuch selbst geöffnet: jetzt darf die KI ähnliche Gesetze suchen."""
        self.fenster.senden({"op": "gesetz_vergleichen"})

    def setzen(self, ev: dict) -> None:
        self.erinnern_ab = int(ev.get("erinnern_ab") or 3)
        self.hinweis_gewicht.setText(f"− und + ändern das Gewicht (⚡ 1 bis 5). Ab {'⚡' * self.erinnern_ab} erinnert "
                                     "NemiCLI die KI zusätzlich bei jeder Nachricht.")
        gruppen = [g for g in ev.get("gruppen") or [] if isinstance(g, dict) and g.get("ids")]
        self._gruppen_zeigen(gruppen, ev.get("zurueck") or [], bool(ev.get("vergleicht")))
        marke = {int(i): str(g.get("buchstabe") or "") for g in gruppen for i in g["ids"]}
        _layout_leeren(self.gesetze_lay)
        gesetze = [g for g in ev.get("liste") or [] if isinstance(g, dict)]
        if not gesetze:
            self.gesetze_lay.addWidget(label("Noch keine Gesetze. Schreib oben eins – oder lass dir Vorschläge "
                                             "holen.", 10.5, "dim"))
        for g in gesetze:
            self.gesetze_lay.addWidget(self._tag(g, marke.get(int(g.get("id") or 0), "")))

    def _gruppen_zeigen(self, gruppen: list[dict], zurueck: list, vergleicht: bool) -> None:
        """Grün markierte Gruppen ähnlicher Gesetze mit Grund, Vorschlag und der Frage ans Zusammenführen."""
        _layout_leeren(self.gruppen_lay)
        if vergleicht:
            self.gruppen_lay.addWidget(label("🔍 Die KI vergleicht deine Gesetze …", 10.5, "cyan"))
        for g in gruppen:
            karte = QFrame()
            karte.setObjectName("aehnlich")
            karte.setStyleSheet(f"QFrame#aehnlich {{ background: {FARBE['flaeche']}; border: 2px solid "
                                f"{FARBE['ok']}; border-radius: 12px; }}")
            v = QVBoxLayout(karte)
            v.setContentsMargins(14, 10, 12, 10)
            nummern = [str(n) for n in g.get("nummern") or []]
            liste_text = " und ".join([", ".join(nummern[:-1]), nummern[-1]]) if len(nummern) > 2 else " und ".join(nummern)
            v.addWidget(label(f"🟢 {g.get('buchstabe', '')} · Gesetz {liste_text} meinen fast dasselbe", 11.5, "ok",
                              fett=True))
            if g.get("grund"):
                v.addWidget(label(str(g["grund"]), 10.5, "text"))
            v.addWidget(label(f"Zusammen wäre es: {g.get('vorschlag', '')}  {'⚡' * int(g.get('gewicht') or 1)}", 10.5,
                              "dim"))
            v.addWidget(label("Soll ich sie zu einem Gesetz machen?", 10.5, "text", fett=True))
            h = QHBoxLayout()
            ja = Knopf("Ja, zusammenführen", "✅", klein=True)
            ja.clicked.connect(lambda _=False, i=int(g["id"]): self.fenster.senden(
                {"op": "gesetz_fusion", "gruppe": i, "ja": True}))
            nein = Knopf("Nein, getrennt lassen", "✖", klein=True)
            nein.clicked.connect(lambda _=False, i=int(g["id"]): self.fenster.senden(
                {"op": "gesetz_fusion", "gruppe": i, "ja": False}))
            h.addWidget(ja)
            h.addWidget(nein)
            h.addStretch(1)
            v.addLayout(h)
            self.gruppen_lay.addWidget(karte)
        if zurueck:
            h = QHBoxLayout()
            zur = Knopf("Letzte Zusammenführung zurückholen", "↩️", klein=True)
            zur.setToolTip("Holt zurück:\n" + "\n".join(f"• {t}" for t in zurueck))
            zur.clicked.connect(lambda: self.fenster.senden({"op": "gesetz_zurueck"}))
            h.addWidget(zur)
            h.addStretch(1)
            self.gruppen_lay.addLayout(h)

    def _tag(self, g: dict, gruppe: str = "") -> QFrame:
        gewicht = max(1, min(5, int(g.get("gewicht") or 1)))
        tag = QFrame()
        tag.setObjectName("gesetz")
        rand = FARBE["ok"] if gruppe else FARBE[GESETZ_FARBE[gewicht]]       # grün: ähnelt anderen Gesetzen
        breite = max(2, 1 + gewicht // 2) if gruppe else 1 + gewicht // 2
        tag.setStyleSheet(f"QFrame#gesetz {{ background: {FARBE['flaeche']}; border: {breite}px "
                          f"{'dashed' if gruppe else 'solid'} {rand}; border-radius: 14px; }}")
        h = QHBoxLayout(tag)
        h.setContentsMargins(14, 8, 10, 8)
        h.setSpacing(6)
        nr = label(f"{g.get('nr', '')}.", 12, "text", fett=True, umbruch=False)
        nr.setMinimumWidth(30)
        h.addWidget(nr)
        if gruppe:
            h.addWidget(label(f"🟢 {gruppe}", 10.5, "ok", fett=True, umbruch=False))
        text = label(f"⚖️ {g.get('text', '')}", 11, "text", fett=gewicht >= 2)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        h.addWidget(text, 1)
        h.addWidget(label("⚡" * gewicht, 11, GESETZ_FARBE[gewicht], umbruch=False))
        nr = int(g.get("id") or 0)
        for zeichen, tipp, schritt in (("−", "Leichter", -1), ("+", "Schwerer – wenn es nicht eingehalten wird", 1)):
            k = Knopf("", zeichen, klein=True)
            k.setToolTip(tipp)
            k.setEnabled(1 <= gewicht + schritt <= 5)
            k.clicked.connect(lambda _=False, s=schritt: self.fenster.senden(
                {"op": "gesetz_aendern", "art": "gewicht", "id": nr, "schritt": s}))
            h.addWidget(k)
        bearbeiten = Knopf("", "✏️", klein=True)
        bearbeiten.setToolTip("Ändern")
        bearbeiten.clicked.connect(lambda: self._bearbeiten(nr, str(g.get("text") or "")))
        weg = Knopf("", "🗑", klein=True)
        weg.setToolTip("Streichen")
        weg.clicked.connect(lambda: self._streichen(nr, str(g.get("text") or "")))
        h.addWidget(bearbeiten)
        h.addWidget(weg)
        return tag

    def _bearbeiten(self, nr: int, alt: str) -> None:
        neu, ok = QInputDialog.getText(self, "Gesetz ändern", "Gesetz:", text=alt)
        if ok and " ".join(neu.split()) and neu.strip() != alt:
            self.fenster.senden({"op": "gesetz_aendern", "art": "text", "id": nr, "text": neu})

    def _streichen(self, nr: int, text: str) -> None:
        if QMessageBox.question(self, "Gesetz streichen", f"Dieses Gesetz streichen?\n\n{text}") == \
                QMessageBox.StandardButton.Yes:
            self.fenster.senden({"op": "gesetz_aendern", "art": "loeschen", "id": nr})


class ControlFenster(QWidget):
    def __init__(self, leitung=None, fortsetzen: bool = False):
        super().__init__()
        self.leitung = leitung
        self.kugel_da = False
        self.gewaehlt = ""
        self.baum_daten: list[dict] = []
        self.testbilder, self.testbild_max = 0, 1
        self.setObjectName("control")
        self.setWindowTitle("Nemi Controllcenter")
        self.resize(1440, 900)
        self.setStyleSheet(f"QWidget#control {{ background: {FARBE['grund']}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        kopf = QFrame()
        kopf.setObjectName("kopf")
        kopf.setStyleSheet(f"QFrame#kopf {{ background: {FARBE['leiste']}; border-bottom: 1px solid {FARBE['rand']}; }}")
        kh = QHBoxLayout(kopf)
        kh.setContentsMargins(22, 12, 22, 12)
        kh.setSpacing(14)
        logo = QLabel("🎛")
        logo.setFont(schrift(17))
        logo.setFixedSize(50, 50)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {FARBE['lila']}, stop:1 "
                           f"{FARBE['cyan']}); border-radius: 15px;")
        kh.addWidget(logo)
        texte = QVBoxLayout()
        texte.setSpacing(0)
        texte.addWidget(label("Nemi Controllcenter", 16, "text", fett=True, umbruch=False))
        texte.addWidget(label("Control Setting for your NemiCLI · dein Einstellungen-Center", 9.5, "cyan",
                              umbruch=False))
        kh.addLayout(texte)
        kh.addStretch(1)
        schrift_pille = QFrame()
        schrift_pille.setObjectName("schriftpille")
        schrift_pille.setStyleSheet(f"QFrame#schriftpille {{ background: {FARBE['flaeche']}; border: 1px solid "
                                    f"{FARBE['rand']}; border-radius: 18px; }}")
        sp = QHBoxLayout(schrift_pille)
        sp.setContentsMargins(6, 2, 6, 2)
        sp.setSpacing(2)
        kleiner = Knopf("", "A−", klein=True)
        kleiner.setToolTip("Schrift kleiner")
        kleiner.clicked.connect(lambda: self.schrift_aendern(-SCHRIFT_SCHRITT))
        groesser = Knopf("", "A+", klein=True)
        groesser.setToolTip("Schrift größer")
        groesser.clicked.connect(lambda: self.schrift_aendern(SCHRIFT_SCHRITT))
        schrift_pille.setSizePolicy(schrift_pille.sizePolicy().horizontalPolicy(),
                                    schrift_pille.sizePolicy().Policy.Fixed)
        self.schrift_text = label(f"{design.px('control')} px", 9, "blass", umbruch=False)
        self.schrift_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sp.addWidget(kleiner)
        sp.addWidget(self.schrift_text)
        sp.addWidget(groesser)
        kh.addWidget(schrift_pille)
        v.addWidget(kopf)

        koerper = QHBoxLayout()
        koerper.setContentsMargins(0, 0, 0, 0)
        koerper.setSpacing(0)
        links = QFrame()
        links.setObjectName("links")
        links.setFixedWidth(380)
        links.setStyleSheet(f"QFrame#links {{ background: {FARBE['leiste']}; border-right: 1px solid "
                            f"{FARBE['rand']}; }}")
        lv = QVBoxLayout(links)
        lv.setContentsMargins(14, 14, 8, 12)
        lv.setSpacing(10)
        self.suche = QLineEdit()
        self.suche.setFont(schrift(10.5))
        self.suche.setPlaceholderText("🔍 Einstellung suchen …")
        self.suche.setStyleSheet(f"QLineEdit {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: "
                                 f"1px solid {FARBE['rand']}; border-radius: 14px; padding: 8px 12px; }}"
                                 f"QLineEdit:focus {{ border: 1px solid {FARBE['lila']}; }}")
        self.suche.textChanged.connect(self._filtern)
        lv.addWidget(self.suche)
        self.baum = QTreeWidget()
        self.baum.setHeaderHidden(True)
        self.baum.setFont(schrift(10.5))
        self.baum.setIndentation(16)
        self.baum.setMouseTracking(True)
        self.baum.setItemDelegate(_BaumZeichner(self.baum))
        self.baum.setStyleSheet(f"QTreeWidget {{ background: transparent; color: {FARBE['text']}; border: none; "
                                f"outline: 0; }} QTreeWidget::item {{ border: none; }}"
                                f"QTreeWidget::item:selected, QTreeWidget::item:hover {{ background: transparent; }}"
                                f"QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}"
                                f"QScrollBar::handle:vertical {{ background: {FARBE['rand']}; border-radius: 4px; "
                                f"min-height: 30px; }} QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}")
        self.baum.itemClicked.connect(lambda it, _s: self._geklickt(it))
        lv.addWidget(self.baum, 1)
        koerper.addWidget(links)

        self.stapel = QStackedWidget()
        self.start = StartSeite(self)
        self.befehl = BefehlSeite(self)
        self.gesundheit = GesundheitSeite(self)
        self.system = SystemSeite(self)
        self.modelle = ModelleSeite(self)
        self.terminal = TerminalSeite(self)
        self.tresor = TresorSeite(self)
        self.sd = SdSeite(self)
        self.hub = HubSeite(self)
        self.rangliste = RanglisteSeite(self)
        self.gesetzbuch = GesetzbuchSeite(self)
        self.krea = KreaSeite(self)
        for s in (self.start, self.befehl, self.gesundheit, self.system, self.modelle, self.terminal, self.tresor,
                  self.sd, self.hub, self.rangliste, self.gesetzbuch, self.krea):
            self.stapel.addWidget(s)
        koerper.addWidget(self.stapel, 1)
        v.addLayout(koerper, 1)

        self.zeiger = Geistzeiger(self)
        self.nemi = ControlAI(self, self._kontext, self.ausfuehren)
        self.nemi.frage_senden.connect(self._frage)
        self.nemi.stopp.connect(self._anhalten)
        self._kugel_sichtbar(False)
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._esc_gedrueckt)

        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)
            leitung.getrennt.connect(self._getrennt)
            leitung.senden({"op": "stand" if fortsetzen else "start"})   # start: Testbild-Zähler neu
        else:
            self._ohne_sitzung()

    # -- Schriftgröße ---------------------------------------------------------------------------
    def schrift_aendern(self, schritt: int) -> None:
        """A−/A+: eigene Schriftgröße des Controllcenters speichern und das Fenster darin neu aufbauen."""
        if self.nemi.laufend is not None:
            self.nemi.hinweis("Erst wenn ich fertig bin – dann passe ich die Schrift an.")
            return
        neu = max(SCHRIFT_MIN, min(SCHRIFT_MAX, design.px("control") + schritt))
        if neu == design.px("control"):
            return
        try:
            import config
            werte = dict(config.load().get("schriftgroesse") or {})
            werte["control"] = neu
            config.update(schriftgroesse=werte)
        except Exception as e:
            self.nemi.hinweis(f"Schriftgröße nicht gespeichert: {e}")
            return
        design.neu_lesen()
        self.neu_aufbauen()

    def neu_aufbauen(self) -> "ControlFenster":
        """Neues Fenster in der neuen Schriftgröße mit demselben Stand (Auswahl, Kugel, Gespräch, Leitung)."""
        leitung = self.leitung
        if leitung is not None:
            for signal, ziel in ((leitung.ereignis, self._ereignis), (leitung.getrennt, self._getrennt)):
                try:
                    signal.disconnect(ziel)
                except (RuntimeError, TypeError):
                    pass
        neu = ControlFenster(leitung, fortsetzen=True)
        neu.setGeometry(self.geometry())
        if self.isMaximized():
            neu.showMaximized()
        else:
            neu.show()
        _dunkler_rahmen(neu)
        neu.nemi.verlauf = list(self.nemi.verlauf)
        neu.testbilder, neu.testbild_max = self.testbilder, self.testbild_max
        if self.kugel_da:
            neu.kugel_da = True
            neu._kugel_sichtbar(True)
            neu.nemi.anordnen()
        if self.gewaehlt:
            neu.waehlen_id(self.gewaehlt)
        _OFFEN[:] = [neu]
        self.leitung = None
        self.close()
        self.deleteLater()
        return neu

    # -- Sitzung ------------------------------------------------------------------------------
    def senden(self, auftrag: dict) -> None:
        if self.leitung is None:
            self.nemi.hinweis("Nicht mit NemiCLI verbunden – im Terminal /control eingeben.")
            return
        self.leitung.senden(auftrag)

    def ausfuehren(self, befehl: str, zusatz: str) -> None:
        self.befehl.ergebnis_setzen(f"⏳ {befehl} {zusatz}".strip())
        self.senden({"op": "ausfuehren", "befehl": befehl, "zusatz": zusatz})

    def _ohne_sitzung(self) -> None:
        try:
            import kontrollzentrum as KZ
            self._baum_bauen(KZ.baum())
        except Exception:
            pass
        self.nemi.stand("Nemi", False)

    # -- Baum ---------------------------------------------------------------------------------
    def _baum_bauen(self, aeste: list[dict]) -> None:
        self.baum_daten = aeste
        offen = {self.baum.topLevelItem(i).data(0, ROLLE_ID) for i in range(self.baum.topLevelItemCount())
                 if self.baum.topLevelItem(i).isExpanded()}
        self.baum.clear()
        for ast in aeste:
            it = QTreeWidgetItem([ast["titel"]])
            it.setData(0, ROLLE_ID, ast["id"])
            if ast.get("gruppe"):
                it.setData(0, ROLLE_ART, "gruppe")
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)                    # Überschrift: nicht wählbar
                self.baum.addTopLevelItem(it)
                continue
            it.setData(0, ROLLE_ART, "ast" if ast["id"].startswith("thema:") else "seite")
            if ast.get("kinder") and ast["id"] != "favoriten":
                it.setData(0, ROLLE_ZAHL, len(ast["kinder"]))
            for kind in ast.get("kinder", []):
                k = QTreeWidgetItem([kind["titel"]])
                k.setData(0, ROLLE_ID, kind["id"])
                k.setData(0, ROLLE_ART, "befehl")
                k.setData(0, ROLLE_NAME, ("⭐ " if kind.get("favorit") else "") + str(kind.get("name") or kind["titel"]))
                k.setData(0, ROLLE_KURZ, str(kind.get("kurz") or ""))
                k.setToolTip(0, kind["titel"])
                it.addChild(k)
            self.baum.addTopLevelItem(it)
            it.setExpanded(ast["id"] in offen or ast["id"] == "favoriten")
        self._filtern(self.suche.text())
        if self.gewaehlt:
            self._markieren(self.gewaehlt)

    def _item(self, kennung: str) -> QTreeWidgetItem | None:
        for i in range(self.baum.topLevelItemCount()):
            ast = self.baum.topLevelItem(i)
            if ast.data(0, ROLLE_ID) == kennung:
                return ast
            for j in range(ast.childCount()):
                if ast.child(j).data(0, ROLLE_ID) == kennung:
                    return ast.child(j)
        if kennung.startswith("befehl:"):                  # Favorit oder Thema: der erste Treffer
            return None
        return None

    def _markieren(self, kennung: str) -> None:
        it = self._item(kennung)
        if it is not None:
            if it.parent() is not None:
                it.parent().setExpanded(True)
            self.baum.setCurrentItem(it)
            self.baum.scrollToItem(it)

    def _filtern(self, text: str) -> None:
        q = text.strip().casefold()
        for i in range(self.baum.topLevelItemCount()):
            ast = self.baum.topLevelItem(i)
            if ast.data(0, ROLLE_ART) == "gruppe":                         # beim Suchen ohne Überschriften
                ast.setHidden(bool(q))
                continue
            treffer = 0
            for j in range(ast.childCount()):
                k = ast.child(j)
                zeigen = not q or q in k.text(0).casefold()
                k.setHidden(not zeigen)
                treffer += zeigen
            ast.setHidden(bool(q) and not treffer and q not in ast.text(0).casefold())
            if q and treffer:
                ast.setExpanded(True)

    def _geklickt(self, it: QTreeWidgetItem) -> None:
        kennung = it.data(0, ROLLE_ID) or ""
        if kennung.startswith("gruppe:"):
            return
        if kennung.startswith("thema:") or (kennung == "favoriten" and it.childCount()):
            it.setExpanded(not it.isExpanded())
            return
        self.waehlen_id(kennung)
        if kennung == "gesetzbuch":          # nur der eigene Klick des Nutzers, nicht das „zeigen“ der KI
            self.gesetzbuch.nutzer_aktiv()

    def waehlen_id(self, kennung: str) -> None:
        """Eine Seite zeigen: „befehl:/x“, „gesundheit“, „system“, „modelle“, „terminal“, „favoriten“."""
        if kennung == "favoriten":
            kennung = "terminal"
        self.gewaehlt = kennung
        self._zur_kugel()
        self._markieren(kennung)
        if kennung.startswith("befehl:"):
            self.stapel.setCurrentIndex(SEITEN["befehl"])
            self.senden({"op": "eintrag", "befehl": kennung[7:]})
        elif kennung in SEITEN:
            self.stapel.setCurrentIndex(SEITEN[kennung])
            if kennung in ("gesundheit", "system", "modelle", "tresor", "sd", "hub", "rangliste", "gesetzbuch",
                           "krea"):
                self.senden({"op": kennung})

    # -- Vom Feld zur Kugel ----------------------------------------------------------------
    def _kugel_sichtbar(self, an: bool) -> None:
        self.nemi.kugel.setVisible(an)
        if not an:
            self.nemi.feld.hide()
            self.nemi.blase.hide()

    def _zur_kugel(self) -> None:
        """Das große Eingabefeld der Startseite fliegt als Kugel nach unten rechts (einmal)."""
        if self.kugel_da:
            return
        self.kugel_da = True
        pille = self.start.pille
        if not pille.isVisible() or self.width() < 200:
            self._kugel_sichtbar(True)
            self.nemi.anordnen()
            return
        bild = QLabel(self)
        bild.setPixmap(pille.grab())
        bild.setScaledContents(True)
        bild.setGeometry(QRect(pille.mapTo(self, QPoint(0, 0)), pille.size()))
        bild.show()
        bild.raise_()
        self.nemi.anordnen()
        rand = 6 + LICHTHOF
        ziel = self.nemi.kugel.geometry().adjusted(rand, rand, -rand, -rand)
        flug = QPropertyAnimation(bild, b"geometry", self)
        flug.setDuration(520)
        flug.setEasingCurve(QEasingCurve.Type.InOutCubic)
        flug.setStartValue(bild.geometry())
        flug.setEndValue(QRect(ziel.topLeft(), ziel.size()))

        def fertig():
            bild.deleteLater()
            self._kugel_sichtbar(True)
            self.nemi.anordnen()
        flug.finished.connect(fertig)
        flug.start()
        self._flug = flug                                 # gegen das Aufräumen durch Python halten

    def erste_frage(self, text: str, anzeige: str | None = None) -> None:
        if not self.kugel_da:
            if self.stapel.currentIndex() == SEITEN["start"]:
                self.stapel.setCurrentIndex(SEITEN["gesundheit"])
                self.gewaehlt = "gesundheit"
                self._markieren("gesundheit")
                self.senden({"op": "gesundheit"})
            self._zur_kugel()
            QTimer.singleShot(560, lambda: self.nemi.fragen(text, anzeige))
        else:
            self.nemi.fragen(text, anzeige)

    # -- KI -----------------------------------------------------------------------------------
    def _kontext(self) -> dict:
        return {"gewaehlt": self.gewaehlt}

    def _frage(self, auftrag: dict) -> None:
        self.senden(auftrag)

    def _anhalten(self, nr: int) -> None:
        self.senden({"op": "stopp", "id": nr})
        self.zeiger.ausblenden()
        self.nemi.fehler(nr, "⏹ Angehalten.")

    def _aktion(self, ev: dict) -> None:
        """„zeigen“ der KI: Zeiger fliegt zum Eintrag im Baum, dann öffnet die Mitte ihn."""
        nr, schritt = ev.get("id"), ev.get("schritt")
        aktion = ev.get("aktion") if isinstance(ev.get("aktion"), dict) else {}

        def melden(ok: bool, text: str) -> None:
            self.senden({"op": "ergebnis", "id": nr, "schritt": schritt, "ok": ok, "text": text})

        if self.nemi.laufend != nr or aktion.get("tool") != "zeigen":
            melden(False, "Der Auftrag läuft nicht mehr.")
            return
        kennung = f"befehl:{aktion['befehl']}" if aktion.get("befehl") else str(aktion.get("seite") or "")
        if kennung == "favoriten":
            kennung = "terminal"
        it = self._item(kennung)
        if it is None:
            melden(False, f"{kennung} steht nicht im Baum.")
            return
        if it.parent() is not None:
            it.parent().setExpanded(True)
        self.baum.scrollToItem(it)
        r = self.baum.visualItemRect(it)
        ziel = self.baum.viewport().mapTo(self, r.center() + QPoint(-40, -6))
        self.zeiger.fliegen(ziel, f"zeigt {aktion.get('befehl') or kennung}")
        QTimer.singleShot(FLUG_MS + 150, lambda: (self.waehlen_id(kennung), melden(True, f"Gezeigt: {kennung}")))
        QTimer.singleShot(FLUG_MS + 2500, self.zeiger.ausblenden)

    # -- Ereignisse der Sitzung -------------------------------------------------------------
    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        nr = ev.get("id") if isinstance(ev.get("id"), int) else None
        if t == "stand":
            name = str(ev.get("name") or "Nemi")
            self.nemi.stand(name, bool(ev.get("modell")),
                            ev.get("gesichter") if isinstance(ev.get("gesichter"), dict) else None)
            self.start.gruss.setText(f"Hallo, ich bin {name}! Frag mich alles rund um deine Einstellungen.")
            if isinstance(ev.get("baum"), list):
                self._baum_bauen(ev["baum"])
            self.terminal.setzen(str(ev.get("modus") or ""), [str(f) for f in ev.get("favoriten") or []])
            self.testbilder = int(ev.get("testbilder") or 0)
            self.testbild_max = int(ev.get("testbild_max") or 1)
            self.gesundheit.testbild_moeglich(self.testbilder < self.testbild_max)
        elif t == "eintrag" and isinstance(ev.get("eintrag"), dict):
            if self.gewaehlt == f"befehl:{ev['eintrag'].get('name')}":
                self.befehl.zeigen(ev["eintrag"])
        elif t == "ausgefuehrt":
            text = ("✓ " if ev.get("ok") else "⚠ ") + str(ev.get("zeile") or "") + (
                f" – {ev['text']}" if ev.get("text") else "")
            self.befehl.ergebnis_setzen(text + ("  (Rückfragen stehen im Terminal)" if ev.get("ok") else ""))
        elif t == "befunde" and isinstance(ev.get("befunde"), list):
            self.gesundheit.zeilen(ev["befunde"])
        elif t == "system" and isinstance(ev.get("werte"), list):
            self.system.werte(ev["werte"])
        elif t == "modelle" and isinstance(ev.get("liste"), list):
            self.modelle.liste(ev["liste"])
        elif t == "testbild_laeuft":
            self.testbilder += 1
            self.gesundheit.testbild_moeglich(False)
            self.gesundheit.bild_text.setText("🌈 Testbild wird gemalt …")
        elif t == "testbild":
            self.gesundheit.testbild(ev)
        elif t == "hinweis":
            self.nemi.hinweis(str(ev.get("text") or ""))
            self.gesundheit.testbild_moeglich(self.testbilder < self.testbild_max)
        elif t == "aktion":
            self._aktion(ev)
        elif t == "vorschlag":
            self.nemi.vorschlag(str(ev.get("befehl") or ""), str(ev.get("zusatz") or ""), str(ev.get("grund") or ""))
        elif t == "status":
            self.nemi.status(nr or 0, str(ev.get("text") or ""))
        elif t == "antwort":
            self.zeiger.ausblenden()
            self.nemi.antwort(nr or 0, str(ev.get("text") or ""))
        elif t == "fehler":
            self.zeiger.ausblenden()
            self.nemi.fehler(nr, str(ev.get("text") or ""))
        elif t == "tresor" and isinstance(ev.get("gruppen"), list):
            self.tresor.setzen(ev)
        elif t == "sd" and isinstance(ev.get("vorlagen"), list):
            self.sd.setzen(ev)
        elif t == "krea" and isinstance(ev.get("modelle"), list):
            self.krea.setzen(ev)
        elif t == "tresor_zeigen":
            self.tresor.wunsch = str(ev.get("name") or "")
            self.waehlen_id("tresor")
        elif t == "tresor_vernichtet":
            self.nemi.hinweis(f"🚨 Tresor vernichtet: {ev.get('grund') or 'Siegel verletzt'}. Bitte die Schlüssel "
                              "neu eintragen.")
            if self.gewaehlt == "tresor":
                self.senden({"op": "tresor"})
        elif t == "rangliste":
            self.rangliste.setzen(ev)
        elif t == "gesetzbuch":
            self.gesetzbuch.setzen(ev)
        elif t == "gesetz_formuliert":
            self.gesetzbuch.formuliert(str(ev.get("text") or ""))
        elif t == "gesetz_vorschlaege":
            self.gesetzbuch.vorschlaege(ev.get("liste") if isinstance(ev.get("liste"), list) else [])
        elif t == "gesetz_hinweis":
            self.gesetzbuch.hinweis(str(ev.get("text") or ""))
        elif t == "hub_stand":
            self.hub.stand(ev)
        elif t == "hub_treffer" and isinstance(ev.get("liste"), list):
            self.hub.treffer(ev["liste"])
        elif t == "hub_dateien":
            self.hub.dateien(ev)
        elif t == "hub_fortschritt":
            self.hub.fortschritt(ev)
        elif t == "hub_fertig":
            self.hub.fertig(ev)
        elif t == "hub_fehler":
            self.hub.fehler(str(ev.get("text") or ""))
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _esc_gedrueckt(self) -> None:
        """ESC hält alles an: die KI und einen laufenden Download (Angefangenes bleibt für später)."""
        self.nemi.anhalten()
        if self.hub.zustand.get("laeuft"):
            self.senden({"op": "hub_stopp"})

    def closeEvent(self, e):
        if self.leitung is not None and self.hub.zustand.get("offen"):
            self.leitung.senden({"op": "hub_schliessen"})           # Fenster zu: Hugging Face ist nicht erreichbar
        super().closeEvent(e)

    def _getrennt(self) -> None:
        self.leitung = None
        laufend = self.nemi.laufend
        self.nemi.fehler(laufend, "Verbindung zur Sitzung getrennt – im Terminal /control neu öffnen.")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.nemi.anordnen()

    def showEvent(self, e):
        super().showEvent(e)
        self.nemi.anordnen()
        self.start.eingabe.setFocus()


def _leitung():
    adresse = os.environ.pop("NEMICLI_GUI_PIPE", "")
    schluessel = os.environ.pop("NEMICLI_GUI_KEY", "")
    if not adresse or not schluessel:
        return None
    try:
        from gui_fenster import Leitung
        return Leitung(adresse, bytes.fromhex(schluessel))
    except Exception:
        return None


def starten() -> int:
    design.flaeche_setzen("control")         # eigene Schriftgröße (A−/A+, auch /schriftgröße control)
    app = app_vorbereiten("NemiCLI.Control")
    fenster = ControlFenster(_leitung())
    _OFFEN[:] = [fenster]
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
