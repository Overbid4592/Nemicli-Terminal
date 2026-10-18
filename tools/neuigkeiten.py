"""neuigkeiten.py – nach einem Update erfährt jede Persönlichkeit, was NemiCLI jetzt neu kann.

Quelle ist die CHANGELOG.md (liegt der exe bei, paths.ASSETS): je Eintrag „- **Überschrift** …“
nur die Überschrift; exe-Build-Zeilen zählen nicht. Was eine Persönlichkeit schon kennt, steht
als kurze Kennung je Eintrag in der Config (`neuigkeiten_gesehen`, je Persönlichkeitskennung).
Beim ersten Mal gibt es nur die letzten ERSTE_TAGE Tage, nicht die ganze Geschichte.
Die Notiz hängt einmal an der nächsten Nutzer-Nachricht (wie lernschleife.hinweise_abholen).
"""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path

ERSTE_TAGE = 2                   # erster Kontakt: Einträge der letzten zwei Tage
MAX_IN_NOTIZ = 12
_AUSLASSEN = re.compile(r"(?i)^(exe build|changelog)\b")
_TITEL = re.compile(r"^- \*\*(.+?)\*\*")

_cache: dict = {"pfad": None, "mtime": None, "eintraege": []}
_lock = threading.Lock()


def _changelog() -> Path:
    import paths
    return Path(paths.ASSETS) / "CHANGELOG.md"


def _kennung(titel: str) -> str:
    return hashlib.sha256(titel.encode("utf-8")).hexdigest()[:12]


def zerlegen(text: str) -> list[tuple[int, str]]:
    """CHANGELOG → [(Tag-Nummer ab 0 = neuester, Überschrift)] in der Reihenfolge der Datei."""
    eintraege, tag = [], -1
    for zeile in text.splitlines():
        if zeile.startswith("### "):
            tag += 1
            continue
        m = _TITEL.match(zeile)
        if not m or tag < 0:
            continue
        titel = m.group(1).replace("`", "").strip().rstrip(".:").strip()
        if titel and not _AUSLASSEN.match(titel):
            eintraege.append((tag, titel))
    return eintraege


def eintraege() -> list[tuple[int, str]]:
    """Einträge der aktuellen CHANGELOG (gelesen nur, wenn sie sich geändert hat)."""
    pfad = _changelog()
    try:
        mtime = pfad.stat().st_mtime
    except OSError:
        return []
    with _lock:
        if _cache["pfad"] != pfad or _cache["mtime"] != mtime:
            try:
                _cache["eintraege"] = zerlegen(pfad.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                _cache["eintraege"] = []
            _cache["pfad"], _cache["mtime"] = pfad, mtime
        return list(_cache["eintraege"])


def hinweis(wer: str) -> str:
    """Notiz mit dem, was `wer` noch nicht kennt ('' wenn nichts) – danach gilt es als bekannt."""
    import config
    alle = eintraege()
    if not alle or not wer:
        return ""
    gesehen_alle = dict(config.load().get("neuigkeiten_gesehen") or {})
    gesehen = gesehen_alle.get(wer)
    jetzt = [_kennung(t) for _tag, t in alle]
    if gesehen is not None and set(jetzt) <= set(gesehen):
        return ""                                      # nichts Neues – Config bleibt unberührt
    if gesehen is None:
        neu = [t for tag, t in alle if tag < ERSTE_TAGE]
    else:
        bekannt = set(gesehen)
        neu = [t for _tag, t in alle if _kennung(t) not in bekannt]
    gesehen_alle[wer] = jetzt
    config.update(neuigkeiten_gesehen=gesehen_alle)
    if not neu:
        return ""
    import lernschleife
    liste = "\n".join(f"- {t}" for t in neu[:MAX_IN_NOTIZ])
    mehr = f"\n- … und {len(neu) - MAX_IN_NOTIZ} weitere" if len(neu) > MAX_IN_NOTIZ else ""
    return (lernschleife.HINWEIS_MARKE + "NemiCLI wurde aktualisiert. Neu seit deinem letzten Stand:\n"
            + liste + mehr + "\nDas kannst du jetzt. Erwähne es, wenn es passt oder der Nutzer fragt; "
            "Einzelheiten zur Bedienung: anleitung_lesen mit thema \"nemicli\".")


def vergessen(wer: str) -> None:
    """Persönlichkeit gelöscht: ihr Stand verfällt (eine neue gleichnamige startet frisch)."""
    import config
    gesehen_alle = dict(config.load().get("neuigkeiten_gesehen") or {})
    if gesehen_alle.pop(wer, None) is not None:
        config.update(neuigkeiten_gesehen=gesehen_alle)
