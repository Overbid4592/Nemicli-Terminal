# NemiCLI – Architektur

Aufbau des Programms für Entwickler. Bedienung und Funktionen beschreibt das [Handbuch](Handbuch.md).

## Das große Bild

- **Eine Sitzung, mehrere Oberflächen.** Die laufende Sitzung hält Modell, Verlauf, Werkzeuge und Gedächtnis.
  Terminal, Desktop-Fenster (Named Pipe), WebUI und Chrome-Erweiterung (nur `127.0.0.1`) sind Oberflächen an
  derselben Sitzung. Es läuft immer nur ein Zug gleichzeitig (`TURN_LOCK`).
- **Eine Schnittstelle für alle Modelle.** Jeder Chat-Motor (Anthropic, OpenAI-kompatibel, eigener GGUF-Motor)
  bietet `stream(text, images)`, `ask_once(prompt, system)`, `reset()` und `.messages`. Darum läuft die
  Aktions-Schleife für jeden Anbieter gleich.
- **Aktions-Schleife:** Antwort streamen → Aktionen erkennen → Freigabe nach Modus und Risiko → ausführen →
  Ergebnis gerahmt zurück an das Modell. Schrittbremsen begrenzen Läufe ohne Fortschritt.
- **Schutz im Code, nicht im Prompt.** Persönlichkeiten sind reiner Prompt-Text; Pfadprüfung, Freigaben,
  Netzregeln und Sperren sitzen in `tools/actions.py`, `tools/sicherheit.py` und `core/modes.py` und gelten
  für jede Persönlichkeit und jedes Modell.
- **Reines Python, Windows-zentriert.** Bewusst ohne transformers, diffusers, scikit-learn und Node.js.

## Prozesse

| Prozess | Aufgabe |
|---|---|
| Hauptprogramm (`main.py` bzw. `NemiCLI.exe`) | Sitzung, Terminal-Oberfläche, Werkzeuge, Motoren |
| Terminal-Fenster | selbst gezeichnetes Fenster (pyte + ConPTY) um das Hauptprogramm |
| Wache (`--wache`) | Hintergrund-Dienst: Sensoren, Regeln, Modelle, Schwebekugel, Schild in der Taskleiste |
| Fenster (`--gui`, `--control`, `--office`, `--atelier`, `--wachefenster`) | eigene Qt-Prozesse, verbunden über eine Pipe mit Schlüssel |
| Hintergrund-Auftrag (`--auftrag <name>`) | Zeitplan-Lauf ohne Fenster, fest „Nur Lesen“, Antwort als Bericht |
| Kurze Rechenprozesse | Malen der Wache, Einlesen des Gedächtnisses, Vollscan – enden danach und geben ihren Speicher frei |

## Verzeichnisse

```
main.py                 Einstieg, Chat-Schleife, Aktions-Steuerung
main_dienste.py         Dienste und Fenster-Brücken
main_befehle.py         Slash-Befehle
main_start.py           Start, Wache, Hintergrund-Aufträge
core/                   Grundbausteine
engines/                Chat-, Bild-, Video-, Musik- und Stimm-Motoren
ggufengine/             eigener GGUF-Motor
trtmotor/               TensorRT-RTX als Zusatz
loramotor/              Coding-LoRA und Lern-LoRA für lokale Modelle
tools/                  Werkzeuge, Gedächtnis, Sandbox, Web, Wache (tools/wache/)
ui/                     Terminal, Fenster, Themes
nemiview/               eigene Web-Engine für den Appstore
installer/              Setup-Programm
tests/                  Offline-Tests
```

## core/

| Modul | Zweck |
|---|---|
| `persona.py` | baut den System-Prompt: Persönlichkeit, Grundregeln, Werkzeuge, Erinnerungen; kompakte Fassung für lokale Modelle, Details über `anleitung_lesen` |
| `commands.py` | alle Slash-Befehle (`COMMAND_LIST`, einzige Quelle für Menü und `/help`) |
| `modes.py` | Arbeitsmodus: `decide(action)` → ausführen, fragen oder sperren |
| `config.py` | Einstellungen in `nemicli.config.json` |
| `tresor.py` | API-Schlüssel, AES-256-GCM mit DPAPI-gebundenem Schlüssel |
| `paths.py` · `profilordner.py` | Programm- und Daten-Ordner, Profile je Persönlichkeit |
| `chatstore.py` | Chats speichern und fortsetzen |
| `coding.py` · `plan.py` | Coding-Assistent · Todo-Liste vor jeder Aktion |
| `beobachter.py` · `pruefer.py` | meldet Änderungen seit dem letzten Blick · Gegenprobe der fertigen Antwort |
| `notstopp.py` | Esc: Antwort und Werkzeuge anhalten, nichts löschen |
| `ersatzbank.py` | Ersatzmodelle bei wiederholten Schutzsperren |
| `hintergrund.py` | Hintergrund-Aufgaben (Bildserien, Video) |
| `debugbericht.py` | Speicher- und Fehlerprotokoll je Prozess |
| `version.py` · `versionsbericht.py` | Version und Build · was neu ist, für die KI |

## engines/

| Modul | Zweck |
|---|---|
| `chat.py` | Anthropic, natives SDK |
| `cloud.py` · `openai_responses.py` | OpenAI-kompatible Anbieter · OpenAI Responses API |
| `models.py` · `providers.py` | Modell-Referenzen `anbieter:modell` · Anbieterliste |
| `gguflokal.py` | Anbindung des eigenen GGUF-Motors an die Sitzung |
| `krea.py` · `bildmotoren/` | Krea 2 · weitere Bild-Modelle |
| `video.py` · `videomotoren/` | Wan 2.2 · LTX-2.5 und SD-Video |
| `liveportrait/` · `musik/` · `stimme.py` | Porträt · Musik (ACE-Step) · Stimme (Qwen3-TTS) |
| `bildbeschreiber.py` | lokales Seh-Modell für Modelle ohne Sehen |

## ggufengine/

Eigener Motor in Python und PyTorch: gehärteter GGUF-Leser (`gguf.py`), Tokenizer, Quantisierung und eigene
CUDA-Kernel (`quant.py`, `mmvq.py`, `cudakern.py`, übersetzt mit NVRTC), Sehen (`vision.py`), Sampling und
spekulatives Dekodieren. `models/baukasten.py` setzt gängige Bauarten aus den Angaben der Datei zusammen;
eigene Klassen gibt es für Gemma 4, Qwen3.5, Qwen3-VL, Ling 3.0, DeepSeek V2/V3 und MoE. Der
Kernel-Zwischenspeicher liegt in `%LOCALAPPDATA%\NemiCli\kernel-cache` und wird auf jedem Rechner neu angelegt.

## tools/

| Bereich | Module |
|---|---|
| Werkzeuge und Freigabe | `actions.py` (alle Werkzeuge, Pfadprüfung), `sicherheit.py`, `fremddaten.py` (Rahmen für Fremdinhalt) |
| Ausführen | `sandbox.py` (AppContainer), `systemabfrage.py` (nur lesend, ohne PowerShell), `zeitplan.py` |
| Gedächtnis | `memory.py`, `indexdb.py` (Stichworte + Vektoren), `wissen.py`, `skills.py`, `skillwaechter.py`, `lernschleife.py` |
| Netz | `webfetch.py` (HTTPS, Allowlist, SSRF-Schutz), `schutzliste.py`, `recherche.py`, `fachquellen.py`, `chromebridge.py`, `webui.py` |
| Einstellungen | `kontrollzentrum.py` (Controllcenter) |
| Wache | `tools/wache/`: Sensoren, Regeln, Modelle, Speicher, Schwebekugel, Steuerung durch die Persönlichkeit |

## ui/

Terminal-Oberfläche (`screen_tx.py`, Textual), eigenes Terminal-Fenster (`terminal_fenster.py`, `terminal_pty.py`),
Desktop-Fenster (`gui_fenster.py`), Controllcenter, Office, Atelier und Wache-Fenster, gemeinsames Design
(`design.py`) und die Fenster-Kugel (`office_kugel.py`). Alle Fenster sind PySide6.

## Konfiguration

| Datei | Inhalt |
|---|---|
| `nemicli.config.json` | gemerkte Einstellungen |
| `.tresor/` | API-Schlüssel, verschlüsselt; nur über das Controllcenter |
| `allowlist.json` | erlaubte Web-Domains nach Vertrauensstufe, alles andere gesperrt |
| `befehl_whitelist.json` · `schreibsperre.json` | harmlose Befehlsanfänge · eigene Sperrorte; nur der Nutzer ändert sie |

## Bau und Auslieferung

| Datei | Zweck |
|---|---|
| `build_exe.py` · `NemiCLI.spec` | PyInstaller-Bau nach `dist/NemiCLI/`, Signatur, `SHA256SUMS.txt`; torch, numpy und Pillow kommen aus dem venv |
| `installer/nemicli_setup.py` | Setup-Datei: findet Installationen, ersetzt nur das Programm |
| `zertifikat.py` | legt das eigene Signatur-Zertifikat `CN=NemiCLI` an |

## Tests

`tests/` enthält über 340 Testdateien mit über 3.300 Offline-Tests, darunter eine statische Sicherheitsprüfung
(`tests/test_sicherheit_statisch.py`: kein `eval`, kein `shell=True`, kein `pickle`, TLS-Prüfung an, SQL nur mit
Platzhaltern). Ausführen: siehe [Installation](Installation.md#aus-dem-quellcode).
