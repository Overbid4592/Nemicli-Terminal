"""Vollscan: alle lokalen Laufwerke als Ausgangsstand (Baseline) der Wache.

Stufe 1  jede Datei aller lokalen Volumes (auch ohne Laufwerksbuchstaben):
         Pfad, Größe, Änderungszeit – nur Verzeichniseinträge.
Stufe 2  ausführbarer Code und Skripte zusätzlich: SHA-256, Signaturstatus,
         Signierer (wintrust.dll im eigenen Prozess). Der vorige Hash bleibt
         erhalten; der Bericht listet Code, dessen Inhalt sich geändert hat.
Stufe 3  Systemstand (systemstand.py): Autostart, Dienste, Aufgaben, Treiber,
         Netz – mit Signatur und Ampel.

Läuft als eigener Prozess (`--vollscan`) im Hintergrundmodus von Windows
(niedrige CPU-, Datenträger- und Speicherpriorität), nur lesend, fortsetzbar.
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
"""

# Spalten, die nach dem ersten Lauf dazukamen – an bestehende Datenbanken anbauen.
_NACHRUESTEN = [("code", "sha_vorher", "TEXT DEFAULT ''"), ("code", "veraendert_lauf", "INTEGER DEFAULT 0"),
                ("laeufe", "unzugaenglich", "TEXT DEFAULT '[]'")]

# Nicht gültig signierter Code wird in jedem Lauf neu gehasht.
_NEU_HASHEN = ("NotSigned", "HashMismatch", "NotTrusted")


# ---------------------------------------------------------------------------
# Umgebung: Laufwerke, Windows-Stand, ausgelassene Ordner
# ---------------------------------------------------------------------------

def volumes() -> tuple[list[str], list[str]]:
    """Alle lokalen Volumes (Festplatten, Wechseldatenträger – keine Netz- und
    CD-Laufwerke), auch ohne Laufwerksbuchstaben oder in einen Ordner
    eingehängt. Rückgabe: (lesbare Wurzeln, nicht zugängliche)."""
    if os.name != "nt":
        return ["/"], []
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.FindFirstVolumeW.restype = ctypes.c_void_p
    k.FindFirstVolumeW.argtypes = [w.LPWSTR, w.DWORD]
    k.FindNextVolumeW.argtypes = [ctypes.c_void_p, w.LPWSTR, w.DWORD]
    k.FindVolumeClose.argtypes = [ctypes.c_void_p]
    k.GetVolumePathNamesForVolumeNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD)]
    lesbar, gesperrt = [], []
    puffer = ctypes.create_unicode_buffer(260)
    h = k.FindFirstVolumeW(puffer, 260)
    if not h or h == ctypes.c_void_p(-1).value:
        return _laufwerksbuchstaben(), []
    try:
        while True:
            guid = puffer.value
            namen = ctypes.create_unicode_buffer(2048)
            laenge = w.DWORD()
            pfade = []
            if k.GetVolumePathNamesForVolumeNameW(guid, namen, 2048, ctypes.byref(laenge)):
                pfade = [x for x in namen[:laenge.value].split("\0") if x]
            wurzel = sorted(pfade, key=len)[0] if pfade else guid
            if k.GetDriveTypeW(guid) in (2, 3):             # REMOVABLE, FIXED
                try:
                    os.listdir(wurzel)
                    lesbar.append(wurzel)
                except OSError:
                    gesperrt.append(wurzel)
            if not k.FindNextVolumeW(h, puffer, 260):
                break
    finally:
        k.FindVolumeClose(h)
    lesbar.sort(key=lambda x: (x.startswith("\\\\"), x))
    return lesbar, gesperrt


def _laufwerksbuchstaben() -> list[str]:
    import ctypes
    k = ctypes.windll.kernel32
    maske = k.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26)
            if maske & (1 << i) and k.GetDriveTypeW(f"{chr(65 + i)}:\\") in (2, 3)]


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
# Zustand für die Anzeige (vollscan.json)
# ---------------------------------------------------------------------------

def stand_lesen() -> dict:
    try:
        return json.loads(STAND.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _stand_schreiben(z: dict) -> None:
    tmp = STAND.with_suffix(".tmp")
    tmp.write_text(json.dumps(z, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STAND)


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
        return (f"{vorn}Stufe 1 (alle Dateien) · {z.get('laufwerk', '')} · "
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


def kurzanzeige(z: dict) -> str:
    """Für die Statuszeile: kurz, mit genau einem „i/n“ für den Mini-Balken."""
    if z.get("phase") == "liste":
        gesamt = max(z.get("belegt", 0) / 1e9, 1)
        gb = min(z.get("bytes", 0) / 1e9, gesamt * 0.99)
        return f"Vollscan {z.get('laufwerk', '')} {gb:.0f}/{gesamt:.0f} GB"
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
        self.ausgelassen_pfade = ausgelassene_ordner()
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
            lid = self.c.execute(
                "INSERT INTO laeufe(start, windows, updates, laufwerke, ausgelassen, unzugaenglich) "
                "VALUES (?,?,?,?,?,?)",
                (time.time(), json.dumps(stand, ensure_ascii=False), "\n".join(stand["updates"]),
                 "[]", json.dumps(self.ausgelassen_pfade), json.dumps(self.unzugaenglich))).lastrowid
            self.c.commit()
            erledigt = []
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

        for w in self.wurzeln:
            if w in erledigt:
                continue
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
        stapel = [wurzel]
        puffer: list[tuple] = []
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
            except OSError:
                self.z["fehler"] += 1
                continue
            for e in eintraege:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.is_symlink() or e.is_junction() or self._ausgelassen(e.path):
                            continue
                        stapel.append(e.path)
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    self.z["fehler"] += 1
                    continue
                endung = os.path.splitext(e.name)[1].lower()
                lokal = not (getattr(st, "st_file_attributes", 0) & _NICHT_LOKAL)
                code = 1 if endung in CODE_ENDUNGEN and lokal else 0
                puffer.append((e.path, wurzel, st.st_size, st.st_mtime, code, self.lid, self.lid))
                self.z["dateien"] += 1
                self.z["bytes"] += st.st_size
            if len(puffer) >= 5000:
                self._schreiben(puffer)
                puffer = []
            gesehen += 1
        self._schreiben(puffer)
        return True

    def _schreiben(self, puffer: list[tuple]) -> None:
        if not puffer:
            return
        self.c.executemany(
            "INSERT INTO dateien(pfad, laufwerk, groesse, mtime, code, lauf, erstmals) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(pfad) DO UPDATE SET "
            # Zweiter Besuch im selben Lauf (Fortsetzen): Merkmal behalten.
            "geaendert = CASE WHEN lauf = excluded.lauf THEN geaendert "
            "ELSE (groesse != excluded.groesse OR mtime != excluded.mtime) END, "
            "groesse = excluded.groesse, mtime = excluded.mtime, code = excluded.code, "
            "laufwerk = excluded.laufwerk, lauf = excluded.lauf", puffer)
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
        bekannt = ({(a, n.lower(), p.lower()) for a, n, p in self.c.execute(
            "SELECT art, name, pfad FROM system WHERE lauf=?", (vorher_lauf,))} if vorher_lauf else None)
        zeilen = []
        for e in eintraege:
            e["neu"] = 1 if bekannt is not None and (e["art"], e["name"].lower(),
                                                    e["pfad"].lower()) not in bekannt else 0
            e["ampel"], e["grund"] = systemstand.ampel(e)
            zeilen.append((self.lid, e["art"], e["name"], e["pfad"], e["detail"], e["status"],
                           e["signierer"], e["ampel"], e["grund"], e["neu"]))
        self.c.execute("DELETE FROM system WHERE lauf=?", (self.lid,))
        self.c.executemany("INSERT INTO system VALUES (?,?,?,?,?,?,?,?,?,?)", zeilen)
        self.c.commit()
        self.z["system_fehler"] = fehler

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
        c.execute("DELETE FROM system WHERE lauf < ?", (lid - 5,))          # die letzten Läufe genügen
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
        with open(pfad, "rb", buffering=0) as f:
            while True:
                block = f.read(1 << 20)
                if not block:
                    break
                sha.update(block)
    except PermissionError:
        return "", "kein Zugriff", ""
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


def _systemteil(c: sqlite3.Connection, lid: int) -> list[str]:
    from .systemstand import AMPEL_REGELN, GELB, GRUEN, ROT
    zeilen = c.execute("SELECT art, name, pfad, detail, status, signierer, ampel, grund, neu "
                       "FROM system WHERE lauf=?", (lid,)).fetchall()
    if not zeilen:
        return []
    zaehl = {f: sum(1 for r in zeilen if r[6] == f) for f in (ROT, GELB, GRUEN)}
    z = ["", "## Systemstand mit Ampel", "",
         f"🔴 anschauen: {zaehl[ROT]} · 🟡 beobachten: {zaehl[GELB]} · 🟢 unauffällig: {zaehl[GRUEN]}", "",
         "Vorsortierung nach festen Regeln, kein Urteil – die erste passende Regel gilt:", ""]
    z += [f"- {farbe}: {regel}" for farbe, regel in AMPEL_REGELN]
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


def _bericht(c: sqlite3.Connection, lid: int, erster: bool, fehler: list[str] | None = None) -> Path:
    lauf = c.execute("SELECT start, ende, windows, updates, ausgelassen, dateien, code, neu, "
                     "geaendert, entfernt, laufwerke, unzugaenglich FROM laeufe WHERE id=?", (lid,)).fetchone()
    (start, ende, windows, updates, ausgelassen, dateien, code, neu, geaendert, entfernt,
     laufwerke_json, unzugaenglich_json) = lauf
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
    pro_laufwerk = [(w, verteilt.get(w, 0)) for w in json.loads(laufwerke_json or "[]")]
    veraendert = c.execute(
        "SELECT pfad, status, signierer FROM code WHERE veraendert_lauf=? "
        "ORDER BY (status = 'Valid'), pfad", (lid,)).fetchall()
    dauer = int((ende or time.time()) - start)
    z = [f"# Vollscan {time.strftime('%d.%m.%Y %H:%M', time.localtime(start))}", "",
         f"- Stand: {windows_text(stand)}",
         f"- Dauer: {dauer // 3600}:{dauer % 3600 // 60:02d} h",
         f"- Dateien: {_zahl(dateien)}, davon Code/Skripte: {_zahl(code)}",
         ("- Erster Lauf: alles ist Ausgangsstand." if erster else
          f"- Seit dem letzten Lauf: {neu} neu · {geaendert} geändert · {entfernt} entfernt"),
         f"- SHA-256 gespeichert für {_zahl(mit_hash)} Code-Dateien"
         + (" – verglichen wird ab dem nächsten Lauf." if erster else "."),
         "", "## Laufwerke", ""]
    for w, n in pro_laufwerk:
        name = volume_name(w)
        z.append(f"- {w}{' (' + name + ')' if name else ''}: {_zahl(n)} Dateien")
    for w in json.loads(unzugaenglich_json or "[]"):
        name = volume_name(w)
        z.append(f"- {w}{' (' + name + ')' if name else ''}: nicht zugänglich (ohne Adminrechte gesperrt)")
    z += ["", "## Signaturen", ""]
    for k, n in sorted(status.items(), key=lambda p: -p[1]):
        z.append(f"- {k}: {_zahl(n)}")
    if updates:
        z += ["", "## Zuletzt installierte Updates", ""] + [f"- {u}" for u in updates.splitlines()]
    z += ["", "## Unsignierte Programme und Bibliotheken nach Herkunft", ""]
    z += [f"- {g}: {n}" for g, n in unsigniert] or ["- keine"]
    z += ["", "## Signatur ungültig oder nicht vertrauenswürdig", ""]
    z += [f"- {p} ({s})" for p, s in auffaellig] or ["- keine"]
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
    if not pfad or not DB.exists():
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


def starten(install_ordner: Path) -> str:
    if laeuft():
        return "Der Vollscan läuft schon."
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
