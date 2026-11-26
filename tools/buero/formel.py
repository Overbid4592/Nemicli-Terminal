"""formel.py – sicherer Formel-Rechner für Tabellen (eigener Zerleger, kein eval).

Schreibweisen: deutsch  =SUMME(A1:A3;2,5)   englisch  =SUM(A1:A3,2.5)   OpenDocument  of:=SUM([.A1:.A3];2.5)
Mit „;“ in der Formel ist „,“ das Dezimalzeichen, sonst trennt „,“ die Argumente. Bezüge auf andere Blätter:
Blatt2!A1, 'Mein Blatt'!A1:B3. Gespeichert wird die deutsche Form; beim Schreiben von .xlsx/.ods übersetzt.

Grenzen: Formellänge, Verschachtelung, Zellen je Bereich und Textlänge sind gedeckelt; Fehler kommen als Wert
(#WERT!, #DIV/0!, …) statt als Ausnahme.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass

MAX_FORMEL = 8000
MAX_TIEFE = 64
MAX_BEREICH = 250_000
MAX_TEXT = 32767
MAX_SPALTE = 16384
MAX_ZEILE = 1_048_576
DATUM_NULL = _dt.date(1899, 12, 30)


class Fehler(str):
    """Fehlerwert einer Zelle, z. B. Fehler("#DIV/0!")."""


DIV0, WERT, NAME, BEZUG, ZAHL, NV, ZIRKEL = (Fehler(c) for c in ("#DIV/0!", "#WERT!", "#NAME?", "#BEZUG!", "#ZAHL!",
                                                               "#NV", "#ZIRKEL!"))
FEHLER = {f: f for f in (DIV0, WERT, NAME, BEZUG, ZAHL, NV, ZIRKEL)} | {
    "#VALUE!": WERT, "#REF!": BEZUG, "#NUM!": ZAHL, "#N/A": NV}


class Datum(float):
    """Tageszahl wie in Tabellenprogrammen (1 = 31.12.1899), angezeigt als TT.MM.JJJJ."""

    def datum(self) -> _dt.date | None:
        try:
            return DATUM_NULL + _dt.timedelta(days=int(self))
        except (OverflowError, ValueError):
            return None

    @classmethod
    def aus(cls, d: _dt.date) -> "Datum":
        return cls((d - DATUM_NULL).days)


class _Abbruch(Exception):
    def __init__(self, fehler: Fehler):
        self.fehler = fehler


# ---------------------------------------------------------------------------
# Spalten und Bezüge
# ---------------------------------------------------------------------------

def spalte_nr(buchstaben: str) -> int:
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    n = 0
    for ch in buchstaben.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def spalte_name(nr: int) -> str:
    s, nr = "", nr + 1
    while nr:
        nr, rest = divmod(nr - 1, 26)
        s = chr(65 + rest) + s
    return s


def zelle_name(zeile: int, spalte: int) -> str:
    return f"{spalte_name(spalte)}{zeile + 1}"


def zelle_aus(name: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?(\d{1,7})", name.strip())
    if not m:
        return None
    s, z = spalte_nr(m.group(1)), int(m.group(2)) - 1
    if not (0 <= s < MAX_SPALTE and 0 <= z < MAX_ZEILE):
        return None
    return z, s


# ---------------------------------------------------------------------------
# Zerlegen
# ---------------------------------------------------------------------------

@dataclass
class Tok:
    art: str            # zahl text bool ref bereich fn name op auf zu sep fehler
    wert: object
    roh: str = ""


_BLATT = r"(?:'(?:[^']|'')+'|[A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß.]*)"
_REF = r"\$?[A-Za-z]{1,3}\$?\d{1,7}"
_RX_BEREICH = re.compile(rf"(?:({_BLATT})!)?({_REF}):(?:(?:{_BLATT})!)?({_REF})(?![\w(])")
_RX_REF = re.compile(rf"(?:({_BLATT})!)?({_REF})(?![\w(])")
_RX_ODS = re.compile(r"\[(\$?(?:'(?:[^']|'')+'|[^.\]:']*))\.(\$?[A-Za-z]{1,3}\$?\d{1,7})"
                     r"(?::\$?(?:'(?:[^']|'')+'|[^.\]:']*)\.(\$?[A-Za-z]{1,3}\$?\d{1,7}))?\]")
_RX_NAME = re.compile(r"[A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß.]*")
_RX_FEHLER = re.compile(r"#(?:DIV/0!|WERT!|VALUE!|NAME\?|BEZUG!|REF!|ZAHL!|NUM!|NV|N/A|ZIRKEL!)")
_OPS = ("<>", "<=", ">=", "+", "-", "*", "/", "^", "&", "=", "<", ">", "%")


def _blatt_name(roh: str | None) -> str | None:
    if not roh:
        return None
    roh = roh.lstrip("$")
    if roh.startswith("'") and roh.endswith("'"):
        return roh[1:-1].replace("''", "'")
    return roh or None


def zerlegen(formel: str) -> list[Tok]:
    """Formel (mit oder ohne führendes = / of:=) in Teile zerlegen. Wirft _Abbruch(NAME) bei Unsinn."""
    s = formel.strip()
    if s.lower().startswith("of:"):
        s = s[3:]
    if s.startswith("="):
        s = s[1:]
    if len(s) > MAX_FORMEL:
        raise _Abbruch(WERT)
    deutsch = _deutsch(s)
    toks: list[Tok] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            j, teile = i + 1, []
            while True:
                k = s.find('"', j)
                if k < 0:
                    raise _Abbruch(WERT)
                teile.append(s[j:k])
                if s.startswith('""', k):
                    teile.append('"')
                    j = k + 2
                    continue
                i = k + 1
                break
            toks.append(Tok("text", "".join(teile)))
            continue
        if ch == "[":
            m = _RX_ODS.match(s, i)
            if not m:
                raise _Abbruch(BEZUG)
            blatt = _blatt_name(m.group(1))
            a = zelle_aus(m.group(2))
            if a is None:
                raise _Abbruch(BEZUG)
            if m.group(3):
                b = zelle_aus(m.group(3))
                if b is None:
                    raise _Abbruch(BEZUG)
                toks.append(Tok("bereich", (blatt, a, b), f"{m.group(2)}:{m.group(3)}"))
            else:
                toks.append(Tok("ref", (blatt, a), m.group(2)))
            i = m.end()
            continue
        if ch == "#":
            m = _RX_FEHLER.match(s, i)
            if not m:
                raise _Abbruch(NAME)
            toks.append(Tok("fehler", FEHLER[m.group(0)]))
            i = m.end()
            continue
        if ch.isdigit() or (ch == "." and i + 1 < len(s) and s[i + 1].isdigit()):
            m = (_RX_ZAHL_DE if deutsch else _RX_ZAHL_EN).match(s, i)
            if m:
                roh = m.group(0)
                try:
                    toks.append(Tok("zahl", float(roh.replace(",", ".")), roh))
                except ValueError:
                    raise _Abbruch(WERT) from None
                i = m.end()
                continue
        if ch in "'$" or ch.isalpha() or ch == "_":
            m = _RX_BEREICH.match(s, i)
            if m:
                a, b = zelle_aus(m.group(2)), zelle_aus(m.group(3))
                if a is None or b is None:
                    raise _Abbruch(BEZUG)
                toks.append(Tok("bereich", (_blatt_name(m.group(1)), a, b), f"{m.group(2)}:{m.group(3)}"))
                i = m.end()
                continue
            m = _RX_REF.match(s, i)
            if m:
                a = zelle_aus(m.group(2))
                if a is None:
                    raise _Abbruch(BEZUG)
                toks.append(Tok("ref", (_blatt_name(m.group(1)), a), m.group(2)))
                i = m.end()
                continue
            m = _RX_NAME.match(s, i)
            if m:
                name = m.group(0)
                j = m.end()
                while j < len(s) and s[j].isspace():
                    j += 1
                if j < len(s) and s[j] == "(":
                    toks.append(Tok("fn", _fn_name(name), name))
                    toks.append(Tok("auf", "("))
                    i = j + 1
                    continue
                if name.upper() in ("WAHR", "TRUE", "FALSCH", "FALSE"):
                    toks.append(Tok("bool", name.upper() in ("WAHR", "TRUE"), name))
                    i = m.end()
                    continue
                raise _Abbruch(NAME)
        if ch == "(":
            toks.append(Tok("auf", "("))
            i += 1
            continue
        if ch == ")":
            toks.append(Tok("zu", ")"))
            i += 1
            continue
        if ch in ";,":                          # deutsche Dezimalzahlen hat der Zahlen-Zweig schon genommen
            toks.append(Tok("sep", ";"))
            i += 1
            continue
        op = next((o for o in _OPS if s.startswith(o, i)), None)
        if op:
            toks.append(Tok("op", op))
            i += len(op)
            continue
        raise _Abbruch(NAME)
    return toks


_RX_ZAHL_DE = re.compile(r"\d*[.,]?\d+(?:[eE][+-]?\d+)?")
_RX_ZAHL_EN = re.compile(r"\d*\.?\d+(?:[eE][+-]?\d+)?")


def _deutsch(s: str) -> bool:
    """Deutsche Schreibweise (Komma = Dezimalzeichen)? Ja bei „;“, nein bei englischen Funktionsnamen,
    ja bei einem Komma außerhalb jeder Klammer (=A1*1,19); sonst deutsch, wenn ein deutscher Name vorkommt."""
    ohne_text = re.sub(r'"(?:[^"]|"")*"', '""', s)
    if ";" in ohne_text:
        return True
    namen = {n.upper() for n in re.findall(r"([A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß.]*)\s*\(", ohne_text)}
    namen = {n.split(".")[-1] for n in namen}
    if namen & _NUR_EN:
        return False
    tiefe = 0
    for ch in ohne_text:
        tiefe += (ch == "(") - (ch == ")")
        if ch == "," and tiefe == 0:
            return True
    return bool(namen & set(_DE_EN)) or "," not in ohne_text


# ---------------------------------------------------------------------------
# Funktionsnamen (deutsch <-> englisch)
# ---------------------------------------------------------------------------

_DE_EN = {
    "SUMME": "SUM", "MITTELWERT": "AVERAGE", "MIN": "MIN", "MAX": "MAX", "ANZAHL": "COUNT", "ANZAHL2": "COUNTA",
    "PRODUKT": "PRODUCT", "RUNDEN": "ROUND", "AUFRUNDEN": "ROUNDUP", "ABRUNDEN": "ROUNDDOWN", "ABS": "ABS",
    "WURZEL": "SQRT", "POTENZ": "POWER", "REST": "MOD", "GANZZAHL": "INT", "WENN": "IF", "UND": "AND",
    "ODER": "OR", "NICHT": "NOT", "WAHR": "TRUE", "FALSCH": "FALSE", "VERKETTEN": "CONCATENATE",
    "TEXTKETTE": "CONCAT", "LÄNGE": "LEN", "LINKS": "LEFT", "RECHTS": "RIGHT", "TEIL": "MID", "GROSS": "UPPER",
    "KLEIN": "LOWER", "GLÄTTEN": "TRIM", "ZÄHLENWENN": "COUNTIF", "SUMMEWENN": "SUMIF",
    "MITTELWERTWENN": "AVERAGEIF", "WENNFEHLER": "IFERROR", "HEUTE": "TODAY", "DATUM": "DATE", "JAHR": "YEAR",
    "MONAT": "MONTH", "TAG": "DAY", "ANZAHLLEEREZELLEN": "COUNTBLANK", "MEDIAN": "MEDIAN", "PI": "PI",
}
_EN_DE = {v: k for k, v in _DE_EN.items()}
_NUR_EN = set(_EN_DE) - set(_DE_EN)
_SCHREIBWEISEN = {"LAENGE": "LÄNGE", "GLAETTEN": "GLÄTTEN", "ZAEHLENWENN": "ZÄHLENWENN", "CONCAT": "TEXTKETTE",
                  "VERKETTEN": "VERKETTEN"}


def _fn_name(roh: str) -> str:
    """Einheitlicher (deutscher) Name; unbekannte Namen bleiben in Großbuchstaben."""
    n = roh.upper()
    for vor in ("_XLFN._XLWS.", "_XLFN.", "COM.MICROSOFT.", "ORG.OPENOFFICE."):
        if n.startswith(vor):
            n = n[len(vor):]
    n = _SCHREIBWEISEN.get(n, n)
    return _EN_DE.get(n, n) if n not in _DE_EN else n


# ---------------------------------------------------------------------------
# Lesen (Baum)
# ---------------------------------------------------------------------------

_BINAER = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5}


class _Leser:
    def __init__(self, toks: list[Tok]):
        self.t = toks
        self.i = 0
        self.tiefe = 0

    def guck(self) -> Tok | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def nimm(self) -> Tok:
        tok = self.guck()
        if tok is None:
            raise _Abbruch(WERT)
        self.i += 1
        return tok

    def ausdruck(self, mind: int = 0):
        self.tiefe += 1
        if self.tiefe > MAX_TIEFE:
            raise _Abbruch(WERT)
        links = self.einzeln()
        while True:
            tok = self.guck()
            if tok is None or tok.art != "op" or tok.wert not in _BINAER:
                break
            rang = _BINAER[tok.wert]
            if rang < mind:
                break
            self.i += 1
            rechts = self.ausdruck(rang + 1)              # alle linksassoziativ, auch ^ (wie in Tabellen)
            links = ("bin", tok.wert, links, rechts)
        self.tiefe -= 1
        return links

    def einzeln(self):
        tok = self.nimm()
        if tok.art == "op" and tok.wert in ("-", "+"):
            innen = self.einzeln()
            knoten = ("neg", innen) if tok.wert == "-" else innen
            return self._prozent(knoten)
        if tok.art == "zahl":
            return self._prozent(("wert", tok.wert))
        if tok.art in ("text", "bool", "fehler"):
            return self._prozent(("wert", tok.wert))
        if tok.art == "ref":
            return self._prozent(("ref",) + tok.wert)
        if tok.art == "bereich":
            return ("bereich",) + tok.wert
        if tok.art == "auf":
            innen = self.ausdruck()
            if self.nimm().art != "zu":
                raise _Abbruch(WERT)
            return self._prozent(innen)
        if tok.art == "fn":
            self.nimm()                                    # "("
            args = []
            if (g := self.guck()) is not None and g.art == "zu":
                self.i += 1
                return self._prozent(("fn", tok.wert, args))
            while True:
                g = self.guck()
                if g is not None and g.art in ("sep", "zu"):
                    args.append(("leer",))
                else:
                    args.append(self.ausdruck())
                trenner = self.nimm()
                if trenner.art == "zu":
                    break
                if trenner.art != "sep":
                    raise _Abbruch(WERT)
                if len(args) > 255:
                    raise _Abbruch(WERT)
            return self._prozent(("fn", tok.wert, args))
        raise _Abbruch(WERT)

    def _prozent(self, knoten):
        while (g := self.guck()) is not None and g.art == "op" and g.wert == "%":
            self.i += 1
            knoten = ("bin", "/", knoten, ("wert", 100.0))
        return knoten


def baum(formel: str):
    toks = zerlegen(formel)
    if not toks:
        raise _Abbruch(WERT)
    leser = _Leser(toks)
    b = leser.ausdruck()
    if leser.guck() is not None:
        raise _Abbruch(WERT)
    return b


def bezuege(knoten, blatt: str) -> list[tuple]:
    """Alle Zellen und Bereiche, auf die ein Baum zeigt: ("z", blatt, zeile, spalte) / ("b", blatt, a, b)."""
    raus, stapel = [], [knoten]
    while stapel:
        k = stapel.pop()
        art = k[0]
        if art == "ref":
            raus.append(("z", k[1] or blatt, k[2][0], k[2][1]))
        elif art == "bereich":
            raus.append(("b", k[1] or blatt, k[2], k[3]))
        elif art == "bin":
            stapel += [k[2], k[3]]
        elif art == "neg":
            stapel.append(k[1])
        elif art == "fn":
            stapel += k[2]
    return raus


# ---------------------------------------------------------------------------
# Rechnen
# ---------------------------------------------------------------------------

def ist_fehler(v) -> bool:
    return isinstance(v, Fehler)


def zahl_aus_text(t: str):
    """Zahl, Prozent, Betrag oder Datum aus einer Eingabe (deutsch oder englisch); None wenn keins davon."""
    s = t.strip().replace("\u00a0", " ")
    if not s:
        return None
    if (d := datum_aus_text(s)) is not None:
        return d
    prozent = s.endswith("%")
    s = s.rstrip("% ").strip()
    for w in ("€", "EUR", "$", "USD", "£", "CHF"):
        if s.endswith(w):
            s = s[: -len(w)].strip()
        elif s.startswith(w):
            s = s[len(w):].strip()
    s = s.replace(" ", "")
    if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):          # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+\.\d+", s):            # 1,234.56
        s = s.replace(",", "")
    elif re.fullmatch(r"[+-]?\d*,\d+", s):                              # 12,5
        s = s.replace(",", ".")
    elif not re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", s):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return v / 100 if prozent else v


def datum_aus_text(s: str) -> Datum | None:
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", s) or None
    if m:
        t, mo, j = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if len(m.group(3)) == 2:
            j += 2000 if j < 70 else 1900
    else:
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
        if not m:
            return None
        j, mo, t = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return Datum.aus(_dt.date(j, mo, t))
    except ValueError:
        return None


def als_zahl(v) -> float:
    if ist_fehler(v):
        raise _Abbruch(v)
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, float):
        return v
    if isinstance(v, int):
        return float(v)
    z = zahl_aus_text(str(v))
    if z is None:
        raise _Abbruch(WERT)
    return z


def als_text(v) -> str:
    if ist_fehler(v):
        raise _Abbruch(v)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "WAHR" if v else "FALSCH"
    if isinstance(v, (int, float)):
        return zahl_text(v)
    return str(v)


def als_bool(v) -> bool:
    if ist_fehler(v):
        raise _Abbruch(v)
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    t = str(v).strip().upper()
    if t in ("WAHR", "TRUE"):
        return True
    if t in ("FALSCH", "FALSE", ""):
        return False
    raise _Abbruch(WERT)


def zahl_text(v: float) -> str:
    """Anzeige einer Zahl: deutsches Komma, ohne überflüssige Nullen; Datum als TT.MM.JJJJ."""
    if isinstance(v, Datum) and (d := v.datum()) is not None:
        return d.strftime("%d.%m.%Y")
    if isinstance(v, bool):
        return "WAHR" if v else "FALSCH"
    if not math.isfinite(v):
        return str(ZAHL)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    t = f"{v:.10g}"
    if "e" in t:
        return t.replace(".", ",")
    return t.replace(".", ",")


class Rechner:
    """Wertet Bäume aus. `wert(blatt, zeile, spalte)` liefert den Wert einer Zelle (aus der Mappe),
    `zellen(blatt, a, b)` die Werte der belegten Zellen eines Bereichs, `blatt_da(name)` prüft Blattnamen."""

    def __init__(self, wert, zellen, blatt_da, blatt: str):
        self.wert_von = wert
        self.zellen_von = zellen
        self.blatt_da = blatt_da
        self.blatt = blatt

    def rechnen(self, knoten):
        try:
            v = self._w(knoten)
            if isinstance(v, list):                        # Bereich allein: erste Zelle wie in Tabellen üblich
                v = v[0] if v else None
            if isinstance(v, float) and not math.isfinite(v):
                return ZAHL
            if isinstance(v, str) and not isinstance(v, Fehler) and len(v) > MAX_TEXT:
                return WERT
            return 0.0 if v is None else v
        except _Abbruch as a:
            return a.fehler
        except (OverflowError, ZeroDivisionError):
            return ZAHL
        except RecursionError:
            return WERT

    def _blatt(self, name):
        b = name or self.blatt
        if not self.blatt_da(b):
            raise _Abbruch(BEZUG)
        return b

    def _w(self, k):
        art = k[0]
        if art == "wert":
            return k[1]
        if art == "leer":
            return None
        if art == "ref":
            return self.wert_von(self._blatt(k[1]), k[2][0], k[2][1])
        if art == "bereich":
            (z1, s1), (z2, s2) = k[2], k[3]                # nur belegte Zellen zählen – auch A:XFD geht
            werte = self.zellen_von(self._blatt(k[1]), (min(z1, z2), min(s1, s2)), (max(z1, z2), max(s1, s2)))
            if len(werte) > MAX_BEREICH:
                raise _Abbruch(BEZUG)
            return werte
        if art == "neg":
            v = self._w(k[1])
            return -als_zahl(_einzeln(v))
        if art == "bin":
            return self._binaer(k[1], _einzeln(self._w(k[2])), _einzeln(self._w(k[3])))
        if art == "fn":
            return self._fn(k[1], k[2])
        raise _Abbruch(WERT)

    def _binaer(self, op, a, b):
        if ist_fehler(a):
            raise _Abbruch(a)
        if ist_fehler(b):
            raise _Abbruch(b)
        if op == "&":
            t = als_text(a) + als_text(b)
            if len(t) > MAX_TEXT:
                raise _Abbruch(WERT)
            return t
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return _vergleich(op, a, b)
        x, y = als_zahl(a), als_zahl(b)
        if op == "+":
            r = x + y
            return Datum(r) if isinstance(a, Datum) ^ isinstance(b, Datum) else r
        if op == "-":
            r = x - y
            return Datum(r) if isinstance(a, Datum) and not isinstance(b, Datum) else r
        if op == "*":
            return x * y
        if op == "/":
            if y == 0:
                raise _Abbruch(DIV0)
            return x / y
        if op == "^":
            if x == 0 and y < 0:
                raise _Abbruch(DIV0)
            if abs(y) > 1e6 and abs(x) > 1:
                raise _Abbruch(ZAHL)
            r = math.pow(x, y)
            return r
        raise _Abbruch(WERT)

    # -- Funktionen -----------------------------------------------------------------------
    def _alle(self, args, nur_zahlen: bool = True) -> list:
        """Werte aller Argumente; aus Bereichen nur Zahlen (Text und Leeres zählen nicht)."""
        raus = []
        for a in args:
            v = self._w(a)
            if isinstance(v, list):
                for x in v:
                    if ist_fehler(x):
                        raise _Abbruch(x)
                    if isinstance(x, float) and not isinstance(x, bool):
                        raus.append(x)
                    elif not nur_zahlen and x not in (None, ""):
                        raus.append(x)
            elif v is not None or not nur_zahlen:
                raus.append(als_zahl(v) if nur_zahlen else v)
        return raus

    def _fn(self, name: str, args: list):
        n = len(args)

        def arg(i, standard=None):
            if i >= n or args[i][0] == "leer":
                return standard
            return _einzeln(self._w(args[i]))

        def anzahl(mind, hoechst=None):
            if n < mind or (hoechst is not None and n > hoechst):
                raise _Abbruch(WERT)

        if name == "SUMME":
            return math.fsum(self._alle(args))
        if name == "PRODUKT":
            return math.prod(self._alle(args)) if args else 0.0
        if name in ("MIN", "MAX"):
            w = self._alle(args)
            return (min(w) if name == "MIN" else max(w)) if w else 0.0
        if name == "MITTELWERT":
            w = self._alle(args)
            if not w:
                raise _Abbruch(DIV0)
            return math.fsum(w) / len(w)
        if name == "MEDIAN":
            w = sorted(self._alle(args))
            if not w:
                raise _Abbruch(ZAHL)
            m = len(w) // 2
            return w[m] if len(w) % 2 else (w[m - 1] + w[m]) / 2
        if name == "ANZAHL":
            return float(len(self._alle(args)))
        if name == "ANZAHL2":
            return float(len(self._alle(args, nur_zahlen=False)))
        if name == "ANZAHLLEEREZELLEN":
            anzahl(1, 1)
            k = args[0]
            if k[0] != "bereich":
                raise _Abbruch(WERT)
            (z1, s1), (z2, s2) = k[2], k[3]
            gesamt = (abs(z2 - z1) + 1) * (abs(s2 - s1) + 1)
            belegt = sum(1 for x in self._w(k) if x not in (None, ""))
            return float(gesamt - belegt)
        if name in ("RUNDEN", "AUFRUNDEN", "ABRUNDEN"):
            anzahl(1, 2)
            x, st = als_zahl(arg(0)), int(als_zahl(arg(1, 0.0)))
            if abs(st) > 15:
                raise _Abbruch(ZAHL)
            f = 10.0 ** st
            if name == "RUNDEN":
                return math.copysign(math.floor(abs(x) * f + 0.5 + 1e-9) / f, x)
            if name == "AUFRUNDEN":
                return math.copysign(math.ceil(abs(x) * f - 1e-9) / f, x)
            return math.copysign(math.floor(abs(x) * f + 1e-9) / f, x)
        if name == "ABS":
            anzahl(1, 1)
            return abs(als_zahl(arg(0)))
        if name == "WURZEL":
            anzahl(1, 1)
            x = als_zahl(arg(0))
            if x < 0:
                raise _Abbruch(ZAHL)
            return math.sqrt(x)
        if name == "POTENZ":
            anzahl(2, 2)
            return self._binaer("^", arg(0), arg(1))
        if name == "REST":
            anzahl(2, 2)
            x, y = als_zahl(arg(0)), als_zahl(arg(1))
            if y == 0:
                raise _Abbruch(DIV0)
            return x - y * math.floor(x / y)
        if name == "GANZZAHL":
            anzahl(1, 1)
            return float(math.floor(als_zahl(arg(0))))
        if name == "PI":
            anzahl(0, 0)
            return math.pi
        if name == "WENN":
            anzahl(1, 3)
            if als_bool(arg(0)):
                return arg(1, True)
            return arg(2, False)
        if name == "WENNFEHLER":
            anzahl(2, 2)
            try:
                v = arg(0)
            except _Abbruch:
                return arg(1, "")
            return arg(1, "") if ist_fehler(v) else v
        if name in ("UND", "ODER"):
            if not args:
                raise _Abbruch(WERT)
            werte = []
            for a in args:
                v = self._w(a)
                werte += [als_bool(x) for x in v if x not in (None, "")] if isinstance(v, list) else [als_bool(v)]
            return all(werte) if name == "UND" else any(werte)
        if name == "NICHT":
            anzahl(1, 1)
            return not als_bool(arg(0))
        if name in ("WAHR", "FALSCH"):
            anzahl(0, 0)
            return name == "WAHR"
        if name in ("VERKETTEN", "TEXTKETTE"):
            teile = []
            for a in args:
                v = self._w(a)
                teile += [als_text(x) for x in v] if isinstance(v, list) else [als_text(v)]
            t = "".join(teile)
            if len(t) > MAX_TEXT:
                raise _Abbruch(WERT)
            return t
        if name == "LÄNGE":
            anzahl(1, 1)
            return float(len(als_text(arg(0))))
        if name in ("LINKS", "RECHTS"):
            anzahl(1, 2)
            t, z = als_text(arg(0)), int(als_zahl(arg(1, 1.0)))
            if z < 0:
                raise _Abbruch(WERT)
            return t[:z] if name == "LINKS" else (t[-z:] if z else "")
        if name == "TEIL":
            anzahl(3, 3)
            t, start, z = als_text(arg(0)), int(als_zahl(arg(1))), int(als_zahl(arg(2)))
            if start < 1 or z < 0:
                raise _Abbruch(WERT)
            return t[start - 1:start - 1 + z]
        if name in ("GROSS", "KLEIN"):
            anzahl(1, 1)
            t = als_text(arg(0))
            return t.upper() if name == "GROSS" else t.lower()
        if name == "GLÄTTEN":
            anzahl(1, 1)
            return " ".join(als_text(arg(0)).split())
        if name in ("ZÄHLENWENN", "SUMMEWENN", "MITTELWERTWENN"):
            anzahl(2, 2 if name == "ZÄHLENWENN" else 3)
            if args[0][0] != "bereich":
                raise _Abbruch(WERT)
            pruef = _kriterium(arg(1))
            if name == "ZÄHLENWENN":
                return float(sum(1 for x in self._bereich_voll(args[0]) if pruef(x)))
            summen = self._bereich_voll(args[2]) if n > 2 and args[2][0] == "bereich" else None
            werte = self._bereich_voll(args[0])
            treffer = []
            for i, x in enumerate(werte):
                if pruef(x):
                    y = summen[i] if summen is not None and i < len(summen) else x
                    if isinstance(y, float) and not isinstance(y, bool):
                        treffer.append(y)
            if name == "SUMMEWENN":
                return math.fsum(treffer)
            if not treffer:
                raise _Abbruch(DIV0)
            return math.fsum(treffer) / len(treffer)
        if name == "HEUTE":
            anzahl(0, 0)
            return Datum.aus(_dt.date.today())
        if name == "DATUM":
            anzahl(3, 3)
            j, m, t = int(als_zahl(arg(0))), int(als_zahl(arg(1))), int(als_zahl(arg(2)))
            if j < 100:
                j += 1900
            try:
                j += (m - 1) // 12
                m = (m - 1) % 12 + 1
                return Datum(Datum.aus(_dt.date(j, m, 1)) + t - 1)
            except (ValueError, OverflowError):
                raise _Abbruch(ZAHL) from None
        if name in ("JAHR", "MONAT", "TAG"):
            anzahl(1, 1)
            v = arg(0)
            d = (Datum(als_zahl(v)) if not isinstance(v, Datum) else v).datum()
            if d is None:
                raise _Abbruch(ZAHL)
            return float(d.year if name == "JAHR" else d.month if name == "MONAT" else d.day)
        raise _Abbruch(NAME)

    def _bereich_voll(self, k) -> list:
        """Alle Zellen eines Bereichs in Zeilenfolge, auch die leeren (für ZÄHLENWENN/SUMMEWENN)."""
        (z1, s1), (z2, s2) = k[2], k[3]
        z1, z2, s1, s2 = min(z1, z2), max(z1, z2), min(s1, s2), max(s1, s2)
        if (z2 - z1 + 1) * (s2 - s1 + 1) > MAX_BEREICH:
            raise _Abbruch(BEZUG)
        blatt = self._blatt(k[1])
        return [self.wert_von(blatt, z, s) for z in range(z1, z2 + 1) for s in range(s1, s2 + 1)]


def _einzeln(v):
    """Ein Bereich an Stelle eines Einzelwerts: dessen erster Wert (wie bei einem einzelnen Bezug)."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _vergleich(op, a, b) -> bool:
    def schluessel(v):
        if v is None:
            return (0, 0.0)
        if isinstance(v, bool):
            return (2, float(v))
        if isinstance(v, (int, float)):
            return (0, float(v))
        return (1, str(v).lower())

    if a is None and isinstance(b, str):
        a = ""
    if b is None and isinstance(a, str):
        b = ""
    x, y = schluessel(a), schluessel(b)
    return {"=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def _kriterium(k):
    """Bedingung für ZÄHLENWENN/SUMMEWENN: Zahl, Text (ohne Groß/klein, * und ? erlaubt) oder ">5", "<>x" …"""
    if isinstance(k, (int, float)) and not isinstance(k, bool):
        return lambda x: isinstance(x, float) and not isinstance(x, bool) and x == k
    t = als_text(k)
    m = re.match(r"(<>|<=|>=|<|>|=)?(.*)", t, re.DOTALL)
    op, rest = m.group(1) or "=", m.group(2)
    zahl = zahl_aus_text(rest) if rest else None
    if zahl is not None:
        def pruef_zahl(x):
            if isinstance(x, float) and not isinstance(x, bool):
                return _vergleich(op, x, zahl)
            return op == "<>"
        return pruef_zahl
    if op in ("=", "<>") and any(c in rest for c in "*?"):
        muster = re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in rest),
                            re.IGNORECASE | re.DOTALL)
        gleich = lambda x: x is not None and bool(muster.fullmatch(als_text(x)))  # noqa: E731
        return gleich if op == "=" else (lambda x: not gleich(x))
    if op in ("=", "<>") and rest == "":
        return (lambda x: x in (None, "")) if op == "=" else (lambda x: x not in (None, ""))
    return lambda x: x is not None and not ist_fehler(x) and _vergleich(op, als_text(x).lower(), rest.lower())


# ---------------------------------------------------------------------------
# Übersetzen (für Dateiformate)
# ---------------------------------------------------------------------------

def _blatt_text(name: str | None, ziel: str) -> str:
    if not name:
        return ""
    einfach = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and not zelle_aus(name)
    if ziel == "ods":
        return "$" + (name if einfach else "'" + name.replace("'", "''") + "'")
    return (name if einfach else "'" + name.replace("'", "''") + "'") + "!"


def uebersetzen(formel: str, ziel: str) -> str:
    """Formel in die Schreibweise von ziel: "de" (=SUMME(A1;2,5)), "excel" (SUM(A1,2.5) ohne =),
    "ods" (of:=SUM([.A1];2.5)). Nicht zerlegbare Formeln kommen unverändert zurück."""
    try:
        toks = zerlegen(formel)
    except _Abbruch:
        rest = formel.strip()
        rest = rest[3:] if rest.lower().startswith("of:") else rest
        rest = rest.lstrip("=")
        return rest if ziel == "excel" else ("of:=" + rest if ziel == "ods" else "=" + rest)
    teile = []
    for t in toks:
        if t.art == "zahl":
            r = t.roh or repr(t.wert)
            r = r.replace(",", ".") if ziel != "de" else r.replace(".", ",")
            teile.append(r)
        elif t.art == "text":
            teile.append('"' + str(t.wert).replace('"', '""') + '"')
        elif t.art == "bool":
            teile.append(("TRUE" if t.wert else "FALSE") if ziel == "excel" else ("WAHR" if t.wert else "FALSCH")
                         if ziel == "de" else ("TRUE()" if t.wert else "FALSE()"))
        elif t.art == "fehler":
            teile.append({"#WERT!": "#VALUE!", "#BEZUG!": "#REF!", "#ZAHL!": "#NUM!", "#NV": "#N/A"}.get(t.wert, t.wert)
                         if ziel != "de" else str(t.wert))
        elif t.art in ("ref", "bereich"):
            blatt = t.wert[0]
            a, _, b = t.roh.upper().partition(":")
            if ziel == "ods":
                pre = _blatt_text(blatt, "ods") if blatt else ""
                teile.append(f"[{pre}.{a}" + (f":{pre}.{b}" if b else "") + "]")
            else:
                teile.append(_blatt_text(blatt, ziel) + a + (f":{b}" if b else ""))
        elif t.art == "fn":
            name = str(t.wert)
            teile.append(name if ziel == "de" else _DE_EN.get(name, name))
        elif t.art == "sep":
            teile.append(";" if ziel in ("de", "ods") else ",")
        else:
            teile.append(str(t.wert))
    text = "".join(teile)
    if ziel == "excel":
        return text
    if ziel == "ods":
        return "of:=" + text
    return "=" + text


def deutsch(formel: str) -> str:
    return uebersetzen(formel, "de")
