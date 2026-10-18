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
>
> 🎂 **Geburtstag: 19. Juni 2026.** An diesem Morgen kam die Frage, woraus Claude Code besteht – zwei Stunden später lief der erste Chat. Vorläufer war Nemi (V1/V2, Desktop- und Android-Apps) ab März 2026. `/version` zeigt das Alter.

---

## ✨ Auf einen Blick

Alles ausführlich: **[docs/Funktionen.md](docs/Funktionen.md)**

| Bereich | Was es kann |
|---|---|
| 🧠 **Eigener GGUF-Motor** | Lokale Modelle ohne Server, reines Python + torch im NemiCLI-Prozess. Gemma 4, Qwen3.5/3.6 (auch MoE), Qwen3-Next, Qwen3-VL, Llama 3/4, Mistral/Ministral, Phi, Granite 4 (Hybrid), Nemotron-H, Falcon-H1, LFM2, DeepSeek, Kimi Linear, Ling 3.0 u. a.; alle gängigen Formate bis IQ1; MoE-Experten bei Bedarf im RAM, das Experten-Lager sortiert auch während der Antwort nach (Gemma 4 26B-A4B bis zu 80 % schneller); spekulatives Dekodieren (n-Gramm, MTP bei Qwen 3.5/3.6) mit gleicher Antwort; Präfix-Cache (Folgerunden in ~0,1 s); `/kontext`: Vorgabe 25k, bis 128k mit VRAM-Automatik oder mit Gesprächsspeicher im RAM, Anzeige wie voll der Kontext ist; volle Gespräche werden als „Stand des Gesprächs“ zusammengefasst |
| 👁 **Sehen** | Cloud, Ollama und eigener Motor (mmproj: Gemma 4, Qwen3.5/Qwen3-VL, Pixtral). Modelle ohne Sehen bekommen Hilfe vom **Bildbeschreiber** (`Vision/`) |
| 🔊 **Stimme** | Lokal mit Qwen3-TTS im eigenen Motor (~3 GB VRAM, schneller als Echtzeit): jede Persönlichkeit entwirft ihre Stimme mit dir aus einer Beschreibung, liest auf Wunsch vor (Text, Markdown, PDF), die Kugel spricht ihre Sprechblasen; Esc stoppt. Die Stimme steht als `voice` in der Charakter-Datei; Proben unter 150 Hz (Männerbereich) spielen nicht, Config `stimme_min_hz` |
| ☁ **Cloud** | Anthropic, OpenAI, Google, Mistral, Cohere, Perplexity, DeepSeek, xAI, Groq, OpenRouter, Ollama Cloud – Modelle live abgefragt, Keys verschlüsselt (DPAPI) |
| ⚙ **Werkzeuge** | Dateien, Ordner, Suche, PowerShell (auch Python/pip), Web über Allowlist (andere Seiten über Ollama), Fachquellen (arXiv, PubMed, GitHub, Stack Overflow, OpenAlex), Recherche mit Bericht (die KI entscheidet selbst, wann gründlich recherchiert wird), PDF, Systemabfragen ohne PowerShell, Zeitplan, Helfer-Agenten, gelbe Todo-Liste bei Aufgaben mit mehreren Schritten |
| 🟦 **Coding-Assistent** | `/code`: Auftrag → Recherche → Todo-Liste → Punkt für Punkt, grün nur nach echter Prüfung; kennt installierte Versionen, ruff, Offline-Doku (Python, MDN), Seiten-Screenshots; **Coding-Sammlung** aus freigegebenem Code, daraus ein **Coding-LoRA** für lokale Modelle (eigener LoRA-Motor, trainiert von einem Cloud-Modell) |
| 🧪 **Sandbox** | Code zum Ausprobieren läuft im Windows-AppContainer – Internet ja, deine Dateien nein |
| 🛡 **Systemwache** | Hintergrund-Wächter mit 14 Regeln, eigenem Isolation Forest und einem Folgen-Netz (GRU), das ungewöhnliche Abläufe in Prozess-Ketten erkennt (erst im Schattenmodus, scharf nur über `/wache dl warnen`); weckt die Persönlichkeit ab 5–20 ungeprüften Alarmen, sie prüft und urteilt selbst; Vollscan mit jeder Festplatte bis in die Partitionen, Download-Herkunft, zusätzlichen Datenströmen (ADS) und nicht lesbaren Ordnern samt Grund |
| 💾 **Gedächtnis** | Chats, Fakten, Lehren, Skills, Wissen-Ordner; hybride Suche (Stichworte + Vektoren), lernt nach Korrekturen von selbst, Skills bessern sich nach Problemen selbst nach (Strichliste je Skill), Kontext ohne Ende (AutoContextCleaner) |
| 🎨 **Bilder** | Eigene Krea-2-Pipeline (NVFP4 auf RTX 50xx), Gesichter nachmalen, Charakter-Datei für eine feste Figur, Serien bis 30 Bilder, Comics mit Sprechblasen; **Charakter-LoRA**: die Persönlichkeit bringt Krea bei, wie sie aussieht (greift nur, wenn sie sich selbst malt); optional Forge-WebUI oder ComfyUI |
| 🎬 **Video** | Eigener Video-Motor (Wan 2.2 TI2V 5B als GGUF, umT5 als GGUF): bis 20 s, 24 Bilder/s, Clips à 5 s aneinander, ein Startbild von Krea 2 hält die Figur, Charakter-LoRA hält das Gesicht; Werkzeug `video_erstellen`, `/video`. Bei Cloud-Modellen läuft es **im Hintergrund** – weiterreden, Stand rechts neben der Eingabe, Fertigmeldung automatisch. Dazu der **Tagesrückblick**: die Bilder des Tages als Video mit ihrer Stimme |
| 🗣 **Porträt** | Eigener Porträt-Motor (LivePortrait): ein Bild bewegt sich – lächeln, zwinkern, nicken – und **spricht** mit ihrer Stimme (MP4 mit Ton, bis 60 s), im kleinen Fenster oder in der Kugel |
| 🎵 **Musik** | Eigener Musik-Motor (ACE-Step 1.5 als GGUF): „Mach mir einen Sommer-Song“ – die KI schreibt Stil und Liedtext, der Song-Planer plant Aufbau und Melodie, 48 kHz Stereo bis 4 Minuten, jeder Song mit Titel. **Cover:** einen vorhandenen Song (auch MP3/M4A/FLAC) im anderen Stil neu einspielen. Fenster `/musik` zum Wünschen, Ausarbeiten und Komponieren |
| 🎭 **Oberfläche** | Eigenes Terminal-Fenster mit **Reitern** und dem Reiter „🤖 KI“ (jeder Befehl der KI live), Textual-Vollbild, Desktop-Fenster `/gui` (mit 🔊 Vorlesen und ⚙ Einstellungsseite), WebUI mit Lesehilfen, Schwebekugel auf dem Desktop (schläft, sagt etwas zur Musik, wählt selbst, was sie anhat, nimmt Bilder und Dateien per Ziehen an, erinnert dich), Spiele, Chrome-Erweiterung mit Seitenleisten-Chat (Seite, Bildschirmfoto, Entwürfe), Rechtsklick-Menü |
| 🔒 **Sicherheit** | Prüffenster mit Vorher/Nachher (F8), Risikostufen, Papierkorb + `/undo`, Lösch-Limit, Systemordner nur lesen, Fremddaten markiert, Skill-Wächter mit KI-Zweitmeinung, Aktions-Protokoll, `/backup` als Zip mit SHA-256, tägliche automatische Sicherung von Gedächtnis & Co. |

---

## 🚀 Starten

**Weg 1 – bequem:** `start.bat` doppelklicken. Der Starter prüft Python, `venv` und Pakete und baut
nach, was fehlt. Danach `/einrichten`: holt ein eigenes Python (isoliert in `.runtime/`, ohne Admin),
legt das venv an und öffnet für torch ein Fenster mit aktiviertem venv (`/venv`) – dort den angezeigten Befehl
eingeben, bei NVIDIA RTX z. B. `pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu132`.

**Weg 2 – von Hand:**
```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

**Weg 3 – als exe:** `venv\Scripts\python build_exe.py` → `dist/NemiCLI/` mit `NemiCLI.exe`
(Fenster-App, eigenes Terminal-Fenster), `NemiCLIc.exe` (mit Konsole) und dem Ordner `NemiCLIT2`.
Bewusst keine Ein-Datei-exe – die entpackt sich bei jedem Start in den Temp-Ordner,
und das hält Bitdefender für Schadsoftware. torch & Co. kommen aus dem `venv` neben der exe.
Jeder Bau schreibt `SHA256SUMS.txt` und **signiert** die exe, wenn ein Zertifikat `CN=NemiCLI`
da ist (einmalig `python zertifikat.py`).

**Weg 4 – Setup-Datei:** Derselbe Bau legt `dist/NemiCLI-Setup-<Build>.exe` daneben (signiert, mit
`.sha256`) – **eine Datei zum Weitergeben**. Sie zeigt in einem eigenen Fenster die gefundenen Installationen
(`.ncli2` im Benutzerordner und auf jedem Laufwerk), installiert neu oder ersetzt nur die Programmteile – deine
Daten bleiben – und legt auf Wunsch eine Desktop-Verknüpfung an. Läuft NemiCLI noch, bittet sie ums Schließen.
Wer lieber einen Windows-Installer will: `build_exe.py --msi` baut zusätzlich `dist/NemiCLI-<Build>.msi`.

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
| Stimme | `Vision/<Name>/` im Programm-Ordner | Qwen3-TTS-Talker (VoiceDesign) + 12-Hz-Tokenizer als GGUF |
| Gedächtnis-Embeddings | `Models/embeddings/<Name>/` im Programm-Ordner | `/embeddings`; GGUF läuft im eigenen Motor, auf der GPU wenn vorhanden |
| Krea 2 (Bilder) | `Models/Krea2/` im Daten-Ordner | Modell, `text_encoders/`, `vae/`, `tokenizer/`; `/bildmodel` |
| TensorRT-RTX (Zusatz, NVIDIA RTX) | `Models/TensorRT-RTX/` im Programm-Ordner | entpackten SDK-Ordner von [developer.nvidia.com/tensorrt-rtx](https://developer.nvidia.com/tensorrt-rtx) hineinlegen – siehe unten |
| Video (Wan 2.2 TI2V, Turbo, Fun InP als GGUF) | `VideoGen/` im Programm-Ordner (wie `Vision/`); je Modell ein Unterordner (`.gguf` + VAE), Liste siehe unten | `<Modell>/` mit `.gguf` und `Wan2.2_VAE.safetensors`, geteilt: `text_encoders/` (umT5 GGUF), `tokenizer/`; `/video` |
| Musik (ACE-Step 1.5) | `Models/Musik/` oder `Models/Music/` (Daten- oder Programm-Ordner) | `acestep-v15-turbo-*.gguf`, `Qwen3-Embedding-0.6B-*.gguf` (Textleser), `vae-*.gguf`, optional `acestep-5Hz-lm-*.gguf` (Song-Planer); jede Datei vor dem ersten Laden per SHA-256 geprüft; `/musik` |
| Porträt (LivePortrait) | `Models/LivePortrait/` (Daten- oder Programm-Ordner) | die fünf `.pth`-Netze + `landmark.onnx`; nur mit passender SHA-256 gelesen, einmal nach safetensors umgeschrieben |
| Cloud | – | `/model` → „Cloud-Anbieter hinzufügen“ → Key eintippen (verschlüsselt) |
| Ollama | läuft separat | erscheint von selbst, Modelle direkt aus NemiCLI laden |

In jedem Modell-Ordner legt NemiCLI einen `LIES-MICH.txt` an, der erklärt, was hineingehört.

### 🎬 Video-Modelle selbst laden

Die Dateien lädst du selbst im Browser von Hugging Face bzw. GitHub und legst sie in `VideoGen/` im
Programm-Ordner. Danach die Größe und den SHA-256 vergleichen (Windows: `certutil -hashfile <Datei> SHA256`).
Text-Encoder, Tokenizer und Mini-VAE dürfen einmal geteilt oben in `VideoGen/` liegen **oder** im Modell-Ordner.

```
VideoGen/
├─ WAN-Turbo/                        schnell: 4 Schritte (empfohlen zum Start)
│  ├─ Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf
│  └─ Wan2.2_VAE.safetensors
├─ WAN-GGUF/                         normal: 30 Schritte
│  ├─ Wan2.2-TI2V-5B-Q4_K_M.gguf
│  └─ Wan2.2_VAE.safetensors
├─ WAN-FunInP/                       Start- und Endbild (Schlüsselbilder für lange Videos)
│  ├─ Wan2.2-Fun-5B-InP-Q8_0.gguf
│  └─ Wan2.2_VAE.safetensors
├─ text_encoders/umt5-xxl-encoder-Q8_0.gguf
├─ tokenizer/                        tokenizer.json, spiece.model, special_tokens_map.json, tokenizer_config.json
└─ tae/                              Mini-VAE (35× schnelleres Auspacken, optional)
   ├─ taew2_2_super.safetensors
   └─ taew2_2.safetensors
```

| Datei | Quelle | Bytes | SHA-256 |
|---|---|---|---|
| `Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf` | [hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF](https://huggingface.co/hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF) | 3.437.927.136 | `13b3bee8fcafd9f9a3778ed1a0ca973f4a7d152094ea305f8384b0abd7e1ad51` |
| `Wan2.2-TI2V-5B-Q4_K_M.gguf` | [QuantStack/Wan2.2-TI2V-5B-GGUF](https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF) | 3.433.116.000 | `95b19697b7f98e65b0a543640e9ca7b4dfec32e2a6e3731e8e10708be52655e2` |
| `Wan2.2-Fun-5B-InP-Q8_0.gguf` | [QuantStack/Wan2.2-Fun-5B-InP-GGUF](https://huggingface.co/QuantStack/Wan2.2-Fun-5B-InP-GGUF) | 5.407.546.080 | `c88969a140da94176d8af263d031d03b83a7b4783108c73e828a69b9a8873d7f` |
| `Wan2.2_VAE.safetensors` | [QuantStack/Wan2.2-Fun-5B-InP-GGUF](https://huggingface.co/QuantStack/Wan2.2-Fun-5B-InP-GGUF) (`vae/`) | 1.409.400.960 | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |
| `umt5-xxl-encoder-Q8_0.gguf` | [city96/umt5-xxl-encoder-gguf](https://huggingface.co/city96/umt5-xxl-encoder-gguf) | 6.043.068.256 | `2521d4de0bf9e1cc6549866463ceae85e4ec3239bc6063f7488810be39033bbc` |
| `tokenizer.json` | [alibaba-pai/Wan2.2-Fun-5B-InP](https://huggingface.co/alibaba-pai/Wan2.2-Fun-5B-InP) (`google/umt5-xxl/`) | 16.837.417 | `6e197b4d3dbd71da14b4eb255f4fa91c9c1f2068b20a2de2472967ca3d22602b` |
| `spiece.model` | ebenda | 4.548.313 | `e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458` |
| `special_tokens_map.json`, `tokenizer_config.json` | ebenda | klein | – |
| `taew2_2_super.safetensors` | [madebyollin/taehv](https://github.com/madebyollin/taehv/tree/main/safetensors) (MIT) | 40.986.488 | `cf6d8bdadb77d9f9741ae28b5b4659bb74cf9b0be6d08fc4afed3cb50c39364c` |
| `taew2_2.safetensors` | ebenda | 22.848.048 | `b84609b2a133d48434bd9636bfcb44bf05168dc436e2d3cecf26256faa1f5325` |

Alle Video-Modelle stehen unter Apache 2.0, die Mini-VAE unter MIT. Ohne `tae/` packt die große VAE aus
(langsamer, gleiches Bild). Wer nur ein Modell will, nimmt `WAN-Turbo` + Text-Encoder + Tokenizer.

### ⚡ TensorRT-RTX (Zusatz für RTX-Karten)

NVIDIA TensorRT for RTX ist **nicht** Teil von NemiCLI (eigene NVIDIA-Lizenz) – wer es nutzen möchte, lädt das
SDK selbst (kostenloses NVIDIA-Developer-Konto) und legt den entpackten Ordner nach `Models/TensorRT-RTX/`
(z. B. `Models/TensorRT-RTX/TensorRT-RTX-1.7.1.116/` mit `bin/` und `python/`). Das Python-Paket installiert
NemiCLI beim ersten Gebrauch selbst aus diesem Ordner (offline, ins eigene venv). `/systemcheck` zeigt den Stand.

| Wo | Was TensorRT übernimmt | Abschalten |
|---|---|---|
| Lokales Sprachmodell | liest lange Prompts mit den FP4-Kernen (Blackwell); die Antwort schreibt weiter der eigene Motor, beide teilen den Gesprächsspeicher. Bauarten aus dem Baukasten (Llama, Qwen 2/3, Mistral/Ministral 3, Granite, Phi-3, Gemma 1, OLMo 2, SmolLM3 …), ohne MoE | `trt_chat` |
| Krea 2 | die Transformer-Blöcke des Bildnetzes; NVFP4-Gewichte werden unverändert übernommen | `bild_trt` |
| Wan 2.2 | die DiT-Blöcke (NVFP4; `video_trt_format` = `fp8` rechnet genau wie bisher); Videos mit Charakter-LoRA rechnet weiter torch | `video_trt` |

So läuft es: Beim ersten Mal misst NemiCLI die Werte im Modell ein und baut das Netz im Hintergrund – bis dahin
rechnet alles wie gewohnt. Gebaute Netze liegen in `Models/TensorRT-RTX/engines/` (je Modell und Grafikkarte,
mehrere GB) und werden beim nächsten Start nur geladen. Die Gewichte kommen nur so weit ins VRAM, wie es frei
ist, der Rest wird über PCIe nachgeschoben; nach einem Bild oder Video und beim Malen neben einem lokalen
Modell geben die Netze ihr VRAM wieder ab.

---

## ⌨ Befehle

Tippe `/` – ein Menü mit allen Befehlen erscheint (Tippen sucht auch in der Beschreibung). `/help`
zeigt alle. Befehle mit Unterpunkten öffnen **ohne Zusatz eine Auswahl** (↑/↓ oder 1–9, Enter, Esc).
Oder frag einfach die KI – sie kennt alle Befehle und öffnet passende Menüs selbst.

**Chat & Modelle**

| Befehl | Was es tut |
|---|---|
| `/model [name\|huggingface]` | Modell wählen, Cloud-Key eintragen, lokales Modell von Hugging Face holen |
| `/kontext [anzeigen\|8k…128k\|max\|8bit\|16bit]` | 📊 Womit der Kontext gefüllt ist (wie `/context` bei Claude Code); lokal: Größe, Vorgabe 25k |
| `/staerke [stufe]` | Denkstufe/Budget: `schnell` · `normal` · `stark` · `max` |
| `/auto [an\|aus]` | Auto-Stark (Ollama): bei schweren Fragen aufs große Gemma |
| `/persönlichkeiten [name\|neu]` | Wer spricht? Wählen oder neu anlegen (auch `/persona`) |
| `/name [zeigen\|loeschen]` | 👤 Über dich: Name, Wunsch-Anrede, was die KI wissen soll |
| `/reset` · `/clear` | neues Gespräch (sonst geht das letzte beim Start von selbst weiter) · Bildschirm leeren |
| `/subagenten [an\|auto\|off]` | Helfer-Agenten: fragen · nach Bedarf · gesperrt |

**Arbeiten**

| Befehl | Was es tut |
|---|---|
| `/modus [chat\|lesen\|normal\|auto\|autoan]` | Arbeitsmodus (auch Shift+Tab) |
| `/code [aufgabe\|status]` · `/codeend` | 🟦 Coding-Assistent starten · beenden |
| `/doku [laden python\|mdn]` | 📚 Offline-Doku für den Coding-Assistenten |
| `/sandbox [freigeben <Ordner> [schreiben]\|entziehen <Ordner>]` | 🧪 Sandbox: Status, Projektordner freigeben/entziehen |
| `/uebung [an\|aus\|jetzt]` | Übungsmodus: kleine Programme im Leerlauf |
| `/skills [selbst an\|aus \| alte]` | 🧩 Skills: Liste mit Bilanz und Wächter-Bericht · Prüffenster an/aus · alte Notizen |
| `/ml` · `/ordner [pfad]` | Ordner-Sinn: Bericht · Ordner-Typ schätzen |

**Bilder, Video, Musik & Kugel**

| Befehl | Was es tut |
|---|---|
| `/bild [prompt\|schritte]` | Bild malen (Krea 2, WebUI oder ComfyUI) · Krea-Schritte 8–16 |
| `/video <beschreibung> [--sekunden 10] [--start bild.png]` | 🎬 Video mit dem eigenen Motor: 704×1024 hochkant (Seitenverhältnis wie Krea 2), 24 Bilder/s, Clips à 5 s bis 20 s |
| `/musik` | 🎵 Musik-Fenster: Wunsch eintippen, ✨ Ausarbeiten (Stil, Liedtext, Tempo), 🎵 Komponieren, 🎙 Als Cover – oder einfach der KI sagen, welchen Song sie machen soll |
| `/bildmodel [name]` | Bild-Motor wählen (`webui-adresse` ändert den Host) |
| `/charakter [oeffnen]` | 🧬 Charakter-Datei: feste Figur der Persönlichkeit |
| `/kugel [malen …\|motive\|impuls\|leben\|musik\|erinnerungen\|weg\|ordner]` | 🔮 Schwebekugel: Gesicht und Motive malen, freier Moment, Desktop-Leben, Musik spüren, Erinnerungen |

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
| `/wache [status\|alarme\|fullscan\|dl\|…]` | 🛡 Systemwache: Lage, Alarme, Regeln, Vollscan, Folgen-Netz, Selbsttest |
| `/backup [teil\|voll\|voll modelle] [ganz]` · `auto [jetzt]` · `pruefen` · `auspacken <name>` · `ordner` | 💾 Sicherung als Zip mit SHA-256: Teil (Bilder, Persönlichkeiten, Profile) oder vollständig; danach differenzielle Nachträge, Wiederherstellen in einen neuen Ordner. Dazu täglich automatisch Gedächtnis, Profile, Persönlichkeiten & Co. (zwei Stände, überschrieben) |
| `/undo` · `/limit [zahl]` · `/whitelist [befehl]` | 🗑 Papierkorb · 🛑 Lösch-Limit · ✅ Befehls-Whitelist |
| `/schluessel <min> [aufgabe]` · `aus` | 🔑 Programm-Ordner auf Zeit für die KI öffnen |
| `/protokoll [n]` | 📜 Aktions-Protokoll |
| `/voice [an\|knopf\|aus\|jetzt\|text]` | 🔊 Stimme: `an` liest jede Antwort vor, `knopf` nur auf Anfrage (`jetzt` = letzte Antwort einmal) und gibt den Grafikspeicher danach sofort frei, `aus` bleibt aus bis du einschaltest; `text` zeigt die letzte Antwort ganz |
| `/web [wort\|key\|fremdseiten frei\|fragen\|aus]` | Web-Allowlist · Ollama-Suche einrichten · Seiten außerhalb der Allowlist über Ollama |
| `/start` | 📁 Wo dein Daten-Ordner liegt |
| `/einrichten` · `/update` · `/venv` | 🧰 Fehlendes installieren · ⬇ venv prüfen (Lücken über OSV.dev, Updates) · 🪟 Fenster mit dem venv (torch) |
| `/systemcheck` · `/selbsttest` · `/version` | PC-Steckbrief · Module prüfen · Version |

**Oberflächen & Extras**

| Befehl | Was es tut |
|---|---|
| `/gui` | Desktop-Fenster (Chat · Work, Galerie, Skills, Persönlichkeiten, ⚙ Einstellungen; 🔊 Vorlesen unter jeder Antwort) |
| `/webui` | Browser-Oberfläche mit Lesehilfen |
| `/chrome [neu]` | Chrome-Erweiterung (Seitenleisten-Chat + Rechtsklick): Port und Schlüssel |
| `/kontextmenue [an\|aus]` | NemiCLI im Windows-Rechtsklick-Menü |
| `/debug [aus\|an\|motor an\|aus]` | 🐞 Debugbericht läuft immer: Grafikspeicher je Prozess, Modelle laden/entladen, Fehler – eine Datei je Start unter `Debugbericht/`; mit einem Cloud-Modell liest die KI ihn selbst („Was sagt der Debug gerade?“) und schreibt ihre Auswertung nach `Berichte/`. **Selbstdiagnose 🩺:** „Prüf dich mal“ – die Cloud-KI sieht sich in einem Zug an (Version, Rechner, Grafikspeicher je Prozess, Systemcheck, letzte Fehler) und legt den Befund als `Berichte/Diagnose_<Datum>.md` ab. **Fehler-Wächter:** erkennt Hänger und Bremsen (mit der Stelle im Code), Arbeit, die beim Entladen nicht aufhört, Abstürze und ein unsauberes Ende früherer Starts, wiederholte Fehler und ungültige Werte – als „⚠ BEFUND“ im Bericht und in der Selbstdiagnose |
| `/spiel` | Schach · Mühle · Dame · TicTacToe gegen die Persönlichkeit |
| `/theme` · `/emoji` · `/nemi` · `/statistik [reset]` | Farben · Emoji-Kürzel · Maskottchen · Nutzung |
| `/help` · `/exit` | Hilfe · Beenden |

**Tasten:** `Enter` senden · `Strg+J` neue Zeile · `Esc` abbrechen · `F2` Denktext ·
`F8` Änderung freigeben · `F12` ganzes Gespräch sichern · `Shift+Tab` Modus ·
`Strg+K` **Suche** (alle Befehle, Einstellungen und Modelle; Tippfehler sind okay) · `F3` **Cockpit** ·
`Bild ↑/↓`, Mausrad scrollen · `Strg+C` markierten Text kopieren.
**Im eigenen Terminal-Fenster:** `Strg+Umschalt+T` neuer Reiter mit Kommandozeile · `Strg+Umschalt+W`
schließen · `Strg+Bild ↑/↓` wechseln · `Strg+Umschalt+K` Reiter „🤖 KI“ (zeigt jeden Befehl der KI live).
**Markieren:** linke Maustaste gedrückt halten und ziehen – am oberen oder unteren Rand (auch über die Eingabe hinaus) rollt der Verlauf von selbst bis zum Textende mit; Loslassen kopiert. Im eigenen NemiCLI-Fenster markiert `Umschalt` + Ziehen nur das Sichtbare.

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
- **Netz nur über HTTPS**, Allowlist (~230 Domains) direkt mit SSRF-Schutz bei jeder Weiterleitung.
  Andere Seiten lädt Ollama (`web_fetch`) als „unbekannte Seite“ – `/web fremdseiten frei|fragen|aus`;
  eine Adresse, die aus keinem Suchtreffer stammt, fragt vorher (sonst könnten Daten über die URL abfließen).
- **Netz-Sperre:** Versteckter Seitentext, unsichtbare Zeichen und Tarnschrift werden vor dem Lesen entfernt
  bzw. erkannt. Solange Inhalt aus dem Netz im Gespräch steht (auch heruntergeladene Dateien und Bilder),
  fragen Befehle, Schreiben und Merken – auch im Auto-Modus; nach privaten Daten auch neue Suchen. Seiten, die
  Befehle einschmuggeln wollten, werden gemerkt und fragen beim nächsten Mal.
- **Pfade werden aufgelöst, bevor sie geprüft werden** (Freigaben dieses Rechners, Gerätepfade, Kurznamen,
  Platzhalter, Variablen), und Programme, die die KI startet, bekommen keine API-Schlüssel.
- **Wächter für steuernde Dateien:** Änderungen an der eigenen Persönlichkeit und an Anleitungen werden geprüft,
  Zeitplan und Skripte fragen immer.
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
├─ core/                  Persona, Befehle, Modi, Config, Keys, Pfade, Coding-Assistent
├─ engines/               Chat-Motoren, Krea 2, Video-Motor, Bildbeschreiber, WebUI/ComfyUI, Systemcheck
├─ ggufengine/            eigener GGUF-Motor (Leser, Tokenizer, Kernel, Modelle)
├─ loramotor/             Coding-LoRA für lokale Modelle: an-/abklinken, Lernweg, Training
├─ tools/                 Werkzeuge, Gedächtnis, Sandbox, Web, Wache (tools/wache/)
├─ ui/                    Textual-Vollbild, Desktop-Fenster, Terminal-Fenster, Themes
├─ ModelGGUF/             lokale Sprachmodelle, ein Ordner je Modell     (nicht in Git)
├─ VideoGen/              Dateien für den Video-Motor (Wan 2.2 TI2V GGUF + VAE je Unterordner, umT5 GGUF)
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
├─ chats/ · Gespraeche/   Gespräche (gehen beim Start weiter) · mit F12 gesichert
├─ learned/               Gedächtnis (memory.db), Statistik, Aktions-Protokoll
├─ Profile/<Name>/        je Persönlichkeit: Chats, Bilder, Videos, Musik, Skills, Erinnerungen, Charakter-Datei, LoRA
├─ Persoenlichkeiten/     eigene Persönlichkeiten (.md) und Kugel-Bilder
├─ Skills/ · Wissen/      Skills und Wissen für alle Persönlichkeiten
├─ Bilder/                gemalte Bilder, Screenshots
├─ Models/Krea2/          Dateien für Krea 2
├─ Wache/ · Berichte/     Systemwache · Berichte von Wache und Zeitplan
├─ Zeitplan/ · Befehle/   Aufträge für die Aufgabenplanung · Hilfs-Skripte
├─ NemiSandbox/           Sandbox: Lauf-Ordner und Pakete
├─ Doku/ · Cache/         Offline-Doku · Präfix-Cache des Motors
├─ Papierkorb/            Gelöschtes und Überschriebenes (30 Tage, /undo)
├─ Backup/                Sicherungen von /backup (Zip + .sha256)
└─ Vorschläge/            was die Persönlichkeit von sich aus vorschlägt
```

Ist der Daten-Ordner beim Start nicht erreichbar, **sagt NemiCLI das** – statt mit leerem Gedächtnis
hochzufahren.

---

## 🛠 Voraussetzungen

- **Windows 10/11**, **Python 3.14** (getestet; oder `start.bat` sagt, was fehlt). Die exe bringt ihr Python
  mit; das venv von `/einrichten` bekommt dieselbe Version – ein venv für ein anderes Python legt es beiseite.
- Pakete aus `requirements.txt`: `anthropic`, `openai`, `httpx`, `rich`, `textual`, `prompt_toolkit`,
  `python-dotenv`, `packaging`, `regex`, `safetensors`, `tokenizers`, `numpy`, `pillow`, `ruff`, `pypdf`,
  `rembg`, `onnxruntime`, `scipy`, `psutil`, `watchdog`, `PySide6-Essentials`, `pyte`
- **torch** mit CUDA für den eigenen Motor, Krea 2 und das Gedächtnis auf der GPU – selbst installieren im
  venv-Fenster (`/venv`), das den passenden Befehl zeigt. Der CPU-Bau geht auch, nur langsamer.
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
- [x] Desktop-Fenster `/gui`, Skills mit Wächter und geschlossener Lern-Schleife, Coding-Assistent mit Todo-Liste
- [x] Pakete gegen Lücken-Datenbanken prüfen (`/update`, OSV.dev)
- [x] `/backup`: Teil (Bilder, Persönlichkeiten, Profile) und vollständig, als Zip mit SHA-256, differenzielle Nachträge, Wiederherstellen
- [x] Autobackup: täglich das Kleine und Wichtige (Gedächtnis, Profile, Persönlichkeiten …), zwei Stände
- [x] Setup-Datei mit eigenem Fenster: findet Installationen, aktualisiert nur das Programm, Desktop-Symbol wählbar
- [x] Charakter-LoRA für Bilder (Krea 2) und Videos (Wan 2.2), trainiert von der Persönlichkeit selbst
- [x] Eigene Motoren für Musik (ACE-Step 1.5, mit Cover) und bewegte, sprechende Porträts (LivePortrait)
- [ ] Lizenz festlegen (Liebesprojekt – nie zum Verkauf)
- [ ] Signatur mit einem anerkannten Zertifikat, wenn NemiCLI an andere geht
- [ ] Build beim Bitdefender-Falsch-Positiv-Formular einreichen
