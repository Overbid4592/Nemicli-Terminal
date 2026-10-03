"""
embedder.py - Qwen3-Embedding ohne sentence-transformers.

WARUM ES DAS GIBT
`sentence-transformers` scheitert auf diesem PC nicht an sich selbst, sondern
an einem Import: es zieht `scikit-learn` mit, und sklearn bringt eine unsignierte
DLL mit (`sparsefuncs_fast`). Windows Smart App Control blockiert die - der
Import stirbt mit "Eine Anwendungssteuerungsrichtlinie hat diese Datei blockiert".
Smart App Control abzuschalten ist eine Einbahnstrasse (zurueck geht es nur mit
einer Windows-Neuinstallation), also wird hier nichts abgeschaltet.

Das Modell selbst braucht sklearn ueberhaupt nicht. sentence-transformers macht
fuer Qwen3-Embedding genau drei Dinge, und die stehen in den Konfigurationsdateien
des Modells:

    modules.json          Transformer -> Pooling -> Normalize
    1_Pooling/config.json "pooling_mode_lasttoken": true
    config_sentence_transformers.json   Query-Prompt fuer Suchanfragen

Also: Text durchs Modell, den Vektor des LETZTEN echten Tokens nehmen, auf
Laenge 1 normieren. Das ist alles. Dafuer reichen torch und transformers.

WICHTIG - die Vektoren muessen zu den ALTEN passen. Im Gedaechtnis liegen
bereits Vektoren, die sentence-transformers erzeugt hat. Waere diese Umsetzung
auch nur beim Pooling anders, wuerde die Aehnlichkeitssuche stillschweigend
Unsinn liefern. Deshalb prueft tests/test_embedder.py die neuen Vektoren gegen
die gespeicherten alten.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path


def _sklearn_blockade_umgehen() -> bool:
    """Setzt einen Platzhalter fuer sklearn, WENN das echte blockiert ist.

    `transformers` zieht sklearn beim Import mit - nicht weil es das Modell
    braucht, sondern fuer eine einzige Funktion in der Textgenerierung:

        generation/candidate_generator.py:29   from sklearn.metrics import roc_curve

    Davor steht zwar `if is_sklearn_available()`, aber diese Pruefung
    (import_utils.py:214) fragt nur, ob sklearn INSTALLIERT ist - nicht, ob es
    sich laden laesst. Bei blockierter DLL sagt sie True, und der Import darunter
    stirbt. Deshalb reicht es nicht, sklearn einfach nicht zu benutzen.

    Der Platzhalter fuellt genau die drei Namen, die transformers beim Import
    anfasst. Wird einer davon WIRKLICH aufgerufen, fliegt ein klarer Fehler statt
    eines falschen Ergebnisses - NemiCLI ruft keinen davon je auf (roc_curve
    gehoert zum spekulativen Dekodieren, das wir nicht nutzen; die anderen zu
    Trainings-Metriken).

    Laesst sich das echte sklearn laden, passiert hier NICHTS.
    """
    import importlib
    import importlib.machinery              # wird nicht von selbst mitgeladen
    import sys
    import types

    try:
        importlib.import_module("sklearn.metrics")
        return False                       # alles in Ordnung - Finger weg
    except Exception:
        pass

    sklearn = types.ModuleType("sklearn")
    sklearn.__nemicli_platzhalter__ = True  # damit man ihn als solchen erkennt
    sklearn.__path__ = []                  # als Paket ausweisen
    sklearn.__spec__ = importlib.machinery.ModuleSpec("sklearn", None, is_package=True)
    metrics = types.ModuleType("sklearn.metrics")
    metrics.__spec__ = importlib.machinery.ModuleSpec("sklearn.metrics", None)

    def _blockiert(*_a, **_k):
        raise NotImplementedError(
            "sklearn ist auf diesem PC durch Windows Smart App Control blockiert "
            "(unsignierte DLL). NemiCLI braucht es nicht - dieser Platzhalter "
            "existiert nur, damit transformers importiert werden kann.")

    for name in ("roc_curve", "f1_score", "matthews_corrcoef"):
        setattr(metrics, name, _blockiert)
    sklearn.metrics = metrics
    sys.modules["sklearn"] = sklearn
    sys.modules["sklearn.metrics"] = metrics
    return True

#: Der Such-Prompt von Qwen3-Embedding (steht dort in config_sentence_transformers.json).
#: Nur noch Vergleichswert für die Tests – geladen wird der Prompt aus dem Modell-Ordner.
QUERY_PROMPT = ("Instruct: Given a web search query, retrieve relevant passages "
                "that answer the query\nQuery:")

#: Modelltypen, die wie Sprachmodelle von links nach rechts lesen: der Satz steckt
#: im letzten Token. Alle anderen (BERT, XLM-R, MPNet …) mitteln über die Tokens.
_DECODER = {"qwen2", "qwen3", "llama", "mistral", "gemma", "gemma2", "gemma3", "phi3", "gpt2"}

#: Kennung der Vektoren aus der Zeit vor der Modellwahl, die von Qwen3-Embedding-0.6B
#: stammen. Noch ältere Installationen hatten andere Modelle (z. B. 768 Dimensionen) –
#: deshalb zählt ein Vektor ohne Kennung nur mit genau dieser Länge als Qwen-Vektor.
ALT_KENNUNG = "qwen3-1024-28-151669-last"
ALT_DIM = 1024


def alte_kennung(vec) -> str:
    """Kennung für einen Vektor ohne gespeicherte Kennung – nach seiner Länge."""
    n = len(vec or [])
    return ALT_KENNUNG if n == ALT_DIM else f"unbekannt-{n}"


def _json(pfad: Path) -> dict | list | None:
    try:
        import json
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


#: Such-Prompt je GGUF-Modellfamilie (`general.basename`) – GGUF-Dateien bringen
#: die sentence-transformers-Dateien nicht mit. `embedding.json` im Ordner geht vor.
_GGUF_PROMPTS = {"qwen3-embedding": QUERY_PROMPT}

_GGUF_KOPF: dict = {}


def gguf_datei(ordner) -> Path | None:
    """Die GGUF-Datei eines Modell-Ordners: genau eine .gguf und keine safetensors
    (ein Ordner mit model.safetensors läuft wie bisher über transformers)."""
    o = Path(ordner)
    if (o / "model.safetensors").is_file():
        return None
    try:
        dateien = sorted(d for d in o.glob("*.gguf") if d.is_file())
    except OSError:
        return None
    return dateien[0] if len(dateien) == 1 else None


def _gguf_kopf(datei: Path) -> dict:
    """Kennwerte aus dem Kopf der GGUF-Datei, je Datei (Pfad, Größe, Zeit) gemerkt."""
    st = datei.stat()
    schluessel = (str(datei), st.st_size, st.st_mtime)
    if schluessel not in _GGUF_KOPF:
        from ggufengine.einbettung import POOLING
        from ggufengine.gguf import GGUFFile
        g = GGUFFile(str(datei))
        try:
            a = g.architecture
            _GGUF_KOPF[schluessel] = {
                "arch": a,
                "dim": g.get(f"{a}.embedding_length", "?"),
                "schichten": g.get(f"{a}.block_count", "?"),
                "vokabular": len(g.get("tokenizer.ggml.tokens") or []) or "?",
                "pooling": POOLING.get(g.get(f"{a}.pooling_type"), ""),
                "familie": str(g.get("general.basename", "") or "").lower(),
                "kontext": g.get(f"{a}.context_length"),
            }
        finally:
            g.close()
    return _GGUF_KOPF[schluessel]


def einstellungen_lesen(ordner) -> dict:
    """Wie dieses Modell Vektoren bildet – aus seinen eigenen Dateien.

    Reihenfolge: `embedding.json` im Ordner (eigene Vorgaben) vor den
    sentence-transformers-Dateien (`1_Pooling/config.json`,
    `config_sentence_transformers.json`) vor der Vorgabe nach Modelltyp.
    Rückgabe: pooling (last|mean|cls), query_prompt, dokument_prompt, max_tokens."""
    o = Path(ordner)
    cfg = _json(o / "config.json") or {}
    e = {"pooling": "", "query_prompt": "", "dokument_prompt": "", "max_tokens": MAX_TOKENS}
    if (datei := gguf_datei(o)) is not None:
        kopf = _gguf_kopf(datei)
        e["pooling"] = kopf["pooling"] or "last"
        e["query_prompt"] = _GGUF_PROMPTS.get(kopf["familie"], "")
        cfg = {"model_type": kopf["arch"], "max_position_embeddings": kopf["kontext"]}
    pool = _json(o / "1_Pooling" / "config.json") or {}
    if pool.get("pooling_mode_lasttoken"):
        e["pooling"] = "last"
    elif pool.get("pooling_mode_cls_token"):
        e["pooling"] = "cls"
    elif pool.get("pooling_mode_mean_tokens"):
        e["pooling"] = "mean"
    prompts = (_json(o / "config_sentence_transformers.json") or {}).get("prompts") or {}
    e["query_prompt"] = prompts.get("query") or e["query_prompt"]
    e["dokument_prompt"] = prompts.get("document") or prompts.get("passage") or e["dokument_prompt"]
    eigen = _json(o / "embedding.json") or {}
    for k in ("pooling", "query_prompt", "dokument_prompt", "max_tokens"):
        if eigen.get(k) not in (None, ""):
            e[k] = eigen[k]
    if e["pooling"] not in ("last", "mean", "cls"):
        e["pooling"] = "last" if cfg.get("model_type") in _DECODER else "mean"
    grenze = cfg.get("max_position_embeddings")
    if isinstance(grenze, int) and grenze > 0:
        e["max_tokens"] = min(int(e["max_tokens"]), grenze)
    return e


def kennung(ordner) -> str:
    """Fingerabdruck des Modells für gespeicherte Vektoren: Bauart + Rechenweise.
    Vektoren mit anderer Kennung passen nicht zu diesem Modell."""
    if (datei := gguf_datei(ordner)) is not None:
        kopf = _gguf_kopf(datei)
        teile = [str(kopf[k]) for k in ("arch", "dim", "schichten", "vokabular")]
        return "-".join(teile + [einstellungen_lesen(ordner)["pooling"]])
    cfg = _json(Path(ordner) / "config.json") or {}
    teile = [str(cfg.get(k, "?")) for k in ("model_type", "hidden_size", "num_hidden_layers", "vocab_size")]
    return "-".join(teile + [einstellungen_lesen(ordner)["pooling"]])

#: Obergrenze fuer die Token-Laenge. Gedaechtnis-Notizen sind kurze Saetze;
#: die Grenze schuetzt nur davor, dass eine versehentlich riesige Notiz die
#: CPU minutenlang beschaeftigt.
MAX_TOKENS = 1024


def gpu_sperre():
    """Gemeinsame Sperre mit der CUDA-Graph-Aufnahme des GGUF-Motors im selben Prozess."""
    try:
        from ggufengine.models.common import GPU_SPERRE
        return GPU_SPERRE
    except Exception:
        return contextlib.nullcontext()


class Embedder:
    """Dieselbe Aufrufform wie SentenceTransformer.encode(), nur ohne sklearn –
    für jedes Embedding-Modell im transformers-Format (safetensors).

    Absichtlich kein Drop-in fuer ALLES - nur fuer das, was memory.py nutzt:
    encode(texte, normalize_embeddings=True, prompt_name="query"|None).
    """

    def __init__(self, ordner: str, *, local_files_only: bool = True, device: str = "cpu"):
        # Nur von der Platte: ohne diese Schalter fragt transformers bei jedem
        # Laden online nach und hängt ohne Netz in Timeouts.
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        config_pruefen(ordner)
        _sklearn_blockade_umgehen()        # muss VOR dem transformers-Import stehen
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.device = device
        self.einstellungen = einstellungen_lesen(ordner)
        self.kennung = kennung(ordner)
        # use_safetensors: nie pickle-Dateien laden – die können beim Laden Code ausführen.
        kw: dict = {"local_files_only": True} if local_files_only else {}
        pfad_oder_id = ordner

        self.tok = AutoTokenizer.from_pretrained(pfad_oder_id, **kw)
        # Auf der GPU in bfloat16: halber VRAM (~1,1 statt 2,3 GB), gut doppelt so
        # schnell, Vektoren zu den gespeicherten float32-Vektoren cos > 0,999.
        # Direkt so laden, sonst entsteht beim Umwandeln kurz die float32-Spitze.
        kw["dtype"] = torch.bfloat16 if str(device).startswith("cuda") else torch.float32
        self.model = AutoModel.from_pretrained(pfad_oder_id, use_safetensors=True, **kw)
        self.model.eval()
        self.model.to(device)
        # Ein Thread pro Kern waere hier kontraproduktiv: das Einbetten laeuft
        # nebenher, waehrend das Sprachmodell antwortet.
        self._lock = threading.RLock()

    # -- Pooling ----------------------------------------------------------
    def _letztes_token(self, hidden, mask):
        """Der Vektor des letzten ECHTEN Tokens je Text.

        Nicht einfach hidden[:, -1]: bei rechtsseitigem Auffuellen stehen dort
        Fuell-Token. Die Maske sagt, wo der Text wirklich endet.
        """
        laengen = mask.sum(dim=1) - 1                     # Index des letzten Tokens
        idx = laengen.clamp(min=0).to(hidden.device)
        return hidden[self._torch.arange(hidden.size(0), device=hidden.device), idx]

    def _mittel(self, hidden, mask):
        """Mittelwert über die echten Tokens (Fülltokens zählen nicht)."""
        m = mask.unsqueeze(-1).to(hidden.dtype)
        return (hidden * m).sum(dim=1) / m.sum(dim=1).clamp(min=1e-9)

    def _poolen(self, hidden, mask):
        art = self.einstellungen["pooling"]
        if art == "cls":
            return hidden[:, 0]
        if art == "mean":
            return self._mittel(hidden, mask)
        return self._letztes_token(hidden, mask)

    # -- oeffentlich ------------------------------------------------------
    def encode(self, texts, *, normalize_embeddings: bool = True,
               prompt_name: str | None = None, batch_size: int = 8, **_ignored):
        """Gibt eine Liste von Vektoren zurueck (je ein numpy-Array wie bei ST)."""
        torch = self._torch
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        if not texts:
            return []
        vorsatz = (self.einstellungen["query_prompt"] if prompt_name == "query"
                   else self.einstellungen["dokument_prompt"])
        if vorsatz:
            texts = [vorsatz + t for t in texts]

        raus = []
        with self._lock, torch.no_grad():
            for i in range(0, len(texts), batch_size):
                teil = texts[i:i + batch_size]
                enc = self.tok(teil, padding=True, truncation=True,
                               max_length=int(self.einstellungen["max_tokens"]), return_tensors="pt")
                gpu = str(self.device).startswith("cuda")
                with (gpu_sperre() if gpu else contextlib.nullcontext()):
                    enc = {k: v.to(self.device) for k, v in enc.items()}
                    out = self.model(**enc)
                    vec = self._poolen(out.last_hidden_state, enc["attention_mask"]).float()
                    if normalize_embeddings:              # float32: numpy kennt kein bfloat16
                        vec = torch.nn.functional.normalize(vec, p=2, dim=1)
                    raus.extend(v.cpu().numpy() for v in vec)
        return raus

    def to(self, device):
        dt = self._torch.bfloat16 if str(device).startswith("cuda") else self._torch.float32
        with gpu_sperre():
            self.model.to(device=device, dtype=dt)
        self.device = device
        return self


class GGUFEmbedder:
    """Embedding-Modell als GGUF über den eigenen Motor. Auf der GPU bleiben die
    Gewichte gepackt; es bleibt auf dem Gerät, auf dem es geladen wurde (`to` wirkt
    nicht – auf der CPU wären die Gewichte entpackt)."""

    def __init__(self, ordner: str, *, device: str = "cuda", **_ignored):
        from ggufengine.einbettung import Einbetter
        datei = gguf_datei(ordner)
        if datei is None:
            raise FileNotFoundError(f"keine eindeutige .gguf in {ordner}")
        self.einstellungen = einstellungen_lesen(ordner)
        self.kennung = kennung(ordner)
        gpu = str(device).startswith("cuda")
        with (gpu_sperre() if gpu else contextlib.nullcontext()):
            self._e = Einbetter(str(datei), "cuda" if gpu else "cpu",
                                max_tokens=int(self.einstellungen["max_tokens"]))
        self.device = self._e.device.type
        self._lock = threading.RLock()

    def encode(self, texts, *, normalize_embeddings: bool = True,
               prompt_name: str | None = None, **_ignored):
        if isinstance(texts, str):
            texts = [texts]
        vorsatz = (self.einstellungen["query_prompt"] if prompt_name == "query"
                   else self.einstellungen["dokument_prompt"])
        gpu = self.device == "cuda"
        raus = []
        with self._lock:
            for t in texts:
                with (gpu_sperre() if gpu else contextlib.nullcontext()):
                    v = self._e.vektor((vorsatz or "") + t, normieren=normalize_embeddings,
                                       pooling=self.einstellungen["pooling"])
                    raus.append(v.cpu().numpy())
        return raus

    def to(self, device):
        return self

    def close(self):
        with self._lock:
            self._e.close()


def config_pruefen(ordner: str) -> None:
    """Lehnt Modell-Konfigurationen ab, die transformers Code nachladen ließen: eigener Code
    (`auto_map`) oder eine Attention-Implementierung als Hub-Verweis (`_attn_implementation*`
    mit „/“, CVE-2026-4372). Zweite Sicherung neben dem Offline-Betrieb."""
    import json

    def suchen(wert, pfad):
        if isinstance(wert, dict):
            for k, v in wert.items():
                if k == "auto_map" or (k.startswith("_attn_implementation") and isinstance(v, str) and "/" in v):
                    raise RuntimeError(f"{pfad}: unsichere Einstellung „{k}“ – Modell wird nicht geladen.")
                suchen(v, pfad)
        elif isinstance(wert, list):
            for v in wert:
                suchen(v, pfad)
    basis = Path(ordner)
    for datei in (basis.glob("*.json") if basis.is_dir() else ()):
        try:
            inhalt = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        suchen(inhalt, datei.name)


def laden(ordner: str, *, device: str = "cpu", local_files_only: bool = True):
    """Passender Encoder für den Ordner: GGUF-Datei -> eigener Motor, sonst transformers."""
    if gguf_datei(ordner) is not None:
        return GGUFEmbedder(ordner, device=device)
    return Embedder(ordner, device=device, local_files_only=local_files_only)


# Früherer Name – ältere Aufrufer und Tests.
QwenEmbedder = Embedder
