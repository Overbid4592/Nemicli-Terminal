"""
chatstore.py - Chats je Persönlichkeit in einer Datenbank (SQLite).

Jede Persönlichkeit hat ihre Datei Profile/<Name>/Chats/chat_<name>.db; Chats aus der Zeit vor
den Profilen liegen in chats/chat_gemeinsam.db und sind für jede Persönlichkeit sichtbar.
Nummern bleiben über alle Datenbanken eindeutig (Suchindex: chat:<Profil>:<Nr>).

- Eine neue Runde hängt nur ihre Nachrichten an; nichts wird ganz neu geschrieben.
- Bilder (data:-URIs) liegen einmal je Inhalt in der Tabelle `bilder` (Fingerabdruck SHA-256);
  in der Nachricht steht nur der Verweis.
- Alte Einzeldateien (NNN.json + NNN.md) werden beim ersten Öffnen übernommen und nach vor_db/
  verschoben – nicht gelöscht.
- pruefen()/sichern()/reparieren(): Gesundheit der Datenbank und chat_backup_<name>.db daneben.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

CHATS_DIR = _ROOT / "chats"                                    # gemeinsam (vor den Profilen)
_STANDARD = CHATS_DIR
VOR_DB = "vor_db"                                              # übernommene Einzeldateien
BILD_AB = 256                                                  # kürzere data:-URIs bleiben in der Nachricht
_BILD_VERWEIS = "nemibild:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, titel TEXT, modell TEXT, aktualisiert TEXT,
                                  prompts TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS nachrichten (chat INTEGER NOT NULL, nr INTEGER NOT NULL, inhalt TEXT NOT NULL,
                                        PRIMARY KEY (chat, nr)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS bilder (hash TEXT PRIMARY KEY, daten TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS stand (schluessel TEXT PRIMARY KEY, wert TEXT);
"""


def ordner() -> Path:
    """Chats der aktiven Persönlichkeit – außer CHATS_DIR wurde umgelenkt (dann nur dort)."""
    if CHATS_DIR != _STANDARD:
        return CHATS_DIR
    try:
        import profilordner
        return profilordner.ordner("Chats", anlegen=False)       # _verbinden legt an
    except Exception:
        return CHATS_DIR


def _orte() -> list[Path]:
    eigen = ordner()
    return [eigen] if eigen == CHATS_DIR else [eigen, CHATS_DIR]


def _alle_orte() -> list[Path]:
    """Jeder Chat-Ort über alle Persönlichkeiten – für eindeutige Nummern."""
    eigen = ordner()
    return [CHATS_DIR] + (sorted(eigen.parent.parent.glob("*/Chats")) if eigen != CHATS_DIR else [])


def _name(ort: Path) -> str:
    return "gemeinsam" if ort == CHATS_DIR else ort.parent.name.lower()


def db_pfad(ort: Path | None = None) -> Path:
    ort = ort or ordner()
    return ort / f"chat_{_name(ort)}.db"


def backup_pfad(ort: Path | None = None) -> Path:
    ort = ort or ordner()
    return ort / f"chat_backup_{_name(ort)}.db"


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

def _verbinden(ort: Path, anlegen: bool = True) -> sqlite3.Connection | None:
    pfad = db_pfad(ort)
    if not pfad.exists():
        if not anlegen and not _einzeldateien(ort):
            return None
        ort.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(pfad), timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(_SCHEMA)
    _uebernehmen(c, ort)
    return c


def _einzeldateien(ort: Path) -> list[Path]:
    return sorted(p for p in ort.glob("*.json") if p.stem.isdigit()) if ort.is_dir() else []


def _uebernehmen(c: sqlite3.Connection, ort: Path) -> None:
    """Alte NNN.json (+ NNN.md) einmal übernehmen; danach liegen sie in vor_db/."""
    for p in _einzeldateien(ort):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            nr = int(d.get("id") or p.stem)
        except (OSError, ValueError, TypeError):
            continue                                            # unlesbar: bleibt liegen
        if c.execute("SELECT 1 FROM chats WHERE id=?", (nr,)).fetchone() is None:
            _schreiben(c, nr, list(d.get("messages") or []), list(d.get("prompts") or []),
                       str(d.get("model") or ""), str(d.get("updated") or ""), str(d.get("title") or ""))
            c.commit()
        ziel = ort / VOR_DB
        ziel.mkdir(exist_ok=True)
        for q in (p, p.with_suffix(".md")):
            if q.exists():
                try:
                    shutil.move(str(q), str(ziel / q.name))
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Bilder nur einmal
# ---------------------------------------------------------------------------

def _bilder_raus(obj, neu: dict):
    """data:-URIs durch Verweise ersetzen; `neu` sammelt {hash: data-URI}."""
    if isinstance(obj, str):
        if obj.startswith("data:") and len(obj) > BILD_AB:
            h = hashlib.sha256(obj.encode("utf-8")).hexdigest()
            neu[h] = obj
            return _BILD_VERWEIS + h
        return obj
    if isinstance(obj, list):
        return [_bilder_raus(x, neu) for x in obj]
    if isinstance(obj, dict):
        return {k: _bilder_raus(v, neu) for k, v in obj.items()}
    return obj


def _bilder_rein(obj, holen):
    if isinstance(obj, str):
        if obj.startswith(_BILD_VERWEIS):
            return holen(obj[len(_BILD_VERWEIS):]) or obj
        return obj
    if isinstance(obj, list):
        return [_bilder_rein(x, holen) for x in obj]
    if isinstance(obj, dict):
        return {k: _bilder_rein(v, holen) for k, v in obj.items()}
    return obj


def _als_zeilen(messages: list) -> tuple[list[str], dict]:
    neu: dict = {}
    zeilen = [json.dumps(_bilder_raus(m, neu), ensure_ascii=False) for m in messages]
    return zeilen, neu


def _gespeichert(c: sqlite3.Connection, chat_id: int) -> list[str]:
    return [r[0] for r in c.execute("SELECT inhalt FROM nachrichten WHERE chat=? ORDER BY nr", (chat_id,))]


def _schreiben(c: sqlite3.Connection, chat_id: int, messages: list, prompts: list[str], model: str,
               zeit: str, titel: str = "", zeilen: list[str] | None = None, bilder: dict | None = None) -> None:
    """Nur die Nachrichten ab der ersten Abweichung neu – eine neue Runde hängt also nur an.
    `zeilen`/`bilder`: schon umgewandelte Nachrichten (sonst aus `messages`)."""
    if zeilen is None:
        zeilen, bilder = _als_zeilen(messages)
    alt = _gespeichert(c, chat_id)
    gleich = 0
    while gleich < min(len(alt), len(zeilen)) and alt[gleich] == zeilen[gleich]:
        gleich += 1
    c.executemany("INSERT OR IGNORE INTO bilder(hash, daten) VALUES (?, ?)", (bilder or {}).items())
    c.execute("DELETE FROM nachrichten WHERE chat=? AND nr>=?", (chat_id, gleich))
    c.executemany("INSERT INTO nachrichten(chat, nr, inhalt) VALUES (?, ?, ?)",
                  [(chat_id, i, zeilen[i]) for i in range(gleich, len(zeilen))])
    c.execute("INSERT OR REPLACE INTO chats(id, titel, modell, aktualisiert, prompts) VALUES (?, ?, ?, ?, ?)",
              (chat_id, titel or _title_from(prompts), model, zeit, json.dumps(prompts, ensure_ascii=False)))


def _ort_von(chat_id: int) -> Path | None:
    for ort in _orte():
        c = _verbinden(ort, anlegen=False)
        if c is None:
            continue
        try:
            if c.execute("SELECT 1 FROM chats WHERE id=?", (int(chat_id),)).fetchone():
                return ort
        finally:
            c.close()
    return None


def _ordner_fuer(chat_id: int) -> Path:
    """Wo Chat `chat_id` liegt – ein neuer kommt zur aktiven Persönlichkeit."""
    return _ort_von(chat_id) or ordner()


def index_ref(chat_id: int) -> str:
    """Name im Suchindex: chat:<Profil>:<Nr> bei Profil-Chats, chat:<Nr> bei gemeinsamen."""
    ort = _ordner_fuer(chat_id)
    return f"chat:{chat_id}" if ort == CHATS_DIR else f"chat:{ort.parent.name}:{chat_id}"


def dateien() -> list[Path]:
    """Die Chat-Datenbanken, die die aktive Persönlichkeit sieht (ihre und die gemeinsame)."""
    return [db_pfad(o) for o in _orte() if db_pfad(o).exists()]


def next_id() -> int:
    """Die nächste freie Chat-Nummer (über alle Persönlichkeiten und die gemeinsamen Chats)."""
    hoechste = 0
    for ort in _alle_orte():
        c = _verbinden(ort, anlegen=False)
        if c is None:
            continue
        try:
            hoechste = max(hoechste, c.execute("SELECT COALESCE(MAX(id), 0) FROM chats").fetchone()[0])
        finally:
            c.close()
    return hoechste + 1


def _title_from(prompts: list[str]) -> str:
    if not prompts:
        return "(leerer Chat)"
    t = prompts[0].strip().replace("\n", " ")
    return (t[:50] + "…") if len(t) > 50 else t


_AKTUELL: list = [None]        # laufender Chat (persona.chat_gewechselt)
_IM_KONTEXT: list = [[]]       # Nachrichten, die gerade im Kontext des Modells stehen (pricing.kuerzen)


def aktuell_setzen(chat_id) -> None:
    _AKTUELL[0] = chat_id


def aktueller_chat():
    return _AKTUELL[0]


def im_kontext_setzen(messages: list) -> None:
    _IM_KONTEXT[0] = list(messages or [])


def im_kontext() -> list:
    return _IM_KONTEXT[0]


def zusammenfuehren(alt: list, neu: list) -> list:
    """Gespeicherter Verlauf + neuer: Setzt `neu` ein aufgeräumtes Ende von `alt` fort
    (die ersten Nachrichten von `neu` = die letzten von `alt`) oder ist `neu` ein Stück
    daraus (letzte Runde zurückgenommen), bleibt der ausgelagerte Anfang erhalten.
    Sonst (Verlauf geändert, anderer Chat) gilt `neu`."""
    if not alt or not neu:
        return neu
    for i in range(len(alt)):
        if alt[i] != neu[0]:
            continue
        rest = len(alt) - i
        if (rest <= len(neu) and alt[i:] == neu[:rest]) or alt[i:i + len(neu)] == neu:
            return alt[:i] + neu
    return neu


def save(chat_id: int, messages: list, prompts: list[str], model: str) -> None:
    """Speichert den Verlauf. Hat der Kontext aufgeräumt, bleibt der ausgelagerte Anfang in der
    Datenbank (und damit im Suchindex)."""
    if not prompts:
        return  # leere Chats nicht anlegen
    c = _verbinden(_ordner_fuer(chat_id))
    try:
        zeilen, bilder = _als_zeilen(list(messages))
        zeilen = zusammenfuehren(_gespeichert(c, chat_id), zeilen)        # gleiche Nachricht = gleiche Zeile
        _schreiben(c, chat_id, [], prompts, model, datetime.now().strftime("%Y-%m-%d %H:%M"),
                   zeilen=zeilen, bilder=bilder)
        c.commit()
    finally:
        c.close()


def _bild(c: sqlite3.Connection, h: str) -> str | None:
    r = c.execute("SELECT daten FROM bilder WHERE hash=?", (h,)).fetchone()
    return r[0] if r else None


def md_path(chat_id: int) -> Path:
    """Frühere Kurzfassung NNN.md (gibt es nach der Übernahme nicht mehr)."""
    return _ordner_fuer(chat_id) / f"{chat_id:03d}.md"


def load(chat_id: int) -> dict | None:
    ort = _ort_von(chat_id)
    if ort is None:
        return None
    c = _verbinden(ort)
    try:
        kopf = c.execute("SELECT titel, modell, aktualisiert, prompts FROM chats WHERE id=?",
                         (int(chat_id),)).fetchone()
        if kopf is None:
            return None
        nachrichten = [_bilder_rein(json.loads(r[0]), lambda h: _bild(c, h)) for r in
                       c.execute("SELECT inhalt FROM nachrichten WHERE chat=? ORDER BY nr", (int(chat_id),))]
        return {"id": int(chat_id), "title": kopf[0] or "", "model": kopf[1] or "", "updated": kopf[2] or "",
                "prompts": json.loads(kopf[3] or "[]"), "messages": nachrichten}
    except (sqlite3.Error, ValueError):
        return None
    finally:
        c.close()


def list_chats() -> list[dict]:
    """Alle gespeicherten Chats (neueste zuerst), als Meta-Dicts."""
    out = []
    for ort in _orte():
        c = _verbinden(ort, anlegen=False)
        if c is None:
            continue
        try:
            for nr, titel, modell, zeit, prompts in c.execute(
                    "SELECT id, titel, modell, aktualisiert, prompts FROM chats"):
                out.append({"id": nr, "title": titel or "", "model": modell or "", "updated": zeit or "",
                            "turns": len(json.loads(prompts or "[]")),
                            "profil": "" if ort == CHATS_DIR else ort.parent.name})
        except (sqlite3.Error, ValueError):
            continue
        finally:
            c.close()
    out.sort(key=lambda d: d["updated"], reverse=True)
    return out


def letzter() -> dict | None:
    """Das zuletzt benutzte Gespräch der aktiven Persönlichkeit (mit mindestens einer Runde) – damit geht
    es beim Start weiter."""
    eigen = ordner()
    profil = "" if eigen == CHATS_DIR else eigen.parent.name
    return next((c for c in list_chats() if c["profil"] == profil and c["turns"]), None)


# ---------------------------------------------------------------------------
# Gesundheit und Sicherung
# ---------------------------------------------------------------------------

def pruefen(ort: Path | None = None) -> str:
    """'ok' – sonst, was SQLite an der Datenbank beanstandet ('fehlt', wenn es keine gibt)."""
    pfad = db_pfad(ort)
    if not pfad.exists():
        return "fehlt"
    try:
        c = sqlite3.connect(str(pfad), timeout=10)
        try:
            ergebnis = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error as e:
        return f"nicht lesbar: {e}"
    return "ok" if ergebnis == "ok" else str(ergebnis)


def sichern(ort: Path | None = None) -> Path:
    """chat_backup_<name>.db neben der Datenbank – über die Sicherungsfunktion von SQLite, also auch
    während jemand schreibt in sich stimmig."""
    ort = ort or ordner()
    if pruefen(ort) != "ok":
        raise RuntimeError(f"Die Chat-Datenbank ist nicht in Ordnung ({pruefen(ort)}) – keine Sicherung "
                           "über die letzte gute.")
    ziel = backup_pfad(ort)
    tmp = ziel.with_suffix(".tmp")
    quelle = sqlite3.connect(str(db_pfad(ort)), timeout=10)
    try:
        kopie = sqlite3.connect(str(tmp))
        try:
            quelle.backup(kopie)
            kopie.execute("INSERT OR REPLACE INTO stand(schluessel, wert) VALUES ('gesichert', ?)",
                          (str(time.time()),))
            kopie.commit()
        finally:
            kopie.close()
        quelle.execute("INSERT OR REPLACE INTO stand(schluessel, wert) VALUES ('gesichert', ?)",
                       (str(time.time()),))
        quelle.commit()
    finally:
        quelle.close()
    tmp.replace(ziel)
    return ziel


def zuletzt_gesichert(ort: Path | None = None) -> float | None:
    if not db_pfad(ort).exists():
        return None
    try:
        c = sqlite3.connect(str(db_pfad(ort)), timeout=10)
        try:
            r = c.execute("SELECT wert FROM stand WHERE schluessel='gesichert'").fetchone()
        finally:
            c.close()
        return float(r[0]) if r else None
    except (sqlite3.Error, ValueError):
        return None


def reparieren(ort: Path | None = None) -> str | None:
    """Ist die Datenbank beschädigt und die Sicherung gesund: Sicherung zurückholen, die kaputte als
    chat_<name>.kaputt-<Zeit>.db daneben legen. Rückgabe: was passiert ist (None = alles in Ordnung)."""
    ort = ort or ordner()
    stand = pruefen(ort)
    if stand in ("ok", "fehlt"):
        return None
    sicherung = backup_pfad(ort)
    if not sicherung.exists() or pruefen_datei(sicherung) != "ok":
        return f"Chat-Datenbank beschädigt ({stand}) – keine gesunde Sicherung zum Zurückholen."
    kaputt = db_pfad(ort).with_name(f"{db_pfad(ort).stem}.kaputt-{time.strftime('%Y%m%d-%H%M%S')}.db")
    for endung in ("", "-wal", "-shm"):
        p = Path(str(db_pfad(ort)) + endung)
        if p.exists():
            p.replace(Path(str(kaputt) + endung))
    shutil.copy2(sicherung, db_pfad(ort))
    return f"Chat-Datenbank war beschädigt ({stand}) – die Sicherung ist zurückgeholt, die beschädigte liegt als {kaputt.name} daneben."


SICHERN_TAGE = 7               # so lange ohne Sicherung: die KI bietet eine an
AUTO_STUNDEN = 24              # die Kugel sichert im Vollbild, wenn die letzte länger her ist


def beim_start() -> str | None:
    """Gesundheit der Chat-Datenbank der aktiven Persönlichkeit; beschädigt: Sicherung zurückholen.
    Rückgabe: Meldung für den Nutzer oder None."""
    try:
        return reparieren()
    except Exception as e:
        return f"Chat-Datenbank nicht prüfbar: {e}"


def faellig(ort: Path | None = None, stunden: float = SICHERN_TAGE * 24) -> bool:
    """Gibt es Chats, die seit `stunden` (oder noch nie) gesichert wurden?"""
    if not db_pfad(ort).exists():
        return False
    zuletzt = zuletzt_gesichert(ort)
    return zuletzt is None or time.time() - zuletzt > stunden * 3600


def pruefen_datei(pfad: Path) -> str:
    try:
        c = sqlite3.connect(str(pfad), timeout=10)
        try:
            return str(c.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            c.close()
    except sqlite3.Error as e:
        return f"nicht lesbar: {e}"
