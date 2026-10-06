"""
themes_eigen.py - Eigene Farbschemata (Werkzeug theme_erstellen), zusätzlich zu den eingebauten.

Datei: themes_eigen.json im Programm-Ordner (für die Datei-Werkzeuge der KI gesperrt).
Aus zwei Farben (brand, accent) werden die gedämpften Töne und der Logo-Verlauf berechnet.
Beide Farben müssen sich vom dunklen Hintergrund abheben (Kontrast ≥ 4,5 wie WCAG AA).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HINTERGRUND = "#0d1117"
MIN_KONTRAST = 4.5
HOECHSTENS = 20
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,19}$")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def datei() -> Path:
    try:
        from paths import INSTALL
        return Path(INSTALL) / "themes_eigen.json"
    except Exception:
        return Path(__file__).resolve().parent.parent / "themes_eigen.json"


def _hex(wert) -> str:
    m = _HEX.match(str(wert or "").strip())
    if not m:
        raise ValueError(f"Farbe {wert!r} ist kein Hex-Wert wie #8b7cff.")
    return "#" + m.group(1).lower()


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _helligkeit(h: str) -> float:
    def kanal(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (kanal(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def kontrast(a: str, b: str = HINTERGRUND) -> float:
    la, lb = sorted((_helligkeit(a), _helligkeit(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _dunkler(h: str, faktor: float = 0.62) -> str:
    return "#" + "".join(f"{round(c * faktor):02x}" for c in _rgb(h))


def _verlauf(stufen: list[str], n: int = 6) -> list[str]:
    """n Farben gleichmäßig entlang der Stufen (mindestens zwei)."""
    raus = []
    for i in range(n):
        t = i / (n - 1) * (len(stufen) - 1)
        k = min(int(t), len(stufen) - 2)
        a, b, f = _rgb(stufen[k]), _rgb(stufen[k + 1]), t - k
        raus.append("#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b)))
    return raus


def bauen(name, label, brand, accent, verlauf=None, eingebaut=()) -> tuple[str, dict]:
    """Prüft die Angaben und baut die Palette. ValueError mit lesbarem Grund bei Fehlern."""
    name = str(name or "").strip().lower()
    if not _NAME.match(name):
        raise ValueError("Name: 2–20 Zeichen, klein, Buchstaben/Ziffern/-/_, mit einem Buchstaben vorn.")
    if name in eingebaut:
        raise ValueError(f"'{name}' ist ein eingebautes Theme und bleibt, wie es ist – nimm einen anderen Namen.")
    brand, accent = _hex(brand), _hex(accent)
    for rolle, farbe in (("brand", brand), ("accent", accent)):
        if kontrast(farbe) < MIN_KONTRAST:
            raise ValueError(f"{rolle} {farbe} ist auf dem dunklen Hintergrund schlecht lesbar "
                             f"(Kontrast {kontrast(farbe):.1f}, nötig {MIN_KONTRAST}) – heller wählen.")
    if verlauf:
        stufen = [_hex(f) for f in (verlauf if isinstance(verlauf, list) else str(verlauf).replace(",", " ").split())]
        if not 2 <= len(stufen) <= 6:
            raise ValueError("verlauf: 2 bis 6 Farben.")
    else:
        stufen = [accent, brand]
    verlauf = _verlauf(stufen)
    label = " ".join(str(label or name).split())[:40]
    return name, {"label": label, "brand": brand, "brand_dim": _dunkler(brand),
                  "accent": accent, "accent_dim": _dunkler(accent), "gradient": verlauf, "eigen": True}


def laden() -> dict[str, dict]:
    """Gespeicherte eigene Themes; kaputte Einträge werden übersprungen."""
    try:
        roh = json.loads(datei().read_text(encoding="utf-8"))
    except Exception:
        return {}
    themes = {}
    for name, p in (roh.items() if isinstance(roh, dict) else []):
        try:
            n, palette = bauen(name, p.get("label"), p.get("brand"), p.get("accent"), p.get("gradient"))
            themes[n] = palette
        except Exception:
            continue
    return themes


def vorschau(name, label, brand, accent, verlauf=None, eingebaut=()) -> str:
    try:
        n, p = bauen(name, label, brand, accent, verlauf, eingebaut)
    except ValueError as e:
        return f"Theme wird NICHT angelegt: {e}"
    alt = "überschreibt das eigene Theme" if n in laden() else "neues Theme"
    return (f"Theme '{n}' ({p['label']}) – {alt}\n"
            f"  brand {p['brand']} (gedämpft {p['brand_dim']})  ·  accent {p['accent']} (gedämpft {p['accent_dim']})\n"
            f"  Verlauf: {' '.join(p['gradient'])}\n"
            f"  Danach wählbar mit /theme {n}.")


def anlegen(name, label, brand, accent, verlauf=None, eingebaut=()) -> tuple[str, dict]:
    """Speichert das Theme (alter Stand in den Papierkorb). Gibt (Name, Palette) zurück."""
    n, palette = bauen(name, label, brand, accent, verlauf, eingebaut)
    alle = laden()
    if n not in alle and len(alle) >= HOECHSTENS:
        raise ValueError(f"Schon {HOECHSTENS} eigene Themes – erst eins überschreiben.")
    alle[n] = palette
    p = datei()
    if p.is_file():
        import snapshot
        snapshot.sichern(p, "theme_erstellen")
    gespeichert = {k: {f: v[f] for f in ("label", "brand", "accent", "gradient")} for k, v in alle.items()}
    p.write_text(json.dumps(gespeichert, indent=2, ensure_ascii=False), encoding="utf-8")
    return n, palette
