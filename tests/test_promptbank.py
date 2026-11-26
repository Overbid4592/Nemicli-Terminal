"""Prompt-Bibliothek für Krea 2 (tools/promptbank.py): eintragen, Urteile, einlesen, Technik, suchen, Vektoren.

python -m unittest tests.test_promptbank
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from PIL import Image, PngImagePlugin  # noqa: E402

import actions  # noqa: E402
import memory  # noqa: E402
import modes  # noqa: E402
import profilordner  # noqa: E402
import promptbank as PB  # noqa: E402

ALTE_URTEILE = PB._alte_urteile           # die echte Funktion – setUp ersetzt sie

EIGEN = ("A portrait photograph of a young woman in a cozy living room. She has red hair with fine blonde strands. "
         "Warm lamp light glows from the left. Shot on an 85mm portrait lens with shallow depth of field.")
FREMD = ("A full body photograph of a woman standing on a beach at sunset. She has long black hair and brown eyes. "
         "She wears a white summer dress. Golden hour light from behind, soft rim light on her shoulders. "
         "Shot on a 50mm lens, sharp focus on the face.")


def png(pfad: Path, prompt: str, backend: str = "krea2", model: str = "krea2_model") -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("prompt", prompt)
    meta.add_text("model", model)
    meta.add_text("params", f"steps=10, cfg=1, size=832x1216, seed=42, sampler=euler, backend={backend}")
    Image.new("RGB", (8, 8)).save(pfad, pnginfo=meta)
    return pfad


class Bibliothek(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wurzel = Path(tmp.name).resolve() / "Profile"
        self.db = Path(tmp.name).resolve() / "learned" / "krea_prompts.db"
        for p in (mock.patch.object(profilordner, "wurzel", return_value=self.wurzel),
                  mock.patch.object(profilordner, "aktiv", return_value="Alpha"),
                  mock.patch.object(PB, "DB", self.db),
                  mock.patch.object(memory, "encoder_loaded", return_value=False),
                  mock.patch.object(PB, "_alte_urteile", return_value={})):
            p.start()
            self.addCleanup(p.stop)
        self.eigen = png(self.wurzel / "Alpha" / "Bilder" / "a1.png", EIGEN)
        self.fremd = png(self.wurzel / "Beta" / "Bilder" / "b1.png", FREMD)

    def test_nur_krea(self):
        self.assertTrue(PB.ist_krea({"params": "seed=1, backend=krea2"}))
        self.assertFalse(PB.ist_krea({"params": "seed=1, backend=webui", "model": "krea_mix"}))
        self.assertTrue(PB.ist_krea({"model": "krea2_mix_model"}))
        sd = png(self.wurzel / "Alpha" / "Bilder" / "sd.png", "a cat", backend="sd", model="sdxl")
        self.assertFalse(PB.eintragen([sd], db=self.db))

    def test_eintragen_mit_profil_und_kopie(self):
        besser = png(self.wurzel / "Alpha" / "Bilder" / "Gesichtszuege" / "a1.png", EIGEN)
        self.assertTrue(PB.eintragen([self.eigen, besser], "cozy living room scene", db=self.db))
        s = PB.stand(self.db)
        self.assertEqual((1, {"Alpha": 1}), (s["gesamt"], s["profile"]))
        self.assertTrue(PB.urteil(besser, "gut", "Licht wunderschön", self.db))          # Urteil über die Kopie
        self.assertEqual({"gut": 1}, PB.stand(self.db)["urteile"])
        PB.eintragen([self.eigen], db=self.db)                                         # neu eintragen: Urteil bleibt
        self.assertEqual({"gut": 1}, PB.stand(self.db)["urteile"])

    def test_einlesen_oberste_ebene_mit_alten_urteilen(self):
        png(self.wurzel / "Alpha" / "Bilder" / "Gesichtszuege" / "a1.png", EIGEN)
        with mock.patch.object(PB, "_alte_urteile", return_value={"b1.png": ("gut", "tolles Licht")}):
            neu, gesamt = PB.einlesen([self.wurzel / "Alpha" / "Bilder", self.wurzel / "Beta" / "Bilder"], self.db)
        self.assertEqual((2, 2), (neu, gesamt))
        s = PB.stand(self.db)
        self.assertEqual(({"Alpha": 1, "Beta": 1}, {"gut": 1}), (s["profile"], s["urteile"]))
        self.assertEqual((0, 2), PB.einlesen([self.wurzel / "Alpha" / "Bilder", self.wurzel / "Beta" / "Bilder"],
                                             self.db))

    def test_alte_urteile_aus_den_notizen(self):
        notizen = [{"text": "Bildgeschmack 👎: Sommersprossen (Bild B1.png, Prompt: …)"},   # neueste zuerst
                   {"text": "Bildgeschmack 👍: schönes Licht (Bild b1.png)"},
                   {"text": "Bildgeschmack ➖: ohne Bild"}]
        with mock.patch.object(memory, "nach_art", return_value=notizen):
            self.assertEqual({"b1.png": ("schlecht", "Sommersprossen")}, ALTE_URTEILE())

    def test_technik_ohne_aussehen_und_kleidung(self):
        t = PB.technik(FREMD)
        self.assertIn("Golden hour light", t)
        self.assertIn("50mm lens", t)
        for wort in ("hair", "eyes", "dress", "black"):
            self.assertNotIn(wort, t)
        self.assertEqual("", PB.technik("Mittellange rote Haare mit blonden Strähnen, warmes Licht."))
        self.assertEqual("", PB.technik("A tasteful nude portrait photograph of me beside a glowing fireplace."))
        self.assertEqual("A portrait photograph of the subject in a cozy room, the subject look into the lens.",
                         PB.technik("A portrait photograph of me in a cozy room, I look into the lens."))

    def test_suchen_eigen_ganz_fremd_nur_technik(self):
        PB.eintragen([self.eigen], db=self.db)
        PB.eintragen([self.fremd], db=self.db)
        t = PB.suchen("woman on a beach at golden hour light", "Alpha", 3, self.db)
        self.assertEqual([], t["technik"])                           # fremd ohne Urteil: nicht gezeigt
        PB.urteil(self.fremd, "gut", "", self.db)
        t = PB.suchen("woman on a beach at golden hour light", "Alpha", 3, self.db)
        self.assertEqual(1, len(t["eigen"]))
        self.assertIn("red hair", t["eigen"][0]["prompt"])
        self.assertEqual(1, len(t["technik"]))
        zeilen = "\n".join(PB.zeilen_fuer_ki(t))
        self.assertIn("Golden hour", zeilen)
        self.assertNotIn("black hair", zeilen)
        self.assertNotIn("summer dress", zeilen)

    def test_warnung_mit_warum(self):
        PB.eintragen([self.eigen], db=self.db)
        PB.urteil(self.eigen, "schlecht", "Sommersprossen im Gesicht", self.db)
        t = PB.suchen("portrait in a living room", "Alpha", 3, self.db)
        self.assertEqual([], t["eigen"])
        self.assertIn("Sommersprossen im Gesicht", "\n".join(PB.zeilen_fuer_ki(t)))
        self.assertIn("ging schief", PB.hinweis("portrait in a living room", "Alpha", self.db))

    def test_vektoren(self):
        PB.eintragen([self.eigen], db=self.db)
        PB.eintragen([self.fremd], db=self.db)

        def einbetten(texte, query=False, **_):
            return [[1.0, 0.0] if ("beach" in t) else [0.0, 1.0] for t in texte]

        with mock.patch.object(memory, "encoder_loaded", return_value=True), \
                mock.patch.object(memory, "_embed_texts", side_effect=einbetten), \
                mock.patch.object(PB, "_kennung", return_value="test-2"):
            self.assertEqual(2, PB.vektorisieren(self.db))
            self.assertEqual(0, PB.vektorisieren(self.db))                    # schon da
            self.assertEqual(2, PB.stand(self.db)["vektor"])
            t = PB.suchen("beach", "Beta", 1, self.db)
            self.assertAlmostEqual(1.0, t["eigen"][0]["aehnlich"])
        with mock.patch.object(PB, "_kennung", return_value="anderes-modell"):
            self.assertEqual(0, PB.stand(self.db)["vektor"])                  # Modellwechsel: neu rechnen

    def test_nebenbei_nur_mit_geladenem_modell(self):
        PB.eintragen([self.eigen], db=self.db)
        with mock.patch.object(memory, "_embed_texts") as einbetten:
            self.assertEqual(0, PB.vektorisieren(self.db, laden=False))           # Modell nicht geladen: nichts
        einbetten.assert_not_called()
        sys.path.insert(0, str(ROOT / "tests"))
        import main_quelle
        quelle = main_quelle.text()
        nach_runde = quelle.index("await _prompt_vektoren_nachrechnen()")
        self.assertLess(nach_runde, quelle.index("memory.release_encoder()", nach_runde))   # vor dem Entladen
        self.assertIn("if memory.encoder_loaded() and promptbank.DB.is_file():", quelle)
        self.assertIn("Vektoren gab es noch keine", quelle)                        # Einlesen sagt, warum

    def test_lern_modul_laedt_im_hintergrund(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import main_quelle
        quelle = main_quelle.text()
        takt = quelle[quelle.index("async def lernen_takt"):]
        takt = takt[:takt.index("while True:")]
        self.assertIn("await asyncio.to_thread", takt)
        self.assertNotIn("from loramotor import lernlora", takt)

    def test_werkzeug_und_sperren(self):
        self.assertIn("prompt_suchen", actions.ACTIONS)
        self.assertIn("prompt_suchen", modes.READ_TOOLS)
        self.assertFalse(actions.ACTIONS["prompt_suchen"]["confirm"])
        self.assertIn("Prompt-Bibliothek", actions.describe({"tool": "prompt_suchen", "frage": "x"}))
        self.assertTrue(actions._DB_NAME.search("krea_prompts.db"))            # Datei-Werkzeuge: gesperrt
        self.assertFalse(actions._prompt_suchen({}).ok)
        PB.eintragen([self.eigen], db=self.db)
        with mock.patch.object(PB, "vektorisieren", return_value=0):
            res = actions._prompt_suchen({"frage": "cozy living room lamp"})
        self.assertIn("Prompt-Bibliothek", res.text)
        self.assertIn("eigener Prompt", res.text)


if __name__ == "__main__":
    unittest.main()
