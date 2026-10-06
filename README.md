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

**Entwickelt von D. Hoffmann (Vibecoder) · gebaut mit Claude Opus 5 (Anthropic)**

</div>

---

## Was ist das?

NemiCLI ist ein selbstgebauter **KI-Agent fürs Terminal** (Python, Windows). Er redet mit
**Cloud-Modellen** (11 Anbieter), mit **lokalen Modellen im eigenen GGUF-Motor** oder über Ollama –
und er kann **handeln**: Dateien lesen und schreiben, Befehle ausführen, im Web nachschlagen,
programmieren, Bilder ansehen und malen, den PC bewachen. Verändernde Schritte immer mit deiner Freigabe.

Dazu eine Oberfläche mit Persönlichkeit (Nemi oder deine eigene), Themes, ein ruhiges Desktop-Fenster
(`/gui`) und Lesehilfen für LRS.

**Reines Python – kein Node.js, kein npm, kein llama.cpp, kein transformers.** Lokale Sprachmodelle,
Bildbeschreiber und Gedächtnis laufen im eigenen GGUF-Motor, Bilder malt die eigene Krea-2-Pipeline.

> Entstanden aus Neugier: „Wie funktioniert eigentlich so ein Coding-Agent?" 💜

---

## ✨ Auf einen Blick

Alles ausführlich: **[docs/Funktionen.md](docs/Funktionen.md)**

| Bereich | Was es kann |
|---|---|
| 🧠 **Eigener GGUF-Motor** | Lokale Modelle ohne Server, reines Python + torch im NemiCLI-Prozess. Gemma 4, Qwen3.5, Llama, Mistral/Ministral, Phi, Granite, DeepSeek, Ling 3.0 u. a.; alle gängigen Formate bis IQ1; MoE-Experten bei Bedarf im RAM; Präfix-Cache (Folgerunden in ~0,1 s); `/kontext` bis 128k mit VRAM-Automatik |
| 👁 **Sehen** | Cloud, Ollama und eigener Motor (mmproj: Gemma 4, Qwen3.5/Qwen3-VL, Pixtral). Modelle ohne Sehen bekommen Hilfe vom **Bildbeschreiber** (`Vision/`) |
| ☁ **Cloud** | Anthropic, OpenAI, Google, Mistral, Cohere, Perplexity, DeepSeek, xAI, Groq, OpenRouter, Ollama Cloud – Modelle live abgefragt, Keys verschlüsselt (DPAPI) |
| ⚙ **Werkzeuge** | Dateien, Ordner, Suche, PowerShell (auch Python/pip), Web nur über Allowlist, PDF, Systemabfragen ohne PowerShell, Zeitplan, Helfer-Agenten, gelbe Todo-Liste vor jeder Aktion |
| 🟦 **Coding-Assistent** | `/code`: Auftrag → Recherche → Todo-Liste → Punkt für Punkt, grün nur nach echter Prüfung; kennt installierte Versionen, ruff, Offline-Doku (Python, MDN), Seiten-Screenshots |
| 🧪 **Sandbox** | Code zum Ausprobieren läuft im Windows-AppContainer – Internet ja, deine Dateien nein |
| 🛡 **Systemwache** | Hintergrund-Wächter mit 14 Regeln, eigenem Isolation Forest, Vollscan; bei Alarm prüft und urteilt die Persönlichkeit selbst |
| 💾 **Gedächtnis** | Chats, Fakten, Lehren, Skills, Wissen-Ordner; hybride Suche (Stichworte + Vektoren), lernt nach Korrekturen von selbst, Kontext ohne Ende (AutoContextCleaner) |
| 🎨 **Bilder** | Eigene Krea-2-Pipeline (NVFP4 auf RTX 50xx), Gesichter nachmalen, Charakter-Datei für eine feste Figur, Serien bis 30 Bilder; optional Forge-WebUI oder ComfyUI |
| 🎭 **Oberfläche** | Textual-Vollbild, Desktop-Fenster `/gui`, WebUI mit Lesehilfen, Schwebekugel auf dem Desktop, Spiele, Chrome-Erweiterung, Rechtsklick-Menü |
| 🔒 **Sicherheit** | Prüffenster mit Vorher/Nachher (F8), Risikostufen, Papierkorb + `/undo`, Lösch-Limit, Systemordner nur lesen, Fremddaten markiert, Aktions-Protokoll |

---

## 🚀 Starten

**Weg 1 – bequem:** `start.bat` doppelklicken. Der Starter prüft Python, `venv` und Pakete und baut
nach, was fehlt. Danach `/einrichten`: holt ein eigenes Python (isoliert in `.runtime/`, ohne Admin)
und installiert torch passend zur Grafikkarte.

**Weg 2 – von Hand:**
```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

**Weg 3 – als exe:** `venv\Scripts\python build_exe.py` → `dist/NemiCLI/` mit `NemiCLI.exe`
(Fenster-App, eigenes Terminal-Fenster), `NemiCLIc.exe` (mit Konsole) und dem Ordner `NemiCLIT2`
(ca. 345 MB). Bewusst keine Ein-Datei-exe – die entpackt sich bei jedem Start in den Temp-Ordner,
und das hält Bitdefender für Schadsoftware. torch & Co. kommen aus dem `venv` neben der exe.
Jeder Bau schreibt `SHA256SUMS.txt` und **signiert** die exe, wenn ein Zertifikat `CN=NemiCLI`
da ist (einmalig `python zertifikat.py`).

**Weg 4 – als Installer:** `venv\Scripts\python build_exe.py --msi` → `dist/NemiCLI-<Build>.msi`
(signiert). Installiert ohne Adminrechte nach `%LOCALAPPDATA%\Programs\NemiCLI`, mit Startmenü und
Eintrag unter **Apps**. Deine Daten fasst das Deinstallieren nie an.

**Dein Ordner:** Beim ersten Start fragt ein Fenster, **wo deine Daten liegen sollen** (Chats,
Bilder, Gelerntes) – getrennt vom Programm. `/start` öffnet das Fenster jederzeit; beim Wechsel
wird **kopiert, nie gelöscht**.

**Von überall starten:** Programmordner einmalig in den Benutzer-PATH, dann `nemicli`
(oder `nemicli --sag "…"` – die erste Nachricht geht sofort ab):
```powershell
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';C:\Users\<du>\AppData\Local\NemiCli', 'User')
```

---

## 🧠 Modelle

`/model` öffnet die Auswahl – beim ersten Start ohne Modell von selbst.

| Was | Wo | Wie |
|---|---|---|
| Lokales Sprachmodell | `ModelGGUF/<Name>/` im Programm-Ordner | `/model huggingface`: Größe wählen, nur ladbare Modelle ab 4B, SHA-256-Prüfung, fortsetzbar. Eine `mmproj-*.gguf` daneben = Modell sieht |
| Bildbeschreiber | `Vision/<Name>/` im Programm-Ordner | kleines sehendes Modell (.gguf + mmproj) für lokale Modelle ohne Sehen |
| Gedächtnis-Embeddings | `Models/embeddings/<Name>/` im Programm-Ordner | `/embeddings`; GGUF läuft im eigenen Motor, auf der GPU wenn vorhanden |
| Krea 2 (Bilder) | `Models/Krea2/` im Daten-Ordner | Modell, `text_encoders/`, `vae/`, `tokenizer/`; `/bildmodel` |
| Cloud | – | `/model` → „Cloud-Anbieter hinzufügen“ → Key eintippen (verschlüsselt) |
| Ollama | läuft separat | erscheint von selbst, Modelle direkt aus NemiCLI laden |

In jedem Modell-Ordner legt NemiCLI einen `LIES-MICH.txt` an, der erklärt, was hineingehört.

---

## ⌨ Befehle

Tippe `/` – ein Menü mit allen Befehlen erscheint (Tippen sucht auch in der Beschreibung). `/help`
zeigt alle. Befehle mit Unterpunkten öffnen **ohne Zusatz eine Auswahl** (↑/↓ oder 1–9, Enter, Esc).
Oder frag einfach die KI – sie kennt alle Befehle und öffnet passende Menüs selbst.

**Chat & Modelle**

| Befehl | Was es tut |
|---|---|
| `/model [name\|huggingface]` | Modell wählen, Cloud-Key eintragen, lokales Modell von Hugging Face holen |
| `/kontext [8k…128k\|max\|8bit\|16bit]` | Kontext des lokalen Modells, mit VRAM-Anzeige |
| `/staerke [stufe]` | Denkstufe/Budget: `schnell` · `normal` · `stark` · `max` |
| `/auto [an\|aus]` | Auto-Stark (Ollama): bei schweren Fragen aufs große Gemma |
| `/persönlichkeiten [name\|neu]` | Wer spricht? Wählen oder neu anlegen (auch `/persona`) |
| `/name [zeigen\|loeschen]` | 👤 Über dich: Name, Wunsch-Anrede, was die KI wissen soll |
| `/resume [#]` · `/reset` · `/clear` | Chat fortsetzen · neuer Chat · Bildschirm leeren |
| `/subagenten [an\|auto\|off]` | Helfer-Agenten: fragen · nach Bedarf · gesperrt |

**Arbeiten**

| Befehl | Was es tut |
|---|---|
| `/modus [chat\|lesen\|normal\|auto\|autoan]` | Arbeitsmodus (auch Shift+Tab) |
| `/code [aufgabe\|status]` · `/codeend` | 🟦 Coding-Assistent starten · beenden |
| `/workspace [pfad]` · `/workspaceend` | 📌 Projektordner festnageln – sie arbeitet nur dort · aufheben |
| `/doku [laden python\|mdn]` | 📚 Offline-Doku für den Coding-Assistenten |
| `/sandbox [freigeben <Ordner> [schreiben]\|entziehen <Ordner>]` | 🧪 Sandbox: Status, Projektordner freigeben/entziehen |
| `/uebung [an\|aus\|jetzt]` | Übungsmodus: kleine Programme im Leerlauf |
| `/skills [selbst an\|aus \| alte]` | 🧩 Skills: Liste · selbst anlegen erlauben · alte Notizen |
| `/ml` · `/ordner [pfad]` | Ordner-Sinn: Bericht · Ordner-Typ schätzen |

**Bilder & Kugel**

| Befehl | Was es tut |
|---|---|
| `/bild [prompt\|schritte]` | Bild malen (Krea 2, WebUI oder ComfyUI) · Krea-Schritte 8–16 |
| `/bildmodel [name]` | Bild-Motor wählen (`webui-adresse` ändert den Host) |
| `/charakter [oeffnen]` | 🧬 Charakter-Datei: feste Figur der Persönlichkeit |
| `/kugel [malen …\|motive\|impuls an\|aus\|weg\|ordner]` | 🔮 Schwebekugel: Gesicht und Motive malen, freier Moment |

**Gedächtnis**

| Befehl | Was es tut |
|---|---|
| `/gedaechtnis [indexieren]` | Erinnerungen ansehen/löschen · Vektor-Rückstand aufholen |
| `/embeddings [<Ordner>\|huggingface]` | 🧬 Embedding-Modell fürs Gedächtnis |
| `/wissen` | Gelernte Code-Schnipsel und Notizen |
| `/reflektieren [auto an\|aus]` | 🪞 Lehren ziehen · automatisch nach Korrekturen |
| `/aufraeumen` | Gedächtnis verdichten |

**Sicherheit & System**

| Befehl | Was es tut |
|---|---|
| `/wache [status\|alarme\|fullscan\|…]` | 🛡 Systemwache: Lage, Alarme, Regeln, Vollscan, Selbsttest |
| `/undo` · `/limit [zahl]` · `/whitelist [befehl]` | 🗑 Papierkorb · 🛑 Lösch-Limit · ✅ Befehls-Whitelist |
| `/schluessel <min> [aufgabe]` · `aus` | 🔑 Programm-Ordner auf Zeit für die KI öffnen |
| `/protokoll [n]` | 📜 Aktions-Protokoll |
| `/web [wort\|key]` | Web-Allowlist · Ollama-Suche einrichten |
| `/start` | 📁 Wo dein Daten-Ordner liegt |
| `/einrichten` · `/update` | 🧰 Fehlendes installieren · ⬇ venv prüfen (Lücken über OSV.dev, Updates) |
| `/systemcheck` · `/selbsttest` · `/version` | PC-Steckbrief · Module prüfen · Version |

**Oberflächen & Extras**

| Befehl | Was es tut |
|---|---|
| `/gui` | Desktop-Fenster (Chat · Work, Galerie, Skills, Persönlichkeiten) |
| `/webui` | Browser-Oberfläche mit Lesehilfen |
| `/chrome [neu]` | Chrome-Erweiterung: Port und Schlüssel |
| `/kontextmenue [an\|aus]` | NemiCLI im Windows-Rechtsklick-Menü |
| `/spiel` | Schach · Mühle · Dame · TicTacToe gegen die Persönlichkeit |
| `/theme` · `/emoji` · `/nemi` · `/statistik [reset]` | Farben · Emoji-Kürzel · Maskottchen · Nutzung |
| `/help` · `/exit` | Hilfe · Beenden |

**Tasten:** `Enter` senden · `Strg+J` neue Zeile · `Esc` abbrechen · `F2` Denktext ·
`F8` Änderung freigeben · `F12` ganzes Gespräch sichern · `Shift+Tab` Modus ·
`Bild ↑/↓`, Mausrad scrollen · `Strg+C` markierten Text kopieren.

**Ohne Oberfläche:** `--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` ·
`--auftrag <name>` (Zeitplan-Auftrag ohne Fenster, Antwort wird Bericht).

---

## 🔒 Sicherheit

- **Verändernde Aktionen fragen** – Dateiänderungen zeigt das Prüffenster vollständig als
  Vorher/Nachher, **F8 gibt frei**, Enter nie. Riskantes (Löschen, fremde Befehle, Zeitplan) ist rot
  markiert und ohne „Immer“-Option; ein fremder Befehl fragt zweimal.
- **Systemordner: nachschauen ja, ändern nie.** `C:\Windows`, `Program Files`, `ProgramData` & Co.
  und die System-Registry darf die KI lesen, nicht ändern. Umwege (`..\..`, `%windir%`, `\\?\`) werden
  aufgelöst; ein Selbsttest prüft 40+ Umgehungsversuche.
- **Eigene Sperren** (`schreibsperre.json`): Orte, die die KI weder ansehen noch ändern darf. Der
  Programm-Ordner ist fest gesperrt – NemiCLI kann sich nicht selbst umbauen.
- **Keine Adminrechte für die KI.** `befehl` weist jede Rechte-Erhöhung ab und sperrt Systemwerkzeuge
  ohne harmlosen Zweck (mshta, certutil, Autostart-Einträge, Firewall …). PowerShell startet nur über
  `befehl`, nach Rückfrage – Systemabfragen, Zeitplan und GPU-Erkennung laufen ohne PowerShell.
- **Schutzsoftware ist tabu:** deren Ordner, Prozesse und Dateien untersucht die KI nicht.
- **Papierkorb & Lösch-Limit:** Löschen verschiebt nur, Überschriebenes wird gesichert (`/undo`).
  Mehr als 20 Dateien pro Sitzung → STOPP.
- **Netz nur über HTTPS und Allowlist** (~230 Domains), SSRF-Schutz bei jeder Weiterleitung.
  Web-Inhalte, Dateien und Erinnerungen gelten als **Daten, nie als Anweisungen**; Befehlsmuster
  darin werden markiert oder entfernt.
- **Alles, was auf einen Port hört, hört nur auf `127.0.0.1`** – mit Token oder Schlüssel. Ein Test wacht darüber.
- **Hintergrund-Läufe** (Zeitplan) laufen fest im Modus „Nur Lesen“.
- **API-Keys** verschlüsselt in `.env` (Windows-DPAPI). **exe signiert** (`CN=NemiCLI`) mit SHA-256-Prüfsummen.
- **Modelldateien** werden gehärtet gelesen: jede Zahl geprüft, das Chat-Template der Datei nie ausgeführt.
- **Persönlichkeiten sind nur Prompt-Text** – der Schutz sitzt im Code und gilt für jede.

Sicherheitslücken bitte privat melden: [SECURITY.md](SECURITY.md).

---

## 📁 Ordner

**Das Programm** (`INSTALL`):

```
NemiCli/
├─ main.py                Chat-Schleife, Aktions-Logik, Befehle, Start
├─ start.bat · nemicli.cmd Starter
├─ core/                  Persona, Befehle, Modi, Config, Keys, Pfade, Workspace, Coding-Assistent
├─ engines/               Chat-Motoren, Krea 2, Bildbeschreiber, WebUI/ComfyUI, Systemcheck
├─ ggufengine/            eigener GGUF-Motor (Leser, Tokenizer, Kernel, Modelle)
├─ tools/                 Werkzeuge, Gedächtnis, Sandbox, Web, Wache (tools/wache/)
├─ ui/                    Textual-Vollbild, Desktop-Fenster, Terminal-Fenster, Themes
├─ ModelGGUF/             lokale Sprachmodelle, ein Ordner je Modell     (nicht in Git)
├─ Vision/                Bildbeschreiber, ein Ordner je Modell           (nicht in Git)
├─ Models/embeddings/     Embedding-Modell fürs Gedächtnis                (nicht in Git)
├─ Agenten/               Anleitungen, die die Persönlichkeit abarbeitet
├─ chrome-erweiterung/    Chrome-Erweiterung (LIESMICH.md)
├─ installer/             MSI-Bau
├─ docs/                  Funktionen, Technik, Modelltests, Pläne, Ideen
├─ build_exe.py · NemiCLI.spec · zertifikat.py
├─ nemicli.config.json · .env            Einstellungen · Schlüssel (verschlüsselt)
├─ allowlist.json · befehl_whitelist.json · schreibsperre.json
└─ venv/ · .runtime/      Arbeits-Umgebung, isoliertes Python
```

**Deine Daten** (`DATEN` – Ort wählst du mit `/start`; ohne Wahl beim Programm):

```
<dein Ordner>/
├─ chats/ · Gespraeche/   Gespräche (/resume) · mit F12 gesichert
├─ learned/               Gedächtnis (memory.db), Statistik, Aktions-Protokoll
├─ Profile/<Name>/        je Persönlichkeit: Chats, Bilder, Skills, Erinnerungen, Charakter-Datei
├─ Persoenlichkeiten/     eigene Persönlichkeiten (.md) und Kugel-Bilder
├─ Skills/ · Wissen/      Skills und Wissen für alle Persönlichkeiten
├─ Bilder/                gemalte Bilder, Screenshots
├─ Models/Krea2/          Dateien für Krea 2
├─ Wache/ · Berichte/     Systemwache · Berichte von Wache und Zeitplan
├─ Zeitplan/ · Befehle/   Aufträge für die Aufgabenplanung · Hilfs-Skripte
├─ NemiSandbox/           Sandbox: Lauf-Ordner und Pakete
├─ Doku/ · Cache/         Offline-Doku · Präfix-Cache des Motors
├─ Papierkorb/            Gelöschtes und Überschriebenes (30 Tage, /undo)
└─ Vorschläge/            was die Persönlichkeit von sich aus vorschlägt
```

Ist der Daten-Ordner beim Start nicht erreichbar, **sagt NemiCLI das** – statt mit leerem Gedächtnis
hochzufahren.

---

## 🛠 Voraussetzungen

- **Windows 10/11**, **Python 3.10+** (oder `start.bat` sagt, was fehlt)
- Pakete aus `requirements.txt`: `anthropic`, `openai`, `httpx`, `rich`, `textual`, `prompt_toolkit`,
  `python-dotenv`, `safetensors`, `tokenizers`, `numpy`, `pillow`, `opencv-python` 4.x, `ruff`, `pypdf`,
  `rembg`, `psutil`, `watchdog`, `PySide6-Essentials`, `pyte`
- **torch** mit CUDA für den eigenen Motor, Krea 2 und das Gedächtnis auf der GPU – `/einrichten`
  installiert den passenden Bau, `/systemcheck` nennt den Befehl. Der CPU-Bau geht auch, nur langsamer.
- Krea 2: am schnellsten ab RTX 50xx (NVFP4 auf den Tensorkernen), ~20 GB freier RAM
- Optional: **Ollama** ([ollama.com/download](https://ollama.com/download)), eine laufende
  Forge-WebUI oder ComfyUI, Chrome für die Erweiterung

---

## 📚 Dokumentation

| Datei | Inhalt |
|---|---|
| [docs/Funktionen.md](docs/Funktionen.md) | alle Funktionen im Detail |
| [docs/TechnischeFunktion.md](docs/TechnischeFunktion.md) | Aufbau, Modul für Modul |
| [docs/Modelltests.md](docs/Modelltests.md) | welche lokalen Modelle taugen |
| [CHANGELOG.md](CHANGELOG.md) | Tagebuch aller Änderungen |
| [SECURITY.md](SECURITY.md) | Sicherheitslücken melden |
| [chrome-erweiterung/LIESMICH.md](chrome-erweiterung/LIESMICH.md) | Chrome-Erweiterung einrichten |
| `docs/Plan_*.md` · `docs/idee*.md` | Pläne und Ideen |

---

## 🗺 Roadmap

- [x] Eigener GGUF-Motor statt llama.cpp – mit Sehen, MoE, allen gängigen Formaten
- [x] Windows Installer `NemiCLI-<Build>.msi`: ohne Admin, Startmenü, Deinstallieren unter Apps
- [x] Echte Sandbox für Code der KI (Windows-AppContainer)
- [x] Desktop-Fenster `/gui`, Skills mit Prüffenster, Coding-Assistent mit Todo-Liste
- [x] Pakete gegen Lücken-Datenbanken prüfen (`/update`, OSV.dev)
- [ ] `/backup`: Normal (deine Daten) und Vollständig (mit Programm), als Zip mit SHA-256
- [ ] Installer mit eigenen Fenstern (Weiter-Weiter-Fertig, Desktop-Symbol wählbar)
- [ ] LoRA-Unterstützung für die Bild-Pipeline
- [ ] `Agenten/workspace.md` in den Daten-Ordner bringen (`/workspace` sucht sie dort)
- [ ] Lizenz festlegen (Liebesprojekt – nie zum Verkauf)
- [ ] Signatur mit einem anerkannten Zertifikat, wenn NemiCLI an andere geht
- [ ] Build beim Bitdefender-Falsch-Positiv-Formular einreichen
