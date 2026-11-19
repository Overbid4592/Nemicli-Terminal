"""
modellorte.py - Wo liegen die KI-Modelle?  (Ein Ort, alle Motoren fragen hier.)

Ein Bereich je Art, darin ein Ordner je Modell (Ordnername = Modellname). Zubehör wie VAE,
Text-Encoder, LoRA oder mmproj liegt im selben Modellordner:

    <BASIS>/KI-Modelle/KI-Chat/Chatmodell/<Modell>/<modell>.gguf

BASIS ist der Programmordner (als exe: der Ordner der exe) oder Config `modelle_ordner`.
Die früheren Orte (ModelGGUF/, Vision/, Models/...) werden weiter gelesen (`alte`), damit
bestehende Installationen ihre Modelle behalten. Neues landet nur im neuen Ort (`ordner`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import paths

WURZEL_NAME = "KI-Modelle"
ANLEITUNG = "Anleitung.txt"

#: Art -> (Bereich, Unterordner mit den Modellordnern)
BEREICHE: dict[str, tuple[str, str]] = {
    "chat":      ("KI-Chat", "Chatmodell"),
    "sehen":     ("KI-Vision", "Sehmodell"),
    "bild":      ("KI-Bild", "BildgenKI"),
    "video":     ("KI-Video", "Videomodell"),
    "stimme":    ("KI-Stimme", "Stimmmodell"),
    "musik":     ("KI-Musik", "Musikmodell"),
    "suche":     ("KI-Gedaechtnis", "Suchmodell"),
    "sortieren": ("KI-Gedaechtnis", "Sortiermodell"),
    "portraet":  ("KI-Portraet", "Portraetmodell"),
    "bewegung":  ("KI-Bewegung", "Bewegungsmodell"),
    "helfer":    ("KI-Helfer", "Helfermodell"),
    "wache":     ("KI-Wache", "Vorpruefer"),
}

# Modellordner mit festem Namen
KREA = "Krea2"                          # unter KI-Bild/BildgenKI
PORTRAET = "LivePortrait"               # unter KI-Portraet/Portraetmodell
BEWEGUNG = "SprongMotion-14M"           # unter KI-Bewegung/Bewegungsmodell
VORPRUEFER = "granite-4.0-h-350m"      # unter KI-Wache/Vorpruefer


def _basis() -> Path:
    """Config `modelle_ordner`, sonst der Programmordner. In der Test-Suite TESTDATEN:
    Tests sehen keine echten Modelle."""
    if paths._testlauf():
        return paths.TESTDATEN
    try:
        d = json.loads(paths.CONFIG_DATEI.read_text(encoding="utf-8"))
        eigen = str(d.get("modelle_ordner") or "").strip() if isinstance(d, dict) else ""
    except Exception:
        eigen = ""
    if eigen:
        try:
            return Path(os.path.expandvars(eigen)).expanduser().resolve()
        except Exception:
            pass
    return paths.INSTALL


BASIS = _basis()
WURZEL = BASIS / WURZEL_NAME


def ordner(art: str) -> Path:
    """Der Ordner, in dem die Modellordner dieser Art liegen (neue Dateien kommen hierher)."""
    bereich, unter = BEREICHE[art]
    return WURZEL / bereich / unter


def anzeige(art: str) -> str:
    """Kurzform für Meldungen, z. B. „KI-Modelle/KI-Vision/Sehmodell/“."""
    bereich, unter = BEREICHE[art]
    return f"{WURZEL_NAME}/{bereich}/{unter}/"


def _alte_orte(schluessel: str) -> list[Path]:
    i, d = paths.INSTALL, paths.DATEN
    return {
        "chat":      [i / "ModelGGUF"],
        "sehen":     [i / "Vision"],
        "stimme":    [i / "Vision", i / "Sinne"],
        "bild":      [d / "Models" / "Bildmodelle", i / "Models" / "Bildmodelle"],
        "krea":      [d / "Models" / "Krea2", i / "Models" / "Krea2"],
        "video":     [d / "Models" / "Videomodelle", i / "Models" / "Videomodelle"],
        "musik":     [b / "Models" / n for b in (d, i) for n in ("Musik", "Music")],
        "suche":     [i / "Models" / "embeddings"],
        "sortieren": [i / "Models" / "reranker"],
        "portraet":  [d / "Models" / "LivePortrait", i / "Models" / "LivePortrait"],
        "bewegung":  [i / "Models" / "Motion"],
        "helfer":    [i / "Models" / "helfer"],
        "wache":     [],
    }[schluessel]


def alte(schluessel: str) -> list[Path]:
    """Frühere Orte (ohne Doppelte), nur zum Lesen. `schluessel`: eine Art aus BEREICHE oder „krea“.
    In der Test-Suite leer."""
    if paths._testlauf():
        return []
    return list(dict.fromkeys(_alte_orte(schluessel)))


def orte(art: str) -> list[Path]:
    """Neuer Ort zuerst, danach die früheren."""
    return list(dict.fromkeys([ordner(art), *alte(art)]))


# ===========================================================================
#  Anleitungen je Bereich
# ===========================================================================

_ANLEITUNGEN: dict[str, str] = {
    "chat": """=== Chatmodelle (eigener GGUF-Motor) ===

Ein Ordner je Modell, Ordnername = Modellname. Darin:
  - die .gguf des Modells (bei manchen Modellen mehrere Teile)
  - optional mmproj-*.gguf: dann sieht das Modell Bilder
  - optional ein Hilfskopf (MTP) mit "assistant" im Namen

Beispiel:
  Chatmodell/gemma-4-E4B/gemma-4-E4B-it-Q4_K_M.gguf

Familie: ein Ordner ohne eigene .gguf, darin je Mitglied ein Unterordner.
  Chatmodell/LFM3Familie/Text/...   erscheint als "LFM3Familie-Text"

Laden: /model huggingface (prueft jede Datei mit SHA-256). Waehlen: /model -> Lokal.
""",
    "sehen": """=== Sehmodelle: Augen fuer Modelle ohne Sehen ===

Sieht das aktive Modell selbst keine Bilder, beschreibt ein Sehmodell von hier das Bild als Text.

Ein Ordner je Modell, Ordnername = Modellname. Darin IMMER:
  - <modell>.gguf
  - mmproj-<modell>.gguf (der Bild-Teil)

Beispiel:
  Sehmodell/Qwen3-VL-4B/Qwen3VL-4B-Instruct-Q4_K_M.gguf
  Sehmodell/Qwen3-VL-4B/mmproj-Qwen3VL-4B-Instruct-F16.gguf

Mehrere Sehmodelle: /vision zeigt sie, /vision <ordner> waehlt eins.
""",
    "bild": """=== Bildmodelle (/bild) ===

Ein Ordner je Modell, Ordnername = Modellname. Darin die Modelldatei (.safetensors oder .gguf)
und ihr Zubehoer: text_encoders/, vae/, tokenizer/ (oder tokenizer_t5/, tokenizer_clip/).

Krea 2 liegt im Ordner "Krea2":
  BildgenKI/Krea2/<krea2>.safetensors
  BildgenKI/Krea2/text_encoders/   BildgenKI/Krea2/vae/   BildgenKI/Krea2/tokenizer/

Zubehoer, das mehrere Modelle teilen, darf auch direkt hier in text_encoders/ und vae/ liegen.
NemiCLI erkennt die Bauart an der Datei, nicht am Namen. Waehlen: /bildmodel.
""",
    "video": """=== Videomodelle (/video) ===

Ein Ordner je Modell, Ordnername = Modellname. Darin das Modell (.safetensors oder .gguf)
und sein Zubehoer: text_encoders/, vae/, bewegung/ (Motion-Modul).

Zubehoer, das mehrere Modelle teilen, darf auch direkt hier liegen:
  Videomodell/text_encoders/   Videomodell/vae/   Videomodell/bewegung/
""",
    "stimme": """=== Stimmmodelle (/voice) ===

Ein Ordner je Modell, Ordnername = Modellname. Fuer Qwen3-TTS beide GGUF-Dateien:
  - der Talker (qwen3-tts)
  - der 12-Hz-Tokenizer (Klang-Decoder)

Beispiel:
  Stimmmodell/Qwen3-TTS/<talker>.gguf
  Stimmmodell/Qwen3-TTS/<tokenizer-12hz>.gguf
""",
    "musik": """=== Musikmodelle (/musik) ===

Ein Ordner je Modell, Ordnername = Modellname. Fuer ACE-Step 1.5 alle Teile in einen Ordner:
  Musikmodell/ACE-Step-1.5/acestep-v15-turbo-Q8_0.gguf
  Musikmodell/ACE-Step-1.5/Qwen3-Embedding-0.6B-Q8_0.gguf
  Musikmodell/ACE-Step-1.5/vae-BF16.gguf
  ...
""",
    "suche": """=== Suchmodelle fuers Gedaechtnis (Embedding, /embeddings) ===

Ein Ordner je Modell, Ordnername = Modellname. Darin genau eine .gguf
(optional mmproj-*.gguf fuer die Bildsuche) - oder ein safetensors-Modell mit Tokenizer.

Beispiel (EmbeddingGemma 2):
  Suchmodell/EmbeddingGemma2Q8_0/embeddinggemma-2-Q8_0.gguf
  Suchmodell/EmbeddingGemma2Q8_0/mmproj-BF16.gguf

Laden: /einrichten modelle -> Gedaechtnis. Waehlen: /embeddings.
""",
    "sortieren": """=== Sortiermodelle fuers Gedaechtnis (Reranker) ===

Sortiert die Treffer der Gedaechtnis-Suche nach. Ein Ordner je Modell,
Ordnername = Modellname, darin genau eine .gguf.

Beispiel:
  Sortiermodell/Qwen3-Reranker-0.6B/Qwen3-Reranker-0.6B-Q8_0.gguf
""",
    "portraet": """=== Portraetmodelle (Gesichter bewegen) ===

LivePortrait liegt im Ordner "LivePortrait", mit den Unterordnern wie im Original:
  Portraetmodell/LivePortrait/base_models/...
  Portraetmodell/LivePortrait/retargeting_models/...
""",
    "bewegung": """=== Bewegungsmodelle (Figur im Terminal, /bewegung) ===

SprongMotion liegt im Ordner "SprongMotion-14M", die Laufzeit im selben Ordner:
  Bewegungsmodell/SprongMotion-14M/SprongMotion-14M-v1.0-Q8_0.gguf
  Bewegungsmodell/SprongMotion-14M/sprongmotion-<version>/

/bewegung holen bzw. /bewegung einrichten legt die Dateien hierher (mit SHA-256-Pruefung).
""",
    "helfer": """=== Helfermodelle (Einrichtungs-Helfer) ===

Ein Ordner je Modell, Ordnername = Modellname, darin die .gguf.
  Helfermodell/gemma4-e2b/gemma-4-E2B-it-Q4_K_M.gguf
  Helfermodell/gemma4-e4b/gemma-4-E4B-it-Q4_K_M.gguf

Laden: /einrichten.
""",
    "wache": """=== Vorpruefer der Wache ===

Ein kleines Modell sieht Alarme vor dem Weckruf: einfache Fehlalarme schliesst es selbst, zu allen
anderen schreibt es der Cloud-KI einen Brief. Das Modell liegt im Ordner "granite-4.0-h-350m",
so wie es im Original-Repo steht (safetensors, kein GGUF):
  Vorpruefer/granite-4.0-h-350m/config.json
  Vorpruefer/granite-4.0-h-350m/model.safetensors
  Vorpruefer/granite-4.0-h-350m/tokenizer.json   (+ tokenizer_config.json, special_tokens_map.json)

Laden: /einrichten modelle -> Wache-Vorpruefer (prueft jede Datei mit SHA-256).
Die Anpassung (LoRA) lernt NemiCLI nur auf Wunsch auf diesem PC (/wache vorpruefer lernen);
sie liegt im Datenordner unter Wache/vorpruefer/. Rechnen braucht torch und transformers im venv.
""",
}

_ZWECK = {"chat": "Chatten (eigener GGUF-Motor)", "sehen": "Bilder ansehen", "bild": "Bilder malen",
          "video": "Videos", "stimme": "Sprechen", "musik": "Musik", "suche": "Gedaechtnis: Suche",
          "sortieren": "Gedaechtnis: Treffer sortieren", "portraet": "Gesichter bewegen",
          "bewegung": "Figur bewegen", "helfer": "Einrichtungs-Helfer", "wache": "Wache: Alarme vorpruefen"}

_UEBERSICHT = """=== KI-Modelle ===

Ein Bereich je Art, darin ein Ordner je Modell (Ordnername = Modellname).
Zubehoer (VAE, Text-Encoder, LoRA, mmproj ...) gehoert in denselben Modellordner.

{bereiche}

In jedem Bereich steht eine Anleitung.txt.
"""


def _write_once(path: Path, text: str) -> None:
    try:
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    except Exception:
        pass


# Ausführliche Motor-Hinweise, die an die Anleitung angehängt werden (Modul, Name)
_ZUSATZ = {"bild": ("bildmotoren", "LIES_MICH"), "video": ("videomotoren", "LIES_MICH")}


def anleitung(art: str) -> str:
    text = _ANLEITUNGEN[art]
    if art in _ZUSATZ:
        try:
            import importlib
            modul, name = _ZUSATZ[art]
            text += "\n\n" + getattr(importlib.import_module(modul), name)
        except Exception:
            pass
    return text


def anlegen() -> None:
    """Legt die Bereiche an und schreibt je Bereich die Anleitung (vorhandene Dateien bleiben)."""
    for art in BEREICHE:
        o = ordner(art)
        try:
            o.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        if not (o / ANLEITUNG).exists():
            _write_once(o / ANLEITUNG, anleitung(art))
    zeilen = "\n".join(f"  {anzeige(a)[len(WURZEL_NAME) + 1:]:<34}{_ZWECK[a]}" for a in BEREICHE)
    _write_once(WURZEL / ANLEITUNG, _UEBERSICHT.format(bereiche=zeilen))
