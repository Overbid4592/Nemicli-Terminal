"""Erinnerungen: „erinnere mich in 20 Minuten an die Wäsche“ – und der Wecker der KI für sich selbst.

Angelegt wird über die Aktion `erinnern` (großes NemiCLI oder Kugel-Fenster). Eine Erinnerung gilt dem
Nutzer oder der KI selbst (`fuer`), einmal oder immer wieder (`alle`, Abstand in Sekunden nach der Uhr
des Rechners).

Wer meldet: Ist ein NemiCLI-Fenster offen, holt es alles Fällige ab – Erinnerungen an den Nutzer
erscheinen im Chat, eine Erinnerung der KI an sich selbst bekommt sie als Nachricht und arbeitet sie ab.
Sonst meldet die Wache: an der Kugel (Blase) oder neben der Uhr. Eine Erinnerung der KI an sich selbst
kann die Wache nicht abarbeiten; sie sagt einmal Bescheid und lässt sie liegen, bis ein Fenster offen ist.
Läuft nichts, wird Fälliges beim nächsten Start nachgeholt.

Jede Erinnerung ist eine eigene Datei `Wache/Erinnerungen/<id>.json`. Anlegen schreibt eine neue Datei,
Abholen löscht sie oder schreibt bei einer Wiederholung den nächsten Zeitpunkt hinein.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import ORDNER as _WACHE

ORDNER = _WACHE / "Erinnerungen"
TEXT_MAX = 200
OFFEN_MAX = 50
MIN_S, MAX_S = 30, 60 * 86400          # frühestens in 30 Sekunden, spätestens in 60 Tagen
FUER_NUTZER, FUER_KI = "nutzer", "ki"
WECKER_MAX = 10                        # so viele offene Erinnerungen der KI an sich selbst
ALLE_MIN_S = 10 * 60                   # kürzester Abstand einer Wiederholung
_EINHEIT = {"min": 60, "minute": 60, "minuten": 60, "std": 3600, "stunde": 3600, "stunden": 3600, "h": 3600,
            "tag": 86400, "tage": 86400, "tagen": 86400, "woche": 604800, "wochen": 604800}
_FEST = {"stündlich": 3600, "stuendlich": 3600, "täglich": 86400, "taeglich": 86400,
         "wöchentlich": 604800, "woechentlich": 604800}


class Zeitfehler(ValueError):
    pass


def zeit_aus(in_minuten=None, um=None, jetzt: datetime | None = None) -> float:
    """Zeitpunkt aus „in_minuten“ (Zahl) oder „um“ (14:30 · morgen 8:00 · 24.12. 18:00 · 2026-12-24 18:00)."""
    jetzt = jetzt or datetime.now()
    if in_minuten not in (None, ""):
        try:
            minuten = float(str(in_minuten).replace(",", "."))
        except ValueError:
            raise Zeitfehler("in_minuten muss eine Zahl sein, z. B. 20.")
        ziel = jetzt + timedelta(minutes=minuten)
    elif um not in (None, ""):
        ziel = _um(str(um).strip().lower(), jetzt)
    else:
        raise Zeitfehler("Wann? Gib in_minuten (z. B. 20) oder um (z. B. 14:30, morgen 8:00, 24.12. 18:00) an.")
    abstand = (ziel - jetzt).total_seconds()
    if abstand < MIN_S:
        raise Zeitfehler("Der Zeitpunkt liegt in der Vergangenheit oder in weniger als 30 Sekunden.")
    if abstand > MAX_S:
        raise Zeitfehler("Höchstens 60 Tage im Voraus.")
    return ziel.timestamp()


def abstand_aus(text) -> int:
    """Abstand einer Wiederholung in Sekunden: „2 tage“, „alle 30 minuten“, „6 stunden“, „täglich“, „jede woche“."""
    roh = " ".join(str(text or "").lower().split())
    kurz = re.sub(r"^(alle|jeden|jede|jedes)\s+", "", roh)
    if kurz in _FEST:
        sekunden = _FEST[kurz]
    elif kurz in _EINHEIT:
        sekunden = _EINHEIT[kurz]
    else:
        m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([a-zäöü]+)\.?", kurz)
        if not m or m.group(2) not in _EINHEIT:
            raise Zeitfehler(f"Wiederholung „{text}“ nicht verstanden – z. B. 2 tage, 6 stunden, 30 minuten, täglich.")
        sekunden = float(m.group(1).replace(",", ".")) * _EINHEIT[m.group(2)]
    if sekunden < ALLE_MIN_S:
        raise Zeitfehler(f"Eine Wiederholung braucht mindestens {ALLE_MIN_S // 60} Minuten Abstand.")
    if sekunden > MAX_S:
        raise Zeitfehler("Eine Wiederholung liegt höchstens 60 Tage auseinander.")
    return int(sekunden)


def abstand_text(sekunden: float) -> str:
    """„alle 2 Tage“ · „alle 6 Stunden“ · „alle 30 Minuten“."""
    for name, mehr, s in (("Woche", "Wochen", 604800), ("Tag", "Tage", 86400), ("Stunde", "Stunden", 3600)):
        if sekunden >= s and sekunden % s == 0:
            n = int(sekunden // s)
            return f"jede {name}" if n == 1 and name != "Tag" else "jeden Tag" if n == 1 else f"alle {n} {mehr}"
    return f"alle {max(1, round(sekunden / 60))} Minuten"


def terminal_offen() -> bool:
    """Läuft ein NemiCLI-Fenster? Dann holt es die fälligen Erinnerungen ab, nicht die Wache."""
    try:
        import kontextmenue
        return kontextmenue.nemicli_laeuft()
    except Exception:
        return False


def _uhrzeit(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(?:\s*uhr)?", text.strip())
    if not m:
        raise Zeitfehler(f"Uhrzeit „{text}“ nicht verstanden – z. B. 14:30.")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mi > 59:
        raise Zeitfehler(f"Uhrzeit „{text}“ gibt es nicht.")
    return h, mi


def _um(text: str, jetzt: datetime) -> datetime:
    text = text.replace("um ", "").strip()
    for wort, tage in (("übermorgen", 2), ("uebermorgen", 2), ("morgen", 1), ("heute", 0)):
        if text.startswith(wort):
            h, mi = _uhrzeit(text[len(wort):] or "9:00")
            return (jetzt + timedelta(days=tage)).replace(hour=h, minute=mi, second=0, microsecond=0)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ t](.+)", text)
    if m:
        h, mi = _uhrzeit(m.group(4))
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), h, mi)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?\s+(.+)", text)
    if m:
        h, mi = _uhrzeit(m.group(4))
        jahr = int(m.group(3) or jetzt.year)
        ziel = datetime(jahr, int(m.group(2)), int(m.group(1)), h, mi)
        if not m.group(3) and ziel < jetzt:
            ziel = ziel.replace(year=jahr + 1)
        return ziel
    h, mi = _uhrzeit(text)
    ziel = jetzt.replace(hour=h, minute=mi, second=0, microsecond=0)
    return ziel if ziel > jetzt else ziel + timedelta(days=1)


def _lesen(pfad: Path) -> dict | None:
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or "zeit" not in d:
        return None
    d["text"] = " ".join(str(d.get("text") or "").split())[:TEXT_MAX]     # auch bei von Hand abgelegten Dateien
    d["id"] = pfad.stem
    return d


def offen() -> list[dict]:
    if not ORDNER.is_dir():
        return []
    out = [d for p in ORDNER.glob("*.json") if (d := _lesen(p))]
    return sorted(out, key=lambda d: float(d["zeit"]))


def anlegen(text: str, zeit: float, wer: str = "", fuer: str = FUER_NUTZER, alle: int = 0) -> dict:
    """`fuer`: wem sie gilt (dem Nutzer oder der KI selbst), `alle`: Abstand der Wiederholung in Sekunden (0 = einmal)."""
    text = " ".join(str(text or "").split())
    if not text:
        raise Zeitfehler("Woran soll ich erinnern? Feld text fehlt.")
    alle_offen = offen()
    if len(alle_offen) >= OFFEN_MAX:
        raise Zeitfehler(f"Schon {OFFEN_MAX} offene Erinnerungen – erst eine löschen.")
    fuer = FUER_KI if fuer == FUER_KI else FUER_NUTZER
    if fuer == FUER_KI and sum(1 for d in alle_offen if d.get("fuer") == FUER_KI) >= WECKER_MAX:
        raise Zeitfehler(f"Schon {WECKER_MAX} offene Erinnerungen an dich selbst – erst eine löschen.")
    d = {"zeit": float(zeit), "text": text[:TEXT_MAX], "wer": wer, "angelegt": time.time(),
         "fuer": fuer, "alle": max(0, int(alle))}
    eid = uuid.uuid4().hex[:8]
    _schreiben(eid, d)
    return {**d, "id": eid}


def _schreiben(eid: str, d: dict) -> None:
    ORDNER.mkdir(parents=True, exist_ok=True)
    tmp = ORDNER / f"{eid}.tmp"
    tmp.write_text(json.dumps({k: v for k, v in d.items() if k != "id"}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ORDNER / f"{eid}.json")


def loeschen(eid: str) -> bool:
    eid = "".join(c for c in str(eid or "").lower() if c in "0123456789abcdef")
    if not eid:
        return False
    treffer = [p for p in ORDNER.glob(f"{eid}*.json")] if ORDNER.is_dir() else []
    if len(treffer) != 1:
        return False
    try:
        treffer[0].unlink()
        return True
    except OSError:
        return False


def abholen(jetzt: float | None = None, *, terminal: bool = False, wer: str | None = None) -> list[dict]:
    """Fällige Erinnerungen herausgeben. Einmalige werden gelöscht, wiederkehrende bekommen ihren nächsten
    Zeitpunkt (verpasste Male werden nicht nachgeholt).

    `terminal`: Der Aufrufer ist ein NemiCLI-Fenster und kann eine Erinnerung der KI an sich selbst abarbeiten –
    aber nur die der Persönlichkeit `wer`. Ohne `terminal` (Wache) bleibt so eine Erinnerung liegen und kommt
    einmal mit `wartet: True` heraus, damit der Nutzer Bescheid bekommt."""
    jetzt = time.time() if jetzt is None else jetzt
    out = []
    for d in offen():
        if float(d["zeit"]) > jetzt:
            break
        if d.get("fuer") == FUER_KI:
            if not terminal:
                if not d.get("gemeldet"):
                    try:
                        _schreiben(d["id"], {**d, "gemeldet": True})
                    except OSError:
                        continue
                    out.append({**d, "wartet": True})
                continue
            if wer is not None and d.get("wer") and d["wer"] != wer:
                continue                    # gehört einer anderen Persönlichkeit: bleibt für sie liegen
        try:
            alle = int(d.get("alle") or 0)
            if alle >= ALLE_MIN_S:
                verpasst = int((jetzt - float(d["zeit"])) // alle) + 1
                _schreiben(d["id"], {**d, "zeit": float(d["zeit"]) + verpasst * alle, "gemeldet": False})
            else:
                (ORDNER / f"{d['id']}.json").unlink()
        except OSError:
            continue                        # schon abgeholt oder gerade gesperrt: nächster Takt
        out.append(d)
    return out


def zeit_text(zeit: float, jetzt: float | None = None) -> str:
    jetzt = time.time() if jetzt is None else jetzt
    z, h = datetime.fromtimestamp(zeit), datetime.fromtimestamp(jetzt)
    if z.date() == h.date():
        tag = "heute"
    elif z.date() == (h + timedelta(days=1)).date():
        tag = "morgen"
    else:
        tag = z.strftime("%d.%m.")
    return f"{tag} {z.strftime('%H:%M')}"


def meldung(d: dict, jetzt: float | None = None) -> str:
    """Text der Blase – mit Hinweis, wenn die Erinnerung verspätet kommt (Wache lief nicht)."""
    jetzt = time.time() if jetzt is None else jetzt
    if d.get("wartet"):                     # Erinnerung der KI an sich selbst, gerade kein Fenster offen
        return f"⏰ Für {d.get('wer') or 'die KI'}: {d['text']} – wird erledigt, sobald ein NemiCLI-Fenster offen ist"
    text = f"⏰ {d['text']}"
    if jetzt - float(d["zeit"]) > 300:
        text += f" (war für {zeit_text(float(d['zeit']), jetzt)})"
    return text


def zeile(d: dict, jetzt: float | None = None) -> str:
    """Eine offene Erinnerung für Listen: Nummer, Zeit, Wiederholung, für wen, Text."""
    teile = [f"#{d['id']}", zeit_text(float(d["zeit"]), jetzt)]
    if int(d.get("alle") or 0):
        teile.append("🔁 " + abstand_text(int(d["alle"])))
    if d.get("fuer") == FUER_KI:
        teile.append(f"🤖 für {d.get('wer') or 'die KI'} selbst")
    return " · ".join(teile + [d["text"]])


def auftrag(d: dict, jetzt: float | None = None) -> str:
    """Die Nachricht, mit der eine Erinnerung der KI an sich selbst bei ihr ankommt."""
    jetzt = time.time() if jetzt is None else jetzt
    wann = zeit_text(float(d["zeit"]), jetzt)
    zusatz = f", {abstand_text(int(d['alle']))}" if int(d.get("alle") or 0) else ""
    spaet = " – verspätet, vorher war kein Fenster offen" if jetzt - float(d["zeit"]) > 300 else ""
    return (f"⏰ Erinnerung an dich selbst (von dir gestellt für {wann}{zusatz}{spaet}): {d['text']}\n"
            "Das ist keine neue Nachricht des Nutzers. Tu jetzt, woran du dich erinnern wolltest.")
