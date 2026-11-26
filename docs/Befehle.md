# Befehle

[Handbuch](Handbuch.md) · [Installation](Installation.md) · [Erste Schritte](Erste-Schritte.md) ·
[Modelle](Modelle.md) · [Funktionen](Funktionen.md) · **Befehle**

Tippe `/` für die Themen, weitertippen sucht in allen Befehlen. `/help <befehl>` erklärt einen Befehl, `Strg+K`
sucht auch in Einstellungen und Modellen. Befehle mit Unterpunkten öffnen ohne Zusatz eine Auswahl. Oder frag
einfach die KI – sie kennt alle Befehle. Alles zusammen in einem Fenster: `/control`.

## Chat und Modelle

| Befehl | Zweck |
|---|---|
| `/model [name\|huggingface\|chatgpt\|ersatz]` | Modell wählen, Cloud-Schlüssel, Modell laden, Ersatzbank |
| `/kontext [8k…128k\|max]` | Kontext ansehen, Größe für lokale Modelle |
| `/staerke [schnell\|normal\|stark\|max]` | Denkstufe |
| `/persönlichkeiten [name\|neu]` · `/name` | Persönlichkeit wählen oder anlegen · Angaben über dich |
| `/reset` · `/clear` | neues Gespräch · Bildschirm leeren |
| `/subagenten [an\|auto\|off]` | Helfer-Agenten |

## Arbeiten

| Befehl | Zweck |
|---|---|
| `/modus [chat\|lesen\|normal\|auto\|autoan]` | Arbeitsmodus (auch `Shift+Tab`) |
| `/code [aufgabe\|status]` · `/codeend` | Coding-Assistent starten · beenden |
| `/doku` | Offline-Doku (Python, MDN) |
| `/sandbox [freigeben\|entziehen <Ordner>]` | Sandbox und freigegebene Projektordner |
| `/zip` | Archive entpacken |
| `/llamacpp` | llama.cpp-Reserve holen, einrichten, vergleichen |
| `/skills` · `/ablauf` | Skills · Befehlsfolgen aufnehmen und abspielen |
| `/zeitplan` | Aufträge in der Windows-Aufgabenplanung |

## Bilder, Video, Musik

| Befehl | Zweck |
|---|---|
| `/bild <prompt>` · `/atelier` | Bild malen · Krea-Atelier |
| `/bildmodel` | Bild-Motor wählen |
| `/video <beschreibung>` · `/video modell` | Video erzeugen · Video-Modell wählen |
| `/musik` | Musik-Fenster |
| `/lora` | LoRA-Werkstatt |
| `/charakter` · `/charaktere` · `/kern` | Charakterdatei · Charakter-Galerie · festes Aussehen |
| `/office` | Nemi Office |
| `/kugel` · `/bewegung` | Schwebekugel · Figur im Terminal |

## Gedächtnis

| Befehl | Zweck |
|---|---|
| `/gedaechtnis` · `/wissen` | Erinnerungen ansehen und löschen · gelernte Notizen |
| `/embeddings` | Embedding-Modell wählen |
| `/einlesen` | Einlese-Speicher jetzt verarbeiten |
| `/reflektieren` · `/lernen` | Lehren ziehen · Lern-LoRA |
| `/aufraeumen` | Gedächtnis verdichten |

## Sicherheit und System

| Befehl | Zweck |
|---|---|
| `/wache [fenster\|status\|alarme\|fullscan\|netz]` | Systemwache |
| `/backup` · `/undo` | Sicherung · Papierkorb |
| `/limit` · `/whitelist` · `/schluessel` | Lösch-Limit · Befehls-Whitelist · Programm-Ordner auf Zeit öffnen |
| `/protokoll` | Aktions-Protokoll |
| `/web` | Allowlist, eigene Seiten, Schutzliste |
| `/vision` · `/voice` | Bildbeschreiber · Stimme |
| `/start` | Daten-Ordner |
| `/einrichten` · `/update` · `/venv` | Einrichten · aktualisieren · Arbeitsumgebung |
| `/systemcheck` · `/selbsttest` · `/version` | Rechner · Installation · Version |
| `/debug` | Debugbericht |

## Oberflächen

| Befehl | Zweck |
|---|---|
| `/control` · `/gui` · `/webui` | Controllcenter · Desktop-Fenster · Browser-Oberfläche |
| `/chrome` · `/kontextmenue` | Chrome-Erweiterung · Windows-Rechtsklick |
| `/terminal` · `/theme` · `/emoji` · `/schriftgröße` | Terminal-Fenster · Farben · Emojis · Schrift |
| `/appstore` · `/spiel` | Apps in NemiView · Spiele |
| `/restart` · `/exit` | neu starten · beenden |

## Tasten

| Taste | Wirkung |
|---|---|
| `Enter` · `Strg+J` | senden · neue Zeile |
| `Esc` | Not-Stopp – hält alles sofort an, löscht nichts |
| `F8` | Änderung im Prüffenster freigeben |
| `Shift+Tab` | Arbeitsmodus wechseln |
| `Strg+K` · `Strg+F` | Suche in Befehlen · Suche im Verlauf |
| `F2` · `F3` · `F12` | Denktext · Cockpit · Gespräch sichern |
| `Strg+Umschalt+T` · `Strg+Umschalt+K` | neuer Reiter · Reiter „🤖 KI“ |

## Ohne Oberfläche

`--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` · `--auftrag <name>`
(Zeitplan-Auftrag ohne Fenster, nur lesend, Antwort als Bericht).
