"""
winjob.py - Macht Kind-Prozesse "kugelsicher" gegen Geister.

Über ein Windows Job-Objekt mit KILL_ON_JOB_CLOSE: alle Prozesse, die wir an
das Job hängen, werden vom Betriebssystem AUTOMATISCH beendet, sobald NemiCLI
endet – egal ob über /exit, Strg+C oder Fenster-X. Kein verwaister Hilfs-Prozess
mehr.

Frueher raeumte hier zusaetzlich kill_stray_servers() verwaiste llama-server-
Prozesse weg. Mit llama.cpp ist das entfallen (15.09.2026) - Ollama verwaltet
seinen Dienst selbst, den darf NemiCLI nicht abschiessen.
"""

from __future__ import annotations

import subprocess
import sys

_IS_WIN = sys.platform == "win32"

if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ULONG_PTR = ctypes.c_size_t

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", _ULONG_PTR),
            ("MaximumWorkingSetSize", _ULONG_PTR),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", _ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", _ULONG_PTR),
            ("JobMemoryLimit", _ULONG_PTR),
            ("PeakProcessMemoryUsed", _ULONG_PTR),
            ("PeakJobMemoryUsed", _ULONG_PTR),
        ]

    _KILL_ON_JOB_CLOSE = 0x2000
    _ExtendedLimitInformation = 9
    _job_handle = None


def _ensure_job():
    """Erstellt (einmalig) das Job-Objekt mit 'kill on close'."""
    global _job_handle
    if not _IS_WIN:
        return None
    if _job_handle is not None:
        return _job_handle
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    h = _kernel32.CreateJobObjectW(None, None)
    if not h:
        return None
    info = _EXT()
    info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    ok = _kernel32.SetInformationJobObject(
        h, _ExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        _kernel32.CloseHandle(h)
        return None
    _job_handle = h
    return _job_handle


def attach(proc) -> bool:
    """Hängt einen Popen-Prozess ans Job. True bei Erfolg."""
    if not _IS_WIN:
        return False
    h = _ensure_job()
    if not h:
        return False
    try:
        _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(_kernel32.AssignProcessToJobObject(h, int(proc._handle)))
    except Exception:
        return False

