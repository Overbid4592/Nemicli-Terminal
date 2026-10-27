"""
autobackup.py - Einmal am Tag von selbst: das Kleine und Wichtige, überschrieben, zwei Stände.

Gesichert wird, was NemiCLI über den Nutzer und sich selbst weiß und nicht neu erzeugen kann:
Gedächtnis (learned/), Profile (Charakter, Erinnerungen, Skills, Lehren), Persönlichkeiten,
Agenten, Skills, Zeitplan, die kleinen Dateien der Wache samt Erinnerungen und die Config.
Nicht dabei: Bilder, Videos, Ton, Modelle, Datenbanken (Chats haben ihre eigene Sicherung,
der Such-Index lässt sich neu rechnen) und jede Datei über MAX_DATEI.

Ablage im Backup-Ordner, im selben Format wie /backup (Zip + .sha256, BACKUP.json, SHA256SUMS):
  NemiCLI_auto_aktuell.zip   der neueste Stand
  NemiCLI_auto_vorher.zip    der Stand davor
Jede neue Sicherung schiebt „aktuell“ nach „vorher“. Zwei Stände, weil ein kaputtes Gedächtnis
sonst die einzige gute Sicherung überschreiben könnte, bevor es jemand merkt.

Schutz davor: Ist eine memory.json nicht lesbar oder hat das Gedächtnis plötzlich weniger als
die Hälfte der Notizen der letzten Sicherung, wird NICHT gesichert; der Grund steht im Stand
(auto_stand.json) und NemiCLI sagt es beim Start. `/backup auto jetzt` sichert trotzdem.

Läuft in NemiCLI und in der Wache; eine Sperrdatei sorgt dafür, dass nur einer schreibt.
Wiederherstellen wie jede Sicherung: /backup auspacken NemiCLI_auto_aktuell.zip
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import backup
import paths

STUNDEN = 24                        # so alt darf die letzte Sicherung werden
AKTUELL = "NemiCLI_auto_aktuell.zip"
VORHER = "NemiCLI_auto_vorher.zip"
NEU = "NemiCLI_auto_neu.zip"
STAND_DATEI = "auto_stand.json"
SPERRE = "auto.lock"
SPERRE_ALT_S = 30 * 60              # ältere Sperre: der Prozess ist abgestürzt
MAX_DATEI = 20 * 1024 * 1024
EINBRUCH_AB = 10                    # erst ab so vielen Notizen auf einen Einbruch achten

DATEN_ORDNER = ("learned", "Profile", "Persoenlichkeiten", "Agenten", "Skills", "Zeitplan")
_AUS_ENDUNGEN = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".heic", ".mp4", ".webm", ".mkv", ".mov",
    ".avi", ".mp3", ".wav", ".ogg", ".flac", ".gguf", ".safetensors", ".onnx", ".pt", ".bin", ".ckpt",
    ".db", ".sqlite", ".sqlite3", ".zip", ".7z", ".log",
}

_laeuft = threading.Lock()
_thread: threading.Thread | None = None


def _ordner() -> Path:
    return backup.ordner()


# ---------------------------------------------------------------------------
# Stand
# ---------------------------------------------------------------------------

def stand() -> dict:
    try:
        d = json.loads((_ordner() / STAND_DATEI).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _stand_schreiben(d: dict) -> None:
    try:
        _ordner().mkdir(parents=True, exist_ok=True)
        tmp = _ordner() / (STAND_DATEI + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_ordner() / STAND_DATEI)
    except OSError:
        pass


def faellig(jetzt: float | None = None) -> bool:
    """Ist die letzte gelungene Sicherung älter als STUNDEN? Ein angehaltener Lauf wiederholt
    sich erst am nächsten Tag – sonst käme die Warnung stündlich."""
    jetzt = time.time() if jetzt is None else jetzt
    d = stand()
    zuletzt = max(float(d.get("zeit") or 0), float(d.get("versucht") or 0))
    return jetzt - zuletzt >= STUNDEN * 3600


# ---------------------------------------------------------------------------
# Was gesichert wird
# ---------------------------------------------------------------------------

def sammeln() -> tuple[list[backup.Datei], list[str]]:
    daten = Path(os.path.abspath(paths.DATEN))
    meiden = {backup._schluessel(_ordner())}
    gesehen: set[str] = set()
    ausgelassen: list[str] = []
    roh: list[backup.Datei] = []
    for name in DATEN_ORDNER:
        roh += backup._durchlaufen(daten / name, f"Daten/{name}", set(), meiden, gesehen, ausgelassen)
    wache = daten / "Wache"
    if wache.is_dir():
        for p in sorted(wache.iterdir()):
            if p.is_file():
                try:
                    roh.append(backup.Datei(p, f"Daten/Wache/{p.name}", p.stat().st_size, p.stat().st_mtime))
                except OSError:
                    pass
        roh += backup._durchlaufen(wache / "Erinnerungen", "Daten/Wache/Erinnerungen", set(), meiden,
                                   gesehen, ausgelassen)
    config = Path(paths.INSTALL) / "nemicli.config.json"
    if config.is_file():
        roh.append(backup.Datei(config, "Programm/nemicli.config.json", config.stat().st_size,
                                config.stat().st_mtime))
    dateien = []
    for d in roh:
        if (d.pfad.suffix.lower() in _AUS_ENDUNGEN or d.groesse > MAX_DATEI
                or d.pfad.name.lower().endswith(("-wal", "-shm", "-journal"))):   # Begleitdateien von SQLite
            continue
        dateien.append(d)
    return dateien, ausgelassen


def notizen_zaehlen(dateien: list[backup.Datei]) -> tuple[int, list[str]]:
    """(Anzahl Notizen in allen memory.json, unlesbare memory.json)."""
    anzahl, kaputt = 0, []
    for d in dateien:
        if d.pfad.name.lower() != "memory.json":
            continue
        try:
            daten = json.loads(d.pfad.read_text(encoding="utf-8"))
            anzahl += len(daten["entries"])
        except (OSError, ValueError, KeyError, TypeError):
            kaputt.append(d.name)
    return anzahl, kaputt


# ---------------------------------------------------------------------------
# Sichern
# ---------------------------------------------------------------------------

def _sperren() -> bool:
    pfad = _ordner() / SPERRE
    try:
        _ordner().mkdir(parents=True, exist_ok=True)
        if pfad.exists() and time.time() - pfad.stat().st_mtime > SPERRE_ALT_S:
            pfad.unlink()
        fd = os.open(str(pfad), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
        os.close(fd)
        return True
    except OSError:
        return False


def _entsperren() -> None:
    try:
        (_ordner() / SPERRE).unlink()
    except OSError:
        pass


def _umbenennen(alt: str, neu: str) -> None:
    """Zip samt .sha256 umbenennen; die .sha256 nennt danach den neuen Namen."""
    z = _ordner()
    os.replace(z / alt, z / neu)
    sha_alt = z / (alt + ".sha256")
    if sha_alt.exists():
        summe = sha_alt.read_text(encoding="utf-8").split()[:1]
        sha_alt.unlink()
        if summe:
            (z / (neu + ".sha256")).write_text(f"{summe[0]}  {neu}\n", encoding="utf-8")


def sichern(erzwingen: bool = False) -> dict:
    """Eine Sicherung schreiben. Gibt {"ok", "text", …} zurück; wirft nicht."""
    if not _laeuft.acquire(blocking=False):
        return {"ok": False, "text": "Die automatische Sicherung läuft schon."}
    try:
        if not _sperren():
            return {"ok": False, "text": "Die automatische Sicherung läuft gerade in einem anderen NemiCLI-Prozess."}
        try:
            return _sichern(erzwingen)
        finally:
            _entsperren()
    finally:
        _laeuft.release()


MAX_GEDAECHTNIS = 500 * 1024 * 1024


def _gedaechtnis_kopie() -> Path | None:
    """Stimmige Kopie von learned/memory.db über die SQLite-Sicherungs-API (auch während geschrieben wird).
    Der Index ist sonst neu rechenbar – ältere Berichte leben aber nur noch dort."""
    import sqlite3
    quelle = Path(os.path.abspath(paths.DATEN)) / "learned" / "memory.db"
    try:
        if not quelle.is_file() or quelle.stat().st_size > MAX_GEDAECHTNIS:
            return None
        ziel = _ordner() / f"memory_{os.getpid()}.db.tmp"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.unlink(missing_ok=True)
        from contextlib import closing                 # „with connect()“ allein schließt nicht (nur commit)
        with closing(sqlite3.connect(f"file:{quelle}?mode=ro", uri=True, timeout=10)) as alt, \
                closing(sqlite3.connect(ziel)) as neu:
            alt.backup(neu)
        return ziel
    except (OSError, sqlite3.Error):
        return None


def _sichern(erzwingen: bool) -> dict:
    jetzt = time.time()
    vorher = stand()
    dateien, ausgelassen = sammeln()
    if not dateien:
        return {"ok": False, "text": "Nichts zu sichern – die Daten-Ordner sind leer."}
    notizen, kaputt = notizen_zaehlen(dateien)
    grund = ""
    if kaputt:
        grund = ("Gedächtnis-Datei nicht lesbar: " + ", ".join(kaputt)
                 + " – die letzten Sicherungen bleiben unverändert.")
    elif (vorher.get("notizen") or 0) >= EINBRUCH_AB and notizen < vorher["notizen"] * 0.5:
        grund = (f"Das Gedächtnis hat plötzlich nur noch {notizen} statt {vorher['notizen']} Notizen – "
                 "die letzten Sicherungen bleiben unverändert. Absichtlich gelöscht? Dann /backup auto jetzt.")
    if grund and not erzwingen:
        _stand_schreiben({**vorher, "versucht": jetzt, "angehalten": grund})
        return {"ok": False, "angehalten": True, "text": "💾 Automatische Sicherung angehalten: " + grund}
    if not backup._LOCK.acquire(blocking=False):
        return {"ok": False, "text": "Es läuft gerade ein Backup – die automatische Sicherung kommt später."}
    kopie = _gedaechtnis_kopie()
    try:
        if kopie is not None:
            dateien = dateien + [backup.Datei(kopie, "Daten/learned/memory.db", kopie.stat().st_size,
                                              kopie.stat().st_mtime)]
        plan = backup.Plan("auto", False, dateien, ausgelassen)
        erg = backup._erstellen(plan, lambda _m: None, None, name=NEU)
        z = _ordner()
        if (z / AKTUELL).exists():
            _umbenennen(AKTUELL, VORHER)
        _umbenennen(NEU, AKTUELL)
    except (OSError, ValueError, RuntimeError) as e:
        _stand_schreiben({**vorher, "versucht": jetzt, "fehler": str(e)[:300]})
        return {"ok": False, "text": f"💾 Automatische Sicherung fehlgeschlagen: {e}"}
    finally:
        backup._LOCK.release()
        if kopie is not None:
            kopie.unlink(missing_ok=True)
    _stand_schreiben({"zeit": jetzt, "notizen": notizen, "dateien": erg.dateien, "bytes": erg.zip,
                      "sha256": erg.sha256, "fehler": erg.fehler[:5]})
    try:
        import protokoll
        protokoll.schreibe("backup", "Automatische Sicherung", "success", veraendernd=False, wer="NemiCLI",
                           ergebnis=f"{erg.dateien} Dateien, {notizen} Notizen, {backup.gb(erg.zip)}")
    except Exception:
        pass
    return {"ok": True, "text": f"💾 Automatische Sicherung: {erg.dateien} Dateien, {notizen} Notizen, "
                                f"{backup.gb(erg.zip)} → {AKTUELL}"}


# ---------------------------------------------------------------------------
# Von selbst
# ---------------------------------------------------------------------------

def _schleife(warten: float, takt: float) -> None:
    time.sleep(warten)
    while True:
        try:
            if faellig():
                sichern()
        except Exception:
            pass
        time.sleep(takt)


def starten(warten: float = 90.0, takt: float = 3600.0) -> None:
    """Prüft im Hintergrund (erst nach `warten` Sekunden, dann jede Stunde), ob eine Sicherung
    fällig ist. Mehrfacher Aufruf startet nur einen Thread."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_schleife, args=(warten, takt), daemon=True, name="nemi-autobackup")
    _thread.start()


def beim_start() -> str:
    """Beim Start von NemiCLI: Hintergrund-Prüfung starten. Gibt eine Warnung zurück, wenn die
    letzte automatische Sicherung angehalten wurde, sonst ""."""
    starten()
    d = stand()
    if d.get("angehalten") and float(d.get("versucht") or 0) > float(d.get("zeit") or 0):
        return "💾 Automatische Sicherung angehalten: " + d["angehalten"]
    return ""


def status_text() -> str:
    d = stand()
    if not d.get("zeit"):
        zeilen = ["💾 Automatische Sicherung: noch keine – die erste kommt kurz nach dem Start, dann täglich."]
    else:
        wann = datetime.fromtimestamp(float(d["zeit"])).strftime("%d.%m.%Y %H:%M")
        zeilen = [f"💾 Automatische Sicherung: zuletzt {wann} · {d.get('dateien', '?')} Dateien · "
                  f"{d.get('notizen', '?')} Notizen · {backup.gb(int(d.get('bytes') or 0))}",
                  f"   {AKTUELL} (neu) und {VORHER} (davor) im Backup-Ordner – täglich überschrieben."]
    if d.get("angehalten") and float(d.get("versucht") or 0) > float(d.get("zeit") or 0):
        zeilen.append("   ⚠ Angehalten: " + d["angehalten"])
    elif d.get("fehler") and isinstance(d["fehler"], str):
        zeilen.append("   ⚠ Letzter Versuch fehlgeschlagen: " + d["fehler"])
    zeilen.append("   Was: Gedächtnis, Profile, Persönlichkeiten, Agenten, Skills, Zeitplan, Wache-Erinnerungen, "
                  "Config – ohne Bilder, Videos, Modelle und Datenbanken. Jetzt: /backup auto jetzt")
    return "\n".join(zeilen)
