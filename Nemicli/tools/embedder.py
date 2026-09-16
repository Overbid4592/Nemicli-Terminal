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

#: Genau der Prompt aus config_sentence_transformers.json. Nur Suchanfragen
#: bekommen ihn vorangestellt, gespeicherte Notizen nicht ("document": "").
QUERY_PROMPT = ("Instruct: Given a web search query, retrieve relevant passages "
                "that answer the query\nQuery:")

#: Obergrenze fuer die Token-Laenge. Gedaechtnis-Notizen sind kurze Saetze;
#: die Grenze schuetzt nur davor, dass eine versehentlich riesige Notiz die
#: CPU minutenlang beschaeftigt.
MAX_TOKENS = 1024


class QwenEmbedder:
    """Dieselbe Aufrufform wie SentenceTransformer.encode(), nur ohne sklearn.

    Absichtlich kein Drop-in fuer ALLES - nur fuer das, was memory.py nutzt:
    encode(texte, normalize_embeddings=True, prompt_name="query"|None).
    """

    def __init__(self, pfad_oder_id: str, *, local_files_only: bool = False,
                 cache_folder: str | None = None, device: str = "cpu"):
        _sklearn_blockade_umgehen()        # muss VOR dem transformers-Import stehen
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.device = device
        kw: dict = {}
        if local_files_only:
            kw["local_files_only"] = True
        if cache_folder:
            kw["cache_dir"] = cache_folder

        self.tok = AutoTokenizer.from_pretrained(pfad_oder_id, **kw)
        self.model = AutoModel.from_pretrained(pfad_oder_id, **kw)
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
        if prompt_name == "query":
            texts = [QUERY_PROMPT + t for t in texts]

        raus = []
        with self._lock, torch.no_grad():
            for i in range(0, len(texts), batch_size):
                teil = texts[i:i + batch_size]
                enc = self.tok(teil, padding=True, truncation=True,
                               max_length=MAX_TOKENS, return_tensors="pt")
                enc = {k: v.to(self.device) for k, v in enc.items()}
                out = self.model(**enc)
                vec = self._letztes_token(out.last_hidden_state, enc["attention_mask"])
                if normalize_embeddings:
                    vec = torch.nn.functional.normalize(vec, p=2, dim=1)
                raus.extend(v.cpu().numpy() for v in vec)
        return raus

    def to(self, device):
        self.model.to(device)
        self.device = device
        return self
