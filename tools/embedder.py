"""
embedder.py - Qwen3-Embedding ohne sentence-transformers.

sentence-transformers zieht scikit-learn mit, dessen unsignierte DLL Smart App Control
blockieren kann. Nötig ist nur, was die Modell-Konfiguration vorgibt (modules.json,
1_Pooling/config.json, config_sentence_transformers.json): Text durchs Modell, Pooling
(letztes echtes Token), auf Länge 1 normieren – dafür reichen torch und transformers.
Die Vektoren müssen zu den gespeicherten passen (eigener Test).
"""

from __future__ import annotations

import contextlib
import re
import threading
from pathlib import Path


def _sklearn_blockade_umgehen() -> bool:
    """Setzt einen Platzhalter für sklearn, wenn das echte sich nicht laden lässt.

    transformers importiert sklearn (roc_curve, spekulatives Dekodieren), sobald es
    installiert ist – auch wenn die DLL blockiert ist. Der Platzhalter füllt nur die Namen,
    die transformers beim Import anfasst; ein echter Aufruf wirft einen klaren Fehler.
    Lässt sich sklearn laden, passiert nichts.
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

#: Suchanweisung je Aufgabe für Modelle mit der Qwen-Vorlage („Instruct: …\nQuery:“). Englisch, wie vom
#: Modell empfohlen. Gilt nur für die Suchfrage – gespeicherte Vektoren bleiben gültig.
AUFGABEN = {
    "gedaechtnis": "Given a message from a conversation, retrieve notes, earlier conversation passages "
                   "and documents that help to answer or continue it",
    "notizen": "Given a question or message, retrieve stored facts and notes about the same topic",
    "chat": "Given a question or message, retrieve earlier conversation passages about the same topic",
    "dokumente": "Given a question, retrieve document passages that answer it",
    "code": "Given a programming question, retrieve code snippets and instructions that solve it",
}
_QWEN_VORLAGE = re.compile(r"Instruct: .+\nQuery: ?", re.S)


#: EmbeddingGemma („task: … | query: “): Aufgaben aus der Modellkarte; Code hat eine eigene, sonst Suche.
_GEMMA_VORLAGE = re.compile(r"task: [^|]+\| query: ?")
_GEMMA_AUFGABEN = {"code": "code retrieval"}


def such_prompt(einstellungen: dict, aufgabe: str | None = None) -> str:
    """Vorsatz einer Suchfrage: bei Qwen- und Gemma-Vorlage die Anweisung der Aufgabe, sonst die des Modells.
    Ein eigener Such-Prompt (embedding.json) bleibt, wie er ist."""
    p = einstellungen.get("query_prompt") or ""
    if aufgabe not in AUFGABEN or einstellungen.get("prompt_eigen"):
        return p
    if _QWEN_VORLAGE.fullmatch(p):
        return f"Instruct: {AUFGABEN[aufgabe]}\nQuery:"
    if _GEMMA_VORLAGE.fullmatch(p):
        return f"task: {_GEMMA_AUFGABEN.get(aufgabe, 'search result')} | query: "
    return p


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
_GGUF_PROMPTS = {"qwen3-embedding": QUERY_PROMPT, "embeddinggemma": "task: search result | query: "}
_GGUF_DOK_PROMPTS = {"embeddinggemma": "title: none | text: "}

_GGUF_KOPF: dict = {}


def _ist_mmproj(p: Path) -> bool:
    return "mmproj" in p.name.lower()


def gguf_datei(ordner) -> Path | None:
    """Die GGUF-Datei eines Modell-Ordners: genau eine .gguf (ohne Bild-/Audio-Teil mmproj) und keine
    safetensors (ein Ordner mit model.safetensors läuft wie bisher über transformers)."""
    o = Path(ordner)
    if (o / "model.safetensors").is_file():
        return None
    try:
        dateien = sorted(d for d in o.glob("*.gguf") if d.is_file() and not _ist_mmproj(d))
    except OSError:
        return None
    return dateien[0] if len(dateien) == 1 else None


def mmproj_datei(ordner) -> Path | None:
    """Der Bild-/Audio-Teil im Modell-Ordner (genau eine mmproj-….gguf), sonst None."""
    try:
        dateien = sorted(d for d in Path(ordner).glob("*.gguf") if d.is_file() and _ist_mmproj(d))
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
                "dim": g.get(f"{a}.embedding_length_out") or g.get(f"{a}.embedding_length", "?"),
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
    Rückgabe: pooling (last|mean|cls), query_prompt, dokument_prompt, max_tokens, prompt_eigen
    (Such-Prompt aus embedding.json)."""
    o = Path(ordner)
    cfg = _json(o / "config.json") or {}
    e = {"pooling": "", "query_prompt": "", "dokument_prompt": "", "max_tokens": MAX_TOKENS}
    if (datei := gguf_datei(o)) is not None:
        kopf = _gguf_kopf(datei)
        e["pooling"] = kopf["pooling"] or "last"
        e["query_prompt"] = _GGUF_PROMPTS.get(kopf["familie"], "")
        e["dokument_prompt"] = _GGUF_DOK_PROMPTS.get(kopf["familie"], "")
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
    e["prompt_eigen"] = eigen.get("query_prompt") not in (None, "")
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


#: Kosinus-Schwellen der Suche. Jedes Modell verteilt seine Ähnlichkeiten anders: bei Qwen3-Embedding
#: liegen unpassende Texte meist bei 0,1–0,3, bei EmbeddingGemma 2 bei 0,45–0,75.
#:   kandidat      ab hier zählt ein Brocken für die Bedeutungs-Suche
#:   schwach       bester Treffer darunter (ohne Stichwort): die Suche hat wohl nichts gefunden
#:   doppelt       Beinahe-Kopie, fällt aus der Trefferliste
#:   verschmelzen  /aufraeumen legt zwei Notizen zusammen (ohne KI)
#:   pflege        ab hier legt die Gedächtnis-Pflege ein Paar der KI zur Prüfung vor
#:   vorgeschmack  ein Treffer nur nach Bedeutung (ohne Stichwort) kommt erst ab hier ungefragt in den Prompt
SCHWELLEN_VORGABE = {"kandidat": 0.315, "schwach": 0.5, "doppelt": 0.95, "verschmelzen": 0.90, "pflege": 0.70,
                     "vorgeschmack": 0.315}
#: Je Kennungs-Anfang. Gemessen auf deutsch/englischem Satz aus Notizen, Chat-Stellen, langen
#: Brocken und Bildern (Bildsuche 6/6), so gewählt, dass Trefferquote und Fehlerquote denen von
#: Qwen3-Embedding mit der Vorgabe entsprechen.
SCHWELLEN = {
    "gemma-embedding2-": {"kandidat": 0.66, "schwach": 0.71, "doppelt": 0.97, "verschmelzen": 0.93,
                          "pflege": 0.85, "vorgeschmack": 0.71},
}


def schwellen(kennung: str | None) -> dict:
    """Die Schwellen zum Modell mit dieser Kennung (unbekannt: SCHWELLEN_VORGABE)."""
    for anfang, werte in SCHWELLEN.items():
        if (kennung or "").startswith(anfang):
            return {**SCHWELLEN_VORGABE, **werte}
    return dict(SCHWELLEN_VORGABE)


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
        Fuell-Token. Die Maske sagt, wo der Text wirklich endet – auch bei
        linksseitigem Auffuellen (so liefern manche Tokenizer aus).
        """
        stellen = self._torch.arange(mask.size(1), device=mask.device)
        idx = (mask.long() * stellen).argmax(dim=1).to(hidden.device)   # letzte Stelle mit Maske 1
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
               prompt_name: str | None = None, batch_size: int = 8, aufgabe: str | None = None, **_ignored):
        """Gibt eine Liste von Vektoren zurueck (je ein numpy-Array wie bei ST).
        `aufgabe` (AUFGABEN) wählt bei Suchfragen die Anweisung."""
        torch = self._torch
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        if not texts:
            return []
        vorsatz = (such_prompt(self.einstellungen, aufgabe) if prompt_name == "query"
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
    """Embedding-Modell als GGUF über den eigenen Motor. Kurze Texte rechnen gepackt in einem
    Durchgang. `to` auf ein anderes Gerät lädt eine zweite Fassung dazu (auf der GPU bleiben die
    Gewichte gepackt); `to` zurück schließt sie wieder, die Grundfassung bleibt geladen."""

    def __init__(self, ordner: str, *, device: str = "cuda", **_ignored):
        self._datei = gguf_datei(ordner)
        if self._datei is None:
            raise FileNotFoundError(f"keine eindeutige .gguf in {ordner}")
        self.einstellungen = einstellungen_lesen(ordner)
        self.kennung = kennung(ordner)
        self._lock = threading.RLock()
        self._grund = self._bauen(device)
        self._zweit = None
        self.device = self._grund.device.type

    def _bauen(self, device):
        from ggufengine.einbettung import Einbetter
        gpu = str(device).startswith("cuda")
        with (gpu_sperre() if gpu else contextlib.nullcontext()):
            return Einbetter(str(self._datei), "cuda" if gpu else "cpu",
                             max_tokens=int(self.einstellungen["max_tokens"]))

    def encode(self, texts, *, normalize_embeddings: bool = True,
               prompt_name: str | None = None, aufgabe: str | None = None, **_ignored):
        if isinstance(texts, str):
            texts = [texts]
        vorsatz = (such_prompt(self.einstellungen, aufgabe) if prompt_name == "query"
                   else self.einstellungen["dokument_prompt"])
        with self._lock:
            e = self._zweit or self._grund
            vecs = e.vektoren([(vorsatz or "") + t for t in texts], normieren=normalize_embeddings,
                              pooling=self.einstellungen["pooling"],
                              sperre=gpu_sperre if e.device.type == "cuda" else None)
        return [v.cpu().numpy() for v in vecs]

    def kann_sehen(self) -> bool:
        """Bilder einbetten möglich: Bauart mit Bild-Eingang und ein mmproj im Modell-Ordner."""
        return (getattr(self._grund.model, "supports_images", False) and hasattr(self._grund.model, "eingabe")
                and mmproj_datei(self._datei.parent) is not None)

    def bilder(self, pfade, normalize_embeddings: bool = True) -> list:
        """Bilder (Pfade) -> Vektoren im selben Raum wie die Texte; None für ein Bild, das sich nicht lesen
        lässt. Der Bild-Teil wird beim ersten Mal dazugeladen."""
        if not self.kann_sehen():
            raise RuntimeError("dieses Embedding-Modell kann keine Bilder einbetten")
        raus = []
        with self._lock:
            e = self._zweit or self._grund
            if not e.kann_sehen():
                with (gpu_sperre() if e.device.type == "cuda" else contextlib.nullcontext()):
                    e.sehen_laden(str(mmproj_datei(self._datei.parent)))
            for p in pfade:
                try:
                    with (gpu_sperre() if e.device.type == "cuda" else contextlib.nullcontext()):
                        raus.append(e.bild_vektor(str(p), normieren=normalize_embeddings).cpu().numpy())
                except Exception:
                    raus.append(None)
        return raus

    def to(self, device):
        ziel = "cuda" if str(device).startswith("cuda") else "cpu"
        with self._lock:
            if ziel == self._grund.device.type:
                alt, self._zweit = self._zweit, None
                if alt is not None:
                    alt.close()
            elif self._zweit is None:
                self._zweit = self._bauen(ziel)
            self.device = (self._zweit or self._grund).device.type
        return self

    def close(self):
        with self._lock:
            for e in (self._zweit, self._grund):
                if e is not None:
                    e.close()
            self._zweit = None


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
