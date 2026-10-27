"""
nemijs_parser.py - Lexer und Parser für NemiJS (die JavaScript-Teilmenge der NemiCLI-Apps).

Ergebnis ist ein Baum aus dicts: {"t": "<Art>", …}. Unterstützt: var/let/const mit Zerlegung (Objekt, Liste,
Vorgaben, Rest), Funktionen und Pfeilfunktionen (auch async), Klassen (constructor, Methoden, extends, static),
if/for/for-of/for-in/while/do/switch/try/throw/break/continue/return, Vorlagen-Text `${…}`, reguläre Ausdrücke,
Spread, optionale Verkettung ?., ??, alle üblichen Operatoren. Fehlende Semikolons werden ergänzt (ASI).
Was nicht dazugehört (with, eval-Syntax, Generatoren, Labels, Getter/Setter-Syntax), meldet ein JSFehler mit Zeile.
"""
from __future__ import annotations

import re

MAX_QUELLE = 2_000_000          # Zeichen je Skript
MAX_TIEFE = 300                 # Verschachtelung im Parser


class SyntaxFehler(Exception):
    def __init__(self, text: str, zeile: int = 0):
        super().__init__(f"{text} (Zeile {zeile})" if zeile else text)
        self.zeile = zeile


KEYWORDS = {"var", "let", "const", "function", "return", "if", "else", "for", "while", "do", "break", "continue",
            "new", "delete", "typeof", "instanceof", "in", "of", "true", "false", "null", "undefined", "this",
            "class", "extends", "super", "throw", "try", "catch", "finally", "switch", "case", "default", "void",
            "async", "await", "static"}
_REGEX_DAVOR = {"(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">", "~", "^",
                "&&", "||", "??", "=>", "==", "===", "!=", "!==", "+=", "-=", "*=", "/=", "<=", ">=", "return",
                "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "await"}
_PUNKTE = sorted("""
>>>= ... === !== **= <<= >>= >>> ?.( ?.[ &&= ||= ??= => == != <= >= && || ?? ?. ++ -- += -= *= /= %= &= |= ^= ** << >>
{ } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = . @ #
""".split(), key=len, reverse=True)
_ESCAPE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_ZAHL = re.compile(r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_$À-￿][\w$À-￿]*")


class Tok:
    __slots__ = ("art", "wert", "zeile", "nl", "pos")

    def __init__(self, art, wert, zeile, nl, pos):
        self.art, self.wert, self.zeile, self.nl, self.pos = art, wert, zeile, nl, pos

    def __repr__(self):
        return f"Tok({self.art},{self.wert!r},{self.zeile})"


def lexen(quelle: str) -> list[Tok]:
    if len(quelle) > MAX_QUELLE:
        raise SyntaxFehler("Skript zu lang")
    toks: list[Tok] = []
    i, n, zeile, nl = 0, len(quelle), 1, False
    while i < n:
        c = quelle[i]
        if c == "\n":
            zeile += 1
            nl = True
            i += 1
            continue
        if c in " \t\r﻿   ":
            i += 1
            continue
        if quelle.startswith("//", i):
            j = quelle.find("\n", i)
            i = n if j < 0 else j
            continue
        if quelle.startswith("/*", i):
            j = quelle.find("*/", i + 2)
            if j < 0:
                raise SyntaxFehler("Kommentar nicht geschlossen", zeile)
            zeile += quelle.count("\n", i, j)
            nl = nl or "\n" in quelle[i:j]
            i = j + 2
            continue
        start = i
        if c.isdigit() or (c == "." and i + 1 < n and quelle[i + 1].isdigit()):
            m = _ZAHL.match(quelle, i)
            roh = m.group(0).replace("_", "")
            i = m.end()
            if i < n and quelle[i] == "n":                 # BigInt-Endung: als Zahl
                i += 1
            if roh[:2].lower() == "0x":
                wert = float(int(roh[2:], 16))
            elif roh[:2].lower() == "0b":
                wert = float(int(roh[2:], 2))
            elif roh[:2].lower() == "0o":
                wert = float(int(roh[2:], 8))
            else:
                wert = float(roh)
            toks.append(Tok("zahl", wert, zeile, nl, start))
        elif c in "\"'":
            wert, i, zeile = _string(quelle, i, zeile)
            toks.append(Tok("text", wert, zeile, nl, start))
        elif c == "`":
            teile, i, zeile2 = _vorlage(quelle, i, zeile)
            toks.append(Tok("vorlage", teile, zeile, nl, start))
            zeile = zeile2
        elif c == "/" and _regex_erlaubt(toks):
            muster, flags, i = _regex(quelle, i, zeile)
            toks.append(Tok("regex", (muster, flags), zeile, nl, start))
        elif (m := _NAME.match(quelle, i)):
            wort = m.group(0)
            i = m.end()
            toks.append(Tok("wort" if wort in KEYWORDS else "name", wort, zeile, nl, start))
        else:
            for p in _PUNKTE:
                if quelle.startswith(p, i):
                    if p in ("?.(", "?.["):                 # nur ?. als Zeichen, Klammer folgt eigens
                        p = "?."
                    elif p == "?." and i + 2 < n and quelle[i + 2].isdigit():
                        p = "?"                              # a?.5:b ist ein Dreifach-Operator
                    toks.append(Tok("punkt", p, zeile, nl, start))
                    i += len(p)
                    break
            else:
                raise SyntaxFehler(f"Unerwartetes Zeichen {c!r}", zeile)
        nl = False
    toks.append(Tok("ende", None, zeile, True, n))
    return toks


def _regex_erlaubt(toks: list[Tok]) -> bool:
    if not toks:
        return True
    t = toks[-1]
    if t.art in ("zahl", "text", "vorlage", "regex", "name"):
        return False
    if t.art == "wort":
        return t.wert in _REGEX_DAVOR
    return t.wert in _REGEX_DAVOR


def _string(q: str, i: int, zeile: int) -> tuple[str, int, int]:
    ende = q[i]
    i += 1
    out = []
    while i < len(q):
        c = q[i]
        if c == ende:
            return "".join(out), i + 1, zeile
        if c == "\n":
            raise SyntaxFehler("Zeilenumbruch in einer Zeichenkette", zeile)
        if c == "\\":
            s, i, zeile = _escape(q, i + 1, zeile)
            out.append(s)
            continue
        out.append(c)
        i += 1
    raise SyntaxFehler("Zeichenkette nicht geschlossen", zeile)


def _escape(q: str, i: int, zeile: int) -> tuple[str, int, int]:
    if i >= len(q):
        raise SyntaxFehler("Escape am Ende", zeile)
    c = q[i]
    if c in _ESCAPE and not (c == "0" and i + 1 < len(q) and q[i + 1].isdigit()):
        return _ESCAPE[c], i + 1, zeile
    if c == "x":
        return chr(int(q[i + 1:i + 3], 16)), i + 3, zeile
    if c == "u":
        if q[i + 1:i + 2] == "{":
            j = q.index("}", i)
            return chr(int(q[i + 2:j], 16)), j + 1, zeile
        return chr(int(q[i + 1:i + 5], 16)), i + 5, zeile
    if c == "\r" and q[i + 1:i + 2] == "\n":
        return "", i + 2, zeile + 1
    if c == "\n":
        return "", i + 1, zeile + 1
    return c, i + 1, zeile


def _vorlage(q: str, i: int, zeile: int) -> tuple[list, int, int]:
    """`text ${ausdruck} text` → [str, (quelle, zeile), str, …]"""
    i += 1
    teile: list = []
    out: list[str] = []
    while i < len(q):
        c = q[i]
        if c == "`":
            teile.append("".join(out))
            return teile, i + 1, zeile
        if c == "\\":
            s, i, zeile = _escape(q, i + 1, zeile)
            out.append(s)
            continue
        if c == "$" and q[i + 1:i + 2] == "{":
            teile.append("".join(out))
            out = []
            j, tiefe, start = i + 2, 1, i + 2
            while j < len(q) and tiefe:
                d = q[j]
                if d in "\"'":
                    _, j, _ = _string(q, j, zeile)
                    continue
                if d == "`":
                    _, j, _ = _vorlage(q, j, zeile)
                    continue
                if d == "{":
                    tiefe += 1
                elif d == "}":
                    tiefe -= 1
                j += 1
            if tiefe:
                raise SyntaxFehler("${ nicht geschlossen", zeile)
            teile.append((q[start:j - 1], zeile))
            zeile += q.count("\n", start, j)
            i = j
            continue
        if c == "\n":
            zeile += 1
        out.append(c)
        i += 1
    raise SyntaxFehler("Vorlage (`…`) nicht geschlossen", zeile)


def _regex(q: str, i: int, zeile: int) -> tuple[str, str, int]:
    i += 1
    out, klasse = [], False
    while i < len(q):
        c = q[i]
        if c == "\n":
            raise SyntaxFehler("Regulärer Ausdruck nicht geschlossen", zeile)
        if c == "\\":
            out.append(q[i:i + 2])
            i += 2
            continue
        if c == "[":
            klasse = True
        elif c == "]":
            klasse = False
        elif c == "/" and not klasse:
            i += 1
            j = i
            while j < len(q) and q[j].isalpha():
                j += 1
            return "".join(out), q[i:j], j
        out.append(c)
        i += 1
    raise SyntaxFehler("Regulärer Ausdruck nicht geschlossen", zeile)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_BINAER = {
    "??": 1, "||": 2, "&&": 3, "|": 4, "^": 5, "&": 6,
    "==": 7, "!=": 7, "===": 7, "!==": 7,
    "<": 8, ">": 8, "<=": 8, ">=": 8, "instanceof": 8, "in": 8,
    "<<": 9, ">>": 9, ">>>": 9, "+": 10, "-": 10, "*": 11, "/": 11, "%": 11, "**": 12,
}
_ZUWEISUNG = {"=", "+=", "-=", "*=", "/=", "%=", "**=", "<<=", ">>=", ">>>=", "&=", "|=", "^=", "&&=", "||=", "??="}


class Parser:
    def __init__(self, quelle: str):
        self.toks = lexen(quelle)
        self.i = 0
        self.tiefe = 0
        self.in_funktion = 0

    # -- Hilfen ------------------------------------------------------------------
    @property
    def t(self) -> Tok:
        return self.toks[self.i]

    def nach(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def ist(self, wert, art=None) -> bool:
        t = self.t
        return t.wert == wert and t.art in ((art,) if art else ("punkt", "wort"))

    def weiter(self) -> Tok:
        t = self.t
        self.i += 1
        return t

    def erwarte(self, wert) -> Tok:
        if not self.ist(wert):
            raise SyntaxFehler(f"Erwartet „{wert}“, gefunden „{self.t.wert if self.t.art != 'ende' else 'Ende'}“",
                               self.t.zeile)
        return self.weiter()

    def name(self) -> str:
        t = self.t
        if t.art == "name" or (t.art == "wort" and t.wert in ("of", "async", "static", "undefined")):
            self.i += 1
            return t.wert
        raise SyntaxFehler(f"Name erwartet, gefunden „{t.wert}“", t.zeile)

    def eigenschaftsname(self) -> str:
        t = self.t
        if t.art in ("name", "wort"):
            self.i += 1
            return t.wert
        if t.art == "text":
            self.i += 1
            return t.wert
        if t.art == "zahl":
            self.i += 1
            return _zahl_text(t.wert)
        raise SyntaxFehler(f"Eigenschaftsname erwartet, gefunden „{t.wert}“", t.zeile)

    def semikolon(self) -> None:
        if self.ist(";"):
            self.i += 1
        elif self.ist("}") or self.t.art == "ende" or self.t.nl:
            return
        else:
            raise SyntaxFehler(f"„;“ erwartet, gefunden „{self.t.wert}“", self.t.zeile)

    def _tiefer(self):
        self.tiefe += 1
        if self.tiefe > MAX_TIEFE:
            raise SyntaxFehler("Zu tief verschachtelt", self.t.zeile)

    # -- Programm und Anweisungen ------------------------------------------------
    def programm(self) -> dict:
        body = []
        while self.t.art != "ende":
            body.append(self.anweisung())
        return {"t": "Programm", "body": body}

    def anweisung(self) -> dict:
        self._tiefer()
        try:
            return self._anweisung()
        finally:
            self.tiefe -= 1

    def _anweisung(self) -> dict:
        t = self.t
        z = t.zeile
        if t.art == "punkt":
            if t.wert == "{":
                return self.block()
            if t.wert == ";":
                self.i += 1
                return {"t": "Leer"}
        if t.art == "wort":
            w = t.wert
            if w in ("var", "let", "const"):
                d = self.deklaration()
                self.semikolon()
                return d
            if w == "function" or (w == "async" and self.nach().wert == "function" and not self.nach().nl):
                return self.funktion(anweisung=True)
            if w == "class":
                return self.klasse(anweisung=True)
            if w == "if":
                self.i += 1
                self.erwarte("(")
                bed = self.ausdruck()
                self.erwarte(")")
                dann = self.anweisung()
                sonst = None
                if self.ist("else"):
                    self.i += 1
                    sonst = self.anweisung()
                return {"t": "If", "bed": bed, "dann": dann, "sonst": sonst, "z": z}
            if w == "for":
                return self.for_schleife()
            if w == "while":
                self.i += 1
                self.erwarte("(")
                bed = self.ausdruck()
                self.erwarte(")")
                return {"t": "While", "bed": bed, "body": self.anweisung(), "z": z}
            if w == "do":
                self.i += 1
                body = self.anweisung()
                self.erwarte("while")
                self.erwarte("(")
                bed = self.ausdruck()
                self.erwarte(")")
                if self.ist(";"):
                    self.i += 1
                return {"t": "DoWhile", "bed": bed, "body": body, "z": z}
            if w in ("break", "continue"):
                self.i += 1
                if self.t.art == "name" and not self.t.nl:
                    raise SyntaxFehler("Sprungmarken (Labels) gibt es in NemiJS nicht", z)
                self.semikolon()
                return {"t": "Break" if w == "break" else "Continue", "z": z}
            if w == "return":
                self.i += 1
                wert = None
                if not (self.ist(";") or self.ist("}") or self.t.art == "ende" or self.t.nl):
                    wert = self.ausdruck()
                self.semikolon()
                return {"t": "Return", "wert": wert, "z": z}
            if w == "throw":
                self.i += 1
                wert = self.ausdruck()
                self.semikolon()
                return {"t": "Throw", "wert": wert, "z": z}
            if w == "try":
                return self.try_anweisung()
            if w == "switch":
                return self.switch()
        e = self.ausdruck()
        self.semikolon()
        return {"t": "Ausdruck", "e": e, "z": z}

    def block(self) -> dict:
        self.erwarte("{")
        body = []
        while not self.ist("}"):
            if self.t.art == "ende":
                raise SyntaxFehler("„}“ fehlt", self.t.zeile)
            body.append(self.anweisung())
        self.i += 1
        return {"t": "Block", "body": body}

    def deklaration(self, ohne_init_ok: bool = False) -> dict:
        art = self.weiter().wert
        teile = []
        while True:
            ziel = self.bindung()
            init = None
            if self.ist("="):
                self.i += 1
                init = self.zuweisung()
            elif art == "const" and not ohne_init_ok:
                raise SyntaxFehler("const braucht einen Wert", self.t.zeile)
            teile.append((ziel, init))
            if not self.ist(","):
                break
            self.i += 1
        return {"t": "Var", "art": art, "teile": teile}

    def bindung(self) -> dict:
        """Ziel einer Deklaration oder eines Parameters: Name, {…} oder […]."""
        if self.ist("{"):
            self.i += 1
            props, rest = [], None
            while not self.ist("}"):
                if self.ist("..."):
                    self.i += 1
                    rest = self.name()
                else:
                    if self.ist("["):
                        self.i += 1
                        schluessel = self.zuweisung()
                        self.erwarte("]")
                        berechnet = True
                    else:
                        schluessel, berechnet = self.eigenschaftsname(), False
                    if self.ist(":"):
                        self.i += 1
                        ziel = self.bindung()
                    elif berechnet:
                        raise SyntaxFehler("Berechneter Schlüssel braucht „:“", self.t.zeile)
                    else:
                        ziel = {"t": "Name", "name": schluessel}
                    vorgabe = None
                    if self.ist("="):
                        self.i += 1
                        vorgabe = self.zuweisung()
                    props.append((schluessel, berechnet, ziel, vorgabe))
                if not self.ist("}"):
                    self.erwarte(",")
            self.i += 1
            return {"t": "ObjMuster", "props": props, "rest": rest}
        if self.ist("["):
            self.i += 1
            elemente, rest = [], None
            while not self.ist("]"):
                if self.ist(","):
                    self.i += 1
                    elemente.append(None)
                    continue
                if self.ist("..."):
                    self.i += 1
                    rest = self.bindung()
                else:
                    ziel = self.bindung()
                    vorgabe = None
                    if self.ist("="):
                        self.i += 1
                        vorgabe = self.zuweisung()
                    elemente.append((ziel, vorgabe))
                if not self.ist("]"):
                    self.erwarte(",")
            self.i += 1
            return {"t": "ListMuster", "elemente": elemente, "rest": rest}
        return {"t": "Name", "name": self.name()}

    def for_schleife(self) -> dict:
        z = self.weiter().zeile
        if self.ist("await"):
            raise SyntaxFehler("for await gibt es in NemiJS nicht", z)
        self.erwarte("(")
        init = None
        if self.ist(";"):
            pass
        elif self.t.wert in ("var", "let", "const") and self.t.art == "wort":
            anfang = self.i
            art = self.weiter().wert
            ziel = self.bindung()
            if self.ist("of") or self.ist("in"):
                welche = self.weiter().wert
                quelle = self.ausdruck() if welche == "in" else self.zuweisung()
                self.erwarte(")")
                return {"t": "ForOf" if welche == "of" else "ForIn", "art": art, "ziel": ziel, "quelle": quelle,
                        "body": self.anweisung(), "z": z}
            self.i = anfang                                  # doch eine normale Deklaration: neu lesen
            init = self.deklaration(ohne_init_ok=True)
        else:
            ausdr = self.ausdruck(ohne_in=True)
            if self.ist("of") or self.ist("in"):
                welche = self.weiter().wert
                quelle = self.ausdruck() if welche == "in" else self.zuweisung()
                self.erwarte(")")
                return {"t": "ForOf" if welche == "of" else "ForIn", "art": None, "ziel": _als_muster(ausdr),
                        "quelle": quelle, "body": self.anweisung(), "z": z}
            init = {"t": "Ausdruck", "e": ausdr}
        self.erwarte(";")
        bed = None if self.ist(";") else self.ausdruck()
        self.erwarte(";")
        schritt = None if self.ist(")") else self.ausdruck()
        self.erwarte(")")
        return {"t": "For", "init": init, "bed": bed, "schritt": schritt, "body": self.anweisung(), "z": z}

    def try_anweisung(self) -> dict:
        z = self.weiter().zeile
        block = self.block()
        param, fang, schluss = None, None, None
        if self.ist("catch"):
            self.i += 1
            if self.ist("("):
                self.i += 1
                param = self.bindung()
                self.erwarte(")")
            fang = self.block()
        if self.ist("finally"):
            self.i += 1
            schluss = self.block()
        if fang is None and schluss is None:
            raise SyntaxFehler("try ohne catch und finally", z)
        return {"t": "Try", "block": block, "param": param, "fang": fang, "schluss": schluss, "z": z}

    def switch(self) -> dict:
        z = self.weiter().zeile
        self.erwarte("(")
        wert = self.ausdruck()
        self.erwarte(")")
        self.erwarte("{")
        faelle = []
        while not self.ist("}"):
            if self.ist("case"):
                self.i += 1
                test = self.ausdruck()
            else:
                self.erwarte("default")
                test = None
            self.erwarte(":")
            body = []
            while not (self.ist("case") or self.ist("default") or self.ist("}")):
                body.append(self.anweisung())
            faelle.append((test, body))
        self.i += 1
        return {"t": "Switch", "wert": wert, "faelle": faelle, "z": z}

    # -- Funktionen und Klassen --------------------------------------------------
    def parameter(self) -> list:
        self.erwarte("(")
        params = []
        while not self.ist(")"):
            if self.ist("..."):
                self.i += 1
                params.append(("rest", self.bindung(), None))
            else:
                ziel = self.bindung()
                vorgabe = None
                if self.ist("="):
                    self.i += 1
                    vorgabe = self.zuweisung()
                params.append(("param", ziel, vorgabe))
            if not self.ist(")"):
                self.erwarte(",")
        self.i += 1
        return params

    def funktionskoerper(self) -> list:
        self.in_funktion += 1
        try:
            return self.block()["body"]
        finally:
            self.in_funktion -= 1

    def funktion(self, anweisung: bool = False) -> dict:
        z = self.t.zeile
        asynchron = False
        if self.ist("async"):
            self.i += 1
            asynchron = True
        self.erwarte("function")
        if self.ist("*"):
            raise SyntaxFehler("Generatoren (function*) gibt es in NemiJS nicht", z)
        name = None
        if self.t.art == "name" or (self.t.art == "wort" and self.t.wert in ("of", "static")):
            name = self.name()
        elif anweisung:
            raise SyntaxFehler("Funktion braucht einen Namen", z)
        params = self.parameter()
        body = self.funktionskoerper()
        knoten = {"t": "Funktion", "name": name, "params": params, "body": body, "pfeil": False,
                  "async": asynchron, "z": z}
        return {"t": "FunktionDekl", "name": name, "f": knoten} if anweisung else knoten

    def klasse(self, anweisung: bool = False) -> dict:
        z = self.weiter().zeile
        name = None
        if self.t.art == "name":
            name = self.name()
        elif anweisung:
            raise SyntaxFehler("Klasse braucht einen Namen", z)
        basis = None
        if self.ist("extends"):
            self.i += 1
            basis = self.links_ausdruck()
        self.erwarte("{")
        methoden = []
        while not self.ist("}"):
            if self.ist(";"):
                self.i += 1
                continue
            statisch = False
            if self.ist("static") and not self.nach().wert in ("(", "="):
                self.i += 1
                statisch = True
            asynchron = False
            if self.ist("async") and self.nach().wert != "(" and not self.nach().nl:
                self.i += 1
                asynchron = True
            if self.t.wert in ("get", "set") and self.nach().art in ("name", "wort", "text") \
                    and self.nach().wert not in ("(", "="):
                raise SyntaxFehler("get/set gibt es in NemiJS nicht – normale Methoden benutzen", self.t.zeile)
            if self.ist("#"):
                raise SyntaxFehler("Private Felder (#) gibt es in NemiJS nicht", self.t.zeile)
            mname = self.eigenschaftsname()
            if self.ist("("):
                params = self.parameter()
                body = self.funktionskoerper()
                f = {"t": "Funktion", "name": mname, "params": params, "body": body, "pfeil": False,
                     "async": asynchron, "z": self.t.zeile, "methode": True}
                methoden.append(("methode", mname, statisch, f))
            else:
                wert = None
                if self.ist("="):
                    self.i += 1
                    wert = self.zuweisung()
                self.semikolon()
                methoden.append(("feld", mname, statisch, wert))
        self.i += 1
        knoten = {"t": "Klasse", "name": name, "basis": basis, "glieder": methoden, "z": z}
        return {"t": "KlasseDekl", "name": name, "k": knoten} if anweisung else knoten

    # -- Ausdrücke ---------------------------------------------------------------
    def ausdruck(self, ohne_in: bool = False) -> dict:
        e = self.zuweisung(ohne_in)
        if self.ist(","):
            folge = [e]
            while self.ist(","):
                self.i += 1
                folge.append(self.zuweisung(ohne_in))
            return {"t": "Folge", "teile": folge}
        return e

    def _pfeil_voraus(self) -> bool:
        """Steht hier eine Pfeilfunktion ( … ) => ?"""
        if not self.ist("("):
            return False
        tiefe, j = 0, self.i
        while j < len(self.toks):
            t = self.toks[j]
            if t.art == "punkt" and t.wert in ("(", "[", "{"):
                tiefe += 1
            elif t.art == "punkt" and t.wert in (")", "]", "}"):
                tiefe -= 1
                if tiefe == 0:
                    n = self.toks[j + 1] if j + 1 < len(self.toks) else None
                    return bool(n and n.art == "punkt" and n.wert == "=>" and not n.nl)
            elif t.art == "ende":
                return False
            j += 1
        return False

    def zuweisung(self, ohne_in: bool = False) -> dict:
        self._tiefer()
        try:
            return self._zuweisung(ohne_in)
        finally:
            self.tiefe -= 1

    def _zuweisung(self, ohne_in: bool) -> dict:
        t = self.t
        # Pfeilfunktionen: x => …, async x => …, (a, b) => …, async (a) => …
        asynchron = False
        if t.art == "wort" and t.wert == "async" and not self.nach().nl and \
                (self.nach().art == "name" or (self.nach().wert == "(" and self._pfeil_nach_async())):
            self.i += 1
            asynchron = True
            t = self.t
        if t.art == "name" and self.nach().wert == "=>" and self.nach().art == "punkt" and not self.nach().nl:
            self.i += 2
            return self.pfeil_rest([("param", {"t": "Name", "name": t.wert}, None)], asynchron, t.zeile)
        if self._pfeil_voraus():
            z = t.zeile
            params = self.parameter()
            self.erwarte("=>")
            return self.pfeil_rest(params, asynchron, z)
        if asynchron:
            self.i -= 1
        links = self.bedingung(ohne_in)
        if self.t.art == "punkt" and self.t.wert in _ZUWEISUNG:
            op = self.weiter().wert
            if links["t"] not in ("Name", "Glied") and op == "=":
                links = _als_muster(links)
            elif links["t"] not in ("Name", "Glied"):
                raise SyntaxFehler("Ungültiges Ziel einer Zuweisung", self.t.zeile)
            rechts = self.zuweisung(ohne_in)
            return {"t": "Zuweisung", "op": op, "ziel": links, "wert": rechts, "z": t.zeile}
        return links

    def _pfeil_nach_async(self) -> bool:
        self.i += 1
        try:
            return self._pfeil_voraus()
        finally:
            self.i -= 1

    def pfeil_rest(self, params, asynchron, z) -> dict:
        if self.ist("{"):
            body = self.funktionskoerper()
            return {"t": "Funktion", "name": None, "params": params, "body": body, "pfeil": True,
                    "async": asynchron, "z": z}
        e = self.zuweisung()
        return {"t": "Funktion", "name": None, "params": params, "body": [{"t": "Return", "wert": e, "z": z}],
                "pfeil": True, "async": asynchron, "z": z}

    def bedingung(self, ohne_in: bool = False) -> dict:
        bed = self.binaer(0, ohne_in)
        if self.ist("?"):
            self.i += 1
            dann = self.zuweisung()
            self.erwarte(":")
            sonst = self.zuweisung(ohne_in)
            return {"t": "Bedingt", "bed": bed, "dann": dann, "sonst": sonst}
        return bed

    def binaer(self, mindest: int, ohne_in: bool = False) -> dict:
        links = self.unaer()
        while True:
            t = self.t
            op = t.wert if t.art in ("punkt", "wort") else None
            if op not in _BINAER or (ohne_in and op == "in"):
                return links
            prio = _BINAER[op]
            if prio < mindest:                    # linksbündig über prio + 1 rechts, ** rechtsbündig über prio
                return links
            self.i += 1
            rechts = self.binaer(prio if op == "**" else prio + 1, ohne_in)
            art = "Logisch" if op in ("&&", "||", "??") else "Binaer"
            links = {"t": art, "op": op, "l": links, "r": rechts, "z": t.zeile}

    def unaer(self) -> dict:
        t = self.t
        if t.art == "punkt" and t.wert in ("!", "-", "+", "~"):
            self.i += 1
            return {"t": "Unaer", "op": t.wert, "e": self.unaer(), "z": t.zeile}
        if t.art == "punkt" and t.wert in ("++", "--"):
            self.i += 1
            ziel = self.unaer()
            if ziel["t"] not in ("Name", "Glied"):
                raise SyntaxFehler("++/-- braucht eine Variable", t.zeile)
            return {"t": "Aendern", "op": t.wert, "ziel": ziel, "vorn": True, "z": t.zeile}
        if t.art == "wort" and t.wert in ("typeof", "void", "delete"):
            self.i += 1
            return {"t": "Unaer", "op": t.wert, "e": self.unaer(), "z": t.zeile}
        if t.art == "wort" and t.wert == "await":
            self.i += 1
            return {"t": "Await", "e": self.unaer(), "z": t.zeile}
        e = self.postfix()
        return e

    def postfix(self) -> dict:
        e = self.links_ausdruck(aufrufe=True)
        t = self.t
        if t.art == "punkt" and t.wert in ("++", "--") and not t.nl:
            if e["t"] not in ("Name", "Glied"):
                raise SyntaxFehler("++/-- braucht eine Variable", t.zeile)
            self.i += 1
            return {"t": "Aendern", "op": t.wert, "ziel": e, "vorn": False, "z": t.zeile}
        return e

    def links_ausdruck(self, aufrufe: bool = False) -> dict:
        if self.ist("new"):
            z = self.weiter().zeile
            if self.ist("."):
                raise SyntaxFehler("new.target gibt es in NemiJS nicht", z)
            ziel = self.links_ausdruck(aufrufe=False)
            args = self.argumente() if self.ist("(") else []
            e = {"t": "Neu", "ziel": ziel, "args": args, "z": z}
        else:
            e = self.primaer()
        kette = False
        while True:
            t = self.t
            if t.art != "punkt":
                break
            if t.wert == ".":
                self.i += 1
                if self.ist("#"):
                    raise SyntaxFehler("Private Felder (#) gibt es in NemiJS nicht", t.zeile)
                e = {"t": "Glied", "obj": e, "prop": {"t": "Wert", "v": self.eigenschaftsname()}, "opt": False,
                     "z": t.zeile}
            elif t.wert == "?.":
                self.i += 1
                kette = True
                if self.ist("("):
                    e = {"t": "Aufruf", "f": e, "args": self.argumente(), "opt": True, "z": t.zeile}
                elif self.ist("["):
                    self.i += 1
                    p = self.ausdruck()
                    self.erwarte("]")
                    e = {"t": "Glied", "obj": e, "prop": p, "opt": True, "z": t.zeile}
                else:
                    e = {"t": "Glied", "obj": e, "prop": {"t": "Wert", "v": self.eigenschaftsname()}, "opt": True,
                         "z": t.zeile}
            elif t.wert == "[":
                self.i += 1
                p = self.ausdruck()
                self.erwarte("]")
                e = {"t": "Glied", "obj": e, "prop": p, "opt": False, "z": t.zeile}
            elif t.wert == "(" and aufrufe:
                e = {"t": "Aufruf", "f": e, "args": self.argumente(), "opt": False, "z": t.zeile}
            else:
                break
            if self.t.art == "vorlage" and not kette:
                raise SyntaxFehler("Markierte Vorlagen (tag`…`) gibt es in NemiJS nicht", self.t.zeile)
        if kette:
            e = {"t": "Kette", "e": e}
        return e

    def argumente(self) -> list:
        self.erwarte("(")
        args = []
        while not self.ist(")"):
            if self.ist("..."):
                self.i += 1
                args.append({"t": "Spread", "e": self.zuweisung()})
            else:
                args.append(self.zuweisung())
            if not self.ist(")"):
                self.erwarte(",")
        self.i += 1
        return args

    def primaer(self) -> dict:
        t = self.t
        z = t.zeile
        if t.art == "zahl":
            self.i += 1
            return {"t": "Wert", "v": t.wert}
        if t.art == "text":
            self.i += 1
            return {"t": "Wert", "v": t.wert}
        if t.art == "vorlage":
            self.i += 1
            teile = []
            for teil in t.wert:
                if isinstance(teil, tuple):
                    p = Parser(teil[0])
                    p.toks = [Tok(x.art, x.wert, x.zeile + teil[1] - 1, x.nl, x.pos) for x in p.toks]
                    e = p.ausdruck()
                    if p.t.art != "ende":
                        raise SyntaxFehler("Ungültiger Ausdruck in ${…}", teil[1])
                    teile.append(e)
                else:
                    teile.append(teil)
            return {"t": "Vorlage", "teile": teile}
        if t.art == "regex":
            self.i += 1
            return {"t": "Regex", "muster": t.wert[0], "flags": t.wert[1], "z": z}
        if t.art == "name":
            self.i += 1
            return {"t": "Name", "name": t.wert, "z": z}
        if t.art == "wort":
            w = t.wert
            if w in ("true", "false"):
                self.i += 1
                return {"t": "Wert", "v": w == "true"}
            if w == "null":
                self.i += 1
                return {"t": "Wert", "v": None}
            if w == "undefined":
                self.i += 1
                return {"t": "Undef"}
            if w == "this":
                self.i += 1
                return {"t": "This"}
            if w == "super":
                self.i += 1
                return {"t": "Super", "z": z}
            if w in ("function", "async") and (w == "function" or self.nach().wert == "function"):
                return self.funktion()
            if w == "class":
                return self.klasse()
            if w in ("of", "async", "static", "get", "set"):
                self.i += 1
                return {"t": "Name", "name": w, "z": z}
        if t.art == "punkt":
            if t.wert == "(":
                self.i += 1
                e = self.ausdruck()
                self.erwarte(")")
                return e
            if t.wert == "[":
                self.i += 1
                elemente = []
                while not self.ist("]"):
                    if self.ist(","):
                        self.i += 1
                        elemente.append({"t": "Undef"})
                        continue
                    if self.ist("..."):
                        self.i += 1
                        elemente.append({"t": "Spread", "e": self.zuweisung()})
                    else:
                        elemente.append(self.zuweisung())
                    if not self.ist("]"):
                        self.erwarte(",")
                self.i += 1
                return {"t": "Liste", "elemente": elemente}
            if t.wert == "{":
                return self.objekt()
        raise SyntaxFehler(f"Unerwartet: „{t.wert if t.art != 'ende' else 'Ende des Skripts'}“", z)

    def objekt(self) -> dict:
        self.erwarte("{")
        props = []
        while not self.ist("}"):
            if self.ist("..."):
                self.i += 1
                props.append(("spread", None, self.zuweisung()))
            else:
                asynchron = False
                if self.ist("async") and self.nach().wert not in (",", ":", "(", "}") and not self.nach().nl:
                    self.i += 1
                    asynchron = True
                if self.t.wert in ("get", "set") and self.nach().art in ("name", "wort", "text") \
                        and self.nach().wert not in (",", ":", "("):
                    raise SyntaxFehler("get/set gibt es in NemiJS nicht – normale Methoden benutzen", self.t.zeile)
                if self.ist("["):
                    self.i += 1
                    schluessel = self.zuweisung()
                    self.erwarte("]")
                    berechnet = True
                else:
                    t = self.t
                    schluessel, berechnet = self.eigenschaftsname(), False
                if self.ist("("):
                    params = self.parameter()
                    body = self.funktionskoerper()
                    f = {"t": "Funktion", "name": schluessel if not berechnet else None, "params": params,
                         "body": body, "pfeil": False, "async": asynchron, "z": self.t.zeile, "methode": True}
                    props.append(("wert", (schluessel, berechnet), f))
                elif self.ist(":"):
                    self.i += 1
                    props.append(("wert", (schluessel, berechnet), self.zuweisung()))
                elif not berechnet and t.art == "name":
                    if self.ist("="):                    # nur in Mustern gültig: { a = 1 } = …
                        self.i += 1
                        props.append(("vorgabe", schluessel, self.zuweisung()))
                    else:
                        props.append(("wert", (schluessel, False), {"t": "Name", "name": schluessel, "z": t.zeile}))
                else:
                    raise SyntaxFehler("„:“ erwartet", self.t.zeile)
            if not self.ist("}"):
                self.erwarte(",")
        self.i += 1
        return {"t": "Objekt", "props": props}


def _als_muster(e: dict) -> dict:
    """Ausdruck links von „=“ ({a, b} = obj, [x, y] = liste) in ein Zuweisungsmuster umbauen."""
    if e["t"] in ("Name", "Glied"):
        return e
    if e["t"] == "Zuweisung" and e["op"] == "=":
        return {"t": "MitVorgabe", "ziel": _als_muster(e["ziel"]), "vorgabe": e["wert"]}
    if e["t"] == "Liste":
        elemente, rest = [], None
        for x in e["elemente"]:
            if x["t"] == "Spread":
                rest = _als_muster(x["e"])
            elif x["t"] == "Undef":
                elemente.append(None)
            else:
                m = _als_muster(x)
                if m["t"] == "MitVorgabe":
                    elemente.append((m["ziel"], m["vorgabe"]))
                else:
                    elemente.append((m, None))
        return {"t": "ListMuster", "elemente": elemente, "rest": rest}
    if e["t"] == "Objekt":
        props, rest = [], None
        for art, schluessel, wert in e["props"]:
            if art == "spread":
                rest = wert["name"] if wert["t"] == "Name" else None
                if rest is None:
                    raise SyntaxFehler("Rest im Muster muss ein Name sein")
            elif art == "vorgabe":
                props.append((schluessel, False, {"t": "Name", "name": schluessel}, wert))
            else:
                (k, berechnet) = schluessel
                m = _als_muster(wert)
                if m["t"] == "MitVorgabe":
                    props.append((k, berechnet, m["ziel"], m["vorgabe"]))
                else:
                    props.append((k, berechnet, m, None))
        return {"t": "ObjMuster", "props": props, "rest": rest}
    raise SyntaxFehler("Ungültiges Ziel einer Zuweisung")


def _zahl_text(x: float) -> str:
    if x != x:
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "Infinity" if x > 0 else "-Infinity"
    if x == int(x) and abs(x) < 1e21:
        return str(int(x))
    r = repr(x)
    if "e" in r:
        mant, exp = r.split("e")
        exp = int(exp)
        return f"{mant}e{'+' if exp > 0 else '-'}{abs(exp)}"
    return r


def parsen(quelle: str) -> dict:
    return Parser(quelle).programm()
