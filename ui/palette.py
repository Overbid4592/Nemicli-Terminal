"""palette.py – Befehls-Palette (Strg+K): alle Befehle, ihre Unterpunkte, Modelle und Persönlichkeiten in einer
Suche, die Tippfehler verzeiht (ä/ae gleich, ähnliche Wörter zählen).

Ein Eintrag ist (Zeile, Beschreibung, Befehl, braucht_eingabe): mit braucht_eingabe kommt der Befehl nur in die
Eingabe und wartet auf den Rest, sonst wird er gleich ausgeführt.
"""
from __future__ import annotations

import difflib
import re
from typing import Callable, NamedTuple


class Eintrag(NamedTuple):
    zeile: str
    beschreibung: str
    befehl: str
    braucht_eingabe: bool = False
    suche: str = ""                      # zusätzlich durchsucht, nicht angezeigt (z. B. Text des Befehls)


VORNE = ("/model", "/musik", "/bild", "/gui", "/voice", "/theme", "/persönlichkeiten", "/kontext", "/backup",
         "/help")


def _falten(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return s


def eintraege(auswahl: Callable[[str], list] | None = None, modelle: dict | None = None,
              personas: list | None = None) -> list[Eintrag]:
    """Alle Einträge: jeder Befehl, die Punkte seines Auswahlmenüs (mit ✓ für das Aktive), Modelle, Persönlichkeiten."""
    import commands
    auswahl = auswahl or commands.auswahl
    aus: list[Eintrag] = []
    gesehen = set()
    for c in commands.COMMAND_LIST:
        if c.name in gesehen:
            continue
        gesehen.add(c.name)
        aus.append(Eintrag(c.name, c.short, c.name, braucht_eingabe=False))
        try:
            punkte = auswahl(c.name)
        except Exception:
            punkte = []
        for wert, text, frage in punkte:
            if not wert:
                continue
            leer = "{}" in wert or bool(frage)
            befehl = f"{c.name} {wert.replace('{}', '').strip()}".strip()
            aus.append(Eintrag(f"{c.name} {wert.replace('{}', '…')}".strip(), str(text).strip(),
                               befehl + (" " if leer else ""), braucht_eingabe=leer, suche=c.short))
    for ref, name in (modelle or {}).items():
        aus.append(Eintrag(f"/model {ref}", f"Modell wechseln: {name or ref}", f"/model {ref}"))
    for key, name in personas or []:
        aus.append(Eintrag(f"/persönlichkeiten {key}", f"Persönlichkeit: {name}", f"/persönlichkeiten {key}"))
    return aus


def _aehnlich(frage: str, wort: str) -> float:
    """0..1: Tippfehler („musick“ ~ musik) über die Ähnlichkeit, Wortstämme („farbe“ ~ farbschema) über
    einen gemeinsamen Anfang von mindestens 4 Buchstaben."""
    r = difflib.SequenceMatcher(None, frage, wort).ratio()
    anfang = 0
    for a, b in zip(frage, wort):
        if a != b:
            break
        anfang += 1
    return max(r, 0.85 if anfang >= 4 else 0.0)


def _wortnaehe(frage: str, text: str) -> float:
    """Wie gut passt das ähnlichste Wort im Text zur Frage (0..1)."""
    return max((_aehnlich(frage, w) for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3), default=0.0)


def bewerten(e: Eintrag, frage: str) -> float:
    """Punkte für einen Eintrag (0 = passt nicht). Name vor Beschreibung, Anfang vor Mitte, genau vor ähnlich."""
    f = _falten(frage.strip().lstrip("/"))
    if not f:
        return 0.0
    name, besch = _falten(e.zeile.lstrip("/")), _falten(f"{e.beschreibung} {e.suche}")
    teile = f.split()
    kopf = name.split()[0] if name else ""
    if len(teile) == 1 and " " not in name and (kopf == f or _aehnlich(f, kopf) >= 0.83):
        return 11.0 - len(name) / 100                       # der Befehl selbst, auch vertippt
    if all(t in name for t in teile):
        return 10.0 - (0 if name.startswith(teile[0]) else 2) - len(name) / 100
    if all(t in name or t in besch for t in teile):
        return 6.0 + 0.5 * sum(t in name for t in teile) - len(name) / 100
    im_namen = min(_wortnaehe(t, name) for t in teile)
    ueberall = min(max(_wortnaehe(t, name), _wortnaehe(t, besch)) for t in teile)
    if im_namen >= 0.72:
        return 5.0 * im_namen - len(name) / 100
    return 4.0 * ueberall - len(name) / 100 if ueberall >= 0.72 else 0.0


def suchen(liste: list[Eintrag], frage: str, n: int = 12) -> list[Eintrag]:
    """Die besten n Treffer; ohne Frage die wichtigsten Befehle zuerst."""
    if not frage.strip():
        vorne = [e for name in VORNE for e in liste if e.zeile == name]
        return (vorne + [e for e in liste if e not in vorne and " " not in e.zeile])[:n]
    punkte = [(bewerten(e, frage), i, e) for i, e in enumerate(liste)]
    return [e for p, _i, e in sorted((x for x in punkte if x[0] > 0), key=lambda x: (-x[0], x[1]))[:n]]
