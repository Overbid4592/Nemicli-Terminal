"""SQLite-Speicher der Wache: Ereignisse, Alarme, Prozess-Profile, Inventar, Meta.

Thread-sicher über EIN Lock und eine Verbindung (die Sensoren schreiben aus
Threads, die Persönlichkeit und /wache lesen). WAL-Modus, damit Lesen nicht auf Schreiben
wartet. Die Prozess-Profile sind das Gedächtnis der Wache: welcher Prozess
läuft üblicherweise woher, unter wem, zu welcher Stunde, mit welchen Kindern
und Zielen – daraus entstehen die „verhält sich untypisch“-Merkmale.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .ereignisse import Alarm, Ereignis

SCHEMA = """
CREATE TABLE IF NOT EXISTS ereignisse (
    id TEXT PRIMARY KEY, zeit REAL NOT NULL, kategorie TEXT NOT NULL, aktion TEXT NOT NULL,
    schwere INTEGER DEFAULT 0, text TEXT DEFAULT '', prozess TEXT DEFAULT '',
    pid INTEGER DEFAULT 0, ppid INTEGER DEFAULT 0, eltern TEXT DEFAULT '',
    nutzer TEXT DEFAULT '', exe TEXT DEFAULT '', cmdline TEXT DEFAULT '',
    lokal TEXT DEFAULT '', ziel TEXT DEFAULT '', zielport INTEGER DEFAULT 0,
    protokoll TEXT DEFAULT '', datei TEXT DEFAULT '', score REAL DEFAULT 0.0,
    anomalie INTEGER DEFAULT 0, extra TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_e_zeit ON ereignisse(zeit DESC);
CREATE INDEX IF NOT EXISTS idx_e_prozess ON ereignisse(prozess);

CREATE TABLE IF NOT EXISTS alarme (
    id TEXT PRIMARY KEY, zeit REAL NOT NULL, titel TEXT NOT NULL, text TEXT DEFAULT '',
    schwere INTEGER DEFAULT 0, regel TEXT DEFAULT '', ereignis_id TEXT DEFAULT '',
    quelle TEXT DEFAULT 'regel', score REAL DEFAULT 0.0, status TEXT DEFAULT 'offen',
    urteil_von TEXT DEFAULT '', begruendung TEXT DEFAULT '',
    subjekt TEXT DEFAULT '', anzahl INTEGER DEFAULT 1, zuletzt REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_a_zeit ON alarme(zeit DESC);

CREATE TABLE IF NOT EXISTS profile (
    prozess TEXT PRIMARY KEY, erstmals REAL NOT NULL, zuletzt REAL NOT NULL,
    anzahl INTEGER DEFAULT 1, exes TEXT DEFAULT '', eltern TEXT DEFAULT '',
    nutzer TEXT DEFAULT '', cpu_mittel REAL DEFAULT 0.0, mem_mittel REAL DEFAULT 0.0,
    stunden TEXT DEFAULT '', kinder TEXT DEFAULT '', ports TEXT DEFAULT '',
    aus_inventar INTEGER DEFAULT 0, vertraut INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inventar (
    id TEXT PRIMARY KEY, art TEXT NOT NULL, name TEXT NOT NULL, detail TEXT DEFAULT '',
    extra TEXT DEFAULT '', erstmals REAL NOT NULL, zuletzt REAL NOT NULL, da INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_i_art ON inventar(art);

CREATE TABLE IF NOT EXISTS meta (schluessel TEXT PRIMARY KEY, wert TEXT);

CREATE TABLE IF NOT EXISTS justierungen (
    zeit REAL NOT NULL, wer TEXT, was TEXT, alt TEXT, neu TEXT, begruendung TEXT
);

-- Bekannte harmlose Dinge: was ist das (bezeichnung) und warum harmlos. Gefüllt
-- aus den Urteilen der Persönlichkeit (wache_bewerten mit bezeichnung). Schlüssel
-- = "prozess:winstore.app.exe", "ziel:2.23.246.164", "datei:c:\…", "eintrag:…".
CREATE TABLE IF NOT EXISTS bekannt (
    schluessel TEXT PRIMARY KEY, art TEXT NOT NULL, name TEXT NOT NULL,
    bezeichnung TEXT NOT NULL, begruendung TEXT DEFAULT '', wer TEXT DEFAULT '',
    erstmals REAL NOT NULL, zuletzt REAL NOT NULL, anzahl INTEGER DEFAULT 1
);
"""


_SKRIPT_HOSTS = {"powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe", "mshta.exe",
                 "rundll32.exe", "regsvr32.exe", "python.exe", "pythonw.exe", "node.exe"}


def bekannt_schluessel(ereignis) -> list[tuple[str, str, str]]:
    """(schluessel, art, name) für alles, woran man ein Ereignis wiedererkennt:
    der Prozess, bei Netz zusätzlich das Ziel, bei Dateien der Pfad, bei
    Inventar-Alarmen (neuer Autostart/Dienst/Aufgabe) der Eintrag selbst."""
    if ereignis is None:
        return []
    out = []
    if ereignis.prozess:
        p = ereignis.prozess.lower()
        if p in _SKRIPT_HOSTS and ereignis.eltern:
            # Ein Skript-Host ist nur zusammen mit seinem Elternprozess „bekannt“: powershell.exe
            # aus NemiCLI ist etwas anderes als powershell.exe aus winword.exe. Pauschal
            # „powershell.exe = harmlos“ wäre nach einem einzigen geratenen Urteil zu grob.
            el = ereignis.eltern.lower()
            out.append((f"prozess:{p}<{el}", "prozess", f"{ereignis.prozess} (von {ereignis.eltern})"))
        else:
            out.append(("prozess:" + p, "prozess", ereignis.prozess))
    if ereignis.ziel:
        out.append(("ziel:" + str(ereignis.ziel).lower(), "ziel", str(ereignis.ziel)))
    if ereignis.datei:
        out.append(("datei:" + str(ereignis.datei).lower(), "datei", str(ereignis.datei)))
    if not out and ereignis.text:
        kurz = " ".join(str(ereignis.text).split())[:160]
        out.append(("eintrag:" + kurz.lower(), "eintrag", kurz))
    return out


def _menge_plus(vorhanden: str, neu: str, grenze: int = 12) -> str:
    """'|'-getrennte Menge um einen Wert ergänzen, gedeckelt."""
    if not neu:
        return vorhanden or ""
    teile = [t for t in (vorhanden or "").split("|") if t]
    if neu in teile:
        return vorhanden
    teile.append(neu)
    return "|".join(teile[-grenze:])


def _stunde_zaehlen(hist: str, stunde: int | None) -> str:
    werte = histogramm(hist)
    if stunde is not None and 0 <= stunde < 24:
        werte[stunde] += 1
    return ",".join(str(w) for w in werte)


def histogramm(hist: str) -> list[int]:
    if not hist:
        return [0] * 24
    try:
        werte = [int(x) for x in hist.split(",")]
    except ValueError:
        return [0] * 24
    return (werte + [0] * 24)[:24]


class Speicher:
    def __init__(self, pfad: str | Path):
        Path(pfad).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._c = sqlite3.connect(str(pfad), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA synchronous=NORMAL")
        self._c.executescript(SCHEMA)
        self._nachruesten()
        self._c.commit()

    def _nachruesten(self) -> None:
        """Später hinzugekommene Spalten an bestehende Datenbanken anbauen.
        ALTER TABLE hängt hinten an – genau die Reihenfolge, die Alarm.zeile() liefert."""
        da = {r["name"] for r in self._c.execute("PRAGMA table_info(alarme)")}
        for spalte, typ in (("subjekt", "TEXT DEFAULT ''"), ("anzahl", "INTEGER DEFAULT 1"),
                            ("zuletzt", "REAL DEFAULT 0.0")):
            if spalte not in da:
                self._c.execute(f"ALTER TABLE alarme ADD COLUMN {spalte} {typ}")
        self._c.execute("CREATE INDEX IF NOT EXISTS idx_a_regel_subjekt ON alarme(regel, subjekt)")
        if "subjekt" not in da:
            self._subjekte_nachtragen()

    def _subjekte_nachtragen(self) -> None:
        """Alte Alarme haben kein Subjekt – aus dem Ereignis dahinter
        nachholen, damit auch sie sich als Gruppe bewerten und fürs Dämpfen zählen lassen."""
        from .regeln import subjekt_fuer
        rows = self._c.execute(
            "SELECT a.id, a.regel, e.* FROM alarme a JOIN ereignisse e ON a.ereignis_id = e.id "
            "WHERE a.subjekt = ''").fetchall()
        neu = []
        for r in rows:
            try:
                s = subjekt_fuer(r["regel"], Ereignis.aus_zeile(r))
                if s:
                    neu.append((s, r["id"]))
            except Exception:
                continue
        if neu:
            self._c.executemany("UPDATE alarme SET subjekt = ? WHERE id = ?", neu)

    # ------------------------------------------------------------ Ereignisse

    def ereignisse_schreiben(self, liste: list[Ereignis]) -> None:
        if not liste:
            return
        with self._lock:
            self._c.executemany(
                f"INSERT OR REPLACE INTO ereignisse VALUES ({','.join('?' * 21)})",
                [e.zeile() for e in liste])
            self._c.commit()

    def ereignisse(self, limit: int = 50, seit: float | None = None, kategorie: str = "",
                   prozess: str = "", nur_anomalien: bool = False,
                   mindestens: int = 0) -> list[Ereignis]:
        sql, args = "SELECT * FROM ereignisse WHERE 1=1", []
        if seit is not None:
            sql += " AND zeit >= ?"; args.append(seit)
        if kategorie:
            sql += " AND kategorie = ?"; args.append(kategorie)
        if prozess:
            sql += " AND prozess LIKE ?"; args.append(f"%{prozess}%")
        if nur_anomalien:
            sql += " AND anomalie = 1"
        if mindestens:
            sql += " AND schwere >= ?"; args.append(mindestens)
        sql += " ORDER BY zeit DESC LIMIT ?"; args.append(limit)
        with self._lock:
            return [Ereignis.aus_zeile(r) for r in self._c.execute(sql, args)]

    def ereignis(self, eid: str) -> Ereignis | None:
        with self._lock:
            r = self._c.execute("SELECT * FROM ereignisse WHERE id = ?", (eid,)).fetchone()
        return Ereignis.aus_zeile(r) if r else None

    def ereignisse_seit(self, seit: float, limit: int = 20_000) -> list[Ereignis]:
        """Fürs Training: die NEUESTEN `limit` Ereignisse seit `seit`, aufsteigend sortiert.
        (Kein ASC LIMIT: bei mehr als `limit` Ereignissen fielen sonst die neuesten
        weg und das Modell lernte mit dem ältesten Stand.)"""
        with self._lock:
            rows = self._c.execute(
                "SELECT * FROM ereignisse WHERE zeit >= ? ORDER BY zeit DESC LIMIT ?",
                (seit, limit)).fetchall()
        return [Ereignis.aus_zeile(r) for r in reversed(rows)]

    def anzahl_ereignisse_seit(self, seit: float) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM ereignisse WHERE zeit > ?", (seit,)).fetchone()[0])

    def anzahl_ereignisse(self) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0])

    def top_prozesse(self, limit: int = 10, stunden: int = 24) -> list[tuple[str, int]]:
        seit = time.time() - stunden * 3600
        with self._lock:
            rows = self._c.execute(
                "SELECT prozess, COUNT(*) n FROM ereignisse WHERE zeit >= ? AND prozess != '' "
                "GROUP BY prozess ORDER BY n DESC LIMIT ?", (seit, limit)).fetchall()
        return [(r["prozess"], r["n"]) for r in rows]

    # ---------------------------------------------------------------- Alarme

    def alarme_schreiben(self, liste: list[Alarm]) -> None:
        if not liste:
            return
        with self._lock:
            self._c.executemany(
                f"INSERT OR REPLACE INTO alarme ({','.join(Alarm.SPALTEN)}) "
                f"VALUES ({','.join('?' * len(Alarm.SPALTEN))})",
                [a.zeile() for a in liste])
            self._c.commit()

    def alarme(self, limit: int = 50, status: str = "", seit: float | None = None,
               mindestens: int = 0) -> list[Alarm]:
        sql, args = "SELECT * FROM alarme WHERE 1=1", []
        if status:
            sql += " AND status = ?"; args.append(status)
        if seit is not None:
            sql += " AND zeit >= ?"; args.append(seit)
        if mindestens:
            sql += " AND schwere >= ?"; args.append(mindestens)
        sql += " ORDER BY zeit DESC LIMIT ?"; args.append(limit)
        with self._lock:
            return [Alarm.aus_zeile(r) for r in self._c.execute(sql, args)]

    def alarm(self, aid: str) -> Alarm | None:
        with self._lock:
            r = self._c.execute("SELECT * FROM alarme WHERE id = ? OR id LIKE ?",
                                (aid, f"{aid}%")).fetchone()
        return Alarm.aus_zeile(r) if r else None

    def alarm_urteil(self, aid: str, status: str, wer: str, begruendung: str = "") -> bool:
        with self._lock:
            cur = self._c.execute(
                "UPDATE alarme SET status = ?, urteil_von = ?, begruendung = ? WHERE id = ?",
                (status, wer, begruendung[:500], aid))
            self._c.commit()
            return cur.rowcount > 0

    def alarm_zaehlen(self, aid: str, zeit: float) -> None:
        """Derselbe Alarm kam innerhalb des Cooldowns wieder: Zähler hoch statt neue Zeile."""
        with self._lock:
            self._c.execute("UPDATE alarme SET anzahl = anzahl + 1, zuletzt = ? WHERE id = ?", (zeit, aid))
            self._c.commit()

    def alarme_unbeurteilt(self, regel: str, subjekt: str = "", limit: int = 500) -> list[Alarm]:
        """Alle noch nicht beurteilten Alarme (offen/gesehen) einer Regel – wahlweise nur zu
        einem Subjekt (Prozess, Elternprozess, Ziel, Pfad; Teilstring, Groß/klein egal)."""
        sql = "SELECT * FROM alarme WHERE status IN ('offen', 'gesehen') AND regel = ?"
        args: list = [regel]
        if subjekt:
            sql += " AND LOWER(subjekt) LIKE ?"; args.append(f"%{subjekt.lower()}%")
        sql += " ORDER BY zeit DESC LIMIT ?"; args.append(limit)
        with self._lock:
            return [Alarm.aus_zeile(r) for r in self._c.execute(sql, args)]

    def gedaempft(self, ab: int = 3) -> dict[tuple[str, str], int]:
        """(Regel, Subjekt)-Paare, die mindestens `ab`-mal als harmlos beurteilt wurden und
        nie als echt – die meldet der Motor nicht mehr. Ein einziges „echt“ hebt das auf.
        Nur Mengen-Regeln (regeln.MENGENREGELN); Muster-Regeln werden nie gedämpft."""
        from .regeln import MENGENREGELN
        if ab <= 0:
            return {}
        regeln = sorted(MENGENREGELN)
        with self._lock:
            rows = self._c.execute(
                "SELECT regel, LOWER(subjekt) s, "
                "SUM(status = 'harmlos') harmlos, SUM(status = 'echt') echt "
                f"FROM alarme WHERE subjekt != '' AND regel IN ({','.join('?' * len(regeln))}) "
                "GROUP BY regel, s HAVING harmlos >= ? AND echt = 0", (*regeln, ab)).fetchall()
        return {(r["regel"], r["s"]): int(r["harmlos"]) for r in rows}

    def alarm_statistik(self) -> dict:
        with self._lock:
            rows = self._c.execute(
                "SELECT status, COUNT(*) n FROM alarme GROUP BY status").fetchall()
        d = {r["status"]: r["n"] for r in rows}
        d["gesamt"] = sum(d.values())
        return d

    # ------------------------------------------------------- Bekanntes

    def bekannt_merken(self, schluessel: str, art: str, name: str, bezeichnung: str,
                       begruendung: str, wer: str) -> None:
        now = time.time()
        with self._lock:
            self._c.execute(
                "INSERT INTO bekannt (schluessel, art, name, bezeichnung, begruendung, wer, erstmals, zuletzt, anzahl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(schluessel) DO UPDATE SET bezeichnung = excluded.bezeichnung, "
                "begruendung = excluded.begruendung, wer = excluded.wer, zuletzt = excluded.zuletzt, "
                "anzahl = anzahl + 1",
                (schluessel, art, name[:200], bezeichnung[:200], begruendung[:500], wer, now, now))
            self._c.commit()

    def bekannt(self, schluessel: str) -> dict | None:
        with self._lock:
            r = self._c.execute("SELECT * FROM bekannt WHERE schluessel = ?", (schluessel,)).fetchone()
        return dict(r) if r else None

    def bekannt_fuer(self, ereignis) -> list[dict]:
        """Alle bekannten Einträge, die zu diesem Ereignis passen (Prozess, Ziel, Datei …)."""
        out = []
        for schluessel, _art, _name in bekannt_schluessel(ereignis):
            b = self.bekannt(schluessel)
            if b:
                out.append(b)
        return out

    def bekannt_liste(self, art: str = "", limit: int = 300) -> list[dict]:
        with self._lock:
            if art:
                rows = self._c.execute("SELECT * FROM bekannt WHERE art = ? ORDER BY zuletzt DESC LIMIT ?",
                                       (art, limit)).fetchall()
            else:
                rows = self._c.execute("SELECT * FROM bekannt ORDER BY zuletzt DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def bekannt_vergessen(self, schluessel: str) -> bool:
        with self._lock:
            n = self._c.execute("DELETE FROM bekannt WHERE schluessel = ?", (schluessel,)).rowcount
            self._c.commit()
        return n > 0

    def fehlalarm_prozesse(self) -> set[str]:
        """Prozesse, deren Alarme als harmlos beurteilt wurden – dämpft den Score."""
        with self._lock:
            rows = self._c.execute(
                "SELECT e.prozess FROM alarme a JOIN ereignisse e ON a.ereignis_id = e.id "
                "WHERE a.status = 'harmlos' AND e.prozess != ''").fetchall()
        return {r["prozess"] for r in rows}

    # --------------------------------------------------------------- Profile

    def profil_fortschreiben(self, name: str, *, exe: str = "", eltern: str = "",
                             nutzer: str = "", cpu: float = 0.0, mem: float = 0.0,
                             stunde: int | None = None, kind: str = "", port: int = 0,
                             aus_inventar: bool = False) -> bool:
        """True, wenn der Prozess neu war."""
        jetzt = time.time()
        with self._lock:
            r = self._c.execute("SELECT * FROM profile WHERE prozess = ?", (name,)).fetchone()
            if r is None:
                self._c.execute(
                    "INSERT INTO profile VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, jetzt, jetzt, 1, exe, eltern, nutzer, cpu, mem,
                     _stunde_zaehlen("", stunde), kind, str(port) if port else "",
                     int(aus_inventar), 0))
                self._c.commit()
                return True
            n = r["anzahl"]
            self._c.execute(
                "UPDATE profile SET zuletzt=?, anzahl=?, exes=?, eltern=?, nutzer=?, "
                "cpu_mittel=?, mem_mittel=?, stunden=?, kinder=?, ports=? WHERE prozess=?",
                (jetzt, n + 1, _menge_plus(r["exes"], exe), _menge_plus(r["eltern"], eltern),
                 _menge_plus(r["nutzer"], nutzer),
                 (r["cpu_mittel"] * n + cpu) / (n + 1), (r["mem_mittel"] * n + mem) / (n + 1),
                 _stunde_zaehlen(r["stunden"], stunde), _menge_plus(r["kinder"], kind, 24),
                 _menge_plus(r["ports"], str(port) if port else "", 32), name))
            self._c.commit()
            return False

    def profile(self) -> dict[str, dict]:
        with self._lock:
            rows = self._c.execute("SELECT * FROM profile").fetchall()
        return {r["prozess"]: {
            "anzahl": r["anzahl"], "exes": r["exes"] or "", "eltern": r["eltern"] or "",
            "nutzer": r["nutzer"] or "", "stunden": histogramm(r["stunden"]),
            "kinder": r["kinder"] or "", "ports": r["ports"] or "",
            "vertraut": bool(r["vertraut"])} for r in rows}

    def anzahl_profile(self) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM profile").fetchone()[0])

    # -------------------------------------------------------------- Inventar

    def inventar_ids(self) -> set[str]:
        with self._lock:
            return {r["id"] for r in self._c.execute("SELECT id FROM inventar")}

    def inventar_abgleichen(self, eintraege: list) -> dict:
        """Ist-Zustand einspielen: neu / wieder da / verschwunden markieren."""
        jetzt = time.time()
        bekannt = self.inventar_ids()
        aktuell = set()
        neu = 0
        with self._lock:
            for e in eintraege:
                if e.id in aktuell:              # zwei gleiche Funde in einem Scan
                    continue
                aktuell.add(e.id)
                if e.id in bekannt:
                    self._c.execute("UPDATE inventar SET zuletzt=?, da=1, detail=?, extra=? "
                                    "WHERE id=?", (jetzt, e.detail,
                                                   json.dumps(e.extra, ensure_ascii=False,
                                                              default=str), e.id))
                else:
                    neu += 1
                    self._c.execute("INSERT INTO inventar VALUES (?,?,?,?,?,?,?,1)",
                                    (e.id, e.art, e.name, e.detail,
                                     json.dumps(e.extra, ensure_ascii=False, default=str),
                                     jetzt, jetzt))
            weg = bekannt - aktuell
            if weg:
                self._c.executemany("UPDATE inventar SET da=0 WHERE id=?", [(i,) for i in weg])
            self._c.commit()
        return {"gesamt": len(aktuell), "neu": neu, "verschwunden": len(weg)}

    def inventar(self, art: str = "", nur_da: bool = True) -> list[dict]:
        sql, args = "SELECT * FROM inventar WHERE 1=1", []
        if art:
            sql += " AND art = ?"; args.append(art)
        if nur_da:
            sql += " AND da = 1"
        sql += " ORDER BY art, name"
        with self._lock:
            rows = self._c.execute(sql, args).fetchall()
        return [{"id": r["id"], "art": r["art"], "name": r["name"], "detail": r["detail"],
                 "extra": json.loads(r["extra"]) if r["extra"] else {},
                 "erstmals": r["erstmals"], "zuletzt": r["zuletzt"]} for r in rows]

    def inventar_zaehlung(self) -> dict[str, int]:
        with self._lock:
            rows = self._c.execute(
                "SELECT art, COUNT(*) n FROM inventar WHERE da = 1 GROUP BY art").fetchall()
        return {r["art"]: r["n"] for r in rows}

    def software_pfade(self) -> set[str]:
        with self._lock:
            rows = self._c.execute(
                "SELECT detail FROM inventar WHERE art='software' AND detail != ''").fetchall()
        return {r["detail"].lower().rstrip("\\") + "\\" for r in rows}

    # ------------------------------------------------------------------ Meta

    def meta(self, schluessel: str, standard: str = "") -> str:
        with self._lock:
            r = self._c.execute("SELECT wert FROM meta WHERE schluessel = ?",
                                (schluessel,)).fetchone()
        return r["wert"] if r else standard

    def meta_setzen(self, schluessel: str, wert: str) -> None:
        with self._lock:
            self._c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (schluessel, str(wert)))
            self._c.commit()

    # ---------------------------------------------------------- Justierungen

    def justierung_merken(self, wer: str, was: str, alt, neu, begruendung: str) -> None:
        with self._lock:
            self._c.execute("INSERT INTO justierungen VALUES (?,?,?,?,?,?)",
                            (time.time(), wer, was, str(alt), str(neu), begruendung[:500]))
            self._c.commit()

    def justierungen(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._c.execute(
                "SELECT * FROM justierungen ORDER BY zeit DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- Wartung

    def aufraeumen(self, tage: int, max_ereignisse: int) -> int:
        grenze = time.time() - tage * 86400
        with self._lock:
            n = self._c.execute("DELETE FROM ereignisse WHERE zeit < ?", (grenze,)).rowcount
            self._c.execute("DELETE FROM alarme WHERE zeit < ? AND status != 'offen'", (grenze,))
            zuviel = self.anzahl_ereignisse() - max_ereignisse
            if zuviel > 0:
                self._c.execute(
                    "DELETE FROM ereignisse WHERE id IN "
                    "(SELECT id FROM ereignisse ORDER BY zeit ASC LIMIT ?)", (zuviel,))
                n += zuviel
            self._c.commit()
        return n

    def lernen_zuruecksetzen(self) -> None:
        with self._lock:
            self._c.execute("DELETE FROM profile")
            self._c.execute("DELETE FROM inventar")
            self._c.execute("DELETE FROM meta WHERE schluessel LIKE 'training%'")
            self._c.commit()

    def verdichten(self) -> None:
        """WAL-Zwischendatei (wache.db-wal) in die Datenbank übernehmen.

        SQLite macht das von selbst nur, wenn zwischendurch KEINE Verbindung
        liest – im Dienst liest aber ständig jemand (Motor, Tray, Wecker). Ohne
        diesen Anstoß wuchs die Zwischendatei auf mehrere MB. Es geht nichts
        verloren: die Daten wandern nur von der Zwischendatei in die Hauptdatei."""
        with self._lock:
            try:
                self._c.commit()
                self._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass

    def schliessen(self) -> None:
        with self._lock:
            try:
                self._c.commit()
                self._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._c.close()
            except sqlite3.Error:
                pass
