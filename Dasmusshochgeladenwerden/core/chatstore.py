"""
chatstore.py - Speichert Chats nummeriert und schreibt automatisch eine memory.md.

Jeder Chat liegt unter chats/ als:
  001.json  -> voller Verlauf (zum Fortsetzen mit /resume 1)
  001.md    -> lesbare Kurz-Zusammenfassung (das "Gedächtnis")

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

CHATS_DIR = _ROOT / "chats"                                    # Projekt-Wurzel


def _ensure() -> None:
    CHATS_DIR.mkdir(exist_ok=True)


def _json_path(chat_id: int) -> Path:
    return CHATS_DIR / f"{chat_id:03d}.json"


def _md_path(chat_id: int) -> Path:
    return CHATS_DIR / f"{chat_id:03d}.md"


def next_id() -> int:
    """Die nächste freie Chat-Nummer."""
    _ensure()
    nums = []
    for p in CHATS_DIR.glob("*.json"):
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


def save(chat_id: int, messages: list, prompts: list[str], model: str) -> None:
    """Speichert Verlauf (.json) und schreibt das Gedächtnis (.md)."""
    if not prompts:
        return  # leere Chats nicht anlegen
    _ensure()
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
    _ensure()
    out = []
    for p in sorted(CHATS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id"),
                "title": d.get("title", ""),
                "model": d.get("model", ""),
                "updated": d.get("updated", ""),
                "turns": len(d.get("prompts", [])),
            })
        except Exception:
            continue
    out.sort(key=lambda d: d["updated"], reverse=True)
    return out


def chat_args() -> dict[str, str]:
    """Für die Autovervollständigung: '1' -> 'Titel (gemma-12b)'."""
    return {str(c["id"]): f"{c['title']}  ({c['model']})" for c in list_chats()}
