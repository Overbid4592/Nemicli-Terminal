"""
pruefen.py - eine NemiCLI-App für NemiView prüfen (Werkzeug app_pruefen).

Lädt die App unsichtbar (virtuelle Zeit), rechnet das Layout und meldet: Syntaxfehler mit Zeile, Fehler beim
Ausführen, Konsolenfehler, CSS-Eigenschaften, die NemiView nicht kennt, fehlende Dateien, Adressen ins Internet
(gibt es in NemiView nicht) und Elemente ohne Unterstützung.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import nemicss
from .laufzeit import AppLaufzeit
from .layout import Layout
from .nemijs import intern
from .nemijs_parser import SyntaxFehler, parsen

NICHT_DA = {"iframe", "video", "audio", "canvas", "object", "embed", "frame", "frameset", "dialog", "portal"}


def _ausserhalb(ziel: str) -> bool:
    """Freigabe (\\\\rechner\\x, auch %5C%5C), Laufwerk, Adresse mit Schema oder Pfad ab der Wurzel – roh und
    entschlüsselt. Wird nie im Dateisystem nachgesehen: der Blick auf eine Freigabe schickt die Windows-Anmeldung."""
    from urllib.parse import unquote
    for t in (ziel, unquote(ziel)):
        t = t.strip().replace("/", "\\")
        if t.startswith("\\") or re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):
            return True
    return False


def pruefen(ordner: Path) -> tuple[bool, str]:
    ordner = Path(ordner)
    funde: list[str] = []
    if not (ordner / "index.html").is_file():
        return False, "index.html fehlt."
    for js in sorted(ordner.rglob("*.js")):
        try:
            parsen(js.read_text(encoding="utf-8", errors="replace"))
        except SyntaxFehler as e:
            funde.append(f"❌ {js.name}: {e}")
        except Exception as e:
            funde.append(f"❌ {js.name}: {intern(e)}")
    roh = (ordner / "index.html").read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", roh, re.I):
        ziel = m.group(1)
        if re.match(r"^(https?:)?//", ziel, re.I):
            funde.append(f"⚠ Adresse ins Internet: {ziel} – gibt es in NemiView nicht (Datei in den App-Ordner legen)")
        elif ziel.startswith(("#", "data:", "javascript:")):
            continue
        elif _ausserhalb(ziel):
            funde.append(f"⚠ Verweis aus dem App-Ordner hinaus: {ziel} – NemiView lädt nur Dateien im App-Ordner")
        elif not (ordner / ziel.split("?")[0]).is_file():
            funde.append(f"❌ Datei fehlt: {ziel}")
    for css in [roh] + [p.read_text(encoding="utf-8", errors="replace") for p in sorted(ordner.rglob("*.css"))]:
        for m in re.finditer(r"([a-z-]+)\s*:", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
            name = m.group(1)
            if name not in nemicss.BEKANNT and not name.startswith(("--", "webkit", "moz")) and name not in (
                    "margin", "padding", "border", "background", "font", "flex", "gap", "inset", "place-items",
                    "border-radius", "border-width", "border-color", "border-style", "border-top", "border-bottom",
                    "border-left", "border-right", "grid-gap", "flex-flow", "place-content", "place-self",
                    "list-style", "http", "https", "data", "javascript", "src", "href", "charset", "content",
                    "name", "viewport", "width", "lang", "rel", "type", "id", "class", "style", "hover", "focus",
                    "active", "root", "before", "after", "nth-child", "not", "checked", "disabled"):
                funde.append(f"⚠ CSS-Eigenschaft „{name}“ kennt NemiView nicht – sie wirkt nicht")
    try:
        lz = AppLaufzeit(ordner, 900, 650, virtuelle_zeit=True)
        lz.laden()
        lz.zeitgeber_laufen(3000)
        Layout(lz.dok, lz.kaskade, 900, 650).rechnen()
        for f in lz.fehler:
            funde.append(f)
        for z in lz.js.konsole:
            if z.startswith("❌") and z not in funde:
                funde.append(z)
        for el in lz.dok.alle():
            if el.tag in NICHT_DA:
                funde.append(f"⚠ <{el.tag}> zeigt NemiView nicht an")
        funktionen = ", ".join(lz.funktionen) or "keine"
    except (OSError, RecursionError) as e:
        return False, f"❌ Die App lässt sich nicht laden: {e}"
    except Exception as e:                             # läuft im Sitzungsprozess: nie bis zum Aufrufer
        return False, f"❌ Die App lässt sich nicht laden: {intern(e)}"
    funde = list(dict.fromkeys(funde))[:40]
    fehler = [f for f in funde if f.startswith("❌")]
    kopf = (f"App {ordner.name}: {'✓ läuft in NemiView' if not fehler else f'{len(fehler)} Fehler'}"
            f" · Funktionen für die KI: {funktionen}")
    return not fehler, kopf + ("\n" + "\n".join(funde) if funde else "\nKeine Auffälligkeiten.")
