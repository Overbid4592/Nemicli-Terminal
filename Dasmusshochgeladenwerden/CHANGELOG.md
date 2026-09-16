# 📅 Änderungen – NemiCLI

Das Tagebuch der Entwicklung. Die aktuelle Übersicht steht in der [README](README.md).

### 16. September 2026
- **Aktionsblock ohne schließenden Zaun wird erkannt.** Manche Modelle beenden die Antwort direkt nach dem `}` und lassen das schließende ``` weg – der Block galt dann als Text, nichts wurde ausgeführt und niemand merkte es. Jetzt darf der Zaun am Ende der Antwort fehlen. Ist ein Block da, aber nicht lesbar (JSON abgeschnitten), warnen Terminal und WebUI sichtbar „NICHTS ausgeführt" (`actions.unparsed_action_note()`). 4 Tests.

### 15. September 2026
- **Nachzügler vom llama.cpp-Ausbau entfernt.** `engines/pricing.py` rief noch `models.is_local` – der Fehler kam erst beim ersten Antworten. Dazu die verwaiste Konstante `LOCAL_CTX` und ein alter Vision-Test entfernt.
- **README und `docs/TechnischeFunktion.md` aktualisiert:** Trennung Programm/Daten, `/start`, die Sperren, Ollama als Weg zu lokalen Modellen, ComfyUI/Forge statt eigener Bild-Pakete.
- **Semantisches Gedächtnis ohne sentence-transformers.** Windows Smart App Control blockiert eine unsignierte DLL aus scikit-learn, was `import sentence_transformers` sterben ließ. Neu: `tools/embedder.py` – Qwen3-Embedding direkt über torch + transformers (Last-Token-Pooling, normiert). Die erzeugten Vektoren sind identisch zu den bisherigen, nichts muss neu eingebettet werden. `sentence-transformers` ist aus `requirements.txt` raus.
- **sklearn-Platzhalter.** `transformers` importiert sklearn beim Laden mit. `embedder.py` legt nur bei tatsächlich blockiertem sklearn einen Platzhalter in `sys.modules`, dessen Funktionen beim Aufruf `NotImplementedError` werfen. `tests/test_embedder.py` (langsamer Vektorvergleich hinter `NEMICLI_SLOW_TESTS=1`).
- **llama.cpp komplett entfernt.** Lokale Modelle laufen ausschließlich über **Ollama**. Gelöscht: `engines/local.py`, `tools/llamaup.py`, `Agenten/llamaup.md`, der Befehl `/llamaup` samt Werkzeugen, der GGUF-Zweig in `/model`, der llama.cpp-Download in `engines/setup.py`. Der lokale Vision-Helfer entfällt damit – Bilder braucht ein Modell, das selbst sieht (Cloud oder Ollama-Modell mit 👁). Der Einrichtungs-Assistent prüft jetzt Ollama.
- **ComfyUI braucht kein `diffusers` mehr.** Die Prüfung „fehlen Bild-Pakete?" fragte nicht, welcher Motor gerade malen würde. Neu `imagegen.missing_reason_aktiv()` und `gewaehltes_backend()`; die Meldung nennt jetzt den echten Grund (z. B. „ComfyUI ist gewählt, aber nicht erreichbar").
- **`venv` liegt beim Programm.** `core/uvsetup.py` suchte das venv im Daten-Ordner; jetzt `INSTALL / "venv"`.
- **Programm und Daten getrennt – `/start`.** `core/paths.py` kennt zwei Wurzeln: `INSTALL` (Quelltext, Config, `.env`) und `DATEN` (Chats, Bilder, Modelle, Gelerntes, Persönlichkeiten). Wo `DATEN` liegt, wählt der Nutzer beim ersten Start in einem Fenster (`core/reich.py`) und jederzeit über **`/start`**. Es wird kein Ordner vorgeschlagen. Beim Umzug wird kopiert, nicht verschoben, und danach nachgemessen. Fehlt der Ordner beim Start, sagt NemiCLI das.
- **`start.bat` prüft die Installation** in drei Stufen: Python da? · venv da? · Pakete drin? – mit Klartext-Hinweis, was von Hand hilft.
- **Sperre für den Programm-Ordner.** Der NemiCLI-Programmordner ist für die KI komplett tabu (weder lesen noch ändern); erweiterbar über `schreibsperre.json`. Die Daten-Bereiche sind ausgenommen. Befehle, die mehrere Pfade nennen, werden Fundstelle für Fundstelle geprüft.
- **ComfyUI: VAE und CLIP fest wählbar.** Ohne Config-Eintrag nahm `engines/comfyui.py` den alphabetisch ersten – ein Audio-VAE konnte so vor den Bild-VAE rutschen. Jetzt `bild_comfy_vae` und `bild_comfy_clip` in `nemicli.config.json`.
- **Aktions-Protokoll.** `tools/protokoll.py` schreibt jede Aktion (auch lesende) nach `learned/aktionen.log`: Zeit · ÄNDERT/liest · Werkzeug · Ergebnis · wer · Beschreibung. Nur Namen, keine Inhalte; dreht ab 5 MB. `/protokoll [n]` zeigt die letzten. Tests leiten das Protokoll in einen Temp-Ordner um.
- **Kürzungs-Hinweis präziser:** „Zeichen 12.000–145.977 fehlen (von 145.977 gesamt)" statt nur „gekürzt".
- **Ordner per Drag & Drop:** kommt als Listing an (eine Ebene, Ordner zuerst, Dateien mit Größe, ab 200 Einträgen gekappt).
- **Dateien per Drag & Drop in den Chat.** `tools/anhang.py` erkennt Dateipfade in der Nachricht und hängt Text (nach Inhalt erkannt, nicht nach Endung), PDF (per `pypdf`) und Bilder (16 Formate, Umwandlung nach PNG) an. Binäres wird übersprungen. Dateiinhalt gilt als Daten, nie als Anweisung; Code-Zäune werden entschärft; 12.000 Zeichen pro Datei, ab 2 MB keine Verarbeitung. 32 Tests.

### 14. September 2026
- **Web-Suche: Schnipsel pro Treffer gedeckelt.** Ein langer erster Treffer fraß das ganze Budget, Treffer 2–5 fielen weg. Jetzt bekommt jeder Treffer ein festes Kontingent.
- **ComfyUI: getrennte Modelle (Krea/Qwen/Flux), nicht nur Checkpoints.** `models()` liefert `{Name: 'checkpoint' | 'diffusion'}`; für Diffusion-Modelle baut NemiCLI den Workflow aus `Load Diffusion Model` + `Load CLIP` + `Load VAE`.
- **ComfyUI als eigener Bild-Motor** (`engines/comfyui.py`, Standard `127.0.0.1:8188`), gleichberechtigt neben Forge/A1111. Die Erreichbarkeits-Prüfung fragte bisher eine A1111-Adresse, die ComfyUI mit 404 beantwortet.
- **Code im Chat bekommt einen Rahmen:** Kopfzeile mit Dateiname oder Sprache, ab vier Zeilen Zeilennummern, Syntax-Farben passend zum Theme, durchsichtiger Hintergrund.
- **Arbeitsmodus im Workspace.** Mit `/workspace` arbeitet die Persönlichkeit als Coding-Agentin: sachlich, strukturiert, knapp. Agentloop bis zur Erledigung, Vorrang für Nutzer-Eingaben, Rücksicht beim Schreiben. Nach `/workspaceend` ist der Modus automatisch wieder aus.
- **`.workspace/Auftrag.md`** – die Arbeitsanweisung liegt als Datei im Projekt, nicht nur im Chat. Kein eigener Notizordner daneben.
- **Fehler: Startordner ging verloren.** `pushd` in `nemicli.cmd` machte den Programmordner immer zum aktuellen Ordner. Jetzt merkt sich der Starter den Aufrufordner und übergibt ihn.
- **`/workspace` – ein Projektordner, und nur dieser.** Lesen, Schreiben, Suchen und Befehle laufen nur noch dort. Der Riegel steht vor allen Arbeitsmodi. Schaltbar nur vom Nutzer; ein Test wacht darüber, dass die KI dafür kein Werkzeug bekommt.
- **Projekt-Gedächtnis im Projekt.** `.workspace/` mit `memory.md`, `Absprache.md`, `Dateien.md`. Solange ein Workspace läuft, schreiben `merken` und `skill_merken` dorthin.
- **`Agenten/workspace.md`** – Arbeitsanleitung fürs Projekt: Ordner ansehen → erst reden → nachschlagen statt raten → Plan vorlegen → in kleinen Schritten bauen.
- **`F12` sichert das laufende Gespräch als Markdown** nach `Gespraeche/` – mit Denktext, Aktionen, Ergebnis und `/resume`-Hinweis. Mitgeschrieben wird während des Gesprächs.
- **Agenten-Anleitungen sind persönlichkeits-neutral:** Platzhalter `{{char}}` und `{{user}}`, gefüllt über `persoenlichkeiten.render(text, name)`.
- *(am 15.09. wieder entfernt)* `/llamaup` – geführtes, abgesichertes Update von llama.cpp mit Prüfziffern, CVE-Abgleich und Backup.

### 13. September 2026
- **README neu aufgesetzt** (von 932 auf ~230 Zeilen). Das Tagebuch lebt seither in `CHANGELOG.md`.
- **Chrome-Erweiterung „Nemi antwortet"** (`chrome-erweiterung/`). Rechtsklick auf einer Webseite → NemiCLI liest den Seitentext, schreibt eine Antwort und setzt sie ins Textfeld; „erklär mir das" erklärt markierten Text. NemiCLI hört dafür fest auf `127.0.0.1:9000` (ein Test wacht darüber), nur `/antwort`, `/erklaer`, `/ping`, jeder Aufruf mit Geheimschlüssel (`X-Nemi-Key`; `/chrome` zeigt ihn, `/chrome neu` würfelt neu).
- **„🐈 Nemi antwortet" im Rechtsklick-Menü.** Screenshot des zuletzt benutzten Fensters, Antwort wird per Zwischenablage eingefügt; du liest gegen und schickst ab.
- **Rechtsklick-Menü von Windows** (`/kontextmenue an · aus`, nur HKCU, kein Admin): „Bildschirm erfassen" und „Mit NemiCLI ansehen". Läuft NemiCLI schon, geht es ins offene Fenster (`.nemicli.alive` / `.nemicli.inbox`); `nemicli --sag "<text>"`.
- **`bild_ansehen` und `bildschirm_ansehen`.** Bilder von der Platte oder ein Screenshot kommen in die nächste Runde; große Bilder werden vor dem Senden auf 1600 px verkleinert.
- **Text im Verlauf markieren und kopieren** (Maus + Strg+C), ohne Panel-Ränder; über OSC 52 und `Set-Clipboard`.
- **WebUI-Malen mit festen Vorgaben:** Format, Sampler, Scheduler, Positiv-/Negativ-Vorsatz und Sperrwörter, alles über `bild_webui_*` in `nemicli.config.json` überschreibbar. `engines/sdwebui.py`.
- **Klickbare Knöpfe in Dialogen + Bild-Vorschau im Chat** (Farbpixel-Block über ▀, kein Zusatzpaket).
- **Vollbild-Oberfläche neu auf Textual** (`ui/screen_tx.py`, Standard). Jeder Block ein echtes Widget, kein Flackern, echtes Scrollen, Menüs und Denktext als Dialoge. Alle Tasten wie bisher.

### 12. September 2026
- **Helfer-Agenten wie echte Sub-Agenten.** Bis zu 5 eigenständige Agenten mit Rolle, Auftrag, eigenem Verlauf und allen Werkzeugen; nur auf Abruf, nur mit Cloud-Modell.
- **Antwort-Deckel 64k** für Modelle ohne Denkstufen-Profil; Helfer 4.096.
- **Name der aktiven Persönlichkeit** in Antwort-Box und Kopfzeile (`ui.persona_name`).
- **Helfer-Budget** von 1.024 auf 4.096 Tokens; bei Überlauf kommt der vorhandene Text zurück statt eines Fehlers.
- **Header responsiv & exakt zentriert.**
- **Keine Befehls-Skripte mehr als Müll:** `befehl` führt mehrzeilige PowerShell direkt aus; dauerhafte Skripte landen in `Befehle/`.
- **Entwickler-Credit** in `/version` und README (`core/version.py`).
- **`/einrichten` komplett isoliert (via `uv`):** eigenständiges Python 3.12 in `.runtime/`, kein Admin, keine PATH-Änderung.
- **Externe Bild-WebUI als zweiter Motor** (`/bildmodel`, Forge/A1111 auf `127.0.0.1:7860`).
- **`/statistik`:** lokale Nutzungszahlen in `learned/stats.json` – keine Inhalte, nur Zahlen und Namen.
- **Nur-Lesen-Modus wirklich dicht:** feste Allowlist echter Lese-Werkzeuge in `core/modes.py`; alles andere blockt.
- **Systemordner komplett tabu:** `C:\Windows`, `Program Files`, `ProgramData` und die System-Registry sind für die KI unsichtbar, auch über Umwege.
- **Arbeitsmodi** (`Shift+Tab` / `/modus`): Chatten · Nur Lesen · Normal · Auto (mit 5-Minuten-Zünder zurück auf Chatten).
- **Aktions-Bremse entschärft:** 12 Aktionen pro Nachricht, danach Zusammenfassung statt stummem Abbruch.
- **Web-Suche: 227 statt 62 erlaubte Domains** (Allowlist v1.1) + feste Suchregeln.
- **Gedächtnis gegen Prompt-Injection abgesichert:** Erinnerungen im Prompt als Daten gerahmt und entschärft; nach Web-Inhalt in derselben Runde fragt `merken` nach.
- **`/version`, `/selbsttest`, `/update`** (`git pull --ff-only`, zeigt geänderte Dateien, bietet pip-Installation an).
- **Geister-Zeichen im Verlauf behoben** (Emoji mit Variation Selector, ZWJ-Ketten).
- **`/persönlichkeiten`:** Profile anlegen, bearbeiten, kopieren, löschen; Platzhalter `{{user}}`, `{{char}}`, `{{current_time}}`, `{{current_date}}`.
- **Git-Start.** Erster Commit; `.gitignore` deckt Modelle, Bilder, Chats, Gedächtnis, Logs und Builds ab.

### 7. September 2026
- **Fester Eingabebereich neu gestaltet:** farbiger Rahmen, Ordner-Erkennung und Maskottchen im Kopf, separate Status- und Tastenzeile; passt sich der Terminalbreite an.
- **Thinking:** Live-Vorschau begrenzt, laufende Denkzeit; F2 öffnet den vollständigen Denktext.
- **Esc:** Abbruch wird auch bei lückenlosem Tokenstrom geprüft; höchstens zwölf Neuaufbauten pro Sekunde.
- **Kimi-Denkbudget:** `/staerke schnell` = 4.096, `normal` = 8.192, `stark` = 16.384, `max` = 32.768 Tokens.
- **Weitere Denkparameter** für GPT-OSS (Ollama) und ausgewählte OpenAI-Modelle; erreichte Ausgabelimits werden sichtbar gemeldet.

### 5. September 2026
- **Langzeitgedächtnis fror den Chat ein – behoben.** Der Encoder wird nicht mehr im System-Prompt nachgeladen, bleibt nach der Runde warm (schläft nach 10 min Ruhe ein) und lädt direkt von der Platte (`local_files_only`).
- **Gedächtnis-Suche ~800x schneller:** Vektoren liegen einmal als numpy-Matrix im RAM (0,25 s → 0,0003 s bei 605 Brocken).
- **Web-Suche: Ollama statt Brave.** `web_suche` nutzt `https://ollama.com/api/web_search` mit dem `OLLAMA_API_KEY`; `/web key` trägt ihn ein.

---

<div align="center">

*Mit 💜 gebaut. NemiCLI ist ein persönliches Lern- & Bastelprojekt.*

*Entwickelt von **D. Hoffmann** (Vibecoder) · gebaut mit **Claude Opus 5** (Anthropic)*

</div>
