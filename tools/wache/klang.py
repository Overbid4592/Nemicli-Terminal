"""Klang: Läuft gerade Musik? Pegel der Lautsprecher und Titel aus bekannten Musik-Programmen.

Pegel: Windows-Core-Audio (IAudioMeterInformation am Standard-Wiedergabegerät) – derselbe
Wert, den der Lautstärkemixer als Balken zeigt. Ohne Adminrechte, ohne Zusatzpaket: die drei
COM-Aufrufe gehen direkt über ctypes. Gelesen wird nur die Lautstärke, nie der Ton selbst.

Titel: nur aus den Fenstertiteln bekannter Musik-Programme (Spotify, YouTube im Browser, VLC,
foobar2000, AIMP, MusicBee, Winamp) – andere Fenster werden nicht ausgewertet.

`Musikgespuer` macht aus dem Pegel zwei Dinge: einen Takt-Schlag (der Pegel springt über
seinen Mittelwert) und „Musik läuft seit …“ (Ton ohne längere Pause).
"""

from __future__ import annotations

import ctypes
import threading
import time
import uuid
from ctypes import POINTER, byref, c_float, c_long, c_ulong, c_void_p

_CLSID_ENUM = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_ENUM = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_METER = "C02216F6-8C67-4B5B-9D00-D008E73E0064"
_CLSCTX_ALL = 23
_E_RENDER, _E_MULTIMEDIA = 0, 1


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _methode(obj: c_void_p, index: int, *argtypes, restype=c_long):
    """Methode Nr. `index` aus der vtable eines COM-Objekts."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
    return lambda *a: fn(obj, *a)


def _freigeben(obj: c_void_p | None) -> None:
    if obj:
        try:
            _methode(obj, 2, restype=c_ulong)()
        except Exception:
            pass


class Pegelmesser:
    """Liest im eigenen Thread 25-mal je Sekunde den Spitzenpegel (0 … 1) des Standard-Lautsprechers.
    Fehlt ein Gerät oder wechselt es (Kopfhörer), wird es alle paar Sekunden neu geöffnet."""

    def __init__(self, takt: float = 0.04):
        self.takt = takt
        self.wert = 0.0
        self.bereit = False
        self._stopp = threading.Event()
        self._thread: threading.Thread | None = None

    def starten(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._lauf, daemon=True, name="wache-klang")
            self._thread.start()

    def stop(self) -> None:
        self._stopp.set()

    def _oeffnen(self) -> c_void_p | None:
        ole32 = ctypes.windll.ole32
        enum, geraet, messer = c_void_p(), c_void_p(), c_void_p()
        try:
            if ole32.CoCreateInstance(byref(_guid(_CLSID_ENUM)), None, _CLSCTX_ALL,
                                      byref(_guid(_IID_ENUM)), byref(enum)) != 0:
                return None
            if _methode(enum, 4, ctypes.c_int, ctypes.c_int, POINTER(c_void_p))(
                    _E_RENDER, _E_MULTIMEDIA, byref(geraet)) != 0:
                return None
            if _methode(geraet, 3, POINTER(_GUID), ctypes.c_uint32, c_void_p, POINTER(c_void_p))(
                    byref(_guid(_IID_METER)), _CLSCTX_ALL, None, byref(messer)) != 0:
                return None
            return messer
        finally:
            _freigeben(geraet)
            _freigeben(enum)

    def _lauf(self) -> None:
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)          # COINIT_MULTITHREADED
        except Exception:
            return
        messer, neu_ab, geoeffnet = None, 0.0, 0.0
        wert = c_float()
        try:
            while not self._stopp.wait(self.takt):
                jetzt = time.time()
                if messer is not None and jetzt - geoeffnet > 30:     # Gerätewechsel mitbekommen
                    _freigeben(messer); messer = None
                if messer is None:
                    if jetzt < neu_ab:
                        self.wert = 0.0
                        continue
                    messer, geoeffnet = self._oeffnen(), jetzt
                    if messer is None:
                        neu_ab = jetzt + 5
                        continue
                if _methode(messer, 3, POINTER(c_float))(byref(wert)) != 0:
                    _freigeben(messer); messer = None; neu_ab = jetzt + 2
                    continue
                self.wert = max(0.0, min(1.0, float(wert.value)))
                self.bereit = True
        finally:
            _freigeben(messer)
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


class Musikgespuer:
    """Aus dem Pegel: Takt-Schläge und „läuft Musik?“ (reine Rechnung, ohne Windows – testbar)."""

    TON_AB = 0.02            # darunter gilt es als still
    PAUSE_MAX = 3.0          # kürzere Pausen unterbrechen ein Stück nicht
    MUSIK_AB = 20.0          # so lange Ton am Stück, dann „Musik läuft“

    def __init__(self):
        self.kurz = 0.0
        self.lang = 0.0
        self._ton_seit: float | None = None
        self._zuletzt_ton = 0.0
        self._schlag = 0.0
        self._zeit: float | None = None

    def fuettern(self, pegel: float, jetzt: float) -> bool:
        """Neuer Pegel-Wert. True = Takt-Schlag (der Pegel springt deutlich über den Mittelwert)."""
        dt = 0.04 if self._zeit is None else max(0.001, min(1.0, jetzt - self._zeit))
        self._zeit = jetzt
        self.kurz += (pegel - self.kurz) * min(1.0, dt / 0.06)
        self.lang += (pegel - self.lang) * min(1.0, dt / 1.5)
        if self.lang > self.TON_AB:
            self._zuletzt_ton = jetzt
            if self._ton_seit is None:
                self._ton_seit = jetzt
        elif self._ton_seit is not None and jetzt - self._zuletzt_ton > self.PAUSE_MAX:
            self._ton_seit = None
        if (self.kurz > self.TON_AB * 1.5 and self.kurz > self.lang * 1.35
                and jetzt - self._schlag > 0.28):
            self._schlag = jetzt
            return True
        return False

    def laeuft_seit(self, jetzt: float) -> float:
        """Sekunden Ton am Stück – 0, wenn gerade nichts läuft."""
        if self._ton_seit is None or jetzt - self._zuletzt_ton > self.PAUSE_MAX:
            return 0.0
        return jetzt - self._ton_seit

    def musik(self, jetzt: float) -> bool:
        return self.laeuft_seit(jetzt) >= self.MUSIK_AB


# ---------------------------------------------------------------- Titel

_BROWSER = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
_SPOTIFY_LEER = {"", "spotify", "spotify free", "spotify premium"}


def titel_aus(fenster: list[tuple[str, str]]) -> tuple[str, str] | None:
    """(Programm, Fenstertitel) aller sichtbaren Fenster → (Quelle, Titel) des laufenden Stücks.
    Reine Rechnung – die Liste liefert `fenster_liste()`."""
    for prozess, titel in fenster:
        p, t = (prozess or "").lower(), (titel or "").strip()
        if not t:
            continue
        if p == "spotify.exe" and t.lower() not in _SPOTIFY_LEER and " - " in t:
            return "Spotify", t
        if p in _BROWSER:
            for marke, quelle in ((" - YouTube Music", "YouTube Music"), (" - YouTube", "YouTube")):
                if marke in t:
                    stueck = t.split(marke)[0].strip()
                    if stueck.startswith("(") and ")" in stueck:      # „(3) Titel“ = ungelesene Hinweise
                        stueck = stueck.split(")", 1)[1].strip()
                    if stueck:
                        return quelle, stueck
        if p == "vlc.exe" and t.endswith(" - VLC media player") and t != "VLC media player":
            return "VLC", t[: -len(" - VLC media player")].strip()
        if p == "foobar2000.exe" and "[foobar2000]" in t:
            return "foobar2000", t.split("[foobar2000]")[0].strip(" -")
        if p == "aimp.exe" and t.lower() != "aimp":
            return "AIMP", t
        if p == "musicbee.exe" and t.endswith(" - MusicBee"):
            return "MusicBee", t[: -len(" - MusicBee")].strip()
        if p == "winamp.exe" and " - Winamp" in t:
            return "Winamp", t.split(" - Winamp")[0].split(". ", 1)[-1].strip()
    return None


def fenster_liste() -> list[tuple[str, str]]:
    """(Programmname, Titel) der sichtbaren Hauptfenster bekannter Musik-Programme."""
    try:
        import psutil
        user32 = ctypes.windll.user32
    except Exception:
        return []
    bekannt = _BROWSER | {"spotify.exe", "vlc.exe", "foobar2000.exe", "aimp.exe", "musicbee.exe", "winamp.exe"}
    namen: dict[int, str] = {}
    out: list[tuple[str, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, c_void_p, c_void_p)
    def je_fenster(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            laenge = user32.GetWindowTextLengthW(hwnd)
            if laenge <= 0:
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, byref(pid))
            if pid.value not in namen:
                try:
                    namen[pid.value] = psutil.Process(pid.value).name().lower()
                except Exception:
                    namen[pid.value] = ""
            if namen[pid.value] not in bekannt:
                return True
            puffer = ctypes.create_unicode_buffer(laenge + 1)
            user32.GetWindowTextW(hwnd, puffer, laenge + 1)
            out.append((namen[pid.value], puffer.value))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(je_fenster, None)
    except Exception:
        return []
    return out


def stueck() -> tuple[str, str] | None:
    return titel_aus(fenster_liste())
