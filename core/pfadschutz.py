"""Windows-Pfade in Markdown unverändert anzeigen.

Markdown liest `\\.` als maskierten Punkt und `__init__` als Hervorhebung – aus
C:\\Users\\name\\.ncli2\\torch\\__init__.py würde C:\\Users\\name.ncli2\\torch_init_.py.
Pfade außerhalb von Code werden darum als Inline-Code gesetzt; Codeblöcke und
Inline-Code bleiben, wie sie sind (mehrfaches Anwenden ändert nichts).
"""
from __future__ import annotations

import re

_CODE = re.compile(r"(```.*?(?:```|$)|`[^`\n]*`)", re.S)
_PFAD = re.compile(r"(?<![\w`])(?:[A-Za-z]:\\|\\\\[\w.$-])[^\s`'\"<>|*]*")
_ENDE = ".,;:!?)]"


def _setzen(m: re.Match) -> str:
    pfad = m.group(0)
    kern = pfad.rstrip(_ENDE)
    return f"`{kern}`{pfad[len(kern):]}" if kern else pfad


def schuetzen(text: str) -> str:
    if not text or "\\" not in text:
        return text
    teile = _CODE.split(text)
    for i in range(0, len(teile), 2):                  # gerade Stellen: Text außerhalb von Code
        teile[i] = _PFAD.sub(_setzen, teile[i])
    return "".join(teile)
