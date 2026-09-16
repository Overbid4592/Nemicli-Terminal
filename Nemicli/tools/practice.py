"""
practice.py - Übungsprogramme nach Freigabe schreiben, ausführen und auswerten.

Jede Codeversion wird vor dem Schreiben und Ausführen vollständig zur Freigabe
vorgelegt. NemiSandbox ist nur das Arbeitsverzeichnis, keine technische
Isolation: freigegebener Python-Code läuft mit den Rechten von NemiCLI.
Ein Abbruch wartet auf das Ende des gestarteten Prozesses.
"""

from __future__ import annotations

import asyncio
import os
import random
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import learn

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

SANDBOX = _ROOT / "NemiSandbox"
MAX_FIX = 3          # so oft darf sie sich pro Übung selbst korrigieren
RUN_TIMEOUT = 20     # Sekunden, dann gilt das Programm als hängend
_IS_WINDOWS = sys.platform == "win32"

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


async def _finish_cleanup(task, *, propagate_cancel=False):
    """Auch erneutes Escape darf eine bereits laufende Bereinigung nicht lösen."""
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            interrupted = True
            if task.done():
                result = task.result()
                break
    if interrupted and propagate_cancel:
        raise asyncio.CancelledError
    return result


async def _stop_process(proc, output_task) -> None:
    """Eigenen Prozess samt Prozessbaum bestmöglich beenden und abwarten."""
    if proc.returncode is None:
        if _IS_WINDOWS:
            # /T erfasst den Prozessbaum, /F beendet auch hängende Programme.
            # Fester Systempfad: kein gleichnamiges Programm im Arbeitsordner.
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                killer = await asyncio.create_subprocess_exec(
                    str(taskkill), "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    await asyncio.wait_for(killer.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    killer.kill()
                    await killer.wait()
            except OSError:
                pass  # Der eigene Prozess wird unten in jedem Fall beendet.
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    await proc.wait()
    if output_task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(output_task), timeout=2.0)
        except asyncio.TimeoutError:
            # Ein abgelöstes Kind kann Ausgabepipes offenhalten. Der direkte
            # Übungsprozess ist bereits beendet; nicht endlos auf Pipes warten.
            output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                transport.close()


async def _run(path: Path, should_stop) -> tuple[str, str]:
    """Freigegebenes Programm ausführen; Status und echte Ausgabe zurückgeben."""
    if should_stop():
        return "abgebrochen", "Übung vor dem Start abgebrochen."
    if getattr(sys, "frozen", False):
        return "fehler", ("Übungsmodus benötigt einen Python-Interpreter. "
                          "Die gepackte NemiCLI.exe kann Python-Dateien nicht ausführen; "
                          "starte dafür die Python-Version von NemiCLI.")
    proc = None
    spawn_task = None
    output_task = None
    cleanup_task = None

    async def stop(*, after_cancel=False):
        nonlocal cleanup_task
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(_stop_process(proc, output_task))
        await _finish_cleanup(cleanup_task, propagate_cancel=not after_cancel)

    try:
        options = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                   if _IS_WINDOWS else {"start_new_session": True})
        # shield verhindert, dass ein Abbruch während des Starts den neu
        # gestarteten Prozess ohne erreichbares Handle zurücklässt.
        spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, str(path), cwd=str(SANDBOX),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options))
        proc = await asyncio.shield(spawn_task)
        output_task = asyncio.create_task(proc.communicate())
        deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT
        while True:
            if should_stop():
                await stop()
                return "abgebrochen", "Übungsprozess beendet; Übung abgebrochen."
            if output_task.done():
                stdout, stderr = output_task.result()
                await proc.wait()
                if should_stop():
                    return "abgebrochen", "Übung abgebrochen."
                stdout = (stdout or b"").decode("utf-8", errors="replace")
                stderr = (stderr or b"").decode("utf-8", errors="replace")
                lines = [line for line in stdout.splitlines() if line.strip()]
                ok = proc.returncode == 0 and bool(lines) and lines[-1] == "ALLE TESTS OK"
                out = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
                return ("erfolg" if ok else "fehler"), out or "(keine Ausgabe)"
            if asyncio.get_running_loop().time() >= deadline:
                await stop()
                return "timeout", "(Zeitüberschreitung – Übungsprozess beendet.)"
            await asyncio.wait({output_task}, timeout=0.1)
    except asyncio.CancelledError:
        if proc is None and spawn_task is not None:
            try:
                proc = await _finish_cleanup(spawn_task)
            except Exception:
                pass
        if proc is not None:
            await stop(after_cancel=True)
        raise
    except Exception as e:
        if proc is not None:
            await stop()
        return "fehler", f"(Ausführung fehlgeschlagen: {e})"


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
    if getattr(sys, "frozen", False):
        emit("error", "Übungsmodus benötigt die Python-Version von NemiCLI; "
             "die gepackte NemiCLI.exe ist kein Python-Interpreter.")
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
