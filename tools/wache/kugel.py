"""Die Schwebekugel: die Persönlichkeit auf dem Desktop, wenn kein Terminal offen ist.

Rahmenloses, durchsichtiges Qt-Fenster, immer oben, verschiebbar (Lage: Wache/kugel.json).
Farbe: grün ruhig · orange offene Alarme · rot „echt“ · Regenbogen, solange die Persönlichkeit im
Hintergrund arbeitet. Klick öffnet das Chatfenster (Verlauf je Persönlichkeit in
Wache/kugel_verlauf_<name>.json). Dazu Grüße (Wache/gruesse.md, eine Zeile je Gruß; {name},
{nutzer}), freie Momente (/kugel impuls), Ablegen von Dateien, Leben (kugel_leben), Musik
(kugel_musik, klang.py) und Erinnerungen (erinnerungen.py).

Alles läuft im GUI-Thread; das Gespräch ist eine Coroutine aus main.py (`chat(text, bilder)`) auf der
asyncio-Schleife des Dienstes – die Antwort kommt über `oberflaeche.im_gui` zurück.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import math
import os
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QMovie, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient, QTextDocument)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from . import ORDNER
from . import lebendig as L
from . import oberflaeche as O
from . import steuerung
from .oberflaeche import THEME

GRUESSE_DATEI = ORDNER / "gruesse.md"
LAGE_DATEI = ORDNER / "kugel.json"
VERLAUF_DATEI = ORDNER / "kugel_verlauf.json"
ZULETZT_DATEI = ORDNER / "kugel_zuletzt.txt"      # zuletzt aktiv gesehen – überdauert Ausschalten
VERLAUF_MAX = 200            # so viele Einträge zeigt und speichert das Chatfenster
EINLEITUNG = ("Klick auf die Kugel schließt das Fenster. Enter sendet. Hier wird nur gelesen und "
              "geredet – Änderungen am PC machst du im großen NemiCLI.")
GROESSE = 64                 # Durchmesser der Kugel
RAND = 18                    # Platz für den Lichthof rundherum
PANEL_BREITE, PANEL_HOEHE = 420, 580
WECHSEL_S = 0.45            # so lange blendet ein Bild ins nächste über

# Ältere Vorgaben, die in angelegten gruesse.md stehen geblieben sind -> heutige Fassung
ALTE_GRUESSE = {"Kurz gemeldet: nichts Auffälliges. 🌙": "Kurz gemeldet: nichts Auffälliges. 🛡"}

VORGABE_GRUESSE = [
    "Hey {nutzer} 🙂 alles ruhig hier.",
    "Ich bin da. Alles ok bei dir?",
    "Kurz gemeldet: nichts Auffälliges. 🛡",
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


def _rollen_schrift(rolle: str) -> QFont:
    """Schrift der Sprechblasen: „ki“ oder „du“ in der Größe von /schriftgröße."""
    f = QFont(QApplication.font())
    try:
        import design
        f.setPixelSize(design.px(rolle))
    except Exception:
        pass
    return f


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
                    zeilen.append(ALTE_GRUESSE.get(z, z))
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


def vollbild() -> bool:
    """Läuft ein Vollbild-Programm (Spiel, Präsentation) oder ist niemand da (Sperre)?"""
    try:
        import ctypes
        z = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(z)) == 0:
            return z.value in (1, 2, 3, 4)     # nicht da · beschäftigt · D3D-Vollbild · Präsentation
    except Exception:
        pass
    return False


def impuls_an() -> bool:
    try:
        import config
        return bool(config.load().get("kugel_impuls", False))
    except Exception:
        return False


def schalter(name: str, vorgabe: bool = True) -> bool:
    """Ein- und Ausschalter der Kugel aus der Config (kugel_leben, kugel_musik)."""
    try:
        import config
        return bool(config.load().get(name, vorgabe))
    except Exception:
        return vorgabe


SCHLAF_AB_S = 10 * 60          # so lange ohne Eingabe, dann schläft die Kugel sichtbar
AUSFLUG_ALLE_MIN = (25, 60)    # Abstand zwischen zwei Ausflügen (Minuten, zufällig)
AUSFLUG_DAUER_S = (120, 360)   # so lange bleibt sie unterwegs
MUSIK_AB_S = 120               # so lange Musik am Stück, dann darf sie etwas dazu sagen
MUSIK_PAUSE_MIN = (40, 90)     # Abstand zwischen zwei Musik-Momenten
ANKUENDIGUNG_S = 2.0           # Regenbogen und Denk-Punkte, bevor eine Blase erscheint
BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
_BILD_PFAD = re.compile(r"[A-Za-z]:\\[^\n\r\"'<>|*?]+?\.(?:png|jpe?g|webp)", re.IGNORECASE)


def bilder_aus(text: str, hoechstens: int = 8) -> list[Path]:
    """Vorhandene Bilddateien, deren Pfad in einem Ergebnistext steht (Videos zählen nicht)."""
    out: list[Path] = []
    for m in _BILD_PFAD.finditer(text or ""):
        p = Path(m.group(0).strip())
        if p not in out and p.is_file():
            out.append(p)
    return out[-hoechstens:]


def kante_position(fenster: tuple[int, int, int, int], breite: int, hoehe: int,
                   schirm: tuple[int, int, int, int], anteil: float) -> tuple[int, int] | None:
    """Wohin sich die Kugel setzt, damit sie auf der Oberkante eines Fensters sitzt.
    fenster/schirm = (links, oben, rechts, unten), breite/hoehe = Kugelfenster samt Rand,
    anteil = 0 … 1 (Stelle entlang der Kante, links von den Fenster-Knöpfen). None = passt nicht."""
    l, o, r, u = fenster
    sl, so, sr, su = schirm
    if r - l < 320 or u - o < 200:
        return None
    y = o + 2 - hoehe + RAND                     # Kugel-Unterkante liegt auf der Fensterkante
    if y < so:
        return None                              # Fenster ganz oben: kein Platz darüber
    x = int(l + (r - l) * (0.12 + 0.45 * anteil) - breite / 2)
    return max(sl, min(x, sr - breite)), y


def musik_text(name: str, quelle: str, titel: str) -> str:
    jetzt = datetime.now()
    return MUSIK_TEXT.format(zeit=jetzt.strftime("%H:%M"), tag=_TAGE[jetzt.weekday()],
                             tageszeit=tageszeit(jetzt.hour), quelle=quelle, titel=titel) + _bilder_zeile(name)


def _schirme_nativ() -> list[tuple]:
    """(Ursprung, Skalierung, logische Geometrie) je Bildschirm – zum Umrechnen von Windows-Pixeln."""
    out = []
    for sc in QGuiApplication.screens():
        g = sc.geometry()
        out.append((g.topLeft(), sc.devicePixelRatio() or 1.0, g))
    return out


def _logisch(x: int, y: int) -> QPoint:
    """Windows-Pixel (GetWindowRect) → Qt-Koordinaten. Qt behält je Bildschirm den Ursprung
    und teilt den Rest durch die Skalierung."""
    for ursprung, dpr, g in _schirme_nativ():
        if (ursprung.x() <= x < ursprung.x() + g.width() * dpr
                and ursprung.y() <= y < ursprung.y() + g.height() * dpr):
            return QPoint(int(ursprung.x() + (x - ursprung.x()) / dpr), int(ursprung.y() + (y - ursprung.y()) / dpr))
    return QPoint(x, y)


def vordergrund_fenster(eigene: set[int]) -> tuple[int, tuple[int, int, int, int]] | None:
    """(Handle, Rechteck in Qt-Koordinaten) des aktiven Fensters – nicht maximiert, nicht minimiert,
    nicht Desktop oder Taskleiste, nicht die Kugel selbst. Sonst None."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd in eigene or user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
            return None
        klasse = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, klasse, 64)
        if klasse.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return None
        rect = (ctypes.c_long * 4)()
        # sichtbarer Rahmen ohne unsichtbaren Schattenrand (DWMWA_EXTENDED_FRAME_BOUNDS)
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
        a, b = _logisch(rect[0], rect[1]), _logisch(rect[2], rect[3])
        return int(hwnd), (a.x(), a.y(), b.x(), b.y())
    except Exception:
        return None


def fenster_rechteck(hwnd: int) -> tuple[int, int, int, int] | None:
    """Rechteck eines Fensters (Qt-Koordinaten) – None, wenn es weg, minimiert oder maximiert ist."""
    try:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
            return None
        rect = (ctypes.c_long * 4)()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
        a, b = _logisch(rect[0], rect[1]), _logisch(rect[2], rect[3])
        return a.x(), a.y(), b.x(), b.y()
    except Exception:
        return None


def ankommen_an() -> bool:
    try:
        import config
        return bool(config.load().get("kugel_ankommen", True))
    except Exception:
        return True


ANKOMMEN_AB_S = 3 * 3600      # so lange ohne Eingabe (oder im Standby), dann gilt die Rückkehr als Ankommen


def zuletzt_lesen() -> float | None:
    try:
        return float(ZULETZT_DATEI.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def zuletzt_schreiben(zeit: float) -> None:
    try:
        ZULETZT_DATEI.write_text(f"{zeit:.0f}", encoding="utf-8")
    except OSError:
        pass


def leerlauf_s() -> float:
    """Sekunden seit der letzten Maus- oder Tastatureingabe (GetLastInputInfo)."""
    try:
        import ctypes

        class _Info(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = _Info()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def tageszeit(stunde: int) -> str:
    if 5 <= stunde < 11:
        return "Morgen"
    if 11 <= stunde < 14:
        return "Mittag"
    if 14 <= stunde < 18:
        return "Nachmittag"
    if 18 <= stunde < 23:
        return "Abend"
    return "Nacht"


def _dauer_text(sek: float) -> str:
    stunden = sek / 3600
    return f"{stunden:.0f} Stunden" if stunden < 48 else f"{stunden / 24:.0f} Tage"


ANKOMMEN_TEXT = (
    "[Ankommen · {zeit} · {tag} · {tageszeit}] Der Nutzer ist gerade an den Rechner zurückgekommen – "
    "er war etwa {weg} weg. Niemand hat dich angesprochen; du darfst ihn von dir aus begrüßen "
    "(deine Antwort erscheint als Sprechblase an der Kugel, höchstens zwei kurze Sätze), ein Bild "
    "malen, dein Kugel-Bild wechseln – oder still bleiben. Was zuletzt war, steht in deinem Tagebuch.\n"
    "Die Tageszeit ist nur eine Angabe: Schließ daraus nicht, dass er müde ist, und wünsch keine gute Nacht.\n"
    "Sag nur, was dir wirklich einfällt – keine Floskel. Willst du nichts tun, antworte genau: (still)"
)


def ankommen_text(name: str, weg_s: float) -> str:
    jetzt = datetime.now()
    text = ANKOMMEN_TEXT.format(zeit=jetzt.strftime("%H:%M"), tag=_TAGE[jetzt.weekday()],
                                tageszeit=tageszeit(jetzt.hour), weg=_dauer_text(weg_s))
    return text + _bilder_zeile(name)


IMPULS_TEXT = (
    "[Freier Moment · {zeit} · {tag} · {tageszeit}] Niemand hat dich gerade angesprochen. Du hast einen Moment "
    "für dich und darfst von dir aus etwas tun – oder nichts.\n"
    "Möglich: dem Nutzer etwas sagen (deine Antwort erscheint als Sprechblase an der Kugel, höchstens "
    "zwei kurze Sätze) · ein Bild malen (bild_malen) · dein Kugel-Bild wechseln (kugel mit stimmung) · "
    "den Bildschirm ansehen (bildschirm_ansehen), wenn du wissen willst, was gerade los ist.\n"
    "Die Tageszeit ist nur eine Angabe: Schließ daraus nicht, dass der Nutzer müde ist, und wünsch keine gute Nacht.\n"
    "Sag nur, was dir wirklich einfällt – keine Floskel, nichts, was du heute schon gesagt hast. "
    "Willst du nichts tun, antworte genau: (still)"
)
_TAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _bilder_zeile(name: str) -> str:
    try:
        import kugelmotive
        da = [k for k in steuerung.bilder(name) if k]
        if da:
            return ("\nDeine Kugel-Bilder (stimmung): "
                    + ", ".join(kugelmotive.MOTIVE.get(k, (k, ""))[0] for k in sorted(da)))
    except Exception:
        pass
    return ""


MUSIK_TEXT = (
    "[Musik · {zeit} · {tag} · {tageszeit}] Beim Nutzer läuft gerade Musik – {quelle}: „{titel}“. Niemand hat "
    "dich angesprochen. Du darfst kurz etwas dazu sagen (deine Antwort erscheint als Sprechblase an der Kugel, "
    "ein kurzer Satz; nicht laut vorlesen, die Musik läuft), dir merken, was er gern hört (merken), dein "
    "Kugel-Bild wechseln (kugel mit stimmung) – oder still bleiben. Kennst du das Stück nicht, erfinde nichts "
    "darüber. Willst du nichts tun, antworte genau: (still)"
)


def impuls_text(name: str) -> str:
    jetzt = datetime.now()
    return IMPULS_TEXT.format(zeit=jetzt.strftime("%H:%M"), tag=_TAGE[jetzt.weekday()],
                              tageszeit=tageszeit(jetzt.hour)) + _bilder_zeile(name)


NICHT_DA_S = 120                 # so lange ohne Maus/Tastatur: zusätzlich ein Hinweis neben der Uhr


def _spontan(an: bool) -> None:
    try:
        import vonsichaus
        vonsichaus.runde(an)
    except ImportError:
        pass


def _vram_zeile() -> str:
    try:
        import vonsichaus
        return vonsichaus.hinweis()
    except ImportError:
        return ""


def _im_chat_zeigen(name: str, text: str, bilder: list) -> None:
    """Auch im NemiCLI-Chat zeigen (holt das laufende NemiCLI ab, sonst der nächste Start)."""
    try:
        import vonsichaus
        vonsichaus.schreiben(name, text, [str(b) for b in bilder])
    except Exception:
        pass


def still(antwort: str) -> bool:
    return antwort.strip().strip("()[]. *_").lower() in ("", "still")


def _verlauf_datei(name: str) -> Path:
    slug = "".join(c for c in name.lower() if c.isalnum())
    return VERLAUF_DATEI.with_name(f"{VERLAUF_DATEI.stem}_{slug}{VERLAUF_DATEI.suffix}") if slug else VERLAUF_DATEI


def verlauf_laden(name: str = "") -> list[dict]:
    """Verlauf des Chatfensters – je Persönlichkeit. Der frühere gemeinsame Verlauf geht an die
    Persönlichkeit, die ihn zuerst lädt (die aktive beim ersten Start nach dem Umstieg)."""
    datei = _verlauf_datei(name)
    if datei != VERLAUF_DATEI and not datei.exists() and VERLAUF_DATEI.exists():
        try:
            VERLAUF_DATEI.replace(datei)
        except OSError:
            pass
    try:
        d = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in d if isinstance(e, dict)][-VERLAUF_MAX:] if isinstance(d, list) else []


def verlauf_speichern(eintraege: list[dict], name: str = "") -> None:
    datei = _verlauf_datei(name)
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(json.dumps(eintraege[-VERLAUF_MAX:], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _ergebnis(erg) -> tuple[str, list, str]:
    """Antwort der Chat-Coroutine: (antwort, bilder) oder (antwort, bilder, todo_text)."""
    antwort, bilder = erg[0], erg[1]
    return antwort, bilder, (erg[2] or "") if len(erg) > 2 else ""


def _weg(item) -> None:
    """Layout-Eintrag samt Inhalt entfernen."""
    if (w := item.widget()) is not None:
        w.hide(); w.setParent(None); w.deleteLater()        # sofort raus, gelöscht wird später
    elif item.layout() is not None:
        lay = item.layout()
        while lay.count():
            _weg(lay.takeAt(0))
        lay.deleteLater()


def _schirm(punkt: QPoint):
    """Der Bildschirm, auf dem ein Punkt liegt – sonst der Hauptbildschirm."""
    s = QGuiApplication.screenAt(punkt) or QGuiApplication.primaryScreen()
    return s.availableGeometry()


# ================================================================ Die Kugel

class _Kugelfenster(QWidget):
    """Rahmenlos, durchsichtig, immer oben. Malt die Kugel und meldet Klick/Ziehen."""

    geklickt = Signal()
    gezogen = Signal()
    abgelegt = Signal(list)                         # Dateien wurden auf die Kugel gezogen
    gestreichelt = Signal()
    gestupst = Signal()                             # Rechtsklick
    beruehrt = Signal(str)                          # sanft · schwindelig (lebendig.Beruehrung)

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.schlaeft = False
        self.ablage = False                         # etwas wird gerade über die Kugel gezogen
        self._herzen: list[tuple[float, float]] = []
        self._streichel = [0.0, 0.0]                # [Weg in px, Beginn]
        self._maus: QPoint | None = None
        self._gestreichelt = 0.0
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
        self._druck_punkt = QPoint()                # Mausposition beim Drücken
        self._bewegt = False
        self.bild: QPixmap | None = None            # statt der Kugel: das Bild der Persönlichkeit
        self._bewegung = ""                         # huepfen · wackeln · nicken
        self._bewegung_start = 0.0
        self._film: QMovie | None = None            # bewegtes Porträt (portraet_bewegen), rund gezeigt
        self._film_rest = 0
        self._film_fertig = None
        self._film_rund = True                      # False: freigestellte Figur ohne Kreis
        self._film_ton = False                      # spielt eine Tonspur dazu
        self._rund = False
        self._alt_bild: QPixmap | None = None       # weicher Übergang: das vorige Bild blendet aus
        self._wechsel = 0.0
        self.blick_an = False                       # zum Mauszeiger neigen (Desktop-Leben)
        self._blick = [0.0, 0.0]                    # [Neigung in Grad, Versatz in px], weich
        self.atem = 1.0                             # Atemtempo nach Tageszeit
        self._beruehrung = L.Beruehrung()
        self._funken: list[tuple[float, float]] = []   # Sternchen, wenn ihr schwindelig ist

    def groesse_setzen(self, g: int) -> None:
        g = max(steuerung.GROESSE_MIN, min(steuerung.GROESSE_MAX, int(g)))
        if g != self.groesse:
            self.groesse = g
            self.setFixedSize(g + 2 * RAND, g + 2 * RAND)
            self.update()

    def bild_setzen(self, pfad: Path | None) -> None:
        pix = QPixmap(str(pfad)) if pfad else QPixmap()
        neu = None if pix.isNull() else pix
        if self.bild is not None and neu is not None and self._film is None:
            self._alt_bild, self._wechsel = self.bild, time.time()
        self.bild = neu
        self.update()

    def film_starten(self, pfad: Path, fertig, runden: int = 2, ton: str | None = None) -> None:
        """Bewegtes Porträt in der Kugel – rund, oder freigestellt (…_frei_kugel.webp) als ganze Figur ohne
        Kreis; mit `ton` (WAV) spricht sie dazu. Danach ruft es `fertig` (das Bild kommt zurück)."""
        self.film_stoppen(melden=False)
        film = QMovie(str(pfad), parent=self)        # Qt besitzt den Film: Freigabe nur über deleteLater
        if not film.isValid():
            film.deleteLater()
            if fertig:                               # sonst bliebe das letzte Bild eines vorigen Films stehen
                fertig()
            return
        self._film, self._film_fertig = film, fertig
        self._film_rund = not Path(pfad).name.endswith("_frei_kugel.webp")
        self._film_rest = runden * max(1, film.frameCount())
        film.frameChanged.connect(self._film_bild)
        film.start()
        self._film_ton = bool(ton)
        if ton:
            try:
                import winsound
                winsound.PlaySound(str(ton), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                self._film_ton = False

    def _film_bild(self, _nr: int) -> None:
        if self._film is None:
            return
        self.bild, self._rund = self._film.currentPixmap(), self._film_rund
        self.update()
        self._film_rest -= 1
        if self._film_rest <= 0:
            self.film_stoppen()

    def film_stoppen(self, melden: bool = True) -> None:
        if self._film is None:
            return
        self._film.stop()
        self._film.deleteLater()                     # kann aus frameChanged des Films selbst kommen
        if getattr(self, "_film_ton", False):
            self._film_ton = False
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        fertig, self._film, self._film_fertig, self._rund = self._film_fertig, None, None, False
        if melden and fertig:
            fertig()

    def bewegen(self, art: str) -> None:
        """Kurze Geste: huepfen (hoch und runter), wackeln (kippen), nicken (ducken), gaehnen (strecken)."""
        self._bewegung, self._bewegung_start = art, time.time()

    def funken(self) -> None:
        jetzt = time.time()
        self._funken = [(jetzt + i * 0.12, i * math.tau / 5) for i in range(5)]

    def herzen(self) -> None:
        jetzt = time.time()
        self._herzen = [h for h in self._herzen if jetzt - h[0] < 1.6][-6:]
        for i in range(3):
            self._herzen.append((jetzt + i * 0.18, random.uniform(-0.35, 0.35)))

    def _geste(self) -> tuple[float, float, float]:
        """(Hebung in px, Drehung in Grad, Stauchung) der laufenden Geste. Stauchung < 0 = strecken."""
        if not self._bewegung:
            return 0.0, 0.0, 0.0
        t = time.time() - self._bewegung_start
        if t > (1.8 if self._bewegung == "gaehnen" else 1.2):
            self._bewegung = ""
            return 0.0, 0.0, 0.0
        if self._bewegung == "gaehnen":
            return 0.0, 0.0, -abs(math.sin(t * math.pi / 1.8)) * 0.14
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
        kern, glanz = self._farben()
        m = self.width() / 2
        atem = 0.5 + 0.5 * math.sin(self.phase)
        hoch, dreh, stauch = self._geste()
        if self.schlaeft:
            p.setOpacity(0.72)
        if self.bild is not None:
            self._bild_malen(p, kern, m, atem, hoch, dreh, stauch)
            self._extras(p, m)
            p.end()
            return
        dreh += self._blick[0]
        if hoch or dreh or stauch or self._blick[1]:
            p.translate(m + self._blick[1], m - hoch); p.rotate(dreh); p.scale(1 + stauch * 0.4, 1 - stauch)
            p.translate(-m, -m)
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
        p.resetTransform()
        self._extras(p, m)
        p.end()

    def _extras(self, p: QPainter, m: float) -> None:
        """Über der Kugel: „z Z“ im Schlaf, aufsteigende Herzen nach dem Streicheln."""
        p.resetTransform()
        p.setOpacity(1.0)
        if self.schlaeft:
            f = QFont("Segoe UI", max(7, int(self.groesse * 0.14)), QFont.Weight.Bold)
            for i, zeichen in enumerate("zZ"):
                t = (self.phase * 0.25 + i * 0.5) % 1.0
                p.setFont(f)
                p.setPen(QColor(220, 230, 255, int(220 * (1 - t))))
                p.drawText(QRectF(m + self.groesse * 0.22 + i * 7, RAND * 0.2 + (1 - t) * RAND * 1.2 + i * 2, 20, 20),
                           Qt.AlignmentFlag.AlignLeft, zeichen)
        jetzt = time.time()
        for start, versatz in self._herzen:
            t = jetzt - start
            if not 0 <= t < 1.6:
                continue
            p.setFont(QFont("Segoe UI Emoji", max(8, int(self.groesse * 0.16))))
            p.setPen(QColor(255, 105, 150, int(255 * (1 - t / 1.6))))
            x = m + versatz * self.groesse * 0.6 - 7
            y = m - self.groesse * 0.3 - t * RAND * 1.6
            p.drawText(QRectF(x, y, 20, 20), Qt.AlignmentFlag.AlignCenter, "♥")
        for start, winkel in self._funken:
            t = jetzt - start
            if not 0 <= t < 1.6:
                continue
            w = winkel + t * 4.0
            p.setFont(QFont("Segoe UI Symbol", max(7, int(self.groesse * 0.13))))
            p.setPen(QColor(255, 215, 90, int(255 * (1 - t / 1.6))))
            x = m + math.cos(w) * self.groesse * 0.42 - 7
            y = m - self.groesse * 0.42 + math.sin(w) * self.groesse * 0.12 - 7
            p.drawText(QRectF(x, y, 16, 16), Qt.AlignmentFlag.AlignCenter, "✦")

    def _farben(self):
        """(Kern, Glanz): Lagenfarbe – oder Regenbogen, solange sie im Hintergrund arbeitet."""
        if not self.beschaeftigt:
            return FARBEN.get(self.farbe, FARBEN["gruen"])
        ton = (self.phase * 0.06) % 1.0
        kern = QColor.fromHsvF(ton, 0.75, 0.95)
        glanz = QColor.fromHsvF(ton, 0.35, 1.0)
        return ((kern.red(), kern.green(), kern.blue()), (glanz.red(), glanz.green(), glanz.blue()))

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
        p.translate(m + self._blick[1], m - hoch)
        p.rotate(dreh + self._blick[0])
        p.scale(skala * (1 + stauch * 0.4), skala * (1 - stauch))
        if self._rund:                              # bewegtes Porträt: Gesicht im Kreis
            weg = QPainterPath()
            weg.addEllipse(QRectF(-g / 2, -g / 2, g, g))
            p.setClipPath(weg)
        deckung = p.opacity()
        t = (time.time() - self._wechsel) / WECHSEL_S
        if self._alt_bild is not None and t < 1.0:  # weicher Übergang zum neuen Bild
            p.setOpacity(deckung * (1.0 - t))
            self._pix_malen(p, self._alt_bild, g)
            p.setOpacity(deckung * t)
        else:
            self._alt_bild = None
        self._pix_malen(p, self.bild, g)
        p.setOpacity(deckung)
        p.restore()
        if self.beschaeftigt:
            for k in range(3):
                an = int((self.phase * 2.5) % 3) == k
                p.setBrush(QColor(255, 255, 255, 235 if an else 110))
                p.setPen(QPen(_qc(kern, 200), 1))
                p.drawEllipse(QRectF(m + (k - 1) * 9 - 3, self.height() - RAND - 2, 6, 6))

    def _pix_malen(self, p: QPainter, bild: QPixmap, g: int) -> None:
        modus = (Qt.AspectRatioMode.KeepAspectRatioByExpanding if self._rund
                 else Qt.AspectRatioMode.KeepAspectRatio)
        pix = bild.scaled(QSize(g, g), modus, Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(QRectF(-pix.width() / 2, -pix.height() / 2, pix.width(), pix.height()).toRect(), pix)

    def takt(self) -> None:
        schritt = 0.055 if self.farbe != "rot" else 0.16
        self.phase += schritt * (0.35 if self.schlaeft else self.atem)
        ziel = (0.0, 0.0)
        if self.blick_an and not self.schlaeft and self._druck is None and self.isVisible():
            mitte, maus = self.frameGeometry().center(), QCursor.pos()
            ziel = L.blick((mitte.x(), mitte.y()), (maus.x(), maus.y()))
        for i in (0, 1):
            self._blick[i] += (ziel[i] - self._blick[i]) * 0.08
        ziel = 1.0 if (self.schwebt or self.ablage) else 0.0
        self._hebung += (ziel - self._hebung) * 0.18
        self.update()

    # --- Maus
    def enterEvent(self, _e) -> None:
        self.schwebt = True

    def leaveEvent(self, _e) -> None:
        self.schwebt = False
        self._maus = None
        self._streichel = [0.0, 0.0]
        self._beruehrung.zuruecksetzen()

    # --- Ablegen (Drag & Drop)
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            self.ablage = True
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:
        self.ablage = False

    def dropEvent(self, e) -> None:
        self.ablage = False
        pfade = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if pfade:
            e.acceptProposedAction()
            self.abgelegt.emit(pfade[:5])

    def _streicheln(self, punkt: QPoint) -> None:
        """Maus kreist über der Kugel (ohne Klick): genug Weg in kurzer Zeit = gestreichelt."""
        jetzt = time.time()
        if self._maus is not None:
            if jetzt - self._streichel[1] > 2.5:
                self._streichel = [0.0, jetzt]
            self._streichel[0] += (punkt - self._maus).manhattanLength()
            if self._streichel[0] > 600 and jetzt - self._gestreichelt > 8:
                self._gestreichelt = jetzt
                self._streichel = [0.0, jetzt]
                self.herzen()
                self.gestreichelt.emit()
        self._maus = punkt

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.gestupst.emit()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._druck_punkt = e.globalPosition().toPoint()
            self._druck = self._druck_punkt - self.frameGeometry().topLeft()
            self._bewegt = False

    def mouseMoveEvent(self, e) -> None:
        if self._druck is None:
            punkt = e.globalPosition().toPoint()
            self._streicheln(punkt)
            if (art := self._beruehrung.fuettern(punkt.x(), punkt.y(), time.time())):
                self.beruehrt.emit(art)
            return
        punkt = e.globalPosition().toPoint()
        if not self._bewegt:
            # Zittern beim Klicken ist kein Ziehen: erst ab der System-Zieh-Distanz zum Druckpunkt.
            if (punkt - self._druck_punkt).manhattanLength() < QApplication.startDragDistance():
                return
            self._bewegt = True
        self.move(punkt - self._druck)
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
        self.vorschau = QLabel("")
        self.vorschau.setStyleSheet("background: transparent; border: none;")
        self.vorschau.hide()
        lay.addWidget(self.vorschau)
        self.ernst = False
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def zeigen(self, text: str, ernst: bool, bild: Path | None = None) -> None:
        self.ernst = ernst
        self.label.setText(text)
        self.label.setVisible(bool(text))
        pix = QPixmap(str(bild)) if bild else QPixmap()
        if not pix.isNull():
            self.vorschau.setPixmap(pix.scaled(QSize(180, 180), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
            self.vorschau.show()
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


def _link_oeffnen(url: str) -> None:
    if str(url).lower().startswith(("https://", "http://")):
        QDesktopServices.openUrl(QUrl(url))


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
        if self.pfad.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            return                                       # nur Bilder öffnen, nie Programme oder Verknüpfungen
        try:
            os.startfile(str(self.pfad))
        except OSError:
            pass


class _Panel(QWidget):
    """Die Karte: Kopf (Name, Status, + Neu, ✕), Verlauf mit Sprechblasen, Eingabe, Knöpfe."""

    geschlossen = Signal()
    neu = Signal()
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
        neu = QPushButton("+ Neu"); neu.setObjectName("neu"); neu.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        neu.setToolTip("Neuer Chat – der bisherige Verlauf wird geleert")
        neu.clicked.connect(self.neu.emit)
        k.addWidget(self.punkt); k.addWidget(self.name); k.addStretch(1); k.addWidget(self.status)
        k.addSpacing(6); k.addWidget(neu); k.addWidget(zu)
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
        self.eingabe.setPlaceholderText(f"Schreib {name} … (Enter sendet, Shift+Enter neue Zeile)")
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
    def _textbreite(text: str, markdown: bool, maximal: int, schrift: QFont | None = None) -> int:
        """So breit wie der Text, höchstens `maximal` – QLabel mit Umbruch rät sonst zu schmal."""
        doc = QTextDocument()
        doc.setDefaultFont(schrift or QApplication.font())
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
        lbl = QLabel(); lbl.setObjectName("text")      # Größe nach /schriftgröße (oberflaeche.QSS)
        schrift = _rollen_schrift("du" if du else "ki")
        lbl.setFont(schrift)
        lbl.setTextFormat(Qt.TextFormat.PlainText if du else Qt.TextFormat.MarkdownText)
        if not du:
            try:
                import pfadschutz                        # Windows-Pfade nicht als Markdown lesen
                text = pfadschutz.schuetzen(text)
            except ImportError:
                pass
        lbl.setText(text)
        lbl.setWordWrap(True)
        lbl.setFixedWidth(self._textbreite(text, not du, int(PANEL_BREITE * 0.78) - 26, schrift))
        lbl.setOpenExternalLinks(False)                  # nur http(s) öffnen – file:// könnte Programme starten
        lbl.linkActivated.connect(_link_oeffnen)
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

    def plan(self, text: str) -> None:
        """Gelber Kasten mit der Todo-Liste der Runde (erste Zeile = Kopf)."""
        zeilen = text.strip().splitlines()
        rahmen = QFrame(); rahmen.setObjectName("plan")
        b = QVBoxLayout(rahmen); b.setContentsMargins(12, 8, 12, 9); b.setSpacing(3)
        kopf = QLabel(zeilen[0] if zeilen else "🟨 Todo"); kopf.setObjectName("plan_kopf")
        b.addWidget(kopf)
        rest = "\n".join(zeilen[1:])
        if rest:
            lbl = QLabel(rest)
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setWordWrap(True)
            lbl.setFixedWidth(self._textbreite(rest, False, int(PANEL_BREITE * 0.85) - 26))
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            b.addWidget(lbl)
        self._anhaengen(rahmen)

    def leeren(self) -> None:
        while self.verlauf.count() > 1:          # der Stretch am Ende bleibt
            _weg(self.verlauf.takeAt(0))

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
                 name: str, nutzer: str, nemicli_oeffnen, screenshot, chat_neu=None, name_von=None,
                 erinnerungen: bool = False, leben: bool = False, musik: bool = False, melden=None,
                 zuschauen=None, frage_taste: bool = False):
        """name_von: liefert den Namen der gerade aktiven Persönlichkeit (Wechsel zur Laufzeit).
        erinnerungen/leben/musik schaltet der Wache-Dienst ein (Erinnerungen abholen, Desktop-Leben,
        Pegel des Lautsprechers); melden(titel, text) zeigt einen Hinweis neben der Uhr.
        zuschauen(text, pfad): Coroutine für den Blick aufs Spiel (lebendig.py); frage_taste: systemweites
        Tastenkürzel (Config kugel_frage_taste) öffnet das Chatfenster."""
        self.app = O.anwendung()
        self._stand = stand
        self._chat = chat
        self._chat_neu = chat_neu
        self._loop = schleife
        self._sichtbar = sichtbar
        self.name = name
        self.nutzer = nutzer
        self._name_von = name_von
        self._name_takt = 0
        self._nemicli = nemicli_oeffnen
        self._screenshot = screenshot

        self.fenster = _Kugelfenster()
        self.fenster.geklickt.connect(self.panel_umschalten)
        self.fenster.gezogen.connect(self._gezogen)
        self.fenster.abgelegt.connect(self._abgelegt)
        self.fenster.gestreichelt.connect(self._gestreichelt)
        self.fenster.gestupst.connect(lambda: self._reagieren("stupsen"))
        self.fenster.beruehrt.connect(self._reagieren)
        self._zuschauen = zuschauen
        self._reaktion: tuple[Path, float] | None = None   # kurzes Motiv nach einer Berührung
        self._taet = L.Taetigkeit()
        self._taet_bild: Path | None = None
        self._spiel: str | None = None                     # Spiel im Vordergrund
        self._naechstes_zuschauen = time.time() + 120
        self._nacht_geprueft = 0.0
        self._getippt = 0.0
        self._taste_offen = False                          # Chatfenster über das Tastenkürzel geöffnet
        self._gezeigtes_bild: Path | None | str = ""
        self._taste = None
        if frage_taste:
            taste = str(L._config().get("kugel_frage_taste", L.TASTE) or "")
            if taste and taste.lower() != "aus":
                self._taste = L.Tastenkuerzel(taste, lambda: O.im_gui(self._frage_taste))
                if not self._taste.starten():
                    print(f"Kugel: {self._taste.fehler}")
                    self._taste = None
        self._erinnerungen = erinnerungen
        self._leben = leben
        self._melden = melden
        self._leben_an = self._musik_an = False
        self._ausflug: dict | None = None
        self._flug: QPropertyAnimation | None = None
        self._naechster_ausflug = time.time() + random.uniform(*AUSFLUG_ALLE_MIN) * 60
        self._naechstes_gaehnen = 0.0
        self._pegel = None
        self._gespuer = None
        if musik:
            from . import klang
            self._pegel, self._gespuer = klang.Pegelmesser(), klang.Musikgespuer()
            self._pegel.starten()
        self._naechster_musik_moment = time.time() + 10 * 60
        self._letztes_stueck: tuple | None = None
        self._ankuendigung_bis = 0.0
        self._blase: _Blase | None = None
        self._blase_bis = 0.0
        self._panel: _Panel | None = None
        self._anhaenge: list[Path] = []
        self._beschaeftigt = False
        self._gezeigt = False
        self._versteckt_fuer_foto = False
        self._farbe = "gruen"
        self._naechster_gruss = self._gruss_zeit()
        self._rot_naechste = 0.0                     # Erinnerungsblase bei Rot
        self._naechster_impuls = self._impuls_zeit()
        zuletzt = zuletzt_lesen()                    # war der Rechner aus? Dann zählt die Zeit seitdem
        self._weg_seit: float | None = (zuletzt if zuletzt and time.time() - zuletzt >= ANKOMMEN_AB_S
                                        else None)
        self._letzter_takt = time.time()
        self._zuletzt_gemerkt = 0.0
        self._sicherung_versucht = 0.0
        self._verlauf = verlauf_laden(name)
        gruesse(name, nutzer)                        # legt Wache/gruesse.md mit Vorgaben an
        # Steuerung durch die Persönlichkeit (Wache/kugel_zustand.json): was beim Start schon
        # drinsteht (Stimmung, Größe, versteckt, Lage) gilt; alte Blasen/Gesten werden nicht nachgeholt.
        z = steuerung.lesen()
        self._gesteuert = {"sagen": float(z.get("sagen_zeit") or 0), "bewegung": float(z.get("bewegung_zeit") or 0),
                       "lage": 0.0, "stimmung": None, "groesse": None, "bild_pfad": None, "outfit": None,
                       "bewegt": float(z.get("bewegt_zeit") or 0)}
        self._versteckt = bool(z.get("versteckt"))
        self._zustand_anwenden(z)
        self.fenster.move(*self._lage_laden())

        self._puls = QTimer(); self._puls.setInterval(33); self._puls.timeout.connect(self._tick)
        self._takt = QTimer(); self._takt.setInterval(1500); self._takt.timeout.connect(self._takt_tick)
        self._puls.start(); self._takt.start()
        QTimer.singleShot(0, self._takt_tick)

    # ------------------------------------------------------------- Takt

    def _tick(self) -> None:
        self.fenster.beschaeftigt = (self._beschaeftigt or steuerung.im_hintergrund()
                                     or time.time() < self._ankuendigung_bis)
        if self._gespuer is not None:
            self._gespuer.fuettern(self._pegel.wert, time.time())
        if self._gezeigt:
            self.fenster.takt()

    def _zustand_anwenden(self, z: dict) -> None:
        """Was die Persönlichkeit über die Aktion `kugel` will – nur Neues wird umgesetzt."""
        st = self._gesteuert
        stimmung = str(z.get("stimmung") or "")
        if z.get("wer") not in (None, "", self.name, "Persönlichkeit"):
            stimmung = ""                          # Stimmung einer anderen Persönlichkeit gilt hier nicht
        if self.fenster.beschaeftigt and stimmung != "kugel":
            stimmung = "denkt"                     # während sie denkt: das Denk-Bild, falls es eins gibt
        try:
            g = int(z.get("groesse") or GROESSE)
        except (TypeError, ValueError):
            g = GROESSE
        if g != st["groesse"]:
            st["groesse"] = g
            self.fenster.groesse_setzen(g)
        if stimmung != st["stimmung"] or z.get("outfit_zeit") != st["outfit"]:
            st["stimmung"], st["outfit"] = stimmung, z.get("outfit_zeit")
            pfad = None
            if stimmung != "kugel":
                da = steuerung.bilder(self.name)
                pfad = da.get(stimmung) or da.get("")
            if pfad != st["bild_pfad"]:
                st["bild_pfad"] = pfad
        self._bild_anzeigen()
        self._versteckt = bool(z.get("versteckt"))
        t = float(z.get("bewegung_zeit") or 0)
        if t > st["bewegung"]:
            st["bewegung"] = t
            self.fenster.bewegen(str(z.get("bewegung") or ""))
        t = float(z.get("bewegt_zeit") or 0)
        if t > st["bewegt"] and z.get("bewegt"):
            st["bewegt"] = t
            if z.get("wer") in (None, "", self.name, "Persönlichkeit") and time.time() - t < 120:
                self.fenster.film_starten(Path(z["bewegt"]), self._nach_film,
                                          runden=int(z.get("bewegt_runden") or 2), ton=z.get("bewegt_ton") or None)
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

    def _nach_film(self) -> None:
        self._gezeigtes_bild = ""
        self._bild_anzeigen()

    def _bild_anzeigen(self) -> None:
        """Welches Bild die Kugel zeigt: eine kurze Reaktion vor allem; was die Persönlichkeit gewählt hat
        (Stimmung) vor der Tätigkeit am PC; sonst das Grundbild."""
        if self.fenster._film is not None:
            return
        st = self._gesteuert
        pfad = st["bild_pfad"]
        if self._reaktion is not None and st["stimmung"] != "denkt":     # beim Denken das Denk-Bild
            if time.time() < self._reaktion[1]:
                pfad = self._reaktion[0]
            else:
                self._reaktion = None
        if self._reaktion is None and not st["stimmung"] and self._taet_bild is not None:
            pfad = self._taet_bild
        if pfad != self._gezeigtes_bild:
            self._gezeigtes_bild = pfad
            self.fenster.bild_setzen(pfad)

    def _motiv(self, art: str) -> Path | None:
        """Gemaltes Motiv zu einer Reaktion oder Tätigkeit (lebendig.REAKTION) – None, wenn es keins gibt."""
        try:
            import kugelmotive
            jetzt = time.time()
            stand = getattr(self, "_motiv_stand", None)
            if stand is None or stand[0] != self.name or jetzt - stand[1] > 30:   # Ordner nicht in jedem Takt lesen
                stand = self._motiv_stand = (self.name, jetzt, steuerung.bilder(self.name))
            da = stand[2]
            k = kugelmotive.finden(L.REAKTION.get(art, art), list(da))
            return da.get(k) if k else None
        except Exception:
            return None

    def _reagieren(self, art: str) -> None:
        """Berührung (stupsen · sanft · schwindelig) oder Tippen im Chatfenster: Geste, kurz ein passendes Motiv."""
        if not self._leben_an or self.fenster._film is not None:
            return
        self.fenster.schlaeft = False
        if art == "stupsen":
            self.fenster.bewegen("huepfen")
        elif art == "schwindelig":
            self.fenster.bewegen("wackeln")
            self.fenster.funken()
        elif art == "sanft":
            self.fenster.herzen()
        if (pfad := self._motiv(art)) is not None:
            self._reaktion = (pfad, time.time() + L.REAKTION_S.get(art, 4.0))
            self._bild_anzeigen()

    def _in_ecke(self, ecke: str) -> None:
        g = _schirm(self.fenster.geometry().center())
        w, h = self.fenster.width(), self.fenster.height()
        x = {"oben_links": g.left() + 24, "unten_links": g.left() + 24,
             "mitte": g.center().x() - w // 2}.get(ecke, g.right() - w - 24)
        y = {"oben_links": g.top() + 24, "oben_rechts": g.top() + 24,
             "mitte": g.center().y() - h // 2}.get(ecke, g.bottom() - h - 60)
        self.fenster.move(x, y)
        self._gezogen()

    def _name_pruefen(self) -> None:
        """Hat der Nutzer die Persönlichkeit gewechselt? Dann Name, Bilder und Verlauf der neuen."""
        self._name_takt += 1
        if self._name_von is None or self._name_takt % 10:
            return
        try:
            neu = str(self._name_von() or "").strip()
        except Exception:
            return
        if not neu or neu == self.name:
            return
        self._panel_schliessen()
        self._blase_weg()
        self.name = neu
        self._verlauf = verlauf_laden(neu)
        self._gesteuert.update(stimmung=None, bild_pfad="")   # Bild neu wählen, auch wenn es keins gibt

    def _takt_tick(self) -> None:
        try:
            self._name_pruefen()
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
            # „versteckt“ der Persönlichkeit wartet, solange der Nutzer im Fenster mit ihr redet
            versteckt = self._versteckt and self._panel is None
            soll = ((bool(self._sichtbar()) or (self._taste_offen and self._panel is not None))
                    and not self._versteckt_fuer_foto and not versteckt)
            if soll and not self._gezeigt:
                self.fenster.show()
                self._gezeigt = True
            elif not soll and self._gezeigt:
                self.fenster.hide()
                self._gezeigt = False
                self._blase_weg()
                if self._panel is not None and not self._versteckt_fuer_foto:
                    self._panel_schliessen()
            self._ankommen_pruefen(s)
            self._chats_sichern()
            self._leben_an = self._leben and schalter("kugel_leben", True)
            self._musik_an = self._gespuer is not None and schalter("kugel_musik", True)
            self._erinnerungen_pruefen()
            self._vorne_pruefen()
            if self._gezeigt:
                self._gruss_pruefen(s)
                self._impuls_pruefen(s)
                self._leben_pruefen(s)
                self._lebendig_pruefen(s)
                self._spiel_pruefen(s)
                self._musik_pruefen(s)
            if self._blase is not None and time.time() > self._blase_bis:
                self._blase_weg()
        except Exception:
            pass

    # ------------------------------------------------------------- Bewegen

    def _gezogen(self) -> None:
        self._ausflug = None                        # wer sie verschiebt, bestimmt ihr neues Zuhause
        if self._flug is not None:
            self._flug.stop()
            self._flug = None
        self._panel_platzieren()
        if self._blase is not None:
            self._blase_platzieren()
        if self.fenster._druck is None:             # beim Ziehen erst nach dem Loslassen speichern
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
        if s.get("farbe") == "rot":
            # Erinnerung höchstens alle 30 min, nicht bei offenem Fenster – und nicht über eine
            # Blase, die die Persönlichkeit gerade selbst geschrieben hat.
            eigene = self._gesteuert["sagen"] + 1800
            if jetzt >= max(self._rot_naechste, eigene) and self._panel is None:
                self.blase("Ich hab was gefunden – klick mich. 🔴", 12)
                self.fenster.bewegen("huepfen")
                self._rot_naechste = jetzt + 1800
            return
        if jetzt < self._naechster_gruss:
            return
        self._naechster_gruss = self._gruss_zeit()
        stunde = datetime.now().hour
        if s.get("farbe") != "gruen" or stunde < 8 or stunde >= 23 or impuls_an():
            return
        gruss = random.choice(gruesse(self.name, self.nutzer))
        self._ankuendigen(lambda: self.blase(gruss, 9))

    # ------------------------------------------------------------- Freier Moment

    def _impuls_zeit(self) -> float:
        return time.time() + random.uniform(1.0, 3.0) * 3600

    def _impuls_pruefen(self, s: dict) -> None:
        if not impuls_an() or time.time() < self._naechster_impuls:
            return
        self._naechster_impuls = self._impuls_zeit()
        stunde = datetime.now().hour
        if (self._chat is None or self._beschaeftigt or self._panel is not None
                or s.get("farbe") != "gruen" or stunde < 8 or stunde >= 23 or vollbild()):
            return
        self._impuls_starten(impuls_text(self.name) + _vram_zeile(), ins_chat=True)

    def _ankommen_pruefen(self, s: dict) -> None:
        """Nach mindestens ANKOMMEN_AB_S ohne Eingabe – oder im Standby, dann ruht dieser Prozess
        und sieht die Pause nur am Zeitsprung – darf die Persönlichkeit den Nutzer begrüßen."""
        jetzt = time.time()
        if jetzt - self._letzter_takt >= ANKOMMEN_AB_S and self._weg_seit is None:
            self._weg_seit = self._letzter_takt
        self._letzter_takt = jetzt
        leer = leerlauf_s()
        if leer < 60 and jetzt - self._zuletzt_gemerkt >= 60:
            self._zuletzt_gemerkt = jetzt
            zuletzt_schreiben(jetzt)
        if leer >= ANKOMMEN_AB_S:
            self._weg_seit = min(self._weg_seit or jetzt, jetzt - leer)
            return
        if self._weg_seit is None or leer > 60 or self._chat is None:     # Chat noch nicht bereit: gleich nochmal
            return
        weg = jetzt - self._weg_seit
        self._weg_seit = None
        if (not ankommen_an() or not self._gezeigt or self._beschaeftigt
                or self._panel is not None or s.get("farbe") == "rot" or vollbild()
                or not 5 <= datetime.now().hour < 23):
            return
        self._naechster_impuls = self._impuls_zeit()       # danach nicht gleich der nächste Moment
        self._naechster_gruss = self._gruss_zeit()
        self._impuls_starten(ankommen_text(self.name, weg))

    def _chats_sichern(self) -> None:
        """Im Vollbild (Spiel, Film) die Chat-Datenbank sichern, wenn die letzte Sicherung alt ist –
        höchstens alle 10 Minuten ein Versuch, im Hintergrund."""
        jetzt = time.time()
        if jetzt - self._sicherung_versucht < 600 or not vollbild():
            return
        self._sicherung_versucht = jetzt
        try:
            import chatstore
            import profilordner
            ort = profilordner.ordner("Chats", self.name, anlegen=False)
            if not chatstore.faellig(ort, chatstore.AUTO_STUNDEN):
                return
        except Exception:
            return

        def los():
            try:
                chatstore.sichern(ort)
            except Exception:
                pass
        threading.Thread(target=los, name="nemi-chats-sichern", daemon=True).start()

    def _impuls_starten(self, text: str, ins_chat: bool = False) -> None:
        """Spontane Runde (freier Moment, Ankommen, Musik); `ins_chat`: was sie sagt oder malt, steht auch im
        NemiCLI-Chat – der Gruß beim Ankommen nicht, der gilt dem Moment am Desktop."""
        self._ins_chat = ins_chat
        self._beschaeftigt = True
        self.fenster.beschaeftigt = True
        _spontan(True)                                   # Grafikspeicher-Regel für diese Runde
        try:
            fut = asyncio.run_coroutine_threadsafe(self._chat(text, []), self._loop)
        except Exception:                                # z. B. Schleife beendet: nicht dauerhaft beschäftigt bleiben
            _spontan(False)
            self._beschaeftigt = self.fenster.beschaeftigt = False
            return

        def fertig(f):
            _spontan(False)
            try:
                antwort, bilder, todo = _ergebnis(f.result())
            except Exception:
                antwort, bilder, todo = "", [], ""
            O.im_gui(lambda: self._impuls_antwort(antwort, bilder, todo))

        fut.add_done_callback(fertig)

    def _impuls_antwort(self, antwort: str, bilder: list, todo: str = "") -> None:
        self._beschaeftigt = False
        self.fenster.beschaeftigt = False
        self._zustand_anwenden(steuerung.lesen())
        bilder = [Path(b) for b in bilder]
        text = "" if still(antwort) else antwort.strip()
        if not text and not bilder:
            return
        if todo:
            self._merken({"plan": todo})
        if text:
            self._schreiben("sie", text)
        for b in bilder:
            self._bild_zeigen(b)
        if getattr(self, "_ins_chat", False):
            _im_chat_zeigen(self.name, text, bilder)
        kurz = " ".join(text.split())[:steuerung.SAGEN_MAX] if text else ""
        if self._melden is not None and (not self._gezeigt or leerlauf_s() >= NICHT_DA_S):
            self._melden(self.name, kurz or "🎨 hat dir ein Bild gemalt")      # schaut gerade niemand hin
        if self._panel is not None:
            return
        self.blase(kurz, 14, bild=bilder[-1] if bilder else None)

    def blase(self, text: str, sekunden: float = 8.0, bild: Path | None = None) -> None:
        """Kurze Sprechblase neben der Kugel (GUI-Thread), auf Wunsch mit kleinem Bild."""
        self._blase_weg()
        b = _Blase()
        b.geklickt.connect(self.panel_umschalten)
        b.zeigen(text, ernst=self._farbe == "rot", bild=bild)
        self._blase = b
        self._blase_platzieren()
        self._blase_bis = time.time() + sekunden

    def _ankuendigen(self, zeigen) -> None:
        """Kurz Regenbogen und Denk-Punkte, danach `zeigen` (die Blase) – statt eines Hüpfers."""
        self._ankuendigung_bis = time.time() + ANKUENDIGUNG_S
        self.fenster.beschaeftigt = True
        QTimer.singleShot(int(ANKUENDIGUNG_S * 1000), zeigen)

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
        p.neu.connect(self._neu_klick)
        p.senden.connect(self._senden_klick)
        p.screenshot.connect(self._screenshot_klick)
        p.alles_ok.connect(lambda: self.senden("Alles ok bei dir? Gib mir kurz die Lage der Wache."))
        p.nemicli.connect(self._nemicli)
        p.eingabe.textChanged.connect(self._tippt)
        p.lage(self._farbe)
        self._panel = p
        self._panel_platzieren()
        p.show()
        p.hinweis(EINLEITUNG)
        for e in self._verlauf:
            self._eintrag_zeigen(e)
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
            self._taste_offen = False

    def _schreiben(self, wer: str, text: str) -> None:
        """Nachrichten kommen in den Verlauf, Hinweise nur ins offene Fenster."""
        if wer in ("du", "sie"):
            self._merken({"wer": wer, "text": text})
        elif self._panel is not None:
            self._panel.hinweis(text)

    def _bild_zeigen(self, pfad: Path) -> None:
        self._merken({"bild": str(pfad)})

    def _merken(self, e: dict) -> None:
        self._verlauf = (self._verlauf + [e])[-VERLAUF_MAX:]
        verlauf_speichern(self._verlauf, self.name)
        self._eintrag_zeigen(e)

    def _eintrag_zeigen(self, e: dict) -> None:
        p = self._panel
        if p is None:
            return
        if e.get("plan"):
            p.plan(str(e["plan"]))
        elif e.get("bild"):
            if Path(e["bild"]).exists():
                p.bild(Path(e["bild"]))
        elif e.get("wer") in ("du", "sie"):
            p.nachricht(e["wer"], str(e.get("text", "")), self.name)

    def _neu_klick(self) -> None:
        """Neuer Chat: Verlauf im Fenster und Gespräch der Persönlichkeit leeren."""
        if self._beschaeftigt:
            self._schreiben("hinweis", f"({self.name} antwortet gerade noch …)")
            return
        self._verlauf = []
        verlauf_speichern(self._verlauf, self.name)
        self._anhaenge = []
        if self._chat_neu is not None:
            asyncio.run_coroutine_threadsafe(self._chat_neu(), self._loop)
        if self._panel is not None:
            self._panel.leeren()
            self._panel.hinweis(EINLEITUNG)
            self._panel.eingabe.setFocus()

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
                                       f"was {self.name} sehen soll, oder drück Enter.")
            self._bild_zeigen(Path(pfad))

    def senden(self, text: str) -> None:
        if self._beschaeftigt:
            self._schreiben("hinweis", f"({self.name} antwortet gerade noch …)")
            return
        if self._panel is None:
            self._panel_oeffnen()
        anhaenge, self._anhaenge = self._anhaenge, []
        self._schreiben("du", text)
        self._beschaeftigt = True
        self.fenster.beschaeftigt = True
        self._panel.denkt(True)
        self._zustand_anwenden(steuerung.lesen())          # Denk-Bild, falls vorhanden
        try:
            fut = asyncio.run_coroutine_threadsafe(self._chat(L.spiel_kontext(self._spiel) + text, anhaenge),
                                                   self._loop)
        except Exception as exc:
            self._antwort(f"(Fehler: {exc})", [], "")
            return

        def fertig(f):
            try:
                antwort, bilder, todo = _ergebnis(f.result())
            except Exception as exc:
                antwort, bilder, todo = f"(Fehler: {exc})", [], ""
            O.im_gui(lambda: self._antwort(antwort, bilder, todo))

        fut.add_done_callback(fertig)

    def _antwort(self, antwort: str, bilder: list, todo: str = "") -> None:
        self._beschaeftigt = False
        self.fenster.beschaeftigt = False
        self._zustand_anwenden(steuerung.lesen())
        if self._panel is not None:
            self._panel.denkt(False)
        if todo:
            self._merken({"plan": todo})
        self._schreiben("sie", antwort.strip() or "(keine Antwort)")
        if self._leben_an:
            self.fenster.bewegen("nicken")             # Antwort ist da
        for b in bilder:
            self._bild_zeigen(Path(b))
        if self._panel is None:                      # Fenster inzwischen zu → Blase
            kurz = antwort.strip().splitlines()[0][:120] if antwort.strip() else "Fertig."
            self.blase(kurz, 10, bild=Path(bilder[-1]) if bilder else None)

    # ------------------------------------------------------------- Ablegen · Streicheln

    def _abgelegt(self, pfade: list) -> None:
        """Dateien auf die Kugel gezogen: Bilder gehen als Bild mit, alles andere als Pfad."""
        pfade = [Path(p) for p in pfade if Path(p).exists()]
        if not pfade:
            return
        if self._beschaeftigt:
            self.blase(f"{self.name} ist gerade noch beschäftigt – gleich nochmal ablegen.", 6)
            return
        self.fenster.schlaeft = False
        bilder = [p for p in pfade if p.is_file() and p.suffix.lower() in BILD_ENDUNGEN][:4]
        andere = [p for p in pfade if p not in bilder]
        if self._panel is None:
            self._panel_oeffnen()
        for b in bilder:
            self._bild_zeigen(b)
        self._anhaenge.extend(bilder)
        teile = []
        if bilder:
            teile.append("Ich hab dir " + ("ein Bild" if len(bilder) == 1 else f"{len(bilder)} Bilder")
                         + " auf die Kugel gelegt – schau es dir an.")
        if andere:
            teile.append("Ich hab dir das hier auf die Kugel gelegt: " + "; ".join(str(p) for p in andere)
                         + ". Schau es dir an (datei_lesen, bei Ordnern ordner_auflisten) und sag mir kurz, was es ist.")
        self.senden(" ".join(teile))

    def _gestreichelt(self) -> None:
        self.fenster.schlaeft = False
        self.fenster.bewegen("huepfen")

    # ------------------------------------------------------------- Erinnerungen

    def _erinnerungen_pruefen(self) -> None:
        if not self._erinnerungen:
            return
        from . import erinnerungen
        if erinnerungen.terminal_offen():            # ein offenes NemiCLI-Fenster holt sie selbst ab
            return
        texte = [erinnerungen.meldung(d) for d in erinnerungen.abholen()]
        if texte:                                    # mehrere fällige: eine Blase, sonst verdeckt die letzte alle
            self.erinnern("\n".join(texte))

    def erinnern(self, text: str) -> None:
        """Fällige Erinnerung zeigen: an der Kugel (Regenbogen, dann Blase) oder neben der Uhr."""
        if not self._gezeigt or self._versteckt_fuer_foto:
            if self._melden is not None:
                self._melden("Erinnerung", text)
            return
        self.fenster.schlaeft = False
        self._merken({"wer": "sie", "text": text})
        if self._panel is None:
            self._ankuendigen(lambda: self.blase(text, 45))

    # ------------------------------------------------------------- Fertige Aufgaben

    def aufgabe_fertig(self, e: dict) -> None:
        """Hintergrund-Aufgabe fertig (GUI-Thread): Bilder klein in der Blase, sonst eine kurze Zeile.
        Videos werden nicht gezeigt."""
        titel = str(e.get("titel") or "Aufgabe")
        bilder = bilder_aus(str(e.get("ergebnis") or "")) if e.get("ok") else []
        text = (f"🎨 Fertig: {titel}" if bilder else f"✅ Fertig: {titel}") if e.get("ok") else f"⚠ Gescheitert: {titel}"
        for b in bilder:
            self._bild_zeigen(b)
        if not self._gezeigt:
            if self._melden is not None:
                self._melden("NemiCLI", text)
            return
        if self._panel is not None:
            self._schreiben("hinweis", text)
        else:
            bild = bilder[-1] if bilder else None
            self._ankuendigen(lambda: self.blase(text, 20, bild=bild))

    # ------------------------------------------------------------- Desktop-Leben

    def _leben_pruefen(self, s: dict) -> None:
        if not self._leben_an:
            self.fenster.schlaeft = False
            if self._ausflug is not None:
                self._heimkehren()
            return
        jetzt = time.time()
        leer = leerlauf_s()
        schlafen = (leer >= SCHLAF_AB_S and not self._beschaeftigt and self._panel is None
                    and s.get("farbe") != "rot")
        if schlafen != self.fenster.schlaeft:
            self.fenster.schlaeft = schlafen
            if not schlafen:
                self.fenster.bewegen("huepfen")      # aufgewacht
        stunde = datetime.now().hour
        if (stunde >= 23 or stunde < 5 or L.morgens(stunde)) and not schlafen and jetzt >= self._naechstes_gaehnen:
            if self._naechstes_gaehnen:
                self.fenster.bewegen("gaehnen")
            self._naechstes_gaehnen = jetzt + random.uniform(20, 45) * 60
        if self._ausflug is not None:
            a = self._ausflug
            weg = a["art"] == "kante" and fenster_rechteck(a["hwnd"]) != a["rechteck"]
            if jetzt >= a["bis"] or weg or self._panel is not None or schlafen:
                self._heimkehren()
            return
        if jetzt < self._naechster_ausflug:
            return
        self._naechster_ausflug = jetzt + random.uniform(*AUSFLUG_ALLE_MIN) * 60
        if (self._beschaeftigt or self._panel is not None or self._blase is not None or schlafen
                or s.get("farbe") != "gruen" or leer > 120 or not 8 <= stunde < 23 or vollbild()):
            return
        self._ausflug_starten()

    def _eigene_fenster(self) -> set[int]:
        out = {int(self.fenster.winId())}
        for w in (self._panel, self._blase):
            if w is not None:
                out.add(int(w.winId()))
        return out

    def _ausflug_starten(self) -> None:
        f = self.fenster
        heim = f.pos()
        g = _schirm(f.geometry().center())
        ziel, art, vg = None, "", None
        if random.random() < 0.6:
            vg = vordergrund_fenster(self._eigene_fenster())
            if vg is not None:
                schirm = _schirm(QPoint((vg[1][0] + vg[1][2]) // 2, vg[1][1]))
                pos = kante_position(vg[1], f.width(), f.height(),
                                     (schirm.left(), schirm.top(), schirm.right(), schirm.bottom()), random.random())
                if pos is not None:
                    ziel, art = QPoint(*pos), "kante"
        if ziel is None:
            schritt = random.choice((-1, 1)) * random.randint(80, 220)
            x = heim.x() + schritt
            if not g.left() <= x <= g.right() - f.width():
                x = heim.x() - schritt
            ziel, art = QPoint(max(g.left(), min(x, g.right() - f.width())), heim.y()), "spaziergang"
        self._ausflug = {"heim": heim, "bis": time.time() + random.uniform(*AUSFLUG_DAUER_S), "art": art,
                         "hwnd": vg[0] if vg else 0, "rechteck": vg[1] if vg else None}
        f.bewegen("huepfen")
        self._fliegen(ziel)

    def _heimkehren(self) -> None:
        a, self._ausflug = self._ausflug, None
        if a is not None:
            self.fenster.bewegen("huepfen")
            self._fliegen(a["heim"])

    def _fliegen(self, ziel: QPoint) -> None:
        if self._flug is not None:
            self._flug.stop()
        anim = QPropertyAnimation(self.fenster, b"pos")
        anim.setDuration(1100)
        anim.setStartValue(self.fenster.pos())
        anim.setEndValue(ziel)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda _w: (self._blase_platzieren(), self._panel_platzieren()))
        anim.start()
        self._flug = anim

    # ------------------------------------------------------------- Immer vorne

    def _vorne_pruefen(self) -> None:
        """/kugel vorne: über allen Fenstern bleiben – Windows schiebt ein Fenster „immer oben“ hinter ein anderes,
        das sich selbst nach vorn holt; deshalb in jedem Takt erneut. Aus: ein normales Fenster."""
        if not self._gezeigt or self._panel is not None:      # mit offenem Chatfenster bleibt die Ordnung
            return
        vorne = L.vorne_an()
        for w in (self.fenster, self._blase):              # jedes Mal: Qt setzt „immer oben“ beim Zeigen neu
            if w is not None:
                L.ganz_vorne(int(w.winId()), vorne)

    # ------------------------------------------------------------- Lebendig

    def _lebendig_pruefen(self, s: dict) -> None:
        """Blick zur Maus, Atem nach Tageszeit, Motiv zur Tätigkeit am PC, nachts der Schlafanzug."""
        f = self.fenster
        f.blick_an = self._leben_an
        if not self._leben_an:
            f.atem, self._taet_bild, self._spiel = 1.0, None, None
            self._bild_anzeigen()
            return
        jetzt = time.time()
        f.atem = L.atem_faktor(datetime.now().hour)
        vg = L.vordergrund(self._eigene_fenster())
        tat = L.taetigkeit(*vg) if vg else None
        self._spiel = tat[1] if tat and tat[0] == "spiel" else (self._spiel if vg is None else None)
        leer = leerlauf_s()
        if tat is None and L.LANGEWEILE_AB_S <= leer < SCHLAF_AB_S and not self._beschaeftigt:
            tat = ("langeweile", "")
        self._taet.fuettern(tat, jetzt)
        stabil = self._taet.stabil(jetzt)
        self._taet_bild = self._motiv(stabil[0]) if stabil and not f.schlaeft else None
        self._bild_anzeigen()
        if jetzt - self._nacht_geprueft >= 60:
            self._nacht_geprueft = jetzt
            self._nacht_outfit()

    def _nacht_outfit(self) -> None:
        """Nachts den Schlafanzug anziehen, wenn sie einen hat; morgens das Outfit von vorher zurück."""
        try:
            import kugelbilder as KB
            d = KB.outfits(self.name)
            nacht = L.nacht_outfit(d["liste"])
            if nacht is None:
                return
            datei = ORDNER / "kugel_nacht.json"
            try:
                vorher = json.loads(datei.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                vorher = None
            if L.nachtzeit(datetime.now().hour):
                if d["aktiv"] != nacht:
                    datei.write_text(json.dumps({"wer": self.name, "aktiv": d["aktiv"]}), encoding="utf-8")
                    KB.outfit_anziehen(self.name, nacht)
                    self._gesteuert["outfit"] = -1              # Bild neu wählen
            elif isinstance(vorher, dict) and vorher.get("wer") == self.name:
                if d["aktiv"] == nacht:                 # noch im Schlafanzug: zurück zu dem von vorher
                    KB.outfit_anziehen(self.name, str(vorher.get("aktiv") or ""))
                    self._gesteuert["outfit"] = -1
                datei.unlink(missing_ok=True)
        except Exception:
            pass

    def _tippt(self) -> None:
        jetzt = time.time()
        if (jetzt - self._getippt >= L.REAKTION_S["tippt"] and not self._beschaeftigt and self._panel is not None
                and self._panel.eingabe.toPlainText().strip()):
            self._getippt = jetzt
            self._reagieren("tippt")

    # ------------------------------------------------------------- Spiel-Begleiterin

    def _frage_taste(self) -> None:
        """Tastenkürzel: Chatfenster auf (auch über einem Spiel) – oder zu, wenn es schon offen ist."""
        if self._panel is not None:
            self._panel_schliessen()
            return
        if not self._gezeigt:
            self.fenster.show()
            self._gezeigt = True
        self._taste_offen = True
        self._panel_oeffnen()

    def _spiel_pruefen(self, s: dict) -> None:
        """/kugel zuschauen an: läuft ein Spiel, alle paar Minuten ein Blick auf den Bildschirm."""
        jetzt = time.time()
        if (self._spiel is None or self._zuschauen is None or jetzt < self._naechstes_zuschauen
                or not L.zuschauen_an()):
            return
        self._naechstes_zuschauen = jetzt + L.zuschau_abstand_s()
        if (self._beschaeftigt or self._panel is not None or s.get("farbe") == "rot"
                or leerlauf_s() > 90):                     # Pause oder weg vom Rechner: nicht zuschauen
            return
        import tempfile
        pfad = Path(tempfile.gettempdir()) / "NemiCLI" / f"spiel_{datetime.now():%Y%m%d_%H%M%S}.png"
        try:
            self.verstecken(True)
            time.sleep(0.3)
            bild = O.foto_aufnehmen()
        except Exception:
            return
        finally:
            self.verstecken(False)
        self._beschaeftigt = True
        self.fenster.beschaeftigt = True

        def vorbereiten():                               # Packen und Prüfen dauern: nicht im GUI-Thread
            try:
                O.foto_speichern(bild, pfad)
                brauchbar = not L.dunkel(pfad)           # exklusives Vollbild: nur Schwarz
            except Exception:
                brauchbar = False
            O.im_gui(lambda: self._spiel_blick(pfad, brauchbar))

        threading.Thread(target=vorbereiten, name="nemi-spielfoto", daemon=True).start()

    def _spiel_blick(self, pfad: Path, brauchbar: bool) -> None:
        if not brauchbar:
            pfad.unlink(missing_ok=True)
            self._beschaeftigt = False
            self.fenster.beschaeftigt = False
            return
        self._ins_chat = False
        _spontan(True)                                   # Grafikspeicher-Regel: das Spiel braucht ihn
        try:
            fut = asyncio.run_coroutine_threadsafe(self._zuschauen(L.spiel_text(self._spiel), str(pfad)), self._loop)
        except Exception:
            _spontan(False)
            pfad.unlink(missing_ok=True)
            self._beschaeftigt = self.fenster.beschaeftigt = False
            return

        def fertig(f):
            _spontan(False)
            try:
                antwort, bilder, todo = _ergebnis(f.result())
            except Exception:
                antwort, bilder, todo = "", [], ""
            pfad.unlink(missing_ok=True)
            O.im_gui(lambda: self._impuls_antwort(antwort, bilder, todo))

        fut.add_done_callback(fertig)

    # ------------------------------------------------------------- Musik

    def _musik_pruefen(self, s: dict) -> None:
        """Läuft ein Stück aus einem bekannten Musik-Programm länger, ab und zu ein Moment dazu."""
        if not self._musik_an or self._gespuer is None:
            return
        jetzt = time.time()
        if self._gespuer.laeuft_seit(jetzt) < MUSIK_AB_S or jetzt < self._naechster_musik_moment:
            return
        if (self._chat is None or self._beschaeftigt or self._panel is not None or self.fenster.schlaeft
                or s.get("farbe") != "gruen" or not 8 <= datetime.now().hour < 23 or vollbild()):
            return
        from . import klang
        stueck = klang.stueck()
        if stueck is None or stueck == self._letztes_stueck:
            self._naechster_musik_moment = jetzt + 5 * 60
            return
        self._letztes_stueck = stueck
        self._naechster_musik_moment = jetzt + random.uniform(*MUSIK_PAUSE_MIN) * 60
        self._naechster_impuls = max(self._naechster_impuls, self._impuls_zeit())
        self._impuls_starten(musik_text(self.name, *stueck))

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
        if self._pegel is not None:
            self._pegel.stop()
        if self._taste is not None:
            self._taste.stop()
        O.im_gui(self.app.quit)
