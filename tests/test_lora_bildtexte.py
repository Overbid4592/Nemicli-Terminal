"""Krea-LoRA-Training mit einem Satz je Bild und Formaten statt Beschneiden: Formatwahl, Bildtexte vom Seh-Modell
(gespeichert, ein zweiter Lauf liest sie nur), die Trainingsschleife mit Mini-Teilen auf der CPU, Werkstatt-Start."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import krea as K                  # noqa: E402
import lora as L                  # noqa: E402
import lora_training as LT        # noqa: E402
import lorabild as LB             # noqa: E402
import paths                      # noqa: E402
import profilordner               # noqa: E402


def _png(pfad: Path, w: int, h: int) -> Path:
    Image.new("RGB", (w, h), (120, 90, 60)).save(pfad)
    return pfad


class _Daten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (mock.patch.object(paths, "DATEN", self.tmp),
                  mock.patch.object(profilordner, "aktiv", lambda: "PersonA")):
            p.start()
            self.addCleanup(p.stop)


class Formate(_Daten):
    def test_naechstes_seitenverhaeltnis(self):
        self.assertEqual((640, 896), LT.krea_format(_png(self.tmp / "h.png", 600, 900)))
        self.assertEqual((896, 640), LT.krea_format(_png(self.tmp / "q.png", 1600, 900)))
        self.assertEqual((768, 768), LT.krea_format(_png(self.tmp / "s.png", 500, 520)))
        self.assertEqual((640, 896), LT.krea_format(self.tmp / "fehlt.png"))

    def test_gleich_viele_bildpunkte(self):
        flaechen = [w * h for w, h in LT.KREA_FORMATE.values()]
        self.assertLess(max(flaechen) / min(flaechen), 1.03)        # Speicher wie bei KREA_GROESSE

    def test_lernsatz(self):
        self.assertEqual("A photo of figur_a, an adult. Wearing a red coat on a pier.",
                         LT.lernsatz("A photo of figur_a, an adult.", " Wearing a red coat on a pier. "))
        self.assertEqual("A photo of figur_a.", LT.lernsatz("A photo of figur_a.", ""))


class Bildtexte(_Daten):
    def daten(self):
        d = LB.ordner("figur_a", anlegen=True) / "daten"
        d.mkdir(parents=True)
        return [_png(d / f"00{i}_abc{i}def.png", 64, 90) for i in range(1, 4)]

    def test_seh_modell_schreibt_einmal(self):
        daten = self.daten()
        frage = mock.Mock(return_value=' "A woman in a red coat\n on a pier." ')
        beschreiber = mock.Mock(finden=mock.Mock(return_value=("m", "mm")), fragen_roh=frage)
        with mock.patch.dict(sys.modules, {"bildbeschreiber": beschreiber}):
            texte = LB.bildtexte("figur_a", daten)
            self.assertEqual(["A woman in a red coat on a pier."] * 3, texte)
            self.assertEqual(3, frage.call_count)
            self.assertIn("Do not describe the person's face", frage.call_args.args[1])
            beschreiber.entladen.assert_called_once()
            (LB.ordner("figur_a") / "texte" / "abc2def.txt").write_text("Sitting in a cafe.", encoding="utf-8")
            texte = LB.bildtexte("figur_a", daten)                    # von Hand geändert: gilt
        self.assertEqual("Sitting in a cafe.", texte[1])
        self.assertEqual(3, frage.call_count)                          # nichts neu gefragt

    def test_ohne_seh_modell(self):
        daten = self.daten()
        beschreiber = mock.Mock(finden=mock.Mock(return_value=None))
        with mock.patch.dict(sys.modules, {"bildbeschreiber": beschreiber}):
            self.assertIsNone(LB.bildtexte("figur_a", daten))
            for p in daten:
                f = LB._bildtext_datei("figur_a", p)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("On a beach.", encoding="utf-8")
            self.assertEqual(["On a beach."] * 3, LB.bildtexte("figur_a", daten))   # vorhandene Texte reichen

    def test_ein_fehler_laesst_den_satz_leer(self):
        daten = self.daten()
        beschreiber = mock.Mock(finden=mock.Mock(return_value=("m", "mm")),
                                fragen_roh=mock.Mock(side_effect=[RuntimeError("x"), "A.", "B."]))
        with mock.patch.dict(sys.modules, {"bildbeschreiber": beschreiber}):
            self.assertEqual(["", "A.", "B."], LB.bildtexte("figur_a", daten))


class _MiniDiT:
    """Steht für das DiT: text() reicht den Kontext durch, der Aufruf merkt sich, welcher Text kam."""

    def __init__(self, w):
        self.w, self.blocks, self.gesehen = w, [], []

    def text(self, ctx):
        return ctx

    def __call__(self, x, sigma, txt):
        self.gesehen.append((tuple(x.shape[-2:]), float(txt.flatten()[0])))
        return x * self.w


class Schleife(_Daten):
    def setUp(self):
        super().setUp()
        self.w = torch.nn.Parameter(torch.ones(1))
        self.dit = _MiniDiT(self.w)
        self.saetze, self.metas = [], []

        def encode(satz, te, device):
            self.saetze.append(satz)
            return torch.full((1, 2, 4), float(len(self.saetze)))

        def speichern(pfad, schichten, meta, opt=None):
            self.metas.append(dict(meta))

        for ziel, attr, wert in (
                (K, "missing_reason", lambda: None), (K, "discover", lambda: {"m": Path("m.safetensors")}),
                (K, "teile", lambda: {"text": Path("t"), "vae": Path("v")}),
                (K, "_geraet", lambda: (torch.device("cpu"), torch.float32)),
                (K, "entladen", lambda: None), (K, "_frei", lambda: None),
                (K, "_VAEEncoder", lambda *a: (lambda x: torch.zeros(1, 16, x.shape[2] // 8, x.shape[3] // 8))),
                (K, "_Qwen3Text", lambda *a: object()), (K, "_encode", encode),
                (K, "_DiT", lambda *a: self.dit), (K, "lora_anbringen", lambda *a: {}),
                (L, "parameter", lambda s: [self.w]), (L, "speichern", speichern)):
            p = mock.patch.object(ziel, attr, wert)
            p.start()
            self.addCleanup(p.stop)
        self.bilder = [_png(self.tmp / "a.png", 60, 90), _png(self.tmp / "b.png", 90, 60),
                       _png(self.tmp / "c.png", 70, 70), _png(self.tmp / "d.png", 60, 90)]

    def test_satz_je_bild_und_format_je_bild(self):
        texte = ["In a red coat.", "On a beach.", "In a cafe.", "At night."]
        LT.trainieren(None, bilder=self.bilder, schritte=12, rang=4, motor="bild", ziel=self.tmp / "f.safetensors",
                      text="A photo of figur_a, an adult.", ausloeser="figur_a", texte=texte)
        self.assertEqual([LT.lernsatz("A photo of figur_a, an adult.", t) for t in texte], self.saetze)
        meta = self.metas[-1]
        self.assertEqual(meta["texte_kennung"], LT._texte_kennung(texte))
        self.assertEqual("640x896 768x768 896x640", meta["groesse"])
        form = {(112, 80): {1.0, 4.0}, (80, 112): {2.0}, (96, 96): {3.0}}   # Latent-Größe -> Satz-Nummer
        for groesse, satz in self.dit.gesehen:
            self.assertIn(satz, form[groesse])                         # jedes Bild lernt mit seinem Satz
        self.assertGreater(len({s for _, s in self.dit.gesehen}), 1)

    def test_ohne_texte_ein_satz_fuer_alle(self):
        LT.trainieren(None, bilder=self.bilder, schritte=6, rang=4, motor="bild", ziel=self.tmp / "f.safetensors",
                      text="A photo of figur_a, an adult.")
        self.assertEqual(["A photo of figur_a, an adult."], self.saetze)
        self.assertNotIn("texte_kennung", self.metas[-1])
        self.assertEqual({1.0}, {s for _, s in self.dit.gesehen})

    def test_falsche_zahl_texte(self):
        with self.assertRaises(RuntimeError):
            LT.trainieren(None, bilder=self.bilder, schritte=2, motor="bild", ziel=self.tmp / "f.safetensors",
                          text="x", texte=["nur einer"])


class Werkstatt(unittest.TestCase):
    def test_start_holt_die_bildtexte(self):
        code = (ROOT / "main_dienste.py").read_text(encoding="utf-8")
        teil = code[code.index("def _training(a: dict)"):code.index("def starten(a: dict)")]
        self.assertIn("texte = lorabild.bildtexte(name, daten, melden)", teil)
        self.assertIn("texte=texte)", teil)


if __name__ == "__main__":
    unittest.main()
