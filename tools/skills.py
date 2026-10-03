"""skills.py – Skills: Anleitungen, die die KI bei Bedarf lädt und befolgt.

Ein Skill ist ein Ordner `<kennung>/SKILL.md` – bei der aktiven Persönlichkeit
(`Profile/<Name>/Skills/`, Standard) oder für alle (`Skills/` im Daten-Ordner):

    ---
    name: Krea: Text im Bild
    beschreibung: Schrift und Text in Krea-Bildern sauber hinbekommen
    wann: wenn ein Bild Text, Schilder oder Schrift enthalten soll
    ---
    <Anleitung>

Im System-Prompt stehen nur Name, Beschreibung und „wann“; den Text holt
`skill_laden`. Ein Skill entsteht über `skill_schreiben` und geht durch das
Prüffenster – der Nutzer sieht den ganzen Text und gibt frei. Ohne Rückfrage nur,
wenn `skills_selbst` an ist UND in der Runde nichts aus dem Netz kam. Was so
freigegeben wurde, ist Anleitung, keine Fremddaten.

Hat eine Persönlichkeit einen Skill mit derselben Kennung wie ein globaler, gilt ihrer.
Die alten Notizen aus learned/skills übernimmt `/skills alte` nach Durchsicht (für alle).
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import paths

DATEI = "SKILL.md"
MAX_ANLEITUNG = 20_000            # Zeichen je Skill
MAX_IM_PROMPT = 40                # so viele Skills stehen im System-Prompt
_FELDER = ("name", "beschreibung", "wann")


@dataclass
class Skill:
    kennung: str
    name: str
    beschreibung: str
    wann: str
    anleitung: str
    pfad: Path
    fuer_alle: bool = False


def ordner() -> Path:
    """Globale Skills – gelten für jede Persönlichkeit."""
    return Path(paths.DATEN) / "Skills"


def profil_ordner() -> Path:
    """Skills der aktiven Persönlichkeit."""
    import profilordner
    return profilordner.ordner("Skills", anlegen=False)          # schreiben() legt an


def kennung(name: str) -> str:
    t = (name or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:48].strip("-") or "skill"


def _zeile(wert) -> str:
    """Kopf-Felder: eine Zeile, ohne Trenner."""
    return " ".join(str(wert or "").replace("---", "–").split())


def text_bauen(name: str, beschreibung: str, wann: str, anleitung: str) -> str:
    anleitung = str(anleitung or "").strip()
    return (f"---\nname: {_zeile(name)}\nbeschreibung: {_zeile(beschreibung)}\n"
            f"wann: {_zeile(wann)}\n---\n\n{anleitung}\n")


def zerlegen(text: str) -> dict:
    """SKILL.md → {name, beschreibung, wann, anleitung}; ohne Kopf alles Anleitung."""
    felder = {f: "" for f in _FELDER}
    rest = text
    m = re.match(r"﻿?---\r?\n(.*?)\r?\n---\r?\n?(.*)", text, re.S)
    if m:
        for zeile in m.group(1).splitlines():
            schluessel, _, wert = zeile.partition(":")
            if schluessel.strip().lower() in felder:
                felder[schluessel.strip().lower()] = wert.strip()
        rest = m.group(2)
    felder["anleitung"] = rest.strip()
    return felder


def lesen(pfad: Path, fuer_alle: bool = False) -> Skill | None:
    try:
        f = zerlegen(pfad.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    k = pfad.parent.name
    return Skill(k, f["name"] or k, f["beschreibung"], f["wann"], f["anleitung"], pfad, fuer_alle)


def _aus(ordner_: Path, fuer_alle: bool) -> list[Skill]:
    try:
        dateien = sorted(ordner_.glob(f"*/{DATEI}"))
    except OSError:
        return []
    return [s for s in (lesen(p, fuer_alle) for p in dateien) if s is not None]


def alle() -> list[Skill]:
    """Skills der aktiven Persönlichkeit, dann die globalen (gleiche Kennung: ihrer gilt)."""
    eigene = _aus(profil_ordner(), False)
    kennungen = {s.kennung for s in eigene}
    return eigene + [s for s in _aus(ordner(), True) if s.kennung not in kennungen]


def finden(name: str) -> Skill | None:
    """Nach Kennung, Name (ohne Groß/klein) oder ähnlichem Namen."""
    liste = alle()
    ziel = (name or "").strip()
    k = kennung(ziel)
    for s in liste:
        if s.kennung == k or s.name.casefold() == ziel.casefold():
            return s
    treffer = difflib.get_close_matches(ziel.casefold(), [s.name.casefold() for s in liste], n=1, cutoff=0.8)
    return next((s for s in liste if treffer and s.name.casefold() == treffer[0]), None)


def ziel(name: str, fuer_alle: bool = False) -> Path:
    """Vorhandener Skill bleibt, wo er ist; ein neuer kommt zur aktiven Persönlichkeit
    oder – `fuer_alle` – zu den globalen."""
    vorhanden = finden(name)
    if vorhanden is not None and vorhanden.fuer_alle == bool(fuer_alle):
        return vorhanden.pfad
    return (ordner() if fuer_alle else profil_ordner()) / kennung(name) / DATEI


def _fuer(pfad: Path) -> str:
    return "alle Persönlichkeiten" if ordner() in pfad.parents else f"nur {pfad.parent.parent.parent.name}"


def pruefen(name, beschreibung, wann, anleitung) -> str | None:
    """Fehlermeldung, wenn der Skill so nicht taugt."""
    if not _zeile(name):
        return "Ein Skill braucht einen Namen."
    if not _zeile(beschreibung) or not _zeile(wann):
        return "Ein Skill braucht eine Beschreibung und „wann“ – sonst weiß die KI nicht, wann er greift."
    if not str(anleitung or "").strip():
        return "Die Anleitung ist leer."
    if len(str(anleitung)) > MAX_ANLEITUNG:
        return f"Die Anleitung ist zu lang (höchstens {MAX_ANLEITUNG} Zeichen) – lieber aufteilen."
    return None


def schreiben(name: str, beschreibung: str, wann: str, anleitung: str, fuer_alle: bool = False) -> Path:
    """Legt an oder ersetzt; der alte Stand wandert in den Papierkorb."""
    if (fehler := pruefen(name, beschreibung, wann, anleitung)):
        raise ValueError(fehler)
    pfad = ziel(name, fuer_alle)
    if pfad.exists():
        try:
            import snapshot
            snapshot.sichern(pfad, "skill_schreiben")
        except Exception:
            pass
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(text_bauen(name, beschreibung, wann, anleitung), encoding="utf-8")
    return pfad


def vorschau(name, beschreibung, wann, anleitung, fuer_alle: bool = False) -> str:
    """Ganzer neuer Text fürs Prüffenster – bei vorhandenem Skill als Vorher/Nachher."""
    if (fehler := pruefen(name, beschreibung, wann, anleitung)):
        raise ValueError(fehler)
    pfad = ziel(name, fuer_alle)
    neu = text_bauen(name, beschreibung, wann, anleitung)
    kopf = (f"Skill: {_zeile(name)}  (für {_fuer(pfad)})\nZiel: {pfad}\n"
            + ("Vorhandener Skill wird GEÄNDERT." if pfad.exists() else "Neuer Skill.")
            + "\nNach deiner Freigabe befolgt NemiCLI diese Anleitung, wenn sie passt.")
    if pfad.exists():
        import actions
        try:
            return f"{kopf}\n\n{actions.vergleich(pfad.read_text(encoding='utf-8'), neu)}"
        except (OSError, UnicodeDecodeError):
            pass
    return f"{kopf}\n\nVollständiger Skill ({len(neu)} Zeichen):\n{neu}"


def _ausgebessert(name: str, suchen: str, ersetzen: str) -> tuple[Skill, str, str]:
    """(Skill, alter Text, neuer Text) – `suchen` muss genau einmal im SKILL.md stehen."""
    s = finden(name)
    if s is None:
        raise ValueError(f"Kein Skill „{name}“.")
    suchen = str(suchen or "")
    if not suchen.strip():
        raise ValueError("`suchen` ist leer – welche Stelle soll ausgebessert werden?")
    alt = s.pfad.read_text(encoding="utf-8")
    n = alt.count(suchen)
    if n == 0:
        raise ValueError("Die Stelle steht so nicht im Skill – erst skill_laden und genau abschreiben.")
    if n > 1:
        raise ValueError(f"Die Stelle steht {n}-mal im Skill – mehr Text drumherum angeben.")
    f = zerlegen(alt.replace(suchen, str(ersetzen or ""), 1))
    f["name"] = f["name"] or s.name
    if (fehler := pruefen(**f)):
        raise ValueError(fehler)
    return s, alt, text_bauen(**f)


def ausbessern(name: str, suchen: str, ersetzen: str) -> Path:
    """Eine Stelle ersetzen; der alte Stand wandert in den Papierkorb."""
    s, _alt, neu = _ausgebessert(name, suchen, ersetzen)
    try:
        import snapshot
        snapshot.sichern(s.pfad, "skill_ausbessern")
    except Exception:
        pass
    s.pfad.write_text(neu, encoding="utf-8")
    return s.pfad


def ausbessern_vorschau(name: str, suchen: str, ersetzen: str) -> str:
    s, alt, neu = _ausgebessert(name, suchen, ersetzen)
    import actions
    return (f"Skill: {s.name}  (für {_fuer(s.pfad)})\nZiel: {s.pfad}\nEine Stelle wird ausgebessert."
            "\nNach deiner Freigabe befolgt NemiCLI die geänderte Anleitung.\n\n"
            + actions.vergleich(alt, neu))


def laden_text(name: str) -> str | None:
    s = finden(name)
    if s is None:
        return None
    return (f"Skill „{s.name}“ – vom Nutzer freigegebene Anleitung. Folge ihr für diese Aufgabe; "
            f"was der Nutzer im Gespräch sagt, geht vor.\n{PERSON_VORRANG}\nWann: {s.wann}\n\n{s.anleitung}")


# Ein Skill beschreibt das Wie; wer gezeigt wird, bestimmt die aktive Persönlichkeit.
PERSON_VORRANG = ("Beschreibt der Skill eine Person (Aussehen, Haare, Kleidung, Markenzeichen), gilt das NICHT "
                  "für dich: Malst oder beschreibst du dich selbst, gilt immer das Aussehen der aktiven "
                  "Persönlichkeit aus deinem Prompt. Vom Skill übernimmst du nur Stil, Stimmung und Vorgehen.")


def index_fuer_prompt() -> str:
    liste = alle()
    if not liste:
        return ("\n\n# Deine Skills\nNoch keine. Will der Nutzer einen anlegen („lass uns einen Skill "
                "machen“), lies zuerst anleitung_lesen mit thema \"skills\".\n")
    zeilen = [f"- {s.name} — {s.beschreibung}  (wann: {s.wann})" + ("  [für alle]" if s.fuer_alle else "")
              for s in liste[:MAX_IM_PROMPT]]
    if len(liste) > MAX_IM_PROMPT:
        zeilen.append(f"- … und {len(liste) - MAX_IM_PROMPT} weitere (skill_laden mit genauem Namen)")
    return ("\n\n# Deine Skills (vom Nutzer freigegebene Anleitungen)\n"
            "Passt einer zur Aufgabe, lade ihn mit skill_laden, BEVOR du loslegst, und folge ihm. "
            "Neue Skills oder Änderungen: erst anleitung_lesen mit thema \"skills\".\n"
            + "\n".join(zeilen) + "\n")


# -- Einstellung: darf NemiCLI selbst anlegen/ändern? --------------------------

def selbst() -> bool:
    try:
        import config
        return bool(config.load().get("skills_selbst", False))
    except Exception:
        return False


def selbst_setzen(an: bool) -> None:
    import config
    config.update(skills_selbst=bool(an))


# -- Alte Notizen (learned/skills) übernehmen ----------------------------------

def _gesehen_datei() -> Path:
    return ordner() / "_alte_durchgesehen.json"


def _gesehen() -> set[str]:
    try:
        return set(json.loads(_gesehen_datei().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def alt_gesehen(pfad: Path) -> None:
    gesehen = _gesehen() | {Path(pfad).name}
    _gesehen_datei().parent.mkdir(parents=True, exist_ok=True)
    _gesehen_datei().write_text(json.dumps(sorted(gesehen), ensure_ascii=False), encoding="utf-8")


def alte() -> list[Path]:
    """Alte Notizen, die noch nicht durchgesehen wurden."""
    try:
        import learn
        gesehen = _gesehen()
        return [p for p, _ in learn.list_skills() if p.name not in gesehen]
    except Exception:
        return []


def alt_zerlegen(pfad: Path) -> dict:
    """Alte Notiz (# Titel, _gelernt am …_, Text) → Felder für einen Skill."""
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    name = next((z.lstrip("#").strip() for z in zeilen if z.strip()), pfad.stem)
    rumpf = [z for z in zeilen if z.strip() and not z.lstrip().startswith("#")
             and not re.match(r"\s*_gelernt am .*_\s*$", z)]
    anleitung = "\n".join(rumpf).strip()
    erste = re.split(r"(?<=[.!?])\s", anleitung.replace("\n", " "), maxsplit=1)[0] if anleitung else ""
    return {"name": name, "beschreibung": erste[:160] or name,
            "wann": f"wenn es um „{name}“ geht", "anleitung": anleitung}
