# 📅 Änderungen – NemiCLI

Kurzfassung je Version, neueste zuerst. Die ausführlichen Einträge stehen in der Git-Historie dieses
Repositorys. Überblick: [README](README.md) · Beschreibung: [Handbuch](docs/Handbuch.md).

### 10.29 – 26.11.2026

- **🎞 Desktop-Kugel bewegt sich beim Arbeiten**
  - Bewegtes Porträt läuft beim Arbeiten in Schleife und blendet danach weich zurück
  - Ohne Porträt: sanftes Schweben und Lichtschimmer; drei Lichtpunkte auf dem Ring
  - Ring beschleunigt und bremst weich

### 10.28 – 25.11.2026

- **🔮 Lebendige Fenster-Kugel**
  - Die Kugel in Office, Atelier, Controllcenter und Wache-Fenster atmet, schaut zur Maus und blendet weich über
  - Bewegtes Porträt beim ersten Drüberfahren und nach jedem Auftrag
- **🔧 Nachbesserung: GGUF-Bildmodelle mit umgefalteten Tensoren**

### 10.27 – 25.11.2026

- **🎨 Krea-Atelier**
  - Eigenes Fenster `/atelier` mit allen Einstellungen von Krea 2, Galerie und A/B-Vergleich
  - Zeitplan beta, Text-Encoder und VAE frei wählbar
- **🔧 Nachbesserung: Atelier öffnet sich auch aus der exe**

### 10.26 – 25.11.2026

- **Gesetzbuch nummeriert, ähnliche Gesetze zusammenführen**
  - Gesetze tragen Nummern
  - Im Controllcenter markiert die KI ähnliche Gesetze; zusammengeführt wird nur nach Bestätigung
- **🔧 Nachbesserung: Geplante Aufgabe aus dem Wache-Fenster deaktivieren oder löschen**
- **🔧 Nachbesserung: Alarm-Einträge im Wache-Fenster löschen**

### 10.25 – 24.11.2026

- **Wache-Fenster**
  - Eigenes Fenster `/wache fenster`: Alarme mit Steckbrief und live geprüfter Signatur
  - Urteil per Klick, eigene Wache-KI mit Cloud-Modell
- **🔧 Nachbesserung: Zeichensperre für alle KIs**
- **🔧 Nachbesserung: Prompt-Schmied erkennt Widersprüche in der Ansicht**
- **🔧 Nachbesserung: Detail-Satz bei Ansicht von hinten**
- **🔧 Nachbesserung: ✨ Detail-Satz für Krea 2**
- **🔧 Nachbesserung: Aussehen auch mitten im Satz nicht doppelt**
- **🔧 Nachbesserung: Aussehen nicht doppelt im Bild-Prompt**
- **🔧 Nachbesserung: Inkling 2 spricht**
- **🔧 Nachbesserung: Motoren-TÜV**
- **🔧 Nachbesserung: Ansicht verrutscht nicht mehr bei Flaggen & Co**
- **🔧 Nachbesserung: Die Wache-KI recherchiert und bewertet selbst**
- **🔧 Nachbesserung: Ein Klick aufs Wache-Symbol öffnet das Wache-Fenster**

### 10.24 – 24.11.2026

- **Seiten selbst erlauben**
  - `/web erlauben <seite>` und `/web sperren <seite>` – nur von Hand, nicht durch die KI
- **🔧 Nachbesserung: Unbekanntes Zahlenformat fragt nach der Reserve**

### 10.23 – 24.11.2026

- **Krea 2 einstellen**
  - Im Controllcenter: CFG, fester Gegen-Prompt, Sampler, Verschiebung und Schritte für Krea 2

### 10.22 – 23.11.2026

- **Gesetzbuch**
  - Richtlinien des Nutzers für alle KIs, ganz oben in jeder Anleitung
  - Ändern nur im Controllcenter; die KI hilft auf Wunsch beim Formulieren
- **🔧 Nachbesserung: Seed-Feld mit Vorschau in der Charakter-Galerie**
- **🔧 Nachbesserung: Git-Prüfsumme als Abgleich markiert**
- **🔧 Nachbesserung: Gesundheit, Prompt-Vektoren, Startbremse**

### 10.21 – 23.11.2026

- **Wertungen des Nutzers und Toplist-KI**
  - Antworten lassen sich mit ➕ oder ➖ werten; die KI berücksichtigt das in den folgenden Antworten

### 10.20 – 23.11.2026

- **Prompt-Bibliothek für Krea 2**
  - Jeder Krea-Prompt kommt mit Modell, Seed, Bewertung und Vektor in eine Datenbank
  - Vor dem Malen bekommt die KI ähnliche bewährte Prompts; `/bild prompts` zeigt den Stand
- **🔧 Nachbesserung: Markieren im Terminal**

### 10.19 – 23.11.2026

- **Schreibschutz für Persönlichkeits-Dateien**
  - Persönlichkeits-Dateien ändert, verschiebt und löscht nur der Nutzer – auch nicht per Befehl

### 10.18 – 23.11.2026

- **Hugging Face im Controllcenter**
  - Modelle suchen, Dateien anhaken und mit Fortschritt laden; Verbindung erst nach Bestätigung
  - Jede Datei wird geprüft (SHA-256 bzw. Git-Prüfsumme), Pickle-Dateien werden nicht geladen
- **🔧 Nachbesserung: Anleitung und Charakter-Datei verhärtet**

### 10.17 – 23.11.2026

- **Controllcenter modern und sortiert**
  - Baum in Gruppen, Kacheln auf der Startseite, Auswahl als Karten
  - Eigenes Thema für Version und Updates

### 10.16 – 23.11.2026

- **Malen wie ein Malprogramm**
  - Die OfficeAI malt Kurven, Bögen, Sterne, Verläufe, gestrichelte Linien und mit Pinselarten
  - Nachbessern: radieren, verschieben, drehen, spiegeln, umfärben; für den Nutzer Sprühdose, Stern, Herz

### 10.15 – 22.11.2026

- **Anleitungen für die KI überarbeitet**
  - Cloud-Modelle bekommen eine schlanke Anleitung, Details passend zur Runde
  - `/kontext anleitung voll` stellt die volle Fassung wieder her
- **🔧 Nachbesserung: Bild- und Stimm-Regeln richten sich nach dem Geschlecht der Persönlichkeit**

### 10.14 – 22.11.2026

- **Charaktere per Zufall**
  - Die Charakter-Galerie erfindet Charaktere nach Wahl (Anzahl, Alter ab 18, Wünsche) samt Porträt

### 10.13 – 22.11.2026

- **Malen in Nemi Office**
  - Dritte App 🎨 Malen: Pinsel, Formen, Farbeimer, Text, Auswahl; PNG, JPEG, WebP, BMP
  - Die OfficeAI zeichnet selbst, schaut mit dem Seh-Modell hin und malt mit Krea 2 um
- **🔧 Nachbesserung: Werkzeugaufrufe in Auszeichnungsform werden ausgeführt**
- **🔧 Nachbesserung: Die KI im Controllcenter kennt alle Befehle und denkt mit**

### 10.12 – 22.11.2026

- **Feste Bildsperre**
  - Für jedes Bild und Video fest im Programm: alle Personen erwachsen, kein Gore, kein Krieg

### 10.11 – 22.11.2026

- **Stable Diffusion als Bild-Motor**
  - SDXL und SD 1.5 als ganzer Checkpoint, Wahl über `/bildmodel`
  - Lange Prompts in Stücken statt abgeschnitten; Gegen-Prompt, CFG und Stil-Vorlagen im Controllcenter
- **🔧 Nachbesserung: Kleidung bleibt an, Gesichts-Details am Ausschnitt geprüft, Verneinung im Kern**
- **🔧 Nachbesserung: Gegen-Prompt der KI, 1024×1024 und lange Charakter-Prompts für Stable Diffusion**

### 10.10 – 21.11.2026

- **Der Tresor**
  - Alle API-Schlüssel und das ChatGPT-Konto verschlüsselt im Tresor (AES-256-GCM, DPAPI)
  - Eintragen nur im Controllcenter; von außen veränderte Tresore werden verworfen

### 10.09 – 21.11.2026

- **Nemi Controllcenter**
  - Alle Einstellungen in einem Fenster `/control`: Befehle nach Thema, Favoriten, Gesundheit, System
  - KI-Kugel als Helfer: schlägt vor, der Nutzer bestätigt
  - `/update torch` holt den neuesten passenden PyTorch-Bau
- **🔧 Nachbesserung: Controllcenter öffnet nur ein Fenster und hat eine eigene Schriftgröße**

### 10.08 – 21.11.2026

- **Lernen aus Erfahrung**
  - Abläufe aufnehmen und abspielen (`/ablauf`)
  - Gedächtnissuche folgt Verknüpfungen und gewichtet Neues
  - Lern-LoRA für lokale Modelle (`/lernen`, Vorgabe aus)

### 10.07 – 21.11.2026

- **Befehle aufgeräumt**
  - Befehle in acht Themen; `/` zeigt zuerst die Themen, `/help <thema>` die Befehle eines Themas

### 10.06 – 21.11.2026

- **Die Kugel im neuen Look**
  - Schwebekugel mit rundem Gesicht im Leuchtring in der Farbe der Wache
  - Eingabefeld direkt an der Kugel, Antwort als Blase
- **🔧 Nachbesserung: Eingabefeld an der Kugel nicht mehr blockiert**

### 10.05 – 21.11.2026

- **Office: Markdown live und Bilder malen**
  - Markdown wird beim Tippen zu Format
  - Die OfficeAI malt Bilder ins Dokument; A4-Seiten wie im Druck
- **🔧 Nachbesserung: A4-Seiten wie im Druck und Markdown mit oder ohne Bilder**

### 10.04 – 21.11.2026

- **OfficeAI: die Kugel arbeitet selbst im Dokument**
  - Die OfficeAI ersetzt, formatiert, trägt ein und sortiert selbst im Dokument
  - Lila Zeiger zeigt jeden Schritt; alles mit Rückgängig, ESC hält an
- **🔧 Nachbesserung: Office-Antwort ohne alten „Übernehmen“-Satz**
- **🔧 Nachbesserung: OfficeAI kennt Datum und Uhrzeit**

### 10.03 – 21.11.2026

- **Nemi Office in Farbe**
  - Zellen färben, Schriftfarbe, Textmarker und Papierfarbe; bleibt in .ods, .xlsx, .docx, .odt und PDF erhalten

### 10.02 – 21.11.2026

- **Nemi Office: Schreiben und Tabelle**
  - `/office` mit ✍️ Schreiben (.md .docx .odt .rtf .txt .html, PDF) und 📊 Tabelle (.ods .xlsx .csv .tsv)
  - Formeln deutsch und englisch

### 10.01 – 20.11.2026

- **Prompt-Schmied, ESC hat Vorrang, sicherer Zeitplan**
  - Prompt-Schmied prüft Krea-Prompts vor dem Malen und lernt aus Bewertungen
  - ESC hält alles sofort an, nichts wird gelöscht
  - Zeitplan-Läufe nur mit lesenden Werkzeugen
- **🔧 Nachbesserung: ESC hat Vorrang vor allem**
- **🔧 Nachbesserung: Zeitplan nur mit sicheren Werkzeugen**
- **🔧 Nachbesserung: Regex aus fremden Daten gehärtet**

### 10.00 – 19.11.2026

- **Werke geschützt, Profil-Grenze, Zeitplan-Fenster, Kompass**
  - Bilder, Videos, Musik und Stimmen ändert und löscht nur der Nutzer
  - Zeitplan-Vorschläge öffnen ein Fenster mit Kalender, eingetragen wird erst per Klick
  - `/kompass an` zeigt alle Befehle neben dem Terminal

## Frühere Versionen

#### 9.x

- 9.99 (19.11.2026) – PyTorch über /update
- 9.98 (18.11.2026) – Krea 2 versteht mehr Modelle
- 9.97 (18.11.2026) – Coding frei: durcharbeiten
- 9.96 (18.11.2026) – CVE-Liste als Gefahrenwissen
- 9.95 (18.11.2026) – Kontext max und gepresster Gesprächsspeicher für llama.cpp
- 9.94 (18.11.2026) – Leichteres Terminal-Fenster
- 9.93 (18.11.2026) – Härtung nach der Gesamtprüfung
- 9.92 (17.11.2026) – Einfache /-Suche
- 9.91 (17.11.2026) – Datei-Scan im Rechtsklick-Menü
- 9.90 (17.11.2026) – Auto-Vollscan nach Zeitplan
- 9.89 (17.11.2026) – Schutzliste: einmal gesperrt, immer gesperrt
- 9.88 (17.11.2026) – KI-Modell in der Wache
- 9.87 (17.11.2026) – KI-Tracker in der Wache
- 9.86 (17.11.2026) – Netz-Zwang: nur https, lokal mit Codewort
- 9.85 (17.11.2026) – Schutzliste und offizielle KI-Anbieter
- 9.84 (17.11.2026) – Selbstentwurf der Persönlichkeit
- 9.83 (17.11.2026) – Archive entpacken, Chat-Exporte erinnern
- 9.82 (16.11.2026) – llama.cpp aktualisieren
- 9.81 (15.11.2026) – Eigener Bereich der KI
- 9.80 (15.11.2026) – Gesichts-Korrektur schaltbar
- 9.79 (15.11.2026) – Ein neues Gesicht finden
- 9.78 (15.11.2026) – Der Nutzer ändert den Kern selbst
- 9.77 (15.11.2026) – Vorprüfer für die Wache
- 9.76 (15.11.2026) – Setup findet NemiCLI wieder
- 9.75 (13.11.2026) – Das Gedächtnis sieht und hört
- 9.64 (12.11.2026) – Ein Wecker für die KI selbst
- 9.63 (11.11.2026) – Video aus Stable Diffusion
- 9.62 (11.11.2026) – Terminal mit Suche, Blöcken und Leuchten
- 9.50 (07.11.2026) – Bildgeschmack, Prompt-Bausteine, Serien-Prüfung, Gedächtnis-Zahlen
- 9.49 (07.11.2026) – Offene Liste für reine Abfragen, weniger falsche Stupser, ruhigere Kugel
- 9.48 (05.11.2026) – Einlese-Speicher mit 100k-Balken, nur noch die GPU rechnet
- 9.47 (04.11.2026) – Gedächtnis: genauere Suche, Pflege durch die Cloud-KI, Archiv
- 9.46 (02.11.2026) – Modell-Familien und LFM3 im eigenen Motor
- 9.34 (31.10.2026) – ⚖️ Große Modelle mit wenig Grafikspeicher
- 9.33 (30.10.2026) – 🎨 Viele Bild-Modelle und 🎞 Video mit Ton
- 9.32 (30.10.2026) – ✔️ Menüs auf einen Blick
- 9.31 (30.10.2026) – 🧍 Figur automatisch
- 9.30 (29.10.2026) – 🔮 Die Kugel lebt mehr, 🎮 Spiel-Begleiterin
- 9.29 (29.10.2026) – 🧩 Eigene Prozesse und eine Agenten-Schleife
- 9.28 (28.10.2026) – 📖 Angehängte Dateien ganz lesen
- 9.27 (28.10.2026) – 📎 Mehr Dateien reinziehen, 😊 Emojis nach Maß
- 9.26 (28.10.2026) – 🧬 Gedächtnis-Modell schlanker und klüger

#### 8.x

- 8.6 (18.10.2026) – Reiter im eigenen Terminal und der Reiter „🤖 KI“
- 8.5 (17.10.2026) – NemiCLI macht Musik
- 8.4 (17.10.2026) – Netz-Sperre – die Firewall für alles, was aus dem Internet kommt
- 8.3 (16.10.2026) – Fehlererkennung: NemiCLI erkennt selbst, wenn etwas schiefläuft
- 8.2 (16.10.2026) – Der GGUF-Motor schreibt schneller, braucht weniger VRAM und rechnet genauer
- 8.1 (16.10.2026) – TensorRT-RTX als Zusatz
- 8.0 (15.10.2026) – Die Kugel lebt

#### 7.x

- 7.9 (15.10.2026) – Die Wache bekommt Deep Learning – ein Folgen-Netz für Prozess-Ketten
- 7.1 (12.10.2026) – Python 3.14
- 7.0 (11.10.2026) – neue Startseite

#### 6.x

- 6.9 (11.10.2026) – Wache weckt nach Anzahl offener Alarme, Vollscan bis in die Partitionen, eigener LoRA-Motor für lokale Modelle
- 6.8 (09.10.2026) – Großes Paket für den GGUF-Motor: Qwen 3.5/3.6 MoE, Qwen3-Next, Mamba-2-Hybride, LFM2, Llama 4, spekulatives Dekodieren
- 6.7 (06.10.2026) – `/backup`: Teil- und Vollsicherung als Zip mit SHA-256
- 6.6 (06.10.2026) – Versionsnummer an den Stand angeglichen

#### 5.x

- 5.1 (20.09.2026) – Die Persönlichkeit bekommt ein Gesicht und steuert es selbst; Desktop-Kugel, Chatfenster und Symbol auf Qt
- 5.0 (19.09.2026) – Arbeitsbereich ohne Rückfrage, Schlüssel auf Zeit, die Wache lernt „bekannt“, Spiele im Browser
