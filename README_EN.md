<div align="center">

```
 █   █  █████  █   █  ███   ████  █      ███
 ██  █  █      ██ ██   █   █      █       █
 █ █ █  ████   █ █ █   █   █      █       █
 █  ██  █      █   █   █   █      █       █
 █   █  █████  █   █  ███   ████  █████  ███
```

# NemiCLI

**Your own AI agent in the terminal — cloud and local, with tools, memory, and heart.** ✦

**Developed by D. Hoffmann (Vibecoder) · built with Claude Opus 5.5 (Anthropic)**

[Deutsch](README.md) · [Latest alpha release](https://github.com/Overbid4592/Nemicli-Terminal/releases/tag/nemicli)

</div>

---

## What is NemiCLI?

NemiCLI is a self-built **AI agent for the Windows terminal**, written in Python. It supports **37 cloud chat providers**, local models through its **own GGUF engine**, and external local servers such as Ollama, LM Studio, Jan, llama.cpp and vLLM.

It can read and write files, execute commands, research the web, assist with programming, understand and generate images, and monitor the PC. **Changes require user approval.** It includes configurable personalities, themes, a desktop window (`/gui`), and accessibility features for people with dyslexia.

The core is **pure Python — no Node.js, npm, llama.cpp, or transformers dependency for its own model engine**. Local language models, image understanding and memory use its GGUF engine; image generation uses a dedicated Krea 2 pipeline.

> 💜 Started from one question: "How does a coding agent actually work?" NemiCLI's birthday is June 19, 2026. Its predecessor, Nemi V1/V2, dates back to March 2026.

### 🔢 Version history

The German README explains a version-number correction: after 8.9 the project continued with 8.10 through 8.23 even though the major version should have become 9. On October 26, 2026 it was renamed **9.23 Alpha**, without changing historical changelog entries. The [current public GitHub release](https://github.com/Overbid4592/Nemicli-Terminal/releases/tag/nemicli) is named **NemiCLI 9.24 Alpha**.

---

## ✨ Features at a glance

For the complete, continually maintained German specification, see [docs/Funktionen.md](docs/Funktionen.md).

| Area | Capabilities |
|---|---|
| 🧠 **Custom GGUF engine** | Local inference inside the Python process with torch; supports Gemma 4, Qwen3.5/3.6 and MoE variants, Qwen3-Next/VL, Llama 3/4, Mistral, Phi, Granite 4, Nemotron-H, Falcon-H1, LFM2, DeepSeek and others. Quantization down to IQ1; RAM-offloaded MoE experts, speculative decoding, prefix caching and context management up to 128k depending on resources. |
| 👁 **Vision** | Cloud, Ollama and the custom engine; an image describer can assist models that lack vision capabilities. |
| 🧭 **Reasoning and verification** | Task difficulty estimation, multi-step reasoning, alternative solutions and a separate reviewer for difficult answers; checks model claims against actual tool results. |
| 🔊 **Voice** | Local Qwen3-TTS, personality-specific voices and read-aloud for text, Markdown and PDF. |
| ☁ **Cloud models** | **37 chat providers**, including Anthropic, OpenAI, Google, Mistral, DeepSeek, xAI, Groq, Kimi, GLM, Qwen, MiniMax, OpenRouter and Ollama Cloud. Live model lists, search and DPAPI-protected keys on Windows. |
| ⚙ **Agent tools** | File operations, PowerShell, web research, specialist sources, PDF, system information, schedules, helper agents and task lists. |
| 🟦 **Coding assistant** | `/code`: research, a task list, stepwise execution and verification, including linting and browser-page checks. Approved code can be collected for coding LoRA training. |
| 🧪 **Sandbox** | Experimental code executes within a Windows AppContainer, isolated from personal files. |
| 🛡 **System Watchdog** | Background security monitoring with detection rules, Sigma rule support, event logs, MITRE ATT&CK mappings, ML anomaly detection and shadow-mode learning. Designed to **complement, not replace, antivirus software**. |
| 🌐 **Network Watchdog** | Investigates connection information and firewall alerts, with ML-based connection and sequence analysis; initially in shadow mode. |
| 💾 **Memory** | Conversations, facts, skills and knowledge files; hybrid keyword/vector retrieval, optional GGUF reranker and conversation summarization. |
| 🎨 **Images** | Krea 2 generation pipeline, character consistency, image series and comics, character LoRA, optional Forge/ComfyUI, plus **12 cloud image providers**. |
| 🎬 **Video** | Local Wan 2.2 video pipeline with GGUF components, clips and character continuity; **6 cloud video provider integrations** and asynchronous cloud generation. |
| 🗣 **Animated portraits** | LivePortrait-based animated talking portraits with voice. |
| 🎭 **Personalities** | Gallery of nine predefined characters, editable traits, portraits and a stable character identity. |
| 🎵 **Music** | Local ACE-Step 1.5 GGUF-based music creation, songwriting and covers, with a dedicated `/musik` interface. |
| 🛍 **Appstore + NemiView** | HTML/CSS/JS apps rendered with NemiCLI's own interface engine. |
| 🎭 **Interface** | Terminal tabs, Textual interface, `/gui` desktop app, web UI, floating desktop companion, Chrome extension and Windows right-click integration. |
| 🔒 **Approvals and recovery** | Before/after review with **F8 to approve**, risk levels, trash/undo, limits on deletion, protected system paths, skill checking, action logs, SHA-256 backups and daily automatic backups. |

Features listed here reflect the project's documentation and may be experimental in the alpha release.

---

## 🚀 Getting started

**Option 1 — `start.bat`:** Double-click the starter. It checks Python and the environment and offers to install missing packages. Then use `/einrichten` for an isolated Python environment, dependencies and the CUDA-enabled torch installation appropriate for your GPU.

**Option 2 — installer:** Download the Windows installer from the [latest release](https://github.com/Overbid4592/Nemicli-Terminal/releases/tag/nemicli). The alpha release includes `NemicliPublish.exe` and a separate SHA-256 file. Models are not bundled; configure a cloud API key, an external local server or GGUF models using `/einrichten`.

**Option 3 — source code:** Use the repository's Python entry point `main.py`, dependencies in `requirements.txt`, and follow the setup instructions in the [German README](README.md). Command names in NemiCLI intentionally remain German.

---

## 🧠 Models

Use `/model` to choose a provider or local model. The built-in GGUF engine can run supported models directly; Ollama, LM Studio, Jan, llama.cpp and vLLM are supported as external backends. Model size, quantization, context length and concurrent image/voice/video components determine resource requirements.

### 🎬 Local video models

The documented video engine uses Wan 2.2 TI2V 5B and umT5 GGUF components. Video generation can be requested using `/video` or an appropriate model tool.

### ⚡ TensorRT-RTX

An optional performance path for supported NVIDIA RTX GPUs is documented in the [German README](README.md). Its compatibility and benefit depend on the GPU, model and installed runtime.

---

## ⌨ Commands

Type `/` – a menu with all commands appears (typing also searches the description). `/help`
shows all of them. Commands with subitems open **a selection without an argument** (↑/↓ or 1–9, Enter, Esc).
Or simply ask the AI – it knows all commands and opens suitable menus itself.

**Chat & models**

| Command | What it does |
|---|---|
| `/model [name\|huggingface]` | choose model, enter cloud key, fetch local model from Hugging Face |
| `/kontext [8k…128k\|max\|8bit\|16bit]` | context of the local model, with VRAM display |
| `/staerke [stufe]` | reasoning level/budget: `schnell` · `normal` · `stark` · `max` |
| `/auto [an\|aus]` | Auto Strong (Ollama): use the large Gemma for difficult questions |
| `/persönlichkeiten [name\|neu]` | who speaks? Select or create new (also `/persona`) |
| `/name [zeigen\|loeschen]` | 👤 About you: name, preferred form of address, what the AI should know |
| `/resume [#]` · `/reset` · `/clear` | resume chat · new chat · clear screen |
| `/subagenten [an\|auto\|off]` | helper agents: ask · as needed · blocked |

**Work**

| Command | What it does |
|---|---|
| `/modus [chat\|lesen\|normal\|auto\|autoan]` | work mode (also Shift+Tab) |
| `/code [aufgabe\|status]` · `/codeend` | 🟦 start coding assistant · end it |
| `/doku [laden python\|mdn]` | 📚 offline docs for the coding assistant |
| `/sandbox [freigeben <Ordner> [schreiben]\|entziehen <Ordner>]` | 🧪 sandbox: status, authorize/revoke project folder |
| `/uebung [an\|aus\|jetzt]` | practice mode: small programs while idle |
| `/skills [selbst an\|aus \| alte]` | 🧩 Skills: list · allow self-creation · old notes |
| `/ml` · `/ordner [pfad]` | folder sense: report · estimate folder type |

**Images & orb**

| Command | What it does |
|---|---|
| `/bild [prompt\|schritte]` | generate image (Krea 2, WebUI, or ComfyUI) · Krea steps 8–16 |
| `/bildmodel [name]` | choose image engine (`webui-adresse` changes the host) |
| `/charakter [oeffnen]` | 🧬 character file: fixed figure of the personality |
| `/kugel [malen …\|motive\|impuls an\|aus\|weg\|ordner]` | 🔮 floating orb: paint face and motifs, free moment |

**Memory**

| Command | What it does |
|---|---|
| `/gedaechtnis [indexieren]` | view/delete memories · catch up vector backlog |
| `/embeddings [<Ordner>\|huggingface]` | 🧬 embedding model for memory |
| `/wissen` | learned code snippets and notes |
| `/reflektieren [auto an\|aus]` | 🪞 draw lessons · automatically after corrections |
| `/aufraeumen` | compact memory |

**Security & system**

| Command | What it does |
|---|---|
| `/wache [status\|alarme\|fullscan\|…]` | 🛡 system watch: situation, alerts, rules, full scan, self-test |
| `/undo` · `/limit [zahl]` · `/whitelist [befehl]` | 🗑 recycle bin · 🛑 delete limit · ✅ command allowlist |
| `/schluessel <min> [aufgabe]` · `aus` | 🔑 open program folder to the AI for a limited time |
| `/protokoll [n]` | 📜 action log |
| `/web [wort\|key]` | web allowlist · configure Ollama search |
| `/start` | 📁 where your data folder is |
| `/einrichten` · `/update` | 🧰 install missing parts · ⬇ check venv (vulnerabilities via OSV.dev, updates) |
| `/systemcheck` · `/selbsttest` · `/version` | PC profile · check modules · version |

**Interfaces & extras**

| Command | What it does |
|---|---|
| `/gui` | desktop window (Chat · Work, Gallery, Skills, Personalities) |
| `/webui` | browser interface with reading aids |
| `/chrome [neu]` | Chrome extension: port and key |
| `/kontextmenue [an\|aus]` | NemiCLI in the Windows right-click menu |
| `/spiel` | chess · Nine Men's Morris · checkers · TicTacToe against the personality |
| `/theme` · `/emoji` · `/nemi` · `/statistik [reset]` | colors · emoji shortcuts · mascot · usage |
| `/help` · `/exit` | help · exit |

**Keys:** `Enter` send · `Strg+J` new line · `Esc` cancel · `F2` reasoning text ·
`F8` approve change · `F12` save entire conversation · `Shift+Tab` mode ·
`Bild ↑/↓`, mouse wheel scroll · `Strg+C` copy selected text.

**Without interface:** `--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` ·
`--auftrag <name>` (scheduled task without a window, answer becomes a report).

---


## 🔒 Security

- **Explicit approval:** Changes to files are displayed in a before/after review. **F8 approves; Enter does not.**
- **Risk-aware controls:** Deletion, unfamiliar commands and scheduled actions receive stricter confirmation.
- **System protection:** Protected system paths are read-only in the action layer; rollback, trash and backup features help recover from changes.
- **Skill and prompt-injection defenses:** External content is treated as untrusted, and tools/skills are subject to additional checks.
- **System and network watchdogs:** Detection rules, event monitoring, anomaly detection and shadow-mode safeguards. These **do not replace an antivirus product**.
- **API credentials:** Windows DPAPI is used to protect supported API keys. Do not commit real `.env` files, tokens, private data or local runtime logs.
- **Alpha warning:** Security features and the installer are still evolving and need independent testing. Report potential vulnerabilities according to [SECURITY.md](SECURITY.md).

---

## 📁 Folders

**The program** (`INSTALL`):

```
NemiCli/
├─ main.py                Chat loop, action logic, commands, startup
├─ start.bat · nemicli.cmd Starter
├─ core/                  Persona, commands, modes, config, keys, paths, coding assistant
├─ engines/               Chat engines, Krea 2, image describer, WebUI/ComfyUI, systemcheck
├─ ggufengine/            own GGUF engine (reader, tokenizer, kernel, models)
├─ tools/                 Tools, memory, sandbox, web, watch (tools/wache/)
├─ ui/                    Textual full screen, desktop window, terminal window, themes
├─ ModelGGUF/             local language models, one folder per model      (not in Git)
├─ Vision/                image describer, one folder per model            (not in Git)
├─ Models/embeddings/     embedding model for memory                       (not in Git)
├─ Agenten/               instructions the personality follows
├─ chrome-erweiterung/    Chrome extension (LIESMICH.md)
├─ installer/             MSI build
├─ docs/                  Functions, technology, model tests, plans, ideas
├─ build_exe.py · NemiCLI.spec · zertifikat.py
├─ nemicli.config.json · .env            settings · keys (encrypted)
├─ allowlist.json · befehl_whitelist.json · schreibsperre.json
└─ venv/ · .runtime/      working environment, isolated Python
```

**Your data** (`DATEN` – choose the location with `/start`; without a choice, next to the program):

```
<dein Ordner>/
├─ chats/ · Gespraeche/   conversations (/resume) · saved with F12
├─ learned/               memory (memory.db), statistics, action log
├─ Profile/<Name>/        per personality: Chats, images, Skills, memories, character file
├─ Persoenlichkeiten/     custom personalities (.md) and orb images
├─ Skills/ · Wissen/      Skills and knowledge for all personalities
├─ Bilder/                generated images, screenshots
├─ Models/Krea2/          files for Krea 2
├─ Wache/ · Berichte/     system watch · reports from watch and scheduler
├─ Zeitplan/ · Befehle/   tasks for scheduling · helper scripts
├─ NemiSandbox/           sandbox: run folder and packages
├─ Doku/ · Cache/         offline docs · prefix cache of the engine
├─ Papierkorb/            deleted and overwritten items (30 days, /undo)
└─ Vorschläge/            what the personality suggests on its own
```

If the data folder is unavailable at startup, **NemiCLI says so** – instead of
starting with empty memory.

---


## 🛠 Requirements

- **Operating system:** **Windows 11 tested**; Windows 10 not tested; Ubuntu support pending.
- **Permissions:** Designed to install and run as an ordinary Windows user, without administrator elevation.
- **Python:** **Python 3.14** tested. The executable bundles Python; `/einrichten` creates a matching environment.
- **Dependencies:** Refer to [requirements.txt](requirements.txt). Install the suitable torch build in the environment for CUDA support, or use the slower CPU variant.
- **Optional services:** Ollama, Forge WebUI, ComfyUI and Chrome for the browser extension.
- **Executable signature:** The publisher uses its own `CN=NemiCLI` certificate, not a publicly trusted commercial code-signing certificate. Windows or antivirus tools may display a warning. **Verify provenance and SHA-256 before running an unfamiliar executable.**
- **Krea 2:** Optimized for RTX 50-series NVFP4 hardware; roughly 20 GB of free RAM is recommended for its pipeline.

### 🖥 GPU memory (VRAM)

| Category | VRAM | Notes |
|---|---:|---|
| Minimum | 8 GB | Smaller models and limited functionality |
| Recommended | 16 GB | More headroom for models |
| Optimal | 24–32 GB | Larger models and contexts |

**8 GB is a minimum, not a guarantee.** Actual requirements depend on quantization, context size, RAM and concurrently loaded components.

### 🧠 System RAM

| Category | RAM | Notes |
|---|---:|---|
| Minimum | 32 GB | Most features possible; Krea 2 can be tight |
| Recommended | 64 GB | Image series, video and larger MoE models |

**16 GB RAM is not recommended for the complete NemiCLI environment.** The German README documents approximately 6–10 GB for Windows/background apps, 2–5 GB for NemiCLI with its memory model, and 20–27 GB for Krea 2 during generation (potentially more in series).

---

## 📚 Documentation

| File | Contents |
|---|---|
| [README.md](README.md) | Full German README |
| [docs/Funktionen.md](docs/Funktionen.md) | Detailed features (German) |
| [docs/TechnischeFunktion.md](docs/TechnischeFunktion.md) | Technical architecture and modules (German) |
| [docs/Modelltests.md](docs/Modelltests.md) | Local model test results (German) |
| [CHANGELOG.md](CHANGELOG.md) | Release and development history |
| [SECURITY.md](SECURITY.md) | How to report security vulnerabilities |
| [chrome-erweiterung/LIESMICH.md](chrome-erweiterung/LIESMICH.md) | Chrome extension setup (German) |
| `docs/Plan_*.md` and `docs/idee*.md` | Plans and ideas (German) |

---

## 📜 License

NemiCLI is distributed under the **GNU General Public License v3.0 (GPL-3.0)**. See the [LICENSE](LICENSE) file for the full license text and conditions.

---

## 🗺 Roadmap

**Completed according to the project documentation:** custom GGUF engine and vision, Windows installer, AppContainer sandbox, GUI, coding assistant, skill safeguards, backup/restore, character LoRA, music and animated portrait engines, model reasoning/review, reranked memory retrieval and browser-page checks.

**Planned:** a dedicated restricted Windows user for the agent, publicly trusted code signing and antivirus false-positive submission.

The project is in **alpha**. See [README.md](README.md) and [CHANGELOG.md](CHANGELOG.md) for the authoritative detailed status.
