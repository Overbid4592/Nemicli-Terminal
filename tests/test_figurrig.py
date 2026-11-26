"""Bewegliche Figur aus dem eigenen Bild: 22 Gelenke auflegen, Haut (Dreiecke mit Gewichten) bauen, ablegen, malen
lassen, im Fenster mit den Knochen bewegen. Mit gezeichneten Figuren in T-Haltung und (Rückfall) A-Haltung."""

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

import figur                     # noqa: E402
import figurgesicht              # noqa: E402
import figurmass                 # noqa: E402
import figurrig as FR            # noqa: E402
import kugelbilder as KB         # noqa: E402
import startseite                # noqa: E402
import terminal_fenster as TF    # noqa: E402

HAUT, HAAR, OBEN, UNTEN, SCHUH = ((255, 214, 170, 255), (250, 220, 90, 255), (110, 40, 140, 255), (30, 25, 35, 255),
                                  (230, 230, 235, 255))
# Wo die Gelenke der gezeichneten Figuren wirklich liegen (rechte Seite der Figur im Bild links)
ECHT_T = {"neck": (500, 190), "r_shoulder": (450, 212), "r_elbow": (260, 216), "r_wrist": (90, 222),
          "r_hip": (462, 540), "r_knee": (445, 750), "r_ankle": (430, 950)}
ECHT_A = {"neck": (300, 150), "r_shoulder": (240, 190), "r_elbow": (150, 330), "r_wrist": (110, 460),
          "r_hip": (270, 540), "r_knee": (255, 760), "r_ankle": (250, 930)}


def t_haltung(vorne: bool = True):
    """Figur 1000 × 1024 in T-Haltung: Arme waagerecht, Beine leicht gegrätscht."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (1000, 1024), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((440, 20, 560, 175), fill=HAUT if vorne else HAAR)
    d.pieslice((432, 10, 568, 130), 180, 360, fill=HAAR)
    d.rectangle((485, 165, 515, 205), fill=HAUT)
    for richtung in (-1, 1):
        schulter, ellbogen, hand = 500 + richtung * 55, 500 + richtung * 240, 500 + richtung * 440
        d.line((schulter, 214, ellbogen, 218), fill=HAUT, width=34)
        d.line((ellbogen, 218, hand, 222), fill=HAUT, width=28)
        d.ellipse((hand - 22, 200, hand + 22, 244), fill=HAUT)
        huefte, knie, knoechel = 500 + richtung * 38, 500 + richtung * 55, 500 + richtung * 70
        d.line((huefte, 540, knie, 750), fill=HAUT, width=44)
        d.line((knie, 750, knoechel, 960), fill=HAUT, width=36)
        d.rectangle((knoechel - 25, 955, knoechel + 25, 1000), fill=SCHUH)
    d.rounded_rectangle((420, 195, 580, 455), 12, fill=OBEN)
    d.rectangle((422, 445, 578, 570), fill=UNTEN)
    return im


def s_haltung():
    """Figur 1000 × 1024, Arme schräg nach unten (etwa 40°) – so malt das Bildmodell die T-Haltung oft."""
    from PIL import Image, ImageDraw
    im = t_haltung().copy()
    d = ImageDraw.Draw(im)
    d.rectangle((0, 195, 1000, 245), fill=(0, 0, 0, 0))                # die waagerechten Arme weg
    d.rounded_rectangle((420, 195, 580, 455), 12, fill=OBEN)
    for richtung in (-1, 1):
        schulter, ellbogen, hand = (500 + richtung * 60, 214), (500 + richtung * 200, 330), (500 + richtung * 330, 440)
        d.line((*schulter, *ellbogen), fill=HAUT, width=34)
        d.line((*ellbogen, *hand), fill=HAUT, width=28)
        d.ellipse((hand[0] - 22, hand[1] - 22, hand[0] + 22, hand[1] + 22), fill=HAUT)
    return im


def a_haltung(vorne: bool = True):
    """Figur 600 × 1024, Arme nah am Körper."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (600, 1024), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((240, 10, 360, 150), fill=HAUT if vorne else HAAR)
    d.pieslice((232, 0, 368, 120), 180, 360, fill=HAAR)
    d.rectangle((285, 140, 315, 175), fill=HAUT)
    d.polygon([(230, 175), (370, 175), (355, 470), (245, 470)], fill=OBEN)
    for x1, x2, x3 in ((235, 150, 105), (365, 450, 495)):
        d.line((x1, 185, x2, 330), fill=HAUT, width=34)
        d.line((x2, 330, x3, 470), fill=HAUT, width=28)
        d.ellipse((x3 - 20, 460, x3 + 20, 515), fill=HAUT)
    d.polygon([(245, 460), (355, 460), (360, 560), (240, 560)], fill=UNTEN)
    for x1, x2, x3 in ((270, 255, 250), (330, 345, 350)):
        d.line((x1, 540, x2, 760), fill=HAUT, width=44)
        d.line((x2, 760, x3, 960), fill=HAUT, width=36)
        d.rectangle((x3 - 25, 955, x3 + 25, 1000), fill=SCHUH)
    return im


class Skelett(unittest.TestCase):
    def test_alle_22_gelenke(self):
        g = FR.gelenke_schaetzen(FR._maske(t_haltung()), "vorne")
        self.assertEqual(set(FR.ELTERN) | {"pelvis"}, set(FR.ELTERN) | {e for e, _k in FR.KNOCHEN})
        for name in list(FR.ELTERN) + ["pelvis", "kopf_oben", "l_hand", "r_hand"]:
            self.assertIn(name, g)
        self.assertEqual(len(FR.KNOCHEN), 23)                             # 21 der Bewegung + 2 Hände

    def test_t_haltung_von_vorn(self):
        g = FR.gelenke_schaetzen(FR._maske(t_haltung()), "vorne")
        for name, (x, y) in ECHT_T.items():
            with self.subTest(gelenk=name):
                self.assertLess(math.hypot(g[name][0] - x, g[name][1] - y), 45, g[name])
        self.assertLess(g["r_shoulder"][0], g["l_shoulder"][0])          # von vorn: rechts im Bild links
        self.assertLess(g["r_hand"][0], g["r_wrist"][0])                 # die Hand liegt außen
        y = [g[n][1] for n in ("pelvis", "spine1", "spine2", "spine3", "neck", "head", "kopf_oben")]
        self.assertEqual(y, sorted(y, reverse=True))                     # der Rücken läuft von unten nach oben

    def test_von_hinten_seiten_getauscht(self):
        g = FR.gelenke_schaetzen(FR._maske(t_haltung(False)), "hinten")
        self.assertGreater(g["r_shoulder"][0], g["l_shoulder"][0])
        self.assertGreater(g["r_hip"][0], g["l_hip"][0])

    def test_schraege_arme(self):
        """Arme schräg nach unten: der Hals bleibt oben (nicht bei den Händen), die Hände liegen außen unten."""
        g = FR.gelenke_schaetzen(FR._maske(s_haltung()), "vorne")
        self.assertLess(abs(g["neck"][1] - 190), 45, g["neck"])
        self.assertLess(math.hypot(g["r_hand"][0] - 150, g["r_hand"][1] - 455), 45, g["r_hand"])
        self.assertLess(math.hypot(g["r_elbow"][0] - 300, g["r_elbow"][1] - 330), 50, g["r_elbow"])
        self.assertLess(math.hypot(g["l_wrist"][0] - 800, g["l_wrist"][1] - 420), 60, g["l_wrist"])
        self.assertLess(abs(g["r_hip"][1] - 540), 45, g["r_hip"])

    def test_a_haltung_geht_auch(self):
        g = FR.gelenke_schaetzen(FR._maske(a_haltung()), "vorne")
        for name, (x, y) in ECHT_A.items():
            with self.subTest(gelenk=name):
                self.assertLess(math.hypot(g[name][0] - x, g[name][1] - y), 45, g[name])
        self.assertGreater(g["r_hip"][1], g["r_wrist"][1] + 40)          # Hände sind keine Beine

    def test_zu_wenig_bild(self):
        from PIL import Image
        with self.assertRaises(ValueError):
            FR.gelenke_schaetzen(FR._maske(Image.new("RGBA", (50, 20))), "vorne")


class Huelle(unittest.TestCase):
    def setUp(self):
        self.maske = FR._maske(t_haltung())
        self.g = FR.gelenke_schaetzen(self.maske, "vorne")
        self.feld = FR.teile_zuordnen(self.maske, self.g)
        self.stuecke = FR.huelle(self.feld, self.g)

    def test_jeder_knochen_hat_seine_roehre(self):
        roehren = [s for s in self.stuecke if s["art"] == "roehre"]
        self.assertEqual(len(roehren), len(FR.ROEHREN))
        self.assertEqual({s["knochen"] for s in roehren}, {FR.KNOCHEN.index(k) for k, _v, _b in FR.ROEHREN})
        self.assertEqual(len([s for s in self.stuecke if s["art"] == "kappe"]), len(FR.KAPPEN))

    def test_jedes_pixel_in_genau_einer_roehre(self):
        import numpy as np
        summe = sum(s["maske"].astype(np.int32) for s in self.stuecke if s["art"] == "roehre")
        self.assertTrue(((summe == 1) == self.maske).all())

    def test_ueber_dem_hals_nur_kopf(self):
        import numpy as np
        kopf = next(s for s in self.stuecke if s["art"] == "roehre" and s["knochen"] == FR.KNOCHEN.index(("neck", "head")))
        ys, _xs = np.nonzero(kopf["maske"])
        self.assertLess(ys.max(), self.g["neck"][1])
        hals = int(self.g["neck"][1]) - 5
        self.assertTrue((kopf["maske"][:hals] == self.maske[:hals]).all())

    def test_kappe_so_dick_wie_das_glied(self):
        """Die Kappe am Ellbogen ist rund um das Gelenk und etwa so groß, wie der Arm dort dick ist."""
        import numpy as np
        unterarm = FR.KNOCHEN.index(("r_elbow", "r_wrist"))
        kappe = next(s for s in self.stuecke if s["art"] == "kappe" and s["knochen"] == unterarm)
        ys, xs = np.nonzero(kappe["maske"])
        ex, ey = self.g["r_elbow"]
        self.assertLess(np.hypot(xs - ex, ys - ey).max(), 30)              # Arm etwa 28–34 px dick
        self.assertGreater(len(xs), 300)

    def test_teile_mit_klarer_grenze(self):
        """Ein Arm beginnt außen am Schultergelenk, ein Bein unter dem Hüftgelenk; alle fünf Teile sind da."""
        import numpy as np
        self.assertEqual(set(np.unique(self.feld)) - {0}, {1, 2, 3, 4, 5})
        for seite in ("l", "r"):
            ys, xs = np.nonzero(self.feld == FR.TEILE.index(f"arm_{seite}") + 1)
            sx = self.g[f"{seite}_shoulder"][0]
            aussen = 1 if sx >= self.g["neck"][0] else -1
            self.assertTrue(((xs - sx) * aussen >= 0).all())
            ys, xs = np.nonzero(self.feld == FR.TEILE.index(f"bein_{seite}") + 1)
            self.assertGreaterEqual(ys.min(), self.g[f"{seite}_hip"][1])

    def test_weiter_pullover_gehoert_zum_rumpf(self):
        """Ist der Rumpf unter der Achsel breiter als die Schultern (weiter Pullover), gehört seine Seite zum Rumpf –
        sonst klappte sie beim Armheben wie ein Flügel mit."""
        import numpy as np
        from PIL import ImageDraw
        im = s_haltung()
        ImageDraw.Draw(im).rectangle((380, 300, 620, 455), fill=OBEN)      # breiter als die Schultern (440 … 560)
        m = FR._maske(im)
        g = FR.gelenke_schaetzen(m, "vorne")
        feld = FR.teile_zuordnen(m, g)
        rumpf = FR.TEILE.index("rumpf") + 1
        self.assertTrue((feld[320:440, 385:440] == rumpf).all())            # die breite Seite: Rumpf
        wx, wy = g["r_wrist"]
        self.assertEqual(feld[int(wy), int(wx)], FR.TEILE.index("arm_r") + 1)   # der freie Arm bleibt Arm

    def test_naht_genau_halb_halb(self):
        """An jeder Naht zwischen zwei Rücken-Röhren mischen genau ihre zwei Knochen, auf beiden Seiten gleich."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        t_haltung().save(tmp / "k.png")
        d = json.loads(FR.bauen(tmp / "k.png", tmp / "rig", "vorne").read_text(encoding="utf-8"))
        unten, oben = FR.KNOCHEN.index(("spine1", "spine2")), FR.KNOCHEN.index(("spine2", "spine3"))
        naht_y = d["gelenke"]["spine2"][1]
        for kn, anderer in ((unten, oben), (oben, unten)):
            netz = next(s for s in d["stuecke"] if s["art"] == "roehre" and s["knochen"] == kn)["netz"]
            i = min(range(len(netz["punkte"])), key=lambda j: abs(netz["punkte"][j][1] - naht_y))
            self.assertEqual(dict((k, w) for k, w in netz["gewichte"][i]), {kn: 0.5, anderer: 0.5})

    def test_kappen_zuerst(self):
        rig = {"stuecke": [{"art": "roehre", "knochen": 0}, {"art": "kappe", "knochen": 1},
                           {"art": "roehre", "knochen": 1}, {"art": "kappe", "knochen": 0}]}
        reihe = TF.rig_reihe(rig, [0.5, -0.5])
        self.assertEqual({rig["stuecke"][i]["art"] for i in reihe[:2]}, {"kappe"})
        self.assertEqual(reihe[2:], [2, 0])                                 # Röhren: hintere (kleinere Tiefe) zuerst


class Ablage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        t_haltung().save(self.tmp / "koerper_vorne.png")
        self.rig_pfad = FR.bauen(self.tmp / "koerper_vorne.png", self.tmp / "rig_vorne", "vorne")

    def _aendern(self, **neu):
        d = json.loads(self.rig_pfad.read_text(encoding="utf-8"))
        d.update(neu)
        self.rig_pfad.write_text(json.dumps(d), encoding="utf-8")

    def test_bauen_und_im_fenster_lesen(self):
        rig = TF.rig_lesen(self.rig_pfad)
        self.assertIsNotNone(rig)
        self.assertEqual(len(rig["stuecke"]), len(FR.ROEHREN) + len(FR.KAPPEN))
        for s in rig["stuecke"]:
            self.assertTrue(s["bild"].is_file())
            self.assertEqual(s["bild"].parent, self.tmp / "rig_vorne")
        self.assertEqual(len(rig["knochen"]), 23)
        self.assertGreater(rig["hoehe"], 900)
        self.assertTrue(FR.aktuell(self.rig_pfad))

    def test_roehre_weich_am_gelenk_starr_in_der_mitte(self):
        """Das Netz einer Röhre: in der Mitte nur ihr Knochen, am Gelenk halb und halb mit dem Nachbarn."""
        d = json.loads(self.rig_pfad.read_text(encoding="utf-8"))
        unterarm = FR.KNOCHEN.index(("r_elbow", "r_wrist"))
        oberarm = FR.KNOCHEN.index(("r_shoulder", "r_elbow"))
        netz = next(s for s in d["stuecke"] if s["art"] == "roehre" and s["knochen"] == unterarm)["netz"]
        for g in netz["gewichte"]:
            self.assertAlmostEqual(sum(w for _k, w in g), 1.0, places=3)
        ex, ey = d["gelenke"]["r_elbow"]
        am_gelenk = min(range(len(netz["punkte"])), key=lambda i: abs(math.hypot(netz["punkte"][i][0] - ex,
                                                                                  netz["punkte"][i][1] - ey)))
        gewicht = dict((k, w) for k, w in netz["gewichte"][am_gelenk])
        self.assertAlmostEqual(gewicht.get(oberarm, 0), 0.5, delta=0.1)
        einzeln = [g for g in netz["gewichte"] if len(g) == 1]
        self.assertTrue(einzeln and all(g[0][0] == unterarm for g in einzeln))   # Mitte: starr

    def test_fenster_prueft_das_netz(self):
        d = json.loads(self.rig_pfad.read_text(encoding="utf-8"))
        i = next(i for i, s in enumerate(d["stuecke"]) if s.get("netz"))
        kaputt = json.loads(json.dumps(d["stuecke"]))
        kaputt[i]["netz"]["dreiecke"].append([0, 1, len(kaputt[i]["netz"]["punkte"])])
        self._aendern(stuecke=kaputt)
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))
        kaputt = json.loads(json.dumps(d["stuecke"]))
        kaputt[i]["netz"]["gewichte"][0] = [[len(d["knochen"]), 1.0]]
        self._aendern(stuecke=kaputt)
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))

    def test_dreieck_genau(self):
        q, z = ((0, 0), (10, 0), (0, 10)), ((100, 50), (120, 55), (95, 70))
        a, b, c, dd, e, f = TF.dreieck_abbildung(q, z)
        for (x, y), (zx, zy) in zip(q, z):
            self.assertAlmostEqual(a * x + c * y + e, zx)
            self.assertAlmostEqual(b * x + dd * y + f, zy)
        self.assertIsNone(TF.dreieck_abbildung(((0, 0), (5, 5), (10, 10)), z))

    def test_alte_form_gilt_nicht(self):
        self._aendern(version=1)
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))
        self.assertFalse(FR.aktuell(self.rig_pfad))

    def test_fenster_lehnt_fremdes_ab(self):
        d = json.loads(self.rig_pfad.read_text(encoding="utf-8"))
        self._aendern(stuecke=[dict(d["stuecke"][0], bild="../boese.png")])
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))
        self.assertIsNone(TF.rig_lesen(self.tmp / "gibtsnicht" / "rig.json"))

    def test_fenster_prueft_nummern(self):
        d = json.loads(self.rig_pfad.read_text(encoding="utf-8"))
        self._aendern(stuecke=d["stuecke"] + [dict(d["stuecke"][0], knochen=len(d["knochen"]))])
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))
        self._aendern(stuecke=d["stuecke"] + [dict(d["stuecke"][0], art="boese")])
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))
        self._aendern(stuecke=d["stuecke"] + [dict(d["stuecke"][0], ursprung=[float("nan"), 0])])
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))
        self._aendern(stuecke=d["stuecke"], knochen=d["knochen"] + [["neck", "schwanz"]])
        self.assertIsNone(TF.rig_lesen(self.rig_pfad))

    def test_befehl(self):
        kopf = base64.urlsafe_b64encode(json.dumps({"vorne": str(self.rig_pfad)}).encode()).decode()
        name, rigs = TF.figur_befehl("rig=" + kopf)
        self.assertEqual((name, set(rigs)), ("rig", {"vorne"}))
        self.assertEqual(TF.figur_befehl("rig="), ("rig", {}))
        nur_hinten = base64.urlsafe_b64encode(json.dumps({"hinten": str(self.rig_pfad)}).encode()).decode()
        self.assertEqual(TF.figur_befehl("rig=" + nur_hinten), ("rig", {}))   # ohne Vorderansicht keine Figur


class Abbildung(unittest.TestCase):
    def _anwenden(self, m, x, y):
        a, b, c, d, e, f = m
        return a * x + c * y + e, b * x + d * y + f

    def _rig(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        t_haltung().save(tmp / "k.png")
        return TF.rig_lesen(FR.bauen(tmp / "k.png", tmp / "rig_vorne", "vorne"))

    def test_knochen_liegt_auf_knochen(self):
        m = TF.rig_abbildung((10, 10), (10, 110), (200, 300), (260, 380), 1.0, 1.0)
        x, y = self._anwenden(m, 10, 10)
        self.assertAlmostEqual(x, 200)
        self.assertAlmostEqual(y, 300)
        x, y = self._anwenden(m, 10, 110)                                 # Länge 100 -> 100 (laengs 1), Richtung stimmt
        self.assertAlmostEqual(math.atan2(y - 300, x - 200), math.atan2(80, 60))
        self.assertAlmostEqual(math.hypot(x - 200, y - 300), 100)

    def test_quer_schmal(self):
        m = TF.rig_abbildung((0, 0), (0, 100), (0, 0), (0, 100), 1.0, 0.5)
        x, _y = self._anwenden(m, 40, 0)
        self.assertAlmostEqual(abs(x), 20)

    def test_ruhe_laesst_jedes_stueck_wo_es_ist(self):
        """Steht die Bewegung genau wie das Bild, liegt jedes Stück auf seinem Platz (jede Abbildung ist die Einheit)."""
        rig = self._rig()
        ziel = {n: (x, y, 0.0) for n, (x, y) in rig["gelenke"].items()}
        for kn, m in zip(rig["knochen"], TF.rig_knochen(rig, ziel, 1.0, 1.0)):
            if kn in TF.RIG_HAND:                                         # die Hand legt sich eigens (Test unten)
                continue
            for x, y in ((0, 0), (500, 300), (900, 1000)):
                px, py = self._anwenden(m, x, y)
                self.assertLess(math.hypot(px - x, py - y), 1e-6)

    def test_verschoben_und_kleiner(self):
        rig = self._rig()
        ziel = {n: (x * 0.5 + 100, y * 0.5 + 40, 0.0) for n, (x, y) in rig["gelenke"].items()}
        for kn, m in zip(rig["knochen"], TF.rig_knochen(rig, ziel, 0.5, 1.0)):
            if kn in TF.RIG_HAND:
                continue
            px, py = self._anwenden(m, 400, 600)
            self.assertLess(math.hypot(px - 300, py - 340), 1e-6)

    def test_arm_runter(self):
        """Den rechten Arm aus der T-Haltung senkrecht nach unten: die Hand landet unter der Schulter."""
        rig = self._rig()
        g = rig["gelenke"]
        ziel = {n: (x, y, 0.0) for n, (x, y) in g.items()}
        sx, sy = g["r_shoulder"]
        for n in ("r_elbow", "r_wrist"):
            ziel[n] = (sx, sy + (sx - g[n][0]), 0.0)
        unterarm = TF.rig_knochen(rig, ziel, 1.0, 1.0)[rig["knochen"].index(("r_elbow", "r_wrist"))]
        hx, hy = self._anwenden(unterarm, *g["r_hand"])
        self.assertLess(abs(hx - sx), 40)
        self.assertGreater(hy, sy + 300)

    def test_hand_haengt_locker_und_winkt_oben(self):
        """Hängt der Arm, zeigt die Hand nach unten und ist schmal (von der Seite); winkt er, bleibt sie oben."""
        von, nach = (0.0, 0.0), (0.0, 50.0)                               # Hand im Bild: 50 px nach unten
        haengt = TF.rig_hand(((100, 100), (100, 200)), von, nach, 1.0)   # Unterarm senkrecht nach unten
        a, b, c, d, e, f = haengt
        self.assertAlmostEqual(c * 50 + e, 100, places=6)                 # Fingerspitze genau unter dem Handgelenk
        self.assertGreater(d * 50 + f, 240)
        self.assertAlmostEqual(math.hypot(a, b), 1 - TF.RIG_HAND_SCHMAL, places=6)   # quer schmal
        oben = TF.rig_hand(((100, 200), (100, 100)), von, nach, 1.0)     # Unterarm nach oben (winken)
        a, b, c, d, e, f = oben
        self.assertAlmostEqual(c * 50 + e, 100, places=6)                 # die Hand knickt nicht ab
        self.assertLess(d * 50 + f, 60)
        self.assertAlmostEqual(math.hypot(a, b), 1.0, places=6)
        schraeg = TF.rig_hand(((100, 100), (200, 200)), von, nach, 1.0)  # schräg nach unten: gibt etwas nach
        a, b, c, d, e, f = schraeg
        winkel = math.degrees(math.atan2(d * 50, c * 50))
        self.assertGreater(winkel, 45)
        self.assertLess(winkel, 90)

    def test_fehlt_ein_gelenk(self):
        rig = self._rig()
        ziel = {n: (x, y, 0.0) for n, (x, y) in rig["gelenke"].items() if n != "l_knee"}
        self.assertIsNone(TF.rig_knochen(rig, ziel, 1.0, 1.0))

    def test_seitlich_bleibt_der_kopf_rund(self):
        rig = self._rig()
        ziel = {n: (x, y, 0.0) for n, (x, y) in rig["gelenke"].items()}
        abb = TF.rig_knochen(rig, ziel, 1.0, 0.0)
        kopf = abb[rig["knochen"].index(("neck", "head"))]
        rumpf = abb[rig["knochen"].index(("spine1", "spine2"))]
        self.assertGreater(abs(kopf[0]), abs(rumpf[0]))                 # quer: Kopf breiter als der Rumpf
        self.assertGreaterEqual(abs(kopf[0]), 0.8)

    def test_blick(self):
        vorn = [(0, 0, 0)] * 22
        namen = ["x"] * 22
        namen[16], namen[17] = "l_shoulder", "r_shoulder"
        vorn = list(vorn)
        vorn[16], vorn[17] = (0.2, 1.4, 0.0), (-0.2, 1.4, 0.0)
        self.assertAlmostEqual(TF.strich_blick(vorn, namen, 0.0), 1.0)
        hinten = list(vorn)
        hinten[16], hinten[17] = (-0.2, 1.4, 0.0), (0.2, 1.4, 0.0)
        self.assertAlmostEqual(TF.strich_blick(hinten, namen, 0.0), -1.0)
        seitlich = list(vorn)
        seitlich[16], seitlich[17] = (0.0, 1.4, -0.2), (0.0, 1.4, 0.2)
        self.assertAlmostEqual(TF.strich_blick(seitlich, namen, 0.0), 0.0)


NAMEN = ["pelvis", "l_hip", "r_hip", "spine1", "l_knee", "r_knee", "spine2", "l_ankle", "r_ankle", "spine3", "l_foot",
         "r_foot", "neck", "l_collar", "r_collar", "head", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist",
         "r_wrist"]
ELTERN = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]


def _stehend():
    """Gelenke im Fenster (x, y nach unten, Tiefe): stehend, Füße bei y = 500."""
    p = {"pelvis": (0, 260), "l_hip": (20, 265), "r_hip": (-20, 265), "spine1": (0, 240), "l_knee": (22, 380),
         "r_knee": (-22, 380), "spine2": (0, 215), "l_ankle": (22, 490), "r_ankle": (-22, 490), "spine3": (0, 190),
         "l_foot": (24, 500), "r_foot": (-24, 500), "neck": (0, 150), "l_collar": (15, 160), "r_collar": (-15, 160),
         "head": (0, 125), "l_shoulder": (40, 160), "r_shoulder": (-40, 160), "l_elbow": (45, 220),
         "r_elbow": (-45, 220), "l_wrist": (48, 275), "r_wrist": (-48, 275)}
    return [(float(p[n][0]), float(p[n][1]), 0.0) for n in NAMEN]


class Justierer(unittest.TestCase):
    def test_sauber_haelt_grenzen(self):
        w = figurmass.sauber({"groesse": 99, "arme": 0.1, "x": "0.5", "y": -9, "boese": 1, "kopf": float("nan")})
        self.assertEqual((w["groesse"], w["arme"], w["x"], w["y"], w["kopf"]), (24, 0.7, 0.5, -2, 1.0))
        self.assertNotIn("boese", w)
        self.assertEqual(figurmass.sauber("kaputt"), figurmass.VORGABE)

    def test_schreiben_und_lesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp) / "Figur" / figurmass.DATEI
            figurmass.schreiben(pfad, {"arme": 1.2, "groesse": 10})
            self.assertEqual(figurmass.lesen(pfad)["arme"], 1.2)
            self.assertEqual(figurmass.lesen(Path(tmp) / "gibtsnicht.json"), figurmass.VORGABE)
            with self.assertRaises(ValueError):
                figurmass.schreiben(Path(tmp) / "andere.json", {})

    def test_strecken(self):
        alt = _stehend()
        gleich = figurmass.strecken(alt, ELTERN, NAMEN, figurmass.VORGABE)
        self.assertEqual(gleich, alt)
        lang = figurmass.strecken(alt, ELTERN, NAMEN, dict(figurmass.VORGABE, arme=1.3))
        i, s = NAMEN.index("l_wrist"), NAMEN.index("l_shoulder")
        self.assertAlmostEqual(math.dist(lang[i][:2], lang[s][:2]), 1.3 * math.dist(alt[i][:2], alt[s][:2]), places=6)
        beine = figurmass.strecken(alt, ELTERN, NAMEN, dict(figurmass.VORGABE, beine=1.2))
        self.assertAlmostEqual(max(q[1] for q in beine), max(q[1] for q in alt))   # steht auf demselben Boden
        kopf = NAMEN.index("head")
        self.assertLess(beine[kopf][1], alt[kopf][1])                                # … und ist größer

    def test_befehl(self):
        kopf = base64.urlsafe_b64encode(json.dumps({"datei": "C:/x/Figur/justierung.json",
                                                    "werte": {"arme": 5}}).encode()).decode()
        name, wert = TF.figur_befehl("justierung=" + kopf)
        self.assertEqual((name, wert["datei"].name, wert["werte"]["arme"]), ("justierung", "justierung.json", 1.3))
        fremd = base64.urlsafe_b64encode(json.dumps({"datei": "C:/x/boese.exe"}).encode()).decode()
        self.assertIsNone(TF.figur_befehl("justierung=" + fremd)[1]["datei"])        # schreibt nur justierung.json

    def test_laengere_arme(self):
        """Mit längeren Armen landet die Hand weiter weg von der Schulter."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        t_haltung().save(tmp / "k.png")
        rig = TF.rig_lesen(FR.bauen(tmp / "k.png", tmp / "rig_vorne", "vorne"))
        ziel = {n: (x, y, 0.0) for n, (x, y) in rig["gelenke"].items()}
        namen = list(ziel)
        eltern = [namen.index(FR.ELTERN[n]) if n in FR.ELTERN else -1 for n in namen]
        werte = dict(figurmass.VORGABE, arme=1.25)
        lang = dict(zip(namen, figurmass.strecken([ziel[n] for n in namen], eltern, namen, werte)))
        unterarm = TF.rig_knochen(rig, lang, 1.0, 1.0, werte)[rig["knochen"].index(("r_elbow", "r_wrist"))]
        a, b, c, d, e, f = unterarm
        hx = a * rig["gelenke"]["r_hand"][0] + c * rig["gelenke"]["r_hand"][1] + e
        sx = rig["gelenke"]["r_shoulder"][0]
        self.assertGreater(sx - hx, 1.15 * (sx - rig["gelenke"]["r_hand"][0]))

    def test_werkzeug(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        gesendet = []
        with patch.object(figur, "ordner", lambda n: tmp / n / "Figur"), patch.object(figur, "_senden", gesendet.append):
            text = figur.justieren({"arme": 115, "links_rechts": 0, "hoch": 3}, "PersonA")
            self.assertIn("Arme 115 %", text)
            w = figurmass.lesen(tmp / "PersonA" / "Figur" / figurmass.DATEI)
            self.assertEqual((w["arme"], w["x"], w["y"]), (1.15, 0.0, 3))
            self.assertTrue(gesendet and gesendet[-1].startswith("justierung="))
            self.assertIn("Figur jetzt", figur.justieren({}, "PersonA"))             # ohne Felder: nur der Stand
            figur.justieren({"standard": True}, "PersonA")
            self.assertEqual(figurmass.lesen(tmp / "PersonA" / "Figur" / figurmass.DATEI), figurmass.VORGABE)
            with self.assertRaises(ValueError):
                figur.justieren({"arme": "lang"}, "PersonA")


class Gesichter(unittest.TestCase):
    """Köpfe je Ausdruck (tools/figurgesicht.py): zugeschnitten wie die Kopf-Röhre, nur gültig, wenn nicht älter als
    das Körper-Bild; das Fenster liest sie mit, die KI findet sie."""
    def setUp(self):
        import os
        import time
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        t_haltung().save(self.tmp / "koerper_vorne.png")
        alt = time.time() - 100
        os.utime(self.tmp / "koerper_vorne.png", (alt, alt))
        from PIL import ImageDraw
        for name in ("froh", "zwinkert"):                               # „Ausdruck“: ein Strich im Gesicht
            im = t_haltung()
            ImageDraw.Draw(im).line((470, 120, 530, 120), fill=(200, 0, 0, 255), width=4)
            im.save(self.tmp / figurgesicht.DATEI.format(name))

    def test_vorhanden_nur_wenn_nicht_aelter(self):
        import os
        self.assertEqual(set(figurgesicht.vorhanden(self.tmp)), {"froh", "zwinkert"})
        self.assertTrue(figurgesicht.fehlen(self.tmp))                    # nicht alle Ausdrücke da
        alt = (self.tmp / "koerper_vorne.png").stat().st_mtime - 50
        os.utime(self.tmp / figurgesicht.DATEI.format("froh"), (alt, alt))
        self.assertEqual(set(figurgesicht.vorhanden(self.tmp)), {"zwinkert"})   # älter als der Körper: veraltet

    def test_kopf_je_ausdruck_im_rig(self):
        import numpy as np
        from PIL import Image
        rig_pfad = FR.bauen(self.tmp / "koerper_vorne.png", self.tmp / "rig_vorne", "vorne")
        d = json.loads(rig_pfad.read_text(encoding="utf-8"))
        kopf = [s for s in d["stuecke"] if s.get("gesichter")]
        self.assertEqual(len(kopf), 1)
        self.assertEqual(kopf[0]["knochen"], FR.KNOCHEN.index(("neck", "head")))
        normal = np.asarray(Image.open(self.tmp / "rig_vorne" / kopf[0]["bild"]))
        froh = np.asarray(Image.open(self.tmp / "rig_vorne" / kopf[0]["gesichter"]["froh"]))
        self.assertEqual(normal.shape, froh.shape)                          # genau wie die Kopf-Röhre zugeschnitten
        self.assertTrue((normal != froh).any())
        rig = TF.rig_lesen(rig_pfad)
        self.assertEqual(set(next(s for s in rig["stuecke"] if s["gesichter"])["gesichter"]), {"froh", "zwinkert"})
        self.assertEqual(figur.rig_gesichter({"vorne": str(rig_pfad)}), ["froh", "zwinkert"])
        hinten = json.loads(FR.bauen(self.tmp / "koerper_vorne.png", self.tmp / "rig_hinten", "hinten")
                            .read_text(encoding="utf-8"))
        self.assertFalse(any(s.get("gesichter") for s in hinten["stuecke"]))   # von hinten kein Gesicht

    def test_fenster_lehnt_fremde_gesichter_ab(self):
        rig_pfad = FR.bauen(self.tmp / "koerper_vorne.png", self.tmp / "rig_vorne", "vorne")
        d = json.loads(rig_pfad.read_text(encoding="utf-8"))
        s = next(s for s in d["stuecke"] if s.get("gesichter"))
        s["gesichter"]["froh"] = "../boese.png"
        rig_pfad.write_text(json.dumps(d), encoding="utf-8")
        self.assertIsNone(TF.rig_lesen(rig_pfad))
        s["gesichter"] = {"Böse!": s["gesichter"]["zwinkert"]}
        rig_pfad.write_text(json.dumps(d), encoding="utf-8")
        self.assertIsNone(TF.rig_lesen(rig_pfad))

    def test_ersatz_gesichter(self):
        da = ["neutral", "froh", "ernst", "staunt", "schlaeft"]
        self.assertEqual(figur.gesicht_finden("denkt", da), "ernst")
        self.assertEqual(figur.gesicht_finden("neugierig", da), "staunt")
        self.assertEqual(figur.gesicht_finden("zwinkert", da), "froh")
        self.assertEqual(figur.gesicht_finden("schlaeft", da), "schlaeft")
        self.assertEqual(figur.BEWEGUNG_JE_POSE["schlafen"][2], "schlaeft")    # beim Dösen sind die Augen zu

    def test_kugel_malen_figur_holt_gesichter_nach(self):
        """Körper aktuell, Gesichter fehlen: /kugel malen figur rechnet nur sie und baut die Figur neu."""
        ordner = self.tmp / "PersonA" / "Figur"
        ordner.mkdir(parents=True)
        for a in KB.KOERPER:
            t_haltung(a == "vorne").save(ordner / f"koerper_{a}.png")
        (ordner / KB.KOERPER_STAND).write_text(json.dumps(KB.KOERPER_VERSION), encoding="utf-8")
        gerechnet = []

        def malen(o, on_status=None):
            gerechnet.append(o)
            for name in figurgesicht.AUSDRUECKE:
                t_haltung().save(o / figurgesicht.DATEI.format(name))
            return []
        with patch.object(KB, "motiv_ordner", lambda n: self.tmp / n),                 patch.object(KB, "figur", lambda n: {"beschreibung": "a person", "seed": 1, "kleidung": ""}),                 patch.object(KB, "veraltet", lambda n: False), patch.object(KB, "_mit_lora", lambda n, b: (b, {})),                 patch.object(KB, "_koerper_noetig", lambda: True), patch.object(KB, "_maler", lambda: None),                 patch.object(figurgesicht, "bereit", lambda: True), patch.object(figurgesicht, "malen", malen):
            self.assertEqual(KB.figur_fehlend("PersonA")[:1], ["gesichter"])
            for pose in figur.POSEN:                                       # die Posen sind schon gemalt
                t_haltung().save(ordner / f"{pose}.png")
            KB.figur_malen("PersonA")
            self.assertEqual(gerechnet, [ordner])
            self.assertEqual(KB.figur_fehlend("PersonA"), [])
        d = json.loads((ordner / "rig_vorne" / "rig.json").read_text(encoding="utf-8"))
        self.assertTrue(any(s.get("gesichter") for s in d["stuecke"]))


class Malen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for name, wert in (("motiv_ordner", lambda n: self.tmp / n),
                           ("figur", lambda n: {"beschreibung": "a person", "seed": 42, "kleidung": ""}),
                           ("veraltet", lambda n: False), ("_mit_lora", lambda n, b: (b, {})),
                           ("_koerper_noetig", lambda: True)):
            p = patch.object(KB, name, wert)
            p.start()
            self.addCleanup(p.stop)
        self.prompts = []
        self.papierkorb = []

        def malen(prompt, **k):
            self.prompts.append(prompt)
            ziel = self.tmp / f"roh{len(self.prompts)}.png"
            t_haltung("behind" not in prompt).save(ziel)
            return str(ziel)

        def freistellen(quelle, ziel, groesse=512, quadrat=True):
            Path(ziel).parent.mkdir(parents=True, exist_ok=True)
            __import__("shutil").copyfile(quelle, ziel)
            return Path(ziel)

        def sichern(f, *a, **k):
            self.papierkorb.append(Path(f).name)
            Path(f).unlink()
            return True
        for ziel, name, wert in ((KB, "_maler", lambda: malen), (KB, "freistellen", freistellen),
                                 (__import__("snapshot"), "sichern", sichern),
                                 (figurgesicht, "bereit", lambda: False)):    # kein echter Porträt-Motor im Test
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_figur_malen_baut_den_koerper(self):
        KB.figur_malen("PersonA")
        ordner = self.tmp / "PersonA" / "Figur"
        self.assertTrue(FR.aktuell(ordner / "rig_vorne" / "rig.json"))
        self.assertTrue(FR.aktuell(ordner / "rig_hinten" / "rig.json"))
        self.assertIn(KB.RAHMEN_KOERPER_SATZ, self.prompts[0])
        self.assertIn("T-pose", self.prompts[0])
        self.assertIn("behind", self.prompts[1])
        self.assertTrue(self.prompts[1].startswith(KB.KOERPER_VORSATZ["hinten"]))   # Rückansicht zuerst
        self.assertEqual(KB.figur_fehlend("PersonA"), [])
        self.assertEqual(json.loads((ordner / KB.KOERPER_STAND).read_text(encoding="utf-8")),
                         dict(KB.KOERPER_VERSION))

    def test_alte_koerper_werden_neu_gemalt(self):
        ordner = self.tmp / "PersonA" / "Figur"
        ordner.mkdir(parents=True)
        a_haltung().save(ordner / "koerper_vorne.png")                   # aus einer älteren Fassung, ohne Stand
        a_haltung(False).save(ordner / "koerper_hinten.png")
        self.assertEqual(KB.koerper_fehlend(ordner), ["koerper_vorne", "koerper_hinten"])
        KB.figur_malen("PersonA")
        self.assertEqual(self.papierkorb[:2], ["koerper_vorne.png", "koerper_hinten.png"])
        self.assertEqual(KB.koerper_fehlend(ordner), [])
        self.assertTrue(FR.aktuell(ordner / "rig_vorne" / "rig.json"))

    def test_nur_das_rueckbild_ist_alt(self):
        ordner = self.tmp / "PersonA" / "Figur"
        ordner.mkdir(parents=True)
        t_haltung().save(ordner / "koerper_vorne.png")
        t_haltung(False).save(ordner / "koerper_hinten.png")
        (ordner / KB.KOERPER_STAND).write_text(json.dumps({"vorne": KB.KOERPER_VERSION["vorne"], "hinten": 2}),
                                               encoding="utf-8")
        self.assertEqual(KB.koerper_fehlend(ordner), ["koerper_hinten"])
        KB.figur_malen("PersonA")
        self.assertEqual(self.papierkorb[:1], ["koerper_hinten.png"])
        self.assertTrue(self.prompts[0].startswith(KB.KOERPER_VORSATZ["hinten"]))

    def test_alte_rigs_werden_neu_gebaut(self):
        KB.figur_malen("PersonA")
        rig = self.tmp / "PersonA" / "Figur" / "rig_vorne" / "rig.json"
        d = json.loads(rig.read_text(encoding="utf-8"))
        d["version"] = 1
        rig.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(len(KB.koerper_bauen(rig.parent.parent)), 1)
        self.assertTrue(FR.aktuell(rig))

    def test_outfit_bekommt_eigenen_koerper(self):
        KB.outfit_malen("PersonA", "winter", "a warm winter coat", [])
        ordner = KB.outfit_ordner("PersonA", "winter") / "Figur"
        self.assertTrue((ordner / "rig_vorne" / "rig.json").is_file())
        self.assertTrue(all("winter coat" in p for p in self.prompts))
        with patch.object(KB, "outfits", lambda n: {"aktiv": "winter", "liste": {"winter": "a warm winter coat"}}):
            self.assertIn("Outfits", figur.rig_pfade("PersonA")["vorne"])

    def test_neue_figur_wirft_alte_koerper_weg(self):
        KB.figur_malen("PersonA")
        KB._motive_weg("PersonA")
        self.assertFalse((self.tmp / "PersonA" / "Figur" / "rig_vorne").exists())
        self.assertFalse((self.tmp / "PersonA" / "Figur" / "koerper_vorne.png").exists())


class Melden(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.alt = dict(figur._STAND)
        self.addCleanup(lambda: (figur._STAND.clear(), figur._STAND.update(self.alt)))
        self.gesendet = []
        for ziel, name, wert in ((startseite, "STEUERN", self.gesendet.append),
                                 (startseite, "echtes_fenster", lambda: True),
                                 (figur, "bewegung_anfordern", lambda *a, **k: None),
                                 (figur, "_strich_bereit", lambda: True),
                                 (figur, "gesichter", lambda name: {}),
                                 (KB, "motiv_ordner", lambda n: self.tmp / n)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        figur._STAND.update(pose=None, posen=None, herz=False, antwort="", art="")

    def test_rig_wird_gemeldet(self):
        ordner = self.tmp / "PersonA" / "Figur"
        ordner.mkdir(parents=True)
        t_haltung().save(ordner / "koerper_vorne.png")
        FR.bauen(ordner / "koerper_vorne.png", ordner / "rig_vorne", "vorne")
        figur.melden("PersonA")
        rig = [s for s in self.gesendet if s.startswith("rig=")]
        self.assertEqual(len(rig), 1)
        self.assertIn("rig_vorne", base64.urlsafe_b64decode(rig[0][4:]).decode())

    def test_alte_form_wird_aus_dem_bild_neu_gebaut(self):
        ordner = self.tmp / "PersonA" / "Figur"
        (ordner / "rig_vorne").mkdir(parents=True)
        t_haltung().save(ordner / "koerper_vorne.png")
        (ordner / "rig_vorne" / "rig.json").write_text(json.dumps({"version": 1, "teile": []}), encoding="utf-8")
        self.assertIn("vorne", figur.rig_pfade("PersonA"))
        self.assertTrue(FR.aktuell(ordner / "rig_vorne" / "rig.json"))

    def test_ohne_rig_leer(self):
        figur.melden("PersonB")
        self.assertIn("rig=", self.gesendet)


if __name__ == "__main__":
    unittest.main()
