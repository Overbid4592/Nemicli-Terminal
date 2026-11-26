"""tabformate.py – Tabellen lesen und schreiben: .ods (OpenDocument), .xlsx (Excel), .csv/.tsv, Markdown, HTML.

Formeln bleiben in .ods/.xlsx erhalten (übersetzt in die jeweilige Schreibweise, mit gespeichertem Ergebnis).
CSV, TSV, Markdown und HTML bekommen die angezeigten Werte. Beim Lesen von CSV wird nichts als Formel gedeutet.
"""
from __future__ import annotations

import csv
import html
import io
import posixpath
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from buero import formel as F
from buero.sicher import FormatFehler, Paket, datei_lesen
from buero.tabelle import (Blatt, Mappe, _bezuege_aendern, aus_markdown, format_sauber, ist_formel,
                           wert_aus_eingabe)
from buero.textmodell import farbe_sauber

MAX_WIEDERHOLUNG = 10_000          # wiederholte, gefüllte Zellen/Zeilen in .ods
MAX_FARB_FLAECHE = 2_000           # leere, eingefärbte Zellen aus einer Wiederholung (sonst ganze Spalten)
MAX_ZEILE_LESEN = 1_048_576


def _text_eingabe(t: str) -> str:
    """Text, der als Zahl, Formel oder Wahrheitswert gedeutet würde, bleibt mit ' davor Text."""
    if t and (t.startswith(("=", "'")) or wert_aus_eingabe(t) != t):
        return "'" + t
    return t


def _zahl_eingabe(v: float) -> str:
    return F.zahl_text(v)


# ---------------------------------------------------------------------------
# CSV / TSV / Markdown / HTML
# ---------------------------------------------------------------------------

def _decodieren(roh: bytes) -> str:
    if roh.startswith(b"\xef\xbb\xbf"):
        return roh[3:].decode("utf-8", errors="replace")
    if roh.startswith((b"\xff\xfe", b"\xfe\xff")):
        return roh.decode("utf-16", errors="replace")
    try:
        return roh.decode("utf-8")
    except UnicodeDecodeError:
        return roh.decode("cp1252", errors="replace")


def csv_lesen(pfad, trenner: str | None = None) -> Mappe:
    text = _decodieren(datei_lesen(pfad))
    if trenner is None:
        probe = text[:20000]
        try:
            trenner = csv.Sniffer().sniff(probe, delimiters=";,\t|").delimiter
        except csv.Error:
            trenner = max(";,\t", key=probe.count)
    m = Mappe([Blatt(re.sub(r"[\\/:*?\[\]!']", "_", Path(pfad).stem)[:31] or "Tabelle1")])
    b = m.blaetter[0]
    for z, zeile in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=trenner)):
        for s, wert in enumerate(zeile):
            if wert != "":
                m.setzen(b, z, s, "'" + wert if wert.startswith(("=", "'")) else wert)
    return m


def _zellen_text(m: Mappe, b: Blatt) -> list[list[str]]:
    zeilen, spalten = b.groesse()
    return [[m.anzeige(b, z, s) for s in range(spalten)] for z in range(zeilen)]


def _csv_sicher(m: Mappe, b: Blatt, z: int, s: int) -> str:
    """Angezeigter Wert; Text, der in anderen Programmen als Formel liefe, bekommt ein ' davor."""
    t = m.anzeige(b, z, s)
    if t[:1] in ("=", "+", "-", "@", "\t", "\r") and not isinstance(m.wert(b, z, s), float):
        return "'" + t
    return t


def csv_bytes(m: Mappe, b: Blatt, trenner: str = ";") -> bytes:
    zeilen, spalten = b.groesse()
    puffer = io.StringIO(newline="")
    w = csv.writer(puffer, delimiter=trenner, lineterminator="\r\n")
    for z in range(zeilen):
        w.writerow([_csv_sicher(m, b, z, s) for s in range(spalten)])
    return b"\xef\xbb\xbf" + puffer.getvalue().encode("utf-8")      # BOM: Excel erkennt UTF-8


def markdown_lesen(pfad) -> Mappe:
    zeilen = aus_markdown(_decodieren(datei_lesen(pfad)))
    if not zeilen:
        raise FormatFehler("Keine Markdown-Tabelle gefunden")
    m = Mappe()
    b = m.blaetter[0]
    for z, zeile in enumerate(zeilen):
        for s, wert in enumerate(zeile):
            if wert:
                m.setzen(b, z, s, "'" + wert if wert.startswith(("=", "'")) else wert)
    return m


def markdown_text(m: Mappe, b: Blatt) -> str:
    return m.als_markdown(b, max_zeilen=10**9, max_spalten=10**6, adressen=False) + "\n"


def html_text(m: Mappe) -> str:
    teile = ['<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Tabelle</title><style>'
             "body{font-family:Segoe UI,Arial,sans-serif;margin:24px}table{border-collapse:collapse;margin-bottom:24px}"
             "td,th{border:1px solid #999;padding:4px 8px}td.z{text-align:right}</style></head><body>"]
    for b in m.blaetter:
        teile.append(f"<h2>{html.escape(b.name)}</h2><table>")
        zeilen, spalten = b.groesse()
        for z in range(zeilen):
            zellen = []
            for s in range(spalten):
                v = m.wert(b, z, s)
                klasse = ' class="z"' if isinstance(v, float) and not isinstance(v, bool) else ""
                f = m.format(b, z, s)
                stil = (f"background:{f['hg']};" if "hg" in f else "") + (f"color:{f['fg']};" if "fg" in f else "") + \
                       ("font-weight:bold;" if f.get("fett") else "")
                stil = f' style="{stil}"' if stil else ""
                zellen.append(f"<td{klasse}{stil}>{html.escape(m.anzeige(b, z, s))}</td>")
            teile.append("<tr>" + "".join(zellen) + "</tr>")
        teile.append("</table>")
    teile.append("</body></html>")
    return "".join(teile)


# ---------------------------------------------------------------------------
# OpenDocument (.ods)
# ---------------------------------------------------------------------------

NS_ODS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "of": "urn:oasis:names:tc:opendocument:xmlns:of:1.2",
}
_O, _T, _TX = (f"{{{NS_ODS[k]}}}" for k in ("office", "table", "text"))
MIME_ODS = "application/vnd.oasis.opendocument.spreadsheet"


def _ods_text(zelle) -> str:
    teile = []
    for p in zelle.findall(_TX + "p"):
        teile.append(_absatz_text(p))
    return "\n".join(teile)


def _absatz_text(e) -> str:
    teile = [e.text or ""]
    for k in e:
        if k.tag == _TX + "s":
            try:
                teile.append(" " * max(1, min(1000, int(k.get(_TX + "c") or 1))))
            except ValueError:
                teile.append(" ")
        elif k.tag == _TX + "tab":
            teile.append("\t")
        elif k.tag == _TX + "line-break":
            teile.append("\n")
        elif k.tag not in (f"{{{NS_ODS['office']}}}annotation",):
            teile.append(_absatz_text(k))
        teile.append(k.tail or "")
    return "".join(teile)


def _ods_eingabe(c) -> str | None:
    formel = c.get(_T + "formula")
    if formel:
        return F.deutsch(formel)
    art = c.get(_O + "value-type")
    text = _ods_text(c)
    if art in ("float", "percentage", "currency"):
        try:
            v = float(c.get(_O + "value") or "")
        except ValueError:
            return text or None
        if text and F.zahl_aus_text(text) is not None and abs(F.zahl_aus_text(text) - v) < 1e-9 * max(1, abs(v)):
            return text                                      # „12,50 €“, „12 %“ so lassen, wie angezeigt
        return _zahl_eingabe(v)
    if art == "date":
        d = (c.get(_O + "date-value") or "")[:10]
        if (dt := F.datum_aus_text(d)) is not None:
            return F.zahl_text(dt)
        return text or None
    if art == "boolean":
        return "WAHR" if (c.get(_O + "boolean-value") or "").lower() == "true" else "FALSCH"
    if art == "time":
        return text or None
    if text:
        return _text_eingabe(text)
    return None


_ST, _FO = f"{{{NS_ODS['style']}}}", f"{{{NS_ODS['fo']}}}"


def _ods_zellstile(wurzel) -> dict[str, dict]:
    """Automatische Zellstile: Name -> {hg, fg, fett}."""
    raus = {}
    for st in wurzel.iter(_ST + "style"):
        if st.get(_ST + "family") != "table-cell":
            continue
        f = {}
        zp, tp = st.find(_ST + "table-cell-properties"), st.find(_ST + "text-properties")
        if zp is not None:
            f["hg"] = zp.get(_FO + "background-color")
        if tp is not None:
            f["fg"] = tp.get(_FO + "color")
            f["fett"] = (tp.get(_FO + "font-weight") or "") in ("bold", "600", "700", "800", "900")
        if (f := format_sauber(f)):
            raus[st.get(_ST + "name") or ""] = f
    return raus


def ods_lesen(pfad) -> Mappe:
    with Paket(pfad) as p:
        if not p.hat("content.xml"):
            raise FormatFehler("Kein OpenDocument (content.xml fehlt)")
        wurzel = p.xml("content.xml")
    tabellen = wurzel.find(f"{_O}body/{_O}spreadsheet")
    if tabellen is None:
        raise FormatFehler("Keine Tabelle (office:spreadsheet fehlt)")
    zellstile = _ods_zellstile(wurzel)
    m = Mappe([])
    for tbl in tabellen.findall(_T + "table"):
        name = tbl.get(_T + "name") or m.neuer_name()
        while m.blatt(name):
            name += "_2"
        b = m.blatt_dazu(name)
        z = 0
        gefuellt = 0
        for zeile in _ods_zeilen(tbl):
            try:
                wdh_z = max(1, int(zeile.get(_T + "number-rows-repeated") or 1))
            except ValueError:
                wdh_z = 1
            zellen = []
            s = 0
            for c in zeile:
                if c.tag not in (_T + "table-cell", _T + "covered-table-cell"):
                    continue
                try:
                    wdh_s = max(1, int(c.get(_T + "number-columns-repeated") or 1))
                except ValueError:
                    wdh_s = 1
                roh = _ods_eingabe(c)
                fmt = zellstile.get(c.get(_T + "style-name") or "")
                if fmt and roh is None and wdh_s * wdh_z > MAX_FARB_FLAECHE:
                    fmt = None                       # ganze Spalten/Zeilen leer eingefärbt: nicht ausrollen
                if roh is not None or fmt:
                    for k in range(min(wdh_s, MAX_WIEDERHOLUNG)):
                        if s + k < F.MAX_SPALTE:
                            zellen.append((s + k, roh, fmt))
                s += wdh_s
            if zellen:
                for k in range(min(wdh_z, MAX_WIEDERHOLUNG)):
                    if z + k >= MAX_ZEILE_LESEN:
                        break
                    for s_, roh, fmt in zellen:
                        if roh is not None:
                            m.setzen(b, z + k, s_, roh)
                        if fmt:
                            m.format_setzen(b, z + k, s_, fmt)
                    gefuellt += 1
                    if gefuellt > MAX_WIEDERHOLUNG * 50:
                        raise FormatFehler("Tabelle hat zu viele gefüllte Zeilen")
            z += wdh_z
    if not m.blaetter:
        m.blatt_dazu("Tabelle1")
    m.berechnen()
    return m


def _ods_zeilen(tbl):
    for k in tbl:
        if k.tag == _T + "table-row":
            yield k
        elif k.tag in (_T + "table-header-rows", _T + "table-rows", _T + "table-row-group"):
            yield from _ods_zeilen(k)


class _OdsStile:
    """Automatische Zellstile beim Schreiben: (Datum, hg, fg, fett) -> Name."""

    def __init__(self):
        self.namen: dict[tuple, str] = {}

    def name(self, datum: bool, f: dict) -> str:
        schluessel = (datum, f.get("hg", ""), f.get("fg", ""), bool(f.get("fett")))
        if schluessel == (False, "", "", False):
            return ""
        if schluessel not in self.namen:
            self.namen[schluessel] = f"ce{len(self.namen) + 1}"
        return self.namen[schluessel]

    def xml(self) -> str:
        teile = []
        for (datum, hg, fg, fett), name in self.namen.items():
            daten = ' style:data-style-name="NDatum"' if datum else ""
            innen = f'<style:table-cell-properties fo:background-color="{hg}"/>' if hg else ""
            tp = (f' fo:color="{fg}"' if fg else "") + \
                 (' fo:font-weight="bold" style:font-weight-asian="bold" style:font-weight-complex="bold"' if fett else "")
            innen += f"<style:text-properties{tp}/>" if tp else ""
            teile.append(f'<style:style style:name="{name}" style:family="table-cell" style:parent-style-name="Default"'
                         f"{daten}>{innen}</style:style>")
        return "".join(teile)


def _ods_zelle(m: Mappe, b: Blatt, z: int, s: int, stile: _OdsStile) -> str:
    roh = m.roh(b, z, s)
    v = m.wert(b, z, s) if roh else None
    datum = isinstance(v, F.Datum) and v.datum() is not None
    stil = stile.name(datum, m.format(b, z, s))
    attr = f' table:style-name="{stil}"' if stil else ""
    if not roh:
        return f"<table:table-cell{attr}/>"
    if ist_formel(roh):
        attr += f" table:formula={quoteattr(F.uebersetzen(roh, 'ods'))}"
    text = m.anzeige(b, z, s)
    if isinstance(v, bool):
        attr += f' office:value-type="boolean" office:boolean-value="{"true" if v else "false"}"'
    elif datum:
        attr += f' office:value-type="date" office:date-value="{v.datum().isoformat()}"'
    elif isinstance(v, float):
        attr += f' office:value-type="float" office:value="{repr(v)}"'
    elif isinstance(v, F.Fehler):
        attr += ' office:value-type="string" office:string-value=""'
    else:
        attr += ' office:value-type="string"'
    absaetze = "".join(f"<text:p>{escape(_xml_sauber(t))}</text:p>" for t in text.split("\n")) if text else ""
    return f"<table:table-cell{attr}>{absaetze}</table:table-cell>"


def ods_bytes(m: Mappe) -> bytes:
    m.berechnen()
    ns = " ".join(f'xmlns:{k}="{v}"' for k, v in NS_ODS.items())
    stile = _OdsStile()
    teile = []
    for b in m.blaetter:
        zeilen, spalten = b.groesse()
        teile.append(f"<table:table table:name={quoteattr(b.name)}>"
                     f'<table:table-column table:number-columns-repeated="{max(1, spalten)}"/>')
        z = 0
        leer = 0
        nach_zeile: dict[int, set[int]] = {}
        for (zz, ss) in [*b.zellen, *b.formate]:
            nach_zeile.setdefault(zz, set()).add(ss)
        for z in range(zeilen):
            if z not in nach_zeile:
                leer += 1
                continue
            if leer:
                teile.append(f'<table:table-row table:number-rows-repeated="{leer}"><table:table-cell/></table:table-row>')
                leer = 0
            zellen, s_vorher = [], -1
            for s in sorted(nach_zeile[z]):
                if s - s_vorher > 1:
                    zellen.append(f'<table:table-cell table:number-columns-repeated="{s - s_vorher - 1}"/>')
                zellen.append(_ods_zelle(m, b, z, s, stile))
                s_vorher = s
            teile.append("<table:table-row>" + "".join(zellen) + "</table:table-row>")
        if zeilen == 0:
            teile.append("<table:table-row><table:table-cell/></table:table-row>")
        teile.append("</table:table>")
    teile.insert(0, f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ns} office:version="1.3">'
                    '<office:automatic-styles>'
                    '<number:date-style style:name="NDatum"><number:day number:style="long"/><number:text>.'
                    '</number:text><number:month number:style="long"/><number:text>.</number:text>'
                    '<number:year number:style="long"/></number:date-style>'
                    + stile.xml() + '</office:automatic-styles><office:body><office:spreadsheet>')
    teile.append("</office:spreadsheet></office:body></office:document-content>")
    manifest = (f'<?xml version="1.0" encoding="UTF-8"?><manifest:manifest '
                f'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
                f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="{MIME_ODS}"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                "</manifest:manifest>")
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), MIME_ODS, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/manifest.xml", manifest)
        zf.writestr("content.xml", "".join(teile))
    return puffer.getvalue()


# ---------------------------------------------------------------------------
# Excel (.xlsx)
# ---------------------------------------------------------------------------

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_S, _R, _PR = f"{{{S}}}", f"{{{R}}}", f"{{{PR}}}"
_DATUM_IDS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}


def _ist_datumsformat(code: str) -> bool:
    ohne = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code.lower())
    return bool(re.search(r"[dy]", ohne)) or (bool(re.search(r"m", ohne)) and not re.search(r"[0#]", ohne))


def _si_text(si) -> str:
    teile = []
    for e in si.iter():
        if e.tag == _S + "t":
            teile.append(e.text or "")
        elif e.tag == _S + "rPh":                # Lesehilfen (Furigana) nicht mitnehmen
            break
    return "".join(teile)


def _verschieben_relativ(formel: str, dz: int, ds: int) -> str:
    """Geteilte Excel-Formel für eine andere Zelle: Bezüge ohne $ um (dz, ds) versetzen."""
    try:
        toks = F.zerlegen(formel)
    except F._Abbruch:
        return formel
    teile = []
    for t in toks:
        if t.art in ("ref", "bereich"):
            neu = []
            for st in t.roh.split(":"):
                m = re.fullmatch(r"(\$?)([A-Za-z]{1,3})(\$?)(\d{1,7})", st)
                if not m:
                    neu.append(st)
                    continue
                s = F.spalte_nr(m.group(2)) + (0 if m.group(1) else ds)
                z = int(m.group(4)) - 1 + (0 if m.group(3) else dz)
                if s < 0 or z < 0:
                    return "=#BEZUG!"
                neu.append(f"{m.group(1)}{F.spalte_name(s)}{m.group(3)}{z + 1}")
            teile.append(F._blatt_text(t.wert[0], "excel") + ":".join(neu))
        elif t.art == "zahl":
            teile.append((t.roh or repr(t.wert)).replace(",", "."))
        elif t.art == "text":
            teile.append('"' + str(t.wert).replace('"', '""') + '"')
        elif t.art == "bool":
            teile.append("TRUE" if t.wert else "FALSE")
        elif t.art == "sep":
            teile.append(",")
        elif t.art == "fn":
            teile.append(F._DE_EN.get(str(t.wert), str(t.wert)))
        else:
            teile.append(str(t.wert))
    return "".join(teile)


def xlsx_lesen(pfad) -> Mappe:
    with Paket(pfad) as p:
        if not p.hat("xl/workbook.xml"):
            raise FormatFehler("Keine Excel-Mappe (xl/workbook.xml fehlt)")
        wb = p.xml("xl/workbook.xml")
        rels = {}
        if p.hat("xl/_rels/workbook.xml.rels"):
            for r in p.xml("xl/_rels/workbook.xml.rels").iter(_PR + "Relationship"):
                rels[r.get("Id") or ""] = r.get("Target") or ""
        texte = [_si_text(si) for si in p.xml("xl/sharedStrings.xml").findall(_S + "si")] \
            if p.hat("xl/sharedStrings.xml") else []
        datum_xf: set[int] = set()
        xf_format: dict[int, dict] = {}
        if p.hat("xl/styles.xml"):
            st = p.xml("xl/styles.xml")
            xf_format = _xlsx_zellformate(st)
            eigene = {}
            fmts = st.find(_S + "numFmts")
            if fmts is not None:
                for nf in fmts.findall(_S + "numFmt"):
                    try:
                        eigene[int(nf.get("numFmtId") or -1)] = nf.get("formatCode") or ""
                    except ValueError:
                        pass
            xfs = st.find(_S + "cellXfs")
            if xfs is not None:
                for i, xf in enumerate(xfs.findall(_S + "xf")):
                    try:
                        fid = int(xf.get("numFmtId") or 0)
                    except ValueError:
                        continue
                    if fid in _DATUM_IDS or (fid in eigene and _ist_datumsformat(eigene[fid])):
                        datum_xf.add(i)
        m = Mappe([])
        blaetter = wb.find(_S + "sheets")
        for sh in (blaetter.findall(_S + "sheet") if blaetter is not None else []):
            name = sh.get("name") or m.neuer_name()
            ziel = rels.get(sh.get(_R + "id") or "", "")
            if not ziel:
                continue
            teil = ziel.lstrip("/") if ziel.startswith("/") else posixpath.normpath(posixpath.join("xl", ziel))
            if not p.hat(teil):
                continue
            while m.blatt(name):
                name += "_2"
            b = m.blatt_dazu(name)
            _xlsx_blatt(p.xml(teil), m, b, texte, datum_xf, xf_format)
    if not m.blaetter:
        m.blatt_dazu("Tabelle1")
    m.berechnen()
    return m


def _xlsx_farbe(e) -> str:
    """Farbe aus <color rgb="FFRRGGBB"/> bzw. <fgColor …/>; Theme- und Index-Farben bleiben Standard."""
    return farbe_sauber(e.get("rgb")) if e is not None else ""


def _xlsx_zellformate(st) -> dict[int, dict]:
    """Zellformat-Index (cellXfs) -> {hg, fg, fett}."""
    schriften, fuellungen = [], []
    for font in (st.find(_S + "fonts") if st.find(_S + "fonts") is not None else []):
        b = font.find(_S + "b")
        fett = b is not None and (b.get("val") or "1").lower() not in ("0", "false")
        schriften.append({"fg": _xlsx_farbe(font.find(_S + "color")), "fett": fett})
    for fill in (st.find(_S + "fills") if st.find(_S + "fills") is not None else []):
        pf = fill.find(_S + "patternFill")
        hg = ""
        if pf is not None and pf.get("patternType") == "solid":
            hg = _xlsx_farbe(pf.find(_S + "fgColor"))
        fuellungen.append(hg)
    raus = {}
    xfs = st.find(_S + "cellXfs")
    for i, xf in enumerate(xfs.findall(_S + "xf") if xfs is not None else []):
        try:
            fo, fi = int(xf.get("fontId") or 0), int(xf.get("fillId") or 0)
        except ValueError:
            continue
        f = {**(schriften[fo] if 0 <= fo < len(schriften) else {}),
             "hg": fuellungen[fi] if 0 <= fi < len(fuellungen) else ""}
        if (f := format_sauber(f)):
            raus[i] = f
    return raus


def _xlsx_blatt(wurzel, m: Mappe, b: Blatt, texte: list[str], datum_xf: set[int],
                xf_format: dict[int, dict] | None = None) -> None:
    geteilt: dict[str, tuple[str, int, int]] = {}
    daten = wurzel.find(_S + "sheetData")
    if daten is None:
        return
    for zeile in daten.findall(_S + "row"):
        for c in zeile.findall(_S + "c"):
            pos = F.zelle_aus(c.get("r") or "")
            if pos is None:
                continue
            z, s = pos
            if xf_format and (c.get("s") or "").isdigit() and (fmt := xf_format.get(int(c.get("s")))):
                m.format_setzen(b, z, s, fmt)
            art = c.get("t") or "n"
            v_e = c.find(_S + "v")
            v = v_e.text if v_e is not None and v_e.text is not None else None
            f_e = c.find(_S + "f")
            if f_e is not None:
                text = f_e.text or ""
                if f_e.get("t") == "shared" and (si := f_e.get("si")) is not None:
                    if text:
                        geteilt[si] = (text, z, s)
                    elif si in geteilt:
                        basis, bz, bs = geteilt[si]
                        text = _verschieben_relativ(basis, z - bz, s - bs)
                if text:
                    m.setzen(b, z, s, F.deutsch("=" + text))
                    continue
            if art == "s" and v is not None:
                try:
                    m.setzen(b, z, s, _text_eingabe(texte[int(v)]))
                except (ValueError, IndexError):
                    pass
            elif art == "inlineStr":
                is_ = c.find(_S + "is")
                if is_ is not None:
                    m.setzen(b, z, s, _text_eingabe(_si_text(is_)))
            elif art == "str" and v is not None:
                m.setzen(b, z, s, _text_eingabe(v))
            elif art == "b" and v is not None:
                m.setzen(b, z, s, "WAHR" if v.strip() == "1" else "FALSCH")
            elif art == "e" and v is not None:
                m.setzen(b, z, s, v)
            elif v is not None:
                try:
                    zahl = float(v)
                except ValueError:
                    continue
                try:
                    xf = int(c.get("s") or 0)
                except ValueError:
                    xf = 0
                if xf in datum_xf and 0 < zahl < 2_958_466:
                    m.setzen(b, z, s, F.zahl_text(F.Datum(int(zahl))))
                else:
                    m.setzen(b, z, s, _zahl_eingabe(zahl))


def _blattname_excel(name: str, vergeben: set[str]) -> str:
    n = re.sub(r"[\[\]:*?/\\]", "_", name).strip("'")[:31] or "Tabelle"
    basis, i = n, 2
    while n.lower() in vergeben:
        n = f"{basis[:28]}_{i}"
        i += 1
    vergeben.add(n.lower())
    return n


class _XlsxStile:
    """Schriften, Füllungen und Zellformate für styles.xml; Index 0 = Standard, 1 = Datum (TT.MM.JJJJ)."""

    def __init__(self):
        self.schriften: list[tuple[str, bool]] = [("", False)]
        self.fuellungen: list[str] = ["", "gray125"]          # Excel verlangt diese beiden zuerst
        self.formate: list[tuple[int, int, int]] = [(0, 0, 0), (14, 0, 0)]

    def index(self, datum: bool, f: dict) -> int:
        schrift = (f.get("fg", ""), bool(f.get("fett")))
        if schrift not in self.schriften:
            self.schriften.append(schrift)
        hg = f.get("hg", "")
        if hg and hg not in self.fuellungen:
            self.fuellungen.append(hg)
        schluessel = (14 if datum else 0, self.schriften.index(schrift), self.fuellungen.index(hg) if hg else 0)
        if schluessel not in self.formate:
            self.formate.append(schluessel)
        return self.formate.index(schluessel)

    def xml(self) -> str:
        schriften = "".join("<font>" + ("<b/>" if fett else "") + "<sz val=\"11\"/>"
                            + (f'<color rgb="FF{fg[1:].upper()}"/>' if fg else "") + '<name val="Calibri"/></font>'
                            for fg, fett in self.schriften)
        fuellungen = ('<fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                      + "".join(f'<fill><patternFill patternType="solid"><fgColor rgb="FF{hg[1:].upper()}"/>'
                                f'<bgColor indexed="64"/></patternFill></fill>' for hg in self.fuellungen[2:]))
        formate = "".join(f'<xf numFmtId="{n}" fontId="{fo}" fillId="{fi}" borderId="0" xfId="0"'
                          + (' applyNumberFormat="1"' if n else "") + (' applyFont="1"' if fo else "")
                          + (' applyFill="1"' if fi else "") + "/>" for n, fo, fi in self.formate)
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{S}">'
                f'<fonts count="{len(self.schriften)}">{schriften}</fonts>'
                f'<fills count="{len(self.fuellungen)}">{fuellungen}</fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                f'<cellXfs count="{len(self.formate)}">{formate}</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def xlsx_bytes(m: Mappe) -> bytes:
    m.berechnen()
    stile = _XlsxStile()
    vergeben: set[str] = set()
    namen = {b.name: _blattname_excel(b.name, vergeben) for b in m.blaetter}
    umbenannt = {alt.lower(): neu for alt, neu in namen.items() if alt != neu}

    def excel_formel(roh: str, blatt: str) -> str:
        if umbenannt:
            roh = _bezuege_aendern(roh, blatt, lambda bl, z, s: (umbenannt.get((bl or "").lower(), bl), z, s))
        return F.uebersetzen(roh, "excel")

    blaetter_xml = []
    for nr, b in enumerate(m.blaetter, 1):
        nach_zeile: dict[int, set[int]] = {}
        for (z, s) in [*b.zellen, *b.formate]:
            nach_zeile.setdefault(z, set()).add(s)
        zeilen = []
        for z in sorted(nach_zeile):
            zellen = []
            for s in sorted(nach_zeile[z]):
                ref = F.zelle_name(z, s)
                roh = m.roh(b, z, s)
                v = m.wert(b, z, s) if roh else None
                xf = stile.index(isinstance(v, F.Datum), m.format(b, z, s))
                st = f' s="{xf}"' if xf else ""
                if not roh:
                    zellen.append(f'<c r="{ref}"{st}/>')
                    continue
                f = f"<f>{escape(excel_formel(roh, b.name))}</f>" if ist_formel(roh) else ""
                if isinstance(v, bool):
                    zellen.append(f'<c r="{ref}"{st} t="b">{f}<v>{int(v)}</v></c>')
                elif isinstance(v, F.Fehler):
                    code = {"#WERT!": "#VALUE!", "#BEZUG!": "#REF!", "#ZAHL!": "#NUM!", "#NV": "#N/A",
                            "#ZIRKEL!": "#REF!"}.get(v, v)
                    zellen.append(f'<c r="{ref}"{st} t="e">{f}<v>{escape(code)}</v></c>')
                elif isinstance(v, F.Datum):
                    zellen.append(f'<c r="{ref}"{st}>{f}<v>{int(v)}</v></c>')
                elif isinstance(v, float):
                    zellen.append(f'<c r="{ref}"{st}>{f}<v>{repr(v)}</v></c>')
                elif f:
                    zellen.append(f'<c r="{ref}"{st} t="str">{f}<v>{escape(str(v or ""))}</v></c>')
                else:
                    t = str(v or "")
                    zellen.append(f'<c r="{ref}"{st} t="inlineStr"><is><t xml:space="preserve">'
                                  f"{escape(_xml_sauber(t))}</t></is></c>")
            zeilen.append(f'<row r="{z + 1}">' + "".join(zellen) + "</row>")
        blaetter_xml.append((nr, f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                 f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>{"".join(zeilen)}</sheetData>'
                                 f"</worksheet>"))
    wb = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
          + "".join(f'<sheet name={quoteattr(namen[b.name])} sheetId="{i}" r:id="rId{i}"/>'
                    for i, b in enumerate(m.blaetter, 1))
          + '</sheets><calcPr fullCalcOnLoad="1"/></workbook>')
    wb_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PR}">'
               + "".join(f'<Relationship Id="rId{i}" Type="{R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i, _ in blaetter_xml)
               + f'<Relationship Id="rId{len(blaetter_xml) + 1}" Type="{R}/styles" Target="styles.xml"/>'
               + "</Relationships>")
    styles = stile.xml()
    typen = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/styles.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                       'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                       for i, _ in blaetter_xml)
             + "</Types>")
    paket_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PR}">'
                  f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", typen)
        zf.writestr("_rels/.rels", paket_rels)
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", styles)
        for i, xml in blaetter_xml:
            zf.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return puffer.getvalue()


def _xml_sauber(t: str) -> str:
    return "".join(ch for ch in t if ch in "\t\n\r" or ch >= " ")
