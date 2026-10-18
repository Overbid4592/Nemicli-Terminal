"""Erinnerungen: „erinnere mich in 20 Minuten an die Wäsche“ – die Kugel meldet sich zur Zeit.

Angelegt wird über die Aktion `erinnern` (großes NemiCLI oder Kugel-Fenster), gemeldet im
Wache-Prozess: Ist die Kugel zu sehen, hüpft sie und zeigt eine Blase; sonst kommt ein
Hinweis neben der Uhr. Läuft die Wache gerade nicht, holt sie fällige Erinnerungen beim
nächsten Start nach.

Jede Erinnerung ist eine eigene Datei `Wache/Erinnerungen/<id>.json`. Zwei Prozesse
(NemiCLI legt an, die Wache holt ab) kommen sich so nie beim Schreiben in die Quere:
Anlegen schreibt eine neue Datei, Abholen löscht sie.
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
    d["id"] = pfad.stem
    return d


def offen() -> list[dict]:
    if not ORDNER.is_dir():
        return []
    out = [d for p in ORDNER.glob("*.json") if (d := _lesen(p))]
    return sorted(out, key=lambda d: float(d["zeit"]))


def anlegen(text: str, zeit: float, wer: str = "") -> dict:
    text = " ".join(str(text or "").split())
    if not text:
        raise Zeitfehler("Woran soll ich erinnern? Feld text fehlt.")
    if len(offen()) >= OFFEN_MAX:
        raise Zeitfehler(f"Schon {OFFEN_MAX} offene Erinnerungen – erst eine löschen.")
    d = {"zeit": float(zeit), "text": text[:TEXT_MAX], "wer": wer, "angelegt": time.time()}
    ORDNER.mkdir(parents=True, exist_ok=True)
    eid = uuid.uuid4().hex[:8]
    tmp = ORDNER / f"{eid}.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ORDNER / f"{eid}.json")
    return {**d, "id": eid}


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


def abholen(jetzt: float | None = None) -> list[dict]:
    """Fällige Erinnerungen herausgeben und ihre Dateien löschen (die Kugel meldet sie dann)."""
    jetzt = time.time() if jetzt is None else jetzt
    out = []
    for d in offen():
        if float(d["zeit"]) > jetzt:
            break
        try:
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
    text = f"⏰ {d['text']}"
    if jetzt - float(d["zeit"]) > 300:
        text += f" (war für {zeit_text(float(d['zeit']), jetzt)})"
    return text
