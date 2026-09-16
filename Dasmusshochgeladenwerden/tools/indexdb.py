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
    return con


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
    vecs = None
    try:
        import memory
        vecs = memory._embed_texts([p for _, p in new])
    except Exception:
        vecs = None
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for i, (h, p) in enumerate(new):
        vec = json.dumps(vecs[i]) if vecs and i < len(vecs) else None
        rows.append((h, kind, ref, p, vec, now))
    con.executemany(
        "INSERT OR REPLACE INTO chunks(hash, kind, ref, text, vec, updated) VALUES (?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    _invalidate()
    return len(rows)


def drop_ref(ref: str) -> None:
    con = _connect()
    con.execute("DELETE FROM chunks WHERE ref=?", (ref,))
    con.commit()
    con.close()
    _invalidate()


def ingest_chat(chat_id: int, title: str, prompts: list[str], messages: list) -> int:
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
    return _upsert("chat", f"chat:{chat_id}", texts)


def ingest_file(kind: str, path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    return _upsert(kind, f"{kind}:{path.name}", [f"{path.name}\n{text}"])


def ingest_note(note_id: int, text: str, kind: str = "fakt") -> int:
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
            n += ingest_file("chatmd", md)
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


def after_turn(chat_id: int | None = None) -> int:
    """Bibliothekar nach der Runde: Dateien lesen → embedden → memory.db.

    chat_id: der gerade gespeicherte Chat (None = nur Notizen/Skills)."""
    n = 0
    if chat_id:
        n += ingest_chat_file(int(chat_id))
    n += ingest_all_notes()
    n += ingest_learned()
    n += backfill()
    return n


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
            ref = f"chat:{meta['id']}"
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

_CACHE: dict = {"stamp": None, "meta": None, "mat": None, "vecs": None, "idx": None}


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
    stamp = _db_stamp()
    if _CACHE["meta"] is not None and stamp is not None and _CACHE["stamp"] == stamp:
        return _CACHE
    meta: list[tuple] = []
    vecs: list[list[float]] = []
    idx: list[int] = []
    try:
        con = _connect()
        for kind, ref, text, vec_s in con.execute(
                "SELECT kind, ref, text, vec FROM chunks"):
            i = len(meta)
            meta.append((kind, ref, text))
            if not vec_s:
                continue
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
    _CACHE.update({"stamp": stamp, "meta": meta, "mat": mat, "vecs": vecs, "idx": idx})
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


def _hit(meta_row: tuple, score: float) -> dict:
    kind, ref, text = meta_row
    return {"kind": kind, "ref": ref, "text": text, "score": score}


def search(query: str, k: int = SEARCH_K) -> list[dict]:
    """Semantische Suche über memory.db. Nur die Frage wird eingebettet;
    die Brocken liegen schon als Vektoren in der DB.

    Wichtig: Diese Funktion hängt im Chat-Pfad (persona.build_system_prompt).
    Sie lädt deshalb NIE das Embedding-Modell nach. Ist der Encoder kalt,
    wärmt sie ihn im Hintergrund vor und beantwortet diese eine Frage per
    Stichwort – der Chat wartet keine Sekunde."""
    if not (query or "").strip():
        return []
    ix = _index()
    meta = ix["meta"] or []
    if not meta:
        return []

    qe = None
    try:
        import memory
        if memory.encoder_loaded():
            qe = memory._embed_texts([query], query=True)
        else:
            memory.warm_encoder()          # ab der nächsten Frage semantisch
    except Exception:
        qe = None

    if qe:
        scored = _scores(ix, qe[0])
        scored.sort(key=lambda x: -x[0])
        hits = [_hit(meta[i], s) for s, i in scored[:k] if s >= MIN_SCORE]
        if hits:
            return hits
        if scored and scored[0][0] >= MIN_SCORE * 0.6:
            return [_hit(meta[scored[0][1]], scored[0][0])]
        return []

    q = query.lower()
    out = []
    for kind, ref, text in meta:
        if q in (text or "").lower():
            out.append({"kind": kind, "ref": ref, "text": text, "score": 1.0})
            if len(out) >= k:
                break
    return out


def guide_block(query: str) -> str:
    """Treffer aus memory.db für den System-Prompt – die KI findet sich zurecht."""
    hits = search(query)
    if not hits:
        return ""
    import memory                      # lazy, wie überall hier (Import-Kreis vermeiden)
    lines = [
        "",
        "# 🧭 Was aus dem Gedächtnis zur aktuellen Frage passt",
        "Nutze das, um dich zurechtzufinden. Das sind Erinnerungen, kein Raten. "
        + memory.DATEN_HINWEIS,
        "",
    ]
    for h in hits:
        snippet = memory.entschaerfen(h.get("text") or "")
        if len(snippet) > 280:
            snippet = snippet[:280] + "…"
        lines.append(f"- [{h.get('kind')}/{h.get('ref')}] {snippet}")
    return "\n".join(lines)
