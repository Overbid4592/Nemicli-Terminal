"""Die gemeinsame Qt-Schicht von Kugel und Tray: EINE QApplication, das Theme,
die Brücke in den GUI-Thread, das Bildschirmfoto.

WARUM QT
tkinter kann nur einen Canvas-Kreis mit Transparenzfarbe und ein graues
Text-Widget. PySide6 (nur „Essentials“:
QtCore/QtGui/QtWidgets) bringt echte Per-Pixel-Transparenz, Antialiasing,
Schatten, Animationen, Markdown im Label und ein Tray-Symbol aus demselben
Guss – pystray und Pillow fallen damit im Hintergrund-Prozess weg. Kein Node,
kein npm, keine WebView: alles ist Python und signierte Qt-DLLs.

REGELN
- `anwendung()` im Hauptthread aufrufen, bevor irgendein Fenster entsteht.
- Alles, was Qt-Objekte anfasst, läuft im GUI-Thread. Aus anderen Threads
  (asyncio-Schleife, Wächter, Motor) geht es NUR über `im_gui(fn)` – das
  Signal wird in die GUI-Schleife eingereiht.
- Das Theme ist das des Terminals: Nacht-Hintergrund, Cyan für die
  Persönlichkeit, Pink für den Nutzer, Grün/Gelb/Rot für die Lage.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

THEME = {
    "nacht":      "#0b0d16",      # Fensterhintergrund
    "flaeche":    "#141828",      # Karten, Blasen der Persönlichkeit
    "flaeche2":   "#1b2036",      # Eingabe, Knöpfe
    "flaeche3":   "#262c48",      # Knopf unter der Maus
    "rand":       "#2a3150",      # feine Linien
    "cyan":       "#22c1d6",      # die Persönlichkeit
    "pink":       "#ff5fb0",      # der Nutzer
    "text":       "#e6e9f2",
    "leise":      "#8b93a7",
    "gruen":      "#3fb950",
    "gelb":       "#d29922",
    "rot":        "#e5484d",
    "grau":       "#6e7681",
}

QSS = f"""
QWidget {{ color: {THEME['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
QFrame#panel {{ background: {THEME['nacht']}; border: 1px solid {THEME['cyan']}; border-radius: 16px; }}
QFrame#kopf {{ background: transparent; border: none; border-bottom: 1px solid {THEME['rand']}; }}
QLabel#name {{ color: {THEME['cyan']}; font-size: 11pt; font-weight: bold; }}
QLabel#status {{ color: {THEME['leise']}; font-size: 9pt; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {THEME['flaeche3']}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QFrame#blase_sie {{ background: {THEME['flaeche']}; border: 1px solid {THEME['rand']};
                    border-radius: 14px; border-bottom-left-radius: 4px; }}
QFrame#blase_du {{ background: #2a1a2e; border: 1px solid #5a2f52;
                   border-radius: 14px; border-bottom-right-radius: 4px; }}
QFrame#blase_hinweis {{ background: transparent; border: none; }}
QFrame#blase_sie QLabel, QFrame#blase_du QLabel {{ background: transparent; border: none; }}
QLabel#hinweis {{ color: {THEME['leise']}; font-size: 9pt; font-style: italic; }}
QLabel#wer_sie {{ color: {THEME['cyan']}; font-size: 8.5pt; font-weight: bold; }}
QLabel#wer_du {{ color: {THEME['pink']}; font-size: 8.5pt; font-weight: bold; }}
QPlainTextEdit#eingabe {{ background: {THEME['flaeche2']}; border: 1px solid {THEME['rand']};
                          border-radius: 12px; padding: 8px 10px; selection-background-color: {THEME['cyan']}; }}
QPlainTextEdit#eingabe:focus {{ border: 1px solid {THEME['cyan']}; }}
QPushButton {{ background: {THEME['flaeche2']}; border: 1px solid {THEME['rand']}; border-radius: 10px;
               padding: 5px 11px; font-size: 9pt; }}
QPushButton:hover {{ background: {THEME['flaeche3']}; border-color: {THEME['cyan']}; }}
QPushButton:pressed {{ background: {THEME['rand']}; }}
QPushButton#senden {{ background: {THEME['cyan']}; color: {THEME['nacht']}; font-weight: bold; border: none; }}
QPushButton#senden:hover {{ background: #4fd6e8; }}
QPushButton#zu {{ background: transparent; border: none; color: {THEME['leise']}; font-size: 12pt; padding: 0 6px; }}
QPushButton#zu:hover {{ color: {THEME['pink']}; }}
QMenu {{ background: {THEME['flaeche']}; border: 1px solid {THEME['rand']}; padding: 6px; }}
QMenu::item {{ padding: 6px 18px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {THEME['flaeche3']}; }}
QMenu::item:disabled {{ color: {THEME['leise']}; }}
QMenu::separator {{ height: 1px; background: {THEME['rand']}; margin: 5px 4px; }}
"""

_app: QApplication | None = None
_bruecke: "_Bruecke | None" = None


class _Bruecke(QObject):
    """Ein Signal mit einem Callable – von jedem Thread gesendet, im GUI-Thread ausgeführt."""
    aufruf = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.aufruf.connect(self._ausfuehren, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _ausfuehren(fn) -> None:
        try:
            fn()
        except Exception:
            pass


def anwendung() -> QApplication:
    """Die eine QApplication – im Hauptthread anlegen, `exec()` blockiert dort."""
    global _app, _bruecke
    if _app is None:
        _app = QApplication.instance() or QApplication(["nemicli-wache"])
        _app.setApplicationName("NemiCLI-Wache")
        _app.setQuitOnLastWindowClosed(False)         # die Kugel darf sich verstecken
        _app.setFont(QFont("Segoe UI", 10))
        _app.setStyleSheet(QSS)
        _bruecke = _Bruecke()
    return _app


def laeuft() -> bool:
    return _app is not None


def im_gui(fn) -> None:
    """`fn()` im GUI-Thread ausführen – aus jedem Thread erlaubt. Ohne laufende
    Anwendung direkt aufrufen (Tests, Kopfloser Betrieb)."""
    if _bruecke is None:
        fn()
        return
    if threading.current_thread() is threading.main_thread():
        fn()
    else:
        _bruecke.aufruf.emit(fn)


def beenden() -> None:
    if _app is not None:
        im_gui(_app.quit)


def bildschirmfoto(pfad: Path) -> Path:
    """Den Hauptbildschirm als PNG speichern (Qt, kein Pillow nötig)."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("kein Bildschirm")
    bild = screen.grabWindow(0)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    if not bild.save(str(pfad), "PNG"):
        raise RuntimeError(f"konnte {pfad.name} nicht speichern")
    return pfad
