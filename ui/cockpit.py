"""cockpit.py – Live-Werte fürs Cockpit rechts im Vollbild (F3) und kleine Kurven für Antworten und Songs.

Grafikkarte über NVML (nvml.dll vom NVIDIA-Treiber, ctypes, keine Zusatzpakete), Tempo aus den Antworten,
Kontext aus dem Sitzungsstand, Hintergrund-Aufgaben, gelbe Todo-Liste und Wache. Ohne NVIDIA-Karte fehlt
nur der GPU-Teil.
"""
from __future__ import annotations

import ctypes
import time
from collections import deque

from rich.console import Group
from rich.text import Text

FUNKEN = "▁▂▃▄▅▆▇█"
TEMPO: deque = deque(maxlen=40)          # tok/s der letzten Antworten
LIVE = {"tps": None, "bis": 0.0}         # Tempo der gerade strömenden Antwort
BREITE = 34                              # Spalten des Cockpits

_NVML = {"dll": None, "griff": None, "fehler": False, "stand": None, "zeit": 0.0}


def tempo_merken(tps: float | None) -> None:
    if tps and tps > 0:
        TEMPO.append(float(tps))


def tempo_live(tokens: int, sekunden: float) -> None:
    """Während eine Antwort strömt (ab 1 s und 8 Token, sonst schwankt es zu sehr)."""
    if sekunden >= 1.0 and tokens >= 8:
        LIVE["tps"], LIVE["bis"] = tokens / sekunden, time.monotonic() + 2.0


def funke(werte, breite: int) -> str:
    """Werte als Kurve aus Blockzeichen, auf `breite` Zeichen zusammengefasst (Höchstwert je Spalte)."""
    werte = [max(0.0, float(w)) for w in werte]
    if not werte or breite <= 0:
        return ""
    if len(werte) > breite:
        schritt = len(werte) / breite
        werte = [max(werte[int(i * schritt):max(int(i * schritt) + 1, int((i + 1) * schritt))])
                 for i in range(breite)]
    oben = max(werte) or 1.0
    return "".join(FUNKEN[min(len(FUNKEN) - 1, int(w / oben * (len(FUNKEN) - 1) + 0.5))] for w in werte)


def wellenform(pfad, breite: int = 48) -> str:
    """Lautstärke eines WAV über die Zeit als Kurve (RMS je Spalte); leer, wenn die Datei nicht lesbar ist."""
    import wave

    import numpy as np
    try:
        with wave.open(str(pfad), "rb") as w:
            if w.getsampwidth() != 2:
                return ""
            n, kanaele = w.getnframes(), w.getnchannels()
            daten = np.frombuffer(w.readframes(n), "<i2").astype(np.float32)
    except Exception:
        return ""
    if not daten.size:
        return ""
    daten = daten[: daten.size - daten.size % kanaele].reshape(-1, kanaele).mean(1)
    stuecke = np.array_split(daten, breite)
    return funke([float(np.sqrt((s ** 2).mean())) if s.size else 0.0 for s in stuecke], breite)


def _nvml():
    if _NVML["fehler"]:
        return None
    if _NVML["griff"] is not None:
        return _NVML["dll"], _NVML["griff"]
    try:
        dll = ctypes.WinDLL("nvml.dll")
        if dll.nvmlInit_v2() != 0:
            raise OSError("nvmlInit")
        griff = ctypes.c_void_p()
        if dll.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(griff)) != 0:
            raise OSError("Handle")
        _NVML["dll"], _NVML["griff"] = dll, griff
        return dll, griff
    except Exception:
        _NVML["fehler"] = True
        return None


class _Speicher(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _Auslastung(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def gpu(alter_s: float = 1.0) -> dict | None:
    """{"name", "last" %, "belegt", "gesamt" (Bytes), "temp" °C} der ersten NVIDIA-Karte, höchstens alle `alter_s`
    neu gelesen; None ohne NVML."""
    jetzt = time.monotonic()
    if _NVML["stand"] is not None and jetzt - _NVML["zeit"] < alter_s:
        return _NVML["stand"]
    nv = _nvml()
    if nv is None:
        return None
    dll, griff = nv
    try:
        sp, last, temp = _Speicher(), _Auslastung(), ctypes.c_uint()
        name = ctypes.create_string_buffer(96)
        dll.nvmlDeviceGetMemoryInfo(griff, ctypes.byref(sp))
        dll.nvmlDeviceGetUtilizationRates(griff, ctypes.byref(last))
        dll.nvmlDeviceGetTemperature(griff, 0, ctypes.byref(temp))
        dll.nvmlDeviceGetName(griff, name, 96)
        stand = {"name": name.value.decode("utf-8", "replace").replace("NVIDIA GeForce ", ""),
                 "last": int(last.gpu), "belegt": int(sp.used), "gesamt": int(sp.total), "temp": int(temp.value)}
    except Exception:
        return None
    _NVML["stand"], _NVML["zeit"] = stand, jetzt
    return stand


def _balken(anteil: float, zellen: int, farbe: str) -> Text:
    anteil = max(0.0, min(1.0, anteil))
    voll = round(anteil * zellen)
    t = Text("█" * voll, style=farbe)
    t.append("░" * (zellen - voll), style="#3a3a4a")
    return t


def _ampel(anteil: float) -> str:
    return "#5fd38d" if anteil < 0.6 else "#ffb454" if anteil < 0.85 else "#ff6b6b"


def _kopf(titel: str, farbe: str) -> Text:
    return Text(titel, style=f"bold {farbe}")


def render(stand: dict, farbe: str, breite: int = BREITE) -> Group:
    """Das Cockpit als Rich-Gruppe. stand: Sitzungsstand (model, ctx, ctx_max) und optional
    "plan" (plan.daten()), "aufgaben" (hintergrund.laufend()), "fortschritt", "wache"."""
    innen = max(12, breite - 2)
    zeilen: list = []

    g = gpu()
    zeilen.append(_kopf("🎮 Grafikkarte", farbe))
    if g is None:
        zeilen.append(Text("  keine NVIDIA-Karte erkannt", style="#7d8590"))
    else:
        anteil = g["belegt"] / max(1, g["gesamt"])
        z = Text("  VRAM ", style="#9298ac")
        z.append_text(_balken(anteil, innen - 17, _ampel(anteil)))
        z.append(f" {g['belegt'] / 2**30:4.1f}/{g['gesamt'] / 2**30:.0f}G", style="#e4e7f2")
        zeilen.append(z)
        z = Text("  Last ", style="#9298ac")
        z.append_text(_balken(g["last"] / 100, innen - 17, farbe))
        z.append(f" {g['last']:3d}% {g['temp']:>3}°", style="#e4e7f2")
        zeilen.append(z)
        zeilen.append(Text(f"  {g['name']}", style="#7d8590"))

    zeilen.append(Text(""))
    zeilen.append(_kopf("⚡ Tempo", farbe))
    live = LIVE["tps"] if time.monotonic() < LIVE["bis"] else None
    if live:
        zeilen.append(Text(f"  jetzt {live:5.1f} tok/s", style="#e4e7f2"))
    if TEMPO:
        zeilen.append(Text(f"  zuletzt {TEMPO[-1]:.0f} · Mittel {sum(TEMPO) / len(TEMPO):.0f} tok/s", style="#e4e7f2"))
        zeilen.append(Text("  " + funke(TEMPO, innen - 2), style=farbe))
    elif not live:
        zeilen.append(Text("  noch keine Antwort", style="#7d8590"))

    zeilen.append(Text(""))
    zeilen.append(_kopf("🧠 Kontext", farbe))
    voll, gesamt = int(stand.get("ctx") or 0), int(stand.get("ctx_max") or 0)
    if gesamt > 0:
        anteil = voll / gesamt
        z = Text("  ")
        z.append_text(_balken(anteil, innen - 14, _ampel(anteil)))
        z.append(f" {voll / 1000:.1f}k/{gesamt / 1000:.0f}k", style="#e4e7f2")
        zeilen.append(z)
    else:
        zeilen.append(Text("  –", style="#7d8590"))
    modell = str(stand.get("model") or "–")
    zeilen.append(Text("  " + (modell if len(modell) <= innen - 2 else "…" + modell[-(innen - 3):]),
                       style="#7d8590"))

    aufgaben = stand.get("aufgaben") or []
    if aufgaben:
        zeilen.append(Text(""))
        zeilen.append(_kopf("⏳ Im Hintergrund", farbe))
        for e in aufgaben[-4:]:
            titel = str(e.get("titel") or e.get("tool") or "")
            zeilen.append(Text(f"  #{e.get('nr')} {titel[:innen - 6]}", style="#e4e7f2"))
        if stand.get("fortschritt"):
            zeilen.append(Text(f"  {str(stand['fortschritt'])[:innen - 2]}", style="#9298ac"))

    plan = stand.get("plan")
    if plan and plan.get("punkte"):
        punkte = plan["punkte"]
        erledigt = sum(p.get("status") == "erledigt" for p in punkte)
        zeilen.append(Text(""))
        zeilen.append(_kopf(f"📋 Todo  {erledigt}/{len(punkte)}", farbe))
        for p in punkte[:8]:
            zeichen = p.get("zeichen") or {"erledigt": "✓", "gestrichen": "–"}.get(p.get("status"), "○")
            stil = "#7d8590" if p.get("status") in ("erledigt", "gestrichen") else "#e4e7f2"
            zeilen.append(Text(f"  {zeichen} {str(p.get('text') or '')[:innen - 6]}", style=stil))

    if stand.get("wache"):
        zeilen.append(Text(""))
        zeilen.append(_kopf("🛡 Wache", farbe))
        zeilen.append(Text(f"  {str(stand['wache'])[:innen - 2]}", style="#e4e7f2"))

    zeilen.append(Text(""))
    zeilen.append(Text("  F3 schließt · Strg+K Suche", style="#5b5f73"))
    return Group(*zeilen)
