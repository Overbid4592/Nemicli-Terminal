"""Lernstoff: erzeugt Einrichtungs-Gespräche (eine Runde je Zeile) aus handgeschriebenen Bausteinen.

Eingaben des Nutzers bekommen Tippfehler, Kleinschreibung und Smileys, damit der Helfer auch ungenaue Eingaben
versteht. Namen kommen aus zwei getrennten Töpfen (Training/Prüfung): so misst die Prüfung, ob das Modell
Namen wirklich abschreibt, statt sie auswendig zu kennen.

Aufruf: python -m einrichtungshelfer.daten <ordner> [--anzahl 60000]
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from einrichtungshelfer import daten_notfall
from einrichtungshelfer.schema import FEHLER, beispiel, zustand

# ---------------------------------------------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------------------------------------------
_VORNAMEN = """Anna Ben Lena Jonas Mia Paul Emma Leon Hannah Finn Sophie Noah Marie Elias Lea Felix Laura Luca
Julia Max Sarah Tim Lisa Jan Nina Tom Katharina Lukas Johanna Moritz Clara Niklas Amelie Simon Charlotte Erik
Frieda Oskar Greta Anton Ida Emil Mila Henri Lina Theo Ella Jakob Nele Karl Paula Fabian Ronja Matteo Zoe Samuel
Alina Vincent Leni Linus Romy Till Carla Malte Pia Jannik Selin Aaron Yara Mehmet Ayse Ali Elif Can Deniz Emre
Leyla Omar Fatma Yusuf Mira Ivan Olga Pavel Katja Nikolai Irina Marco Giulia Lorenzo Chiara Pedro Ines Diego
Lucia Pierre Chloe Louis Camille Jamal Amina Kofi Zara Hugo Elin Sven Freya Lars Ingrid Bjarne Tilda Kai Jule
Mats Wiebke Hauke Frauke Dörte Jörg Björn Jürgen Günter Ursula Brigitte Heinz Gisela Uwe Sabine Thorsten Petra
Dirk Anja Ralf Silke Kerstin Holger Bernd Monika Andreas Stefan Michael Thomas Christian Daniel Alexander
Martin Markus Sebastian Florian Tobias Dennis Patrick Kevin Jennifer Jessica Vanessa Nadine Melanie Kathrin
Sandra Nicole Tanja Carina Svenja Marlene Rosa Ruth Agnes Mathilda Konstantin Leopold Ferdinand Benedikt Quentin
Xaver Zacharias Yannick Valentin Ulrich Wolfgang Horst Edith Erna Hedwig Helga Waltraud Gerda Luise Merle Juna
Enno Ole Piet Rasmus Smilla Elsa Alva Liv Nora Tessa Fiona Leonie Marlon Jona Levi Mattis Hanna Lotta Lilly""".split()

_SPITZNAMEN = """Schatz Süßer Süße Chef Chefin Boss Kapitän Captain Meister Hase Sonnenschein Prinzessin Kumpel Doc
Professor Maus Bär Tiger Sternchen Krümel Held Heldin Champion Liebling Spatz Mausi Bärchen Kleiner Kleine
Großer Kollege Kollegin Partner Partnerin Käpt'n Majestät Sheriff Admiral Zauberer Hexe Ninja Pirat
Piratin""".split()

_SILBEN_A = "Ka Lo Mi Ra Ti Ne Zu Fe Jo Va Be Ri Sa Do Lu Pe Ma Ki Ny Ro Xa Qui El An Or Ul Ys".split()
_SILBEN_B = "ra lin to na mo vi ke sa ro dan ja mir lo tis ven ka ri nu bel sol ar en ix om".split()

_KONSONANTEN = "b c d f g h j k l m n p r s t v w z ch sch st tr br kl fl gr ph th x y qu".split()
_VOKALE = "a e i o u ä ö ü ai ei au ie ea ia io y".split()

_PERSONEN = """Nemi Aurora Luna Nova Kira Mika Juna Selma Fenja Ylvi Rika Sumi Aiko Nala Elara Iris
Vega Mira Tali Sola Lyra Kaja Pina Ayla Rhea Thea Cleo Zoe Neva Ivy Faye Wren Juno Kiki Momo Nico Rune Kenji
Orin Taro Eli Bo Sam Ari Lumi Navi Pixel Echo Sky Robin""".split()

_HELFER_NAMEN = """Pip Funke Fips Pünktchen Bolt Kiko Wusel Tüftel Flinki Sprotte Kobold Bit Byte Momo Klecks
Nuss Purzel Zwirbel Fluff Socke""".split()


def _namenstoepfe(rng: random.Random) -> tuple[list[str], list[str]]:
    erfunden = {rng.choice(_SILBEN_A) + rng.choice(_SILBEN_B) + rng.choice(["", "", "a", "o", "i", "e"])
                for _ in range(400)}
    alle = sorted(set(_VORNAMEN) | erfunden)
    rng.shuffle(alle)
    teil = len(alle) * 85 // 100
    return alle[:teil], alle[teil:]


# ---------------------------------------------------------------------------------------------------------------
# Tippfehler und Stil des Nutzers
# ---------------------------------------------------------------------------------------------------------------
_UMLAUT = {"ä": ("ae", "a"), "ö": ("oe", "o"), "ü": ("ue", "u"), "ß": ("ss", "s"),
           "Ä": ("Ae", "A"), "Ö": ("Oe", "O"), "Ü": ("Ue", "U")}
_ENDEN = ["", "", "", "", " =)", " xD", " :)", " <3", "!", " 😊", "...", " :D", "?", " 🙂", " haha", " =) <3"]


def tippfehler(text: str, rng: random.Random) -> str:
    if rng.random() < 0.55:
        text = text.lower()
    elif rng.random() < 0.3 and text:
        text = text[0].upper() + text[1:]
    if rng.random() < 0.3:
        text = "".join(rng.choice(_UMLAUT[c]) if c in _UMLAUT else c for c in text)
    zeichen = list(text)
    for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
        if len(zeichen) < 4:
            break
        i = rng.randrange(1, len(zeichen) - 1)
        if not zeichen[i].isalpha():
            continue
        art = rng.random()
        if art < 0.35:
            del zeichen[i]                                     # Buchstabe vergessen
        elif art < 0.65 and zeichen[i + 1].isalpha():
            zeichen[i], zeichen[i + 1] = zeichen[i + 1], zeichen[i]   # vertauscht
        elif art < 0.85:
            zeichen.insert(i, zeichen[i])                      # doppelt
        elif zeichen[i] in "ie":
            zeichen.insert(i, "e" if zeichen[i] == "i" else "i")  # ie/ei-Verwechslung
    text = "".join(zeichen)
    if rng.random() < 0.15:
        text = text.replace(",", "").replace(".", "")
    return text


def nutzer(vorlage: str, rng: random.Random, **slots: str) -> str:
    """Vorlage mit Tippfehlern, danach die Slots (Namen bleiben so, wie der Nutzer sie schreibt)."""
    stuecke = re.split(r"(\{[a-z]+\})", vorlage)
    klein = rng.random() < 0.55
    text = "".join(slots.get(s[1:-1], s) if s.startswith("{") and s.endswith("}")
                   else tippfehler(s.lower() if klein else s, rng) for s in stuecke)
    return text + rng.choice(_ENDEN)


def w(rng: random.Random, *listen: list[str]) -> str:
    """Je Liste ein Stück, leere weglassen, mit Leerzeichen verbinden."""
    return " ".join(s for s in (rng.choice(liste) for liste in listen) if s)


# ---------------------------------------------------------------------------------------------------------------
# Bausteine je Schritt
# ---------------------------------------------------------------------------------------------------------------
_HALLO = ["Hallo! 👋", "Hey, schön, dass du da bist! 😊", "Hallo und herzlich willkommen! ✨", "Hi! Willkommen bei NemiCLI! 👋",
          "Hallöchen! 😊", "Willkommen! 💜", "Hey! 👋", "Schön, dass du NemiCLI ausprobierst! ✨"]
_VORSTELLEN = ["Ich bin {ich}, dein kleiner Einrichtungs-Helfer.", "Ich heiße {ich} und helfe dir beim Einrichten.",
               "Ich bin {ich}. Ich begleite dich durch die Einrichtung.", "Mein Name ist {ich} – ich bin fürs Einrichten da.",
               "Ich bin {ich}, ein kleiner Helfer nur für die Einrichtung."]
_ABLAUF = ["Wir gehen das zusammen Schritt für Schritt durch.", "Das geht ganz schnell, versprochen.",
           "Wenn ich etwas nicht weiß, sage ich es dir ehrlich.",
           "Keine Sorge, ich erkläre dir alles.", "Wir machen das ganz in Ruhe.", "", ""]
_FRAGE_ANREDE = ["Wie soll ich dich nennen?", "Wie darf ich dich nennen?", "Verrätst du mir, wie ich dich nennen soll?",
                 "Wie heißt du denn?", "Wie möchtest du genannt werden?", "Wie soll ich dich ansprechen?"]
_NOCHMAL_ANREDE = ["Und wie soll ich dich nennen?", "Also – wie darf ich dich nennen?", "Wie soll ich dich denn nennen?",
                   "Verrätst du mir jetzt deinen Namen?"]

_ANREDE_EIN = ["{n}", "{n}", "ich bin {n}", "ich heiße {n}", "ich heisse {n}", "mein name ist {n}", "nenn mich {n}",
               "nenn mich einfach {n}", "sag {n} zu mir", "du kannst mich {n} nennen", "{n} reicht", "einfach {n}",
               "ich bin der {n}", "ich bin die {n}", "hey ich bin {n}", "hallo, ich bin {n}", "{n} heiß ich",
               "man nennt mich {n}", "ruf mich {n}", "{n} bitte", "am liebsten {n}", "alle sagen {n} zu mir",
               "hi, {n} hier", "hier ist {n}", "ich bin's, {n}", "nenn mich bitte {n}", "{n} wäre schön",
               "sag einfach {n}", "mein name? {n}", "ich heiß {n}", "kannst {n} sagen", "{n} ist mein name"]
_ANREDE_ZWEI = ["ich heiße {v}, aber nenn mich {n}", "eigentlich {v}, aber sag lieber {n}",
                "mein name ist {v} aber alle sagen {n}", "{v}, aber {n} ist mir lieber", "ich bin {v}, sag aber {n}",
                "heiße {v} aber nenn mich ruhig {n}", "{v} – aber für dich {n}"]
_ANREDE_EGAL = ["egal", "ist mir egal", "wie du willst", "such du aus", "lass so", "lass einfach so",
                "mein normaler name passt", "nimm einfach meinen windows namen", "weiß nicht, egal", "keine ahnung, egal",
                "brauchst du nicht", "will ich nicht sagen", "lieber nicht", "nimm den namen vom pc", "passt schon so"]
_ANREDE_OK = ["Freut mich, {name}! 😊", "Hallo {name}, schön dich kennenzulernen!", "Super, dann nenne ich dich ab jetzt {name}. ✨",
              "Alles klar, {name}! Gemerkt. 💜", "Schön, {name}! Das merke ich mir.", "{name} – wie schön! 😊",
              "Hallo {name}! 👋", "Gemerkt: {name}. ✨"]
_ANREDE_EGAL_OK = ["Alles klar, dann nehme ich den Namen von deinem Windows-Konto: {name}.",
                   "Kein Problem! Dann bleibe ich erst mal bei {name}.", "Okay, dann nenne ich dich {name}, wie dein Windows-Konto.",
                   "Gar kein Problem. Ich nehme {name}, das steht in deinem Windows-Konto."]
_SPAETER_AENDERN = ["Du kannst das später jederzeit ändern – sag es einfach deiner Persönlichkeit.",
                    "Ändern geht jederzeit: Sag es einfach deiner Persönlichkeit oder nutze /name.", "", "", ""]

_PERS_EINSTIEG = ["Jetzt suchst du dir eine Persönlichkeit aus. 🎭", "Als Nächstes: Mit wem möchtest du reden? 🎭",
                  "Weiter geht's mit der Persönlichkeit.", "Jetzt kommt was Schönes: deine Persönlichkeit! ✨"]
_PERS_EINSTIEG_NAME = ["So, {name}, jetzt suchst du dir eine Persönlichkeit aus. 🎭", "{name}, mit wem möchtest du reden? 🎭",
                       "Weiter geht's, {name}: deine Persönlichkeit! ✨"]
_PERS_ERKLAER = ["Das ist die KI, mit der du später redest – jede hat ihren eigenen Charakter.",
                 "Jede Persönlichkeit hat ihre eigene Art zu reden.", "Das ist sozusagen der Charakter deiner KI.", ""]
_PERS_LISTE = ["Diese gibt es schon: {liste}.", "Zur Auswahl stehen: {liste}.", "Hier sind sie: {liste}.", "Da sind: {liste}."]
_PERS_FRAGE = ["Welche möchtest du? Du kannst auch eine eigene anlegen.", "Welche nimmst du – oder willst du eine ganz eigene?",
               "Such dir eine aus, oder sag „neu“ für eine eigene.", "Welche gefällt dir? Eine eigene geht auch."]
_NOCHMAL_PERS = ["Welche Persönlichkeit möchtest du?", "Also, welche nimmst du? {liste}.", "Welche darf es sein?",
                 "Zur Wahl stehen: {liste} – oder eine eigene."]

_PERS_EIN = ["{p}", "{p}", "{p} bitte", "nimm {p}", "ich nehme {p}", "ich will {p}", "{p} klingt gut", "ich nehm {p}",
             "die {p}", "lass uns {p} nehmen", "ich mag {p}", "{p} natürlich", "gerne {p}", "{p} bitte schön",
             "ich hätte gern {p}", "{p}!", "für mich {p}", "{p} ist toll", "ich wähle {p}", "{p} nehm ich"]
_PERS_NEU = ["ich will eine eigene", "eine neue bitte", "neu", "ich möchte eine eigene machen",
             "keine davon, ich bau mir eine", "kann ich eine eigene erstellen?", "eigene", "eine eigene bitte",
             "ich will meine eigene ki", "lieber selbst eine machen", "neue anlegen"]
_PERS_EGAL = ["egal", "such du aus", "ist mir egal", "irgendeine", "weiß nicht, nimm einfach eine", "mir egal",
              "du entscheidest", "nimm irgendwas", "überrasch mich"]
_PERS_OK = ["Schöne Wahl! {pers} freut sich auf dich. 😊", "{pers} – gute Wahl! ✨", "Alles klar, {pers} ist ab jetzt für dich da.",
            "Super! Dann redest du ab jetzt mit {pers}. 💜", "Toll, {pers} ist es! 🎭"]
_PERS_NEU_OK = ["Wie schön! Deine eigene Persönlichkeit legen wir gleich nach der Einrichtung an. ✨",
                "Klasse! Die eigene kommt direkt nach der Einrichtung dran – ich merke es mir.",
                "Oh, eine eigene! 😊 Die baust du gleich nach der Einrichtung."]
_PERS_EGAL_OK = ["Dann nehmen wir erst mal {pers}. Wechseln kannst du jederzeit.", "Ich such dir {pers} aus. 😊 Wechseln geht jederzeit.",
                 "Okay, {pers} ist es – fürs Erste. Du kannst jederzeit wechseln."]
_PERS_UNBEKANNT = ["Die kenne ich leider nicht. 🙈", "Hm, die gibt es hier noch nicht.", "Die finde ich nicht."]

_MODELL_EINSTIEG = ["Jetzt das Wichtigste: das Gehirn deiner KI. 🧠", "Nächster Schritt: Womit soll deine KI denken? 🧠",
                    "Jetzt geht's ums Sprach-Modell – das Gehirn deiner KI. 🧠"]
_MODELL_EINSTIEG_NAME = ["{name}, jetzt kommt das Wichtigste: das Gehirn deiner KI. 🧠", "So, {name}: Womit soll deine KI denken? 🧠"]
_CLOUD = ["Cloud heißt: Die KI läuft bei einem Anbieter im Internet, dafür brauchst du einen API-Schlüssel.",
          "Bei Cloud denkt die KI auf den Rechnern eines Anbieters – du brauchst dafür einen API-Schlüssel."]
_LOKAL = ["Lokal heißt: Die KI läuft auf deinem eigenen PC, zum Beispiel über Ollama oder eine GGUF-Datei.",
          "Bei lokal bleibt alles auf deinem PC – über Ollama oder eine GGUF-Datei."]
_MODELL_FRAGE = ["Möchtest du Cloud oder lokal? Du kannst auch „später“ sagen.", "Was ist dir lieber: Cloud, lokal oder erst mal später?",
                 "Cloud oder lokal? Oder machen wir das später?"]
_NOCHMAL_MODELL = ["Also: Cloud, lokal oder später?", "Was möchtest du – Cloud oder lokal?", "Cloud, lokal oder später?"]

_MODELL_EIN = {
    "cloud": ["cloud", "die cloud", "cloud bitte", "ich hab einen api key", "ich hab nen schlüssel von openai",
              "ich nutze claude", "online", "übers internet", "ich will gemini", "ich hab einen deepseek key",
              "anthropic", "cloud natürlich", "ich hab schon einen key", "mit api schlüssel", "openai",
              "ich nehm die cloud", "lieber cloud", "cloud, ich hab nen key"],
    "lokal": ["lokal", "auf meinem pc", "offline", "ohne internet", "ich hab ollama", "ich will lokal",
              "auf meiner grafikkarte", "lokal bitte", "gguf", "ich hab eine gguf datei", "privat auf meinem rechner",
              "lieber lokal", "alles auf meinem pc", "lokal natürlich", "mit ollama", "ich will nix in der cloud"],
    "spaeter": ["später", "weiß ich noch nicht, später", "mach ich später", "überspringen", "skip", "erstmal nicht",
                "nachher", "lass das erst mal", "später bitte", "das mach ich danach"],
}
_MODELL_OK = {
    "cloud": ["Gut! Den Schlüssel trägst du nach der Einrichtung ein – ich öffne dir dann das passende Menü. 🔑",
              "Cloud, alles klar! Nach der Einrichtung zeige ich dir, wo der API-Schlüssel hinkommt. 🔑",
              "Prima! Den API-Schlüssel tragen wir gleich nach der Einrichtung ein."],
    "lokal": ["Lokal, schön! Dann bleibt alles auf deinem PC. Das Modell suchst du dir nach der Einrichtung aus. 💻",
              "Alles klar, lokal! Nach der Einrichtung öffne ich dir das Menü für dein Modell. 💻",
              "Super, lokal! Welches Modell, suchst du dir gleich danach aus."],
    "spaeter": ["Kein Problem! Das geht jederzeit später – sag es einfach oder nutze /model.",
                "Okay, machen wir später. Mit /model geht das jederzeit.", "Gar kein Ding – später geht immer, mit /model."],
}
_MODELL_UNSICHER_EIN = ["weiß nicht", "was ist besser", "keine ahnung", "was empfiehlst du", "hm schwer",
                        "was würdest du nehmen", "was ist der unterschied", "erklär nochmal"]
_MODELL_UNSICHER = ["Beides hat Vorteile: Cloud ist sofort startklar und sehr stark. Lokal bleibt alles auf deinem PC, "
                    "braucht aber eine gute Grafikkarte.",
                    "Cloud: sofort startklar, du brauchst nur einen API-Schlüssel. Lokal: alles bleibt bei dir, "
                    "dafür braucht dein PC etwas Kraft.",
                    "Wenn du schon einen API-Schlüssel hast, ist Cloud am einfachsten. Willst du alles auf deinem PC "
                    "behalten, nimm lokal."]

_PRUEF_EINSTIEG = ["Ich habe mir deinen PC angeschaut. 🔍", "So, ich habe nachgeschaut, was schon da ist. 🔍",
                   "Fertig mit Nachschauen! 🧰", "Ich war mal neugierig und habe nachgeschaut. 🔍"]
_OFFEN = {
    "python": ["ein eigenes Python", "Python (das hole ich mir selbst)"],
    "venv": ["eine eigene Arbeits-Umgebung (venv)", "das venv, eine eigene Arbeits-Umgebung"],
    "pakete": ["ein paar Pakete", "einige Python-Pakete"],
}
_OFFEN_SATZ = ["Es fehlen noch: {x}.", "Noch nicht da sind: {x}.", "Fehlen tun noch: {x}."]
_OFFEN_BERUHIGEN = ["Das kann ich selbst installieren – an deinem Windows ändere ich dabei nichts.",
                    "Das erledige ich für dich, alles bleibt im NemiCLI-Ordner.", "Das mache ich gern für dich."]
_OFFEN_FRAGE = ["Soll ich loslegen?", "Darf ich das jetzt installieren?", "Soll ich das für dich installieren?",
                "Soll ich das gleich erledigen?"]
_NOCHMAL_PRUEF = ["Soll ich das Fehlende jetzt installieren?", "Also – soll ich loslegen?", "Darf ich installieren?"]
_ALLES_DA = ["Alles, was ich selbst installieren kann, ist schon da! ✨", "Die Grundlagen sind alle schon da! ✨",
             "Super, alles Wichtige zum Installieren ist schon da! ✨"]
_FEHLT = {
    "torch": ["torch, den Rechen-Motor für Bilder und lokale Modelle, installierst du gleich selbst im venv-Fenster – "
              "ich zeige dir, wie.", "Für Bilder und lokale Modelle fehlt noch torch. Das machst du gleich selbst im "
              "venv-Fenster, ich helfe dir dabei."],
    "ollama": ["Ollama läuft gerade nicht – das brauchst du nur für lokale Modelle über Ollama.",
               "Ollama ist nicht aktiv. Das ist kein Muss, NemiCLI kann auch GGUF-Dateien selbst laden."],
    "sprachmodell": ["Ein Sprach-Modell fehlt noch – das richten wir danach ein.",
                     "Dein KI-Gehirn fehlt noch – darum kümmern wir uns gleich danach."],
    "gedaechtnismodell": ["Fürs Gedächtnis fehlt ein Embedding-Modell. Das ist kein Muss.",
                          "Ein Embedding-Modell fürs Gedächtnis fehlt noch – das geht auch später."],
    "bildmodell": ["Zum Malen fehlt noch Krea 2. Das ist auch kein Muss.", "Krea 2 zum Malen ist noch nicht da – nur nötig, "
                   "wenn du Bilder malen willst."],
}
_JA = ["ja", "ja bitte", "mach", "los", "klar", "jo", "jap", "ok", "okay", "leg los", "ja mach das", "gerne", "jaaa",
       "sicher", "mach ruhig", "yes", "jup", "na klar", "mach mal", "bitte ja", "auf jeden fall", "logo", "jawohl"]
_NEIN = ["nein", "nö", "lieber nicht", "jetzt nicht", "nee", "nicht installieren", "mach ich selbst", "nein danke",
         "nope", "noch nicht", "erst mal nicht", "ne lass mal"]
_INSTALL_JA = ["Prima, ich lege los! Das kann ein paar Minuten dauern. ⏳", "Los geht's! Ich sage Bescheid, wenn ich fertig bin. ⏳",
               "Super, ich installiere jetzt. Kurz Geduld bitte. ⏳"]
_INSTALL_NEIN = ["Okay, dann lassen wir das erst mal. Mit /einrichten geht es jederzeit weiter.",
                 "Kein Problem! Du kannst das später mit /einrichten nachholen.", "Alles klar, ich installiere nichts."]

_TORCH_EINSTIEG = ["Jetzt fehlt nur noch torch, der Rechen-Motor für Bilder und lokale Modelle. ⚙",
                   "Noch eine Sache: torch, der Rechen-Motor für deine Grafikkarte. ⚙"]
_TORCH_ERKLAER = ["Den installierst du selbst: Ich öffne dir ein Fenster mit NemiCLIs venv und zeige dir den passenden Befehl.",
                  "Das machst du selbst in einem kleinen Fenster – den passenden Befehl zeige ich dir dort."]
_TORCH_FRAGE = ["Soll ich das Fenster öffnen?", "Fenster aufmachen?", "Soll ich dir das venv-Fenster öffnen?"]
_NOCHMAL_TORCH = ["Soll ich das venv-Fenster öffnen?", "Also – Fenster öffnen?", "Fenster auf?"]
_TORCH_JA = ["Das Fenster geht auf! Gib dort den Befehl ein und starte NemiCLI danach einmal neu. 🔄",
             "Ich öffne es! Befehl eingeben, warten, dann NemiCLI neu starten. 🔄"]
_TORCH_NEIN = ["Kein Problem. Das Fenster öffnest du später jederzeit mit /venv.", "Okay! Mit /venv geht das jederzeit."]

_ENDE_HURRA = ["Geschafft! 🎉", "Fertig! Die Einrichtung ist durch. ✨", "Das war's schon! 🎉", "Juhu, fertig! 🎉"]
_ENDE_HURRA_NAME = ["Geschafft, {name}! 🎉", "Fertig, {name}! Die Einrichtung ist durch. ✨", "Das war's schon, {name}! 🎉"]
_ENDE_MODELL = {
    "cloud": ["Jetzt öffne ich dir das Menü für deinen API-Schlüssel. 🔑", "Gleich kommt das Menü für deinen API-Schlüssel. 🔑"],
    "lokal": ["Jetzt öffne ich dir das Menü, in dem du dein lokales Modell aussuchst. 💻",
              "Gleich kommt das Menü für dein lokales Modell. 💻"],
    "spaeter": ["Ein Modell richtest du jederzeit mit /model ein.", "Fürs Modell: /model, wann immer du magst."],
}
_ENDE_TORCH = ["Denk an torch im venv-Fenster (/venv), wenn du malen willst.", "torch kannst du jederzeit mit /venv nachholen."]
_ENDE_WIEDER = ["Wenn etwas fehlt: /einrichten bringt mich jederzeit zurück.", "Du kannst mich jederzeit mit /einrichten wieder rufen.",
                "Brauchst du mich nochmal? /einrichten genügt."]
_ENDE_TSCHUESS = ["Viel Spaß mit NemiCLI! 💜", "Ich wünsch dir ganz viel Freude! 😊", "Hab ganz viel Spaß! ✨", "Bis bald! 👋"]

# Fragen, die in jedem Schritt kommen können: (Eingaben, Antworten)
_FAQ: dict[str, tuple[list[str], list[str]]] = {
    "wer": (["wer bist du", "was bist du", "bist du eine ki", "bist du chatgpt", "wie heißt du", "wer redet da",
             "bist du ein mensch"],
            ["Ich bin {ich}, ein ganz kleiner Helfer – nur für die Einrichtung von NemiCLI gemacht. 😊",
             "Ich bin {ich}! Eine kleine KI, nur fürs Einrichten trainiert.", "Ich bin {ich}, NemiCLIs eigener kleiner Einrichtungs-Helfer.",
             "Ich bin {ich} – klein, aber ehrlich. Ich bringe NemiCLI für dich zum Laufen. ✨"]),
    "schluessel": (["was ist ein api schlüssel", "was ist ein api key", "wo bekomme ich einen key", "wozu brauch ich den schlüssel",
                    "wo trage ich den key ein", "was ist ein token key", "brauch ich einen api key"],
                   ["Ein API-Schlüssel ist wie ein Ausweis für einen Cloud-Anbieter. Du bekommst ihn auf der Seite des "
                    "Anbieters und trägst ihn in NemiCLI ein. 🔑",
                    "Den API-Schlüssel gibt dir der Cloud-Anbieter auf seiner Seite. Eintragen geht nach der Einrichtung – "
                    "später jederzeit mit /model.", "Nur für Cloud-KI brauchst du einen API-Schlüssel – lokal geht es ohne."]),
    "ollama": (["was ist ollama", "brauch ich ollama", "wo krieg ich ollama", "muss ich ollama installieren"],
               ["Ollama ist ein Programm, das KI-Modelle auf deinem PC laufen lässt. NemiCLI kann aber auch GGUF-Dateien "
                "selbst laden – Ollama ist also kein Muss.", "Ollama ist nur ein Weg für lokale Modelle. Es geht auch ohne, "
                "mit GGUF-Dateien."]),
    "venv": (["was ist ein venv", "was ist eine arbeitsumgebung", "wozu venv", "was ist venv", "was soll das venv"],
             ["Ein venv ist wie ein eigenes Zimmer für NemiCLIs Python-Pakete. So kommen sie anderen Programmen nicht in "
              "die Quere. 🚪", "Ein venv ist eine eigene Ecke nur für NemiCLI – die Pakete darin stören sonst niemanden."]),
    "torch": (["was ist torch", "wozu torch", "brauch ich torch", "was ist pytorch", "warum installiere ich torch selbst"],
              ["torch ist der Rechen-Motor für alles, was auf deiner Grafikkarte läuft: Bilder, Videos und lokale Modelle. "
               "Fürs Chatten mit einer Cloud-KI brauchst du ihn nicht.",
               "torch rechnet auf deiner Grafikkarte. Er ist groß, darum installierst du ihn selbst im venv-Fenster – dann "
               "siehst du genau, was passiert."]),
    "unterschied": (["was ist der unterschied zwischen cloud und lokal", "cloud oder lokal was ist besser", "cloud oder lokal?",
                     "was heißt lokal", "was heißt cloud"],
                    ["Cloud: Die KI läuft bei einem Anbieter, du brauchst einen API-Schlüssel. Lokal: Die KI läuft auf deinem "
                     "PC, alles bleibt bei dir.", "Bei Cloud denkt ein Anbieter im Internet für dich. Bei lokal denkt dein "
                     "eigener PC – dafür braucht er etwas Kraft."]),
    "persoenlichkeit": (["was ist eine persönlichkeit", "was bringt eine persönlichkeit", "kann ich später wechseln",
                         "kann ich mehrere persönlichkeiten haben"],
                        ["Eine Persönlichkeit ist der Charakter deiner KI – wie sie redet und wer sie ist. Wechseln kannst "
                         "du jederzeit. 🎭", "Jede Persönlichkeit hat ihren eigenen Charakter und ihre eigenen Erinnerungen. "
                         "Du kannst jederzeit wechseln oder neue anlegen."]),
    "dauer": (["wie lange dauert das", "dauert das lange", "wie lang geht das noch", "wie lange noch"],
              ["Meist nur ein paar Minuten. Das Installieren dauert am längsten – das hängt von deiner Internet-Leitung ab. ⏳",
               "Nicht lange! Ein paar Fragen noch, dann das Installieren."]),
    "sicher": (["ist das sicher", "verändert das mein system", "brauch ich admin rechte", "macht das was kaputt",
                "installierst du was in windows"],
               ["Keine Sorge: Alles landet im NemiCLI-Ordner. An deinem Windows ändere ich nichts, und Admin-Rechte brauche "
                "ich nicht. 🛡", "Ganz sicher: Ich arbeite nur im NemiCLI-Ordner, ohne Admin-Rechte."]),
    "grafikkarte": (["brauch ich eine grafikkarte", "reicht meine grafikkarte", "geht das ohne gpu", "welche grafikkarte brauche ich"],
                    ["Für Cloud-KI brauchst du keine besondere Grafikkarte. Für lokale Modelle, Bilder und Videos hilft eine "
                     "NVIDIA-Karte mit viel Grafikspeicher sehr.", "Zum Chatten über Cloud reicht jeder PC. Für lokale Modelle "
                     "und Bilder ist eine starke Grafikkarte gut."]),
    "ordner": (["wo werden meine daten gespeichert", "wo landen die dateien", "wo ist der ordner", "wo liegt alles"],
               ["Alles liegt in deinem NemiCLI-Ordner. Wo der ist, siehst du jederzeit mit /start. 📁",
                "In deinem NemiCLI-Ordner – /start zeigt dir, wo er liegt."]),
    "nochmal": (["kann ich das später ändern", "kann ich das nochmal machen", "kann ich später zurück", "geht das nochmal"],
                ["Klar! Mit /einrichten kommst du jederzeit zu mir zurück.", "Na klar, alles lässt sich später ändern."]),
    "bilder": (["kann nemicli bilder malen", "wie male ich bilder", "was ist krea", "kann ich bilder machen"],
               ["Ja! Zum Malen nutzt NemiCLI Krea 2. Die Dateien kommen in den Ordner Models/Krea2 – dafür brauchst du "
                "auch torch. 🎨", "Klar! Mit Krea 2 malt NemiCLI Bilder. Dafür braucht es torch und die Krea-Dateien."]),
    "internet": (["brauch ich internet", "geht das offline", "geht das ohne internet"],
                 ["Zum Installieren schon. Danach läuft mit einem lokalen Modell alles auch ohne Internet.",
                  "Fürs Einrichten ja – mit einem lokalen Modell geht danach alles offline."]),
    "modelle": (["wo bekomme ich ein modell", "wie lade ich ein modell", "welches modell soll ich nehmen",
                 "woher bekomme ich eine gguf"],
                ["Über /model holst du Modelle von Hugging Face. Welches passt, hängt von deiner Grafikkarte ab – NemiCLI "
                 "hilft dir dabei.", "Modelle gibt es über /model, direkt von Hugging Face."]),
    "verwirrt": (["hilfe", "ich versteh das nicht", "was soll ich machen", "ich bin verwirrt", "häh", "versteh ich nicht"],
                 ["Kein Stress, wir machen das ganz langsam. 😊", "Alles gut, ich helfe dir. 💜", "Keine Sorge, das kriegen wir hin. 😊"]),
    "danke": (["danke", "dankeschön", "danke dir", "lieb von dir"],
              ["Gern geschehen! 💜", "Sehr gerne! 😊", "Immer doch! ✨"]),
}
# Nah an der Einrichtung, aber nicht sicher zu beantworten: ehrlich sagen statt raten.
_UNSICHER = ["welcher anbieter ist der beste", "wie viel grafikspeicher brauche ich genau", "welches modell ist das schlauste",
             "ist meine grafikkarte gut genug für videos", "warum ist mein internet so langsam", "läuft nemicli auf linux",
             "wie schnell wird mein modell sein", "funktioniert das mit meiner amd karte", "welche gguf passt zu mir",
             "wie viel ram brauche ich", "geht das auch auf dem mac", "wird mein pc dadurch langsamer"]
_UNSICHER_ANTWORT = ["Das weiß ich leider nicht sicher – und raten mag ich nicht. 🙈 Deine Persönlichkeit kann das gleich "
                     "genauer nachschauen.", "Ehrlich gesagt: Das weiß ich nicht genau. Lieber sage ich das, als dir etwas "
                     "Falsches zu erzählen. ✨", "Da bin ich mir nicht sicher. Frag das gleich deine Persönlichkeit – die "
                     "kann nachsehen."]

_FREMD = ["wie ist das wetter", "erzähl einen witz", "was ist 2+2", "wer hat die wm gewonnen", "schreib mir ein gedicht",
          "wie spät ist es", "kannst du mir bei hausaufgaben helfen", "was ist der sinn des lebens", "magst du pizza",
          "programmier mir ein spiel", "was kostet bitcoin", "kennst du einen guten film", "wie alt bist du",
          "übersetz das ins englische", "was gibt es neues in der welt"]
_FREMD_ANTWORT = ["Da kann ich dir leider nicht helfen – ich kenne mich nur mit der Einrichtung aus. Das kann gleich "
                  "deine Persönlichkeit! 😊", "Oh, das weiß ich nicht. Ich bin nur fürs Einrichten da – frag das gleich "
                  "deine Persönlichkeit.", "Das ist leider nicht mein Gebiet. 🙈 Deine Persönlichkeit weiß das bestimmt!"]
_ABBRUCH = ["abbrechen", "stopp", "hör auf", "ich will nicht mehr", "beenden", "tschüss", "exit",
            "lass uns später weitermachen", "keine lust mehr", "ich mach das ein andermal", "cancel"]
_ABBRUCH_ANTWORT = ["Alles klar, wir hören hier auf. Mit /einrichten geht es jederzeit weiter. 👋",
                    "Okay! Wenn du magst, ruf mich später mit /einrichten. 👋", "Kein Problem – /einrichten bringt dich zurück. Bis dann! 👋"]
_UNKLAR = ["asdf", "hmm", "?", "ähm", "jkl", "...", "qwertz", "xyz", "öhm", "lol", "k", "äh was"]
_UNKLAR_ANTWORT = ["Hm, das habe ich nicht verstanden. 🙈", "Das verstehe ich leider nicht.", "Oh, da komme ich nicht mit. 🙈"]
_HALLO_EIN = ["hallo", "hi", "hey", "moin", "servus", "guten tag", "huhu", "hallöchen"]

_NOCHMAL = {"anrede": _NOCHMAL_ANREDE, "pers": _NOCHMAL_PERS, "modell": _NOCHMAL_MODELL,
            "pruefung": _NOCHMAL_PRUEF, "torch": _NOCHMAL_TORCH}


# ---------------------------------------------------------------------------------------------------------------
# Erzeuger
# ---------------------------------------------------------------------------------------------------------------
class Lehrer:
    def __init__(self, rng: random.Random, namen: list[str]):
        self.rng = rng
        self.namen = namen

    def nutzer(self, vorlage: str, **slots: str) -> str:
        return nutzer(vorlage, self.rng, **slots)

    def _erfunden(self) -> str:
        """Frischer Fantasiename – kommt praktisch nie zweimal vor, also muss das Modell abschreiben."""
        rng = self.rng
        name = "".join(rng.choice(_KONSONANTEN) + rng.choice(_VOKALE) for _ in range(rng.randint(1, 3)))
        if rng.random() < 0.4:
            name += rng.choice(_KONSONANTEN)
        return name.capitalize() if rng.random() < 0.8 else name

    def _einzelname(self) -> str:
        return self._erfunden() if self.rng.random() < 0.55 else self.rng.choice(self.namen)

    def _name(self) -> str:
        r = self.rng.random()
        if r < 0.1:
            return self.rng.choice(_SPITZNAMEN)
        if r < 0.2:
            return self._einzelname() + " " + self._einzelname()   # Doppelname
        return self._einzelname()

    def _persname(self) -> str:
        return self._erfunden().capitalize() if self.rng.random() < 0.5 else self.rng.choice(_PERSONEN)

    def _personen(self) -> list[str]:
        namen: list[str] = []
        ziel = self.rng.randint(1, 5)
        while len(namen) < ziel:
            n = self._persname()
            if n not in namen:
                namen.append(n)
        return namen

    def _zust_name(self) -> str:
        return self.rng.choice(["ja", "ja", "nein"])

    # --- Fragen (Nutzer leer) ---
    def frage(self, schritt: str) -> str:
        rng = self.rng
        if schritt == "anrede":
            return beispiel(zustand("anrede"), "", w(rng, _HALLO, _VORSTELLEN, _ABLAUF, _FRAGE_ANREDE))
        if schritt == "pers":
            nm = self._zust_name()
            einstieg = _PERS_EINSTIEG_NAME if nm == "ja" and rng.random() < 0.6 else _PERS_EINSTIEG
            text = w(rng, einstieg, _PERS_ERKLAER, _PERS_LISTE, _PERS_FRAGE)
            return beispiel(zustand("pers", name=nm, pers=self._personen()), "", text)
        if schritt == "modell":
            nm = self._zust_name()
            einstieg = _MODELL_EINSTIEG_NAME if nm == "ja" and rng.random() < 0.6 else _MODELL_EINSTIEG
            return beispiel(zustand("modell", name=nm), "", w(rng, einstieg, _CLOUD, _LOKAL, _MODELL_FRAGE))
        if schritt == "pruefung":
            offen = [x for x in ("python", "venv", "pakete") if rng.random() < 0.4]
            fehlt = [x for x in _FEHLT if rng.random() < 0.4]
            teile = [rng.choice(_PRUEF_EINSTIEG)]
            if offen:
                teile.append(rng.choice(_OFFEN_SATZ).replace("{x}", _aufzaehlen([rng.choice(_OFFEN[x]) for x in offen])))
                teile.append(rng.choice(_OFFEN_BERUHIGEN))
            else:
                teile.append(rng.choice(_ALLES_DA))
            teile += [rng.choice(_FEHLT[x]) for x in fehlt]
            if offen:
                teile.append(rng.choice(_OFFEN_FRAGE))
            return beispiel(zustand("pruefung", name=self._zust_name(), offen=offen, fehlt=fehlt), "", " ".join(teile))
        if schritt == "torch":
            return beispiel(zustand("torch", name=self._zust_name()), "", w(rng, _TORCH_EINSTIEG, _TORCH_ERKLAER, _TORCH_FRAGE))
        # ende
        nm = self._zust_name()
        modell = rng.choice(list(_ENDE_MODELL))
        fehlt = [x for x in _FEHLT if rng.random() < 0.35]
        teile = [rng.choice(_ENDE_HURRA_NAME if nm == "ja" and rng.random() < 0.6 else _ENDE_HURRA),
                 rng.choice(_ENDE_MODELL[modell])]
        if "torch" in fehlt:
            teile.append(rng.choice(_ENDE_TORCH))
        teile += [rng.choice(_ENDE_WIEDER), rng.choice(_ENDE_TSCHUESS)]
        return beispiel(zustand("ende", name=nm, modell=modell, fehlt=fehlt), "", " ".join(teile))

    # --- Antworten auf den Schritt selbst ---
    def antwort(self, schritt: str) -> str:
        rng = self.rng
        if schritt == "anrede":
            r = rng.random()
            if r < 0.72:
                n = self._name()
                ein = nutzer(rng.choice(_ANREDE_EIN), rng, n=n)
            elif r < 0.85:
                n, v = self._name(), self._einzelname()
                ein = nutzer(rng.choice(_ANREDE_ZWEI), rng, n=n, v=v)
            else:
                ein = nutzer(rng.choice(_ANREDE_EGAL), rng)
                return beispiel(zustand("anrede"), ein, "[anrede=-] " + w(rng, _ANREDE_EGAL_OK, _SPAETER_AENDERN))
            return beispiel(zustand("anrede"), ein, f"[anrede={n}] " + w(rng, _ANREDE_OK, _SPAETER_AENDERN))
        if schritt == "pers":
            personen, nm = self._personen(), self._zust_name()
            zust = zustand("pers", name=nm, pers=personen)
            r = rng.random()
            if r < 0.55:
                p = rng.choice(personen)
                geschrieben = p.lower() if rng.random() < 0.5 else p
                if rng.random() < 0.15 and len(geschrieben) > 3:      # vertippt
                    i = rng.randrange(1, len(geschrieben) - 1)
                    geschrieben = geschrieben[:i] + geschrieben[i + 1:]
                ein = nutzer(rng.choice(_PERS_EIN), rng, p=geschrieben)
                return beispiel(zust, ein, f"[pers={p}] " + rng.choice(_PERS_OK))
            if r < 0.67:
                return beispiel(zust, nutzer(rng.choice(_PERS_NEU), rng), "[pers=neu] " + rng.choice(_PERS_NEU_OK))
            if r < 0.77:
                return beispiel(zust, nutzer(rng.choice(_PERS_EGAL), rng), "[pers=egal] " + rng.choice(_PERS_EGAL_OK))
            fremd = self._persname()
            while fremd in personen:
                fremd = self._persname()
            ein = nutzer(rng.choice(_PERS_EIN), rng, p=fremd if rng.random() < 0.5 else fremd.lower())
            return beispiel(zust, ein, "[-] " + w(rng, _PERS_UNBEKANNT, _NOCHMAL_PERS))
        if schritt == "modell":
            zust = zustand("modell", name=self._zust_name())
            if rng.random() < 0.12:
                return beispiel(zust, nutzer(rng.choice(_MODELL_UNSICHER_EIN), rng),
                                "[-] " + w(rng, _MODELL_UNSICHER, _NOCHMAL_MODELL))
            art = rng.choice(["cloud", "cloud", "lokal", "lokal", "spaeter"])
            return beispiel(zust, nutzer(rng.choice(_MODELL_EIN[art]), rng), f"[modell={art}] " + rng.choice(_MODELL_OK[art]))
        if schritt == "pruefung":
            zust = zustand("pruefung", name=self._zust_name())
            if rng.random() < 0.6:
                return beispiel(zust, nutzer(rng.choice(_JA), rng), "[installieren=ja] " + rng.choice(_INSTALL_JA))
            return beispiel(zust, nutzer(rng.choice(_NEIN), rng), "[installieren=nein] " + rng.choice(_INSTALL_NEIN))
        # torch
        zust = zustand("torch", name=self._zust_name())
        if rng.random() < 0.6:
            return beispiel(zust, nutzer(rng.choice(_JA), rng), "[venv=ja] " + rng.choice(_TORCH_JA))
        return beispiel(zust, nutzer(rng.choice(_NEIN), rng), "[venv=nein] " + rng.choice(_TORCH_NEIN))

    # --- Nebenwege: Fragen, Fremdes, Abbruch, Unklares ---
    def nebenweg(self, schritt: str) -> str:
        rng = self.rng
        felder: dict[str, object] = {}
        if schritt != "anrede":
            felder["name"] = self._zust_name()
        if schritt == "pers":
            felder["pers"] = self._personen()
        if schritt in ("notfall", "plaudern"):
            felder["fehler"] = "kein_modell" if schritt == "plaudern" else rng.choice(FEHLER)
            nochmal = rng.choice(daten_notfall.nochmal(str(felder["fehler"])))
        else:
            nochmal = rng.choice(_NOCHMAL[schritt])
        zust = zustand(schritt, **felder)
        r = rng.random()
        if r < 0.55:
            ein, aus = _FAQ[rng.choice(list(_FAQ))]
            return beispiel(zust, nutzer(rng.choice(ein), rng), "[-] " + rng.choice(aus) + " " + nochmal)
        if r < 0.64:
            return beispiel(zust, nutzer(rng.choice(_UNSICHER), rng), "[-] " + rng.choice(_UNSICHER_ANTWORT) + " " + nochmal)
        notfall = schritt in ("notfall", "plaudern")      # die große KI ist gerade nicht da
        if r < 0.72:
            antwort = daten_notfall.GROSS_ANTWORT if notfall else _FREMD_ANTWORT
            return beispiel(zust, nutzer(rng.choice(_FREMD), rng), "[-] " + rng.choice(antwort) + " " + nochmal)
        if r < 0.84:
            antwort = daten_notfall.ABBRUCH_ANTWORT if notfall else _ABBRUCH_ANTWORT
            return beispiel(zust, nutzer(rng.choice(_ABBRUCH), rng), "[abbruch] " + rng.choice(antwort))
        if r < 0.94 or schritt != "anrede":
            return beispiel(zust, nutzer(rng.choice(_UNKLAR), rng), "[-] " + rng.choice(_UNKLAR_ANTWORT) + " " + nochmal)
        return beispiel(zust, nutzer(rng.choice(_HALLO_EIN), rng), "[-] " + rng.choice(["Hallo! 😊", "Hey! 👋", "Hi! 😊"])
                        + " " + nochmal)

    def eins(self) -> str:
        rng = self.rng
        bereich = rng.random()
        if bereich < 0.25:
            return self.nebenweg("notfall") if rng.random() < 0.15 else daten_notfall.notfall(self)
        if bereich < 0.4:
            return self.nebenweg("plaudern") if rng.random() < 0.15 else daten_notfall.plaudern(self)
        schritt = rng.choice(["anrede", "anrede", "anrede", "pers", "pers", "modell", "modell", "pruefung", "torch", "ende"])
        if schritt == "ende":
            return self.frage("ende")
        r = rng.random()
        if r < 0.22:
            return self.frage(schritt)
        if r < 0.72:
            return self.antwort(schritt)
        return self.nebenweg(schritt)


def _aufzaehlen(teile: list[str]) -> str:
    return teile[0] if len(teile) == 1 else ", ".join(teile[:-1]) + " und " + teile[-1]


def erzeugen(anzahl: int, seed: int = 7) -> tuple[list[str], list[str]]:
    """(Training, Prüfung). Die Prüfung nutzt nur Namen, die im Training nie vorkommen."""
    rng = random.Random(seed)
    namen_train, namen_test = _namenstoepfe(rng)
    lehrer = Lehrer(rng, namen_train)
    train = [lehrer.eins() for _ in range(anzahl)]
    pruefer = Lehrer(random.Random(seed + 1), namen_test)
    test = [pruefer.eins() for _ in range(max(200, anzahl // 50))]
    return train, test


def main() -> None:
    ap = argparse.ArgumentParser(description="Lernstoff für den Einrichtungs-Helfer erzeugen")
    ap.add_argument("ordner", type=Path)
    ap.add_argument("--anzahl", type=int, default=60_000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    train, test = erzeugen(a.anzahl, a.seed)
    a.ordner.mkdir(parents=True, exist_ok=True)
    for name, daten in (("train.jsonl", train), ("pruefung.jsonl", test)):
        with open(a.ordner / name, "w", encoding="utf-8") as f:
            for zeile in daten:
                f.write(json.dumps({"text": zeile}, ensure_ascii=False) + "\n")
    einzigartig = len(set(train))
    print(f"Training: {len(train)} Runden ({einzigartig} verschiedene) · Prüfung: {len(test)} · {a.ordner}")


if __name__ == "__main__":
    main()
