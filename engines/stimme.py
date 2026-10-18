"""
stimme.py – Sprechen mit Qwen3-TTS (eigener GGUF-Motor, `ggufengine/models/qwen3tts.py`).

Modell: ein Ordner unter `Vision/` oder `Sinne/` im Programm-Ordner mit dem GGUF-Paar
arch "qwen3-tts" (Talker) und "qwen3-tts-tokenizer" (Klang-Decoder).  Geladen wird erst beim
ersten Sprechen; nach FREIGABE_S ohne Nutzung und beim Bildermalen wird die GPU wieder frei.

Stimme einer Persönlichkeit: Eintrag `voice` {beschreibung, seed, sprache} in ihrer Charakter-Datei
(`Profile/<Name>/<name>.json`, tools/charakter.py); fehlt er, gilt STARTSTIMME.  Gleiche Beschreibung +
Seed + Sprache ergeben dieselbe Stimme.  Hz-Sperre: Proben unter MIN_HZ (Männerbereich, Config
`stimme_min_hz`, 0 = aus) werden nicht abgespielt und lassen sich nicht festlegen.

Sprechen läuft in einem eigenen Faden: Text in Sätze teilen, Satz n+1 berechnen, während Satz n
spielt (winsound, WAV im Temp-Ordner).  `stopp()` bricht Berechnung und Wiedergabe ab.
"""
from __future__ import annotations

import json
import queue
import re
import tempfile
import threading
import time

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
import wave
from pathlib import Path
from typing import Callable, Optional

try:
    from paths import INSTALL as _INSTALL
except Exception:
    _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = ("Vision", "Sinne")
FREIGABE_S = 90                    # Modus „an“: so lange bleibt die Stimme nach dem letzten Satz warm
MODI = ("aus", "knopf", "an")
ABTASTRATE = 24000
STARTSTIMME = {
    "beschreibung": "Eine junge Frau mit sanfter, süßer und warmer Stimme, leicht verspielt und freundlich.",
    "seed": 7,
    "sprache": "german",
}
MIN_HZ = 150                       # darunter klingt eine Stimme männlich
MAX_SATZ = 280                     # Zeichen je Stück: kurze Stücke = schneller erster Ton
MAX_VORLESEN = 6000                # Zeichen je Vorlese-Auftrag

AKTIV = False                      # main.py setzt True: nur das große NemiCLI spricht (nicht die Wache)

_lock = threading.RLock()
_rechnen = threading.Lock()        # immer nur ein Satz gleichzeitig auf dem Modell
_modell = None                     # (talker, codec)
_pfade: Optional[tuple[Path, Path]] = None
_zuletzt = 0.0
_auftraege: "queue.Queue[tuple[int, str, dict]]" = queue.Queue()
_generation = 0                    # stopp() erhöht sie: ältere Aufträge verfallen
_faden: Optional[threading.Thread] = None
_spielt_bis = 0.0
_zustand = {"spricht": False, "fehler": ""}


# ---------------------------------------------------------------------------
# Modell finden, laden, freigeben
# ---------------------------------------------------------------------------

def finden(basis: Optional[Path] = None) -> Optional[tuple[Path, Path]]:
    """(Talker, Klang-Decoder) aus dem ersten passenden Ordner – oder None."""
    import config
    eigen = str(config.load().get("stimme_ordner") or "").strip()
    kandidaten = [Path(eigen)] if eigen else []
    for name in ORDNER:
        o = (basis or _INSTALL) / name
        if o.is_dir():
            kandidaten += sorted(p for p in o.iterdir() if p.is_dir())
    for d in kandidaten:
        talker = decoder = None
        for f in sorted(d.glob("*.gguf")):
            arch = _architektur(f)
            if arch == "qwen3-tts" and talker is None:
                talker = f
            elif arch == "qwen3-tts-tokenizer" and decoder is None:
                decoder = f
        if talker and decoder:
            return talker, decoder
    return None


def _architektur(f: Path) -> str:
    try:
        from ggufengine.gguf import GGUFFile
        g = GGUFFile(f)
        try:
            return str(g.architecture)
        finally:
            g.close()
    except Exception:
        return ""


def verfuegbar() -> bool:
    global _pfade
    if _pfade is None:
        _pfade = finden()
    return _pfade is not None


def _laden():
    """Talker + Decoder auf die GPU (einmal; danach aus dem Speicher)."""
    global _modell, _zuletzt
    with _lock:
        _zuletzt = time.time()
        if _modell is not None:
            return _modell
        if not verfuegbar():
            raise RuntimeError("Kein Stimm-Modell gefunden – ein Ordner unter Vision/ mit dem "
                               "Qwen3-TTS-Talker und dem 12-Hz-Tokenizer (GGUF) fehlt.")
        import torch
        from ggufengine.gguf import GGUFFile
        from ggufengine.models.qwen3tts import Qwen3TTSCodec, Qwen3TTSTalker
        dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if dev.type == "cuda" else torch.float32
        talker = Qwen3TTSTalker(GGUFFile(_pfade[0]), dev, dtype)
        codec = Qwen3TTSCodec(GGUFFile(_pfade[1]), dev)
        _modell = (talker, codec)
        _DBG.ereignis("Stimme geladen")
        _waechter_starten()
        return _modell


def geladen() -> bool:
    return _modell is not None


def freigeben() -> None:
    """GPU frei machen (wartet auf einen laufenden Satz)."""
    global _modell
    with _lock:
        if _modell is None:
            return
        _modell = None
        try:
            import gc
            import torch
            from ggufengine.models.common import GPU_SPERRE
            gc.collect()
            if torch.cuda.is_available():
                with GPU_SPERRE:
                    torch.cuda.empty_cache()
        except Exception:
            pass
        _DBG.ereignis("Stimme freigegeben")


def gpu_freigeben() -> None:
    """Beim Bildermalen: Stimme entladen, wenn sie gerade nicht spricht."""
    if not _zustand["spricht"]:
        freigeben()


def nach_dem_sprechen() -> None:
    """Nach dem letzten Satz: im Modus „knopf“ (und „aus“) das Modell sofort entladen; im Modus „an“ bleibt es
    FREIGABE_S warm, nur der Zwischenspeicher von torch geht zurück – der hielt sonst bis 2,5 GB, die kein
    anderer Prozess bekam."""
    if modus() != "an":
        freigeben()
        return
    try:
        import torch
        from ggufengine.models.common import GPU_SPERRE
        if torch.cuda.is_available():
            with GPU_SPERRE:
                torch.cuda.empty_cache()
        _DBG.ereignis("Stimme: Zwischenspeicher zurückgegeben")
    except Exception:
        pass


def _waechter_starten() -> None:
    def warten():
        while _modell is not None:
            time.sleep(30)
            if _modell is not None and not _zustand["spricht"] and time.time() - _zuletzt > FREIGABE_S:
                freigeben()
    threading.Thread(target=warten, daemon=True, name="stimme-freigabe").start()


# ---------------------------------------------------------------------------
# Stimme der Persönlichkeit
# ---------------------------------------------------------------------------

def _alte_datei(name: Optional[str] = None) -> Path:
    """Früherer Ort der Stimme (Profile/<Name>/stimme.json) – wird nur noch gelesen."""
    import profilordner
    return profilordner.ordner(name=name, anlegen=False) / "stimme.json"


def stimme(name: Optional[str] = None) -> dict:
    """Stimme der (aktiven) Persönlichkeit: `voice` der Charakter-Datei – sonst die Startstimme."""
    import charakter
    v = charakter.stimme_sauber(charakter.roh(name).get("voice"))
    if v is None:
        try:
            v = charakter.stimme_sauber(json.loads(_alte_datei(name).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            v = None
    if v is None:
        return dict(STARTSTIMME)
    return {"beschreibung": v["beschreibung"], "seed": v["seed"], "sprache": v["sprache"]}


def min_hz() -> float:
    import config
    try:
        return max(0.0, float(config.load().get("stimme_min_hz", MIN_HZ)))
    except (TypeError, ValueError):
        return float(MIN_HZ)


_PROBEN: dict[tuple[str, int], Optional[float]] = {}     # (Beschreibung, Seed) -> gemessene Stimmhöhe


def probe_merken(beschreibung: str, seed: int, hz: Optional[float]) -> None:
    _PROBEN[(" ".join(beschreibung.split()), int(seed))] = hz


def sperre(hz: Optional[float]) -> Optional[str]:
    """Grund, wenn die Stimmhöhe im Männerbereich liegt – sonst None."""
    grenze = min_hz()
    if grenze and hz is not None and hz < grenze:
        return (f"Stimmhöhe ≈ {hz:.0f} Hz – das ist der Männerbereich (Sperre unter {grenze:.0f} Hz). "
                "Die Stimme heller und klar weiblich beschreiben und einen neuen Seed nehmen.")
    return None


def stimme_festlegen(beschreibung: str, seed: int, sprache: str = "german", name: Optional[str] = None) -> Path:
    """Als `voice` in die Charakter-Datei – nur nach einer Probe über der Hz-Sperre."""
    import charakter
    beschreibung = " ".join(str(beschreibung or "").split())
    if not beschreibung:
        raise ValueError("Die Beschreibung der Stimme fehlt.")
    schluessel = (beschreibung, int(seed))
    if min_hz() and schluessel not in _PROBEN:
        raise ValueError("Erst eine Probe mit genau dieser Beschreibung und diesem Seed (stimme_probe) – "
                         "die Hz-Sperre prüft die Stimmhöhe.")
    if (grund := sperre(_PROBEN.get(schluessel))):
        raise ValueError(grund)
    voice = {"beschreibung": beschreibung, "seed": int(seed), "sprache": sprache or "german"}
    if _PROBEN.get(schluessel):
        voice["stimmhoehe_hz"] = round(_PROBEN[schluessel])
    p = charakter.stimme_setzen(voice, name)
    try:
        _alte_datei(name).unlink(missing_ok=True)      # nicht doppelt: die Stimme wohnt jetzt in `voice`
    except OSError:
        pass
    return p


# ---------------------------------------------------------------------------
# Text vorbereiten
# ---------------------------------------------------------------------------

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def sprechtext(text: str) -> str:
    """Markdown, Code, Links und Emojis so umformen, dass man es vorlesen kann."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)             # Code-Blöcke nicht vorlesen
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                        # Bilder
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                     # Links: nur der Text
    t = re.sub(r"https?://\S+", "ein Link", t)
    t = re.sub(r"^\s*\|?\s*:?-{3,}.*$", " ", t, flags=re.M)            # Tabellen-Trennzeilen
    t = t.replace("|", ", ")
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)
    t = re.sub(r"[*_~]{1,3}", "", t)
    t = _EMOJI.sub("", t)
    t = re.sub(r"\s*\n\s*\n\s*", ". ", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\s*[.,]\s*){2,}", ". ", t)
    return t.strip(" ,")


def saetze(text: str, grenze: int = MAX_SATZ) -> list[str]:
    """In Sätze teilen, zu Stücken bis `grenze` Zeichen bündeln."""
    teile = re.split(r"(?<=[.!?…])\s+", sprechtext(text))
    stuecke, akt = [], ""
    for s in teile:
        while len(s) > grenze:                                         # überlange Sätze am Komma teilen
            cut = max(s.rfind(", ", 0, grenze), s.rfind(" ", 0, grenze))
            cut = cut if cut > grenze // 3 else grenze
            if akt:
                stuecke.append(akt)
                akt = ""
            stuecke.append(s[:cut].strip())
            s = s[cut:].strip(" ,")
        if akt and len(akt) + 1 + len(s) > grenze:
            stuecke.append(akt)
            akt = s
        else:
            akt = f"{akt} {s}".strip()
    if akt:
        stuecke.append(akt)
    return [s for s in stuecke if re.search(r"\w", s)]


# ---------------------------------------------------------------------------
# Erzeugen und abspielen
# ---------------------------------------------------------------------------

def erzeugen(text: str, st: Optional[dict] = None, abbruch: Optional[Callable[[], bool]] = None):
    """Text -> Wellenform (numpy float32, 24 kHz)."""
    global _zuletzt
    st = st or stimme()
    with _rechnen:
        talker, codec = _laden()
        codes = talker.codes(text, st["beschreibung"], st.get("sprache", "german"), seed=st.get("seed"),
                             abbruch=abbruch)
        _zuletzt = time.time()
        if codes.shape[0] == 0:
            import numpy as np
            return np.zeros(0, dtype="float32")
        return codec.decode(codes).float().cpu().numpy()


def wav_schreiben(wav, ziel: Path) -> Path:
    import numpy as np
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ziel), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ABTASTRATE)
        w.writeframes((np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes())
    return ziel


def stimmhoehe(wav) -> Optional[float]:
    """Mittlere Grundfrequenz (Hz) der stimmhaften Abschnitte (Autokorrelation, 80–600 Hz).
    Gewählt wird die kürzeste Periode, die fast so gut passt wie die beste – sonst landet die
    Messung oft eine Oktave zu tief (doppelte Periode passt bei sauberen Tönen genauso gut)."""
    import numpy as np
    fr = int(0.04 * ABTASTRATE)
    lo, hi = ABTASTRATE // 600, ABTASTRATE // 80
    werte = []
    for i in range(0, len(wav) - fr, fr):
        s = np.asarray(wav[i:i + fr], dtype="float64")
        if np.sqrt((s ** 2).mean()) < 0.05:
            continue
        s = s - s.mean()
        a = np.correlate(s, s, "full")[fr - 1:]
        if a[0] <= 0:
            continue
        r = a[lo:hi] / a[0]
        beste = float(r.max())
        if beste < 0.3:                                # kaum periodisch: kein Stimmton
            continue
        k = int(np.argmax(r >= 0.85 * beste))
        while k + 1 < len(r) and r[k + 1] >= r[k]:      # bis zur Spitze dieser Periode
            k += 1
        d = 0.0
        if 0 < k < len(r) - 1:                          # Parabel durch die Spitze: Bruchteil der Periode
            nenner = r[k - 1] - 2 * r[k] + r[k + 1]
            d = 0.5 * (r[k - 1] - r[k + 1]) / nenner if nenner else 0.0
        werte.append(ABTASTRATE / (lo + k + d))
    return float(np.median(werte)) if werte else None


def abspielen(pfad: Path, dauer: float) -> None:
    """Asynchron abspielen; wartet vorher, bis das vorige Stück zu Ende ist."""
    global _spielt_bis
    import winsound
    rest = _spielt_bis - time.time()
    if rest > 0:
        time.sleep(rest)
    winsound.PlaySound(str(pfad), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    _spielt_bis = time.time() + dauer


def spricht() -> bool:
    return _zustand["spricht"] or time.time() < _spielt_bis


def _arbeiter() -> None:
    zaehler = 0
    while True:
        gen, text, st = _auftraege.get()
        if gen != _generation:
            continue
        _zustand["spricht"] = True
        try:
            for stueck in saetze(text):
                if gen != _generation:
                    break
                wav = erzeugen(stueck, st, abbruch=lambda: gen != _generation)
                if gen != _generation or not len(wav):
                    break
                zaehler += 1
                ziel = Path(tempfile.gettempdir()) / "NemiCLI-Stimme" / f"stueck_{zaehler % 4}.wav"
                abspielen(wav_schreiben(wav, ziel), len(wav) / ABTASTRATE)
            _zustand["fehler"] = ""
        except Exception as exc:                                       # Stimme ist Zugabe, nie Pflicht
            _zustand["fehler"] = str(exc)
        finally:
            _zustand["spricht"] = not _auftraege.empty()
            if not _zustand["spricht"]:
                nach_dem_sprechen()


def sagen(text: str, st: Optional[dict] = None) -> bool:
    """Text vorlesen (Hintergrund).  False bei Modus „aus“, ohne Modell oder ohne Text – dann lädt nichts."""
    global _faden
    if not AKTIV or modus() == "aus" or not str(text or "").strip() or not verfuegbar():
        return False
    with _lock:
        if _faden is None or not _faden.is_alive():
            _faden = threading.Thread(target=_arbeiter, daemon=True, name="stimme")
            _faden.start()
    _auftraege.put((_generation, str(text)[:MAX_VORLESEN], st or stimme()))
    return True


def stopp() -> bool:
    """Berechnung und Wiedergabe sofort beenden.  True, wenn etwas lief."""
    global _generation, _spielt_bis
    lief = spricht()
    _generation += 1
    while not _auftraege.empty():
        try:
            _auftraege.get_nowait()
        except queue.Empty:
            break
    _spielt_bis = 0.0
    try:
        import winsound
        winsound.PlaySound(None, 0)
    except Exception:
        pass
    return lief


def letzter_fehler() -> str:
    return _zustand["fehler"]


def schalter(name: str, vorgabe: bool) -> bool:
    import config
    v = config.load().get(name, vorgabe)
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "an", "ja", "yes")


def modus() -> str:
    """Stimm-Modus (`voice_modus`): „aus“ – nichts spricht, das Modell lädt nie, bis der Nutzer wieder
    einschaltet; „knopf“ (Vorgabe) – nur auf Anfrage (Werkzeug vorlesen, Kugel, /voice jetzt), danach
    sofort entladen; „an“ – jede Antwort wird vorgelesen. Alte Werte: True → an, False → aus
    (der Nutzer hatte abgeschaltet); ohne Eintrag zählt der alte Schalter vorlesen_auto."""
    import config
    v = config.load().get("voice_modus")
    if isinstance(v, str) and v.strip().lower() in MODI:
        return v.strip().lower()
    if v is True:
        return "an"
    if v is False:
        return "aus"
    return "an" if schalter("vorlesen_auto", False) else "knopf"


def modus_setzen(neu: str) -> str:
    """Modus speichern; „aus“ beendet Sprechen und gibt das Modell sofort frei."""
    import config
    neu = neu if neu in MODI else "knopf"
    config.update(voice_modus=neu)
    if neu == "aus":
        stopp()
        freigeben()
    return neu


def ordner_info() -> str:
    if not verfuegbar():
        return "kein Stimm-Modell gefunden"
    return f"{_pfade[0].parent} ({'geladen' if geladen() else 'bereit, lädt beim ersten Satz'})"


def _test_reset() -> None:                                             # nur für Tests
    global _pfade, _modell
    _pfade = None
    _modell = None
