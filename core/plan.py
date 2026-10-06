"""
plan.py - Gelbe Todo-Liste: vor jeder Aktion steht fest, was die KI vorhat.

Bevor die KI in einer Nutzer-Runde eine Aktion ausführt, schreibt sie mit dem
Werkzeug `plan` die Schritte auf. Ohne Liste führt das Programm keine Aktion aus.
Ausnahme: der Coding-Assistent, der seine eigene Todo-Liste führt (coding.py).

Die Liste gilt, bis alle Punkte erledigt oder gestrichen sind – auch über mehrere Nutzer-Runden
(lange Serien, „weiter“). Ein neuer Chat verwirft sie. Sie liegt nur im Speicher.

Status eines Punktes:
    offen       🔴  noch nicht begonnen
    (aktuell)   🟠  der erste offene Punkt – daran wird gerade gearbeitet
    erledigt    🟢  von der KI abgehakt
    gestrichen  ⚪  weggelassen (mit Grund)
"""

from __future__ import annotations

import copy

OFFEN, ERLEDIGT, GESTRICHEN = "offen", "erledigt", "gestrichen"
ZEICHEN = {OFFEN: "🔴", ERLEDIGT: "🟢", GESTRICHEN: "⚪"}
IN_ARBEIT = "🟠"
MAX_PUNKTE = 20
MAX_TEXT = 160

# Werkzeuge, die keine Liste brauchen: die Liste selbst und der Coding-Assistent.
# Ohne Liste frei: die Liste selbst und Bedienhilfe (Anleitung lesen, Menü für den Nutzer öffnen).
FREI = {"plan", "coding_start", "todo", "anleitung_lesen", "menue_oeffnen"}

SPERR_TEXT = (
    "Nicht ausgeführt: {tools}. Vor jeder Aktion steht die Todo-Liste. Schreib zuerst auf, "
    "was du vorhast, z. B. {{\"tool\": \"plan\", \"punkte\": [\"Ordner ansehen\", \"Datei anlegen\"]}} – "
    "im selben Zug darfst du direkt danach die Aktion schicken. Hake Punkte mit "
    "{{\"tool\": \"plan\", \"erledigt\": 1}} ab.")

_liste: dict | None = None
_gezeigt: tuple | None = None


def runde_neu() -> bool:
    """Neue Nutzer-Runde. Eine Liste mit offenen Punkten bleibt (lange Arbeit über mehrere Runden),
    eine fertige weicht. True = eine offene Liste läuft weiter."""
    global _liste, _gezeigt
    _gezeigt = None
    if _liste is not None and aktuell() is not None:
        return True
    _liste = None
    return False


def verwerfen() -> None:
    """Neuer Chat: keine Liste mitnehmen."""
    global _liste, _gezeigt
    _liste = None
    _gezeigt = None


def abhaken_nach(act: dict, ok) -> bool:
    """Feld `abhaken` an einer Aktion: Punkt(e) sofort abhaken, wenn sie geklappt hat.
    True, wenn sich die Liste geändert hat."""
    if ok is not True or _liste is None or act.get("tool") == "plan" or act.get("abhaken") in (None, ""):
        return False
    nrn = _nummern(act.get("abhaken"))
    if nrn == "alle" or not nrn:
        return False
    vorher = marke()
    for p in _liste["punkte"]:
        if p["nr"] in nrn and p["status"] == OFFEN:
            p["status"] = ERLEDIGT
    return marke() != vorher


def aktiv() -> bool:
    return _liste is not None


def daten() -> dict | None:
    return copy.deepcopy(_liste) if _liste is not None else None


def aktuell(d: dict | None = None) -> dict | None:
    d = _liste if d is None else d
    if not d:
        return None
    return next((p for p in d["punkte"] if p["status"] == OFFEN), None)


def zeichen(p: dict, d: dict | None = None) -> str:
    a = aktuell(d)
    if a is not None and p["nr"] == a["nr"]:
        return IN_ARBEIT
    return ZEICHEN.get(p["status"], "?")


def zaehlen(d: dict | None = None) -> tuple[int, int]:
    """(erledigt, gesamt ohne gestrichene)."""
    d = _liste if d is None else d
    if not d:
        return 0, 0
    gueltig = [p for p in d["punkte"] if p["status"] != GESTRICHEN]
    return sum(p["status"] == ERLEDIGT for p in gueltig), len(gueltig)


def marke() -> tuple | None:
    """Fortschritts-Stand; ändert er sich, beginnt die Schrittbremse neu."""
    if _liste is None:
        return None
    return tuple((p["text"], p["status"]) for p in _liste["punkte"])


def neu_seit_anzeige() -> bool:
    return _liste is not None and marke() != _gezeigt


def angezeigt() -> None:
    global _gezeigt
    _gezeigt = marke()


def _texte(wert) -> list[str]:
    if isinstance(wert, str):
        wert = wert.splitlines()
    if not isinstance(wert, list):
        return []
    raus = []
    for t in wert:
        t = " ".join(str(t.get("text", "") if isinstance(t, dict) else t).split())
        t = t.lstrip("-•*0123456789.) ").strip()
        if t:
            raus.append(t[:MAX_TEXT])
    return raus


def _nummern(wert) -> list[int] | str:
    if wert is None or wert == "":
        return []
    if isinstance(wert, str) and wert.strip().lower() in ("alle", "all"):
        return "alle"
    if not isinstance(wert, list):
        wert = str(wert).replace(",", " ").split() if isinstance(wert, str) else [wert]
    raus = []
    for n in wert:
        try:
            raus.append(int(n))
        except (TypeError, ValueError):
            pass
    return raus


def ausfuehren(a: dict) -> tuple[str, bool]:
    """Werkzeug `plan`. Felder: punkte (neue Liste), neu (anhängen),
    erledigt / streichen (Nummern oder "alle"), grund (zum Streichen)."""
    global _liste
    punkte = _texte(a.get("punkte"))
    if punkte:
        _liste = {"punkte": [{"nr": i, "text": t, "status": OFFEN, "notiz": ""}
                             for i, t in enumerate(punkte[:MAX_PUNKTE], 1)]}
    elif a.get("punkte") is not None:
        return "Fehler: 'punkte' ist leer. Schreib die Schritte als Liste von kurzen Sätzen.", False
    if _liste is None:
        return ("Fehler: Es gibt noch keine Todo-Liste. Leg sie an mit "
                "{\"tool\": \"plan\", \"punkte\": [\"…\", \"…\"]}."), False
    for t in _texte(a.get("neu")):
        if len(_liste["punkte"]) >= MAX_PUNKTE:
            break
        _liste["punkte"].append({"nr": len(_liste["punkte"]) + 1, "text": t, "status": OFFEN, "notiz": ""})

    unbekannt = []
    for feld, status in (("streichen", GESTRICHEN), ("erledigt", ERLEDIGT)):
        nrn = _nummern(a.get(feld))
        if nrn == "alle":
            nrn = [p["nr"] for p in _liste["punkte"] if p["status"] == OFFEN]
        for nr in nrn:
            p = next((p for p in _liste["punkte"] if p["nr"] == nr), None)
            if p is None:
                unbekannt.append(str(nr))
                continue
            p["status"] = status
            if status == GESTRICHEN:
                p["notiz"] = " ".join(str(a.get("grund") or "").split())[:MAX_TEXT]
    text = text_fuer_modell()
    if unbekannt:
        text += f"\n(Punkt {', '.join(unbekannt)} gibt es nicht.)"
    return text, True


def text_fuer_modell(d: dict | None = None) -> str:
    d = _liste if d is None else d
    if not d:
        return ""
    erl, ges = zaehlen(d)
    zeilen = [f"🟨 Todo · {erl}/{ges} erledigt"]
    for p in d["punkte"]:
        z = f"{zeichen(p, d)} {p['nr']}. {p['text']}"
        if p["status"] == GESTRICHEN and p.get("notiz"):
            z += f" (gestrichen: {p['notiz']})"
        zeilen.append(z)
    return "\n".join(zeilen)


def kurzstand() -> str:
    """Eine Zeile für die Rückmeldung an die KI nach ausgeführten Aktionen."""
    if _liste is None:
        return ""
    erl, ges = zaehlen()
    a = aktuell()
    if a is None:
        return f"🟨 Todo: {erl}/{ges} erledigt – alles abgehakt."
    return (f"🟨 Todo: {erl}/{ges} erledigt · jetzt {a['nr']}. {a['text']} – "
            "fertige Punkte mit plan \"erledigt\" abhaken.")


def zurueckweisen(acts: list[dict], frei: bool = False) -> tuple[list[dict], list[dict]]:
    """Teilt die Aktionen eines Zuges in (laufen, gesperrt). Eine Aktion läuft, wenn
    schon eine Liste besteht oder im selben Zug vorher eine angelegt wird.
    frei=True (Coding-Assistent an): alles läuft."""
    if frei:
        return list(acts), []
    liste_da = _liste is not None
    laufen, gesperrt = [], []
    for act in acts:
        tool = act.get("tool")
        if tool == "plan" and _texte(act.get("punkte")):
            liste_da = True
        if tool in FREI or liste_da:
            laufen.append(act)
        else:
            gesperrt.append(act)
    return laufen, gesperrt


def sperr_text(gesperrt: list[dict]) -> str:
    tools = ", ".join(dict.fromkeys(str(a.get("tool", "?")) for a in gesperrt))
    return SPERR_TEXT.format(tools=tools)
