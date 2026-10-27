"""verknuepfung.py - Ziel einer Windows-Verknüpfung (.lnk) lesen, ohne COM und ohne sie auszuführen.

Eine .lnk hat kein Signaturformat; prüfen lässt sich nur, worauf sie zeigt. Gelesen wird das Format
MS-SHLLINK: Kopf, LinkInfo (lokaler Pfad), die Textfelder (relativer Pfad, Arbeitsordner, Argumente) und
der Umgebungsvariablen-Block. Höchstens 64 KB, jeder Fehler ergibt „nicht lesbar“ (None).
"""
from __future__ import annotations

import os
import struct

_KOPF = 0x4C
_CLSID = bytes.fromhex("0114020000000000c000000000000046")
_HAT_IDLISTE, _HAT_LINKINFO, _HAT_NAME, _HAT_RELATIV, _HAT_ORDNER, _HAT_ARGUMENTE, _HAT_SYMBOL, _UNICODE = (
    0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80)
_UMGEBUNG = 0xA0000001
MAX_BYTES = 64 * 1024


def _ansi(daten: bytes, ab: int) -> str:
    ende = daten.find(b"\0", ab)
    return daten[ab:ende if ende >= 0 else len(daten)].decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def lesen(pfad: str) -> dict | None:
    """{ziel, argumente, ordner} oder None. `ziel` ist ein Dateipfad (Umgebungsvariablen aufgelöst)."""
    try:
        with open(pfad, "rb") as f:
            d = f.read(MAX_BYTES)
    except OSError:
        return None
    try:
        if len(d) < _KOPF or struct.unpack_from("<I", d, 0)[0] != _KOPF or d[4:20] != _CLSID:
            return None
        flags = struct.unpack_from("<I", d, 0x14)[0]
        pos = _KOPF
        if flags & _HAT_IDLISTE:
            pos += 2 + struct.unpack_from("<H", d, pos)[0]
        ziel = ""
        if flags & _HAT_LINKINFO:
            groesse, kopf, li_flags, _vol, basis, _netz, suffix = struct.unpack_from("<7I", d, pos)
            if li_flags & 1 and basis:
                ziel = _ansi(d, pos + basis)
                if kopf >= 0x24:                                  # Unicode-Fassungen vorhanden
                    basis_u = struct.unpack_from("<I", d, pos + 0x1C)[0]
                    if basis_u:
                        ende = d.find(b"\0\0", pos + basis_u)
                        while ende >= 0 and (ende - pos - basis_u) % 2:
                            ende = d.find(b"\0\0", ende + 1)
                        ziel = d[pos + basis_u:ende].decode("utf-16-le", "replace") or ziel
                anhang = _ansi(d, pos + suffix) if suffix else ""
                if anhang:
                    ziel = os.path.join(ziel, anhang)
            pos += groesse
        texte = {}
        breite = 2 if flags & _UNICODE else 1
        for bit, name in ((_HAT_NAME, "name"), (_HAT_RELATIV, "relativ"), (_HAT_ORDNER, "ordner"),
                          (_HAT_ARGUMENTE, "argumente"), (_HAT_SYMBOL, "symbol")):
            if flags & bit:
                n = struct.unpack_from("<H", d, pos)[0]
                roh = d[pos + 2:pos + 2 + n * breite]
                texte[name] = roh.decode("utf-16-le" if breite == 2 else "latin-1", "replace")
                pos += 2 + n * breite
        while pos + 8 <= len(d) and not ziel:                   # Zusatzblöcke: Ziel mit %VARIABLEN%
            groesse, art = struct.unpack_from("<II", d, pos)
            if groesse < 8:
                break
            if art == _UMGEBUNG and groesse >= 0x314:
                ziel = d[pos + 268:pos + 788].decode("utf-16-le", "replace").split("\0")[0] or _ansi(d, pos + 8)
            pos += groesse
        if not ziel and texte.get("relativ"):
            ziel = os.path.normpath(os.path.join(os.path.dirname(pfad), texte["relativ"]))
    except (struct.error, IndexError, ValueError):
        return None
    if not ziel:
        return None
    return {"ziel": os.path.expandvars(ziel), "argumente": texte.get("argumente", ""),
            "ordner": os.path.expandvars(texte.get("ordner", ""))}
