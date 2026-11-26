"""rtf.py – Rich Text Format lesen und schreiben: Absätze, Fett/Kursiv/Unterstrichen/Durchgestrichen,
Ausrichtung, einfache Tabellen und Umlaute (\\'hh, \\uN). Bilder, Felder und Schriftwechsel werden übergangen."""
from __future__ import annotations

import re

from buero.sicher import FormatFehler
from buero.textmodell import AUFGABE_ZEICHEN, Absatz, Dokument, Lauf, Linie, Tabelle, aufgabe_aus_text, farbe_sauber

MAX_TIEFE = 200
# Ziele, deren Inhalt kein Fließtext ist
_UEBERGEHEN = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "headerl", "headerr",
    "headerf", "footerl", "footerr", "footerf", "footnote", "listtable", "listoverridetable", "revtbl", "rsidtbl",
    "generator", "xmlnstbl", "themedata", "colorschememapping", "datastore", "latentstyles", "fldinst", "filetbl",
    "pgdsctbl", "mmathPr", "wgrffmtfilter", "nonshppict", "shp", "shpinst", "sp", "comment", "annotation",
    "atnid", "atnauthor", "bkmkstart", "bkmkend", "pn", "pntext", "pntxta", "pntxtb", "listtext",
})
_TOKEN = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([\r\n]+)|([^\\{}\r\n]+)",
                    re.DOTALL)


def _ersatzpaare(a: Absatz) -> Absatz:
    """\\uN-Paare außerhalb der BMP (Emojis) zu einem Zeichen verbinden; einzelne Hälften werden zu �."""
    for l in a.laeufe:
        l.text = l.text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return a


def lesen(pfad) -> Dokument:
    from buero.sicher import datei_lesen
    return aus_bytes(datei_lesen(pfad))


def aus_bytes(roh: bytes) -> Dokument:
    if not roh.lstrip().startswith(b"{\\rtf"):
        raise FormatFehler("Keine RTF-Datei")
    m = re.search(rb"\\ansicpg(\d+)", roh[:4000])
    codepage = f"cp{int(m.group(1))}" if m else "cp1252"
    try:
        b"".decode(codepage)
    except LookupError:
        codepage = "cp1252"

    dok = Dokument()
    absatz = Absatz()
    zeile: list[list[Absatz]] = []
    zelle: list[Absatz] = []
    tabelle: Tabelle | None = None
    stapel: list[dict] = []
    zustand = {"fett": False, "kursiv": False, "unter": False, "durch": False, "weg": False, "uc": 1,
               "ausr": "", "intbl": False, "farbtab": False, "farbe": "", "marker": ""}
    ueberspringen = 0                           # Zeichen nach \uN, die als Ersatz dastehen
    puffer: list[bytes] = []
    farben: list[str] = []                      # \colortbl: Index -> "#rrggbb" ("" = automatisch)
    rgb: dict[str, int] = {}

    def text_rein(t: str) -> None:
        if t and not zustand["weg"]:
            absatz.laeufe.append(Lauf(t, zustand["fett"], zustand["kursiv"], zustand["unter"], zustand["durch"],
                                      farbe=zustand["farbe"], marker=zustand["marker"]))

    def farbe_nr(n: int | None) -> str:
        return farben[n] if n is not None and 0 < n < len(farben) else ""

    def puffer_leeren() -> None:
        if puffer:
            text_rein(b"".join(puffer).decode(codepage, errors="replace"))
            puffer.clear()

    def absatz_ende() -> None:
        nonlocal absatz, tabelle
        puffer_leeren()
        absatz.ausrichtung = zustand["ausr"]
        aufgabe_aus_text(_ersatzpaare(absatz.zusammenfassen()))
        if zustand["intbl"]:
            zelle.append(absatz)
        else:
            if tabelle is not None:
                dok.bloecke.append(tabelle)
                tabelle = None
            dok.bloecke.append(absatz)
        absatz = Absatz()

    erstes = True
    for t in _TOKEN.finditer(roh):
        wort, zahl, hexwert, zeichen, klammer, umbruch, text = t.groups()
        if klammer == b"{":
            puffer_leeren()
            if len(stapel) >= MAX_TIEFE:
                raise FormatFehler("RTF ist zu tief verschachtelt")
            stapel.append(dict(zustand))
            erstes = True
            continue
        if klammer == b"}":
            puffer_leeren()
            if stapel:
                alt = stapel.pop()
                alt["intbl"] = zustand["intbl"]          # Tabellen-Zustand gilt über Gruppen hinweg
                zustand.clear()
                zustand.update(alt)
            erstes = False
            continue
        if umbruch:
            continue
        if text is not None and zustand["farbtab"]:
            for _ in range(text.count(b";")):       # jedes ; schließt einen Eintrag der Farbtabelle
                if len(farben) < 1000:
                    farben.append("#%02x%02x%02x" % (rgb.get("red", 0), rgb.get("green", 0), rgb.get("blue", 0))
                                  if rgb else "")
                rgb.clear()
            continue
        if text is not None:
            if ueberspringen:
                n = min(ueberspringen, len(text))
                text, ueberspringen = text[n:], ueberspringen - n
            if text:
                puffer.append(text)
            erstes = False
            continue
        if hexwert is not None:
            if ueberspringen:
                ueberspringen -= 1
            else:
                puffer.append(bytes([int(hexwert, 16)]))
            erstes = False
            continue
        if zeichen is not None:
            if zeichen == b"*" and erstes:
                zustand["weg"] = True                    # unbekanntes optionales Ziel
            elif zeichen in (b"\\", b"{", b"}"):
                puffer.append(zeichen)
            elif zeichen == b"~":
                puffer.append(b"\xa0")
            elif zeichen in (b"\n", b"\r"):
                absatz_ende()
            elif zeichen == b"-" or zeichen == b"_":
                puffer.append(b"-" if zeichen == b"_" else b"")
            erstes = False
            continue
        w = wort.decode("ascii")
        n = int(zahl) if zahl is not None else None
        if erstes and w in _UEBERGEHEN:
            zustand["weg"] = True
            zustand["farbtab"] = w == "colortbl"
        erstes = False
        if zustand["farbtab"]:
            if w in ("red", "green", "blue") and n is not None:
                rgb[w] = max(0, min(255, n))
            continue
        if w == "u" and n is not None:
            puffer_leeren()
            text_rein(chr(n + 65536 if n < 0 else n) if 0 <= (n + 65536 if n < 0 else n) <= 0x10FFFF else "")
            ueberspringen = zustand["uc"]
            continue
        if w == "uc" and n is not None:
            zustand["uc"] = max(0, min(10, n))
            continue
        if w in ("b", "i", "strike", "striked"):
            puffer_leeren()
            zustand[{"b": "fett", "i": "kursiv"}.get(w, "durch")] = n != 0
        elif w in ("ul", "uld", "uldb", "ulw", "ulth", "uldash"):
            puffer_leeren()
            zustand["unter"] = n != 0
        elif w == "ulnone":
            puffer_leeren()
            zustand["unter"] = False
        elif w == "plain":
            puffer_leeren()
            zustand.update(fett=False, kursiv=False, unter=False, durch=False, farbe="", marker="")
        elif w == "cf":
            puffer_leeren()
            zustand["farbe"] = farbe_nr(n)
        elif w in ("highlight", "cb", "chcbpat"):
            puffer_leeren()
            zustand["marker"] = farbe_nr(n)
        elif w == "pard":
            zustand["ausr"] = ""
            zustand["intbl"] = False
        elif w in ("qc", "qr", "qj", "ql"):
            zustand["ausr"] = {"qc": "mitte", "qr": "rechts", "qj": "block"}.get(w, "")
        elif w == "intbl":
            zustand["intbl"] = True
        elif w in ("par", "sect", "page"):
            absatz_ende()
        elif w == "line":
            puffer.append(b"\n")
        elif w == "tab":
            puffer.append(b"\t")
        elif w == "cell":
            puffer_leeren()
            if absatz.laeufe or not zelle:
                _ersatzpaare(absatz.zusammenfassen())
                zelle.append(absatz)
                absatz = Absatz()
            zeile.append(list(zelle))
            zelle.clear()
        elif w == "row":
            if tabelle is None:
                tabelle = Tabelle()
            tabelle.zeilen.append(list(zeile))
            zeile.clear()
            zustand["intbl"] = False
        elif w in ("emdash", "endash"):
            puffer.append("—".encode(codepage, "replace") if w == "emdash" else "–".encode(codepage, "replace"))
        elif w in ("lquote", "rquote", "ldblquote", "rdblquote", "bullet"):
            zeichen_ = {"lquote": "‘", "rquote": "’", "ldblquote": "„", "rdblquote": "“", "bullet": "•"}[w]
            puffer_leeren()
            text_rein(zeichen_)
    puffer_leeren()
    if absatz.laeufe:
        absatz_ende()
    if tabelle is not None:
        dok.bloecke.append(tabelle)
    return dok


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    raus = []
    for ch in text:
        o = ord(ch)
        if ch in "\\{}":
            raus.append("\\" + ch)
        elif ch == "\t":
            raus.append("\\tab ")
        elif ch == "\n":
            raus.append("\\line ")
        elif o < 32:
            continue
        elif o < 128:
            raus.append(ch)
        elif o <= 0xFFFF:
            raus.append(f"\\u{o if o < 32768 else o - 65536}?")
        else:                                         # außerhalb der BMP: Ersatzpaar
            o -= 0x10000
            hoch, tief = 0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)
            raus.append(f"\\u{hoch - 65536}?\\u{tief - 65536}?")
    return "".join(raus)


def _laeufe(a: Absatz, farben: dict[str, int]) -> str:
    raus = []
    for l in a.laeufe:
        an = ("\\b" if l.fett else "") + ("\\i" if l.kursiv else "") + ("\\ul" if l.unter else "") + \
             ("\\strike" if l.durch else "")
        if (f := farbe_sauber(l.farbe)) in farben:
            an += f"\\cf{farben[f]}"
        if (m := farbe_sauber(l.marker)) in farben:
            an += f"\\highlight{farben[m]}"
        if l.code:
            an += "\\f1"
        raus.append("{" + (an + " " if an else "") + _esc(l.text) + "}")
    return "".join(raus)


_GROESSE = {1: 36, 2: 30, 3: 26, 4: 24, 5: 22, 6: 22}


def _absatz(a: Absatz, farben: dict[str, int]) -> str:
    kopf = "\\pard" + {"mitte": "\\qc", "rechts": "\\qr", "block": "\\qj"}.get(a.ausrichtung, "")
    inhalt = _laeufe(a, farben)
    if a.stufe:
        inhalt = f"{{\\b\\fs{_GROESSE.get(a.stufe, 24)} {inhalt}}}"
    elif a.aufgabe in AUFGABE_ZEICHEN:                 # Checkbox: ☐/☑ vor dem Text
        kopf += f"\\li{360 * (a.ebene + 2)}"
        inhalt = "{" + _esc(AUFGABE_ZEICHEN[a.aufgabe] + " ") + "}" + inhalt
    elif a.zitat:                                       # Zitat: eingerückt mit Balken links, kursiv
        kopf += "\\li567\\brdrl\\brdrs\\brdrw30\\brsp120\\brdrcf1"
        inhalt = "{\\i " + inhalt + "}"
    elif a.liste:
        einzug = 360 * (a.ebene + 1)
        kopf += f"\\li{einzug + 360}\\fi-360"
        inhalt = ("\\bullet\\tab " if a.liste == "punkt" else "#\\tab ") + inhalt
    return f"{kopf} {inhalt}\\par\n"


def _alle_laeufe(dok: Dokument):
    for b in dok.bloecke:
        if isinstance(b, Absatz):
            yield from b.laeufe
        elif isinstance(b, Tabelle):
            for z in b.zeilen:
                for zelle in z:
                    for a in zelle:
                        yield from a.laeufe


def nach_bytes(dok: Dokument) -> bytes:
    farben: dict[str, int] = {"#8b5cf6": 1}           # Farbe -> Index in \\colortbl (0 = automatisch, 1 = Zitat-Balken)
    for l in _alle_laeufe(dok):
        for f in (farbe_sauber(l.farbe), farbe_sauber(l.marker)):
            if f and f not in farben:
                farben[f] = len(farben) + 1
    tabelle = "".join(f"\\red{int(f[1:3], 16)}\\green{int(f[3:5], 16)}\\blue{int(f[5:7], 16)};" for f in farben)
    teile = ["{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1{\\fonttbl{\\f0\\fswiss Calibri;}{\\f1\\fmodern Consolas;}}"
             + (f"{{\\colortbl;{tabelle}}}" if farben else "") + "\\f0\\fs22\n"]
    zaehler = 0
    for b in dok.bloecke:
        if isinstance(b, Absatz):
            if b.liste == "zahl":
                zaehler += 1
                teile.append(_absatz(b, farben).replace("#\\tab ", f"{zaehler}.\\tab ", 1))
                continue
            zaehler = 0
            teile.append(_absatz(b, farben))
        elif isinstance(b, Linie):
            zaehler = 0
            teile.append("\\pard\\brdrb\\brdrs\\brdrw10\\brsp20 \\par\n")
        elif isinstance(b, Tabelle):
            zaehler = 0
            spalten = max(1, b.spalten)
            breite = 9000 // spalten
            raster = "".join(f"\\clbrdrt\\brdrs\\clbrdrl\\brdrs\\clbrdrb\\brdrs\\clbrdrr\\brdrs\\cellx{breite * (i + 1)}"
                             for i in range(spalten))
            for z in b.zeilen:
                zellen = []
                for i in range(spalten):
                    absaetze = z[i] if i < len(z) else []
                    zellen.append("\\pard\\intbl " + "\\line ".join(_laeufe(a, farben) for a in absaetze) + "\\cell")
                teile.append(f"\\trowd\\trgaph108{raster}\n" + "\n".join(zellen) + "\\row\n")
    teile.append("}")
    return "".join(teile).encode("ascii")
