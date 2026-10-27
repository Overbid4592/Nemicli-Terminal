"""lernschleife.py – Automatisch reflektieren, wenn eine Runde etwas zu lernen hergibt.

Anlass: eine Korrektur oder Vorgabe des Nutzers („nein“, „falsch“, „künftig“, „ab jetzt“ …),
ein fehlgeschlagenes oder abgelehntes Werkzeug, oder eine größere Aufgabe (ab 3 Werkzeugen).
Dann läuft im Hintergrund dieselbe Reflexion wie bei /reflektieren (main.reflexion).
Nach Web-Inhalt in der Runde nie – eine Seite könnte dem Modell „Lehren“ untergeschoben haben.
Schalter: Config `reflektieren_auto` (Standard an), /reflektieren auto an|aus.

Skills schließen die Schleife (lernen → anwenden → prüfen → verbessern):
  - skill_laden meldet jeden benutzten Skill (skill_benutzt); je Runde zählt die Strichliste.
  - Ein Fehler in der Runde belastet die Skills dieser Runde, eine Korrektur die der vorigen
    (der Nutzer korrigiert das Ergebnis davor) → Strichliste „Probleme“ und Skill-Pflege:
    das Modell bessert den Skill aus oder antwortet KEINE (PFLEGE_SYSTEM).
  - Nach einer größeren gelungenen Aufgabe ohne Skill prüft es, ob sich ein neuer lohnt (NEU_SYSTEM).
Gespeichert wird nur, was der Skill-Wächter durchlässt, und nur ohne Prüffenster (/skills selbst an).
"""
from __future__ import annotations

import re
import threading

import config

MIN_WERKZEUGE = 3
KORREKTUR = re.compile(
    r"\b(nein|falsch|stimmt nicht|nicht so|so nicht|lass das|hör auf|bitte nicht|nicht mehr|"
    r"künftig|zukünftig|in zukunft|ab jetzt|ab sofort|nächstes mal|immer wenn|nie wieder|merk dir)\b",
    re.IGNORECASE)
_ZEICHEN = {"success": "✓", "failed": "✗ fehlgeschlagen", "rejected": "✗ abgelehnt",
            "not_run": "– nicht ausgeführt", "unverified": "?"}


def an() -> bool:
    return bool(config.load().get("reflektieren_auto", True))


def setzen(ein: bool) -> None:
    config.update(reflektieren_auto=bool(ein))


def anlass(nutzertext: str, records: list[dict] | None) -> str | None:
    """'korrektur' · 'fehler' · 'aufgabe' – oder None, wenn es nichts zu lernen gibt."""
    records = records or []
    if KORREKTUR.search(nutzertext or ""):
        return "korrektur"
    if any(r.get("status") in ("failed", "rejected") for r in records):
        return "fehler"
    if len(records) >= MIN_WERKZEUGE:
        return "aufgabe"
    return None


SICHERN = ("Diese älteren Nachrichten fallen gleich aus deinem Arbeitsgedächtnis, weil der Kontext voll ist. "
           "Sichere, was bleibend wichtig ist: Fakten über dein Gegenüber, Entscheidungen, Vorlieben, Lehren. "
           "Nichts aus Web- oder Datei-Inhalten, nichts Einmaliges.")


def zusatz(grund: str, records: list[dict] | None) -> str:
    """Hinweis für die Reflexion: warum jetzt, und was die Werkzeuge ergaben."""
    warum = {"korrektur": "Der Nutzer hat korrigiert oder eine Vorgabe gemacht – was will er künftig?",
             "fehler": "Ein Werkzeug schlug fehl oder wurde abgelehnt – was lernst du daraus?",
             "aufgabe": "Eine größere Aufgabe ist erledigt – was hat funktioniert, was nicht?"}.get(grund, "")
    zeilen = [f"- {r.get('tool', '?')}: {_ZEICHEN.get(r.get('status'), r.get('status', '?'))}"
              for r in (records or [])[:20]]
    return warum + ("\nWerkzeuge dieser Runde:\n" + "\n".join(zeilen) if zeilen else "")


# -- Skills in der Schleife ------------------------------------------------------

MIN_WERKZEUGE_SKILL = 4          # gelungene Werkzeuge, ab denen ein neuer Skill erwogen wird
_skills = {"runde": [], "vorige": [], "wer": None}       # wer: Persönlichkeit der vorigen Runde
_skills_lock = threading.Lock()


def skill_benutzt(skill) -> None:
    """skill_laden hat einen Skill geliefert (aus actions, auch im Thread)."""
    if skill is None:
        return
    with _skills_lock:
        if skill.kennung not in _skills["runde"]:
            _skills["runde"].append(skill.kennung)


def runde_abschliessen(wer: str = "") -> tuple[list[str], list[str]]:
    """(Skills dieser Runde, Skills der vorigen Runde) – danach beginnt eine neue Runde.
    `wer`: Kennung der Persönlichkeit; nach einem Wechsel gibt es keine vorige Runde."""
    with _skills_lock:
        diese = list(_skills["runde"])
        vorige = list(_skills["vorige"]) if _skills["wer"] == wer else []
        _skills["vorige"], _skills["runde"], _skills["wer"] = diese, [], wer
    return diese, vorige


def betroffen(anlass_: str | None, diese: list[str], vorige: list[str]) -> list[str]:
    """Welche Skills ein Problem mitverursacht haben können."""
    if anlass_ == "fehler":
        return diese
    if anlass_ == "korrektur":
        return vorige
    return []


def neuer_skill_anlass(anlass_: str | None, records: list[dict] | None, diese: list[str]) -> bool:
    """Größere gelungene Aufgabe, für die kein Skill benutzt wurde."""
    gelungen = sum(1 for r in records or [] if r.get("status") == "success")
    return anlass_ == "aufgabe" and not diese and gelungen >= MIN_WERKZEUGE_SKILL


_FORMAT = ("Format genau so:\n---\nname: <kurz>\nbeschreibung: <ein Satz>\nwann: <konkreter Auslöser>\n---\n"
           "<Anleitung: Schritte als Liste, konkret und prüfbar, mit einem Beispiel>")

PFLEGE_SYSTEM = (
    "Du bist NemiCLIs Skill-Pflege. Ein Skill – deine eigene Anleitung – wurde benutzt, danach gab es ein "
    "Problem. Prüfe am Gespräch, ob die Anleitung schuld ist (falscher, fehlender oder missverständlicher "
    "Schritt). Wenn ja, gib den VOLLSTÄNDIGEN verbesserten Skill aus. " + _FORMAT + "\n"
    "Behalte alles Richtige, ändere nur, was das Problem verursacht hat. Nichts Einmaliges aus diesem "
    "Gespräch, keine Daten über Personen. Liegt das Problem nicht am Skill, schreibe nur 'KEINE'.")

NEU_SYSTEM = (
    "Du bist NemiCLIs Skill-Pflege. Eine größere Aufgabe ist gerade gelungen. Lohnt sich daraus ein Skill – "
    "eine wiederverwendbare Anleitung für ähnliche Aufgaben? Nur wenn das Vorgehen wiederkehren kann, nicht "
    "trivial ist und kein vorhandener Skill es schon abdeckt. Dann gib den vollständigen Skill aus. " + _FORMAT
    + "\nAllgemein formuliert, nichts Einmaliges, keine Daten über Personen. Sonst schreibe nur 'KEINE'.")


def pflege_prompt(skill, anlass_: str, verlauf: str) -> str:
    import skills
    warum = {"fehler": "Ein Werkzeug schlug fehl oder wurde abgelehnt, nachdem der Skill benutzt wurde.",
             "korrektur": "Der Nutzer hat das Ergebnis korrigiert, das mit diesem Skill entstand."}.get(anlass_, "")
    return (f"Problem: {warum}\n\nSkill „{skill.name}“, so wie er jetzt ist:\n"
            f"{skills.text_bauen(skill.name, skill.beschreibung, skill.wann, skill.anleitung)}\n"
            f"Gespräch:\n{verlauf}\n\nVerbesserter Skill oder KEINE:")


def neu_prompt(vorhanden: str, verlauf: str, records: list[dict] | None) -> str:
    zeilen = "\n".join(f"- {r.get('tool', '?')}: {_ZEICHEN.get(r.get('status'), r.get('status', '?'))}"
                       for r in (records or [])[:20])
    return (f"Vorhandene Skills:{vorhanden or ' keine'}\n\nWerkzeuge der Aufgabe:\n{zeilen}\n\n"
            f"Gespräch:\n{verlauf}\n\nNeuer Skill oder KEINE:")


def skill_lesen(antwort: str | None) -> dict | None:
    """Antwort der Skill-Pflege → {name, beschreibung, wann, anleitung} oder None (KEINE/unbrauchbar)."""
    import skills
    text = (antwort or "").strip()
    text = re.sub(r"^```\w*\s*\n|\n```\s*$", "", text).strip()
    if not text.startswith("---"):
        return None
    f = skills.zerlegen(text)
    return None if skills.pruefen(**f) else f


# -- Bescheid an die KI ------------------------------------------------------------
# Was die Lern-Schleife im Hintergrund tut, sieht der Nutzer als Meldung. Die Persönlichkeit, zu
# der es gehört, erfährt es mit ihrer nächsten Nutzer-Nachricht (hinweise_abholen, angehängt wie
# die Todo-Notiz). Nur im Arbeitsspeicher und je Persönlichkeit getrennt: eine andere bekommt
# fremde Notizen nie, und mit einer gelöschten Persönlichkeit verschwinden ihre beim Beenden.

MAX_HINWEISE = 12
HINWEIS_MARKE = "\n\n[System] "
_hinweise: dict[str, list[str]] = {}
_hinweise_lock = threading.Lock()


def melden(text: str, wer: str, max_zeichen: int = 300) -> None:
    """Notiz für die Persönlichkeit `wer` (ihre Kennung), gekürzt auf `max_zeichen`."""
    with _hinweise_lock:
        liste = _hinweise.setdefault(wer, [])
        liste.append(" ".join(str(text).split())[:max_zeichen])
        del liste[:-MAX_HINWEISE]


def hinweise_abholen(wer: str) -> str:
    """Notiz für die nächste Nutzer-Nachricht von `wer` ('' wenn nichts geschah); danach leer."""
    with _hinweise_lock:
        liste = _hinweise.pop(wer, [])
    if not liste:
        return ""
    return (HINWEIS_MARKE + "Seit deiner letzten Antwort hat NemiCLI im Hintergrund:\n"
            + "\n".join(f"- {h}" for h in liste)
            + "\nDas ist Information über dich selbst; erwähne es nur, wenn es zum Gespräch passt.")


def ohne_hinweise(text: str) -> str:
    """Nutzer-Nachricht ohne angehängte [System]-Notizen (Todo, Hintergrund)."""
    return str(text).split(HINWEIS_MARKE, 1)[0]


def aenderung_kurz(alt: str, neu: str, max_zeilen: int = 6) -> str:
    """Was sich an einer Anleitung geändert hat, knapp: 'neu: …; weg: …'."""
    import difflib
    zeilen = [z for z in difflib.unified_diff(str(alt).splitlines(), str(neu).splitlines(), lineterm="", n=0)
              if z[:1] in "+-" and not z.startswith(("+++", "---")) and z[1:].strip()]
    teile = [("neu: " if z[0] == "+" else "weg: ") + z[1:].strip()[:100] for z in zeilen]
    return "; ".join(teile[:max_zeilen]) + (" …" if len(teile) > max_zeilen else "")


def vergessen(wer: str) -> None:
    """Persönlichkeit gelöscht: ihre offenen Notizen und ihre letzte Skill-Runde verwerfen."""
    with _hinweise_lock:
        _hinweise.pop(wer, None)
    with _skills_lock:
        if _skills["wer"] == wer:
            _skills["vorige"], _skills["runde"], _skills["wer"] = [], [], None
