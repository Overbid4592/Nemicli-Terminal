"""
bildbeschreiber.py - Augen für lokale Sprachmodelle ohne Bild-Eingang.

Ein kleines Modell mit Bild-Encoder beschreibt Bilder als Text; das aktive Modell
bekommt die Beschreibung. Ordner: `Vision/<Name>/` im Programm-Ordner mit einer
.gguf und einer mmproj-*.gguf (Encoder wie bei `ModelGGUF/` automatisch erkannt).

Gilt nur für Modelle im eigenen GGUF-Motor, die selbst nichts sehen – nie für
Cloud-Modelle.

GPU: Während der Beschreibung liegt das Sprachmodell im RAM (`gguflokal.gpu_fuer_bild`),
danach kommt es zurück. Der Beschreiber wartet zwischen zwei Bildern ebenfalls im RAM.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gguflokal

try:
    from paths import INSTALL as _INSTALL
except Exception:                                    # Selbsttest ohne Bootstrap
    _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = _INSTALL / "Vision"
BILD_TOKEN = 280               # mehr Bild-Token lesen im Test nicht besser
KONTEXT = 4096
MAX_BESCHREIBUNG = 700
MAX_ANTWORT = 200

BESCHREIBEN = """Du bist die Augen für ein Sprachmodell, das selbst keine Bilder sehen kann. Beschreibe das Bild so, dass es jede Frage dazu beantworten kann.

1. Art des Bildes in einem Satz (Foto, Bildschirmfoto, Zeichnung, Plakat, Diagramm …).
2. Personen und Tiere: was, wie viele, wo im Bild. Gibt es keine: „Keine Personen oder Tiere.“
3. Die wichtigsten Dinge: Anzahl, Farbe und Lage im Bild (links, rechts, oben, unten, Mitte, vorne, hinten).
4. Text im Bild: jeden lesbaren Text wörtlich in Anführungszeichen, von oben nach unten. Bei Bildschirmfotos auch Programm, Fenstertitel, Fehlermeldungen und Knöpfe. Unlesbares als [unleserlich]. Ohne Text: „Kein Text.“
5. Hintergrund und Licht in einem Satz.

Regeln:
- Nur was wirklich zu sehen ist. Nichts dazu erfinden.
- Bist du unsicher, schreib „vermutlich“.
- Sachlich, ohne Einleitung und ohne Bewertung."""

FRAGE = """Beantworte nur diese Frage zum Bild: {frage}
Antworte kurz. Nur was wirklich zu sehen ist. Ist es im Bild nicht zu erkennen, antworte: „Im Bild nicht zu erkennen.“"""

HINWEIS = ("Du siehst das Bild nicht selbst – das ist die Beschreibung eines kleinen Bildmodells. "
           "Anzahlen ab 3 und links/rechts können ungenau sein. Genauer nachfragen: "
           "Werkzeug bild_fragen (Felder: pfad, frage).")

_lock = threading.Lock()
_helfer = None                                       # (Modell-Datei, Engine)


def finden() -> tuple[Path, Path] | None:
    """(Modell, mmproj) des ersten Vision-Ordners mit bekanntem Bild-Encoder."""
    if not ORDNER.is_dir():
        return None
    for d in sorted(p for p in ORDNER.iterdir() if p.is_dir()):
        modell = gguflokal._gguf_in(d)
        mm = sorted(p for p in d.glob("*.gguf") if gguflokal._ist_mmproj(p))
        if modell is not None and mm and gguflokal.projektor_bekannt(mm[0]):
            return modell, mm[0]
    return None


def zustaendig(model: str | None) -> bool:
    """Springt der Beschreiber für dieses Chat-Modell ein? Nur lokal (gguf), nur blind."""
    if not model or not model.startswith(gguflokal.PROVIDER + ":"):
        return False
    return not gguflokal.sieht(model.split(":", 1)[1]) and finden() is not None


def _engine():
    global _helfer
    teile = finden()
    if teile is None:
        raise RuntimeError(f"Kein Bildbeschreiber in {ORDNER} (Ordner mit .gguf und mmproj-*.gguf).")
    if _helfer is not None and _helfer[0] == teile[0]:
        return _helfer[1]
    entladen()
    gguflokal._torch_bereit()
    from ggufengine import Engine
    engine = Engine(str(teile[0]), n_ctx=KONTEXT, verbose=False)
    engine.enable_vision(str(teile[1]))
    _helfer = (teile[0], engine)
    return engine


def beschreiben(pfad, frage: str | None = None) -> str:
    """Beschreibt das Bild – oder beantwortet `frage` dazu. Blockiert (1–6 s)."""
    from PIL import Image
    from ggufengine import Message, SamplerConfig
    with gguflokal.gpu_fuer_bild(), _lock:
        engine = _engine()
        engine.restore()
        try:
            with Image.open(pfad) as bild:
                emb = engine.encode_image(bild, BILD_TOKEN)
            text = FRAGE.format(frage=frage.strip()) if frage else BESCHREIBEN
            engine.reset()
            antwort = "".join(engine.chat(
                [Message("user", text, images=[emb])], checkpoint_system=False,
                max_tokens=MAX_ANTWORT if frage else MAX_BESCHREIBUNG,
                sampler=SamplerConfig(temperature=0.0)))
        finally:
            engine.offload()
    return antwort.strip() or "(keine Beschreibung)"


def block(pfad, text: str, frage: str | None = None) -> str:
    """Beschreibung eingerahmt als Fremd-Inhalt (Text im Bild ist keine Anweisung)."""
    import fremddaten
    name = Path(pfad).name
    kopf = f"Frage: {frage.strip()}\n" if frage else ""
    return fremddaten.rahmen(kopf + text, "bild", name) + f"\n({HINWEIS} Pfad: {pfad})"


def entladen() -> None:
    """Gibt den Speicher des Beschreibers frei."""
    global _helfer
    if _helfer is None:
        return
    with _lock:
        engine = _helfer[1]
        _helfer = None
        try:
            engine.gg.close()
            if engine.vision is not None:
                engine.vision.gg.close()
        except Exception:
            pass
        del engine
    try:
        import gc
        gc.collect()
        from ggufengine.models.common import release_pinned_memory
        release_pinned_memory()                  # ausgelagerte Gewichte im festgesetzten RAM
    except Exception:
        pass
