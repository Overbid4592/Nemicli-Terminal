"""Embedding-Modelle im GGUF-Format (z. B. Qwen3-Embedding): Text -> normierter Vektor.

Das Modell setzt sich wie jedes Sprachmodell aus der Datei zusammen (Baukasten);
statt Logits wird der Zustand nach der Schluss-Norm gepoolt. Auf der GPU bleiben
die Gewichte gepackt (Q8_0 ~0,6 GB statt ~1,2 GB in bf16).
"""
from __future__ import annotations

import contextlib
from typing import List, Optional

import torch

from .gguf import GGUFError, GGUFFile
from .models import build_model
from .models.common import GPU_SPERRE, KV_GROWTH
from .qlinear import kernel_available
from .tokenizer import Tokenizer

# llama.cpp: {arch}.pooling_type – 1 Mittel, 2 erstes Token (CLS), 3 letztes Token
POOLING = {1: "mean", 2: "cls", 3: "last"}


class Einbetter:
    add_bos = False                 # <bos> vor jede Folge (Bauarten mit `mit_bos`, z. B. EmbeddingGemma)

    def __init__(self, pfad: str, device: Optional[str] = None, max_tokens: int = 1024):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.max_tokens = max(8, min(int(max_tokens), KV_GROWTH - 8))
        self.gg = GGUFFile(pfad)
        try:
            a = self.gg.architecture
            art = self.gg.get(f"{a}.pooling_type")
            if art not in POOLING:
                raise GGUFError("no supported pooling_type – not an embedding model")
            self.pooling = POOLING[art]
            self.tok = Tokenizer(self.gg)
            self.add_eos = bool(self.gg.get("tokenizer.ggml.add_eos_token", False))
            dtype = torch.bfloat16 if self.device.type == "cuda" else torch.float32
            with GPU_SPERRE:                        # nicht neben dem Bau einer Engine (KV_GPU_SLOTS, SCHICHTEN_RAM)
                self.model = build_model(self.gg, self.device, dtype, self.max_tokens + 2,
                                         quant=kernel_available(self.device))
            if not hasattr(self.model, "einbetten"):
                raise GGUFError(f"architecture {a!r} cannot produce embeddings")
            self.model.graph = None                     # nur ganze Folgen, kein Einzeltoken-Graph
            self.dim = int(getattr(self.model, "dim_out", 0) or self.gg.require(f"{a}.embedding_length"))
            self.add_bos = bool(getattr(self.model, "mit_bos", False))
            self.n_layer = int(self.gg.require(f"{a}.block_count"))
            self.n_vocab = self.model.n_vocab
            self.arch = a
            self.name = str(self.gg.get("general.basename", "") or self.gg.get("general.name", "") or "")
        except BaseException:
            self.close()
            raise

    def tokens(self, text: str) -> List[int]:
        bos = self.add_bos and self.tok.bos_id is not None
        ids = self.tok.encode(text)[:self.max_tokens - bos]
        if bos:
            ids.insert(0, self.tok.bos_id)
        if self.add_eos and self.tok.eos_id is not None:
            ids.append(self.tok.eos_id)
        if not ids:
            raise ValueError("empty text")
        return ids

    def vektor(self, text: str, normieren: bool = True, pooling: Optional[str] = None) -> torch.Tensor:
        v = self.model.einbetten(self.tokens(text), pooling or self.pooling)
        return torch.nn.functional.normalize(v, dim=-1) if normieren else v

    def vektoren(self, texte: List[str], normieren: bool = True, pooling: Optional[str] = None,
                 sperre=None) -> List[torch.Tensor]:
        """Vektoren für mehrere Texte in Eingabe-Reihenfolge. Kurze Texte laufen gepackt in einem Durchgang
        (bis `stapel_platz` Token). `sperre`: Kontext je Durchgang (CUDA-Graph-Sperre des Motors)."""
        art = pooling or self.pooling
        folgen = [self.tokens(t) for t in texte]
        platz = self.model.stapel_platz() if hasattr(self.model, "stapel_platz") else 0
        packen, belegt = [], 0                          # Indizes je Durchgang, Token im letzten
        for i, f in enumerate(folgen):
            if packen and belegt + len(f) <= platz:
                packen[-1].append(i)
                belegt += len(f)
            else:
                packen.append([i])
                belegt = len(f)
        raus: List[Optional[torch.Tensor]] = [None] * len(folgen)
        for gruppe in packen:
            with (sperre() if sperre else contextlib.nullcontext()):
                if len(gruppe) == 1:
                    vecs = [self.model.einbetten(folgen[gruppe[0]], art)]
                else:
                    vecs = self.model.einbetten_stapel([folgen[i] for i in gruppe], art)
            for i, v in zip(gruppe, vecs):
                raus[i] = torch.nn.functional.normalize(v, dim=-1) if normieren else v
        return raus

    # -- Bilder (Modelle mit Bild-Teil, z. B. EmbeddingGemma 2 + mmproj) -------------------------------
    def sehen_laden(self, mmproj: str) -> None:
        """Bild-Teil (mmproj) dazuladen; danach liefert `bild_vektor` Vektoren im selben Raum wie Text."""
        if not getattr(self.model, "supports_images", False) or not hasattr(self.model, "eingabe"):
            raise GGUFError(f"architecture {self.arch!r} cannot embed images")
        from .vision import load_projector
        sehen = load_projector(mmproj, self.device, torch.bfloat16 if self.device.type == "cuda" else torch.float32)
        marken = [self.tok.special.get(m) for m in getattr(sehen, "marks", ())]
        if len(marken) != 3 or None in marken:
            raise GGUFError("the tokenizer lacks the image marks of this projector")
        sehen.load()
        self._sehen, self._bild_marken = sehen, marken

    def kann_sehen(self) -> bool:
        return getattr(self, "_sehen", None) is not None

    def bild_vektor(self, bild, normieren: bool = True) -> torch.Tensor:
        """Bild (Pfad oder PIL) -> Vektor: <bos> <|image> Bild-Token <image|> <eos>, Bild-Zeilen aus dem
        Bild-Teil statt der Platzhalter, Mittel über alle Zeilen."""
        if not self.kann_sehen():
            raise RuntimeError("no vision projector loaded (sehen_laden)")
        from PIL import Image
        img = bild if isinstance(bild, Image.Image) else Image.open(bild)
        with torch.inference_mode():
            emb = self._sehen.encode(img)
        auf, platz, zu = self._bild_marken
        n = int(emb.shape[0])
        ids = [self.tok.bos_id, auf] + [platz] * n + [zu, self.tok.eos_id]
        rows = torch.arange(2, 2 + n, dtype=torch.long)
        v = self.model.einbetten(ids, self.pooling, inject=(rows, emb))
        return torch.nn.functional.normalize(v, dim=-1) if normieren else v

    def close(self):
        sehen, self._sehen = getattr(self, "_sehen", None), None
        if sehen is not None:
            sehen.unload()
        self.model = None
        try:
            import gc
            gc.collect()
            self.gg.close()
        except Exception:
            pass
