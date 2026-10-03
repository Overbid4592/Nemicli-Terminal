"""terminal_pty.py – Pseudokonsole (ConPTY) für das eigene NemiCLI-Terminal.

Windows' eingebaute Pseudokonsole (kernel32: CreatePseudoConsole, seit Windows 10
1809) – dieselbe Schnittstelle, die auch Windows Terminal nutzt. Direkt über
ctypes angesprochen, ohne Zusatz-DLL: eine unsignierte Fremd-DLL würde Smart App
Control blockieren.

Das Kindprogramm sieht eine ganz normale Konsole; alles, was es ausgibt, kommt
hier als VT-Text (UTF-8) an, und Tastendrücke gehen als VT-Folgen zurück.
"""

from __future__ import annotations

import codecs
import ctypes
import os
import subprocess
import threading
from ctypes import wintypes as w

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

HPCON = ctypes.c_void_p
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STD_NUMMERN = (w.DWORD(-10 & 0xFFFFFFFF).value, w.DWORD(-11 & 0xFFFFFFFF).value,
                w.DWORD(-12 & 0xFFFFFFFF).value)       # STD_INPUT/OUTPUT/ERROR_HANDLE
_STD_LOCK = threading.Lock()
INFINITE = 0xFFFFFFFF


class _COORD(ctypes.Structure):
    _fields_ = [("X", w.SHORT), ("Y", w.SHORT)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
                ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
                ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
                ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


def _signaturen() -> None:
    k = _k32
    k.CreatePipe.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD]
    k.CreatePseudoConsole.argtypes = [_COORD, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(HPCON)]
    k.CreatePseudoConsole.restype = ctypes.c_long
    k.ResizePseudoConsole.argtypes = [HPCON, _COORD]
    k.ResizePseudoConsole.restype = ctypes.c_long
    k.ClosePseudoConsole.argtypes = [HPCON]
    k.ClosePseudoConsole.restype = None
    k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD,
                                                   ctypes.POINTER(ctypes.c_size_t)]
    k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                           ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                                 ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
                                 ctypes.POINTER(_PROCESS_INFORMATION)]
    k.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    k.WriteFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    k.CloseHandle.argtypes = [w.HANDLE]
    k.GetStdHandle.argtypes = [w.DWORD]
    k.GetStdHandle.restype = w.HANDLE
    k.SetStdHandle.argtypes = [w.DWORD, w.HANDLE]


if _k32 is not None:
    _signaturen()


def _umgebung_block(env: dict) -> ctypes.Array:
    """Umgebung als Unicode-Block „NAME=wert\\0…\\0\\0“, sortiert wie Windows es erwartet."""
    teile = [f"{k}={v}" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())]
    return ctypes.create_unicode_buffer("\0".join(teile) + "\0\0")


class Pty:
    """Ein Programm in einer Pseudokonsole.

    `bei_ausgabe(text)` wird aus einem Lese-Thread mit fertig dekodiertem Text
    aufgerufen, `bei_ende(code)` einmal, wenn das Programm beendet ist."""

    def __init__(self, befehl: list[str], spalten: int, zeilen: int, *, ordner: str | None = None,
                 env: dict | None = None, bei_ausgabe=None, bei_ende=None):
        if _k32 is None:
            raise OSError("Pseudokonsole gibt es nur unter Windows")
        self.bei_ausgabe = bei_ausgabe or (lambda _t: None)
        self.bei_ende = bei_ende or (lambda _c: None)
        self._hpc = HPCON()
        self._ein_schreiben = w.HANDLE()      # wir schreiben Tastendrücke hier hinein
        self._aus_lesen = w.HANDLE()          # wir lesen die Ausgabe hier heraus
        self._prozess = _PROCESS_INFORMATION()
        self._zu = threading.Lock()
        self._geschlossen = False
        self.exitcode: int | None = None

        ein_lesen, aus_schreiben = w.HANDLE(), w.HANDLE()
        if not _k32.CreatePipe(ctypes.byref(ein_lesen), ctypes.byref(self._ein_schreiben), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not _k32.CreatePipe(ctypes.byref(self._aus_lesen), ctypes.byref(aus_schreiben), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        hr = _k32.CreatePseudoConsole(_COORD(spalten, zeilen), ein_lesen, aus_schreiben, 0,
                                      ctypes.byref(self._hpc))
        # Die Konsole hat ihre eigenen Kopien – unsere Enden auf ihrer Seite brauchen wir nicht.
        _k32.CloseHandle(ein_lesen)
        _k32.CloseHandle(aus_schreiben)
        if hr != 0:
            raise OSError(f"CreatePseudoConsole fehlgeschlagen (0x{hr & 0xFFFFFFFF:08x})")

        groesse = ctypes.c_size_t()
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(groesse))
        liste = ctypes.create_string_buffer(groesse.value)
        if not _k32.InitializeProcThreadAttributeList(liste, 1, 0, ctypes.byref(groesse)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _k32.UpdateProcThreadAttribute(liste, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                                  self._hpc, ctypes.sizeof(HPCON), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            si = _STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            si.lpAttributeList = ctypes.cast(liste, ctypes.c_void_p)
            zeile = ctypes.create_unicode_buffer(subprocess.list2cmdline(befehl))
            block = _umgebung_block(env if env is not None else dict(os.environ))
            # Sind die eigenen Standard-Kanäle umgeleitet (Pipes), gibt Windows sie dem Kind
            # mit – es schriebe an der Pseudokonsole vorbei. Für den Start beiseitelegen,
            # dann bekommt das Kind Ein- und Ausgabe der Pseudokonsole.
            with _STD_LOCK:
                alt = [_k32.GetStdHandle(n) for n in _STD_NUMMERN]
                for n in _STD_NUMMERN:
                    _k32.SetStdHandle(n, None)
                try:
                    ok = _k32.CreateProcessW(None, zeile, None, None, False,
                                             EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                                             block, ordner, ctypes.byref(si.StartupInfo),
                                             ctypes.byref(self._prozess))
                    fehler = ctypes.get_last_error()
                finally:
                    for n, h in zip(_STD_NUMMERN, alt):
                        _k32.SetStdHandle(n, h)
            if not ok:
                raise ctypes.WinError(fehler)
        finally:
            _k32.DeleteProcThreadAttributeList(liste)
        _k32.CloseHandle(self._prozess.hThread)
        self.pid = self._prozess.dwProcessId
        self._leser = threading.Thread(target=self._lesen, name="nemicli-pty-lesen", daemon=True)
        self._leser.start()
        threading.Thread(target=self._warten, name="nemicli-pty-warten", daemon=True).start()

    # -- Datenfluss --------------------------------------------------------------
    def _lesen(self) -> None:
        dekoder = codecs.getincrementaldecoder("utf-8")("replace")
        puffer = ctypes.create_string_buffer(65536)
        gelesen = w.DWORD()
        while True:
            ok = _k32.ReadFile(self._aus_lesen, puffer, len(puffer), ctypes.byref(gelesen), None)
            if not ok or gelesen.value == 0:
                break
            text = dekoder.decode(puffer.raw[:gelesen.value])
            if text:
                self.bei_ausgabe(text)

    def _warten(self) -> None:
        _k32.WaitForSingleObject(self._prozess.hProcess, INFINITE)
        code = w.DWORD()
        _k32.GetExitCodeProcess(self._prozess.hProcess, ctypes.byref(code))
        self.exitcode = int(code.value)
        # Die Pseudokonsole schließen beendet auch den Lese-Thread (ReadFile kehrt zurück).
        # Erst melden, wenn er die letzten Ausgaben durchgereicht hat.
        self._konsole_schliessen()
        self._leser.join(timeout=5)
        self.bei_ende(self.exitcode)

    def schreiben(self, text: str) -> None:
        if self._geschlossen or not text:
            return
        daten = text.encode("utf-8")
        geschrieben = w.DWORD()
        _k32.WriteFile(self._ein_schreiben, daten, len(daten), ctypes.byref(geschrieben), None)

    def groesse(self, spalten: int, zeilen: int) -> None:
        if not self._geschlossen:
            _k32.ResizePseudoConsole(self._hpc, _COORD(max(1, spalten), max(1, zeilen)))

    def laeuft(self) -> bool:
        return self.exitcode is None

    # -- Aufräumen ---------------------------------------------------------------
    def _konsole_schliessen(self) -> None:
        with self._zu:
            if self._geschlossen:
                return
            self._geschlossen = True
            _k32.ClosePseudoConsole(self._hpc)
            _k32.CloseHandle(self._ein_schreiben)

    def beenden(self, warten_s: float = 3.0) -> None:
        """Konsole schließen (das Programm bekommt „Fenster zu“) und notfalls hart beenden."""
        self._konsole_schliessen()
        if self.exitcode is None:
            if _k32.WaitForSingleObject(self._prozess.hProcess, int(warten_s * 1000)) != 0:
                _k32.TerminateProcess(self._prozess.hProcess, 1)
