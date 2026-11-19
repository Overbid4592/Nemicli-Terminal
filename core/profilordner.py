"""profilordner.py – Eigener Ordner je Persönlichkeit: Profile/<Name>/{Chats,Bilder,Videos,Skills,Erinnerungen,LoRA}.

Der Ordnername kommt aus dem Namen der aktiven Persönlichkeit: nur A–Z, a–z, 0–9 und _
(Umlaute → ae/oe/ue/ss). Gemeinsam für alle bleiben: „Über dich“, Fakten und Vorlieben
über den Nutzer (learned/memory.json), globale Skills (Skills/) und die alten Ordner
chats/ und Bilder/ aus der Zeit vor den Profilen.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import paths

UNTERORDNER = ("Chats", "Bilder", "Videos", "Skills", "Erinnerungen", "LoRA")
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


# Gerätenamen: Windows legt dafür keinen Ordner an.
RESERVIERT = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10))})


def ordnername(name: str) -> str:
    """Anzeigename → Ordnername ohne Sonderzeichen."""
    t = str(name or "").translate(_UMLAUTE)
    t = re.sub(r"[^A-Za-z0-9_]+", "_", t).strip("_")
    t = re.sub(r"_+", "_", t)[:60].strip("_") or "Profil"
    return f"{t}_" if t.upper() in RESERVIERT else t


def belegt() -> set[str]:
    """Vorhandene Profil-Ordner (klein geschrieben – Windows unterscheidet nicht)."""
    try:
        return {p.name.lower() for p in wurzel().iterdir() if p.is_dir()}
    except OSError:
        return set()


def beiseitelegen(name: str) -> Path | None:
    """Profile/<Name>/ als _geloescht_<Name>_<Zeit> umbenennen statt löschen: Chats, Bilder und
    Eigenes bleiben erhalten, eine spätere gleichnamige Persönlichkeit erbt nichts davon.
    Rückgabe: der neue Ort, None wenn es keinen Ordner gab oder er gerade nicht umzubenennen ist."""
    alt = wurzel() / ordnername(name)
    if not alt.is_dir():
        return None
    basis = f"_geloescht_{alt.name}_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
    ziel, n = alt.with_name(basis), 2
    while ziel.exists():
        ziel, n = alt.with_name(f"{basis}_{n}"), n + 1
    try:
        alt.rename(ziel)
    except OSError:                         # Datei noch offen: Ordner bleibt (neue Namen weichen ihm aus)
        return None
    return ziel


def aktiv() -> str:
    """Ordnername der aktiven Persönlichkeit."""
    try:
        import persoenlichkeiten
        return ordnername(persoenlichkeiten.active().name)
    except Exception:
        return "Profil"


def wurzel() -> Path:
    return Path(paths.DATEN) / "Profile"


def ordner(unter: str = "", name: str | None = None, *, anlegen: bool = True) -> Path:
    """Profile/<Name>[/<unter>] – für die aktive Persönlichkeit, wenn kein Name angegeben ist."""
    pfad = wurzel() / (ordnername(name) if name else aktiv())
    if unter:
        pfad = pfad / unter
    if anlegen:
        pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def _eindeutig(ordner_: Path, endung: str) -> Path:
    name = aktiv()
    basis = ordner_ / f"{name}_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
    pfad, n = basis.parent / f"{basis.name}{endung}", 2
    while pfad.exists():
        pfad = basis.parent / f"{basis.name}_{n}{endung}"
        n += 1
    return pfad


def bild_pfad(endung: str = ".png") -> Path:
    """Neuer, eindeutiger Bildpfad: Profile/<Name>/Bilder/<Name>_JJJJ-MM-TT_HH-MM-SS.png."""
    return _eindeutig(ordner("Bilder"), endung)


def video_pfad(endung: str = ".mp4") -> Path:
    """Neuer, eindeutiger Videopfad: Profile/<Name>/Videos/<Name>_JJJJ-MM-TT_HH-MM-SS.mp4."""
    return _eindeutig(ordner("Videos"), endung)


def musik_pfad(endung: str = ".wav", titel: str = "") -> Path:
    """Neuer, eindeutiger Musikpfad: Profile/<Name>/Musik/<Titel>_JJJJ-MM-TT_HH-MM.wav,
    ohne Titel <Name>_JJJJ-MM-TT_HH-MM-SS.wav."""
    kurz = "".join(c for c in str(titel or "") if ord(c) >= 32 and c not in '<>:"/\\|?*')
    kurz = re.sub(r"\s+", " ", kurz).strip(" .")[:60].strip(" .")
    if not kurz:
        return _eindeutig(ordner("Musik"), endung)
    o = ordner("Musik")
    basis = f"{kurz}_{time.strftime('%Y-%m-%d_%H-%M')}"
    pfad, n = o / f"{basis}{endung}", 2
    while pfad.exists():
        pfad = o / f"{basis}_{n}{endung}"
        n += 1
    return pfad


def lora_pfad(ziel: str, name: str | None = None) -> Path:
    """Charakter-LoRA einer Persönlichkeit für einen Motor: Profile/<Name>/LoRA/<ziel>.safetensors."""
    return ordner("LoRA", name) / f"{ziel}.safetensors"


def bild_ziel(alter_ordner, endung: str = ".png") -> Path:
    """Wohin ein Bild-Motor speichert: in den Ordner der aktiven Persönlichkeit – außer sein
    OUT_DIR zeigt nicht mehr auf das gemeinsame Bilder/ (umgelenkt), dann dorthin."""
    standard = Path(paths.DATEN) / "Bilder"
    try:
        umgelenkt = Path(alter_ordner).resolve() != standard.resolve()
    except OSError:
        umgelenkt = True
    if not umgelenkt:
        return bild_pfad(endung)
    Path(alter_ordner).mkdir(parents=True, exist_ok=True)
    return _eindeutig(Path(alter_ordner), endung)


def bilder_ordner_alle() -> list[Path]:
    """Wo Bilder der aktiven Persönlichkeit liegen: ihr Ordner, dann das alte gemeinsame Bilder/."""
    return [ordner("Bilder", anlegen=False), Path(paths.DATEN) / "Bilder"]
