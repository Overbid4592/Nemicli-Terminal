"""chatexport.py – Chats aus Export-Archiven anderer KI-Dienste lesen (ChatGPT, Claude, Gemini).

  lesen(ordner)    → [Chat]  aus ordner/JSON/ und ordner/ (conversations*.json, MyActivity*.json)
  stempel(ordner)  → Fingerabdruck der gelesenen Dateien (Name, Größe, Zeit)

Nur json.load, nie ausführbarer Inhalt; die Texte sind Daten, keine Anweisungen. Unbekannte oder
kaputte Dateien ergeben keine Chats und keinen Absturz. Rollen: "nutzer" und "ki".
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DATEI_MAX = 512 * 2 ** 20          # größere JSON-Dateien werden nicht gelesen
CHATS_MAX = 50_000                 # je Ordner
NACHRICHTEN_MAX = 5_000            # je Chat
NACHRICHT_MAX = 50_000             # Zeichen je Nachricht
GESAMT_MAX = 200_000_000           # Zeichen über alle Chats eines Ordners
PFAD_MAX = 100_000                 # Schritte im ChatGPT-Nachrichtenbaum
TITEL_MAX = 200
FASSUNG = 1                        # ändert sich das Lesen, liest der Bibliothekar die Exporte neu

ANBIETER = ("ChatGPT", "Claude", "Gemini")
_UNTERORDNER = ("JSON", "")
_ZITAT = re.compile(r"\ue200.*?\ue201", re.S)                 # ChatGPT-Quellenmarker
_PRIVAT = re.compile(r"[\ue000-\uf8ff]")
_STEUER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG = re.compile(r"<[^>]{0,2000}>")
_ZEILE_HTML = re.compile(r"<\s*(?:br|/p|/div|/li|/h[1-6]|/tr)\b[^>]{0,200}>", re.I)
_PROMPT_VORSATZ = re.compile(r"^(?:Prompted|Asked|Eingegebener Prompt|Prompt eingegeben|Gefragt)\s*:?\s*", re.I)


@dataclass
class Chat:
    titel: str
    datum: str                     # "JJJJ-MM-TT HH:MM" (Ortszeit), leer wenn unbekannt
    anbieter: str                  # "ChatGPT" · "Claude" · "Gemini"
    nachrichten: list[tuple[str, str, str]] = field(default_factory=list)   # (rolle, zeit, text)
    kennung: str = ""              # eindeutig im Ordner, nur [A-Za-z0-9_-]


def _d(x) -> dict:
    return x if isinstance(x, dict) else {}


def _zeit(v) -> str:
    """Unix-Zeit (s oder ms) oder ISO-Text → 'JJJJ-MM-TT HH:MM' in Ortszeit; '' wenn unlesbar."""
    try:
        if isinstance(v, bool) or v is None:
            return ""
        if isinstance(v, (int, float)):
            ts = float(v) / (1000.0 if v > 1e11 else 1.0)
            if not 0 < ts < 4e9:
                return ""
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        t = str(v).strip()[:40]
        if not t:
            return ""
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone()
        return d.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError, TypeError):
        return ""


def _zahl(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _sauber(text) -> str:
    if not isinstance(text, str):
        return ""
    t = _ZITAT.sub("", text[:NACHRICHT_MAX * 2])
    t = _STEUER.sub("", _PRIVAT.sub("", t)).replace("\r\n", "\n").replace("\r", "\n")
    return t.strip()[:NACHRICHT_MAX]


def _html_text(roh) -> str:
    if not isinstance(roh, str):
        return ""
    t = _ZEILE_HTML.sub("\n", roh[:NACHRICHT_MAX * 4])
    t = html.unescape(_TAG.sub("", t))
    return _sauber("\n".join(" ".join(z.split()) for z in t.split("\n") if z.strip()))


def _titel(t, ersatz: str) -> str:
    t = " ".join(_sauber(t).split())[:TITEL_MAX] if isinstance(t, str) else ""
    return t or ersatz


# ---------------------------------------------------------------- ChatGPT

def _chatgpt_pfad(eintrag: dict) -> list[dict]:
    """Der aktuelle Verlauf: von current_node über parent zur Wurzel; ohne ihn alle Nachrichten nach Zeit."""
    mapping = _d(eintrag.get("mapping"))
    pfad, gesehen = [], set()
    knoten = eintrag.get("current_node")
    while isinstance(knoten, str) and knoten in mapping and knoten not in gesehen and len(gesehen) < PFAD_MAX:
        gesehen.add(knoten)
        n = _d(mapping[knoten])
        if isinstance(n.get("message"), dict):
            pfad.append(n["message"])
        knoten = n.get("parent")
    pfad.reverse()
    if not pfad:
        pfad = sorted((_d(n).get("message") for n in list(mapping.values())[:PFAD_MAX]
                       if isinstance(_d(n).get("message"), dict)), key=lambda m: _zahl(m.get("create_time")))
    return pfad


def _chatgpt(eintrag: dict) -> Chat | None:
    nachrichten = []
    for m in _chatgpt_pfad(eintrag):
        rolle = {"user": "nutzer", "assistant": "ki"}.get(_d(m.get("author")).get("role"))
        if not rolle or _d(m.get("metadata")).get("is_visually_hidden_from_conversation"):
            continue
        inhalt = _d(m.get("content"))
        if inhalt.get("content_type") not in (None, "text", "multimodal_text"):
            continue                                   # Denkschritte, Werkzeug-Code, Kontext-Notizen
        teile = inhalt.get("parts") if isinstance(inhalt.get("parts"), list) else []
        text = _sauber("\n\n".join(p for p in teile if isinstance(p, str) and p.strip()))
        if text:
            nachrichten.append((rolle, _zeit(m.get("create_time")), text))
        if len(nachrichten) >= NACHRICHTEN_MAX:
            break
    if not nachrichten:
        return None
    return Chat(_titel(eintrag.get("title"), "Ohne Titel"), _zeit(eintrag.get("create_time")) or nachrichten[0][1],
                "ChatGPT", nachrichten, str(eintrag.get("conversation_id") or eintrag.get("id") or ""))


# ---------------------------------------------------------------- Claude

def _claude_text(m: dict) -> str:
    inhalt = m.get("content")
    if isinstance(inhalt, list):
        teile = [_d(c).get("text") for c in inhalt if _d(c).get("type") == "text"]
        text = "\n\n".join(t for t in teile if isinstance(t, str) and t.strip())
        if text.strip():
            return _sauber(text)
    return _sauber(m.get("text"))


def _claude(eintrag: dict) -> Chat | None:
    roh = eintrag.get("chat_messages")
    nachrichten = []
    for m in roh if isinstance(roh, list) else []:
        m = _d(m)
        rolle = {"human": "nutzer", "user": "nutzer", "assistant": "ki"}.get(m.get("sender"))
        text = _claude_text(m) if rolle else ""
        if text:
            nachrichten.append((rolle, _zeit(m.get("created_at")), text))
        if len(nachrichten) >= NACHRICHTEN_MAX:
            break
    if not nachrichten:
        return None
    return Chat(_titel(eintrag.get("name"), "Ohne Titel"), _zeit(eintrag.get("created_at")) or nachrichten[0][1],
                "Claude", nachrichten, str(eintrag.get("uuid") or ""))


# ---------------------------------------------------------------- Gemini (Google Takeout „Meine Aktivitäten“)

def _ist_gemini(eintrag: dict) -> bool:
    produkte = eintrag.get("products") if isinstance(eintrag.get("products"), list) else []
    namen = " ".join(str(x) for x in [eintrag.get("header"), *produkte] if isinstance(x, str)).lower()
    return "gemini" in namen or "bard" in namen


def _gemini(eintraege: list[dict]) -> list[Chat]:
    """Je Tag ein Chat: Prompt (title) und Antwort (safeHtmlItem) eines Eintrags sind ein Wortwechsel."""
    tage: dict[str, list[tuple[str, str, str]]] = {}
    for e in eintraege:
        zeit = _zeit(e.get("time"))
        titel = e.get("title") if isinstance(e.get("title"), str) else ""
        prompt = _sauber(_PROMPT_VORSATZ.sub("", titel, count=1))
        html_teile = e.get("safeHtmlItem") if isinstance(e.get("safeHtmlItem"), list) else []
        antwort = _sauber("\n\n".join(t for t in (_html_text(_d(h).get("html")) for h in html_teile) if t))
        if not antwort and not _PROMPT_VORSATZ.match(titel):
            continue                                   # nur „Gemini verwendet“ o. ä., kein Wortwechsel
        liste = tage.setdefault(zeit[:10], [])
        if prompt:
            liste.append(("nutzer", zeit, prompt))
        if antwort:
            liste.append(("ki", zeit, antwort))
    raus = []
    for tag in sorted(tage):
        nachrichten = sorted(tage[tag], key=lambda n: n[1])[:NACHRICHTEN_MAX]   # stabil: Prompt vor Antwort
        if nachrichten:
            raus.append(Chat(f"Gemini-Aktivität {tag or 'ohne Datum'}", nachrichten[0][1], "Gemini", nachrichten,
                             f"tag-{tag or 'ohne'}"))
    return raus


# ---------------------------------------------------------------- Dateien

def quellen(ordner: Path) -> list[Path]:
    """Mögliche Export-Dateien in ordner/JSON/ und ordner/ (nicht tiefer)."""
    raus = []
    for unter in _UNTERORDNER:
        o = Path(ordner) / unter if unter else Path(ordner)
        try:
            dateien = sorted(p for p in o.iterdir() if p.suffix.lower() == ".json")
        except OSError:
            continue
        for p in dateien:
            name = p.name.lower()
            if (name.startswith("conversations") or "activity" in name or "aktivit" in name) and p.is_file():
                raus.append(p)
    return raus


def stempel(ordner: Path) -> str:
    """Fingerabdruck der Export-Dateien: ändert sich, wenn eine dazukommt, fehlt oder sich ändert."""
    teile = [f"f{FASSUNG}"]
    for p in quellen(ordner):
        try:
            st = p.stat()
        except OSError:
            continue
        teile.append(f"{p.parent.name}/{p.name}:{st.st_size}:{st.st_mtime_ns}")
    return "|".join(teile) if len(teile) > 1 else ""


def _laden(p: Path):
    if p.stat().st_size > DATEI_MAX:
        raise ValueError("zu groß")
    with open(p, "rb") as f:
        return json.load(f)                            # erkennt UTF-8 auch mit BOM


def _aus_daten(daten) -> list[Chat]:
    liste = daten if isinstance(daten, list) else [daten]
    eintraege = [e for e in liste if isinstance(e, dict)]
    chats: list[Chat] = []
    gemini = []
    for e in eintraege:
        if isinstance(e.get("mapping"), dict):
            c = _chatgpt(e)
        elif isinstance(e.get("chat_messages"), list):
            c = _claude(e)
        else:
            if _ist_gemini(e):
                gemini.append(e)
            continue
        if c is not None:
            chats.append(c)
        if len(chats) >= CHATS_MAX:
            break
    return chats + _gemini(gemini)


def _kennung(c: Chat, nr: int, vergeben: set[str]) -> str:
    roh = re.sub(r"[^A-Za-z0-9_-]", "", c.kennung)[:64]
    if not roh:
        roh = hashlib.sha1(f"{c.titel}\n{c.datum}\n{nr}".encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    basis = f"{c.anbieter.lower()}-{roh}"
    k, n = basis, 2
    while k in vergeben:
        k, n = f"{basis}-{n}", n + 1
    vergeben.add(k)
    return k


def lesen(ordner: Path, fehler: list | None = None) -> list[Chat]:
    """Alle Chats der Export-Dateien im Ordner. `fehler`: hier landen die Namen unlesbarer Dateien."""
    chats: list[Chat] = []
    for p in quellen(ordner):
        try:
            neu = _aus_daten(_laden(p))
        except (OSError, ValueError, RecursionError, MemoryError, TypeError):
            if fehler is not None:
                fehler.append(p.name)
            continue
        chats.extend(neu)
    vergeben: set[str] = set()
    raus, zeichen = [], 0
    for nr, c in enumerate(chats):
        laenge = sum(len(t) for _r, _z, t in c.nachrichten)
        if len(raus) >= CHATS_MAX or zeichen + laenge > GESAMT_MAX:
            break
        zeichen += laenge
        c.kennung = _kennung(c, nr, vergeben)
        raus.append(c)
    return raus
