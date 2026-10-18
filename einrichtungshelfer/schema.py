"""Gemeinsames Format von Trainingsdaten und Laufzeit.

Ein Beispiel ist genau eine Runde:
    <z>schritt=pers;name=ja;pers=Nemi,Mira</z><n>nimm mira</n><h>[pers=mira] Schöne Wahl! …</h>

- <z> Zustand, vom Code gesetzt. Felder je Schritt siehe SCHRITTE.
- <n> Eingabe des Nutzers; leer = der Helfer stellt die Frage des Schritts.
- <h> Antwort: zuerst die Aktion in eckigen Klammern ([-] = keine), dann der Text.
Platzhalter im Text füllt der Code ein, damit das Modell keine Namen abschreiben muss.
"""
from __future__ import annotations

import re

SPEZIAL = ("<z>", "</z>", "<n>", "</n>", "<h>", "</h>", "<?>")
PLATZHALTER = ("{name}", "{ich}", "{liste}", "{pers}")
ATOMAR = SPEZIAL + PLATZHALTER
# Funke Groß (Chat-Modell): zusätzlich Vorgaben <v> und Denken <d>. Hinten angehängt, damit ältere
# Wortschätze ihre Nummern behalten.
ATOMAR_GROSS = ATOMAR + ("<v>", "</v>", "<d>", "</d>")

# Schritt → erlaubte Aktionen (außer "-" und "abbruch", die überall gelten).
SCHRITTE: dict[str, tuple[str, ...]] = {
    "anrede": ("anrede",),
    "pers": ("pers",),
    "modell": ("modell",),
    "pruefung": ("installieren",),
    "torch": ("venv",),
    "ende": (),
    # Notfall: das große Modell ist ausgefallen (fehler=<Art>); plaudern: es ist keins eingerichtet.
    # oeffnen=ja: der Code öffnet das Menü, das zur Fehler-Art gehört.
    "notfall": ("oeffnen",),
    "plaudern": ("oeffnen",),
}
WERTE = {
    "modell": ("cloud", "lokal", "spaeter"),
    "installieren": ("ja", "nein"),
    "venv": ("ja", "nein"),
    "oeffnen": ("ja", "nein"),
}
# Fehler-Arten, die der Code erkennt. Funke bekommt nur die Art, nie den Originaltext (Datenschutz).
FEHLER = ("schluessel", "limit", "anbieter", "netz", "ollama", "modell_fehlt", "vram", "torch_fehlt",
          "datei", "kein_modell", "unbekannt")

_AKTION = re.compile(r"^\s*\[([^\]]*)\]\s*")


def zustand(schritt: str, **felder: object) -> str:
    """Zustand als Text; Listen werden mit Komma verbunden, leere Felder entfallen."""
    teile = [f"schritt={schritt}"]
    for k, v in felder.items():
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        if v is None or v == "":
            continue
        teile.append(f"{k}={v}")
    return ";".join(teile)


def eingabe(zust: str, nutzer: str) -> str:
    """Alles vor der Antwort – so beginnt jede Anfrage an das Modell."""
    return f"<z>{zust}</z><n>{nutzer}</n><h>"


def beispiel(zust: str, nutzer: str, antwort: str) -> str:
    return eingabe(zust, nutzer) + antwort + "</h>"


def aktion_lesen(antwort: str) -> tuple[str, str, str]:
    """'[anrede=tom] Hallo {name}!' → ("anrede", "tom", "Hallo {name}!"). Ohne Klammer: ("-", "", text)."""
    m = _AKTION.match(antwort)
    if not m:
        return "-", "", antwort.strip()
    inhalt = m.group(1).strip()
    art, _, wert = inhalt.partition("=")
    return (art.strip() or "-"), wert.strip(), antwort[m.end():].strip()


def aktion_gueltig(schritt: str, art: str, wert: str) -> bool:
    """Passt die Aktion zum Schritt? Freie Werte (Anrede, Persönlichkeit) prüft der Aufrufer."""
    if art in ("-", "abbruch"):
        return not wert
    if art not in SCHRITTE.get(schritt, ()):
        return False
    if art in WERTE:
        return wert in WERTE[art]
    return bool(wert)
