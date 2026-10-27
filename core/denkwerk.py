"""
denkwerk.py - Nachdenken für jedes Modell, automatisch nach Schwierigkeit.

  Weiche    schätzt jede Nutzernachricht ein: einfach / mittel / schwer (ohne Modellaufruf).
            Ein Wunsch in der Nachricht („denk gründlich nach“, „kurz und knapp“) geht vor.
  Hinweis   geht nur mit der gesendeten Nachricht an das Modell, nie in den Verlauf.
            Modelle ohne eigenes Nachdenken denken in <think>…</think> (die Backends trennen
            das als Denktext ab); Modelle mit eigenem Nachdenken bekommen nur die Vorgehensweise.
            mittel:  Schritt für Schritt (Chain-of-Thought).
            schwer:  zerlegen, zwei bis drei Lösungswege, schwache verwerfen, zusammenführen,
                     selbst nachprüfen (Decomposition, Self-Consistency, Tree of Thoughts).
  Prüfer    eine zweite, unabhängige Anfrage sieht eine schwere fertige Antwort durch
            (Critic/Verifier); findet sie einen Fehler, bessert die KI in einem eigenen Schritt nach.

Config `denkwerk` (Vorgabe an).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

STUFEN = ("einfach", "mittel", "schwer")
PRUEF_MIN_ZEICHEN = 150          # kürzere Antworten prüft der Prüfer nicht
PRUEF_TEXT_MAX = 12000           # so viel von Frage und Antwort sieht der Prüfer
PRUEF_ZEIT_S = 120.0
MARKE = "[Denkwerk]"

_GRUSS = re.compile(r"^\W*(hi|hallo|hey|huhu|moin|servus|guten (morgen|tag|abend)|gute nacht|danke\w*|"
                    r"ok(ay)?|super|cool|top|ja|nein|jo|nö|lol|xd|<3|:\)|=\))\b", re.I)
_GRUENDLICH = re.compile(r"\b(denk\w*|überleg\w*)\s+(\w+\s+){0,3}(gründlich|genau|gut|richtig|scharf|"
                         r"ausführlich|sorgfältig|tief)\w*|\bganz genau\b|\bgründlich\b", re.I)
_KURZ = re.compile(r"\b(denk\w*|überleg\w*)\s+(\w+\s+){0,2}nicht\s+(so\s+)?(lange?|viel)\b|"
                   r"\bkurz und knapp\b|\bohne (lange[sn]? )?(nach)?denken\b|\bnur kurz\b", re.I)

# Merkmale schwerer Aufgaben: je Treffer ein Punkt
_SCHWER = [
    re.compile(r"```|\b(def|class|function|import|return|SELECT|for\s*\(|while\s*\()\b"),
    re.compile(r"\b(code|skript|script|programm\w*|funktion|algorithm\w*|bug|debug\w*|kompilier\w*|"
               r"python|javascript|rust|regex|sql)\b", re.I),
    re.compile(r"\d\s*[-+*/^×÷=]\s*\d|\b(berechne\w*|rechne\w*|ausrechnen|gleichung|wahrscheinlichkeit|"
               r"prozent|integral|ableitung|beweis\w*|formel)\b|%", re.I),
    re.compile(r"\b(warum|wieso|weshalb|erklär\w*|begründ\w*|analysier\w*|vergleich\w*|bewert\w*|"
               r"unterschied\w*|vor- und nachteile|abwäg\w*)\b", re.I),
    re.compile(r"\b(plan\w*|strategie\w*|konzept\w*|architektur|entwirf|entwerfen|optimier\w*|"
               r"schritt für schritt|rätsel|logik\w*|knobel\w*)\b", re.I),
]


@dataclass
class Runde:
    """Was das Denkwerk für eine Nutzer-Runde festgelegt hat."""
    frage: str
    stufe: str
    geprueft: bool = False

    @property
    def schwer(self) -> bool:
        return self.stufe == "schwer"


def an() -> bool:
    try:
        import config
        return bool(config.load().get("denkwerk", True))
    except Exception:
        return True


def wunsch(text: str) -> str | None:
    """Ausdrücklicher Wunsch in der Nachricht: „schwer“ (gründlich) oder „einfach“ (kurz)."""
    if _KURZ.search(text):
        return "einfach"
    if _GRUENDLICH.search(text):
        return "schwer"
    return None


def einschaetzen(text: str) -> str:
    """einfach / mittel / schwer – nach Länge, Fragen und Merkmalen schwerer Aufgaben."""
    t = (text or "").strip()
    if (w := wunsch(t)) is not None:
        return w
    if not t or (len(t) < 40 and _GRUSS.match(t)):
        return "einfach"
    punkte = sum(1 for m in _SCHWER if m.search(t))
    if t.count("?") >= 2 or re.search(r"(?m)^\s*(\d+[.)]|[-*•])\s+\S", t):
        punkte += 1                                     # mehrere Teilfragen
    if len(t) > 600:
        punkte += 1
    if punkte >= 2:
        return "schwer"
    if punkte == 1 or len(t) > 160:
        return "mittel"
    return "einfach"


def runde(text: str) -> Runde | None:
    """Die Einschätzung einer Nutzernachricht; None, wenn das Denkwerk aus ist."""
    if not an():
        return None
    return Runde(frage=text or "", stufe=einschaetzen(text or ""))


_WEG = {
    "mittel": "Denk kurz Schritt für Schritt: Was ist gefragt, was weißt du, was folgt daraus?",
    "schwer": ("Geh so vor: 1) Zerlege die Aufgabe in Teilfragen. 2) Skizziere zwei bis drei verschiedene "
               "Lösungswege und prüfe jeden kurz; verwirf schwache. 3) Führe das Beste zusammen. 4) Prüfe das "
               "Ergebnis selbst: Rechnungen nachrechnen, Code gedanklich ausführen, jede Teilfrage beantwortet, "
               "kein Widerspruch. Braucht es mehrere Schritte mit Werkzeugen, beginne mit `plan`."),
}


def hinweis(r: Runde | None, denkt_selbst: bool) -> str:
    """Der Hinweis zur gesendeten Nachricht ("" bei einfachen Fragen). Ohne eigenes Nachdenken als klare
    Form-Vorgabe am Ende der Nachricht – so befolgt (gemessen mit Gemma 4 12B: davor 0 von 1, danach 2 von 2)."""
    if r is None or r.stufe not in _WEG:
        return ""
    weg = _WEG[r.stufe]
    if denkt_selbst:
        return f"{MARKE} Nutze dein Nachdenken vor der Antwort. {weg}"
    return (f"{MARKE} Beginne deine Antwort mit <think>. Denke dort nach – {weg} – und schließe mit </think>. "
            "Erst danach schreibst du die Antwort für den Nutzer. Aktionsblöcke gehören in die Antwort, nicht ins "
            "Nachdenken.")


def mit_hinweis(text: str, zusatz: str) -> str:
    """Die gesendete Fassung einer Nachricht: Hinweis dahinter."""
    return f"{text}\n\n{zusatz}" if zusatz else text


# ---------------------------------------------------------------------------
# Prüfer
# ---------------------------------------------------------------------------

# Erst selbst prüfen, dann urteilen: mit „antworte nur OK oder FEHLER“ winkte Gemma 4 12B eine falsche Rechnung
# durch; so fand es den Fehler 2 von 2 und ließ Richtiges und eine Geschichte in Ruhe.
PRUEF_SYSTEM = (
    "Du bist ein strenger, unabhängiger Prüfer. Du bekommst eine Frage und eine Antwort darauf. Prüfe die Antwort "
    "selbst: Rechne jede Rechnung nach, geh Logik und Code Schritt für Schritt durch, sieh nach, ob jede Teilfrage "
    "beantwortet ist und ob Fakten stimmen. Stil, Länge und Kleinigkeiten zählen nicht. Geschichten, Rollenspiel, "
    "Gedichte und Meinungen haben keine Fakten-Fehler.\n"
    "Schreib zuerst kurz deine Prüfung. Die letzte Zeile ist das Urteil, genau so:\n"
    "URTEIL: OK\n"
    "oder\n"
    "URTEIL: FEHLER: <höchstens fünf kurze Punkte, jeder mit Begründung>")


def pruefen_noetig(r: Runde | None, antwort: str) -> bool:
    return (r is not None and r.schwer and not r.geprueft
            and len((antwort or "").strip()) >= PRUEF_MIN_ZEICHEN)


def _kuerzen(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …"


def befund_lesen(text: str) -> str | None:
    """Antwort des Prüfers → die Fehlerliste, oder None (in Ordnung / nicht lesbar). Es gilt das letzte
    „URTEIL:“ – mit allem, was danach steht (die Punkte kommen auch mal in eigenen Zeilen); ohne Urteil
    eine Antwort, die mit OK oder FEHLER: beginnt."""
    t = re.sub(r"(?s)<think>.*?</think>", "", text or "").strip()
    urteile = list(re.finditer(r"urteil\s*:", t, re.I))
    if urteile:
        t = t[urteile[-1].end():].strip()
    if not t or re.match(r"^\W*ok\b", t, re.I):
        return None
    m = re.match(r"^\W*fehler\s*:", t, re.I)
    if m is None:
        return None
    rest = t[m.end():].strip()
    return rest[:1500] or None


async def pruefen(backend, r: Runde, antwort: str) -> str | None:
    """Fehlerliste des Prüfers zu einer fertigen Antwort, oder None. Scheitert die Prüfung, gilt sie als bestanden."""
    r.geprueft = True
    frage = _kuerzen(r.frage, PRUEF_TEXT_MAX // 3)
    text = f"FRAGE:\n{frage}\n\nANTWORT:\n{_kuerzen(antwort, PRUEF_TEXT_MAX)}"
    try:
        raus = await asyncio.wait_for(backend.ask_once(text, PRUEF_SYSTEM), PRUEF_ZEIT_S)
    except Exception:
        return None
    return befund_lesen(raus)


def nachbessern(befund: str) -> str:
    """Die Nachricht an die KI, wenn der Prüfer etwas gefunden hat."""
    return ("[System] ⚖️ Ein unabhängiger Prüfer hat deine Antwort durchgesehen und meint:\n"
            f"{befund}\n\n"
            "Prüfe die Einwände selbst. Stimmen sie, schreib die Antwort korrigiert und vollständig noch einmal "
            "(der Nutzer liest nur die neue). Stimmt ein Einwand nicht, sag kurz, warum, und bleib bei deiner Antwort.")


def kurz(befund: str) -> str:
    """Eine Zeile für die Anzeige."""
    zeile = next((z.strip(" -*•\t") for z in befund.splitlines() if z.strip(" -*•\t")), befund)
    return _kuerzen(zeile, 140)
