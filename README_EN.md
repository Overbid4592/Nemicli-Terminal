<div align="center">

# NemiCLI

**An AI agent for the Windows terminal – with local and cloud models, tools, memory and built-in engines for
images, video, music and speech.**

![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2011-informational)
![Python 3.14](https://img.shields.io/badge/Python-3.14-informational)
![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange)

[Manual (German)](docs/Handbuch.md) · [Privacy (German)](docs/Datenschutz-und-Sicherheit.md) ·
[Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) · [Deutsch](README.md)

</div>

NemiCLI talks to language models and uses tools while doing so: files, commands, web research, coding, images.
Every action that changes something is shown first and only carried out after approval. Written in Python, without
Node.js or transformers. **Alpha, version 10.29** · Windows 11 (Windows 10 untested). The user interface and the
detailed documentation are in German.

## Features

- 🧠 **Local or cloud** – own GGUF engine (Gemma 4, Qwen 3.5, Llama, Mistral, DeepSeek …), Ollama, LM Studio, Jan,
  vLLM or 37 cloud providers
- 🛠 **Tools with approval** – files, PowerShell, web search through an allowlist, research sources, PDF, scheduler
- 🟦 **Coding assistant** – to-do list with verified steps, code runs in a Windows AppContainer
- 💾 **Memory** – hybrid search across conversations, notes, knowledge and media
- 🎨 **Images** – Krea 2 in its own engine, further image models, Krea studio, LoRA training
- 🎬 **Video, music, speech** – LTX-2.5 with audio, music with ACE-Step, speech with Qwen3-TTS, talking portraits
- 🖥 **Interfaces** – own terminal window, desktop window, control center, Nemi Office, WebUI, Chrome extension
- 🛡 **System guard** – processes, services, autostart and network with rules and learning models

All details (German): [Funktionen](docs/Funktionen.md).

## Quick start

1. Download `NemiCLI-Setup-<Build>.exe` and `.sha256` from the
   [releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) and compare:

   ```powershell
   Get-FileHash .\NemiCLI-Setup-<Build>.exe -Algorithm SHA256
   Get-Content .\NemiCLI-Setup-<Build>.exe.sha256
   ```

2. Run the setup – no administrator rights needed. The file is self-signed, so SmartScreen may ask.
3. In NemiCLI enter `/einrichten`, then choose a model with `/model`.

From source: `git clone`, then `.\start.bat`. Requirements and details (German):
[Installation](docs/Installation.md).

## Privacy

- With local models the conversation stays on the computer; with cloud models it goes to the chosen provider.
- No telemetry. All network connections and their triggers are
  [documented](docs/Datenschutz-und-Sicherheit.md#4-netzwerkverbindungen).
- API keys are encrypted (AES-256-GCM, bound to the Windows account and computer); no administrator rights.

## Documentation (German)

[Handbuch](docs/Handbuch.md) · [Erste Schritte](docs/Erste-Schritte.md) · [Befehle](docs/Befehle.md) ·
[Modelle](docs/Modelle.md) · [Architektur](docs/Architektur.md) · [CHANGELOG](CHANGELOG.md)

Please report vulnerabilities privately: [SECURITY.md](SECURITY.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

## License

[GPL-3.0](LICENSE) · © 2026 D. Hoffmann · Unpackt (`tools/unpackt.py`) under MIT ·
Built with Claude Code (Claude Opus 5.5, Anthropic).
