"""Offline-Tests für core/uvsetup.py – ohne echten Download/Subprocess."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import uvsetup   # noqa: E402


class UvSetupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        # alle Pfade in den Temp-Ordner umbiegen
        self._old = {k: getattr(uvsetup, k) for k in
                     ("RUNTIME", "UV_DIR", "UV_EXE", "PY_INSTALL_DIR",
                      "CACHE_DIR", "BIN_DIR", "VENV_DIR")}
        uvsetup.RUNTIME = t / ".runtime"
        uvsetup.UV_DIR = uvsetup.RUNTIME / "uv"
        uvsetup.UV_EXE = uvsetup.UV_DIR / "uv.exe"
        uvsetup.PY_INSTALL_DIR = uvsetup.RUNTIME / "python"
        uvsetup.CACHE_DIR = uvsetup.RUNTIME / "cache"
        uvsetup.BIN_DIR = uvsetup.RUNTIME / "bin"
        uvsetup.VENV_DIR = t / "venv"

    def tearDown(self):
        for k, v in self._old.items():
            setattr(uvsetup, k, v)
        self._tmp.cleanup()

    def test_env_is_isolated(self):
        e = uvsetup._env()
        self.assertEqual(e["UV_PYTHON_INSTALL_DIR"], str(uvsetup.PY_INSTALL_DIR))
        self.assertEqual(e["UV_CACHE_DIR"], str(uvsetup.CACHE_DIR))
        self.assertEqual(e["UV_NO_MODIFY_PATH"], "1")
        self.assertEqual(e["UV_PYTHON_PREFERENCE"], "only-managed")

    def test_allowed_hosts_cover_github_redirect(self):
        # Der eigentliche Download-Host von GitHub-Releases muss erlaubt sein.
        self.assertIn("github.com", uvsetup._ALLOWED_HOSTS)
        self.assertIn("release-assets.githubusercontent.com", uvsetup._ALLOWED_HOSTS)

    def test_ensure_uv_rejects_foreign_host(self):
        with patch.object(uvsetup, "UV_URL", "https://evil.example/uv.zip"):
            with self.assertRaises(RuntimeError):
                uvsetup.ensure_uv()

    def test_ensure_uv_extracts_zip(self):
        # Fake-Download: httpx.stream liefert ein echtes Zip mit uv.exe als Bytes.
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("uv.exe", b"MZ fake binary")
            z.writestr("uvx.exe", b"MZ")
        zip_bytes = buf.getvalue()

        def fake_stream(method, url, **kw):
            class R:
                url = uvsetup.UV_URL
                headers = {"content-length": str(len(zip_bytes))}
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
                def raise_for_status(self_): pass
                def iter_bytes(self_, n): return iter([zip_bytes])
            return R()

        import httpx
        with patch.object(httpx, "stream", fake_stream):
            path = uvsetup.ensure_uv()
        self.assertTrue(path.exists())
        self.assertTrue(uvsetup.uv_available())
        self.assertEqual(path.read_bytes(), b"MZ fake binary")

    def test_status_shape(self):
        s = uvsetup.status()
        for k in ("uv", "python", "venv", "runtime_dir", "venv_dir"):
            self.assertIn(k, s)

    def test_python_ready_false_when_empty(self):
        self.assertFalse(uvsetup.python_ready())
        self.assertIsNone(uvsetup.venv_python())


    def test_run_timeout_greift_ohne_ausgabe(self):
        # Ein Prozess, der nichts schreibt, muss trotzdem an der Zeitgrenze enden.
        code, aus = uvsetup._run([sys.executable, "-c", "import time; time.sleep(30)"],
                                 timeout=1)
        self.assertEqual(code, 124)
        self.assertIn("Zeitüberschreitung", aus)
        self.assertFalse(uvsetup._LAUFEND)

    def test_run_bricht_bei_stillstand_ab(self):
        code, aus = uvsetup._run([sys.executable, "-c", "import time; time.sleep(30)"],
                                 stillstand=1)
        self.assertEqual(code, 124)
        self.assertIn("keine Aktivität", aus)

    def test_fortschritt_balken_waehrend_download(self):
        zeilen = ["Resolved 10 packages in 2.33s",
                  "Downloading sympy (6.0MiB)", "Downloading torch (1.9GiB)",
                  " Downloaded sympy"]
        text = uvsetup.fortschritt(zeilen, 450, 4.1, 88, {"torch": "PyTorch CUDA 13.2"})
        self.assertTrue(text.startswith("PyTorch CUDA 13.2 · 450/1952 MB · 4.1 MB/s"), text)
        self.assertIn("noch ~", text)
        self.assertNotIn("sympy", text)

    def test_fortschritt_nie_ueber_hundert_vor_ende(self):
        zeilen = ["Downloading torch (100.0MiB)"]
        text = uvsetup.fortschritt(zeilen, 500, 1.0, 10)
        self.assertIn("99/100 MB", text)

    def test_fortschritt_nach_download_installiert(self):
        zeilen = ["Downloading torch (1.9GiB)", " Downloaded torch"]
        self.assertIn("installiere", uvsetup.fortschritt(zeilen, 1900, 0, 400))

    def test_io_zaehler_misst_eigenen_prozess(self):
        import os
        io = uvsetup._IoZaehler(os.getpid())
        try:
            _, alles = io.lesen()
            self.assertGreater(alles, 0)
        finally:
            io.schliessen()

    def test_run_liefert_ausgabe(self):
        code, aus = uvsetup._run([sys.executable, "-c", "print('eins'); print('zwei')"])
        self.assertEqual(code, 0)
        self.assertEqual(aus.splitlines(), ["eins", "zwei"])

    def test_install_sperre_verhindert_zweiten_lauf(self):
        with uvsetup.install_sperre():
            with self.assertRaises(RuntimeError):
                with uvsetup.install_sperre():
                    pass
        with uvsetup.install_sperre():      # danach wieder frei
            pass


if __name__ == "__main__":
    unittest.main()
