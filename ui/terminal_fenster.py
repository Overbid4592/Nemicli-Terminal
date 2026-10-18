"""terminal_fenster.py – NemiCLIs eigenes Terminal-Fenster.

NemiCLI läuft in einer Pseudokonsole (terminal_pty.py); dieses Fenster setzt die
Steuerfolgen mit `pyte` in ein Zeichenraster um, zeichnet es mit PySide6 und
schickt Tasten, Maus, Einfügen, Zeichen aus der Eingabemethode (Emoji-Feld
Win + .) und abgelegte Dateien als Terminal-Folgen zurück. Kein Windows
Terminal, keine fremde Terminal-Software, keine Fremd-DLL.

Einstellungen (nemicli.config.json): `terminal_thema` (breeze | mint),
`terminal_schrift`, `terminal_groesse`, `terminal_hintergrund` (0.3–1.0, Durchsicht
nur des Hintergrunds, verschwommen), `terminal_unschaerfe` (true/false), `terminal_deckkraft`
(0.5–1.0, ganzes Fenster), `terminal_ki_reiter` (true/false).

Reiter wie bei GNOME-Terminal: die Leiste erscheint ab dem zweiten. Strg+Umschalt+T öffnet eine
Kommandozeile, Strg+Umschalt+W schließt, Strg+Bild↑/↓ wechselt. „🤖 KI“ (Strg+Umschalt+K) zeigt die
Befehle der KI live – NemiCLI meldet sie über eine Named Pipe (core/ki_terminal.py).
"""

from __future__ import annotations

import base64
import os
import re
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
        raus.append("".join(c for c in z[von:bis + 1] if not ist_platzhalter(c)).rstrip())
    return "\n".join(raus)


# ---------------------------------------------------------------------------
# Echte Bilder: Die Sitzung füllt die Bildstelle mit Platzhaltern – Zeichen U+E000 + n·64 + z
# (n = Bild-Nummer, z = Zeile im Bild) – und schickt den Pfad als Steuerbefehl
# „bild<n>-<Zeilen>=<Pfad, base64>“. Das Fenster malt das Bild über die Platzhalter, scharf statt
# aus Zeichen-Blöcken, ohne Sixel oder Zusatzpaket. Aus der Zeilennummer weiß es, wo das ganze Bild
# säße, wenn ein Teil schon hinausgerollt ist – es wird angeschnitten, nicht gestaucht.
# ---------------------------------------------------------------------------

PLATZ_ERSTES, PLATZ_BILDER, PLATZ_ZEILEN = 0xE000, 8, 64
BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
BILD_MAX = 50 * 1024 * 1024


def ist_platzhalter(zeichen: str) -> bool:
    return len(zeichen) == 1 and PLATZ_ERSTES <= ord(zeichen) < PLATZ_ERSTES + PLATZ_BILDER * PLATZ_ZEILEN


def platzhalter(nr: int, zeile: int) -> str:
    return chr(PLATZ_ERSTES + nr * PLATZ_ZEILEN + zeile)


def bild_befehl(befehl: str) -> tuple[int, int, Path | None] | None:
    """„bild<n>-<Zeilen>=<base64>“ -> (n, Zeilen, Pfad); leerer Pfad -> Pfad None = Bild weg.
    None: kein Bild-Befehl oder keine lesbare Bilddatei."""
    m = re.fullmatch(r"bild(\d)-(\d{1,2})=([A-Za-z0-9_=-]*)", befehl or "")
    if not m or int(m.group(1)) >= PLATZ_BILDER or not 1 <= int(m.group(2)) <= PLATZ_ZEILEN:
        return None
    nr, hoehe = int(m.group(1)), int(m.group(2))
    if not m.group(3):
        return nr, hoehe, None
    try:
        pfad = Path(base64.urlsafe_b64decode(m.group(3)).decode("utf-8"))
        if pfad.suffix.lower() in BILD_ENDUNGEN and pfad.is_file() and pfad.stat().st_size <= BILD_MAX:
            return nr, hoehe, pfad
    except (ValueError, OSError):
        pass
    return None


def platzhalter_kaesten(zeilen: list) -> dict[int, tuple[int, int, int, int, int]]:
    """Je Bild-Nummer der sichtbare Kasten (x0, y0, x1, y1) in Zellen und die oberste sichtbare Bildzeile."""
    kaesten: dict[int, list[int]] = {}
    for y, z in enumerate(zeilen):
        for x, c in enumerate(z):
            if ist_platzhalter(c):
                nr, zeile = divmod(ord(c) - PLATZ_ERSTES, PLATZ_ZEILEN)
                k = kaesten.get(nr)
                if k is None:
                    kaesten[nr] = [x, y, x, y, zeile]
                else:
                    k[0], k[1], k[2], k[3] = min(k[0], x), min(k[1], y), max(k[2], x), max(k[3], y)
                    k[4] = min(k[4], zeile)
    return {n: tuple(k) for n, k in kaesten.items()}


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
            "unschaerfe": bool(c.get("terminal_unschaerfe", True)),
            # Reiter „KI“: Befehle der KI live mitlesen
            "ki_reiter": bool(c.get("terminal_ki_reiter", True))}


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


class KiSchirm(pyte.HistoryScreen):
    """Bildschirm des Reiters „KI“: nur zum Lesen, mit Verlauf zum Zurückrollen. Er antwortet nicht,
    kennt keine Maus- und Steuer-Modi – Ausgaben der Befehle können dort nichts auslösen."""

    VERLAUF = 5000

    def __init__(self, spalten: int, zeilen: int):
        super().__init__(spalten, zeilen, history=self.VERLAUF, ratio=0.5)

    def write_process_input(self, data: str) -> None:
        pass

    def maus_an(self) -> bool:
        return False

    maus_bewegung = maus_sgr = einfuegen_geklammert = fokus_melden = anwendungs_cursor = maus_an


def ki_text(ereignis: dict) -> str:
    """Ereignis von ki_terminal.py -> Terminal-Text (CR LF, Farben)."""
    t = ereignis.get("t")
    if t == "start":
        befehl = str(ereignis.get("befehl") or "").replace("\r\n", "\n").replace("\n", "\r\n   ")
        ordner = str(ereignis.get("ordner") or "")
        kopf = f"\x1b[2m{ordner}\x1b[0m\r\n" if ordner else ""
        return f"\r\n{kopf}\x1b[1;36m❯\x1b[0m \x1b[1m{befehl}\x1b[0m\r\n"
    if t == "aus":
        text = str(ereignis.get("text") or "").replace("\r\n", "\n").replace("\n", "\r\n")
        return f"\x1b[31m{text}\x1b[0m" if ereignis.get("fehler") else text
    if t == "ende":
        dauer = str(ereignis.get("sekunden", "")).replace(".", ",")
        if ereignis.get("abgebrochen"):
            return f"\x1b[33m⏹ abgebrochen\x1b[0m \x1b[2m({dauer} s)\x1b[0m\r\n"
        code = ereignis.get("code")
        if code in (0, 250):
            return f"\x1b[32m✓ fertig\x1b[0m \x1b[2m({dauer} s)\x1b[0m\r\n"
        return f"\x1b[31m✗ Exitcode {code}\x1b[0m \x1b[2m({dauer} s)\x1b[0m\r\n"
    return ""


class KiLeitung:
    """Named Pipe, über die NemiCLI die Befehle der KI meldet (core/ki_terminal.py). Jede Verbindung muss
    den Schlüssel beweisen (multiprocessing-Challenge); danach kommen nur JSON-Ereignisse."""

    MAX_NACHRICHT = 1 << 20

    def __init__(self):
        import secrets
        from multiprocessing.connection import Listener
        self.adresse = r"\\.\pipe\nemicli-ki-" + secrets.token_hex(8)
        self.schluessel = secrets.token_bytes(32)
        self._listener = Listener(self.adresse, family="AF_PIPE", authkey=self.schluessel)
        self._lock = threading.Lock()
        self._ereignisse: list[dict] = []
        threading.Thread(target=self._annehmen, name="ki-annehmen", daemon=True).start()

    def umgebung(self) -> dict:
        return {"NEMICLI_KI_PIPE": self.adresse, "NEMICLI_KI_KEY": self.schluessel.hex()}

    def _annehmen(self) -> None:
        while True:
            try:
                conn = self._listener.accept()
            except Exception:
                return
            threading.Thread(target=self._lesen, args=(conn,), name="ki-lesen", daemon=True).start()

    def _lesen(self, conn) -> None:
        import json
        try:
            while True:
                ereignis = json.loads(conn.recv_bytes(self.MAX_NACHRICHT).decode("utf-8"))
                if isinstance(ereignis, dict):
                    with self._lock:
                        self._ereignisse.append(ereignis)
        except Exception:
            pass

    def holen(self) -> list[dict]:
        with self._lock:
            ereignisse, self._ereignisse = self._ereignisse, []
        return ereignisse


REITER_ZEICHEN = 28
PROTOKOLL_MAX = 2_000_000            # Zeichen, die der Reiter „KI“ für den Neuaufbau behält


def reiter_name(titel: str) -> str:
    """Fenstertitel eines Programms -> kurzer Reitername (Pfade nur mit dem letzten Teil)."""
    titel = titel.strip()
    if re.match(r"^[A-Za-z]:[\\/]|^[\\/]", titel):
        titel = re.split(r"[\\/]", titel.split(" - ")[0].rstrip("\\/"))[-1] + (
            " - " + titel.split(" - ", 1)[1] if " - " in titel else "")
    return titel if len(titel) <= REITER_ZEICHEN else titel[:REITER_ZEICHEN - 1] + "…"


def _shell() -> list[str]:
    """Befehl für einen neuen Reiter: die Windows-Kommandozeile aus COMSPEC."""
    return [os.environ.get("COMSPEC") or "cmd.exe"]


def starten(befehl: list[str], *, ordner: str | None = None, env: dict | None = None,
            titel: str = "NemiCLI") -> int:
    """Öffnet das Fenster, startet `befehl` darin und kehrt zurück, wenn es zu ist.
    Weitere Reiter: Kommandozeile (Strg+Umschalt+T) und „🤖 KI“ (Befehle der KI live, nur lesen)."""
    _app_kennung()
    from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
    from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QIcon, QKeySequence,
                               QPainter, QPixmap, QShortcut)
    from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMenu, QStackedWidget, QSystemTrayIcon, QTabBar,
                                   QToolButton, QVBoxLayout, QWidget)
    import terminal_pty

    e = _einstellungen()
    thema = e["thema"]
    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        ki_leitung = KiLeitung() if e["ki_reiter"] else None
    except Exception:
        ki_leitung = None

    class Ansicht(QWidget):
        """Ein Reiter: Programm in einer Pseudokonsole – oder nur zum Lesen (Reiter „KI“)."""

        def __init__(self, fenster, programm=None, *, kind_env=None, nur_lesen=False, haupt=False):
            super().__init__(fenster)
            self.fenster, self.programm, self.kind_env = fenster, programm, kind_env
            self.nur_lesen, self.haupt = nur_lesen, haupt
            self.setFocusPolicy(Qt.StrongFocus)
            if not fenster.durchsichtig:
                self.setAttribute(Qt.WA_OpaquePaintEvent)
            self.grund = QColor(thema["hintergrund"])
            if fenster.durchsichtig:
                self.grund.setAlphaF(e["hintergrund"])
            self.setMouseTracking(True)
            self.setAttribute(Qt.WA_InputMethodEnabled, True)     # Emoji-Feld (Win + .), IME
            self.setAcceptDrops(not nur_lesen)
            self.schrift, self.fett = fenster.schrift, fenster.fett
            self.zb, self.zh, self.oben = fenster.zb, fenster.zh, fenster.oben
            self.lock = threading.Lock()
            self.schirm = KiSchirm(100, 30) if nur_lesen else Schirm(100, 30, self._senden, self._steuer)
            self.titel_alt = ""
            self.protokoll: list[str] = []            # Reiter „KI“: alles Eingespeiste
            self.protokoll_laenge = 0
            self.steuerbefehle = []       # aus dem Lese-Thread, ausgeführt im Takt
            self.bilder = {}              # Bild-Nummer -> QPixmap (echte Bilder über Platzhaltern)
            self.bilder_skaliert = {}
            self.strom = pyte.Stream(self.schirm)
            self.neu = False
            self.ende = False
            self.pty = None
            self.maus_knopf = None
            # Markieren: Will das Programm die Maus (NemiCLI), bekommt es auch das Ziehen – es markiert
            # selbst, rollt am Rand mit und kopiert. Sonst – oder mit Umschalt – markiert das Fenster
            # (nur Sichtbares); ein Klick ohne Ziehen geht ans Programm.
            self.druck = None             # (zelle, shift, alt, strg) beim Drücken links
            self.zieht = False
            self.auswahl = None           # (start, ende) in Zellen
            self.takt = QTimer(self)
            self.takt.timeout.connect(self._takt)
            self.takt.start(16)

        # -- Programm ------------------------------------------------------------
        def programm_starten(self):
            sp, ze = self._raster()
            with self.lock:
                self.schirm.resize(ze, sp)
            kind_env = dict(self.kind_env if self.kind_env is not None else os.environ)
            kind_env.update({"COLORTERM": "truecolor", "TERM": "xterm-256color"})
            self.pty = terminal_pty.Pty(self.programm, sp, ze, ordner=ordner, env=kind_env,
                                        bei_ausgabe=self._ausgabe, bei_ende=self._beendet)

        def _ausgabe(self, text):                       # Lese-Thread
            with self.lock:
                self.strom.feed(text)
                self.neu = True

        def einspeisen(self, text):
            """Reiter „KI“: Text ohne Programm. Er bleibt gemerkt – bei neuer Größe wird der Bildschirm
            daraus neu aufgebaut (pyte verliert beim Verkleinern Zeilen; ein Programm zeichnet selbst neu)."""
            self.protokoll.append(text)
            self.protokoll_laenge += len(text)
            while self.protokoll_laenge > PROTOKOLL_MAX and len(self.protokoll) > 1:
                self.protokoll_laenge -= len(self.protokoll.pop(0))
            self._ausgabe(text)

        def _beendet(self, _code):                      # Warte-Thread
            self.ende = True

        def _senden(self, text):
            if self.pty is not None and text and not self.nur_lesen:
                self.pty.schreiben(text)

        def _steuer(self, befehl):                      # Lese-Thread
            self.steuerbefehle.append(befehl)

        def beenden(self):
            if self.pty is not None and self.pty.laeuft():
                self.pty.beenden()

        def _takt(self):
            if self.ende:
                self.takt.stop()
                self.fenster.programm_zu(self)
                return
            befehle, self.steuerbefehle = self.steuerbefehle, []
            for befehl in befehle:
                if befehl in ("tray", "zeigen"):
                    self.fenster.in_tray(befehl == "tray")
                elif (bild := bild_befehl(befehl)) is not None:
                    self._bild_setzen(*bild)
            if not self.haupt and not self.nur_lesen and self.schirm.title != self.titel_alt:
                self.titel_alt = self.schirm.title
                self.fenster.reiter_benennen(self, self.titel_alt)
            if self.neu:
                self.neu = False
                self.update()

        def _bild_setzen(self, nr, hoehe, pfad):
            self.bilder_skaliert = {k: v for k, v in self.bilder_skaliert.items() if k[0] != nr}
            bild = QPixmap(str(pfad)) if pfad is not None else None
            if bild is None or bild.isNull():
                self.bilder.pop(nr, None)
            else:
                self.bilder[nr] = (bild, hoehe)
            self.update()

        def _bilder_malen(self, p, zeilen):
            """Echte Bilder über ihre Platzhalter: eingepasst in den ganzen Bildkasten (auch den
            hinausgerollten Teil), gezeichnet nur im sichtbaren."""
            if not self.bilder:
                return
            dpr = self.devicePixelRatioF()
            for nr, (x0, y0, x1, y1, erste) in platzhalter_kaesten(zeilen).items():
                if nr not in self.bilder:
                    continue
                bild, hoehe = self.bilder[nr]
                sichtbar = QRectF(8 + x0 * self.zb, 8 + y0 * self.zh,
                                  (x1 - x0 + 1) * self.zb, (y1 - y0 + 1) * self.zh)
                ganz = QRectF(sichtbar.x(), sichtbar.y() - erste * self.zh, sichtbar.width(), hoehe * self.zh)
                f = min(ganz.width() / max(1, bild.width()), ganz.height() / max(1, bild.height()))
                b, h = bild.width() * f, bild.height() * f
                schluessel = (nr, int(b * dpr), int(h * dpr))
                klein = self.bilder_skaliert.get(schluessel)
                if klein is None:
                    klein = bild.scaled(schluessel[1], schluessel[2], Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    klein.setDevicePixelRatio(dpr)
                    self.bilder_skaliert[schluessel] = klein
                p.save()
                p.setClipRect(sichtbar)
                p.drawPixmap(QPointF(ganz.x() + (ganz.width() - b) / 2, ganz.y() + (ganz.height() - h) / 2), klein)
                p.restore()

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
                    if self.nur_lesen:
                        self.schirm = KiSchirm(sp, ze)
                        self.strom = pyte.Stream(self.schirm)
                        self.strom.feed("".join(self.protokoll))
                    else:
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
                self._bilder_malen(p, [[z.data for z in (s.buffer[y][x] for x in range(s.columns))]
                                       for y in range(s.lines)])
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
                if not self.nur_lesen and not c.hidden and 0 <= c.y < s.lines and 0 <= c.x < s.columns:
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
                    if ch and ch != " " and not ist_platzhalter(ch):
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
            if self.nur_lesen:
                if ev.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
                    self._blaettern(ev.key() == Qt.Key_PageUp)
                return
            name = self._QT_NAMEN.get(ev.key(), "")
            text = ev.text()
            if strg and not text and Qt.Key_A <= ev.key() <= Qt.Key_Z:
                text = chr(ev.key()).lower()
            folge = taste_zu_folge(name, text, shift=shift, strg=strg, alt=alt,
                                   anwendungs_cursor=self.schirm.anwendungs_cursor())
            self._senden(folge)

        def _blaettern(self, zurueck: bool):
            with self.lock:
                (self.schirm.prev_page if zurueck else self.schirm.next_page)()
            self.update()

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
            if not text or self.nur_lesen:
                return
            text = text.replace("\r\n", "\r").replace("\n", "\r")
            if self.schirm.einfuegen_geklammert():
                text = "\x1b[200~" + text + "\x1b[201~"
            self._senden(text)

        # -- Eingabemethode (Emoji-Feld, IME) ---------------------------------------
        def inputMethodEvent(self, ev):
            if ev.commitString() and not self.nur_lesen:
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
                return not self.nur_lesen
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
                self.window().activateWindow()
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

        def _programm_markiert(self, ev) -> bool:
            """Das Programm markiert selbst (Maus mit Bewegung angefordert, ohne Umschalt)."""
            return self.schirm.maus_an() and self.schirm.maus_bewegung() and not self._tasten(ev)[0]

        def mousePressEvent(self, ev):
            k = self._KNOPF.get(ev.button())
            if k == 0 and self._programm_markiert(ev):
                if self.auswahl:
                    self.auswahl = None
                    self.update()
                self.druck = None
                self.maus_knopf = 0
                self._maus(ev, 0)
                return
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
            if self.nur_lesen:
                self._blaettern(ev.angleDelta().y() > 0)
            elif self.schirm.maus_an():
                self._maus(ev, 64 if ev.angleDelta().y() > 0 else 65)
            else:
                self._senden(taste_zu_folge("up" if ev.angleDelta().y() > 0 else "down"))

    class Fenster(QWidget):
        """Das Fenster: Reiterleiste (ab zwei Reitern sichtbar), Durchsicht, Tray, Tastenkürzel."""

        def __init__(self):
            super().__init__()
            self.setWindowTitle(titel)
            self.durchsichtig = False
            if e["unschaerfe"] and e["hintergrund"] < 1.0:
                self.setAttribute(Qt.WA_TranslucentBackground)
                self.durchsichtig = acrylic(int(self.winId()))
                if not self.durchsichtig:
                    self.setAttribute(Qt.WA_TranslucentBackground, False)
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
            self.tray = None
            self.haupt = None
            self.ki = None
            self.ki_zu = False            # Reiter „KI“ geschlossen: erst Strg+Umschalt+K öffnet ihn wieder
            self.reiter: list = []
            self.resize(int(self.zb * 110 + 16), int(self.zh * 32 + 16))
            self.setWindowOpacity(e["deckkraft"])
            # In der exe liegt das Icon in NemiCLIT2 (sys._MEIPASS), im Quelltext daneben.
            for icon in (Path(getattr(sys, "_MEIPASS", "")) / "nemicli.ico",
                         Path(__file__).resolve().parent.parent / "nemicli.ico"):
                if icon.is_file():
                    self.setWindowIcon(QIcon(str(icon)))
                    app.setWindowIcon(QIcon(str(icon)))
                    break
            spalte = QVBoxLayout(self)
            spalte.setContentsMargins(0, 0, 0, 0)
            spalte.setSpacing(0)
            self.leiste = QWidget(self)
            self.leiste.setAttribute(Qt.WA_StyledBackground, True)
            reihe = QHBoxLayout(self.leiste)
            reihe.setContentsMargins(6, 4, 6, 0)
            reihe.setSpacing(4)
            self.tabs = QTabBar(self.leiste)
            self.tabs.setTabsClosable(True)
            self.tabs.setMovable(True)
            self.tabs.setExpanding(False)
            self.tabs.setDrawBase(False)
            self.tabs.setFocusPolicy(Qt.NoFocus)
            self.tabs.currentChanged.connect(self._gewechselt)
            self.tabs.tabCloseRequested.connect(lambda i: self.reiter_schliessen(self._ansicht(i)))
            self.tabs.tabMoved.connect(self._verschoben)
            plus = QToolButton(self.leiste)
            plus.setText("+")
            plus.setToolTip("Neue Kommandozeile (Strg+Umschalt+T)")
            plus.setFocusPolicy(Qt.NoFocus)
            plus.clicked.connect(self.neue_shell)
            reihe.addWidget(self.tabs, 1)
            reihe.addWidget(plus)
            self.leiste.setStyleSheet(self._leisten_stil())
            self.leiste.hide()
            self.stapel = QStackedWidget(self)
            spalte.addWidget(self.leiste)
            spalte.addWidget(self.stapel, 1)
            for tasten, aktion in (("Ctrl+Shift+T", self.neue_shell), ("Ctrl+Shift+W", self._aktuellen_schliessen),
                                   ("Ctrl+PgDown", lambda: self._weiter(1)), ("Ctrl+PgUp", lambda: self._weiter(-1)),
                                   ("Ctrl+Shift+K", self.ki_zeigen)):
                kurz = QShortcut(QKeySequence(tasten), self)
                kurz.setContext(Qt.WindowShortcut)
                kurz.activated.connect(aktion)
            if ki_leitung is not None:
                self.ki_takt = QTimer(self)
                self.ki_takt.timeout.connect(self._ki_ereignisse)
                self.ki_takt.start(50)

        def _leisten_stil(self) -> str:
            grund = QColor(thema["hintergrund"])
            if self.durchsichtig:
                grund.setAlphaF(e["hintergrund"])
            gewaehlt = QColor(thema["hintergrund"]).lighter(140)
            return (f"QWidget {{ background: rgba({grund.red()},{grund.green()},{grund.blue()},{grund.alpha()}); }}"
                    f"QTabBar::tab {{ background: transparent; color: {thema['normal'][7]}; padding: 4px 12px;"
                    f" border: none; border-bottom: 2px solid transparent; margin-right: 2px; }}"
                    f"QTabBar::tab:selected {{ color: {thema['vordergrund']}; background: {gewaehlt.name()};"
                    f" border-bottom: 2px solid {thema['hell'][4]}; border-radius: 4px; }}"
                    f"QTabBar::tab:hover {{ color: {thema['vordergrund']}; }}"
                    f"QToolButton {{ color: {thema['vordergrund']}; background: transparent; border: none;"
                    f" padding: 2px 8px; font-size: 14px; }}"
                    f"QToolButton:hover {{ background: {gewaehlt.name()}; border-radius: 4px; }}")

        # -- Reiter --------------------------------------------------------------
        def _ansicht(self, i: int):
            return self.reiter[i] if 0 <= i < len(self.reiter) else None

        def hinzufuegen(self, ansicht, name: str, zeigen: bool = True):
            self.reiter.append(ansicht)
            self.stapel.addWidget(ansicht)
            i = self.tabs.addTab(name)
            if ansicht.haupt:                              # NemiCLI selbst schließt man über das Fenster
                for seite in (QTabBar.RightSide, QTabBar.LeftSide):
                    if self.tabs.tabButton(i, seite) is not None:
                        self.tabs.tabButton(i, seite).hide()
            self.leiste.setVisible(len(self.reiter) > 1)
            if zeigen:
                self.tabs.setCurrentIndex(i)
                self.stapel.setCurrentWidget(ansicht)
                ansicht.setFocus()

        def reiter_benennen(self, ansicht, name: str):
            if ansicht in self.reiter and name:
                self.tabs.setTabText(self.reiter.index(ansicht), reiter_name(name))

        def neue_shell(self):
            kind_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NEMICLI_")}
            ansicht = Ansicht(self, _shell(), kind_env=kind_env)
            self.hinzufuegen(ansicht, "Kommandozeile")
            ansicht.programm_starten()

        def ki_zeigen(self):
            self.ki_zu = False
            self._ki_ansicht(zeigen=True)

        def _ki_ansicht(self, zeigen: bool = False):
            if self.ki is None:
                self.ki = Ansicht(self, nur_lesen=True)
                self.hinzufuegen(self.ki, "🤖 KI", zeigen=zeigen)
                self.ki.einspeisen("\x1b[2mHier erscheinen die Befehle der KI live (nur lesen, "
                                   "Mausrad/Bild↑↓ blättert zurück).\x1b[0m\r\n")
            elif zeigen:
                self.tabs.setCurrentIndex(self.reiter.index(self.ki))
            return self.ki

        def _ki_ereignisse(self):
            for ereignis in ki_leitung.holen():
                if self.ki is None and (self.ki_zu or ereignis.get("t") != "start"):
                    continue
                self._ki_ansicht().einspeisen(ki_text(ereignis))

        def reiter_schliessen(self, ansicht):
            # schon zu: von Hand geschlossen, danach meldet das beendete Programm sich noch einmal
            if ansicht is None or ansicht.haupt or ansicht not in self.reiter:
                return
            ansicht.takt.stop()
            ansicht.beenden()
            i = self.reiter.index(ansicht)
            self.reiter.pop(i)
            self.tabs.removeTab(i)
            self.stapel.removeWidget(ansicht)
            if ansicht is self.ki:
                self.ki, self.ki_zu = None, True
            ansicht.deleteLater()
            self.leiste.setVisible(len(self.reiter) > 1)
            aktuell = self._ansicht(self.tabs.currentIndex())
            if aktuell is not None:
                self.stapel.setCurrentWidget(aktuell)
                aktuell.setFocus()

        def programm_zu(self, ansicht):
            """Programm eines Reiters beendet: NemiCLI schließt das Fenster, eine Kommandozeile ihren Reiter."""
            if ansicht.haupt:
                if self.tray is not None:
                    self.tray.hide()
                self.close()
            else:
                self.reiter_schliessen(ansicht)

        def _aktuellen_schliessen(self):
            self.reiter_schliessen(self._ansicht(self.tabs.currentIndex()))

        def _weiter(self, schritt: int):
            if len(self.reiter) > 1:
                self.tabs.setCurrentIndex((self.tabs.currentIndex() + schritt) % len(self.reiter))

        def _gewechselt(self, i: int):
            ansicht = self._ansicht(i)
            if ansicht is not None:
                self.stapel.setCurrentWidget(ansicht)
                ansicht.setFocus()

        def _verschoben(self, von: int, nach: int):
            self.reiter.insert(nach, self.reiter.pop(von))

        # -- Tray ------------------------------------------------------------------
        def in_tray(self, an: bool):
            """Solange das Desktop-Fenster (/gui) offen ist: Terminal aus dem Weg, Symbol im Tray."""
            if an:
                if self.tray is None:
                    self.tray = QSystemTrayIcon(self.windowIcon(), self)
                    self.tray.setToolTip("NemiCLI – läuft (Desktop-Fenster offen)")
                    self.tray_menue = QMenu()                 # setContextMenu übernimmt es nicht
                    self.tray_menue.addAction("Terminal zeigen", lambda: self.in_tray(False))
                    self.tray_menue.addSeparator()
                    self.tray_menue.addAction("NemiCLI beenden", self.close)
                    self.tray.setContextMenu(self.tray_menue)
                    self.tray.activated.connect(
                        lambda grund: self.in_tray(False)
                        if grund in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
                self.tray.show()
                self.hide()
            else:
                self.showNormal()
                self.raise_()
                self.activateWindow()
                if self.tray is not None:
                    self.tray.hide()

        def closeEvent(self, ev):
            for ansicht in list(self.reiter):
                ansicht.beenden()
            super().closeEvent(ev)
            # ausdrücklich: hat jemand setQuitOnLastWindowClosed(False) gesetzt (Wache), liefe exec() sonst weiter
            QTimer.singleShot(0, app.quit)

    fenster = Fenster()
    haupt_env = dict(env if env is not None else os.environ)
    haupt_env["NEMICLI_FENSTER"] = "1"
    if ki_leitung is not None:
        haupt_env.update(ki_leitung.umgebung())
    fenster.haupt = Ansicht(fenster, befehl, kind_env=haupt_env, haupt=True)
    fenster.hinzufuegen(fenster.haupt, titel)
    fenster.show()
    fenster.haupt.programm_starten()
    fenster.haupt.setFocus()
    app.exec()
    pty = fenster.haupt.pty
    return (pty.exitcode or 0) if pty is not None else 0
