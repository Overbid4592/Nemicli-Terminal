"""
lorabild.py - Bild-LoRAs (Krea 2) aus eigenen Bildern: Bibliothek, Auslösewort im Prompt, Bindung an einen
Charakter mit SHA-256, Bild-Check vor dem Training.

Ablage im Daten-Ordner:  LoRA/Bild/<name>/
    <name>.safetensors        die LoRA (Metadaten: ausloeser, schritte, rang …)
    <name>.safetensors.sha256 Prüfsumme der fertigen Datei
    daten/                    Kopien der gewählten Bilder (die Originale bleiben unberührt)
    texte/<kennung>.txt       je Bild ein Satz vom Seh-Modell (Kleidung, Pose, Ort, Licht) – beim Training hinter
                              dem Auslöser; von Hand änderbar, ein zweiter Lauf liest ihn nur
    vorschau/                 Testbilder während des Trainings
    proben/                   Probebilder danach (Stärke 0,6 · 0,8 · 1,0)

Prompt mit LoRA:  „[name] was passiert“ – [name] wählt die LoRA, der Rest ist nur die Szene. Hat die Figur einen
Kern, steht er immer davor (Kern · [name] · Rest). Gebunden an einen Charakter steht in seiner Charakter-Datei
`lora_bild` = {name, datei, sha256}: Malt sich die Figur selbst, kommt die LoRA zum Kern dazu (A). Fehlt die
Datei oder stimmt die Prüfsumme nicht, gilt nur der Kern (B).
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MIN_BILDER = 4
NAME_MAX = 32
_NAME = re.compile(r"[^a-z0-9_-]+")
_PROMPT = re.compile(r"^\s*\[\s*([A-Za-z0-9_ -]{1,40}?)\s*\]\s*[,:;.\-–]?\s*")
_SHA_CACHE: dict[str, tuple[int, int, str]] = {}


# --- Namen und Ablage -----------------------------------------------------------------------------------
def name_sauber(text: str) -> str:
    """„Figur Eins“ → „figur_eins“: Kleinbuchstaben, Ziffern, _ und -; leer, wenn nichts übrig bleibt."""
    t = str(text or "").strip().lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    t = _NAME.sub("_", t.replace(" ", "_")).strip("_-")
    return re.sub(r"_+", "_", t)[:NAME_MAX].strip("_-")


def wurzel() -> Path:
    import paths
    return Path(paths.DATEN) / "LoRA" / "Bild"


def ordner(name: str, *, anlegen: bool = False) -> Path:
    n = name_sauber(name)
    if not n:
        raise ValueError("Der LoRA fehlt ein Name (Buchstaben oder Ziffern).")
    p = wurzel() / n
    if anlegen:
        p.mkdir(parents=True, exist_ok=True)
    return p


def datei(name: str) -> Path:
    n = name_sauber(name)
    return ordner(n) / f"{n}.safetensors"


def ausloeser(name: str) -> str:
    """Der Satz, mit dem die LoRA lernt und mit dem sie beim Malen wieder geweckt wird."""
    return f"A photo of {name_sauber(name)}, an adult."


# Was sich von Bild zu Bild ändert, steht im Text; was gleich bleibt (die Person), lernt der Auslöser allein.
BILDTEXT_FRAGE = ("Write one English sentence that describes this photo for an image-model caption. Mention only "
                  "what can change from photo to photo: clothing, pose, expression, setting, lighting and framing. "
                  "Do not describe the person's face, eyes, hair, skin or body, and do not guess who it is.")
BILDTEXT_MAX = 300


def _bildtext_datei(name: str, bild: Path) -> Path:
    """texte/<kennung>.txt – die Kennung ist der SHA-256-Anfang im Namen der Kopie (001_<kennung>.png)."""
    kennung = bild.stem.split("_", 1)[-1]
    return ordner(name, anlegen=True) / "texte" / f"{kennung}.txt"


def _bildtext_sauber(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip().strip("\"'“”„")
    return t[:BILDTEXT_MAX].strip()


def bildtexte(name: str, daten: list[Path], melden=None) -> list[str] | None:
    """Je Trainingsbild ein Satz (gleiche Reihenfolge wie `daten`). Vorhandene Texte werden gelesen, fehlende
    schreibt das Seh-Modell. None, wenn Texte fehlen und kein Seh-Modell da ist – dann lernt jedes Bild mit dem
    Auslöser allein."""
    dateien = [_bildtext_datei(name, p) for p in daten]
    fehlen = [i for i, f in enumerate(dateien) if not f.is_file()]
    if fehlen:
        import bildbeschreiber
        if bildbeschreiber.finden() is None:
            return None
        from PIL import Image
        import vision
        try:
            for n, i in enumerate(fehlen, 1):
                if melden:
                    melden(f"Bilder beschreiben {n}/{len(fehlen)} …")
                try:
                    with Image.open(daten[i]) as bild:
                        text = _bildtext_sauber(bildbeschreiber.fragen_roh(vision.aufrecht(bild), BILDTEXT_FRAGE,
                                                                           90))
                except Exception:
                    text = ""
                from atomar import text_schreiben
                dateien[i].parent.mkdir(parents=True, exist_ok=True)
                text_schreiben(dateien[i], text)
        finally:
            bildbeschreiber.entladen()              # Platz für das Training
    return [_bildtext_sauber(f.read_text(encoding="utf-8")) for f in dateien]


def liste() -> list[dict]:
    """Fertige LoRAs der Bibliothek: {name, datei, sha256} – neueste zuerst."""
    w = wurzel()
    if not w.is_dir():
        return []
    raus = []
    for d in w.iterdir():
        f = d / f"{d.name}.safetensors"
        if d.is_dir() and f.is_file():
            raus.append({"name": d.name, "datei": str(f), "sha256": gespeicherte_sha(f), "zeit": f.stat().st_mtime})
    raus.sort(key=lambda e: e["zeit"], reverse=True)
    return raus


# --- Prüfsummen -----------------------------------------------------------------------------------------
def sha256(pfad: Path) -> str:
    """SHA-256 einer Datei; gemerkt, solange Größe und Änderungszeit gleich bleiben."""
    p = Path(pfad)
    st = p.stat()
    schluessel = str(p.resolve())
    alt = _SHA_CACHE.get(schluessel)
    if alt and alt[0] == st.st_size and alt[1] == st.st_mtime_ns:
        return alt[2]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 20), b""):
            h.update(stueck)
    _SHA_CACHE[schluessel] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return h.hexdigest()


def sha_schreiben(pfad: Path) -> str:
    p = Path(pfad)
    wert = sha256(p)
    p.with_name(p.name + ".sha256").write_text(f"{wert}  {p.name}\n", encoding="utf-8")
    return wert


def gespeicherte_sha(pfad: Path) -> str:
    try:
        return Path(str(pfad) + ".sha256").read_text(encoding="utf-8").split()[0].lower()
    except (OSError, IndexError):
        return ""


# --- Prompt ---------------------------------------------------------------------------------------------
def prompt_lesen(prompt: str) -> tuple[str | None, str]:
    """„[beispiel] sitzt im Café“ → ("beispiel", "sitzt im Café"). Ohne [name] am Anfang: (None, prompt)."""
    m = _PROMPT.match(prompt or "")
    if not m:
        return None, prompt or ""
    return name_sauber(m.group(1)) or None, (prompt or "")[m.end():].strip()


def _satz(t: str) -> str:
    t = (t or "").strip().rstrip(".,;")
    return (t[:1].upper() + t[1:] + ".") if t else ""


def prompt_bauen(name: str, szene: str = "", outfit: str = "", pose: str = "", stil: str = "",
                 kern: str = "", frisur: str = "") -> str:
    """Kern · Auslösewort · Frisur · Kleidung · Pose · Szene · Stil. Der Kern (das feste Aussehen eines Charakters)
    steht immer vorne, wenn es einen gibt; die LoRA kommt dazu."""
    teile = []
    if kern:
        import charakter
        teile.append(_satz(charakter.kern_erwachsen(kern)))
    teile.append(ausloeser(name))
    if frisur:
        teile.append(_satz("Hair: " + frisur))
    if outfit:
        teile.append(_satz("Wearing " + outfit))
    teile += [_satz(pose), _satz(szene), _satz(stil)]
    return " ".join(t for t in teile if t)


# --- Bindung an einen Charakter ---------------------------------------------------------------------------
def bindung_sauber(v) -> dict | None:
    """Der Eintrag `lora_bild` der Charakter-Datei – None, wenn er unvollständig ist."""
    if not isinstance(v, dict):
        return None
    name = name_sauber(v.get("name") or "")
    sha = str(v.get("sha256") or "").strip().lower()
    pfad = str(v.get("datei") or "").strip()
    if not name or not pfad or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    return {"name": name, "datei": pfad, "sha256": sha}


def pruefen(bindung: dict | None) -> tuple[Path | None, str]:
    """(Pfad, "") wenn die gebundene LoRA da ist und ihre SHA-256 stimmt – sonst (None, Grund)."""
    b = bindung_sauber(bindung)
    if b is None:
        return None, ""
    p = Path(b["datei"])
    if not p.is_file():
        return None, f"die LoRA-Datei {p.name} fehlt"
    try:
        if sha256(p) != b["sha256"]:
            return None, f"die LoRA-Datei {p.name} hat sich verändert (SHA-256 stimmt nicht)"
    except OSError as e:
        return None, f"die LoRA-Datei {p.name} ist nicht lesbar ({e})"
    return p, ""


def fuer_charakter(name: str | None = None) -> tuple[dict | None, Path | None, str]:
    """Gebundene Bild-LoRA eines Charakters (sonst des aktiven): (Bindung, Pfad oder None, Grund)."""
    import charakter
    b = bindung_sauber(charakter.roh(name).get("lora_bild"))
    if b is None:
        return None, None, ""
    pfad, grund = pruefen(b)
    return b, pfad, grund


def binden(lora: str, charakter_name: str) -> dict:
    """Die LoRA an einen Charakter binden: SHA-256 der Datei in seine Charakter-Datei (mit Sicherung A/B)."""
    import charakter
    f = datei(lora)
    if not f.is_file():
        raise FileNotFoundError(f"LoRA „{lora}“ gibt es noch nicht.")
    b = {"name": name_sauber(lora), "datei": str(f), "sha256": sha_schreiben(f)}
    d = charakter.roh(charakter_name)
    if not d.get("name"):
        d["name"] = charakter_name
    d["lora_bild"] = b
    charakter.schreiben(d, charakter_name, "lora_binden")
    return b


def loesen(charakter_name: str) -> bool:
    import charakter
    d = charakter.roh(charakter_name)
    if "lora_bild" not in d:
        return False
    d.pop("lora_bild")
    charakter.schreiben(d, charakter_name, "lora_loesen")
    return True


def finden(name: str) -> Path | None:
    """Pfad der LoRA „name“ aus der Bibliothek – None, wenn es sie nicht gibt."""
    try:
        f = datei(name)
    except ValueError:
        return None
    return f if f.is_file() else None


# --- Bilder für das Training --------------------------------------------------------------------------
def bilder_sammeln(pfade) -> list[Path]:
    """Bilddateien aus Dateien und Ordnern (eine Ebene), ohne Doppelte, in der gegebenen Reihenfolge."""
    raus, gesehen = [], set()
    for p in pfade:
        p = Path(str(p).strip().strip('"'))
        kandidaten = sorted(p.iterdir()) if p.is_dir() else [p]
        for k in kandidaten:
            if k.is_file() and k.suffix.lower() in ENDUNGEN:
                schluessel = str(k.resolve()).lower()
                if schluessel not in gesehen:
                    gesehen.add(schluessel)
                    raus.append(k)
    return raus


def daten_kopieren(name: str, bilder: list[Path]) -> list[Path]:
    """Kopien der gewählten Bilder nach LoRA/Bild/<name>/daten – das Training liest nur diese.
    Nicht mehr gewählte Kopien wandern in den Papierkorb; gleiche Bilder werden nicht neu kopiert."""
    ziel = ordner(name, anlegen=True) / "daten"
    ziel.mkdir(parents=True, exist_ok=True)
    neu: list[Path] = []
    for i, quelle in enumerate(bilder, 1):
        kennung = sha256(quelle)[:12]
        k = ziel / f"{i:03d}_{kennung}{quelle.suffix.lower()}"
        vorhanden = next(iter(ziel.glob(f"*_{kennung}{quelle.suffix.lower()}")), None)
        if vorhanden is not None and vorhanden != k:
            vorhanden.replace(k)
        elif vorhanden is None:
            shutil.copy2(quelle, k)
        neu.append(k)
    behalten = {p.name for p in neu}
    for alt in ziel.iterdir():
        if alt.is_file() and alt.name not in behalten:
            try:
                import snapshot
                snapshot.sichern(alt, "lora_bild_abgewaehlt", verschieben=True)
            except Exception:
                alt.unlink(missing_ok=True)
    return neu


def schaerfe(pfad: Path) -> float:
    """Wie scharf ein Bild ist: Streuung des Laplace-Bilds (Graustufen, höchstens 512 px)."""
    import numpy as np
    from PIL import Image
    with Image.open(pfad) as im:
        g = im.convert("L")
        g.thumbnail((512, 512))
        a = np.asarray(g, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = (-4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
    return float(lap.var())


def pruefung(bilder: list[Path], gesichter=None) -> list[dict]:
    """Bild-Check vor dem Training. Je Bild: {pfad, ok, zeichen, hinweis, gesichter}.
    🛑 = doppelt, nicht lesbar oder mehrere Gesichter (vorgeschlagen: abwählen) · ⚠ = unscharf oder kein Gesicht
    erkannt (vielleicht von hinten) · ✔️ = passt. `gesichter(arr) -> Kästen` (Seh-Modell), None = ohne Gesichter."""
    import numpy as np
    from PIL import Image
    werte, raus, gesehen = [], [], {}
    for p in bilder:
        e = {"pfad": str(p), "ok": True, "zeichen": "✔️", "hinweis": "", "gesichter": None}
        try:
            sha = sha256(p)
            if sha in gesehen:
                e.update(ok=False, zeichen="🛑", hinweis=f"doppelt (wie Bild {gesehen[sha] + 1})")
            else:
                gesehen[sha] = len(raus)
                e["schaerfe"] = schaerfe(p)
                werte.append(e["schaerfe"])
        except Exception as ex:
            e.update(ok=False, zeichen="🛑", hinweis=f"nicht lesbar ({type(ex).__name__})")
        raus.append(e)
    mitte = float(np.median(werte)) if werte else 0.0
    for e in raus:
        if e["zeichen"] == "🛑":
            continue
        if e.get("schaerfe", 0.0) < max(8.0, 0.35 * mitte):
            e.update(zeichen="⚠", hinweis="wirkt unscharf")
        if gesichter is None:
            continue
        try:
            with Image.open(e["pfad"]) as im:
                n = len(gesichter(np.asarray(im.convert("RGB"))))
        except Exception:
            continue
        e["gesichter"] = n
        if n >= 2:
            e.update(ok=False, zeichen="🛑", hinweis=f"{n} Gesichter – eine LoRA lernt eine Person")
        elif n == 0 and e["zeichen"] == "✔️":
            e.update(zeichen="⚠", hinweis="kein Gesicht erkannt (vielleicht von hinten)")
    return raus
