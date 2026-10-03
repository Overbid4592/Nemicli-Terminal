"""Wache ↔ NemiCLI: Lebenszeichen behält Start und Build, Neustart bei anderem Stand,
Stopp binnen Sekunden, Startschalter der Kugel, Entladen des lokalen Modells."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "tools", "engines", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from wache import dienst, zugang      # noqa: E402


class Lebenszeichen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        lock = Path(self.tmp.name) / "wache.lock"
        for p in (mock.patch.object(zugang, "LOCK", lock), mock.patch.object(zugang, "ordner_anlegen", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.lock = lock

    def test_start_und_build_bleiben(self):
        zugang.lock_schreiben()
        erstes = json.loads(self.lock.read_text(encoding="utf-8"))
        time.sleep(0.02)
        zugang.lock_schreiben()
        zweites = json.loads(self.lock.read_text(encoding="utf-8"))
        self.assertEqual(erstes["start"], zweites["start"])
        self.assertEqual(zweites["start"], zugang._START)
        self.assertEqual(zweites["build"], zugang._build())
        self.assertGreater(zweites["zuletzt"], erstes["zuletzt"])

    def test_veraltet(self):
        build = zugang._build()
        with mock.patch.object(zugang, "code_neuer_als", return_value=False):
            self.assertFalse(zugang.veraltet(None))
            self.assertFalse(zugang.veraltet({"hier": True}))
            self.assertFalse(zugang.veraltet({"build": build, "start": 1.0}))
            self.assertTrue(zugang.veraltet({"build": build + "x", "start": 1.0}))
            self.assertTrue(zugang.veraltet({"start": 1.0}))              # alte Wache ohne Build
        with mock.patch.object(zugang, "code_neuer_als", return_value=True):
            self.assertTrue(zugang.veraltet({"build": build, "start": 1.0}))


def _dienst_ohne_start(**felder):
    d = dienst.Dienst.__new__(dienst.Dienst)
    d._ende = threading.Event()
    d._einstellungs_stand = ""
    d.motor = mock.Mock()
    d.kugel = mock.Mock()
    d.tray = None
    for k, v in felder.items():
        setattr(d, k, v)
    return d


class Dienst(unittest.TestCase):
    def test_stopp_binnen_sekunden(self):
        d = _dienst_ohne_start()
        d.beenden = d._ende.set
        with mock.patch.object(zugang, "stopp_gewuenscht", return_value=True), \
                mock.patch.object(zugang, "STOPP", Path(tempfile.gettempdir()) / "nemicli_test_stopp_gibtsnicht"):
            t0 = time.monotonic()
            t = threading.Thread(target=d._waechter)
            t.start()
            t.join(5)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - t0, 3)
        self.assertGreaterEqual(zugang.STOPP_WARTEN_S, 12)            # stoppen() wartet länger als ein Takt

    def test_ohne_kugel_bleibt_bis_der_nutzer_sie_zeigt(self):
        d = _dienst_ohne_start(kugel_erlaubt=False, _kugel_schalter=False)
        with mock.patch.object(dienst, "einstellungen", return_value={"kugel": True}):
            d._einstellungen_pruefen()
            self.assertFalse(d.kugel_erlaubt)
            with mock.patch("wache.justierung.speichern"):
                d.aktion("kugel")                                     # Nutzer zeigt sie im Tray
            self.assertTrue(d.kugel_erlaubt)
        with mock.patch.object(dienst, "einstellungen", return_value={"kugel": True, "x": 1}):
            d._einstellungen_pruefen()
            self.assertTrue(d.kugel_erlaubt)


class LokalesModell(unittest.TestCase):
    def test_entladen_unter_sperre_und_nach_fehlern(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        start = code.index("def wache_lauf()")
        teil = code[start:code.index("async def run_classic", start)]
        self.assertIn("_freigabe_absagen()", teil[teil.index("async def wecken"):])
        chat = teil[teil.index("async def chat("):teil.index("async def _kugel_gespraech")]
        self.assertIn("_freigabe_absagen()", chat)
        self.assertIn("finally:", chat)
        self.assertIn("_freigabe_planen(KUGEL_MODELL_HALTEN_S)", chat)
        entladen = teil[teil.index("async def _entladen"):]
        self.assertLess(entladen.index("async with _lock()"), entladen.index("_lokales_modell_freigeben"))
        self.assertIn('gespraech["freigabe_nr"] == nr', entladen)


if __name__ == "__main__":
    unittest.main()
