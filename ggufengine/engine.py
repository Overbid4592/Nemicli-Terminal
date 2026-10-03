"""High-level API: load a GGUF model and generate text."""
from __future__ import annotations

import itertools
import sys
import time
from typing import Iterator, List, Optional

import torch

from . import debuglog as dbg
from .chat import ChatFormatter, Message, detect_format
from .gguf import GGUFFile
from .models import build_model
from .models.common import GPU_SPERRE, PREFILL_CHUNK
from .qlinear import UNPACK_FROM
from .sampling import Sampler, SamplerConfig
from .tokenizer import StreamDecoder, Tokenizer

RELEASE_FROM = UNPACK_FROM   # reads this long allocate scratch worth releasing afterwards

_BILD_NR = itertools.count(1)


def _bild_nicht_teilen(keys: list, anfang: int, ende: int) -> int:
    """Ende eines Lese-Stücks so vorziehen, dass kein Bild (Schlüssel ("img", nr, zeile))
    über die Grenze läuft."""
    if ende >= len(keys):
        return ende
    a, b = keys[ende - 1], keys[ende]
    if not (isinstance(a, tuple) and isinstance(b, tuple) and a[1] == b[1]):
        return ende
    j = ende - 1
    while j > anfang and isinstance(keys[j - 1], tuple) and keys[j - 1][1] == a[1]:
        j -= 1
    if j == anfang:
        raise ValueError("image has more tokens than one forward call can read")
    return j


def bild_nr(img) -> int:
    """Feste Nummer eines Bild-Embeddings für den Präfix-Cache. `id()` taugt nicht:
    Python vergibt die Kennung eines freigegebenen Objekts sofort neu, ein neues
    Bild sähe dann aus wie das alte an derselben Stelle."""
    nr = getattr(img, "bild_nr", None)
    if nr is None:
        nr = next(_BILD_NR)
        img.bild_nr = nr
    return nr


_DTYPES = {"bf16": torch.bfloat16, "bfloat16": torch.bfloat16, "fp16": torch.float16,
           "float16": torch.float16, "fp32": torch.float32, "float32": torch.float32}


def _move_tensors(root, select, convert) -> set:
    """Replaces every tensor reachable from `root` (attributes, __slots__, lists, tuples,
    dicts of engine objects) for which `select(t)` holds by `convert(t)`.  A tensor
    referenced twice is converted once and stays shared.  Returns the ids of the new
    tensors."""
    memo: dict = {}
    seen: set = set()

    def conv(v):
        if isinstance(v, torch.Tensor):
            if not select(v):
                return v
            if id(v) not in memo:
                memo[id(v)] = convert(v)
            return memo[id(v)]
        if isinstance(v, list):
            v[:] = [conv(x) for x in v]
            return v
        if isinstance(v, tuple):
            new = tuple(conv(x) for x in v)
            return v if all(a is b for a, b in zip(new, v)) else new
        if isinstance(v, dict):
            for k in list(v):
                v[k] = conv(v[k])
            return v
        if type(v).__module__.startswith(__package__) and id(v) not in seen:
            walk(v)
        return v

    def walk(obj):
        seen.add(id(obj))
        names = list(getattr(obj, "__dict__", {}))
        for cls in type(obj).__mro__:
            names += [n for n in getattr(cls, "__slots__", ()) if n not in names]
        for name in names:
            if hasattr(obj, name):
                old = getattr(obj, name)
                new = conv(old)
                if new is not old:                  # frozen objects are only read
                    setattr(obj, name, new)

    walk(root)
    return {id(t) for t in memo.values()}


class Engine:
    def __init__(self, path: str, device: Optional[str] = None, dtype: str = "bf16",
                 n_ctx: int = 8192, thinking: bool = False, verbose: bool = True, weights: str = "quant",
                 progress=None, max_tok_s: Optional[float] = None, kv_bits: int = 16,
                 experts_vram: Optional[int] = None):
        """`denk_budget` (attribute): max. thinking tokens per answer in chat() (None: no limit).
        progress: optional callback(fraction 0..1) for loading progress (used by the GUI).
        max_tok_s: upper limit for generated tokens per second (None = as fast as possible);
        the GPU idles between steps instead of running flat out.
        kv_bits: 16 = KV cache in the compute dtype, 8 = int8 with a scale per 32 values
        (about half the VRAM per token of context).
        experts_vram: VRAM budget in bytes for MoE experts (None: all in VRAM, 0: all in pinned
        RAM); experts beyond it stay in RAM and the GPU reads the chosen ones over PCIe."""
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        if dtype not in _DTYPES:
            raise ValueError(f"unknown dtype {dtype!r}")
        self.dtype = _DTYPES[dtype]
        if self.device.type == "cpu" and self.dtype == torch.float16:
            self.dtype = torch.bfloat16   # fp16 matmul is very slow on CPU
        self.verbose = verbose
        self.max_tok_s = max_tok_s if max_tok_s and max_tok_s > 0 else None
        t0 = time.time()
        self.gg = GGUFFile(path)
        self.tokenizer = Tokenizer(self.gg)
        total = sum(t.n_bytes for t in self.gg.tensor_list)
        self._log_model_header(path, weights, n_ctx)

        user_progress = progress

        def progress(name, done):
            frac = done / max(total, 1)
            if user_progress is not None:
                user_progress(frac)
            elif verbose:
                sys.stderr.write(f"\rloading weights: {100 * frac:5.1f}%   ")
                sys.stderr.flush()

        if weights not in ("quant", "bf16"):
            raise ValueError("weights must be 'quant' or 'bf16'")
        self.model = build_model(self.gg, self.device, self.dtype, n_ctx, progress, quant=(weights == "quant"),
                                 kv_bits=kv_bits, experten_vram=experts_vram)
        self._warmup()
        self.load_seconds = time.time() - t0
        self._log_loaded()
        if verbose:
            mem = f", {torch.cuda.memory_allocated() / 2**30:.2f} GiB VRAM" if self.device.type == "cuda" else ""
            mode = (f"{self.model.n_quant} matrices quantised in VRAM" if self.model.n_quant
                    else f"weights in {self.dtype}")
            sys.stderr.write(f"\rloaded {self.gg.get('general.name', path)} ({self.gg.architecture}) "
                             f"in {time.time() - t0:.1f}s on {self.device}{mem}; {mode}\n")
        fmt = detect_format(self.gg.get("tokenizer.chat_template"), self.gg.architecture)
        self.chat_format = ChatFormatter(self.tokenizer, fmt, thinking=thinking)
        self.n_ctx = n_ctx
        self._pos = 0
        self._history: List[int] = []
        self._own: List[bool] = []        # per position: generated by the model (DRY)
        self._breaker = None              # bool array over the vocabulary, built on first use
        # one key per cached position: the token id, or ("img", bild_nr(embeddings), row) for an
        # image position – different images share the placeholder id but not the key
        self._keys: list = []
        self._images: dict = {}          # image key -> (embeddings tensor, row)
        self.pad_id = int(self.gg.get("tokenizer.ggml.padding_token_id", 0) or 0)
        # M-RoPE models: rotary position of the next text token and of the start of the
        # image being read (images take fewer positions than cache slots)
        self._mrope = bool(getattr(self.model, "uses_mrope", False))
        self._rope_state = (0, 0)
        self.vision = None
        self._checkpoint = None          # (position, prefix keys, model state) after the system turn
        self._answer_checkpoint = None   # the same before the generation prompt of the last chat()
        self.checkpoint_new = False      # set when chat() made a new checkpoint (save_prefix)
        self.last_cached = 0             # prompt tokens taken from the cache in the last chat()
        self.last_ids: List[int] = []    # token ids generated in the last call

    # -- debug log -------------------------------------------------------------
    def _log_model_header(self, path, weights, n_ctx):
        gg = self.gg
        arch = gg.architecture
        dbg.section(f"Modell laden: {gg.get('general.name', path)}")
        dbg.kv({"Datei": path, "Größe": f"{gg.file_size / 2**30:.2f} GiB", "GGUF-Version": gg.version,
                "Architektur": arch, "Tensoren": len(gg.tensor_list), "Metadaten-Keys": len(gg.metadata),
                "Quant (file_type)": gg.get("general.file_type"), "Gerät": str(self.device), "dtype": str(self.dtype),
                "weights-Modus": weights, "n_ctx": n_ctx,
                "Tokenizer": f"{gg.get('tokenizer.ggml.model')} / pre={gg.get('tokenizer.ggml.pre')} / "
                             f"vocab={self.tokenizer.n_vocab} / bos={self.tokenizer.bos_id} eos={self.tokenizer.eos_id} "
                             f"stop={sorted(self.tokenizer.stop_ids)}"})
        hp = {k: v for k, v in gg.metadata.items()
              if k.startswith(arch + ".") and not isinstance(v, list)}
        hp.update({k: f"[{len(v)} Einträge] {v[:8]}" for k, v in gg.metadata.items()
                   if k.startswith(arch + ".") and isinstance(v, list)})
        dbg.kv(hp, "Hyperparameter")
        types = {}
        for t in gg.tensor_list:
            types[t.type_name] = types.get(t.type_name, 0) + 1
        dbg.kv(types, "Tensor-Typen")
        if self.device.type == "cuda":
            dbg.event(f"VRAM vor dem Laden: {torch.cuda.memory_allocated() / 2**30:.2f} GiB")

    def _log_loaded(self):
        mem = f"{torch.cuda.memory_allocated() / 2**30:.2f} GiB" if self.device.type == "cuda" else "-"
        dbg.kv({"Ladezeit inkl. Warmup": f"{self.load_seconds:.1f}s", "VRAM nach dem Laden": mem,
                "quantisierte Matrizen": self.model.n_quant,
                "Modellklasse": type(self.model).__name__}, "Geladen")

    def _warmup(self):
        """Trigger lazy CUDA/cuBLAS initialisation and the first graph capture up front."""
        ids = self.tokenizer.encode("warm up") or [0]
        self.model.forward(ids, 0)
        self.model.forward([ids[0]], len(ids))
        self.model.reset()

    # -- low level -----------------------------------------------------------
    def reset(self):
        self.model.reset()
        self._answer_checkpoint = None
        self._pos = 0
        self._history = []
        self._own = []
        self._keys = []
        self._rope_state = (0, 0)

    def _rope_positions(self, keys: list, state: tuple):
        """(t, h, w) rotary positions for `keys` starting from `state`; returns them with
        the new state.  Text: the same position on all axes.  Image of r x c tokens
        starting at s: (s, s + row, s + col); the following text starts at s + max(r, c)."""
        nxt, start = state
        out = []
        for k in keys:
            if not isinstance(k, tuple):
                out.append((nxt, nxt, nxt))
                nxt += 1
                continue
            img, row = self._images[k]
            rows, cols = img.grid
            if row == 0:
                start = nxt
            out.append((start, start + row // cols, start + row % cols))
            if row == rows * cols - 1:
                nxt = start + max(rows, cols)
        return out, (nxt, start)

    def feed(self, tokens: List[int], chunk: Optional[int] = None, keys: Optional[list] = None,
             own: Optional[list] = None) -> torch.Tensor:
        """Run tokens through the model (in chunks); returns logits of the last token.
        `keys` marks image positions (see `chat`); their embeddings come from `_images`.
        `own`: per token, generated by the model (default: no)."""
        if self.offloaded:
            self.restore()
        keys = list(tokens) if keys is None else list(keys)
        logits = None
        grenze = int(getattr(self.model, "prefill_chunk", PREFILL_CHUNK))
        chunk = max(1, min(chunk or grenze, grenze))
        self._kv_room(self._pos + len(tokens))
        ganz = bool(getattr(self.model, "images_bidirectional", False))
        i = 0
        while i < len(tokens):
            ende = min(i + chunk, len(tokens))
            if ganz:                                    # an image is read in one call (bidirectional)
                ende = _bild_nicht_teilen(keys, i, ende)
            part, kpart = tokens[i:ende], keys[i:ende]
            i = ende
            rows = [j for j, k in enumerate(kpart) if isinstance(k, tuple)]
            extra = {}
            if self._mrope:
                pos3, self._rope_state = self._rope_positions(kpart, self._rope_state)
                if rows:
                    extra["pos3"] = pos3
                else:                           # plain text: slot + offset, keeps the CUDA graph
                    self.model.set_rope_offset(pos3[0][0] - self._pos)
            if rows:
                emb = torch.stack([self._images[kpart[j]][0][self._images[kpart[j]][1]] for j in rows])
                model_tokens = [self.pad_id if isinstance(k, tuple) else t for t, k in zip(part, kpart)]
                inject = (torch.tensor(rows, device=self.device, dtype=torch.long), emb.to(self.device))
                logits = self.model.forward(model_tokens, self._pos, inject, **extra)
            else:
                logits = self.model.forward(part, self._pos)
            self._pos += len(part)
        if len(tokens) >= RELEASE_FROM and self.device.type == "cuda":
            self._release_scratch()
        self._history.extend(tokens)
        self._own.extend(own if own is not None else [False] * len(tokens))
        self._keys.extend(keys)
        return logits

    @staticmethod
    def _release_scratch() -> None:
        """Hands the scratch memory of a long read (bf16-unpacked matrices, activations)
        back to the driver; the caching allocator would otherwise keep it reserved for the
        rest of the session.  Not while a CUDA graph is being captured."""
        with GPU_SPERRE:
            torch.cuda.empty_cache()

    # -- images ------------------------------------------------------------------
    def enable_vision(self, mmproj_path: str) -> None:
        """Attaches a vision projector (mmproj); the encoder is picked from the file
        (see vision.load_projector).  Its weights stay in RAM and only go to the GPU
        inside `encode_image`."""
        if not getattr(self.model, "supports_images", False):
            raise ValueError(f"{self.gg.architecture} does not take image inputs")
        from .vision import load_projector
        vision = load_projector(mmproj_path, self.device)
        width = self.gg.get(f"{self.gg.architecture}.embedding_length")
        if width is not None and int(width) != vision.n_out:
            raise ValueError(f"mmproj width {vision.n_out} does not match the model ({width})")
        if not all(m in self.tokenizer.special for m in vision.marks):
            raise ValueError("the model has no image tokens for this mmproj")
        if vision.uses_grid and not self._mrope:
            raise ValueError("this mmproj needs a model with M-RoPE")
        self.chat_format.image_marks = vision.marks
        self.vision = vision

    def encode_image(self, image, tokens: Optional[int] = None) -> torch.Tensor:
        """PIL image -> image embeddings (CPU tensor) for `Message.images`.  The vision
        weights are loaded onto the GPU for this call only.  Encoders with a token grid
        leave it in `.grid` (rows, cols)."""
        if self.vision is None:
            raise RuntimeError("no vision projector attached (enable_vision)")
        t0 = time.time()
        self.vision.load()
        try:
            out = self.vision.encode(image, tokens or self.vision.default_tokens)
        finally:
            self.vision.unload()
        grid = None
        if isinstance(out, tuple):
            out, grid = out
        emb = out.cpu()
        bild_nr(emb)
        if grid is not None:
            emb.grid = tuple(grid)
        dbg.event(f"Bild kodiert: {emb.shape[0]} Token in {time.time() - t0:.2f}s")
        return emb

    def _breakers(self):
        """For DRY, over the vocabulary: (tokens that end a match – newline, ':', '"', '*'
        and special tokens; tokens that begin a word – space or punctuation first)."""
        if self._breaker is None:
            import numpy as np
            tok = self.tokenizer
            n = max(tok.n_vocab, self.model.n_vocab)
            b, w = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
            for tid in range(tok.n_vocab):
                if tok.types[tid] != 1:                      # control, user-defined, byte …
                    b[tid] = True
                    continue
                try:
                    t = tok.token_bytes(tid)
                except Exception:
                    b[tid] = True
                    continue
                b[tid] = any(c in t for c in (b"\n", b":", b'"', b"*"))
                w[tid] = bool(t) and (t[:1].isspace() or (t[0] < 0x80 and not t[:1].isalnum()))
            self._breaker = (b, w)
        return self._breaker

    def generate_tokens(self, prompt_ids: List[int], max_tokens: int = 512,
                        sampler: Optional[SamplerConfig] = None, stop_ids=None,
                        prompt_keys: Optional[list] = None, prompt_own: Optional[list] = None,
                        denk_budget: Optional[tuple] = None) -> Iterator[int]:
        """denk_budget = (tokens, open ids, close ids, starts inside): after `tokens` thinking
        tokens the close ids are inserted and generation goes on with the answer (budget
        forcing).  The model's own close (first close token) ends the thinking as well."""
        cfg = sampler or SamplerConfig()
        if denk_budget:
            grenze, auf, zu, drin = denk_budget
            gedacht, zuletzt = 0, []
        smp = Sampler(cfg, self.device)
        stop = set(self.tokenizer.stop_ids)
        if stop_ids:
            stop |= set(stop_ids)
        if self._pos + len(prompt_ids) + 1 > self.n_ctx:
            raise RuntimeError("prompt does not fit into the context window")
        t0 = time.time()
        logits = self.feed(prompt_ids, keys=prompt_keys, own=prompt_own)
        self._prefill_seconds = time.time() - t0
        step = 1.0 / self.max_tok_s if self.max_tok_s else 0.0
        next_at = time.perf_counter()
        dry = cfg.dry_multiplier > 0
        breaker, word_start = self._breakers() if dry else (None, None)
        erzeugt = b""                                        # for code blocks: DRY stays off inside
        for _ in range(max_tokens):
            code = erzeugt.count(b"```") % 2 == 1
            tid = smp.sample(logits, self._history, self._own if dry and not code else None, breaker,
                             word_start)
            if tid in stop:
                break
            yield tid
            if self._pos + 1 > self.n_ctx:
                break
            if denk_budget:
                zuletzt = (zuletzt + [tid])[-len(auf):]
                if not drin and zuletzt == auf:
                    drin, gedacht = True, 0
                elif drin and tid == zu[0]:
                    drin = False
                elif drin:
                    gedacht += 1
                    if gedacht >= grenze and self._pos + 1 + len(zu) <= self.n_ctx:
                        self.feed([tid], own=[True])         # last thinking token, then close the block
                        for t in zu:
                            yield t
                        logits = self.feed(zu, own=[True] * len(zu))
                        drin, denk_budget = False, None
                        continue
            if step:
                next_at += step
                wait = next_at - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                else:
                    next_at = time.perf_counter()   # slower than the limit: do not catch up
            if dry:
                erzeugt += self.tokenizer.token_bytes(tid)
            logits = self.feed([tid], own=[True])

    # -- high level ----------------------------------------------------------
    def complete(self, text: str, **kw) -> Iterator[str]:
        ids = self.tokenizer.encode(text)
        if self.tokenizer.add_bos and self.tokenizer.bos_id is not None:
            ids = [self.tokenizer.bos_id] + ids
        yield from self._stream(ids, **kw)

    def chat(self, messages: List[Message], reuse: bool = True, checkpoint_system: bool = True,
             **kw) -> Iterator[str]:
        """Chat over the whole conversation.  With `reuse` the tokens already in the KV
        cache are kept as far as they match the new prompt (prefix cache), and only the
        rest is read.  `checkpoint_system` saves the small model state right after the
        system turn, so a conversation that diverges later never re-reads the system
        prompt.  `self.last_ids` holds the generated token ids afterwards (for
        `Message.ids`)."""
        ids = self.chat_format.encode(messages, add_generation_prompt=True)
        keys = self._image_keys(messages, ids)
        own = self._own_mask(messages, ids)
        start = self._resume(keys) if reuse else self._start_over()
        self.last_cached = start
        if checkpoint_system and (cut := self._system_end(messages, ids)) and start < cut < len(ids):
            self.feed(ids[start:cut], keys=keys[start:cut], own=own[start:cut])
            self._checkpoint = (cut, keys[:cut], self.model.checkpoint_state())
            self.checkpoint_new = True
            start = cut
        # checkpoint before the generation prompt: when the next prompt renders this answer
        # differently (thinking left out), models whose state cannot step back (recurrent
        # layers, sliding-window rings) continue here instead of re-reading the conversation
        ohne = self.chat_format.encode(messages, add_generation_prompt=False)
        vor = len(ohne)
        if start < vor < len(ids) and ids[:vor] == ohne:
            self.feed(ids[start:vor], keys=keys[start:vor], own=own[start:vor])
            zustand = self.model.checkpoint_state()
            self._answer_checkpoint = (vor, keys[:vor], zustand) if zustand is not None else None
            start = vor
        dbg.section(f"Chat-Anfrage ({len(messages)} Nachrichten, {len(ids)} Prompt-Tokens, "
                    f"{start} aus dem Cache)")
        smp = kw.get("sampler")
        dbg.kv({"Template": self.chat_format.fmt, "Thinking": self.chat_format.thinking,
                "max_tokens": kw.get("max_tokens"), "Sampler": smp.__dict__ if smp else "default",
                "Rollen": " → ".join(m.role for m in messages)})
        dbg.code(self.tokenizer.decode(ids, skip_special=False), "text", "Gerenderter Prompt (Template + Nachrichten)", limit=4000)
        dbg.code(" ".join(map(str, ids[:48])) + (" …" if len(ids) > 48 else ""), "text", "Erste Prompt-Token-IDs")
        marken = self.chat_format.denk_marken() if self.chat_format.thinking else None
        if getattr(self, "denk_budget", None) and marken and "denk_budget" not in kw:
            kw["denk_budget"] = (self.denk_budget, marken[0], marken[1], self.chat_format.opens_thinking)
        yield from self._stream(ids[start:], keys=keys[start:], own=own[start:], **kw)

    @staticmethod
    def _own_mask(messages: List[Message], ids: List[int]) -> List[bool]:
        """True at the positions of earlier answers this engine generated (Message.ids)."""
        own = [False] * len(ids)
        pos = 0
        for m in messages:
            if m.role != "assistant" or not m.ids:
                continue
            seq, n = list(m.ids), len(m.ids)
            i = pos
            while i + n <= len(ids):
                try:
                    i = ids.index(seq[0], i)
                except ValueError:
                    break
                if i + n <= len(ids) and ids[i:i + n] == seq:
                    own[i:i + n] = [True] * n
                    pos = i + n
                    break
                i += 1
        return own

    def _image_keys(self, messages: List[Message], ids: List[int]) -> list:
        """Keys for `ids`: the token id, or ("img", bild_nr(embeddings), row) at the image
        placeholders (in message order).  Registers the embeddings in `_images`."""
        images = [img for m in messages if m.role == "user" for img in (m.images or [])]
        if not images:
            return list(ids)
        if not getattr(self.model, "supports_images", False):
            raise ValueError(f"{self.gg.architecture} does not take image inputs")
        if self._mrope and any(getattr(img, "grid", None) is None for img in images):
            raise ValueError("image embeddings without a token grid")
        slots = [(img, r) for img in images for r in range(int(img.shape[0]))]
        marks = self.chat_format.image_marks
        if not marks:
            raise ValueError("this model has no image tokens")
        placeholder = self.tokenizer.special_id(marks[1])
        where = [i for i, t in enumerate(ids) if t == placeholder]
        if len(where) != len(slots):
            raise ValueError("image placeholders and embeddings do not match")
        keys = list(ids)
        self._images = {}
        for i, (img, r) in zip(where, slots):
            key = ("img", bild_nr(img), r)
            keys[i] = key
            self._images[key] = (img, r)
        return keys

    # -- system prompt on disk ------------------------------------------------------
    PREFIX_FORMAT = 1

    def _kv_room(self, n: int) -> None:
        """Grow the full KV caches to hold n positions; drop captured decode graphs if a
        cache moved."""
        if getattr(self, "_kv_list", None) is None:
            self._kv_list = self._full_caches()
        moved = False
        for c in self._kv_list:
            moved |= c.ensure(n)
        graph = getattr(self.model, "graph", None)
        if moved and graph is not None:            # graphs and their memory pool, as in offload()
            torch.cuda.synchronize()
            graph.graphs.clear()
            graph.pool = None

    def _full_caches(self) -> list:
        """Full-context KV caches of the model in a fixed order (ring caches belong to
        the checkpoint state)."""
        from .models.common import KVCache
        found, seen = [], set()

        def walk(obj):
            if id(obj) in seen:
                return
            seen.add(id(obj))
            if isinstance(obj, KVCache):
                found.append(obj)
                return
            if isinstance(obj, (list, tuple)):
                for x in obj:
                    walk(x)
            elif type(obj).__module__.startswith(__package__):
                names = list(getattr(obj, "__dict__", {}))
                for cls in type(obj).__mro__:
                    names += [n for n in getattr(cls, "__slots__", ()) if n not in names]
                for n in names:
                    if hasattr(obj, n):
                        walk(getattr(obj, n))
        walk(self.model)
        return found

    def save_prefix(self, path: str, fingerprint: str) -> bool:
        """Writes the system-turn checkpoint (tokens, KV of those positions, model state)
        as safetensors – data only, nothing in the file is ever executed."""
        if self._checkpoint is None:
            return False
        import json
        import os
        from safetensors.torch import save_file
        cut, keys, state = self._checkpoint
        if any(not isinstance(k, int) for k in keys):
            return False                                     # images in the system turn: not stored
        tensors = {}
        for i, c in enumerate(self._full_caches()):
            k, v = c.kv(cut)
            tensors[f"kv.{i}.k"] = k.contiguous()
            tensors[f"kv.{i}.v"] = v.contiguous()

        def flat(obj, name):
            if obj is None:
                return None
            if isinstance(obj, torch.Tensor):
                tensors[name] = obj.contiguous()
                return name
            if isinstance(obj, (list, tuple)):
                return [flat(x, f"{name}.{j}") for j, x in enumerate(obj)]
            raise TypeError(f"cannot store {type(obj).__name__}")
        form = flat(state, "state")
        meta = {"format": str(self.PREFIX_FORMAT), "fingerprint": fingerprint, "cut": str(cut),
                "keys": json.dumps(keys), "state": json.dumps(form)}
        tmp = path + ".tmp"
        save_file({k: v.detach().cpu() for k, v in tensors.items()}, tmp, metadata=meta)
        os.replace(tmp, path)
        self.checkpoint_new = False
        return True

    def load_prefix(self, path: str, fingerprint: str, system_ids: List[int]) -> bool:
        """Loads a stored system-turn checkpoint if it belongs to this model file and to
        exactly these system tokens; returns False (and changes nothing) otherwise."""
        import json
        from safetensors import safe_open
        try:
            with safe_open(path, framework="pt", device="cpu") as f:
                meta = f.metadata() or {}
                if (meta.get("format") != str(self.PREFIX_FORMAT) or meta.get("fingerprint") != fingerprint
                        or json.loads(meta.get("keys", "[]")) != list(system_ids)):
                    return False
                cut = int(meta["cut"])
                caches = self._full_caches()
                if not 0 < cut < self.n_ctx:
                    return False
                kv = []
                for i, c in enumerate(caches):
                    k, v = f.get_tensor(f"kv.{i}.k"), f.get_tensor(f"kv.{i}.v")
                    want = (c.heads, cut, c.head_dim)
                    if tuple(k.shape) != want or tuple(v.shape) != want:
                        return False
                    kv.append((k, v))

                def build(form):
                    if form is None:
                        return None
                    if isinstance(form, str):
                        return f.get_tensor(form).to(self.device)
                    return [build(x) for x in form]
                state = build(json.loads(meta["state"]))
        except Exception:
            return False
        self.reset()
        self._kv_room(cut)
        with torch.inference_mode():
            for c, (k, v) in zip(caches, kv):
                c.fill(k, v)
            if state is not None:
                self.model.restore_checkpoint(state)
        keys = list(system_ids)
        self._pos, self._history, self._keys, self._own = cut, list(keys), keys, [False] * cut
        if self._mrope:
            self._rope_state = (cut, 0)
        self._checkpoint = (cut, list(keys), self.model.checkpoint_state())
        self.checkpoint_new = False
        return True

    # -- GPU <-> RAM ----------------------------------------------------------
    @property
    def offloaded(self) -> bool:
        return bool(getattr(self, "_moved", None))

    def offload(self) -> float:
        """Moves every CUDA tensor of the model (weights, KV caches, buffers) into pinned
        RAM and frees the VRAM.  The conversation state is kept; `restore()` brings it
        back.  Returns the seconds taken."""
        if self.device.type != "cuda" or self.offloaded:
            return 0.0
        t0 = time.time()
        torch.cuda.synchronize()
        graph = getattr(self.model, "graph", None)
        if graph is not None:                       # captured graphs point at VRAM addresses
            graph.graphs.clear()
            graph.pool = None

        def to_ram(t):
            host = torch.empty(t.shape, dtype=t.dtype, device="cpu", pin_memory=True)
            host.copy_(t, non_blocking=True)
            return host
        self._moved = _move_tensors(self.model, lambda t: t.device.type == "cuda", to_ram)
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        dbg.event(f"ausgelagert in {time.time() - t0:.2f}s, VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GiB")
        return time.time() - t0

    def restore(self) -> float:
        """Brings an offloaded model back onto the GPU (only the tensors that were moved;
        tables that live in RAM on purpose stay there)."""
        if not self.offloaded:
            return 0.0
        t0 = time.time()
        moved = self._moved
        self._moved = None
        _move_tensors(self.model, lambda t: id(t) in moved,
                      lambda t: t.to(self.device, non_blocking=True))
        torch.cuda.synchronize()
        dbg.event(f"zurück auf der GPU in {time.time() - t0:.2f}s")
        return time.time() - t0

    # -- prefix cache ----------------------------------------------------------
    def _start_over(self) -> int:
        self.reset()
        return 0

    def _resume(self, ids: list) -> int:
        """Keeps the cached prefix shared with `ids` (keys, see `_image_keys`); returns
        the position to read from."""
        old = self._keys
        common = 0
        for a, b in zip(old, ids):
            if a != b:
                break
            common += 1
        common = min(common, len(ids) - 1)          # at least one token yields the logits
        start = self.model.resume_from(common, len(old)) if common > 0 else None
        cp = self._checkpoint
        if cp is not None and (len(ids) < cp[0] or ids[:cp[0]] != cp[1]):
            cp = self._checkpoint = None             # its prefix is about to be overwritten
        acp = getattr(self, "_answer_checkpoint", None)
        if acp is not None and (len(ids) < acp[0] or ids[:acp[0]] != acp[1]):
            acp = self._answer_checkpoint = None
        for punkt in (acp, cp):                      # the later one first
            if start is None and punkt is not None and punkt[0] <= common:
                self.model.restore_checkpoint(punkt[2])
                start = punkt[0]
        if start is None:
            return self._start_over()
        if acp is not None and acp[0] > start:
            self._answer_checkpoint = None           # the slots behind `start` get overwritten
        self._pos = start
        self._history = self._history[:start]
        self._own = self._own[:start]
        self._keys = old[:start]
        if self._mrope:
            _, self._rope_state = self._rope_positions(self._keys, (0, 0))
        return start

    def _system_end(self, messages: List[Message], ids: List[int]) -> int:
        """Token count of the system turn at the start of `ids` (0 if there is none)."""
        system = [m for m in messages if m.role == "system"]
        if not system:
            return 0
        head = self.chat_format.encode(system[:1], add_generation_prompt=False)
        return len(head) if ids[:len(head)] == head else 0

    def _stream(self, ids: List[int], keys: Optional[list] = None, own: Optional[list] = None,
                **kw) -> Iterator[str]:
        dec = StreamDecoder(self.tokenizer)
        t0 = time.time()
        n = 0
        self._prefill_seconds = 0.0
        reply = []
        self.last_ids = []
        try:
            for tid in self.generate_tokens(ids, prompt_keys=keys, prompt_own=own, **kw):
                n += 1
                self.last_ids.append(tid)
                s = dec.push(tid)
                if s:
                    reply.append(s)
                    yield s
            tail = dec.flush()
            if tail:
                reply.append(tail)
                yield tail
        except GeneratorExit:
            dbg.event("Generierung vom Aufrufer abgebrochen (Stop)")
            dbg.code("".join(reply), "text", "Teil-Antwort bis zum Stop")
            raise
        except Exception:
            dbg.exception("Generierung")
            raise
        else:
            dbg.code("".join(reply), "text", "Antwort")
        finally:   # also runs when the consumer stops iterating early
            total = time.time() - t0
            pre = self._prefill_seconds
            self.last_stats = dict(prompt_tokens=len(ids), generated=n, seconds=total,
                                   prefill_seconds=pre, decode_seconds=max(total - pre, 1e-9))
            st = self.last_stats
            dbg.kv({"Prompt-Tokens": st["prompt_tokens"],
                    "Prefill": f"{pre:.2f}s ({st['prompt_tokens'] / pre if pre > 0 else 0:.0f} tok/s)",
                    "generiert": f"{n} Tokens in {st['decode_seconds']:.2f}s ({n / st['decode_seconds']:.1f} tok/s)",
                    "Position danach": self._pos,
                    "VRAM": f"{torch.cuda.memory_allocated() / 2**30:.2f} GiB" if self.device.type == "cuda" else "-"},
                   "Antwort-Statistik")
