"""sigma.py - Sigma-Regeln (https://github.com/SigmaHQ/sigma) für Prozessstarts auswerten.

Unterstützt die Teilmenge, die zu den Ereignissen der Wache passt: logsource category process_creation,
product windows; Felder Image, OriginalFileName, CommandLine, ParentImage, User; Modifikatoren contains,
startswith, endswith, all, re (mit i, m, s), windash, cased; Bedingungen mit and/or/not, Klammern, „1 of …“, „all of …“, „them“.
Eine Regel mit einem Feld oder Modifikator, den die Wache nicht kennt, wird nicht geladen – lieber keine
Regel als eine, die falsch anschlägt (gemeldet unter `uebersprungen`).

Regeln kommen aus zwei Orten: die mitgelieferten (sigma_regeln.py) und alle *.yml im Wache-Ordner
`sigma/` – dort kann der Nutzer Regeln selbst ablegen (die Wache lädt nichts aus dem Netz).
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ereignisse import Schwere

_FELDER = {"Image", "OriginalFileName", "CommandLine", "ParentImage", "User"}
_MODIFIKATOREN = {"contains", "startswith", "endswith", "all", "re", "windash", "cased", "i", "m", "s"}
CMD_MAX = 8192                     # so viel der Kommandozeile sieht eine Sigma-Regel
RE_ZEITLIMIT_S = 0.05              # |re: höchstens so lange je Wert (Paket regex)
_STUFEN = {"informational": Schwere.NIEDRIG, "low": Schwere.NIEDRIG, "medium": Schwere.MITTEL,
           "high": Schwere.HOCH, "critical": Schwere.KRITISCH}


@dataclass
class SigmaRegel:
    kennung: str
    titel: str
    beschreibung: str
    schwere: Schwere
    auswahl: dict                                  # Name → Liste von Bedingungs-Gruppen
    bedingung: str
    technik: str = ""
    fehlalarme: list = field(default_factory=list)


class Unbekannt(ValueError):
    pass


def _gruppen(wert) -> list[list[tuple[str, list[str], list]]]:
    """Auswahl → Liste von Gruppen (ODER); Gruppe = Liste von (Feld, Modifikatoren, Werte) (UND)."""
    if isinstance(wert, dict):
        wert = [wert]
    if not isinstance(wert, list):
        raise Unbekannt("Auswahl ohne Felder (Stichwortsuche wird nicht unterstützt)")
    gruppen = []
    for g in wert:
        if not isinstance(g, dict):
            raise Unbekannt("Stichwortsuche wird nicht unterstützt")
        teile = []
        for schluessel, werte in g.items():
            feld, *mods = str(schluessel).split("|")
            if feld not in _FELDER:
                raise Unbekannt(f"Feld {feld}")
            if set(mods) - _MODIFIKATOREN:
                raise Unbekannt(f"Modifikator {', '.join(sorted(set(mods) - _MODIFIKATOREN))}")
            if set(mods) & {"i", "m", "s"} and "re" not in mods:
                raise Unbekannt("Modifikator i/m/s nur mit re")
            liste = werte if isinstance(werte, list) else [werte]
            teile.append((feld, mods, [_vergleicher(w, mods) for w in liste]))
        gruppen.append(teile)
    return gruppen


def regel_aus(daten: dict) -> SigmaRegel:
    log = daten.get("logsource") or {}
    if log.get("category") != "process_creation" or log.get("product", "windows") != "windows":
        raise Unbekannt("nur process_creation unter Windows")
    det = dict(daten.get("detection") or {})
    bedingung = det.pop("condition", "")
    if isinstance(bedingung, list):
        bedingung = " or ".join(f"({b})" for b in bedingung)
    if not bedingung or not det:
        raise Unbekannt("keine Bedingung")
    det.pop("timeframe", None)
    auswahl = {name: _gruppen(wert) for name, wert in det.items()}
    _bedingung_pruefen(str(bedingung), auswahl)
    technik = " · ".join(t.split(".", 1)[1].upper() for t in (daten.get("tags") or [])
                         if str(t).lower().startswith("attack.t"))
    return SigmaRegel(kennung=str(daten.get("id") or daten.get("title") or "?"),
                      titel=str(daten.get("title") or "Sigma-Regel"),
                      beschreibung=" ".join(str(daten.get("description") or "").split())[:400],
                      schwere=_STUFEN.get(str(daten.get("level") or "medium").lower(), Schwere.MITTEL),
                      auswahl=auswahl, bedingung=str(bedingung), technik=technik,
                      fehlalarme=[str(f) for f in (daten.get("falsepositives") or [])][:5])


# ---------------------------------------------------------------------------
# Werte vergleichen
# ---------------------------------------------------------------------------

def _feldwert(feld: str, e, originalname) -> str:
    if feld == "Image":
        return e.exe or ""
    if feld == "CommandLine":
        return (e.cmdline or "")[:CMD_MAX]
    if feld == "User":
        return e.nutzer or ""
    if feld == "ParentImage":                     # die Wache kennt vom Elternprozess nur den Namen
        return "\\" + e.eltern if e.eltern else ""
    if feld == "OriginalFileName":
        return originalname(e.exe) if e.exe else ""
    return ""


def _windash(wert: str) -> list[str]:
    if not wert.startswith("-") and " -" not in wert:
        return [wert]
    return [wert, wert.replace("-", "/"), wert.replace("-", "–"), wert.replace("-", "—")]


def _stuecke(soll: str) -> list[list[str]]:
    """Sigma-Wert → Stücke zwischen den `*`, je Zeichen ein Regex-Baustein fester Länge (`?` = `.`).
    `\\*`, `\\?`, `\\\\` stehen für das Zeichen selbst, alles andere (auch `[`) gilt wörtlich."""
    stuecke: list[list[str]] = [[]]
    i = 0
    while i < len(soll):
        c = soll[i]
        if c == "\\" and i + 1 < len(soll) and soll[i + 1] in "*?\\":
            stuecke[-1].append(re.escape(soll[i + 1]))
            i += 2
            continue
        if c == "*":
            stuecke.append([])
        else:
            stuecke[-1].append("." if c == "?" else re.escape(c))
        i += 1
    return stuecke


def _platzhalter(soll: str, art: str, flags: int):
    """Vergleich mit `*`/`?` ohne Backtracking. Die Stücke zwischen den `*` haben feste Länge; sie werden der
    Reihe nach am frühesten Ort gesucht, das erste und letzte bei fester Kante. Der Aufwand wächst linear mit
    dem Text – `.*a.*b.*` mit fullmatch wüchse bei vielen `a` quadratisch (die Befehlszeile bestimmt der
    beobachtete Prozess)."""
    stuecke = _stuecke(soll)
    vorne = art in ("in", "ende") or len(stuecke) > 1 and not stuecke[0]      # davor darf etwas stehen
    hinten = art in ("in", "anfang") or len(stuecke) > 1 and not stuecke[-1]  # danach darf etwas stehen
    teile = [(len(s), re.compile("".join(s), flags) if s else None) for s in stuecke]
    letztes = len(teile) - 1

    def pruefen(ist: str) -> bool:
        pos, n = 0, len(ist)
        for j, (laenge, rx) in enumerate(teile):
            if rx is None:
                continue
            if j == 0 and not vorne:
                if rx.match(ist, 0) is None:
                    return False
                pos = laenge
            elif j == letztes and not hinten:
                start = n - laenge
                if start < pos or rx.match(ist, start) is None:
                    return False
                pos = n
            else:
                m = rx.search(ist, pos)
                if m is None:
                    return False
                pos = m.end()
        return hinten or pos == n
    return pruefen


def _woertlich(soll: str) -> str | None:
    """Sigma-Wert ohne Platzhalter als Text (Maskierungen aufgelöst) – None, wenn `*` oder `?` darin steht."""
    teile, i = [], 0
    while i < len(soll):
        c = soll[i]
        if c == "\\" and i + 1 < len(soll) and soll[i + 1] in "*?\\":
            teile.append(soll[i + 1])
            i += 2
            continue
        if c in "*?":
            return None
        teile.append(c)
        i += 1
    return "".join(teile)


def _regex_modul():
    """`regex` (mit Zeitlimit) aus dem venv, sonst `re`."""
    try:
        import regex
        return regex
    except ImportError:                               # pragma: no cover
        return None


def _vergleicher(soll, mods: list[str]):
    """Sigma-Wert einmal beim Laden in eine Prüffunktion übersetzen: ohne Platzhalter einfache Textvergleiche
    (enthält, beginnt, endet, gleich), sonst ein vorkompilierter Ausdruck; `|re` mit Zeitlimit."""
    if soll is None:
        return lambda ist: ist == ""
    soll = str(soll)
    if "re" in mods:                                  # Sigma: re unterscheidet Groß/klein, außer mit |i
        flags = ((re.IGNORECASE if "i" in mods else 0) | (re.MULTILINE if "m" in mods else 0)
                 | (re.DOTALL if "s" in mods else 0))
        rx = _regex_modul()
        try:
            muster = rx.compile(soll, flags) if rx is not None else re.compile(soll, flags)
        except Exception:
            return lambda ist: False
        if rx is None:
            return lambda ist: muster.search(ist) is not None

        def suchen(ist: str) -> bool:
            try:
                return muster.search(ist, timeout=RE_ZEITLIMIT_S) is not None
            except TimeoutError:                      # zu teurer Ausdruck: kein Treffer statt Hänger
                return False
        return suchen
    egal = "cased" not in mods                        # ohne |cased: Groß/klein egal
    art = "in" if "contains" in mods else "anfang" if "startswith" in mods else "ende" if "endswith" in mods \
        else "gleich"
    pruefer: list[tuple[str, object]] = []
    for s in (_windash(soll) if "windash" in mods else [soll]):
        text = _woertlich(s)
        if text is not None:
            pruefer.append((art, text.lower() if egal else text))
            continue
        pruefer.append(("rx", _platzhalter(s, art, re.DOTALL | (re.IGNORECASE if egal else 0))))

    def pruefen(ist: str) -> bool:
        k = ist.lower() if egal else ist
        for a, w in pruefer:
            if ((a == "rx" and w(ist)) or (a == "in" and w in k)
                    or (a == "anfang" and k.startswith(w)) or (a == "ende" and k.endswith(w))
                    or (a == "gleich" and k == w)):
                return True
        return False
    return pruefen


def _passt_wert(ist: str, soll, mods: list[str]) -> bool:
    return _vergleicher(soll, mods)(ist)


def _passt_gruppe(gruppe, e, originalname) -> bool:
    for feld, mods, werte in gruppe:
        ist = _feldwert(feld, e, originalname)
        treffer = [w(ist) for w in werte]
        if not (all(treffer) if "all" in mods else any(treffer)):
            return False
    return True


# ---------------------------------------------------------------------------
# Bedingung: kleiner Parser für and / or / not / Klammern / „1 of x*“ / „all of x*“
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def _namen(muster: str, auswahl: dict) -> list[str]:
    if muster == "them":
        return list(auswahl)
    return [n for n in auswahl if fnmatch.fnmatchcase(n, muster)]


def _bedingung_pruefen(bedingung: str, auswahl: dict) -> None:
    for t in _TOKEN.findall(bedingung):
        low = t.lower()
        if t in ("(", ")") or low in ("and", "or", "not", "of", "all", "1", "them") or t in auswahl:
            continue
        if "*" in t and _namen(t, auswahl):
            continue
        raise Unbekannt(f"Bedingung „{t}“")


def _auswerten(bedingung: str, ergebnis) -> bool:
    tokens = _TOKEN.findall(bedingung)
    pos = 0

    def nimm():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def schau():
        return tokens[pos].lower() if pos < len(tokens) else ""

    def ausdruck():
        wert = und()
        while schau() == "or":
            nimm()
            rechts = und()
            wert = wert or rechts
        return wert

    def und():
        wert = nicht()
        while schau() == "and":
            nimm()
            rechts = nicht()
            wert = wert and rechts
        return wert

    def nicht():
        if schau() == "not":
            nimm()
            return not nicht()
        return einfach()

    def einfach():
        t = nimm()
        if t == "(":
            wert = ausdruck()
            nimm()                                         # „)“
            return wert
        if t.lower() in ("1", "all") and schau() == "of":
            nimm()
            namen = ergebnis.namen(nimm())
            werte = [ergebnis(n) for n in namen]
            return (any(werte) if t == "1" else bool(werte) and all(werte))
        return ergebnis(t)

    return bool(ausdruck())


class _Ergebnis:
    def __init__(self, regel: SigmaRegel, e, originalname):
        self.regel, self.e, self.originalname, self._merk = regel, e, originalname, {}

    def __call__(self, name: str) -> bool:
        if name not in self._merk:
            gruppen = self.regel.auswahl.get(name, [])
            self._merk[name] = any(_passt_gruppe(g, self.e, self.originalname) for g in gruppen)
        return self._merk[name]

    def namen(self, muster: str) -> list[str]:
        return _namen(muster, self.regel.auswahl)


def trifft(regel: SigmaRegel, e, originalname=lambda _p: "") -> bool:
    return _auswerten(regel.bedingung, _Ergebnis(regel, e, originalname))


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

def laden(ordner: Path | None = None) -> tuple[list[SigmaRegel], list[str]]:
    """(Regeln, übersprungene mit Grund) – mitgelieferte plus *.yml/*.yaml aus `ordner`."""
    import yaml
    from .sigma_regeln import MITGELIEFERT
    texte = [("mitgeliefert", t) for t in MITGELIEFERT]
    if ordner is not None and ordner.is_dir():
        for p in sorted(list(ordner.glob("*.yml")) + list(ordner.glob("*.yaml")))[:2000]:
            try:
                texte.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    regeln, uebersprungen = [], []
    for quelle, text in texte:
        try:
            for daten in yaml.safe_load_all(text):
                if isinstance(daten, dict) and daten.get("detection"):
                    regeln.append(regel_aus(daten))
        except Unbekannt as e:
            uebersprungen.append(f"{quelle}: {e}")
        except Exception as e:                           # kaputtes YAML: nur diese Datei fällt aus
            uebersprungen.append(f"{quelle}: nicht lesbar ({type(e).__name__})")
    return regeln, uebersprungen
