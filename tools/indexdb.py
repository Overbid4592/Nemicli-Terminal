"""
indexdb.py - Bibliothekar nach der Chat-Runde.

Ablauf (nicht im Dialog):
  Chat → chats/*.json + *.md + learned/memory.json
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
BERICHTE_BEHALTEN = 3            # so viele Berichte bleiben als Datei; ältere leben nur noch als Vektoren
EMBED_BATCH = 16                 # so viele Brocken je Encoder-Aufruf – dazwischen wird Fortschritt gemeldet
BERICHTE_JE_RUNDE = 2            # Berichte je Bibliothekar-Lauf (ein Wache-Bericht = bis zu 60 Brocken ≈ 90 s CPU)
WISSEN_JE_RUNDE = 3              # geänderte Wissen-Dateien je Bibliothekar-Lauf

# Fortschritt nach außen: after_turn(status=fn) setzt den Melder; die Einbett-Stellen
# rufen _melden("Chat #152 · Brocken 12/40"). Ein „i/n“ in der Meldung wird in der Oberfläche
# zum Balken (ui.progress_panel). Ohne Melder passiert nichts – Tests und Skripte merken nichts.
_status = None
_kontext = ""


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
    con = sqlite3.connect(str(path))
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


def _alt_kennung(vec_json: str) -> str:
    try:
        import embedder
        return embedder.alte_kennung(json.loads(vec_json))
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
        vec = json.dumps(vecs[i]) if vecs and i < len(vecs) else None
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


def _fremdes_profil(ref: str) -> bool:
    teile = str(ref).split(":")
    if teile[0] not in ("chat", "chatmd") or len(teile) < 3:
        return False
    try:
        import profilordner
        return teile[1] != profilordner.aktiv()
    except Exception:
        return False


def ingest_chat(chat_id: int, title: str, prompts: list[str], messages: list) -> int:
    global _kontext
    _kontext = f"Chat #{chat_id}"
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
    """Liest chats/NNN.json (und .md, falls da) und schreibt Vektoren in memory.db."""
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
        r = con.execute("SELECT COUNT(*), SUM(vec IS NULL OR vec_modell != ?) FROM chunks WHERE ref=?",
                        (_kennung(), ref)).fetchone()
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
    rows = con.execute("SELECT hash, text FROM chunks WHERE vec IS NULL OR vec_modell != ? LIMIT ?",
                       (kennung, limit)).fetchall()
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
                    [(json.dumps(v), kennung, h) for (h, _t), v in zip(rows, vecs)])
    con.commit()
    con.close()
    _invalidate()
    return len(vecs)


def ingest_berichte(behalten: int | None = None, je_runde: int | None = BERICHTE_JE_RUNDE) -> tuple[int, int]:
    """Berichte/*.md (Wache-Weckrufe, Zeitplan-Aufträge) → Vektoren (kind „bericht“).
    Danach bleiben nur die `behalten` neuesten als Datei liegen; die älteren wandern in
    den Papierkorb (snapshot, 30 Tage) – aber NUR, wenn ihre Vektoren wirklich in der
    DB sind. Ältere Berichte bleiben übers Gedächtnis findbar, nicht als Datei.
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
    neue_vec = {h: json.dumps(vecs[i]) for i, (h, _p) in enumerate(neu) if vecs and i < len(vecs)}
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


def wissen_namen() -> list[str]:
    """Dateien aus Wissen/, die im Gedächtnis sind (Pfad relativ zu Wissen/).
    Legt keine Datenbank an – läuft auch beim Bau des System-Prompts."""
    if not _db_path().exists():
        return []
    con = _connect()
    try:
        refs = [r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='wissen'")]
    finally:
        con.close()
    return sorted((r.split(":", 1)[1] for r in refs), key=str.casefold)


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
    if stempel == _stand("wissen"):
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
        if je_runde is not None and geschrieben >= je_runde:
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


def after_turn(chat_id: int | None = None, status=None, alles: bool = False) -> int:
    """Bibliothekar nach der Runde: Dateien lesen → embedden → memory.db.

    chat_id: der gerade gespeicherte Chat (None = nur Notizen/Skills).
    status:  Melder für den Fortschritt (Text mit „i/n“ → Balken in der Oberfläche).
    alles:   ohne Portionsgrenzen (Chats, Berichte, Nachzügler) – für /gedaechtnis indexieren."""
    global _status, _kontext
    _status, _kontext = status, ""
    n = 0
    try:
        if chat_id:
            n += ingest_chat_file(int(chat_id))
        n += ingest_all_notes()
        n += ingest_learned()
        n += backfill(10_000 if alles else BACKFILL_BATCH)
        n += nachvektorisieren(1_000_000 if alles else BACKFILL_BATCH)
        try:
            n += ingest_berichte(je_runde=None if alles else BERICHTE_JE_RUNDE)[0]
        except Exception:
            pass
        try:
            n += ingest_wissen(None if alles else WISSEN_JE_RUNDE)
        except Exception:
            pass
    finally:
        _status, _kontext = None, ""
    return n


def offen() -> dict:
    """Was der Bibliothekar noch vor sich hat – für /gedaechtnis."""
    con = _connect()
    try:
        ohne = int(con.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NULL OR vec_modell != ?",
                               (_kennung(),)).fetchone()[0])
        drin = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks WHERE kind='bericht'")}
        gesamt = int(con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    finally:
        con.close()
    ordner = _ROOT / "Berichte"
    berichte = [p for p in ordner.glob("*.md")] if ordner.exists() else []
    return {"brocken": gesamt, "ohne_vektor": ohne,
            "berichte_offen": sum(1 for p in berichte if f"bericht:{p.name}" not in drin)}


def backfill(limit: int = BACKFILL_BATCH) -> int:
    """Indexiert noch fehlende Chats/Skills/Snippets, portionsweise."""
    done = 0
    con = _connect()
    have_refs = {r[0] for r in con.execute("SELECT DISTINCT ref FROM chunks")}
    con.close()
    try:
        import chatstore
        for meta in chatstore.list_chats():
            if done >= limit:
                return done
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

_CACHE: dict = {"stamp": None, "meta": None, "mat": None, "vecs": None, "idx": None, "zeile": {}}


def _db_stamp():
    try:
        st = _db_path().stat()
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def _invalidate() -> None:
    _CACHE["stamp"] = None


def _index() -> dict:
    """Alle Brocken als Matrix im Speicher (mit Metadaten). Billig, wenn sich
    an der DB nichts geändert hat."""
    kennung = _kennung()
    stamp = (_db_stamp(), kennung)
    if _CACHE["meta"] is not None and stamp[0] is not None and _CACHE["stamp"] == stamp:
        return _CACHE
    meta: list[tuple] = []
    vecs: list[list[float]] = []
    idx: list[int] = []
    try:
        con = _connect()
        for rowid, kind, ref, text, vec_s, datum, vec_modell in con.execute(
                "SELECT rowid, kind, ref, text, vec, datum, vec_modell FROM chunks"):
            i = len(meta)
            meta.append((kind, ref, text, datum or "", rowid))
            if not vec_s or not kennung or vec_modell != kennung:
                continue                  # kein Vektor oder von einem anderen Modell
            try:
                v = json.loads(vec_s)
            except Exception:
                continue
            vecs.append(v)
            idx.append(i)
        con.close()
    except Exception:
        pass
    mat = None
    if vecs:
        try:
            import numpy as np
            mat = np.asarray(vecs, dtype="float32")
            norm = np.linalg.norm(mat, axis=1, keepdims=True)
            norm[norm == 0] = 1.0
            mat /= norm
            vecs = []                 # numpy hat die Daten, Listen freigeben
        except Exception:
            mat = None                # ohne numpy: Notfall-Pfad in _scores
    zeile = {m[4]: i for i, m in enumerate(meta)}
    _CACHE.update({"stamp": stamp, "meta": meta, "mat": mat, "vecs": vecs, "idx": idx, "zeile": zeile})
    return _CACHE


def _scores(ix: dict, qv: list[float]) -> list[tuple[float, int]]:
    """(Ähnlichkeit, meta-Index) für alle Brocken mit Vektor."""
    mat, idx = ix["mat"], ix["idx"]
    if mat is not None:
        import numpy as np
        q = np.asarray(qv, dtype="float32")
        n = float(np.linalg.norm(q)) or 1.0
        sims = mat @ (q / n)
        return [(float(s), idx[j]) for j, s in enumerate(sims)]
    import memory
    return [(memory._cos(qv, v), idx[j]) for j, v in enumerate(ix["vecs"] or [])]


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


def _passt(meta_row: tuple, arten, seit, bis, ref_praefix) -> bool:
    kind, ref, _t, datum = meta_row[:4]
    if _fremdes_profil(ref):
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
    ix = _index()
    meta = ix["meta"] or []
    if not meta:
        return []
    punkte: dict[int, float] = {}
    wie: dict[int, set] = {}

    ausdruck = _fts_anfrage(frage)
    if ausdruck:
        bedingung, werte = _filter_sql(arten, seit, bis, ref_praefix)
        try:
            con = _connect()
            zeilen = con.execute(
                "SELECT c.rowid FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
                f"WHERE chunks_fts MATCH ?{bedingung} ORDER BY bm25(chunks_fts) LIMIT ?",
                [ausdruck, *werte, KANDIDATEN]).fetchall()
            con.close()
        except sqlite3.Error:
            zeilen = []
        for rang, (rowid,) in enumerate(zeilen):
            i = ix["zeile"].get(rowid)
            if i is not None:
                punkte[i] = punkte.get(i, 0.0) + 1.0 / (RRF_K + rang)
                wie.setdefault(i, set()).add("wort")

    if ix["mat"] is not None or ix["vecs"]:
        qe = None
        try:
            import memory
            if memory.encoder_loaded() or laden:
                qe = memory._embed_texts([frage], query=True)
            else:
                memory.warm_encoder()          # ab der nächsten Frage auch nach Bedeutung
        except Exception:
            qe = None
        if qe:
            bewertet = [(s, i) for s, i in _scores(ix, qe[0])
                        if s >= MIN_SCORE * 0.75 and _passt(meta[i], arten, seit, bis, ref_praefix)]
            bewertet.sort(key=lambda x: -x[0])
            for rang, (_s, i) in enumerate(bewertet[:KANDIDATEN]):
                punkte[i] = punkte.get(i, 0.0) + 1.0 / (RRF_K + rang)
                wie.setdefault(i, set()).add("sinn")

    beste = sorted(punkte.items(), key=lambda x: -x[1])[:k]
    return [_hit(meta[i], round(p * RRF_K / 2, 3),
                 "+".join(sorted(wie.get(i, ())))) for i, p in beste]


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


def guide_block(query: str) -> str:
    """Die besten Treffer für den System-Prompt – ein Vorgeschmack. Wer mehr
    braucht, sucht mit gedaechtnis_suchen selbst. Hat der laufende Chat aufgeräumt,
    kommen zuerst passende Stellen aus seinem ausgelagerten Teil."""
    ref, kontext, ausgelagert = _laufender_chat()
    vorhin = _vorhin_im_chat(query, ref, kontext) if ref and ausgelagert else []
    hits = [h for h in search(query, GUIDE_K + VORHIN_K) if not (ref and h.get("ref") == ref)][:GUIDE_K]
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
