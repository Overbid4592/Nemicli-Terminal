"""
snapshot.py - Sicherung vor jeder Änderung: der Papierkorb von NemiCLI.

Vor jeder verändernden Datei-Aktion landet der alte Stand
in `Papierkorb/` (im Daten-Ordner, neben chats/ und learned/):

  loeschen          Datei/Ordner wird NICHT gelöscht, sondern in den Papierkorb
                    verschoben (schnell, auch bei großen Ordnern).
  datei_schreiben   Gab es die Datei schon: Kopie vom alten Inhalt.
  datei_bearbeiten  Kopie vom alten Inhalt.
  verschieben       Wird am Ziel etwas überschrieben: Kopie davon. Die Quelle
                    selbst ist nicht weg – sie liegt am neuen Ort.

Jeder Eintrag steht in `Papierkorb/_index.jsonl` (eine JSON-Zeile pro
Sicherung: Zeit, Werkzeug, Original, Kopie). `/undo` zeigt die letzten und
stellt auf Wunsch wieder her. Nach AUFBEWAHRUNG_TAGE räumt sich der Papierkorb
beim Start selbst auf.

Grenzen: Einzeldateien über MAX_KOPIE_BYTES werden nicht kopiert (das Ergebnis
sagt es dann deutlich – "kein Undo möglich"). Was schon im Papierkorb liegt,
wird beim Löschen wirklich gelöscht, sonst dreht es sich im Kreis.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

ORDNER = _ROOT / "Papierkorb"
INDEX_NAME = "_index.jsonl"
MAX_KOPIE_BYTES = 200 * 1024 * 1024      # größere Einzeldateien: kein Snapshot
AUFBEWAHRUNG_TAGE = 30


def _index() -> Path:
    return ORDNER / INDEX_NAME


def im_papierkorb(pfad: str | Path) -> bool:
    try:
        Path(pfad).resolve().relative_to(ORDNER.resolve())
        return True
    except (ValueError, OSError):
        return False


def _zielname(original: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ziel = ORDNER / f"{stamp}_{original.name}"
    n = 1
    while ziel.exists():                 # zwei Sicherungen in derselben Sekunde
        ziel = ORDNER / f"{stamp}_{n}_{original.name}"
        n += 1
    return ziel


def _eintragen(eintrag: dict) -> None:
    try:
        with open(_index(), "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except OSError:
        pass


def sichern(pfad: str | Path, werkzeug: str, *, verschieben: bool = False) -> str | None:
    """Sichert `pfad` in den Papierkorb. Gibt den Pfad der Kopie zurück –
    oder None, wenn es nichts zu sichern gab (Pfad fehlt) bzw. nicht ging.

    verschieben=True: das Original wandert in den Papierkorb (für loeschen),
    sonst wird kopiert (für Überschreiben/Bearbeiten).
    Fehler werden nie zur Aktion durchgereicht – der Aufrufer prüft None."""
    original = Path(pfad)
    if not original.exists() or im_papierkorb(original):
        return None
    try:
        ORDNER.mkdir(parents=True, exist_ok=True)
        if original.is_file() and not verschieben \
                and original.stat().st_size > MAX_KOPIE_BYTES:
            return None
        kopie = _zielname(original)
        if verschieben:
            shutil.move(str(original), str(kopie))
        elif original.is_dir():
            shutil.copytree(original, kopie)
        else:
            shutil.copy2(original, kopie)
        try:                             # copy2/move behalten die alte Änderungszeit –
            os.utime(kopie, None)        # aufraeumen() soll ab HEUTE zählen
        except OSError:
            pass
        _eintragen({"zeit": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "werkzeug": werkzeug, "original": str(original.resolve()),
                    "kopie": str(kopie), "art": "verschoben" if verschieben else "kopiert",
                    "ordner": kopie.is_dir()})
        return str(kopie)
    except (OSError, shutil.Error):
        return None


def letzte(n: int = 10) -> list[dict]:
    """Die letzten n Sicherungen, neueste zuerst – nur die, deren Kopie noch da ist."""
    try:
        zeilen = _index().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for z in reversed(zeilen):
        try:
            e = json.loads(z)
        except json.JSONDecodeError:
            continue
        if Path(e.get("kopie", "")).exists():
            out.append(e)
        if len(out) >= n:
            break
    return out


def wiederherstellen(eintrag: dict) -> str:
    """Bringt eine Sicherung an ihren Ursprungsort zurück. Liegt dort inzwischen
    wieder etwas, wird DAS vorher gesichert – ein Undo darf nichts vernichten."""
    kopie = Path(eintrag["kopie"])
    original = Path(eintrag["original"])
    if not kopie.exists():
        raise FileNotFoundError(f"Die Sicherung fehlt: {kopie}")
    if original.exists():
        sichern(original, "undo")
        if original.exists():            # sichern() kopiert bei Dateien nur
            if original.is_dir():
                shutil.rmtree(original)
            else:
                original.unlink()
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(kopie), str(original))
    return str(original)


def aufraeumen(tage: int = AUFBEWAHRUNG_TAGE) -> int:
    """Sicherungen älter als `tage` löschen. Gibt die Anzahl zurück."""
    if not ORDNER.is_dir():
        return 0
    grenze = time.time() - tage * 86400
    weg = 0
    for p in ORDNER.iterdir():
        if p.name == INDEX_NAME:
            continue
        try:
            if p.stat().st_mtime < grenze:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                weg += 1
        except OSError:
            pass
    if weg:                              # Index auf die noch vorhandenen Kopien eindampfen
        rest = [json.dumps(e, ensure_ascii=False) for e in reversed(letzte(10_000))]
        try:
            _index().write_text("\n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
        except OSError:
            pass
    return weg


def groesse() -> int:
    """Belegter Platz in Bytes."""
    total = 0
    if ORDNER.is_dir():
        for root, _dirs, files in os.walk(ORDNER):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
    return total
