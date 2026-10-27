"""pruefer.py – Gegenprobe: passt die fertige Antwort der KI zu dem, was in der Runde wirklich geschah?

Grundlage ist die Werkzeug-Bilanz der Runde (records: tool + status), nie der Inhalt einer Ausgabe.
Drei Muster:
1. Ein veränderndes Werkzeug ist gescheitert oder wurde abgelehnt – die Antwort klingt nach Erfolg.
2. Die Antwort sagt „ich habe … gespeichert/gelöscht/…“, in der Runde lief aber kein veränderndes Werkzeug.
3. Die Werkzeuge der Runde sind gescheitert (keins hat geklappt) – die Antwort erwähnt keinen Fehler.
   Nur Ablehnungen reichen dafür nicht: die hat der Nutzer selbst entschieden.
4. Die Antwort kündigt eine Tat an („ich urteile sie alle harmlos“), in der Runde lief aber gar kein Werkzeug.
„Nicht ausgeführt“ zählt nicht: das entscheidet NemiCLI selbst (Wiederholung, Todo-Liste, Grenze) und
sagt es dem Modell schon im Ergebnis.
Gefunden wird nur ein Verdacht; die KI prüft ihn selbst noch einmal im Denken (eine Gegenprobe je Runde).
"""

from __future__ import annotations

import re

_TATEN = (r"gespeichert|geschrieben|angelegt|erstellt|gelöscht|entfernt|installiert|verschoben|kopiert|"
          r"bewertet|beurteilt|geurteilt|markiert|"
          r"geändert|aktualisiert|eingetragen|umbenannt|behoben|repariert|ausgeführt|gestartet|gemalt|erzeugt")
_ERFOLG = re.compile(rf"(?i)\b(erledigt|fertig|geschafft|hat geklappt|klappt jetzt|funktioniert jetzt|{_TATEN})\b|✅")
_MISSERFOLG = re.compile(
    rf"(?i)fehlgeschlagen|gescheitert|scheiterte|misslungen|nicht geklappt|ging nicht|hat nicht funktioniert|"
    rf"konnte\b[^.!?\n]{{0,30}}\bnicht|\bnicht\b[^.!?\n]{{0,30}}\b({_TATEN})\b|leider|abgelehnt|"
    rf"fehlermeldung|\b(ein|einen|einem|mit)\s+fehler\b|fehler\w*\s+(aufgetreten|gemeldet|kam)|"
    rf"nicht ausgeführt|nicht erlaubt|gesperrt|nicht gefunden|❌|⚠")
_ICH_TAT = re.compile(rf"(?i)\bich\s+(habe|hab)\b[^.!?\n]{{0,80}}?\b({_TATEN})\b")
_ANKUENDIGUNG = re.compile(r"(?i)\bich\s+(urteile|bewerte|beurteile|markiere|lösche|speichere|schreibe|"
                           r"erstelle|installiere|verschiebe)\b")
# Nur mit einem Gegenstand, den ein Werkzeug bearbeitet – sonst wäre jedes „ich schreibe dir gleich“ ein Verdacht.
_WERKZEUG_DING = re.compile(r"(?i)\b(alarme?|harmlos|echt|gesehen|datei(en)?|ordner|skills?|notiz(en)?|einträge?|"
                            r"pakete?|programme?|bilder?)\b")
_SPAETER = re.compile(r"(?i)\b(morgen|später|nachher|demnächst|irgendwann|sonst)\b")
_NACHFRAGE = re.compile(r"(?i)\?|\bsoll ich\b|\bdarf ich\b|\bmöchtest du\b|\bwillst du\b|\bwenn du\b")
_FRUEHER = re.compile(r"(?i)\b(vorhin|gestern|vorher|zuvor|bereits|schon|letztes mal|davor|früher|eben|"
                      r"neulich|heute morgen|letzte runde)\b")
_GESCHEITERT = {"failed": "fehlgeschlagen", "rejected": "vom Nutzer abgelehnt"}
_INTERN = {"plan", "todo", "coding_start", "menue_oeffnen", "subagenten", "recherche"}

HINWEIS = ("\n\n[System] 🔎 Gegenprobe von NemiCLI – deine Antwort passt vielleicht nicht zu dem, was in dieser "
           "Runde wirklich passiert ist:\n{punkte}\n"
           "Prüf das jetzt im Denken: Was zeigen die Werkzeug-Ergebnisse oben wirklich? Stimmt deine Antwort? "
           "Wenn nicht: sag es dem Nutzer ehrlich und korrigiere sie (ohne lange Entschuldigung). Wenn doch: "
           "bestätige in einem Satz, warum es passt. Eine neue Aktion nur, wenn sie zum Beheben nötig ist.")


def _endstand(records: list[dict]) -> dict[str, str]:
    """Letzter Status je Werkzeug – ein späterer Erfolg desselben Werkzeugs gilt."""
    stand: dict[str, str] = {}
    for r in records:
        tool = str(r.get("tool") or "?")
        status = str(r.get("status") or "")
        if tool not in _INTERN and status != "not_run":
            stand[tool] = status
    return stand


def _saetze(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?\n])\s+", text or "") if s.strip()]


def pruefen(antwort: str, records: list[dict], lese_werkzeuge: frozenset | set = frozenset()) -> list[str]:
    """Verdachtsmomente (leer = alles stimmig)."""
    antwort = antwort or ""
    if not antwort.strip():
        return []
    stand = _endstand(records)
    veraendernd = {t: s for t, s in stand.items() if t not in lese_werkzeuge}
    ehrlich = bool(_MISSERFOLG.search(antwort))
    befunde = []
    offen = {t: s for t, s in veraendernd.items() if s in _GESCHEITERT}
    if offen and not ehrlich and _ERFOLG.search(antwort):
        befunde.append("Deine Antwort klingt nach Erfolg, aber: " + ", ".join(
            f"`{t}` {_GESCHEITERT[s]}" for t, s in offen.items()) + ".")
    getan = any(s in ("success", "unverified") for s in veraendernd.values())
    if not getan and not offen:
        for satz in _saetze(antwort):
            m = _ICH_TAT.search(satz)
            if m and not _FRUEHER.search(satz) and not _MISSERFOLG.search(satz):
                befunde.append(f"Du schreibst „{satz.strip()[:120]}“ – in dieser Runde lief aber kein Werkzeug, "
                               "das das getan hätte.")
                break
    gescheitert = [t for t, s in stand.items() if s == "failed"]
    if gescheitert and all(s in _GESCHEITERT for s in stand.values()) and not ehrlich and not befunde:
        befunde.append("Kein Werkzeug dieser Runde hat geklappt ("
                       + ", ".join(f"`{t}` fehlgeschlagen" for t in gescheitert) + "), deine Antwort erwähnt das nicht.")
    if not stand and not befunde:
        for satz in _saetze(antwort):
            if (_ANKUENDIGUNG.search(satz) and _WERKZEUG_DING.search(satz) and not _NACHFRAGE.search(satz)
                    and not _SPAETER.search(satz)):
                befunde.append(f"Du kündigst an: „{satz.strip()[:120]}“ – in dieser Runde lief aber kein Werkzeug. "
                               "Führe es jetzt aus oder sag, warum nicht.")
                break
    return befunde


def hinweis(befunde: list[str]) -> str:
    return HINWEIS.format(punkte="\n".join(f"- {b}" for b in befunde))
