"""Prüfung des fertigen Helfers mit der echten Laufzeit (reines Python).

Misst: Aktionen je Art, Ehrlichkeit bei unsicheren Fragen, {name} nur bei bekanntem Namen, sauberes Satzende,
Antwortzeit. Aufruf: python -m einrichtungshelfer.pruefen <werkstatt> [--anzahl 300]
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path

from einrichtungshelfer import daten
from einrichtungshelfer.laufzeit import Helfer
from einrichtungshelfer.schema import FEHLER, aktion_lesen, zustand

_SATZENDE = set(".!?)✨😊💜👋🎉⏳🔄🔑💻🙈🎭🧠🛡📁🎨🚪🔍⚙🧰🚑🌙☕😄🥹🔧🧳🌐😴📄🐣🙂️")
_EHRLICH = ("nicht sicher", "nicht genau", "weiß ich leider nicht", "weiß ich nicht")


def _teile(zeile: str) -> tuple[str, str, str]:
    return zeile[3:zeile.index("</z>")], zeile[zeile.index("<n>") + 3:zeile.index("</n>")], zeile[zeile.index("<h>") + 3:-4]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("werkstatt", type=Path)
    ap.add_argument("--anzahl", type=int, default=300)
    a = ap.parse_args()
    helfer = Helfer(a.werkstatt / "Modell", seed=1)
    test = [json.loads(z)["text"] for z in (a.werkstatt / "Daten" / "pruefung.jsonl").read_text(encoding="utf-8").splitlines()]
    gesamt, falsch, zeiten = Counter(), Counter(), []
    name_fehler = ende_fehler = 0
    for zeile in test[:a.anzahl]:
        zust, nutzer, soll = _teile(zeile)
        t = time.perf_counter()
        ist = helfer.antworten(zust, nutzer)
        zeiten.append(time.perf_counter() - t)
        if nutzer:
            s, i = aktion_lesen(soll)[:2], aktion_lesen(ist)[:2]
            gesamt[s[0]] += 1
            if s != i:
                falsch[s[0]] += 1
                print(f"  ✗ {zust.split(';')[0]:<16} | {nutzer[:40]:<40} | soll {s} | ist {i}")
        if "name=nein" in zust and "{name}" in ist and not ist.startswith("[anrede"):
            name_fehler += 1
            print(f"  ✗ {{name}} ohne Namen: {ist[:90]}")
        if not ist or ist[-1] not in _SATZENDE:
            ende_fehler += 1
            print(f"  ✗ Satzende: …{ist[-60:]}")
    ehrlich, ehrlich_n = 0, 0
    rng = random.Random(2)
    for frage in daten._UNSICHER:
        for schritt in ("anrede", "modell", "notfall", "plaudern"):
            fehler = {"notfall": rng.choice(FEHLER), "plaudern": "kein_modell"}.get(schritt, "")
            zust = zustand(schritt) if schritt == "anrede" else zustand(schritt, name=rng.choice(["ja", "nein"]),
                                                                          fehler=fehler)
            ist = helfer.antworten(zust, frage)
            ehrlich_n += 1
            if aktion_lesen(ist)[0] == "-" and any(e in ist for e in _EHRLICH):
                ehrlich += 1
            else:
                print(f"  ✗ nicht ehrlich: {frage} → {ist[:90]}")
    print("\nAktionen:", {k: f"{gesamt[k] - falsch[k]}/{gesamt[k]}" for k in gesamt})
    richtig = sum(gesamt.values()) - sum(falsch.values())
    print(f"Gesamt: {richtig}/{sum(gesamt.values())} ({richtig / max(1, sum(gesamt.values())):.1%})")
    print(f"Ehrlich bei unsicheren Fragen: {ehrlich}/{ehrlich_n}")
    print(f"{{name}} ohne bekannten Namen: {name_fehler} · unsauberes Satzende: {ende_fehler}")
    zeiten.sort()
    print(f"Antwortzeit: Mittel {sum(zeiten) / len(zeiten):.1f} s · längste {zeiten[-1]:.1f} s")


if __name__ == "__main__":
    main()
