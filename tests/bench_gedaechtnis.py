"""Messlauf Gedächtnis gegen Chat: wie lange braucht die Suche einer Runde, und wie oft stockt die
Ereignisschleife (Ersatz für das Token-Streaming) – ohne und mit paralleler Konsolidierung.

Synthetische Daten in einem Temp-Ordner, kein Embedding-Modell: Vektoren sind Zufall, das Einbetten der
Suchfrage wird mit --einbetten-ms nachgestellt (time.sleep gibt das GIL frei wie torch beim Rechnen).

    venv/Scripts/python.exe tests/bench_gedaechtnis.py --brocken 10000 --einbetten-ms 300
"""
from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import random
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.argv[0] = "unittest-bench"         # paths._testlauf(): Test-Datenordner, nie der eingestellte
ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

DIM = 1024
EINGEBETTET = [0]                      # Suchfragen mit Vektor (sonst nur Stichworte)
WOERTER = ("garten herbst drucker kater signatur wache bericht modell grafik speicher bild wald hund "
           "treiber update kontext anfrage server fenster tastatur netz datei ordner skill notiz").split()


def _vektor(rng: random.Random) -> list[float]:
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


def _einrichten(ordner: Path, einbetten_ms: float, seed: int = 1):
    import indexdb
    import memory
    rng = random.Random(seed)
    indexdb._ROOT = ordner
    indexdb._DB_NEW = ordner / "learned" / "memory.db"
    indexdb._DB_OLD = ordner / "learned" / "index.sqlite"
    memory._embed_texts = lambda texts, **k: [_vektor(rng) for _ in texts]
    memory.modell_kennung = lambda: "bench"
    memory.encoder_loaded = lambda: True
    memory.warm_encoder = lambda: None

    def frage(text, aufgabe="gedaechtnis"):
        EINGEBETTET[0] += 1
        time.sleep(einbetten_ms / 1000)
        return [_vektor(rng)]
    memory.frage_einbetten = frage
    import chatstore
    chatstore.aktueller_chat = lambda: None
    return indexdb


def _satz(rng: random.Random) -> str:
    return " ".join(rng.choice(WOERTER) for _ in range(rng.randint(12, 40))) + "."


def _fuellen(indexdb, brocken: int, je_quelle: int = 20, start: int = 0, seed: int = 2) -> None:
    rng = random.Random(seed)
    for q in range(start, start + brocken // je_quelle):
        indexdb._upsert("chat", f"chat:{q}", [f"Nutzer: {_satz(rng)} Nemi: {_satz(rng)} {q}-{i}"
                                              for i in range(je_quelle)])


def _konsolidieren(ordner: str, dauer_s: float, start: int, eigener_prozess: bool = True) -> None:
    """Hintergrund-Last wie der Bibliothekar: neue Brocken einbetten (Zufallsvektoren in reinem Python, hält
    das GIL – ungünstiger als torch) und schreiben."""
    if eigener_prozess:
        indexdb = _einrichten(Path(ordner), 0, seed=start)
    else:
        import indexdb                 # im selben Prozess schon eingerichtet
    ende, q = time.monotonic() + dauer_s, start
    while time.monotonic() < ende:
        _fuellen(indexdb, 40, start=q, seed=q)
        q += 2


class Schleife:
    """Misst, wie stark die Ereignisschleife stockt: alle 5 ms ein Takt, Verspätung je Takt."""

    TAKT = 0.005

    def __init__(self):
        self.spaet: list[float] = []

    async def laufen(self, bis: float) -> None:
        while time.monotonic() < bis:
            t = time.perf_counter()
            await asyncio.sleep(self.TAKT)
            self.spaet.append(max(0.0, time.perf_counter() - t - self.TAKT) * 1000)

    def bericht(self) -> str:
        s = sorted(self.spaet) or [0.0]
        p99 = s[min(len(s) - 1, int(len(s) * 0.99))]
        return f"Schleife stockt: p99 {p99:.1f} ms · max {s[-1]:.1f} ms · über 50 ms: {sum(x > 50 for x in s)}×"


def _suchen(indexdb, fragen: list[str]) -> tuple[list[float], list[float]]:
    vorab, voll = [], []
    for f in fragen:
        t = time.perf_counter()
        indexdb.VORAB.melden = lambda _b, t=t: vorab.append((time.perf_counter() - t) * 1000)
        try:
            indexdb.guide_block(f)
        finally:
            indexdb.VORAB.melden = None
        voll.append((time.perf_counter() - t) * 1000)
    return vorab, voll


def _ms(xs: list[float]) -> str:
    if not xs:
        return "-"
    return f"Median {statistics.median(xs):.0f} ms · max {max(xs):.0f} ms"


async def _szenario(name: str, indexdb, fragen, last=None) -> None:
    schleife = Schleife()
    if last is not None:
        last.start()
        await asyncio.sleep(0.5)
    ergebnis = {}

    def arbeit():
        ergebnis["v"] = _suchen(indexdb, fragen)

    EINGEBETTET[0] = 0
    faden = threading.Thread(target=arbeit)
    faden.start()
    await schleife.laufen(time.monotonic() + 0.2)
    while faden.is_alive():
        await schleife.laufen(time.monotonic() + 0.1)
    if last is not None:
        last.join() if isinstance(last, threading.Thread) else last.join(60)
    vorab, voll = ergebnis["v"]
    budget = sum(x <= 200 for x in voll)
    print(f"\n[{name}]")
    print(f"  Stichwort-Zwischenstand: {_ms(vorab)}")
    print(f"  volle Suche:             {_ms(voll)} · im Budget 200 ms: {budget}/{len(voll)} · "
          f"mit Vektoren: {EINGEBETTET[0]}/{len(voll)} · erste Suche {voll[0]:.0f} ms")
    print(f"  {schleife.bericht()}")


async def haupt(a) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ordner = Path(tmp)
        indexdb = _einrichten(ordner, a.einbetten_ms)
        t = time.perf_counter()
        _fuellen(indexdb, a.brocken)
        print(f"{a.brocken} Brocken angelegt in {time.perf_counter() - t:.1f} s · Einbetten der Frage nachgestellt "
              f"mit {a.einbetten_ms:.0f} ms")
        rng = random.Random(7)
        fragen = [" ".join(rng.sample(WOERTER, 4)) for _ in range(a.runden)]

        leer = Schleife()
        await leer.laufen(time.monotonic() + 2.0)
        print()
        print("[Leerlauf, Grundlinie]")
        print(f"  {leer.bericht()}")
        indexdb._CACHE["meta"] = None
        indexdb._invalidate()
        await _szenario("kalt: Index fehlt, baut im Hintergrund", indexdb, fragen[:3])
        if indexdb._index_bau is not None:
            indexdb._index_bau.join(120)
        indexdb._index()
        await _szenario("warm, ohne Konsolidierung", indexdb, fragen)
        dauer = 4.0
        await _szenario("Konsolidierung im selben Prozess (Faden)", indexdb, fragen,
                        threading.Thread(target=_konsolidieren, args=(tmp, dauer, 100_000, False)))
        indexdb._index()
        kind = mp.get_context("spawn").Process(target=_konsolidieren, args=(tmp, dauer, 200_000))
        await _szenario("Konsolidierung im eigenen Prozess", indexdb, fragen, kind)
        await _szenario("danach ohne Vorwärmen (erste Suche lädt nach)", indexdb, fragen[:3])
        kind = mp.get_context("spawn").Process(target=_konsolidieren, args=(tmp, dauer, 300_000))
        kind.start()
        kind.join(60)
        indexdb.vorwaermen()
        indexdb._index_bau.join(60)
        await _szenario("danach mit Vorwärmen", indexdb, fragen[:3])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--brocken", type=int, default=10_000)
    p.add_argument("--einbetten-ms", type=float, default=300.0)
    p.add_argument("--runden", type=int, default=12)
    asyncio.run(haupt(p.parse_args()))
