# Funktionen

[Handbuch](Handbuch.md) · [Installation](Installation.md) · [Erste Schritte](Erste-Schritte.md) ·
[Modelle](Modelle.md) · **Funktionen** · [Befehle](Befehle.md)

## Inhalt

[Chat und Modelle](#chat-und-modelle) · [Eigener GGUF-Motor](#eigener-gguf-motor) ·
[Werkzeuge](#werkzeuge-und-handeln) · [Gedächtnis](#gedächtnis-und-lernen) · [Bilder](#bilder) ·
[Video, Porträt, Musik](#video-porträt-und-musik) · [Oberflächen](#oberflächen) ·
[Persönlichkeit und Kugel](#persönlichkeit-und-kugel) · [Systemwache](#systemwache) ·
[Sicherheit](#sicherheit)

## Chat und Modelle

- **Ein Schema für alles:** `anthropic:claude-…`, `openai:gpt-…`, `ollama:gemma4:12b`, `gguf:Gemma4`.
  Modell-Listen kommen live vom Anbieter.
- **37 Cloud-Anbieter**, OpenAI über die Responses API, dazu „Sign in with ChatGPT“ (`/model chatgpt`).
- **Denken sichtbar:** Denk-Modelle zeigen ihr Nachdenken kompakt, **F2** öffnet den ganzen Text, `/staerke`
  regelt die Denkstufe. Das **Denkwerk** gibt jedem Modell je nach Schwierigkeit eine Vorgehensweise mit, nach
  schweren Antworten prüft eine zweite Anfrage nach.
- **Ersatzbank** (`/model ersatz`): bis zu zwei Cloud-Modelle springen ein, wenn das aktive Modell wiederholt an
  Schutzsperren scheitert.
- **Kontext:** `/kontext` zeigt, womit er gefüllt ist; ist er voll, räumt NemiCLI einmal kräftig auf und gibt
  passende Stellen aus dem ausgelagerten Teil bei Bedarf wieder mit.

## Eigener GGUF-Motor

Lokale Modelle laufen ohne llama.cpp, Ollama oder Server – reines Python und PyTorch im NemiCLI-Prozess.

- **Viele Bauarten:** Llama, Mistral, Ministral 3, Qwen 2/2.5/3/3.5, Gemma 1–4, Phi, Granite, OLMo 2,
  DeepSeek V2/V3 und weitere; das Modell setzt sich aus der GGUF-Datei zusammen.
- **Alle gängigen Formate:** Q4_0 bis Q8_0, K-Quants, IQ1–IQ4, MXFP4, ternäre und 1-Bit-Formate; eigene
  CUDA-Kernel, beim ersten Gebrauch mit NVRTC übersetzt (kein CUDA-Toolkit nötig).
- **Gehärteter Leser:** jede Zahl aus der Datei wird geprüft, das Chat-Template der Datei nie ausgeführt.
- **Sparsam:** MoE-Experten bei Bedarf im RAM, Gesprächsspeicher in 8/6/4 Bit, Präfix-Cache (auch auf der
  Platte), wachsender Kontext bis zur Grenze des Modells.
- **Sehen:** liegt eine `mmproj`-Datei daneben, sieht das Modell Bilder (Gemma 4, Qwen3.5/Qwen3-VL,
  Ministral 3). Für Modelle ohne Sehen gibt es einen lokalen **Bildbeschreiber** (`/vision`).
- **llama.cpp als Reserve** (`/llamacpp`): ein festgelegter Build mit geprüften Prüfsummen, nur auf
  127.0.0.1, für Bauarten, die der eigene Motor nicht lädt.

## Werkzeuge und Handeln

- **Werkzeuge:** Dateien und Ordner, Suche, PowerShell (`befehl`, nach Freigabe, Esc beendet den ganzen
  Prozessbaum), PDF, Websuche über eine Allowlist, Fachquellen (arXiv, PubMed, OpenAlex, Stack Overflow),
  feste nur lesende Systemabfragen ohne PowerShell.
- **Prüffenster:** jede Änderung vorher als Vorher/Nachher; **F8** gibt frei. Riskantes ist rot und fragt
  ohne „Immer“-Option.
- **Todo-Liste vor jeder Aktion:** die KI schreibt erst auf, was sie vorhat (🔴 offen · 🟠 in Arbeit ·
  🟢 erledigt).
- **Coding-Assistent** (`/code`): Auftrag → Recherche → Todo-Liste → Punkt für Punkt. Grün setzt nur eine
  bestandene Prüfung oder der Nutzer. Schlägt in der installierten Bibliothek nach, prüft jede Datei sofort.
- **Sandbox** (`/sandbox`): Code der KI läuft in einem Windows-AppContainer – mit Internet, aber ohne Zugriff
  auf eigene Dateien, Registry oder andere Programme.
- **Helfer-Agenten** (`/subagenten`): bis zu fünf eigenständige Helfer, nur mit Cloud-Modell.
- **Erinnerungen, Wecker, Zeitplan:** „Erinnere mich in 20 Minuten …“; Aufträge in der
  Windows-Aufgabenplanung laufen ohne Fenster im Modus „Nur Lesen“ und hinterlassen einen Bericht.
- **Abläufe** (`/ablauf`): Befehlsfolgen aufnehmen und abspielen.

## Gedächtnis und Lernen

- **Chats bleiben** und gehen beim nächsten Start weiter; `F12` sichert ein Gespräch als Markdown.
- **Suche:** Stichworte und Vektoren (lokales Embedding-Modell im eigenen Motor), optional ein Reranker. Mit
  EmbeddingGemma 2 sind auch Bilder, Videos und Ton nach Inhalt findbar.
- **Wissen:** PDF, Markdown und Text in `Wissen/` legen – jede Persönlichkeit findet es.
- **Lernt von selbst:** nach Korrekturen oder Fehlern merkt sich die KI bleibende Lehren (`/reflektieren`).
  **Skills** sind Anleitungen für wiederkehrende Aufgaben, bewacht von einem Skill-Wächter.
- **Lern-LoRA** (`/lernen`, Vorgabe aus): das lokale Modell lernt nachts aus den Gesprächen, die Daten
  bleiben auf dem Rechner.
- **Schutz:** jeder Gedächtnis-Brocken kennt seine Herkunft; Web- und Werkzeug-Inhalte gelten nie als Aussage
  des Nutzers.

## Bilder

- **Krea 2** im eigenen Motor, ohne ComfyUI – liest die üblichen Dateien direkt (NVFP4, FP8, bf16, GGUF).
- **Weitere Bild-Modelle:** Z-Image, FLUX.2 klein, Qwen-Image, FLUX.1, Chroma, Stable Diffusion
  (`/bildmodel`); optional auch über eine laufende Forge- oder ComfyUI-Instanz auf 127.0.0.1.
- **Krea-Atelier** (`/atelier`): alle Einstellungen, Galerie, A/B-Vergleich mit gleichem Seed, Vorlagen.
- **Gleiche Figur in jedem Bild:** die [Charakterdatei](Charakterdatei.md) hält Aussehen und Seed fest;
  Gesichter werden auf Wunsch nachgemalt.
- **LoRA-Werkstatt** (`/lora`): eine Bild-LoRA aus eigenen Bildern trainieren, lokal.
- **Bilder ansehen:** Datei reinziehen oder Pfad nennen; die KI holt sich auch selbst Bilder oder einen
  Screenshot.
- **Feste Bildsperre:** jedes Bild und Video ist erwachsen (ab 18) – unabhängig von Motor und Prompt.

## Video, Porträt und Musik

- **Video:** LTX-2.5 mit Ton, SD-Video (Stable Diffusion mit Bewegungs-Modul) und Wan 2.2; Startbild und
  Charakter-LoRA halten die Figur (`/video`).
- **Porträt** (LivePortrait): ein Bild lächelt, zwinkert, nickt und spricht mit der eigenen Stimme.
- **Stimme** (Qwen3-TTS): jede Persönlichkeit entwirft ihre Stimme aus einer Beschreibung; `/voice` liest vor.
- **Musik** (ACE-Step 1.5): Songs mit Liedtext, 10–240 s, auch als Cover eines vorhandenen Songs (`/musik`).
- **Comic und Tagesrückblick:** eine Comicseite mit derselben Figur; ein Video aus den Bildern des Tages.

## Oberflächen

| Oberfläche | Beschreibung |
|---|---|
| Terminal-Fenster (`NemiCLI.exe`) | selbst gezeichnet, Reiter, Reiter „🤖 KI“ mit jedem Befehl der KI live, Suche, Cockpit (`F3`) |
| Desktop-Fenster (`/gui`) | ruhige Chat-Oberfläche mit Galerie, Skills, Persönlichkeiten und Vorlesen |
| Controllcenter (`/control`) | alle Einstellungen als Baum, Favoriten, Gesundheit, Tresor für API-Schlüssel |
| Nemi Office (`/office`) | Schreiben (.md .docx .odt …), Tabelle (.ods .xlsx .csv …), Malen; die KI arbeitet sichtbar im Dokument |
| Krea-Atelier (`/atelier`) | Bilder malen mit allen Einstellungen |
| WebUI (`/webui`) | lesefreundlich im Browser, nur 127.0.0.1 mit Token |
| Chrome-Erweiterung | Seitenleisten-Chat und Rechtsklick „Antworten“ / „Erklären“ ([Anleitung](../chrome-erweiterung/LIESMICH.md)) |
| Windows-Rechtsklick (`/kontextmenue`) | Bildschirm erfassen, Bild ansehen |
| Spiele (`/spiel`) | Schach, Mühle, Dame, TicTacToe gegen die Persönlichkeit |

Themes (`/theme`), Schriftgrößen je Rolle und Emoji-Stufen (`/emoji`) gelten für alle Fenster.

## Persönlichkeit und Kugel

- **Persönlichkeiten** mit eigenem Profil, Gedächtnis, Bildern und Stimme; sie sehen die Bereiche der anderen
  nicht. Ein eigener Bereich (`/eigenes`) gehört nur der KI.
- **Schwebekugel** (`/kugel`): ist kein Terminal offen, schwebt die Persönlichkeit als Kugel auf dem Desktop –
  Eingabefeld direkt an der Kugel, Antwort als Blase, Ring in der Farbe der Wache. Sie reagiert auf die Maus,
  zeigt passende Motive, spielt beim Arbeiten ihr bewegtes Porträt und kann beim Spielen zuschauen
  (`Strg+Alt+N`). Dieselbe lebendige Kugel sitzt in Office, Atelier, Controllcenter und Wache-Fenster.
- **Figur im Terminal:** ein bewegliches Strichmännchen mit dem Gesicht der Persönlichkeit (SprongMotion).

## Systemwache

`/wache` startet einen Hintergrund-Prozess mit Symbol in der Taskleiste (grün ruhig · gelb offen · rot echt):

- Sensoren für Prozesse, Netz und Dateien, Systeminventar, Regeln (MITRE ATT&CK), Sigma-Regeln und lernende
  Modelle; ein Vollscan auf Wunsch oder nach Zeitplan.
- Alarme bewertet die aktive Persönlichkeit mit einem Cloud-Modell; das Wache-Fenster (`/wache fenster`) zeigt
  jeden Alarm mit Steckbrief.
- Ohne Administratorrechte und ohne Eingriff in andere Schutzsoftware – **sie ergänzt den Virenschutz, sie
  ersetzt ihn nicht.**

## Sicherheit

- Verändernde Aktionen fragen; Papierkorb mit `/undo`, Lösch-Limit, Aktions-Protokoll (`/protokoll`).
- Systemordner und der Programm-Ordner sind für die KI gesperrt, Umwege über Pfade werden vorher aufgelöst.
- Keine Administratorrechte für die KI; PowerShell nur nach Rückfrage.
- Netz nur über HTTPS mit Allowlist und Schutzliste gegen Schad- und Phishing-Adressen; lokale Dienste nur auf
  `127.0.0.1`.
- Inhalte aus Dateien und Web gelten als Daten, nie als Anweisung.
- API-Schlüssel verschlüsselt im Tresor (AES-256-GCM, an Konto und Rechner gebunden).

Einzelheiten mit Verweisen auf den Quellcode: [Datenschutz und Sicherheit](Datenschutz-und-Sicherheit.md).
