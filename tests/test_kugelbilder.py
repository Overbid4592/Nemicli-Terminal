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
                on_status(f"Schritt 3/8")
            img = Image.new("RGB", (256, 256), (40, 220, 60)); d = ImageDraw.Draw(img)
            d.ellipse((78, 40, 178, 140), fill=(250, 200, 190)); d.rectangle((98, 130, 158, 230), fill=(60, 60, 120))
            p = self.roh / f"bild_{len(self.aufrufe)}.png"; img.save(p)
            return str(p), True
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
                          on_status=meldungen.append, sd=True)
        self.assertEqual(["Nemi.png", "Nemi_froh.png", "Nemi_ernst.png", "Nemi_denkt.png"], [p.name for p in bilder])
        self.assertEqual(4, len(self.aufrufe))
        self.assertTrue(all(a["seed"] == 4711 and a["size"] == (1024, 1024) for a in self.aufrufe))
        self.assertIn("smiling warmly", self.aufrufe[1]["prompt"]); self.assertIn("serious", self.aufrufe[2]["prompt"])
        self.assertTrue(all("plain flat solid bright green background" in a["prompt"] for a in self.aufrufe))
        self.assertTrue(all(a["prompt"].startswith("small round robot, matte white shell, cyan eyes, ") for a in self.aufrufe))
        self.assertTrue(all(a["neg"] == KB.NEGATIV for a in self.aufrufe))
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
        KB.malen("Nemi", "same girl", seed=1, sd=True)
        self.assertEqual(4, len(list(snapshot.ORDNER.glob("*Nemi*.png"))))
        # entfernen → Papierkorb, Kugel wieder Kugel
        self.assertEqual(4, KB.entfernen("Nemi"))
        self.assertEqual({}, KB.vorhandene("Nemi"))
        self.assertEqual(8, len(list(snapshot.ORDNER.glob("*Nemi*.png"))))

    def test_ohne_beschreibung_und_zufallsseed(self):
        with self.assertRaises(RuntimeError):
            KB.malen("Nemi", "   ")
        KB.malen("Maia", "a small robot", stimmungen=[("", "neutral"), ("froh", "happy")], sd=True)
        self.assertEqual(2, len(self.aufrufe))
        self.assertEqual(self.aufrufe[0]["seed"], self.aufrufe[1]["seed"])
        self.assertIsInstance(self.aufrufe[0]["seed"], int)
        self.assertEqual(["Maia.png", "Maia_froh.png"], sorted(p.name for p in KB.ORDNER.glob("*.png")))

    def test_krea_saetze_ohne_negativ(self):
        KB.malen("Nemi", "A small round robot with a matte white shell and cyan eyes.", seed=7, sd=False)
        self.assertTrue(all(a["neg"] is None for a in self.aufrufe))
        self.assertIn("warm, happy smile", self.aufrufe[1]["prompt"])
        for a in self.aufrufe:
            self.assertTrue(a["prompt"].startswith("A small round robot with a matte white shell and cyan eyes. "))
            self.assertTrue(a["prompt"].endswith(KB.RAHMEN_SATZ))
            self.assertNotIn(KB.RAHMEN, a["prompt"])                          # keine Stichwort-Stapel

    def test_prompt_und_selbstbeschreibung(self):
        self.assertEqual("x, y, " + KB.RAHMEN, KB.prompt_bauen("x.", "y"))
        self.assertEqual("x. The expression is calm and gentle. " + KB.RAHMEN_SATZ,
                         KB.prompt_bauen("x.", "neutral", "", sd=False))
        sd = KB.selbstbeschreibung_system("Maia", sd=True)
        satz = KB.selbstbeschreibung_system("Maia", "# Maia\n## Aussehen\nSynthetisches Beispiel.", sd=False)
        self.assertIn("Du bist Maia.", sd)
        self.assertIn("Englisch", sd)
        self.assertIn("Stichworten", sd)
        self.assertIn("ganze Sätze", satz)
        self.assertIn("übernimm das genau", satz)
        self.assertIn("Synthetisches Beispiel.", satz)                          # Persönlichkeit geht mit
        self.assertNotIn("# Deine Persönlichkeit", sd)                        # ohne Text kein leerer Abschnitt
        self.assertNotIn("anime", KB.SELBSTBESCHREIBUNG_SYSTEM.lower())      # kein Stil vorgekaut


if __name__ == "__main__":
    unittest.main()
