"""textmodell.py – gemeinsames Modell für Textdokumente: Absätze aus Läufen, Tabellen und Bilder.

Die Leser (docx, odt, rtf) liefern ein Dokument, die Schreiber nehmen eins. Für die Anzeige wird daraus HTML;
Bilder stehen darin als  nemi-bild:<n>  und kommen als Ressource dazu (kein Dateizugriff aus dem HTML).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace

BILD_SCHEMA = "nemi-bild"
BILD_ENDUNGEN = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp"}


@dataclass
class Lauf:
    text: str
    fett: bool = False
    kursiv: bool = False
    unter: bool = False
    durch: bool = False
    link: str = ""
    farbe: str = ""                 # Textfarbe "#rrggbb", leer = Standard
    marker: str = ""                # Hintergrund (Textmarker) "#rrggbb"
    code: bool = False              # Schreibmaschinenschrift (Markdown `code`)

    def gleich_formatiert(self, o: "Lauf") -> bool:
        return (self.fett, self.kursiv, self.unter, self.durch, self.link, self.farbe, self.marker, self.code) == \
            (o.fett, o.kursiv, o.unter, o.durch, o.link, o.farbe, o.marker, o.code)


@dataclass
class Absatz:
    laeufe: list[Lauf] = field(default_factory=list)
    stufe: int = 0                  # Überschrift 1–6, 0 = normaler Text
    liste: str = ""                 # "punkt" | "zahl" | ""
    ebene: int = 0                  # Einrückung der Liste (0 = oberste)
    ausrichtung: str = ""           # "" | "mitte" | "rechts" | "block"
    zitat: bool = False             # Markdown „> …“
    aufgabe: str = ""               # Checkbox: "" | "offen" | "erledigt" (Markdown „- [ ]“ / „- [x]“)

    @property
    def text(self) -> str:
        return "".join(l.text for l in self.laeufe)

    def zusammenfassen(self) -> "Absatz":
        """Benachbarte Läufe gleicher Formatierung verbinden, leere entfernen."""
        neu: list[Lauf] = []
        for l in self.laeufe:
            if not l.text:
                continue
            if neu and neu[-1].gleich_formatiert(l):
                neu[-1] = replace(l, text=neu[-1].text + l.text)
            else:
                neu.append(l)
        self.laeufe = neu
        return self


@dataclass
class Bild:
    daten: bytes
    endung: str = "png"             # Schlüssel aus BILD_ENDUNGEN
    breite_pt: float = 0.0          # 0 = Originalgröße
    hoehe_pt: float = 0.0


@dataclass
class Tabelle:
    zeilen: list[list[list[Absatz]]] = field(default_factory=list)   # Zeile -> Zelle -> Absätze

    @property
    def spalten(self) -> int:
        return max((len(z) for z in self.zeilen), default=0)


@dataclass
class Linie:
    """Trennlinie (Markdown „---“)."""


Block = Absatz | Tabelle | Bild | Linie
AUFGABE_ZEICHEN = {"offen": "☐", "erledigt": "☑"}      # so stehen Checkboxen in .docx/.odt/.rtf
_ZEICHEN_AUFGABE = {"☐": "offen", "☑": "erledigt", "☒": "erledigt"}


def aufgabe_aus_text(a: "Absatz") -> "Absatz":
    """Beginnt ein Absatz mit ☐/☑ (so speichern docx/odt/rtf Checkboxen), wird er wieder eine Checkbox."""
    if a.aufgabe or not a.laeufe:
        return a
    erster = a.laeufe[0]
    t = erster.text.lstrip()
    if t[:1] in _ZEICHEN_AUFGABE:
        a.aufgabe = _ZEICHEN_AUFGABE[t[0]]
        erster.text = t[1:].lstrip(" \u00a0")
        a.liste = a.liste or "punkt"
        if not erster.text:
            a.laeufe.pop(0)
    return a


@dataclass
class Dokument:
    bloecke: list[Block] = field(default_factory=list)
    papier: str = ""                # Seitenfarbe "#rrggbb", leer = weiß

    def text(self) -> str:
        teile = []
        for b in self.bloecke:
            if isinstance(b, Absatz):
                teile.append(b.text)
            elif isinstance(b, Tabelle):
                for z in b.zeilen:
                    teile.append("\t".join(" ".join(a.text for a in zelle) for zelle in z))
        return "\n".join(teile)


def farbe_sauber(wert) -> str:
    """"#rrggbb" (klein) aus "#RRGGBB", "RRGGBB", "#RGB" oder "FFRRGGBB" (ARGB); sonst ""."""
    t = str(wert or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{8}", t):
        t = t[2:]
    if re.fullmatch(r"[0-9a-fA-F]{3}", t):
        t = "".join(c * 2 for c in t)
    return "#" + t.lower() if re.fullmatch(r"[0-9a-fA-F]{6}", t) else ""


def bild_endung(daten: bytes) -> str | None:
    """Bildart an den ersten Bytes erkennen (nicht am Dateinamen)."""
    if daten.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if daten.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if daten[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if daten.startswith(b"BM"):
        return "bmp"
    return None


def bild_pixel(daten: bytes) -> tuple[int, int]:
    """(Breite, Höhe) in Pixeln aus dem Bildkopf; (0, 0) wenn unbekannt."""
    import struct
    try:
        art = bild_endung(daten)
        if art == "png" and len(daten) >= 24:
            return struct.unpack(">II", daten[16:24])
        if art == "gif" and len(daten) >= 10:
            return struct.unpack("<HH", daten[6:10])
        if art == "bmp" and len(daten) >= 26:
            b, h = struct.unpack("<ii", daten[18:26])
            return abs(b), abs(h)
        if art == "jpeg":
            i = 2
            while i + 9 < len(daten):
                if daten[i] != 0xFF:
                    i += 1
                    continue
                marke = daten[i + 1]
                if marke in (0xD8, 0x01) or 0xD0 <= marke <= 0xD7:
                    i += 2
                    continue
                laenge = struct.unpack(">H", daten[i + 2:i + 4])[0]
                if 0xC0 <= marke <= 0xCF and marke not in (0xC4, 0xC8, 0xCC):
                    h, b = struct.unpack(">HH", daten[i + 5:i + 9])
                    return b, h
                i += 2 + laenge
    except struct.error:
        pass
    return 0, 0


def bild_masse_pt(b: Bild, max_breite_pt: float = 450.0) -> tuple[float, float]:
    """Anzeigegröße in Punkt: angegebene Größe, sonst Pixel bei 96 dpi – höchstens so breit wie die Seite."""
    breite, hoehe = b.breite_pt, b.hoehe_pt
    if breite <= 0 or hoehe <= 0:
        px_b, px_h = bild_pixel(b.daten)
        breite, hoehe = (px_b * 0.75, px_h * 0.75) if px_b and px_h else (200.0, 150.0)
    if breite > max_breite_pt:
        hoehe, breite = hoehe * max_breite_pt / breite, max_breite_pt
    return breite, hoehe


def _lauf_html(l: Lauf) -> str:
    t = html.escape(l.text).replace("\n", "<br>").replace("\t", "&emsp;")
    if l.fett:
        t = f"<b>{t}</b>"
    if l.kursiv:
        t = f"<i>{t}</i>"
    if l.unter:
        t = f"<u>{t}</u>"
    if l.durch:
        t = f"<s>{t}</s>"
    if l.code:
        t = f"<code>{t}</code>"
    stil = ""
    if (f := farbe_sauber(l.farbe)):
        stil += f"color:{f};"
    if (m := farbe_sauber(l.marker)):
        stil += f"background-color:{m};"
    if stil:
        t = f'<span style="{stil}">{t}</span>'
    if l.link and l.link.lower().startswith(("https://", "http://", "mailto:")):
        t = f'<a href="{html.escape(l.link, quote=True)}">{t}</a>'
    return t


_AUSRICHTUNG = {"mitte": "center", "rechts": "right", "block": "justify"}


def _absatz_html(a: Absatz, tag: str = "p") -> str:
    inhalt = "".join(_lauf_html(l) for l in a.laeufe) or "<br>"
    stil = f' align="{_AUSRICHTUNG[a.ausrichtung]}"' if a.ausrichtung in _AUSRICHTUNG else ""
    if a.stufe:
        tag = f"h{min(6, max(1, a.stufe))}"
    if tag == "li" and a.aufgabe in AUFGABE_ZEICHEN:            # Qt liest class="checked"/"unchecked"
        stil += f' class="{"checked" if a.aufgabe == "erledigt" else "unchecked"}"'
    html_ = f"<{tag}{stil}>{inhalt}</{tag}>"
    return f"<blockquote>{html_}</blockquote>" if a.zitat and tag != "li" else html_


def nach_html(dok: Dokument) -> tuple[str, dict[str, Bild]]:
    """(HTML, {"nemi-bild:<n>": Bild}) für die Anzeige."""
    teile: list[str] = []
    bilder: dict[str, Bild] = {}
    offen: list[tuple[str, int]] = []           # offene Listen: (tag, ebene)

    def listen_schliessen(bis: int = -1):
        while offen and offen[-1][1] > bis:
            teile.append(f"</{offen.pop()[0]}>")

    for b in dok.bloecke:
        if isinstance(b, Absatz) and b.liste:
            tag = "ol" if b.liste == "zahl" else "ul"
            listen_schliessen(b.ebene)
            if offen and offen[-1][1] == b.ebene and offen[-1][0] != tag:
                teile.append(f"</{offen.pop()[0]}>")
            while not offen or offen[-1][1] < b.ebene:
                ebene = offen[-1][1] + 1 if offen else 0
                art = tag if ebene == b.ebene else "ul"
                teile.append(f"<{art}>")
                offen.append((art, ebene))
            teile.append(_absatz_html(b, "li"))
            continue
        listen_schliessen()
        if isinstance(b, Linie):
            teile.append("<hr>")
        elif isinstance(b, Absatz):
            teile.append(_absatz_html(b))
        elif isinstance(b, Tabelle):
            zeilen = []
            for z in b.zeilen:
                zellen = "".join("<td>" + ("".join(_absatz_html(a) for a in zelle) or "<br>") + "</td>" for zelle in z)
                zeilen.append(f"<tr>{zellen}</tr>")
            teile.append('<table border="1" cellspacing="0" cellpadding="4">' + "".join(zeilen) + "</table>")
        elif isinstance(b, Bild) and b.endung in BILD_ENDUNGEN:
            name = f"{BILD_SCHEMA}:{len(bilder) + 1}"
            bilder[name] = b
            masse = ""
            if b.breite_pt > 0 and b.hoehe_pt > 0:      # pt -> px bei 96 dpi
                masse = f' width="{round(b.breite_pt * 4 / 3)}" height="{round(b.hoehe_pt * 4 / 3)}"'
            teile.append(f'<p><img src="{name}"{masse}></p>')
    listen_schliessen()
    papier = farbe_sauber(dok.papier)
    kopf = f'<body bgcolor="{papier}">' if papier else "<body>"
    return "<html>" + kopf + "\n".join(teile) + "</body></html>", bilder
