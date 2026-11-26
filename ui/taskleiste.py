"""
taskleiste.py - Taskleisten-Identität der NemiCLI-Fenster (Windows).

Mit eigener AppUserModelID sucht die Taskleiste Symbol und Namen über eine Verknüpfung mit derselben
Kennung; fehlt sie, zeigt der Knopf ein allgemeines Symbol. Deshalb trägt jedes Hauptfenster Kennung,
Symbol, Namen und Startbefehl als eigene Fenster-Eigenschaften (SHGetPropertyStoreForWindow,
System.AppUserModel.*). Gesetzt wird vor dem ersten Zeigen, damit der Knopf gleich richtig entsteht.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import uuid
from pathlib import Path

NAME = "NemiCLI"
ANHEFTBAR = "NemiCLI.Terminal"                            # nur dieses Fenster lässt sich neu starten (angeheftet)
_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"           # System.AppUserModel.*
_PID = {"befehl": 2, "symbol": 3, "name": 4, "kennung": 5, "nicht_anheften": 9}
_IID_PROPERTYSTORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_VT_LPWSTR, _VT_BOOL = 31, 11
_IDX_RELEASE, _IDX_SETVALUE, _IDX_COMMIT = 2, 6, 7      # Plätze in der vtable von IPropertyStore


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_ulong)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("wert", ctypes.c_void_p), ("rest", ctypes.c_void_p)]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text)
    return _GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def symbol_quelle() -> str:
    """„Pfad,Index“ des Symbols: als exe die exe selbst, sonst nemicli.ico im Programmordner."""
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    return f"{Path(__file__).resolve().parent.parent / 'nemicli.ico'},0"


def startbefehl() -> str:
    """Befehl, mit dem die Taskleiste NemiCLI neu starten kann (angeheftet)."""
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    return subprocess.list2cmdline([sys.executable, *sys.argv])


def werte(kennung: str) -> list[tuple[int, object]]:
    """(Eigenschaft, Wert) in der Reihenfolge des Setzens; die Kennung zuletzt. Andere Fenster als das Terminal
    (GUI, Musik …) laufen nur mit der Verbindung zum Hauptprogramm – sie dürfen nicht angeheftet werden."""
    raus: list[tuple[int, object]] = [(_PID["befehl"], startbefehl()), (_PID["name"], NAME),
                                      (_PID["symbol"], symbol_quelle())]
    if kennung != ANHEFTBAR:
        raus.append((_PID["nicht_anheften"], True))
    return raus + [(_PID["kennung"], kennung)]


def kennzeichnen(hwnd: int, kennung: str) -> bool:
    """Kennung, Symbol, Name und Startbefehl an das Fenster `hwnd` hängen. False, wenn es nicht geht."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 2)       # schon initialisiert: S_FALSE, unschädlich
        speicher = ctypes.c_void_p()
        iid = _guid(_IID_PROPERTYSTORE)
        if ctypes.windll.shell32.SHGetPropertyStoreForWindow(ctypes.c_void_p(hwnd), ctypes.byref(iid),
                                                              ctypes.byref(speicher)) != 0 or not speicher:
            return False
        tabelle = ctypes.cast(speicher, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        setzen = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY),
                                    ctypes.POINTER(_PROPVARIANT))(tabelle[_IDX_SETVALUE])
        festschreiben = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(tabelle[_IDX_COMMIT])
        freigeben = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(tabelle[_IDX_RELEASE])
        ok = True
        try:
            for pid, inhalt in werte(kennung):
                if isinstance(inhalt, bool):              # VARIANT_BOOL: -1 = wahr
                    wert = _PROPVARIANT(vt=_VT_BOOL, wert=0xFFFF if inhalt else 0)
                else:
                    puffer = ctypes.create_unicode_buffer(str(inhalt))
                    wert = _PROPVARIANT(vt=_VT_LPWSTR, wert=ctypes.cast(puffer, ctypes.c_void_p).value)
                schluessel = _PROPERTYKEY(_guid(_FMTID), pid)
                ok = setzen(speicher, ctypes.byref(schluessel), ctypes.byref(wert)) == 0 and ok
            ok = festschreiben(speicher) == 0 and ok
        finally:
            freigeben(speicher)
        return ok
    except Exception:
        return False


def prozess_kennung(kennung: str) -> None:
    """Kennung für den ganzen Prozess (Fenster ohne eigene Eigenschaften, z. B. Meldungen)."""
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(kennung)
        except Exception:
            pass


def installieren(app, kennung: str) -> None:
    """Prozess-Kennung setzen und jedes Hauptfenster der QApplication vor dem ersten Zeigen kennzeichnen."""
    prozess_kennung(kennung)
    if os.name != "nt" or getattr(app, "_taskleiste_filter", None) is not None:
        return
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import QWidget

    class _Filter(QObject):
        def __init__(self, eltern):
            super().__init__(eltern)
            self.erledigt: set[int] = set()

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow() \
                    and obj.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog):
                hwnd = int(obj.winId())
                if hwnd not in self.erledigt:
                    self.erledigt.add(hwnd)
                    kennzeichnen(hwnd, kennung)
            return False

    app._taskleiste_filter = _Filter(app)
    app.installEventFilter(app._taskleiste_filter)
