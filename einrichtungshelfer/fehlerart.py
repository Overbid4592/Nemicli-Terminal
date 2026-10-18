"""Ordnet Fehler des Sprach-Modells einer Fehler-Art zu (schema.FEHLER) – für Funke im Notfall.

Entschieden wird über Typ und HTTP-Status, nicht über die Meldung des Anbieters: Die kann Schlüssel, Pfade oder
Namen enthalten und erreicht Funke nie. Nur NemiCLIs eigene RuntimeError-Texte (feste Wortlaute im Code)
werden gelesen. Kein Import von openai/anthropic/torch – Typen werden am Namen erkannt.
"""
from __future__ import annotations

_NETZ = {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
         "PoolTimeout", "TimeoutException", "NetworkError", "RemoteProtocolError", "ConnectionError", "TimeoutError",
         "ConnectionRefusedError", "ConnectionResetError", "gaierror"}
_SCHLUESSEL = {"AuthenticationError", "PermissionDeniedError"}
_LIMIT = {"RateLimitError"}
_FEHLT = {"NotFoundError"}
_ANBIETER = {"InternalServerError", "ServiceUnavailableError", "OverloadedError", "APIStatusError"}

# Menü, das Funke bei „ja“ öffnet
MENUE = {"torch_fehlt": "/venv", "unbekannt": "/selbsttest"}
MENUE_STANDARD = "/model"


def _typen(exc: BaseException) -> set[str]:
    return {k.__name__ for k in type(exc).__mro__}


def einordnen(exc: BaseException, anbieter: str = "") -> str:
    """Fehler-Art zu einer Ausnahme. anbieter: provider_id des Backends (z. B. "ollama"), sonst leer."""
    typen = _typen(exc)
    if "OutOfMemoryError" in typen:                       # torch.OutOfMemoryError (CUDA)
        return "vram"
    if "GGUFError" in typen:
        return "datei"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return "schluessel"
        if status == 429:
            return "limit"
        if status == 404:
            return "ollama" if anbieter == "ollama" else "modell_fehlt"
        if status >= 500:
            return "ollama" if anbieter == "ollama" else "anbieter"
    if typen & _SCHLUESSEL:
        return "schluessel"
    if typen & _LIMIT:
        return "limit"
    if typen & _FEHLT:
        return "modell_fehlt"
    if typen & _NETZ:
        return "ollama" if anbieter == "ollama" else "netz"
    if typen & _ANBIETER:
        return "anbieter"
    if isinstance(exc, RuntimeError):                     # NemiCLIs eigene Meldungen (feste Wortlaute)
        text = str(exc)
        if "fehlt torch" in text:
            return "torch_fehlt"
        if "Kein (lokales) Modell" in text or "Kein lokales Modell" in text:
            return "datei"
        if "Kein Key" in text or "API-Key" in text or "API_KEY" in text:
            return "ollama" if anbieter == "ollama" else "schluessel"
        if "out of memory" in text.lower():
            return "vram"
    return "unbekannt"


def menue(fehler: str) -> str:
    return MENUE.get(fehler, MENUE_STANDARD)
