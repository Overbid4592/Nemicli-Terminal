"""Kern-Fenster: ganzer Kern-Stand als JSON bearbeiten – Prüfung, Sicherung, Verlauf, Schutz vor
Überschreiben, Brücke zur Sitzung und das Fenster selbst."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import charakter  # noqa: E402
import paths  # noqa: E402
import profilordner  # noqa: E402
import qt_testhilfe  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import kern_fenster
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # PySide6 fehlt
    kern_fenster = None

KERN = "an adult woman with long red hair and green eyes"
NEU = "an adult woman with short black hair and brown eyes"


class _Daten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(paths, "DATEN", Path(tmp.name)),
                  mock.patch.object(profilordner, "aktiv", lambda: "PersonA")):
            p.start()
            self.addCleanup(p.stop)
        charakter.schreiben({"name": "PersonA", "kern": KERN, "seed_referenz": 42, "stil": "photo"}, "PersonA")

    def bearbeitet(self, **felder) -> str:
        d = json.loads(charakter.kern_json("PersonA"))
        d.update(felder)
        return json.dumps(d, ensure_ascii=False)


class JsonSpeichern(_Daten):
    def test_ganzer_stand_wird_uebernommen(self):
        basis = charakter.kern_stand("PersonA")
        charakter.kern_json_setzen(self.bearbeitet(kern=NEU, sprechweise="frech, kurze Sätze", gesperrt=True),
                                   "PersonA", basis=basis)
        s = charakter.kern_stand("PersonA")
        self.assertEqual((NEU, "frech, kurze Sätze", True), (s["kern"], s["sprechweise"], s["gesperrt"]))
        self.assertEqual(KERN, s["vorher"][0])                       # der alte Kern bleibt zurückholbar
        self.assertEqual(NEU, charakter.roh("PersonA")["kern"])      # Spiegel in der Charakter-Datei
        self.assertEqual(charakter.kern_datei("PersonA").read_text(encoding="utf-8"),
                         charakter.kern_sicherung("PersonA").read_text(encoding="utf-8"))
        self.assertEqual(KERN, charakter.kern_zurueck("PersonA"))

    def test_geleerter_verlauf_behaelt_den_alten_kern(self):
        charakter.kern_json_setzen(self.bearbeitet(kern=NEU, vorher=[]), "PersonA")
        self.assertEqual([KERN], charakter.kern_stand("PersonA")["vorher"])

    def test_fehler_werden_verstaendlich_gemeldet(self):
        for text, teil in (('{"kern": "x",}', "JSON"), ("[1, 2]", "Objekt"),
                           (self.bearbeitet(farbe="rot"), "Unbekanntes Feld"),
                           (self.bearbeitet(gesperrt="ja"), "true oder false"),
                           (self.bearbeitet(vorher="alt"), "Liste"),
                           (self.bearbeitet(kern="  "), "leer"),
                           (self.bearbeitet(sprechweise="x" * (charakter.MAX_SPRECHWEISE + 1)), "zu lang")):
            with self.subTest(teil), self.assertRaisesRegex(ValueError, teil):
                charakter.kern_json_setzen(text, "PersonA")
        self.assertEqual(KERN, charakter.kern("PersonA"))

    def test_fremde_aenderung_wird_nicht_ueberschrieben(self):
        basis = charakter.kern_stand("PersonA")
        charakter.kern_setzen("an adult woman with blue hair", "PersonA")       # woanders geändert
        with self.assertRaisesRegex(ValueError, "inzwischen"):
            charakter.kern_json_setzen(self.bearbeitet(kern=NEU), "PersonA", basis=basis)
        self.assertEqual("an adult woman with blue hair", charakter.kern("PersonA"))


class _Bruecke:
    def __init__(self, loop, handlers, **_):
        self.handlers, self.ereignisse = handlers, []

    def senden(self, ev):
        self.ereignisse.append(ev)

    def verbunden(self):
        return True


class Sitzung(_Daten, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import gui_bruecke

        import main
        for p in (mock.patch.object(gui_bruecke, "GuiBruecke", _Bruecke),
                  mock.patch.object(main.PS, "active", lambda: NS(name="PersonA"))):
            p.start()
            self.addCleanup(p.stop)
        self.bruecke = await main.start_kern()

    async def letztes(self, art):
        for _ in range(100):
            if (treffer := [e for e in self.bruecke.ereignisse if e["t"] == art]):
                return treffer[-1]
            await asyncio.sleep(0.01)
        self.fail(f"kein {art}: {self.bruecke.ereignisse}")

    async def test_laden_und_speichern(self):
        self.bruecke.handlers["laden"]({})
        stand = await self.letztes("stand")
        self.assertEqual("PersonA", stand["name"])
        self.assertEqual(KERN, json.loads(stand["json"])["kern"])
        text = stand["json"].replace(KERN, NEU)
        self.bruecke.handlers["speichern"]({"json": text, "basis": stand["basis"], "name": "PersonA"})
        fertig = await self.letztes("gespeichert")
        self.assertEqual(NEU, json.loads(fertig["json"])["kern"])
        self.assertEqual(NEU, charakter.kern("PersonA"))

    async def test_kleidung_fragt_erst_nach(self):
        self.bruecke.handlers["laden"]({})
        stand = await self.letztes("stand")
        text = stand["json"].replace(KERN, KERN + ", wearing a red dress")
        auftrag = {"json": text, "basis": stand["basis"], "name": "PersonA"}
        self.bruecke.handlers["speichern"](auftrag)
        self.assertTrue((await self.letztes("kleidung"))["woerter"])
        self.assertEqual(KERN, charakter.kern("PersonA"))
        self.bruecke.handlers["speichern"]({**auftrag, "trotzdem": True})
        await self.letztes("gespeichert")
        self.assertIn("red dress", charakter.kern("PersonA"))

    async def test_ohne_laden_oder_fuer_andere_nichts(self):
        self.bruecke.handlers["speichern"]({"json": self.bearbeitet(kern=NEU), "basis": {}, "name": "PersonA"})
        self.assertIn("neu laden", (await self.letztes("fehler"))["text"])
        self.bruecke.handlers["laden"]({})
        stand = await self.letztes("stand")
        self.bruecke.handlers["speichern"]({"json": self.bearbeitet(kern=NEU), "basis": stand["basis"],
                                            "name": "PersonB"})
        await asyncio.sleep(0.05)
        self.assertEqual(2, sum(1 for e in self.bruecke.ereignisse if e["t"] == "fehler"))
        self.assertEqual(KERN, charakter.kern("PersonA"))


if kern_fenster is not None:
    class _Leitung(QObject):
        ereignis = Signal(object)
        getrennt = Signal()

        def __init__(self):
            super().__init__()
            self.auftraege = []

        def senden(self, a):
            self.auftraege.append(a)


@unittest.skipIf(kern_fenster is None, "PySide6 fehlt")
class Fenster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        self.leitung = _Leitung()
        self.f = kern_fenster.KernFenster(self.leitung)
        self.addCleanup(setattr, self.f, "verbunden", False)    # beim Aufräumen keine Rückfrage
        self.stand = {"kern": KERN, "sprechweise": "", "gesperrt": True, "vorher": []}
        self.leitung.ereignis.emit({"t": "stand", "name": "PersonA", "basis": self.stand,
                                    "json": json.dumps(self.stand, indent=2)})

    def test_laedt_beim_start_und_zeigt_den_ganzen_stand(self):
        self.assertEqual({"op": "laden"}, self.leitung.auftraege[0])
        self.assertEqual(self.stand, json.loads(self.f.text.toPlainText()))
        self.assertIn("PersonA", self.f.kopf.text())
        self.assertIn("gesperrt", self.f.sperre.text())
        self.assertFalse(self.f.ungespeichert())

    def test_speichern_schickt_text_und_basis(self):
        self.f.text.setPlainText(self.f.text.toPlainText().replace(KERN, NEU))
        self.assertTrue(self.f.ungespeichert())
        self.f.speichern(False)
        a = self.leitung.auftraege[-1]
        self.assertEqual(("speichern", "PersonA", self.stand), (a["op"], a["name"], a["basis"]))
        self.assertEqual(NEU, json.loads(a["json"])["kern"])

    def test_kaputtes_json_bleibt_im_fenster(self):
        self.f.text.setPlainText('{"kern": ')
        self.f.speichern(False)
        self.assertNotEqual("speichern", self.leitung.auftraege[-1]["op"])
        self.assertIn("JSON", self.f.status.text())

    def test_getrennt_sperrt_das_speichern(self):
        self.leitung.getrennt.emit()
        self.f.speichern(False)
        self.assertFalse(self.f.speichern_k.isEnabled())
        self.assertEqual(1, len(self.leitung.auftraege))


if __name__ == "__main__":
    unittest.main()
