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

[Deutsch](README.md) · [Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases)

</div>

---

## What is NemiCLI?

NemiCLI is a self-built **AI agent for the Windows terminal**, written in Python. It supports **37 cloud chat providers**, local models through its **own GGUF engine**, and external local servers such as Ollama, LM Studio, Jan, llama.cpp and vLLM.

It can read and write files, execute commands, research the web, assist with programming, understand and generate images, and monitor the PC. **Actions follow the selected work mode and its safety checks; permitted Auto-mode actions may run without individual prompts.** It includes configurable personalities, themes, a desktop window (`/gui`), and accessibility features for people with dyslexia.

The core is **pure Python — no Node.js, npm, llama.cpp, or transformers dependency for its own model engine**. Local language models, image understanding and memory use its GGUF engine; image generation uses Krea 2 and other dedicated image engines.

> 💜 Started from one question: "How does a coding agent actually work?" NemiCLI's birthday is June 19, 2026. Its predecessor, Nemi V1/V2, dates back to March 2026.

### 🔢 Version history

After 8.9, versions continued as 8.10–8.23; the project was renamed **9.23 Alpha** on October 26, 2026. Historical changelog entries retain their original numbers. **Current development source (`main`): NemiCLI 9.47 Alpha** as of November 7, 2026. The **latest published release is still NemiCLI 9.24 Alpha**. The source branch and the downloadable release are not the same version.

---

## ✨ Features at a glance

For the complete, continually maintained German specification, see [docs/Funktionen.md](docs/Funktionen.md).

| Area | Capabilities |
|---|---|
| 🧠 **Custom GGUF engine** | Local inference inside the Python process with torch; supports Gemma 4, Qwen3.5/3.6 and MoE variants, Qwen3-Next/VL, Llama 3/4, Mistral, Phi, Granite 4, Nemotron-H, Falcon-H1, LFM2, DeepSeek and others. Quantization down to IQ1; RAM-offloaded MoE experts, speculative decoding, prefix caching and context management up to 128k depending on resources. |
| 👁 **Vision** | Cloud, Ollama and the custom engine; an image describer can assist models that lack vision capabilities. |
| 🧭 **Reasoning and verification** | Per-personality `/denken` settings, task difficulty estimation, alternative solutions and a separate reviewer for complex answers; compares claimed results with actual tool output. |
| 🔊 **Voice** | Local Qwen3-TTS, personality-specific voices and read-aloud for text, Markdown and PDF. |
| ☁ **Cloud models** | **37 chat providers**, including Anthropic, OpenAI, Google, Mistral, DeepSeek, xAI, Groq, Kimi, GLM, Qwen, MiniMax, OpenRouter and Ollama Cloud. Live model lists, search and DPAPI-protected keys on Windows. |
| ⚙ **Agent tools** | File operations, PowerShell, web research, specialist sources, PDF, system information, schedules, helper agents and task lists. |
| 🟦 **Coding assistant** | `/code`: research, a task list, stepwise execution and verification, including linting and browser-page checks. Approved code can be collected for coding LoRA training. |
| 🧪 **Sandbox** | Experimental code executes within a Windows AppContainer, isolated from personal files. |
| 🛡 **System Watchdog** | Background security monitoring with detection rules, Sigma rule support, event logs, MITRE ATT&CK mappings, ML anomaly detection and shadow-mode learning. Designed to **complement, not replace, antivirus software**. |
| 🌐 **Network Watchdog** | Investigates connection information and firewall alerts, with ML-based connection and sequence analysis; initially in shadow mode. |
| 💾 **Memory** | Conversations, facts, lessons, skills, knowledge, hybrid keyword/vector retrieval, GGUF reranking, conversation compaction and memory archives. |
| 🎨 **Images** | Own Krea 2 pipeline, **Z-Image, FLUX.2 (4B/9B), Qwen-Image, FLUX.1 and Chroma**, plus Forge/ComfyUI and cloud providers. Supports character files, consistent identities, series, comics and LoRA. |
| 🎬 **Video** | Local Wan 2.2 GGUF pipeline **and LTX-2.5 with stereo audio**, plus configurable settings and cloud video integrations. |
| 🗣 **Animated portraits** | LivePortrait-based animated talking portraits with voice. |
| 🎭 **Personalities** | Gallery of nine predefined characters, editable traits, portraits and a stable character identity. |
| 🎵 **Music** | Local ACE-Step 1.5 GGUF-based music creation, songwriting and covers, with a dedicated `/musik` interface. |
| 🛍 **Appstore + NemiView** | HTML/CSS/JS apps rendered by NemiCLI's own engine, with templates and AI-assisted app debugging. |
| 🎭 **Interface** | Terminal tabs, Textual interface, `/gui` desktop app, web UI, floating desktop companion, Chrome extension and Windows right-click integration. |
| 🔒 **Approvals and recovery** | Before/after review with **F8 to approve**, risk levels, trash/undo, limits on deletion, protected system paths, skill checking, action logs, SHA-256 backups and daily automatic backups. |

Features listed here reflect the project's documentation and may be experimental in the alpha release.

---

## 🚀 Getting started

**Option 1 — `start.bat`:** Double-click the starter. It checks Python and the environment and offers to install missing packages. Then use `/einrichten` for an isolated Python environment, dependencies and the CUDA-enabled torch installation appropriate for your GPU.

**Option 2 — Windows setup EXE:** Download `NemicliPublish.exe` from the [releases](https://github.com/Overbid4592/Nemicli-Terminal/releases), along with `NemicliPublish.sha256.txt` to verify the download. The currently published alpha asset is an `.exe`, not an `.msi` file. Models are not bundled; configure a cloud API key, an external local server or GGUF models using `/einrichten`.

**Option 3 — source code:** Create a virtual environment, install dependencies from `requirements.txt`, then run `main.py`. The tested Python target is 3.14; an appropriate torch build is installed separately for GPU support. The command names intentionally remain German.

**Option 4 — build your own installer:** `python build_exe.py` creates `dist/NemiCLI/` with `NemiCLI.exe`, `NemiCLIc.exe` and `NemiCLIT2/`, plus `NemiCLI-Setup-<Build>.exe` and a SHA-256 checksum. `python build_exe.py --msi` additionally creates an MSI. The optional in-house `CN=NemiCLI` signature is not commercially trusted.

**Data folder:** Use `/start` to select where chat history, generated images and learned information are stored. A folder change copies data instead of deleting it. If the selected folder becomes inaccessible, NemiCLI reports it rather than silently starting with empty memory.

---

## 🧠 Models

`/model` opens the model selector automatically if no model has been configured.

| Component | Location | Setup and notes |
|---|---|---|
| Local GGUF language model | `ModelGGUF/<Name>/` in the program folder | `/model huggingface` offers compatible models of 4B or more with resumable, SHA-256-checked downloads. A matching `mmproj-*.gguf` may add vision support. |
| Image describer | `Vision/<Name>/` in the program folder | Vision GGUF + mmproj; choose with `/vision`. |
| Local voice | `Vision/<Name>/` in the program folder | Qwen3-TTS Talker/VoiceDesign and 12 Hz GGUF tokenizer. |
| Memory embeddings | `Models/embeddings/<Name>/` | Choose with `/embeddings`; uses RAM and GPU as resources permit. |
| Krea 2 | `Models/Krea2/` in the data folder | Model, `text_encoders/`, `vae/`, `tokenizer/`; select via `/bildmodel`. |
| Z-Image, FLUX.2, Qwen-Image, FLUX.1, Chroma | `Models/Bildmodelle/` (data or program folder) | Model-specific safetensors or GGUF and shared text encoders, tokenizers and VAEs as required. |
| TensorRT-RTX | `Models/TensorRT-RTX/` in the program folder | Optional separately downloaded NVIDIA SDK (see below). |
| LTX-2.5 video and audio | `Models/Videomodelle/` (data or program folder) | Each model needs its transformer, projected Gemma 4 text encoder and video/audio VAE components. Select using `/video modell`. |
| Wan 2.2 GGUF video | `VideoGen/` in the program folder | GGUF model and VAE in each model folder, plus shared umT5 encoder/tokenizer; manifest below. |
| ACE-Step 1.5 music | `Models/Musik/` or `Models/Music/` | GGUF audio engine, Qwen3 embedding reader, VAE and optional song planner. |
| LivePortrait | `Models/LivePortrait/` | Five network weight files plus `landmark.onnx`; documented SHA-256 validation. |
| Cloud models | Provider keys | Add through `/model`, with DPAPI protection on supported Windows installations. |
| Ollama | Separate local server | Detected when running. |
| LM Studio, Jan, llama.cpp/LocalAI, vLLM | Separate servers | Typical ports 1234, 1337, 8080 and 8000. |

NemiCLI writes a `LIES-MICH.txt` note in model folders with instructions about required files.

**🪑 Fallback bench (`/model ersatz`):** Up to two cloud models can take over after repeated blocked actions. If an active model hits a protected-action block five times in 30 minutes, the current turn is stopped and the first fallback is activated in the terminal, orb or watchdog. Read-only mode, orb restrictions and deletion limits do not contribute to this count.

### 🎬 Local GGUF video model file manifest

Download the files separately and compare their sizes and SHA-256 hashes. The following paths, filenames and hashes are taken from the German README. The encoder, tokenizer and optional mini-VAE can live in shared folders or model-specific folders.

```
VideoGen/
├─ WAN-Turbo/                        fast: four steps
│  ├─ Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf
│  └─ Wan2.2_VAE.safetensors
├─ WAN-GGUF/                         standard: 30 steps
│  ├─ Wan2.2-TI2V-5B-Q4_K_M.gguf
│  └─ Wan2.2_VAE.safetensors
├─ WAN-FunInP/                       start/end keyframes
│  ├─ Wan2.2-Fun-5B-InP-Q8_0.gguf
│  └─ Wan2.2_VAE.safetensors
├─ text_encoders/umt5-xxl-encoder-Q8_0.gguf
├─ tokenizer/                        tokenizer.json, spiece.model, special_tokens_map.json, tokenizer_config.json
└─ tae/                              optional mini-VAE (faster decoding)
   ├─ taew2_2_super.safetensors
   └─ taew2_2.safetensors
```

| File | Source | Bytes | SHA-256 |
|---|---|---|---|
| `Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf` | [hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF](https://huggingface.co/hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF) | 3.437.927.136 | `13b3bee8fcafd9f9a3778ed1a0ca973f4a7d152094ea305f8384b0abd7e1ad51` |
| `Wan2.2-TI2V-5B-Q4_K_M.gguf` | [QuantStack/Wan2.2-TI2V-5B-GGUF](https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF) | 3.433.116.000 | `95b19697b7f98e65b0a543640e9ca7b4dfec32e2a6e3731e8e10708be52655e2` |
| `Wan2.2-Fun-5B-InP-Q8_0.gguf` | [QuantStack/Wan2.2-Fun-5B-InP-GGUF](https://huggingface.co/QuantStack/Wan2.2-Fun-5B-InP-GGUF) | 5.407.546.080 | `c88969a140da94176d8af263d031d03b83a7b4783108c73e828a69b9a8873d7f` |
| `Wan2.2_VAE.safetensors` | [QuantStack/Wan2.2-Fun-5B-InP-GGUF](https://huggingface.co/QuantStack/Wan2.2-Fun-5B-InP-GGUF) (`vae/`) | 1.409.400.960 | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |
| `umt5-xxl-encoder-Q8_0.gguf` | [city96/umt5-xxl-encoder-gguf](https://huggingface.co/city96/umt5-xxl-encoder-gguf) | 6.043.068.256 | `2521d4de0bf9e1cc6549866463ceae85e4ec3239bc6063f7488810be39033bbc` |
| `tokenizer.json` | [alibaba-pai/Wan2.2-Fun-5B-InP](https://huggingface.co/alibaba-pai/Wan2.2-Fun-5B-InP) (`google/umt5-xxl/`) | 16.837.417 | `6e197b4d3dbd71da14b4eb255f4fa91c9c1f2068b20a2de2472967ca3d22602b` |
| `spiece.model` | same source | 4.548.313 | `e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458` |
| `special_tokens_map.json`, `tokenizer_config.json` | same source | small | – |
| `taew2_2_super.safetensors` | [madebyollin/taehv](https://github.com/madebyollin/taehv/tree/main/safetensors) (MIT) | 40.986.488 | `cf6d8bdadb77d9f9741ae28b5b4659bb74cf9b0be6d08fc4afed3cb50c39364c` |
| `taew2_2.safetensors` | same source | 22.848.048 | `b84609b2a133d48434bd9636bfcb44bf05168dc436e2d3cecf26256faa1f5325` |
The video models listed in this manifest are documented as Apache-2.0 licensed; the optional mini-VAEs are MIT-licensed. Without `tae/`, the larger VAE handles decoding more slowly. **These checksums are copied from the German source, not independently rechecked against downloaded model files.**

### ⚡ Optional TensorRT-RTX acceleration

NVIDIA TensorRT for RTX is **not bundled with NemiCLI**. Download the NVIDIA-licensed SDK separately from [NVIDIA](https://developer.nvidia.com/tensorrt-rtx) and place the extracted folder under `Models/TensorRT-RTX/`. NemiCLI installs the corresponding local package into its own venv if needed. `/systemcheck` reports the status.

| Area | Optional role | Setting |
|---|---|---|
| Local chat | FP4 prompt prefill on compatible Blackwell hardware and non-MoE model architectures. The built-in engine still performs decoding. | `trt_chat` |
| Krea 2 | NVFP4 image-transformer layers. | `bild_trt` |
| Wan 2.2 | DiT transformer layers; video with character LoRA stays on torch. | `video_trt` |

Compiled engines are cached under `Models/TensorRT-RTX/engines/` by model and GPU. Actual compatibility and performance depend on the selected hardware and software.

---

## ⌨ Commands

Commands intentionally remain **German**, even in the English documentation. Type `/` to search commands, or `/help` for full descriptions. Submenus can open when a command has no arguments.

**Chat & models**

| Command | Description |
|---|---|
| `/help` | Show all available commands. |
| `/model [name\|huggingface\|ersatz]` | Select providers and models, download GGUF models, or set cloud fallbacks. |
| `/staerke [stufe]` | Choose reasoning strength or budget. |
| `/denken [auto\|an\|aus]` | Configure how each personality reasons. |
| `/auto [an\|aus]` | Switch to a larger Ollama model for difficult questions. |
| `/kontext [anzeigen\|8k…128k\|max]` | Inspect context usage or choose a GGUF context length. The old 8bit and 16bit arguments are not valid. |
| `/compact [jetzt\|zeigen]` | Compact a conversation or inspect its saved summary where supported. |
| `/persönlichkeiten [name\|neu]` | Choose, create or export personalities. |
| `/name [zeigen\|loeschen]` | Manage personal name and preferred address. |
| `/reset` | Start a new conversation. |
| `/clear` | Clear the display. |
| `/subagenten [an\|auto\|off]` | Configure helper agents. |

**Work & development**

| Command | Description |
|---|---|
| `/modus [chat\|lesen\|normal\|auto\|autoan]` | Choose work mode; Shift+Tab cycles modes. |
| `/code [aufgabe\|status]` | Start or inspect the coding assistant. |
| `/codeend` | End the coding workflow. |
| `/doku [laden python\|mdn]` | Load offline programming documentation. |
| `/sandbox [freigeben <Ordner> [schreiben]\|entziehen <Ordner>]` | Grant or revoke a sandbox project folder. |
| `/uebung [an\|aus\|jetzt]` | Configure sandboxed coding practice. |
| `/skills [selbst an\|aus\|alte]` | Inspect and manage skills. |
| `/ml` | Show folder-sense information. |
| `/ordner [pfad]` | Inspect a folder and its type. |
| `/anzeige [kompakt\|rahmen]` | Choose compact or framed tool output. |

**Images, video, music & voice**

| Command | Description |
|---|---|
| `/bild [prompt\|schritte]` | Create an image or set Krea 2 steps. |
| `/bildmodel [name]` | Choose Krea 2, Z-Image, FLUX.2, Qwen-Image, FLUX.1, Chroma, external or cloud engines. |
| `/video <beschreibung> [options]` | Create a video with a local motor, optional LTX-2.5 audio or a configured cloud provider. |
| `/charaktere` | Open the character gallery to create or improve adult characters. |
| `/charakter [oeffnen]` | Open the active character definition. |
| `/musik` | Open song planning, composing and cover creation. |
| `/kugel [options]` | Configure the floating orb, appearance, activity and memories. |
| `/voice [an\|knopf\|aus\|jetzt\|text]` | Control local speech and read-aloud. |

**Memory**

| Command | Description |
|---|---|
| `/gedaechtnis [indexieren\|archiv\|zurueck]` | Inspect and index memories, view the archive and restore entries. |
| `/embeddings [Ordner\|huggingface]` | Choose an embedding model or download it. |
| `/embeddingtimer` | Inspect embedding scheduler activity. |
| `/wissen` | Show learned knowledge and snippets. |
| `/reflektieren [auto an\|aus]` | Extract lessons or enable automatic reflection. |
| `/aufraeumen` | Consolidate memories. |

**Security & maintenance**

| Command | Description |
|---|---|
| `/wache [status\|alarme\|fullscan\|dl\|netz\|…]` | Inspect watchdog events, network checks and scans. |
| `/backup [teil\|voll\|auto\|pruefen\|auspacken]` | Create, inspect and restore backups. |
| `/undo` | Restore deleted or overwritten files where possible. |
| `/limit [zahl]` | Set the deletion limit. |
| `/whitelist [befehl]` | Inspect the command allowlist. |
| `/schluessel <min> [aufgabe]` | Temporarily authorize program-folder access. |
| `/protokoll [n]` | Inspect action logs. |
| `/vision [ordner\|restart\|debug\|protokoll]` | Configure and troubleshoot the image describer. |
| `/web [wort\|key\|fremdseiten frei\|fragen\|aus]` | Manage web allowlist and unknown-site permissions. |
| `/start` | Choose the data folder. |
| `/einrichten [modelle]` | Run setup and download optional model components. |
| `/update` | Inspect dependency vulnerabilities and updates. |
| `/venv` | Open the venv and torch instructions. |
| `/systemcheck` | Inspect system specifications. |
| `/selbsttest` | Run internal checks. |
| `/version` | Display version, build and runtime information. |
| `/debug [aus\|an\|motor an\|aus]` | Inspect diagnostics, crashes and resource reports. |
| `/restart` | Restart the program and watchdog using direct user input. |

**Interfaces & extras**

| Command | Description |
|---|---|
| `/gui` | Open the desktop GUI. |
| `/webui` | Open browser interface with reading aids. |
| `/chrome [neu]` | Configure Chrome extension. |
| `/kontextmenue [an\|aus]` | Toggle the Windows context-menu entry. |
| `/spiel` | Play built-in games. |
| `/appstore [app\|debug\|liste]` | Use and debug NemiView apps. |
| `/theme` | Change theme. |
| `/emoji` | Configure emoji shortcuts and level. |
| `/nemi` | Interact with mascot. |
| `/statistik [reset]` | Inspect or reset statistics. |
| `/exit` | Exit NemiCLI. |

**Keys:** `Enter` send · `Ctrl+J` newline · `Esc` cancel · `F2` reasoning text · **`F8` approve** · `F12` save conversation · `Shift+Tab` work mode · `Ctrl+K` search · `F3` cockpit · `PgUp/PgDn` scroll · `Ctrl+C` copy.

**In the tabbed terminal:** `Ctrl+Shift+T` opens a tab · `Ctrl+Shift+W` closes it · `Ctrl+PgUp/PgDn` switches tabs · `Ctrl+Shift+K` opens the AI-action tab.

**Without GUI:** `--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` · `--auftrag <name>` (read-only scheduled task with report).

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

**Source repository and program files** (checked against the current repository root):

```text
Nemicli-Terminal/
├─ main.py                 Main entry point; loads the three parts below
├─ main_dienste.py         Services, learning, integrations and setup
├─ main_befehle.py         Downloads, updates, input and slash commands
├─ main_start.py           More commands, watchdog service and startup
├─ start.bat               Windows starter
├─ nemicli.cmd             Command-line launcher
├─ core/                   Core application modules
├─ engines/                Model, image and other engines
├─ ggufengine/             Built-in GGUF engine
├─ loramotor/              LoRA engine
├─ trtmotor/               TensorRT-related engine
├─ nemiview/               NemiView interface/rendering engine
├─ tools/                  Tools and system watchdog (tools/wache/)
├─ ui/                     Terminal and desktop interfaces
├─ einrichtungshelfer/     Setup assistant and bundled helper model files
├─ appstore_vorlagen/      Appstore templates
├─ chrome-erweiterung/     Chrome extension
├─ installer/              Installer build sources (not a download format)
├─ docs/                   Documentation, plans and ideas
├─ .github/                GitHub workflows and templates
├─ build_exe.py            Executable build helper
├─ NemiCLI.spec            PyInstaller specification
├─ zertifikat.py           Certificate-related utility
├─ allowlist.json          Allowed resources/configuration
├─ befehl_whitelist.json   Command whitelist
├─ schreibsperre.example.json   Example write-protection config
├─ .env.example            Example environment file; no real credentials
└─ requirements.txt        Python dependencies
```

The four `main*.py` files form **one application**: `main.py` loads `main_dienste.py`, `main_befehle.py`, and `main_start.py` into its shared namespace. They are not four independent programs.

**Local or generated folders** (not tracked in the Git repository; availability depends on installation and usage):

```text
NemiCli/ or your configured data directory/
├─ ModelGGUF/             Downloaded local language models
├─ Vision/                Downloaded image describer models
├─ Models/embeddings/     Embedding models
├─ Agenten/               Agent/personality instructions (created at runtime if used)
├─ venv/                  Python virtual environment
├─ .runtime/              Isolated Python/runtime files
├─ .env                   Local credentials (never commit)
├─ nemicli.config.json    Local settings
└─ schreibsperre.json      Local write-protection settings, if configured
```

**Your data** (`DATEN` — choose the location with `/start`; without a choice, next to the program):

```text
<your folder>/
├─ chats/ · Gespraeche/   Conversations and saved transcripts
├─ learned/               Memory, statistics, action logs
├─ Profile/<Name>/        Per-personality chats, images, skills and memory
├─ Persoenlichkeiten/     Custom personality definitions and orb images
├─ Skills/ · Wissen/      Shared skills and knowledge
├─ Bilder/                Generated images and screenshots
├─ Models/Krea2/          Krea 2 model files
├─ Wache/ · Berichte/     Watchdog data and reports
├─ Zeitplan/ · Befehle/   Scheduled tasks and helper scripts
├─ NemiSandbox/           Sandbox files and packages
├─ Doku/ · Cache/         Offline docs and engine caches
├─ Papierkorb/            Deleted/overwritten files (undo)
└─ Vorschläge/            Proactive personality suggestions
```

These data paths describe the intended runtime layout; individual directories may only appear when their feature is used. If the selected data folder is unavailable at startup, NemiCLI reports it rather than silently starting with empty memory.

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
