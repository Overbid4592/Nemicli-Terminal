"""
codesammlung.py - Coding-Sammlung: bewährter Code aus dem Coding-Assistenten.

Eine Sammlung für alle Modelle. Lokale Modelle lernen daraus über ein Coding-LoRA
(trainiert nur von einem Cloud-Modell), Cloud-Modelle schlagen beim Coden darin
nach (Werkzeug `coding_sammlung`).

Aufgenommen wird, was bei aktivem Coding-Assistenten mit Freigabe in eine
Code-Datei geschrieben wird (datei_schreiben, datei_bearbeiten). Status:

    fehler     der Sofort-Check meldete Fehler – wird nie verwendet
    sauber     freigegeben, Sofort-Check ohne Befund
    geprueft   danach wurde der Todo-Punkt, an dem gearbeitet wurde, grün
    verworfen  per /undo zurückgenommen

Suche und Training nehmen nur `sauber` und `geprueft`, geprüfte zuerst.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

DB = _ROOT / "learned" / "coding_sammlung.db"
MAX_ZEICHEN = 30_000                     # größere Dateien sind keine guten Lernbeispiele
NUTZBAR = ("sauber", "geprueft")
FEHLER, SAUBER, GEPRUEFT, VERWORFEN = "fehler", "sauber", "geprueft", "verworfen"

SPRACHEN = {
    ".py": "Python", ".pyw": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".ps1": "PowerShell", ".psm1": "PowerShell", ".bat": "Batch", ".cmd": "Batch",
    ".sh": "Shell", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".cs": "C#",
    ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".lua": "Lua", ".sql": "SQL", ".swift": "Swift", ".dart": "Dart",
}
_KENNUNG = {"Python": "python", "JavaScript": "javascript", "TypeScript": "typescript", "HTML": "html",
            "CSS": "css", "PowerShell": "powershell", "Batch": "bat", "Shell": "bash", "C": "c",
            "C++": "cpp", "C#": "csharp", "Java": "java", "Kotlin": "kotlin", "Go": "go", "Rust": "rust",
            "Ruby": "ruby", "PHP": "php", "Lua": "lua", "SQL": "sql", "Swift": "swift", "Dart": "dart"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS beispiele (
    id INTEGER PRIMARY KEY, zeit TEXT, pfad TEXT COLLATE NOCASE, projekt TEXT, datei TEXT,
    sprache TEXT, art TEXT, auftrag TEXT, punkt TEXT, punkt_nr INTEGER, vorher TEXT, nachher TEXT,
    modell TEXT, status TEXT, geprueft TEXT DEFAULT '', sicherung TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_b_status ON beispiele(status);
CREATE INDEX IF NOT EXISTS idx_b_punkt ON beispiele(projekt, punkt_nr);
"""

_lock = threading.Lock()


def _verbinden() -> tuple[sqlite3.Connection, bool]:
    """(Verbindung, Volltextsuche verfügbar)."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.executescript(SCHEMA)
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS suche USING fts5(auftrag, punkt, datei, nachher, "
                  "content='beispiele', content_rowid='id')")
        fts = True
    except sqlite3.OperationalError:
        fts = False
    return c, fts


def sprache(pfad: str | os.PathLike) -> str:
    return SPRACHEN.get(Path(pfad).suffix.lower(), "")


def _jetzt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def aufnehmen(pfad: str | os.PathLike, art: str, vorher: str, nachher: str, sauber: bool,
              sicherung: str | None = None) -> int | None:
    """Freigegebenen Code aufnehmen – nur bei aktivem Coding-Assistenten und in Code-Dateien.
    `sicherung` = Papierkorb-Kopie des Stands davor (für /undo). Gibt die id zurück oder None,
    wenn nichts aufgenommen wurde. Fehler bleiben still."""
    try:
        import coding
        if not coding.aktiv():
            return None
        sp = sprache(pfad)
        if not sp or not str(nachher).strip() or len(nachher) > MAX_ZEICHEN or len(vorher) > MAX_ZEICHEN:
            return None
        projekt = coding.projekt()
        auftrag, punkt, nr = coding.kontext()
        voll = Path(pfad).resolve()
        try:
            datei = str(voll.relative_to(projekt.resolve())) if projekt else voll.name
        except ValueError:
            datei = voll.name
        try:
            import config
            modell = str(config.load().get("model") or "")
        except Exception:
            modell = ""
        with _lock:
            c, fts = _verbinden()
            try:
                bid = c.execute(
                    "INSERT INTO beispiele(zeit, pfad, projekt, datei, sprache, art, auftrag, punkt, punkt_nr, "
                    "vorher, nachher, modell, status, sicherung) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_jetzt(), os.path.normcase(str(voll)), str(projekt or ""), datei, sp, art, auftrag,
                     punkt, nr, vorher, nachher, modell, SAUBER if sauber else FEHLER,
                     sicherung or "")).lastrowid
                if fts:
                    c.execute("INSERT INTO suche(rowid, auftrag, punkt, datei, nachher) VALUES (?,?,?,?,?)",
                              (bid, auftrag, punkt, datei, nachher))
                c.commit()
                return bid
            finally:
                c.close()
    except Exception:
        return None


def punkt_geprueft(projekt: str | os.PathLike, nr: int) -> int:
    """Ein Todo-Punkt wurde grün: seine sauberen Beispiele gelten als geprüft."""
    try:
        with _lock:
            c, _ = _verbinden()
            try:
                n = c.execute("UPDATE beispiele SET status=?, geprueft=? WHERE projekt=? AND punkt_nr=? "
                              "AND status=?", (GEPRUEFT, _jetzt(), str(projekt), int(nr), SAUBER)).rowcount
                c.commit()
                return n
            finally:
                c.close()
    except Exception:
        return 0


def verwerfen(pfad: str | os.PathLike, seit: str, kopie: str = "") -> int:
    """/undo hat `pfad` auf die Sicherung `kopie` (angelegt um `seit`) zurückgesetzt: das Beispiel,
    das diese Sicherung ausgelöst hat, und alles danach zur selben Datei gilt nicht mehr."""
    try:
        with _lock:
            c, _ = _verbinden()
            try:
                norm = os.path.normcase(str(Path(pfad).resolve()))
                ab = c.execute("SELECT MIN(id) FROM beispiele WHERE sicherung=? AND sicherung!=''",
                               (kopie or "",)).fetchone()[0]
                if ab is not None:
                    n = c.execute("UPDATE beispiele SET status=? WHERE pfad=? AND id>=? AND status!=?",
                                  (VERWORFEN, norm, ab, VERWORFEN)).rowcount
                else:
                    n = c.execute("UPDATE beispiele SET status=? WHERE pfad=? AND zeit>=? AND status!=?",
                                  (VERWORFEN, norm, seit or "", VERWORFEN)).rowcount
                c.commit()
                return n
            finally:
                c.close()
    except Exception:
        return 0


def _woerter(text: str) -> list[str]:
    return list(dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß]{2,}", text or "")))[:12]


def suchen(frage: str, anzahl: int = 3, sprache_filter: str = "") -> list[dict]:
    """Bewährte Beispiele zu `frage`, geprüfte zuerst."""
    woerter = _woerter(frage)
    if not woerter or not DB.exists():
        return []
    anzahl = max(1, min(int(anzahl or 3), 8))
    with _lock:
        c, fts = _verbinden()
        c.row_factory = sqlite3.Row
        try:
            bedingung, werte = f"b.status IN {NUTZBAR}", []
            if sprache_filter:
                bedingung += " AND lower(b.sprache)=?"
                werte.append(sprache_filter.lower())
            if fts:
                ausdruck = " OR ".join('"' + w.replace('"', "") + '"' for w in woerter)
                zeilen = c.execute(
                    f"SELECT b.* FROM suche s JOIN beispiele b ON b.id = s.rowid WHERE suche MATCH ? AND "
                    f"{bedingung} ORDER BY (b.status = 'geprueft') DESC, bm25(suche) LIMIT ?",
                    (ausdruck, *werte, anzahl)).fetchall()
            else:
                teile = " OR ".join("(lower(b.auftrag || ' ' || b.punkt || ' ' || b.datei || ' ' || b.nachher) "
                                    "LIKE ?)" for _ in woerter)
                zeilen = c.execute(
                    f"SELECT b.* FROM beispiele b WHERE ({teile}) AND {bedingung} "
                    f"ORDER BY (b.status = 'geprueft') DESC, b.id DESC LIMIT ?",
                    (*[f"%{w}%" for w in woerter], *werte, anzahl)).fetchall()
            return [dict(z) for z in zeilen]
        finally:
            c.close()


def stand() -> dict:
    """{status: n, "sprachen": {sprache: n nutzbar}, "nutzbar": n}."""
    if not DB.exists():
        return {"nutzbar": 0, "sprachen": {}}
    with _lock:
        c, _ = _verbinden()
        try:
            d = dict(c.execute("SELECT status, COUNT(*) FROM beispiele GROUP BY status").fetchall())
            d["sprachen"] = dict(c.execute(
                f"SELECT sprache, COUNT(*) n FROM beispiele WHERE status IN {NUTZBAR} GROUP BY sprache "
                "ORDER BY n DESC").fetchall())
        finally:
            c.close()
    d["nutzbar"] = sum(d.get(s, 0) for s in NUTZBAR)
    return d


def beispiele(nur_geprueft: bool = False) -> list[dict]:
    """Alle nutzbaren Beispiele, älteste zuerst – Grundlage für das Training."""
    if not DB.exists():
        return []
    stati = (GEPRUEFT,) if nur_geprueft else NUTZBAR
    with _lock:
        c, _ = _verbinden()
        c.row_factory = sqlite3.Row
        try:
            platz = ",".join("?" * len(stati))
            return [dict(z) for z in c.execute(
                f"SELECT * FROM beispiele WHERE status IN ({platz}) ORDER BY id", stati)]
        finally:
            c.close()


# ---------------------------------------------------------------------------
# Werkzeug `coding_sammlung`
# ---------------------------------------------------------------------------

def _beispiel_text(b: dict, laenge: int = 2500) -> str:
    kennung = _KENNUNG.get(b["sprache"], "")
    kopf = (f"### Beispiel #{b['id']} · {b['sprache']} · {b['datei']} · "
            + (f"geprüft ({b['geprueft'][:10]})" if b["status"] == GEPRUEFT else "sauber"))
    zeilen = [kopf]
    if b.get("auftrag"):
        zeilen.append(f"Auftrag: {b['auftrag']}")
    if b.get("punkt"):
        zeilen.append(f"Punkt: {b['punkt']}")
    if b["art"] == "bearbeiten" and b.get("vorher"):
        zeilen += ["vorher:", f"```{kennung}", b["vorher"][:laenge // 2], "```", "nachher:"]
    zeilen += [f"```{kennung}", b["nachher"][:laenge] + ("\n… (gekürzt)" if len(b["nachher"]) > laenge else ""),
               "```"]
    return "\n".join(zeilen)


def werkzeug(a: dict) -> str:
    aktion = str(a.get("aktion") or "suchen").strip().lower()
    if aktion == "stand":
        s = stand()
        if not s.get("nutzbar") and not any(s.get(x) for x in (FEHLER, VERWORFEN)):
            return ("Die Coding-Sammlung ist noch leer. Sie füllt sich von selbst, sobald im Coding-Assistenten "
                    "Code geschrieben und freigegeben wird.")
        sprachen = ", ".join(f"{k} {n}" for k, n in s["sprachen"].items()) or "–"
        return (f"Coding-Sammlung: {s['nutzbar']} nutzbare Beispiele ({s.get(GEPRUEFT, 0)} geprüft, "
                f"{s.get(SAUBER, 0)} sauber) · nicht genutzt: {s.get(FEHLER, 0)} mit Fehlern, "
                f"{s.get(VERWORFEN, 0)} verworfen\nSprachen: {sprachen}")
    if aktion != "suchen":
        return "Unbekannte aktion. Erlaubt: suchen, stand."
    frage = str(a.get("frage") or a.get("text") or "").strip()
    if not frage:
        return "`frage` fehlt – wonach suchst du (z. B. „SQLite Tabelle anlegen“, „Flask Login“)?"
    treffer = suchen(frage, a.get("anzahl") or 3, str(a.get("sprache") or ""))
    if not treffer:
        return f"Nichts Passendes in der Coding-Sammlung zu „{frage}“."
    text = ("Bewährter Code aus früheren Projekten (freigegeben, Sofort-Check sauber; „geprüft“ = Todo-Punkt "
            "wurde danach grün). Als Vorlage für Stil und Vorgehen – an das aktuelle Projekt anpassen.\n\n"
            + "\n\n".join(_beispiel_text(b) for b in treffer))
    try:
        import fremddaten
        return fremddaten.rahmen(text, "ausgabe", "Coding-Sammlung")
    except Exception:
        return text
