"""nutzerprofil.py – Was der Nutzer selbst über sich einträgt (/name, Desktop-Fenster).

Drei Felder in nemicli.config.json: `nutzername` (auch {{user}} in Persönlichkeiten),
`nutzer_anrede` (wie er genannt werden möchte) und `nutzer_ueber` (frei: was er mag,
was nicht, was die KI wissen soll). Der Abschnitt steht im System-Prompt direkt nach
der Persönlichkeit – jede Persönlichkeit kennt ihn ab der ersten Nachricht. Nur der
Nutzer ändert ihn; die KI hat dafür kein Werkzeug.
"""
from __future__ import annotations

import config

MAX_NAME = 80
MAX_ANREDE = 200
MAX_UEBER = 4000


def _zeile(text, grenze: int) -> str:
    return " ".join(str(text or "").split())[:grenze]


def laden() -> dict:
    c = config.load()
    return {"name": _zeile(c.get("nutzername"), MAX_NAME),
            "anrede": _zeile(c.get("nutzer_anrede"), MAX_ANREDE),
            "ueber": str(c.get("nutzer_ueber") or "").strip()[:MAX_UEBER]}


def speichern(name: str, anrede: str, ueber: str) -> dict:
    """Alle drei Felder setzen (leer = entfernen). Gibt den gespeicherten Stand zurück."""
    config.update(nutzername=_zeile(name, MAX_NAME), nutzer_anrede=_zeile(anrede, MAX_ANREDE),
                  nutzer_ueber=str(ueber or "").strip()[:MAX_UEBER])
    return laden()


def leer(profil: dict | None = None) -> bool:
    p = profil if profil is not None else laden()
    return not (p["name"] or p["anrede"] or p["ueber"])


def prompt_abschnitt() -> str:
    """Abschnitt für den System-Prompt – leer, wenn nichts eingetragen ist."""
    p = laden()
    if leer(p):
        return ""
    zeilen = ["", "", "# Dein Gegenüber (selbst eingetragen – gilt ab der ersten Nachricht)"]
    if p["name"]:
        zeilen.append(f"- Name: {p['name']}")
    if p["anrede"]:
        zeilen.append(f"- So möchte dein Gegenüber angesprochen werden: {p['anrede']} "
                      "(natürlich und abwechselnd, nicht in jeder Antwort und nicht als Gruß am Anfang)")
    if p["ueber"]:
        zeilen += ["- Über sich:", p["ueber"]]
    zeilen += ["Das hat dein Gegenüber selbst über sich geschrieben – halte dich von Anfang an daran. "
               "Was es im Gespräch anders sagt, geht vor.", ""]
    return "\n".join(zeilen)
