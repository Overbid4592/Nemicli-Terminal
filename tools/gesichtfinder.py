"""gesichtfinder.py – Gesichter für den Kern finden (/kern kopf, Kopf-Fenster).

Die KI macht aus Wünschen (deutsch) mehrere Gesichts-Varianten: Jede ist der ganze Kern (englisch), in dem nur
das Gesicht anders ist – Gesichtsform, Augen, Nase, Mund, Haut im Gesicht. Haare und Körper bleiben Wort für Wort.
Jede Variante bekommt einen eigenen Seed und wird als Nahaufnahme gemalt. Übernommen werden Kern und Seed:
derselbe Text mit demselben Seed ergibt dasselbe Gesicht.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

ANZAHL = 6
MAX_WUNSCH = 400
MAX_KERN = 700
NACHGEBESSERT = "Gesichtszuege"      # Unterordner der Gesichts-Korrektur (krea.GESICHTSZUEGE)

SYSTEM = (
    "Du entwirfst Gesichter für eine Bild-KI. Du bekommst den Kern einer erwachsenen Figur (englisch: ihr festes "
    "Aussehen) und Wünsche (deutsch). Antworte NUR mit JSON: einer Liste von Objekten mit titel (deutsch, 2–5 Worte, "
    "was dieses Gesicht ausmacht) und kern (ENGLISCH, der ganze Kern neu). In jedem kern änderst oder ergänzt du NUR "
    "das Gesicht: Gesichtsform, Wangenknochen, Kinn, Kieferlinie, Stirn, Augenform und -stellung, Augenbrauen, "
    "Wimpern, Nase, Lippen, Mund, Haut im Gesicht (Sommersprossen, Muttermale, Grübchen). Alles über Haare (Farbe, "
    "Länge, Art) übernimmst du Wort für Wort, ebenso Körper, Haut und Bleibendes wie Tattoos. Die Augenfarbe bleibt, "
    "außer die Wünsche nennen eine andere. Keine Kleidung, keine Frisur, kein Make-up, kein Alter, immer erwachsen. "
    "Beschreibe jedes Gesicht konkret und bildhaft (z. B. „a heart-shaped face with high cheekbones, wide-set almond "
    "eyes, a small upturned nose and full lips“). Jede Variante deutlich anders. Kein Text außerhalb des JSON.")

_STOPP = {"with", "and", "a", "an", "the", "has", "her", "his", "their", "of", "in", "she", "he", "wears"}
_HAAR = re.compile(r"(?i)\b(hair|bangs|fringe)\b")
_WORT = re.compile(r"[\w'-]+")


def haar_teile(kern: str) -> list[str]:
    """Die Haar-Angaben im Kern („long red hair“): je Vorkommen von hair/bangs/fringe die Wörter davor bis zu
    einem Füllwort oder Satzzeichen. Klein geschrieben, ohne Doppelte."""
    raus: list[str] = []
    for m in _HAAR.finditer(kern or ""):
        vorne = re.split(r"[,.;:()]", kern[:m.start()])[-1]
        woerter = _WORT.findall(vorne)
        teil = []
        for w in reversed(woerter[-6:]):
            if w.lower() in _STOPP:
                break
            teil.insert(0, w)
        t = " ".join([*teil, m.group(1)]).lower()
        if t not in raus:
            raus.append(t)
    return raus


def _norm(text: str) -> str:
    return " ".join(_WORT.findall(str(text or "").lower()))


def haare_gleich(alt: str, neu: str) -> bool:
    """Stehen alle Haar-Angaben des alten Kerns unverändert im neuen – und keine anderen dazu?"""
    a, n = haar_teile(alt), haar_teile(neu)
    return sorted(a) == sorted(n) and all(_norm(t) in _norm(neu) for t in a)


def auftrag(kern: str, wunsch: str, anzahl: int = ANZAHL, bisher: list[str] | None = None) -> str:
    wunsch = " ".join(str(wunsch or "").split())[:MAX_WUNSCH]
    teile = [f"Kern jetzt: {kern}",
             f"Wünsche: {wunsch}" if wunsch else "Wünsche: keine – überrasche mit sehr unterschiedlichen Gesichtern.",
             f"Liefere genau {anzahl} Varianten."]
    if bisher:
        teile.append("Schon gezeigt (diesmal andere Gesichter): " + " | ".join(bisher[-12:]))
    return "\n".join(teile)


def lesen(text: str, kern_alt: str, anzahl: int = ANZAHL) -> list[dict]:
    """KI-Antwort → geprüfte Varianten {titel, kern, seed}. Varianten, die die Haare ändern, Kleidung nennen
    oder nichts ändern, fallen weg."""
    import charakter
    from charaktergalerie import _json_liste
    raus: list[dict] = []
    for d in _json_liste(text):
        if not isinstance(d, dict):
            continue
        kern = " ".join(str(d.get("kern") or "").split())[:MAX_KERN].rstrip(".")
        titel = " ".join(str(d.get("titel") or "").split())[:60]
        if (not kern or _norm(kern) == _norm(kern_alt) or not haare_gleich(kern_alt, kern)
                or charakter.kleidung_im_kern(kern) or any(_norm(kern) == _norm(v["kern"]) for v in raus)):
            continue
        raus.append({"titel": titel or f"Gesicht {len(raus) + 1}", "kern": kern,
                     "seed": random.randint(1, 2 ** 31 - 1)})
        if len(raus) >= anzahl:
            break
    if not raus:
        raise ValueError("Die KI hat kein brauchbares Gesicht geliefert (Haare geändert oder Kleidung genannt) – "
                         "noch einmal versuchen.")
    return raus


def portraet_prompt(kern: str, stil: str = "") -> str:
    """Nahaufnahme fürs Vergleichen: Kopf und Schultern, neutraler Hintergrund, der Kern erwachsen."""
    import charakter
    k = charakter.kern_erwachsen(" ".join(str(kern or "").split()))[:MAX_KERN].rstrip(".")
    stil = " ".join(str(stil or "").split()).rstrip(".") or \
        "detailed portrait photo, soft even light, sharp focus on the face, high detail"
    return (f"Close-up portrait of a single adult person, alone, head and shoulders, facing the viewer, neutral "
            f"expression, plain background. {k[:1].upper() + k[1:]}. {stil}.")


def ordner(name: str) -> Path:
    import charaktergalerie
    import profilordner
    return charaktergalerie.ordner() / "Gesichter" / profilordner.ordnername(name)


def aufraeumen(name: str) -> None:
    """Die Bilder der letzten Runde entfernen (nur die eigenen Vorschau-Bilder, auch die nachgebesserten)."""
    o = ordner(name)
    for p in [*o.glob("gesicht_*.png"), *(o / NACHGEBESSERT).glob("gesicht_*.png")]:
        try:
            p.unlink()
        except OSError:
            pass


def uebernehmen(variante: dict, name: str) -> dict:
    """Kern und Seed der gewählten Variante übernehmen; der alte Kern bleibt in `vorher` (/kern zurueck)."""
    import charakter
    kern = " ".join(str(variante.get("kern") or "").split())
    seed = variante.get("seed")
    if not kern or not isinstance(seed, int) or seed < 1:
        raise ValueError("Diese Variante ist unvollständig.")
    charakter.kern_setzen(kern, name, grund="kern_kopf")
    charakter.seed_setzen(seed, name, grund="kern_kopf")
    return {"kern": charakter.kern(name), "seed": charakter.roh(name).get("seed_referenz")}
