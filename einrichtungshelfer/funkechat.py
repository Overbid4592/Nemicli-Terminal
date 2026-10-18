"""Funke Groß als Chat-Modell in /model („funke:funke-gross“) – eigenes Modell, ohne Werkzeuge und Bilder.

Rechnet mit torch auf der CPU, wenn torch da ist (die Grafikkarte bleibt frei), sonst in reinem Python.
Liefert im stream() erst das Denken („thinking“), dann die Antwort – wie die anderen Backends.
Vorgaben: Name der aktiven Persönlichkeit; anrede/laenge/emoji aus nemicli.config.json → "funke_vorgaben".
"""
from __future__ import annotations

import asyncio
import random
import threading
from pathlib import Path

from einrichtungshelfer.laufzeit import Modell, _waehlen, bereinigen, stuecke_zusammensetzen
from einrichtungshelfer.wortschatz import Wortschatz

MODELL = "funke-gross"
REF = f"funke:{MODELL}"
KONTEXT = 512
MAX_NEU = 220
_DATEIEN = ("helfer.bin", "wortschatz.json")
_STANDARD = {"anrede": "du", "laenge": "normal", "emoji": "ja"}
_ERLAUBT = {"anrede": ("du", "sie"), "laenge": ("kurz", "normal", "lang"), "emoji": ("ja", "nein")}


def ordner() -> Path:
    try:
        from paths import ASSETS
    except Exception:
        ASSETS = Path(__file__).resolve().parent.parent
    return Path(ASSETS) / "einrichtungshelfer" / "modell_gross"


def verfuegbar(pfad: Path | None = None) -> bool:
    pfad = pfad or ordner()
    stuecke_zusammensetzen(pfad / "helfer.bin")
    return all((pfad / d).is_file() for d in _DATEIEN)


def kopf(pfad: Path | None = None) -> dict:
    """Liest nur den Kopf der Modelldatei und prüft die Größe (für den Selbsttest, ohne zu laden).
    Rückgabe {"parameter": n, "typ": "f2"|"f4"}; ValueError bei kaputter oder abgeschnittener Datei."""
    import json
    import math
    import struct

    from einrichtungshelfer.laufzeit import KENNUNG
    datei = (pfad or ordner()) / "helfer.bin"
    stuecke_zusammensetzen(datei)
    with open(datei, "rb") as f:
        if f.read(len(KENNUNG)) != KENNUNG:
            raise ValueError("unbekanntes Format")
        n = struct.unpack("<I", f.read(4))[0]
        k = json.loads(f.read(n).decode("utf-8"))
    typ = k.get("typ", "f4")
    parameter = sum(math.prod(t["form"]) for t in k["tensoren"].values())
    if datei.stat().st_size != len(KENNUNG) + 4 + n + parameter * (2 if typ == "f2" else 4):
        raise ValueError("Datei unvollständig")
    return {"parameter": parameter, "typ": typ}


def beschreibung() -> str:
    return "✨ Funke Groß – NemiCLIs eigenes kleines Modell (27 Mio., von null trainiert, ohne Grafikkarte)"


def _text(inhalt) -> str:
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        return " ".join(b.get("text", "") for b in inhalt if isinstance(b, dict) and b.get("type") == "text")
    return ""


class Motor:
    """Lädt das Modell (torch, sonst reines Python) und erzeugt Denken + Antwort."""

    def __init__(self, pfad: Path, seed: int | None = None):
        self.ws = Wortschatz.laden(pfad / "wortschatz.json")
        self.rng = random.Random(seed)
        stuecke_zusammensetzen(pfad / "helfer.bin")
        try:
            from einrichtungshelfer.torchmodell import Generator
            self.torch = Generator(pfad / "helfer.bin", threads=None)
            self.python = None
            self.art = "torch"
        except Exception:                                   # kein torch: langsamer, aber es geht
            self.torch = None
            self.python = Modell(pfad / "helfer.bin")
            self.art = "python"
        self.id = self.ws.id

    def _vorne(self, ids: list[int]):
        if self.torch is not None:
            return self.torch.vorne(ids)
        cache = self.python.neu()
        for pos, tok in enumerate(ids[:-1]):
            self.python.schritt(tok, pos, cache, mit_logits=False)
        return cache, self.python.schritt(ids[-1], len(ids) - 1, cache)

    def _schritt(self, tok: int, pos: int, cache) -> list[float]:
        if self.torch is not None:
            return self.torch.schritt(tok, pos, cache)
        return self.python.schritt(tok, pos, cache)

    def erzeugen(self, ids: list[int], melden, abbruch: threading.Event | None = None,
                 temperatur: float = 0.6) -> tuple[str, str, int]:
        """melden(art, stück) mit art "thinking"/"text". Rückgabe (Gedanke, Antwort, erzeugte Tokens).
        Gedacht wird ohne Würfeln, geantwortet mit wenig Zufall."""
        d_auf, d_zu, h_auf, h_zu = self.id["<d>"], self.id["</d>"], self.id["<h>"], self.id["</h>"]
        cache, logits = self._vorne(ids)
        teil, gedanke, antwort, n = "", [], [], 0
        for n in range(1, MAX_NEU + 1):
            if abbruch is not None and abbruch.is_set():
                break
            tok = _waehlen(logits, temperatur if teil == "h" else 0.0, 8, self.rng)
            if tok == h_zu:
                break
            if tok == d_auf:
                teil = "d"
            elif tok == d_zu:
                teil = ""
            elif tok == h_auf:
                teil = "h"
            elif teil:
                stueck = self.ws.tokens[tok]
                (gedanke if teil == "d" else antwort).append(stueck)
                melden("thinking" if teil == "d" else "text", stueck)
            pos = len(ids) + n - 1
            if pos >= KONTEXT:
                break
            logits = self._schritt(tok, pos, cache)
        return "".join(gedanke).strip(), "".join(antwort).strip(), n


class FunkeChat:
    provider_id = "funke"

    def __init__(self, ref: str = REF, pfad: Path | None = None):
        self.model = ref
        self.model_id = MODELL
        self.name = MODELL
        self.strength = None
        self.messages: list[dict] = []
        self._pfad = pfad or ordner()
        self._motor: Motor | None = None
        self._gedanken: dict[str, str] = {}         # Antworttext → Gedanke (fürs Gesprächs-Gedächtnis)

    def reset(self) -> None:
        self.messages = []
        self._gedanken.clear()

    # --- Vorgaben und Eingabe ---
    def vorgaben(self) -> str:
        werte = dict(_STANDARD)
        try:
            import config
            eigene = config.load().get("funke_vorgaben") or {}
            werte |= {k: str(v) for k, v in eigene.items() if k in _ERLAUBT and str(v) in _ERLAUBT[k]}
        except Exception:
            pass
        try:
            import persoenlichkeiten as PS
            name = PS.active().name or "Funke"
        except Exception:
            name = "Funke"
        name = bereinigen(name).replace(";", " ").replace("=", " ")[:30]
        return f"name={name};anrede={werte['anrede']};laenge={werte['laenge']};emoji={werte['emoji']}"

    def eingabe(self, nachrichten: list[dict]) -> list[int]:
        """Vorgaben + so viele frühere Runden wie passen + die letzte Nachricht des Nutzers."""
        ws = self._wortschatz()
        kopf = ws.kodieren(f"<v>{self.vorgaben()}</v>")
        letzte = bereinigen(_text(nachrichten[-1]["content"]))[:400] if nachrichten else ""
        ende = ws.kodieren(f"<n>{letzte}</n>")
        platz = KONTEXT - MAX_NEU - len(kopf) - len(ende)
        runden: list[list[int]] = []
        frueher = nachrichten[:-1]
        i = len(frueher) - 1
        while i >= 1:                                   # Paare (Nutzer, Funke), jüngste zuerst
            frage, antwort = frueher[i - 1], frueher[i]
            i -= 1
            if frage.get("role") != "user" or antwort.get("role") != "assistant":
                continue
            text = _text(antwort["content"]).strip()
            gedanke = self._gedanken.get(text)
            if gedanke is None:                         # Antwort eines anderen Modells: Funke kennt den Gedanken nicht
                continue
            stueck = ws.kodieren(f"<n>{bereinigen(_text(frage['content']))[:400]}</n><d>{gedanke}</d><h>{text}</h>")
            if len(stueck) > platz:
                break
            runden.insert(0, stueck)
            platz -= len(stueck)
            i -= 1
        return kopf + [t for r in runden for t in r] + ende[-(KONTEXT - MAX_NEU - len(kopf)):]

    def _wortschatz(self) -> Wortschatz:
        if self._motor is not None:
            return self._motor.ws
        return Wortschatz.laden(self._pfad / "wortschatz.json")

    async def _bereit(self) -> Motor:
        if self._motor is None:
            self._motor = await asyncio.to_thread(Motor, self._pfad)
        return self._motor

    # --- Schnittstelle wie die anderen Backends ---
    async def stream(self, user_text: str, images=None):
        try:
            import pricing  # in NemiCLI: Verlauf kürzen wie bei den anderen Backends
        except ImportError:                             # Plauder-Programm außerhalb von NemiCLI
            pricing = None
        frage = {"role": "user", "content": user_text}
        self.messages.append(frage)
        abbruch = threading.Event()
        try:
            if pricing is not None:
                pricing.kuerzen(self)
                if hinweis := pricing.aufraeum_hinweis(self):
                    yield {"type": "note", "text": hinweis}
            if self._motor is None:
                yield {"type": "note", "text": "✨ Funke wacht auf …"}
            motor = await self._bereit()
            if motor.art == "python":
                yield {"type": "note", "text": "✨ Funke denkt ohne torch (reines Python) – das dauert etwas länger."}
            if images:
                yield {"type": "note", "text": "⚠ Funke sieht keine Bilder – ich gehe nur auf den Text ein."}
            ids = self.eingabe(self.messages)
            schlange: asyncio.Queue = asyncio.Queue()
            schleife = asyncio.get_running_loop()

            def melden(art: str, stueck: str) -> None:
                schleife.call_soon_threadsafe(schlange.put_nowait, (art, stueck))

            def arbeit():
                try:
                    return motor.erzeugen(ids, melden, abbruch)
                finally:
                    schleife.call_soon_threadsafe(schlange.put_nowait, None)

            aufgabe = asyncio.ensure_future(asyncio.to_thread(arbeit))
            while (ev := await schlange.get()) is not None:
                yield {"type": ev[0], "text": ev[1]}
            gedanke, antwort, n = await aufgabe
        except BaseException:
            abbruch.set()
            if self.messages and self.messages[-1] is frage:
                self.messages.pop()
            raise
        antwort = antwort or "(keine Antwort)"
        self._gedanken[antwort] = gedanke
        self.messages.append({"role": "assistant", "content": antwort})
        yield {"type": "usage", "input": len(ids), "output": n}

    async def ask_once(self, prompt: str, system: str = "") -> str:
        motor = await self._bereit()
        ids = self.eingabe([{"role": "user", "content": prompt}])
        return (await asyncio.to_thread(motor.erzeugen, ids, lambda a, s: None))[1]

    async def ask_messages(self, system: str, messages: list[dict]) -> str:
        letzte = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        return await self.ask_once(_text(letzte["content"]) if letzte else "", system)

