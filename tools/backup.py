"""
backup.py - Sicherungen als Zip mit SHA-256 (/backup, Werkzeug backup_erstellen).

Zwei Arten:
  teil  Bilder/, Persoenlichkeiten/ und Profile/ aus dem Daten-Ordner.
  voll  der ganze Daten-Ordner und die Programmdateien – ohne, was sich neu erzeugen
        lässt (venv, .runtime, build, dist, .git, __pycache__, Cache/) und ohne die
        Sicherungen selbst. Modelldateien (ModelGGUF/, Vision/, Models/) nur mit modelle=True.

Differenziell: Gibt es ein volles Backup derselben Art, das höchstens BASIS_TAGE alt ist,
entsteht ein Nachtrag (…_nachtrag_…zip) mit allem, was seit DIESEM vollen Backup neu oder
geändert ist, und der Liste der gelöschten Dateien. Wiederherstellen braucht also nur das
volle Backup und den gewünschten Nachtrag (auspacken). Schnellprüfung über Größe und Zeit
aus dem Zip-Verzeichnis, bei Abweichung entscheidet die SHA-256 aus SHA256SUMS.txt.

Ablage: <Daten-Ordner>/Backup/NemiCLI_<art>_<JJJJ-MM-TT_HH-MM-SS>.zip, daneben
<name>.zip.sha256 im Format von sha256sum. Im Zip liegen BACKUP.json (Art, Version, Zeit,
Zählung) und SHA256SUMS.txt (jede Datei einzeln). Geschrieben wird erst nach <name>.zip.part;
umbenannt wird nur ein vollständiges Zip.

SQLite-Datenbanken (Gedächtnis, Wache) kommen über die Backup-API von sqlite3 ins Zip – die
Kopie ist in sich stimmig, auch wenn die Wache gerade schreibt. Verknüpfungen (Symlinks,
Junctions) und Cloud-Platzhalter werden nicht betreten.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from collections.abc import Callable

import paths

ORDNER_NAME = "Backup"
TEIL_ORDNER = ("Bilder", "Persoenlichkeiten", "Profile")
ARTEN = ("teil", "voll")

# Oberste Ebene des Programm-Ordners: neu erzeugbar.
_PROGRAMM_AUS = {"venv", ".venv", ".runtime", "build", "dist", ".git"}
# Oberste Ebene des Daten-Ordners: Präfix-Cache (neu erzeugbar) und die Sicherungen selbst.
_DATEN_AUS = {"cache", ORDNER_NAME.lower()}
_MODELL_AUS = {"modelgguf", "vision", "models"}
_UEBERALL_AUS = {"__pycache__"}
_DATEI_AUS = (".pyc", "-wal", "-shm", "-journal", ".zip.part")

# Schon gepackte Formate: speichern statt noch einmal komprimieren.
_GEPACKT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".heic", ".zip", ".7z", ".gz", ".xz",
            ".gguf", ".safetensors", ".onnx", ".pt", ".bin", ".mp3", ".mp4", ".ogg", ".webm"}
_SQLITE = {".db", ".sqlite", ".sqlite3"}

# Windows-Dateiattribute: Verknüpfung / Inhalt liegt nicht lokal (Cloud-Platzhalter).
_REPARSE, _OFFLINE, _RECALL_OPEN, _RECALL_ZUGRIFF = 0x400, 0x1000, 0x40000, 0x400000
_NICHT_LOKAL = _REPARSE | _OFFLINE | _RECALL_OPEN | _RECALL_ZUGRIFF

RESERVE = 200 * 1024 * 1024        # so viel bleibt auf dem Ziel-Laufwerk mindestens frei
BASIS_TAGE = 30                    # ältere volle Backups: wieder ganz sichern statt Nachtrag
_META = ("BACKUP.json", "SHA256SUMS.txt")
BLOCK = 1024 * 1024

LIES_MICH = """\
=== NemiCLI-Sicherungen ===

Jede Sicherung ist eine Zip-Datei mit einer .sha256-Datei daneben (Prüfsumme).
/backup pruefen rechnet sie nach – stimmt sie, ist das Zip unverändert.

NemiCLI_teil_…            Bilder, Persönlichkeiten, Profile
NemiCLI_voll_…            Daten-Ordner und Programm (ohne venv, Cache und Modelle)
NemiCLI_voll_modelle_…    dasselbe mit den Modelldateien
NemiCLI_auto_aktuell/vorher  automatisch einmal am Tag: Gedächtnis, Profile, Persönlichkeiten,
                          Agenten, Skills, Zeitplan, Config (ohne Bilder, Modelle, Datenbanken).
                          Jede neue schiebt „aktuell“ nach „vorher“ – diese beiden ersetzt NemiCLI.
…_nachtrag_…              nur, was seit dem letzten vollen Backup derselben Art neu oder
                          geändert ist (+ Liste gelöschter Dateien). Ein volles Backup ist
                          höchstens 30 Tage die Grundlage, danach wird wieder ganz gesichert.

Wiederherstellen: /backup auspacken <name> – packt das volle Backup und (bei einem Nachtrag)
den Nachtrag geprüft in einen NEUEN Ordner hier. Vorhandene Dateien werden nie überschrieben.

Im Zip: BACKUP.json (was, wann, welche Version) und SHA256SUMS.txt (jede Datei einzeln).
Gespeicherte API-Schlüssel (.env) lassen sich nur unter diesem Windows-Konto entschlüsseln.
Alte Sicherungen löschst du selbst – NemiCLI räumt hier nichts weg (außer den beiden automatischen).
"""

_LOCK = threading.Lock()


class Abgebrochen(Exception):
    """Der Nutzer hat das Backup abgebrochen; das halbe Zip ist schon entfernt."""


class NichtsNeu(Exception):
    """Seit dem vollen Backup hat sich nichts geändert – kein Nachtrag nötig."""


@dataclass
class Datei:
    pfad: Path
    name: str                       # Pfad im Zip
    groesse: int
    mtime: float = 0.0


@dataclass
class Ergebnis:
    pfad: Path
    sha256: str
    dateien: int
    roh: int                        # Bytes der gesicherten Dateien
    zip: int                        # Bytes des Zips
    ausgelassen: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    basis: str | None = None        # bei einem Nachtrag: Name des vollen Backups
    unveraendert: int = 0
    geloescht: int = 0

    def text(self) -> str:
        if self.basis:
            zeilen = [f"💾 Nachtrag fertig: {self.pfad.name}",
                      f"   zu {self.basis}",
                      f"   {self.dateien} neu/geändert · {self.unveraendert} unverändert · "
                      f"{self.geloescht} gelöscht · {gb(self.roh)} → {gb(self.zip)}"]
        else:
            zeilen = [f"💾 Backup fertig: {self.pfad.name}",
                      f"   {self.dateien} Dateien · {gb(self.roh)} → {gb(self.zip)}"]
        zeilen += [f"   SHA-256: {self.sha256}",
                   f"   Ordner: {self.pfad.parent}"]
        if self.ausgelassen:
            zeilen.append("   Bewusst nicht dabei: " + ", ".join(self.ausgelassen[:12])
                          + (" …" if len(self.ausgelassen) > 12 else ""))
        if self.fehler:
            zeilen.append(f"   ⚠ {len(self.fehler)} Datei(en) nicht lesbar: " + "; ".join(self.fehler[:5])
                          + (" …" if len(self.fehler) > 5 else ""))
        return "\n".join(zeilen)


def gb(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.0f} KB"


def ordner() -> Path:
    """Backup-Ordner im aktuellen Daten-Ordner (nach /start neu bestimmt)."""
    return Path(paths.DATEN) / ORDNER_NAME


def _schluessel(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))


def _attribute(st: os.stat_result) -> int:
    return getattr(st, "st_file_attributes", 0)


def _ist_verknuepfung(p: Path) -> bool:
    try:
        st = p.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(_attribute(st) & _REPARSE)


# ---------------------------------------------------------------------------
# Was gesichert wird
# ---------------------------------------------------------------------------

def _durchlaufen(start: Path, praefix: str, aus_oben: set[str], meiden: set[str],
                 gesehen: set[str], ausgelassen: list[str]) -> list[Datei]:
    """Alle Dateien unter `start` (ohne Verknüpfungen zu folgen). `aus_oben`: Ordnernamen,
    die direkt unter `start` wegfallen; `meiden`: Ordner (normcase-Pfade), die nie betreten werden."""
    out: list[Datei] = []
    if not start.is_dir() or _ist_verknuepfung(start):
        return out
    for wurzel, unter, dateien in os.walk(start, followlinks=False):
        w = Path(wurzel)
        oben = _schluessel(w) == _schluessel(start)
        behalten = []
        for o in unter:
            p = w / o
            if o.lower() in _UEBERALL_AUS or _schluessel(p) in meiden:
                continue
            if oben and o.lower() in aus_oben:
                ausgelassen.append(f"{praefix}/{o}/")
                continue
            if _ist_verknuepfung(p):
                ausgelassen.append(f"{praefix}/{p.relative_to(start).as_posix()}/ (Verknüpfung)")
                continue
            behalten.append(o)
        unter[:] = sorted(behalten)
        for d in sorted(dateien):
            if d.lower().endswith(_DATEI_AUS):
                continue
            p = w / d
            try:
                st = p.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or _attribute(st) & _NICHT_LOKAL:
                continue
            k = _schluessel(p)
            if k in gesehen:
                continue
            gesehen.add(k)
            out.append(Datei(p, f"{praefix}/{p.relative_to(start).as_posix()}", st.st_size, st.st_mtime))
    return out


def sammeln(art: str, modelle: bool = False) -> tuple[list[Datei], list[str]]:
    """(Dateien, bewusst Ausgelassenes) für eine Sicherung der Art `art`."""
    if art not in ARTEN:
        raise ValueError(f"Unbekannte Art {art!r} – erlaubt: {', '.join(ARTEN)}.")
    daten = Path(os.path.abspath(paths.DATEN))
    install = Path(os.path.abspath(paths.INSTALL))
    meiden = {_schluessel(ordner())}
    gesehen: set[str] = set()
    ausgelassen: list[str] = []
    dateien: list[Datei] = []

    if art == "teil":
        for name in TEIL_ORDNER:
            dateien += _durchlaufen(daten / name, f"Daten/{name}", set(), meiden, gesehen, ausgelassen)
        return dateien, ausgelassen

    modell = set() if modelle else _MODELL_AUS
    if _schluessel(daten) == _schluessel(install):
        dateien += _durchlaufen(install, "NemiCLI", _PROGRAMM_AUS | _DATEN_AUS | modell,
                                meiden, gesehen, ausgelassen)
        return dateien, ausgelassen
    # Liegt eine Wurzel in der anderen, übernimmt jede nur ihren eigenen Teil.
    dateien += _durchlaufen(daten, "Daten", _DATEN_AUS | modell,
                            meiden | {_schluessel(install)}, gesehen, ausgelassen)
    dateien += _durchlaufen(install, "Programm", _PROGRAMM_AUS | modell,
                            meiden | {_schluessel(daten)}, gesehen, ausgelassen)
    return dateien, ausgelassen


# ---------------------------------------------------------------------------
# Zip schreiben
# ---------------------------------------------------------------------------

def _ist_sqlite(p: Path) -> bool:
    if p.suffix.lower() not in _SQLITE:
        return False
    try:
        with open(p, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_kopie(p: Path, tmp: Path) -> Path:
    """Stimmige Kopie einer SQLite-Datenbank über die Backup-API (nur lesend geöffnet)."""
    ziel = tmp / f"{len(list(tmp.iterdir()))}.db"
    quelle = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        kopie = sqlite3.connect(ziel)
        try:
            quelle.backup(kopie)
        finally:
            kopie.close()
    finally:
        quelle.close()
    return ziel


def _datei_hash(p: Path, melde: Callable[[str], None] | None = None) -> str:
    h = hashlib.sha256()
    gesamt, fertig = max(p.stat().st_size, 1), 0
    with open(p, "rb") as f:
        while blk := f.read(BLOCK * 8):
            h.update(blk)
            fertig += len(blk)
            if melde and fertig % (BLOCK * 256) < BLOCK * 8:
                melde(f"Prüfsumme · {fertig * 100 // gesamt} %")
    return h.hexdigest()


def _lies_mich(ziel: Path) -> None:
    """LIES-MICH anlegen; ein älterer, von NemiCLI angelegter Stand wird ersetzt."""
    datei = ziel / "LIES-MICH.txt"
    try:
        if datei.exists():
            alt = datei.read_text(encoding="utf-8", errors="replace")
            if alt == LIES_MICH or not alt.startswith("=== NemiCLI-Sicherungen ==="):
                return
        datei.write_text(LIES_MICH, encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Voll oder Nachtrag
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    """Was eine Sicherung schreiben würde. `basis` gesetzt = Nachtrag zu diesem vollen Backup."""
    art: str
    modelle: bool
    dateien: list[Datei]
    ausgelassen: list[str]
    basis: dict | None = None       # {"name", "erstellt", "sha256"}
    geloescht: list[str] = field(default_factory=list)
    unveraendert: int = 0

    @property
    def nachtrag(self) -> bool:
        return self.basis is not None

    @property
    def roh(self) -> int:
        return sum(d.groesse for d in self.dateien)


def info(zip_pfad: Path) -> dict:
    """BACKUP.json eines Zips ({} wenn unlesbar)."""
    try:
        with zipfile.ZipFile(zip_pfad) as zf:
            d = json.loads(zf.read("BACKUP.json").decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def _summen(zf: zipfile.ZipFile) -> dict[str, str]:
    out = {}
    for zeile in zf.read("SHA256SUMS.txt").decode("utf-8").splitlines():
        teile = zeile.split(None, 1)
        if len(teile) == 2:
            out[teile[1].strip()] = teile[0].lower()
    return out


def _erstellt(d: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(d.get("erstellt")))
    except ValueError:
        return None


def basis_finden(art: str, modelle: bool = False) -> tuple[Path, dict] | None:
    """Neuestes volles (kein Nachtrag) Backup derselben Art und Modell-Wahl."""
    z = ordner()
    if not z.is_dir():
        return None
    beste = None
    for p in z.glob("NemiCLI_*.zip"):
        d = info(p)
        if (d.get("art") != art or bool(d.get("modelle")) != modelle or d.get("nachtrag")
                or _erstellt(d) is None):
            continue
        if beste is None or _erstellt(d) > _erstellt(beste[1]):
            beste = (p, d)
    return beste


def _gleiche_zeit(zip_zeit: tuple, mtime: float) -> bool:
    """Zip-Zeit (lokal, 2-Sekunden-Raster) gegen die Änderungszeit der Datei."""
    lt = time.localtime(mtime)
    return tuple(zip_zeit[:5]) == tuple(lt[:5]) and zip_zeit[5] // 2 == lt[5] // 2


def planen(art: str, modelle: bool = False, ganz: bool = False,
           melde: Callable[[str], None] | None = None,
           abbruch: Callable[[], bool] | None = None) -> Plan:
    """Nachtrag, wenn ein volles Backup derselben Art höchstens BASIS_TAGE alt ist (und nicht
    `ganz`); sonst volles Backup. Unverändert = gleiche Größe und Zeit wie im vollen Backup
    oder – bei abweichender Zeit – gleiche SHA-256."""
    modelle = modelle and art == "voll"
    dateien, ausgelassen = sammeln(art, modelle)
    gefunden = None if ganz else basis_finden(art, modelle)
    if gefunden is None or _erstellt(gefunden[1]) < datetime.now() - timedelta(days=BASIS_TAGE):
        return Plan(art, modelle, dateien, ausgelassen)
    pfad, d = gefunden
    try:
        with zipfile.ZipFile(pfad) as zf:
            eintraege = {zi.filename: zi for zi in zf.infolist()}
            summen = _summen(zf)
    except (OSError, KeyError, zipfile.BadZipFile):
        return Plan(art, modelle, dateien, ausgelassen)
    neu, unveraendert = [], 0
    for i, f in enumerate(dateien, 1):
        zi, soll = eintraege.get(f.name), summen.get(f.name)
        if zi is None or soll is None:
            neu.append(f)
            continue
        if zi.file_size == f.groesse and _gleiche_zeit(zi.date_time, f.mtime):
            unveraendert += 1
            continue
        if abbruch and abbruch():
            raise Abgebrochen()
        if melde:
            melde(f"vergleiche {i}/{len(dateien)}")
        try:
            if _datei_hash(f.pfad) == soll:
                unveraendert += 1
                continue
        except OSError:
            pass
        neu.append(f)
    namen = {f.name for f in dateien}
    try:
        sha = (pfad.parent / (pfad.name + ".sha256")).read_text(encoding="utf-8").split()[0].lower()
    except (OSError, IndexError):
        sha = ""
    return Plan(art, modelle, neu, ausgelassen,
                basis={"name": pfad.name, "erstellt": d.get("erstellt"), "sha256": sha},
                geloescht=sorted(n for n in summen if n not in namen), unveraendert=unveraendert)


def schaetzen(art: str, modelle: bool = False) -> tuple[int, int]:
    """(Anzahl Dateien, Bytes) eines vollen Backups."""
    dateien, _ = sammeln(art, modelle)
    return len(dateien), sum(d.groesse for d in dateien)


def erstellen(art: str, modelle: bool = False, melde: Callable[[str], None] | None = None,
              abbruch: Callable[[], bool] | None = None, *, ganz: bool = False,
              plan: Plan | None = None) -> Ergebnis:
    """Sicherung schreiben – Nachtrag oder voll (planen). Wirft ValueError (nichts zu sichern /
    falsche Art), NichtsNeu, RuntimeError (läuft schon, zu wenig Platz) oder Abgebrochen."""
    melde = melde or (lambda _m: None)
    if art not in ARTEN:
        raise ValueError(f"Unbekannte Art {art!r} – erlaubt: {', '.join(ARTEN)}.")
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("Es läuft schon ein Backup.")
    try:
        if plan is None:
            melde("sammle Dateien …")
            plan = planen(art, modelle, ganz, melde, abbruch)
        return _erstellen(plan, melde, abbruch)
    finally:
        _LOCK.release()


def _erstellen(plan: Plan, melde: Callable[[str], None],
               abbruch: Callable[[], bool] | None, name: str | None = None) -> Ergebnis:
    """`name` = fester Dateiname (automatische Sicherung, wird ersetzt); sonst Zeitstempel, nie überschrieben."""
    art, modelle, dateien = plan.art, plan.modelle, plan.dateien
    if plan.nachtrag and not dateien and not plan.geloescht:
        raise NichtsNeu(f"Seit dem vollen Backup {plan.basis['name']} hat sich nichts geändert – "
                        "kein Nachtrag nötig.")
    if not dateien and not plan.nachtrag:
        raise ValueError("Nichts zu sichern – die Ordner sind leer oder fehlen.")
    roh = plan.roh
    ziel = ordner()
    ziel.mkdir(parents=True, exist_ok=True)
    _lies_mich(ziel)
    frei = shutil.disk_usage(ziel).free
    if frei < roh + RESERVE:
        raise RuntimeError(f"Zu wenig Platz auf dem Laufwerk: gebraucht bis zu {gb(roh + RESERVE)}, "
                           f"frei {gb(frei)}.")

    stempel = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stamm = (f"NemiCLI_{art}{'_modelle' if modelle else ''}"
             f"{'_nachtrag' if plan.nachtrag else ''}_{stempel}")
    if name is None:
        name, n = f"{stamm}.zip", 1
        while (ziel / name).exists() or (ziel / (name + ".part")).exists():   # nie überschreiben
            n += 1
            name = f"{stamm}_{n}.zip"
    endgueltig = ziel / name
    teil = ziel / (name + ".part")
    summen: list[str] = []
    fehler: list[str] = []
    fertig = 0
    try:
        with tempfile.TemporaryDirectory(prefix="nemicli_backup_") as tmp, \
                zipfile.ZipFile(teil, "w", allowZip64=True, compresslevel=6) as zf:
            tmp_pfad = Path(tmp)
            for i, d in enumerate(dateien, 1):
                if abbruch and abbruch():
                    raise Abgebrochen()
                melde(f"Datei {i}/{len(dateien)} · {gb(fertig)} von {gb(roh)}")
                quelle = d.pfad
                if _ist_sqlite(d.pfad):
                    try:
                        quelle = _sqlite_kopie(d.pfad, tmp_pfad)
                    except sqlite3.Error as e:
                        fehler.append(f"{d.name} (Datenbank roh kopiert: {e})")
                try:
                    src = open(quelle, "rb")
                except OSError as e:
                    fehler.append(f"{d.name} ({e.strerror or e})")
                    fertig += d.groesse
                    continue
                with src:
                    zinfo = zipfile.ZipInfo.from_file(quelle, d.name)
                    zinfo.compress_type = (zipfile.ZIP_STORED if d.pfad.suffix.lower() in _GEPACKT
                                           else zipfile.ZIP_DEFLATED)
                    h = hashlib.sha256()
                    with zf.open(zinfo, "w") as dst:
                        while blk := src.read(BLOCK):
                            if abbruch and abbruch():
                                raise Abgebrochen()
                            h.update(blk)
                            dst.write(blk)
                summen.append(f"{h.hexdigest()}  {d.name}")
                fertig += d.groesse
            zf.writestr("SHA256SUMS.txt", "\n".join(summen) + ("\n" if summen else ""))
            kopf = {
                "programm": "NemiCLI",
                "version": _version(),
                "art": art,
                "modelle": modelle,
                "nachtrag": plan.nachtrag,
                "erstellt": datetime.now().isoformat(timespec="seconds"),
                "dateien": len(summen),
                "bytes": roh,
            }
            if plan.nachtrag:
                kopf.update(basis=plan.basis["name"], basis_erstellt=plan.basis["erstellt"],
                            basis_sha256=plan.basis["sha256"], unveraendert=plan.unveraendert,
                            geloescht=plan.geloescht)
            kopf.update(nicht_dabei=plan.ausgelassen, nicht_lesbar=fehler)
            zf.writestr("BACKUP.json", json.dumps(kopf, ensure_ascii=False, indent=2))
        melde("Prüfsumme des Zips …")
        sha = _datei_hash(teil, melde)
        os.replace(teil, endgueltig)
    except BaseException:
        try:
            teil.unlink()
        except OSError:
            pass
        raise
    (ziel / (name + ".sha256")).write_text(f"{sha}  {name}\n", encoding="utf-8")
    return Ergebnis(endgueltig, sha, len(summen), roh, endgueltig.stat().st_size, plan.ausgelassen, fehler,
                    basis=plan.basis["name"] if plan.nachtrag else None,
                    unveraendert=plan.unveraendert, geloescht=len(plan.geloescht))


def _version() -> str:
    try:
        import version
        return version.build_label()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Ansehen und prüfen
# ---------------------------------------------------------------------------

def liste() -> list[dict]:
    """Vorhandene Sicherungen, neueste zuerst."""
    z = ordner()
    if not z.is_dir():
        return []
    out = []
    for p in z.glob("NemiCLI_*.zip"):
        st = p.stat()
        out.append({"name": p.name, "groesse": st.st_size, "zeit": st.st_mtime,
                     "sha256_datei": (z / (p.name + ".sha256")).is_file(),
                     "nachtrag": "_nachtrag_" in p.name})
    return sorted(out, key=lambda e: e["zeit"], reverse=True)


def pruefen(name: str | None = None, melde: Callable[[str], None] | None = None) -> list[tuple[str, bool, str]]:
    """Prüfsummen nachrechnen: [(Name, stimmt, Text)]. `name` nur als Dateiname im Backup-Ordner."""
    z = ordner()
    if name:
        kandidaten = [z / Path(name).name]
    else:
        kandidaten = [z / e["name"] for e in liste()]
    out = []
    for i, p in enumerate(kandidaten, 1):
        if not p.is_file() or not p.name.lower().endswith(".zip"):
            out.append((p.name, False, "nicht gefunden"))
            continue
        sha_datei = z / (p.name + ".sha256")
        if not sha_datei.is_file():
            out.append((p.name, False, "keine .sha256-Datei"))
            continue
        erwartet = sha_datei.read_text(encoding="utf-8", errors="replace").split()[:1]
        teil_melde = (lambda m, i=i: melde(f"{i}/{len(kandidaten)} · {m}")) if melde else None
        ist = _datei_hash(p, teil_melde)
        if erwartet and erwartet[0].lower() == ist:
            out.append((p.name, True, "Prüfsumme stimmt"))
        else:
            out.append((p.name, False, "Prüfsumme stimmt NICHT – Zip verändert oder beschädigt"))
    return out


def status_text() -> str:
    eintraege = liste()
    if not eintraege:
        return f"💾 Noch keine Backups. Ordner: {ordner()}"
    zeilen = [f"💾 {len(eintraege)} Backup(s) in {ordner()}:"]
    for e in eintraege[:20]:
        zeit = datetime.fromtimestamp(e["zeit"]).strftime("%d.%m.%Y %H:%M")
        zeilen.append(f"   {zeit}  {gb(e['groesse']):>10}  {e['name']}"
                      + ("" if e["sha256_datei"] else "  (ohne .sha256)"))
    zeilen.append("   Nachträge enthalten nur Neues seit dem vollen Backup derselben Art – "
                  "Wiederherstellen: /backup auspacken <name>")
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Wiederherstellen (in einen neuen Ordner)
# ---------------------------------------------------------------------------

@dataclass
class Ausgepackt:
    ordner: Path
    dateien: int
    quellen: list[str]
    fehler: list[str] = field(default_factory=list)

    def text(self) -> str:
        zeilen = [f"📦 Ausgepackt: {self.dateien} Dateien",
                  "   aus " + " + ".join(self.quellen),
                  f"   nach {self.ordner}"]
        if self.fehler:
            zeilen.append(f"   ⚠ {len(self.fehler)} Problem(e): " + "; ".join(self.fehler[:5])
                          + (" …" if len(self.fehler) > 5 else ""))
        return "\n".join(zeilen)


def _sicherer_name(name: str) -> Path | None:
    """Pfad aus dem Zip als relativer Pfad – nichts, was aus dem Zielordner herausführt."""
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        return None
    teile = PurePosixPath(name).parts
    if not teile or any(t in (".", "..") for t in teile):
        return None
    return Path(*teile)


def auspacken(name: str, melde: Callable[[str], None] | None = None,
              abbruch: Callable[[], bool] | None = None) -> Ausgepackt:
    """Backup in einen NEUEN Ordner Backup/Wiederhergestellt_<name>/ auspacken. Ein Nachtrag
    kommt über sein volles Backup; dort gelöschte Dateien fehlen. Jede Datei wird gegen
    SHA256SUMS.txt geprüft. `name` nur als Dateiname im Backup-Ordner."""
    melde = melde or (lambda _m: None)
    z = ordner()
    p = z / Path(name).name
    if not p.is_file() or p.suffix.lower() != ".zip":
        raise ValueError(f"{Path(name).name} nicht gefunden.")
    d = info(p)
    if not d:
        raise ValueError(f"{p.name} ist kein NemiCLI-Backup (BACKUP.json fehlt).")
    kette, geloescht = [p], set()
    if d.get("nachtrag"):
        basis = z / Path(str(d.get("basis") or "")).name
        if not basis.is_file() or info(basis).get("nachtrag"):
            raise ValueError(f"Das volle Backup zu diesem Nachtrag fehlt: {basis.name}")
        kette, geloescht = [basis, p], set(d.get("geloescht") or [])
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("Es läuft schon ein Backup.")
    try:
        summen, groesse = [], 0
        for zp in kette:
            with zipfile.ZipFile(zp) as zf:
                summen.append(_summen(zf))
                groesse += sum(zi.file_size for zi in zf.infolist())
        quelle = {n: i for i, s in enumerate(summen) for n in s}      # neuere Zips gewinnen
        for n in geloescht:
            if quelle.get(n) == 0:
                del quelle[n]
        frei = shutil.disk_usage(z).free
        if frei < groesse + RESERVE:
            raise RuntimeError(f"Zu wenig Platz: gebraucht bis zu {gb(groesse + RESERVE)}, frei {gb(frei)}.")
        ziel, n = z / f"Wiederhergestellt_{p.stem}", 1
        while ziel.exists() or ziel.with_name(ziel.name + ".part").exists():
            n += 1
            ziel = z / f"Wiederhergestellt_{p.stem}_{n}"
        teil = ziel.with_name(ziel.name + ".part")
        fehler: list[str] = []
        fertig = 0
        try:
            for i, zp in enumerate(kette):
                with zipfile.ZipFile(zp) as zf:
                    for eintrag, soll in summen[i].items():
                        if quelle.get(eintrag) != i:
                            continue
                        if abbruch and abbruch():
                            raise Abgebrochen()
                        rel = _sicherer_name(eintrag)
                        if rel is None:
                            fehler.append(f"{eintrag} (unsicherer Pfad, ausgelassen)")
                            continue
                        fertig += 1
                        melde(f"Datei {fertig}/{len(quelle)} · {zp.name}")
                        aus = teil / rel
                        aus.parent.mkdir(parents=True, exist_ok=True)
                        h = hashlib.sha256()
                        try:
                            with zf.open(eintrag) as src, open(aus, "wb") as dst:
                                while blk := src.read(BLOCK):
                                    h.update(blk)
                                    dst.write(blk)
                            zeit = time.mktime(zf.getinfo(eintrag).date_time + (0, 0, -1))
                            os.utime(aus, (zeit, zeit))
                        except (KeyError, OSError, zipfile.BadZipFile) as e:
                            fehler.append(f"{eintrag} ({e})")
                            continue
                        if h.hexdigest() != soll:
                            fehler.append(f"{eintrag} (Prüfsumme stimmt nicht)")
            teil.mkdir(parents=True, exist_ok=True)
            os.replace(teil, ziel)
        except BaseException:
            shutil.rmtree(teil, ignore_errors=True)
            raise
        return Ausgepackt(ziel, fertig, [zp.name for zp in kette], fehler)
    finally:
        _LOCK.release()
