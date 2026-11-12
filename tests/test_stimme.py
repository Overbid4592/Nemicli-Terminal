"""Stimm-Dienst (engines/stimme.py) und seine Werkzeuge – ohne Modelldateien, Wiedergabe gemockt."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions                 # noqa: E402
import config                  # noqa: E402
import modes                   # noqa: E402
import paths                   # noqa: E402
import profilordner            # noqa: E402
import stimme as S             # noqa: E402


class Text(unittest.TestCase):
    def test_sprechtext(self):
        t = S.sprechtext("## Titel\n**Fett** und `code` 😊\n```py\nprint(1)\n```\n[Seite](https://x.example) "
                         "https://y.example/z\n| a | b |\n|---|---|\n- Punkt")
        for weg in ("##", "**", "`", "😊", "print(1)", "https://", "|---"):
            self.assertNotIn(weg, t)
        for bleibt in ("Titel", "Fett", "code", "Seite", "ein Link", "Punkt"):
            self.assertIn(bleibt, t)

    def test_saetze(self):
        stuecke = S.saetze("Eins. Zwei! Drei? " + "Sehr lang, " * 60 + "Ende.", grenze=80)
        self.assertTrue(all(len(s) <= 80 for s in stuecke))
        self.assertEqual(stuecke[0], "Eins. Zwei! Drei?")
        self.assertTrue(stuecke[-1].endswith("Ende."))
        self.assertEqual(S.saetze("😊 ```x```"), [])


class StimmeJePersoenlichkeit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.konfig = {}
        for p in (mock.patch.object(paths, "DATEN", Path(tmp.name)),
                  mock.patch.object(profilordner, "aktiv", return_value="Alpha"),
                  mock.patch.object(config, "load", side_effect=lambda: dict(self.konfig)),
                  mock.patch("snapshot.sichern", return_value=None),
                  mock.patch.dict(S._PROBEN, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.ordner = Path(paths.DATEN) / "Profile" / "Alpha"

    def test_startstimme_und_festlegen(self):
        self.assertEqual(S.stimme(), S.STARTSTIMME)
        S.probe_merken("Eine ruhige Stimme", 42, 210.0)
        pfad = S.stimme_festlegen("  Eine ruhige   Stimme ", 42)
        self.assertEqual(pfad, self.ordner / "alpha.json")
        d = json.loads(pfad.read_text(encoding="utf-8"))
        self.assertEqual(d["voice"]["stimmhoehe_hz"], 210)
        self.assertEqual(S.stimme(), {"beschreibung": "Eine ruhige Stimme", "seed": 42, "sprache": "german"})
        with self.assertRaises(ValueError):
            S.stimme_festlegen("  ", 1)

    def test_voice_neben_anderen_eintraegen(self):
        import charakter
        self.ordner.mkdir(parents=True)
        (self.ordner / "alpha.json").write_text(json.dumps({"name": "Alpha", "kern": "a girl", "stil": "anime",
                                                            "eigenes": 1}), encoding="utf-8")
        (self.ordner / "stimme.json").write_text(json.dumps({"beschreibung": "Alt", "seed": 3}), encoding="utf-8")
        self.assertEqual(S.stimme()["beschreibung"], "Alt")            # früherer Ort wird noch gelesen
        S.probe_merken("Hell und weich", 9, 230.0)
        S.stimme_festlegen("Hell und weich", 9)
        d = json.loads((self.ordner / "alpha.json").read_text(encoding="utf-8"))
        self.assertEqual((d["kern"], d["stil"], d["eigenes"]), ("a girl", "anime", 1))
        self.assertFalse((self.ordner / "stimme.json").exists())
        self.assertEqual(S.stimme()["seed"], 9)
        text, ok = charakter.aendern({"stil": "realistic"})            # Stimme bleibt beim Ändern
        self.assertTrue(ok, text)
        self.assertEqual(charakter.laden()["voice"]["beschreibung"], "Hell und weich")

    def test_hz_sperre(self):
        with self.assertRaises(ValueError):                             # ohne Probe kein Festlegen
            S.stimme_festlegen("Eine Stimme", 1)
        S.probe_merken("Eine tiefe Stimme", 5, 120.0)
        self.assertIn("120 Hz", S.sperre(120.0))
        self.assertIsNone(S.sperre(200.0))
        self.assertIsNone(S.sperre(None))
        with self.assertRaises(ValueError) as e:
            S.stimme_festlegen("Eine tiefe Stimme", 5)
        self.assertIn("Männerbereich", str(e.exception))
        self.assertFalse((self.ordner / "alpha.json").exists())
        self.konfig["stimme_min_hz"] = 0                                # 0 = Sperre aus
        self.assertIsNone(S.sperre(120.0))
        S.stimme_festlegen("Ohne Probe", 2)

    def test_probe_unter_der_grenze_wird_nicht_gespielt(self):
        with mock.patch.object(S, "verfuegbar", return_value=True), \
             mock.patch.object(S, "erzeugen", return_value=np.zeros(2400, dtype="float32")), \
             mock.patch.object(S, "stimmhoehe", return_value=110.0), \
             mock.patch.object(S, "abspielen") as spielen:
            res = actions._stimme_probe({"beschreibung": "Eine tiefe Stimme", "satz": "Hallo.", "seed": 4})
        self.assertFalse(res.ok)
        self.assertIn("Hz-Sperre", res.text)
        spielen.assert_not_called()
        res = actions._stimme_festlegen({"beschreibung": "Eine tiefe Stimme", "seed": 4})
        self.assertFalse(res.ok)


class Stimmhoehe(unittest.TestCase):
    def test_reine_toene(self):
        t = np.arange(S.ABTASTRATE) / S.ABTASTRATE
        for hz in (95, 140, 200, 260, 420):
            ton = 0.3 * np.sin(2 * np.pi * hz * t) + 0.15 * np.sin(4 * np.pi * hz * t)
            self.assertAlmostEqual(S.stimmhoehe(ton.astype("float32")), hz, delta=hz * 0.02)
        self.assertIsNone(S.stimmhoehe(np.zeros(S.ABTASTRATE, dtype="float32")))


class Finden(unittest.TestCase):
    def test_paar_im_vision_ordner(self):
        with tempfile.TemporaryDirectory() as tmp:
            basis = Path(tmp)
            (basis / "Vision" / "Bild").mkdir(parents=True)
            (basis / "Vision" / "Bild" / "vl.gguf").write_bytes(b"")
            tts = basis / "Vision" / "TTS"
            tts.mkdir()
            for n in ("talker.gguf", "tok.gguf"):
                (tts / n).write_bytes(b"")
            arten = {"vl.gguf": "qwen3vl", "talker.gguf": "qwen3-tts", "tok.gguf": "qwen3-tts-tokenizer"}
            with mock.patch.object(S, "_architektur", side_effect=lambda f: arten[f.name]), \
                 mock.patch.object(config, "load", return_value={}):
                self.assertEqual(S.finden(basis), (tts / "talker.gguf", tts / "tok.gguf"))
                (tts / "tok.gguf").unlink()
                self.assertIsNone(S.finden(basis))


class Modus(unittest.TestCase):
    """aus / knopf / an aus der Konfiguration – alte Werte bleiben verständlich."""

    def _modus(self, cfg):
        with mock.patch.object(config, "load", return_value=cfg):
            return S.modus()

    def test_werte(self):
        self.assertEqual(self._modus({}), "knopf")                          # Vorgabe: nur auf Anfrage
        self.assertEqual(self._modus({"voice_modus": "an"}), "an")
        self.assertEqual(self._modus({"voice_modus": "AUS"}), "aus")
        self.assertEqual(self._modus({"voice_modus": True}), "an")           # alter Sprachmodus
        self.assertEqual(self._modus({"voice_modus": False}), "aus")         # alt: der Nutzer hatte /voice aus gesagt
        self.assertEqual(self._modus({"vorlesen_auto": True}), "an")         # alter Schalter der KI
        self.assertEqual(self._modus({"voice_modus": "knopf", "vorlesen_auto": True}), "knopf")

    def test_aus_laedt_nichts(self):
        with mock.patch.object(S, "modus", return_value="aus"), mock.patch.object(S, "AKTIV", True), \
                mock.patch.object(S, "verfuegbar", return_value=True), mock.patch.object(S, "_laden") as laden:
            self.assertFalse(S.sagen("Hallo."))
        laden.assert_not_called()

    def test_aus_setzen_gibt_frei(self):
        gespeichert = {}
        with mock.patch.object(config, "update", side_effect=lambda **k: gespeichert.update(k)), \
                mock.patch.object(S, "freigeben") as frei:
            self.assertEqual(S.modus_setzen("aus"), "aus")
            self.assertEqual(S.modus_setzen("unsinn"), "knopf")
        self.assertEqual(gespeichert, {"voice_modus": "knopf"})
        frei.assert_called_once()

    def test_werkzeuge_bei_aus(self):
        with mock.patch.object(S, "verfuegbar", return_value=True), mock.patch.object(S, "modus", return_value="aus"), \
                mock.patch.object(S, "erzeugen") as erzeugen:
            r = actions._vorlesen({"text": "Hallo"})
            p = actions._stimme_probe({"beschreibung": "hell", "satz": "Hallo"})
        self.assertFalse(r.ok)
        self.assertIn("/voice aus", r.text)
        self.assertFalse(p.ok)
        erzeugen.assert_not_called()


class Sprechen(unittest.TestCase):
    def setUp(self):
        self.gespielt, self.lock = [], threading.Lock()
        for p in (mock.patch.object(S, "verfuegbar", return_value=True),
                  mock.patch.object(S, "modus", return_value="an"),
                  mock.patch.object(S, "erzeugen", side_effect=lambda t, st=None, abbruch=None:
                                    np.full(2400, 0.1, dtype="float32")),
                  mock.patch.object(S, "abspielen", side_effect=lambda p, d: self.gespielt.append(Path(p).name)),
                  mock.patch.object(S, "AKTIV", True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(S.stopp)

    def warten(self, n):
        ende = time.time() + 5
        while len(self.gespielt) < n and time.time() < ende:
            time.sleep(0.02)

    def test_satz_fuer_satz(self):
        self.assertTrue(S.sagen("Erster Satz. " + "Wort " * 80 + "Ende.", S.STARTSTIMME))
        self.warten(2)
        self.assertGreaterEqual(len(self.gespielt), 2)

    def test_nur_im_grossen_nemicli(self):
        with mock.patch.object(S, "AKTIV", False):
            self.assertFalse(S.sagen("Hallo."))
        self.assertFalse(S.sagen("   "))

    def test_knopf_entlaedt_sofort_an_haelt_warm(self):
        with mock.patch.object(S, "freigeben") as frei, mock.patch.object(S, "modus", return_value="knopf"):
            self.assertTrue(S.sagen("Ein Satz.", S.STARTSTIMME))
            self.warten(1)
            time.sleep(0.2)
        frei.assert_called()                                             # Knopf: direkt danach entladen
        with mock.patch.object(S, "freigeben") as frei:                  # Modus an (setUp): bleibt warm
            self.assertTrue(S.sagen("Noch ein Satz.", S.STARTSTIMME))
            self.warten(2)
            time.sleep(0.2)
        frei.assert_not_called()

    def test_stopp_verwirft_ausstehendes(self):
        S.stopp()
        gen = S._generation
        S._auftraege.put((gen - 1, "alt", S.STARTSTIMME))           # Auftrag aus der Zeit vor stopp()
        self.assertTrue(S.sagen("Neu.", S.STARTSTIMME))
        self.warten(1)
        time.sleep(0.2)
        self.assertEqual(len(self.gespielt), 1)


class Werkzeuge(unittest.TestCase):
    def test_registriert_und_modi(self):
        for t in ("stimme_probe", "stimme_festlegen", "vorlesen", "stimme_schalter"):
            self.assertIn(t, actions.ACTIONS)
            self.assertNotIn(t, modes.READ_TOOLS)                   # Hintergrund-Läufe sprechen nicht

    def test_schalter(self):
        gespeichert = {}
        with mock.patch.object(config, "update", side_effect=lambda **k: gespeichert.update(k)), \
             mock.patch.object(config, "load", side_effect=lambda: dict(gespeichert)), \
             mock.patch.object(S, "verfuegbar", return_value=False):
            text = actions._stimme_schalter({"vorlesen_auto": "an", "kugel_spricht": False})
            self.assertEqual(gespeichert, {"voice_modus": "an", "kugel_spricht": False})
            self.assertIn("Antworten vorlesen: an", text)
            self.assertIn("Kugel spricht: aus", text)
            actions._stimme_schalter({"vorlesen_auto": False})
            self.assertEqual(gespeichert["voice_modus"], "knopf")
            gespeichert["voice_modus"] = "aus"                      # der Nutzer hat abgeschaltet
            text = actions._stimme_schalter({"vorlesen_auto": True})
            self.assertEqual(gespeichert["voice_modus"], "aus")     # die KI hebt das nicht auf
            self.assertIn("/voice aus", text)

    def test_ohne_modell_klare_meldung(self):
        with mock.patch.object(S, "verfuegbar", return_value=False):
            r = actions._vorlesen({"text": "Hallo"})
        self.assertFalse(r.ok)
        self.assertIn("Kein Stimm-Modell", r.text)

    def test_vorlesen_prueft_den_pfad(self):
        with mock.patch.object(S, "verfuegbar", return_value=True), \
             mock.patch.object(actions, "_read_guard", return_value="Fehler: gesperrt"):
            r = actions._vorlesen({"pfad": r"C:\gesperrt\x.txt"})
        self.assertFalse(r.ok)

    def test_kugel_spricht_nur_wenn_da(self):
        from wache import zugang
        with mock.patch.object(S, "sagen") as sagen, mock.patch.object(config, "load", return_value={}):
            with mock.patch.object(zugang, "laeuft", return_value={"pid": 1}):
                actions._kugel_sprechen("Ich male dir was.", {})
                actions._kugel_sprechen("Versteckt.", {"versteckt": True})
            with mock.patch.object(zugang, "laeuft", return_value=None):
                actions._kugel_sprechen("Ohne Wache.", {})
        sagen.assert_called_once_with("Ich male dir was.")


class Sprachmodus(unittest.TestCase):
    def test_kurzfassung(self):
        import ast
        quelle = main_quelle.text()
        knoten = [n for n in ast.parse(quelle).body if isinstance(n, ast.FunctionDef) and n.name == "_kurzfassung"]
        ns = {"re": __import__("re")}
        exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), ns)
        kurz = ns["_kurzfassung"]("Hallo Schatz! Hier kommt eine lange Erklärung. Zweiter Satz.")
        self.assertTrue(kurz.startswith("Hallo Schatz!"))
        self.assertNotIn("Zweiter Satz", kurz)
        self.assertIn("/voice text", kurz)
        self.assertLess(len(ns["_kurzfassung"]("Wort " * 100).split("\n")[0]), 125)

    def test_prompt_hinweis_nur_wenn_an(self):
        import persona
        with mock.patch.object(S, "modus", return_value="knopf"):
            self.assertEqual(persona._voice_hinweis(), "")
        with mock.patch.object(S, "modus", return_value="an"):
            self.assertIn("Sprachmodus ist an", persona._voice_hinweis())

    def test_befehl_und_menue(self):
        import commands
        self.assertIn("/voice", commands.COMMANDS)
        self.assertEqual([z for z, _, _ in commands.UNTERMENUES["/voice"]],
                         ["", "an", "knopf", "aus", "jetzt", "text"])


class Bildermalen(unittest.TestCase):
    def test_stimme_macht_platz(self):
        import imagegen
        with mock.patch.object(S, "gpu_freigeben") as frei, mock.patch.dict(sys.modules, {"gguflokal": None}):
            sys.modules.pop("gguflokal", None)
            imagegen._gpu_fuer_bild(lambda: "gemalt")()
        frei.assert_called_once()


if __name__ == "__main__":
    unittest.main()
