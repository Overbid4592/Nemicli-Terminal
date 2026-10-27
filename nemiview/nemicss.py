"""
nemicss.py - CSS für NemiView: Stilregeln lesen, Selektoren prüfen, Kaskade (Spezifität, Reihenfolge,
!important), Vererbung, Variablen (var(--x, Ersatz)) und Kurzformen (margin, padding, border, font, background,
flex, gap, place-items, inset …).

Werte bleiben Text; Längen (px, %, em, rem, vw, vh, calc/min/max/clamp) rechnet das Layout aus, denn erst dort
sind Bezugsgrößen bekannt. Unbekannte Eigenschaften werden still übergangen – app_pruefen meldet sie.
"""
from __future__ import annotations

import re

VERERBT = {"color", "font-family", "font-size", "font-weight", "font-style", "line-height", "text-align",
           "letter-spacing", "visibility", "cursor", "white-space", "word-break", "text-transform",
           "list-style-type", "direction", "accent-color", "user-select"}

BEKANNT = VERERBT | {
    "display", "position", "top", "right", "bottom", "left", "z-index", "float", "clear",
    "width", "height", "min-width", "min-height", "max-width", "max-height", "box-sizing",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-top-style", "border-right-style", "border-bottom-style", "border-left-style",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "border-top-left-radius", "border-top-right-radius", "border-bottom-right-radius", "border-bottom-left-radius",
    "background-color", "background-image", "opacity", "box-shadow", "outline", "outline-offset",
    "overflow", "overflow-x", "overflow-y", "text-decoration", "text-overflow", "vertical-align",
    "flex-direction", "flex-wrap", "justify-content", "align-items", "align-content", "align-self", "flex-grow",
    "flex-shrink", "flex-basis", "order", "row-gap", "column-gap", "grid-template-columns", "grid-template-rows",
    "justify-items", "justify-self", "grid-column", "grid-row", "transition", "transform", "object-fit",
    "aspect-ratio", "pointer-events", "content", "list-style", "filter", "backdrop-filter", "appearance",
}

UA_CSS = """
html, body, div, main, section, article, header, footer, nav, aside, form, fieldset, figure, p, ul, ol, li, h1, h2, h3,
h4, h5, h6, pre, blockquote, hr, table, details, summary, dl, dt, dd, address, figcaption, legend { display: block; }
head, script, style, link, meta, title, template, noscript, [hidden] { display: none; }
li { display: list-item; }
table { display: table; } tr { display: table-row; } td, th { display: table-cell; padding: 2px 6px; }
body { margin: 8px; font: 16px/1.4 system-ui, sans-serif; color: #000; }
h1 { font-size: 2em; font-weight: 700; margin: 0.67em 0; }
h2 { font-size: 1.5em; font-weight: 700; margin: 0.83em 0; }
h3 { font-size: 1.17em; font-weight: 700; margin: 1em 0; }
h4, h5, h6 { font-weight: 700; margin: 1.33em 0; }
p, ul, ol, pre, blockquote, dl { margin: 1em 0; }
ul, ol { padding-left: 40px; }
b, strong, th { font-weight: 700; } i, em { font-style: italic; }
small { font-size: 0.83em; }
code, pre, kbd { font-family: ui-monospace, monospace; }
pre { white-space: pre; }
a { color: #4a8cff; text-decoration: underline; cursor: pointer; }
hr { border-top: 1px solid #888; margin: 8px 0; }
button, input, select, textarea { display: inline-block; font: 13px system-ui, sans-serif; }
button { padding: 4px 10px; border: 1px solid #8a8a8a; border-radius: 4px; background-color: #e9e9ed; color: #000;
         cursor: pointer; }
input, select, textarea { padding: 3px 5px; border: 1px solid #8a8a8a; border-radius: 3px; background-color: #fff;
                          color: #000; }
input { width: 160px; } textarea { width: 240px; height: 60px; }
input[type=checkbox], input[type=radio] { width: 16px; height: 16px; padding: 0; }
input[type=range] { width: 130px; height: 20px; padding: 0; border: 0; background-color: transparent; }
input[type=color] { width: 50px; height: 28px; padding: 2px; }
button:disabled, input:disabled { opacity: 0.5; cursor: default; }
img, svg, canvas { display: inline-block; }
"""


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

class Regel:
    __slots__ = ("selektoren", "dekl", "reihe", "media")

    def __init__(self, selektoren, dekl, reihe, media=None):
        self.selektoren, self.dekl, self.reihe, self.media = selektoren, dekl, reihe, media


def _kommentare_weg(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def deklarationen(text: str) -> list[tuple[str, str, bool]]:
    """'a: b; c: d !important' → [(eigenschaft, wert, wichtig)] mit aufgelösten Kurzformen."""
    out = []
    for teil in _teilen(text, ";"):
        if ":" not in teil:
            continue
        name, _, wert = teil.partition(":")
        name, wert = name.strip().lower(), wert.strip()
        if not name or not wert:
            continue
        wichtig = False
        if re.search(r"!\s*important\s*$", wert, re.I):
            wichtig = True
            wert = re.sub(r"!\s*important\s*$", "", wert, flags=re.I).strip()
        for n, w in kurzformen(name, wert):
            out.append((n, w, wichtig))
    return out


def _teilen(text: str, zeichen: str) -> list[str]:
    """An `zeichen` teilen, aber nicht innerhalb von Klammern oder Anführungszeichen."""
    teile, tiefe, aktuell, quote = [], 0, [], None
    for c in text:
        if quote:
            aktuell.append(c)
            if c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c in "([":
            tiefe += 1
        elif c in ")]":
            tiefe -= 1
        elif c == zeichen and tiefe == 0:
            teile.append("".join(aktuell))
            aktuell = []
            continue
        aktuell.append(c)
    teile.append("".join(aktuell))
    return teile


def werte(text: str) -> list[str]:
    """Wertliste an Leerzeichen teilen (Funktionen wie rgb( … ) bleiben ganz)."""
    return [w for w in _teilen(text.strip().replace("\t", " ").replace("\n", " "), " ") if w]


def stylesheet(css: str, start_reihe: int = 0) -> list[Regel]:
    css = _kommentare_weg(css)
    regeln: list[Regel] = []
    reihe = [start_reihe]

    def lesen(text: str, media):
        i = 0
        while i < len(text):
            j = text.find("{", i)
            if j < 0:
                break
            kopf = text[i:j].strip()
            tiefe, k = 1, j + 1
            while k < len(text) and tiefe:
                if text[k] == "{":
                    tiefe += 1
                elif text[k] == "}":
                    tiefe -= 1
                k += 1
            inhalt = text[j + 1:k - 1]
            i = k
            if kopf.startswith("@"):
                if kopf.lower().startswith("@media"):
                    lesen(inhalt, kopf[6:].strip())
                continue                                   # @keyframes, @font-face, @import: nicht dabei
            sel = [s.strip() for s in _teilen(kopf, ",") if s.strip()]
            dekl = deklarationen(inhalt)
            if sel and dekl:
                reihe[0] += 1
                regeln.append(Regel([(s, spezifitaet(s)) for s in sel], dekl, reihe[0], media))

    lesen(css, None)
    return regeln


def media_passt(media: str | None, breite: float, dunkel: bool) -> bool:
    if not media:
        return True
    for teil in _teilen(media, ","):
        ok = True
        for bed in re.findall(r"\(([^)]*)\)", teil):
            k, _, v = bed.partition(":")
            k, v = k.strip().lower(), v.strip().lower()
            if k == "prefers-color-scheme":
                ok &= (v == "dark") == dunkel
            elif k in ("max-width", "min-width"):
                px = _px_einfach(v)
                ok &= (breite <= px) if k == "max-width" else (breite >= px)
            elif k == "prefers-reduced-motion":
                ok &= v == "no-preference"
        if re.search(r"\bprint\b", teil) and "screen" not in teil:
            ok = False
        if ok:
            return True
    return False


def _px_einfach(v: str) -> float:
    m = re.match(r"([\d.]+)(px|em|rem)?", v)
    if not m:
        return 0
    x = float(m.group(1))
    return x * 16 if m.group(2) in ("em", "rem") else x


# ---------------------------------------------------------------------------
# Kurzformen
# ---------------------------------------------------------------------------

_SEITEN = ("top", "right", "bottom", "left")
_STILE = {"none", "hidden", "solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset"}


def _vier(w: list[str]) -> list[str]:
    if len(w) == 1:
        return w * 4
    if len(w) == 2:
        return [w[0], w[1], w[0], w[1]]
    if len(w) == 3:
        return [w[0], w[1], w[2], w[1]]
    return w[:4]


def ist_farbe(w: str) -> bool:
    w = w.lower()
    return w.startswith(("#", "rgb", "hsl")) or w in FARBEN or w in ("transparent", "currentcolor")


def kurzformen(name: str, wert: str) -> list[tuple[str, str]]:
    w = werte(wert)
    if name.startswith("--"):
        return [(name, wert)]
    if name in ("margin", "padding"):
        return [(f"{name}-{s}", v) for s, v in zip(_SEITEN, _vier(w))]
    if name == "inset":
        return list(zip(_SEITEN, _vier(w)))
    if name == "border-width":
        return [(f"border-{s}-width", v) for s, v in zip(_SEITEN, _vier(w))]
    if name == "border-color":
        return [(f"border-{s}-color", v) for s, v in zip(_SEITEN, _vier(w))]
    if name == "border-style":
        return [(f"border-{s}-style", v) for s, v in zip(_SEITEN, _vier(w))]
    if name in ("border", "border-top", "border-right", "border-bottom", "border-left"):
        seiten = _SEITEN if name == "border" else (name.split("-")[1],)
        breite, stil, farbe = "medium", "none", "currentcolor"
        if wert.strip().lower() in ("0", "none"):
            breite, stil = "0", "none"
        for x in w:
            if x.lower() in _STILE:
                stil = x.lower()
            elif ist_farbe(x):
                farbe = x
            else:
                breite = x
        if stil == "none":
            breite = "0"
        out = []
        for s in seiten:
            out += [(f"border-{s}-width", breite), (f"border-{s}-style", stil), (f"border-{s}-color", farbe)]
        return out
    if name == "border-radius":
        haupt = wert.split("/")[0]
        return [(f"border-{e}-radius", v) for e, v in
                zip(("top-left", "top-right", "bottom-right", "bottom-left"), _vier(werte(haupt)))]
    if name == "background":
        out = [("background-color", "transparent"), ("background-image", "none")]
        for x in w:
            if "gradient(" in x or x.startswith("url("):
                out[1] = ("background-image", x)
            elif ist_farbe(x) or x.startswith("var("):
                out[0] = ("background-color", x)
        if len(w) == 1 and w[0].lower() == "none":
            out = [("background-color", "transparent"), ("background-image", "none")]
        return out
    if name == "font":
        return _font(wert)
    if name == "flex":
        if wert.strip() == "none":
            return [("flex-grow", "0"), ("flex-shrink", "0"), ("flex-basis", "auto")]
        if wert.strip() == "auto":
            return [("flex-grow", "1"), ("flex-shrink", "1"), ("flex-basis", "auto")]
        g = w + [None, None]
        if len(w) == 1 and not re.fullmatch(r"[\d.]+", w[0]):
            return [("flex-grow", "1"), ("flex-shrink", "1"), ("flex-basis", w[0])]
        basis = g[2] if len(w) >= 3 else (g[1] if len(w) == 2 and not re.fullmatch(r"[\d.]+", g[1]) else "0%")
        schrumpf = g[1] if len(w) >= 2 and re.fullmatch(r"[\d.]+", g[1] or "") else "1"
        return [("flex-grow", g[0]), ("flex-shrink", schrumpf), ("flex-basis", basis)]
    if name == "flex-flow":
        out = []
        for x in w:
            out.append(("flex-wrap" if "wrap" in x else "flex-direction", x))
        return out
    if name == "gap" or name == "grid-gap":
        return [("row-gap", w[0]), ("column-gap", w[1] if len(w) > 1 else w[0])]
    if name == "place-items":
        return [("align-items", w[0]), ("justify-items", w[1] if len(w) > 1 else w[0])]
    if name == "place-content":
        return [("align-content", w[0]), ("justify-content", w[1] if len(w) > 1 else w[0])]
    if name == "place-self":
        return [("align-self", w[0]), ("justify-self", w[1] if len(w) > 1 else w[0])]
    if name == "overflow" and len(w) == 2:
        return [("overflow", w[0]), ("overflow-x", w[0]), ("overflow-y", w[1])]
    if name == "list-style":
        return [("list-style-type", w[0])]
    if name == "outline":
        return [("outline", wert)]
    return [(name, wert)]


def _font(wert: str) -> list[tuple[str, str]]:
    if wert.strip().lower() == "inherit":
        return [(k, "inherit") for k in ("font-style", "font-weight", "font-size", "line-height", "font-family")]
    teile = werte(wert)
    stil, gewicht, groesse, zeile, familie = "normal", "normal", None, "normal", []
    i = 0
    while i < len(teile):
        x = teile[i]
        xl = x.lower()
        if groesse is None:
            if xl in ("italic", "oblique"):
                stil = xl
            elif xl in ("bold", "bolder", "lighter") or re.fullmatch(r"[1-9]00", xl):
                gewicht = xl
            elif xl in ("normal", "small-caps"):
                pass
            else:
                if "/" in x:
                    groesse, _, zeile = x.partition("/")
                elif i + 1 < len(teile) and teile[i + 1].startswith("/"):
                    groesse = x
                    zeile = teile[i + 1][1:] or (teile[i + 2] if i + 2 < len(teile) else "normal")
                    i += 1 if teile[i + 1] != "/" else 2
                else:
                    groesse = x
        else:
            familie.append(x)
        i += 1
    out = [("font-style", stil), ("font-weight", gewicht), ("line-height", zeile)]
    if groesse:
        out.append(("font-size", groesse))
    if familie:
        out.append(("font-family", " ".join(familie)))
    return out


# ---------------------------------------------------------------------------
# Selektoren
# ---------------------------------------------------------------------------

_SEL_TEIL = re.compile(r"""
    (?P<kombi>\s*[>+~]\s*|\s+)
  | (?P<stern>\*)
  | \#(?P<id>[\w-]+)
  | \.(?P<klasse>[\w-]+)
  | \[(?P<attr>[\w-]+)(?:(?P<aop>[~|^$*]?=)\s*(?P<awert>"[^"]*"|'[^']*'|[^\]\s]+)\s*(?P<ai>i)?)?\]
  | ::?(?P<pseudo>[\w-]+)(?:\((?P<parg>[^()]*(?:\([^()]*\))?[^()]*)\))?
  | (?P<tag>[\w-]+)
""", re.X)


def selektor_lesen(s: str) -> list:
    """'main > .a:hover' → [(einfach, None), (einfach, '>')] – rechts zuerst ausgewertet."""
    s = s.strip()
    teile, aktuell, i = [], {"tag": None, "ids": [], "klassen": [], "attr": [], "pseudo": []}, 0
    kombi = None
    leer = True
    while i < len(s):
        m = _SEL_TEIL.match(s, i)
        if not m:
            raise ValueError(f"Selektor nicht lesbar: {s!r}")
        i = m.end()
        if m.group("kombi") is not None:
            k = m.group("kombi").strip() or " "
            if not leer:
                teile.append((aktuell, kombi))
                aktuell = {"tag": None, "ids": [], "klassen": [], "attr": [], "pseudo": []}
                kombi, leer = k, True
            elif k != " ":
                kombi = k
            continue
        leer = False
        if m.group("stern"):
            pass
        elif m.group("id"):
            aktuell["ids"].append(m.group("id"))
        elif m.group("klasse"):
            aktuell["klassen"].append(m.group("klasse"))
        elif m.group("attr"):
            w = m.group("awert")
            if w and w[0] in "\"'":
                w = w[1:-1]
            aktuell["attr"].append((m.group("attr").lower(), m.group("aop"), w, bool(m.group("ai"))))
        elif m.group("pseudo"):
            aktuell["pseudo"].append((m.group("pseudo").lower(), m.group("parg")))
        elif m.group("tag"):
            aktuell["tag"] = m.group("tag").lower()
    if not leer:
        teile.append((aktuell, kombi))
    return teile


def spezifitaet(s: str) -> tuple[int, int, int]:
    try:
        teile = selektor_lesen(s)
    except ValueError:
        return (0, 0, 0)
    a = b = c = 0
    for einfach, _k in teile:
        a += len(einfach["ids"])
        b += len(einfach["klassen"]) + len(einfach["attr"]) + sum(1 for p, _ in einfach["pseudo"]
                                                                 if p not in ("before", "after"))
        c += 1 if einfach["tag"] else 0
    return (a, b, c)


_CACHE: dict[str, list] = {}


def passt(el, selektor: str, zustand=None) -> bool:
    """Passt das Element zum Selektor? zustand(el, pseudo) → bool für :hover/:focus/:active."""
    teile = _CACHE.get(selektor)
    if teile is None:
        try:
            teile = selektor_lesen(selektor)
        except ValueError:
            teile = []
        if len(_CACHE) > 5000:
            _CACHE.clear()
        _CACHE[selektor] = teile
    if not teile:
        return False
    return _passt_ab(el, teile, len(teile) - 1, zustand)


def _passt_ab(el, teile, i, zustand) -> bool:
    einfach, kombi = teile[i]
    if not _einfach_passt(el, einfach, zustand):
        return False
    if i == 0:
        return True
    if kombi == ">":
        p = el.eltern_element()
        return p is not None and _passt_ab(p, teile, i - 1, zustand)
    if kombi == " " or kombi is None:
        p = el.eltern_element()
        while p is not None:
            if _passt_ab(p, teile, i - 1, zustand):
                return True
            p = p.eltern_element()
        return False
    if kombi == "+":
        v = el.vorheriges_element()
        return v is not None and _passt_ab(v, teile, i - 1, zustand)
    if kombi == "~":
        v = el.vorheriges_element()
        while v is not None:
            if _passt_ab(v, teile, i - 1, zustand):
                return True
            v = v.vorheriges_element()
        return False
    return False


def _einfach_passt(el, e, zustand) -> bool:
    if e["tag"] and e["tag"] != el.tag:
        return False
    for i in e["ids"]:
        if el.attrs.get("id") != i:
            return False
    if e["klassen"]:
        k = set(el.attrs.get("class", "").split())
        if not all(x in k for x in e["klassen"]):
            return False
    for name, op, wert, gross in e["attr"]:
        if name not in el.attrs:
            return False
        if op is None:
            continue
        ist = el.attrs[name]
        if gross:
            ist, wert = ist.lower(), wert.lower()
        if op == "=" and ist != wert:
            return False
        if op == "~=" and wert not in ist.split():
            return False
        if op == "^=" and not ist.startswith(wert):
            return False
        if op == "$=" and not ist.endswith(wert):
            return False
        if op == "*=" and wert not in ist:
            return False
        if op == "|=" and not (ist == wert or ist.startswith(wert + "-")):
            return False
    for p, arg in e["pseudo"]:
        if not _pseudo(el, p, arg, zustand):
            return False
    return True


def _pseudo(el, p, arg, zustand) -> bool:
    if p == "root":
        return el.eltern_element() is None
    if p in ("hover", "focus", "active", "focus-visible", "focus-within"):
        return bool(zustand and zustand(el, p))
    if p == "disabled":
        return "disabled" in el.attrs
    if p == "enabled":
        return "disabled" not in el.attrs
    if p == "checked":
        return bool(getattr(el, "checked", False))
    if p == "empty":
        return not el.kinder
    if p in ("first-child", "last-child", "only-child", "nth-child", "nth-last-child", "first-of-type",
             "last-of-type", "nth-of-type"):
        eltern = el.eltern_element()
        geschwister = [k for k in (eltern.kinder if eltern else [el]) if getattr(k, "tag", None)]
        if p.endswith("of-type"):
            geschwister = [k for k in geschwister if k.tag == el.tag]
        pos = geschwister.index(el) + 1 if el in geschwister else 0
        if p in ("first-child", "first-of-type"):
            return pos == 1
        if p in ("last-child", "last-of-type"):
            return pos == len(geschwister)
        if p == "only-child":
            return len(geschwister) == 1
        if p == "nth-last-child":
            pos = len(geschwister) - pos + 1
        return _an_b(arg or "", pos)
    if p == "not":
        return not any(passt(el, s.strip(), zustand) for s in _teilen(arg or "", ","))
    if p in ("is", "where"):
        return any(passt(el, s.strip(), zustand) for s in _teilen(arg or "", ","))
    if p == "placeholder-shown":
        return not getattr(el, "wert", "") and "placeholder" in el.attrs
    if p in ("before", "after", "placeholder", "selection", "-webkit-scrollbar", "first-line", "first-letter"):
        return False                                           # Pseudo-Elemente: nicht gezeichnet
    return False


def _an_b(arg: str, pos: int) -> bool:
    a = arg.strip().lower().replace(" ", "")
    if a == "odd":
        return pos % 2 == 1
    if a == "even":
        return pos % 2 == 0
    m = re.fullmatch(r"([+-]?\d*)n([+-]\d+)?", a)
    if m:
        k = m.group(1)
        k = -1 if k == "-" else (1 if k in ("", "+") else int(k))
        b = int(m.group(2) or 0)
        if k == 0:
            return pos == b
        return (pos - b) % k == 0 and (pos - b) // k >= 0
    try:
        return pos == int(a)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Kaskade
# ---------------------------------------------------------------------------

class Kaskade:
    """Alle Regeln einer Seite; berechnet den Stil je Element (dict Eigenschaft → Text)."""

    def __init__(self, css_texte: list[str], breite: float = 1000, dunkel: bool = True):
        self.regeln = stylesheet(UA_CSS, 0)
        self.ua_bis = len(self.regeln)
        reihe = 100_000
        for t in css_texte:
            neu = stylesheet(t, reihe)
            self.regeln += neu
            reihe += len(neu) + 1
        self.breite, self.dunkel = breite, dunkel

    def stil(self, el, eltern_stil: dict | None, zustand=None) -> dict:
        treffer = []                                       # (wichtig, autor, spezifität, reihe, eigenschaft, wert)
        for i, r in enumerate(self.regeln):
            if r.media and not media_passt(r.media, self.breite, self.dunkel):
                continue
            beste = None
            for sel, spez in r.selektoren:
                if passt(el, sel, zustand):
                    beste = spez if beste is None or spez > beste else beste
            if beste is not None:
                autor = i >= self.ua_bis
                for name, wert, wichtig in r.dekl:
                    treffer.append(((wichtig, autor, beste, r.reihe), name, wert))
        inline = el.attrs.get("style")
        if inline:
            for name, wert, wichtig in deklarationen(inline):
                treffer.append(((wichtig, True, (999, 0, 0), 10 ** 9), name, wert))
        for name, wert in getattr(el, "stil_js", {}).items():           # element.style.x = … aus JS
            for n, w, wichtig in deklarationen(f"{name}: {wert}"):
                treffer.append(((wichtig, True, (999, 0, 0), 10 ** 9 + 1), n, w))
        treffer.sort(key=lambda t: (t[0][0], t[0][1] if not t[0][0] else not t[0][1], t[0][2], t[0][3]))
        s: dict = {}
        for _rang, name, wert in treffer:
            s[name] = wert
        eltern_stil = eltern_stil or {}
        for k, v in eltern_stil.items():                   # Variablen erben immer
            if k.startswith("--") and k not in s:
                s[k] = v
        for k in VERERBT:
            if k not in s and k in eltern_stil:
                s[k] = eltern_stil[k]
        for k, v in list(s.items()):
            if v == "inherit":
                if k in eltern_stil:
                    s[k] = eltern_stil[k]
                else:
                    del s[k]
            elif v in ("initial", "unset", "revert"):
                if v == "unset" and k in VERERBT and k in eltern_stil:
                    s[k] = eltern_stil[k]
                else:
                    del s[k]
        for k, v in list(s.items()):
            if "var(" in v and not k.startswith("--"):
                s[k] = variablen(v, s)
        return s


def variablen(wert: str, s: dict, tiefe: int = 0) -> str:
    if tiefe > 10 or "var(" not in wert:
        return wert
    i = wert.find("var(")
    tiefe_k, j = 0, i + 3
    while j < len(wert):
        if wert[j] == "(":
            tiefe_k += 1
        elif wert[j] == ")":
            tiefe_k -= 1
            if tiefe_k == 0:
                break
        j += 1
    inneres = wert[i + 4:j]
    name, _, ersatz = inneres.partition(",")
    ersetzt = s.get(name.strip(), ersatz.strip())
    return variablen(wert[:i] + variablen(ersetzt, s, tiefe + 1) + wert[j + 1:], s, tiefe + 1)


# ---------------------------------------------------------------------------
# Farben
# ---------------------------------------------------------------------------

FARBEN = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
    "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "gray": "#808080",
    "grey": "#808080", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3", "darkgray": "#a9a9a9", "silver": "#c0c0c0",
    "navy": "#000080", "teal": "#008080", "lime": "#00ff00", "aqua": "#00ffff", "cyan": "#00ffff",
    "magenta": "#ff00ff", "fuchsia": "#ff00ff", "maroon": "#800000", "olive": "#808000", "brown": "#a52a2a",
    "gold": "#ffd700", "crimson": "#dc143c", "coral": "#ff7f50", "tomato": "#ff6347", "salmon": "#fa8072",
    "violet": "#ee82ee", "indigo": "#4b0082", "turquoise": "#40e0d0", "skyblue": "#87ceeb",
    "steelblue": "#4682b4", "royalblue": "#4169e1", "dodgerblue": "#1e90ff", "lightblue": "#add8e6",
    "darkblue": "#00008b", "darkgreen": "#006400", "lightgreen": "#90ee90", "forestgreen": "#228b22",
    "seagreen": "#2e8b57", "darkred": "#8b0000", "beige": "#f5f5dc", "ivory": "#fffff0", "khaki": "#f0e68c",
    "lavender": "#e6e6fa", "plum": "#dda0dd", "orchid": "#da70d6", "tan": "#d2b48c", "chocolate": "#d2691e",
    "whitesmoke": "#f5f5f5", "gainsboro": "#dcdcdc", "slategray": "#708090", "dimgray": "#696969",
    "hotpink": "#ff69b4", "deeppink": "#ff1493", "mintcream": "#f5fffa", "aliceblue": "#f0f8ff",
}


def farbe(wert: str | None, aktuell=(0, 0, 0, 255)) -> tuple[int, int, int, int] | None:
    """CSS-Farbe → (r, g, b, a) mit a 0–255; None bei transparent/unbekannt."""
    if not wert:
        return None
    w = wert.strip().lower()
    if w == "transparent" or w == "none":
        return None
    if w == "currentcolor":
        return aktuell
    w = FARBEN.get(w, w)
    if w.startswith("#"):
        h = w[1:]
        try:
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h)
            if len(h) == 6:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255
            if len(h) == 8:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        except ValueError:
            return None
        return None
    m = re.match(r"(rgba?|hsla?)\(([^)]*)\)", w)
    if not m:
        return None
    teile = [t for t in re.split(r"[\s,/]+", m.group(2).strip()) if t]
    if len(teile) < 3:
        return None

    def zahl(t, voll=255.0):
        return float(t[:-1]) / 100 * voll if t.endswith("%") else float(t.replace("deg", ""))
    try:
        alpha = 255
        if len(teile) > 3:
            a = teile[3]
            alpha = int(round((float(a[:-1]) / 100 if a.endswith("%") else float(a)) * 255))
        if m.group(1).startswith("rgb"):
            r, g, b = (max(0, min(255, int(round(zahl(t))))) for t in teile[:3])
            return r, g, b, max(0, min(255, alpha))
        import colorsys
        h = zahl(teile[0]) % 360 / 360
        s = zahl(teile[1], 1.0) if teile[1].endswith("%") else float(teile[1]) / 100
        light = zahl(teile[2], 1.0) if teile[2].endswith("%") else float(teile[2]) / 100
        r, g, b = colorsys.hls_to_rgb(h, light, s)
        return int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), max(0, min(255, alpha))
    except ValueError:
        return None
