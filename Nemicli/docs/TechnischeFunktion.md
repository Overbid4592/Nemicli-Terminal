# NemiCLI — Technische Funktionen

Diese Doku beschreibt, wie NemiCLI aufgebaut ist und was jedes Modul tut.
Kurz gehalten, Modul für Modul.

---

## Was ist NemiCLI?

Eine deutschsprachige KI-Agentin fürs Terminal. Sie chattet und führt
echte System-Aktionen aus (Dateien, Ordner, Befehle, Web, PDF, Bilder).
Läuft mit **Cloud-Modellen** und **lokal** (offline). Hat ein Vollbild-TUI,
eine optionale Browser-Oberfläche, eigenes ML (Ordner-Erkennung),
ein Langzeitgedächtnis und verschlüsselte API-Keys.

**Reines Python, wenige Abhängigkeiten. Windows-zentriert.**

---

## Architektur (das große Bild)

- `main.py` legt beim Start `core/`, `engines/`, `tools/`, `ui/` auf den Pfad
  → flache Imports (`import ui`, `import models`).
- Alle Chat-Backends haben **dieselbe Schnittstelle**:
  `stream(text, images)`, `ask_once(prompt, system)`, `reset()`, `.messages`.
- Dadurch läuft die **Aktions-Schleife identisch** für jeden Anbieter —
  egal ob Anthropic, OpenAI-kompatibel oder lokal.

---

## main.py — Einstiegspunkt

Bootstrap, Chat-Schleife, Aktions-Steuerung, alle Slash-Befehle.

| Funktion | Zweck |
|---|---|
| `main()` | Lädt Config, verschlüsselt Klartext-Keys einmalig, stellt Theme + Modell wieder her, startet TUI (oder klassischen Loop mit `NEMICLI_CLASSIC=1`). |
| `converse(backend, text, images)` | **Kern-Aktions-Schleife** (max. 8 Schritte): Antwort streamen → Aktionen parsen → bei verändernden Aktionen Bestätigung → ausführen → Ergebnis zurück ans Modell → nächster Schritt. |
| `web_converse(...)` | Gleiche Schleife, aber kopflos fürs WebUI (sendet Ereignisse statt Terminal-Ausgabe). |
| `make_backend(ref, ...)` | Baut das passende Backend (lokal / Anthropic / OpenAI-kompatibel) und überträgt den Verlauf. |
| `switch_model / pick_model` | Zweistufige Auswahl: Anbieter → konkretes Modell (inkl. Ersteinrichtung). |
| `start_webui(ctx)` | Startet die Browser-UI und verbindet sie mit dem Terminal. |
| `do_reflect(ctx)` | Zieht aus dem Gespräch eine Lektion/Vorliebe und speichert sie ins Gedächtnis. |
| `Ctx` | Sitzungs-Status (Backend, Modell, Stärke, aktueller Chat), geteilt zwischen TUI und Loop. |
| `handle_line(ctx, text)` | Verarbeitet eine Eingabezeile. `TURN_LOCK` erlaubt nur EINEN Zug gleichzeitig (Terminal oder Browser). |
| `auftrag_lauf(name)` | **Hintergrund-Auftrag** (`main.py --auftrag <name>`, gestartet von der Windows-Aufgabenplanung): kein Fenster, Ausgabe in `Berichte/<name>.log`, Modus fest auf „lesen“, jede Rückfrage ist ein Nein. Der Auftrag aus `Zeitplan/<name>.json` geht als Nachricht an die Persönlichkeit; ihre letzte Antwort wird `Berichte/<name>_<datum>.md`. Beim nächsten normalen Start zeigt `main()` die erste Zeile jedes neuen Berichts. |

**Slash-Befehle:** `/help` `/theme` `/model` `/bild` `/staerke` `/resume`
`/reset` `/clear` `/wissen` `/gedaechtnis` `/reflektieren` `/aufraeumen`
`/ml` `/ordner` `/web` `/emoji` `/nemi` `/uebung` `/webui` `/exit`

---

## core/ — Grundbausteine

| Modul | Zweck |
|---|---|
| **chatstore.py** | Nummerierte Chats: `chats/NNN.json` (voller Verlauf für `/resume`) + `chats/NNN.md` (lesbare Zusammenfassung). |
| **commands.py** | Slash-Befehle + Autovervollständigung (`SlashCompleter` schlägt Befehle, Themes, Modelle, Chats vor). |
| **config.py** | Einstellungen in `nemicli.config.json`. Schreibt Keys in `.env` + live in die Umgebung, verschlüsselt sensible Keys (Name endet auf `API_KEY`). |
| **confirm.py** | Schönes Auswahl-Menü (Pfeil/Zahl, Esc = sichere Option). `ask()`, `ask_text()`. |
| **keyvault.py** | API-Key-Schutz über **Windows-DPAPI** (an Konto + PC gebunden). `encrypt/decrypt`, `mask` (`sk-12345•••`). Nicht-Windows: Klartext. |
| **persona.py** | **Der System-Prompt** für alle Modelle: Persönlichkeit (aus `persoenlichkeiten.py`, austauschbar) + feste Grundregeln + Windows-Umgebung + komplettes **Aktions-Protokoll**, Werkzeugliste, Web-Sicherheitshinweis. Baut den Prompt bei jeder Nachricht neu aus Basis + Ordner-Sinn + Wissen + passenden Erinnerungen. |
| **modes.py** | Arbeitsmodus (Sitzung, Start = normal): `decide(action, needs_confirm)` → run/ask/block je Modus; Auto mit Ordner-Grenze (`_inside_cwd`) und Zünder (`AUTO_FUSE_S`, `check_fuse()` im TUI-Ticker); `prompt_hint()` für den System-Prompt, `status_fragment()` für die Leiste. Netz-Taint (needs_confirm=True) schlägt jeden Modus. |
| **version.py** | `VERSION` (Nummer des Entwicklers, z.B. 3.5 Alpha) + `STAND` (Datum des Codes) + Git-Info (Hash, Branch, Datum, dirty) für `/version` und `--version`; in der exe nur die Nummer. |
| **persoenlichkeiten.py** | `/persönlichkeiten`: eingebaute „Nemi“ + eigene als Markdown-Dateien in `Persoenlichkeiten/` (erste `# Überschrift` = Name, erste `> Zeile` = Kurzbeschreibung, Rest = Prompt-Text). Aktive steht in `nemicli.config.json` (`persoenlichkeit`), fehlt sie → Nemi. `create()` baut aus Name/Kurz/Geschlecht/Tonfall eine editierbare Vorlage, überschreibt nie. |

---

## engines/ — die Chat-Motoren

| Modul | Zweck |
|---|---|
| **chat.py** | **Anthropic-Kern** (natives SDK). Adaptive Denk-Stärke (effort + Thinking). Streamt Denken und Text getrennt, liefert Live-Token-Zahlen. |
| **cloud.py** | **Ein** OpenAI-kompatibler Motor für alle anderen (OpenAI, Gemini, Mistral, Cohere, Perplexity, DeepSeek, xAI, Groq, OpenRouter, Ollama). Vision-Fallback, Verlaufs-Kappung. |
| **models.py** | Lokale **GGUF**-Modelle (im `Models/`, auto-erkannt). Ref-Schema `anbieter:modell`, Vision-Erkennung, Denk-Stärken, defensiver GGUF-Parser + CVE-Build-Warnung. |
| **reasoning.py** | Reine Auswahl passender Denkparameter: Kimi über Ollama erhält ein gemeinsames Denk-/Antwortbudget; GPT-OSS und bekannte OpenAI-Modelle zusätzlich Effort-Stufen. Unbekannte Modelle erhalten keine zusätzlichen Parameter. |
| **pricing.py** | Nachschlage-Tabellen: Kontextgröße + grobe $-Kosten je Zug. Lokal/Ollama = kostenlos. |
| **providers.py** | Cloud-Anbieter-Registry. Modelle werden **live** über `/models` abgefragt, nicht hartkodiert. Anbieter ohne Key erscheinen nicht. Ollama-Sonderweg + Embeddings. |
| **setup.py** | Ersteinrichtung: GPU-Erkennung, Ollama erkennen, Ollama-Katalog + Pull. (Der llama.cpp-Download ist am 15.09.2026 entfallen.) |
| **imagegen.py** | **Eigene Stable-Diffusion-Pipeline** (SD1.5/SDXL): Laden, CLIP-Embeddings mit Block-Zerlegung, eigener DPM++ 2M / Euler + Karras-Loop, VAE-Decode, OpenCV-Gesichter (img2img). `backend()`/`paint()` routen zwischen dieser Pipeline und der externen WebUI. |
| **sdwebui.py** | Externe **Forge/A1111-WebUI** als Bild-Motor über `/sdapi/v1` (available/models/current_model/set_model/generate). Eigene Vorgaben (kein Negativ-Prompt, CFG 1, 1024², 14 Schritte), Fortschritts-Poller, speichert nach `Bilder/` mit echtem Seed. Nur `127.0.0.1`. Für Modelle, die die eigene Pipeline nicht kann (Krea/Qwen/Flux). |
| **winjob.py** | Kindprozesse "kugelsicher": Windows Job-Objekt → jeder gestartete Hilfsprozess stirbt automatisch mit NemiCLI. |
| **uvsetup.py** (core/) | Isolierte Python-Umgebung via `uv` (Hermes-Vorbild): lädt `uv` von GitHub, holt ein eigenständiges Python 3.12 nach `.runtime/`, baut das venv neben NemiCLI und installiert Pakete – **kein Admin, kein PATH, nichts am System**. Alle uv-Pfade per Env in den App-Ordner gezwungen; Host-Prüfung, Zip-Slip-Schutz. `wizard.py` ruft es für `/einrichten`. |

**Cloud-Provider:** Anthropic (nativ, mit Denk-Stärke), OpenAI, Google/Gemini,
Mistral, Cohere, Perplexity, DeepSeek, xAI/Grok, Groq, OpenRouter.
**Lokal:** Ollama — offline, bringt seine eigene Rechen-Maschine mit. (llama.cpp mit eigenen GGUF-Dateien ist am 15.09.2026 entfallen.)

---

## tools/ — Werkzeuge & Extras

| Modul | Zweck |
|---|---|
| **actions.py** | **Das Aktions-System.** `ACTIONS`-Registry, parst ` ```aktion `-Blöcke, korrigiert Tippfehler, führt aus. **Sicherheit:** sperrt verändernde Aktionen in System-/Windows-Ordnern (Pfad-Schutz gegen `..`, destruktive Befehle). Systemordner: nachschauen ja, ändern nie (seit 18.09.2026): `_read_guard()` sperrt für Lesewerkzeuge nur noch die absolut gesperrten Orte des Nutzers, `_guard()` fürs Ändern bleibt streng, `_guard_command()` sperrt jeden `befehl`, der einen Systemordner/HKLM nennt – mit `lesend=True` (für `abfragen`) darf er sie nennen; `_skip_system(p, base)` hält Suchläufe aus Windows heraus, außer die Suche beginnt dort. **`abfragen`** = PowerShell ohne Rückfrage, aber nur lesend: `_nur_lesend()` lässt Cmdlets nur mit Lese-Verb durch (Get/Test/Measure/Select/Sort/Where/Format/Compare/Resolve/Convert …), kennt eine Allowlist nativer Lese-Programme (netstat, ipconfig, tasklist, nslookup …) und sperrt Umleitungen, `&`-Aufrufe, Dot-Sourcing, .NET-Schreib-/Start-Methoden, Add-Type/New-Object und eigenen Netz-Zugriff. Fail-safe: unbekannter Befehlsanfang = nein. |
| **zeitplan.py** | **Windows-Aufgabenplanung** für die Aktion `zeitplan`: `parse_wann()` versteht deutsche Zeitangaben (täglich HH:MM · alle N Stunden/Minuten · wöchentlich Tag HH:MM · Anmeldung · einmal Datum), `anlegen/loeschen/liste` reden per `Register-/Unregister-/Get-ScheduledTask` mit Windows – nur im Ordner `\NemiCLI\`, fremde Aufgaben nie. Aufträge liegen als `Zeitplan/<name>.json`, die Aufgabe startet `pythonw.exe main.py --auftrag <name>` (kein Fenster). Berichte + Log in `Berichte/`; `neue_berichte()` merkt sich per Marker, was der Nutzer schon gesehen hat. |
| **foldersense.py** | Eigener **Naive-Bayes-Klassifikator** (reines Python): erkennt Ordner-Typ (Python, Node, Web, Rust, Dokumente, Bilder …). Lernt echte Ordner dazu. Modell in `learned/folder_model.json`. |
| **learn.py** | Wissensspeicher: sichert Python-Code (`learned/snippets/`, dedupliziert) und selbstgeschriebene Anleitungen (`learned/skills/`). Kompakter Index in den Prompt. |
| **memory.py** | **Langzeitgedächtnis** in natürlicher Sprache. `remember/recall` — semantisch über Qwen3-Embedding-0.6B auf der CPU (`embedder.py`), Fallback Stichwort. Arten: Fakt/Vorliebe/Projekt/Person/PC/Lektion. `consolidate` (für `/aufraeumen`). |
| **stats.py** | `/statistik`: akkumuliert Nutzungs-Zahlen über alle Sitzungen in `learned/stats.json` – `start_session`/`end_session`, `record_command`, `record_turn` (Modell/Persona/Modus/Tokens/Kosten/Stunde/Wochentag), `record_tools` (aus der Werkzeug-Bilanz, ✓/✗, zählt Bilder). Speichert gedrosselt, jede Aufzeichnung fehlertolerant. Keine Chat-Inhalte. |
| **updater.py** | `/update`: `why_not()` (exe / kein Repo / kein Remote), `run()` = Stand merken → `git pull --ff-only` → geänderte Dateien, `install_requirements()` per venv-pip. Verweigert bei lokalen Änderungen. |
| **pdfgen.py** | **PDF-Generator in reinem Python** (keine Fremd-Lib): Überschriften, Fett/Kursiv/Code, Listen, Tabellen, Bilder (PNG selbst dekodiert), Umlaute, Seitenzahlen. |
| **practice.py** | **Übungsmodus:** Jede erzeugte Codefassung benötigt vor Schreiben/Ausführen eine eigene Freigabe über `approve(path, code)`. Asynchroner Prozess mit Abbruch und abgewartetem Prozessende; Erfolg nur bei Exitcode 0 und exakter letzter Ausgabezeile `ALLE TESTS OK`. `NemiSandbox/` ist ein Arbeitsverzeichnis, keine Betriebssystem-Isolation. |
| **subagents.py** | Bis zu **zwei parallele Helfer-Agenten** für unabhängige Teilaufgaben (eigener frischer Kontext). |
| **textproc.py** | `think_splitter()` — trennt `<think>`-Tags lokaler Modelle sauber in Denken/Text (robust gegen zerrissene Tags). |
| **vision.py** | Bild-Aufbereitung für Vision: Bildpfade im Text finden, base64/data-URI, OpenAI-Bildformat. |
| **embedder.py** | Qwen3-Embedding **ohne** sentence-transformers: Text durchs Modell, Vektor des letzten Tokens, normieren — auf torch + transformers. Nötig, weil sentence-transformers scikit-learn zwingend mitzieht und dessen unsignierte DLL von Smart App Control blockiert wird. Setzt einen sklearn-Platzhalter, aber nur bei echter Blockade. Vektoren bitgleich zu den alten (geprüft). |
| **webfetch.py** | **Sicherer Web-Zugang:** nur Allowlist-Domains (`allowlist.json`), HTML→Text, **SSRF-Schutz** (blockt interne IPs), Fremddaten als "untrusted". Wikipedia-API + **Ollama-Suche**. |
| **webui.py** | **Browser-Oberfläche** (nur Standard-Lib): SSE an Tabs, mit der CLI verbunden. **Sicherheit:** nur 127.0.0.1 + Token in der URL. Barrierefrei (OpenDyslexic-Schrift, Kontrast, Vorlesen/TTS). |

---

## ui/ — Darstellung

| Modul | Zweck |
|---|---|
| **emoji.py** | Wandelt Smileys/Kürzel in echte Emojis (`=)` → 🙂, `:feuer:` → 🔥). |
| **mascot.py** | Kleines Maskottchen: Gesichter, Begrüßungen, Tipps, Sprüche (`/nemi`). |
| **screen.py** | **Vollbild-TUI** (prompt_toolkit): Eingabe unten, Verlauf scrollt oben, ML-Balken + Maskottchen + Statusleiste. Leitet die rich-Ausgabe in einen Scroll-Puffer um. |
| **Thinking im TUI** | F2 vergrößert die laufende Denk-Vorschau. Nach der Antwort öffnet F2 den letzten vollständigen Denktext in einer schreibgeschützten Ansicht zum Scrollen; F2/Esc schließt sie. Der Text bleibt ausschließlich im RAM, der ungesendete Entwurf erhalten. |
| **Aktionsfreigabe** | Vollständiger schreibgeschützter Vorschaupuffer für Dateiänderungen und Übungscode, unabhängig vom gekürzten Verlauf. F8 bestätigt, Esc lehnt ab. Auch die klassische Konsole und das WebUI zeigen vollständige Vorschauen. |
| **Aktionsstatus** | `actions.run` liefert `ActionResult(text, ok, returncode)`. `ok=None` kennzeichnet Quellen ohne prüfbaren Erfolgsstatus. Terminal und WebUI verwenden diesen Status direkt und zeigen am Ende jeder Anfrage eine Werkzeugbilanz, einschließlich Ablehnungen und reinen Textantworten. |
| **ui.py** | Zentrale Darstellung (rich): Themes/Farben (`/theme`), Logo, alle Panels (Antwort/Denken/Aktion/Ergebnis), Live-Stream-Anzeige, Statusleiste. |

---

## Abhängigkeiten & Konfig

**requirements.txt** (bewusst schlank):
`anthropic`, `openai`, `httpx`, `rich`, `prompt_toolkit`, `python-dotenv`.
Optional nur für `/bild` (lazy): torch, diffusers, transformers, Pillow …

**Konfig-Dateien:**
- `.env` — API-Keys je Anbieter + `OLLAMA_API_KEY` (Cloud + Web-Suche) + optional `OLLAMA_HOST`.
- `nemicli.config.json` — gemerkte Einstellungen (Modell, Theme, Stärke).
- `allowlist.json` — Web-Whitelist nach Vertrauens-Tier; alles andere `deny_by_default`.

---

## Kern-Features auf einen Blick

- **Multi-Provider:** 10 Cloud-Anbieter (live abgefragt) + Ollama, offline möglich.
- **Vision:** Ollama (👁-Modelle) und Cloud — Bildpfade werden automatisch angehängt. Ein blindes Modell bekommt den Hinweis, ein sehendes zu wählen.
- **Aktionen:** Dateien, Ordner, PowerShell, Web, PDF, Bild — verändernde mit Bestätigung, Systemordner-Schutz.
- **Systemwache & Zeitplan:** `abfragen` liest Prozesse, Verbindungen, Dienste, Virenschutz … ohne Rückfrage (nur Lese-Befehle kommen durch); `zeitplan` trägt NemiCLI in die Windows-Aufgabenplanung ein, der Hintergrund-Lauf schreibt Berichte. Was geprüft wird, steht in einer Anleitung in `Agenten/`, nicht im Code.
- **Eigenes ML:** Naive-Bayes-Ordnererkennung, Wissensspeicher, Langzeitgedächtnis mit Embedding-Recall, Reflexion, Übungsmodus.
- **UI:** Vollbild-TUI, klassischer Fallback, barrierefreie WebUI.
- **Sicherheit:** verschlüsselte Keys (DPAPI), Systemordner-Schutz, Web-Allowlist + SSRF-Schutz + Injection-Warnung, GGUF-Parser + CVE-Warnung, kugelsichere Kindprozesse, WebUI nur lokal + Token.
