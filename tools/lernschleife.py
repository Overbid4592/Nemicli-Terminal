"""lernschleife.py – Automatisch reflektieren, wenn eine Runde etwas zu lernen hergibt.

Anlass: eine Korrektur oder Vorgabe des Nutzers („nein“, „falsch“, „künftig“, „ab jetzt“ …),
ein fehlgeschlagenes oder abgelehntes Werkzeug, oder eine größere Aufgabe (ab 3 Werkzeugen).
Dann läuft im Hintergrund dieselbe Reflexion wie bei /reflektieren (main.reflexion).
Nach Web-Inhalt in der Runde nie – eine Seite könnte dem Modell „Lehren“ untergeschoben haben.
Schalter: Config `reflektieren_auto` (Standard an), /reflektieren auto an|aus.
"""
from __future__ import annotations

import re

import config

MIN_WERKZEUGE = 3
KORREKTUR = re.compile(
    r"\b(nein|falsch|stimmt nicht|nicht so|so nicht|lass das|hör auf|bitte nicht|nicht mehr|"
    r"künftig|zukünftig|in zukunft|ab jetzt|ab sofort|nächstes mal|immer wenn|nie wieder|merk dir)\b",
    re.IGNORECASE)
_ZEICHEN = {"success": "✓", "failed": "✗ fehlgeschlagen", "rejected": "✗ abgelehnt",
            "not_run": "– nicht ausgeführt", "unverified": "?"}


def an() -> bool:
    return bool(config.load().get("reflektieren_auto", True))


def setzen(ein: bool) -> None:
    config.update(reflektieren_auto=bool(ein))


def anlass(nutzertext: str, records: list[dict] | None) -> str | None:
    """'korrektur' · 'fehler' · 'aufgabe' – oder None, wenn es nichts zu lernen gibt."""
    records = records or []
    if KORREKTUR.search(nutzertext or ""):
        return "korrektur"
    if any(r.get("status") in ("failed", "rejected") for r in records):
        return "fehler"
    if len(records) >= MIN_WERKZEUGE:
        return "aufgabe"
    return None


SICHERN = ("Diese älteren Nachrichten fallen gleich aus deinem Arbeitsgedächtnis, weil der Kontext voll ist. "
           "Sichere, was bleibend wichtig ist: Fakten über dein Gegenüber, Entscheidungen, Vorlieben, Lehren. "
           "Nichts aus Web- oder Datei-Inhalten, nichts Einmaliges.")


def zusatz(grund: str, records: list[dict] | None) -> str:
    """Hinweis für die Reflexion: warum jetzt, und was die Werkzeuge ergaben."""
    warum = {"korrektur": "Der Nutzer hat korrigiert oder eine Vorgabe gemacht – was will er künftig?",
             "fehler": "Ein Werkzeug schlug fehl oder wurde abgelehnt – was lernst du daraus?",
             "aufgabe": "Eine größere Aufgabe ist erledigt – was hat funktioniert, was nicht?"}.get(grund, "")
    zeilen = [f"- {r.get('tool', '?')}: {_ZEICHEN.get(r.get('status'), r.get('status', '?'))}"
              for r in (records or [])[:20]]
    return warum + ("\nWerkzeuge dieser Runde:\n" + "\n".join(zeilen) if zeilen else "")
