"""
sofortcheck.py - Sofort-Check nach dem Schreiben einer Datei: Fehler mit Zeile zurück an die KI.

  .py     Syntax (compile, ohne Ausführen) + ruff E9/F (undefinierte Namen …), wenn installiert
  .json   json.loads
  .html   nicht geschlossene/falsch verschachtelte Tags, fehlende lokale src/href-Dateien,
          IDs, die das JS der Seite sucht, aber im HTML fehlen
  .css    Klammern { } außerhalb von Kommentaren und Zeichenketten
  .js     Klammern ( ) [ ] { } außerhalb von Kommentaren, Zeichenketten und Template-Strings

Kein Node, kein npm – alles in reinem Python. Leeres Ergebnis = nichts gefunden.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

MAX_FUNDE = 12
_LEER = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
         "track", "wbr", "!doctype"}
# Werden in HTML oft ohne End-Tag geschrieben (vom Parser automatisch geschlossen).
_OPTIONAL_ZU = {"p", "li", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "option", "colgroup",
                "caption", "rb", "rt", "rp", "optgroup", "html", "head", "body"}


def pruefen(pfad, inhalt: str | None = None) -> str:
    """Funde als Text (eine Zeile je Fund) – leer, wenn alles passt oder die Endung unbekannt ist."""
    p = Path(pfad)
    endung = p.suffix.lower()
    if inhalt is None:
        try:
            inhalt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    pruefer = {".py": _python, ".pyw": _python, ".json": _json, ".html": _html, ".htm": _html,
               ".css": _css, ".js": _js, ".mjs": _js}.get(endung)
    if pruefer is None:
        return ""
    try:
        funde = pruefer(p, inhalt)
    except Exception:
        return ""
    if not funde:
        return ""
    rest = len(funde) - MAX_FUNDE
    return "\n".join(funde[:MAX_FUNDE]) + (f"\n… (+{rest} weitere)" if rest > 0 else "")


def hinweis(pfad, inhalt: str | None = None) -> str:
    funde = pruefen(pfad, inhalt)
    if not funde:
        return ""
    return (f"\n\n🔎 Sofort-Check {Path(pfad).name}: Fehler gefunden – beheben, bevor es weitergeht:\n" + funde)


# --- Python / JSON -------------------------------------------------------------

def _python(p: Path, inhalt: str) -> list[str]:
    try:
        compile(inhalt, str(p), "exec", dont_inherit=True)
    except SyntaxError as e:
        return [f"Zeile {e.lineno}: Syntaxfehler – {e.msg}" + (f"  ({(e.text or '').strip()[:80]})" if e.text else "")]
    try:
        import pyumgebung
        ergebnis = pyumgebung.ruff_pruefen([p], p.parent if p.parent.is_dir() else None,
                                           regeln="E9,F821,F822,F823,F811,F632,F704,F706", inhalt=inhalt)
    except Exception:
        ergebnis = None
    if not ergebnis or not ergebnis[1]:
        return []
    funde = []
    for z in ergebnis[0].splitlines():
        if (m := re.search(r":(\d+):\d+: ([A-Z]+\d+) (?:\[\*\] )?(.*)", z)):
            funde.append(f"Zeile {m.group(1)}: {m.group(3)} ({m.group(2)})")
    return funde


def _json(p: Path, inhalt: str) -> list[str]:
    try:
        json.loads(inhalt)
    except json.JSONDecodeError as e:
        return [f"Zeile {e.lineno}, Spalte {e.colno}: {e.msg}"]
    return []


# --- HTML ----------------------------------------------------------------------

class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stapel: list[tuple[str, int]] = []
        self.funde: list[str] = []
        self.ids: set[str] = set()
        self.verweise: list[tuple[str, int]] = []
        self.skripte: list[str] = []
        self._im_skript = False

    def handle_starttag(self, tag, attrs):
        zeile = self.getpos()[0]
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        for feld in ("src", "href"):
            if a.get(feld):
                self.verweise.append((a[feld], zeile))
        if tag == "script":
            self._im_skript = not a.get("src")
        if tag not in _LEER:
            self.stapel.append((tag, zeile))

    def handle_startendtag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        for feld in ("src", "href"):
            if a.get(feld):
                self.verweise.append((a[feld], self.getpos()[0]))

    def handle_endtag(self, tag):
        zeile = self.getpos()[0]
        if tag == "script":
            self._im_skript = False
        if tag in _LEER:
            return
        offen = [t for t, _ in self.stapel]
        if tag not in offen:
            self.funde.append(f"Zeile {zeile}: </{tag}> schließt nichts – kein offenes <{tag}>")
            return
        while self.stapel:
            t, z = self.stapel.pop()
            if t == tag:
                break
            if t not in _OPTIONAL_ZU:
                self.funde.append(f"Zeile {z}: <{t}> wird nicht geschlossen (vor </{tag}> in Zeile {zeile})")

    def handle_data(self, data):
        if self._im_skript:
            self.skripte.append(data)


def _html(p: Path, inhalt: str) -> list[str]:
    h = _Html()
    h.feed(inhalt)
    h.close()
    funde = list(h.funde)
    funde += [f"Zeile {z}: <{t}> wird nicht geschlossen" for t, z in h.stapel if t not in _OPTIONAL_ZU]
    js = "\n".join(h.skripte)
    for ziel, zeile in h.verweise:
        sauber = ziel.split("#", 1)[0].split("?", 1)[0]
        if not sauber or re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|\{)", ziel, re.I):
            continue
        datei = (p.parent / sauber).resolve()
        if not datei.exists():
            funde.append(f"Zeile {zeile}: verweist auf '{ziel}' – die Datei gibt es nicht")
        elif datei.suffix.lower() in (".js", ".mjs"):
            try:
                js += "\n" + datei.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    gesucht = set(re.findall(r"getElementById\(\s*['\"]([\w-]+)['\"]\s*\)", js))
    gesucht |= set(re.findall(r"querySelector(?:All)?\(\s*['\"]#([\w-]+)['\"]\s*\)", js))
    for i in sorted(gesucht - h.ids):
        funde.append(f"Das JS sucht die ID '{i}', im HTML gibt es sie nicht")
    return funde


# --- CSS / JS: Klammern --------------------------------------------------------

_PAARE = {")": "(", "]": "[", "}": "{"}


def _klammern(inhalt: str, arten: str, js: bool) -> list[str]:
    stapel: list[tuple[str, int]] = []
    funde: list[str] = []
    i, zeile, n = 0, 1, len(inhalt)
    while i < n:
        c = inhalt[i]
        if c == "\n":
            zeile += 1
        elif inhalt.startswith("/*", i):
            ende = inhalt.find("*/", i + 2)
            ende = n if ende < 0 else ende + 2
            zeile += inhalt.count("\n", i, ende)
            i = ende
            continue
        elif js and inhalt.startswith("//", i):
            ende = inhalt.find("\n", i)
            i = n if ende < 0 else ende
            continue
        elif js and c == "/" and _regex_moeglich(inhalt, i):
            j, klasse = i + 1, False                   # regulärer Ausdruck: /\(/g, /[)]/ …
            while j < n and inhalt[j] != "\n":
                if inhalt[j] == "\\":
                    j += 2
                    continue
                if inhalt[j] == "[":
                    klasse = True
                elif inhalt[j] == "]":
                    klasse = False
                elif inhalt[j] == "/" and not klasse:
                    break
                j += 1
            i = j + 1
            continue
        elif c in "\"'" or (js and c == "`"):
            j = i + 1
            while j < n and inhalt[j] != c:
                if inhalt[j] == "\\":
                    j += 1
                elif inhalt[j] == "\n" and c != "`":
                    break
                j += 1
            zeile += inhalt.count("\n", i, min(j, n))
            i = j + 1
            continue
        elif c in arten and c in "([{":
            stapel.append((c, zeile))
        elif c in arten and c in ")]}":
            if not stapel or stapel[-1][0] != _PAARE[c]:
                funde.append(f"Zeile {zeile}: '{c}' ohne passende öffnende Klammer")
                if len(funde) >= MAX_FUNDE:
                    return funde
            else:
                stapel.pop()
        i += 1
    funde += [f"Zeile {z}: '{k}' wird nicht geschlossen" for k, z in stapel]
    return funde


def _regex_moeglich(inhalt: str, i: int) -> bool:
    """Beginnt an Stelle i ein regulärer Ausdruck? Ja nach Operator, Klammer auf, Komma, Zeilenanfang
    oder return/typeof/case – nach Namen, Zahlen und ) ] ist / eine Division."""
    k = i - 1
    while k >= 0 and inhalt[k].isspace():
        k -= 1
    if k < 0 or inhalt[k] in "(,=:[!&|?{};+-*%<>~^":
        return True
    return bool(re.search(r"\b(return|typeof|case|in|of|delete|void|throw|new|else|do)$", inhalt[max(0, k - 9):k + 1]))


def _css(p: Path, inhalt: str) -> list[str]:
    return _klammern(inhalt, "{}", js=False)


def _js(p: Path, inhalt: str) -> list[str]:
    return _klammern(inhalt, "()[]{}", js=True)
