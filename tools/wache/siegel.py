"""siegel.py - SHA-256 der Dateien, die die Wache selbst schreibt.

Die Wache merkt sich in ihrer Datenbank (Tabelle meta) die SHA-256 der zuletzt selbst geschriebenen Stände
einer Datei. Beim Laden zählt nur ein Stand, den sie selbst geschrieben hat; ein fremd getauschter Stand wird
nicht geladen und meldet Alarm WS-001. Die letzten MAX_STAENDE bleiben gültig, damit ein Zurückdrehen auf eine
eigene Vorversion (`/wache ml zurueck`) weiter geht.

Ohne gemerkten Stand (erster Lauf mit Siegel) wird der vorhandene Stand übernommen.
"""
from __future__ import annotations

import hashlib
import json

REGEL = "WS-001"
MAX_STAENDE = 8


def sha256(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


class Siegel:
    def __init__(self, speicher, name: str):
        self.speicher = speicher
        self.schluessel = f"siegel:{name}"

    def _staende(self) -> list[str]:
        try:
            liste = json.loads(self.speicher.meta(self.schluessel, "[]") or "[]")
        except ValueError:
            return []
        return [str(x) for x in liste] if isinstance(liste, list) else []

    def merken(self, digest: str) -> None:
        """Einen selbst geschriebenen Stand festhalten (der jüngste steht hinten)."""
        liste = [d for d in self._staende() if d != digest] + [digest]
        self.speicher.meta_setzen(self.schluessel, json.dumps(liste[-MAX_STAENDE:]))

    def pruefen(self, digest: str) -> str:
        """'ok' (eigener Stand), 'neu' (noch nichts gemerkt) oder 'fremd'."""
        liste = self._staende()
        if not liste:
            return "neu"
        return "ok" if digest in liste else "fremd"
