"""Zustand der CUDA-Sitzung dieses Prozesses.

Ein Speicherzugriffsfehler auf der Grafikkarte (illegal address, launch failure) macht die
CUDA-Sitzung dauerhaft unbrauchbar: jeder weitere GPU-Aufruf scheitert mit demselben Fehler,
und das Freigeben großer Modelle (gepinnter RAM, Ereignisse) kann den Prozess hart beenden.
Nach so einem Fehler wird deshalb nichts mehr auf der Karte gerechnet oder freigegeben;
erst ein Neustart gibt eine neue Sitzung.
"""

from __future__ import annotations

import threading

MERKMALE = ("illegal memory access", "cudaerrorillegaladdress", "cuda_error_illegal_address",
            "unspecified launch failure", "cudaerrorlaunchfailure", "illegal instruction",
            "misaligned address", "device-side assert", "cuda_error_launch_failed")
MELDUNG = ("Die Grafikkarte hat einen Speicherfehler gemeldet. Damit ist die CUDA-Sitzung von NemiCLI "
           "unbrauchbar – bis zum Neustart rechnet nichts mehr auf der Grafikkarte. Bitte NemiCLI neu starten.")

_sperre = threading.Lock()
_grund = ""


class GpuKaputt(RuntimeError):
    """GPU-Arbeit abgelehnt: die CUDA-Sitzung hatte einen Speicherfehler."""

    def __init__(self) -> None:
        super().__init__(MELDUNG)


def _texte(exc: BaseException):
    gesehen = set()
    while exc is not None and id(exc) not in gesehen:
        gesehen.add(id(exc))
        yield f"{type(exc).__name__}: {exc}"
        exc = exc.__cause__ or exc.__context__


def passt(text: str) -> bool:
    klein = text.lower()
    return any(m in klein for m in MERKMALE)


def pruefen(fehler: BaseException | str | None) -> bool:
    """Merkt sich einen CUDA-Speicherfehler (Ausnahme oder Fehlertext); True, wenn es einer war."""
    global _grund
    if fehler is None or isinstance(fehler, GpuKaputt):
        return False
    texte = [fehler] if isinstance(fehler, str) else list(_texte(fehler))
    treffer = next((t for t in texte if passt(t)), None)
    if treffer is None:
        return False
    with _sperre:
        neu = not _grund
        if neu:
            _grund = treffer.strip().splitlines()[0][:300]
    if neu:
        try:
            import fehlerwaechter
            fehlerwaechter.befund("gpu", "CUDA-Sitzung unbrauchbar – keine GPU-Arbeit mehr bis zum Neustart", _grund)
        except Exception:
            pass
    return True


def kaputt() -> bool:
    return bool(_grund)


def grund() -> str:
    return _grund


def sicherstellen() -> None:
    """Vor GPU-Arbeit: wirft GpuKaputt, wenn die Sitzung unbrauchbar ist."""
    if _grund:
        raise GpuKaputt()


def _zuruecksetzen() -> None:
    """Nur für Tests."""
    global _grund
    with _sperre:
        _grund = ""
