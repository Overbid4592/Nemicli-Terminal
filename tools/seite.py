"""
seite.py - Werkzeug seite_ansehen: eine lokale Webseite unsichtbar im Browser öffnen,
Bildschirmfoto für die KI und die Meldungen der JavaScript-Konsole (Fehler zuerst).

Nimmt einen installierten Chromium-Browser (Edge, Chrome, Brave, Chromium, Vivaldi) im Headless-Modus
mit eigenem, leerem Profil – das Profil des Nutzers bleibt unberührt. Nur lokale Dateien und
localhost/127.0.0.1, nichts aus dem Internet.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

ZEIT = 45
BREITE, HOEHE = 1280, 900
_LOKAL = {"localhost", "127.0.0.1", "::1", "[::1]"}
_KONSOLE = re.compile(r':CONSOLE[^\]]*\]\s*"(.*)", source: (\S+) \((\d+)\)')


def browser() -> Path | None:
    """Erster gefundener Chromium-Browser."""
    pf, pf86, lad = os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LOCALAPPDATA", "")
    kandidaten = [
        Path(pf86) / "Microsoft/Edge/Application/msedge.exe", Path(pf) / "Microsoft/Edge/Application/msedge.exe",
        Path(pf) / "Google/Chrome/Application/chrome.exe", Path(pf86) / "Google/Chrome/Application/chrome.exe",
        Path(lad) / "Google/Chrome/Application/chrome.exe",
        Path(pf) / "BraveSoftware/Brave-Browser/Application/brave.exe", Path(lad) / "Chromium/Application/chrome.exe",
        Path(lad) / "Vivaldi/Application/vivaldi.exe",
    ]
    for k in kandidaten:
        if k.is_file():
            return k
    try:
        import winreg
        for name in ("msedge.exe", "chrome.exe", "brave.exe"):
            for wurzel in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(wurzel, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}") as s:
                        p = Path(winreg.QueryValue(s, None))
                        if p.is_file():
                            return p
                except OSError:
                    continue
    except ImportError:
        pass
    return None


def adresse(ziel: str) -> tuple[str | None, str]:
    """(URL, Fehler): lokale Datei → file://, sonst nur http(s) auf localhost."""
    ziel = str(ziel or "").strip().strip('"')
    if not ziel:
        return None, "Feld 'ziel' fehlt: Pfad zur HTML-Datei oder http://localhost:PORT/…"
    if re.match(r"^https?://", ziel, re.I):
        host = (urlparse(ziel).hostname or "").lower()
        if host not in _LOKAL:
            return None, "Nur lokale Seiten: eine Datei oder http://localhost / 127.0.0.1 – nichts aus dem Internet."
        return ziel, ""
    p = Path(ziel).expanduser()
    if p.is_dir():
        p = p / "index.html"
    if not p.is_file():
        return None, f"Datei nicht gefunden: {p}"
    return p.resolve().as_uri(), ""


def ansehen(ziel: str, breite: int = BREITE, hoehe: int = HOEHE) -> tuple[Path | None, str]:
    """(Bildpfad, Text mit Konsolenmeldungen) – Bildpfad None bei Fehler."""
    url, fehler = adresse(ziel)
    if url is None:
        return None, fehler
    b = browser()
    if b is None:
        return None, "Kein Chromium-Browser gefunden (Edge, Chrome, Brave, Chromium oder Vivaldi)."
    arbeit = Path(tempfile.gettempdir()) / "NemiCLI-Seiten"
    arbeit.mkdir(parents=True, exist_ok=True)
    bild = arbeit / f"seite_{time.strftime('%H%M%S')}_{abs(hash(url)) % 10000}.png"
    breite = min(max(int(breite or BREITE), 320), 2560)
    hoehe = min(max(int(hoehe or HOEHE), 240), 2560)
    befehl = [str(b), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
              "--no-default-browser-check", "--disable-extensions", "--disable-sync",
              f"--user-data-dir={arbeit / 'profil'}", f"--window-size={breite},{hoehe}",
              f"--screenshot={bild}", "--enable-logging=stderr", "--v=0", "--virtual-time-budget=3000", url]
    try:
        r = subprocess.run(befehl, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=ZEIT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ausgabe = (r.stderr or "") + (r.stdout or "")
    except subprocess.TimeoutExpired:
        return None, f"Der Browser hat nach {ZEIT} s nicht geantwortet."
    except OSError as e:
        return None, f"Browser ließ sich nicht starten: {e}"
    if not bild.is_file():
        return None, "Kein Bildschirmfoto entstanden." + (f"\n{ausgabe.strip()[-600:]}" if ausgabe.strip() else "")
    meldungen = [(m.group(1), Path(urlparse(m.group(2)).path).name or m.group(2), m.group(3))
                 for m in _KONSOLE.finditer(ausgabe)]
    fehler_zeilen = [f"  ❌ {t}  ({q}:{z})" for t, q, z in meldungen if re.search(r"error|uncaught|failed", t, re.I)]
    sonst = [f"  · {t}  ({q}:{z})" for t, q, z in meldungen if not re.search(r"error|uncaught|failed", t, re.I)]
    text = f"Seite {url} im Browser ({b.stem}, {breite}×{hoehe}) – das Bild bekommst du jetzt zu sehen."
    if fehler_zeilen:
        text += "\nJavaScript-Fehler:\n" + "\n".join(fehler_zeilen[:15])
    if sonst:
        text += "\nKonsole:\n" + "\n".join(sonst[:10])
    if not meldungen:
        text += "\nKonsole: keine Meldungen."
    return bild, text
