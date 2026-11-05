"""
malkind.py - Bilder der Wache in einem eigenen, kurzlebigen Prozess malen.

Ein Prozess, der einmal auf der GPU gerechnet hat, behält seinen CUDA-Kontext (0,3–0,5 GB) bis zu
seinem Ende. Die Wache läuft den ganzen Tag; malt sie selbst, fehlt dieser Speicher dauerhaft. Deshalb
malt sie über einen Kindprozess (`--malkind`). Er bleibt für eine Serie offen (Krea hält die Gewichte
solange im RAM) und beendet sich nach RUHE_S ohne Auftrag selbst – mit ihm geht sein Grafikspeicher.
Will ein anderer Prozess malen (malsperre), endet es sofort.

Aufträge laufen über Dateien in einem Ordner je Elternprozess, denn die Fenster-exe hat keine
Standardausgabe: <nr>.auftrag (JSON) – das Kind benennt ihn in <nr>.laeuft um, schreibt je Meldung
eine Zeile nach <nr>.status und zuletzt <nr>.ergebnis (JSON).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TAKT_S = 0.25
RUHE_S = 120.0                     # höchstens so lange ohne Auftrag, dann endet das Kind
RUHE_MIN_S = 30.0
MAX_WARTEN_S = 300.0               # so lange wartet ein Auftrag höchstens auf die Mal-Sperre (LoRA-Training …)
ENV = "NEMICLI_MALKIND"
FUNKTIONEN = ("paint", "gesicht_nachbessern")

_lock = threading.Lock()
_kind: subprocess.Popen | None = None
_nr = 0


def aktiv() -> bool:
    """Malt dieser Prozess über das Kind? Nur in der Wache und nicht im Kind selbst."""
    if os.environ.get(ENV):
        return False
    modes = sys.modules.get("modes")
    if modes is None or not getattr(modes, "_wache_dienst", False):
        return False
    try:
        import config
        return config.load().get("bild_eigener_prozess", True) is not False
    except Exception:
        return True


def ordner(pid: int | None = None) -> Path:
    return Path(tempfile.gettempdir()) / "NemiCLI" / f"malkind_{pid or os.getpid()}"


def befehl(o: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        from paths import exe_ohne_fenster
        return [str(exe_ohne_fenster()), "--malkind", str(o), str(os.getpid())]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    main = Path(__file__).resolve().parents[1] / "main.py"
    return [str(pythonw if pythonw.exists() else python), str(main), "--malkind", str(o), str(os.getpid())]


def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def _starten(o: Path) -> None:
    global _kind
    if _kind is not None and _kind.poll() is None:
        return
    o.mkdir(parents=True, exist_ok=True)
    _kind = subprocess.Popen(befehl(o), env=dict(os.environ, **{ENV: "1"}), cwd=str(o),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _melden(f"Mal-Prozess gestartet (PID {_kind.pid})")


def beenden() -> None:
    """Kind sofort beenden (Abbruch, Programmende)."""
    global _kind
    k, _kind = _kind, None
    if k is not None and k.poll() is None:
        k.kill()
        try:
            k.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _weg(basis: Path) -> None:
    for endung in (".auftrag", ".laeuft", ".status", ".ergebnis", ".tmp"):
        try:
            basis.with_suffix(endung).unlink(missing_ok=True)
        except OSError:
            pass


def _status_lesen(basis: Path, schon: int, on_status) -> int:
    p = basis.with_suffix(".status")
    try:
        zeilen = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return schon
    for z in zeilen[schon:]:
        if on_status is not None:
            on_status(z)
    return len(zeilen)


def auftrag(fn: str, kw: dict, on_status=None):
    """`imagegen.<fn>(**kw)` im Kind ausführen und sein Ergebnis zurückgeben. Fehler im Kind kommen als
    RuntimeError. Wirft on_status (Abbruch), wird das Kind beendet."""
    global _nr
    if fn not in FUNKTIONEN:
        raise ValueError(f"unbekannte Mal-Funktion {fn!r}")
    with _lock:
        o = ordner()
        _starten(o)
        _nr += 1
        basis = o / str(_nr)
        tmp = basis.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fn": fn, "kw": kw}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(basis.with_suffix(".auftrag"))
        schon, neu_gestartet = 0, False
        try:
            while True:
                schon = _status_lesen(basis, schon, on_status)
                erg = basis.with_suffix(".ergebnis")
                if erg.exists():
                    d = json.loads(erg.read_text(encoding="utf-8"))
                    _status_lesen(basis, schon, on_status)
                    if "fehler" in d:
                        raise RuntimeError(d["fehler"])
                    return d.get("pfad")
                if _kind is None or _kind.poll() is not None:
                    # Endete das Kind gerade aus Ruhe, liegt der Auftrag noch da: einmal neu starten.
                    if basis.with_suffix(".auftrag").exists() and not neu_gestartet:
                        neu_gestartet = True
                        _starten(o)
                    else:
                        code = _kind.returncode if _kind is not None else "?"
                        raise RuntimeError(f"Der Mal-Prozess ist unerwartet beendet (Code {code}).")
                time.sleep(TAKT_S)
        except BaseException:
            if not basis.with_suffix(".ergebnis").exists():
                beenden()
            raise
        finally:
            _weg(basis)


# --- Kind ------------------------------------------------------------------------------------------

def _ruhe_s() -> float:
    try:
        import krea
        return max(RUHE_MIN_S, min(RUHE_S, krea._entladen_nach()))
    except Exception:
        return RUHE_S


def _lebt(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return True


def _ausfuehren(job: Path) -> bool:
    """Einen Auftrag rechnen; False nach einem Fehler (das Kind endet dann, der nächste bekommt ein frisches)."""
    laeuft = job.with_suffix(".laeuft")
    try:
        job.replace(laeuft)
        d = json.loads(laeuft.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    status = job.with_suffix(".status")

    def melde(text) -> None:
        with open(status, "a", encoding="utf-8") as f:
            f.write(" ".join(str(text).split()) + "\n")

    import imagegen
    kw = dict(d.get("kw") or {})
    if kw.get("size"):
        kw["size"] = tuple(kw["size"])
    ok = True
    try:
        if d.get("fn") not in FUNKTIONEN:
            raise ValueError(f"unbekannte Mal-Funktion {d.get('fn')!r}")
        erg = {"pfad": getattr(imagegen, d["fn"])(**kw, on_status=melde)}
    except Exception as e:
        erg, ok = {"fehler": f"{type(e).__name__}: {e}"}, False
    tmp = job.with_suffix(".tmp")
    tmp.write_text(json.dumps(erg, ensure_ascii=False), encoding="utf-8")
    tmp.replace(job.with_suffix(".ergebnis"))
    return ok


def hauptprogramm(argv: list[str]) -> int:
    """`--malkind <ordner> <eltern-pid>`: Aufträge rechnen, bis RUHE_S lang keiner kommt oder die Wache endet."""
    if len(argv) < 2 or not argv[1].isdigit():
        return 2
    o, eltern = Path(argv[0]), int(argv[1])
    os.environ[ENV] = "1"
    import malsperre
    ruhe, zuletzt = _ruhe_s(), time.monotonic()
    while True:
        jobs = sorted(o.glob("*.auftrag"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
        if jobs:
            if not _ausfuehren(jobs[0]):
                return 1
            zuletzt = time.monotonic()
            continue
        if time.monotonic() - zuletzt > ruhe or not _lebt(eltern):
            return 0
        if malsperre.fremd_will_malen():         # ruhende Gewichte sofort freigeben statt doppelt im Speicher
            _melden("Mal-Prozess endet: ein anderer Prozess will malen")
            return 0
        time.sleep(TAKT_S)
