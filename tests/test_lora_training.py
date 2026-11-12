"""LoRA-Training (engines/lora_training.py) und Werkzeuge ohne Modelldateien: Bildauswahl, Stand, eine kurze
Trainingsschleife mit Mini-DiT auf der CPU (inkl. Anhalten + Fortsetzen), Einmischen im Video-Motor, Cloud-Sperre."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import torch

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools", "tests"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import lora as L                  # noqa: E402
import lora_training as LT        # noqa: E402
import paths                      # noqa: E402
import profilordner               # noqa: E402
import video as V                 # noqa: E402
from test_video import dit_zufall  # noqa: E402

NAME = "test_user"


class Grundlage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (mock.patch.object(paths, "DATEN", Path(self._tmp.name)),
                  mock.patch.object(profilordner, "aktiv", return_value=NAME)):
            p.start()
            self.addCleanup(p.stop)

    def _bilder(self, namen):
        ordner = profilordner.ordner("Bilder", NAME)
        for i, n in enumerate(namen):
            (ordner / n).write_bytes(b"x")
            t = time.time() - 100 + i
            os.utime(ordner / n, (t, t))
        return ordner


class Auswahl(Grundlage):
    def test_gesicht_bevorzugt_und_neueste_zuerst(self):
        self._bilder(["a.png", "a_gesicht.png", "b.png", "c.png"])
        wahl = [p.name for p in LT.bilder_auswaehlen(NAME)]
        self.assertEqual(wahl, ["c.png", "b.png", "a_gesicht.png"])
        self.assertEqual(len(LT.bilder_auswaehlen(NAME, hoechstens=2)), 2)

    def test_gesichtszuege_ordner(self):
        ordner = self._bilder(["a.png", "b.png"])
        (ordner / "Gesichtszuege").mkdir()
        (ordner / "Gesichtszuege" / "a.png").write_bytes(b"x")
        wahl = LT.bilder_auswaehlen(NAME)
        self.assertEqual([(p.parent.name, p.name) for p in wahl], [("Bilder", "b.png"), ("Gesichtszuege", "a.png")])

    def test_ohne_ordner_leer(self):
        self.assertEqual(LT.bilder_auswaehlen("niemand"), [])

    def test_beschreibung_aus_charakter(self):
        stub = mock.Mock()
        stub.laden.return_value = {"kern": "long red hair, green eyes"}
        with mock.patch.dict(sys.modules, {"charakter": stub}):
            self.assertEqual(LT.beschreibung(NAME), "test_user. long red hair, green eyes")
        stub.laden.return_value = {}
        with mock.patch.dict(sys.modules, {"charakter": stub}):
            self.assertEqual(LT.beschreibung(NAME), "A photo of test_user.")

    def test_zu_wenige_bilder(self):
        with self.assertRaises(RuntimeError):
            LT.trainieren(NAME, bilder=[Path("x.png")] * 3)
        self.assertFalse(LT.laeuft())


class Schleife(Grundlage):
    """Mini-DiT statt Wan 5B: Laden, Text und VAE werden ersetzt, die Schleife selbst läuft echt."""

    def setUp(self):
        super().setUp()
        torch.manual_seed(3)
        self.sd, self.cfg = dit_zufall()
        latents = [torch.randn(48, 1, 4, 4) for _ in range(4)]
        ctx = torch.randn(5, 24)
        t = {"name": "mini", "dit": Path("mini.safetensors"), "gguf": False}
        for modul, attr, wert in ((V, "teile", lambda *_: t), (V, "missing_reason", lambda *_: None),
                                  (V, "dit_config", lambda *_: self.cfg), (V, "entladen", lambda: None),
                                  (V, "_geraet", lambda: (torch.device("cpu"), torch.float32)),
                                  (V, "_lade_safetensors", lambda *_: {k: v.clone() for k, v in self.sd.items()}),
                                  (LT, "daten", lambda *_a, **_k: (latents, ctx))):
            p = mock.patch.object(modul, attr, wert)
            p.start()
            self.addCleanup(p.stop)
        self.bilder = [Path(f"b{i}.png") for i in range(4)]

    def test_trainieren_speichert_je_profil(self):
        meldungen = []
        pfad = LT.trainieren(NAME, bilder=self.bilder, schritte=6, rang=4, lr=1e-2, text="test_user, red hair",
                             on_status=meldungen.append)
        self.assertEqual(pfad, profilordner.lora_pfad(LT.ZIEL, NAME))
        lora, meta = L.laden(pfad)
        self.assertEqual(len(lora), 20)
        self.assertEqual((meta["rang"], meta["schritt"], meta["beschreibung"]), ("4", "6", "test_user, red hair"))
        self.assertTrue(any(A.abs().sum() > 0 for A, B, _ in lora.values()))
        self.assertTrue(any(B.abs().sum() > 0 for A, B, _ in lora.values()))   # B startet bei 0 -> hat gelernt
        self.assertTrue(any("Schritt 6/6" in m for m in meldungen))
        self.assertFalse(LT._stand_pfad(NAME).exists())
        self.assertFalse(LT.laeuft())

    def test_anhalten_und_fortsetzen(self):
        zaehler = {"n": 0}

        def abbruch():
            zaehler["n"] += 1
            return zaehler["n"] > 2

        with mock.patch.dict(LT.VORGABEN, {"zwischenstand": 1}):
            with self.assertRaises(RuntimeError):
                LT.trainieren(NAME, bilder=self.bilder, schritte=5, rang=4, abbruch=abbruch)
            stand = LT.stand(NAME)
            self.assertEqual(stand["zwischenstand"]["schritt"], "2")
            self.assertIsNone(stand["lora"])
            meldungen = []
            LT.trainieren(NAME, bilder=self.bilder, schritte=5, rang=4, on_status=meldungen.append)
        schritte = [m for m in meldungen if "Schritt" in m]
        self.assertTrue(schritte[0].startswith("LoRA: Schritt 3/5"), schritte[0])    # setzt fort
        self.assertEqual(LT.stand(NAME)["lora"]["schritt"], "5")


class FortsetzenWieOhnePause(Schleife):
    """Zwischenstand mit AdamW-Momenten und Zufall je Schritt: Pause + weiter = genau wie durchgehend."""

    def _lauf(self, abbruch=None):
        torch.manual_seed(11)                                    # gleiche Anfangswerte von A
        return LT.trainieren(NAME, bilder=self.bilder, schritte=5, rang=4, lr=1e-2, text="test_user",
                             abbruch=abbruch)

    def test_gleiche_gewichte(self):
        durchgehend = {k: (a.clone(), b.clone()) for k, (a, b, _) in L.laden(self._lauf())[0].items()}
        profilordner.lora_pfad(LT.ZIEL, NAME).unlink()
        zaehler = {"n": 0}

        def abbruch():
            zaehler["n"] += 1
            return zaehler["n"] > 2
        with self.assertRaises(RuntimeError):
            self._lauf(abbruch)
        _, meta = L.laden(LT._stand_pfad(NAME))
        self.assertEqual(meta["schritt"], "2")
        self.assertEqual(meta["adam_schritt"], "2")                # Optimizer-Zustand ist mitgesichert
        mit_pause = L.laden(self._lauf())[0]
        for k, (a, b) in durchgehend.items():
            self.assertTrue(torch.allclose(a, mit_pause[k][0], atol=1e-6), k)
            self.assertTrue(torch.allclose(b, mit_pause[k][1], atol=1e-6), k)

    def test_anderer_text_beginnt_neu(self):
        meldungen = []
        with self.assertRaises(RuntimeError):
            LT.trainieren(NAME, bilder=self.bilder, schritte=5, rang=4, abbruch=lambda: True)
        LT.trainieren(NAME, bilder=self.bilder, schritte=5, rang=4, text="ganz anders", on_status=meldungen.append)
        self.assertTrue(any("passt nicht" in m for m in meldungen))
        self.assertTrue(any(m.startswith("LoRA: Schritt 1/5") for m in meldungen))


class VideoMotor(Grundlage):
    def test_lora_der_persoenlichkeit(self):
        pfad = profilordner.lora_pfad("wan22-5b")
        self.assertIsNone(V._lora_fuer(None))
        pfad.write_bytes(b"x")
        self.assertEqual(V._lora_fuer(None), pfad)
        with mock.patch.object(V, "_cfg", lambda k, d=None: False if k == "video_lora" else d):
            self.assertIsNone(V._lora_fuer(None))
        with self.assertRaises(RuntimeError):
            V._lora_fuer(Path(self._tmp.name) / "fehlt.safetensors")

    def test_einmischen_einzeln_wie_am_stueck(self):
        torch.manual_seed(2)
        sd, _ = dit_zufall()
        schichten = L.anbringen({k: v.clone() for k, v in sd.items()}, rang=4, alpha=4, device="cpu",
                                dtype=torch.float32)
        for s in schichten.values():
            s.B.data.normal_(0, 0.1)
        pfad = Path(self._tmp.name) / "l.safetensors"
        L.speichern(pfad, schichten, {})
        am_stueck = {k: v.clone() for k, v in sd.items()}
        L.einmischen(am_stueck, L.laden(pfad)[0], 0.8, torch.float32)
        V._als_fp8(am_stueck, torch.float32)
        einzeln = {k: v.clone() for k, v in sd.items()}
        self.assertEqual(V._lora_einmischen(einzeln, pfad, 0.8, torch.float32, fp8=True), (20, ""))
        for k, v in am_stueck.items():
            if isinstance(v, V._F8):
                self.assertIsInstance(einzeln[k], V._F8)
                self.assertTrue(torch.equal(v.w8.float(), einzeln[k].w8.float()), k)

    def test_unpassende_lora_bleibt_draussen(self):
        torch.manual_seed(2)
        sd, _ = dit_zufall()
        schichten = L.anbringen({k: v.clone() for k, v in sd.items()}, rang=4, alpha=4, device="cpu",
                                dtype=torch.float32)
        name = next(iter(schichten))
        schichten[name].A = torch.nn.Parameter(torch.zeros(4, schichten[name].A.shape[1] + 1))   # falsche Form
        pfad = Path(self._tmp.name) / "falsch.safetensors"
        L.speichern(pfad, schichten, {})
        vorher = {k: v.clone() for k, v in sd.items()}
        n, grund = V._lora_einmischen(sd, pfad, 1.0, torch.float32, fp8=False)
        self.assertEqual(n, 0)
        self.assertIn("Form", grund)
        self.assertTrue(all(torch.equal(sd[k], vorher[k]) for k in vorher))         # Modell unverändert
        schichten = L.anbringen({k: v.clone() for k, v in sd.items()}, rang=4, alpha=4, device="cpu",
                                dtype=torch.float32)
        L.speichern(pfad, schichten, {"familie": "wan21-t2v"})                          # fremde Bauart
        n, grund = V._lora_einmischen(sd, pfad, 1.0, torch.float32, False, "wan22-ti2v")
        self.assertEqual(n, 0)
        self.assertIn("wan21-t2v", grund)
        L.speichern(pfad, schichten, {"familie": "wan22-fun-inp"})                      # verwandt: wirkt
        n, _ = V._lora_einmischen(sd, pfad, 1.0, torch.float32, False, "wan22-ti2v")
        self.assertGreater(n, 0)

    def test_dichte_matrizen_werden_fp8(self):
        torch.manual_seed(1)
        sd = {"blocks.0.self_attn.q.weight": torch.randn(32, 32), "blocks.0.norm3.weight": torch.ones(32),
              "head.head.weight": torch.randn(32, 32)}
        self.assertEqual(V._als_fp8(sd, torch.float32), 1)
        self.assertIsInstance(sd["blocks.0.self_attn.q.weight"], V._F8)
        self.assertIsInstance(sd["head.head.weight"], torch.Tensor)


class Werkzeuge(Grundlage):
    def test_nur_cloud(self):
        import actions
        import debugbericht as D
        with mock.patch.object(D, "cloud_modell", return_value=False):
            res = actions._lora_trainieren({})
        self.assertFalse(res.ok)
        self.assertIn("Cloud", res.text)
        self.assertTrue(actions.ACTIONS["lora_trainieren"].get("hintergrund"))
        self.assertIn("lokal", res.text)                                      # trainiert wird lokal
        self.assertTrue(actions.needs_confirm({"tool": "lora_trainieren"}))    # Nutzer gibt frei
        self.assertTrue(actions.needs_confirm({"tool": "coding_lora_trainieren"}))

    def test_stand_und_anhalten(self):
        import actions
        self._bilder(["a.png"])
        text = actions._lora({})
        self.assertIn("Fertige LoRA: keine", text)
        self.assertIn("a.png", text)
        self.assertIn("kein LoRA-Training", actions._lora({"aktion": "anhalten"}))

    def test_hinweis_nur_cloud(self):
        import debugbericht as D
        import persona
        with mock.patch.object(D, "cloud_modell", return_value=False):
            self.assertEqual(persona._lora_hinweis(), "")
        with mock.patch.object(D, "cloud_modell", return_value=True):
            self.assertIn("lora_trainieren", persona._lora_hinweis())


if __name__ == "__main__":
    unittest.main()
