"""lebendig.py - Was die Kugel lebendiger macht: Berührungen, Blick, Tätigkeit, Tagesrhythmus, Spiel-Begleiterin.

Reine Logik (testbar) plus kleine Windows-Helfer; kugel.py zeichnet und entscheidet, wann.

  Anfassen      Rechtsklick = anstupsen; langsam über die Kugel streichen = sie genießt es; wild hin und
                her = ihr wird schwindelig (Beruehrung).
  Blick         sie neigt sich leicht zum Mauszeiger (blick).
  Tätigkeit     Spiel, Code, Video, Langeweile → passendes Motiv, wenn es gemalt ist (taetigkeit).
  Rhythmus      morgens verschlafen, abends ruhig; nachts ein Schlafanzug, wenn es einen gibt.
  Spiel         /kugel zuschauen an: läuft ein Spiel, schaut sie alle paar Minuten auf den Bildschirm
                und darf etwas dazu sagen; Strg+Alt+N öffnet ihr Fenster für Fragen (Tastenkuerzel).
Feste Sätze gibt es nicht – was sie sagt, formuliert die Persönlichkeit selbst.
"""
from __future__ import annotations

import ctypes
import math
import random
import re
import threading
import time
from pathlib import Path

# Wunsch-Texte für kugelmotive.finden – nur Motive, die schon gemalt sind, werden gezeigt.
REAKTION = {
    "stupsen": "kichert",
    "sanft": "verträumter Blick",
    "schwindelig": "schielt",
    "spiel": "spielt konzentriert",
    "code": "programmiert",
    "video": "Popcorn beim Zuschauen",
    "langeweile": "wartet im Ladebildschirm",
    "tippt": "neugierig",
}
REAKTION_S = {"stupsen": 4.0, "sanft": 6.0, "schwindelig": 5.0, "tippt": 6.0}
TAETIGKEIT_AB_S = 45.0           # so lange muss eine Tätigkeit laufen, dann zeigt sie das Motiv
LANGEWEILE_AB_S = 4 * 60         # ohne Eingabe (aber noch vor dem Schlafen)

# Ordner, in denen Spiele liegen (klein, mit Trennzeichen) – Launcher selbst liegen eine Ebene höher.
SPIEL_ORDNER = ("\\steamapps\\common\\", "\\epic games\\", "\\gog galaxy\\games\\", "\\gog games\\",
                "\\xboxgames\\", "\\riot games\\", "\\ubisoft game launcher\\games\\", "\\ea games\\",
                "\\battle.net\\games\\", "\\games\\")
KEIN_SPIEL = {"launcher", "_commonredist", "directx", "steamworks shared", "steam controller configs"}
CODE_PROGRAMME = {"code.exe", "code - insiders.exe", "cursor.exe", "devenv.exe", "pycharm64.exe", "idea64.exe",
                  "notepad++.exe", "sublime_text.exe", "windowsterminal.exe", "nemiclic.exe", "zed.exe"}
VIDEO_PROGRAMME = {"vlc.exe", "mpc-hc64.exe", "mpv.exe", "potplayermini64.exe", "netflix.exe"}
VIDEO_TITEL = re.compile(r"(?i)\b(youtube|netflix|twitch|prime video|disney\+|crunchyroll)\b")

ZUSCHAUEN_MIN = 5                # Vorgabe: so viele Minuten zwischen zwei Blicken auf das Spiel
TASTE = "strg+alt+n"

SPIEL_TEXT = (
    "[Spiel · {zeit}] Der Nutzer spielt gerade „{spiel}“. Du schaust ihm kurz über die Schulter – das "
    "Bildschirmfoto hängt an. Niemand hat dich angesprochen. Ist etwas Spannendes, Lustiges oder Hilfreiches zu "
    "sehen, sag es kurz (Sprechblase an der Kugel, ein bis zwei Sätze; er spielt gerade, also nichts Langes). "
    "Erkennst du nichts sicher, erfinde nichts. Hast du heute zu diesem Spiel schon etwas Ähnliches gesagt oder "
    "ist nichts los, antworte genau: (still)"
)
SPIEL_KONTEXT = ("[Gerade läuft das Spiel „{spiel}“. Fragen dazu darfst du im Netz nachschlagen (web_suche, "
                 "recherche) – sag, woher die Antwort stammt. Antworte kurz, der Nutzer spielt gerade.] ")


# ------------------------------------------------------------- Schalter

def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


AUTO_SCHALTER = {"kugel_impuls": False, "kugel_ankommen": True, "kugel_leben": True, "kugel_musik": True,
                 "kugel_zuschauen": False}          # Schalter hinter /kugel auto, mit ihrer Vorgabe


def auto_stand() -> bool | None:
    """True: alles hinter AUTO an · False: alles aus · None: gemischt."""
    c = _config()
    werte = {bool(c.get(k, v)) for k, v in AUTO_SCHALTER.items()}
    return werte.pop() if len(werte) == 1 else None


def auto_setzen(an: bool) -> None:
    import config
    config.update(**{k: bool(an) for k in AUTO_SCHALTER})


def vorne_an() -> bool:
    return bool(_config().get("kugel_vorne", True))


def zuschauen_an() -> bool:
    return bool(_config().get("kugel_zuschauen", False))


def zuschau_abstand_s() -> float:
    """Sekunden bis zum nächsten Blick: die eingestellten Minuten, ±20 % (wirkt weniger mechanisch)."""
    try:
        minuten = max(2.0, min(60.0, float(_config().get("kugel_zuschauen_min", ZUSCHAUEN_MIN))))
    except (TypeError, ValueError):
        minuten = float(ZUSCHAUEN_MIN)
    return minuten * 60 * random.uniform(0.8, 1.2)


# ------------------------------------------------------------- Spiel und Tätigkeit

def spiel_name(exe: str, titel: str = "") -> str | None:
    """Name des Spiels, wenn `exe` in einem Spiele-Ordner liegt (Ordner unter …\\common\\ usw.), sonst None."""
    pfad = str(exe or "").replace("/", "\\")
    klein = pfad.lower()
    for marke in SPIEL_ORDNER:
        i = klein.find(marke)
        if i < 0:
            continue
        rest = pfad[i + len(marke):].split("\\")
        if len(rest) >= 2 and rest[0] and rest[0].lower() not in KEIN_SPIEL:   # <Spielordner>\…\spiel.exe
            return rest[0]
    return None


def taetigkeit(exe: str, titel: str, d3d_vollbild: bool) -> tuple[str, str] | None:
    """(Art, Name) der Tätigkeit im Vordergrund: spiel · code · video – sonst None."""
    name = spiel_name(exe, titel)
    if name:
        return "spiel", name
    programm = Path(str(exe or "")).name.lower()
    if d3d_vollbild and programm not in VIDEO_PROGRAMME:
        return "spiel", (titel or Path(str(exe or "")).stem or "Spiel").strip()
    if programm in CODE_PROGRAMME:
        return "code", programm
    if programm in VIDEO_PROGRAMME or VIDEO_TITEL.search(titel or ""):
        return "video", programm
    return None


def _d3d_vollbild() -> bool:
    try:
        z = ctypes.c_int(0)
        return ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(z)) == 0 and z.value == 3
    except Exception:
        return False


def vordergrund(eigene: set[int] | None = None) -> tuple[str, str, bool] | None:
    """(Programmpfad, Fenstertitel, D3D-Vollbild) des aktiven Fensters – None für eigene Fenster oder bei Fehlern."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or (eigene and int(hwnd) in eigene):
            return None
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        puffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, puffer, 256)
        import psutil
        exe = psutil.Process(pid.value).exe()
        return exe, puffer.value, _d3d_vollbild()
    except Exception:
        return None


def spiel_im_vordergrund() -> str | None:
    """Name des Spiels im Vordergrund oder None (für die Sichtbarkeit der Kugel)."""
    vg = vordergrund()
    t = taetigkeit(*vg) if vg else None
    return t[1] if t and t[0] == "spiel" else None


HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
_SWP = 0x0001 | 0x0002 | 0x0010            # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE


def ganz_vorne(hwnd: int, an: bool = True) -> bool:
    """Fenster über alle anderen legen (oder die Ebene „immer oben“ verlassen), ohne es zu aktivieren."""
    try:
        return bool(ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(int(hwnd)),
                                                      ctypes.c_void_p(HWND_TOPMOST if an else HWND_NOTOPMOST),
                                                      0, 0, 0, 0, _SWP))
    except Exception:
        return False


class Taetigkeit:
    """Merkt sich, seit wann dieselbe Tätigkeit läuft; `stabil()` erst nach TAETIGKEIT_AB_S."""

    def __init__(self) -> None:
        self.jetzt: tuple[str, str] | None = None
        self.seit = 0.0

    def fuettern(self, t: tuple[str, str] | None, zeit: float) -> None:
        if t != self.jetzt:
            self.jetzt, self.seit = t, zeit

    def stabil(self, zeit: float) -> tuple[str, str] | None:
        return self.jetzt if self.jetzt is not None and zeit - self.seit >= TAETIGKEIT_AB_S else None


# ------------------------------------------------------------- Berührung

class Beruehrung:
    """Mausbewegungen über der Kugel (ohne Klick) → „sanft“ (ruhig gestrichen) oder „schwindelig“
    (wild hin und her). Das bisherige Umkreisen (Herzchen) erkennt kugel.py selbst."""

    PAUSE_S = 8.0

    def __init__(self) -> None:
        self._punkte: list[tuple[float, float, float]] = []      # (x, y, zeit)
        self._zuletzt: dict[str, float] = {}

    def zuruecksetzen(self) -> None:
        self._punkte = []

    def fuettern(self, x: float, y: float, zeit: float) -> str | None:
        self._punkte = [p for p in self._punkte if zeit - p[2] <= 2.5] + [(x, y, zeit)]
        if len(self._punkte) < 6:
            return None
        art = self._schwindelig(zeit) or self._sanft(zeit)
        if art and zeit - self._zuletzt.get(art, -1e9) >= self.PAUSE_S:
            self._zuletzt[art] = zeit
            self._punkte = []
            return art
        return None

    def _schwindelig(self, zeit: float) -> str | None:
        neu = [p for p in self._punkte if zeit - p[2] <= 1.2]
        if len(neu) < 6:
            return None
        wenden, richtung, weg = 0, 0, 0.0
        for a, b in zip(neu, neu[1:]):
            dx = b[0] - a[0]
            weg += math.hypot(dx, b[1] - a[1])
            r = (dx > 0) - (dx < 0)
            if r and richtung and r != richtung:
                wenden += 1
            richtung = r or richtung
        dauer = max(neu[-1][2] - neu[0][2], 1e-3)
        return "schwindelig" if wenden >= 5 and weg / dauer > 900 else None

    def _sanft(self, zeit: float) -> str | None:
        p = self._punkte
        dauer = p[-1][2] - p[0][2]
        if dauer < 1.8:
            return None
        weg = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(p, p[1:]))
        luecke = max(b[2] - a[2] for a, b in zip(p, p[1:]))
        tempo = weg / dauer
        return "sanft" if 60 <= tempo <= 500 and weg >= 200 and luecke < 0.4 else None


# ------------------------------------------------------------- Blick und Rhythmus

def blick(mitte: tuple[float, float], maus: tuple[float, float], reichweite: float = 700.0) -> tuple[float, float]:
    """(Neigung in Grad, Versatz in px) zum Mauszeiger hin; weit weg oder direkt drüber: geradeaus."""
    dx, dy = maus[0] - mitte[0], maus[1] - mitte[1]
    abstand = math.hypot(dx, dy)
    if abstand < 40 or abstand > reichweite:
        return 0.0, 0.0
    staerke = min(1.0, abstand / 300.0) * (1.0 - max(0.0, abstand - 450.0) / (reichweite - 450.0) * 0.6)
    richtung = dx / abstand
    return round(6.0 * richtung * staerke, 2), round(3.0 * richtung * staerke, 2)


def atem_faktor(stunde: int) -> float:
    """Wie schnell sie atmet: morgens verschlafen, mittags munter, abends ruhig, nachts langsam."""
    if 5 <= stunde < 9:
        return 0.7
    if 9 <= stunde < 18:
        return 1.0
    if 18 <= stunde < 23:
        return 0.85
    return 0.6


def morgens(stunde: int) -> bool:
    return 6 <= stunde < 9


NACHT_OUTFIT = re.compile(r"(?i)schlaf|pyjama|pajama|nacht|night|sleep")


def nacht_outfit(liste: dict) -> str | None:
    """Schlüssel eines Schlafanzugs unter den Outfits (Name oder Beschreibung), sonst None."""
    for k, v in (liste or {}).items():
        if NACHT_OUTFIT.search(str(k)) or NACHT_OUTFIT.search(str(v)):
            return str(k)
    return None


def nachtzeit(stunde: int) -> bool:
    return stunde >= 23 or stunde < 7


def dunkel(pfad: Path) -> bool:
    """Fast schwarzes Bildschirmfoto (exklusives Vollbild lässt sich so nicht fotografieren)?"""
    try:
        from PIL import Image, ImageStat
        with Image.open(pfad) as im:
            klein = im.convert("L").resize((64, 36))
            return ImageStat.Stat(klein).mean[0] < 8
    except Exception:
        return False


# ------------------------------------------------------------- Tastenkürzel

_MOD = {"alt": 0x0001, "strg": 0x0002, "ctrl": 0x0002, "umschalt": 0x0004, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000


def taste_lesen(text: str) -> tuple[int, int] | None:
    """'strg+alt+n' → (Modifier, virtueller Tastencode); None, wenn es nicht taugt (mindestens ein Modifier)."""
    teile = [t.strip().lower() for t in str(text or "").split("+") if t.strip()]
    if len(teile) < 2:
        return None
    mod = 0
    for t in teile[:-1]:
        if t not in _MOD:
            return None
        mod |= _MOD[t]
    k = teile[-1]
    if len(k) == 1 and k.isalnum():
        return mod, ord(k.upper())
    if re.fullmatch(r"f([1-9]|1[0-2])", k):
        return mod, 0x6F + int(k[1:])
    return None


class Tastenkuerzel:
    """Systemweites Tastenkürzel über RegisterHotKey (kein Tastatur-Haken): eigener Faden mit Nachrichtenschleife,
    `bei_druck` wird dort aufgerufen – kugel.py reicht es an den GUI-Faden weiter."""

    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, text: str, bei_druck) -> None:
        self.text, self._druck = text, bei_druck
        self._faden: threading.Thread | None = None
        self._tid = 0
        self.fehler = ""

    def starten(self) -> bool:
        taste = taste_lesen(self.text)
        if taste is None:
            self.fehler = f"Tastenkürzel „{self.text}“ ist ungültig"
            return False
        bereit = threading.Event()
        self._faden = threading.Thread(target=self._lauf, args=(taste, bereit), daemon=True, name="kugel-taste")
        self._faden.start()
        bereit.wait(2.0)
        return not self.fehler

    def _lauf(self, taste, bereit) -> None:
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, taste[0] | MOD_NOREPEAT, taste[1]):
            self.fehler = f"Tastenkürzel „{self.text}“ ist schon belegt"
            bereit.set()
            return
        bereit.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY:
                    try:
                        self._druck()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)


def spiel_text(spiel: str) -> str:
    return SPIEL_TEXT.format(zeit=time.strftime("%H:%M"), spiel=spiel)


def spiel_kontext(spiel: str | None) -> str:
    return SPIEL_KONTEXT.format(spiel=spiel) if spiel else ""
