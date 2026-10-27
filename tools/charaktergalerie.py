"""charaktergalerie.py – Charakter-Galerie: fertige Persönlichkeiten übernehmen oder eigene erschaffen.

Ablauf im Fenster (ui/charakter_fenster.py, /charaktere): 9 Vorlagen mit Porträt – oder eigene: Geschlecht →
Alter (ab 18) → bis zu 8 Eigenschaften → die KI schreibt Vorschläge, Krea malt je ein Porträt → einen anklicken,
mit ✨ (die KI hilft) verbessern, übernehmen. Übernehmen legt fest an: die Persönlichkeit (Persoenlichkeiten/),
die Charakter-Datei mit Aussehen und festem Seed und das Porträt als Profilbild (Profile/<Name>/).

Porträts der Vorlagen und Vorschläge liegen bis zum Übernehmen in Charaktergalerie/ im Datenordner.
Die Porträts zeigen immer EINE erwachsene Person; das Alter im Bild ist nie unter 22, auch wenn die Figur 18 ist.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import time
from pathlib import Path

GESCHLECHTER = {"w": ("weiblich", "woman"), "m": ("männlich", "man"), "n": ("nichtbinär", "androgynous person")}
ALTER_MIN, ALTER_MAX = 18, 99
BILD_ALTER_MIN = 22                 # das Porträt zeigt nie jünger als das
MAX_EIGENSCHAFTEN = 8
ANZAHL_VORSCHLAEGE = 4
TOENE = ("locker", "sachlich", "foermlich", "verspielt")

KATEGORIEN: dict[str, list[str]] = {
    "Wesen": ["fröhlich", "ruhig", "frech", "schüchtern", "selbstbewusst", "verträumt", "sarkastisch",
              "fürsorglich", "geheimnisvoll", "energiegeladen"],
    "Umgang": ["locker", "sachlich", "förmlich", "verspielt", "direkt", "einfühlsam", "neugierig", "geduldig"],
    "Interessen": ["Technik", "Gaming", "Kunst", "Musik", "Natur", "Bücher", "Sport", "Kochen", "Mode",
                   "Wissenschaft", "Fantasy", "Sterne & Weltall"],
    "Stil & Look": ["elegant", "sportlich", "gothic", "punk", "casual", "futuristisch", "vintage", "natürlich",
                    "Business", "Anime-Stil"],
    "Fantasie": ["Vampir", "Elfe", "Cyborg", "Hexe", "Engel", "Dämon", "Meerwesen", "Ritter", "Astronaut"],
}
_UMGANG_TON = {"locker": "locker", "sachlich": "sachlich", "förmlich": "foermlich", "verspielt": "verspielt"}
STIL_STANDARD = "detailed portrait photo, soft cinematic light, shallow depth of field, high detail"

VORLAGEN: list[dict] = [
    {"key": "lina", "name": "Lina", "geschlecht": "w", "alter": 26, "ton": "locker",
     "kurz": "fröhlich, technikverliebt, immer einen Spruch auf Lager",
     "eigenschaften": ["fröhlich", "locker", "Technik", "casual"],
     "kern": "a cheerful woman with shoulder-length copper hair, freckles, bright green eyes and a warm smile, "
             "wearing a cozy oversized grey hoodie", "seed": 412233},
    {"key": "selene", "name": "Selene", "geschlecht": "w", "alter": 29, "ton": "foermlich",
     "kurz": "geheimnisvolle Vampirin mit Stil und trockenem Humor",
     "eigenschaften": ["geheimnisvoll", "förmlich", "Vampir", "gothic"],
     "kern": "an elegant pale woman with long straight black hair, deep red eyes and dark red lips, wearing a "
             "black velvet high-collar dress with silver jewelry", "seed": 804411},
    {"key": "mira", "name": "Mira", "geschlecht": "w", "alter": 24, "ton": "verspielt",
     "kurz": "verträumte Waldelfe, liebt Natur und Geschichten",
     "eigenschaften": ["verträumt", "verspielt", "Natur", "Elfe"],
     "kern": "a dreamy elf woman with long wavy silver-blond hair, pointed ears, soft violet eyes and a flower "
             "crown, wearing a light green linen dress", "seed": 220517},
    {"key": "nova", "name": "Nova", "geschlecht": "w", "alter": 27, "ton": "sachlich",
     "kurz": "präzise Cyborg-Forscherin aus der Zukunft",
     "eigenschaften": ["selbstbewusst", "sachlich", "Wissenschaft", "Cyborg", "futuristisch"],
     "kern": "a confident cyborg woman with a sleek white bob haircut, ice-blue eyes and fine glowing circuit "
             "lines on her cheek, wearing a futuristic white and silver jacket", "seed": 951207},
    {"key": "hanna", "name": "Hanna", "geschlecht": "w", "alter": 34, "ton": "locker",
     "kurz": "ruhig und fürsorglich, kocht gern und hört gut zu",
     "eigenschaften": ["ruhig", "fürsorglich", "einfühlsam", "Kochen", "natürlich"],
     "kern": "a calm warm-hearted woman with chestnut hair in a loose bun, hazel eyes and a gentle smile, "
             "wearing a soft cream knit sweater", "seed": 330981},
    {"key": "kai", "name": "Kai", "geschlecht": "m", "alter": 28, "ton": "locker",
     "kurz": "energiegeladener Gamer und Sportfan",
     "eigenschaften": ["energiegeladen", "locker", "Gaming", "Sport", "sportlich"],
     "kern": "an energetic man with short messy dark brown hair, brown eyes, light stubble and a big grin, "
             "wearing a black sports jacket over a gaming t-shirt", "seed": 671042},
    {"key": "elias", "name": "Elias", "geschlecht": "m", "alter": 35, "ton": "sachlich",
     "kurz": "ruhiger Bücherwurm, denkt erst und redet dann",
     "eigenschaften": ["ruhig", "sachlich", "geduldig", "Bücher", "Business"],
     "kern": "a thoughtful man with neatly combed blond hair, grey-blue eyes and thin round glasses, wearing a "
             "dark navy blazer over a white shirt", "seed": 158826},
    {"key": "sky", "name": "Sky", "geschlecht": "n", "alter": 25, "ton": "verspielt",
     "kurz": "neugierig, schaut gern in die Sterne",
     "eigenschaften": ["neugierig", "verspielt", "Sterne & Weltall", "futuristisch"],
     "kern": "an androgynous person with short tousled blue-black hair, bright amber eyes and a curious "
             "expression, wearing a deep blue jacket with small star embroidery", "seed": 742390},
    {"key": "robin", "name": "Robin", "geschlecht": "n", "alter": 30, "ton": "verspielt",
     "kurz": "freche Künstlerseele mit Punk-Herz",
     "eigenschaften": ["frech", "direkt", "Kunst", "Musik", "punk"],
     "kern": "an androgynous person with a bright pink undercut, dark eyeliner, several ear piercings and a "
             "confident smirk, wearing a paint-splattered black denim jacket", "seed": 519364},
]
for _v in VORLAGEN:
    _v.setdefault("stil", STIL_STANDARD)


def ordner() -> Path:
    import paths
    return paths.ROOT / "Charaktergalerie"


def vorlagen_bild(key: str) -> Path:
    return ordner() / "Vorlagen" / f"{key}.png"


def vorlage(key: str) -> dict | None:
    return next((dict(v) for v in VORLAGEN if v["key"] == key), None)


# --- Auswahl prüfen ----------------------------------------------------------------------------------------

def auswahl(geschlecht: str, alter, eigenschaften) -> tuple[str, int, list[str]]:
    """Geschlecht, Alter, Eigenschaften prüfen. ValueError mit Klartext, wenn etwas nicht passt."""
    g = str(geschlecht or "").strip().lower()[:1]
    if g not in GESCHLECHTER:
        raise ValueError("Geschlecht: weiblich, männlich oder nichtbinär.")
    try:
        a = int(alter)
    except (TypeError, ValueError):
        raise ValueError("Das Alter muss eine Zahl sein.") from None
    if a < ALTER_MIN:
        raise ValueError(f"Charaktere sind immer erwachsen – mindestens {ALTER_MIN} Jahre.")
    a = min(a, ALTER_MAX)
    alle = {e for liste in KATEGORIEN.values() for e in liste}
    gewaehlt = [e for e in dict.fromkeys(str(x) for x in eigenschaften or []) if e in alle]
    if not gewaehlt:
        raise ValueError("Wähl mindestens eine Eigenschaft.")
    if len(gewaehlt) > MAX_EIGENSCHAFTEN:
        raise ValueError(f"Höchstens {MAX_EIGENSCHAFTEN} Eigenschaften.")
    return g, a, gewaehlt


def ton_aus(eigenschaften: list[str], vorschlag: str = "") -> str:
    for e in eigenschaften:
        if e in _UMGANG_TON:
            return _UMGANG_TON[e]
    return vorschlag if vorschlag in TOENE else "locker"


# --- Porträt ----------------------------------------------------------------------------------------------

def portraet_prompt(geschlecht: str, alter: int, kern: str, stil: str = "") -> str:
    """Englischer Krea-Prompt: genau eine erwachsene Person, Brustbild, Aussehen aus `kern`."""
    wort = GESCHLECHTER.get(geschlecht, GESCHLECHTER["n"])[1]
    bild_alter = max(int(alter), BILD_ALTER_MIN)
    kern = " ".join(str(kern or "").split())[:600].rstrip(".")
    return (f"Portrait of a single adult {wort}, around {bild_alter} years old, alone, upper body, looking at the "
            f"viewer. {kern[:1].upper() + kern[1:]}. {(stil or STIL_STANDARD).strip().rstrip('.')}.")


def neuer_seed() -> int:
    return random.randint(1, 2 ** 31 - 1)


# --- KI: Vorschläge und Verbessern ----------------------------------------------------------------------------

VORSCHLAG_SYSTEM = (
    "Du erfindest Charaktere für eine KI-Persönlichkeit. Antworte NUR mit JSON: einer Liste von Objekten mit "
    "name (ein Vorname, passend zum Charakter), kurz (deutsch, 4–10 Worte, wie die Person ist), wesen (deutsch, "
    "2–4 Sätze: Art zu reden, Eigenheiten, Vorlieben), ton (locker|sachlich|foermlich|verspielt), kern (ENGLISCH, "
    "ein Satz: festes Aussehen ohne Alter und ohne Geschlechtswort am Anfang – Haare, Augen, Gesicht, Kleidung), "
    "stil (ENGLISCH, kurz: Licht und Bildstil). Jeder Vorschlag deutlich anders. Immer erwachsen. Kein Text "
    "außerhalb des JSON.")


def vorschlag_auftrag(geschlecht: str, alter: int, eigenschaften: list[str], anzahl: int = ANZAHL_VORSCHLAEGE) -> str:
    return (f"Erfinde {anzahl} verschiedene Charaktere. Geschlecht: {GESCHLECHTER[geschlecht][0]}. Alter: {alter} "
            f"Jahre. Eigenschaften: {', '.join(eigenschaften)}. Nur die JSON-Liste.")


def _json_liste(text: str):
    t = re.sub(r"(?is)<think>.*?</think>", "", text or "").strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        raise ValueError("Die KI hat keine Liste geschickt.")
    try:
        return json.loads(m.group(0))
    except ValueError as exc:
        raise ValueError(f"Die Antwort der KI war kein gültiges JSON ({exc}).") from None


def _feld(d: dict, k: str, n: int) -> str:
    return " ".join(str(d.get(k) or "").split())[:n]


def vorschlaege_lesen(text: str, geschlecht: str, alter: int, eigenschaften: list[str],
                      anzahl: int = ANZAHL_VORSCHLAEGE) -> list[dict]:
    """KI-Antwort → geprüfte Entwürfe (mit Seed), höchstens `anzahl`."""
    raus = []
    for d in _json_liste(text):
        if not isinstance(d, dict):
            continue
        name = re.sub(r"[^\w .'-]", "", _feld(d, "name", 40)).strip()
        kern = _feld(d, "kern", 500)
        if not name or not kern:
            continue
        raus.append({"name": name, "kurz": _feld(d, "kurz", 120) or ", ".join(eigenschaften[:3]),
                     "wesen": _feld(d, "wesen", 700), "ton": ton_aus(eigenschaften, _feld(d, "ton", 12)),
                     "kern": kern, "stil": _feld(d, "stil", 200) or STIL_STANDARD, "geschlecht": geschlecht,
                     "alter": alter, "eigenschaften": list(eigenschaften), "seed": neuer_seed()})
        if len(raus) >= anzahl:
            break
    if not raus:
        raise ValueError("Die KI hat keinen brauchbaren Vorschlag geliefert – noch einmal versuchen.")
    return raus


FELDER = {"kern": ("Aussehen", "ENGLISCH, ein Satz: Haare, Augen, Gesicht, Kleidung – ohne Alter"),
          "stil": ("Bildstil", "ENGLISCH, kurz: Licht, Stimmung, Art des Bildes"),
          "wesen": ("Wesen", "deutsch, 2–4 Sätze: wie die Persönlichkeit redet und was sie ausmacht"),
          "kurz": ("Kurzbeschreibung", "deutsch, 4–10 Worte")}
VERBESSERN_SYSTEM = ("Du hilfst, einen Charakter auszufeilen. Antworte NUR mit dem verbesserten Text des Feldes – "
                     "keine Anführungszeichen, keine Erklärung. Immer erwachsen.")


def verbessern_auftrag(feld: str, text: str, entwurf: dict, wunsch: str = "") -> str:
    if feld not in FELDER:
        raise ValueError("Unbekanntes Feld.")
    titel, regel = FELDER[feld]
    rest = {k: entwurf.get(k) for k in ("name", "kurz", "wesen", "kern", "stil") if k != feld and entwurf.get(k)}
    return (f"Charakter: {GESCHLECHTER.get(entwurf.get('geschlecht', 'n'), GESCHLECHTER['n'])[0]}, "
            f"{entwurf.get('alter', '?')} Jahre, Eigenschaften: {', '.join(entwurf.get('eigenschaften') or [])}. "
            f"Übrige Felder: {json.dumps(rest, ensure_ascii=False)}.\n"
            f"Verbessere das Feld „{titel}“ ({regel}): lebendiger, genauer, passend zum Rest."
            + (f" Wunsch: {wunsch.strip()[:300]}." if wunsch.strip() else "")
            + f"\nBisher: {text.strip()[:800] or '(leer)'}")


def verbessert_lesen(text: str, feld: str) -> str:
    t = re.sub(r"(?is)<think>.*?</think>", "", text or "").strip().strip('"„“').strip()
    return " ".join(t.split())[:700 if feld == "wesen" else 500]


# --- Übernehmen ins Profil ------------------------------------------------------------------------------------

def charakter_daten(entwurf: dict) -> dict:
    """Die Charakter-Datei (tools/charakter.py) aus einem Entwurf: Aussehen, fester Seed, Stil, Regeln."""
    g = entwurf.get("geschlecht", "n")
    wort = GESCHLECHTER.get(g, GESCHLECHTER["n"])[1]
    bild_alter = max(int(entwurf.get("alter") or BILD_ALTER_MIN), BILD_ALTER_MIN)
    return {"name": entwurf["name"],
            "kern": f"an adult {wort} around {bild_alter} years old, {entwurf['kern'].rstrip('.')}",
            "seed_referenz": int(entwurf.get("seed") or neuer_seed()), "stil": entwurf.get("stil") or STIL_STANDARD,
            "outfits": {}, "posen": {},
            "regeln": [f"Always one single adult {wort}, never younger than {BILD_ALTER_MIN}."]}


def uebernehmen(entwurf: dict, bild: str | None = None) -> dict:
    """Persönlichkeit + Charakter-Datei + Porträt anlegen. Gibt {key, name, pfad, charakter, bild} zurück.
    Aktiv setzen und Profilbild wählen macht der Aufrufer (die Sitzung)."""
    import charakter
    import persoenlichkeiten as PS
    import profilordner
    name = re.sub(r"[^\w .'-]", "", str(entwurf.get("name") or "")).strip()[:40]
    if not name or not str(entwurf.get("kern") or "").strip():
        raise ValueError("Name und Aussehen fehlen.")
    g = entwurf.get("geschlecht") if entwurf.get("geschlecht") in GESCHLECHTER else "n"
    g, alter, eigenschaften = auswahl(g, entwurf.get("alter"), entwurf.get("eigenschaften") or ["ruhig"])
    entwurf = {**entwurf, "name": name, "geschlecht": g, "alter": alter, "eigenschaften": eigenschaften}
    pfad = PS.create(name, str(entwurf.get("kurz") or ""), g, ton_aus(eigenschaften, str(entwurf.get("ton") or "")))
    p = PS.list_all().get(pfad.stem)
    if p is None:
        raise RuntimeError("Die Persönlichkeit wurde angelegt, lässt sich aber nicht lesen.")
    p_name = p.name
    for zeile in (f"- Du bist {alter} Jahre alt (erwachsen).",
                  f"- Deine Eigenschaften: {', '.join(eigenschaften)}.",
                  *([f"- {entwurf['wesen']}"] if entwurf.get("wesen") else [])):
        PS.add_line(p, zeile)
    datei = charakter.datei(p_name)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(charakter._sauber(charakter_daten({**entwurf, "name": p_name})), indent=2,
                                ensure_ascii=False), encoding="utf-8")
    ziel_bild = None
    if bild and Path(bild).is_file():
        ziel_bild = profilordner.ordner("Bilder", p_name) / f"portraet_{time.strftime('%Y%m%d_%H%M%S')}.png"
        shutil.copy2(bild, ziel_bild)
    return {"key": p.key, "name": p_name, "pfad": str(pfad), "charakter": str(datei),
            "bild": str(ziel_bild) if ziel_bild else ""}


def bearbeiten_laden(name: str) -> dict | None:
    """Die Charakter-Datei einer Persönlichkeit zum Bearbeiten (Aussehen, Stil, Seed)."""
    import charakter
    d = charakter.roh(name)
    if not d.get("kern"):
        return None
    return {"name": d.get("name") or name, "kern": d.get("kern", ""), "stil": d.get("stil", ""),
            "seed": d.get("seed_referenz")}


def bearbeitung_speichern(name: str, kern: str, stil: str, seed=None) -> Path:
    """Aussehen und Stil der Charakter-Datei ändern – alles andere (Outfits, Posen, Stimme) bleibt; der alte Stand
    geht in den Papierkorb."""
    import charakter
    kern = " ".join(str(kern or "").split())
    if not kern:
        raise ValueError("Das Aussehen darf nicht leer sein.")
    d = charakter.roh(name)
    d.update({"name": d.get("name") or name, "kern": kern, "stil": " ".join(str(stil or "").split())})
    if seed not in (None, ""):
        d["seed_referenz"] = int(seed)
    neu = charakter._sauber(d)
    if not neu.get("seed_referenz"):
        neu["seed_referenz"] = neuer_seed()
    p = charakter.datei(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        import snapshot
        snapshot.sichern(p, "charakter_galerie")
    from atomar import text_schreiben
    text_schreiben(p, json.dumps(neu, indent=2, ensure_ascii=False))
    return p
