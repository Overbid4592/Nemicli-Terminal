"""
pdfgen.py - Ein PDF-Generator in REINEM Python. Keine Fremd-Bibliothek.

Warum das geht: Eine PDF-Datei ist im Kern ein Textformat – eine Folge von
„Objekten", eine Querverweis-Tabelle (xref) und ein Trailer. Text, Linien und
Bilder werden über einfache Operatoren in „Content-Streams" beschrieben.

Dieses Modul kann:
  • Markdown -> PDF (Überschriften, Absätze, **fett**, *kursiv*, `code`,
    Listen, Code-Blöcke, Tabellen, Trennlinien, Seitenumbruch, Seitenzahlen)
  • Bilder einbetten: JPEG (direkt) und PNG (selbst dekodiert via stdlib `zlib`)
  • Umlaute/€ über die Standard-Schrift Helvetica (WinAnsi/cp1252) – kein
    Font muss eingebettet werden (die 14 Standard-Fonts kennt jeder PDF-Reader).

Öffentliche Funktion:  markdown_to_pdf(markdown, ausgabe_pfad, titel="") -> Pfad
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

# --- Seitenmaße (A4, in PDF-Punkten = 1/72 Zoll) ---------------------------
PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 56.0
USABLE = PAGE_W - 2 * MARGIN
TOP = MARGIN                 # y von oben gemessen
BOTTOM_LIMIT = PAGE_H - MARGIN

# Schrift-Ressourcen im PDF
F_REG, F_BOLD, F_ITAL, F_MONO = "F1", "F2", "F3", "F4"

# --- Farbpalette (dezent & modern) -----------------------------------------
ACCENT       = (0.16, 0.40, 0.69)    # ruhiges Blau: Überschriften, Akzente
ACCENT_DARK  = (0.11, 0.27, 0.47)    # dunkleres Blau (H3)
ACCENT_LIGHT = (0.91, 0.94, 0.98)    # zarter Akzent-Hintergrund (Zebra)
INK          = (0.13, 0.15, 0.18)    # weiches Fast-Schwarz für Fließtext
MUTED        = (0.42, 0.45, 0.50)    # gedämpft: Zitate, Fußzeile
RULE         = (0.80, 0.83, 0.87)    # feine Linien
CODE_BG      = (0.96, 0.97, 0.985)   # Code-Hintergrund
CODE_INK     = (0.16, 0.18, 0.27)    # Code-Text
INLINE_BG    = (0.93, 0.94, 0.96)    # `inline code` Hintergrund

# Helvetica-Zeichenbreiten (AFM, 1/1000 em) für sauberen Zeilenumbruch.
_HELV_ASCII = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278,
    278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584,
    584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
    278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222,
    500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500,
    500, 334, 260, 334, 584,
]
_W = {i + 32: w for i, w in enumerate(_HELV_ASCII)}     # Code -> Breite


def _char_w(code: int) -> int:
    return _W.get(code, 556)


def _text_width(s: str, size: float, mono: bool) -> float:
    if mono:
        return len(s) * 600 * size / 1000.0
    data = s.encode("cp1252", "replace")
    return sum(_char_w(b) for b in data) * size / 1000.0


def _esc(s: str) -> bytes:
    """Text für eine PDF-Zeichenkette aufbereiten (cp1252 + Klammern escapen)."""
    b = s.encode("cp1252", "replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


# ===========================================================================
#  Bild-Dekodierung (JPEG direkt, PNG selbst entpackt)
# ===========================================================================

def _jpeg(data: bytes) -> dict | None:
    """JPEG kann ein PDF direkt einbetten (Filter DCTDecode). Wir lesen nur die
    Maße/Kanäle aus dem SOF-Marker."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i < n - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        # SOF-Marker (Bildkopf), aber nicht DHT/DAC/etc.
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            comps = data[i + 9]
            cs = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comps, "/DeviceRGB")
            return {"w": w, "h": h, "cs": cs, "bpc": 8,
                    "filter": "/DCTDecode", "data": data}
        i += 2 + seg_len
    return None


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _png(data: bytes) -> dict | None:
    """PNG selbst dekodieren -> RGB-Bytes, dann für PDF wieder mit zlib packen."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    w = h = bd = ct = 0
    interlace = 0
    idat = bytearray()
    plte = b""
    n = len(data)
    while pos + 8 <= n:
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln                       # 4 len + 4 typ + ln + 4 crc
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", chunk[:10])
            interlace = chunk[12]
        elif typ == b"PLTE":
            plte = chunk
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    if not w or not h or interlace:          # interlaced PNG: nicht unterstützt
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except Exception:
        return None

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ct)
    if channels is None or bd not in (1, 2, 4, 8, 16):
        return None
    stride = (w * channels * bd + 7) // 8
    bpp = max(1, channels * bd // 8)

    # 1) Entfiltern (PNG-Zeilenfilter rückgängig machen)
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        if i >= len(raw):
            break
        f = raw[i]; i += 1
        line = bytearray(raw[i:i + stride]); i += stride
        if len(line) < stride:
            line += bytes(stride - len(line))
        if f == 1:                                   # Sub
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif f == 2:                                 # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:                                 # Average
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:                                 # Paeth
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + _paeth(a, prev[x], c)) & 0xFF
        out += line
        prev = line

    # 2) In 8-bit-RGB wandeln (Alpha auf Weiß rechnen)
    rgb = bytearray()

    def _samples(row_bytes: bytes):
        """Liefert die Sample-Werte einer Zeile als 0..255 (skaliert je bd)."""
        if bd == 8:
            return row_bytes
        if bd == 16:
            return row_bytes[0::2]                    # high byte
        # bd < 8: Bits auspacken
        vals = []
        maxv = (1 << bd) - 1
        for byte in row_bytes:
            for shift in range(8 - bd, -1, -bd):
                vals.append(((byte >> shift) & maxv) * 255 // maxv)
        return vals

    for row in range(h):
        line = out[row * stride:(row + 1) * stride]
        s = _samples(bytes(line))
        si = 0
        for _ in range(w):
            if ct == 2:                               # RGB
                rgb += bytes((s[si], s[si + 1], s[si + 2])); si += 3
            elif ct == 6:                             # RGBA -> auf Weiß
                r, g, b, a = s[si], s[si + 1], s[si + 2], s[si + 3]; si += 4
                rgb += bytes((_blend(r, a), _blend(g, a), _blend(b, a)))
            elif ct == 0:                             # Grau
                v = s[si]; si += 1
                rgb += bytes((v, v, v))
            elif ct == 4:                             # Grau+Alpha
                v, a = s[si], s[si + 1]; si += 2
                vb = _blend(v, a); rgb += bytes((vb, vb, vb))
            elif ct == 3:                             # Palette
                idx = s[si]; si += 1
                off = idx * 3
                if off + 3 <= len(plte):
                    rgb += plte[off:off + 3]
                else:
                    rgb += b"\x00\x00\x00"
    return {"w": w, "h": h, "cs": "/DeviceRGB", "bpc": 8,
            "filter": "/FlateDecode", "data": zlib.compress(bytes(rgb), 6)}


def _blend(v: int, a: int) -> int:
    return (v * a + 255 * (255 - a)) // 255


def _load_image(path: Path) -> dict | None:
    try:
        data = path.read_bytes()
    except Exception:
        return None
    if data[:2] == b"\xff\xd8":
        return _jpeg(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    return None


# ===========================================================================
#  Das Dokument (Layout in Content-Streams)
# ===========================================================================

class Document:
    def __init__(self, title: str = ""):
        self.title = title
        self.pages: list[bytearray] = []
        self.images: list[dict] = []          # {name, w, h, cs, bpc, filter, data}
        self.cur = bytearray()
        self.y = TOP
        self._page_started = False
        self._new_page()

    # --- Seiten ---
    def _new_page(self):
        self.cur = bytearray()
        self.pages.append(self.cur)
        self.y = TOP

    def _need(self, height: float):
        if self.y + height > BOTTOM_LIMIT:
            self._new_page()

    # --- Roh-Operatoren ---
    def _op(self, s: str):
        self.cur.extend(s.encode("latin-1"))

    def _show(self, x: float, baseline_top: float, text: str, size: float,
              font: str, rgb=(0, 0, 0)):
        if not text:
            return
        y = PAGE_H - baseline_top
        r, g, b = rgb
        self.cur.extend(
            f"BT /{font} {size:.1f} Tf {r:.2f} {g:.2f} {b:.2f} rg "
            f"{x:.2f} {y:.2f} Td (".encode("latin-1"))
        self.cur.extend(_esc(text))
        self.cur.extend(b") Tj ET\n")

    def _rect(self, x, y_top, w, h, rgb):
        y = PAGE_H - y_top - h
        r, g, b = rgb
        self._op(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f\n")

    def _hline(self, x1, x2, y_top, width=0.6, rgb=(0.6, 0.6, 0.6)):
        y = PAGE_H - y_top
        r, g, b = rgb
        self._op(f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w "
                 f"{x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S\n")

    # --- Bausteine ---
    def space(self, h: float):
        self.y += h

    def heading(self, text: str, level: int):
        size = {1: 22, 2: 16.5, 3: 13.5}.get(level, 12)
        color = {1: ACCENT, 2: ACCENT, 3: ACCENT_DARK}.get(level, INK)
        self.space(12 if self.y > TOP + 1 else 0)
        self._need(size * 1.3)
        if level == 1:
            # kleiner Akzent-Balken links vor der Hauptüberschrift
            self._rect(MARGIN, self.y + 1, 4, size, ACCENT)
            self._para(_inline(text, force_bold=True), size,
                       leading=size * 1.3, indent=MARGIN + 12, color=color)
            self.y += 4
            self._hline(MARGIN, PAGE_W - MARGIN, self.y, 1.0, ACCENT)
            self.y += 8
        else:
            self._para(_inline(text, force_bold=True), size,
                       leading=size * 1.3, color=color)
            self.y += 4

    def paragraph(self, text: str):
        self._para(_inline(text), 11, leading=15.5, color=INK)
        self.y += 5

    def bullet(self, text: str, ordered: str | None = None):
        marker = ordered if ordered else "•"
        self._show(MARGIN + 6, self.y + 11, marker, 11,
                   F_BOLD if ordered else F_REG, ACCENT)
        self._para(_inline(text), 11, leading=15.5, indent=MARGIN + 22, color=INK)
        self.y += 3

    def blockquote(self, text: str):
        self.space(4)
        self._need(15.5)
        y0 = self.y
        self._para(_inline(text), 11, leading=15.5, indent=MARGIN + 18, color=MUTED)
        # Akzent-Balken links über die Höhe des Zitats
        if self.y >= y0:
            self._rect(MARGIN + 2, y0, 3, self.y - y0, ACCENT)
        else:                                   # Zitat lief auf neue Seite um
            self._rect(MARGIN + 2, TOP, 3, self.y - TOP, ACCENT)
        self.space(6)

    def rule(self):
        self.space(6)
        self._need(8)
        self._hline(MARGIN, PAGE_W - MARGIN, self.y, 0.6, RULE)
        self.space(8)

    def code_block(self, lines: list[str]):
        size = 9.3
        lead = size * 1.45
        pad_x = 9
        pad_y = 7
        # harter Umbruch auf Monospace-Breite (inkl. Platz für Akzent-Streifen)
        max_chars = int((USABLE - 2 * pad_x - 4) / (600 * size / 1000.0))
        wrapped: list[str] = []
        for ln in lines:
            ln = ln.replace("\t", "    ")
            if not ln:
                wrapped.append("")
            while len(ln) > max_chars:
                wrapped.append(ln[:max_chars]); ln = ln[max_chars:]
            wrapped.append(ln)
        self.space(6)
        i, n = 0, len(wrapped)
        # in Segmente je Seite aufteilen -> durchgehender Hintergrund mit Padding
        while i < n:
            if self.y + pad_y + lead > BOTTOM_LIMIT:
                self._new_page()
            seg_top = self.y
            ty = seg_top + pad_y
            seg: list[str] = []
            while i < n and ty + lead <= BOTTOM_LIMIT - pad_y:
                seg.append(wrapped[i]); ty += lead; i += 1
            seg_h = 2 * pad_y + len(seg) * lead
            self._rect(MARGIN, seg_top, USABLE, seg_h, CODE_BG)   # Hintergrund
            self._rect(MARGIN, seg_top, 3, seg_h, ACCENT)         # Akzent-Streifen
            ty = seg_top + pad_y
            for ln in seg:
                self._show(MARGIN + pad_x + 3, ty + size, ln, size, F_MONO, CODE_INK)
                ty += lead
            self.y = seg_top + seg_h
            if i < n:
                self._new_page()
        self.space(8)

    def table(self, rows: list[list[str]]):
        if not rows:
            return
        cols = max(len(r) for r in rows)
        cw = USABLE / cols
        size = 10
        lead = 14
        pad = 6
        self.space(4)
        for ri, row in enumerate(rows):
            # Zeilenhöhe = höchste Zelle (mit Umbruch)
            cell_lines = []
            for ci in range(cols):
                txt = row[ci] if ci < len(row) else ""
                cell_lines.append(_wrap_plain(txt, cw - 2 * pad, size))
            rh = max(1, max(len(c) for c in cell_lines)) * lead + 2 * pad
            self._need(rh)
            # Hintergrund: Kopf = Akzent, Datenzeilen = Zebra
            if ri == 0:
                self._rect(MARGIN, self.y, USABLE, rh, ACCENT)
            elif ri % 2 == 0:
                self._rect(MARGIN, self.y, USABLE, rh, ACCENT_LIGHT)
            # Zelltext (kein Gitter – nur feine Linie unter jeder Zeile)
            for ci in range(cols):
                x = MARGIN + ci * cw
                font = F_BOLD if ri == 0 else F_REG
                color = (1, 1, 1) if ri == 0 else INK
                ty = self.y + pad
                for line in cell_lines[ci]:
                    self._show(x + pad, ty + size, line, size, font, color)
                    ty += lead
            if ri > 0:
                self._hline(MARGIN, PAGE_W - MARGIN, self.y + rh, 0.4, RULE)
            self.y += rh
        self.space(8)

    def image(self, path: Path, alt: str = ""):
        info = _load_image(path)
        if info is None:
            self.paragraph(f"_[Bild nicht ladbar: {path.name}]_"
                           if path else "_[Bild fehlt]_")
            return
        name = f"Im{len(self.images) + 1}"
        info["name"] = name
        self.images.append(info)
        # auf nutzbare Breite skalieren (nie hochskalieren)
        scale = min(1.0, USABLE / info["w"])
        w = info["w"] * scale
        h = info["h"] * scale
        # passt das Bild nicht mehr auf die Seite? -> neue Seite
        if self.y + h > BOTTOM_LIMIT and h <= (BOTTOM_LIMIT - TOP):
            self._new_page()
        # ggf. zusätzlich auf Resthöhe begrenzen
        avail = BOTTOM_LIMIT - self.y
        if h > avail:
            f = avail / h
            w *= f; h *= f
        x = MARGIN + (USABLE - w) / 2
        y = PAGE_H - self.y - h
        self._op(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q\n")
        self.y += h + 6

    # --- Absatz mit Inline-Stilen (fett/kursiv/code), Wort-Umbruch ---
    def _para(self, runs: list[tuple[str, str]], size: float,
              leading: float, indent: float = MARGIN, color=INK):
        self._para_color = color
        x_start = indent
        space_w = _text_width(" ", size, False)
        # Wörter mit zugehörigem Stil sammeln
        words: list[tuple[str, str]] = []
        for text, style in runs:
            parts = text.split(" ")
            for k, p in enumerate(parts):
                if p:
                    words.append((p, style))
                if k < len(parts) - 1:
                    words.append((" ", style))
        line: list[tuple[str, str]] = []
        x = x_start
        for word, style in words:
            mono = style == "code"
            ww = _text_width(word, size, mono)
            if word != " " and x + ww > PAGE_W - MARGIN and line:
                self._flush_line(line, x_start, size, leading)
                line = []
                x = x_start
                if word == " ":
                    continue
            line.append((word, style))
            x += ww
        if line:
            self._flush_line(line, x_start, size, leading)

    def _flush_line(self, line, x_start, size, leading):
        self._need(leading)
        baseline = self.y + size
        x = x_start
        base_color = getattr(self, "_para_color", INK)
        for word, style in line:
            mono = style == "code"
            font = {"bold": F_BOLD, "italic": F_ITAL, "code": F_MONO}.get(style, F_REG)
            rgb = CODE_INK if mono else base_color
            if mono and word.strip():
                self._rect(x - 0.5, self.y + 1.5, _text_width(word, size, True) + 1,
                           leading - 2, INLINE_BG)
            self._show(x, baseline, word, size, font, rgb)
            x += _text_width(word, size, mono)
        self.y += leading

    # --- Seitenzahlen ---
    def _add_page_numbers(self):
        total = len(self.pages)
        line_y = MARGIN / 1.8 + 12          # y (von unten) der Fußzeilen-Linie
        r, g, b = RULE
        mr, mg, mb = MUTED
        for i, page in enumerate(self.pages, 1):
            # feine Trennlinie über der Fußzeile
            page.extend(
                f"{r:.3f} {g:.3f} {b:.3f} RG 0.5 w "
                f"{MARGIN:.2f} {line_y:.2f} m {PAGE_W - MARGIN:.2f} {line_y:.2f} l S\n"
                .encode("latin-1"))
            # Seitenzahl mittig
            txt = f"{i} / {total}"
            w = _text_width(txt, 8, False)
            page.extend(
                f"BT /{F_REG} 8 Tf {mr:.2f} {mg:.2f} {mb:.2f} rg "
                f"{(PAGE_W - w) / 2:.2f} {MARGIN / 1.8:.2f} Td (".encode("latin-1"))
            page.extend(_esc(txt))
            page.extend(b") Tj ET\n")
            # Titel links in der Fußzeile (dezent)
            if self.title:
                page.extend(
                    f"BT /{F_REG} 8 Tf {mr:.2f} {mg:.2f} {mb:.2f} rg "
                    f"{MARGIN:.2f} {MARGIN / 1.8:.2f} Td (".encode("latin-1"))
                page.extend(_esc(self.title[:60]))
                page.extend(b") Tj ET\n")

    # ===================================================================
    #  Zu PDF-Bytes zusammenbauen
    # ===================================================================
    def to_bytes(self) -> bytes:
        self._add_page_numbers()
        objects: list[bytes] = []

        def add(obj: bytes) -> int:
            objects.append(obj)
            return len(objects)            # Objektnummer (1-basiert)

        # 1 Catalog, 2 Pages (Nummern fest)
        add(b"")   # Platzhalter Catalog (Nr 1)
        add(b"")   # Platzhalter Pages   (Nr 2)

        # Fonts (3..6)
        def font_obj(base):
            return (f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
                    f"/Encoding /WinAnsiEncoding >>").encode("latin-1")
        n_f1 = add(font_obj("Helvetica"))
        n_f2 = add(font_obj("Helvetica-Bold"))
        n_f3 = add(font_obj("Helvetica-Oblique"))
        n_f4 = add(font_obj("Courier"))

        # Bilder (XObjects)
        img_nums = []
        for im in self.images:
            extra = ""
            hdr = (f"<< /Type /XObject /Subtype /Image /Width {im['w']} "
                   f"/Height {im['h']} /ColorSpace {im['cs']} "
                   f"/BitsPerComponent {im['bpc']} /Filter {im['filter']} "
                   f"/Length {len(im['data'])}{extra} >>\nstream\n").encode("latin-1")
            obj = hdr + im["data"] + b"\nendstream"
            img_nums.append(add(obj))

        # Seiten + Content-Streams
        font_res = (f"/Font << /{F_REG} {n_f1} 0 R /{F_BOLD} {n_f2} 0 R "
                    f"/{F_ITAL} {n_f3} 0 R /{F_MONO} {n_f4} 0 R >>")
        xobj_res = ""
        if self.images:
            xobj_res = " /XObject << " + " ".join(
                f"/{im['name']} {num} 0 R" for im, num in zip(self.images, img_nums)
            ) + " >>"
        page_nums = []
        for content in self.pages:
            comp = zlib.compress(bytes(content), 6)
            cnum = add(b"<< /Length " + str(len(comp)).encode() +
                       b" /Filter /FlateDecode >>\nstream\n" + comp + b"\nendstream")
            pnum = add((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
                        f"/Resources << {font_res}{xobj_res} >> /Contents {cnum} 0 R >>"
                        ).encode("latin-1"))
            page_nums.append(pnum)

        # Platzhalter füllen
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join(f"{p} 0 R" for p in page_nums)
        objects[1] = (f"<< /Type /Pages /Kids [{kids}] /Count {len(page_nums)} >>").encode("latin-1")

        # Zusammensetzen + xref
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, obj in enumerate(objects, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
        xref_pos = len(out)
        count = len(objects) + 1
        out += f"xref\n0 {count}\n".encode("latin-1")
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode("latin-1")
        out += (f"trailer\n<< /Size {count} /Root 1 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF").encode("latin-1")
        return bytes(out)


# ===========================================================================
#  Inline-Stile + Hilfen
# ===========================================================================
_INLINE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")


def _inline(text: str, force_bold: bool = False) -> list[tuple[str, str]]:
    """Zerlegt einen Text in (Text, Stil)-Stücke. Stil: normal/bold/italic/code."""
    text = text.replace("\t", "    ")
    runs: list[tuple[str, str]] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            runs.append((part[2:-2], "bold"))
        elif part.startswith("`") and part.endswith("`") and len(part) >= 2:
            runs.append((part[1:-1], "code"))
        elif part.startswith("*") and part.endswith("*") and len(part) >= 2:
            runs.append((part[1:-1], "italic"))
        else:
            runs.append((part, "bold" if force_bold else "normal"))
    if force_bold:
        runs = [(t, "bold") for t, _ in runs]
    return runs or [("", "normal")]


def _wrap_plain(text: str, max_w: float, size: float) -> list[str]:
    """Einfacher Wort-Umbruch (für Tabellenzellen)."""
    text = re.sub(r"[*`]", "", text)
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _text_width(trial, size, False) > max_w and cur:
            lines.append(cur); cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


# ===========================================================================
#  Markdown -> PDF
# ===========================================================================
_H = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUM = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_IMG = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")


def markdown_to_pdf(markdown: str, out_path: str | Path,
                    titel: str = "", base_dir: str | Path | None = None) -> str:
    """Rendert Markdown in ein echtes PDF. Bildpfade dürfen relativ zu base_dir
    (Standard: Ordner der Ausgabedatei) oder absolut sein."""
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".pdf":
        out_path = out_path.with_suffix(".pdf")
    base = Path(base_dir) if base_dir else out_path.parent

    doc = Document(title=titel)
    if titel:
        doc.heading(titel, 1)

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Code-Block ```
        if stripped.startswith("```"):
            block = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                block.append(lines[i]); i += 1
            i += 1
            doc.code_block(block)
            continue

        # Tabelle (| ... | mit Trenn-Zeile darunter)
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1]) \
                and "-" in lines[i + 1]:
            rows = []
            header = [c.strip() for c in stripped.strip("|").split("|")]
            rows.append(header)
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            doc.table(rows)
            continue

        # Leerzeile
        if not stripped:
            i += 1
            continue

        # Trennlinie
        if _HR.match(stripped):
            doc.rule(); i += 1; continue

        # Zitat (> Text, auch mehrzeilig)
        m = _QUOTE.match(line)
        if m:
            quote = [m.group(1)]
            i += 1
            while i < n and _QUOTE.match(lines[i]):
                quote.append(_QUOTE.match(lines[i]).group(1)); i += 1
            doc.blockquote(" ".join(q for q in quote if q.strip()))
            continue

        # Bild
        m = _IMG.match(line)
        if m:
            p = Path(m.group(2).strip().strip('"'))
            if not p.is_absolute():
                p = base / p
            doc.image(p, m.group(1)); i += 1; continue

        # Überschrift
        m = _H.match(stripped)
        if m:
            doc.heading(m.group(2), len(m.group(1))); i += 1; continue

        # Aufzählung
        m = _BULLET.match(line)
        if m:
            doc.bullet(m.group(1)); i += 1; continue
        m = _NUM.match(line)
        if m:
            doc.bullet(m.group(2), ordered=f"{m.group(1)}."); i += 1; continue

        # Absatz (zusammenhängende Zeilen bis Leerzeile/Block)
        para = [stripped]
        i += 1
        while i < n and lines[i].strip() and not lines[i].strip().startswith(("#", "```", "|", ">")) \
                and not _BULLET.match(lines[i]) and not _NUM.match(lines[i]) \
                and not _IMG.match(lines[i]) and not _HR.match(lines[i].strip()):
            para.append(lines[i].strip()); i += 1
        doc.paragraph(" ".join(para))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(doc.to_bytes())
    return str(out_path)
