"""Kennung des Programmordners: Das Setup findet NemiCLI auch nach dem Umbenennen des Ordners.

- `kennung.json` im Programmordner (versteckt + System): zufällige Kennung der Installation, der
  aktuelle Ordnername und frühere Namen. Wandert beim Umbenennen mit.
- `ordner.json` an einem festen Ort (%LOCALAPPDATA%\\NemiCLI): Kennung und Pfad, verschlüsselt mit
  Windows DPAPI (an den Benutzer gebunden; verändert lässt es sich nicht mehr entschlüsseln).

NemiCLI pflegt beides beim Start (`pflegen`). Das Setup liest `gemerkter_ordner` und vertraut dem
Ordner nur, wenn dort dieselbe Kennung liegt (und die exe-Signatur stimmt, siehe installer/aufspielen.py).
Steht für sich allein: kein Import aus dem übrigen Projekt, damit das Setup es einbinden kann.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
from ctypes import wintypes
from datetime import date
from pathlib import Path
from typing import Optional

KENNUNG_DATEI = "kennung.json"
_ENTROPIE = b"NemiCLI-Programmordner-1"        # zusätzlicher DPAPI-Schlüsselteil, nur für diesen Zweck
_VERSTECKT_SYSTEM = 0x2 | 0x4                   # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
_NORMAL = 0x80
MAX_VORHER = 20


def register_datei() -> Path:
    basis = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(basis) / "NemiCLI" / "ordner.json"


# --- DPAPI ------------------------------------------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(daten: bytes) -> tuple[_Blob, ctypes.Array]:
    puffer = ctypes.create_string_buffer(daten, len(daten))
    return _Blob(len(daten), ctypes.cast(puffer, ctypes.POINTER(ctypes.c_char))), puffer


def _dpapi(daten: bytes, schuetzen: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    rein, _p1 = _blob(daten)
    zusatz, _p2 = _blob(_ENTROPIE)
    raus = _Blob()
    f = crypt32.CryptProtectData if schuetzen else crypt32.CryptUnprotectData
    if not f(ctypes.byref(rein), None, ctypes.byref(zusatz), None, None, 0x1, ctypes.byref(raus)):  # UI_FORBIDDEN
        raise OSError("DPAPI fehlgeschlagen")
    try:
        return ctypes.string_at(raus.pbData, raus.cbData)
    finally:
        kernel32.LocalFree(raus.pbData)


# --- Dateien ----------------------------------------------------------------------------------------------

def _attribute(p: Path, wert: int) -> None:
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(p), wert)
    except (AttributeError, OSError):
        pass


def _schreiben(p: Path, text: str, verstecken: bool) -> None:
    """Atomar schreiben; eine versteckte Systemdatei lässt Windows sonst nicht überschreiben."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".neu")
    tmp.write_text(text, encoding="utf-8")
    if p.exists():
        _attribute(p, _NORMAL)
    os.replace(tmp, p)
    if verstecken:
        _attribute(p, _VERSTECKT_SYSTEM)


def lesen(ordner: Path) -> Optional[dict]:
    """kennung.json eines Programmordners – None, wenn sie fehlt oder kaputt ist."""
    try:
        d = json.loads((ordner / KENNUNG_DATEI).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("kennung"), str) or len(d["kennung"]) != 32:
        return None
    return d


def gemerkter_ordner() -> Optional[tuple[Path, str]]:
    """(Pfad, Kennung) aus ordner.json – None ohne Datei, nach Veränderung oder unter einem anderen Benutzer."""
    try:
        roh = json.loads(register_datei().read_text(encoding="utf-8"))
        d = json.loads(_dpapi(base64.b64decode(roh["daten"]), schuetzen=False).decode("utf-8"))
        return Path(d["pfad"]), str(d["kennung"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def pflegen(ordner: Path, build: str = "") -> Optional[dict]:
    """Beim Start: Kennung anlegen, Umbenennung erkennen (früherer Name nach `vorher`), ordner.json
    nachziehen. Schreibt nur bei Änderungen. Gibt die Kennung zurück (None, wenn nichts ging)."""
    ordner = ordner.resolve()
    d = lesen(ordner)
    neu = d is None
    if neu:
        d = {"kennung": secrets.token_hex(16), "name": ordner.name, "vorher": [], "umbenannt": ""}
    elif d.get("name") != ordner.name:
        vorher = [n for n in d.get("vorher") or [] if isinstance(n, str)]
        d["vorher"] = (vorher + [str(d.get("name") or "")])[-MAX_VORHER:]
        d["name"], d["umbenannt"] = ordner.name, date.today().isoformat()
        neu = True
    try:
        if neu:
            _schreiben(ordner / KENNUNG_DATEI, json.dumps(d, ensure_ascii=False, indent=1), verstecken=True)
        if gemerkter_ordner() != (ordner, d["kennung"]):
            inhalt = json.dumps({"kennung": d["kennung"], "pfad": str(ordner)}).encode("utf-8")
            _schreiben(register_datei(), json.dumps({"v": 1, "build": build,
                                                     "daten": base64.b64encode(_dpapi(inhalt, True)).decode()}),
                       verstecken=False)
    except OSError:
        return None
    return d
