"""entpacken.py - Hülle um Unpackt (tools/unpackt.py, MIT, siehe tools/unpackt_LICENSE.txt).

Unpackt entpackt ein Archiv in einen neuen Ordner, sortiert nach Art und prüft jede Datei
(Größe, CRC32, SHA-256). Die Datei wird nur geladen, wenn ihre SHA-256 stimmt; sie bleibt
unverändert. Die Hülle setzt nach dem Laden nur die Abfrage des Virenschutz-Status über das
Windows-Sicherheitscenter (wscapi.dll) ein – ohne Hilfsprozess. Die AMSI-Prüfung bleibt.

Sichtbar läuft das Entpacken in einem eigenen Fensterprozess (`--entpacken`, ui/entpacken_fenster.py).
Ergebnis über eine JSON-Datei, nur Zahlen und Status – nie Dateiinhalte.
Entpackte Dateien werden nie geöffnet oder gestartet.
"""
from __future__ import annotations

import csv
import functools
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import paths

UNPACKT_SHA256 = "343ac949a58ba70b153e8babca734a3b6f1357640aaeeb99e94b8e0bfa2e68a9"
UNPACKT_VERSION = "1.1.0"
MODULNAME = "unpackt_geprueft"

# Wie Unpackt-Kategorie „Archive“ (Test vergleicht beides).
ARCHIV_ENDUNGEN = frozenset("." + e for e in
                            "zip 7z rar tar gz tgz bz2 tbz2 xz txz zst lz lzma cab iso dmg".split())
_TAR_ENDUNGEN = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lzma", ".tar.lz")

MODI = ("an", "auto", "aus")
ERLEDIGT_ARTEN = ("entpackt", "abgelehnt", "fehler")
ERGEBNIS_FELDER = ("code", "dateien", "ordner", "geprueft", "fehler", "ziel", "eingelesen", "chats")
CODES_FERTIG = (0, 1, 5)               # entpackt (5: der Virenschutz hat einzelne Dateien blockiert)

CODE_TEXT = {0: "Fertig – alles geprüft ✓", 1: "Fertig, aber mit Fehlern", 2: "Konnte nicht starten",
             3: "Abgebrochen", 4: "Vom Schutz angehalten (verdächtiges Archiv)",
             5: "Fertig – der Virenschutz hat Dateien blockiert"}

_MODUL = None
_LADE_LOCK = threading.Lock()
_ERLEDIGT_LOCK = threading.Lock()
_VERGEBEN: set[str] = set()


# ---------------------------------------------------------------- Laden mit SHA-256-Prüfung

def unpackt_pfad() -> Path:
    """Rohe unpackt.py: im Quelltext neben dieser Datei, in der exe unter tools/ im Bündel."""
    if getattr(sys, "frozen", False):
        return Path(paths.ASSETS) / "tools" / "unpackt.py"
    return Path(__file__).resolve().with_name("unpackt.py")


class _GeprueftLader(importlib.machinery.SourceFileLoader):
    """Übersetzt genau die geprüften Bytes – kein erneutes Lesen, kein Bytecode-Cache."""

    def __init__(self, name: str, pfad: str, daten: bytes):
        super().__init__(name, pfad)
        self._daten = daten

    def get_data(self, path):
        return self._daten if path == self.path else super().get_data(path)

    def get_code(self, fullname):
        return self.source_to_code(self._daten, self.path)


@functools.lru_cache(maxsize=1)
def virenschutz_aktiv() -> tuple[tuple[str, bool], ...]:
    """Ersatz für unpackt.antivirus_products: Zustand des Virenschutzes laut Windows-Sicherheitscenter.
    Gut → (("Virenschutz", True),), sonst oder bei Fehler (). Kein Hilfsprozess."""
    if sys.platform != "win32":
        return ()
    try:
        import ctypes
        dll = ctypes.WinDLL("wscapi.dll")
        abfrage = dll.WscGetSecurityProviderHealth
        abfrage.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)]
        abfrage.restype = ctypes.c_long
        zustand = ctypes.c_int(-1)
        if abfrage(4, ctypes.byref(zustand)) != 0:          # WSC_SECURITY_PROVIDER_ANTIVIRUS
            return ()
        return (("Virenschutz", True),) if zustand.value == 0 else ()   # WSC_SECURITY_PROVIDER_HEALTH_GOOD
    except (OSError, AttributeError, ValueError):
        return ()


def _laden_aus(pfad: Path):
    try:
        daten = Path(pfad).read_bytes()
    except OSError as e:
        raise RuntimeError(f"Unpackt fehlt oder ist nicht lesbar: {pfad} ({e})") from e
    ist = hashlib.sha256(daten).hexdigest()
    if ist != UNPACKT_SHA256:
        raise RuntimeError(f"Unpackt wird nicht geladen: SHA-256 von {Path(pfad).name} stimmt nicht "
                           f"(erwartet {UNPACKT_SHA256[:16]}…, gefunden {ist[:16]}…).")
    lader = _GeprueftLader(MODULNAME, str(pfad), daten)
    spec = importlib.util.spec_from_file_location(MODULNAME, str(pfad), loader=lader)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[MODULNAME] = modul
    try:
        spec.loader.exec_module(modul)
    except BaseException:
        sys.modules.pop(MODULNAME, None)
        raise
    modul.antivirus_products = virenschutz_aktiv
    return modul


def laden():
    """Unpackt-Modul (einmal geladen, danach gemerkt). RuntimeError, wenn die SHA-256 nicht stimmt."""
    global _MODUL
    with _LADE_LOCK:
        if _MODUL is None:
            _MODUL = _laden_aus(unpackt_pfad())
        return _MODUL


# ---------------------------------------------------------------- Modus

def modus() -> str:
    """„an“ = nachfragen, „auto“ = ohne Nachfrage entpacken, „aus“ = nie."""
    try:
        import config
        wert = config.load().get("zip_modus", "an")
    except Exception:
        wert = "an"
    return wert if wert in MODI else "an"


def modus_setzen(wert: str) -> bool:
    wert = str(wert or "").strip().lower()
    if wert not in MODI:
        raise ValueError(f"Unbekannter Modus: {wert!r} (erlaubt: {', '.join(MODI)})")
    import config
    return bool(config.update(zip_modus=wert))


# ---------------------------------------------------------------- Ziele

def stamm(quelle: Path) -> str:
    """Archivname ohne Endung, nur sichere Zeichen, höchstens 60 Zeichen."""
    name = Path(quelle).name
    for endung in _TAR_ENDUNGEN:
        if name.lower().endswith(endung):
            name = name[:-len(endung)]
            break
    else:
        name = Path(name).stem
    name = name.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                                         "ß": "ss"}))
    t = re.sub(r"[^A-Za-z0-9_\-]+", "_", name)
    t = re.sub(r"_+", "_", t)[:60].strip("_-") or "Archiv"
    from profilordner import RESERVIERT
    return f"{t}_" if t.upper() in RESERVIERT else t


def _neues_ziel(ordner: Path, quelle: Path) -> Path:
    basis = f"{stamm(quelle)}_{time.strftime('%Y-%m-%d_%H%M')}"
    kandidat, n = ordner / basis, 2
    with _ERLEDIGT_LOCK:
        while kandidat.exists() or str(kandidat).lower() in _VERGEBEN:
            kandidat, n = ordner / f"{basis}_{n}", n + 1
        _VERGEBEN.add(str(kandidat).lower())
    return kandidat


def ziel_fuer_chat(quelle: Path) -> Path:
    return _neues_ziel(Path(paths.DATEN) / "Entpackt", quelle)


def _profil_ordner(profil: str) -> Path:
    import profilordner
    return profilordner.wurzel() / profilordner.ordnername(profil)


def ziel_fuer_profil(profil: str, quelle: Path) -> Path:
    return _neues_ziel(_profil_ordner(profil) / "Entpackt", quelle)


# ---------------------------------------------------------------- Neue Archive im Profilordner

def _erledigt_datei(profil: str) -> Path:
    return _profil_ordner(profil) / "Entpackt" / "_erledigt.json"


def _schluessel(pfad: Path) -> str:
    st = Path(pfad).stat()
    return f"{Path(pfad).name}|{st.st_size}|{st.st_mtime_ns}"


def _erledigt_lesen(profil: str) -> dict:
    import atomar
    try:
        return atomar.json_lesen(_erledigt_datei(profil), {}, dict)
    except OSError:
        return {}


def ist_archiv(pfad: Path) -> bool:
    return Path(pfad).suffix.lower() in ARCHIV_ENDUNGEN


def neue_zips(profil: str) -> list[Path]:
    """Archive direkt in Profile/<profil>/ (nicht rekursiv), die noch nicht behandelt sind; älteste zuerst."""
    ordner = _profil_ordner(profil)
    erledigt = _erledigt_lesen(profil)
    funde = []
    try:
        eintraege = list(ordner.iterdir())
    except OSError:
        return []
    for p in eintraege:
        try:
            if not (p.is_file() and ist_archiv(p)) or _schluessel(p) in erledigt:
                continue
            funde.append((p.stat().st_mtime_ns, p))
        except OSError:
            continue
    return [p for _, p in sorted(funde)]


def als_erledigt(profil: str, pfad: Path, wie: str) -> None:
    """Archiv als behandelt merken (entpackt | abgelehnt | fehler) – danach fragt neue_zips nicht mehr."""
    if wie not in ERLEDIGT_ARTEN:
        raise ValueError(f"Unbekannt: {wie!r} (erlaubt: {', '.join(ERLEDIGT_ARTEN)})")
    import atomar
    schluessel = _schluessel(pfad)
    datei = _erledigt_datei(profil)
    with _ERLEDIGT_LOCK:
        datei.parent.mkdir(parents=True, exist_ok=True)
        daten = _erledigt_lesen(profil)
        daten[schluessel] = {"wie": wie, "zeit": time.strftime("%Y-%m-%d %H:%M:%S")}
        atomar.text_schreiben(datei, json.dumps(daten, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- Ergebnis

def auswerten(ziel: Path, code: int) -> dict:
    """Zahlen aus der Zuordnung.csv von Unpackt: einsortierte Dateien, Ordner nach Art, Fehler."""
    ziel = Path(ziel)
    dateien, fehler, arten = 0, 0, set()
    try:
        with open(ziel / "Zuordnung.csv", encoding="utf-8-sig", newline="") as f:
            for zeile in csv.DictReader(f, delimiter=";"):
                pruefung = zeile.get("pruefung") or ""
                if pruefung.startswith("ok"):
                    dateien += 1
                    arten.add(zeile.get("art") or "")
                else:
                    fehler += 1
    except (OSError, csv.Error, UnicodeDecodeError):
        pass
    chats = 0
    try:
        chats = sum(1 for p in (ziel / "Chats").glob("*.md") if p.is_file())
    except OSError:
        pass
    return {"code": int(code), "dateien": dateien, "ordner": len(arten), "geprueft": code == 0 and fehler == 0,
            "fehler": fehler, "ziel": str(ziel), "eingelesen": 0, "chats": chats}


def leeres_ergebnis(ziel: Path | str = "", code: int = 3) -> dict:
    return {"code": code, "dateien": 0, "ordner": 0, "geprueft": False, "fehler": 0, "ziel": str(ziel),
            "eingelesen": 0, "chats": 0}


def ergebnis_schreiben(pfad: Path, daten: dict) -> None:
    import atomar
    sauber = ergebnis_bereinigen(daten, daten.get("ziel", ""))
    Path(pfad).parent.mkdir(parents=True, exist_ok=True)
    atomar.text_schreiben(pfad, json.dumps(sauber, ensure_ascii=False))


def ergebnis_bereinigen(daten: dict, ziel: Path | str = "") -> dict:
    """Nur die bekannten Felder mit festen Typen."""
    aus = leeres_ergebnis(ziel)
    if not isinstance(daten, dict):
        return aus
    for feld in ("code", "dateien", "ordner", "fehler", "eingelesen", "chats"):
        try:
            aus[feld] = int(daten.get(feld, aus[feld]))
        except (TypeError, ValueError):
            pass
    aus["geprueft"] = daten.get("geprueft") is True
    if isinstance(daten.get("ziel"), str) and daten["ziel"]:
        aus["ziel"] = daten["ziel"]
    return aus


def ergebnis_lesen(pfad: Path, ziel: Path | str = "") -> dict:
    """Ergebnis des Fensters; fehlt es (Prozess beendet ohne Ergebnis) → code 3."""
    import atomar
    try:
        daten = atomar.json_lesen(pfad, None, dict)
    except OSError:
        daten = None
    return leeres_ergebnis(ziel) if daten is None else ergebnis_bereinigen(daten, ziel)


def zusammenfassung(ergebnis: dict) -> str:
    """Kurzer Text zum Ergebnis, z. B. für die Antwort im Chat."""
    e = ergebnis_bereinigen(ergebnis, ergebnis.get("ziel", "") if isinstance(ergebnis, dict) else "")
    teile = [CODE_TEXT.get(e["code"], f"Ende (Code {e['code']})")]
    if e["dateien"]:
        teile.append(f"{e['dateien']} Dateien in {e['ordner']} Ordnern"
                     + (f", {e['chats']} Chats" if e["chats"] else ""))
    if e["fehler"]:
        teile.append(f"{e['fehler']} Fehler")
    if e["geprueft"]:
        teile.append("geprüft ✓ (Größe, CRC32, SHA-256)")
    if e["eingelesen"]:
        teile.append(f"{e['eingelesen']} ins Gedächtnis eingelesen")
    if e["ziel"]:
        teile.append(f"Ziel: {e['ziel']}")
    return " · ".join(teile)


# ---------------------------------------------------------------- Fensterprozess

def fenster_befehl(quelle: Path, ziel: Path, ergebnis: Path, profil: str | None = None,
                   einlesen: bool = False) -> list[str]:
    if getattr(sys, "frozen", False):
        befehl = [str(paths.exe_ohne_fenster()), "--entpacken"]
    else:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        main = Path(__file__).resolve().parents[1] / "main.py"
        befehl = [str(pythonw if pythonw.exists() else python), str(main), "--entpacken"]
    befehl += [str(quelle), str(ziel), str(ergebnis)]
    if profil:
        befehl += ["--profil", str(profil)]
    if einlesen:
        befehl.append("--einlesen")
    return befehl


def fenster_starten(quelle: Path, ziel: Path, profil: str | None = None, einlesen: bool = False) -> dict:
    """Entpackt sichtbar im eigenen Fensterprozess und wartet, bis das Entpacken fertig ist (synchron).
    Das Fenster bleibt danach offen (Einlesen, Zusammenfassung) – darauf wartet der Aufrufer nicht.
    Rückgabe: code, dateien, ordner, geprueft, fehler, ziel, eingelesen, chats."""
    quelle, ziel = Path(quelle).resolve(), Path(ziel).resolve()
    ablage = Path(tempfile.gettempdir()) / "NemiCLI"
    ablage.mkdir(parents=True, exist_ok=True)
    ergebnis = ablage / f"entpacken_{os.getpid()}_{time.time_ns()}.json"
    befehl = fenster_befehl(quelle, ziel, ergebnis, profil, einlesen)
    try:
        prozess = subprocess.Popen(befehl, cwd=str(paths.INSTALL))
        while prozess.poll() is None and not ergebnis.exists():     # atomar geschrieben: da = vollständig
            time.sleep(0.2)
    except OSError:
        return leeres_ergebnis(ziel, code=2)
    try:
        aus = ergebnis_lesen(ergebnis, ziel)
    finally:
        try:
            ergebnis.unlink(missing_ok=True)
        except OSError:
            pass
    zone_uebertragen(quelle, ziel)
    return aus


# ---------------------------------------------------------------- Download-Vermerk

ZONE_STROM = ":Zone.Identifier"
_ZONE_MAX = 4096


def zone_lesen(quelle: Path) -> str | None:
    """Download-Vermerk (Mark of the Web) der Quelle, wenn sie aus dem Internet oder einer nicht vertrauten Zone
    stammt (ZoneId ≥ 3) – sonst None."""
    try:
        with open(str(quelle) + ZONE_STROM, encoding="utf-8", errors="replace") as f:
            text = f.read(_ZONE_MAX)
    except OSError:
        return None
    m = re.search(r"(?im)^\s*ZoneId\s*=\s*(\d+)", text)
    return text if m and int(m.group(1)) >= 3 else None


def zone_uebertragen(quelle: Path, ziel: Path) -> int:
    """Den Download-Vermerk des Archivs auf jede entpackte Datei übertragen – wie der Explorer beim Entpacken.
    So erkennen Windows (SmartScreen, Office) und NemiCLI (fremddaten) die Dateien als aus dem Netz.
    Keine Verknüpfungen: Junctions werden nicht betreten, Dateien mit mehreren Namen nicht beschrieben.
    Gibt die Anzahl markierter Dateien zurück."""
    text = zone_lesen(quelle)
    if text is None or not Path(ziel).is_dir():
        return 0
    n = 0
    for wurzel, ordner, dateien in os.walk(ziel):
        ordner[:] = [o for o in ordner if not (os.path.islink(p := os.path.join(wurzel, o)) or os.path.isjunction(p))]
        for name in dateien:
            pfad = os.path.join(wurzel, name)
            try:
                st = os.lstat(pfad)
                if os.path.islink(pfad) or st.st_nlink > 1:
                    continue
                with open(pfad + ZONE_STROM, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
                n += 1
            except OSError:
                continue
    return n
