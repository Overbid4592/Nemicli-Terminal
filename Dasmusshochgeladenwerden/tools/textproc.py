"""
textproc.py - Streaming-Hilfen für Modell-Ausgaben.

Manche lokalen Modelle (z.B. GLM) schreiben ihr Nachdenken in <think>…</think>-
Tags mitten in den Text. `think_splitter` trennt das beim Streamen sauber:
Inhalt zwischen den Tags wird als "thinking" gemeldet (gedimmt angezeigt),
der Rest als "text" (die eigentliche Antwort).
"""

from __future__ import annotations

OPEN, CLOSE = "<think>", "</think>"


def _tail_len(buf: str, marker: str) -> int:
    """Längstes Suffix von buf, das ein Präfix von marker ist (gegen zerrissene Tags)."""
    for k in range(min(len(buf), len(marker) - 1), 0, -1):
        if buf[-k:] == marker[:k]:
            return k
    return 0


def think_splitter():
    """Liefert (feed, flush). feed(delta)->Liste von (kind, text); kind = 'text'|'thinking'.
    flush() gibt den Rest am Ende aus."""
    state = {"in_think": False, "buf": ""}

    def feed(delta: str):
        out = []
        state["buf"] += delta
        while True:
            buf = state["buf"]
            if not state["in_think"]:
                idx = buf.find(OPEN)
                if idx == -1:
                    keep = _tail_len(buf, OPEN)
                    if len(buf) > keep:
                        out.append(("text", buf[:len(buf) - keep]))
                        state["buf"] = buf[len(buf) - keep:]
                    break
                if idx > 0:
                    out.append(("text", buf[:idx]))
                state["buf"] = buf[idx + len(OPEN):]
                state["in_think"] = True
            else:
                idx = buf.find(CLOSE)
                if idx == -1:
                    keep = _tail_len(buf, CLOSE)
                    if len(buf) > keep:
                        out.append(("thinking", buf[:len(buf) - keep]))
                        state["buf"] = buf[len(buf) - keep:]
                    break
                if idx > 0:
                    out.append(("thinking", buf[:idx]))
                state["buf"] = buf[idx + len(CLOSE):]
                state["in_think"] = False
        return out

    def flush():
        if not state["buf"]:
            return []
        kind = "thinking" if state["in_think"] else "text"
        rest = state["buf"]
        state["buf"] = ""
        return [(kind, rest)]

    return feed, flush
