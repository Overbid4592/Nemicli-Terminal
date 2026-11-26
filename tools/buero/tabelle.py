"""tabelle.py – Arbeitsmappe aus Blättern; jede Zelle hält die Eingabe als Text („12,5“, „=SUMME(A1:A3)“).

Berechnet wird die ganze Mappe in Abhängigkeits-Reihenfolge (ohne Rekursion, auch bei langen Ketten);
Kreisbezüge ergeben #ZIRKEL!. Beim Einfügen und Löschen von Zeilen/Spalten werden die Bezüge der Formeln
mitgeführt; ein gelöschter Bezug wird zu #BEZUG!.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from buero import formel as F

MAX_ZELLEN = 2_000_000


@dataclass
class Blatt:
    name: str
    zellen: dict[tuple[int, int], str] = field(default_factory=dict)     # (zeile, spalte) -> Eingabe
    breiten: dict[int, int] = field(default_factory=dict)                # Spalte -> Breite in px
    formate: dict[tuple[int, int], dict] = field(default_factory=dict)   # (zeile, spalte) -> {hg, fg, fett}

    def groesse(self) -> tuple[int, int]:
        """(Zeilen, Spalten) bis zur letzten belegten oder eingefärbten Zelle."""
        alle = list(self.zellen) + list(self.formate)
        if not alle:
            return 0, 0
        return max(z for z, _ in alle) + 1, max(s for _, s in alle) + 1


FORMAT_FELDER = ("hg", "fg", "fett")          # Füllfarbe, Schriftfarbe (#rrggbb), fett


def format_sauber(f) -> dict:
    """Nur bekannte Felder mit gültigen Werten; leeres Dict = Standard."""
    from buero.textmodell import farbe_sauber
    if not isinstance(f, dict):
        return {}
    raus = {}
    for k in ("hg", "fg"):
        if (c := farbe_sauber(f.get(k))):
            raus[k] = c
    if f.get("fett") is True:
        raus["fett"] = True
    return raus


def wert_aus_eingabe(roh: str):
    """Wert einer Eingabe ohne Formel: None, Zahl, Datum, Wahrheitswert oder Text."""
    if roh is None or roh == "":
        return None
    if roh.startswith("'"):
        return roh[1:]
    if re.fullmatch(r"0\d+", roh.strip()):           # führende Null (Postleitzahl, Nummer): bleibt Text
        return roh
    if (z := F.zahl_aus_text(roh)) is not None:
        return z
    t = roh.strip().upper()
    if t in ("WAHR", "TRUE"):
        return True
    if t in ("FALSCH", "FALSE"):
        return False
    if (fehler := F.FEHLER.get(roh.strip())):
        return fehler
    return roh


def ist_formel(roh: str) -> bool:
    return isinstance(roh, str) and len(roh) > 1 and roh.startswith("=")


def anzeige_wert(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "WAHR" if v else "FALSCH"
    if isinstance(v, float):
        return F.zahl_text(v)
    return str(v)


class Mappe:
    def __init__(self, blaetter: list[Blatt] | None = None):
        self.blaetter: list[Blatt] = blaetter if blaetter is not None else [Blatt("Tabelle1")]
        self.werte: dict[tuple[str, int, int], object] = {}
        self._baeume: dict[str, object] = {}
        self._sortiert: dict[str, list] = {}

    # -- Blätter ---------------------------------------------------------------------------
    def blatt(self, name: str) -> Blatt | None:
        n = name.lower()
        return next((b for b in self.blaetter if b.name.lower() == n), None)

    def neuer_name(self) -> str:
        i = len(self.blaetter) + 1
        while self.blatt(f"Tabelle{i}"):
            i += 1
        return f"Tabelle{i}"

    def blatt_dazu(self, name: str | None = None) -> Blatt:
        b = Blatt(name or self.neuer_name())
        self.blaetter.append(b)
        return b

    def umbenennen(self, alt: str, neu: str) -> None:
        neu = neu.strip()
        if not neu or "!" in neu or "[" in neu or "]" in neu or len(neu) > 80:
            raise ValueError("Ungültiger Blattname")
        if self.blatt(neu) and neu.lower() != alt.lower():
            raise ValueError("Den Namen gibt es schon")
        b = self.blatt(alt)
        if b is None:
            return
        for blatt in self.blaetter:                 # Bezüge auf das Blatt mitnehmen
            for k, roh in list(blatt.zellen.items()):
                if ist_formel(roh):
                    blatt.zellen[k] = _bezuege_aendern(roh, blatt.name,
                                                       lambda bl, z, s: (neu if bl and bl.lower() == alt.lower()
                                                                         else bl, z, s))
        b.name = neu

    # -- Zellen ----------------------------------------------------------------------------
    def setzen(self, blatt: Blatt, zeile: int, spalte: int, roh: str) -> None:
        if roh is None or roh == "":
            blatt.zellen.pop((zeile, spalte), None)
            return
        if (zeile, spalte) not in blatt.zellen and self.anzahl() >= MAX_ZELLEN:
            raise ValueError(f"Mehr als {MAX_ZELLEN} Zellen")
        blatt.zellen[(zeile, spalte)] = roh

    def anzahl(self) -> int:
        return sum(len(b.zellen) for b in self.blaetter)

    @staticmethod
    def format(blatt: Blatt, zeile: int, spalte: int) -> dict:
        return blatt.formate.get((zeile, spalte), {})

    @staticmethod
    def format_setzen(blatt: Blatt, zeile: int, spalte: int, f: dict) -> None:
        f = format_sauber(f)
        if f:
            blatt.formate[(zeile, spalte)] = f
        else:
            blatt.formate.pop((zeile, spalte), None)

    def roh(self, blatt: Blatt, zeile: int, spalte: int) -> str:
        return blatt.zellen.get((zeile, spalte), "")

    def wert(self, blatt: Blatt, zeile: int, spalte: int):
        roh = blatt.zellen.get((zeile, spalte), "")
        if ist_formel(roh):
            return self.werte.get((blatt.name.lower(), zeile, spalte), F.ZIRKEL)
        return wert_aus_eingabe(roh)

    def anzeige(self, blatt: Blatt, zeile: int, spalte: int) -> str:
        roh = blatt.zellen.get((zeile, spalte), "")
        if ist_formel(roh):
            return anzeige_wert(self.wert(blatt, zeile, spalte))
        return roh[1:] if roh.startswith("'") else roh

    # -- Berechnen -------------------------------------------------------------------------
    def _baum(self, roh: str):
        if roh not in self._baeume:
            if len(self._baeume) > 50_000:
                self._baeume.clear()
            try:
                self._baeume[roh] = F.baum(roh)
            except F._Abbruch as a:
                self._baeume[roh] = ("wert", a.fehler)
        return self._baeume[roh]

    def berechnen(self) -> None:
        """Alle Formeln neu rechnen."""
        knoten: dict[tuple[str, int, int], object] = {}
        for b in self.blaetter:
            n = b.name.lower()
            for (z, s), roh in b.zellen.items():
                if ist_formel(roh):
                    knoten[(n, z, s)] = self._baum(roh)
        # Formelzellen je Blatt für Bereichs-Abhängigkeiten
        je_blatt: dict[str, list[tuple[int, int]]] = {}
        for (n, z, s) in knoten:
            je_blatt.setdefault(n, []).append((z, s))
        nachfolger: dict[tuple, list] = {k: [] for k in knoten}
        offen: dict[tuple, int] = {k: 0 for k in knoten}
        for k, b in knoten.items():
            for ref in F.bezuege(b, k[0]):
                bl = (ref[1] or k[0]).lower()
                if ref[0] == "z":
                    ziele = [(bl, ref[2], ref[3])] if (bl, ref[2], ref[3]) in knoten else []
                else:
                    (z1, s1), (z2, s2) = ref[2], ref[3]
                    z1, z2, s1, s2 = min(z1, z2), max(z1, z2), min(s1, s2), max(s1, s2)
                    ziele = [(bl, z, s) for z, s in je_blatt.get(bl, ()) if z1 <= z <= z2 and s1 <= s <= s2]
                for ziel in ziele:
                    nachfolger[ziel].append(k)
                    offen[k] += 1
        reihe = deque(k for k, n in offen.items() if n == 0)
        self.werte = {}
        self._sortiert = {}
        while reihe:
            k = reihe.popleft()
            self.werte[k] = self._rechnen(k, knoten[k])
            for nach in nachfolger[k]:
                offen[nach] -= 1
                if offen[nach] == 0:
                    reihe.append(nach)
        for k in knoten:                           # übrig: im Kreis oder hängt an einem Kreis
            self.werte.setdefault(k, F.ZIRKEL)

    def _rechnen(self, k, baum):
        n = k[0]

        def wert(blatt_name, z, s):
            b = self.blatt(blatt_name)
            if b is None:
                raise F._Abbruch(F.BEZUG)
            v = self.wert(b, z, s)
            return v

        def zellen(blatt_name, a, b):
            bl = self.blatt(blatt_name)
            if bl is None:
                raise F._Abbruch(F.BEZUG)
            (z1, s1), (z2, s2) = a, b
            if (z2 - z1 + 1) * (s2 - s1 + 1) <= 4096:          # kleiner Bereich: direkt nachsehen
                return [self.wert(bl, z, s) for z in range(z1, z2 + 1) for s in range(s1, s2 + 1)
                        if (z, s) in bl.zellen]
            schluessel = self._sortiert.get(bl.name.lower())
            if schluessel is None:
                schluessel = self._sortiert[bl.name.lower()] = sorted(bl.zellen)
            return [self.wert(bl, z, s) for (z, s) in schluessel if z1 <= z <= z2 and s1 <= s <= s2]

        r = F.Rechner(wert, zellen, lambda name: self.blatt(name) is not None, self._name_von(n))
        return r.rechnen(baum)

    def _name_von(self, klein: str) -> str:
        b = self.blatt(klein)
        return b.name if b else klein

    # -- Zeilen und Spalten ----------------------------------------------------------------
    def einfuegen(self, blatt: Blatt, achse: str, ab: int, anzahl: int = 1) -> None:
        """Zeilen (achse="zeile") oder Spalten (achse="spalte") ab Index `ab` einfügen."""
        self._verschieben(blatt, achse, ab, anzahl)

    def loeschen(self, blatt: Blatt, achse: str, ab: int, anzahl: int = 1) -> None:
        self._verschieben(blatt, achse, ab, -anzahl)

    def _verschieben(self, blatt: Blatt, achse: str, ab: int, d: int) -> None:
        zeile = achse == "zeile"
        neu = {}
        for (z, s), roh in blatt.zellen.items():
            pos = z if zeile else s
            if d < 0 and ab <= pos < ab - d:
                continue                                     # gelöscht
            if pos >= ab:
                pos += d
            neu[(pos, s) if zeile else (z, pos)] = roh
        blatt.zellen = neu
        neu_f = {}
        for (z, s), f in blatt.formate.items():             # Farben wandern mit
            pos = z if zeile else s
            if d < 0 and ab <= pos < ab - d:
                continue
            if pos >= ab:
                pos += d
            neu_f[(pos, s) if zeile else (z, pos)] = f
        blatt.formate = neu_f
        if not zeile:
            blatt.breiten = {(s + d if s >= ab else s): b for s, b in blatt.breiten.items()
                             if not (d < 0 and ab <= s < ab - d)}
        name = blatt.name.lower()

        def anpassen(bl, z, s):
            if (bl or "").lower() != name:
                return bl, z, s
            pos = z if zeile else s
            if d < 0 and ab <= pos < ab - d:
                return bl, None, None                       # Bezug gelöscht
            if pos >= ab:
                pos += d
            return (bl, pos, s) if zeile else (bl, z, pos)

        for b in self.blaetter:
            for k, roh in list(b.zellen.items()):
                if ist_formel(roh):
                    b.zellen[k] = _bezuege_aendern(roh, b.name, anpassen)

    # -- Ausgabe ---------------------------------------------------------------------------
    def als_markdown(self, blatt: Blatt, max_zeilen: int = 200, max_spalten: int = 30,
                     adressen: bool = True, formeln: bool = False) -> str:
        """Belegter Bereich als Markdown-Tabelle (mit Spaltenbuchstaben und Zeilennummern für die KI)."""
        zeilen, spalten = blatt.groesse()
        zeilen, spalten = min(zeilen, max_zeilen), min(spalten, max_spalten)
        if not zeilen:
            return ""

        def zelle(z, s):
            roh = self.roh(blatt, z, s)
            t = self.anzeige(blatt, z, s)
            if formeln and ist_formel(roh):
                t = f"{t} ({roh})"
            return t.replace("|", "\\|").replace("\n", " ")

        raus = []
        if adressen:
            raus.append("| | " + " | ".join(F.spalte_name(s) for s in range(spalten)) + " |")
            raus.append("|---" * (spalten + 1) + "|")
            for z in range(zeilen):
                raus.append(f"| {z + 1} | " + " | ".join(zelle(z, s) for s in range(spalten)) + " |")
        else:
            for z in range(zeilen):
                raus.append("| " + " | ".join(zelle(z, s) for s in range(spalten)) + " |")
                if z == 0:
                    raus.append("|---" * spalten + "|")
        return "\n".join(raus)


def _bezuege_aendern(roh: str, eigenes_blatt: str, fn) -> str:
    """Formel neu schreiben, Bezüge über fn(blatt, zeile, spalte) -> (blatt, zeile, spalte) geändert;
    zeile None = Bezug weg (#BEZUG!)."""
    try:
        toks = F.zerlegen(roh)
    except F._Abbruch:
        return roh
    teile = []
    for t in toks:
        if t.art in ("ref", "bereich"):
            blatt = t.wert[0]
            stuecke = t.roh.split(":")
            neu = []
            weg = False
            for st, (z, s) in zip(stuecke, t.wert[1:]):
                bl, z2, s2 = fn(blatt or eigenes_blatt, z, s)
                if z2 is None:
                    weg = True
                    break
                m = re.fullmatch(r"(\$?)[A-Za-z]{1,3}(\$?)\d{1,7}", st)
                d1, d2 = (m.group(1), m.group(2)) if m else ("", "")
                neu.append(f"{d1}{F.spalte_name(s2)}{d2}{z2 + 1}")
                if blatt:
                    blatt = bl
            if weg:
                teile.append("#BEZUG!")
                continue
            teile.append(F._blatt_text(blatt, "de") + ":".join(neu))
        else:
            teile.append(_tok_text(t))
    return "=" + "".join(teile)


def _tok_text(t: F.Tok) -> str:
    if t.art == "zahl":
        return (t.roh or repr(t.wert)).replace(".", ",")
    if t.art == "text":
        return '"' + str(t.wert).replace('"', '""') + '"'
    if t.art == "bool":
        return "WAHR" if t.wert else "FALSCH"
    if t.art == "sep":
        return ";"
    return str(t.wert)


def aus_markdown(text: str) -> list[list[str]]:
    """Erste Markdown-Tabelle (oder CSV-artige Zeilen mit ; oder Tab) aus einem Text als Zeilen von Zellen."""
    zeilen = [z.strip() for z in text.strip().splitlines()]
    tabelle = [z for z in zeilen if z.startswith("|") and z.endswith("|") and len(z) > 1]
    if tabelle:
        raus = []
        for z in tabelle:
            if re.fullmatch(r"\|[\s:|-]+\|", z):
                continue
            zellen = re.split(r"(?<!\\)\|", z[1:-1])
            raus.append([c.strip().replace("\\|", "|") for c in zellen])
        return raus
    trenner = "\t" if any("\t" in z for z in zeilen) else ";" if any(";" in z for z in zeilen) else None
    if trenner:
        return [z.split(trenner) for z in zeilen if z]
    return []
