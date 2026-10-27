"""atomar.py - Dateien so schreiben, dass ein Absturz mittendrin nie eine halbe Datei hinterlässt.

Erst eine Zwischendatei im selben Ordner, dann `os.replace` (unter Windows atomar auf demselben Laufwerk).
Hält ein Virenscanner das Ziel kurz fest, wird ein paarmal neu versucht.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def text_schreiben(pfad: str | Path, text: str, encoding: str = "utf-8") -> None:
    pfad = Path(pfad)
    tmp = pfad.with_name(f".{pfad.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding) as f:          # Zeilenenden wie Path.write_text
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
