"""Zeichen, die keine KI in NemiCLI ausgeben darf – herausgefiltert aus Antwort, Denktext und Verlauf.

`sperren(backend)` legt den Filter um stream, ask_once und ask_messages eines Chat-Backends (alle Anbieter,
lokal und Cloud) und räumt nach jeder Antwort auch den gespeicherten Verlauf des Backends auf, damit das Modell
das Zeichen nicht aus seinen eigenen früheren Antworten wieder aufgreift.
"""
from __future__ import annotations

import re

# Als Codepunkte statt als Zeichen im Quelltext.
GESPERRT = (0x1F60F,)                    # grinsendes Gesicht (Smirk)
_VARIANTE = 0xFE0F                       # Emoji-Darstellungswahl, gehört zum Zeichen davor
_MUSTER = re.compile("(?:" + "|".join(re.escape(chr(c)) for c in GESPERRT) + ")" + re.escape(chr(_VARIANTE)) + "?")
_DOPPELT = re.compile(r"[ \t]{2,}")


def ohne(text: str) -> str:
    """Text ohne gesperrte Zeichen. Bleiben dadurch zwei Leerzeichen nebeneinander, wird eines daraus."""
    if not text or not _MUSTER.search(text):
        return text
    return _DOPPELT.sub(" ", _MUSTER.sub("", text))


def _inhalt(c):
    if isinstance(c, str):
        return ohne(c)
    if isinstance(c, list):
        neu = []
        for b in c:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                b = {**b, "text": ohne(b["text"])}
            neu.append(b)
        return neu
    return c


def verlauf_putzen(messages) -> None:
    """Antworten der KI im Verlauf an Ort und Stelle säubern."""
    if not isinstance(messages, list):
        return
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant" and "content" in m:
            m["content"] = _inhalt(m["content"])


def sperren(backend):
    """Filter um die Ausgaben eines Chat-Backends legen (einmal je Objekt). Gibt das Backend zurück."""
    if backend is None or getattr(backend, "_zeichensperre", False):
        return backend
    stream = getattr(backend, "stream", None)
    if callable(stream):
        async def _stream(*a, **k):
            try:
                async for ev in stream(*a, **k):
                    if isinstance(ev, dict) and isinstance(ev.get("text"), str):
                        ev = {**ev, "text": ohne(ev["text"])}
                    yield ev
            finally:
                verlauf_putzen(getattr(backend, "messages", None))
        backend.stream = _stream
    for name in ("ask_once", "ask_messages"):
        f = getattr(backend, name, None)
        if callable(f):
            async def _frage(*a, _f=f, **k):
                r = await _f(*a, **k)
                return ohne(r) if isinstance(r, str) else r
            setattr(backend, name, _frage)
    backend._zeichensperre = True
    return backend
