"""wunsch.py – einen frei formulierten Musikwunsch von der KI in Motor-Eingaben übersetzen lassen.

Die KI antwortet mit einem JSON-Objekt (titel, stil, text, sprache, bpm, tonart, sekunden);
`lesen` macht daraus geprüfte Werte für motor.komponieren.
"""
from __future__ import annotations

import json
import re

MIN_SEKUNDEN, MAX_SEKUNDEN = 10, 240
INSTRUMENTAL = "[Instrumental]"

SYSTEM = """Du arbeitest einen Musikwunsch für einen Musik-Generator (ACE-Step) aus.
Antworte NUR mit einem JSON-Objekt, ohne Erklärung davor oder danach:
{"titel": "kurzer Titel", "stil": "...", "text": "...", "sprache": "de", "bpm": 120, "tonart": "A minor", "sekunden": 60}

- stil: englisch, kommagetrennt, 15-40 Wörter: Genre und Untergenre, Instrumente, Stimmung, Tempo-Gefühl,
  Gesangsstimme (z. B. "female vocals", "male raspy vocals"), Produktion/Klang. Nur lateinische Buchstaben.
- text: Liedtext mit Abschnitts-Marken in eigenen Zeilen: [Intro] [Verse] [Pre-Chorus] [Chorus] [Bridge] [Outro].
  Pro Abschnitt 2-6 kurze, singbare Zeilen; Länge passend zu sekunden (grob eine Zeile je 3-4 s Gesang,
  Platz für Intro und Outro lassen). Sprache des Liedtexts = Sprache des Wunsches, wenn nichts anderes verlangt ist.
  Ohne Gesang: genau "[Instrumental]".
- sprache: Sprachcode des Gesangs (de, en, es, fr, it, ja, ...).
- bpm: passendes Tempo als Zahl. tonart: z. B. "C major", "F# minor".
- sekunden: die gewünschte Länge (10-240)."""


def anfrage(wunsch: str, sekunden: int | None = None, gesang: bool = True, entwurf: dict | None = None) -> str:
    """Nachricht an die KI. Mit `entwurf` überarbeitet sie einen vorhandenen Vorschlag."""
    zeilen = [f"Wunsch: {wunsch.strip()}"]
    if sekunden:
        zeilen.append(f"Länge: {int(sekunden)} Sekunden")
    zeilen.append("Gesang: ja" if gesang else "Gesang: nein (instrumental)")
    if entwurf:
        zeilen.append("Bisheriger Entwurf (überarbeite ihn nach dem Wunsch):\n"
                      + json.dumps(entwurf, ensure_ascii=False, indent=1))
    return "\n".join(zeilen)


def _json_block(antwort: str) -> dict | None:
    antwort = re.sub(r"(?s)<think>.*?</think>", "", antwort or "")
    for treffer in re.finditer(r"\{", antwort):
        tiefe = 0
        for i in range(treffer.start(), len(antwort)):
            if antwort[i] == "{":
                tiefe += 1
            elif antwort[i] == "}":
                tiefe -= 1
                if tiefe == 0:
                    try:
                        wert = json.loads(antwort[treffer.start():i + 1])
                    except ValueError:
                        break
                    if isinstance(wert, dict):
                        return wert
                    break
    return None


def _zahl(wert, unten: int, oben: int) -> int | None:
    try:
        z = int(round(float(wert)))
    except (TypeError, ValueError):
        return None
    return min(oben, max(unten, z))


def _lateinisch(text: str) -> str:
    """Nur Zeichen bis Latin Extended (U+024F) – der Textleser des Motors ist auf Englisch trainiert."""
    return re.sub(r"\s+", " ", "".join(c if ord(c) <= 0x24F else " " for c in text)).strip()


def titel_vorschlag(text: str, stil: str = "") -> str:
    """Titel, wenn keiner genannt ist: erste Zeile des Refrains, sonst erste Liedzeile, sonst der Stil."""
    zeilen = [z.strip() for z in (text or "").splitlines()]
    refrain = False
    erste = ""
    for z in zeilen:
        if z.startswith("["):
            refrain = z.lower().startswith("[chorus")
            continue
        if z and not erste:
            erste = z
        if z and refrain:
            erste = z
            break
    kandidat = erste or " ".join(w.strip(",") for w in (stil or "").split()[:4]).title()
    kandidat = re.sub(r"[.,;:!?]+$", "", kandidat)
    return kandidat[:40].rsplit(" ", 1)[0] if len(kandidat) > 40 else kandidat


def lesen(antwort: str, gesang: bool = True, sekunden: int | None = None) -> dict:
    """KI-Antwort -> {titel, stil, text, sprache, bpm, tonart, sekunden}. Wirft ValueError ohne JSON/Stil."""
    roh = _json_block(antwort)
    if roh is None:
        raise ValueError("Die KI hat keinen Entwurf (JSON) geliefert.")
    stil = _lateinisch(str(roh.get("stil") or roh.get("caption") or ""))
    if not stil:
        raise ValueError("Im Entwurf fehlt der Stil.")
    text = str(roh.get("text") or roh.get("lyrics") or "").strip().replace("\r\n", "\n")
    if not gesang or not text:
        text = INSTRUMENTAL
    tonart = str(roh.get("tonart") or roh.get("keyscale") or "").strip()
    sprache = str(roh.get("sprache") or roh.get("language") or "").strip().lower()[:5]
    return {
        "titel": str(roh.get("titel") or roh.get("title") or "").strip()[:80],
        "stil": stil,
        "text": text,
        "sprache": sprache if re.fullmatch(r"[a-z]{2,3}", sprache) else "",
        "bpm": _zahl(roh.get("bpm"), 30, 300),
        "tonart": tonart[:20],
        "sekunden": _zahl(roh.get("sekunden") or roh.get("duration") or sekunden, MIN_SEKUNDEN, MAX_SEKUNDEN)
        or 30,
    }
