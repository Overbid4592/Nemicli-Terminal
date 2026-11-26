"""
gemmahelfer.py - Gemma 4 E2B/E4B als Einrichtungs-Helfer.

Der eigene GGUF-Motor rechnet mit Gemma 4, angepasst durch ein LoRA aus dem eigenen LoRA-Motor.
`gespraech.Gespraech` bekommt dafür einen `Rechner` als Helfer; Prüfen der Aktion, Platzhalter und Anzeige
bleiben dort.

Grundmodell: die offizielle unsloth-GGUF in Q4_K_M, geladen über /einrichten nach KI-Modelle/KI-Helfer/Helfermodell/<Wahl>/.
LoRA: einrichtungshelfer/lora/<Grundmodell>.safetensors, mit NemiCLI ausgeliefert. Gemma 4 steht unter
Apache 2.0; das LoRA ist eine Veränderung davon (LIZENZ.txt im lora-Ordner).
"""
from __future__ import annotations

from pathlib import Path

from .gespraech import NAME, Gespraech, bereinigen

WAHL = ("gemma4-e2b", "gemma4-e4b")
DATEI = {"gemma4-e2b": "gemma-4-E2B-it-Q4_K_M.gguf", "gemma4-e4b": "gemma-4-E4B-it-Q4_K_M.gguf"}
KONTEXT = 2048
MAX_NEU = 140
LEER = "–"                      # Eingabe, wenn der Helfer die Frage des Schritts stellt

SYSTEM = ("Du bist {ich}, der Einrichtungs-Helfer von NemiCLI. Beginne jede Antwort mit der Aktion in eckigen "
          "Klammern ([-] = keine Aktion), danach ein bis drei kurze, freundliche Sätze auf Deutsch. Die "
          "Platzhalter {name}, {liste} und {pers} schreibst du genau so; NemiCLI setzt sie ein. "
          "Ist die Eingabe „–“, stellst du die Frage des Schritts.")


def system_text(zust: str) -> str:
    return f"{SYSTEM}\nZustand: {zust}"


def nutzer_text(nutzer: str) -> str:
    return bereinigen(nutzer) or LEER


def _install() -> Path:
    try:
        from paths import INSTALL
        return Path(INSTALL)
    except Exception:
        return Path(__file__).resolve().parent.parent


def lora_ordner() -> Path:
    try:
        from paths import ASSETS
    except Exception:
        ASSETS = Path(__file__).resolve().parent.parent
    return Path(ASSETS) / "einrichtungshelfer" / "lora"


def orte() -> list[Path]:
    """KI-Modelle/KI-Helfer/Helfermodell (neue Dateien), danach der frühere Ort Models/helfer."""
    try:
        import modellorte
        neu = modellorte.ordner("helfer")
    except Exception:
        neu = _install() / "KI-Modelle" / "KI-Helfer" / "Helfermodell"
    return [neu, _install() / "Models" / "helfer"]


def grundmodell(wahl: str) -> Path | None:
    if wahl not in DATEI:
        return None
    return next((p for o in orte() if (p := o / wahl / DATEI[wahl]).is_file()), None)


def lora(wahl: str) -> Path | None:
    if wahl not in DATEI:
        return None
    p = lora_ordner() / f"{Path(DATEI[wahl]).stem}.safetensors"
    return p if p.is_file() else None


def angeboten() -> list[str]:
    """Wahlmöglichkeiten: nur Gemma-Varianten mit ausgeliefertem LoRA (Grundmodell lädt /einrichten)."""
    return [w for w in WAHL if lora(w) is not None]


def gewaehlt() -> str | None:
    """Gespeicherte Wahl, wenn sie angeboten wird; sonst die erste angebotene; None ohne Angebot."""
    angebot = angeboten()
    try:
        import config
        w = str(config.load().get("einrichtungs_helfer") or "")
    except Exception:
        w = ""
    if w in WAHL and w in angebot:
        return w
    return angebot[0] if angebot else None


class Rechner:
    """`antworten(zust, nutzer, beim_token)` -> roher Text mit Aktions-Klammer."""

    def __init__(self, gguf: Path, lora_datei: Path, engine=None):
        if engine is None:
            from ggufengine import Engine
            from loramotor import adapter
            engine = Engine(str(gguf), n_ctx=KONTEXT, verbose=False)
            adapter.anklinken(engine.model, lora_datei, gguf)
        self.engine = engine

    def antworten(self, zust: str, nutzer: str = "", beim_token=None, max_neu: int = MAX_NEU) -> str:
        from ggufengine import Message, SamplerConfig
        nachrichten = [Message("system", system_text(zust)), Message("user", nutzer_text(nutzer))]
        text = ""
        for stueck in self.engine.chat(nachrichten, max_tokens=max_neu, sampler=SamplerConfig(temperature=0.0)):
            text += stueck
            if beim_token is not None:
                beim_token(text)
        return text.strip()


def helfer_bauen(wahl: str) -> Gespraech:
    """Gespräch mit Gemma; braucht Grundmodell und LoRA. Ladefehler gehen an den Aufrufer."""
    gguf, zusatz = grundmodell(wahl), lora(wahl)
    if gguf is None:
        raise RuntimeError(f"Grundmodell für {wahl} fehlt – /einrichten lädt es.")
    if zusatz is None:
        raise RuntimeError(f"LoRA für {wahl} fehlt.")
    return Gespraech(helfer=Rechner(gguf, zusatz))


__all__ = ["NAME", "WAHL", "DATEI", "Rechner", "angeboten", "bereinigen", "gewaehlt", "grundmodell", "helfer_bauen",
           "lora", "system_text", "nutzer_text"]
