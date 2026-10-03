"""terminal_fenster.py – NemiCLIs eigenes Terminal-Fenster.

NemiCLI läuft in einer Pseudokonsole (terminal_pty.py); dieses Fenster setzt die
Steuerfolgen mit `pyte` in ein Zeichenraster um, zeichnet es mit PySide6 und
schickt Tasten, Maus, Einfügen, Zeichen aus der Eingabemethode (Emoji-Feld
Win + .) und abgelegte Dateien als Terminal-Folgen zurück. Kein Windows
Terminal, keine fremde Terminal-Software, keine Fremd-DLL.

Einstellungen (nemicli.config.json): `terminal_thema` (breeze | mint),
`terminal_schrift`, `terminal_groesse`, `terminal_hintergrund` (0.3–1.0, Durchsicht
nur des Hintergrunds, verschwommen), `terminal_unschaerfe` (true/false), `terminal_deckkraft`
(0.5–1.0, ganzes Fenster).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pyte

# ---------------------------------------------------------------------------
# Farbschemata
# ---------------------------------------------------------------------------

_NAMEN = ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white")

THEMEN = {
    # KDE Breeze – Standard von Konsole (u. a. CachyOS KDE)
    "breeze": {"hintergrund": "#232627", "vordergrund": "#fcfcfc", "cursor": "#fcfcfc",
               "normal": ["#232627", "#ed1515", "#11d116", "#f67400", "#1d99f3", "#9b59b6", "#1abc9c", "#fcfcfc"],
               "hell": ["#7f8c8d", "#c0392b", "#1cdc9a", "#fdbc4b", "#3daee9", "#8e44ad", "#16a085", "#ffffff"]},
    # Tango – GNOME-Terminal (u. a. Linux Mint)
    "mint": {"hintergrund": "#2b2b2b", "vordergrund": "#e6e6e6", "cursor": "#e6e6e6",
             "normal": ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf"],
             "hell": ["#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"]},
}


def farbe(wert: str, thema: dict, vordergrund: bool) -> str:
    """pyte-Farbe (Name, „brightred“ oder Hex ohne #) → „#rrggbb“."""
    if not wert or wert == "default":
        return thema["vordergrund"] if vordergrund else thema["hintergrund"]
    if wert.startswith("bright") and wert[6:] in _NAMEN:
        return thema["hell"][_NAMEN.index(wert[6:])]
    if wert in _NAMEN:
        return thema["normal"][_NAMEN.index(wert)]
    if len(wert) == 6:
        return "#" + wert
    return thema["vordergrund"] if vordergrund else thema["hintergrund"]


# ---------------------------------------------------------------------------
# Tasten → Terminal-Folgen (reine Funktion, testbar ohne Fenster)
# ---------------------------------------------------------------------------

_PFEILE = {"up": "A", "down": "B", "right": "C", "left": "D"}
_TILDE = {"insert": 2, "delete": 3, "pageup": 5, "pagedown": 6,
          "f5": 15, "f6": 17, "f7": 18, "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24}
_SS3 = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}


def taste_zu_folge(taste: str, text: str = "", *, shift=False, strg=False, alt=False,
                   anwendungs_cursor=False) -> str:
    """Eine Taste (Name wie „up“, „enter“, „f5“ oder leer) mit Text → was ins Terminal geht."""
    mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if strg else 0)
    if taste in _PFEILE:
        if mod > 1:
            return f"\x1b[1;{mod}{_PFEILE[taste]}"
        return ("\x1bO" if anwendungs_cursor else "\x1b[") + _PFEILE[taste]
    if taste in ("home", "end"):
        z = "H" if taste == "home" else "F"
        return f"\x1b[1;{mod}{z}" if mod > 1 else f"\x1b[{z}"
    if taste in _TILDE:
        n = _TILDE[taste]
        return f"\x1b[{n};{mod}~" if mod > 1 else f"\x1b[{n}~"
    if taste in _SS3:
        return f"\x1b[1;{mod}{_SS3[taste]}" if mod > 1 else "\x1bO" + _SS3[taste]
    if taste == "enter":
        return "\x1b\r" if alt else "\r"
    if taste == "backspace":
        return "\x08" if strg else "\x7f"
    if taste == "tab":
        return "\x1b[Z" if shift else "\t"
    if taste == "escape":
        return "\x1b"
    if strg and text and len(text) == 1:
        c = text.lower()
        if "a" <= c <= "z":
            return ("\x1b" if alt else "") + chr(ord(c) - 96)
        steuer = {"[": "\x1b", "\\": "\x1c", "]": "\x1d", "^": "\x1e", "_": "\x1f"}
        if c in steuer:
            return steuer[c]
        if c == " ":
            return "\x00"
    if text:
        return ("\x1b" + text) if alt else text
    return ""


def maus_folge(knopf: int, spalte: int, zeile: int, *, losgelassen=False, bewegung=False,
               shift=False, alt=False, strg=False, sgr=True) -> str:
    """Mausereignis (Knopf 0 links, 1 Mitte, 2 rechts, 64/65 Rad) an Zelle (0-basiert) → Folge."""
    b = knopf + (32 if bewegung else 0) + (4 if shift else 0) + (8 if alt else 0) + (16 if strg else 0)
    x, y = spalte + 1, zeile + 1
    if sgr:
        return f"\x1b[<{b};{x};{y}{'m' if losgelassen else 'M'}"
    if losgelassen:
        b = 3 + (b & ~3)
    return "\x1b[M" + chr(32 + b) + chr(32 + min(x, 223)) + chr(32 + min(y, 223))


def abgelegt_zu_text(pfade: list[str]) -> str:
    """Abgelegte Dateien → Text wie in der Windows-Konsole: Pfade mit Leerzeichen in
    Anführungszeichen, durch Leerzeichen getrennt, eins am Ende zum Weitertippen."""
    teile = [f'"{p}"' if " " in p else p for p in pfade if p]
    return " ".join(teile) + " " if teile else ""


def geordnet(a: tuple, b: tuple) -> tuple:
    """Zwei Zellen (spalte, zeile) in Lesereihenfolge."""
    return (a, b) if (a[1], a[0]) <= (b[1], b[0]) else (b, a)


def auswahl_text(zeilen: list, start: tuple, ende: tuple) -> str:
    """Text zwischen zwei Zellen (einschließlich), Zeile für Zeile wie gelesen.
    `zeilen`: je Zeile die Zellinhalte; leere Zellen breiter Zeichen zählen nicht."""
    (x1, y1), (x2, y2) = geordnet(start, ende)
    raus = []
    for y in range(y1, y2 + 1):
        z = zeilen[y]
        von = x1 if y == y1 else 0
        bis = x2 if y == y2 else len(z) - 1
        raus.append("".join(z[von:bis + 1]).rstrip())
    return "\n".join(raus)


# ---------------------------------------------------------------------------
# Bildschirm: pyte + gemerkte Modi + Antworten ans Programm
# ---------------------------------------------------------------------------

STEUER_TITEL = "nemicli-steuer:"   # Titel „nemicli-steuer:<befehl>:<zähler>“ steuert das Fenster


class Schirm(pyte.Screen):
    """pyte-Bildschirm, der Antworten (Cursor-Position, Gerätekennung) ans Programm
    zurückgibt und die privaten Modi merkt, die pyte selbst nicht auswertet.

    Steuerbefehle der Sitzung kommen als Konsolentitel (SetConsoleTitleW – die
    Pseudokonsole reicht ihn als OSC 0 weiter); der Zähler macht jeden Titel neu."""

    def __init__(self, spalten: int, zeilen: int, antworten=None, steuer=None):
        super().__init__(spalten, zeilen)
        self.antworten = antworten or (lambda _t: None)
        self.steuer = steuer or (lambda _b: None)
        self.privat: set[int] = set()

    def write_process_input(self, data: str) -> None:       # pyte ruft das für Antworten
        self.antworten(data)

    def set_title(self, param: str) -> None:
        if param.startswith(STEUER_TITEL):
            self.steuer(param[len(STEUER_TITEL):].split(":", 1)[0])
            return
        super().set_title(param)

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            self.privat.update(modes)
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            self.privat.difference_update(modes)
        super().reset_mode(*modes, **kwargs)

    # Modi, die das Fenster braucht
    def maus_an(self) -> bool:
        return bool(self.privat & {1000, 1002, 1003})

    def maus_bewegung(self) -> bool:
        return 1003 in self.privat or 1002 in self.privat

    def maus_sgr(self) -> bool:
        return 1006 in self.privat

    def einfuegen_geklammert(self) -> bool:
        return 2004 in self.privat

    def fokus_melden(self) -> bool:
        return 1004 in self.privat

    def anwendungs_cursor(self) -> bool:
        return 1 in self.privat


# ---------------------------------------------------------------------------
# Das Fenster
# ---------------------------------------------------------------------------

def _einstellungen() -> dict:
    try:
        import config
        c = config.load()
    except Exception:
        c = {}
    thema = str(c.get("terminal_thema") or "breeze").lower()
    def zahl(schluessel, standard, unten, oben):
        try:
            return min(oben, max(unten, float(c.get(schluessel, standard))))
        except (TypeError, ValueError):
            return standard
    return {"thema": THEMEN.get(thema, THEMEN["breeze"]),
            "schrift": str(c.get("terminal_schrift") or ""),
            "groesse": int(c.get("terminal_groesse") or 11),
            # ganzes Fenster (auch Schrift) – 1.0 = aus
            "deckkraft": zahl("terminal_deckkraft", 1.0, 0.5, 1.0),
            # nur der Hintergrund, verschwommen (Windows 11 Acrylic) – 1.0 = deckend
            "hintergrund": zahl("terminal_hintergrund", 0.82, 0.3, 1.0),
            "unschaerfe": bool(c.get("terminal_unschaerfe", True))}


def acrylic(hwnd: int) -> bool:
    """Windows-11-Hintergrund „Acrylic“ (verschwommen durchsichtig) und dunkle Titelleiste.
    False, wenn Windows das nicht kann (z. B. Windows 10) – dann bleibt es deckend."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes as w

        class _MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("o", ctypes.c_int), ("u", ctypes.c_int)]
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        dwm.DwmExtendFrameIntoClientArea.argtypes = [w.HWND, ctypes.POINTER(_MARGINS)]
        dunkel = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dunkel), 4)       # DWMWA_USE_IMMERSIVE_DARK_MODE
        art = ctypes.c_int(3)                                               # DWMSBT_TRANSIENTWINDOW = Acrylic
        if dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(art), 4) != 0:  # DWMWA_SYSTEMBACKDROP_TYPE
            return False
        rand = _MARGINS(-1, -1, -1, -1)
        return dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(rand)) == 0
    except Exception:
        return False


def _app_kennung() -> None:
    """Eigene Taskleisten-Identität: NemiCLI erscheint als eigene App, nicht als Python."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NemiCLI.Terminal")
        except Exception:
            pass


def starten(befehl: list[str], *, ordner: str | None = None, env: dict | None = None,
            titel: str = "NemiCLI") -> int:
    """Öffnet das Fenster, startet `befehl` darin und kehrt zurück, wenn es zu ist."""
    _app_kennung()
    from PySide6.QtCore import QRect, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QIcon, QPainter
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
    import terminal_pty

    e = _einstellungen()
    thema = e["thema"]
    app = QApplication.instance() or QApplication(sys.argv[:1])

    class Ansicht(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(titel)
            self.setFocusPolicy(Qt.StrongFocus)
            self.durchsichtig = False
            if e["unschaerfe"] and e["hintergrund"] < 1.0:
                self.setAttribute(Qt.WA_TranslucentBackground)
                self.durchsichtig = acrylic(int(self.winId()))
                if not self.durchsichtig:
                    self.setAttribute(Qt.WA_TranslucentBackground, False)
            if not self.durchsichtig:
                self.setAttribute(Qt.WA_OpaquePaintEvent)
            self.grund = QColor(thema["hintergrund"])
            if self.durchsichtig:
                self.grund.setAlphaF(e["hintergrund"])
            self.setMouseTracking(True)
            self.setAttribute(Qt.WA_InputMethodEnabled, True)     # Emoji-Feld (Win + .), IME
            self.setAcceptDrops(True)
            schrift = QFont(e["schrift"]) if e["schrift"] else QFontDatabase.systemFont(QFontDatabase.FixedFont)
            if not e["schrift"]:
                for name in ("Cascadia Mono", "Cascadia Code", "Consolas"):
                    if name in QFontDatabase.families():
                        schrift = QFont(name)
                        break
            schrift.setPointSize(e["groesse"])
            schrift.setStyleHint(QFont.Monospace)
            self.schrift = schrift
            self.fett = QFont(schrift)
            self.fett.setBold(True)
            m = QFontMetricsF(schrift)
            self.zb, self.zh, self.oben = m.horizontalAdvance("M"), m.height(), m.ascent()
            self.lock = threading.Lock()
            self.schirm = Schirm(100, 30, self._senden, self._steuer)
            self.steuerbefehl = None      # aus dem Lese-Thread, ausgeführt im Takt
            self.tray = None
            self.strom = pyte.Stream(self.schirm)
            self.neu = False
            self.ende = False
            self.pty = None
            self.maus_knopf = None
            # Markieren: Ziehen mit links markiert immer, ein Klick ohne Ziehen geht ans Programm.
            self.druck = None             # (zelle, shift, alt, strg) beim Drücken links
            self.zieht = False
            self.auswahl = None           # (start, ende) in Zellen
            self.resize(int(self.zb * 110 + 16), int(self.zh * 32 + 16))
            self.setWindowOpacity(e["deckkraft"])
            # In der exe liegt das Icon in NemiCLIT2 (sys._MEIPASS), im Quelltext daneben.
            for icon in (Path(getattr(sys, "_MEIPASS", "")) / "nemicli.ico",
                         Path(__file__).resolve().parent.parent / "nemicli.ico"):
                if icon.is_file():
                    self.setWindowIcon(QIcon(str(icon)))
                    app.setWindowIcon(QIcon(str(icon)))
                    break
            self.takt = QTimer(self)
            self.takt.timeout.connect(self._takt)
            self.takt.start(16)

        # -- Programm ------------------------------------------------------------
        def programm_starten(self):
            sp, ze = self._raster()
            with self.lock:
                self.schirm.resize(ze, sp)
            kind_env = dict(env if env is not None else os.environ)
            kind_env.update({"COLORTERM": "truecolor", "TERM": "xterm-256color", "NEMICLI_FENSTER": "1"})
            self.pty = terminal_pty.Pty(befehl, sp, ze, ordner=ordner, env=kind_env,
                                        bei_ausgabe=self._ausgabe, bei_ende=self._beendet)

        def _ausgabe(self, text):                       # Lese-Thread
            with self.lock:
                self.strom.feed(text)
                self.neu = True

        def _beendet(self, _code):                      # Warte-Thread
            self.ende = True

        def _senden(self, text):
            if self.pty is not None and text:
                self.pty.schreiben(text)

        def _steuer(self, befehl):                      # Lese-Thread
            self.steuerbefehl = befehl

        def _in_tray(self, an: bool):
            """Solange das Desktop-Fenster (/gui) offen ist: Terminal aus dem Weg, Symbol im Tray."""
            if an:
                if self.tray is None:
                    self.tray = QSystemTrayIcon(self.windowIcon(), self)
                    self.tray.setToolTip("NemiCLI – läuft (Desktop-Fenster offen)")
                    self.tray_menue = QMenu()                 # setContextMenu übernimmt es nicht
                    self.tray_menue.addAction("Terminal zeigen", lambda: self._in_tray(False))
                    self.tray_menue.addSeparator()
                    self.tray_menue.addAction("NemiCLI beenden", self.close)
                    self.tray.setContextMenu(self.tray_menue)
                    self.tray.activated.connect(
                        lambda grund: self._in_tray(False)
                        if grund in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
                self.tray.show()
                self.hide()
            else:
                self.showNormal()
                self.raise_()
                self.activateWindow()
                if self.tray is not None:
                    self.tray.hide()

        def _takt(self):
            if self.ende:
                self.takt.stop()
                if self.tray is not None:
                    self.tray.hide()
                self.close()
                return
            befehl, self.steuerbefehl = self.steuerbefehl, None
            if befehl in ("tray", "zeigen"):
                self._in_tray(befehl == "tray")
            if self.neu:
                self.neu = False
                self.update()

        # -- Raster --------------------------------------------------------------
        def _raster(self):
            return max(20, int((self.width() - 16) // self.zb)), max(5, int((self.height() - 16) // self.zh))

        def _zelle(self, pos):
            sp = int((pos.x() - 8) // self.zb)
            ze = int((pos.y() - 8) // self.zh)
            return max(0, min(sp, self.schirm.columns - 1)), max(0, min(ze, self.schirm.lines - 1))

        def resizeEvent(self, ev):
            sp, ze = self._raster()
            if (sp, ze) != (self.schirm.columns, self.schirm.lines):
                with self.lock:
                    self.schirm.resize(ze, sp)
                if self.pty is not None:
                    self.pty.groesse(sp, ze)
            super().resizeEvent(ev)

        # -- Zeichnen ------------------------------------------------------------
        def paintEvent(self, _ev):
            p = QPainter(self)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(self.rect(), self.grund)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            with self.lock:
                s = self.schirm
                for y in range(s.lines):
                    zeile = s.buffer[y]
                    x = 0
                    while x < s.columns:
                        z = zeile[x]
                        stil = (z.fg, z.bg, z.bold, z.italics, z.underscore, z.reverse)
                        start, zellen = x, [z.data]
                        x += 1
                        while x < s.columns:
                            n = zeile[x]
                            if (n.fg, n.bg, n.bold, n.italics, n.underscore, n.reverse) != stil:
                                break
                            zellen.append(n.data)
                            x += 1
                        self._lauf(p, start, y, x - start, zellen, stil)
                if self.auswahl:
                    (x1, y1), (x2, y2) = geordnet(*self.auswahl)
                    farbe_a = QColor(thema["hell"][4])
                    farbe_a.setAlpha(110)
                    for y in range(y1, y2 + 1):
                        von = x1 if y == y1 else 0
                        bis = x2 if y == y2 else s.columns - 1
                        p.fillRect(QRectF(8 + von * self.zb, 8 + y * self.zh,
                                          (bis - von + 1) * self.zb, self.zh), farbe_a)
                c = s.cursor
                if not c.hidden and 0 <= c.y < s.lines and 0 <= c.x < s.columns:
                    r = QRectF(8 + c.x * self.zb, 8 + c.y * self.zh, self.zb, self.zh)
                    farbe_c = QColor(thema["cursor"])
                    if self.hasFocus():
                        farbe_c.setAlpha(170)
                        p.fillRect(r, farbe_c)
                    else:
                        p.setPen(farbe_c)
                        p.drawRect(r.adjusted(0, 0, -1, -1))
            p.end()

        def _lauf(self, p, x, y, breite, zellen, stil):
            fg, bg, fett, kursiv, unter, umgekehrt = stil
            vorn, hinten = farbe(fg, thema, True), farbe(bg, thema, False)
            if umgekehrt:
                vorn, hinten = hinten, vorn
            r = QRectF(8 + x * self.zb, 8 + y * self.zh, breite * self.zb, self.zh)
            if hinten != thema["hintergrund"]:
                p.fillRect(r, QColor(hinten))
            if any(ch.strip() for ch in zellen):
                f = self.fett if fett else self.schrift
                if kursiv:
                    f = QFont(f)
                    f.setItalic(True)
                p.setFont(f)
                p.setPen(QColor(vorn))
                # Jede Zelle an ihre Rasterposition. Breite Zeichen (Emojis) belegen zwei
                # Zellen, die zweite ist leer – sie zählt trotzdem mit, sonst rutscht der Rest.
                for i, ch in enumerate(zellen):
                    if ch and ch != " ":
                        p.drawText(r.left() + i * self.zb, r.top() + self.oben, ch)
                if unter:
                    p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)

        # -- Tastatur ------------------------------------------------------------
        _QT_NAMEN = {Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
                     Qt.Key_Home: "home", Qt.Key_End: "end", Qt.Key_PageUp: "pageup",
                     Qt.Key_PageDown: "pagedown", Qt.Key_Insert: "insert", Qt.Key_Delete: "delete",
                     Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Backspace: "backspace",
                     Qt.Key_Tab: "tab", Qt.Key_Backtab: "tab", Qt.Key_Escape: "escape",
                     **{getattr(Qt, f"Key_F{i}"): f"f{i}" for i in range(1, 13)}}

        def event(self, ev):
            # Tab und Shift+Tab gehören dem Programm, nicht dem Fokuswechsel von Qt.
            from PySide6.QtCore import QEvent
            if ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Tab, Qt.Key_Backtab):
                self.keyPressEvent(ev)
                return True
            return super().event(ev)

        def keyPressEvent(self, ev):
            mods = ev.modifiers()
            shift = bool(mods & Qt.ShiftModifier) or ev.key() == Qt.Key_Backtab
            strg = bool(mods & Qt.ControlModifier)
            alt = bool(mods & Qt.AltModifier)
            if strg and not alt and ev.key() == Qt.Key_V or (shift and ev.key() == Qt.Key_Insert):
                self._einfuegen()
                return
            if strg and not alt and ev.key() == Qt.Key_C:
                # Wie unter Windows gewohnt: Strg+C kopiert und beendet nie – als Signal
                # durchgereicht würde es NemiCLI sofort schließen. Abbrechen geht mit Esc.
                self._kopieren()
                self.auswahl = None
                self.update()
                return
            if self.auswahl:
                self.auswahl = None
                self.update()
            name = self._QT_NAMEN.get(ev.key(), "")
            text = ev.text()
            if strg and not text and Qt.Key_A <= ev.key() <= Qt.Key_Z:
                text = chr(ev.key()).lower()
            folge = taste_zu_folge(name, text, shift=shift, strg=strg, alt=alt,
                                   anwendungs_cursor=self.schirm.anwendungs_cursor())
            self._senden(folge)

        def _kopieren(self):
            if not self.auswahl:
                return
            with self.lock:
                s = self.schirm
                zeilen = [[s.buffer[y][x].data for x in range(s.columns)] for y in range(s.lines)]
            text = auswahl_text(zeilen, *self.auswahl)
            if text:
                QGuiApplication.clipboard().setText(text)

        def _einfuegen(self):
            self._text_einfuegen(QGuiApplication.clipboard().text())

        def _text_einfuegen(self, text):
            if not text:
                return
            text = text.replace("\r\n", "\r").replace("\n", "\r")
            if self.schirm.einfuegen_geklammert():
                text = "\x1b[200~" + text + "\x1b[201~"
            self._senden(text)

        # -- Eingabemethode (Emoji-Feld, IME) ---------------------------------------
        def inputMethodEvent(self, ev):
            if ev.commitString():
                if self.auswahl:
                    self.auswahl = None
                    self.update()
                self._senden(ev.commitString())
            ev.accept()

        def inputMethodQuery(self, frage):
            if frage == Qt.ImCursorRectangle:            # Emoji-Feld erscheint am Cursor
                c = self.schirm.cursor
                return QRect(int(8 + c.x * self.zb), int(8 + c.y * self.zh), int(self.zb), int(self.zh))
            if frage == Qt.ImEnabled:
                return True
            return super().inputMethodQuery(frage)

        # -- Ablegen (Drag & Drop) ---------------------------------------------------
        def _abgelegt(self, mime):
            dateien = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
            if dateien:
                return abgelegt_zu_text([os.path.normpath(d) for d in dateien])
            return mime.text() if mime.hasText() else ""

        def dragEnterEvent(self, ev):
            if self._abgelegt(ev.mimeData()):
                ev.acceptProposedAction()

        def dragMoveEvent(self, ev):
            ev.acceptProposedAction()

        def dropEvent(self, ev):
            text = self._abgelegt(ev.mimeData())
            if text:
                ev.acceptProposedAction()
                self._text_einfuegen(text)
                self.activateWindow()
                self.setFocus()

        def focusInEvent(self, ev):
            if self.schirm.fokus_melden():
                self._senden("\x1b[I")
            self.update()
            super().focusInEvent(ev)

        def focusOutEvent(self, ev):
            if self.schirm.fokus_melden():
                self._senden("\x1b[O")
            self.update()
            super().focusOutEvent(ev)

        # -- Maus ----------------------------------------------------------------
        _KNOPF = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}

        @staticmethod
        def _tasten(ev):
            m = ev.modifiers()
            return (bool(m & Qt.ShiftModifier), bool(m & Qt.AltModifier), bool(m & Qt.ControlModifier))

        def _maus_an(self, knopf, zelle, tasten, **kw):
            if not self.schirm.maus_an():
                return
            shift, alt, strg = tasten
            self._senden(maus_folge(knopf, zelle[0], zelle[1], shift=shift, alt=alt, strg=strg,
                                    sgr=self.schirm.maus_sgr(), **kw))

        def _maus(self, ev, knopf, **kw):
            self._maus_an(knopf, self._zelle(ev.position()), self._tasten(ev), **kw)

        def mousePressEvent(self, ev):
            k = self._KNOPF.get(ev.button())
            if k == 0:
                # Noch nicht weitergeben: erst beim Loslassen steht fest, ob es ein Klick
                # oder eine Markierung war.
                self.druck = (self._zelle(ev.position()), self._tasten(ev))
                self.zieht = False
                if self.auswahl:
                    self.auswahl = None
                    self.update()
                return
            if k is not None:
                self.maus_knopf = k
                self._maus(ev, k)

        def mouseReleaseEvent(self, ev):
            k = self._KNOPF.get(ev.button())
            if k == 0 and self.druck is not None:
                zelle, tasten = self.druck
                self.druck = None
                if self.zieht:
                    self.zieht = False
                    self._kopieren()             # wie unter Linux: markiert = kopiert
                else:
                    self._maus_an(0, zelle, tasten)
                    self._maus_an(0, self._zelle(ev.position()), self._tasten(ev), losgelassen=True)
                return
            if k is not None:
                self._maus(ev, k, losgelassen=True)
                self.maus_knopf = None

        def mouseMoveEvent(self, ev):
            if self.druck is not None and ev.buttons() & Qt.LeftButton:
                zelle = self._zelle(ev.position())
                if self.zieht or zelle != self.druck[0]:
                    self.zieht = True
                    self.auswahl = (self.druck[0], zelle)
                    self.update()
                return
            if self.schirm.maus_bewegung():
                self._maus(ev, self.maus_knopf if self.maus_knopf is not None else 3, bewegung=True)

        def wheelEvent(self, ev):
            if self.schirm.maus_an():
                self._maus(ev, 64 if ev.angleDelta().y() > 0 else 65)
            else:
                self._senden(taste_zu_folge("up" if ev.angleDelta().y() > 0 else "down"))

        def closeEvent(self, ev):
            if self.pty is not None and self.pty.laeuft():
                self.pty.beenden()
            super().closeEvent(ev)

    fenster = Ansicht()
    fenster.show()
    fenster.programm_starten()
    fenster.setFocus()
    app.exec()
    return fenster.pty.exitcode or 0 if fenster.pty is not None else 0
