"""Prüfung von Funke Groß auf dem Prüf-Topf (Namen, die im Training nie vorkamen).

Je Gespräch wird die letzte Runde erzeugt; die früheren stehen fest wie im Lernstoff. Gemessen wird:
richtiger Befehl, Name aus früheren Runden, Ehrlichkeit bei Allgemeinwissen, Vorgaben (Sie-Form, ohne Emojis,
kurz), sauberes Satzende, Antwortzeit. Aufruf: python -m einrichtungshelfer.pruefen_gross <werkstatt> [--anzahl 400]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

from einrichtungshelfer.daten_gross import _DU_REST, _EMOJI
from einrichtungshelfer.funkechat import Motor

_EHRLICH = ("nicht sicher", "weiß ich nicht", "weiß ich leider nicht", "raten", "nicht verraten", "noch nicht")
_SATZENDE = set(".!?)✨😊💜👋🎉⏳🔄🔑💻🙈🎭🧠🛡📁🎨🚪🔍⚙🧰🚑🌙☕😄🥹🔧🧳🌐😴📄🐣🙂️")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("werkstatt", type=Path)
    ap.add_argument("--anzahl", type=int, default=400)
    a = ap.parse_args()
    motor = Motor(a.werkstatt / "Modell", seed=1)
    test = [json.loads(z)["text"] for z in (a.werkstatt / "Daten" / "pruefung.jsonl").read_text(encoding="utf-8").splitlines()]
    gut, alle, zeiten = Counter(), Counter(), []

    def pruefe(art: str, ok: bool, hinweis: str) -> None:
        alle[art] += 1
        gut[art] += ok
        if not ok:
            print(f"  ✗ {art:<12} {hinweis[:150]}")

    for g in test[:a.anzahl]:
        vorne = g[:g.rindex("</n>") + 4]
        soll_d = re.findall(r"<d>(.*?)</d>", g)[-1]
        soll_h = re.findall(r"<h>(.*?)</h>", g)[-1]
        t = time.perf_counter()
        gedanke, antwort, _ = motor.erzeugen(motor.ws.kodieren(vorne), lambda *_: None)
        zeiten.append(time.perf_counter() - t)
        frage = re.findall(r"<n>(.*?)</n>", g)[-1]
        if (m := re.match(r"Frage: .*?Dafür ist (/\S+) da\.", soll_d)):
            pruefe("befehl", m.group(1) in antwort, f"{frage} → {antwort}  (soll {m.group(1)})")
        elif (m := re.match(r"Vorhin genannt: (.+?)\.", soll_d)):
            pruefe("name", m.group(1) in antwort, f"{frage} → {antwort}  (soll {m.group(1)})")
        elif soll_d.startswith(("Das weiß ich nicht sicher", "Name noch nicht genannt")):
            pruefe("ehrlich", any(e in antwort for e in _EHRLICH), f"{frage} → {antwort}")
        if "anrede=sie" in g:
            pruefe("sie-form", not _DU_REST.search(antwort), antwort)
        if "emoji=nein" in g:
            pruefe("ohne-emoji", not _EMOJI.search(antwort), antwort)
        if "laenge=kurz" in g and soll_h.count(". ") == 0:
            pruefe("kurz", antwort.count(". ") <= 1, antwort)
        pruefe("satzende", bool(antwort) and antwort[-1] in _SATZENDE, antwort[-80:])
    print()
    for art in alle:
        print(f"{art:<12} {gut[art]}/{alle[art]}  ({gut[art] / alle[art]:.1%})")
    zeiten.sort()
    print(f"Antwortzeit ({motor.art}): Mittel {sum(zeiten) / len(zeiten):.2f} s · längste {zeiten[-1]:.2f} s")


if __name__ == "__main__":
    main()
