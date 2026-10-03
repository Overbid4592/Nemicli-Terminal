"""profilordner.py – Eigener Ordner je Persönlichkeit: Profile/<Name>/{Chats,Bilder,Skills,Erinnerungen}.

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

UNTERORDNER = ("Chats", "Bilder", "Skills", "Erinnerungen")
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def ordnername(name: str) -> str:
    """Anzeigename → Ordnername ohne Sonderzeichen."""
    t = str(name or "").translate(_UMLAUTE)
    t = re.sub(r"[^A-Za-z0-9_]+", "_", t).strip("_")
    return re.sub(r"_+", "_", t)[:60].strip("_") or "Profil"


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
