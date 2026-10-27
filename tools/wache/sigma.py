"""sigma.py - Sigma-Regeln (https://github.com/SigmaHQ/sigma) für Prozessstarts auswerten.

Unterstützt die Teilmenge, die zu den Ereignissen der Wache passt: logsource category process_creation,
product windows; Felder Image, OriginalFileName, CommandLine, ParentImage, User; Modifikatoren contains,
startswith, endswith, all, re, windash; Bedingungen mit and/or/not, Klammern, „1 of …“, „all of …“, „them“.
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
_MODIFIKATOREN = {"contains", "startswith", "endswith", "all", "re", "windash", "cased"}
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
            liste = werte if isinstance(werte, list) else [werte]
            teile.append((feld, mods, liste))
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
        return e.cmdline or ""
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


def _passt_wert(ist: str, soll, mods: list[str]) -> bool:
    if soll is None:
        return ist == ""
    soll = str(soll)
    if "re" in mods:
        try:
            return re.search(soll, ist, 0 if "cased" in mods else re.IGNORECASE) is not None
        except re.error:
            return False
    if "cased" not in mods:
        ist, soll = ist.lower(), soll.lower()
    for s in (_windash(soll) if "windash" in mods else [soll]):
        if "contains" in mods:
            muster = f"*{s}*"
        elif "startswith" in mods:
            muster = f"{s}*"
        elif "endswith" in mods:
            muster = f"*{s}"
        else:
            muster = s
        if "*" in muster or "?" in muster:
            if fnmatch.fnmatchcase(ist, muster):
                return True
        elif ist == muster:
            return True
    return False


def _passt_gruppe(gruppe, e, originalname) -> bool:
    for feld, mods, werte in gruppe:
        ist = _feldwert(feld, e, originalname)
        treffer = [_passt_wert(ist, w, mods) for w in werte]
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
