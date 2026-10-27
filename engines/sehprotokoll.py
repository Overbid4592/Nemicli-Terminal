"""sehprotokoll.py - Protokoll des Bildbeschreibers, je Persönlichkeit getrennt.

`Profile/<Name>/Vision/protokoll.jsonl`, je Zeile: Zeit, Pfad, Fingerabdruck (SHA-256 des
Inhalts), Frage (leer = Beschreibung), Text, Dauer und Seh-Modell (Ordnername unter Vision/).
Dasselbe Bild mit derselben Frage und demselben Seh-Modell wird aus dem Protokoll beantwortet –
das Seh-Modell lädt dafür nicht.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

DATEINAME = "protokoll.jsonl"
MAX_EINTRAEGE = 500
_lock = threading.Lock()


def datei(name: str | None = None, *, anlegen: bool = True) -> Path:
    import profilordner
    return profilordner.ordner("Vision", name, anlegen=anlegen) / DATEINAME


def fingerabdruck(pfad) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 20), b""):
            h.update(stueck)
    return h.hexdigest()


def lesen(pfad: Path | None = None) -> list[dict]:
    """Alle Einträge, älteste zuerst. Kaputte Zeilen werden übersprungen."""
    p = pfad or datei(anlegen=False)
    try:
        zeilen = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    raus = []
    for z in zeilen:
        try:
            e = json.loads(z)
        except ValueError:
            continue
        if isinstance(e, dict):
            raus.append(e)
    return raus


def nachschlagen(sha: str, frage: str | None, modell: str | None, pfad: Path | None = None) -> dict | None:
    """Jüngster Eintrag zu diesem Bild, dieser Frage und diesem Seh-Modell."""
    if not sha or not modell:
        return None
    frage = (frage or "").strip()
    for e in reversed(lesen(pfad)):
        if (e.get("sha") == sha and (e.get("frage") or "") == frage
                and e.get("modell") == modell and e.get("text")):
            return e
    return None


def eintragen(bild, sha: str, frage: str | None, text: str, dauer_s: float, modell: str | None,
              pfad: Path | None = None) -> None:
    """Anhängen; über MAX_EINTRAEGE (+10 %) bleiben die neuesten MAX_EINTRAEGE."""
    p = pfad or datei()
    e = {"zeit": time.strftime("%Y-%m-%d %H:%M:%S"), "pfad": str(bild), "sha": sha,
         "frage": (frage or "").strip(), "text": text, "dauer_s": round(dauer_s, 2), "modell": modell}
    with _lock:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        alle = lesen(p)
        if len(alle) > MAX_EINTRAEGE * 1.1:
            tmp = p.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in alle[-MAX_EINTRAEGE:]),
                           encoding="utf-8")
            os.replace(tmp, p)


def kurz(text: str, n: int = 110) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def zeilen(eintraege: list[dict], volltext: bool = False) -> list[str]:
    """Lesbare Zeilen, neueste zuerst."""
    raus = []
    for e in reversed(eintraege):
        art = f"Frage: {e.get('frage')}" if e.get("frage") else "Beschreibung"
        kopf = (f"{e.get('zeit', '?')}  {Path(str(e.get('pfad', '?'))).name}  · {art}  · "
                f"{e.get('dauer_s', '?')} s · {e.get('modell') or '?'}")
        text = str(e.get("text") or "") if volltext else kurz(e.get("text", ""))
        raus.append(f"{kopf}\n  Pfad: {e.get('pfad', '?')}\n  {text}")
    return raus


def fuer_ki(anzahl: int = 10, bild: str | None = None) -> str:
    """Protokoll für das Werkzeug bild_protokoll: die letzten `anzahl` Einträge, mit `bild` nur die
    zu diesem Bild (Pfad oder Dateiname) und dann mit vollem Text. Als Fremdinhalt gerahmt."""
    import fremddaten
    alle = lesen()
    if bild:
        ziel = str(bild).strip().strip('"').lower()
        name = Path(ziel).name
        alle = [e for e in alle if str(e.get("pfad", "")).lower() == ziel
                or Path(str(e.get("pfad", ""))).name.lower() == name]
    if not alle:
        return ("Im Seh-Protokoll steht dazu nichts." if bild else
                "Das Seh-Protokoll ist leer – der Bildbeschreiber hat für diese Persönlichkeit noch kein Bild angesehen.")
    auswahl = alle[-max(1, min(int(anzahl or 10), 50)):]
    text = "\n\n".join(zeilen(auswahl, volltext=bool(bild)))
    return (f"{len(alle)} Einträge, gezeigt: {len(auswahl)} (neueste zuerst)\n"
            + fremddaten.rahmen(text, "bild", "Seh-Protokoll"))
