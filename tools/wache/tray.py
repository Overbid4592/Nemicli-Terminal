"""Symbol in der Taskleiste (Infobereich) für den Hintergrund-Prozess.

Seit 20.09.2026 Qt (QSystemTrayIcon, siehe oberflaeche.py) – vorher pystray +
Pillow. Das Schild wird zur Laufzeit gemalt: grün = Wache läuft, alles ruhig;
gelb = offene Alarme, noch niemand hat draufgeschaut; rot = die Persönlichkeit
hat etwas als ECHT eingestuft; grau = Wache gestoppt. Eine Zahl zeigt offene
Alarme.

Menü: Status · NemiCLI öffnen · Berichte öffnen · (Kugel-Einträge) · Inventar
jetzt · Jetzt trainieren · Wache pausieren/fortsetzen · Beenden. Das Menü wird
beim Aufklappen frisch gebaut, damit Texte wie „pausieren/fortsetzen“ stimmen.
Läuft im GUI-Thread; `aktualisieren()`/`melden()` dürfen von überall kommen.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import oberflaeche as O

FARBEN = {"gruen": (63, 185, 80), "gelb": (210, 153, 34), "rot": (229, 72, 77), "grau": (110, 118, 129)}


def symbol(farbe: str = "gruen", zahl: int = 0, groesse: int = 64) -> QIcon:
    """Ein Schild mit Häkchen (oder Zahl) malen."""
    pix = QPixmap(groesse, groesse)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QColor(*FARBEN.get(farbe, FARBEN["gruen"]))
    s = groesse / 64
    kontur = QPolygonF([QPointF(32 * s, 4 * s), QPointF(56 * s, 14 * s), QPointF(56 * s, 32 * s),
                        QPointF(50 * s, 48 * s), QPointF(32 * s, 60 * s), QPointF(14 * s, 48 * s),
                        QPointF(8 * s, 32 * s), QPointF(8 * s, 14 * s)])
    fuellung = QColor(f); fuellung.setAlpha(70)
    p.setBrush(fuellung)
    p.setPen(QPen(f, max(2.0, 4 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPolygon(kontur)
    if zahl > 0:
        text = str(zahl) if zahl < 100 else "99+"
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", int((24 if zahl < 10 else 18) * s), QFont.Weight.Bold))
        p.drawText(QRectF(0, -2 * s, groesse, groesse), Qt.AlignmentFlag.AlignCenter, text)
    else:
        p.setPen(QPen(f, max(3.0, 6 * s), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(21 * s, 32 * s), QPointF(29 * s, 40 * s), QPointF(44 * s, 24 * s)]))
    p.end()
    return QIcon(pix)


class Tray:
    def __init__(self, *, stand, aktion, install_ordner: Path, berichte: Path, extra_menue=None):
        """stand(): dict mit farbe/zahl/text · aktion(name): Menüaktion ausführen ·
        extra_menue(): Liste (text|callable, callback) für weitere Einträge."""
        self._stand = stand
        self._aktion = aktion
        self._extra = extra_menue
        self.install = install_ordner
        self.berichte = berichte
        self._icon: QSystemTrayIcon | None = None
        self._menue: QMenu | None = None
        self._takt: QTimer | None = None
        self._letzt = ("", -1)

    # ------------------------------------------------------------- Menü

    def _menue_bauen(self) -> None:
        m = self._menue
        if m is None:
            return
        m.clear()
        s = self._stand()
        kopf = QAction(s.get("text", "🛡 NemiCLI-Wache"), m); kopf.setEnabled(False)
        m.addAction(kopf)
        m.addSeparator()
        m.addAction("NemiCLI öffnen", self._oeffnen)
        m.addAction("Berichte öffnen", lambda: self._ordner(self.berichte))
        if self._extra:
            m.addSeparator()
            for text, cb in self._extra():
                m.addAction(text() if callable(text) else text, cb)
        m.addSeparator()
        m.addAction("Inventar jetzt scannen", lambda: self._aktion("inventar"))
        m.addAction("Modell jetzt trainieren", lambda: self._aktion("training"))
        m.addAction("Wache fortsetzen" if s.get("pausiert") else "Wache pausieren", lambda: self._aktion("pause"))
        m.addSeparator()
        m.addAction("Wache beenden", lambda: self._aktion("beenden"))

    def _oeffnen(self) -> None:
        cmd = self.install / "nemicli.cmd"
        try:
            if cmd.exists():
                subprocess.Popen(["cmd", "/c", "start", "", str(cmd)], cwd=str(Path.home()))
            else:
                subprocess.Popen(["cmd", "/c", "start", "", "python", str(self.install / "main.py")],
                                 cwd=str(Path.home()))
        except OSError:
            pass

    @staticmethod
    def _ordner(pfad: Path) -> None:
        try:
            pfad.mkdir(parents=True, exist_ok=True)
            os.startfile(str(pfad))
        except OSError:
            pass

    # ------------------------------------------------------------- Stand

    def aktualisieren(self) -> None:
        O.im_gui(self._aktualisieren)

    def _aktualisieren(self) -> None:
        if self._icon is None:
            return
        s = self._stand()
        key = (s.get("farbe", "gruen"), int(s.get("zahl", 0)))
        if key != self._letzt:
            self._letzt = key
            self._icon.setIcon(symbol(*key))
        self._icon.setToolTip(str(s.get("text", "NemiCLI-Wache"))[:127])

    def melden(self, titel: str, text: str) -> None:
        def _m():
            if self._icon is not None:
                self._icon.showMessage(titel[:60], text[:250], QSystemTrayIcon.MessageIcon.Information, 6000)
        O.im_gui(_m)

    def _klick(self, grund) -> None:
        if grund == QSystemTrayIcon.ActivationReason.Trigger:      # Linksklick = mit ihr reden
            self._aktion("chat")

    # ------------------------------------------------------------- Laufen

    def starten(self) -> None:
        """Symbol anlegen – nach oberflaeche.anwendung(), im GUI-Thread. Blockiert nicht."""
        O.anwendung()
        self._icon = QSystemTrayIcon(symbol())
        self._icon.setToolTip("NemiCLI-Wache")
        self._menue = QMenu()
        self._menue.aboutToShow.connect(self._menue_bauen)
        self._icon.setContextMenu(self._menue)
        self._icon.activated.connect(self._klick)
        self._icon.show()
        self._takt = QTimer(); self._takt.setInterval(5000); self._takt.timeout.connect(self._aktualisieren)
        self._takt.start()
        self._aktualisieren()

    def stop(self) -> None:
        def _s():
            if self._takt is not None:
                self._takt.stop()
            if self._icon is not None:
                self._icon.hide()
        O.im_gui(_s)
