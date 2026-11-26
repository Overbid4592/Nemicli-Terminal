"""termin_fenster.py – Zeitplan für den Auto-Vollscan (/wache termin), eigener Prozess (`--termin`).

Wochentage anklicken, Uhrzeit wählen, „bei Systemstart“ dazu – „Speichern“ schreibt den Plan in den
Schlüsselordner (wache/termin.py), den die KI nicht erreicht. Keine KI dazwischen; die Wache liest den Plan.
"""
from __future__ import annotations

import time

from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label, schrift
from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import QHBoxLayout, QTimeEdit, QVBoxLayout, QWidget

from wache import termin as T

STIL = f"""
QWidget#termin {{ background: {FARBE['grund']}; }}
QTimeEdit {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px 10px; selection-background-color: {FARBE['lila']}; }}
QTimeEdit:focus {{ border: 1px solid {FARBE['cyan']}; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ width: 18px; }}
"""


class Schalter(Knopf):
    """Knopf zum An- und Abwählen: gewählt = farbig."""

    def __init__(self, text: str, symbol: str = "", *, klein: bool = False):
        super().__init__(text, symbol, klein=klein, mitte=True)
        self.clicked.connect(lambda: self.setzen(not self.akzent))
        self.bei_wechsel = None

    def setzen(self, an: bool) -> None:
        self.akzent = bool(an)
        self.update()
        if self.bei_wechsel:
            self.bei_wechsel()


class TerminFenster(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("termin")
        self.setWindowTitle("NemiCLI – Auto-Vollscan")
        self.setStyleSheet(STIL)
        self.resize(620, 540)
        self.setMinimumSize(560, 500)

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(12)
        v.addWidget(label("🛡 Auto-Vollscan", 17, "text", fett=True))
        v.addWidget(label("An welchen Tagen und um wie viel Uhr soll die Wache alle Laufwerke prüfen? "
                          "Den Plan stellst nur du ein – die KI kommt an ihn nicht heran.", 9.5, "dim"))

        v.addWidget(label("Tage", 10, "dim"))
        zeile = QHBoxLayout()
        zeile.setSpacing(6)
        self.tage = []
        for name in T.TAGE:
            k = Schalter(name)
            k.setMinimumWidth(56)
            k.bei_wechsel = self._anzeigen
            self.tage.append(k)
            zeile.addWidget(k)
        v.addLayout(zeile)

        schnell = QHBoxLayout()
        schnell.setSpacing(6)
        for text, auswahl in (("Jeden Tag", range(7)), ("Werktags", range(5)), ("Wochenende", (5, 6)),
                              ("Keinen", ())):
            k = Knopf(text, klein=True, mitte=True)
            k.clicked.connect(lambda _=False, a=tuple(auswahl): self._tage_setzen(a))
            schnell.addWidget(k)
        schnell.addStretch(1)
        v.addLayout(schnell)

        h = QHBoxLayout()
        h.setSpacing(14)
        spalte = QVBoxLayout()
        spalte.addWidget(label("Uhrzeit", 10, "dim"))
        self.uhr = QTimeEdit()
        self.uhr.setDisplayFormat("HH:mm")
        self.uhr.setFont(schrift(16, True))
        self.uhr.setMinimumSize(130, 46)
        self.uhr.timeChanged.connect(lambda _t: self._anzeigen())
        spalte.addWidget(self.uhr)
        h.addLayout(spalte)
        self.systemstart = Schalter("Bei Systemstart", "⚡")
        self.systemstart.bei_wechsel = self._anzeigen
        self.systemstart.setMinimumHeight(46)
        spalte2 = QVBoxLayout()
        spalte2.addWidget(label("Außerdem", 10, "dim"))
        spalte2.addWidget(self.systemstart)
        h.addLayout(spalte2)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(label("Einmal nach jedem Hochfahren (wenn die Wache mit Windows startet). War der Rechner "
                          "zur Uhrzeit aus, holt die Wache den Scan beim nächsten Mal nach.", 9, "dim"))

        v.addStretch(1)
        self.vorschau = label("", 10.5, "text")
        v.addWidget(self.vorschau)
        self.status = label("", 10, "dim")
        v.addWidget(self.status)
        unten = QHBoxLayout()
        abbrechen = Knopf("Schließen", mitte=True)
        abbrechen.clicked.connect(self.close)
        self.speichern_k = Knopf("Speichern", "💾", mitte=True)
        self.speichern_k.akzent = True
        self.speichern_k.clicked.connect(self.speichern)
        unten.addStretch(1)
        unten.addWidget(abbrechen)
        unten.addWidget(self.speichern_k)
        v.addLayout(unten)
        self.laden()

    # -- Plan ------------------------------------------------------------------------
    def laden(self) -> None:
        plan = T.plan_lesen()
        for i, k in enumerate(self.tage):
            k.akzent = i in plan["tage"]
            k.update()
        h, m = (int(x) for x in plan["uhrzeit"].split(":"))
        self.uhr.setTime(QTime(h, m))
        self.systemstart.akzent = plan["systemstart"]
        self.systemstart.update()
        self._anzeigen()

    def auswahl(self) -> dict:
        return T.plan_normal({"tage": [i for i, k in enumerate(self.tage) if k.akzent],
                              "uhrzeit": self.uhr.time().toString("HH:mm"),
                              "systemstart": self.systemstart.akzent, "geaendert": time.time()})

    def _tage_setzen(self, auswahl) -> None:
        for i, k in enumerate(self.tage):
            k.akzent = i in auswahl
            k.update()
        self._anzeigen()

    def _anzeigen(self) -> None:
        plan = self.auswahl()
        if not T.aktiv(plan):
            self.vorschau.setText("Kein Zeitplan – der Vollscan läuft nur, wenn du ihn startest.")
            return
        text = "📅 " + T.beschreibung(plan)
        n = T.naechster_termin(plan, time.time())
        if n:
            text += (f"\nNächster Scan: {T.TAGE[time.localtime(n).tm_wday]} "
                     f"{time.strftime('%d.%m. um %H:%M', time.localtime(n))}")
        self.vorschau.setText(text)

    def speichern(self) -> None:
        a = self.auswahl()
        try:
            T.plan_speichern(a["tage"], a["uhrzeit"], a["systemstart"])
        except OSError as e:
            self._melden(f"⚠ Nicht gespeichert: {e}", "fehler")
            return
        self._melden("✅ Gespeichert – die laufende Wache übernimmt den Plan in spätestens einer Minute.", "ok")
        QTimer.singleShot(1800, self.close)

    def _melden(self, text: str, farbname: str) -> None:
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Termin")
    fenster = TerminFenster()
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
