"""
bildbeschreiber.py - Augen für lokale Sprachmodelle ohne Bild-Eingang.

Ein kleines Modell mit Bild-Encoder beschreibt Bilder als Text; das aktive Modell
bekommt die Beschreibung. Ordner: `Vision/<Name>/` im Programm-Ordner mit einer
.gguf und einer mmproj-*.gguf (Encoder wie bei `ModelGGUF/` automatisch erkannt).
Mehrere Ordner: Config `vision_ordner` (`/vision <name>`) wählt, sonst der erste.

Gilt für jedes Chat-Modell, das selbst nichts sieht – eigener Motor, Ollama oder
Cloud. Ans Modell geht nur der Text, nie das Bild.

GPU: Während der Beschreibung liegt das Sprachmodell im RAM (`gguflokal.gpu_fuer_bild`),
danach kommt es zurück. Der Beschreiber wartet zwischen zwei Bildern im RAM und wird
nach NACHLAUF_S ohne Bild ganz entladen.

Jede Beschreibung und Nachfrage landet im Seh-Protokoll der Persönlichkeit (sehprotokoll.py);
dasselbe Bild mit derselben Frage wird von dort beantwortet. `neustart()` lädt neu,
`bericht_text()` ist der Fehlerbericht für `/vision debug`.
"""

from __future__ import annotations

import collections
import threading
import time
import traceback
from pathlib import Path

import gguflokal

try:
    import debugbericht as _DBG
except Exception:                                    # Selbsttest ohne core/ im Pfad
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

        @staticmethod
        def fehler(*a, **k): pass

        @staticmethod
        def motor_log(): pass

        class messen:
            def __init__(self, *a): pass

            def __enter__(self): return self

            def __exit__(self, *a): return False

try:
    from paths import INSTALL as _INSTALL
except Exception:                                    # Selbsttest ohne Bootstrap
    _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = _INSTALL / "Vision"
BILD_TOKEN = 280               # mehr Bild-Token lesen im Test nicht besser
KONTEXT = 4096
MAX_BESCHREIBUNG = 700
MAX_ANTWORT = 200
NACHLAUF_S = 120               # so lange ohne Bild, dann ganz entladen (RAM frei)

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

# Kein fester Ausweich-Satz: Qwen3-VL schreibt ihn sonst auch bei sichtbaren Dingen ab.
FRAGE = """{frage}
Antworte in einem Satz. Halte dich nur an das Bild; fehlt etwas im Bild, sag, dass es fehlt."""

HINWEIS = ("Du siehst das Bild nicht selbst – das ist die Beschreibung eines kleinen Bildmodells. "
           "Anzahlen und links/rechts können ungenau sein. Genauer nachfragen: "
           "Werkzeug bild_fragen (Felder: pfad, frage).")

_lock = threading.Lock()
_helfer = None                                       # (Modell-Datei, Engine)
_zuletzt = 0.0                                       # Ende der letzten Nutzung (monotonic)
_wecker: threading.Timer | None = None
_verlauf: collections.deque = collections.deque(maxlen=40)    # (Uhrzeit, was) für den Bericht
_letzter_fehler = ""


def _merken(was: str) -> None:
    _verlauf.append((time.strftime("%H:%M:%S"), was))


def kandidaten() -> list[tuple[str, Path, Path]]:
    """(Ordnername, Modell, mmproj) aller Vision-Ordner mit bekanntem Bild-Encoder.
    Ordner ohne mmproj (z. B. die Stimme) zählen nicht."""
    if not ORDNER.is_dir():
        return []
    raus = []
    for d in sorted(p for p in ORDNER.iterdir() if p.is_dir()):
        modell = gguflokal._gguf_in(d)
        mm = sorted(p for p in d.glob("*.gguf") if gguflokal._ist_mmproj(p))
        try:
            if modell is not None and mm and gguflokal.projektor_bekannt(mm[0]):
                raus.append((d.name, modell, mm[0]))
        except OSError:
            pass
    return raus


def gewaehlt() -> str:
    """Config `vision_ordner` (leer = erster passender Ordner)."""
    return str(gguflokal._config().get("vision_ordner") or "").strip()


def finden() -> tuple[Path, Path] | None:
    """(Modell, mmproj) des gewählten Vision-Ordners – fehlt er, der erste passende."""
    alle = kandidaten()
    if not alle:
        return None
    wahl = gewaehlt().lower()
    _, modell, mm = next((k for k in alle if k[0].lower() == wahl), alle[0])
    return modell, mm


def aktiv() -> str | None:
    """Name des Ordners, den finden() nimmt."""
    teile = finden()
    return teile[0].parent.name if teile else None


def geladen() -> bool:
    return _helfer is not None


def waehlen(name: str) -> str | None:
    """Vision-Ordner festlegen (Groß/klein egal). Gibt den Ordnernamen zurück, None wenn es ihn
    nicht gibt. Ein anderer geladener Beschreiber wird entladen. OSError, wenn die Config nicht speichert."""
    treffer = next((k[0] for k in kandidaten() if k[0].lower() == name.strip().lower()), None)
    if treffer is None:
        return None
    import config
    if not config.update(vision_ordner=treffer):
        raise OSError("Config nicht gespeichert")
    if _helfer is not None and _helfer[0].parent.name != treffer:
        entladen()
    return treffer


def zustaendig(model: str | None) -> bool:
    """Springt der Beschreiber für dieses Chat-Modell ein? Für jedes Modell ohne eigenes Sehen,
    sofern unter Vision/ ein Seh-Modell liegt. Ob Ollama-/Cloud-Modelle selbst sehen, prüfen die
    Aufrufer vorher (main._modell_sieht); hier nur der eigene Motor."""
    if not model:
        return False
    if model.startswith(gguflokal.PROVIDER + ":") and gguflokal.sieht(model.split(":", 1)[1]):
        return False
    return finden() is not None


def _engine():
    """Den Beschreiber laden oder den geladenen zurückgeben. Nur mit gehaltener `_lock` aufrufen."""
    global _helfer
    teile = finden()
    if teile is None:
        raise RuntimeError(f"Kein Bildbeschreiber in {ORDNER} (Ordner mit .gguf und mmproj-*.gguf).")
    if _helfer is not None and _helfer[0] == teile[0]:
        return _helfer[1]
    _entladen()                     # die Sperre hält der Aufrufer schon – `entladen()` wartete ewig auf sie
    gguflokal._torch_bereit()
    from ggufengine import Engine
    t0 = time.monotonic()
    engine = Engine(str(teile[0]), n_ctx=KONTEXT, verbose=False)
    engine.enable_vision(str(teile[1]))
    _helfer = (teile[0], engine)
    _DBG.ereignis("Bildbeschreiber geladen")
    _merken(f"geladen: {teile[0].parent.name} in {time.monotonic() - t0:.1f} s")
    return engine


def beschreiben(pfad, frage: str | None = None) -> str:
    """Beschreibt das Bild – oder beantwortet `frage` dazu. Blockiert (1–6 s; aus dem
    Seh-Protokoll sofort)."""
    import sehprotokoll as SP
    modell = aktiv()
    try:
        sha = SP.fingerabdruck(pfad)
    except OSError:
        sha = ""
    if (alt := SP.nachschlagen(sha, frage, modell)) is not None:
        _merken(f"aus dem Protokoll: {Path(pfad).name}")
        return str(alt["text"])
    from PIL import Image
    import vision
    text = FRAGE.format(frage=frage.strip()) if frage else BESCHREIBEN
    t0 = time.monotonic()
    with Image.open(pfad) as bild:
        antwort = fragen_roh(vision.aufrecht(bild), text, MAX_ANTWORT if frage else MAX_BESCHREIBUNG)
    if not antwort:
        return "(keine Beschreibung)"
    if sha:
        try:
            SP.eintragen(pfad, sha, frage, antwort, time.monotonic() - t0, modell)
        except OSError as e:
            _DBG.fehler("Seh-Protokoll", e)
    return antwort


def fragen_roh(bild, text: str, max_tokens: int = MAX_ANTWORT) -> str:
    """`text` wörtlich zu einem PIL-Bild fragen (ohne Vorlage), temperatur 0. Blockiert.
    Auch für Aufgaben ohne Chat-Modell, z. B. Gesichter finden (gesichter.py)."""
    global _letzter_fehler
    from ggufengine import Message, SamplerConfig
    t0 = time.monotonic()
    try:
        with gguflokal.gpu_fuer_bild(), _lock:
            engine = _engine()
            engine.restore()
            _DBG.ereignis("Bildbeschreiber auf der GPU")
            try:
                emb = engine.encode_image(bild, BILD_TOKEN)
                engine.reset()
                antwort = "".join(engine.chat(
                    [Message("user", text, images=[emb])], checkpoint_system=False,
                    max_tokens=max_tokens, sampler=SamplerConfig(temperature=0.0)))
            finally:
                engine.offload()
                _DBG.ereignis("Bildbeschreiber in den RAM")
                _nachlauf_stellen()
    except Exception as e:
        _letzter_fehler = f"{time.strftime('%H:%M:%S')}\n{traceback.format_exc()}"
        _merken(f"Fehler: {e!r}")
        raise
    _merken(f"angesehen in {time.monotonic() - t0:.1f} s, {len(antwort)} Zeichen")
    return antwort.strip()


def neustart() -> tuple[str, float]:
    """Ganz entladen und sofort neu laden – prüft, ob das Seh-Modell lädt. Gibt (Ordner, Sekunden)
    zurück; Fehler beim Laden gehen an den Aufrufer."""
    global _letzter_fehler
    t0 = time.monotonic()
    try:
        with gguflokal.gpu_fuer_bild(), _lock:
            _entladen()
            _merken("Neustart")
            t0 = time.monotonic()
            _engine().offload()
            _nachlauf_stellen()
            name = _helfer[0].parent.name
    except Exception as e:
        _letzter_fehler = f"{time.strftime('%H:%M:%S')}\n{traceback.format_exc()}"
        _merken(f"Neustart fehlgeschlagen: {e!r}")
        raise
    return name, time.monotonic() - t0


def _nachlauf_stellen() -> None:
    """Nach der Nutzung: in NACHLAUF_S ganz entladen, falls bis dahin kein Bild mehr kommt.
    Nur mit gehaltener `_lock` aufrufen."""
    global _zuletzt, _wecker
    _zuletzt = time.monotonic()
    if _wecker is not None:
        _wecker.cancel()
    _wecker = threading.Timer(NACHLAUF_S, _nachlauf)
    _wecker.daemon = True
    _wecker.start()


def _nachlauf() -> None:
    with _lock:
        if _helfer is not None and time.monotonic() - _zuletzt >= NACHLAUF_S - 1:
            _DBG.ereignis(f"Bildbeschreiber {NACHLAUF_S // 60} Min. ohne Bild")
            _merken(f"{NACHLAUF_S // 60} Min. ohne Bild")
            _entladen()


def block(pfad, text: str, frage: str | None = None) -> str:
    """Beschreibung eingerahmt als Fremd-Inhalt (Text im Bild ist keine Anweisung)."""
    import fremddaten
    name = Path(pfad).name
    kopf = f"Frage: {frage.strip()}\n" if frage else ""
    return fremddaten.rahmen(kopf + text, "bild", name) + f"\n({HINWEIS} Pfad: {pfad})"


def entladen() -> None:
    """Gibt den Speicher des Beschreibers frei."""
    if _helfer is None:
        return
    with _lock:
        _entladen()


def _entladen() -> None:
    """Wie entladen(), aber ohne die Sperre zu nehmen – der Aufrufer hält sie."""
    global _helfer
    if _helfer is None:
        return
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
    _DBG.ereignis("Bildbeschreiber entladen")
    _merken("entladen")


def _encoder(mm: Path) -> str:
    """Bild-Encoder einer mmproj, wie der Motor ihn erkennt (oder warum nicht)."""
    try:
        bekannt = gguflokal.projektor_bekannt(mm)
        typ = gguflokal._mmproj_typ.get((str(mm), mm.stat().st_mtime))
    except Exception as e:
        return f"nicht lesbar: {e!r}"
    return f"{typ} ({'bekannt' if bekannt else 'kennt der Motor nicht'})" if typ else "kein Encoder erkannt"


def bericht_text(model: str | None = None, sieht: bool | None = None) -> str:
    """Fehlerbericht für `/vision debug`: Ordner und Dateien, Wahl, Ladezustand, Verlauf, letzter Fehler."""
    import sehprotokoll as SP
    z = [f"Bildbeschreiber-Bericht {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    try:
        from version import VERSION
        z.append(f"NemiCLI {VERSION}")
    except Exception:
        pass
    z.append(f"Vision-Ordner: {ORDNER} ({'vorhanden' if ORDNER.is_dir() else 'fehlt'})")
    z.append(f"Config vision_ordner: {gewaehlt() or '(leer = erster passender)'} · aktiv: {aktiv() or '–'}")
    z.append(f"Nachlauf: {NACHLAUF_S} s · Bild-Token {BILD_TOKEN} · Kontext {KONTEXT}")
    z.append("")
    try:
        ordner = sorted(p for p in ORDNER.iterdir() if p.is_dir()) if ORDNER.is_dir() else []
    except OSError as e:
        ordner = []
        z.append(f"Ordner nicht lesbar: {e!r}")
    seh = {k[0] for k in kandidaten()}
    for d in ordner:
        z.append(f"[{d.name}] {'Seh-Modell ✓' if d.name in seh else 'kein Seh-Modell'}")
        try:
            dateien = sorted(f for f in d.iterdir() if f.is_file())
        except OSError as e:
            z.append(f"  nicht lesbar: {e!r}")
            continue
        for f in dateien:
            try:
                groesse = f"{f.stat().st_size:,} Bytes".replace(",", ".")
            except OSError:
                groesse = "?"
            extra = f" · Encoder {_encoder(f)}" if f.suffix.lower() == ".gguf" and gguflokal._ist_mmproj(f) else ""
            z.append(f"  {f.name}  {groesse}{extra}")
    z.append("")
    if _helfer is not None:
        z.append(f"Geladen: {_helfer[0].parent.name}/{_helfer[0].name} · zuletzt genutzt vor "
                 f"{time.monotonic() - _zuletzt:.0f} s · Wecker {'läuft' if _wecker and _wecker.is_alive() else 'aus'}")
    else:
        z.append("Geladen: nein")
    if model:
        z.append(f"Chat-Modell: {model} · sieht selbst: {'?' if sieht is None else ('ja' if sieht else 'nein')} · "
                 f"Beschreiber springt ein: {'ja' if (not sieht and zustaendig(model)) else 'nein'}")
    speicher = getattr(_DBG, "torch_speicher", lambda: None)()
    if speicher:
        z.append("Grafikspeicher: " + ", ".join(f"{k} {v}" for k, v in speicher.items()))
    eintraege = SP.lesen()
    z.append(f"Seh-Protokoll dieser Persönlichkeit: {len(eintraege)} Einträge")
    for e in eintraege[-5:]:
        z.append(f"  {e.get('zeit')}  {Path(str(e.get('pfad', ''))).name}  "
                 f"{'Frage' if e.get('frage') else 'Beschreibung'}  {e.get('dauer_s')} s  {e.get('modell')}")
    z.append("")
    z.append("Verlauf (diese Sitzung):")
    z += [f"  {zeit}  {was}" for zeit, was in _verlauf] or ["  (noch nichts)"]
    z.append("")
    z.append("Letzter Fehler:")
    z.append(_letzter_fehler.rstrip() or "  (keiner)")
    return "\n".join(z) + "\n"


def bericht_schreiben(model: str | None = None, sieht: bool | None = None) -> Path:
    """`bericht_text()` nach Debugbericht/vision_bericht.txt (überschreibt den letzten)."""
    import debugbericht
    ziel = debugbericht.ordner()
    ziel.mkdir(parents=True, exist_ok=True)
    pfad = ziel / "vision_bericht.txt"
    pfad.write_text(bericht_text(model, sieht), encoding="utf-8")
    return pfad
