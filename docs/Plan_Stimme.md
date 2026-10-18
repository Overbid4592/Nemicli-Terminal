# Plan: Stimme für NemiCLI (Qwen3-TTS)

Stand: 8. Oktober 2026 · Motor (`ggufengine/models/qwen3tts.py`, CUDA-Graphen) und Einbindung (`engines/stimme.py`) gebaut

## Ziel

Jede Persönlichkeit bekommt eine eigene Stimme, die die KI zusammen mit dem Nutzer entwirft:
beschreiben → Probe hören → anpassen → festlegen. Danach liest sie auf Wunsch vor.

## Bausteine

| Teil | Inhalt |
|---|---|
| Modell finden | Ordner unter `Vision/` (später `Sinne/`) mit GGUF-Paar `qwen3-tts` + `qwen3-tts-tokenizer` |
| Laden | erst bei Bedarf, nach einer Weile ohne Nutzung wieder frei (≈ 3 GB VRAM); beim Malen zuerst freigeben |
| Tempo | CUDA-Graphen für Talker- und Vorhersager-Schritt, Ziel: schneller als Echtzeit |
| Abspielen | WAV im Temp-Ordner, `winsound` asynchron, Esc stoppt |
| Gleiche Stimme | Beschreibung + Seed + Sprache je Persönlichkeit; driftet die Stimme zwischen Sätzen zu stark, später Base-Modell (ggml-org) zum Klonen der festgelegten Probe |

## Werkzeuge für die KI

| Werkzeug | Felder | Was passiert |
|---|---|---|
| `stimme_probe` | beschreibung, satz (opt.), seed (opt.) | erzeugt und spielt eine Probe, meldet Länge und Stimmhöhe; die KI fragt, was anders sein soll |
| `stimme_festlegen` | beschreibung, seed | speichert in `Profile/<Name>/stimme.json` (eigener Bereich der Persönlichkeit) |
| `vorlesen` | text oder pfad | liest in der Stimme der aktiven Persönlichkeit vor |

Einstellung `vorlesen_auto` (Vorgabe aus): jede Antwort vorlesen. Kein `/`-Befehl – die KI bedient das.

## Startstimme

„Stimme_Probe1“: *Eine junge Frau mit sanfter, süßer und warmer Stimme, leicht verspielt und
freundlich.* – Sprache german, Seed 7.

## Später

- Spontane Zwischenrufe bei Aktionen (KI formuliert selbst, Regler selten/manchmal/oft/aus, mit Kugel-Geste)
- LRS-Hilfen: Tempo-Regler, Mitlesen (Wort markieren) in `/gui`, „nochmal“
- 🔊 in `/gui` und der Chrome-Seitenleiste
- Hören (Spracherkennung) als nächster Sinn
