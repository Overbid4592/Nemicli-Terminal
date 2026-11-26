"""profilbild.py – Profilbild der aktiven Persönlichkeit im Terminal (Startseite und feste Leiste).

Die Persönlichkeit wählt es selbst: eines ihrer Kugel-Bilder (Stimmung), ein Lieblingsbild oder ein Bild aus
ihrem Bilder-Ordner (auch ein frisch gemaltes). Gespeichert in Profile/<Name>/profilbild.json; ohne Wahl gilt
ihr Standard-Kugel-Bild. Die Schwebekugel auf dem Desktop bleibt davon unberührt.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import profilordner

STANDARD = {"", "standard", "normal", "neutral", "zurueck", "zurück", "standardbild"}


def _datei() -> Path:
    return profilordner.ordner(anlegen=False) / "profilbild.json"


def _name() -> str:
    try:
        import persoenlichkeiten as PS
        return PS.active().name
    except Exception:
        return ""


def _kugelbilder() -> dict[str, Path]:
    try:
        from wache import steuerung
        return steuerung.bilder(_name())
    except Exception:
        return {}


def standard() -> Path | None:
    da = _kugelbilder()
    return da.get("") or da.get("froh") or next(iter(da.values()), None)


def pfad() -> Path | None:
    """Das gewählte Profilbild, sonst das Standard-Kugel-Bild."""
    try:
        gewaehlt = Path(json.loads(_datei().read_text(encoding="utf-8"))["pfad"])
        if gewaehlt.is_file():
            return gewaehlt
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return standard()


def _finden(wahl: str) -> tuple[Path, str]:
    """Wahl -> (Bild, Beschreibung). Nur eigene Bilder: Kugel-Bild, Lieblingsbild, eigener Bilder-Ordner."""
    import tagebuch
    klein = wahl.strip().strip('"').lower()
    da = _kugelbilder()
    schluessel = klein.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")
    if schluessel in da:
        return da[schluessel], f"Kugel-Bild „{schluessel}“"
    for e in tagebuch.lieblinge():
        if e["name"].lower() == klein and Path(e["pfad"]).is_file():
            return Path(e["pfad"]), f"Lieblingsbild „{e['name']}“"
    return tagebuch._bild_pfad(wahl), "eigenes Bild"


def setzen(wahl: str) -> str:
    """Setzt das Profilbild (oder zurück auf das Standardbild) und gibt eine Beschreibung zurück."""
    if str(wahl or "").strip().lower() in STANDARD:
        try:
            _datei().unlink()
        except OSError:
            pass
        return "wieder das Standardbild"
    bild, art = _finden(str(wahl))
    ziel = _datei()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps({"pfad": str(bild), "seit": datetime.now().strftime("%Y-%m-%d %H:%M")},
                               ensure_ascii=False), encoding="utf-8")
    return f"{art} ({bild.name})"


def auswahl_text() -> str:
    """Was zur Wahl steht – für die Persönlichkeit."""
    import tagebuch
    zeilen = [f"Jetzt: {p.name}" if (p := pfad()) else "Jetzt: kein Bild"]
    stimmungen = sorted(k for k in _kugelbilder() if k)
    if stimmungen:
        zeilen.append("Kugel-Bilder: " + ", ".join(stimmungen[:60]) + (" …" if len(stimmungen) > 60 else ""))
    lieb = [e["name"] for e in tagebuch.lieblinge()]
    if lieb:
        zeilen.append("Lieblingsbilder: " + ", ".join(lieb))
    zeilen.append("Oder ein Bild aus deinem Bilder-Ordner (pfad) – auch eins, das du gerade gemalt hast.")
    return "\n".join(zeilen)
