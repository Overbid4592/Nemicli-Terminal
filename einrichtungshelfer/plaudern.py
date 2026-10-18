"""Funke im Terminal ausprobieren – ohne echtes Installieren, ohne Menüs.

Aufruf: python -m einrichtungshelfer.plaudern <werkstatt>/Modell [--frei | --notfall <art> | --probe | --gross]
  (ohne Schalter)   eine Einrichtung durchspielen
  --frei            frei plaudern (wie ohne eingerichtetes Sprach-Modell)
  --notfall <art>   ein Ausfall des großen Modells, z. B. schluessel, netz, vram (siehe schema.FEHLER)
  --probe           feste Eingaben abspielen und die Antwortzeit messen
  --gross           mit Funke Groß chatten (Ordner von Funke Groß angeben), Gedanken werden gezeigt
Strg+C beendet.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from einrichtungshelfer.gespraech import NAME, Funke
from einrichtungshelfer.schema import FEHLER

_PERSONEN = ["Nemi", "Mira"]
_PROBE = ["ich heiße Lennart aber nenn mich Lenny", "was ist eine persönlichkeit", "mira bitte", "weiß nicht",
          "lokal", "ja mach", "nö"]


class _Mitlesen:
    """Schreibt Funkes Antwort Wort für Wort in die Konsole."""
    def __init__(self):
        self.gezeigt = ""

    def __call__(self, text: str) -> None:
        if text.startswith(self.gezeigt):
            sys.stdout.write(text[len(self.gezeigt):])
            sys.stdout.flush()
            self.gezeigt = text


def _runde(funke: Funke, schritt: str, nutzer: str = "", **felder):
    sys.stdout.write(f"✨ {NAME}  ")
    mit = _Mitlesen()
    t = time.perf_counter()
    a = funke.sagen(schritt, nutzer, beim_text=mit, **felder)
    if a.text != mit.gezeigt:                        # vom Türsteher ersetzt oder Platzhalter am Ende
        sys.stdout.write("\r" + " " * (len(mit.gezeigt) + 12) + f"\r✨ {NAME}  {a.text}")
    aktion = f"[{a.art}{'=' + a.wert if a.wert else ''}] " if nutzer else ""
    print(f"   {aktion}({time.perf_counter() - t:.1f} s)")
    return a


def _eingabe(probe) -> str:
    if probe is not None:
        ein = next(probe)
        print(f"🙂 {ein}")
        return ein
    return input("🙂 ").strip() or "?"


def _einrichten(funke: Funke, probe) -> None:
    felder = {"pruefung": {"offen": ["venv", "pakete"], "fehlt": ["torch"]}, "ende": {"modell": "spaeter", "fehlt": ["torch"]}}
    for schritt in ("anrede", "pers", "modell", "pruefung", "torch", "ende"):
        _runde(funke, schritt, **felder.get(schritt, {}))
        if schritt == "ende":
            return
        while True:
            a = _runde(funke, schritt, _eingabe(probe), **felder.get(schritt, {}))
            if a.art == "abbruch":
                return
            if a.art == "modell":
                felder["ende"]["modell"] = a.wert
            if a.art != "-":
                break


def _gross(ordner: Path) -> None:
    """Chat mit Funke Groß wie in NemiCLI: erst das Denken (💭), dann die Antwort (✨)."""
    import asyncio
    from einrichtungshelfer.funkechat import FunkeChat
    chat = FunkeChat(pfad=ordner)

    async def runde(text: str) -> None:
        art_vorher = ""
        t = time.perf_counter()
        async for ev in chat.stream(text):
            if ev["type"] in ("thinking", "text"):
                if ev["type"] != art_vorher:
                    sys.stdout.write("\n💭 " if ev["type"] == "thinking" else "\n✨ ")
                    art_vorher = ev["type"]
                sys.stdout.write(ev["text"])
                sys.stdout.flush()
            elif ev["type"] == "note":
                print(f"\n   ({ev['text']})", end="")
        print(f"   ({time.perf_counter() - t:.1f} s)")

    print(f"Vorgaben: {chat.vorgaben()}  ·  Strg+C beendet")
    while True:
        asyncio.run(runde(input("\n🙂 ").strip() or "?"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("modell", type=Path)
    gruppe = ap.add_mutually_exclusive_group()
    gruppe.add_argument("--frei", action="store_true")
    gruppe.add_argument("--notfall", choices=FEHLER)
    gruppe.add_argument("--probe", action="store_true")
    gruppe.add_argument("--gross", action="store_true")
    a = ap.parse_args()
    if a.gross:
        try:
            _gross(a.modell)
        except (KeyboardInterrupt, EOFError):
            print()
        return
    funke = Funke(a.modell)
    funke.personen, funke.standard, funke.ersatzname = _PERSONEN, _PERSONEN[0], "du"
    try:
        if a.frei:
            _runde(funke, "plaudern", fehler="kein_modell")
            while True:
                _runde(funke, "plaudern", _eingabe(None), fehler="kein_modell")
        elif a.notfall:
            _runde(funke, "notfall", fehler=a.notfall)
            while _runde(funke, "notfall", _eingabe(None), fehler=a.notfall).art == "-":
                pass
        else:
            _einrichten(funke, iter(_PROBE) if a.probe else None)
    except (KeyboardInterrupt, EOFError, StopIteration):
        print()


if __name__ == "__main__":
    main()
