"""modellgroesse.py - Darf das Modell den eigenen Charakter umschreiben? Prüfung nach Modellgröße.

Cloud-Modelle dürfen immer. Lokale Modelle erst ab GRENZE_B Milliarden Parametern:
- MoE zählt mit allen Experten.
- Per-Layer-Embedding-Tabellen (Gemma 3n, Gemma 4 E2B/E4B: `per_layer_token_embd`) zählen nicht,
  nur was rechnet. Token-Embeddings und Ausgabe-Kopf zählen mit; Bild-/Ton-Teile liegen in
  eigenen mmproj-Dateien und werden nicht gelesen.
- Unbekannte Größe: darf nicht.

Eigener Motor (`gguf:`): Summe der Tensor-Elemente aus dem GGUF-Kopf (keine Gewichte geladen),
je Datei und Änderungszeit gemerkt. Andere lokale Anbieter: Größe aus dem Namen (`8b`, `26b-a4b`,
`e4b`), bei Ollama ohne Größe im Namen aus /api/tags (nur der eigene Rechner).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, NamedTuple

GRENZE_B = 6.0
PLE_TENSOR = "per_layer_token_embd"           # Per-Layer-Embedding-Tabelle (llama.cpp-Name)
PLE_FAMILIEN = ("gemma3n", "gemma4")          # Ollama: Größe aus /api/tags enthält die Tabellen

_TEIL = re.compile(r"^(.+)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
_E_NAME = re.compile(r"(?<![a-z0-9])e(\d+(?:\.\d+)?)b(?![a-z0-9])")
_MOE_NAME = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b[-_]a\d+(?:\.\d+)?b(?![a-z0-9])")
_MIX_NAME = re.compile(r"(?<![a-z0-9.])(\d+)x(\d+(?:\.\d+)?)b(?![a-z0-9])")
_GROESSE_NAME = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)([bm])(?![a-z0-9])")


class Groesse(NamedTuple):
    milliarden: float
    art: str = ""                             # "" | "moe" | "rechnend" (ohne Per-Layer-Embeddings)


_gemerkt: dict = {}                           # ((Datei, Änderungszeit), …) -> Groesse | None


def _ist_cloud(anbieter: str) -> bool:
    """Cloud = registrierter Cloud-Anbieter, der nicht als lokal gilt (debugbericht.LOKAL)."""
    try:
        import debugbericht
        if anbieter in debugbericht.LOKAL:
            return False
    except Exception:
        pass
    try:
        from cloudprovider import CLOUD
        return anbieter in CLOUD
    except Exception:
        return False


def _zaehlen(tensoren: Iterable) -> tuple[int, bool, bool]:
    """(zählende Elemente, MoE, Per-Layer-Embeddings ausgenommen) aus TensorInfo-Objekten."""
    summe, moe, ple = 0, False, False
    for ti in tensoren:
        if PLE_TENSOR in ti.name:
            ple = True
            continue
        if "_exps." in ti.name:
            moe = True
        summe += int(ti.n_elements)
    return summe, moe, ple


def _teile(datei: Path) -> list[Path] | None:
    """Alle Teile einer geteilten GGUF (…-00001-of-0000N.gguf); None, wenn einer fehlt."""
    m = _TEIL.match(datei.name)
    if not m:
        return [datei]
    alle = [datei.with_name(f"{m.group(1)}-{i:05d}-of-{m.group(3)}.gguf") for i in range(1, int(m.group(3)) + 1)]
    return alle if all(p.is_file() for p in alle) else None


def _aus_gguf(datei: Path) -> Groesse | None:
    teile = _teile(datei)
    if not teile:
        return None
    try:
        schluessel = tuple((str(p), p.stat().st_mtime) for p in teile)
    except OSError:
        return None
    if schluessel in _gemerkt:
        return _gemerkt[schluessel]
    ergebnis = None
    try:
        from ggufengine.gguf import GGUFFile
        summe, moe, ple = 0, False, False
        for p in teile:
            gg = GGUFFile(str(p))
            try:
                s, m, e = _zaehlen(gg.tensors.values())
            finally:
                gg.close()
            summe, moe, ple = summe + s, moe or m, ple or e
        if summe > 0:
            ergebnis = Groesse(summe / 1e9, "moe" if moe else "rechnend" if ple else "")
    except Exception:
        ergebnis = None
    _gemerkt[schluessel] = ergebnis
    return ergebnis


def _aus_namen(name: str) -> Groesse | None:
    """Größe aus dem Modellnamen; bei mehreren Angaben die kleinste."""
    n = (name or "").lower()
    if m := _E_NAME.search(n):
        return Groesse(float(m.group(1)), "rechnend")
    if m := _MOE_NAME.search(n):
        return Groesse(float(m.group(1)), "moe")
    if m := _MIX_NAME.search(n):
        return Groesse(int(m.group(1)) * float(m.group(2)), "moe")
    werte = [float(z) / (1000 if e == "m" else 1) for z, e in _GROESSE_NAME.findall(n)]
    return Groesse(min(werte)) if werte else None


def _parameter_text(text: str) -> float | None:
    """Ollama `parameter_size` („8.0B“, „567.72M“) in Milliarden."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([BMK])\s*", str(text or ""), re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1)) * {"B": 1.0, "M": 1e-3, "K": 1e-6}[m.group(2).upper()]


def _aus_ollama(model_id: str) -> Groesse | None:
    """Größe laut lokalem Ollama (/api/tags). Familien mit Per-Layer-Embeddings: unbekannt."""
    try:
        import providers
        if not providers._ollama_lokal():
            return None
        tags = providers._ollama_tags() or []
    except Exception:
        return None
    gesucht = {model_id, model_id if ":" in model_id else model_id + ":latest"}
    for t in tags:
        if not {t.get("name"), t.get("model")} & gesucht:
            continue
        d = t.get("details") or {}
        familien = [str(f).lower() for f in [d.get("family"), *(d.get("families") or [])] if f]
        if any(f.startswith(PLE_FAMILIEN) for f in familien):
            return None
        wert = _parameter_text(d.get("parameter_size"))
        return Groesse(wert) if wert else None
    return None


def _groesse(ref: str) -> Groesse | None:
    import models
    anbieter, name = models.split_ref(ref or "")
    if anbieter == "gguf":
        try:
            import gguflokal
            datei = gguflokal.modelle().get(name)
        except Exception:
            return None
        return _aus_gguf(datei) if datei is not None else None
    g = _aus_namen(name)
    if g is None and anbieter == "ollama":
        g = _aus_ollama(name)
    return g


def parameter_milliarden(ref: str) -> float | None:
    """Zählende Parameter in Milliarden (MoE voll, ohne Per-Layer-Embeddings); None = unbekannt."""
    g = _groesse(ref)
    return g.milliarden if g else None


def _zahl(x: float, unter: bool) -> str:
    text = f"{x:.1f}"
    if unter and float(text) >= GRENZE_B:     # 5,96 nicht als „6,0 – unter 6“ zeigen
        text = f"{x:.2f}"
    return text.replace(".", ",") + "B"


def darf_selbst_umschreiben(ref: str | None = None) -> tuple[bool, str]:
    """(darf, kurzer Grund). ref None = aktives Modell (Config `model`)."""
    if ref is None:
        try:
            import config
            ref = str(config.load().get("model") or "")
        except Exception:
            ref = ""
    if not ref:
        return False, "kein Modell gewählt"
    try:
        import models
        anbieter = models.split_ref(ref)[0]
    except Exception:
        return False, "Modell unbekannt"
    if _ist_cloud(anbieter):
        return True, "Cloud-Modell"
    try:
        g = _groesse(ref)
    except Exception:
        g = None
    grenze = _zahl(GRENZE_B, False).replace(",0B", "B")
    if g is None:
        return False, f"Größe unbekannt – lokal erst ab {grenze}"
    darf = g.milliarden >= GRENZE_B
    text = _zahl(g.milliarden, not darf)
    if g.art == "moe":
        text += " (MoE, volle Größe)"
    elif g.art == "rechnend":
        text += " rechnend"
    return (True, text) if darf else (False, f"{text} – unter {grenze}")
