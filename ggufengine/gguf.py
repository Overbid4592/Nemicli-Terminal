"""
Gehärteter GGUF-Leser.

Entwurfsziele (abgeleitet aus der CVE-Historie von llama.cpp / ollama):

* Jede aus der Datei gelesene Ganzzahl wird auf Grenzen geprüft, BEVOR sie für
  eine Allokation, einen Slice oder einen Seek verwendet wird.  (CVE-2024-23605, CVE-2024-21836,
  CVE-2026-27940, CVE-2026-52131, "Bleeding Llama" CVE-2026-7482)
* Die Ausdehnung der Tensoren wird mit exakten Python-Ganzzahlen gegen die echte Dateigröße
  geprüft, ein Integer-Überlauf ist damit von vornherein unmöglich.
* `general.alignment` muss eine Zweierpotenz in einem vernünftigen Bereich sein
  (die Lücke unbegrenztes Alignment / GGML_PAD-Überlauf, V-01).
* Strings und Arrays haben harte Längengrenzen gegen Speichererschöpfung (V-02), und
  die Metadaten insgesamt haben ein Budget: Eine kleine Datei darf sich nicht zu Gigabytes
  an Python-Objekten entfalten.
* Tensoren beginnen an ausgerichteten Offsets und überlappen nicht (wie llama.cpp es verlangt).
* Nichts aus der Datei wird je ausgeführt oder ausgewertet (kein Jinja, kein eval).

Der Leser bildet die Datei schreibgeschützt ab und liefert schlanke Tensor-Deskriptoren;
Tensor-Bytes werden erst bei Bedarf und ohne Kopie ausgeschnitten.
"""
from __future__ import annotations

import mmap
import os
import struct
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

GGUF_MAGIC = b"GGUF"
SUPPORTED_VERSIONS = (2, 3)

# --- harte Grenzen ----------------------------------------------------------
MAX_TENSORS = 1 << 18          # 262144 tensors
MAX_KV = 1 << 16               # 65536 Metadaten-Einträge
MAX_STRING = 16 << 20          # 16 MiB je String
MAX_ARRAY = 1 << 24            # 16M Elemente je Array
MAX_NAME = 512                 # Länge von Tensor- und Schlüsselnamen
MAX_DIMS = 5                   # GGML_MAX_DIMS ist 4; Conv3d-Gewichte in Video-DiT-GGUFs nutzen 5
MAX_ELEMENTS = 1 << 40         # je Tensor
MAX_META_ELEMENTS = 1 << 23    # alle Array-Elemente aller Metadatenwerte zusammen (8M; echte Modelle ~0.3M)
MAX_META_BYTES = 512 << 20      # alle String-Bytes aller Metadatenwerte zusammen
MIN_ALIGNMENT, MAX_ALIGNMENT = 8, 1 << 16
DEFAULT_ALIGNMENT = 32


class GGUFError(ValueError):
    """Wird bei jedem fehlerhaften / feindseligen Dateiinhalt ausgelöst."""


# --- Werttypen der GGUF-Metadaten ------------------------------------------
(T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32, T_FLOAT32, T_BOOL,
 T_STRING, T_ARRAY, T_UINT64, T_INT64, T_FLOAT64) = range(13)

_SCALAR_FMT = {
    T_UINT8: "B", T_INT8: "b", T_UINT16: "H", T_INT16: "h", T_UINT32: "I",
    T_INT32: "i", T_FLOAT32: "f", T_BOOL: "?", T_UINT64: "Q", T_INT64: "q",
    T_FLOAT64: "d",
}


# --- GGML-Tensortypen: id -> (name, block_size, type_size_bytes) ------------
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
    35: ("TQ2_0", 256, 66), 39: ("MXFP4", 32, 17), 41: ("Q1_0", 128, 18), 42: ("Q2_0", 64, 18),
    # PrismML-Fork: Q2_0 und TQ1_0 mit einer Scale je 128 Werte
    142: ("PQ2_0", 128, 34), 143: ("PTQ1_0", 128, 28),
}
Q2_0, PQ2_0 = 42, 142


@dataclass(frozen=True)
class TensorInfo:
    name: str
    dims: Tuple[int, ...]      # GGUF-Reihenfolge: dims[0] ändert sich am schnellsten (ne0)
    ggml_type: int
    offset: int                # relativ zum Beginn des Datenabschnitts
    n_elements: int
    n_bytes: int

    @property
    def type_name(self) -> str:
        return GGML_TYPES[self.ggml_type][0]

    @property
    def shape(self) -> Tuple[int, ...]:
        """Form in Zeilen-Reihenfolge (row-major, wie numpy / torch)."""
        return tuple(reversed(self.dims))


class _Cursor:
    """Grenzgeprüfter Little-Endian-Leser über einem schreibgeschützten Puffer."""

    __slots__ = ("buf", "pos", "end", "elements", "string_bytes")

    def __init__(self, buf, start: int, end: int):
        self.buf, self.pos, self.end = buf, start, end
        self.elements = 0            # Budget der Metadaten, siehe MAX_META_*
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
                raw = self.raw(n * size)   # Grenzen durch need() geprüft
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
    """Eine geprüfte, schreibgeschützte Sicht auf eine GGUF-Datei."""

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        self._fh = open(self.path, "rb")
        self._lesen = threading.Lock()   # read_into: Seek + Read auf dem gemeinsamen Handle
        self.file_size = os.fstat(self._fh.fileno()).st_size
        if self.file_size < 24:
            self._fh.close()
            raise GGUFError("file too small to be GGUF")
        try:
            self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except BaseException:
            self._fh.close()
            raise
        self.metadata: Dict[str, Any] = {}
        self.tensors: Dict[str, TensorInfo] = {}
        self.tensor_list: List[TensorInfo] = []
        self.version = 0
        self.alignment = DEFAULT_ALIGNMENT
        self.data_offset = 0
        try:
            self._parse()
        except struct.error as e:  # defensiv: sollte schon von need() abgefangen sein
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

        # der Datenabschnitt beginnt an der nächsten ausgerichteten Position nach dem Header
        header_end = c.pos
        self.data_offset = (header_end + self.alignment - 1) // self.alignment * self.alignment
        if self.data_offset > self.file_size:
            raise GGUFError("data section starts beyond end of file")
        data_size = self.file_size - self.data_offset
        infos = self._q2_0_gruppe(infos, data_size)

        for ti in infos:
            if ti.name in self.tensors:
                raise GGUFError(f"duplicate tensor name {ti.name!r}")
            if ti.offset % self.alignment:
                raise GGUFError(f"tensor {ti.name!r}: offset {ti.offset} not aligned to {self.alignment}")
            # Bereichsprüfung mit exakten Ganzzahlen: offset + n_bytes <= data_size
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

    @classmethod
    def _q2_0_gruppe(cls, infos: List[TensorInfo], data_size: int) -> List[TensorInfo]:
        """Q2_0 hat 64 Werte je Block; ältere PrismML-Dateien nutzen dieselbe Typ-ID mit 128 Werten
        (heute PQ2_0).  Ein Tensor, dessen Größe bei 64er-Blöcken nicht bis zum nächsten Tensor passt,
        bei 128er-Blöcken aber schon, wird als PQ2_0 gelesen (der Größenunterschied, n/64 Bytes,
        übersteigt das Padding)."""
        if not any(ti.ggml_type == Q2_0 for ti in infos):
            return infos
        folge = sorted(infos, key=lambda t: t.offset)
        platz = {id(a): (b.offset if b is not None else data_size) - a.offset
                 for a, b in zip(folge, folge[1:] + [None])}
        aus = []
        for ti in infos:
            if ti.ggml_type == Q2_0 and ti.n_bytes > platz[id(ti)]:
                alt = cls._make_tensor_info(ti.name, ti.dims, PQ2_0, ti.offset)
                if alt.n_bytes <= platz[id(ti)]:
                    ti = alt
            aus.append(ti)
        return aus

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
        """Schreibgeschützte Sicht ohne Kopie auf die rohen Tensor-Bytes."""
        start = self.data_offset + ti.offset
        end = start + ti.n_bytes
        if end > self.file_size:  # kann nach der Prüfung nicht vorkommen; doppelt gesichert
            raise GGUFError("tensor out of bounds")
        return memoryview(self._mm)[start:end]

    def read_into(self, ti: TensorInfo, out, start: int = 0) -> int:
        """Kopiert die Tensor-Bytes [start, start + len(out)) in den beschreibbaren Puffer `out`, mit
        einfachen Datei-Reads (große sequenzielle Anfragen statt Page Faults auf dem Mapping);
        hört am Tensor-Ende auf und gibt die Anzahl gelesener Bytes zurück."""
        view = memoryview(out).cast("B")
        if not 0 <= start <= ti.n_bytes:
            raise GGUFError("read outside the tensor")
        n = min(view.nbytes, ti.n_bytes - start)
        pos = self.data_offset + ti.offset + start
        if pos + n > self.file_size:  # kann nach der Prüfung nicht vorkommen; doppelt gesichert
            raise GGUFError("tensor out of bounds")
        with self._lesen:
            self._fh.seek(pos)
            got = 0
            while got < n:
                k = self._fh.readinto(view[got:n])
                if not k:
                    raise GGUFError("unexpected end of file")
                got += k
        return n

    def close(self) -> None:
        mm = getattr(self, "_mm", None)
        try:
            if mm is not None:
                mm.close()
        except BufferError:              # noch eine Sicht (Tensor) in Benutzung: das Abbild schließt mit ihr
            pass
        finally:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
