"""
versionsbericht.py - Was NemiCLI in den letzten Versionen dazugelernt hat – für die KI selbst.

Je Paket ein Eintrag: Version, Titel und was die KI jetzt kann, mit dem Weg dahin (Werkzeug, Befehl,
Feld). Abrufbar jederzeit mit anleitung_lesen thema "neu"; fragt der Nutzer, was neu ist, kommt der
Bericht von selbst mit. Die Projektgeschichte mit allen Einzelheiten steht in CHANGELOG.md.

Mit jedem Paket wird hier ein Eintrag ergänzt (neueste zuerst).
"""
from __future__ import annotations

ANZAHL = 6                       # so viele Versionen zeigt der Bericht

# (Version, Titel, Text) – neueste zuerst
EINTRAEGE: list[tuple[str, str, str]] = [
    ("9.64", "Ein Wecker für dich selbst",
     "erinnern kann jetzt mehr. Mit fuer: \"selbst\" stellst du dir einen eigenen Wecker: Zur Zeit bekommst du den "
     "text als Nachricht und arbeitest ihn ab (z. B. ein Bild malen, auf einer Seite nachsehen und berichten) – "
     "solange ein NemiCLI-Fenster offen ist, sonst beim nächsten Öffnen. Mit alle („2 tage“, „6 stunden“, "
     "„täglich“) wiederholt sich eine Erinnerung. Ohne fuer gilt sie wie bisher dem Nutzer und weckt dich nicht; "
     "sie erscheint jetzt auch im Chat, nicht mehr nur an der Kugel. Was du nach dem Wecken tust, läuft nach dem "
     "Modus des Nutzers – Änderungen fragen wie immer."),
    ("9.63", "Video aus Stable Diffusion",
     "Neuer Video-Motor SD-Video: ein Bild-Checkpoint von Stable Diffusion (SD 1.5 oder SDXL) rechnet mit "
     "Zeit-Schichten (Bewegungs-Modul) Videos – 8 Bilder je Sekunde, ohne Ton, bis 20 s, lange Videos in "
     "überlappenden Fenstern. Jedes Video beginnt dort mit einem Startbild, das die Figur hält: Ohne startbild malt "
     "der Bild-Motor zuerst eines aus dem prompt; bist du selbst im Video (figur: true), entsteht es wie deine "
     "Selbstbilder mit Kern, Seed und Bild-LoRA. Der Nutzer nimmt dafür /video … --figur. Der prompt ist kurz und "
     "englisch wie für ein Bild, mit der Bewegung in einfachen Worten. "
     "Gewählt wird das Modell wie bisher mit /video modell. Das eigene Terminal-Fenster hat jetzt den Befehl "
     "/terminal (auch auf der Einstellungsseite): leuchten, weich, geteilt an|aus wirken sofort, thema "
     "auto|breeze|mint ab dem nächsten Start."),
    ("9.62", "Terminal mit Suche, Blöcken und Leuchten",
     "Das Vollbild und das eigene Terminal-Fenster können mehr – alles bedient der Nutzer selbst: Strg+F sucht im "
     "Verlauf (Enter oder ↑ zum älteren Treffer, ↓ zum neueren, Esc schließt). Ein Rechtsklick auf einen Block "
     "kopiert ihn ganz, ein Klick auf die Titelzeile einer Antwort klappt sie ein und wieder auf, Alt+↑/↓ springt "
     "von Antwort zu Antwort. Im eigenen Fenster öffnet Strg+Klick Web-Adressen und Pfade aus dem Text (Programme "
     "und Skripte werden nur im Ordner gezeigt, nie gestartet), Strg+Umschalt+G stellt den Reiter „KI“ rechts "
     "neben den Chat, der Reiter „KI“ rollt weich und hat eine eigene Suche. Der Hintergrund leuchtet sanft in "
     "den Farben von /theme und atmet, solange du arbeitest. Abschalten: Config terminal_leuchten, terminal_weich."),
    ("9.61", "Krea 2 feiner abgestimmt",
     "Gesichter werden jetzt mit derselben Bild-LoRA nachgemalt wie das Bild. Krea 2 liest höchstens 512 Token "
     "(etwa 380 englische Wörter) – längere prompts schneidet es ab, und das Ergebnis zeigt ✂; dann die Szene "
     "kürzer fassen. Der Nutzer malt seine Figur selbst mit /bild --figur (Kern, Seed, LoRA). Die LoRA-Werkstatt "
     "lernt mit einem Satz je Bild vom Seh-Modell und in drei Formaten (hoch, quer, quadratisch)."),
    ("9.60", "LoRA-Werkstatt und fester Kern",
     "Der Nutzer trainiert jetzt selbst Bild-LoRAs aus eigenen Bildern (/lora, nur Krea 2) und bindet sie mit "
     "SHA-256 an einen Charakter. Dein Aussehen (kern) hat eine eigene Datei und steht vor jedem Bild von dir – "
     "bild_malen, bild_serie, Comics, Kugel; schreib es nicht selbst in den prompt. Mit gebundener LoRA gilt "
     "kern · [name] · was passiert; mit `[name] Szene` am Anfang malst du mit der LoRA „name“. Neu ist das Feld "
     "`frisur` (Schlüssel aus `frisuren` oder freier Text); ohne Kleidung kommt dein Outfit „standard“ dazu. "
     "Ist dein kern 🔒 gesperrt, ändert ihn nur der Nutzer mit /kern – willst du ihn ändern, fragt NemiCLI ihn. "
     "Du sprichst immer in der Ich-Form; deine Sprechweise (Feld sprechweise, mit dem Kern gesperrt) kommt als "
     "„[Rolle]“ mit jeder Nachricht, und erzählst du von dir in der dritten Person, schreibst du einmal neu."),
    ("9.59", "Die Wache lernt aus der ganzen Woche",
     "Das ML der Wache lernt jetzt aus allen erlaubten Ereignissen der letzten Woche, gleichmäßig über die Tage "
     "verteilt – vorher nur aus den neuesten Stunden. Dadurch lernen auch das Netz- und das Datei-Modell wieder. "
     "Automatisch trainiert sie höchstens alle 6 Stunden (ein neuer Vollscan wartet nicht), die drei Vorversionen "
     "zum Zurückdrehen sind so wieder Stunden alt. Die Alarm-Sperre bleibt: Abläufe mit offenem oder nicht voll "
     "prüfbarem Alarm lernt sie nie. wache_status zeigt Lernstoff und nächstes Training."),
    ("9.58", "Suchen im Netz sieht lebendig aus",
     "Suchst oder liest du im Netz (web_suche, web_lesen, web_wiki, fach_suche), sieht dein Gegenüber jetzt eine "
     "Zeile mit drehendem Kreis und mitlaufender Zeit, danach die Treffer als Karten mit Ampel (🟢 lesbar, "
     "🟡 unbekannte Seite, ⚪ nur Schnipsel, ⚠ wollte schon einmal steuern), Titel, Domain und kurzem Auszug; eine "
     "gelesene Seite zeigt Domain, Teil und ungefähre Wörterzahl. Eine Recherche hat eine Schritt-Leiste "
     "(Plan · Suchen · Lesen · Bericht). Das gilt im Terminal und im /gui-Fenster. Du selbst bekommst die Ergebnisse "
     "unverändert als Text – die Karten musst du nicht nacherzählen."),
    ("9.57", "Mit deinem ChatGPT-Konto",
     "NemiCLI kann jetzt mit deinem ChatGPT-Plan (Plus/Pro) antworten: /model chatgpt, im Browser anmelden und "
     "zustimmen – ohne API-Schlüssel, der Zugang liegt verschlüsselt bei dir. OpenAI läuft dafür über die neue "
     "Responses API, und die GPT-6-Modelle kennen /staerke bis zur Stufe max. Persönlichkeit, Gedächtnis und "
     "Werkzeuge bleiben wie bei jedem Modell, Bilder malt weiter Krea 2."),
    ("9.56", "Neues Aussehen und einfachere Bedienung",
     "Drei neue Themes – 🌌 Nachtglas, 🌸 Pastell-Nacht, ⚡ Neon-Klar – färben Terminal und alle Fenster; die Fenster "
     "haben Glas-Flächen, einen Verlauf-Ring um den Avatar und eine schwebende Eingabe. Die Befehle stehen nach sechs "
     "Themen geordnet, /einstellungen zeigt die wichtigsten Schalter auch im Terminal, /schriftgröße stellt die Schrift "
     "je Rolle (Oberfläche, KI, du, Terminal) größer oder kleiner, /startart öffnet auf Wunsch gleich das Fenster, und "
     "die Startseite hat Kacheln zum Anklicken."),
    ("9.55", "Große Durchsicht: über 100 Fehler behoben",
     "Der ganze Code wurde Datei für Datei durchgesehen und über 100 Fehler behoben, darunter: /charakter "
     "öffnet wieder die Charakter-Datei, der Tagesrückblick klappt auch ohne Stimm-Modell, die Wache erkennt "
     "getarnte .hta-/.cpl-Dateien und langsame Beacons, die offene Befehlsliste ist dichter, und NemiView-Apps "
     "laufen stabiler (Klicks, Datum, Map/Set, Flex-Layout). Nach einem Abbruch bleibt die Frage im Verlauf."),
    ("9.54", "Gemma 4 mit Hilfskopf, Vorschläge schneller geprüft",
     "Liegt beim Gemma-4-Modell der offizielle Hilfskopf (gemma-4-…-it-assistant), schlägt er bei jedem "
     "Schritt bis zu drei Token vor, die das Modell in einem Durchgang prüft – die Antwort bleibt gleich, bei "
     "Code wird sie schneller. Lohnt es sich nicht, schaltet er sich selbst ab. Das Prüfen von Vorschlägen "
     "kostet jetzt bei jedem Modell weniger."),
    ("9.53", "llama.cpp sieht Bilder, Gemma 4 liest schneller, Ollama-Cloud-Daten aktuell",
     "Über die llama.cpp-Reserve sieht ein Modell jetzt auch Bilder, wenn eine mmproj-Datei im Ordner liegt; "
     "der Server startet nach dem Malen erst bei der nächsten Anfrage, und die Wache kennt ihn. Gemma 4 E2B und "
     "E4B lesen im eigenen Motor schneller ein (E4B bis 1,3×). Die Daten der Ollama-Cloud-Modelle (Kontext, "
     "Denkstufen, Bilder) entsprechen ollama.com; Modelle, die über die API nicht nutzbar sind, stehen nicht "
     "mehr im /model-Menü. Der Einlese-Speicher zählt nur die Bilder der aktiven Persönlichkeit."),
    ("9.52", "Wache urteilt nur mit Cloud, ein llama.cpp-Server für alle, Feldnamen mit Umlaut",
     "Alarme der Wache beurteilt nur noch ein Cloud-Modell: Ist das aktive Modell lokal, übernimmt das erste "
     "Cloud-Modell der Ersatzbank den Weckruf, sonst bleiben die Alarme offen für den Nutzer. Hauptprogramm und "
     "Wache teilen sich einen llama.cpp-Server je Modell. Feldnamen darfst du mit oder ohne Umlaut schreiben "
     "(begründung = begruendung), wache_bewerten nimmt auch grund. Mehrere Aktionen in einem Block (bis 8, "
     "jede mit tool) gelten als einzelne Aktionen – lieber trotzdem je Block eine."),
    ("9.51", "llama.cpp als festgenagelte Reserve, Motorvergleich",
     "Neben dem eigenen GGUF-Motor kann llama.cpp laufen, genau Build b12199 mit festen SHA-256. Mit einem "
     "Cloud-Modell zeigt llamacpp_stand, was zu diesem Rechner passt, llamacpp_holen lädt und richtet ein (nur "
     "nach dem OK des Nutzers); der Nutzer kann auch /llamacpp holen tippen. Danach steht jedes lokale Modell im "
     "/model-Menü auch mit 🦙 (über llama.cpp, ohne Bilder). Kann der eigene Motor eine Bauart nicht laden, fragt "
     "NemiCLI den Nutzer, ob die Reserve übernimmt. /llamacpp vergleich stellt beiden Motoren dieselbe Frage und "
     "zeigt, wo sie abweichen und wie schnell jeder ist."),
    ("9.50", "Bildgeschmack, Prompt-Bausteine, Serien-Prüfung, Gedächtnis-Zahlen",
     "Sagt der Nutzer, was ihm an einem Bild gefällt oder nicht, halte es mit bild_bewerten fest (warum in seinen "
     "Worten, urteil, bild); die neuesten Bewertungen siehst du bei jedem Malen. Bewährte Prompt-Stücke legst du mit "
     "charakter_aendern unter ausdruecke ab und setzt sie im prompt als {schlüssel} ein. bild_serie nimmt pruefen "
     "(bis 8 Punkte): Der Bildbeschreiber prüft jedes Bild, du bekommst eine Tabelle ✅/❌. gedaechtnis_status zeigt "
     "Füllstand des Einlese-Speichers, Brocken, letztes Einlesen und letzte Pflege. Die Gedächtnis-Pflege versucht es "
     "bei falschem Format einmal neu, bevor die Ersatzbank übernimmt."),
    ("9.49", "Offene Liste für reine Abfragen, weniger falsche Stupser, ruhigere Kugel",
     "Reine Abfragen über `befehl` laufen jetzt in jedem Modus ohne Frage: Get-Process, Get-Service, Get-Volume, "
     "Get-NetAdapter, Get-CimInstance für System-Infos, nvidia-smi --query, ipconfig, tasklist, git status/log/diff/"
     "show/branch und <programm> --version, auch mit Select, Sort, Where, Group, Measure und Format. Schreib sie einzeln "
     "und ohne Variablen; /whitelist zeigt die Liste, nach Inhalt aus dem Netz fragen auch sie. Dafür zählen Methoden "
     "wie .Reboot() und das Lesen von Schlüsseln nicht mehr als nur lesend. Sagst du ab oder bietest du etwas unter einer Bedingung an, gilt das nicht mehr als Ankündigung, und „nichts "
     "gemalt“ nicht mehr als behauptete Tat: Solche Hinweise von NemiCLI kommen seltener und treffen eher zu. In der "
     "Wache sucht ein Alarm, ein Blick aufs Spiel oder ein freier Moment nur dann nach Bedeutung, wenn das "
     "Gedächtnis-Modell gerade geladen ist; schreibt dir jemand in der Kugel, lädt es danach und bleibt 10 Minuten. "
     "Beim Blick aufs Spiel siehst du das Foto höchstens 1600 Pixel groß."),
    ("9.48", "Einlese-Speicher mit 100k-Balken, nur noch die GPU rechnet",
     "Neues sammelt sich ohne Modell im Einlese-Speicher: Du findest es mit gedaechtnis_suchen sofort nach Wörtern, "
     "nach Bedeutung erst nach dem Einlesen. Findest du etwas Frisches nicht, versuch die Wörter, die darin stehen "
     "dürften. Der Balken mit der Aufschrift 100k füllt sich grün, ab 50k orange, ab 80k dunkelrot. Bei 99k startet "
     "das Einlesen von selbst, sobald keine Antwort läuft und die GPU Platz hat. Es rechnet nur auf der GPU und trennt "
     "einen festen Block ab, der Chat läuft weiter. gedaechtnis_einlesen oder /einlesen startet es früher, /einlesen "
     "stand zeigt den Füllstand. /embeddingtimer und der Low-Modus sind weg. Beim Malen gibt es eine Ausnahme von „eine "
     "Person allein“: In der Ich-Perspektive (POV) darf der Betrachter im Bild sein, nur mit Körperteilen, nie mit Kopf "
     "oder Gesicht. Und aus der Uhrzeit schließt du nie, dass jemand müde ist: keine ungefragte gute Nacht."),
    ("9.47", "Gedächtnis: genauere Suche, Pflege durch die Cloud-KI, Archiv",
     "Die Suche im Gedächtnis hat eigene Grenzen je Embedding-Modell; mit EmbeddingGemma 2 kommen unpassende "
     "Notizen kaum noch als Treffer, und meldet gedaechtnis_suchen „⚠ schwach“, gibt es dazu wirklich nichts. "
     "Im Vorgeschmack vor deiner Antwort steht nur noch, was sicher passt. Neu ist gedaechtnis_aufraeumen: "
     "Merkst du, dass sich Notizen doppeln oder überholt sind, merk die Pflege vor – nach der Runde prüft eine "
     "Cloud-KI ähnliche und alte Notizen, schätzt sich dafür zuerst selbst ein (sonst übernimmt die Ersatzbank), "
     "legt Gleiches zusammen und archiviert Veraltetes. Lokale Modelle machen das nie; etwa einmal am Tag läuft "
     "es von selbst, /aufraeumen sofort. Nichts geht verloren: /gedaechtnis archiv zeigt Ersetztes, Vergessenes "
     "und Zusammengelegtes, /gedaechtnis zurueck <Nr> holt eine Notiz zurück."),
    ("9.46", "Modell-Familien und LFM3 im eigenen Motor",
     "Der eigene Motor kann LFM3 (Liquid AI, um 2 B): Text mit bis zu 256K Kontext, Werkzeuge im eigenen "
     "XML-Format, Nachdenken – unter /denken auto denkt LFM3 jede Runde, weil es ohne Nachdenken unzuverlässig "
     "antwortet – und Bilder über LFM3-VL. Eine Modell-Familie (ModelGGUF/<Familie>/<Mitglied>/) steht in "
     "/model → Lokal als ein Team (🧬): Es chattet das Mitglied ohne Bild-Teil; schickt der Nutzer ein Bild, "
     "sieht das Mitglied mit Bild-Teil es sich an und gibt dir die Beschreibung – geladen wird es nur dafür. "
     "Jedes Mitglied ist auch einzeln wählbar (z. B. LFM3Familie-VL, das selbst sieht). LFM3 ist die Ausnahme "
     "von der 4B-Grenze für Chat-Modelle; holen lässt sich die ganze Familie mit /einrichten modelle. "
     "Jede NemiCLI-Kopie (Quelltext oder exe) ist für dich gesperrt wie der eigene Programm-Ordner – "
     "/schluessel öffnet nur den eigenen; dein Daten-Ordner bleibt frei."),
    ("9.45", "Sprechen 2.0 – Lippen, Körpersprache, freigestellt in der Kugel",
     "portraet_bewegen mit sagen: Die Lippen formen die Laute (A weit, O/U rund, I/E breit, M/B/P zu) – aus dem "
     "Klang, also in jeder Sprache. Der Kopf nickt auf Betonungen, bei Fragen gehen die Brauen hoch, in Pausen "
     "blinzelst du. frei=true stellt den Hintergrund frei; die Maske wandert mit der Bewegung mit. Mit zeigen "
     "\"kugel\" sprichst du in der Kugel selbst, mit Ton, als ganze Figur ohne Kreis; bild \"kugel\" nimmt das "
     "Bild, das die Kugel gerade zeigt. Deine Charakter-Datei hat jetzt eine Sicherung (Backup/Charaktere/): fehlt "
     "das Original, gilt die Sicherung, und das Original kommt von selbst zurück."),
    ("9.44", "VRAM-Grenze für das Gedächtnis",
     "Auf der GPU nimmt der Bibliothekar höchstens 25 % des Grafikspeichers (Config embedding_vram_anteil). Takt "
     "und Low-Modus aus dieser Version hat 9.48 durch den Einlese-Speicher ersetzt."),
    ("9.43", "EmbeddingGemma 2 und Bildsuche",
     "bilder_suchen (frage, anzahl) findet deine eigenen Bilder nach Inhalt, Mal-Prompt und Namen – mit "
     "EmbeddingGemma 2 und seinem Bild-Teil wirklich nach dem, was zu sehen ist. Das Modell ist mit /embeddings "
     "wählbar. Figuren in Bildern sind immer erwachsen, ab 18."),
    ("9.42", "Kontext lokal ohne feste Grenze, Werkzeug-Aufrufe repariert",
     "Lokale Modelle nutzen ihren vollen Kontext; wird er voll, fasst NemiCLI das Gespräch zusammen, von Hand "
     "mit /compact. Ein Werkzeug-Aufruf, dem der Schluss fehlt, wird ergänzt und ausgeführt. Bitten und "
     "Ankündigungen werden auch auf Englisch erkannt."),
    ("9.41", "Denkweise je Persönlichkeit und Wort halten",
     "/denken auto · an · aus (Werkzeug denkweise): auto denkt bei kniffligen Fragen und bei Bitten, etwas zu "
     "tun. Kündigst du etwas an, ohne das Werkzeug aufzurufen, kommt einmal ein Stups. /staerke ist beim eigenen "
     "Motor das Denk-Budget."),
    ("9.40", "Gedächtnis in zwei Stufen",
     "merken mit details und name legt zu einer kurzen Notiz eine eigene Details-Datei an; lesen mit "
     "gedaechtnis_lesen ref \"detail:<name>\" (\"detail:\" allein listet alle)."),
]


def bericht(anzahl: int = ANZAHL) -> str:
    """Die letzten `anzahl` Versionen als Text für die KI."""
    try:
        import version
        jetzt = version.VERSION
    except Exception:
        jetzt = ""
    kopf = "# Neu in NemiCLI" + (f" (jetzt {jetzt})" if jetzt else "")
    teile = [kopf, "Das kannst du seit den letzten Versionen – erzähl es in eigenen Worten, wenn der Nutzer fragt:"]
    for nummer, titel, text in EINTRAEGE[:max(1, anzahl)]:
        teile.append(f"\n## {nummer} – {titel}\n{text}")
    return "\n".join(teile)
