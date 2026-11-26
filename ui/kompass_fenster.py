"""kompass_fenster.py – 🧭 Kompass: Begleitfenster mit allen Befehlen neben dem Terminal (`--kompass`).

Startet aus der Sitzung (core/kompass.py) und verbindet sich über gui_bruecke. Die Sitzung schickt bei jeder
Änderung der Eingabe den getippten Text: Der passende Befehl leuchtet oben auf, seine Unterpunkte stehen als
Knöpfe darunter. Ein Klick setzt den Befehl in die Eingabe – ausgeführt wird erst mit Enter im Terminal.
Das Fenster nimmt nie den Tastatur-Fokus, man tippt im Terminal einfach weiter.
"""
from __future__ import annotations

import os
import re

from gui_fenster import FARBE, _dunkler_rahmen, app_vorbereiten, label, leitung_oeffnen, schrift
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

SATZ_MAX = 2                  # so viele Sätze der Langhilfe zeigt eine Karte, der Rest über „Mehr lesen“
KARTEN_MAX = 12
_ABKUERZUNGEN = ("z. B.", "u. a.", "d. h.", "o. ä.", "z. T.", "bzw.", "ca.", "usw.", "etc.", "evtl.", "ggf.")


def saetze(text: str) -> list[str]:
    """Langhilfe in kurze Stücke teilen: an Satzende und Semikolon; „z. B.“ & Co. bleiben ganz."""
    text = re.sub(r"\s+", " ", text or "").strip()
    for i, a in enumerate(_ABKUERZUNGEN):
        text = text.replace(a, f"\x00{i}\x00")
    teile = re.split(r"(?<=[.!?;])\s+", text)
    return [re.sub(r"\x00(\d+)\x00", lambda m: _ABKUERZUNGEN[int(m.group(1))], t).strip() for t in teile if t.strip()]


class Befehle:
    """Befehle mit Thema und Unterpunkten; Suche nach dem, was im Terminal steht."""

    def __init__(self, daten: dict):
        self.liste = {b["name"]: b for b in daten.get("befehle", [])}
        self.unter = daten.get("untermenues", {})
        self.thema_von: dict[str, tuple[str, str]] = {}
        for t in daten.get("themen", []):
            for name in t["befehle"]:
                self.thema_von.setdefault(name, (t["symbol"], t["name"]))

    def suchen(self, eingabe: str) -> tuple[list[dict], str | None]:
        """(passende Befehle, bester Treffer): erst Anfang des Namens, dann im Namen, dann in der Kurzhilfe."""
        roh = eingabe.strip()
        wort = roh.split()[0].lower() if roh else "/"
        if wort in ("/", ""):
            return list(self.liste.values()), None
        suche = wort.lstrip("/")
        anfang = [b for n, b in self.liste.items() if n.lower().startswith(wort)]
        drin = [b for n, b in self.liste.items() if suche in n.lower() and b not in anfang]
        kurz = []
        if len(suche) >= 3:                          # Kurzhilfe nur ab 3 Buchstaben und am Wortanfang
            muster = re.compile(r"\b" + re.escape(suche), re.I)
            kurz = [b for b in self.liste.values()
                    if muster.search(b.get("kurz") or "") and b not in anfang and b not in drin]
        treffer = anfang + drin + kurz
        genau = self.liste.get(wort)
        bester = genau["name"] if genau else (treffer[0]["name"] if treffer else None)
        return treffer, bester


def _stil() -> str:
    return f"""
QWidget#kompass {{ background: {FARBE['grund']}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLabel {{ background: transparent; }}
QPushButton#unterpunkt {{ background: {FARBE['flaeche_hell']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 9px; padding: 9px 12px; text-align: left; }}
QPushButton#unterpunkt:hover {{ border-color: {FARBE['cyan']}; color: {FARBE['cyan']}; }}
QPushButton#leise {{ background: transparent; border: none; color: {FARBE['lila']}; padding: 2px 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {FARBE['rand']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


class Karte(QFrame):
    """Ein Befehl: Name, Kurzhilfe, wenige Sätze Erklärung, Unterpunkte als Knöpfe."""

    def __init__(self, befehl: dict, unter: list[dict], thema: tuple[str, str] | None, waehlen):
        super().__init__()
        self.befehl = befehl
        self.setObjectName("karte")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        kopf = QHBoxLayout()
        self.name = label(befehl["name"], 14, "text", fett=True, umbruch=False)
        kopf.addWidget(self.name)
        kopf.addStretch(1)
        if thema:
            kopf.addWidget(label(f"{thema[0]} {thema[1]}", 9.5, "dim", umbruch=False))
        lay.addLayout(kopf)
        lay.addWidget(label(befehl.get("kurz") or "", 11, "text"))
        self.saetze = saetze(befehl.get("lang") or "")
        self.erklaerung = QLabel()
        self.erklaerung.setWordWrap(True)
        self.erklaerung.setTextFormat(Qt.TextFormat.PlainText)
        self.erklaerung.setFont(schrift(10.5))
        self.erklaerung.setStyleSheet(f"color: {FARBE['dim']};")
        lay.addWidget(self.erklaerung)
        self.mehr = QPushButton("Mehr lesen ▾")
        self.mehr.setObjectName("leise")
        self.mehr.setFont(schrift(10))
        self.mehr.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mehr.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mehr.clicked.connect(self._mehr_umschalten)
        self.ganz = False
        lay.addWidget(self.mehr, 0, Qt.AlignmentFlag.AlignLeft)
        self.mehr.setVisible(len(self.saetze) > SATZ_MAX)
        self._erklaerung_setzen()
        raster = QGridLayout()
        raster.setSpacing(8)
        eintraege = unter or [{"zusatz": "", "anzeige": f"{befehl['name']} einsetzen", "frage": None}]
        spalten = 1 if max(len(u["anzeige"]) for u in eintraege) > 28 else 2      # lange Texte untereinander
        for i, u in enumerate(eintraege):
            knopf = QPushButton(u["anzeige"])
            knopf.setObjectName("unterpunkt")
            knopf.setFont(schrift(10.5))
            knopf.setCursor(Qt.CursorShape.PointingHandCursor)
            knopf.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            knopf.setToolTip(f"{befehl['name']} {u['zusatz']}".strip())
            knopf.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            knopf.setMinimumWidth(0)
            knopf.setMinimumHeight(knopf.sizeHint().height())   # sonst rechnet die Rollfläche die Knöpfe flach
            knopf.clicked.connect(lambda _=False, b=befehl, z=u: waehlen(b, z))
            raster.addWidget(knopf, i // spalten, i % spalten)
        lay.addLayout(raster)
        self.leuchten(False)

    def _erklaerung_setzen(self) -> None:
        teile = self.saetze if self.ganz else self.saetze[:SATZ_MAX]
        self.erklaerung.setText("\n".join(f"• {s}" for s in teile))
        self.erklaerung.setVisible(bool(teile))
        self.mehr.setText("Weniger ▴" if self.ganz else "Mehr lesen ▾")

    def _mehr_umschalten(self) -> None:
        self.ganz = not self.ganz
        self._erklaerung_setzen()

    def leuchten(self, an: bool) -> None:
        rand = FARBE["cyan"] if an else FARBE["rand"]
        grund = FARBE["flaeche_hell"] if an else FARBE["flaeche"]
        self.setStyleSheet(f"QFrame#karte {{ background: {grund}; border: 2px solid {rand}; border-radius: 12px; }}")
        self.name.setStyleSheet(f"color: {FARBE['cyan'] if an else FARBE['text']};")


class Kompass(QWidget):
    """Begleitfenster: nimmt keinen Fokus, bleibt oben, dockt neben das Fenster, in dem getippt wird."""

    def __init__(self, leitung=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("kompass")
        self.setWindowTitle("🧭 Kompass")
        self.setStyleSheet(_stil())
        self.resize(500, 660)
        self.leitung = leitung
        self.befehle = Befehle({})
        self.karten: dict[str, Karte] = {}
        self._zuletzt = None
        self._zu = False                   # vom Nutzer geschlossen: bis zur nächsten neuen „/“-Eingabe
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(label("🧭 Kompass", 17, "text", fett=True))
        self.hinweis = label("Tipp weiter – der passende Befehl leuchtet auf.", 10.5, "dim")
        lay.addWidget(self.hinweis)
        self.flaeche = QScrollArea()
        self.flaeche.setWidgetResizable(True)
        self.flaeche.setFrameShape(QFrame.Shape.NoFrame)
        self.flaeche.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.flaeche.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        innen = QWidget()
        self.liste = QVBoxLayout(innen)
        self.liste.setContentsMargins(0, 0, 6, 0)
        self.liste.setSpacing(10)
        self.liste.addStretch(1)
        self.flaeche.setWidget(innen)
        lay.addWidget(self.flaeche, 1)
        lay.addWidget(label("Klick setzt den Befehl ins Terminal – Enter führt ihn aus.", 9.5, "blass"))
        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)
            leitung.getrennt.connect(QApplication.quit)
            leitung.senden({"op": "laden"})

    # -- Sitzung ---------------------------------------------------------------------
    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "befehle":
            self.befehle = Befehle(ev)
            self._zuletzt = None
        elif t == "eingabe":
            text = str(ev.get("text") or "")
            if self._zu:
                return
            if not self.isVisible():
                self._andocken()
                self.show()
            self.zeigen_fuer(text)
        elif t == "verstecken":
            self._zu = False
            self.hide()

    def closeEvent(self, ev) -> None:
        ev.ignore()                        # der Prozess bleibt: das nächste „/“ öffnet sofort
        self._zu = True
        self.hide()

    # -- Anzeige ---------------------------------------------------------------------
    def zeigen_fuer(self, eingabe: str) -> None:
        treffer, bester = self.befehle.suchen(eingabe)
        schluessel = (tuple(b["name"] for b in treffer), bester)
        if schluessel == self._zuletzt:
            return
        self._zuletzt = schluessel
        while self.liste.count() > 1:                 # alte Karten weg (die Dehnung unten bleibt)
            w = self.liste.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.karten.clear()
        if bester:
            treffer = [self.befehle.liste[bester]] + [b for b in treffer if b["name"] != bester]
        for b in treffer[:KARTEN_MAX]:
            karte = Karte(b, self.befehle.unter.get(b["name"], []), self.befehle.thema_von.get(b["name"]),
                          self.waehlen)
            karte.leuchten(b["name"] == bester)
            self.liste.insertWidget(self.liste.count() - 1, karte)
            self.karten[b["name"]] = karte
        anzahl = len(treffer)
        if not treffer:
            self.hinweis.setText("Kein Befehl passt dazu. Lösch ein paar Buchstaben und versuch es anders.")
        elif bester:
            self.hinweis.setText(f"{bester} leuchtet auf · {anzahl} passende" if anzahl > 1 else f"{bester} leuchtet auf")
        else:
            self.hinweis.setText(f"Alle {anzahl} Befehle · tipp weiter, z. B. /wache")
        self.flaeche.verticalScrollBar().setValue(0)

    def waehlen(self, befehl: dict, unter: dict | None) -> None:
        """Klick: Befehl in die Eingabe. Braucht er noch etwas („{}“), bleibt ein Leerzeichen zum Weitertippen."""
        text = f"{befehl['name']} {(unter or {}).get('zusatz', '')}".strip()
        if "{}" in text:
            text = text.replace("{}", "").rstrip() + " "
            if unter and unter.get("frage"):
                self.hinweis.setText(f"↳ {unter['frage']}")
        if self.leitung is not None:
            self.leitung.senden({"op": "einsetzen", "text": text})

    # -- Platz neben dem Terminal ----------------------------------------------------
    def _andocken(self) -> None:
        """Rechts neben das Fenster im Vordergrund (dort wird getippt), sonst links; Höhe wie dieses Fenster."""
        r = _vordergrund_rechteck()
        bildschirm = QGuiApplication.screenAt(r.center()) if r is not None else None
        bildschirm = bildschirm or QGuiApplication.primaryScreen()
        frei = bildschirm.availableGeometry()
        if r is None:
            self.move(frei.right() - self.width() - 20, frei.top() + 40)
            return
        hoehe = max(420, min(r.height(), frei.height()))
        x = r.right() + 8 if r.right() + 8 + self.width() <= frei.right() else r.left() - 8 - self.width()
        x = max(frei.left(), min(x, frei.right() - self.width()))
        y = max(frei.top(), min(r.top(), frei.bottom() - hoehe))
        self.setGeometry(x, y, self.width(), hoehe)


def _vordergrund_rechteck() -> QRect | None:
    """Rechteck des Vordergrundfensters in Qt-Koordinaten (geräteunabhängige Pixel)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes as w
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        rect = w.RECT()
        if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        faktor = (QGuiApplication.primaryScreen().devicePixelRatio() or 1.0)
        return QRect(int(rect.left / faktor), int(rect.top / faktor),
                     int((rect.right - rect.left) / faktor), int((rect.bottom - rect.top) / faktor))
    except Exception:
        return None


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Kompass")
    leitung = leitung_oeffnen("/kompass an")
    if leitung is None:
        return 1
    app.setQuitOnLastWindowClosed(False)           # verstecken beendet nicht; die Sitzung trennt am Ende
    fenster = Kompass(leitung)
    _dunkler_rahmen(fenster)
    return app.exec()
