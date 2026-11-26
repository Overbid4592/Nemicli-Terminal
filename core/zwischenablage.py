"""zwischenablage.py - Text in die Windows-Zwischenablage (user32, im eigenen Prozess)."""

from __future__ import annotations

import sys
import time

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def setzen(text: str) -> bool:
    """Legt `text` als Unicode-Text in die Zwischenablage. True bei Erfolg."""
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    from ctypes import wintypes as w

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [w.HWND]
    user32.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
    user32.SetClipboardData.restype = w.HANDLE
    kernel32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = w.HGLOBAL
    kernel32.GlobalLock.argtypes = [w.HGLOBAL]
    kernel32.GlobalLock.restype = w.LPVOID
    kernel32.GlobalUnlock.argtypes = [w.HGLOBAL]
    kernel32.GlobalFree.argtypes = [w.HGLOBAL]

    daten = (str(text) + "\0").encode("utf-16-le")
    # Eine andere Anwendung kann die Zwischenablage kurz belegt halten.
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(daten))
        if not h:
            return False
        ziel = kernel32.GlobalLock(h)
        if not ziel:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ziel, daten, len(daten))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(_CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True                          # der Speicher gehört jetzt dem System
    finally:
        user32.CloseClipboard()
