"""
keyvault.py - API-Keys schützen: Windows-DPAPI + Anzeige-Maske.

encrypt/decrypt: DPAPI über ctypes, an das Windows-Konto auf diesem PC gebunden. Eine kopierte
.env ist anderswo nicht lesbar; Programme, die unter demselben Konto laufen, können entschlüsseln.
mask: zeigt einen Key nur gekürzt an. Ohne Windows ist available() False (Keys bleiben Klartext).
"""

from __future__ import annotations

import base64
import os

MARKER = "dpapi:"          # so erkennen wir verschlüsselte Werte in der .env

_HAVE = os.name == "nt"
if _HAVE:
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        _crypt32 = ctypes.windll.crypt32
        _kernel32 = ctypes.windll.kernel32
    except Exception:
        _HAVE = False


def available() -> bool:
    """True, wenn der Windows-Umschlag (DPAPI) nutzbar ist."""
    return _HAVE


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(MARKER)


def _to_blob(data: bytes) -> "_BLOB":
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def encrypt(text: str) -> str | None:
    """Klartext -> verschlüsselter, mit MARKER versehener String. None bei Fehler."""
    if not _HAVE or text is None:
        return None
    try:
        blob_in = _to_blob(text.encode("utf-8"))
        blob_out = _BLOB()
        ok = _crypt32.CryptProtectData(ctypes.byref(blob_in), u"nemicli-key",
                                       None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return None
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return MARKER + base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def decrypt(value: str) -> str | None:
    """Verschlüsselten Wert -> Klartext. Nicht-verschlüsselte Werte kommen
    unverändert zurück. None nur, wenn das Entschlüsseln fehlschlägt."""
    if not is_encrypted(value):
        return value
    if not _HAVE:
        return None
    try:
        raw = base64.b64decode(value[len(MARKER):])
        blob_in = _to_blob(raw)
        blob_out = _BLOB()
        ok = _crypt32.CryptUnprotectData(ctypes.byref(blob_in), None,
                                         None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return None
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return out.decode("utf-8")
    except Exception:
        return None


def mask(secret: str, show: int = 8) -> str:
    """Key fürs Auge verstecken:  'sk-1234567890abcdef'  ->  'sk-12345•••••••••'."""
    if not secret:
        return ""
    if len(secret) <= show:
        return secret[:2] + "•" * 6
    return secret[:show] + "•" * min(len(secret) - show, 18)
