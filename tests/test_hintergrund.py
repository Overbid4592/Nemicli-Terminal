"""Hintergrund-Aufgaben (core/hintergrund.py): starten, Stand, Fertigmeldung als Hinweis, Werkzeug `aufgaben`,
Kennzeichen an den langen Werkzeugen, Weiche in main.py (nur Cloud-Modelle)."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
if str(ROOT) not in sys.path:                     # main.py für die Freigabe-Tests
    sys.path.append(str(ROOT))
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import hintergrund as H       # noqa: E402
import lernschleife           # noqa: E402


class Aufgaben(unittest.TestCase):
    def setUp(self):
        H._test_reset()
        self.addCleanup(H._test_reset)

    def test_start_stand_und_fertigmeldung(self):
        meldungen = []

        async def arbeit():
            await asyncio.sleep(0.05)
            return NS(text="Video fertig 🎬 gespeichert: C:/x/v.mp4", ok=True)

        async def lauf():
            with mock.patch.object(lernschleife, "melden", side_effect=lambda t, w, **_k: meldungen.append((t, w))), \
                    mock.patch("actions.bild_status", return_value="Video: Schritt 3/30 (15s)", create=True):
                nr = H.starten("video_erstellen", "🎬 erzeuge dein Video", arbeit, "ada")
                self.assertEqual(nr, 1)
                self.assertIn("läuft im Hintergrund", H.start_text(nr))
                self.assertIn("aufgaben", H.start_text(nr))
                self.assertEqual(len(H.laufend()), 1)
                self.assertIn("Schritt 3/30", H.stand_text())
                self.assertIn("#1 läuft seit", H.uebersicht())
                await H._AUFGABEN[nr]["task"]
            return nr
        nr = asyncio.run(lauf())
        self.assertEqual(H.laufend(), [])
        self.assertEqual(H.stand_text(), "")
        self.assertEqual(len(meldungen), 1)
        text, wer = meldungen[0]
        self.assertEqual(wer, "ada")
        self.assertIn("#1", text)
        self.assertIn("fertig", text)
        self.assertIn("v.mp4", text)
        self.assertIn("#1 fertig", H.uebersicht())
        self.assertTrue(H._AUFGABEN[nr]["ok"])

    def test_fehler_wird_gemeldet(self):
        meldungen = []

        async def kaputt():
            raise RuntimeError("Grafikspeicher voll")

        async def lauf():
            with mock.patch.object(lernschleife, "melden", side_effect=lambda t, w, **_k: meldungen.append(t)):
                nr = H.starten("bild_serie", "🎨 male deine Serie", kaputt, "ada")
                await H._AUFGABEN[nr]["task"]
        asyncio.run(lauf())
        self.assertIn("gescheitert", meldungen[0])
        self.assertIn("Grafikspeicher voll", meldungen[0])
        self.assertIn("gescheitert", H.uebersicht())

    def test_leer(self):
        self.assertEqual(H.uebersicht(), "Keine Hintergrund-Aufgaben.")
        self.assertEqual(H.stand_text(), "")


class GrafikkarteBelegt(unittest.TestCase):
    """Läuft eine Aufgabe, startet keine zweite Grafikkarten-Arbeit – die KI erfährt es vorher und beim Versuch."""

    def setUp(self):
        H._test_reset()
        self.addCleanup(H._test_reset)

    def test_sperre_hinweis_und_freigabe(self):
        import main
        import persona
        import protokoll
        geloggt = []

        async def lauf():
            halt = asyncio.Event()

            async def arbeit():
                await halt.wait()
                return NS(text="fertig", ok=True)
            with mock.patch.object(lernschleife, "melden"):
                H.starten("bild_serie", "🎨 male deine Serie", arbeit, "ada")
                self.assertIn("#1", H.sperre("bild_malen"))
                self.assertIn("nicht noch einmal", H.sperre("video_erstellen"))
                self.assertEqual(H.sperre("datei_lesen"), "")             # Lesen und Reden gehen weiter
                self.assertIn("Gerade läuft im Hintergrund: #1", H.prompt_hinweis())
                self.assertIn("nur normal schreiben", persona._hintergrund_hinweis())
                gefragt = []
                with mock.patch.object(protokoll, "schreibe", side_effect=lambda *a, **k: geloggt.append(a)):
                    lage, res = await main._freigabe(
                        {"tool": "bild_malen", "prompt": "Katze"}, frage=lambda *a: gefragt.append(a),
                        pruefe=lambda *a: gefragt.append(a), zeige_aktion=lambda *a: None, wer="ada")
                self.assertEqual(lage, "block")
                self.assertFalse(res.ok)
                self.assertIn("Grafikkarte ist belegt", res.text)
                self.assertEqual(gefragt, [])                              # gar nicht erst gefragt
                halt.set()
                await H._AUFGABEN[1]["task"]
            self.assertEqual(H.sperre("bild_malen"), "")                   # fertig: wieder frei
            self.assertEqual(H.prompt_hinweis(), "")
        asyncio.run(lauf())
        self.assertTrue(geloggt and geloggt[0][2] == "blocked")

    def test_nur_grafikkarten_aufgaben_sperren(self):
        H._AUFGABEN[1] = {"nr": 1, "tool": "backup_erstellen", "titel": "💾 sichere", "start": 0, "ende": None}
        self.assertIsNone(H.gpu_belegt())
        self.assertEqual(H.sperre("bild_malen"), "")                   # Sicherung blockiert kein Bild
        with mock.patch("actions.bild_status", return_value="", create=True):
            self.assertIsNotNone(H.plakette())                          # angezeigt wird sie trotzdem
        H._AUFGABEN[2] = {"nr": 2, "tool": "video_erstellen", "titel": "🎬 erzeuge dein Video", "start": 0,
                          "ende": None}
        self.assertEqual(H.gpu_belegt()["nr"], 2)
        self.assertIn("#2", H.sperre("bild_malen"))

    def test_befehle_bild_und_video_warten(self):
        import main
        self.assertTrue(main._grafikkarte_frei())
        H._AUFGABEN[1] = {"nr": 1, "tool": "video_erstellen", "titel": "🎬 erzeuge dein Video", "start": 0,
                          "ende": None}
        with mock.patch.object(main.ui, "warn") as warn:
            self.assertFalse(main._grafikkarte_frei())
        self.assertIn("#1", warn.call_args[0][0])
        quelle = main_quelle.text()
        self.assertEqual(quelle.count("if not _grafikkarte_frei():"), 2)            # /bild und /video


class Anbindung(unittest.TestCase):
    def test_kennzeichen_und_werkzeug(self):
        import actions
        import modes
        self.assertTrue(actions.ACTIONS["video_erstellen"].get("hintergrund"))
        self.assertTrue(actions.ACTIONS["bild_serie"].get("hintergrund"))
        self.assertFalse(actions.ACTIONS["bild_malen"].get("hintergrund"))
        self.assertIn("aufgaben", actions.ACTIONS)
        self.assertIn("aufgaben", modes.READ_TOOLS)
        H._test_reset()
        self.assertEqual(actions._aufgaben({}), "Keine Hintergrund-Aufgaben.")

    def test_weiche_in_main_nur_cloud(self):
        """Die Bedingung aus main.py: Kennzeichen + kein warten + Cloud-Modell."""
        import re
        quelle = main_quelle.text()
        self.assertEqual(quelle.count(".get(tool, {}).get(\"hintergrund\") and not act.get(\"warten\")"), 1)   # eine Schleife für Terminal + Browser
        m = re.search(r'str\(SESSION\.get\("model"\) or ""\)\.split\(":"\)\[0\] not in \(([^)]*)\)', quelle)
        self.assertIsNotNone(m)
        self.assertEqual(set(eval("(" + m.group(1) + ")")), {"", "gguf", "ollama"})

    def test_eingabezeile_zeigt_stand(self):
        import mascot
        H._test_reset()
        with mock.patch.object(H, "plakette", return_value=("🎬", "#2 Video 5/30")):
            text = "".join(t for _, t in mascot.rprompt())
            self.assertIn("#2", text)
            ohne = "".join(t for _, t in mascot.rprompt(mit_hintergrund=False))   # Vollbild: links statt rechts
            self.assertNotIn("#2", ohne)
        with mock.patch.object(H, "plakette", return_value=None):
            text = "".join(t for _, t in mascot.rprompt())
        self.assertNotIn("#", text)

    def test_plakette_kurz_und_vorn(self):
        H._test_reset()
        self.assertIsNone(H.plakette())
        H._AUFGABEN[2] = {"nr": 2, "tool": "video_erstellen", "titel": "🎬 erzeuge dein Video", "start": 0, "ende": None}
        lang = "Video: Schritt 22/30 · 12.8 s je Schritt · seit Beginn 4:43 · noch etwa 1:43"
        with mock.patch("actions.bild_status", return_value=lang, create=True):
            self.assertEqual(H.plakette(), ("🎬", "#2 Video 22/30 · noch 1:43"))
        with mock.patch("actions.bild_status", return_value="", create=True):
            sym, text = H.plakette()
        self.assertEqual(sym, "🎬")
        self.assertTrue(text.startswith("#2 erzeuge dein Video ("))
        H._test_reset()
        for datei in ("ui/screen_tx.py", "ui/screen.py"):                # links und vor allen anderen Plaketten
            quelle = (ROOT / datei).read_text(encoding="utf-8-sig")
            self.assertIn("hintergrund.plakette()", quelle)
            self.assertIn("mascot.rprompt(mit_hintergrund=False)", quelle)
        tx = (ROOT / "ui/screen_tx.py").read_text(encoding="utf-8-sig")
        self.assertLess(tx.index("hintergrund.plakette()"), tx.index("if self.bibliothekar_text:"))


if __name__ == "__main__":
    unittest.main()
