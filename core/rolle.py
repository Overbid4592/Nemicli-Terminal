"""Rolle halten: erkennt, wenn die KI über sich selbst in der dritten Person schreibt.

Zwei sichere Muster, beide mit dem eigenen Namen: eine Sternchen-Aktion, die mit dem Namen beginnt
(*Name lächelt*), und ein Satz, der mit dem Namen und einem Verb beginnt (Name findet …). Pronomen
(„sie lacht“) bleiben außen vor – sie meinen oft jemand anderen. Code wird nicht geprüft.
"""
from __future__ import annotations

import re

_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)
# Verben in der dritten Person: meist auf -t (lacht, findet, grinst), dazu die häufigen Ausnahmen.
_VERB = r"(?:[a-zäöüß]{2,}t|ist|war|kann|will|mag|muss|darf|soll|weiß)\b"
# Ausdrücklich gewünschte Erzählung in der dritten Person: dann nicht eingreifen.
_ERZAEHLUNG = re.compile(r"dritte[nr]?\s+Person|Erzähler", re.I)


def vorname(name: str) -> str:
    """Vom Anzeigenamen das erste Wort („Name – Zusatz“ → Name)."""
    n = re.split(r"[\s–\-:,(]+", str(name or "").strip(" *_"), maxsplit=1)[0].strip(" *_")
    return n if len(n) >= 2 else ""


def dritte_person(text: str, name: str, frage: str = "") -> str | None:
    """Die erste Stelle, an der die KI über sich als «Name» erzählt – sonst None."""
    n = vorname(name)
    if not n or not text or _ERZAEHLUNG.search(frage or ""):
        return None
    t = re.sub(r"\*+", "*", re.sub(r"_{2,}", "", _CODE.sub(" ", text)))
    nm = re.escape(n)
    muster = (
        rf"\*\s*{nm}\s+{_VERB}[^*\n]{{0,120}}\*",                          # *Name lächelt*
        rf"(?:^|[.!?…:]\s+|\n)[\s>*\-]*{nm}\*?\s+{_VERB}",                 # Name findet …
    )
    for m in muster:
        if (treffer := re.search(m, t, re.M)):
            return treffer.group(0).strip(" \n.!?…:>-")[:80]
    return None


def stups(stelle: str, name: str) -> str:
    """Nachricht an die KI: die letzte Antwort in der Ich-Form neu schreiben."""
    n = vorname(name) or name
    return (f"🎭 [Rolle] Du hast über dich in der dritten Person geschrieben („{stelle}“). Du bist {n}: schreib "
            "deine letzte Antwort noch einmal vollständig in der Ich-Form (ich, mich, mein) – auch die Aktionen "
            "in Sternchen (*lächelt*, nicht *" + n + " lächelt*). Behalte Inhalt und Sprechweise, erwähne diesen "
            "Hinweis nicht.")
