"""
vision.py - Bilder erkennen und für Vision-Modelle aufbereiten.

Wenn der Nutzer einen Bildpfad in den Chat schreibt (z.B.
C:\\...\\screenshot.png), findet `find_images` ihn und `to_data_uri` macht
daraus eine base64-Data-URI, die OpenAI-kompatible Vision-Modelle
direkt im Chat-Inhalt verstehen (Feld image_url).
"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

# Endungen, die als Bild GELTEN. Die Modelle selbst verstehen nur PNG/JPEG/
# WebP/GIF – alles andere (TIFF, ICO, HEIC, PSD …) wandelt to_data_uri vor dem
# Senden per PIL um. So lässt sich jedes Bild in den Chat ziehen, das PIL öffnet.
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".bmp",
             ".tif", ".tiff", ".ico", ".avif", ".heic", ".heif", ".psd", ".tga", ".dds"}
# Was ohne Umwandlung ans Modell darf.
_DIREKT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Pfade, die auf eine Bildendung enden – Windows (C:\..) und Unix (/..) sowie
# relative Pfade. In Anführungszeichen oder roh. Die Endungen kommen aus
# IMAGE_EXT, damit die Liste nur an EINER Stelle steht.
_ENDUNGEN = "|".join(re.escape(e[1:]) for e in sorted(IMAGE_EXT, key=len, reverse=True))
_PATH_RX = re.compile(
    r'"([^"]+?\.(?:' + _ENDUNGEN + r'))"'          # "in Anführungszeichen"
    r"|((?:[A-Za-z]:\\|/|\.{0,2}/|~/)?[^\s\"]+?\.(?:" + _ENDUNGEN + r"))",
    re.IGNORECASE,
)


def find_images(text: str) -> list[Path]:
    """Findet existierende Bilddateien, die im Text erwähnt werden."""
    found: list[Path] = []
    seen: set[str] = set()

    # 1) Ganzer Text als Pfad (häufigster Fall: nur der Pfad getippt)
    candidates = [text.strip().strip('"').strip("'")]
    # 2) Einzelne Treffer per Regex
    for m in _PATH_RX.finditer(text):
        candidates.append(m.group(1) or m.group(2))

    for c in candidates:
        if not c:
            continue
        p = Path(c.strip().strip('"').strip("'"))
        key = str(p).lower()
        if key in seen:
            continue
        if p.suffix.lower() in IMAGE_EXT and p.is_file():
            seen.add(key)
            found.append(p)
    return found


def to_data_uri(path: Path) -> str:
    """Liest ein Bild und macht eine base64-Data-URI daraus.

    PNG/JPEG/WebP/GIF gehen roh. Alles andere (TIFF, ICO, HEIC, PSD …) verstehen
    die Modelle nicht – das wird per PIL nach PNG gewandelt. Klappt das nicht,
    geht es roh raus (besser ein Versuch als gar kein Bild)."""
    if path.suffix.lower() not in _DIREKT:
        try:
            from PIL import Image
            import io
            img = Image.open(path)
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.mode else "RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            pass
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def user_content(text: str, images: list[str] | None):
    """Baut den Nachrichten-Inhalt fürs OpenAI-Format: nur Text (str) ODER
    Text + Bilder (Liste mit image_url-Einträgen, base64-Data-URIs)."""
    if not images:
        return text
    parts = [{"type": "text", "text": text}]
    for uri in images:
        parts.append({"type": "image_url", "image_url": {"url": uri}})
    return parts


def to_data_uri_small(path: Path, max_side: int = 1600) -> str:
    """Wie to_data_uri, aber große Bilder (Screenshots!) vorher verkleinern und als
    JPEG schicken – spart Tokens und Zeit. Bei Problemen: unverändert."""
    try:
        from PIL import Image
        import io
        img = Image.open(path)
        if max(img.size) <= max_side and path.suffix.lower() in (".jpg", ".jpeg"):
            return to_data_uri(path)
        img = img.convert("RGB")
        if max(img.size) > max_side:
            f = max_side / max(img.size)
            img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return to_data_uri(path)


def strip_paths(text: str, images: list[Path]) -> str:
    """Entfernt die reinen Bildpfade aus dem Text (für eine saubere Frage).
    Bleibt sonst nichts übrig, gibt einen freundlichen Standardtext zurück."""
    out = text
    for p in images:
        out = out.replace(str(p), "").replace('"', "")
    out = out.strip()
    return out or "Was ist auf diesem Bild zu sehen?"
