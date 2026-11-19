"""Schlüssel in der .env: set_env, decrypt_env, secure_existing_keys mit Schein-Tresor."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import config                                          # noqa: E402

MARKER = "enc:"


class _Tresor:
    """Schein-keyvault: umkehrbar, ohne DPAPI."""

    def __init__(self, verfuegbar=True, kaputt=()):
        self.verfuegbar = verfuegbar
        self.kaputt = set(kaputt)          # Werte, die sich nicht entschlüsseln lassen

    def available(self):
        return self.verfuegbar

    def is_encrypted(self, wert):
        return isinstance(wert, str) and wert.startswith(MARKER)

    def encrypt(self, text):
        return MARKER + text[::-1]

    def decrypt(self, wert):
        if wert in self.kaputt:
            return None
        return wert[len(MARKER):][::-1]


class EnvTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = Path(tmp.name) / ".env"
        self.tresor = _Tresor()
        for ziel, wert in (("_ENV", self.env), ("keyvault", self.tresor)):
            p = mock.patch.object(config, ziel, wert)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {}, clear=False)
        p.start()
        self.addCleanup(p.stop)

    def _zeilen(self):
        return self.env.read_text(encoding="utf-8").splitlines()

    # set_env
    def test_set_env_schluessel_verschluesselt_auf_platte_klartext_live(self):
        self.assertTrue(config.set_env("TEST_API_KEY", "  geheim123  "))
        self.assertEqual(self._zeilen(), [f"TEST_API_KEY={MARKER}321mieheg"])
        self.assertEqual(os.environ["TEST_API_KEY"], "geheim123")

    def test_set_env_kein_schluessel_bleibt_klartext(self):
        self.assertTrue(config.set_env("TEST_MODELL", "klein"))
        self.assertEqual(self._zeilen(), ["TEST_MODELL=klein"])
        self.assertEqual(os.environ["TEST_MODELL"], "klein")

    def test_set_env_ersetzt_vorhandene_zeile_und_laesst_rest(self):
        self.env.write_text("# Kommentar\nTEST_API_KEY=alt\nANDERES=1\n", encoding="utf-8")
        self.assertTrue(config.set_env("TEST_API_KEY", "neu"))
        self.assertEqual(self._zeilen(), ["# Kommentar", f"TEST_API_KEY={MARKER}uen", "ANDERES=1"])

    def test_set_env_ohne_tresor_klartext(self):
        self.tresor.verfuegbar = False
        self.assertTrue(config.set_env("TEST_API_KEY", "offen"))
        self.assertEqual(self._zeilen(), ["TEST_API_KEY=offen"])

    def test_set_env_hinterlaesst_keine_zwischendatei(self):
        config.set_env("TEST_API_KEY", "x")
        self.assertEqual([p.name for p in self.env.parent.iterdir()], [".env"])

    def test_set_env_schreibfehler_false_und_nicht_live(self):
        with mock.patch.object(config, "_env_schreiben", side_effect=OSError("voll")):
            self.assertFalse(config.set_env("TEST_API_KEY", "x"))
        self.assertNotIn("TEST_API_KEY", os.environ)

    # decrypt_env
    def test_decrypt_env_ersetzt_umschlag_durch_klartext(self):
        os.environ["TEST_API_KEY"] = MARKER + "cba"
        os.environ["TEST_NORMAL"] = "bleibt"
        config.decrypt_env()
        self.assertEqual(os.environ["TEST_API_KEY"], "abc")
        self.assertEqual(os.environ["TEST_NORMAL"], "bleibt")

    def test_decrypt_env_entfernt_nicht_entschluesselbares(self):
        kaputt = MARKER + "fremd"
        self.tresor.kaputt.add(kaputt)
        os.environ["TEST_API_KEY"] = kaputt
        config.decrypt_env()
        self.assertNotIn("TEST_API_KEY", os.environ)

    # secure_existing_keys
    def test_secure_existing_keys_schuetzt_nur_klartext_schluessel(self):
        self.env.write_text(
            "# TEST_API_KEY=kommentar\n"
            "TEST_API_KEY=\"abc\"\n"
            f"ZWEITER_API_KEY={MARKER}schon\n"
            "export DRITTER_API_KEY=xyz # Notiz\n"
            "LEER_API_KEY=\n"
            "TEST_MODELL=klein\n", encoding="utf-8")
        self.assertEqual(config.secure_existing_keys(), 2)
        self.assertEqual(self._zeilen(), [
            "# TEST_API_KEY=kommentar",
            f"TEST_API_KEY={MARKER}cba",
            f"ZWEITER_API_KEY={MARKER}schon",
            f"export DRITTER_API_KEY={MARKER}zyx",
            "LEER_API_KEY=",
            "TEST_MODELL=klein",
        ])
        self.assertEqual(os.environ["TEST_API_KEY"], "abc")
        self.assertEqual(os.environ["DRITTER_API_KEY"], "xyz")

    def test_secure_existing_keys_nichts_zu_tun_laesst_datei_unberuehrt(self):
        self.env.write_text(f"TEST_API_KEY={MARKER}x\n", encoding="utf-8")
        vorher = self.env.stat().st_mtime_ns
        with mock.patch.object(config, "_env_schreiben") as schreiben:
            self.assertEqual(config.secure_existing_keys(), 0)
        schreiben.assert_not_called()
        self.assertEqual(self.env.stat().st_mtime_ns, vorher)

    def test_secure_existing_keys_ohne_tresor_oder_datei(self):
        self.assertEqual(config.secure_existing_keys(), 0)          # keine .env
        self.env.write_text("TEST_API_KEY=abc\n", encoding="utf-8")
        self.tresor.verfuegbar = False
        self.assertEqual(config.secure_existing_keys(), 0)
        self.assertEqual(self._zeilen(), ["TEST_API_KEY=abc"])

    def test_secure_existing_keys_schreibfehler_null(self):
        self.env.write_text("TEST_API_KEY=abc\n", encoding="utf-8")
        with mock.patch.object(config, "_env_schreiben", side_effect=OSError("voll")):
            self.assertEqual(config.secure_existing_keys(), 0)
        self.assertEqual(self._zeilen(), ["TEST_API_KEY=abc"])


if __name__ == "__main__":
    unittest.main()
