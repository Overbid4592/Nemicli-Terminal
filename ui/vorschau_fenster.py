"""vorschau_fenster.py – kleines Fenster, das ein bewegtes Porträt (animiertes WebP) in Schleife zeigt.

Eigener Prozess (`--vorschau <datei>`), damit die Oberfläche nicht wartet: rahmenlos, abgerundet, immer
oben, unten rechts über der Taskleiste; mit der Maus verschiebbar, ✕ oder Esc schließt.
Schließen von außen ohne Prozessrechte: schliessen_alle() schreibt eine Uhrzeit in SIGNAL; jedes Fenster,
das vorher geöffnet wurde, schließt sich. starten() tut das zuerst – ein neues Porträt ersetzt das alte.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HOEHE_MAX = 420               # so hoch ist die Vorschau höchstens
HORCHEN_MS = 400              # so oft schaut ein Fenster nach dem Schließ-Signal


def _signal() -> Path:
    try:
        import paths
        return Path(paths.DATEN) / "learned" / "vorschau_schliessen.txt"
    except Exception:
        return Path(__file__).resolve().parents[1] / "learned" / "vorschau_schliessen.txt"


def schliessen_alle() -> None:
    """Alle offenen Vorschau-Fenster schließen sich innerhalb von HORCHEN_MS."""
    p = _signal()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{time.time():.3f}", encoding="utf-8")


def _signal_zeit() -> float:
    try:
        return float(_signal().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def befehl(pfad: str) -> list[str]:
    if getattr(sys, "frozen", False):
        from paths import exe_ohne_fenster
        return [str(exe_ohne_fenster()), "--vorschau", str(pfad)]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    main = Path(__file__).resolve().parents[1] / "main.py"
    return [str(pythonw if pythonw.exists() else python), str(main), "--vorschau", str(pfad)]


def starten(pfad: str) -> None:
    """Fenster in einem eigenen Prozess öffnen (kehrt sofort zurück); ein offenes schließt sich."""
    schliessen_alle()
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    befehl_ = befehl(pfad)
    if not Path(befehl_[0]).name.lower().startswith("pythonw"):
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(befehl_, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fenster_bauen(datei: Path):
    """Das Fenster als Widget (noch nicht gezeigt) – braucht eine laufende QApplication."""
    from PySide6.QtCore import QPoint, QSize, Qt, QTimer
    from PySide6.QtGui import QColor, QMovie, QPainter, QPainterPath
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

    farben = {"nacht": "#0b0d16", "rand": "#2a3150", "text": "#e6e9f2", "leise": "#8b93a7", "knopf": "#262c48"}

    class Fenster(QWidget):
        def __init__(self):
            super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                             | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setWindowTitle("NemiCLI · Porträt")
            self.start = time.time()
            self._druck: QPoint | None = None
            self.film = QMovie(str(datei))
            self.film.jumpToFrame(0)
            g = self.film.currentImage().size()
            f = min(1.0, HOEHE_MAX / max(1, g.height()))
            self.film.setScaledSize(QSize(round(g.width() * f), round(g.height() * f)))
            kopf = QHBoxLayout()
            titel = QLabel("🎭  " + datei.stem.replace("_", " "))
            titel.setStyleSheet(f"color:{farben['leise']}; font-size:11px;")
            zu = QPushButton("✕")
            zu.setFixedSize(22, 22)
            zu.setCursor(Qt.CursorShape.PointingHandCursor)
            zu.setStyleSheet(f"QPushButton{{color:{farben['text']}; background:transparent; border:none;}}"
                             f"QPushButton:hover{{background:{farben['knopf']}; border-radius:11px;}}")
            zu.clicked.connect(self.close)
            kopf.addWidget(titel)
            kopf.addStretch(1)
            kopf.addWidget(zu)
            bild = QLabel()
            bild.setMovie(self.film)
            schicht = QVBoxLayout(self)
            schicht.setContentsMargins(10, 6, 10, 10)
            schicht.setSpacing(4)
            schicht.addLayout(kopf)
            schicht.addWidget(bild)
            self.horchen = QTimer(self)
            self.horchen.timeout.connect(self._horchen)
            self.horchen.start(HORCHEN_MS)
            self.film.start()

        def _horchen(self) -> None:
            if _signal_zeit() > self.start:
                self.close()

        def paintEvent(self, _e) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            weg = QPainterPath()
            weg.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)
            p.fillPath(weg, QColor(farben["nacht"]))
            p.setPen(QColor(farben["rand"]))
            p.drawPath(weg)

        def keyPressEvent(self, e) -> None:
            if e.key() == Qt.Key.Key_Escape:
                self.close()

        def mousePressEvent(self, e) -> None:
            self._druck = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, e) -> None:
            if self._druck is not None:
                self.move(e.globalPosition().toPoint() - self._druck)

        def mouseReleaseEvent(self, _e) -> None:
            self._druck = None

    return Fenster()


def zeigen(pfad: str) -> int:
    datei = Path(pfad)
    if datei.suffix.lower() != ".webp" or not datei.is_file():
        return 2
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = fenster_bauen(datei)
    w.adjustSize()
    flaeche = QGuiApplication.primaryScreen().availableGeometry()
    w.move(flaeche.right() - w.width() - 24, flaeche.bottom() - w.height() - 24)
    w.show()
    return app.exec()
