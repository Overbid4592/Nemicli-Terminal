"""
layout.py - Layout für NemiView: aus DOM + berechnetem Stil Kästen mit Lage und Größe.

Block-Fluss (mit zusammenfallenden Außenabständen zwischen Geschwistern), Zeilen mit Umbruch (Text, Inline,
Inline-Block, Formular-Elemente), Flexbox (Richtung, Umbruch, gap, justify/align, grow/shrink/basis, order),
Grid (Spalten px/fr/auto/repeat/minmax, gap, place-items), position relative/absolute/fixed, overflow.
Längen: px, %, em, rem, vw, vh, vmin, vmax, calc(), min(), max(), clamp().
Textbreiten misst ein Messer (Qt-Schriftmetrik im Fenster, eine einfache Schätzung in Tests).
"""
from __future__ import annotations

import math
import re

from . import nemicss
from .nemidom import Element, Text

INLINE = {"inline", "inline-block", "inline-flex", "inline-grid"}
ERSETZT = {"input", "button", "select", "textarea", "img", "svg", "canvas", "progress", "meter"}


class Messer:
    """Schätzung ohne Qt: mittlere Zeichenbreite. Das Fenster setzt eine Qt-Fassung ein."""

    def breite(self, text: str, schrift: dict) -> float:
        return len(text) * schrift["size"] * 0.55 + max(0, len(text) - 1) * schrift.get("spacing", 0)

    def hoehe(self, schrift: dict) -> float:
        return schrift["size"] * 1.2


# ---------------------------------------------------------------------------
# Längen
# ---------------------------------------------------------------------------

class Ctx:
    """Bezugsgrößen für Längen."""
    __slots__ = ("vw", "vh", "root_fs")

    def __init__(self, vw, vh, root_fs=16.0):
        self.vw, self.vh, self.root_fs = vw, vh, root_fs


def laenge(wert, bezug, fs, ctx: Ctx, auto=None):
    """CSS-Länge → px; None für auto (oder wenn % ohne Bezug)."""
    if wert is None:
        return auto
    w = str(wert).strip().lower()
    if w in ("", "auto", "none", "normal", "fit-content", "max-content", "min-content"):
        return auto
    if w in ("thin",):
        return 1.0
    if w in ("medium",):
        return 3.0
    if w in ("thick",):
        return 5.0
    m = re.fullmatch(r"(-?[\d.]+(?:e-?\d+)?)(px|%|em|rem|vw|vh|vmin|vmax|pt|ch|ex)?", w)
    if m:
        x = float(m.group(1))
        e = m.group(2) or "px"
        if e == "px":
            return x
        if e == "%":
            return None if bezug is None else x * bezug / 100
        if e == "em":
            return x * fs
        if e == "rem":
            return x * ctx.root_fs
        if e == "vw":
            return x * ctx.vw / 100
        if e == "vh":
            return x * ctx.vh / 100
        if e == "vmin":
            return x * min(ctx.vw, ctx.vh) / 100
        if e == "vmax":
            return x * max(ctx.vw, ctx.vh) / 100
        if e == "pt":
            return x * 4 / 3
        if e in ("ch", "ex"):
            return x * fs * 0.5
    m = re.fullmatch(r"(calc|min|max|clamp)\((.*)\)", w)
    if m:
        args = [a.strip() for a in nemicss._teilen(m.group(2), ",")]
        if m.group(1) == "calc":
            return _calc(m.group(2), bezug, fs, ctx)
        werte = [laenge(a, bezug, fs, ctx) for a in args]
        if any(v is None for v in werte):
            return auto
        if m.group(1) == "min":
            return min(werte)
        if m.group(1) == "max":
            return max(werte)
        if len(werte) == 3:
            return max(werte[0], min(werte[1], werte[2]))
    return auto


def _calc(ausdruck, bezug, fs, ctx):
    teile = re.findall(r"\d*\.?\d+(?:px|%|em|rem|vw|vh|vmin|vmax)?|[-+*/()]|calc|min|max", ausdruck.lower())
    text = []
    for t in teile:
        if re.match(r"\d|\.", t):
            v = laenge(t, bezug, fs, ctx)
            if v is None:
                return None
            text.append(repr(v))
        elif t in "+-*/()":
            text.append(t)
    try:
        code = "".join(text)
        if not re.fullmatch(r"[\d.e+\-*/() ]*", code):
            return None
        return float(eval(code, {"__builtins__": {}}, {}))          # nur Zahlen und + - * / ( )
    except Exception:
        return None


def schrift_von(s: dict, eltern_fs: float, ctx: Ctx) -> dict:
    fs_w = s.get("font-size", f"{eltern_fs}px")
    groessen = {"xx-small": 9, "x-small": 10, "small": 13, "medium": 16, "large": 18, "x-large": 24,
                "xx-large": 32, "smaller": eltern_fs * 0.83, "larger": eltern_fs * 1.2}
    fs = groessen.get(fs_w) or laenge(fs_w, eltern_fs, eltern_fs, ctx) or eltern_fs
    gw = s.get("font-weight", "400")
    gewicht = {"normal": 400, "bold": 700, "bolder": 700, "lighter": 300}.get(gw)
    if gewicht is None:
        try:
            gewicht = int(float(gw))
        except ValueError:
            gewicht = 400
    familie = s.get("font-family", "system-ui, sans-serif")
    lh = s.get("line-height", "normal")
    if lh == "normal":
        zeile = fs * 1.25
    elif re.fullmatch(r"[\d.]+", lh):
        zeile = float(lh) * fs
    else:
        zeile = laenge(lh, fs, fs, ctx) or fs * 1.25
    return {"size": max(1.0, fs), "weight": gewicht, "italic": s.get("font-style") in ("italic", "oblique"),
            "family": familie, "line": zeile, "spacing": laenge(s.get("letter-spacing"), None, fs, ctx) or 0.0}


# ---------------------------------------------------------------------------
# Kästen
# ---------------------------------------------------------------------------

class Kasten:
    __slots__ = ("el", "stil", "anzeige", "kinder", "x", "y", "w", "h", "rand", "polster", "aussen", "schrift",
                 "zeilen", "pos", "abs_kinder", "clip", "z", "texte", "marker", "inhalt_h", "fest")

    def __init__(self, el, stil, anzeige):
        self.el, self.stil, self.anzeige = el, stil, anzeige
        self.kinder: list = []
        self.x = self.y = self.w = self.h = 0.0
        self.rand = [0.0] * 4          # oben, rechts, unten, links
        self.polster = [0.0] * 4
        self.aussen = [0.0] * 4
        self.schrift: dict = {}
        self.zeilen: list = []
        self.texte: list = []           # (x, y, text, schrift, farbe) für Textstücke
        self.pos = stil.get("position", "static")
        self.abs_kinder: list = []
        self.clip = stil.get("overflow", "visible") in ("hidden", "auto", "scroll", "clip")
        try:
            self.z = int(stil.get("z-index", "0"))
        except ValueError:
            self.z = 0
        self.marker = None
        self.inhalt_h = 0.0
        self.fest = False


class Layout:
    def __init__(self, dok, kaskade, breite, hoehe, messer: Messer | None = None):
        self.dok, self.kaskade = dok, kaskade
        self.breite, self.hoehe = breite, hoehe
        self.messer = messer or Messer()
        self.ctx = Ctx(breite, hoehe)
        self.wurzel: Kasten | None = None
        self.alle: list[Kasten] = []
        self.dok_hoehe = hoehe
        self.bild_groesse = None         # Rückruf (el) → (w, h) für <img>

    # -- Baum ---------------------------------------------------------------------
    def rechnen(self) -> Kasten:
        html_el = self.dok.html_el
        self.alle = []
        s_html = self.kaskade.stil(html_el, {}, self.dok.zustand)
        html_el.stil = s_html
        self.ctx.root_fs = schrift_von(s_html, 16, self.ctx)["size"]
        self.wurzel = self._kasten(html_el, s_html, None)
        self._block(self.wurzel, 0, 0, self.breite, self.hoehe, wurzel=True)
        self._absolute(self.wurzel)
        self.dok_hoehe = max(self.hoehe, self.wurzel.h + self.wurzel.aussen[0] + self.wurzel.aussen[2])
        self.dok.geaendert = False
        return self.wurzel

    def _kasten(self, el, s, eltern_kasten):
        anzeige = s.get("display", "inline")
        k = Kasten(el, s, anzeige)
        k.schrift = schrift_von(s, eltern_kasten.schrift["size"] if eltern_kasten else 16, self.ctx)
        el.box = None
        self.alle.append(k)
        for kind in el.kinder:
            if isinstance(kind, Text):
                if kind.text:
                    k.kinder.append(kind)
            elif isinstance(kind, Element):
                ks = self.kaskade.stil(kind, s, self.dok.zustand)
                kind.stil = ks
                if ks.get("display") == "none" or kind.tag in ("script", "style", "head", "template"):
                    kind.box = None
                    continue
                kk = self._kasten(kind, ks, k)
                if kk.pos in ("absolute", "fixed"):
                    k.abs_kinder.append(kk)
                k.kinder.append(kk)
        return k

    # -- Kastenmodell ----------------------------------------------------------------
    def _abstaende(self, k: Kasten, cb_w):
        s, fs = k.stil, k.schrift["size"]
        for i, seite in enumerate(("top", "right", "bottom", "left")):
            k.aussen[i] = laenge(s.get(f"margin-{seite}"), cb_w, fs, self.ctx, auto=0.0) or 0.0
            k.polster[i] = max(0.0, laenge(s.get(f"padding-{seite}"), cb_w, fs, self.ctx, auto=0.0) or 0.0)
            stil = s.get(f"border-{seite}-style", "none")
            k.rand[i] = 0.0 if stil in ("none", "hidden") else max(0.0, laenge(s.get(f"border-{seite}-width", "medium"),
                                                                                None, fs, self.ctx, auto=0.0) or 0.0)

    def _breite_vorgabe(self, k: Kasten, cb_w):
        s = k.stil
        w = laenge(s.get("width"), cb_w, k.schrift["size"], self.ctx)
        if w is not None and s.get("box-sizing") == "border-box":
            w = max(0.0, w - k.polster[1] - k.polster[3] - k.rand[1] - k.rand[3])
        return w

    def _begrenzen(self, k: Kasten, w, cb_w, achse="width"):
        s, fs = k.stil, k.schrift["size"]
        rand_pol = (k.polster[1] + k.polster[3] + k.rand[1] + k.rand[3]) if achse == "width" else \
            (k.polster[0] + k.polster[2] + k.rand[0] + k.rand[2])
        bb = s.get("box-sizing") == "border-box"
        mn = laenge(s.get(f"min-{achse}"), cb_w, fs, self.ctx)
        mx = laenge(s.get(f"max-{achse}"), cb_w, fs, self.ctx)
        if mx is not None:
            w = min(w, max(0.0, mx - rand_pol) if bb else mx)
        if mn is not None:
            w = max(w, max(0.0, mn - rand_pol) if bb else mn)
        return w

    # -- Block ------------------------------------------------------------------------
    def _block(self, k: Kasten, x, y, cb_w, cb_h, wurzel=False, fest_w=None):
        """Kasten k als Block an (x, y) im Bezugsblock der Breite cb_w legen."""
        k.texte, k.marker = [], None          # neu legen: nichts vom letzten Messen behalten
        self._abstaende(k, cb_w)
        s = k.stil
        w = fest_w if fest_w is not None else self._breite_vorgabe(k, cb_w)
        rp = k.polster[1] + k.polster[3] + k.rand[1] + k.rand[3]
        if w is None:
            if k.el.tag in ERSETZT or k.anzeige in INLINE:
                w = self._inhalt_breite(k, cb_w)
                w = min(w, max(0.0, cb_w - rp - k.aussen[1] - k.aussen[3])) if k.el.tag not in ERSETZT else w
            else:
                w = max(0.0, cb_w - rp - k.aussen[1] - k.aussen[3])
        w = self._begrenzen(k, w, cb_w)
        if fest_w is None and s.get("width") not in (None, "auto") and k.anzeige not in INLINE:
            frei = cb_w - (w + rp)
            links_auto, rechts_auto = s.get("margin-left") == "auto", s.get("margin-right") == "auto"
            if links_auto and rechts_auto:
                k.aussen[1] = k.aussen[3] = max(0.0, frei / 2)
            elif links_auto:
                k.aussen[3] = max(0.0, frei - k.aussen[1])
        k.x, k.y = x + k.aussen[3], y + k.aussen[0]
        k.w = w + rp
        innen_x = k.x + k.rand[3] + k.polster[3]
        innen_y = k.y + k.rand[0] + k.polster[0]
        h_vorgabe = laenge(s.get("height"), cb_h, k.schrift["size"], self.ctx)
        if h_vorgabe is not None and s.get("box-sizing") == "border-box":
            h_vorgabe = max(0.0, h_vorgabe - k.polster[0] - k.polster[2] - k.rand[0] - k.rand[2])
        if wurzel and h_vorgabe is None:
            h_vorgabe = None
        innen_h = self._inhalt(k, innen_x, innen_y, w, h_vorgabe if h_vorgabe is not None else
                               self._min_h(k, cb_h))
        k.inhalt_h = innen_h
        h = h_vorgabe if h_vorgabe is not None else innen_h
        h = self._begrenzen(k, h, cb_h, "height")
        k.h = h + k.polster[0] + k.polster[2] + k.rand[0] + k.rand[2]
        self._relativ(k, cb_w, cb_h)
        k.el.box = (k.x, k.y, k.w, k.h)

    def _min_h(self, k, cb_h):
        mn = laenge(k.stil.get("min-height"), cb_h, k.schrift["size"], self.ctx)
        if mn is None:
            return None
        if k.stil.get("box-sizing") == "border-box":
            mn -= k.polster[0] + k.polster[2] + k.rand[0] + k.rand[2]
        return max(0.0, mn)

    def _relativ(self, k, cb_w, cb_h):
        if k.pos != "relative":
            return
        fs = k.schrift["size"]
        dx = laenge(k.stil.get("left"), cb_w, fs, self.ctx) or -(laenge(k.stil.get("right"), cb_w, fs, self.ctx) or 0)
        dy = laenge(k.stil.get("top"), cb_h, fs, self.ctx) or -(laenge(k.stil.get("bottom"), cb_h, fs, self.ctx) or 0)
        if dx or dy:
            self._verschieben(k, dx, dy)

    def _verschieben(self, k, dx, dy):
        k.x += dx
        k.y += dy
        k.texte = [(x + dx, y + dy, t, f, c) for x, y, t, f, c in k.texte]
        if k.marker:
            mx, my, mt = k.marker
            k.marker = (mx + dx, my + dy, mt)
        if isinstance(k.el, Element):
            k.el.box = (k.x, k.y, k.w, k.h)
        for c in k.kinder:
            if isinstance(c, Kasten):
                self._verschieben(c, dx, dy)

    def _inhalt(self, k: Kasten, x, y, w, h):
        """Inhalt von k in den Bereich (x, y, w); h = feste Höhe oder Mindesthöhe (oder None). → Inhaltshöhe"""
        a = k.anzeige
        tag = k.el.tag
        if tag in ERSETZT:
            return self._ersetzt_hoehe(k, w, x, y)
        if a in ("flex", "inline-flex"):
            return self._flex(k, x, y, w, h)
        if a in ("grid", "inline-grid"):
            return self._grid(k, x, y, w, h)
        if a == "table-row":
            return self._zeile_tabelle(k, x, y, w)
        hoehe = self._fluss(k, x, y, w)
        if a == "list-item":
            typ = k.stil.get("list-style-type", "disc")
            if typ != "none":
                eltern = k.el.eltern
                nr = (eltern.elemente().index(k.el) + 1) if eltern is not None and k.el in eltern.elemente() else 1
                text = f"{nr}." if typ in ("decimal",) or (eltern is not None and eltern.tag == "ol") else "•"
                k.marker = (x - k.schrift["size"] * 1.1, y, text)
        return max(hoehe, h or 0.0)

    def _fluss(self, k: Kasten, x, y, w):
        """Block-Fluss: Block-Kinder untereinander, Inline-Inhalt in Zeilen."""
        cy = y
        zeile_inhalt: list = []
        vorige_unten = None
        erster = True

        def zeilen_abschliessen():
            nonlocal cy, zeile_inhalt, vorige_unten
            if zeile_inhalt:
                if any(not (isinstance(t, Text) and not t.text.strip()) for t in zeile_inhalt) or \
                        k.stil.get("white-space", "normal").startswith("pre"):
                    cy = self._zeilen(k, zeile_inhalt, x, cy, w)
                    vorige_unten = 0.0
                zeile_inhalt = []

        for kind in k.kinder:
            if isinstance(kind, Kasten) and kind.pos in ("absolute", "fixed"):
                continue
            if isinstance(kind, Text) or kind.anzeige in INLINE or (kind.el.tag in ERSETZT and kind.anzeige not in
                                                                    ("block", "flex", "grid")):
                zeile_inhalt.append(kind)
                continue
            zeilen_abschliessen()
            self._abstaende(kind, w)
            oben = kind.aussen[0]
            if vorige_unten is not None:
                einzug = max(vorige_unten, oben) - vorige_unten - oben       # Außenabstände fallen zusammen
            elif erster and k.rand[0] == 0 and k.polster[0] == 0 and k.el.tag not in ("html",):
                einzug = 0.0
            else:
                einzug = 0.0
            self._block(kind, x, cy + einzug, w, None)
            cy = kind.y + kind.h + kind.aussen[2] - (kind.pos == "relative" and 0)
            if kind.pos == "relative":
                cy = kind.y + kind.h + kind.aussen[2] - (laenge(kind.stil.get("top"), None, kind.schrift["size"],
                                                                self.ctx) or 0)
            vorige_unten = kind.aussen[2]
            erster = False
        zeilen_abschliessen()
        return cy - y

    # -- Zeilen (Inline-Formatierung) -------------------------------------------------------
    def _woerter(self, k_eltern, inhalt):
        """Inline-Inhalt → Liste von Stücken: ("wort", text, schrift, farbe, besitzer) / ("kasten", Kasten) /
        ("umbruch",)."""
        stuecke = []
        ws = k_eltern.stil.get("white-space", "normal")

        def text_von(t: str, schrift, farbe, besitzer, ws_):
            tt = besitzer.stil.get("text-transform") if isinstance(besitzer, Kasten) else None
            if tt == "uppercase":
                t = t.upper()
            elif tt == "lowercase":
                t = t.lower()
            elif tt == "capitalize":
                t = t.title()
            if ws_.startswith("pre"):
                for i, zeile in enumerate(t.split("\n")):
                    if i:
                        stuecke.append(("umbruch",))
                    if zeile:
                        stuecke.append(("wort", zeile, schrift, farbe, besitzer, False))
                return
            teile = re.split(r"(\s+)", t)
            for teil in teile:
                if not teil:
                    continue
                if teil.isspace():
                    stuecke.append(("raum", " ", schrift, farbe, besitzer, False))
                else:
                    stuecke.append(("wort", teil, schrift, farbe, besitzer, ws_ == "nowrap"))

        def durch(inh, schrift, farbe, besitzer, ws_):
            for c in inh:
                if isinstance(c, Text):
                    text_von(c.text, schrift, farbe, besitzer, ws_)
                elif c.el.tag == "br":
                    stuecke.append(("umbruch",))
                elif c.anzeige == "inline" and c.el.tag not in ERSETZT:
                    self._abstaende(c, 0)
                    cf = nemicss.farbe(c.stil.get("color"), (0, 0, 0, 255)) or farbe
                    if c.polster[3] or c.aussen[3] or c.rand[3]:
                        stuecke.append(("luecke", c.polster[3] + c.aussen[3] + c.rand[3], c))
                    stuecke.append(("start", c))
                    durch(c.kinder, c.schrift, cf, c, c.stil.get("white-space", ws_))
                    stuecke.append(("ende", c))
                    if c.polster[1] or c.aussen[1] or c.rand[1]:
                        stuecke.append(("luecke", c.polster[1] + c.aussen[1] + c.rand[1], c))
                else:
                    stuecke.append(("kasten", c))
        farbe = nemicss.farbe(k_eltern.stil.get("color"), (0, 0, 0, 255)) or (0, 0, 0, 255)
        durch(inhalt, k_eltern.schrift, farbe, k_eltern, ws)
        return stuecke

    def _zeilen(self, k: Kasten, inhalt, x, y, w):
        stuecke = self._woerter(k, inhalt)
        for st in stuecke:                     # Inline-Flächen neu sammeln
            if st[0] == "start":
                st[1].el.box = None
        zeilen: list[list] = [[]]
        breiten = [0.0]
        for st in stuecke:
            art = st[0]
            if art == "umbruch":
                zeilen.append([])
                breiten.append(0.0)
                continue
            if art in ("start", "ende"):
                zeilen[-1].append((st, 0.0))
                continue
            if art == "luecke":
                zeilen[-1].append((st, st[1]))
                breiten[-1] += st[1]
                continue
            if art == "kasten":
                c = st[1]
                self._block(c, 0, 0, w, None)
                bw = c.w + c.aussen[1] + c.aussen[3]
            else:
                bw = self.messer.breite(st[1], st[2]) + st[2].get("spacing", 0)
                if art == "raum" and not zeilen[-1]:
                    continue
            if breiten[-1] + bw > w + 0.5 and zeilen[-1] and art != "raum" and not (art == "wort" and st[5] and False):
                while zeilen[-1] and zeilen[-1][-1][0][0] == "raum":
                    breiten[-1] -= zeilen[-1][-1][1]
                    zeilen[-1].pop()
                zeilen.append([])
                breiten.append(0.0)
            zeilen[-1].append((st, bw))
            breiten[-1] += bw
        ausricht = k.stil.get("text-align", "left")
        cy = y
        for zeile, zb in zip(zeilen, breiten):
            while zeile and zeile[-1][0][0] == "raum":
                zb -= zeile[-1][1]
                zeile.pop()
            if not zeile:
                cy += k.schrift["line"] if not stuecke or zeilen.index(zeile) < len(zeilen) - 1 else 0
                continue
            zh = k.schrift["line"]
            for st, _b in zeile:
                if st[0] == "kasten":
                    c = st[1]
                    zh = max(zh, c.h + c.aussen[0] + c.aussen[2])
                elif st[0] in ("wort", "raum"):
                    zh = max(zh, st[2]["line"])
            frei = w - zb
            cx = x + (frei / 2 if ausricht == "center" else frei if ausricht in ("right", "end") else 0)
            for st, b in zeile:
                art = st[0]
                if art == "kasten":
                    c = st[1]
                    va = c.stil.get("vertical-align", "baseline")
                    oy = (zh - c.h - c.aussen[0] - c.aussen[2]) / 2 if va == "middle" else \
                        0 if va == "top" else zh - c.h - c.aussen[0] - c.aussen[2]
                    self._verschieben(c, cx + c.aussen[3] - c.x, cy + oy + c.aussen[0] - c.y)
                elif art in ("wort", "raum"):
                    schrift = st[2]
                    oy = (zh - schrift["line"]) / 2 + (schrift["line"] - self.messer.hoehe(schrift)) / 2
                    if art == "wort":
                        k.texte.append((cx, cy + oy, st[1], schrift, st[3]))
                        besitzer = st[4]
                        if isinstance(besitzer, Kasten) and besitzer is not k:
                            _inline_box(besitzer, cx, cy, b, zh)
                elif art == "luecke":
                    pass
                cx += b
            cy += zh
        k.zeilen = zeilen
        return cy

    # -- Ersetzte Elemente ------------------------------------------------------------------
    def _inhalt_breite(self, k: Kasten, cb_w) -> float:
        """Bevorzugte Breite (max-content) des Inhalts ohne Polster/Rand."""
        tag = k.el.tag
        fs = k.schrift["size"]
        if tag == "input":
            typ = k.el.attrs.get("type", "text")
            if typ in ("checkbox", "radio"):
                return 16.0
            if typ == "range":
                return 130.0
            if typ == "color":
                return 46.0
            if typ in ("button", "submit", "reset"):
                return self.messer.breite(k.el.attrs.get("value", "Senden"), k.schrift)
            return fs * 10
        if tag == "img":
            w = laenge(k.el.attrs.get("width"), None, fs, self.ctx)
            if w is None and self.bild_groesse:
                gr = self.bild_groesse(k.el)
                w = gr[0] if gr else 0.0
            return w or 0.0
        if tag in ("svg", "canvas"):
            return laenge(k.el.attrs.get("width"), None, fs, self.ctx) or (300.0 if tag == "canvas" else 24.0)
        if tag == "select":
            opts = [o.text_inhalt() for o in k.el.alle() if o.tag == "option"] or [""]
            return max(self.messer.breite(t, k.schrift) for t in opts) + fs * 1.6
        if tag == "textarea":
            return fs * 15
        breite = 0.0
        zeile = 0.0
        for st in self._woerter(k, k.kinder):
            if st[0] == "umbruch":
                breite, zeile = max(breite, zeile), 0.0
            elif st[0] in ("wort", "raum"):
                zeile += self.messer.breite(st[1], st[2])
            elif st[0] == "luecke":
                zeile += st[1]
            elif st[0] == "kasten":
                c = st[1]
                self._abstaende(c, cb_w)
                cw = self._breite_vorgabe(c, cb_w)
                if cw is None:
                    cw = self._inhalt_breite(c, cb_w)
                zeile += cw + c.polster[1] + c.polster[3] + c.rand[1] + c.rand[3] + c.aussen[1] + c.aussen[3]
        for c in k.kinder:                       # Block-Kinder (z. B. in einem inline-block)
            if isinstance(c, Kasten) and c.anzeige not in INLINE and c.el.tag not in ERSETZT and c.pos == "static":
                self._abstaende(c, cb_w)
                cw = self._breite_vorgabe(c, cb_w)
                if cw is None:
                    cw = self._inhalt_breite(c, cb_w)
                breite = max(breite, cw + c.polster[1] + c.polster[3] + c.rand[1] + c.rand[3] + c.aussen[1] + c.aussen[3])
        if k.anzeige in ("flex", "inline-flex") and k.stil.get("flex-direction", "row").startswith("row"):
            gap = laenge(k.stil.get("column-gap"), None, fs, self.ctx) or 0.0
            teile = []
            for c in k.kinder:
                if isinstance(c, Kasten) and c.pos == "static":
                    self._abstaende(c, cb_w)
                    cw = self._breite_vorgabe(c, cb_w)
                    if cw is None:
                        cw = self._inhalt_breite(c, cb_w)
                    teile.append(cw + c.polster[1] + c.polster[3] + c.rand[1] + c.rand[3] + c.aussen[1] + c.aussen[3])
            if teile:
                breite = max(breite, sum(teile) + gap * (len(teile) - 1))
        return max(breite, zeile)

    def _ersetzt_hoehe(self, k: Kasten, w, x=0.0, y=0.0) -> float:
        tag = k.el.tag
        fs = k.schrift["size"]
        if tag == "img":
            h = laenge(k.el.attrs.get("height"), None, fs, self.ctx)
            if h is None and self.bild_groesse:
                gr = self.bild_groesse(k.el)
                if gr and gr[0]:
                    h = gr[1] * w / gr[0]
            return h or 0.0
        if tag in ("svg", "canvas"):
            return laenge(k.el.attrs.get("height"), None, fs, self.ctx) or (150.0 if tag == "canvas" else 24.0)
        if tag == "textarea":
            return fs * 1.3 * int(k.el.attrs.get("rows", "3") or 3)
        if tag == "input" and k.el.attrs.get("type") in ("checkbox", "radio"):
            return 16.0
        if tag == "input" and k.el.attrs.get("type") == "range":
            return 20.0
        if tag == "input" and k.el.attrs.get("type") == "color":
            return 24.0
        if tag == "button":
            return self._fluss(k, x, y, w) if k.kinder else k.schrift["line"]
        return k.schrift["line"]

    # -- Flex ----------------------------------------------------------------------------
    def _flex(self, k: Kasten, x, y, w, h):
        s = k.stil
        fs = k.schrift["size"]
        richtung = s.get("flex-direction", "row")
        zeile = richtung.startswith("row")
        umbruch = s.get("flex-wrap", "nowrap") != "nowrap"
        gap_h = laenge(s.get("column-gap"), w, fs, self.ctx) or 0.0
        gap_v = laenge(s.get("row-gap"), h, fs, self.ctx) or 0.0
        kinder = [c for c in k.kinder if not (isinstance(c, Kasten) and c.pos in ("absolute", "fixed"))]
        posten = []
        for c in kinder:
            if isinstance(c, Text):
                if not c.text.strip():
                    continue
                c = self._anonym(k, c)
            posten.append(c)
        posten.sort(key=lambda c: _zahl(c.stil.get("order"), 0))
        if richtung.endswith("reverse"):
            posten.reverse()
        if not posten:
            return h or 0.0
        haupt = w if zeile else h
        maße = []
        for c in posten:
            self._abstaende(c, w)
            rp_h = (c.polster[1] + c.polster[3] + c.rand[1] + c.rand[3] + c.aussen[1] + c.aussen[3]) if zeile else \
                (c.polster[0] + c.polster[2] + c.rand[0] + c.rand[2] + c.aussen[0] + c.aussen[2])
            basis = c.stil.get("flex-basis", "auto")
            b = laenge(basis, haupt, c.schrift["size"], self.ctx) if basis not in ("auto", "content") else None
            if b is None:
                if zeile:
                    b = self._breite_vorgabe(c, w)
                    if b is None:
                        b = self._inhalt_breite(c, w)
                else:
                    b = laenge(c.stil.get("height"), h, c.schrift["size"], self.ctx)
                    if b is None:
                        self._block(c, 0, 0, w, None)
                        b = c.h - c.polster[0] - c.polster[2] - c.rand[0] - c.rand[2]
            maße.append([c, b, rp_h, _zahl(c.stil.get("flex-grow"), 0), _zahl(c.stil.get("flex-shrink"), 1)])
        # Zeilen bilden
        reihen, aktuell, summe = [], [], 0.0
        for m in maße:
            groesse = m[1] + m[2]
            if umbruch and aktuell and haupt is not None and summe + groesse + gap_h * len(aktuell) > haupt + 0.5:
                reihen.append(aktuell)
                aktuell, summe = [], 0.0
            aktuell.append(m)
            summe += groesse
        reihen.append(aktuell)
        justify = s.get("justify-content", "flex-start")
        align = s.get("align-items", "stretch")
        cy = 0.0
        reihen_lagen = []
        for reihe in reihen:
            gaps = (gap_h if zeile else gap_v) * (len(reihe) - 1)
            summe = sum(m[1] + m[2] for m in reihe) + gaps
            if haupt is not None:
                frei = haupt - summe
                if frei > 0:
                    gesamt_grow = sum(m[3] for m in reihe)
                    if gesamt_grow > 0:
                        for m in reihe:
                            m[1] += frei * m[3] / gesamt_grow
                        frei = 0.0
                elif frei < 0:
                    gesamt = sum(m[4] * m[1] for m in reihe)
                    if gesamt > 0:
                        for m in reihe:
                            m[1] = max(0.0, m[1] + frei * m[4] * m[1] / gesamt)
                        frei = 0.0
            else:
                frei = 0.0
            # Größen festlegen, Querachse messen
            quer = 0.0
            for m in reihe:
                c = m[0]
                if zeile:
                    self._block(c, 0, 0, w, h, fest_w=self._begrenzen(c, m[1], w))
                    quer = max(quer, c.h + c.aussen[0] + c.aussen[2])
                else:
                    self._block(c, 0, 0, w, None)
                    quer = max(quer, c.w + c.aussen[1] + c.aussen[3])
            reihen_lagen.append((reihe, frei, quer))
        if zeile and h is not None and len(reihen_lagen) == 1:
            reihen_lagen[0] = (reihen_lagen[0][0], reihen_lagen[0][1], max(reihen_lagen[0][2], h))
        for reihe, frei, quer in reihen_lagen:
            n = len(reihe)
            start, zwischen = 0.0, 0.0
            if justify in ("center",):
                start = frei / 2
            elif justify in ("flex-end", "end", "right"):
                start = frei
            elif justify == "space-between" and n > 1:
                zwischen = frei / (n - 1)
            elif justify == "space-around":
                zwischen = frei / n
                start = zwischen / 2
            elif justify == "space-evenly":
                zwischen = frei / (n + 1)
                start = zwischen
            pos = start
            for m in reihe:
                c = m[0]
                selbst = c.stil.get("align-self", "auto")
                a = align if selbst == "auto" else selbst
                if zeile:
                    ch = c.h + c.aussen[0] + c.aussen[2]
                    if a == "stretch" and c.stil.get("height") in (None, "auto"):
                        neu_h = quer - c.aussen[0] - c.aussen[2]
                        if abs(neu_h - c.h) > 0.5:
                            fest = self._begrenzen(c, m[1], w)
                            c.stil = dict(c.stil, height=f"{neu_h}px", **({"box-sizing": "border-box"}))
                            self._block(c, 0, 0, w, h, fest_w=fest)
                        ch = c.h + c.aussen[0] + c.aussen[2]
                    oy = (quer - ch) / 2 if a == "center" else (quer - ch) if a in ("flex-end", "end") else 0.0
                    self._verschieben(c, x + pos + c.aussen[3] - c.x, y + cy + oy + c.aussen[0] - c.y)
                    pos += c.w + c.aussen[1] + c.aussen[3] + gap_h + zwischen
                else:
                    cw = c.w + c.aussen[1] + c.aussen[3]
                    if a == "stretch" and c.stil.get("width") in (None, "auto"):
                        self._block(c, 0, 0, w, None, fest_w=max(0.0, w - c.aussen[1] - c.aussen[3] - c.polster[1] -
                                                                  c.polster[3] - c.rand[1] - c.rand[3]))
                        cw = c.w + c.aussen[1] + c.aussen[3]
                    ox = (w - cw) / 2 if a == "center" else (w - cw) if a in ("flex-end", "end") else 0.0
                    self._verschieben(c, x + ox + c.aussen[3] - c.x, y + pos + c.aussen[0] - c.y)
                    pos += c.h + c.aussen[0] + c.aussen[2] + gap_v + zwischen
            if zeile:
                cy += quer + gap_v
            else:
                cy = pos - gap_v - zwischen if reihe else 0.0
        if zeile:
            hoehe = cy - gap_v
            return max(hoehe, h or 0.0) if s.get("height") in (None, "auto") else hoehe
        return max(cy, h or 0.0)

    def _anonym(self, k, text):
        a = Kasten(k.el, {"display": "block", "color": k.stil.get("color", "#000"),
                          **{x: k.stil[x] for x in nemicss.VERERBT if x in k.stil}}, "block")
        a.schrift = k.schrift
        a.kinder = [text]
        a.fest = True
        return a

    # -- Grid ----------------------------------------------------------------------------
    def _grid(self, k: Kasten, x, y, w, h):
        s = k.stil
        fs = k.schrift["size"]
        gap_h = laenge(s.get("column-gap"), w, fs, self.ctx) or 0.0
        gap_v = laenge(s.get("row-gap"), h, fs, self.ctx) or 0.0
        posten = [c for c in k.kinder if isinstance(c, Kasten) and c.pos not in ("absolute", "fixed")]
        posten += [self._anonym(k, c) for c in k.kinder if isinstance(c, Text) and c.text.strip()]
        spalten = self._spalten(s.get("grid-template-columns", "none"), w, gap_h, fs)
        n = max(1, len(spalten))
        ji = s.get("justify-items", "stretch")
        ai = s.get("align-items", "stretch")
        reihen = [posten[i:i + n] for i in range(0, len(posten), n)] or [[]]
        hoehen = []
        for reihe in reihen:
            rh = 0.0
            for i, c in enumerate(reihe):
                sw = spalten[i] if i < len(spalten) else w
                js_ = c.stil.get("justify-self", "auto")
                j = ji if js_ == "auto" else js_
                if j in ("center", "start", "end", "left", "right", "flex-start", "flex-end") \
                        or c.stil.get("width") not in (None, "auto"):
                    self._block(c, 0, 0, sw, None)
                    if c.stil.get("width") in (None, "auto") and c.el.tag not in ERSETZT:
                        self._abstaende(c, sw)
                        bw = min(self._inhalt_breite(c, sw), sw - c.polster[1] - c.polster[3] - c.rand[1] - c.rand[3])
                        self._block(c, 0, 0, sw, None, fest_w=max(0.0, bw))
                else:
                    self._block(c, 0, 0, sw, None, fest_w=max(0.0, sw - c.aussen[1] - c.aussen[3] - c.polster[1] -
                                                              c.polster[3] - c.rand[1] - c.rand[3]))
                rh = max(rh, c.h + c.aussen[0] + c.aussen[2])
            hoehen.append(rh)
        summe = sum(hoehen) + gap_v * (len(hoehen) - 1)
        if h is not None and summe < h and s.get("align-content", "normal") in ("normal", "stretch"):
            extra = (h - summe) / len(hoehen)
            hoehen = [rh + extra for rh in hoehen]
            summe = h
        cy = y
        for reihe, rh in zip(reihen, hoehen):
            cx = x
            for i, c in enumerate(reihe):
                sw = spalten[i] if i < len(spalten) else w
                js_ = c.stil.get("justify-self", "auto")
                j = ji if js_ == "auto" else js_
                as_ = c.stil.get("align-self", "auto")
                a = ai if as_ == "auto" else as_
                cw, ch = c.w + c.aussen[1] + c.aussen[3], c.h + c.aussen[0] + c.aussen[2]
                ox = (sw - cw) / 2 if j == "center" else (sw - cw) if j in ("end", "right", "flex-end") else 0.0
                oy = (rh - ch) / 2 if a == "center" else (rh - ch) if a in ("end", "flex-end") else 0.0
                self._verschieben(c, cx + ox + c.aussen[3] - c.x, cy + oy + c.aussen[0] - c.y)
                cx += sw + gap_h
            cy += rh + gap_v
        return max(summe, h or 0.0)

    def _spalten(self, vorlage: str, w, gap, fs) -> list[float]:
        if not vorlage or vorlage == "none":
            return [w]
        teile = []
        for t in nemicss.werte(vorlage):
            m = re.fullmatch(r"repeat\(\s*([^,]+),(.*)\)", t)
            if m:
                anzahl, muster = m.group(1).strip(), nemicss.werte(m.group(2))
                if anzahl in ("auto-fill", "auto-fit"):
                    mm = re.fullmatch(r"minmax\(([^,]+),(.*)\)", muster[0]) if muster else None
                    mind = laenge(mm.group(1) if mm else muster[0], w, fs, self.ctx) or 100.0
                    n = max(1, int((w + gap) // (mind + gap)))
                    teile += ["1fr"] * n
                else:
                    teile += muster * max(1, int(_zahl(anzahl, 1)))
            else:
                teile.append(t)
        fest, fr = 0.0, 0.0
        werte_ = []
        for t in teile:
            mm = re.fullmatch(r"minmax\(([^,]+),(.*)\)", t)
            if mm:
                t = mm.group(2).strip()
            if t.endswith("fr"):
                f = _zahl(t[:-2], 1)
                werte_.append(("fr", f))
                fr += f
            else:
                v = laenge(t, w, fs, self.ctx)
                if v is None:
                    werte_.append(("fr", 1.0))
                    fr += 1
                else:
                    werte_.append(("px", v))
                    fest += v
        rest = max(0.0, w - fest - gap * (len(werte_) - 1))
        return [v if a == "px" else (rest * v / fr if fr else 0.0) for a, v in werte_]

    def _zeile_tabelle(self, k, x, y, w):
        zellen = [c for c in k.kinder if isinstance(c, Kasten)]
        if not zellen:
            return 0.0
        sw = w / len(zellen)
        hoehe = 0.0
        for i, c in enumerate(zellen):
            self._block(c, x + i * sw, y, sw, None, fest_w=max(0.0, sw - 12))
            hoehe = max(hoehe, c.h)
        return hoehe

    # -- Absolut positioniert ---------------------------------------------------------------
    def _absolute(self, k: Kasten, cb=None):
        cb = cb or (0.0, 0.0, self.breite, self.hoehe)
        eigener = (k.x, k.y, k.w, k.h) if k.pos != "static" else cb
        for c in k.kinder:
            if not isinstance(c, Kasten):
                continue
            if c.pos in ("absolute", "fixed"):
                bezug = (0.0, 0.0, self.breite, self.hoehe) if c.pos == "fixed" else eigener
                bx, by, bw, bh = bezug
                fs = c.schrift["size"]
                self._abstaende(c, bw)
                links = laenge(c.stil.get("left"), bw, fs, self.ctx)
                rechts = laenge(c.stil.get("right"), bw, fs, self.ctx)
                oben = laenge(c.stil.get("top"), bh, fs, self.ctx)
                unten = laenge(c.stil.get("bottom"), bh, fs, self.ctx)
                fest_w = None
                if c.stil.get("width") in (None, "auto"):
                    if links is not None and rechts is not None:
                        fest_w = max(0.0, bw - links - rechts - c.polster[1] - c.polster[3] - c.rand[1] - c.rand[3]
                                     - c.aussen[1] - c.aussen[3])
                    else:
                        fest_w = self._inhalt_breite(c, bw)
                self._block(c, 0, 0, bw, bh, fest_w=fest_w)
                if c.stil.get("height") in (None, "auto") and oben is not None and unten is not None:
                    c.stil = dict(c.stil, height=f"{max(0.0, bh - oben - unten)}px", **{"box-sizing": "border-box"})
                    self._block(c, 0, 0, bw, bh, fest_w=fest_w)
                zx = bx + (links if links is not None else (bw - c.w - rechts - c.aussen[1] - c.aussen[3]
                                                             if rechts is not None else 0.0))
                zy = by + (oben if oben is not None else (bh - c.h - unten - c.aussen[0] - c.aussen[2]
                                                          if unten is not None else 0.0))
                self._verschieben(c, zx - c.x + c.aussen[3], zy - c.y + c.aussen[0])
                c.fest = c.pos == "fixed"
            self._absolute(c, (c.x, c.y, c.w, c.h) if c.pos != "static" else (eigener if k.pos != "static" else cb))

    # -- Suchen ----------------------------------------------------------------------------
    def treffer(self, px, py) -> Element | None:
        """Oberstes Element unter dem Punkt (Dokument-Koordinaten)."""
        beste = None
        beste_rang = None
        for rang, kasten in enumerate(self.alle):
            el = kasten.el
            if el.box is None or kasten.stil.get("pointer-events") == "none" or kasten.stil.get("visibility") == "hidden":
                continue
            x, y, w, h = el.box
            if x <= px < x + w and y <= py < y + h:
                r = (kasten.z if kasten.pos != "static" else 0, rang)
                if beste_rang is None or r >= beste_rang:
                    beste, beste_rang = el, r
        return beste


def _inline_box(besitzer: Kasten, x, y, w, h):
    """Inline-Element (span, a …) bekommt die Fläche seiner Textstücke (für Hintergrund und Treffer)."""
    if besitzer.el.box is None:
        besitzer.x, besitzer.y, besitzer.w, besitzer.h = x, y, w, h
    else:
        x0 = min(besitzer.x, x)
        y0 = min(besitzer.y, y)
        besitzer.w = max(besitzer.x + besitzer.w, x + w) - x0
        besitzer.h = max(besitzer.y + besitzer.h, y + h) - y0
        besitzer.x, besitzer.y = x0, y0
    besitzer.el.box = (besitzer.x, besitzer.y, besitzer.w, besitzer.h)


def _zahl(v, vorgabe):
    try:
        return float(v)
    except (TypeError, ValueError):
        return vorgabe


_ = math
