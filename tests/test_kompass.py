"""🧭 Kompass: Sitzungsseite (core/kompass.py) und das Fenster (ui/kompass_fenster.py, offscreen)."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import commands  # noqa: E402
import kompass  # noqa: E402
import qt_testhilfe  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import kompass_fenster
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # PySide6 fehlt
    kompass_fenster = None


class _Bruecke:
    def __init__(self):
        self.gesendet = []

    def verbunden(self):
        return True

    def senden(self, ev):
        self.gesendet.append(ev)


class Sitzung(unittest.TestCase):
    def setUp(self):
        self.b = _Bruecke()
        for p in (mock.patch.object(kompass, "_bruecke", self.b), mock.patch.object(kompass, "_gesendet", ""),
                  mock.patch.object(kompass, "_wunsch", ""), mock.patch.object(kompass, "_setzen", None),
                  mock.patch.object(kompass, "an", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_daten_aus_commands(self):
        d = kompass.daten()
        namen = [b["name"] for b in d["befehle"]]
        self.assertEqual([c.name for c in commands.COMMAND_LIST], namen)
        self.assertIn("/kompass", namen)
        self.assertEqual("an", d["untermenues"]["/kompass"][0]["zusatz"])
        self.assertTrue(any("/kompass" in t["befehle"] for t in d["themen"]))

    def test_tippen_zeigen_und_verstecken(self):
        kompass.getippt("/wa")
        kompass.getippt("/wa")                                   # gleich: nichts Neues
        kompass.getippt("/wache")
        kompass.getippt("hallo")
        kompass.getippt("hallo du")                              # schon versteckt: nichts
        self.assertEqual([{"t": "eingabe", "text": "/wa"}, {"t": "eingabe", "text": "/wache"}, {"t": "verstecken"}],
                         self.b.gesendet)

    def test_aus_oder_mehrzeilig_zeigt_nichts(self):
        kompass.getippt("/wache\nzweite Zeile")
        with mock.patch.object(kompass, "an", return_value=False):
            kompass.getippt("/wache")
        self.assertEqual([], self.b.gesendet)

    def test_laden_bringt_befehle_und_stand(self):
        kompass._wunsch = "/bild"
        kompass._laden({})
        self.assertEqual("befehle", self.b.gesendet[0]["t"])
        self.assertEqual({"t": "eingabe", "text": "/bild"}, self.b.gesendet[1])

    def test_einsetzen_nur_einzeiliger_befehl(self):
        gesetzt = []
        kompass.getippt("/w", gesetzt.append)
        for text in ("/wache status", "/wache trigger ", "hallo", "/wache\nrm x", "/x\rweiter", ""):
            kompass._einsetzen({"text": text})
        self.assertEqual(["/wache status", "/wache trigger "], gesetzt)

    def test_fenster_wird_einmal_gestartet(self):
        with mock.patch.object(kompass, "_bruecke", None), mock.patch.object(kompass, "_gestartet", 0.0), \
                mock.patch.object(kompass, "_neue_bruecke", return_value=mock.Mock(verbunden=lambda: False)), \
                mock.patch.object(kompass, "_fenster_starten") as start:
            kompass.getippt("/a")
            kompass.getippt("/ab")                               # binnen NEUSTART_S kein zweiter Start
        self.assertEqual(1, start.call_count)

    def test_fenster_befehl(self):
        self.assertIn("--kompass", kompass.fenster_befehl())


@unittest.skipIf(kompass_fenster is None, "PySide6 fehlt")
class Fenster(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.auftraege = []

            def senden(self, a):
                self.auftraege.append(a)

        self.leitung = Leitung()
        self.k = kompass_fenster.Kompass(self.leitung)
        self.addCleanup(self.k.deleteLater)
        self.leitung.ereignis.emit({"t": "befehle", **kompass.daten()})

    def test_meldet_sich_an(self):
        self.assertEqual({"op": "laden"}, self.leitung.auftraege[0])

    def test_passender_befehl_leuchtet_oben(self):
        with mock.patch.object(kompass_fenster, "_vordergrund_rechteck", return_value=None):
            self.leitung.ereignis.emit({"t": "eingabe", "text": "/wac"})
        self.assertTrue(self.k.isVisible())
        erste = next(iter(self.k.karten))
        self.assertEqual("/wache", erste)
        self.assertIn("/wache leuchtet auf", self.k.hinweis.text())
        self.leitung.ereignis.emit({"t": "verstecken"})
        self.assertFalse(self.k.isVisible())

    def test_klick_setzt_befehl(self):
        self.k.waehlen({"name": "/wache"}, {"zusatz": "status", "anzeige": "x", "frage": None})
        self.k.waehlen({"name": "/wache"}, {"zusatz": "trigger {}", "anzeige": "x", "frage": "Ab wie vielen?"})
        self.assertEqual([{"op": "einsetzen", "text": "/wache status"}, {"op": "einsetzen", "text": "/wache trigger "}],
                         self.leitung.auftraege[1:])
        self.assertIn("Ab wie vielen?", self.k.hinweis.text())

    def test_schliessen_bis_zur_naechsten_eingabe(self):
        with mock.patch.object(kompass_fenster, "_vordergrund_rechteck", return_value=None):
            self.leitung.ereignis.emit({"t": "eingabe", "text": "/b"})
            self.k.close()
            self.assertFalse(self.k.isVisible())
            self.leitung.ereignis.emit({"t": "eingabe", "text": "/bi"})
            self.assertFalse(self.k.isVisible())                  # zu bis die Eingabe kein / mehr hat
            self.leitung.ereignis.emit({"t": "verstecken"})
            self.leitung.ereignis.emit({"t": "eingabe", "text": "/"})
            self.assertTrue(self.k.isVisible())

    def test_suche(self):
        b = kompass_fenster.Befehle(kompass.daten())
        treffer, bester = b.suchen("/zeitp")
        self.assertEqual("/zeitplan", bester)
        alle, keiner = b.suchen("/")
        self.assertIsNone(keiner)
        self.assertEqual(len(commands.COMMAND_LIST), len(alle))
        self.assertEqual(([], None), b.suchen("/gibtsnichtxyz"))

    def test_saetze_halten_abkuerzungen(self):
        self.assertEqual(["Eins, z. B. so.", "Zwei;", "drei!"], kompass_fenster.saetze("Eins, z. B. so. Zwei; drei!"))


if __name__ == "__main__":
    unittest.main()
