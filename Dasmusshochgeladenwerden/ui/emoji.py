"""
emoji.py - Wandelt Smileys & Kürzel in echte Emojis um.

Damit du im Terminal bequem Emojis nutzen kannst, ohne sie umständlich zu tippen:
  =)  <3  :D  xD  ...        -> 🙂 ❤️ 😄 😆
  :herz:  :feuer:  :rakete:  -> ❤️ 🔥 🚀

Wird auf deine Eingabe angewendet, bevor sie an NemiCLI geht.
"""

from __future__ import annotations

import re

# Klassische Smileys (längere/speziellere zuerst, damit nichts kollidiert)
EMOTICONS: list[tuple[str, str]] = [
    ("<3", "❤️"),
    (":-)", "🙂"), (":-D", "😄"), (":-(", "🙁"), (":-P", "😛"),
    ("xD", "😆"), ("XD", "😆"),
    (":D", "😄"), ("=D", "😄"),
    (";)", "😉"), (";-)", "😉"),
    (":P", "😛"), (":p", "😛"),
    (":O", "😮"), (":o", "😮"),
    (":(", "🙁"), ("=(", "🙁"),
    (":)", "🙂"), ("=)", "🙂"),
]

# Kürzel zwischen Doppelpunkten  ->  :name:
SHORTCODES: dict[str, str] = {
    "herz": "❤️", "love": "❤️",
    "lol": "😂", "lach": "😂",
    "freu": "😊", "smile": "😊", "lächeln": "😊",
    "zwinker": "😉", "wink": "😉",
    "daumen": "👍", "top": "👍", "ok": "👍",
    "feuer": "🔥", "fire": "🔥",
    "rakete": "🚀", "rocket": "🚀",
    "stern": "⭐", "star": "⭐",
    "denk": "🤔", "hmm": "🤔",
    "party": "🎉", "feier": "🎉",
    "cool": "😎",
    "traurig": "😢", "sad": "😢",
    "hallo": "👋", "hi": "👋", "winke": "👋",
    "gehirn": "🧠", "brain": "🧠",
    "check": "✅", "haken": "✅",
    "blitz": "⚡",
    "schau": "👀", "auge": "👀",
    "kaffee": "☕",
    "hundert": "💯",
    "herzaugen": "😍",
    "kuss": "😘",
    "zunge": "😛",
    "weinen": "😭",
}

_SHORT_RE = re.compile(r":([a-zäöü]+):", re.IGNORECASE)

# Emoticons nur ersetzen, wenn sie frei stehen (Leerzeichen/Satzzeichen ringsum) –
# so bleibt echter Code wie  x<3 ,  dict[str,int]={}  oder  if a==b:  unangetastet.
_EMO_MAP = dict(EMOTICONS)
_EMOTICON_RE = re.compile(
    r"(?<!\S)(" + "|".join(re.escape(tok) for tok, _ in EMOTICONS) + r")(?=[\s.,!?;:)]|$)"
)

# Code soll komplett in Ruhe gelassen werden: ```Blöcke``` und `inline`.
_CODE_SPLIT = re.compile(r"(```.*?```|`[^`]*`)", re.DOTALL)


def _expand_plain(text: str) -> str:
    """Wandelt Smileys & :kürzel: in einem Nicht-Code-Stück um."""
    text = _EMOTICON_RE.sub(lambda m: _EMO_MAP[m.group(1)], text)
    text = _SHORT_RE.sub(lambda m: SHORTCODES.get(m.group(1).lower(), m.group(0)), text)
    return text


def expand(text: str) -> str:
    """Ersetzt Smileys und :kürzel: durch echte Emojis – aber niemals in Code.

    Code-Abschnitte (```Blöcke``` und `inline`) bleiben unberührt, und freie
    Smileys werden nur an Wortgrenzen ersetzt, damit z.B. `x<3` oder `a==b:`
    nicht versehentlich zu Emojis werden.
    """
    parts = _CODE_SPLIT.split(text)
    # split() mit einer Gruppe liefert abwechselnd: Text, Code, Text, Code, …
    return "".join(
        part if i % 2 else _expand_plain(part)
        for i, part in enumerate(parts)
    )
