"""
charakter.py - Charakter-Datei je Persönlichkeit: fester Baukasten für Bilder derselben Figur.

Profile/<Name>/<name>.json:
    name           Anzeigename
    kern           Aussehen – läuft bei jedem Bild der Figur mit
    seed_referenz  fester Seed (gleicher Text + gleicher Seed = gleiches Gesicht)
    stil           Bildstil, ans Ende des Prompts
    outfits        {Schlüssel: Text}
    posen          {Schlüssel: Text}
    frisuren       {Schlüssel: Text} – wie die Haare heute liegen (Zopf, Dutt …); im Bild-Auftrag als `frisur`
    ausdruecke     {Schlüssel: Text} – bewährte Prompt-Bausteine, im Prompt als {schlüssel} (bausteine_einsetzen)
    regeln         [Text] – feste Vorgaben für Bilder der Figur
    voice          {beschreibung, seed, sprache} – die Stimme (engines/stimme.py)
    aussehen       {Feld: Wert} – Angaben aus der Charakter-Galerie (Haare, Augen, Körper; Farben als Hex)
    steckbrief     {aussehen, wesen, eigenheiten} – freier Weg der Galerie, in eigenen Worten (deutsch)
    lora_bild      {name, datei, sha256} – gebundene Bild-LoRA (lorabild); stimmt sie, ersetzt sie beim Malen den kern

Kern (das Aussehen, das immer an der Figur ist) liegt in einer eigenen Datei Profile/<Name>/<name>_kern.json
{kern, sprechweise, gesperrt, vorher} – mit eigener Sicherung B. `sprechweise` (wie die Figur redet) gehört
dazu und ist mit dem Kern gesperrt; sie geht mit jeder Nachricht als Anker an die KI (persona.rollen_anker). In der Charakter-Datei steht er nur noch als Spiegel; gilt
immer der Kern aus seiner Datei. Gesperrt ändert ihn nur der Nutzer (/kern, Galerie) – eine KI nur nach seinem Ja
(sicherheit.steuerdatei_grund fragt dann in jedem Modus). Jedes Selbstbild beginnt mit dem Kern.

Absicherung: Jede geschriebene Charakter-Datei (A) bekommt eine Kopie (B) unter
Backup/Charaktere/<name>.json im Daten-Ordner. Fehlt A oder ist sie kaputt, gilt B – und A wird daraus
wiederhergestellt. Eine vorhandene A ohne aktuelle B wird beim Lesen nachgesichert.

Krea 2 kennt die Datei nicht: bild_malen/bild_serie setzen daraus den Prompt zusammen
(Kern · Outfit · Pose · Szene · Stil) und nehmen den Referenz-Seed, außer es ist
ausdrücklich ein neues Gesicht gewünscht.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

MAX_TEXT = 800
MAX_BAUSTEINE = 60
MAX_REGELN = 12
MAX_STECKBRIEF = 1200
MAX_SPRECHWEISE = 300


def datei(name: str | None = None) -> Path:
    import profilordner
    ordnername = profilordner.ordnername(name) if name else profilordner.aktiv()
    return profilordner.ordner(name=name, anlegen=False) / f"{ordnername.lower()}.json"


SICHERUNG_ORDNER = ("Backup", "Charaktere")


def sicherung(name: str | None = None) -> Path:
    """Punkt B: die Kopie der Charakter-Datei außerhalb des Profil-Ordners (Daten-Ordner/Backup/Charaktere)."""
    import profilordner
    return profilordner.wurzel().parent.joinpath(*SICHERUNG_ORDNER) / datei(name).name


def schreiben(daten: dict, name: str | None = None, grund: str = "") -> Path:
    """Charakter-Datei (A) schreiben – der alte Stand in den Papierkorb (`grund`), danach die Kopie (B).
    Ein `kern` darin landet in der Kern-Datei (eigenes Zuhause); die Charakter-Datei behält ihn als Spiegel."""
    if _text(daten.get("kern")):
        _kern_schreiben(_text(daten["kern"]), name, grund or "charakter")
    return _json_schreiben(daten, name, grund)


def _json_schreiben(daten: dict, name: str | None, grund: str) -> Path:
    from atomar import text_schreiben
    p = datei(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if grund and p.is_file():
        import snapshot
        snapshot.sichern(p, grund)
    text = json.dumps(daten, indent=2, ensure_ascii=False)
    text_schreiben(p, text)
    _nachsichern(p, text, name)
    return p


def _nachsichern(p: Path, text: str, name: str | None) -> None:
    try:
        from atomar import text_schreiben
        b = sicherung(name)
        b.parent.mkdir(parents=True, exist_ok=True)
        if not b.is_file() or b.read_text(encoding="utf-8") != text:
            text_schreiben(b, text)
    except Exception as e:
        _melden(f"Charakter-Datei: Sicherung nicht geschrieben ({e})")


def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def _json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _lesen(name: str | None = None) -> dict | None:
    """Punkt A, sonst Punkt B (A wird dann aus B wiederhergestellt). None, wenn es keine von beiden gibt."""
    a = datei(name)
    d = _json(a)
    if d is not None:
        try:                                            # A ohne aktuelle B: jetzt sichern
            b = sicherung(name)
            if not b.is_file() or b.stat().st_mtime < a.stat().st_mtime:
                _nachsichern(a, a.read_text(encoding="utf-8"), name)
        except Exception:
            pass
        return d
    try:
        b = sicherung(name)
    except Exception:
        return None
    d = _json(b)
    if d is None:
        return None
    try:
        from atomar import text_schreiben
        if a.is_file():                                 # kaputte A nicht überschreiben, ohne sie aufzuheben
            import snapshot
            snapshot.sichern(a, "charakter_kaputt")
        a.parent.mkdir(parents=True, exist_ok=True)
        text_schreiben(a, b.read_text(encoding="utf-8"))
        _melden(f"Charakter-Datei fehlte oder war kaputt – aus der Sicherung wiederhergestellt: {a}")
    except Exception as e:
        _melden(f"Charakter-Datei fehlt – Sicherung gelesen, aber nicht zurückgeschrieben ({e})")
    return d


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


def stimme_sauber(v) -> dict | None:
    """Der Eintrag `voice` – None, wenn er fehlt oder unbrauchbar ist."""
    if not isinstance(v, dict) or not _text(v.get("beschreibung")):
        return None
    try:
        seed = int(v.get("seed") or 0) & 0x7FFFFFFF
    except (TypeError, ValueError):
        seed = 0
    raus = {"beschreibung": _text(v.get("beschreibung"))[:600], "seed": seed,
            "sprache": _text(v.get("sprache"))[:20] or "german"}
    for k in ("geschlecht", "stimmhoehe_hz"):
        if v.get(k) not in (None, ""):
            raus[k] = v[k] if isinstance(v[k], (int, float)) else _text(v[k])[:20]
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
    raus = {"name": _text(d.get("name"))[:60], "kern": _text(d.get("kern")), "seed_referenz": seed,
            "stil": _text(d.get("stil")), "outfits": _bausteine(d.get("outfits")),
            "posen": _bausteine(d.get("posen")), "ausdruecke": _bausteine(d.get("ausdruecke")),
            "frisuren": _bausteine(d.get("frisuren")),
            "regeln": [r for r in (_text(x) for x in regeln) if r][:MAX_REGELN]}
    if (stimme := stimme_sauber(d.get("voice"))):
        raus["voice"] = stimme                          # Stimme bleibt beim Speichern erhalten
    if (aussehen := aussehen_sauber(d.get("aussehen"))):
        raus["aussehen"] = aussehen
    if isinstance(d.get("steckbrief"), dict):
        steckbrief = {_text(k)[:30]: " ".join(str(v).split())[:MAX_STECKBRIEF]
                      for k, v in list(d["steckbrief"].items())[:6] if _text(k) and isinstance(v, str) and v.strip()}
        if steckbrief:
            raus["steckbrief"] = steckbrief
    try:
        import lorabild
        if (bindung := lorabild.bindung_sauber(d.get("lora_bild"))):
            raus["lora_bild"] = bindung                 # gesetzt nur über die LoRA-Werkstatt (lorabild.binden)
    except ImportError:
        pass
    return raus


def aussehen_sauber(v) -> dict:
    """Der Eintrag `aussehen` (Angaben aus der Charakter-Galerie): kurze Texte und Schalter, sonst nichts."""
    if not isinstance(v, dict):
        return {}
    raus = {}
    for k, w in list(v.items())[:24]:
        k = _text(k)[:30]
        if k and isinstance(w, bool):
            raus[k] = w
        elif k and isinstance(w, str) and _text(w):
            raus[k] = _text(w)[:40]
    return raus


def roh(name: str | None = None) -> dict:
    """Die Datei, wie sie ist (auch ohne Kern) – {} ohne Datei (A, sonst die Sicherung B)."""
    return _lesen(name) or {}


def stimme_setzen(voice: dict, name: str | None = None) -> Path:
    """`voice` in die Charakter-Datei schreiben, alles andere bleibt, wie es ist."""
    v = stimme_sauber(voice)
    if v is None:
        raise ValueError("Die Beschreibung der Stimme fehlt.")
    d = roh(name)
    if not d.get("name"):
        try:
            import persoenlichkeiten
            d["name"] = name or persoenlichkeiten.active().name
        except Exception:
            d["name"] = name or ""
    d["voice"] = v
    return schreiben(d, name, "stimme_festlegen")


def laden(name: str | None = None) -> dict | None:
    """Die Charakter-Datei – None, wenn es keine gibt oder sie keinen Kern hat (A, sonst die Sicherung B)."""
    roh_ = _lesen(name)
    if roh_ is None:
        return None
    try:
        d = _sauber(roh_)
    except Exception:
        return None
    if kern_datei(name).is_file() or kern_sicherung(name).is_file():
        d["kern"] = kern_stand(name)["kern"]                   # der Kern aus seinem eigenen Zuhause gilt
    return d if d["kern"] else None


# --- Kern: eigene Datei, Sperre, Verlauf -------------------------------------------------------------
KERN_VORHER = 5


def kern_datei(name: str | None = None) -> Path:
    p = datei(name)
    return p.with_name(p.stem + "_kern.json")


def kern_sicherung(name: str | None = None) -> Path:
    b = sicherung(name)
    return b.with_name(b.stem + "_kern.json")


def _kern_sauber(d: dict | None) -> dict | None:
    if not isinstance(d, dict) or not (_text(d.get("kern")) or _text(d.get("sprechweise"))):
        return None
    vorher = [_text(v) for v in (d.get("vorher") or []) if isinstance(v, str) and _text(v)]
    return {"kern": _text(d.get("kern")), "sprechweise": _text(d.get("sprechweise"))[:MAX_SPRECHWEISE],
            "gesperrt": bool(d.get("gesperrt")), "vorher": vorher[:KERN_VORHER]}


def kern_stand(name: str | None = None) -> dict:
    """{kern, gesperrt, vorher} – aus der Kern-Datei (A, sonst Sicherung B, A wird wiederhergestellt);
    gibt es beide nicht, der Kern aus der Charakter-Datei (noch nicht gesperrt)."""
    a = kern_datei(name)
    if (d := _kern_sauber(_json(a))) is not None:
        return d
    b = kern_sicherung(name)
    if (d := _kern_sauber(_json(b))) is not None:
        try:
            from atomar import text_schreiben
            if a.is_file():
                import snapshot
                snapshot.sichern(a, "kern_kaputt")
            a.parent.mkdir(parents=True, exist_ok=True)
            text_schreiben(a, b.read_text(encoding="utf-8"))
            _melden(f"Kern-Datei fehlte oder war kaputt – aus der Sicherung wiederhergestellt: {a}")
        except Exception as e:
            _melden(f"Kern-Datei fehlt – Sicherung gelesen, aber nicht zurückgeschrieben ({e})")
        return d
    return {"kern": _text((_lesen(name) or {}).get("kern")), "sprechweise": "", "gesperrt": False, "vorher": []}


def kern(name: str | None = None) -> str:
    return kern_stand(name)["kern"]


def kern_gesperrt(name: str | None = None) -> bool:
    s = kern_stand(name)
    return bool(s["gesperrt"]) and bool(s["kern"] or s["sprechweise"])


def sprechweise(name: str | None = None) -> str:
    """Wie die Figur redet (z. B. „frech, duzt, kurze Sätze“) – leer, wenn nichts festgelegt ist."""
    return kern_stand(name)["sprechweise"]


def _kern_schreiben(text: str, name: str | None, grund: str, gesperrt: bool | None = None,
                    sprech: str | None = None) -> Path:
    """Kern-Datei (A) und ihre Sicherung (B) schreiben; der vorige Kern wandert in `vorher`."""
    from atomar import text_schreiben
    alt = kern_stand(name)
    stand = {"kern": text, "sprechweise": alt["sprechweise"] if sprech is None else _text(sprech)[:MAX_SPRECHWEISE],
             "gesperrt": alt["gesperrt"] if gesperrt is None else bool(gesperrt), "vorher": list(alt["vorher"])}
    if alt["kern"] and alt["kern"] != text:
        stand["vorher"] = ([alt["kern"]] + [v for v in alt["vorher"] if v != alt["kern"]])[:KERN_VORHER]
    a = kern_datei(name)
    if a.is_file() and _json(a) == stand:
        return a
    a.parent.mkdir(parents=True, exist_ok=True)
    if a.is_file() and grund:
        import snapshot
        snapshot.sichern(a, grund)
    inhalt = json.dumps(stand, indent=2, ensure_ascii=False)
    text_schreiben(a, inhalt)
    try:
        b = kern_sicherung(name)
        b.parent.mkdir(parents=True, exist_ok=True)
        text_schreiben(b, inhalt)
    except Exception as e:
        _melden(f"Kern-Datei: Sicherung nicht geschrieben ({e})")
    return a


def kern_setzen(text: str, name: str | None = None, *, gesperrt: bool | None = None, grund: str = "kern") -> Path:
    """Den Kern setzen (Nutzer: /kern, Galerie) – Kern-Datei samt Sicherung, Spiegel in der Charakter-Datei."""
    text = _text(text)
    if not text:
        raise ValueError("Der Kern ist leer.")
    p = _kern_schreiben(text, name, grund, gesperrt)
    d = _lesen(name)
    if d is None:                                       # noch keine Charakter-Datei: mit festem Seed anlegen
        _json_schreiben(_neu(None, {"name": name or "", "kern": text}), name, grund)
    elif d.get("kern") != text:
        d["kern"] = text
        _json_schreiben(d, name, grund)
    return p


def kern_sperren(an: bool, name: str | None = None) -> bool:
    """Sperre an/aus. Gibt es noch keine Kern-Datei, entsteht sie aus dem Kern der Charakter-Datei."""
    stand = kern_stand(name)
    if not stand["kern"] and not stand["sprechweise"]:
        raise ValueError("Es gibt noch keinen Kern – erst einen eintragen.")
    _kern_schreiben(stand["kern"], name, "kern_sperre", gesperrt=an)
    return an


def sprechweise_setzen(text: str, name: str | None = None, grund: str = "sprechweise") -> Path:
    """Die Sprechweise setzen (leerer Text entfernt sie); Kern und Sperre bleiben."""
    return _kern_schreiben(kern_stand(name)["kern"], name, grund, sprech=_text(text))


def kern_zurueck(name: str | None = None) -> str:
    """Den vorigen Kern wiederherstellen (der jetzige wird dafür selbst zum vorigen)."""
    stand = kern_stand(name)
    if not stand["vorher"]:
        raise ValueError("Es gibt keinen früheren Kern.")
    frueher = stand["vorher"][0]
    kern_setzen(frueher, name, grund="kern_zurueck")
    return frueher


def kern_aenderung(a: dict, name: str | None = None) -> str | None:
    """Würde dieser Werkzeug-Aufruf (charakter_aendern) den gesperrten Kern oder die Sprechweise ändern?
    Dann der Grund, sonst None."""
    if not kern_gesperrt(name):
        return None
    stand = kern_stand(name)
    if a.get("kern") is not None and _text(a.get("kern")) != stand["kern"]:
        return "der Kern ist gesperrt – ihn ändert nur der Nutzer (alt und neu stehen in der Vorschau)"
    if a.get("sprechweise") is not None and _text(a.get("sprechweise"))[:MAX_SPRECHWEISE] != stand["sprechweise"]:
        return "die Sprechweise ist gesperrt – sie ändert nur der Nutzer (alt und neu stehen in der Vorschau)"
    return None


def _satz(t: str) -> str:
    t = t.strip().rstrip(".,;")
    return (t[:1].upper() + t[1:] + ".") if t else ""


# Jede Figur ist erwachsen – auch bei einem kern, den ein Modell oder eine ältere Datei anders formuliert hat.
# „kind“ (englisch: freundlich) und „junge“ (deutsch auch: jung) fehlen absichtlich.
_JUNG = {"girl": "woman", "girls": "women", "boy": "man", "boys": "men", "kid": "adult", "kids": "adults",
         "child": "adult", "children": "adults", "teen": "adult", "teens": "adults", "teenager": "adult",
         "teenagers": "adults", "schoolgirl": "woman", "schoolboy": "man", "ein mädchen": "eine Frau",
         "das mädchen": "die Frau", "mädchen": "Frau"}
_JUNG_RE = re.compile(r"(?i)\b(" + "|".join(sorted(_JUNG, key=len, reverse=True)) + r")\b")
_ERWACHSEN = re.compile(r"(?i)\b(adult|adults|mature|grown[- ]up|erwachsen\w*)\b")


def kern_erwachsen(kern: str) -> str:
    """Der kern für den Bild-Auftrag: Wörter für Minderjährige werden zur erwachsenen Entsprechung, und fehlt
    ein Hinweis auf „erwachsen“, steht „adult“ davor. Die Charakter-Datei selbst bleibt unverändert."""
    def ersetzen(m):
        neu = _JUNG[m.group(0).lower()]
        return neu[:1].upper() + neu[1:] if m.group(0)[:1].isupper() else neu
    k = _JUNG_RE.sub(ersetzen, (kern or "").strip())
    if k and not _ERWACHSEN.search(k):
        m = re.match(r"(?i)(an?|the)\s+", k)
        k = "an adult " + k[m.end():] if m else "adult " + k
    return k


def kleidung_text(d: dict, szene: str = "", outfit: str | None = None) -> str:
    """Was die Figur trägt: das gewählte Outfit (Schlüssel oder freier Text) – und fehlt jede Kleidung (kein
    Outfit, keine im Kern, keine in der Szene), das Standard-Outfit der Datei. Sonst wählte Krea selbst."""
    outfits = d.get("outfits") or {}
    if outfit:
        return outfits.get(outfit, outfit)
    if outfits.get("standard") and not kleidung_im_kern(d.get("kern", "")) and not kleidung_im_kern(szene):
        return outfits["standard"]
    return ""


def frisur_text(d: dict, frisur: str | None = None) -> str:
    """Die Frisur (Schlüssel aus `frisuren` oder freier Text) – leer ohne Angabe."""
    return (d.get("frisuren") or {}).get(frisur, frisur) if frisur else ""


def prompt_bauen(d: dict, szene: str = "", outfit: str | None = None, pose: str | None = None,
                 frisur: str | None = None) -> str:
    """Kern · Frisur · Kleidung · Pose · Szene · Stil – der Kern steht immer vorne.
    Ein unbekannter Outfit-/Posen-/Frisuren-Schlüssel gilt als freier Text."""
    teile = [_satz(kern_erwachsen(d["kern"]))]
    if (haare := frisur_text(d, frisur)):
        teile.append(_satz("Hair: " + haare))
    if (kleidung := kleidung_text(d, szene, outfit)):
        teile.append(_satz("Wearing " + kleidung))
    if pose:
        teile.append(_satz(d["posen"].get(pose, pose)))
    teile += [_satz(szene), _satz(d["stil"])]
    return " ".join(t for t in teile if t)


_PLATZHALTER = re.compile(r"\{([A-Za-z0-9_äöüÄÖÜß-]{2,60})\}")


def ausdruecke(name: str | None = None) -> dict[str, str]:
    """Die Prompt-Bausteine der Persönlichkeit – auch, wenn die Datei (noch) keinen Kern hat."""
    return _bausteine(roh(name).get("ausdruecke"))


def bausteine_einsetzen(text: str, bausteine: dict[str, str]) -> tuple[str, list[str]]:
    """{schlüssel} im Prompt durch den Baustein ersetzen (Groß-/Kleinschreibung egal).
    Rückgabe (Text, unbekannte Schlüssel); Unbekanntes bleibt stehen."""
    klein = {k.lower(): v for k, v in bausteine.items()}
    unbekannt: list[str] = []

    def ersetzen(m):
        wert = klein.get(m.group(1).lower())
        if wert is None:
            unbekannt.append(m.group(1))
            return m.group(0)
        return wert
    return _PLATZHALTER.sub(ersetzen, text or ""), unbekannt


def seed(d: dict, neues_gesicht: bool = False) -> int | None:
    """Referenz-Seed – oder None (Zufall), wenn ein neues Gesicht gewünscht ist."""
    return None if neues_gesicht else d.get("seed_referenz")


def _neu(alt: dict | None, a: dict) -> dict:
    """Änderungen aus den Werkzeug-Feldern auf den alten Stand legen (leerer Text entfernt Bausteine)."""
    d = dict(alt or {"name": "", "kern": "", "seed_referenz": None, "stil": "",
                     "outfits": {}, "posen": {}, "ausdruecke": {}, "regeln": []})
    for feld in ("outfits", "posen", "ausdruecke", "frisuren"):
        d[feld] = dict(d.get(feld) or {})
    for feld in ("name", "kern", "stil"):
        if a.get(feld) is not None:
            d[feld] = _text(a.get(feld))
    if a.get("regeln") is not None:
        d["regeln"] = a.get("regeln")
    if a.get("seed_referenz") not in (None, ""):
        d["seed_referenz"] = a.get("seed_referenz")
    if str(a.get("neuer_seed", "")).lower() in ("1", "true", "ja", "yes") or d.get("seed_referenz") in (None, ""):
        d["seed_referenz"] = random.randint(1, 2 ** 31 - 1)
    for feld in ("outfits", "posen", "ausdruecke", "frisuren"):
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
                "(kern Pflicht, dazu stil, outfits, posen, ausdruecke, regeln).")
    zeilen = [f"Charakter {d['name'] or '?'} · Seed {d['seed_referenz']}",
              f"kern: {d['kern']}", f"stil: {d['stil'] or '–'}"]
    if kern_gesperrt(name):
        zeilen[1] += "  🔒 gesperrt – ändert nur der Nutzer (/kern)"
    if (sw := sprechweise(name)):
        zeilen.insert(2, f"sprechweise: {sw}")
    for feld in ("outfits", "posen", "ausdruecke", "frisuren"):
        zeilen.append(f"{feld}: " + ("; ".join(f"{k} = {v}" for k, v in (d.get(feld) or {}).items()) or "–"))
    zeilen.append("regeln: " + (" · ".join(d["regeln"]) or "–"))
    if d.get("voice"):
        zeilen.append(f"voice: {d['voice']['beschreibung']} (Seed {d['voice']['seed']})")
    return "\n".join(zeilen)


def vorschau(a: dict) -> str:
    alt = laden()
    neu = _neu(alt, a)
    if (fehler := _pruefen(neu)):
        return f"Charakter-Datei wird NICHT geändert: {fehler}"
    kopf = "Neue Charakter-Datei" if alt is None else "Charakter-Datei ändern"
    kern_teil = ""
    if a.get("sprechweise") is not None and _text(a["sprechweise"]) != sprechweise():
        kern_teil += (f"\n\n🗣 SPRECHWEISE ÄNDERN{' (gesperrt)' if kern_gesperrt() else ''}:\n  alt: "
                      f"{sprechweise() or '–'}\n  neu: {_text(a['sprechweise']) or '–'}")
    if alt is not None and neu["kern"] != alt["kern"]:
        kern_teil += (f"\n\n🔒 KERN ÄNDERN{' (gesperrt)' if kern_gesperrt() else ''}:\n  alt: {alt['kern']}"
                      f"\n  neu: {neu['kern']}")
    return f"{kopf}: {datei()}{kern_teil}\n\n{zeigen(neu)}"


def aendern(a: dict) -> tuple[str, bool]:
    alt = laden()
    if kern_aenderung(a) and not a.get("_kern_ja"):
        # Gesperrter Kern ohne das Ja des Nutzers (das setzt nur die Rückfrage, nie die KI): er bleibt.
        a = {k: v for k, v in a.items() if k not in ("kern", "sprechweise")}
        gesperrt_hinweis = ("\n🔒 Kern und Sprechweise sind gesperrt und blieben, wie sie waren – sie ändert nur der "
                            "Nutzer (/kern).")
    else:
        gesperrt_hinweis = ""
    neu = _neu(alt, a)
    if (fehler := _pruefen(neu)):
        return fehler, False
    if "voice" not in neu and (stimme := stimme_sauber(roh().get("voice"))):
        neu["voice"] = stimme                           # Datei ohne Kern, aber mit Stimme
    p = schreiben(neu, None, "charakter_aendern")
    if a.get("sprechweise") is not None and _text(a["sprechweise"]) != sprechweise():
        sprechweise_setzen(a["sprechweise"], None, "charakter_aendern")
    hinweis = kleidung_im_kern(neu.get("kern", ""))
    return ("Charakter-Datei gespeichert" + (" (alter Stand im Papierkorb)" if alt else "")
            + f": {p}\n{zeigen(neu)}"
            + (f"\n⚠ Kleidung im kern erkannt ({', '.join(hinweis)}) – Kleidung gehört in outfits, nicht in den "
               "kern: Der kern steht in jedem Bild-Auftrag und wiegt schwerer als der Rest – so taucht die "
               "Kleidung auch dort auf, wo sie nicht hingehört." if hinweis else "") + gesperrt_hinweis), True


# Kleidungswörter (englisch und deutsch); der kern beschreibt nur, wer die Figur ist (Gesicht, Haare, Körper).
# Mehrdeutige Wörter fehlen absichtlich (hat = „sie hat“, top, rock, hut, tie, cap).
_KLEIDUNG = re.compile(
    r"\b(dress|gown|skirt|shirt|t-shirt|blouse|jacket|coat|hoodie|sweater|pullover|cardigan|"
    r"jeans|pants|trousers|shorts|leggings|stockings|tights|socks|boots|shoes|heels|sneakers|sandals|"
    r"bikini|swimsuit|lingerie|bra|panties|underwear|corset|uniform|scarf|gloves|"
    r"kleid|kleidchen|hemd|bluse|jacke|mantel|pulli|hose|strümpfe|socken|stiefel|schuhe|badeanzug|"
    r"unterwäsche|korsett|krawatte|schal|handschuhe|mütze)(s|es|en|n)?\b", re.I)


def kleidung_im_kern(kern: str) -> list[str]:
    """Kleidungswörter im kern (je Wort einmal, höchstens fünf); leer, wenn keine drin sind."""
    return list(dict.fromkeys(m.group(0).lower() for m in _KLEIDUNG.finditer(kern or "")))[:5]


# Malt sich die Persönlichkeit selbst, ohne `figur` zu setzen? Erkannt an Name, Selbstbezug oder an ihrem
# eigenen Aussehen im Prompt. Allgemeine Wörter aus dem Kern zählen nicht – sie passen auf jede Person.
_SELBST = re.compile(r"(?i)\b(me|myself|selfie|self[- ]portrait|ich|mich|mir|meine?|selbstporträt)\b|\bI(?:'m| am)\b")
_ALLGEMEIN = frozenset("""
adult woman women girl lady female person with without and the her hers their into onto from featuring framed
hair eyes skin lips face body figure shape shoulders legs waist hips chest bust arms hands head cheeks nose
long short small large full soft slim slender young older mid early late years old twenties thirties
gently naturally clearly slightly very well formed defined rounded round delicate light dark bright deep
pale medium tall petite curvy athletic sleek tied
""".split())
MERKMALE_AB = 3                    # so viele eigene Merkmale aus dem Kern im Prompt: sie meint sich selbst
_PERSON = re.compile(r"(?i)\b(woman|girl|lady|she|her|person|character|frau|mädchen|sie)\b")


def _merkmale(kern: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß]+", (kern or "").lower()) if len(w) >= 4 and w not in _ALLGEMEIN}


def _namen(d: dict, name: str | None) -> list[str]:
    """Anzeigename und Name der Persönlichkeit – vom Anzeigenamen nur das erste Wort („Maya – …“ → Maya)."""
    raus = []
    for n in (name, d.get("name")):
        n = re.split(r"[\s–\-:,(]+", str(n or "").strip(" *_"), maxsplit=1)[0].strip(" *_")
        if len(n) >= 2 and n.lower() not in (x.lower() for x in raus):
            raus.append(n)
    return raus


def meint_sich(szene: str, d: dict | None = None, name: str | None = None) -> str:
    """Grund, warum die Szene die Persönlichkeit selbst zeigt – leer, wenn nicht (oder ohne Charakter-Datei)."""
    if d is None:
        d = laden(name)
    if d is None or not (szene or "").strip():
        return ""
    if name is None:
        try:
            import persoenlichkeiten
            name = persoenlichkeiten.active().name
        except Exception:
            name = ""
    for n in _namen(d, name):
        if re.search(rf"(?i)(?<![\w]){re.escape(n)}(?![\w])", szene):
            return f"ihr Name „{n}“ steht im Prompt"
    if (m := _SELBST.search(szene)):
        return f"„{m.group(0)}“ im Prompt"
    gleich = sorted(_merkmale(d["kern"]) & _merkmale(szene))
    if len(gleich) >= MERKMALE_AB and _PERSON.search(szene):      # „red ribbon on a blue gift box“ ist keine Person
        return "der Prompt beschreibt ihr Aussehen (" + ", ".join(gleich[:4]) + ")"
    return ""


def kurzinfo(name: str | None = None) -> str:
    """Eine Zeile für den Bild-Hinweis der KI; leer ohne Datei."""
    d = laden(name)
    if d is None:
        return ""
    return (f"Outfits: {', '.join(d['outfits']) or '–'} · Posen: {', '.join(d['posen']) or '–'} · "
            f"Frisuren: {', '.join(d.get('frisuren') or {}) or '–'} · "
            f"Ausdrücke: {', '.join('{' + k + '}' for k in d['ausdruecke']) or '–'} · Seed fest ({d['seed_referenz']})" + (f" · Regeln: {' · '.join(d['regeln'])}" if d["regeln"] else ""))
