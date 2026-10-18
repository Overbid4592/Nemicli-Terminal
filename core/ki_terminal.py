"""ki_terminal.py – Befehle der KI live ans eigene Terminal-Fenster (Reiter „🤖 KI“).

Das Fenster (ui/terminal_fenster.py) lauscht auf einer Named Pipe und gibt Adresse und Schlüssel in
NEMICLI_KI_PIPE / NEMICLI_KI_KEY mit. Beide werden beim Import aus der Umgebung genommen, damit
Programme, die die KI startet, sie nicht erben. Ohne Fenster tut das Modul nichts.
Gesendet wird aus einem eigenen Faden; ist die Leitung voll oder weg, geht nur die Anzeige verloren.
"""
from __future__ import annotations

import json
import os
import queue
import threading

_ADRESSE = os.environ.pop("NEMICLI_KI_PIPE", "")
_SCHLUESSEL = os.environ.pop("NEMICLI_KI_KEY", "")
WARTESCHLANGE = 4000
_schlange: queue.Queue = queue.Queue(maxsize=WARTESCHLANGE)
_zustand = {"faden": None, "weg": False}
_sperre = threading.Lock()


def aktiv() -> bool:
    return bool(_ADRESSE and _SCHLUESSEL) and not _zustand["weg"]


def _senden() -> None:
    from multiprocessing.connection import Client
    try:
        conn = Client(_ADRESSE, family="AF_PIPE", authkey=bytes.fromhex(_SCHLUESSEL))
    except Exception:
        _zustand["weg"] = True
        return
    while True:
        ereignis = _schlange.get()
        try:
            conn.send_bytes(json.dumps(ereignis, ensure_ascii=False).encode("utf-8"))
        except Exception:
            _zustand["weg"] = True
            return


def _schicken(ereignis: dict) -> None:
    if not aktiv():
        return
    with _sperre:
        if _zustand["faden"] is None:
            _zustand["faden"] = threading.Thread(target=_senden, name="nemicli-ki-terminal", daemon=True)
            _zustand["faden"].start()
    try:
        _schlange.put_nowait(ereignis)
    except queue.Full:
        pass


def start(befehl: str, ordner: str = "") -> None:
    _schicken({"t": "start", "befehl": befehl, "ordner": ordner})


def ausgabe(text: str, fehler: bool = False) -> None:
    if text:
        _schicken({"t": "aus", "text": text, "fehler": fehler})


def ende(code, sekunden: float, abgebrochen: bool = False) -> None:
    _schicken({"t": "ende", "code": code, "sekunden": round(sekunden, 1), "abgebrochen": abgebrochen})
