<div align="center">

# NemiCLI

**KI-Agent für das Windows-Terminal – mit lokalen und Cloud-Modellen, Werkzeugen, Gedächtnis und eigenen Motoren
für Bild, Video, Musik und Stimme.**

![Lizenz: GPL-3.0](https://img.shields.io/badge/Lizenz-GPL--3.0-blue)
![Plattform: Windows](https://img.shields.io/badge/Plattform-Windows%2011-informational)
![Python 3.14](https://img.shields.io/badge/Python-3.14-informational)
![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange)

[Handbuch](docs/Handbuch.md) · [Datenschutz](docs/Datenschutz-und-Sicherheit.md) ·
[Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) · [English](README_EN.md)

</div>

NemiCLI führt Gespräche mit Sprachmodellen und nutzt dabei Werkzeuge: Dateien, Befehle, Web-Recherche,
Programmieren, Bilder. Jede verändernde Aktion wird vorher gezeigt und erst nach Freigabe ausgeführt. Geschrieben in
Python, ohne Node.js und ohne transformers. **Alpha, Version 10.29** · Windows 11 (Windows 10 ungetestet).

## Funktionen

- 🧠 **Lokal oder Cloud** – eigener GGUF-Motor (Gemma 4, Qwen 3.5, Llama, Mistral, DeepSeek …), Ollama, LM Studio,
  Jan, vLLM oder 37 Cloud-Anbieter
- 🛠 **Werkzeuge mit Freigabe** – Dateien, PowerShell, Websuche über eine Allowlist, Fachquellen, PDF, Zeitplan
- 🟦 **Coding-Assistent** – Todo-Liste mit geprüften Schritten, Code läuft in einem Windows-AppContainer
- 💾 **Gedächtnis** – hybride Suche über Gespräche, Notizen, Wissen und Medien
- 🎨 **Bilder** – Krea 2 im eigenen Motor, weitere Bild-Modelle, Krea-Atelier, LoRA-Training
- 🎬 **Video, Musik, Stimme** – LTX-2.5 mit Ton, Musik mit ACE-Step, Stimme mit Qwen3-TTS, sprechende Porträts
- 🖥 **Oberflächen** – eigenes Terminal-Fenster, Desktop-Fenster, Controllcenter, Nemi Office, WebUI, Chrome
- 🛡 **Systemwache** – Prozesse, Dienste, Autostart und Netz mit Regeln und lernenden Modellen

Alle Einzelheiten: [Funktionen](docs/Funktionen.md).

## Schnellstart

1. `NemiCLI-Setup-<Build>.exe` und `.sha256` von den
   [Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) laden und vergleichen:

   ```powershell
   Get-FileHash .\NemiCLI-Setup-<Build>.exe -Algorithm SHA256
   Get-Content .\NemiCLI-Setup-<Build>.exe.sha256
   ```

2. Setup starten – ohne Administratorrechte. Die Datei ist selbst signiert, SmartScreen kann nachfragen.
3. In NemiCLI `/einrichten` eingeben, dann mit `/model` ein Modell wählen.

Aus dem Quellcode: `git clone`, dann `.\start.bat`. Voraussetzungen und Einzelheiten:
[Installation](docs/Installation.md).

## Datenschutz

- Mit lokalen Modellen bleibt das Gespräch auf dem Rechner; mit Cloud-Modellen geht es an den gewählten Anbieter.
- Keine Telemetrie. Alle Netzverbindungen und ihre Auslöser sind
  [dokumentiert](docs/Datenschutz-und-Sicherheit.md#4-netzwerkverbindungen).
- API-Schlüssel verschlüsselt (AES-256-GCM, an Windows-Konto und Rechner gebunden), keine Administratorrechte.

## Dokumentation

[Handbuch](docs/Handbuch.md) · [Erste Schritte](docs/Erste-Schritte.md) · [Befehle](docs/Befehle.md) ·
[Modelle](docs/Modelle.md) · [Architektur](docs/Architektur.md) · [CHANGELOG](CHANGELOG.md)

Sicherheitslücken bitte vertraulich melden: [SECURITY.md](SECURITY.md) · Mitmachen: [CONTRIBUTING.md](CONTRIBUTING.md)

## Lizenz

[GPL-3.0](LICENSE) · © 2026 D. Hoffmann · Unpackt (`tools/unpackt.py`) unter MIT ·
Erstellt mit Claude Code (Claude Opus 5.5, Anthropic).
