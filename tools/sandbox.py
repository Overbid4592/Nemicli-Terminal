"""
sandbox.py - Code in einem Windows-AppContainer ausführen.

Ein AppContainer ist die Prozess-Isolation von Windows (wie bei Edge und
Store-Apps): Der Prozess bekommt eine eigene Kennung und sieht nur, was ihm
ausdrücklich freigegeben ist. Hier:

  - Internet (Capability internetClient), kein Zugriff auf localhost
  - ein Arbeitsordner (lesen/schreiben)
  - der Python-Interpreter (nur lesen/ausführen)

Dateien des Nutzers, die Registry außerhalb der Container-Zweige, andere
Prozesse und Adminrechte bleiben unerreichbar. Ein Job-Objekt begrenzt
Speicher und beendet alle Prozesse des Laufs, wenn er endet.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from paths import ROOT as _ROOT, INSTALL as _INSTALL
except Exception:                                   # Selbsttest ohne Bootstrap
    _ROOT = _INSTALL = Path(__file__).resolve().parent.parent

CONTAINER = "NemiCLI.Sandbox"
ARBEIT = _ROOT / "NemiSandbox"
PAKETE_ORDNER = "_pakete"                           # in ARBEIT: Pakete, die in der Sandbox installiert wurden
SPEICHER_GRENZE = 4 * 1024 ** 3                   # Bytes für alle Prozesse eines Laufs
MAX_AUSGABE = 200_000                               # Zeichen je Strom

_INTERNET_CLIENT = "S-1-15-3-1"
_WIN = os.name == "nt"


class SandboxFehler(RuntimeError):
    pass


@dataclass
class Ergebnis:
    code: int | None            # None = abgebrochen oder Zeit überschritten
    ausgabe: str
    fehler: str
    zeit_ueberschritten: bool = False
    abgebrochen: bool = False


# ---------------------------------------------------------------------------
# Windows-Schnittstellen
# ---------------------------------------------------------------------------

if _WIN:
    import ctypes
    from ctypes import wintypes as w

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _env = ctypes.WinDLL("userenv", use_last_error=True)

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p),
                    ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
                    ("CapabilityCount", w.DWORD), ("Reserved", w.DWORD)]

    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                    ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int),
                    ("ptstrName", ctypes.c_void_p)]

    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [("grfAccessPermissions", w.DWORD), ("grfAccessMode", ctypes.c_int),
                    ("grfInheritance", w.DWORD), ("Trustee", _TRUSTEE_W)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", w.BOOL)]

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

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
                    ("SchedulingClass", w.DWORD)]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _env.CreateAppContainerProfile.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p,
                                               w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _env.CreateAppContainerProfile.restype = ctypes.c_long
    _env.DeriveAppContainerSidFromAppContainerName.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _env.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    _adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    _adv.GetNamedSecurityInfoW.argtypes = [w.LPCWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _adv.GetNamedSecurityInfoW.restype = w.DWORD
    _adv.SetEntriesInAclW.argtypes = [w.ULONG, ctypes.POINTER(_EXPLICIT_ACCESS_W), ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p)]
    _adv.SetEntriesInAclW.restype = w.DWORD
    _adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _adv.SetNamedSecurityInfoW.restype = w.DWORD
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _k32.LocalFree.restype = ctypes.c_void_p
    _k32.CreatePipe.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE),
                                ctypes.POINTER(_SECURITY_ATTRIBUTES), w.DWORD]
    _k32.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
    _k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD,
                                                       ctypes.POINTER(ctypes.c_size_t)]
    _k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                               ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    _k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _k32.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                    w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(_STARTUPINFOW),
                                    ctypes.POINTER(_PROCESS_INFORMATION)]
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    _k32.CreateJobObjectW.restype = w.HANDLE
    _k32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    _k32.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    _k32.ResumeThread.argtypes = [w.HANDLE]
    _k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    _k32.WaitForSingleObject.restype = w.DWORD
    _k32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    _k32.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    _k32.CloseHandle.argtypes = [w.HANDLE]

    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NO_WINDOW = 0x08000000
    _STARTF_USESTDHANDLES = 0x00000100
    _HANDLE_FLAG_INHERIT = 0x1
    _SE_GROUP_ENABLED = 0x4
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x4
    _GRANT_ACCESS = 1
    _REVOKE_ACCESS = 4
    _SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
    _TRUSTEE_IS_SID = 0
    _TRUSTEE_IS_UNKNOWN = 0
    _LESEN_AUSFUEHREN = 0x001200A9                  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE
    _AENDERN = 0x001301BF                           # Ändern (lesen, schreiben, löschen, ausführen)
    _JOB_KILL_ON_CLOSE = 0x2000
    _JOB_MEMORY = 0x0200
    _JOB_EXTENDED_LIMIT = 9
    _WAIT_OBJECT_0 = 0
    _SCHON_DA = ctypes.c_long(0x800700B7).value    # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)


def verfuegbar() -> bool:
    return _WIN


# ---------------------------------------------------------------------------
# Container-Kennung und Freigaben
# ---------------------------------------------------------------------------

_sid_cache: list = []


def _container_sid():
    """SID des AppContainers; legt das Profil beim ersten Mal an."""
    if _sid_cache:
        return _sid_cache[0]
    sid = ctypes.c_void_p()
    hr = _env.CreateAppContainerProfile(CONTAINER, "NemiCLI Sandbox",
                                        "Isolierte Ausführung von Code für NemiCLI",
                                        None, 0, ctypes.byref(sid))
    if hr == _SCHON_DA:
        hr = _env.DeriveAppContainerSidFromAppContainerName(CONTAINER, ctypes.byref(sid))
    if hr != 0 or not sid.value:
        raise SandboxFehler(f"AppContainer nicht anlegbar (HRESULT {hr & 0xFFFFFFFF:#010x}).")
    _sid_cache.append(sid)
    return sid


def container_sid_text() -> str:
    text = w.LPWSTR()
    if not _adv.ConvertSidToStringSidW(_container_sid(), ctypes.byref(text)):
        return ""
    try:
        return text.value or ""
    finally:
        _k32.LocalFree(text)


def _freigabe_vorhanden(zeile: str, marke: Path) -> bool:
    try:
        return zeile.lower() in marke.read_text(encoding="utf-8").lower().splitlines()
    except OSError:
        return False


def freigeben(pfad: Path, schreiben: bool = False) -> None:
    """Gibt dem Container Zugriff auf einen Ordner (samt Inhalt, vererbbar).

    Nur ein zusätzlicher Eintrag für die Container-Kennung; bestehende Rechte
    bleiben unverändert. Einmal gesetzt, wird er in `.freigaben` vermerkt."""
    pfad = Path(pfad).resolve()
    marke = ARBEIT / ".freigaben"
    zeile = f"{'rw' if schreiben else 'r'}|{pfad}"
    if _freigabe_vorhanden(zeile, marke):
        return
    _acl_setzen(pfad, _GRANT_ACCESS, _AENDERN if schreiben else _LESEN_AUSFUEHREN)
    ARBEIT.mkdir(parents=True, exist_ok=True)
    with open(marke, "a", encoding="utf-8") as f:
        f.write(zeile + "\n")


def entziehen(pfad: Path) -> None:
    """Nimmt dem Container jeden Zugriff auf den Ordner wieder weg."""
    pfad = Path(pfad).resolve()
    _acl_setzen(pfad, _REVOKE_ACCESS, 0)
    marke = ARBEIT / ".freigaben"
    try:
        rest = [z for z in marke.read_text(encoding="utf-8").splitlines()
                if z.split("|", 1)[-1].lower() != str(pfad).lower()]
        marke.write_text("".join(z + "\n" for z in rest), encoding="utf-8")
    except OSError:
        pass


def _acl_setzen(pfad: Path, modus: int, rechte: int) -> None:
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    err = _adv.GetNamedSecurityInfoW(str(pfad), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
                                     None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if err:
        raise SandboxFehler(f"Rechte von {pfad} nicht lesbar (Fehler {err}).")
    try:
        ea = _EXPLICIT_ACCESS_W()
        ea.grfAccessPermissions = rechte
        ea.grfAccessMode = modus
        ea.grfInheritance = _SUB_CONTAINERS_AND_OBJECTS_INHERIT
        ea.Trustee.TrusteeForm = _TRUSTEE_IS_SID
        ea.Trustee.TrusteeType = _TRUSTEE_IS_UNKNOWN
        ea.Trustee.ptstrName = _container_sid().value
        neu = ctypes.c_void_p()
        err = _adv.SetEntriesInAclW(1, ctypes.byref(ea), dacl, ctypes.byref(neu))
        if err:
            raise SandboxFehler(f"Freigabe für {pfad} nicht erstellbar (Fehler {err}).")
        try:
            err = _adv.SetNamedSecurityInfoW(str(pfad), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
                                             None, None, neu, None)
            if err:
                raise SandboxFehler(f"Freigabe für {pfad} nicht setzbar (Fehler {err}).")
        finally:
            _k32.LocalFree(neu)
    finally:
        _k32.LocalFree(sd)


# ---------------------------------------------------------------------------
# Projektordner (nur der Nutzer gibt frei, per /sandbox)
# ---------------------------------------------------------------------------

def _projektliste() -> Path:
    return ARBEIT / ".projekte"


def _projekt_eintraege() -> list[tuple[Path, bool]]:
    """[(Ordner, schreibbar)] – Zeilen 'r|pfad' oder 'rw|pfad'."""
    try:
        zeilen = _projektliste().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    raus = []
    for z in zeilen:
        modus, _, pfad = z.strip().rpartition("|") if "|" in z else ("r", "", z.strip())
        if pfad:
            raus.append((Path(pfad), modus == "rw"))
    return raus


def projekte() -> list[Path]:
    return [p for p, _ in _projekt_eintraege()]


def schreibbar(projekt: Path) -> bool:
    return any(p == Path(projekt) and rw for p, rw in _projekt_eintraege())


def _projekt_verboten(pfad: Path) -> str | None:
    """Grund, warum dieser Ordner kein Sandbox-Projekt sein darf – sonst None."""
    if pfad.parent == pfad:
        return "ein ganzes Laufwerk"
    home = Path.home().resolve()
    if pfad == home or pfad in home.parents:
        return "das Benutzerprofil (oder ein Ordner darüber)"
    for sperre in (_INSTALL, _ROOT):
        s = Path(sperre).resolve()
        if pfad == s or s in pfad.parents or pfad in s.parents:
            return "der NemiCLI-Programm- oder Datenordner"
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        wert = os.environ.get(var)
        if wert:
            s = Path(wert).resolve()
            if pfad == s or s in pfad.parents:
                return "ein Systemordner"
    for name in ("Desktop", "Documents", "Downloads", "AppData"):
        if pfad == home / name:
            return f"der ganze Ordner {name}"
    return None


def _projekte_speichern(eintraege: list[tuple[Path, bool]]) -> None:
    _projektliste().write_text("".join(f"{'rw' if rw else 'r'}|{p}\n" for p, rw in eintraege),
                               encoding="utf-8")


def projekt_freigeben(pfad: Path, schreiben: bool = False) -> Path:
    """Gibt einen Projektordner frei – zum Lesen, mit `schreiben` auch zum Ändern.
    SandboxFehler bei verbotenen Orten."""
    pfad = Path(pfad).expanduser().resolve()
    if not pfad.is_dir():
        raise SandboxFehler(f"Kein Ordner: {pfad}")
    if (grund := _projekt_verboten(pfad)):
        raise SandboxFehler(f"{pfad} ist {grund} – das gebe ich der Sandbox nicht frei.")
    vorbereiten()
    alt = dict(_projekt_eintraege())
    if pfad in alt and alt[pfad] and not schreiben:
        entziehen(pfad)                           # von lesen+schreiben zurück auf lesen
    freigeben(pfad, schreiben=schreiben)
    alt[pfad] = schreiben
    _projekte_speichern(list(alt.items()))
    return pfad


def projekt_entziehen(pfad: Path) -> Path:
    pfad = Path(pfad).expanduser().resolve()
    if pfad not in projekte():
        raise SandboxFehler(f"{pfad} ist nicht freigegeben.")
    entziehen(pfad)
    _projekte_speichern([(p, rw) for p, rw in _projekt_eintraege() if p != pfad])
    return pfad


def projekt_von(datei: Path) -> Path | None:
    """Das freigegebene Projekt, in dem `datei` liegt – sonst None."""
    datei = Path(datei).resolve()
    for p in projekte():
        if p in datei.parents:
            return p
    return None


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

def _venv_python() -> Path | None:
    """Python des NemiCLI-venv (Quelltext: das laufende, exe: das von /einrichten)."""
    if not getattr(sys, "frozen", False) and sys.prefix != sys.base_prefix:
        return Path(sys.executable)
    kandidat = _INSTALL / "venv" / "Scripts" / "python.exe"
    return kandidat if kandidat.is_file() else None


def _installiertes_python() -> Path | None:
    """Ein regulär installiertes Python laut Registry (PEP 514), neueste Version zuerst."""
    try:
        import winreg
    except ImportError:
        return None
    funde: list[tuple[tuple, Path]] = []
    for wurzel in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            core = winreg.OpenKey(wurzel, r"Software\Python\PythonCore")
        except OSError:
            continue
        with core:
            i = 0
            while True:
                try:
                    tag = winreg.EnumKey(core, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(core, tag + r"\InstallPath") as k:
                        exe = Path(str(winreg.QueryValueEx(k, "ExecutablePath")[0]))
                except OSError:
                    continue
                nummern = tuple(int(x) for x in "".join(c if c.isdigit() else " " for c in tag).split()[:2])
                if exe.is_file() and not tag.endswith("-32"):
                    funde.append((nummern, exe))
    return max(funde)[1] if funde else None


def interpreter() -> tuple[Path, list[Path]]:
    """(python.exe, Ordner, die der Container lesen muss). SandboxFehler, wenn keiner da ist."""
    py = _venv_python()
    if py is None:
        py = _installiertes_python()
        if py is None:
            raise SandboxFehler("Kein Python für die Sandbox gefunden – /einrichten ausführen "
                                "oder Python installieren.")
        return py, [py.parent]
    venv = py.parent.parent
    ordner = [venv]
    try:
        for zeile in (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            if zeile.strip().lower().startswith("home"):
                ordner.append(Path(zeile.split("=", 1)[1].strip()))
                break
    except OSError:
        pass
    return py, ordner


# Python legt Ordner mit mode=0o700 (tempfile.mkdtemp) mit einer Rechteliste nur
# für den Eigentümer an – die Container-Kennung steht darin nicht, der Prozess
# sperrt sich aus seinem eigenen Temp-Ordner aus. In der Sandbox erben solche
# Ordner stattdessen die Rechte des Arbeitsordners.
_SITECUSTOMIZE = '''import os as _os
_mkdir = _os.mkdir
def _mkdir_geerbt(path, mode=0o777, *, dir_fd=None):
    return _mkdir(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)
_os.mkdir = _mkdir_geerbt
'''


def _start_ordner() -> Path:
    return ARBEIT / "_start"


def vorbereiten() -> Path:
    """Container, Arbeitsordner und Interpreter-Freigaben anlegen. Gibt python.exe zurück."""
    if not _WIN:
        raise SandboxFehler("Die Sandbox gibt es nur unter Windows.")
    ARBEIT.mkdir(parents=True, exist_ok=True)
    start = _start_ordner()
    start.mkdir(exist_ok=True)
    datei = start / "sitecustomize.py"
    if not datei.is_file() or datei.read_text(encoding="utf-8") != _SITECUSTOMIZE:
        datei.write_text(_SITECUSTOMIZE, encoding="utf-8")
    py, ordner = interpreter()
    freigeben(ARBEIT, schreiben=True)
    for o in ordner:
        freigeben(o)
    return py


# ---------------------------------------------------------------------------
# Ausführen
# ---------------------------------------------------------------------------

def _befehlszeile(argv: list[str]) -> str:
    import subprocess
    return subprocess.list2cmdline([str(a) for a in argv])


def _appdata() -> Path:
    return ARBEIT / "_appdata"


def _temp() -> Path:
    """Windows setzt TEMP im AppContainer auf %LOCALAPPDATA%\\Packages\\<Container>\\AC\\Temp."""
    return _appdata() / "Packages" / CONTAINER.lower() / "AC" / "Temp"


def _umgebung(ordner: Path, pythonpfad: list[Path] | None = None) -> ctypes.Array:
    tmp = _temp()
    tmp.mkdir(parents=True, exist_ok=True)
    behalten = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
                "PROCESSOR_ARCHITECTURE", "OS", "SYSTEMDRIVE")
    env = {k: v for k, v in os.environ.items() if k.upper() in behalten}
    env.update({
        "PATH": os.pathsep.join(filter(None, [os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32"])),
        "TEMP": str(tmp), "TMP": str(tmp),
        "USERPROFILE": str(ordner), "HOME": str(ordner),
        "APPDATA": str(_appdata()), "LOCALAPPDATA": str(_appdata()),
        "PYTHONUSERBASE": str(ordner / "_pakete"), "PIP_CACHE_DIR": str(tmp / "pip"),
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(str(p) for p in [_start_ordner(), *(pythonpfad or []),
                                                       ARBEIT / PAKETE_ORDNER]),
    })
    teile = [f"{k}={v}" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())]
    return ctypes.create_unicode_buffer("\0".join(teile) + "\0\0")


def _leser(handle, puffer: list[bytes]) -> None:
    block = ctypes.create_string_buffer(65536)
    n = w.DWORD()
    groesse = 0
    while _k32.ReadFile(handle, block, len(block), ctypes.byref(n), None) and n.value:
        if groesse < MAX_AUSGABE * 4:
            puffer.append(block.raw[:n.value])
            groesse += n.value


def ausfuehren(argv: list[str], ordner: Path, timeout: float = 60.0,
               stopp: "threading.Event | None" = None,
               pythonpfad: list[Path] | None = None) -> Ergebnis:
    """Startet argv im AppContainer mit `ordner` als Arbeitsordner und wartet.

    `ordner` muss in ARBEIT liegen. `stopp` bricht den Lauf von außen ab."""
    vorbereiten()
    ordner = Path(ordner).resolve()
    erlaubt = [ARBEIT.resolve()] + [p for p, rw in _projekt_eintraege() if rw]
    if not any(b in (ordner, *ordner.parents) for b in erlaubt):
        raise SandboxFehler(f"Arbeitsordner muss in {ARBEIT} oder einem beschreibbaren Projekt liegen.")
    ordner.mkdir(parents=True, exist_ok=True)

    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    rohre = []
    for _ in range(3):                               # stdin, stdout, stderr
        lesen, schreiben = w.HANDLE(), w.HANDLE()
        if not _k32.CreatePipe(ctypes.byref(lesen), ctypes.byref(schreiben), ctypes.byref(sa), 0):
            raise SandboxFehler(f"Pipe nicht anlegbar (Fehler {ctypes.get_last_error()}).")
        rohre.append((lesen, schreiben))
    (in_l, in_s), (out_l, out_s), (err_l, err_s) = rohre
    for eigen in (in_s, out_l, err_l):               # eigene Enden nicht vererben
        _k32.SetHandleInformation(eigen, _HANDLE_FLAG_INHERIT, 0)

    cap = _SID_AND_ATTRIBUTES()
    cap_sid = ctypes.c_void_p()
    if not _adv.ConvertStringSidToSidW(_INTERNET_CLIENT, ctypes.byref(cap_sid)):
        raise SandboxFehler("Internet-Freigabe nicht erzeugbar.")
    cap.Sid, cap.Attributes = cap_sid.value, _SE_GROUP_ENABLED
    sc = _SECURITY_CAPABILITIES(_container_sid().value, ctypes.pointer(cap), 1, 0)
    erben = (w.HANDLE * 3)(in_l, out_s, err_s)

    groesse = ctypes.c_size_t()
    _k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(groesse))
    liste = ctypes.create_string_buffer(groesse.value)
    job = None
    pi = _PROCESS_INFORMATION()
    try:
        if not _k32.InitializeProcThreadAttributeList(liste, 2, 0, ctypes.byref(groesse)):
            raise SandboxFehler("Startattribute nicht anlegbar.")
        try:
            for attr, wert, laenge in (
                    (_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, ctypes.byref(sc), ctypes.sizeof(sc)),
                    (_PROC_THREAD_ATTRIBUTE_HANDLE_LIST, erben, ctypes.sizeof(erben))):
                if not _k32.UpdateProcThreadAttribute(liste, 0, attr, wert, laenge, None, None):
                    raise SandboxFehler(f"Startattribut nicht setzbar (Fehler {ctypes.get_last_error()}).")
            si = _STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = in_l, out_s, err_s
            si.lpAttributeList = ctypes.cast(liste, ctypes.c_void_p)

            job = _k32.CreateJobObjectW(None, None)
            if not job:
                raise SandboxFehler("Job-Objekt nicht anlegbar.")
            grenze = _EXT_LIMIT()
            grenze.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE | _JOB_MEMORY
            grenze.JobMemoryLimit = SPEICHER_GRENZE
            _k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT, ctypes.byref(grenze), ctypes.sizeof(grenze))

            zeile = ctypes.create_unicode_buffer(_befehlszeile(argv))
            flags = (_EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT
                     | _CREATE_SUSPENDED | _CREATE_NO_WINDOW)
            if not _k32.CreateProcessW(str(argv[0]), zeile, None, None, True, flags,
                                       _umgebung(ordner, pythonpfad), str(ordner),
                                       ctypes.cast(ctypes.byref(si), ctypes.POINTER(_STARTUPINFOW)),
                                       ctypes.byref(pi)):
                raise SandboxFehler(f"Start im AppContainer fehlgeschlagen (Fehler {ctypes.get_last_error()}).")
        finally:
            _k32.DeleteProcThreadAttributeList(liste)
            _k32.LocalFree(cap_sid)
        for h in (in_l, out_s, err_s):                # gehören jetzt dem Kind
            _k32.CloseHandle(h)
        _k32.CloseHandle(in_s)                        # keine Eingabe
        _k32.AssignProcessToJobObject(job, pi.hProcess)
        _k32.ResumeThread(pi.hThread)

        aus, feh = [], []
        leser = [threading.Thread(target=_leser, args=(out_l, aus), daemon=True),
                 threading.Thread(target=_leser, args=(err_l, feh), daemon=True)]
        for t in leser:
            t.start()
        ende = time.monotonic() + timeout
        zu_lang = abgebrochen = False
        while _k32.WaitForSingleObject(pi.hProcess, 100) != _WAIT_OBJECT_0:
            if stopp is not None and stopp.is_set():
                abgebrochen = True
            elif time.monotonic() >= ende:
                zu_lang = True
            if zu_lang or abgebrochen:
                _k32.TerminateJobObject(job, 1)
                _k32.WaitForSingleObject(pi.hProcess, 5000)
                break
        _k32.TerminateJobObject(job, 1)               # übrig gebliebene Kindprozesse
        for t in leser:
            t.join(timeout=3)
        code = w.DWORD()
        _k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        text = lambda teile: b"".join(teile).decode("utf-8", errors="replace")[:MAX_AUSGABE]
        return Ergebnis(None if (zu_lang or abgebrochen) else int(code.value),
                        text(aus), text(feh), zu_lang, abgebrochen)
    finally:
        for h in (pi.hProcess, pi.hThread, out_l, err_l, job):
            if h:
                _k32.CloseHandle(h)


def python_ausfuehren(datei: Path, timeout: float = 60.0,
                      stopp: "threading.Event | None" = None,
                      ordner: Path | None = None, argumente: list[str] | None = None) -> Ergebnis:
    """Führt eine .py-Datei im AppContainer aus.

    Ohne `ordner` ist der Ordner der Datei der Arbeitsordner (Datei in ARBEIT
    oder in einem beschreibbaren Projekt). Aus einem nur lesbaren Projekt läuft
    sie vor Ort mit `ordner` (in ARBEIT) als Arbeitsordner."""
    py = vorbereiten()
    datei = Path(datei).resolve()
    projekt = projekt_von(datei)
    if ordner is None:
        ordner = datei.parent
    elif projekt is None and ARBEIT.resolve() not in datei.parents:
        raise SandboxFehler(f"{datei} liegt weder in der Sandbox noch in einem freigegebenen Projekt.")
    return ausfuehren([str(py), "-X", "utf8", str(datei), *(argumente or [])], ordner, timeout, stopp,
                      pythonpfad=[projekt] if projekt else None)


_PAKETNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(\[[A-Za-z0-9,._\-]+\])?"
                        r"([<>=!~]=?[A-Za-z0-9.*+!\-]+(,[<>=!~]=?[A-Za-z0-9.*+!\-]+)*)?$")


def pakete_installieren(pakete: list[str], timeout: float = 300.0) -> Ergebnis:
    """pip install in den Paketordner der Sandbox – im AppContainer, nur Paketnamen.

    Optionen (--index-url, -r …) sind nicht erlaubt: nur PyPI, nur benannte Pakete."""
    namen = [str(p).strip() for p in pakete if str(p).strip()]
    if not namen:
        raise SandboxFehler("Keine Pakete angegeben.")
    falsch = [n for n in namen if not _PAKETNAME.match(n)]
    if falsch:
        raise SandboxFehler("Kein gültiger Paketname: " + ", ".join(falsch))
    py = vorbereiten()
    ziel = ARBEIT / PAKETE_ORDNER
    ziel.mkdir(parents=True, exist_ok=True)
    lauf = ARBEIT / "_pip"
    lauf.mkdir(exist_ok=True)
    return ausfuehren([str(py), "-m", "pip", "install", "--target", str(ziel), "--upgrade",
                       "--disable-pip-version-check", "--no-input", "--no-warn-script-location",
                       *namen], lauf, timeout)
