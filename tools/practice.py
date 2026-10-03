"""
practice.py - Übungsprogramme nach Freigabe schreiben, ausführen und auswerten.

Jede Codeversion wird vor dem Schreiben und Ausführen vollständig zur Freigabe
vorgelegt. Ausgeführt wird im AppContainer (sandbox.py): der Code sieht nur
NemiSandbox und das Internet, nicht die Dateien des Nutzers.
"""

from __future__ import annotations

import asyncio
import random
import threading
from datetime import datetime
from pathlib import Path

import learn
import sandbox

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

SANDBOX = _ROOT / "NemiSandbox"
MAX_FIX = 3          # so oft darf sie sich pro Übung selbst korrigieren
RUN_TIMEOUT = 20     # Sekunden, dann gilt das Programm als hängend

THEMEN = [
    "Strings: Palindrom prüfen, Vokale zählen, Wörter umdrehen",
    "Listen & Dicts: Häufigkeiten zählen, gruppieren, Duplikate entfernen",
    "kleine Mathe-Aufgabe: Primzahlen, ggT, Fakultät, Fibonacci",
    "eine kleine Klasse mit Methoden (z.B. ein Konto oder Stapel)",
    "Rekursion: Summe, Verschachtelung durchlaufen, Türme von Hanoi",
    "einen Sortier-Algorithmus selbst schreiben (Bubble/Insertion/Quick)",
    "Datum/Zeit: Tage zwischen zwei Daten, Wochentag bestimmen",
    "Text parsen: Wörter zählen, eine CSV-Zeile zerlegen",
    "eine Datei im aktuellen Ordner schreiben und wieder einlesen",
    "ein winziger Taschenrechner / eine kleine Zustandsmaschine",
]

_SYSTEM = (
    "Du übst selbstständig Programmieren, um besser zu werden. Schreibe EIN "
    "kleines, eigenständiges Python-Programm (nur Standardbibliothek) zum "
    "gegebenen Thema. Es MUSS am Ende eigene Tests mit assert enthalten und bei "
    "Erfolg als ALLERLETZTE Zeile genau 'ALLE TESTS OK' ausgeben (mit print). "
    "Wenn du Dateien anlegst, dann NUR im aktuellen Ordner mit relativen Pfaden. "
    "Antworte AUSSCHLIESSLICH mit einem einzigen ```python …``` Codeblock, sonst nichts."
)


def ensure_sandbox() -> Path:
    """Legt nach Freigabe den Arbeitsordner an, falls er fehlt."""
    SANDBOX.mkdir(parents=True, exist_ok=True)
    return SANDBOX


def _extract(text: str) -> str:
    """Holt den Python-Code aus der Antwort (```python-Block, sonst roher Text)."""
    blocks = learn.extract_python(text or "")
    return blocks[0] if blocks else (text or "").strip()


async def _run(path: Path, should_stop) -> tuple[str, str]:
    """Freigegebenes Programm im AppContainer ausführen; Status und Ausgabe zurückgeben."""
    if should_stop():
        return "abgebrochen", "Übung vor dem Start abgebrochen."
    stopp = threading.Event()
    lauf = asyncio.create_task(asyncio.to_thread(sandbox.python_ausfuehren, path, RUN_TIMEOUT, stopp))
    try:
        while not lauf.done():
            if should_stop():
                stopp.set()
            await asyncio.wait({lauf}, timeout=0.1)
        e = lauf.result()
    except asyncio.CancelledError:
        stopp.set()
        await asyncio.gather(lauf, return_exceptions=True)
        raise
    except sandbox.SandboxFehler as exc:
        return "fehler", f"(Sandbox: {exc})"
    except Exception as exc:
        return "fehler", f"(Ausführung fehlgeschlagen: {exc})"
    if e.abgebrochen or should_stop():
        return "abgebrochen", "Übungsprozess beendet; Übung abgebrochen."
    if e.zeit_ueberschritten:
        return "timeout", "(Zeitüberschreitung – Übungsprozess beendet.)"
    lines = [line for line in e.ausgabe.splitlines() if line.strip()]
    ok = e.code == 0 and bool(lines) and lines[-1] == "ALLE TESTS OK"
    out = (e.ausgabe + ("\n" if e.ausgabe and e.fehler else "") + e.fehler).strip()
    return ("erfolg" if ok else "fehler"), out or "(keine Ausgabe)"


async def one_round(backend, emit, should_stop, n: int = 1, approve=None) -> str:
    """Eine Runde; async approve(path, code) muss jede Codeversion freigeben."""
    try:
        return await _one_round(backend, emit, should_stop, n, approve)
    except asyncio.CancelledError:
        emit("cancelled", "Übung abgebrochen.")
        raise


async def _one_round(backend, emit, should_stop, n, approve) -> str:
    if approve is None:
        emit("cancelled", "Übung nicht gestartet: Es fehlt die Ausführungsfreigabe.")
        return "abgelehnt"
    try:
        sandbox.interpreter()
    except sandbox.SandboxFehler as exc:
        emit("error", f"Übungsmodus: {exc}")
        return "fehler"
    thema = random.choice(THEMEN)
    emit("start", f"Übung #{n}: {thema}")
    if should_stop():
        return "abgebrochen"

    try:
        answer = await backend.ask_once(f"Thema: {thema}", _SYSTEM)
    except Exception as e:
        emit("error", f"Modell-Fehler: {e}")
        return "fehler"
    code = _extract(answer)
    if not code or should_stop():
        return "abgebrochen"

    path = SANDBOX / datetime.now().strftime("uebung_%Y%m%d_%H%M%S_%f.py")

    for versuch in range(1, MAX_FIX + 1):
        if should_stop():
            emit("cancelled", "Übung abgebrochen.")
            return "abgebrochen"
        try:
            approved = await approve(path, code)
        except Exception as e:
            emit("error", f"Codefreigabe fehlgeschlagen: {e}")
            return "fehler"
        if should_stop():
            emit("cancelled", "Übung abgebrochen.")
            return "abgebrochen"
        if approved is not True:
            emit("cancelled", "Codeversion abgelehnt – nicht geschrieben oder ausgeführt.")
            return "abgelehnt"
        ensure_sandbox()
        path.write_text(code, encoding="utf-8")
        emit("code", (path, code))
        emit("run", f"führe aus … (Versuch {versuch}/{MAX_FIX})")
        status, out = await _run(path, should_stop)
        if status == "abgebrochen" or should_stop():
            emit("cancelled", "Übung abgebrochen.")
            return "abgebrochen"
        if status == "erfolg":
            emit("ok", out)
            learn.save_snippet(f"Übung: {thema}", code)
            emit("learned", f'💡 Gelernt & gemerkt: Übung „{thema}"')
            return "erfolg"
        emit("fail", out)
        if versuch >= MAX_FIX or should_stop():
            break
        try:
            answer = await backend.ask_once(
                f"Dein Programm hatte einen Fehler. Ausgabe:\n{out[:1500]}\n\n"
                "Gib das KORRIGIERTE komplette Programm zurück – nur als "
                "```python …``` Block.", _SYSTEM)
        except Exception as e:
            emit("error", f"Modell-Fehler beim Korrigieren: {e}")
            return "fehler"
        code = _extract(answer)
        if not code:
            break

    emit("giveup", f"Diese Übung nach {MAX_FIX} Versuchen aufgegeben – "
                   "nächstes Mal klappt's. 🌱")
    return "aufgegeben"
