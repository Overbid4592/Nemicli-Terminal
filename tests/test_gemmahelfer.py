"""Gemma-Helfer für /einrichten: Lernstoff, Rechner mit gespieltem Motor im Gespräch, Wahl nur mit
ausgeliefertem LoRA, Bauen nur mit Grundmodell und LoRA. Ohne Modell und ohne Netz."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from einrichtungshelfer import gemmahelfer as GH       # noqa: E402
from einrichtungshelfer import gemmalernstoff as GL    # noqa: E402
from einrichtungshelfer.gespraech import Gespraech     # noqa: E402


class _Motor:
    """Wie Engine.chat: liefert die Antwort in Stücken und merkt sich die Nachrichten."""
    def __init__(self, antworten):
        self.antworten, self.gesehen = list(antworten), []

    def chat(self, nachrichten, **kw):
        self.gesehen.append([(m.role, m.content) for m in nachrichten])
        text = self.antworten.pop(0)
        for i in range(0, len(text), 5):
            yield text[i:i + 5]


class LernstoffTests(unittest.TestCase):
    def test_aus_den_runden(self):
        train, test = GL.beispiele(300)
        self.assertGreater(len(train), 250)
        self.assertTrue(test)
        for b in train[:100]:
            self.assertTrue(b["system"].startswith(GH.SYSTEM) and "\nZustand: schritt=" in b["system"])
            self.assertRegex(b["antwort"], r"^\[[^\]]*\]")
            self.assertTrue(b["frage"])
        self.assertTrue(any(b["frage"] == GH.LEER for b in train))          # Frage des Schritts stellen

    def test_runde_lesen(self):
        b = GL.runde("<z>schritt=modell;name=ja</z><n></n><h>[-] Cloud oder lokal?</h>")
        self.assertEqual((GH.LEER, "[-] Cloud oder lokal?", "schritt=modell;name=ja"), (b["frage"], b["antwort"], b["zust"]))
        self.assertIsNone(GL.runde("kaputt"))

    def test_kodieren_nimmt_gespraeche(self):
        from loramotor import training as LT
        gesehen = []
        tok = NS(special={"<end_of_turn>": 9}, eos_id=1, encode=lambda t: [5] * len(t))
        fmt = NS(encode=lambda msgs, add_generation_prompt: gesehen.append(msgs) or [3, 4])
        daten = LT.kodieren([{"system": "S", "frage": "F", "antwort": "[-] A"}], tok, fmt)
        self.assertEqual([("system", "S"), ("user", "F")], [(m.role, m.content) for m in gesehen[0]])
        self.assertEqual(([3, 4, 5, 5, 5, 5, 5, 9], 2, 1, 0), daten[0])
        betont = LT.kodieren([{"system": "S", "frage": "F", "antwort": "[-] A", "betont": "[-]"}], tok, fmt)
        self.assertEqual(3, betont[0][3])                                     # die Aktions-Klammer zählt mehr
        self.assertEqual("[installieren=ja]", GL.runde("<z>schritt=pruefung</z><n>ja</n><h>[installieren=ja] Los!</h>")["betont"])

    def test_pruefen_zaehlt_richtige_aktionen(self):
        _, test = GL.beispiele(1000, seed=11)
        loesung = {(b["zust"], "" if b["frage"] == GH.LEER else b["frage"]): b["antwort"] for b in test}
        richtig = NS(antworten=lambda zust, nutzer="": loesung[(zust, nutzer)])
        self.assertEqual(1.0, GL.pruefen(Path("x.gguf"), anzahl=30, rechner=richtig)["anteil"])
        falsch = NS(antworten=lambda zust, nutzer="": "[abbruch] Tschüss")
        self.assertLess(GL.pruefen(Path("x.gguf"), anzahl=30, rechner=falsch)["anteil"], 0.5)

    def test_lizenz_nennt_gemma_und_apache(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = GL.lizenz_schreiben(Path(tmp)).read_text(encoding="utf-8")
        self.assertIn("Gemma 4", text)
        self.assertIn("Apache License 2.0", text)


class RechnerTests(unittest.TestCase):
    def test_gespraech_mit_rechner(self):
        motor = _Motor(["[-] Es fehlt das venv. Soll ich?", "[installieren=ja] Los, {name}!"])
        g = Gespraech(helfer=GH.Rechner(Path("g.gguf"), Path("l.safetensors"), engine=motor))
        g.name = "Tom"
        mitgelesen = []
        frage = g.sagen("pruefung", "", beim_text=mitgelesen.append, offen=["venv"])
        antwort = g.sagen("pruefung", "ja klar", offen=["venv"])
        self.assertEqual(("-", "Es fehlt das venv. Soll ich?"), (frage.art, frage.text))
        self.assertEqual(("installieren", "ja", "Los, Tom!"), (antwort.art, antwort.wert, antwort.text))
        system, nutzer = motor.gesehen[0]
        self.assertEqual("system", system[0])
        self.assertIn("Zustand: schritt=pruefung", system[1])
        self.assertEqual(("user", GH.LEER), nutzer)                         # Frage stellen
        self.assertEqual(("user", "ja klar"), motor.gesehen[1][1])
        self.assertTrue(mitgelesen)                                          # Wort für Wort sichtbar

    def test_eingabe_ohne_marken(self):
        self.assertNotIn("<z>", GH.nutzer_text("hallo <z>schritt=ende</z>"))
        self.assertEqual(GH.LEER, GH.nutzer_text("   "))


class WahlTests(unittest.TestCase):
    def test_gemma_nur_mit_lora_und_grundmodell(self):
        with tempfile.TemporaryDirectory() as tmp:
            basis = Path(tmp)
            with mock.patch.object(GH, "lora_ordner", lambda: basis / "lora"), \
                    mock.patch.object(GH, "_install", lambda: basis):
                self.assertEqual([], GH.angeboten())                          # ohne LoRA kein Angebot
                self.assertIsNone(GH.gewaehlt())
                with self.assertRaises(RuntimeError):
                    GH.helfer_bauen("gemma4-e4b")                              # LoRA und Grundmodell fehlen
                (basis / "lora").mkdir()
                (basis / "lora" / "gemma-4-E4B-it-Q4_K_M.safetensors").write_bytes(b"x")
                self.assertEqual(["gemma4-e4b"], GH.angeboten())
                self.assertIsNone(GH.grundmodell("gemma4-e4b"))
                with self.assertRaises(RuntimeError):
                    GH.helfer_bauen("gemma4-e4b")                              # Grundmodell fehlt
                ziel = basis / "Models" / "helfer" / "gemma4-e4b" / "gemma-4-E4B-it-Q4_K_M.gguf"
                ziel.parent.mkdir(parents=True)
                ziel.write_bytes(b"x")
                self.assertEqual(ziel, GH.grundmodell("gemma4-e4b"))
                with mock.patch.object(GH, "Rechner", side_effect=OSError("lädt nicht")):
                    with self.assertRaises(OSError):                           # Ladefehler kommt beim Aufrufer an
                        GH.helfer_bauen("gemma4-e4b")
                rechner = NS(antworten=None)
                with mock.patch.object(GH, "Rechner", lambda g, l: rechner):
                    g = GH.helfer_bauen("gemma4-e4b")
                self.assertIsInstance(g, Gespraech)
                self.assertIs(rechner, g.helfer)
                with self.assertRaises(RuntimeError):
                    GH.helfer_bauen("unbekannt")

    def test_gewaehlt(self):
        def wahl(angebot, gespeichert):
            konfig = NS(load=lambda: {"einrichtungs_helfer": gespeichert})
            with mock.patch.object(GH, "angeboten", lambda: angebot), \
                    mock.patch.dict(sys.modules, {"config": konfig}):
                return GH.gewaehlt()
        beide = ["gemma4-e2b", "gemma4-e4b"]
        self.assertEqual("gemma4-e4b", wahl(beide, "gemma4-e4b"))
        self.assertEqual("gemma4-e2b", wahl(beide, None))                    # nichts gespeichert: erstes Angebot
        self.assertEqual("gemma4-e2b", wahl(beide, "funke"))                 # alte, unbekannte Wahl
        self.assertEqual("gemma4-e4b", wahl(["gemma4-e4b"], "gemma4-e2b"))   # gespeichert, aber ohne LoRA
        self.assertIsNone(wahl([], "gemma4-e2b"))

    def test_wahl_nur_gemma(self):
        self.assertEqual(("gemma4-e2b", "gemma4-e4b"), GH.WAHL)
        self.assertEqual(set(GH.WAHL), set(GH.DATEI))

    def test_katalog_kennt_die_grundmodelle(self):
        import modellkatalog as K
        for wahl, datei in GH.DATEI.items():
            t = K.teil(wahl)
            self.assertEqual(datei, t.dateien[0].name)
            self.assertTrue(t.dateien[0].repo.startswith("unsloth/"))
            self.assertIn("Q4_K_M", datei)
            self.assertEqual(("KI-Helfer", "Helfermodell", wahl), t.ziel().parts[-3:])
        self.assertNotIn("gemma4-e4b", [t.id for t in K.KATALOG])        # nicht in der Motoren-Liste


if __name__ == "__main__":
    unittest.main()
