"""
promptschmied.py - Prompt-Prüfung für Krea 2 vor dem Malen und Lernen aus Bildurteilen.

Krea 2 liest den Prompt mit einem Sprachmodell (Qwen3-VL): ganze englische Sätze statt Schlagwortlisten,
keine Gewichtsklammern, keine Qualitätswörter. Turbo-Modelle haben keinen Negativ-Prompt (CFG 1), Verneinungen
im Prompt wirken kaum. Der Erkenner findet das vor dem Malen. Modus (Config `bild_schmied`):

    an      ein KI-Prompt mit harten Funden kommt einmal mit Hinweisen zurück; der nächste Aufruf malt.
            Weiche Funde (Licht, Fokus) halten nicht auf, sie stehen im Ergebnis.
    zeigen  es wird gemalt, alle Funde stehen im Ergebnis
    aus     keine Prüfung

Prompts des Nutzers (/bild) werden nie geändert, dort gibt es nur Hinweise.

Der Lerner hält je Bild Modell, Prompt und das spätere Urteil (bild_bewerten) in learned/promptschmied.db
und zählt je Modell, welche Wortbausteine bei guten und schlechten Bildern standen.
"""
from __future__ import annotations

import re
import sqlite3
import struct
import threading
import time
import zlib
from contextlib import closing, contextmanager
from pathlib import Path

try:
    from paths import DATEN as _DATEN
except Exception:                                  # Selbsttest ohne Bootstrap
    _DATEN = Path(__file__).resolve().parent.parent

DB = Path(_DATEN) / "learned" / "promptschmied.db"
MODI = ("an", "zeigen", "aus")
VORGABE = "an"
OFFEN_S = 600.0                  # so lange gilt ein zurückgegebener Prompt als „in Überarbeitung“
MIN_WOERTER = 12
MIN_URTEILE = 3                  # ab so vielen Urteilen gilt ein Baustein als belegt
MAX_BAUSTEINE = 60               # je Bild
MAX_HINWEISE = 6                 # je Richtung (bewährt / eher schlecht)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bild (
    pfad TEXT PRIMARY KEY, modell TEXT NOT NULL, prompt TEXT NOT NULL, vorher TEXT DEFAULT '',
    funde TEXT DEFAULT '', zeit REAL NOT NULL, urteil TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS baustein (
    modell TEXT NOT NULL, baustein TEXT NOT NULL, gut INTEGER DEFAULT 0, schlecht INTEGER DEFAULT 0,
    PRIMARY KEY (modell, baustein)
);
"""

# ---------------------------------------------------------------------------
# Erkenner
# ---------------------------------------------------------------------------

_ZITAT = re.compile(r"\"[^\"]*\"|“[^”]*”|„[^“”]*[“”]")                  # Text im Bild ist kein Prompt-Fehler
_GEWICHT = re.compile(r"\(\s*[^()]{1,80}:\s*\d+(?:[.,]\d+)?\s*\)|\(\(|\[\[|\{\{")
_VERNEINUNG = re.compile(r"(?i)\b(no|not|without|never|avoid|don't|doesn't|isn't|aren't|kein\w*|nicht|ohne)\b")
_QUALITAET = re.compile(r"(?i)\b(masterpiece|best quality|high quality|highest quality|ultra[- ]detailed|"
                        r"hyper[- ]?detailed|8k|4k|uhd|hdr|trending on artstation|award[- ]winning|"
                        r"ultra realistic|hyperrealistic|absurdres)\b")
_LICHT = re.compile(r"(?i)\b(light\w*|lit|sun\w*|glow\w*|shadow\w*|lamp\w*|neon|candle\w*|backlit|daylight|"
                    r"moonlight|overcast|golden hour|blue hour|illuminat\w*|dusk|dawn|twilight|"
                    r"licht\w*|beleucht\w*|sonne\w*|schatten|kerze\w*|dämmer\w*|lampe\w*)\b")
_FOKUS = re.compile(r"(?i)(\b\d{2,3}\s?mm\b|\bf/\d|\bfocus\w*|\bsharp\w*|\bbokeh\b|depth of field|\bdof\b|"
                    r"\blens\b|\bmacro\b|close-?up|\bshot\b|\bcamera\b|\bangle\b|\bframing\b|"
                    r"\bfokus\w*|\bscharf\w*|\bobjektiv\w*|\bkamera\w*|\bnahaufnahme\w*|\bperspektive\w*)")
_GEZEICHNET = re.compile(r"(?i)\b(anime|manga|cartoon|illustration|drawing|sketch|painting|painted|"
                         r"watercolou?r|oil paint\w*|pixel art|3d render|cel[- ]shad\w*|comic|vector|"
                         r"zeichnung|gemälde|aquarell)\b")
_DEUTSCH = re.compile(r"(?i)\b(der|die|das|und|mit|ein|eine|einer|ist|sind|auf|im|vom|zum|beim|sie|"
                      r"trägt|steht|sitzt)\b")
_WORT = re.compile(r"[\wäöüß'/.\-]+")
# Ansicht von hinten und zugleich etwas, das man nur von vorne sieht: Krea entscheidet sich dann meist für vorne.
# Ein Blick über die Schulter passt zu „von hinten“ und zählt nicht.
_HINTEN = re.compile(r"(?i)\b(from behind|from the back|seen from the back|back view|rear view|back to the camera|"
                     r"facing away|turned away|von hinten|rückansicht)\b")
_VORNE_TEIL = r"(?:breasts?|chest|bust|cleavage|nipples?|navel|belly|abdomen|stomach)"
_VORNE_SICHT = r"(?:visible|showing|shown|exposed|seen|revealed|revealing|exposing|on display)"
_VORNE = re.compile(rf"(?i)\b{_VORNE_TEIL}\b[^.]{{0,60}}\b{_VORNE_SICHT}\b|\b{_VORNE_SICHT}\b[^.]{{0,40}}\b{_VORNE_TEIL}\b"
                    r"|\b(?:looking|staring|gazing|smiling)\s+(?:straight\s+|directly\s+)?(?:at|into)\s+(?:the\s+)?"
                    r"(?:camera|viewer|lens)\b|\beye contact\b|\bfacing\s+(?:the\s+)?(?:camera|viewer)\b"
                    r"|\bfront view\b|\bfrontal\b")

FUNDE = {
    "gewicht": "Gewichtsklammern wie (wort:1.3) oder ((…)) wirken bei Krea nicht – Wichtiges nach vorne "
               "und genauer beschreiben.",
    "verneinung": "Verneinung im Prompt ({wort}) – Krea hat keinen Negativ-Prompt. Positiv sagen, was zu "
                  "sehen sein soll (statt „no blur“: „sharp focus throughout“).",
    "qualitaet": "Qualitätswörter ({wort}) bringen bei Krea nichts – Oberflächen, Licht und Material "
                 "beschreiben.",
    "liste": "Schlagwortliste – Krea liest ganze Sätze: Bildart und Einstellung, Motiv, Details, Licht, "
             "Hintergrund als Fließtext.",
    "kurz": "Sehr kurz ({n} Wörter) – genaue Beschreibungen geben bei Krea die besten Bilder.",
    "sprache": "Der Prompt ist deutsch – Krea folgt englischen Sätzen am genauesten.",
    "licht": "Kein Licht beschrieben (z. B. soft window light, golden hour, studio light, neon glow).",
    "fokus": "Keine Kamera und kein Fokus (z. B. „sharp focus on the eyes“, 85mm, f/1.8, shallow depth of "
             "field, close-up).",
    "ansicht": "Widerspruch in der Ansicht: {hinten}, aber auch {vorne} – das sieht man nur von vorne, und Krea "
               "malt dann meist von vorne. Entscheide dich: von hinten (Rücken, Po, Blick über die Schulter) oder "
               "von vorne.",
}
WEICH = frozenset({"licht", "fokus"})          # halten nicht auf, nur Hinweis


def _ohne_zitate(prompt: str) -> str:
    return _ZITAT.sub(" ", prompt or "")


def pruefen(prompt: str) -> list[tuple[str, str]]:
    """Funde als (Kennung, Hinweis). Leer = nichts aufgefallen."""
    text = _ohne_zitate(prompt)
    woerter = _WORT.findall(text)
    funde: list[tuple[str, str]] = []
    if _GEWICHT.search(text):
        funde.append(("gewicht", FUNDE["gewicht"]))
    if (m := _VERNEINUNG.search(text)):
        funde.append(("verneinung", FUNDE["verneinung"].format(wort=f"„{m.group(0)}“")))
    if (m := _QUALITAET.search(text)):
        funde.append(("qualitaet", FUNDE["qualitaet"].format(wort=f"„{m.group(0)}“")))
    teile = [t.strip() for t in re.split(r"[,;|\n]", text) if t.strip()]
    if len(teile) >= 6 and sorted(len(t.split()) for t in teile)[len(teile) // 2] <= 3:
        funde.append(("liste", FUNDE["liste"]))
    if len(woerter) < MIN_WOERTER:
        funde.append(("kurz", FUNDE["kurz"].format(n=len(woerter))))
    if woerter and len(_DEUTSCH.findall(text)) >= max(3, len(woerter) // 8):
        funde.append(("sprache", FUNDE["sprache"]))
    if not _LICHT.search(text):
        funde.append(("licht", FUNDE["licht"]))
    if not _FOKUS.search(text) and not _GEZEICHNET.search(text):
        funde.append(("fokus", FUNDE["fokus"]))
    if (h := _HINTEN.search(text)) and (v := _VORNE.search(text)):
        funde.append(("ansicht", FUNDE["ansicht"].format(hinten=f"„{h.group(0)}“",
                                                          vorne=f"„{' '.join(v.group(0).split())}“")))
    return funde


def modus() -> str:
    """an · zeigen · aus. In der Test-Suite aus: ihre Mal-Tests arbeiten mit kurzen Attrappen-Prompts
    (test_promptschmied prüft den Schmied selbst)."""
    try:
        import paths
        if paths._testlauf():
            return "aus"
    except Exception:
        pass
    try:
        import config
        m = str(config.load().get("bild_schmied", VORGABE)).strip().lower()
    except Exception:
        m = VORGABE
    return m if m in MODI else VORGABE


def set_modus(m: str) -> str:
    m = str(m or "").strip().lower()
    if m not in MODI:
        raise ValueError(f"Modus {m!r} gibt es nicht – {' · '.join(MODI)}.")
    import config
    config.update(bild_schmied=m)
    return m


_lock = threading.Lock()
_offen: dict = {}                # der zuletzt zurückgegebene Prompt: {"prompt", "zeit"}


def vorpruefen(prompts: list[str], modell: str | None) -> tuple[str | None, str]:
    """Vor einem KI-Malauftrag (Krea 2). Rückgabe (Rückgabetext oder None = malen, vorheriger Prompt).
    Im Modus „an“ geht ein Auftrag mit harten Funden einmal zurück; der nächste Auftrag malt in jedem Fall."""
    if modus() != "an":
        return None, ""
    jetzt = time.time()
    with _lock:
        alt = dict(_offen)
        _offen.clear()
    if alt and jetzt - alt["zeit"] < OFFEN_S:
        return None, alt["prompt"]
    funde = [(i, pruefen(p)) for i, p in enumerate(prompts, 1)]
    funde = [(i, f) for i, f in funde if f]
    if not any(k not in WEICH for _i, f in funde for k, _h in f):
        return None, ""
    with _lock:
        _offen.update(prompt="\n".join(prompts), zeit=jetzt)
    zeilen = ["🔨 Prompt-Schmied: noch nicht gemalt – der Prompt passt so nicht gut zu Krea 2. Überarbeite ihn "
              "und ruf das Werkzeug gleich nochmal auf (der nächste Aufruf malt in jedem Fall):"]
    for i, f in funde:
        kopf = f"Bild {i}: " if len(prompts) > 1 else ""
        zeilen += [f"- {kopf}{h}" for _k, h in f]
    if (gelernt := hinweise(modell)):
        zeilen.append(gelernt)
    zeilen.append("Halte Motiv und Wunsch des Nutzers genau bei – nur die Form ändern.")
    return "\n".join(zeilen), ""


def nachher(prompts: list[str]) -> str:
    """Hinweise fürs Ergebnis eines gemalten Auftrags: im Modus „an“ die weichen Funde, bei „zeigen“ alle."""
    m = modus()
    if m == "aus":
        return ""
    zeilen = []
    for i, p in enumerate(prompts, 1):
        kopf = f"Bild {i}: " if len(prompts) > 1 else ""
        zeilen += [f"- {kopf}{h}" for k, h in pruefen(p) if m == "zeigen" or k in WEICH]
    return ("\n💡 Prompt-Schmied – fürs nächste Bild:\n" + "\n".join(zeilen)) if zeilen else ""


def tipps(prompt: str) -> str:
    """Kurze Hinweise für einen Prompt des Nutzers (nur anzeigen, nichts ändern)."""
    f = pruefen(prompt)
    return " · ".join(h.split(" – ")[0] for _k, h in f)


# ---------------------------------------------------------------------------
# Lerner
# ---------------------------------------------------------------------------

_FUELL = frozenset("""
a an the and or of in on at to with by for from into onto over under is are was were be been being it its
this that these those as her his their she he they them very some any while which who whom whose there here
then than so such also just only up down out off about after before behind near between through
der die das den dem des ein eine einer einem eines und oder mit auf im in am an zu zum zur von vom bei beim
ist sind war hat sie er es ihr ihre sein seine wie als auch nur
""".split())


def bausteine(prompt: str) -> list[str]:
    """Wörter und Wortpaare eines Prompts ohne Füllwörter – die Einheiten, die der Lerner zählt."""
    woerter = [w.strip(".'-").lower() for w in _WORT.findall(_ohne_zitate(prompt))]
    woerter = [w for w in woerter if w and w not in _FUELL and not w.isdigit()]
    aus: list[str] = []
    for i, w in enumerate(woerter):
        if len(w) >= 4 or re.fullmatch(r"\d+mm|f/[\d.]+", w):
            aus.append(w)
        if i + 1 < len(woerter):
            aus.append(f"{w} {woerter[i + 1]}")
    return list(dict.fromkeys(aus))[:MAX_BAUSTEINE]


@contextmanager
def _verbinden(db: Path | None = None):
    """Verbindung mit Transaktion; wird danach geschlossen."""
    p = Path(db or DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(p), timeout=15)) as c:
        c.execute("PRAGMA busy_timeout=15000")
        c.executescript(SCHEMA)
        with c:
            yield c


def _schluessel(pfad) -> str:
    return str(Path(str(pfad))).casefold()


def merken(pfade: list, modell: str, prompt: str, vorher: str = "", funde: str = "",
           db: Path | None = None) -> None:
    """Ein gemaltes Bild (auch seine nachgebesserte Kopie) für spätere Urteile vormerken."""
    if not modell or not prompt:
        return
    with _lock, _verbinden(db) as c:
        for p in dict.fromkeys(str(x) for x in pfade if x):
            c.execute("INSERT OR REPLACE INTO bild (pfad, modell, prompt, vorher, funde, zeit) "
                      "VALUES (?, ?, ?, ?, ?, ?)", (_schluessel(p), modell, prompt, vorher, funde, time.time()))


def urteil(pfad, wertung: str, modell: str = "", prompt: str = "", db: Path | None = None) -> bool:
    """Urteil (gut · schlecht · gemischt) zu einem Bild zählen. Unbekannte Bilder kommen mit Modell und Prompt
    aus den PNG-Daten dazu. Ein neues Urteil zum selben Bild ersetzt das alte. False = nichts zu lernen."""
    if wertung not in ("gut", "schlecht", "gemischt"):
        return False
    schluessel = _schluessel(pfad)
    with _lock, _verbinden(db) as c:
        zeile = c.execute("SELECT modell, prompt, urteil FROM bild WHERE pfad = ?", (schluessel,)).fetchone()
        if zeile is None:
            if not modell or not prompt:
                return False
            c.execute("INSERT INTO bild (pfad, modell, prompt, zeit) VALUES (?, ?, ?, ?)",
                      (schluessel, modell, prompt, time.time()))
            zeile = (modell, prompt, "")
        modell, prompt, alt = zeile
        teile = bausteine(prompt)
        for wert, richtung in ((alt, -1), (wertung, 1)):
            spalte = {"gut": "gut", "schlecht": "schlecht"}.get(wert)
            if spalte is None:
                continue
            for b in teile:
                c.execute(f"INSERT INTO baustein (modell, baustein, {spalte}) VALUES (?, ?, max(0, ?)) "
                          f"ON CONFLICT(modell, baustein) DO UPDATE SET {spalte} = max(0, {spalte} + ?)",
                          (modell, b, richtung, richtung))
        c.execute("UPDATE bild SET urteil = ? WHERE pfad = ?", (wertung, schluessel))
    return True


def gelernt(modell: str | None, db: Path | None = None) -> tuple[list[str], list[str]]:
    """(bewährt, eher schlecht) für ein Modell – nur Bausteine mit genug Urteilen und klarer Richtung."""
    if not modell or not Path(db or DB).is_file():
        return [], []
    try:
        with _verbinden(db) as c:
            zeilen = c.execute("SELECT baustein, gut, schlecht FROM baustein WHERE modell = ? AND gut + schlecht >= ?",
                               (modell, MIN_URTEILE)).fetchall()
    except sqlite3.Error:
        return [], []
    bewertet = [(b, (g + 1) / (g + s + 2), g + s) for b, g, s in zeilen]
    # Wortpaare vor Einzelwörtern: sie sagen genauer, was gewirkt hat.
    gut = sorted((x for x in bewertet if x[1] >= 0.75), key=lambda x: (-x[1], -(" " in x[0]), -x[2]))
    schlecht = sorted((x for x in bewertet if x[1] <= 0.25), key=lambda x: (x[1], -(" " in x[0]), -x[2]))
    return [b for b, _, _ in gut[:MAX_HINWEISE]], [b for b, _, _ in schlecht[:MAX_HINWEISE]]


def hinweise(modell: str | None, db: Path | None = None) -> str:
    """Was der Lerner für dieses Modell weiß, als eine Zeile – leer, solange zu wenig Urteile da sind."""
    gut, schlecht = gelernt(modell, db)
    if not gut and not schlecht:
        return ""
    teile = []
    if gut:
        teile.append("bewährt: " + ", ".join(f"„{b}“" for b in gut))
    if schlecht:
        teile.append("eher schlecht: " + ", ".join(f"„{b}“" for b in schlecht))
    return f"Aus den Bildurteilen bei {modell} – " + " · ".join(teile) + "."


# ---------------------------------------------------------------------------
# PNG-Daten
# ---------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n"


def png_text(pfad) -> dict:
    try:
        from PIL import Image
        with Image.open(pfad) as im:
            return {str(k): str(v) for k, v in (getattr(im, "text", None) or im.info or {}).items()}
    except Exception:
        return {}


def png_ergaenzen(pfad, felder: dict) -> bool:
    """Textfelder als iTXt (UTF-8) vor IEND einfügen – die Bilddaten werden nicht neu geschrieben."""
    p = Path(str(pfad))
    try:
        roh = p.read_bytes()
    except OSError:
        return False
    if not roh.startswith(_PNG) or roh[-12:-8] != b"\x00\x00\x00\x00" or roh[-8:-4] != b"IEND":
        return False
    stuecke = []
    for k, v in felder.items():
        if not v:
            continue
        daten = str(k).encode("latin-1", "replace")[:79] + b"\x00\x00\x00\x00\x00" + str(v).encode("utf-8")
        stuecke.append(struct.pack(">I", len(daten)) + b"iTXt" + daten
                       + struct.pack(">I", zlib.crc32(b"iTXt" + daten) & 0xFFFFFFFF))
    if not stuecke:
        return True
    zwischen = p.with_name(p.name + ".schmied")
    try:
        zwischen.write_bytes(roh[:-12] + b"".join(stuecke) + roh[-12:])
        zwischen.replace(p)
    except OSError:
        zwischen.unlink(missing_ok=True)
        return False
    return True
