"""Reranker im GGUF-Format (z. B. Qwen3-Reranker): Frage + Dokument -> Relevanz 0..1.

Ein Cross-Encoder: Frage und Dokument gehen zusammen durch das Modell (Vorlage aus
`tokenizer.chat_template.rerank`). Der Zustand des letzten Tokens nach der Schluss-Norm
läuft durch den Klassifikator-Kopf `cls.output` (Zeilen „yes“/„no“); die Relevanz ist
softmax(yes, no)[yes] – wie llama.cpp mit pooling_type 4 (rank).
"""
from __future__ import annotations

import re
from typing import List, Optional

import torch

from .gguf import GGUFError, GGUFFile
from .models import build_model
from .models.common import KV_GROWTH
from .qlinear import kernel_available
from .quant import dequantize
from .tokenizer import Tokenizer

POOLING_RANG = 4                   # llama.cpp: LLAMA_POOLING_TYPE_RANK
VORLAGE_STANDARD = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and "
                    "the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n"
                    "<|im_start|>user\n<Instruct>: Given a web search query, retrieve relevant passages that answer "
                    "the query\n<Query>: {query}\n<Document>: {document}<|im_end|>\n<|im_start|>assistant\n"
                    "<think>\n\n</think>\n\n")
DOKUMENT_MAX = 1536                # Token je Dokument, der Rest wird abgeschnitten


class Nachsortierer:
    def __init__(self, pfad: str, device: Optional[str] = None, max_tokens: int = 2048):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.max_tokens = max(64, min(int(max_tokens), KV_GROWTH - 8))
        self.gg = GGUFFile(pfad)
        try:
            a = self.gg.architecture
            if self.gg.get(f"{a}.pooling_type") != POOLING_RANG:
                raise GGUFError("pooling_type is not rank – not a reranker")
            labels = [str(x).strip().lower() for x in (self.gg.get(f"{a}.classifier.output_labels") or [])]
            ti = self.gg.tensors.get("cls.output.weight")
            dim = int(self.gg.require(f"{a}.embedding_length"))
            if ti is None or ti.shape != (2, dim):
                raise GGUFError("cls.output.weight (2 x embedding_length) missing – an incomplete reranker conversion")
            self.ja = labels.index("yes") if "yes" in labels else 0
            roh = torch.frombuffer(bytearray(self.gg.tensor_bytes(ti)), dtype=torch.uint8)
            self.kopf = dequantize(roh, ti.ggml_type, ti.n_elements).reshape(ti.shape).to(self.device)
            self.vorlage = str(self.gg.get("tokenizer.chat_template.rerank") or VORLAGE_STANDARD)
            if "{query}" not in self.vorlage or "{document}" not in self.vorlage:
                raise GGUFError("rerank template without {query}/{document}")
            self.tok = Tokenizer(self.gg)
            dtype = torch.bfloat16 if self.device.type == "cuda" else torch.float32
            self.model = build_model(self.gg, self.device, dtype, self.max_tokens + 2,
                                     quant=kernel_available(self.device))
            if not hasattr(self.model, "einbetten"):
                raise GGUFError(f"architecture {a!r} cannot be used as a reranker")
            self.model.graph = None
            self.arch = a
            self.name = str(self.gg.get("general.basename", "") or self.gg.get("general.name", "") or "")
        except BaseException:
            self.close()
            raise

    def _mit_marken(self, text: str, frage: str = "") -> List[int]:
        """Vorlagen-Text: Sondermarken der Vorlage (<|im_start|>, <think> …) als ihre eigenen Token,
        der Rest – auch die eingesetzte Frage – als gewöhnlicher Text."""
        marken = sorted((m for m in self.tok.special if m and m in self.vorlage), key=len, reverse=True)
        muster = re.compile("(" + "|".join(map(re.escape, marken)) + ")") if marken else None
        ids: List[int] = []
        for i, teil in enumerate(text.split("{query}")):
            if i:
                ids += self.tok.encode(frage)
            for stueck in (muster.split(teil) if muster else [teil]):
                if stueck in marken:
                    ids.append(self.tok.special_id(stueck))
                elif stueck:
                    ids += self.tok.encode(stueck)
        return ids

    def tokens(self, frage: str, dokument: str) -> List[int]:
        """Vorlage mit Frage und Dokument; ein zu langes Dokument wird gekürzt, die Vorlage bleibt ganz."""
        vorn, _, rest = self.vorlage.partition("{document}")
        vorn_ids = self._mit_marken(vorn, frage)
        hinten_ids = self._mit_marken(rest)
        platz = max(16, min(DOKUMENT_MAX, self.max_tokens - len(vorn_ids) - len(hinten_ids)))
        dok_ids = self.tok.encode(dokument)[:platz]
        return (vorn_ids + dok_ids + hinten_ids)[-self.max_tokens:]

    @torch.inference_mode()
    def bewerten(self, frage: str, dokumente: List[str]) -> List[float]:
        """Relevanz 0..1 je Dokument (in derselben Reihenfolge)."""
        raus = []
        for d in dokumente:
            h = self.model.einbetten(self.tokens(frage, d), "last")
            logits = self.kopf.float() @ h.float()
            raus.append(float(torch.softmax(logits, dim=-1)[self.ja]))
        return raus

    def close(self):
        self.model = None
        self.kopf = None
        try:
            import gc
            gc.collect()
            self.gg.close()
        except Exception:
            pass
