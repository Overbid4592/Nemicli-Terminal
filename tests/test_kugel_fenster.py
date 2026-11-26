"""Lebendige Fenster-Kugel (office_kugel._Kugel): Atmen, Ring-Gründe, weiches Überblenden, Ruhe-Takt, bewegtes
Porträt; dazu fenster_gesichter aus main_dienste.

python -m unittest tests.test_kugel_fenster
"""
import ast
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
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qt_testhilfe  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import office_kugel as OK  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen
APP = QApplication.instance() or QApplication([])


def _pix(farbe: str, b: int = 300, h: int = 200) -> QPixmap:
    p = QPixmap(b, h)
    p.fill(QColor(farbe))
    return p


def _webp(pfad: Path, bilder: int = 3) -> Path:
    from PIL import Image
    folge = [Image.new("RGB", (64, 64), (40 * i, 80, 200)) for i in range(bilder)]
    folge[0].save(pfad, save_all=True, append_images=folge[1:], duration=40, loop=0)
    return pfad


class Kugel(unittest.TestCase):
    def setUp(self):
        self.fenster = QWidget()
        self.fenster.resize(800, 600)
        with mock.patch.object(OK, "leben_an", return_value=True):
            self.ai = OK.OfficeAI(self.fenster, lambda: None)
        self.k = self.ai.kugel
        self.fenster.show()
        APP.processEvents()

    def tearDown(self):
        self.fenster.close()
        self.fenster.deleteLater()
        APP.processEvents()

    def test_atmet_in_ruhe_mit_ruhetakt(self):
        self.k._takt_planen()
        self.assertTrue(self.k._takt.isActive())
        self.assertEqual(self.k._takt.interval(), self.k.RUHE_MS)
        werte = set()
        for i in range(8):
            with mock.patch.object(OK.time, "monotonic", return_value=self.k._start + i * self.k.ATEM_S / 8):
                werte.add(round(self.k.atem(), 2))
        self.assertGreater(max(werte) - min(werte), 0.9)             # ein ganzer Atemzug: 0 … 1
        winkel = self.k.winkel
        self.k._zuletzt = time.monotonic() - 0.1
        self.k._tick()
        self.assertGreater(self.k.winkel, winkel)                    # Ring dreht in Ruhe langsam

    def test_ohne_leben_ruht_der_takt(self):
        self.k.leben = False
        self.k._takt_planen()
        self.assertFalse(self.k._takt.isActive())
        self.assertEqual(self.k.atem(), 0.0)
        self.assertFalse(self.k.film(str(ROOT / "gibt_es_nicht_kugel.webp")))

    def test_ring_gruende(self):
        self.k.arbeiten(True)
        self.k.arbeiten(True, "malen")
        self.assertEqual(self.k._takt.interval(), self.k.TAKT_MS)
        self.k.arbeiten(False)
        self.assertTrue(self.k.arbeitet)                             # Bild entsteht noch
        self.k.arbeiten(False, "malen")
        self.assertFalse(self.k.arbeitet)
        self.ai._arbeiten(True)
        self.assertTrue(self.k.arbeitet)
        self.ai._arbeiten(False)
        self.assertFalse(self.k.arbeitet)

    def test_weiches_ueberblenden(self):
        a, b = _pix("#ff0000"), _pix("#0000ff")
        self.ai.gesichter = {"": a, "denkt": b}
        self.ai._gesicht("")
        self.ai._gesicht("denkt")
        self.assertIs(self.k.bild, b)
        self.assertIs(self.k._alt, a)
        self.k.grab()                                                # mitten im Übergang zeichnen
        self.k._wechsel -= 1.0
        self.k._tick()
        self.assertIsNone(self.k._alt)

    def test_minimiert_schlaeft(self):
        self.fenster.hide()
        APP.processEvents()
        self.k._takt_planen()
        self.assertEqual(self.k._takt.interval(), self.k.SCHLAF_MS)
        winkel = self.k.winkel
        self.k._tick()
        self.assertEqual(self.k.winkel, winkel)                      # nichts gerechnet

    def test_zeichnen_in_allen_lagen(self):
        for bild in (None, _pix("#22aa22", 120, 400)):
            self.k.bild_setzen(bild)
            for arbeitet in (False, True):
                self.k.arbeiten(arbeitet)
                self.k.hebung = 1.0
                self.k.blick = [2.0, -2.0]
                self.assertFalse(self.k.grab().isNull())

    def test_quadrat_mittig_und_vorskaliert(self):
        q = OK._quadrat(_pix("#ffffff", 400, 100), 50)
        self.assertEqual((q.width(), q.height()), (50, 50))
        self.k.bild_setzen(_pix("#ffffff"))
        self.k.grab()
        self.k.grab()
        self.assertEqual(len(self.k._quadrate), 1)                   # einmal skaliert, dann aus dem Speicher


class Bewegt(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.fenster = QWidget()
        self.fenster.resize(800, 600)
        with mock.patch.object(OK, "leben_an", return_value=True):
            self.ai = OK.OfficeAI(self.fenster, lambda: None)
        self.fenster.show()
        APP.processEvents()

    def tearDown(self):
        self.fenster.close()
        self.fenster.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)       # Filme geben ihre Datei frei
        APP.processEvents()

    def test_stand_nimmt_nur_kugel_webp(self):
        film = _webp(self.tmp / "clip_kugel.webp")
        fremd = _webp(self.tmp / "clip.webp")
        self.ai.stand("Test", True, {"bewegt": str(film)})
        self.assertEqual(self.ai.bewegt, str(film))
        self.assertNotIn("bewegt", self.ai.gesichter)                # kein Standbild daraus
        for falsch in (str(fremd), str(self.tmp / "fehlt_kugel.webp"), 5):
            self.ai.stand("Test", True, {"bewegt": falsch})
            self.assertEqual(self.ai.bewegt, "")

    def test_film_beim_ersten_drueberfahren_und_nach_antwort(self):
        self.ai.stand("Test", True, {"bewegt": str(_webp(self.tmp / "a_kugel.webp"))})
        with mock.patch.object(self.ai.kugel, "film", return_value=True) as film:
            self.ai.ausklappen()
            self.ai.einklappen()
            self.ai.ausklappen()
            self.assertEqual(film.call_count, 1)                     # nur beim ersten Mal
            self.ai.laufend = 3
            self.ai.antwort(3, "fertig")
            self.assertEqual(film.call_count, 2)

    def test_film_laeuft_und_endet(self):
        k = self.ai.kugel
        self.assertTrue(k.film(str(_webp(self.tmp / "b_kugel.webp", bilder=3)), runden=1))
        self.assertFalse(k.film(str(self.tmp / "b_kugel.webp")))     # nicht doppelt
        self.assertFalse(k.grab().isNull())
        for _ in range(3):
            k._film_bild(0)
        self.assertIsNone(k._film)
        APP.processEvents()

    def test_kaputter_film_wird_nicht_gespielt(self):
        kaputt = self.tmp / "c_kugel.webp"
        kaputt.write_bytes(b"kein bild")
        self.assertFalse(self.ai.kugel.film(str(kaputt)))
        self.assertIsNone(self.ai.kugel._film)


class LebenSchalter(unittest.TestCase):
    def test_config(self):
        for wert, erwartet in (({"kugel_leben": False}, False), ({}, True)):
            with mock.patch.dict(sys.modules, {"config": types.SimpleNamespace(load=lambda w=wert: w)}):
                self.assertIs(OK.leben_an(), erwartet)
        with mock.patch.dict(sys.modules, {"config": types.SimpleNamespace(load=mock.Mock(side_effect=OSError))}):
            self.assertTrue(OK.leben_an())


class Gesichter(unittest.TestCase):
    """fenster_gesichter aus main_dienste (mit Schein-Modulen)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        quelle = (ROOT / "main_dienste.py").read_text(encoding="utf-8-sig")
        baum = ast.parse(quelle)
        teile = [n for n in baum.body if isinstance(n, ast.FunctionDef) and n.name == "fenster_gesichter"]
        self.assertEqual(len(teile), 1)
        self.ns = {"Path": Path, "PS": types.SimpleNamespace(active=lambda: types.SimpleNamespace(name="Test"))}
        exec(compile(ast.Module(body=teile, type_ignores=[]), "main_dienste.py", "exec"), self.ns)
        self.videos = self.tmp / "Videos"
        self.videos.mkdir()
        bild = self.tmp / "Test.png"
        bild.write_bytes(b"\x89PNG")
        self.film = None
        steuerung = types.SimpleNamespace(
            bilder=lambda _n: {"": bild, "denkt": bild, "traurig": bild},
            bewegtes_portraet=lambda _n: self.film)
        wache = types.ModuleType("wache")
        wache.steuerung = steuerung
        self.module = mock.patch.dict(sys.modules, {
            "wache": wache, "wache.steuerung": steuerung,
            "profilordner": types.SimpleNamespace(ordner=lambda unter, name, anlegen=True: self.tmp / unter),
            "profilbild": types.SimpleNamespace(pfad=lambda: bild)})
        self.module.start()
        self.addCleanup(self.module.stop)

    def test_bewegt_aus_der_steuerung(self):
        self.film = self.videos / "neu_kugel.webp"                   # Suche selbst: test_kugel_desktop_film
        g = self.ns["fenster_gesichter"]()
        self.assertEqual(g["bewegt"], str(self.film))
        self.assertEqual(set(g), {"", "denkt", "bewegt"})            # nur Kugel-Stimmungen

    def test_ohne_film_kein_eintrag(self):
        self.assertNotIn("bewegt", self.ns["fenster_gesichter"]())


class AtelierRing(unittest.TestCase):
    def test_ring_dreht_beim_malen(self):
        import atelier_fenster
        f = atelier_fenster.AtelierFenster(None)
        try:
            f._malt(True, {})
            self.assertTrue(f.nemi.kugel.arbeitet)
            f._malt(False, {})
            self.assertFalse(f.nemi.kugel.arbeitet)
            f._malt(True, {})
            f._getrennt()
            self.assertFalse(f.nemi.kugel.arbeitet)
        finally:
            f.close()
            f.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
