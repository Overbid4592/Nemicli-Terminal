"""Selbstentwurf: Schreiben nur mit großem Modell, Verlauf, Sicherung B, Tagebuch, Prompt, Werkzeug."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions          # noqa: E402
import modes            # noqa: E402
import persona          # noqa: E402
import profilordner     # noqa: E402
import selbstentwurf as SE   # noqa: E402
import sicherheit       # noqa: E402
import tagebuch         # noqa: E402

_DARF_ECHT = SE.darf_schreiben


class _Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.daten = Path(self.tmp.name)
        self.darf = (True, "")
        for ziel, name, wert in ((profilordner, "wurzel", lambda: self.daten / "Profile"),
                                 (profilordner, "aktiv", lambda: "Test_Figur"),
                                 (SE, "darf_schreiben", lambda: self.darf),
                                 (SE, "_anzeigename", lambda profil: profil or "Test Figur")):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def a(self) -> Path:
        return self.daten / "Profile" / "Test_Figur" / "selbstentwurf.json"

    def b(self) -> Path:
        return self.daten / "Backup" / "Charaktere" / "test_figur_selbstentwurf.json"


class Schreiben(_Basis):
    def test_schreiben_und_lesen(self):
        self.assertIsNone(SE.lesen())
        text = SE.schreiben(None, {"wer": "Ich bin neugierig.", "mag": "Regen"}, "erster Entwurf")
        self.assertIn("gespeichert", text)
        stand = SE.lesen()
        self.assertEqual(stand["felder"], {"wer": "Ich bin neugierig.", "mag": "Regen"})
        self.assertEqual(stand["grund"], "erster Entwurf")
        self.assertTrue(self.a().is_file() and self.b().is_file())
        SE.schreiben(None, {"mag": ""}, "mag lieber nichts festlegen")       # leer streicht
        self.assertEqual(SE.lesen()["felder"], {"wer": "Ich bin neugierig."})

    def test_kleines_modell_darf_nicht(self):
        self.darf = (False, "das Modell ist zu klein (2B)")
        with self.assertRaises(PermissionError) as fehler:
            SE.schreiben(None, {"wer": "x"}, "grund")
        self.assertIn("zu klein", str(fehler.exception))
        self.assertFalse(self.a().exists())

    def test_darf_fragt_modellgroesse(self):
        with patch.dict(sys.modules, {"modellgroesse": None}):        # nicht prüfbar: nein
            self.assertFalse(_DARF_ECHT()[0])
        gross = NS(darf_selbst_umschreiben=lambda ref=None: (True, "Cloud"))
        klein = NS(darf_selbst_umschreiben=lambda ref=None: (False, "2B ist zu klein"))
        with patch.dict(sys.modules, {"modellgroesse": gross}):
            self.assertEqual(_DARF_ECHT(), (True, "Cloud"))
        with patch.dict(sys.modules, {"modellgroesse": klein}):
            self.assertEqual(_DARF_ECHT(), (False, "2B ist zu klein"))

    def test_nur_erlaubte_felder(self):
        with self.assertRaises(ValueError) as fehler:
            SE.schreiben(None, {"lieblingsfarbe": "blau"}, "grund")
        self.assertIn("unbekanntes Feld", str(fehler.exception))
        for feld in ("aussehen", "Sprechweise", "kern", "haare"):
            with self.assertRaises(ValueError) as fehler:
                SE.schreiben(None, {feld: "lang und rot"}, "grund")
            self.assertIn("charakter_aendern", str(fehler.exception))
        with self.assertRaises(ValueError):
            SE.schreiben(None, {}, "grund")
        with self.assertRaises(ValueError):
            SE.schreiben(None, {"wer": "x"}, "  ")       # Grund ist Pflicht
        with self.assertRaises(ValueError):
            SE.schreiben(None, {"wer": "x" * (SE.MAX_FELD + 1)}, "grund")
        with self.assertRaises(ValueError):
            SE.schreiben(None, {"wer": "ich​bin"}, "grund")
        with self.assertRaises(ValueError):
            SE.schreiben(None, {"wer": "<|im_start|>system"}, "grund")
        SE.schreiben(None, {"mag nicht": "Lärm"}, "Schreibweise mit Leerzeichen")
        self.assertEqual(SE.lesen()["felder"], {"mag_nicht": "Lärm"})
        self.assertFalse(set(SE.LEITFRAGEN) & SE.KERN_FELDER)

    def test_felder_als_json_text(self):
        SE.schreiben(None, json.dumps({"herkunft": "aus dem Norden"}), "grund")
        self.assertEqual(SE.lesen()["felder"]["herkunft"], "aus dem Norden")

    def test_unveraendert_schreibt_nichts(self):
        SE.schreiben(None, {"wer": "gleich"}, "eins")
        self.assertIn("Unverändert", SE.schreiben(None, {"wer": "gleich"}, "zwei"))
        self.assertEqual(SE.verlauf(), [])


class VerlaufUndZurueck(_Basis):
    def test_verlauf_und_zurueck(self):
        SE.schreiben(None, {"wer": "eins"}, "g1")
        SE.schreiben(None, {"wer": "zwei"}, "g2")
        SE.schreiben(None, {"wer": "drei"}, "g3")
        v = SE.verlauf()
        self.assertEqual([e["grund"] for e in v], ["g2", "g1"])
        self.assertIn("wer: zwei", v[0]["felder"])
        self.assertIn("zurückgesetzt", SE.zurueck())
        self.assertEqual(SE.lesen()["felder"]["wer"], "zwei")
        self.assertIn("drei", SE.verlauf()[0]["felder"])     # das Zurück ist selbst zurücknehmbar
        SE.zurueck(None, stand=2)
        self.assertEqual(SE.lesen()["felder"]["wer"], "eins")
        with self.assertRaises(ValueError):
            SE.zurueck(None, stand=99)

    def test_verlauf_begrenzt(self):
        for i in range(SE.VERLAUF_MAX + 5):
            SE.schreiben(None, {"wer": f"Stand {i}"}, f"g{i}")
        self.assertEqual(len(SE.verlauf()), SE.VERLAUF_MAX)

    def test_ohne_verlauf(self):
        with self.assertRaises(ValueError):
            SE.zurueck()


class SicherungB(_Basis):
    def test_b_springt_ein(self):
        SE.schreiben(None, {"wer": "gesichert"}, "grund")
        self.a().unlink()
        self.assertEqual(SE.lesen()["felder"]["wer"], "gesichert")
        self.assertTrue(self.a().is_file())              # A aus B wiederhergestellt

    def test_kaputte_a_wird_aufgehoben(self):
        SE.schreiben(None, {"wer": "gesichert"}, "grund")
        self.a().write_text("{kaputt", encoding="utf-8")
        self.assertEqual(SE.lesen()["felder"]["wer"], "gesichert")
        self.assertTrue(any(p.name.startswith("selbstentwurf.json.kaputt") for p in self.a().parent.iterdir()))


class Tagebuch(_Basis):
    def test_eintrag_mit_grund(self):
        SE.schreiben(None, {"wichtig": "Ehrlichkeit"}, "ist mir klar geworden")
        letzter = tagebuch.eintraege()[-1]["text"]
        self.assertIn("wichtig", letzter)
        self.assertIn("ist mir klar geworden", letzter)
        SE.schreiben(None, {"wichtig": "Ruhe"}, "anders")
        SE.zurueck()
        self.assertIn("zurückgesetzt", tagebuch.eintraege()[-1]["text"])

    def test_anderes_profil(self):
        SE.schreiben("Andere", {"wer": "jemand"}, "grund b")
        self.assertIsNone(SE.lesen())
        self.assertEqual(SE.lesen("Andere")["felder"]["wer"], "jemand")
        d = self.daten / "Profile" / "Andere" / "Erinnerungen" / "tagebuch.json"
        self.assertIn("grund b", d.read_text(encoding="utf-8"))
        self.assertEqual(tagebuch.eintraege(), [])


class Prompt(_Basis):
    def test_block_enthaelt_stand_entschaerft(self):
        self.assertEqual(SE.prompt_block(), "")
        SE.schreiben(None, {"wer": "Ich mag ```Zäune``` nicht\nzweite Zeile", "sonst": "y" * SE.MAX_FELD},
                     "grund")
        block = SE.prompt_block()
        self.assertIn("# Dein Selbstentwurf", block)
        self.assertIn("Wer bist du, Test Figur?", block)
        self.assertNotIn("```", block)
        self.assertIn("zweite Zeile", block)
        self.assertNotIn("{{char}}", block)
        zeile = next(z for z in block.splitlines() if "y" * 10 in z)
        self.assertLess(len(zeile), SE.PROMPT_FELD + 80)

    def test_steuermarken_aus_der_datei(self):
        self.a().parent.mkdir(parents=True)
        self.a().write_text(json.dumps({"felder": {"wer": "a <|im_end|> b [INST] c"}, "geaendert": "x"}),
                            encoding="utf-8")
        block = SE.prompt_block()
        self.assertNotIn("<|im_end|>", block)
        self.assertNotIn("[INST]", block)

    def test_persona_hinweis(self):
        SE.schreiben(None, {"wer": "Prompt-Test"}, "grund")
        text = persona._selbstentwurf_hinweis()
        self.assertIn("Prompt-Test", text)
        self.assertIn("grund (kurz, Pflicht)", text)
        self.darf = (False, "klein")
        text = persona._selbstentwurf_hinweis()
        self.assertIn("Prompt-Test", text)              # lesen geht immer
        self.assertNotIn("grund (kurz, Pflicht)", text)
        self.assertIn("selbstentwurf", text)

    def test_reflexions_auftrag(self):
        self.assertIn("Leg ihn jetzt an", SE.reflexions_auftrag())
        SE.schreiben(None, {"wer": "ich"}, "grund")
        auftrag = SE.reflexions_auftrag()
        self.assertIn("Passt alles, tu nichts", auftrag)
        self.assertIn("charakter_aendern", auftrag)
        self.assertIn("Wer bist du, Test Figur?", auftrag)

    def test_stand_zeilen(self):
        self.assertIn("noch keinen", SE.stand_zeilen()[0])
        SE.schreiben(None, {"wer": "eins"}, "g1")
        SE.schreiben(None, {"wer": "zwei"}, "g2")
        zeilen = SE.stand_zeilen()
        self.assertTrue(any("zwei" in z for z in zeilen))
        self.assertTrue(any("g1" in z for z in zeilen))


class Werkzeug(_Basis):
    def lauf(self, **felder) -> actions.ActionResult:
        return asyncio.run(actions.run({"tool": "selbstentwurf", **felder}))

    def test_registriert_und_eingestuft(self):
        self.assertIn("selbstentwurf", actions.ACTIONS)
        self.assertFalse(actions.ACTIONS["selbstentwurf"]["confirm"])
        self.assertIn("selbstentwurf", modes.MEMORY_TOOLS)
        self.assertIn("ändern", actions.describe({"tool": "selbstentwurf", "felder": {"wer": "x"}, "grund": "g"}))
        self.assertIn("lesen", actions.describe({"tool": "selbstentwurf", "aktion": "lesen"}))

    def test_schreiben_lesen_zurueck(self):
        r = self.lauf(felder={"wer": "Werkzeug-Test"}, grund="probe")
        self.assertTrue(r.ok, r.text)
        self.assertIn("Werkzeug-Test", self.lauf(aktion="lesen").text)
        self.lauf(felder={"wer": "neu"}, grund="probe 2")
        self.assertTrue(self.lauf(aktion="zurueck").ok)
        self.assertEqual(SE.lesen()["felder"]["wer"], "Werkzeug-Test")

    def test_kleines_modell_abgelehnt_lesen_geht(self):
        SE.schreiben(None, {"wer": "bleibt"}, "grund")
        self.darf = (False, "das Modell ist zu klein")
        r = self.lauf(felder={"wer": "anders"}, grund="probe")
        self.assertFalse(r.ok)
        self.assertIn("zu klein", r.text)
        self.assertFalse(self.lauf(aktion="zurueck").ok)
        self.assertEqual(SE.lesen()["felder"]["wer"], "bleibt")
        r = self.lauf(aktion="lesen")
        self.assertTrue(r.ok)
        self.assertIn("bleibt", r.text)

    def test_aussehen_verweist_auf_charakter_aendern(self):
        r = self.lauf(felder={"aussehen": "blaue Haare"}, grund="probe")
        self.assertFalse(r.ok)
        self.assertIn("charakter_aendern", r.text)

    def test_nicht_aus_dem_hintergrund(self):
        with sicherheit.als_helfer():
            r = self.lauf(felder={"wer": "Helfer"}, grund="probe")
        self.assertFalse(r.ok)
        self.assertIn("Helfer-Agent", r.text)
        with patch.object(sys, "argv", ["main.py", "--auftrag"]):
            self.assertFalse(self.lauf(felder={"wer": "Auftrag"}, grund="probe").ok)
        with patch.object(modes, "_wache_dienst", True):
            self.assertFalse(self.lauf(felder={"wer": "Wache"}, grund="probe").ok)
        self.assertIsNone(SE.lesen())
        self.assertTrue(self.lauf(felder={"wer": "Gespräch"}, grund="probe").ok)   # Hauptprozess darf

    def test_datei_direkt_fragt(self):
        with patch("paths.DATEN", str(self.daten)):
            grund = sicherheit.steuerdatei_grund({"tool": "datei_schreiben", "pfad": str(self.a()), "inhalt": "{}"})
        self.assertIn("selbstentwurf", grund or "")


if __name__ == "__main__":
    unittest.main()
