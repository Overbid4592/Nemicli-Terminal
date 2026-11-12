"""/kugel malen – die Persönlichkeit malt sich vier Stimmungsbilder mit festem Seed,
die werden freigestellt und in Persoenlichkeiten/ abgelegt.

Bild-Motor und rembg sind Attrappen: der Motor liefert ein grünes Bild mit Figur,
„rembg“ macht daraus per Farbschlüssel Transparenz. So läuft der Test ohne ComfyUI
und ohne 176-MB-Modell."""

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

from PIL import Image, ImageDraw     # noqa: E402

import kugelbilder as KB             # noqa: E402
import snapshot                      # noqa: E402


def _rembg_attrappe(bild, session=None):
    """Grün → durchsichtig, alles andere bleibt."""
    bild = bild.convert("RGBA")
    px = bild.load()
    for y in range(bild.height):
        for x in range(bild.width):
            r, g, b, a = px[x, y]
            if g > 180 and r < 100 and b < 100:
                px[x, y] = (0, 0, 0, 0)
    return bild


class KugelbilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._alt = (KB.ORDNER, snapshot.ORDNER, KB._maler, KB._rembg_session)
        KB.ORDNER, snapshot.ORDNER = root / "Persoenlichkeiten", root / "Papierkorb"
        self.roh = root / "roh"; self.roh.mkdir()
        self.aufrufe: list[dict] = []

        def paint(prompt, *, neg=None, size=None, seed=None, on_status=None, **k):
            self.aufrufe.append({"prompt": prompt, "seed": seed, "size": size, "neg": neg})
            if on_status:
                on_status("Schritt 3/8")
            img = Image.new("RGB", (256, 256), (40, 220, 60)); d = ImageDraw.Draw(img)
            d.ellipse((78, 40, 178, 140), fill=(250, 200, 190)); d.rectangle((98, 130, 158, 230), fill=(60, 60, 120))
            p = self.roh / f"bild_{len(self.aufrufe)}.png"; img.save(p)
            return str(p)
        KB._maler = lambda: paint
        # rembg-Attrappe als Modul
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

    def test_vier_bilder_gleicher_seed_freigestellt(self):
        meldungen = []
        bilder = KB.malen("Nemi", "small round robot, matte white shell, cyan eyes", seed=4711,
                          on_status=meldungen.append)
        self.assertEqual(["neutral.png", "froh.png", "ernst.png", "denkt.png"], [p.name for p in bilder])
        self.assertTrue(all(p.parent == KB.ORDNER / "Nemi" for p in bilder))
        self.assertEqual(4, len(self.aufrufe))
        self.assertTrue(all(a["seed"] == 4711 and a["size"] == (1024, 1024) for a in self.aufrufe))
        self.assertIn("warm, happy smile", self.aufrufe[1]["prompt"]); self.assertIn("serious", self.aufrufe[2]["prompt"])
        self.assertTrue(all(a["prompt"].endswith(KB.RAHMEN_SATZ) for a in self.aufrufe))
        self.assertTrue(all(a["prompt"].startswith("small round robot, matte white shell, cyan eyes. ") for a in self.aufrufe))
        self.assertTrue(all(a["neg"] is None for a in self.aufrufe))
        for p in bilder:
            im = Image.open(p)
            self.assertEqual(("RGBA", (512, 512)), (im.mode, im.size))
            a = im.getchannel("A")
            self.assertEqual(0, a.getpixel((1, 1)))                  # Ecke: Hintergrund weg
            self.assertEqual(255, a.getpixel((256, 256)))            # Mitte: Figur da
        # Fortschritt: Bild i/4 mit Unterschritt des Motors und Freistellen
        self.assertTrue(any(m.startswith("Bild 1/4 · neutral · male") for m in meldungen))
        self.assertTrue(any("Bild 2/4 · froh · Schritt 3/8" in m for m in meldungen))
        self.assertTrue(any("stelle frei" in m for m in meldungen))
        self.assertIn("Seed 4711", meldungen[-1])
        self.assertEqual({"", "froh", "ernst", "denkt"}, set(KB.vorhandene("Nemi")))
        # nochmal malen: alte Bilder landen im Papierkorb, nicht im Nirwana
        KB.malen("Nemi", "same girl", seed=1)
        self.assertEqual(4, len(list(snapshot.ORDNER.glob("*.png"))))
        # entfernen → Papierkorb, Kugel wieder Kugel
        self.assertEqual(4, KB.entfernen("Nemi"))
        self.assertEqual({}, KB.vorhandene("Nemi"))
        self.assertEqual(8, len(list(snapshot.ORDNER.glob("*.png"))))

    def test_fehler_beim_freistellen_behaelt_altes_bild(self):
        KB.malen("Nemi", "small round robot", seed=3, stimmungen=[("", "neutral")])
        ziel = KB.dateiname("Nemi", "")
        vorher = ziel.read_bytes()

        def kaputt(bild, session=None):
            raise RuntimeError("Freistellen fehlgeschlagen")
        sys.modules["rembg"].remove = kaputt
        with self.assertRaises(RuntimeError):
            KB.malen("Nemi", "small round robot", seed=4, stimmungen=[("", "neutral")])
        self.assertEqual(vorher, ziel.read_bytes())
        self.assertEqual([], list(snapshot.ORDNER.glob("*.png")))

    def test_ohne_beschreibung_und_zufallsseed(self):
        with self.assertRaises(RuntimeError):
            KB.malen("Nemi", "   ")
        KB.malen("Juna", "a small robot", stimmungen=[("", "neutral"), ("froh", "happy")])
        self.assertEqual(2, len(self.aufrufe))
        self.assertEqual(self.aufrufe[0]["seed"], self.aufrufe[1]["seed"])
        self.assertIsInstance(self.aufrufe[0]["seed"], int)
        self.assertEqual(["froh.png", "neutral.png"], sorted(p.name for p in (KB.ORDNER / "Juna").glob("*.png")))

    def test_saetze_ohne_negativ(self):
        KB.malen("Nemi", "A small round robot with a matte white shell and cyan eyes.", seed=7)
        self.assertTrue(all(a["neg"] is None for a in self.aufrufe))
        self.assertIn("warm, happy smile", self.aufrufe[1]["prompt"])
        for a in self.aufrufe:
            self.assertTrue(a["prompt"].startswith("A small round robot with a matte white shell and cyan eyes. "))
            self.assertTrue(a["prompt"].endswith(KB.RAHMEN_SATZ))

    def _steuerung(self):
        from unittest.mock import patch
        from wache import steuerung
        for name, wert in (("BILDER_ORDNER", KB.ORDNER), ("ZUSTAND", Path(self.tmp.name) / "zustand.json")):
            p = patch.object(steuerung, name, wert); p.start(); self.addCleanup(p.stop)
        return steuerung

    def test_outfit_gleicher_ausdruck_andere_kleidung(self):
        S = self._steuerung()
        KB.malen("Nemi", "small round robot", seed=11)
        self.aufrufe.clear()
        motiv = KB.motiv_schluessel("glücklich")
        self.assertEqual("gluecklich", motiv)
        neu = KB.outfit_malen("Nemi", "winter", "a red wool scarf", ["froh", motiv])
        self.assertEqual(["froh.png", "gluecklich.png"], [p.name for p in neu])
        self.assertTrue(all(p.parent == KB.ORDNER / "Nemi" / "Outfits" / "winter" for p in neu))
        self.assertTrue(all(a["seed"] == 11 for a in self.aufrufe))
        froh, motiv_prompt = self.aufrufe[0]["prompt"], self.aufrufe[1]["prompt"]
        self.assertTrue(froh.startswith("small round robot. The character is wearing a red wool scarf instead"))
        self.assertIn(KB.SAETZE["froh"], froh); self.assertTrue(froh.endswith(KB.RAHMEN_SATZ))
        self.assertIn("happy, joyful smile", motiv_prompt); self.assertTrue(motiv_prompt.endswith(KB.RAHMEN_MOTIV_SATZ))
        # angezogen: froh aus dem Outfit, ernst bleibt ernst in Grundkleidung
        KB.outfit_anziehen("Nemi", "winter")
        da = S.bilder("Nemi")
        self.assertEqual(KB.ORDNER / "Nemi" / "Outfits" / "winter" / "froh.png", da["froh"])
        self.assertEqual(KB.ORDNER / "Nemi" / "ernst.png", da["ernst"])
        self.assertEqual(KB.ORDNER / "Nemi" / "froh.png", S.bilder("Nemi", outfit=False)["froh"])
        self.assertEqual(("winter", {"froh", "gluecklich"}), S.outfit_stand("Nemi"))
        # schon gemalt: nichts neu; neue Kleidung: alte Outfit-Bilder in den Papierkorb, neu malen
        self.assertEqual([], KB.outfit_malen("Nemi", "winter", "a red wool scarf", ["froh"]))
        self.assertEqual(2, len(self.aufrufe))
        KB.outfit_malen("Nemi", "winter", "a thick blue coat", ["froh"])
        self.assertIn("a thick blue coat", self.aufrufe[-1]["prompt"])
        self.assertEqual(["froh.png"], [p.name for p in (KB.ORDNER / "Nemi" / "Outfits" / "winter").glob("*.png")])
        # Kugel entfernen: Outfit bleibt gespeichert, angezogen ist keins mehr
        KB.entfernen("Nemi")
        self.assertEqual("", KB.outfits("Nemi")["aktiv"])
        self.assertTrue((KB.ORDNER / "Nemi" / "Outfits" / "winter" / "froh.png").exists())
        # neue Figur: Outfit-Bilder passen nicht mehr
        KB.malen("Nemi", "a different robot", seed=12)
        self.assertEqual([], list((KB.ORDNER / "Nemi" / "Outfits").rglob("*.png")))

    def test_outfit_ueber_kugel_und_werkzeug(self):
        from unittest.mock import patch
        import actions
        import imagegen
        S = self._steuerung()
        KB.malen("Nemi", "small round robot", seed=11)
        with patch.object(actions, "_wer", lambda: "Nemi"), \
                patch.object(imagegen, "missing_reason_aktiv", lambda: ""):
            self.assertFalse(actions._kugel_anziehen({"outfit": "winter"}).ok)          # ohne Kleidung
            r = actions._kugel_anziehen({"outfit": "Winter", "beschreibung": "a red wool scarf",
                                         "motive": ["froh", "quatschmotiv"]})
            self.assertTrue(r.ok, r.text)
            self.assertIn("Outfit winter an", r.text); self.assertIn("quatschmotiv", r.text)
            self.assertIn("winter (an)", actions._kugel_anziehen({}).text)
        self.assertEqual("winter", KB.outfits("Nemi")["aktiv"])
        self.assertTrue(S.lesen().get("outfit_zeit"))
        self.assertIn("noch nicht gemalt", S.steuern({"stimmung": "ernst"}, wer="Nemi"))
        self.assertIn("Grundkleidung", S.steuern({"outfit": "standard"}, wer="Nemi"))
        self.assertEqual("", KB.outfits("Nemi")["aktiv"])
        self.assertIn("gibt es noch nicht", S.steuern({"outfit": "sommer"}, wer="Nemi"))
        self.assertIn("Outfit winter", S.steuern({"outfit": "winter"}, wer="Nemi"))
        self.assertEqual("winter", KB.outfits("Nemi")["aktiv"])

    def test_freie_kleidung_fuer_grundbilder_und_motive(self):
        KB.malen("Nemi", "small round robot", seed=5, kleidung="a yellow raincoat")
        self.assertTrue(all("small round robot. The character is wearing a yellow raincoat. " in a["prompt"]
                            for a in self.aufrufe))
        self.assertEqual("a yellow raincoat", KB.figur("Nemi")["kleidung"])
        KB.motive_malen("Nemi", ["gluecklich"])
        self.assertIn("wearing a yellow raincoat. The character is happy", self.aufrufe[-1]["prompt"])
        # andere Kleidung = neue Figur: alte Motive in den Papierkorb
        KB.malen("Nemi", "small round robot", seed=5, kleidung="")
        self.assertFalse((KB.ORDNER / "Nemi" / "gluecklich.png").exists())
        self.assertNotIn("wearing", self.aufrufe[-1]["prompt"])
        # Antwort der Persönlichkeit: eine Zeile, „-“ = nichts dazu
        self.assertEqual("", KB.kleidung_aus(" - "))
        self.assertEqual("", KB.kleidung_aus(""))
        self.assertEqual("a long green coat", KB.kleidung_aus('\n"a long green coat"\nweiterer Text'))
        system = KB.kleidung_system("Juna", "a small robot.", "Synthetisches Beispiel.")
        self.assertIn("entscheidest du allein", system)
        self.assertIn("a small robot.", system); self.assertIn("Synthetisches Beispiel.", system)
        self.assertNotIn("z. B.", KB.KLEIDUNG_SYSTEM); self.assertNotIn("e.g.", KB.KLEIDUNG_SYSTEM)

    def test_kleidung_und_outfits_je_persoenlichkeit(self):
        S = self._steuerung()
        KB.malen("PersonA", "a tall figure", seed=1, kleidung="a black cloak")
        KB.malen("PersonB", "a small figure", seed=2)
        KB.outfit_malen("PersonA", "winter", "a wool scarf", ["froh"])
        KB.outfit_anziehen("PersonA", "winter")
        self.assertEqual("a black cloak", KB.figur("PersonA")["kleidung"])
        self.assertEqual("", KB.figur("PersonB")["kleidung"])
        self.assertEqual("winter", KB.outfits("PersonA")["aktiv"])
        self.assertEqual({"aktiv": "", "liste": {}}, KB.outfits("PersonB"))
        self.assertEqual(KB.ORDNER / "PersonB" / "froh.png", S.bilder("PersonB")["froh"])
        self.assertEqual(("", set()), S.outfit_stand("PersonB"))
        self.assertIn("gibt es noch nicht", S.steuern({"outfit": "winter"}, wer="PersonB"))
        self.assertEqual("winter", KB.outfits("PersonA")["aktiv"])

    def test_prompt_und_selbstbeschreibung(self):
        self.assertEqual("x. y. " + KB.RAHMEN_SATZ, KB.prompt_bauen("x.", "y", "unbekannt"))
        self.assertEqual("x. The expression is calm and gentle. " + KB.RAHMEN_SATZ,
                         KB.prompt_bauen("x.", "neutral", ""))
        sd = KB.selbstbeschreibung_system("Juna")
        satz = KB.selbstbeschreibung_system("Juna", "# Juna\n## Aussehen\nSynthetisches Beispiel.")
        self.assertIn("Du bist Juna.", sd)
        self.assertIn("Englisch", sd)
        self.assertIn("ganze Sätze", satz)
        self.assertIn("übernimm das genau", satz)
        self.assertIn("Synthetisches Beispiel.", satz)                          # Persönlichkeit geht mit
        self.assertNotIn("# Deine Persönlichkeit", sd)                        # ohne Text kein leerer Abschnitt
        self.assertNotIn("anime", KB.SELBSTBESCHREIBUNG_SYSTEM.lower())      # kein Stil vorgekaut


if __name__ == "__main__":
    unittest.main()
