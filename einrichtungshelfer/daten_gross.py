"""Lernstoff für Funke Groß (Chat-Modell in /model): Gespräche über mehrere Runden mit Denken und Vorgaben.

Format eines Gesprächs:
    <v>name=Funke;anrede=du;laenge=normal;emoji=ja</v><n>…</n><d>Gedanke</d><h>Antwort</h><n>…</n><d>…</d><h>…</h>
Wissen über NemiCLI kommt aus der Befehls-Hilfe (commands.COMMAND_LIST) – beim nächsten Training automatisch aktuell.
Alles andere ist handgeschrieben; keine echten Chats, Namen oder Daten.
"""
from __future__ import annotations

import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from einrichtungshelfer import daten, daten_notfall

# Stichwörter je Befehl (Infinitiv-Gruppen für Fragen wie „wie kann ich …?“)
STICHWORTE: dict[str, list[str]] = {
    "/help": ["alle befehle sehen", "die hilfe öffnen"],
    "/model": ["das modell wechseln", "ein anderes modell nehmen", "einen api schlüssel eintragen", "ein modell von hugging face holen"],
    "/staerke": ["die denk-stärke ändern", "das denkbudget einstellen"],
    "/theme": ["die farben ändern", "ein anderes farbschema nehmen"],
    "/modus": ["den arbeitsmodus wechseln", "in den chat-modus wechseln"],
    "/persönlichkeiten": ["die persönlichkeit wechseln", "eine eigene persönlichkeit anlegen"],
    "/bild": ["ein bild malen", "bilder erstellen lassen"],
    "/video": ["ein video machen", "ein video erzeugen"],
    "/bildmodel": ["das bild-modell wechseln"],
    "/venv": ["torch installieren", "das venv-fenster öffnen"],
    "/wissen": ["sehen, was nemicli gelernt hat"],
    "/gedaechtnis": ["das gedächtnis ansehen", "das gedächtnis löschen"],
    "/embeddings": ["ein embedding-modell wählen"],
    "/reflektieren": ["über das gespräch nachdenken lassen"],
    "/aufraeumen": ["das gedächtnis aufräumen"],
    "/start": ["den nemicli-ordner finden", "sehen, wo meine daten liegen"],
    "/einrichten": ["nemicli einrichten", "fehlendes installieren"],
    "/selbsttest": ["einen selbsttest machen", "prüfen, ob alles läuft"],
    "/statistik": ["meine statistik sehen"],
    "/version": ["die version sehen"],
    "/protokoll": ["das aktions-protokoll sehen"],
    "/doku": ["die offline-doku laden"],
    "/charakter": ["die charakter-datei öffnen"],
    "/kugel": ["die schwebekugel ändern", "das gesicht der kugel malen"],
    "/wache": ["die systemwache einschalten", "die wache ansehen"],
    "/backup": ["ein backup machen", "meine daten sichern"],
    "/undo": ["gelöschtes zurückholen", "den papierkorb öffnen"],
    "/schluessel": ["den programm-ordner für die ki öffnen"],
    "/limit": ["die lösch-grenze einstellen"],
    "/whitelist": ["harmlose befehle freigeben"],
    "/update": ["nemicli aktualisieren", "pakete aktualisieren"],
    "/code": ["mit dem coding-assistenten programmieren"],
    "/codeend": ["den coding-assistenten beenden"],
    "/systemcheck": ["meinen pc prüfen"],
    "/voice": ["die stimme einschalten", "die stimme ausschalten", "mir etwas vorlesen lassen"],
    "/web": ["die web-allowlist sehen"],
    "/name": ["meinen namen eintragen", "einstellen, wie ich genannt werde"],
    "/skills": ["die skills ansehen"],
    "/gui": ["das desktop-fenster öffnen"],
    "/webui": ["die browser-oberfläche öffnen"],
    "/spiel": ["ein spiel spielen", "schach spielen"],
    "/chrome": ["die chrome-erweiterung einrichten"],
    "/emoji": ["die emoji-kürzel sehen"],
    "/uebung": ["den übungsmodus einschalten"],
    "/kontext": ["sehen, wie voll der kontext ist"],
    "/debug": ["den debugbericht ansehen"],
    "/sandbox": ["die sandbox ansehen"],
    "/kontextmenue": ["das rechtsklick-menü einrichten"],
    "/subagenten": ["helfer-agenten einschalten"],
    "/auto": ["auto-stark einschalten"],
    "/reset": ["einen neuen chat starten"],
    "/clear": ["den bildschirm leeren"],
    "/exit": ["nemicli beenden"],
}
_FRAGEN = ["wie kann ich {k}", "ich möchte {k}", "ich will {k}", "wie kann man {k}", "zeig mir, wie ich {k} kann",
           "kann ich {k}", "geht das, {k}", "was muss ich tun, um {k}", "hilf mir, {k}", "wie geht das: {k}"]
_NACHFRAGE = ["und wie genau", "erklär mal genauer", "was heißt das genau", "mehr dazu bitte", "und dann",
              "kannst du das ausführlicher erklären", "wie meinst du das"]

_ALLGEMEIN = ["was ist die hauptstadt von peru", "wie hoch ist der mount everest", "wer war goethe", "wie funktioniert ein motor",
              "was ist 17 mal 23", "wann war der zweite weltkrieg", "wie backe ich einen kuchen", "was ist fotosynthese",
              "wie viele einwohner hat berlin", "erklär mir schwarze löcher", "wer ist der bundeskanzler",
              "was bedeutet carpe diem", "wie wird das wetter morgen", "welcher film ist gerade gut"]
_ALLGEMEIN_ANTWORT = ["Das weiß ich leider nicht sicher – ich kenne mich nur mit NemiCLI aus. 🙈 Ein großes Modell kann dir "
                      "das besser beantworten.", "Ehrlich gesagt: Das weiß ich nicht. Ich bin eine kleine KI nur für NemiCLI – "
                      "lieber sage ich das, als zu raten.", "Da müsste ich raten, und das mag ich nicht. 🙈 Frag das am besten "
                      "ein großes Modell – das findest du in /model."]

_HALLO = ["hallo", "hi", "hey", "moin", "servus", "guten tag", "huhu", "hallöchen", "na du"]
_HALLO_ANTWORT = ["Hallo! Ich bin {name}. 😊 Wobei kann ich dir helfen?", "Hey, {name} hier! ✨ Was kann ich für dich tun?",
                  "Hallöchen! Ich bin {name}. Frag mich alles rund um NemiCLI. 😊"]
_VORSTELLEN = ["ich heiße {x}", "ich bin {x}", "mein name ist {x}", "nenn mich {x}", "hier ist {x}"]
_VORSTELLEN_ANTWORT = ["Freut mich, {x}! 😊", "Schön, dich kennenzulernen, {x}! ✨", "Hallo {x}! Das merke ich mir. 💜"]
_NAME_FRAGE = ["wie heiße ich", "weißt du noch, wie ich heiße", "wie ist mein name", "kennst du meinen namen"]
_NAME_JA = ["Du heißt {x}! 😊", "Klar: {x}. ✨", "Du hast gesagt, du heißt {x}."]
_NAME_NEIN = ["Das hast du mir noch nicht verraten. 🙈 Wie heißt du denn?", "Deinen Namen kenne ich noch nicht – magst du ihn mir sagen?"]
_WER = ["wer bist du", "was bist du", "stell dich vor", "bist du eine ki"]
_WER_ANTWORT = ["Ich bin {name}, eine kleine KI von NemiCLI – selbst trainiert, ohne fremdes Modell. Ich kenne mich mit NemiCLI aus "
                "und plaudere gern. ✨", "Ich bin {name}! Eine kleine, selbst trainierte KI. Ich helfe dir mit NemiCLI – für "
                "Allgemeinwissen bin ich zu klein. 😊"]
_KANNST = ["was kannst du", "wobei hilfst du", "was weißt du alles"]
_KANNST_ANTWORT = ["Ich kenne alle Befehle von NemiCLI, helfe bei Problemen und plaudere ein bisschen. Für Allgemeinwissen, "
                   "Programmieren oder Bilder ist ein großes Modell besser.", "NemiCLI erklären, bei Fehlern helfen, ein wenig "
                   "plaudern – das kann ich. Für alles andere nimm ein großes Modell in /model. ✨"]
_DANKE_ANTWORT = ["Gern geschehen! 💜", "Sehr gerne! 😊", "Immer doch! ✨"]


# ---------------------------------------------------------------------------------------------------------------
# Vorgaben
# ---------------------------------------------------------------------------------------------------------------
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆⏳⌚⌨️‍\U0001F1E6-\U0001F1FF]+")

# du → Sie: ganze Wendungen zuerst, dann Wortformen. Sätze, die danach noch ein Du enthalten, fallen weg.
_SATZANFANG = r"(?:^|(?<=[.!?:–]\s))"
# Befehlsformen am Satzanfang: „Frag …“ → „Fragen Sie …“, reflexiv „Gönn dir“ → „Gönnen Sie sich“
_IMPERATIV = {"frag": "fragen", "sag": "sagen", "nimm": "nehmen", "wähl": "wählen", "schau": "schauen", "prüf": "prüfen",
              "probier": "probieren", "starte": "starten", "lade": "laden", "schließ": "schließen", "gönn": "gönnen",
              "ruh": "ruhen", "atme": "atmen", "ruf": "rufen", "denk": "denken", "gib": "geben", "lass": "lassen",
              "mach": "machen", "sieh": "sehen", "erzähl": "erzählen", "versuch": "versuchen", "lies": "lesen",
              "schreib": "schreiben", "öffne": "öffnen", "tipp": "tippen", "melde": "melden", "trag": "tragen",
              "geh": "gehen", "klick": "klicken", "drück": "drücken", "warte": "warten", "leg": "legen", "hol": "holen",
              "such": "suchen", "kopier": "kopieren"}
_SIE_WENDUNGEN = [
    *[(_SATZANFANG + rf"{du}\s+(?:dir|dich)\b", f"{sie} Sie sich") for du, sie in _IMPERATIV.items()],
    *[(_SATZANFANG + rf"{du}\b", f"{sie} Sie") for du, sie in _IMPERATIV.items()],
    (r"\bes dir verdient\b", "es sich verdient"),
    (r"\bkannst du\b", "können Sie"), (r"\bdu kannst\b", "Sie können"), (r"\bhast du\b", "haben Sie"),
    (r"\bdu hast\b", "Sie haben"), (r"\bbist du\b", "sind Sie"), (r"\bdu bist\b", "Sie sind"),
    (r"\bwillst du\b", "wollen Sie"), (r"\bdu willst\b", "Sie wollen"), (r"\bmöchtest du\b", "möchten Sie"),
    (r"\bdu möchtest\b", "Sie möchten"), (r"\bmusst du\b", "müssen Sie"), (r"\bdu musst\b", "Sie müssen"),
    (r"\bdarfst du\b", "dürfen Sie"), (r"\bdu darfst\b", "Sie dürfen"), (r"\bweißt du\b", "wissen Sie"),
    (r"\bdu weißt\b", "Sie wissen"), (r"\bmagst du\b", "mögen Sie"), (r"\bdu magst\b", "Sie mögen"),
    (r"\bwirst du\b", "werden Sie"), (r"\bdu wirst\b", "Sie werden"), (r"\bsiehst du\b", "sehen Sie"),
    (r"\bdu siehst\b", "Sie sehen"), (r"\bgibst du\b", "geben Sie"), (r"\bdu gibst\b", "Sie geben"),
    (r"\bnimmst du\b", "nehmen Sie"), (r"\bdu nimmst\b", "Sie nehmen"), (r"\bhilfst du\b", "helfen Sie"),
    (r"\bliest du\b", "lesen Sie"), (r"\bdu liest\b", "Sie lesen"), (r"\bheißt du\b", "heißen Sie"),
    (r"\bdu heißt\b", "Sie heißen"), (r"\bdu brauchst\b", "Sie brauchen"), (r"\bbrauchst du\b", "brauchen Sie"),
    (r"\b(\w+?)est du\b", r"\1en Sie"), (r"\bdu (\w+?)est\b", r"Sie \1en"),
    (r"\b(\w+?)st du\b", r"\1en Sie"), (r"\bdu (\w+?)st\b", r"Sie \1en"),
    (r"\bdir\b", "Ihnen"), (r"\bdich\b", "Sie"), (r"\bDir\b", "Ihnen"), (r"\bDich\b", "Sie"),
    (r"\bdeinem\b", "Ihrem"), (r"\bdeinen\b", "Ihren"), (r"\bdeiner\b", "Ihrer"), (r"\bdeines\b", "Ihres"),
    (r"\bdeine\b", "Ihre"), (r"\bdein\b", "Ihr"), (r"\bDeine\b", "Ihre"), (r"\bDein\b", "Ihr"),
]
_DU_REST = re.compile(r"\b(du|Du|dir|dich|dein\w*|Dein\w*)\b")


def siezen(text: str) -> str | None:
    """Du-Text in Sie-Form; None, wenn eine Du-Form übrig bliebe (dann ist das Beispiel für „Sie“ ungeeignet)."""
    def ersetzen(m: re.Match, ersatz: str) -> str:
        neu = m.expand(ersatz)
        return neu[:1].upper() + neu[1:] if m.group(0)[:1].isupper() else neu

    for muster, ersatz in _SIE_WENDUNGEN:
        text = re.sub(muster, lambda m, e=ersatz: ersetzen(m, e), text, flags=re.IGNORECASE)
    return None if _DU_REST.search(text) else text


def ohne_emoji(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", text)).strip()


def _saetze(text: str) -> list[str]:
    """Sätze; der letzte bekommt einen Punkt, falls ihm das Satzzeichen fehlt."""
    saetze = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if saetze and saetze[-1][-1] not in ".!?":
        saetze[-1] += "."
    return saetze


@dataclass
class Vorgaben:
    name: str
    anrede: str = "du"         # du | sie
    laenge: str = "normal"     # kurz | normal | lang
    emoji: str = "ja"          # ja | nein

    def text(self) -> str:
        return f"name={self.name};anrede={self.anrede};laenge={self.laenge};emoji={self.emoji}"

    def gedanke(self) -> str:
        teile = []
        if self.anrede == "sie":
            teile.append("Sie-Form")
        if self.laenge != "normal":
            teile.append(self.laenge)
        if self.emoji == "nein":
            teile.append("ohne Emojis")
        return ("Vorgabe: " + ", ".join(teile) + ".") if teile else ""

    def anwenden(self, text: str) -> str | None:
        if self.laenge == "kurz":
            text = " ".join(_saetze(text)[:1])
        if self.emoji == "nein":
            text = ohne_emoji(text)
        if self.anrede == "sie":
            return siezen(text)
        return text


# ---------------------------------------------------------------------------------------------------------------
# Gespräche
# ---------------------------------------------------------------------------------------------------------------
def _befehle() -> list:
    wurzel = Path(__file__).resolve().parent.parent
    if str(wurzel / "core") not in sys.path:
        sys.path.insert(0, str(wurzel / "core"))
    import commands
    return [c for c in commands.COMMAND_LIST if c.name in STICHWORTE]


@dataclass
class Stand:
    nutzer: str = ""           # Name, den der Nutzer genannt hat
    thema: object = None       # zuletzt besprochener Befehl
    runden: list = field(default_factory=list)


class Gross:
    def __init__(self, rng: random.Random, namen: list[str]):
        self.rng = rng
        self.lehrer = daten.Lehrer(rng, namen)
        self.befehle = _befehle()

    def _n(self, vorlage: str, **slots: str) -> str:
        return daten.nutzer(vorlage, self.rng, **slots)

    def _vorgaben(self) -> Vorgaben:
        rng = self.rng
        name = rng.choice(["Funke"] * 3 + [self.lehrer._persname() for _ in range(2)])
        return Vorgaben(name, rng.choice(["du"] * 3 + ["sie"]), rng.choice(["kurz", "normal", "normal", "lang"]),
                        rng.choice(["ja", "ja", "ja", "nein"]))

    def _befehl_antwort(self, c, v: Vorgaben) -> str:
        kurz = c.short.rstrip(" .")
        if v.laenge == "kurz":
            return f"Das geht mit {c.name}."
        text = f"Dafür gibt es {c.name} – {kurz}."
        if v.laenge == "lang" and c.long:
            text += " " + " ".join(_saetze(c.long)[:2])
        return text

    def runde(self, s: Stand, v: Vorgaben) -> tuple[str, str, str]:
        """(Nutzer, Gedanke, Antwort) – Antwort noch ohne Vorgaben."""
        rng = self.rng
        r = rng.random()
        if not s.runden and r < 0.35:
            return self._n(rng.choice(_HALLO)), "Begrüßung. Ich stelle mich vor.", rng.choice(_HALLO_ANTWORT)
        if r < 0.38 and s.thema is not None:
            c = s.thema
            mehr = " ".join(_saetze(c.long)[:3]) if c.long else ""
            antwort = mehr or f"Mehr weiß ich zu {c.name} leider nicht – probier es am besten einfach aus."
            return self._n(rng.choice(_NACHFRAGE)), f"Mehr zu {c.name} gewünscht.", antwort
        if r < 0.62:
            c = rng.choice(self.befehle)
            s.thema = c
            k = rng.choice(STICHWORTE[c.name])
            return (self._n(rng.choice(_FRAGEN), k=k), f"Frage: {k}. Dafür ist {c.name} da.", self._befehl_antwort(c, v))
        if r < 0.68:
            x = self.lehrer._name()
            s.nutzer = x
            return self._n(rng.choice(_VORSTELLEN), x=x), f"Der Nutzer heißt {x}. Merken.", rng.choice(_VORSTELLEN_ANTWORT)
        if r < 0.72:
            if s.nutzer:
                return self._n(rng.choice(_NAME_FRAGE)), f"Vorhin genannt: {s.nutzer}.", rng.choice(_NAME_JA)
            return self._n(rng.choice(_NAME_FRAGE)), "Name noch nicht genannt. Ehrlich sein.", rng.choice(_NAME_NEIN)
        if r < 0.8:
            thema = rng.choice(list(daten_notfall._PLAUDER))
            ein, aus = daten_notfall._PLAUDER[thema]
            return self._n(rng.choice(ein)), "Plaudern. Freundlich antworten.", rng.choice(aus)
        if r < 0.84:
            return self._n(rng.choice(daten_notfall._FRUST)), "Frust. Erst trösten.", rng.choice(daten_notfall._TROST)
        if r < 0.9:
            ein, aus = daten._FAQ[rng.choice(list(daten._FAQ))]
            return self._n(rng.choice(ein)), "Frage zur Einrichtung. Kurz erklären.", rng.choice(aus)
        if r < 0.95:
            return (self._n(rng.choice(_ALLGEMEIN + daten._UNSICHER)), "Das weiß ich nicht sicher. Nicht raten, ehrlich sagen.",
                    rng.choice(_ALLGEMEIN_ANTWORT))
        if r < 0.97:
            return self._n(rng.choice(_WER)), "Frage, wer ich bin.", rng.choice(_WER_ANTWORT)
        if r < 0.99:
            return self._n(rng.choice(_KANNST)), "Frage, was ich kann.", rng.choice(_KANNST_ANTWORT)
        return self._n(rng.choice(["danke", "dankeschön", "danke dir"])), "Dank.", rng.choice(_DANKE_ANTWORT)

    def gespraech(self, max_runden: int = 5) -> str:
        v = self._vorgaben()
        s = Stand()
        teile = [f"<v>{v.text()}</v>"]
        for _ in range(self.rng.randint(1, max_runden)):
            if sum(len(x) for x in teile) > 1300:          # muss ins Gedächtnis (512 Wortstücke) passen
                break
            nutzer, gedanke, antwort = self.runde(s, v)
            antwort = antwort.replace("{name}", v.name).replace("{x}", s.nutzer).replace("{ich}", v.name)
            fertig = v.anwenden(antwort)
            if fertig is None:                                  # Sie-Form nicht sauber möglich: diese Runde auslassen
                continue
            g = " ".join(x for x in (gedanke, v.gedanke()) if x)
            teile.append(f"<n>{nutzer}</n><d>{g}</d><h>{fertig}</h>")
            s.runden.append(nutzer)
        return "".join(teile) if len(teile) > 1 else self.gespraech(max_runden)


def erzeugen(anzahl: int, seed: int = 11) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    train_namen, test_namen = daten._namenstoepfe(rng)
    g = Gross(rng, train_namen)
    train = [g.gespraech() for _ in range(anzahl)]
    p = Gross(random.Random(seed + 1), test_namen)
    test = [p.gespraech() for _ in range(max(200, anzahl // 50))]
    return train, test
