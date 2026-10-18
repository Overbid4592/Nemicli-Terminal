"""Funke im /einrichten-Gespräch: Zustand führen, Aktionen prüfen, Platzhalter füllen.

Der Türsteher lässt nur Aktionen durch, die zum Schritt passen: Eine Anrede muss in der Eingabe stehen, eine
Persönlichkeit in der Liste. Was er abweist, beantwortet Funke wie eine unklare Eingabe (er fragt selbst nach).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from einrichtungshelfer.laufzeit import Helfer, bereinigen
from einrichtungshelfer.schema import aktion_gueltig, aktion_lesen, zustand

NAME = "Funke"
MAX_VERSUCHE = 3
_DATEIEN = ("helfer.bin", "wortschatz.json")


def ordner() -> Path:
    try:
        from paths import ASSETS
    except Exception:
        ASSETS = Path(__file__).resolve().parent.parent
    return Path(ASSETS) / "einrichtungshelfer" / "modell"


def verfuegbar(pfad: Path | None = None) -> bool:
    pfad = pfad or ordner()
    return all((pfad / d).is_file() for d in _DATEIEN)


@dataclass
class Antwort:
    art: str          # "-", "abbruch", "anrede", "pers", "modell", "installieren", "venv"
    wert: str
    text: str         # fertig zum Anzeigen


def _anrede_schreiben(wert: str) -> str:
    """Kleingeschriebenes wird groß begonnen („tom“ → „Tom“); eigene Schreibweisen bleiben."""
    return " ".join(w[:1].upper() + w[1:] for w in wert.split()) if wert.islower() else wert


class Funke:
    def __init__(self, pfad: Path | None = None, helfer: Helfer | None = None):
        self.helfer = helfer or Helfer(pfad or ordner())
        self.name = ""                 # Anrede des Nutzers
        self.ersatzname = ""           # gilt, wenn der Nutzer keine Anrede nennen mag
        self.personen: list[str] = []
        self.standard = ""             # Persönlichkeit bei „egal“
        self.pers = ""

    def einsetzen(self, text: str) -> str:
        for k, v in (("{ich}", NAME), ("{name}", self.name or self.ersatzname or "du"),
                     ("{liste}", ", ".join(self.personen)), ("{pers}", self.pers or self.standard)):
            text = text.replace(k, v)
        return text

    def _zustand(self, schritt: str, felder: dict) -> str:
        if schritt == "anrede":
            return zustand("anrede")
        extra = dict(felder)
        if schritt == "pers":
            extra["pers"] = self.personen
        return zustand(schritt, name="ja" if self.name else "nein", **extra)

    def _pruefen(self, schritt: str, art: str, wert: str, nutzer: str) -> tuple[str, str] | None:
        """Gibt (art, wert) bereinigt zurück oder None, wenn die Aktion abgewiesen wird."""
        if not aktion_gueltig(schritt, art, wert):
            return None
        if art == "anrede" and wert != "-":
            return (art, _anrede_schreiben(wert)) if wert.lower() in bereinigen(nutzer).lower() else None
        if art == "pers" and wert not in ("neu", "egal"):
            klein = {p.lower(): p for p in self.personen}
            treffer = difflib.get_close_matches(wert.lower(), list(klein), n=1, cutoff=0.75)
            return (art, klein[treffer[0]]) if treffer else None
        return art, wert

    def anzeige(self, roh: str) -> str:
        """Halbfertige Antwort fürs Mitlesen: ohne Aktions-Klammer, ohne angefangenen Platzhalter."""
        if roh.lstrip().startswith("[") and "]" not in roh:    # Aktion noch nicht fertig
            return ""
        text = aktion_lesen(roh)[2]
        if text.count("{") > text.count("}"):
            text = text[:text.rfind("{")]
        return self.einsetzen(text)

    def sagen(self, schritt: str, nutzer: str = "", beim_text=None, **felder) -> Antwort:
        """Eine Runde: ohne Eingabe stellt Funke die Frage des Schritts, mit Eingabe reagiert er darauf.
        beim_text(text) bekommt die Antwort Wort für Wort (fürs Mitlesen)."""
        zust = self._zustand(schritt, felder)
        mitlesen = None if beim_text is None else (lambda roh: beim_text(self.anzeige(roh)))
        art, wert, text = aktion_lesen(self.helfer.antworten(zust, nutzer, beim_token=mitlesen))
        if nutzer:
            geprueft = self._pruefen(schritt, art, wert, nutzer)
            if geprueft is None:
                # Abgewiesen: Funke fragt in eigenen Worten nach, wie bei einer unklaren Eingabe.
                art, wert = "-", ""
                text = aktion_lesen(self.helfer.antworten(zust, "?", beim_token=mitlesen))[2]
            else:
                art, wert = geprueft
        else:
            art, wert = "-", ""
        if art == "anrede":
            self.name = self.ersatzname if wert == "-" else wert
        elif art == "pers":
            self.pers = self.standard if wert in ("neu", "egal") else wert
        return Antwort(art, wert, self.einsetzen(text))
