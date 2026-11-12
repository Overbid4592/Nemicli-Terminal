"""terminal_text.py – Text im Zeichenraster des Terminal-Fensters: Zeilen lesen, Links finden, suchen.

Reine Funktionen ohne Qt. Eine Zeile ist eine Liste von Zellinhalten: ein Zeichen oder eine
Graphem-Gruppe je Zelle, die zweite Zelle eines breiten Zeichens ist leer.
"""

from __future__ import annotations

import os
import re

LINK_MAX = 2000                       # längste Adresse, die als Link gilt

# Dateien, die ein Klick mit ihrem Programm öffnet; alles andere wird nur im Ordner gezeigt.
# Ohne Formate, die beim Öffnen selbst etwas ausführen können (SVG mit Skript, Tabellen mit Formeln).
OEFFNEN_ENDUNGEN = frozenset((
    ".txt", ".md", ".log", ".json", ".ini", ".toml", ".yml", ".yaml", ".pdf",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".mp4", ".webm", ".mkv"))

_RAHMEN = "│┃▎▏║|"
# Nur verschlüsselte Adressen sind Links; http:// bleibt Text.
_WEB = re.compile(r"(?<![A-Za-z0-9])https://[^\s<>\"'`" + _RAHMEN + r"]+", re.IGNORECASE)
_PFAD = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^<>\"|?*" + _RAHMEN + r"]*")
_ENDE_WEG = ".,;:!?)]}'\"…»›"
_ZEILENNUMMER = re.compile(r":\d+(:\d+)?$")


def zeilentext(zellen: list, platzhalter=None) -> tuple[str, list[int]]:
    """Zeile als Text und je Zeichen die Zelle, in der es steht. Bildstellen zählen als Leerzeichen."""
    text: list[str] = []
    zelle: list[int] = []
    for x, inhalt in enumerate(zellen):
        if not inhalt:
            continue
        if platzhalter is not None and platzhalter(inhalt):
            inhalt = " "
        for zeichen in inhalt:
            text.append(zeichen)
            zelle.append(x)
    return "".join(text), zelle


def _web_ende(adresse: str) -> str:
    """Satzzeichen am Ende gehören nicht zur Adresse; eine Klammer nur, wenn sie in der Adresse aufgeht."""
    while adresse and adresse[-1] in _ENDE_WEG:
        if adresse[-1] == ")" and adresse.count("(") >= adresse.count(")"):
            break
        adresse = adresse[:-1]
    return adresse


def _pfad_ende(kandidat: str, gibt_es) -> str:
    """Längster Anfang des Kandidaten, den es auf der Platte gibt (Pfade dürfen Leerzeichen haben)."""
    kandidat = kandidat.rstrip()
    while kandidat:
        kurz = kandidat.rstrip(_ENDE_WEG + " ")
        for versuch in (kurz, _ZEILENNUMMER.sub("", kurz)):
            if len(versuch) > 3 and gibt_es(versuch):
                return versuch
        if " " not in kandidat:
            return ""
        kandidat = kandidat.rsplit(" ", 1)[0].rstrip()
    return ""


def finde_links(zellen: list, *, gibt_es=os.path.exists, platzhalter=None) -> list[tuple[int, int, str, str]]:
    """Links einer Zeile als (erste Zelle, letzte Zelle, Art „web“ | „pfad“, Ziel)."""
    text, zelle = zeilentext(zellen, platzhalter)
    links = []
    for m in _WEB.finditer(text):
        adresse = _web_ende(m.group(0))
        if len(adresse) > len("https://") and len(adresse) <= LINK_MAX:
            links.append((zelle[m.start()], zelle[m.start() + len(adresse) - 1], "web", adresse))
    for m in _PFAD.finditer(text):
        if any(a <= zelle[m.start()] <= b for a, b, _art, _ziel in links):
            continue                                  # steckt in einer Web-Adresse
        try:
            pfad = _pfad_ende(m.group(0), gibt_es)
        except OSError:
            pfad = ""
        if pfad:
            links.append((zelle[m.start()], zelle[m.start() + len(pfad) - 1], "pfad", pfad))
    return sorted(links)


def link_an(zellen: list, spalte: int, **kw) -> tuple[int, int, str, str] | None:
    """Der Link unter einer Zelle – oder None."""
    for link in finde_links(zellen, **kw):
        if link[0] <= spalte <= link[1]:
            return link
    return None


def oeffnen_art(pfad: str) -> str:
    """Was ein Klick auf einen Pfad tut: „ordner“ öffnen, „datei“ öffnen oder nur im Ordner „zeigen“.
    Programme, Skripte und Verknüpfungen startet ein Klick nie."""
    if os.path.isdir(pfad):
        return "ordner"
    return "datei" if os.path.splitext(pfad)[1].lower() in OEFFNEN_ENDUNGEN else "zeigen"


def suche(zeilen, begriff: str, platzhalter=None) -> list[tuple[int, int, int]]:
    """Treffer ohne Groß/Klein als (Zeilennummer, erste Zelle, letzte Zelle).
    `zeilen`: Paare (Zeilennummer, Zellen)."""
    begriff = begriff.strip()
    if not begriff:
        return []
    muster = re.compile(re.escape(begriff), re.IGNORECASE)
    treffer = []
    for nummer, zellen in zeilen:
        text, zelle = zeilentext(zellen, platzhalter)
        for m in muster.finditer(text):
            if m.end() > m.start():
                treffer.append((nummer, zelle[m.start()], zelle[m.end() - 1]))
    return treffer


def naechster(treffer: list, aktuell: int, schritt: int) -> int:
    """Nummer des nächsten Treffers (läuft am Ende um); -1 ohne Treffer."""
    if not treffer:
        return -1
    return (aktuell + schritt) % len(treffer) if 0 <= aktuell < len(treffer) else (0 if schritt > 0 else len(treffer) - 1)


def im_ordner_zeigen(pfad: str) -> bool:
    """Explorer öffnen und die Datei markieren – über die Shell, ohne ein Programm zu starten."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        shell32 = ctypes.WinDLL("shell32")
        shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p
        shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                                       ctypes.c_ulong]
        shell32.ILFree.argtypes = [ctypes.c_void_p]
        ctypes.WinDLL("ole32").CoInitialize(None)
        kennung = shell32.ILCreateFromPathW(os.path.normpath(pfad))
        if not kennung:
            return False
        try:
            return shell32.SHOpenFolderAndSelectItems(kennung, 0, None, 0) == 0
        finally:
            shell32.ILFree(kennung)
    except Exception:
        return False


def oeffnen(art: str, ziel: str) -> bool:
    """Link öffnen: Web-Adresse im Browser, Ordner im Explorer, Datei mit ihrem Programm oder im Ordner gezeigt."""
    try:
        if art == "web":
            if not re.fullmatch(r"https://[^\s\x00-\x1f]+", ziel, re.IGNORECASE) or len(ziel) > LINK_MAX:
                return False
            import webbrowser
            return bool(webbrowser.open(ziel))
        if art != "pfad" or not os.path.exists(ziel):
            return False
        wie = oeffnen_art(ziel)
        if wie == "zeigen":
            return im_ordner_zeigen(ziel) or _starten(os.path.dirname(ziel))
        return _starten(ziel)
    except Exception:
        return False


def _starten(pfad: str) -> bool:
    if os.name != "nt" or not pfad:
        return False
    os.startfile(os.path.normpath(pfad))            # nur Ordner und Dateien aus OEFFNEN_ENDUNGEN
    return True
