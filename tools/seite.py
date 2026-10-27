"""
seite.py - Werkzeug seite_ansehen: eine lokale Webseite unsichtbar laden, Bildschirmfoto für die KI und die
Meldungen der JavaScript-Konsole (Fehler zuerst).

Lädt mit NemiView (eigener Motor, nemiview/bild.py) in einem eigenen Prozess – kein Browser, kein Netz,
jedes Mal ohne gespeicherten Zustand. Nur lokale Dateien.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

ZEIT = 45
BREITE, HOEHE = 1280, 900
_LOKAL = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _ohne_geheimnisse() -> dict:
    """Umgebung ohne Schlüssel und Tokens: der gestartete Prozess braucht sie nicht."""
    import config
    return config.umgebung_ohne_geheimnisse()


def _befehl(*args: str) -> list[str]:
    """NemiCLI mit dem Schalter --seitenbild (als exe: die Fenster-exe, sonst main.py)."""
    if getattr(sys, "frozen", False):
        import paths
        return [str(paths.exe_ohne_fenster()), "--seitenbild", *args]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), "--seitenbild", *args]


def adresse(ziel: str) -> tuple[str | None, str]:
    """(URL, Fehler): lokale Datei → file://; Adressen werden abgelehnt."""
    ziel = str(ziel or "").strip().strip('"')
    if not ziel:
        return None, "Feld 'ziel' fehlt: Pfad zur HTML-Datei."
    if re.match(r"^https?://", ziel, re.I):
        if (urlparse(ziel).hostname or "").lower() not in _LOKAL:
            return None, "Nur lokale Dateien – nichts aus dem Internet."
        return None, ("Server-Adressen (localhost) öffnet NemiView nicht – gib die HTML-Datei an, "
                      "z. B. index.html oder dist/index.html.")
    if ziel.startswith(("\\\\", "//")) or ziel.lower().startswith("file:"):
        return None, "Nur lokale Dateien mit Laufwerk (C:\\…) – keine Netzwerkpfade."
    p = Path(ziel).expanduser()
    if p.is_dir():
        p = p / "index.html"
    if not p.is_file():
        return None, f"Datei nicht gefunden: {p}"
    return p.resolve().as_uri(), ""


def _datei(url: str) -> Path:
    return Path(unquote(urlparse(url).path.lstrip("/")))


def _alte_bilder_weg(arbeit: Path, alter_s: float = 6 * 3600) -> None:
    """Bildschirmfotos älter als ein paar Stunden aufräumen – die KI hat sie längst gesehen."""
    grenze = time.time() - alter_s
    for p in arbeit.glob("seite_*.png"):
        try:
            if p.stat().st_mtime < grenze:
                p.unlink()
        except OSError:
            pass


def _lauf(ziel: str, breite: int, hoehe: int) -> tuple[Path | None, str, list, list]:
    """(Bildpfad, Kopfzeile oder Fehler, Fehlerzeilen, übrige Konsolenzeilen) – Bildpfad None bei Fehler."""
    url, fehler = adresse(ziel)
    if url is None:
        return None, fehler, [], []
    datei = _datei(url)
    arbeit = Path(tempfile.gettempdir()) / "NemiCLI-Seiten"
    arbeit.mkdir(parents=True, exist_ok=True)
    _alte_bilder_weg(arbeit)
    bild = arbeit / f"seite_{time.strftime('%H%M%S')}_{secrets.token_hex(4)}.png"
    ergebnis = bild.with_suffix(".json")
    breite = min(max(int(breite or BREITE), 320), 2560)
    hoehe = min(max(int(hoehe or HOEHE), 240), 2560)
    try:
        subprocess.run(_befehl(str(datei), str(breite), str(hoehe), str(bild)), capture_output=True, timeout=ZEIT,
                       env=_ohne_geheimnisse(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        erg = json.loads(ergebnis.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        return None, f"Die Seite hat nach {ZEIT} s nicht fertig geladen.", [], []
    except (OSError, ValueError) as e:
        return None, f"NemiView ließ sich nicht starten: {e}", [], []
    finally:
        ergebnis.unlink(missing_ok=True)
    fehler_zeilen = [f"  ❌ {z.removeprefix('❌ ')}" for z in erg.get("fehler") or []]
    if not erg.get("ok") or not bild.is_file():
        return None, "\n".join(["Kein Bildschirmfoto entstanden.", *fehler_zeilen]), [], []
    sonst = [f"  · {z}" for z in erg.get("konsole") or []] + [f"  ⚠ {z}" for z in erg.get("hinweise") or []]
    return bild, f"Seite {datei.name} in NemiView ({breite}×{hoehe})", fehler_zeilen, sonst


def _konsole(fehler_zeilen: list, sonst: list) -> str:
    text = ""
    if fehler_zeilen:
        text += "\nJavaScript-Fehler:\n" + "\n".join(fehler_zeilen[:15])
    if sonst:
        text += "\nKonsole:\n" + "\n".join(sonst[:10])
    if not fehler_zeilen and not sonst:
        text += "\nKonsole: keine Meldungen."
    return text


def ansehen(ziel: str, breite: int = BREITE, hoehe: int = HOEHE) -> tuple[Path | None, str]:
    """(Bildpfad, Text mit Konsolenmeldungen) – Bildpfad None bei Fehler."""
    bild, kopf, fehler_zeilen, sonst = _lauf(ziel, breite, hoehe)
    if bild is None:
        return None, kopf
    return bild, f"{kopf} – das Bild bekommst du jetzt zu sehen." + _konsole(fehler_zeilen, sonst)


_VERWEIS = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"'#?]*)""", re.I)


def fehlende_dateien(datei: Path) -> list[str]:
    """Lokale Verweise der HTML-Datei (src/href), deren Datei es nicht gibt."""
    try:
        html = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    fehlt = []
    for ziel in dict.fromkeys(m.group(1).strip() for m in _VERWEIS.finditer(html)):
        if not ziel or re.match(r"^[a-z][a-z0-9+.-]*:|^//", ziel, re.I):    # http:, data:, mailto:, //host …
            continue
        p = (datei.parent / unquote(ziel)).resolve()
        if not (p / "index.html" if p.is_dir() else p).is_file():
            fehlt.append(ziel)
    return fehlt


def pruefen(ziel: str) -> tuple[bool, str]:
    """Für `todo pruefen`: (bestanden, Text). Bestanden, wenn die Seite lädt und die Konsole beim Laden
    (3 s Seitenzeit) keinen Fehler meldet – auch nicht für fehlende Dateien. Klicks und Eingaben prüft das nicht."""
    bild, kopf, fehler_zeilen, sonst = _lauf(ziel, BREITE, HOEHE)
    if bild is None:
        return False, kopf
    try:
        bild.unlink()
    except OSError:
        pass
    fehlt = fehlende_dateien(_datei(adresse(ziel)[0]))
    teile = [f"{len(fehler_zeilen)} Fehler in der Konsole" if fehler_zeilen else "keine Fehler in der Konsole"]
    if fehlt:
        teile.append(f"{len(fehlt)} Verweis(e) ins Leere")
    text = f"{kopf}: {', '.join(teile)}." + _konsole(fehler_zeilen, sonst)
    if fehlt:
        text += "\nDatei fehlt:\n" + "\n".join(f"  ❌ {z}" for z in fehlt[:15])
    return not fehler_zeilen and not fehlt, text
