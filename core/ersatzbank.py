"""Ersatzbank: bis zu zwei Cloud-Modelle springen ein, wenn das aktive Modell ausfällt.

Ein Modell, das innerhalb von FENSTER_S mindestens SCHWELLE-mal an einer Schutzsperre
scheitert (geschützter Befehl, gesperrter Bereich), wird aus der Config genommen; das
erste Ersatzmodell rückt nach. Modus-Sperren, Kugel-Sperre und Löschlimit zählen nicht:
sie entstehen aus normalen Wünschen des Nutzers, nicht aus Umgehungsversuchen.
Die Liste der herausgefilterten Modelle steht in der Config, damit auch ein
anderer Prozess (Terminal, Wache) das alte Modell nicht weiter benutzt.
"""

from __future__ import annotations

import threading
import time

import config

MAX_ERSATZ = 2
SCHWELLE = 5
FENSTER_S = 30 * 60
LOKAL = ("gguf", "ollama", "funke")
SPERR_ANFAENGE = ("Fehler: 🛡️ Geschützt", "Fehler: 🔒 Komplett gesperrt", "Fehler: 🛡️ Tabu")
MELDUNG = ("Lieber Nutzer, die KI hat mehrfach versucht, die Sperre zu durchdringen, und musste "
           "deshalb herausgefiltert werden. Bitte suche dir mit /model eine neue aus.")

_LOCK = threading.Lock()
_treffer: dict[str, list[float]] = {}
_wechsel: dict | None = None


def ist_cloud(ref: str | None) -> bool:
    return bool(ref) and ":" in ref and ref.split(":", 1)[0] not in LOKAL


def liste() -> list[str]:
    roh = config.load().get("model_ersatz") or []
    return [r for r in roh if isinstance(r, str) and ist_cloud(r)][:MAX_ERSATZ]


def setzen(refs: list[str]) -> list[str]:
    """Speichert die Ersatzbank: nur Cloud-Modelle, ohne Doppelte und ohne das aktive Modell."""
    aktiv = config.load().get("model")
    neu: list[str] = []
    for r in refs:
        if ist_cloud(r) and r != aktiv and r not in neu:
            neu.append(r)
    neu = neu[:MAX_ERSATZ]
    config.update(model_ersatz=neu)
    return neu


def rausgeflogen() -> list[str]:
    return [r for r in (config.load().get("model_rausgeflogen") or []) if isinstance(r, str)]


def begnadigen(ref: str) -> None:
    """Der Nutzer wählt ein herausgefiltertes Modell selbst wieder aus."""
    raus = rausgeflogen()
    if ref in raus:
        config.update(model_rausgeflogen=[r for r in raus if r != ref])
    _treffer.pop(ref, None)


def ist_sperre(text: str | None, ok: bool | None = False) -> bool:
    """Schutzsperre eines Werkzeugs? Nur Fehlschläge zählen – Erfolgstext (etwa ein
    gelesener Dateiinhalt) kann so nie als Sperre gelten."""
    return ok is False and str(text or "").lstrip().startswith(SPERR_ANFAENGE)


def sperre(ref: str | None = None, jetzt: float | None = None) -> bool:
    """Zählt eine Sperre für `ref` (Standard: das Modell aus der Config).
    True, wenn das Modell damit herausgefiltert wurde."""
    ref = ref or config.load().get("model")
    if not ref:
        return False
    jetzt = time.monotonic() if jetzt is None else jetzt
    with _LOCK:
        treffer = [t for t in _treffer.get(ref, []) if jetzt - t < FENSTER_S] + [jetzt]
        _treffer[ref] = treffer
        if len(treffer) < SCHWELLE:
            return False
        return _rauswerfen(ref)


def _rauswerfen(ref: str) -> bool:
    """Unter _LOCK. False, wenn die Config nicht geschrieben werden konnte."""
    global _wechsel
    cfg = config.load()
    bank = [r for r in liste() if r != ref]
    raus = [r for r in rausgeflogen() if r != ref] + [ref]
    if cfg.get("model") in (ref, None, ""):
        neu = bank.pop(0) if bank else ""
        ok = config.update(model=neu, model_ersatz=bank, model_rausgeflogen=raus[-10:])
    else:                                    # ein anderer Prozess hat schon gewechselt
        neu = cfg.get("model")
        ok = config.update(model_ersatz=bank, model_rausgeflogen=raus[-10:])
    if not ok:
        return False
    _treffer.pop(ref, None)
    _wechsel = {"alt": ref, "neu": neu or None}
    return True


def wechsel_offen() -> bool:
    return _wechsel is not None


def abholen() -> dict | None:
    """Der letzte Rauswurf ({'alt', 'neu'}) – einmalig, danach None."""
    global _wechsel
    with _LOCK:
        w, _wechsel = _wechsel, None
    return w


def meldung(neu: str | None) -> str:
    return MELDUNG + (f" Bis dahin springt {neu} ein." if neu else "")
