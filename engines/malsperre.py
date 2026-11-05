"""
malsperre.py - Eine Mal-Sperre für alle NemiCLI-Prozesse auf diesem Rechner.

Bilder und Videos brauchen die Grafikkarte und viel gepinnten RAM. Die Sperre in einem Prozess
(threading) sieht andere Prozesse nicht: Hauptprogramm und der Mal-Prozess der Wache luden Krea sonst
gleichzeitig – doppelter Grafikspeicher, voller Arbeitsspeicher, alles wird langsam.

Die Sperre ist ein Byte-Lock auf einer Datei im Temp-Ordner; Windows gibt ihn frei, wenn der Prozess
endet. Wer wartet oder malt, legt eine Marke `<art>_<pid>` an. Daran sehen andere Prozesse, dass sie
ihre ruhenden Gewichte freigeben sollen (`fremd_will_malen`).
"""
from __future__ import annotations

import contextlib
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


def _marke(art: str, an: bool) -> None:
    p = ordner() / f"{art}_{os.getpid()}"
    try:
        if an:
            p.write_text(str(time.time()), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _lebt(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return True


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
        if _lebt(int(pid)):
            raus.append((art, int(pid)))
        else:
            with contextlib.suppress(OSError):
                p.unlink()
    return raus


def fremd_will_malen() -> bool:
    return bool(fremde())


class Belegt(RuntimeError):
    """Die Grafikkarte blieb länger als erlaubt bei einem anderen Prozess."""


@contextlib.contextmanager
def halten(on_status=None, was: str = "Bild", max_warten: float | None = None):
    """Malen nur, wenn kein anderer NemiCLI-Prozess malt – sonst warten (höchstens `max_warten` s, dann
    `Belegt`). In einem Prozess verschachtelbar."""
    global _tiefe, _datei
    with _lokal:
        if _tiefe:
            _tiefe += 1
            try:
                yield
            finally:
                _tiefe -= 1
            return
        ordner().mkdir(parents=True, exist_ok=True)
        f = open(ordner() / "sperre", "a+b")
        start, gemeldet = time.monotonic(), False
        try:
            while not _sperren(f):
                if not gemeldet:
                    gemeldet = True
                    _marke("wartet", True)
                    wer = ", ".join(f"PID {pid}" for _, pid in fremde()) or "?"
                    _melden(f"Mal-Sperre: {was} wartet auf einen anderen Prozess ({wer})")
                    if on_status is not None:
                        on_status("🎨 wartet – ein anderer NemiCLI-Prozess malt gerade …")
                if max_warten is not None and time.monotonic() - start > max_warten:
                    _melden(f"Mal-Sperre: {was} gibt nach {max_warten:g} s auf")
                    raise Belegt("Die Grafikkarte ist belegt – ein anderer NemiCLI-Prozess malt oder trainiert "
                                 "gerade. Später noch einmal versuchen.")
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
