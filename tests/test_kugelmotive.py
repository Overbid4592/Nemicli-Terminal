"""Kugel-Motive: 20 Bereiche, Motive frei formuliert finden, fehlende Motive mit der Figur
der Grundbilder malen, freier Moment der Kugel. Bild-Motor und rembg sind Attrappen."""

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image                 # noqa: E402

import kugelbilder as KB              # noqa: E402
import kugelmotive as KM              # noqa: E402
import snapshot                       # noqa: E402
from wache import steuerung           # noqa: E402


class MotivlisteTests(unittest.TestCase):
    def test_bereiche_und_schluessel(self):
        self.assertEqual(20, len(KM.BEREICHE))
        alle = [k for _, liste in KM.BEREICHE.values() for k in liste]
        self.assertEqual(len(alle), len(set(alle)))                  # Doppelte nur einmal
        self.assertGreater(len(alle), 390)
        for k in alle:
            self.assertTrue(k.isalnum() and k == k.lower(), k)
            self.assertTrue(KM.MOTIVE[k][1], k)                      # englische Beschreibung da
        self.assertEqual("haeltsnackschuessel", KM.schluessel("hält Snackschüssel"))

    def test_menue_motive_malen_startet_nie_grundbilder(self):
        import commands as C
        zusaetze = [z for z, _, _ in C.UNTERMENUES["/kugel"]]
        self.assertIn("motive {}", zusaetze)                       # leere Antwort bricht ab
        self.assertEqual(1, sum(z.startswith("malen") for z in zusaetze))

    def test_bereich_finden(self):
        self.assertEqual("gaming", KM.bereich_finden("6"))
        self.assertEqual("gaming", KM.bereich_finden("Gaming"))
        self.assertEqual("essen", KM.bereich_finden("essen"))
        self.assertEqual("gefuehle", KM.bereich_finden("1"))
        self.assertIsNone(KM.bereich_finden("21"))
        self.assertIsNone(KM.bereich_finden("quatsch"))

    def test_frei_formuliert_finden(self):
        da = ["popcorn", "pizza", "haeltcontroller", "facepalm", "gaehnt"]
        self.assertEqual("popcorn", KM.finden("ich ess Popcorn", da))
        self.assertEqual("haeltcontroller", KM.finden("hält den Controller", da))
        self.assertEqual("facepalm", KM.finden("Facepalm", da))
        self.assertEqual("gaehnt", KM.finden("gähnt", da))
        self.assertIsNone(KM.finden("Raumschiff fliegen", da))
        self.assertIsNone(KM.finden("popcorn", []))


class SteuerungMotivTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._alt = (steuerung.BILDER_ORDNER, steuerung.ZUSTAND)
        steuerung.BILDER_ORDNER = root / "Persoenlichkeiten"
        steuerung.ZUSTAND = root / "kugel_zustand.json"
        self.addCleanup(self._zurueck)
        m = steuerung.BILDER_ORDNER / "Nemi"
        m.mkdir(parents=True)
        Image.new("RGBA", (8, 8)).save(m / "neutral.png")
        for k in ("popcorn", "haeltcontroller"):
            Image.new("RGBA", (8, 8)).save(m / f"{k}.png")

    def _zurueck(self):
        steuerung.BILDER_ORDNER, steuerung.ZUSTAND = self._alt

    def test_motive_gehoeren_zu_den_bildern(self):
        self.assertEqual({"", "popcorn", "haeltcontroller"}, set(steuerung.bilder("Nemi")))
        self.assertEqual(steuerung.BILDER_ORDNER / "Nemi" / "neutral.png", steuerung.bilder("Nemi")[""])

    def test_alte_bilder_gelten_weiter(self):
        Image.new("RGBA", (8, 8)).save(steuerung.BILDER_ORDNER / "Nemi_ernst.png")
        self.assertIn("ernst", steuerung.bilder("Nemi"))

    def test_stimmung_frei_formuliert(self):
        r = steuerung.steuern({"stimmung": "isst Popcorn"}, wer="Nemi")
        self.assertIn("popcorn", r)
        self.assertEqual("popcorn", steuerung.lesen()["stimmung"])

    def test_hintergrund_zaehlt(self):
        self.assertFalse(steuerung.im_hintergrund())
        steuerung.hintergrund(True); steuerung.hintergrund(True)
        steuerung.hintergrund(False)
        self.assertTrue(steuerung.im_hintergrund())
        steuerung.hintergrund(False); steuerung.hintergrund(False)
        self.assertFalse(steuerung.im_hintergrund())


def _rembg_attrappe(bild, session=None):
    return bild.convert("RGBA")


class MotiveMalenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._alt = (KB.ORDNER, snapshot.ORDNER, KB._maler, KB._rembg_session)
        KB.ORDNER, snapshot.ORDNER = root / "Persoenlichkeiten", root / "Papierkorb"
        self.roh = root / "roh"; self.roh.mkdir()
        self.aufrufe: list[dict] = []

        def paint(prompt, *, neg=None, size=None, seed=None, steps=None, on_status=None, **k):
            self.aufrufe.append({"prompt": prompt, "seed": seed, "steps": steps})
            p = self.roh / f"bild_{len(self.aufrufe)}.png"
            Image.new("RGB", (64, 64), (200, 50, 50)).save(p)
            return str(p)
        KB._maler = lambda: paint
        fake = types.ModuleType("rembg"); fake.remove = _rembg_attrappe; fake.new_session = lambda name: name
        self._rembg_echt = sys.modules.get("rembg")
        sys.modules["rembg"] = fake
        KB._rembg_session = None
        self.addCleanup(self._zurueck)

    def _zurueck(self):
        KB.ORDNER, snapshot.ORDNER, KB._maler, KB._rembg_session = self._alt
        if self._rembg_echt is not None:
            sys.modules["rembg"] = self._rembg_echt
        else:
            sys.modules.pop("rembg", None)

    def test_ohne_figur_kein_motiv(self):
        with self.assertRaises(RuntimeError):
            KB.motive_malen("Nemi", ["popcorn"])

    def test_gleiche_figur_zehn_schritte_nur_fehlende(self):
        KB.malen("Nemi", "small round robot", seed=4711,
                 stimmungen=[("", "neutral")])
        self.assertEqual({"beschreibung": "small round robot", "seed": 4711, "kleidung": ""}, KB.figur("Nemi"))
        self.aufrufe.clear()
        bilder = KB.motive_malen("Nemi", ["popcorn", "haeltcontroller"])
        self.assertEqual(["popcorn.png", "haeltcontroller.png"], [b.name for b in bilder])
        self.assertTrue(all(a["seed"] == 4711 and a["steps"] == 10 for a in self.aufrufe))
        self.assertIn("eating popcorn", self.aufrufe[0]["prompt"])
        self.assertTrue(self.aufrufe[0]["prompt"].startswith("small round robot. "))
        self.aufrufe.clear()
        KB.motive_malen("Nemi", ["popcorn", "pizza"])               # popcorn gibt es schon
        self.assertEqual(1, len(self.aufrufe))
        self.assertIn("popcorn", KB.vorhandene("Nemi"))
        # neue Figur: alte Motive in den Papierkorb, Grundbilder neu
        KB.malen("Nemi", "a different look", seed=1, stimmungen=[("", "neutral")])
        self.assertEqual(["neutral.png"], [p.name for p in KB.motiv_ordner("Nemi").glob("*.png")])
        self.assertEqual(3, len(list(snapshot.ORDNER.glob("*.png"))) - 1)    # 3 Motive + altes Grundbild

    def test_abbruch_nach_laufendem_bild(self):
        KB.malen("Nemi", "robot", seed=1, stimmungen=[("", "neutral")])
        self.aufrufe.clear()
        KB.motive_malen("Nemi", ["popcorn", "pizza", "burger"],
                        abbruch=lambda: len(self.aufrufe) >= 1)
        self.assertEqual(1, len(self.aufrufe))


class FreierMomentTests(unittest.TestCase):
    def test_still_und_text(self):
        try:
            from wache import kugel
        except Exception as exc:                     # ohne PySide6
            self.skipTest(f"Qt fehlt: {exc}")
        self.assertTrue(kugel.still("(still)"))
        self.assertTrue(kugel.still("  Still. "))
        self.assertTrue(kugel.still(""))
        self.assertFalse(kugel.still("Hey, schon spät – Pause?"))
        t = kugel.impuls_text("Niemand")
        self.assertIn("[Freier Moment", t)
        self.assertIn("(still)", t)
        self.assertNotIn("{zeit}", t)


if __name__ == "__main__":
    unittest.main()
