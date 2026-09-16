"""
kontextmenue.py - NemiCLI im Windows-Rechtsklick-Menü.

Zwei Einträge (nur für den angemeldeten Benutzer, HKCU – kein Admin nötig):
  · Desktop / Ordner-Hintergrund:  „🐈 NemiCLI: Bildschirm erfassen"
        -> kurz warten (Menü zu), Screenshot nach Bilder/Screenshots/, dann
           „Schau dir dieses Bild an: <pfad>" an NemiCLI – ins OFFENE Fenster,
           wenn eins läuft (.nemicli.alive/.nemicli.inbox), sonst neues Fenster
           per nemicli --sag. Die aktive Persönlichkeit schaut es sofort an.
  · Rechtsklick auf ein Bild:      „🐈 Mit NemiCLI ansehen"
        -> genauso.
  · Desktop / Ordner-Hintergrund:  „🐈 Nemi antwortet (in das Fenster daneben)"
        -> merkt sich das zuletzt benutzte Programmfenster (z.B. den Browser mit
           der offenen E-Mail), Screenshot, Auftrag „schreib die Antwort" an
           NemiCLI. NemiCLI fügt die fertige Antwort dann DIREKT in dieses
           Fenster ein (Zwischenablage + Strg+V, Fenster wird nach vorn geholt).
  Der Pfad liegt zusätzlich in der Zwischenablage (Strg+V in einem laufenden NemiCLI).

Windows 11 zeigt solche Einträge unter „Weitere Optionen anzeigen" (Shift+F10).

Aufruf aus dem Menü (ohne Konsolenfenster, pythonw.exe):
    kontextmenue.py screenshot
    kontextmenue.py bild "<pfad>"
Ein-/Ausschalten in NemiCLI: /kontextmenue an · aus
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:                    # nemicli.cmd + die Alive/Inbox-Dateien gehoeren zum
    from paths import INSTALL            # PROGRAMM - sie muessen dort liegen, wo
except Exception:                        # der Starter liegt, nicht bei den Daten.
    INSTALL = Path(__file__).resolve().parent.parent
ROOT = INSTALL                                         # Projektordner (neben main.py)
SCREENSHOT_DIR = ROOT / "Bilder" / "Screenshots"

# Registry-Orte (HKCU\Software\Classes\...): Ordner-Hintergrund, Desktop, Bilddateien
_KEY_SCREEN = [r"Software\Classes\Directory\Background\shell\NemiCLI.Screenshot",
               r"Software\Classes\DesktopBackground\Shell\NemiCLI.Screenshot"]
_KEY_BILD = [r"Software\Classes\SystemFileAssociations\image\shell\NemiCLI.Ansehen"]
_KEY_ANTWORT = [r"Software\Classes\Directory\Background\shell\NemiCLI.Antwort",
                r"Software\Classes\DesktopBackground\Shell\NemiCLI.Antwort"]
_LABEL_SCREEN = "🐈 NemiCLI: Bildschirm erfassen"
_LABEL_BILD = "🐈 Mit NemiCLI ansehen"
_LABEL_ANTWORT = "🐈 Nemi antwortet (in das Fenster daneben)"

# Nachrichten-Präfix: NemiCLI fügt die Antwort danach in dieses Fenster ein
EINFUEGEN_TAG = "[[einfuegen:{hwnd}]] "
AUFTRAG_ANTWORT = (
    "Auf dem Bildschirm ist eine Nachricht, E-Mail, ein Chat oder ein Formular, auf das ich "
    "antworten möchte. Lies alles genau, versteh den Zusammenhang und schreib in meinem Namen "
    "eine passende Antwort – Ton und Sprache wie das Original. Gib NUR den fertigen "
    "Antworttext aus: keine Einleitung, kein Kommentar, keine Anführungszeichen, keine "
    "Aktion. Das Bild: {pfad}"
)


# ---------------------------------------------------------------------------
# Was die Menüeinträge tun
# ---------------------------------------------------------------------------
def in_zwischenablage(text: str) -> bool:
    """Text in die Windows-Zwischenablage (PowerShell Set-Clipboard, UTF-8)."""
    try:
        cmd = ("[Console]::InputEncoding=[System.Text.Encoding]::UTF8; "
               "Set-Clipboard -Value ([Console]::In.ReadToEnd())")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           input=text.encode("utf-8"), capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def _hinweis(text: str, dauer_ms: int = 1800) -> None:
    """Kleines Fenster unten rechts, das von selbst wieder verschwindet."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        lbl = tk.Label(root, text=text, bg="#11131d", fg="#e4e7f2", padx=18, pady=10,
                       font=("Segoe UI", 11))
        lbl.pack()
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"+{sw - w - 24}+{sh - h - 80}")
        root.after(dauer_ms, root.destroy)
        root.mainloop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Zielfenster finden und Text hineinsetzen (nur Windows, ctypes/user32)
# ---------------------------------------------------------------------------
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                  "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
                  "CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"}   # Terminal = NemiCLI selbst


def _user32():
    import ctypes
    return ctypes.windll.user32


def _fenster_klasse(hwnd: int) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buf, 256)
    return buf.value


def fenster_titel(hwnd: int) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(512)
    _user32().GetWindowTextW(hwnd, buf, 512)
    return buf.value


def zielfenster() -> int | None:
    """Das zuletzt benutzte Programmfenster (z.B. der Browser): das oberste sichtbare
    Fenster in der Z-Reihenfolge, das nicht Desktop/Taskleiste/Terminal ist."""
    if not sys.platform.startswith("win"):
        return None
    u = _user32()
    GW_HWNDNEXT, GW_HWNDFIRST = 2, 0
    hwnd = u.GetForegroundWindow()
    hwnd = u.GetWindow(hwnd, GW_HWNDFIRST) if hwnd else 0
    for _ in range(500):
        if not hwnd:
            break
        if (u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd) and fenster_titel(hwnd)
                and _fenster_klasse(hwnd) not in _SHELL_CLASSES):
            return int(hwnd)
        hwnd = u.GetWindow(hwnd, GW_HWNDNEXT)
    return None


def einfuegen(hwnd: int, text: str) -> bool:
    """Text in die Zwischenablage, Fenster nach vorn, Strg+V senden."""
    if not sys.platform.startswith("win") or not hwnd or not text:
        return False
    if not in_zwischenablage(text):
        return False
    u = _user32()
    if not u.IsWindow(hwnd):
        return False
    VK_MENU, VK_CONTROL, VK_V, KEYUP = 0x12, 0x11, 0x56, 0x0002
    # Alt kurz drücken – dann lässt Windows den Fokuswechsel zu (bekannter Kniff)
    u.keybd_event(VK_MENU, 0, 0, 0)
    u.SetForegroundWindow(hwnd)
    u.keybd_event(VK_MENU, 0, KEYUP, 0)
    time.sleep(0.25)
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_V, 0, 0, 0)
    u.keybd_event(VK_V, 0, KEYUP, 0)
    u.keybd_event(VK_CONTROL, 0, KEYUP, 0)
    return True


def antwort_tag(text: str) -> tuple[int | None, str]:
    """'[[einfuegen:123]] Auftrag' -> (123, 'Auftrag'); sonst (None, text)."""
    if text.startswith("[[einfuegen:"):
        ende = text.find("]]")
        if ende > 0:
            try:
                return int(text[12:ende]), text[ende + 2:].lstrip()
            except ValueError:
                pass
    return None, text


def antwort_text(antwort: str) -> str:
    """Modell-Antwort für das Einfügen säubern: Code-Zäune/Aktionsblöcke weg,
    umschließende Anführungszeichen weg, Ränder trimmen."""
    import re
    t = re.sub(r"```(?:aktion|json)?\s*\n\{.*?\}\s*```", "", antwort, flags=re.S)
    t = re.sub(r"^```[a-z]*\n?|\n?```$", "", t.strip(), flags=re.M).strip()
    if len(t) >= 2 and t[0] in "\"„“" and t[-1] in "\"“”":
        t = t[1:-1].strip()
    return t


NEMICLI_CMD = INSTALL / "nemicli.cmd"
ALIVE_DATEI = INSTALL / ".nemicli.alive"       # das laufende NemiCLI erneuert sie alle 2 s
INBOX_DATEI = INSTALL / ".nemicli.inbox"       # Nachricht fürs laufende NemiCLI
ALIVE_MAX_S = 6.0                           # älter -> NemiCLI läuft nicht (mehr)


def nemicli_laeuft() -> bool:
    """Läuft gerade ein NemiCLI-Fenster? (Lebenszeichen jünger als ALIVE_MAX_S)"""
    try:
        return time.time() - ALIVE_DATEI.stat().st_mtime < ALIVE_MAX_S
    except OSError:
        return False


def an_laufendes(nachricht: str) -> bool:
    """Nachricht ins offene NemiCLI legen – das schickt sie innerhalb 1 s ab."""
    try:
        INBOX_DATEI.write_text(nachricht, encoding="utf-8")
        return True
    except OSError:
        return False


def uebergeben(nachricht: str) -> bool:
    """Offenes Fenster? Dann dorthin. Sonst ein neues starten."""
    if nemicli_laeuft() and an_laufendes(nachricht):
        _hinweis("🐈 An das offene NemiCLI übergeben")
        return True
    return nemicli_starten(nachricht)


def nemicli_starten(nachricht: str) -> bool:
    """Öffnet ein neues NemiCLI-Fenster, das die Nachricht sofort abschickt."""
    if not NEMICLI_CMD.exists():
        _hinweis("NemiCLI: nemicli.cmd nicht gefunden")
        return False
    try:
        # `start` -> eigenes Konsolenfenster (Windows Terminal, wenn Standard)
        subprocess.Popen(["cmd", "/c", "start", "", str(NEMICLI_CMD), "--sag", nachricht],
                         cwd=str(ROOT), close_fds=True)
        return True
    except Exception as e:
        _hinweis(f"NemiCLI: Start fehlgeschlagen ({e})")
        return False


def nachricht_fuer(pfad: Path) -> str:
    return f"Schau dir bitte dieses Bild an und sag mir, was du siehst: {pfad}"


def screenshot(warte_s: float = 0.6, ordner: Path | None = None, starten: bool = True) -> Path | None:
    """Screenshot nach Bilder/Screenshots/, dann NemiCLI damit starten."""
    try:
        from PIL import ImageGrab
    except Exception:
        _hinweis("NemiCLI: PIL fehlt – kein Screenshot möglich")
        return None
    time.sleep(warte_s)                                   # Rechtsklick-Menü erst schließen lassen
    ziel = (ordner or SCREENSHOT_DIR)
    try:
        ziel.mkdir(parents=True, exist_ok=True)
        pfad = ziel / f"screen_{datetime.now():%Y%m%d_%H%M%S}.png"
        ImageGrab.grab().save(pfad)
    except Exception as e:
        _hinweis(f"NemiCLI: Screenshot fehlgeschlagen ({e})")
        return None
    in_zwischenablage(str(pfad))
    if starten:
        uebergeben(nachricht_fuer(pfad))
    return pfad


def antwort(warte_s: float = 0.6, ordner: Path | None = None, starten: bool = True) -> Path | None:
    """„Nemi antwortet": Zielfenster merken, Screenshot, Auftrag an NemiCLI –
    die Antwort fügt NemiCLI danach in das Zielfenster ein."""
    hwnd = zielfenster()
    pfad = screenshot(warte_s, ordner, starten=False)
    if pfad is None:
        return None
    nachricht = AUFTRAG_ANTWORT.format(pfad=pfad)
    if hwnd:
        nachricht = EINFUEGEN_TAG.format(hwnd=hwnd) + nachricht
    else:
        _hinweis("NemiCLI: kein Zielfenster gefunden – Antwort kommt in die Zwischenablage")
        nachricht = EINFUEGEN_TAG.format(hwnd=0) + nachricht
    if starten:
        uebergeben(nachricht)
    return pfad


def bild(pfad: str, starten: bool = True) -> bool:
    """Bild an NemiCLI übergeben (neues Fenster, Nachricht sofort abgeschickt)."""
    p = Path(pfad)
    if not p.is_file():
        _hinweis("NemiCLI: Datei nicht gefunden")
        return False
    in_zwischenablage(str(p))
    return uebergeben(nachricht_fuer(p)) if starten else True


# ---------------------------------------------------------------------------
# Registry: eintragen / entfernen / Status
# ---------------------------------------------------------------------------
def _python_ohne_fenster() -> str:
    """pythonw.exe (kein Konsolenfenster) neben dem laufenden Python, sonst python."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def befehle() -> dict[str, str]:
    """Die Kommandozeilen, die Windows beim Klick ausführt."""
    py = _python_ohne_fenster()
    skript = str(Path(__file__).resolve())
    return {
        "screenshot": f'"{py}" "{skript}" screenshot',
        "bild": f'"{py}" "{skript}" bild "%1"',
        "antwort": f'"{py}" "{skript}" antwort',
    }


def _winreg():
    if not sys.platform.startswith("win"):
        raise RuntimeError("Das Rechtsklick-Menü gibt es nur unter Windows.")
    import winreg
    return winreg


def _eintragen(winreg, key_path: str, label: str, command: str, icon: str | None) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, label)
        if icon:
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)


def _entfernen(winreg, key_path: str) -> bool:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path + r"\command")
    except OSError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        return True
    except OSError:
        return False


def _vorhanden(winreg, key_path: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path + r"\command"):
            return True
    except OSError:
        return False


def installieren() -> list[str]:
    """Trägt beide Einträge ein. Gibt die Beschriftungen zurück."""
    winreg = _winreg()
    cmds = befehle()
    icon = str(Path(sys.executable))       # Python-Icon; reicht als kleines Symbol
    for key in _KEY_SCREEN:
        _eintragen(winreg, key, _LABEL_SCREEN, cmds["screenshot"], icon)
    for key in _KEY_BILD:
        _eintragen(winreg, key, _LABEL_BILD, cmds["bild"], icon)
    for key in _KEY_ANTWORT:
        _eintragen(winreg, key, _LABEL_ANTWORT, cmds["antwort"], icon)
    return [_LABEL_SCREEN, _LABEL_BILD, _LABEL_ANTWORT]


def entfernen() -> int:
    """Entfernt alle Einträge. Gibt die Zahl der gelöschten Schlüssel zurück."""
    winreg = _winreg()
    return sum(_entfernen(winreg, k) for k in _KEY_SCREEN + _KEY_BILD + _KEY_ANTWORT)


def status() -> dict[str, bool]:
    winreg = _winreg()
    return {"bildschirm": all(_vorhanden(winreg, k) for k in _KEY_SCREEN),
            "bild": all(_vorhanden(winreg, k) for k in _KEY_BILD),
            "antwort": all(_vorhanden(winreg, k) for k in _KEY_ANTWORT)}


# ---------------------------------------------------------------------------
# Aufruf aus dem Menü
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "screenshot":
        return 0 if screenshot() else 1
    if argv[0] == "antwort":
        return 0 if antwort() else 1
    if argv[0] == "bild" and len(argv) > 1:
        return 0 if bild(argv[1]) else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
