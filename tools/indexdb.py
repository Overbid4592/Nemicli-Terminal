"""
indexdb.py - Bibliothekar nach der Chat-Runde.

Ablauf (nicht im Dialog):
  Chat → Chat-Datenbank der Persönlichkeit (chatstore) + learned/memory.json
       → Embedding (CPU) → Vektoren
       → learned/memory.db
       → Encoder bleibt warm, schläft nach 10 min Ruhe von selbst ein

Der Chat spricht nur mit dem Sprachmodell. Hier wird nur gelesen, was schon
auf der Platte liegt. search() lädt nie ein Modell nach – sie hängt im
System-Prompt und darf den Chat unter keinen Umständen warten lassen.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import re
import sqlite3
import threading
import time
from array import array
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

_DB_NEW = _ROOT / "learned" / "memory.db"
_DB_OLD = _ROOT / "learned" / "index.sqlite"
CHUNK = 900
SCHNITT = 2                      # Fassung des Zerschneidens (1: alle 900 Zeichen hart); ältere werden nachgeschnitten
NEU_SCHNEIDEN_JE_LAUF = 2        # so viele alt geschnittene Quellen je Bibliothekar-Lauf
SEARCH_K = 8
BACKFILL_BATCH = 24
UMWANDELN_JE_LAUF = 2000         # alte JSON-Vektoren je Bibliothekar-Lauf in float32 umschreiben
SQL_WARTEN_S = 15.0              # so lange wartet ein Zugriff auf eine gerade schreibende Verbindung
BERICHTE_BEHALTEN = 3            # so viele Berichte bleiben als Datei; ältere leben nur noch als Vektoren
VOLLSCAN_BEHALTEN = 2            # davon getrennt: die letzten Vollscan-Berichte
EMBED_BATCH = 16                 # so viele Brocken je Encoder-Aufruf – dazwischen wird Fortschritt gemeldet
BERICHTE_JE_RUNDE = 2            # Berichte je Bibliothekar-Lauf (ein Wache-Bericht = bis zu 60 Brocken ≈ 90 s CPU)
WISSEN_JE_RUNDE = 3              # geänderte Wissen-Dateien je Bibliothekar-Lauf
NACHRECHNEN_STUECK = 256          # /einlesen schreibt nach so vielen Brocken: der Balken läuft leer, ein Abbruch kostet wenig
NACHZUEGLER_CHATS = 3            # so viele zuletzt benutzte andere Chats holt der Bibliothekar nach

# Unterbrechen: Beginnt eine neue Runde, hört after_turn nach dem laufenden Stapel auf –
# der Chat bekommt Encoder und CPU. Was fehlt, folgt im nächsten Lauf (Brocken ohne Vektor
# holt nachvektorisieren nach). Ein Zähler statt eines Schalters: niemand muss zurücksetzen.
_unterbrechung = 0
_lauf_marke: int | None = None   # Zählerstand beim Start des laufenden after_turn (None = keiner läuft)
SIGNAL_DATEI: Path | None = None  # Bibliothekar im eigenen Prozess (bibkind): Unterbrechen über diese Datei
_chat_stand: dict[str, int] = {}  # Chat-Ref → Token beim letzten Einlesen (in dieser Sitzung)
_lauf = threading.local()         # nur_text: dieser Lauf sammelt ohne Modell (Brocken landen ohne Vektor im Speicher)
_nachgeholt: dict[str, str] = {}    # Chat-Ref → Stand („aktualisiert“) beim letzten Nachholen

# Fortschritt nach außen (Balken in der Statuszeile). after_turn(status=fn) setzt den Melder; die Einbett-Stellen
# rufen _melden("Chat #152 · Brocken 12/40"). Ein „i/n“ in der Meldung wird in der Oberfläche
# zum Balken (ui.progress_panel). Ohne Melder passiert nichts – Tests und Skripte merken nichts.
_status = None
_kontext = ""


def unterbrechen() -> None:
    """Neue Runde beginnt: ein laufender Bibliothekar hört nach dem aktuellen Stapel auf."""
    global _unterbrechung
    _unterbrechung += 1
    if SIGNAL_DATEI is not None:
        try:
            SIGNAL_DATEI.touch()
        except OSError:
            pass


def _unterbrochen() -> bool:
    """Soll der laufende Lauf aufhören?"""
    stopp = getattr(_lauf, "stopp", None)          # entpackt_einlesen: Abbruch aus dem Fenster
    if stopp is not None and stopp.is_set():
        return True
    if _lauf_marke is None:
        return False
    return _lauf_marke != _unterbrechung or (SIGNAL_DATEI is not None and SIGNAL_DATEI.exists())


def _melden(text: str) -> None:
    if _status is not None:
        try:
            _status(text)
        except Exception:
            pass


def _db_path() -> Path:
    """learned/memory.db; alte index.sqlite wird einmal umbenannt."""
    if _DB_NEW.exists():
        return _DB_NEW
    if _DB_OLD.exists():
        try:
            _DB_OLD.rename(_DB_NEW)
            return _DB_NEW
        except Exception:
            return _DB_OLD
    return _DB_NEW


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    vorhanden = path.is_file()
    con = sqlite3.connect(str(path), timeout=SQL_WARTEN_S)
    # WAL: Lesen (Prompt-Suche) und Schreiben (Bibliothekar) blockieren sich nicht gegenseitig.
    # Die Einstellung bleibt in der Datei; /backup kopiert über die Backup-Schnittstelle samt WAL.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    # REPLACE löscht die alte Zeile – ohne diesen Schalter liefe der Lösch-Trigger
    # dafür nicht, und der Stichwort-Index behielte eine Leiche.
    con.execute("PRAGMA recursive_triggers=ON")
    if vorhanden and str(path) in _SCHEMA_FERTIG:      # Tabellen und Nachrüsten nur einmal je Prozess
        return con
    con.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
            hash TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            ref TEXT NOT NULL,
            text TEXT NOT NULL,
            vec TEXT,
            updated TEXT
        )"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ref ON chunks(ref)")
    con.execute("CREATE TABLE IF NOT EXISTS stand (schluessel TEXT PRIMARY KEY, wert TEXT)")
    _nachruesten(con)
    _SCHEMA_FERTIG.add(str(path))
    return con


# Datenbanken, deren Schema in diesem Prozess schon geprüft ist: nach jeder Runde öffnet das Einlesen je
# Notiz eine Verbindung – rund ein Dutzend Schema-Befehle je Notiz kostete bei vielen Notizen Sekunden.
_SCHEMA_FERTIG: set[str] = set()


def _nachruesten(con: sqlite3.Connection) -> None:
    """Datum der Quelle und Stichwort-Index (FTS5) an bestehende Datenbanken anbauen.

    `datum` ist das Datum der QUELLE (Chat, Bericht, Notiz), nicht des Einlesens –
    danach filtert gedaechtnis_suchen („letzte Woche“). `chunks_fts` spiegelt die
    Texte über Trigger; die Stichwortsuche findet exakte Begriffe (KB-Nummern,
    Dateinamen) und arbeitet auch, wenn es keine Vektoren gibt."""
    spalten = {r[1] for r in con.execute("PRAGMA table_info(chunks)")}
    neu_datum = "datum" not in spalten
    if neu_datum:
        con.execute("ALTER TABLE chunks ADD COLUMN datum TEXT DEFAULT ''")
    if "schnitt" not in spalten:                 # Fassung des Zerschneidens, siehe SCHNITT
        con.execute("ALTER TABLE chunks ADD COLUMN schnitt INTEGER DEFAULT 1")
        con.commit()
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_schnitt ON chunks(schnitt, ref)")
    if "vec_modell" not in spalten:
        # Welches Modell den Vektor gerechnet hat. Alles davor war Qwen3-Embedding.
        con.execute("ALTER TABLE chunks ADD COLUMN vec_modell TEXT DEFAULT ''")
        alt = [(_alt_kennung(v), h) for h, v in
               con.execute("SELECT hash, vec FROM chunks WHERE vec IS NOT NULL").fetchall()]
        con.executemany("UPDATE chunks SET vec_modell=? WHERE hash=?", alt)
        con.commit()
    # Abgleich des Such-Index (_zustand_lesen) ohne die Vektoren: datum und vec_modell liegen hinter dem
    # großen vec-Text, ohne diesen Index liest SQLite dafür jede Zeile ganz.
    if "herkunft" not in spalten:
        _herkunft_nachruesten(con)
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_stand ON chunks(hash, vec_modell, datum)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ref_stand ON chunks(ref, vec_modell)")
    # wissen_namen liest nur diesen Index statt der Tabelle mit allen Texten
    con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_kind_ref ON chunks(kind, ref)")
    fts_da = con.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_fts'").fetchone()
    if not fts_da:
        con.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5("
                    "text, content='chunks', content_rowid='rowid', "
                    "tokenize='unicode61 remove_diacritics 2')")
        con.execute("CREATE TRIGGER IF NOT EXISTS chunks_fts_neu AFTER INSERT ON chunks BEGIN "
                    "INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text); END")
        con.execute("CREATE TRIGGER IF NOT EXISTS chunks_fts_weg AFTER DELETE ON chunks BEGIN "
                    "INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text); END")
        con.execute("CREATE TRIGGER IF NOT EXISTS chunks_fts_neu_text AFTER UPDATE OF text ON chunks BEGIN "
                    "INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text); "
                    "INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text); END")
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    if neu_datum:
        _datum_nachtragen(con)
    if neu_datum or not fts_da:
        con.commit()
    # Wissen/: Stand je Datei und die Satz-Fingerabdrücke fürs Entdoppeln (ingest_wissen liest nur Geändertes)
    con.execute("CREATE TABLE IF NOT EXISTS wissen_stand (pfad TEXT PRIMARY KEY, mtime INTEGER, groesse INTEGER, "
                "sha TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS wissen_satz (pfad TEXT NOT NULL, satz TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_wissen_satz ON wissen_satz(pfad)")
    con.commit()


def _herkunft_nachruesten(con: sqlite3.Connection) -> None:
    """Spalte `herkunft` anbauen – nur die Spalte (sofort, auch bei großen Datenbanken). Vorhandene Brocken
    bleiben leer: beim Lesen gilt `herkunft_ableiten`, nachgetragen wird im Bibliothekar (herkunft_nachtragen).
    Ein UPDATE schriebe jede Zeile samt Text und Vektor neu – das hielte den Start minutenlang an."""
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        if "herkunft" not in {r[1] for r in con.execute("PRAGMA table_info(chunks)")}:
            con.execute("ALTER TABLE chunks ADD COLUMN herkunft TEXT DEFAULT ''")
        con.commit()
    except BaseException:
        con.rollback()
        raise


HERKUNFT_NACHTRAGEN_JE_LAUF = 400          # so viele Quellen (refs) trägt ein Bibliothekar-Lauf höchstens nach
HERKUNFT_NACHTRAGEN_JE_SCHUB = 40          # je Schreib-Transaktion: andere Prozesse warten nie lange


def herkunft_ableiten(kind: str, ref: str, text: str) -> str:
    """Herkunft eines einzelnen Brockens ohne gespeicherten Wert (ältere Einträge). Ein Folge-Brocken eines
    Chats („[Nutzer] …“) kann ohne den Brocken davor auch ein Werkzeug-Ergebnis sein: gilt als Werkzeug,
    nie als Nutzer – er kommt so erst nach dem Nachtragen in den Vorgeschmack."""
    kind = kind or "chat"
    if kind in MEDIEN_ARTEN:
        return _medien_herkunft(ref)
    if kind != "chat":
        return _ART_HERKUNFT.get(kind, "datei")
    text = text or ""
    h = _herkunft_reihe("chat", [text])[0]
    if h == "nutzer" and _FOLGE.match(text):
        return "werkzeug"
    return h


def herkunft_nachtragen(max_refs: int = HERKUNFT_NACHTRAGEN_JE_LAUF) -> int:
    """Leere Herkunft vorhandener Brocken nachtragen (Bibliothekar, in kleinen Schüben). Je Quelle in
    Dokument-Reihenfolge, damit Folge-Brocken die Herkunft ihrer Nachricht erben. Gibt die Zahl der Quellen zurück."""
    try:
        con = _connect()
    except sqlite3.Error:
        return 0
    try:
        refs = [r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE herkunft='' OR herkunft IS NULL "
                                          "LIMIT ?", (max(1, int(max_refs)),))]
        try:
            import fremddaten
            marken = list(fremddaten.FREMD_MARKEN)
        except Exception:
            marken = []
        fremd_sql = " OR ".join("instr(text, ?) > 0" for _m in marken) or "0"
        fertig = 0
        for i in range(0, len(refs), HERKUNFT_NACHTRAGEN_JE_SCHUB):
            if _unterbrochen():
                break
            schub = refs[i:i + HERKUNFT_NACHTRAGEN_JE_SCHUB]
            updates: list[tuple[str, int]] = []
            for ref in schub:
                zeilen = con.execute(f"SELECT rowid, kind, substr(text, 1, 200), ({fremd_sql}) FROM chunks "
                                     "WHERE ref=? ORDER BY rowid", [*marken, ref]).fetchall()
                if not zeilen:
                    continue
                kind = zeilen[0][1] or "chat"
                if kind in MEDIEN_ARTEN:
                    werte = [_medien_herkunft(ref)] * len(zeilen)
                else:
                    werte = _herkunft_reihe(kind, [z[2] for z in zeilen], [bool(z[3]) for z in zeilen])
                updates += [(h, z[0]) for h, z in zip(werte, zeilen)]
            con.execute("BEGIN IMMEDIATE")
            try:
                con.executemany("UPDATE chunks SET herkunft=? WHERE rowid=? AND (herkunft='' OR herkunft IS NULL)",
                                updates)
                con.commit()
            except BaseException:
                con.rollback()
                raise
            fertig += len(schub)
        return fertig
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def vec_packen(v) -> bytes:
    """Vektor → float32-Bytes für die Spalte `vec` (vorher JSON-Text: etwa fünfmal größer, und
    jedes Einlesen musste ihn Zahl für Zahl zerlegen)."""
    return array("f", v).tobytes()


def vec_entpacken(roh) -> list[float] | None:
    """Spalte `vec` → Liste; liest float32-Bytes und das alte JSON."""
    if roh is None:
        return None
    if isinstance(roh, (bytes, bytearray, memoryview)):
        a = array("f")
        a.frombytes(bytes(roh))
        return a.tolist()
    return json.loads(roh)


def _alt_kennung(vec_json: str) -> str:
    try:
        import embedder
        return embedder.alte_kennung(vec_entpacken(vec_json))
    except Exception:
        return ""


def _kennung() -> str:
    """Kennung des aktiven Embedding-Modells ('' ohne Modell)."""
    try:
        import memory
        return memory.modell_kennung()
    except Exception:
        return ""


_DATUM_IM_NAMEN = re.compile(r"(20\d\d)-?(\d\d)-?(\d\d)(?:[-_](\d\d)(\d\d))?")


def _datum_fuer(kind: str, ref: str) -> str:
    """Datum der Quelle als 'YYYY-MM-DD HH:MM', leer wenn unbekannt."""
    name = ref.split(":", 1)[1] if ":" in ref else ref
    try:
        if kind == "chat" or kind == "chatmd":
            import chatstore
            nummer = int(re.sub(r"\D", "", name) or 0)
            daten = chatstore.load(nummer) if nummer else None
            if daten and daten.get("updated"):
                return str(daten["updated"])[:16]
        elif kind == "note":
            import memory
            for e in memory.all_entries():
                if str(e.get("id")) == name and e.get("ts"):
                    return str(e["ts"])[:16]
        elif kind == "bericht":
            m = _DATUM_IM_NAMEN.search(name)
            if m:
                j, mo, t, h, mi = m.groups()
                return f"{j}-{mo}-{t} {h or '00'}:{mi or '00'}"
            p = _ROOT / "Berichte" / name
            if p.exists():
                return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        elif kind == "wissen":
            import wissen
            p = wissen.ordner() / name
            if p.exists():
                return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        elif kind in ("skill", "code"):
            import learn
            liste = learn.list_skills() if kind == "skill" else learn.list_snippets()
            for pfad, _t in liste:
                if pfad.name == name:
                    return datetime.fromtimestamp(pfad.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return ""


def _datum_nachtragen(con: sqlite3.Connection) -> None:
    """Einmalig beim Anbau der Spalte: Datum je Quelle, sonst das Einlese-Datum."""
    for kind, ref in con.execute("SELECT DISTINCT kind, ref FROM chunks").fetchall():
        datum = _datum_fuer(kind, ref)
        if datum:
            con.execute("UPDATE chunks SET datum=? WHERE ref=?", (datum, ref))
    con.execute("UPDATE chunks SET datum=COALESCE(updated, '') WHERE datum='' OR datum IS NULL")


def _hash(kind: str, ref: str, text: str) -> str:
    return hashlib.sha1(f"{kind}\n{ref}\n{text}".encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


_SATZENDE = re.compile(r"(?<=[.!?…])\s+")
_SPRECHER = re.compile(r"^(Nutzer|KI|Nemi|Werkzeug|Netz):\s")   # „Nemi“: ältere Einträge
_UEBERSCHRIFT = re.compile(r"^#{1,6}\s+(.+)")

# Herkunft eines Brockens (Spalte `herkunft`). Nur HERKUNFT_PROMPT darf ungefragt in den Systemprompt (guide_block);
# alles andere findet die gezielte Suche. HERKUNFT_FREMD gilt beim Lesen als Inhalt von außen (Netz-Sperre).
# Wissen/ legt der Nutzer selbst an – es bleibt im Vorgeschmack, gerahmt als Daten wie jeder Treffer dort.
HERKUNFT_PROMPT = ("nutzer", "ki", "note", "skill", "wissen")
HERKUNFT_FREMD = ("netz", "export")
_ART_HERKUNFT = {"note": "note", "skill": "skill", "code": "skill", "bericht": "netz", "wissen": "wissen",
                 "export": "export", "chatmd": "datei"}
ERGEBNIS_VORSATZ = "Ergebnis von '"           # Werkzeug-Ergebnisse stehen im Verlauf als Nachricht der Rolle user
_START = re.compile(r"^(Nutzer|KI|Nemi|Werkzeug|Netz): ")
_FOLGE = re.compile(r"^\[(Nutzer|KI|Nemi|Werkzeug|Netz)(?: › [^\]\n]*)?\] ")


def _fremd_markiert(text: str) -> bool:
    try:
        import fremddaten
        return fremddaten.im_text(text)
    except Exception:
        return False


def _herkunft_reihe(kind: str, texte: list[str], fremd: list[bool] | None = None) -> list[str]:
    """Herkunft je Brocken einer Quelle, Brocken in Dokument-Reihenfolge. Bei Chats entscheidet der Vorsatz
    (ingest_chat); Folge-Brocken („[Nutzer] …“) erben die Herkunft ihrer Nachricht. Ältere Einträge mit
    „Nutzer: Ergebnis von '…“ gelten als Werkzeug-Ergebnis. Unklares nie als Nutzer oder KI.
    `fremd`: je Brocken, ob er Netz-Marken trägt (sonst aus dem Text)."""
    if fremd is None:
        fremd = [_fremd_markiert(t) for t in texte]
    if kind != "chat":
        return [_ART_HERKUNFT.get(kind, "datei")] * len(texte)
    raus: list[str] = []
    stand = ""
    for text, markiert in zip(texte, fremd):
        if text.startswith("Chat #"):
            h = "nutzer"                                     # Titelzeile aus der ersten Eingabe
        elif (m := _START.match(text)):
            wer, rest = m.group(1), text[m.end():]
            if wer == "Netz":
                h = "netz"
            elif wer == "Werkzeug" or (wer == "Nutzer" and rest.startswith(ERGEBNIS_VORSATZ)):
                h = "werkzeug"
            elif wer == "Nutzer":
                h = "nutzer"
            else:
                h = "ki"
            if markiert and h in ("nutzer", "werkzeug"):
                h = "netz"
            stand = h
        elif (m := _FOLGE.match(text)):
            wer = m.group(1)
            if wer in ("KI", "Nemi"):
                h = "ki"
            elif wer == "Netz":
                h = "netz"
            elif wer == "Werkzeug":
                h = stand if stand in ("werkzeug", "netz") else "werkzeug"
            else:
                h = stand if stand in ("nutzer", "werkzeug", "netz") else "werkzeug"
            if markiert and h in ("nutzer", "werkzeug"):
                h = "netz"
        else:
            h = "netz" if markiert else "werkzeug"
        raus.append(h)
    return raus


def _medien_herkunft(ref: str) -> str:
    return "netz" if f":{ENTPACKT}/" in str(ref) else "datei"


def _bloecke(text: str) -> list[tuple[str, str]]:
    """Text → (Block, Überschrift darüber). Blöcke sind Absätze; ein Code-Block (```) bleibt ganz,
    auch mit Leerzeilen darin."""
    raus: list[tuple[str, str]] = []
    abschnitt, aktuell, im_code = "", [], False

    def fertig():
        block = "\n".join(aktuell).strip()
        if block:
            raus.append((block, abschnitt))
        aktuell.clear()

    for zeile in text.replace("\r\n", "\n").split("\n"):
        if zeile.lstrip().startswith("```"):
            if not im_code:
                fertig()
            aktuell.append(zeile)
            im_code = not im_code
            if not im_code:
                fertig()
            continue
        if im_code:
            aktuell.append(zeile)
            continue
        if not zeile.strip():
            fertig()
            continue
        if (m := _UEBERSCHRIFT.match(zeile.strip())):
            fertig()
            abschnitt = m.group(1).strip()[:80]
        aktuell.append(zeile)
    fertig()
    return raus


def _teilen(block: str, platz: int) -> list[str]:
    """Ein zu langer Block: an Satzenden, Code an Zeilen, überlange Stücke hart."""
    stuecke = block.split("\n") if block.lstrip().startswith("```") else _SATZENDE.split(block)
    raus, aktuell = [], ""
    for s in stuecke:
        while len(s) > platz:
            if aktuell:
                raus.append(aktuell)
                aktuell = ""
            raus.append(s[:platz])
            s = s[platz:]
        trenner = "\n" if block.lstrip().startswith("```") else " "
        if aktuell and len(aktuell) + 1 + len(s) > platz:
            raus.append(aktuell)
            aktuell = ""
        aktuell = f"{aktuell}{trenner}{s}" if aktuell else s
    if aktuell:
        raus.append(aktuell)
    return raus


def _pieces(text: str) -> list[str]:
    """Text → Brocken bis CHUNK Zeichen, an Absätzen, Sätzen und Code-Blöcken geschnitten. Folge-Brocken
    tragen vorn, woher sie stammen ([Sprecher] oder [erste Zeile › Überschrift]) – so findet und versteht
    man sie auch allein. Den Zusammenhang davor und danach liefert die Suche als Nachbarn."""
    text = (text or "").strip()
    if len(text) < 20:
        return []
    if len(text) <= CHUNK:
        return [text]
    erste = text.split("\n", 1)[0].strip()
    if (m := _SPRECHER.match(text)):
        kopf = m.group(1)
    elif len(erste) <= 80 and "\n" in text and not _SATZENDE.search(erste):
        kopf = erste                                     # Dateiname, Chat-Titel
    else:
        kopf = ""
    raus: list[str] = []
    aktuell, abschnitt = "", None

    def vorsatz(ueber: str) -> str:
        marke = " › ".join(x for x in (kopf, ueber) if x)
        return f"[{marke}] " if marke else ""

    for block, ueber in _bloecke(text):
        neuer_abschnitt, abschnitt = ueber != abschnitt, ueber
        vorn = vorsatz("" if block.startswith("#") else ueber)   # die Überschrift steht dann selbst da
        platz = max(200, CHUNK - len(vorn))
        for t in ([block] if len(block) <= platz else _teilen(block, platz)):
            if aktuell and (neuer_abschnitt or len(aktuell) + 2 + len(t) > CHUNK):
                raus.append(aktuell)
                aktuell = ""
            neuer_abschnitt = False
            if aktuell:
                aktuell = f"{aktuell}\n\n{t}"
            else:
                aktuell = (vorn if raus else "") + t     # der erste Brocken beginnt mit dem Text selbst
    if aktuell:
        raus.append(aktuell)
    return [p.strip() for p in raus if len(p.strip()) >= 20]


def _msg_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text") or "")
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts).strip()
    return str(c or "").strip()


def _upsert(kind: str, ref: str, texts: list[str], datum: str | None = None) -> int:
    """Texte einer Quelle zerschneiden und speichern – in Dokument-Reihenfolge (die Suche liefert die
    Nachbarn eines Treffers mit). `datum`: Datum der Quelle, wenn schon bekannt. Rückgabe: neu
    eingebettete Brocken."""
    pieces = []
    for t in texts:
        pieces.extend(_pieces(t))
    if not pieces:
        return 0
    return _upsert_geordnet(kind, ref, pieces, datum) or 0


def _gpu_vorrat_leeren() -> None:
    try:
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


#: Arten mit einem natürlichen Titel (Dateiname) – das Embedding-Modell bekommt ihn im Dokument-Vorsatz
_TITEL_ARTEN = ("skill", "code", "bericht", "wissen")


def _titel(kind: str, ref: str) -> str:
    """Titel eines Brockens für den Dokument-Vorsatz: Dateiname ohne Endung, sonst leer („none“)."""
    if kind not in _TITEL_ARTEN:
        return ""
    name = str(ref).split(":", 1)[-1].replace("\\", "/").rsplit("/", 1)[-1]
    return Path(name).stem.replace("_", " ").strip()


def _mit_titel(texte: list[str], titel: list[str]) -> tuple:
    """Aufruf-Felder für _einbetten: die Titel nur, wenn es mindestens einen gibt."""
    return (texte, titel) if any(titel) else (texte,)


def _einbetten(texte: list[str], titel: list[str] | None = None) -> list[list[float]] | None:
    """Portionsweise einbetten und dabei Fortschritt melden. None, wenn der Encoder fehlt oder der Lauf
    nur sammelt (`_lauf.nur_text`). Bei Speichermangel halbiert er den Stapel; was gar nicht passt, bleibt im
    Einlese-Speicher. `titel`: je Text ein Titel (oder leer)."""
    if getattr(_lauf, "nur_text", False):
        return None
    try:
        import memory
    except Exception:
        return None
    try:
        if not memory.encoder_loaded():
            wohin = "auf die GPU" if memory.encoder_geraet() == "cuda" else "auf die CPU"
            text = f"lade {memory.modell_name() or 'Embedding-Modell'} {wohin} …"
            _melden(f"{_kontext} · {text}" if _kontext else text)
    except Exception:
        pass
    out: list[list[float]] = []
    n = len(texte)
    # Großer Auftrag? Im Modus „auto“ rechnet er auf der GPU und geht danach zurück in den RAM.
    with memory.auftrag(n) as auf_gpu:
        wo = " auf der GPU" if auf_gpu else ""
        i, stapel = 0, EMBED_BATCH
        while i < n:
            if _unterbrochen():
                return out or None                # Rest landet ohne Vektor, nachvektorisieren holt ihn
            teil = texte[i:i + stapel]
            teil_titel = list(titel[i:i + stapel]) if titel and any(titel[i:i + stapel]) else None
            _melden(f"{_kontext} · Brocken {i}/{n}{wo}" if _kontext else f"Brocken {i}/{n}{wo}")
            try:
                vecs = memory._embed_texts(teil, **({"titel": teil_titel} if teil_titel else {}))
            except Exception:
                vecs = None
            if not vecs:
                if stapel > 1 and memory.encoder_loaded():
                    stapel //= 2
                    _gpu_vorrat_leeren()
                    continue
                return out or None
            out.extend(vecs)
            i += len(teil)
    _melden(f"{_kontext} · Brocken {n}/{n}" if _kontext else f"Brocken {n}/{n}")
    return out


def drop_ref(ref: str) -> None:
    con = _connect()
    try:
        con.execute("DELETE FROM chunks WHERE ref=?", (ref,))
        con.commit()
    finally:
        con.close()
    _invalidate()


def _chat_ref(chat_id: int) -> str:
    try:
        import chatstore
        return chatstore.index_ref(chat_id)
    except Exception:
        return f"chat:{chat_id}"


EXPORT = "export"                  # Chats aus entpackten Export-Archiven: export:<Profil>:<Archiv>/<Chat>


def _profil_arten() -> tuple[str, ...]:
    """Arten, deren Refs <art>:<Profil>:… je Persönlichkeit getrennt sind."""
    return ("chat", "chatmd") + MEDIEN_ARTEN + (EXPORT,)


def _profil_bedingung() -> tuple[str, list]:
    """SQL: Chats, Medien und Export-Chats anderer Persönlichkeiten ausblenden (chat:<Profil>:<Nr>,
    chatmd:<Profil>:…, bild:/video:/ton:<Profil>:…, export:<Profil>:…)."""
    arten = _profil_arten()
    try:
        import profilordner
        eigen = profilordner.aktiv()
    except Exception:
        eigen = None
    if not eigen:                                 # Persönlichkeit unbekannt: nichts Persönliches zeigen
        return " AND ".join(f"c.ref NOT GLOB '{a}:*:*'" for a in arten), []
    return (" AND ".join(f"(c.ref NOT GLOB '{a}:*:*' OR c.ref GLOB ?)" for a in arten),
            [f"{a}:{eigen}:*" for a in arten])


def _aktives_profil() -> str | None:
    try:
        import profilordner
        return profilordner.aktiv()
    except Exception:
        return None


def _fremdes_profil(ref: str, aktiv: str | None = None) -> bool:
    """Chat oder Bild einer anderen Persönlichkeit? `aktiv` vorher einmal holen, wenn viele Refs geprüft werden –
    profilordner.aktiv() liest Einstellungen und Persönlichkeiten von der Platte."""
    teile = str(ref).split(":")
    if teile[0] not in _profil_arten() or len(teile) < 3:
        return False
    aktiv = aktiv or _aktives_profil()
    return aktiv is None or teile[1] != aktiv     # unbekannt: als fremd behandeln


def _profil_sql(profil: str) -> tuple[str, list]:
    """SQL-Bedingung: alle Refs <art>:<profil>:… der Profil-Arten (Groß/klein egal, wie Windows-Ordner)."""
    vorsaetze = [f"{a}:{profil}:".lower() for a in _profil_arten()]
    return ("(" + " OR ".join("lower(substr(ref, 1, ?)) = ?" for _v in vorsaetze) + ")",
            [x for v in vorsaetze for x in (len(v), v)])


def profil_austragen(profil: str, beiseite: Path | None = None) -> int:
    """Gelöschte Persönlichkeit: ihre Chats, Medien und Export-Chats (Refs <art>:<profil>:…) und deren
    Merkzettel aus memory.db austragen – eine spätere gleichnamige Persönlichkeit findet sie nicht.
    `beiseite`: der beiseitegelegte Profil-Ordner; dessen Lehren (note:/detail:) werden ebenfalls ausgetragen,
    soweit sie nicht auch gemeinsam sind. Die Dateien bleiben unberührt. Rückgabe: ausgetragene Brocken."""
    if not profil or not _db_path().exists():
        return 0
    refs: set[str] = set()
    if beiseite is not None:
        try:
            import memory as mem
            gemeinsam = {e["id"] for e in mem._load(mem._FILE)["entries"]}
            gemeinsam_details = {p.stem for p in (mem._FILE.parent / mem.DETAIL_ORDNER).glob("*.md")}
            erinnerungen = Path(beiseite) / "Erinnerungen"
            refs |= {f"note:{e['id']}" for e in mem._load(erinnerungen / "memory.json")["entries"]
                     if e.get("id") not in gemeinsam}
            refs |= {f"detail:{p.stem}" for p in (erinnerungen / mem.DETAIL_ORDNER).glob("*.md")
                     if p.stem not in gemeinsam_details}
        except Exception:
            pass
    bedingung, werte = _profil_sql(profil)
    con = _connect()
    try:
        n = con.execute(f"DELETE FROM chunks WHERE {bedingung}", werte).rowcount
        if refs:
            n += con.executemany("DELETE FROM chunks WHERE ref=?", [(r,) for r in sorted(refs)]).rowcount
        vorsatz = f"{EXPORT}:{profil}:".lower()
        con.execute("DELETE FROM stand WHERE lower(substr(schluessel, 1, ?)) = ?", (len(vorsatz), vorsatz))
        con.commit()
    finally:
        con.close()
    _invalidate()
    return max(0, n)


def profil_umbenennen(alt: str, neu: str) -> int:
    """Umbenannte Persönlichkeit (Profil-Ordner zieht mit): Refs <art>:<alt>:… auf <art>:<neu>:… umschreiben,
    samt Fingerabdruck – die Vektoren bleiben, nichts wird doppelt eingelesen. Liegt eine Quelle unter dem
    neuen Namen schon vor, fällt die alte Fassung weg. Rückgabe: umgeschriebene Brocken."""
    if not alt or not neu or alt == neu or not _db_path().exists():
        return 0
    bedingung, werte = _profil_sql(alt)
    con = _connect()
    n = 0
    try:
        if con.in_transaction:
            con.commit()
        con.execute("BEGIN IMMEDIATE")
        zeilen = con.execute(f"SELECT rowid, kind, ref, text FROM chunks WHERE {bedingung} ORDER BY rowid",
                             werte).fetchall()
        neu_bedingung, neu_werte = _profil_sql(neu)
        neue_refs = {r for (r,) in con.execute(f"SELECT DISTINCT ref FROM chunks WHERE {neu_bedingung}", neu_werte)
                     if r.split(":", 2)[1] == neu}
        weg, um = [], []
        for rowid, kind, ref, text in zeilen:
            art, name, rest = ref.split(":", 2)
            if name == neu:                       # nur Groß/klein geändert: schon unter dem neuen Namen
                continue
            neuer = f"{art}:{neu}:{rest}"
            if neuer in neue_refs:
                weg.append((rowid,))
            else:
                um.append((neuer, _hash(kind, neuer, text), rowid))
        con.executemany("DELETE FROM chunks WHERE rowid=?", weg)
        con.executemany("UPDATE OR REPLACE chunks SET ref=?, hash=? WHERE rowid=?", um)
        vorsatz = f"{EXPORT}:{alt}:"
        for schluessel, wert in con.execute("SELECT schluessel, wert FROM stand WHERE lower(substr(schluessel, 1, ?)) "
                                            "= ?", (len(vorsatz), vorsatz.lower())).fetchall():
            con.execute("DELETE FROM stand WHERE schluessel=?", (schluessel,))
            con.execute("INSERT OR REPLACE INTO stand(schluessel, wert) VALUES (?, ?)",
                        (f"{EXPORT}:{neu}:{schluessel[len(vorsatz):]}", wert))
        con.commit()
        n = len(um)
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    _invalidate()
    return n


def ingest_chat(chat_id: int, title: str, prompts: list[str], messages: list, datum: str | None = None) -> int:
    global _kontext
    _kontext = f"Chat „{title[:24]}“" if title else "Chat"
    texts = []
    if title:
        texts.append(f"Chat #{chat_id}: {title}")
    for p in prompts or []:
        if p and p.strip():
            texts.append(("Netz: " if _fremd_markiert(p) else "Nutzer: ") + p.strip())
    for m in messages or []:
        role = m.get("role") or ""
        body = _msg_text(m)
        if not body:
            continue
        if role == "assistant":
            texts.append("KI: " + body)                  # neutral: gilt für jede Persönlichkeit
        elif _fremd_markiert(body):
            texts.append("Netz: " + body)                # Inhalt aus dem Netz (Web-Werkzeug, Seite aus dem Browser)
        elif role == "user" and not body.startswith(ERGEBNIS_VORSATZ):
            texts.append("Nutzer: " + body)
        else:
            texts.append("Werkzeug: " + body)            # Werkzeug-Ergebnis: steht als user im Verlauf, ist keine Eingabe
    return _upsert("chat", _chat_ref(chat_id), texts, datum)


_ARTEN = {"skill": "Skill", "code": "Snippet", "bericht": "Bericht", "chatmd": "Chat-Mitschrift",
          "wissen": "Wissen"}


def ingest_file(kind: str, path: Path, ref: str | None = None, datum: str | None = None) -> int:
    global _kontext
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    _kontext = f"{_ARTEN.get(kind, kind)} {path.name}"
    return _upsert(kind, ref or f"{kind}:{path.name}", [f"{path.name}\n{text}"], datum)


def ingest_note(note_id: int, text: str, kind: str = "fakt") -> int:
    global _kontext
    _kontext = f"Notiz {note_id}"
    return _upsert("note", f"note:{note_id}", [f"({kind}) {text}"])


def _chat_datum(data: dict) -> str:
    """Datum eines geladenen Chats wie _datum_fuer ('' wenn unbekannt)."""
    return str(data.get("updated") or "")[:16]


def ingest_chat_file(chat_id: int, data: dict | None = None) -> int:
    """Liest einen Chat aus der Chat-Datenbank (und eine alte NNN.md, falls da) und schreibt Vektoren in memory.db.
    `data`: der schon geladene Chat (spart das erneute Laden)."""
    try:
        import chatstore
    except Exception:
        return 0
    if data is None:
        data = chatstore.load(chat_id)
    if not data:
        return 0
    datum = _chat_datum(data)
    n = ingest_chat(
        data.get("id") or chat_id,
        data.get("title") or "",
        data.get("prompts") or [],
        data.get("messages") or [],
        datum,
    )
    try:
        md = chatstore.md_path(chat_id)
        if md.exists():
            teile = chatstore.index_ref(chat_id).split(":")
            n += ingest_file("chatmd", md, f"chatmd:{teile[1]}:{md.name}" if len(teile) == 3 else None, datum)
    except Exception:
        pass
    return n


def _chat_laden(chat_id) -> dict:
    try:
        import chatstore
        return chatstore.load(int(chat_id)) or {}
    except Exception:
        return {}


def _tokens_von(data: dict) -> int:
    return sum(len(_msg_text(m)) for m in data.get("messages") or []) // 4


def chat_tokens(chat_id: int) -> int:
    """Geschätzte Token aller gespeicherten Nachrichten des Chats (~4 Zeichen je Token)."""
    return _tokens_von(_chat_laden(chat_id))


def chat_geaendert(chat_id: int, data: dict | None = None) -> bool:
    """Hat sich der Chat seit dem letzten Einlesen geändert? `data`: schon geladener Chat."""
    if data is None:
        data = _chat_laden(chat_id)
    return _tokens_von(data) != _chat_stand.get(_chat_ref(chat_id), 0)


def chat_einbetten(chat_id: int, data: dict | None = None) -> int:
    """Chat einlesen (Vektoren je nach Lauf) und seinen Stand merken. Geladen wird er dafür nur einmal."""
    if data is None:
        data = _chat_laden(chat_id)
    n = ingest_chat_file(int(chat_id), data) if data else 0
    _chat_stand[_chat_ref(chat_id)] = _tokens_von(data)
    return n


def nachzuegler_chats(aktuell: int | None, anzahl: int = NACHZUEGLER_CHATS) -> int:
    """Die zuletzt benutzten anderen Chats vollständig einlesen – was dort liegen blieb (Chat gewechselt,
    Programm beendet), kommt so nach."""
    try:
        import chatstore
        neueste = [m for m in chatstore.list_chats() if m["id"] != aktuell][:anzahl]   # neueste zuerst
    except Exception:
        return 0
    n = 0
    for meta in neueste:
        if _unterbrochen():
            break
        ref = _chat_ref(meta["id"])
        if _nachgeholt.get(ref) == meta["updated"]:      # seit dem letzten Nachholen unverändert
            continue
        n += chat_einbetten(int(meta["id"]))
        _nachgeholt[ref] = meta["updated"]
    return n


class _NotizenGesperrt(frozenset):
    """Ersatz für _fremde_notizen, wenn sich die Lehren nicht auflisten ließen: jede note:/detail:-Ref gilt als
    fremd, außer den eigenen und gemeinsamen in `erlaubt` (fail-closed)."""

    def __new__(cls, erlaubt=()):
        obj = super().__new__(cls)
        obj.erlaubt = frozenset(erlaubt)
        return obj

    def __contains__(self, ref) -> bool:
        return str(ref).startswith(("note:", "detail:")) and ref not in self.erlaubt


def _eigene_notizen(mem, eigen) -> set[str]:
    """note:/detail:-Refs der gemeinsamen Datei und der eigenen Lehren (ohne Ausnahme: leer)."""
    erlaubt: set[str] = set()
    for datei in (mem._FILE, eigen):
        if datei is None:
            continue
        try:
            erlaubt |= {f"note:{e['id']}" for e in mem._load(datei)["entries"]}
            erlaubt |= {f"detail:{p.stem}" for p in (datei.parent / mem.DETAIL_ORDNER).glob("*.md")}
        except Exception:
            continue
    return erlaubt


def _fremde_notizen(profil: str | None = None) -> frozenset[str]:
    """note:/detail:-Refs, die nur bei einer anderen Persönlichkeit stehen (Lehren sind je Persönlichkeit).
    Leer, wenn das Gedächtnis auf eine einzige Datei umgelenkt ist. `profil`: Ordnername der aktiven
    Persönlichkeit, einmal je Suche geholt (profilordner.aktiv liest von der Platte). Lässt sich das nicht
    feststellen, gilt jede Notiz außer den eigenen und gemeinsamen als fremd (_NotizenGesperrt)."""
    mem, eigen = None, None
    try:
        import memory as mem
        if profil is None:
            eigen = mem._profil_datei()
        elif mem._FILE == mem._STANDARD_FILE:
            import profilordner
            eigen = profilordner.wurzel() / profil / "Erinnerungen" / "memory.json"
        else:
            eigen = None
        if eigen is None:
            return frozenset()
        ids = {e["id"] for d in (mem._FILE, eigen) for e in mem._load(d)["entries"]}
        namen = {p.stem for o in (mem._FILE.parent, eigen.parent) for p in (o / mem.DETAIL_ORDNER).glob("*.md")}
        eigen_ort = os.path.normcase(os.path.abspath(eigen))
        raus: set[str] = set()
        for datei in eigen.parent.parent.parent.glob(f"*/{eigen.parent.name}/{eigen.name}"):
            if os.path.normcase(os.path.abspath(datei)) == eigen_ort:
                continue
            raus |= {f"note:{e['id']}" for e in mem._load(datei)["entries"] if e.get("id") not in ids}
            raus |= {f"detail:{p.stem}" for p in (datei.parent / mem.DETAIL_ORDNER).glob("*.md")
                     if p.stem not in namen}
        return frozenset(raus)
    except Exception:
        try:
            erlaubt = _eigene_notizen(mem, eigen) if mem is not None else set()
        except Exception:
            erlaubt = set()
        return _NotizenGesperrt(erlaubt)


def ingest_all_notes() -> int:
    """Liest learned/memory.json und legt Notiz-Vektoren in memory.db."""
    try:
        import memory as mem
    except Exception:
        return 0
    entries = mem.all_entries()
    details = mem.details() if hasattr(mem, "details") else []
    live = {f"note:{e['id']}" for e in entries} | {f"detail:{name}" for name, _ in details}
    fremd = _fremde_notizen()                      # bleiben liegen: sonst nach jedem Wechsel neu einbetten
    con = _connect()
    try:
        drin = _hashes_je_ref(con, "note")
    finally:
        con.close()
    for ref in drin:
        if ref not in live and ref not in fremd:
            drop_ref(ref)
    quellen: list[tuple[str, str, str]] = []       # (ref, Text, Kontext der Fortschrittsmeldung)
    for e in entries:
        text = e.get("text") or ""
        if e.get("detail"):
            text += f" (📎 detail:{e['detail']})"
        quellen.append((f"note:{e['id']}", f"({e.get('kind') or 'fakt'}) {text}", f"Notiz {e['id']}"))
    for name, pfad in details:                     # die Details der Notizen: ref detail:<name>
        try:
            inhalt = pfad.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        quellen.append((f"detail:{name}", f"{pfad.name}\n{inhalt}", f"Details {pfad.name}"))
    return sum(_wenn_geaendert("note", ref, text, drin, kontext) for ref, text, kontext in quellen)


def _hashes_je_ref(con: sqlite3.Connection, *kinds: str) -> dict[str, list[tuple[str, int]]]:
    """ref → [(hash, schnitt)] in Dokument-Reihenfolge, für alle Brocken der Arten in einer Abfrage."""
    raus: dict[str, list[tuple[str, int]]] = {}
    for ref, h, s in con.execute(f"SELECT ref, hash, schnitt FROM chunks WHERE kind IN ({','.join('?' * len(kinds))}) "
                                 "ORDER BY rowid", kinds):
        raus.setdefault(ref, []).append((h, s))
    return raus


def _wenn_geaendert(kind: str, ref: str, text: str, drin: dict, kontext: str) -> int:
    """Wie _upsert für einen Text, aber eine unveränderte Quelle (gleiche Hashes und Schnitt-Fassung in `drin`,
    siehe _hashes_je_ref) kostet keine Abfrage. Rückgabe: neu eingebettete Brocken."""
    global _kontext
    pieces = _pieces(text)
    if not pieces or drin.get(ref) == [(h, SCHNITT) for h, _p in _reihe(kind, ref, pieces)]:
        return 0
    _kontext = kontext
    return _upsert_geordnet(kind, ref, pieces) or 0


def ingest_learned() -> int:
    """Liest Skills und Code-Snippets von der Platte in memory.db; geschrieben wird nur Geändertes."""
    try:
        import learn
        dateien = [("skill", p) for p, _t in learn.list_skills()] + [("code", p) for p, _t in learn.list_snippets()]
        con = _connect()
        try:
            drin = _hashes_je_ref(con, "skill", "code")
        finally:
            con.close()
    except Exception:
        return 0
    n = 0
    for kind, path in dateien:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            n += _wenn_geaendert(kind, f"{kind}:{path.name}", f"{path.name}\n{text}", drin,
                                 f"{_ARTEN.get(kind, kind)} {path.name}")
        except Exception:
            continue
    return n


def notiz_vektoren() -> dict:
    """Notiz-Nr → normierter Vektor des aktiven Modells (erster Brocken von note:<Nr>) aus memory.db."""
    kennung = _kennung()
    if not kennung or not _db_path().exists():
        return {}
    try:
        import numpy as np
    except Exception:
        np = None
    con = _connect()
    try:
        zeilen = con.execute("SELECT ref, vec FROM chunks WHERE kind='note' AND ref GLOB 'note:*' AND vec_modell=? "
                             "AND vec IS NOT NULL ORDER BY rowid", (kennung,)).fetchall()
    finally:
        con.close()
    raus: dict = {}
    for ref, roh in zeilen:
        nr = ref.split(":", 1)[1]
        if not nr.isdigit() or int(nr) in raus:
            continue
        if (v := _vektor(roh, np)) is not None:
            raus[int(nr)] = v
    return raus


def _ref_vollstaendig(ref: str) -> bool:
    """Hat jeder Brocken dieser Quelle einen Vektor? (Ohne Encoder landen Brocken
    erst mal ohne Vektor in der DB – dann darf die Datei nicht weg.)"""
    con = _connect()
    try:
        r = con.execute("SELECT COUNT(*), SUM(vec_modell != ?) FROM chunks INDEXED BY idx_chunks_ref_stand "
                        "WHERE ref=?", (_kennung(), ref)).fetchone()
    finally:
        con.close()
    return bool(r and r[0] and not r[1])


def ohne_vektor_token(bild_token: int = 800) -> int | None:
    """Füllstand des Einlese-Speichers: Token aller Brocken ohne Vektor des aktiven Modells (Text: Zeichen / 4,
    Bild, Video und Ton pauschal). Medien nur die der aktiven Persönlichkeit – die anderen bettet das Einlesen erst ein, wenn
    ihre Persönlichkeit aktiv ist, sie blieben sonst als Rest im Stand. None ohne Embedding-Modell."""
    kennung = _kennung()
    if not kennung:
        return None
    try:
        import profilordner
        profil = profilordner.aktiv()
    except Exception:
        profil = "*"
    medien = "kind IN (" + ",".join(f"'{a}'" for a in MEDIEN_ARTEN) + ")"
    eigene = " OR ".join("ref GLOB ?" for _a in MEDIEN_ARTEN)
    con = _connect()
    try:
        bilder, zeichen = con.execute(
            f"SELECT COALESCE(SUM({medien}), 0), COALESCE(SUM(CASE WHEN {medien} THEN 0 ELSE LENGTH(text) END), 0) "
            f"FROM chunks INDEXED BY idx_chunks_stand WHERE vec_modell != ? AND vec_modell != ? "
            f"AND (NOT {medien} OR {eigene})",
            (kennung, kennung + FEHLGESCHLAGEN, *[f"{a}:{profil}:*" for a in MEDIEN_ARTEN])).fetchone()
    finally:
        con.close()
    return int(zeichen) // 4 + int(bilder) * bild_token


def _chat_nachrechnen(ref: str) -> int:
    """Brocken eines Chats ohne passenden Vektor jetzt einbetten (Kontext aufgeräumt: gleich nach Bedeutung findbar)."""
    kennung = _kennung()
    if not kennung:
        return 0
    con = _connect()
    try:
        rows = con.execute("SELECT hash, text FROM chunks WHERE ref=? AND vec_modell != ?", (ref, kennung)).fetchall()
    finally:
        con.close()
    if not rows:
        return 0
    _lauf.nur_text = False
    try:
        vecs = _einbetten([t for _h, t in rows])
    finally:
        _lauf.nur_text = True
    if not vecs:
        return 0
    con = _connect()
    try:
        con.executemany("UPDATE chunks SET vec=?, vec_modell=? WHERE hash=?",
                        [(vec_packen(v), kennung, h) for (h, _t), v in zip(rows, vecs)])
        con.commit()
    finally:
        con.close()
    _invalidate()
    return len(vecs)


def _block_einlesen() -> int:
    """Den abgetrennten Block abarbeiten: alles bis zur jetzt letzten Zeile. Was der Chat-Prozess währenddessen
    sammelt, liegt dahinter und gehört zum nächsten Block. Geschrieben wird stückweise."""
    kennung = _kennung()
    if not kennung:
        return 0
    import memory
    ohne_bilder = _ohne_medien_sql()
    con = _connect()
    try:
        grenze = int(con.execute("SELECT COALESCE(MAX(rowid), 0) FROM chunks").fetchone()[0])
        gesamt = int(con.execute("SELECT COUNT(*) FROM chunks INDEXED BY idx_chunks_stand "
                                 f"WHERE vec_modell != ? AND rowid <= ?{ohne_bilder}", (kennung, grenze)).fetchone()[0])
    finally:
        con.close()
    _lauf.nur_text = False
    n = 0
    if gesamt:
        with memory.auftrag(gesamt):
            while (neu := nachvektorisieren(NACHRECHNEN_STUECK, f"Einlesen {n}/{gesamt}", grenze)):
                n += neu
    n += ingest_medien(None)
    ingest_berichte(je_runde=None)                # jetzt mit Vektoren: alte Berichte dürfen in den Papierkorb
    return n


def nachvektorisieren(limit: int = BACKFILL_BATCH, kontext: str = "Nachzügler ohne passenden Vektor",
                      bis: int | None = None) -> int:
    """Brocken ohne Vektor (noch im Einlese-Speicher, Encoder war nicht da) oder mit Vektor eines anderen
    Modells (Modell gewechselt) nachträglich einbetten. `bis`: nur bis zu dieser Zeile (abgetrennter Block)."""
    kennung = _kennung()
    if not kennung:
        return 0
    ohne_bilder = _ohne_medien_sql()               # die bettet ingest_medien als Medium ein
    con = _connect()
    bis_sql = "" if bis is None else f" AND rowid <= {int(bis)}"
    try:
        rows = con.execute("SELECT hash, text, kind, ref FROM chunks WHERE rowid IN (SELECT rowid FROM chunks "
                           f"INDEXED BY idx_chunks_stand WHERE vec_modell != ?{ohne_bilder}{bis_sql} LIMIT ?)",
                           (kennung, limit)).fetchall()
    finally:
        con.close()
    if not rows:
        return 0
    global _kontext
    _kontext = kontext
    vecs = _einbetten(*_mit_titel([t for _h, t, _k, _r in rows], [_titel(k, r) for _h, _t, k, r in rows]))
    if not vecs:
        return 0
    con = _connect()
    try:
        con.executemany("UPDATE chunks SET vec=?, vec_modell=? WHERE hash=?",
                        [(vec_packen(v), kennung, row[0]) for row, v in zip(rows, vecs)])
        con.commit()
    finally:
        con.close()
    _invalidate()
    return len(vecs)


def vektoren_umwandeln(limit: int = UMWANDELN_JE_LAUF) -> int:
    """Alte JSON-Vektoren in float32-Bytes umschreiben, portionsweise. Hash, Modell und Werte
    bleiben gleich – der Such-Index muss nichts neu einlesen."""
    con = _connect()
    n = 0
    try:
        zeilen = con.execute("SELECT rowid, vec FROM chunks WHERE typeof(vec) = 'text' LIMIT ?",
                             (limit,)).fetchall()
        for i in range(0, len(zeilen), 200):
            if _unterbrochen():
                break
            teil = []
            for rowid, roh in zeilen[i:i + 200]:
                try:
                    teil.append((vec_packen(json.loads(roh)), rowid))
                except (ValueError, TypeError):
                    continue
            con.executemany("UPDATE chunks SET vec=? WHERE rowid=?", teil)
            con.commit()
            n += len(teil)
    finally:
        con.close()
    return n


def ingest_berichte(behalten: int | None = None, je_runde: int | None = BERICHTE_JE_RUNDE) -> tuple[int, int]:
    """Berichte/*.md (Wache-Weckrufe, Zeitplan-Aufträge) → Vektoren (kind „bericht“).
    Danach bleiben nur die `behalten` neuesten als Datei liegen; die älteren wandern in
    den Papierkorb (snapshot, 30 Tage) – aber NUR, wenn ihre Vektoren wirklich in der
    DB sind. Ältere Berichte bleiben so übers Gedächtnis findbar, nicht als Datei.
    `je_runde`: höchstens so viele NEUE Berichte einbetten (neueste zuerst) – der Encoder
    braucht auf der CPU ~1,5 s je Brocken, ein dicker Wache-Bericht hat 60. None = alle.
    Rückgabe: (neue Brocken, weggeräumte Dateien)."""
    if behalten is None:
        behalten = BERICHTE_BEHALTEN
        try:
            import config
            behalten = int(config.load().get("berichte_behalten", behalten))
        except Exception:
            pass
    ordner = _ROOT / "Berichte"
    if not ordner.exists():
        return 0, 0
    dateien = sorted((p for p in ordner.glob("*.md") if p.is_file()), key=lambda p: p.stat().st_mtime)
    con = _connect()
    try:
        drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'")}
    finally:
        con.close()
    neu = [p for p in reversed(dateien) if f"bericht:{p.name}" not in drin]     # neueste zuerst
    if je_runde is not None:
        neu = neu[:max(0, je_runde)]
    n = 0
    for i, p in enumerate(neu, 1):
        if _unterbrochen():
            break
        if len(neu) > 1:
            _melden(f"Berichte {i}/{len(neu)} · {p.name}")
        n += ingest_file("bericht", p)
    weg = 0
    # Vollscan-Berichte zählen für sich: sonst schöben drei Weckrufe den letzten Vollscan in den Papierkorb.
    vollscan = [p for p in dateien if p.name.startswith("Vollscan_")]
    uebrige = [p for p in dateien if not p.name.startswith("Vollscan_")]
    alte = (uebrige[:-behalten] if behalten > 0 else uebrige) + vollscan[:-VOLLSCAN_BEHALTEN]
    if alte:
        try:
            import snapshot
        except Exception:
            return n, 0
        for p in alte:
            if not _ref_vollstaendig(f"bericht:{p.name}"):
                continue
            try:
                if snapshot.sichern(p, "berichte", verschieben=True):
                    weg += 1
            except Exception:
                pass
    return n, weg


def _stand(schluessel: str, wert: str | None = None) -> str:
    """Kleiner Merkzettel in memory.db (z. B. Fingerabdruck des Wissen-Ordners)."""
    con = _connect()
    try:
        if wert is not None:
            con.execute("INSERT OR REPLACE INTO stand(schluessel, wert) VALUES (?, ?)", (schluessel, wert))
            con.commit()
            return wert
        r = con.execute("SELECT wert FROM stand WHERE schluessel=?", (schluessel,)).fetchone()
        return r[0] if r else ""
    finally:
        con.close()


def _reihe(kind: str, ref: str, pieces: list[str]) -> list[tuple[str, str]]:
    """(hash, Brocken) in Reihenfolge, gleiche Brocken einer Quelle nur einmal."""
    reihe: list[tuple[str, str]] = []
    gesehen: set[str] = set()
    for p in pieces:
        h = _hash(kind, ref, p)
        if h not in gesehen:
            gesehen.add(h)
            reihe.append((h, p))
    return reihe


def _upsert_geordnet(kind: str, ref: str, pieces: list[str], datum: str | None = None) -> int | None:
    """Wie _upsert, aber die Brocken stehen danach in Dokument-Reihenfolge (rowid),
    so liest gedaechtnis_lesen sie. Vorhandene Vektoren werden übernommen. `datum`: Datum der
    Quelle, wenn schon bekannt (sonst _datum_fuer).
    Rückgabe: neu eingebettete Brocken, None wenn sich nichts geändert hat."""
    reihe = _reihe(kind, ref, pieces)
    herkunft: dict[str, str] = {}
    for p, hk in zip(pieces, _herkunft_reihe(kind, pieces)):   # über alle Brocken: Folge-Brocken erben
        herkunft.setdefault(_hash(kind, ref, p), hk)
    con = _connect()
    try:
        alt = {h: (v, m, u, s) for h, v, m, u, s in con.execute(
            "SELECT hash, vec, vec_modell, updated, schnitt FROM chunks WHERE ref=? ORDER BY rowid", (ref,))}
    finally:
        con.close()
    hashes = [h for h, _p in reihe]
    if list(alt) == hashes:
        if any(s != SCHNITT for _v, _m, _u, s in alt.values()):
            _schnitt_merken(ref)                  # neu geschnitten, gleich geblieben
        return None
    neu = [(h, p) for h, p in reihe if h not in alt]
    if alt and list(alt) == hashes[:len(alt)]:    # nur hinten dazu (ein Chat wächst): anhängen
        return _anhaengen(kind, ref, neu, datum, herkunft)
    vecs = _einbetten(*_mit_titel([p for _h, p in neu], [_titel(kind, ref)] * len(neu))) if neu else None
    kennung = _kennung() if vecs else ""
    neue_vec = {h: vec_packen(vecs[i]) for i, (h, _p) in enumerate(neu) if vecs and i < len(vecs)}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    datum = (_datum_fuer(kind, ref) if datum is None else datum) or now
    rows = []
    for h, p in reihe:
        if h in alt:
            v, m, u, _s = alt[h]
        else:
            v = neue_vec.get(h)
            m, u = (kennung if v else ""), now
        rows.append((h, kind, ref, p, v, u, datum, m, SCHNITT, herkunft.get(h, "datei")))
    con = _connect()
    try:
        con.execute("DELETE FROM chunks WHERE ref=?", (ref,))
        con.executemany(
            "INSERT OR REPLACE INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell, schnitt, herkunft) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    _invalidate()
    return len(neu)


def _anhaengen(kind: str, ref: str, neu: list[tuple[str, str]], datum: str | None = None,
               herkunft: dict[str, str] | None = None) -> int:
    """Neue Brocken hinter die vorhandenen einer Quelle (Reihenfolge bleibt, nichts wird umgeschrieben).
    `herkunft`: Hash → Herkunft (_upsert_geordnet bestimmt sie über die ganze Quelle)."""
    if herkunft is None:
        herkunft = dict(zip((h for h, _p in neu), _herkunft_reihe(kind, [p for _h, p in neu])))
    vecs = _einbetten(*_mit_titel([p for _h, p in neu], [_titel(kind, ref)] * len(neu)))
    kennung = _kennung() if vecs else ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    datum = (_datum_fuer(kind, ref) if datum is None else datum) or now
    rows = []
    for i, (h, p) in enumerate(neu):
        v = vec_packen(vecs[i]) if vecs and i < len(vecs) else None
        rows.append((h, kind, ref, p, v, now, datum, kennung if v else "", SCHNITT, herkunft.get(h, "datei")))
    con = _connect()
    try:
        con.executemany(
            "INSERT OR REPLACE INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell, schnitt, herkunft) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("UPDATE chunks SET datum=? WHERE ref=?", (datum, ref))
        con.commit()
    finally:
        con.close()
    _invalidate()
    return len(neu)


def _schnitt_merken(ref: str) -> None:
    con = _connect()
    try:
        con.execute("UPDATE chunks SET schnitt=? WHERE ref=?", (SCHNITT, ref))
        con.commit()
    finally:
        con.close()


_WISSEN_NAMEN: dict = {}           # Stand der Datenbank -> Namen (der Prompt fragt jede Runde)


def wissen_namen() -> list[str]:
    """Dateien aus Wissen/, die im Gedächtnis sind (Pfad relativ zu Wissen/).
    Legt keine Datenbank an – läuft auch beim Bau des System-Prompts."""
    if not _db_path().exists():
        return []
    stand = (_db_stamp(), str(_db_path()))
    if _WISSEN_NAMEN.get("stand") == stand:
        return list(_WISSEN_NAMEN["namen"])
    con = _connect()
    try:
        refs = [r[0] for r in con.execute(
            "SELECT DISTINCT ref FROM chunks INDEXED BY idx_chunks_kind_ref WHERE kind='wissen'")]
    finally:
        con.close()
    namen = sorted((r.split(":", 1)[1] for r in refs), key=str.casefold)
    _WISSEN_NAMEN.update(stand=stand, namen=namen)
    return list(namen)


def ingest_wissen(je_runde: int | None = WISSEN_JE_RUNDE) -> int:
    """Wissen/ (PDF, Markdown, Text) → Vektoren (kind „wissen“), für alle Persönlichkeiten.

    Gelesen wird nur, was sich geändert hat (Tabelle wissen_stand: Änderungszeit, Größe, Fingerabdruck).
    Entdoppelt wird über alle Dateien (wortgleiche Sätze nur einmal, die älteste Datei behält sie, siehe
    wissen.py) – mit den Satz-Fingerabdrücken aus wissen_satz statt aller Sätze im Speicher. Eine unveränderte
    Datei wird nur neu gelesen, wenn sich für einen ihrer Sätze ändert, ob eine ältere Datei ihn schon hat.
    Gelöschte Dateien verschwinden aus dem Index. `je_runde`: höchstens so viele geschriebene Dateien je Lauf
    (None = alle), der Rest folgt in der nächsten Runde. Rückgabe: neu eingebettete Brocken."""
    global _kontext
    try:
        import wissen
    except Exception:
        return 0
    if _unterbrochen():
        return 0
    basis = wissen.ordner()
    liste = wissen.dateien_stand(basis)                 # älteste zuerst, ein stat je Datei
    verfahren = f"schnitt{SCHNITT}:{getattr(wissen, 'VERFAHREN', '')}:{CHUNK}"
    con = _connect()
    try:
        if (con.execute("SELECT wert FROM stand WHERE schluessel='wissen_verfahren'").fetchone() or ("",))[0] \
                != verfahren:                           # neues Lesen oder Schneiden: alles einmal neu
            con.execute("DELETE FROM wissen_stand")
            con.execute("DELETE FROM wissen_satz")
            con.execute("INSERT OR REPLACE INTO stand(schluessel, wert) VALUES ('wissen_verfahren', ?)", (verfahren,))
            con.commit()
        stand = {p: (m, g, s or "") for p, m, g, s in con.execute("SELECT pfad, mtime, groesse, sha FROM wissen_stand")}
        saetze_alt: dict[str, list[str]] = {}
        for p, s in con.execute("SELECT pfad, satz FROM wissen_satz ORDER BY rowid"):
            saetze_alt.setdefault(p, []).append(s)
        drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks INDEXED BY idx_chunks_kind_ref "
                                          "WHERE kind='wissen'")}
    finally:
        con.close()
    live = {rel: (p, m, g) for p, rel, m, g in liste}
    weg = (set(stand) - set(live)) | {r.split(":", 1)[1] for r in drin} - set(live)
    geaendert = [rel for rel, (_p, m, g) in live.items()
                 if rel not in stand or stand[rel][:2] != (m, g) or not stand[rel][2]]
    if not geaendert and not weg:
        return 0
    for rel in weg:
        if f"wissen:{rel}" in drin:
            drop_ref(f"wissen:{rel}")
    texte: dict[str, str] = {}
    neue_hashes: dict[str, list[str]] = {}
    neuer_stand: dict[str, tuple[int, int, str]] = {}
    neu_inhalt: set[str] = set()
    for rel in geaendert:
        if _unterbrochen():
            return 0                                    # nichts gemerkt: der nächste Lauf liest neu
        p, m, g = live[rel]
        try:
            text = wissen.lesen(p)
        except Exception:
            neuer_stand[rel] = (m, g, "!unlesbar")      # alten Stand behalten, erst nach Änderung neu versuchen
            continue
        sha = hashlib.sha1(text.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:20]
        texte[rel], neue_hashes[rel], neuer_stand[rel] = text, wissen.satz_hashes(text), (m, g, sha)
        if rel not in stand or stand[rel][2] != sha:
            neu_inhalt.add(rel)

    def hashes(rel: str) -> list[str]:
        return neue_hashes[rel] if rel in neue_hashes else saetze_alt.get(rel, [])

    eigner_alt: dict[str, str] = {}                     # Satz → älteste Datei, die ihn hat (vorher)
    for rel in sorted(stand, key=lambda r: (stand[r][0], r.casefold())):
        for h in saetze_alt.get(rel, []):
            eigner_alt.setdefault(h, rel)
    eigner: dict[str, str] = {}                         # … und jetzt
    for rel in live:
        for h in hashes(rel):
            eigner.setdefault(h, rel)
    betroffen = {rel for rel in live if rel not in neu_inhalt and neuer_stand.get(rel, (0, 0, ""))[2] != "!unlesbar"
                 and any((eigner_alt.get(h) == rel) != (eigner.get(h) == rel) for h in hashes(rel))}
    n, geschrieben, erledigt = 0, 0, set()
    for rel in live:                                    # älteste zuerst
        if rel not in neu_inhalt and rel not in betroffen:
            if rel in neuer_stand:                      # nur berührt oder unlesbar: Stand merken
                _wissen_merken(rel, neuer_stand[rel], hashes(rel))
            continue
        if (je_runde is not None and geschrieben >= je_runde) or _unterbrochen():
            for rest in betroffen - neu_inhalt - erledigt:   # unverändert, aber betroffen: im nächsten Lauf neu
                if rest in stand:
                    _wissen_merken(rest, stand[rest][:2] + ("",), saetze_alt.get(rest, []))
            break
        erledigt.add(rel)
        if rel not in texte:
            try:
                texte[rel] = wissen.lesen(live[rel][0])
            except Exception:
                continue
            p, m, g = live[rel]
            sha = hashlib.sha1(texte[rel].encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:20]
            neuer_stand[rel], neue_hashes[rel] = (m, g, sha), wissen.satz_hashes(texte[rel])
        absaetze = wissen.entdoppeln_eine(texte[rel], lambda h, r=rel: eigner.get(h, r) != r)
        stuecke = wissen.brocken(f"📚 {rel}", absaetze, CHUNK)
        ref = f"wissen:{rel}"
        if not stuecke:                                 # leer oder ganz doppelt
            if ref in drin:
                drop_ref(ref)
        else:
            _kontext = f"Wissen {rel}"
            neu = _upsert_geordnet("wissen", ref, stuecke)
            if neu is not None:
                geschrieben += 1
                n += neu
        _wissen_merken(rel, neuer_stand[rel], hashes(rel))
    if weg:
        con = _connect()
        try:
            con.executemany("DELETE FROM wissen_stand WHERE pfad=?", [(r,) for r in weg])
            con.executemany("DELETE FROM wissen_satz WHERE pfad=?", [(r,) for r in weg])
            con.commit()
        finally:
            con.close()
    return n


def _wissen_merken(rel: str, stand: tuple, saetze: list[str]) -> None:
    """Stand einer Wissen-Datei und ihre Satz-Fingerabdrücke speichern."""
    m, g, sha = stand
    con = _connect()
    try:
        con.execute("INSERT OR REPLACE INTO wissen_stand(pfad, mtime, groesse, sha) VALUES (?, ?, ?, ?)",
                    (rel, m, g, sha))
        con.execute("DELETE FROM wissen_satz WHERE pfad=?", (rel,))
        con.executemany("INSERT INTO wissen_satz(pfad, satz) VALUES (?, ?)", [(rel, h) for h in saetze])
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Bilder, Videos und Ton der aktiven Persönlichkeit (Medien-Suche)
# ---------------------------------------------------------------------------
# Kann das Embedding-Modell sehen (EmbeddingGemma 2 + mmproj), bekommt jedes Bild einen Vektor aus Bild und Mal-Prompt,
# jedes Video einen aus seinen Einzelbildern (1 je Sekunde); hat der mmproj einen Audio-Encoder, bekommt Ton (Lieder
# mit Titel und Stil, Stimm-Proben) einen aus dem Klang – alles im selben Raum wie Text, eine Suchfrage findet es nach
# Inhalt. Sonst bleibt der Text (Name, Datum, Prompt, Liedtext) für Stichworte und einen Text-Vektor. Liegt ein Bild
# auch nachgebessert in Gesichtszuege/, zählt nur diese Fassung.

BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_ENDUNGEN = (".mp4", ".mov", ".m4v", ".wmv", ".avi", ".mkv")
TON_ENDUNGEN = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".wma", ".ogg", ".opus")
MEDIEN_ARTEN = ("bild", "video", "ton")
#: Art -> Unterordner der Persönlichkeit (profilordner), aus denen eingelesen wird
MEDIEN_ORDNER = {"bild": ("Bilder",), "video": ("Videos",), "ton": ("Musik", "Stimme")}
#: Entpackte Archive: Profile/<profil>/Entpackt/<Archiv>/<Unterordner>; Refs <art>:<profil>:Entpackt/<Archiv>/<U>/<rel>
ENTPACKT = "Entpackt"
ENTPACKT_MEDIEN = {"bild": ("Bilder",), "video": ("Videos",), "ton": ("Audio",)}
BILDER_JE_RUNDE = 12             # ≈ 45 s Prozessor je Bibliothekar-Lauf (Videos und Ton zählen je Datei mit)
_PROMPT_MAX = 600


def _bilder_sehen() -> bool:
    try:
        import memory
        return memory.kann_bilder()
    except Exception:
        return False


def _ton_hoeren() -> bool:
    try:
        import memory
        return memory.kann_hoeren()
    except Exception:
        return False


def _medien_als_medium() -> tuple[str, ...]:
    """Arten, die das Modell als Medium einbettet (die übrigen bekommen einen Text-Vektor)."""
    sehen = _bilder_sehen()
    return tuple(a for a in MEDIEN_ARTEN if (a == "ton" and sehen and _ton_hoeren()) or (a != "ton" and sehen))


def _ohne_medien_sql() -> str:
    arten = _medien_als_medium()
    return (" AND kind NOT IN (" + ",".join(f"'{a}'" for a in arten) + ")") if arten else ""


def _bild_dateien(wurzel: Path) -> list[Path]:
    try:
        alle = [p for p in wurzel.rglob("*") if p.suffix.lower() in BILD_ENDUNGEN and p.is_file()]
    except OSError:
        return []
    alle = _nur_darunter(alle, wurzel)
    nachgebessert = {p.name for p in alle if p.parent.name == "Gesichtszuege"}
    return sorted((p for p in alle if p.parent.name == "Gesichtszuege" or p.name not in nachgebessert),
                  key=lambda p: p.stat().st_mtime, reverse=True)          # neueste zuerst


#: Endung von vec_modell, wenn ein Medium sich nicht einbetten ließ: bis die Datei sich ändert, kein neuer Versuch
FEHLGESCHLAGEN = ":fehlgeschlagen"


def _medien_ordner(art: str, ref: str) -> str:
    """Unterordner der Persönlichkeit, aus dem ein Medien-Eintrag stammt (entpackt: 'Entpackt/<Archiv>/<U>')."""
    teile = str(ref).split(":", 2)[-1].split("/", 3)
    if len(teile) == 4 and teile[0] == ENTPACKT and teile[2] in ENTPACKT_MEDIEN.get(art, ()):
        return "/".join(teile[:3])
    if art == "bild":
        return "Bilder"
    return teile[0]


def _im_profil(pfad: Path, profil: str) -> bool:
    """Liegt der Ordner wirklich im Ordner der Persönlichkeit (keine Verknüpfung hinaus)?"""
    try:
        import profilordner
        return Path(pfad).resolve().is_relative_to((profilordner.wurzel() / profil).resolve())
    except (OSError, ValueError):
        return False


def _entpackt_ordner(profil: str) -> list[Path]:
    """Die Archiv-Ordner unter Profile/<profil>/Entpackt/, die wirklich dort liegen."""
    try:
        import profilordner
        basis = profilordner.wurzel() / profil / ENTPACKT
        kinder = sorted(p for p in basis.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in kinder if _im_profil(p, profil)]


def _medien_wurzeln(profil: str, art: str, nur: Path | None = None, entpackt: bool = True) -> list[tuple]:
    """(Schlüssel wie _medien_ordner, Ordner, Ref-Vorsatz) für eine Art. `nur`: nur dieses entpackte Archiv."""
    import profilordner
    basis = profilordner.wurzel() / profil
    raus = [] if nur is not None else [(u, basis / u, "" if art == "bild" else u + "/") for u in MEDIEN_ORDNER[art]]
    archive = [basis / ENTPACKT / nur.name] if nur is not None else (_entpackt_ordner(profil) if entpackt else [])
    for archiv in archive:
        for u in ENTPACKT_MEDIEN[art]:
            schluessel = f"{ENTPACKT}/{archiv.name}/{u}"
            raus.append((schluessel, archiv / u, schluessel + "/"))
    return raus


def _medien_dateien(art: str, wurzel: Path) -> list[Path]:
    if art == "bild":
        return _bild_dateien(wurzel)
    endungen = VIDEO_ENDUNGEN if art == "video" else TON_ENDUNGEN
    try:
        alle = [p for p in wurzel.rglob("*") if p.suffix.lower() in endungen and p.is_file()]
    except OSError:
        return []
    return sorted(_nur_darunter(alle, wurzel), key=lambda p: p.stat().st_mtime, reverse=True)


def _nur_darunter(dateien: list[Path], wurzel: Path) -> list[Path]:
    """Nur Dateien, die wirklich im Ordner liegen: Verknüpfungen (Junction, Symlink) dürfen nicht in den Ordner einer
    anderen Persönlichkeit oder anderswohin zeigen."""
    try:
        w = wurzel.resolve()
    except OSError:
        return []
    raus = []
    for p in dateien:
        try:
            if p.resolve().is_relative_to(w):
                raus.append(p)
        except OSError:
            continue
    return raus


def _bild_text(p: Path) -> tuple[str, str]:
    """(Text für Suche und Anzeige, Datum der Datei). Der Mal-Prompt steht bei NemiCLI-Bildern im PNG."""
    text, datum, _teile = _medien_text("bild", p)
    return text, datum


def _medien_text(art: str, p: Path) -> tuple[str, str, list]:
    """(Text für Suche und Anzeige, Datum, Teile für den Medien-Vektor). Bilder: Mal-Prompt aus dem PNG; Lieder:
    Titel, Stil und Liedtext aus dem WAV-INFO-Block (Musik-Motor). Der Vektor sieht das Medium und danach einen kurzen
    Text – Prompt bzw. Titel und Stil, sonst den Dateinamen. Text hebt die Ähnlichkeit zu jeder Frage um rund 0,06;
    bekäme nur ein Teil der Medien Text, lägen die ohne ihn grundlos weiter hinten (gemessen: Medium vor Text trennt
    passend ↔ unpassend am besten)."""
    st = p.stat()
    datum = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    kopf = f"Pfad: {p}\nDatum: {datum} · {st.st_size // 1024} KB"
    if art == "bild":
        prompt = ""
        try:
            from PIL import Image
            with Image.open(p) as img:
                prompt = " ".join(str(img.info.get("prompt") or "").split())[:_PROMPT_MAX]
        except Exception:
            pass
        teile = [("bild", str(p)), ("text", " " + (prompt or _dateiname(p)))]
        return f"Bild {p.name}\n{kopf}" + (f"\nPrompt: {prompt}" if prompt else ""), datum, teile
    if art == "video":
        return f"Video {p.name}\n{kopf}", datum, [("video", str(p)), ("text", " " + _dateiname(p))]
    info = {}
    if p.suffix.lower() == ".wav":
        try:
            from musik import tondatei
            info = tondatei.info(p)
        except Exception:
            info = {}
    titel = " ".join(str(info.get("INAM") or "").split())[:120]
    stil = " ".join(str(info.get("IGNR") or "").split())[:_PROMPT_MAX]
    liedtext = " ".join(str(info.get("ICMT") or "").split())[:_PROMPT_MAX]
    zeilen = [f"Ton {p.name}", kopf] + ([f"Titel: {titel}"] if titel else []) + ([f"Stil: {stil}"] if stil else []) \
        + ([f"Liedtext: {liedtext}"] if liedtext else [])
    teile = [("ton", str(p)), ("text", " " + (" · ".join(x for x in (titel, stil) if x) or _dateiname(p)))]
    return "\n".join(zeilen), datum, teile


def _dateiname(p: Path) -> str:
    """Dateiname als kurzer Text (ohne Endung, Trenner als Leerzeichen)."""
    return " ".join(p.stem.replace("_", " ").replace("-", " ").split()) or p.name


def _vektor_passt(art: str, modell: str, als_medium: tuple, kennung: str) -> bool:
    """Braucht ein unveränderter Eintrag keinen neuen Versuch? (Text-Vektoren holt nachvektorisieren.)"""
    return art not in als_medium or modell in (kennung, kennung + FEHLGESCHLAGEN)


def _kopf_gleich(p: Path, alter_text: str) -> bool:
    """Pfad, Datum und Größe wie beim letzten Einlesen: die Datei ist unverändert, ohne sie zu öffnen."""
    try:
        st = p.stat()
    except OSError:
        return False
    datum = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    return f"Pfad: {p}\nDatum: {datum} · {st.st_size // 1024} KB" in (alter_text or "")


def _medien_offen(profil: str, arten: tuple[str, ...], als_medium: tuple, kennung: str,
                  nur: Path | None = None, entpackt: bool = True) -> tuple[list, list[str], int]:
    """(neue und geänderte Medien, verschwundene Refs, Zahl aller gefundenen Dateien). `nur`: nur dieses
    entpackte Archiv; `entpackt=False`: entpackte Archive weder lesen noch austragen."""
    import profilordner
    entpackt_da = entpackt and (profilordner.wurzel() / profil).is_dir()
    vorsatz_nur = f"{ENTPACKT}/{nur.name}/" if nur is not None else None
    offen: list[tuple[str, str, Path, str, str, str, list]] = []    # (art, ref, pfad, text, datum, hash, teile)
    weg: list[str] = []
    anzahl = 0
    for art in arten:
        con = _connect()
        try:
            alt = {r: (h, m, t) for r, h, m, t in con.execute(
                "SELECT ref, hash, vec_modell, text FROM chunks WHERE kind=? AND ref GLOB ?", (art, f"{art}:{profil}:*"))}
        finally:
            con.close()
        if vorsatz_nur is not None:
            alt = {r: v for r, v in alt.items() if _medien_ordner(art, r).startswith(vorsatz_nur)}
        da, gelesen = set(), set()
        for schluessel, wurzel, vorsatz in _medien_wurzeln(profil, art, nur, entpackt):
            aus_archiv = schluessel.startswith(ENTPACKT + "/")
            if not wurzel.is_dir() or (aus_archiv and not _im_profil(wurzel, profil)):
                continue
            gelesen.add(schluessel)
            for p in _medien_dateien(art, wurzel):
                ref = f"{art}:{profil}:{vorsatz}{p.relative_to(wurzel).as_posix()}"
                if _medien_ordner(art, ref) != schluessel:
                    continue                              # sähe aus wie ein entpacktes Archiv: Refs bleiben eindeutig
                da.add(ref)
                vorher = alt.get(ref)
                if (aus_archiv and vorher and _kopf_gleich(p, vorher[2])
                        and _vektor_passt(art, vorher[1], als_medium, kennung)):
                    continue                              # Archive ändern sich kaum: ohne die Datei zu öffnen
                try:
                    text, datum, teile = _medien_text(art, p)
                except OSError:
                    continue
                h = _hash(art, ref, text)
                if vorher and vorher[0] == h and _vektor_passt(art, vorher[1], als_medium, kennung):
                    continue                              # unverändert – auch nicht erneut, wenn es schon misslang
                offen.append((art, ref, p, text, datum, h, teile))
        anzahl += len(da)
        # nur austragen, was in einem gelesenen Ordner fehlt (ein Laufwerk, das kurz weg ist, löscht nichts);
        # entpackte Archive, deren Ordner fehlt, sind gelöscht, solange der Ordner der Persönlichkeit da ist
        weg += [r for r in alt if r not in da and (
            _medien_ordner(art, r) in gelesen
            or (entpackt_da and _medien_ordner(art, r).startswith(ENTPACKT + "/")))]
    return offen, weg, anzahl


def _austragen(refs: list[str]) -> None:
    if not refs:
        return
    con = _connect()
    try:
        con.executemany("DELETE FROM chunks WHERE ref=?", [(r,) for r in refs])
        con.commit()
    finally:
        con.close()


def _medium_schreiben(eintrag: tuple, als_medium: tuple, kennung: str) -> None:
    """Ein Medium einbetten (als Medium, wenn das Modell es kann) und speichern."""
    art, ref, p, text, datum, h, teile = eintrag
    vec = None
    if art in als_medium:
        vecs = None
        try:
            import memory
            vecs = memory.medien_einbetten([teile])
        except Exception:
            pass
        vec = vecs[0] if vecs else None
    modell = kennung if vec else (kennung + FEHLGESCHLAGEN if art in als_medium else "")
    con = _connect()
    try:
        con.execute("DELETE FROM chunks WHERE ref=?", (ref,))
        con.execute("INSERT OR REPLACE INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell, schnitt, "
                    "herkunft) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (h, art, ref, text, vec_packen(vec) if vec else None,
                     datetime.now().strftime("%Y-%m-%d %H:%M"), datum, modell, SCHNITT, _medien_herkunft(ref)))
        con.commit()
    finally:
        con.close()


_MEDIEN_NAMEN = {"bild": "Bild", "video": "Video", "ton": "Ton"}


def ingest_medien(je_runde: int | None = BILDER_JE_RUNDE, arten: tuple[str, ...] = MEDIEN_ARTEN) -> int:
    """Neue und geänderte Bilder, Videos und Ton der aktiven Persönlichkeit einlesen (auch aus Entpackt/),
    verschwundene austragen. Liest gerade ein anderer Prozess ein (einbett_sperre), bleibt Entpackt/ liegen."""
    try:
        import profilordner
        profil = profilordner.aktiv()
    except Exception:
        return 0
    sammeln = getattr(_lauf, "nur_text", False)                     # beim Sammeln kommt nur der Text
    als_medium = () if sammeln else _medien_als_medium()
    kennung = _kennung() if als_medium else ""
    offen, weg, _anzahl = _medien_offen(profil, arten, als_medium, kennung, entpackt=not _fremd_gesperrt())
    _austragen(weg)
    if je_runde is not None:
        offen = offen[:je_runde]
    global _kontext
    n = 0
    for i, eintrag in enumerate(offen):
        if _unterbrochen():
            break
        _kontext = f"{_MEDIEN_NAMEN[eintrag[0]]} {eintrag[2].name}"
        _melden(f"Medien {i}/{len(offen)}")
        _medium_schreiben(eintrag, als_medium, kennung)
        n += 1
    if n or weg:
        _invalidate()
    return n


def ingest_bilder(je_runde: int | None = BILDER_JE_RUNDE) -> int:
    """Nur die Bilder (ingest_medien)."""
    return ingest_medien(je_runde, ("bild",))


def medien_suchen(frage: str, k: int = 5, arten: tuple[str, ...] = MEDIEN_ARTEN) -> list[dict]:
    """Bilder, Videos oder Ton der aktiven Persönlichkeit zu einer Frage: nach Inhalt (Medien-Vektor), Prompt,
    Titel und Namen. Die Ähnlichkeit entscheidet; reine Stichwort-Treffer folgen danach – Wörter wie „Bild“ stehen
    in jedem Eintrag und würden die Rangliste sonst verwässern."""
    arten = tuple(a for a in arten if a in MEDIEN_ARTEN) or MEDIEN_ARTEN
    hits = suchen(frage, max(3 * k, 15), arten=arten, laden=True, lernen=False, nachsortieren=False)
    hits.sort(key=lambda h: (h.get("aehnlich") is None, -(h.get("aehnlich") or 0.0), -h["score"]))
    return hits[:k]


def bilder_suchen(frage: str, k: int = 5) -> list[dict]:
    """Nur Bilder (medien_suchen)."""
    return medien_suchen(frage, k, ("bild",))


# ---------------------------------------------------------------------------
# Einbett-Sperre über Prozesse: Bibliothekar-Kind (bibkind) und Entpackt-Fenster laden nie beide ein Modell
# ---------------------------------------------------------------------------

SPERRE_WARTEN_S = 600.0            # so lange wartet entpackt_einlesen auf den Bibliothekar, danach nur Text
SPERRE_TAKT_S = 0.5
_sperre_tiefe = 0                  # hält dieser Prozess die Sperre schon? (verschachtelt: frei)


def _sperr_datei() -> Path:
    """Eine Sperr-Datei je Datenbank, im Temp-Ordner (nicht bei den Daten)."""
    import tempfile
    ort = os.path.normcase(os.path.abspath(str(_db_path())))
    kurz = hashlib.sha1(ort.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "NemiCLI" / f"einbetten_{kurz}.sperre"


def _sperre_nehmen(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _sperre_lassen(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def einbett_sperre(warten_s: float = 0.0, stopp=None, beim_warten=None):
    """Prozessübergreifend: nur ein Prozess bettet mit Modell ein. Liefert True, wenn dieser Prozess sie hält.
    `beim_warten` läuft einmal, bevor gewartet wird. Endet der Prozess, gibt das System die Sperre frei."""
    global _sperre_tiefe
    if _sperre_tiefe:
        _sperre_tiefe += 1
        try:
            yield True
        finally:
            _sperre_tiefe -= 1
        return
    try:
        pfad = _sperr_datei()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(pfad), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        fd = None
    frei = fd is None or _sperre_nehmen(fd)                  # ohne Sperr-Datei: kein Schutz, aber kein Stillstand
    if not frei and warten_s > 0 and not (stopp is not None and stopp.is_set()):
        if beim_warten is not None:
            try:
                beim_warten()
            except Exception:
                pass
        ende = time.monotonic() + warten_s
        while not frei and time.monotonic() < ende and not (stopp is not None and stopp.is_set()):
            time.sleep(SPERRE_TAKT_S)
            frei = _sperre_nehmen(fd)
    if frei:
        _sperre_tiefe += 1
    try:
        yield frei
    finally:
        if frei:
            _sperre_tiefe -= 1
        if fd is not None:
            if frei:
                _sperre_lassen(fd)
            os.close(fd)
            if frei and os.name == "nt":
                try:
                    pfad.unlink()               # Windows löscht keine Datei, die ein anderer gerade offen hat
                except OSError:
                    pass


def _fremd_gesperrt() -> bool:
    """Bettet gerade ein anderer Prozess ein (Entpackt-Fenster oder Bibliothekar-Kind)?"""
    with einbett_sperre() as frei:
        return not frei


def _bibliothekar_anhalten() -> None:
    """Laufende Bibliothekar-Kinder hören nach dem laufenden Stapel auf (Signal-Datei, siehe bibkind)."""
    try:
        import bibkind
        ordner = list(bibkind.ordner().parent.glob("bibliothekar_*"))
    except Exception:
        return
    for o in ordner:
        try:
            if o.is_dir():
                (o / "unterbrechen").touch()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Chats aus Export-Archiven (chatexport) in Profile/<profil>/Entpackt/<Archiv>/
# ---------------------------------------------------------------------------

_SATZZEICHEN_VOR_LEER = re.compile(r"[.!?…]+(?=\s|$)")


def _export_text(chat) -> str:
    """Ein Export-Chat als Text: Kopfzeile (Anbieter, Titel, Datum) und je Nachricht ein Abschnitt. Die Kopfzeile
    steht vor jedem Folge-Brocken (_pieces); die Antworten heißen nach dem Anbieter, nicht „KI“."""
    titel = " ".join(str(chat.titel or "").split())
    kurz = _SATZZEICHEN_VOR_LEER.sub("", titel)[:50].strip() or "Ohne Titel"
    kopf = f"{chat.anbieter}-Chat „{kurz}“" + (f" · {chat.datum[:10]}" if chat.datum else "")
    zeilen = [kopf, f"Export aus {chat.anbieter}" + (f", begonnen {chat.datum}" if chat.datum else "")
              + f". Titel: {titel}", ""]
    for rolle, zeit, text in chat.nachrichten:
        wer = "Nutzer" if rolle == "nutzer" else chat.anbieter
        zeilen += [f"## {wer}" + (f" · {zeit}" if zeit else ""), text, ""]
    return "\n".join(zeilen)


def _export_vorsatz(profil: str, archiv: str) -> str:
    return f"{EXPORT}:{profil}:{archiv}/"


def _exporte_lesen(archiv: Path, profil: str) -> tuple[dict[str, tuple[list[str], str, str]], bool]:
    """ref → (Brocken, Datum, Titel) aller Chats eines Archivs; zweiter Wert: alle Export-Dateien lesbar."""
    try:
        import chatexport
    except Exception:
        return {}, False
    fehler: list = []
    live: dict[str, tuple[list[str], str, str]] = {}
    for c in chatexport.lesen(archiv, fehler):
        stuecke = _pieces(_export_text(c))
        if stuecke:
            live[_export_vorsatz(profil, archiv.name) + c.kennung] = (stuecke, c.datum, c.titel)
    return live, not fehler


def _export_stempel(archiv: Path) -> str:
    try:
        import chatexport
        return f"schnitt{SCHNITT}:{chatexport.stempel(archiv)}"
    except Exception:
        return ""


def _export_drin(profil: str) -> dict[str, list[tuple[str, int]]]:
    con = _connect()
    try:
        alle = _hashes_je_ref(con, EXPORT)
    finally:
        con.close()
    vorsatz = f"{EXPORT}:{profil}:"
    return {r: v for r, v in alle.items() if r.startswith(vorsatz)}


def _unveraendert(ref: str, stuecke: list[str], drin: dict) -> bool:
    return drin.get(ref) == [(h, SCHNITT) for h, _p in _reihe(EXPORT, ref, stuecke)]


def ingest_exporte(je_runde: int | None = None, profil: str | None = None) -> int:
    """Chats aus den Export-Archiven der Persönlichkeit (Profile/<profil>/Entpackt/*/JSON) → kind „export“.
    Ein Archiv, dessen Export-Dateien sich nicht geändert haben, wird nicht erneut gelesen; verschwundene Archive
    und Chats werden ausgetragen. `je_runde`: höchstens so viele geänderte Chats (None = alle).
    Rückgabe: neu eingebettete Brocken."""
    global _kontext
    profil = profil or _aktives_profil()
    if not profil or _fremd_gesperrt():           # das Fenster liest gerade ein: nicht dazwischenschreiben
        return 0
    try:
        import profilordner
        if not (profilordner.wurzel() / profil).is_dir():
            return 0                              # Persönlichkeit (oder Laufwerk) gerade nicht da: nichts austragen
    except Exception:
        return 0
    archive = _entpackt_ordner(profil)
    namen = {a.name for a in archive}
    drin = _export_drin(profil)
    for ref in [r for r in drin if r.split(":", 2)[2].split("/", 1)[0] not in namen]:
        drop_ref(ref)
    n, geschrieben = 0, 0
    for archiv in archive:
        if _unterbrochen() or (je_runde is not None and geschrieben >= je_runde):
            break
        schluessel = f"{EXPORT}:{profil}:{archiv.name}"
        stempel = _export_stempel(archiv)
        if stempel == _stand(schluessel):
            continue
        live, lesbar = _exporte_lesen(archiv, profil)
        vorsatz = _export_vorsatz(profil, archiv.name)
        if lesbar:
            for ref in [r for r in drin if r.startswith(vorsatz) and r not in live]:
                drop_ref(ref)
        fertig = True
        for ref, (stuecke, datum, titel) in live.items():
            if _unveraendert(ref, stuecke, drin):
                continue
            if (je_runde is not None and geschrieben >= je_runde) or _unterbrochen():
                fertig = False
                break
            _kontext = f"Export „{titel[:24]}“"
            n += _upsert_geordnet(EXPORT, ref, stuecke, datum or None) or 0
            geschrieben += 1
        if fertig and lesbar:
            _stand(schluessel, stempel)
    return n


def _ref_nachrechnen(ref: str, ohne_arten: tuple[str, ...] = ()) -> int:
    """Brocken einer Quelle ohne Vektor des aktiven Modells jetzt einbetten (Arten in `ohne_arten` nicht)."""
    kennung = _kennung()
    if not kennung or getattr(_lauf, "nur_text", False):
        return 0
    ohne = (" AND kind NOT IN (" + ",".join("?" * len(ohne_arten)) + ")") if ohne_arten else ""
    con = _connect()
    try:
        rows = con.execute(f"SELECT hash, text, kind FROM chunks WHERE ref=? AND vec_modell != ?{ohne} ORDER BY rowid",
                           (ref, kennung, *ohne_arten)).fetchall()
    finally:
        con.close()
    if not rows:
        return 0
    vecs = _einbetten(*_mit_titel([t for _h, t, _k in rows], [_titel(k, ref) for _h, _t, k in rows]))
    if not vecs:
        return 0
    con = _connect()
    try:
        con.executemany("UPDATE chunks SET vec=?, vec_modell=? WHERE hash=?",
                        [(vec_packen(v), kennung, row[0]) for row, v in zip(rows, vecs)])
        con.commit()
    finally:
        con.close()
    return len(vecs)


def _ohne_vektor_unter(vorsaetze: list[str]) -> int:
    """Brocken unter diesen Ref-Anfängen ohne Vektor des aktiven Modells (Fehlversuche bei Medien zählen nicht)."""
    kennung = _kennung()
    bedingung = " OR ".join("substr(ref, 1, ?) = ?" for _v in vorsaetze)
    werte = [x for v in vorsaetze for x in (len(v), v)]
    con = _connect()
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM chunks WHERE ({bedingung}) AND vec_modell != ? "
                               "AND vec_modell != ?", (*werte, kennung, kennung + FEHLGESCHLAGEN)).fetchone()[0])
    finally:
        con.close()


def entpackt_einlesen(ordner: Path, profil: str, fortschritt=None, stopp=None) -> dict:
    """Ein entpacktes Archiv (Profile/<profil>/Entpackt/<Archiv>) jetzt ganz ins Gedächtnis der Persönlichkeit
    `profil` lesen: Bilder, Videos, Audio und Export-Chats, ohne Grenze je Runde, mit Vektoren.

    Für einen eigenen Prozess (Fenster): Das Modell lädt hier nach; läuft gerade ein Bibliothekar-Kind, hört es
    nach seinem Stapel auf (einbett_sperre). Wird die Sperre nicht frei, bleibt es beim Text (Stichworte), die
    Vektoren holt der Bibliothekar nach. `fortschritt(name, erledigt, gesamt)` je Datei und Chat;
    `stopp.is_set()` bricht nach dem laufenden Stück ab – Geschriebenes bleibt, der nächste Lauf macht weiter.
    Rückgabe: eingelesen (erledigte Dateien und Chats, auch unveränderte), gesamt, abgebrochen, neu (geschrieben),
    ohne_vektor (Brocken), fehler (Text, leer wenn keiner)."""
    erg = {"eingelesen": 0, "gesamt": 0, "abgebrochen": False, "neu": 0, "ohne_vektor": 0, "fehler": ""}

    def melden(name: str, erledigt: int, gesamt: int) -> None:
        if fortschritt is not None:
            try:
                fortschritt(name, erledigt, gesamt)
            except Exception:
                pass

    try:
        import profilordner
        profil = profilordner.ordnername(profil)
        basis = profilordner.wurzel() / profil / ENTPACKT
        ordner = Path(ordner)
        archiv = basis / ordner.name
        passt = (ordner.is_dir() and os.path.normcase(str(ordner.parent.resolve())) ==
                 os.path.normcase(str(basis.resolve())) and _im_profil(archiv, profil))
    except Exception:
        passt = False
    if not passt:
        erg["fehler"] = "kein Archiv-Ordner unter Entpackt/ dieser Persönlichkeit"
        return erg
    vorher_stopp, vorher_text = getattr(_lauf, "stopp", None), getattr(_lauf, "nur_text", False)
    _lauf.stopp, _lauf.nur_text = stopp, False
    global _kontext
    try:
        melden("Archiv wird gelesen …", 0, 0)
        als_medium = _medien_als_medium()
        kennung = _kennung()
        offen, weg, anzahl = _medien_offen(profil, MEDIEN_ARTEN, als_medium, kennung, nur=archiv)
        live, lesbar = _exporte_lesen(archiv, profil)
        vorsatz = _export_vorsatz(profil, archiv.name)
        gesamt = anzahl + len(live)
        erledigt = anzahl - len(offen)                       # unveränderte Medien sind schon fertig
        erg["gesamt"] = gesamt
        melden("Gedächtnis wird vorbereitet …", erledigt, gesamt)

        def warten() -> None:
            _bibliothekar_anhalten()
            melden("wartet auf den Bibliothekar …", erledigt, gesamt)

        with einbett_sperre(SPERRE_WARTEN_S, stopp, warten) as frei:
            if not frei:
                _lauf.nur_text = True                        # nur Text: die Vektoren folgen im Bibliothekar
                erg["fehler"] = "Bibliothekar rechnet noch – erst einmal nur Stichworte, Vektoren folgen"
            medium = als_medium if frei else ()
            _austragen(weg)
            drin = _export_drin(profil)
            if lesbar:
                _austragen([r for r in drin if r.startswith(vorsatz) and r not in live])
            ohne_vektor: set[str] = set()                    # schon da, aber ohne Text-Vektor (Abbruch, Sammeln)
            if kennung:
                vorsaetze = [vorsatz] + [f"{a}:{profil}:{ENTPACKT}/{archiv.name}/" for a in MEDIEN_ARTEN
                                         if a not in als_medium]
                con = _connect()
                try:
                    for v in vorsaetze:
                        ohne_vektor |= {r for (r,) in con.execute(
                            "SELECT DISTINCT ref FROM chunks WHERE substr(ref, 1, ?) = ? AND vec_modell != ?",
                            (len(v), v, kennung))}
                finally:
                    con.close()
            for eintrag in offen:
                if _unterbrochen():
                    break
                art, ref, p = eintrag[0], eintrag[1], eintrag[2]
                melden(p.name, erledigt, gesamt)
                _kontext = f"{_MEDIEN_NAMEN[art]} {p.name}"
                _medium_schreiben(eintrag, medium, kennung if medium else "")
                if art not in als_medium:
                    _ref_nachrechnen(ref)                    # Text-Vektor
                erg["neu"] += 1
                erledigt += 1
            for ref in sorted(ohne_vektor - set(live) - {e[1] for e in offen}):
                if _unterbrochen():
                    break
                _ref_nachrechnen(ref)                        # unveränderte Medien: Text-Vektor nachholen
            for ref, (stuecke, datum, titel) in live.items():
                if _unterbrochen():
                    break
                melden(titel, erledigt, gesamt)
                _kontext = f"Export „{titel[:24]}“"
                if not _unveraendert(ref, stuecke, drin):
                    _upsert_geordnet(EXPORT, ref, stuecke, datum or None)
                    erg["neu"] += 1
                    _ref_nachrechnen(ref)                    # was beim Abbruch eines Stapels liegen blieb
                elif ref in ohne_vektor:
                    _ref_nachrechnen(ref)
                erledigt += 1
            erg["abgebrochen"] = erledigt < gesamt and _unterbrochen()
            if not erg["abgebrochen"] and lesbar:
                _stand(f"{EXPORT}:{profil}:{archiv.name}", _export_stempel(archiv))
        erg["eingelesen"] = erledigt
        erg["ohne_vektor"] = _ohne_vektor_unter([vorsatz] + [f"{a}:{profil}:{ENTPACKT}/{archiv.name}/"
                                                             for a in MEDIEN_ARTEN])
        if not lesbar and not erg["fehler"]:
            erg["fehler"] = "eine Export-Datei war nicht lesbar"
        melden("abgebrochen" if erg["abgebrochen"] else "fertig", erledigt, gesamt)
    finally:
        _lauf.stopp, _lauf.nur_text = vorher_stopp, vorher_text
        _kontext = ""
        _invalidate()
    return erg


def after_turn(chat_id: int | None = None, status=None, alles: bool = False,
               chat_erzwingen: bool = False, nur_kurz: bool = False) -> int:
    """Bibliothekar: Quellen lesen → memory.db. Ohne `alles` sammelt er nur, ohne Modell: Die Brocken landen ohne
    Vektor im Einlese-Speicher (einlesen.py), die Stichwortsuche findet sie sofort.

    chat_id:  der gerade gespeicherte Chat (None = nur Notizen/Skills).
    status:   Melder für den Fortschritt (Text mit „i/n“ → Balken in der Oberfläche).
    alles:    /einlesen: erst alles sammeln, das trennt den Block ab; dann ohne Unterbrechen seine Vektoren.
    chat_erzwingen: Kontext aufgeräumt: der laufende Chat bekommt gleich seine Vektoren.
    nur_kurz: beim Sammeln nur Chat, Notizen und Skills; Berichte, Wissen und Bilder lesen die seltenen Läufe.
    Beginnt währenddessen eine Runde (unterbrechen), bleiben die Rückstände für später."""
    global _status, _kontext, _lauf_marke
    _status, _kontext = status, ""
    _lauf_marke = None if alles else _unterbrechung
    _lauf.nur_text = True
    n = 0
    try:
        if chat_id:
            daten = _chat_laden(chat_id)
            if alles or chat_erzwingen or chat_geaendert(chat_id, daten):
                n += chat_einbetten(int(chat_id), daten)
            if chat_erzwingen and not alles:
                n += _chat_nachrechnen(_chat_ref(chat_id))
        n += ingest_all_notes()
        n += ingest_learned()
        rueckstand = [] if nur_kurz else [
            lambda: nachzuegler_chats(chat_id),
            lambda: backfill(10_000 if alles else BACKFILL_BATCH, ausser=None if alles else chat_id),
            lambda: ingest_berichte(je_runde=None)[0],
            lambda: ingest_wissen(None),
            lambda: ingest_medien(None),
            lambda: ingest_exporte(None),
            lambda: neu_schneiden(1_000_000 if alles else NEU_SCHNEIDEN_JE_LAUF),
            lambda: vektoren_umwandeln(1_000_000 if alles else UMWANDELN_JE_LAUF) and 0,   # zählt nicht als neu
            lambda: herkunft_nachtragen() and 0,                                         # ältere Brocken, in Schüben
            lambda: (pflege(), 0)[1],                                                    # höchstens einmal am Tag
        ]
        for schritt in rueckstand:
            if _unterbrochen():
                break
            try:
                n += schritt()
            except Exception:
                pass
        if alles:
            n += _block_einlesen()
    finally:
        _status, _kontext, _lauf_marke = None, "", None
        _lauf.nur_text = False
    return n


PFLEGE_STAND = "pflege"            # Merkzettel: Tag der letzten Pflege
FTS_MERGE_SEITEN = 500             # so viel Arbeit je Pflege für das Zusammenlegen des Stichwort-Index


def verwaiste_profile() -> list[str]:
    """Profil-Namen aus Refs <art>:<profil>:…, deren Profil-Ordner fehlt. Leer, solange Profile/ selbst fehlt
    oder leer ist (Laufwerk weg, Umzug): dann wird nichts ausgetragen."""
    try:
        import profilordner
        wurzel = profilordner.wurzel()
        vorhanden = {p.name.lower() for p in wurzel.iterdir() if p.is_dir()}
    except OSError:
        return []
    except Exception:
        return []
    if not vorhanden:
        return []
    con = _connect()
    try:
        namen: set[str] = set()
        for a in _profil_arten():
            namen |= {r for (r,) in con.execute(
                "SELECT DISTINCT substr(ref, ?, instr(substr(ref, ?), ':') - 1) FROM chunks "
                "WHERE kind=? AND ref GLOB ?", (len(a) + 2, len(a) + 2, a, f"{a}:*:*"))}
    finally:
        con.close()
    return sorted(n for n in namen if n and n.lower() not in vorhanden)


def pflege(erzwingen: bool = False) -> dict:
    """Pflege von memory.db, höchstens einmal am Tag (`erzwingen`: jetzt): Brocken verschwundener Profil-Ordner
    austragen, den Stichwort-Index zusammenlegen (FTS5 merge) und PRAGMA optimize. Kein VACUUM – das bräuchte
    die Datei für sich allein, während andere Prozesse lesen. Rückgabe: was getan wurde ({} = nicht fällig)."""
    heute = datetime.now().strftime("%Y-%m-%d")
    if not _db_path().exists() or (not erzwingen and _stand(PFLEGE_STAND) == heute):
        return {}
    erg = {"verwaist": 0, "profile": []}
    for profil in verwaiste_profile():
        if _unterbrochen():
            return erg                            # nicht als erledigt merken: der nächste Lauf macht weiter
        erg["verwaist"] += profil_austragen(profil)
        erg["profile"].append(profil)
    con = _connect()
    try:
        con.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('merge', ?)", (FTS_MERGE_SEITEN,))
        con.commit()
        con.execute("PRAGMA optimize")
    except sqlite3.Error:
        pass
    finally:
        con.close()
    _stand(PFLEGE_STAND, heute)
    return erg


def offen() -> dict:
    """Was der Bibliothekar noch vor sich hat – für /gedaechtnis."""
    con = _connect()
    try:
        ohne = int(con.execute("SELECT COUNT(*) FROM chunks INDEXED BY idx_chunks_stand WHERE vec_modell != ?",
                               (_kennung(),)).fetchone()[0])
        drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'")}
        gesamt = int(con.execute("SELECT COUNT(*) FROM chunks INDEXED BY idx_chunks_stand").fetchone()[0])
        alt = int(con.execute("SELECT COUNT(DISTINCT ref) FROM chunks INDEXED BY idx_chunks_schnitt WHERE schnitt < ?",
                              (SCHNITT,)).fetchone()[0])
    finally:
        con.close()
    ordner = _ROOT / "Berichte"
    berichte = [p for p in ordner.glob("*.md")] if ordner.exists() else []
    return {"brocken": gesamt, "ohne_vektor": ohne, "alt_geschnitten": alt,
            "berichte_offen": sum(1 for p in berichte if f"bericht:{p.name}" not in drin)}


_NICHT_JETZT: set[str] = set()     # alt geschnittene Quellen, die gerade nicht neu gelesen werden können


def neu_schneiden(limit: int = NEU_SCHNEIDEN_JE_LAUF) -> int:
    """Quellen mit Brocken einer älteren Schnitt-Fassung neu einlesen (höchstens `limit` je Lauf); die
    Vektoren gleicher Brocken bleiben. Ohne Quelle (weggeräumter Bericht) bleibt der alte Schnitt und
    gilt als erledigt. Chats einer anderen Persönlichkeit warten, bis sie aktiv ist. Wissen schneidet
    ingest_wissen neu."""
    con = _connect()
    try:
        alte = con.execute("SELECT DISTINCT kind, ref FROM chunks INDEXED BY idx_chunks_schnitt "
                           "WHERE schnitt < ?", (SCHNITT,)).fetchall()
    finally:
        con.close()
    n, gemacht = 0, 0
    for kind, ref in alte:
        if gemacht >= limit or _unterbrochen():
            break
        if kind == "wissen" or ref in _NICHT_JETZT:
            continue
        gemacht += 1
        try:
            if kind in ("chat", "chatmd"):
                if _fremdes_profil(ref):
                    _NICHT_JETZT.add(ref)
                    gemacht -= 1
                    continue
                import chatstore
                nr = int(re.sub(r"\D", "", ref.rsplit(":", 1)[-1]) or 0)
                eigen = chatstore.index_ref(nr) if nr else ""
                # chatmd:<Profil>:152.md gehört zu chat:<Profil>:152 – beide liest chat_einbetten
                passt = (ref == eigen if kind == "chat" else
                         ref.rsplit(":", 1)[0] == eigen.replace("chat:", "chatmd:", 1).rsplit(":", 1)[0])
                if passt and (daten := chatstore.load(nr)):
                    n += chat_einbetten(nr, daten)
            elif kind in ("skill", "code"):
                import learn
                liste = learn.list_skills() if kind == "skill" else learn.list_snippets()
                pfad = next((p for p, _t in liste if f"{kind}:{p.name}" == ref), None)
                if pfad is not None:
                    n += ingest_file(kind, pfad)
            elif kind == "bericht":
                pfad = _ROOT / "Berichte" / ref.split(":", 1)[1]
                if pfad.is_file():
                    n += ingest_file("bericht", pfad)
            elif kind == "note":
                n += ingest_all_notes()
        except Exception:
            pass
        _schnitt_merken(ref)                      # neu gelesen – oder ohne Quelle: so lassen
    return n


def backfill(limit: int = BACKFILL_BATCH, ausser: int | None = None) -> int:
    """Indexiert noch fehlende Chats/Skills/Snippets, portionsweise. `ausser`: der laufende Chat –
    der steht im Kontext und wird eigens gesammelt."""
    done = 0
    con = _connect()
    try:
        have_refs = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks")}
    finally:
        con.close()
    try:
        import chatstore
        for meta in chatstore.list_chats():
            if done >= limit or _unterbrochen():
                return done
            if ausser is not None and meta["id"] == ausser:
                continue
            ref = _chat_ref(meta["id"])
            if ref in have_refs:
                continue
            data = chatstore.load(meta["id"])
            if not data:
                continue
            done += 1 if ingest_chat(
                data.get("id") or meta["id"],
                data.get("title") or "",
                data.get("prompts") or [],
                data.get("messages") or [],
                _chat_datum(data),
            ) else 0
    except Exception:
        pass
    try:
        import learn
        for path, _t in learn.list_skills():
            if done >= limit:
                return done
            ref = f"skill:{path.name}"
            if ref in have_refs:
                continue
            n = ingest_file("skill", path)
            if n:
                done += 1
        for path, _t in learn.list_snippets():
            if done >= limit:
                return done
            ref = f"code:{path.name}"
            if ref in have_refs:
                continue
            n = ingest_file("code", path)
            if n:
                done += 1
    except Exception:
        pass
    return done


# ---------------------------------------------------------------------------
# Vektor-Cache: die Brocken einmal lesen, nicht bei jeder Frage neu
# ---------------------------------------------------------------------------
#
# Vorher zog search() bei JEDER Frage die komplette Tabelle aus der SQLite
# (14 MB JSON), parste jeden Vektor einzeln und verglich ihn in reinem Python.
# Jetzt liegt alles einmal als numpy-Matrix im RAM; ein Vergleich ist ein
# einziges Matrix-Produkt. Neu eingelesen wird nur, wenn sich die Datei
# geändert hat.

#
# Ändert sich die Datei (der Bibliothekar schreibt nach fast jeder Runde), werden nur geänderte Zeilen
# nachgeladen: Jeder Vektor steht als JSON-Text in der DB, ein voller Aufbau kostet bei vielen Brocken
# Minuten-Bruchteile bis Minuten. Den vollen Aufbau übernimmt im Prompt-Pfad ein Hintergrund-Faden.

_CACHE: dict = {"stamp": None, "meta": None, "mat": None, "vecs": None, "idx": None, "zeile": {}}
_INDEX_SPERRE = threading.Lock()
_BAU_SPERRE = threading.Lock()
_index_bau: threading.Thread | None = None
_NACHLADEN_BIS = 0.5               # Anteil geänderter Zeilen, bis zu dem Nachladen billiger ist als neu bauen
_IN_BLOCK = 500                    # Zeilen je „rowid IN (…)“-Abfrage


def _db_stamp():
    """Stand der Datenbank – mit der WAL-Datei, in der neue Einträge bis zum Checkpoint stehen."""
    try:
        st = _db_path().stat()
    except Exception:
        return None
    try:
        wal = Path(str(_db_path()) + "-wal").stat()
        return (st.st_mtime_ns, st.st_size, wal.st_mtime_ns, wal.st_size)
    except OSError:
        return (st.st_mtime_ns, st.st_size)


def _invalidate() -> None:
    _CACHE["stamp"] = None


def _index(warten: bool = True) -> dict | None:
    """Alle Brocken als Matrix im Speicher (mit Metadaten). Billig, wenn sich an der DB nichts
    geändert hat; sonst werden nur geänderte Zeilen nachgeladen.

    warten=False (Prompt-Pfad): Baut gerade ein anderer Faden oder ist ein voller Aufbau nötig,
    None – der volle Aufbau läuft dann im Hintergrund, die Suche nimmt diese Runde nur Stichworte."""
    if not _INDEX_SPERRE.acquire(blocking=warten):
        return None
    try:
        ix = _index_aktualisieren(voll_erlaubt=warten)
        # Schnappschuss unter der Sperre: ein anderer Faden ersetzt _CACHE-Einträge, während die Suche noch
        # meta, mat und idx liest – mit dem gemeinsamen Dict passten die Indizes dann nicht mehr zusammen.
        return dict(ix) if ix is not None else None
    finally:
        _INDEX_SPERRE.release()


def _index_nach_der_runde() -> None:
    """Den vollen Aufbau (Python-Schleifen über alle Brocken) erst, wenn keine Runde läuft."""
    try:
        import memory
        memory.warten_bis_ruhe()
    except Exception:
        pass
    _index()


def _index_im_hintergrund() -> None:
    global _index_bau
    with _BAU_SPERRE:
        if _index_bau is not None and _index_bau.is_alive():
            return
        _index_bau = threading.Thread(target=_index_nach_der_runde, name="nemi-index", daemon=True)
        _index_bau.start()


def vorwaermen() -> None:
    """Index im Hintergrund bereitlegen (beim Start, nach jedem Bibliothekar-Lauf). Sonst zahlt die erste
    Suche danach Aufbau, Nachladen und den ersten Import von torch über die Modell-Kennung."""
    _index_im_hintergrund()


def _index_aktualisieren(voll_erlaubt: bool) -> dict | None:
    kennung = _kennung()
    db = str(_db_path())
    stamp = (_db_stamp(), kennung, db)
    if _CACHE["meta"] is not None and stamp[0] is not None and _CACHE["stamp"] == stamp:
        return _CACHE
    try:
        import numpy as np
    except Exception:
        np = None
    nachladbar = (np is not None and _CACHE["meta"] is not None and _CACHE.get("kennung") == kennung
                  and _CACHE.get("db") == db and "zustand" in _CACHE)
    if nachladbar:
        try:
            if _nachladen(stamp, kennung, np):
                return _CACHE
        except Exception:
            pass
    if not voll_erlaubt:
        _index_im_hintergrund()
        return None
    _voll_aufbauen(stamp, kennung, db, np)
    return _CACHE


def _zustand_lesen(con, kennung: str) -> dict:
    """rowid → (hash, hat Vektor des aktiven Modells, datum) – nur aus idx_chunks_stand, daher schnell.
    vec_modell ist genau dann gesetzt, wenn ein Vektor da ist (alle Schreibstellen setzen beides zusammen)."""
    return {r: (h, bool(kennung) and m == kennung, d or "") for r, h, m, d in con.execute(
        "SELECT rowid, hash, vec_modell, datum FROM chunks INDEXED BY idx_chunks_stand")}


def _vektor(vec_s, np):
    if np is not None and isinstance(vec_s, (bytes, bytearray, memoryview)):
        a = np.frombuffer(vec_s, dtype="<f4").astype("float32")   # float32: ohne Zerlegen
    else:
        try:
            v = vec_entpacken(vec_s)
        except Exception:
            return None
        if np is None or v is None:
            return v
        a = np.asarray(v, dtype="float32")
    n = float(np.linalg.norm(a))
    return a / n if n else a


def _voll_aufbauen(stamp, kennung: str, db: str, np) -> None:
    meta: list[tuple] = []
    vecs: list = []
    vec_zeilen: list[int] = []
    zustand: dict = {}
    try:
        con = _connect()
        try:
            for rowid, h, kind, ref, text, vec_s, datum, vec_modell, herkunft in con.execute(
                    "SELECT rowid, hash, kind, ref, text, vec, datum, vec_modell, herkunft FROM chunks"):
                meta.append((kind, ref, text, datum or "", rowid, herkunft or herkunft_ableiten(kind, ref, text)))
                hat = vec_s is not None and bool(kennung) and vec_modell == kennung
                zustand[rowid] = (h, hat, datum or "")
                if not hat:
                    continue                  # kein Vektor oder von einem anderen Modell
                v = _vektor(vec_s, np)
                if v is not None:
                    vecs.append(v)
                    vec_zeilen.append(rowid)
        finally:
            con.close()
    except Exception as e:
        # Ein halber Index unter dem aktuellen Stand gespeichert hieße: Suche still leer, bis die DB
        # sich wieder ändert. Also nicht merken – der nächste Abruf baut neu.
        try:
            import debugbericht
            debugbericht.fehler("Gedächtnis-Index aufbauen", e)
        except Exception:
            pass
        _CACHE_setzen(None, kennung, db, meta, None, [], [], {})
        _CACHE.pop("zustand", None)               # kein Nachladen auf halbem Stand
        return
    mat = None
    if vecs and np is not None:
        laengen = [len(v) for v in vecs]
        if len(set(laengen)) > 1:             # gemischte Längen (Rechenweise gewechselt): die häufigste gilt
            haeufig = max(set(laengen), key=laengen.count)
            behalten = [i for i, n in enumerate(laengen) if n == haeufig]
            vecs, vec_zeilen = [vecs[i] for i in behalten], [vec_zeilen[i] for i in behalten]
        mat = np.vstack(vecs)
        vecs = []                         # numpy hat die Daten, Listen freigeben
    _CACHE_setzen(stamp, kennung, db, meta, mat, vecs, vec_zeilen, zustand)


def _nachladen(stamp, kennung: str, np) -> bool:
    """Nur geänderte Zeilen einlesen. False, wenn sich so viel geändert hat, dass neu bauen billiger ist."""
    con = _connect()
    try:
        jetzt = _zustand_lesen(con, kennung)
        alt = _CACHE["zustand"]
        weg = {r for r, z in alt.items() if r not in jetzt or jetzt[r][0] != z[0]}       # gelöscht/ersetzt
        neu = [r for r, z in jetzt.items() if r not in alt or alt[r][0] != z[0]]
        gleich = [r for r in jetzt if r in alt and alt[r][0] == jetzt[r][0]]
        vec_dazu = [r for r in gleich if jetzt[r][1] and not alt[r][1]]                 # Vektor nachgetragen
        vec_weg = {r for r in gleich if alt[r][1] and not jetzt[r][1]}
        datum_neu = {r: jetzt[r][2] for r in gleich if jetzt[r][2] != alt[r][2]}
        if len(neu) + len(vec_dazu) > _NACHLADEN_BIS * max(1, len(jetzt)):
            return False
        zeilen: dict[int, tuple] = {}
        laden = neu + vec_dazu
        for i in range(0, len(laden), _IN_BLOCK):
            teil = laden[i:i + _IN_BLOCK]
            zeilen.update({r[0]: r[1:] for r in con.execute(
                "SELECT rowid, kind, ref, text, vec, datum, herkunft FROM chunks WHERE rowid IN "
                f"({','.join('?' * len(teil))})", teil)})
    finally:
        con.close()
    meta_von = {m[4]: m for m in _CACHE["meta"] if m[4] not in weg}
    for r, d in datum_neu.items():
        if r in meta_von:
            meta_von[r] = meta_von[r][:3] + (d,) + meta_von[r][4:]
    dazu_r, dazu_v = [], []
    for r in neu + vec_dazu:
        if r not in zeilen:
            continue                      # inzwischen gelöscht: der nächste Abgleich sieht es
        kind, ref, text, vec_s, datum, herkunft = zeilen[r]
        meta_von[r] = (kind, ref, text, datum or "", r, herkunft or herkunft_ableiten(kind, ref, text))
        if jetzt[r][1] and (v := _vektor(vec_s, np)) is not None:
            dazu_r.append(r)
            dazu_v.append(v)
    mat, vz = _CACHE["mat"], list(_CACHE["vec_zeilen"])
    raus = weg | vec_weg | set(vec_dazu)
    if mat is not None and raus:
        behalten = [j for j, r in enumerate(vz) if r not in raus]
        mat, vz = mat[behalten], [vz[j] for j in behalten]
    if dazu_v:
        breite = mat.shape[1] if mat is not None and len(mat) else len(dazu_v[0])
        passend = [(r, v) for r, v in zip(dazu_r, dazu_v) if len(v) == breite]      # andere Länge: nicht mischen
        if passend:
            mat = np.vstack(([mat] if mat is not None and len(mat) else []) + [v for _r, v in passend])
            vz += [r for r, _v in passend]
    if mat is not None and not len(mat):
        mat = None
    meta = [meta_von[r] for r in sorted(meta_von)]
    _CACHE_setzen(stamp, kennung, _CACHE["db"], meta, mat, [], vz, jetzt)
    return True


def _CACHE_setzen(stamp, kennung, db, meta, mat, vecs, vec_zeilen, zustand) -> None:
    zeile = {m[4]: i for i, m in enumerate(meta)}
    idx = [zeile[r] for r in vec_zeilen]
    _CACHE.update({"stamp": stamp, "meta": meta, "mat": mat, "vecs": vecs, "idx": idx, "zeile": zeile,
                   "mat_zeile": {i: j for j, i in enumerate(idx)},
                   "vec_zeilen": vec_zeilen, "zustand": zustand, "kennung": kennung, "db": db})


def _scores(ix: dict, qv: list[float], ab: float = -1.0) -> list[tuple[float, int]]:
    """(Ähnlichkeit, meta-Index) für alle Brocken mit Vektor und Ähnlichkeit ≥ ab."""
    mat, idx = ix["mat"], ix["idx"]
    if mat is not None:
        import numpy as np
        q = np.asarray(qv, dtype="float32")
        n = float(np.linalg.norm(q)) or 1.0
        sims = mat @ (q / n)
        return [(float(sims[j]), idx[j]) for j in np.flatnonzero(sims >= ab)]
    import memory
    return [(s, idx[j]) for j, v in enumerate(ix["vecs"] or []) if (s := memory._cos(qv, v)) >= ab]


def _hit(meta_row: tuple, score: float, wie: str = "") -> dict:
    kind, ref, text, datum = meta_row[:4]
    return {"kind": kind, "ref": ref, "text": text, "datum": datum, "score": score, "wie": wie,
            "herkunft": meta_row[5] if len(meta_row) > 5 else ""}


# ---------------------------------------------------------------------------
# Hybride Suche: Stichworte (FTS5/BM25) + Bedeutung (Vektoren), per RRF vereint
# ---------------------------------------------------------------------------

RRF_K = 60                         # Reciprocal Rank Fusion: 1 / (RRF_K + Rang)
KANDIDATEN = 50                    # je Liste so viele Plätze in die Fusion

# Füllwörter tragen in der Stichwortsuche nichts bei, würden aber fast jeden Brocken treffen.
_FUELLWOERTER = set("""
der die das den dem des ein eine einer eines einem einen und oder aber doch nicht kein keine
ist sind war waren wird werden wurde hat haben hatte hatten bin bist sein ich du er sie es wir ihr
mich dich mir dir uns euch mein dein sein ihr unser euer was wer wie wo wann warum wieso welche
welcher welches mit von zu zum zur für auf aus bei nach über unter vor im in an am als auch noch
schon so da dann denn dass ob wenn weil mal nur sehr ja nein bitte kannst kann könnte hast habe
the a an and or of to in on for is are was were be it this that with
""".split())


def _fts_woerter(frage: str) -> list[str]:
    woerter = []
    for w in re.findall(r"[\w.\-]+", frage.lower()):
        w = w.strip(".-").replace('"', "")
        if len(w) < 2 or w in _FUELLWOERTER or w in woerter:
            continue
        woerter.append(w)
    return woerter[:16]


def _fts_teil(w: str) -> str:
    return f'"{w}"*' if len(w) >= 4 else f'"{w}"'


def _fts_anfrage(frage: str) -> str:
    """Frage → FTS5-Ausdruck: Wörter als Phrase, ab 4 Zeichen mit Präfix, ODER-verknüpft."""
    return " OR ".join(_fts_teil(w) for w in _fts_woerter(frage))


HAEUFIG_ANTEIL = 0.3               # ein Wort in mehr als so vielen Brocken findet „alles“ und kostet am meisten
VORAUSWAHL = 200                   # so viele beste Stichwort-Treffer holt die erste Stufe


def _wie_im_index(w: str) -> str:
    """So, wie der Tokenizer (unicode61 remove_diacritics 2) das Wort ablegt: ohne Akzente."""
    import unicodedata
    return "".join(z for z in unicodedata.normalize("NFKD", w) if not unicodedata.combining(z))


SELTENSTE_REST = 3                 # stehen alle Wörter in sehr vielen Brocken: so viele seltenste bleiben


def _seltene_woerter(con: sqlite3.Connection, woerter: list[str]) -> list[str]:
    """Wörter ohne Treffer fallen weg (sie finden nichts), ebenso solche in mehr als HAEUFIG_ANTEIL der Brocken
    (sie finden alles) – sind alle vorhandenen so häufig, bleiben die SELTENSTE_REST seltensten. Gezählt wird im
    Wortlexikon des Index (fts5vocab, Präfix-Wörter als Bereich), ohne Text zu lesen."""
    if len(woerter) < 2:
        return woerter
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_woerter USING fts5vocab(main, chunks_fts, 'row')")
        gesamt = con.execute("SELECT max(rowid) FROM chunks").fetchone()[0] or 0
        if gesamt < 1000:                    # kleine Datenbank: schnell genug, alles behalten
            return woerter
        zahl = {}
        for w in woerter:
            n = _wie_im_index(w)
            if len(w) >= 4:
                doc = con.execute("SELECT coalesce(sum(doc), 0) FROM temp.fts_woerter WHERE term >= ? AND term <= ?",
                                  (n, n + chr(0x10FFFF))).fetchone()[0]
            else:
                doc = (con.execute("SELECT doc FROM temp.fts_woerter WHERE term = ?", (n,)).fetchone() or (0,))[0]
            zahl[w] = doc
    except sqlite3.Error:
        return woerter
    vorhanden = [w for w in woerter if zahl[w] > 0]
    if not vorhanden:
        return woerter                       # nichts davon im Index: die Suche bleibt ohnehin leer
    selten = [w for w in vorhanden if zahl[w] <= gesamt * HAEUFIG_ANTEIL]
    return selten or sorted(vorhanden, key=lambda w: zahl[w])[:SELTENSTE_REST]


def _stichwort_treffer(con: sqlite3.Connection, frage: str, arten, seit, bis, ref_praefix,
                       herkunft=None) -> list[tuple]:
    """(rowid, kind, ref, text, datum) nach BM25. Zwei Stufen: erst die besten VORAUSWAHL nur im Index, dann deren
    Zeilen mit den Filtern – statt jede passende Zeile zu holen und danach zu sortieren. Bleiben nach den Filtern zu
    wenige übrig, entscheidet die volle Abfrage wie bisher."""
    woerter = _seltene_woerter(con, _fts_woerter(frage))
    ausdruck = " OR ".join(_fts_teil(w) for w in woerter)
    if not ausdruck:
        return []
    bedingung, werte = _filter_sql(arten, seit, bis, ref_praefix, herkunft)
    ids = [r[0] for r in con.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                                     (ausdruck, VORAUSWAHL))]
    if not ids:
        return []
    rang = {r: i for i, r in enumerate(ids)}
    zeilen = con.execute(f"SELECT c.rowid, c.kind, c.ref, c.text, c.datum, c.herkunft FROM chunks c WHERE c.rowid IN "
                         f"({','.join('?' * len(ids))}){bedingung}", [*ids, *werte]).fetchall()
    zeilen.sort(key=lambda z: rang[z[0]])
    if not (len(zeilen) >= KANDIDATEN or len(ids) < VORAUSWAHL):
        zeilen = con.execute(
            "SELECT c.rowid, c.kind, c.ref, c.text, c.datum, c.herkunft FROM chunks_fts JOIN chunks c "
            f"ON c.rowid = chunks_fts.rowid WHERE chunks_fts MATCH ?{bedingung} "
            "ORDER BY bm25(chunks_fts) LIMIT ?", [ausdruck, *werte, KANDIDATEN]).fetchall()
    zeilen = [z[:5] + (z[5] or herkunft_ableiten(z[1], z[2], z[3]),) for z in zeilen[:KANDIDATEN]]
    if herkunft:                                 # leere (noch nicht nachgetragene) Herkunft: abgeleitet prüfen
        zeilen = [z for z in zeilen if z[5] in herkunft]
    return zeilen


def _filter_sql(arten, seit, bis, ref_praefix, herkunft=None) -> tuple[str, list]:
    """`herkunft`: nur Brocken dieser Herkünfte und keine Berichte (Suche für den Systemprompt)."""
    teile, werte = [], []
    if herkunft:
        teile.append(f"(c.herkunft IN ({','.join('?' * len(herkunft))}) OR c.herkunft = '' OR c.herkunft IS NULL) "
                     "AND c.kind != 'bericht'")
        werte += list(herkunft)
    if arten:
        teile.append(f"c.kind IN ({','.join('?' * len(arten))})")
        werte += list(arten)
    else:
        teile.append("c.kind NOT IN (" + ",".join(f"'{a}'" for a in MEDIEN_ARTEN) + ")")   # nur in der Medien-Suche
    if seit:
        teile.append("c.datum >= ?")
        werte.append(seit)
    if bis:
        teile.append("c.datum <= ?")
        werte.append(bis + " 99")               # ganzer Tag
    if ref_praefix:                             # "ref$" = genau diese Quelle
        # substr statt LIKE: „_“/„%“ im Präfix sind keine Platzhalter, Groß/klein zählt – wie in _passt
        teile.append("(" + " OR ".join("c.ref = ?" if p.endswith("$") else "substr(c.ref, 1, ?) = ?"
                                        for p in ref_praefix) + ")")
        for p in ref_praefix:
            werte += [p[:-1]] if p.endswith("$") else [len(p), p]
    profil, profil_werte = _profil_bedingung()
    if profil:
        teile.append(profil)
        werte += profil_werte
    return (" AND " + " AND ".join(teile)) if teile else "", werte


def _passt(meta_row: tuple, arten, seit, bis, ref_praefix, aktiv: str | None = None, herkunft=None) -> bool:
    kind, ref, _t, datum = meta_row[:4]
    if _fremdes_profil(ref, aktiv):
        return False
    if herkunft and (kind == "bericht" or (meta_row[5] if len(meta_row) > 5 else "") not in herkunft):
        return False
    if arten and kind not in arten:
        return False
    if not arten and kind in MEDIEN_ARTEN:       # Medien nur in der Medien-Suche
        return False
    if seit and (datum or "") < seit:
        return False
    if bis and (datum or "") > bis + " 99":
        return False
    if ref_praefix and not any(ref == p[:-1] if p.endswith("$") else ref.startswith(p) for p in ref_praefix):
        return False
    return True


FRAGEN_MAX = 4                     # so viele Fassungen einer Suche (Multi-Query)
VIELFALT = 0.75                    # MMR: Gewicht der Passung gegen die Nähe zu schon gewählten Treffern
DOPPELT_WORTE_AB = 0.95            # ohne Vektoren: so viele gemeinsame Wörter = fast derselbe Treffer


def schwelle(name: str) -> float:
    """Kosinus-Schwelle (embedder.SCHWELLEN) des Modells, dessen Vektoren im Such-Index liegen."""
    import embedder
    return embedder.schwellen(_CACHE.get("kennung") or _kennung())[name]


def _fragen_liste(frage) -> list[str]:
    roh = [frage] if isinstance(frage, str) else list(frage or [])
    raus: list[str] = []
    for f in roh:
        f = " ".join(str(f or "").split())
        if f and f.casefold() not in {x.casefold() for x in raus}:
            raus.append(f)
    return raus[:FRAGEN_MAX]


# Suchanweisung (embedder.AUFGABEN) nach den durchsuchten Arten; gemischt: "gedaechtnis".
_AUFGABE_JE_ARTEN = (("chat", {"chat", "chatmd"}), ("notizen", {"note"}), ("code", {"code", "skill"}),
                     ("dokumente", {"wissen", "bericht"}))


def _aufgabe(arten) -> str:
    gewaehlt = set(arten or ())
    for aufgabe, gruppe in _AUFGABE_JE_ARTEN:
        if gewaehlt and gewaehlt <= gruppe:
            return aufgabe
    return "gedaechtnis"


_MERK = threading.local()          # Stichwort-Treffer je guide_block: Vorab- und volle Suche teilen sie
_NUR_PROMPT = threading.local()    # _treffer_block: die Suche liefert nur Herkünfte aus HERKUNFT_PROMPT


def _worte(con: sqlite3.Connection, f: str, arten, seit, bis, ref_praefix, herkunft=None) -> list[tuple]:
    merk = getattr(_MERK, "worte", None)
    schluessel = (f, tuple(arten) if arten else None, seit, bis, tuple(ref_praefix) if ref_praefix else None,
                  tuple(herkunft) if herkunft else None)
    if merk is not None and schluessel in merk:
        return merk[schluessel]
    zeilen = _stichwort_treffer(con, f, arten, seit, bis, ref_praefix, herkunft)
    if merk is not None:
        merk[schluessel] = zeilen
    return zeilen


def suchen(frage, k: int = SEARCH_K, *, arten=None, seit: str = "", bis: str = "",
           ref_praefix=None, laden: bool = False, vermutung: str = "", lernen: bool = True,
           nur_worte: bool = False, nachsortieren: bool = True) -> list[dict]:
    """Hybride Suche über memory.db.

    Stichworte (FTS5, BM25) finden exakte Begriffe; Vektoren finden Sinnverwandtes.
    Alle Ranglisten werden per Reciprocal Rank Fusion vereint – ein Brocken, den
    mehrere finden, steht oben. `frage` darf eine Liste von Fassungen sein (Multi-Query);
    `vermutung` ist eine vermutete Antwort, gesucht wird mit ihr nach Bedeutung (HyDE).
    Danach sortiert das gelernte Rangmodell um (rangliste, `lernen`), und MMR sorgt für
    Vielfalt: keine Nachbarn und Beinahe-Kopien derselben Stelle nebeneinander.
    `laden=True` lädt den Encoder notfalls (~6 s): nur für das Werkzeug der Persönlichkeit,
    nie im Prompt-Pfad. Ohne Vektoren oder Encoder bleibt die Stichwortsuche.
    `nur_worte=True`: nur FTS, ohne Index und Encoder (Zwischenstand für den Prompt).
    Jeder Treffer trägt `aehnlich` (bester Kosinus, None ohne Vektor) und `rowid`."""
    fragen = _fragen_liste(frage)
    vermutung = " ".join(str(vermutung or "").split())
    if not fragen and not vermutung:
        return []
    uhr = time.perf_counter()
    ix = None if nur_worte else _index(warten=laden)     # Prompt-Pfad: nie auf einen Neuaufbau warten
    uhr = _messen("Index", uhr)
    if ix is not None and not ix["meta"]:
        if ix.get("stamp") is not None:
            return []
        ix = None                            # Aufbau gescheitert: trotzdem Stichwortsuche
    meta = ix["meta"] if ix is not None else []
    punkte: dict[int, float] = {}        # rowid → Punkte
    wie: dict[int, set] = {}
    aehnlich: dict[int, float] = {}      # rowid → bester Kosinus
    gefunden: dict[int, tuple] = {}      # rowid → (kind, ref, text, datum, rowid)
    aktiv = _aktives_profil()                                     # einmal je Suche
    fremd = _fremde_notizen(aktiv) if not arten or "note" in arten else frozenset()   # Lehren anderer Persönlichkeiten
    herkunft = HERKUNFT_PROMPT if getattr(_NUR_PROMPT, "an", False) else None      # Suche für den Systemprompt

    if any(_fts_anfrage(f) for f in fragen):
        try:
            con = _connect()
            try:
                listen = [_worte(con, f, arten, seit, bis, ref_praefix, herkunft) for f in fragen if _fts_anfrage(f)]
            finally:
                con.close()
        except sqlite3.Error:
            listen = []
        for zeilen in listen:
            zeilen = [z for z in zeilen if z[2] not in fremd]
            for rang, (rowid, kind, ref, text, datum, *rest) in enumerate(zeilen):
                gefunden[rowid] = (kind, ref, text, datum or "", rowid, (rest[0] if rest else "") or "")
                punkte[rowid] = punkte.get(rowid, 0.0) + 1.0 / (RRF_K + rang)
                wie.setdefault(rowid, set()).add("wort")
    uhr = _messen("Stichworte", uhr)

    if ix is not None and (ix["mat"] is not None or ix["vecs"]):
        vektoren: list[tuple[list[float], str]] = []
        try:
            import memory
            aufgabe = _aufgabe(arten)
            if laden:
                vektoren += [(v, "sinn") for v in (memory._embed_texts(fragen, query=True, aufgabe=aufgabe)
                                                   or [])] if fragen else []
                if vermutung:              # wie ein Dokument einbetten: vergleicht Antwort mit Antwort
                    vektoren += [(v, "vermutung") for v in (memory._embed_texts([vermutung]) or [])]
            elif memory.encoder_loaded() and fragen:
                vektoren += [(v, "sinn") for v in (memory.frage_einbetten(fragen[0], aufgabe=aufgabe) or [])]
            else:
                memory.warm_encoder()          # ab der nächsten Frage auch nach Bedeutung
        except Exception:
            vektoren = []
        uhr = _messen("Einbetten", uhr)
        kandidat_ab = schwelle("kandidat") if vektoren else 0.0
        for qv, art in vektoren:
            bewertet = []                # beste zuerst, gefiltert nur bis genug beisammen sind
            for s, i in sorted(_scores(ix, qv, kandidat_ab), key=lambda x: -x[0]):
                if _passt(meta[i], arten, seit, bis, ref_praefix, aktiv, herkunft) and meta[i][1] not in fremd:
                    bewertet.append((s, i))
                    if len(bewertet) >= KANDIDATEN:
                        break
            for rang, (s, i) in enumerate(bewertet):
                rowid = meta[i][4]
                gefunden.setdefault(rowid, meta[i])
                punkte[rowid] = punkte.get(rowid, 0.0) + 1.0 / (RRF_K + rang)
                wie.setdefault(rowid, set()).add(art)
                aehnlich[rowid] = max(aehnlich.get(rowid, -1.0), s)
        _messen("Vergleich", uhr)

    kandidaten = sorted(punkte.items(), key=lambda x: -x[1])[:max(3 * k, 24)]
    hits = []
    for r, p in kandidaten:
        h = _hit(gefunden[r], round(p * RRF_K / 2, 3), "+".join(sorted(wie.get(r, ()))))
        h["rowid"], h["aehnlich"] = r, (round(aehnlich[r], 3) if r in aehnlich else None)
        hits.append(h)
    if lernen:
        try:
            import rangliste
            hits = rangliste.umsortieren(hits)
        except Exception:
            pass
    if laden and nachsortieren:              # nur die Suche der Persönlichkeit, nie der Prompt-Pfad
        try:
            import reranker
            hits = reranker.nachsortieren(fragen[0] if fragen else vermutung, hits)
        except Exception:
            pass
    return _vielfalt(hits, k, ix)


def _vektor_von(ix: dict | None, rowid: int):
    """Der normierte Vektor eines Brockens aus dem Such-Index, oder None."""
    if ix is None or ix.get("mat") is None or "zeile" not in ix:
        return None
    i = ix["zeile"].get(rowid)
    j = ix.get("mat_zeile", {}).get(i) if i is not None else None
    return ix["mat"][j] if j is not None else None


def _woerter(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", (text or "").casefold()))


def _gleich(a: dict, b: dict, ix: dict | None) -> tuple[float, bool]:
    """(Ähnlichkeit, aus Vektoren?): Kosinus der Vektoren, sonst Anteil gemeinsamer Wörter (0..1)."""
    va, vb = _vektor_von(ix, a["rowid"]), _vektor_von(ix, b["rowid"])
    if va is not None and vb is not None:
        return float(va @ vb), True
    wa, wb = _woerter(a["text"]), _woerter(b["text"])
    return len(wa & wb) / max(1, len(wa | wb)), False


def _kopie(a: dict, b: dict, ix: dict | None, kopie_ab: float) -> bool:
    wert, vektor = _gleich(a, b, ix)
    return wert >= (kopie_ab if vektor else DOPPELT_WORTE_AB)


def _naehe(a: dict, b: dict, ix: dict | None) -> float:
    """0..1: wie sehr b zeigt, was a schon zeigt – direkte Nachbarn derselben Quelle zählen voll."""
    if a["ref"] == b["ref"] and abs(a["rowid"] - b["rowid"]) <= 1:
        return 1.0
    return max(0.0, _gleich(a, b, ix)[0])


def _vielfalt(hits: list[dict], k: int, ix: dict | None) -> list[dict]:
    """Maximal Marginal Relevance: der nächste Treffer ist der, der am besten passt und am wenigsten
    zeigt, was schon gewählt ist. Beinahe-Kopien (Schwelle „doppelt“) fallen ganz weg."""
    if len(hits) <= 1:
        return hits[:k]
    kopie_ab = schwelle("doppelt")
    beste = max(h["score"] for h in hits) or 1.0
    gewaehlt: list[dict] = []
    rest = list(hits)
    while rest and len(gewaehlt) < k:
        def wert(h):
            naehe = max((_naehe(g, h, ix) for g in gewaehlt), default=0.0)
            return VIELFALT * h["score"] / beste - (1 - VIELFALT) * naehe
        naechster = max(rest, key=wert)
        rest.remove(naechster)
        if any(_kopie(g, naechster, ix, kopie_ab) for g in gewaehlt):
            continue
        gewaehlt.append(naechster)
    return gewaehlt


def nachbarn(rowid: int) -> tuple[str, str]:
    """Text des Brockens davor und danach in derselben Quelle (Small-to-Big), "" wenn keiner da ist."""
    ix = _CACHE if _CACHE.get("meta") else None
    if ix is None:
        return "", ""
    meta, i = ix["meta"], ix["zeile"].get(rowid)
    if i is None:
        return "", ""
    ref = meta[i][1]
    vorher = meta[i - 1][2] if i > 0 and meta[i - 1][1] == ref else ""
    nachher = meta[i + 1][2] if i + 1 < len(meta) and meta[i + 1][1] == ref else ""
    return vorher, nachher


def quelle_lesen(ref: str, max_zeichen: int = 12_000, ab: int = 1) -> dict | None:
    """Die Brocken einer Quelle (z. B. 'chat:152') in Reihenfolge, ab Abschnitt `ab`
    (1 = Anfang), so viele ganze Abschnitte wie in `max_zeichen` passen. `weiter`
    nennt den nächsten Abschnitt (0 = Ende). Überlappungen werden nicht entfernt.
    `herkunft`: die Herkünfte der gezeigten Brocken (agentrag setzt danach die Netz-Marke)."""
    aktiv = _aktives_profil()
    if _fremdes_profil(ref, aktiv):              # Chats einer anderen Persönlichkeit
        return None
    if str(ref).startswith(("note:", "detail:")) and ref in _fremde_notizen(aktiv):   # Lehren einer anderen
        return None
    try:
        con = _connect()
        try:
            zeilen = con.execute("SELECT kind, text, datum, herkunft FROM chunks WHERE ref=? ORDER BY rowid",
                                 (ref,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not zeilen:
        return None
    start = min(max(1, int(ab or 1)), len(zeilen))
    if any(not z[3] for z in zeilen):           # noch nicht nachgetragen: aus der ganzen Quelle ableiten
        kind = zeilen[0][0] or "chat"
        abgeleitet = ([_medien_herkunft(ref)] * len(zeilen) if kind in MEDIEN_ARTEN
                      else _herkunft_reihe(kind, [z[1] for z in zeilen]))
        zeilen = [z[:3] + (z[3] or h,) for z, h in zip(zeilen, abgeleitet)]
    teile, laenge, weiter, herkunft = [], 0, 0, set()
    for nr, (_k, t, _d, hk) in enumerate(zeilen[start - 1:], start):
        if teile and laenge + len(t) > max_zeichen:
            weiter = nr
            break
        teile.append(t)
        herkunft.add(hk)
        laenge += len(t) + 3
    text = "\n…\n".join(teile)
    gekuerzt = bool(weiter) or len(text) > max_zeichen
    return {"ref": ref, "kind": zeilen[0][0], "datum": zeilen[0][2] or "",
            "text": text[:max_zeichen], "gekuerzt": gekuerzt, "brocken": len(zeilen),
            "ab": start, "weiter": weiter, "herkunft": sorted(herkunft)}


def search(query: str, k: int = SEARCH_K, nur_worte: bool = False) -> list[dict]:
    """Hybride Suche für den Prompt-Pfad (persona.build_system_prompt).

    Lädt NIE das Embedding-Modell nach: ist der Encoder kalt, wird er im
    Hintergrund vorgewärmt, und diese Frage läuft nur über die Stichwortsuche –
    der Chat wartet keine Sekunde."""
    return suchen(query, k, laden=False, nur_worte=nur_worte)


GUIDE_K = 3                        # Vorgeschmack im Prompt – tiefer sucht die Persönlichkeit selbst


VORHIN_K = 3                       # Treffer aus dem ausgelagerten Teil des laufenden Chats
GUIDE_KANDIDATEN = 3 * GUIDE_K     # Vorrat: laufender Chat, Berichte und Kern-Gedächtnis fallen danach heraus


def _laufender_chat() -> tuple[str, list[str], int]:
    """(ref des laufenden Chats, Texte im Kontext, Zahl ausgelagerter Nachrichten)."""
    try:
        import chatstore
        chat = chatstore.aktueller_chat()
        if not chat:
            return "", [], 0
        kontext = chatstore.im_kontext()
        gespeichert = (chatstore.load(int(chat)) or {}).get("messages") or []
        return (chatstore.index_ref(int(chat)), [_msg_text(m) for m in kontext],
                max(0, len(gespeichert) - len(kontext)))
    except Exception:
        return "", [], 0


_HERKUNFT = re.compile(r"^(?:(?:Nutzer|KI|Nemi|Werkzeug|Netz): |\[(?:Nutzer|KI|Nemi|Werkzeug|Netz)(?: › [^\]\n]*)?\] )")


def _herkunft_von(h: dict) -> str:
    """Herkunft eines Treffers; ohne das Feld (Aufrufer mit eigenen Treffern) aus Art und Text abgeleitet."""
    wert = h.get("herkunft")
    if wert:
        return str(wert)
    text = str(h.get("text") or "")
    if wert is not None:                         # leer: älterer Brocken, noch nicht nachgetragen
        return herkunft_ableiten(h.get("kind") or "chat", str(h.get("ref") or ""), text)
    if (h.get("kind") or "chat") != "chat":
        return _ART_HERKUNFT.get(h.get("kind"), "datei")
    if _fremd_markiert(text) or re.match(r"^(?:Werkzeug|Netz): |^\[(?:Werkzeug|Netz)[\] ]|^(?:Nutzer: )?"
                                         + re.escape(ERGEBNIS_VORSATZ), text):
        return "werkzeug"
    return "ki" if re.match(r"^(?:KI|Nemi): |^\[(?:KI|Nemi)[\] ]", text) else "nutzer"


def _fuer_prompt(h: dict) -> bool:
    """Darf dieser Treffer ungefragt in den Systemprompt? Nur Eingaben des Nutzers, Antworten der KI, Notizen
    Skills und Wissen-Dokumente – keine Werkzeug-Ergebnisse, Netz-Inhalte, Berichte oder Exporte."""
    return h.get("kind") != "bericht" and _herkunft_von(h) in HERKUNFT_PROMPT


def _vorhin_im_chat(query: str, ref: str, kontext: list[str], nur_worte: bool = False) -> list[dict]:
    """Treffer aus dem laufenden Chat, die nicht mehr im Kontext stehen (ohne Werkzeug-Ergebnisse und Netz-Inhalt)."""
    raus = []
    for h in suchen(query, VORHIN_K * 4, arten=("chat",), ref_praefix=(ref + "$",), nur_worte=nur_worte):
        text = h.get("text") or ""
        if text.startswith("Chat #") or not _fuer_prompt(h):     # Titelzeile, Fremdinhalt
            continue
        text = _HERKUNFT.sub("", text, count=1)              # „Nutzer: “ oder „[Nutzer] “ (Folge-Brocken)
        probe = text.strip()[:200]
        if probe and any(probe in k for k in kontext):     # steht noch im Kontext
            continue
        raus.append(h)
        if len(raus) >= VORHIN_K:
            break
    return raus


def _sicher(hits: list[dict], ab: float) -> list[dict]:
    """Ohne Treffer, die nur nach Bedeutung passen (kein Stichwort) und unter `ab` liegen (Schwelle
    „vorgeschmack“). Stichwort-Treffer und Treffer ohne Ähnlichkeitswert bleiben."""
    return [h for h in hits if "wort" in (h.get("wie") or "") or h.get("aehnlich") is None or h["aehnlich"] >= ab]


def _zeile(h: dict, memory) -> str:
    snippet = memory.entschaerfen(h.get("text") or "")
    if len(snippet) > 280:
        snippet = snippet[:280] + "…"
    tag = f" · {h['datum'][:10]}" if h.get("datum") else ""
    return f"- [{h.get('kind')}/{h.get('ref')}{tag}] {snippet}"


_ZEITEN = threading.local()        # Teilzeiten der Suche für den Systemprompt (nur in guide_block)
SUCHE_LANGSAM_S = 1.0              # ab so viel Suchzeit stehen die Teile im Debugbericht


def _messen(teil: str, seit: float) -> float:
    jetzt = time.perf_counter()
    zeiten = getattr(_ZEITEN, "messen", None)
    if zeiten is not None:
        zeiten[teil] = zeiten.get(teil, 0.0) + jetzt - seit
    return jetzt


def _zeiten_melden(zeiten: dict) -> None:
    if sum(zeiten.values()) < SUCHE_LANGSAM_S:
        return
    try:
        import debugbericht
        debugbericht.ereignis("Gedächtnissuche-Teile: " + " · ".join(
            f"{n} {s:.1f} s" for n, s in zeiten.items() if s >= 0.05), speicher=False)
    except Exception:
        pass


VORAB = threading.local()          # persona._Suche setzt VORAB.melden: Zwischenstand nur aus Stichworten


def guide_block(query: str, ausser: set[str] | None = None) -> str:
    """Die besten Treffer für den System-Prompt – ein Vorgeschmack. Wer mehr
    braucht, sucht mit gedaechtnis_suchen selbst. Hat der laufende Chat aufgeräumt,
    kommen zuerst passende Stellen aus seinem ausgelagerten Teil.
    `ausser`: refs, die schon im Prompt stehen (Kern-Gedächtnis) – sie zählen nicht als Treffer.
    Ist VORAB.melden gesetzt, bekommt es vor der vollen Suche den Block nur aus Stichworten
    (ohne Index und Encoder, meist in Millisekunden fertig)."""
    _ZEITEN.messen = {}
    _MERK.worte = {}
    uhr = time.perf_counter()
    try:
        ref, kontext, ausgelagert = _laufender_chat()
        _messen("Chat", uhr)
        melden = getattr(VORAB, "melden", None)
        if melden is not None:
            try:
                melden(_treffer_block(query, ref, kontext, ausgelagert, nur_worte=True, ausser=ausser))
            except Exception:
                pass
        return _treffer_block(query, ref, kontext, ausgelagert, ausser=ausser)
    finally:
        _zeiten_melden(_ZEITEN.__dict__.pop("messen", None) or {})
        _MERK.__dict__.pop("worte", None)


def _treffer_block(query: str, ref: str, kontext: list[str], ausgelagert: int, nur_worte: bool = False,
                   ausser: set[str] | None = None) -> str:
    vorher, _NUR_PROMPT.an = getattr(_NUR_PROMPT, "an", False), True   # Fremdes belegt keine Plätze
    try:
        if nur_worte:
            vorhin = _vorhin_im_chat(query, ref, kontext, nur_worte=True) if ref and ausgelagert else []
            hits = search(query, GUIDE_KANDIDATEN, nur_worte=True)
        else:
            vorhin = _vorhin_im_chat(query, ref, kontext) if ref and ausgelagert else []
            hits = search(query, GUIDE_KANDIDATEN)
    finally:
        _NUR_PROMPT.an = vorher
    # Berichte, Werkzeug-Ergebnisse, Netz-Inhalt und Exporte nie ungefragt in den Systemprompt –
    # über gedaechtnis_suchen kommen sie gerahmt (Fremdes mit Netz-Marke). Was schon im Prompt steht, zählt nicht.
    weg = set(ausser or ()) | ({ref} if ref else set())
    hits = [h for h in hits if _fuer_prompt(h) and h.get("ref") not in weg]
    if not nur_worte:                  # unsichere Treffer nur nach Bedeutung: lieber keiner als ein falscher
        ab = schwelle("vorgeschmack")
        hits, vorhin = (_sicher(hits, ab)[:GUIDE_K], _sicher(vorhin, ab))
    else:
        hits = hits[:GUIDE_K]
    if not hits and not vorhin and not ausgelagert:
        return ""
    import memory                      # lazy, wie überall hier (Import-Kreis vermeiden)
    lines = [""]
    if ausgelagert:
        nr = ref.rsplit(":", 1)[-1]
        lines += ["# 🔁 Vorhin in diesem Chat (ausgelagert)",
                  f"Dieser Chat (#{nr}) ist länger als dein Kontext: {ausgelagert} ältere Nachrichten sind "
                  "ausgelagert. Brauchst du etwas von früher, such mit gedaechtnis_suchen und quelle "
                  "„aktuell“." + (" Passend zur Nachricht:" if vorhin else ""), ""]
        lines += [_zeile(h, memory) for h in vorhin] + [""]
    if hits:
        lines += [
            "# 🧭 Was aus dem Gedächtnis zur aktuellen Frage passen könnte",
            "Automatisch gesucht mit deiner letzten Nachricht – ein Vorgeschmack, nicht alles. "
            "Reicht das nicht, such selbst mit gedaechtnis_suchen (eigene Suchworte, Quelle, Zeitraum) "
            "und lies mit gedaechtnis_lesen weiter. " + memory.DATEN_HINWEIS,
            "",
        ]
        lines += [_zeile(h, memory) for h in hits]
    return "\n".join(lines).rstrip()
