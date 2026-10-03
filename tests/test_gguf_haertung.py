"""GGUF-Leser: feindliche Dateien werden abgewiesen (selbstgebaute Mini-Dateien).

python -m unittest tests.test_gguf_haertung
"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ggufengine import gguf as GG                      # noqa: E402


def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def baue(tensoren, meta=None, align=32) -> bytes:
    """tensoren: [(name, dims, typ, offset, datenbytes)] -> GGUF v3."""
    meta = dict(meta or {})
    meta.setdefault("general.architecture", ("s", "test"))
    kopf = b"GGUF" + struct.pack("<IQQ", 3, len(tensoren), len(meta))
    for k, (art, wert) in meta.items():
        kopf += _s(k)
        if art == "s":
            kopf += struct.pack("<I", GG.T_STRING) + _s(wert)
        elif art == "u32":
            kopf += struct.pack("<II", GG.T_UINT32, wert)
        elif art == "arr_u8":
            kopf += struct.pack("<IIQ", GG.T_ARRAY, GG.T_UINT8, len(wert)) + bytes(wert)
    for name, dims, typ, off, _ in tensoren:
        kopf += _s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        kopf += struct.pack("<IQ", typ, off)
    kopf += b"\0" * ((-len(kopf)) % align)
    daten = bytearray(max((off + len(b) for _, _, _, off, b in tensoren), default=0))
    for _, _, _, off, b in tensoren:
        daten[off:off + len(b)] = b
    return kopf + bytes(daten)


class HaertungTests(unittest.TestCase):

    def _lesen(self, inhalt: bytes):
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.write(fd, inhalt)
        os.close(fd)
        try:
            g = GG.GGUFFile(pfad)
            g.close()
        finally:
            os.unlink(pfad)

    def test_gueltige_datei(self):
        self._lesen(baue([("a", (8,), 0, 0, b"\1" * 32), ("b", (8,), 0, 32, b"\2" * 32)]))

    def test_versatz_nicht_ausgerichtet(self):
        with self.assertRaises(GG.GGUFError):
            self._lesen(baue([("a", (8,), 0, 4, b"\1" * 32)]))

    def test_ueberlappende_tensoren(self):
        with self.assertRaises(GG.GGUFError):
            self._lesen(baue([("a", (16,), 0, 0, b"\1" * 64), ("b", (8,), 0, 32, b"\2" * 32)]))

    def test_tensor_hinter_dem_dateiende(self):
        roh = baue([("a", (8,), 0, 0, b"\1" * 32)])
        with self.assertRaises(GG.GGUFError):
            self._lesen(roh[:-8])

    def test_metadaten_budget(self):
        alt = GG.MAX_META_ELEMENTS
        GG.MAX_META_ELEMENTS = 100
        try:
            meta = {f"x.{i}": ("arr_u8", [1] * 40) for i in range(3)}      # 120 > 100
            with self.assertRaises(GG.GGUFError):
                self._lesen(baue([("a", (8,), 0, 0, b"\1" * 32)], meta))
        finally:
            GG.MAX_META_ELEMENTS = alt

    def test_ausrichtung_muss_zweierpotenz_sein(self):
        with self.assertRaises(GG.GGUFError):
            self._lesen(baue([("a", (8,), 0, 0, b"\1" * 32)], {"general.alignment": ("u32", 24)}, align=24))


if __name__ == "__main__":
    unittest.main()
