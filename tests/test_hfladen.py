"""Hugging-Face-Downloader (tools/hfladen.py) ohne Netz: Filter, Dateiwahl, Laden mit Prüfung.

python -m unittest tests.test_hfladen
"""

import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import hfladen as H                                    # noqa: E402

BAUARTEN = {"qwen35", "qwen3moe", "gemma4", "llama"}


def eintrag(repo, bauart="qwen35", total=8_000_000_000, **kw):
    e = {"id": repo, "gguf": {"architecture": bauart, "total": total, "context_length": 32768},
         "downloads": kw.pop("downloads", 10), "pipeline_tag": kw.pop("pipeline_tag", "text-generation")}
    e.update(kw)
    return e


class FilterTests(unittest.TestCase):
    def test_modell(self):
        m = H._modell(eintrag("unsloth/Qwen3.5-9B-GGUF", total=9_000_000_000), BAUARTEN)
        self.assertEqual((m.name, m.moe, m.groesse_text), ("Qwen3.5-9B", False, "9B"))
        self.assertEqual(H._modell(eintrag("a/M-4B-GGUF", total=4_200_000_000), BAUARTEN).groesse_text, "4,2B")
        moe = H._modell(eintrag("unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "qwen3moe", 30_500_000_000), BAUARTEN)
        self.assertEqual((moe.moe, moe.groesse_text), (True, "30B MoE (aktiv 3B)"))
        self.assertTrue(H._modell(eintrag("x/gemma-4-26B-A4B-it-GGUF", "gemma4", 25_000_000_000), BAUARTEN).moe)

    def test_ausgeschlossen(self):
        for e in (eintrag("a/Klein-3B-GGUF", total=3_000_000_000),            # unter 4B
                  eintrag("a/gemma-4-E2B-it-GGUF", "gemma4", 4_600_000_000),   # effektiv 2B
                  eintrag("a/Fremd-8B-GGUF", "mamba"),                         # Motor kann es nicht
                  eintrag("a/Gesperrt-8B-GGUF", gated="manual"),
                  eintrag("a/models-moved")):                                  # kein GGUF-Modell
            with self.subTest(e["id"]):
                self.assertIsNone(H._modell(e, BAUARTEN))
        self.assertIsNotNone(H._modell(eintrag("a/gemma-4-E4B-it-GGUF", "gemma4", 7_500_000_000), BAUARTEN))

    def test_bauarten_wie_der_motor(self):
        try:
            from ggufengine.models import SUPPORTED_ARCHS
        except ImportError:
            self.skipTest("torch fehlt")
        self.assertEqual(H.unterstuetzt(), set(SUPPORTED_ARCHS))

    def test_ordnername(self):
        self.assertEqual(H.ordnername("bartowski/Qwen_Qwen3-8B-GGUF"), "Qwen3-8B")
        self.assertEqual(H.ordnername("unsloth/Qwen3.5-4B-GGUF"), "Qwen3.5-4B")
        self.assertEqual(H.ordnername("x/Meta-Llama-3.1-8B-Instruct-GGUF"), "Meta-Llama-3.1-8B-Instruct")

    def test_katalog_doppelte_und_groessen(self):
        antworten = {
            "unsloth": [eintrag("unsloth/Qwen3-8B-GGUF", "qwen35", downloads=5),
                        eintrag("unsloth/Qwen3-Coder-30B-A3B-GGUF", "qwen3moe", 30_000_000_000, downloads=9)],
            "bartowski": [eintrag("bartowski/Qwen_Qwen3-8B-GGUF", "qwen35", downloads=7)],
        }

        def holen(url, params=None):
            return antworten.get(params["author"], [])
        with mock.patch.object(H, "unterstuetzt", lambda: BAUARTEN):
            k = H.katalog(holen, neu=True)
            self.addCleanup(setattr, H, "_KATALOG", None)
            self.assertEqual([m.repo for m in k], ["unsloth/Qwen3-Coder-30B-A3B-GGUF", "bartowski/Qwen_Qwen3-8B-GGUF"])
            self.assertEqual([m.name for m in H.suchen("8-15", None, holen)], ["Qwen3-8B"])
            self.assertEqual([m.name for m in H.suchen("15-35", True, holen)], ["Qwen3-Coder-30B-A3B"])
            self.assertEqual(H.suchen("15-35", False, holen), [])

    def test_offline(self):
        def kaputt(url, params=None):
            raise OSError("kein Netz")
        H._KATALOG = None
        with self.assertRaises(OSError):
            H.katalog(kaputt, neu=True)
        self.assertIsNone(H._KATALOG)


class DateiTests(unittest.TestCase):
    def baum(self):
        sha = "a" * 64
        return [
            {"type": "file", "path": "M-Q8_0.gguf", "lfs": {"oid": sha, "size": 800}},
            {"type": "file", "path": "M-Q4_K_M.gguf", "lfs": {"oid": sha, "size": 400}},
            {"type": "file", "path": "M-UD-Q4_K_XL.gguf", "lfs": {"oid": sha, "size": 450}},
            {"type": "file", "path": "BF16/M-BF16-00001-of-00002.gguf", "lfs": {"oid": sha, "size": 9}},
            {"type": "file", "path": "M-ohne-pruefsumme.gguf", "size": 5},
            {"type": "file", "path": "MTP/mtp-M-Q8_0.gguf", "lfs": {"oid": sha, "size": 40}},
            {"type": "file", "path": "mtp-M.gguf", "lfs": {"oid": sha, "size": 40}},
            {"type": "file", "path": "imatrix_unsloth.gguf", "lfs": {"oid": sha, "size": 4}},
            {"type": "file", "path": "mmproj-F32.gguf", "lfs": {"oid": sha, "size": 300}},
            {"type": "file", "path": "mmproj-BF16.gguf", "lfs": {"oid": sha, "size": 150}},
            {"type": "file", "path": "README.md"},
        ]

    def test_dateien_und_wahl(self):
        sprach, bild = H.dateien("x/M-GGUF", lambda url, params=None: self.baum())
        self.assertEqual([d.quant for d in sprach], ["Q4_K_M", "UD-Q4_K_XL", "Q8_0"])
        self.assertEqual(H.empfohlen(sprach).quant, "Q4_K_M")
        self.assertEqual(H.mmproj_wahl(bild).name, "mmproj-BF16.gguf")
        self.assertEqual(H.empfohlen([d for d in sprach if d.quant == "Q8_0"]).quant, "Q8_0")
        self.assertIsNone(H.empfohlen([]))

    def test_passt(self):
        gb = 2**30
        self.assertEqual(H.passt(5 * gb, 16 * 1024)[0], "✅")
        self.assertEqual(H.passt(13 * gb, 16 * 1024)[0], "⚠")
        self.assertEqual(H.passt(20 * gb, 16 * 1024)[0], "🐢")
        self.assertEqual(H.passt(1 * gb, 0)[0], "🐢")
        self.assertEqual(H.passt(20 * gb, 16 * 1024, 64 * 1024)[0], "🐢")      # VRAM + RAM reichen
        self.assertEqual(H.passt(80 * gb, 16 * 1024, 64 * 1024)[0], "❌")
        self.assertEqual(H.passt(5 * gb, 0, 32 * 1024)[0], "🐢")               # CPU, RAM reicht
        self.assertEqual(H.passt(30 * gb, 0, 32 * 1024)[0], "❌")


class _Antwort:
    def __init__(self, daten: bytes, status: int):
        self.daten, self.status_code = daten, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, n):
        for i in range(0, len(self.daten), 7):
            yield self.daten[i:i + 7]


class _Netz:
    """Wie httpx.Client.stream: liefert `inhalt`, mit Range ab der gewünschten Stelle (206) –
    außer kann_fortsetzen=False."""
    def __init__(self, inhalt: bytes, kann_fortsetzen: bool = True, status: int = 0):
        self.inhalt, self.kann_fortsetzen, self.status, self.aufrufe = inhalt, kann_fortsetzen, status, []

    def stream(self, methode, url, headers=None):
        if self.status:
            return _Antwort(b"", self.status)
        self.aufrufe.append(dict(headers or {}))
        rng = (headers or {}).get("Range")
        if rng and self.kann_fortsetzen:
            return _Antwort(self.inhalt[int(rng[6:-1]):], 206)
        return _Antwort(self.inhalt, 200)


class LadenTests(unittest.TestCase):
    INHALT = bytes(range(256)) * 9

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name) / "Modell"
        self.datei = H.Datei("sub/M-Q4_K_M.gguf", len(self.INHALT), hashlib.sha256(self.INHALT).hexdigest())
        self.schritte = []

    def laden(self, netz, stopp=None):
        return H.laden("x/M-GGUF", self.datei, self.ordner, lambda *a: self.schritte.append(a),
                       stopp or threading.Event(), session=netz)

    def test_geprueft_abgelegt(self):
        ziel = self.laden(_Netz(self.INHALT))
        self.assertEqual(ziel, self.ordner / "M-Q4_K_M.gguf")
        self.assertEqual(ziel.read_bytes(), self.INHALT)
        self.assertFalse((self.ordner / "M-Q4_K_M.gguf.part").exists())
        self.assertEqual(self.schritte[-1], ("lade", len(self.INHALT), len(self.INHALT)))

    def test_falscher_hash_verworfen(self):
        self.datei.sha256 = "0" * 64
        with self.assertRaises(H.PruefFehler):
            self.laden(_Netz(self.INHALT))
        self.assertEqual(list(self.ordner.iterdir()), [])

    def test_fortsetzen(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "M-Q4_K_M.gguf.part").write_bytes(self.INHALT[:1000])
        netz = _Netz(self.INHALT)
        self.assertEqual(self.laden(netz).read_bytes(), self.INHALT)
        self.assertEqual(netz.aufrufe[0], {"Range": "bytes=1000-"})
        self.assertEqual(self.schritte[0][0], "pruefe")

    def test_server_ohne_fortsetzen_faengt_neu_an(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "M-Q4_K_M.gguf.part").write_bytes(b"x" * 1000)
        self.assertEqual(self.laden(_Netz(self.INHALT, kann_fortsetzen=False)).read_bytes(), self.INHALT)

    def test_anhalten_laesst_teil_liegen(self):
        stopp = threading.Event()

        class _Bremse(_Netz):
            def stream(s, *a, **k):
                antwort = super().stream(*a, **k)
                stopp.set()
                return antwort
        with self.assertRaises(H.Abbruch):
            self.laden(_Bremse(self.INHALT), stopp)
        self.assertTrue((self.ordner / "M-Q4_K_M.gguf.part").exists())
        self.assertFalse((self.ordner / "M-Q4_K_M.gguf").exists())

    def test_server_fehler(self):
        with self.assertRaises(OSError):
            self.laden(_Netz(b"", status=404))
        self.assertFalse((self.ordner / "M-Q4_K_M.gguf").exists())

    def test_schon_da_ohne_netz(self):
        self.ordner.mkdir(parents=True)
        (self.ordner / "M-Q4_K_M.gguf").write_bytes(self.INHALT)
        netz = _Netz(b"")
        self.laden(netz)
        self.assertEqual(netz.aufrufe, [])


if __name__ == "__main__":
    unittest.main()
