"""appfoto.py – Fenster eines Programms fotografieren (Windows, reines Python über ctypes).

Für den Coding-Assistenten: ein Programm aus dem Projekt starten, warten, bis sein Fenster da ist, nur dieses
Fenster aufnehmen (PrintWindow), das Programm wieder beenden. Es läuft auf einem eigenen, unsichtbaren Desktop
(CreateDesktop): auf dem Bildschirm des Nutzers geht nichts auf. Geht das nicht, auf dem sichtbaren Desktop ohne
Fokus. Kein Pillow, kein numpy: das PNG schreibt zlib.
"""
from __future__ import annotations

import ctypes
import itertools
import os
import struct
import subprocess
import threading
import time
import zlib
from ctypes import wintypes as w
from pathlib import Path

WARTEN_VORGABE_S = 4.0
WARTEN_MAX_S = 30.0
NACHMALEN_S = 0.8            # nach dem Erscheinen: Zeit, bis der Inhalt gemalt ist
MAX_KANTE = 4096             # größere Fenster werden nicht aufgenommen (Speicher)

_PW_RENDERFULLCONTENT = 0x2
_DIB_RGB = 0
_BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD)]


def _dlls():
    u, g = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.GetWindowThreadProcessId.restype = w.DWORD
    u.IsWindowVisible.argtypes = [w.HWND]
    u.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    u.GetWindowTextLengthW.argtypes = [w.HWND]
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.GetWindow.argtypes = [w.HWND, ctypes.c_uint]
    u.GetWindow.restype = w.HWND
    u.GetDC.argtypes = [w.HWND]
    u.GetDC.restype = w.HDC
    u.ReleaseDC.argtypes = [w.HWND, w.HDC]
    u.PrintWindow.argtypes = [w.HWND, w.HDC, ctypes.c_uint]
    g.CreateCompatibleDC.argtypes = [w.HDC]
    g.CreateCompatibleDC.restype = w.HDC
    g.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
    g.CreateCompatibleBitmap.restype = w.HBITMAP
    g.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
    g.SelectObject.restype = w.HGDIOBJ
    g.GetDIBits.argtypes = [w.HDC, w.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                            ctypes.POINTER(_BITMAPINFOHEADER), ctypes.c_uint]
    g.DeleteObject.argtypes = [w.HGDIOBJ]
    g.DeleteDC.argtypes = [w.HDC]
    try:                                       # echte Pixel, auch auf skalierten Bildschirmen
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    except AttributeError:
        pass
    return u, g


def png_schreiben(pfad: Path, breite: int, hoehe: int, bgra: bytes) -> Path:
    """BGRA (oben nach unten) als PNG mit Alphakanal 255."""
    rgba = bytearray(len(bgra))
    rgba[0::4], rgba[1::4], rgba[2::4] = bgra[2::4], bgra[1::4], bgra[0::4]
    rgba[3::4] = b"\xff" * (len(bgra) // 4)
    zeile = breite * 4
    roh = b"".join(b"\x00" + bytes(rgba[y * zeile:(y + 1) * zeile]) for y in range(hoehe))

    def stueck(art: bytes, daten: bytes) -> bytes:
        return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + stueck(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 6, 0, 0, 0))
           + stueck(b"IDAT", zlib.compress(roh, 6)) + stueck(b"IEND", b""))
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(png)
    return pfad


def _prozessbaum(pid: int) -> set[int]:
    """Die PID und alle ihre Kinder (das venv-python.exe startet das eigentliche Python als Kind)."""
    pids = {pid}
    try:
        import psutil
        pids |= {k.pid for k in psutil.Process(pid).children(recursive=True)}
    except Exception:
        pass
    return pids


def fenster_von(pids: set[int]) -> list[tuple[int, str, tuple[int, int, int, int]]]:
    """Sichtbare Hauptfenster (ohne Besitzer, mit Größe) der Prozesse: (hwnd, Titel, Rechteck), größtes zuerst."""
    u, _g = _dlls()
    funde = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def je(hwnd, _l):
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, 4):   # GW_OWNER
            r = w.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 40 and r.bottom - r.top > 40:
                n = u.GetWindowTextLengthW(hwnd)
                puffer = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, puffer, n + 1)
                funde.append((int(hwnd), puffer.value, (r.left, r.top, r.right, r.bottom)))
        return True
    u.EnumWindows(je, 0)
    return sorted(funde, key=lambda f: -(f[2][2] - f[2][0]) * (f[2][3] - f[2][1]))


def fenster_foto(hwnd: int, pfad: Path) -> tuple[int, int]:
    """Ein Fenster per PrintWindow als PNG speichern. (Breite, Höhe)."""
    u, g = _dlls()
    alt = None
    if hasattr(u, "SetThreadDpiAwarenessContext"):
        alt = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    try:
        r = w.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        breite, hoehe = r.right - r.left, r.bottom - r.top
        if not (0 < breite <= MAX_KANTE and 0 < hoehe <= MAX_KANTE):
            raise RuntimeError(f"Fenstergröße {breite}×{hoehe} nicht aufnehmbar")
        schirm = u.GetDC(None)
        dc = g.CreateCompatibleDC(schirm)
        bild = g.CreateCompatibleBitmap(schirm, breite, hoehe)
        vorher = g.SelectObject(dc, bild)
        try:
            if not u.PrintWindow(hwnd, dc, _PW_RENDERFULLCONTENT):
                raise RuntimeError("PrintWindow fehlgeschlagen")
            kopf = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), breite, -hoehe, 1, 32, _BI_RGB, 0, 0, 0, 0, 0)
            puffer = ctypes.create_string_buffer(breite * hoehe * 4)
            if not g.GetDIBits(dc, bild, 0, hoehe, puffer, ctypes.byref(kopf), _DIB_RGB):
                raise RuntimeError("GetDIBits fehlgeschlagen")
            png_schreiben(pfad, breite, hoehe, puffer.raw)
        finally:
            g.SelectObject(dc, vorher)
            g.DeleteObject(bild)
            g.DeleteDC(dc)
            u.ReleaseDC(None, schirm)
        return breite, hoehe
    finally:
        if alt is not None:
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(alt))


def _baum_beenden(pid: int) -> None:
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    try:
        subprocess.run([str(taskkill), "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


# --- Eigener, unsichtbarer Desktop -------------------------------------------------------------------------------

class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                ("hStdError", w.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


_ZAEHLER = itertools.count(1)
_GENERIC_ALL = 0x10000000
_STARTF_USESTDHANDLES = 0x100
_CREATE_UNICODE_ENVIRONMENT = 0x400
_CREATE_NO_WINDOW = 0x08000000
_HANDLE_FLAG_INHERIT = 0x1


class _Unsichtbar:
    """Ein Prozess auf einem eigenen Desktop „NemiCLI-Probe-…“, den der Nutzer nie sieht."""

    def __init__(self, argv: list[str], ordner: Path, env: dict | None, log):
        k, u = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)
        u.CreateDesktopW.restype = w.HANDLE
        u.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p]
        u.CloseDesktop.argtypes = [w.HANDLE]
        u.SetThreadDesktop.argtypes = [w.HANDLE]
        k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                                     ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
                                     ctypes.POINTER(_PROCESS_INFORMATION)]
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        k.CloseHandle.argtypes = [w.HANDLE]
        k.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
        self.k, self.u = k, u
        self.name = f"NemiCLI-Probe-{os.getpid()}-{next(_ZAEHLER)}"
        self.desktop = u.CreateDesktopW(self.name, None, None, 0, _GENERIC_ALL, None)
        if not self.desktop:
            raise OSError(f"CreateDesktop fehlgeschlagen ({ctypes.get_last_error()})")
        import msvcrt
        ausgabe = msvcrt.get_osfhandle(log.fileno())
        k.SetHandleInformation(ausgabe, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT)
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = self.name
        si.dwFlags = _STARTF_USESTDHANDLES
        si.hStdInput, si.hStdOutput, si.hStdError = None, ausgabe, ausgabe
        block = None
        if env is not None:                    # Umgebungsblock: "k=v\0…\0\0", UTF-16
            block = ctypes.create_unicode_buffer("".join(f"{a}={b}\0" for a, b in env.items()) + "\0")
        self.pi = _PROCESS_INFORMATION()
        befehl = subprocess.list2cmdline(argv)
        ok = k.CreateProcessW(None, ctypes.create_unicode_buffer(befehl), None, None, True,
                              _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW, block, str(ordner),
                              ctypes.byref(si), ctypes.byref(self.pi))
        if not ok:
            fehler = ctypes.get_last_error()
            u.CloseDesktop(self.desktop)
            raise OSError(f"CreateProcess fehlgeschlagen ({fehler})")
        k.CloseHandle(self.pi.hThread)
        self.pid = int(self.pi.dwProcessId)
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is None and self.k.WaitForSingleObject(self.pi.hProcess, 0) == 0:
            code = w.DWORD()
            self.k.GetExitCodeProcess(self.pi.hProcess, ctypes.byref(code))
            self.returncode = int(code.value)
        return self.returncode

    def im_desktop(self, fn):
        """`fn()` in einem eigenen Faden ausführen, der auf dem unsichtbaren Desktop sitzt (Fenster finden und
        aufnehmen geht nur von dort). Ausnahmen kommen zurück."""
        aus: dict = {}

        def lauf():
            try:
                self.u.SetThreadDesktop(self.desktop)
                aus["wert"] = fn()
            except BaseException as e:      # noqa: BLE001 – an den Aufrufer weiterreichen
                aus["fehler"] = e
        t = threading.Thread(target=lauf, name="appfoto-desktop", daemon=True)
        t.start()
        t.join(timeout=60)
        if "fehler" in aus:
            raise aus["fehler"]
        return aus.get("wert")

    def schliessen(self) -> None:
        if self.poll() is None:
            _baum_beenden(self.pid)
        self.k.WaitForSingleObject(self.pi.hProcess, 5000)
        self.k.CloseHandle(self.pi.hProcess)
        self.u.CloseDesktop(self.desktop)


class _Sichtbar:
    """Rückfall: sichtbarer Desktop, ohne Fokus (wer gerade tippt, tippt weiter)."""

    def __init__(self, argv: list[str], ordner: Path, env: dict | None, log):
        start = None
        if os.name == "nt":
            start = subprocess.STARTUPINFO()
            start.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            start.wShowWindow = 4              # SW_SHOWNOACTIVATE
        self.proc = subprocess.Popen(argv, cwd=str(ordner), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, startupinfo=start)
        self.pid = self.proc.pid

    def poll(self) -> int | None:
        return self.proc.poll()

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode

    def im_desktop(self, fn):
        return fn()

    def schliessen(self) -> None:
        if self.proc.poll() is None:
            _baum_beenden(self.pid)
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass


def aufnehmen(argv: list[str], ordner: Path, ziel: Path, env: dict | None = None,
              warten_s: float = WARTEN_VORGABE_S, unsichtbar: bool = True) -> dict:
    """Programm starten, auf sein Fenster warten, fotografieren, beenden.
    {"ok", "bild", "titel", "groesse", "ausgabe", "endete", "code", "unsichtbar"}.
    Die Ausgabe des Programms steht in ziel.with_suffix('.log')."""
    warten_s = max(0.5, min(float(warten_s or WARTEN_VORGABE_S), WARTEN_MAX_S))
    log_pfad = ziel.with_suffix(".log")
    log_pfad.parent.mkdir(parents=True, exist_ok=True)
    erg = {"ok": False, "bild": None, "titel": "", "groesse": None, "ausgabe": "", "endete": False, "code": None,
           "unsichtbar": False}
    with open(log_pfad, "wb") as log:
        proc = None
        if unsichtbar and os.name == "nt":
            try:
                proc = _Unsichtbar(argv, ordner, env, log)
                erg["unsichtbar"] = True
            except OSError:
                proc = None
        if proc is None:
            proc = _Sichtbar(argv, ordner, env, log)

    def suchen_und_aufnehmen():
        ende = time.monotonic() + warten_s
        funde = []
        while time.monotonic() < ende:
            if proc.poll() is not None:
                return None
            funde = fenster_von(_prozessbaum(proc.pid))
            if funde:
                break
            time.sleep(0.2)
        if not funde:
            return None
        time.sleep(NACHMALEN_S)
        funde = fenster_von(_prozessbaum(proc.pid)) or funde
        hwnd, titel, _r = funde[0]
        return titel, fenster_foto(hwnd, ziel)

    try:
        gefunden = proc.im_desktop(suchen_und_aufnehmen)
        if gefunden:
            erg.update(ok=True, bild=str(ziel), titel=gefunden[0], groesse=gefunden[1])
        elif proc.poll() is not None:
            erg["endete"], erg["code"] = True, proc.returncode
    finally:
        proc.schliessen()
        try:
            erg["ausgabe"] = log_pfad.read_bytes()[-6000:].decode("utf-8", "replace")
        except OSError:
            pass
    return erg
