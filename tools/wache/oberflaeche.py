"""Die gemeinsame Qt-Schicht von Kugel und Tray: EINE QApplication, das Theme,
die Brücke in den GUI-Thread, das Bildschirmfoto.

PySide6 (nur QtCore/QtGui/QtWidgets): Per-Pixel-Transparenz, Antialiasing, Animationen,
Markdown im Label und das Tray-Symbol aus einer Bibliothek – ohne WebView.

REGELN
- `anwendung()` im Hauptthread aufrufen, bevor irgendein Fenster entsteht.
- Alles, was Qt-Objekte anfasst, läuft im GUI-Thread. Aus anderen Threads
  (asyncio-Schleife, Wächter, Motor) geht es NUR über `im_gui(fn)` – das
  Signal wird in die GUI-Schleife eingereiht.
- Das Theme ist das gemeinsame Design (ui/design.py): dunkle Flächen, Cyan für die
  Persönlichkeit, Lila für den Nutzer, Grün/Gelb/Rot für die Lage.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QImage
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
# Gemeinsames Design aller Fenster (ui/design.py): gleiche Flächen und Farben wie das Desktop-Fenster,
# der Nutzer in Lila wie dort. Ohne das Modul bleibt die eigene Tabelle oben.
try:
    import design as _D
    _F = _D.FARBE
    THEME.update({"nacht": _F["grund"], "flaeche": _F["flaeche"], "flaeche2": _F["flaeche_hell"],
                  "flaeche3": _F["flaeche_hoch"], "rand": _F["rand"], "cyan": _F["cyan"], "pink": _F["lila"],
                  "text": _F["text"], "leise": _F["dim"], "gruen": _F["ok"], "gelb": _F["warn"],
                  "rot": _F["fehler"], "grau": _F["blass"]})
    _pt, _rolle_px = _D.groesse, _D.px
    _DU_GRUND, _DU_RAND = _F["flaeche_hell"], _F["blase_a"]       # Blase des Nutzers im Farbschema
except ImportError:
    _D = None

    def _pt(punkt: float) -> float:
        return punkt

    def _rolle_px(rolle: str) -> int:
        return 15

    _DU_GRUND, _DU_RAND = "#241a33", "#4c1d95"

def _rgba(hex_farbe: str, alpha: float) -> str:
    h = hex_farbe.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


QSS = f"""
QWidget {{ color: {THEME['text']}; font-family: 'Segoe UI'; font-size: {_pt(10):.1f}pt; }}
QFrame#panel {{ background: {THEME['nacht']}; border: 1px solid {_rgba(THEME['cyan'], 0.45)}; border-radius: 22px; }}
QFrame#kopf {{ background: transparent; border: none; border-bottom: 1px solid {THEME['rand']}; }}
QLabel#name {{ color: {THEME['cyan']}; font-size: {_pt(11):.1f}pt; font-weight: bold; }}
QLabel#status {{ color: {THEME['leise']}; font-size: {_pt(9):.1f}pt; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {THEME['flaeche3']}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QFrame#blase_sie {{ background: {THEME['flaeche']}; border: 1px solid {THEME['rand']};
                    border-radius: 16px; border-bottom-left-radius: 6px; }}
QFrame#blase_du {{ background: {_DU_GRUND}; border: 1px solid {_DU_RAND};
                   border-radius: 16px; border-bottom-right-radius: 6px; }}
QFrame#blase_hinweis {{ background: transparent; border: none; }}
QFrame#blase_sie QLabel, QFrame#blase_du QLabel {{ background: transparent; border: none; }}
QFrame#blase_sie QLabel#text {{ font-size: {_rolle_px('ki')}px; }}
QFrame#blase_du QLabel#text {{ font-size: {_rolle_px('du')}px; }}
QLabel#hinweis {{ color: {THEME['leise']}; font-size: {_pt(9):.1f}pt; font-style: italic; }}
QLabel#wer_sie {{ color: {THEME['cyan']}; font-size: {_pt(8.5):.1f}pt; font-weight: bold; }}
QLabel#wer_du {{ color: {THEME['pink']}; font-size: {_pt(8.5):.1f}pt; font-weight: bold; }}
QPlainTextEdit#eingabe {{ background: {THEME['flaeche2']}; border: 1px solid {THEME['rand']};
                          border-radius: 14px; padding: 8px 10px; selection-background-color: {THEME['cyan']}; }}
QPlainTextEdit#eingabe:focus {{ border: 1px solid {THEME['cyan']}; }}
QPushButton {{ background: {THEME['flaeche2']}; border: 1px solid {THEME['rand']}; border-radius: 10px;
               padding: 5px 11px; font-size: {_pt(9):.1f}pt; }}
QPushButton:hover {{ background: {THEME['flaeche3']}; border-color: {THEME['cyan']}; }}
QPushButton:pressed {{ background: {THEME['rand']}; }}
QPushButton#senden {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {THEME['cyan']}, stop:1 {THEME['pink']});
                      color: {THEME['nacht']}; font-weight: bold; border: none; border-radius: 12px; }}
QPushButton#senden:hover {{ background: {THEME['cyan']}; border: 1px solid {THEME['text']}; }}
QPushButton#zu {{ background: transparent; border: none; color: {THEME['leise']}; font-size: {_pt(12):.1f}pt; padding: 0 6px; }}
QPushButton#zu:hover {{ color: {THEME['pink']}; }}
QPushButton#neu {{ background: transparent; border: 1px solid {THEME['rand']}; border-radius: 9px;
                   color: {THEME['cyan']}; font-weight: bold; padding: 2px 10px; }}
QPushButton#neu:hover {{ background: {THEME['flaeche3']}; border-color: {THEME['cyan']}; }}
QFrame#plan {{ background: rgba(250, 204, 21, 0.07); border: 1px solid #facc15; border-radius: 12px; }}
QFrame#plan QLabel {{ background: transparent; border: none; }}
QLabel#plan_kopf {{ color: #facc15; font-size: {_pt(9):.1f}pt; font-weight: bold; }}
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


def _sitzung_endet(*_args) -> None:
    """Windows meldet Abmelden oder Herunterfahren (WM_QUERYENDSESSION → commitDataRequest). Die Wache hat
    kein Konsolenfenster, den Konsolen-Haken des Fehler-Wächters bekommt sie nie – ohne diese Meldung zählte
    das harte Ende durch Windows beim nächsten Start als „unsauber“."""
    try:
        import fehlerwaechter
        fehlerwaechter.sauber_beendet("Windows meldet Abmelden oder Herunterfahren")
    except Exception:
        pass


def anwendung() -> QApplication:
    """Die eine QApplication – im Hauptthread anlegen, `exec()` blockiert dort."""
    global _app, _bruecke
    if _app is None:
        _app = QApplication.instance() or QApplication(["nemicli-wache"])
        _app.setApplicationName("NemiCLI-Wache")
        _app.setQuitOnLastWindowClosed(False)         # die Kugel darf sich verstecken
        if _D is not None:                             # Schrift wie alle Fenster (/schriftgröße)
            _app.setFont(_D.schrift(10))
            _app.setStyleSheet(QSS.replace("font-family: 'Segoe UI'", f"font-family: '{_D.familie() or 'Segoe UI'}'"))
        else:
            _app.setFont(QFont("Segoe UI", 10))
            _app.setStyleSheet(QSS)
        _app.commitDataRequest.connect(_sitzung_endet)
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
    bild = foto_aufnehmen()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    if not bild.save(str(pfad), "PNG"):
        raise RuntimeError(f"konnte {pfad.name} nicht speichern")
    return pfad


FOTO_KANTE = 1600                 # mehr bekommt ein Modell ohnehin nicht (vision.to_data_uri_small)


def foto_aufnehmen() -> QImage:
    """Der Hauptbildschirm als Bild, ohne Speichern (GUI-Thread). QImage, nicht QPixmap: QPixmap.save hält beim
    Packen ganz Python an."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("kein Bildschirm")
    return screen.grabWindow(0).toImage()


def foto_speichern(bild: QImage, pfad: Path) -> Path:
    """Verkleinert als PNG speichern (für einen eigenen Thread): Ein buntes 4K-Bild zu packen dauert Sekunden."""
    from PIL import Image
    rgb = bild.convertToFormat(QImage.Format.Format_RGB888)
    im = Image.frombuffer("RGB", (rgb.width(), rgb.height()), bytes(rgb.constBits()), "raw", "RGB",
                          rgb.bytesPerLine(), 1)
    im.thumbnail((FOTO_KANTE, FOTO_KANTE), Image.Resampling.BILINEAR)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    im.save(pfad, "PNG", compress_level=1)
    return pfad
