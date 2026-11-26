"""
schutzsoftware.py - Ordner der installierten Schutzsoftware (Virenschutz, Firewall).

Die KI untersucht Schutzsoftware nicht: keine Signaturprüfung, kein Auflisten,
kein Lesen ihrer Dateien, keine Befehle, die sie nennen. Welche Software
installiert ist, meldet Windows im Security Center (Registry); daraus ergibt
sich der Hersteller-Ordner unter Program Files, dazu gleichnamige Ordner in
ProgramData und Program Files (x86).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_ZWEIGE = (r"SOFTWARE\Microsoft\Security Center\Provider\Av",
           r"SOFTWARE\Microsoft\Security Center\Provider\Fw",
           r"SOFTWARE\Microsoft\Security Center\Provider\As")

_cache: list[Path] | None = None


def _programmordner() -> list[Path]:
    return [Path(v) for v in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                              os.environ.get("ProgramW6432")) if v]


def _datenordner() -> list[Path]:
    v = os.environ.get("ProgramData")
    return [Path(v)] if v else []


def _exe_pfade() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    pfade = []
    for zweig in _ZWEIGE:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, zweig)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, name) as eintrag:
                        for wert in ("PRODUCTEXE", "REPORTINGEXE"):
                            try:
                                pfade.append(os.path.expandvars(str(winreg.QueryValueEx(eintrag, wert)[0])))
                            except OSError:
                                pass
                except OSError:
                    continue
    return pfade


def _hersteller_ordner(exe: str) -> Path | None:
    """C:\\Program Files\\<Hersteller>\\…\\x.exe -> C:\\Program Files\\<Hersteller>."""
    if not exe or "://" in exe:
        return None
    try:
        p = Path(exe).resolve()
    except (OSError, ValueError):
        return None
    for basis in _programmordner():
        try:
            rel = p.relative_to(basis.resolve())
        except (ValueError, OSError):
            continue
        if rel.parts:
            return basis.resolve() / rel.parts[0]
    return None


def ordner() -> list[Path]:
    """Alle Ordner der installierten Schutzsoftware (einmal ermittelt)."""
    global _cache
    if _cache is not None:
        return _cache
    namen: set[str] = set()
    raus: list[Path] = []
    for exe in _exe_pfade():
        h = _hersteller_ordner(exe)
        if h is not None:
            namen.add(h.name)
    for basis in _programmordner() + _datenordner():
        for name in sorted(namen):
            kandidat = basis / name
            if kandidat not in raus:
                raus.append(kandidat)
    # Windows' eigener Schutz: fester Ort, auch wenn er nicht aktiv meldet.
    for basis in _programmordner():
        raus += [basis / "Windows Defender", basis / "Windows Defender Advanced Threat Protection"]
    for basis in _datenordner():
        raus.append(basis / "Microsoft" / "Windows Defender")
    _cache = list(dict.fromkeys(raus))
    return _cache


def betroffen(pfad: str | os.PathLike) -> Path | None:
    """Der Schutzsoftware-Ordner, in dem `pfad` liegt – sonst None."""
    try:
        p = str(Path(os.path.expandvars(str(pfad))).resolve()).lower()
    except (OSError, ValueError):
        return None
    for o in ordner():
        ol = str(o).lower()
        if p == ol or p.startswith(ol + os.sep):
            return o
    return None


def nennt(text: str) -> str | None:
    """Nennt `text` einen Schutzsoftware-Ordner (voller Pfad oder Hersteller-Ordnername)?"""
    # nur %NAME% ersetzen – $name ist in PowerShell eine Variable, keine Umgebung
    t = re.sub(r"%([A-Za-z_][\w()]*)%", lambda m: os.environ.get(m.group(1), m.group(0)), text or "")
    t = t.lower().replace("/", "\\")
    for o in ordner():
        if str(o).lower() in t:
            return str(o)
    for name in {o.name for o in ordner()}:
        if re.search(r"(?<![\w-])" + re.escape(name.lower()) + r"(?![\w-])", t):
            return name
    return None


HINWEIS = ("Das ist Schutzsoftware (Virenschutz/Firewall). NemiCLI untersucht sie nicht: "
           "keine Signaturen, Prozesse, Dateien oder Befehle dazu. Sie ist bekannt und gehört "
           "zum System.")
