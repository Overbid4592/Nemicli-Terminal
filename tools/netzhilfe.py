"""
netzhilfe.py - Gemeinsame Netz-Bausteine: ein httpx-Client je Modul, Gesamtfrist beim Lesen,
einmaliges Wiederholen bei 429/503.

httpx begrenzt nur jeden einzelnen Lese-Vorgang. Ein Server, der alle paar Sekunden ein Byte
schickt, hielte einen Abruf sonst beliebig lange offen.
"""
from __future__ import annotations

import email.utils
import http.cookiejar
import threading
import time

import httpx

WIEDERHOLEN_BEI = (429, 503)
MAX_WARTEN_S = 10.0              # länger verlangte Pausen: nicht wiederholen
STANDARD_WARTEN_S = 2.0          # ohne Retry-After
_schlafen = time.sleep


class FristAbgelaufen(httpx.TimeoutException):
    """Gesamtfrist eines Abrufs überschritten."""


class ZuGross(ValueError):
    """Antwort größer als erlaubt."""


def _ohne_cookies() -> http.cookiejar.CookieJar:
    return http.cookiejar.CookieJar(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))


class GeteilterClient:
    """Ein httpx.Client für alle Abrufe eines Moduls (thread-sicher, Verbindungen bleiben offen).
    Cookies werden nicht angenommen: ein Abruf soll den nächsten nicht wiedererkennbar machen.
    Ist httpx.Client ersetzt worden (Testattrappe), entsteht ein neuer."""

    def __init__(self, **einstellungen):
        self._einstellungen = einstellungen
        self._client = None
        self._fabrik = None
        self._sperre = threading.Lock()

    def __call__(self) -> httpx.Client:
        fabrik = httpx.Client
        with self._sperre:
            c = self._client
            if c is None or self._fabrik is not fabrik or getattr(c, "is_closed", False):
                c = self._client = fabrik(**{"cookies": _ohne_cookies(), **self._einstellungen})
                self._fabrik = fabrik
            return c


def warten_s(r) -> float | None:
    """Pause vor dem zweiten Versuch laut Retry-After (Sekunden oder Datum).
    None: der Server verlangt mehr als MAX_WARTEN_S – dann nicht wiederholen."""
    roh = str(r.headers.get("retry-after") or "").strip()
    if not roh:
        return STANDARD_WARTEN_S
    try:
        s = float(roh)
    except ValueError:
        try:
            s = email.utils.parsedate_to_datetime(roh).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return STANDARD_WARTEN_S
    s = max(0.0, s)
    return s if s <= MAX_WARTEN_S else None


def lesen(r, ende: float, max_bytes: int | None = None, block: int | None = None) -> bytes:
    """Rumpf einer Stream-Antwort bis zur Frist `ende` (time.monotonic) und höchstens `max_bytes`."""
    daten = bytearray()
    for stueck in r.iter_bytes(block):
        daten += stueck
        if max_bytes is not None and len(daten) > max_bytes:
            raise ZuGross(f"Antwort größer als {max_bytes} Bytes")
        if time.monotonic() > ende:
            raise FristAbgelaufen("Gesamtfrist des Abrufs überschritten")
    return bytes(daten)


_OHNE = {"content-encoding", "content-length", "transfer-encoding"}


def _fertig(r, daten: bytes) -> httpx.Response:
    kopf = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in _OHNE]
    return httpx.Response(r.status_code, headers=kopf, content=daten, request=r.request)


def abrufen(client, methode: str, url: str, *, frist_s: float = 60.0, max_bytes: int | None = None,
            **kw) -> httpx.Response:
    """Ganze Antwort innerhalb der Gesamtfrist; bei 429/503 einmal nach Retry-After wiederholt.
    Die Antwort ist fertig gelesen (.content, .json(), raise_for_status())."""
    ende = time.monotonic() + frist_s
    for versuch in range(2):
        with client.stream(methode, url, **kw) as r:
            pause = warten_s(r) if versuch == 0 and r.status_code in WIEDERHOLEN_BEI else None
            if pause is None or time.monotonic() + pause >= ende:
                return _fertig(r, lesen(r, ende, max_bytes))
        _schlafen(pause)
    raise AssertionError("unerreichbar")
