"""
chatstore.py - Speichert Chats nummeriert und schreibt automatisch eine memory.md.

Jeder Chat liegt bei der Persönlichkeit, mit der er begann (Profile/<Name>/Chats/), als:
  001.json  -> voller Verlauf (zum Fortsetzen mit /resume 1)
  001.md    -> lesbare Kurz-Zusammenfassung (das "Gedächtnis")
Chats aus der Zeit vor den Profilen bleiben im gemeinsamen chats/ und sind für jede
Persönlichkeit sichtbar. Nummern zählen über beide Orte weiter – eine Nummer ist eindeutig.

Wird nach jeder Runde aktualisiert.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

CHATS_DIR = _ROOT / "chats"                                    # gemeinsam (vor den Profilen)
_STANDARD = CHATS_DIR


def ordner() -> Path:
    """Chats der aktiven Persönlichkeit – außer CHATS_DIR wurde umgelenkt (dann nur dort)."""
    if CHATS_DIR != _STANDARD:
        return CHATS_DIR
    try:
        import profilordner
        return profilordner.ordner("Chats", anlegen=False)       # save() legt an
    except Exception:
        return CHATS_DIR


def _orte() -> list[Path]:
    eigen = ordner()
    return [eigen] if eigen == CHATS_DIR else [eigen, CHATS_DIR]


def _ensure() -> None:
    ordner().mkdir(parents=True, exist_ok=True)


def _ordner_fuer(chat_id: int) -> Path:
    """Wo Chat `chat_id` liegt – ein neuer kommt zur aktiven Persönlichkeit."""
    for ort in _orte():
        if (ort / f"{chat_id:03d}.json").exists():
            return ort
    return ordner()


def _json_path(chat_id: int) -> Path:
    return _ordner_fuer(chat_id) / f"{chat_id:03d}.json"


def _md_path(chat_id: int) -> Path:
    return _ordner_fuer(chat_id) / f"{chat_id:03d}.md"


def index_ref(chat_id: int) -> str:
    """Name im Suchindex: chat:<Profil>:<Nr> bei Profil-Chats, chat:<Nr> bei gemeinsamen."""
    ort = _ordner_fuer(chat_id)
    return f"chat:{chat_id}" if ort == CHATS_DIR else f"chat:{ort.parent.name}:{chat_id}"


def dateien() -> list[Path]:
    """Alle Chat-Dateien, die die aktive Persönlichkeit sieht (ihre und die gemeinsamen)."""
    out = []
    for ort in _orte():
        out += sorted(ort.glob("*.json")) if ort.exists() else []
    return out


def next_id() -> int:
    """Die nächste freie Chat-Nummer (über alle Profile und den gemeinsamen Ordner)."""
    nums = []
    orte = [CHATS_DIR] + (sorted(ordner().parent.parent.glob("*/Chats")) if ordner() != CHATS_DIR else [])
    for ort in orte:
        for p in ort.glob("*.json"):
            try:
                nums.append(int(p.stem))
            except ValueError:
                pass
    return (max(nums) + 1) if nums else 1


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
    """Speichert Verlauf (.json) und schreibt das Gedächtnis (.md). Hat der Kontext
    aufgeräumt, bleibt der ausgelagerte Anfang in der Datei (und damit im Suchindex)."""
    if not prompts:
        return  # leere Chats nicht anlegen
    _ensure()
    alt = load(chat_id)
    if alt and isinstance(alt.get("messages"), list):
        messages = zusammenfuehren(alt["messages"], list(messages))
    title = _title_from(prompts)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = {
        "id": chat_id,
        "title": title,
        "model": model,
        "updated": now,
        "prompts": prompts,
        "messages": messages,
    }
    _json_path(chat_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_memory(chat_id, title, model, now, prompts)


def md_path(chat_id: int) -> Path:
    return _md_path(chat_id)


def _write_memory(chat_id: int, title: str, model: str, now: str, prompts: list[str]) -> None:
    lines = [
        f"# Chat {chat_id:03d} — {title}",
        "",
        f"- **Modell:** {model}",
        f"- **Zuletzt aktualisiert:** {now}",
        f"- **Runden:** {len(prompts)}",
        f"- **Fortsetzen mit:** `/resume {chat_id}`",
        "",
        "## Worum es ging",
        "",
    ]
    for p in prompts:
        eintrag = p.strip().replace("\n", " ")
        eintrag = (eintrag[:120] + "…") if len(eintrag) > 120 else eintrag
        lines.append(f"- 👤 {eintrag}")
    _md_path(chat_id).write_text("\n".join(lines) + "\n", encoding="utf-8")


def load(chat_id: int) -> dict | None:
    p = _json_path(chat_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_chats() -> list[dict]:
    """Alle gespeicherten Chats (neueste zuerst), als Meta-Dicts."""
    out = []
    for p in dateien():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id"),
                "title": d.get("title", ""),
                "model": d.get("model", ""),
                "updated": d.get("updated", ""),
                "turns": len(d.get("prompts", [])),
                "profil": "" if p.parent == CHATS_DIR else p.parent.parent.name,
            })
        except Exception:
            continue
    out.sort(key=lambda d: d["updated"], reverse=True)
    return out


def chat_args() -> dict[str, str]:
    """Für die Autovervollständigung: '1' -> 'Titel (gemma-12b)'."""
    return {str(c["id"]): f"{c['title']}  ({c['model']})" for c in list_chats()}
