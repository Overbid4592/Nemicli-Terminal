"""docx.py – Word-Dokumente (.docx) lesen und schreiben: Absätze, Überschriften, Listen, Fett/Kursiv/…, Links,
Tabellen und eingebettete Bilder. Felder, Kommentare, Änderungsverfolgung und Makros werden nicht übernommen."""
from __future__ import annotations

import io
import posixpath
import zipfile
from xml.sax.saxutils import escape, quoteattr

from buero.sicher import MAX_BILD, FormatFehler, Paket
from buero.textmodell import (AUFGABE_ZEICHEN, Absatz, Bild, Dokument, Lauf, Linie, Tabelle, BILD_ENDUNGEN,
                              aufgabe_aus_text, bild_endung, bild_masse_pt, farbe_sauber)

MONO = {"consolas", "courier new", "cascadia mono", "cascadia code", "lucida console", "liberation mono",
        "dejavu sans mono", "source code pro", "monospace"}
CODE_HG = "F2F2F4"

# Benannte Textmarker-Farben von Word (w:highlight)
MARKER_FARBEN = {"yellow": "#ffff00", "green": "#00ff00", "cyan": "#00ffff", "magenta": "#ff00ff", "blue": "#0000ff",
                 "red": "#ff0000", "darkblue": "#000080", "darkcyan": "#008080", "darkgreen": "#008000",
                 "darkmagenta": "#800080", "darkred": "#800000", "darkyellow": "#808000", "darkgray": "#808080",
                 "lightgray": "#c0c0c0", "black": "#000000", "white": "#ffffff"}

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V = "urn:schemas-microsoft-com:vml"
_W, _R, _PR, _A, _WP, _V = (f"{{{n}}}" for n in (W, R, PR, A, WP, V))
EMU_JE_PT = 12700
_AUS = {"center": "mitte", "right": "rechts", "end": "rechts", "both": "block", "distribute": "block"}
_EIN = {v: k for k, v in _AUS.items() if k in ("center", "right", "both")}


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def _an(e) -> bool:
    """w:b, w:i …: vorhanden und nicht ausdrücklich aus."""
    return e is not None and (e.get(_W + "val") or "true").lower() not in ("0", "false", "off", "none")


class _Leser:
    def __init__(self, paket: Paket):
        self.p = paket
        self.rels: dict[str, tuple[str, str]] = {}
        if paket.hat("word/_rels/document.xml.rels"):
            for r in paket.xml("word/_rels/document.xml.rels").iter(_PR + "Relationship"):
                self.rels[r.get("Id") or ""] = (r.get("Target") or "", r.get("TargetMode") or "")
        self.stile = self._stile()
        self.zitate = self._zitat_stile()
        self.listen = self._nummern()

    def _zitat_stile(self) -> set[str]:
        """Stil-IDs für Zitate (Word: Quote / Intense Quote, deutsch: Zitat)."""
        raus = set()
        if not self.p.hat("word/styles.xml"):
            return raus
        for s in self.p.xml("word/styles.xml").iter(_W + "style"):
            name_e = s.find(_W + "name")
            name = ((name_e.get(_W + "val") if name_e is not None else "") or "").lower().replace(" ", "")
            if name in ("quote", "intensequote", "zitat", "intensiveszitat", "blocktext"):
                raus.add(s.get(_W + "styleId") or "")
        return raus

    def _stile(self) -> dict[str, int]:
        """Stil-ID -> Überschrift-Stufe (über Name oder Gliederungsebene)."""
        stufen: dict[str, int] = {}
        if not self.p.hat("word/styles.xml"):
            return stufen
        for s in self.p.xml("word/styles.xml").iter(_W + "style"):
            sid = s.get(_W + "styleId") or ""
            name_e = s.find(_W + "name")
            name = (name_e.get(_W + "val") if name_e is not None else "") or ""
            n = name.lower().replace(" ", "")
            stufe = 0
            if n == "title":
                stufe = 1
            for pre in ("heading", "überschrift", "berschrift"):
                if n.startswith(pre) and n[len(pre):].isdigit():
                    stufe = int(n[len(pre):])
            gl = s.find(f"{_W}pPr/{_W}outlineLvl")
            if not stufe and gl is not None and (gl.get(_W + "val") or "").isdigit():
                stufe = int(gl.get(_W + "val")) + 1
            if 1 <= stufe <= 6:
                stufen[sid] = stufe
        return stufen

    def _nummern(self) -> dict[tuple[str, int], str]:
        """(numId, Ebene) -> "punkt" | "zahl"."""
        art: dict[tuple[str, int], str] = {}
        if not self.p.hat("word/numbering.xml"):
            return art
        wurzel = self.p.xml("word/numbering.xml")
        abstrakt: dict[str, dict[int, str]] = {}
        for an in wurzel.iter(_W + "abstractNum"):
            ebenen = {}
            for lvl in an.iter(_W + "lvl"):
                fmt = lvl.find(_W + "numFmt")
                wert = (fmt.get(_W + "val") if fmt is not None else "") or ""
                try:
                    ebenen[int(lvl.get(_W + "ilvl") or 0)] = "punkt" if wert in ("bullet", "none", "") else "zahl"
                except ValueError:
                    continue
            abstrakt[an.get(_W + "abstractNumId") or ""] = ebenen
        for num in wurzel.iter(_W + "num"):
            ref = num.find(_W + "abstractNumId")
            ebenen = abstrakt.get((ref.get(_W + "val") if ref is not None else "") or "", {})
            for ebene, a in ebenen.items():
                art[(num.get(_W + "numId") or "", ebene)] = a
        return art

    def dokument(self) -> Dokument:
        wurzel = self.p.xml("word/document.xml")
        koerper = wurzel.find(_W + "body")
        dok = Dokument()
        hg = wurzel.find(_W + "background")
        if hg is not None:
            dok.papier = farbe_sauber(hg.get(_W + "color"))
        if koerper is not None:
            self._bloecke(koerper, dok.bloecke)
        return dok

    def _bloecke(self, eltern, ziel: list) -> None:
        for k in eltern:
            if k.tag == _W + "p":
                self._absatz(k, ziel)
            elif k.tag == _W + "tbl":
                ziel.append(self._tabelle(k))
            elif k.tag in (_W + "sdt", _W + "customXml"):
                inhalt = k.find(_W + "sdtContent")
                self._bloecke(inhalt if inhalt is not None else k, ziel)

    def _tabelle(self, tbl) -> Tabelle:
        t = Tabelle()
        for tr in tbl.findall(_W + "tr"):
            zeile = []
            for tc in tr.findall(_W + "tc"):
                inhalt: list = []
                self._bloecke(tc, inhalt)
                zelle = []
                for b in inhalt:                      # verschachtelte Tabellen: als Text-Zeilen
                    if isinstance(b, Absatz):
                        zelle.append(b)
                    elif isinstance(b, Tabelle):
                        for z in b.zeilen:
                            zelle.append(Absatz([Lauf(" | ".join(" ".join(a.text for a in c) for c in z))]))
                zeile.append(zelle)
            t.zeilen.append(zeile)
        return t

    def _absatz(self, p, ziel: list) -> None:
        a = Absatz()
        bilder: list[Bild] = []
        ppr = p.find(_W + "pPr")
        if ppr is not None:
            st = ppr.find(_W + "pStyle")
            if st is not None:
                a.stufe = self.stile.get(st.get(_W + "val") or "", 0)
                a.zitat = (st.get(_W + "val") or "") in self.zitate
            gl = ppr.find(_W + "outlineLvl")
            if not a.stufe and gl is not None and (gl.get(_W + "val") or "").isdigit():
                a.stufe = min(6, int(gl.get(_W + "val")) + 1)
            jc = ppr.find(_W + "jc")
            if jc is not None:
                a.ausrichtung = _AUS.get(jc.get(_W + "val") or "", "")
            num = ppr.find(_W + "numPr")
            if num is not None:
                ilvl, nid = num.find(_W + "ilvl"), num.find(_W + "numId")
                try:
                    ebene = int(ilvl.get(_W + "val")) if ilvl is not None else 0
                except (TypeError, ValueError):
                    ebene = 0
                numid = (nid.get(_W + "val") if nid is not None else "") or ""
                if numid and numid != "0":
                    a.liste = self.listen.get((numid, ebene), "punkt")
                    a.ebene = max(0, min(8, ebene))
        self._laeufe(p, a, bilder, "")
        rand = ppr.find(f"{_W}pBdr/{_W}bottom") if ppr is not None else None
        if rand is not None and not a.text.strip() and not bilder:     # leerer Absatz mit Unterkante = Linie
            ziel.append(Linie())
            return
        if a.laeufe or not bilder:
            ziel.append(aufgabe_aus_text(a.zusammenfassen()))
        ziel.extend(bilder)

    def _laeufe(self, eltern, a: Absatz, bilder: list, link: str) -> None:
        for k in eltern:
            if k.tag == _W + "r":
                self._lauf(k, a, bilder, link)
            elif k.tag == _W + "hyperlink":
                ziel = self.rels.get(k.get(_R + "id") or "", ("", ""))[0]
                self._laeufe(k, a, bilder, ziel)
            elif k.tag in (_W + "ins", _W + "smartTag", _W + "fldSimple", _W + "customXml"):
                self._laeufe(k, a, bilder, link)
            elif k.tag == _W + "sdt":
                inhalt = k.find(_W + "sdtContent")
                if inhalt is not None:
                    self._laeufe(inhalt, a, bilder, link)

    def _lauf(self, r, a: Absatz, bilder: list, link: str) -> None:
        rpr = r.find(_W + "rPr")
        f = {"fett": False, "kursiv": False, "unter": False, "durch": False, "farbe": "", "marker": "", "code": False}
        if rpr is not None:
            fonts = rpr.find(_W + "rFonts")
            if fonts is not None:
                f["code"] = (fonts.get(_W + "ascii") or fonts.get(_W + "hAnsi") or "").lower() in MONO
            f["fett"] = _an(rpr.find(_W + "b"))
            f["kursiv"] = _an(rpr.find(_W + "i"))
            u = rpr.find(_W + "u")
            f["unter"] = u is not None and (u.get(_W + "val") or "single") != "none"
            f["durch"] = _an(rpr.find(_W + "strike")) or _an(rpr.find(_W + "dstrike"))
            farbe = rpr.find(_W + "color")
            if farbe is not None:
                f["farbe"] = farbe_sauber(farbe.get(_W + "val"))
            shd, hl = rpr.find(_W + "shd"), rpr.find(_W + "highlight")
            if shd is not None:
                f["marker"] = farbe_sauber(shd.get(_W + "fill"))
            if not f["marker"] and hl is not None:
                f["marker"] = MARKER_FARBEN.get((hl.get(_W + "val") or "").lower(), "")
            if f["code"] and f["marker"] == "#" + CODE_HG.lower():
                f["marker"] = ""                          # Grau hinter Code gehört zum Code, nicht zum Marker
        for e in r:
            if e.tag == _W + "t":
                a.laeufe.append(Lauf(e.text or "", link=link, **f))
            elif e.tag == _W + "tab":
                a.laeufe.append(Lauf("\t", link=link, **f))
            elif e.tag in (_W + "br", _W + "cr"):
                a.laeufe.append(Lauf("\n", link=link, **f))
            elif e.tag in (_W + "drawing", _W + "pict", _W + "object"):
                self._bild(e, bilder)

    def _bild(self, e, bilder: list) -> None:
        breite = hoehe = 0.0
        ext = next(e.iter(_WP + "extent"), None)
        if ext is not None:
            try:
                breite, hoehe = int(ext.get("cx") or 0) / EMU_JE_PT, int(ext.get("cy") or 0) / EMU_JE_PT
            except ValueError:
                pass
        rid = ""
        blip = next(e.iter(_A + "blip"), None)
        if blip is not None:
            rid = blip.get(_R + "embed") or ""
        else:
            vml = next(e.iter(_V + "imagedata"), None)
            if vml is not None:
                rid = vml.get(_R + "id") or ""
        ziel, modus = self.rels.get(rid, ("", ""))
        if not ziel or modus.lower() == "external":           # nie nachladen
            return
        pfad = ziel.lstrip("/") if ziel.startswith("/") else posixpath.normpath(posixpath.join("word", ziel))
        try:
            daten = self.p.roh(pfad, MAX_BILD)
        except FormatFehler:
            return
        art = bild_endung(daten)
        if art:
            bilder.append(Bild(daten, art, breite, hoehe))


def lesen(pfad) -> Dokument:
    with Paket(pfad) as p:
        if not p.hat("word/document.xml"):
            raise FormatFehler("Kein Word-Dokument (word/document.xml fehlt)")
        return _Leser(p).dokument()


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

_TYPEN = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
{bilder}<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>"""
# Word zeigt die Seitenfarbe (w:background) nur mit displayBackgroundShape
_EINSTELLUNGEN = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="{W}">'
                  '<w:displayBackgroundShape/><w:defaultTabStop w:val="708"/></w:settings>')

_PAKET_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PR}">
<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _stile_xml() -> str:
    teile = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">',
             '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
             '<w:sz w:val="22"/><w:lang w:val="de-DE"/></w:rPr></w:rPrDefault>'
             '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
             '</w:docDefaults>',
             '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>',
             '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
             '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720"/></w:pPr></w:style>',
             '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
             '<w:qFormat/><w:pPr><w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="8B5CF6"/></w:pBdr>'
             '<w:ind w:left="567"/></w:pPr><w:rPr><w:i/><w:color w:val="595959"/></w:rPr></w:style>']
    groessen = {1: 32, 2: 26, 3: 24, 4: 22, 5: 22, 6: 22}
    for n in range(1, 7):
        teile.append(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
                     f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
                     f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="{n - 1}"/></w:pPr>'
                     f'<w:rPr><w:b/><w:sz w:val="{groessen[n]}"/></w:rPr></w:style>')
    teile.append('<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
                 + "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                           for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
                 + '</w:tblBorders></w:tblPr></w:style></w:styles>')
    return "".join(teile)


def _nummern_xml(zahl_listen: int) -> str:
    punkte = "•◦▪"
    lvl_p = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                    f'<w:lvlText w:val="{punkte[i % 3]}"/><w:lvlJc w:val="left"/>'
                    f'<w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>' for i in range(9))
    fmt = ("decimal", "lowerLetter", "lowerRoman")
    lvl_z = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt[i % 3]}"/>'
                    f'<w:lvlText w:val="%{i + 1}."/><w:lvlJc w:val="left"/>'
                    f'<w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>' for i in range(9))
    nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>']
    for k in range(zahl_listen):                     # jede Nummernliste beginnt neu bei 1
        nums.append(f'<w:num w:numId="{k + 2}"><w:abstractNumId w:val="1"/>'
                    '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>')
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="{W}">'
            f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{lvl_p}</w:abstractNum>'
            f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{lvl_z}</w:abstractNum>'
            + "".join(nums) + "</w:numbering>")


def _text_xml(text: str) -> str:
    """Text eines Laufs: Tabs und Umbrüche als eigene Elemente, Steuerzeichen raus (XML 1.0)."""
    teile = []
    puffer = []
    for ch in text:
        if ch == "\t" or ch == "\n":
            if puffer:
                teile.append(f'<w:t xml:space="preserve">{escape("".join(puffer))}</w:t>')
                puffer = []
            teile.append("<w:tab/>" if ch == "\t" else "<w:br/>")
        elif ch >= " " or ch == "\r":
            if ch != "\r":
                puffer.append(ch)
    if puffer:
        teile.append(f'<w:t xml:space="preserve">{escape("".join(puffer))}</w:t>')
    return "".join(teile)


class _Schreiber:
    def __init__(self):
        self.rels: list[str] = []
        self.medien: list[tuple[str, bytes]] = []
        self.zahl_listen = 0
        self._zahl_aktiv = 0
        self._id = 3                                  # rId1 = styles, rId2 = numbering, rId3 = settings

    def _rid(self) -> str:
        self._id += 1
        return f"rId{self._id}"

    def lauf(self, l: Lauf) -> str:
        # Reihenfolge wie im Schema verlangt: rFonts, b, i, strike, color, u, shd
        farbe = farbe_sauber(l.farbe) or ("#0563c1" if l.link else "")
        marker = farbe_sauber(l.marker) or ("#" + CODE_HG if l.code else "")
        rpr = ('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>' if l.code else "") + \
              ("<w:b/>" if l.fett else "") + ("<w:i/>" if l.kursiv else "") + ("<w:strike/>" if l.durch else "") + \
              (f'<w:color w:val="{farbe[1:].upper()}"/>' if farbe else "") + \
              ('<w:u w:val="single"/>' if l.unter or l.link else "") + \
              (f'<w:shd w:val="clear" w:color="auto" w:fill="{marker[1:].upper()}"/>' if marker else "")
        inhalt = f"<w:r>{f'<w:rPr>{rpr}</w:rPr>' if rpr else ''}{_text_xml(l.text)}</w:r>"
        if l.link and l.link.lower().startswith(("https://", "http://", "mailto:")):
            rid = self._rid()
            self.rels.append(f'<Relationship Id="{rid}" Type="{R}/hyperlink" Target={quoteattr(l.link)} '
                             'TargetMode="External"/>')
            return f'<w:hyperlink r:id="{rid}">{inhalt}</w:hyperlink>'
        return inhalt

    def absatz(self, a: Absatz, in_liste_vorher: bool) -> str:
        ppr = ""
        aufgabe = a.aufgabe in AUFGABE_ZEICHEN        # Checkbox: eingerückt mit ☐/☑ statt Aufzählungszeichen
        if a.stufe:
            ppr += f'<w:pStyle w:val="Heading{min(6, a.stufe)}"/>'
        elif a.liste:
            ppr += '<w:pStyle w:val="ListParagraph"/>'
        elif a.zitat:
            ppr += '<w:pStyle w:val="Quote"/>'
        if aufgabe:
            ppr += f'<w:ind w:left="{720 * (a.ebene + 1)}"/>'
        elif a.liste:
            if a.liste == "zahl":
                if not (in_liste_vorher and self._zahl_aktiv):
                    self.zahl_listen += 1
                    self._zahl_aktiv = self.zahl_listen + 1
                numid = self._zahl_aktiv
            else:
                numid = 1
            ppr += f'<w:numPr><w:ilvl w:val="{max(0, min(8, a.ebene))}"/><w:numId w:val="{numid}"/></w:numPr>'
        if a.ausrichtung in _EIN:
            ppr += f'<w:jc w:val="{_EIN[a.ausrichtung]}"/>'
        laeufe = "".join(self.lauf(l) for l in a.laeufe)
        if aufgabe:
            laeufe = self.lauf(Lauf(AUFGABE_ZEICHEN[a.aufgabe] + " ")) + laeufe
        return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{laeufe}</w:p>"

    def bild(self, b: Bild) -> str:
        if b.endung not in BILD_ENDUNGEN:
            return ""
        nr = len(self.medien) + 1
        name = f"bild{nr}.{'jpeg' if b.endung == 'jpeg' else b.endung}"
        self.medien.append((name, b.daten))
        rid = self._rid()
        self.rels.append(f'<Relationship Id="{rid}" Type="{R}/image" Target="media/{name}"/>')
        breite, hoehe = bild_masse_pt(b)
        cx, cy = round(breite * EMU_JE_PT), round(hoehe * EMU_JE_PT)
        return (f'<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{nr}" name="Bild {nr}"/>'
                f'<a:graphic xmlns:a="{A}"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<pic:nvPicPr><pic:cNvPr id="{nr}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                f'</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

    def tabelle(self, t: Tabelle) -> str:
        spalten = max(1, t.spalten)
        breite = 9000 // spalten
        raster = "".join(f'<w:gridCol w:w="{breite}"/>' for _ in range(spalten))
        zeilen = []
        for z in t.zeilen:
            zellen = []
            for i in range(spalten):
                absaetze = z[i] if i < len(z) else []
                inhalt = "".join(self.absatz(a, False) for a in absaetze) or "<w:p/>"
                zellen.append(f'<w:tc><w:tcPr><w:tcW w:w="{breite}" w:type="dxa"/></w:tcPr>{inhalt}</w:tc>')
            zeilen.append("<w:tr>" + "".join(zellen) + "</w:tr>")
        return (f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
                f'<w:tblGrid>{raster}</w:tblGrid>{"".join(zeilen)}</w:tbl>')

    def koerper(self, dok: Dokument) -> str:
        teile = []
        vorher_liste = False
        for b in dok.bloecke:
            if isinstance(b, Absatz):
                teile.append(self.absatz(b, vorher_liste))
                vorher_liste = bool(b.liste)
                if not b.liste:
                    self._zahl_aktiv = 0
                continue
            vorher_liste = False
            self._zahl_aktiv = 0
            if isinstance(b, Linie):
                teile.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="A6A6A6"/>'
                             '</w:pBdr></w:pPr></w:p>')
            else:
                teile.append(self.tabelle(b) if isinstance(b, Tabelle) else self.bild(b))
        teile.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                     '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" '
                     'w:footer="708" w:gutter="0"/></w:sectPr>')
        papier = farbe_sauber(dok.papier)
        hg = f'<w:background w:color="{papier[1:].upper()}"/>' if papier else ""
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}">{hg}<w:body>{"".join(teile)}</w:body>'
                f'</w:document>')


def schreiben_bytes(dok: Dokument) -> bytes:
    s = _Schreiber()
    koerper = s.koerper(dok)
    rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PR}">'
            f'<Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId2" Type="{R}/numbering" Target="numbering.xml"/>'
            f'<Relationship Id="rId3" Type="{R}/settings" Target="settings.xml"/>'
            + "".join(s.rels) + "</Relationships>")
    endungen = sorted({n.rsplit(".", 1)[1] for n, _ in s.medien})
    bilder = "".join(f'<Default Extension="{e}" ContentType="{BILD_ENDUNGEN[e]}"/>' for e in endungen)
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _TYPEN.format(bilder=bilder))
        z.writestr("_rels/.rels", _PAKET_RELS)
        z.writestr("word/document.xml", koerper)
        z.writestr("word/styles.xml", _stile_xml())
        z.writestr("word/numbering.xml", _nummern_xml(s.zahl_listen))
        z.writestr("word/settings.xml", _EINSTELLUNGEN)
        z.writestr("word/_rels/document.xml.rels", rels)
        for name, daten in s.medien:
            z.writestr(f"word/media/{name}", daten)
    return puffer.getvalue()
