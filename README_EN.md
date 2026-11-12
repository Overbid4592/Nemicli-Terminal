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

**Developed by D. Hoffmann (Vibecoder) · built with Claude Opus 5.5 (Anthropic) · Ideas & planning: Roxie – ChatGPT (OpenAI)**

[Deutsch](README.md) · [Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases)

</div>

---

## What is NemiCLI?

NemiCLI is a self-built **AI agent for the Windows terminal**, written in Python. It supports **37 cloud chat providers**, local models through its **own GGUF engine**, and external local servers such as Ollama, LM Studio, Jan, llama.cpp and vLLM.

It can read and write files, execute commands, research the web, assist with programming, understand and generate images, and monitor the PC. **Actions follow the selected work mode and its safety checks; permitted Auto-mode actions may run without individual prompts.** It includes configurable personalities, themes, a desktop window (`/gui`), and accessibility features for people with dyslexia.

The core is **pure Python — no Node.js, npm, llama.cpp, or transformers dependency for its own model engine**. Local language models, image understanding and memory use its GGUF engine; image generation uses Krea 2 and other dedicated image engines.

> 💜 Started from one question: "How does a coding agent actually work?" NemiCLI's birthday is June 19, 2026. Its predecessor, Nemi V1/V2, dates back to March 2026.

### 🔢 Current version: 9.64 Alpha — and a numbering slip

After 8.9, development continued as 8.10 through 8.23. The major version digit was not incremented, even though fourteen substantial feature packages had been added. This was corrected on October 26, 2026, when that development line became **9.23 Alpha**. Build counts and installer version comparisons were unaffected; historical versions remain as originally recorded in [CHANGELOG.md](CHANGELOG.md).

**Current development source (`main`): NemiCLI 9.64 Alpha (November 12, 2026).** Published installers may have a different version; check [GitHub Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) for actual downloads and checksums.

---

## ✨ Features at a glance

For the detailed German feature specification, see [docs/Funktionen.md](docs/Funktionen.md).

| Area | Capabilities |
|---|---|
| 🧠 **Custom GGUF engine** | Local Python/torch inference with an optional pinned llama.cpp reserve (`/llamacpp`); Gemma 4, Qwen3.5/3.6 (MoE), Qwen3-Next/VL, Llama 3/4, Mistral/Ministral, Phi, Granite 4, Nemotron-H, Falcon-H1, LFM2/LFM3, DeepSeek, Kimi Linear, Ling 3.0 and more. Supports IQ1, ternary/1-bit types (TQ1_0, TQ2_0, Q1_0, Q2_0, PQ2_0, PTQ1_0), RAM-managed MoE experts, n-gram/MTP speculation, prefix caching, context compaction and VRAM-aware KV-cache precision. |
| 👁 **Vision** | Native multimodal models via cloud, Ollama or the GGUF engine, assisted by a separate image describer for non-vision models. The personality tracks which images have already been described. |
| 🔎 **Situational awareness** | Informs the model about changed files/folders, model and personality switches or long pauses; checks claimed successes against actual tool results before reporting completion. |
| 🧭 **Reasoning and verification** | Automatically assesses difficulty, compares approaches and optionally uses a separate reviewer on complex tasks; user instructions and per-personality settings take precedence. |
| 🔊 **Voice** | Local Qwen3-TTS, custom personality voices, read-aloud for text/Markdown/PDF and speech in the desktop orb. |
| ☁ **Cloud models** | 37 providers with live model lists, search and Windows DPAPI-protected keys, including Anthropic, OpenAI, Google, Mistral, DeepSeek, xAI, Groq, Moonshot, Z.ai, Alibaba, MiniMax, OpenRouter and Ollama Cloud. |
| ⚙ **Agent tools & AI alarm clock** | Files, PowerShell, research with specialist sources, PDF, system information, scheduled tasks and helper agents; one-off and **recurring reminders set by the personality for itself** (for example, checking again in two days). |
| 🟦 **Coding assistant** | `/code` runs researched, stepwise tasks with to-do tracking, real lint/browser checks, offline docs and a source collection for approved Coding-LoRA training. |
| 🧪 **Sandbox** | Windows AppContainer for experimental code, intended to keep personal files separate. |
| 🛡 **System Watchdog** | 17 rules with MITRE ATT&CK mapping, Sigma, Windows event logs, Isolation Forest, GRU process-sequence model, safeguards against poisoning through pending alerts, and learning from activity over a full week. Complements — does **not** replace — antivirus. |
| 🌐 **Network Watchdog** | Address/owner inspection and machine-learning checks of network events and sequences; initially in shadow mode. |
| 💾 **Memory** | Chat histories, facts, lessons, skills, hybrid keyword/vector retrieval, optional GGUF reranking, contextual excerpts, learned rankings, an ingestion buffer (`/einlesen`), archives and automatic conversation compaction. |
| 🎨 **Images & LoRA workshop** | Krea 2, Z-Image, FLUX.2, Qwen-Image, FLUX.1 and Chroma; character files, image series and comics. **`/lora`** trains a character image LoRA from reviewed images with progress/pause/test images; SHA-256 associates it with a character. Optional Forge/ComfyUI and cloud image engines. |
| 🎬 **Video** | New **SD-Video** turns SD 1.5/SDXL image checkpoints into silent 8 fps clips (AnimateDiff/Hotshot, up to 20 s); **LTX-2.5** produces video with 48 kHz stereo audio, automatic resolution and tiled VAE; **Wan 2.2 GGUF** handles 24 fps clips with start images and character consistency. Cloud video jobs can run while chat continues; daily image recaps with voice are documented. |
| 🗣 **Talking portraits** | Local LivePortrait animations with speech, facial expressions and clips up to 60 seconds. |
| 🎭 **Character gallery** | Nine presets or user-created adult characters, AI-assisted portrait refinement, persistent image seeds and a **user-controlled appearance core** (`/kern`). |
| 🎵 **Music** | ACE-Step 1.5 GGUF songs, lyrics, a song planner, stereo output and cover creation in `/musik`. |
| 🛍 **Appstore & NemiView** | Own HTML/CSS/JavaScript engine, templates, AI-built apps and in-app debugging. |
| 🎭 **Interface** | Tabbed custom terminal, live AI-action tab, search, split view, collapsible responses, link support, themes; Textual UI, `/gui`, web UI, floating desktop orb, Chrome extension and Explorer integration. |
| 🔒 **Safety & recovery** | F8 before/after approval, protected paths, untrusted-content handling, skill checks, deletion limits, trash/undo, action logs, ZIP/SHA-256 backups and scheduled automatic backups. |

This describes the **alpha source** and may include experimental features not present in older release downloads.

---

## 🚀 Getting started

**Option 1 — `start.bat`:** Double-click the Windows starter. It checks Python, venv and missing dependencies. Run `/einrichten` to create an isolated Python environment under `.runtime/` without administrator rights and open the venv terminal (`/venv`) for the recommended GPU-compatible torch command. `/einrichten modelle` also offers optional memory, reranker, image describer, voice, music, video and portrait components from official sources, showing license, size and SHA-256 verification.

**Option 2 — manual source installation:**
~~~bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
~~~
Python 3.14 is the tested target; CUDA-enabled torch must be installed separately for GPU operation.

**Option 3 — build the EXEs:** `venv\Scripts\python build_exe.py` produces `dist/NemiCLI/` with `NemiCLI.exe` (custom terminal), `NemiCLIc.exe` (console) and `NemiCLIT2/`. It uses a folder layout instead of extracting a one-file EXE on each launch. A `SHA256SUMS.txt` is generated, and EXEs may be signed with the project's own `CN=NemiCLI` certificate (`python zertifikat.py`).

**Option 4 — Windows installer:** The same build creates `dist/NemiCLI-Setup-<Build>.exe` plus SHA-256 checksum. It can find existing installations, replace program components without deleting personal data, and create a desktop shortcut. `build_exe.py --msi` optionally creates `NemiCLI-<Build>.msi`. See [Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) for the installer format actually published; a self-issued signature does not guarantee publisher trust.

**Data folder:** Choose where chats, images and learned data live on first start. `/start` opens the chooser later. Moving the folder copies data instead of deleting them; an inaccessible folder is reported rather than silently replaced with empty memory.

**Start from anywhere:** Add your installation folder to the Windows user PATH, then launch `nemicli` or `nemicli --sag "…"` to send a first message.

---

## 🧠 Models

`/model` opens the model selector automatically if no model has been configured.

| Component | Location | Setup and notes |
|---|---|---|
| Local GGUF language model | `ModelGGUF/<Name>/` in the program folder | `/model huggingface` offers compatible models of 4B or more with resumable, SHA-256-checked downloads. A matching `mmproj-*.gguf` may add vision support. |
| Image describer | `Vision/<Name>/` in the program folder | Vision GGUF + mmproj; choose with `/vision`. |
| Local voice | `Vision/<Name>/` in the program folder | Qwen3-TTS Talker/VoiceDesign and 12 Hz GGUF tokenizer. |
| Memory embeddings | `Models/embeddings/<Name>/` in the program folder | Choose with `/embeddings`; GGUF inference can use RAM or GPU as resources permit. |
| Krea 2 | `Models/Krea2/` in the data folder | Model, `text_encoders/`, `vae/`, `tokenizer/`; select via `/bildmodel`. |
| Z-Image, FLUX.2, Qwen-Image, FLUX.1, Chroma | `Models/Bildmodelle/` (data or program folder) | Model-specific safetensors or GGUF and shared text encoders, tokenizers and VAEs as required. |
| TensorRT-RTX | `Models/TensorRT-RTX/` in the program folder | Optional separately downloaded NVIDIA SDK (see below). |
| **SD-Video (SD 1.5 / SDXL)** | `Models/Videomodelle/` (data or program folder) | Complete image checkpoint plus AnimateDiff/Hotshot motion module (model folder or shared `bewegung/`), plus CLIP tokenizer files in `tokenizer_clip/`. Use `/video modell`. |
| LTX-2.5 video and audio | `Models/Videomodelle/` (data or program folder) | Each model needs its transformer (GGUF or safetensors), projected Gemma 4 text encoder and video/audio VAE components. Select using `/video modell` and `/video motorordner`. |
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

### 🎬 SD-Video: installing motion modules

SD-Video requires a complete Stable Diffusion **SD 1.5 or SDXL checkpoint**, a compatible **motion module** and the **CLIP tokenizer**. Download the files separately, then use this directory structure:

~~~text
Models/Videomodelle/
├─ MyModel/                        arbitrary model folder
│  └─ <checkpoint>.safetensors     complete image checkpoint
├─ bewegung/                       shared motion modules (or put them in the model folder)
└─ tokenizer_clip/                 vocab.json and merges.txt
~~~

| Target | File | Source | Bytes | SHA-256 |
|---|---|---|---|---|
| SD 1.5, 4 steps (fast) | `animatediff_lightning_4step_comfyui.safetensors` | [ByteDance/AnimateDiff-Lightning](https://huggingface.co/ByteDance/AnimateDiff-Lightning) | 908.929.664 | `aeb66ae8ff4a868d31379c3bde3e5e7e510a4d4b565a06ee4f1297e93a561dc5` |
| SD 1.5, 8 steps | `animatediff_lightning_8step_comfyui.safetensors` | same source | 908.929.664 | `5173ea8209053dd5de9b973baefac1801fbe7a433d07fa0122664121751885cf` |
| SD 1.5, 25 steps | `v3_sd15_mm.ckpt` | [guoyww/animatediff](https://huggingface.co/guoyww/animatediff) | 1.673.262.583 | `2412711886f61091846f53204aabc38aa6e09356d62a9808abe4daa802168343` |
| SDXL, 8 frames per window | `hsxl_temporal_layers.f16.safetensors` | [hotshotco/Hotshot-XL](https://huggingface.co/hotshotco/Hotshot-XL) | 474.694.696 | `b46ce6b4ec297b21e508b8888135f65b5a07aa76d755cc086dc5ee804064bb1c` |
| SDXL, 16 frames per window (beta) | `mm_sdxl_v10_beta.ckpt` | [guoyww/animatediff](https://huggingface.co/guoyww/animatediff) | 950.143.538 | `fa4950a062e892fca50d4c441fcd6130d1ad68a621a0404d155be17580072978` |
| alle | `vocab.json`, `merges.txt` | [openai/clip-vit-large-patch14](https://huggingface.co/openai/clip-vit-large-patch14) | small | – |

Motion-module licenses: AnimateDiff Apache 2.0, AnimateDiff-Lightning CreativeML OpenRAIL-M, Hotshot-XL OpenRAIL++. Official motion modules are checked using SHA-256 before first use; checksum-matching `.ckpt` files are read as weights and converted to `.safetensors`. The chosen image checkpoint has no fixed checksum in this manifest. These values are copied from the German README and were not independently checked against downloads.


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
| `/video <beschreibung> [--sekunden 10] [--start image.png] [--figur]` | Generate video using local SD-Video, Wan or LTX-2.5 engines, or a cloud provider; can use a starting image or character. |
| `/video modell [name]` · `/video einstellungen [name wert]` | Select a local video model; configure steps, frame rate, resolution, audio and automatic GPU-aware options. |
| `/charaktere` | Open the character gallery to create or improve adult characters. |
| `/charakter [oeffnen]` | Open the active character definition. |
| `/kern [aendern\|sperren\|entsperren\|zurueck]` | Manage the fixed appearance core of a character. Only the user can approve protected core changes. |
| `/lora` | Character LoRA workshop: review images, train with progress/pause, view test images and bind to a character. |
| `/musik` | Open song planning, composing and cover creation. |
| `/kugel [options]` | Configure the floating orb, appearance, activity and memories. |
| `/voice [an\|knopf\|aus\|jetzt\|text]` | Control local speech and read-aloud. |

**Memory**

| Command | Description |
|---|---|
| `/gedaechtnis [indexieren\|archiv\|zurueck]` | Inspect and index memories, view the archive and restore entries. |
| `/einlesen [stand]` | Process the memory ingestion buffer manually or inspect its status; automatic handling at about 99k. |
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
| `/terminal [leuchten\|weich\|geteilt an\|aus\|thema auto\|breeze\|mint]` | Set custom terminal glow, smooth scrolling, split view and visual theme. |
| `/theme` | Change theme. |
| `/emoji` | Configure emoji shortcuts and level. |
| `/nemi` | Interact with mascot. |
| `/statistik [reset]` | Inspect or reset statistics. |
| `/exit` | Exit NemiCLI. |

**Keys:** `Enter` send · `Ctrl+J` newline · `Esc` cancel · `F2` reasoning text · **`F8` approve** · `F12` save conversation · `Shift+Tab` work mode · `Ctrl+K` global search · `Ctrl+F` search history · `Alt+Up/Down` jump between answers · `F3` cockpit · `PgUp/PgDn` scroll · `Ctrl+C` copy.

**In the tabbed terminal:** `Ctrl+Shift+T` opens a tab · `Ctrl+Shift+W` closes it · `Ctrl+PgUp/PgDn` switches tabs · `Ctrl+Shift+K` opens the AI-action tab · `Ctrl+Shift+G` docks AI actions beside chat · `Ctrl+Click` opens supported HTTPS links and safe files.

**Without GUI:** `--version` · `--selftest` · `--systemcheck` · `--bild "a fox"` · `--sag "…"` · `--auftrag <name>` (read-only scheduled task with report).

---

## 🔒 Security

NemiCLI implements several safeguards, but these are **documented protections, not a guarantee that alpha software is secure**.

- **Explicit review and work modes:** A before/after view can show file modifications. **F8 approves; Enter does not.** Higher-risk actions have stricter checks. Auto modes can perform specifically permitted operations without a new prompt; this is not the same as unconditional approval for every action.
- **Protected system paths:** Windows system folders (such as `C:\Windows`, `Program Files` and `ProgramData`) and sensitive registry entries are intended to be read-only for the agent. Checks normalize path traversal, environment variables and device paths.
- **Custom write protection:** `schreibsperre.json` can forbid access to user-selected locations. The program folder is protected against unauthorized agent edits.
- **No agent privilege escalation:** Command guards reject elevation and restrict risky Windows utilities. System inspection can use direct APIs rather than unrestricted PowerShell.
- **Antivirus remains separate:** The System Watchdog complements security software; it must not disable or interfere with antivirus protection.
- **Trash, undo and deletion limit:** Deleted files may move to an application trash folder and overwritten files can be backed up. `/undo` can recover retained versions. The documented per-session deletion limit is 20 files.
- **Web retrieval:** Direct fetches use HTTPS and an allowlist with SSRF checks across redirects. Other sites can be fetched through a configured external service with additional controls.
- **External data is not an instruction:** Web text, downloaded files, images and memory content are treated as untrusted. Suspicious instructions, hidden text and framing tricks receive additional checks. Reading external material can cause subsequent file, command or memory actions to require review, even in Auto mode.
- **Command and path filters:** Safeguards examine resolved paths and common PowerShell indirection. **A textual filter is not a complete security boundary.** A dedicated restricted Windows account is still planned.
- **Sensitive control files:** Changes to personality instructions, scheduled tasks, watchdog state and learned files receive additional review. Untrusted content is framed with randomized markers to resist forged boundaries; skill memories use the same filters as other memories.
- **Browser extension:** Right-click explanations run without tool access. Hidden page content is not forwarded. Browser content is treated as untrusted, and the bridge checks the host and restricts replies to its configured Chrome extension identity.
- **Watchdog alarm handling:** Alarms are framed as measurement data rather than executable commands. Some important rules can only be disabled by the user.
- **Terminal links:** Only `Ctrl+Click` opens a link after displaying its target; HTTPS and safe files can open, but executables, scripts, shortcuts and network shares are not launched.
- **Watchdog isolation:** Heavy content/file search stays in the terminal instead of running in the watchdog background process.
- **Local services:** Interfaces designed for local access bind to `127.0.0.1` and use authentication tokens or keys.
- **Scheduled background jobs:** Run in Read Only mode.
- **API credentials:** Supported keys stored in `.env` are protected by Windows DPAPI. Executables may be signed with an in-house `CN=NemiCLI` certificate and distributed with SHA-256 checksums. A self-signed publisher certificate is not equivalent to a publicly trusted signature.
- **Model files and personas:** Model data undergoes defensive parsing and validation. Persona definitions are prompt text; safety enforcement is implemented in application code rather than trusted to the persona.

Report potential vulnerabilities privately using [SECURITY.md](SECURITY.md).

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
├─ VideoGen/              GGUF video model components
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
├─ Profile/<Name>/        Per-personality chats, images, video, music, character files, LoRA, skills and memory
├─ Persoenlichkeiten/     Custom personality definitions and orb images
├─ Agenten/               Agent/personality instructions
├─ Skills/ · Wissen/      Shared skills and knowledge
├─ Bilder/                Generated images and screenshots
├─ Models/Krea2/          Krea 2 model files
├─ Models/Bildmodelle/    Additional image models
├─ Models/Videomodelle/   SD-Video and video/audio models
├─ Wache/ · Berichte/     Watchdog data and reports
├─ Zeitplan/ · Befehle/   Scheduled tasks and helper scripts
├─ NemiSandbox/           Sandbox files and packages
├─ Doku/ · Cache/         Offline docs and engine caches
├─ Backup/                ZIP/SHA-256 backups
├─ Papierkorb/            Retained deleted/overwritten files (undo)
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

**16 GB RAM is not recommended for the complete NemiCLI environment.** The German README reports the following workload-dependent observations:

| Component | Approximate RAM |
|---|---|
| Windows and background applications | 6–10 GB |
| NemiCLI with memory model | 2–5 GB |
| **Krea 2 image generation** | **20–27 GB resident; up to ~40 GB for a series** |
| Ollama while loading a model on Windows | Roughly the model-file size |

With **16 GB RAM and 16 GB VRAM**, basic chat can work if the model fits on the GPU, and voice/vision often work, but large RAM-offloaded MoE models are constrained. The German README marks Krea 2 and video generation as unsupported for that configuration.

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

## 🤝 Contributors

| Who | Role |
|---|---|
| D. Hoffmann | Development, idea, vibecoding |
| Claude Opus 5.5 (Anthropic) | Code (Claude Code) |
| Roxie – ChatGPT (OpenAI) | Ideas & planning |

---

## 📜 License

NemiCLI is distributed under the **GNU General Public License v3.0 (GPL-3.0)**. See the [LICENSE](LICENSE) file for the full license text and conditions.

---

## 🗺 Roadmap

- [x] Custom GGUF engine supporting vision, MoE and common quantization formats
- [x] Windows installer without admin rights, with Start menu and Apps uninstall
- [x] Windows AppContainer sandbox for experimental code
- [x] Desktop `/gui`, monitored skills, learning loops and to-do driven coding assistant
- [x] Dependency vulnerability checks using `/update` and OSV.dev
- [x] Partial and full ZIP/SHA-256 backups, incremental updates and restore
- [x] Automatic daily backups of memory, profiles and personalities
- [x] Installer UI with installation discovery and data-preserving updates
- [x] Character LoRA support for images and video
- [x] Local ACE-Step 1.5 music and LivePortrait talking portraits
- [x] Difficulty-aware reasoning and independent reviews
- [x] Reranked memory retrieval and learned ranking
- [x] Coding assistant browser checks for missing resources and console errors
- [x] GPL-3.0 license
- [ ] Dedicated restricted Windows user for stronger isolation
- [ ] Publicly trusted code-signing certificate for distribution
- [ ] Submit builds to Bitdefender's false-positive program

The project is **alpha**. The German [README.md](README.md) and [CHANGELOG.md](CHANGELOG.md) provide more detail.

