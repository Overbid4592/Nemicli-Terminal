"""
setup.py - Ersteinrichtung des lokalen Motors (Ollama) + Grafikkarten-Erkennung.

NemiCLI soll auch ohne Vorwissen laufen. Dieses Modul:

  • erkennt die Grafikkarte (NVIDIA/AMD/Intel) und ihren Speicher,
  • empfiehlt das „perfekte" Modell für genau diesen PC,
  • legt den Models-Ordner an, falls er fehlt,
  • erkennt, ob Ollama installiert/erreichbar ist, gibt sonst einen
    Hinweis, und lädt Ollama-Modelle direkt herunter (/api/pull).

Bis zum 15.09.2026 lud dieses Modul zusätzlich die llama.cpp-Binaries von
GitHub (passend zur GPU, mit Zip-Slip-Schutz und Host-Allowlist). Das ist auf
Wunsch des Nutzers komplett entfallen - lokale Modelle laufen jetzt
ausschließlich über Ollama, das sein eigenes Laufzeit-Paket mitbringt.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Iterator

import httpx

import models as M
import providers as P

# Wo man Ollama bzw. GGUF-Modelle bekommt (zum SELBER Aussuchen – NemiCLI gibt
# bewusst KEIN bestimmtes Modell vor; der Nutzer entscheidet selbst).
OLLAMA_INSTALL_URL = "https://ollama.com/download"
OLLAMA_LIBRARY_URL = "https://ollama.com/library"


# ===========================================================================
#  GPU erkennen
# ===========================================================================

def _run(cmd: list[str], timeout: float = 8.0) -> str:
    """Führt ein Kommando aus und gibt stdout zurück ('' bei Fehler/fehlt)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "")
    except Exception:
        return ""


def _nvidia() -> dict | None:
    """NVIDIA-GPU über nvidia-smi: Name, VRAM (MB) und max. CUDA-Version des Treibers."""
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits"])
    if not out.strip():
        return None
    first = out.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    name = parts[0] if parts else "NVIDIA GPU"
    vram = 0
    if len(parts) > 1:
        m = re.search(r"\d+", parts[1])
        vram = int(m.group()) if m else 0
    # Max. unterstützte CUDA-Version steht im Kopf der normalen nvidia-smi-Ausgabe.
    # Je nach Treiber heißt sie „CUDA Version:" oder „CUDA UMD Version:".
    head = _run(["nvidia-smi"])
    cm = re.search(r"CUDA(?:\s+UMD)?\s+Version:\s*([\d.]+)", head)
    cuda_max = float(cm.group(1)) if cm else 12.4
    return {"vendor": "nvidia", "name": name, "vram_mb": vram, "cuda_max": cuda_max}


def _wmi_gpu() -> dict | None:
    """Fallback (kein NVIDIA): GPU-Name über Windows abfragen (PowerShell/WMI)."""
    out = _run(["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_VideoController | "
                "Select-Object -ExpandProperty Name) -join '|'"])
    names = [n.strip() for n in out.split("|") if n.strip()]
    if not names:
        return None
    name = names[0]
    low = name.lower()
    if any(x in low for x in ("radeon", "amd", "rx ")):
        vendor = "amd"
    elif any(x in low for x in ("intel", "arc", "iris", "uhd")):
        vendor = "intel"
    elif "nvidia" in low or "geforce" in low or "rtx" in low or "gtx" in low:
        vendor = "nvidia"
    else:
        vendor = "unknown"
    return {"vendor": vendor, "name": name, "vram_mb": 0, "cuda_max": 0.0}


def gpu_info() -> dict:
    """Erkennt die Grafikkarte. Gibt immer ein dict zurück:
    {vendor, name, vram_mb, cuda_max}. vendor='cpu' = keine (nutzbare) GPU."""
    nv = _nvidia()
    if nv:
        return nv
    wmi = _wmi_gpu()
    if wmi and wmi["vendor"] in ("amd", "intel", "nvidia"):
        return wmi
    return {"vendor": "cpu", "name": (wmi or {}).get("name", "CPU"),
            "vram_mb": 0, "cuda_max": 0.0}


# ===========================================================================
#  Hardware-Hinweis (NUR Fakten, KEINE Modell-Empfehlung)
# ===========================================================================
#  Wichtig: NemiCLI gibt KEIN bestimmtes Modell vor. Es nennt nur, welche
#  Größenklasse ungefähr in den vorhandenen Speicher passt – aussuchen tut der
#  Nutzer selbst (Links: Ollama-Bibliothek / Hugging Face).

def hardware_hint(gpu: dict) -> dict:
    """Gibt eine rein faktische Einschätzung zurück: {ram, fits} – welche
    Modell-GRÖSSE etwa in den Speicher passt (ohne ein Modell zu nennen)."""
    vram = gpu.get("vram_mb", 0)
    has_gpu = gpu.get("vendor") in ("nvidia", "amd", "intel")
    if not has_gpu or vram <= 0:
        return {"ram": "keine erkannte GPU",
                "fits": "kleine Modelle (~1–3B) laufen auf der CPU, je nach Arbeitsspeicher"}
    gb = f"{vram / 1024:.0f} GB VRAM"
    if vram >= 24000:
        fits = "große Modelle bis ~30B (Q4) passen rein"
    elif vram >= 16000:
        fits = "Modelle bis ~14B (Q4) laufen flüssig"
    elif vram >= 12000:
        fits = "Modelle bis ~12B (Q4) passen gut"
    elif vram >= 8000:
        fits = "Modelle bis ~8B (Q4) sind ein guter Bereich"
    elif vram >= 4000:
        fits = "kleinere Modelle bis ~3–4B (Q4) passen"
    else:
        fits = "sehr kleine Modelle (~1–2B)"
    return {"ram": gb, "fits": fits}


# ===========================================================================
#  Models-Ordner
# ===========================================================================


def ensure_models_dir() -> Path:
    """Legt den Models-Ordner an, falls er fehlt. Gibt den Pfad zurück."""
    M.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return M.MODELS_DIR


def ollama_cli() -> str | None:
    """Pfad zur ollama-CLI, falls installiert (sonst None)."""
    return shutil.which("ollama")


def ollama_installed() -> bool:
    return ollama_cli() is not None or P.ollama_reachable()


def ollama_running() -> bool:
    return P.ollama_reachable()


def ollama_install_hint() -> str:
    return ("Ollama ist nicht installiert. Hol es dir kostenlos unter "
            f"{OLLAMA_INSTALL_URL} (Windows-Installer), starte es einmal – "
            "danach erkennt NemiCLI es automatisch.")


OLLAMA_LIBRARY_LIST = "https://ollama.com/library?sort=popular"
_catalog_cache: dict = {"t": -1e9, "data": None}


def ollama_catalog(force: bool = False) -> list[tuple[str, list[str]]]:
    """Holt die Modell-Liste LIVE von ollama.com/library (nach Beliebtheit) und
    liest je Modell die Größen-Badges aus. Gibt [(name, [größen])] in
    Beliebtheits-Reihenfolge. Leer bei Netzfehler. Für die Sitzung gecacht.

    So bleibt die Auswahl IMMER aktuell – NemiCLI pflegt keine eigene (veraltende)
    Modell-Liste, sondern zeigt das, was es bei Ollama gerade wirklich gibt."""
    import time as _time
    now = _time.monotonic()
    if not force and _catalog_cache["data"] is not None and now - _catalog_cache["t"] < 600:
        return _catalog_cache["data"]
    try:
        h = httpx.get(OLLAMA_LIBRARY_LIST, timeout=20, follow_redirects=True).text
    except Exception:
        return _catalog_cache["data"] or []
    out: list[tuple[str, list[str]]] = []
    for block in re.split(r"<li x-test-model", h)[1:]:
        m = re.search(r'/library/([^"]+)"', block)
        if not m:
            continue
        name = m.group(1)
        if "embed" in name.lower():                  # Embedding-Modelle sind kein Chat
            continue
        sizes = [s.lower() for s in re.findall(r"x-test-size[^>]*>([^<]+)<", block)]
        # nur echte Parameter-Größen; MoE-Schreibweisen (8x7b) raus (uneindeutig)
        sizes = [s for s in sizes if re.match(r"\d", s) and "x" not in s]
        if sizes:
            out.append((name, sizes))
    if out:
        _catalog_cache.update(t=now, data=out)
    return out


def _size_num(s: str) -> float:
    """Parametergröße in Milliarden. Beachtet die Einheit: '8b'→8, '270m'→0.27."""
    m = re.match(r"([\d.]+)\s*([bm]?)", s.lower())
    if not m:
        return 1e9
    val = float(m.group(1))
    return val / 1000.0 if m.group(2) == "m" else val


def ollama_sizes(catalog, lo: float = 3.0, hi: float = 14.0) -> list[tuple[str, int]]:
    """Saubere Einzel-Stufen (ganze Milliarden, z.B. 4b/7b/8b) zwischen lo und hi,
    aufsteigend, mit Modell-Anzahl. Krumme Größen (3.8b…) landen in den Sammel-
    Stufen ‚klein'/‚groß' (siehe ollama_models_for mit min_b/max_b)."""
    from collections import Counter
    c: Counter = Counter()
    for _name, sizes in catalog:
        for s in set(sizes):
            if re.fullmatch(r"\d+b", s) and lo <= _size_num(s) <= hi:
                c[s] += 1
    return sorted(c.items(), key=lambda kv: _size_num(kv[0]))


def ollama_models_for(catalog, size: str | None = None, min_b: float | None = None,
                      max_b: float | None = None, limit: int = 18) -> list[str]:
    """Modell-Tags (name:größe) für eine exakte Größe ODER einen Bereich
    (min_b < n < max_b), in Beliebtheits-Reihenfolge (gekappt)."""
    out: list[str] = []
    for name, sizes in catalog:
        for s in sizes:
            n = _size_num(s)
            if size is not None and s != size:
                continue
            if min_b is not None and n <= min_b:
                continue
            if max_b is not None and n >= max_b:
                continue
            out.append(f"{name}:{s}")
    return out[:limit]


def ollama_pull(model: str,
                on_progress: Callable[[str, int, int], None] | None = None) -> Iterator[None]:
    """Lädt ein Ollama-Modell über /api/pull (streamt den Fortschritt).
    on_progress(status, completed, total) wird laufend aufgerufen.
    Wirft bei Fehler eine Exception."""
    host = P.ollama_host()
    with httpx.Client(timeout=None) as c:
        with c.stream("POST", f"{host}/api/pull",
                      json={"model": model, "stream": True}) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    import json
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                if on_progress:
                    on_progress(ev.get("status", ""),
                                ev.get("completed", 0) or 0,
                                ev.get("total", 0) or 0)
                yield
