"""
malsperre.py - Eine Mal-Sperre für alle NemiCLI-Prozesse auf diesem Rechner.

Bilder und Videos brauchen die Grafikkarte und viel gepinnten RAM. Die Sperre in einem Prozess
(threading) sieht andere Prozesse nicht: Hauptprogramm und der Mal-Prozess der Wache luden Krea sonst
gleichzeitig – doppelter Grafikspeicher, voller Arbeitsspeicher, alles wird langsam.

Die Sperre ist ein Byte-Lock auf einer Datei im Temp-Ordner; Windows gibt ihn frei, wenn der Prozess
endet. Wer wartet oder malt, legt eine Marke `<art>_<pid>` an (Inhalt: Startzeit des Prozesses, damit eine
neu vergebene PID nicht als derselbe gilt). Daran sehen andere Prozesse, dass sie ihre ruhenden Gewichte
freigeben sollen (`fremd_will_malen`).
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

TAKT_S = 0.25
_lokal = threading.RLock()                # Threads dieses Prozesses untereinander
_tiefe = 0
_datei = None


def ordner() -> Path:
    return Path(tempfile.gettempdir()) / "NemiCLI" / "malsperre"


def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def _sperren(f) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _entsperren(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def startzeit(pid: int | None = None) -> float | None:
    """Startzeit des Prozesses (psutil create_time) – zusammen mit der PID eindeutig, auch wenn Windows
    die PID später neu vergibt. None, wenn sie nicht zu lesen ist."""
    try:
        import psutil
        return float(psutil.Process(os.getpid() if pid is None else pid).create_time())
    except Exception:
        return None


def lebt(pid: int, start: float | None = None) -> bool:
    """Läuft der Prozess noch? Mit `start`: nur, wenn es noch derselbe ist (nicht eine neu vergebene PID)."""
    try:
        import psutil
    except Exception:
        return True
    try:
        if not psutil.pid_exists(pid):
            return False
        return start is None or abs(psutil.Process(pid).create_time() - start) < 1.0
    except psutil.NoSuchProcess:
        return False
    except Exception:                         # kein Zugriff o. Ä.: lieber als lebend zählen
        return True


def _marke(art: str, an: bool) -> None:
    p = ordner() / f"{art}_{os.getpid()}"
    try:
        if an:
            p.write_text(json.dumps({"zeit": time.time(), "start": startzeit()}), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _marken_start(p: Path) -> float | None:
    """Startzeit aus einer Marke; ältere Marken (nur die Uhrzeit) haben keine."""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return float(d["start"]) if isinstance(d, dict) and d.get("start") is not None else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def marken_weg(pid: int) -> None:
    """Marken eines Prozesses entfernen, der hart beendet wurde (sein finally lief nicht mehr)."""
    for art in ("malt", "wartet"):
        with contextlib.suppress(OSError):
            (ordner() / f"{art}_{pid}").unlink(missing_ok=True)


def fremde() -> list[tuple[str, int]]:
    """Andere Prozesse, die gerade malen oder darauf warten: [(art, pid)]. Marken toter Prozesse fallen weg."""
    raus = []
    try:
        marken = list(ordner().glob("*_*"))
    except OSError:
        return raus
    for p in marken:
        art, _, pid = p.name.rpartition("_")
        if art not in ("malt", "wartet") or not pid.isdigit() or int(pid) == os.getpid():
            continue
        if lebt(int(pid), _marken_start(p)):
            raus.append((art, int(pid)))
        else:
            with contextlib.suppress(OSError):
                p.unlink()
    return raus


def fremd_will_malen() -> bool:
    return bool(fremde())


class Belegt(RuntimeError):
    """Die Grafikkarte blieb länger als erlaubt bei einem anderen Prozess."""


BELEGT_TEXT = ("Die Grafikkarte ist belegt – ein anderer NemiCLI-Prozess malt oder trainiert gerade. "
               "Später noch einmal versuchen.")


def _warten_melden(on_status, text: str, start: float) -> None:
    """Je Wartetakt: ESC prüfen und den Stand melden (on_status prüft bei vielen Aufrufern selbst auf Abbruch)."""
    try:
        import notstopp
    except Exception:                          # Selbsttest ohne core/ im Pfad
        notstopp = None
    if notstopp is not None:
        notstopp.pruefen()
    if on_status is not None:
        on_status(f"{text} ({time.monotonic() - start:.0f} s)")


def _lokal_erwerben(on_status, was: str, max_warten: float | None, start: float) -> None:
    """Die Sperre der Fäden dieses Prozesses holen – nicht stumm: je Takt melden, ESC und Grenze prüfen."""
    gemeldet = False
    while not _lokal.acquire(timeout=TAKT_S):
        if not gemeldet:
            gemeldet = True
            _melden(f"Mal-Sperre: {was} wartet auf ein anderes Bild in diesem Prozess")
        _warten_melden(on_status, "🎨 wartet – gerade entsteht ein anderes Bild …", start)
        if max_warten is not None and time.monotonic() - start > max_warten:
            _melden(f"Mal-Sperre: {was} gibt nach {max_warten:g} s auf")
            raise Belegt(BELEGT_TEXT)


@contextlib.contextmanager
def halten(on_status=None, was: str = "Bild", max_warten: float | None = None):
    """Malen nur, wenn kein anderer NemiCLI-Prozess malt – sonst warten (höchstens `max_warten` s, dann
    `Belegt`). In einem Prozess verschachtelbar."""
    global _tiefe, _datei
    start = time.monotonic()
    _lokal_erwerben(on_status, was, max_warten, start)
    try:
        if _tiefe:
            _tiefe += 1
            try:
                yield
            finally:
                _tiefe -= 1
            return
        ordner().mkdir(parents=True, exist_ok=True)
        f = open(ordner() / "sperre", "a+b")
        gemeldet = False
        try:
            while not _sperren(f):
                if not gemeldet:
                    gemeldet = True
                    _marke("wartet", True)
                    wer = ", ".join(f"PID {pid}" for _, pid in fremde()) or "?"
                    _melden(f"Mal-Sperre: {was} wartet auf einen anderen Prozess ({wer})")
                _warten_melden(on_status, "🎨 wartet – ein anderer NemiCLI-Prozess malt gerade …", start)
                if max_warten is not None and time.monotonic() - start > max_warten:
                    _melden(f"Mal-Sperre: {was} gibt nach {max_warten:g} s auf")
                    raise Belegt(BELEGT_TEXT)
                time.sleep(TAKT_S)
        except BaseException:
            _marke("wartet", False)
            f.close()
            raise
        _marke("wartet", False)
        if gemeldet:
            _melden(f"Mal-Sperre: frei nach {time.monotonic() - start:.1f} s")
        _marke("malt", True)
        _datei, _tiefe = f, 1
        try:
            yield
        finally:
            _tiefe = 0
            _marke("malt", False)
            _entsperren(f)
            f.close()
            _datei = None
    finally:
        _lokal.release()
