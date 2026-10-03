from __future__ import annotations

from ..gguf import GGUFError, GGUFFile
from .gemma4 import Gemma4Model
from .bailing3 import BailingMoe3Model
from .baukasten import BaukastenModel
from .deepseek2 import DeepSeek2Model
from .qwen35 import Qwen35Model

_MODELS = (Qwen35Model, Gemma4Model, BailingMoe3Model, DeepSeek2Model, BaukastenModel)
SUPPORTED_ARCHS = tuple(a for m in _MODELS for a in m.arch_names)


def build_model(gg: GGUFFile, device, dtype, n_ctx: int, progress=None, quant: bool = True, kv_bits: int = 16,
                experten_vram: Optional[int] = None):
    arch = gg.architecture
    for cls in _MODELS:
        if arch in cls.arch_names:
            return cls(gg, device, dtype, n_ctx=n_ctx, progress=progress, quant=quant, kv_bits=kv_bits,
                       experten_vram=experten_vram)
    raise GGUFError(f"architecture {arch!r} is not supported (supported: {', '.join(SUPPORTED_ARCHS)})")
