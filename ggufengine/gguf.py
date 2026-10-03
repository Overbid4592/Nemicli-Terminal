"""
Hardened GGUF reader.

Design goals (derived from the CVE history of llama.cpp / ollama):

* Every integer read from the file is bounds-checked BEFORE it is used for
  an allocation, a slice or a seek.  (CVE-2024-23605, CVE-2024-21836,
  CVE-2026-27940, CVE-2026-52131, "Bleeding Llama" CVE-2026-7482)
* Tensor extents are validated against the real file size using exact
  Python integers, so an integer overflow is impossible by construction.
* `general.alignment` must be a power of two within a sane range
  (the unbounded-alignment / GGML_PAD overflow flaw, V-01).
* Strings and arrays have hard length caps to prevent memory exhaustion (V-02), and
  the metadata as a whole has a budget: a small file must not unfold into gigabytes
  of Python objects.
* Tensors start at aligned offsets and do not overlap (as llama.cpp requires).
* Nothing in the file is ever executed or evaluated (no Jinja, no eval).

The reader maps the file read-only and returns lightweight tensor
descriptors; tensor bytes are sliced lazily and zero-copy.
"""
from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

GGUF_MAGIC = b"GGUF"
SUPPORTED_VERSIONS = (2, 3)

# --- hard limits ------------------------------------------------------------
MAX_TENSORS = 1 << 18          # 262144 tensors
MAX_KV = 1 << 16               # 65536 metadata entries
MAX_STRING = 16 << 20          # 16 MiB per string
MAX_ARRAY = 1 << 24            # 16M elements per array
MAX_NAME = 512                 # tensor / key name length
MAX_DIMS = 4                   # GGML_MAX_DIMS
MAX_ELEMENTS = 1 << 40         # per tensor
MAX_META_ELEMENTS = 1 << 25    # all array elements of all metadata values together (32M)
MAX_META_BYTES = 512 << 20      # all string bytes of all metadata values together
MIN_ALIGNMENT, MAX_ALIGNMENT = 8, 1 << 16
DEFAULT_ALIGNMENT = 32


class GGUFError(ValueError):
    """Raised for any malformed / hostile file content."""


# --- GGUF metadata value types ---------------------------------------------
(T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32, T_FLOAT32, T_BOOL,
 T_STRING, T_ARRAY, T_UINT64, T_INT64, T_FLOAT64) = range(13)

_SCALAR_FMT = {
    T_UINT8: "B", T_INT8: "b", T_UINT16: "H", T_INT16: "h", T_UINT32: "I",
    T_INT32: "i", T_FLOAT32: "f", T_BOOL: "?", T_UINT64: "Q", T_INT64: "q",
    T_FLOAT64: "d",
}


# --- GGML tensor types: id -> (name, block_size, type_size_bytes) -----------
GGML_TYPES: Dict[int, Tuple[str, int, int]] = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50), 20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136), 24: ("I8", 1, 1),
    25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56), 30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54),
    35: ("TQ2_0", 256, 66), 39: ("MXFP4", 32, 17),
}


@dataclass(frozen=True)
class TensorInfo:
    name: str
    dims: Tuple[int, ...]      # GGUF order: dims[0] is the fastest-varying (ne0)
    ggml_type: int
    offset: int                # relative to data section start
    n_elements: int
    n_bytes: int

    @property
    def type_name(self) -> str:
        return GGML_TYPES[self.ggml_type][0]

    @property
    def shape(self) -> Tuple[int, ...]:
        """Row-major (numpy / torch) shape."""
        return tuple(reversed(self.dims))


class _Cursor:
    """Bounds-checked little-endian reader over a read-only buffer."""

    __slots__ = ("buf", "pos", "end", "elements", "string_bytes")

    def __init__(self, buf, start: int, end: int):
        self.buf, self.pos, self.end = buf, start, end
        self.elements = 0            # metadata budget, see MAX_META_*
        self.string_bytes = 0

    def need(self, n: int) -> None:
        if n < 0 or self.pos + n > self.end:
            raise GGUFError(f"truncated file: need {n} bytes at {self.pos}, have {self.end - self.pos}")

    def raw(self, n: int) -> bytes:
        self.need(n)
        b = self.buf[self.pos:self.pos + n]
        self.pos += n
        return b

    def scalar(self, fmt: str):
        size = struct.calcsize("<" + fmt)
        return struct.unpack("<" + fmt, self.raw(size))[0]

    def u32(self) -> int:
        return self.scalar("I")

    def u64(self) -> int:
        return self.scalar("Q")

    def string(self, limit: int = MAX_STRING) -> str:
        n = self.u64()
        if n > limit:
            raise GGUFError(f"string length {n} exceeds limit {limit}")
        self.string_bytes += n
        if self.string_bytes > MAX_META_BYTES:
            raise GGUFError("metadata strings exceed the total budget")
        try:
            return self.raw(n).decode("utf-8")
        except UnicodeDecodeError as e:
            raise GGUFError(f"invalid UTF-8 in string at {self.pos}: {e}") from None

    def value(self, vtype: int, depth: int = 0):
        if vtype == T_STRING:
            return self.string()
        if vtype == T_ARRAY:
            if depth > 1:
                raise GGUFError("nested arrays deeper than 1 level are not allowed")
            etype = self.u32()
            n = self.u64()
            if n > MAX_ARRAY:
                raise GGUFError(f"array length {n} exceeds limit {MAX_ARRAY}")
            self.elements += n
            if self.elements > MAX_META_ELEMENTS:
                raise GGUFError("metadata arrays exceed the total budget")
            if etype in _SCALAR_FMT:
                fmt = _SCALAR_FMT[etype]
                size = struct.calcsize("<" + fmt)
                raw = self.raw(n * size)   # bounds-checked by need()
                return list(struct.unpack(f"<{n}{fmt}", raw))
            if etype == T_STRING:
                return [self.string() for _ in range(n)]
            if etype == T_ARRAY:
                return [self.value(T_ARRAY, depth + 1) for _ in range(n)]
            raise GGUFError(f"unknown array element type {etype}")
        fmt = _SCALAR_FMT.get(vtype)
        if fmt is None:
            raise GGUFError(f"unknown metadata value type {vtype}")
        return self.scalar(fmt)


class GGUFFile:
    """A validated, read-only view of a GGUF file."""

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        self._fh = open(self.path, "rb")
        self.file_size = os.fstat(self._fh.fileno()).st_size
        if self.file_size < 24:
            self._fh.close()
            raise GGUFError("file too small to be GGUF")
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        self.metadata: Dict[str, Any] = {}
        self.tensors: Dict[str, TensorInfo] = {}
        self.tensor_list: List[TensorInfo] = []
        self.version = 0
        self.alignment = DEFAULT_ALIGNMENT
        self.data_offset = 0
        try:
            self._parse()
        except struct.error as e:  # defensive: should already be caught by need()
            self.close()
            raise GGUFError(f"corrupt header: {e}") from None
        except Exception:
            self.close()
            raise

    # -- parsing -------------------------------------------------------------
    def _parse(self) -> None:
        c = _Cursor(self._mm, 0, self.file_size)
        if c.raw(4) != GGUF_MAGIC:
            raise GGUFError("bad magic (not a GGUF file)")
        self.version = c.u32()
        if self.version not in SUPPORTED_VERSIONS:
            raise GGUFError(f"unsupported GGUF version {self.version}")
        n_tensors = c.u64()
        n_kv = c.u64()
        if n_tensors > MAX_TENSORS:
            raise GGUFError(f"tensor count {n_tensors} exceeds limit {MAX_TENSORS}")
        if n_kv > MAX_KV:
            raise GGUFError(f"metadata count {n_kv} exceeds limit {MAX_KV}")

        for _ in range(n_kv):
            key = c.string(MAX_NAME)
            if not key:
                raise GGUFError("empty metadata key")
            vtype = c.u32()
            val = c.value(vtype)
            if key in self.metadata:
                raise GGUFError(f"duplicate metadata key {key!r}")
            self.metadata[key] = val

        align = self.metadata.get("general.alignment", DEFAULT_ALIGNMENT)
        if (not isinstance(align, int) or isinstance(align, bool)
                or align < MIN_ALIGNMENT or align > MAX_ALIGNMENT or align & (align - 1)):
            raise GGUFError(f"invalid general.alignment {align!r}")
        self.alignment = align

        infos: List[TensorInfo] = []
        for _ in range(n_tensors):
            name = c.string(MAX_NAME)
            if not name:
                raise GGUFError("empty tensor name")
            n_dims = c.u32()
            if n_dims < 1 or n_dims > MAX_DIMS:
                raise GGUFError(f"tensor {name!r}: invalid n_dims {n_dims}")
            dims = tuple(c.u64() for _ in range(n_dims))
            ggml_type = c.u32()
            offset = c.u64()
            infos.append(self._make_tensor_info(name, dims, ggml_type, offset))

        # data section starts at the next aligned position after the header
        header_end = c.pos
        self.data_offset = (header_end + self.alignment - 1) // self.alignment * self.alignment
        if self.data_offset > self.file_size:
            raise GGUFError("data section starts beyond end of file")
        data_size = self.file_size - self.data_offset

        for ti in infos:
            if ti.name in self.tensors:
                raise GGUFError(f"duplicate tensor name {ti.name!r}")
            if ti.offset % self.alignment:
                raise GGUFError(f"tensor {ti.name!r}: offset {ti.offset} not aligned to {self.alignment}")
            # exact-integer range check: offset + n_bytes <= data_size
            if ti.offset > data_size or ti.n_bytes > data_size - ti.offset:
                raise GGUFError(
                    f"tensor {ti.name!r} extends beyond file: offset={ti.offset} "
                    f"bytes={ti.n_bytes} data_size={data_size}")
            self.tensors[ti.name] = ti
            self.tensor_list.append(ti)
        ende = 0
        for ti in sorted(infos, key=lambda x: x.offset):
            if ti.offset < ende:
                raise GGUFError(f"tensor {ti.name!r} overlaps the previous tensor")
            ende = ti.offset + ti.n_bytes

    @staticmethod
    def _make_tensor_info(name: str, dims: Tuple[int, ...], ggml_type: int, offset: int) -> TensorInfo:
        tinfo = GGML_TYPES.get(ggml_type)
        if tinfo is None:
            raise GGUFError(f"tensor {name!r}: unknown ggml type {ggml_type}")
        _, block_size, type_size = tinfo
        n_elements = 1
        for d in dims:
            if d < 1:
                raise GGUFError(f"tensor {name!r}: zero/invalid dimension {dims}")
            n_elements *= d
            if n_elements > MAX_ELEMENTS:
                raise GGUFError(f"tensor {name!r}: too many elements {dims}")
        if dims[0] % block_size != 0:
            raise GGUFError(f"tensor {name!r}: ne0={dims[0]} not a multiple of block size {block_size}")
        n_bytes = n_elements // block_size * type_size
        return TensorInfo(name, dims, ggml_type, offset, n_elements, n_bytes)

    # -- access --------------------------------------------------------------
    @property
    def architecture(self) -> str:
        arch = self.metadata.get("general.architecture")
        if not isinstance(arch, str):
            raise GGUFError("missing general.architecture")
        return arch

    def get(self, key: str, default=None):
        return self.metadata.get(key, default)

    def require(self, key: str):
        if key not in self.metadata:
            raise GGUFError(f"missing required metadata key {key!r}")
        return self.metadata[key]

    def tensor_bytes(self, ti: TensorInfo) -> memoryview:
        """Zero-copy read-only view of the raw tensor bytes."""
        start = self.data_offset + ti.offset
        end = start + ti.n_bytes
        if end > self.file_size:  # cannot happen after validation; belt and braces
            raise GGUFError("tensor out of bounds")
        return memoryview(self._mm)[start:end]

    def close(self) -> None:
        mm = getattr(self, "_mm", None)
        try:
            if mm is not None:
                mm.close()
        finally:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
