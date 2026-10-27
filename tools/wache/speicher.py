"""SQLite-Speicher der Wache: Ereignisse, Alarme, Prozess-Profile, Inventar, Meta.

Thread-sicher über EIN Lock und eine Verbindung (die Sensoren schreiben aus
Threads, die Persönlichkeit und /wache lesen). WAL-Modus, damit Lesen nicht auf Schreiben
wartet. Die Prozess-Profile sind das Gedächtnis der Wache: welcher Prozess
läuft üblicherweise woher, unter wem, zu welcher Stunde, mit welchen Kindern
und Zielen – daraus entstehen die „verhält sich untypisch“-Merkmale.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .ereignisse import Alarm, Ereignis

ID_PRAEFIX_AB = 6                 # abgekürzte Alarm-ids (KIs kürzen gern): erst ab so vielen Zeichen, eindeutig
DAEMPFEN_TAGE = 30                # nur so junge harmlos-Urteile dämpfen – keine Freigabe für immer

SCHEMA = """
CREATE TABLE IF NOT EXISTS ereignisse (
    id TEXT PRIMARY KEY, zeit REAL NOT NULL, kategorie TEXT NOT NULL, aktion TEXT NOT NULL,
    schwere INTEGER DEFAULT 0, text TEXT DEFAULT '', prozess TEXT DEFAULT '',
    pid INTEGER DEFAULT 0, ppid INTEGER DEFAULT 0, eltern TEXT DEFAULT '',
    nutzer TEXT DEFAULT '', exe TEXT DEFAULT '', cmdline TEXT DEFAULT '',
    lokal TEXT DEFAULT '', ziel TEXT DEFAULT '', zielport INTEGER DEFAULT 0,
    protokoll TEXT DEFAULT '', datei TEXT DEFAULT '', score REAL DEFAULT 0.0,
    anomalie INTEGER DEFAULT 0, extra TEXT DEFAULT '',
    start REAL DEFAULT 0.0, eltern_start REAL DEFAULT 0.0
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
-- Netz-Auskunft: Besitzer öffentlicher Netze laut Adressregister (RDAP), gemerkt statt jedes Mal gefragt.
CREATE TABLE IF NOT EXISTS netz_besitzer (
    von TEXT NOT NULL, bis TEXT NOT NULL, netz TEXT DEFAULT '', org TEXT DEFAULT '',
    land TEXT DEFAULT '', zeit REAL NOT NULL, PRIMARY KEY (von, bis)
);

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
        # Mit bekanntem Pfad gilt der Pfad: ein fremdes „chrome.exe“ aus Temp ist nicht
        # das Chrome aus Programme. Ohne Pfad (fremder Prozess, keine Rechte) bleibt der Name.
        p = (getattr(ereignis, "exe", "") or ereignis.prozess).lower()
        if ereignis.prozess.lower() in _SKRIPT_HOSTS and ereignis.eltern:
            # Ein Skript-Host ist nur zusammen mit seinem Elternprozess „bekannt“: powershell.exe
            # aus NemiCLI ist etwas anderes als powershell.exe aus winword.exe. Früher galt
            # „powershell.exe = harmlos“ pauschal – nach einem geratenen Urteil.
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
        """Spalten, die später dazukamen, an bestehende Datenbanken anbauen.
        ALTER TABLE hängt hinten an – genau die Reihenfolge, die Alarm.zeile() liefert."""
        ev = {r["name"] for r in self._c.execute("PRAGMA table_info(ereignisse)")}
        for spalte in ("start", "eltern_start"):
            if spalte not in ev:
                self._c.execute(f"ALTER TABLE ereignisse ADD COLUMN {spalte} REAL DEFAULT 0.0")
        da = {r["name"] for r in self._c.execute("PRAGMA table_info(alarme)")}
        for spalte, typ in (("subjekt", "TEXT DEFAULT ''"), ("anzahl", "INTEGER DEFAULT 1"),
                            ("zuletzt", "REAL DEFAULT 0.0")):
            if spalte not in da:
                self._c.execute(f"ALTER TABLE alarme ADD COLUMN {spalte} {typ}")
        self._c.execute("CREATE INDEX IF NOT EXISTS idx_a_regel_subjekt ON alarme(regel, subjekt)")
        if "subjekt" not in da:
            self._subjekte_nachtragen()

    def _subjekte_nachtragen(self) -> None:
        """Alarme aus älteren Datenbanken haben kein Subjekt – aus dem Ereignis dahinter
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
                f"INSERT OR REPLACE INTO ereignisse ({','.join(Ereignis.SPALTEN)}) "
                f"VALUES ({','.join('?' * len(Ereignis.SPALTEN))})",
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
        (Mit ASC LIMIT – bei mehr als `limit` Ereignissen wären die
        neuesten weggefallen und das Modell hätte mit dem ältesten Stand gelernt.)"""
        with self._lock:
            rows = self._c.execute(
                "SELECT * FROM ereignisse WHERE zeit >= ? ORDER BY zeit DESC LIMIT ?",
                (seit, limit)).fetchall()
        return [Ereignis.aus_zeile(r) for r in reversed(rows)]

    def lern_ereignisse(self, seit: float, bis: float, limit: int = 20_000) -> list[Ereignis]:
        """Fürs Training: Ereignisse zwischen `seit` und `bis` ohne alles, was mit einem
        offenen, unklaren oder echten Alarm zusammenhängt.

        Herausgenommen werden: das Ereignis des Alarms, Wiederholungen, die der Motor nur am
        Alarm gezählt hat (`extra.alarm`), Schatten-Funde des Folgen-Netzes (`extra.dl_fund`) und des
        Netzwächters (`extra.netz_fund`),
        die ganze Prozess-Kette dieser Ereignisse und die Prozesse, die darin gestartet wurden.
        Ketten über PID + Startzeit, bei älteren Ereignissen ohne Startzeit über PID +
        Prozessname innerhalb einer Stunde. Ein Ablauf, den noch niemand als harmlos
        beurteilt hat, wird so nie zum gelernten Normalbild (auch kein „nicht voll prüfbares“)."""
        belastete_status = "('offen', 'gesehen', 'unpruefbar', 'echt', 'erledigt')"
        with self._lock:
            rows = self._c.execute(
                "SELECT * FROM ereignisse WHERE zeit >= ? AND zeit < ? ORDER BY zeit DESC LIMIT ?",
                (seit, bis, limit)).fetchall()
            belastet = self._c.execute(
                "SELECT e.id, e.pid, e.start, LOWER(e.prozess) prozess, e.zeit FROM alarme a "
                "JOIN ereignisse e ON a.ereignis_id = e.id "
                f"WHERE a.status IN {belastete_status} AND e.zeit >= ?",
                (seit - 3600,)).fetchall()
            offene_alarme = {r["id"] for r in self._c.execute(
                f"SELECT id FROM alarme WHERE status IN {belastete_status} AND MAX(zeit, zuletzt) >= ?",
                (seit - 3600,))}
        ids = {r["id"] for r in belastet}
        ketten = {(r["pid"], round(r["start"], 3)) for r in belastet if r["pid"] and r["start"]}
        ohne_start: dict[tuple[int, str], list[float]] = {}
        for r in belastet:
            if r["pid"] and not r["start"]:
                ohne_start.setdefault((r["pid"], r["prozess"]), []).append(r["zeit"])
        for r in rows:
            x = r["extra"] or ""
            if '"alarm"' not in x and '"dl_fund"' not in x and '"netz_fund"' not in x:
                continue
            try:
                extra = json.loads(x)
            except ValueError:
                continue
            if extra.get("dl_fund") or extra.get("netz_fund") or extra.get("alarm") in offene_alarme:
                ids.add(r["id"])
                if r["pid"] and r["start"]:
                    ketten.add((r["pid"], round(r["start"], 3)))
        out = []
        for r in reversed(rows):
            if r["id"] in ids or str(r["aktion"] or "").startswith("evtlog_"):   # Protokolle: nur Regeln
                continue
            if r["pid"] and r["start"] and (r["pid"], round(r["start"], 3)) in ketten:
                continue
            if r["ppid"] and r["eltern_start"] and (r["ppid"], round(r["eltern_start"], 3)) in ketten:
                continue
            zeiten = ohne_start.get((r["pid"], (r["prozess"] or "").lower())) if r["pid"] else None
            if zeiten and any(abs(r["zeit"] - z) <= 3600 for z in zeiten):
                continue
            out.append(Ereignis.aus_zeile(r))
        return out

    def anzahl_ereignisse_seit(self, seit: float) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM ereignisse WHERE zeit > ?", (seit,)).fetchone()[0])

    def anzahl_ereignisse(self) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0])

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
        """Alarm per id – oder per eindeutigem Anfang der id (ab ID_PRAEFIX_AB Hex-Zeichen)."""
        aid = str(aid or "").strip()
        with self._lock:
            r = self._c.execute("SELECT * FROM alarme WHERE id = ?", (aid,)).fetchone()
            if r is None and len(aid) >= ID_PRAEFIX_AB and re.fullmatch(r"[0-9a-fA-F]+", aid):
                treffer = self._c.execute("SELECT * FROM alarme WHERE id LIKE ? LIMIT 2",
                                          (aid.lower() + "%",)).fetchall()
                r = treffer[0] if len(treffer) == 1 else None
        return Alarm.aus_zeile(r) if r else None

    def alarm_urteil(self, aid: str, status: str, wer: str, begruendung: str = "") -> bool:
        with self._lock:
            cur = self._c.execute(
                "UPDATE alarme SET status = ?, urteil_von = ?, begruendung = ? WHERE id = ?",
                (status, wer, begruendung[:500], aid))
            self._c.commit()
            return cur.rowcount > 0

    def unklar(self, seit: float) -> int:
        """Alarme seit `seit`, die die Persönlichkeit als unklar beurteilt hat (`gesehen`) – ohne die,
        die die Wache früher nur „an die Persönlichkeit übergeben“ markiert hat."""
        with self._lock:
            return self._c.execute(
                "SELECT COUNT(*) FROM alarme WHERE status = 'gesehen' AND urteil_von NOT IN ('', 'Wache') "
                "AND zeit >= ?", (seit,)).fetchone()[0]

    def urteile_zu(self, regel: str, subjekt: str) -> dict[str, int]:
        """Wie oft Alarme dieser Regel zu diesem Subjekt so beurteilt wurden: {status: anzahl}."""
        with self._lock:
            rows = self._c.execute("SELECT status, COUNT(*) FROM alarme WHERE regel = ? AND LOWER(subjekt) = ? "
                                   "GROUP BY status", (regel, subjekt.lower())).fetchall()
        return {s: int(n) for s, n in rows}

    def alarm_zaehlen(self, aid: str, zeit: float) -> str:
        """Derselbe Alarm kam innerhalb des Cooldowns wieder: Zähler hoch statt neue Zeile – aber
        nur an einem unbeurteilten Alarm. "gezaehlt", "beurteilt" (schon harmlos/echt/erledigt:
        die Wiederholung gehört in einen neuen Alarm) oder "fehlt" (noch nicht geschrieben)."""
        with self._lock:
            cur = self._c.execute("UPDATE alarme SET anzahl = anzahl + 1, zuletzt = ? "
                                  "WHERE id = ? AND status IN ('offen', 'gesehen')", (zeit, aid))
            self._c.commit()
            if cur.rowcount:
                return "gezaehlt"
            da = self._c.execute("SELECT 1 FROM alarme WHERE id = ?", (aid,)).fetchone()
        return "beurteilt" if da else "fehlt"

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

    def gedaempft(self, ab: int = 3, *, mit_schwere: bool = False,
                  jetzt: float | None = None) -> dict[tuple[str, str], int | tuple[int, int]]:
        """(Regel, Subjekt)-Paare, die in den letzten DAEMPFEN_TAGE mindestens `ab`-mal als
        harmlos beurteilt wurden und nie als echt – die meldet der Motor nicht mehr. Ein
        einziges „echt“ hebt das auf, ohne neue Urteile läuft es ab. Nur Mengen-Regeln
        (regeln.MENGENREGELN) außer NIE_GEDAEMPFT; Muster-Regeln werden nie gedämpft.
        `mit_schwere`: Wert (Anzahl, höchste als harmlos beurteilte Schwere)."""
        from .regeln import MENGENREGELN, NIE_GEDAEMPFT
        if ab <= 0:
            return {}
        regeln = sorted(MENGENREGELN - NIE_GEDAEMPFT)
        grenze = (jetzt if jetzt is not None else time.time()) - DAEMPFEN_TAGE * 86400
        with self._lock:
            rows = self._c.execute(
                "SELECT regel, LOWER(subjekt) s, "
                "SUM(status = 'harmlos' AND MAX(zeit, zuletzt) >= ?) harmlos, "
                "MAX(CASE WHEN status = 'harmlos' AND MAX(zeit, zuletzt) >= ? THEN schwere END) schwere, "
                "SUM(status IN ('echt', 'erledigt')) echt "
                f"FROM alarme WHERE subjekt != '' AND regel IN ({','.join('?' * len(regeln))}) "
                "GROUP BY regel, s HAVING harmlos >= ? AND echt = 0",
                (grenze, grenze, *regeln, ab)).fetchall()
        if mit_schwere:
            return {(r["regel"], r["s"]): (int(r["harmlos"]), int(r["schwere"] or 0)) for r in rows}
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

    def ganz_bekannt(self, ereignis) -> bool:
        """Sind ALLE Merkmale des Ereignisses bekannt (Programm UND Ziel bzw. Datei)? Ein bekanntes
        Ziel allein macht ein neues Programm nicht harmlos."""
        schluessel = bekannt_schluessel(ereignis)
        return bool(schluessel) and all(self.bekannt(s) for s, _a, _n in schluessel)

    def programm_bekannt(self, ereignis) -> bool:
        """Leiserstellen bei Mengen-Regeln: Hat das Ereignis ein Programm, muss GENAU dieses bekannt sein
        (ein bekanntes Ziel adelt kein neues Programm); ohne Programm reicht ein bekanntes Merkmal."""
        schluessel = bekannt_schluessel(ereignis)
        prozess = [s for s, art, _n in schluessel if art == "prozess"]
        if prozess:
            return bool(self.bekannt(prozess[0]))
        return any(self.bekannt(s) for s, _a, _n in schluessel)

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

    def fehlalarm_pfade(self) -> set[str]:
        """Programmpfade (klein), deren Alarme als harmlos beurteilt wurden und nie als echt –
        dämpft den ML-Score. Am Pfad, nicht am Namen: ein svchost.exe im Temp-Ordner ist ein
        anderes Programm als das aus System32."""
        with self._lock:
            rows = self._c.execute(
                "SELECT LOWER(e.exe) exe, SUM(a.status = 'harmlos') harmlos, "
                "SUM(a.status IN ('echt', 'erledigt')) echt "
                "FROM alarme a JOIN ereignisse e ON a.ereignis_id = e.id WHERE e.exe != '' "
                "GROUP BY LOWER(e.exe) HAVING harmlos > 0 AND echt = 0").fetchall()
        return {r["exe"] for r in rows}

    def offene_pfade(self) -> set[str]:
        """Programmpfade (klein) mit unbeurteiltem oder nicht voll prüfbarem Alarm – deren Verhalten wird
        nicht ins Profil übernommen, bis jemand belegt geurteilt hat."""
        with self._lock:
            rows = self._c.execute(
                "SELECT DISTINCT LOWER(e.exe) exe FROM alarme a JOIN ereignisse e ON a.ereignis_id = e.id "
                "WHERE a.status IN ('offen', 'gesehen', 'unpruefbar') AND e.exe != ''").fetchall()
        return {r["exe"] for r in rows}

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

    def profil_bekannt(self, name: str) -> bool:
        with self._lock:
            return self._c.execute("SELECT 1 FROM profile WHERE prozess = ?", (name,)).fetchone() is not None

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

    # ------------------------------------------------------------------ Netz

    def besitzer_merken(self, d: dict) -> None:
        with self._lock:
            self._c.execute("INSERT OR REPLACE INTO netz_besitzer VALUES (?,?,?,?,?,?)",
                            (d["von"], d["bis"], d.get("netz", "")[:200], d.get("org", "")[:200],
                             d.get("land", "")[:10], time.time()))
            self._c.commit()

    def besitzer_fuer(self, ip: str, max_alter_s: float = 30 * 86400) -> dict | None:
        """Gemerktes Netz, in dem `ip` liegt (das kleinste, wenn mehrere passen)."""
        import ipaddress
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return None
        with self._lock:
            rows = self._c.execute("SELECT * FROM netz_besitzer WHERE zeit >= ?",
                                   (time.time() - max_alter_s,)).fetchall()
        best, groesse = None, None
        for r in rows:
            try:
                von, bis = ipaddress.ip_address(r["von"]), ipaddress.ip_address(r["bis"])
            except ValueError:
                continue
            if von.version == a.version and von <= a <= bis:
                g = int(bis) - int(von)
                if groesse is None or g < groesse:
                    best, groesse = dict(r), g
        return best

    def netz_name_fuer(self, ip: str) -> str:
        """Zuletzt gestempelter Name (`extra.name`) zu einer Zieladresse."""
        with self._lock:
            r = self._c.execute("SELECT extra FROM ereignisse WHERE ziel = ? AND extra LIKE '%\"name\"%' "
                                "ORDER BY zeit DESC LIMIT 1", (ip,)).fetchone()
        try:
            return str(json.loads(r["extra"]).get("name") or "") if r else ""
        except ValueError:
            return ""

    def netz_programme_fuer(self, ip: str) -> list[dict]:
        with self._lock:
            rows = self._c.execute(
                "SELECT prozess, exe, COUNT(*) n, MIN(zeit) erst, MAX(zeit) zuletzt FROM ereignisse "
                "WHERE kategorie = 'netz' AND aktion = 'conn_open' AND ziel = ? "
                "GROUP BY prozess, exe ORDER BY zuletzt DESC LIMIT 20", (ip,)).fetchall()
        return [{"prozess": r["prozess"], "exe": r["exe"], "anzahl": r["n"], "erstmals": r["erst"],
                 "zuletzt": r["zuletzt"]} for r in rows]

    def netz_verbindungen(self, seit: float, limit: int = 20_000) -> list[dict]:
        """Verbindungen nach draußen seit `seit` – für die Übersicht (/wache netz)."""
        with self._lock:
            rows = self._c.execute(
                "SELECT zeit, prozess, exe, ziel, zielport, extra FROM ereignisse WHERE kategorie = 'netz' "
                "AND aktion = 'conn_open' AND zeit >= ? ORDER BY zeit DESC LIMIT ?", (seit, limit)).fetchall()
        out = []
        for r in rows:
            try:
                x = json.loads(r["extra"]) if r["extra"] else {}
            except ValueError:
                x = {}
            out.append({"zeit": r["zeit"], "prozess": r["prozess"], "exe": r["exe"], "ziel": r["ziel"],
                        "port": r["zielport"], "extra": x})
        return out

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

    HARMLOS_TAGE = 90           # so lange zählen harmlos-Urteile fürs Dämpfen

    def aufraeumen(self, tage: int, max_ereignisse: int) -> int:
        """Alte Ereignisse und Urteile weg. Echte und erledigte Alarme bleiben samt ihrem Ereignis
        (sie sollen sichtbar bleiben), harmlose und nicht voll prüfbare bleiben HARMLOS_TAGE, unklare `tage`."""
        grenze = time.time() - tage * 86400
        behalten = "id NOT IN (SELECT ereignis_id FROM alarme WHERE status IN ('echt', 'erledigt'))"
        with self._lock:
            n = self._c.execute(f"DELETE FROM ereignisse WHERE zeit < ? AND {behalten}", (grenze,)).rowcount
            self._c.execute("DELETE FROM alarme WHERE zeit < ? AND status = 'gesehen'", (grenze,))
            self._c.execute("DELETE FROM alarme WHERE zeit < ? AND status IN ('harmlos', 'unpruefbar')",
                            (time.time() - max(tage, self.HARMLOS_TAGE) * 86400,))
            zuviel = self.anzahl_ereignisse() - max_ereignisse
            if zuviel > 0:
                self._c.execute(
                    "DELETE FROM ereignisse WHERE id IN "
                    f"(SELECT id FROM ereignisse WHERE {behalten} ORDER BY zeit ASC LIMIT ?)", (zuviel,))
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
