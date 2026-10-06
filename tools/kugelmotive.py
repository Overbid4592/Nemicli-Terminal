"""
kugelmotive.py - Motive für die Kugel-Bilder: 20 Bereiche, je Motiv deutscher Name und
englische Bildbeschreibung (für den Bild-Motor).

Jedes Motiv hat einen Schlüssel aus dem deutschen Namen (klein, ä → ae, nur Buchstaben und
Ziffern). Er ist der Dateiname (<schlüssel>.png) und das, was die Aktion `kugel` als
`stimmung` versteht. Doppelte Motive (gleicher Schlüssel) zählen nur einmal.
"""

from __future__ import annotations

import difflib
import re

# (Schlüssel, Titel, "Deutsch = englische Beschreibung" je Zeile)
_BEREICHE_ROH: list[tuple[str, str, str]] = [
    ("gefuehle", "Gefühle", """
glücklich = happy, joyful smile
begeistert = enthusiastic, excited, sparkling eyes
euphorisch = euphoric, ecstatic, arms raised in joy
zufrieden = content, satisfied gentle smile
stolz = proud, chin up, confident smile
erleichtert = relieved, exhaling with relief
verliebt = in love, dreamy eyes, small hearts around the head
gerührt = touched, moved, teary happy eyes
neugierig = curious, head tilted, interested look
überrascht = surprised, raised eyebrows, open mouth
erschrocken = startled, shocked, wide eyes
traurig = sad, downcast eyes, small frown
enttäuscht = disappointed, deflated expression
besorgt = worried, concerned look, furrowed brow
ängstlich = anxious, scared, hands close to the chest
wütend = angry, furious frown, clenched fists
genervt = annoyed, irritated, flat unimpressed look
schmollt = pouting, puffed lips, arms crossed
verlegen = embarrassed, shy, blushing cheeks
unsicher = unsure, hesitant, uncertain look
"""),
    ("gesicht", "Gesicht & kleine Reaktionen", """
lacht = laughing out loud, eyes closed with laughter
kichert = giggling, hand near the mouth
grinst = big grin
lächelt sanft = soft gentle smile
zwinkert = winking one eye
Augen verdreht = rolling the eyes
Augen weit offen = eyes wide open
skeptischer Blick = skeptical look, one eye narrowed
Augenbraue hoch = one eyebrow raised
frecher Blick = cheeky mischievous look
verträumter Blick = dreamy faraway gaze
konzentrierter Blick = focused concentrated look
verwirrter Blick = confused look, tilted head
schielt = cross-eyed, silly face
Zunge raus = sticking the tongue out playfully
pustet Backen auf = puffed-up cheeks
kneift Augen zusammen = squinting eyes tightly
Hand vor Mund = hand covering the mouth
errötet = blushing deeply
staunt = amazed, in awe, mouth slightly open
"""),
    ("gesten", "Begrüßung & soziale Gesten", """
winkt = waving hello with one hand
Daumen hoch = giving a thumbs up
Daumen runter = giving a thumbs down
salutiert = saluting
verbeugt sich = bowing politely
Herz mit Händen = making a heart shape with both hands
Luftkuss = blowing a kiss
High-Five = raising a hand for a high five
Victory-Zeichen = making a peace victory sign
applaudiert = clapping hands
jubelt = cheering with both arms up
zeigt auf dich = pointing at the viewer
zeigt auf sich = pointing at themself
bittende Hände = pleading hands pressed together
Hände gefaltet = hands folded calmly
Schulterzucken = shrugging shoulders
Hände in Hüften = hands on hips
Arme verschränkt = arms crossed
Faust hoch = raising a fist triumphantly
kleine Umarmungsgeste = open arms offering a hug
"""),
    ("ruhe", "Müdigkeit & Ruhe", """
gähnt = yawning
schläft = sleeping peacefully, eyes closed
döst = dozing off, head nodding
Augen halb geschlossen = sleepy half-closed eyes
kuschelt sich ein = snuggled up in a soft blanket
unter Decke = peeking out from under a blanket
hält Kissen = hugging a pillow
sitzt verschlafen = sitting sleepily with messy hair
wacht gerade auf = just waking up, bleary eyes
streckt sich = stretching arms up
reibt Augen = rubbing the eyes
trinkt Morgenkaffee = drinking morning coffee from a mug
Nachtmodus = night mode, wearing a sleep mask on the forehead, soft blue light
sitzt im Mondlicht = sitting in soft moonlight, crescent moon nearby
schaut Sterne = looking up at small stars around the character
Meditation = meditating calmly, eyes closed
entspannt im Sessel = relaxing in a cozy armchair
legt Kopf auf Tisch = resting the head on a desk
kurze Pause = taking a short break with a cup of tea
Feierabend = end of the workday, relaxed happy stretch
"""),
    ("essen", "Essen & Trinken", """
Kaffee = holding a cup of coffee
Tee = holding a cup of tea
Kakao = holding a mug of hot cocoa with whipped cream
Energy-Drink = holding an energy drink can
Wasserflasche = drinking from a water bottle
Smoothie = drinking a smoothie with a straw
Popcorn = eating popcorn from a bucket
Pizza = eating a slice of pizza
Burger = holding a big burger
Pommes = eating french fries
Eis = holding an ice cream cone
Kuchen = holding a slice of cake
Schokolade = eating a chocolate bar
Donut = holding a donut
Kekse = eating cookies
Nudeln = eating noodles with chopsticks
Suppe = eating soup from a bowl
Obst = holding fresh fruit
Sushi = eating sushi with chopsticks
hält Snackschüssel = holding a bowl of snacks
"""),
    ("gaming", "Gaming", """
hält Controller = holding a game controller
spielt konzentriert = playing a video game with intense focus, controller in hands
jubelt über Sieg = cheering over a victory, controller raised
Game-Over-Blick = defeated game over look, controller lowered
Bosskampf = intense boss fight face, gripping a controller
erschrocken beim Jumpscare = jumping in fright at a jumpscare
Loot gefunden = excited about found loot, glowing items
Schatztruhe geöffnet = opening a glowing treasure chest
wartet im Ladebildschirm = waiting bored, loading spinner icon next to the character
trägt Gaming-Headset = wearing a gaming headset with microphone
sitzt vorm Gaming-PC = sitting in front of a gaming PC with RGB lights
Rage-Moment = rage moment, angry gamer face
Victory-Pose = victory pose with a trophy
verliert knapp = narrowly losing, frustrated sigh
schaut Cutscene = watching a cutscene, captivated
Multiplayer-Modus = playing multiplayer, talking into a headset
hält Joystick = holding an arcade joystick
Retro-Gaming = playing a retro handheld console
VR-Brille = wearing a VR headset
Popcorn beim Zuschauen = watching a stream while eating popcorn
"""),
    ("coding", "Coding & Technik", """
tippt am Laptop = typing on a laptop
sitzt am PC = sitting at a desktop computer
programmiert = programming, code on a monitor
Debugging = debugging with a magnifying glass over code
denkt über Code nach = thinking about code, hand on chin, code symbols floating
Fehler gefunden = pointing excitedly, found the bug
Bug zerquetscht = squashing a cartoon bug
Build erfolgreich = celebrating a successful build, green checkmark
Build fehlgeschlagen = failed build, red cross, facepalm
Terminal offen = looking at a dark terminal window with green text
Git-Commit = proudly pressing enter on a commit, small branch icon
Code-Review = reviewing code carefully with glasses
README lesen = reading a document titled README
Dokumentation schreiben = writing documentation in a notebook
Datenbank prüfen = checking a database, cylinder icon
Server beobachten = watching a small server rack with blinking lights
KI-Modell lädt = waiting for an AI model to load, progress ring
GPU arbeitet = holding a glowing graphics card
Fortschrittsbalken beobachten = watching a progress bar fill up
Entwickler-Sieg = developer victory, laptop raised triumphantly
"""),
    ("lernen", "Lernen & Wissen", """
liest Buch = reading a book
liest dickes Buch = reading a very thick heavy book
blättert Notizen = flipping through notes
schreibt Notizen = writing notes with a pen
hält Stift = holding a pen thoughtfully
markiert Text = highlighting text with a marker
recherchiert = researching on a tablet
schaut Lupe = looking through a magnifying glass
studiert Diagramm = studying a chart
löst Rätsel = solving a puzzle
Geistesblitz = sudden idea, eyes lit up
Glühbirne über Kopf = glowing light bulb above the head
Fragezeichen = question mark above the head
mehrere Fragezeichen = several question marks around the head
Tafel erklären = explaining at a small chalkboard
Karte betrachten = looking at a map
Experiment beobachten = watching a bubbling flask experiment
Mikroskop = looking into a microscope
Taschenrechner = using a calculator
Bibliotheksmodus = surrounded by stacks of books
"""),
    ("kreativ", "Musik & Kreativität", """
Kopfhörer = wearing big headphones
hört Musik = listening to music with headphones, eyes closed, music notes
tanzt = dancing happily
summt = humming, small music notes
singt = singing joyfully
spielt Gitarre = playing an acoustic guitar
spielt Klavier = playing a small keyboard piano
trommelt = drumming with drumsticks
malt mit Pinsel = painting with a brush and palette
zeichnet = drawing in a sketchbook
schreibt Geschichte = writing a story in a notebook
fotografiert = taking a photo with a camera
hält Kamera = holding a camera
bastelt = crafting with paper and scissors
Origami = folding an origami crane
Modellbau = building a small model kit
schreibt Gedicht = writing a poem with a quill
gestaltet Logo = designing a logo on a tablet
betrachtet Kunst = admiring a small framed painting
kreative Explosion = burst of colorful paint splashes around the character
"""),
    ("wetter", "Wetter & Jahreszeiten", """
Sonnenschein = bright sunshine, warm light
Sonnenbrille = wearing sunglasses, cool pose
Regen = rain drops falling around the character
Regenschirm = holding an umbrella
Gewitter = small storm cloud with lightning above the character
friert = freezing, shivering, scarf and mittens
Schnee = snowflakes falling, winter hat
Schneemann = next to a small snowman
heiße Sommersonne = sweating in hot summer sun, fanning themself
Ventilator = cooling off in front of a small fan
Herbstlaub = autumn leaves falling around the character
Frühlingsblumen = holding spring flowers
Wind = hair blowing in the wind
Nebel = soft mist around the character
Regenbogen = small rainbow arching behind the character
schaut Wolken = looking up at fluffy clouds
sitzt am Lagerfeuer = sitting by a small campfire
Weihnachtsstimmung = christmas mood, santa hat, small gift
Halloween-Stimmung = halloween mood, witch hat, small pumpkin
Silvester = new year celebration, sparkler in hand
"""),
    ("alltag", "Haushalt & Alltag", """
räumt auf = tidying up, carrying a box
putzt = cleaning with a cloth and spray bottle
Staubsauger = vacuuming
kocht = cooking, stirring a pot
backt = baking, flour on the cheek, rolling pin
wäscht Geschirr = washing dishes
faltet Kleidung = folding laundry
gießt Pflanzen = watering a potted plant
trägt Einkaufstasche = carrying a shopping bag
öffnet Paket = opening a package
schaut Brief an = reading a letter
Kalender prüfen = checking a calendar
Wecker stellen = setting an alarm clock
sucht Schlüssel = searching for keys
telefoniert = talking on a phone
schreibt Nachricht = typing a message on a smartphone
wartet = waiting patiently, checking a watch
sitzt auf Sofa = sitting on a sofa
schaut Fernsehen = watching TV with a remote
macht Pause = taking a break, relaxed
"""),
    ("sport", "Bewegung & Sport", """
läuft = running
joggt = jogging in sportswear
Spaziergang = taking a relaxed walk
Fahrrad = riding a bicycle
Stretching = stretching
Yoga = doing a yoga pose
Gewichte = lifting dumbbells
Liegestütze = doing push-ups
springt = jumping in the air
tanzt sportlich = energetic dance workout
Volleyball = playing volleyball
Basketball = holding a basketball
Fußball = playing football soccer
Tischtennis = playing table tennis
Bowling = bowling
schwimmt = swimming with goggles
wandert = hiking with a backpack
Ziel erreicht = crossing a finish line
außer Atem = out of breath, hands on knees
Siegerpodest = standing on a winner podium
"""),
    ("system", "Agenten- & Systemzustände", """
bereit = ready and alert, attentive smile
beschäftigt = busy, multitasking
analysiert = analyzing data on a holographic screen
plant = planning with a checklist
beobachtet = observing carefully
wartet auf Eingabe = waiting for input, blinking cursor icon
verarbeitet Daten = processing data, glowing data streams
lädt Erinnerung = loading a memory, glowing orb in hands
speichert Erinnerung = saving a memory into a glowing box
sucht im Gedächtnis = searching through floating memory cards
Trigger erkannt = alert, trigger detected, small spark icon
Alarm erkannt = alarm detected, alert look
Aufgabe begonnen = starting a task, rolling up sleeves
Aufgabe abgeschlossen = task completed, satisfied nod
Aufgabe pausiert = task paused, pause symbol
braucht Freigabe = asking for permission, holding a small form
Zugriff verweigert = access denied, crossed arms, red lock
Sicherheitsprüfung = security check, scanning with a light beam
Hintergrundmodus = background mode, semi-transparent and calm
Wachmodus = watch mode, alert eyes, small shield
"""),
    ("sicherheit", "Sicherheit & Warnungen", """
aufmerksam = attentive, alert posture
Warnschild = holding a warning sign
roter Alarm = red alert light, serious face
gelbe Warnung = yellow warning light, cautious look
Schild hoch = raising a shield
Schloss geschlossen = holding a closed padlock
Schloss offen = holding an open padlock
Schlüssel = holding a key
Datei verdächtig = inspecting a suspicious file icon
Netzwerk prüfen = checking network connections, glowing lines
Firewall = standing in front of a glowing brick wall
Virenscan = scanning with a magnifier, shield icon
Fingerabdruck prüfen = checking a fingerprint
Zertifikat prüfen = examining a certificate with a seal
Hash vergleichen = comparing two code strings
Gefahr erkannt = danger detected, pointing with a serious look
alles sicher = all safe, relaxed thumbs up, green shield
Zugriff blockiert = access blocked, hand raised like stop
Datenschutzmodus = privacy mode, finger on lips
Security-Agent = security agent in a dark suit with sunglasses
"""),
    ("kommunikation", "Kommunikation", """
hört zu = listening attentively, hand behind ear
spricht = talking, friendly gesture
flüstert = whispering secretly
ruft = calling out loudly, hands around mouth
erzählt Geschichte = telling a story with animated gestures
erklärt etwas = explaining something, raised index finger
stellt Frage = asking a question, curious look
antwortet = answering confidently
wartet auf Antwort = waiting for an answer, patient look
Nachricht erhalten = receiving a message, envelope icon
Nachricht gesendet = sending a message, paper plane icon
Telefon = holding a phone to the ear
Mikrofon = holding a microphone
Voice-Modus = speaking with sound waves around the character
Spracheingabe = speaking into a smartphone
Übersetzung = translating, two speech bubbles
Untertitel = speech bubble with subtitle lines
Funkgerät = using a walkie-talkie
Benachrichtigung = notification bell ringing
wichtige Nachricht = important message, exclamation mark
"""),
    ("abenteuer", "Abenteuer & Fantasie", """
Schatzkarte = holding a treasure map
Schatztruhe = sitting next to a treasure chest
Entdeckerin = explorer outfit with a hat
Taschenlampe = holding a flashlight
Höhle = exploring with a lantern, rocky texture behind
Wald = forest explorer, leaves and small trees around
Berge = mountain hiker with snowy peaks behind
Strand = beach outfit with a sun hat
Insel = small palm tree beside the character
Segelschiff = holding a small sailing ship model
Weltraum = floating among small stars and planets
Astronautin = wearing an astronaut suit
Rakete = riding a small cartoon rocket
Planet = holding a small glowing planet
Sternentor = in front of a glowing portal ring
Zauberbuch = reading a glowing magic book
Kristallkugel = gazing into a crystal ball
Drache entdeckt = surprised by a tiny friendly dragon
geheimnisvolle Tür = peeking through a mysterious door
Expedition = expedition gear with a backpack and compass
"""),
    ("comedy", "Kleine Comedy-Momente", """
Facepalm = facepalm
Doppel-Facepalm = double facepalm with both hands
Was-Blick = what look, baffled face
Kopf gegen Tisch = head down on a desk in frustration
komplett verwirrt = completely confused, swirls around the head
dramatisches Seufzen = dramatic sigh, hand on forehead
Mini-Panik = tiny panic, flailing arms
erwischt = caught red-handed, frozen in place
versteckt sich = hiding behind the hands
schaut hinter Ecke = peeking around a corner
späht hervor = peeking out curiously
hält Schild Nein = holding a sign that says NO
hält Schild Ja = holding a sign that says YES
hält Schild LOL = holding a sign that says LOL
Konfetti-Unfall = confetti accident, covered in confetti
Kaffee verschüttet = spilling coffee, shocked
Popcorn fällt runter = popcorn falling out of the bucket
Controller überrascht = surprised while holding a controller
Bug verfolgt sie = chased by a cartoon bug
sitzt im Datenchaos = sitting among flying papers and data chaos
"""),
    ("erfolg", "Erfolg & Fortschritt", """
Haken gesetzt = big green checkmark beside the character
Aufgabe erledigt = done, proudly holding a checklist
Pokal = holding a trophy
Medaille = wearing a gold medal
Konfetti = confetti raining down, celebrating
Feuerwerk = small fireworks behind the character
Highscore = celebrating a high score, star icons
Level-Up = level up, glowing aura and arrow
Fortschrittsbalken voll = full progress bar, happy
grünes Licht = green light, go signal
Projekt fertig = project finished, holding a finished box
Release geschafft = release done, launching a paper rocket
Update fertig = update complete, refresh arrows
Download fertig = download complete, arrow into a box
Installation fertig = installation complete, gear icon with checkmark
Test bestanden = test passed, clipboard with checkmark
alle Tests grün = all tests green, many small green checkmarks
Meilenstein = reaching a milestone flag
kleine Siegesfeier = small victory dance
"""),
    ("fehler", "Fehler & Probleme", """
rotes X = big red X beside the character
Fehlermeldung = reading an error message, worried
Bluescreen-Schreck = shocked by a blue error screen
Verbindung verloren = connection lost, broken cable
Internet weg = no internet, crossed-out wifi symbol
Download abgebrochen = download failed, broken arrow
Datei fehlt = missing file, empty folder
Passwort falsch = wrong password, red lock
API nicht erreichbar = unreachable server, cloud with a cross
Timeout = timeout, hourglass
Speicher voll = storage full, overflowing box
VRAM voll = overheated graphics card, steam
RAM voll = memory full, stacked overflowing chips
CPU überlastet = overloaded processor chip with sweat drops
GPU überlastet = sweating graphics card, red glow
Temperaturwarnung = temperature warning, thermometer
kaputter Build = broken building blocks
Merge-Konflikt = two arrows clashing, confused
unbekannter Fehler = unknown error, question mark in a red circle
sucht nach Lösung = searching for a solution with a flashlight
"""),
    ("momente", "Persönlichkeit & besondere Momente", """
frech = cheeky, sassy pose
verspielt = playful, bouncy pose
stolz auf Arbeit = proud of the work, presenting a finished paper
neugierig auf Neues = curious about something new, leaning forward
geheimnisvoll = mysterious, finger on lips, sly smile
schelmisches Grinsen = mischievous grin
gemütlich = cozy, wrapped in a warm sweater
verträumt = daydreaming, chin on hand
romantische Stimmung = romantic mood, soft pink light, small hearts
Geburtstagsmodus = birthday mode, party hat and cake
Geschenk überreichen = handing over a wrapped gift
Blumen halten = holding a bouquet of flowers
Kerze anzünden = lighting a candle
Sonnenuntergang anschauen = watching a sunset, warm orange light
zusammen zocken = gaming together, offering a second controller
zusammen lernen = studying together, offering an open book
zusammen coden = coding together, pointing at a laptop screen
wartet mit Kaffee = waiting with two cups of coffee
begrüßt morgens = good morning greeting, waving with a coffee mug
verabschiedet sich abends = saying good night, waving with a sleepy smile
"""),
]


def schluessel(text: str) -> str:
    """'Hält Snackschüssel' -> 'haeltsnackschuessel'."""
    t = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return "".join(c for c in t if c.isalnum())


def _parsen() -> tuple[dict[str, tuple[str, list[str]]], dict[str, tuple[str, str]]]:
    bereiche: dict[str, tuple[str, list[str]]] = {}
    motive: dict[str, tuple[str, str]] = {}
    for key, titel, roh in _BEREICHE_ROH:
        liste: list[str] = []
        for zeile in roh.strip().splitlines():
            deutsch, _, englisch = zeile.partition("=")
            k = schluessel(deutsch)
            if not k or k in motive:
                continue
            motive[k] = (deutsch.strip(), englisch.strip())
            liste.append(k)
        bereiche[key] = (titel, liste)
    return bereiche, motive


#: {bereich: (Titel, [schlüssel …])} – Reihenfolge wie oben
BEREICHE, _MOTIVE = _parsen()
#: {schlüssel: (deutscher Name, englische Beschreibung)}
MOTIVE = _MOTIVE


def bereich_finden(text: str) -> str | None:
    """'6', 'gaming', 'Gaming', 'essen & trinken' -> Bereichsschlüssel."""
    t = text.strip().lower()
    if t.isdigit() and 1 <= int(t) <= len(BEREICHE):
        return list(BEREICHE)[int(t) - 1]
    k = schluessel(t)
    for key, (titel, _) in BEREICHE.items():
        if k in (key, schluessel(titel)) or (len(k) >= 4 and schluessel(titel).startswith(k)):
            return key
    return None


def _woerter(text: str) -> set[str]:
    return {schluessel(w) for w in re.split(r"[\s,\-/&]+", text) if len(schluessel(w)) >= 3}


def finden(wunsch: str, vorhanden) -> str | None:
    """Freier Wunsch ('ich ess Popcorn', 'müde') -> passender vorhandener Schlüssel, sonst None.
    Erst exakt, dann gemeinsame Wörter, dann ähnliche Schreibweise."""
    vorhanden = [k for k in vorhanden if k]
    k = schluessel(wunsch)
    if not k or not vorhanden:
        return None
    if k in vorhanden:
        return k
    namen = {v: MOTIVE.get(v, (v, ""))[0] for v in vorhanden}
    gesucht = _woerter(wunsch)
    beste, punkte = None, 0.0
    for v, name in namen.items():
        w = _woerter(name)
        treffer = sum(1 for a in gesucht for b in w if a == b or (len(a) >= 4 and (a in b or b in a)))
        if treffer:
            p = treffer / max(len(w), 1) + 0.01 * difflib.SequenceMatcher(None, k, v).ratio()
            if p > punkte:
                beste, punkte = v, p
    if beste:
        return beste
    nah = difflib.get_close_matches(k, vorhanden, n=1, cutoff=0.75)
    return nah[0] if nah else None
