"""Figur der Persönlichkeit im eigenen Terminal-Fenster: Steuerbefehle, Posen, Bewegung, Bilder, Malen."""

import base64
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import figur              # noqa: E402
import kugelbilder as KB  # noqa: E402
import startseite         # noqa: E402
import terminal_fenster as TF  # noqa: E402


def _png(pfad: Path, breite: int = 40, hoehe: int = 60) -> Path:
    from PIL import Image
    pfad.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (breite, hoehe), (200, 120, 255, 255)).save(pfad)
    return pfad


def _posen(daten: dict) -> str:
    return "posen=" + base64.urlsafe_b64encode(json.dumps(daten).encode("utf-8")).decode("ascii")


class Gleichstand(unittest.TestCase):
    def test_fenster_kennt_dieselben_posen(self):
        self.assertEqual(set(TF.FIGUR_POSEN), set(figur.POSEN))
        self.assertEqual(set(TF.FIGUR_KURZ), set(figur.KURZ))
        self.assertTrue(set(figur.STATUS_POSE.values()) <= set(figur.POSEN))


class Steuerbefehle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_pose_und_sitz(self):
        self.assertEqual(TF.figur_befehl("pose=freude"), ("pose", "freude"))
        self.assertIsNone(TF.figur_befehl("pose=tanzen"))
        self.assertEqual(TF.figur_befehl("sitz=5"), ("sitz", 5))
        self.assertEqual(TF.figur_befehl("sitz=0"), ("sitz", 1))
        self.assertIsNone(TF.figur_befehl("sitz=abc"))
        self.assertIsNone(TF.figur_befehl("puls=1"))

    def test_posen_mit_bildern(self):
        sitzen, winken = _png(self.tmp / "sitzen.png"), _png(self.tmp / "winken.png")
        art, bilder = TF.figur_befehl(_posen({"art": "figur", "bilder": {
            "sitzen": str(sitzen), "winken": str(winken), "tanzen": str(winken),
            "freude": str(self.tmp / "fehlt.png"), "herz": str(self.tmp / "text.txt")}}))[1]
        self.assertEqual(art, "figur")
        self.assertEqual(set(bilder), {"sitzen", "winken"})              # Unbekanntes und Fehlendes fällt weg

    def test_ohne_sitzen_keine_figur(self):
        winken = _png(self.tmp / "winken.png")
        self.assertEqual(TF.figur_befehl(_posen({"art": "figur", "bilder": {"winken": str(winken)}})),
                         ("posen", ("", {})))
        self.assertEqual(TF.figur_befehl("posen="), ("posen", ("", {})))
        self.assertIsNone(TF.figur_befehl("posen=@@@"))
        self.assertEqual(TF.figur_befehl(_posen({"art": "riese", "bilder": {"sitzen": str(winken)}})),
                         ("posen", ("", {})))

    def test_schalter(self):
        self.assertEqual(TF.steuer_wert("figur=0"), ("figur", "0"))
        self.assertEqual(TF.steuer_wert("figur=1"), ("figur", "1"))


class PoseUndBewegung(unittest.TestCase):
    def test_kurze_pose_endet_von_selbst(self):
        self.assertEqual(TF.figur_pose("sitzen", "freude", 100.0, 101.0, 100.0), "freude")
        self.assertEqual(TF.figur_pose("sitzen", "freude", 100.0, 100.0 + TF.FIGUR_KURZ["freude"] + 0.1, 100.0),
                         "sitzen")
        self.assertEqual(TF.figur_pose("nachdenken", None, 0.0, 50.0, 49.0), "nachdenken")

    def test_doest_nur_in_ruhe(self):
        spaet = TF.FIGUR_SCHLAF_S + 1
        self.assertEqual(TF.figur_pose("sitzen", None, 0.0, spaet, 0.0), "schlafen")
        self.assertEqual(TF.figur_pose("arbeiten", None, 0.0, spaet, 0.0), "arbeiten")

    def test_bewegung_bleibt_klein(self):
        for pose in TF.FIGUR_POSEN:
            for t in (0.0, 0.3, 0.7, 1.1, 2.5, 9.0):
                dy, winkel, sx, sy = TF.figur_bewegung(pose, t)
                with self.subTest(pose=pose, t=t):
                    self.assertTrue(all(math.isfinite(v) for v in (dy, winkel, sx, sy)))
                    self.assertLessEqual(abs(dy), 0.1)
                    self.assertLessEqual(abs(winkel), 6.0)
                    self.assertTrue(0.9 < sx < 1.1 and 0.9 < sy < 1.1)

    def test_freude_huepft_zweimal(self):
        self.assertLess(TF.figur_bewegung("freude", 0.4)[0], -0.05)
        self.assertLess(TF.figur_bewegung("freude", 1.2)[0], -0.05)
        self.assertEqual(TF.figur_bewegung("freude", 2.0)[0], 0.0)

    def test_neugierig_legt_den_kopf_schief(self):
        self.assertAlmostEqual(TF.figur_bewegung("neugierig", 1.0)[1], 5.0)

    def test_fuesse_bleiben_im_fenster(self):
        zh = 27.0
        for sitz in (3, 5, 8):
            h = TF.figur_hoehe("figur", zh, 900, sitz)
            with self.subTest(sitz=sitz):
                self.assertLessEqual(h * (1 - TF.FIGUR_SITZ_ANTEIL), (sitz - 0.5) * zh + 8)
        self.assertEqual(TF.figur_hoehe("buste", zh, 900, 3), TF.FIGUR_ZEILEN["buste"] * zh)
        self.assertLessEqual(TF.figur_hoehe("buste", zh, 300, 5), 300 * TF.FIGUR_HOECHSTENS)

    def test_platz_nur_wenn_genug_text_bleibt(self):
        self.assertGreater(TF.figur_reserve(140, 9.0, 150.0), 0)
        self.assertEqual(TF.figur_reserve(85, 9.0, 150.0), 0)
        frei = TF.figur_reserve(140, 9.0, 150.0)
        self.assertGreaterEqual(frei * 9.0, 150.0)


class Bilder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        p = patch.object(KB, "motiv_ordner", lambda name: self.tmp / name)
        p.start()
        self.addCleanup(p.stop)

    def test_ganze_figur_geht_vor(self):
        _png(self.tmp / "PersonA" / "Figur" / "sitzen.png")
        _png(self.tmp / "PersonA" / "Figur" / "freude.png")
        art, bilder = figur.bilder("PersonA")
        self.assertEqual(art, "figur")
        self.assertEqual(set(bilder), set(figur.POSEN))                  # fehlende Posen zeigen „sitzen“
        self.assertEqual(bilder["freude"].name, "freude.png")
        self.assertEqual(bilder["herz"].name, "sitzen.png")

    def test_buste_aus_motiven_und_stimmungen(self):
        _png(self.tmp / "PersonB" / "jubelt.png")
        neutral = _png(self.tmp / "PersonB" / "neutral.png")
        denkt = _png(self.tmp / "PersonB" / "denkt.png")
        with patch.object(KB, "vorhandene", return_value={"": neutral, "denkt": denkt}):
            art, bilder = figur.bilder("PersonB")
        self.assertEqual(art, "buste")
        self.assertEqual(bilder["freude"].name, "jubelt.png")             # Motiv vor Stimmung
        self.assertEqual(bilder["nachdenken"].name, "denkt.png")
        self.assertEqual(bilder["sitzen"].name, "neutral.png")

    def test_ohne_bilder_keine_figur(self):
        with patch.object(KB, "vorhandene", return_value={}):
            self.assertEqual(figur.bilder("PersonC"), ("", {}))


class Melden(unittest.TestCase):
    def setUp(self):
        self.gesendet = []
        self.alt = dict(figur._STAND)
        self.addCleanup(lambda: (figur._STAND.clear(), figur._STAND.update(self.alt)))
        for ziel, wert in ((startseite, ("STEUERN", self.gesendet.append)),
                           (startseite, ("echtes_fenster", lambda: True))):
            p = patch.object(ziel, wert[0], wert[1])
            p.start()
            self.addCleanup(p.stop)
        figur._STAND.update(pose=None, posen=None, herz=False, antwort="", art="")
        p = patch.object(figur, "_strich_bereit", lambda: False)        # Bilder-Figur, unabhängig vom Rechner
        p.start()
        self.addCleanup(p.stop)

    def test_neue_persoenlichkeit_winkt_einmal(self):
        a = ("buste", {"sitzen": Path("a.png")})
        b = ("buste", {"sitzen": Path("b.png")})
        with patch.object(figur, "bilder", side_effect=lambda name: a if name == "PersonA" else b):
            figur.melden("PersonA")
            figur.melden("PersonA")                                       # gleiche Bilder: nichts Neues
            figur.melden("PersonB")
        posen = [s for s in self.gesendet if s.startswith("posen=")]
        self.assertEqual(len(posen), 2)
        self.assertEqual(self.gesendet.count("pose=winken"), 2)

    def test_status_und_zugende(self):
        figur.status("denkt")
        figur.status("prueft")
        figur.status("denkt")
        figur.status(None)
        self.assertEqual(self.gesendet, ["pose=nachdenken", "pose=pruefen", "pose=nachdenken", "pose=sitzen"])
        self.gesendet.clear()
        figur.nutzer("Danke dir ❤")
        figur.fertig()
        self.assertEqual(self.gesendet, ["pose=herz"])

    def test_zugende_ohne_herz(self):
        self.assertEqual(figur.pose_nach_zug(False, "Soll ich noch eins malen?"), "neugierig")
        self.assertEqual(figur.pose_nach_zug(False, "Erwischt 😏"), "grinsen")
        self.assertEqual(figur.pose_nach_zug(False, "Fertig."), "freude")
        self.assertEqual(figur.pose_nach_zug(True, "Fertig?"), "herz")

    def test_sitz_begrenzt(self):
        figur.sitz(0)
        figur.sitz(500)
        self.assertEqual(self.gesendet, ["sitz=1", "sitz=99"])

    def test_ohne_eigenes_fenster_still(self):
        with patch.object(startseite, "echtes_fenster", lambda: False):
            figur.pose("freude")
        self.assertEqual(self.gesendet, [])


class Malen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for name, wert in (("motiv_ordner", lambda n: self.tmp / n),
                           ("figur", lambda n: {"beschreibung": "a person", "seed": 42, "kleidung": ""}),
                           ("veraltet", lambda n: False), ("_mit_lora", lambda n, b: (b, {})),
                           ("_koerper_noetig", lambda: False)):      # nur die Bild-Posen (Körper: test_figurrig)
            p = patch.object(KB, name, wert)
            p.start()
            self.addCleanup(p.stop)
        self.prompts = []

        def malen(prompt, **k):
            self.prompts.append((prompt, k["seed"]))
            return str(_png(self.tmp / f"roh{len(self.prompts)}.png"))

        p = patch.object(KB, "_maler", lambda: malen)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(KB, "freistellen", lambda quelle, ziel, groesse, quadrat=True: _png(ziel))
        p.start()
        self.addCleanup(p.stop)

    def test_alle_posen_gleiche_figur(self):
        fertig = KB.figur_malen("PersonA")
        self.assertEqual({f.stem for f in fertig}, set(figur.POSEN))
        self.assertTrue(all(f.parent == self.tmp / "PersonA" / "Figur" for f in fertig))
        self.assertTrue(all(seed == 42 for _p, seed in self.prompts))
        self.assertTrue(all(KB.RAHMEN_FIGUR_SATZ in p for p, _s in self.prompts))
        self.assertTrue(any(figur.POSEN["herz"][1] in p for p, _s in self.prompts))
        self.assertEqual(KB.figur_fehlend("PersonA"), [])
        self.assertEqual(KB.figur_malen("PersonA"), [])                   # nichts mehr offen

    def test_abbruch_nach_dem_laufenden_bild(self):
        fertig = KB.figur_malen("PersonA", abbruch=lambda: len(self.prompts) >= 2)
        self.assertEqual(len(fertig), 2)
        self.assertEqual(len(KB.figur_fehlend("PersonA")), len(figur.POSEN) - 2)

    def test_ohne_grundbilder_nicht(self):
        with patch.object(KB, "figur", lambda n: None):
            with self.assertRaises(RuntimeError):
                KB.figur_malen("PersonA")


class Freistellen(unittest.TestCase):
    def test_figur_bleibt_hochkant(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        quelle = tmp / "roh.png"
        bild = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        bild.paste(Image.new("RGBA", (100, 300), (255, 0, 0, 255)), (150, 50))
        bild.save(quelle)
        attrappe = type(sys)("rembg")
        attrappe.new_session = lambda name: object()
        attrappe.remove = lambda b, session=None: b
        with patch.dict(sys.modules, {"rembg": attrappe}), patch.object(KB, "_rembg_session", None):
            ziel = KB.freistellen(quelle, tmp / "figur.png", 300, quadrat=False)
        with Image.open(ziel) as fertig:
            self.assertEqual(fertig.height, 300)
            self.assertLess(fertig.width, 200)                            # nicht auf ein Quadrat aufgefüllt


class Hilfe(unittest.TestCase):
    def test_hilfe_kennt_die_figur(self):
        import commands
        self.assertIn("figur", commands.hilfe_fuer_ki("/terminal"))
        self.assertIn("/kugel malen figur", commands.hilfe_fuer_ki("/kugel"))


if __name__ == "__main__":
    unittest.main()
