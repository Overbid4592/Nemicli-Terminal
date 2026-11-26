"""atomar.py - Dateien so schreiben, dass ein Absturz mittendrin nie eine halbe Datei hinterlässt.

Erst eine Zwischendatei im selben Ordner, dann `os.replace` (unter Windows atomar auf demselben Laufwerk).
Hält ein Virenscanner das Ziel kurz fest, wird ein paarmal neu versucht.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path


def text_schreiben(pfad: str | Path, text: str, encoding: str = "utf-8") -> None:
    pfad = Path(pfad)
    # Eindeutig je Aufruf (zwei Threads eines Prozesses); O_EXCL: nie eine vorhandene Datei weiterbenutzen.
    tmp = pfad.with_name(f".{pfad.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    except OSError as e:                                       # Meldung mit dem Ziel, nicht dem Zufallsnamen
        raise type(e)(e.errno, e.strerror, str(pfad)) from None
    try:
        with open(fd, "w", encoding=encoding) as f:          # Zeilenenden wie Path.write_text
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for versuch in range(5):
            try:
                os.replace(tmp, pfad)
                return
            except PermissionError:
                if versuch == 4:
                    raise
                time.sleep(0.05 * (versuch + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def bytes_schreiben(pfad: str | Path, daten: bytes) -> None:
    """Wie text_schreiben, für Binärdaten (Office-Pakete, Bilder)."""
    pfad = Path(pfad)
    tmp = pfad.with_name(f".{pfad.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    except OSError as e:
        raise type(e)(e.errno, e.strerror, str(pfad)) from None
    try:
        with open(fd, "wb") as f:
            f.write(daten)
            f.flush()
            os.fsync(f.fileno())
        for versuch in range(5):
            try:
                os.replace(tmp, pfad)
                return
            except PermissionError:
                if versuch == 4:
                    raise
                time.sleep(0.05 * (versuch + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def beiseitelegen(pfad: str | Path) -> Path:
    """Unlesbare Datei als `<name>.kaputt` umbenennen; vorhandene Ablagen bleiben (dann mit Zeitstempel).
    OSError geht an den Aufrufer: ohne Ablage darf die Datei nicht überschrieben werden."""
    pfad = Path(pfad)
    ziel = pfad.with_name(pfad.name + ".kaputt")
    n = 0
    while ziel.exists():
        n += 1
        ziel = pfad.with_name(f"{pfad.name}.kaputt-{time.strftime('%Y%m%d-%H%M%S')}" + (f"-{n}" if n > 1 else ""))
    os.rename(pfad, ziel)                                  # überschreibt unter Windows nie
    return ziel


def json_lesen(pfad: str | Path, standard, typ: type | tuple = (dict, list)):
    """JSON-Datei lesen (mit oder ohne BOM).

    Fehlt sie: `standard`. Ist sie da, aber kaputt oder vom falschen Typ: beiseitelegen und `standard` –
    so überschreibt das nächste Speichern sie nicht. Andere Lesefehler (gesperrt …) gehen als OSError
    an den Aufrufer, denn dann ist der Inhalt unbekannt, nicht leer."""
    pfad = Path(pfad)
    try:
        roh = pfad.read_bytes()
    except FileNotFoundError:
        return standard
    try:
        daten = json.loads(roh.decode("utf-8-sig"))
    except ValueError:                                     # auch UnicodeDecodeError
        daten = None
    if not isinstance(daten, typ):
        if not roh.strip():                                # leere Datei: nichts zu retten
            return standard
        beiseitelegen(pfad)
        return standard
    return daten
