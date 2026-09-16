"""
subagents.py - Helfer-Agenten: eigenständige kleine Agenten für Teilaufgaben.

Der Haupt-Agent ruft sie NUR bei Bedarf (nicht automatisch) und gibt jedem
eine Rolle und einen Auftrag. Bis zu FÜNF laufen parallel (nur Cloud-Modelle –
ein lokales Modell hat keinen Platz für einen zweiten Kontext).

Jeder Helfer hat einen eigenen, frischen Verlauf, dasselbe Modell und dieselben
Werkzeuge wie der Haupt-Agent. Er arbeitet Schritt für Schritt (Aktion ->
Ergebnis -> nächste Aktion) bis er seinen Bericht abgibt oder die Schrittbremse
greift. Ausgeführt werden die Aktionen NICHT hier, sondern über einen von main
übergebenen Ausführer – der kennt Bestätigung, Modus (lesen/auto) und Anzeige.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import actions
import persona

MAX = 5            # hartes Limit: maximal fünf Helfer gleichzeitig
MAX_STEPS = 8      # Schrittbremse pro Helfer (keine Endlosschleife)
_VERBOTEN = {"subagenten"}   # Helfer rufen keine Helfer (keine Rekursion)

# Sicherheitsstufe (/subagenten): off = gesperrt · an = Nutzer wird vor jedem
# Losschicken gefragt · auto = der Haupt-Agent nimmt sich Helfer nach Bedarf.
STUFEN = ("an", "auto", "off")
STUFE_STANDARD = "an"
_ALIAS = {"on": "an", "aus": "off", "ein": "an"}


def stufe() -> str:
    """Aktuelle Stufe aus der Config (unbekannter Wert -> Standard)."""
    try:
        import config
        wert = str(config.load().get("subagenten", STUFE_STANDARD)).lower()
    except Exception:
        wert = STUFE_STANDARD
    wert = _ALIAS.get(wert, wert)
    return wert if wert in STUFEN else STUFE_STANDARD


def set_stufe(wert: str) -> str | None:
    """Stufe setzen und merken. Gibt die normierte Stufe zurück, None bei Unsinn."""
    wert = _ALIAS.get((wert or "").strip().lower(), (wert or "").strip().lower())
    if wert not in STUFEN:
        return None
    import config
    config.update(subagenten=wert)
    return wert


# Wie viele Helfer laufen gerade? (für die pulsierende Anzeige)
_AKTIV = 0
_ON_AKTIV: list = []          # Callbacks (n) -> None


def aktiv() -> int:
    return _AKTIV


def on_aktiv(cb) -> None:
    """Callback registrieren, das bei jeder Änderung der Helfer-Zahl gerufen wird."""
    if cb not in _ON_AKTIV:
        _ON_AKTIV.append(cb)


def _set_aktiv(n: int) -> None:
    global _AKTIV
    _AKTIV = max(0, n)
    for cb in list(_ON_AKTIV):
        try:
            cb(_AKTIV)
        except Exception:
            pass

# Ausführer: (aktion, helfer_label) -> Rückmeldung als Text fürs Modell
Executor = Callable[[dict, str], Awaitable[str]]

_STOP_NOTE = (
    f"\n\n[System] ⏸ Du hast {MAX_STEPS} Aktionen ausgeführt – das ist deine Obergrenze. "
    "Führe KEINE weitere Aktion aus. Gib jetzt deinen Bericht ab: was du herausgefunden "
    "hast und was offen bleibt."
)


def normalize(act: dict) -> list[dict]:
    """Helfer-Liste aus der Aktion: neues Format `helfer` = [{rolle, aufgabe}, …],
    altes Format `aufgaben` = ["…", …] (Rolle dann „Helfer"). Höchstens MAX."""
    out: list[dict] = []
    roh = act.get("helfer")
    if isinstance(roh, list):
        for h in roh:
            if isinstance(h, dict) and str(h.get("aufgabe", "")).strip():
                out.append({"rolle": str(h.get("rolle", "") or "Helfer").strip(),
                            "aufgabe": str(h["aufgabe"]).strip()})
            elif isinstance(h, str) and h.strip():
                out.append({"rolle": "Helfer", "aufgabe": h.strip()})
    alt = act.get("aufgaben") or ([act["aufgabe"]] if act.get("aufgabe") else [])
    for t in alt or []:
        if isinstance(t, str) and t.strip():
            out.append({"rolle": "Helfer", "aufgabe": t.strip()})
    return out[:MAX]


def label(idx: int, helfer: dict) -> str:
    return f"Helfer {idx} · {helfer['rolle']}"


async def run_one(helfer: dict, backend, execute: Executor, idx: int = 1) -> str:
    """Ein Helfer: eigener Verlauf, Aktionsschleife, am Ende der Bericht.
    Zählt sich für die Anzeige an und – egal wie er endet – wieder ab."""
    _set_aktiv(_AKTIV + 1)
    try:
        return await _run_one(helfer, backend, execute, idx)
    finally:
        _set_aktiv(_AKTIV - 1)


async def _run_one(helfer: dict, backend, execute: Executor, idx: int) -> str:
    system = persona.helfer_prompt(helfer["rolle"], helfer["aufgabe"])
    messages: list[dict] = [{"role": "user", "content": helfer["aufgabe"]}]
    lab = label(idx, helfer)
    for schritt in range(MAX_STEPS + 1):
        letzter = schritt == MAX_STEPS
        try:
            answer = await backend.ask_messages(system, messages)
        except Exception as e:
            return f"(Helfer-Fehler: {e})"
        messages.append({"role": "assistant", "content": answer})
        acts, cleaned = actions.parse_actions(answer)
        if not acts:
            return (cleaned or answer).strip() or "(leerer Bericht)"
        if letzter:
            return (cleaned.strip() + "\n\n(Schrittbremse: Bericht unvollständig)").strip()
        rueck = []
        for act in acts:
            fixed = actions.resolve_tool(act.get("tool"))
            if fixed:
                act["tool"] = fixed
            if act.get("tool") in _VERBOTEN:
                rueck.append("Helfer dürfen keine weiteren Helfer rufen. Mach es selbst.")
                continue
            rueck.append(await execute(act, lab))
        feedback = "\n\n".join(rueck)
        if schritt == MAX_STEPS - 1:
            feedback += _STOP_NOTE
        messages.append({"role": "user", "content": feedback})
    return "(Helfer ohne Bericht)"


async def run(helfer: list[dict], backend, execute: Executor) -> list[tuple[dict, str]]:
    """Führt bis zu MAX Helfer parallel aus. Gibt [(helfer, bericht), …] zurück."""
    helfer = helfer[:MAX]
    if not helfer:
        return []
    berichte = await asyncio.gather(
        *(run_one(h, backend, execute, i) for i, h in enumerate(helfer, 1)))
    return list(zip(helfer, berichte))
