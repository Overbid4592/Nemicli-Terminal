"""Embedding-Modelle im GGUF-Format (z. B. Qwen3-Embedding): Text -> normierter Vektor.

Das Modell setzt sich wie jedes Sprachmodell aus der Datei zusammen (Baukasten);
statt Logits wird der Zustand nach der Schluss-Norm gepoolt. Auf der GPU bleiben
die Gewichte gepackt (Q8_0 ~0,6 GB statt ~1,2 GB in bf16).
"""
from __future__ import annotations

from typing import List, Optional

import torch

from .gguf import GGUFError, GGUFFile
from .models import build_model
from .models.common import KV_GROWTH
from .qlinear import kernel_available
from .tokenizer import Tokenizer

# llama.cpp: {arch}.pooling_type – 1 Mittel, 2 erstes Token (CLS), 3 letztes Token
POOLING = {1: "mean", 2: "cls", 3: "last"}


class Einbetter:
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
            self.model = build_model(self.gg, self.device, dtype, self.max_tokens + 2,
                                     quant=kernel_available(self.device))
            if not hasattr(self.model, "einbetten"):
                raise GGUFError(f"architecture {a!r} cannot produce embeddings")
            self.model.graph = None                     # nur ganze Folgen, kein Einzeltoken-Graph
            self.dim = int(self.gg.require(f"{a}.embedding_length"))
            self.n_layer = int(self.gg.require(f"{a}.block_count"))
            self.n_vocab = self.model.n_vocab
            self.arch = a
            self.name = str(self.gg.get("general.basename", "") or self.gg.get("general.name", "") or "")
        except BaseException:
            self.close()
            raise

    def tokens(self, text: str) -> List[int]:
        ids = self.tok.encode(text)[:self.max_tokens]
        if self.add_eos and self.tok.eos_id is not None:
            ids.append(self.tok.eos_id)
        if not ids:
            raise ValueError("empty text")
        return ids

    def vektor(self, text: str, normieren: bool = True, pooling: Optional[str] = None) -> torch.Tensor:
        v = self.model.einbetten(self.tokens(text), pooling or self.pooling)
        return torch.nn.functional.normalize(v, dim=-1) if normieren else v

    def close(self):
        self.model = None
        try:
            import gc
            gc.collect()
            self.gg.close()
        except Exception:
            pass
