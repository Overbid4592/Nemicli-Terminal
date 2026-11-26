"""Desktop-Kugel beim Arbeiten: bewegtes Porträt in Schleife, weicher Ring, Schweben, Schimmer, Lichtpunkte;
dazu steuerung.bewegtes_portraet.

python -m unittest tests.test_kugel_desktop_film
"""
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
import main_quelle  # noqa: E402,F401
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wache import kugel as K, steuerung  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen
APP = QApplication.instance() or QApplication([])


def _webp(pfad: Path, bilder: int = 3) -> Path:
    from PIL import Image
    folge = [Image.new("RGB", (64, 64), (40 * i, 80, 200)) for i in range(bilder)]
    folge[0].save(pfad, save_all=True, append_images=folge[1:], duration=40, loop=0)
    return pfad


def _pix(farbe: str = "#22aa22") -> QPixmap:
    p = QPixmap(120, 160)
    p.fill(QColor(farbe))
    return p


class _Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.f = K._Kugelfenster()

    def tearDown(self):
        self.f.film_stoppen(melden=False)
        self.f.close()
        self.f.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)       # Filme geben ihre Datei frei
        APP.processEvents()

    def _takte(self, n: int, dt: float = 0.033) -> None:
        for _ in range(n):
            self.f._zuletzt -= dt
            self.f.takt()


class Bewegung(_Basis):
    def test_ring_beschleunigt_und_bremst_weich(self):
        self._takte(5)
        self.f.beschaeftigt = True
        drehs, winkel = [], []
        for _ in range(60):
            self._takte(1)
            drehs.append(self.f._dreh)
            winkel.append(self.f._winkel)
        self.assertTrue(all(b >= a for a, b in zip(drehs, drehs[1:])))          # steigt stetig
        self.assertLess(drehs[0], K.DREH_ARBEIT / 4)                             # kein Sprung
        self.assertGreater(drehs[-1], K.DREH_ARBEIT * 0.9)
        schritte = [(b - a) % 360 for a, b in zip(winkel, winkel[1:])]
        self.assertLess(max(schritte), K.DREH_ARBEIT * 0.1)                      # je Takt höchstens ~7°
        self.f.beschaeftigt = False
        self._takte(1)
        self.assertGreater(self.f._dreh, K.DREH_RUHE * 4)                        # bremst, steht nicht sofort
        self._takte(120)
        self.assertLess(self.f._dreh, K.DREH_RUHE * 1.5)

    def test_pause_macht_keinen_sprung(self):
        self.f.beschaeftigt = True
        self.f._zuletzt -= 30.0                                                  # war lange versteckt
        w = self.f._winkel
        self.f.takt()
        self.assertLess((self.f._winkel - w) % 360, K.DREH_ARBEIT * 0.11)

    def test_arbeit_blendet_ein_und_aus(self):
        self.assertEqual(self.f.arbeit, 0.0)
        self.f.beschaeftigt = True
        self._takte(60)
        self.assertGreater(self.f.arbeit, 0.95)
        self.f.schlaeft = True
        self._takte(90)
        self.assertEqual(self.f.arbeit, 0.0)                                     # im Schlaf nichts

    def test_schweben_zeigt_nie_den_bildrand(self):
        r = 32.0
        self.f.arbeit = 1.0
        for i in range(200):
            with mock.patch.object(K.time, "monotonic", return_value=i * 0.37):
                zoom, dx, dy = self.f._schweben(r)
            self.assertGreaterEqual(r * (zoom - 1.0) + 1e-9, max(abs(dx), abs(dy)))
        self.f.arbeit = 0.0
        self.assertEqual(self.f._schweben(r), (1.0, 0.0, 0.0))

    def test_zeichnen_in_allen_lagen(self):
        for bild in (None, _pix()):
            self.f.bild = bild
            for arbeit, beschaeftigt in ((0.0, False), (1.0, True)):
                self.f.arbeit, self.f.beschaeftigt = arbeit, beschaeftigt
                for t in (0.2, 2.0):                                             # mit und ohne Schimmer
                    with mock.patch.object(K.time, "monotonic", return_value=t):
                        self.assertFalse(self.f.grab().isNull())


class Film(_Basis):
    def test_schleife_laeuft_bis_ausklingen(self):
        self.f.bild = _pix()
        fertig = []
        self.f.film_starten(_webp(self.tmp / "a_kugel.webp"), lambda: fertig.append(1), schleife=True)
        self.assertTrue(self.f.schleife_laeuft)
        self.assertIsNotNone(self.f._alt_bild)                                   # weich vom Standbild in den Film
        for _ in range(30):
            self.f._film_bild(0)
        self.assertTrue(self.f.schleife_laeuft)
        self.assertEqual(fertig, [])
        self.f.film_ausklingen()
        self.assertFalse(self.f.schleife_laeuft)
        self.assertIsNotNone(self.f._film)                                       # Runde läuft zu Ende
        self.assertLessEqual(self.f._film_rest, 2 * 3)
        for _ in range(6):
            self.f._film_bild(0)
        self.assertIsNone(self.f._film)
        self.assertEqual(fertig, [1])

    def test_normaler_film_ist_keine_schleife(self):
        self.f.film_starten(_webp(self.tmp / "b_kugel.webp"), None, runden=1)
        self.assertFalse(self.f.schleife_laeuft)
        rest = self.f._film_rest
        self.assertLessEqual(rest, 3)
        self.f.film_ausklingen()                                                 # ändert nichts
        self.assertEqual(self.f._film_rest, rest)


class Pflegen(_Basis):
    """Kugel._film_pflegen mit einem schlanken Stellvertreter statt der ganzen Kugel."""

    def setUp(self):
        super().setUp()
        self.film = _webp(self.tmp / "c_kugel.webp")
        self.suchen = mock.Mock(return_value=self.film)
        self.k = types.SimpleNamespace(fenster=self.f, _schleife_dran=True, _schleifen_film=self.suchen,
                                       _nach_film=lambda: None)
        self.f.show()
        APP.processEvents()

    def _pflegen(self):
        K.Kugel._film_pflegen(self.k)

    def test_startet_beim_arbeiten_einmal_und_klingt_aus(self):
        self._pflegen()
        self.assertIsNone(self.f._film)                                          # Ruhe: kein Film
        self.f.beschaeftigt = True
        self._pflegen()
        self.assertTrue(self.f.schleife_laeuft)
        film = self.f._film
        self._pflegen()
        self.assertIs(self.f._film, film)                                        # nicht neu gestartet
        self.f.beschaeftigt = False
        self._pflegen()
        self.assertFalse(self.f.schleife_laeuft)
        self.assertIsNotNone(self.f._film)                                       # klingt aus

    def test_ein_versuch_je_arbeitsphase(self):
        self.suchen.return_value = None
        self.f.beschaeftigt = True
        for _ in range(5):
            self._pflegen()
        self.assertEqual(self.suchen.call_count, 1)
        self.f.beschaeftigt = False
        self._pflegen()
        self.f.beschaeftigt = True
        self._pflegen()
        self.assertEqual(self.suchen.call_count, 2)

    def test_versteckt_oder_schlafend_stoppt(self):
        self.f.beschaeftigt = True
        self._pflegen()
        self.assertTrue(self.f.schleife_laeuft)
        self.f.hide()
        self._pflegen()
        self.assertIsNone(self.f._film)
        self.f.show()
        self.k._schleife_dran = True
        self.f.schlaeft = True
        self._pflegen()
        self.assertIsNone(self.f._film)

    def test_fremder_film_wird_nicht_angefasst(self):
        self.f.film_starten(self.film, None, runden=2)                           # von der Persönlichkeit
        rest = self.f._film_rest
        self.f.beschaeftigt = True
        self._pflegen()
        self.f.beschaeftigt = False
        self._pflegen()
        self.assertEqual(self.f._film_rest, rest)
        self.assertFalse(self.f.schleife_laeuft)


class PortraetSuche(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.videos = Path(tmp.name) / "Videos"
        self.videos.mkdir()
        modul = types.SimpleNamespace(ordner=lambda unter, name, anlegen=True: self.videos)
        patcher = mock.patch.dict(sys.modules, {"profilordner": modul})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_neuestes_rundes_portraet(self):
        alt, neu, frei = (self.videos / n for n in ("alt_kugel.webp", "neu_kugel.webp", "neu_frei_kugel.webp"))
        for i, p in enumerate((alt, neu, frei)):
            p.write_bytes(b"x")
            os.utime(p, (1000 + i, 1000 + i))
        (self.videos / "film.webp").write_bytes(b"x")
        self.assertEqual(steuerung.bewegtes_portraet("Test"), neu)

    def test_ohne_film_none(self):
        self.assertIsNone(steuerung.bewegtes_portraet("Test"))
        with mock.patch.dict(sys.modules, {"profilordner": None}):              # Modul fehlt
            self.assertIsNone(steuerung.bewegtes_portraet("Test"))


if __name__ == "__main__":
    unittest.main()
