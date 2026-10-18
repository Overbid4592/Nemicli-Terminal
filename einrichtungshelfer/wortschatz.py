"""Eigener Wortschatz (BPE) in reinem Python – Training und Kodieren.

Vorzerlegung wie bei GPT-2 (Wortstücke mit führendem Leerzeichen); Sondermarken und Platzhalter bleiben ganz.
Unbekannte Zeichen werden zu <?>.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from einrichtungshelfer.schema import ATOMAR

def _zerleger(atomar: tuple[str, ...]) -> re.Pattern:
    a = "|".join(re.escape(x) for x in atomar)
    return re.compile(rf"({a})| ?\w+| ?(?:(?!{a})[^\w\s])+|\s+")


_ZERLEGEN = _zerleger(ATOMAR)


def stuecke(text: str, zerleger: re.Pattern = _ZERLEGEN) -> list[str]:
    return [m.group(0) for m in zerleger.finditer(text)]


class Wortschatz:
    def __init__(self, zeichen: list[str], merges: list[tuple[str, str]], atomar: tuple[str, ...] = ATOMAR):
        self.atomar = tuple(atomar)
        self._zerleger = _zerleger(self.atomar)
        self.merges = [tuple(m) for m in merges]
        self.rang = {m: i for i, m in enumerate(self.merges)}
        self.tokens: list[str] = list(self.atomar) + list(zeichen) + [a + b for a, b in self.merges]
        self.id = {t: i for i, t in enumerate(self.tokens)}
        self.unbekannt = self.id["<?>"]
        self._cache: dict[str, list[int]] = {}

    def __len__(self) -> int:
        return len(self.tokens)

    def _stueck(self, s: str) -> list[int]:
        if s in self._cache:
            return self._cache[s]
        teile = list(s)
        while len(teile) > 1:
            beste, rang = -1, len(self.rang)
            for i in range(len(teile) - 1):
                r = self.rang.get((teile[i], teile[i + 1]), rang)
                if r < rang:
                    beste, rang = i, r
            if beste < 0:
                break
            teile[beste:beste + 2] = [teile[beste] + teile[beste + 1]]
        ids = [self.id.get(t, self.unbekannt) for t in teile]
        if len(self._cache) < 50_000:
            self._cache[s] = ids
        return ids

    def kodieren(self, text: str) -> list[int]:
        aus: list[int] = []
        for s in stuecke(text, self._zerleger):
            if s in self.atomar:
                aus.append(self.id[s])
            else:
                aus.extend(self._stueck(s))
        return aus

    def dekodieren(self, ids: list[int]) -> str:
        return "".join(self.tokens[i] for i in ids)

    def speichern(self, pfad: Path) -> None:
        zeichen = self.tokens[len(self.atomar):len(self.tokens) - len(self.merges)]
        daten = {"zeichen": zeichen, "merges": self.merges}
        if self.atomar != ATOMAR:
            daten["atomar"] = list(self.atomar)
        pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def laden(cls, pfad: Path) -> "Wortschatz":
        d = json.loads(Path(pfad).read_text(encoding="utf-8"))
        return cls(d["zeichen"], [tuple(m) for m in d["merges"]], tuple(d.get("atomar", ATOMAR)))


def trainieren(texte: list[str], groesse: int = 2048, atomar: tuple[str, ...] = ATOMAR) -> Wortschatz:
    """Klassisches BPE über die Häufigkeit der Wortstücke; Paar-Zähler werden nach jedem Schritt nachgeführt."""
    zerleger = _zerleger(atomar)
    haeufig = Counter(s for t in texte for s in stuecke(t, zerleger) if s not in atomar)
    woerter = [list(w) for w in haeufig]
    zahl = list(haeufig.values())
    zeichen = sorted({c for w in woerter for c in w})
    paare: Counter = Counter()
    wo: dict[tuple[str, str], set[int]] = defaultdict(set)
    for i, w in enumerate(woerter):
        for a, b in zip(w, w[1:]):
            paare[(a, b)] += zahl[i]
            wo[(a, b)].add(i)
    merges: list[tuple[str, str]] = []
    while len(atomar) + len(zeichen) + len(merges) < groesse and paare:
        paar, n = max(paare.items(), key=lambda kv: kv[1])
        if n < 2:
            break
        merges.append(paar)
        neu = paar[0] + paar[1]
        for i in list(wo.get(paar, ())):
            w = woerter[i]
            for a, b in zip(w, w[1:]):                         # alte Paare dieses Worts abziehen
                paare[(a, b)] -= zahl[i]
                if paare[(a, b)] <= 0:
                    del paare[(a, b)]
                wo[(a, b)].discard(i)
            j, neu_w = 0, []
            while j < len(w):
                if j < len(w) - 1 and (w[j], w[j + 1]) == paar:
                    neu_w.append(neu)
                    j += 2
                else:
                    neu_w.append(w[j])
                    j += 1
            woerter[i] = neu_w
            for a, b in zip(neu_w, neu_w[1:]):
                paare[(a, b)] += zahl[i]
                wo[(a, b)].add(i)
        wo.pop(paar, None)
        paare.pop(paar, None)
    return Wortschatz(zeichen, merges, atomar)
