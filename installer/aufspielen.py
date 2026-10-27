"""
aufspielen.py - Installieren und Aktualisieren aus einer Setup-Datei (ohne Oberfläche).

Die Setup-Datei trägt den fertigen Bau (dist/NemiCLI) als ZIP. Gesucht wird nach
vorhandenen Installationen im Ordner `.ncli2` im Benutzerordner und an der Wurzel
jedes lokalen Laufwerks. Beim Aktualisieren werden nur die Programmteile ersetzt
(PROGRAMMTEILE und was oben im ZIP liegt); venv, Modelle, Config und Daten im
Ordner bleiben unberührt. Der Tausch läuft über Umbenennen: Schlägt ein Schritt
fehl, kommen die alten Programmteile zurück.
"""
from __future__ import annotations

import ctypes
import shutil
import uuid
import zipfile
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ORDNERNAME = ".ncli2"
PROGRAMMTEILE = ("NemiCLI.exe", "NemiCLIc.exe", "NemiCLIT2", "chrome-erweiterung", "SHA256SUMS.txt")
BUILD_DATEI = "NemiCLIT2/build.txt"
NEU, ALT = ".setup_neu", ".setup_alt"          # Zwischenordner im Ziel, nur während des Tauschs
_LAUFWERK_WECHSEL, _LAUFWERK_FEST = 2, 3       # GetDriveTypeW


@dataclass
class Installation:
    ordner: Path
    build: str                                   # "" = Ordner da, aber noch kein Programm darin


# --- Suchen --------------------------------------------------------------------------------------------

def laufwerke() -> list[str]:
    """Buchstaben der lokalen Laufwerke (fest und wechselbar, ohne Netz und CD)."""
    try:
        maske = ctypes.windll.kernel32.GetLogicalDrives()
        typ = ctypes.windll.kernel32.GetDriveTypeW
    except (AttributeError, OSError):
        return []
    return [chr(65 + i) for i in range(26)
            if maske >> i & 1 and typ(f"{chr(65 + i)}:\\") in (_LAUFWERK_WECHSEL, _LAUFWERK_FEST)]


def kandidaten() -> list[Path]:
    orte = [Path.home() / ORDNERNAME] + [Path(f"{lw}:\\") / ORDNERNAME for lw in laufwerke()]
    raus, gesehen = [], set()
    for p in orte:
        schluessel = str(p).lower()
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            raus.append(p)
    return raus


def ist_installation(ordner: Path) -> bool:
    return (ordner / "NemiCLI.exe").is_file() and (ordner / "NemiCLIT2").is_dir()


def build_von(ordner: Path) -> str:
    try:
        return (ordner / BUILD_DATEI).read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return "?"


def finden(orte: Optional[list[Path]] = None) -> list[Installation]:
    """Installationen (mit Build) und vorhandene `.ncli2`-Ordner ohne Programm (Build "")."""
    raus = []
    for p in kandidaten() if orte is None else orte:
        try:
            if ist_installation(p):
                raus.append(Installation(p, build_von(p)))
            elif p.is_dir():
                raus.append(Installation(p, ""))
        except OSError:                              # Laufwerk nicht bereit
            continue
    return raus


def laeuft(ordner: Path) -> list[str]:
    """Programme, die gerade aus diesem Ordner laufen (NemiCLI, Wache): Name und Prozessnummer."""
    import psutil
    try:
        wurzel = ordner.resolve()
    except OSError:
        return []
    raus = []
    for proc in psutil.process_iter(["exe", "name", "pid"]):
        exe = proc.info.get("exe")
        if not exe:
            continue
        try:
            if Path(exe).resolve().is_relative_to(wurzel):
                raus.append(f"{proc.info.get('name')} ({proc.info.get('pid')})")
        except (OSError, ValueError):
            continue
    return raus


# --- Nutzlast --------------------------------------------------------------------------------------------

def nutzlast_build(zip_pfad: Path) -> str:
    with zipfile.ZipFile(zip_pfad) as z:
        try:
            return z.read(BUILD_DATEI).decode("utf-8").strip() or "?"
        except KeyError:
            return "?"


def _teile(z: zipfile.ZipFile) -> set[str]:
    """Namen oben im ZIP (Programmteile der neuen Fassung)."""
    return {n.split("/", 1)[0] for n in z.namelist() if n.split("/", 1)[0]}


def _sicher_entpacken(z: zipfile.ZipFile, ziel: Path, fortschritt: Optional[Callable[[int, int], None]]) -> None:
    gesamt = sum(i.file_size for i in z.infolist()) or 1
    fertig = 0
    wurzel = ziel.resolve()
    for info in z.infolist():
        pfad = (ziel / info.filename).resolve()
        if not pfad.is_relative_to(wurzel):
            raise ValueError(f"ungültiger Pfad im Paket: {info.filename}")
        if info.is_dir():
            pfad.mkdir(parents=True, exist_ok=True)
            continue
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as quelle, open(pfad, "wb") as aus:
            shutil.copyfileobj(quelle, aus, 1 << 20)
        fertig += info.file_size
        if fortschritt is not None:
            fortschritt(fertig, gesamt)


def aufspielen(zip_pfad: Path, ziel: Path, fortschritt: Optional[Callable[[int, int], None]] = None) -> list[str]:
    """Programmteile aus dem ZIP nach `ziel` (neu oder Aktualisierung). Alles andere im Ordner bleibt.
    Gibt die ersetzten Teile zurück."""
    ziel.mkdir(parents=True, exist_ok=True)
    neu, alt = ziel / NEU, ziel / ALT
    for rest in (neu, alt):                          # Reste eines abgebrochenen Laufs (nur eigene Ordner)
        shutil.rmtree(rest, ignore_errors=True)
    with zipfile.ZipFile(zip_pfad) as z:
        # Nur die festen Programmteile tauschen: läge im Paket versehentlich eine Konfiguration oder ein
        # chats-Ordner, würde sonst der des Empfängers ersetzt und gelöscht.
        teile = sorted(PROGRAMMTEILE)
        if (fremd := sorted(_teile(z) - set(PROGRAMMTEILE))):
            raise ValueError("Das Paket enthält mehr als Programmteile: " + ", ".join(fremd[:5]))
        _sicher_entpacken(z, neu, fortschritt)
    if not ist_installation(neu):
        shutil.rmtree(neu, ignore_errors=True)
        raise ValueError("Das Paket enthält kein vollständiges NemiCLI")
    alt.mkdir()
    weg, rein = [], []
    try:
        for t in teile:
            if (ziel / t).exists():
                (ziel / t).rename(alt / t)
                weg.append(t)
        for t in teile:
            if (neu / t).exists():
                (neu / t).rename(ziel / t)
                rein.append(t)
    except OSError:
        for t in rein:                               # zurück: neue Teile raus, alte wieder hin
            (ziel / t).rename(neu / t)
        for t in weg:
            (alt / t).rename(ziel / t)
        raise
    finally:
        shutil.rmtree(neu, ignore_errors=True)
    shutil.rmtree(alt, ignore_errors=True)
    return rein


# --- Desktop-Verknüpfung (Shell-COM über ctypes) -----------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def von(cls, text: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"
_FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


def _methode(obj: ctypes.c_void_p, index: int, *argtypes):
    """Methode `index` aus der Tabelle eines COM-Objekts (HRESULT-Fehler werden zu OSError)."""
    tabelle = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(tabelle[index])


def desktop() -> Path:
    pfad = ctypes.c_wchar_p()
    ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(_GUID.von(_FOLDERID_DESKTOP)), 0, None,
                                               ctypes.byref(pfad))
    try:
        return Path(pfad.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pfad)


def verknuepfung(exe: Path, ordner: Optional[Path] = None, name: str = "NemiCLI") -> Path:
    """`name`.lnk auf `exe` (Arbeitsordner: der Programmordner, Symbol aus der exe) im Ordner
    `ordner` – ohne Angabe auf dem Desktop."""
    datei = (ordner or desktop()) / f"{name}.lnk"
    ole32 = ctypes.windll.ole32
    angemeldet = ole32.CoInitialize(None) >= 0          # S_FALSE: schon angemeldet, zählt mit
    link, datei_obj = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(ctypes.byref(_GUID.von(_CLSID_SHELLLINK)), None, 1,
                               ctypes.byref(_GUID.von(_IID_ISHELLLINKW)), ctypes.byref(link))
        if not link:
            raise OSError("Verknüpfung: Shell-Objekt nicht verfügbar")
        _methode(link, 20, wintypes.LPCWSTR)(link, str(exe))                    # SetPath
        _methode(link, 9, wintypes.LPCWSTR)(link, str(exe.parent))              # SetWorkingDirectory
        _methode(link, 7, wintypes.LPCWSTR)(link, name)                         # SetDescription
        _methode(link, 17, wintypes.LPCWSTR, ctypes.c_int)(link, str(exe), 0)   # SetIconLocation
        _methode(link, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
            link, ctypes.byref(_GUID.von(_IID_IPERSISTFILE)), ctypes.byref(datei_obj))
        _methode(datei_obj, 6, wintypes.LPCWSTR, wintypes.BOOL)(datei_obj, str(datei), True)   # Save
    finally:
        for obj in (datei_obj, link):
            if obj:
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                    ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][2])(obj)  # Release
        if angemeldet:
            ole32.CoUninitialize()
    return datei
