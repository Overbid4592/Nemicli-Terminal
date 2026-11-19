"""fensterstart.py – reine Fenster-Prozesse (eigenes Terminal, GUI-Fenster, Charakter-Galerie) früh verzweigen.

Diese Prozesse brauchen die App selbst nicht: Sie zeigen nur ein Fenster und verbinden sich mit der Sitzung
(oder starten sie in einer Pseudokonsole). main.py ruft `frueh()` direkt nach dem Setzen des Suchpfads; ohne
Treffer läuft alles wie bisher (main_start.py verzweigt dann selbst). Der Netz-Zwang gilt auch hier.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Aufträge ohne eigenes Fenster: NemiCLI.exe (Fenster-App) öffnet mit ihnen kein Terminal-Fenster
HINTERGRUND = ("--wache", "--vollscan", "--auftrag", "--sag", "--selftest", "--bild",
               "--systemcheck", "--kontext", "--malkind", "--bibliothekar", "--vorpruefer-lernen")
# Schalter, die main_start.py vor dem jeweiligen Fenster prüft – mit ihnen verzweigt main_start.py selbst
_VOR_GUI = ("--version", "--vorpruefer-lernen", "--malkind", "--bibliothekar", "--entpacken", "--kontext")
_VOR_CHARAKTERE = ("--nemiview", "--seitenbild", "--musik", "--lora")
_VOR_FENSTER = ("--termin", "--zeitplan", "--kompass", "--kern", "--kopf", "--vorschau")


def ziel(argv: list[str], frozen: bool, ohne_konsole: bool) -> str | None:
    """"gui", "charaktere", "fenster" oder None – in derselben Reihenfolge wie main_start.py."""
    if any(a in argv for a in _VOR_GUI):
        return None
    if "--gui" in argv:
        return "gui"
    if any(a in argv for a in _VOR_CHARAKTERE):
        return None
    if "--charaktere" in argv:
        return "charaktere"
    if any(a in argv for a in _VOR_FENSTER):
        return None
    if "--fenster" in argv or (frozen and ohne_konsole and not any(a in argv for a in HINTERGRUND)):
        return "fenster"
    return None


def kind_befehl(argv: list[str], frozen: bool, haupt_datei: Path) -> list[str]:
    """Befehl, mit dem das Terminal-Fenster NemiCLI in seiner Pseudokonsole startet."""
    rest = [a for a in argv[1:] if a != "--fenster"]
    if frozen:
        import paths
        return [str(paths.exe_mit_fenster()), *rest]
    return [sys.executable, str(haupt_datei), *rest]


def _netz_schuetzen() -> None:
    import netzzwang                     # vor jeder Verbindung: nur https, Klartext nur zu freigegebenen lokalen Ports
    netzzwang.installieren()
    try:                                 # eigene Namensauflösungen mitschreiben (Netz-Auskunft der Wache)
        from wache import netzspur
        netzspur.installieren()
    except Exception:
        pass


def frueh(modulname: str, haupt_datei: Path) -> None:
    """Aus main.py: ist dies ein reiner Fenster-Prozess, startet er hier und endet (SystemExit)."""
    if modulname != "__main__":
        return
    art = ziel(sys.argv, bool(getattr(sys, "frozen", False)), sys.stdout is None)
    if art is not None:
        raise SystemExit(starten(art, haupt_datei))


def starten(art: str, haupt_datei: Path) -> int:
    _netz_schuetzen()
    try:                                 # .env wie sonst laden (z. B. TEXTUAL_FPS); Schlüssel bleiben verschlüsselt
        from dotenv import load_dotenv
        import config
        load_dotenv(config._ENV)
    except Exception:
        pass
    if art == "gui":
        import gui_fenster
        return gui_fenster.starten()
    if art == "charaktere":
        import charakter_fenster
        return charakter_fenster.starten()
    import terminal_fenster
    frozen = bool(getattr(sys, "frozen", False))
    terminal_fenster.schutz_starten()
    return terminal_fenster.starten(kind_befehl(sys.argv, frozen, haupt_datei), ordner=os.getcwd())
