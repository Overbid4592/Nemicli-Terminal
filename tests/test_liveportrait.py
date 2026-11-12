"""Porträt-Motor (engines/liveportrait): Netze, Gewichte, Zuschnitt, Ablauf – ohne echte Modelldateien.

Gleichheit mit dem Original-Quelltext (Zufallsgewichte, Abweichung 0,0) wurde einmalig außerhalb
der Testreihe geprüft; hier stehen die Teile, die ohne den fremden Quelltext prüfbar sind.
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for d in ("core", "engines", "tools", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

from liveportrait import gesicht, gewichte, motor, netze  # noqa: E402


def _zufall(netz):
    torch.manual_seed(1)
    with torch.no_grad():
        for name, p in netz.named_parameters():
            p.normal_(0, 0.03 if "weight" in name else 0.01)
        for name, b in netz.named_buffers():
            if "running_var" in name:
                b.uniform_(0.8, 1.2)
    return netz.eval()


def _gesicht_punkte(cx=200.0, cy=220.0, s=60.0) -> np.ndarray:
    """203 Punkte mit Augen (0–42) und Mund (48–102) an plausibler Stelle."""
    pts = np.tile([cx, cy], (203, 1)).astype(float)
    for basis, dx in ((0, -1), (24, 1)):                        # linkes/rechtes Auge: 24 Punkte im Kreis
        for i in range(24):
            a = i / 24 * 2 * np.pi
            pts[basis + i] = [cx + dx * 0.45 * s + 0.2 * s * np.cos(a), cy - 0.3 * s + 0.08 * s * np.sin(a)]
    for i in range(48, 103):                                    # 48 und 66 liegen sich gegenüber
        a = (i - 48) / 36 * 2 * np.pi
        pts[i] = [cx + 0.35 * s * np.cos(a), cy + 0.5 * s + 0.12 * s * np.sin(a)]
    return pts


class Netze(unittest.TestCase):
    def test_namen_wie_im_original(self):
        n = netze.bauen()
        erwartet = {
            "F": ["first.conv.weight", "down_blocks.1.norm.running_var", "second.weight",
                  "resblocks_3d.3dr5.conv2.weight"],
            "M": ["detector.downsample_layers.0.0.weight", "detector.downsample_layers.0.1.weight",
                  "detector.stages.2.8.grn.gamma", "detector.fc_pitch.weight", "detector.norm.bias"],
            "W": ["dense_motion_network.hourglass.encoder.down_blocks.4.conv.weight",
                  "dense_motion_network.hourglass.decoder.conv.weight", "dense_motion_network.mask.weight",
                  "dense_motion_network.compress.weight", "dense_motion_network.occlusion.weight",
                  "third.conv.weight", "fourth.weight"],
            "G": ["fc.weight", "G_middle_5.norm_1.mlp_beta.weight", "up_0.conv_s.weight",
                  "up_1.norm_s.mlp_shared.0.weight", "conv_img.0.weight"],
            "eye": ["mlp.0.weight", "mlp.10.bias"],
        }
        for teil, namen in erwartet.items():
            sd = n[teil].state_dict()
            for name in namen:
                self.assertIn(name, sd, f"{teil}: {name}")
        self.assertEqual(tuple(n["G"].state_dict()["conv_img.0.weight"].shape), (12, 64, 3, 3))
        self.assertEqual(tuple(n["M"].state_dict()["detector.fc_kp.weight"].shape), (63, 768))

    def test_formen(self):
        n = {k: _zufall(v) for k, v in netze.bauen().items()}
        with torch.no_grad():
            bild = torch.rand(1, 3, 256, 256)
            f = n["F"](bild)
            self.assertEqual(tuple(f.shape), (1, 32, 16, 64, 64))
            self.assertEqual(tuple(n["M"](bild)["pitch"].shape), (1, 66))
            kp = torch.rand(1, 21, 3) * 0.4 - 0.2
            w = n["W"](f, kp, kp)
            self.assertEqual(tuple(w.shape), (1, 256, 64, 64))
            self.assertEqual(tuple(n["G"](w).shape), (1, 3, 512, 512))

    def test_spektral_einrechnen_wie_torch(self):
        torch.manual_seed(3)
        conv = torch.nn.utils.spectral_norm(torch.nn.Conv2d(4, 6, 3, padding=1))
        conv(torch.rand(1, 4, 8, 8))                          # einmal im Training: u, v gesetzt
        conv.eval()
        x = torch.rand(1, 4, 8, 8)
        erwartet = conv(x)
        sd = gewichte.spektral_einrechnen({f"c.{k}": v for k, v in conv.state_dict().items()})
        eigen = torch.nn.Conv2d(4, 6, 3, padding=1)
        eigen.load_state_dict({"weight": sd["c.weight"], "bias": sd["c.bias"]})
        self.assertLess(float((eigen(x) - erwartet).abs().max().detach()), 1e-6)


class Gewichte(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        n = netze.bauen()
        (self.tmp / "base").mkdir()
        for teil in "FMWG":
            torch.save(n[teil].state_dict(), self.tmp / "base" / (gewichte.DATEIEN[teil] + ".pth"))
        torch.save({t: {"module." + k: v for k, v in n[name].state_dict().items()}
                    for t, name in gewichte.S_TEILE.items()},
                   self.tmp / (gewichte.DATEIEN["S"] + ".pth"))
        self.summen = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.tmp.rglob("*.pth")}

    def test_ohne_passende_pruefsumme_nichts(self):
        with self.assertRaises(RuntimeError):
            gewichte.umwandeln(self.tmp)
        self.assertEqual(list(self.tmp.rglob("*.safetensors")), [])

    def test_umwandeln_dann_nur_safetensors(self):
        with patch.dict(gewichte.SHA256, self.summen):
            neu = gewichte.umwandeln(self.tmp)
        self.assertEqual(len(neu), 5)
        for p in self.tmp.rglob("*.pth"):
            p.unlink()                                          # ab jetzt kein Pickle mehr nötig
        sds = gewichte.laden(self.tmp)
        self.assertIn("eye.mlp.0.weight", sds["S"])
        self.assertIn("stitching.mlp.6.bias", sds["S"])
        n = netze.bauen()
        n["W"].load_state_dict(sds["W"], strict=True)
        self.assertEqual(gewichte.fehlt(self.tmp), [gewichte.LANDMARK])

    def test_ordner_name_egal(self):
        models = self.tmp.parent / (self.tmp.name + "_models")
        (models / "Irgendwie").mkdir(parents=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(models, ignore_errors=True))
        (models / "Irgendwie" / "appearance_feature_extractor.pth").write_bytes(b"x")
        with patch.object(gewichte, "ORDNER", [models / "LivePortrait"]):
            self.assertEqual(gewichte.ordner(), models / "Irgendwie")

    def test_landmark_nur_mit_pruefsumme(self):
        (self.tmp / gewichte.LANDMARK).write_bytes(b"onnx")
        with self.assertRaises(RuntimeError):
            gewichte.landmark_pfad(self.tmp)
        with patch.dict(gewichte.SHA256, {gewichte.LANDMARK: hashlib.sha256(b"onnx").hexdigest()}):
            self.assertEqual(gewichte.landmark_pfad(self.tmp).name, gewichte.LANDMARK)


class Zuschnitt(unittest.TestCase):
    def test_verziehen_wie_warpaffine(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("OpenCV nur zum Vergleich")
        rng = np.random.default_rng(0)
        bild = rng.integers(0, 255, (120, 90, 3), dtype=np.uint8)
        M = gesicht.matrix((40, 70), 80, 0.3, 64)
        cv = cv2.warpAffine(bild, M[:2], (64, 64), flags=cv2.INTER_LINEAR).astype(float)
        eigen = (gesicht.verziehen(gesicht.als_tensor(bild), M, 64, 64)[0].permute(1, 2, 0).numpy() * 255)
        self.assertLess(np.abs(cv - eigen)[2:-2, 2:-2].mean(), 1.0)

    def test_rechteck_und_matrix(self):
        pts = _gesicht_punkte()
        mitte, groesse, winkel = gesicht.rechteck(pts, 2.3, vy=-0.125)
        self.assertAlmostEqual(winkel, 0.0, places=5)
        self.assertLess(mitte[1], pts.mean(0)[1] + 1)          # vy < 0: Ausschnitt rutscht Richtung Stirn
        M = gesicht.matrix(mitte, groesse, winkel, 512)
        self.assertTrue(np.allclose(gesicht.punkte_abbilden(np.array([mitte]), M), [[256, 256]]))
        gedreht = pts.copy()
        c, s = np.cos(0.2), np.sin(0.2)
        gedreht = (gedreht - 200) @ np.array([[c, -s], [s, c]]).T + 200
        self.assertAlmostEqual(abs(gesicht.rechteck(gedreht, 1.5)[2]), 0.2, places=3)

    def test_verhaeltnisse_und_maske(self):
        pts = _gesicht_punkte()
        links, rechts = gesicht.augen_offen(pts)
        self.assertAlmostEqual(links, rechts, places=5)
        self.assertGreater(gesicht.mund_offen(pts), 0)
        m = gesicht.maske(64)
        self.assertEqual(float(m[0, 0, 32, 32]), 1.0)
        self.assertEqual(float(m[0, 0, 0, 0]), 0.0)


class Ablauf(unittest.TestCase):
    def test_werte_halten_blenden_und_grenzen(self):
        w = motor.verlauf([{"t": 0, "laecheln": 0}, {"t": 1, "laecheln": 1, "drehen": 99},
                           {"t": 2, "nicken": 5}], 3, 10, 0.3)
        self.assertEqual(len(w), 30)
        self.assertEqual(w[0]["augen"], 0.3)                    # wie im Bild
        self.assertEqual(w[10]["drehen"], 25)                   # begrenzt wie der Regler
        self.assertEqual(w[15]["laecheln"], 1)                  # bleibt, solange nichts Neues kommt
        self.assertTrue(0 < w[5]["laecheln"] < 1)               # weich dazwischen
        self.assertTrue(all(abs(b["laecheln"] - a["laecheln"]) < 0.2 for a, b in zip(w, w[1:])))

    def test_mund_folgt_der_stimme(self):
        import numpy as np
        rate = 24000
        still, laut = np.zeros(rate // 2, np.float32), (0.5 * np.sin(np.arange(rate // 2) / 5)).astype(np.float32)
        mund = motor.mundwerte(np.concatenate([still, laut, still]), rate)
        self.assertEqual(len(mund), 38)                             # 1,5 s bei 25 Bildern je Sekunde
        self.assertEqual(max(mund[:12]), 0.0)                       # Stille: Mund zu
        self.assertGreater(max(mund[12:25]), motor.MUND_MAX * 0.8)  # Laut: Mund auf
        self.assertLess(mund[-1], 5.0)                              # danach wieder zu
        self.assertTrue(all(0 <= m <= motor.MUND_MAX for m in mund))

    def test_fertige_ablaeufe(self):
        for name in motor.ABLAEUFE:
            w = motor.verlauf(motor.ablauf(name, 4), 4, 25, 0.35)
            self.assertEqual(len(w), 100)
            for v in w:
                for k, (lo, hi) in motor.GRENZEN.items():
                    self.assertTrue(lo - 1e-9 <= v[k] <= hi + 1e-9, (name, k, v[k]))
        blinz = motor.verlauf(motor.ablauf("lebendig", 6), 6, 25, 0.35)
        self.assertTrue(any(v["augen"] < 0.05 for v in blinz))  # blinzelt


class _Punkte:
    def __init__(self, *_):
        pass


class Motor(unittest.TestCase):
    def setUp(self):
        n = {k: _zufall(v) for k, v in netze.bauen().items()}
        sds = {t: n[t].state_dict() for t in "FMWG"}
        sds["S"] = {f"{name}.{k}": v for name in netze.NAEHTE for k, v in n[name].state_dict().items()}
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        from PIL import Image
        rng = np.random.default_rng(1)
        self.bild = self.tmp / "portrait.png"
        Image.fromarray(rng.integers(0, 255, (360, 300, 3), dtype=np.uint8)).save(self.bild)
        self.vorher = self.bild.read_bytes()
        self.patches = [patch.object(gewichte, "laden", lambda basis=None: sds),
                        patch.object(gewichte, "landmark_pfad", lambda basis=None: Path("x.onnx")),
                        patch.object(gewichte, "fehlt", lambda basis=None: []),
                        patch.object(gesicht, "Gesichtspunkte", _Punkte),
                        patch.object(gesicht, "finden", lambda bild, gp, kasten=None: _gesicht_punkte(150, 170, 70))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        motor.entladen()
        self.addCleanup(motor.entladen)

    def test_neutral_bleibt_das_bild_und_ganzes_video(self):
        m = motor.motor("cpu")
        q = m.vorbereiten(self.bild)
        self.assertEqual(tuple(q.bild.shape), (1, 3, 360, 300))
        neutral = dict(motor.NEUTRAL, augen=sum(q.augen) / 2)
        w = {k: torch.tensor([float(neutral[k])]) for k in motor.NEUTRAL}
        delta = motor.ausdruck_verschieben(q.ausdruck, w)
        self.assertTrue(torch.equal(delta, q.ausdruck))         # neutrale Werte verschieben nichts
        b = m.bilder(q, [neutral, dict(neutral, laecheln=1.0, augen=0.0, drehen=10)])
        self.assertEqual(tuple(b.shape), (2, 3, 360, 300))
        rand = (q.maske[0, 0] < 1e-6)
        self.assertLess(float((b[:, :, rand] - q.bild[:, :, rand]).abs().max()), 1e-5)   # außen unberührt
        geschrieben = {}
        def schreiben(bilder, pfad, fps, ton=None):
            bilder = list(bilder)                                    # eine Folge, kein fertiger Block
            geschrieben.update(n=len(bilder), pfad=pfad, form=(len(bilder),) + bilder[0].shape)
        with patch("videodatei.schreiben", schreiben):
            erg = motor.animieren(self.bild, "blinzeln", sekunden=1, fps=10, ziel=self.tmp / "v.mp4", geraet="cpu")
        self.assertEqual(geschrieben["n"], 10)
        self.assertEqual(geschrieben["form"][1:], (360, 300, 3))
        self.assertEqual(erg["bilder"], 10)
        from PIL import Image
        with Image.open(erg["kugel"]) as k:
            self.assertEqual(k.size, (motor.KUGEL_KANTE, motor.KUGEL_KANTE))  # Gesicht, quadratisch
        self.assertTrue(Path(erg["vorschau"]).is_file())
        self.assertTrue(erg["kugel"].endswith("v_kugel.webp"))
        self.assertEqual(self.bild.read_bytes(), self.vorher)   # Original unverändert

    def test_grafikkarte_belegt(self):
        import hintergrund
        with patch.object(hintergrund, "gpu_belegt", lambda: {"nr": 3, "titel": "🎬 Video"}):
            with self.assertRaises(RuntimeError) as f:
                motor.animieren(self.bild, "blinzeln", sekunden=1, geraet="cuda")
        self.assertIn("#3", str(f.exception))


if __name__ == "__main__":
    unittest.main()


class Werkzeug(unittest.TestCase):
    def setUp(self):
        import actions
        self.A = actions
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.bild = self.tmp / "ich.png"
        self.bild.write_bytes(b"x")
        self.aufrufe = []

        def animieren(bild, ablauf, sekunden=4, on_status=None, mund=None, ton=None, text="", frei=False):
            self.aufrufe.append((bild, ablauf, sekunden))
            self.gesprochen = (mund, ton, text)
            self.frei = frei
            if ton:
                sekunden = len(ton[0]) / ton[1]
            erg = {"pfad": str(self.tmp / "v.mp4"), "vorschau": str(self.tmp / "v.webp"),
                   "kugel": str(self.tmp / ("v_frei_kugel.webp" if frei else "v_kugel.webp")),
                   "bilder": int(sekunden * 25), "sekunden_rechnen": 3.0, "geraet": "cuda"}
            if ton:
                erg["ton"] = str(self.tmp / "v.wav")
            return erg
        self.fenster, self.kugel, self.kugel_ton = [], [], []
        import vorschau_fenster
        from wache import steuerung
        for ziel, name, wert in ((motor, "fehlt", lambda: None), (motor, "animieren", animieren),
                                 (motor, "entladen", lambda: None),
                                 (actions.os, "startfile", lambda p: None),
                                 (vorschau_fenster, "starten", lambda p: self.fenster.append(p)),
                                 (steuerung, "steuern", lambda a, wer="": (self.kugel.append(a["bewegt"]),
                                                                          self.kugel_ton.append(a.get("ton")),
                                                                          "Kugel: bewegtes Porträt.")[2])):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_eingebunden(self):
        import hintergrund
        self.assertFalse(self.A.ACTIONS["portraet_bewegen"]["confirm"])
        self.assertIn("portraet_bewegen", hintergrund.GPU_WERKZEUGE)
        self.assertIn('"portraet_bewegen"', main_quelle.text())
        self.assertIn("- portraet_bewegen ", (ROOT / "core" / "persona.py").read_text(encoding="utf-8"))
        self.assertIn('"liveportrait"', (ROOT / "NemiCLI.spec").read_text(encoding="utf-8"))

    def test_ablauf_und_grenzen(self):
        text = self.A._portraet_bewegen({"bild": str(self.bild), "ablauf": "hallo", "sekunden": 99})
        self.assertIn("Porträt bewegt", text)
        self.assertEqual(self.aufrufe[-1], (str(self.bild), "hallo", motor.MAX_SEKUNDEN))
        self.assertFalse(self.A._portraet_bewegen({"bild": str(self.bild), "ablauf": "tanzen"}).ok)
        self.assertFalse(self.A._portraet_bewegen({}).ok)
        self.assertFalse(self.A._portraet_bewegen({"bild": str(self.tmp / "fehlt.png")}).ok)
        eigen = [{"t": 0.5, "laecheln": 1}]
        self.A._portraet_bewegen({"bild": str(self.bild), "schluessel": eigen})
        self.assertEqual(self.aufrufe[-1][1], eigen)
        self.assertFalse(self.A._portraet_bewegen({"bild": str(self.bild), "schluessel": "lächeln"}).ok)

    def test_bild_der_kugel(self):
        from wache import steuerung
        with patch.object(steuerung, "kugelbild", lambda name: self.bild):
            self.A._portraet_bewegen({"bild": "kugel", "zeigen": "kugel"})
        self.assertEqual(self.aufrufe[-1][0], str(self.bild))
        self.assertTrue(self.frei)
        with patch.object(steuerung, "kugelbild", lambda name: None):
            res = self.A._portraet_bewegen({"bild": "Kugel"})
        self.assertFalse(res.ok)
        self.assertIn("/kugel malen", res.text)

    def test_lieblingsbild_und_grenze(self):
        import tagebuch
        with patch.object(tagebuch, "lieblinge", lambda: [{"name": "Mein Strandbild", "pfad": str(self.bild)}]):
            self.A._portraet_bewegen({"bild": "mein strandbild"})
        self.assertEqual(self.aufrufe[-1][0], str(self.bild))
        with patch.object(self.A, "_fremde_persoenlichkeit_meldung", lambda p: "Fehler: gehört einer anderen"):
            res = self.A._portraet_bewegen({"bild": str(self.bild)})
        self.assertFalse(res.ok)
        self.assertIn("anderen", res.text)


    def test_sprechen(self):
        import numpy as np
        stimme = SimpleNamespace(
            modus=lambda: "knopf", verfuegbar=lambda: True, ABTASTRATE=24000, freigeben=lambda: None,
            saetze=lambda t: ["Hallo.", "Wie geht es dir?"],
            erzeugen=lambda s: (0.4 * np.sin(np.arange(12000) / 10)).astype(np.float32))
        gestartet = []
        with patch.object(self.A, "_stimme", lambda: stimme), \
             patch.object(self.A.os, "startfile", lambda p: gestartet.append(p)):
            text = self.A._portraet_bewegen({"bild": str(self.bild), "sagen": "Hallo. Wie geht es dir?"})
        self.assertIn("spricht", text)
        self.assertIn("Video-Player", text)                         # mit Ton im Terminal: Player
        mund, ton, gesagt = self.gesprochen
        self.assertIsNone(mund)                                      # Lippen formt jetzt der Motor aus dem Ton
        self.assertEqual(ton[1], 24000)
        self.assertEqual(gesagt, "Hallo. Wie geht es dir?")          # Text verrät Fragen (Brauen)
        self.assertFalse(self.frei)
        self.assertEqual(gestartet, [str(self.tmp / "v.mp4")])
        with patch.object(self.A, "_stimme", lambda: stimme):        # in der Kugel: freigestellt und mit Ton
            text = self.A._portraet_bewegen({"bild": str(self.bild), "sagen": "Hallo", "zeigen": "kugel"})
        self.assertIn("Freigestellt", text)
        self.assertEqual((self.kugel[-1], self.kugel_ton[-1]), (str(self.tmp / "v_frei_kugel.webp"),
                                                                str(self.tmp / "v.wav")))
        stimme.modus = lambda: "aus"                                 # „aus“ bleibt aus – der Clip entsteht stumm
        stimme.erzeugen = lambda s: self.fail("bei ausgeschalteter Stimme wird nichts gesprochen")
        with patch.object(self.A, "_stimme", lambda: stimme):
            text = self.A._portraet_bewegen({"bild": str(self.bild), "sagen": "Hallo"})
        self.assertIn("bewegt", text)
        self.assertIn("ohne Ton", text)
        self.assertEqual((None, None), self.gesprochen[:2])          # ohne Mund und ohne Ton gerechnet

    def test_zeigen(self):
        self.assertIn("kleinen Fenster", self.A._portraet_bewegen({"bild": str(self.bild)}))
        self.assertEqual(self.fenster, [str(self.tmp / "v.webp")])
        self.assertIn("Kugel", self.A._portraet_bewegen({"bild": str(self.bild), "zeigen": "kugel"}))
        self.assertEqual(self.kugel, [str(self.tmp / "v_frei_kugel.webp")])     # Kugel: Vorgabe freigestellt
        self.A._portraet_bewegen({"bild": str(self.bild), "zeigen": "kugel", "frei": False})
        self.assertEqual(self.kugel[-1], str(self.tmp / "v_kugel.webp"))
        self.kugel.pop()
        self.A._portraet_bewegen({"bild": str(self.bild), "zeigen": "beides"})
        self.assertEqual((len(self.fenster), len(self.kugel)), (2, 2))
        self.assertFalse(self.A._portraet_bewegen({"bild": str(self.bild), "zeigen": "leinwand"}).ok)
        import modes
        with patch.object(modes, "_wache_dienst", True):          # im Kugel-Prozess: in der Kugel
            self.A._portraet_bewegen({"bild": str(self.bild)})
        self.assertEqual(len(self.kugel), 3)


class Vorschau(unittest.TestCase):
    def test_webp_animiert_und_verkleinert(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        bilder = np.stack([np.full((600, 400, 3), i * 20, dtype=np.uint8) for i in range(10)])
        p = motor.webp_schreiben(bilder, tmp / "a.webp", 25, 300)
        with Image.open(p) as w:
            self.assertEqual(w.n_frames, 10)
            self.assertEqual(w.info["loop"], 0)                    # Endlosschleife
            self.assertEqual(w.size, (200, 300))

    def test_fenster_befehl_und_schutz(self):
        import vorschau_fenster
        b = vorschau_fenster.befehl("x.webp")
        self.assertEqual(b[-2:], ["--vorschau", "x.webp"])
        self.assertEqual(vorschau_fenster.zeigen("gibt_es_nicht.webp"), 2)
        self.assertIn('if "--vorschau" in sys.argv:', main_quelle.text())

    def test_fenster_schliesst_auf_signal(self):
        import time as _t
        from PIL import Image
        from PySide6.QtWidgets import QApplication
        import actions
        import modes
        import plan
        import vorschau_fenster
        app = QApplication.instance() or QApplication([])
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        folge = [Image.new("RGB", (40, 60), (i * 60, 0, 0)) for i in range(3)]
        folge[0].save(tmp / "a.webp", save_all=True, append_images=folge[1:], duration=40, loop=0)
        with patch.object(vorschau_fenster, "_signal", lambda: tmp / "signal.txt"):
            alt = vorschau_fenster.fenster_bauen(tmp / "a.webp")
            alt.show()
            _t.sleep(0.05)
            self.assertIn("geschlossen", actions._vorschau_schliessen({}))   # wie die KI es aufruft
            neu = vorschau_fenster.fenster_bauen(tmp / "a.webp")             # danach geöffnet: bleibt
            neu.show()
            ende = _t.time() + 3
            while alt.isVisible() and _t.time() < ende:
                app.processEvents()
                _t.sleep(0.02)
            self.assertFalse(alt.isVisible())
            self.assertTrue(neu.isVisible())
            neu.close()
        self.assertIn("vorschau_schliessen", modes.MEMORY_TOOLS)        # läuft auch im Chat-Modus
        self.assertIn("vorschau_schliessen", modes.WACHE_URTEIL_TOOLS)  # und im Kugel-Fenster
        self.assertIn("vorschau_schliessen", plan.BEGLEITEN)
        self.assertFalse(actions.ACTIONS["vorschau_schliessen"]["confirm"])

    def test_lange_clips_kleine_vorschau(self):
        self.assertEqual(motor.MAX_SEKUNDEN, 60)
        n = 60 * motor.FPS
        jedes = max(1, -(-n // motor.VORSCHAU_BILDER))
        self.assertLessEqual(len(range(0, n, jedes)), motor.VORSCHAU_BILDER)

    def test_kugel_runden(self):
        import profilordner
        from wache import steuerung
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        videos = tmp / "Profile" / "Ada" / "Videos"
        videos.mkdir(parents=True)
        (videos / "a_kugel.webp").write_bytes(b"x")
        geschrieben = {}
        with patch.object(profilordner, "wurzel", lambda: tmp / "Profile"),                 patch.object(steuerung, "lesen", lambda: {}),                 patch.object(steuerung, "schreiben", lambda z: geschrieben.update(z)):
            self.assertIn("4×", steuerung.steuern({"bewegt": str(videos / "a_kugel.webp"), "runden": 4}, wer="Ada"))
            self.assertEqual(geschrieben["bewegt_runden"], 4)
            steuerung.steuern({"bewegt": str(videos / "a_kugel.webp"), "runden": 99}, wer="Ada")
            self.assertEqual(geschrieben["bewegt_runden"], steuerung.RUNDEN_MAX)

    def test_kugel_nur_eigene_videos(self):
        import profilordner
        from wache import steuerung
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        videos = tmp / "Profile" / "Ada" / "Videos"
        videos.mkdir(parents=True)
        (videos / "a_kugel.webp").write_bytes(b"x")
        (tmp / "b_kugel.webp").write_bytes(b"x")
        with patch.object(profilordner, "wurzel", lambda: tmp / "Profile"):
            self.assertIsNone(steuerung.bewegt_pruefen(str(videos / "a_kugel.webp"), "Ada"))
            self.assertIsNotNone(steuerung.bewegt_pruefen(str(tmp / "b_kugel.webp"), "Ada"))
            self.assertIsNotNone(steuerung.bewegt_pruefen(str(videos / "a_kugel.webp"), "Bea"))

    def test_kugel_spielt_und_kehrt_zurueck(self):
        import time as _t
        from PIL import Image
        from PySide6.QtWidgets import QApplication
        from wache import kugel as K
        app = QApplication.instance() or QApplication([])
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        folge = [Image.new("RGB", (32, 32), (i * 60, 0, 0)) for i in range(3)]
        folge[0].save(tmp / "f_kugel.webp", save_all=True, append_images=folge[1:], duration=20, loop=0)
        f = K._Kugelfenster()
        fertig = []
        f.film_starten(tmp / "f_kugel.webp", lambda: fertig.append(1), runden=1)
        ende = _t.time() + 5
        while not fertig and _t.time() < ende:
            app.processEvents()
            _t.sleep(0.01)
        self.assertEqual(fertig, [1])
        self.assertFalse(f._rund)
        self.assertIsNone(f._film)
