"""eingabe_win.py – Zeichen ohne eigene Taste (Emojis) in Textuals Windows-Eingabe.

Die Windows-Konsole und die Pseudokonsole liefern Zeichen, die auf keiner Taste
liegen, als Alt+Ziffernblock-Folge; das Zeichen steckt im Loslassen von Alt.
Textual liest nur gedrückte Tasten: es verliert das Zeichen, und die leeren
Tasten der Folge landen zwischen den Hälften eines Emojis außerhalb der
Grundebene (Ersatzzeichen-Paar), woran das Zusammensetzen scheitert.

`einschalten()` wandelt beim Einlesen um: das Alt-Loslassen mit Zeichen wird ein
Tastendruck mit diesem Zeichen, die leeren Tasten der Folge werden ausgeblendet.
"""

from __future__ import annotations

import os

KEY_EVENT = 0x0001
FOCUS_EVENT = 0x0010               # von Textual übergangen: dient zum Ausblenden
VK_MENU = 0x12
VK_NUMPAD = range(0x60, 0x6A)      # Ziffernblock 0–9
ALT = 0x0001 | 0x0002              # RIGHT_ALT_PRESSED | LEFT_ALT_PRESSED


def umwandeln(records, anzahl: int) -> int:
    """Alt+Ziffernblock-Folgen mit Zeichen → ein Tastendruck mit dem Zeichen (in place).
    Gibt die Zahl der umgewandelten Folgen zurück."""
    n = 0
    folge: list = []                 # Alt- und Ziffernblock-Ereignisse der laufenden Folge
    for i in range(anzahl):
        r = records[i]
        if r.EventType != KEY_EVENT:
            folge = []
            continue
        k = r.Event.KeyEvent
        leer = k.uChar.UnicodeChar in ("", "\x00")
        if k.wVirtualKeyCode == VK_MENU and k.bKeyDown and leer:
            folge = [r]
        elif folge and k.wVirtualKeyCode in VK_NUMPAD and leer and k.dwControlKeyState & ALT:
            folge.append(r)
        elif folge and k.wVirtualKeyCode == VK_MENU and not k.bKeyDown and not leer:
            for alt in folge:
                alt.EventType = FOCUS_EVENT
            k.bKeyDown = 1
            k.wVirtualKeyCode = 0
            k.dwControlKeyState = 0
            n += 1
            folge = []
        else:
            folge = []
    return n


class _Kernel32:
    """Reicht alles an kernel32 durch; nur ReadConsoleInputW wandelt nach dem Lesen um."""

    def __init__(self, echt):
        self._echt = echt

    def __getattr__(self, name):
        return getattr(self._echt, name)

    def ReadConsoleInputW(self, handle, puffer, max_anzahl, anzahl):
        ok = self._echt.ReadConsoleInputW(handle, puffer, max_anzahl, anzahl)
        try:
            umwandeln(puffer._obj, anzahl._obj.value)
        except Exception:
            pass
        return ok


def einschalten() -> bool:
    """Hängt die Umwandlung in Textuals Windows-Treiber ein (einmal, nur unter Windows)."""
    if os.name != "nt":
        return False
    try:
        from textual.drivers import win32
    except Exception:
        return False
    if isinstance(win32.KERNEL32, _Kernel32):
        return True
    if not hasattr(win32.KERNEL32, "ReadConsoleInputW"):
        return False
    win32.KERNEL32 = _Kernel32(win32.KERNEL32)
    return True
