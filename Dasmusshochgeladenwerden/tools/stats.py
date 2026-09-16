"""
stats.py - NemiCLI merkt sich, was du mit ihr machst (/statistik).

Sammelt über ALLE Sitzungen hinweg Nutzungs-Zahlen in learned/stats.json –
lokal, privat, wächst mit. KEINE Chat-Inhalte oder Prompts (die liegen ohnehin
vollständig in chats/); auch keine Bilddateien (die liegen in Bilder/). Hier
stehen nur Zahlen und Namen: wie oft welcher Befehl, welches Werkzeug (mit
Erfolg/Fehler), welches Modell, welche Persönlichkeit, welcher Modus, Tokens,
Kosten, gemalte Bilder, aktivste Stunde/Wochentag, Sitzungen und Gesamtzeit.

Robust: Jede Aufzeichnung ist in try/except gekapselt und darf den Chat NIE
stören. Gespeichert wird gedrosselt (höchstens alle paar Sekunden) plus einmal
am Sitzungsende.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent
_FILE = _ROOT / "learned" / "stats.json"

_SAVE_EVERY = 5.0            # Sekunden: nicht bei jedem Ereignis auf die Platte
_data: dict | None = None
_dirty = False
_last_save = 0.0
_session_start: float | None = None


# ---------------------------------------------------------------------------
# Laden / Speichern
# ---------------------------------------------------------------------------

def _fresh() -> dict:
    return {
        "version": 1,
        "first_seen": datetime.now().isoformat(timespec="seconds"),
        "last_seen": None,
        "sessions": 0,
        "total_seconds": 0.0,
        "messages": 0,              # Chat-Runden (Nutzer schickt etwas)
        "commands_total": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
        "images": 0,
        "commands": {},             # "/model": 12
        "tools": {},                # "datei_lesen": {"ok": 40, "fail": 1, "unverified": 0}
        "models": {},               # ref: {"turns", "tokens_in", "tokens_out", "cost"}
        "personas": {},             # Name: turns
        "modes": {},                # "auto": turns
        "hours": [0] * 24,          # aktivste Tageszeit (nach Nachrichten)
        "weekdays": [0] * 7,        # Mo=0 … So=6
    }


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    try:
        _data = json.loads(_FILE.read_text(encoding="utf-8"))
        # Fehlende Felder aus einer älteren Version ergänzen
        base = _fresh()
        for k, v in base.items():
            _data.setdefault(k, v)
        if len(_data.get("hours", [])) != 24:
            _data["hours"] = [0] * 24
        if len(_data.get("weekdays", [])) != 7:
            _data["weekdays"] = [0] * 7
    except Exception:
        _data = _fresh()
    return _data


def _touch(force: bool = False) -> None:
    global _dirty, _last_save
    _dirty = True
    now = time.monotonic()
    if force or now - _last_save >= _SAVE_EVERY:
        flush()


def flush() -> None:
    """Jetzt wirklich speichern (am Sitzungsende aufrufen)."""
    global _dirty, _last_save
    if _data is None or not _dirty:
        return
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8")
        _dirty = False
        _last_save = time.monotonic()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Aufzeichnen  (jede Funktion darf niemals werfen)
# ---------------------------------------------------------------------------

def start_session(model: str | None = None) -> None:
    global _session_start
    try:
        d = _load()
        d["sessions"] += 1
        _session_start = time.monotonic()
        _touch(force=True)
    except Exception:
        pass


def end_session() -> None:
    global _session_start
    try:
        d = _load()
        if _session_start is not None:
            d["total_seconds"] = round(d.get("total_seconds", 0.0)
                                       + (time.monotonic() - _session_start), 1)
            _session_start = None
        d["last_seen"] = datetime.now().isoformat(timespec="seconds")
        flush()
    except Exception:
        pass


def record_command(cmd: str) -> None:
    try:
        d = _load()
        d["commands_total"] += 1
        d["commands"][cmd] = d["commands"].get(cmd, 0) + 1
        _touch()
    except Exception:
        pass


def record_turn(model: str | None, persona: str | None, mode: str | None,
                tokens_in: int, tokens_out: int, cost: float) -> None:
    """Eine abgeschlossene Chat-Runde (Nutzer → Antwort)."""
    try:
        d = _load()
        d["messages"] += 1
        d["tokens_in"] += max(0, int(tokens_in or 0))
        d["tokens_out"] += max(0, int(tokens_out or 0))
        d["cost"] = round(d.get("cost", 0.0) + max(0.0, float(cost or 0.0)), 6)
        now = datetime.now()
        d["hours"][now.hour] += 1
        d["weekdays"][now.weekday()] += 1
        if model:
            m = d["models"].setdefault(model, {"turns": 0, "tokens_in": 0,
                                               "tokens_out": 0, "cost": 0.0})
            m["turns"] += 1
            m["tokens_in"] += max(0, int(tokens_in or 0))
            m["tokens_out"] += max(0, int(tokens_out or 0))
            m["cost"] = round(m["cost"] + max(0.0, float(cost or 0.0)), 6)
        if persona:
            d["personas"][persona] = d["personas"].get(persona, 0) + 1
        if mode:
            d["modes"][mode] = d["modes"].get(mode, 0) + 1
        _touch()
    except Exception:
        pass


def record_tools(records: list) -> None:
    """Die Werkzeug-Bilanz einer Runde übernehmen (records aus dem Aktions-Loop)."""
    try:
        d = _load()
        for r in records or []:
            tool = r.get("tool")
            if not tool:
                continue
            status = r.get("status", "unverified")
            bucket = "ok" if status == "success" else (
                "fail" if status == "failed" else "unverified")
            slot = d["tools"].setdefault(tool, {"ok": 0, "fail": 0, "unverified": 0})
            slot[bucket] = slot.get(bucket, 0) + 1
            if tool == "bild_malen" and status == "success":
                d["images"] += 1
        _touch()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bericht  (/statistik)
# ---------------------------------------------------------------------------

def report() -> dict:
    """Aufbereitete Zahlen für die Anzeige."""
    d = _load()
    return {
        "first_seen": d.get("first_seen"),
        "last_seen": d.get("last_seen"),
        "sessions": d.get("sessions", 0),
        "total_seconds": d.get("total_seconds", 0.0),
        "messages": d.get("messages", 0),
        "commands_total": d.get("commands_total", 0),
        "tokens_in": d.get("tokens_in", 0),
        "tokens_out": d.get("tokens_out", 0),
        "cost": d.get("cost", 0.0),
        "images": d.get("images", 0),
        "top_commands": _top(d.get("commands", {}), 8),
        "top_tools": _tools_ranked(d.get("tools", {}), 10),
        "top_models": _models_ranked(d.get("models", {})),
        "top_personas": _top(d.get("personas", {}), 6),
        "modes": d.get("modes", {}),
        "hours": d.get("hours", [0] * 24),
        "weekdays": d.get("weekdays", [0] * 7),
        "file": str(_FILE),
    }


def _top(counter: dict, n: int) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _tools_ranked(tools: dict, n: int) -> list[tuple[str, int, int, int]]:
    rows = []
    for name, slot in tools.items():
        ok, fail, unv = slot.get("ok", 0), slot.get("fail", 0), slot.get("unverified", 0)
        rows.append((name, ok + fail + unv, ok, fail))
    return sorted(rows, key=lambda r: r[1], reverse=True)[:n]


def _models_ranked(models: dict) -> list[tuple[str, int, int, float]]:
    rows = [(name, m.get("turns", 0), m.get("tokens_out", 0), m.get("cost", 0.0))
            for name, m in models.items()]
    return sorted(rows, key=lambda r: r[1], reverse=True)


def reset() -> None:
    """Alles auf null (für /statistik reset)."""
    global _data
    _data = _fresh()
    flush()
