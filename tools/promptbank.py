"""
promptbank.py - Prompt-Bibliothek für Krea 2: jeder Mal-Prompt jeder Persönlichkeit mit Urteil und Vektor.

learned/krea_prompts.db hält je Bild den ganzen Prompt, den Teil der KI, Modell, Seed, Größe, das Urteil des
Nutzers (bild_bewerten) mit seinem Warum und einen Vektor vom Gedächtnis-Modell. Vor dem Malen bekommt die KI
die ähnlichsten Einträge:

    eigene     Prompts der aktiven Persönlichkeit, ganz (bewährte zuerst)
    Technik    bewährte Prompts der anderen, nur die Sätze zu Licht, Kamera, Bildaufbau, Pose und Umgebung –
               Aussehen und Kleidung einer anderen Figur bleiben draußen
    Warnung    Prompts mit schlechtem Urteil, mit dem Warum

Eingetragen wird nach jedem Krea-Bild (actions._schmied_merken); Vorhandenes liest /bild prompts einlesen aus den
PNG-Daten der Bilder-Ordner. Vektoren rechnet die Bibliothek nur mit geladenem Gedächtnis-Modell (oder beim
Einlesen); ohne Vektor sucht sie nach gemeinsamen Wörtern. Die Datenbank sperrt actions._daten_privat für
Datei-Werkzeuge; die KI liest sie nur über prompt_suchen.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

try:
    from paths import DATEN as _DATEN
except Exception:                                  # Selbsttest ohne Bootstrap
    _DATEN = Path(__file__).resolve().parent.parent

DB = Path(_DATEN) / "learned" / "krea_prompts.db"
URTEILE = ("gut", "schlecht", "gemischt")
MAX_PROMPT = 4000
MAX_WARUM = 300
HINWEIS_ZEICHEN = 420            # je Eintrag im Hinweis vor dem Malen
VEKTOR_STAPEL = 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt (
    pfad TEXT PRIMARY KEY, besser TEXT DEFAULT '', profil TEXT NOT NULL, prompt TEXT NOT NULL,
    prompt_ki TEXT DEFAULT '', modell TEXT DEFAULT '', seed TEXT DEFAULT '', groesse TEXT DEFAULT '',
    zeit REAL NOT NULL, urteil TEXT DEFAULT '', warum TEXT DEFAULT '', vec BLOB, kennung TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS prompt_profil ON prompt(profil);
CREATE INDEX IF NOT EXISTS prompt_besser ON prompt(besser);
"""

_lock = threading.Lock()


def _verbinden(db: Path | None = None) -> sqlite3.Connection:
    p = Path(db or DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10)
    c.executescript(SCHEMA)
    return c


def _schluessel(pfad) -> str:
    try:
        return str(Path(str(pfad)).resolve()).casefold()
    except (OSError, ValueError):
        return str(pfad).casefold()


# ---------------------------------------------------------------------------
# Herkunft: Profil und PNG-Daten
# ---------------------------------------------------------------------------

def _profil_von(pfad) -> str:
    """Profil-Ordner der Persönlichkeit, zu der ein Bild gehört (Profile/<Name>/…) – sonst die aktive."""
    try:
        import profilordner
        wurzel = profilordner.wurzel().resolve()
        p = Path(str(pfad)).resolve()
        if wurzel in p.parents:
            return p.relative_to(wurzel).parts[0]
        return profilordner.aktiv()
    except Exception:
        return ""


def aktives_profil() -> str:
    try:
        import profilordner
        return profilordner.aktiv()
    except Exception:
        return ""


def _png_text(pfad) -> dict:
    try:
        import promptschmied
        return promptschmied.png_text(pfad)
    except Exception:
        return {}


_PARAM = re.compile(r"(\w+)=([^,]+)")


def ist_krea(daten: dict) -> bool:
    """Stammt das Bild von Krea 2 (Motor in den Parametern, sonst der Modellname)?"""
    params = dict(_PARAM.findall(daten.get("params", "")))
    if params.get("backend", "").strip():
        return params["backend"].strip().lower().startswith("krea")
    return "krea" in daten.get("model", "").lower()


def _zeile_aus_png(pfad, prompt_ki: str = "", profil: str | None = None) -> dict | None:
    daten = _png_text(pfad)
    prompt = " ".join(str(daten.get("prompt", "")).split())[:MAX_PROMPT]
    if not prompt or not ist_krea(daten):
        return None
    params = dict(_PARAM.findall(daten.get("params", "")))
    try:
        zeit = Path(str(pfad)).stat().st_mtime
    except OSError:
        zeit = time.time()
    return {"pfad": _schluessel(pfad), "profil": profil if profil is not None else _profil_von(pfad),
            "prompt": prompt,
            "prompt_ki": " ".join(str(prompt_ki or daten.get("prompt_ki", "")).split())[:MAX_PROMPT],
            "modell": daten.get("model", ""), "seed": params.get("seed", "").strip(),
            "groesse": params.get("size", "").strip(), "zeit": zeit}


# ---------------------------------------------------------------------------
# Eintragen, Urteil, Einlesen
# ---------------------------------------------------------------------------

def eintragen(pfade: list, prompt_ki: str = "", profil: str | None = None, db: Path | None = None) -> bool:
    """Ein gemaltes Krea-Bild eintragen; eine nachgebesserte Kopie (zweiter Pfad) gehört zum selben Eintrag."""
    pfade = [p for p in dict.fromkeys(str(x) for x in pfade if x)]
    if not pfade or (z := _zeile_aus_png(pfade[0], prompt_ki, profil)) is None:
        return False
    z["besser"] = _schluessel(pfade[1]) if len(pfade) > 1 else ""
    with _lock, closing(_verbinden(db)) as c, c:
        alt = c.execute("SELECT urteil, warum FROM prompt WHERE pfad = ?", (z["pfad"],)).fetchone()
        c.execute("INSERT OR REPLACE INTO prompt (pfad, besser, profil, prompt, prompt_ki, modell, seed, groesse, "
                  "zeit, urteil, warum) VALUES (:pfad, :besser, :profil, :prompt, :prompt_ki, :modell, :seed, "
                  ":groesse, :zeit, :urteil, :warum)",
                  {**z, "urteil": alt[0] if alt else "", "warum": alt[1] if alt else ""})
    return True


def urteil(pfad, wertung: str, warum: str = "", db: Path | None = None) -> bool:
    """Urteil des Nutzers zu einem Bild (auch über die nachgebesserte Kopie). Unbekannte Krea-Bilder kommen
    vorher aus ihren PNG-Daten dazu. False = kein Krea-Bild."""
    if wertung not in URTEILE:
        return False
    s = _schluessel(pfad)
    warum = " ".join(str(warum or "").split())[:MAX_WARUM]
    with _lock, closing(_verbinden(db)) as c, c:
        n = c.execute("UPDATE prompt SET urteil = ?, warum = ? WHERE pfad = ? OR besser = ?",
                      (wertung, warum, s, s)).rowcount
    if n:
        return True
    if not eintragen([pfad], db=db):
        return False
    return urteil(pfad, wertung, warum, db)


_GESCHMACK = re.compile(r"Bildgeschmack (👍|👎|➖): (.*?) \(Bild ([^,)]+)")
_EMOJI = {"👍": "gut", "👎": "schlecht", "➖": "gemischt"}


def _alte_urteile() -> dict[str, tuple[str, str]]:
    """Dateiname (klein) → (Urteil, Warum) aus den Notizen von bild_bewerten."""
    try:
        import memory
        notizen = memory.nach_art("geschmack", 10_000)
    except Exception:
        return {}
    raus = {}
    for e in reversed(notizen):                    # älteste zuerst: das neueste Urteil gewinnt
        if (m := _GESCHMACK.search(str(e.get("text", "")))):
            raus[m.group(3).strip().casefold()] = (_EMOJI[m.group(1)], m.group(2).strip()[:MAX_WARUM])
    return raus


def bilder_ordner() -> list[Path]:
    """Bilder-Ordner aller Profile und der allgemeine im Daten-Ordner."""
    ordner = [Path(_DATEN) / "Bilder"]
    try:
        import profilordner
        wurzel = profilordner.wurzel()
        if wurzel.is_dir():
            ordner += [p / "Bilder" for p in sorted(wurzel.iterdir()) if p.is_dir()]
    except Exception:
        pass
    return [o for o in ordner if o.is_dir()]


def einlesen(ordner: list[Path] | None = None, db: Path | None = None, melden=None) -> tuple[int, int]:
    """Vorhandene Krea-Bilder aus den Bilder-Ordnern eintragen (nur die oberste Ebene – Unterordner wie
    Gesichtszuege halten Kopien), mit den Urteilen aus bild_bewerten. (neu, Krea-Bilder gesamt)."""
    with closing(_verbinden(db)) as c:
        drin = {r[0] for r in c.execute("SELECT pfad FROM prompt")}
    alte = _alte_urteile()
    neu = gesamt = 0
    for o in (ordner if ordner is not None else bilder_ordner()):
        profil = _profil_von(o / "x.png")
        for i, p in enumerate(sorted(o.glob("*.png"))):
            if melden and i % 200 == 0:
                melden(f"{o.parent.name}: {i} Bilder …")
            s = _schluessel(p)
            if s in drin:
                gesamt += 1
                continue
            if (z := _zeile_aus_png(p, profil=profil)) is None:
                continue
            gesamt += 1
            neu += 1
            wertung, warum = alte.get(p.name.casefold(), ("", ""))
            with _lock, closing(_verbinden(db)) as c, c:
                c.execute("INSERT OR IGNORE INTO prompt (pfad, profil, prompt, prompt_ki, modell, seed, groesse, "
                          "zeit, urteil, warum) VALUES (:pfad, :profil, :prompt, :prompt_ki, :modell, :seed, "
                          ":groesse, :zeit, :urteil, :warum)", {**z, "urteil": wertung, "warum": warum})
    return neu, gesamt


# ---------------------------------------------------------------------------
# Vektoren
# ---------------------------------------------------------------------------

def _kennung() -> str:
    try:
        import indexdb
        return indexdb._kennung()
    except Exception:
        return ""


def _such_text(prompt: str, prompt_ki: str) -> str:
    return prompt_ki or prompt


def vektorisieren(db: Path | None = None, laden: bool = False, max_n: int = 2000, melden=None) -> int:
    """Fehlende oder veraltete Vektoren rechnen. Ohne `laden` nur, wenn das Gedächtnis-Modell schon geladen ist
    (sonst bräuchte das ein paar Sekunden). Anzahl der neuen Vektoren."""
    try:
        import indexdb
        import memory
        if not laden and not memory.encoder_loaded():
            return 0
    except Exception:
        return 0
    kennung = _kennung()
    if not kennung:
        return 0
    with closing(_verbinden(db)) as c:
        offen = c.execute("SELECT pfad, prompt, prompt_ki FROM prompt WHERE vec IS NULL OR kennung != ? LIMIT ?",
                          (kennung, max_n)).fetchall()
    fertig = 0
    for i in range(0, len(offen), VEKTOR_STAPEL):
        teil = offen[i:i + VEKTOR_STAPEL]
        if melden:
            melden(f"Vektoren {i}/{len(offen)} …")
        try:
            vecs = memory._embed_texts([_such_text(p, k) for _s, p, k in teil])
        except Exception:
            vecs = None
        if not vecs:
            break
        with _lock, closing(_verbinden(db)) as c, c:
            for (s, _p, _k), v in zip(teil, vecs):
                c.execute("UPDATE prompt SET vec = ?, kennung = ? WHERE pfad = ?", (indexdb.vec_packen(v), kennung, s))
        fertig += len(teil)
    return fertig


def _frage_vektor(frage: str, laden: bool) -> list[float] | None:
    try:
        import memory
        if not laden and not memory.encoder_loaded():
            return None
        vecs = memory._embed_texts([frage], query=True)
        return vecs[0] if vecs else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Technik: was sich von einer anderen Figur übernehmen lässt
# ---------------------------------------------------------------------------

# Aussehen und Kleidung einer Figur – solche Sätze bleiben bei fremden Prompts draußen.
_FIGUR = re.compile(
    r"(?i)\b(hair\w*|blond\w*|brunette|redhead|ginger|bangs|braid\w*|ponytail|curl\w*|eye\w*|iris\w*|lash\w*|"
    r"brow\w*|freckle\w*|mole\w*|lip\w*|mouth|teeth|nose|cheek\w*|jaw\w*|chin|skin\w*|complexion|"
    r"tan|tanned|pale|fair|olive|ebony|bust\w*|breast\w*|chest|cleavage|waist\w*|hip\w*|thigh\w*|leg\w*|butt\w*|"
    r"curv\w*|hourglass|figure|body|slim|petite|athletic|muscular|tattoo\w*|piercing\w*|nail\w*|makeup|"
    r"eyeliner|mascara|lipstick|wear\w*|dress\w*|gown|skirt|shirt|blouse|top|jacket|coat|jeans|pants|shorts|"
    r"bikini|lingerie|bra|stocking\w*|heels|boots|outfit|satin|silk|lace|leather|latex|neckline|strap\w*|"
    r"sleeve\w*|nude|naked|topless|bare|undress\w*|haar\w*|augen|haut|lippen|figur|oberweite|taille|hüfte\w*|"
    r"nägel|sommersprossen|kleid\w*|nackt)\b")
# Ich-Form der anderen Figur („of me“, „I look …“) – neutral als „the subject“.
_ICH = ((re.compile(r"\b(?:me|myself)\b"), "the subject"), (re.compile(r"\bmy\b"), "the subject's"),
        (re.compile(r"\bMy\b"), "The subject's"), (re.compile(r"\bI\b"), "the subject"))
_TECHNIK = re.compile(
    r"(?i)\b(light\w*|lit|lamp\w*|glow\w*|shadow\w*|sun\w*|window|neon|candle\w*|backlit|rim|golden hour|"
    r"lens|\d{2,3}\s?mm|f/\d|focus\w*|depth of field|bokeh|shot|photograph\w*|portrait|close-up|closeup|"
    r"medium|full body|wide|angle|camera|frame\w*|composition|centered|background|foreground|room|interior|"
    r"street|beach|forest|studio|wall|floor|sofa|bed|window|stand\w*|sit\w*|lean\w*|kneel\w*|pose\w*|looking|"
    r"looks|gaze|smil\w*|expression|cinematic|film|grain|color grad\w*|palette|atmosphere|mood)\b")
_SATZ = re.compile(r"(?<=[.!?])\s+")


def technik(prompt: str) -> str:
    """Nur die übertragbaren Sätze eines Prompts: Licht, Kamera, Bildaufbau, Pose, Umgebung – ohne Aussehen und
    Kleidung der Figur, ohne deutsche Sätze."""
    try:
        import promptschmied
        deutsch = promptschmied._DEUTSCH
    except Exception:
        deutsch = re.compile(r"(?i)\b(der|die|das|und|mit|ein|eine)\b")
    saetze = []
    for s in _SATZ.split(" ".join(str(prompt or "").split())):
        if _TECHNIK.search(s) and not _FIGUR.search(s) and len(deutsch.findall(s)) < 2:
            for muster, ersatz in _ICH:
                s = muster.sub(ersatz, s)
            saetze.append(s.strip()[:1].upper() + s.strip()[1:])
    return " ".join(saetze)


# ---------------------------------------------------------------------------
# Suchen
# ---------------------------------------------------------------------------

_WORT = re.compile(r"[a-zäöüß]{3,}")
_FUELL = frozenset("the and with her his she him its into from that this for are was has have over very".split())


def _woerter(text: str) -> set[str]:
    return {w for w in _WORT.findall(str(text or "").lower()) if w not in _FUELL}


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def suchen(frage: str, profil: str | None = None, anzahl: int = 3, db: Path | None = None,
           laden: bool = False) -> dict[str, list[dict]]:
    """{"eigen", "technik", "warnung"} – je Liste die ähnlichsten Einträge (mit `aehnlich`)."""
    raus: dict[str, list[dict]] = {"eigen": [], "technik": [], "warnung": []}
    if not Path(db or DB).is_file():
        return raus
    profil = aktives_profil() if profil is None else profil
    with closing(_verbinden(db)) as c:
        c.row_factory = sqlite3.Row
        zeilen = [dict(r) for r in c.execute("SELECT * FROM prompt")]
    if not zeilen:
        return raus
    fv, kennung = _frage_vektor(frage, laden), _kennung()
    fw = _woerter(frage)
    try:
        import indexdb
        entpacken = indexdb.vec_entpacken
    except Exception:
        entpacken = None
    for z in zeilen:
        v = entpacken(z["vec"]) if fv and entpacken and z.get("vec") and z.get("kennung") == kennung else None
        if v:
            z["aehnlich"] = _cos(fv, v)
        else:
            zw = _woerter(_such_text(z["prompt"], z["prompt_ki"]))
            z["aehnlich"] = len(fw & zw) / (len(fw) or 1) * 0.6 if fw else 0.0
        z["eigen"] = z["profil"].casefold() == (profil or "").casefold()
        bonus = {"gut": 0.08, "gemischt": 0.0, "": 0.0}.get(z["urteil"], 0.0)
        z["rang"] = z["aehnlich"] + bonus + z["zeit"] / 1e12             # Gleichstand: neuere zuerst
    zeilen.sort(key=lambda z: -z["rang"])
    for z in zeilen:
        if z["urteil"] == "schlecht":
            if len(raus["warnung"]) < anzahl and (z["eigen"] or technik(z["prompt_ki"] or z["prompt"])):
                raus["warnung"].append(z)
        elif z["eigen"]:
            if len(raus["eigen"]) < anzahl:
                raus["eigen"].append(z)
        elif z["urteil"] == "gut" and (t := technik(z["prompt_ki"] or z["prompt"])):
            if len(raus["technik"]) < anzahl:
                raus["technik"].append({**z, "technik": t})
    return raus


def _kurz(text: str, n: int = HINWEIS_ZEICHEN) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def _marke(z: dict) -> str:
    return {"gut": "👍", "schlecht": "👎", "gemischt": "➖"}.get(z["urteil"], "·")


def zeilen_fuer_ki(treffer: dict[str, list[dict]], zeichen: int = HINWEIS_ZEICHEN) -> list[str]:
    """Treffer als kurze Zeilen: eigene ganz, fremde nur als Technik, Warnungen mit dem Warum."""
    zeilen = []
    for z in treffer["eigen"]:
        zeilen.append(f"- {_marke(z)} eigener Prompt: {_kurz(z['prompt_ki'] or z['prompt'], zeichen)}"
                      + (f" — Nutzer: {z['warum']}" if z["warum"] else ""))
    for z in treffer["technik"]:
        zeilen.append(f"- 👍 Technik (andere Figur): {_kurz(z['technik'], zeichen)}")
    for z in treffer["warnung"]:
        text = z["prompt_ki"] or z["prompt"] if z["eigen"] else technik(z["prompt_ki"] or z["prompt"])
        zeilen.append(f"- 👎 ging schief: {_kurz(text, zeichen)}" + (f" — Nutzer: {z['warum']}" if z["warum"] else ""))
    return zeilen


def hinweis(frage: str, profil: str | None = None, db: Path | None = None) -> str:
    """Block für die Mal-Anleitung: die ähnlichsten Einträge zur Nachricht – leer ohne Treffer."""
    if not frage or not Path(db or DB).is_file():
        return ""
    try:
        zeilen = zeilen_fuer_ki(suchen(frage, profil, 2, db))
    except Exception:
        return ""
    if not zeilen:
        return ""
    return ("- 📚 Aus deiner Prompt-Bibliothek (ähnlich zu dieser Nachricht; Muster, nicht wörtlich kopieren – dein "
            "Aussehen kommt aus Kern und Persönlichkeit). Mehr: prompt_suchen.\n"
            + "\n".join(f"  {z}" for z in zeilen) + "\n")


def stand(db: Path | None = None) -> dict:
    """Zahlen für /bild prompts: gesamt, je Urteil, mit Vektor, je Profil."""
    if not Path(db or DB).is_file():
        return {"gesamt": 0, "urteile": {}, "vektor": 0, "profile": {}}
    with closing(_verbinden(db)) as c:
        gesamt = c.execute("SELECT COUNT(*) FROM prompt").fetchone()[0]
        urteile = dict(c.execute("SELECT urteil, COUNT(*) FROM prompt WHERE urteil != '' GROUP BY urteil"))
        vektor = c.execute("SELECT COUNT(*) FROM prompt WHERE vec IS NOT NULL AND kennung = ?",
                           (_kennung(),)).fetchone()[0]
        profile = dict(c.execute("SELECT profil, COUNT(*) FROM prompt GROUP BY profil ORDER BY 2 DESC"))
    return {"gesamt": gesamt, "urteile": urteile, "vektor": vektor, "profile": profile}
