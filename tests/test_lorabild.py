"""Bild-LoRAs (lorabild): Prompt mit [name], SHA-256, Bindung an einen Charakter (A = LoRA, B = Charakter-Datei),
Bild-Check, Kopien fürs Training, Kugel, Weiterleitung im Trainer und die LoRA-Werkstatt (Fenster)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import charakter          # noqa: E402
import lorabild as LB     # noqa: E402
import paths              # noqa: E402
import profilordner       # noqa: E402

KERN = "an adult woman with long red hair and green eyes"


class _Daten(unittest.TestCase):
    """Eigener Daten-Ordner je Test, aktive Persönlichkeit „PersonA“."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.daten = Path(tmp.name)
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(profilordner, "aktiv", lambda: "PersonA")):
            p.start()
            self.addCleanup(p.stop)
        LB._SHA_CACHE.clear()

    def lora_anlegen(self, name="figur_a", inhalt=b"lora-gewichte"):
        f = LB.datei(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(inhalt)
        return f

    def charakter_anlegen(self, name="PersonA"):
        charakter.schreiben({"name": name, "kern": KERN, "seed_referenz": 42, "stil": "photo, soft light",
                             "outfits": {"sommer": "a light summer dress"}, "posen": {}, "ausdruecke": {},
                             "regeln": []}, name)


class Prompt(unittest.TestCase):
    def test_name_sauber(self):
        self.assertEqual("figur_sommer", LB.name_sauber("Figur Sommer"))
        self.assertEqual("joerg", LB.name_sauber("Jörg!"))
        self.assertEqual("", LB.name_sauber("  ** "))

    def test_prompt_lesen(self):
        self.assertEqual(("figur_a", "sitzt im Café"), LB.prompt_lesen("[figur_a] sitzt im Café"))
        self.assertEqual(("figur_a", "at the beach"), LB.prompt_lesen("  [ Figur_A ]: at the beach"))
        self.assertEqual((None, "eine Katze"), LB.prompt_lesen("eine Katze"))
        self.assertEqual((None, "Katze [figur_a]"), LB.prompt_lesen("Katze [figur_a]"))     # nur am Anfang

    def test_prompt_ohne_aussehen(self):
        p = LB.prompt_bauen("figur_a", "reading in a cafe", outfit="a red coat", stil="photo")
        self.assertTrue(p.startswith("A photo of figur_a, an adult."))
        self.assertIn("Wearing a red coat.", p)
        self.assertIn("Reading in a cafe.", p)
        self.assertNotIn("hair", p)


class Pruefsumme(_Daten):
    def test_sha_gemerkt_und_geschrieben(self):
        f = self.lora_anlegen()
        wert = LB.sha_schreiben(f)
        self.assertEqual(64, len(wert))
        self.assertEqual(wert, LB.gespeicherte_sha(f))
        self.assertEqual(wert, LB.sha256(f))
        self.assertEqual([("figur_a", wert)], [(e["name"], e["sha256"]) for e in LB.liste()])


class Bindung(_Daten):
    def test_binden_schreibt_sha_mit_sicherung(self):
        self.lora_anlegen()
        self.charakter_anlegen()
        b = LB.binden("figur_a", "PersonA")
        roh = charakter.roh("PersonA")
        self.assertEqual(b, roh["lora_bild"])
        self.assertEqual(KERN, roh["kern"])                                   # Aussehen bleibt als Sicherung B
        self.assertIn("lora_bild", charakter.sicherung("PersonA").read_text(encoding="utf-8"))
        bindung, pfad, grund = LB.fuer_charakter()
        self.assertEqual((pfad, grund), (LB.datei("figur_a"), ""))

    def test_veraendert_oder_weg_gilt_nicht(self):
        f = self.lora_anlegen()
        self.charakter_anlegen()
        LB.binden("figur_a", "PersonA")
        f.write_bytes(b"andere-gewichte")
        _b, pfad, grund = LB.fuer_charakter()
        self.assertIsNone(pfad)
        self.assertIn("SHA-256 stimmt nicht", grund)
        f.unlink()
        self.assertIn("fehlt", LB.fuer_charakter()[2])

    def test_ki_kann_die_bindung_nicht_aendern(self):
        self.lora_anlegen()
        self.charakter_anlegen()
        echt = LB.binden("figur_a", "PersonA")
        falsch = {"name": "x", "datei": "C:/x.safetensors", "sha256": "0" * 64}
        text, ok = charakter.aendern({"kern": KERN + ", freckles", "lora_bild": falsch})
        self.assertTrue(ok, text)
        self.assertEqual(echt, charakter.roh("PersonA")["lora_bild"])
        self.assertTrue(LB.loesen("PersonA"))
        self.assertNotIn("lora_bild", charakter.roh("PersonA"))


class Malen(_Daten):
    """actions._figur: A = gebundene LoRA, B = Aussehen aus der Charakter-Datei."""

    def setUp(self):
        super().setUp()
        import actions
        import imagegen
        self.A = actions
        p = mock.patch.object(imagegen, "lora_moeglich", lambda vorlage=None: self.krea)
        p.start()
        self.addCleanup(p.stop)
        self.krea = True
        self.lora_anlegen()
        self.charakter_anlegen()

    def test_mit_lora_kern_vorne_dann_lora(self):
        LB.binden("figur_a", "PersonA")
        prompt, seed, lora, hinweis = self.A._figur({"figur": True, "outfit": "sommer"}, "reading in a cafe")
        self.assertTrue(prompt.startswith("An adult woman with long red hair and green eyes."))
        self.assertLess(prompt.index("red hair"), prompt.index("A photo of figur_a, an adult."))
        self.assertIn("Wearing a light summer dress.", prompt)
        self.assertEqual((seed, lora), (42, str(LB.datei("figur_a"))))
        self.assertIn("Bild-LoRA „figur_a“", hinweis)

    def test_ohne_bindung_wie_bisher(self):
        prompt, seed, lora, hinweis = self.A._figur({"figur": True}, "reading")
        self.assertIn("red hair", prompt)
        self.assertEqual((seed, lora, hinweis), (42, None, ""))

    def test_veraenderte_lora_faellt_auf_die_datei_zurueck(self):
        LB.binden("figur_a", "PersonA")
        LB.datei("figur_a").write_bytes(b"manipuliert")
        prompt, _s, lora, hinweis = self.A._figur({"figur": True}, "reading")
        self.assertIn("red hair", prompt)
        self.assertIsNone(lora)
        self.assertIn("SHA-256 stimmt nicht", hinweis)

    def test_anderer_bildmotor_nimmt_die_datei(self):
        LB.binden("figur_a", "PersonA")
        self.krea = False
        prompt, _s, lora, hinweis = self.A._figur({"figur": True}, "reading")
        self.assertIn("red hair", prompt)
        self.assertIsNone(lora)
        self.assertIn("nur mit Krea 2", hinweis)

    def test_name_im_prompt(self):
        prompt, seed, lora, _h = self.A._figur({}, "[figur_a] walking on a beach")
        self.assertEqual("A photo of figur_a, an adult. Walking on a beach.", prompt)
        self.assertEqual((seed, lora), (None, str(LB.datei("figur_a"))))
        fehler = self.A._figur({}, "[gibtsnicht] x")
        self.assertFalse(fehler.ok)
        self.assertIn("[figur_a]", fehler.text)
        self.krea = False
        self.assertIn("nur Krea 2", self.A._figur({}, "[figur_a] x").text)

    def test_andere_motive_ohne_lora(self):
        LB.binden("figur_a", "PersonA")
        self.assertEqual(("a cat in the snow", None, None, ""), self.A._figur({}, "a cat in the snow"))


class Kugel(_Daten):
    def test_kugel_malt_mit_gebundener_lora(self):
        import imagegen
        import kugelbilder
        self.lora_anlegen()
        self.charakter_anlegen()
        with mock.patch.object(imagegen, "lora_moeglich", lambda vorlage=None: True):
            self.assertEqual((None, {}), kugelbilder._lora("PersonA"))
            LB.binden("figur_a", "PersonA")
            satz, extra = kugelbilder._lora("PersonA")
        self.assertEqual("A photo of figur_a, an adult.", satz)
        self.assertEqual({"lora": str(LB.datei("figur_a"))}, extra)


class BilderCheck(_Daten):
    def _bilder(self):
        from PIL import Image, ImageFilter
        import random
        random.seed(5)
        ordner = self.daten / "eingang"
        ordner.mkdir()
        scharf = Image.new("RGB", (256, 256))
        scharf.putdata([(random.randrange(256),) * 3 for _ in range(256 * 256)])
        pfade = []
        for i in range(4):
            p = ordner / f"scharf{i}.png"
            Image.effect_noise((256, 256), 60 + i).convert("RGB").save(p)
            pfade.append(p)
        weich = ordner / "weich.png"
        scharf.filter(ImageFilter.GaussianBlur(8)).save(weich)
        doppelt = ordner / "doppelt.png"
        doppelt.write_bytes(pfade[0].read_bytes())
        return pfade + [weich, doppelt]

    def test_doppelt_unscharf_und_gesichter(self):
        import numpy as np
        from PIL import Image
        bilder = self._bilder()
        zwei = str(bilder[1])
        with Image.open(zwei) as im:
            zweites = np.asarray(im.convert("RGB"))

        def finder(arr):                                  # Bild 2 zeigt zwei Gesichter, alle anderen eins
            gleich = arr.shape == zweites.shape and bool((arr == zweites).all())
            return [(0, 0, 5, 5)] * (2 if gleich else 1)
        erg = LB.pruefung(bilder, gesichter=finder)
        nach = {Path(e["pfad"]).name: e for e in erg}
        self.assertEqual("🛑", nach["doppelt.png"]["zeichen"])
        self.assertFalse(nach["doppelt.png"]["ok"])
        self.assertEqual("⚠", nach["weich.png"]["zeichen"])
        self.assertEqual("🛑", nach[Path(zwei).name]["zeichen"])            # zwei Gesichter
        self.assertEqual("✔️", nach["scharf0.png"]["zeichen"])

    def test_sammeln_und_kopien(self):
        bilder = self._bilder()
        self.assertEqual(len(bilder), len(LB.bilder_sammeln([bilder[0].parent, bilder[0]])))
        kopien = LB.daten_kopieren("figur_a", bilder[:3])
        self.assertEqual(3, len(kopien))
        self.assertTrue(all(k.parent == LB.ordner("figur_a") / "daten" for k in kopien))
        self.assertTrue(bilder[0].is_file())                                  # Originale bleiben
        weniger = LB.daten_kopieren("figur_a", bilder[1:3])
        self.assertEqual(2, len(list((LB.ordner("figur_a") / "daten").iterdir())))
        self.assertEqual(2, len(weniger))


class Trainer(unittest.TestCase):
    def test_zusatz_nur_fuer_krea(self):
        import lora_training as LT
        gerufen = {}

        def krea(*a, **kw):
            gerufen["krea"] = kw
            return Path("k")

        def video(*a, **kw):
            gerufen["video"] = (len(a), kw)
            return Path("v")
        with mock.patch.object(LT, "_trainieren_krea", krea), mock.patch.object(LT, "_trainieren", video):
            LT.trainieren(motor="bild", fortschritt=print, vorschau={"alle": 200}, ausloeser="figur_a")
            LT.trainieren(motor="video", fortschritt=print)
        self.assertEqual({"alle": 200}, gerufen["krea"]["vorschau"])
        self.assertEqual("figur_a", gerufen["krea"]["ausloeser"])
        self.assertEqual((10, {}), gerufen["video"])


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QApplication
    import lora_fenster
except ImportError:
    lora_fenster = None


@unittest.skipIf(lora_fenster is None, "PySide6 fehlt")
class Werkstatt(_Daten):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def _fenster(self):
        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, auftrag):
                self.gesendet.append(auftrag)
        lt = Leitung()
        return lora_fenster.LoraFenster(lt), lt

    def test_ablauf(self):
        from PIL import Image
        f, lt = self._fenster()
        self.assertEqual({"op": "stand"}, lt.gesendet[0])
        pfade = []
        for i in range(5):
            p = self.daten / f"b{i}.png"
            Image.new("RGB", (64, 64), (i * 40, 80, 120)).save(p)
            pfade.append(str(p))
        f.dazu(pfade + [pfade[0]])
        self.assertEqual(5, f.liste.count())                                   # kein Bild doppelt
        f._ereignis({"t": "stand", "personas": ["PersonA", "PersonB"], "loras": [], "krea": ""})
        self.assertEqual(3, f.binden.count())
        f._ereignis({"t": "pruefung", "gesichter": True, "bilder": [
            {"pfad": pfade[4], "ok": False, "zeichen": "🛑", "hinweis": "2 Gesichter"}]})
        self.assertEqual(Qt.CheckState.Unchecked, f.liste.item(4).checkState())
        self.assertEqual(4, len(f.gewaehlt()))
        f.name.setText("Figur_A")
        f.binden.setCurrentText("PersonB")
        f.starten()
        auftrag = lt.gesendet[-1]
        self.assertEqual(("starten", "figur_a", 4, "PersonB"),
                         (auftrag["op"], auftrag["name"], len(auftrag["pfade"]), auftrag["binden"]))
        f._ereignis({"t": "fortschritt", "schritt": 580, "schritte": 1000, "prozent": 58.0, "verlust": 0.1,
                     "rest_s": 2160})
        self.assertEqual((580, "58 %"), (f.balken.value(), f.balken.format()))
        self.assertIn("noch etwa 36 min", f.schritt_text.text())
        self.assertTrue(f.pause_k.isEnabled())
        f._ereignis({"t": "fertig", "name": "figur_a", "sha256": "a" * 64, "proben": [], "gebunden": "PersonB"})
        self.assertIn("gebunden an PersonB", f.status.text())
        self.assertFalse(f.pause_k.isEnabled())

    def test_zu_wenige_bilder_startet_nicht(self):
        f, lt = self._fenster()
        f.name.setText("figur_a")
        f.starten()
        self.assertEqual(["stand"], [a["op"] for a in lt.gesendet])
        self.assertIn("Mindestens 4", f.status.text())


if __name__ == "__main__":
    unittest.main()
