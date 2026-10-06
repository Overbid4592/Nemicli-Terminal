# NemiCLI – Funktionen im Detail

Die ausführliche Beschreibung aller Funktionen. Den Überblick, den Start und die Befehle
zeigt die [README](../README.md); wie es technisch gebaut ist, steht in
[TechnischeFunktion.md](TechnischeFunktion.md).

### 💬 Chat & Modelle
- **Ein Schema für alles:** `anthropic:claude-…`, `openai:gpt-…`, `ollama:gemma4:12b`, `gguf:Gemma4`.
  Drei Motoren (Anthropic nativ, OpenAI-kompatibel, eigener GGUF-Motor), eine Schnittstelle.
- **Modelle live** vom Anbieter abgefragt – nichts hartkodiert, neuer Key = neue Modelle.
- **Eigener GGUF-Motor 🧠** (`ggufengine/`, `/model` → „Lokal (eigener Motor)“): lokale Modelle
  ohne llama.cpp, Ollama oder Server – reines Python + torch **im NemiCLI-Prozess**, kein Port, kein
  Kindprozess. Gehärteter GGUF-Leser (jede Zahl aus der Datei wird vor Gebrauch geprüft, das
  Chat-Template der Datei wird nie ausgeführt), Gewichte bleiben quantisiert (Int4) im VRAM, CUDA
  Graphs. **Baukasten:** das Modell setzt sich aus der GGUF-Datei selbst zusammen – Llama 2/3.x, Mistral, Mixtral, Ministral 3, Qwen2/2.5/3 (auch MoE), Gemma 1–4, Phi-3/4, Granite, OLMo 2, Qwen3.5, K2-Horizon u. a. laufen ohne eigenen Code; dazu **Ling 3.0** (`bailingmoe3`: Kimi-Delta-Attention + MLA + 128 Experten, 1,3B aktiv – ~40–65 Token/s); Chat-Format und Tokenizer-Regeln werden erkannt. Jedes Modell ist ein Ordner
  in `ModelGGUF/` im Programm-Ordner (`ModelGGUF/Gemma4/…gguf`, eine `.gguf` mit „mmproj“ im Namen
  daneben ist der Bild-Teil).
  - **Eigener Code für besondere Bauarten:** die ganze Gemma-4-Familie (E2B/E4B, 12B/31B, MoE 26B-A4B),
    Qwen3.5 (Gated DeltaNet), Ling 3.0 und DeepSeek V2/V3/R1 samt Kimi K2 (`deepseek2`: Multi-Head-Latent-
    Attention, der Gesprächsspeicher hält nur den komprimierten Teil).
  - **Alle gängigen GGUF-Formate:** Q4_0 … Q8_0, K-Quants (Q2_K–Q6_K), die IQ-Familie (IQ1_S … IQ4_XS,
    IQ4_NL) und MXFP4 – beim Auspacken bitgenau wie die Referenz aus llama.cpp. Eigene CUDA-Kernel rechnen
    für ein Token direkt auf den gepackten Blöcken (übersetzt beim ersten Gebrauch mit NVRTC aus dem
    PyTorch-Paket, kein CUDA-Toolkit nötig). Gemma 4 26B-A4B: ~45 Token/s.
  - **MoE-Experten im RAM, automatisch** (Config `gguf_experten`: `auto` · `vram` · `ram`): Passt ein
    MoE-Modell samt Kontext nicht in den VRAM, bleiben so viele Experten im VRAM wie Platz ist; der Rest
    liegt in festgesetztem RAM und wird direkt über PCIe gelesen.
  - **Modelle von Hugging Face holen** (`/model huggingface`): Größe und MoE wählen, gezeigt werden nur
    Modelle, die der Motor laden kann (ab 4B), jede Datei mit SHA-256 geprüft, Abbruch wird fortgesetzt.
  - **⏱ Ladezeit je Schritt** nach dem ersten Laden: Datei, Tokenizer, Gewichte, Aufwärmen, Bildteil,
    Einlesen bis zum ersten Wort.
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
  - **Sehen 👁:** liegt eine mmproj-Datei im Modell-Ordner, sieht das Modell Bilder
    (`bild_ansehen`, Bilder im Chat). Der Bild-Encoder wird **automatisch aus der Datei erkannt**
    – nichts einzustellen:
    - **Gemma 4** (`gemma4v`): ViT mit 2D-RoPE, 3×3-Pooling, bis 280 Bild-Token je Bild.
    - **Gemma 4 12B „Unified“** (`gemma4uv`): ohne Bild-Encoder – 48×48-Pixel-Blöcke, LayerNorms,
      Positionstabellen, Projektion; die Bild-Token liest das 12B in beide Richtungen (wie llama.cpp).
    - **Qwen3.5 / Qwen3-VL** (`qwen3vl_merger`): ViT mit 2D-RoPE, 2×2-Zusammenfassung, bis
      1024 Bild-Token je Bild (Text auf Screenshots lesbar); im Sprachmodell M-RoPE (Zeit,
      Zeile, Spalte) für die Bild-Positionen.
    - **Ministral 3 / Mistral Small 3.x** (`pixtral`): ViT mit 2D-RoPE, 14×14-Patches,
      2×2-Patch-Merger, bis 1024 Bild-Token je Bild; jede Bildzeile endet mit `[IMG_BREAK]`.

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
- **Auto-Stark** (`/auto`, nur Ollama): Läuft ein kleines Gemma (E2B/E4B), schätzt es vor jeder Antwort
  selbst ein – bei schweren Fragen übernimmt automatisch das große Gegenstück (12B).
- **Statusleiste:** Modell · Modus · Chat-Nr · Kontext-Ampelbalken · Kosten · Dauer.

### ⚙ Handeln – mit Bestätigung
- **Werkzeuge:** Dateien lesen/schreiben/bearbeiten/**kopieren**, Ordner, Suche, **öffnen** (Bild, Dokument,
  Medien oder Ordner mit dem Standardprogramm – nie Programme oder Skripte), PowerShell-Befehle, PDF
  erzeugen, Web (Suche · Wikipedia · Seiten und PDFs lesen – nur der Hauptinhalt, lange Seiten in Teilen –,
  nur ~230 vertrauenswürdige Domains), Bilder malen/ansehen.
- **Coding-Assistent 🟦** (`/code`, startet bei Coding-Aufgaben auch von selbst): fester Weg
  Auftragskarte → Recherche → Todo-Liste (🔴 offen · 🟠 Frage · 🟢 geprüft) → Punkt für Punkt. Grün setzt
  nur die Prüfung (Prüfbefehl mit Exitcode 0) oder du – nie die KI. Ablage im Projekt unter `.nemicli/`,
  `/code status` zeigt die Liste, `/codeend` beendet. Kein zweites Modell.
- **Coding-Assistent bleibt aktuell:** sieht die installierten Versionen des Projekts, schlägt Funktionen in
  der **installierten** Bibliothek nach (`api_nachschlagen`), bekommt Veraltet-Warnungen und ruff-Funde
  (`code_pruefen`) als Aufgabe, prüft jede geschriebene Datei sofort (Python, JSON, HTML, CSS, JS), sieht
  Webseiten als Bild samt JS-Fehlern (`seite_ansehen`, Chromium-Browser headless), kennt neue Versionen und
  Lücken (`paket_info`) und sucht in der Offline-Doku von Python und MDN (`/doku`, `doku_suchen`).
- **🟨 Todo-Liste vor jeder Aktion** (Werkzeug `plan`): Bevor die KI in einer Runde etwas tut, schreibt sie
  auf, was sie vorhat – gelber Kasten mit 🔴 offen · 🟠 in Arbeit · 🟢 erledigt · ⚪ gestrichen. Ohne Liste
  führt das Programm keine Aktion aus. Gilt in Terminal, /gui, WebUI und Kugel-Fenster; im Coding-Assistenten
  zählt dessen eigene Todo-Liste. Eine Liste mit offenen Punkten bleibt über Runden bestehen („weiter“),
  `abhaken` an einer Aktion macht den Punkt sofort grün, und mit offener Liste laufen bis zu 120 Schritte am
  Stück – lange Serien brauchen kein „weiter“. Hilfe lesen und Menüs öffnen gehen ohne Liste.
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
- **Befehle direkt** (`befehl`): PowerShell-Befehle – auch `python`, `pip` und `pytest` – laufen nach deiner
  Freigabe im Arbeitsordner (Workspace, sonst Startordner), ohne Zeitgrenze, mit Live-Anzeige; **Esc** beendet
  den ganzen Prozessbaum. Die Sandbox bleibt der Ort zum gefahrlosen Ausprobieren.
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
  lesen und reden – ändern tut sie im großen NemiCLI. Der Verlauf bleibt erhalten, auch nach Schließen
  und Neustart; **[+ Neu]** oben im Fenster beginnt einen neuen Chat. Kugel, Fenster und Schild sind Qt (PySide6).
  **Ihr eigenes Gesicht:** liegt `Persoenlichkeiten/<Name>.png` (transparent) da, schwebt das Bild statt
  der Kugel; `<Name>_froh.png`, `_ernst.png`, `_denkt.png` … sind Stimmungen. Und sie steuert das selbst
  (Aktion `kugel`): Stimmung, Sprechblase, Geste (hüpfen/wackeln/nicken), Ecke, verstecken – mit Grenzen
  (höchstens alle 10 min von sich aus reden, nachts nur Wichtiges). **`/kugel malen`**: sie beschreibt
  sich selbst – steht in ihrer Persönlichkeit, wie sie aussieht, genau so –, der Bild-Motor malt vier
  Stimmungen mit festem Seed, rembg stellt frei – fertig ist ihr Gesicht. Prompts in ganzen Sätzen,
  ohne Negativ.
  **400 Motive** in 20 Bereichen (Gefühle, Gaming, Coding, Essen, Wetter, Comedy …): `/kugel motive` zeigt,
  was schon gemalt ist, `/kugel malen <nummer|bereich>` oder `alle` malt die fehlenden – gleiche Figur und
  gleicher Seed wie die Grundbilder, 10 Schritte je Bild. Alle Bilder liegen in `Persoenlichkeiten/<Name>/`.
  Sie wählt frei formuliert („isst Popcorn“), das passende Bild wird gesucht.
  **Freier Moment** (`/kugel impuls an`): statt fester Grüße bekommt sie alle 1–3 Stunden einen Moment für
  sich und entscheidet selbst – etwas sagen, malen, ihr Bild wechseln oder still bleiben. Nicht nachts,
  nicht bei Vollbild (Spiel, Präsentation). Solange sie im Hintergrund arbeitet, leuchtet die Kugel 🌈.
- **Anleitungen** (`Agenten/*.md`): Arbeitsanweisungen, die die Persönlichkeit selbst abarbeitet. Jede
  `.md` dort steht ab der nächsten Antwort im Prompt (Pfad + erste Zeile); passt eine, liest sie sie und
  folgt ihr. Nichts fest verdrahtet – neue Datei, neue Fähigkeit.
- **Lesende Aktionen laufen sofort, verändernde fragen** (Ja · Ja & nicht mehr fragen · Nein).
  Bei Dateiänderungen zeigt das Prüffenster die komplette Datei – **F8 gibt frei**, Enter nie. Auf einer
  schlichten grauen, eingelassenen Fläche; bei vorhandenen Dateien als **Vorher/Nachher-Vergleich** mit
  Zeilennummern: **grün `+`** neu geschrieben oder hinzugefügt, **rot `−`** entfernt oder korrigiert,
  Unverändertes gedämpft. Kopfzeile mit Ziel und orangefarbener Warnung, wenn überschrieben wird.
- **Risikostufen:** Löschen, PowerShell außerhalb der Whitelist,
  Zeitplan anlegen und ganze Ordner verschieben sind **rot** markiert, ohne „Immer“-Option; ein
  fremder Befehl fragt **zweimal**. Harmlose Änderungen einer Runde (Ordner anlegen, umbenennen …)
  werden zu **einer** Sammelfrage gebündelt – gegen das blinde Durchdrücken.
- **Befehls-Whitelist** (`/whitelist`, `befehl_whitelist.json` im Programm-Ordner): `befehl` gilt als
  harmlos, wenn es nur liest oder aus Whitelist-Anfängen besteht (git status, git diff …) – nur der
  Nutzer erweitert die Liste, die KI kommt nicht an die Datei.
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
  ⚙ Normal · ⚡ Auto (ohne Rückfrage im aktuellen Ordner, 5-Minuten-Zünder zurück auf Chatten) ·
  🚀 Auto ON (wie Auto, bleibt an bis zum Umschalten).
- **Werkzeugbilanz** nach jeder Runde: was lief, was schlug fehl, was wurde abgelehnt – aus dem
  Programmablauf, nicht aus Behauptungen des Modells.
- **Aktions-Protokoll** (`/protokoll`, `learned/aktionen.log`): jede Aktion dauerhaft mit Zeit,
  ändert/liest, Werkzeug, ausgeführt/abgelehnt/gesperrt, wer (auch Helfer) und Beschreibung. **Auch
  lesende** Aktionen – die fragen nicht nach, und genau da könnte eine Prompt-Injection etwas auslösen.
  Dazu die erste Zeile des Ergebnisses, `⚠RISIKO` bei riskanten Aktionen und ein Vermerk, wenn im
  Gelesenen Befehlsmuster steckten. Terminal, WebUI und Helfer schreiben alle mit. Dreht ab 5 MB.
- **Helfer-Agenten** (`/subagenten`): Die Persönlichkeit schickt bis zu **5** eigenständige Helfer los – jeder mit
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
  passende Erinnerungen (`learned/memory.db`, lokal, `/gedaechtnis`). Das Modell liegt im Programm-Ordner
  unter `Models/embeddings/<Name>/` und wird mit `/embeddings` gewählt
  (Qwen3-Embedding-0.6B als GGUF, z. B. `Qwen3-Embedding-0.6B-Q8_0.gguf`); es läuft im eigenen GGUF-Motor –
  ohne transformers; ein safetensors-Ordner geht weiter, wenn transformers selbst installiert ist. Auf der
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
  wird gebaut, wie du es willst. **Rücksicht auf LRS und Sprachstörungen** ist Teil der Anweisung:
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
  Die KI holt sich Bilder auch selbst: `bild_ansehen` (Datei) und `bildschirm_ansehen` (Screenshot,
  fragt vorher). Sieht ein Modell im eigenen Motor nicht, springt der **Bildbeschreiber** (`Vision/`)
  ein; bei Cloud und Ollama gibt es sonst den Hinweis, ein sehendes Modell zu wählen. Ollama Cloud
  (z.B. `deepseek-v4.1-flash`) wird als sehend erkannt; kleine Gemmas (E2B/E4B) gelten als nicht sehend.
- **Bild-Vorschau im Chat:** Gemalte oder angesehene Bilder erscheinen als Farbpixel-Block im Verlauf.
- **Eigene Krea-2-Pipeline** (`engines/krea.py`): Krea 2 direkt in NemiCLI, ohne ComfyUI. Alles von
  Hand in torch nachgebaut – Qwen3-VL-4B als Text-Encoder (12 abgegriffene Schichten), der
  Single-Stream-DiT mit 28 Blöcken, Flow-Matching-Sampler (Euler a, „simple", Shift 1.15) und der
  Qwen-Image-VAE. Fest eingestellt: **CFG 1, kein Negativ-Prompt**, 832×1216; **Schritte 8 bis 16** per
  `/bild schritte` (Auswahl mit Enter, bleibt gespeichert als `bild_krea_schritte`, Standard 14).
  Die ComfyUI-Dateien werden direkt gelesen: NVFP4-Modell (auf Blackwell über die FP4-Tensorkerne,
  sonst entpackt), FP8-Text-Encoder, bf16-VAE. Speicher wie ComfyUI mit `--disable-smart-memory`:
  jedes Teil ist nur für seinen Rechenschritt im VRAM, dazwischen im RAM; **nach 2 Minuten ohne
  Bild wird auch der RAM geräumt** (`bild_krea_entladen`, Sekunden). Ordner `Models/Krea2/`:
  Modell, `text_encoders/`, `vae/`, `tokenizer/` (vocab.json + merges.txt). Nichts wird aus dem Netz
  geladen. Krea 2 ist **der** Bild-Motor (`/bildmodel` → 🟣); eine gewählte WebUI oder ComfyUI malt
  nur, solange sie erreichbar ist, sonst springt Krea ein. In der exe kommt torch aus dem venv
  neben der exe (extlibs). RTX 5060 Ti: ~1,2 s/Schritt, ~23 s/Bild.
- **Charakter-Datei je Persönlichkeit** (`/charakter`): `Profile/<Name>/<name>.json` mit Kern-Look,
  festem Seed, Stil, Outfits, Posen und Regeln. `bild_malen` mit `figur: true` (+ `outfit`, `pose`) baut
  daraus den Prompt und nimmt den Seed – gleiche Figur in jedem Bild; `neues_gesicht` würfelt neu.
  `bild_serie` malt bis zu 30 Varianten in einem Auftrag mit gleichem Seed. Die KI pflegt die Datei
  mit `charakter_aendern` (du bestätigst), von Hand per `/charakter oeffnen`.
- **Gesichter nachmalen (Krea 2):** Nach `/bild` und `bild_malen` sucht OpenCV die Gesichter
  (Haar-Cascades, Gegenprobe mit den Augen). Jedes kleine Gesicht wird ausgeschnitten, auf 1024 px
  vergrößert, von Krea 2 ab Rauschstärke 0,45 in 10 Schritten nachgemalt (eigener VAE-Encoder, gleicher
  Prompt) und weich zurückgesetzt. Das Original bleibt, daneben liegt `<name>_gesicht.png`.
  `/bild … --ohne-gesicht` lässt es aus; Config: `bild_krea_gesicht` (an/aus),
  `bild_krea_gesicht_staerke`, `bild_krea_gesicht_schritte`, `bild_krea_gesicht_kante`.
- **ComfyUI** (`/bildmodel` → 🧩, Standard `127.0.0.1:8188`): NemiCLI schickt einen fertigen
  **Ablaufplan** (CheckpointLoader → CLIPTextEncode → KSampler → VAEDecode → SaveImage), wartet auf das
  Ergebnis und lädt das Bild ab. Zwei Bauarten: ein Checkpoint (alles in einer Datei) oder getrennt –
  Diffusions-Modell + CLIP + VAE wie bei Krea/Qwen/Flux; den CLIP-Typ rät NemiCLI aus dem Dateinamen
  (`bild_comfy_cliptype` überstimmt). Getrennte Modelle bekommen die Krea-Werte (832×1216, 14 Schritte,
  CFG 1, euler_ancestral, simple, kein Negativ-Prompt). Sampler und Scheduler werden von der Instanz
  gelesen; Vorgaben per `bild_comfy_*`. Kennt ComfyUI keinen Checkpoint, kommt der Hinweis auf
  `extra_model_paths.yaml`.
- **Externe Forge/A1111-WebUI** (`/bildmodel` → 🌐): für Qwen/Flux, die die eigene Pipeline
  nicht kann. Erprobtes Krea-Setup: 14 Schritte, CFG 1, 832×1216, Euler a, fester Positiv-Vorsatz und Negativ-Prompt,
  Sperrwörter im Code (`pov`, `1boy`, `group` …). Alles per `bild_webui_*` in der Config änderbar.
  Nur `127.0.0.1:7860`; NemiCLI startet die WebUI nicht.

### 🌐 Browser & 🖱 Rechtsklick
- **Chrome-Erweiterung** (`chrome-erweiterung/`, `/chrome`): Rechtsklick auf einer Webseite →
  **„Nemi antwortet"** – die Persönlichkeit liest die Seite, schreibt in deinem Namen eine Antwort und setzt sie
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
- **Themes** (`/theme`): cyan · matrix · amber · cyber – und **eigene, die die KI entwirft**
  (`theme_erstellen`: zwei Farben, Rest rechnet NemiCLI; zu dunkle Farben lehnt es ab; gespeichert in
  `themes_eigen.json` im Programm-Ordner, im Menü mit ✨). **Die KI öffnet Menüs** (`menue_oeffnen`):
  „anderes Farbschema?“ → das `/theme`-Menü geht auf, du wählst. Maskottchen, Emoji-Kürzel, Statistik (`/statistik`).
