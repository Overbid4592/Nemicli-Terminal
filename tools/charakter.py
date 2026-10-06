"""
charakter.py - Charakter-Datei je Persönlichkeit: fester Baukasten für Bilder derselben Figur.

Profile/<Name>/<name>.json:
    name           Anzeigename
    kern           Aussehen – läuft bei jedem Bild der Figur mit
    seed_referenz  fester Seed (gleicher Text + gleicher Seed = gleiches Gesicht)
    stil           Bildstil, ans Ende des Prompts
    outfits        {Schlüssel: Text}
    posen          {Schlüssel: Text}
    regeln         [Text] – feste Vorgaben für Bilder der Figur

Krea 2 kennt die Datei nicht: bild_malen/bild_serie setzen daraus den Prompt zusammen
(Kern · Outfit · Pose · Szene · Stil) und nehmen den Referenz-Seed, außer es ist
ausdrücklich ein neues Gesicht gewünscht.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

MAX_TEXT = 800
MAX_BAUSTEINE = 60
MAX_REGELN = 12


def datei(name: str | None = None) -> Path:
    import profilordner
    ordnername = profilordner.ordnername(name) if name else profilordner.aktiv()
    return profilordner.ordner(name=name, anlegen=False) / f"{ordnername.lower()}.json"


def _text(wert) -> str:
    return " ".join(str(wert or "").split())[:MAX_TEXT]


def _bausteine(wert) -> dict[str, str]:
    if not isinstance(wert, dict):
        return {}
    raus = {}
    for k, v in list(wert.items())[:MAX_BAUSTEINE]:
        k, v = _text(k)[:60], _text(v)
        if k and v:
            raus[k] = v
    return raus


def _sauber(d: dict) -> dict:
    seed = d.get("seed_referenz")
    try:
        seed = int(seed) & 0x7FFFFFFF if seed not in (None, "") else None
    except (TypeError, ValueError):
        seed = None
    regeln = d.get("regeln") or []
    if isinstance(regeln, str):
        regeln = [regeln]
    return {"name": _text(d.get("name"))[:60], "kern": _text(d.get("kern")), "seed_referenz": seed,
            "stil": _text(d.get("stil")), "outfits": _bausteine(d.get("outfits")),
            "posen": _bausteine(d.get("posen")),
            "regeln": [r for r in (_text(x) for x in regeln) if r][:MAX_REGELN]}


def laden(name: str | None = None) -> dict | None:
    """Die Charakter-Datei – None, wenn es keine gibt oder sie keinen Kern hat."""
    try:
        d = _sauber(json.loads(datei(name).read_text(encoding="utf-8")))
    except Exception:
        return None
    return d if d["kern"] else None


def _satz(t: str) -> str:
    t = t.strip().rstrip(".,;")
    return (t[:1].upper() + t[1:] + ".") if t else ""


def prompt_bauen(d: dict, szene: str = "", outfit: str | None = None, pose: str | None = None) -> str:
    """Kern · Outfit · Pose · Szene · Stil. Ein unbekannter Outfit-/Posen-Schlüssel gilt als freier Text."""
    teile = [_satz(d["kern"])]
    if outfit:
        teile.append(_satz("Wearing " + d["outfits"].get(outfit, outfit)))
    if pose:
        teile.append(_satz(d["posen"].get(pose, pose)))
    teile += [_satz(szene), _satz(d["stil"])]
    return " ".join(t for t in teile if t)


def seed(d: dict, neues_gesicht: bool = False) -> int | None:
    """Referenz-Seed – oder None (Zufall), wenn ein neues Gesicht gewünscht ist."""
    return None if neues_gesicht else d.get("seed_referenz")


def _neu(alt: dict | None, a: dict) -> dict:
    """Änderungen aus den Werkzeug-Feldern auf den alten Stand legen (leerer Text entfernt Bausteine)."""
    d = dict(alt or {"name": "", "kern": "", "seed_referenz": None, "stil": "",
                     "outfits": {}, "posen": {}, "regeln": []})
    d["outfits"], d["posen"] = dict(d.get("outfits") or {}), dict(d.get("posen") or {})
    for feld in ("name", "kern", "stil"):
        if a.get(feld) is not None:
            d[feld] = _text(a.get(feld))
    if a.get("regeln") is not None:
        d["regeln"] = a.get("regeln")
    if a.get("seed_referenz") not in (None, ""):
        d["seed_referenz"] = a.get("seed_referenz")
    if str(a.get("neuer_seed", "")).lower() in ("1", "true", "ja", "yes") or d.get("seed_referenz") in (None, ""):
        d["seed_referenz"] = random.randint(1, 2 ** 31 - 1)
    for feld in ("outfits", "posen"):
        for k, v in (a.get(feld) or {}).items() if isinstance(a.get(feld), dict) else ():
            if _text(v):
                d[feld][_text(k)[:60]] = _text(v)
            else:
                d[feld].pop(_text(k)[:60], None)
    if not d.get("name"):
        try:
            import persoenlichkeiten
            d["name"] = persoenlichkeiten.active().name
        except Exception:
            d["name"] = ""
    return _sauber(d)


def _pruefen(d: dict) -> str | None:
    if not d["kern"]:
        return "Feld 'kern' fehlt: das feste Aussehen der Figur (englisch, ein Satz)."
    return None


def zeigen(d: dict | None, name: str | None = None) -> str:
    if d is None:
        return (f"Noch keine Charakter-Datei ({datei(name)}). Anlegen mit charakter_aendern "
                "(kern Pflicht, dazu stil, outfits, posen, regeln).")
    zeilen = [f"Charakter {d['name'] or '?'} · Seed {d['seed_referenz']}",
              f"kern: {d['kern']}", f"stil: {d['stil'] or '–'}"]
    for feld in ("outfits", "posen"):
        zeilen.append(f"{feld}: " + ("; ".join(f"{k} = {v}" for k, v in d[feld].items()) or "–"))
    zeilen.append("regeln: " + (" · ".join(d["regeln"]) or "–"))
    return "\n".join(zeilen)


def vorschau(a: dict) -> str:
    alt = laden()
    neu = _neu(alt, a)
    if (fehler := _pruefen(neu)):
        return f"Charakter-Datei wird NICHT geändert: {fehler}"
    kopf = "Neue Charakter-Datei" if alt is None else "Charakter-Datei ändern"
    return f"{kopf}: {datei()}\n\n{zeigen(neu)}"


def aendern(a: dict) -> tuple[str, bool]:
    alt = laden()
    neu = _neu(alt, a)
    if (fehler := _pruefen(neu)):
        return fehler, False
    p = datei()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        import snapshot
        snapshot.sichern(p, "charakter_aendern")
    p.write_text(json.dumps(neu, indent=2, ensure_ascii=False), encoding="utf-8")
    return ("Charakter-Datei gespeichert" + (" (alter Stand im Papierkorb)" if alt else "")
            + f": {p}\n{zeigen(neu)}"), True


def kurzinfo(name: str | None = None) -> str:
    """Eine Zeile für den Bild-Hinweis der KI; leer ohne Datei."""
    d = laden(name)
    if d is None:
        return ""
    return (f"Outfits: {', '.join(d['outfits']) or '–'} · Posen: {', '.join(d['posen']) or '–'} · "
            f"Seed fest ({d['seed_referenz']})" + (f" · Regeln: {' · '.join(d['regeln'])}" if d["regeln"] else ""))
