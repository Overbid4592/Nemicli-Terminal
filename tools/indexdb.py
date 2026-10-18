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
import re
import sqlite3
import threading
import time
from array import array
from datetime import datetime
from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

_DB_NEW = _ROOT / "learned" / "memory.db"
_DB_OLD = _ROOT / "learned" / "index.sqlite"
CHUNK = 900
OVERLAP = 80
SEARCH_K = 8
MIN_SCORE = 0.42
BACKFILL_BATCH = 24
UMWANDELN_JE_LAUF = 2000         # alte JSON-Vektoren je Bibliothekar-Lauf in float32 umschreiben
SQL_WARTEN_S = 15.0              # so lange wartet ein Zugriff auf eine gerade schreibende Verbindung
BERICHTE_BEHALTEN = 3            # so viele Berichte bleiben als Datei; ältere leben nur noch als Vektoren
EMBED_BATCH = 16                 # so viele Brocken je Encoder-Aufruf – dazwischen wird Fortschritt gemeldet
BERICHTE_JE_RUNDE = 2            # Berichte je Bibliothekar-Lauf (ein Wache-Bericht = bis zu 60 Brocken ≈ 90 s CPU)
WISSEN_JE_RUNDE = 3              # geänderte Wissen-Dateien je Bibliothekar-Lauf
CHAT_SCHRITT = 25_000            # Token: der laufende Chat steht im Kontext und wird erst je so viel Zuwachs eingebettet
NACHZUEGLER_CHATS = 3            # so viele zuletzt benutzte andere Chats holt der Bibliothekar nach

# Unterbrechen: Beginnt eine neue Runde, hört after_turn nach dem laufenden Stapel auf –
# der Chat bekommt Encoder und CPU. Was fehlt, folgt im nächsten Lauf (Brocken ohne Vektor
# holt nachvektorisieren nach). Ein Zähler statt eines Schalters: niemand muss zurücksetzen.
_unterbrechung = 0
_lauf_marke: int | None = None   # Zählerstand beim Start des laufenden after_turn (None = keiner läuft)
_chat_stand: dict[str, int] = {}  # Chat-Ref → Token beim letzten Einbetten (in dieser Sitzung)
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


def _unterbrochen() -> bool:
    return _lauf_marke is not None and _lauf_marke != _unterbrechung


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
    con = sqlite3.connect(str(path), timeout=SQL_WARTEN_S)
    # WAL: Lesen (Prompt-Suche) und Schreiben (Bibliothekar) blockieren sich nicht gegenseitig.
    # Die Einstellung bleibt in der Datei; /backup kopiert über die Backup-Schnittstelle samt WAL.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    # REPLACE löscht die alte Zeile – ohne diesen Schalter liefe der Lösch-Trigger
    # dafür nicht, und der Stichwort-Index behielte eine Leiche.
    con.execute("PRAGMA recursive_triggers=ON")
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
    return con


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
    if "vec_modell" not in spalten:
        # Welches Modell den Vektor gerechnet hat. Alles davor war Qwen3-Embedding.
        con.execute("ALTER TABLE chunks ADD COLUMN vec_modell TEXT DEFAULT ''")
        alt = [(_alt_kennung(v), h) for h, v in
               con.execute("SELECT hash, vec FROM chunks WHERE vec IS NOT NULL").fetchall()]
        con.executemany("UPDATE chunks SET vec_modell=? WHERE hash=?", alt)
        con.commit()
    # Abgleich des Such-Index (_zustand_lesen) ohne die Vektoren: datum und vec_modell liegen hinter dem
    # großen vec-Text, ohne diesen Index liest SQLite dafür jede Zeile ganz.
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
    return hashlib.sha1(f"{kind}\n{ref}\n{text}".encode("utf-8")).hexdigest()[:20]


def _pieces(text: str) -> list[str]:
    text = (text or "").strip()
    if len(text) < 20:
        return []
    if len(text) <= CHUNK:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + CHUNK].strip())
        i += CHUNK - OVERLAP
    return [p for p in out if len(p) >= 20]


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


def _upsert(kind: str, ref: str, texts: list[str]) -> int:
    pieces = []
    for t in texts:
        pieces.extend(_pieces(t))
    if not pieces:
        return 0
    hashes = [_hash(kind, ref, p) for p in pieces]
    con = _connect()
    have = {
        r[0]
        for r in con.execute(
            f"SELECT hash FROM chunks WHERE hash IN ({','.join('?' * len(hashes))})",
            hashes,
        )
    }
    stale = [
        r[0]
        for r in con.execute("SELECT hash FROM chunks WHERE ref=?", (ref,))
        if r[0] not in hashes
    ]
    if stale:
        con.executemany("DELETE FROM chunks WHERE hash=?", [(h,) for h in stale])
    new = [(h, p) for h, p in zip(hashes, pieces) if h not in have]
    if not new:
        con.commit()
        con.close()
        if stale:
            _invalidate()
        return 0
    vecs = _einbetten([p for _, p in new])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    datum = _datum_fuer(kind, ref) or now
    kennung = _kennung() if vecs else ""
    rows = []
    for i, (h, p) in enumerate(new):
        vec = vec_packen(vecs[i]) if vecs and i < len(vecs) else None
        rows.append((h, kind, ref, p, vec, now, datum, kennung if vec else ""))
    con.executemany(
        "INSERT OR REPLACE INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    _invalidate()
    return len(rows)


def _einbetten(texte: list[str]) -> list[list[float]] | None:
    """Portionsweise einbetten und dabei Fortschritt melden. None, wenn der Encoder fehlt."""
    try:
        import memory
    except Exception:
        return None
    try:
        if not memory.encoder_loaded():
            wohin = "auf die GPU" if memory.encoder_geraet() == "cuda" else "auf die CPU"
            _melden(f"{_kontext} · lade Qwen3-Embedding {wohin} …" if _kontext else f"lade Qwen3-Embedding {wohin} …")
    except Exception:
        pass
    out: list[list[float]] = []
    n = len(texte)
    # Großer Auftrag? Im Modus „auto“ rechnet er auf der GPU und geht danach zurück in den RAM.
    with memory.auftrag(n) as auf_gpu:
        wo = " auf der GPU" if auf_gpu else ""
        for i in range(0, n, EMBED_BATCH):
            if _unterbrochen():
                return out or None                # Rest landet ohne Vektor, nachvektorisieren holt ihn
            teil = texte[i:i + EMBED_BATCH]
            _melden(f"{_kontext} · Brocken {i}/{n}{wo}" if _kontext else f"Brocken {i}/{n}{wo}")
            try:
                vecs = memory._embed_texts(teil)
            except Exception:
                vecs = None
            if not vecs:
                return None
            out.extend(vecs)
    _melden(f"{_kontext} · Brocken {n}/{n}" if _kontext else f"Brocken {n}/{n}")
    return out


def drop_ref(ref: str) -> None:
    con = _connect()
    con.execute("DELETE FROM chunks WHERE ref=?", (ref,))
    con.commit()
    con.close()
    _invalidate()


def _chat_ref(chat_id: int) -> str:
    try:
        import chatstore
        return chatstore.index_ref(chat_id)
    except Exception:
        return f"chat:{chat_id}"


def _profil_bedingung() -> tuple[str, list]:
    """SQL: Chats anderer Persönlichkeiten ausblenden (chat:<Profil>:<Nr>, chatmd:<Profil>:…)."""
    try:
        import profilordner
        eigen = profilordner.aktiv()
    except Exception:
        return "", []
    return ("(c.ref NOT GLOB 'chat:*:*' OR c.ref GLOB ?) AND (c.ref NOT GLOB 'chatmd:*:*' OR c.ref GLOB ?)",
            [f"chat:{eigen}:*", f"chatmd:{eigen}:*"])


def _aktives_profil() -> str | None:
    try:
        import profilordner
        return profilordner.aktiv()
    except Exception:
        return None


def _fremdes_profil(ref: str, aktiv: str | None = None) -> bool:
    """Chat einer anderen Persönlichkeit? `aktiv` vorher einmal holen, wenn viele Refs geprüft werden –
    profilordner.aktiv() liest Einstellungen und Persönlichkeiten von der Platte."""
    teile = str(ref).split(":")
    if teile[0] not in ("chat", "chatmd") or len(teile) < 3:
        return False
    aktiv = aktiv or _aktives_profil()
    return aktiv is not None and teile[1] != aktiv


def ingest_chat(chat_id: int, title: str, prompts: list[str], messages: list) -> int:
    global _kontext
    _kontext = f"Chat „{title[:24]}“" if title else "Chat"
    texts = []
    if title:
        texts.append(f"Chat #{chat_id}: {title}")
    for p in prompts or []:
        if p and p.strip():
            texts.append("Nutzer: " + p.strip())
    for m in messages or []:
        role = m.get("role") or ""
        body = _msg_text(m)
        if not body:
            continue
        if role == "assistant":
            texts.append("Nemi: " + body)
        elif role == "user":
            texts.append("Nutzer: " + body)
        else:
            texts.append(body)
    return _upsert("chat", _chat_ref(chat_id), texts)


_ARTEN = {"skill": "Skill", "code": "Snippet", "bericht": "Bericht", "chatmd": "Chat-Mitschrift",
          "wissen": "Wissen"}


def ingest_file(kind: str, path: Path, ref: str | None = None) -> int:
    global _kontext
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    _kontext = f"{_ARTEN.get(kind, kind)} {path.name}"
    return _upsert(kind, ref or f"{kind}:{path.name}", [f"{path.name}\n{text}"])


def ingest_note(note_id: int, text: str, kind: str = "fakt") -> int:
    global _kontext
    _kontext = f"Notiz {note_id}"
    return _upsert("note", f"note:{note_id}", [f"({kind}) {text}"])


def ingest_chat_file(chat_id: int) -> int:
    """Liest einen Chat aus der Chat-Datenbank (und eine alte NNN.md, falls da) und schreibt Vektoren in memory.db."""
    try:
        import chatstore
    except Exception:
        return 0
    data = chatstore.load(chat_id)
    if not data:
        return 0
    n = ingest_chat(
        data.get("id") or chat_id,
        data.get("title") or "",
        data.get("prompts") or [],
        data.get("messages") or [],
    )
    try:
        md = chatstore.md_path(chat_id)
        if md.exists():
            teile = chatstore.index_ref(chat_id).split(":")
            n += ingest_file("chatmd", md, f"chatmd:{teile[1]}:{md.name}" if len(teile) == 3 else None)
    except Exception:
        pass
    return n


def chat_tokens(chat_id: int) -> int:
    """Geschätzte Token aller gespeicherten Nachrichten des Chats (~4 Zeichen je Token)."""
    try:
        import chatstore
        data = chatstore.load(int(chat_id)) or {}
    except Exception:
        return 0
    return sum(len(_msg_text(m)) for m in data.get("messages") or []) // 4


def chat_faellig(chat_id: int, schritt: int = CHAT_SCHRITT) -> bool:
    """Ist der Chat seit dem letzten Einbetten um `schritt` Token gewachsen?"""
    return chat_tokens(chat_id) - _chat_stand.get(_chat_ref(chat_id), 0) >= schritt


def chat_einbetten(chat_id: int) -> int:
    n = ingest_chat_file(int(chat_id))
    _chat_stand[_chat_ref(chat_id)] = chat_tokens(chat_id)
    return n


def nachzuegler_chats(aktuell: int | None, anzahl: int = NACHZUEGLER_CHATS) -> int:
    """Die zuletzt benutzten anderen Chats vollständig einbetten – was dort unter
    CHAT_SCHRITT liegen blieb (Chat gewechselt, Programm beendet), kommt so nach."""
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


def ingest_all_notes() -> int:
    """Liest learned/memory.json und legt Notiz-Vektoren in memory.db."""
    try:
        import memory as mem
    except Exception:
        return 0
    entries = mem.all_entries()
    live = {f"note:{e['id']}" for e in entries}
    con = _connect()
    have = [r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='note'")]
    con.close()
    for ref in have:
        if ref not in live:
            drop_ref(ref)
    n = 0
    for e in entries:
        n += ingest_note(e["id"], e.get("text") or "", e.get("kind") or "fakt")
    return n


def ingest_learned() -> int:
    """Liest Skills und Code-Snippets von der Platte in memory.db."""
    n = 0
    try:
        import learn
        for path, _t in learn.list_skills():
            n += ingest_file("skill", path)
        for path, _t in learn.list_snippets():
            n += ingest_file("code", path)
    except Exception:
        pass
    return n


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


def nachvektorisieren(limit: int = BACKFILL_BATCH) -> int:
    """Brocken ohne Vektor (Encoder war nicht da) oder mit Vektor eines anderen
    Modells (Modell gewechselt) nachträglich einbetten."""
    kennung = _kennung()
    if not kennung:
        return 0
    con = _connect()
    rows = con.execute("SELECT hash, text FROM chunks WHERE rowid IN (SELECT rowid FROM chunks "
                       "INDEXED BY idx_chunks_stand WHERE vec_modell != ? LIMIT ?)", (kennung, limit)).fetchall()
    con.close()
    if not rows:
        return 0
    global _kontext
    _kontext = "Nachzügler ohne passenden Vektor"
    vecs = _einbetten([t for _h, t in rows])
    if not vecs:
        return 0
    con = _connect()
    con.executemany("UPDATE chunks SET vec=?, vec_modell=? WHERE hash=?",
                    [(vec_packen(v), kennung, h) for (h, _t), v in zip(rows, vecs)])
    con.commit()
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
    drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'")}
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
    alte = dateien[:-behalten] if behalten > 0 else dateien
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


def _upsert_geordnet(kind: str, ref: str, pieces: list[str]) -> int | None:
    """Wie _upsert, aber die Brocken stehen danach in Dokument-Reihenfolge (rowid),
    so liest gedaechtnis_lesen sie. Vorhandene Vektoren werden übernommen.
    Rückgabe: neu eingebettete Brocken, None wenn sich nichts geändert hat."""
    reihe: list[tuple[str, str]] = []
    gesehen: set[str] = set()
    for p in pieces:
        h = _hash(kind, ref, p)
        if h not in gesehen:
            gesehen.add(h)
            reihe.append((h, p))
    con = _connect()
    try:
        alt = {h: (v, m, u) for h, v, m, u in con.execute(
            "SELECT hash, vec, vec_modell, updated FROM chunks WHERE ref=? ORDER BY rowid", (ref,))}
    finally:
        con.close()
    if list(alt) == [h for h, _p in reihe]:
        return None
    neu = [(h, p) for h, p in reihe if h not in alt]
    vecs = _einbetten([p for _h, p in neu]) if neu else None
    kennung = _kennung() if vecs else ""
    neue_vec = {h: vec_packen(vecs[i]) for i, (h, _p) in enumerate(neu) if vecs and i < len(vecs)}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    datum = _datum_fuer(kind, ref) or now
    rows = []
    for h, p in reihe:
        if h in alt:
            v, m, u = alt[h]
        else:
            v = neue_vec.get(h)
            m, u = (kennung if v else ""), now
        rows.append((h, kind, ref, p, v, u, datum, m))
    con = _connect()
    try:
        con.execute("DELETE FROM chunks WHERE ref=?", (ref,))
        con.executemany(
            "INSERT OR REPLACE INTO chunks(hash, kind, ref, text, vec, updated, datum, vec_modell) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    _invalidate()
    return len(neu)


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

    Läuft nur, wenn sich am Ordner etwas geändert hat. Dann wird über alle Dateien
    entdoppelt (wortgleiche Sätze nur einmal, siehe wissen.py) und jede Datei mit
    geänderten Brocken neu geschrieben; gelöschte Dateien verschwinden aus dem Index.
    `je_runde`: höchstens so viele geänderte Dateien je Lauf (None = alle), der Rest
    folgt in der nächsten Runde. Rückgabe: neu eingebettete Brocken."""
    global _kontext
    try:
        import wissen
    except Exception:
        return 0
    basis = wissen.ordner()
    liste = wissen.dateien(basis)
    stempel = wissen.stempel(liste, basis)
    if stempel == _stand("wissen") or _unterbrochen():
        return 0
    texte, unlesbar = [], set()
    for p in liste:
        try:
            texte.append(wissen.lesen(p))
        except Exception:
            texte.append("")
            unlesbar.add(f"wissen:{wissen.name(p, basis)}")
    absaetze, _entfernt = wissen.entdoppeln(texte)
    live: dict[str, list[str]] = {}
    for p, a in zip(liste, absaetze):
        rel = wissen.name(p, basis)
        live[f"wissen:{rel}"] = wissen.brocken(f"📚 {rel}", a, CHUNK)
    con = _connect()
    drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='wissen'")}
    con.close()
    for ref in drin - set(live):
        drop_ref(ref)
    n, geschrieben, fertig = 0, 0, True
    for ref, stuecke in live.items():
        if ref in unlesbar:                       # alten Stand behalten
            continue
        if not stuecke:                           # leer oder ganz doppelt
            if ref in drin:
                drop_ref(ref)
            continue
        if (je_runde is not None and geschrieben >= je_runde) or _unterbrochen():
            fertig = False
            break
        _kontext = f"Wissen {ref.split(':', 1)[1]}"
        neu = _upsert_geordnet("wissen", ref, stuecke)
        if neu is not None:
            geschrieben += 1
            n += neu
    if fertig:
        _stand("wissen", stempel)
    return n


def after_turn(chat_id: int | None = None, status=None, alles: bool = False,
               chat_erzwingen: bool = False) -> int:
    """Bibliothekar nach der Runde: Dateien lesen → embedden → memory.db.

    chat_id: der gerade gespeicherte Chat (None = nur Notizen/Skills). Er steht im Kontext
             und wird nur je CHAT_SCHRITT Token Zuwachs eingebettet – oder sofort mit
             `chat_erzwingen` (Kontext aufgeräumt: der ausgelagerte Teil muss findbar sein).
    status:  Melder für den Fortschritt (Text mit „i/n“ → Balken in der Oberfläche).
    alles:   ohne Portionsgrenzen und ohne Unterbrechen – für /gedaechtnis indexieren.
    Beginnt währenddessen eine Runde (unterbrechen), bleiben die Rückstände für später."""
    global _status, _kontext, _lauf_marke
    _status, _kontext = status, ""
    _lauf_marke = None if alles else _unterbrechung
    n = 0
    try:
        if chat_id and (alles or chat_erzwingen or chat_faellig(chat_id)):
            n += chat_einbetten(int(chat_id))
        n += ingest_all_notes()
        n += ingest_learned()
        rueckstand = [
            lambda: nachzuegler_chats(chat_id),
            lambda: backfill(10_000 if alles else BACKFILL_BATCH, ausser=None if alles else chat_id),
            lambda: nachvektorisieren(1_000_000 if alles else BACKFILL_BATCH),
            lambda: ingest_berichte(je_runde=None if alles else BERICHTE_JE_RUNDE)[0],
            lambda: ingest_wissen(None if alles else WISSEN_JE_RUNDE),
            lambda: vektoren_umwandeln(1_000_000 if alles else UMWANDELN_JE_LAUF) and 0,   # zählt nicht als neu
        ]
        for schritt in rueckstand:
            if _unterbrochen():
                break
            try:
                n += schritt()
            except Exception:
                pass
    finally:
        _status, _kontext, _lauf_marke = None, "", None
    return n


def offen() -> dict:
    """Was der Bibliothekar noch vor sich hat – für /gedaechtnis."""
    con = _connect()
    try:
        ohne = int(con.execute("SELECT COUNT(*) FROM chunks INDEXED BY idx_chunks_stand WHERE vec_modell != ?",
                               (_kennung(),)).fetchone()[0])
        drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'")}
        gesamt = int(con.execute("SELECT COUNT(*) FROM chunks INDEXED BY idx_chunks_stand").fetchone()[0])
    finally:
        con.close()
    ordner = _ROOT / "Berichte"
    berichte = [p for p in ordner.glob("*.md")] if ordner.exists() else []
    return {"brocken": gesamt, "ohne_vektor": ohne,
            "berichte_offen": sum(1 for p in berichte if f"bericht:{p.name}" not in drin)}


def backfill(limit: int = BACKFILL_BATCH, ausser: int | None = None) -> int:
    """Indexiert noch fehlende Chats/Skills/Snippets, portionsweise. `ausser`: der laufende Chat –
    der steht im Kontext und wird im eigenen Takt eingebettet (CHAT_SCHRITT)."""
    done = 0
    con = _connect()
    have_refs = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks")}
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
        return _index_aktualisieren(voll_erlaubt=warten)
    finally:
        _INDEX_SPERRE.release()


def _index_im_hintergrund() -> None:
    global _index_bau
    with _BAU_SPERRE:
        if _index_bau is not None and _index_bau.is_alive():
            return
        _index_bau = threading.Thread(target=_index, name="nemi-index", daemon=True)
        _index_bau.start()


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
        for rowid, h, kind, ref, text, vec_s, datum, vec_modell in con.execute(
                "SELECT rowid, hash, kind, ref, text, vec, datum, vec_modell FROM chunks"):
            meta.append((kind, ref, text, datum or "", rowid))
            hat = vec_s is not None and bool(kennung) and vec_modell == kennung
            zustand[rowid] = (h, hat, datum or "")
            if not hat:
                continue                  # kein Vektor oder von einem anderen Modell
            v = _vektor(vec_s, np)
            if v is not None:
                vecs.append(v)
                vec_zeilen.append(rowid)
        con.close()
    except Exception:
        pass
    mat = None
    if vecs and np is not None:
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
                "SELECT rowid, kind, ref, text, vec, datum FROM chunks WHERE rowid IN "
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
        kind, ref, text, vec_s, datum = zeilen[r]
        meta_von[r] = (kind, ref, text, datum or "", r)
        if jetzt[r][1] and (v := _vektor(vec_s, np)) is not None:
            dazu_r.append(r)
            dazu_v.append(v)
    mat, vz = _CACHE["mat"], list(_CACHE["vec_zeilen"])
    raus = weg | vec_weg | set(vec_dazu)
    if mat is not None and raus:
        behalten = [j for j, r in enumerate(vz) if r not in raus]
        mat, vz = mat[behalten], [vz[j] for j in behalten]
    if dazu_v:
        mat = np.vstack(([mat] if mat is not None and len(mat) else []) + dazu_v)
        vz += dazu_r
    if mat is not None and not len(mat):
        mat = None
    meta = [meta_von[r] for r in sorted(meta_von)]
    _CACHE_setzen(stamp, kennung, _CACHE["db"], meta, mat, [], vz, jetzt)
    return True


def _CACHE_setzen(stamp, kennung, db, meta, mat, vecs, vec_zeilen, zustand) -> None:
    zeile = {m[4]: i for i, m in enumerate(meta)}
    idx = [zeile[r] for r in vec_zeilen]
    _CACHE.update({"stamp": stamp, "meta": meta, "mat": mat, "vecs": vecs, "idx": idx, "zeile": zeile,
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
    return {"kind": kind, "ref": ref, "text": text, "datum": datum, "score": score, "wie": wie}


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


def _fts_anfrage(frage: str) -> str:
    """Frage → FTS5-Ausdruck: Wörter als Phrase, ab 4 Zeichen mit Präfix, ODER-verknüpft."""
    woerter = []
    for w in re.findall(r"[\w.\-]+", frage.lower()):
        w = w.strip(".-")
        if len(w) < 2 or w in _FUELLWOERTER or w in woerter:
            continue
        woerter.append(w)
    teile = []
    for w in woerter[:16]:
        sicher = w.replace('"', "")
        teile.append(f'"{sicher}"*' if len(sicher) >= 4 else f'"{sicher}"')
    return " OR ".join(teile)


def _filter_sql(arten, seit, bis, ref_praefix) -> tuple[str, list]:
    teile, werte = [], []
    if arten:
        teile.append(f"c.kind IN ({','.join('?' * len(arten))})")
        werte += list(arten)
    if seit:
        teile.append("c.datum >= ?")
        werte.append(seit)
    if bis:
        teile.append("c.datum <= ?")
        werte.append(bis + " 99")               # ganzer Tag
    if ref_praefix:                             # "ref$" = genau diese Quelle
        teile.append("(" + " OR ".join("c.ref = ?" if p.endswith("$") else "c.ref LIKE ?"
                                        for p in ref_praefix) + ")")
        werte += [p[:-1] if p.endswith("$") else p + "%" for p in ref_praefix]
    profil, profil_werte = _profil_bedingung()
    if profil:
        teile.append(profil)
        werte += profil_werte
    return (" AND " + " AND ".join(teile)) if teile else "", werte


def _passt(meta_row: tuple, arten, seit, bis, ref_praefix, aktiv: str | None = None) -> bool:
    kind, ref, _t, datum = meta_row[:4]
    if _fremdes_profil(ref, aktiv):
        return False
    if arten and kind not in arten:
        return False
    if seit and (datum or "") < seit:
        return False
    if bis and (datum or "") > bis + " 99":
        return False
    if ref_praefix and not any(ref == p[:-1] if p.endswith("$") else ref.startswith(p) for p in ref_praefix):
        return False
    return True


def suchen(frage: str, k: int = SEARCH_K, *, arten=None, seit: str = "", bis: str = "",
           ref_praefix=None, laden: bool = False) -> list[dict]:
    """Hybride Suche über memory.db.

    Stichworte (FTS5, BM25) finden exakte Begriffe; Vektoren finden Sinnverwandtes.
    Beide Ranglisten werden per Reciprocal Rank Fusion vereint – ein Brocken, den
    beide finden, steht oben. `laden=True` lädt den Encoder notfalls (~6 s): nur
    für das Werkzeug der Persönlichkeit, nie im Prompt-Pfad. Ohne Vektoren oder
    Encoder bleibt die Stichwortsuche."""
    if not (frage or "").strip():
        return []
    uhr = time.perf_counter()
    ix = _index(warten=laden)            # Prompt-Pfad: nie auf einen Neuaufbau warten
    uhr = _messen("Index", uhr)
    if ix is not None and not ix["meta"]:
        return []
    meta = ix["meta"] if ix is not None else []
    punkte: dict[int, float] = {}        # rowid → Punkte
    wie: dict[int, set] = {}
    gefunden: dict[int, tuple] = {}      # rowid → (kind, ref, text, datum, rowid)

    ausdruck = _fts_anfrage(frage)
    if ausdruck:
        bedingung, werte = _filter_sql(arten, seit, bis, ref_praefix)
        try:
            con = _connect()
            zeilen = con.execute(
                "SELECT c.rowid, c.kind, c.ref, c.text, c.datum FROM chunks_fts JOIN chunks c "
                f"ON c.rowid = chunks_fts.rowid WHERE chunks_fts MATCH ?{bedingung} "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                [ausdruck, *werte, KANDIDATEN]).fetchall()
            con.close()
        except sqlite3.Error:
            zeilen = []
        for rang, (rowid, kind, ref, text, datum) in enumerate(zeilen):
            gefunden[rowid] = (kind, ref, text, datum or "", rowid)
            punkte[rowid] = punkte.get(rowid, 0.0) + 1.0 / (RRF_K + rang)
            wie.setdefault(rowid, set()).add("wort")
    uhr = _messen("Stichworte", uhr)

    if ix is not None and (ix["mat"] is not None or ix["vecs"]):
        qe = None
        try:
            import memory
            if laden:
                qe = memory._embed_texts([frage], query=True)
            elif memory.encoder_loaded():
                qe = memory.frage_einbetten(frage)   # wartet nie auf den Bibliothekar
            else:
                memory.warm_encoder()          # ab der nächsten Frage auch nach Bedeutung
        except Exception:
            qe = None
        uhr = _messen("Einbetten", uhr)
        if qe:
            aktiv = _aktives_profil()
            bewertet = []                # beste zuerst, gefiltert nur bis genug beisammen sind
            for s, i in sorted(_scores(ix, qe[0], MIN_SCORE * 0.75), key=lambda x: -x[0]):
                if _passt(meta[i], arten, seit, bis, ref_praefix, aktiv):
                    bewertet.append((s, i))
                    if len(bewertet) >= KANDIDATEN:
                        break
            for rang, (_s, i) in enumerate(bewertet):
                rowid = meta[i][4]
                gefunden.setdefault(rowid, meta[i])
                punkte[rowid] = punkte.get(rowid, 0.0) + 1.0 / (RRF_K + rang)
                wie.setdefault(rowid, set()).add("sinn")
        _messen("Vergleich", uhr)

    beste = sorted(punkte.items(), key=lambda x: -x[1])[:k]
    return [_hit(gefunden[r], round(p * RRF_K / 2, 3),
                 "+".join(sorted(wie.get(r, ())))) for r, p in beste]


def quelle_lesen(ref: str, max_zeichen: int = 12_000, ab: int = 1) -> dict | None:
    """Die Brocken einer Quelle (z. B. 'chat:152') in Reihenfolge, ab Abschnitt `ab`
    (1 = Anfang), so viele ganze Abschnitte wie in `max_zeichen` passen. `weiter`
    nennt den nächsten Abschnitt (0 = Ende). Überlappungen werden nicht entfernt."""
    if _fremdes_profil(ref):                     # Chats einer anderen Persönlichkeit
        return None
    try:
        con = _connect()
        zeilen = con.execute("SELECT kind, text, datum FROM chunks WHERE ref=? ORDER BY rowid",
                             (ref,)).fetchall()
        con.close()
    except sqlite3.Error:
        return None
    if not zeilen:
        return None
    start = min(max(1, int(ab or 1)), len(zeilen))
    teile, laenge, weiter = [], 0, 0
    for nr, (_k, t, _d) in enumerate(zeilen[start - 1:], start):
        if teile and laenge + len(t) > max_zeichen:
            weiter = nr
            break
        teile.append(t)
        laenge += len(t) + 3
    text = "\n…\n".join(teile)
    gekuerzt = bool(weiter) or len(text) > max_zeichen
    return {"ref": ref, "kind": zeilen[0][0], "datum": zeilen[0][2] or "",
            "text": text[:max_zeichen], "gekuerzt": gekuerzt, "brocken": len(zeilen),
            "ab": start, "weiter": weiter}


def search(query: str, k: int = SEARCH_K) -> list[dict]:
    """Hybride Suche für den Prompt-Pfad (persona.build_system_prompt).

    Lädt NIE das Embedding-Modell nach: ist der Encoder kalt, wird er im
    Hintergrund vorgewärmt, und diese Frage läuft nur über die Stichwortsuche –
    der Chat wartet keine Sekunde."""
    return suchen(query, k, laden=False)


GUIDE_K = 3                        # Vorgeschmack im Prompt – tiefer sucht die Persönlichkeit selbst


VORHIN_K = 3                       # Treffer aus dem ausgelagerten Teil des laufenden Chats


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


def _vorhin_im_chat(query: str, ref: str, kontext: list[str]) -> list[dict]:
    """Treffer aus dem laufenden Chat, die nicht mehr im Kontext stehen."""
    raus = []
    for h in suchen(query, VORHIN_K * 4, arten=("chat",), ref_praefix=(ref + "$",)):
        text = h.get("text") or ""
        if text.startswith("Chat #"):                       # Titelzeile
            continue
        for vorn in ("Nutzer: ", "Nemi: "):
            if text.startswith(vorn):
                text = text[len(vorn):]
                break
        probe = text.strip()[:200]
        if probe and any(probe in k for k in kontext):     # steht noch im Kontext
            continue
        raus.append(h)
        if len(raus) >= VORHIN_K:
            break
    return raus


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


def guide_block(query: str) -> str:
    """Die besten Treffer für den System-Prompt – ein Vorgeschmack. Wer mehr
    braucht, sucht mit gedaechtnis_suchen selbst. Hat der laufende Chat aufgeräumt,
    kommen zuerst passende Stellen aus seinem ausgelagerten Teil."""
    _ZEITEN.messen = {}
    uhr = time.perf_counter()
    try:
        ref, kontext, ausgelagert = _laufender_chat()
        uhr = _messen("Chat", uhr)
        vorhin = _vorhin_im_chat(query, ref, kontext) if ref and ausgelagert else []
        hits = [h for h in search(query, GUIDE_K + VORHIN_K) if not (ref and h.get("ref") == ref)][:GUIDE_K]
    finally:
        _zeiten_melden(_ZEITEN.__dict__.pop("messen", None) or {})
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
