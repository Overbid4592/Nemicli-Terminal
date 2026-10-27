"""Netzspur: NemiCLI schreibt mit, welche Namen es selbst im Internet nachschlägt.

Jede Verbindung beginnt mit einer Namensauflösung (`socket.getaddrinfo`). Ein dünner Haken davor
merkt sich Name → Adressen und schreibt sie nach `Wache/eigene_namen.jsonl`. Taucht eine Adresse
später in einer Firewall-Meldung auf, kann die Wache sagen, welcher Dienst das war und wofür
NemiCLI ihn nutzt (netzauskunft.ZWECK). Gespeichert werden nur Name, Adressen, Zeit und Programm –
keine Inhalte, keine Pfade.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

from . import ORDNER

DATEI = ORDNER / "eigene_namen.jsonl"
MAX_BYTES = 2_000_000          # dann wird die Datei zu .1 und neu begonnen
ABSTAND_S = 600.0              # derselbe Name mit denselben Adressen höchstens alle 10 min neu

_lock = threading.Lock()
_zuletzt: dict[tuple, float] = {}
_original = None
_programm = ""


def _ist_adresse(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
        return True
    except ValueError:
        return False


def merken(host, ergebnis, datei: Path | None = None) -> bool:
    """Eine Auflösung festhalten. True, wenn eine Zeile geschrieben wurde."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or _ist_adresse(host) or "." not in host:
        return False
    ips = sorted({str(e[4][0]) for e in ergebnis or [] if len(e) > 4 and e[4]})
    if not ips:
        return False
    jetzt = time.time()
    key = (host, tuple(ips))
    datei = datei or DATEI
    with _lock:
        if jetzt - _zuletzt.get(key, 0.0) < ABSTAND_S:
            return False
        _zuletzt[key] = jetzt
        if len(_zuletzt) > 5000:
            _zuletzt.clear()
        try:
            datei.parent.mkdir(parents=True, exist_ok=True)
            if datei.exists() and datei.stat().st_size > MAX_BYTES:
                os.replace(datei, datei.with_suffix(".jsonl.1"))
            with open(datei, "a", encoding="utf-8") as f:
                f.write(json.dumps({"zeit": round(jetzt, 1), "name": host, "ips": ips,
                                    "programm": _programm}, ensure_ascii=False) + "\n")
        except OSError:
            return False
    return True


def installieren(programm: str = "") -> bool:
    """Den Haken einmal je Prozess setzen. Fehler beim Mitschreiben stören die Verbindung nie."""
    global _original, _programm
    if _original is not None:
        return False
    _programm = programm or Path(sys.executable).name
    _original = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        ergebnis = _original(host, *args, **kwargs)
        try:
            merken(host, ergebnis)
        except Exception:
            pass
        return ergebnis

    socket.getaddrinfo = getaddrinfo
    return True


def lesen(datei: Path | None = None, max_zeilen: int = 20_000) -> list[dict]:
    """Die letzten Einträge (ältere Datei .1 zuerst), älteste vorne."""
    datei = datei or DATEI
    zeilen: list[str] = []
    for p in (datei.with_suffix(".jsonl.1"), datei):
        try:
            zeilen += p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    out = []
    for z in zeilen[-max_zeilen:]:
        try:
            d = json.loads(z)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("name") and isinstance(d.get("ips"), list):
            out.append(d)
    return out
