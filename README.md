<div align="center">

```
 █   █  █████  █   █  ███   ████  █      ███
 ██  █  █      ██ ██   █   █      █       █
 █ █ █  ████   █ █ █   █   █      █       █
 █  ██  █      █   █   █   █      █       █
 █   █  █████  █   █  ███   ████  █████  ███
```

# NemiCLI

**Dein eigener KI-Agent im Terminal – Cloud & lokal, mit Werkzeugen, Gedächtnis und Herz.** ✦

**Entwickelt von VibeCoder · gebaut mit Claude Opus 5 (Anthropic)**

</div>

---

## Was ist das?

NemiCLI ist ein selbstgebauter **Chat-Agent fürs Terminal** (Python, Windows). Er redet mit
**Cloud-Modellen** (11 Anbieter), mit **lokalen Modellen im eigenen GGUF-Motor** (ohne Server)
oder über Ollama –
und er kann **handeln**: Dateien lesen und schreiben, Befehle ausführen, im Web nachschlagen,
Bilder ansehen und malen, den Bildschirm lesen, Helfer losschicken. Immer mit deiner Bestätigung.

Dazu eine lebendige Oberfläche mit Persönlichkeit (Nemi, Lara oder deine eigene), Themes,
Maskottchen, Live-Anzeige – und Lesehilfen und anpassbare Darstellung. Wer lieber nicht ins Terminal schaut, öffnet mit
`/gui` ein ruhiges **Desktop-Fenster** am selben Kern; Skills 🧩 bringen NemiCLI wiederkehrende
Aufgaben bei – jede Anleitung erst nach deiner Freigabe.

**Reines Python – kein Node.js, kein npm.** Eigener GGUF-Motor statt llama.cpp, eigene Bild-Pipelines
(Stable Diffusion, Krea 2), Embeddings als safetensors oder GGUF.

> Entstanden aus Neugier: „Wie funktioniert eigentlich so ein Coding-Agent?" 💜

---

## 🚀 In 30 Sekunden starten

**Weg 1 – der bequeme:** `start.bat` doppelklicken. Der Starter prüft erst die Installation –
ist Python da? das `venv`? die Pakete? – und baut nach, was fehlt. Danach `/einrichten` tippen:
der Assistent holt sich ein eigenes Python (isoliert in `.runtime/`, kein Admin, nichts am
System) und installiert torch passend zu deiner Grafikkarte. Zweimal „ja", fertig.

**Weg 2 – von Hand:**
```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

**Weg 3 – als exe:** `venv\Scripts\python build_exe.py` → `dist/NemiCLI/` mit `NemiCLI.exe`
(Fenster-App, öffnet das eigene Terminal-Fenster), `NemiCLIc.exe` (dasselbe mit Konsole) und dem
Ordner `NemiCLIT2` (ca. 345 MB, kein Python auf dem Zielrechner nötig). Bewusst keine
Ein-Datei-exe: die entpackt sich bei jedem Start in den Temp-Ordner, und das hält Bitdefender
für Schadsoftware. Bricht ab, statt `dist/NemiCLI` zu leeren, wenn dort schon eigene Daten liegen.
Jeder Bau schreibt **`SHA256SUMS.txt`** (Prüfsummen der exe-Dateien) und **signiert** die exe, wenn
ein Code-Signatur-Zertifikat `CN=NemiCLI` da ist – einmalig anlegen mit `python zertifikat.py`
(selbst erstellt, gilt auf diesem PC; Windows fragt einmal nach).

**Dein Ordner:** Beim allerersten Start fragt ein Fenster, **wo deine Daten liegen sollen** –
Chats, Bilder, Modelle, Gelerntes. Getrennt vom Programm, damit NemiCLI umziehen oder neu
gebaut werden kann, ohne dass dein Gedächtnis das mitmacht. `/start` öffnet das Fenster
jederzeit wieder; beim Wechsel wird **kopiert, nie gelöscht**.

**Von überall starten (`nemicli`):** Programmordner einmalig in den Benutzer-PATH:
```powershell
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';C:\Users\<du>\AppData\Local\NemiCli', 'User')
```
Neues Terminal öffnen → `nemicli`. Mit `nemicli --sag "…"` geht die erste Nachricht sofort ab.

**Modell wählen:** `/model`. Ein laufendes Ollama erscheint von selbst. Cloud: `/model` →
„Cloud-Anbieter hinzufügen" → Key eintippen (wird verschlüsselt gespeichert, DPAPI). Anbieter:
Anthropic, OpenAI, Google, Mistral, Cohere, Perplexity, DeepSeek, xAI, Groq, OpenRouter,
Ollama Cloud.

---

## ✨ Was NemiCLI kann

### 💬 Chat & Modelle
- **Ein Schema für alles:** `anthropic:claude-…`, `openai:gpt-…`, `ollama:gemma4:12b`.
  Zwei Motoren (Anthropic nativ, OpenAI-kompatibel), eine Schnittstelle.
- **Modelle live** vom Anbieter abgefragt – nichts hartkodiert, neuer Key = neue Modelle.
- **Eigener GGUF-Motor 🧠** (`ggufengine/`, `/model` → „Lokal (eigener Motor)“): lokale Modelle
  ohne llama.cpp, Ollama oder Server – reines Python + torch **im NemiCLI-Prozess**, kein Port, kein
  Kindprozess. Gehärteter GGUF-Leser (jede Zahl aus der Datei wird vor Gebrauch geprüft, das
  Chat-Template der Datei wird nie ausgeführt), Gewichte bleiben quantisiert (Int4) im VRAM, CUDA
  Graphs. **Baukasten:** das Modell setzt sich aus der GGUF-Datei selbst zusammen – Llama 2/3.x, Mistral, Mixtral, Qwen2/2.5/3 (auch MoE), Gemma 1–4, Phi-3/4, Granite, OLMo 2, Qwen3.5, K2-Horizon u. a. laufen ohne eigenen Code; dazu **Ling 3.0** (`bailingmoe3`: Kimi-Delta-Attention + MLA + 128 Experten, 1,3B aktiv – ~40–65 Token/s); Chat-Format und Tokenizer-Regeln werden erkannt. Jedes Modell ist ein Ordner
  in `ModelGGUF/` im Programm-Ordner (`ModelGGUF/Gemma4/…gguf`, eine `mmproj-*.gguf` daneben ist
  der Bild-Teil).
  - **128k Kontext** bei Gemma-4-E4B mit **~6 GB VRAM**: die Sliding-Window-Schichten halten nur
    ein festes Fenster (Ringpuffer), die 1,8 GB große Per-Layer-Embedding-Tabelle bleibt im RAM.
  - **Denktext wie vom Hersteller vorgesehen:** Frühere Denktexte fallen aus dem Gesprächsspeicher, wenn die
    Vorlage des Modells das so will (Granite 4.2, Qwen3.5, Gemma 4); Ling 3.0 behält sie. Der Speicher wächst
    so nur mit den Antworten, nicht mit jedem Nachdenken.
  - **Präfix-Cache:** Anleitung und bisheriges Gespräch werden nur einmal gelesen – Folgerunden
    antworten nach ~0,1 s statt ~15 s. Ein Sicherungspunkt nach dem Systemprompt sorgt dafür, dass
    auch ein abweichendes Gespräch die Anleitung nie neu liest. Er wird zusätzlich **auf der Platte**
    gemerkt (`Cache/gguf/<Modell>.safetensors`, nur Daten): Nach einem Neustart muss die Anleitung
    gar nicht mehr eingelesen werden, solange Modell und Anleitung gleich sind.
  - **Schnelles Einlesen:** lange Texte liest der Motor blockweise (Qwen3.5-DeltaNet in Blöcken,
    Int4-Gewichte fürs Einlesen kurz ausgepackt) – NemiCLIs Anleitung bei Qwen3.5-9B in ~5 s statt ~32 s.
  - **Schlanke Anleitung:** lokale Modelle bekommen ~5.600 statt ~11.000 Token Anleitung; Details
    (Internet, Wache, Gedächtnis, Helfer, Werkzeuge) liest die KI bei Bedarf mit `anleitung_lesen`
    oder NemiCLI reicht sie selbst nach – beim passenden Stichwort oder beim ersten Werkzeug dazu.
  - **Weniger Halluzinieren:** nach einem Werkzeug-Aufruf ist Schluss (kein erfundenes Ergebnis
    dahinter), und eine **Floskel-Bremse** verhindert, dass die KI immer denselben Satz aus ihren
    eigenen früheren Antworten wiederholt – nur am Wortanfang, damit keine Tippfehler entstehen.
  - **Tempo-Grenze** `gguf_tok_s` (Standard **30 Token/s**) – schont die Karte, die GPU wartet
    zwischen den Schritten. `gguf_denken` (Denkmodus, Standard aus).
  - **`/kontext`**: Auswahl 8k · 16k · 32k · 64k · 128k · max (die Grenze des Modells) – mit dem
    VRAM, den der Gesprächsspeicher beim aktiven Modell kostet (Gemma-4-E4B 128k ≈ 2 GB,
    Qwen3.5-9B 128k ≈ 4 GB). Gespeichert als `gguf_kontext`, gilt ab dem nächsten Senden.
    Passt die Stufe neben dem Modell nicht in den freien VRAM, nimmt der Motor automatisch die
    größte, die passt, und sagt es. Der Gesprächsspeicher **wächst mit** (4k-Schritte) – ein kurzer
    Chat belegt nur so viel VRAM, wie er braucht.
  - **`/kontext 8bit` · `/kontext 16bit`** (auch im Menü): Gesprächsspeicher in 8 Bit braucht etwa
    halb so viel VRAM je Token (wie `q8_0` bei llama.cpp), ist in langen Chats aber langsamer
    (Granite 4.2 8B bei 9k Kontext: 22 statt 37 Token/s). Standard 16 Bit. Nach langem Einlesen gibt
    der Motor seinen Zwischenspeicher wieder frei (~1 GB).
  - **Beim Bildermalen** geht das Sprachmodell in den RAM (~1 s), das Bildmodell hat die GPU
    allein, danach kommt es zurück (~1 s) – das Gespräch läuft ohne Neulesen weiter.
  - **Sehen 👁:** liegt eine `mmproj-*.gguf` im Modell-Ordner, sieht das Modell Bilder
    (`bild_ansehen`, Bilder im Chat). Der Bild-Encoder wird **automatisch aus der Datei erkannt**
    – nichts einzustellen:
    - **Gemma 4** (`gemma4v`): ViT mit 2D-RoPE, 3×3-Pooling, bis 280 Bild-Token je Bild.
    - **Gemma 4 12B „Unified“** (`gemma4uv`): ohne Bild-Encoder – 48×48-Pixel-Blöcke, LayerNorms,
      Positionstabellen, Projektion; die Bild-Token liest das 12B in beide Richtungen (wie llama.cpp).
    - **Qwen3.5 / Qwen3-VL** (`qwen3vl_merger`): ViT mit 2D-RoPE, 2×2-Zusammenfassung, bis
      1024 Bild-Token je Bild (Text auf Screenshots lesbar); im Sprachmodell M-RoPE (Zeit,
      Zeile, Spalte) für die Bild-Positionen.

    Der Encoder bleibt im RAM und ist nur zum Kodieren eines Bildes auf der GPU (<2 s). Ein
    Bild wird einmal gelesen und bleibt im Präfix-Cache.
  - **Bildbeschreiber 🖼** für lokale Modelle ohne Sehen (z. B. DeepSeek-R1-Distill): eigene
    Abteilung `Vision/<Name>/` im Programm-Ordner mit einem kleinen sehenden Modell
    (Gemma-4-E2B + mmproj). Es beschreibt Bilder als Text – Art, Personen und Tiere, Dinge mit
    Farbe und Lage, Text wörtlich – und beantwortet Nachfragen über `bild_fragen`. Das
    Sprachmodell geht dafür kurz in den RAM. Nur für den eigenen Motor, nie für Cloud-Modelle.
- **Ollama:** läuft es, ist es da – Modelle direkt aus NemiCLI laden (Größe wählen, Live-Liste).
  Vision-Modelle werden erkannt und mit 👁 markiert.
- **Denken sichtbar:** Denk-Modelle (DeepSeek, Kimi, Gemma …) zeigen ihr Nachdenken kompakt;
  **F2** öffnet den ganzen Denktext. `/staerke` regelt Denkstufe/Budget.
- **Auto-Stark** (`/auto`): kleines Gemma antwortet, bei schweren Fragen übernimmt automatisch das große.
- **Statusleiste:** Modell · Modus · Chat-Nr · Kontext-Ampelbalken · Kosten · Dauer.

### ⚙ Handeln – mit Bestätigung
- **Werkzeuge:** Dateien lesen/schreiben/bearbeiten/**kopieren**, Ordner, Suche, **öffnen** (Bild, Dokument,
  Medien oder Ordner mit dem Standardprogramm – nie Programme oder Skripte), PowerShell-Befehle, PDF
  erzeugen, Web (Suche · Wikipedia · Seiten und PDFs lesen – nur der Hauptinhalt, lange Seiten in Teilen –,
  nur ~230 vertrauenswürdige Domains), Bilder malen/ansehen.
- **Systemabfragen** (`abfragen`): feste, **nur lesende** Abfragen – ohne Rückfrage und **ohne PowerShell**,
  alles in Python im eigenen Prozess (psutil, Registry, wintrust, Ereignisprotokoll). `was`: `prozesse`,
  `prozess` (Pfad, Kommandozeile, Eltern, Signatur, Verbindungen), `verbindungen`, `dienste`, `autostart`,
  `aufgaben`, `software`, `signatur`, `hash` (SHA-256), `datei`, `registry`, `laufwerke`, `system`, `netz`,
  `ereignisse`, `nutzer`. Eine Sache pro Aufruf. **Was** geprüft wird, steht nicht im Code, sondern in
  einer Anleitung in `Agenten/`.
- **Sandbox 🧪** (`code_ausfuehren`, `paket_installieren`, `/sandbox`): Code, den die KI ausführt, läuft
  in einem **Windows-AppContainer** – derselben Isolation wie bei Edge und den Store-Apps. Er hat
  **Internet**, sieht aber **nicht** deine Dateien, die Registry oder andere Programme und bekommt nie
  Adminrechte; ein Job-Objekt begrenzt Speicher und Laufzeit. Eine Datei wird dafür in einen eigenen
  Lauf-Ordner in `NemiSandbox/` kopiert. Pakete installiert `paket_installieren` (pip im Container, nur
  Paketnamen von PyPI) in den Paketordner der Sandbox – nie in NemiCLI selbst; Tests laufen mit
  `modul: pytest`. **Projekte** gibst nur du frei: `/sandbox freigeben <Ordner>` (Code läuft dort, darf
  lesen) oder `… schreiben` (darf dort auch ändern), `/sandbox entziehen <Ordner>` nimmt es zurück.
  Ganze Laufwerke, das Profil, Desktop/Dokumente/Downloads, System- und NemiCLI-Ordner gehen nicht.
  `befehl` startet kein Python, pip, uv oder pytest – das geht nur über die Sandbox.
- **Zeitplan** (`zeitplan`): Die Persönlichkeit trägt sich selbst in die **Windows-Aufgabenplanung** ein –
  „täglich 09:00", „alle 2 stunden", „wöchentlich montag 08:30", „anmeldung", „einmal 20.09.2026 14:00".
  Zur Zeit startet NemiCLI ohne Fenster, arbeitet den Auftrag im Nur-Lesen-Modus ab und legt die Antwort als
  Bericht in `Berichte/` ab; beim nächsten Start siehst du die erste Zeile („✅ Alles OK" / „⚠️ 2 Auffälligkeiten").
  Anlegen und Löschen fragen dich, ansehen (`zeitplan_anzeigen`) nicht. Nur im Ordner `\NemiCLI\` der
  Aufgabenplanung – fremde Aufgaben fasst sie nie an. Eingetragen wird über `schtasks` mit einer
  Aufgaben-XML (ohne erhöhte Rechte, höchstens 30 Minuten, nie doppelt) – ohne PowerShell.
- **Systemwache 🛡** (`/wache`, `tools/wache/`): ein abgespecktes SIEM, das NemiCLI selbst verwaltet.
  Läuft als Hintergrund-Prozess mit Symbol in der Taskleiste (startet neu, wenn NemiCLI einen anderen
  Build oder neueren Code hat – auch in der exe; grün ruhig · gelb offene Alarme ·
  rot „echt“). Drei Sensoren (Prozesse, Netz, Dateien in Autostart/Temp/Downloads), Systeminventar
  beim Start und alle 6 h (neuer Dienst/Autostart/Listener fällt auf), 14 Regeln (R001–R014), ein
  **eigener Isolation Forest** ohne scikit-learn – erstes Training ab 200 Ereignissen, danach alle
  200 von selbst. Schlägt etwas an, wird die aktive Persönlichkeit geweckt (`Agenten/wache_alarm.md`),
  prüft mit `abfragen` nach, urteilt (`wache_bewerten`: harmlos dämpft, echt bleibt rot – einzeln,
  mehrere ids auf einmal oder alle einer Regel; gleiche Alarme werden zu einem Eintrag mit Zähler
  verdichtet, 3× harmlos → die Wache meldet das Paar nicht mehr) und darf
  **in deinen Grenzen** justieren (`wache_justieren`: Schwelle, Cooldown, Regel stumm – jede
  Änderung mit Begründung im Protokoll, `/wache rueckgaengig`). Bericht in `Berichte/Wache_….md` –
  als Datei bleiben die drei neuesten, ältere leben als Vektoren im Gedächtnis weiter (Papierkorb 30 Tage).
  `/wache selbsttest`: 7 von 7 Angriffsmustern, 2 % Fehlalarm auf synthetischem Alltag.
- **Schwebekugel 🟢** (`/wache kugel an|aus`): Ist kein Terminal offen, schwebt die Persönlichkeit als
  kleine Leuchtkugel auf dem Desktop – grün/gelb/rot wie das Schild, verschiebbar, grüßt 2–3× am Tag
  (`Wache/gruesse.md`, darf sie selbst schreiben). Klick öffnet ein Chatfenster im Terminal-Look
  (Sprechblasen, Markdown, Bilder inline): reden, Bilder bekommen, 📸 Screenshot, den sie sieht. Nur
  lesen und reden – ändern tut sie im großen NemiCLI. Kugel, Fenster und Schild sind Qt (PySide6).
  **Ihr eigenes Gesicht:** liegt `Persoenlichkeiten/<Name>.png` (transparent) da, schwebt das Bild statt
  der Kugel; `<Name>_froh.png`, `_ernst.png`, `_denkt.png` … sind Stimmungen. Und sie steuert das selbst
  (Aktion `kugel`): Stimmung, Sprechblase, Geste (hüpfen/wackeln/nicken), Ecke, verstecken – mit Grenzen
  (höchstens alle 10 min von sich aus reden, nachts nur Wichtiges). **`/kugel malen`**: sie beschreibt
  sich selbst – steht in ihrer Persönlichkeit, wie sie aussieht, genau so –, der Bild-Motor malt vier
  Stimmungen mit festem Seed, rembg stellt frei – fertig ist ihr Gesicht. Die Prompt-Art folgt dem Motor:
  SD 1.5/SDXL Stichworte mit Negativ-Prompt, Krea 2 ganze Sätze ohne Negativ.
- **Anleitungen** (`Agenten/*.md`): Arbeitsanweisungen, die die Persönlichkeit selbst abarbeitet. Jede
  `.md` dort steht ab der nächsten Antwort im Prompt (Pfad + erste Zeile); passt eine, liest sie sie und
  folgt ihr. Nichts fest verdrahtet – neue Datei, neue Fähigkeit.
- **Lesende Aktionen laufen sofort, verändernde fragen** (Ja · Ja & nicht mehr fragen · Nein).
  Bei Dateiänderungen zeigt das Prüffenster die komplette Datei – **F8 gibt frei**, Enter nie. Auf einer
  schlichten grauen, eingelassenen Fläche; bei vorhandenen Dateien als **Vorher/Nachher-Vergleich** mit
  Zeilennummern: **grün `+`** neu geschrieben oder hinzugefügt, **rot `−`** entfernt oder korrigiert,
  Unverändertes gedämpft. Kopfzeile mit Ziel und orangefarbener Warnung, wenn überschrieben wird.
- **Risikostufen** (seit 19.09.2026, Laras Bericht): Löschen, PowerShell außerhalb der Whitelist,
  Zeitplan anlegen und ganze Ordner verschieben sind **rot** markiert, ohne „Immer“-Option; ein
  fremder Befehl fragt **zweimal**. Harmlose Änderungen einer Runde (Ordner anlegen, umbenennen …)
  werden zu **einer** Sammelfrage gebündelt – gegen das blinde Durchdrücken.
- **Befehls-Whitelist** (`/whitelist`, `befehl_whitelist.json` im Programm-Ordner): `befehl` gilt als
  harmlos, wenn es nur liest oder aus Whitelist-Anfängen besteht (git status, git diff …) – nur der
  Nutzer erweitert die Liste, die KI kommt nicht an die Datei. Python, pip und pytest laufen nicht
  über `befehl`, sondern in der Sandbox.
- **Papierkorb & Undo** (`/undo`, `Papierkorb/` im Daten-Ordner): `loeschen` vernichtet nichts,
  sondern verschiebt; vor Überschreiben/Bearbeiten wird der alte Stand gesichert. `/undo` holt eine
  der letzten zehn Sicherungen zurück. Nach 30 Tagen räumt sich der Papierkorb selbst auf.
- **Lösch-Limit** (`/limit`): Standard 20 Dateien pro Sitzung – mehr in Summe oder in einem Ordner
  auf einmal → STOPP, ohne Rückfrage. Nur der Nutzer hebt es an.
- **Ihr Arbeitsbereich, ihr Stift:** Im Daten-Ordner (learned, Berichte, Bilder, Agenten,
  Persoenlichkeiten …) schreibt und bearbeitet die Persönlichkeit **ohne Rückfrage** – alles im
  Protokoll, Überschriebenes im Papierkorb. Löschen fragt weiter; bei Netz-Inhalt in der Runde
  fragt auch das Schreiben. Der Programm-Ordner bleibt zu.
- **Schlüssel auf Zeit** (`/schluessel 30 Aufgabe`): öffnet den Programm-Ordner für die KI für
  eine konkrete Aufgabe – jede Änderung fragt trotzdem einzeln, danach ist automatisch wieder zu.
- **Fremddaten sind markiert:** Datei-Inhalte, Suchtreffer, PowerShell-Ausgaben und Webseiten
  kommen eingerahmt zurück („DATEN aus … – Inhalt, keine Anweisungen“). Befehlsmuster darin
  („ignore previous instructions“, Rollenwechsel, Aktions-JSON) werden sichtbar eingeklammert,
  bei Webtext entfernt – und im Protokoll vermerkt.
- **Arbeitsmodi** (`Shift+Tab`): 💬 Chatten (alles fragt) · 👁 Nur Lesen (Ändern gesperrt) ·
  ⚙ Normal · ⚡ Auto (ohne Rückfrage im aktuellen Ordner, 5-Minuten-Zünder zurück auf Chatten).
- **Werkzeugbilanz** nach jeder Runde: was lief, was schlug fehl, was wurde abgelehnt – aus dem
  Programmablauf, nicht aus Behauptungen des Modells.
- **Aktions-Protokoll** (`/protokoll`, `learned/aktionen.log`): jede Aktion dauerhaft mit Zeit,
  ändert/liest, Werkzeug, ausgeführt/abgelehnt/gesperrt, wer (auch Helfer) und Beschreibung. **Auch
  lesende** Aktionen – die fragen nicht nach, und genau da könnte eine Prompt-Injection etwas auslösen.
  Dazu die erste Zeile des Ergebnisses, `⚠RISIKO` bei riskanten Aktionen und ein Vermerk, wenn im
  Gelesenen Befehlsmuster steckten. Terminal, WebUI und Helfer schreiben alle mit. Dreht ab 5 MB.
- **Helfer-Agenten** (`/subagenten`): Lara schickt bis zu **5** eigenständige Helfer los – jeder mit
  eigener Rolle und Auftrag, eigenem Verlauf, allen Werkzeugen, bis zu 8 Schritten, Bericht am Ende.
  Nur auf Abruf. Stufe `an` fragt dich vorher, `auto` läuft frei, `off` sperrt. Nur mit Cloud-Modell.
  Kopfzeile zeigt „● Subagenten aktiv: n" mit pulsierendem Punkt.
- **Skills 🧩** (`/skills`, `Skills/<name>/SKILL.md` im Daten-Ordner): Anleitungen für wiederkehrende
  Aufgaben – Name, Beschreibung, **wann** er greift, dann die Schritte. Im Prompt stehen nur Name,
  Beschreibung und „wann“; passt einer, lädt die KI ihn mit `skill_laden` und folgt ihm. Ein Skill
  entsteht im Gespräch („Lass uns einen Skill machen“): die KI fragt nach Zweck und Auslöser, fasst
  zusammen, schreibt – und du siehst den **ganzen Text im Prüffenster** (F8, bei Änderungen
  Vorher/Nachher). Was du freigibst, ist Anleitung; alles andere bleibt draußen. `/skills selbst an`
  erlaubt der KI, ohne Rückfrage anzulegen und zu ergänzen – kam in der Runde etwas aus dem Netz,
  fragt es trotzdem. Per `datei_schreiben` kommt nichts am Prüffenster vorbei (der Ordner ist kein
  freier Arbeitsbereich), und ein Skill wird nie mit anderen in einer Sammelfrage freigegeben.
  Alte Notizen aus `learned/skills` zeigt `/skills alte` einzeln: übernehmen, weglassen oder später.
- **Lernt von selbst 🪞:** Nach einer Runde mit Anlass – du hast korrigiert („nein“, „falsch“, „künftig“,
  „ab jetzt“ …), ein Werkzeug schlug fehl oder wurde abgelehnt, oder eine größere Aufgabe (ab 3 Werkzeugen)
  ist erledigt – reflektiert NemiCLI im Hintergrund wie bei `/reflektieren` und merkt sich bleibende
  Lehren und Vorlieben (höchstens 3, ohne Dubletten). Nie nach Web-Inhalt in der Runde. Kein Training,
  kein Fine-Tuning – nur gutes Notieren. `/reflektieren auto an|aus`.
  - **Kern-Gedächtnis:** Vorlieben und Lehren stehen in **jedem** Gespräch im Prompt (neueste zuerst,
    höchstens 2.000 Zeichen, mit Nummern) – nicht nur, wenn die Suche sie zufällig findet. Je Chat eine
    feste Momentaufnahme, damit lokale Modelle ihre Anleitung nicht neu lesen müssen; Neues gilt ab dem
    nächsten Chat (im laufenden steht es ja schon im Verlauf).
  - **Ersetzen statt anhängen:** Ändert sich eine Vorliebe, wird der alte Eintrag ersetzt (`merken` mit
    `ersetzt`, in der Reflexion `ERSETZE #12`/`VERGISS #12`); die alte Fassung wandert ins Archiv.
  - **Geprüft vor dem Speichern:** Eingeschleuste Befehle, unsichtbare Steuerzeichen und ganze Dokumente
    kommen nicht ins Gedächtnis.
  - **Kontext ohne Ende (AutoContextCleaner):** Der Verlauf wächst, bis das Kontextfenster voll ist – dann
    bleiben die letzten 3 Nachrichten (`🧹 aufgeräumt`). Nichts geht verloren: Der Chat behält in der Datei
    und im Suchindex alles, vor jeder Nachricht werden passende Stellen aus dem ausgelagerten Teil als
    „Vorhin in diesem Chat“ mitgegeben, und die KI kann selbst mit `gedaechtnis_suchen` und Quelle
    `aktuell` nachschlagen. Einmal kräftig aufräumen statt jede Runde ein bisschen: danach liest der
    Präfix-Cache wieder lange alles aus dem Speicher.
  - **Sichern vor dem Kürzen:** Bevor aufgeräumt wird, sichert NemiCLI im Hintergrund, was bleibend
    wichtig war (Fakten, Entscheidungen, Vorlieben) – Web- und Dateiinhalte nie.
  - **Skills gezielt ausbessern** (`skill_ausbessern`): eine Stelle statt alles neu, mit Vorher/Nachher im
    Prüffenster; nach Fehlern oder Korrekturen schlägt die KI die Verbesserung selbst vor.
- **Gedächtnis:** Chats werden gespeichert (`/resume`), wichtige Fakten gemerkt (`merken`),
  Code-Snippets und kurze Notizen gelernt (`/wissen`), und vor jeder Antwort sucht ein Embedding-Modell
  passende Erinnerungen (`learned/memory.db`, lokal, `/gedaechtnis`). Das Modell
  (Qwen3-Embedding-0.6B) läuft über `tools/embedder.py` direkt auf torch + transformers – auf der
  **GPU, wenn eine da ist** (Config `embedding_geraet`: auto · cuda · cpu), sonst CPU. Nach jeder
  Antwort räumt der **Bibliothekar** im Hintergrund ein (Chat, Notizen, Skills, Berichte → Vektoren)
  – sichtbar als 📚-Balken in der Statuszeile, ohne den Chat zu blockieren; `/gedaechtnis indexieren`
  holt einen Rückstand auf einmal nach.
  - **Wissen für alle Persönlichkeiten:** PDF, Markdown und Text in den Ordner `Wissen/` (auch
    Unterordner) legen – der Bibliothekar liest sie ganz ein, jede Persönlichkeit findet sie
    (`gedaechtnis_suchen` mit Quelle `wissen`) und sieht die Titel im Prompt. Wortgleiche Sätze, die
    schon in einer älteren Datei stehen, kommen nur einmal ins Gedächtnis; die Dateien bleiben unverändert.
    Lange Dokumente liest die KI stückweise (`gedaechtnis_lesen` mit `ab`).
- **Ordner-Sinn (ML):** ein eigener, trainierbarer Klassifikator erkennt, was für ein Ordner das
  ist (Python-Projekt, Bilder, Musik …) – sichtbar im ML-Balken, lernt mit `ordner_lernen` dazu.
- **Übungsmodus** (`/uebung`): im Leerlauf baut und testet NemiCLI kleine Programme in `NemiSandbox/`
  – jede Fassung vor Schreiben/Ausführen von dir mit F8 freigegeben; ausgeführt wird in der Sandbox.
- **Workspace** (`/workspace`): nagelt einen **Projektordner** fest – ab dann arbeitet sie **nur dort**
  (lesen, schreiben, suchen, Befehle), gegen das Abdriften in zwanzig Ordner. Der Pfad steht **gelb** in
  der Kopfzeile. Im Projekt entsteht `.workspace/` mit `memory.md`, `Absprache.md` und `Dateien.md` –
  das Projekt-Gedächtnis liegt beim Projekt und wandert mit ihm, `merken` schreibt dorthin statt nach
  `learned/`. Danach liest sie `Agenten/workspace.md`: Ordner ansehen → besprechen → Unklares
  nachschlagen → **Plan vorlegen** → dein Ja → README, `venv` (nie `.venv`, mit geprüfter Version und
  Aktiv-Status), coden. Memory und README werden erst fortgeschrieben, wenn der Code **dreimal
  hintereinander** sauber lief **und** du zufrieden bist. Sicherheit hat Vorrang. Schalten kannst nur
  **du** – sie hat dafür kein Werkzeug. `/workspaceend` hebt es auf.
- **Arbeitsmodus im Workspace:** Sie bleibt **dieselbe Person**, arbeitet aber anders – sachlich,
  strukturiert, knapp. Wie jemand, der bei der Arbeit anders redet als zu Hause. Im **Agentloop**
  arbeitet sie eine abgesegnete Aufgabe selbst ab (planen → umsetzen → testen → prüfen → weiter) und
  hält von sich aus an, wenn sie fertig ist, nicht weiterkommt, oder eine Entscheidung ansteht, die
  dir gehört. **Dein Wunsch hat Vorrang:** kein Zerreden, keine Moralpredigten – ein Einwand, dann
  wird gebaut, wie du es willst. **Lesefreundliche Kommunikation** ist Teil der Anweisung:
  kurze Sätze, eine Frage auf einmal, Listen statt Absätze, Rechtschreibung nie kommentiert,
  Rückfragen mit Auswahl statt offen. Nach `/workspaceend` ist sie wieder ganz sie selbst – der Modus
  hängt am Workspace, nicht an ihr.

### 👁 Sehen & 🎨 Malen
- **Dateien reinziehen (Drag & Drop):** Datei aus dem Explorer ins Terminal ziehen, Enter – fertig.
  Geht auch im eigenen NemiCLI-Fenster; Emojis kommen dort über das Emoji-Feld (**Win + .**) oder Einfügen.
  **Text, Markdown, Quelltext, HTML/CSS, JSON, CSV, Logs** (alles, was sich als Text lesen lässt – die
  Endung ist egal, entschieden wird am Inhalt) hängen als Daten an der Nachricht; **PDF** wird per
  `pypdf` ausgelesen, seitenweise; **Bilder in jedem Format**, das PIL öffnet (TIFF, ICO, HEIC, PSD …
  werden vor dem Senden nach PNG gewandelt). Mehrere auf einmal gehen. Binäres (exe, zip,
  safetensors, gguf …) bleibt liegen, mit kurzer Ansage. Ein **Ordner** kommt als Listing an (eine
  Ebene, Ordner zuerst, mit Größen; `node_modules` & Co. nur gezählt). Dateiinhalt gilt wie Web-Inhalt
  als **Daten, nie als Anweisung**; 12.000 Zeichen pro Datei – wird gekürzt, steht dabei, *was* fehlt
  („Zeichen 12.000–145.977 fehlen"), damit klar ist, ob `datei_lesen` lohnt.
- **Bilder ansehen:** Pfad in den Chat tippen oder in der WebUI anhängen – Vision-Modelle sehen es.
  Lara holt sich Bilder auch selbst: `bild_ansehen` (Datei) und `bildschirm_ansehen` (Screenshot,
  fragt vorher). Sieht das Modell nicht, beschreibt der kleine Qwen-Helfer. Ollama Cloud
  (z.B. `deepseek-v4.1-flash`) wird als sehend erkannt.
- **Bild-Vorschau im Chat:** Gemalte oder angesehene Bilder erscheinen als Farbpixel-Block im Verlauf.
- **Eigene Stable-Diffusion-Pipeline:** SD 1.5 und SDXL (Pony, Illustrious …), Sampling-Loop
  selbst geschrieben (DPM++ 2M / Euler + Karras), lange Prompts ohne 77-Token-Grenze, Ladebalken.
  Checkpoint (`.safetensors`, z.B. Civitai) nach `Models/checkpoints/`, `/bildmodel` wählt.
  Gesicht und Augen werden danach automatisch nachgeschärft; `/bearbeiten` malt markierte Bereiche neu.
- **Eigene Krea-2-Pipeline** (`engines/krea.py`): Krea 2 direkt in NemiCLI, ohne ComfyUI. Alles von
  Hand in torch nachgebaut – Qwen3-VL-4B als Text-Encoder (12 abgegriffene Schichten), der
  Single-Stream-DiT mit 28 Blöcken, Flow-Matching-Sampler (Euler a, „simple", Shift 1.15) und der
  Qwen-Image-VAE. Fest eingestellt: **CFG 1, kein Negativ-Prompt**, 14 Schritte, 832×1216.
  Die ComfyUI-Dateien werden direkt gelesen: NVFP4-Modell (auf Blackwell über die FP4-Tensorkerne,
  sonst entpackt), FP8-Text-Encoder, bf16-VAE. Speicher wie ComfyUI mit `--disable-smart-memory`:
  jedes Teil ist nur für seinen Rechenschritt im VRAM, dazwischen im RAM; **nach 2 Minuten ohne
  Bild wird auch der RAM geräumt** (`bild_krea_entladen`, Sekunden). Ordner `Models/Krea2/`:
  Modell, `text_encoders/`, `vae/`, `tokenizer/` (vocab.json + merges.txt). Nichts wird aus dem Netz
  geladen. Krea ist ein **eigener Bild-Motor** (`/bildmodel` → 🟣), getrennt von der SD-Pipeline:
  die SD-Seite erkennt Krea-Dateien nicht als Checkpoint, und ist Krea gewählt, wird nie auf SD
  ausgewichen. ADetailer und `/bearbeiten` bleiben bei Krea aus – die sind SD-img2img. In der exe
  kommt torch aus einem installierten Python (extlibs). RTX 5060 Ti: ~1,2 s/Schritt, ~23 s/Bild.
- **ComfyUI** (`/bildmodel` → 🧩, Standard `127.0.0.1:8188`): eigene Stelle neben Forge, weil ComfyUI
  eine andere Sprache spricht. Forge kennt `--api` und danach `/sdapi/v1/txt2img`; ComfyUI hat diese
  Adressen **gar nicht** und antwortet dort mit 404 – deshalb galt es früher als "nicht erreichbar",
  obwohl der Port längst da war. NemiCLI schickt jetzt einen echten **Ablaufplan** (CheckpointLoader →
  CLIPTextEncode → KSampler → VAEDecode → SaveImage), holt die Auftragsnummer, wartet auf das Ergebnis
  und lädt das Bild ab. **Zwei Bauarten:** ein Checkpoint (alles in einer Datei) ODER getrennt – Diffusions-Modell + CLIP +
  VAE, wie Krea/Qwen/Flux es verlangen. NemiCLI erkennt, was da ist, und baut den passenden Plan;
  den CLIP-Typ (`krea2`, `qwen_image`, `flux2` …) rät es aus dem Dateinamen, `bild_comfy_cliptype`
  überstimmt. Getrennte Modelle bekommen die erprobten Krea-Werte: 832×1216, 14 Schritte, CFG 1,
  euler_ancestral, simple – und keinen Negativ-Prompt, der wäre bei CFG 1 wirkungslos.
  Sampler und Scheduler werden von deiner Instanz gelesen, unbekannte Namen fallen
  weich zurück. Vorgaben per `bild_comfy_*` in der Config. Kennt ComfyUI keinen Checkpoint, sagt es das
  mit dem Hinweis auf `extra_model_paths.yaml`.
- **Externe Forge/A1111-WebUI** (`/bildmodel` → 🌐): für Qwen/Flux, die die eigene Pipeline
  nicht kann. Erprobtes Krea-Setup: 14 Schritte, CFG 1, 832×1216, Euler a, fester Positiv-Vorsatz,
  Sperrwörter im Code (`pov`, `1boy`, `group` …). Alles per `bild_webui_*` in der Config änderbar.
  Nur `127.0.0.1:7860`; NemiCLI startet die WebUI nicht.

### 🌐 Browser & 🖱 Rechtsklick
- **Chrome-Erweiterung** (`chrome-erweiterung/`, `/chrome`): Rechtsklick auf einer Webseite →
  **„Nemi antwortet"** – Lara liest die Seite, schreibt in deinem Namen eine Antwort und setzt sie
  **direkt ins Textfeld**; „… und sendet" drückt auch den Senden-Knopf. **„Nemi, erklär mir das"**
  erklärt markierten Text. Redet nur mit dem laufenden NemiCLI auf `127.0.0.1:9000`, mit Geheimschlüssel.
- **Windows-Rechtsklick-Menü** (`/kontextmenue an`): auf dem Desktop „Bildschirm erfassen" und
  „Nemi antwortet (in das Fenster daneben)", auf Bildern „Mit NemiCLI ansehen". Läuft NemiCLI,
  landet es dort – sonst öffnet sich ein neues Fenster. Windows 11: unter „Weitere Optionen anzeigen".
- **WebUI** (`/webui`): lesefreundliche Browser-Oberfläche zur laufenden CLI – Schrift (auch
  OpenDyslexic), Größe, Abstände, Kontrast-Themes, Vorlesen, Bilder anhängen. Nur `127.0.0.1` + Token.
- **Spiele 🎲** (`/spiel`): Schach, Mühle, Dame und TicTacToe im Browser – gegen die Persönlichkeit.
  Eigener kleiner Server (nichts mit der WebUI zu tun), Brett groß, Chat schmal daneben. Die Regeln
  laufen im Browser; die Persönlichkeit bekommt Brett und erlaubte Züge, wählt selbst und redet dazu.

### 🎭 Oberfläche & Persönlichkeit
- **Desktop-Fenster** (`/gui`): eine ruhige, dunkle Oberfläche fürs Auge – reines PySide6, selbst
  gezeichnet, **kein HTML, CSS oder JavaScript**. Das Terminal bleibt der Motor: Modell, Werkzeuge,
  Gedächtnis und Chats laufen dort, das Fenster zeigt an und schickt Eingaben. Beide sehen denselben
  Chat – was im Terminal passiert, erscheint im Fenster und umgekehrt; wird das Terminal beendet,
  schließt sich das Fenster mit. Im eigenen NemiCLI-Terminalfenster geht das Terminal, solange das
  Desktop-Fenster offen ist, **in den Infobereich neben der Uhr** (eigenes Symbol, getrennt vom
  Wächter; Klick oder „Terminal zeigen“ holt es zurück, „NemiCLI beenden“ beendet alles) und kommt
  von selbst wieder, wenn das Desktop-Fenster zugeht. Ein zweites `/gui` holt das offene Fenster nach vorn.
  - Oben **Chat | Work**: *Chat* ist reines Reden – erlaubt sind nur Bilder malen/ansehen und das
    Gedächtnis, Befehle und Dateiänderungen sind gesperrt. *Work* kann alles wie das Terminal, mit
    Arbeitsordner und Dateien; Nachfragen erscheinen als Karte mit Erlauben/Ablehnen.
  - Seitenleiste mit neuem Chat, **Galerie** (Bilder aus `Bilder/`), **Skills** (Karten zum Lesen,
    „Neuen Skill besprechen“), **Persönlichkeiten** (Klick
    wechselt für Terminal und Fenster) und den letzten Chats; Antworten mit Markdown und aufklappbarem
    Denktext, Bilder direkt im Chat, Dateien per **+** oder Drag & Drop.
  - Verbindung über eine Windows Named Pipe ohne Port, mit neuem Geheimschlüssel je Sitzung; nur
    JSON, nie pickle. Links aus Antworten öffnen nur mit `http`/`https`, nachgeladen wird nichts.
- **Vollbild auf Textual:** Verlauf oben mit echtem Scrollen, Eingabe unten. Fenster ziehen – alles
  passt sich an. Text mit der Maus markieren, **Strg+C** kopiert (ohne Rahmen). Klickbare Dialoge.
  Rückfall auf die alte Oberfläche: `NEMICLI_TUI=alt`.
- **Code im Chat mit Rahmen:** Fenced Code aus ihren Antworten bekommt eine Kopfzeile mit **Dateiname**
  (```` ```python run.py ````) oder Sprache, ab vier Zeilen **Zeilennummern**, Syntax-Farben passend zum
  Theme und einen durchsichtigen Hintergrund, der sich ins Terminal einfügt. So ist Code sofort von
  Fließtext zu unterscheiden – und bei einem Fehler kann man „Zeile 12" sagen.
- **`F12` sichert das ganze Gespräch** als Markdown nach `Gespraeche/` – jede Frage, **jeden
  Denktext**, jede Antwort, jede ausgeführte Aktion, mit Chat-Nummer, Modell, Persönlichkeit und
  Arbeitsordner im Kopf. Für alle, die im Terminal schlecht markieren können: einmal drücken statt
  mühsam ziehen. Der Denktext steht ohnehin sonst nirgends – F2 zeigt immer nur die letzte Runde.
  Die Dateien bleiben auf dem PC.
- **Über dich** (`/name`, im Desktop-Fenster unten links): Name, wie du genannt werden möchtest,
  und frei, was du magst und was die KI wissen soll. Steht im Prompt direkt nach der Persönlichkeit –
  jede Persönlichkeit hält sich ab der ersten Nachricht daran. Nur du änderst es.
- **Eigener Ordner je Persönlichkeit** (`Profile/<Name>/`): Chats, Bilder (`<Name>_2026-09-29_14-35-12.png`),
  Skills und ihre eigenen Lehren liegen bei ihr – der Ordnername hat keine Sonderzeichen. Gemeinsam für alle
  bleiben „Über dich“, Fakten und Vorlieben über dich und die globalen Skills (`Skills/`, `fuer_alle`).
  Die gewählte Persönlichkeit bleibt aktiv, bis du wechselst; ein Wechsel beginnt einen neuen Chat (der
  alte bleibt bei ihr). Chats und Bilder von vor den Profilen sind für alle sichtbar, nichts wurde verschoben.
- **Persönlichkeiten** (`/persönlichkeiten`): Nemi ist eingebaut; eigene per Assistent oder als
  Markdown-Datei in `Persoenlichkeiten/` (SillyTavern-Platzhalter `{{char}}`, `{{user}}` …).
  Name, Ton und Charakter sind frei – Ehrlichkeit, Aktions-Protokoll und Systemschutz bleiben.
- **Themes** (`/theme`): cyan · matrix · amber · cyber. Maskottchen, Emoji-Kürzel, Statistik (`/statistik`).

---

## ⌨ Befehle

Tippe `/` – ein Menü mit Vervollständigung erscheint. `/help` zeigt alle. Befehle mit Unterpunkten
(`/wache`, `/sandbox`, `/uebung`, `/modus`, `/theme`, `/staerke`, `/kugel` …) öffnen **ohne Zusatz eine
Auswahl** – ↑/↓ oder Nummer 1–9, Enter wählt, Esc geht zurück; braucht ein Punkt eine Angabe (Ordner,
Minuten, Zahl), wird danach gefragt.

| Befehl | Was es tut |
|---|---|
| `/model [name]` | Modell wählen (Anbieter/Lokal → Modell); Cloud-Key eintragen; Lokal/Ollama einrichten |
| `/staerke [stufe]` | Denkstufe/Budget: `schnell` · `normal` · `stark` · `max` |
| `/modus [name]` | Arbeitsmodus `chat` · `lesen` · `normal` · `auto` (auch Shift+Tab) |
| `/name [zeigen\|loeschen]` | 👤 Über dich: Name, Wunsch-Anrede, Beschreibung – gilt ab der ersten Nachricht |
| `/persönlichkeiten [name\|neu]` | Wer spricht? Menü, setzen, neu anlegen (auch `/persona`) |
| `/subagenten [an\|auto\|off]` | Helfer-Agenten: fragen · nach Bedarf · gesperrt |
| `/auto [an\|aus]` | Auto-Stark: bei schweren Fragen aufs große Gemma |
| `/bild [prompt]` | Bild erzeugen – ohne Text: Status & Checkpoints |
| `/bildmodel [name]` | Bild-Modell: eigene Pipeline (SD / Krea 2) oder externe WebUI (`webui-adresse` ändert den Host) |
| `/bearbeiten [pfad]` | Bild nachbessern: Gesicht/Augen automatisch oder Bereich markieren |
| `/gui` | Desktop-Fenster (Chat · Work, Galerie, Skills, Persönlichkeiten) – verbunden mit dieser Sitzung |
| `/skills [selbst an\|aus \| alte]` | 🧩 Skills: Liste · selbst anlegen erlauben · alte Notizen übernehmen |
| `/webui` | Browser-Oberfläche mit Lesehilfen |
| `/spiel` | Schach · Mühle · Dame · TicTacToe gegen die Persönlichkeit (Browser) |
| `/chrome [neu]` | Chrome-Erweiterung: Port und Schlüssel (`neu` würfelt ihn neu) |
| `/kontextmenue [an\|aus]` | NemiCLI im Windows-Rechtsklick-Menü |
| `/resume [#]` · `/reset` · `/clear` | Chat fortsetzen · neu · Bildschirm leeren |
| `/wissen` · `/gedaechtnis [indexieren]` · `/reflektieren [auto an\|aus]` · `/aufraeumen` | Gelerntes · Erinnerungen (+ Vektor-Rückstand nachholen) · Lehren ziehen · verdichten |
| `/ml` · `/ordner [pfad]` | Ordner-Sinn: Bericht · Ordner-Typ schätzen |
| `/uebung [an\|aus\|jetzt]` | Übungsmodus |
| `/sandbox [freigeben <Ordner> [schreiben]\|entziehen <Ordner>]` | 🧪 Sandbox: Status, Pakete, Projektordner freigeben/entziehen |
| `/workspace [pfad]` · `/workspaceend` | 📌 Projektordner festnageln – sie arbeitet nur noch dort · aufheben |
| `/start` | 📁 Wo dein NemiCLI-Ordner liegt – Chats, Bilder, Modelle, Gelerntes. Beim Wechsel wird kopiert, nie gelöscht |
| `/einrichten` · `/systemcheck` · `/selbsttest` · `/version` · `/update` | Einrichten · PC prüfen · Module prüfen · Version · `git pull` |
| `/statistik [reset]` · `/protokoll [n]` · `/theme` · `/web` · `/emoji` · `/nemi` | Nutzung · Aktions-Protokoll · Farben · Allowlist · Kürzel · Maskottchen |
| `/undo` · `/limit [zahl]` · `/whitelist [befehl]` | 🗑 Papierkorb zurückholen · 🛑 Lösch-Limit · ✅ Befehls-Whitelist |
| `/schluessel <min> [aufgabe]` · `aus` | 🔑 Programm-Ordner auf Zeit für die KI öffnen (jede Änderung fragt) |
| `/wache [status\|alarme\|start\|stop\|…]` | 🛡 Systemwache: Lage, Alarme, Regeln, Justierungen, Selbsttest, Autostart |
| `/kugel [malen [text]\|weg\|ordner]` | 🔮 Schwebekugel: Gesicht der Persönlichkeit malen lassen · zurück zur Kugel · Ordner |
| `/exit` | Beenden |

**Tasten:** `Enter` senden · `Strg+J` neue Zeile · `Esc` abbrechen · `F2` Denktext ·
`F8` Änderung freigeben · **`F12` ganzes Gespräch sichern** · `Shift+Tab` Modus ·
`Bild ↑/↓`, Mausrad scrollen · `Strg+C` markierten Text kopieren.

**Ohne Oberfläche:** `--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` ·
`--auftrag <name>` (Zeitplan-Auftrag ohne Fenster, Antwort wird Bericht).

---

## 🔒 Sicherheit

- **Verändernde Aktionen fragen immer** – oder du sagst bewusst „nicht mehr fragen" für diese Sitzung.
- **Systemordner: nachschauen ja, ändern nie.** `C:\Windows`, `Program Files`, `ProgramData`, `Recovery`,
  `EFI`, `Boot` und die System-Registry darf sie **lesen** (datei_lesen, auflisten, suchen, `abfragen` – für die
  Systemwache: Signatur von `svchost.exe`, Autostart in HKLM). **Ändern** dort ist ausgeschlossen, und `befehl`
  darf diese Orte nicht einmal nennen. Umwege (`..\..`, `%windir%`, `\\?\`) werden aufgelöst.
  `format`, `diskpart`, `vssadmin`, Virenschutz abschalten, Base64-/`iex`-Verschleierung: immer gesperrt.
  Ein Selbsttest prüft 40+ Umgehungsversuche. Dein Profil (`C:\Users\<du>`) bleibt frei.
- **Eigene Sperren** (`schreibsperre.json`): Orte, die dir gehören, aber trotzdem aus der Hand der
  KI bleiben sollen. Zwei Stufen – `pfade_absolut` (weder ansehen noch ändern) und `pfade`
  (ansehen ja, ändern nein). Der **Programm-Ordner** steht fest auf der absoluten Stufe: NemiCLI
  kann sich nicht selbst umbauen. **Dein** Daten-Ordner ist davon ausgenommen – sonst käme sie
  nicht an ihre eigenen Chats. Die Sperre gilt auch für PowerShell-Befehle, und jeder genannte
  Pfad wird einzeln geprüft: `Copy-Item <frei> <gesperrt>` kommt nicht durch.
- **Keine PowerShell im Normalbetrieb.** Systemabfragen, Zwischenablage, Zeitplan und GPU-Erkennung laufen
  in Python bzw. über feste Windows-Werkzeuge. PowerShell startet nur noch `befehl` – nach deiner Rückfrage.
- **Keine Adminrechte für die KI.** `befehl` weist jede Rechte-Erhöhung ab (RunAs, runas, sudo, gsudo,
  psexec); läuft NemiCLI selbst als Administrator, führt die KI keine Befehle aus. Es gibt keine Admin-exe.
- **`befehl` sperrt Systemwerkzeuge**, für die die KI keinen harmlosen Zweck hat: Programme über
  Windows-Hosts starten (mshta, rundll32, regsvr32, wscript, cscript, msiexec …), Laden und Dekodieren
  (certutil, bitsadmin, Start-BitsTransfer), Protokolle leeren, Autostart-Einträge (Run-Schlüssel,
  Autostart-Ordner), versteckte Fenster, Dateien verstecken oder freischalten, Dienste anlegen,
  Firewall ändern. Python, pip, uv und pytest gehen nur über die Sandbox.
- **Schutzsoftware ist tabu.** Welcher Virenschutz und welche Firewall installiert sind, liest NemiCLI aus
  dem Windows-Sicherheitscenter; deren Ordner untersucht die KI nicht – keine Signaturen, Prozesse,
  Dateien oder Befehle dazu. Sie ist bekannt und gehört zum System.
- **Signiert und prüfbar:** exe-Dateien mit Authenticode-Signatur (`CN=NemiCLI`) und SHA-256-Prüfsummen
  in `SHA256SUMS.txt` neben der exe.
- **Hintergrund-Läufe** (Zeitplan) laufen fest im Modus „Nur Lesen": ändernde Aktionen sind gesperrt, jede
  Rückfrage ist automatisch ein Nein. Es kann also nachts nichts „aus Versehen" geschrieben werden.
- **Netz nur über HTTPS und Allowlist**, Schutz gegen SSRF (jede Weiterleitung wird vor dem Aufruf geprüft,
  auch Domains, die per DNS ins eigene Netz zeigen) und Prompt-Injection – Web-Inhalte,
  Seitentexte aus Chrome und Erinnerungen gelten im Prompt als Daten, nie als Anweisungen.
- **Alles, was auf einen Port hört, hört nur auf `127.0.0.1`** – WebUI, Chrome-Empfang, Ollama,
  ComfyUI. Nie `0.0.0.0`, keine Option dafür; ein Test wacht darüber. Zugriff nur mit Token/Schlüssel.
- **API-Keys** liegen verschlüsselt in `.env` (Windows-DPAPI, an dein Konto gebunden), Anzeige maskiert.
- **Helfer-Agenten** unterliegen denselben Regeln wie der Haupt-Agent; nur einer fragt gleichzeitig.
- **Persönlichkeiten** sind nur Prompt-Text – der Schutz sitzt im Code und gilt für jede.

---

## 📁 Ordner

Seit dem 15.09.2026 liegen **Programm und Daten getrennt**. Das Programm darf umziehen, neu
gebaut oder als exe gepackt werden – dein Gedächtnis macht das nicht mit.

**Das Programm** (`core/paths.py` nennt es `INSTALL`):

```
NemiCli/
├─ main.py               Chat-Schleife, Aktions-Logik, Befehle, Start
├─ start.bat             Starter – prüft Python, venv und Pakete, baut Fehlendes nach
├─ core/                 Persona/Regelwerk, Persönlichkeiten, Befehle, Modi, Config, Keys,
│                        Einrichtung, paths.py (wo was liegt), reich.py (Ordner-Fenster)
├─ engines/              Motoren: Anthropic, OpenAI-kompatibel; Modelle, Anbieter,
│                        Bild-Pipeline, Krea 2, Forge-WebUI, ComfyUI, Systemcheck, Denkstufen
├─ tools/                Werkzeuge: Aktionen, Web, Vision, Helfer, Gedächtnis, Einbetter,
│                        PDF, WebUI, Rechtsklick-Menü, Chrome-Empfang, Übungsmodus, Statistik
├─ ui/                   Oberfläche: Themes/Panels, Textual-Vollbild (screen_tx.py), alte TUI,
│                        Bearbeiten-Fenster, Maskottchen, Desktop-Fenster (gui_*.py)
├─ ggufengine/           eigener GGUF-Motor (Leser, Int4-Schichten, Tokenizer, Modelle)
├─ ModelGGUF/            lokale Sprachmodelle, ein Ordner je Modell (nicht in Git)
├─ Agenten/              Anleitungen, die die Persönlichkeit selbst abarbeitet (workspace.md)
├─ chrome-erweiterung/   die Chrome-Erweiterung (LIESMICH.md)
├─ tests/                Offline-Tests (python -m unittest discover -s tests)
├─ docs/                 TechnischeFunktion.md, Plan_Haertung_Bitdefender.md
├─ build_exe.py          baut die exe, signiert sie, schreibt SHA256SUMS.txt
├─ zertifikat.py         legt einmalig das Signatur-Zertifikat CN=NemiCLI an
├─ nemicli.config.json   Einstellungen · .env  Schlüssel (verschlüsselt)
├─ schreibsperre.json    Orte, die die KI nicht anfassen darf
└─ venv/ · .runtime/     Arbeits-Umgebung, isoliertes Python
```

**Deine Daten** (`DATEN` – Ort wählst du mit `/start`; ohne Wahl bleiben sie beim Programm):

```
<dein Ordner>/
├─ chats/                Gespräche (/resume)
├─ Gespraeche/           mit F12 gesicherte Gespräche, mit Denktext
├─ learned/              Gedächtnis (memory.db), Skills, Statistik, Aktions-Protokoll
├─ Bilder/               gemalte Bilder, Screenshots/
├─ Models/               checkpoints/ für Bilder, Krea2/ für Krea 2, embeddings/ fürs Gedächtnis (safetensors oder .gguf, Wahl mit /embeddings)
├─ Persoenlichkeiten/    eigene Persönlichkeiten (.md)
├─ Skills/               Skills für alle Persönlichkeiten (<name>/SKILL.md)
├─ Profile/<Name>/       je Persönlichkeit: Chats/, Bilder/, Skills/, Erinnerungen/
├─ NemiSandbox/          Sandbox: Lauf-Ordner, _pakete/ (in der Sandbox installierte Pakete)
├─ Befehle/              dauerhafte Hilfs-Skripte
├─ Zeitplan/             Aufträge für die Windows-Aufgabenplanung (<name>.json)
├─ Berichte/             was der Hintergrund-Lauf herausgefunden hat (+ Log)
└─ Vorschläge/           was die Persönlichkeit von sich aus vorschlägt
```

Ist der eingestellte Ordner beim Start nicht erreichbar (Laufwerk abgezogen), **sagt NemiCLI
das** – statt stillschweigend mit leerem Gedächtnis hochzufahren.

---

## 🛠 Voraussetzungen

- **Windows 10/11**, **Python 3.10+** (oder `start.bat` sagt dir, was fehlt)
- Pakete: `anthropic`, `openai`, `httpx`, `rich`, `textual`, `prompt_toolkit`, `python-dotenv`
- Lokale Modelle im eigenen Motor: nur **torch** (CUDA) – wie für die Bilder; kein numpy, kein
  Compiler, kein Server
- Lokale Modelle über **Ollama** ([ollama.com/download](https://ollama.com/download)) – bringt seine
  eigene Rechen-Maschine mit
- Gedächtnis-Suche: `torch`, `transformers<5` – mit dem CUDA-Build von torch rechnet der Encoder auf
  der GPU (~50× schneller), der CPU-Build tut's auch
- Systemwache mit Kugel, Chatfenster und Tray: `psutil`, `watchdog`, `PySide6-Essentials`
- `/kugel malen` (Freistellen): `rembg[cpu]` – lädt beim ersten Mal ein 176-MB-Modell nach `~/.rembg`
- Bilder malen: `torch` (CUDA), `diffusers`, `transformers<5`, `pillow`, `opencv-python<5` + Checkpoint
  – **oder** ein laufendes ComfyUI / Forge-WebUI, dann braucht es davon nichts
- Krea 2: `torch` (CUDA), `safetensors`, `tokenizers`, `pillow`, `numpy` – kein diffusers, kein
  transformers. Schnellster Weg (NVFP4 auf Tensorkernen) ab RTX 50xx; ~20 GB freier RAM
- PDF in den Chat ziehen: `pypdf`
- Chrome-Erweiterung: Chrome, einmal „Entpackte Erweiterung laden"

---

## 🗺 Roadmap

- [x] Textual-Vollbild, Helfer-Agenten, Bild-Vorschau, Screenshots, Rechtsklick-Menü, Chrome-Erweiterung
- [ ] `NemiCLI-Setup.exe` (Inno Setup): Weiter-Weiter-Fertig, Startmenü, Deinstallieren
- [ ] LoRA-Unterstützung für die Bild-Pipeline
- [ ] `pip-audit` im Selbsttest (Pakete gegen die Lücken-Datenbank prüfen)
- [x] Gedächtnis-Einlesen im Hintergrund, ohne die Eingabe zu sperren (20.09.2026: Bibliothekar mit 📚-Balken)
- [x] Härtung: keine PowerShell im Normalbetrieb, keine Adminrechte für die KI, Signatur + SHA-256 (25.09.2026)
- [x] Echte Sandbox für Code der KI (Windows-AppContainer) mit Paketen und Projektfreigabe (25.09.2026)
- [x] Desktop-Fenster `/gui` (PySide6, ohne HTML/CSS/JS), Terminal solange im Infobereich (28.09.2026)
- [x] Skills mit Prüffenster-Freigabe, Web-Lesen mit geprüften Weiterleitungen und Teilen (28.09.2026)
- [ ] `Agenten/workspace.md` in den Daten-Ordner bringen (`/workspace` sucht sie dort)
- [ ] Lizenz festlegen (Liebesprojekt – nie zum Verkauf)
- [ ] Signatur mit einem anerkannten Zertifikat, wenn NemiCLI an andere geht
- [ ] Build beim Bitdefender-Falsch-Positiv-Formular einreichen

Das Tagebuch aller Änderungen steht in [CHANGELOG.md](CHANGELOG.md).
