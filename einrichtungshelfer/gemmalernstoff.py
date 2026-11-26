"""
gemmalernstoff.py - Lernstoff und Training des Gemma-Helfers (Werkstatt, nicht in der exe).

Der Lernstoff kommt aus daten.erzeugen (synthetische Einrichtungs-Gespräche ohne persönliche Daten, Namen
aus Töpfen), umgeschrieben in Chat-Form: Zustand in den Systemtext, Eingabe als Nachricht,
Antwort mit Aktions-Klammer. Geprüft wird mit Runden, deren Namen im Training nie vorkommen.

  python -m einrichtungshelfer.gemmalernstoff trainieren <gguf> [--schritte 3000] [--rang 16]
  python -m einrichtungshelfer.gemmalernstoff pruefen <gguf> [--anzahl 200]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import daten
from .gemmahelfer import LEER, lora_ordner, system_text
from .schema import aktion_lesen

_RUNDE = re.compile(r"<z>(.*?)</z><n>(.*?)</n><h>(.*?)</h>", re.S)
LIZENZ = """Dieses LoRA verändert Gemma 4 (Google), lizenziert unter der Apache License 2.0:
https://ai.google.dev/gemma/docs/gemma_4_license

Veränderung: ein LoRA-Zusatz für die Einrichtung von NemiCLI (Gespräch mit Aktions-Klammer), trainiert mit
dem LoRA-Motor von NemiCLI auf synthetischem Lernstoff. Das Grundmodell selbst wird nicht mitgeliefert.
"""


def runde(text: str) -> dict | None:
    """'<z>…</z><n>…</n><h>…</h>' -> {system, frage, antwort, zust}."""
    m = _RUNDE.fullmatch(text.strip())
    if not m:
        return None
    zust, nutzer, antwort = m.groups()
    antwort = antwort.strip()
    if not antwort.startswith("["):              # Fragen im Lernstoff haben keine Klammer
        antwort = "[-] " + antwort
    klammer = antwort[:antwort.index("]") + 1] if "]" in antwort else ""
    return {"system": system_text(zust), "frage": nutzer.strip() or LEER, "antwort": antwort, "zust": zust,
            "betont": klammer}


def beispiele(anzahl: int, seed: int = 7) -> tuple[list[dict], list[dict]]:
    """(Training, Prüfung) als Chat-Beispiele."""
    train, test = daten.erzeugen(anzahl, seed)
    umschreiben = lambda liste: [b for b in map(runde, liste) if b is not None]
    return umschreiben(train), umschreiben(test)


def lizenz_schreiben(ordner: Path | None = None) -> Path:
    ordner = ordner or lora_ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / "LIZENZ.txt"
    ziel.write_text(LIZENZ, encoding="utf-8")
    return ziel


def trainieren(gguf: Path, schritte: int = 3000, rang: int = 16, anzahl: int = 20_000, lr: float = 2e-4) -> Path:
    from loramotor import training
    train, _ = beispiele(anzahl)
    ziel = lora_ordner() / f"{Path(gguf).stem}.safetensors"
    pfad = training.trainieren(gguf=gguf, beispiele=train, schritte=schritte, rang=rang, lr=lr, ziel=ziel,
                               titel="Helfer-LoRA", on_status=print)
    lizenz_schreiben(ziel.parent)
    return pfad


def pruefen(gguf: Path, anzahl: int = 200, rechner=None) -> dict:
    """Anteil richtiger Aktionen (Art und Wert) auf Prüf-Runden mit fremden Namen."""
    from .gemmahelfer import Rechner
    _, test = beispiele(1000, seed=11)
    test = test[:anzahl]
    if rechner is None:
        rechner = Rechner(Path(gguf), lora_ordner() / f"{Path(gguf).stem}.safetensors")
    richtig = 0
    for b in test:
        soll = aktion_lesen(b["antwort"])[:2]
        ist = aktion_lesen(rechner.antworten(b["zust"], "" if b["frage"] == LEER else b["frage"]))[:2]
        richtig += ist == soll
    return {"runden": len(test), "richtig": richtig, "anteil": richtig / max(1, len(test))}


def main() -> None:
    ap = argparse.ArgumentParser(description="Gemma-Helfer: Lernstoff, Training, Prüfung")
    ap.add_argument("was", choices=("trainieren", "pruefen"))
    ap.add_argument("gguf", type=Path)
    ap.add_argument("--schritte", type=int, default=3000)
    ap.add_argument("--rang", type=int, default=16)
    ap.add_argument("--anzahl", type=int, default=200)
    a = ap.parse_args()
    if a.was == "trainieren":
        print(f"fertig: {trainieren(a.gguf, a.schritte, a.rang)}")
    else:
        e = pruefen(a.gguf, a.anzahl)
        print(f"{e['richtig']}/{e['runden']} Aktionen richtig ({e['anteil']:.1%})")


if __name__ == "__main__":
    main()
