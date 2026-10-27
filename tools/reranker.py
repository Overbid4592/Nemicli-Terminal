"""reranker.py - Treffer der Gedächtnissuche mit einem Cross-Encoder neu sortieren.

Nur wenn die Persönlichkeit selbst sucht (gedaechtnis_suchen), nie im Prompt-Pfad:
Frage und Treffer gehen zusammen durch das Reranker-Modell, das ihre Relevanz 0..1
schätzt. Die besten RERANK_KANDIDATEN der Fusion werden danach sortiert; die Fusion
zählt mit (RERANK_ANTEIL), damit ein knapper Wert sie nicht ganz überstimmt.

Modell: ein Ordner je Modell unter Models/reranker/<Name> im Programm-Ordner mit genau
einer .gguf (z. B. Qwen3-Reranker-0.6B). Config `reranker_modell` (Ordnername), sonst
der erste; `reranker` = False schaltet aus. Gerät: GPU, wenn dort genug frei ist und
gerade kein Bild entsteht, sonst CPU. Nach NACHLAUF_S Ruhe und beim Bildermalen wird
das Modell freigegeben.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

try:
    from paths import INSTALL as _INSTALL
except Exception:                                    # Selbsttest ohne Bootstrap
    _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = _INSTALL / "Models" / "reranker"
RERANK_KANDIDATEN = 16
RERANK_ANTEIL = 0.75               # Gewicht des Rerankers gegen den Rang der Fusion
GPU_FREI_AB = 2 * 2**30            # so viel freier Grafikspeicher, dann rechnet er auf der GPU
NACHLAUF_S = 180.0
NEUER_VERSUCH_S = 300.0

_sperre = threading.Lock()
_modell = None                     # (Pfad, Nachsortierer)
_zuletzt = 0.0
_fehler_bis = 0.0
_uhr: threading.Timer | None = None


def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def an() -> bool:
    return _config().get("reranker", True) is not False


def modelle() -> list[Path]:
    """Ordner mit genau einer .gguf unter Models/reranker."""
    try:
        return [d for d in sorted(ORDNER.iterdir()) if d.is_dir() and len(list(d.glob("*.gguf"))) == 1]
    except OSError:
        return []


def datei() -> Path | None:
    alle = modelle()
    wahl = str(_config().get("reranker_modell") or "")
    ordner = next((d for d in alle if d.name == wahl), alle[0] if alle else None)
    return next(iter(ordner.glob("*.gguf")), None) if ordner else None


def verfuegbar() -> bool:
    return an() and datei() is not None


def _geraet() -> str:
    try:
        import torch
        if not torch.cuda.is_available():
            return "cpu"
        import sys
        zustand = sys.modules.get("gpuzustand")
        if zustand is not None and zustand.kaputt():
            return "cpu"
        lokal = sys.modules.get("gguflokal")
        if lokal is not None and getattr(lokal, "_bild_tiefe", 0):
            return "cpu"
        frei, _gesamt = torch.cuda.mem_get_info()
        return "cuda" if frei >= GPU_FREI_AB else "cpu"
    except Exception:
        return "cpu"


def _laden():
    """Das Modell (lädt beim ersten Mal); None ohne Modell oder nach einem Fehlschlag."""
    global _modell, _fehler_bis
    pfad = datei()
    if pfad is None or time.monotonic() < _fehler_bis:
        return None
    if _modell is not None and _modell[0] == pfad:
        return _modell[1]
    _freigeben_locked()
    try:
        from ggufengine.nachsortierer import Nachsortierer
        geraet = _geraet()
        t = time.monotonic()
        r = Nachsortierer(str(pfad), device=geraet)
        _melden(f"Reranker geladen ({r.name}, {geraet}) in {time.monotonic() - t:.1f} s")
    except Exception as exc:
        _fehler_bis = time.monotonic() + NEUER_VERSUCH_S
        _melden(f"Reranker nicht ladbar: {exc}")
        return None
    _modell = (pfad, r)
    return r


def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def _freigeben_locked() -> None:
    global _modell
    if _modell is None:
        return
    _pfad, r = _modell
    _modell = None
    gpu = getattr(r, "device", None) is not None and r.device.type == "cuda"
    r.close()
    if gpu:
        try:
            import torch
            from ggufengine.models.common import GPU_SPERRE
            with GPU_SPERRE:
                torch.cuda.empty_cache()
        except Exception:
            pass
    _melden("Reranker freigegeben")


def freigeben() -> None:
    """Speicher zurück (Bildermalen, Ruhe). Nach einem CUDA-Speicherfehler bleibt alles, wie es ist."""
    import sys
    zustand = sys.modules.get("gpuzustand")
    if zustand is not None and zustand.kaputt():
        return
    with _sperre:
        _freigeben_locked()


def _nachlauf() -> None:
    global _uhr
    if _uhr is not None:
        _uhr.cancel()

    def pruefen():
        if time.monotonic() - _zuletzt >= NACHLAUF_S:
            freigeben()
        else:
            _nachlauf()
    _uhr = threading.Timer(NACHLAUF_S, pruefen)
    _uhr.daemon = True
    _uhr.start()


def nachsortieren(frage: str, treffer: list[dict]) -> list[dict]:
    """Die besten RERANK_KANDIDATEN nach Relevanz sortieren (Feld `relevanz` 0..1); der Rest folgt
    unverändert. Ohne Modell, ohne Frage oder bei einem Fehler: unverändert."""
    global _zuletzt
    frage = (frage or "").strip()
    if not treffer or not frage or not verfuegbar():
        return treffer
    vorn, rest = treffer[:RERANK_KANDIDATEN], treffer[RERANK_KANDIDATEN:]
    with _sperre:
        r = _laden()
        if r is None:
            return treffer
        try:
            if r.device.type == "cuda":
                import gpuzustand
                gpuzustand.sicherstellen()
                from ggufengine.models.common import GPU_SPERRE
                with GPU_SPERRE:
                    werte = r.bewerten(frage, [h.get("text") or "" for h in vorn])
            else:
                werte = r.bewerten(frage, [h.get("text") or "" for h in vorn])
        except Exception as exc:
            try:
                import gpuzustand
                gpuzustand.pruefen(exc)
            except Exception:
                pass
            _melden(f"Reranker: {type(exc).__name__}: {exc}")
            return treffer
        finally:
            _zuletzt = time.monotonic()
    _nachlauf()
    # neuer score 0..1 – die Vielfalt-Stufe danach (MMR) sortiert nach ihm; der Rest bleibt darunter
    n = len(vorn)
    for i, (h, w) in enumerate(zip(vorn, werte)):
        h["relevanz"] = round(w, 3)
        h["score"] = round(RERANK_ANTEIL * w + (1 - RERANK_ANTEIL) * (1 - i / max(1, n)), 4)
    vorn.sort(key=lambda h: -h["score"])
    if rest:
        boden, oben = min(h["score"] for h in vorn), max(h["score"] for h in rest) or 1.0
        for h in rest:
            h["score"] = round(0.5 * boden * h["score"] / oben, 4)
    return vorn + rest
