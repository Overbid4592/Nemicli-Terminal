"""
fenster.py - NemiView als Fenster: Store mit Kacheln (➕ Neue App baut die KI), App-Ansicht (Maus, Tasten, Formular-Elemente) und debugAI.

debugAI ist ein eigener Qt-Bereich neben der App – das JavaScript der App erreicht ihn nicht. Aufträge gehen über
die Leitung (Named Pipe mit Schlüssel, wie /gui) an die NemiCLI-Sitzung; die KI ändert die Dateien, die Ansicht lädt
neu. Vorher → nachher zeigt die Sicherung (Apps/_vorher/<app>) und die neue Fassung nebeneinander.
Gezeichnet wird mit QPainter ohne Grafikkarte; ✕ beendet den Prozess.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (QApplication, QColorDialog, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from .laufzeit import AppLaufzeit
from .layout import Layout
from .zeichnen import Maler, QtMesser, bild_laden

MAX_NACHRICHT = 4 * 1024 * 1024
DUNKEL = "#0f1117"
KARTE = "#1a1d27"
AKZENT = "#8b7cff"
STIL = f"""
QWidget {{ background: {DUNKEL}; color: #e8eaf2; font: 14px 'Segoe UI'; }}
QLabel {{ background: transparent; }}
QFrame#karte, QFrame#debug {{ background: {KARTE}; border: 1px solid #2a2f3d; border-radius: 14px; }}
QFrame#debug {{ border: 1px solid {AKZENT}; }}
QPushButton {{ background: {AKZENT}; color: white; border: 0; border-radius: 15px; padding: 7px 14px; }}
QPushButton:disabled {{ background: #3a3550; color: #9aa1b5; }}
QPushButton#leise {{ background: transparent; border: 1px solid #2a2f3d; color: #e8eaf2; }}
QPushButton#kaefer {{ background: {KARTE}; border: 1px solid {AKZENT}; border-radius: 23px; font-size: 22px; padding: 0; }}
QPlainTextEdit {{ background: {DUNKEL}; border: 1px solid #2a2f3d; border-radius: 8px; padding: 4px; }}
QLabel#leise {{ color: #9aa1b5; font-size: 12px; }}
QLabel#titel {{ font-size: 16px; font-weight: 600; }}
"""
_FOKUSBAR = {"input", "select", "textarea", "button"}


# ---------------------------------------------------------------------------
# Leitung zur Sitzung
# ---------------------------------------------------------------------------

class Leitung(QObject):
    ereignis = Signal(object)
    getrennt = Signal()

    def __init__(self, adresse: str, schluessel: bytes):
        super().__init__()
        from multiprocessing.connection import Client
        self.conn = Client(adresse, family="AF_PIPE", authkey=schluessel)
        self._lock = threading.Lock()
        threading.Thread(target=self._lesen, name="nemiview-lesen", daemon=True).start()

    def _lesen(self):
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

    def senden(self, auftrag: dict):
        daten = json.dumps(auftrag, ensure_ascii=False).encode("utf-8")
        with self._lock:
            try:
                self.conn.send_bytes(daten)
            except (OSError, ValueError):
                self.getrennt.emit()


def stand(ordner: Path) -> tuple:
    """Kennung des Dateistands einer App – ändert sie sich, lädt die Ansicht neu."""
    teile = []
    try:
        for p in sorted(ordner.rglob("*")):
            if p.is_file():
                st = p.stat()
                teile.append((p.name, st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    return tuple(teile)


# ---------------------------------------------------------------------------
# App-Ansicht
# ---------------------------------------------------------------------------

class AppAnsicht(QWidget):
    gezeigt = Signal(object)                       # Zeige-Modus: gewähltes Element

    def __init__(self, ordner: Path, speicher: Path | None, nur_ansehen: bool = False, parent=None):
        super().__init__(parent)
        self.ordner, self.speicher, self.nur_ansehen = Path(ordner), speicher, nur_ansehen
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.messer = QtMesser()
        self.lz: AppLaufzeit | None = None
        self.layout_: Layout | None = None
        self.scroll = 0.0
        self.zeige_modus = False
        self.markiert = None
        self._bilder: dict = {}
        self._zieht = None
        self._groesse = None
        self.fehler_text = ""
        self.laden()
        self.uhr = QTimer(self)
        self.uhr.timeout.connect(self._takt)
        self.uhr.start(16)

    # -- Laden ---------------------------------------------------------------------
    def laden(self):
        self._bilder.clear()
        self.layout_ = None
        self.scroll = 0.0
        self.fehler_text = ""
        try:
            self.lz = AppLaufzeit(self.ordner, max(320, self.width()), max(240, self.height()),
                                  speicher_ordner=self.speicher, gastgeber=self)
            self.lz.laden()
        except OSError as e:
            self.lz = None
            self.fehler_text = f"Die App lässt sich nicht laden: {e}"
        self.update()

    def neu_laden(self):
        QTimer.singleShot(0, self.laden)

    def dialog(self, art, text, vorgabe):
        if self.nur_ansehen:
            return {"alert": None, "confirm": False, "prompt": None}[art]
        if art == "alert":
            QMessageBox.information(self, self.titel(), text)
            from .nemijs import UNDEF
            return UNDEF
        if art == "confirm":
            return QMessageBox.question(self, self.titel(), text) == QMessageBox.StandardButton.Yes
        wert, ok = QInputDialog.getText(self, self.titel(), text, text=vorgabe)
        return wert if ok else None

    def titel(self) -> str:
        return (self.lz.dok.titel if self.lz and self.lz.dok else "") or self.ordner.name

    def _bild(self, el):
        src = el.attrs.get("src", "")
        if src not in self._bilder:
            daten = self.lz.dok.datei(src) if self.lz else None
            self._bilder[src] = bild_laden(daten, src) if daten else None
        return self._bilder[src]

    def _bild_groesse(self, el):
        b = self._bild(el)
        if isinstance(b, QImage):
            return (b.width(), b.height())
        if b is not None and hasattr(b, "defaultSize"):
            s = b.defaultSize()
            return (s.width(), s.height())
        return None

    # -- Zeichnen --------------------------------------------------------------------
    def _rechnen(self):
        lz = self.lz
        if lz is None:
            return
        groesse = (self.width(), self.height())
        if groesse != self._groesse:
            self._groesse = groesse
            lz.groesse(*groesse)
        if self.layout_ is None or lz.dok.geaendert:
            L = Layout(lz.dok, lz.kaskade, self.width(), self.height(), self.messer)
            L.bild_groesse = self._bild_groesse
            try:
                L.rechnen()
            except RecursionError:
                lz.melden("❌ Layout zu tief verschachtelt")
            self.layout_ = L
            self.scroll = max(0.0, min(self.scroll, L.dok_hoehe - self.height()))
            self.window().setWindowTitle(self.titel())

    def paintEvent(self, _ev):
        p = QPainter(self)
        if self.lz is None:
            p.fillRect(self.rect(), QColor(DUNKEL))
            p.setPen(QColor("#e8eaf2"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.fehler_text)
            p.end()
            return
        self._rechnen()
        maler = Maler(self.layout_, self._bild)
        maler.fokus = self.lz.dok.fokus
        maler.malen(p, self.scroll, self.width(), self.height())
        if self.zeige_modus or self.markiert is not None:
            el = self.markiert if self.markiert is not None else self.lz.dok.ueber
            if el is not None and el.box:
                x, y, w, h = el.box
                p.setPen(QPen(QColor("#ffd400"), 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(x - 2, y - 2 - self.scroll, w + 4, h + 4), 4, 4)
        p.end()

    def _takt(self):
        if self.lz is None:
            return
        self.lz.zeitgeber()
        if self.lz.dok.geaendert:
            self.update()

    # -- Maus --------------------------------------------------------------------------
    def _treffer(self, pos):
        if self.layout_ is None:
            return None
        return self.layout_.treffer(pos.x(), pos.y() + self.scroll)

    def mouseMoveEvent(self, ev):
        if self.lz is None:
            return
        el = self._treffer(ev.position())
        if self._zieht is not None:
            self._schieber(self._zieht, ev.position().x())
            return
        if el is not self.lz.dok.ueber:
            self.lz.dok.ueber = el
            if not self.zeige_modus:
                self.lz.dok.aendern()
            self.update()
        cursor = Qt.CursorShape.ArrowCursor
        if self.zeige_modus:
            cursor = Qt.CursorShape.CrossCursor
        elif el is not None:
            c = el.stil.get("cursor", "auto")
            if c == "pointer":
                cursor = Qt.CursorShape.PointingHandCursor
            elif el.tag in ("input", "textarea") and el.attrs.get("type", "text") in ("text", "number", "search",
                                                                                    "email", "password", "url"):
                cursor = Qt.CursorShape.IBeamCursor
        self.setCursor(QCursor(cursor))

    def leaveEvent(self, _ev):
        if self.lz is not None and self.lz.dok.ueber is not None:
            self.lz.dok.ueber = None
            self.lz.dok.aendern()
            self.update()

    def mousePressEvent(self, ev):
        if self.lz is None or ev.button() != Qt.MouseButton.LeftButton:
            return
        el = self._treffer(ev.position())
        if self.zeige_modus:
            if el is not None:
                self.markiert = el
                self.update()
                self.gezeigt.emit(el)
            return
        if self.nur_ansehen:
            return
        self.setFocus()
        self.lz.dok.gedrueckt = el
        self.lz.dok.aendern()
        if el is not None and el.tag == "input" and el.attrs.get("type") == "range" and "disabled" not in el.attrs:
            self._zieht = el
            self._schieber(el, ev.position().x())
        self.update()

    def mouseReleaseEvent(self, ev):
        if self.lz is None or self.zeige_modus or self.nur_ansehen or ev.button() != Qt.MouseButton.LeftButton:
            return
        el = self._treffer(ev.position())
        gedrueckt, self.lz.dok.gedrueckt = self.lz.dok.gedrueckt, None
        if self._zieht is not None:
            ziel, self._zieht = self._zieht, None
            self.lz.ausloesen(ziel, "change")
            self.lz.dok.aendern()
            self.update()
            return
        if el is None or el is not gedrueckt:
            self.lz.dok.aendern()
            self.update()
            return
        fokus = el
        while fokus is not None and not (getattr(fokus, "tag", None) in _FOKUSBAR or "tabindex" in getattr(fokus, "attrs", {})):
            fokus = fokus.eltern_element()
        self.lz.sicher(self.lz.dok.fokus_setzen, fokus)
        self.lz.sicher(self.lz.dok.klick, el)
        if el.tag == "input" and el.attrs.get("type") == "color" and "disabled" not in el.attrs:
            f = QColorDialog.getColor(QColor(el.wert or "#000000"), self, "Farbe wählen")
            if f.isValid():
                el.wert = f.name()
                self.lz.ausloesen(el, "input")
                self.lz.ausloesen(el, "change")
        elif el.tag == "select" and "disabled" not in el.attrs:
            self._auswahl(el)
        self.lz.dok.aendern()
        self.update()

    def _auswahl(self, el):
        menue = QMenu(self)
        optionen = [o for o in el.alle() if o.tag == "option"]
        for o in optionen:
            a = menue.addAction(o.text_inhalt().strip())
            a.setData(optionen.index(o))
        x, y, w, h = el.box
        gewaehlt = menue.exec(self.mapToGlobal(QPointF(x, y + h - self.scroll).toPoint()))
        if gewaehlt is not None:
            for i, o in enumerate(optionen):
                if i == gewaehlt.data():
                    o.attrs["selected"] = ""
                else:
                    o.attrs.pop("selected", None)
            self.lz.ausloesen(el, "input")
            self.lz.ausloesen(el, "change")

    def _schieber(self, el, x_maus):
        x, _y, w, _h = el.box
        try:
            mn, mx = float(el.attrs.get("min", "0") or 0), float(el.attrs.get("max", "100") or 100)
            schritt = float(el.attrs.get("step", "1") or 1)
        except ValueError:
            mn, mx, schritt = 0.0, 100.0, 1.0
        anteil = max(0.0, min(1.0, (x_maus - x - 8) / max(1.0, w - 16)))
        wert = mn + round((mx - mn) * anteil / schritt) * schritt if schritt > 0 else mn + (mx - mn) * anteil
        text = f"{wert:.10g}"
        if text != el.wert:
            el.wert = text
            self.lz.ausloesen(el, "input")
            self.lz.dok.aendern()
            self.update()

    def wheelEvent(self, ev):
        if self.layout_ is None:
            return
        self.scroll = max(0.0, min(self.scroll - ev.angleDelta().y() / 2, self.layout_.dok_hoehe - self.height()))
        self.update()

    # -- Tasten -------------------------------------------------------------------------
    def keyPressEvent(self, ev):
        if self.lz is None or self.nur_ansehen:
            return
        dok = self.lz.dok
        ziel = dok.fokus or dok.finden("body")
        taste = _tastenname(ev)
        if not self.lz.ausloesen(ziel, "keydown", key=taste, code=taste, ctrlKey=bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier),
                                 shiftKey=bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier),
                                 altKey=bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)):
            self.update()
            return
        el = dok.fokus
        if el is None:
            self.update()
            return
        if taste == "Tab":
            self._fokus_weiter(el, rueckwaerts=bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        elif el.tag in ("input", "textarea") and el.attrs.get("type", "text") not in ("checkbox", "radio", "range",
                                                                                      "color", "button", "submit"):
            self._tippen(el, ev, taste)
        elif taste in ("Enter", " ") and el.tag in ("button",) or (el.tag == "input" and el.attrs.get("type") in
                                                                    ("checkbox", "radio", "button", "submit") and taste == " "):
            self.lz.sicher(dok.klick, el)
        dok.aendern()
        self.update()

    def _tippen(self, el, ev, taste):
        if "readonly" in el.attrs or "disabled" in el.attrs:
            return
        alt = el.wert
        if taste == "Backspace":
            el.wert = el.wert[:-1]
        elif taste == "Enter":
            if el.tag == "textarea":
                el.wert += "\n"
            else:
                if getattr(el, "_geaendert", False):
                    el._geaendert = False
                    self.lz.ausloesen(el, "change")
                form = el.eltern
                while form is not None and getattr(form, "tag", None) != "form":
                    form = form.eltern
                if form is not None:
                    self.lz.ausloesen(form, "submit")
                return
        elif ev.matches(ev.StandardKey.Paste) if hasattr(ev, "StandardKey") else False:
            el.wert += QApplication.clipboard().text()
        elif ev.text() and ev.text().isprintable():
            t = ev.text()
            if el.attrs.get("type") == "number" and not (t.isdigit() or t in "-.,eE"):
                return
            laenge = int(el.attrs.get("maxlength", "0") or 0)
            if laenge and len(el.wert) >= laenge:
                return
            el.wert += t.replace(",", ".") if el.attrs.get("type") == "number" else t
        if el.wert != alt:
            el._geaendert = True
            self.lz.ausloesen(el, "input", inputType="insertText")

    def _fokus_weiter(self, el, rueckwaerts=False):
        alle = [e for e in self.lz.dok.alle() if (e.tag in _FOKUSBAR or "tabindex" in e.attrs) and e.box
                and "disabled" not in e.attrs]
        if not alle:
            return
        i = alle.index(el) if el in alle else -1
        neu = alle[(i - 1 if rueckwaerts else i + 1) % len(alle)]
        self.lz.sicher(self.lz.dok.fokus_setzen, neu)

    def focusNextPrevChild(self, _next):
        return False                                    # Tab bleibt in der App

    # -- debugAI-Hilfen --------------------------------------------------------------------
    def element_info(self, el) -> dict:
        from .nemicss import passt
        x, y, w, h = el.box or (0, 0, 0, 0)
        css = {k: el.stil.get(k, "") for k in ("color", "background-color", "font-size", "font-weight", "width",
                                               "height", "margin-top", "padding-top", "display", "position",
                                               "border-top-left-radius", "text-align") if el.stil.get(k)}
        return {"tag": el.tag, "selektor": _selektor(el), "text": el.text_inhalt().strip()[:120],
                "rect": {"x": x, "y": y, "breite": w, "hoehe": h}, "css": css, "_passt": bool(passt)}


def _selektor(el) -> str:
    if el.attrs.get("id"):
        return "#" + el.attrs["id"]
    teile = []
    e = el
    while e is not None and e.tag not in ("body", "html") and len(teile) < 5:
        t = e.tag + "".join("." + k for k in e.klassen())
        eltern = e.eltern_element()
        if eltern is not None:
            gleiche = [k for k in eltern.elemente() if k.tag == e.tag]
            if len(gleiche) > 1:
                t += f":nth-of-type({gleiche.index(e) + 1})"
        teile.insert(0, t)
        if e.attrs.get("id"):
            teile[0] = "#" + e.attrs["id"]
            break
        e = eltern
    return " > ".join(teile)


def _tastenname(ev) -> str:
    namen = {Qt.Key.Key_Return: "Enter", Qt.Key.Key_Enter: "Enter", Qt.Key.Key_Backspace: "Backspace",
             Qt.Key.Key_Tab: "Tab", Qt.Key.Key_Escape: "Escape", Qt.Key.Key_Left: "ArrowLeft",
             Qt.Key.Key_Right: "ArrowRight", Qt.Key.Key_Up: "ArrowUp", Qt.Key.Key_Down: "ArrowDown",
             Qt.Key.Key_Delete: "Delete", Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End", Qt.Key.Key_Space: " "}
    return namen.get(ev.key(), ev.text() or "")


# ---------------------------------------------------------------------------
# debugAI
# ---------------------------------------------------------------------------

class DebugBereich(QFrame):
    def __init__(self, fenster: "AppFenster"):
        super().__init__(fenster)
        self.setObjectName("debug")
        self.f = fenster
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        kopf = QHBoxLayout()
        t = QLabel("🐞 debugAI")
        t.setObjectName("titel")
        zu = QPushButton("✕")
        zu.setObjectName("leise")
        zu.setFixedWidth(34)
        zu.clicked.connect(self.f.debug_zu)
        kopf.addWidget(t)
        kopf.addStretch()
        kopf.addWidget(zu)
        lay.addLayout(kopf)
        self.info = QLabel()
        self.info.setObjectName("leise")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.entscheid = QFrame()
        el = QVBoxLayout(self.entscheid)
        self.entscheid.setStyleSheet("QFrame { border: 1px solid #ffd400; border-radius: 10px; }")
        self.etext = QLabel()
        self.etext.setWordWrap(True)
        self.etext.setStyleSheet("border: 0;")
        el.addWidget(self.etext)
        vgl = QPushButton("⇆ Vorher → nachher")
        vgl.clicked.connect(self.f.vergleichen)
        el.addWidget(vgl)
        reihe = QHBoxLayout()
        behalten = QPushButton("✓ Behalten")
        behalten.clicked.connect(lambda: self.f.entscheiden("behalten"))
        zurueck = QPushButton("↶ Zurück")
        zurueck.setObjectName("leise")
        zurueck.clicked.connect(lambda: self.f.entscheiden("zurueck"))
        reihe.addWidget(behalten)
        reihe.addWidget(zurueck)
        el.addLayout(reihe)
        lay.addWidget(self.entscheid)
        self.frage = QLabel("Was stört dich, was soll geändert werden?")
        self.frage.setStyleSheet("font-weight: 600;")
        self.frage.setWordWrap(True)
        lay.addWidget(self.frage)
        self.wunsch = QPlainTextEdit()
        self.wunsch.setPlaceholderText("z. B. „der Knopf soll gelb werden“ oder „das muss bisschen höher“")
        self.wunsch.setFixedHeight(70)
        lay.addWidget(self.wunsch)
        self.weiter = QPushButton("Weiter – Stelle zeigen")
        self.weiter.clicked.connect(self.f.zeigen_starten)
        lay.addWidget(self.weiter)
        self.gewaehlt = QLabel()
        self.gewaehlt.setObjectName("leise")
        self.gewaehlt.setWordWrap(True)
        lay.addWidget(self.gewaehlt)
        reihe2 = QHBoxLayout()
        self.senden = QPushButton("An die KI schicken")
        self.senden.clicked.connect(lambda: self.f.schicken(True))
        self.ohne = QPushButton("Ohne Stelle")
        self.ohne.setObjectName("leise")
        self.ohne.clicked.connect(lambda: self.f.schicken(False))
        reihe2.addWidget(self.senden)
        reihe2.addWidget(self.ohne)
        lay.addLayout(reihe2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)
        self.fragen_bereich = QVBoxLayout()
        lay.addLayout(self.fragen_bereich)
        self.schritt(1)

    def schritt(self, n):
        zeigen = n == 2
        self.weiter.setVisible(n == 1)
        self.wunsch.setEnabled(n in (1, 2))
        self.gewaehlt.setVisible(zeigen)
        self.senden.setVisible(zeigen)
        self.ohne.setVisible(zeigen)
        self.senden.setEnabled(False)
        if zeigen:
            self.gewaehlt.setText("👆 Tippe jetzt in der App auf die Stelle.")

    def zeile(self, text):
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()


class AppFenster(QWidget):
    def __init__(self, apps: Path, name: str, speicher: Path, leitung: Leitung | None, debug_offen=False):
        super().__init__()
        self.apps, self.name, self.leitung = Path(apps), name, leitung
        self.ordner = self.apps / name
        self.resize(1000, 720)
        self.setWindowIcon(_icon(self.ordner))
        self.ansicht = AppAnsicht(self.ordner, speicher, parent=self)
        self.ansicht.gezeigt.connect(self._gezeigt)
        self.kaefer = QPushButton("🐞", self)
        self.kaefer.setObjectName("kaefer")
        self.kaefer.setFixedSize(46, 46)
        self.kaefer.setToolTip("debugAI: etwas an dieser App ändern")
        self.kaefer.clicked.connect(self.debug_auf)
        self.kaefer.setVisible(leitung is not None)
        self.debug = DebugBereich(self)
        self.debug.hide()
        self.element = None
        self.laeuft = False
        self._stand = stand(self.ordner)
        self.wache = QTimer(self)
        self.wache.timeout.connect(self._dateien_pruefen)
        self.wache.start(1500)
        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)
        if debug_offen or self._sicherung() is not None:
            QTimer.singleShot(50, self.debug_auf)

    def resizeEvent(self, ev):
        w, h = self.width(), self.height()
        self.ansicht.setGeometry(0, 0, w, h)
        self.kaefer.move(w - 60, h - 60)
        self.debug.setGeometry(w - 352, 12, 340, h - 24)
        super().resizeEvent(ev)

    # -- Sicherung (Apps/_vorher/<app>) ----------------------------------------------
    def _sicherung(self):
        ordner = self.apps / "_vorher" / self.name
        if not ordner.is_dir():
            return None
        try:
            meta = json.loads((self.apps / "_vorher" / f"{self.name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        return meta.get("wuensche", []) if isinstance(meta, dict) else []

    def _info_text(self):
        try:
            roh = json.loads((self.ordner / "app.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            roh = {}
        dateien = [p.relative_to(self.ordner).as_posix() for p in sorted(self.ordner.rglob("*")) if p.is_file()]
        return (f"{roh.get('titel') or self.name} · Version {roh.get('version') or '1.0'} · ID {self.name}\n"
                f"{len(dateien)} Datei(en): {', '.join(dateien[:12])}")

    def debug_auf(self):
        self.debug.info.setText(self._info_text())
        wuensche = self._sicherung()
        self.debug.entscheid.setVisible(wuensche is not None)
        if wuensche is not None:
            self.debug.etext.setText("Geändert: " + (" · ".join(wuensche) or "–") + "\nSo behalten?")
        self.debug.show()
        self.debug.raise_()
        self.kaefer.hide()
        self.debug.wunsch.setFocus()

    def debug_zu(self):
        self.debug.hide()
        self.kaefer.setVisible(self.leitung is not None)
        self.ansicht.zeige_modus = False
        self.ansicht.markiert = None
        self.ansicht.update()

    def zeigen_starten(self):
        if not self.debug.wunsch.toPlainText().strip():
            self.debug.wunsch.setFocus()
            return
        self.debug.schritt(2)
        self.element = None
        self.ansicht.markiert = None
        self.ansicht.zeige_modus = True
        self.ansicht.update()

    def _gezeigt(self, el):
        self.element = el
        self.debug.gewaehlt.setText(f"Gewählt: <{el.tag}> {el.text_inhalt().strip()[:60]}")
        self.debug.senden.setEnabled(True)

    def schicken(self, mit_stelle: bool):
        wunsch = self.debug.wunsch.toPlainText().strip()
        if not wunsch or self.laeuft or self.leitung is None:
            return
        info = self.ansicht.element_info(self.element) if (mit_stelle and self.element is not None) else {}
        info.pop("_passt", None)
        self.laeuft = True
        self.ansicht.zeige_modus = False
        self.debug.schritt(0)
        self.debug.log.clear()
        self.debug.zeile(f"🐞 {wunsch}\n")
        self.leitung.senden({"op": "zeigen", "app": self.name, "wunsch": wunsch, "element": info})

    def _ereignis(self, ev):
        if ev.get("app") not in (None, self.name):
            return
        t = ev.get("t")
        d = self.debug
        if t == "answer" and ev.get("delta"):
            d.zeile(ev["delta"])
        elif t == "action" and ev.get("desc"):
            d.zeile(f"\n🔧 {ev['desc']}\n")
        elif t == "action_result":
            d.zeile("✗ nicht geklappt\n" if ev.get("ok") is False else "✓\n" if ev.get("ok") else "")
        elif t in ("error", "warn") and ev.get("text"):
            d.zeile(f"\n⚠ {ev['text']}\n")
        elif t == "confirm" and self.laeuft:              # Rückfragen gehören dem laufenden Auftrag
            self._rueckfrage(ev)
        elif t == "debug":
            self.laeuft = False
            self.ansicht.markiert = None
            d.wunsch.clear()
            d.schritt(1)
            if ev.get("geaendert"):
                d.zeile("\n↻ Neue Fassung geladen.\n")
                self._stand = stand(self.ordner)
                self.ansicht.laden()
            else:
                d.zeile("\nAn den Dateien hat sich nichts geändert.\n")
            self.debug_auf()

    def _rueckfrage(self, ev):
        box = QFrame()
        lay = QVBoxLayout(box)
        frage = QLabel("❓ " + str(ev.get("question", "")))
        frage.setWordWrap(True)
        lay.addWidget(frage)
        reihe = QHBoxLayout()
        for wert, text in ev.get("options", []):
            k = QPushButton(str(text))
            k.clicked.connect(lambda _=False, w=wert, b=box, t=text: (self.leitung.senden(
                {"op": "antwort", "id": ev.get("id"), "wahl": w}), self.debug.zeile(f"→ {t}\n"), b.deleteLater()))
            reihe.addWidget(k)
        lay.addLayout(reihe)
        self.debug.fragen_bereich.addWidget(box)

    def entscheiden(self, was):
        if self.leitung is not None:
            self.leitung.senden({"op": was, "app": self.name})
        QTimer.singleShot(400, self._nach_entscheid)

    def _nach_entscheid(self):
        self._stand = stand(self.ordner)
        self.ansicht.laden()
        self.debug_auf()

    def vergleichen(self):
        vorher = self.apps / "_vorher" / self.name
        if not vorher.is_dir():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{self.ansicht.titel()} – vorher → nachher")
        dlg.resize(1200, 700)
        lay = QVBoxLayout(dlg)
        kopf = QHBoxLayout()
        for text in ("Vorher", "Nachher"):
            lab = QLabel(text)
            lab.setObjectName("titel")
            kopf.addWidget(lab, 1)
        behalten = QPushButton("✓ Behalten")
        behalten.clicked.connect(lambda: (self.entscheiden("behalten"), dlg.accept()))
        zurueck = QPushButton("↶ Zurück")
        zurueck.setObjectName("leise")
        zurueck.clicked.connect(lambda: (self.entscheiden("zurueck"), dlg.accept()))
        kopf.addWidget(behalten)
        kopf.addWidget(zurueck)
        lay.addLayout(kopf)
        reihe = QHBoxLayout()
        reihe.addWidget(AppAnsicht(vorher, None, nur_ansehen=True), 1)
        reihe.addWidget(AppAnsicht(self.ordner, None, nur_ansehen=True), 1)
        lay.addLayout(reihe, 1)
        dlg.exec()

    def _dateien_pruefen(self):
        if self.laeuft:
            return
        neu = stand(self.ordner)
        if neu != self._stand:                      # z. B. die KI im Terminal hat die App geändert
            self._stand = neu
            self.ansicht.laden()


def _icon(ordner: Path) -> QIcon:
    for name in ("icon.svg", "icon.png"):
        p = ordner / name
        if p.is_file():
            b = bild_laden(p.read_bytes(), name)
            if isinstance(b, QImage):
                return QIcon(QPixmap.fromImage(b))
            if b is not None:
                bild = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
                bild.fill(Qt.GlobalColor.transparent)
                q = QPainter(bild)
                b.render(q)
                q.end()
                return QIcon(QPixmap.fromImage(bild))
    return QIcon()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

NEU_APP = "__neu__"            # Kennung der Ereignisse, wenn die KI eine neue App baut
KACHEL_B, KACHEL_H, ICON = 112, 124, 64


class Kachel(QWidget):
    """Kleine Kachel: Icon und Name. Unter der Maus hebt sich das Icon an, ein Klick öffnet, Rechtsklick zeigt mehr."""
    geklickt = Signal()
    menue = Signal(object)

    def __init__(self, titel: str, bild: QPixmap | None, beschreibung: str = "", plus: bool = False):
        super().__init__()
        self.titel, self.bild, self.plus = titel, bild, plus
        self.setFixedSize(KACHEL_B, KACHEL_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if beschreibung:
            self.setToolTip(beschreibung)
        self.wert = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(170)
        self.anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self.anim.valueChanged.connect(self._schritt)

    def _schritt(self, w):
        self.wert = float(w)
        self.update()

    def _hin(self, ziel: float):
        self.anim.stop()
        self.anim.setStartValue(self.wert)
        self.anim.setEndValue(ziel)
        self.anim.start()

    def enterEvent(self, _ev):
        self._hin(1.0)

    def leaveEvent(self, _ev):
        self._hin(0.0)

    def mouseReleaseEvent(self, ev):
        if not self.rect().contains(ev.position().toPoint()):
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            self.geklickt.emit()
        elif ev.button() == Qt.MouseButton.RightButton:
            self.menue.emit(ev.globalPosition().toPoint())

    def paintEvent(self, _ev):
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = max(0.0, min(1.2, self.wert))
        if w > 0.01:                                       # leiser Hintergrund unter der Maus
            hg = QColor(KARTE)
            hg.setAlpha(int(255 * min(1.0, w)))
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(hg)
            q.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 16, 16)
        groesse = ICON * (1.0 + 0.12 * w)
        mitte_x, mitte_y = KACHEL_B / 2, 14 + ICON / 2 - 5 * w
        ziel = QRectF(mitte_x - groesse / 2, mitte_y - groesse / 2, groesse, groesse)
        if self.plus:
            q.setPen(QPen(QColor(AKZENT), 2, Qt.PenStyle.DashLine))
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawRoundedRect(ziel.adjusted(2, 2, -2, -2), 18, 18)
            q.setPen(QPen(QColor(AKZENT), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            m, r = ziel.center(), groesse * 0.2
            q.drawLine(QPointF(m.x() - r, m.y()), QPointF(m.x() + r, m.y()))
            q.drawLine(QPointF(m.x(), m.y() - r), QPointF(m.x(), m.y() + r))
        elif self.bild is not None and not self.bild.isNull():
            q.drawPixmap(ziel, self.bild, QRectF(self.bild.rect()))
        else:                                              # ohne Icon: Anfangsbuchstabe auf Farbe
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(AKZENT))
            q.drawRoundedRect(ziel, 18, 18)
            q.setPen(QColor("white"))
            f = QFont("Segoe UI")
            f.setPixelSize(int(groesse * 0.45))
            f.setBold(True)
            q.setFont(f)
            q.drawText(ziel, Qt.AlignmentFlag.AlignCenter, (self.titel or "?")[:1].upper())
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        q.setFont(f)
        q.setPen(QColor("#e8eaf2"))
        text = QRectF(6, 14 + ICON + 10, KACHEL_B - 12, KACHEL_H - ICON - 26)
        zeilen = self.zeilen(q.fontMetrics(), self.titel, int(text.width()))
        q.drawText(text, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "\n".join(zeilen))
        q.end()

    @staticmethod
    def zeilen(fm, text: str, breite: int) -> list[str]:
        """Höchstens zwei Zeilen; was nicht passt, endet mit …"""
        zeilen, jetzt = [], ""
        for wort in text.split():
            probe = f"{jetzt} {wort}".strip()
            if fm.horizontalAdvance(probe) <= breite or not jetzt:
                jetzt = probe
            else:
                zeilen.append(jetzt)
                jetzt = wort
        if jetzt:
            zeilen.append(jetzt)
        if len(zeilen) > 2:
            zeilen = [zeilen[0], " ".join(zeilen[1:])]
        return [fm.elidedText(z, Qt.TextElideMode.ElideRight, breite) for z in zeilen]


class StoreFenster(QWidget):
    def __init__(self, apps: Path, speicher: Path, leitung: Leitung | None):
        super().__init__()
        self.apps, self.speicher, self.leitung = Path(apps), speicher, leitung
        self.offen: list = []
        self.baut = False
        self.setWindowTitle("NemiCLI Appstore")
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        t = QLabel("🛍 NemiCLI Appstore")
        t.setStyleSheet("font-size: 22px; font-weight: 700;")
        lay.addWidget(t)
        unter = QLabel("Klick öffnet eine App · Rechtsklick: 🐞 debugAI · eigene Engine, ohne Internet")
        unter.setObjectName("leise")
        lay.addWidget(unter)
        rahmen = QScrollArea()
        rahmen.setWidgetResizable(True)
        rahmen.setFrameShape(QFrame.Shape.NoFrame)
        innen = QWidget()
        self.raster = QGridLayout(innen)
        self.raster.setSpacing(10)
        self.raster.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        rahmen.setWidget(innen)
        lay.addWidget(rahmen, 1)
        self.bau = self._bau_bereich()
        self.bau.hide()
        lay.addWidget(self.bau)
        self.fuellen()
        self._liste = self._app_namen()
        self.wache = QTimer(self)                     # baut die KI eine App, erscheint sie ohne Neu-Öffnen
        self.wache.timeout.connect(self._neue_pruefen)
        self.wache.start(2000)
        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)

    # -- Kacheln -----------------------------------------------------------
    def _app_ordner(self) -> list[Path]:
        try:
            return [p for p in sorted(self.apps.iterdir()) if p.is_dir() and not p.name.startswith(("_", "."))
                    and (p / "index.html").is_file()]
        except OSError:
            return []

    def _app_namen(self) -> tuple:
        try:
            return tuple((p.name, (p / "app.json").stat().st_mtime_ns if (p / "app.json").is_file() else 0)
                         for p in self._app_ordner())
        except OSError:
            return ()

    def _neue_pruefen(self):
        neu = self._app_namen()
        if neu != self._liste:
            self._liste = neu
            self.fuellen()

    def _spalten(self) -> int:
        return max(1, (self.width() - 40) // (KACHEL_B + 10))

    def fuellen(self):
        while self.raster.count():
            w = self.raster.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        kacheln = [self._kachel(o) for o in self._app_ordner()]
        if self.leitung is not None:
            plus = Kachel("Neue App", None, "Beschreib kurz eine App – die KI baut sie.", plus=True)
            plus.geklickt.connect(self.neue_app)
            kacheln.append(plus)
        if not kacheln:
            leer = QLabel(f"Noch keine Apps in {self.apps}.")
            leer.setObjectName("leise")
            self.raster.addWidget(leer, 0, 0)
            return
        spalten = self._spalten()
        for i, k in enumerate(kacheln):
            self.raster.addWidget(k, i // spalten, i % spalten)

    def _kachel(self, ordner: Path) -> Kachel:
        try:
            roh = json.loads((ordner / "app.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            roh = {}
        if not isinstance(roh, dict):
            roh = {}
        ic = _icon(ordner)
        bild = ic.pixmap(ICON * 2, ICON * 2) if not ic.isNull() else None
        k = Kachel(str(roh.get("titel") or ordner.name)[:60], bild, str(roh.get("beschreibung") or "")[:200])
        k.name = ordner.name
        k.geklickt.connect(lambda n=ordner.name: self.oeffnen(n))
        k.menue.connect(lambda pos, n=ordner.name: self._menue(n, pos))
        return k

    def _menue(self, name: str, pos):
        m = QMenu(self)
        m.addAction("Öffnen", lambda: self.oeffnen(name))
        if self.leitung is not None:
            m.addAction("🐞 debugAI", lambda: self.oeffnen(name, debug=True))
            m.addSeparator()
            m.addAction("🗑 Entfernen", lambda: self.entfernen(name))
        m.exec(pos)

    def entfernen(self, name: str):
        titel = next((k.titel for k in (self.raster.itemAt(i).widget() for i in range(self.raster.count()))
                      if isinstance(k, Kachel) and getattr(k, "name", None) == name), name)
        frage = QMessageBox.question(self, "App entfernen", f"„{titel}“ wirklich entfernen?\n\n"
                                     "Sie kommt in den Papierkorb von NemiCLI – /undo holt sie zurück.")
        if frage != QMessageBox.StandardButton.Yes:
            return
        for f in [f for f in self.offen if f.name == name]:
            f.close()
            self.offen.remove(f)
        self.leitung.senden({"op": "entfernen", "app": name})

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        spalten = self._spalten()
        if spalten != getattr(self, "_spalten_alt", None):       # nur neu ordnen, wenn sich die Spaltenzahl ändert
            self._spalten_alt = spalten
            self.fuellen()

    def oeffnen(self, name, debug=False):
        f = AppFenster(self.apps, name, self.speicher, self.leitung, debug_offen=debug)
        f.show()
        self.offen.append(f)

    # -- Neue App ----------------------------------------------------------
    def _bau_bereich(self) -> QFrame:
        box = QFrame()
        box.setObjectName("debug")
        lay = QVBoxLayout(box)
        self.bau_titel = QLabel("➕ Neue App")
        self.bau_titel.setObjectName("titel")
        lay.addWidget(self.bau_titel)
        self.bau_log = QPlainTextEdit()
        self.bau_log.setReadOnly(True)
        self.bau_log.setMaximumHeight(150)
        lay.addWidget(self.bau_log)
        self.bau_fragen = QVBoxLayout()
        lay.addLayout(self.bau_fragen)
        reihe = QHBoxLayout()
        reihe.addStretch(1)
        self.bau_zu = QPushButton("Schließen")
        self.bau_zu.setObjectName("leise")
        self.bau_zu.clicked.connect(box.hide)
        reihe.addWidget(self.bau_zu)
        lay.addLayout(reihe)
        return box

    def _zeile(self, text: str):
        self.bau_log.moveCursor(QTextCursor.MoveOperation.End)
        self.bau_log.insertPlainText(text)
        self.bau_log.ensureCursorVisible()

    def neue_app(self):
        if self.leitung is None or self.baut:
            return
        wunsch, ok = QInputDialog.getMultiLineText(self, "Neue App", "Was soll die App können? (kurz beschreiben)")
        wunsch = (wunsch or "").strip()
        if not ok or not wunsch:
            return
        self.baut = True
        self.bau_log.clear()
        self.bau_titel.setText("➕ Neue App wird gebaut …")
        self.bau_zu.setEnabled(False)
        self._zeile(f"✏ {wunsch}\n")
        self.bau.show()
        self.leitung.senden({"op": "neue_app", "wunsch": wunsch[:2000]})

    def _ereignis(self, ev):
        t = ev.get("t")
        if t == "entfernt":
            if not ev.get("ok"):
                QMessageBox.warning(self, "App entfernen", str(ev.get("text") or "Das ging nicht."))
            self._liste = self._app_namen()
            self.fuellen()
            return
        if not self.baut or (ev.get("app") != NEU_APP and t != "confirm"):     # Rückfragen tragen keine App
            return
        if t == "answer" and ev.get("delta"):
            self._zeile(ev["delta"])
        elif t == "action" and ev.get("desc"):
            self._zeile(f"\n🔧 {ev['desc']}\n")
        elif t == "action_result":
            self._zeile("✗ nicht geklappt\n" if ev.get("ok") is False else "✓\n" if ev.get("ok") else "")
        elif t in ("error", "warn") and ev.get("text"):
            self._zeile(f"\n⚠ {ev['text']}\n")
        elif t == "neu_fertig":
            self.baut = False
            self.bau_zu.setEnabled(True)
            neu = [n for n in ev.get("neu") or [] if isinstance(n, str)]
            self.bau_titel.setText("➕ Neue App fertig" if neu else "➕ Keine neue App entstanden")
            if neu:
                self._zeile("\n✓ Im Store: " + ", ".join(neu) + "\n")
            self._liste = self._app_namen()
            self.fuellen()
        elif t == "confirm":
            self._rueckfrage(ev)

    def _rueckfrage(self, ev):
        box = QFrame()
        lay = QVBoxLayout(box)
        frage = QLabel("❓ " + str(ev.get("question", "")))
        frage.setWordWrap(True)
        lay.addWidget(frage)
        reihe = QHBoxLayout()
        for wert, text in ev.get("options", []):
            k = QPushButton(str(text))
            k.clicked.connect(lambda _=False, w=wert, b=box, t=text: (self.leitung.senden(
                {"op": "antwort", "id": ev.get("id"), "wahl": w}), self._zeile(f"→ {t}\n"), b.deleteLater()))
            reihe.addWidget(k)
        reihe.addStretch(1)
        lay.addLayout(reihe)
        self.bau_fragen.addWidget(box)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def starten(argv: list[str]) -> int:
    """nemiview <Apps-Ordner> [--app NAME] [--debug] [--speicher ORDNER]"""
    args = list(argv)
    apps = Path(args[0]) if args and not args[0].startswith("--") else Path.cwd()
    name = args[args.index("--app") + 1] if "--app" in args else None
    speicher = Path(args[args.index("--speicher") + 1]) if "--speicher" in args else apps / "_daten"
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyleSheet(STIL)
    app.setFont(QFont("Segoe UI", 10))
    leitung = None
    adresse, schluessel = os.environ.get("NEMICLI_GUI_PIPE"), os.environ.get("NEMICLI_GUI_KEY")
    if adresse and schluessel:
        try:
            leitung = Leitung(adresse, bytes.fromhex(schluessel))
        except (OSError, ValueError, EOFError):
            leitung = None
    if name:
        fenster = AppFenster(apps, name, speicher, leitung, debug_offen="--debug" in args)
    else:
        fenster = StoreFenster(apps, speicher, leitung)
    fenster.show()
    return app.exec()
