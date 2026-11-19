"""Gesichter finden (/kern kopf): Haare bleiben, Varianten prüfen, Nahaufnahme, Übernehmen von Kern und Seed,
Brücke zur Sitzung und das Kopf-Fenster."""
import asyncio
import json
import os
import shutil
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
import charaktergalerie  # noqa: E402
import gesichtfinder as G  # noqa: E402
import paths  # noqa: E402
import profilordner  # noqa: E402
import qt_testhilfe  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import kopf_fenster
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # PySide6 fehlt
    kopf_fenster = None

KERN = "an adult woman with long red hair and green eyes, a slim build"
GESICHT_A = "an adult woman with long red hair and green eyes, a heart-shaped face, a small upturned nose, a slim build"
GESICHT_B = "an adult woman with long red hair and green eyes, a square jaw, full lips and freckles, a slim build"


def antwort(*kerne) -> str:
    return "<think>…</think>" + json.dumps([{"titel": f"Variante {i}", "kern": k} for i, k in enumerate(kerne)])


class Haare(unittest.TestCase):
    def test_haar_teile(self):
        self.assertEqual(["long red hair"], G.haar_teile(KERN))
        self.assertEqual(["shoulder-length wavy chestnut hair", "side-swept bangs"],
                         G.haar_teile("a man, shoulder-length wavy chestnut hair with side-swept bangs, grey eyes"))
        self.assertEqual([], G.haar_teile("a bald man with grey eyes"))

    def test_haare_gleich(self):
        self.assertTrue(G.haare_gleich(KERN, GESICHT_A))
        self.assertFalse(G.haare_gleich(KERN, KERN.replace("long red", "short black")))
        self.assertFalse(G.haare_gleich(KERN, KERN + ", with bangs"))                 # neue Haar-Angabe
        self.assertFalse(G.haare_gleich("a bald man", "a bald man with short hair"))


class Varianten(unittest.TestCase):
    def test_nur_brauchbare_varianten(self):
        v = G.lesen(antwort(GESICHT_A, KERN, KERN.replace("long red", "short black"),
                            GESICHT_B + ", wearing a red dress", GESICHT_A, GESICHT_B), KERN)
        self.assertEqual([GESICHT_A, GESICHT_B], [x["kern"] for x in v])
        self.assertTrue(all(isinstance(x["seed"], int) and x["seed"] >= 1 for x in v))
        self.assertNotEqual(v[0]["seed"], v[1]["seed"])

    def test_nichts_brauchbares_meldet_sich(self):
        with self.assertRaisesRegex(ValueError, "Haare"):
            G.lesen(antwort(KERN.replace("long red", "short black")), KERN)
        with self.assertRaises(ValueError):
            G.lesen("keine Liste", KERN)

    def test_auftrag_und_nahaufnahme(self):
        a = G.auftrag(KERN, "rundes Gesicht, Stupsnase", 6, ["Herzform"])
        self.assertIn(KERN, a)
        self.assertIn("Stupsnase", a)
        self.assertIn("Herzform", a)
        self.assertIn("überrasche", G.auftrag(KERN, ""))
        p = G.portraet_prompt("a girl with long red hair, a round face")
        self.assertIn("Close-up portrait of a single adult", p)
        self.assertNotIn("girl", p)
        self.assertIn("head and shoulders", p)


class _Daten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (mock.patch.object(paths, "DATEN", self.tmp),
                  mock.patch.object(profilordner, "aktiv", lambda: "PersonA"),
                  mock.patch.object(charaktergalerie, "ordner", return_value=self.tmp / "Galerie")):
            p.start()
            self.addCleanup(p.stop)
        charakter.schreiben({"name": "PersonA", "kern": KERN, "seed_referenz": 42, "stil": "photo"}, "PersonA")


class Uebernehmen(_Daten):
    def test_kern_und_seed_der_alte_kern_bleibt(self):
        erg = G.uebernehmen({"titel": "Herz", "kern": GESICHT_A, "seed": 1234}, "PersonA")
        self.assertEqual({"kern": GESICHT_A, "seed": 1234}, erg)
        self.assertEqual(1234, charakter.roh("PersonA")["seed_referenz"])
        self.assertEqual("photo", charakter.roh("PersonA")["stil"])               # der Rest bleibt
        self.assertEqual(KERN, charakter.kern_stand("PersonA")["vorher"][0])
        self.assertEqual(KERN, charakter.kern_zurueck("PersonA"))

    def test_seed_setzen_prueft(self):
        for falsch in (0, -5, True, "12", 2 ** 32):
            with self.subTest(falsch), self.assertRaises(ValueError):
                charakter.seed_setzen(falsch, "PersonA")
        with self.assertRaises(ValueError):
            G.uebernehmen({"kern": GESICHT_A, "seed": None}, "PersonA")
        self.assertEqual(42, charakter.roh("PersonA")["seed_referenz"])


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
        import hintergrund
        import imagegen

        import main
        self.prompts = []

        def malen(prompt, seed=None):
            self.prompts.append((prompt, seed))
            p = self.tmp / f"roh_{len(self.prompts)}.png"
            p.write_bytes(b"PNG")
            return str(p)
        for p in (mock.patch.object(gui_bruecke, "GuiBruecke", _Bruecke),
                  mock.patch.object(main.PS, "active", lambda: NS(name="PersonA")),
                  mock.patch.object(imagegen, "missing_reason_aktiv", return_value=""),
                  mock.patch.object(imagegen, "paint", side_effect=malen),
                  mock.patch.object(hintergrund, "gpu_belegt", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

        async def ask_once(prompt, system):
            self.frage = (prompt, system)
            return antwort(GESICHT_A, GESICHT_B)
        self.bruecke = await main.start_kopf(NS(backend=NS(ask_once=ask_once)))

    async def warten(self, art, anzahl=1):
        for _ in range(300):
            if sum(1 for e in self.bruecke.ereignisse if e["t"] == art) >= anzahl:
                return [e for e in self.bruecke.ereignisse if e["t"] == art]
            await asyncio.sleep(0.01)
        self.fail(f"kein {art}: {self.bruecke.ereignisse}")

    async def test_erschaffen_malen_uebernehmen(self):
        self.bruecke.handlers["start"]({})
        start = (await self.warten("start"))[0]
        self.assertEqual((KERN, "PersonA"), (start["kern"], start["name"]))
        self.bruecke.handlers["erschaffen"]({"wunsch": "Stupsnase"})
        bilder = await self.warten("bild", 2)
        self.assertIn("Stupsnase", self.frage[0])
        varianten = (await self.warten("varianten"))[0]["liste"]
        self.assertEqual([v["seed"] for v in varianten], [s for _, s in self.prompts])     # je Gesicht sein Seed
        self.assertTrue(all("Close-up" in p and "photo" in p for p, _ in self.prompts))
        self.assertTrue(all(Path(b["pfad"]).is_file() for b in bilder))
        self.bruecke.handlers["uebernehmen"]({"nr": bilder[0]["nr"], "i": 1})
        ueber = (await self.warten("uebernommen"))[0]
        self.assertEqual(GESICHT_B, charakter.kern("PersonA"))
        self.assertEqual(varianten[1]["seed"], charakter.roh("PersonA")["seed_referenz"])
        self.assertEqual(varianten[1]["seed"], ueber["seed"])

    async def test_alte_runde_wird_nicht_uebernommen(self):
        self.bruecke.handlers["erschaffen"]({"wunsch": ""})
        bilder = await self.warten("bild", 2)
        self.bruecke.handlers["uebernehmen"]({"nr": bilder[0]["nr"] - 1, "i": 0})
        self.bruecke.handlers["uebernehmen"]({"nr": bilder[0]["nr"], "i": 7})
        await self.warten("fehler", 2)
        self.assertEqual(KERN, charakter.kern("PersonA"))
        self.assertEqual(42, charakter.roh("PersonA")["seed_referenz"])

    async def test_ohne_motor_kein_auftrag(self):
        import imagegen
        with mock.patch.object(imagegen, "missing_reason_aktiv", return_value="Krea 2 fehlt"):
            self.bruecke.handlers["erschaffen"]({"wunsch": ""})
            fehler = (await self.warten("fehler"))[0]
        self.assertIn("Krea 2 fehlt", fehler["text"])
        self.assertFalse(hasattr(self, "frage"))

    async def test_korrektur_vorher_und_nachher(self):
        import imagegen
        import krea
        aufrufe = []

        def nachbessern(pfad, immer=False):
            aufrufe.append(immer)
            ziel = Path(pfad).parent / G.NACHGEBESSERT / Path(pfad).name
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_bytes(b"PNG")
            return str(ziel)
        with mock.patch.object(imagegen, "gesicht_nachbessern", side_effect=nachbessern),                 mock.patch.object(krea, "gesicht_an", return_value=False):
            self.bruecke.handlers["start"]({})
            self.assertFalse((await self.warten("start"))[0]["korrektur"])
            self.bruecke.handlers["erschaffen"]({"wunsch": "", "korrektur": True})
            bilder = await self.warten("bild", 2)
        self.assertEqual([True, True], aufrufe)                         # auch wenn sie für alle Bilder aus ist
        self.assertTrue(all(G.NACHGEBESSERT in b["besser"] and b["besser"] != b["pfad"] for b in bilder))
        G.aufraeumen("PersonA")
        self.assertFalse(any(Path(b["besser"]).exists() or Path(b["pfad"]).exists() for b in bilder))

    async def test_ohne_korrektur_nur_vorher(self):
        import imagegen
        with mock.patch.object(imagegen, "gesicht_nachbessern") as nb:
            self.bruecke.handlers["erschaffen"]({"wunsch": ""})
            bilder = await self.warten("bild", 2)
        nb.assert_not_called()
        self.assertEqual(["", ""], [b["besser"] for b in bilder])


if kopf_fenster is not None:
    class _Leitung(QObject):
        ereignis = Signal(object)
        getrennt = Signal()

        def __init__(self):
            super().__init__()
            self.auftraege = []

        def senden(self, a):
            self.auftraege.append(a)


@unittest.skipIf(kopf_fenster is None, "PySide6 fehlt")
class Fenster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        self.leitung = _Leitung()
        self.f = kopf_fenster.KopfFenster(self.leitung)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.bild = Path(tmp) / "gesicht_1_0.png"
        from PySide6.QtGui import QImage
        QImage(8, 8, QImage.Format.Format_RGB32).save(str(self.bild))
        self.leitung.ereignis.emit({"t": "start", "name": "PersonA", "kern": KERN, "malen": "", "lora": ""})

    def test_start_und_erschaffen(self):
        self.assertEqual({"op": "start"}, self.leitung.auftraege[0])
        self.assertIn("PersonA", self.f.kopf.text())
        self.f.wunsch.setPlainText("Stupsnase")
        self.f.erschaffen()
        self.assertEqual({"op": "erschaffen", "wunsch": "Stupsnase", "korrektur": False}, self.leitung.auftraege[-1])
        self.f.erschaffen()                                                # läuft schon: kein zweiter Auftrag
        self.assertEqual(2, len(self.leitung.auftraege))

    def test_karten_bilder_und_wahl(self):
        self.leitung.ereignis.emit({"t": "varianten", "nr": 3, "liste": [{"titel": "Herz", "kern": GESICHT_A,
                                                                          "seed": 7}]})
        self.assertFalse(self.f.karten[0].isHidden())
        self.assertTrue(self.f.karten[1].isHidden())
        self.leitung.ereignis.emit({"t": "bild", "nr": 2, "i": 0, "pfad": str(self.bild)})    # alte Runde
        self.assertEqual("", self.f.karten[0].pfad)
        self.leitung.ereignis.emit({"t": "bild", "nr": 3, "i": 0, "pfad": str(self.bild)})
        self.assertEqual(str(self.bild), self.f.karten[0].pfad)
        with mock.patch.object(kopf_fenster, "ja_nein", return_value=True):
            self.f.waehlen(0)
        self.assertEqual({"op": "uebernehmen", "nr": 3, "i": 0}, self.leitung.auftraege[-1])
        with mock.patch.object(kopf_fenster, "ja_nein", return_value=False):
            self.f.waehlen(0)
        self.assertEqual(2, len(self.leitung.auftraege))

    def test_korrektur_schalter_und_vorher_nachher(self):
        self.leitung.ereignis.emit({"t": "start", "name": "PersonA", "kern": KERN, "korrektur": True})
        self.assertTrue(self.f.korrektur.isChecked())
        self.f.erschaffen()
        self.assertTrue(self.leitung.auftraege[-1]["korrektur"])
        besser = self.bild.with_name("besser.png")
        besser.write_bytes(self.bild.read_bytes())
        self.leitung.ereignis.emit({"t": "varianten", "nr": 1, "liste": [{"titel": "Herz", "kern": GESICHT_A}]})
        self.leitung.ereignis.emit({"t": "bild", "nr": 1, "i": 0, "pfad": str(self.bild), "besser": str(besser)})
        k = self.f.karten[0]
        self.assertFalse(k.umschalten_k.isHidden())
        self.assertTrue(k.nachher)
        self.assertIn("vorher zeigen", k.umschalten_k.text())
        k.umschalten()
        self.assertFalse(k.nachher)
        self.assertIn("nachher zeigen", k.umschalten_k.text())
        self.leitung.ereignis.emit({"t": "bild", "nr": 1, "i": 0, "pfad": str(self.bild), "besser": "C:/x.bat"})
        self.assertTrue(k.umschalten_k.isHidden())                       # nur echte PNG-Bilder

    def test_ohne_kern_kein_erschaffen(self):
        self.leitung.ereignis.emit({"t": "start", "name": "PersonA", "kern": ""})
        self.assertFalse(self.f.erschaffen_k.isEnabled())


if __name__ == "__main__":
    unittest.main()
