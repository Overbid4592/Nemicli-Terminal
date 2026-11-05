"""handyexport.py – Persönlichkeit für die NemiCLI-App aufs Handy (/persönlichkeiten export).

Eine Datei `<Name>.nemicli.json` im Ordner Export/ des Daten-Ordners:
    format    "nemicli-persoenlichkeit", version 1
    name      Anzeigename
    wesen     der Persönlichkeits-Text mit eingesetzten Platzhaltern (geht in der App als System-Prompt mit)
    bild_png  Profilbild (aktive) bzw. Kugel-Grundbild, höchstens BILD_MAX px, Base64 – fehlt ohne Bild
Die App liest sie über „📥 Vom PC“ ein (NemiAPPStudio, Speicher.einlesen).
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

FORMAT = "nemicli-persoenlichkeit"
BILD_MAX = 512


def ordner() -> Path:
    import paths
    return Path(paths.DATEN) / "Export"


def _bild(p) -> Path | None:
    try:
        import persoenlichkeiten as PS
        if p.key == PS.active().key:
            import profilbild
            return profilbild.pfad()
        from wache import steuerung
        da = steuerung.bilder(p.name)
        return da.get("") or da.get("froh") or next(iter(da.values()), None)
    except Exception:
        return None


def bild_png(pfad: Path | None) -> str:
    """Bild als verkleinertes PNG in Base64 – leer, wenn es keins gibt oder es nicht lesbar ist."""
    if pfad is None or not Path(pfad).is_file():
        return ""
    try:
        from PIL import Image
        with Image.open(pfad) as im:
            im = im.convert("RGBA")
            im.thumbnail((BILD_MAX, BILD_MAX))
            puffer = io.BytesIO()
            im.save(puffer, "PNG", optimize=True)
        return base64.b64encode(puffer.getvalue()).decode("ascii")
    except Exception:
        return ""


def daten(p) -> dict:
    import persoenlichkeiten as PS
    d = {"format": FORMAT, "version": 1, "name": p.name, "wesen": PS.render_text(p).strip()}
    if (b := bild_png(_bild(p))):
        d["bild_png"] = b
    return d


def dateiname(name: str) -> str:
    sauber = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_") or "persoenlichkeit"
    return f"{sauber}.nemicli.json"


def exportieren(p=None) -> Path:
    """Export-Datei schreiben und ihren Pfad zurückgeben (ohne `p`: die aktive Persönlichkeit)."""
    import persoenlichkeiten as PS
    p = p or PS.active()
    ziel = ordner() / dateiname(p.name)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    from atomar import text_schreiben
    text_schreiben(ziel, json.dumps(daten(p), ensure_ascii=False))
    return ziel
