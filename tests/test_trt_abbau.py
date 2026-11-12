"""Entladen ohne Stillstand: TensorRT-Netze und Krea-RAM-Kopien werden Stück für Stück freigegeben, mit Pause
dazwischen; Krea räumt erst nach 15 Minuten ohne Bild."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from trtmotor import bild, laufzeit, video  # noqa: E402


class _Teil:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def __del__(self):
        self.log.append(self.name)


def _netz(nr, log):
    return SimpleNamespace(**{t: _Teil(f"{nr}.{t}", log) for t in ("ctx", "_puffer", "cache", "rc", "engine")})


class Abbau(unittest.TestCase):
    def test_kontext_vor_gewichten_mit_pausen(self):
        log, pausen = [], []
        n = _netz(1, log)
        with mock.patch.object(laufzeit.time, "sleep", side_effect=pausen.append):
            laufzeit.abbauen(n)
        self.assertEqual(log, ["1.ctx", "1._puffer", "1.cache", "1.rc", "1.engine"])
        self.assertEqual(pausen, [laufzeit.ABBAU_PAUSE_S] * 5)

    def test_kette_netz_fuer_netz_und_einzelnetz(self):
        log, pausen = [], []
        kette = SimpleNamespace(netze=[_netz(1, log), _netz(2, log)])
        eintraege = [{"netz": kette, "mess": object()}, {"netz": _netz(3, log)}, {"baut": True}]
        with mock.patch.object(laufzeit.time, "sleep", side_effect=pausen.append):
            laufzeit.stand_abbauen(eintraege)
        self.assertEqual([x.split(".")[0] for x in log], ["2"] * 5 + ["1"] * 5 + ["3"] * 5)
        self.assertEqual(len(pausen), 15)
        self.assertEqual(kette.netze, [])
        self.assertTrue(all(e == {} for e in eintraege))

    def test_bild_und_video_leeren_ihren_stand(self):
        for modul in (bild, video):
            with self.subTest(modul=modul.__name__):
                log = []
                modul._stand["x"] = {"netz": SimpleNamespace(netze=[_netz(1, log)])}
                with mock.patch.object(laufzeit.time, "sleep"):
                    modul.entladen()
                self.assertEqual(modul._stand, {})
                self.assertEqual(len(log), 5)


class Krea(unittest.TestCase):
    def test_fuenf_minuten_und_ram_stueckweise(self):
        import krea
        self.assertEqual(krea.DEFAULTS["entladen_nach"], 300)
        pausen = []
        with mock.patch.dict(krea._RAM, {"text": ("a", 1), "dit": ("b", 2), "vae": ("c", 3)}, clear=True), \
                mock.patch.object(krea.time, "sleep", side_effect=pausen.append), \
                mock.patch.object(krea, "_frei"), mock.patch.object(bild, "entladen") as tb:
            krea.entladen()
            self.assertEqual(krea._RAM, {})
        self.assertEqual(pausen.count(0.02), 3)
        tb.assert_called_once()


if __name__ == "__main__":
    unittest.main()
