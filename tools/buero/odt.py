"""odt.py – OpenDocument-Text (.odt) lesen und schreiben: Absätze, Überschriften, Listen,
Fett/Kursiv/Unterstrichen/Durchgestrichen, Ausrichtung, Links, Tabellen und eingebettete Bilder."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from buero.sicher import MAX_BILD, FormatFehler, Paket
from buero.textmodell import (AUFGABE_ZEICHEN, Absatz, Bild, Dokument, Lauf, Linie, Tabelle, BILD_ENDUNGEN,
                              aufgabe_aus_text, bild_endung, bild_masse_pt, farbe_sauber)

MONO = ("mono", "consolas", "courier", "lucida console")
CODE_HG = "#f2f2f4"
ZITAT_STILE = {"quotations", "quotation", "zitat", "zitate"}

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}
O, S, T, TB, D, FO, XL, SVG = (f"{{{NS[k]}}}" for k in ("office", "style", "text", "table", "draw", "fo", "xlink",
                                                         "svg"))
MIME = "application/vnd.oasis.opendocument.text"
_PT = {"pt": 1.0, "cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "pc": 12.0, "px": 0.75}
_AUS = {"center": "mitte", "end": "rechts", "right": "rechts", "justify": "block"}


def laenge_pt(wert: str | None) -> float:
    m = re.fullmatch(r"\s*([0-9.]+)\s*(pt|cm|mm|in|pc|px)?\s*", wert or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1)) * _PT[m.group(2) or "pt"]
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

class _Stile:
    """Text-, Absatz- und Listenstile aus styles.xml und den automatischen Stilen in content.xml."""

    def __init__(self):
        self.text: dict[str, dict] = {}
        self.absatz: dict[str, dict] = {}
        self.eltern: dict[str, str] = {}
        self.listen: dict[str, dict[int, str]] = {}
        self.papier = ""

    def lesen(self, wurzel) -> None:
        for st in wurzel.iter(S + "style"):
            name = st.get(S + "name") or ""
            if (eltern := st.get(S + "parent-style-name")):
                self.eltern[name] = eltern
            tp = st.find(S + "text-properties")
            if tp is not None:
                f = {}
                if (w := tp.get(FO + "font-weight")) is not None:
                    f["fett"] = w in ("bold", "600", "700", "800", "900")
                if (k := tp.get(FO + "font-style")) is not None:
                    f["kursiv"] = k in ("italic", "oblique")
                if (u := tp.get(S + "text-underline-style")) is not None:
                    f["unter"] = u != "none"
                if (d := tp.get(S + "text-line-through-style")) is not None:
                    f["durch"] = d != "none"
                if (c := tp.get(FO + "color")) is not None:
                    f["farbe"] = farbe_sauber(c)
                if (b := tp.get(FO + "background-color")) is not None:
                    f["marker"] = farbe_sauber(b)          # "transparent" ergibt ""
                schrift = (tp.get(S + "font-name") or tp.get(FO + "font-family") or "").lower()
                if schrift:
                    f["code"] = any(m in schrift for m in MONO)
                if f.get("code") and f.get("marker") == CODE_HG:
                    f["marker"] = ""                       # Grau hinter Code gehört zum Code
                self.text[name] = f
            pp = st.find(S + "paragraph-properties")
            ausr = _AUS.get(pp.get(FO + "text-align") or "", "") if pp is not None else ""
            stufe = 0
            m = re.fullmatch(r"Heading_20_([1-6])", name) or re.fullmatch(r"Heading ([1-6])",
                                                                          st.get(S + "display-name") or "")
            if m:
                stufe = int(m.group(1))
            elif (ol := st.get(S + "default-outline-level") or "").isdigit():
                stufe = min(6, int(ol))
            anzeige = (st.get(S + "display-name") or name.replace("_20_", " ")).lower()
            self.absatz[name] = {"ausrichtung": ausr, "stufe": stufe, "zitat": anzeige in ZITAT_STILE,
                                 "linie": anzeige == "horizontal line"}
        for ls in wurzel.iter(T + "list-style"):
            ebenen = {}
            for k in ls:
                try:
                    ebene = int(k.get(T + "level") or 1) - 1
                except ValueError:
                    continue
                ebenen[ebene] = "zahl" if k.tag == T + "list-level-style-number" else "punkt"
            self.listen[ls.get(S + "name") or ""] = ebenen
        for pl in wurzel.iter(S + "page-layout-properties"):   # Seitenfarbe (erste Seitenvorlage)
            if not self.papier and (c := farbe_sauber(pl.get(FO + "background-color"))):
                self.papier = c

    def text_format(self, name: str) -> dict:
        kette, f, n = [], {}, name
        while n and n not in kette and len(kette) < 20:
            kette.append(n)
            n = self.eltern.get(n, "")
        for n in reversed(kette):
            f.update(self.text.get(n, {}))
        return f

    def absatz_format(self, name: str) -> dict:
        f, n, gesehen = {"ausrichtung": "", "stufe": 0, "zitat": False, "linie": False}, name, set()
        while n and n not in gesehen:
            gesehen.add(n)
            a = self.absatz.get(n, {})
            f["zitat"] = f["zitat"] or bool(a.get("zitat"))
            f["linie"] = f["linie"] or bool(a.get("linie"))
            if not f["ausrichtung"] and a.get("ausrichtung"):
                f["ausrichtung"] = a["ausrichtung"]
            if not f["stufe"] and a.get("stufe"):
                f["stufe"] = a["stufe"]
            n = self.eltern.get(n, "")
        return f


class _Leser:
    def __init__(self, p: Paket):
        self.p = p
        self.stile = _Stile()
        if p.hat("styles.xml"):
            self.stile.lesen(p.xml("styles.xml"))
        self.inhalt = p.xml("content.xml")
        self.stile.lesen(self.inhalt)

    def dokument(self) -> Dokument:
        dok = Dokument(papier=self.stile.papier)
        koerper = self.inhalt.find(f"{O}body/{O}text")
        if koerper is not None:
            self._bloecke(koerper, dok.bloecke, None, 0)
        return dok

    def _bloecke(self, eltern, ziel: list, liste: tuple[str, str] | None, ebene: int) -> None:
        for k in eltern:
            if k.tag in (T + "p", T + "h"):
                self._absatz(k, ziel, liste, ebene)
            elif k.tag == T + "list":                 # verschachtelt (in einem Listenpunkt): eine Ebene tiefer
                stil = k.get(T + "style-name") or (liste[0] if liste else "")
                tiefe = min(8, ebene + 1) if liste is not None else 0
                for item in k:
                    if item.tag in (T + "list-item", T + "list-header"):
                        art = self.stile.listen.get(stil, {}).get(tiefe, "punkt")
                        self._bloecke(item, ziel, (stil, art), tiefe)
            elif k.tag == TB + "table":
                ziel.append(self._tabelle(k))
            elif k.tag in (T + "section", T + "index-body", T + "table-of-content"):
                self._bloecke(k, ziel, liste, ebene)

    def _absatz(self, p, ziel: list, liste, ebene: int) -> None:
        f = self.stile.absatz_format(p.get(T + "style-name") or "")
        a = Absatz(ausrichtung=f["ausrichtung"], stufe=f["stufe"], zitat=f["zitat"])
        if p.tag == T + "h":
            try:
                a.stufe = max(1, min(6, int(p.get(T + "outline-level") or 1)))
            except ValueError:
                a.stufe = 1
        if liste is not None:
            a.liste, a.ebene = liste[1], ebene
        bilder: list[Bild] = []
        self._inhalt(p, a, bilder, {}, "")
        if f["linie"] and not a.text.strip() and not bilder:
            ziel.append(Linie())
            return
        if a.laeufe or not bilder:
            ziel.append(aufgabe_aus_text(a.zusammenfassen()))
        ziel.extend(bilder)

    def _inhalt(self, e, a: Absatz, bilder: list, fmt: dict, link: str) -> None:
        if e.text:
            a.laeufe.append(Lauf(e.text, link=link, **_fmt(fmt)))
        for k in e:
            if k.tag == T + "span":
                self._inhalt(k, a, bilder, {**fmt, **self.stile.text_format(k.get(T + "style-name") or "")}, link)
            elif k.tag == T + "a":
                self._inhalt(k, a, bilder, fmt, k.get(XL + "href") or "")
            elif k.tag == T + "s":
                try:
                    n = max(1, min(1000, int(k.get(T + "c") or 1)))
                except ValueError:
                    n = 1
                a.laeufe.append(Lauf(" " * n, link=link, **_fmt(fmt)))
            elif k.tag == T + "tab":
                a.laeufe.append(Lauf("\t", link=link, **_fmt(fmt)))
            elif k.tag == T + "line-break":
                a.laeufe.append(Lauf("\n", link=link, **_fmt(fmt)))
            elif k.tag == D + "frame":
                self._bild(k, bilder)
            elif k.tag in (T + "note", T + "annotation", O + "annotation"):
                pass                                   # Fuß-/Endnoten und Kommentare: nicht im Fließtext
            else:
                self._inhalt(k, a, bilder, fmt, link)
            if k.tail:
                a.laeufe.append(Lauf(k.tail, link=link, **_fmt(fmt)))

    def _bild(self, frame, bilder: list) -> None:
        img = frame.find(D + "image")
        if img is None:
            return
        href = img.get(XL + "href") or ""
        if not href or "://" in href or href.startswith(("/", "\\")):   # nur Teile aus dem Paket
            return
        pfad = posixpath.normpath(href.lstrip("./"))
        try:
            daten = self.p.roh(pfad, MAX_BILD)
        except FormatFehler:
            return
        if (art := bild_endung(daten)):
            bilder.append(Bild(daten, art, laenge_pt(frame.get(SVG + "width")), laenge_pt(frame.get(SVG + "height"))))

    def _tabelle(self, tbl) -> Tabelle:
        t = Tabelle()
        for zeile in tabellen_zeilen(tbl):
            zellen = []
            for c in zeile:
                if c.tag not in (TB + "table-cell", TB + "covered-table-cell"):
                    continue
                inhalt: list = []
                self._bloecke(c, inhalt, None, 0)
                absaetze = [b for b in inhalt if isinstance(b, Absatz)]
                try:
                    n = max(1, min(64, int(c.get(TB + "number-columns-repeated") or 1)))
                except ValueError:
                    n = 1
                zellen.extend([absaetze] * n)
            while zellen and not any(a.text for a in zellen[-1]):
                zellen.pop()
            t.zeilen.append(zellen)
        while t.zeilen and not t.zeilen[-1]:
            t.zeilen.pop()
        return t


def tabellen_zeilen(tbl):
    """Zeilen einer Tabelle (auch in Kopf- und Zeilengruppen), ohne in Zellen verschachtelte Tabellen."""
    for k in tbl:
        if k.tag == TB + "table-row":
            yield k
        elif k.tag in (TB + "table-header-rows", TB + "table-rows", TB + "table-row-group"):
            yield from tabellen_zeilen(k)


def _fmt(f: dict) -> dict:
    return {**{k: bool(f.get(k)) for k in ("fett", "kursiv", "unter", "durch")},
            "farbe": f.get("farbe") or "", "marker": f.get("marker") or "", "code": bool(f.get("code"))}


def lesen(pfad) -> Dokument:
    with Paket(pfad) as p:
        if not p.hat("content.xml"):
            raise FormatFehler("Kein OpenDocument (content.xml fehlt)")
        return _Leser(p).dokument()


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

_NS_KOPF = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items() if k != "manifest")


def _text_xml(text: str) -> str:
    """Text mit Tabs, Umbrüchen und mehrfachen Leerzeichen als ODF-Elemente."""
    raus = []
    for teil in re.split(r"(\t|\n| {2,})", text):
        if not teil:
            continue
        if teil == "\t":
            raus.append("<text:tab/>")
        elif teil == "\n":
            raus.append("<text:line-break/>")
        elif teil.startswith("  "):
            raus.append(f' <text:s text:c="{len(teil) - 1}"/>')
        else:
            raus.append(escape("".join(ch for ch in teil if ch >= " ")))
    return "".join(raus)


class _Schreiber:
    def __init__(self):
        self.text_stile: dict[tuple, str] = {}
        self.absatz_stile: dict[str, str] = {}
        self.bilder: list[tuple[str, bytes]] = []

    def _tstil(self, l: Lauf) -> str:
        schluessel = (l.fett, l.kursiv, l.unter, l.durch, farbe_sauber(l.farbe), farbe_sauber(l.marker), l.code)
        if not any(schluessel):
            return ""
        if schluessel not in self.text_stile:
            self.text_stile[schluessel] = f"T{len(self.text_stile) + 1}"
        return self.text_stile[schluessel]

    def laeufe(self, a: Absatz) -> str:
        raus = []
        for l in a.laeufe:
            t = _text_xml(l.text)
            if (st := self._tstil(l)):
                t = f'<text:span text:style-name="{st}">{t}</text:span>'
            if l.link and l.link.lower().startswith(("https://", "http://", "mailto:")):
                t = f'<text:a xlink:type="simple" xlink:href={quoteattr(l.link)}>{t}</text:a>'
            raus.append(t)
        return "".join(raus)

    def absatz(self, a: Absatz) -> str:
        if a.stufe:
            stil = f"Heading_20_{min(6, a.stufe)}"
            if a.ausrichtung:
                stil = self._pstil(a.ausrichtung, stil)
            return (f'<text:h text:style-name="{stil}" text:outline-level="{min(6, a.stufe)}">'
                    f"{self.laeufe(a)}</text:h>")
        if a.aufgabe in AUFGABE_ZEICHEN:                   # Checkbox: ☐/☑ vor dem Text, eingerückt
            zeichen = _text_xml(AUFGABE_ZEICHEN[a.aufgabe] + " ")
            return f'<text:p text:style-name="Aufgabe">{zeichen}{self.laeufe(a)}</text:p>'
        basis = "Quotations" if a.zitat else "Standard"
        stil = self._pstil(a.ausrichtung, basis) if a.ausrichtung else basis
        return f'<text:p text:style-name="{stil}">{self.laeufe(a)}</text:p>'

    def _pstil(self, ausrichtung: str, eltern: str) -> str:
        schluessel = f"{eltern}|{ausrichtung}"
        if schluessel not in self.absatz_stile:
            self.absatz_stile[schluessel] = f"P{len(self.absatz_stile) + 1}"
        return self.absatz_stile[schluessel]

    def bild(self, b: Bild) -> str:
        if b.endung not in BILD_ENDUNGEN:
            return ""
        name = f"Pictures/bild{len(self.bilder) + 1}.{'jpg' if b.endung == 'jpeg' else b.endung}"
        self.bilder.append((name, b.daten))
        breite, hoehe = bild_masse_pt(b)
        return (f'<text:p text:style-name="Standard"><draw:frame draw:name="Bild{len(self.bilder)}" '
                f'text:anchor-type="as-char" svg:width="{breite:.2f}pt" svg:height="{hoehe:.2f}pt">'
                f'<draw:image xlink:href="{name}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>'
                f"</draw:frame></text:p>")

    def tabelle(self, t: Tabelle, nr: int) -> str:
        spalten = max(1, t.spalten)
        zeilen = []
        for z in t.zeilen:
            zellen = []
            for i in range(spalten):
                absaetze = z[i] if i < len(z) else []
                inhalt = "".join(self.absatz(a) for a in absaetze) or '<text:p text:style-name="Standard"/>'
                zellen.append(f'<table:table-cell table:style-name="Zelle" office:value-type="string">{inhalt}'
                              f"</table:table-cell>")
            zeilen.append("<table:table-row>" + "".join(zellen) + "</table:table-row>")
        return (f'<table:table table:name="Tabelle{nr}" table:style-name="Tab">'
                f'<table:table-column table:number-columns-repeated="{spalten}"/>{"".join(zeilen)}</table:table>')

    def listen(self, bloecke: list, i: int) -> tuple[str, int]:
        """Eine zusammenhängende Liste ab Block i (mit Verschachtelung). Rückgabe: (XML, nächster Index)."""

        def ebene_xml(ebene: int, j: int, art: str) -> tuple[str, int]:
            stil = "L_zahl" if art == "zahl" else "L_punkt"
            teile = [f'<text:list text:style-name="{stil}">' if ebene == 0 else "<text:list>"]
            while j < len(bloecke):
                b = bloecke[j]
                if not (isinstance(b, Absatz) and b.liste) or b.aufgabe or b.ebene < ebene:
                    break
                if ebene == 0 and b.ebene == 0 and b.liste != art:
                    break
                if b.ebene > ebene:                       # tiefer: in den letzten Punkt hängen
                    inner, j = ebene_xml(ebene + 1, j, b.liste)
                    if teile[-1].endswith("</text:list-item>"):
                        teile[-1] = teile[-1][:-len("</text:list-item>")] + inner + "</text:list-item>"
                    else:
                        teile.append(f"<text:list-item>{inner}</text:list-item>")
                    continue
                teile.append(f"<text:list-item>{self.absatz(Absatz(b.laeufe, 0, '', 0, b.ausrichtung))}"
                             "</text:list-item>")
                j += 1
            teile.append("</text:list>")
            return "".join(teile), j

        return ebene_xml(0, i, bloecke[i].liste)

    def koerper(self, dok: Dokument) -> str:
        teile, i, tabellen = [], 0, 0
        while i < len(dok.bloecke):
            b = dok.bloecke[i]
            if isinstance(b, Absatz) and b.liste and not b.aufgabe:
                xml, i = self.listen(dok.bloecke, i)
                teile.append(xml)
                continue
            if isinstance(b, Absatz):
                teile.append(self.absatz(b))
            elif isinstance(b, Tabelle):
                tabellen += 1
                teile.append(self.tabelle(b, tabellen))
            elif isinstance(b, Linie):
                teile.append('<text:p text:style-name="Horizontal_20_Line"/>')
            else:
                teile.append(self.bild(b))
            i += 1
        return "".join(teile)

    def auto_stile(self) -> str:
        teile = []
        for (fett, kursiv, unter, durch, farbe, marker, code), name in self.text_stile.items():
            marker = marker or (CODE_HG if code else "")
            tp = (' fo:font-weight="bold" style:font-weight-asian="bold" style:font-weight-complex="bold"' if fett else "") + \
                 (' fo:font-style="italic" style:font-style-asian="italic" style:font-style-complex="italic"' if kursiv else "") + \
                 (' style:text-underline-style="solid" style:text-underline-width="auto" '
                  'style:text-underline-color="font-color"' if unter else "") + \
                 (' style:text-line-through-style="solid" style:text-line-through-type="single"' if durch else "") + \
                 (f' fo:color="{farbe}"' if farbe else "") + (f' fo:background-color="{marker}"' if marker else "") + \
                 (' style:font-name="Liberation Mono" fo:font-family="\'Liberation Mono\', Consolas, monospace"'
                  if code else "")
            teile.append(f'<style:style style:name="{name}" style:family="text"><style:text-properties{tp}/>'
                         "</style:style>")
        ausr = {"mitte": "center", "rechts": "end", "block": "justify"}
        for schluessel, name in self.absatz_stile.items():
            eltern, a = schluessel.split("|")
            teile.append(f'<style:style style:name="{name}" style:family="paragraph" style:parent-style-name="{eltern}">'
                         f'<style:paragraph-properties fo:text-align="{ausr.get(a, "start")}"/></style:style>')
        teile.append('<style:style style:name="Tab" style:family="table"><style:table-properties '
                     'style:width="16cm" table:align="margins"/></style:style>')
        teile.append('<style:style style:name="Zelle" style:family="table-cell"><style:table-cell-properties '
                     'fo:padding="0.1cm" fo:border="0.5pt solid #000000"/></style:style>')
        punkte = "•◦▪"
        lp = "".join(f'<text:list-level-style-bullet text:level="{n}" text:bullet-char="{punkte[(n - 1) % 3]}">'
                     f'<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
                     f'<style:list-level-label-alignment text:label-followed-by="listtab" '
                     f'fo:text-indent="-0.635cm" fo:margin-left="{0.635 * (n + 1):.3f}cm"/>'
                     f"</style:list-level-properties></text:list-level-style-bullet>" for n in range(1, 11))
        lz = "".join(f'<text:list-level-style-number text:level="{n}" style:num-suffix="." style:num-format="1">'
                     f'<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
                     f'<style:list-level-label-alignment text:label-followed-by="listtab" '
                     f'fo:text-indent="-0.635cm" fo:margin-left="{0.635 * (n + 1):.3f}cm"/>'
                     f"</style:list-level-properties></text:list-level-style-number>" for n in range(1, 11))
        teile.append(f'<text:list-style style:name="L_punkt">{lp}</text:list-style>')
        teile.append(f'<text:list-style style:name="L_zahl">{lz}</text:list-style>')
        return "".join(teile)


def _styles_xml(papier: str = "") -> str:
    papier = farbe_sauber(papier)
    hg = f' fo:background-color="{papier}"' if papier else ""
    groesse = {1: "130%", 2: "115%", 3: "110%", 4: "100%", 5: "100%", 6: "95%"}
    kopf = "".join(f'<style:style style:name="Heading_20_{n}" style:display-name="Heading {n}" style:family="paragraph" '
                   f'style:parent-style-name="Heading" style:next-style-name="Standard" '
                   f'style:default-outline-level="{n}" style:class="text">'
                   f'<style:text-properties fo:font-size="{groesse[n]}" fo:font-weight="bold"/></style:style>'
                   for n in range(1, 7))
    return (f'<?xml version="1.0" encoding="UTF-8"?><office:document-styles {_NS_KOPF} office:version="1.3">'
            '<office:styles>'
            '<style:default-style style:family="paragraph"><style:text-properties style:font-name="Liberation Sans" '
            'fo:font-family="\'Liberation Sans\', Calibri, sans-serif" fo:font-size="11pt" fo:language="de" '
            'fo:country="DE"/></style:default-style>'
            '<style:style style:name="Standard" style:family="paragraph" style:class="text">'
            '<style:paragraph-properties fo:margin-bottom="0.21cm" fo:line-height="115%"/></style:style>'
            '<style:style style:name="Heading" style:family="paragraph" style:parent-style-name="Standard" '
            'style:next-style-name="Standard" style:class="text"><style:paragraph-properties fo:margin-top="0.42cm" '
            'fo:margin-bottom="0.21cm" fo:keep-with-next="always"/></style:style>'
            '<style:style style:name="Quotations" style:display-name="Quotations" style:family="paragraph" '
            'style:parent-style-name="Standard" style:class="html"><style:paragraph-properties fo:margin-left="1cm" '
            'fo:padding-left="0.25cm" fo:border-left="2.5pt solid #8b5cf6" fo:border-right="none" fo:border-top="none" '
            'fo:border-bottom="none"/><style:text-properties fo:font-style="italic" fo:color="#595959"/></style:style>'
            '<style:style style:name="Horizontal_20_Line" style:display-name="Horizontal Line" style:family="paragraph" '
            'style:parent-style-name="Standard" style:class="html"><style:paragraph-properties fo:margin-bottom="0.3cm" '
            'fo:padding="0cm" fo:border-left="none" fo:border-right="none" fo:border-top="none" '
            'fo:border-bottom="0.5pt solid #a6a6a6"/></style:style>'
            '<style:style style:name="Aufgabe" style:family="paragraph" style:parent-style-name="Standard">'
            '<style:paragraph-properties fo:margin-left="0.635cm"/></style:style>'
            f"{kopf}</office:styles>"
            '<office:automatic-styles><style:page-layout style:name="Seite"><style:page-layout-properties '
            'fo:page-width="21cm" fo:page-height="29.7cm" fo:margin-top="2cm" fo:margin-bottom="2cm" '
            f'fo:margin-left="2cm" fo:margin-right="2cm"{hg}/></style:page-layout></office:automatic-styles>'
            '<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="Seite"/>'
            "</office:master-styles></office:document-styles>")


def schreiben_bytes(dok: Dokument) -> bytes:
    s = _Schreiber()
    koerper = s.koerper(dok)
    inhalt = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_NS_KOPF} office:version="1.3">'
              f"<office:automatic-styles>{s.auto_stile()}</office:automatic-styles>"
              f"<office:body><office:text>{koerper}</office:text></office:body></office:document-content>")
    eintraege = "".join(f'<manifest:file-entry manifest:full-path="{n}" '
                        f'manifest:media-type="{BILD_ENDUNGEN[n.rsplit(".", 1)[1].replace("jpg", "jpeg")]}"/>'
                        for n, _ in s.bilder)
    manifest = (f'<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="{NS["manifest"]}" '
                f'manifest:version="1.3"><manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
                f'manifest:media-type="{MIME}"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                f"{eintraege}</manifest:manifest>")
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), MIME, compress_type=zipfile.ZIP_STORED)   # muss zuerst und roh stehen
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("content.xml", inhalt)
        z.writestr("styles.xml", _styles_xml(dok.papier))
        for name, daten in s.bilder:
            z.writestr(name, daten)
    return puffer.getvalue()
