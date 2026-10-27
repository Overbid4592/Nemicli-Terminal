"""Vollscan: alle lokalen Laufwerke als Ausgangsstand (Baseline) der Wache.

Stufe 0  Datenträger: jede Festplatte mit Partitionen und Volumes (datentraeger.py).
Stufe 1  jede Datei aller lokalen Volumes (Festplatten, Wechseldatenträger,
         RAM-Disks, eingebundene Abbilder – auch ohne Laufwerksbuchstaben, auch
         Pfade über 260 Zeichen): Pfad, Größe, Änderungszeit, Attribute. Auf
         NTFS dazu zusätzliche Datenströme (ADS) und bei Programmen die
         Download-Herkunft (Zone.Identifier). Nicht lesbare Ordner und nicht
         betretene Verweise werden mit Grund bzw. Ziel festgehalten.
Stufe 2  ausführbarer Code und Skripte zusätzlich: SHA-256, Signaturstatus,
         Signierer (wintrust.dll im eigenen Prozess). Der vorige Hash bleibt
         erhalten; der Bericht listet Code, dessen Inhalt sich geändert hat.
Stufe 3  Systemstand (systemstand.py): Autostart, Dienste, Aufgaben, Treiber,
         Netz – mit Signatur und Ampel.

Läuft als eigener Prozess (`--vollscan`) im Hintergrundmodus von Windows
(niedrige CPU-, Datenträger- und Speicherpriorität), nur lesend, fortsetzbar.
Von Windows gesperrte Systembereiche sind Revier des Virenschutzes: Sie werden
nicht aufgebrochen und stehen im Bericht als gesperrt.
Ein weiterer Lauf prüft in Stufe 2 Dateien, deren Größe oder Änderungszeit
sich geändert hat, und hasht nicht gültig signierten Code immer neu – dort
fiele eine Änderung ohne neues Datum sonst nicht auf. Jeder Lauf trägt den Windows-Stand (Build,
zuletzt installierte Updates), damit neue Dateien einem Update zuordenbar sind.

Nicht betreten werden: Verknüpfungen/Junctions, Cloud-Platzhalter (würden
sonst heruntergeladen) und die Programmordner der Produkte, die im
Windows-Sicherheitscenter eingetragen sind.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import BERICHTE, ORDNER, ordner_anlegen

DB = ORDNER / "vollscan.db"
STAND = ORDNER / "vollscan.json"
STOPP = ORDNER / "vollscan_stopp"
STARTS = ORDNER / "vollscan_starts.json"   # Startzeiten neuer Vollscans der letzten 7 Tage
PRO_WOCHE = 3                              # höchstens so viele neue Vollscans in 7 Tagen
WOCHE = 7 * 86400

# Stufe 2: alles, was Code ausführen kann.
CODE_ENDUNGEN = {
    ".exe", ".dll", ".pyd", ".sys", ".scr", ".ocx", ".cpl", ".com", ".drv", ".efi", ".ax", ".mui",
    ".msi", ".msp", ".msix", ".appx", ".msixbundle", ".appxbundle",
    ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta",
    ".py", ".pyw", ".jar",
}
# Dateiformate, für die Windows eine Signaturprüfung kennt.
SIGNATUR_ENDUNGEN = {
    ".exe", ".dll", ".pyd", ".sys", ".scr", ".ocx", ".cpl", ".drv", ".efi", ".ax", ".mui",
    ".msi", ".msp", ".msix", ".appx", ".msixbundle", ".appxbundle",
    ".ps1", ".psm1", ".psd1", ".vbs", ".wsf",
}
_PE = {".exe", ".dll", ".pyd", ".sys", ".scr", ".ocx", ".cpl", ".drv", ".efi", ".ax"}

# Cloud-Platzhalter: Inhalt liegt nicht lokal, Lesen löst einen Download aus.
_NICHT_LOKAL = 0x00001000 | 0x00040000 | 0x00400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

SCHEMA = """
CREATE TABLE IF NOT EXISTS dateien (
    pfad TEXT PRIMARY KEY COLLATE NOCASE, laufwerk TEXT, groesse INTEGER, mtime REAL,
    code INTEGER DEFAULT 0, lauf INTEGER, erstmals INTEGER, geaendert INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_d_lauf ON dateien(lauf, code);
CREATE TABLE IF NOT EXISTS code (
    pfad TEXT PRIMARY KEY COLLATE NOCASE, groesse INTEGER, mtime REAL, sha256 TEXT,
    status TEXT, signierer TEXT, gruppe TEXT, lauf INTEGER
);
CREATE TABLE IF NOT EXISTS laeufe (
    id INTEGER PRIMARY KEY, start REAL, ende REAL, windows TEXT, updates TEXT,
    laufwerke TEXT, ausgelassen TEXT, dateien INTEGER DEFAULT 0, code INTEGER DEFAULT 0,
    neu INTEGER DEFAULT 0, geaendert INTEGER DEFAULT 0, entfernt INTEGER DEFAULT 0,
    fertig INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS system (
    lauf INTEGER, art TEXT, name TEXT, pfad TEXT, detail TEXT, status TEXT,
    signierer TEXT, ampel TEXT, grund TEXT, neu INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_s_lauf ON system(lauf, art);
CREATE TABLE IF NOT EXISTS luecken (
    lauf INTEGER, pfad TEXT COLLATE NOCASE, grund TEXT, PRIMARY KEY (lauf, pfad)
);
CREATE TABLE IF NOT EXISTS verweise (
    lauf INTEGER, pfad TEXT COLLATE NOCASE, art TEXT, ziel TEXT, PRIMARY KEY (lauf, pfad)
);
CREATE TABLE IF NOT EXISTS stroeme (
    lauf INTEGER, pfad TEXT COLLATE NOCASE, name TEXT, groesse INTEGER, inhalt TEXT,
    PRIMARY KEY (lauf, pfad, name)
);
CREATE TABLE IF NOT EXISTS volumen (
    lauf INTEGER, wurzel TEXT, daten TEXT, PRIMARY KEY (lauf, wurzel)
);
"""

# Spalten, die nach dem ersten Lauf dazukamen – an bestehende Datenbanken anbauen.
_NACHRUESTEN = [("code", "sha_vorher", "TEXT DEFAULT ''"), ("code", "veraendert_lauf", "INTEGER DEFAULT 0"),
                ("laeufe", "unzugaenglich", "TEXT DEFAULT '[]'"), ("dateien", "attribute", "INTEGER DEFAULT 0"),
                ("laeufe", "datentraeger", "TEXT DEFAULT '{}'")]

# Dateiattribute (st_file_attributes)
_VERSTECKT, _SYSTEM, _KOMPRIMIERT, _VERSCHLUESSELT = 0x2, 0x4, 0x800, 0x4000

# Gründe für nicht lesbare Ordner und Dateien (winerror)
_GRUENDE = {5: "Zugriff verweigert", 32: "von einem Programm gesperrt", 2: "während des Scans verschwunden",
            3: "während des Scans verschwunden", 21: "Gerät nicht bereit", 1920: "vom System gesperrt",
            1392: "beschädigt", 206: "Name zu lang", 123: "ungültiger Name", 1005: "Dateisystem nicht erkannt"}

# Zusätzliche Datenströme, die Windows und gängige Programme selbst anlegen.
_STROM_UEBLICH = {"zone.identifier", "smartscreen", "com.dropbox.attributes", "com.dropbox.attrs",
                  "oecustomproperty", "encryptable", "ms-properties", "afp_afpinfo", "afp_resource",
                  "{4c8cc155-6c1e-11d1-8e41-00c04fb9386d}", "$kernel.purge.esbcache",
                  "$kernel.purge.apprxcache", "$kernel.purge.cipcache", "$kernel.smartlocker.originclaim",
                  "favicon"}

# Windows legt in jedem Profil Verweise unter alten Ordnernamen an (Kompatibilität).
_ALTE_ORDNERNAMEN = {
    "application data", "cookies", "local settings", "my documents", "nethood", "printhood", "recent",
    "sendto", "start menu", "templates", "documents and settings", "all users", "default user",
    "my music", "my pictures", "my videos", "history", "temporary internet files", "anwendungsdaten",
    "druckumgebung", "netzwerkumgebung", "lokale einstellungen", "startmenü", "vorlagen", "eigene dateien",
    "eigene bilder", "eigene musik", "eigene videos", "verlauf", "programme", "desktop", "favoriten",
    "dokumente", "programs", "common files", "gemeinsame dateien", "dokumente und einstellungen"}

_ARTEN = [
    ("Programme und Bibliotheken", {".exe", ".dll", ".pyd", ".sys", ".scr", ".ocx", ".cpl", ".drv", ".efi",
                                    ".ax", ".com", ".mui"}),
    ("Skripte", {".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta",
                 ".py", ".pyw", ".jar", ".sh"}),
    ("Installationspakete", {".msi", ".msp", ".msix", ".appx", ".msixbundle", ".appxbundle", ".cab"}),
    ("Dokumente", {".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
                   ".ods", ".rtf", ".csv", ".epub", ".json", ".xml", ".html", ".htm"}),
    ("Bilder", {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".svg",
                ".heic", ".psd", ".raw"}),
    ("Videos", {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".flv"}),
    ("Audio", {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}),
    ("Archive und Abbilder", {".zip", ".7z", ".rar", ".tar", ".gz", ".xz", ".bz2", ".zst", ".iso", ".img",
                              ".vhd", ".vhdx", ".vmdk", ".wim", ".esd"}),
    ("KI-Modelle", {".gguf", ".safetensors", ".ckpt", ".pt", ".pth", ".onnx"}),
    ("Datenbanken", {".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".edb"}),
    ("Verknüpfungen", {".lnk", ".url"}),
]
_ART_JE_ENDUNG = {e: name for name, endungen in _ARTEN for e in endungen}

# Nicht gültig signierter Code wird in jedem Lauf neu gehasht – auch Skripte (Inhalt änderbar bei gleicher
# Größe und zurückgesetzter Zeit) und was beim letzten Mal nicht lesbar war.
_NEU_HASHEN = ("NotSigned", "HashMismatch", "NotTrusted", "Skript", "kein Zugriff", "nicht lesbar", "unbekannt")


# ---------------------------------------------------------------------------
# Umgebung: Laufwerke, Windows-Stand, ausgelassene Ordner
# ---------------------------------------------------------------------------

def volumes() -> tuple[list[str], list[str]]:
    """Alle lokalen Volumes (Festplatten, Wechseldatenträger, RAM-Disks, optische
    Laufwerke und eingebundene Abbilder mit Medium – keine Netzlaufwerke), auch
    ohne Laufwerksbuchstaben oder in einen Ordner eingehängt.
    Rückgabe: (lesbare Wurzeln, nicht zugängliche)."""
    if os.name != "nt":
        return ["/"], []
    from .datentraeger import LOKAL, volume_liste
    liste = volume_liste()
    if not liste:
        return _laufwerksbuchstaben(), []
    lesbar, gesperrt = [], []
    for v in liste:
        if v["art"] not in LOKAL:
            continue
        try:
            os.listdir(v["wurzel"])
            lesbar.append(v["wurzel"])
        except OSError as exc:
            if getattr(exc, "winerror", 0) != 21:          # 21 = kein Medium eingelegt
                gesperrt.append(v["wurzel"])
    lesbar.sort(key=lambda x: (x.startswith("\\\\"), x))
    return lesbar, gesperrt


def _laufwerksbuchstaben() -> list[str]:
    import ctypes
    k = ctypes.windll.kernel32
    maske = k.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26)
            if maske & (1 << i) and k.GetDriveTypeW(f"{chr(65 + i)}:\\") in (2, 3, 5, 6)]


def laufwerke() -> list[str]:
    return volumes()[0]


def volume_name(wurzel: str) -> str:
    """Bezeichnung eines Volumes („Daten“, „VTOYEFI“) – für den Bericht."""
    if os.name != "nt":
        return ""
    import ctypes
    puffer = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    if ctypes.windll.kernel32.GetVolumeInformationW(wurzel, puffer, 261, None, None, None, fs, 261):
        return f"{puffer.value or '(ohne Namen)'}, {fs.value}"
    return ""


def _dateisystem(wurzel: str) -> str:
    """NTFS, FAT32, exFAT … des Volumes, auf dem `wurzel` liegt."""
    if os.name != "nt":
        return ""
    import ctypes
    k = ctypes.windll.kernel32
    fs = ctypes.create_unicode_buffer(261)
    volume = ctypes.create_unicode_buffer(1024)
    pfad = wurzel if wurzel.endswith("\\") else wurzel + "\\"
    if not k.GetVolumePathNameW(pfad, volume, 1024):
        volume.value = pfad
    if k.GetVolumeInformationW(volume.value, None, 0, None, None, None, fs, 261):
        return fs.value
    return ""


def windows_stand() -> dict:
    """Build mit Revision (26200.9550), Version (25H2) und die zuletzt
    installierten Updates laut Protokoll."""
    stand = {"build": "", "version": "", "updates": [], "kbs": []}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def wert(n):
                try:
                    return winreg.QueryValueEx(k, n)[0]
                except OSError:
                    return ""
            stand["build"] = f"{wert('CurrentBuild')}.{wert('UBR')}".strip(".")
            stand["version"] = str(wert("DisplayVersion") or "")
    except Exception:
        pass
    try:
        from . import herkunft
        zeilen = herkunft.update_kontext(time.time(), vorher=45 * 86400, nachher=0)
    except Exception:
        zeilen = []
    stand["updates"] = zeilen[-6:]
    kbs: list[str] = []
    for z in reversed(zeilen):
        for kb in re.findall(r"KB\d{6,8}", z):
            if kb not in kbs:
                kbs.append(kb)
    stand["kbs"] = kbs
    return stand


def windows_text(stand: dict) -> str:
    teile = [f"Windows {stand.get('build') or '?'}"]
    if stand.get("version"):
        teile.append(stand["version"])
    if stand.get("kbs"):
        teile.append("zuletzt " + stand["kbs"][0])
    return " · ".join(teile)


def ausgelassene_ordner() -> list[str]:
    """Programmordner der im Windows-Sicherheitscenter eingetragenen Produkte."""
    try:
        import winreg
    except ImportError:
        return []
    basen = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData")]
    basen = [b for b in basen if b]
    ordner: set[str] = set()
    for art in ("Av", "Fw", "As"):
        wurzel = rf"SOFTWARE\Microsoft\Security Center\Provider\{art}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, wurzel) as k:
                i = 0
                while True:
                    try:
                        guid = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(k, guid) as p:
                            for feld in ("PRODUCTEXE", "REPORTINGEXE"):
                                try:
                                    wert = os.path.expandvars(str(winreg.QueryValueEx(p, feld)[0]))
                                except OSError:
                                    continue
                                ordner.update(_produktordner(wert, basen))
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(ordner)


def _produktordner(exe: str, basen: list[str]) -> set[str]:
    """Aus einem Programmpfad den Herstellerordner unter Programme/ProgramData."""
    if not re.match(r"^[A-Za-z]:\\", exe):
        return set()
    verz = os.path.dirname(exe)
    for b in basen:
        try:
            rel = os.path.relpath(verz, b)
        except ValueError:
            continue
        if not rel.startswith(".."):
            name = rel.split(os.sep)[0]
            raus = {os.path.join(x, name) for x in basen}
            raus |= {os.path.join(x, "Microsoft", name) for x in basen}
            return {o for o in raus if os.path.isdir(o)}
    return {verz}


def gruppe(pfad: str) -> str:
    """Herkunft einer Datei für die Zusammenfassung: Herstellerordner unter
    Programme, App-Ordner unter AppData, sonst die ersten zwei Ebenen."""
    teile = Path(pfad).parent.parts
    klein = [t.lower() for t in teile]
    for anker in ("program files", "program files (x86)", "programdata", "local", "roaming",
                  "locallow", "windowsapps"):
        if anker in klein:
            i = klein.index(anker)
            return str(Path(*teile[:i + 2]))
    return str(Path(*teile[:3])) if teile else pfad


# ---------------------------------------------------------------------------
# Lesen: lange Pfade, Datenströme, Download-Herkunft
# ---------------------------------------------------------------------------

_LANG = "\\\\?\\"


def _lang(pfad: str) -> str:
    """Lange Windows-Form (Präfix _LANG): auch Pfade über 260 Zeichen sind lesbar."""
    if os.name != "nt" or pfad.startswith("\\\\"):
        return pfad
    return _LANG + os.path.abspath(pfad)


def _kurz(pfad: str) -> str:
    if pfad.startswith(_LANG) and pfad[5:6] == ":":
        return pfad[4:]
    return pfad


def grund(exc: OSError) -> str:
    n = getattr(exc, "winerror", None) or 0
    return _GRUENDE.get(n, f"Fehler {n or exc.errno}")


def dateiart(endung: str) -> str:
    return _ART_JE_ENDUNG.get(endung, "Andere")


_STROM_API = None


def _strom_api():
    global _STROM_API
    if _STROM_API is None:
        import ctypes
        from ctypes import wintypes as w

        class Daten(ctypes.Structure):
            _fields_ = [("groesse", ctypes.c_longlong), ("name", ctypes.c_wchar * 296)]
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.FindFirstStreamW.restype = ctypes.c_void_p
        k.FindFirstStreamW.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k.FindNextStreamW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.FindClose.argtypes = [ctypes.c_void_p]
        _STROM_API = (k, Daten, ctypes.c_void_p(-1).value)
    return _STROM_API


def stroeme(pfad: str) -> list[tuple[str, int]]:
    """Zusätzliche Datenströme (ADS) einer Datei oder eines Ordners: [(name, groesse)],
    ohne den Hauptinhalt. Leer, wenn es keine gibt oder das Dateisystem keine kennt."""
    if os.name != "nt":
        return []
    import ctypes
    k, Daten, ungueltig = _strom_api()
    d = Daten()
    h = k.FindFirstStreamW(_lang(pfad), 0, ctypes.byref(d), 0)
    if not h or h == ungueltig:
        return []
    raus = []
    try:
        while True:
            teile = d.name.split(":")                       # ":name:$DATA"
            if len(teile) == 3 and teile[1] and teile[2] == "$DATA":
                raus.append((teile[1], int(d.groesse)))
            if not k.FindNextStreamW(h, ctypes.byref(d)):
                break
    finally:
        k.FindClose(h)
    return raus


def _strom_kopf(pfad: str, name: str) -> bytes:
    try:
        with open(_lang(pfad) + ":" + name, "rb") as f:
            return f.read(2)
    except OSError:
        return b""


def download_herkunft(pfad: str) -> str:
    """Zone.Identifier einer Datei in Kurzform: „Internet · <Adresse> · über <Seite>“."""
    try:
        with open(_lang(pfad) + ":Zone.Identifier", "r", encoding="utf-8", errors="replace") as f:
            text = f.read(4096)
    except OSError:
        return ""
    werte = {k.strip().lower(): v.strip() for k, v in
             (z.split("=", 1) for z in text.splitlines() if "=" in z)}
    zone = werte.get("zoneid", "")
    teile = [{"0": "dieser Rechner", "1": "Intranet", "2": "vertrauenswürdige Seite", "3": "Internet",
              "4": "gesperrte Seite"}.get(zone, f"Zone {zone or '?'}")]
    if werte.get("hosturl"):
        teile.append(werte["hosturl"][:300])
    if werte.get("referrerurl"):
        teile.append("über " + werte["referrerurl"][:300])
    return " · ".join(teile)


# ---------------------------------------------------------------------------
# Zustand für die Anzeige (vollscan.json)
# ---------------------------------------------------------------------------

def stand_lesen() -> dict:
    try:
        return json.loads(STAND.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _stand_schreiben(z: dict) -> None:
    """Fortschritt schreiben. Liest die Oberfläche gerade (Windows: Datei offen → PermissionError),
    kurz wiederholen und notfalls auslassen – ein Lesezugriff darf keinen Vollscan abbrechen."""
    tmp = STAND.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(z, ensure_ascii=False), encoding="utf-8")
        for versuch in range(5):
            try:
                os.replace(tmp, STAND)
                return
            except PermissionError:
                time.sleep(0.05 * (versuch + 1))
    except OSError:
        pass
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def laeuft() -> dict | None:
    z = stand_lesen()
    pid = int(z.get("pid") or 0)
    if not pid or z.get("phase") in ("fertig", "abgebrochen", "fehler"):
        return None
    try:
        import psutil
        p = psutil.Process(pid)
        name = p.name().lower()
        if p.is_running() and ("python" in name or "nemicli" in name):
            return z
    except Exception:
        pass
    return None


def anzeige(z: dict) -> str:
    """Eine Zeile für die Oberfläche; „i/n“ darin zeichnet den Balken."""
    phase = z.get("phase", "")
    dauer = int(time.time() - z.get("start", time.time()))
    uhr = f"{dauer // 3600}:{dauer % 3600 // 60:02d}:{dauer % 60:02d}"
    vorn = "Fortsetzung · " if z.get("fortsetzung") else ""
    if phase == "liste" and not z.get("laufwerk"):
        return f"{vorn}Stufe 1 (alle Dateien) startet …"
    if phase == "liste":
        gb = z.get("bytes", 0) / 1e9
        gesamt = max(z.get("belegt", 0) / 1e9, 1)
        return (f"{vorn}Stufe 1 (alle Dateien) · {_wo(z)} · "
                f"{min(gb, gesamt * 0.99):.0f}/{gesamt:.0f} GB · "
                f"{_zahl(z.get('dateien', 0))} Dateien · {uhr}")
    if phase == "code":
        return (f"{vorn}Stufe 2 (Fingerabdruck) · {z.get('code_fertig', 0)}/{z.get('code_gesamt', 0)} "
                f"Code-Dateien · {uhr}")
    if phase == "system":
        return f"{vorn}Stufe 3 (Autostart, Dienste, Aufgaben, Treiber, Netz) · {z.get('teil', '')} · {uhr}"
    if phase == "fertig":
        return f"fertig · Bericht: {z.get('bericht', '')}"
    return phase or "unbekannt"


def _wo(z: dict) -> str:
    """Aktuelles Laufwerk samt Position, z. B. „C: (Laufwerk 1 von 4)“ – die Laufwerke kommen nacheinander."""
    w = z.get("laufwerk", "")
    if w.startswith(_LANG + "Volume{"):
        w = "Volume " + w[len(_LANG + "Volume{"):][:8]
    if z.get("anzahl", 0) > 1:
        w += f" (Laufwerk {z.get('nr', 1)} von {z['anzahl']})"
    return w


def kurzanzeige(z: dict) -> str:
    """Für die Statuszeile: kurz, mit genau einem „i/n“ für den Mini-Balken."""
    if z.get("phase") == "liste":
        gesamt = max(z.get("belegt", 0) / 1e9, 1)
        gb = min(z.get("bytes", 0) / 1e9, gesamt * 0.99)
        return f"Vollscan {_wo(z)} {gb:.0f}/{gesamt:.0f} GB"
    if z.get("phase") == "code":
        return f"Vollscan Fingerabdruck {z.get('code_fertig', 0)}/{z.get('code_gesamt', 0)}"
    if z.get("phase") == "system":
        return f"Vollscan Systemstand: {z.get('teil', '')}"
    return ""


# ---------------------------------------------------------------------------
# Der Lauf
# ---------------------------------------------------------------------------

def _hintergrundmodus() -> None:
    """Windows-Hintergrundmodus: niedrige CPU-, E/A- und Speicherpriorität."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00100000)   # PROCESS_MODE_BACKGROUND_BEGIN
    except Exception:
        pass


def _verbinden() -> sqlite3.Connection:
    ordner_anlegen()
    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    for tabelle, spalte, typ in _NACHRUESTEN:
        da = {r[1] for r in c.execute(f"PRAGMA table_info({tabelle})")}
        if spalte not in da:
            c.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
    c.commit()
    return c


class Lauf:
    def __init__(self, c: sqlite3.Connection, wurzeln: list[str] | None = None):
        self.c = c
        from .herkunft import _SCHUTZ_ERSATZ              # feste Einträge, falls das Sicherheitscenter schweigt
        self.ausgelassen_pfade = ausgelassene_ordner()
        bekannt = {os.path.normcase(o) for o in self.ausgelassen_pfade}
        self.ausgelassen_pfade += [o for o in (os.path.expandvars(x) for x in _SCHUTZ_ERSATZ)
                                   if "%" not in o and os.path.isdir(o) and os.path.normcase(o) not in bekannt]
        if wurzeln is None:
            self.wurzeln, self.unzugaenglich = volumes()
            # Volumes, die ein Sicherheitscenter-Produkt angelegt hat (Bezeichnung
            # beginnt mit dessen Herstellerordner), werden wie dessen Ordner ausgelassen.
            hersteller = {os.path.basename(o).lower() for o in self.ausgelassen_pfade
                          if len(os.path.basename(o)) >= 4}
            fremd = [w for w in self.wurzeln
                     if any(volume_name(w).lower().startswith(h) for h in hersteller)]
            self.wurzeln = [w for w in self.wurzeln if w not in fremd]
            self.ausgelassen_pfade += fremd
        else:
            self.wurzeln, self.unzugaenglich = wurzeln, []
        self.ausgelassen = [os.path.normcase(o) for o in self.ausgelassen_pfade]
        self.z: dict = {}
        self._zuletzt = 0.0

    # -- Zustand ---------------------------------------------------------------
    def _melden(self, sofort: bool = False, **felder) -> None:
        self.z.update(felder)
        jetzt = time.time()
        if sofort or jetzt - self._zuletzt > 2:
            self._zuletzt = jetzt
            _stand_schreiben(self.z)

    def _stopp(self) -> bool:
        return STOPP.exists()

    # -- Ablauf ----------------------------------------------------------------
    def starten(self) -> int:
        offen = self.c.execute("SELECT id, laufwerke FROM laeufe WHERE fertig=0 "
                               "ORDER BY id DESC LIMIT 1").fetchone()
        if offen:
            lid = offen[0]
            erledigt = json.loads(offen[1] or "[]")
        else:
            stand = windows_stand()
            try:
                from .datentraeger import uebersicht
                platten = uebersicht()
            except Exception as exc:
                platten = {"fehler": repr(exc)[:200]}
            lid = self.c.execute(
                "INSERT INTO laeufe(start, windows, updates, laufwerke, ausgelassen, unzugaenglich, "
                "datentraeger) VALUES (?,?,?,?,?,?,?)",
                (time.time(), json.dumps(stand, ensure_ascii=False), "\n".join(stand["updates"]),
                 "[]", json.dumps(self.ausgelassen_pfade), json.dumps(self.unzugaenglich),
                 json.dumps(platten, ensure_ascii=False))).lastrowid
            erledigt = []
            self.c.commit()
        self.lid = lid
        self.z = {"pid": os.getpid(), "lauf": lid, "start": time.time(), "phase": "liste",
                  "laufwerk": "", "dateien": 0, "bytes": 0, "belegt": 0, "fehler": 0,
                  "fortsetzung": bool(offen)}
        if offen:
            # Stand der bereits erfassten Laufwerke übernehmen, statt bei 0 zu zählen.
            self.z["dateien"] = self.c.execute(
                "SELECT COUNT(*) FROM dateien WHERE lauf=?", (lid,)).fetchone()[0]
            if all(w in erledigt for w in self.wurzeln):
                self.z["phase"] = "code"
        self._melden(sofort=True)

        for nr, w in enumerate(self.wurzeln, 1):
            if w in erledigt:
                continue
            self.z.update(nr=nr, anzahl=len(self.wurzeln))
            if not self._laufwerk(w):
                self._melden(sofort=True, phase="abgebrochen")
                return 2
            erledigt.append(w)
            self.c.execute("UPDATE laeufe SET laufwerke=? WHERE id=?", (json.dumps(erledigt), lid))
            self.c.commit()

        if not self._fingerabdruecke():
            self._melden(sofort=True, phase="abgebrochen")
            return 2
        self._systemstand()
        bericht = self._abschliessen()
        self._melden(sofort=True, phase="fertig", bericht=str(bericht), ende=time.time())
        return 0

    def _ausgelassen(self, pfad: str) -> bool:
        n = os.path.normcase(pfad)
        return any(n == o or n.startswith(o + os.sep) for o in self.ausgelassen)

    def _laufwerk(self, wurzel: str) -> bool:
        """Stufe 1 für ein Laufwerk: jeden Verzeichniseintrag erfassen."""
        import shutil
        try:
            belegt = shutil.disk_usage(wurzel).used
        except OSError:
            belegt = 0
        self._melden(sofort=True, laufwerk=wurzel, belegt=belegt, bytes=0)
        fs = _dateisystem(wurzel)
        mit_stroemen = fs in ("NTFS", "ReFS")
        s = {"dateisystem": fs, "ordner": 0, "dateien": 0, "bytes": 0, "versteckt": 0, "system": 0,
             "komprimiert": 0, "verschluesselt": 0, "cloud": 0, "code": 0, "verweise": 0,
             "stroeme": 0, "stroeme_ueblich": 0, "luecken": 0, "arten": {}, "ebene1": {}, "ebene2": {}}
        stapel = [_lang(wurzel)]
        puffer: list[tuple] = []
        self._luecken: list[tuple] = []
        self._verweise: list[tuple] = []
        self._stroeme: list[tuple] = []
        gesehen = 0
        while stapel:
            if gesehen % 200 == 0:
                if self._stopp():
                    self._schreiben(puffer)
                    return False
                self._melden()
            verz = stapel.pop()
            try:
                with os.scandir(verz) as it:
                    eintraege = list(it)
            except OSError as exc:
                self._luecke(verz, exc, s)
                continue
            s["ordner"] += 1
            if mit_stroemen:
                self._ads(verz, False, s)
            for e in eintraege:
                try:
                    if e.is_symlink() or e.is_junction():
                        self._verweis(e, s)
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if not self._ausgelassen(_kurz(e.path)):
                            stapel.append(e.path)
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError as exc:
                    self._luecke(e.path, exc, s)
                    continue
                pfad = _kurz(e.path)
                endung = os.path.splitext(e.name)[1].lower()
                attr = getattr(st, "st_file_attributes", 0)
                lokal = not (attr & _NICHT_LOKAL)
                code = 1 if endung in CODE_ENDUNGEN and lokal else 0
                puffer.append((pfad, wurzel, st.st_size, st.st_mtime, code, self.lid, self.lid, attr))
                self._zaehlen(s, pfad, wurzel, endung, st.st_size, attr, lokal, code)
                if mit_stroemen and lokal:
                    self._ads(e.path, bool(code), s)
                self.z["dateien"] += 1
                self.z["bytes"] += st.st_size
            if len(puffer) >= 5000:
                self._schreiben(puffer)
                puffer = []
            gesehen += 1
        self._schreiben(puffer)
        for name in ("ebene1", "ebene2"):
            s[name] = dict(sorted(s[name].items(), key=lambda p: -p[1])[:15])
        self.c.execute("INSERT OR REPLACE INTO volumen VALUES (?,?,?)",
                       (self.lid, wurzel, json.dumps(s, ensure_ascii=False)))
        self.c.commit()
        return True

    def _zaehlen(self, s: dict, pfad: str, wurzel: str, endung: str, groesse: int, attr: int,
                 lokal: bool, code: int) -> None:
        s["dateien"] += 1
        s["bytes"] += groesse
        s["versteckt"] += bool(attr & _VERSTECKT)
        s["system"] += bool(attr & _SYSTEM)
        s["komprimiert"] += bool(attr & _KOMPRIMIERT)
        s["verschluesselt"] += bool(attr & _VERSCHLUESSELT)
        s["cloud"] += not lokal
        s["code"] += code
        art = s["arten"].setdefault(dateiart(endung), [0, 0])
        art[0] += 1
        art[1] += groesse
        teile = pfad[len(wurzel):].strip("\\/").replace("/", "\\").split("\\")
        e1 = teile[0] if len(teile) > 1 else "(Dateien direkt im Wurzelordner)"
        s["ebene1"][e1] = s["ebene1"].get(e1, 0) + groesse
        if len(teile) > 2:
            e2 = teile[0] + "\\" + teile[1]
            s["ebene2"][e2] = s["ebene2"].get(e2, 0) + groesse

    def _luecke(self, pfad: str, exc: OSError, s: dict) -> None:
        self.z["fehler"] += 1
        s["luecken"] += 1
        self._luecken.append((self.lid, _kurz(pfad), grund(exc)))
        if len(self._luecken) >= 2000:
            self._nebentabellen()

    def _verweis(self, e, s: dict) -> None:
        s["verweise"] += 1
        try:
            ziel = _kurz(os.readlink(e.path))
        except OSError:
            ziel = ""
        art = "Abzweigung (Junction)" if e.is_junction() else "symbolischer Link"
        self._verweise.append((self.lid, _kurz(e.path), art, ziel))
        if len(self._verweise) >= 2000:
            self._nebentabellen()

    def _ads(self, pfad: str, code: bool, s: dict) -> None:
        for name, groesse in stroeme(pfad):
            klein = name.lower()
            if klein in _STROM_UEBLICH:
                s["stroeme_ueblich"] += 1
                if klein == "zone.identifier" and code:
                    self._stroeme.append((self.lid, _kurz(pfad), name, groesse, download_herkunft(pfad)))
                continue
            s["stroeme"] += 1
            kopf = _strom_kopf(pfad, name)
            self._stroeme.append((self.lid, _kurz(pfad), name, groesse,
                                  "Programmcode (MZ)" if kopf == b"MZ" else ""))
        if len(self._stroeme) >= 2000:
            self._nebentabellen()

    def _nebentabellen(self) -> None:
        if self._luecken:
            self.c.executemany("INSERT OR REPLACE INTO luecken VALUES (?,?,?)", self._luecken)
        if self._verweise:
            self.c.executemany("INSERT OR REPLACE INTO verweise VALUES (?,?,?,?)", self._verweise)
        if self._stroeme:
            self.c.executemany("INSERT OR REPLACE INTO stroeme VALUES (?,?,?,?,?)", self._stroeme)
        self._luecken, self._verweise, self._stroeme = [], [], []

    def _schreiben(self, puffer: list[tuple]) -> None:
        self._nebentabellen()
        if puffer:
            self.c.executemany(
                "INSERT INTO dateien(pfad, laufwerk, groesse, mtime, code, lauf, erstmals, attribute) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(pfad) DO UPDATE SET "
                # Zweiter Besuch im selben Lauf (Fortsetzen): Merkmal behalten.
                "geaendert = CASE WHEN lauf = excluded.lauf THEN geaendert "
                "ELSE (groesse != excluded.groesse OR mtime != excluded.mtime) END, "
                "groesse = excluded.groesse, mtime = excluded.mtime, code = excluded.code, "
                "laufwerk = excluded.laufwerk, lauf = excluded.lauf, attribute = excluded.attribute", puffer)
        self.c.commit()

    def _offene_code(self) -> list[tuple]:
        return self.c.execute(
            "SELECT d.pfad, d.groesse, d.mtime FROM dateien d LEFT JOIN code c ON c.pfad = d.pfad "
            "WHERE d.lauf = ? AND d.code = 1 AND "
            "(c.pfad IS NULL OR c.groesse != d.groesse OR c.mtime != d.mtime "
            f" OR (c.lauf < ? AND c.status IN {_NEU_HASHEN}))",
            (self.lid, self.lid)).fetchall()

    def _fingerabdruecke(self) -> bool:
        """Stufe 2: Hash und Signatur für neuen oder geänderten Code."""
        from . import herkunft
        offen = self._offene_code()
        schon = self.c.execute("SELECT COUNT(*) FROM code WHERE lauf = ?", (self.lid,)).fetchone()[0]
        self._melden(sofort=True, phase="code", code_gesamt=len(offen) + schon, code_fertig=schon)
        puffer: list[tuple] = []
        for i, (pfad, groesse, mtime) in enumerate(offen):
            if i % 100 == 0:
                if self._stopp():
                    self._code_schreiben(puffer)
                    return False
                self._melden()
            sha, status, signierer = _fingerabdruck(pfad, herkunft)
            puffer.append((pfad, groesse, mtime, sha, status, signierer, gruppe(pfad), self.lid))
            self.z["code_fertig"] += 1
            if len(puffer) >= 500:
                self._code_schreiben(puffer)
                puffer = []
        self._code_schreiben(puffer)
        return True

    def _code_schreiben(self, puffer: list[tuple]) -> None:
        if not puffer:
            return
        geaendert = ("code.sha256 != '' AND excluded.sha256 != '' "
                     "AND code.sha256 != excluded.sha256")
        self.c.executemany(
            "INSERT INTO code(pfad, groesse, mtime, sha256, status, signierer, gruppe, lauf) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(pfad) DO UPDATE SET "
            f"sha_vorher = CASE WHEN {geaendert} THEN code.sha256 ELSE code.sha_vorher END, "
            f"veraendert_lauf = CASE WHEN {geaendert} THEN excluded.lauf ELSE code.veraendert_lauf END, "
            "groesse = excluded.groesse, mtime = excluded.mtime, sha256 = excluded.sha256, "
            "status = excluded.status, signierer = excluded.signierer, gruppe = excluded.gruppe, "
            "lauf = excluded.lauf", puffer)
        self.c.commit()

    def _systemstand(self) -> None:
        """Stufe 3: Autostart, Dienste, Aufgaben, Treiber, Netz – mit Ampel."""
        from . import herkunft, systemstand
        self._melden(sofort=True, phase="system", teil="")

        def signatur(pfad: str) -> tuple[str, str]:
            k = self.c.execute("SELECT status, signierer FROM code WHERE pfad=?", (pfad,)).fetchone()
            if k:
                return k[0], k[1] or ""
            s = herkunft.signatur(pfad)
            return s.get("status", "unbekannt"), s.get("signierer", "")

        eintraege, fehler = systemstand.sammeln(signatur, lambda t: self._melden(sofort=True, teil=t))
        vorher_lauf = self.c.execute("SELECT MAX(lauf) FROM system WHERE lauf < ?", (self.lid,)).fetchone()[0]
        schluessel = system_schluessel
        bekannt = ({schluessel(a, n, p) for a, n, p in self.c.execute(
            "SELECT art, name, pfad FROM system WHERE lauf=?", (vorher_lauf,))} if vorher_lauf else None)
        zeilen = []
        for e in eintraege:
            e["neu"] = 1 if bekannt is not None and schluessel(e["art"], e["name"], e["pfad"]) not in bekannt else 0
            e["ampel"], e["grund"] = systemstand.ampel(e)
            zeilen.append((self.lid, e["art"], e["name"], e["pfad"], e["detail"], e["status"],
                           e["signierer"], e["ampel"], e["grund"], e["neu"]))
        self.c.execute("DELETE FROM system WHERE lauf=?", (self.lid,))
        self.c.executemany("INSERT INTO system VALUES (?,?,?,?,?,?,?,?,?,?)", zeilen)
        self.c.commit()
        self.z["system_fehler"] = fehler
        try:
            from . import zugang
            self.z["alarme"] = funde_als_alarme(zugang.speicher(), eintraege)
        except Exception as exc:                 # der Bericht entsteht trotzdem
            fehler.append(f"Funde als Alarme: {exc!r}"[:200])

    def _abschliessen(self) -> Path:
        """Zählen, Verschwundenes austragen, Bericht schreiben."""
        c, lid = self.c, self.lid
        vorher = c.execute("SELECT COUNT(*) FROM laeufe WHERE fertig=1").fetchone()[0]
        neu = c.execute("SELECT COUNT(*) FROM dateien WHERE lauf=? AND erstmals=?", (lid, lid)).fetchone()[0]
        geaendert = c.execute("SELECT COUNT(*) FROM dateien WHERE lauf=? AND erstmals<? AND geaendert=1",
                              (lid, lid)).fetchone()[0]
        # Nur auf gescannten Laufwerken austragen – ein abgezogener Stick bleibt erhalten.
        platz = ",".join("?" * len(self.wurzeln))
        entfernt = c.execute(f"DELETE FROM dateien WHERE lauf<? AND laufwerk IN ({platz})",
                             (lid, *self.wurzeln)).rowcount
        c.execute("DELETE FROM code WHERE pfad NOT IN (SELECT pfad FROM dateien)")
        for tabelle in ("system", "luecken", "verweise", "stroeme", "volumen"):
            c.execute(f"DELETE FROM {tabelle} WHERE lauf < ?", (lid - 5,))   # die letzten Läufe genügen
        dateien = c.execute("SELECT COUNT(*) FROM dateien WHERE lauf=?", (lid,)).fetchone()[0]
        code = c.execute("SELECT COUNT(*) FROM dateien WHERE lauf=? AND code=1", (lid,)).fetchone()[0]
        c.execute("UPDATE laeufe SET ende=?, dateien=?, code=?, neu=?, geaendert=?, entfernt=?, fertig=1 "
                  "WHERE id=?", (time.time(), dateien, code, neu, geaendert, entfernt, lid))
        c.commit()
        return _bericht(c, lid, erster=vorher == 0, fehler=self.z.get("system_fehler") or [])


def _fingerabdruck(pfad: str, herkunft) -> tuple[str, str, str]:
    """(sha256, status, signierer)."""
    sha = hashlib.sha256()
    try:
        f = open(_lang(pfad), "rb", buffering=0)
    except PermissionError:
        return "", "kein Zugriff", ""
    except OSError:
        return "", "nicht lesbar", ""
    try:
        with f:
            while True:
                block = f.read(1 << 20)
                if not block:
                    break
                sha.update(block)
    except OSError:
        return "", "nicht lesbar", ""
    endung = os.path.splitext(pfad)[1].lower()
    if endung not in SIGNATUR_ENDUNGEN:
        return sha.hexdigest(), "Skript", ""
    s = herkunft.signatur(pfad)
    return sha.hexdigest(), s.get("status", "unbekannt"), s.get("signierer", "")


def _zahl(n: int) -> str:
    return f"{n:,}".replace(",", ".")


_ART_TITEL = [("autostart", "Autostart"), ("dienst", "Dienste"), ("aufgabe", "Geplante Aufgaben"),
              ("treiber", "Geladene Treiber"), ("port", "Offene Ports"),
              ("verbindung", "Verbindungen ins Internet (je Programm)"), ("hosts", "hosts-Datei"),
              ("dns", "DNS-Server von Hand gesetzt"), ("proxy", "Proxy")]


VS_REGEL = "VS-001"
_ART_NAME = {"autostart": "Autostart", "dienst": "Dienst", "aufgabe": "Geplante Aufgabe", "treiber": "Treiber",
             "port": "Offener Port", "netz": "Verbindung", "hosts": "hosts-Eintrag", "dns": "DNS", "proxy": "Proxy"}


def funde_als_alarme(speicher, eintraege: list[dict]) -> int:
    """Rote Einträge des Systemstands werden einmal je Fund ein Alarm (Vorbild: Hayabusa/Chainsaw liefern
    Treffer statt Listen) – so bekommen sie ein Urteil und wiederholen sich nicht bei jedem Lauf. Was
    schon einmal gemeldet wurde (egal mit welchem Urteil), kommt nicht noch einmal."""
    from .ereignisse import Alarm, Schwere
    from .systemstand import ROT
    neu = []
    for e in eintraege:
        if e.get("ampel") != ROT:
            continue
        art, name, pfad = system_schluessel(e["art"], e["name"], e.get("pfad") or "")
        subjekt = f"{_ART_NAME.get(art, art)} · {name} · {pfad}"[:300]
        if speicher.urteile_zu(VS_REGEL, subjekt):
            continue
        text = (f"{_ART_NAME.get(e['art'], e['art'])} „{e['name']}“ – {e.get('pfad') or 'ohne Pfad'}\n"
                f"Ampel rot: {e.get('grund', '')}\n"
                f"Signatur: {e.get('status') or '?'}" + (f" ({e['signierer']})" if e.get("signierer") else "")
                + (f"\nDetail: {str(e.get('detail'))[:300]}" if e.get("detail") else ""))
        neu.append(Alarm(titel=f"Vollscan: {_ART_NAME.get(e['art'], e['art'])} rot", text=text,
                         schwere=Schwere.MITTEL, regel=VS_REGEL, quelle="vollscan", subjekt=subjekt))
    if neu:
        speicher.alarme_schreiben(neu)
    return len(neu)


def system_schluessel(art: str, name: str, pfad: str) -> tuple:
    """Vergleich mit dem vorigen Lauf. Dienste je Anmeldung (CDPUserSvc_dbf95 …) tragen bei jeder Anmeldung
    eine neue Endung; verglichen wird ohne sie – der Pfad zählt weiter mit."""
    from .inventar import dienstname
    return art, (dienstname(name) if art == "dienst" else name).lower(), (pfad or "").lower()


def _systemteil(c: sqlite3.Connection, lid: int) -> list[str]:
    from .systemstand import GELB, GELB_AB, GRUEN, PUNKTE, ROT, ROT_AB
    zeilen = c.execute("SELECT art, name, pfad, detail, status, signierer, ampel, grund, neu "
                       "FROM system WHERE lauf=?", (lid,)).fetchall()
    if not zeilen:
        return []
    zaehl = {f: sum(1 for r in zeilen if r[6] == f) for f in (ROT, GELB, GRUEN)}
    z = ["", "## Systemstand mit Ampel", "",
         f"🔴 anschauen: {zaehl[ROT]} · 🟡 beobachten: {zaehl[GELB]} · 🟢 unauffällig: {zaehl[GRUEN]}", "",
         f"Vorsortierung nach Punkten, kein Urteil – alle zutreffenden Gründe zählen zusammen, ab {ROT_AB} rot, "
         f"ab {GELB_AB} gelb:", ""]
    z += [f"- {'+' if p > 0 else '−'}{abs(p)}: {grund}" for p, grund in
          sorted(PUNKTE.values(), key=lambda x: -x[0])]
    symbol = {ROT: "🔴", GELB: "🟡", GRUEN: "🟢"}
    for art, titel in _ART_TITEL:
        teil = [r for r in zeilen if r[0] == art]
        if not teil:
            continue
        gruen = sum(1 for r in teil if r[6] == GRUEN)
        z += ["", f"### {titel} ({len(teil)}, davon {gruen} grün)", ""]
        # Grüne nur bei kleinen Listen einzeln – sonst ginge das Wichtige unter.
        zeigen = [r for r in teil if r[6] != GRUEN or len(teil) <= 15]
        zeigen.sort(key=lambda r: ({ROT: 0, GELB: 1}.get(r[6], 2), r[1].lower()))
        for _, name, pfad, detail, status, signierer, farbe, grund, neu in zeigen[:150]:
            sig = f"{status}{': ' + signierer if signierer else ''}" if status else ""
            teile = [x for x in (pfad, sig, detail if art not in ("autostart", "aufgabe", "dienst")
                                 else detail.split(" · ")[0]) if x]
            z.append(f"- {symbol.get(farbe, '')} **{name}**{' (neu)' if neu else ''} – "
                     + " · ".join(teile) + (f"  _({grund})_" if farbe != GRUEN else ""))
        if len(zeigen) > 150:
            z.append(f"- … {len(zeigen) - 150} weitere")
    return z


_NAMEN_OHNE_AUSSAGE = {"basic data partition", "microsoft reserved partition", "efi system partition",
                       "eps", "msr"}


def _gr(n: float) -> str:
    from .datentraeger import groesse_text
    return groesse_text(n)


def _datentraegerteil(d: dict, gescannt: set, gesperrt: set, ausgelassen: set) -> list[str]:
    """Festplatte → Partition → Volume, mit Scan-Stand je Volume."""
    if not d:
        return []
    if d.get("fehler") or not d.get("platten"):
        return ["", "## Datenträger", "", f"- nicht ermittelt: {d.get('fehler') or 'keine Angaben'}"]
    from .datentraeger import ART_TEXT
    vols = {v["wurzel"]: v for v in d.get("volumes", [])}

    def volume(w: str) -> str:
        v = vols.get(w) or {}
        teile = [f"„{v.get('name') or 'ohne Namen'}“", v.get("dateisystem") or "kein Dateisystem"]
        if v.get("groesse"):
            teile.append(f"{_gr(v['groesse'])}, frei {_gr(v.get('frei', 0))}")
        weitere = [p for p in v.get("pfade", []) if p != w]
        if weitere:
            teile.append("auch unter " + ", ".join(weitere))
        if w in gescannt:
            stand = "gescannt"
        elif w in gesperrt:
            stand = "von Windows gesperrt (Systembereich, Revier des Virenschutzes)"
        elif w in ausgelassen:
            stand = "bewusst nicht betreten (Produkt im Windows-Sicherheitscenter)"
        else:
            stand = "nicht gescannt" + (f" ({v['grund']})" if v.get("grund") else "")
        return f"{w} ({', '.join(teile)}) – {stand}"

    z = ["", "## Datenträger", "", "Jede Festplatte mit ihren Partitionen; „→“ zeigt das Volume darauf.", ""]
    zugeordnet = set()
    for p in d["platten"]:
        art = {True: "SSD", False: "HDD"}.get(p.get("ssd"), "")
        kopf = [p.get("modell") or "unbekanntes Modell", p.get("anschluss", ""), art,
                _gr(p["groesse"]) if p.get("groesse") else "", p.get("stil", ""),
                "wechselbar" if p.get("wechselbar") else ""]
        z += [f"### Datenträger {p['nummer']} · {' · '.join(x for x in kopf if x)}", ""]
        if not p.get("partitionen"):
            z.append("- keine Partitionen")
        for t in p.get("partitionen", []):
            name = f" „{t['name']}“" if t.get("name") and t["name"].lower() not in _NAMEN_OHNE_AUSSAGE else ""
            ziel = f" → {volume(t['volume'])}" if t.get("volume") else " – kein Volume (nicht einhängbar)"
            z.append(f"- Partition {t['nummer']} · {t['art']}{name} · {_gr(t['groesse'])}{ziel}")
            zugeordnet.add(t.get("volume"))
        z.append("")
    rest = [w for w in vols if w not in zugeordnet]
    if rest:
        z += ["### Volumes ohne zugeordnete Partition", ""]
        z += [f"- {volume(w)} · {ART_TEXT.get(vols[w].get('art'), 'andere Art')}" for w in rest]
    return z


def _laufwerkteil(w: str, n: int, s: dict | None) -> list[str]:
    name = volume_name(w)
    if not s:
        return [f"- {w}{' (' + name + ')' if name else ''}: {_zahl(n)} Dateien"]
    z = [f"### {w}{' (' + name + ')' if name else ''}", "",
         f"- {_zahl(s['dateien'])} Dateien in {_zahl(s['ordner'])} Ordnern · {_gr(s['bytes'])}",
         f"- Code und Skripte: {_zahl(s['code'])} · versteckt: {_zahl(s['versteckt'])} · "
         f"Systemdateien: {_zahl(s['system'])} · komprimiert: {_zahl(s['komprimiert'])} · "
         f"verschlüsselt (EFS): {_zahl(s['verschluesselt'])} · nur in der Cloud: {_zahl(s['cloud'])}",
         f"- Verweise (nicht betreten): {_zahl(s['verweise'])} · zusätzliche Datenströme: "
         + (f"{_zahl(s['stroeme'])} ungewöhnlich, {_zahl(s['stroeme_ueblich'])} üblich"
            if s.get("dateisystem") in ("NTFS", "ReFS") else f"– ({s.get('dateisystem') or '?'} kennt keine)")
         + f" · nicht lesbar: {_zahl(s['luecken'])}"]
    arten = sorted(s.get("arten", {}).items(), key=lambda p: -p[1][1])
    if arten:
        z.append("- Dateiarten: " + " · ".join(f"{a} {_zahl(x[0])} ({_gr(x[1])})" for a, x in arten))
    if s.get("ebene1"):
        z.append("- Größte Ordner: " + " · ".join(f"{o} {_gr(b)}" for o, b in list(s["ebene1"].items())[:10]))
    if s.get("ebene2"):
        z.append("- Größte Unterordner: " + " · ".join(f"{o} {_gr(b)}" for o, b in list(s["ebene2"].items())[:10]))
    return z + [""]


def _versteckt_teil(c: sqlite3.Connection, lid: int) -> list[str]:
    windows = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")) + os.sep
    zeilen = [(p, s) for p, s in c.execute(
        "SELECT d.pfad, c.status FROM dateien d LEFT JOIN code c ON c.pfad = d.pfad "
        "WHERE d.lauf=? AND d.code=1 AND (d.attribute & ?) != 0 ORDER BY d.pfad",
        (lid, _VERSTECKT)) if not os.path.normcase(p).startswith(windows)]
    z = ["", f"## Versteckte Programme und Skripte außerhalb des Windows-Ordners ({len(zeilen)})", ""]
    z += [f"- {'🟢' if s == 'Valid' else '🟡'} {p} ({s or 'ohne Prüfung'})" for p, s in
          sorted(zeilen, key=lambda r: (r[1] == "Valid", r[0]))[:150]] or ["- keine"]
    if len(zeilen) > 150:
        z.append(f"- … {len(zeilen) - 150} weitere")
    return z


def _download_teil(c: sqlite3.Connection, lid: int) -> list[str]:
    zeilen = c.execute(
        "SELECT s.pfad, s.inhalt, c.status, c.signierer FROM stroeme s LEFT JOIN code c ON c.pfad = s.pfad "
        "WHERE s.lauf=? AND lower(s.name)='zone.identifier' ORDER BY (c.status = 'Valid'), s.pfad",
        (lid,)).fetchall()
    z = ["", f"## Heruntergeladene Programme und Skripte ({len(zeilen)})", "",
         "Herkunft laut Download-Vermerk von Windows (Zone.Identifier); nicht gültig signierte zuerst.", ""]
    for pfad, inhalt, status, signierer in zeilen[:200]:
        sig = "gültig signiert" + (f": {signierer}" if signierer else "") if status == "Valid" else (status or "?")
        z.append(f"- {'🟢' if status == 'Valid' else '🟡'} {pfad} – {inhalt or 'ohne Angaben'} ({sig})")
    if not zeilen:
        z.append("- keine")
    elif len(zeilen) > 200:
        z.append(f"- … {len(zeilen) - 200} weitere")
    return z


def _stroeme_teil(c: sqlite3.Connection, lid: int) -> list[str]:
    zeilen = c.execute(
        "SELECT pfad, name, groesse, inhalt FROM stroeme WHERE lauf=? AND lower(name) != 'zone.identifier' "
        "ORDER BY (inhalt = ''), groesse DESC", (lid,)).fetchall()
    z = ["", f"## Ungewöhnliche zusätzliche Datenströme (ADS) ({len(zeilen)})", "",
         "Versteckter Inhalt hinter einer Datei oder einem Ordner, den der Explorer nicht zeigt. "
         "Übliche Ströme (Download-Vermerk, Cloud-Merkmale) sind nur gezählt.", ""]
    for pfad, name, groesse, inhalt in zeilen[:150]:
        z.append(f"- {'🔴' if inhalt else '🟡'} {pfad}:{name} · {_gr(groesse)}" + (f" · {inhalt}" if inhalt else ""))
    if not zeilen:
        z.append("- keine")
    elif len(zeilen) > 150:
        z.append(f"- … {len(zeilen) - 150} weitere")
    return z


def _verweise_teil(c: sqlite3.Connection, lid: int) -> list[str]:
    zeilen = c.execute("SELECT pfad, art, ziel FROM verweise WHERE lauf=? ORDER BY pfad", (lid,)).fetchall()
    alt = [r for r in zeilen if os.path.basename(r[0]).lower() in _ALTE_ORDNERNAMEN]
    andere = [r for r in zeilen if r not in alt]
    z = ["", f"## Verweise – nicht betreten ({len(zeilen)})", "",
         "Abzweigungen und symbolische Links zeigen auf andere Orte; ihr Ziel wird dort erfasst, wo es "
         f"liegt. {len(alt)} davon sind Kompatibilitätsverweise von Windows unter alten Ordnernamen.", ""]
    z += [f"- {p} → {ziel or '?'} ({art})" for p, art, ziel in andere[:150]] or ["- keine weiteren"]
    if len(andere) > 150:
        z.append(f"- … {len(andere) - 150} weitere")
    return z


def _luecken_teil(c: sqlite3.Connection, lid: int) -> list[str]:
    gruende = c.execute("SELECT grund, COUNT(*) n FROM luecken WHERE lauf=? GROUP BY grund ORDER BY n DESC",
                        (lid,)).fetchall()
    gesamt = sum(n for _, n in gruende)
    z = ["", f"## Nicht lesbar ({gesamt})", ""]
    if not gesamt:
        return z + ["- nichts – alles wurde gelesen"]
    z.append("- " + " · ".join(f"{g}: {_zahl(n)}" for g, n in gruende))
    if any(g == "Zugriff verweigert" for g, _ in gruende):
        z.append("- „Zugriff verweigert“: von Windows gesperrte Systembereiche – Revier des Virenschutzes, "
                 "der Vollscan bricht sie bewusst nicht auf.")
    z.append("")
    zeilen = c.execute("SELECT pfad, grund FROM luecken WHERE lauf=? ORDER BY pfad LIMIT 200", (lid,)).fetchall()
    z += [f"- {p} ({g})" for p, g in zeilen]
    if gesamt > 200:
        z.append(f"- … {gesamt - 200} weitere")
    return z


def _bericht(c: sqlite3.Connection, lid: int, erster: bool, fehler: list[str] | None = None) -> Path:
    lauf = c.execute("SELECT start, ende, windows, updates, ausgelassen, dateien, code, neu, geaendert, "
                     "entfernt, laufwerke, unzugaenglich, datentraeger FROM laeufe WHERE id=?",
                     (lid,)).fetchone()
    (start, ende, windows, updates, ausgelassen, dateien, code, neu, geaendert, entfernt,
     laufwerke_json, unzugaenglich_json, datentraeger_json) = lauf
    volumen = {w: json.loads(d) for w, d in c.execute("SELECT wurzel, daten FROM volumen WHERE lauf=?", (lid,))}
    gescannt = json.loads(laufwerke_json or "[]")
    gesperrt = json.loads(unzugaenglich_json or "[]")
    stand = json.loads(windows or "{}")
    status = dict(c.execute("SELECT status, COUNT(*) FROM code GROUP BY status").fetchall())
    pe = tuple(_PE)
    platz = ",".join("?" * len(pe))
    unsigniert = c.execute(
        f"SELECT gruppe, COUNT(*) n FROM code WHERE status IN ('NotSigned', 'NotSupportedFileFormat') "
        f"AND lower(substr(pfad, -4)) IN ({platz}) GROUP BY gruppe ORDER BY n DESC LIMIT 40",
        tuple(e[-4:] for e in pe)).fetchall()
    auffaellig = c.execute(
        "SELECT pfad, status FROM code WHERE status IN ('HashMismatch', 'NotTrusted') "
        "ORDER BY pfad LIMIT 50").fetchall()
    mit_hash = c.execute("SELECT COUNT(*) FROM code WHERE sha256 != ''").fetchone()[0]
    verteilt = dict(c.execute("SELECT laufwerk, COUNT(*) FROM dateien WHERE lauf=? GROUP BY laufwerk",
                              (lid,)).fetchall())
    pro_laufwerk = [(w, verteilt.get(w, 0)) for w in gescannt]
    veraendert = c.execute(
        "SELECT pfad, status, signierer FROM code WHERE veraendert_lauf=? "
        "ORDER BY (status = 'Valid'), pfad", (lid,)).fetchall()
    dauer = int((ende or time.time()) - start)
    z = [f"# Vollscan {time.strftime('%d.%m.%Y %H:%M', time.localtime(start))}", "",
         f"- Stand: {windows_text(stand)}",
         f"- Dauer: {dauer // 3600}:{dauer % 3600 // 60:02d} h",
         f"- Dateien: {_zahl(dateien)} in {_zahl(sum(s.get('ordner', 0) for s in volumen.values()))} "
         f"Ordnern, davon Code/Skripte: {_zahl(code)}",
         ("- Erster Lauf: alles ist Ausgangsstand." if erster else
          f"- Seit dem letzten Lauf: {neu} neu · {geaendert} geändert · {entfernt} entfernt"),
         f"- SHA-256 gespeichert für {_zahl(mit_hash)} Code-Dateien"
         + (" – verglichen wird ab dem nächsten Lauf." if erster else "."),
         f"- Nicht lesbar: {_zahl(sum(s.get('luecken', 0) for s in volumen.values()))} Ordner/Dateien "
         "(Liste unten)"]
    z += _datentraegerteil(json.loads(datentraeger_json or "{}"), set(gescannt), set(gesperrt),
                           set(json.loads(ausgelassen or "[]")))
    z += ["", "## Laufwerke", ""]
    for w, n in pro_laufwerk:
        z += _laufwerkteil(w, n, volumen.get(w))
    for w in gesperrt:
        name = volume_name(w)
        z.append(f"- {w}{' (' + name + ')' if name else ''}: von Windows gesperrt (Systembereich, Revier des Virenschutzes)")
    z += ["", "## Signaturen", ""]
    for k, n in sorted(status.items(), key=lambda p: -p[1]):
        z.append(f"- {k}: {_zahl(n)}")
    if updates:
        z += ["", "## Zuletzt installierte Updates", ""] + [f"- {u}" for u in updates.splitlines()]
    z += ["", "## Unsignierte Programme und Bibliotheken nach Herkunft", ""]
    z += [f"- {g}: {n}" for g, n in unsigniert] or ["- keine"]
    z += ["", "## Signatur ungültig oder nicht vertrauenswürdig", ""]
    z += [f"- {p} ({s})" for p, s in auffaellig] or ["- keine"]
    z += _versteckt_teil(c, lid)
    z += _download_teil(c, lid)
    z += _stroeme_teil(c, lid)
    z += _verweise_teil(c, lid)
    z += _luecken_teil(c, lid)
    if not erster:
        skripte = [v for v in veraendert if v[1] == "Skript"]
        nicht_gueltig = [v for v in veraendert if v[1] not in ("Valid", "Skript")]
        z += ["", f"## Code mit verändertem Inhalt seit dem letzten Lauf ({len(veraendert)})", "",
              f"Programme nicht gültig signiert: {len(nicht_gueltig)} (🔴) · Skripte ohne "
              f"Signaturformat: {len(skripte)} (🟡) · gültig signiert, etwa durch Updates: "
              f"{len(veraendert) - len(nicht_gueltig) - len(skripte)}", ""]
        z += [f"- 🔴 {p} ({s})" for p, s, _ in nicht_gueltig[:200]]
        z += [f"- 🟡 {p}" for p, _, _ in skripte[:200]]
        if not nicht_gueltig and not skripte:
            z.append("- nur gültig signierte")
    z += _systemteil(c, lid)
    if fehler:
        z += ["", "## Nicht erfasst", ""] + [f"- {f}" for f in fehler]
    aus = json.loads(ausgelassen or "[]")
    if aus:
        z += ["", "## Nicht betreten (Produkte im Windows-Sicherheitscenter)", ""] + [f"- {o}" for o in aus]
    BERICHTE.mkdir(parents=True, exist_ok=True)
    pfad = BERICHTE / f"Vollscan_{time.strftime('%Y-%m-%d_%H%M', time.localtime(start))}.md"
    pfad.write_text("\n".join(z) + "\n", encoding="utf-8")
    return pfad


# ---------------------------------------------------------------------------
# Nachschlagen für die Merkmale des Modells
# ---------------------------------------------------------------------------

class Baseline:
    """Nachschlagen im letzten fertigen Vollscan.

    Das Ergebnis hängt nur vom Vollscan-Stand ab, nicht vom Zustand der Datei
    jetzt – Training und Bewertung sehen für dasselbe Ereignis dieselben Werte.
    Ohne fertigen Lauf, auf nicht gescannten Laufwerken und in ausgelassenen
    Ordnern ist alles 0 („weiß nicht“), nie „neu“."""

    _CACHE_GRENZE = 50_000

    def __init__(self, db: Path | None = None):
        import threading
        self._db = db
        self._c: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, float, float]] = {}
        self._ordner_cache: dict[str, float] = {}
        self.lauf = 0
        self._wurzeln: list[str] = []
        self._ausgelassen: list[str] = []

    def aktualisieren(self) -> bool:
        """Neuesten fertigen Lauf übernehmen. True, wenn sich der Stand geändert hat."""
        db = self._db or DB
        with self._lock:
            try:
                if self._c is None:
                    if not db.exists():
                        return False
                    self._c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5,
                                              check_same_thread=False)
                zeile = self._c.execute("SELECT id, laufwerke, ausgelassen FROM laeufe WHERE fertig=1 "
                                        "ORDER BY id DESC LIMIT 1").fetchone()
            except sqlite3.Error:
                return False
            if not zeile or zeile[0] == self.lauf:
                return False
            self.lauf = zeile[0]
            self._wurzeln = [os.path.normcase(w) for w in json.loads(zeile[1] or "[]")]
            self._ausgelassen = [os.path.normcase(o) for o in json.loads(zeile[2] or "[]")]
            self._cache.clear()
            self._ordner_cache.clear()
            return True

    def schliessen(self) -> None:
        with self._lock:
            if self._c is not None:
                self._c.close()
                self._c = None

    def _abgedeckt(self, pfad: str) -> bool:
        if not self.lauf:
            return False
        n = os.path.normcase(pfad)
        if not any(n.startswith(w) for w in self._wurzeln):
            return False
        return not any(n == o or n.startswith(o + os.sep) for o in self._ausgelassen)

    def datei(self, pfad: str) -> tuple[float, float, float]:
        """(bekannt, neu, signiert) für eine Datei."""
        if not pfad or not self._abgedeckt(pfad):
            return 0.0, 0.0, 0.0
        treffer = self._cache.get(pfad)
        if treffer is not None:
            return treffer
        with self._lock:
            try:
                d = self._c.execute("SELECT 1 FROM dateien WHERE pfad=?", (pfad,)).fetchone()
                k = self._c.execute("SELECT status FROM code WHERE pfad=?", (pfad,)).fetchone()
            except (sqlite3.Error, AttributeError):
                return 0.0, 0.0, 0.0
        ergebnis = ((1.0, 0.0, 1.0 if k and k[0] == "Valid" else 0.0) if d else (0.0, 1.0, 0.0))
        if len(self._cache) > self._CACHE_GRENZE:
            self._cache.clear()
        self._cache[pfad] = ergebnis
        return ergebnis

    def ordner(self, ordner: str) -> float:
        """1, wenn der Ordner beim Vollscan schon Dateien enthielt."""
        if not ordner or not self._abgedeckt(ordner):
            return 0.0
        ordner = ordner.rstrip("\\/")
        treffer = self._ordner_cache.get(ordner)
        if treffer is not None:
            return treffer
        with self._lock:
            try:
                d = self._c.execute("SELECT 1 FROM dateien WHERE pfad > ? AND pfad < ? LIMIT 1",
                                    (ordner + "\\", ordner + "\\￿")).fetchone()
            except (sqlite3.Error, AttributeError):
                return 0.0
        ergebnis = 1.0 if d else 0.0
        if len(self._ordner_cache) > self._CACHE_GRENZE:
            self._ordner_cache.clear()
        self._ordner_cache[ordner] = ergebnis
        return ergebnis


# ---------------------------------------------------------------------------
# Abfrage für Alarme: kennt der Vollscan diese Datei?
# ---------------------------------------------------------------------------

def zeile(pfad: str) -> str:
    """Beleg für einen Alarm: im letzten fertigen Vollscan erfasst, verändert
    oder seitdem hinzugekommen. Leer, wenn es noch keinen Vollscan gibt."""
    if not pfad or not DB.exists() or str(pfad).replace("/", "\\").startswith("\\\\"):
        return ""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        try:
            lauf = c.execute("SELECT id, start, windows FROM laeufe WHERE fertig=1 "
                             "ORDER BY id DESC LIMIT 1").fetchone()
            if not lauf:
                return ""
            wann = time.strftime("%d.%m.%Y", time.localtime(lauf[1]))
            stand = windows_text(json.loads(lauf[2] or "{}"))
            d = c.execute("SELECT groesse, mtime FROM dateien WHERE pfad=?", (pfad,)).fetchone()
            k = c.execute("SELECT status, signierer FROM code WHERE pfad=?", (pfad,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return ""
    if d is None:
        return f"nicht im Vollscan vom {wann} ({stand}) – seitdem hinzugekommen"
    try:
        st = os.stat(pfad)
        gleich = st.st_size == d[0] and abs(st.st_mtime - d[1]) < 1
    except OSError:
        gleich = True
    text = f"im Vollscan vom {wann} ({stand}) erfasst" + ("" if gleich else ", seitdem verändert")
    if k and k[0] == "Valid":
        text += f" · damals signiert: {k[1] or 'gültig'}"
    elif k:
        text += f" · damals: {k[0]}"
    return text


# ---------------------------------------------------------------------------
# Starten / Stoppen aus der Oberfläche, Einstieg für --vollscan
# ---------------------------------------------------------------------------

def befehl(install_ordner: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        from paths import exe_ohne_fenster
        return [str(exe_ohne_fenster()), "--vollscan"]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else python), str(install_ordner / "main.py"), "--vollscan"]


def starts_lesen() -> list[float]:
    try:
        return [float(t) for t in json.loads(STARTS.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def woche_frei(jetzt: float | None = None) -> tuple[bool, float]:
    """(darf ein neuer Vollscan starten, ab wann wieder frei). Fortsetzen zählt nicht."""
    jetzt = jetzt if jetzt is not None else time.time()
    letzte = sorted(t for t in starts_lesen() if jetzt - t < WOCHE)
    if len(letzte) < PRO_WOCHE:
        return True, jetzt
    return False, letzte[-PRO_WOCHE] + WOCHE


def sperre_text(jetzt: float | None = None) -> str:
    _frei, ab = woche_frei(jetzt)
    return (f"Höchstens {PRO_WOCHE} Vollscans in 7 Tagen – der nächste geht ab "
            f"{time.strftime('%d.%m. %H:%M', time.localtime(ab))}. Einen angehaltenen Scan "
            "fortsetzen geht jederzeit.")


def _start_merken(jetzt: float | None = None) -> None:
    jetzt = jetzt if jetzt is not None else time.time()
    letzte = [t for t in starts_lesen() if jetzt - t < WOCHE] + [jetzt]
    tmp = STARTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(letzte), encoding="utf-8")
    os.replace(tmp, STARTS)


def starten(install_ordner: Path) -> str:
    if laeuft():
        return "Der Vollscan läuft schon."
    neu = stand_lesen().get("phase") != "abgebrochen"       # Fortsetzen zählt nicht als neue Prüfung
    if neu and not woche_frei()[0]:
        return sperre_text()
    ordner_anlegen()
    STOPP.unlink(missing_ok=True)
    b = befehl(install_ordner)
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        subprocess.Popen(b, cwd=str(install_ordner), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return f"Start fehlgeschlagen: {exc}"
    if neu:
        try:
            _start_merken()
        except OSError:
            pass
    for _ in range(50):
        time.sleep(0.2)
        if laeuft():
            return "Vollscan gestartet – läuft im Hintergrund mit niedriger Priorität."
    return "Vollscan gestartet, meldet sich noch nicht – gleich mit /wache fullscan nachsehen."


def stoppen() -> str:
    if not laeuft():
        return "Es läuft kein Vollscan."
    ordner_anlegen()
    STOPP.write_text(str(time.time()), encoding="utf-8")
    return "Vollscan hält an – der nächste Start macht dort weiter."


def hauptprogramm() -> int:
    """Einstieg für `--vollscan`."""
    ordner_anlegen()
    if laeuft():
        return 0
    STOPP.unlink(missing_ok=True)
    _hintergrundmodus()
    c = _verbinden()
    try:
        return Lauf(c).starten()
    except Exception as exc:
        z = stand_lesen()
        z.update(phase="fehler", fehlertext=repr(exc)[:500])
        _stand_schreiben(z)
        return 1
    finally:
        c.close()
