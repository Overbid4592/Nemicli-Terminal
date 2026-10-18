from __future__ import annotations

from typing import Optional

from .. import ausfuellen
from ..gguf import GGUFError, GGUFFile
from .gemma4 import Gemma4Model
from .bailing3 import BailingMoe3Model
from .baukasten import BaukastenModel
from .deepseek2 import DeepSeek2Model
from .hybrid import HybridModel
from .llama4 import Llama4Model
from .qwen35 import Qwen35Model
from .qwen3vl import Qwen3VLModel

_MODELS = (Qwen35Model, Qwen3VLModel, Gemma4Model, BailingMoe3Model, DeepSeek2Model, HybridModel, Llama4Model, BaukastenModel)
# Name der Steine-Datei in der Modell-Datenbank (daten/bauarten.json, Feld "klasse")
KLASSEN = {"qwen35": Qwen35Model, "qwen3vl": Qwen3VLModel, "gemma4": Gemma4Model, "bailing3": BailingMoe3Model,
           "deepseek2": DeepSeek2Model, "hybrid": HybridModel, "llama4": Llama4Model, "baukasten": BaukastenModel}
SUPPORTED_ARCHS = tuple(ausfuellen.bauarten())


def build_model(gg: GGUFFile, device, dtype, n_ctx: int, progress=None, quant: bool = True, kv_bits: int = 16,
                experten_vram: Optional[int] = None):
    arch = gg.architecture
    eintrag = ausfuellen.bauart(arch)
    cls = KLASSEN.get(eintrag["klasse"]) if eintrag else None
    if cls is not None and arch in cls.arch_names:
        return cls(gg, device, dtype, n_ctx=n_ctx, progress=progress, quant=quant, kv_bits=kv_bits,
                   experten_vram=experten_vram)
    raise GGUFError(f"architecture {arch!r} is not supported (supported: {', '.join(SUPPORTED_ARCHS)})")
