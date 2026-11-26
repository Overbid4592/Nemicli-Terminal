"""charaktergalerie.py – Charakter-Galerie: Charaktere von der KI erfinden lassen oder eigene erschaffen.

Ablauf im Fenster (ui/charakter_fenster.py, /charaktere): 🎲 Zufall – wer (Frauen, Männer, nichtbinär, gemischt), wie
viele (3, 6, 9), Alter und Wünsche; die KI erfindet die Charaktere mit Namen, Wesen, Geschichte und Aussehen, Krea
malt je ein Porträt. Oder eigene: Geschlecht und Alter (ab 18) → Aussehen (Haare, Augen, Körper) → bis zu 8
Eigenschaften → die KI schreibt Vorschläge mit diesem Aussehen, Krea malt je ein Porträt → einen anklicken, mit ✨ (die
KI hilft) verbessern → Name (die KI schlägt 6 vor) → übernehmen. Unterwegs lässt sich alles auch im Gespräch mit der
KI einstellen (dieselben Felder, dieselbe Prüfung). Übernehmen legt fest an: die Persönlichkeit (Persoenlichkeiten/),
die Charakter-Datei mit Aussehen (`kern` + die Angaben in `aussehen`) und festem Seed und das Porträt als Profilbild
(Profile/<Name>/).

Porträts der Vorschläge liegen bis zum Übernehmen in Charaktergalerie/ im Datenordner.
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
BILD_ALTER_MIN = ALTER_MIN          # das Porträt zeigt das gewählte Alter, nie unter 18
MAX_EIGENSCHAFTEN = 8
ANZAHL_VORSCHLAEGE = 6
MAX_IDEE = 300
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
STIL_STANDARD = ("portrait photograph, soft directional window light, natural skin texture with fine pores, "
                 "sharp focus on the eyes, shallow depth of field, 85mm lens")

# Zufall: Geschlecht und Alter legt der Code je Platz fest (die KI übernimmt sie), die Wünsche gelten für alle.
ZUFALL_WER = {"w": "Frauen", "m": "Männer", "n": "Nichtbinär", "gemischt": "Gemischt"}
ZUFALL_ANZAHLEN = (3, 6, 9)
ZUFALL_ALTER = (20, 50)                 # Spanne, wenn kein Alter gewählt ist
MAX_WUENSCHE = 300


def ordner() -> Path:
    import paths
    return paths.ROOT / "Charaktergalerie"


# --- Auswahl prüfen ----------------------------------------------------------------------------------------

def person_pruefen(geschlecht: str, alter) -> tuple[str, int]:
    """Geschlecht und Alter prüfen. ValueError mit Klartext, wenn etwas nicht passt."""
    g = str(geschlecht or "").strip().lower()[:1]
    if g not in GESCHLECHTER:
        raise ValueError("Geschlecht: weiblich, männlich oder nichtbinär.")
    try:
        a = int(alter)
    except (TypeError, ValueError):
        raise ValueError("Das Alter muss eine Zahl sein.") from None
    if a < ALTER_MIN:
        raise ValueError(f"Charaktere sind immer erwachsen – mindestens {ALTER_MIN} Jahre.")
    return g, min(a, ALTER_MAX)


def auswahl(geschlecht: str, alter, eigenschaften) -> tuple[str, int, list[str]]:
    """Geschlecht, Alter, Eigenschaften prüfen. ValueError mit Klartext, wenn etwas nicht passt."""
    g, a = person_pruefen(geschlecht, alter)
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


def name_sauber(name) -> str:
    return re.sub(r"[^\w .'-]", "", " ".join(str(name or "").split())).strip()[:40]


# --- Aussehen ---------------------------------------------------------------------------------------------
# Anzeige (deutsch) → Bild-Prompt (englisch). Ein leeres Feld heißt „egal“: das entscheidet die KI.

HAAR_LAENGEN = {"sehr kurz": "very short", "kurz": "short", "kinnlang": "chin-length",
                "schulterlang": "shoulder-length", "lang": "long", "sehr lang": "very long"}
HAAR_ARTEN = {"glatt": ("straight", ""), "gewellt": ("wavy", ""), "lockig": ("curly", ""), "kraus": ("coily", ""),
              "mit Pony": ("", "with bangs"), "Zopf": ("", "in a braid"), "Pferdeschwanz": ("", "in a ponytail"),
              "Dutt": ("", "in a bun"), "Bob": ("", "in a bob cut"), "Pixie": ("", "in a pixie cut"),
              "Undercut": ("", "with an undercut")}
HAAR_FARBEN = [("Schwarz", "#151515", "black"), ("Dunkelbraun", "#3b2417", "dark brown"),
               ("Braun", "#6b4428", "chestnut brown"), ("Hellbraun", "#9c6f45", "light brown"),
               ("Dunkelblond", "#b08a55", "dark blond"), ("Blond", "#e0c07a", "golden blond"),
               ("Platinblond", "#efe6d2", "platinum blond"), ("Kupfer", "#b5562c", "copper"),
               ("Rot", "#9e1f1f", "deep red"), ("Grau", "#8b8b8b", "grey"), ("Silber", "#c9ccd4", "silver"),
               ("Weiß", "#f5f5f5", "white"), ("Rosa", "#f19ec2", "pastel pink"), ("Pink", "#e0388f", "hot pink"),
               ("Blau", "#2f5fd0", "blue"), ("Türkis", "#2fb8b0", "turquoise"), ("Lila", "#7d4fc0", "purple"),
               ("Grün", "#3c9a58", "green")]
AUGEN_FARBEN = [("Dunkelbraun", "#3a2210", "dark brown"), ("Braun", "#6b3e1e", "brown"),
                ("Haselnuss", "#8e6b3a", "hazel"), ("Bernstein", "#c48a2a", "amber"), ("Grün", "#4f8a4a", "green"),
                ("Blaugrün", "#3f8c8c", "teal"), ("Blau", "#3f74c9", "blue"), ("Hellblau", "#8fb8e8", "light blue"),
                ("Grau", "#8c96a0", "grey"), ("Violett", "#8a5fc0", "violet"), ("Rot", "#b0242a", "red"),
                ("Gold", "#d9b030", "golden")]
BUSEN_GROESSEN = {"klein": "small", "mittel": "medium", "groß": "large", "sehr groß": "very large"}
BUSEN_FORMEN = {"rund": "round", "natürlich": "natural", "tropfenförmig": "teardrop-shaped"}
BRUST = {"schmal": "a narrow chest", "normal": "an average chest", "breit": "a broad chest",
         "muskulös": "a muscular chest"}
TAILLEN = {"sportlich schlank": "a slim athletic waist", "schlank": "a slim waist", "normal": "an average waist",
           "kräftig": "a fuller waist"}
PO_GROESSEN = {"klein": "small", "mittel": "medium", "groß": "large"}
PO_FORMEN = {"rund": "round", "normal": ""}
PO_SPANNUNG = {"durchtrainiert": "toned", "normal": ""}
KOERPERFORMEN = {"Trainiert": "athletic build", "Kurvig": "curvy build", "Zierlich": "petite build",
                 "Voll": "full-figured build", "Normal": "average build"}

AUSWAHL_FELDER = {"haar_laenge": HAAR_LAENGEN, "haar_art": HAAR_ARTEN, "busen_groesse": BUSEN_GROESSEN,
                  "busen_form": BUSEN_FORMEN, "brust": BRUST, "taille": TAILLEN, "po_groesse": PO_GROESSEN,
                  "po_form": PO_FORMEN, "po_spannung": PO_SPANNUNG, "koerperform": KOERPERFORMEN}
FARB_FELDER = {"haar_farbe": HAAR_FARBEN, "augen_farbe": AUGEN_FARBEN}
_NUR_W, _NUR_M = ("busen_groesse", "busen_form"), ("brust",)
_HEX = re.compile(r"#[0-9a-f]{6}")


def farbe_naechste(hexwert: str, tabelle: list[tuple[str, str, str]]) -> tuple[str, str, str]:
    """Der nächste Eintrag der Farbtabelle (gewichteter RGB-Abstand, „redmean“)."""
    def rgb(h: str) -> tuple[int, int, int]:
        return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    r1, g1, b1 = rgb(hexwert)

    def abstand(eintrag) -> float:
        r2, g2, b2 = rgb(eintrag[1])
        rm = (r1 + r2) / 2
        return (2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - rm) / 256) * (b1 - b2) ** 2
    return min(tabelle, key=abstand)


def farbe_anzeige(hexwert: str, tabelle: list[tuple[str, str, str]]) -> str:
    """„Kupfer“ für einen Farbtupfer, „≈ Kupfer“ für eine eigene Farbe, „egal“ ohne Farbe."""
    if not hexwert:
        return "egal"
    de, h, _ = farbe_naechste(hexwert, tabelle)
    return de if h == hexwert.lower() else f"≈ {de}"


def koerperform(a: dict) -> str:
    """Körperform aus Busen/Brust, Taille und Po – leer, solange dazu nichts gewählt ist."""
    t, b, br = a.get("taille", ""), a.get("busen_groesse", ""), a.get("brust", "")
    pg, sp = a.get("po_groesse", ""), a.get("po_spannung", "")
    if not any((t, b, br, pg, sp, a.get("busen_form"), a.get("po_form"))):
        return ""
    gross = (b in ("groß", "sehr groß")) + (pg == "groß")
    klein = (b == "klein") + (pg == "klein") + (br == "schmal")
    if t == "kräftig":
        return "Voll"
    if gross == 2:
        return "Kurvig"
    if br == "muskulös" or sp == "durchtrainiert":
        return "Trainiert"
    if gross and t in ("schlank", "sportlich schlank"):
        return "Kurvig"
    if t == "sportlich schlank":
        return "Trainiert"
    if klein >= 2 or (klein and t == "schlank"):
        return "Zierlich"
    return "Normal"


def aussehen_pruefen(roh, geschlecht: str) -> dict:
    """Nur bekannte Angaben behalten; Busen nur bei w/n, Brust nur bei m/n. Die Körperform wird errechnet,
    außer sie ist ausdrücklich gewählt (`koerperform_fest`)."""
    roh = roh if isinstance(roh, dict) else {}
    a: dict = {}
    for feld, werte in AUSWAHL_FELDER.items():
        if (w := str(roh.get(feld) or "").strip()) in werte:
            a[feld] = w
    for feld in FARB_FELDER:
        if _HEX.fullmatch(w := str(roh.get(feld) or "").strip().lower()):
            a[feld] = w
    g = geschlecht if geschlecht in GESCHLECHTER else "n"
    for feld in (_NUR_W if g == "m" else _NUR_M if g == "w" else ()):
        a.pop(feld, None)
    if roh.get("koerperform_fest") and a.get("koerperform"):
        a["koerperform_fest"] = True
    elif (form := koerperform(a)):
        a["koerperform"] = form
    else:
        a.pop("koerperform", None)
    return a


def _haare(a: dict) -> str:
    vorn, hinten = HAAR_ARTEN.get(a.get("haar_art", ""), ("", ""))
    farbe = farbe_naechste(a["haar_farbe"], HAAR_FARBEN)[2] if a.get("haar_farbe") else ""
    if not (a.get("haar_laenge") or vorn or hinten or farbe):
        return ""
    worte = [HAAR_LAENGEN.get(a.get("haar_laenge", ""), ""), vorn, farbe, "hair", hinten]
    return " ".join(w for w in worte if w)


def kern_aus(a: dict) -> str:
    """Englischer Satzteil für den `kern` aus den Aussehen-Angaben – nur der Körper, keine Kleidung."""
    teile = [_haare(a)]
    if a.get("augen_farbe"):
        teile.append(f"{farbe_naechste(a['augen_farbe'], AUGEN_FARBEN)[2]} eyes")
    busen = " ".join(w for w in (BUSEN_GROESSEN.get(a.get("busen_groesse", ""), ""),
                                 BUSEN_FORMEN.get(a.get("busen_form", ""), "")) if w)
    teile += [f"a {busen} bust" if busen else "", BRUST.get(a.get("brust", ""), ""),
              TAILLEN.get(a.get("taille", ""), "")]
    po = " ".join(w for w in (PO_GROESSEN.get(a.get("po_groesse", ""), ""), PO_FORMEN.get(a.get("po_form", ""), ""),
                              PO_SPANNUNG.get(a.get("po_spannung", ""), "")) if w)
    teile += [f"a {po} butt" if po else "", KOERPERFORMEN.get(a.get("koerperform", ""), "")]
    return ", ".join(t for t in teile if t)


def aussehen_text(a: dict) -> str:
    """Die Angaben deutsch für den Auftrag an die KI."""
    teile = []
    haar = [a.get("haar_laenge", ""), a.get("haar_art", "")]
    if a.get("haar_farbe"):
        haar.append(farbe_anzeige(a["haar_farbe"], HAAR_FARBEN))
    if any(haar):
        teile.append("Haare " + " ".join(h for h in haar if h))
    if a.get("augen_farbe"):
        teile.append("Augen " + farbe_anzeige(a["augen_farbe"], AUGEN_FARBEN))
    for titel, felder in (("Busen", ("busen_groesse", "busen_form")), ("Brust", ("brust",)), ("Taille", ("taille",)),
                          ("Po", ("po_groesse", "po_form", "po_spannung")), ("Körperform", ("koerperform",))):
        if (werte := [a[f] for f in felder if a.get(f)]):
            teile.append(f"{titel} {' '.join(werte)}")
    return "; ".join(teile)


def kern_neu(kern: str, alt: dict | None, neu: dict | None) -> str:
    """Den aus `aussehen` gebauten Teil im `kern` gegen die neuen Angaben tauschen; der Rest (Gesicht …) bleibt."""
    kern = " ".join(str(kern or "").split())
    a, n = kern_aus(alt or {}), kern_aus(neu or {})
    if a and a in kern:
        kern = kern.replace(a, n, 1)
    elif n:
        m = re.match(r"(?i)an? adult .*? years old,\s*", kern)
        stelle = m.end() if m else 0
        rest = kern[stelle:]
        kern = kern[:stelle] + n + (f", {rest}" if rest else "")
    return re.sub(r"\s*,(\s*,)+\s*", ", ", kern).strip(" ,")


# --- Porträt ----------------------------------------------------------------------------------------------

def portraet_prompt(geschlecht: str, alter: int, kern: str, stil: str = "", outfit: str = "") -> str:
    """Englischer Krea-Prompt: genau eine erwachsene Person, Brustbild, Aussehen aus `kern`."""
    wort = GESCHLECHTER.get(geschlecht, GESCHLECHTER["n"])[1]
    bild_alter = max(int(alter), BILD_ALTER_MIN)
    kern = " ".join(str(kern or "").split())[:600].rstrip(".")
    outfit = " ".join(str(outfit or "").split())[:300].rstrip(".")
    return (f"Portrait of a single adult {wort}, around {bild_alter} years old, alone, upper body, looking at the "
            f"viewer. {kern[:1].upper() + kern[1:]}. " + (f"Wearing {outfit}. " if outfit else "")
            + f"{(stil or STIL_STANDARD).strip().rstrip('.')}.")


SEED_MAX = 2 ** 31 - 1


def neuer_seed() -> int:
    return random.randint(1, SEED_MAX)


def seed_lesen(wert) -> int | None:
    """Eingetippter Seed -> Zahl (1 bis SEED_MAX); leer -> None. Sonst ValueError mit verständlichem Grund."""
    text = str(wert if wert is not None else "").strip().replace(".", "").replace(" ", "")
    if not text:
        return None
    if not text.isdigit() or not 1 <= int(text) <= SEED_MAX:
        raise ValueError(f"Der Seed ist eine ganze Zahl von 1 bis {SEED_MAX:,}.".replace(",", "."))
    return int(text)


def portraet_von(name: str) -> str:
    """Das neueste gespeicherte Porträt einer Persönlichkeit (Profile/<Name>/Bilder/portraet_*.png) oder ""."""
    import profilordner
    try:
        bilder = sorted(profilordner.ordner("Bilder", name, anlegen=False).glob("portraet_*.png"))
    except OSError:
        return ""
    return str(bilder[-1]) if bilder else ""


# --- KI: Vorschläge und Verbessern ----------------------------------------------------------------------------

VORSCHLAG_SYSTEM = (
    "Du erfindest Charaktere für eine KI-Persönlichkeit. Antworte NUR mit JSON: einer Liste von Objekten mit "
    "name (ein Vorname, passend zum Charakter), kurz (deutsch, 4–10 Worte, wie die Person ist), wesen (deutsch, "
    "2–4 Sätze: Art zu reden, Eigenheiten, Vorlieben), geschichte (deutsch, 2–4 Sätze: Herkunft, Werdegang, was "
    "die Person erlebt hat), ton (locker|sachlich|foermlich|verspielt), kern (ENGLISCH, "
    "ein Satz: festes Aussehen ohne Alter, ohne Geschlechtswort am Anfang und ohne Kleidung – Haare, Augen mit "
    "Farbe, Gesicht, Haut, Körper), outfit (ENGLISCH, kurz: Kleidung passend zum Charakter, oben und unten), stil "
    "(ENGLISCH, kurz: Bildart, Licht und Kamera, z. B. portrait photograph, soft window light, 85mm lens). "
    "Jeder Vorschlag deutlich anders. Immer erwachsen. Kein Text außerhalb des JSON.")


def vorschlag_auftrag(geschlecht: str, alter: int, eigenschaften: list[str], anzahl: int = ANZAHL_VORSCHLAEGE,
                      aussehen: dict | None = None, idee: str = "") -> str:
    fest = aussehen_text(aussehen or {})
    idee = " ".join(str(idee or "").split())[:MAX_IDEE]
    return (f"Erfinde {anzahl} verschiedene Charaktere. Geschlecht: {GESCHLECHTER[geschlecht][0]}. Alter: {alter} "
            f"Jahre. Eigenschaften: {', '.join(eigenschaften)}."
            + (f" Das Aussehen steht fest und gilt für alle: {fest}. Der kern beschreibt nur, was noch fehlt (Gesicht, "
               "Haut, Ausstrahlung), und widerspricht diesen Angaben nicht." if fest else "")
            + (f" Eigene Wünsche, bei allen berücksichtigen: {idee}." if idee else "")
            + " Nur die JSON-Liste.")


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
                      anzahl: int = ANZAHL_VORSCHLAEGE, aussehen: dict | None = None,
                      frei: dict | None = None) -> list[dict]:
    """KI-Antwort → geprüfte Entwürfe (mit Seed), höchstens `anzahl`. Gewähltes Aussehen steht vorn im `kern`;
    `frei`: der Steckbrief des freien Wegs geht mit in den Entwurf."""
    aussehen = aussehen or {}
    fest = kern_aus(aussehen)
    raus = []
    for d in _json_liste(text):
        if not isinstance(d, dict):
            continue
        name = name_sauber(_feld(d, "name", 40))
        kern = ", ".join(t for t in (fest, _feld(d, "kern", 500).rstrip(".")) if t)
        if not name or not kern:
            continue
        wesen = _feld(d, "wesen", 700)
        entwurf = {"name": name, "kurz": _feld(d, "kurz", 120) or ", ".join(eigenschaften[:3]) or wesen[:80],
                   "wesen": wesen, "geschichte": _feld(d, "geschichte", 700),
                   "ton": ton_aus(eigenschaften, _feld(d, "ton", 12)),
                   "kern": kern, "outfit": _feld(d, "outfit", 300), "stil": _feld(d, "stil", 200) or STIL_STANDARD,
                   "geschlecht": geschlecht, "alter": alter, "eigenschaften": list(eigenschaften),
                   "aussehen": dict(aussehen), "seed": neuer_seed()}
        if frei:
            entwurf["frei"] = steckbrief_pruefen(frei)
        raus.append(entwurf)
        if len(raus) >= anzahl:
            break
    if not raus:
        raise ValueError("Die KI hat keinen brauchbaren Vorschlag geliefert – noch einmal versuchen.")
    return raus


# --- KI: Zufall ----------------------------------------------------------------------------------------------

ZUFALL_SYSTEM = (
    "Du erfindest Charaktere für eine KI-Persönlichkeit. Antworte NUR mit JSON: einer Liste mit einem Objekt je "
    "Platz, in derselben Reihenfolge, mit name (ein Vorname, frei gewählt aus beliebigen Sprachen und Kulturen, alle "
    "verschieden), kurz (deutsch, 4–10 Worte, wie die Person ist), wesen (deutsch, 2–4 Sätze: Art zu reden, "
    "Eigenheiten, Vorlieben), geschichte (deutsch, 2–4 Sätze: Herkunft, Werdegang, was die Person erlebt hat), "
    "eigenschaften (2–4 Wörter aus der vorgegebenen Liste), ton (locker|sachlich|foermlich|verspielt), kern "
    "(ENGLISCH, ein Satz: festes Aussehen ohne Alter, ohne Geschlechtswort am Anfang und ohne Kleidung – Haare, "
    "Augen mit Farbe, Gesicht, Haut, Körper), outfit (ENGLISCH, kurz: Kleidung passend zum Charakter, oben und "
    "unten), stil (ENGLISCH, kurz: Bildart, Licht und Kamera, z. B. portrait photograph, soft window light, 85mm "
    "lens). Geschlecht und Alter je Platz sind verbindlich. Jeder Charakter deutlich anders in Name, "
    "Aussehen, Wesen und Geschichte. Immer erwachsen. Kein Text außerhalb des JSON.")


def _zahl_oder_leer(wert):
    return None if wert is None or str(wert).strip() == "" else wert


def zufall_pruefen(wer, anzahl, alter_von=None, alter_bis=None, wuensche="") -> dict:
    """Auswahl für den Zufall: {wer, anzahl, von, bis, wuensche}. Ohne Alter gilt ZUFALL_ALTER; ein einzelnes Alter
    gilt für alle. ValueError mit Klartext, wenn etwas nicht passt."""
    wer = str(wer or "").strip().lower()
    if wer not in ZUFALL_WER:
        raise ValueError("Wähl, wen die KI erfinden soll: Frauen, Männer, Nichtbinär oder Gemischt.")
    try:
        anzahl = int(anzahl)
    except (TypeError, ValueError):
        anzahl = 0
    if anzahl not in ZUFALL_ANZAHLEN:
        raise ValueError("Wie viele? 3, 6 oder 9.")
    von, bis = _zahl_oder_leer(alter_von), _zahl_oder_leer(alter_bis)
    if von is None and bis is None:
        von, bis = ZUFALL_ALTER
    else:
        try:
            von, bis = int(von if von is not None else bis), int(bis if bis is not None else von)
        except (TypeError, ValueError):
            raise ValueError("Das Alter muss eine Zahl sein.") from None
        von, bis = min(von, bis), max(von, bis)
        if von < ALTER_MIN:
            raise ValueError(f"Charaktere sind immer erwachsen – mindestens {ALTER_MIN} Jahre.")
        von, bis = min(von, ALTER_MAX), min(bis, ALTER_MAX)
    return {"wer": wer, "anzahl": anzahl, "von": von, "bis": bis,
            "wuensche": " ".join(str(wuensche or "").split())[:MAX_WUENSCHE]}


def zufall_plaetze(auswahl: dict, rnd: random.Random | None = None) -> list[dict]:
    """Geschlecht und Alter je Platz. Gemischt: reihum verteilt, dann gemischt; das Alter zufällig in der Spanne."""
    rnd = rnd or random.Random()
    n = auswahl["anzahl"]
    if auswahl["wer"] == "gemischt":
        arten = [("w", "m", "n")[i % 3] for i in range(n)]
        rnd.shuffle(arten)
    else:
        arten = [auswahl["wer"]] * n
    return [{"geschlecht": g, "alter": rnd.randint(auswahl["von"], auswahl["bis"])} for g in arten]


def zufall_auftrag(plaetze: list[dict], wuensche: str = "", vergeben: list[str] | None = None) -> str:
    zeilen = [f"{i}. {GESCHLECHTER[p['geschlecht']][0]}, {p['alter']} Jahre" for i, p in enumerate(plaetze, 1)]
    alle = ", ".join(e for liste in KATEGORIEN.values() for e in liste)
    wuensche = " ".join(str(wuensche or "").split())[:MAX_WUENSCHE]
    return (f"Erfinde {len(plaetze)} Charaktere, einen je Platz:\n" + "\n".join(zeilen)
            + f"\nEigenschaften zur Auswahl: {alle}."
            + (f"\nWünsche, bei allen berücksichtigen: {wuensche}." if wuensche else "")
            + (f"\nDiese Namen sind schon vergeben: {', '.join((vergeben or [])[:40])}." if vergeben else "")
            + "\nNur die JSON-Liste.")


def zufall_lesen(text: str, plaetze: list[dict], vergeben: list[str] | None = None) -> list[dict]:
    """KI-Antwort → geprüfte Entwürfe (mit Seed), höchstens einer je Platz. Geschlecht und Alter kommen vom Platz,
    nie aus der Antwort; vergebene oder doppelte Namen fallen weg."""
    alle = {e for liste in KATEGORIEN.values() for e in liste}
    gesehen = {str(n).casefold() for n in vergeben or []}
    raus = []
    for d in _json_liste(text):
        if len(raus) >= len(plaetze):
            break
        if not isinstance(d, dict):
            continue
        name, kern = name_sauber(_feld(d, "name", 40)), _feld(d, "kern", 500).rstrip(".")
        if not name or not kern or name.casefold() in gesehen:
            continue
        gesehen.add(name.casefold())
        platz = plaetze[len(raus)]
        roh = d.get("eigenschaften") if isinstance(d.get("eigenschaften"), list) else []
        eig = ([e for e in dict.fromkeys(str(x) for x in roh) if e in alle][:MAX_EIGENSCHAFTEN]
               or [random.choice(KATEGORIEN["Wesen"])])
        raus.append({"name": name, "kurz": _feld(d, "kurz", 120) or ", ".join(eig[:3]), "wesen": _feld(d, "wesen", 700),
                     "geschichte": _feld(d, "geschichte", 700), "ton": ton_aus(eig, _feld(d, "ton", 12)),
                     "kern": kern, "outfit": _feld(d, "outfit", 300), "stil": _feld(d, "stil", 200) or STIL_STANDARD,
                     "geschlecht": platz["geschlecht"], "alter": platz["alter"], "eigenschaften": eig,
                     "aussehen": {}, "seed": neuer_seed(), "zufall": True})
    if not raus:
        raise ValueError("Die KI hat keinen brauchbaren Charakter geliefert – noch einmal versuchen.")
    return raus


FELDER = {"kern": ("Aussehen", "ENGLISCH, ein Satz: Haare, Augen, Gesicht, Körper – ohne Alter, ohne Kleidung"),
          "outfit": ("Kleidung", "ENGLISCH, kurz: was die Figur trägt"),
          "stil": ("Bildstil", "ENGLISCH, kurz: Licht, Stimmung, Art des Bildes"),
          "wesen": ("Wesen", "deutsch, 2–4 Sätze: wie die Persönlichkeit redet und was sie ausmacht"),
          "geschichte": ("Geschichte", "deutsch, 2–4 Sätze: Herkunft, Werdegang, was die Person erlebt hat"),
          "kurz": ("Kurzbeschreibung", "deutsch, 4–10 Worte")}
VERBESSERN_SYSTEM = ("Du hilfst, einen Charakter auszufeilen. Antworte NUR mit dem verbesserten Text des Feldes – "
                     "keine Anführungszeichen, keine Erklärung. Immer erwachsen.")


def verbessern_auftrag(feld: str, text: str, entwurf: dict, wunsch: str = "") -> str:
    if feld not in FELDER:
        raise ValueError("Unbekanntes Feld.")
    titel, regel = FELDER[feld]
    rest = {k: entwurf.get(k) for k in ("name", "kurz", "wesen", "geschichte", "kern", "outfit", "stil")
            if k != feld and entwurf.get(k)}
    return (f"Charakter: {GESCHLECHTER.get(entwurf.get('geschlecht', 'n'), GESCHLECHTER['n'])[0]}, "
            f"{entwurf.get('alter', '?')} Jahre, Eigenschaften: {', '.join(entwurf.get('eigenschaften') or [])}. "
            f"Übrige Felder: {json.dumps(rest, ensure_ascii=False)}.\n"
            f"Verbessere das Feld „{titel}“ ({regel}): lebendiger, genauer, passend zum Rest."
            + (f" Wunsch: {wunsch.strip()[:300]}." if wunsch.strip() else "")
            + f"\nBisher: {text.strip()[:800] or '(leer)'}")


def verbessert_lesen(text: str, feld: str) -> str:
    t = re.sub(r"(?is)<think>.*?</think>", "", text or "").strip().strip('"„“').strip()
    return " ".join(t.split())[:700 if feld == "wesen" else 500]


ANZAHL_NAMEN = 6
NAMEN_SYSTEM = (
    "Du erfindest Vornamen für einen Charakter. Antworte NUR mit JSON: einer Liste von 6 Objekten mit name (ein "
    "Vorname), art (passend|frei) und warum (deutsch, 3–8 Worte). Die ersten 3 passen zu Wesen und Aussehen, die "
    "letzten 3 sind frei erfunden – überraschend, aus beliebigen Sprachen und Kulturen. Alle verschieden. Kein Text "
    "außerhalb des JSON.")


def namen_auftrag(entwurf: dict, bisher: list[str] | None = None) -> str:
    g = GESCHLECHTER.get(entwurf.get("geschlecht", "n"), GESCHLECHTER["n"])[0]
    teile = [f"Charakter: {g}, {entwurf.get('alter', '?')} Jahre",
             f"Eigenschaften: {', '.join(entwurf.get('eigenschaften') or [])}"]
    teile += [f"{titel}: {str(entwurf[k])[:500]}" for k, titel in
              (("kurz", "Kurz"), ("wesen", "Wesen"), ("kern", "Aussehen")) if entwurf.get(k)]
    if bisher:
        teile.append(f"Nicht noch einmal: {', '.join(bisher[-30:])}")
    return ". ".join(teile) + ". Nur die JSON-Liste."


def namen_lesen(text: str, bisher: list[str] | None = None) -> list[dict]:
    """KI-Antwort → bis zu 6 Namen {name, art, warum}; doppelte und schon gezeigte fallen weg."""
    gesehen = {n.casefold() for n in bisher or []}
    raus = []
    for d in _json_liste(text):
        d = d if isinstance(d, dict) else {"name": d}
        name = name_sauber(_feld(d, "name", 40))
        if not re.search(r"[^\W\d_]", name) or re.search(r"\d", name) or name.casefold() in gesehen:
            continue
        gesehen.add(name.casefold())
        raus.append({"name": name, "art": "frei" if _feld(d, "art", 10) == "frei" else "passend",
                     "warum": _feld(d, "warum", 80)})
        if len(raus) >= ANZAHL_NAMEN:
            break
    if not raus:
        raise ValueError("Die KI hat keine neuen Namen geliefert – noch einmal 🎲 oder selbst einen eintippen.")
    return raus


# --- KI: Charakter im Gespräch gestalten -------------------------------------------------------------------
# Die KI antwortet und nennt nur die Felder, um die es ging; dieselbe Prüfung wie beim Klicken. Der Stand ist
# {geschlecht, alter, aussehen, eigenschaften, idee}.

def _erlaubt() -> str:
    zeilen = ["geschlecht: w | m | n", f"alter: Zahl ab {ALTER_MIN}"]
    zeilen += [f"{feld}: {' | '.join(werte)}" for feld, werte in AUSWAHL_FELDER.items()]
    zeilen += [f"{feld}: Hex-Farbe wie #a83c8f" for feld in FARB_FELDER]
    zeilen += [f"eigenschaften: Liste, höchstens {MAX_EIGENSCHAFTEN}, nur aus: "
               + ", ".join(e for liste in KATEGORIEN.values() for e in liste),
               f"idee: deutsch, frei, höchstens {MAX_IDEE} Zeichen – alles, wofür es kein Feld gibt"]
    return "\n".join(zeilen)


TON = ("Du bist eine kreative Mitgestalterin mit viel Fantasie – locker, herzlich und schlau, wie eine gute "
       "Freundin, mit der man zusammen an einer Figur tüftelt. Du duzt, redest natürlich, gern mit etwas Humor und "
       "passenden Emojis, nie steif, förmlich oder nach Formular. Du denkst mit: greifst Ideen auf, spinnst sie "
       "weiter, machst eigene überraschende Vorschläge und fragst höchstens eine Sache auf einmal. Deine Worte "
       "wählst du selbst, jedes Mal neu.")

EDITOR_SYSTEM = (
    TON + " Die Person erzählt, wie sie sich ihren Charakter vorstellt. Antworte NUR mit JSON: "
    '{"antwort": "...", "felder": {...}}. antwort: deutsch, kurz. felder: nur, was die Person gerade gesagt oder '
    "bestätigt hat – alles andere bleibt, wie es ist. Leerer Text = egal. Der Charakter ist erwachsen. Erlaubte "
    "Felder und Werte:\n" + _erlaubt())


def editor_auftrag(verlauf: list[dict], text: str, stand: dict) -> str:
    zeilen = [f"{'KI' if v.get('wer') == 'ki' else 'Person'}: {str(v.get('text') or '')[:600]}"
              for v in (verlauf or [])[-16:] if isinstance(v, dict)]
    return (f"Stand: {json.dumps(stand or {}, ensure_ascii=False)}\n"
            + ("Bisher:\n" + "\n".join(zeilen) + "\n" if zeilen else "")
            + f"Person: {str(text or '').strip()[:800]}\nNur das JSON-Objekt.")


def _geschlecht_aus(wert) -> str:
    w = str(wert or "").strip().lower()
    if w.startswith(("w", "f")):
        return "w"
    if w.startswith("m"):
        return "m"
    return "n" if w.startswith(("n", "d", "a")) else ""


def editor_aenderungen(felder) -> dict:
    """Die Felder der KI prüfen: nur bekannte Werte, Alter ab 18, „egal“ als leerer Text."""
    felder = felder if isinstance(felder, dict) else {}
    raus: dict = {}
    if (g := _geschlecht_aus(felder.get("geschlecht"))):
        raus["geschlecht"] = g
    try:
        if felder.get("alter") not in (None, "") and int(felder["alter"]) >= ALTER_MIN:
            raus["alter"] = min(int(felder["alter"]), ALTER_MAX)
    except (TypeError, ValueError):
        pass
    quelle = {**felder, **(felder["aussehen"] if isinstance(felder.get("aussehen"), dict) else {})}
    aussehen = {}
    for feld, werte in AUSWAHL_FELDER.items():
        if feld in quelle:
            w = str(quelle[feld] or "").strip()
            if not w or w in werte:
                aussehen[feld] = w
    for feld in FARB_FELDER:
        if feld in quelle:
            w = str(quelle[feld] or "").strip().lower()
            if not w or _HEX.fullmatch(w):
                aussehen[feld] = w
    if aussehen:
        raus["aussehen"] = aussehen
    if isinstance(felder.get("eigenschaften"), list):
        alle = {e for liste in KATEGORIEN.values() for e in liste}
        raus["eigenschaften"] = [e for e in dict.fromkeys(str(x) for x in felder["eigenschaften"])
                                 if e in alle][:MAX_EIGENSCHAFTEN]
    if "idee" in felder:
        raus["idee"] = " ".join(str(felder["idee"] or "").split())[:MAX_IDEE]
    return raus


def editor_lesen(text: str) -> tuple[str, dict]:
    """KI-Antwort → (Antworttext, geprüfte Änderungen). Ohne JSON zählt der Text allein."""
    t = re.sub(r"(?is)<think>.*?</think>", "", text or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return " ".join(t.split())[:600] or "…", {}
    return _feld(d, "antwort", 600) or "✔", editor_aenderungen(d.get("felder"))


def stand_anwenden(stand: dict, aenderungen: dict) -> dict:
    """Änderungen auf den Stand legen; ein leerer Wert beim Aussehen heißt „egal“."""
    neu = {**(stand or {}), "aussehen": dict((stand or {}).get("aussehen") or {}),
           "eigenschaften": list((stand or {}).get("eigenschaften") or [])}
    for k in ("geschlecht", "alter", "eigenschaften", "idee"):
        if k in aenderungen:
            neu[k] = aenderungen[k]
    a = neu["aussehen"]
    for k, v in (aenderungen.get("aussehen") or {}).items():
        if v:
            a[k] = v
            if k == "koerperform":
                a["koerperform_fest"] = True
        else:
            a.pop(k, None)
            if k == "koerperform":
                a.pop("koerperform_fest", None)
    neu["aussehen"] = aussehen_pruefen(a, neu.get("geschlecht") or "n")
    return neu


# --- Freier Weg: Steckbrief in eigenen Worten ---------------------------------------------------------------
# Keine Auswahllisten: drei Abschnitte freier Text, die Person und die KI schreiben beide darin.

STECKBRIEF = {"aussehen": ("💇 Aussehen", "Körper, Gesicht, Haare, Augen, Besonderheiten – ohne Kleidung"),
              "wesen": ("💭 Wesen", "wie der Charakter ist, redet und fühlt"),
              "eigenheiten": ("✨ Eigenheiten & Stil", "Kleidung, Vorlieben, Macken, alles andere")}
MAX_ABSCHNITT = 1200

FREI_SYSTEM = (
    TON + " Gemeinsam mit der Person entsteht ein Charakter ganz frei, ohne Auswahllisten. Du führst seinen "
    "Steckbrief in drei Abschnitten: " + "; ".join(f"{k} ({v[1]})" for k, v in STECKBRIEF.items()) + ". Antworte "
    'NUR mit JSON: {"antwort": "...", "felder": {...}}. antwort: deutsch, kurz. felder: nur geänderte Abschnitte, '
    "jeweils der VOLLSTÄNDIGE neue Text des Abschnitts – Bisheriges übernehmen, Neues einarbeiten, nichts "
    "weglassen, was die Person gesagt oder selbst eingetragen hat; deutsch, lebendig, ohne Listen, höchstens "
    f"{MAX_ABSCHNITT} Zeichen. Dazu geschlecht (w | m | n) und alter (Zahl ab {ALTER_MIN}), sobald sie feststehen. "
    "Der Charakter ist erwachsen.")


def steckbrief_pruefen(roh) -> dict:
    roh = roh if isinstance(roh, dict) else {}
    return {k: " ".join(str(roh.get(k) or "").split())[:MAX_ABSCHNITT] for k in STECKBRIEF if roh.get(k)}


def frei_auftrag(verlauf: list[dict], text: str, stand: dict) -> str:
    return editor_auftrag(verlauf, text, stand)


def frei_lesen(text: str) -> tuple[str, dict]:
    """KI-Antwort im freien Weg → (Antworttext, Änderungen: Abschnitte, geschlecht, alter)."""
    t = re.sub(r"(?is)<think>.*?</think>", "", text or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return " ".join(t.split())[:600] or "…", {}
    felder = d.get("felder") if isinstance(d.get("felder"), dict) else {}
    aend = {k: v for k, v in editor_aenderungen(felder).items() if k in ("geschlecht", "alter")}
    aend.update(steckbrief_pruefen(felder))
    return _feld(d, "antwort", 600) or "✔", aend


def frei_varianten_auftrag(geschlecht: str, alter: int, steckbrief: dict,
                           anzahl: int = ANZAHL_VORSCHLAEGE) -> str:
    teile = [f"{STECKBRIEF[k][0].split(' ', 1)[1]}: {v}" for k, v in steckbrief_pruefen(steckbrief).items()]
    return (f"Erfinde {anzahl} Varianten EINES Charakters. Geschlecht: {GESCHLECHTER[geschlecht][0]}. Alter: "
            f"{alter} Jahre. Die Beschreibung der Person ist verbindlich: Alles, was sie nennt, gilt für jede "
            "Variante genau so – das Aussehen gehört ins kern (englisch), die Kleidung ins outfit. Verschieden ist nur, "
            "was offen bleibt: Gesichtszüge, Ausdruck, Details, Stimmung, Nuancen im Wesen.\n"
            + "\n".join(teile) + "\nNur die JSON-Liste.")


# --- Übernehmen ins Profil ------------------------------------------------------------------------------------

def charakter_daten(entwurf: dict) -> dict:
    """Die Charakter-Datei (tools/charakter.py) aus einem Entwurf: Aussehen, fester Seed, Stil, Regeln."""
    g = entwurf.get("geschlecht", "n")
    wort = GESCHLECHTER.get(g, GESCHLECHTER["n"])[1]
    bild_alter = max(int(entwurf.get("alter") or BILD_ALTER_MIN), BILD_ALTER_MIN)
    outfit = " ".join(str(entwurf.get("outfit") or "").split()).rstrip(".")
    d = {"name": entwurf["name"],
         "kern": f"an adult {wort} around {bild_alter} years old, {entwurf['kern'].rstrip('.')}",
         "seed_referenz": seed_lesen(entwurf.get("seed")) or neuer_seed(), "stil": entwurf.get("stil") or STIL_STANDARD,
         "outfits": {"standard": outfit} if outfit else {}, "posen": {},
         "regeln": [f"Always one single adult {wort}, never younger than {BILD_ALTER_MIN}."]}
    if (aussehen := aussehen_pruefen(entwurf.get("aussehen"), g)):
        d["aussehen"] = {**aussehen, "geschlecht": g}
    if (steckbrief := steckbrief_pruefen(entwurf.get("frei"))):
        d["steckbrief"] = steckbrief                    # der freie Weg: die Beschreibung in eigenen Worten
    return d


def uebernehmen(entwurf: dict, bild: str | None = None) -> dict:
    """Persönlichkeit + Charakter-Datei + Porträt anlegen. Gibt {key, name, pfad, charakter, bild} zurück.
    Aktiv setzen und Profilbild wählen macht der Aufrufer (die Sitzung)."""
    import charakter
    import persoenlichkeiten as PS
    import profilordner
    name = name_sauber(entwurf.get("name"))
    if not name or not str(entwurf.get("kern") or "").strip():
        raise ValueError("Name und Aussehen fehlen.")
    g = entwurf.get("geschlecht") if entwurf.get("geschlecht") in GESCHLECHTER else "n"
    frei = steckbrief_pruefen(entwurf.get("frei"))
    if frei:                                        # freier Weg: keine Eigenschaften aus der Liste
        (g, alter), eigenschaften = person_pruefen(g, entwurf.get("alter")), []
    else:
        g, alter, eigenschaften = auswahl(g, entwurf.get("alter"), entwurf.get("eigenschaften") or ["ruhig"])
    entwurf = {**entwurf, "name": name, "geschlecht": g, "alter": alter, "eigenschaften": eigenschaften}
    pfad = PS.create(name, str(entwurf.get("kurz") or ""), g, ton_aus(eigenschaften, str(entwurf.get("ton") or "")))
    p = PS.list_all().get(pfad.stem)
    if p is None:
        raise RuntimeError("Die Persönlichkeit wurde angelegt, lässt sich aber nicht lesen.")
    p_name = p.name
    for zeile in (f"- Du bist {alter} Jahre alt (erwachsen).",
                  *([f"- Deine Eigenschaften: {', '.join(eigenschaften)}."] if eigenschaften else []),
                  *([f"- {entwurf['wesen']}"] if entwurf.get("wesen") else []),
                  *([f"- Deine Geschichte: {str(entwurf['geschichte'])[:700]}"] if entwurf.get("geschichte") else []),
                  *([f"- Eigenheiten & Stil: {frei['eigenheiten'][:500]}"] if frei.get("eigenheiten") else [])):
        PS.add_line(p, zeile)
    datei = charakter.schreiben(charakter._sauber(charakter_daten({**entwurf, "name": p_name})), p_name)
    ziel_bild = None
    if bild and Path(bild).is_file():
        ziel_bild = profilordner.ordner("Bilder", p_name) / f"portraet_{time.strftime('%Y%m%d_%H%M%S')}.png"
        shutil.copy2(bild, ziel_bild)
    return {"key": p.key, "name": p_name, "pfad": str(pfad), "charakter": str(datei),
            "bild": str(ziel_bild) if ziel_bild else ""}


def bearbeiten_laden(name: str) -> dict | None:
    """Die Charakter-Datei einer Persönlichkeit zum Bearbeiten (Aussehen, Stil, Seed, Angaben zum Aussehen)."""
    import charakter
    d = charakter.roh(name)
    if not d.get("kern"):
        return None
    aussehen = d.get("aussehen") if isinstance(d.get("aussehen"), dict) else {}
    g = aussehen.get("geschlecht") if aussehen.get("geschlecht") in GESCHLECHTER else "n"
    return {"name": d.get("name") or name, "kern": d.get("kern", ""), "stil": d.get("stil", ""),
            "seed": d.get("seed_referenz"), "geschlecht": g, "aussehen": aussehen_pruefen(aussehen, g)}


def bearbeitung_speichern(name: str, kern: str, stil: str, seed=None, aussehen: dict | None = None) -> Path:
    """Aussehen und Stil der Charakter-Datei ändern – alles andere (Outfits, Posen, Stimme) bleibt; der alte Stand
    geht in den Papierkorb."""
    import charakter
    kern = " ".join(str(kern or "").split())
    if not kern:
        raise ValueError("Das Aussehen darf nicht leer sein.")
    d = charakter.roh(name)
    d.update({"name": d.get("name") or name, "kern": kern, "stil": " ".join(str(stil or "").split())})
    if (wert := seed_lesen(seed)) is not None:
        d["seed_referenz"] = wert
    if isinstance(aussehen, dict):
        alt = d.get("aussehen") if isinstance(d.get("aussehen"), dict) else {}
        g = next((x for x in (aussehen.get("geschlecht"), alt.get("geschlecht")) if x in GESCHLECHTER), "n")
        d["aussehen"] = {**aussehen_pruefen(aussehen, g), "geschlecht": g}
    neu = charakter._sauber(d)
    if not neu.get("seed_referenz"):
        neu["seed_referenz"] = neuer_seed()
    return charakter.schreiben(neu, name, "charakter_galerie")
